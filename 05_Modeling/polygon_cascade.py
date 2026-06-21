"""
AquaCascade AI - TRUE polygon-adjacency cascade (REAL EPA GIS data).

Upgrades the spatial layer from county clusters to a real adjacency network:
two water systems are neighbours iff their service-area POLYGONS touch /
nearly touch (System_Area_Boundary_Layer, SABL_PWSID). This is a connected
graph, so multi-hop Katz propagation (the FAA/NEXTOR operator) is meaningful.

Coverage is a regional subset (the boundary layer is mostly California,
~5k polygons) -> results are reported on the GIS-covered subset only, with
the subset size stated. Leakage-safe CV throughout. No fabricated metrics.
"""
import json, time
from pathlib import Path
import numpy as np
import pandas as pd
import scipy.sparse as sp
import geopandas as gpd
from shapely import STRtree
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import StratifiedKFold
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score, average_precision_score, roc_curve

import signature_pipeline as spl

BASE = Path(__file__).resolve().parent.parent
GIS = BASE / "01_Raw_Data" / "Service_Area_GIS" / "System_Area_Boundary_Layer.zip"
OUT = BASE / "05_Modeling"
CHARTS = BASE / "03_Outputs" / "Charts"
BUF = 75.0   # metres-ish (EPSG:3857) tolerance for "adjacent" boundaries


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def build_design():
    pids, paths, static = spl.build_paths()
    sig, signames = spl.signatures(paths)
    sigdf = pd.DataFrame(sig, index=pids, columns=signames)
    universe = set(pids)
    health = set(pd.read_csv(OUT / "cache_health_pwsids.csv",
                             dtype=str)["pwsid"])
    sv = spl.site_visit_features(universe)
    ps = spl.pubsys_features(universe)
    df = sigdf.join(static).join(sv).join(ps)
    df["y"] = df.index.to_series().isin(health).astype(int)
    df = df.dropna(subset=["y"])
    for c in ["pws_type", "owner", "src"]:
        df[c] = df[c].astype(str).fillna("UNK")
    X = pd.get_dummies(df.drop(columns=["y"]),
                       columns=["pws_type", "owner", "src"], dummy_na=False)
    X = X.replace([np.inf, -np.inf], np.nan).fillna(0.0).astype(float)
    return df.index.to_numpy(), X, df["y"].to_numpy()


def polygon_adjacency(keep_pids):
    g = gpd.read_file("zip://" + str(GIS),
                      columns=["SABL_PWSID"])
    g = g[g["SABL_PWSID"].isin(set(keep_pids))].copy()
    g = g[~g.geometry.is_empty & g.geometry.notna()]
    g["geometry"] = g.geometry.buffer(0)            # fix invalid polygons
    # one (multi)polygon per PWSID
    g = g.dissolve(by="SABL_PWSID").reset_index()
    pids = g["SABL_PWSID"].to_numpy()
    geoms = g.geometry.values
    idx = {p: i for i, p in enumerate(pids)}

    tree = STRtree(geoms)
    bgeoms = g.geometry.buffer(BUF).values
    rows, cols = [], []
    for i, bg in enumerate(bgeoms):
        for j in tree.query(bg):
            if j != i and geoms[i].intersects(bgeoms[j]):
                rows.append(i)
                cols.append(j)
    n = len(pids)
    A = sp.csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(n, n))
    A = ((A + A.T) > 0).astype(float)               # symmetric, binary
    A.setdiag(0)
    A.eliminate_zeros()
    return pids, A


def neighbor_risk(A, y, train_mask):
    """Leakage-safe: mean of TRAIN y over graph neighbours; fallback to
    train prevalence for isolated/no-train-neighbour nodes."""
    ytr = np.where(train_mask, y, 0.0)
    deg_tr = A @ train_mask.astype(float)
    s = A @ ytr
    out = np.divide(s, deg_tr, out=np.full(len(y), np.nan), where=deg_tr > 0)
    return np.where(np.isnan(out), y[train_mask].mean(), out)


def katz_feature(A, y, train_mask, alpha=0.15, hops=8):
    """Multi-hop Katz diffusion of TRAIN risk over the real adjacency graph.
    r <- (1-a) s + a * rownorm(A) r,  s = train-neighbour risk seed."""
    deg = np.asarray(A.sum(1)).ravel()
    deg[deg == 0] = 1.0
    P = sp.diags(1.0 / deg) @ A
    s = neighbor_risk(A, y, train_mask)
    r = s.copy()
    for _ in range(hops):
        r = (1 - alpha) * s + alpha * (P @ r)
    return r


def cv(Xmat, y, folds):
    clf = HistGradientBoostingClassifier(
        max_depth=4, learning_rate=0.06, max_iter=400,
        class_weight="balanced", random_state=42)
    oof = np.zeros(len(y))
    for tr, te in folds:
        clf.fit(Xmat[tr], y[tr])
        oof[te] = clf.predict_proba(Xmat[te])[:, 1]
    return oof


