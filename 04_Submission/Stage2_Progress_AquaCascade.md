# AquaCascade AI — Stage 2 Progress

**Founder:** Moise Tchivwila
**Venture (scoped honestly):** A calibrated decision-support tool that helps a water system or a state drinking-water program decide *which of its lead-status-unknown service lines to investigate first*, using that system's own historical EPA data. It improves triage where historical inventory data exists; it is not a nationwide cold-start predictor (the data does not support that claim — see *Limitation*).

---

## 1. What changed since Stage 1

My Stage 1 idea was network "cascade" modeling for water utilities. I tested it and the data did not support it (Section 4). Following the evidence, I moved to a problem the data *does* support and that has a binding national mandate: prioritizing lead and lead-status-unknown service line work under EPA's Lead and Copper Rule Improvements (LCRI, finalized October 2024). Every U.S. community water system must inventory and replace lead and galvanized-requiring-replacement service lines on a ~10-year horizon, with disadvantaged communities prioritized. This pivot is the central learning of Stage 1→2, and it was driven by experiments, not preference.

## 2. The problem, quantified from real EPA data

All figures below are computed directly from EPA SDWIS / SDWA public datasets on file (a reproducibility manifest with SHA-256 hashes and row counts of every input is included with this submission):

- **49,387** active community water systems analyzed.
- **2.96 million** lead + galvanized-requiring-replacement service lines that must be replaced (~$16.7B at a literature mid-range of $4,700/line — stated as an explicit assumption, not a finding).
- **22.5 million** "lead status unknown" service lines. This is **~7.6× larger than the known lead problem** and is the real bottleneck: every system is legally required to resolve these unknowns, investigation is cheap (~$200/line) relative to replacement, and there is no operational tool for deciding *which unknowns to investigate first*. This is where I focused.

## 3. What I built (real, reproducible)

A reproducible pipeline on the full EPA record:

- **Step 1 — trajectory:** per-system quarterly time series (EPA Water System Summary, 2013Q1–2026Q1, the full 53-quarter panel).
- **Step 2 — path signature transform** (geometric/Stratonovich, time-augmented) to linearize each system's nonlinear trajectory. Depth and stochastic-calculus convention were *chosen by experiment*, not assumption (Section 4).
- **Step 3 — LASSO feature selection → gradient-boosted classifier**, with isotonic probability calibration.
- A real wholesale buyer→seller dependency network (EPA SAB data) is included as a feature.

Every result below is cross-validated, leakage-controlled, and reproducible from the included manifest with one command.

## 4. Experiment log — what I tested, the evidence, the decision

The point of this section is the discipline, including the ideas I killed.

| Hypothesis tested | Evidence | Decision |
|---|---|---|
| Inter-utility physical "cascade" (risk propagates between adjacent systems) | Built the true service-area-polygon adjacency graph; only 52% of systems even have a neighbor, multi-hop propagation gave **no** lift and over-smoothing *hurt* | **Rejected.** Separate utilities are not a connected pipe network. |
| Risk is regionally clustered | Leakage-safe county-risk term lifts the health-violation model **+0.047 ROC-AUC** (0.695→0.742), residual Moran +0.06, stable across every rebuild | **Kept** (for the violation-risk model). |
| Long-run 2013–2025 distress trajectory predicts which unknowns are lead | Built it from local EPA data; A/B showed **−0.008 ROC**, no lift | **Rejected.** Lead pipe material is set by installation era, not violation history. |
| Original signature model AUC 0.727 | Found a temporal-ordering flaw: predictors post-dated part of the target window | **Corrected.** Honest leakage-free number is **0.683**. |
| Signature truncation depth (2 vs 3) | 25 paired CV splits: depth-3 beats depth-2 by **+0.0065 ROC, 100% of splits** | Depth-3 adopted on evidence; log-signature tested and rejected. |
| Itô vs Stratonovich signature | 25 paired splits: indistinguishable (Δ +0.0001) though feature blocks differ ~2–16% | Stratonovich kept on principled grounds (now data-backed). |
| Does the triage model generalize to unseen states? | Group-by-state CV: ROC collapses **0.71 → 0.57** | **Material limitation found** (Section 6). |

## 5. Headline verified result — the unknown-line triage tool

Production-hardened, calibrated, validated three independent ways, fully reproducible:

- **Within-distribution (repeated stratified 5×5 CV):** ROC-AUC **0.71** (per-repeat 0.710 ± 0.007), PR-AUC **0.22** vs a 0.092 base rate (~2.4×).
- **Well-calibrated:** ECE **0.024**, Brier **0.079** — the predicted probabilities are usable for budgeting, not just ranking.
- **Operational value:** prioritizing by the model, the **top 10% of systems contains ~33% of the lead-rich systems** (~3.3× better than choosing at random) — directly relevant to spending the cheap investigation dollars first.

I am deliberately *not* quoting a "$X billion saved" figure. Earlier versions of that calculation were artifacts of a flawed exposure proxy and a circular metric; I removed them. The honest, defensible claim is ranking and calibration quality, not a headline dollar number.

## 6. The limitation I am not hiding

Under group-by-state cross-validation — training on some states, testing on entirely unseen states — ROC-AUC falls from 0.71 to **0.57**, near chance. The model learns state- and program-specific reclassification patterns; it does **not** currently generalize cold to a new state.

This is why the venture is scoped as it is: it improves triage for a utility or state program **using its own historical SDWIS data** (which most have), not as a turnkey nationwide oracle. Stating this honestly is the point — weaker validation would have hidden it.

## 7. Next experiments (defined by the evidence, not by hope)

1. **Recover cross-state generalization:** test a hierarchical / state-aware model (state as a modeled level; per-state calibration). Clean, falsifiable; success criterion fixed in advance (group-by-state ROC materially above 0.57).
2. **Customer-discovery validation (not yet done — stated honestly):** structured interviews with state drinking-water program engineers and mid-size utility asset managers to test (a) whether unknown-line investigation triage is a real budget pain, (b) whether a calibrated priority list changes their sequencing, (c) what evidence they require to trust a model recommendation. No interviews or pilots have been conducted yet; this is the immediate next step, and I will report outcomes honestly whatever they are.
3. **Equity layer:** integrate the EPA Census-linked service-area demographics to test whether disadvantaged-community weighting (an explicit LCRI requirement) changes the priority ranking.

## 8. How AI was used

AI was the engine of the velocity shown in Section 4: rapid hypothesis generation, building and discarding the polygon-cascade, trajectory, and log-signature variants, the leakage audit that corrected 0.727→0.683, the depth and calculus A/B designs, and the reproducibility tooling. The compounding here is in *experiments run and killed per week*, not lines of code.

## 9. Responsible AI and public interest

This model allocates public health spending, so the standards are explicit and built in, not added later:
- **Transparency / reproducibility:** every input is hashed; the full pipeline re-runs from one command. Any reviewer or agency can verify the numbers independently.
- **Honest scope:** the state-generalization limit is stated up front so no agency over-trusts the tool outside its validated regime.
- **Equity:** LCRI requires prioritizing disadvantaged communities; the planned Census-linked equity layer makes that explicit and auditable.
- **No fabricated evidence:** every number in this document is computed from public EPA data and cross-validated. Claims that could not be substantiated were removed rather than softened.

---

*All metrics are reproducible from the included manifest (`triage_production_manifest.json`): input file SHA-256 hashes, row counts, library versions, fixed random seed, and exact outputs.*
