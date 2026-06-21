# Report A — Every Method Considered, Result, and Decision

**Purpose:** a complete, honest record of every modeling approach that was tried, the number it produced, why it was rejected or kept, and why the final method was chosen. All numbers are cross-validated and reproducible from `05_Modeling/` scripts and the JSON result files named in each row.

There were **two model tracks**, kept distinct on purpose:

- **Track 1 — Signature risk model.** The original "signature process" idea: predict a future health-based water-quality violation from a system's operating trajectory. Used to investigate methodology and the spatial-clustering question.
- **Track 2 — Unknown-line lead triage (the venture product).** Predict which of a system's "lead-status-unknown" service lines are actually lead, to prioritize the cheap investigation spend the LCRI mandate requires.

The final venture method is **Track 2, production-hardened** (Section 9). Track 1's value was the evidence it produced, not a shipped model.

---

## 1. Inter-utility cascade — county graph (Track 1 spatial test)

- **Idea:** risk propagates between water systems that share a county; use a county adjacency + leakage-safe county-risk term.
- **Result:** county-risk term lifts the health-violation model **+0.047 ROC-AUC** (0.695 → 0.742), PR-AUC 0.368 → 0.447, residual Moran +0.061. Stable across every rebuild. *(`spatial_results.json`)*
- **Decision: KEPT for Track 1.** This is the most robust spatial signal in the whole project. **But** the graph is ~1 county per system — disjoint clusters, not a connected propagation network. So this is *regional risk correlation* (shared regulator, source water, environment), **not** physical cascade. We kept the feature and dropped the "cascade" interpretation.

## 2. Inter-utility cascade — true service-area polygon adjacency

- **Idea:** the legitimate version of the cascade thesis — connect systems whose service-area polygons physically touch (EPA boundary GIS), then propagate risk multi-hop (Katz, the FAA/NEXTOR operator).
- **Result (GIS-covered subset, 3,591 systems, 2,289 edges):** only **52.5%** of systems have any neighbor (mean degree 1.27). 1-hop neighbor risk added **+0.006 ROC** (noise). Multi-hop Katz **hurt** (0.827 → 0.782). *(`polygon_results.json`)*
- **Decision: REJECTED.** Separate public water systems are geographically isolated entities, not a connected pipe network. The data refutes inter-utility physical cascade. This rejection is itself a key finding.

## 3. Long-run distress trajectory layer (Track 2 feature test)

- **Idea:** a 2013–2025 per-system annual violation/site-visit trajectory (plus its path-signature) should predict which unknowns are lead.
- **Result:** added to the triage model, A/B gave **−0.008 ROC** — no lift, slightly harmful. *(`unknown_triage_results.json`: `trajectory_layer_*`)*
- **Decision: REJECTED.** Substantively correct: lead-pipe material is set by installation era and plumbing codes, not by a utility's regulatory-violation history. This also showed the manually-downloaded Water System Summary quarters were unnecessary for the triage model.

## 4. Original signature model (the 0.727 that looked good)

- **Idea:** 5-quarter service-line-inventory path → predict health violation since 2022.
- **Result:** ROC-AUC 0.727.
- **Decision: CORRECTED (not kept).** Audit found a temporal-ordering flaw — predictors (2025–2026 data) post-dated part of the target window (≥2022). The honest, leakage-free rebuild on the 2013Q1–2021Q4 trajectory gives **ROC-AUC 0.683**. The 0.727 is not used anywhere; 0.683 is the real number. *(`model_results.json`, `supersedes` field)*

## 5. Signature truncation depth — 2 vs 3 vs log-signature

- **Idea:** does a deeper path signature carry more predictive information?
- **Result (25 paired CV splits, `sig_depth_ab_results.json`):**
  - depth-2: ROC 0.6753 ± 0.0056 (20 signature dims)
  - depth-3: ROC 0.6818 ± 0.0057 — **+0.0065 paired, beats depth-2 on 100% of 25 splits**
  - depth-3 log-signature: ROC 0.6736 — **worse**, and less parsimonious
- **Decision: depth-3 ADOPTED; log-signature REJECTED.** A first single-split read suggested "within noise"; the rigorous *paired* repeated-CV test overturned that — the effect is small but real and perfectly consistent. Decided by data, not assumption.

## 6. Stochastic-calculus convention — Itô vs Stratonovich

- **Idea:** does the integral convention of the signature matter for this data?
- **Result (25 paired splits, `sig_calculus_ab_results.json`):** the two feature blocks genuinely differ on this data (median relative L2 difference 2.3%), but predictively they are indistinguishable — paired Δ = **+0.0001 ± 0.0026**, Itô wins 44% of splits (coin flip).
- **Decision: STRATONOVICH KEPT.** Not because Itô is worse (it is equivalent here) but because, when two choices are empirically tied, you pick on principle: the geometric/Stratonovich signature has the shuffle identity and reparameterization invariance and is the standard object. Now data-backed.

