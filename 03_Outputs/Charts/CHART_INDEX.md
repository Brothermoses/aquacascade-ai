# CHART INDEX — 03_Outputs/Charts

Every figure, what it shows, the script that made it, and the headline number on it. All are 200 DPI, publication-clean.

| File | Shows | Made by | Headline |
|---|---|---|---|
| `triage_production.png` | **FINAL MODEL.** 3 panels: calibration curve, precision-recall, lift curve. | `triage_production.py` | ROC 0.71 within-dist; ECE 0.024; top-10% finds ~33% of lead-rich systems |
| `model_signature_results.png` | Signature health-risk model ROC + top LASSO predictors. | `signature_pipeline.py` | ROC-AUC 0.683 (leakage-free, 2013–2021 → 2022+) |
| `spatial_cascade_results.png` | Effect of the county regional-risk term + risk clustering by county decile. | `spatial_cascade.py` | base 0.695 → +county 0.742 (+0.047); Moran +0.06 |
| `sig_depth_ab.png` | Signature depth 2 vs 3 vs log-sig, 25 paired CV splits (boxplots). | `sig_depth_ab.py` | depth-3 +0.0065, 100% of splits |
| `sig_calculus_ab.png` | Itô vs Stratonovich, 25 paired splits (boxplots). | `sig_calculus_ab.py` | Δ +0.0001 — indistinguishable; kept Stratonovich |
| `process_diagnostics.png` | AR(1) φ and Hurst distributions per channel. | `process_diagnostics.py` | Hurst ≈ 0.95–0.98 → drift-dominated, not Brownian |
| `unknown_triage_results.png` | First triage model ROC + investigation-targeting on real outcomes. | `unknown_triage.py` | ROC 0.738; top-decile 38% of real lead |
| `polygon_cascade_results.png` | True polygon-adjacency cascade test + degree distribution. | `polygon_cascade.py` | **REJECTED:** +1-hop +0.006; Katz hurts; graph too sparse |
| `lsl_optimizer_results.png` | Replacement efficiency frontier + top-priority systems. | `lsl_optimizer.py` | **De-prioritized:** real edge only 4.2% (62.7% was an artifact) |

**For the presentation, lead with:** `triage_production.png` (the product), then `spatial_cascade_results.png` (regional clustering), then `sig_depth_ab.png` / `sig_calculus_ab.png` (methodology decided by evidence). `polygon_cascade_results.png` and `lsl_optimizer_results.png` are the honest "tested and rejected" slides.

Superseded Stage-1 charts were moved to `_archive/old_stage1_charts/` to avoid confusion.
