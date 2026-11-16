"""Draws the charts of docs/when-to-compact.md from fit.py's results, in a light and a dark theme."""

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import fit

# "page" is GitHub's background in each theme. α is drawn on a diverging
# scale: below 1 in orange, 1 in a neutral grey, above 1 in blue.
THEMES = {
    "light": {"low": "#eb6834", "mid": "#52514e", "high": "#2a78d6", "ink": "#0b0b0b", "muted": "#52514e",
              "grid": "#e4e3df", "page": "#ffffff", "band": 0.16},
    "dark": {"low": "#d95926", "mid": "#9a998f", "high": "#3987e5", "ink": "#f0efec", "muted": "#c3c2b7",
             "grid": "#3a3936", "page": "#0d1117", "band": 0.28},
}


def tint(color, page, share):
    a, b = (tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) for c in (color, page))
    return "#" + "".join(f"{round(x * share + y * (1 - share)):02x}" for x, y in zip(a, b))


def style(ax, t):
    ax.set_facecolor("none")
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(t["grid"])
    ax.tick_params(colors=t["muted"], length=0, labelsize=10)
    ax.grid(axis="y", color=t["grid"], lw=0.8)
    ax.set_axisbelow(True)


def tokens(v, _=None):
    return "1M" if v >= 1_000_000 else f"{v / 1000:.0f}k" if v else "0"


def alpha_color(t, k):
    return t["low"] if k < 1 else t["high"] if k > 1 else t["mid"]


def sawtooth(p, c, t, path):
    """Context per request: compacting at X* against letting Claude Code compact near the window.
    The area under each line is what its requests read."""
    X = fit.steady(p, c, 1.0)
    K1, g = p.K + p.E, p.g
    n = int((fit.AUTO - K1) / g) + 120
    req = np.arange(n)
    never = K1 + (req % ((fit.AUTO - K1) / g)) * g
    cycle = (X - K1) / g
    model = K1 + (req % cycle) * g
    fig, ax = plt.subplots(figsize=(9, 3.2))
    style(ax, t)
    ax.fill_between(req, never, color=tint(t["mid"], t["page"], t["band"]), lw=0)
    ax.fill_between(req, model, color=tint(t["high"], t["page"], t["band"] + 0.08), lw=0)
    ax.plot(req, never, color=t["mid"], lw=2)
    ax.plot(req, model, color=t["high"], lw=2)
    for level, label in ((K1, "K′"), (X, "X*")):
        ax.axhline(level, color=t["muted"], lw=0.8, ls=":")
        ax.text(n * 1.008, level, label, va="center", fontsize=10, color=t["ink"])
    tooth = 3 * cycle  # the fourth tooth, clear of the never-compacting line
    ax.annotate("", (tooth + cycle * 0.5, X), (tooth + cycle * 0.5, K1),
                arrowprops={"arrowstyle": "<->", "color": t["ink"], "lw": 0.9, "shrinkA": 0, "shrinkB": 0})
    ax.text(tooth + cycle * 0.56, X + 25_000, f"Δ* ≈ {(X - K1) / 1000:.0f}k: {cycle:.0f} requests at g ≈ {g / 1000:.1f}k",
            fontsize=9, color=t["ink"], va="bottom")
    ax.annotate("compaction: the summary reads X*,\nthe next request writes K′ again", (2 * cycle, X),
                (2 * cycle + 4, X + 175_000), fontsize=9, color=t["ink"], ha="left",
                arrowprops={"arrowstyle": "-", "color": t["muted"], "lw": 0.8})
    ax.text(n * 0.30, 780_000, "never compacting, until Claude Code\ncompacts near the window", fontsize=9,
            color=t["muted"], ha="center")
    ax.set_ylim(0, 1_020_000)
    ax.set_xlim(0, n)
    ax.yaxis.set_major_formatter(tokens)
    ax.set_yticks([0, 250_000, 500_000, 750_000, 1_000_000])
    ax.set_xlabel("requests", color=t["muted"], fontsize=10)
    ax.set_ylabel("context (tokens)", color=t["muted"], fontsize=10)
    fig.savefig(path, transparent=True, bbox_inches="tight", metadata={"Date": None})
    plt.close(fig)


