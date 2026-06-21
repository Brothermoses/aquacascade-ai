"""Canonical line-level service-line investigation schema + EPA LCRR logic.

Single source of truth for the tool's data contract (Report C, Section 3).
v1 scope: capture + compliant export + model feedback. 4 SDWIS categories
with the LCRR-required system-owned / customer-owned split.
"""

# EPA LCRR/LCRI material classes (per portion)
MATERIALS = [
    "Lead",
    "Galvanized Requiring Replacement",
    "Non-lead",
    "Lead Status Unknown",
]

# EPA-recognized bases of classification (predictive model = our pre-
# investigation basis; field work upgrades it to a verified basis)
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

# SDWIS rollup categories (must match the public inventory submission)
SDWIS_CATEGORIES = [
    "Lead",
    "Galvanized Requiring Replacement",
    "Lead Status Unknown",
    "Non-lead",
]

# imported utility line-list columns we look for (case-insensitive,
# flexible aliases). Only service_line_id is strictly required.
IMPORT_ALIASES = {
    "service_line_id": ["service_line_id", "line_id", "id", "sl_id",
                        "asset_id"],
    "pwsid": ["pwsid", "pws_id", "pws id"],
    "location": ["location", "address", "service_address", "parcel",
                 "gis_id", "geometry_id"],
    "install_year": ["install_year", "year_installed", "install_yr",
                     "year"],
    "current_status": ["current_status", "status", "material",
                       "current_material"],
}

# the persisted investigation record (besides imported line fields)
INVESTIGATION_FIELDS = [
    "system_side_material",
    "customer_side_material",
    "overall_classification",      # derived
    "basis_of_classification",
    "install_year",
    "investigation_method",
    "investigation_date",
    "inspector",
    "investigation_cost",
    "predicted_status",            # from model, if available
    "confirmed_status",            # = overall_classification
    "disadvantaged_community_flag",
    "notes",
    "photo_ref",
]


def overall_classification(system_side, customer_side):
    """EPA worst-case logic: a service line is Lead if ANY portion is
    lead; else GRR if any galvanized-requiring-replacement; else Unknown
    if any portion unknown; else Non-lead."""
    pair = {(system_side or "").strip(), (customer_side or "").strip()}
    if "Lead" in pair:
        return "Lead"
    if "Galvanized Requiring Replacement" in pair:
        return "Galvanized Requiring Replacement"
    if "Lead Status Unknown" in pair or "" in pair:
        return "Lead Status Unknown"
    return "Non-lead"


def validate(form):
    """Return (clean_dict, errors[])."""
    errors = []
    d = {}
    ss = form.get("system_side_material", "").strip()
    cs = form.get("customer_side_material", "").strip()
    if ss not in MATERIALS:
        errors.append("system_side_material is required")
    if cs not in MATERIALS:
        errors.append("customer_side_material is required")
    basis = form.get("basis_of_classification", "").strip()
    if basis not in BASES:
        errors.append("basis_of_classification is required")
    method = form.get("investigation_method", "").strip()
    if method not in INVESTIGATION_METHODS:
        errors.append("investigation_method is required")
    date = form.get("investigation_date", "").strip()
    if not date:
        errors.append("investigation_date is required")
    iy = form.get("install_year", "").strip()
    if iy:
        if not (iy.isdigit() and 1850 <= int(iy) <= 2100):
            errors.append("install_year must be a 4-digit year")
    cost = form.get("investigation_cost", "").strip()
    if cost:
        try:
            float(cost)
        except ValueError:
            errors.append("investigation_cost must be a number")
    if errors:
        return None, errors
    d["system_side_material"] = ss
    d["customer_side_material"] = cs
    d["overall_classification"] = overall_classification(ss, cs)
    d["confirmed_status"] = d["overall_classification"]
    d["basis_of_classification"] = basis
    d["investigation_method"] = method
    d["investigation_date"] = date
    d["install_year"] = iy
    d["inspector"] = form.get("inspector", "").strip()
    d["investigation_cost"] = cost
    d["disadvantaged_community_flag"] = (
        "Y" if form.get("disadvantaged_community_flag") else "N")
    d["notes"] = form.get("notes", "").strip()
    d["photo_ref"] = form.get("photo_ref", "").strip()
    return d, []
