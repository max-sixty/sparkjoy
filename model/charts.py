"""Draws the charts of docs/cost-model.md, in a light and a dark theme, and prints the figures
the doc quotes from them. fit.py's --charts draws each of these as model-<name>-{light,dark}.svg,
from one read of the sessions:

  cycles         the context and each request's cost over 600 requests, at four compaction points, as a
                 schematic: axes in the model's symbols
  inputs         a request's cost and a compaction's cost by part, against the context, the cache write
                 after a compaction split by what fills the context (context.py's context-parts.json)
  payback        how many later requests a compaction takes to pay for itself, against the context it
                 compacts, for the README's What it saves
  bill           the sessions' cost by kind of token
  average        a request's mean cost over a cycle against the compaction point, with the marginal cost,
                 as a schematic
  effectiveness  a request's effectiveness against its context, at the base case for β (fit.BASE),
                 at half and twice it, and at β = 0
  sensitivity    how far the best compaction point moves with each measured parameter and with β, at the
                 base case
  threshold      the best compaction point against β, in the steady state and the replay
  voi            the expected value of a classifier's information, on three mixes of work
  voi-point      the compaction point of the work a classifier says to compact, on the same mixes
"""

import html
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Patch

import fit

plt.rcParams.update({"svg.fonttype": "none", "font.family": ["Helvetica", "Arial", "DejaVu Sans"],
                     "svg.hashsalt": "sparkjoy"})

# "page" is GitHub's background in each theme. Text in "fit" names what is measured in my
# sessions; its marks stay neutral, so the colour says where a number comes from without drawing the
# eye. "cost" colours what the charts count in dollars. Two ordinal ramps run from faint to strong
# against the page: "beta" for β, an assumption, and "costs" for magnitudes of a cost. "beta" is the
# dataviz reference palette's blue, "costs" OKLCH steps of its aqua, each validated as an ordinal
# ramp against its page (validate_palette.js --ordinal). "parts" are the steps, faint to strong, that
# `parts` takes from the page toward "cost" to colour a cost's parts. "with" and "without" are the README's two arms,
# sparkjoy's compactions as they ran and the replay without them, validated as a categorical pair
# against each page.
THEMES = {
    "light": {"ink": "#0b0b0b", "muted": "#52514e", "grid": "#e4e3df", "page": "#ffffff", "fit": "#a3500c",
              "cost": "#1baf7a", "beta": ("#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"),
              "costs": ("#22b37d", "#088a5e", "#035f40"), "parts": (0.2, 0.36, 0.52, 0.7), "band": 0.14,
              "with": "#2a78d6", "without": "#eb6834"},
    "dark": {"ink": "#f0efec", "muted": "#c3c2b7", "grid": "#3a3936", "page": "#0d1117", "fit": "#e39457",
             "cost": "#199e70", "beta": ("#256abf", "#3987e5", "#6da7ec", "#9ec5f4", "#cde2fb"),
             "costs": ("#049165", "#43ba8a", "#75e8b6"), "parts": (0.3, 0.5, 0.7, 0.9), "band": 0.18,
             "with": "#3987e5", "without": "#d95926"},
}
RAMP = (0, 0.3, 0.6, 1, 2)  # the β each step of the "beta" ramp stands for


def tint(color, page, share):
    a, b = (tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) for c in (color, page))
    return "#" + "".join(f"{round(x * share + y * (1 - share)):02x}" for x, y in zip(a, b))


def shade(t, beta):
    """The colour of a β: its step of the ramp, or between the two steps around it."""
    rgb = np.array([[int(h[i:i + 2], 16) for i in (1, 3, 5)] for h in t["beta"]])
    return "#" + "".join(f"{round(np.interp(beta, RAMP, rgb[:, k])):02x}" for k in range(3))


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
    ax.tick_params(axis="x", pad=7)  # clear of the y-axis's lowest label where the two meet
    ax.grid(axis="y", color=t["grid"], lw=0.8)
    ax.set_axisbelow(True)


def axis_name(ax, text, t):
    """Name the y-axis with its own label, set horizontal above the axis rather than rotated beside it;
    `save` lines it up with the tick labels' left edge once their widths are known."""
    ax.set_ylabel(text, rotation=0, ha="left", va="bottom", fontsize=10, color=t["muted"])


