"""End-to-end + unit tests (no pytest/network/browser).

Covers schema logic, rehabilitation scheduling, API authentication,
tenant scoping, role-separated queues, atomic claiming, CSRF-protected web
forms, and exports. Exits non-zero on any failure.
"""
import sys
import json
import re
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # 07_Tool/

from aquacascade_system import config as C        # noqa: E402
for p in (C.DB_PATH, Path(str(C.DB_PATH) + "-wal"),
          Path(str(C.DB_PATH) + "-shm")):
    if p.exists():
        p.unlink()

from aquacascade_system.app import create_app      # noqa: E402
from aquacascade_system.db import db_info, tx      # noqa: E402
from aquacascade_system.auth import hash_token     # noqa: E402
from aquacascade_system.ops import backup_sqlite, restore_sqlite  # noqa: E402
from aquacascade_system import rehab_engine as RE  # noqa: E402
from aquacascade_system import schema_def as SD    # noqa: E402

F = []


def ck(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name +
          ("" if cond else f"  <-- {extra}"))
    if not cond:
        F.append(name)


def csrf(client, path):
    rv = client.get(path)
    m = re.search(r'name="_csrf" value="([^"]+)"',
                  rv.get_data(as_text=True))
    return m.group(1) if m else ""


def login(client, token):
    tok = csrf(client, "/login")
    return client.post("/login", data={"token": token, "_csrf": tok})


def post_form(client, path, data=None, csrf_path=None):
    data = dict(data or {})
    data["_csrf"] = csrf(client, csrf_path or path)
    return client.post(path, data=data)


# ---- unit: schema + rehab engine ----
ck("overall lead", SD.overall_classification("Lead", "Non-lead") == "Lead")
ck("overall unknown",
   SD.overall_classification("Non-lead", "") == "Lead Status Unknown")
cl, er = SD.validate_result({
    "system_side_material": "Lead", "customer_side_material": "Non-lead",
    "basis_of_classification": "Visual inspection",
    "investigation_method": "Visual inspection at meter or point of entry",
    "investigation_date": "2026-05-16"})
ck("validate ok", not er and cl["overall_classification"] == "Lead")
demo = [{"line_id": i, "pwsid": "P", "classification": "Lead",
         "install_year": 1960 + i, "disadvantaged": i == 0,
         "cost": 4000} for i in range(5)]
pl = RE.plan(demo, annual_budget=8000, horizon=5)
ck("rehab schedules all", len(pl["schedule"]) == 5)
ck("rehab equity first",
   [x for x in pl["schedule"] if x["disadvantaged"]][0]["year"] == 1)
ck("rehab infeasible flagged",
   RE.plan(demo, 1000, 1)["summary"]["feasible_within_horizon"] is False)

app = create_app()
c = app.test_client()
KEY = C.get_api_key()
HK = {"X-API-Key": KEY}
H = {**HK, "X-User-Token": "admin-national-001"}

info = db_info()
ck("db migration recorded",
   any(m["version"] == "001_initial_schema" for m in info["migrations"]),
   json.dumps(info, default=str))
ck("token hash migration recorded",
   any(m["version"] == "002_user_token_hashes" for m in info["migrations"]),
   json.dumps(info, default=str))
ck("service-line geo migration recorded",
   any(m["version"] == "003_service_line_geo" for m in info["migrations"]),
   json.dumps(info, default=str))
ck("service-line geometry migration recorded",
   any(m["version"] == "004_service_line_geometry" for m in info["migrations"]),
   json.dumps(info, default=str))
ck("system inventory migration recorded",
   any(m["version"] == "005_system_inventory" for m in info["migrations"]),
   json.dumps(info, default=str))

ck("health open", c.get("/api/v1/health").status_code == 200)
ck("readiness open", c.get("/api/v1/health/ready").status_code == 200)
ck("stats needs key", c.get("/api/v1/stats").status_code == 401)
ck("stats needs user token", c.get("/api/v1/stats",
                                   headers=HK).status_code == 401)
