# Experiment Log #02 — Signature methodology chosen by experiment, not assumption

| | |
|---|---|
| Team ID | *[your Team ID]* |
| Founder | Moise Tchivwila |
| Venture | AquaCascade AI |
| Submitted | 2026-05-17 |
| Entry # | 2 |
| Type | Methodology rigor — paired A/B testing |

## Assumption tested

Two design choices at the heart of the venture's mathematical centerpiece (the path-signature transform that linearizes per-system nonlinear trajectories) were carried in as defaults from prior rough-path-theory work:

1. **Truncation depth = 2** ("depth-2 should be enough; depth-3 quadruples dimensionality").
2. **Geometric / Stratonovich** convention ("standard in the signature literature; Itô would not make a real difference on observed paths").

Both deserved to be tested on **our** data rather than asserted from outside literature.

## Method (what we did)

For every methodology choice we used the same rigorous protocol so the results are comparable:

- Same train/test data (51,056 community water systems; target = a 2022+ health-based SDWA violation; predictor window strictly pre-2022 → no temporal leakage).
- Same downstream pipeline (LASSO feature selection → calibrated gradient-boosted trees).
- **RepeatedStratifiedKFold 5 × 5 = 25 paired CV splits**, identical splits across variants, paired delta — the right test for small effects.
- Decision criterion fixed in advance: a variant wins only if mean Δ > 0.005 ROC AND beats the other on a clear majority of paired splits (>≈ 90%).

Two A/B experiments run:

**A/B-1 — Signature depth (3 variants):** depth-2 signature (20 dims), depth-3 signature (84 dims), depth-3 *log-signature* (84 dims, less-redundant coordinates).

**A/B-2 — Stochastic-calculus convention:** Stratonovich (geometric) vs Itô (left-point iterated sums), at depth 3.

## Evidence gathered

**A/B-1 — Depth**

| Variant | sig dim | LASSO selected | CV ROC-AUC (25 splits) | Paired Δ vs depth-2 | % splits favouring |
|---|---|---|---|---|---|
| A · depth-2 | 20 | 30 | 0.6753 ± 0.0056 | — | — |
| B · depth-3 | 84 | 66 | **0.6818 ± 0.0057** | **+0.0065 ± 0.0026** | **25 / 25 (100 %)** |
| C · depth-3 log-sig | 84 | 90 | 0.6736 ± 0.0051 | −0.0018 | 6 / 25 (24 %) |

**A/B-2 — Calculus** (depth-3, 25 paired splits)

| Variant | CV ROC-AUC | Paired Δ (Itô − Strat) | % splits Itô beats Strat | Feature relative L2 difference (median per system) |
|---|---|---|---|---|
| Stratonovich (geometric) | 0.6818 ± 0.0057 | — | — | — |
| Itô (iterated sums) | 0.6819 ± 0.0065 | +0.0001 ± 0.0026 | 11 / 25 (44 %) | 2.3 % |

Artifacts: `05_Modeling/sig_depth_ab.py`, `sig_depth_ab_results.json`, `03_Outputs/Charts/sig_depth_ab.png`; `05_Modeling/sig_calculus_ab.py`, `sig_calculus_ab_results.json`, `03_Outputs/Charts/sig_calculus_ab.png`.

## Learning

- **Depth-3 robustly beats depth-2.** A first single-split read suggested the +0.0065 gain was within noise. The proper *paired* analysis overturned that doubt: 100 % of 25 splits favour depth-3, mean Δ ≈ 12 standard errors above zero. Small but real.
- **Log-signature does NOT help on this data.** Slightly worse and *less* parsimonious (LASSO kept 90 features vs 30); the parametrization story does not survive contact with our data.
- **Itô vs Stratonovich are predictively indistinguishable** even though the feature blocks differ ~2 % L2 — meaningful difference in inputs, zero difference in outputs.

## Decision

1. **Adopt depth-3 Stratonovich signature** as the canonical methodology — robust evidence, paired test passes the pre-registered bar.
2. **Reject the log-signature variant.**
3. **Keep Stratonovich** on principled grounds (shuffle identity, reparameterization invariance) — but the choice is now data-backed rather than asserted.

## How the venture changed

- The venture's methodological story moved from *"we use the signature method from rough-path theory"* (assertive) to *"we tested depth and calculus by paired CV; here are the numbers and the decisions they justify"* (rigorous).
- The full methodology audit (Report A in `06_Reports/`) is now a defensible companion document.
- For a competition weighting *Experimentation Rigor* at 30 %, this is the discipline I'm explicitly committing to.

## AI leverage

The paired-CV harness, the depth-3 Chen-recursion implementation, the truncated tensor-logarithm, the Itô variant (= drop the symmetric correction terms; exactly the discrete left-point iterated-sums signature), the same-splits paired-delta accounting, and the publication-quality figures were all built with AI assistance in under a day. The depth and calculus experiments took **3 minutes each** of compute and **~half a day** of design + interpretation. Running the same A/Bs by hand would have taken weeks and very likely yielded a single-split "within noise" conclusion that the rigorous test contradicted.

## Responsible impact

The headline finding I was tempted to brag about (an early run showed +0.040 ROC — a flashy number) was traced to a confound; the *honest* paired test gave +0.0065. We are publishing the small, honest number with its statistical justification, not the inflated one — and the same discipline now applies to every modeling choice in the venture.

## Next experiment defined by this result

The *content* of the signature is settled; what is **not** settled is the **target generalization** — see Log #03 for the production-grade triage validation that surfaced the state-generalization limit.

## Verifiable artifacts

Public repository: **https://github.com/Brothermoses/aquacascade-ai**

- Depth A/B (script + JSON + chart):
  https://github.com/Brothermoses/aquacascade-ai/blob/main/05_Modeling/sig_depth_ab.py
  · https://github.com/Brothermoses/aquacascade-ai/blob/main/05_Modeling/sig_depth_ab_results.json
  · https://github.com/Brothermoses/aquacascade-ai/blob/main/03_Outputs/Charts/sig_depth_ab.png
- Itô vs Stratonovich A/B (script + JSON + chart):
  https://github.com/Brothermoses/aquacascade-ai/blob/main/05_Modeling/sig_calculus_ab.py
  · https://github.com/Brothermoses/aquacascade-ai/blob/main/05_Modeling/sig_calculus_ab_results.json
  · https://github.com/Brothermoses/aquacascade-ai/blob/main/03_Outputs/Charts/sig_calculus_ab.png
- Canonical pipeline (`signatures_ex(depth=3, calculus="strat")` default):
  https://github.com/Brothermoses/aquacascade-ai/blob/main/05_Modeling/signature_pipeline.py
- Decision narrative: https://github.com/Brothermoses/aquacascade-ai/blob/main/06_Reports/A_Methods_Decision_Log.md (sections 5–6)
