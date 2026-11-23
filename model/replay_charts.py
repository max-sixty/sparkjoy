#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy>=2.1", "matplotlib>=3.9"]
# ///
"""Draws the README's replay charts, in a light and a dark theme, and prints the figures it quotes.

    uv run --script model/replay_charts.py --since 2026-11-18T00:00Z --until 2026-11-23T00:00Z \
        --session 00e0c1c4 --out assets

The replay is the cost model's `as_run` (model/fit.py): it prices each session twice from its
logged requests, at fit.MODEL's list prices, "with" sparkjoy's compactions where they happened and
its questions, and "without" them, so that Claude Code compacts on its own near the window, and in a
session someone had open, also an idle context from fit.CC_IDLE. The charts:

  session.svg   one session's context per request, with and without sparkjoy
  context.svg   the share of the window's requests at each context size, with and without
  cost.svg      the replayed cost of the window's sessions, with and without
"""

import argparse
from pathlib import Path

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


def cost_chart(without, with_, judge, t, path):
    """The sessions' cost without and with sparkjoy, in dollars, the latter's questions, `judge`,
    marked within it."""
    fig, ax = plt.subplots(figsize=(9, 1.5))
    charts.style(ax, t)
    ax.grid(False)
    ax.spines["bottom"].set_visible(False)
    ax.barh([1, 0], [without, with_], color=[t["without"], t["with"]], height=0.62)
    ax.barh([0], [judge], left=[with_ - judge], color=t["ink"], height=0.62)
    for y, v, label in (
        (1, without, f"without {PLUGIN}"),
        (0, with_, f"with {PLUGIN}"),
    ):
        ax.text(
            -without * 0.01,
            y,
            label,
            ha="right",
            va="center",
            fontsize=10,
            color=t["ink"],
        )
        ax.text(
            v + without * 0.01,
            y,
            f"${v:,.0f}",
            ha="left",
            va="center",
            fontsize=10,
            color=t["ink"],
        )
    ax.text(
        with_ + without * 0.09,
        0,
        charts.percent(with_ / without - 1),
        ha="left",
        va="center",
        fontsize=10,
        color=t["muted"],
    )
    ax.annotate(
        f"{PLUGIN}'s questions, ${judge:,.0f}",
        (with_ - judge / 2, -0.31),
        xytext=(0, -12),
        textcoords="offset points",
        ha="center",
        va="top",
        fontsize=9,
        color=t["muted"],
        arrowprops={"arrowstyle": "-", "color": t["muted"], "lw": 0.8},
    )
    ax.set_xlim(0, without * 1.12)
    ax.set_yticks([])
    ax.set_xticks([])
    charts.save(fig, path)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    fit.window_args(ap)
    ap.add_argument(
        "--session",
        required=True,
        help="a prefix of the id of the session the session chart shows, from the window",
    )
    ap.add_argument(
        "--out", type=Path, required=True, help="the directory the SVGs go to"
    )
    a = ap.parse_args()
    S, since, until = fit.read(a)
    p = fit.measure(S, fit.usual(s.ttl for s in S))
    runs = [(s, *r) for s in S if (r := fit.as_run(s, p, model=fit.MODEL))[0]]
    without, with_, judge = (sum(d[i] for _, d, _, _ in runs) for i in (1, 0, 2))
    contexts = {
        "with": np.concatenate([w for _, _, w, _ in runs]),
        "without": np.concatenate([wo for _, _, _, wo in runs]),
    }
    one = next(s for s in S if s.path.name.startswith(a.session))
    (one_with, one_without, _), traces = (
        (d := fit.as_run(one, p, model=fit.MODEL))[0],
        {"with": d[1], "without": d[2]},
    )
    asked = [v for v in one.verdicts if v.kind in ("compacted", "kept")]
    checks = [
        (int(np.searchsorted(one.t, v.t)) - 1, v.kind == "compacted") for v in asked
    ]
    a.out.mkdir(parents=True, exist_ok=True)
    for theme, t in charts.THEMES.items():
        session_chart(traces, checks, t, a.out / f"session-{theme}.svg")
        context_chart(contexts, t, a.out / f"context-{theme}.svg")
        cost_chart(without, with_, judge, t, a.out / f"cost-{theme}.svg")

    n = len(contexts["with"])
    logged = 0.0
    for s, *_ in runs:
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
    print(
        f"{len(runs)} sessions, {n:,} requests, {since:%Y-%m-%d %H:%M}Z to {until:%Y-%m-%d %H:%M}Z; "
        f"the requests' logged tokens at {fit.MODEL} prices ${logged:,.0f}"
    )
    print(
        f"cost: ${without:,.0f} without, ${with_:,.0f} with ({with_ / without - 1:+.1%}); its questions ${judge:,.0f}"
    )
    print(
        f"mean context {contexts['without'].mean() / 1000:.0f}k without, {contexts['with'].mean() / 1000:.0f}k with; "
        f"requests at {LONG // 1000}k+: {(contexts['without'] >= LONG).mean():.0%} without, "
        f"{(contexts['with'] >= LONG).mean():.0%} with"
    )
    every = [v for s, *_ in runs for v in s.verdicts if v.kind in ("compacted", "kept")]
    print(
        f"verdicts: {len(every)}, compacted after {sum(v.kind == 'compacted' for v in every)}"
    )
    days = (one.t[-1] - one.t[0]) / 86_400
    print(
        f"session chart: {one.path.name}, {len(one.t):,} requests over {days:.1f} days, "
        f"${one_without:,.0f} without, ${one_with:,.0f} with ({one_with / one_without - 1:+.1%}); "
        f"asked {len(asked)} times, {sum(v.idle is None for v in asked)} at turn ends and "
        f"{sum(v.idle is not None for v in asked)} when idle; kept going {sum(not c for _, c in checks)} times; compacted at "
        f"{', '.join(f'{traces["with"][i] / 1000:.0f}k' for i, c in checks if c)}; without, Claude Code compacts "
        f"{int((np.diff(traces['without']) < -300_000).sum())} times"
    )
    for v in asked:
        print(
            f"  {v.kind} at {v.tokens // 1000}k{'' if v.idle is None else ', idle'}: {v.reason}"
        )


if __name__ == "__main__":
    main()
