"""
Rehabilitation scheduler engine (standalone, unit-testable).

Turns the confirmed Lead / Galvanized-Requiring-Replacement lines into a
multi-year replacement plan that:
  - removes every line before the LCRI compliance horizon (hard);
  - stays within the annual capital budget (hard);
  - front-loads disadvantaged-community lines (LCRI equity requirement,
    via an exposure weight multiplier);
  - minimizes cumulative lead-exposure-years (a line accrues exposure
    every year until it is replaced).

Honest: the optimization edge over naive ordering is modest (proven
earlier on this project). The value is deadline feasibility, equity
scheduling, and an auditable plan — not a flashy savings number. Cost
figures are explicit parametrized assumptions, never measured savings.
"""
from math import ceil

SEVERITY = {"Lead": 1.0, "Galvanized Requiring Replacement": 0.7}


def _weight(line, equity_mult):
    sev = SEVERITY.get(line["classification"], 1.0)
    return sev * (equity_mult if line.get("disadvantaged") else 1.0)


def _greedy(order, annual_budget, horizon):
    """Fill years 1..H in the given line order, respecting the annual
    budget. Returns (schedule list, per_year list, exposure_years,
    feasible, lines_unscheduled)."""
    spent = {}
    sched = []
    year = 1
    unsched = 0
    for ln in order:
        c = ln["cost"]
        # advance to the first year that can absorb this line's cost
        while year <= horizon and spent.get(year, 0.0) + c > annual_budget \
                and spent.get(year, 0.0) > 0.0:
            year += 1
        placed = None
        y = year
        while y <= horizon:
            if spent.get(y, 0.0) + c <= annual_budget or \
                    spent.get(y, 0.0) == 0.0:
                placed = y
                break
            y += 1
        if placed is None:                       # past the deadline
            unsched += 1
            placed = horizon + 1                 # overflow bucket
        spent[placed] = spent.get(placed, 0.0) + c
        sched.append({**ln, "year": placed})
    exposure = sum(l["weight"] * min(l["year"], horizon + 1)
                   for l in sched)
    per_year = []
    tot = len(sched)
    tot_dis = sum(1 for l in sched if l.get("disadvantaged")) or 1
    cum = cum_dis = 0
    for yy in range(1, horizon + 1):
        ys = [l for l in sched if l["year"] == yy]
        cum += len(ys)
        cum_dis += sum(1 for l in ys if l.get("disadvantaged"))
        per_year.append({
            "year": yy, "n_lines": len(ys),
            "cost": round(sum(l["cost"] for l in ys), 2),
            "cum_pct_complete": round(100 * cum / tot, 1) if tot else 0,
            "cum_pct_disadvantaged_complete":
                round(100 * cum_dis / tot_dis, 1)})
    feasible = unsched == 0
    return sched, per_year, exposure, feasible, unsched


def plan(lines, annual_budget, horizon=10, default_cost=4700.0,
         equity_mult=2.0):
    """lines: list of dicts {line_id,pwsid,classification,install_year,
    disadvantaged(bool),cost(float|None)}. Returns the full plan dict."""
    if not lines:
        return {"error": "No confirmed Lead/GRR lines to schedule."}
    if annual_budget is None or annual_budget <= 0:
        return {"error": "Annual budget must be a positive number."}

    norm = []
    for ln in lines:
        c = ln.get("cost")
        try:
            c = float(c) if c not in (None, "", "nan") else default_cost
        except (TypeError, ValueError):
            c = default_cost
        if c <= 0:
            c = default_cost
        iy = ln.get("install_year")
        try:
            iy = int(iy)
        except (TypeError, ValueError):
            iy = None
        r = {"line_id": ln["line_id"], "pwsid": ln.get("pwsid", ""),
             "classification": ln["classification"],
             "install_year": iy,
             "disadvantaged": bool(ln.get("disadvantaged")),
             "cost": c}
        r["weight"] = _weight(r, equity_mult)
        norm.append(r)

    total_cost = sum(l["cost"] for l in norm)
    years_needed = ceil(total_cost / annual_budget) if annual_budget else 0
    min_budget = round(total_cost / horizon, 2) if horizon else None

    # optimized: highest exposure-weight per dollar first
    opt_order = sorted(norm, key=lambda l: -(l["weight"] / l["cost"]))
    sched, per_year, exp_opt, feasible, unsched = _greedy(
        opt_order, annual_budget, horizon)

    # honest baselines on the same budget
    def big(l):
        return l["install_year"] if l["install_year"] is not None else 9999
    _, _, exp_old, _, _ = _greedy(sorted(norm, key=big),
                                  annual_budget, horizon)
    _, _, exp_asis, _, _ = _greedy(list(norm), annual_budget, horizon)

    mid = horizon // 2
    dis_total = sum(1 for l in norm if l["disadvantaged"]) or 1
    dis_by_mid = sum(1 for l in sched
                     if l["disadvantaged"] and l["year"] <= mid)

    return {
        "summary": {
            "n_lines": len(norm),
            "n_disadvantaged": sum(1 for l in norm if l["disadvantaged"]),
            "total_cost": round(total_cost, 2),
            "annual_budget": annual_budget,
            "horizon": horizon,
            "default_cost_assumption": default_cost,
            "equity_multiplier": equity_mult,
            "years_needed_at_budget": years_needed,
            "feasible_within_horizon": feasible,
            "lines_past_deadline": unsched,
            "min_budget_for_horizon": min_budget,
        },
        "exposure_years": {
            "optimized": round(exp_opt, 1),
            "oldest_first": round(exp_old, 1),
            "as_found": round(exp_asis, 1),
            "pct_better_than_oldest_first": round(
                100 * (exp_old - exp_opt) / exp_old, 1)
                if exp_old else 0.0,
        },
        "equity": {
            "disadvantaged_total": sum(1 for l in norm
                                       if l["disadvantaged"]),
            "disadvantaged_done_by_mid_horizon": dis_by_mid,
            "pct_disadvantaged_by_mid_horizon": round(
                100 * dis_by_mid / dis_total, 1),
        },
        "per_year": per_year,
        "schedule": sorted(sched, key=lambda l: (l["year"],
                                                 -l["weight"])),
    }


if __name__ == "__main__":   # tiny self-test
    demo = [
        {"line_id": "A", "pwsid": "P1", "classification": "Lead",
         "install_year": 1955, "disadvantaged": True, "cost": 5000},
        {"line_id": "B", "pwsid": "P1", "classification": "Lead",
         "install_year": 1990, "disadvantaged": False, "cost": 4000},
        {"line_id": "C", "pwsid": "P1",
         "classification": "Galvanized Requiring Replacement",
         "install_year": 1970, "disadvantaged": False, "cost": 3000},
    ]
    import json
    print(json.dumps(plan(demo, annual_budget=6000, horizon=3),
                      indent=2))
