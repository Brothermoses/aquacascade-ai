# Experiment Log #01 — Inter-utility "cascade" hypothesis tested and rejected; venture pivoted

| | |
|---|---|
| Team ID | *[your Team ID]* |
| Founder | Moise Tchivwila |
| Venture | AquaCascade AI |
| Submitted | 2026-05-17 |
| Entry # | 1 |
| Type | Major hypothesis test → strategic pivot |

## Assumption tested

That risk in water-utility infrastructure **cascades across utilities** through their physical service-area adjacency, and that a Katz-style multi-hop network propagation model on a real adjacency graph would produce a defensible nationwide risk product.

## Method (what we did)

1. Built the per-system signature risk model on real EPA SDWA data and confirmed the base predictive signal (51,056 community water systems; out-of-fold CV ROC-AUC 0.683, leakage-corrected).
2. Constructed the **true polygon-adjacency graph** of U.S. community water systems from EPA's Service Area Boundary Layer (4,993 polygons, GIS, geopandas + STRtree spatial index).
3. Tested the cascade hypothesis end-to-end on the same training data: leakage-safe 1-hop neighbor-risk feature, then multi-hop **Katz propagation** (α=0.4, 6 hops) — a standard network-centrality operator.
4. Compared against a non-cascade alternative (a leakage-safe **county regional-risk** term computed from training labels only, per fold).

## Evidence gathered

| Test | Result |
|---|---|
| GIS coverage on modelled systems | 3,591 / 65,720 covered, **2,289 edges, mean degree 1.27**, only **52.5%** of systems have any neighbor |
| Base ROC on GIS subset | **0.827** |
| + 1-hop neighbor risk | 0.833 ( **Δ +0.006** — within CV noise ) |
| + Multi-hop Katz | **0.782** ( **Δ −0.045 — actually hurt** ) |
| County regional-risk term (alternative) | base 0.695 → **0.742 (+0.047 ROC)**, stable across every rebuild |

Artifacts: `05_Modeling/polygon_cascade.py`, `05_Modeling/polygon_results.json`, `05_Modeling/spatial_cascade.py`, `05_Modeling/spatial_results.json`, `03_Outputs/Charts/polygon_cascade_results.png`.

## Learning

Separate public water systems are **geographically isolated entities**, not a connected pipe network. The cascade graph is too sparse for propagation to add signal; multi-hop Katz over-smooths a near-disconnected graph and **degrades** predictive performance. The real, robust spatial signal is **regional correlation** (shared regulator, source water, environmental context — captured by the county term), not physical inter-utility cascade.

## Decision

**Reject the inter-utility cascade framing.** Keep the county regional-risk feature as a legitimate auxiliary signal but stop calling it "cascade." Pivot the venture to the bottleneck the EPA data actually supports: the **22.5 million "lead-status-unknown" service lines** mandated by EPA's Lead and Copper Rule Improvements (LCRI) — a real, unsolved national prioritization problem with public ground truth (the SDWIS service-line inventory) where a calibrated criticality-and-scheduling formulation applies cleanly.

## How the venture changed

- Stated venture statement moved from *"network-cascade risk analytics for U.S. water utilities"* → *"calibrated decision-support tool that prioritizes which lead-status-unknown service lines to investigate first under LCRI."*
- Market sizing re-anchored on the SDWIS unknown-line inventory: ~49,000 community water systems, 2.96 M known Lead/GRR lines, **22.5 M unknowns** (~7.6× the known problem), ~$4.5B investigation @ $200/line.
- Methodology centerpiece (path signatures) retained for the risk model; cascade operator demoted to "tested and rejected — see Log #01."

## AI leverage

Hypothesis generation and the entire test harness were AI-assisted: rapid construction of the polygon-adjacency graph (geopandas + STRtree), the Katz propagation operator, the leakage-safe cross-validation, the alternative spatial baseline, and side-by-side comparison — built and run in a single day. Without AI acceleration, a test of this rigor (real GIS data, real CV, honest decision criterion fixed in advance) would have taken weeks; we'd likely have shipped the cascade story unchallenged.

## Responsible impact

We discovered (and avoided shipping) a framing that *sounded* technically sophisticated to non-specialist judges and customers but is **not supported by the data**. Putting a refuted hypothesis on the home page would have misled state drinking-water programs and ratepayers. Surfacing this negative result openly is the integrity standard the venture is committing to.

## Next experiment defined by this result

A **hierarchical / per-state-calibrated** model on the new (unknown-line triage) target, falsifiable in advance: success means group-by-state ROC materially above the cold-start baseline. (See Log #03 for why this experiment is the natural next step.)

## Verifiable artifacts

Public repository: **https://github.com/Brothermoses/aquacascade-ai**

- Engine + CV: https://github.com/Brothermoses/aquacascade-ai/blob/main/05_Modeling/polygon_cascade.py
- Exact numbers above (JSON): https://github.com/Brothermoses/aquacascade-ai/blob/main/05_Modeling/polygon_results.json
- County counter‑experiment: https://github.com/Brothermoses/aquacascade-ai/blob/main/05_Modeling/spatial_cascade.py and https://github.com/Brothermoses/aquacascade-ai/blob/main/05_Modeling/spatial_results.json
- Chart: https://github.com/Brothermoses/aquacascade-ai/blob/main/03_Outputs/Charts/polygon_cascade_results.png
- Decision narrative: https://github.com/Brothermoses/aquacascade-ai/blob/main/06_Reports/A_Methods_Decision_Log.md (sections 1–2)
