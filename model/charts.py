"""Draws the charts of docs/cost-model.md, in a light and a dark theme, and returns the figures
the doc quotes from them, which docs.py writes to docs/figures.json's `charts` section. docs.py
draws each of these as model-<name>-{light,dark}.svg, from one read of the sessions:

  cycles         the context and each request's cost over 600 requests, at four compaction thresholds, as a
                 schematic: axes in the model's symbols
  inputs         a request's cost and a compaction's cost by part while the cache holds the context, against
                 the context, the cache write after a compaction split by what fills the context
                 (context.py's measurement)
  payback        how many later requests a compaction takes to pay for itself, against the context it
                 compacts, for the README's Cost saving
  lapse          a compaction's cost by part after the prompt cache lapses, against the context, in the form
                 of inputs' compaction panel
  average        a request's mean cost over a cycle against the compaction threshold, with the marginal cost,
                 as a schematic
  effectiveness  a request's effectiveness against its context, at the base case for β (fit.BASE),
                 at half and twice it, and at β = 0
  cost           the steady state's cost per unit of effectiveness against the compaction threshold, at the
                 same four β, with each one's lowest point, under a request's mean cost and mean
                 effectiveness, the averages it is the ratio of, on one panel, all relative to Claude Code's default
  sensitivity    how far the most efficient threshold moves with each measured parameter and with β, at the
                 base case
  threshold      the most efficient threshold against β, in the steady state and the replay
  voi            the expected value of a classifier's information, on three mixes of work

It also returns, drawing nothing, what a classifier saves where work differs in the detail a
compaction loses rather than in β (voi_lost).
"""

import html
from dataclasses import replace

import matplotlib

matplotlib.use("Agg")
import fit
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Patch

plt.rcParams.update(
    {
        "svg.fonttype": "none",
        "font.family": ["Helvetica", "Arial", "DejaVu Sans"],
        "svg.hashsalt": "sparkjoy",
    }
)

# "page" is GitHub's background in each theme. Text in "fit" names what is measured in my
# sessions; its marks stay neutral, so the colour says where a number comes from without drawing the
# eye. "cost" colours what the charts count in dollars. Two ordinal ramps run from faint to strong
# against the page: "beta" for β, an assumption, and "costs" for magnitudes of a cost. "beta" is the
# dataviz reference palette's blue, "costs" OKLCH steps of its aqua, each validated as an ordinal
# ramp against its page (validate_palette.js --ordinal). "parts" are the steps, faint to strong, that
# `parts` takes from the page toward "cost" to colour a cost's parts. "lapse" colours the part a lapse of the
# cache adds, the cache write of the context, apart from the parts that are there anyway; it is a step of "beta"'s
# hue, validated against its neighbouring parts (validate_palette.js). "with" is sparkjoy, in the logo's pink,
# and "without" is Claude Code's default, in Anthropic's orange (#d97757, a step darker on the dark page
# to stay in its lightness band), validated as a categorical pair against each page.
THEMES = {
    "light": {
        "ink": "#0b0b0b",
        "muted": "#52514e",
        "grid": "#e4e3df",
        "page": "#ffffff",
        "fit": "#a3500c",
        "cost": "#1baf7a",
        "beta": ("#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"),
        "costs": ("#22b37d", "#088a5e", "#035f40"),
        "parts": (0.2, 0.36, 0.7),
        "lapse": "#5799e9",
        "band": 0.14,
        "with": "#bf397a",
        "without": "#d97757",
    },
    "dark": {
        "ink": "#f0efec",
        "muted": "#c3c2b7",
        "grid": "#3a3936",
        "page": "#0d1117",
        "fit": "#e39457",
        "cost": "#199e70",
        "beta": ("#256abf", "#3987e5", "#6da7ec", "#9ec5f4", "#cde2fb"),
        "costs": ("#049165", "#43ba8a", "#75e8b6"),
        "parts": (0.3, 0.5, 0.9),
        "lapse": "#2c64a7",
        "band": 0.18,
        "with": "#bf397a",
        "without": "#d57353",
    },
}
RAMP = (0, 0.3, 0.6, 1, 2)  # the β each step of the "beta" ramp stands for


def tint(color, page, share):
    a, b = (tuple(int(c[i : i + 2], 16) for i in (1, 3, 5)) for c in (color, page))
    return "#" + "".join(
        f"{round(x * share + y * (1 - share)):02x}" for x, y in zip(a, b, strict=True)
    )


def shade(t, beta):
    """The colour of a β: its step of the ramp, or between the two steps around it."""
    rgb = np.array([[int(h[i : i + 2], 16) for i in (1, 3, 5)] for h in t["beta"]])
    return "#" + "".join(
        f"{round(np.interp(beta, RAMP, rgb[:, k])):02x}" for k in range(3)
    )


def parts(t):
    """The colours of a cost's parts, one hue from faint to strong, in the order they stack."""
    return [tint(t["cost"], t["page"], v) for v in t["parts"]]


def style(ax, t):
    ax.set_facecolor("none")
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(t["muted"])
    ax.spines["bottom"].set_linewidth(0.8)
    ax.tick_params(colors=t["muted"], length=0, labelsize=10)
    ax.tick_params(
        axis="x", pad=7
    )  # clear of the y-axis's lowest label where the two meet
    ax.grid(axis="y", color=t["grid"], lw=0.8)
    ax.set_axisbelow(True)


def axis_name(ax, text, t):
    """Name the y-axis with its own label, set horizontal above the axis rather than rotated beside it;
    `save` lines it up with the tick labels' left edge once their widths are known."""
    ax.set_ylabel(
        text, rotation=0, ha="left", va="bottom", fontsize=10, color=t["muted"]
    )


def place_axis_names(fig):
    """Put each horizontal y-axis label just above its axis, its left edge on the tick labels'."""
    renderer = fig.canvas.get_renderer()
    fig.canvas.draw()
    for ax in fig.axes:
        if not ax.get_ylabel() or ax.yaxis.label.get_rotation() != 0:
            continue
        lefts = [
            t.get_window_extent(renderer).x0
            for t in ax.get_yticklabels()
            if t.get_text()
        ]
        x = min(lefts, default=ax.get_window_extent(renderer).x0)
        ax.yaxis.set_label_coords(ax.transAxes.inverted().transform((x, 0))[0], 1.04)


