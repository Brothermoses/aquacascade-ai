"""
Fact-based A/B: does a SIGNATURE of the lead-sample (PB90) concentration
path help the unknown-line triage target?

This is the one signature application not previously tested. Same triage
ground truth (1,935 systems), same base features, same rigorous 25 paired
RepeatedStratifiedKFold protocol. Only difference: add a depth-3
Stratonovich signature of each system's yearly PB90 trajectory.

Non-anticipative: only lead samples with sampling year <= 2024 are used
(strictly before the 2025Q1->2026Q1 resolution that defines the label).
No synthetic data; the decision is read off the paired CV delta.
"""
import json, time
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import RepeatedStratifiedKFold
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score, average_precision_score

import signature_pipeline as spl
import unknown_triage as ut
from lsl_optimizer import lead_severity

OUT, CHARTS, ECHO = spl.OUT, spl.CHARTS, spl.ECHO
Y0, Y1 = 2010, 2024            # PB90 path window (pre-resolution)


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def base_design():
    """Identical to the production triage base build; returns gt + base X."""
    j = ut.load_q0_q4()
    j["lg0"] = j["lead_0"] + j["galv_0"]
    j["lg4"] = j["lead_4"] + j["galv_4"]
    j["resolved"] = (j["unknown_0"] - j["unknown_4"]).clip(lower=0)
    j["d_lg"] = (j["lg4"] - j["lg0"]).clip(lower=0)
    pids = j.index.to_numpy()
    lf0 = (j["lg0"] / j["total_0"].clip(lower=1)).to_numpy(float)
    sr, hb = ut.wholesale_seller_risk(pids, lf0)
    j["seller_lead_risk"] = sr
    j["is_buyer"] = hb
    j = j.join(lead_severity().set_index("pwsid")["lead90"]).fillna(
        {"lead90": 0.0})
    uni = set(pids)
    j = j.join(spl.site_visit_features(uni)).join(spl.pubsys_features(uni))
    gt = j[(j["unknown_0"] >= ut.MIN_RESOLVED) &
           (j["resolved"] >= ut.MIN_RESOLVED)].copy()
    gt["lead_yield"] = (gt["d_lg"] / gt["resolved"]).clip(0, 1)
    gt["y"] = (gt["lead_yield"] >= ut.HIGH_YIELD).astype(int)
    gt["lead_frac0_feat"] = (gt["lg0"] /
                             gt["total_0"].clip(lower=1)).clip(0, 1)
    gt["pop_0"] = np.log1p(gt["pop_0"])
    gt["total_0"] = np.log1p(gt["total_0"])
    gt["unknown_0"] = np.log1p(gt["unknown_0"])
    for c in ("owner", "src"):
        gt[c] = gt[c].astype(str).fillna("UNK")
    base_feat = ["unknown_0", "total_0", "pop_0", "lead_frac0_feat",
                 "lead90", "seller_lead_risk", "is_buyer", "n_site_visits",
                 "n_signif_defic", "log_conns", "is_school", "owner", "src"]
    X = pd.get_dummies(gt[base_feat], columns=["owner", "src"],
                       dummy_na=False).replace([np.inf, -np.inf], np.nan
                                               ).fillna(0.0).astype(float)
    return gt.index.to_numpy(), X, gt["y"].to_numpy()


def pb90_paths(pids):
    """Per-system yearly mean PB90 path over Y0..Y1, samples year<=2024."""
    want = set(pids)
    acc = {}                              # pid -> {year: [vals]}
    for ch in pd.read_csv(ECHO / "SDWA_LCR_SAMPLES.csv", dtype=str,
                          usecols=["PWSID", "CONTAMINANT_CODE",
                                   "SAMPLE_MEASURE", "SAMPLING_END_DATE"],
                          chunksize=300_000, encoding="latin-1"):
        ch = ch[(ch["CONTAMINANT_CODE"] == "PB90")
                & ch["PWSID"].isin(want)]
        v = pd.to_numeric(ch["SAMPLE_MEASURE"], errors="coerce")
        yr = pd.to_numeric(ch["SAMPLING_END_DATE"].str[-4:],
                           errors="coerce")
        ok = v.notna() & yr.notna() & (yr <= Y1) & (yr >= Y0)
        for pid, val, y in zip(ch["PWSID"][ok], v[ok], yr[ok]):
            acc.setdefault(pid, {}).setdefault(int(y), []).append(float(val))
    years = list(range(Y0, Y1 + 1))
    T = len(years)
    n = len(pids)
    arr = np.full((n, T), np.nan)
    sampled_years = np.zeros(n, int)
    for i, pid in enumerate(pids):
        d = acc.get(pid)
        if not d:
            continue
        sampled_years[i] = len(d)
        for k, yv in enumerate(years):
            if yv in d:
                arr[i, k] = np.mean(d[yv])
    # forward/back fill each row that has >=1 observation
    df = pd.DataFrame(arr).ffill(axis=1).bfill(axis=1)
    arr = df.to_numpy()
    arr = np.nan_to_num(arr, nan=0.0)
    # standardize the PB90 channel, add a time channel -> (n,T,2)
    mu, sd = arr[sampled_years > 0].mean(), arr[sampled_years > 0].std() + 1e-9
    z = (arr - mu) / sd
    z[sampled_years == 0] = 0.0
    t = (np.arange(T) / (T - 1))[None, :, None].repeat(n, 0)
    path = np.concatenate([t, z[:, :, None]], axis=2)
    return path, sampled_years


