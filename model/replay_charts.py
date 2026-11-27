"""Draws the README's replay charts, in a light and a dark theme, and returns the figures the README
quotes from them, which docs.py writes to docs/figures.json's `replay` section.

The replay is the cost model's `as_run` (model/fit.py): it prices each session twice from its
logged requests, at fit.MODEL's list prices, "with" sparkjoy's compactions where they happened and
its questions, and "without" them, so that Claude Code compacts on its own near the window, and in a
session someone had open, also an idle context from fit.CC_IDLE. The charts:

  session.svg   one session's context per request, with and without sparkjoy
  context.svg   the share of the window's requests at each context size, with and without
  savings.svg   what the window's sessions cost under Claude Code's default, as if none had its idle
                compaction and as if all had it, and with sparkjoy as they ran
"""

import charts
import fit
import numpy as np
from charts import plt

PLUGIN = "sparkjoy"
LONG = 600_000  # the size the README counts requests at or above
ARMS = (
    ("without", "without (replayed)"),
    ("with", f"with {PLUGIN}"),
)


def marker(t, compacted):
    """A question sparkjoy asked, at a turn end or in an idle session, in the colour of the line it sits
    on: a large dot where the model compacted, a small one where it kept going."""
    if compacted:
        return {"marker": "o", "ms": 8, "mew": 1.5, "mec": t["page"], "mfc": t["with"]}
    return {"marker": "o", "ms": 5, "mew": 1, "mec": t["page"], "mfc": t["with"]}


def areas(t, draw):
    """Each arm's fill, then each arm's line, so neither fill covers a line."""
    for key, _ in ARMS:
        draw(key, charts.tint(t[key], t["page"], t["band"]), True)
    for key, _ in ARMS:
        draw(key, t[key], False)


def session_chart(traces, checks, t, path):
    """One session's context per request in each arm, keyed as ARMS is, with a dot at each question
    sparkjoy asked, as (request, whether it compacted)."""
    fig, ax = plt.subplots(figsize=(9, 3.4))
    charts.style(ax, t)

    def draw(key, color, fill):
        ys = traces[key]
        if fill:
            # The area under the line, closed by its two corners on the axis, where fill_between would
            # write a point on the axis under every request.
            ax.fill([0, *range(len(ys)), len(ys) - 1], [0, *ys, 0], color=color, lw=0)
        else:
            ax.plot(ys, color=color, lw=2, solid_joinstyle="round")

    areas(t, draw)
    ys = traces["with"]
    for i, compacted in checks:
        ax.plot([i], [ys[i]], zorder=3, clip_on=False, **marker(t, compacted))
    # The line ends sit close together, so a legend names the arms, each beside a swatch of its line.
    lines = [
        (plt.Line2D([], [], color=t[key], lw=2), label)
        for key, label in (
            ("with", f"with {PLUGIN}"),
            ("without", f"without {PLUGIN}, replayed"),
        )
    ]
    dots = [
        (plt.Line2D([], [], ls="", **marker(t, c)), label)
        for c, label in ((False, "asked, kept going"), (True, "asked, compacted"))
    ]
    handles, labels = zip(*lines, *dots, strict=True)
    ax.legend(
        handles,
        labels,
        frameon=False,
        fontsize=9,
        labelcolor=t["ink"],
        loc="lower right",
        bbox_to_anchor=(1, 1),
        ncols=4,
        borderaxespad=0.2,
        handlelength=1.3,
        handletextpad=0.5,
        columnspacing=1.1,
    )
    ax.set_ylim(0, 1_020_000)
    ax.set_xlim(0, len(ys) - 1)
    ax.yaxis.set_major_formatter(charts.tokens)
    ax.set_yticks([0, 250_000, 500_000, 750_000, 1_000_000])
    ax.set_xlabel("Requests in one session", color=t["muted"], fontsize=10)
    charts.axis_name(ax, "Context (tokens)", t)
    charts.save(fig, path)


def context_chart(contexts, t, path):
    """The share of requests at each context size, from every request's context in each arm."""
    bins = list(range(0, 1_000_001, 50_000))
    fig, ax = plt.subplots(figsize=(9, 2.8))
    charts.style(ax, t)
    shares = {}

    def draw(key, color, fill):
        ys = contexts[key]
        shares[key], _, _ = ax.hist(
            ys,
            bins=bins,
            weights=[100 / len(ys)] * len(ys),
            color=color,
            lw=0 if fill else 2,
            histtype="stepfilled" if fill else "step",
        )

    areas(t, draw)
    # Each arm named above its tallest bin past where the two part: sparkjoy's peak, the replay's tail.
    for key, at in (("with", 250_000), ("without", 800_000)):
        i = bins.index(at)
        label = dict(ARMS)[key]
        ax.text(
            at + 25_000,
            max(shares[key][i - 1 : i + 2]) + 0.5,
            label,
            ha="center",
            va="bottom",
            fontsize=9,
            color=t["ink"],
        )
    ax.xaxis.set_major_formatter(charts.tokens)
    ax.set_xticks([0, 200_000, 400_000, 600_000, 800_000, 1_000_000])
    ax.set_xlim(0, 1_000_000)
    # Up to the next 5% above the tallest bin and the name over it.
    top = 5 * np.ceil((max(v.max() for v in shares.values()) + 2) / 5)
    ax.set_ylim(0, top)
    ax.set_yticks(np.arange(0, top + 1, 5))
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0f}%")
    ax.set_xlabel("Context (tokens)", color=t["muted"], fontsize=10)
    charts.axis_name(ax, "Share of requests", t)
    charts.save(fig, path)