def label_ends(ax, items, gap):
    """Name lines in the margin right of the plot, each beside its line's end: `items` are
    (y, text, colour). Labels nearer than `gap` in data units are spread apart about their middle,
    and a label moved off its line's level gets a short leader to the line's end."""
    items = sorted(items)
    ys = [y for y, _, _ in items]
    for _ in range(100):
        for i in range(1, len(ys)):
            short = gap - (ys[i] - ys[i - 1])
            if short > 0:
                ys[i - 1] -= short / 2
                ys[i] += short / 2
    for (y0, text, color), y in zip(items, ys, strict=True):
        leader = {
            "arrowstyle": "-",
            "color": color,
            "lw": 0.6,
            "shrinkA": 1,
            "shrinkB": 1,
        }
        ax.annotate(
            text,
            (1, y0),
            xycoords=("axes fraction", "data"),
            xytext=(1.025, y),
            textcoords=("axes fraction", "data"),
            va="center",
            fontsize=9,
            color=color,
            arrowprops=leader if abs(y - y0) > gap * 0.2 else None,
        )


def tokens(v, _=None):
    """A count of tokens: 0, 500k, 1.5M."""
    return "0" if not v else f"{v / 1e6:g}M" if abs(v) >= 1e6 else f"{v / 1e3:.0f}k"


def dollars(v, _=None):
    """A cost: $0, $0.10, $1.20."""
    return "$0" if not v else f"${v:.2f}"


def percent(v, _=None):
    return f"{v:.0%}".replace("-", "−")


def save(fig, path, labels=None):
    """Save a drawing. `labels` maps the gid of a part a reader can point at to the words that name it,
    written as its aria-label, since matplotlib writes none."""
    place_axis_names(fig)
    fig.savefig(path, transparent=True, bbox_inches="tight", metadata={"Date": None})
    plt.close(fig)
    if labels:
        s = path.read_text()
        for gid, label in labels.items():
            tag = f'<g id="{gid}">'
            assert s.count(tag) == 1, tag
            s = s.replace(tag, f'<g id="{gid}" aria-label="{html.escape(label)}">')
        path.write_text(s)


def cycle(p, c, X):
    """A cycle compacting at X: its requests, and a request's mean cost, its share of the compaction
    included, which is J at β = 0."""
    return (X - p.K) / p.g, fit.per_unit(p, c, X, 0)


def cycles(out, p, c):
    """The trade a compaction makes, request by request: the context, and a request's cost with its
    share of the compaction that ends its cycle, at each of fit.points, which are also points on the
    average chart's curve. It assumes nothing about effectiveness. The doc draws it under The model, so
    its axes carry the model's symbols and no figures; the shapes come from the fitted values. The column
    titles say where each column
    compacts against X*(0), so the peaks carry no labels but the last, where Claude Code compacts."""
    h, c0, F0 = fit.terms(p, c)
    R = 600  # requests drawn
    X = fit.points(p, c)
    rows = [cycle(p, c, x) for x in X]

    r = np.arange(R + 1)
    titles = (
        "Compact early",
        "Compact at X*(0)",
        "Compact late",
        "Claude Code's default",
    )
    for theme, t in THEMES.items():
        fig, axes = plt.subplots(
            2,
            len(X),
            figsize=(9.6, 4.8),
            sharex=True,
            sharey="row",
            gridspec_kw={"hspace": 0.55, "wspace": 0.08},
        )
        grey = tint(t["cost"], t["page"], 0.36)
        share_col = tint(t["cost"], t["page"], 0.75)
        for col, (x, (n, cost)) in enumerate(zip(X, rows, strict=True)):
            # Everything drawn is linear in the request between the context's drops, so the first and
            # last requests and those either side of each drop draw the same lines as all R + 1.
            drop = np.flatnonzero(np.diff(np.mod(r, n)) < 0)
            at = np.unique(np.concatenate([[0, R], drop, drop + 1]))
            ctx = p.K + p.g * np.mod(at, n)
            top, low = axes[:, col]
            for ax in (top, low):
                style(ax, t)
                ax.set_xlim(0, R)
                ax.set_xticks([])
            top.plot(at, ctx, color=t["ink"], lw=1.2)
            top.set_title(
                titles[col],
                color=t["ink"],
                fontsize=10,
                loc="left",
                pad=24,
                fontweight="bold",
            )
            if x == fit.AUTO:
                # The session never compacts, so the drop is Claude Code's: named above it, ending at it.
                top.text(
                    n + 10,
                    x + 30_000,
                    "Claude Code compacts",
                    fontsize=9,
                    color=t["ink"],
                    va="bottom",
                    ha="right",
                )
            each = h * ctx + c0
            share = (x + F0) / n
            low.fill_between(
                at, 0, each, color=grey, lw=0, label="the request's own cost"
            )
            low.fill_between(
                at,
                each,
                each + share,
                color=share_col,
                lw=0,
                label="its share of the compaction ending its cycle",
            )
            low.axhline(cost, color=t["muted"], lw=0.9, label="mean over a cycle")
        axes[0, 0].set_ylim(0, 1_100_000)
        axes[0, 0].set_yticks([0, p.K], ["0", "K"])
        axes[1, 0].set_ylim(0, 1_600_000)
        axes[1, 0].set_yticks([0], ["0"])
        for row, text in enumerate(("Context (tokens)", "Cost per request")):
            axis_name(axes[row, 0], text, t)
        fig.supxlabel("Requests", color=t["muted"], fontsize=10, y=0.02)
        handles, labels = axes[1, 0].get_legend_handles_labels()
        fig.legend(
            handles,
            labels,
            frameon=False,
            fontsize=9,
            labelcolor=t["ink"],
            ncol=1,
            loc="upper left",
            bbox_to_anchor=(0.11, -0.01),
        )
        save(fig, out / f"model-cycles-{theme}.svg")


def written(p, parts):
    """What the cache write after a compaction writes, K - P tokens in all, by where it comes from:
    each group's mean tokens per compaction in `parts`, context.py's measurement of the same sessions,
    scaled so the groups sum to K - P. The measurement's first request plus E comes within about 2% of
    K - P, which takes medians where the measurement takes means."""
    first = {k: v["mean"]["value"] for k, v in parts["first_request"].items()}
    groups = {
        "files, re-attached or read again": ("files", "E"),
        "skills, instructions and memory": (
            "skill_bodies",
            "skill_listing",
            "user_claude_md",
            "project_instructions",
            "memory_index",
            "instructions_wrapper",
        ),
    }
    sizes = {
        what: sum(parts["E_total"] if k == "E" else first[k] for k in keys)
        for what, keys in groups.items()
    }
    taken = {k for keys in groups.values() for k in keys}
    sizes = {
        "the summary and the rest": sum(v for k, v in first.items() if k not in taken),
        **sizes,
    }
    total = sum(sizes.values())
    return [(what, (p.K - p.P) * v / total) for what, v in sizes.items()]