def cost_curve(cost, work, p, c, t, path):
    """Replayed cost per unit of work against the turn-end threshold, for three values of α. Under
    K' every turn end compacts, so the curves run flat there: a policy that compacts only at turn
    ends compacts at most once a turn."""
    ks = list(fit.CHART_ALPHAS)
    J = cost.sum(0)[:, None] / work.sum(0)
    fig, ax = plt.subplots(figsize=(9, 3.4))
    style(ax, t)
    x = fit.THRESHOLDS
    K1 = fit.terms(p, c)[3]
    ax.axvspan(0, K1, color=tint(t["muted"], t["page"], 0.12), lw=0)
    ax.text(K1 / 2, 0.99, f"under {K1 / 1000:.0f}k, what a\ncompaction leaves,\nevery turn end\ncompacts, at the\nsize its turn\nreached", ha="center",
            va="top", fontsize=9, color=t["muted"])
    for k in (0.8, 1.0, 1.2):
        i = ks.index(k)
        y = J[:-1, i] / J[-1, i]
        color = alpha_color(t, k)
        ax.plot(x, y, color=color, lw=2, label=f"α = {k:g}")
        j = y.argmin()
        ax.plot([x[j]], [y[j]], "o", ms=7, mfc=color, mec=t["page"], mew=1.5, zorder=3)
        ax.text(x[j], y[j] - 0.014, f"{x[j] / 1000:.0f}k: {y[j]:.0%}", ha="center", va="top", fontsize=9, color=t["ink"])
    ax.set_xlim(0, x[-1])
    ax.xaxis.set_major_formatter(tokens)
    ax.set_xticks([0, 200_000, 400_000, 600_000, 800_000])
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    ax.set_ylim(0.6, 1.0)
    ax.legend(frameon=False, fontsize=10, labelcolor=t["ink"], loc="lower right")
    ax.set_xlabel("threshold: compact at the first turn end at or above this context", color=t["muted"], fontsize=10)
    ax.set_ylabel("cost per unit of work,\nvs never compacting", color=t["muted"], fontsize=10)
    fig.savefig(path, transparent=True, bbox_inches="tight", metadata={"Date": None})
    plt.close(fig)


def thresholds(cost, work, BT, BX, p, c, t, path):
    """The best compaction point against α: the steady-state model and the turn-end replay, with 90% bands."""
    ks = np.array(fit.CHART_ALPHAS)
    X = [fit.steady(p, c, k) for k in ks]
    # Never compacting short of Claude Code's own compaction is drawn at it.
    T, BT = (np.minimum(v, fit.AUTO) for v in (fit.best(cost, work)[0], BT))
    fig, ax = plt.subplots(figsize=(9, 3.6))
    style(ax, t)
    ax.axvspan(0.8, 1.2, color=tint(t["mid"], t["page"], 0.10), lw=0)
    ax.text(1.0, 40_000, "α from 0.8 to 1.2", ha="center", va="bottom", fontsize=9, color=t["muted"])
    for level, label in ((fit.FLOOR, "sparkjoy asks from 400k"), (fit.AUTO, "Claude Code compacts")):
        ax.axhline(level, color=t["muted"], lw=0.8, ls=":")
        ax.text(ks[0], level + 12_000, label, fontsize=9, color=t["muted"])
    for y, band, color, label in ((X, BX, t["mid"], "steady state, compacting at any request"),
                                  (T, BT, t["high"], "replay, compacting at turn ends")):
        lo, hi = np.percentile(band, [5, 95], axis=0)
        ax.fill_between(ks, lo, hi, color=tint(color, t["page"], t["band"]), lw=0)
        ax.plot(ks, y, color=color, lw=2, label=label, marker="o", ms=4)
    ax.set_xlim(ks[0], ks[-1])
    ax.set_ylim(0, 1_020_000)
    ax.yaxis.set_major_formatter(tokens)
    ax.set_yticks([0, 250_000, 500_000, 750_000, 1_000_000])
    ax.set_xlabel("context efficiency coefficient α", color=t["muted"], fontsize=10)
    ax.set_ylabel("best compaction point", color=t["muted"], fontsize=10)
    ax.legend(frameon=False, fontsize=10, labelcolor=t["ink"], loc="lower right")
    fig.savefig(path, transparent=True, bbox_inches="tight", metadata={"Date": None})
    plt.close(fig)


def draw(out, p, c, cost, work, BT, BX):
    plt.rcParams.update({"svg.fonttype": "none", "font.family": ["Helvetica", "Arial", "DejaVu Sans"],
                         "svg.hashsalt": "sparkjoy"})
    out.mkdir(parents=True, exist_ok=True)
    for theme, t in THEMES.items():
        sawtooth(p, c, t, out / f"model-sawtooth-{theme}.svg")
        cost_curve(cost, work, p, c, t, out / f"model-cost-{theme}.svg")
        thresholds(cost, work, BT, BX, p, c, t, out / f"model-threshold-{theme}.svg")