def paired_cv(Xmat, y):
    rkf = RepeatedStratifiedKFold(n_splits=5, n_repeats=5, random_state=42)
    cur = np.zeros(len(y))
    rep = []
    for i, (tr, te) in enumerate(rkf.split(Xmat, y)):
        clf = HistGradientBoostingClassifier(
            max_depth=4, learning_rate=0.06, max_iter=400,
            class_weight="balanced", random_state=42)
        clf.fit(Xmat[tr], y[tr])
        cur[te] = clf.predict_proba(Xmat[te])[:, 1]
        if (i + 1) % 5 == 0:
            rep.append(roc_auc_score(y, cur))
            cur = np.zeros(len(y))
    return np.array(rep)


def lasso_select(X, y):
    Xs = StandardScaler().fit_transform(X)
    l1 = LogisticRegression(solver="saga", l1_ratio=1.0, C=0.1,
                            class_weight="balanced", max_iter=5000,
                            random_state=42)
    l1.fit(Xs, y)
    sel = np.where(np.abs(l1.coef_.ravel()) > 1e-8)[0]
    return Xs[:, sel], len(sel)


def main():
    log("Building triage ground truth + base features")
    pids, Xb, y = base_design()
    log(f"  {len(y):,} systems, {int(y.sum())} positive ({y.mean():.1%})")

    log("Building per-system PB90 lead-sample paths (year<=2024)")
    path, syrs = pb90_paths(pids)
    cov = (syrs >= 3).mean()
    log(f"  systems with >=3 sampled years: {(syrs>=3).sum():,} "
        f"({cov:.0%}); >=1 year: {(syrs>0).mean():.0%}")

    sig, signames = spl.signatures_ex(path, depth=3, calculus="strat")
    sig = np.nan_to_num(sig, nan=0.0, posinf=0.0, neginf=0.0)
    log(f"  PB90-path signature dims: {sig.shape[1]}")

    Xb_v = Xb.values
    Xbs, nb = lasso_select(Xb_v, y)
    Xfs, nf = lasso_select(np.hstack([Xb_v, sig]), y)

    a = paired_cv(Xbs, y)                       # base
    b = paired_cv(Xfs, y)                       # base + PB90-path signature
    d = b - a
    se = d.std() / np.sqrt(len(d))
    robust = (d.mean() > 0.005 and d.mean() > 2 * se
              and (d > 0).mean() >= 0.8)
    res = {
        "n_systems": int(len(y)),
        "positive_rate": float(y.mean()),
        "pb90_path_window": f"{Y0}-{Y1} yearly mean (year<=2024)",
        "pct_systems_ge3_sampled_years": float(cov),
        "pct_systems_ge1_sampled_year": float((syrs > 0).mean()),
        "signature": "geometric/Stratonovich depth-3 of [time, PB90]",
        "sig_dims": int(sig.shape[1]),
        "base_lasso_selected": int(nb),
        "base_plus_sig_lasso_selected": int(nf),
        "base_roc_auc_mean": float(a.mean()),
        "base_roc_auc_std": float(a.std()),
        "plus_pb90sig_roc_auc_mean": float(b.mean()),
        "plus_pb90sig_roc_auc_std": float(b.std()),
        "paired_delta_mean": float(d.mean()),
        "paired_delta_std": float(d.std()),
        "pct_splits_sig_helps": float((d > 0).mean()),
        "verdict": ("PB90-path signature helps the triage target"
                    if robust else
                    "PB90-path signature does NOT materially help the "
                    "triage target (gain within CV noise / not consistent)"),
    }
    (OUT / "leadpath_signature_ab_results.json").write_text(
        json.dumps(res, indent=2))
    log("RESULTS")
    log(f"  base                 ROC {a.mean():.4f} +/- {a.std():.4f}")
    log(f"  + PB90-path signature ROC {b.mean():.4f} +/- {b.std():.4f}")
    log(f"  paired delta {d.mean():+.4f} +/- {d.std():.4f}  "
        f"({(d>0).mean()*100:.0f}% splits help)")
    log(f"  VERDICT: {res['verdict']}")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(6.6, 4.6), dpi=200)
        ax.boxplot([a, b], labels=["base\n(19 feats)",
                                   "base + PB90-path\nsignature"],
                   showmeans=True, widths=0.5)
        for k, dd in enumerate([a, b], 1):
            ax.scatter(np.full(len(dd), k)
                       + np.random.uniform(-0.07, 0.07, len(dd)),
                       dd, s=10, color="#1b6ca8", alpha=0.5, zorder=3)
        ax.set_ylabel("ROC-AUC per split (25 paired splits)")
        ax.set_title("Lead-sample (PB90) path signature on the triage "
                     f"target\nΔ={d.mean():+.4f}±{d.std():.4f}; "
                     f"{res['verdict'].split(' (')[0]}", fontsize=9)
        ax.spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
        fig.savefig(CHARTS / "leadpath_signature_ab.png",
                    bbox_inches="tight")
        log(f"  figure -> {CHARTS / 'leadpath_signature_ab.png'}")
    except Exception as e:
        log(f"  (figure skipped: {e})")


if __name__ == "__main__":
    main()
