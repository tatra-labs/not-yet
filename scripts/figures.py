"""Draw figures/frontier.png and figures/coverage.png from results/results.json. Needs matplotlib."""

import json
import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
SURFACE, INK, INK2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9"
BLUE, ORANGE = "#2a78d6", "#eb6834"
LABELS = {"none": "no defense", "tool-filter": "tool filter", "taint": "strict taint",
          "gard-seed": "boundary only (seed TBox)", "gard-reference-no-wait": "ontology, waiting = refuse",
          "gard-reference": "ontology + waiting"}


def style(ax):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#c3c2b7")
    ax.tick_params(colors=MUTED, labelcolor=INK2)
    ax.grid(color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def frontier(data, out):
    fig, ax = plt.subplots(figsize=(7.6, 4.8), facecolor=SURFACE)
    style(ax)
    offsets = {"none": (8, -4), "tool-filter": (8, -4), "taint": (8, -4), "gard-seed": (-8, 8),
               "gard-reference-no-wait": (-10, 8), "gard-reference": (-10, 10)}
    for name, label in LABELS.items():
        s = data["headline"][name]
        ours = name.startswith("gard")
        ax.scatter(s["au"], s["asr_actions"], s=90, color=BLUE if ours else ORANGE, edgecolor=SURFACE,
                   linewidth=2, zorder=3)
        dx, dy = offsets[name]
        ax.annotate(f"{label}\nCU {s['cu']:.2f}", (s["au"], s["asr_actions"]), xytext=(dx, dy),
                    textcoords="offset points", fontsize=8.5, color=INK2,
                    ha="right" if dx < 0 else "left", va="bottom" if dy > 0 else "center")
    ax.set_xlim(0.6, 1.02)
    ax.set_ylim(-0.04, 0.9)
    ax.set_xlabel("utility under attack", color=INK2)
    ax.set_ylabel("attack success, action injections", color=INK2)
    ax.scatter([], [], color=ORANGE, label="baselines")
    ax.scatter([], [], color=BLUE, label="ready / waiting / forbidden gate")
    ax.legend(frameon=False, loc="upper left", fontsize=8.5, labelcolor=INK2)
    ax.set_title("Better is down and to the right", color=INK, fontsize=11, loc="left")
    fig.tight_layout()
    fig.savefig(out, dpi=160, facecolor=SURFACE)


def coverage(data, out):
    fig, ax = plt.subplots(figsize=(7.6, 4.2), facecolor=SURFACE)
    style(ax)
    for ms, color in ((2, ORANGE), (1, BLUE)):
        rows = [r for r in data["coverage_sweep"] if r["min_support"] == ms]
        xs = [r["train_fraction"] for r in rows]
        ys = [r["action_asr_mean"] for r in rows]
        hi = [r["action_asr_max"] for r in rows]
        ax.fill_between(xs, ys, hi, color=color, alpha=0.12, linewidth=0)
        ax.plot(xs, ys, color=color, linewidth=2, marker="o", markersize=7, markeredgecolor=SURFACE,
                label=f"support from {ms} task group{'s' if ms > 1 else ''}")
        ax.annotate(f"{ys[1]:.3f}", (xs[1], ys[1]), xytext=(6, 6), textcoords="offset points", fontsize=8.5,
                    color=INK2)
    ax.axhline(0, color=MUTED, linewidth=1, linestyle=(0, (4, 3)))
    ax.text(0.205, 0.006, "hand-written reference TBox: 0", fontsize=8.5, color=MUTED)
    ax.set_xticks([0.2, 0.4, 0.6, 0.8], ["20%", "40%\n(paper)", "60%", "80%"])
    ax.set_xlabel("share of user tasks used for training", color=INK2)
    ax.set_ylabel("action-attack success on held-out tasks", color=INK2)
    ax.legend(frameon=False, fontsize=8.5, labelcolor=INK2)
    ax.set_title("An evolved ontology only blocks what its training tasks exercised (mean, band to worst of 10 "
                 "splits)", color=INK, fontsize=10, loc="left")
    fig.tight_layout()
    fig.savefig(out, dpi=160, facecolor=SURFACE)


if __name__ == "__main__":
    data = json.loads((ROOT / "results" / "results.json").read_text(encoding="utf-8"))
    (ROOT / "figures").mkdir(exist_ok=True)
    frontier(data, ROOT / "figures" / "frontier.png")
    coverage(data, ROOT / "figures" / "coverage.png")
