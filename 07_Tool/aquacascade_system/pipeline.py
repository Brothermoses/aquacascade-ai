"""Analysis pipeline orchestrator + CLI.

Flow: ingest service lines -> join triage priority -> generate
prioritised INSPECTION work orders -> (field results recorded) ->
build rehabilitation plan -> REHAB work orders.

CLI:
  python -m aquacascade_system.pipeline ingest <csv>
  python -m aquacascade_system.pipeline prioritize
  python -m aquacascade_system.pipeline rehab <annual_budget> [horizon]
"""
import sys
import csv
from . import models as M
from . import schema_def as SD
from .db import init_db


def _alias(cols):
    low = {c.lower().strip(): c for c in cols}
    pick = {}
    for k, al in SD.IMPORT_ALIASES.items():
        for a in al:
            if a in low:
                pick[k] = low[a]
                break
    return pick


def ingest_csv(path, principal=None):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rd = csv.DictReader(f)
        m = _alias(rd.fieldnames or [])
        if "service_line_id" not in m:
            raise ValueError("CSV needs a service line id column "
                             "(service_line_id/line_id/asset_id)")
        rows = []
        for r in rd:
            rows.append({
                "external_line_id": r.get(m["service_line_id"], ""),
                "pwsid": r.get(m.get("pwsid", ""), "") if "pwsid" in m
                else "",
                "location": r.get(m.get("location", ""), "")
                if "location" in m else "",
                "latitude": r.get(m.get("latitude", ""), "")
                if "latitude" in m else "",
                "longitude": r.get(m.get("longitude", ""), "")
                if "longitude" in m else "",
                "geometry": r.get(m.get("geometry", ""), "")
                if "geometry" in m else "",
                "install_year": r.get(m.get("install_year", ""), "")
                if "install_year" in m else "",
                "current_status": r.get(m.get("current_status", ""), "")
                if "current_status" in m else "",
                "expected_service_life_years": r.get(
                    m.get("expected_service_life_years", ""), "")
                if "expected_service_life_years" in m else "",
                "replacement_year": r.get(m.get("replacement_year", ""), "")
                if "replacement_year" in m else "",
                "diameter_in": r.get(m.get("diameter_in", ""), "")
                if "diameter_in" in m else "",
                "length_ft": r.get(m.get("length_ft", ""), "")
                if "length_ft" in m else "",
                "ownership_side": r.get(m.get("ownership_side", ""), "")
                if "ownership_side" in m else "",
                "verification_method": r.get(
                    m.get("verification_method", ""), "")
                if "verification_method" in m else "",
                "evidence_source": r.get(m.get("evidence_source", ""), "")
                if "evidence_source" in m else "",
                "confidence_score": r.get(
                    m.get("confidence_score", ""), "")
                if "confidence_score" in m else ""})
    return M.import_service_lines(rows, principal)


def run_prioritization():
    return M.generate_inspection_work_orders()


def build_rehab(budget, horizon=10):
    return M.build_rehab_plan(float(budget), int(horizon))


def main(argv):
    init_db()
    if not argv:
        print(__doc__)
        return
    cmd = argv[0]
    if cmd == "ingest" and len(argv) > 1:
        print(ingest_csv(argv[1]))
    elif cmd == "prioritize":
        print(run_prioritization())
    elif cmd == "rehab" and len(argv) > 1:
        h = int(argv[2]) if len(argv) > 2 else 10
        r = build_rehab(argv[1], h)
        print(r.get("summary", r))
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
