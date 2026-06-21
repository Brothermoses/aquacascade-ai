"""Bridge to the triage model output (system-level priority).

Honest: the model predicts at the SYSTEM level. A line's work-order
priority inherits its system's model rank; it is not a per-line
prediction."""
import csv
from .config import RANKING_CSV

_CACHE = None


def load(force=False):
    global _CACHE
    if _CACHE is not None and not force:
        return _CACHE
    out = {}
    if RANKING_CSV.exists():
        with open(RANKING_CSV, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    out[r["pwsid"]] = (int(float(r["rank"])),
                                       float(r["p_lead_rich"]))
                except (KeyError, ValueError):
                    pass
    _CACHE = out
    return out


def priority(pwsid):
    """(rank, p_lead_rich, label) or (None, None, 'N/A')."""
    d = load()
    if pwsid in d:
        rk, p = d[pwsid]
        return rk, p, ("High" if p >= 0.5 else "Low")
    return None, None, "N/A"


def loaded_count():
    return len(load())