sys_info = c.get("/api/v1/system/info", headers=H).get_json()
ck("system info reports ranking artifact",
   sys_info.get("model_systems_loaded", 0) > 10000
   and sys_info.get("ranking_csv", "").endswith(".csv"),
   json.dumps(sys_info, default=str)[:300])

# ---- national onboarding ----
ob = c.post("/api/v1/onboard-national", headers=H,
            json={"limit": 50}).get_json()
ck("onboard national seeds systems",
   ob["systems_onboarded"] == 50 and ob["national_universe"] > 10000,
   json.dumps(ob))

# ---- multi-tenant create + scoping ----
tx_t = c.post("/api/v1/tenants", headers=H, json={
    "name": "Texas DWP", "kind": "STATE", "scope_key": "TX",
    "user_name": "TX Dispatcher"}).get_json()
il_u = c.post("/api/v1/tenants", headers=H, json={
    "name": "Springfield Water", "kind": "UTILITY",
    "scope_key": "IL0430800", "user_name": "Springfield Disp"}
).get_json()
ck("tenant TX created", tx_t.get("kind") == "STATE" and tx_t.get("token"))
ck("tenant IL utility created",
   il_u.get("kind") == "UTILITY" and il_u.get("token"))
with tx() as con:
    urow = con.execute("""SELECT token,token_hash,token_label
        FROM users WHERE id=?""", (tx_t["user_id"],)).fetchone()
ck("new tenant token stored as hash",
   urow["token"] != tx_t["token"]
   and urow["token_hash"] == hash_token(tx_t["token"])
   and urow["token_label"].startswith("sha256:"),
   json.dumps(dict(urow)))

tenants = c.get("/api/v1/tenants", headers=H).get_json()["items"]
ck("admin can list tenants without secrets",
   any(t["id"] == tx_t["tenant_id"] for t in tenants)
   and all("token" not in t for t in tenants))
users_before = c.get("/api/v1/users", headers=H).get_json()["items"]
ck("admin user list hides bearer tokens",
   any(u["id"] == tx_t["user_id"] for u in users_before)
   and all("token" not in u for u in users_before)
   and all("token_hash" not in u for u in users_before))

created_user = c.post("/api/v1/users", headers=H, json={
    "tenant_id": tx_t["tenant_id"], "name": "TX Inspector",
    "role": "INSPECTOR"}).get_json()
ck("admin creates user token once",
   created_user.get("token", "").startswith("ins_")
   and created_user.get("token_label", "").startswith("sha256:"),
   json.dumps(created_user))
cuH = {**HK, "X-User-Token": created_user["token"]}
ck("created inspector token authenticates",
   c.get("/api/v1/my-work-orders", headers=cuH).status_code == 200)
rotated = c.post(f"/api/v1/users/{created_user['id']}/rotate-token",
                 headers=H).get_json()
ck("rotating user token returns a fresh token",
   rotated.get("token", "") != created_user["token"]
   and rotated.get("token", "").startswith("ins_"),
   json.dumps(rotated))
ck("old rotated token rejected",
   c.get("/api/v1/my-work-orders", headers=cuH).status_code == 401)
rotH = {**HK, "X-User-Token": rotated["token"]}
ck("new rotated token authenticates",
   c.get("/api/v1/my-work-orders", headers=rotH).status_code == 200)
deact = c.post(f"/api/v1/users/{created_user['id']}/deactivate",
               headers=H).get_json()
ck("deactivate user flips active false", deact.get("active") is False,
   json.dumps(deact))
ck("deactivated token rejected",
   c.get("/api/v1/my-work-orders", headers=rotH).status_code == 401)
react = c.post(f"/api/v1/users/{created_user['id']}/activate",
               headers=H).get_json()
ck("reactivate user flips active true", react.get("active") is True,
   json.dumps(react))

