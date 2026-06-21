# Report D — Rehabilitation Module: Pre-Build Analysis

Purpose: decide what to build before building it. After lines are
investigated and confirmed in the register tool, the rehabilitation module
turns the confirmed lead + galvanized-requiring-replacement (GRR) lines
into a **multi-year, budget-constrained replacement plan** that meets the
LCRI deadline and the LCRI equity requirement.

The module is a standard multi-period capital-allocation formulation:
criticality × cost, scheduled over multiple periods under a budget,
equity, and deadline constraint.

---

## 1. Where it sits in the pipeline

`Triage model (which systems)` → `Register tool (confirm each line)` →
**`Rehabilitation module (when to replace each confirmed line)`**

It consumes the register's `register.csv` export — the rows whose
`overall_classification` is **Lead** or **Galvanized Requiring
Replacement** are the replacement backlog. Same data contract, same
granularity (per line, within a utility). No new external data.

## 2. What it must do

1. **Ingest** confirmed replacement lines (from the register): line ID,
   PWSID, location, install year, `disadvantaged_community_flag`,
   system/customer split, any per-line cost.
2. **Parametrize** the plan: annual capital budget, LCRI compliance
   horizon (default 10 years), per-line replacement cost (explicit
   assumption), optional mobilization saving for co-located lines.
3. **Schedule** each line into a replacement year to:
   - remove all Lead/GRR lines **before the deadline** (hard constraint);
   - **front-load disadvantaged-community lines** (LCRI legal
     requirement — a constraint, not an optimization nicety);
   - stay within the **annual budget** (hard constraint);
   - prefer **geographic/area clustering** to cut real mobilization cost.
4. **Output**: a year-by-year schedule (which lines, which year), the
   annual cost profile, a **compliance trajectory** (on track for the
   deadline at this budget? if not, the minimum budget that is), and an
   **equity report** (share of disadvantaged lines done early).

## 3. Optimization formulation (honest)

Multi-period budget-constrained scheduling: assign each line *i* a year
*t* in 1..H to **minimize cumulative lead-exposure-years** (a line
contributes exposure every year until replaced), subject to:
Σ cost ≤ budget each year; all lines replaced by H; disadvantaged lines
weighted to earlier years; optional cluster bonus.

Solved as a greedy value-density schedule (exposure-per-dollar with an
equity multiplier and a cluster bonus), with an exact small-LP/ILP option
later if a utility needs provable optimality. Compared honestly against
the naive "oldest/most-exposed first" and "as-found order" baselines.

## 4. Honest scope (lessons already learned on this project)

- We proved earlier that for **pure ordering**, naive heuristics are
  ~96% as good as exposure-optimization (the 62.7% "saving" was an
  artifact; real edge ≈ 4%). **So the rehab module's value is NOT a
  flashy savings number.** Its value is:
  (a) **feasibility & compliance** — will this utility hit the LCRI
  deadline at its budget, and if not, what budget closes the gap;
  (b) **equity scheduling** — the legally-required prioritization of
  disadvantaged communities, made explicit and auditable;
  (c) **mobilization clustering** — a *real* operational saving (digging
  co-located lines together), unlike the exposure-ordering mirage;
  (d) it runs on **confirmed** register data, not predictions.
- Cost figures are explicit, parametrized assumptions — never presented
  as measured savings.

## 5. What we have vs. need

**Have:** register export (confirmed lines + disadvantaged flag +
install year + cost field); the scheduling formulation above; the Census-linked
boundaries (optional later equity enrichment); the honest cost
assumptions already used in `lsl_optimizer.py`.

**Need from the utility (imported, not fabricated):** its annual budget,
and — for clustering — an area/route identifier per line if it wants the
mobilization saving (optional; degrade gracefully without it).

**Build:** the scheduler, the compliance/equity reporting, the budget
"what-if", and the plan export.

## 6. Architecture options

| Option | Pros | Cons |
|---|---|---|
| **A. New page in the existing register app** | One coherent tool: investigate → plan; reuses the data + UI | Heavier app |
| **B. Separate `08_Rehab` script + CSV round-trip** | Clean separation; easy to reproduce/test | Two tools to run |
| **C. Both: scheduler module imported by the app** | Reusable engine, app UI on top | Slightly more wiring |

**Recommendation: C** — a standalone, testable scheduler module that the
register app exposes as a "Rehabilitation plan" page (consistent with the
"one tool, loop closed" story; engine still unit-testable on its own).

## 7. Decisions needed before building

1. **Architecture:** integrate into the register app (a "Rehabilitation
   plan" page backed by a standalone scheduler module) vs. a separate
   script.
2. **Objective emphasis:** minimize lead-exposure-years (health-first)
   vs. simplest "oldest/disadvantaged-first within budget" — recommend
   health-first with the equity constraint, baselines reported honestly.
3. **Clustering in v1?** include the mobilization-saving cluster bonus
   now (needs an area field from the utility) or defer to v2.
