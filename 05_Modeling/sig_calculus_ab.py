"""
Fact-based A/B: Ito vs Stratonovich signature, at the adopted depth-3.

Identical pipeline (same WSS 2013-2021 paths, same 2022+ health-violation
target, same static features, same LASSO->GBM, same 25 paired CV splits).
Only the stochastic-integral convention of the signature changes:

  S  Stratonovich (geometric)  - trapezoidal; shuffle identity
  I  Ito (left-point iterated sums) - Strat minus 1/2 realized quad. covar.

Also reports, as factual evidence, HOW different the two feature blocks
actually are on this data (the realized-quadratic-covariation magnitude).
No assumptions baked into the decision.
"""
import json, time
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import RepeatedStratifiedKFold
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score

import signature_pipeline as spl

OUT, CHARTS = spl.OUT, spl.CHARTS
DEPTH = 3


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def main():
    log("Building WSS 2013-2021 paths (shared)")
    pids, paths, static = spl.build_paths()
    universe = set(pids)
    health = spl.violation_target()
    sv = spl.site_visit_features(universe, before_year=spl.TARGET_YEAR)
    ps = spl.pubsys_features(universe)

    base = static.join(sv).join(ps)
    base["y"] = pd.Series(pids, index=pids).isin(health).astype(int)
    base = base.loc[pids]
    y = base["y"].to_numpy()
    for c in ["owner", "src"]:
        base[c] = base[c].astype(str).fillna("UNK")
    nonsig = pd.get_dummies(base.drop(columns=["y"]),
                            columns=["owner", "src"], dummy_na=False)
    nonsig = nonsig.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    log(f"  {len(y):,} systems, {y.sum():,} positives ({y.mean():.1%})")

    variants = {"S_stratonovich": "strat", "I_ito": "ito"}
    feat_blocks = {}
    for name, cal in variants.items():
        f, nm = spl.signatures_ex(paths, depth=DEPTH, calculus=cal)
        feat_blocks[name] = (f, nm)

    # factual divergence: how different are the two signature blocks?
    fs = feat_blocks["S_stratonovich"][0]
    fi = feat_blocks["I_ito"][0]
    rel = (np.linalg.norm(fs - fi, axis=1)
           / (np.linalg.norm(fs, axis=1) + 1e-12))
    log(f"  Ito vs Strat feature-block relative L2 difference: "
        f"median {np.median(rel):.3f}, mean {rel.mean():.3f}, "
        f"p90 {np.quantile(rel, .9):.3f}")

    designs, meta = {}, {}
    for name, (f, nm) in feat_blocks.items():
        X = pd.DataFrame(f, index=pids, columns=nm).join(nonsig)
        X = X.replace([np.inf, -np.inf], np.nan).fillna(0.0).astype(float)
        Xs = StandardScaler().fit_transform(X.values)
        l1 = LogisticRegression(solver="saga", l1_ratio=1.0, C=0.1,
                                class_weight="balanced", max_iter=5000,
                                random_state=42)
        l1.fit(Xs, y)
        sel = np.where(np.abs(l1.coef_.ravel()) > 1e-8)[0]
        designs[name] = Xs[:, sel]
        meta[name] = dict(sig_features=int(f.shape[1]),
                          lasso_selected=int(len(sel)))
        log(f"  {name:15s} sigdim={f.shape[1]} LASSO={len(sel)}")

    rkf = RepeatedStratifiedKFold(n_splits=5, n_repeats=5, random_state=42)
    splits = list(rkf.split(np.zeros(len(y)), y))
    per = {}
    for name in variants:
        Xd = designs[name]
        a = []
        for tr, te in splits:
            clf = HistGradientBoostingClassifier(
                max_depth=4, learning_rate=0.06, max_iter=400,
                class_weight="balanced", random_state=42)
            clf.fit(Xd[tr], y[tr])
            a.append(roc_auc_score(y[te], clf.predict_proba(Xd[te])[:, 1]))
        per[name] = np.array(a)
        log(f"  {name:15s} ROC-AUC {per[name].mean():.4f} "
            f"+/- {per[name].std():.4f} (25 splits)")

    d = per["I_ito"] - per["S_stratonovich"]                # paired
    res = {
        "depth": DEPTH,
        "cv": "RepeatedStratifiedKFold 5x5 (25 paired splits)",
        "ito_vs_strat_feature_rel_L2_median": float(np.median(rel)),
        "stratonovich": {**meta["S_stratonovich"],
                         "roc_auc_mean": float(per["S_stratonovich"].mean()),
                         "roc_auc_std": float(per["S_stratonovich"].std())},
        "ito": {**meta["I_ito"],
                "roc_auc_mean": float(per["I_ito"].mean()),
                "roc_auc_std": float(per["I_ito"].std())},
        "paired_delta_ito_minus_strat_mean": float(d.mean()),
        "paired_delta_std": float(d.std()),
        "pct_splits_ito_beats_strat": float((d > 0).mean()),
    }
    se = d.std() / np.sqrt(len(d))
    if abs(d.mean()) <= max(0.005, 2 * se):
        verdict = ("statistically indistinguishable on this data -> keep "
                   "Stratonovich on principled grounds (shuffle identity, "
                   "reparam-invariance)")
    elif d.mean() > 0:
        verdict = "Ito materially better -> adopt Ito"
    else:
        verdict = "Stratonovich materially better -> keep Stratonovich"
    res["verdict"] = verdict
    (OUT / "sig_calculus_ab_results.json").write_text(
        json.dumps(res, indent=2))
    log(f"  paired delta (Ito - Strat): {d.mean():+.4f} +/- {d.std():.4f}"
        f"  ({(d > 0).mean()*100:.0f}% splits Ito>Strat)")
    log(f"VERDICT: {verdict}")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(6.4, 4.6), dpi=200)
        dt = [per["S_stratonovich"], per["I_ito"]]
        ax.boxplot(dt, labels=["Stratonovich\n(geometric)", "Ito\n(iter. sums)"],
                   showmeans=True, widths=0.5)
        for i, dd in enumerate(dt, 1):
            ax.scatter(np.full(len(dd), i)
                       + np.random.uniform(-0.07, 0.07, len(dd)),
                       dd, s=10, color="#1b6ca8", alpha=0.5, zorder=3)
        ax.set_ylabel("ROC-AUC per split")
        ax.set_title("Ito vs Stratonovich signature (depth-3, 25 paired "
                     f"splits)\nΔ={d.mean():+.4f}±{d.std():.4f}; "
                     f"feat. rel-diff median {np.median(rel):.2f}",
                     fontsize=9)
        ax.spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
        fig.savefig(CHARTS / "sig_calculus_ab.png", bbox_inches="tight")
        log(f"  figure -> {CHARTS / 'sig_calculus_ab.png'}")
    except Exception as e:
        log(f"  (figure skipped: {e})")


if __name__ == "__main__":
    main()