def place_axis_names(fig):
    """Put each horizontal y-axis label just above its axis, its left edge on the tick labels'."""
    renderer = fig.canvas.get_renderer()
    fig.canvas.draw()
    for ax in fig.axes:
        if not ax.get_ylabel() or ax.yaxis.label.get_rotation() != 0:
            continue
        lefts = [t.get_window_extent(renderer).x0 for t in ax.get_yticklabels() if t.get_text()]
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
    for (y0, text, color), y in zip(items, ys):
        leader = {"arrowstyle": "-", "color": color, "lw": 0.6, "shrinkA": 1, "shrinkB": 1}
        ax.annotate(text, (1, y0), xycoords=("axes fraction", "data"), xytext=(1.025, y),
                    textcoords=("axes fraction", "data"), va="center", fontsize=9, color=color,
                    arrowprops=leader if abs(y - y0) > gap * 0.2 else None)


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
    its axes carry the model's symbols, K and each column's X, and no figures; the shapes come from the
    fitted values, and fit.py prints the numbers the doc's Trade-off table quotes."""
    h, c0, F0 = fit.terms(p, c)
    R = 600  # requests drawn
    X = fit.points(p, c)
    rows = [cycle(p, c, x) for x in X]

    r = np.arange(R + 1)
    titles = ("Compact early", "Compact at X*", "Compact late", "Never compact")
    marks = ("X", "X*", "X", "Claude Code compacts at X")
    for theme, t in THEMES.items():
        fig, axes = plt.subplots(2, len(X), figsize=(9.6, 4.8), sharex=True, sharey="row",
                                 gridspec_kw={"hspace": 0.55, "wspace": 0.08})
        grey = tint(t["cost"], t["page"], 0.36)
        share_col = tint(t["cost"], t["page"], 0.75)
        for col, (x, (n, cost)) in enumerate(zip(X, rows)):
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
            top.set_title(titles[col], color=t["ink"], fontsize=10, loc="left", pad=24, fontweight="bold")
            # Each column's compaction point, named above the first peak of its sawtooth: centred on it,
            # or ending at it where the name is long.
            long = x == fit.AUTO
            top.text(n + (10 if long else 0), x + 30_000, marks[col], fontsize=9, color=t["ink"], va="bottom",
                     ha="right" if long else "center")
            each = h * ctx + c0
            share = (x + F0) / n
            low.fill_between(at, 0, each, color=grey, lw=0, label="the request's own cost")
            low.fill_between(at, each, each + share, color=share_col, lw=0,
                             label="its share of the compaction ending its cycle")
            low.axhline(cost, color=t["muted"], lw=0.9, label="mean over a cycle")
        axes[0, 0].set_ylim(0, 1_100_000)
        axes[0, 0].set_yticks([0, p.K], ["0", "K"])
        axes[1, 0].set_ylim(0, 1_600_000)
        axes[1, 0].set_yticks([0], ["0"])
        for row, text in enumerate(("Context (tokens)", "Cost per request")):
            axis_name(axes[row, 0], text, t)
        fig.supxlabel("Requests", color=t["muted"], fontsize=10, y=0.02)
        handles, labels = axes[1, 0].get_legend_handles_labels()
        fig.legend(handles, labels, frameon=False, fontsize=9, labelcolor=t["ink"], ncol=1, loc="upper left",
                   bbox_to_anchor=(0.11, -0.01))
        save(fig, out / f"model-cycles-{theme}.svg")


def written(p):
    """What the cache write after a compaction writes, K - P tokens in all, by where it comes from:
    each group's mean tokens per compaction in context.py's measurement of the same sessions, scaled
    so the groups sum to K - P. The measurement's first request plus E comes within about 2% of K - P,
    which takes medians where the measurement takes means."""
    m = json.loads(Path(__file__).with_name("context-parts.json").read_text())
    first = m["first_request"]
    groups = {
        "files, re-attached or read again": ("files", "E"),
        "skills, instructions and memory": ("skill_bodies", "skill_listing", "user_claude_md", "project_instructions",
                                            "memory_index", "instructions_wrapper"),
    }
    sizes = {what: sum(m["E_total"] if k == "E" else first[k] for k in keys) for what, keys in groups.items()}
    taken = {k for keys in groups.values() for k in keys}
    sizes = {"the summary and the rest": sum(v for k, v in first.items() if k not in taken), **sizes}
    total = sum(sizes.values())
    return [(what, (p.K - p.P) * v / total) for what, v in sizes.items()]


