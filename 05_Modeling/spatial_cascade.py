"""
AquaCascade AI - Spatial cascade layer (REAL EPA data).

Builds on signature_pipeline.py. Hypothesis under test:

  "A water system's infrastructure risk is not independent of its neighbours.
   Systems sharing a county are hydrologically/operationally coupled, so risk
   exhibits spatial structure that a per-system model misses."

Method (leakage-safe):
  1. Reuse the Step 1-3 design matrix (signature + static features) and the
     real target (health-based SDWA violation since 2022).
  2. Build a spatial graph: edge between two systems iff they serve a common
     county (SDWA_GEOGRAPHIC_AREAS, AREA_TYPE_CODE='CN').
  3. Cascade test: inside each CV fold, compute county risk from TRAIN labels
     only (leave-one-out for train rows), add it as ONE feature, and measure
     whether out-of-fold AUC improves vs the base model. Same folds both ways.
  4. Spatial-autocorrelation statistic (Moran-like) on base-model residuals.
  5. Katz propagation (the FAA/NEXTOR operator) over the county graph applied
     to base risk scores -> network-amplified priority ranking (illustrative).

No synthetic data, no fabricated metrics.
"""
import json, time
from pathlib import Path
import numpy as np
import pandas as pd
import scipy.sparse as sp
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import StratifiedKFold
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score, average_precision_score, roc_curve

import signature_pipeline as spl

BASE = Path(__file__).resolve().parent.parent
ECHO = BASE / "01_Raw_Data" / "SDWA_ECHO"
OUT = BASE / "05_Modeling"
CHARTS = BASE / "03_Outputs" / "Charts"


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def build_design():
    """Reconstruct the validated design matrix (target from cache -> fast)."""
    pids, paths, static = spl.build_paths()
    sig, signames = spl.signatures_ex(paths, depth=3, calculus="strat")
    sigdf = pd.DataFrame(sig, index=pids, columns=signames)
    universe = set(pids)

    cache = OUT / "cache_health_pwsids.csv"
    health = set(pd.read_csv(cache, dtype=str)["pwsid"])
    log(f"  health-violation systems (cached): {len(health):,}")

    sv = spl.site_visit_features(universe)
    ps = spl.pubsys_features(universe)
    df = sigdf.join(static).join(sv).join(ps)
    df["y"] = df.index.to_series().isin(health).astype(int)
    df = df.dropna(subset=["y"])
    cat = ["owner", "src"]
    for c in cat:
        df[c] = df[c].astype(str).fillna("UNK")
    X = pd.get_dummies(df.drop(columns=["y"]), columns=cat, dummy_na=False)
    X = X.replace([np.inf, -np.inf], np.nan).fillna(0.0).astype(float)
    y = df["y"].to_numpy()
    pid_index = df.index.to_numpy()
    return pid_index, X, y


def county_membership(pids):
    """system -> set(county_id). county_id = state(from PWSID) + ANSI code."""
    want = set(pids)
    m = {}
    for ch in pd.read_csv(ECHO / "SDWA_GEOGRAPHIC_AREAS.csv", dtype=str,
                           usecols=["PWSID", "AREA_TYPE_CODE",
                                    "ANSI_ENTITY_CODE"],
                           chunksize=200_000, encoding="latin-1"):
        ch = ch[(ch["AREA_TYPE_CODE"] == "CN")
                & ch["PWSID"].isin(want)
                & ch["ANSI_ENTITY_CODE"].notna()]
        for pid, ansi in zip(ch["PWSID"], ch["ANSI_ENTITY_CODE"]):
            cid = pid[:2] + ansi
            m.setdefault(pid, set()).add(cid)
    return m


def incidence(pids, memb):
    """Sparse systems x counties incidence matrix."""
    counties = sorted({c for s in memb.values() for c in s})
    cidx = {c: i for i, c in enumerate(counties)}
    rows, cols = [], []
    for i, p in enumerate(pids):
        for c in memb.get(p, ()):
            rows.append(i)
            cols.append(cidx[c])
    M = sp.csr_matrix((np.ones(len(rows)), (rows, cols)),
                      shape=(len(pids), len(counties)))
    return M


