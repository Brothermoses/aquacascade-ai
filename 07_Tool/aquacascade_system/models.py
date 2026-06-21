"""Data-access + domain logic. All SQL here. Multi-tenant + scoped for
nationwide use (utility / state / national)."""
import json
import re
from .db import tx, log_event
from .auth import generate_user_token, hash_token, token_label
from . import triage_bridge as TB
from . import schema_def as SD
from . import rehab_engine as RE
import datetime
from .config import (DEFAULT_HORIZON_YEARS, DEFAULT_REPLACE_COST,
                     EQUITY_MULTIPLIER, ROLE_TYPES,
                     ASSET_SERVICE_LIFE_YEARS)

LIFECYCLE_IMPORT_FIELDS = (
    "expected_service_life_years",
    "replacement_year",
    "diameter_in",
    "length_ft",
    "ownership_side",
    "verification_method",
    "evidence_source",
    "confidence_score",
)


def _state(pwsid):
    return (pwsid or "")[:2].upper()


def _clean_float(v):
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _clean_int(v):
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return int(float(s.replace(",", "")))
    except ValueError:
        return None


def _clean_year(v):
    yr = _clean_int(v)
    if yr is None or not 1850 <= yr <= 2100:
        return None
    return yr


def _service_life_years(v, default=ASSET_SERVICE_LIFE_YEARS):
    life = _clean_int(v)
    if life is None or not 1 <= life <= 200:
        return int(default)
    return life


def _lifecycle_metrics(row, current_year=None):
    current_year = current_year or datetime.date.today().year
    install_year = _clean_year(row.get("install_year"))
    life = _service_life_years(row.get("expected_service_life_years"))
    supplied_life = _clean_int(row.get("expected_service_life_years"))
    if not install_year:
        return {
            "asset_age_years": None,
            "remaining_life_years": None,
            "expected_service_life_years": life,
            "service_life_basis": "Default assumption",
            "lifecycle_flag": "Needs install year",
            "renewal_due_year": None,
        }
    age = max(0, current_year - install_year)
    remaining = life - age
    if remaining < 0:
        flag = "Past service life"
    elif remaining == 0:
        flag = "Due this year"
    elif remaining <= 5:
        flag = "Due within 5 years"
    elif remaining <= 10:
        flag = "Due within 10 years"
    else:
        flag = "Within service life"
    return {
        "asset_age_years": age,
        "remaining_life_years": remaining,
        "expected_service_life_years": life,
        "service_life_basis": "Utility supplied"
        if supplied_life is not None else "Default assumption",
        "lifecycle_flag": flag,
        "renewal_due_year": install_year + life,
    }


def _valid_latlon(lat, lon):
    return lat is not None and lon is not None and -90 <= lat <= 90 \
        and -180 <= lon <= 180


def _valid_lonlat_pair(pair):
    return isinstance(pair, (list, tuple)) and len(pair) >= 2 and \
        _valid_latlon(_clean_float(pair[1]), _clean_float(pair[0]))


def _parse_lonlat_pair(text):
    parts = [p for p in re.split(r"\s+", text.strip()) if p]
    if len(parts) < 2:
        return None
    lon = _clean_float(parts[0])
    lat = _clean_float(parts[1])
    if not _valid_latlon(lat, lon):
        return None
    return [lon, lat]


def _parse_wkt_geometry(text):
    s = str(text or "").strip()
    m = re.match(r"POINT\s*\(\s*([^)]+)\s*\)$", s, re.I)
    if m:
        pair = _parse_lonlat_pair(m.group(1))
        return {"type": "Point", "coordinates": pair} if pair else None
    m = re.match(r"LINESTRING\s*\(\s*([^)]+)\s*\)$", s, re.I)
    if m:
        coords = [_parse_lonlat_pair(p) for p in m.group(1).split(",")]
        coords = [p for p in coords if p]
        return {"type": "LineString", "coordinates": coords} \
            if len(coords) >= 2 else None
    m = re.match(r"MULTILINESTRING\s*\(\s*(.+)\s*\)$", s, re.I)
    if m:
        lines = []
        for chunk in re.findall(r"\(([^()]+)\)", m.group(1)):
            coords = [_parse_lonlat_pair(p) for p in chunk.split(",")]
            coords = [p for p in coords if p]
            if len(coords) >= 2:
                lines.append(coords)
        return {"type": "MultiLineString", "coordinates": lines} \
            if lines else None
    return None


def _clean_geojson_geometry(obj):
    if not isinstance(obj, dict):
        return None
    geom = obj.get("geometry") if obj.get("type") == "Feature" else obj
    if not isinstance(geom, dict):
        return None
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    if gtype == "Point" and _valid_lonlat_pair(coords):
        return {"type": "Point", "coordinates": [
            _clean_float(coords[0]), _clean_float(coords[1])]}
    if gtype == "LineString" and isinstance(coords, list):
        line = [[_clean_float(p[0]), _clean_float(p[1])] for p in coords
                if _valid_lonlat_pair(p)]
        return {"type": "LineString", "coordinates": line} \
            if len(line) >= 2 else None
    if gtype == "MultiLineString" and isinstance(coords, list):
        lines = []
        for raw in coords:
            if not isinstance(raw, list):
                continue
            line = [[_clean_float(p[0]), _clean_float(p[1])] for p in raw
                    if _valid_lonlat_pair(p)]
            if len(line) >= 2:
                lines.append(line)
        return {"type": "MultiLineString", "coordinates": lines} \
            if lines else None
    return None


def _parse_geometry(text):
    s = str(text or "").strip()
    if not s:
        return None
    if s.startswith("{"):
        try:
            return _clean_geojson_geometry(json.loads(s))
        except json.JSONDecodeError:
            return None
    return _parse_wkt_geometry(s)


def _geometry_text(row):
    return str(row.get("geometry") or row.get("location") or "").strip()