def inputs(out, p, c):
    """What the costs are made of: a request's cost at its context by part, and a compaction's cost by
    part, on one axis. Parts that come from the sessions' fitted parameters are named in the fit colour."""
    h, c0, F0 = fit.terms(p, c)
    w1 = c.write - 1
    # Every part is linear in x but for the lapse's kink at P, so the ends and P draw the same bands as
    # any finer grid; K and 700k are where the figures below are read.
    x = np.unique([1_000, p.P, p.K, 700_000, fit.AUTO])
    # c(x) = h x + c0 and F(x) = x + F0 by part, in reads: the parts that don't grow with the
    # context first, so they stack at the bottom.
    # Each part says whether it comes from what the sessions measured (output, growth, lapses, K, P,
    # N) or not: the cache read of the context is the context itself.
    # Each part also names its step of the ramp: output 0, cache writes 1 (2 for the lapse's), the cache
    # read of the context the strongest, as the bill chart colours the same kinds.
    request = [("output and uncached input", np.full_like(x, c.output * p.out + c.input * p.inp), True, 0),
               ("cache write of the new tokens", np.full_like(x, (1 - p.lapse) * w1 * p.g), True, 1),
               ("cache write of the context after a lapse", p.lapse * w1 * np.maximum(x - p.P, 0), True, 2),
               ("cache read of the context", x, False, 3)]
    compaction = [("output of the compaction request", np.full_like(x, c.output * p.N), True, 0),
                  *[(f"cache write of {what}", np.full_like(x, w1 * tokens), True, 1)
                    for what, tokens in written(p)],
                  ("cache read of the context", x, False, 3)]
    assert np.allclose(sum(v for _, v, _, _ in request)[x > p.P], (h * x + c0)[x > p.P])
    assert np.allclose(sum(v for _, v, _, _ in compaction), x + F0)
    for name, stack in (("request", request), ("compaction", compaction)):
        for v in (p.K, 700_000):
            j = np.abs(x - v).argmin()
            print(f"model-inputs, a {name} at {tokens(v)}: " + ", ".join(f"{k} ${val[j] * c.read:.3f}" for k, val, _, _ in stack)
                  + f"; ${sum(val[j] for _, val, _, _ in stack) * c.read:.3f} in all")

    for theme, t in THEMES.items():
        fit_c = t["fit"]
        fig, (mid, low) = plt.subplots(2, 1, figsize=(9, 5.6), sharex=True, gridspec_kw={"hspace": 0.32})
        for ax in (mid, low):
            style(ax, t)
            ax.set_xlim(0, fit.AUTO)
        # One hue, lighter for the parts lower in the stack, each band parted from the next by the page;
        # the labels say which parts the sessions measured.
        ramp = parts(t)
        for ax, stack, cap in ((mid, request, 0.3), (low, compaction, 1.3)):
            base = np.zeros_like(x)
            ys = []
            for label, v, fitted, step in stack:
                v = v * c.read  # in dollars
                ax.fill_between(x, base, base + v, fc=ramp[step], ec=t["page"], lw=1.2, label=label)
                ys.append(base[-1] + v[-1] / 2)
                base = base + v
            # Each part named at the right end of its band, thin bands' names pushed apart to stay
            # legible: up from the bottom, then down from the cap where that pushed one past it. A
            # name moved off its band's middle has a leader to it.
            mids, gap = list(ys), 0.1 * cap
            for i in range(1, len(ys)):
                ys[i] = max(ys[i], ys[i - 1] + gap)
            ys[-1] = min(ys[-1], cap - gap / 2)
            for i in range(len(ys) - 2, -1, -1):
                ys[i] = min(ys[i], ys[i + 1] - gap)
            for (label, _, fitted, _), y, mid_y in zip(stack, ys, mids):
                if abs(y - mid_y) > 0.02 * cap:
                    ax.plot([fit.AUTO + 6_000, fit.AUTO + 30_000], [mid_y, y], color=t["muted"], lw=0.8,
                            clip_on=False)
                ax.text(fit.AUTO + 36_000, y, label, va="center", fontsize=9, color=fit_c if fitted else t["ink"])
            ax.set_ylim(0, cap)
            ax.set_yticks(np.arange(0, cap, 0.1 if cap < 0.5 else 0.4))
            ax.yaxis.set_major_formatter(dollars)
        low.xaxis.set_major_formatter(tokens)
        low.set_xticks([0, 200_000, 400_000, 600_000, 800_000])
        low.set_xlabel("Context x (tokens) before the request or the compaction", color=t["muted"], fontsize=10)
        axis_name(mid, "Cost per request c(x) by part ($)", t)
        axis_name(low, "Cost per compaction F(x) by part ($)", t)
        mid.text(1, 1.04, "parts named in orange are fitted to the sessions", transform=mid.transAxes, ha="right",
                 va="bottom", fontsize=9, color=fit_c)
        save(fig, out / f"model-inputs-{theme}.svg")