## 7. Process-nature diagnostics (informs, not a model)

- **Result (`process_diagnostics.json`):** the per-system series are **drift-dominated integrated processes** — Hurst ≈ 0.95–0.98 (not Brownian's 0.5), AR(1) φ ≈ 0.92–0.96 (near unit root, not mean-reverting at the level); increments are weakly mean-reverting/anti-persistent. The pipeline is **non-anticipative by construction** (causal signature + strict pre-2022 predictor window + leakage channels excluded); only caveat is SDWIS reporting lag, which is conservative.
- **Use:** confirmed time-augmentation is needed, explained why deeper signatures and the Itô/Stratonovich choice barely move the needle (drift dominates the quadratic-variation correction), and confirmed signatures are appropriate (genuinely non-martingale paths).

## 8. Lead-replacement cost-optimization (early venture framing)

- **Idea:** optimize *which lead lines to replace first* to cut health exposure per dollar.
- **Result:** first version showed a 62.7% saving — found to be an **artifact** of a flawed exposure proxy (population ÷ lead-lines). With a defensible exposure model the real edge over "most-lines-first" is only **~4.2%**. *(`lsl_optimizer_results.json`)*
- **Decision: DE-PRIORITIZED.** Replacement ordering is nearly solved by the naive heuristic; the real, unsolved, mandated bottleneck is the **22.5M unknown lines** — which is why the venture moved to Track 2 triage. (The artifact was caught and removed; this is logged for transparency.)

## 9. Final method — production unknown-line triage (CHOSEN)

- **What it is:** calibrated gradient-boosted classifier on the evidence-supported base feature set, predicting which systems' unknown lines are lead, trained on **real reclassification behavior** observed across the SLI quarters (not synthetic labels). Rejected ideas (2, 3, 5-log-sig) are deliberately excluded.
- **Result (`triage_production_results.json`):**
  - Within-distribution repeated 5×5 CV: **ROC-AUC 0.71** (per-repeat 0.710 ± 0.007, 95% CI 0.705–0.716), PR-AUC 0.22 vs 0.092 base.
  - Well-calibrated: **ECE 0.024, Brier 0.079**.
  - Operational lift: top 10% by model contains **~33%** of lead-rich systems (~3.3×).
  - **Honest limitation:** group-by-state CV ROC = **0.57** — does not generalize cold to unseen states; county-rate term re-tested here and again **rejected** (−0.019).
- **Why chosen:** it is the only approach that (a) targets the real mandated unsolved problem, (b) is built on real ground truth, (c) is calibrated and reproducible, and (d) survives honest multi-scheme validation while stating its own limits. Every competing/auxiliary idea was tested and either folded in or rejected on evidence.

## 10. Were signatures applicable to the SELECTED (triage) target? Exhaustively tested

Signatures apply to any path; the data being non-Brownian was never a reason to exclude them. We tested every plausible signature path for the triage target (25 paired CV splits each):

- **Distress-trajectory signature** (2013–2025 violation/site-visit path): −0.008 ROC → **rejected**.
- **Service-line composition path**: not run — the label *is* the composition change over those same 5 quarters (circular/leaky, only 5 steps) → **excluded by design**.
- **Lead-sample PB90 concentration path** (`leadpath_signature_ab.py`, 2010–2024, 82% of systems with ≥3 sampled years): base ROC 0.700±0.012 → +PB90-signature 0.685±0.010; paired Δ **−0.0149±0.0075, 0% of 25 splits help** → **rejected**.

**Conclusion:** signatures are central to and help the Track-1 risk model, but every plausible signature application to the lead-material triage target was tested and rejected on evidence — the lead-pipe question is structural (installation era / plumbing codes), not trajectory-driven.

---

## Summary table

| # | Method | Headline result | Decision |
|---|---|---|---|
| 1 | County regional-risk term | +0.047 ROC | Kept (regional correlation, not cascade) |
| 2 | Polygon-adjacency multi-hop cascade | +0.006 (1-hop); Katz hurts | **Rejected** |
| 3 | Long-run trajectory layer | −0.008 ROC | **Rejected** |
| 4 | Original 5-qtr signature model | 0.727 (leakage) | **Corrected → 0.683** |
| 5 | Signature depth 2 vs 3 vs log | depth-3 +0.0065 (100% splits) | depth-3 kept; log-sig rejected |
| 6 | Itô vs Stratonovich | Δ +0.0001 (tie) | Stratonovich kept (principled) |
| 7 | Process diagnostics | Drift-dominated, non-anticipative | Informed design |
| 8 | Replacement cost-optimization | 4.2% (after removing 62.7% artifact) | De-prioritized |
| 9 | Distress-trajectory signature on triage | −0.008 ROC | **Rejected** |
| 10 | Lead-sample PB90-path signature on triage | −0.0149 ROC (0% of 25 splits help) | **Rejected** |
| 11 | **Production unknown-line triage** | **0.71 within / 0.57 cross-state, calibrated** | **CHOSEN** |
