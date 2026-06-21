"""Server-rendered UI. Role-separated: Inspectors and Replacement crews
get distinct workspaces; Dispatchers/Admin get the national dashboard.
Users log in (token) from any device; the session carries identity."""
import io
import csv
import tempfile
import secrets
from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, Response, session, jsonify)
import pandas as pd
from . import models as M
from . import pipeline as P
from . import schema_def as SD
from .config import (DEFAULT_REPLACE_COST, DEMO_MODE,
                     ALLOW_QUERY_TOKENS, MAP_TILE_URL,
                     MAP_TILE_ATTRIBUTION)

web = Blueprint("web", __name__)

DEMO_TOKENS = {"Admin": "admin-national-001",
               "Dispatcher": "disp-demo-001",
               "Inspector": "insp-demo-001",
               "Replacement crew": "rehab-demo-001"}


@web.app_context_processor
def _inject():
    return {"get_principal": M.get_principal,
            "csrf_token": csrf_token}


def csrf_token():
    tok = session.get("_csrf_token")
    if not tok:
        tok = secrets.token_urlsafe(32)
        session["_csrf_token"] = tok
    return tok


@web.before_request
def _csrf_protect():
    if request.method != "POST":
        return None
    sent = request.form.get("_csrf")
    if not sent or not secrets.compare_digest(sent,
                                              session.get("_csrf_token", "")):
        flash("Security check failed. Please try again.")
        return redirect(request.referrer or url_for("web.login"))
    return None


def current_token():
    tok = session.get("token")
    if tok:
        return tok
    if ALLOW_QUERY_TOKENS:
        return request.args.get("token")
    return None


def me():
    t = current_token()
    return M.get_principal(t) if t else None


def _home_for(role):
    return {"INSPECTOR": "web.inspections",
            "REHAB": "web.replacements"}.get(role, "web.dashboard")


def _state_arg():
    raw = (request.args.get("state") or "").strip().upper()
    st = "".join(ch for ch in raw if ch.isalnum())
    return st[:3] if 1 <= len(st) <= 3 else ""


# ---------- auth ----------
@web.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        tok = request.form.get("token", "").strip()
        p = M.get_principal(tok)
        if not p:
            flash("Unknown or inactive token.")
            return redirect(url_for("web.login"))
        session["token"] = tok
        flash(f"Signed in as {p['un']} ({p['role']}).")
        return redirect(url_for(_home_for(p["role"])))
    return render_template("login.html", demo=DEMO_TOKENS if DEMO_MODE
                           else {})


@web.get("/logout")
def logout():
    session.pop("token", None)
    flash("Signed out.")
    return redirect(url_for("web.login"))


def _require(roles=None):
    p = me()
    if not p:
        return None, redirect(url_for("web.login"))
    if roles and p["role"] not in roles:
        flash(f"{p['role']} cannot access that workspace.")
        return p, redirect(url_for(_home_for(p["role"])))
    return p, None


# ---------- dispatcher / admin ----------
@web.get("/")
def dashboard():
    p, redir = _require(("DISPATCHER", "ADMIN"))
    if redir:
        return redir
    return render_template("dashboard.html", s=M.stats(p),
                           inventory=M.inventory_overview(p),
                           lifecycle=M.asset_lifecycle_summary(p),
                           states=M.rollup_by_state(p)[:15], me=p)


@web.post("/run-pipeline")
def run_pipeline():
    p, redir = _require(("DISPATCHER", "ADMIN"))
    if redir:
        return redir
    r = M.generate_inspection_work_orders(principal=p)
    flash(f"{r['inspection_work_orders_created']} inspection work "
          f"orders created.")
    return redirect(url_for("web.dashboard"))


@web.post("/run-renewals")
def run_renewals():
    p, redir = _require(("DISPATCHER", "ADMIN"))
    if redir:
        return redir
    r = M.generate_renewal_work_orders(principal=p)
    flash(f"{r['renewal_work_orders_created']} post-lifespan RENEWAL "
          f"work orders created (service life "
          f"{r['service_life_years']} yrs, an explicit assumption).")
    return redirect(url_for("web.dashboard"))


@web.post("/onboard-national")
def onboard_national():
    p, redir = _require(("ADMIN",))
    if redir:
        return redir
    if p["kind"] != "NATIONAL":
        flash("Only a national admin can onboard the national universe.")
        return redirect(url_for("web.dashboard"))
    r = M.onboard_national_universe()
    flash(f"Onboarded {r['systems_onboarded']} systems "
          f"(national universe {r['national_universe']}).")
    return redirect(url_for("web.dashboard"))


