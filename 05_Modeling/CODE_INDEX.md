# CODE INDEX — 05_Modeling

Run any script with `python 05_Modeling/<name>.py` from the project root. Track 1 = signature health-risk model; Track 2 = unknown-line triage (the venture product).

| Script | Track | Purpose | Key output files | Status |
|---|---|---|---|---|
| `signature_pipeline.py` | 1 | Core signature method: WSS 2013–2021 path → depth-3 Stratonovich signature → LASSO → GBM, predict 2022+ health violation. Also defines shared loaders (`build_paths`, `signatures_ex`, `site_visit_features`, `pubsys_features`, `violation_target`). | `model_results.json`, `model_signature_results.png`, `cache_health_pwsids.csv` | **ACTIVE / canonical** |
| `spatial_cascade.py` | 1 | Leakage-safe county-risk term + Moran spatial-autocorr on the signature model. | `spatial_results.json`, `spatial_cascade_results.png` | ACTIVE (county term kept) |
| `unknown_triage.py` | 2 | Builds the real reclassification ground truth; first triage model + the (rejected) trajectory A/B. | `unknown_triage_results.json`, `unknown_triage_ranking.csv` | ACTIVE (superseded by production version for final numbers) |
| `triage_production.py` | 2 | **THE FINAL MODEL.** Calibrated triage + 3-scheme validation + reproducibility manifest + app ranking artifact. | `triage_production_results.json`, `triage_production_manifest.json`, `triage_production_ranking.csv`, `triage_production.png` | **ACTIVE / FINAL** |
| `sig_depth_ab.py` | 1 | Experiment: signature depth 2 vs 3 vs log-sig (25 paired CV). | `sig_depth_ab_results.json`, `sig_depth_ab.png` | Diagnostic (decision made) |
| `sig_calculus_ab.py` | 1 | Experiment: Itô vs Stratonovich (25 paired CV). | `sig_calculus_ab_results.json`, `sig_calculus_ab.png` | Diagnostic (decision made) |
| `process_diagnostics.py` | — | Empirical stochastic-nature tests (Hurst, AR(1), variance ratio). | `process_diagnostics.json`, `process_diagnostics.png` | Diagnostic |
| `trajectory_features.py` | 2 | Builds the 2013–2025 distress-trajectory features (TESTED, REJECTED — no lift). | `cache_trajectory.parquet` | Diagnostic (rejected) |
| `polygon_cascade.py` | 1 | True service-area polygon adjacency + Katz (TESTED, REJECTED). | `polygon_results.json`, `polygon_cascade_results.png` | Diagnostic (rejected) |
| `lsl_optimizer.py` | 2 | Lead-replacement cost-optimization (de-prioritized; 62.7% artifact found & removed → real 4.2%). | `lsl_optimizer_results.json`, `lsl_optimizer_results.png`, `lsl_priority_ranking.csv` | Diagnostic (de-prioritized) |

**Reproducibility:** `cache_health_pwsids.csv` (national health-violation set, year ≥ 2022) and `cache_trajectory.parquet` are caches; delete to force a clean rebuild from the 4 GB SDWA file. `triage_production_manifest.json` hashes every raw input.

**To reproduce the headline venture result:** `python 05_Modeling/triage_production.py` → read `triage_production_results.json`.