def payback(out, p, c):
    """How many later requests a compaction takes to pay for itself, against the context x it compacts:
    its cost, x + F0 cached reads, over what each later request saves, h (x - K), with every request
    alike (β = 0). Each later request reads x - K less, so the curve rises without bound as x falls to
    K, which a line marks. Dots mark the three sizes the README names."""
    h, _, F0 = fit.terms(p, c)
    X = np.arange(p.K + 1_000, fit.AUTO + 1, 1_000.0)
    N = (X + F0) / (h * (X - p.K))
    marks = (200_000, 400_000, 700_000)
    each = {x: int(np.ceil((x + F0) / (h * (x - p.K)))) for x in marks}
    for x in marks:
        print(f"model-payback: at {x / 1e3:.0f}k each later request reads {(x - p.K) / 1e3:.0f}k less, and the "
              f"compaction (${(x + F0) * c.read:.2f}) pays for itself after {each[x]} requests")
    top = 100
    for theme, t in THEMES.items():
        fig, ax = plt.subplots(figsize=(9, 3))
        style(ax, t)
        ax.axvline(p.K, color=t["grid"], lw=1.2, zorder=1)
        ax.text(p.K + 12_000, 3, f"{p.K / 1e3:.0f}k, what a compaction leaves", ha="left", va="bottom",
                fontsize=9, color=t["fit"])
        ax.plot(X, N, color=t["ink"], lw=2, zorder=2)
        for x in marks:
            ax.plot([x], [each[x]], "o", ms=7, mfc=t["ink"], mec=t["page"], mew=1.5, zorder=3)
            ax.text(x + 14_000, each[x] + 4, f"{each[x]} requests at {x / 1e3:.0f}k,\neach reading "
                    f"{(x - p.K) / 1e3:.0f}k less", ha="left", va="bottom", fontsize=9, color=t["ink"])
        ax.set_xlim(0, fit.AUTO)
        ax.set_ylim(0, top)
        ax.xaxis.set_major_formatter(tokens)
        ax.set_xticks([0, 250_000, 500_000, 750_000])
        ax.set_yticks([0, 25, 50, 75, 100])
        ax.set_xlabel("Context compacted at a turn end (tokens)", color=t["muted"], fontsize=10)
        axis_name(ax, "Later requests until the compaction pays for itself", t)
        save(fig, out / f"model-payback-{theme}.svg")


def bill(out, S, p, c):
    """The sessions' cost by kind of token, as one stacked bar: each request's
    logged usage at the prices c, in cached reads. The transcripts don't log a compaction's own request, so its
    tokens come from the model and join their kinds: it reads the context it compacts and writes N
    output tokens. The request after it, which writes the compacted context to the cache, is logged
    like any other."""
    n_comp = sum(len(s.compactions) for s in S)
    comp_read = sum(float(x[-1]) for s in S for x in s.compactions)  # a compaction's last field: tokens before it
    comp_out = n_comp * c.output * p.N
    # Each kind, in the order the inputs chart stacks them, with its step of that chart's ramp.
    kinds = [("Output, thinking included", sum(float(s.out.sum()) for s in S) * c.output + comp_out, 0),
             ("Cache writes", sum(float(s.write.sum()) for s in S) * c.write, 1),
             ("Cache reads", sum(float(s.read.sum()) for s in S) + comp_read, 3)]
    uncached = sum(float(s.inp.sum()) for s in S) * c.input
    total = sum(v for _, v, _ in kinds) + uncached
    after = sum(float(s.write[(s.kind == fit.USER) | (s.kind == fit.POLICY)].sum()) for s in S) * c.write
    print("model-bill: " + ", ".join(f"{name.lower()} {v / total:.2%}" for name, v, _ in kinds)
          + f", uncached input {uncached / total:.3%}")
    print(f"model-bill, compactions' part: their own requests' reads {comp_read / total:.2%} and output "
          f"{comp_out / total:.2%}, the cache write after them {after / total:.2%}; "
          f"{(comp_read + comp_out + after) / total:.2%} in all")

    for theme, t in THEMES.items():
        ramp = parts(t)
        fig, ax = plt.subplots(figsize=(9, 1.1))
        ax.set_axis_off()
        left = 0.0
        for i, (name, v, step) in enumerate(kinds):
            share = v / total
            ax.barh(0, share, left=left, height=0.5, color=ramp[step], ec=t["page"], lw=1.5)
            # Each kind named from its segment's left edge, above and below the bar in turn so that names
            # longer than their segments don't meet.
            ax.text(left + 0.004, 0.36 if i % 2 == 0 else -0.36, f"{name}, {share:.0%}", ha="left",
                    va="bottom" if i % 2 == 0 else "top", fontsize=9, color=t["ink"])
            left += share
        ax.set_xlim(0, 1)
        ax.set_ylim(-0.6, 0.6)
        save(fig, out / f"model-bill-{theme}.svg")


