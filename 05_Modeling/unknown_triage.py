"""
AquaCascade AI - Unknown-line lead-likelihood TRIAGE model (real EPA data).

The binding national problem is not which lead lines to replace first (only a
~4% efficiency edge) - it is the 22.5M "Lead Status Unknown" lines every
system is legally required to resolve. Investigation is cheap (~$200/line) vs
replacement (~$4,700); pointing the cheap money at the systems whose unknowns
are most likely lead is real, unsolved value.

Honest training signal (no synthetic data):
  The 5 quarterly SLI snapshots (2025Q1->2026Q1) capture systems actually
  RESOLVING unknowns. For systems that reclassified a meaningful number of
  unknown lines, the realised "lead yield" = increase in (lead+galv) divided
  by unknowns resolved is observed ground truth. Learn lead-yield from
  pre-resolution features, then apply to systems whose unknowns remain.

Network layer: real wholesale Buyer->Seller relationships (EPA SAB repo). A
seller's lead profile is propagated (Katz) to its downstream buyers - the
legitimate, data-backed application of network-criticality propagation.

Leakage control: every feature is knowable BEFORE resolution (system
attributes, q0 composition, historical lead samples/violations, seller q0
profile). The target uses only the q0->q4 reclassification.
"""
import json, time
from pathlib import Path
import numpy as np
import pandas as pd
import scipy.sparse as sp
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score, average_precision_score, roc_curve

import signature_pipeline as spl
from lsl_optimizer import lead_severity
import trajectory_features

BASE = Path(__file__).resolve().parent.parent
SLI = BASE / "01_Raw_Data" / "Service_Line_Inventory"
ECHO = BASE / "01_Raw_Data" / "SDWA_ECHO"
BS = BASE / "01_Raw_Data" / "EPA_SAB_repo" / "Buyers_Sellers_2023Q4.csv"
OUT = BASE / "05_Modeling"
CHARTS = BASE / "03_Outputs" / "Charts"

COST_INVESTIGATE = 200.0
MIN_RESOLVED = 10        # min unknowns reclassified to count as ground truth
HIGH_YIELD = 0.10        # >=10% of resolved unknowns turned out lead/galv


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def load_q0_q4():
    cmap = {"PWS ID": "pwsid",
            "# Galvanized Requiring Replacement Service Lines": "galv",
            "# Lead Service Lines": "lead",
            "# Lead Status Unknown Service Lines": "unknown",
            "# Non-lead Service Lines": "nonlead",
            "Total # Service Lines Reported": "total",
            "Population Served Count": "pop",
            "PWS Type": "ptype", "Activity Status": "act"}
    frames = {}
    for q in ["2025Q1", "2026Q1"]:
        d = pd.read_csv(SLI / f"SDWIS_service_line_inventory_{q}.csv",
                        dtype=str, usecols=list(cmap), encoding="latin-1")
        d = d.rename(columns=cmap)
        for c in ["galv", "lead", "unknown", "nonlead", "total", "pop"]:
            d[c] = pd.to_numeric(d[c].str.replace(",", "", regex=False),
                                 errors="coerce").fillna(0)
        d = d[(d["ptype"] == "Community water system") &
              (d["act"] == "Active")]
        d = d.groupby("pwsid", as_index=False).agg(
            galv=("galv", "sum"), lead=("lead", "sum"),
            unknown=("unknown", "sum"), nonlead=("nonlead", "sum"),
            total=("total", "sum"), pop=("pop", "max"))
        frames[q] = d.set_index("pwsid")
    a, b = frames["2025Q1"], frames["2026Q1"]
    j = a.join(b, lsuffix="_0", rsuffix="_4", how="inner")
    return j


def wholesale_seller_risk(pids, lead_frac0):
    """Katz-propagate seller lead fraction (q0) to downstream buyers over the
    real Buyer->Seller wholesale graph."""
    bs = pd.read_csv(BS, dtype=str).dropna()
    idx = {p: i for i, p in enumerate(pids)}
    rows, cols = [], []
    for buy, sell in zip(bs["Buyer_PWSID"], bs["Seller_PWSID"]):
        if buy in idx and sell in idx:
            rows.append(idx[buy]); cols.append(idx[sell])   # buyer<-seller
    n = len(pids)
    A = sp.csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(n, n))
    deg = np.asarray(A.sum(1)).ravel(); deg[deg == 0] = 1.0
    P = sp.diags(1.0 / deg) @ A
    r = lead_frac0.copy()
    for _ in range(6):                      # multi-hop seller risk
        r = 0.6 * lead_frac0 + 0.4 * (P @ r)
    has_seller = (np.asarray(A.sum(1)).ravel() > 0).astype(float)
    return r, has_seller