# ingest lines in two states (national admin token = national scope)
ing = c.post("/api/v1/ingest/service-lines", headers=H, json={"lines": [
    {"service_line_id": "IL-1", "pwsid": "IL0430800",
     "current_status": "Unknown", "install_year": "1958",
     "latitude": 39.7817, "longitude": -89.6501},
    {"service_line_id": "IL-2", "pwsid": "IL0310450",
     "current_status": "Unknown", "location": "12 River Rd",
     "geometry": "LINESTRING(-87.6301 41.8780,-87.6290 41.8788)"},
    {"service_line_id": "TX-1", "pwsid": "TX0420012",
     "current_status": "Unknown", "latitude": 30.2672,
     "longitude": -97.7431},
    {"service_line_id": "TX-2", "pwsid": "TX0420012",
     "current_status": "Unknown", "location": "30.2700, -97.7400"}]})
ck("national ingest 4 lines",
   ing.get_json()["lines_imported"] == 4, ing.get_data(as_text=True))

c.post("/api/v1/pipeline/prioritize", headers=H)

allwo = c.get("/api/v1/work-orders", headers=H).get_json()["items"]
ck("national sees all 4 WOs", len(allwo) == 4)

txH = {**HK, "X-User-Token": tx_t["token"]}
txwo = c.get("/api/v1/work-orders", headers=txH).get_json()["items"]
ck("TX tenant scoped to TX only",
   len(txwo) == 2 and all(w["state"] == "TX" for w in txwo),
   json.dumps([w["state"] for w in txwo]))

ilH = {**HK, "X-User-Token": il_u["token"]}
ilwo = c.get("/api/v1/work-orders", headers=ilH).get_json()["items"]
ck("IL utility scoped to its pwsid only",
   len(ilwo) == 1 and ilwo[0]["pwsid"] == "IL0430800",
   json.dumps([w["pwsid"] for w in ilwo]))

ck("whoami reflects tenant",
   c.get("/api/v1/whoami", headers=txH).get_json()["kind"] == "STATE")

# ---- atomic concurrency: same WO cannot be claimed twice ----
wid = next(w["id"] for w in allwo if w["sid"] == "IL-1")
c1 = c.post(f"/api/v1/work-orders/{wid}/claim", headers=HK,
            json={"token": "tech-demo-001"})
c2 = c.post(f"/api/v1/work-orders/{wid}/claim", headers=HK,
            json={"token": "tech-demo-001"})
ck("first claim ok", c1.get_json().get("ok") is True)
ck("second claim rejected (atomic)",
   c2.status_code == 409 and c2.get_json().get("ok") is False,
   c2.get_data(as_text=True))

# ---- result + scoped exports ----
rs = c.post(f"/api/v1/work-orders/{wid}/result", headers=HK, json={
    "token": "tech-demo-001",
    "system_side_material": "Lead", "customer_side_material": "Lead",
    "basis_of_classification": "Potholing / vacuum excavation",
    "investigation_method": "Potholing / vacuum excavation",
    "investigation_date": "2026-05-16", "install_year": "1958",
    "disadvantaged_community_flag": "on"})
ck("result -> Lead",
   rs.get_json().get("overall_classification") == "Lead",
   rs.get_data(as_text=True))

map_nat = c.get("/api/v1/service-lines/map", headers=H).get_json()
ck("map endpoint returns mapped service lines",
   map_nat["counts"]["total"] == 4 and map_nat["counts"]["mapped"] == 4,
   json.dumps(map_nat["counts"]))
il1 = next((f for f in map_nat["features"]
            if f["service_line_id"] == "IL-1"), {})
ck("map metadata carries material and work status",
   il1.get("material") == "Lead" and il1.get("work_order_status") == "DONE"
   and il1.get("latitude") is not None and il1.get("longitude") is not None,
   json.dumps(il1))
ck("map composition includes Lead",
   any(x["material"] == "Lead" and x["count"] == 1
       for x in map_nat["composition"]), json.dumps(map_nat["composition"]))
il2 = next((f for f in map_nat["features"]
            if f["service_line_id"] == "IL-2"), {})