def compaction_parts(x, p, c, parts):
    """F(x) = x + F0 by part, in reads, as (name, values, fitted, colour): the parts that don't grow with
    the context first, so they stack at the bottom. A part is fitted when it comes from what the sessions
    measured (output, growth, lapses, K, P, N); the cache read of the context is the context itself. The
    colour is a step of the parts ramp, output 0, cache writes 1, the cache read of the context 2, or the
    name of a theme colour, "lapse" for the cache write a lapse adds."""
    w1 = c.write - 1
    return [
        ("output of the compaction request", np.full_like(x, c.output * p.N), True, 0),
        *[
            (f"cache write of {what}", np.full_like(x, w1 * tokens), True, 1)
            for what, tokens in written(p, parts)
        ],
        ("cache read of the context", x, False, 2),
    ]


def bands(ax, x, stack, cap, step, t, c):
    """Draw `stack`'s parts as bands stacked from the axis, in dollars, in the colour each part names
    (compaction_parts), each band parted from the next by the page. Each part is named at the right end of its
    band, in the fit colour where the sessions measured it, thin bands' names pushed apart to stay legible:
    up from the bottom, then down from the cap where that pushed one past it. A name moved off its band's
    middle has a leader to it."""
    ramp = parts(t)
    base = np.zeros_like(x)
    ys = []
    for label, v, _, k in stack:
        v = v * c.read
        ax.fill_between(
            x,
            base,
            base + v,
            fc=t[k] if isinstance(k, str) else ramp[k],
            ec=t["page"],
            lw=1.2,
            label=label,
        )
        ys.append(base[-1] + v[-1] / 2)
        base = base + v
    mids, gap = list(ys), 0.1 * cap
    for i in range(1, len(ys)):
        ys[i] = max(ys[i], ys[i - 1] + gap)
    ys[-1] = min(ys[-1], cap - gap / 2)
    for i in range(len(ys) - 2, -1, -1):
        ys[i] = min(ys[i], ys[i + 1] - gap)
    for (label, _, fitted, _), y, mid_y in zip(stack, ys, mids, strict=True):
        if abs(y - mid_y) > 0.02 * cap:
            ax.plot(
                [fit.AUTO + 6_000, fit.AUTO + 30_000],
                [mid_y, y],
                color=t["muted"],
                lw=0.8,
                clip_on=False,
            )
        ax.text(
            fit.AUTO + 36_000,
            y,
            label,
            va="center",
            fontsize=9,
            color=t["fit"] if fitted else t["ink"],
        )
    ax.set_xlim(0, fit.AUTO)
    ax.set_ylim(0, cap)
    ax.set_yticks(np.arange(0, cap, step))
    ax.yaxis.set_major_formatter(dollars)


def inputs(out, p, c, parts):
    """What the costs are made of while the cache holds the context: a request's cost at its context
    by part, and a compaction's cost by part, on one axis. The request is one that finds the cache
    holding its context, c(x) at ℓ = 0: the share ℓ of requests that find it lapsed is an average
    that h carries, not a part of any one request. Parts that come from the sessions' fitted
    parameters are named in the fit colour. Returns each part's cost in dollars, and the total, for a
    request and a compaction at K and 700k."""
    _, c0, F0 = fit.terms(replace(p, lapse=0), c)
    # Every part is linear in x, so the ends draw the same bands as any finer grid; K and 700k are
    # where the figures below are read.
    x = np.unique([1_000, p.K, 700_000, fit.AUTO])
    # c(x) = x + c0 at ℓ = 0 by part, in the form of compaction_parts.
    request = [
        (
            "output and uncached input",
            np.full_like(x, c.output * p.out + c.input * p.inp),
            True,
            0,
        ),
        (
            "cache write of the new tokens",
            np.full_like(x, (c.write - 1) * p.g),
            True,
            1,
        ),
        ("cache read of the context", x, False, 2),
    ]
    compaction = compaction_parts(x, p, c, parts)
    assert np.allclose(sum(v for _, v, _, _ in request), x + c0)
    assert np.allclose(sum(v for _, v, _, _ in compaction), x + F0)
    figures = {
        name: {
            at: {
                **{k: val[j] * c.read for k, val, _, _ in stack},
                "total": sum(val[j] for _, val, _, _ in stack) * c.read,
            }
            for at, j in (
                ("K", np.abs(x - p.K).argmin()),
                ("700k", np.abs(x - 700_000).argmin()),
            )
        }
        for name, stack in (("request_usd", request), ("compaction_usd", compaction))
    }

    for theme, t in THEMES.items():
        fit_c = t["fit"]
        fig, (mid, low) = plt.subplots(
            2, 1, figsize=(9, 5.6), sharex=True, gridspec_kw={"hspace": 0.32}
        )
        for ax in (mid, low):
            style(ax, t)
        bands(mid, x, request, 0.3, 0.1, t, c)
        bands(low, x, compaction, 1.3, 0.4, t, c)
        low.xaxis.set_major_formatter(tokens)
        low.set_xticks([0, 200_000, 400_000, 600_000, 800_000])
        low.set_xlabel(
            "Context x (tokens) before the request or the compaction",
            color=t["muted"],
            fontsize=10,
        )
        axis_name(mid, "Cost per request by part, with the cache warm ($)", t)
        axis_name(low, "Cost per compaction F(x) by part ($)", t)
        mid.text(
            1,
            1.04,
            "parts named in orange are fitted to the sessions",
            transform=mid.transAxes,
            ha="right",
            va="bottom",
            fontsize=9,
            color=fit_c,
        )
        save(fig, out / f"model-inputs-{theme}.svg")
    return figures


