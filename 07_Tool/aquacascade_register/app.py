"""
AquaCascade — Service-Line Investigation Register (local web app, v1)

Offline-capable. Closes the loop: the model prioritizes which systems to
investigate; field/office staff record each line here in an EPA-LCRR-
compliant schema; the tool exports the SDWIS rollup for the state AND a
labeled-feedback file that retrains the model.

Run:  python 07_Tool/aquacascade_register/app.py
Open: http://127.0.0.1:5000   (Ctrl+C to stop)

No cloud, no PII beyond what the utility imports. SQLite stored locally
in ./data/register.db.
"""
import io
import sqlite3
import csv
from pathlib import Path
from flask import (Flask, request, redirect, url_for, render_template,
                   Response, flash)
import pandas as pd

import schema as S
import rehab as R

LAST_PLAN = {}                    # in-memory cache for the CSV export

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parents[1]                         # project root
DB = APP_DIR / "data" / "register.db"
RANKING = ROOT / "05_Modeling" / "unknown_triage_ranking.csv"

app = Flask(__name__)
app.secret_key = "aquacascade-local"


# ---------- model priority (system-level context) ----------------------
def load_ranking():
    if not RANKING.exists():
        return {}
    df = pd.read_csv(RANKING, dtype=str)
    out = {}
    for _, r in df.iterrows():
        try:
            out[r["pwsid"]] = (int(float(r["rank"])),
                               float(r["p_lead_rich"]))
        except Exception:
            pass
    return out


RANK = load_ranking()


def predicted(pwsid):
    """System-level model signal (honest: prediction is per-SYSTEM, not
    per-line)."""
    if pwsid in RANK:
        rk, p = RANK[pwsid]
        return rk, p, ("High" if p >= 0.5 else "Low")
    return None, None, "N/A"


# ---------- database ---------------------------------------------------
def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    DB.parent.mkdir(exist_ok=True)
    con = db()
    con.execute("""CREATE TABLE IF NOT EXISTS lines(
        id INTEGER PRIMARY KEY, service_line_id TEXT, pwsid TEXT,
        location TEXT, install_year TEXT, current_status TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS investigations(
        line_id INTEGER PRIMARY KEY, system_side_material TEXT,
        customer_side_material TEXT, overall_classification TEXT,
        basis_of_classification TEXT, install_year TEXT,
        investigation_method TEXT, investigation_date TEXT,
        inspector TEXT, investigation_cost TEXT, predicted_p TEXT,
        predicted_label TEXT, confirmed_status TEXT,
        disadvantaged_community_flag TEXT, notes TEXT, photo_ref TEXT)""")
    con.commit()
    con.close()


# ---------- routes -----------------------------------------------------
@app.route("/")
def index():
    con = db()
    nl = con.execute("SELECT COUNT(*) c FROM lines").fetchone()["c"]
    ni = con.execute("SELECT COUNT(*) c FROM investigations").fetchone()["c"]
    con.close()
    return render_template("index.html", nl=nl, ni=ni,
                           model_loaded=len(RANK), ranking_path=str(RANKING))


@app.route("/import", methods=["GET", "POST"])
def imp():
    if request.method == "POST":
        f = request.files.get("file")
        if not f or not f.filename:
            flash("Choose a CSV or XLSX file.")
            return redirect(url_for("imp"))
        try:
            if f.filename.lower().endswith((".xlsx", ".xls")):
                df = pd.read_excel(f)
            else:
                df = pd.read_csv(f, dtype=str)
        except Exception as e:
            flash(f"Could not read file: {e}")
            return redirect(url_for("imp"))
        cols = {c.lower().strip(): c for c in df.columns}

        def pick(key):
            for a in S.IMPORT_ALIASES[key]:
                if a in cols:
                    return cols[a]
            return None
        slc = pick("service_line_id")
        if slc is None:
            flash("File must contain a service line ID column "
                  "(e.g. service_line_id / line_id / asset_id).")
            return redirect(url_for("imp"))
        m = {k: pick(k) for k in S.IMPORT_ALIASES}
        con = db()
        n = 0
        for _, r in df.iterrows():
            con.execute(
                "INSERT INTO lines(service_line_id,pwsid,location,"
                "install_year,current_status) VALUES(?,?,?,?,?)",
                (str(r[m["service_line_id"]]),
                 str(r[m["pwsid"]]) if m["pwsid"] else "",
                 str(r[m["location"]]) if m["location"] else "",
                 str(r[m["install_year"]]) if m["install_year"] else "",
                 str(r[m["current_status"]]) if m["current_status"]
                 else ""))
            n += 1
        con.commit()
        con.close()
        flash(f"Imported {n} service lines.")
        return redirect(url_for("worklist"))
    return render_template("import.html")


