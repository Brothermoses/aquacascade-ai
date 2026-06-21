"""REST API (JSON), /api/v1. Two-layer auth for nationwide multi-tenant
use: X-API-Key (deployment secret) gates the API; X-User-Token
identifies the tenant + role and SCOPES all data (utility / state /
national). /health is open."""
import io
import csv
import functools
import secrets
from flask import Blueprint, request, jsonify, Response
from . import models as M
from . import triage_bridge as TB
from .config import get_api_key, DB_DIALECT, RANKING_CSV
from .db import db_info

api = Blueprint("api", __name__, url_prefix="/api/v1")


def require_key(fn):
    @functools.wraps(fn)
    def w(*a, **k):
        sent = request.headers.get("X-API-Key", "")
        if not secrets.compare_digest(sent, get_api_key()):
            return jsonify(error="unauthorized: X-API-Key"), 401
        return fn(*a, **k)
    return w


def user_token():
    tok = request.headers.get("X-User-Token")
    if tok:
        return tok
    body = request.get_json(silent=True) or {}
    return body.get("token")


def principal(required=False):
    """Resolve the calling tenant/role from X-User-Token (or body
    token)."""
    tok = user_token()
    p = M.get_principal(tok) if tok else None
    if required and not p:
        return None
    return p


def require_roles(*roles):
    def deco(fn):
        @functools.wraps(fn)
        def w(*a, **k):
            p = principal(required=True)
            if not p:
                return jsonify(error="valid X-User-Token required"), 401
            if p["role"] not in roles:
                return jsonify(error="forbidden for role"), 403
            return fn(*a, **k)
        return w
    return deco


def _err(exc, default=400):
    status = 403 if isinstance(exc, PermissionError) else default
    return jsonify(error=str(exc)), status


@api.get("/health")
def health():
    return jsonify(status="ok", service="aquacascade-system",
                   version="v1", tenancy="multi-tenant")


@api.get("/health/ready")
def ready():
    try:
        info = db_info()
        return jsonify(status="ok", database=info["dialect"],
                       migrations=[m["version"] for m in info["migrations"]],
                       model_systems_loaded=TB.loaded_count())
    except Exception as e:
        return jsonify(status="error", error=str(e)), 503


@api.get("/whoami")
@require_key
def whoami():
    p = principal(required=True)
    if not p:
        return jsonify(error="valid X-User-Token required"), 401
    return jsonify(p)


@api.get("/system/info")
@require_key
@require_roles("ADMIN")
def system_info():
    return jsonify(database=db_info(), db_dialect=DB_DIALECT,
                   ranking_csv=str(RANKING_CSV),
                   model_systems_loaded=TB.loaded_count())


@api.get("/stats")
@require_key
@require_roles("DISPATCHER", "ADMIN")
def stats():
    return jsonify(M.stats(principal()))


@api.get("/rollup/by-state")
@require_key
@require_roles("DISPATCHER", "ADMIN")
def rollup_state():
    return jsonify(states=M.rollup_by_state(principal()))


@api.get("/tenants")
@require_key
@require_roles("ADMIN")
def tenants():
    try:
        return jsonify(items=M.list_tenants(principal()))
    except (PermissionError, ValueError) as e:
        return _err(e)


@api.post("/tenants")
@require_key
@require_roles("ADMIN")
def create_tenant():
    p = principal()
    if p["kind"] != "NATIONAL":
        return jsonify(error="national admin token required"), 403
    b = request.get_json(silent=True) or {}
    for f in ("name", "kind", "scope_key", "user_name"):
        if not b.get(f):
            return jsonify(error=f"missing '{f}'"), 400
    if b["kind"].upper() not in ("UTILITY", "STATE", "NATIONAL"):
        return jsonify(error="kind must be UTILITY|STATE|NATIONAL"), 400
    try:
        return jsonify(M.create_tenant(
            b["name"], b["kind"], b["scope_key"], b["user_name"],
            b.get("role", "DISPATCHER")))
    except ValueError as e:
        return jsonify(error=str(e)), 400