ck("map endpoint supports pipeline line geometry",
   il2.get("geometry", {}).get("type") == "LineString"
   and len(il2.get("geometry", {}).get("coordinates", [])) == 2,
   json.dumps(il2))
map_tx = c.get("/api/v1/service-lines/map", headers=txH).get_json()
ck("TX map scoped to TX only",
   map_tx["counts"]["total"] == 2
   and all(f["state"] == "TX" for f in map_tx["features"]),
   json.dumps(map_tx))

st_nat = c.get("/api/v1/stats", headers=H).get_json()
st_tx = c.get("/api/v1/stats", headers=txH).get_json()
ck("national stats >= TX stats service_lines",
   st_nat["service_lines"] == 4 and st_tx["service_lines"] == 2,
   json.dumps([st_nat["service_lines"], st_tx["service_lines"]]))

byst = c.get("/api/v1/rollup/by-state", headers=H).get_json()["states"]
ck("rollup by state present",
   any(r["state"] == "TX" for r in byst)
   and any(r["state"] == "IL" for r in byst))

rp = c.post("/api/v1/rehab/plan", headers=H,
            json={"annual_budget": 100000, "horizon": 5}).get_json()
ck("rehab plan built", rp.get("summary", {}).get("n_lines", 0) >= 1,
   json.dumps(rp)[:200])
rehab_items = c.get("/api/v1/work-orders?type=REHAB",
                    headers=H).get_json()["items"]
ck("rehab REHAB WOs created", any(w["type"] == "REHAB"
                                  for w in rehab_items))
bad_claim = c.post(f"/api/v1/work-orders/{rehab_items[0]['id']}/claim",
                   headers=HK, json={"token": "insp-demo-001"})
ck("inspector cannot claim REHAB work",
   bad_claim.status_code == 409 and "cannot claim REHAB" in
   bad_claim.get_json().get("error", ""), bad_claim.get_data(as_text=True))

reg = c.get("/api/v1/exports/register.csv", headers=H).get_data(as_text=True)
ck("register export has IL-1 Lead", "IL-1" in reg and "Lead" in reg)
reg_tx = c.get("/api/v1/exports/register.csv",
               headers=txH).get_data(as_text=True)
ck("TX-scoped register excludes IL-1", "IL-1" not in reg_tx)

# ---- ROLE SEPARATION: inspector vs replacement crew, distinct ----
ck("my-work-orders needs user token",
   c.get("/api/v1/my-work-orders", headers=HK).status_code == 401)
inspH = {**HK, "X-User-Token": "insp-demo-001"}
rehH = {**HK, "X-User-Token": "rehab-demo-001"}
mi = c.get("/api/v1/my-work-orders", headers=inspH).get_json()
itypes = {w["type"] for w in mi["available"]} | \
    {w["type"] for w in mi["assigned"]}
ck("inspector receives INSPECTION work", "INSPECTION" in itypes,
   json.dumps(list(itypes)))
ck("inspector does NOT see REHAB/RENEWAL (separate role)",
   "REHAB" not in itypes and "RENEWAL" not in itypes,
   json.dumps(list(itypes)))
mr = c.get("/api/v1/my-work-orders", headers=rehH).get_json()
rtypes = {w["type"] for w in mr["available"]} | \
    {w["type"] for w in mr["assigned"]}
ck("replacement crew receives REHAB work", "REHAB" in rtypes,
   json.dumps(list(rtypes)))
ck("replacement crew does NOT see INSPECTION (separate role)",
   "INSPECTION" not in rtypes, json.dumps(list(rtypes)))

# claim moves into assigned (inspector on an inspection WO)
if mi["available"]:
    cwid = mi["available"][0]["id"]
    c.post(f"/api/v1/work-orders/{cwid}/claim", headers=HK,
           json={"token": "insp-demo-001"})
    mi2 = c.get("/api/v1/my-work-orders", headers=inspH).get_json()
    ck("claimed work appears in inspector's assigned list",
       any(w["id"] == cwid for w in mi2["assigned"]))