def _representative_coord(geometry):
    if not geometry:
        return None, None
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if gtype == "Point" and _valid_lonlat_pair(coords):
        return _clean_float(coords[1]), _clean_float(coords[0])
    if gtype == "LineString" and coords:
        p = coords[len(coords) // 2]
        if _valid_lonlat_pair(p):
            return _clean_float(p[1]), _clean_float(p[0])
        return None, None
    if gtype == "MultiLineString" and coords:
        longest = max(coords, key=len)
        if longest:
            p = longest[len(longest) // 2]
            if _valid_lonlat_pair(p):
                return _clean_float(p[1]), _clean_float(p[0])
            return None, None
    return None, None


def _coords_from_location(location):
    """Parse common offline-safe coordinate forms.

    Supported:
      - "POINT(lon lat)"
      - "lat, lon" or "lon, lat" when the values are unambiguous
    """
    s = str(location or "")
    m = re.search(r"POINT\s*\(\s*(-?\d+(?:\.\d+)?)\s+"
                  r"(-?\d+(?:\.\d+)?)\s*\)", s, re.I)
    if m:
        lon, lat = float(m.group(1)), float(m.group(2))
        return (lat, lon) if _valid_latlon(lat, lon) else (None, None)
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)", s)
    if not m:
        return None, None
    a, b = float(m.group(1)), float(m.group(2))
    if 18 <= a <= 72 and -180 <= b <= -60:
        return a, b
    if -180 <= a <= -60 and 18 <= b <= 72:
        return b, a
    return None, None


def _coords(row):
    lat = _clean_float(row.get("latitude"))
    lon = _clean_float(row.get("longitude"))
    if _valid_latlon(lat, lon):
        return lat, lon
    geometry = _parse_geometry(_geometry_text(row))
    lat, lon = _representative_coord(geometry)
    if _valid_latlon(lat, lon):
        return lat, lon
    return _coords_from_location(row.get("location"))


def _map_geometry(row):
    geometry = _parse_geometry(_geometry_text(row))
    if geometry:
        return geometry
    lat, lon = _coords(row)
    if _valid_latlon(lat, lon):
        return {"type": "Point", "coordinates": [lon, lat]}
    return None


def _material_bucket(value):
    s = str(value or "").strip()
    low = s.lower()
    if not s or "unknown" in low:
        return "Lead Status Unknown"
    if "galvanized" in low:
        return "Galvanized Requiring Replacement"
    if "non-lead" in low or "nonlead" in low or "renewed" in low:
        return "Non-lead"
    if low == "lead" or low.startswith("lead "):
        return "Lead"
    return s


def allowed_types(principal):
    if not principal:
        return ()
    return ROLE_TYPES.get(principal["role"], ())


def _in_scope(principal, pwsid, state):
    if not principal or principal["kind"] == "NATIONAL":
        return True
    if principal["kind"] == "STATE":
        return (state or "").upper() == principal["scope_key"].upper()
    if principal["kind"] == "UTILITY":
        return (pwsid or "") == principal["scope_key"]
    return False


def _is_admin(principal):
    return principal is not None and principal.get("role") == "ADMIN"


def _can_manage_tenant(principal, tenant_id):
    """True when principal may manage users for tenant_id.

    principal=None is reserved for trusted local CLI/admin maintenance.
    API and web callers must pass a real principal.
    """
    if principal is None:
        return True
    if not _is_admin(principal):
        return False
    if principal["kind"] == "NATIONAL":
        return True
    return int(principal["tid"]) == int(tenant_id)


def _public_user(row):
    d = dict(row)
    return {
        "id": d["id"],
        "tenant_id": d["tenant_id"],
        "tenant": d["tenant"],
        "kind": d["kind"],
        "scope_key": d["scope_key"],
        "name": d["name"],
        "role": d["role"],
        "active": bool(d["active"]),
        "token_label": d.get("token_label") or token_label(d.get("token")),
        "token_issued_at": d.get("token_issued_at"),
        "token_last_used_at": d.get("token_last_used_at"),
    }


# ---------- tenancy / auth scope --------------------------------------
def get_principal(token):
    """token -> {user_id,name,role,tenant,kind,scope_key} or None."""
    if not token:
        return None
    h = hash_token(token)
    with tx() as con:
        r = con.execute("""SELECT u.id uid,u.name un,u.role,
            u.token_label,t.id tid,t.name tn,t.kind,t.scope_key
            FROM users u JOIN tenants t ON t.id=u.tenant_id
            WHERE u.active=1 AND (u.token_hash=? OR u.token=?)""",
                        (h, token)).fetchone()
        if not r:
            return None
        con.execute("UPDATE users SET token_last_used_at=CURRENT_TIMESTAMP "
                    "WHERE id=?", (r["uid"],))
        return dict(r)


def create_tenant(name, kind, scope_key, user_name, role="DISPATCHER",
                   token=None):
    kind = kind.upper()
    role = role.upper()
    scope_key = scope_key.upper() if kind == "STATE" else scope_key
    if kind not in ("UTILITY", "STATE", "NATIONAL"):
        raise ValueError("kind must be UTILITY|STATE|NATIONAL")
    if role not in ROLE_TYPES:
        raise ValueError("role must be INSPECTOR|REHAB|DISPATCHER|ADMIN")
    token = token or generate_user_token(kind[:3].lower())
    label = token_label(token)
    with tx() as con:
        tid = con.insert_id("INSERT INTO tenants(name,kind,scope_key) "
                            "VALUES(?,?,?)", (name, kind, scope_key))
        uid = con.insert_id("""INSERT INTO users(tenant_id,name,role,token,
            token_hash,token_label,token_issued_at)
            VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
                            (tid, user_name, role, label,
                             hash_token(token), label))
        log_event(con, "TENANT", f"{kind} {scope_key} ({name})")
    return {"tenant_id": tid, "user_id": uid, "token": token,
            "kind": kind, "scope_key": scope_key, "role": role}


def list_tenants(principal=None):
    if principal is not None and not _is_admin(principal):
        raise PermissionError("admin role required")
    with tx() as con:
        if principal is not None and principal["kind"] != "NATIONAL":
            rows = con.execute("""SELECT id,name,kind,scope_key,created_at
                FROM tenants WHERE id=? ORDER BY id""",
                               (principal["tid"],)).fetchall()
        else:
            rows = con.execute("""SELECT id,name,kind,scope_key,created_at
                FROM tenants ORDER BY kind,scope_key,name""").fetchall()
    return [dict(r) for r in rows]


def list_users(principal=None):
    if principal is not None and not _is_admin(principal):
        raise PermissionError("admin role required")
    q = ["""SELECT u.id,u.tenant_id,u.name,u.role,u.active,u.token,
        u.token_label,u.token_issued_at,u.token_last_used_at,
        t.name tenant,t.kind,t.scope_key
        FROM users u JOIN tenants t ON t.id=u.tenant_id"""]
    params = []
    if principal is not None and principal["kind"] != "NATIONAL":
        q.append("WHERE u.tenant_id=?")
        params.append(principal["tid"])
    q.append("ORDER BY t.kind,t.scope_key,u.role,u.name,u.id")
    with tx() as con:
        rows = con.execute(" ".join(q), params).fetchall()
    return [_public_user(r) for r in rows]


def create_user(tenant_id, name, role, principal=None):
    role = (role or "").upper()
    name = (name or "").strip()
    if not name:
        raise ValueError("name is required")
    if role not in ROLE_TYPES:
        raise ValueError("role must be INSPECTOR|REHAB|DISPATCHER|ADMIN")
    with tx() as con:
        t = con.execute("SELECT id FROM tenants WHERE id=?",
                        (tenant_id,)).fetchone()
        if not t:
            raise ValueError("tenant not found")
        if not _can_manage_tenant(principal, tenant_id):
            raise PermissionError("cannot manage users for that tenant")
        token = generate_user_token(role[:3].lower())
        label = token_label(token)
        uid = con.insert_id("""INSERT INTO users(tenant_id,name,role,token,
            token_hash,token_label,token_issued_at)
            VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
                            (tenant_id, name, role, label,
                             hash_token(token), label))
        row = con.execute("""SELECT u.id,u.tenant_id,u.name,u.role,u.active,
            u.token,u.token_label,u.token_issued_at,u.token_last_used_at,
            t.name tenant,t.kind,t.scope_key
            FROM users u JOIN tenants t ON t.id=u.tenant_id
            WHERE u.id=?""", (uid,)).fetchone()
        log_event(con, "USER_CREATE", f"user {uid} tenant {tenant_id}")
    out = _public_user(row)
    out["token"] = token
    return out


def _user_with_tenant(con, user_id):
    return con.execute("""SELECT u.id,u.tenant_id,u.name,u.role,u.active,
        u.token,u.token_label,u.token_issued_at,u.token_last_used_at,
        t.name tenant,t.kind,t.scope_key
        FROM users u JOIN tenants t ON t.id=u.tenant_id
        WHERE u.id=?""", (user_id,)).fetchone()


def rotate_user_token(user_id, principal=None):
    with tx() as con:
        row = _user_with_tenant(con, user_id)
        if not row:
            raise ValueError("user not found")
        if not _can_manage_tenant(principal, row["tenant_id"]):
            raise PermissionError("cannot manage users for that tenant")
        token = generate_user_token(row["role"][:3].lower())
        label = token_label(token)
        con.execute("""UPDATE users SET token=?,token_hash=?,token_label=?,
            token_issued_at=CURRENT_TIMESTAMP,token_last_used_at=NULL
            WHERE id=?""", (label, hash_token(token), label, user_id))
        row = _user_with_tenant(con, user_id)
        log_event(con, "USER_TOKEN_ROTATE", f"user {user_id}")
    out = _public_user(row)
    out["token"] = token
    return out


def set_user_active(user_id, active, principal=None):
    active = bool(active)
    with tx() as con:
        row = _user_with_tenant(con, user_id)
        if not row:
            raise ValueError("user not found")
        if not _can_manage_tenant(principal, row["tenant_id"]):
            raise PermissionError("cannot manage users for that tenant")
        if (principal is not None and not active and
                int(user_id) == int(principal["uid"])):
            raise ValueError("cannot deactivate your own user")
        if not active and row["role"] == "ADMIN" and row["kind"] == "NATIONAL":
            n = con.execute("""SELECT COUNT(*) c FROM users u
                JOIN tenants t ON t.id=u.tenant_id
                WHERE u.role='ADMIN' AND u.active=1
                AND t.kind='NATIONAL'""").fetchone()["c"]
            if n <= 1:
                raise ValueError("cannot deactivate the last active national admin")
        con.execute("UPDATE users SET active=? WHERE id=?",
                    (1 if active else 0, user_id))
        row = _user_with_tenant(con, user_id)
        log_event(con, "USER_ACTIVE",
                  f"user {user_id} active={1 if active else 0}")
    return _public_user(row)


def _scope_sql(principal, col_pws="pwsid", col_state="state"):
    """Return (sql, params) restricting rows to the principal's scope.
    None or NATIONAL -> no restriction (nationwide view)."""
    if not principal or principal["kind"] == "NATIONAL":
        return "1=1", []
    if principal["kind"] == "STATE":
        return f"{col_state}=?", [principal["scope_key"].upper()]
    if principal["kind"] == "UTILITY":
        return f"{col_pws}=?", [principal["scope_key"]]
    return "1=1", []


def _state_focus_sql(principal, state_filter, col_state="state"):
    st = str(state_filter or "").strip().upper()
    if not st:
        return "", []
    if principal and principal["kind"] == "STATE" and \
            st != principal["scope_key"].upper():
        return " AND 1=0", []
    if principal and principal["kind"] == "UTILITY" and \
            st != _state(principal["scope_key"]):
        return " AND 1=0", []
    return f" AND {col_state}=?", [st]


# ---------- ingest ----------------------------------------------------
def import_service_lines(rows, principal=None):
    n_lines = n_sys = skipped = 0
    with tx() as con:
        for r in rows:
            lid = str(r.get("external_line_id") or "").strip()
            if not lid:
                skipped += 1
                continue
            pw = str(r.get("pwsid") or "").strip().upper()
            st = _state(pw)
            # utility/state tenants may only ingest within their scope
            if principal and principal["kind"] in ("UTILITY", "STATE") \
                    and not pw:
                skipped += 1
                continue
            if principal and principal["kind"] == "UTILITY" \
                    and pw != principal["scope_key"]:
                skipped += 1
                continue
            if principal and principal["kind"] == "STATE" \
                    and st != principal["scope_key"].upper():
                skipped += 1
                continue
            lat, lon = _coords(r)
            geom = str(r.get("geometry") or "").strip()
            lifecycle = {
                k: str(r.get(k) or "").strip()
                for k in LIFECYCLE_IMPORT_FIELDS
            }
            if pw and not con.execute(
                    "SELECT 1 FROM water_systems WHERE pwsid=?",
                    (pw,)).fetchone():
                rk, p, _ = TB.priority(pw)
                con.execute("INSERT INTO water_systems(pwsid,name,state,"
                            "model_rank,p_lead_rich) VALUES(?,?,?,?,?)",
                            (pw, r.get("name", ""), st, rk, p))
                n_sys += 1
            existing = con.execute("""SELECT id FROM service_lines
                    WHERE pwsid=? AND external_line_id=?""",
                                   (pw, lid)).fetchone()
            if existing:
                lifecycle_sql = ",\n                    ".join(
                    f"{k}=CASE WHEN ?!='' THEN ? ELSE {k} END"
                    for k in LIFECYCLE_IMPORT_FIELDS)
                lifecycle_args = []
                for k in LIFECYCLE_IMPORT_FIELDS:
                    lifecycle_args.extend([lifecycle[k], lifecycle[k]])
                con.execute(f"""UPDATE service_lines SET
                    location=CASE WHEN ?!='' THEN ? ELSE location END,
                    latitude=COALESCE(?, latitude),
                    longitude=COALESCE(?, longitude),
                    geometry=CASE WHEN ?!='' THEN ? ELSE geometry END,
                    install_year=CASE WHEN ?!='' THEN ? ELSE install_year END,
                    current_status=CASE WHEN ?!='' THEN ? ELSE current_status END,
                    {lifecycle_sql}
                    WHERE id=?""",
                            (str(r.get("location") or ""),
                             str(r.get("location") or ""),
                             lat, lon, geom, geom,
                             str(r.get("install_year") or ""),
                             str(r.get("install_year") or ""),
                             str(r.get("current_status") or ""),
                             str(r.get("current_status") or ""),
                             *lifecycle_args, existing["id"]))
                continue
            cols = [
                "external_line_id", "pwsid", "state", "location",
                "latitude", "longitude", "geometry", "install_year",
                "current_status", *LIFECYCLE_IMPORT_FIELDS,
            ]
            values = [
                lid, pw, st, str(r.get("location") or ""), lat, lon, geom,
                str(r.get("install_year") or ""),
                str(r.get("current_status") or ""),
                *(lifecycle[k] for k in LIFECYCLE_IMPORT_FIELDS),
            ]
            con.execute(
                f"INSERT INTO service_lines({','.join(cols)}) "
                f"VALUES({','.join('?' for _ in cols)})",
                values)
            n_lines += 1
        log_event(con, "INGEST", f"{n_lines} lines / {n_sys} systems")
    return {"lines_imported": n_lines, "systems_added": n_sys,
            "rows_skipped": skipped}


def replace_system_inventory(rows, source="SDWIS"):
    """Replace the real system-level SDWIS service-line inventory layer."""
    inserted = skipped = 0
    with tx() as con:
        con.execute("DELETE FROM system_inventory")
        for r in rows:
            pwsid = str(r.get("pwsid") or "").strip().upper()
            if not pwsid:
                skipped += 1
                continue
            lat = _clean_float(r.get("latitude"))
            lon = _clean_float(r.get("longitude"))
            if not _valid_latlon(lat, lon):
                lat = lon = None
            def iv(key):
                try:
                    return int(float(str(r.get(key) or "0").replace(",", "")))
                except ValueError:
                    return 0
            con.execute("""INSERT INTO system_inventory(
                pwsid,pws_name,state,source_quarter,lead_count,grr_count,
                unknown_count,nonlead_count,total_count,report_status,
                pws_type,activity_status,primacy_agency,epa_region,
                population_served,latitude,longitude,inventory_source)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (pwsid, str(r.get("pws_name") or ""),
                         str(r.get("state") or _state(pwsid)).upper(),
                         str(r.get("source_quarter") or ""),
                         iv("lead_count"), iv("grr_count"),
                         iv("unknown_count"), iv("nonlead_count"),
                         iv("total_count"), str(r.get("report_status") or ""),
                         str(r.get("pws_type") or ""),
                         str(r.get("activity_status") or ""),
                         str(r.get("primacy_agency") or ""),
                         str(r.get("epa_region") or ""),
                         iv("population_served"), lat, lon, source))
            inserted += 1
        log_event(con, "SYSTEM_INVENTORY",
                  f"{inserted} systems from {source}")
    return {"systems_imported": inserted, "rows_skipped": skipped,
            "source": source}


def onboard_national_universe(limit=None):
    """Seed water_systems for the whole triage universe so any state /
    utility nationwide can be activated without manual import."""
    rank = TB.load()
    added = 0
    with tx() as con:
        for i, (pw, (rk, p)) in enumerate(rank.items()):
            if limit and i >= limit:
                break
            if con.execute("SELECT 1 FROM water_systems WHERE pwsid=?",
                            (pw,)).fetchone():
                continue
            con.execute("INSERT INTO water_systems(pwsid,name,state,"
                        "model_rank,p_lead_rich) VALUES(?,?,?,?,?)",
                        (pw, "", _state(pw), rk, p))
            added += 1
        log_event(con, "ONBOARD_NATIONAL", f"{added} systems")
    return {"systems_onboarded": added,
            "national_universe": len(rank)}


# ---------- pipeline --------------------------------------------------
def generate_inspection_work_orders(horizon=DEFAULT_HORIZON_YEARS,
                                    principal=None):
    sc, pa = _scope_sql(principal, "s.pwsid", "s.state")
    created = 0
    with tx() as con:
        rows = con.execute(f"""
            SELECT s.id,s.pwsid,s.state,s.current_status,
                   w.model_rank rk, w.p_lead_rich p
            FROM service_lines s
            LEFT JOIN water_systems w ON w.pwsid=s.pwsid
            WHERE {sc} AND NOT EXISTS(
              SELECT 1 FROM work_orders o WHERE o.line_id=s.id
              AND o.type='INSPECTION' AND o.status!='CANCELLED')""",
                           pa).fetchall()
        for r in rows:
            st = (r["current_status"] or "").strip().lower()
            if st and st not in ("unknown", "lead status unknown", ""):
                continue
            con.execute("INSERT INTO work_orders(line_id,pwsid,state,"
                        "type,status,priority_rank,p_lead_rich,due_year)"
                        " VALUES(?,?,?,?,?,?,?,?)",
                        (r["id"], r["pwsid"], r["state"], "INSPECTION",
                         "OPEN",
                         r["rk"] if r["rk"] is not None else 10 ** 9,
                         r["p"], horizon))
            created += 1
        log_event(con, "PRIORITIZE", f"{created} inspection WOs")
    return {"inspection_work_orders_created": created}


def generate_renewal_work_orders(principal=None,
                                  service_life=ASSET_SERVICE_LIFE_YEARS):
    """Post-LCRI usefulness: once a line is non-lead, flag it for
    RENEWAL when it has passed its expected service life (age from
    install_year). Honest: an age/material rule-of-thumb, not a measured
    failure model."""
    yr = datetime.date.today().year
    sc, pa = _scope_sql(principal, "s.pwsid", "s.state")
    created = 0
    with tx() as con:
        rows = con.execute(f"""SELECT s.id,s.pwsid,s.state,
            s.install_year iy, s.current_status cs,
            s.expected_service_life_years expected_life
            FROM service_lines s WHERE {sc}
            AND s.install_year IS NOT NULL AND s.install_year!=''
            AND (lower(COALESCE(s.current_status,'')) LIKE 'non-lead%'
              OR lower(COALESCE(s.current_status,'')) LIKE 'renewed%')
            AND NOT EXISTS(SELECT 1 FROM work_orders o
              WHERE o.line_id=s.id AND o.type='RENEWAL'
              AND o.status!='CANCELLED')""", pa).fetchall()
        for r in rows:
            try:
                age = yr - int(str(r["iy"])[:4])
            except (TypeError, ValueError):
                continue
            line_life = _service_life_years(r["expected_life"], service_life)
            if age < line_life:
                continue
            overdue = age - line_life
            con.execute("INSERT INTO work_orders(line_id,pwsid,state,"
                        "type,status,priority_rank,rehab_year) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (r["id"], r["pwsid"], r["state"], "RENEWAL",
                         "OPEN", max(1, 1000 - overdue), yr))
            created += 1
        log_event(con, "RENEWALS", f"{created} renewal WOs")
    return {"renewal_work_orders_created": created,
            "service_life_years": service_life}


# ---------- work-order queue & lifecycle ------------------------------
def list_work_orders(status=None, wo_type=None, principal=None,
                     limit=200, offset=0):
    sc, pa = _scope_sql(principal, "o.pwsid", "o.state")
    q = [f"""SELECT o.*, s.external_line_id sid, s.location loc,
         u.name tech FROM work_orders o
         JOIN service_lines s ON s.id=o.line_id
         LEFT JOIN users u ON u.id=o.assigned_to WHERE {sc}"""]
    a = list(pa)
    if status:
        q.append("AND o.status=?")
        a.append(status)
    if wo_type:
        q.append("AND o.type=?")
        a.append(wo_type)
    q.append("""ORDER BY CASE o.type WHEN 'REHAB' THEN o.rehab_year
             ELSE 0 END, o.priority_rank ASC, o.id ASC
             LIMIT ? OFFSET ?""")
    a += [limit, offset]
    with tx() as con:
        return [dict(x) for x in con.execute(" ".join(q), a).fetchall()]


def my_work_orders(token, limit=300):
    """The field technician's output: work the system is sending them.
    'assigned' = open/in-progress work orders assigned to this user;
    'available' = unclaimed OPEN work in their tenant scope they may
    claim. Ordered REHAB-by-year then by model priority. Covers BOTH
    inspection and rehabilitation."""
    p = get_principal(token)
    if not p:
        return None
    sc, pa = _scope_sql(p, "o.pwsid", "o.state")
    types = ROLE_TYPES.get(p["role"], ("INSPECTION", "REHAB", "RENEWAL"))
    tph = ",".join("?" * len(types))
    base = (f"""SELECT o.id,o.type,o.status,o.priority_rank,
        o.rehab_year,o.p_lead_rich,o.pwsid,o.state,
        s.external_line_id sid,s.location loc
        FROM work_orders o JOIN service_lines s ON s.id=o.line_id
        WHERE {sc} AND o.type IN ({tph})
        AND o.status!='DONE' AND o.status!='CANCELLED' """)
    pa = pa + list(types)
    order = (" ORDER BY CASE WHEN o.type IN ('REHAB','RENEWAL') "
             "THEN o.rehab_year ELSE 0 END, o.priority_rank ASC, "
             "o.id ASC LIMIT ?")
    with tx() as con:
        assigned = [dict(x) for x in con.execute(
            base + "AND o.assigned_to=?" + order,
            pa + [p["uid"], limit]).fetchall()]
        available = [dict(x) for x in con.execute(
            base + "AND o.assigned_to IS NULL AND o.status='OPEN'"
            + order, pa + [limit]).fetchall()]
    return {"technician": p["un"], "tenant": p["tn"],
            "scope": p["kind"], "assigned": assigned,
            "available": available}


def technician_worksheet_rows(token):
    """Flat rows for the downloadable field work sheet (what the tech
    takes out for inspections / rehabilitation)."""
    mw = my_work_orders(token)
    if mw is None:
        return None
    rows = []
    for bucket, items in (("ASSIGNED", mw["assigned"]),
                          ("AVAILABLE", mw["available"])):
        for w in items:
            rows.append([
                bucket, w["id"], w["type"], w["status"],
                w["rehab_year"] if w["type"] == "REHAB"
                else (w["priority_rank"] if w["priority_rank"] and
                      w["priority_rank"] < 999999999 else ""),
                w["sid"], w["pwsid"], w["state"], w["loc"] or ""])
    return rows


def get_work_order(wo_id, principal=None):
    with tx() as con:
        r = con.execute("""SELECT o.*, s.external_line_id sid,
            s.location loc, s.install_year iy FROM work_orders o
            JOIN service_lines s ON s.id=o.line_id WHERE o.id=?""",
                        (wo_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        return d if _in_scope(principal, d["pwsid"], d["state"]) else None


def claim_work_order(wo_id, token):
    """Concurrency-safe: a single atomic UPDATE guarded on status, so two
    technicians nationwide cannot grab the same work order."""
    p = get_principal(token)
    if not p:
        return False, "unknown/!inactive user token"
    types = allowed_types(p)
    if not types:
        return False, "role cannot claim work orders"
    sc, pa = _scope_sql(p, "pwsid", "state")
    tph = ",".join("?" * len(types))
    with tx() as con:
        cur = con.execute(
            "UPDATE work_orders SET status='IN_PROGRESS',assigned_to=?,"
            "updated_at=CURRENT_TIMESTAMP WHERE id=? AND status IN "
            f"('OPEN','ASSIGNED') AND {sc} AND type IN ({tph})",
            [p["uid"], wo_id] + pa + list(types))
        if cur.rowcount != 1:
            r = con.execute("SELECT status,type,pwsid,state FROM work_orders WHERE id=?",
                            (wo_id,)).fetchone()
            if not r:
                return False, "work order not found"
            if not _in_scope(p, r["pwsid"], r["state"]):
                return False, "work order outside tenant scope"
            if r["type"] not in types:
                return False, f"{p['role']} cannot claim {r['type']} orders"
            return False, f"already {r['status']}"
        log_event(con, "CLAIM", f"WO {wo_id} -> {p['un']}")
    return True, p["un"]


def submit_result(wo_id, payload, token=None):
    clean, errs = SD.validate_result(payload)
    if errs:
        return False, errs
    p = get_principal(token)
    if not p:
        return False, ["valid user token required"]
    with tx() as con:
        wo = con.execute("SELECT * FROM work_orders WHERE id=?",
                         (wo_id,)).fetchone()
        if not wo:
            return False, ["work order not found"]
        if not _in_scope(p, wo["pwsid"], wo["state"]):
            return False, ["work order outside tenant scope"]
        if wo["type"] != "INSPECTION":
            return False, ["not an inspection work order"]
        if "INSPECTION" not in allowed_types(p):
            return False, [f"{p['role']} cannot complete inspection orders"]
        if wo["assigned_to"] not in (None, p["uid"]) and \
                p["role"] not in ("DISPATCHER", "ADMIN"):
            return False, ["work order assigned to another user"]
        if wo["assigned_to"] is None and p["role"] == "INSPECTOR":
            return False, ["claim the work order before submitting a result"]
        if wo["status"] == "DONE":
            return False, ["work order already completed"]
        rk, pred_p, lab = TB.priority(wo["pwsid"])
        con.execute("DELETE FROM investigations WHERE work_order_id=?",
                    (wo_id,))
        con.execute("""INSERT INTO investigations(
            work_order_id,line_id,pwsid,state,system_side_material,
            customer_side_material,overall_classification,
            basis_of_classification,install_year,investigation_method,
            investigation_date,inspector,investigation_cost,predicted_p,
            predicted_label,confirmed_status,
            disadvantaged_community_flag,notes,photo_ref)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (wo_id, wo["line_id"], wo["pwsid"], wo["state"],
                     clean["system_side_material"],
                     clean["customer_side_material"],
                     clean["overall_classification"],
                     clean["basis_of_classification"],
                     clean["install_year"],
                     clean["investigation_method"],
                     clean["investigation_date"], clean["inspector"],
                     clean["investigation_cost"],
                     f"{pred_p:.4f}" if pred_p is not None else "", lab,
                     clean["confirmed_status"],
                     clean["disadvantaged_community_flag"],
                     clean["notes"], clean["photo_ref"]))
        con.execute("UPDATE work_orders SET status='DONE',"
                    "updated_at=CURRENT_TIMESTAMP WHERE id=?", (wo_id,))
        con.execute("UPDATE service_lines SET current_status=? "
                    "WHERE id=?",
                    (clean["overall_classification"], wo["line_id"]))
        log_event(con, "RESULT",
                  f"WO {wo_id} -> {clean['overall_classification']}")
    return True, clean["overall_classification"]


def complete_rehab(wo_id, payload, token=None):
    p = get_principal(token)
    if not p:
        return False, "valid user token required"
    with tx() as con:
        wo = con.execute("SELECT * FROM work_orders WHERE id=?",
                         (wo_id,)).fetchone()
        if not wo:
            return False, "work order not found"
        if not _in_scope(p, wo["pwsid"], wo["state"]):
            return False, "work order outside tenant scope"
        if wo["type"] not in ("REHAB", "RENEWAL"):
            return False, "not a replacement/renewal work order"
        if wo["type"] not in allowed_types(p):
            return False, f"{p['role']} cannot complete {wo['type']} orders"
        if wo["assigned_to"] not in (None, p["uid"]) and \
                p["role"] not in ("DISPATCHER", "ADMIN"):
            return False, "work order assigned to another user"
        if wo["assigned_to"] is None and p["role"] == "REHAB":
            return False, "claim the work order before completing it"
        if wo["status"] == "DONE":
            return False, "already completed"
        con.execute("UPDATE work_orders SET status='DONE',"
                    "updated_at=CURRENT_TIMESTAMP WHERE id=?", (wo_id,))
        newstat = ("Renewed (asset replaced)"
                   if wo["type"] == "RENEWAL"
                   else "Non-lead (replaced)")
        con.execute("UPDATE service_lines SET current_status=?,"
                    "install_year=? WHERE id=?",
                    (newstat, str(datetime.date.today().year),
                     wo["line_id"]))
        log_event(con, f"{wo['type']}_DONE", f"WO {wo_id}")
    return True, "completion recorded"


# ---------- rehabilitation planning -----------------------------------
def build_rehab_plan(annual_budget, horizon=DEFAULT_HORIZON_YEARS,
                     default_cost=DEFAULT_REPLACE_COST, principal=None):
    sc, pa = _scope_sql(principal, "s.pwsid", "s.state")
    with tx() as con:
        rows = con.execute(f"""SELECT s.id lid,s.pwsid pw,s.state st,
            i.overall_classification oc,
            COALESCE(NULLIF(i.install_year,''),s.install_year) iy,
            i.investigation_cost ic,i.disadvantaged_community_flag dc
            FROM investigations i JOIN service_lines s ON s.id=i.line_id
            WHERE {sc} AND i.overall_classification IN
            ('Lead','Galvanized Requiring Replacement')""",
                           pa).fetchall()
    lines = [{"line_id": r["lid"], "pwsid": r["pw"],
              "classification": r["oc"], "install_year": r["iy"],
              "disadvantaged": r["dc"] == "Y", "cost": r["ic"],
              "_state": r["st"]} for r in rows]
    plan = RE.plan(lines, annual_budget=annual_budget, horizon=horizon,
                   default_cost=default_cost,
                   equity_mult=EQUITY_MULTIPLIER)
    if "error" in plan:
        return plan
    smap = {r["lid"]: r["st"] for r in rows}
    with tx() as con:
        pid = con.insert_id(
            "INSERT INTO rehab_plans(scope,params_json,summary_json) "
            "VALUES(?,?,?)",
            (principal["kind"] if principal else "NATIONAL",
             json.dumps({"annual_budget": annual_budget,
                         "horizon": horizon}),
             json.dumps(plan["summary"])))
        for it in plan["schedule"]:
            stt = smap.get(it["line_id"], _state(it["pwsid"]))
            con.execute("""INSERT INTO rehab_plan_items(plan_id,line_id,
                pwsid,state,classification,year,cost,weight,
                disadvantaged) VALUES(?,?,?,?,?,?,?,?,?)""",
                        (pid, it["line_id"], it["pwsid"], stt,
                         it["classification"], it["year"], it["cost"],
                         it["weight"], 1 if it["disadvantaged"] else 0))
            if not con.execute("""SELECT 1 FROM work_orders
                WHERE line_id=? AND type='REHAB'
                AND status!='CANCELLED'""", (it["line_id"],)).fetchone():
                con.execute("INSERT INTO work_orders(line_id,pwsid,state,"
                            "type,status,priority_rank,rehab_year) "
                            "VALUES(?,?,?,?,?,?,?)",
                            (it["line_id"], it["pwsid"], stt, "REHAB",
                             "OPEN", it["year"], it["year"]))
        log_event(con, "REHAB_PLAN", f"plan {pid}")
    plan["plan_id"] = pid
    return plan


# ---------- exports & rollups -----------------------------------------
def register_rows(principal=None):
    sc, pa = _scope_sql(principal, "i.pwsid", "i.state")
    with tx() as con:
        rs = con.execute(f"""SELECT s.external_line_id,i.* FROM
            investigations i JOIN service_lines s ON s.id=i.line_id
            WHERE {sc} ORDER BY i.work_order_id""", pa).fetchall()
    return [dict(r) for r in rs]


def service_line_map_data(principal=None, limit=5000, state_filter=None):
    """Scoped service-line map payload with material composition.

    The frontend consumes this as live JSON. Geometry comes from WKT/GeoJSON,
    explicit latitude/longitude columns, or parsed coordinate text in location.
    """
    sc, pa = _scope_sql(principal, "s.pwsid", "s.state")
    sf, sfa = _state_focus_sql(principal, state_filter, "s.state")
    with tx() as con:
        rows = con.execute(f"""SELECT s.id,s.external_line_id,s.pwsid,
            s.state,s.location,s.latitude,s.longitude,s.geometry,s.install_year,
            s.expected_service_life_years,s.replacement_year,
            s.diameter_in,s.length_ft,s.ownership_side,
            s.verification_method,s.evidence_source,s.confidence_score,
            s.current_status,w.name system_name,w.model_rank,w.p_lead_rich,
            o.id work_order_id,o.type work_order_type,
            o.status work_order_status,o.priority_rank,o.rehab_year,
            u.name assigned_to_name,
            i.system_side_material,i.customer_side_material,
            i.overall_classification,i.basis_of_classification,
            i.investigation_method,i.investigation_date,i.notes
            FROM service_lines s
            LEFT JOIN water_systems w ON w.pwsid=s.pwsid
            LEFT JOIN work_orders o ON o.id=(
              SELECT oo.id FROM work_orders oo WHERE oo.line_id=s.id
              ORDER BY CASE oo.status
                WHEN 'IN_PROGRESS' THEN 0 WHEN 'OPEN' THEN 1
                WHEN 'ASSIGNED' THEN 2 WHEN 'DONE' THEN 3 ELSE 4 END,
                oo.updated_at DESC,oo.id DESC LIMIT 1)
            LEFT JOIN users u ON u.id=o.assigned_to
            LEFT JOIN investigations i ON i.work_order_id=(
              SELECT ii.work_order_id FROM investigations ii
              WHERE ii.line_id=s.id
              ORDER BY ii.created_at DESC,ii.work_order_id DESC LIMIT 1)
            WHERE {sc}{sf}
            ORDER BY s.state,s.pwsid,s.external_line_id
            LIMIT ?""", pa + sfa + [int(limit)]).fetchall()
    features = []
    counts = {}
    mapped = 0
    for r in rows:
        d = dict(r)
        material = _material_bucket(d.get("overall_classification")
                                    or d.get("current_status"))
        counts[material] = counts.get(material, 0) + 1
        geometry = _map_geometry(d)
        lat, lon = _representative_coord(geometry)
        if geometry:
            mapped += 1
        else:
            lat = lon = None
        lifecycle = _lifecycle_metrics(d)
        features.append({
            "id": d["id"],
            "service_line_id": d["external_line_id"],
            "pwsid": d["pwsid"],
            "system_name": d.get("system_name") or "",
            "state": d["state"],
            "location": d.get("location") or "",
            "latitude": lat,
            "longitude": lon,
            "geometry": geometry,
            "install_year": d.get("install_year") or "",
            "expected_service_life_years":
                lifecycle["expected_service_life_years"],
            "service_life_basis": lifecycle["service_life_basis"],
            "asset_age_years": lifecycle["asset_age_years"],
            "remaining_life_years": lifecycle["remaining_life_years"],
            "renewal_due_year": lifecycle["renewal_due_year"],
            "lifecycle_flag": lifecycle["lifecycle_flag"],
            "replacement_year": d.get("replacement_year") or "",
            "diameter_in": d.get("diameter_in") or "",
            "length_ft": d.get("length_ft") or "",
            "ownership_side": d.get("ownership_side") or "",
            "verification_method": d.get("verification_method") or "",
            "evidence_source": d.get("evidence_source") or "",
            "confidence_score": d.get("confidence_score") or "",
            "material": material,
            "current_status": d.get("current_status") or "",
            "model_rank": d.get("model_rank"),
            "p_lead_rich": d.get("p_lead_rich"),
            "work_order_id": d.get("work_order_id"),
            "work_order_type": d.get("work_order_type"),
            "work_order_status": d.get("work_order_status"),
            "priority_rank": d.get("priority_rank"),
            "rehab_year": d.get("rehab_year"),
            "assigned_to": d.get("assigned_to_name") or "",
            "system_side_material": d.get("system_side_material") or "",
            "customer_side_material": d.get("customer_side_material") or "",
            "overall_classification": d.get("overall_classification") or "",
            "basis_of_classification": d.get("basis_of_classification") or "",
            "investigation_method": d.get("investigation_method") or "",
            "investigation_date": d.get("investigation_date") or "",
            "notes": d.get("notes") or "",
        })
    total = len(features)
    ordered = [m for m in SD.SDWIS_CATEGORIES if m in counts]
    ordered += sorted(m for m in counts if m not in ordered)
    composition = [{"material": m, "count": counts[m],
                    "pct": (counts[m] / total * 100) if total else 0}
                   for m in ordered]
    return {
        "layer": "service_lines",
        "labels": {"total": "Service lines", "mapped": "Mapped",
                   "unmapped": "Unmapped"},
        "generated_at": datetime.datetime.utcnow().isoformat(timespec="seconds")
        + "Z",
        "scope": (principal["kind"] if principal else "NATIONAL"),
        "scope_key": (principal["scope_key"] if principal else "*"),
        "state_filter": str(state_filter or "").strip().upper(),
        "counts": {"total": total, "mapped": mapped,
                   "unmapped": total - mapped},
        "composition": composition,
        "features": features,
    }


def system_inventory_map_data(principal=None, limit=100000, state_filter=None):
    """Real SDWIS system-level service-line inventory map layer.

    This is not pipe-by-pipe street geometry. It is the real public SDWIS
    inventory counts joined to public water-system geography.
    """
    sc, pa = _scope_sql(principal, "pwsid", "state")
    sf, sfa = _state_focus_sql(principal, state_filter, "state")
    with tx() as con:
        rows = con.execute(f"""SELECT * FROM system_inventory
            WHERE {sc}{sf}
            ORDER BY total_count DESC,pwsid
            LIMIT ?""", pa + sfa + [int(limit)]).fetchall()
    features = []
    counts = {m: 0 for m in SD.SDWIS_CATEGORIES}
    mapped = 0
    total_lines = 0
    for r in rows:
        d = dict(r)
        lead = int(d.get("lead_count") or 0)
        grr = int(d.get("grr_count") or 0)
        unk = int(d.get("unknown_count") or 0)
        non = int(d.get("nonlead_count") or 0)
        total = int(d.get("total_count") or (lead + grr + unk + non))
        total_lines += total
        counts["Lead"] += lead
        counts["Galvanized Requiring Replacement"] += grr
        counts["Lead Status Unknown"] += unk
        counts["Non-lead"] += non
        material = max((
            ("Lead", lead),
            ("Galvanized Requiring Replacement", grr),
            ("Lead Status Unknown", unk),
            ("Non-lead", non),
        ), key=lambda x: x[1])[0] if total else "Lead Status Unknown"
        lat, lon = d.get("latitude"), d.get("longitude")
        geometry = None
        if _valid_latlon(lat, lon):
            mapped += 1
            geometry = {"type": "Point", "coordinates": [lon, lat]}
        else:
            lat = lon = None
        features.append({
            "id": d["pwsid"],
            "feature_kind": "system_inventory",
            "service_line_id": d["pwsid"],
            "pwsid": d["pwsid"],
            "system_name": d.get("pws_name") or "",
            "state": d.get("state") or "",
            "location": d.get("pws_name") or "",
            "latitude": lat,
            "longitude": lon,
            "geometry": geometry,
            "material": material,
            "source_quarter": d.get("source_quarter") or "",
            "install_year": "",
            "asset_age_years": None,
            "remaining_life_years": None,
            "expected_service_life_years": ASSET_SERVICE_LIFE_YEARS,
            "service_life_basis": "Not available in public SDWIS",
            "lifecycle_flag": "Requires utility asset data",
            "renewal_due_year": None,
            "lifecycle_note": "Public SDWIS inventory has material counts "
            "by system, but no install year or pipe lifespan fields.",
            "lead_count": lead,
            "grr_count": grr,
            "unknown_count": unk,
            "nonlead_count": non,
            "total_count": total,
            "report_status": d.get("report_status") or "",
            "pws_type": d.get("pws_type") or "",
            "activity_status": d.get("activity_status") or "",
            "primacy_agency": d.get("primacy_agency") or "",
            "epa_region": d.get("epa_region") or "",
            "population_served": d.get("population_served") or 0,
            "inventory_source": d.get("inventory_source") or "",
            "current_status": d.get("report_status") or "",
        })
    composition = [{"material": m, "count": counts[m],
                    "pct": (counts[m] / total_lines * 100)
                    if total_lines else 0}
                   for m in SD.SDWIS_CATEGORIES if counts[m]]
    return {
        "layer": "system_inventory",
        "labels": {"total": "Reported lines", "mapped": "Mapped systems",
                   "unmapped": "Unmapped systems"},
        "generated_at": datetime.datetime.utcnow().isoformat(timespec="seconds")
        + "Z",
        "scope": (principal["kind"] if principal else "NATIONAL"),
        "scope_key": (principal["scope_key"] if principal else "*"),
        "state_filter": str(state_filter or "").strip().upper(),
        "counts": {"total": total_lines, "mapped": mapped,
                   "unmapped": len(features) - mapped,
                   "systems": len(features)},
        "composition": composition,
        "features": features,
    }


def map_data(principal=None, state_filter=None):
    """Prefer real SDWIS inventory when loaded; fall back to local lines."""
    inv = system_inventory_map_data(principal, state_filter=state_filter)
    if inv["counts"]["systems"] > 0:
        return inv
    return service_line_map_data(principal, state_filter=state_filter)


def sdwis_rollup_rows(principal=None):
    sc, pa = _scope_sql(principal, "i.pwsid", "i.state")
    with tx() as con:
        rs = con.execute(f"""SELECT i.pwsid pw,
            i.overall_classification oc FROM investigations i
            WHERE {sc}""", pa).fetchall()
    agg = {}
    for r in rs:
        d = agg.setdefault(r["pw"], {c: 0 for c in SD.SDWIS_CATEGORIES})
        if r["oc"] in d:
            d[r["oc"]] += 1
    return [[pid, d["Lead"], d["Galvanized Requiring Replacement"],
             d["Lead Status Unknown"], d["Non-lead"], sum(d.values())]
            for pid, d in sorted(agg.items())]


def feedback_rows(principal=None):
    sc, pa = _scope_sql(principal, "pwsid", "state")
    with tx() as con:
        rs = con.execute(f"""SELECT pwsid,line_id,predicted_p,
            predicted_label,confirmed_status,basis_of_classification,
            investigation_date FROM investigations WHERE {sc}""",
                         pa).fetchall()
    return [list(r.values()) if hasattr(r, "values") else list(r)
            for r in rs]


def rehab_rows(plan_id=None, principal=None):
    sc, pa = _scope_sql(principal, "pwsid", "state")
    with tx() as con:
        if plan_id is None:
            m = con.execute(
                f"SELECT MAX(plan_id) m FROM rehab_plan_items WHERE {sc}",
                pa).fetchone()["m"]
            plan_id = m
        if plan_id is None:
            return []
        rs = con.execute(f"""SELECT year,pwsid,state,line_id,
            classification,cost,weight,disadvantaged FROM
            rehab_plan_items WHERE plan_id=? AND {sc}
            ORDER BY year,weight DESC""", [plan_id] + pa).fetchall()
    return [list(r.values()) if hasattr(r, "values") else list(r)
            for r in rs]


def inventory_overview(principal=None, state_limit=12):
    """Lightweight dashboard summary for the real SDWIS inventory layer."""
    sc, pa = _scope_sql(principal, "pwsid", "state")
    with tx() as con:
        summary = con.execute(f"""SELECT COUNT(*) systems,
            SUM(CASE WHEN latitude IS NOT NULL AND longitude IS NOT NULL
                THEN 1 ELSE 0 END) mapped_systems,
            SUM(lead_count) lead,
            SUM(grr_count) grr,
            SUM(unknown_count) unknown,
            SUM(nonlead_count) nonlead,
            SUM(total_count) total,
            MAX(source_quarter) source_quarter
            FROM system_inventory WHERE {sc}""", pa).fetchone()
        state_rows = con.execute(f"""SELECT state,
            COUNT(*) systems,
            SUM(CASE WHEN latitude IS NOT NULL AND longitude IS NOT NULL
                THEN 1 ELSE 0 END) mapped_systems,
            SUM(lead_count) lead,
            SUM(grr_count) grr,
            SUM(unknown_count) unknown,
            SUM(nonlead_count) nonlead,
            SUM(total_count) total
            FROM system_inventory WHERE {sc}
            GROUP BY state ORDER BY
            SUM(lead_count + grr_count + unknown_count) DESC,
            SUM(total_count) DESC LIMIT ?""",
                                 pa + [int(state_limit)]).fetchall()
    lead = int(summary["lead"] or 0)
    grr = int(summary["grr"] or 0)
    unknown = int(summary["unknown"] or 0)
    nonlead = int(summary["nonlead"] or 0)
    total = int(summary["total"] or 0)
    systems = int(summary["systems"] or 0)
    mapped = int(summary["mapped_systems"] or 0)
    replacement = lead + grr
    risk = replacement + unknown
    composition = []
    for material, count in (
            ("Lead", lead),
            ("Galvanized Requiring Replacement", grr),
            ("Lead Status Unknown", unknown),
            ("Non-lead", nonlead)):
        pct = (count / total * 100) if total else 0
        composition.append({"material": material, "count": count,
                            "pct": pct})
    states = []
    max_risk = 0
    for r in state_rows:
        d = dict(r)
        row_total = int(d.get("total") or 0)
        row_risk = int(d.get("lead") or 0) + int(d.get("grr") or 0) + \
            int(d.get("unknown") or 0)
        max_risk = max(max_risk, row_risk)
        states.append({
            "state": d.get("state") or "--",
            "systems": int(d.get("systems") or 0),
            "mapped_systems": int(d.get("mapped_systems") or 0),
            "lead": int(d.get("lead") or 0),
            "grr": int(d.get("grr") or 0),
            "unknown": int(d.get("unknown") or 0),
            "nonlead": int(d.get("nonlead") or 0),
            "total": row_total,
            "risk": row_risk,
            "risk_pct": (row_risk / row_total * 100) if row_total else 0,
        })
    for row in states:
        row["risk_share"] = (row["risk"] / max_risk * 100) if max_risk else 0
    return {
        "loaded": systems > 0,
        "source_quarter": summary["source_quarter"] or "",
        "systems": systems,
        "mapped_systems": mapped,
        "unmapped_systems": systems - mapped,
        "total_lines": total,
        "lead_count": lead,
        "grr_count": grr,
        "replacement_count": replacement,
        "unknown_count": unknown,
        "nonlead_count": nonlead,
        "risk_count": risk,
        "replacement_pct": (replacement / total * 100) if total else 0,
        "unknown_pct": (unknown / total * 100) if total else 0,
        "risk_pct": (risk / total * 100) if total else 0,
        "mapped_pct": (mapped / systems * 100) if systems else 0,
        "composition": composition,
        "states": states,
    }


def register_summary(principal=None):
    sc_sl, pa_sl = _scope_sql(principal, "pwsid", "state")
    sc_i, pa_i = _scope_sql(principal, "i.pwsid", "i.state")
    with tx() as con:
        sl = con.execute(f"""SELECT COUNT(*) total,
            SUM(CASE WHEN current_status IN
              ('Lead','Galvanized Requiring Replacement') THEN 1 ELSE 0 END)
              replacement,
            SUM(CASE WHEN current_status='' OR current_status IS NULL
              OR lower(current_status) LIKE '%unknown%'
              THEN 1 ELSE 0 END) unknown
            FROM service_lines WHERE {sc_sl}""", pa_sl).fetchone()
        inv = con.execute(f"""SELECT overall_classification material,
            COUNT(*) count FROM investigations i WHERE {sc_i}
            GROUP BY overall_classification ORDER BY count DESC""",
                          pa_i).fetchall()
        wo = con.execute(f"""SELECT status,COUNT(*) count
            FROM work_orders WHERE {sc_sl}
            GROUP BY status""", pa_sl).fetchall()
    total = int(sl["total"] or 0)
    replacement = int(sl["replacement"] or 0)
    unknown = int(sl["unknown"] or 0)
    return {
        "service_lines": total,
        "replacement": replacement,
        "unknown": unknown,
        "investigations": sum(int(r["count"] or 0) for r in inv),
        "materials": [dict(r) for r in inv],
        "work_orders": [dict(r) for r in wo],
    }


def asset_lifecycle_summary(principal=None):
    """Asset-age readiness for utility-imported line-level records.

    SDWIS public inventory does not contain install dates or pipe lifespan.
    This summary is intentionally based only on imported utility assets.
    """
    sc, pa = _scope_sql(principal, "pwsid", "state")
    current_year = datetime.date.today().year
    with tx() as con:
        rows = con.execute(f"""SELECT external_line_id,pwsid,state,
            latitude,longitude,geometry,install_year,current_status,
            expected_service_life_years,replacement_year,diameter_in,
            length_ft,ownership_side,verification_method,evidence_source,
            confidence_score
            FROM service_lines WHERE {sc}""", pa).fetchall()
    total = len(rows)
    with_install = 0
    with_geometry = 0
    with_supplied_life = 0
    overdue = 0
    due_5 = 0
    due_10 = 0
    replacement_candidates = 0
    replacement_missing_age = 0
    unknown_missing_age = 0
    nonlead_overdue = 0
    ready_records = 0
    oldest_year = None
    oldest_age = 0
    for r in rows:
        d = dict(r)
        status = (d.get("current_status") or "").strip().lower()
        install_year = _clean_year(d.get("install_year"))
        has_geometry = _valid_latlon(_clean_float(d.get("latitude")),
                                     _clean_float(d.get("longitude"))) or \
            bool(str(d.get("geometry") or "").strip())
        if has_geometry:
            with_geometry += 1
        if _clean_int(d.get("expected_service_life_years")) is not None:
            with_supplied_life += 1
        if status in ("lead", "galvanized requiring replacement"):
            replacement_candidates += 1
            if not install_year:
                replacement_missing_age += 1
        if "unknown" in status and not install_year:
            unknown_missing_age += 1
        if not install_year:
            continue
        with_install += 1
        if has_geometry:
            ready_records += 1
        if oldest_year is None or install_year < oldest_year:
            oldest_year = install_year
            oldest_age = max(0, current_year - install_year)
        lifecycle = _lifecycle_metrics(d, current_year)
        remaining = lifecycle["remaining_life_years"]
        if remaining is None:
            continue
        if remaining < 0:
            overdue += 1
            if status.startswith("non-lead") or status.startswith("renewed"):
                nonlead_overdue += 1
        if remaining <= 5:
            due_5 += 1
        if remaining <= 10:
            due_10 += 1
    return {
        "current_year": current_year,
        "default_service_life_years": ASSET_SERVICE_LIFE_YEARS,
        "total": total,
        "with_install_year": with_install,
        "missing_install_year": total - with_install,
        "with_geometry": with_geometry,
        "missing_geometry": total - with_geometry,
        "with_supplied_service_life": with_supplied_life,
        "using_default_service_life": max(0, with_install - with_supplied_life),
        "overdue": overdue,
        "due_5_years": due_5,
        "due_10_years": due_10,
        "nonlead_overdue": nonlead_overdue,
        "replacement_candidates": replacement_candidates,
        "replacement_missing_age": replacement_missing_age,
        "unknown_missing_age": unknown_missing_age,
        "ready_records": ready_records,
        "oldest_install_year": oldest_year,
        "oldest_age_years": oldest_age,
        "age_coverage_pct": (with_install / total * 100) if total else 0,
        "geometry_coverage_pct": (with_geometry / total * 100) if total else 0,
        "rehab_ready_pct": (ready_records / total * 100) if total else 0,
    }


def asset_lifecycle_export_rows(principal=None):
    sc, pa = _scope_sql(principal, "pwsid", "state")
    current_year = datetime.date.today().year
    with tx() as con:
        rows = con.execute(f"""SELECT external_line_id,pwsid,state,location,
            latitude,longitude,geometry,install_year,current_status,
            expected_service_life_years,replacement_year,diameter_in,
            length_ft,ownership_side,verification_method,evidence_source,
            confidence_score
            FROM service_lines WHERE {sc}
            ORDER BY state,pwsid,external_line_id""", pa).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        lifecycle = _lifecycle_metrics(d, current_year)
        geometry_available = _valid_latlon(_clean_float(d.get("latitude")),
                                           _clean_float(d.get("longitude"))) \
            or bool(str(d.get("geometry") or "").strip())
        gaps = []
        if not _clean_year(d.get("install_year")):
            gaps.append("missing install_year")
        if not geometry_available:
            gaps.append("missing geometry")
        if not str(d.get("evidence_source") or "").strip():
            gaps.append("missing evidence_source")
        if _clean_int(d.get("expected_service_life_years")) is None:
            gaps.append("using default service life")
        out.append({
            "service_line_id": d.get("external_line_id") or "",
            "pwsid": d.get("pwsid") or "",
            "state": d.get("state") or "",
            "location": d.get("location") or "",
            "current_status": d.get("current_status") or "",
            "install_year": d.get("install_year") or "",
            "expected_service_life_years":
                lifecycle["expected_service_life_years"],
            "service_life_basis": lifecycle["service_life_basis"],
            "asset_age_years": lifecycle["asset_age_years"],
            "remaining_life_years": lifecycle["remaining_life_years"],
            "renewal_due_year": lifecycle["renewal_due_year"],
            "lifecycle_flag": lifecycle["lifecycle_flag"],
            "replacement_year": d.get("replacement_year") or "",
            "diameter_in": d.get("diameter_in") or "",
            "length_ft": d.get("length_ft") or "",
            "ownership_side": d.get("ownership_side") or "",
            "verification_method": d.get("verification_method") or "",
            "evidence_source": d.get("evidence_source") or "",
            "confidence_score": d.get("confidence_score") or "",
            "geometry_available": "Y" if geometry_available else "N",
            "readiness_gaps": "; ".join(gaps),
        })
    return out


def system_inventory_export_rows(principal=None):
    sc, pa = _scope_sql(principal, "pwsid", "state")
    with tx() as con:
        rs = con.execute(f"""SELECT pwsid,pws_name,state,source_quarter,
            lead_count,grr_count,unknown_count,nonlead_count,total_count,
            report_status,pws_type,activity_status,population_served
            FROM system_inventory WHERE {sc}
            ORDER BY state,pwsid""", pa).fetchall()
    return [dict(r) for r in rs]


def stats(principal=None):
    sc, pa = _scope_sql(principal, "pwsid", "state")
    with tx() as con:
        def c(table, extra=""):
            q = f"SELECT COUNT(*) c FROM {table} WHERE {sc} {extra}"
            return con.execute(q, pa).fetchone()["c"]
        return {
            "scope": (principal["kind"] if principal else "NATIONAL"),
            "systems": con.execute(
                f"SELECT COUNT(*) c FROM water_systems WHERE {sc}", pa
                ).fetchone()["c"],
            "service_lines": c("service_lines"),
            "work_orders_open": c("work_orders", "AND status='OPEN'"),
            "work_orders_in_progress":
                c("work_orders", "AND status='IN_PROGRESS'"),
            "work_orders_done": c("work_orders", "AND status='DONE'"),
            "inspections_recorded": c("investigations"),
            "model_systems_loaded": TB.loaded_count(),
        }


def rollup_by_state(principal=None):
    """National / scoped rollup by state — the nationwide view."""
    sc, pa = _scope_sql(principal, "pwsid", "state")
    with tx() as con:
        rs = con.execute(f"""SELECT state,
            COUNT(*) lines,
            SUM(CASE WHEN current_status IN ('Lead',
              'Galvanized Requiring Replacement') THEN 1 ELSE 0 END)
              confirmed_replace,
            SUM(CASE WHEN current_status='' OR current_status IS NULL
              OR lower(current_status) LIKE '%unknown%'
              THEN 1 ELSE 0 END) unknown
            FROM service_lines WHERE {sc}
            GROUP BY state ORDER BY confirmed_replace DESC, lines DESC""",
                         pa).fetchall()
    return [dict(r) for r in rs]
