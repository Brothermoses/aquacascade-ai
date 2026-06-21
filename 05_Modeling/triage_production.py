"""
Production-hardened unknown-line lead-triage model - built for VERIFIABILITY.

Scope discipline: uses ONLY the evidence-supported base feature set (the one
that gave the honest CV ROC-AUC ~0.738). Rejected ideas (long-run trajectory,
deeper signatures, inter-utility cascade) are deliberately NOT re-added.

What "production / verifiable" adds:
  1. Probability calibration (isotonic, nested CV) -> budgetable risk scores;
     reported with reliability curve, Brier score and ECE.
  2. Three validation schemes side-by-side:
       (a) RepeatedStratifiedKFold 5x5  -> headline metric + CI
       (b) GroupKFold by US state       -> generalisation to unseen regions
       (c) leakage-safe county-rate ablation (does spatial structure help
           THIS target - tested per-fold, not assumed)
  3. Ranking metrics for the actual use case: ROC/PR-AUC, top-decile lift,
     precision@k.
  4. A reproducibility manifest: SHA-256 + row counts of every input file,
     library versions, fixed seeds, exact metrics -> anyone can re-run and
     verify bit-for-bit.

No synthetic data, no fabricated metrics.
"""
import json, time, hashlib, sys, platform
from pathlib import Path
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.calibration import CalibratedClassifierCV
from sklearn.model_selection import RepeatedStratifiedKFold, GroupKFold
from sklearn.metrics import (roc_auc_score, average_precision_score,
                             brier_score_loss)

import signature_pipeline as spl
import unknown_triage as ut
from lsl_optimizer import lead_severity
import spatial_cascade as sc

SEED = 42
OUT, CHARTS = spl.OUT, spl.CHARTS
ECHO = spl.ECHO
SLI = spl.BASE / "01_Raw_Data" / "Service_Line_Inventory"
BS = spl.BASE / "01_Raw_Data" / "EPA_SAB_repo" / "Buyers_Sellers_2023Q4.csv"

BASE_FEATURES = ["unknown_0", "total_0", "pop_0", "lead_frac0_feat",
                 "lead90", "seller_lead_risk", "is_buyer", "n_site_visits",
                 "n_signif_defic", "log_conns", "is_school", "owner", "src"]