def main():
    log("Reconstructing validated design matrix")
    pid_all, Xdf, y_all = build_design()
    log(f"  national modelled systems: {len(pid_all):,}")

    log("Building TRUE polygon-adjacency graph (System_Area_Boundary_Layer)")
    gp, A = polygon_adjacency(pid_all)
    deg = np.asarray(A.sum(1)).ravel()
    log(f"  GIS-covered modelled systems: {len(gp):,}; "
        f"edges: {int(A.nnz/2):,}; mean degree {deg.mean():.2f}; "
        f"connected (>=1 nb): {(deg>0).mean():.1%}")

    # align design matrix to GIS subset
    pos = {p: i for i, p in enumerate(pid_all)}
    sel = np.array([pos[p] for p in gp])
    X = StandardScaler().fit_transform(Xdf.values[sel])
    y = y_all[sel]
    log(f"  subset positives: {y.mean():.1%} (n={len(y):,})")

    skf = StratifiedKFold(5, shuffle=True, random_state=42)
    folds = list(skf.split(X, y))

    oof_b = cv(X, y, folds)

    oof_1, oof_k = np.zeros(len(y)), np.zeros(len(y))
    clf = HistGradientBoostingClassifier(
        max_depth=4, learning_rate=0.06, max_iter=400,
        class_weight="balanced", random_state=42)
    for tr, te in folds:
        tm = np.zeros(len(y), bool); tm[tr] = True
        nb = neighbor_risk(A, y, tm).reshape(-1, 1)
        kz = katz_feature(A, y, tm).reshape(-1, 1)
        for store, extra in ((oof_1, nb), (oof_k, kz)):
            Xa = np.hstack([X, extra])
            clf.fit(Xa[tr], y[tr])
            store[te] = clf.predict_proba(Xa[te])[:, 1]

    def m(o):
        return roc_auc_score(y, o), average_precision_score(y, o)
    ab = m(oof_b); a1 = m(oof_1); ak = m(oof_k)

    resid = y - oof_b
    nb_res = neighbor_risk(A, resid + 0.0, np.ones(len(y), bool))
    keep = deg > 0
    moran = float(np.corrcoef(resid[keep], nb_res[keep])[0, 1])

    res = {
        "national_modelled_systems": int(len(pid_all)),
        "gis_covered_systems": int(len(gp)),
        "edges": int(A.nnz / 2),
        "mean_degree": float(deg.mean()),
        "pct_connected": float((deg > 0).mean()),
        "subset_positive_rate": float(y.mean()),
        "base_roc_auc": ab[0], "base_pr_auc": ab[1],
        "plus_1hop_roc_auc": a1[0], "plus_1hop_pr_auc": a1[1],
        "plus_katz_roc_auc": ak[0], "plus_katz_pr_auc": ak[1],
        "residual_spatial_autocorr_moran": moran,
        "note": "GIS boundary layer is a regional subset (mostly CA); "
                "metrics are on that covered subset only.",
    }
    (OUT / "polygon_results.json").write_text(json.dumps(res, indent=2))
    log("RESULTS (GIS-covered subset)")
    log(f"  base        ROC {ab[0]:.3f}  PR {ab[1]:.3f}")
    log(f"  + 1-hop nb  ROC {a1[0]:.3f}  PR {a1[1]:.3f}")
    log(f"  + Katz multi-hop ROC {ak[0]:.3f}  PR {ak[1]:.3f}")
    log(f"  residual spatial autocorr (Moran-like): {moran:+.3f}")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, 2, figsize=(11, 4.4), dpi=200)
        for o, c, lab in ((oof_b, "#7f8c8d", f"base  {ab[0]:.3f}"),
                          (oof_1, "#e08e0b", f"+1-hop  {a1[0]:.3f}"),
                          (oof_k, "#1b6ca8", f"+Katz multi-hop  {ak[0]:.3f}")):
            fpr, tpr, _ = roc_curve(y, o)
            ax[0].plot(fpr, tpr, color=c, lw=2, label=f"AUC {lab}")
        ax[0].plot([0, 1], [0, 1], "--", color="#ccc", lw=1)
        ax[0].set_xlabel("False positive rate")
        ax[0].set_ylabel("True positive rate")
        ax[0].set_title(f"True polygon-adjacency cascade  (n={len(y):,})")
        ax[0].legend(frameon=False, loc="lower right")
        ax[0].spines[["top", "right"]].set_visible(False)

        d = deg[deg > 0]
        ax[1].hist(d, bins=range(1, int(d.max()) + 2),
                   color="#1b6ca8", edgecolor="white")
        ax[1].set_xlabel("Number of adjacent service areas (node degree)")
        ax[1].set_ylabel("Systems")
        ax[1].set_title(f"Adjacency structure  ({int(A.nnz/2):,} edges)")
        ax[1].spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
        fig.savefig(CHARTS / "polygon_cascade_results.png",
                    bbox_inches="tight")
        log(f"  figure -> {CHARTS / 'polygon_cascade_results.png'}")
    except Exception as e:
        log(f"  (figure skipped: {e})")


if __name__ == "__main__":
    main()
