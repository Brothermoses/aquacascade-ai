"""
Fact-based stochastic-nature diagnostics of the per-system WSS trajectories.

Questions:
  (1) Mean-reverting vs pure Brownian (random walk)?
  (2) Non-anticipative (adapted)?

For each channel and across all systems we estimate, per system, on the
2013Q1-2021Q4 series (>=12 real quarters, ffill/bfill as in the model):
  - AR(1) phi  : level series   (phi~1 unit-root/random-walk; phi<<1 mean-rev)
  - AR(1) phi  : increments     (negative -> mean-reverting increments)
  - VR(4)      : Lo-MacKinlay variance ratio of increments
                 (~1 random walk; <1 mean reversion; >1 trend/persistence)
  - Hurst      : aggregated-variance exponent of the level series
                 (0.5 Brownian; <0.5 anti-persistent/mean-rev; >0.5 trend)
Reported as population distributions (medians + fractions). No assumptions.
"""
import json, time
import numpy as np
import pandas as pd
import signature_pipeline as spl

OUT, CHARTS = spl.OUT, spl.CHARTS


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def load_raw():
    cols = ["PWS ID", "PWS Type", "Population Served Count",
            "# of Facilities", "# of Site Visits"]
    fr = []
    for qi, q in enumerate(spl.PRED_QUARTERS):
        d = pd.read_csv(spl.WSS / f"Water System Summary {q}.csv",
                        dtype=str, usecols=cols, encoding="latin-1")
        d = d[d["PWS Type"] == "Community water system"]
        d = d.rename(columns={"PWS ID": "pwsid"})
        d["logpop"] = np.log1p(spl._num(d["Population Served Count"])
                               .clip(lower=0))
        d["nfac"] = spl._num(d["# of Facilities"])
        d["cumsv"] = spl._num(d["# of Site Visits"])
        d = d[["pwsid", "logpop", "nfac", "cumsv"]].drop_duplicates("pwsid")
        d["qidx"] = qi
        fr.append(d)
    allq = pd.concat(fr, ignore_index=True)
    nq = allq.groupby("pwsid")["qidx"].nunique()
    keep = nq[nq >= spl.MIN_QUARTERS].index
    allq = allq[allq["pwsid"].isin(keep)]
    T = len(spl.PRED_QUARTERS)
    out = {}
    for ch in ["logpop", "nfac", "cumsv"]:
        piv = (allq.pivot_table(index="pwsid", columns="qidx", values=ch)
               .reindex(columns=range(T)).ffill(axis=1).bfill(axis=1))
        out[ch] = piv.to_numpy(float)
    return out


def ar1(M):
    """Per-row AR(1) phi via OLS x_t ~ x_{t-1}. Constant series -> nan."""
    x0, x1 = M[:, :-1], M[:, 1:]
    x0m = x0 - x0.mean(1, keepdims=True)
    x1m = x1 - x1.mean(1, keepdims=True)
    den = (x0m ** 2).sum(1)
    phi = np.where(den > 1e-12, (x0m * x1m).sum(1) / np.where(den > 1e-12,
                                                              den, 1), np.nan)
    return phi


def var_ratio(M, q=4):
    d1 = np.diff(M, axis=1)
    dq = M[:, q:] - M[:, :-q]
    v1 = d1.var(1)
    vq = dq.var(1)
    return np.where(v1 > 1e-12, vq / (q * np.where(v1 > 1e-12, v1, 1)),
                    np.nan)


def hurst(M):
    """Aggregated-variance Hurst on each row (level series)."""
    n, T = M.shape
    scales = [1, 2, 3, 4, 6]
    H = np.full(n, np.nan)
    for i in range(n):
        x = M[i]
        if np.nanstd(x) < 1e-9:
            continue
        lv, ls = [], []
        for m in scales:
            k = T // m
            if k < 2:
                break
            blocks = x[:k * m].reshape(k, m).mean(1)
            v = blocks.var()
            if v > 1e-12:
                lv.append(np.log(v))
                ls.append(np.log(m))
        if len(lv) >= 3:
            slope = np.polyfit(ls, lv, 1)[0]   # var ~ m^(2H-2)
            H[i] = 1.0 + slope / 2.0
    return H