@web.route("/import", methods=["GET", "POST"])
def imp():
    p, redir = _require(("DISPATCHER", "ADMIN"))
    if redir:
        return redir
    if request.method == "POST":
        f = request.files.get("file")
        if not f or not f.filename:
            flash("Choose a CSV or XLSX file.")
            return redirect(url_for("web.imp"))
        try:
            if f.filename.lower().endswith((".xlsx", ".xls")):
                df = pd.read_excel(f).astype(str)
                cols = {c.lower().strip(): c for c in df.columns}
                pick = {}
                for k, al in SD.IMPORT_ALIASES.items():
                    for a in al:
                        if a in cols:
                            pick[k] = cols[a]
                            break
                rows = [{"external_line_id": r.get(
                    pick.get("service_line_id"), ""),
                    "pwsid": r.get(pick.get("pwsid"), "")
                    if "pwsid" in pick else "",
                    "location": r.get(pick.get("location"), "")
                    if "location" in pick else "",
                    "latitude": r.get(pick.get("latitude"), "")
                    if "latitude" in pick else "",
                    "longitude": r.get(pick.get("longitude"), "")
                    if "longitude" in pick else "",
                    "geometry": r.get(pick.get("geometry"), "")
                    if "geometry" in pick else "",
                    "install_year": r.get(pick.get("install_year"), "")
                    if "install_year" in pick else "",
                    "expected_service_life_years": r.get(
                        pick.get("expected_service_life_years"), "")
                    if "expected_service_life_years" in pick else "",
                    "replacement_year": r.get(pick.get("replacement_year"), "")
                    if "replacement_year" in pick else "",
                    "diameter_in": r.get(pick.get("diameter_in"), "")
                    if "diameter_in" in pick else "",
                    "length_ft": r.get(pick.get("length_ft"), "")
                    if "length_ft" in pick else "",
                    "ownership_side": r.get(pick.get("ownership_side"), "")
                    if "ownership_side" in pick else "",
                    "verification_method": r.get(
                        pick.get("verification_method"), "")
                    if "verification_method" in pick else "",
                    "evidence_source": r.get(pick.get("evidence_source"), "")
                    if "evidence_source" in pick else "",
                    "confidence_score": r.get(pick.get("confidence_score"), "")
                    if "confidence_score" in pick else "",
                    "current_status": r.get(pick.get("current_status"),
                                            "") if "current_status" in
                    pick else ""} for _, r in df.iterrows()]
                res = M.import_service_lines(rows, p)
            else:
                with tempfile.NamedTemporaryFile(
                        delete=False, suffix=".csv") as o:
                    o.write(f.read())
                    path = o.name
                res = P.ingest_csv(path, p)
        except Exception as e:
            flash(f"Import failed: {e}")
            return redirect(url_for("web.imp"))
        flash(f"Imported {res['lines_imported']} lines, "
              f"{res['systems_added']} systems.")
        return redirect(url_for("web.dashboard"))
    return render_template("import.html", lifecycle=M.asset_lifecycle_summary(p))


@web.get("/map")
def line_map():
    p, redir = _require(("DISPATCHER", "ADMIN"))
    if redir:
        return redir
    state_focus = _state_arg()
    data_url = url_for("web.line_map_data", state=state_focus) \
        if state_focus else url_for("web.line_map_data")
    return render_template("map.html", me=p, tile_url=MAP_TILE_URL,
                           tile_attribution=MAP_TILE_ATTRIBUTION,
                           state_focus=state_focus, data_url=data_url)


@web.get("/map/data")
def line_map_data():
    p, redir = _require(("DISPATCHER", "ADMIN"))
    if redir:
        return jsonify(error="login required"), 401
    return jsonify(M.map_data(p, state_filter=_state_arg()))


@web.get("/register")
def register_page():
    p, redir = _require(("DISPATCHER", "ADMIN"))
    if redir:
        return redir
    rows = M.register_rows(p)
    return render_template("register.html", rows=rows[:80],
                           summary=M.register_summary(p), total=len(rows),
                           lifecycle=M.asset_lifecycle_summary(p))


@web.get("/sdwis")
def sdwis_page():
    p, redir = _require(("DISPATCHER", "ADMIN"))
    if redir:
        return redir
    return render_template("sdwis.html", inventory=M.inventory_overview(p),
                           lifecycle=M.asset_lifecycle_summary(p))


# ---------- role-separated technician workspaces ----------
def _queue(kind_label, accent, roles):
    p, redir = _require(roles)
    if redir:
        return redir
    tok = current_token()
    mw = M.my_work_orders(tok)
    return render_template("workspace.html", mw=mw, token=tok,
                           kind=kind_label, accent=accent)


@web.get("/tech")
def tech_queue():
    p = me()
    if not p:
        return redirect(url_for("web.login"))
    return redirect(url_for(_home_for(p["role"])))


@web.get("/inspections")
def inspections():
    return _queue("Inspection", "insp", ("INSPECTOR",))


@web.get("/replacements")
def replacements():
    return _queue("Replacement & renewal", "rehab", ("REHAB",))


