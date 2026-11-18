"""Draws the charts of docs/when-to-compact.md from fit.py's results, in a light and a dark theme."""

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import fit

# "page" is GitHub's background in each theme. s is drawn on a sequential scale, from a
# neutral grey at 0 through deepening shades of blue.
THEMES = {
    "light": {"mid": "#52514e", "high": "#2a78d6", "ink": "#0b0b0b", "muted": "#52514e",
              "grid": "#e4e3df", "page": "#ffffff", "band": 0.16},
    "dark": {"mid": "#9a998f", "high": "#3987e5", "ink": "#f0efec", "muted": "#c3c2b7",
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


def shades(t):
    """The colour of each value of s the charts draw: deepening shades of the high colour as s grows,
    so the order of the curves reads at a glance."""
    return {0: t["mid"], 100_000: tint(t["high"], t["page"], 0.45), 200_000: tint(t["high"], t["page"], 0.72),
            400_000: t["high"]}


def compacted(ax, K, t, y, va):
    """Shade the contexts below K, with a label at height y."""
    ax.axvspan(0, K, color=tint(t["muted"], t["page"], 0.12), lw=0)
    ax.text(K / 2, y, f"under {K / 1000:.0f}k,\nwhat a\ncompaction\nleaves", ha="center", va=va,
            fontsize=9, color=t["muted"])


def progress_curve(p, c, t, path):
    """A request's efficacy against its context, 1 - 2^(-x/s): how much it gets done, where 1 is the
    most a request can get done, for the values of s the cost chart draws. Each curve reaches half at x = s."""
    fig, ax = plt.subplots(figsize=(9, 3.2))
    style(ax, t)
    compacted(ax, p.K, t, 0.75, "center")
    x = np.linspace(0, fit.AUTO, 1_000)
    at_s = 0.5
    for k, color in shades(t).items():
        ax.plot(x, fit.eta(x, k), color=color, lw=2, label=f"s = {tokens(k)}")
        if k:
            ax.plot([k], [at_s], "o", ms=7, mfc=color, mec=t["page"], mew=1.5, zorder=3)
    ax.axhline(at_s, color=t["muted"], lw=0.8, ls=":")
    ax.text(fit.AUTO, at_s - 0.03, "each dot is at x = s, where efficacy reaches half", ha="right", va="top",
            fontsize=9, color=t["muted"])
    ax.set_xlim(0, fit.AUTO)
    ax.xaxis.set_major_formatter(tokens)
    ax.set_xticks([0, 200_000, 400_000, 600_000, 800_000])
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    ax.set_ylim(0, 1.05)
    ax.legend(frameon=False, fontsize=10, labelcolor=t["ink"], loc="lower right")
    ax.set_xlabel("the request's context, x", color=t["muted"], fontsize=10)
    ax.set_ylabel("η(x), a request's efficacy\n(100% = the most it can do)", color=t["muted"], fontsize=10)
    fig.savefig(path, transparent=True, bbox_inches="tight", metadata={"Date": None})
    plt.close(fig)


def sawtooth(p, c, t, path):
    """Context per request: compacting at X* against letting Claude Code compact near the window.
    The area under each line is what its requests read."""
    X = fit.steady(p, c)
    K, g = p.K, p.g
    n = int((fit.AUTO - K) / g) + 120
    req = np.arange(n)
    never = K + (req % ((fit.AUTO - K) / g)) * g
    cycle = (X - K) / g
    model = K + (req % cycle) * g
    fig, ax = plt.subplots(figsize=(9, 3.2))
    style(ax, t)
    ax.fill_between(req, never, color=tint(t["mid"], t["page"], t["band"]), lw=0)
    ax.fill_between(req, model, color=tint(t["high"], t["page"], t["band"] + 0.08), lw=0)
    ax.plot(req, never, color=t["mid"], lw=2)
    ax.plot(req, model, color=t["high"], lw=2)
    for level, label in ((K, "K"), (X, "X*")):
        ax.axhline(level, color=t["muted"], lw=0.8, ls=":")
        ax.text(n * 1.008, level, label, va="center", fontsize=10, color=t["ink"])
    tooth = 3 * cycle  # the fourth tooth, clear of the never-compacting line
    ax.annotate("", (tooth + cycle * 0.5, X), (tooth + cycle * 0.5, K),
                arrowprops={"arrowstyle": "<->", "color": t["ink"], "lw": 0.9, "shrinkA": 0, "shrinkB": 0})
    ax.text(tooth + cycle * 0.56, X + 25_000, f"Δ* ≈ {(X - K) / 1000:.0f}k: {cycle:.0f} requests at g ≈ {g / 1000:.1f}k",
            fontsize=9, color=t["ink"], va="bottom")
    ax.annotate("compaction: the summary reads X*,\nthe next request writes K again", (2 * cycle, X),
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


def cost_curve(cost, progress, p, c, t, path):
    """The steady state's cost per unit of progress against the compaction point, for s from 0 to 400k:
    a compaction can follow any request, and the context grows smoothly. Against never compacting,
    where Claude Code compacts at AUTO."""
    fig, ax = plt.subplots(figsize=(9, 3.4))
    style(ax, t)
    compacted(ax, p.K, t, 1.28, "top")
    for k, color in shades(t).items():
        X, J = fit.curve(p, c, k)
        y = J / J[-1]
        ax.plot(X, y, color=color, lw=2, label=f"s = {tokens(k)}")
        j = y.argmin()
        if j == len(y) - 1:
            continue  # no compaction short of Claude Code's own pays
        ax.plot([X[j]], [y[j]], "o", ms=7, mfc=color, mec=t["page"], mew=1.5, zorder=3)
        ax.text(X[j] + 14_000, y[j] - 0.012, f"{X[j] / 1000:.0f}k: {y[j]:.0%}", ha="left", va="top", fontsize=9,
                color=t["ink"])
    ax.set_xlim(0, fit.AUTO)
    ax.xaxis.set_major_formatter(tokens)
    ax.set_xticks([0, 200_000, 400_000, 600_000, 800_000])
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    ax.set_ylim(0.4, 1.3)
    ax.legend(frameon=False, fontsize=10, labelcolor=t["ink"], loc="lower right")
    ax.set_xlabel("compact when the context reaches", color=t["muted"], fontsize=10)
    ax.set_ylabel("cost per unit of progress,\nvs never compacting", color=t["muted"], fontsize=10)
    fig.savefig(path, transparent=True, bbox_inches="tight", metadata={"Date": None})
    plt.close(fig)


def thresholds(cost, progress, BT, BX, p, c, t, path):
    """The best compaction point against s: the steady-state model and the turn-end replay, with 90% bands."""
    ks = np.array(fit.CHART_SCALES)
    X = [fit.steady(p, c, k) for k in ks]
    # Never compacting short of Claude Code's own compaction is drawn at it.
    T, BT = (np.minimum(v, fit.AUTO) for v in (fit.best(cost, progress)[0], BT))
    fig, ax = plt.subplots(figsize=(9, 3.6))
    style(ax, t)
    for level, label in ((fit.FLOOR, f"sparkjoy asks from {tokens(fit.FLOOR)}"), (fit.AUTO, "Claude Code compacts")):
        ax.axhline(level, color=t["muted"], lw=0.8, ls=":")
        # At the right, where both curves have climbed past sparkjoy's floor.
        ax.text(ks[-1], level + 12_000, label, ha="right", fontsize=9, color=t["muted"])
    for y, band, color, label in ((X, BX, t["mid"], "steady state, compacting at any request"),
                                  (T, BT, t["high"], "replay, compacting at turn ends")):
        lo, hi = np.percentile(band, [5, 95], axis=0)
        ax.fill_between(ks, lo, hi, color=tint(color, t["page"], t["band"]), lw=0)
        ax.plot(ks, y, color=color, lw=2, label=label, marker="o", ms=4)
    ax.set_xlim(ks[0], ks[-1])
    ax.xaxis.set_major_formatter(tokens)
    ax.set_ylim(0, 1_020_000)
    ax.yaxis.set_major_formatter(tokens)
    ax.set_yticks([0, 250_000, 500_000, 750_000, 1_000_000])
    ax.set_xlabel("s, the context at which a request reaches half of full efficacy", color=t["muted"], fontsize=10)
    ax.set_ylabel("best compaction point", color=t["muted"], fontsize=10)
    ax.legend(frameon=False, fontsize=10, labelcolor=t["ink"], loc="lower right")
    fig.savefig(path, transparent=True, bbox_inches="tight", metadata={"Date": None})
    plt.close(fig)


def draw(out, p, c, cost, progress, BT, BX):
    plt.rcParams.update({"svg.fonttype": "none", "font.family": ["Helvetica", "Arial", "DejaVu Sans"],
                         "svg.hashsalt": "sparkjoy"})
    out.mkdir(parents=True, exist_ok=True)
    for theme, t in THEMES.items():
        progress_curve(p, c, t, out / f"model-progress-{theme}.svg")
        sawtooth(p, c, t, out / f"model-sawtooth-{theme}.svg")
        cost_curve(cost, progress, p, c, t, out / f"model-cost-{theme}.svg")
        thresholds(cost, progress, BT, BX, p, c, t, out / f"model-threshold-{theme}.svg")