def summarize(name, phi, phid, vr, H):
    def med(a):
        return float(np.nanmedian(a))

    def frac(a, lo=None, hi=None):
        a = a[~np.isnan(a)]
        if lo is not None:
            return float((a < lo).mean())
        return float((a > hi).mean())
    return {
        "channel": name,
        "n": int(np.isfinite(phi).sum()),
        "AR1_phi_level_median": med(phi),
        "pct_phi_below_0.9 (mean-revert-ish)": frac(phi, lo=0.9),
        "pct_phi_above_0.98 (unit-root/RW-ish)": frac(phi, hi=0.98),
        "AR1_phi_increments_median": med(phid),
        "pct_incr_phi_negative (mean-rev incr)": frac(phid, lo=0.0),
        "VR4_increments_median": med(vr),
        "pct_VR4_below_0.8 (mean-revert)": frac(vr, lo=0.8),
        "pct_VR4_above_1.2 (trend/persist)": frac(vr, hi=1.2),
        "Hurst_level_median": med(H),
        "pct_Hurst_above_0.55 (trending)": frac(H, hi=0.55),
        "pct_Hurst_below_0.45 (mean-revert)": frac(H, lo=0.45),
    }


def main():
    log("Loading raw WSS 2013-2021 series per system")
    raw = load_raw()
    res = {"note": "cumsv & (excluded) violations are cumulative monotone "
                   "counters -> integrated by construction; mean-reversion "
                   "is only well-posed on their increments."}
    rows = {}
    for ch, M in raw.items():
        phi = ar1(M)
        d = np.diff(M, axis=1)
        phid = ar1(d)
        vr = var_ratio(M)
        H = hurst(M)
        rows[ch] = summarize(ch, phi, phid, vr, H)
        s = rows[ch]
        log(f"  {ch:7s} n={s['n']:,}  phi_level~{s['AR1_phi_level_median']:.3f}"
            f"  phi_incr~{s['AR1_phi_increments_median']:+.3f}"
            f"  VR4~{s['VR4_increments_median']:.2f}"
            f"  Hurst~{s['Hurst_level_median']:.2f}")
    res["channels"] = rows
    (OUT / "process_diagnostics.json").write_text(json.dumps(res, indent=2))

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, 2, figsize=(11, 4.3), dpi=200)
        for ch, M in raw.items():
            ax[0].hist(ar1(M)[np.isfinite(ar1(M))], bins=40, alpha=0.5,
                       label=ch, density=True)
            Hv = hurst(M)
            ax[1].hist(Hv[np.isfinite(Hv)], bins=40, alpha=0.5,
                       label=ch, density=True)
        ax[0].axvline(1.0, ls="--", c="#c0392b", lw=1,
                      label="phi=1 (random walk)")
        ax[0].set_xlabel("AR(1) phi of level series")
        ax[0].set_ylabel("density")
        ax[0].set_title("Mean reversion vs unit root")
        ax[0].legend(frameon=False, fontsize=7)
        ax[0].spines[["top", "right"]].set_visible(False)
        ax[1].axvline(0.5, ls="--", c="#c0392b", lw=1,
                      label="H=0.5 (Brownian)")
        ax[1].set_xlabel("Hurst exponent (level)")
        ax[1].set_title("Brownian (0.5) vs trending (>0.5)")
        ax[1].legend(frameon=False, fontsize=7)
        ax[1].spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
        fig.savefig(CHARTS / "process_diagnostics.png", bbox_inches="tight")
        log(f"  figure -> {CHARTS / 'process_diagnostics.png'}")
    except Exception as e:
        log(f"  (figure skipped: {e})")


if __name__ == "__main__":
    main()