@web.get("/tech/worksheet.csv")
def tech_worksheet():
    p, redir = _require(("INSPECTOR", "REHAB"))
    if redir:
        return redir
    tok = current_token()
    rows = M.technician_worksheet_rows(tok)
    if rows is None:
        flash("Unknown technician token.")
        return redirect(url_for("web.login"))
    return _csv(["bucket", "work_order_id", "type", "status",
                 "priority_or_year", "line_id", "pwsid", "state",
                 "location"], rows)


@web.route("/wo/<int:wid>", methods=["GET", "POST"])
def work_order(wid):
    p, redir = _require(("INSPECTOR", "REHAB"))
    if redir:
        return redir
    wo = M.get_work_order(wid, p)
    if not wo:
        flash("Work order not found.")
        return redirect(url_for("web.tech_queue"))
    if wo["type"] not in M.allowed_types(p):
        flash(f"{p['role']} cannot work {wo['type']} orders.")
        return redirect(url_for(_home_for(p["role"])))
    back = url_for("web.replacements" if wo["type"] in ("REHAB",
                   "RENEWAL") else "web.inspections")
    if request.method == "POST":
        if request.form.get("_action") == "claim":
            ok, msg = M.claim_work_order(wid, current_token())
            flash(("Claimed by " + msg) if ok else ("Claim failed: "
                                                    + msg))
            return redirect(url_for("web.work_order", wid=wid))
        if wo["type"] in ("REHAB", "RENEWAL"):
            ok, msg = M.complete_rehab(wid, request.form, current_token())
            flash((msg) if ok else ("Failed: " + msg))
            return redirect(back)
        ok, info = M.submit_result(wid, request.form, current_token())
        if ok:
            flash(f"Recorded line -> {info}.")
            return redirect(back)
        for e in info:
            flash(e)
        return redirect(url_for("web.work_order", wid=wid))
    return render_template("work_order.html", wo=wo, S=SD,
                           default_cost=int(DEFAULT_REPLACE_COST))


# ---------- exports ----------
def _csv(header, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow(r if isinstance(r, (list, tuple))
                   else [r[h] for h in header])
    return Response(buf.getvalue(), mimetype="text/csv")


@web.get("/export/<name>.csv")
def export(name):
    p, redir = _require(("DISPATCHER", "ADMIN"))
    if redir:
        return redir
    if name == "register":
        rs = M.register_rows(p)
        if not rs:
            return _csv(["(no investigations yet)"], [])
        h = list(rs[0].keys())
        return _csv(h, [[r[k] for k in h] for r in rs])
    if name == "sdwis_rollup":
        return _csv(["PWS ID", "# Lead Service Lines",
                     "# Galvanized Requiring Replacement Service Lines",
                     "# Lead Status Unknown Service Lines",
                     "# Non-lead Service Lines",
                     "Total # Service Lines Reported"],
                    M.sdwis_rollup_rows(p))
    if name == "system_inventory":
        rows = M.system_inventory_export_rows(p)
        header = ["pwsid", "pws_name", "state", "source_quarter",
                  "lead_count", "grr_count", "unknown_count",
                  "nonlead_count", "total_count", "report_status",
                  "pws_type", "activity_status", "population_served"]
        return _csv(header, [[r[h] for h in header] for r in rows])
    if name == "model_feedback":
        return _csv(["pwsid", "line_id", "model_predicted_p",
                     "model_predicted_label", "confirmed_status",
                     "basis_of_classification", "investigation_date"],
                    M.feedback_rows(p))
    if name == "rehab_plan":
        return _csv(["replacement_year", "pwsid", "state", "line_id",
                     "classification", "cost_assumption_usd",
                     "exposure_weight", "disadvantaged"],
                    M.rehab_rows(principal=p))
    if name == "asset_lifecycle":
        rows = M.asset_lifecycle_export_rows(p)
        header = ["service_line_id", "pwsid", "state", "location",
                  "current_status", "install_year",
                  "expected_service_life_years", "service_life_basis",
                  "asset_age_years", "remaining_life_years",
                  "renewal_due_year", "lifecycle_flag",
                  "replacement_year", "diameter_in", "length_ft",
                  "ownership_side", "verification_method",
                  "evidence_source", "confidence_score",
                  "geometry_available", "readiness_gaps"]
        return _csv(header, [[r[h] for h in header] for r in rows])
    flash("Unknown export.")
    return redirect(url_for("web.dashboard"))


@web.route("/rehab", methods=["GET", "POST"])
def rehab():
    p, redir = _require(("DISPATCHER", "ADMIN"))
    if redir:
        return redir
    plan = None
    form = {}
    if request.method == "POST":
        form = request.form
        try:
            plan = M.build_rehab_plan(
                float(form.get("annual_budget", "0")),
                horizon=int(form.get("horizon", "10")),
                default_cost=float(form.get("default_cost", "4700")),
                principal=p)
        except ValueError:
            flash("Budget, horizon and cost must be numbers.")
    return render_template("rehab.html", plan=plan, form=form,
                           lifecycle=M.asset_lifecycle_summary(p))


@web.get("/guide")
def guide():
    return render_template("guide.html")
