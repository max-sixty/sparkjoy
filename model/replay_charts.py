#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy>=2.1", "matplotlib>=3.9"]
# ///
"""Draws the README's replay charts, in a light and a dark theme, and prints the figures it quotes.

    uv run --script model/replay_charts.py replay.json --session 00e0c1c4 --out assets

The replay prices each session twice from its logged requests: "as run", with sparkjoy's
compactions where they happened and a check at each turn end it asked about, and "no mod", with
none of them, so that Claude Code compacts on its own near the window. The tool that writes it
isn't published. Its JSON holds, for each session, the transcript's "path", its "logged" cost, and
for each arm the context of every request ("trace"), the request count ("n"), the summed context
("ctx") and the cost of the requests, the compactions and the checks ("reqs", "compact", "judge",
"n_judge"), all in dollars. "checks" lists the turn ends sparkjoy asked about, as [request index,
whether it compacted], and "reasons" the verdicts it wrote. The charts:

  session.svg   one session's context per request, with and without sparkjoy
  context.svg   the share of all requests at each context size, with and without
  cost.svg      the replayed cost of all sessions, with and without
"""

import argparse
import json
from pathlib import Path

import charts
from charts import plt

PLUGIN = "sparkjoy"
LONG = 600_000  # the size the README counts requests at or above
ARMS = (("without", "no mod", "without (replayed)"), ("with", "as run", f"with {PLUGIN}"))


def total(arm):
    return arm.get("reqs", 0) + arm.get("compact", 0) + arm.get("judge", 0)


def marker(t, compacted):
    """A turn end sparkjoy asked about, in the colour of the line it sits on: a large dot where the
    model compacted, a small one where it kept going."""
    if compacted:
        return {"marker": "o", "ms": 8, "mew": 1.5, "mec": t["page"], "mfc": t["with"]}
    return {"marker": "o", "ms": 5, "mew": 1, "mec": t["page"], "mfc": t["with"]}


def areas(t, draw):
    """Each arm's fill, then each arm's line, so neither fill covers a line."""
    for key, _, _ in ARMS:
        draw(key, charts.tint(t[key], t["page"], t["band"]), True)
    for key, _, _ in ARMS:
        draw(key, t[key], False)


def session_chart(s, t, path):
    fig, ax = plt.subplots(figsize=(9, 3.4))
    charts.style(ax, t)
    traces = {key: s[arm]["trace"] for key, arm, _ in ARMS}

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
    for i, compacted in s["checks"]:
        ax.plot([i], [ys[i]], zorder=3, clip_on=False, **marker(t, compacted))
    # The line ends sit close together, so a legend names the arms, each beside a swatch of its line.
    lines = [(plt.Line2D([], [], color=t[key], lw=2), label)
             for key, label in (("with", f"with {PLUGIN}"), ("without", f"without {PLUGIN}, replayed"))]
    dots = [(plt.Line2D([], [], ls="", **marker(t, c)), label)
            for c, label in ((False, "turn end, kept going"), (True, "turn end, compacted"))]
    handles, labels = zip(*lines, *dots, strict=True)
    ax.legend(handles, labels, frameon=False, fontsize=9, labelcolor=t["ink"], loc="lower right",
              bbox_to_anchor=(1, 1), ncols=4, borderaxespad=0.2, handlelength=1.3, handletextpad=0.5,
              columnspacing=1.1)
    ax.set_ylim(0, 1_020_000)
    ax.set_xlim(0, len(ys) - 1)
    ax.yaxis.set_major_formatter(charts.tokens)
    ax.set_yticks([0, 250_000, 500_000, 750_000, 1_000_000])
    ax.set_xlabel("Requests in one session", color=t["muted"], fontsize=10)
    charts.axis_name(ax, "Context (tokens)", t)
    charts.save(fig, path)


def context_chart(S, t, path):
    bins = list(range(0, 1_000_001, 50_000))
    fig, ax = plt.subplots(figsize=(9, 2.8))
    charts.style(ax, t)
    contexts = {key: [c for s in S for c in s[arm]["trace"]] for key, arm, _ in ARMS}
    shares = {}

    def draw(key, color, fill):
        ys = contexts[key]
        shares[key], _, _ = ax.hist(ys, bins=bins, weights=[100 / len(ys)] * len(ys), color=color,
                                    lw=0 if fill else 2, histtype="stepfilled" if fill else "step")

    areas(t, draw)
    # Each arm named above its tallest bin past where the two part: sparkjoy's peak, the replay's tail.
    for key, at in (("with", 250_000), ("without", 800_000)):
        i = bins.index(at)
        label = next(label for k, _, label in ARMS if k == key)
        ax.text(at + 25_000, max(shares[key][i - 1:i + 2]) + 0.5, label, ha="center", va="bottom", fontsize=9,
                color=t["ink"])
    ax.xaxis.set_major_formatter(charts.tokens)
    ax.set_xticks([0, 200_000, 400_000, 600_000, 800_000, 1_000_000])
    ax.set_xlim(0, 1_000_000)
    ax.set_ylim(0, 15)
    ax.set_yticks([0, 5, 10, 15])
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0f}%")
    ax.set_xlabel("Context (tokens)", color=t["muted"], fontsize=10)
    charts.axis_name(ax, "Share of requests", t)
    charts.save(fig, path)


