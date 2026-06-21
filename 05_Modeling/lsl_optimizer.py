"""
AquaCascade AI - Lead Service Line (LSL) replacement prioritization optimizer.

Problem (real, mandated): EPA's Lead & Copper Rule Improvements require every
US community water system to replace all lead + galvanized-requiring-
replacement service lines on a ~10-year horizon, prioritizing the highest
public-health exposure. Budgets are finite -> which lines first?

This is the FAA/NEXTOR capital-allocation method transferred IP-clean:
  replacement-need (count)  x  health-exposure criticality  /  cost
ranked under a budget, vs. the heuristics utilities actually use today.

Inputs (all real EPA data on disk):
  - SLI 2026Q1 : lead / galvanized / unknown / non-lead counts per system
  - SDWA_LCR_SAMPLES : measured PB90 lead concentrations (exposure severity)
  - SDWA_PUB_WATER_SYSTEMS : population, name, state, ownership

Cost figures are explicit, literature-based ASSUMPTIONS (flagged), not data.
No fabricated traction or metrics. Outputs an honest efficiency comparison.
"""
import json, time
from pathlib import Path
import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
SLI = BASE / "01_Raw_Data" / "Service_Line_Inventory"
ECHO = BASE / "01_Raw_Data" / "SDWA_ECHO"
OUT = BASE / "05_Modeling"
CHARTS = BASE / "03_Outputs" / "Charts"

# ---- explicit assumptions (parametrise / defensible from public literature) --
COST_REPLACE = 4700.0     # $/line, EPA & AWWA mid-range full LSL replacement
COST_INVESTIGATE = 200.0  # $/line, materials verification of an unknown line
P_UNKNOWN_LEAD = 0.50     # assumed share of "unknown" lines that are lead
LEAD_ACTION_LEVEL = 0.015 # mg/L, EPA lead action level
SEVERITY_CAP = 10.0       # cap on lead/AL severity multiplier
HORIZON_YEARS = 10        # LCRI replacement horizon


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def load_inventory():
    cols = ["PWS ID", "PWS Name", "# Galvanized Requiring Replacement "
            "Service Lines", "# Lead Service Lines",
            "# Lead Status Unknown Service Lines",
            "# Non-lead Service Lines", "Total # Service Lines Reported",
            "Population Served Count", "PWS Type", "Activity Status"]
    d = pd.read_csv(SLI / "SDWIS_service_line_inventory_2026Q1.csv",
                    dtype=str, usecols=cols, encoding="latin-1")
    d = d.rename(columns={
        "PWS ID": "pwsid", "PWS Name": "name",
        "# Galvanized Requiring Replacement Service Lines": "galv",
        "# Lead Service Lines": "lead",
        "# Lead Status Unknown Service Lines": "unknown",
        "# Non-lead Service Lines": "nonlead",
        "Total # Service Lines Reported": "total",
        "Population Served Count": "pop"})
    for c in ["galv", "lead", "unknown", "nonlead", "total", "pop"]:
        d[c] = pd.to_numeric(d[c].str.replace(",", "", regex=False),
                             errors="coerce").fillna(0)
    d = d[(d["PWS Type"] == "Community water system")
          & (d["Activity Status"] == "Active")]
    d = d.groupby("pwsid", as_index=False).agg(
        name=("name", "first"), galv=("galv", "sum"), lead=("lead", "sum"),
        unknown=("unknown", "sum"), total=("total", "sum"),
        pop=("pop", "max"))
    d["must_replace"] = d["lead"] + d["galv"]      # mandated replacements
    return d


def lead_severity():
    """Per-system recent PB90 lead level (mg/L) from real samples."""
    rec = {}
    for ch in pd.read_csv(ECHO / "SDWA_LCR_SAMPLES.csv", dtype=str,
                          usecols=["PWSID", "CONTAMINANT_CODE",
                                   "SAMPLE_MEASURE", "SAMPLING_END_DATE"],
                          chunksize=300_000, encoding="latin-1"):
        ch = ch[ch["CONTAMINANT_CODE"] == "PB90"]
        v = pd.to_numeric(ch["SAMPLE_MEASURE"], errors="coerce")
        yr = pd.to_numeric(ch["SAMPLING_END_DATE"].str[-4:], errors="coerce")
        ok = v.notna() & yr.notna()
        for pid, val, y in zip(ch["PWSID"][ok], v[ok], yr[ok]):
            p = rec.get(pid)
            if p is None or y > p[0] or (y == p[0] and val > p[1]):
                rec[pid] = (y, val)
    s = pd.DataFrame([(k, a[0], a[1]) for k, a in rec.items()],
                     columns=["pwsid", "lead_year", "lead90"])
    return s