def average(out, p, c):
    """Why the best compaction point sits where it does when context size doesn't matter: against the
    compaction point X, a request's cost averaged over the cycle, its share of the compaction included,
    and the marginal cost, what one more request before compacting costs. The average is least where
    they cross, which a dot marks. The doc draws it under The model, so its axes carry K and X* and
    no figures."""
    h, c0, _ = fit.terms(p, c)
    X, A = fit.curve(p, c, 0)  # with every request alike, cost per unit of effectiveness is cost per request
    # d/dX of the cycle's cost, g (X + F0) + h (X² - K²)/2 + c0 (X - K), over d/dX of its requests: the
    # next request's cost, plus the g tokens more the compaction will read.
    M = h * X + c0 + p.g
    j = A.argmin()
    cross = np.abs(M - A)[: len(X) - 1].argmin()
    print(f"model-average: least at {X[j] / 1e3:.0f}k, ${A[j] * c.read:.3f} a request; "
          f"the marginal cost crosses it at {X[cross] / 1e3:.0f}k")
    best = fit.points(p, c)[1]
    least = cycle(p, c, best)[1]  # exactly, as the cycles chart computes it, rather than off the 1k grid
    default = cycle(p, c, fit.AUTO)[1]  # Claude Code's default, compacting near the window's end

    for theme, t in THEMES.items():
        fig, ax = plt.subplots(figsize=(9, 3.4))
        style(ax, t)
        ax.plot(X, A, color=t["cost"], lw=2.2)
        ax.plot(X, M, color=t["muted"], lw=1.6)
        label_ends(ax, [(M[-1], "marginal", t["ink"]), (A[-1], "average", t["ink"])], gap=80_000)
        ax.plot([best], [least], "o", ms=7, mfc=t["cost"], mec=t["page"], mew=1.5, zorder=3)
        ax.text(best + 20_000, least - 45_000, "min(cost) at X*", ha="left", va="top", fontsize=9,
                color=t["ink"])
        # Unclipped, so the dot on the plot's right edge draws whole.
        ax.plot([fit.AUTO], [default], "o", ms=6, mfc=t["page"], mec=t["cost"], mew=1.5, zorder=3,
                clip_on=False)
        ax.text(fit.AUTO - 10_000, default - 150_000, "Claude Code's default,\nnear the window's end",
                ha="right", va="top", fontsize=9, color=t["ink"])
        ax.set_xlim(0, fit.AUTO)
        ax.set_xticks([0, p.K, best], ["0", "K", "X*"])
        ax.set_ylim(0, 1_400_000)
        ax.set_yticks([0], ["0"])
        ax.set_xlabel("Compaction point X (tokens)", color=t["muted"], fontsize=10)
        axis_name(ax, "Cost per request", t)
        save(fig, out / f"model-average-{theme}.svg")


def effectiveness_curve(out, p):
    """A request's effectiveness against its context, (x / 1M)^β, at the base case and at the β the
    sensitivity chart takes it to (halved and doubled), with β = 0 for reference. A dot on each curve at
    K, the context a compaction leaves, carries the curve's name."""
    x = np.linspace(0, fit.WINDOW, 1_000)
    betas = (0, fit.BASE / 2, fit.BASE, fit.BASE * 2)
    for theme, t in THEMES.items():
        fig, ax = plt.subplots(figsize=(9, 3.8))
        style(ax, t)
        for b in betas:
            color = shade(t, b)
            base = b == fit.BASE
            # Unclipped: each curve starts on the plot's left edge, where clipping would halve its line.
            ax.plot(x, fit.eta(x, b), color=color, lw=2.6 if base else 1.6, clip_on=False, zorder=3 if base else 2)
            y = float(fit.eta(p.K, b))
            ax.plot([p.K], [y], "o", ms=6.5, mfc=color, mec=t["page"], mew=1.5, zorder=4)
            # The base case's name takes a second line, which ends before the steepest curve below it.
            name = f"β = {b:g}: {y:.0%}" + ("\nthe base case" if base else "")
            ax.text(p.K + 14_000, y - 0.03, name, ha="left", va="top", fontsize=9, color=t["ink"], linespacing=1.3)
        ax.text(p.K + 14_000, 0.015, f"dots at {tokens(p.K)}, what a compaction leaves", ha="left", va="bottom",
                fontsize=9, color=t["muted"])
        ax.set_xlim(0, fit.WINDOW)
        ax.xaxis.set_major_formatter(tokens)
        ax.set_xticks([0, 250_000, 500_000, 750_000, 1_000_000])
        ax.yaxis.set_major_formatter(percent)
        ax.set_yticks([0, 0.25, 0.5, 0.75, 1])
        ax.set_ylim(0, 1.04)
        ax.set_xlabel("Context x (tokens)", color=t["muted"], fontsize=10)
        axis_name(ax, "Effectiveness η(x), relative to a request at 1M", t)
        save(fig, out / f"model-effectiveness-{theme}.svg")


