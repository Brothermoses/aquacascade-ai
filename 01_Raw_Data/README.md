# Raw EPA data — where to download

The modeling consumes public EPA data; the data itself is not in this
repository (too large — ~17 GB on disk). Re-download from the official
sources below, then place under `01_Raw_Data/` following the folder
structure each script expects (the modeling scripts in `05_Modeling/`
reference these paths directly).

## SDWA (Safe Drinking Water Act) — EPA ECHO

Bulk download (one zip containing every CSV the project uses):
https://echo.epa.gov/tools/data-downloads#dwdownloads

Extract into `01_Raw_Data/SDWA_ECHO/`. The files referenced are:

- `SDWA_PUB_WATER_SYSTEMS.csv`
- `SDWA_VIOLATIONS_ENFORCEMENT.csv`  (~4 GB)
- `SDWA_SITE_VISITS.csv`              (~390 MB)
- `SDWA_LCR_SAMPLES.csv`              (~124 MB)
- `SDWA_FACILITIES.csv`
- `SDWA_GEOGRAPHIC_AREAS.csv`
- `SDWA_SERVICE_AREAS.csv`
- `SDWA_EVENTS_MILESTONES.csv`
- `SDWA_REF_CODE_VALUES.csv`
- `SDWA_REF_ANSI_AREAS.csv`

## SDWIS service-line inventory (quarterly)

EPA SDWIS Federal Reports — Service Line Inventory:
https://sdwis.epa.gov/ords/sfdw_pub/r/sfdw/sdwis_fed_reports_public/service-line-inventory

Download Q1 2025 → Q1 2026 and place under
`01_Raw_Data/Service_Line_Inventory/` as
`SDWIS_service_line_inventory_YYYYQn.csv`.

## SDWIS Water System Summary (quarterly)

EPA SDWIS Federal Reports (Apex portal):
https://sdwis.epa.gov/ords/sfdw_pub/r/sfdw/sdwis_fed_reports_public/1

Select report = "Water System Summary", choose Year + Quarter, View
Reports → Actions → Download. Place under
`01_Raw_Data/Water_System_Summary/` as
`Water System Summary YYYY Qn.csv` (the project uses 53 quarters,
2013 Q1 → 2026 Q1).

## EPA Service Area Boundary Layer (GIS)

EPA Office of Research and Development:
https://github.com/USEPA/ORD_SAB_Model

The `Buyers_Sellers_2023Q4.csv` file (wholesale water purchase
relationships used as a feature) IS included in this repo at
`01_Raw_Data/EPA_SAB_repo/` because it is small. The full boundary
GeoPackage (~570 MB) is not — download from the ORD_SAB_Model
release page.

## EPA Census-linked PWS boundaries (v3)

Download `PWS_Boundaries_Latest.zip` from the same ORD_SAB_Model
release and place under `01_Raw_Data/Boundaries_Census_v3/`.

---

The reproducibility manifest at
`05_Modeling/triage_production_manifest.json` records the SHA-256
and row count of every input file at the time the production model
was built — re-download the latest snapshot, and you can verify the
shape (numbers will drift slightly as EPA refreshes).