def cost_chart(S, t, path):
    without = sum(total(s["no mod"]) for s in S)
    with_ = sum(total(s["as run"]) for s in S)
    judge = sum(s["as run"].get("judge", 0) for s in S)
    fig, ax = plt.subplots(figsize=(9, 1.5))
    charts.style(ax, t)
    ax.grid(False)
    ax.spines["bottom"].set_visible(False)
    ax.barh([1, 0], [without, with_], color=[t["without"], t["with"]], height=0.62)
    ax.barh([0], [judge], left=[with_ - judge], color=t["ink"], height=0.62)
    for y, v, label in ((1, without, f"without {PLUGIN}"), (0, with_, f"with {PLUGIN}")):
        ax.text(-without * 0.01, y, label, ha="right", va="center", fontsize=10, color=t["ink"])
        ax.text(v + without * 0.01, y, f"${v:,.0f}", ha="left", va="center", fontsize=10, color=t["ink"])
    ax.text(with_ + without * 0.09, 0, charts.percent(with_ / without - 1), ha="left", va="center", fontsize=10,
            color=t["muted"])
    ax.annotate(f"{PLUGIN}'s questions, ${judge:,.0f}",(with_ - judge / 2, -0.31), xytext=(0, -12), textcoords="offset points",
                ha="center", va="top", fontsize=9, color=t["muted"],
                arrowprops={"arrowstyle": "-", "color": t["muted"], "lw": 0.8})
    ax.set_xlim(0, without * 1.12)
    ax.set_yticks([])
    ax.set_xticks([])
    charts.save(fig, path)


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("replay", type=Path, help="the replay's JSON")
    p.add_argument("--session", required=True, help="a prefix of the session id the session chart shows")
    p.add_argument("--out", type=Path, required=True, help="the directory the SVGs go to")
    a = p.parse_args()
    d = json.loads(a.replay.read_text())
    S = d["sessions"]
    s = next(s for s in S if Path(s["path"]).name.startswith(a.session))
    a.out.mkdir(parents=True, exist_ok=True)
    for theme, t in charts.THEMES.items():
        session_chart(s, t, a.out / f"session-{theme}.svg")
        context_chart(S, t, a.out / f"context-{theme}.svg")
        cost_chart(S, t, a.out / f"cost-{theme}.svg")

    n = sum(s["as run"]["n"] for s in S)
    calls = sum(s["as run"].get("n_judge", 0) for s in S)
    w, wo = sum(total(s["as run"]) for s in S), sum(total(s["no mod"]) for s in S)
    over = {arm: sum(c >= LONG for s in S for c in s[arm]["trace"]) / n for arm in ("as run", "no mod")}
    mean = {arm: sum(s[arm]["ctx"] for s in S) / n for arm in ("as run", "no mod")}
    print(f"{len(S)} sessions, {n:,.0f} requests, {d['since'][:16]}Z to {d['until'][:16]}Z; "
          f"logged ${sum(s['logged'] for s in S):,.0f}")
    print(f"cost: ${wo:,.0f} without, ${w:,.0f} with ({w / wo - 1:+.1%}); judge ${sum(s['as run'].get('judge', 0) for s in S):,.0f}, "
          f"{calls:.0f} calls ({calls / n:.2%} of requests)")
    print(f"mean context {mean['no mod'] / 1000:.0f}k without, {mean['as run'] / 1000:.0f}k with; "
          f"requests at {LONG // 1000}k+: {over['no mod']:.0%} without, {over['as run']:.0%} with")
    asked = [c for s in S for c in s["checks"]]
    print(f"turn ends asked about: {len(asked)}, compacted after {sum(c for _, c in asked)}")
    print(f"session chart: {Path(s['path']).name}, {len(s['as run']['trace'])} requests, "
          f"${total(s['no mod']):,.0f} without, ${total(s['as run']):,.0f} with; "
          f"kept going at {sum(not c for _, c in s['checks'])} of {len(s['checks'])} turn ends asked about")
    for n, reason in enumerate(s["reasons"], 1):
        print(f"  {n}. {reason}")


if __name__ == "__main__":
    main()