def sensitivity(out, p, c, boot):
    """How far the best compaction point moves at the base case for β when each parameter measured in the
    sessions is half or twice its value, and across its 90% interval over `boot`, the sessions resampled,
    and when β is half or twice the base case (fit.sensitivity), with a line at the best point with every
    parameter as measured. β, which isn't measured, has no interval. Each bar is labelled for a reader to
    point at."""
    b = fit.BASE
    names = {"K": f"context after a compaction ({tokens(p.K)})", "g": f"growth of the context per request ({p.g / 1e3:.1f}k)",
             "P": f"size of the system prompt and tools ({p.P / 1e3:.1f}k)",
             "lapse": f"share of requests that find the cache lapsed ({p.lapse:.2%})",
             "N": f"output of the compaction request ({p.N / 1e3:.1f}k)",
             "β": f"growth of effectiveness with the context ({b:g}, not measured)"}
    symbols = {**fit.MEASURED, "β": "β"}
    rows = fit.sensitivity(p, c, boot, b)
    base = fit.steady(p, c, b)
    # The measured parameters from the bottom up by how far halving and doubling them moves the point, then β.
    order = [*sorted(fit.MEASURED, key=lambda f: abs(rows[f][1] - rows[f][0])), "β"]
    k = lambda v: f"{v / 1e3:.0f}k"
    labels = {f"sens-{f}": f"{symbols[f]}, {names[f]}: {k(rows[f][0])} at half, {k(rows[f][1])} at twice"
                           + ("" if rows[f][2] is None else f", {k(rows[f][2][0])}–{k(rows[f][2][1])} across its 90% interval")
              for f in order}

    for theme, t in THEMES.items():
        # Each bar's name sits above it rather than as a tick label beside it, so the drawing is no wider
        # than the plot and its text keeps the size of the other charts' where the doc scales it to the column.
        fig, ax = plt.subplots(figsize=(9, 4.6))
        style(ax, t)
        ax.grid(False)
        ax.grid(axis="x", color=t["grid"], lw=0.8)
        ink = t["ink"]
        ax.axvline(base, color=t["muted"], lw=1, zorder=1)
        ax.text(base + 8_000, len(order) - 0.3, f"{k(base)}, with every parameter as measured", ha="left",
                va="bottom", fontsize=9, color=t["ink"])
        for y, f in enumerate(order):
            lo, hi, ends = rows[f]
            # Measured parameters' names in the fit colour; β's in ink, since it is assumed. Each on the
            # page's colour, which breaks the line at the measured point where the name crosses it.
            ax.text(0, y + 0.24, f"{symbols[f]}, {names[f]}", transform=ax.get_yaxis_transform(), ha="left",
                    va="bottom", fontsize=9, color=t["fit"] if f in fit.MEASURED else t["ink"], zorder=4,
                    bbox={"facecolor": t["page"], "edgecolor": "none", "pad": 1.5})
            # A thin line from halved to doubled, thick across the interval.
            ax.plot([lo, hi], [y, y], color=ink, lw=1.6, solid_capstyle="butt", zorder=2, gid=f"sens-{f}")
            if ends is not None:
                ax.plot(ends, [y, y], color=ink, lw=6, solid_capstyle="butt", zorder=2)
            # Unclipped, so a dot on the plot's right edge, at Claude Code's own compaction, draws whole.
            ax.plot([lo], [y], "o", ms=6, mfc=t["page"], mec=ink, mew=1.6, zorder=3, clip_on=False)
            ax.plot([hi], [y], "o", ms=6, mfc=ink, mec=t["page"], mew=1, zorder=3, clip_on=False)
            ax.text(min(lo, hi) - 9_000, y, k(min(lo, hi)), ha="right", va="center", fontsize=9, color=t["ink"])
            right = max(lo, hi)
            if right >= fit.AUTO:
                # At the plot's right edge, so named below the line's end rather than beside it.
                ax.text(right, y - 0.2, f"{k(right)}, where Claude Code compacts", ha="right", va="top",
                        fontsize=9, color=t["ink"])
            else:
                ax.text(right + 9_000, y, k(right), ha="left", va="center", fontsize=9, color=t["ink"])
        ax.set_yticks([])
        ax.set_ylim(-0.4, len(order))
        ax.set_xlim(100_000, fit.AUTO)
        ax.set_xticks(range(200_000, 900_001, 200_000))
        ax.xaxis.set_major_formatter(tokens)
        muted = t["muted"]
        keys = [plt.Line2D([], [], ls="", marker="o", ms=6, mfc=t["page"], mec=muted, mew=1.6),
                plt.Line2D([], [], ls="", marker="o", ms=6, mfc=muted, mec=t["page"], mew=1),
                Patch(color=muted, lw=0)]
        ax.legend(keys, ["halved", "doubled", "across its 90% interval"], frameon=False, fontsize=9,
                  labelcolor=t["ink"], ncol=3, loc="lower right", bbox_to_anchor=(1, 1.0), handletextpad=0.4,
                  columnspacing=1.0, handlelength=1.2)
        ax.set_xlabel(f"Compaction point of lowest cost per unit of effectiveness at β = {b:g} (tokens)",
                      color=t["muted"], fontsize=10)
        save(fig, out / f"model-sensitivity-{theme}.svg", labels)