ws = c.get("/api/v1/my-work-orders.csv",
           headers=inspH).get_data(as_text=True)
ck("work sheet CSV populated",
   "work_order_id" in ws and len(ws.strip().splitlines()) > 1)

# ---- POST-LIFESPAN RENEWAL ----
c.post("/api/v1/ingest/service-lines", headers=H, json={"lines": [
    {"service_line_id": "OLD-1", "pwsid": "IL0430800",
     "current_status": "Non-lead", "install_year": "1928"}]})
rn = c.post("/api/v1/pipeline/renewals", headers=H).get_json()
ck("renewal work orders generated for aged non-lead pipe",
   rn.get("renewal_work_orders_created", 0) >= 1, json.dumps(rn))
mr2 = c.get("/api/v1/my-work-orders", headers=rehH).get_json()
ck("replacement crew receives RENEWAL work",
   any(w["type"] == "RENEWAL" for w in
       mr2["available"] + mr2["assigned"]))

# ---- LOGIN / workspaces / guide / CSRF ----
anon = app.test_client()
ck("anonymous dashboard redirects to login", anon.get("/").status_code == 302)
ck("anonymous import redirects to login", anon.get("/import").status_code == 302)
ck("guide page open", anon.get("/guide").status_code == 200)
ck("login page 200", anon.get("/login").status_code == 200)

lc = app.test_client()
lr = login(lc, "insp-demo-001")
ck("login redirects to inspector workspace",
   lr.status_code == 302 and "/inspections" in lr.headers["Location"],
   lr.headers.get("Location", ""))
ck("inspector workspace 200 after login",
   lc.get("/inspections").status_code == 200)
ck("inspector blocked from dashboard (redirects)",
   lc.get("/").status_code == 302)
ck("logout works", lc.get("/logout").status_code == 302)
ck("login required for /tech (redirect)",
   anon.get("/tech").status_code == 302)

dc = app.test_client()
login(dc, "disp-demo-001")
dash = dc.get("/").get_data(as_text=True)
ck("dispatcher dashboard 200", dc.get("/").status_code == 200)
ck("dashboard does not reveal API key", KEY not in dash)
ck("csrf blocks dispatcher POST without token",
   dc.post("/run-pipeline").status_code == 302)
ck("dispatcher map page 200", dc.get("/map").status_code == 200)
web_map = dc.get("/map/data").get_json()
ck("web map data uses session scope",
   web_map["counts"]["total"] == 5 and web_map["counts"]["mapped"] == 4,
   json.dumps(web_map["counts"]))

ac = app.test_client()
login(ac, "admin-national-001")
ck("web onboard-national with csrf redirects",
   post_form(ac, "/onboard-national").status_code in (302, 200))

# ---- OPERATIONS: SQLite backup/restore ----
with tempfile.TemporaryDirectory() as td:
    backup_path = Path(td) / "aquacascade-backup.db"
    bk = backup_sqlite(backup_path)
    ck("sqlite backup created",
       Path(bk["path"]).exists() and bk["bytes"] > 0, json.dumps(bk))
    probe = c.post("/api/v1/users", headers=H, json={
        "tenant_id": tx_t["tenant_id"], "name": "Restore Probe",
        "role": "INSPECTOR"}).get_json()
    with tx() as con:
        exists_before = con.execute("SELECT 1 FROM users WHERE id=?",
                                    (probe["id"],)).fetchone() is not None
    restore = restore_sqlite(backup_path)
    with tx() as con:
        exists_after = con.execute("SELECT 1 FROM users WHERE id=?",
                                   (probe["id"],)).fetchone() is not None
    ck("sqlite restore reverts later changes",
       exists_before and not exists_after, json.dumps(restore))

print("\n" + ("ALL TESTS PASSED" if not F
              else "FAILURES: " + ", ".join(F)))
sys.exit(1 if F else 0)
