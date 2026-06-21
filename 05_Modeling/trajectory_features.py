"""
Long-run per-system distress trajectory (2013-2025) from SDWA files already
on disk - no downloads, no quarterly gaps. Equivalent to (and finer than)
the Water System Summary rollup.

Sources:
  SDWA_VIOLATIONS_ENFORCEMENT.csv : violations by year (NON_COMPL begin date)
  SDWA_SITE_VISITS.csv            : site visits + significant deficiencies/yr

Outputs a per-PWSID feature frame and caches it. Derived features include a
truncated path-signature of the standardized annual [violations, visits]
trajectory - consistent with the project's signature methodology.
"""
import time
from pathlib import Path
import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
ECHO = BASE / "01_Raw_Data" / "SDWA_ECHO"
CACHE = BASE / "05_Modeling" / "cache_trajectory.parquet"
Y0, Y1 = 2013, 2025
YEARS = list(range(Y0, Y1 + 1))


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def _yearbin(s):
    y = pd.to_numeric(s.str[-4:], errors="coerce")
    return y.clip(Y0, Y1)


def _sig2(path):
    """Level 1+2 truncated signature of an (n, T, d) array."""
    n, T, d = path.shape
    s1 = np.zeros((n, d))
    s2 = np.zeros((n, d, d))
    for k in range(T - 1):
        dx = path[:, k + 1, :] - path[:, k, :]
        s2 += s1[:, :, None] * dx[:, None, :] + 0.5 * dx[:, :, None] * dx[:, None, :]
        s1 += dx
    return np.concatenate([s1, s2.reshape(n, d * d)], axis=1)


def build(force=False):
    if CACHE.exists() and not force:
        log(f"trajectory: loading cache {CACHE.name}")
        return pd.read_parquet(CACHE)

    log("trajectory: scanning SDWA_VIOLATIONS_ENFORCEMENT.csv (~4 GB)")
    viol = {}
    rows = 0
    for ch in pd.read_csv(ECHO / "SDWA_VIOLATIONS_ENFORCEMENT.csv",
                          usecols=["PWSID", "VIOLATION_ID",
                                   "NON_COMPL_PER_BEGIN_DATE"],
                          dtype=str, chunksize=600_000, encoding="latin-1"):
        rows += len(ch)
        ch = ch.drop_duplicates(["PWSID", "VIOLATION_ID"])
        ch["yr"] = _yearbin(ch["NON_COMPL_PER_BEGIN_DATE"])
        ch = ch.dropna(subset=["yr"])
        g = ch.groupby(["PWSID", "yr"]).size()
        for (pid, yr), c in g.items():
            a = viol.setdefault(pid, np.zeros(len(YEARS)))
            a[int(yr) - Y0] += c
    log(f"  violation rows scanned: {rows:,}; systems: {len(viol):,}")

    log("trajectory: scanning SDWA_SITE_VISITS.csv")
    evalc = ["MANAGEMENT_OPS_EVAL_CODE", "SOURCE_WATER_EVAL_CODE",
             "TREATMENT_EVAL_CODE", "DISTRIBUTION_EVAL_CODE",
             "FINISHED_WATER_STOR_EVAL_CODE", "PUMPS_EVAL_CODE"]
    sv, svS = {}, {}
    for ch in pd.read_csv(ECHO / "SDWA_SITE_VISITS.csv",
                          usecols=["PWSID", "VISIT_ID", "VISIT_DATE"] + evalc,
                          dtype=str, chunksize=400_000, encoding="latin-1"):
        ch["yr"] = _yearbin(ch["VISIT_DATE"])
        ch = ch.dropna(subset=["yr"])
        sig = (ch[evalc] == "S").any(axis=1)
        for (pid, yr), c in ch.groupby(["PWSID", "yr"])["VISIT_ID"].count(
        ).items():
            a = sv.setdefault(pid, np.zeros(len(YEARS)))
            a[int(yr) - Y0] += c
        for (pid, yr), c in ch[sig].groupby(["PWSID", "yr"])["VISIT_ID"
                                                             ].count().items():
            a = svS.setdefault(pid, np.zeros(len(YEARS)))
            a[int(yr) - Y0] += c

    pids = sorted(set(viol) | set(sv))
    V = np.array([viol.get(p, np.zeros(len(YEARS))) for p in pids])
    S = np.array([sv.get(p, np.zeros(len(YEARS))) for p in pids])
    SS = np.array([svS.get(p, np.zeros(len(YEARS))) for p in pids])

    df = pd.DataFrame(index=pd.Index(pids, name="pwsid"))
    df["tj_viol_total"] = V.sum(1)
    df["tj_viol_last3"] = V[:, -3:].sum(1)
    df["tj_viol_prev"] = V[:, :-3].sum(1)
    df["tj_viol_trend"] = V[:, -3:].mean(1) - V[:, :-3].mean(1)
    df["tj_sv_total"] = S.sum(1)
    df["tj_signif_total"] = SS.sum(1)
    df["tj_signif_last3"] = SS[:, -3:].sum(1)
    df["tj_active_years"] = (V > 0).sum(1)

    # path signature of the standardized annual [viol, visits] trajectory
    P = np.stack([V, S], axis=2).astype(float)
    mu = P.reshape(-1, 2).mean(0)
    sd = P.reshape(-1, 2).std(0) + 1e-9
    P = (P - mu) / sd
    tcol = (np.arange(len(YEARS)) / (len(YEARS) - 1))[None, :, None]
    P = np.concatenate([np.repeat(tcol, len(pids), 0), P], axis=2)
    sig = _sig2(P)
    for i in range(sig.shape[1]):
        df[f"tj_sig_{i}"] = sig[:, i]

    df = df.reset_index()
    df.to_parquet(CACHE, index=False)
    log(f"trajectory: cached {CACHE.name} ({len(df):,} systems, "
        f"{df.shape[1]-1} features)")
    return df


if __name__ == "__main__":
    d = build(force=True)
    print(d.head().to_string())
    print("shape:", d.shape)