def fold_county_risk(M, y, train_idx, test_idx):
    """County mean of y from TRAIN only. Train rows: leave-one-out (exclude
    self). Test rows: plain county train-mean. Returns full-length vector."""
    n = M.shape[0]
    tr = np.zeros(n, bool)
    tr[train_idx] = True
    ytr = np.where(tr, y, 0.0)
    cnt_pos = M.T @ ytr                       # positives per county (train)
    cnt_tot = M.T @ tr.astype(float)          # train systems per county
    # per system: pooled train pos/tot over its counties, leaving self out
    pos_s = M @ cnt_pos
    tot_s = M @ cnt_tot
    self_pos = np.where(tr, y, 0.0) * (M.multiply(M).sum(axis=1)).A1
    self_tot = tr.astype(float) * (M.multiply(M).sum(axis=1)).A1
    deg = (M.sum(axis=1)).A1
    deg[deg == 0] = 1
    num = pos_s - self_pos
    den = tot_s - self_tot
    den[den <= 0] = np.nan
    out = (num / den)
    glob = ytr[train_idx].mean()
    out = np.where(np.isnan(out), glob, out)
    return out


def cv_oof(X, y, folds):
    clf = HistGradientBoostingClassifier(
        max_depth=4, learning_rate=0.06, max_iter=400,
        class_weight="balanced", random_state=42)
    oof = np.zeros(len(y))
    for tr, te in folds:
        clf.fit(X[tr], y[tr])
        oof[te] = clf.predict_proba(X[te])[:, 1]
    return oof


def katz(M, seed, alpha=0.4, hops=6):
    """Bipartite Katz-style propagation through county hubs (FAA/NEXTOR
    operator). r <- (1-a) seed + a * row-norm(M M^T, no self) r."""
    deg = np.asarray(M.sum(axis=1)).ravel()
    deg[deg == 0] = 1
    r = seed.copy()
    for _ in range(hops):
        cty = (M.T @ r)                       # aggregate to counties
        back = (M @ cty)                      # back to systems
        back = (back - r * (M.multiply(M).sum(axis=1)).A1) / deg
        r = (1 - alpha) * seed + alpha * back
    return r