@api.get("/users")
@require_key
@require_roles("ADMIN")
def users():
    try:
        return jsonify(items=M.list_users(principal()))
    except (PermissionError, ValueError) as e:
        return _err(e)


@api.post("/users")
@require_key
@require_roles("ADMIN")
def create_user():
    b = request.get_json(silent=True) or {}
    for f in ("tenant_id", "name", "role"):
        if not b.get(f):
            return jsonify(error=f"missing '{f}'"), 400
    try:
        return jsonify(M.create_user(
            int(b["tenant_id"]), b["name"], b["role"], principal()))
    except (PermissionError, ValueError) as e:
        return _err(e)


@api.post("/users/<int:user_id>/rotate-token")
@require_key
@require_roles("ADMIN")
def rotate_user_token(user_id):
    try:
        return jsonify(M.rotate_user_token(user_id, principal()))
    except (PermissionError, ValueError) as e:
        return _err(e)


@api.post("/users/<int:user_id>/deactivate")
@require_key
@require_roles("ADMIN")
def deactivate_user(user_id):
    try:
        return jsonify(M.set_user_active(user_id, False, principal()))
    except (PermissionError, ValueError) as e:
        return _err(e)


@api.post("/users/<int:user_id>/activate")
@require_key
@require_roles("ADMIN")
def activate_user(user_id):
    try:
        return jsonify(M.set_user_active(user_id, True, principal()))
    except (PermissionError, ValueError) as e:
        return _err(e)


@api.post("/onboard-national")
@require_key
@require_roles("ADMIN")
def onboard():
    p = principal()
    if p["kind"] != "NATIONAL":
        return jsonify(error="national admin token required"), 403
    b = request.get_json(silent=True) or {}
    return jsonify(M.onboard_national_universe(limit=b.get("limit")))


@api.post("/ingest/service-lines")
@require_key
@require_roles("DISPATCHER", "ADMIN")
def ingest():
    body = request.get_json(silent=True) or {}
    lines = body.get("lines")
    if not isinstance(lines, list) or not lines:
        return jsonify(error="body must be {'lines':[{...}]}"), 400
    norm = [{"external_line_id": x.get("service_line_id")
             or x.get("external_line_id"),
             "pwsid": x.get("pwsid", ""),
             "location": x.get("location", ""),
             "latitude": x.get("latitude", x.get("lat", "")),
             "longitude": x.get("longitude", x.get("lon", x.get("lng", ""))),
             "geometry": x.get("geometry", x.get("wkt",
                         x.get("geojson", ""))),
             "install_year": x.get("install_year", ""),
             "expected_service_life_years":
             x.get("expected_service_life_years",
                   x.get("service_life_years", "")),
             "replacement_year": x.get("replacement_year", ""),
             "diameter_in": x.get("diameter_in", x.get("diameter", "")),
             "length_ft": x.get("length_ft", x.get("length", "")),
             "ownership_side": x.get("ownership_side", ""),
             "verification_method": x.get("verification_method", ""),
             "evidence_source": x.get("evidence_source", ""),
             "confidence_score": x.get("confidence_score", ""),
             "current_status": x.get("current_status", "")}
            for x in lines]
    return jsonify(M.import_service_lines(norm, principal()))


@api.get("/service-lines/map")
@require_key
@require_roles("DISPATCHER", "ADMIN")
def service_lines_map():
    return jsonify(M.map_data(principal()))


@api.get("/inventory/map")
@require_key
@require_roles("DISPATCHER", "ADMIN")
def inventory_map():
    return jsonify(M.system_inventory_map_data(principal()))


@api.post("/pipeline/prioritize")
@require_key
@require_roles("DISPATCHER", "ADMIN")
def prioritize():
    return jsonify(M.generate_inspection_work_orders(
        principal=principal()))


@api.post("/pipeline/renewals")
@require_key
@require_roles("DISPATCHER", "ADMIN")
def renewals():
    return jsonify(M.generate_renewal_work_orders(principal=principal()))