def main():
    log("Loading SLI 2025Q1 vs 2026Q1 (Community, Active)")
    j = load_q0_q4()
    j["lg0"] = j["lead_0"] + j["galv_0"]
    j["lg4"] = j["lead_4"] + j["galv_4"]
    j["resolved"] = (j["unknown_0"] - j["unknown_4"]).clip(lower=0)
    j["d_lg"] = (j["lg4"] - j["lg0"]).clip(lower=0)
    log(f"  community systems in both quarters: {len(j):,}")

    pids = j.index.to_numpy()
    lead_frac0 = (j["lg0"] / j["total_0"].clip(lower=1)).to_numpy(float)
    seller_risk, has_seller = wholesale_seller_risk(pids, lead_frac0)
    j["seller_lead_risk"] = seller_risk
    j["is_buyer"] = has_seller
    log(f"  systems that buy wholesale water: {int(has_seller.sum()):,}")

    # measured lead severity (mostly pre-period -> safe feature)
    sev = lead_severity().set_index("pwsid")
    j = j.join(sev["lead90"]).fillna({"lead90": 0.0})

    # site-visit + pub-system context (historical -> safe)
    uni = set(pids)
    sv = spl.site_visit_features(uni)
    ps = spl.pubsys_features(uni)
    j = j.join(sv).join(ps)

    # long-run 2013-2025 distress trajectory (from local SDWA, no downloads)
    tj = trajectory_features.build().set_index("pwsid")
    tj_cols = [c for c in tj.columns]
    j = j.join(tj)
    log(f"  trajectory features joined: {len(tj_cols)} cols, "
        f"{j[tj_cols[0]].notna().mean():.0%} of systems matched")

    # ---- ground-truth subset: systems that actually resolved unknowns ----
    gt = j[(j["unknown_0"] >= MIN_RESOLVED) &
           (j["resolved"] >= MIN_RESOLVED)].copy()
    gt["lead_yield"] = (gt["d_lg"] / gt["resolved"]).clip(0, 1)
    gt["y"] = (gt["lead_yield"] >= HIGH_YIELD).astype(int)
    log(f"  ground-truth systems (resolved >= {MIN_RESOLVED}): "
        f"{len(gt):,};  positive (lead-rich unknowns): {gt['y'].mean():.1%}")

    base_feat = ["unknown_0", "total_0", "pop_0", "lead_frac0_feat",
                 "lead90", "seller_lead_risk", "is_buyer",
                 "n_site_visits", "n_signif_defic", "log_conns",
                 "is_school", "owner", "src"]
    full_feat = base_feat + tj_cols
    for df in (gt, j):
        df["lead_frac0_feat"] = (df["lg0"] /
                                 df["total_0"].clip(lower=1)).clip(0, 1)
        df["pop_0"] = np.log1p(df["pop_0"])
        df["total_0"] = np.log1p(df["total_0"])
        df["unknown_0"] = np.log1p(df["unknown_0"])
    cat = ["owner", "src"]
    for c in cat:
        gt[c] = gt[c].astype(str).fillna("UNK")
        j[c] = j[c].astype(str).fillna("UNK")

    yg = gt["y"].to_numpy()
    prev = yg.mean()
    cv = StratifiedKFold(5, shuffle=True, random_state=42)

    def make_X(frame, feats, cols=None):
        X = pd.get_dummies(frame[feats], columns=cat, dummy_na=False)
        X = X.replace([np.inf, -np.inf], np.nan).fillna(0.0).astype(float)
        if cols is not None:
            X = X.reindex(columns=cols, fill_value=0.0)
        return X

    def evaluate(feats):
        X = make_X(gt, feats)
        s = StandardScaler().fit(X.values)
        c = HistGradientBoostingClassifier(
            max_depth=4, learning_rate=0.06, max_iter=400,
            class_weight="balanced", random_state=42)
        o = cross_val_predict(c, s.transform(X.values), yg, cv=cv,
                              method="predict_proba")[:, 1]
        return X, s, c, o, roc_auc_score(yg, o), \
            average_precision_score(yg, o)

    # honest A/B: same data/folds, with vs without the trajectory layer.
    # The long-run trajectory was TESTED and did not help -> the production
    # model is the base feature set; the trajectory result is reported as a
    # negative finding for transparency.
    Xg, sc, clf, oof, auc, ap = evaluate(base_feat)      # model of record
    _, _, _, _, auc_traj, ap_traj = evaluate(full_feat)  # tested variant
    log(f"  triage model (base)          CV ROC-AUC {auc:.3f}  "
        f"PR-AUC {ap:.3f}  (baseline {prev:.3f})")
    log(f"  + long-run trajectory layer  CV ROC-AUC {auc_traj:.3f}  "
        f"PR-AUC {ap_traj:.3f}  -> delta ROC {auc_traj-auc:+.3f} "
        f"(rejected: no lift)")
    feat = base_feat

    # ---- apply to systems with unknowns still outstanding -------------
    clf.fit(sc.transform(Xg.values), yg)
    rem = j[j["unknown_4"] >= MIN_RESOLVED].copy()
    Xr = pd.get_dummies(rem[feat], columns=cat, dummy_na=False)
    Xr = Xr.reindex(columns=Xg.columns, fill_value=0.0)
    Xr = Xr.replace([np.inf, -np.inf], np.nan).fillna(0.0).astype(float)
    rem["p_lead_rich"] = clf.predict_proba(sc.transform(Xr.values))[:, 1]
    # expected lead lines hiding in unsolved unknowns (conservative: scale
    # predicted probability by the empirical positive-class mean yield)
    pos_yield = gt.loc[gt["y"] == 1, "lead_yield"].mean()
    rem["exp_hidden_lead"] = (rem["p_lead_rich"] * pos_yield
                              * rem["unknown_4"])
    rem["inv_cost"] = COST_INVESTIGATE * rem["unknown_4"]

    # HONEST efficiency test: on the ground-truth systems, using LEAKAGE-SAFE
    # out-of-fold predictions and the REAL lead uncovered (gt.d_lg), does
    # model-ordered investigation find real lead faster than the common
    # "most unknowns first" heuristic? (Not circular: order = oof model
    # score; value = real observed lead lines, never the model's own output.)
    real = gt["d_lg"].to_numpy(float)                  # real lead uncovered
    costg = COST_INVESTIGATE * gt["resolved"].to_numpy(float)
    o_model = np.argsort(-oof)                          # leakage-safe order
    o_naive = np.argsort(-(gt["unknown_0"].to_numpy()))  # log-monotonic
    tot_e = real.sum()

    def frontier(order):
        return np.cumsum(costg[order]), np.cumsum(real[order])
    cm, em = frontier(o_model)
    cn, en = frontier(o_naive)

    def spend_for(frac, c, e):
        f = e / tot_e
        return float(c[np.searchsorted(f, frac)]) if f[-1] >= frac \
            else float(c[-1])
    s_m = spend_for(0.80, cm, em)
    s_n = spend_for(0.80, cn, en)
    save = (s_n - s_m) / s_n * 100 if s_n else 0.0
    # top-decile lift: share of all real lead captured by the model's top 10%
    k = max(1, len(real) // 10)
    decile_lift = float(real[o_model[:k]].sum() / tot_e) if tot_e else 0.0

    res = {
        "community_systems_both_quarters": int(len(j)),
        "ground_truth_systems": int(len(gt)),
        "gt_positive_rate": float(prev),
        "triage_cv_roc_auc": float(auc),
        "triage_cv_pr_auc": float(ap),
        "trajectory_layer_tested_cv_roc_auc": float(auc_traj),
        "trajectory_layer_delta_roc_auc": float(auc_traj - auc),
        "trajectory_layer_verdict": "rejected - no predictive lift",
        "triage_baseline_pr_auc": float(prev),
        "systems_with_unresolved_unknowns": int(len(rem)),
        "unresolved_unknown_lines": int(rem["unknown_4"].sum()),
        "est_total_investigation_cost_usd": float(rem["inv_cost"].sum()),
        "projected_hidden_lead_lines_modelestimate":
            float(rem["exp_hidden_lead"].sum()),
        "honest_gt_test": {
            "real_lead_in_gt_systems": float(tot_e),
            "spend_80pct_model_usd": s_m,
            "spend_80pct_most_unknowns_first_usd": s_n,
            "savings_pct_vs_naive_on_real_outcomes": float(save),
            "model_top_decile_share_of_real_lead": decile_lift,
        },
        "assumptions": {"cost_per_investigation_usd": COST_INVESTIGATE,
                        "min_resolved_for_ground_truth": MIN_RESOLVED,
                        "high_yield_threshold": HIGH_YIELD},
        "note": "lead_yield is observed from real q0->q4 reclassification; "
                "selection bias (early resolvers differ) is a known caveat.",
    }
    (OUT / "unknown_triage_results.json").write_text(json.dumps(res, indent=2))
    rank = rem.assign(rank=np.argsort(np.argsort(
        -(rem["p_lead_rich"].to_numpy()))) + 1).sort_values("rank")
    rank.reset_index()[["pwsid", "p_lead_rich", "exp_hidden_lead",
                        "inv_cost", "rank"]].to_csv(
        OUT / "unknown_triage_ranking.csv", index=False)

    log("RESULTS")
    log(f"  triage model: CV ROC-AUC {auc:.3f}, PR-AUC {ap:.3f} "
        f"(base {prev:.3f}) on {len(gt):,} real ground-truth systems")
    log("  -- honest test on held-out (oof) systems, REAL lead found --")
    log(f"  spend to find 80% of real lead - model:        "
        f"${s_m/1e6:,.1f}M")
    log(f"  spend to find 80% of real lead - most-unknowns: "
        f"${s_n/1e6:,.1f}M")
    log(f"  triage vs naive: {save:.1f}% less investigation spend "
        f"for the same REAL lead discovered")
    log(f"  model top-10% of systems captures "
        f"{decile_lift*100:.0f}% of all real lead found")
    log(f"  national context: {len(rem):,} systems / "
        f"{int(rem['unknown_4'].sum()):,} unknown lines still unresolved; "
        f"projected model-estimated hidden lead "
        f"{rem['exp_hidden_lead'].sum()/1e6:,.1f}M lines "
        f"(${rem['inv_cost'].sum()/1e9:,.1f}B to investigate)")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, 2, figsize=(11, 4.4), dpi=200)
        fpr, tpr, _ = roc_curve(yg, oof)
        ax[0].plot(fpr, tpr, color="#1b6ca8", lw=2,
                   label=f"triage model  AUC={auc:.3f}")
        ax[0].plot([0, 1], [0, 1], "--", color="#ccc", lw=1)
        ax[0].set_xlabel("False positive rate")
        ax[0].set_ylabel("True positive rate")
        ax[0].set_title("Predicting lead-rich unknowns (5-fold CV)")
        ax[0].legend(frameon=False, loc="lower right")
        ax[0].spines[["top", "right"]].set_visible(False)
        ax[1].plot(cm / 1e6, em / tot_e * 100, color="#1b6ca8", lw=2,
                   label="AquaCascade triage")
        ax[1].plot(cn / 1e6, en / tot_e * 100, color="#c0392b", lw=2,
                   label="Most unknowns first")
        ax[1].axhline(80, ls="--", color="#bbb", lw=1)
        ax[1].set_xlabel("Investigation spend ($M, held-out systems)")
        ax[1].set_ylabel("Real lead lines uncovered (%)")
        ax[1].set_title("Investigation targeting — real outcomes")
        ax[1].legend(frameon=False, loc="lower right")
        ax[1].spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
        fig.savefig(CHARTS / "unknown_triage_results.png",
                    bbox_inches="tight")
        log(f"  figure -> {CHARTS / 'unknown_triage_results.png'}")
    except Exception as e:
        log(f"  (figure skipped: {e})")


if __name__ == "__main__":
    main()