def savings_chart(bars, t, path):
    """What the sessions cost, one bar each for `bars`, as (name, dollars, arm): Claude Code's bars in
    "without", sparkjoy's last in "with", each of Claude Code's named with what sparkjoy saves on it."""
    fig, ax = plt.subplots(figsize=(9, 2.2))
    charts.style(ax, t)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color=t["grid"], lw=0.8)
    ax.spines["bottom"].set_visible(False)
    ax.spines["left"].set_visible(True)
    ax.spines["left"].set_color(t["muted"])
    ax.spines["left"].set_linewidth(0.8)
    ours = bars[-1][1]
    for i, (_, v, key) in enumerate(bars):
        ax.barh(-i, v, height=0.62, color=t[key], lw=0)
        note = (
            f"${v:,.0f}"
            if key == "with"
            else f"${v:,.0f} ({PLUGIN} saves {1 - ours / v:.0%})"
        )
        ax.annotate(
            note,
            (v, -i),
            xytext=(5, 0),
            textcoords="offset points",
            va="center",
            fontsize=9,
            color=t["ink"],
        )
    ax.set_yticks([-i for i in range(len(bars))], [name for name, _, _ in bars])
    ax.tick_params(axis="y", labelsize=10, labelcolor=t["ink"])
    ax.set_ylim(-len(bars) + 0.4, 0.6)
    ax.set_xlim(0, max(v for _, v, _ in bars) * 1.32)
    ax.xaxis.set_major_formatter(lambda v, _: f"${v:,.0f}")
    ax.set_xlabel("Cost of the sessions", color=t["muted"], fontsize=10)
    charts.save(fig, path)


def draw(out, S, runs, session):
    """Draw the charts into `out` and return their figures, from the sessions S and `runs`, each
    session's `fit.as_run` at fit.MODEL's prices as it ran (None), as if in the background (False) and
    as if open (True), keyed by that argument; `session` is a prefix of the id of the session the
    session chart shows."""
    ran = runs[None]
    with_ = sum(r[0][0] for r in ran)
    contexts = {
        "with": np.concatenate([r[1] for r in ran]),
        "without": np.concatenate([r[2] for r in ran]),
    }
    i, one = next((i, s) for i, s in enumerate(S) if s.path.name.startswith(session))
    (one_with, one_without, _), *trace = ran[i]
    traces = dict(zip(("with", "without"), trace, strict=True))
    asked = [v for v in one.verdicts if v.kind in ("compacted", "kept") and v.asked]
    checks = [
        (int(np.searchsorted(one.t, v.t)) - 1, v.kind == "compacted") for v in asked
    ]
    # Claude Code's default, each session priced as if none had its idle compaction and as if all had
    # it, then sparkjoy as each session ran.
    bars = [
        (name, sum(r[0][1] for r in runs[opened]), "without")
        for opened, name in (
            (False, "Claude Code"),
            (True, "Claude Code with\nidle compaction"),
        )
    ]
    bars.append((PLUGIN, with_, "with"))
    out.mkdir(parents=True, exist_ok=True)
    for theme, t in charts.THEMES.items():
        session_chart(traces, checks, t, out / f"session-{theme}.svg")
        context_chart(contexts, t, out / f"context-{theme}.svg")
        savings_chart(bars, t, out / f"savings-{theme}.svg")

    logged = 0.0
    for s in S:
        c = fit.prices(fit.MODEL, s.ttl)
        logged += (
            float(
                s.read.sum()
                + c.write * s.write.sum()
                + c.input * s.inp.sum()
                + c.output * s.out.sum()
            )
            * c.read
        )
    every = [
        v for s in S for v in s.verdicts if v.kind in ("compacted", "kept") and v.asked
    ]
    return {
        # The requests' logged tokens at fit.MODEL's prices; docs.py's `as_ran` holds what the replay
        # prices, the savings chart's bars included.
        "logged_usd": logged,
        # What sparkjoy as the sessions ran saves against each of the savings chart's Claude Code bars.
        "savings_chart_saved": {
            "claude_code": 1 - with_ / bars[0][1],
            "claude_code_with_idle_compaction": 1 - with_ / bars[1][1],
        },
        "mean_context": {k: v.mean() for k, v in contexts.items()},
        f"share_at_{LONG // 1000}k": {
            k: (v >= LONG).mean() for k, v in contexts.items()
        },
        "verdicts": {
            "asked": len(every),
            "compacted": sum(v.kind == "compacted" for v in every),
        },
        "session": {
            "requests": len(one.t),
            "days": (one.t[-1] - one.t[0]) / 86_400,
            "without_usd": one_without,
            "with_usd": one_with,
            "asked_at_turn_ends": sum(v.idle is None for v in asked),
            "asked_idle": sum(v.idle is not None for v in asked),
            "kept_going": sum(not c for _, c in checks),
            "compacted_at": [traces["with"][i] for i, c in checks if c],
            "asked": [
                {"kind": v.kind, "tokens": v.tokens, "idle": v.idle is not None}
                for v in asked
            ],
            "peak_without": traces["without"].max(),
            # Claude Code's own compactions without sparkjoy, where the context falls by over 300k.
            "compactions_without": int((np.diff(traces["without"]) < -300_000).sum()),
        },
    }