def thresholds(out, cost, effectiveness, BT, BX, p, c):
    """The best compaction point against β: the steady-state model and the turn-end replay, with 90%
    bands. The replay's legend entry takes the fit colour, since it is measured in the sessions."""
    bs = np.array(fit.CHART_BETAS)
    X = [fit.steady(p, c, b) for b in bs]
    # Never compacting short of Claude Code's own compaction is drawn at it.
    T, BT = (np.minimum(v, fit.AUTO) for v in (fit.best(cost, effectiveness)[0], BT))
    for theme, t in THEMES.items():
        fig, ax = plt.subplots(figsize=(9, 3.6))
        style(ax, t)
        ax.axhline(fit.AUTO, color=t["muted"], lw=0.8)
        ax.text(bs[0] + 0.01, fit.AUTO + 15_000, f"Claude Code compacts at {tokens(fit.AUTO)}", ha="left",
                fontsize=9, color=t["muted"])
        ax.axvline(fit.BASE, color=t["muted"], lw=0.8)
        ax.text(fit.BASE + 0.01, 60_000, f"base case, β = {fit.BASE:g}", ha="left", fontsize=9, color=t["muted"])
        ax.axhline(fit.FLOOR, color=t["muted"], lw=0.8)
        ax.text(bs[-1], fit.FLOOR + 15_000, f"sparkjoy's default floor, {tokens(fit.FLOOR)}", ha="right", fontsize=9,
                color=t["muted"])
        # The steady state in a grey halfway to the page, so it reads apart from the replay's ink; each
        # band a tint of its line, the lighter line's tinted less so its band still shows.
        for y, band, color, share, label in (
                (X, BX, tint(t["muted"], t["page"], 0.55), 2.2 * t["band"], "steady state, compacting at any request"),
                (T, BT, t["ink"], t["band"], "replay of the sessions, compacting at turn ends")):
            lo, hi = np.percentile(band, [5, 95], axis=0)
            ax.fill_between(bs, lo, hi, color=tint(color, t["page"], share), lw=0)
            ax.plot(bs, y, color=color, lw=2, label=label)
        ax.fill_between([], [], color=tint(t["ink"], t["page"], t["band"]), lw=0, label="shaded, their 90% intervals")
        ax.set_xlim(bs[0], bs[-1])
        ax.xaxis.set_major_formatter(lambda v, _: f"{v:g}")
        ax.set_ylim(0, 1_050_000)
        ax.yaxis.set_major_formatter(tokens)
        ax.set_yticks([0, 250_000, 500_000, 750_000])
        ax.set_xlabel("β, the exponent of the power law", color=t["muted"], fontsize=10)
        axis_name(ax, "Best compaction point (tokens)", t)
        legend = ax.legend(frameon=False, fontsize=9, labelcolor=t["ink"], loc="upper left",
                           bbox_to_anchor=(0, 0.88))
        legend.get_texts()[1].set_color(t["fit"])
        save(fig, out / f"model-threshold-{theme}.svg")