CAT_FEATURES = ["owner", "src"]


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def sha256_rows(p):
    h = hashlib.sha256()
    n = -1
    with open(p, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
            n += blk.count(b"\n")
    return h.hexdigest(), max(n, 0)


def manifest():
    files = [
        SLI / "SDWIS_service_line_inventory_2025Q1.csv",
        SLI / "SDWIS_service_line_inventory_2026Q1.csv",
        ECHO / "SDWA_LCR_SAMPLES.csv",
        ECHO / "SDWA_SITE_VISITS.csv",
        ECHO / "SDWA_PUB_WATER_SYSTEMS.csv",
        ECHO / "SDWA_GEOGRAPHIC_AREAS.csv",
        BS,
    ]
    inp = {}
    for f in files:
        if f.exists():
            d, r = sha256_rows(f)
            inp[f.name] = {"sha256": d, "data_rows": r,
                           "bytes": f.stat().st_size}
        else:
            inp[f.name] = {"error": "missing"}
    return {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "seed": SEED,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "numpy": np.__version__, "pandas": pd.__version__,
        "scikit_learn": sklearn.__version__,
        "inputs": inp,
        "entrypoint": "python 05_Modeling/triage_production.py",
    }


def build(include_scoring=False):
    """Mirror unknown_triage's validated target + BASE feature build."""
    j = ut.load_q0_q4()
    j["lg0"] = j["lead_0"] + j["galv_0"]
    j["lg4"] = j["lead_4"] + j["galv_4"]
    j["resolved"] = (j["unknown_0"] - j["unknown_4"]).clip(lower=0)
    j["d_lg"] = (j["lg4"] - j["lg0"]).clip(lower=0)
    pids = j.index.to_numpy()
    lead_frac0 = (j["lg0"] / j["total_0"].clip(lower=1)).to_numpy(float)
    sr, hb = ut.wholesale_seller_risk(pids, lead_frac0)
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
    for frame in (gt, j):
        frame["lead_frac0_feat"] = (
            frame["lg0"] / frame["total_0"].clip(lower=1)).clip(0, 1)
        frame["pop_0"] = np.log1p(frame["pop_0"])
        frame["total_0"] = np.log1p(frame["total_0"])
        frame["unknown_0"] = np.log1p(frame["unknown_0"])
        for c in CAT_FEATURES:
            frame[c] = frame[c].astype(str).fillna("UNK")
    X = pd.get_dummies(gt[BASE_FEATURES], columns=CAT_FEATURES,
                       dummy_na=False).replace([np.inf, -np.inf], np.nan
                                               ).fillna(0.0).astype(float)
    y = gt["y"].to_numpy()
    state = pd.Index(gt.index).str[:2].to_numpy()
    memb = sc.county_membership(gt.index.to_numpy())
    county = np.array([sorted(memb.get(p, {"NA"}))[0]
                       for p in gt.index], dtype=object)
    if include_scoring:
        rem = j[j["unknown_4"] >= ut.MIN_RESOLVED].copy()
        return X.values, y, state, county, X.columns.tolist(), gt, rem
    return X.values, y, state, county, X.columns.tolist()


def write_production_ranking(model, cols, gt, rem):
    """Score unresolved systems with the final calibrated model.

    This is the operational artifact consumed by aquacascade_system. It
    deliberately uses the same evidence-supported features and fitted final
    model described in triage_production_results.json.
    """
    Xr = pd.get_dummies(rem[BASE_FEATURES], columns=CAT_FEATURES,
                        dummy_na=False)
    Xr = Xr.reindex(columns=cols, fill_value=0.0)
    Xr = Xr.replace([np.inf, -np.inf], np.nan).fillna(0.0).astype(float)
    rem["p_lead_rich"] = model.predict_proba(Xr.values)[:, 1]
    pos_yield = gt.loc[gt["y"] == 1, "lead_yield"].mean()
    rem["exp_hidden_lead"] = (
        rem["p_lead_rich"] * pos_yield * rem["unknown_4"])
    rem["inv_cost"] = ut.COST_INVESTIGATE * rem["unknown_4"]
    rank = rem.assign(rank=np.argsort(np.argsort(
        -(rem["p_lead_rich"].to_numpy()))) + 1).sort_values("rank")
    out = OUT / "triage_production_ranking.csv"
    rank.reset_index()[["pwsid", "p_lead_rich", "exp_hidden_lead",
                        "inv_cost", "rank"]].to_csv(out, index=False)
    return {"file": str(out), "systems_ranked": int(len(rank)),
            "unresolved_unknown_lines": int(rem["unknown_4"].sum())}


def cal_model():
    base = HistGradientBoostingClassifier(
        max_depth=4, learning_rate=0.06, max_iter=400,
        class_weight="balanced", random_state=SEED)
    return CalibratedClassifierCV(base, method="isotonic", cv=3)


def metrics(y, p):
    pos = y.sum()
    order = np.argsort(-p)
    k10 = max(1, len(y) // 10)
    return {
        "roc_auc": float(roc_auc_score(y, p)),
        "pr_auc": float(average_precision_score(y, p)),
        "brier": float(brier_score_loss(y, p)),
        "top_decile_share_of_positives": float(y[order[:k10]].sum() / pos),
        "precision_at_5pct": float(
            y[order[:max(1, len(y) // 20)]].mean()),
        "precision_at_10pct": float(y[order[:k10]].mean()),
    }


def ece(y, p, bins=10):
    edges = np.linspace(0, 1, bins + 1)
    e, rel = 0.0, []
    for i in range(bins):
        m = (p >= edges[i]) & (p < edges[i + 1] if i < bins - 1
                               else p <= edges[i + 1])
        if m.sum():
            conf, acc = p[m].mean(), y[m].mean()
            e += m.mean() * abs(conf - acc)
            rel.append([float(conf), float(acc), int(m.sum())])
    return float(e), rel


def main():
    OUT.mkdir(exist_ok=True)
    CHARTS.mkdir(parents=True, exist_ok=True)
    log("Writing reproducibility manifest (hashing inputs)")
    man = manifest()
    (OUT / "triage_production_manifest.json").write_text(
        json.dumps(man, indent=2))

    log("Building validated target + base features")
    X, y, state, county, cols, gt, rem = build(include_scoring=True)
    prev = y.mean()
    log(f"  {len(y):,} ground-truth systems, {y.sum():,} positives "
        f"({prev:.1%}), {X.shape[1]} features, {len(set(state))} states")

    rng = np.random.RandomState(SEED)

    # ---- (a) RepeatedStratifiedKFold 5x5: per-repeat AUC + pooled OOF ----
    rkf = RepeatedStratifiedKFold(n_splits=5, n_repeats=5,
                                  random_state=SEED)
    oof_sum = np.zeros(len(y))
    oof_cnt = np.zeros(len(y))
    rep_auc = []
    cur, rid = np.zeros(len(y)), 0
    for i, (tr, te) in enumerate(rkf.split(X, y)):
        m = cal_model().fit(X[tr], y[tr])
        pp = m.predict_proba(X[te])[:, 1]
        oof_sum[te] += pp
        oof_cnt[te] += 1
        cur[te] = pp
        if (i + 1) % 5 == 0:                       # one full repeat done
            rep_auc.append(roc_auc_score(y, cur))
            cur = np.zeros(len(y))
            rid += 1
    oof = oof_sum / np.maximum(oof_cnt, 1)
    rep_auc = np.array(rep_auc)
    mA = metrics(y, oof)
    e, rel = ece(y, oof)
    mA["ece"] = e
    mA["roc_auc_per_repeat_mean"] = float(rep_auc.mean())
    mA["roc_auc_per_repeat_std"] = float(rep_auc.std())
    mA["roc_auc_95ci"] = [float(rep_auc.mean() - 1.96 * rep_auc.std()
                                / np.sqrt(len(rep_auc))),
                          float(rep_auc.mean() + 1.96 * rep_auc.std()
                                / np.sqrt(len(rep_auc)))]
    log(f"  (a) repeated CV  ROC-AUC {mA['roc_auc']:.3f} "
        f"(per-repeat {rep_auc.mean():.3f}+/-{rep_auc.std():.3f})  "
        f"PR {mA['pr_auc']:.3f}  Brier {mA['brier']:.3f}  ECE {e:.3f}")

    # ---- (b) GroupKFold by state: generalisation to unseen regions ----
    gkf = GroupKFold(n_splits=5)
    oofg = np.zeros(len(y))
    for tr, te in gkf.split(X, y, groups=state):
        m = cal_model().fit(X[tr], y[tr])
        oofg[te] = m.predict_proba(X[te])[:, 1]
    mB = metrics(y, oofg)
    log(f"  (b) group-by-state CV  ROC-AUC {mB['roc_auc']:.3f}  "
        f"PR {mB['pr_auc']:.3f}  (generalisation stress test)")

    # ---- (c) leakage-safe county-rate ablation (paired, same splits) ----
    rkf2 = RepeatedStratifiedKFold(n_splits=5, n_repeats=5,
                                   random_state=SEED)
    a_auc, c_auc = [], []
    cur_a = np.zeros(len(y))
    cur_c = np.zeros(len(y))
    for i, (tr, te) in enumerate(rkf2.split(X, y)):
        # base
        m = cal_model().fit(X[tr], y[tr])
        cur_a[te] = m.predict_proba(X[te])[:, 1]
        # + leakage-safe county rate (train labels only)
        df = pd.DataFrame({"c": county, "y": y})
        rate = df.iloc[tr].groupby("c")["y"].mean()
        glob = y[tr].mean()
        cr = np.array([rate.get(c, glob) for c in county])[:, None]
        Xc = np.hstack([X, cr])
        mc = cal_model().fit(Xc[tr], y[tr])
        cur_c[te] = mc.predict_proba(Xc[te])[:, 1]
        if (i + 1) % 5 == 0:
            a_auc.append(roc_auc_score(y, cur_a))
            c_auc.append(roc_auc_score(y, cur_c))
            cur_a = np.zeros(len(y))
            cur_c = np.zeros(len(y))
    a_auc, c_auc = np.array(a_auc), np.array(c_auc)
    d = c_auc - a_auc
    abl = {
        "base_roc_auc_mean": float(a_auc.mean()),
        "plus_county_roc_auc_mean": float(c_auc.mean()),
        "delta_mean": float(d.mean()),
        "delta_std": float(d.std()),
        "pct_repeats_county_helps": float((d > 0).mean()),
        "verdict": ("county term helps this target"
                    if d.mean() > 0.005 and (d > 0).mean() >= 0.8
                    else "county term does NOT materially help this "
                         "target (kept out of production model)"),
    }
    log(f"  (c) county ablation  base {a_auc.mean():.3f} -> "
        f"+county {c_auc.mean():.3f}  delta {d.mean():+.4f}"
        f"+/-{d.std():.4f}  -> {abl['verdict']}")

    res = {
        "n_systems": int(len(y)), "positives": int(y.sum()),
        "positive_rate": float(prev), "n_features": int(X.shape[1]),
        "model": "HistGradientBoosting + isotonic calibration (nested CV)",
        "scope": "evidence-supported base features only; rejected ideas "
                 "(trajectory, depth>2 elsewhere, inter-utility cascade) "
                 "deliberately excluded",
        "validation_a_repeated_stratified_cv": mA,
        "validation_b_group_by_state_cv": mB,
        "validation_c_county_rate_ablation": abl,
        "reliability_curve": rel,
        "baseline_pr_auc": float(prev),
    }
    final_model = cal_model().fit(X, y)
    res["ranking_artifact"] = write_production_ranking(
        final_model, cols, gt, rem)
    (OUT / "triage_production_results.json").write_text(
        json.dumps(res, indent=2))
    log("Wrote triage_production_results.json + manifest + "
        "triage_production_ranking.csv")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from sklearn.metrics import precision_recall_curve
        fig, ax = plt.subplots(1, 3, figsize=(15, 4.4), dpi=200)
        rr = np.array(rel)
        ax[0].plot([0, 1], [0, 1], "--", color="#ccc", lw=1)
        ax[0].plot(rr[:, 0], rr[:, 1], "o-", color="#1b6ca8", lw=2)
        ax[0].set_xlabel("Predicted probability")
        ax[0].set_ylabel("Observed frequency")
        ax[0].set_title(f"Calibration (ECE={e:.3f}, "
                        f"Brier={mA['brier']:.3f})")
        ax[0].spines[["top", "right"]].set_visible(False)
        pr, rc, _ = precision_recall_curve(y, oof)
        ax[1].plot(rc, pr, color="#1b6ca8", lw=2,
                   label=f"PR-AUC={mA['pr_auc']:.3f}")
        ax[1].axhline(prev, ls="--", color="#c0392b", lw=1,
                      label=f"baseline {prev:.3f}")
        ax[1].set_xlabel("Recall")
        ax[1].set_ylabel("Precision")
        ax[1].set_title("Precision-Recall (repeated-CV OOF)")
        ax[1].legend(frameon=False, fontsize=8)
        ax[1].spines[["top", "right"]].set_visible(False)
        order = np.argsort(-oof)
        frac = np.arange(1, len(y) + 1) / len(y)
        gain = np.cumsum(y[order]) / y.sum()
        ax[2].plot(frac * 100, gain * 100, color="#1b6ca8", lw=2,
                   label="model")
        ax[2].plot([0, 100], [0, 100], "--", color="#ccc", lw=1,
                   label="random")
        ax[2].set_xlabel("% systems investigated (by model priority)")
        ax[2].set_ylabel("% lead-rich systems found")
        ax[2].set_title(f"Lift: top 10% finds "
                        f"{mA['top_decile_share_of_positives']*100:.0f}%")
        ax[2].legend(frameon=False, fontsize=8, loc="lower right")
        ax[2].spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
        fig.savefig(CHARTS / "triage_production.png", bbox_inches="tight")
        log(f"  figure -> {CHARTS / 'triage_production.png'}")
    except Exception as ex:
        log(f"  (figure skipped: {ex})")


if __name__ == "__main__":
    main()
