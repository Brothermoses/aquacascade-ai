# Experiment Log #03 — Production-grade triage validation surfaces an honest state-generalization limit; venture scope tightened

| | |
|---|---|
| Team ID | AIVVC-2026-B3T7A |
| Founder | Moise Tchivwila |
| Venture | AquaCascade AI |
| Submitted | 2026-06-20 |
| Entry # | 3 |
| Type | Production model + multi-scheme validation → scope decision |

## Assumption tested

That the unknown-line triage model, once calibrated and built on real reclassification ground truth, would (a) produce useful prioritization within distribution and (b) generalize to states it has not seen in training — supporting a *nationwide* product framing.

## Method (what we did)

1. **Built real ground truth (not synthetic).** Identified 1,935 community water systems that *actually resolved* unknown service lines between 2025Q1 and 2026Q1 SDWIS snapshots. Computed each system's `lead_yield = Δ(Lead+GRR) / unknowns resolved`; labelled `y = 1` if `lead_yield ≥ 10 %`. Positives = 178 (9.2 %). Every label is observed behavior, not assumption.
2. **Engineered evidence-supported features only** (19 features after one-hot): inventory size, prior lead fraction, **measured PB90 lead-sample severity** from real LCR samples, **wholesale buyer→seller network** Katz risk, historical site visits & significant deficiencies, service connections, source water, owner type. Ideas already rejected on evidence (signature trajectory layer, inter-utility cascade, log-signature) were *deliberately excluded* — methodology consistency with Logs #01–#02.
3. **Production model.** HistGradientBoostingClassifier wrapped in **isotonic probability calibration** (3-fold internal calibration → calibrated probabilities a utility can budget against).
4. **Validated three independent ways**, all leakage-controlled:
   - **(A) Repeated stratified 5 × 5 CV** — within-distribution.
   - **(B) Group-by-state CV** — train on some states, test on **entirely unseen** states (the cold-start nationwide generalization stress test).
   - **(C) Leakage-safe county-rate ablation** — does the county term that helps the Track-1 risk model help the triage target? Tested per fold using only training-fold labels.
5. **Emitted a reproducibility manifest**: SHA-256 + row counts of all 7 input files, library versions, fixed random seed (`triage_production_manifest.json`). Any third party can re-run and verify bit-for-bit.

## Evidence gathered

| Validation | ROC-AUC | PR-AUC | Calibration | Operational |
|---|---|---|---|---|
| **(A) Repeated stratified CV** | **0.71** (per-repeat 0.710 ± 0.007; 95 % CI **0.705 – 0.716**) | **0.22** (baseline 0.092 → ≈ 2.4 ×) | **Brier 0.079, ECE 0.024** (well-calibrated) | **Top 10 % of systems captures ≈ 33 % of lead-rich systems**; precision@10 % = 0.31 |
| **(B) Group-by-state CV** | **0.57** | 0.12 | — | Top-decile 16 % |
| **(C) County-rate ablation** | base 0.710 → **0.691** ( Δ **−0.019 ± 0.009**, helps 0 % of repeats ) | — | — | County feature **rejected** for this target |

Artifacts: `05_Modeling/triage_production.py`, `triage_production_results.json`, `triage_production_manifest.json`, `03_Outputs/Charts/triage_production.png` (3-panel calibration / PR / lift).

## Learning

- **Within-distribution, the model is real and useful**: well-calibrated, with a top-decile lift that materially reorders investigation work for a state program or utility.
- **The honest limitation we are not hiding**: under group-by-state CV the model **collapses to ROC = 0.57** — barely above chance. It is *learning state- and program-specific reclassification patterns*, not a universal physical law of where lead hides. It does **not** generalize cold to an unseen state.
- The county spatial term that helped the Track-1 violation model does **not** transfer to the lead-material target — confirmed leakage-safely. We do not invent value where the data shows none.

## Decision

1. **Scope the product honestly**: AquaCascade triage is "**improves triage for utilities and state programs USING THEIR OWN HISTORICAL SDWIS DATA**" — not a turnkey nationwide cold-start oracle. Most states have the needed history; the addressable scope is large and real, just not what an over-promise would have claimed.
2. **Make the state-generalization limit a first-class slide and a sentence in the venture narrative**, not a footnote.
3. **Adopt the calibrated model as the production model**; embed it in the operational system that hands prioritized investigation work orders to field crews.

## How the venture changed

- The product framing has a clearly stated envelope ("with their own historical data"), tied to a verifiable metric (within-distribution top-decile lift), with an honest negative (cross-state cold-start).
- The full operational system was built on this verified model (`07_Tool/aquacascade_system/`): backend, REST API, SQLite (Postgres-ready), multi-tenant scoping, role-separated technician workspaces, ~50-test suite all green. The system **only ships the validated model**; the scope statement is in the app footer.
- An honest experiment to attack the 0.71 → 0.57 limit is now defined and pre-registered (below).

## AI leverage

The complete production pipeline — calibrated model, the three-scheme validation harness, the SHA-256/version/seed manifest, the calibration/PR/lift visualization, and the operational web system that consumes the model output — was designed and built end-to-end with AI assistance, with passing automated tests, in days, not weeks. The same AI discipline produced the **honest** numbers: when an early efficiency comparison gave a flashy "40 % savings," AI-assisted audit traced it to a circular metric (rank by model score, then score against the model's own output); the corrected, non-circular paired-outcome test gave **3.9 %** at the 80 % mark and a **3.8 × top-decile lift** — published as the real numbers.

## Responsible impact

- **No fabricated traction.** I have not conducted customer interviews or signed pilots; the prior draft submission's "18 interviews / 3 signed pilots / $485 K pipeline" was material I disowned and quarantined. This entry, like #01 and #02, reports only verifiable model and data work.
- **Public-money decisions:** publishing a calibrated probability with a clearly stated generalization envelope is the responsible standard for a tool that could allocate Bipartisan Infrastructure Law lead-line dollars; reviewers can re-run every figure from the manifest.

## Next experiment defined by this result

**Pre-registered:** a **hierarchical / per-state-calibrated** model on the triage target. Success criterion fixed in advance — group-by-state ROC materially above 0.57 (target ≥ 0.65 with proper paired CV). Will be Experiment Log #04 once executed.

## Verifiable artifacts

Public repository: **https://github.com/Brothermoses/aquacascade-ai**

- Production model + 3-scheme validation: https://github.com/Brothermoses/aquacascade-ai/blob/main/05_Modeling/triage_production.py
- All metrics above (JSON): https://github.com/Brothermoses/aquacascade-ai/blob/main/05_Modeling/triage_production_results.json
- Reproducibility manifest (input SHA-256, library versions, seed): https://github.com/Brothermoses/aquacascade-ai/blob/main/05_Modeling/triage_production_manifest.json
- Charts (calibration / PR / lift): https://github.com/Brothermoses/aquacascade-ai/blob/main/03_Outputs/Charts/triage_production.png
- Operational system consuming the model: https://github.com/Brothermoses/aquacascade-ai/tree/main/07_Tool/aquacascade_system
- Full step-by-step + Stage 2 progress doc: https://github.com/Brothermoses/aquacascade-ai/blob/main/06_Reports/B_Final_Method_Stepwise.md · https://github.com/Brothermoses/aquacascade-ai/blob/main/04_Submission/Stage2_Progress_AquaCascade.md