@api.get("/work-orders")
@require_key
@require_roles("DISPATCHER", "ADMIN")
def work_orders():
    return jsonify(items=M.list_work_orders(
        status=request.args.get("status"),
        wo_type=request.args.get("type"),
        principal=principal(),
        limit=int(request.args.get("limit", 200)),
        offset=int(request.args.get("offset", 0))))


@api.get("/my-work-orders")
@require_key
def my_work_orders():
    p = principal(required=True)
    if not p:
        return jsonify(error="valid X-User-Token required"), 401
    if p["role"] not in ("INSPECTOR", "REHAB"):
        return jsonify(error="technician role required"), 403
    tok = user_token()
    mw = M.my_work_orders(tok)
    if mw is None:
        return jsonify(error="valid X-User-Token (technician) "
                       "required"), 401
    return jsonify(mw)


@api.get("/my-work-orders.csv")
@require_key
def my_work_orders_csv():
    p = principal(required=True)
    if not p:
        return jsonify(error="valid X-User-Token required"), 401
    if p["role"] not in ("INSPECTOR", "REHAB"):
        return jsonify(error="technician role required"), 403
    tok = user_token()
    rows = M.technician_worksheet_rows(tok)
    if rows is None:
        return jsonify(error="valid X-User-Token (technician) "
                       "required"), 401
    return _csv_response(["bucket", "work_order_id", "type", "status",
                          "priority_or_rehab_year", "line_id", "pwsid",
                          "state", "location"], rows)


@api.post("/work-orders/<int:wid>/claim")
@require_key
def claim(wid):
    tok = user_token()
    ok, msg = M.claim_work_order(wid, tok)
    return (jsonify(ok=True, technician=msg) if ok
            else (jsonify(ok=False, error=msg), 409))


@api.post("/work-orders/<int:wid>/result")
@require_key
def result(wid):
    ok, info = M.submit_result(wid, request.get_json(silent=True) or {},
                               user_token())
    return (jsonify(ok=True, overall_classification=info) if ok
            else (jsonify(ok=False, errors=info), 400))


@api.post("/rehab/plan")
@require_key
@require_roles("DISPATCHER", "ADMIN")
def rehab_plan():
    b = request.get_json(silent=True) or {}
    try:
        budget = float(b.get("annual_budget"))
    except (TypeError, ValueError):
        return jsonify(error="annual_budget (number) required"), 400
    return jsonify(M.build_rehab_plan(
        budget, horizon=int(b.get("horizon", 10)),
        default_cost=float(b.get("default_cost", 4700)),
        principal=principal()))


def _csv_response(header, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow(r if isinstance(r, (list, tuple))
                   else [r[h] for h in header])
    return Response(buf.getvalue(), mimetype="text/csv")


@api.get("/exports/<name>.csv")
@require_key
@require_roles("DISPATCHER", "ADMIN")
def export(name):
    p = principal()
    if name == "register":
        rows = M.register_rows(p)
        if not rows:
            return _csv_response(["(no investigations yet)"], [])
        hdr = list(rows[0].keys())
        return _csv_response(hdr, [[r[h] for h in hdr] for r in rows])
    if name == "sdwis_rollup":
        return _csv_response(
            ["PWS ID", "# Lead Service Lines",
             "# Galvanized Requiring Replacement Service Lines",
             "# Lead Status Unknown Service Lines",
             "# Non-lead Service Lines",
             "Total # Service Lines Reported"],
            M.sdwis_rollup_rows(p))
    if name == "model_feedback":
        return _csv_response(
            ["pwsid", "line_id", "model_predicted_p",
             "model_predicted_label", "confirmed_status",
             "basis_of_classification", "investigation_date"],
            M.feedback_rows(p))
    if name == "rehab_plan":
        return _csv_response(
            ["replacement_year", "pwsid", "state", "line_id",
             "classification", "cost_assumption_usd", "exposure_weight",
             "disadvantaged"], M.rehab_rows(principal=p))
    return jsonify(error="unknown export"), 404
