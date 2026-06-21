"""Load real public SDWIS inventory data into the map layer.

This creates a system-level map: real reported material counts by PWS,
joined to public water-system service-area representative points. It does
not fabricate pipe-by-pipe street geometry.

Run from 07_Tool:
  python -m aquacascade_system.real_data load-inventory
"""
import json
import sys

import geopandas as gpd
import pandas as pd

from .config import ROOT
from .db import init_db
from . import models as M


INV = ROOT / "01_Raw_Data" / "Service_Line_Inventory" / \
    "SDWIS_service_line_inventory_2026Q1.csv"
BOUNDARIES = ROOT / "01_Raw_Data" / "Boundaries_Census_v3" / \
    "PWS_Boundaries_Latest.zip"

STATE_ABBR = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT",
    "Delaware": "DE", "District Of Columbia": "DC", "Florida": "FL",
    "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL",
    "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY",
    "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN",
    "Mississippi": "MS", "Missouri": "MO", "Montana": "MT",
    "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH",
    "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH",
    "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA",
    "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX",
    "Utah": "UT", "Vermont": "VT", "Virginia": "VA",
    "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI",
    "Wyoming": "WY", "American Samoa": "AS", "Guam": "GU",
    "Northern Mariana Islands": "MP", "Puerto Rico": "PR",
    "Virgin Islands": "VI",
}


def _state_from(pwsid, primacy):
    p = str(pwsid or "").strip().upper()
    if len(p) >= 2 and p[:2].isalpha():
        return p[:2]
    name = str(primacy or "").strip()
    return STATE_ABBR.get(name.title(), "")


def _boundary_uri():
    z = str(BOUNDARIES.resolve()).replace("\\", "/")
    return f"/vsizip/{z}/3_0/Service_Areas_V_3_0.gpkg"


def _load_centroids():
    uri = _boundary_uri()
    parts = []
    for layer in ("CWS", "T_NTNC"):
        gdf = gpd.read_file(uri, layer=layer,
                            columns=["PWSID", "PWS_Name", "geometry"])
        if gdf.empty:
            continue
        pts = gdf.geometry.representative_point()
        frame = pd.DataFrame({
            "pwsid": gdf["PWSID"].astype(str).str.upper(),
            "geo_name": gdf.get("PWS_Name", "").astype(str),
            "longitude": pts.x,
            "latitude": pts.y,
        })
        parts.append(frame)
    geo = pd.concat(parts, ignore_index=True)
    geo = geo.dropna(subset=["latitude", "longitude"])
    geo = geo.drop_duplicates("pwsid")
    return geo


def load_inventory():
    inv = pd.read_csv(INV, encoding="latin1", dtype={"PWS ID": str},
                      low_memory=False)
    inv["pwsid"] = inv["PWS ID"].astype(str).str.upper().str.strip()
    count_cols = [
        "# Lead Service Lines",
        "# Galvanized Requiring Replacement Service Lines",
        "# Lead Status Unknown Service Lines",
        "# Non-lead Service Lines",
        "Total # Service Lines Reported",
    ]
    for c in count_cols + ["Population Served Count"]:
        inv[c] = pd.to_numeric(inv[c].astype(str).str.replace(",", "",
                              regex=False), errors="coerce").fillna(0)
    inv = inv.groupby("pwsid", as_index=False).agg({
        "Submission Year Quarter": "first",
        "PWS Name": "first",
        "# Lead Service Lines": "sum",
        "# Galvanized Requiring Replacement Service Lines": "sum",
        "# Lead Status Unknown Service Lines": "sum",
        "# Non-lead Service Lines": "sum",
        "Total # Service Lines Reported": "sum",
        "Service Line Report Status": "first",
        "PWS Type": "first",
        "Activity Status": "first",
        "Primacy Agency": "first",
        "EPA Region": "first",
        "Population Served Count": "sum",
    })
    geo = _load_centroids()
    df = inv.merge(geo, on="pwsid", how="left")
    rows = []
    for _, r in df.iterrows():
        rows.append({
            "pwsid": r["pwsid"],
            "pws_name": r.get("PWS Name") or r.get("geo_name") or "",
            "state": _state_from(r["pwsid"], r.get("Primacy Agency")),
            "source_quarter": r.get("Submission Year Quarter", ""),
            "lead_count": r.get("# Lead Service Lines", 0),
            "grr_count": r.get("# Galvanized Requiring Replacement Service Lines", 0),
            "unknown_count": r.get("# Lead Status Unknown Service Lines", 0),
            "nonlead_count": r.get("# Non-lead Service Lines", 0),
            "total_count": r.get("Total # Service Lines Reported", 0),
            "report_status": r.get("Service Line Report Status", ""),
            "pws_type": r.get("PWS Type", ""),
            "activity_status": r.get("Activity Status", ""),
            "primacy_agency": r.get("Primacy Agency", ""),
            "epa_region": r.get("EPA Region", ""),
            "population_served": r.get("Population Served Count", 0),
            "latitude": r.get("latitude"),
            "longitude": r.get("longitude"),
        })
    return M.replace_system_inventory(rows, source=str(INV.relative_to(ROOT)))


def main(argv=None):
    argv = list(argv or sys.argv[1:])
    if not argv or argv[0] != "load-inventory":
        print(__doc__)
        return 2
    init_db()
    result = load_inventory()
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