def payback(out, p, c):
    """How many later requests a compaction takes to pay for itself, against the context x it compacts:
    its cost, x + F0 cached reads, over what each later request saves, h (x - K), with every request
    alike (β = 0). Each later request reads x - K less, so the curve rises without bound as x falls to
    K, which a line marks. Dots mark the three sizes the README's Cost saving names. Returns, for each
    size the docs name, the requests a compaction there takes to pay for itself, what it costs, how much
    less each later request reads, and how many requests the context takes to grow back."""
    h, _, F0 = fit.terms(p, c)
    X = np.arange(p.K + 1_000, fit.AUTO + 1, 1_000.0)
    N = (X + F0) / (h * (X - p.K))
    marks = (200_000, 400_000, 700_000)
    sizes = (200_000, 250_000, 300_000, 400_000, 500_000, 600_000, 700_000)
    each = {x: int(np.ceil((x + F0) / (h * (x - p.K)))) for x in sizes}
    figures = {
        tokens(x): {
            "requests": each[x],
            "compaction_usd": (x + F0) * c.read,
            "reads_less": x - p.K,
            "grows_back_in": (x - p.K) / p.g,
        }
        for x in sizes
    }
    top = 100
    for theme, t in THEMES.items():
        fig, ax = plt.subplots(figsize=(9, 3))
        style(ax, t)
        ax.axvline(p.K, color=t["grid"], lw=1.2, zorder=1)
        ax.text(
            p.K + 12_000,
            3,
            f"{p.K / 1e3:.0f}k, the context after a compaction",
            ha="left",
            va="bottom",
            fontsize=9,
            color=t["fit"],
        )
        ax.plot(X, N, color=t["ink"], lw=2, zorder=2)
        for x in marks:
            ax.plot(
                [x],
                [each[x]],
                "o",
                ms=7,
                mfc=t["ink"],
                mec=t["page"],
                mew=1.5,
                zorder=3,
            )
            ax.text(
                x + 14_000,
                each[x] + 4,
                f"{each[x]} requests at {x / 1e3:.0f}k,\neach reading "
                f"{(x - p.K) / 1e3:.0f}k less",
                ha="left",
                va="bottom",
                fontsize=9,
                color=t["ink"],
            )
        ax.set_xlim(0, fit.AUTO)
        ax.set_ylim(0, top)
        ax.xaxis.set_major_formatter(tokens)
        ax.set_xticks([0, 250_000, 500_000, 750_000])
        ax.set_yticks([0, 25, 50, 75, 100])
        ax.set_xlabel(
            "Context compacted at a turn end (tokens)", color=t["muted"], fontsize=10
        )
        axis_name(ax, "Later requests until the compaction pays for itself", t)
        save(fig, out / f"model-payback-{theme}.svg")
    return figures


def lapse(out, p, c, parts):
    """What a compaction costs after the prompt cache lapses, as in a session left idle past the cache's
    lifetime, by part, in the form of inputs' compaction panel. The compaction request first writes all of
    the context but the shared prefix P to the cache, so on top of F(x)'s parts it pays the part a request
    pays after a lapse, the cache write of the context, (w - 1)(x - P) reads. Returns, at each size it
    marks, the compaction's cost while the cache holds the context and after it lapses, and how many
    tokens may follow the first part of a context that is dropped in place of compacting it, such as old
    tool results, before the next request's cache write of them costs as much as the compaction."""
    marks = (200_000, 500_000, fit.AUTO)
    x = np.unique([1_000, p.P, p.K, *marks, fit.AUTO])
    held = compaction_parts(x, p, c, parts)
    lapse_write = (c.write - 1) * np.maximum(x - p.P, 0)
    stack = [
        *held[:-1],
        ("cache write of the context after a lapse", lapse_write, True, "lapse"),
        held[-1],
    ]
    _, _, F0 = fit.terms(p, c)
    after = x > p.P
    total = sum(v for _, v, _, _ in stack)
    assert np.allclose(total[after], (p.P + c.write * (x - p.P) + F0)[after])
    figures = {}
    for v in marks:
        j = np.abs(x - v).argmin()
        figures[tokens(v)] = {
            "warm_usd": (total[j] - lapse_write[j]) * c.read,
            "lapsed_usd": total[j] * c.read,
            "drop_break_even": (v + F0) / (c.write - 1),
        }
    for theme, t in THEMES.items():
        fig, ax = plt.subplots(figsize=(9, 2.8))
        style(ax, t)
        bands(ax, x, stack, 9, 2, t, c)
        ax.xaxis.set_major_formatter(tokens)
        ax.set_xticks([0, 200_000, 400_000, 600_000, 800_000])
        ax.set_xlabel(
            "Context x (tokens) before the compaction", color=t["muted"], fontsize=10
        )
        axis_name(ax, "Cost per compaction after a lapse by part ($)", t)
        ax.text(
            1,
            1.04,
            "parts named in orange are fitted to the sessions",
            transform=ax.transAxes,
            ha="right",
            va="bottom",
            fontsize=9,
            color=t["fit"],
        )
        save(fig, out / f"model-lapse-{theme}.svg")
    return figures


def average(out, p, c):
    """Why the most efficient threshold sits where it does when context size doesn't matter: against the
    compaction threshold X, a request's cost averaged over the cycle, its share of the compaction included,
    and the marginal cost, what one more request before compacting costs. The average is least where
    they cross, which a dot marks. It draws the model in symbols, so its axes carry K and X*(0), the
    most efficient threshold counting cost alone, and no figures."""
    h, c0, _ = fit.terms(p, c)
    X, A = fit.curve(
        p, c, 0
    )  # with every request alike, cost per unit of effectiveness is cost per request
    # d/dX of the cycle's cost, g (X + F0) + h (X² - K²)/2 + c0 (X - K), over d/dX of its requests: the
    # next request's cost, plus the g tokens more the compaction will read.
    M = h * X + c0 + p.g
    j = A.argmin()
    cross = np.abs(M - A)[: len(X) - 1].argmin()
    figures = {
        "least_at": X[j],
        "least_usd": A[j] * c.read,
        "marginal_crosses_at": X[cross],
    }
    best = fit.points(p, c)[1]
    least = cycle(p, c, best)[
        1
    ]  # exactly, as the cycles chart computes it, rather than off the 1k grid
    default = cycle(p, c, fit.AUTO)[
        1
    ]  # Claude Code's default, compacting near the window's end

    for theme, t in THEMES.items():
        fig, ax = plt.subplots(figsize=(9, 3.4))
        style(ax, t)
        ax.plot(X, A, color=t["cost"], lw=2.2)
        ax.plot(X, M, color=t["muted"], lw=1.6)
        label_ends(
            ax,
            [(M[-1], "marginal", t["ink"]), (A[-1], "average", t["ink"])],
            gap=80_000,
        )
        ax.plot(
            [best], [least], "o", ms=7, mfc=t["cost"], mec=t["page"], mew=1.5, zorder=3
        )
        ax.text(
            best + 20_000,
            least - 45_000,
            "min(cost) at X*(0)",
            ha="left",
            va="top",
            fontsize=9,
            color=t["ink"],
        )
        # Unclipped, so the dot on the plot's right edge draws whole.
        ax.plot(
            [fit.AUTO],
            [default],
            "o",
            ms=6,
            mfc=t["page"],
            mec=t["cost"],
            mew=1.5,
            zorder=3,
            clip_on=False,
        )
        ax.text(
            fit.AUTO - 10_000,
            default - 150_000,
            "Claude Code's default,\nnear the window's end",
            ha="right",
            va="top",
            fontsize=9,
            color=t["ink"],
        )
        ax.set_xlim(0, fit.AUTO)
        ax.set_xticks([0, p.K, best], ["0", "K", "X*(0)"])
        ax.set_ylim(0, 1_400_000)
        ax.set_yticks([0], ["0"])
        ax.set_xlabel("Compaction threshold X (tokens)", color=t["muted"], fontsize=10)
        axis_name(ax, "Cost per request", t)
        save(fig, out / f"model-average-{theme}.svg")
    return figures


