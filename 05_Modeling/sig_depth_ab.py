"""
Fact-based A/B: does going past the 2nd signature level help?

Identical pipeline (same WSS 2013-2021 paths, same 2022+ health-violation
target, same static features, same LASSO->GBM, same 5-fold CV). Only the
signature block changes:

  A  depth-2 signature        (current default)
  B  depth-3 signature        (raw order-3, more redundant)
  C  depth-3 tensor-log       (same order-3 info, less-redundant coords)

Decision is read off the cross-validated ROC-AUC / PR-AUC and the number of
LASSO-selected features (parsimony). No assumptions baked in.
"""
import json, time
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score, average_precision_score, roc_curve

import signature_pipeline as spl

OUT = spl.OUT
CHARTS = spl.CHARTS


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def main():
    log("Building WSS 2013-2021 paths (shared across all variants)")
    pids, paths, static = spl.build_paths()
    universe = set(pids)
    health = spl.violation_target()
    sv = spl.site_visit_features(universe, before_year=spl.TARGET_YEAR)
    ps = spl.pubsys_features(universe)

    base = static.join(sv).join(ps)
    base["y"] = pd.Series(pids, index=pids).isin(health).astype(int)
    base = base.loc[pids]
    y = base["y"].to_numpy()
    cat = ["owner", "src"]
    for c in cat:
        base[c] = base[c].astype(str).fillna("UNK")
    nonsig = pd.get_dummies(base.drop(columns=["y"]), columns=cat,
                            dummy_na=False)
    nonsig = nonsig.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    prev = y.mean()
    log(f"  {len(y):,} systems, {y.sum():,} positives ({prev:.1%}), "
        f"{nonsig.shape[1]} non-signature features")

    from sklearn.model_selection import RepeatedStratifiedKFold

    variants = {
        "A_depth2_sig": dict(depth=2, logsig=False),
        "B_depth3_sig": dict(depth=3, logsig=False),
        "C_depth3_logsig": dict(depth=3, logsig=True),
    }
    # precompute the (scaled, LASSO-selected) design for each variant once
    designs, meta = {}, {}
    for name, kw in variants.items():
        feats, signames = spl.signatures_ex(paths, **kw)
        X = pd.DataFrame(feats, index=pids, columns=signames).join(nonsig)
        X = X.replace([np.inf, -np.inf], np.nan).fillna(0.0).astype(float)
        Xs = StandardScaler().fit_transform(X.values)
        l1 = LogisticRegression(solver="saga", l1_ratio=1.0, C=0.1,
                                class_weight="balanced", max_iter=5000,
                                random_state=42)
        l1.fit(Xs, y)
        sel = np.where(np.abs(l1.coef_.ravel()) > 1e-8)[0]
        designs[name] = Xs[:, sel]
        meta[name] = dict(sig_features=int(feats.shape[1]),
                          lasso_selected=int(len(sel)))
        log(f"  {name:18s} sigdim={feats.shape[1]:3d}  "
            f"LASSO={len(sel):3d}")

    # paired repeated CV: SAME splits for every variant -> honest deltas
    rkf = RepeatedStratifiedKFold(n_splits=5, n_repeats=5, random_state=42)
    per = {k: [] for k in variants}
    splits = list(rkf.split(np.zeros(len(y)), y))
    for name in variants:
        Xd = designs[name]
        for tr, te in splits:
            clf = HistGradientBoostingClassifier(
                max_depth=4, learning_rate=0.06, max_iter=400,
                class_weight="balanced", random_state=42)
            clf.fit(Xd[tr], y[tr])
            per[name].append(roc_auc_score(
                y[te], clf.predict_proba(Xd[te])[:, 1]))
        per[name] = np.array(per[name])
        log(f"  {name:18s} ROC-AUC {per[name].mean():.4f} "
            f"+/- {per[name].std():.4f} (25 splits)")

    A = per["A_depth2_sig"]
    res = {"baseline_pr_auc": float(prev), "n_systems": int(len(y)),
           "cv": "RepeatedStratifiedKFold 5x5 (25 paired splits)"}
    for name in variants:
        d = per[name] - A                       # paired per-split delta
        res[name] = {
            **meta[name],
            "roc_auc_mean": float(per[name].mean()),
            "roc_auc_std": float(per[name].std()),
            "delta_vs_depth2_mean": float(d.mean()),
            "delta_vs_depth2_std": float(d.std()),
            "pct_splits_beating_depth2": float((d > 0).mean()),
        }
    b = res["B_depth3_sig"]
    # robust verdict: clearly beyond noise AND consistent across splits
    robust = (b["delta_vs_depth2_mean"] > 0.005 and
              b["delta_vs_depth2_mean"] > 2 * b["delta_vs_depth2_std"] /
              np.sqrt(25) and b["pct_splits_beating_depth2"] >= 0.9)
    res["verdict"] = ("depth-3 robustly justified" if robust else
                      "depth-2 sufficient: depth-3 gain within CV noise / "
                      "not consistent; not worth 4x dimensionality")
    (OUT / "sig_depth_ab_results.json").write_text(json.dumps(res, indent=2))
    log(f"  B vs A: mean delta {b['delta_vs_depth2_mean']:+.4f} "
        f"+/- {b['delta_vs_depth2_std']:.4f}, "
        f"{b['pct_splits_beating_depth2']*100:.0f}% of splits favor B")
    log(f"VERDICT: {res['verdict']}")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        order = ["A_depth2_sig", "B_depth3_sig", "C_depth3_logsig"]
        data = [per[k] for k in order]
        labels = [f"{k}\n{meta[k]['sig_features']}d, "
                  f"LASSO {meta[k]['lasso_selected']}" for k in order]
        fig, ax = plt.subplots(figsize=(7, 4.6), dpi=200)
        bp = ax.boxplot(data, labels=labels, showmeans=True, widths=0.5)
        for i, dd in enumerate(data, 1):
            ax.scatter(np.full(len(dd), i)
                       + np.random.uniform(-0.07, 0.07, len(dd)),
                       dd, s=10, color="#1b6ca8", alpha=0.5, zorder=3)
        ax.set_ylabel("ROC-AUC per split")
        ax.set_title("Signature depth A/B — 25 paired CV splits\n"
                     f"verdict: {res['verdict']}", fontsize=10)
        ax.spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
        fig.savefig(CHARTS / "sig_depth_ab.png", bbox_inches="tight")
        log(f"  figure -> {CHARTS / 'sig_depth_ab.png'}")
    except Exception as e:
        log(f"  (figure skipped: {e})")


if __name__ == "__main__":
    main()
