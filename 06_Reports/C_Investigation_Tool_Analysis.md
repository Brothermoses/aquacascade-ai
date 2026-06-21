# Report C — Investigation-Recording Tool: Pre-Build Analysis

Purpose: decide what to build before building it. This tool lets a water system record and register each service line as crews investigate the unknowns, following the model's prioritized order. Rehabilitation is a later module (noted, not designed here).

---

## 1. The central design fact (granularity gap)

| Layer | What it is | Source |
|---|---|---|
| **Model output** | *System-level* priority: which water systems' unknown lines are most likely lead. `unknown_triage_ranking.csv` = pwsid, rank, p_lead_rich, exp_hidden_lead, inv_cost (14,113 systems). | Track-2 model |
| **EPA/SDWIS inventory** | *System-level* rollup counts in 4 categories: # Lead, # Galvanized Requiring Replacement (GRR), # Lead Status Unknown, # Non-lead. | `SDWIS_service_line_inventory_*` |
| **Investigation reality** | *Line-level*: each individual service line is dug/inspected and classified. | Utility's own data (NOT public) |

The model and the public data are system-level; investigation is per line. **The tool lives at the line level inside one utility**, takes the utility's own service-line list, uses the model's system-level priority as context/ordering, and rolls confirmed line records back up to the 4 SDWIS categories for state submission.

We confirmed earlier that line-level service-line location data is not public (homeland-security restricted). So the tool must **import the utility's own line list** — it cannot be fabricated or sourced by us.

## 2. What the tool must do (scope, honest)

1. **Import** a utility's service-line list (CSV/Excel): line ID, address/parcel/GIS ID, install year if known, current status.
2. **Order the worklist** by the model's priority for that system (+ simple within-utility heuristics the utility already has, e.g., install era) — v1 ordering is heuristic, a line-level model is a later module.
3. **Record each investigation** against an EPA-LCRR-compliant per-line schema (Section 3), with validation and controlled vocabularies.
4. **Export** (a) the utility's working register and (b) a state-submittable inventory file that rolls up to the 4 SDWIS categories.
5. **Close the loop:** every confirmed line is a new labeled datapoint (predicted vs. actual) appended to a feedback dataset that retrains/improves the triage model. **This is the venture's real moat** — proprietary ground truth that also directly attacks the state-generalization weakness (0.71→0.57).

## 3. The compliant per-line record schema (EPA LCRR/LCRI-aligned)

| Field | Notes |
|---|---|
| `pwsid` | Links each line to the system and the model ranking |
| `service_line_id` | Utility's unique line ID |
| `location` | Address / parcel / GIS feature ID (utility-supplied) |
| `system_side_material` | Lead / GRR / Non-lead / Unknown (LCRR requires the utility-owned portion) |
| `customer_side_material` | Lead / GRR / Non-lead / Unknown (LCRR requires the customer-owned portion) |
| `overall_classification` | Worst-case of the two portions, per EPA logic |
| `basis_of_classification` | Records review / visual inspection / water-quality sampling / potholing-excavation / **predictive model** (our model is the pre-investigation basis; field work upgrades it) |
| `install_year` | Estimated or known; key driver (pre/post lead-ban era) |
| `investigation_method` | How it was verified in the field |
| `investigation_date`, `inspector` | Audit trail |
| `investigation_cost` | Feeds cost-per-find analytics |
| `predicted_status`, `confirmed_status` | The feedback-loop pair (model said X, field found Y) |
| `disadvantaged_community_flag` | LCRI requires prioritizing these |
| `notes`, `photo_ref` | Evidence |

Rollup rule for submission: count each line into exactly one of {Lead, GRR, Unknown, Non-lead} by `overall_classification` → reproduces the SDWIS 4-category report the state expects.

## 4. What we already have vs. what is needed

**Have (no new work):** model system-priority ranking; the SDWIS 4-category schema; SDWA reference vocabularies (`SDWA_REF_CODE_VALUES`); EPA LCRR category/basis definitions; the model↔tool data contract design above.

**Needed but not buildable by us (must be imported from the utility):** the line-level service-line list with locations and install years. No public dataset provides it — consistent with the earlier data-access finding.

**Needed and we will build:** the schema enforcement, the import/priority-join, the capture UI, the dual export, and the labeled-feedback writer.

## 5. Architecture options for the MVP (honest, solo-founder, pre-customer)

| Option | Pros | Cons |
|---|---|---|
| **A. Local web app (Flask, offline-capable)** | Real per-line form UI; usable by office staff; clean export + feedback file | More to build; field crews need a laptop/tablet |
| **B. Structured Excel/CSV template + validator script** | Fastest, lowest-risk, works anywhere, no install; round-trips to the model | Weakest UX; validation is batch, not at entry |
| **C. Cloud SaaS** | "Product" feel | Cannot honestly build/secure/validate this solo pre-customer; out of scope |

**Recommendation: A (local web app) as the MVP, with B's CSV round-trip as the import/export format** — usable, demonstrable, reproducible, no overclaiming. C is explicitly out of scope until there is a validated customer.

## 6. Honest limitations to state

- v1 orders the worklist by *system* priority + utility heuristics; true *line-level* prioritization is a later module (needs utility line features).
- The feedback loop improves the model only as real investigations are logged; value compounds with use, it is not instant.
- The tool's compliance value depends on the utility's imported data quality (garbage in → garbage rollup).

## 7. Decisions needed before building

1. **Form factor:** local web app (A) vs. spreadsheet-template + validator (B).
2. **v1 scope:** capture + compliant export + feedback file only, or also attempt within-utility line ordering now.
3. **Inventory target:** confirm we target the 4 SDWIS categories + system/customer split (the safe superset of LCRR/LCRI) — recommended.
