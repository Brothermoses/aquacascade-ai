"""EPA LCRR/LCRI line-level schema, controlled vocabularies, and the
worst-case overall-classification logic. Single source of truth for the
investigation data contract (carried over from Report C)."""

MATERIALS = [
    "Lead",
    "Galvanized Requiring Replacement",
    "Non-lead",
    "Lead Status Unknown",
]
BASES = [
    "Predictive model (AquaCascade)",
    "Records / historical review",
    "Visual inspection",
    "Water-quality (lead) sampling",
    "Potholing / vacuum excavation",
    "Other",
]
INVESTIGATION_METHODS = [
    "Potholing / vacuum excavation",
    "Visual inspection at meter or point of entry",
    "Records / historical review",
    "Water-quality (lead) sampling",
    "CCTV / borescope",
    "Other",
]
SDWIS_CATEGORIES = [
    "Lead",
    "Galvanized Requiring Replacement",
    "Lead Status Unknown",
    "Non-lead",
]
REPLACE_CLASSES = ("Lead", "Galvanized Requiring Replacement")

IMPORT_ALIASES = {
    "service_line_id": ["service_line_id", "line_id", "id", "sl_id",
                        "asset_id"],
    "pwsid": ["pwsid", "pws_id", "pws id"],
    "location": ["location", "address", "service_address", "parcel",
                 "gis_id", "geometry_id"],
    "latitude": ["latitude", "lat", "y"],
    "longitude": ["longitude", "lon", "lng", "x"],
    "geometry": ["geometry", "geom", "wkt", "geojson", "shape"],
    "install_year": ["install_year", "year_installed", "install_yr",
                     "year"],
    "expected_service_life_years": [
        "expected_service_life_years", "service_life_years",
        "expected_life_years", "design_life_years", "asset_life_years",
        "lifespan_years", "lifespan"],
    "replacement_year": ["replacement_year", "renewal_year",
                         "last_replaced_year", "replaced_year"],
    "diameter_in": ["diameter_in", "diameter_inches", "diameter",
                    "pipe_diameter"],
    "length_ft": ["length_ft", "pipe_length_ft", "length_feet",
                  "line_length_ft", "length"],
    "ownership_side": ["ownership_side", "owner_side", "ownership",
                       "utility_or_customer_side"],
    "verification_method": ["verification_method", "verification",
                            "install_year_method", "record_method"],
    "evidence_source": ["evidence_source", "source_document",
                        "source_record", "record_source"],
    "confidence_score": ["confidence_score", "confidence", "confidence_pct",
                         "data_confidence"],
    "current_status": ["current_status", "status", "material",
                       "current_material"],
}


def overall_classification(system_side, customer_side):
    pair = {(system_side or "").strip(), (customer_side or "").strip()}
    if "Lead" in pair:
        return "Lead"
    if "Galvanized Requiring Replacement" in pair:
        return "Galvanized Requiring Replacement"
    if "Lead Status Unknown" in pair or "" in pair:
        return "Lead Status Unknown"
    return "Non-lead"


def validate_result(d):
    """Validate an investigation-result dict (API or form). Returns
    (clean, errors)."""
    e = []
    ss = (d.get("system_side_material") or "").strip()
    cs = (d.get("customer_side_material") or "").strip()
    if ss not in MATERIALS:
        e.append("system_side_material invalid")
    if cs not in MATERIALS:
        e.append("customer_side_material invalid")
    basis = (d.get("basis_of_classification") or "").strip()
    if basis not in BASES:
        e.append("basis_of_classification invalid")
    method = (d.get("investigation_method") or "").strip()
    if method not in INVESTIGATION_METHODS:
        e.append("investigation_method invalid")
    date = (d.get("investigation_date") or "").strip()
    if not date:
        e.append("investigation_date required")
    iy = str(d.get("install_year") or "").strip()
    if iy and not (iy.isdigit() and 1850 <= int(iy) <= 2100):
        e.append("install_year must be a 4-digit year")
    cost = str(d.get("investigation_cost") or "").strip()
    if cost:
        try:
            float(cost)
        except ValueError:
            e.append("investigation_cost must be numeric")
    if e:
        return None, e
    clean = {
        "system_side_material": ss, "customer_side_material": cs,
        "overall_classification": overall_classification(ss, cs),
        "basis_of_classification": basis, "investigation_method": method,
        "investigation_date": date, "install_year": iy,
        "inspector": (d.get("inspector") or "").strip(),
        "investigation_cost": cost,
        "disadvantaged_community_flag":
            "Y" if str(d.get("disadvantaged_community_flag") or "")
            .lower() in ("y", "yes", "true", "on", "1") else "N",
        "notes": (d.get("notes") or "").strip(),
        "photo_ref": (d.get("photo_ref") or "").strip(),
    }
    clean["confirmed_status"] = clean["overall_classification"]
    return clean, []