def effectiveness_curve(out):
    """A request's effectiveness against its context, (x / 1M)^β, at the base case and at the β the
    sensitivity chart takes it to (halved and doubled), with β = 0 for reference, each curve named beside it.
    The base case is marked by its thicker line alone: "the base case" fits beside its curve nowhere that the
    curve below leaves clear."""
    x = np.linspace(0, fit.WINDOW, 1_000)
    betas = (0, fit.BASE / 2, fit.BASE, fit.BASE * 2)
    for theme, t in THEMES.items():
        fig, ax = plt.subplots(figsize=(9, 3.8))
        style(ax, t)
        for b in betas:
            color = shade(t, b)
            base = b == fit.BASE
            # Unclipped: each curve starts on the plot's left edge, where clipping would halve its line.
            ax.plot(
                x,
                fit.eta(x, b),
                color=color,
                lw=2.6 if base else 1.6,
                clip_on=False,
                zorder=3 if base else 2,
            )
            # Each name below its curve at 150k, where the curves are far apart enough that no curve crosses it.
            ax.text(
                164_000,
                float(fit.eta(150_000, b)) - 0.03,
                f"β = {b:g}",
                ha="left",
                va="top",
                fontsize=9,
                color=t["ink"],
            )
        ax.set_xlim(0, fit.WINDOW)
        ax.xaxis.set_major_formatter(tokens)
        ax.set_xticks([0, 250_000, 500_000, 750_000, 1_000_000])
        ax.yaxis.set_major_formatter(percent)
        ax.set_yticks([0, 0.25, 0.5, 0.75, 1])
        ax.set_ylim(0, 1.04)
        ax.set_xlabel("Context x (tokens)", color=t["muted"], fontsize=10)
        axis_name(ax, "Effectiveness η(x), relative to a request at 1M", t)
        save(fig, out / f"model-effectiveness-{theme}.svg")


def cost_curve(out, p, c):
    """The steady state's cost per unit of effectiveness against the compaction threshold, relative to Claude Code's
    default, at the β the effectiveness chart draws, the base case thicker, under the two averages it is
    the ratio of, on one panel and relative to Claude Code's default too: a request's mean cost over a cycle, its
    share of the compaction included, which no β changes, and its mean effectiveness, at each β. Dividing
    both by Claude Code's default compares an average with an average, and puts the cost line below a β's line
    wherever compacting there costs less per unit than Claude Code's default. A dot marks the lowest point of
    each β's cost per unit (fit.steady), and each line is named by its β alone, since the axes give the
    points' values. Returns, relative to Claude Code's default, the cost's lowest point, and for each β
    the effectiveness at K, the threshold from which the cost line is below the effectiveness line, and
    the lowest cost per unit, where a threshold short of fit.AUTO pays."""
    betas = (0, fit.BASE / 2, fit.BASE, fit.BASE * 2)
    X, A = fit.curve(p, c, 0)
    cost = A / A[-1]
    figures = {"cost_least": {"at": X[cost.argmin()], "relative": cost.min()}}
    for b in betas:
        mean = fit.cycle(p, c, X, b)[1] / (X - p.K)
        below = cost[:-1] < (mean / mean[-1])[:-1]
        Xb, J = fit.curve(p, c, b)
        best = fit.steady(p, c, b)
        figures[f"beta_{b:g}"] = {
            "effectiveness_at_K": mean[0] / mean[-1],
            "cost_below_from": X[np.argmax(below)] if below.any() else np.inf,
            "least": None
            if best >= fit.AUTO
            else {"at": best, "relative": J[Xb >= best][0] / J[-1]},
        }
    for theme, t in THEMES.items():
        fig, (top, low) = plt.subplots(
            2,
            1,
            figsize=(9, 6.8),
            sharex=True,
            gridspec_kw={"height_ratios": (1.3, 1.4), "hspace": 0.3},
        )
        for ax in (top, low):
            style(ax, t)

        # A request's mean effectiveness at each β, the cycle's effectiveness over its requests, and its mean
        # cost, the same at every β, each relative to Claude Code's default.
        X, A = fit.curve(p, c, 0)
        for b in betas:
            base = b == fit.BASE
            mean = fit.cycle(p, c, X, b)[1] / (X - p.K)
            top.plot(
                X,
                mean / mean[-1],
                color=shade(t, b),
                lw=2.6 if base else 1.6,
                zorder=3 if base else 2,
            )
            # Each name left of its line's start, where the lines are furthest apart; they all end at 100%.
            top.text(
                X[0] - 10_000,
                float(mean[0] / mean[-1]),
                f"β = {b:g}",
                ha="right",
                va="center",
                fontsize=9,
                color=t["ink"],
            )
        top.plot(X, A / A[-1], color=t["cost"], lw=2.6, zorder=4)
        # The name right of the cost line's steep fall, above every other line.
        top.text(
            172_000, 1.2, "cost", ha="left", va="center", fontsize=9, color=t["ink"]
        )
        top.yaxis.set_major_formatter(percent)
        top.set_ylim(0, 1.3)
        top.set_yticks([0, 0.25, 0.5, 0.75, 1, 1.25])
        axis_name(
            top,
            "Cost and effectiveness per request, averaged over a cycle, relative to Claude Code's default",
            t,
        )

        # Their ratio, relative to Claude Code's default.
        for b in betas:
            color = shade(t, b)
            base = b == fit.BASE
            X, J = fit.curve(p, c, b)
            y = J / J[-1]
            low.plot(
                X, y, color=color, lw=2.6 if base else 1.6, zorder=3 if base else 2
            )
            best = fit.steady(p, c, b)
            if best >= fit.AUTO:
                # No compaction short of Claude Code's own pays, so the name sits above the line's descent.
                x = 600_000
                low.text(
                    x,
                    float(np.interp(x, X, y)) + 0.03,
                    f"β = {b:g}",
                    ha="left",
                    va="bottom",
                    fontsize=9,
                    color=t["ink"],
                )
                continue
            least = float(J[X >= best][0] / J[-1])
            low.plot(
                [best],
                [least],
                "o",
                ms=6.5,
                mfc=color,
                mec=t["page"],
                mew=1.5,
                zorder=4,
            )
            # Each name below its dot, but the base case's above it, since below it the name would cross the
            # flatter β = 0.25 line.
            if base:
                low.text(
                    best,
                    least + 0.04,
                    f"β = {b:g}, the base case",
                    ha="center",
                    va="bottom",
                    fontsize=9,
                    color=t["ink"],
                )
            else:
                low.text(
                    best + 14_000,
                    least - 0.015,
                    f"β = {b:g}",
                    ha="left",
                    va="top",
                    fontsize=9,
                    color=t["ink"],
                )
        low.set_xlim(0, fit.AUTO)
        low.xaxis.set_major_formatter(tokens)
        low.set_xticks([0, 200_000, 400_000, 600_000, 800_000])
        low.yaxis.set_major_formatter(percent)
        low.set_ylim(0.4, 1.3)
        low.set_yticks([0.4, 0.6, 0.8, 1, 1.2])
        low.set_xlabel("Compaction threshold X (tokens)", color=t["muted"], fontsize=10)
        axis_name(
            low, "Cost per unit of effectiveness, relative to Claude Code's default", t
        )
        save(fig, out / f"model-cost-{theme}.svg")
    return figures