def main():
    log("Reconstructing validated design matrix")
    pids, Xdf, y = build_design()
    X = StandardScaler().fit_transform(Xdf.values)
    log(f"  {len(y):,} systems, {X.shape[1]} features, "
        f"positives {y.mean():.1%}")

    log("Building county spatial graph from SDWA_GEOGRAPHIC_AREAS")
    memb = county_membership(pids)
    M = incidence(pids, memb)
    cov = np.asarray(M.sum(axis=1)).ravel()
    log(f"  {M.shape[1]:,} counties; systems with >=1 county: "
        f"{(cov > 0).mean():.1%}; mean counties/system {cov.mean():.2f}")

    skf = StratifiedKFold(5, shuffle=True, random_state=42)
    folds = list(skf.split(X, y))

    log("Base model: 5-fold out-of-fold predictions")
    oof_base = cv_oof(X, y, folds)
    auc_b = roc_auc_score(y, oof_base)
    ap_b = average_precision_score(y, oof_base)

    log("Cascade model: + leakage-safe county-risk feature")
    oof_sp = np.zeros(len(y))
    clf = HistGradientBoostingClassifier(
        max_depth=4, learning_rate=0.06, max_iter=400,
        class_weight="balanced", random_state=42)
    for tr, te in folds:
        cr = fold_county_risk(M, y, tr, te).reshape(-1, 1)
        Xa = np.hstack([X, cr])
        clf.fit(Xa[tr], y[tr])
        oof_sp[te] = clf.predict_proba(Xa[te])[:, 1]
    auc_s = roc_auc_score(y, oof_sp)
    ap_s = average_precision_score(y, oof_sp)

    # spatial autocorrelation (Moran-like) on base residuals
    resid = y - oof_base
    cty_resid_sum = M.T @ resid
    cty_resid_cnt = np.asarray(M.sum(axis=0)).ravel()
    cty_resid_cnt[cty_resid_cnt == 0] = 1
    nb_mean = (M @ (cty_resid_sum) - resid * (M.multiply(M).sum(axis=1)).A1)
    deg = np.asarray(M.sum(axis=1)).ravel()
    deg[deg == 0] = 1
    nb_mean = nb_mean / deg
    keep = cov > 0
    moran = np.corrcoef(resid[keep], nb_mean[keep])[0, 1]

    # NOTE: the EPA county field yields ~1 county/system, so the spatial
    # structure here is disjoint county clusters, not a connected adjacency
    # network. Multi-hop Katz propagation is therefore not meaningful on this
    # graph and is intentionally not reported. The katz() operator is kept for
    # use once true polygon adjacency (GIS) is available.
    res = {
        "n_systems": int(len(y)),
        "n_counties": int(M.shape[1]),
        "mean_counties_per_system": float(cov.mean()),
        "graph_note": "disjoint county clusters (~1 county/system); "
                      "not a multi-hop network",
        "pct_systems_with_county": float((cov > 0).mean()),
        "base_cv_roc_auc": float(auc_b),
        "base_cv_pr_auc": float(ap_b),
        "cascade_cv_roc_auc": float(auc_s),
        "cascade_cv_pr_auc": float(ap_s),
        "delta_roc_auc": float(auc_s - auc_b),
        "delta_pr_auc": float(ap_s - ap_b),
        "residual_spatial_autocorr_moran": float(moran),
    }
    (OUT / "spatial_results.json").write_text(json.dumps(res, indent=2))
    log("RESULTS")
    log(f"  base      ROC-AUC {auc_b:.3f}  PR-AUC {ap_b:.3f}")
    log(f"  +cascade  ROC-AUC {auc_s:.3f}  PR-AUC {ap_s:.3f}")
    log(f"  delta     ROC-AUC {auc_s-auc_b:+.3f}  PR-AUC {ap_s-ap_b:+.3f}")
    log(f"  residual spatial autocorrelation (Moran-like): {moran:+.3f}")
    log(f"  graph note: ~{cov.mean():.2f} counties/system "
        f"(disjoint clusters; Katz multi-hop not reported)")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fb = roc_curve(y, oof_base)
        fsp = roc_curve(y, oof_sp)
        fig, ax = plt.subplots(1, 2, figsize=(11, 4.4), dpi=200)
        ax[0].plot(fb[0], fb[1], color="#7f8c8d", lw=2,
                   label=f"base  AUC={auc_b:.3f}")
        ax[0].plot(fsp[0], fsp[1], color="#1b6ca8", lw=2,
                   label=f"+ spatial cascade  AUC={auc_s:.3f}")
        ax[0].plot([0, 1], [0, 1], "--", color="#ccc", lw=1)
        ax[0].set_xlabel("False positive rate")
        ax[0].set_ylabel("True positive rate")
        ax[0].set_title("Effect of the spatial cascade layer (5-fold CV)")
        ax[0].legend(frameon=False, loc="lower right")
        ax[0].spines[["top", "right"]].set_visible(False)

        # binned: county mean observed risk vs system positive rate
        cty_pos = (M.T @ y)
        cty_tot = np.asarray(M.sum(axis=0)).ravel()
        cty_tot[cty_tot == 0] = 1
        sys_cty_risk = (M @ (cty_pos / cty_tot)) / deg
        b = pd.qcut(sys_cty_risk[keep], 10, duplicates="drop")
        g = pd.DataFrame({"b": b, "y": y[keep]}).groupby("b",
                                                         observed=True)["y"].mean()
        ax[1].plot(range(len(g)), g.values, "o-", color="#c0392b")
        ax[1].set_xlabel("County-risk decile (low → high)")
        ax[1].set_ylabel("Observed health-violation rate")
        ax[1].set_title(f"Spatial clustering of risk  (Moran ≈ {moran:+.2f})")
        ax[1].spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
        fig.savefig(CHARTS / "spatial_cascade_results.png",
                    bbox_inches="tight")
        log(f"  figure -> {CHARTS / 'spatial_cascade_results.png'}")
    except Exception as e:
        log(f"  (figure skipped: {e})")


if __name__ == "__main__":
    main()