def voi(out, p, c):
    """The expected value of a classifier's information on three mixes of work with the same mean β,
    the base case's: a share of the work at one β > 0, the rest at β = 0, where the context doesn't help. For each
    mix and each accuracy, the classifier splits the work into a group to keep and a group to compact,
    each compacting at its own best point; the saving is against the best single threshold, and the x
    axis is the mutual information between the classifier's verdict and the kind of work, as a share
    of a perfect classifier's. A second chart, model-voi-point, draws the best compaction point of the
    group to compact against the same axis."""
    # Denser near 1, where a classifier's saving on rare, steep work climbs fastest.
    A = np.unique(np.round(np.concatenate([np.linspace(0.5, 1, 201), 1 - np.logspace(-4, -1.6, 80)]), 6))
    running = 0.004  # what running the classifier costs, as a share of the cost: the replay's 0.43% at the base case, rounded
    # (share of the work at β, β, step of the β ramp), each share giving a mean β of fit.BASE.
    mixes = tuple((fit.BASE / beta, beta, step) for beta, step in ((1, 2), (2, 3), (5, 4)))

    def H(q):
        q = np.clip(q, 1e-12, 1 - 1e-12)
        return -(q * np.log2(q) + (1 - q) * np.log2(1 - q))

    # On fit.steady's 100-token grid, so the best point of the work at β = 0 is the 262k the doc gives.
    X, J0 = fit.curve(p, c, 0, step=100)
    lines = []
    for share, beta, step in mixes:
        Js = np.array([J0, fit.curve(p, c, beta, step=100)[1]])
        w = np.array([1 - share, share])
        single = (w[:, None] * Js).sum(0).min()
        keep = np.array([1 - A, A]).T * w  # the classifier keeps the context for a share a of the β work, 1 - a of the rest
        both = np.array([(k[:, None] * Js).sum(0).min() + ((w - k)[:, None] * Js).sum(0).min() for k in keep])
        saved = 1 - both / single
        point = np.array([X[((w - k)[:, None] * Js).sum(0).argmin()] for k in keep])
        kept_at = np.array([X[(k[:, None] * Js).sum(0).argmin()] for k in keep])
        info = (H(share * A + (1 - share) * (1 - A)) - H(A)) / H(share)
        lines.append((share, beta, step, saved, info, point))
        at = [int(np.abs(A - a).argmin()) for a in (0.8, 0.9, 0.99, 1.0)]
        print(f"model-voi, {share:.0%} at β = {beta}: best single threshold {tokens(X[(w[:, None] * Js).sum(0).argmin()])}; " + ", ".join(
            f"right {A[i]:.0%}, saves {saved[i]:.1%} ({info[i]:.0%} of the information)" for i in at)
              + f"; pays for running it from {A[np.argmax(saved > running)]:.1%} right")
        print(f"model-voi-point, {share:.0%} at β = {beta}: compacts the group to compact at "
              + ", ".join(f"{tokens(point[i])} right {A[i]:.0%}" for i in at)
              + f"; first below {tokens(fit.AUTO)} at {info[np.argmax(point < fit.AUTO)]:.0%} of the information;"
              + f" compacts the rest at {tokens(fit.AUTO)} from {A[np.argmax(kept_at == fit.AUTO)]:.0%} right")

    for theme, t in THEMES.items():
        fig, ax = plt.subplots(figsize=(8, 4.2))
        style(ax, t)
        ends = []
        for share, beta, step, saved, info, _ in lines:
            ax.plot(info, saved, color=t["beta"][step], lw=2, clip_on=False)
            ends.append((saved[-1], f"{share:.0%} at β = {beta}", t["ink"]))
        # A band from 0 behind the savings: at this scale a line would sit on the axis and over the lines' starts.
        ax.fill_between([0, 1], 0, running, color=t["cost"], lw=0, zorder=1, clip_on=False)
        ends.append((running, f"Running the classifier, {running:.1%}", t["ink"]))
        label_ends(ax, ends, gap=0.02)
        ax.set_xlim(0, 1)
        ax.set_xticks([0, 0.25, 0.5, 0.75, 1])
        ax.set_ylim(0, 0.32)
        ax.set_yticks([0, 0.1, 0.2, 0.3])
        ax.xaxis.set_major_formatter(percent)
        ax.yaxis.set_major_formatter(percent)
        ax.set_xlabel("Accuracy (% of a perfect classifier's information)",
                      color=t["muted"], fontsize=10)
        axis_name(ax, "Expected value of information (% of cost saved)", t)
        save(fig, out / f"model-voi-{theme}.svg")

        fig, ax = plt.subplots(figsize=(8, 3.6))
        style(ax, t)
        for share, beta, step, _, info, point in lines:
            ax.plot(info, point, color=t["beta"][step], lw=2, clip_on=False, label=f"{share:.0%} at β = {beta}")
        ax.legend(frameon=False, fontsize=9, labelcolor=t["ink"], loc="upper right")
        ax.set_xlim(0, 1)
        ax.set_xticks([0, 0.25, 0.5, 0.75, 1])
        ax.xaxis.set_major_formatter(percent)
        ax.set_ylim(X[J0.argmin()] - 40_000, fit.AUTO + 20_000)
        ax.set_yticks([X[J0.argmin()], 500_000, 750_000, fit.AUTO])
        ax.yaxis.set_major_formatter(tokens)
        ax.set_xlabel("Accuracy (% of a perfect classifier's information)", color=t["muted"], fontsize=10)
        axis_name(ax, "Compaction point of the work it says to compact (tokens)", t)
        save(fig, out / f"model-voi-point-{theme}.svg")


def draw(out, S, p, c, boot, cost, effectiveness, BT, BX):
    """Draw each chart the docstring lists into `out`, from fit.py's sessions S, their parameters p and
    `boot`, the parameters of each resample of them, the prices c, and the replay's cost and
    effectiveness with BT and BX, the best turn-end threshold and steady-state point of each resample."""
    out.mkdir(parents=True, exist_ok=True)
    print()
    cycles(out, p, c)
    inputs(out, p, c)
    payback(out, p, c)
    bill(out, S, p, c)
    average(out, p, c)
    effectiveness_curve(out, p)
    sensitivity(out, p, c, boot)
    thresholds(out, cost, effectiveness, BT, BX, p, c)
    voi(out, p, c)