def sensitivity(out, p, c, boot):
    """How far the most efficient threshold moves at the base case for β when each parameter measured in the
    sessions is half or twice its value, and across its 90% interval over `boot`, the sessions resampled,
    and when β is half or twice the base case (fit.sensitivity), with a line at the most efficient threshold
    with every parameter as measured. β, which isn't measured, has no interval. Each bar is labelled for a reader to
    point at."""
    b = fit.BASE
    names = {
        "K": f"context after a compaction ({tokens(p.K)})",
        "g": f"growth of the context per request ({p.g / 1e3:.1f}k)",
        "P": f"size of the system prompt and tools ({p.P / 1e3:.1f}k)",
        "lapse": f"share of requests that find the cache lapsed ({p.lapse:.2%})",
        "N": f"output of the compaction request ({p.N / 1e3:.1f}k)",
        "β": f"growth of effectiveness with the context ({b:g}, not measured)",
    }
    symbols = {**fit.MEASURED, "β": "β"}
    rows = fit.sensitivity(p, c, boot, b)
    base = fit.steady(p, c, b)
    # The measured parameters from the bottom up by how far halving and doubling them moves the point, then β.
    order = [*sorted(fit.MEASURED, key=lambda f: abs(rows[f][1] - rows[f][0])), "β"]
    k = lambda v: f"{v / 1e3:.0f}k"
    labels = {
        f"sens-{f}": f"{symbols[f]}, {names[f]}: {k(rows[f][0])} at half, {k(rows[f][1])} at twice"
        + (
            ""
            if rows[f][2] is None
            else f", {k(rows[f][2][0])}–{k(rows[f][2][1])} across its 90% interval"
        )
        for f in order
    }

    for theme, t in THEMES.items():
        # Each bar's name sits above it rather than as a tick label beside it, so the drawing is no wider
        # than the plot and its text keeps the size of the other charts' where the doc scales it to the column.
        fig, ax = plt.subplots(figsize=(9, 4.6))
        style(ax, t)
        ax.grid(False)
        ax.grid(axis="x", color=t["grid"], lw=0.8)
        # The bars in the base case's blue, the colour the effectiveness and cost charts give it; the line
        # at the measured point a tint of it, so the names it passes stay legible.
        ink = shade(t, b)
        ax.axvline(base, color=tint(ink, t["page"], 0.6), lw=1, zorder=1)
        ax.text(
            base + 8_000,
            len(order) - 0.3,
            f"{k(base)}, with every parameter as measured",
            ha="left",
            va="bottom",
            fontsize=9,
            color=t["ink"],
        )
        for y, f in enumerate(order):
            lo, hi, ends = rows[f]
            # Measured parameters' names in the fit colour; β's in ink, since it is assumed. The line at the
            # measured point is a tint light enough to pass behind a name.
            ax.text(
                0,
                y + 0.24,
                f"{symbols[f]}, {names[f]}",
                transform=ax.get_yaxis_transform(),
                ha="left",
                va="bottom",
                fontsize=9,
                color=t["fit"] if f in fit.MEASURED else t["ink"],
                zorder=4,
            )
            # A thin line from halved to doubled, thick across the interval.
            ax.plot(
                [lo, hi],
                [y, y],
                color=ink,
                lw=1.6,
                solid_capstyle="butt",
                zorder=2,
                gid=f"sens-{f}",
            )
            if ends is not None:
                ax.plot(ends, [y, y], color=ink, lw=6, solid_capstyle="butt", zorder=2)
            # Unclipped, so a dot on the plot's right edge, at Claude Code's own compaction, draws whole.
            ax.plot(
                [lo],
                [y],
                "o",
                ms=6,
                mfc=t["page"],
                mec=ink,
                mew=1.6,
                zorder=3,
                clip_on=False,
            )
            ax.plot(
                [hi],
                [y],
                "o",
                ms=6,
                mfc=ink,
                mec=t["page"],
                mew=1,
                zorder=3,
                clip_on=False,
            )
            ax.text(
                min(lo, hi) - 9_000,
                y,
                k(min(lo, hi)),
                ha="right",
                va="center",
                fontsize=9,
                color=t["ink"],
            )
            right = max(lo, hi)
            if right >= fit.AUTO:
                # At the plot's right edge, so named below the line's end rather than beside it.
                ax.text(
                    right,
                    y - 0.2,
                    f"{k(right)}, where Claude Code compacts",
                    ha="right",
                    va="top",
                    fontsize=9,
                    color=t["ink"],
                )
            else:
                ax.text(
                    right + 9_000,
                    y,
                    k(right),
                    ha="left",
                    va="center",
                    fontsize=9,
                    color=t["ink"],
                )
        ax.set_yticks([])
        ax.set_ylim(-0.4, len(order))
        ax.set_xlim(100_000, fit.AUTO)
        ax.set_xticks(range(200_000, 900_001, 200_000))
        ax.xaxis.set_major_formatter(tokens)
        keys = [
            plt.Line2D(
                [], [], ls="", marker="o", ms=6, mfc=t["page"], mec=ink, mew=1.6
            ),
            plt.Line2D([], [], ls="", marker="o", ms=6, mfc=ink, mec=t["page"], mew=1),
            Patch(color=ink, lw=0),
        ]
        ax.legend(
            keys,
            ["halved", "doubled", "across its 90% interval"],
            frameon=False,
            fontsize=9,
            labelcolor=t["ink"],
            ncol=3,
            loc="lower right",
            bbox_to_anchor=(1, 1.0),
            handletextpad=0.4,
            columnspacing=1.0,
            handlelength=1.2,
        )
        ax.set_xlabel(
            f"Most efficient threshold at β = {b:g} (tokens)",
            color=t["muted"],
            fontsize=10,
        )
        save(fig, out / f"model-sensitivity-{theme}.svg", labels)