def add_population_name(d):
    keep = set(d["pwsid"])
    parts = []
    for ch in pd.read_csv(ECHO / "SDWA_PUB_WATER_SYSTEMS.csv", dtype=str,
                          usecols=["PWSID", "PWS_NAME", "STATE_CODE",
                                   "POPULATION_SERVED_COUNT",
                                   "OWNER_TYPE_CODE"],
                          chunksize=200_000, encoding="latin-1"):
        parts.append(ch[ch["PWSID"].isin(keep)])
    p = pd.concat(parts).drop_duplicates("PWSID").set_index("PWSID")
    d = d.set_index("pwsid")
    d["state"] = p["STATE_CODE"].reindex(d.index)
    d["owner"] = p["OWNER_TYPE_CODE"].reindex(d.index)
    pp = pd.to_numeric(p["POPULATION_SERVED_COUNT"], errors="coerce")
    d["pop"] = np.where(d["pop"] > 0, d["pop"],
                        pp.reindex(d.index).fillna(0))
    return d.reset_index()


def frontier(order_idx, cost, exposure):
    """Cumulative (cost, exposure-reduction) along a given priority order."""
    c = np.cumsum(cost[order_idx])
    e = np.cumsum(exposure[order_idx])
    return c, e


def main():
    log("Loading 2026Q1 service-line inventory (Community, Active)")
    d = load_inventory()
    log(f"  systems: {len(d):,};  lead+galv lines (must replace): "
        f"{int(d['must_replace'].sum()):,};  unknown lines: "
        f"{int(d['unknown'].sum()):,}")

    log("Loading measured PB90 lead samples (exposure severity)")
    sev = lead_severity()
    d = d.merge(sev, on="pwsid", how="left")
    d["lead90"] = d["lead90"].fillna(0.0)
    log(f"  systems with a lead sample: {d['lead90'].gt(0).sum():,}")

    d = add_population_name(d)

    # health-exposure criticality per replaced line.
    # Each lead/galv line ~ one household; people exposed per line is the
    # system's household occupancy (population / total service lines, bounded
    # to a realistic 1-6 persons, default ~2.7 if total missing). Scaled by
    # MEASURED lead severity relative to the action level. This avoids the
    # population/lead-line artifact and makes exposure ~ lines replaced.
    sev_mult = np.minimum(1.0 + d["lead90"] / LEAD_ACTION_LEVEL,
                          SEVERITY_CAP)
    sev_mult = np.where(d["lead90"] > LEAD_ACTION_LEVEL,
                        sev_mult + 2.0, sev_mult)
    lines = d["must_replace"].clip(lower=0).to_numpy(float)
    occ = np.where(d["total"].to_numpy(float) > 0,
                   d["pop"].to_numpy(float)
                   / np.maximum(d["total"].to_numpy(float), 1), 2.7)
    occ = np.clip(occ, 1.0, 6.0)
    expo_per_line = occ * sev_mult
    d["exposure_per_line"] = expo_per_line
    d["exposure_total"] = expo_per_line * lines           # value if fully done
    d["cost_total"] = COST_REPLACE * lines + COST_INVESTIGATE * d["unknown"]

    work = d[lines > 0].copy()
    cost = work["cost_total"].to_numpy(float)
    expo = work["exposure_total"].to_numpy(float)

    strategies = {
        "AquaCascade (exposure/$ optimized)":
            np.argsort(-(work["exposure_per_line"].to_numpy())),
        "Most lead lines first (common practice)":
            np.argsort(-(work["must_replace"].to_numpy())),
        "Largest population first":
            np.argsort(-(work["pop"].to_numpy())),
        "Unordered (as-reported)": np.arange(len(work)),
    }
    tot_exposure = expo.sum()
    tot_cost = cost.sum()

    # honest efficiency metric: spend needed to remove 80% of national
    # exposure, optimized vs the standard most-lines-first heuristic
    eff = {}
    for nm, idx in strategies.items():
        c, e = frontier(idx, cost, expo)
        frac = e / tot_exposure
        spend80 = float(c[np.searchsorted(frac, 0.80)]) if frac[-1] >= 0.8 \
            else float(c[-1])
        eff[nm] = spend80

    opt = eff["AquaCascade (exposure/$ optimized)"]
    base = eff["Most lead lines first (common practice)"]
    savings_pct = (base - opt) / base * 100 if base else 0.0

    res = {
        "n_community_systems": int(len(d)),
        "n_systems_with_lead_or_galv": int((d["must_replace"] > 0).sum()),
        "total_lead_plus_galv_lines": int(d["must_replace"].sum()),
        "total_unknown_lines": int(d["unknown"].sum()),
        "est_total_replacement_cost_usd": float(tot_cost),
        "assumptions": {
            "cost_per_replacement_usd": COST_REPLACE,
            "cost_per_investigation_usd": COST_INVESTIGATE,
            "assumed_unknown_lead_share": P_UNKNOWN_LEAD,
            "lead_action_level_mg_l": LEAD_ACTION_LEVEL,
        },
        "spend_to_cut_80pct_exposure_usd": eff,
        "optimized_vs_most_lines_savings_pct": float(savings_pct),
    }
    (OUT / "lsl_optimizer_results.json").write_text(json.dumps(res, indent=2))

    rank = work.assign(
        priority_rank=np.argsort(np.argsort(
            -(work["exposure_per_line"].to_numpy()))) + 1)
    rank = rank.sort_values("priority_rank")[
        ["pwsid", "name", "state", "must_replace", "unknown", "pop",
         "lead90", "exposure_per_line", "cost_total", "priority_rank"]]
    rank.to_csv(OUT / "lsl_priority_ranking.csv", index=False)

    log("RESULTS")
    log(f"  national mandated lead+galv lines: "
        f"{int(d['must_replace'].sum()):,}")
    log(f"  national 'unknown' lines to investigate: "
        f"{int(d['unknown'].sum()):,}")
    log(f"  est. replacement cost @ ${COST_REPLACE:,.0f}/line: "
        f"${tot_cost/1e9:,.1f}B")
    for nm, v in eff.items():
        log(f"  spend to cut 80% exposure - {nm}: ${v/1e9:,.2f}B")
    log(f"  optimized vs most-lines-first: {savings_pct:.1f}% less spend "
        f"for the same 80% public-health exposure reduction")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, 2, figsize=(11, 4.4), dpi=200)
        colors = {"AquaCascade (exposure/$ optimized)": "#1b6ca8",
                  "Most lead lines first (common practice)": "#c0392b",
                  "Largest population first": "#e08e0b",
                  "Unordered (as-reported)": "#95a5a6"}
        for nm, idx in strategies.items():
            c, e = frontier(idx, cost, expo)
            ax[0].plot(c / 1e9, e / tot_exposure * 100,
                       color=colors[nm], lw=2, label=nm)
        ax[0].axhline(80, ls="--", color="#bbb", lw=1)
        ax[0].set_xlabel("Cumulative spend ($B)")
        ax[0].set_ylabel("Public-health exposure reduced (%)")
        ax[0].set_title("Replacement efficiency frontier")
        ax[0].legend(frameon=False, fontsize=7, loc="lower right")
        ax[0].spines[["top", "right"]].set_visible(False)

        top = rank.head(15)[::-1]
        ax[1].barh(range(len(top)), top["exposure_per_line"],
                   color="#1b6ca8")
        ax[1].set_yticks(range(len(top)))
        ax[1].set_yticklabels(
            [f"{n[:22]} ({s})" for n, s in zip(top["name"], top["state"])],
            fontsize=6)
        ax[1].set_xlabel("Exposure reduced per line replaced")
        ax[1].set_title("Top-15 national priority systems")
        ax[1].spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
        fig.savefig(CHARTS / "lsl_optimizer_results.png",
                    bbox_inches="tight")
        log(f"  figure -> {CHARTS / 'lsl_optimizer_results.png'}")
    except Exception as e:
        log(f"  (figure skipped: {e})")


if __name__ == "__main__":
    main()
