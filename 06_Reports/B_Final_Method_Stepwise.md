# Report B — Complete Step-by-Step Method & Results

Every step, the method used at that step, and the exact result obtained. All numbers are cross-validated and reproducible from the named `05_Modeling/` script and its result JSON. Two tracks are documented: **Track 1** (signature risk model — the methodology engine) and **Track 2** (the selected venture product: unknown-line lead triage).

---

## PART I — Data foundation

| Step | Method | Result |
|---|---|---|
| I.1 Acquire EPA data | Download SDWA/ECHO + SDWIS service-line inventory + Water System Summary (53 quarters 2013Q1–2026Q1) + service-area GIS + Census-linked boundaries + wholesale buyer→seller list | All inputs on disk; 49,387 active community water systems |
| I.2 Verify encoding/joins | Detected SDWA CSVs are latin-1; confirmed SLI `PWS ID` == SDWA `PWSID` (direct join, no fuzzy match) | Clean joins; 0 duplicate PWSIDs in WSS |
| I.3 Reproducibility manifest | SHA-256 + row counts of all inputs; pin python/numpy/pandas/sklearn versions and seed 42 | `triage_production_manifest.json` |

---

## PART II — Track 1: Signature risk model

**Goal:** predict a 2022+ health-based SDWA violation from a system's 2013–2021 operating trajectory.