def thresholds(out, cost, effectiveness, BT, BX, p, c):
    """The most efficient threshold against β: the steady-state model and the turn-end replay, with 90%
    bands. The replay's legend entry takes the fit colour, since it is measured in the sessions."""
    bs = np.array(fit.CHART_BETAS)
    X = [fit.steady(p, c, b) for b in bs]
    # Never compacting short of Claude Code's own compaction is drawn at it.
    T, BT = (np.minimum(v, fit.AUTO) for v in (fit.best(cost, effectiveness)[0], BT))
    for theme, t in THEMES.items():
        fig, ax = plt.subplots(figsize=(9, 3.6))
        style(ax, t)
        ax.axhline(fit.AUTO, color=t["muted"], lw=0.8)
        ax.text(
            bs[0] + 0.01,
            fit.AUTO + 15_000,
            f"Claude Code compacts at {tokens(fit.AUTO)}",
            ha="left",
            fontsize=9,
            color=t["muted"],
        )
        ax.axvline(fit.BASE, color=t["muted"], lw=0.8)
        ax.text(
            fit.BASE + 0.01,
            60_000,
            f"base case, β = {fit.BASE:g}",
            ha="left",
            fontsize=9,
            color=t["muted"],
        )
        ax.axhline(fit.FLOOR, color=t["muted"], lw=0.8)
        ax.text(
            bs[-1],
            fit.FLOOR + 15_000,
            f"sparkjoy's default floor, {tokens(fit.FLOOR)}",
            ha="right",
            fontsize=9,
            color=t["muted"],
        )
        # The steady state in a grey halfway to the page, so it reads apart from the replay's ink; each
        # band a tint of its line, the lighter line's tinted less so its band still shows.
        for y, band, color, share, label in (
            (
                X,
                BX,
                tint(t["muted"], t["page"], 0.55),
                2.2 * t["band"],
                "steady state, compacting at any request",
            ),
            (
                T,
                BT,
                t["ink"],
                t["band"],
                "replay of the sessions, compacting at turn ends",
            ),
        ):
            lo, hi = np.percentile(band, [5, 95], axis=0)
            ax.fill_between(bs, lo, hi, color=tint(color, t["page"], share), lw=0)
            ax.plot(bs, y, color=color, lw=2, label=label)
        ax.fill_between(
            [],
            [],
            color=tint(t["ink"], t["page"], t["band"]),
            lw=0,
            label="shaded, their 90% intervals",
        )
        ax.set_xlim(bs[0], bs[-1])
        ax.xaxis.set_major_formatter(lambda v, _: f"{v:g}")
        ax.set_ylim(0, 1_050_000)
        ax.yaxis.set_major_formatter(tokens)
        ax.set_yticks([0, 250_000, 500_000, 750_000])
        ax.set_xlabel("β, the exponent of the power law", color=t["muted"], fontsize=10)
        axis_name(ax, "Most efficient threshold (tokens)", t)
        legend = ax.legend(
            frameon=False,
            fontsize=9,
            labelcolor=t["ink"],
            loc="upper left",
            bbox_to_anchor=(0, 0.88),
        )
        legend.get_texts()[1].set_color(t["fit"])
        save(fig, out / f"model-threshold-{theme}.svg")


