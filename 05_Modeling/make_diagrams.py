"""Generate clean diagrams (no re-running of experiments needed):
  - method_flow.png   : the selected-method pipeline, step by step
  - experiment_map.png : every method tested, colour-coded by decision
Publication-clean, matched to the deck palette."""
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

CH = Path(__file__).resolve().parent.parent / "03_Outputs" / "Charts"
NIGHT, DEEP, TEAL = "#21295C", "#065A82", "#1C7293"
INK, LIGHT, RED, GREEN = "#1E293B", "#EAF0F6", "#B23B3B", "#2E7D52"


def box(ax, x, y, w, h, title, sub, fc, tc="#FFFFFF"):
    ax.add_patch(FancyBboxPatch((x, y), w, h,
                 boxstyle="round,pad=0.02,rounding_size=0.04",
                 linewidth=0, facecolor=fc))
    ax.text(x + w / 2, y + h * 0.66, title, ha="center", va="center",
            fontsize=11, fontweight="bold", color=tc)
    ax.text(x + w / 2, y + h * 0.30, sub, ha="center", va="center",
            fontsize=8.2, color=tc, wrap=True)


def arrow(ax, x1, y1, x2, y2):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2),
                 arrowstyle="-|>", mutation_scale=14,
                 lw=1.6, color=TEAL))


# ---------- 1. METHOD FLOW ----------
fig, ax = plt.subplots(figsize=(12, 4.6), dpi=200)
ax.set_xlim(0, 12)
ax.set_ylim(0, 4.6)
ax.axis("off")
fig.suptitle("Selected method — unknown-line lead triage (every step)",
             fontsize=13, fontweight="bold", color=NIGHT, y=0.99)
steps = [
    ("Inputs", "7 EPA files\nSHA-256 hashed", DEEP),
    ("Ground truth", "1,935 systems\nreal reclassification", DEEP),
    ("Features", "19 evidence-\nsupported", DEEP),
    ("Model", "GBM + isotonic\ncalibration", TEAL),
    ("Validate", "3 schemes\n(see below)", TEAL),
    ("Outputs", "ranked list +\nmanifest", NIGHT),
]
n = len(steps)
w, h, gap = 1.72, 1.5, 0.30
x0 = (12 - (n * w + (n - 1) * gap)) / 2
yT = 2.55
for i, (t, sname, c) in enumerate(steps):
    x = x0 + i * (w + gap)
    box(ax, x, yT, w, h, t, sname, c)
    if i:
        arrow(ax, x - gap - 0.02, yT + h / 2, x + 0.02, yT + h / 2)
# validation detail row
vd = [("A · Repeated CV", "ROC 0.71 · ECE 0.024 · top-10% ≈ 33%", GREEN),
      ("B · Group-by-state", "ROC 0.57 — state-bound (stated openly)", RED),
      ("C · County ablation", "−0.019 → spatial term excluded", RED)]
vw, vh = 3.5, 0.95
vx0 = (12 - (3 * vw + 2 * 0.35)) / 2
for i, (t, sname, c) in enumerate(vd):
    x = vx0 + i * (vw + 0.35)
    ax.add_patch(FancyBboxPatch((x, 0.45), vw, vh,
                 boxstyle="round,pad=0.02,rounding_size=0.04",
                 linewidth=1.1, edgecolor=c, facecolor="#FFFFFF"))
    ax.text(x + vw / 2, 0.45 + vh * 0.66, t, ha="center", va="center",
            fontsize=9.5, fontweight="bold", color=c)
    ax.text(x + vw / 2, 0.45 + vh * 0.28, sname, ha="center",
            va="center", fontsize=8.2, color=INK)
arrow(ax, x0 + 4 * (w + gap) + w / 2, yT, vx0 + 1.5 * vw + 0.35, 1.4)
fig.savefig(CH / "method_flow.png", bbox_inches="tight")
plt.close(fig)

# ---------- 2. EXPERIMENT MAP ----------
fig, ax = plt.subplots(figsize=(12, 5.2), dpi=200)
ax.set_xlim(0, 12)
ax.set_ylim(0, 5.2)
ax.axis("off")
fig.suptitle("Every method tested — decided by evidence",
             fontsize=13, fontweight="bold", color=NIGHT, y=0.995)
rows = [
    ("County regional-risk term", "+0.047 ROC", "KEPT", GREEN),
    ("Polygon multi-hop cascade", "+0.006; Katz hurts", "REJECTED", RED),
    ("Long-run trajectory layer", "-0.008 ROC", "REJECTED", RED),
    ("5-qtr signature (leakage)", "0.727 -> 0.683", "CORRECTED", TEAL),
    ("Signature depth 2/3/log", "depth-3 +0.0065 (100%)", "KEPT d3", GREEN),
    ("Ito vs Stratonovich", "tie (+0.0001)", "KEPT Strat", TEAL),
    ("Replacement optimization", "4.2% (62.7% artifact)", "DROPPED", RED),
    ("Trajectory sig on triage", "-0.008 ROC", "REJECTED", RED),
    ("PB90-path sig on triage", "-0.015 (0% splits)", "REJECTED", RED),
    ("Production triage", "0.71 / 0.57 calibrated", "CHOSEN", DEEP),
]
ry = 4.55
rh = 0.42
for name, res, dec, c in rows:
    ax.add_patch(FancyBboxPatch((0.3, ry), 11.4, rh - 0.08,
                 boxstyle="round,pad=0.01,rounding_size=0.02",
                 linewidth=0, facecolor=LIGHT))
    ax.text(0.55, ry + 0.17, name, ha="left", va="center",
            fontsize=10.5, fontweight="bold", color=INK)
    ax.text(6.4, ry + 0.17, res, ha="left", va="center",
            fontsize=10, color=INK)
    ax.add_patch(FancyBboxPatch((9.7, ry + 0.02), 1.9, rh - 0.12,
                 boxstyle="round,pad=0.01,rounding_size=0.03",
                 linewidth=0, facecolor=c))
    ax.text(10.65, ry + 0.17, dec, ha="center", va="center",
            fontsize=9, fontweight="bold", color="#FFFFFF")
    ry -= rh
fig.savefig(CH / "experiment_map.png", bbox_inches="tight")
plt.close(fig)
print("wrote method_flow.png and experiment_map.png")