| Step | Method | Result |
|---|---|---|
| II.1 Build path | Per-system quarterly path of [log Population, # Facilities, cumulative # Site Visits], 2013Q1–2021Q4 (36 quarters), ≥12 quarters required, ffill/bfill, z-scored, time-augmented. `#Violations` channel excluded (target leakage) | 51,056 systems; path array (51056, 36, 4) |
| II.2 Signature transform | Geometric/Stratonovich path signature, depth 3, exact Chen recursion (`signatures_ex`) | 84 signature features (+16 static = 100) |
| II.3 Feature selection | L1 logistic (saga, l1_ratio=1.0, C=0.1) | 66 / 100 features kept |
| II.4 Classifier + validation | HistGradientBoosting, class-balanced, 5-fold stratified CV | **CV ROC-AUC 0.683**, PR-AUC 0.357 vs 0.196 base |
| II.5 Leakage audit & correction | Found original 5-quarter SLI version used 2025–26 predictors for a ≥2022 target (predictors post-dated target). Rebuilt with strict pre-2022 predictor window | 0.727 (flawed) → **0.683 (honest)**. The 0.727 is not used anywhere |
| II.6 Spatial county term | Leakage-safe county-risk feature (county mean of y from training rows only, per fold) added; residual Moran-like spatial autocorrelation measured | base 0.695 → **+county 0.742 (+0.047 ROC)**, PR 0.368→0.447; Moran +0.061. County graph is ~1 county/system (disjoint clusters → regional correlation, NOT physical cascade) |

---

## PART III — Methodology experiments (each decided by paired experiment)

Protocol unless noted: RepeatedStratifiedKFold, 5 folds × 5 repeats = **25 paired CV splits**, same splits across variants, paired delta.

| Step | Hypothesis / method | Result | Decision |
|---|---|---|---|
| III.1 Signature depth | depth-2 vs depth-3 vs depth-3 log-signature | depth-2 ROC 0.6753±0.0056; **depth-3 0.6818±0.0057 (+0.0065 paired, 100% of 25 splits)**; log-sig 0.6736 (−0.0018, 24%) | **depth-3 adopted; log-signature rejected** |
| III.2 Stochastic calculus | Itô vs Stratonovich signature | feature blocks differ (median rel-L2 2.3%) but ROC: Strat 0.6818±0.0057, Itô 0.6819±0.0065; paired Δ **+0.0001±0.0026**, 44% splits | **Stratonovich kept** on shuffle/reparam-invariance grounds (now data-backed) |
| III.3 Process nature | Per-system AR(1) φ, variance-ratio VR(4), Hurst (aggregated-variance), on levels and increments, all 51k systems | logpop φ≈0.92 H≈0.95; nfac φ≈0.93 H≈0.96; cumsv φ≈0.96 H≈0.98, VR4≈0.75; increment AR(1)<0 in 89–94% | Data is **drift-dominated integrated**, not Brownian, not OU; increments weakly mean-reverting. Pipeline **non-anticipative by construction**; only caveat = SDWIS reporting lag (conservative) |

---

## PART IV — Track 2: The SELECTED method (unknown-line lead triage)

**What it does:** estimate the probability a system's "lead-status-unknown" service lines are actually lead, to spend the cheap (~$200/line) investigation budget before the expensive (~$4,700/line) replacement. Script: `triage_production.py`.

| Step | Method | Result |
|---|---|---|
| IV.0 Inputs & manifest | 7 EPA files hashed (SHA-256 + row counts), versions + seed pinned | `triage_production_manifest.json` |
| IV.1 Real ground truth | For systems in both 2025Q1 & 2026Q1 SLI: `resolved`=unknowns reclassified; `lead_yield`=Δ(lead+galv)/resolved; keep `resolved≥10`; label `y=1` if lead_yield≥10% | **1,935 ground-truth systems, 178 positive (9.2%)** — observed, not synthetic |
| IV.2 Features (evidence-supported only) | 19 features: log unknown/total/pop, prior lead fraction, measured `lead90` (LCR), wholesale seller lead-risk (Katz over real buyer→seller graph), is-buyer, historical site-visits, significant-deficiency count, service connections, school flag, owner, source. Rejected ideas excluded by design | 19 modeled features, 44 states |
| IV.3 Model | HistGradientBoosting (class-balanced) + **isotonic probability calibration** via internal 3-fold (nested) | Calibrated probabilities |
| IV.4 Validation A — repeated stratified CV | 5×5 = 25 OOF evaluations; pooled + per-repeat | **ROC-AUC 0.71** (per-repeat 0.710±0.007, 95% CI 0.705–0.716); PR-AUC 0.22 vs 0.092; **Brier 0.079, ECE 0.024** (well-calibrated); **top-10% captures ≈33%** of lead-rich systems; precision@10% 0.31 |
| IV.5 Validation B — group-by-state CV | Train on some states, test on entirely unseen states | **ROC-AUC 0.57**, PR 0.12, top-decile 16% — does NOT generalize cold to a new state |
| IV.6 Validation C — leakage-safe spatial ablation | County lead-rate from training labels only, recomputed each fold, added as a feature | base 0.710 → +county **0.691**, Δ −0.019±0.009, helps 0% of repeats → spatial term **correctly excluded** |
| IV.7 Outputs | Metrics JSON, manifest, 3-panel figure (calibration / PR / lift), prioritized CSV of unresolved-unknown systems | `triage_production_results.json`, `unknown_triage_ranking.csv`, `triage_production.png` |
| IV.8 Market context (real EPA counts) | Count systems/lines still unresolved | 14,113 systems, **22.5M unknown lines (~$4.5B to investigate)**; value framed as top-decile lift, NOT a dollar headline |

---

## PART V — Did signatures help the SELECTED (triage) method? Every test.

Signatures apply to any path — the data being non-Brownian was never a reason. We tested every plausible signature path for the triage target. Protocol: 25 paired CV splits.

| Step | Signature path tested | Method | Result | Decision |
|---|---|---|---|---|
| V.1 Distress trajectory | Signature of 2013–2025 annual violation/site-visit path added to triage | A/B vs base | **−0.008 ROC, no lift** | Rejected |
| V.2 Service-line composition path | Signature of the 5-quarter lead/galv/unknown composition path | Not run — the label *is* the composition change over those same 5 quarters → circular / leaky / only 5 steps | Excluded by design (documented) |
| V.3 Lead-sample (PB90) path | Depth-3 Stratonovich signature of each system's yearly PB90 lead-concentration path (2010–2024, year≤2024 so non-anticipative); 82% of systems have ≥3 sampled years | A/B vs base, 25 paired splits (`leadpath_signature_ab.py`) | base ROC **0.700±0.012** → +PB90-sig **0.685±0.010**; paired Δ **−0.0149±0.0075, 0% of 25 splits help** | **Rejected** |

**Conclusion:** signatures are central to and help the Track-1 risk model, but every plausible signature application to the Track-2 *lead-material* target was tested and rejected on evidence — because the lead-pipe question is structural (pipe installation era, plumbing codes), not trajectory-driven. This is a tested fact, not an assumption.

---

## PART VI — Other methods tested and rejected/de-prioritized

| Step | Method | Result | Decision |
|---|---|---|---|
| VI.1 Inter-utility polygon cascade | True service-area-polygon adjacency + multi-hop Katz (3,591 GIS-covered systems, 2,289 edges) | only 52% have any neighbor; +1-hop +0.006 (noise); Katz **hurt** (0.827→0.782) | Rejected |
| VI.2 Replacement cost-optimization | Optimize which lead lines to replace first for exposure/$ | First version 62.7% saving = **artifact** of population÷lead-line proxy; corrected exposure model → real edge only **4.2%** vs naive | De-prioritized; artifact removed |

---

## PART VII — Honest limitations (state these in the talk)

1. **State-bound:** strong within-distribution (0.71), near-random cold-start on an unseen state (0.57).
2. **Selection bias:** ground truth = systems that resolved unknowns *early*; may differ from laggards.
3. **Modest absolute accuracy:** value is triage *concentration* (top-decile lift), not precise per-system prediction.
4. **Cost figures are explicit assumptions** ($200 investigate / $4,700 replace), not measured data.
5. **Signatures help the risk model, not the triage target** — exhaustively tested (Part V).

---

## PART VIII — Reproducibility

`python 05_Modeling/triage_production.py` regenerates every Track-2 number; `signature_pipeline.py` / `spatial_cascade.py` regenerate Track-1; the `sig_depth_ab.py`, `sig_calculus_ab.py`, `process_diagnostics.py`, `leadpath_signature_ab.py` scripts regenerate every experiment. Inputs are SHA-256-pinned in the manifest; seed 42 throughout.

## One-paragraph summary for the talk

> From how 1,935 water systems actually resolved their unknown service lines, we learn a calibrated model that ranks which systems' unknowns are lead — ROC-AUC 0.71 in repeated cross-validation, well-calibrated (ECE 0.024), with the top 10% capturing about a third of all lead-rich systems. We stress-tested it on unseen states (0.57) and scope it honestly to systems and programs using their own historical data. We tested signatures on this target three ways and the data rejected all three; signatures remain the engine of our separate risk model. Every figure reproduces from one command with hashed inputs.