def voi(out, p, c):
    """What a classifier saves on three mixes of work with the same mean β, the base case's: a share of
    the work at one β > 0, the rest at β = 0, where the context doesn't help. For each mix and each
    accuracy, the classifier splits the work into a group to keep and a group to compact, each
    compacting at its own most efficient threshold, no lower than fit.FLOOR, where sparkjoy starts asking;
    the saving is against Claude Code's default, compacting at fit.AUTO, after the questions sparkjoy asks
    from fit.FLOOR, as fit.cycle counts them, and the x axis is the mutual
    information between the classifier's verdict and the kind of work, as a share of a perfect
    classifier's. With no information it compacts all the work at the most efficient single
    threshold."""
    # Denser near 1, where a classifier's saving on rare, steep work climbs fastest.
    A = np.unique(
        np.round(
            np.concatenate([np.linspace(0.5, 1, 201), 1 - np.logspace(-4, -1.6, 80)]), 6
        )
    )
    # (share of the work at β, β, step of the β ramp), each share giving a mean β of fit.BASE.
    mixes = tuple(
        (fit.BASE / beta, beta, step) for beta, step in ((1, 2), (2, 3), (5, 4))
    )

    def H(q):
        q = np.clip(q, 1e-12, 1 - 1e-12)
        return -(q * np.log2(q) + (1 - q) * np.log2(1 - q))

    # On fit.steady's 100-token grid, so the most efficient threshold of the work at β = 0 is the doc's 262k.
    X, J0 = fit.curve(p, c, 0, step=100)
    J0q = fit.curve(p, c, 0, step=100, asks=fit.FLOOR)[1]  # with sparkjoy's questions
    lines, figures = [], {}
    for share, beta, step in mixes:
        Js = np.array([J0, fit.curve(p, c, beta, step=100)[1]])
        Jq = np.array([J0q, fit.curve(p, c, beta, step=100, asks=fit.FLOOR)[1]])
        w = np.array([1 - share, share])
        single = (w[:, None] * Js).sum(0)
        default = single[-1]  # fit.curve's last point is fit.AUTO
        keep = (
            np.array([1 - A, A]).T * w
        )  # the classifier keeps the context for a share a of the β work, 1 - a of the rest
        both = np.array(
            [
                (k[:, None] * Jq).sum(0).min() + ((w - k)[:, None] * Jq).sum(0).min()
                for k in keep
            ]
        )
        saved = 1 - both / default
        info = (H(share * A + (1 - share) * (1 - A)) - H(A)) / H(share)
        lines.append((share, beta, step, saved, info))
        at = [int(np.abs(A - a).argmin()) for a in (0.8, 0.9, 0.99, 1.0)]
        # Saved against Claude Code's default.
        figures[f"{share:g}_at_beta_{beta:g}"] = {
            "single_threshold": X[single.argmin()],
            "single_saved": 1 - single.min() / default,
            "by_accuracy": {
                f"{A[i]:g}": {"saved": saved[i], "information": info[i]} for i in at
            },
            "beats_single_from": A[np.argmax(saved > 1 - single.min() / default)],
        }

    for theme, t in THEMES.items():
        fig, ax = plt.subplots(figsize=(8, 4.2))
        style(ax, t)
        ends = []
        for share, beta, step, saved, info in lines:
            ax.plot(info, saved, color=t["beta"][step], lw=2, clip_on=False)
            ends.append((saved[-1], f"{share:.0%} at β = {beta}", t["ink"]))
        label_ends(ax, ends, gap=0.02)
        ax.axhline(0, color=t["muted"], lw=0.8, zorder=1)
        ax.set_xlim(0, 1)
        ax.set_xticks([0, 0.25, 0.5, 0.75, 1])
        ax.set_ylim(-0.03, 0.32)
        ax.set_yticks([0, 0.1, 0.2, 0.3])
        ax.xaxis.set_major_formatter(percent)
        ax.yaxis.set_major_formatter(percent)
        ax.set_xlabel(
            "Accuracy (% of a perfect classifier's information)",
            color=t["muted"],
            fontsize=10,
        )
        axis_name(ax, "Saved against Claude Code's default (% of cost)", t)
        save(fig, out / f"model-voi-{theme}.svg")
    return figures


# The share of turn ends in the middle of a task, and the share u of the context that a compaction
# at one loses beyond one at a turn end that finishes a piece of work, for voi_lost. No session
# measures either; at half the turn ends and u = 5%, a compaction loses 2.5% of its context on
# average, about what calibrate.py measures after my sessions' compactions.
MID = 0.5
LOST_SHARES = (0.05, 0.1, 0.2, 0.4)


def voi_lost(p, c):
    """The expected value of a classifier's information where work differs in the detail a
    compaction loses, at the base case for β. A share MID of turn ends fall in the middle of a task,
    where a compaction loses a share u of its context, which costs ρ u x at context x (fit.redo), and
    the rest finish a piece of work, where it loses none. Without a classifier a compaction falls at a
    turn end of either kind, so it loses MID·u on average. A classifier compacts only at turn ends it
    calls finished, right at a share a of each kind, so each of its compactions loses
    u·MID(1 - a) / (MID(1 - a) + (1 - MID)a), and the threshold moves to where that costs least.
    Waiting for such a turn end moves each compaction later by about the same amount, which the
    threshold makes up for. Claude Code's own compaction at fit.AUTO falls in the middle of a turn, and
    so of a task, and loses u. Returns, for each u, the most efficient single threshold, what a
    compaction there loses, and what it saves, what a perfect classifier and one right 9 times in 10
    save after the questions they ask from fit.FLOOR, all against Claude Code's default, and the
    accuracy from which a classifier saves more than the single threshold."""
    A = np.round(np.linspace(0.5, 1, 501), 4)

    def lowest(lost, u, asks=None):
        """The most efficient threshold where a compaction at a turn end loses the share `lost`, and J
        there; one at fit.AUTO, the curve's last point, is Claude Code's and loses u."""
        X, J = fit.curve(p, c, fit.BASE, lost, step=100, asks=asks)
        J[-1] = fit.per_unit(p, c, fit.AUTO, fit.BASE, u, asks)
        return X[J.argmin()], J.min()

    figures = {"mid_task_share": MID}
    for u in LOST_SHARES:
        X, single = lowest(MID * u, u)
        default = fit.per_unit(p, c, fit.AUTO, fit.BASE, u)
        picked = u * MID * (1 - A) / (MID * (1 - A) + (1 - MID) * A)
        saved = np.array([1 - lowest(v, u, fit.FLOOR)[1] / default for v in picked])
        right = {a: saved[np.abs(A - a).argmin()] for a in (0.9, 1.0)}
        figures[f"u_{u:g}"] = {
            # What a compaction at a turn end in the middle of a task loses at the single threshold.
            "lost_usd": fit.redo(p, c) * u * X * c.read,
            "single_threshold": X,
            "single_saved": 1 - single / default,
            "perfect_saved": right[1.0],
            "right_0.9_saved": right[0.9],
            "beats_single_from": A[np.argmax(saved > 1 - single / default)],
        }
    return figures


def draw(out, p, c, boot, cost, effectiveness, BT, BX, parts):
    """Draw each chart the docstring lists into `out`, and return the figures the doc quotes from them,
    by chart, from the parameters p of the sessions and `boot`, the parameters of each resample of them,
    the prices c, the replay's cost and effectiveness with BT and BX, the most efficient turn-end
    threshold and steady-state threshold of each resample, and `parts`, context.py's measurement."""
    out.mkdir(parents=True, exist_ok=True)
    cycles(out, p, c)
    figures = {
        "inputs": inputs(out, p, c, parts),
        "payback": payback(out, p, c),
        "lapse": lapse(out, p, c, parts),
        "average": average(out, p, c),
    }
    effectiveness_curve(out)
    figures["cost"] = cost_curve(out, p, c)
    sensitivity(out, p, c, boot)
    thresholds(out, cost, effectiveness, BT, BX, p, c)
    figures["voi"] = voi(out, p, c)
    figures["voi_lost"] = voi_lost(p, c)
    return figures