@app.route("/clear")
def clear():
    con = db()
    con.execute("DELETE FROM investigations")
    con.execute("DELETE FROM lines")
    con.commit()
    con.close()
    flash("Cleared all imported lines and investigations.")
    return redirect(url_for("index"))


@app.route("/worklist")
def worklist():
    con = db()
    rows = con.execute("""SELECT l.*, i.overall_classification oc
        FROM lines l LEFT JOIN investigations i ON i.line_id=l.id""").fetchall()
    con.close()
    items = []
    for r in rows:
        rk, p, lab = predicted(r["pwsid"])
        items.append({
            "id": r["id"], "sid": r["service_line_id"],
            "pwsid": r["pwsid"], "loc": r["location"],
            "rank": rk if rk is not None else 10 ** 9,
            "rank_disp": rk if rk is not None else "—",
            "p": f"{p:.2f}" if p is not None else "—",
            "plabel": lab,
            "status": r["oc"] or "Pending",
            "done": r["oc"] is not None})
    # v1 ordering: model system-priority, pending first
    items.sort(key=lambda x: (x["done"], x["rank"]))
    return render_template("worklist.html", items=items)


@app.route("/investigate/<int:lid>", methods=["GET", "POST"])
def investigate(lid):
    con = db()
    line = con.execute("SELECT * FROM lines WHERE id=?", (lid,)).fetchone()
    if not line:
        con.close()
        flash("Line not found.")
        return redirect(url_for("worklist"))
    rk, p, lab = predicted(line["pwsid"])
    if request.method == "POST":
        clean, errs = S.validate(request.form)
        if errs:
            for e in errs:
                flash(e)
            con.close()
            return redirect(url_for("investigate", lid=lid))
        con.execute("""INSERT OR REPLACE INTO investigations(line_id,
            system_side_material,customer_side_material,
            overall_classification,basis_of_classification,install_year,
            investigation_method,investigation_date,inspector,
            investigation_cost,predicted_p,predicted_label,
            confirmed_status,disadvantaged_community_flag,notes,photo_ref)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            lid, clean["system_side_material"],
            clean["customer_side_material"],
            clean["overall_classification"],
            clean["basis_of_classification"], clean["install_year"],
            clean["investigation_method"], clean["investigation_date"],
            clean["inspector"], clean["investigation_cost"],
            f"{p:.4f}" if p is not None else "", lab,
            clean["confirmed_status"],
            clean["disadvantaged_community_flag"], clean["notes"],
            clean["photo_ref"]))
        con.commit()
        con.close()
        flash(f"Recorded line {line['service_line_id']} → "
              f"{clean['overall_classification']}.")
        return redirect(url_for("worklist"))
    prev = con.execute("SELECT * FROM investigations WHERE line_id=?",
                        (lid,)).fetchone()
    con.close()
    return render_template("investigate.html", line=line, rk=rk, p=p,
                           lab=lab, S=S, prev=prev,
                           default_iy=line["install_year"] or "")


def _confirmed_replacement_lines():
    con = db()
    rows = con.execute("""SELECT l.service_line_id sid, l.pwsid pw,
        COALESCE(NULLIF(i.install_year,''), l.install_year) iy,
        i.overall_classification oc, i.investigation_cost ic,
        i.disadvantaged_community_flag dc
        FROM lines l JOIN investigations i ON i.line_id=l.id
        WHERE i.overall_classification IN
        ('Lead','Galvanized Requiring Replacement')""").fetchall()
    con.close()
    return [{"line_id": r["sid"], "pwsid": r["pw"],
             "classification": r["oc"], "install_year": r["iy"],
             "disadvantaged": r["dc"] == "Y",
             "cost": r["ic"]} for r in rows]


@app.route("/rehab", methods=["GET", "POST"])
def rehab():
    global LAST_PLAN
    lines = _confirmed_replacement_lines()
    if request.method == "POST":
        try:
            budget = float(request.form.get("annual_budget", "0"))
            horizon = int(request.form.get("horizon", "10"))
            dcost = float(request.form.get("default_cost", "4700"))
        except ValueError:
            flash("Budget, horizon and cost must be numbers.")
            return redirect(url_for("rehab"))
        LAST_PLAN = R.plan(lines, annual_budget=budget, horizon=horizon,
                           default_cost=dcost)
        return render_template("rehab.html", n=len(lines),
                               plan=LAST_PLAN, form=request.form)
    return render_template("rehab.html", n=len(lines), plan=None,
                           form={})


@app.route("/export/rehab_plan.csv")
def export_rehab():
    if not LAST_PLAN or "schedule" not in LAST_PLAN:
        return Response("run a rehabilitation plan first",
                        mimetype="text/plain")
    hdr = ["replacement_year", "pwsid", "service_line_id",
           "classification", "install_year", "disadvantaged",
           "cost_assumption_usd", "exposure_weight"]
    out = [[l["year"], l["pwsid"], l["line_id"], l["classification"],
            l["install_year"], "Y" if l["disadvantaged"] else "N",
            l["cost"], round(l["weight"], 3)]
           for l in LAST_PLAN["schedule"]]
    return _csv(out, hdr)


def _csv(rows, header):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return Response(buf.getvalue(), mimetype="text/csv")


@app.route("/export/register.csv")
def export_register():
    con = db()
    rows = con.execute("""SELECT l.service_line_id,l.pwsid,l.location,
        i.* FROM lines l JOIN investigations i ON i.line_id=l.id""").fetchall()
    con.close()
    hdr = ["service_line_id", "pwsid", "location"] + [
        k for k in rows[0].keys() if k not in
        ("service_line_id", "pwsid", "location", "line_id")] \
        if rows else ["service_line_id", "pwsid", "location"]
    out = [[r[h] for h in hdr] for r in rows]
    return _csv(out, hdr)


@app.route("/export/sdwis_rollup.csv")
def export_rollup():
    con = db()
    rows = con.execute("""SELECT l.pwsid p, i.overall_classification oc
        FROM lines l JOIN investigations i ON i.line_id=l.id""").fetchall()
    con.close()
    agg = {}
    for r in rows:
        d = agg.setdefault(r["p"], {c: 0 for c in S.SDWIS_CATEGORIES})
        if r["oc"] in d:
            d[r["oc"]] += 1
    hdr = ["PWS ID", "# Lead Service Lines",
           "# Galvanized Requiring Replacement Service Lines",
           "# Lead Status Unknown Service Lines",
           "# Non-lead Service Lines", "Total # Service Lines Reported"]
    out = []
    for pid, d in sorted(agg.items()):
        tot = sum(d.values())
        out.append([pid, d["Lead"], d["Galvanized Requiring Replacement"],
                    d["Lead Status Unknown"], d["Non-lead"], tot])
    return _csv(out, hdr)


@app.route("/export/model_feedback.csv")
def export_feedback():
    con = db()
    rows = con.execute("""SELECT l.pwsid,l.service_line_id,
        i.predicted_p,i.predicted_label,i.confirmed_status,
        i.basis_of_classification,i.investigation_date
        FROM lines l JOIN investigations i ON i.line_id=l.id""").fetchall()
    con.close()
    hdr = ["pwsid", "service_line_id", "model_predicted_p",
           "model_predicted_label", "confirmed_status",
           "basis_of_classification", "investigation_date"]
    out = [[r[k] for k in ("pwsid", "service_line_id", "predicted_p",
            "predicted_label", "confirmed_status",
            "basis_of_classification", "investigation_date")]
           for r in rows]
    return _csv(out, hdr)


if __name__ == "__main__":
    init_db()
    print("AquaCascade Register -> http://127.0.0.1:5000  (Ctrl+C to stop)")
    app.run(debug=False, port=5000)
