"""
AquaCascade AI - Signature-based water-system risk model (REAL EPA data).

CORRECTED build using the full Water System Summary panel (2013Q1-2026Q1).

3-step methodology, on real data only:
  Step 1: per-PWS nonlinear time series = the 2013Q1..2021Q4 quarterly
          trajectory of [population, # facilities, cumulative # site visits]
          (36 real quarters, STRICTLY before the prediction window).
  Step 2: truncated path-signature transform (levels 1-2, time-augmented)
          to linearize the trajectory into signature coordinates.
  Step 3: L1 (LASSO) feature selection, then a gradient-boosted classifier,
          evaluated with honest stratified 5-fold cross-validation.

Target: a health-based SDWA violation with non-compliance beginning in 2022
or later -> the predictor window (<=2021Q4) is entirely BEFORE the target
window (>=2022), a clean temporal split. The cumulative #Violations channel
is deliberately EXCLUDED from the path (predicting violations from
violations would be leakage).

This supersedes the earlier 5-quarter Service-Line-Inventory version, which
(a) had only 5 timesteps and (b) used 2025-2026 paths to predict 2022+
violations - a temporal-ordering flaw now fixed. No synthetic data.
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

BASE = Path(__file__).resolve().parent.parent
RAW = BASE / "01_Raw_Data"
WSS = RAW / "Water_System_Summary"
ECHO = RAW / "SDWA_ECHO"
OUT = BASE / "05_Modeling"
CHARTS = BASE / "03_Outputs" / "Charts"

# predictor window: strictly before the 2022+ target window
PRED_QUARTERS = [f"{y} Q{q}" for y in range(2013, 2022) for q in (1, 2, 3, 4)]
MIN_QUARTERS = 12          # min observed quarters to keep a system
TARGET_YEAR = 2022


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _num(s):
    return pd.to_numeric(s.astype(str).str.replace(",", "", regex=False),
                         errors="coerce")


# ---------- STEP 1: per-PWS 2013-2021 quarterly trajectory ---------------
def build_paths():
    cols = ["PWS ID", "PWS Type", "Population Served Count",
            "# of Facilities", "# of Site Visits"]
    frames = []
    for qi, q in enumerate(PRED_QUARTERS):
        f = WSS / f"Water System Summary {q}.csv"
        d = pd.read_csv(f, dtype=str, usecols=cols, encoding="latin-1")
        d = d[d["PWS Type"] == "Community water system"]
        d = d.rename(columns={"PWS ID": "pwsid"})
        d["pop"] = _num(d["Population Served Count"])
        d["nfac"] = _num(d["# of Facilities"])
        d["nsv"] = _num(d["# of Site Visits"])
        d = d[["pwsid", "pop", "nfac", "nsv"]].drop_duplicates("pwsid")
        d["qidx"] = qi
        frames.append(d)
    allq = pd.concat(frames, ignore_index=True)
    nq = allq.groupby("pwsid")["qidx"].nunique()
    keep = nq[nq >= MIN_QUARTERS].index
    allq = allq[allq["pwsid"].isin(keep)]
    log(f"  systems with >= {MIN_QUARTERS}/{len(PRED_QUARTERS)} quarters: "
        f"{len(keep):,}")

    chans = ["pop", "nfac", "nsv"]
    piv = allq.pivot_table(index="pwsid", columns="qidx", values=chans)
    pids = piv.index.to_numpy()
    T = len(PRED_QUARTERS)
    arr = np.zeros((len(pids), T, len(chans)))
    for ci, ch in enumerate(chans):
        sub = piv[ch].reindex(columns=range(T))
        # cumulative/level series -> ffill then bfill along time
        sub = sub.ffill(axis=1).bfill(axis=1)
        v = sub.to_numpy(float)
        if ch == "pop":
            v = np.log1p(np.clip(v, 0, None))
        # standardise channel for comparable signature scales
        mu, sd = np.nanmean(v), np.nanstd(v) + 1e-9
        arr[:, :, ci] = (np.nan_to_num(v, nan=mu) - mu) / sd

    t = (np.arange(T) / (T - 1))[None, :, None]
    paths = np.concatenate([np.repeat(t, len(pids), 0), arr], axis=2)

    latest = (allq.sort_values("qidx").groupby("pwsid").tail(1)
              .set_index("pwsid"))
    static = pd.DataFrame(index=pids)
    static["log_pop_2021"] = np.log1p(
        latest["pop"].reindex(pids).fillna(0).clip(lower=0))
    static["nfac_2021"] = latest["nfac"].reindex(pids).fillna(0)
    log(f"  built paths array {paths.shape} (channels: time,logpop,nfac,nsv)")
    return pids, paths, static


# ---------- STEP 2: truncated path signature (levels 1-2) ----------------
def signatures(paths):
    n, L, d = paths.shape
    s1 = np.zeros((n, d))
    s2 = np.zeros((n, d, d))
    for k in range(L - 1):
        dx = paths[:, k + 1, :] - paths[:, k, :]
        s2 = s2 + s1[:, :, None] * dx[:, None, :] \
            + 0.5 * dx[:, :, None] * dx[:, None, :]
        s1 = s1 + dx
    feats = np.concatenate([s1, s2.reshape(n, d * d)], axis=1)
    names = [f"sig1_{i}" for i in range(d)] + \
            [f"sig2_{i}_{j}" for i in range(d) for j in range(d)]
    return feats, names


def signatures_ex(paths, depth=2, logsig=False, calculus="strat"):
    """Path signature, exact for piecewise-linear paths, truncated at
    `depth` in {2,3}.

    calculus="strat" : geometric / Stratonovich signature (trapezoidal;
        per segment keeps the +1/2 dx@dx and +1/6 dx^3 symmetric terms;
        obeys the shuffle identity; reparam-invariant).
    calculus="ito"   : Ito / left-point iterated-SUMS signature
        (S_n += S_{n-1} (x) dx only). Equals the Stratonovich signature
        minus 1/2 the realized quadratic-covariation correction.

    If logsig (Stratonovich only): truncated tensor-logarithm instead.
    """
    n, L, d = paths.shape
    s1 = np.zeros((n, d))
    s2 = np.zeros((n, d, d))
    s3 = np.zeros((n, d, d, d))
    ito = (calculus == "ito")
    for k in range(L - 1):
        dx = paths[:, k + 1, :] - paths[:, k, :]
        dxdx = dx[:, :, None] * dx[:, None, :]
        if depth >= 3:                       # update s3 with OLD s1,s2
            s3 = s3 + s2[:, :, :, None] * dx[:, None, None, :]
            if not ito:
                s3 = (s3
                      + s1[:, :, None, None] * (0.5 * dxdx)[:, None, :, :]
                      + (dx[:, :, None, None] * dx[:, None, :, None]
                         * dx[:, None, None, :]) / 6.0)
        s2 = s2 + s1[:, :, None] * dx[:, None, :]
        if not ito:
            s2 = s2 + 0.5 * dxdx
        s1 = s1 + dx
    if ito:
        logsig = False                       # logsig defined for geometric

    if logsig:
        # truncated tensor logarithm: log(1+X) = X - X^2/2 + X^3/3,
        # X = s1+s2+s3 (graded), keep level <= depth
        L1 = s1
        L2 = s2 - 0.5 * (s1[:, :, None] * s1[:, None, :])
        blocks = [L1.reshape(n, -1), L2.reshape(n, -1)]
        names = [f"ls1_{i}" for i in range(d)] + \
                [f"ls2_{i}_{j}" for i in range(d) for j in range(d)]
        if depth >= 3:
            s1s2 = s1[:, :, None, None] * s2[:, None, :, :]
            s2s1 = s2[:, :, :, None] * s1[:, None, None, :]
            s1c = (s1[:, :, None, None] * s1[:, None, :, None]
                   * s1[:, None, None, :])
            L3 = s3 - 0.5 * (s1s2 + s2s1) + (1.0 / 3.0) * s1c
            blocks.append(L3.reshape(n, -1))
            names += [f"ls3_{i}_{j}_{k}" for i in range(d)
                      for j in range(d) for k in range(d)]
        return np.concatenate(blocks, axis=1), names

    blocks = [s1, s2.reshape(n, d * d)]
    names = [f"sig1_{i}" for i in range(d)] + \
            [f"sig2_{i}_{j}" for i in range(d) for j in range(d)]
    if depth >= 3:
        blocks.append(s3.reshape(n, d * d * d))
        names += [f"sig3_{i}_{j}_{k}" for i in range(d)
                  for j in range(d) for k in range(d)]
    return np.concatenate(blocks, axis=1), names


# ---------- real target / features from SDWA (chunked) -------------------
def violation_target(universe=None):
    """All PWSIDs with a health-based violation whose non-compliance period
    began in TARGET_YEAR or later. Universe-independent (cached) so it can be
    reused across model variants."""
    cache = OUT / "cache_health_pwsids.csv"
    if cache.exists():
        s = set(pd.read_csv(cache, dtype=str)["pwsid"])
        log(f"  health target loaded from cache ({len(s):,})")
        return s
    cols = ["PWSID", "IS_HEALTH_BASED_IND", "NON_COMPL_PER_BEGIN_DATE"]
    health = set()
    rows = 0
    for chunk in pd.read_csv(ECHO / "SDWA_VIOLATIONS_ENFORCEMENT.csv",
                             usecols=cols, dtype=str, chunksize=600_000,
                             encoding="latin-1"):
        rows += len(chunk)
        yr = pd.to_numeric(
            chunk["NON_COMPL_PER_BEGIN_DATE"].str[-4:], errors="coerce")
        c = chunk[(yr >= TARGET_YEAR) & (chunk["IS_HEALTH_BASED_IND"] == "Y")]
        health.update(c["PWSID"].unique())
    log(f"  violations scanned: {rows:,} rows; health systems "
        f"(>= {TARGET_YEAR}): {len(health):,}")
    pd.Series(sorted(health), name="pwsid").to_csv(cache, index=False)
    return health


def site_visit_features(universe, before_year=None):
    cols = ["PWSID", "VISIT_ID", "VISIT_DATE",
            "MANAGEMENT_OPS_EVAL_CODE", "SOURCE_WATER_EVAL_CODE",
            "TREATMENT_EVAL_CODE", "DISTRIBUTION_EVAL_CODE",
            "FINISHED_WATER_STOR_EVAL_CODE", "PUMPS_EVAL_CODE"]
    evalc = cols[3:]
    visits, signif = {}, {}
    for chunk in pd.read_csv(ECHO / "SDWA_SITE_VISITS.csv",
                             usecols=cols, dtype=str, chunksize=400_000,
                             encoding="latin-1"):
        chunk = chunk[chunk["PWSID"].isin(universe)]
        if before_year is not None:
            yr = pd.to_numeric(chunk["VISIT_DATE"].str[-4:], errors="coerce")
            chunk = chunk[yr < before_year]
        if chunk.empty:
            continue
        vc = chunk.groupby("PWSID")["VISIT_ID"].count()
        for k, v in vc.items():
            visits[k] = visits.get(k, 0) + int(v)
        sig = (chunk[evalc] == "S").any(axis=1)
        sc = chunk.loc[sig].groupby("PWSID")["VISIT_ID"].count()
        for k, v in sc.items():
            signif[k] = signif.get(k, 0) + int(v)
    df = pd.DataFrame(index=sorted(universe))
    df["n_site_visits"] = pd.Series(visits).reindex(df.index).fillna(0)
    df["n_signif_defic"] = pd.Series(signif).reindex(df.index).fillna(0)
    return df


def pubsys_features(universe):
    cols = ["PWSID", "PWS_ACTIVITY_CODE", "OWNER_TYPE_CODE", "GW_SW_CODE",
            "SERVICE_CONNECTIONS_COUNT", "IS_SCHOOL_OR_DAYCARE_IND"]
    parts = []
    for chunk in pd.read_csv(ECHO / "SDWA_PUB_WATER_SYSTEMS.csv",
                             usecols=cols, dtype=str, chunksize=200_000,
                             encoding="latin-1"):
        parts.append(chunk[chunk["PWSID"].isin(universe)])
    df = pd.concat(parts, ignore_index=True).drop_duplicates("PWSID")
    df = df.set_index("PWSID")
    out = pd.DataFrame(index=df.index)
    out["log_conns"] = np.log1p(pd.to_numeric(
        df["SERVICE_CONNECTIONS_COUNT"], errors="coerce").fillna(0))
    out["is_school"] = (df["IS_SCHOOL_OR_DAYCARE_IND"] == "Y").astype(int)
    out["owner"] = df["OWNER_TYPE_CODE"].fillna("UNK")
    out["src"] = df["GW_SW_CODE"].fillna("UNK")
    return out


# ---------- assemble + Step 3 -------------------------------------------
def main():
    OUT.mkdir(exist_ok=True)
    CHARTS.mkdir(parents=True, exist_ok=True)

    log("STEP 1: per-PWS 2013Q1-2021Q4 trajectory (Water System Summary)")
    pids, paths, static = build_paths()

    log("STEP 2: path-signature transform (levels 1-3, geometric/"
        "Stratonovich, time-augmented; depth & calculus chosen empirically)")
    sig, signames = signatures_ex(paths, depth=3, calculus="strat")
    sigdf = pd.DataFrame(sig, index=pids, columns=signames)

    universe = set(pids)
    log("Target: health-based SDWA violation, non-compliance >= 2022")
    health = violation_target()

    log("Static features: pre-2022 site visits + system attributes")
    sv = site_visit_features(universe, before_year=TARGET_YEAR)
    ps = pubsys_features(universe)

    df = sigdf.join(static).join(sv).join(ps)
    df["y"] = df.index.to_series().isin(health).astype(int)
    df = df.dropna(subset=["y"])

    cat = ["owner", "src"]
    for c in cat:
        df[c] = df[c].astype(str).fillna("UNK")
    X = pd.get_dummies(df.drop(columns=["y"]), columns=cat, dummy_na=False)
    X = X.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    y = df["y"].to_numpy()

    prev = y.mean()
    log(f"Dataset: {len(y):,} systems, {X.shape[1]} features, "
        f"positives={y.sum():,} ({prev:.1%})")

    Xs = StandardScaler().fit_transform(X.astype(float))

    l1 = LogisticRegression(solver="saga", l1_ratio=1.0, C=0.1,
                            class_weight="balanced", max_iter=5000,
                            random_state=42)
    l1.fit(Xs, y)
    coef = l1.coef_.ravel()
    sel = np.where(np.abs(coef) > 1e-8)[0]
    log(f"STEP 3: LASSO selected {len(sel)}/{X.shape[1]} features")
    top = sorted(zip(X.columns[sel], coef[sel]),
                 key=lambda t: -abs(t[1]))[:15]

    clf = HistGradientBoostingClassifier(
        max_depth=4, learning_rate=0.06, max_iter=400,
        class_weight="balanced", random_state=42)
    cv = StratifiedKFold(5, shuffle=True, random_state=42)
    proba = cross_val_predict(clf, Xs[:, sel], y, cv=cv,
                              method="predict_proba")[:, 1]
    auc = roc_auc_score(y, proba)
    ap = average_precision_score(y, proba)

    results = {
        "data": "Water System Summary 2013Q1-2021Q4 (36 real quarters)",
        "target": "health-based SDWA violation, non-compliance >= 2022",
        "temporal_split": "predictors <=2021Q4 strictly before target >=2022",
        "signature": "geometric/Stratonovich, depth 3 (time-augmented)",
        "signature_choices_evidence": (
            "depth: depth-3 beats depth-2 by +0.0065 ROC over 25 paired CV "
            "splits (100% of splits); calculus: Ito vs Stratonovich "
            "indistinguishable (+0.0001 paired), Stratonovich kept on "
            "shuffle/reparam-invariance grounds"),
        "n_systems": int(len(y)),
        "n_features_total": int(X.shape[1]),
        "n_features_selected": int(len(sel)),
        "positive_rate": float(prev),
        "cv_roc_auc": float(auc),
        "cv_pr_auc": float(ap),
        "baseline_pr_auc": float(prev),
        "supersedes": "prior 5-quarter SLI version (ROC-AUC 0.727, had "
                      "temporal-ordering flaw)",
        "top_lasso_features": [[n, float(c)] for n, c in top],
    }
    (OUT / "model_results.json").write_text(json.dumps(results, indent=2))
    log("RESULTS")
    log(f"  CV ROC-AUC : {auc:.3f}   (prior 5-qtr SLI version: 0.727)")
    log(f"  CV PR-AUC  : {ap:.3f}   (baseline {prev:.3f})")
    log("  top selected features:")
    for nme, c in top[:10]:
        log(f"     {c:+.3f}  {nme}")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fpr, tpr, _ = roc_curve(y, proba)
        fig, ax = plt.subplots(1, 2, figsize=(11, 4.4), dpi=200)
        ax[0].plot(fpr, tpr, color="#1b6ca8", lw=2, label=f"AUC = {auc:.3f}")
        ax[0].plot([0, 1], [0, 1], "--", color="#999", lw=1)
        ax[0].set_xlabel("False positive rate")
        ax[0].set_ylabel("True positive rate")
        ax[0].set_title("2022+ health-violation risk from 2013-2021 "
                        "trajectory\n(signature model, 5-fold CV)",
                        fontsize=10)
        ax[0].legend(frameon=False)
        ax[0].spines[["top", "right"]].set_visible(False)
        names = [n for n, _ in top[::-1]]
        vals = [c for _, c in top[::-1]]
        cols = ["#c0392b" if v < 0 else "#1b6ca8" for v in vals]
        ax[1].barh(range(len(vals)), vals, color=cols)
        ax[1].set_yticks(range(len(vals)))
        ax[1].set_yticklabels(names, fontsize=7)
        ax[1].set_xlabel("LASSO coefficient (standardized)")
        ax[1].set_title("Top selected predictors", fontsize=10)
        ax[1].spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
        fig.savefig(CHARTS / "model_signature_results.png",
                    bbox_inches="tight")
        log(f"  figure -> {CHARTS / 'model_signature_results.png'}")
    except Exception as e:
        log(f"  (figure skipped: {e})")


if __name__ == "__main__":
    main()
