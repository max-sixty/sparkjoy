"""Draws the charts of docs/cost-model.md, in a light and a dark theme, and prints the figures
the doc quotes from them. fit.py's --charts draws each of these as model-<name>-{light,dark}.svg,
from one read of the sessions:

  cycles         the context and each request's cost over 600 requests, at four compaction points, as a
                 schematic: axes in the model's symbols
  inputs         a request's cost and a compaction's cost by part, against the context
  bill           the sessions' cost by kind of token
  average        a request's mean cost over a cycle against the compaction point, with the marginal cost,
                 as a schematic
  effectiveness  a request's effectiveness against its context, for each β in BETAS
  cost           cost per unit of effectiveness against the compaction point, for each β in BETAS
  sensitivity    how far the best compaction point moves with each measured parameter
  threshold      the best compaction point against β, in the steady state and the replay
  voi            the expected value of a classifier's information, on three mixes of work
  voi-point      the compaction point of the work a classifier says to compact, on the same mixes
"""

import html
from dataclasses import replace

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
BETAS = (0, 0.3, 0.6, 1)  # the β the effectiveness and cost charts draw


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
    included, from the same integrals as fit.curve."""
    h, c0, F0 = fit.terms(p, c)
    n = (X - p.K) / p.g
    return n, h * (X + p.K) / 2 + c0 + (X + F0) / n


def columns(p, c):
    """The compaction points the cycles chart draws, each one point on the average chart's curve: 175k,
    the best point rounded as the text gives it, which the average chart marks, 400k, and Claude
    Code's own."""
    return (175_000, round(fit.closed_form(p, c), -4), 400_000, fit.AUTO)


def cycles(out, p, c):
    """The trade a compaction makes, request by request: the context, and a request's cost with its
    share of the compaction that ends its cycle, at each of `columns`. It assumes nothing about
    effectiveness. The doc draws it under The model, so its axes carry the model's symbols, K and each
    column's X, and no figures; the shapes come from the fitted values, and the numbers stay in the
    printed lines that the doc's Trade-off table quotes."""
    h, c0, F0 = fit.terms(p, c)
    R = 600  # requests drawn
    X = columns(p, c)
    rows = [cycle(p, c, x) for x in X]
    never = rows[-1][1]
    for x, (n, cost) in zip(X, rows):
        print(f"model-cycles, compacting at {tokens(x)}: {n:.0f} requests a cycle; mean cost ${cost * c.read:.3f}, "
              f"${(x + F0) / n * c.read:.3f} of it the compaction's share; {cost / never:.0%} of never compacting")

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
    request = [("output and uncached input", np.full_like(x, c.output * p.out + c.input * p.inp), True),
               ("cache write of the new tokens", np.full_like(x, (1 - p.lapse) * w1 * p.g), True),
               ("cache write of the context after a lapse", p.lapse * w1 * np.maximum(x - p.P, 0), True),
               ("cache read of the context", x, False)]
    compaction = [("output of the compaction request", np.full_like(x, c.output * p.N), True),
                  ("cache write of the compacted context", np.full_like(x, w1 * (p.K - p.P)), True),
                  ("cache read of the context", x, False)]
    assert np.allclose(sum(v for _, v, _ in request)[x > p.P], (h * x + c0)[x > p.P])
    assert np.allclose(sum(v for _, v, _ in compaction), x + F0)
    for name, stack in (("request", request), ("compaction", compaction)):
        for v in (p.K, 700_000):
            j = np.abs(x - v).argmin()
            print(f"model-inputs, a {name} at {tokens(v)}: " + ", ".join(f"{k} ${val[j] * c.read:.3f}" for k, val, _ in stack)
                  + f"; ${sum(val[j] for _, val, _ in stack) * c.read:.3f} in all")

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
            for i, (label, v, fitted) in enumerate(stack):
                # The cache read of the context takes the darkest step in both panels.
                col = ramp[-1] if label.startswith("cache read") else ramp[i]
                v = v * c.read  # in dollars
                ax.fill_between(x, base, base + v, fc=col, ec=t["page"], lw=1.2, label=label)
                ys.append(base[-1] + v[-1] / 2)
                base = base + v
            # Each part named at the right end of its band, thin bands pushed apart to stay legible.
            gap = 0.1 * cap
            for i in range(1, len(ys)):
                ys[i] = max(ys[i], ys[i - 1] + gap)
            for (label, _, fitted), y in zip(stack, ys):
                ax.text(fit.AUTO + 12_000, y, label, va="center", fontsize=9, color=fit_c if fitted else t["ink"])
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
    no figures; the printed lines give the numbers the doc's Average cost section quotes."""
    h, c0, _ = fit.terms(p, c)
    X, A = fit.curve(p, c, 0)  # with every request alike, cost per unit of effectiveness is cost per request
    # d/dX of the cycle's cost, g (X + F0) + h (X² - K²)/2 + c0 (X - K), over d/dX of its requests: the
    # next request's cost, plus the g tokens more the compaction will read.
    M = h * X + c0 + p.g
    j = A.argmin()
    cross = np.abs(M - A)[: len(X) - 1].argmin()
    print(f"model-average: least at {X[j] / 1e3:.0f}k, ${A[j] * c.read:.3f} a request; "
          f"the marginal cost crosses it at {X[cross] / 1e3:.0f}k")
    best = columns(p, c)[1]
    least = cycle(p, c, best)[1]  # exactly, as the cycles chart computes it, rather than off the 1k grid
    # Savings are against Claude Code's default, compacting near the window's end.
    default = cycle(p, c, fit.AUTO)[1]
    print("model-average: against compacting at 965k, " + ", ".join(
        f"{x / 1e3:.0f}k saves {1 - cycle(p, c, x)[1] / default:.1%}" for x in (best, 400_000)))

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


def effectiveness_curve(out):
    """A request's effectiveness against its context, (x / 1M)^β, for each β the cost chart draws."""
    x = np.linspace(0, fit.WINDOW, 1_000)
    names = {0: "β = 0, equally effective at any context", 1: "β = 1, all context equally valuable"}
    for theme, t in THEMES.items():
        fig, ax = plt.subplots(figsize=(9, 3.2))
        style(ax, t)
        for b in BETAS:
            # Unclipped: each curve starts on the plot's left edge, where clipping would halve its line.
            ax.plot(x, fit.eta(x, b), color=shade(t, b), lw=2, label=names.get(b, f"β = {b:g}"), clip_on=False)
        ax.set_xlim(0, fit.WINDOW)
        ax.xaxis.set_major_formatter(tokens)
        ax.set_xticks([0, 250_000, 500_000, 750_000, 1_000_000])
        ax.yaxis.set_major_formatter(percent)
        ax.set_yticks([0, 0.25, 0.5, 0.75, 1])
        ax.set_ylim(0, 1.04)
        ax.legend(frameon=False, fontsize=9, labelcolor=t["ink"], loc="lower right")
        ax.set_xlabel("Context x (tokens)", color=t["muted"], fontsize=10)
        axis_name(ax, "Effectiveness η(x), relative to a request at 1M", t)
        save(fig, out / f"model-effectiveness-{theme}.svg")


def cost_curve(out, p, c):
    """The steady state's cost per unit of effectiveness against the compaction point, for each β: a
    compaction can follow any request, and the context grows smoothly. Against never compacting, where
    Claude Code compacts at AUTO. Each minimum is given rounded, as the text gives it."""
    for theme, t in THEMES.items():
        fig, ax = plt.subplots(figsize=(9, 3.4))
        style(ax, t)
        for b in BETAS:
            color = shade(t, b)
            X, J = fit.curve(p, c, b)
            y = J / J[-1]
            ax.plot(X, y, color=color, lw=2, label=f"β = {b:g}")
            j = y.argmin()
            if j == len(y) - 1:
                continue  # no compaction short of Claude Code's own pays
            ax.plot([X[j]], [y[j]], "o", ms=6.5, mfc=color, mec=t["page"], mew=1.5, zorder=3)
            ax.text(X[j] + 14_000, y[j] - 0.015, f"lowest at {tokens(round(X[j], -4))}, {y[j]:.0%}", ha="left",
                    va="top", fontsize=9, color=t["ink"])
        ax.set_xlim(0, fit.AUTO)
        ax.xaxis.set_major_formatter(tokens)
        ax.set_xticks([0, 200_000, 400_000, 600_000, 800_000])
        ax.yaxis.set_major_formatter(percent)
        ax.set_ylim(0.4, 1.3)
        ax.set_yticks([0.4, 0.6, 0.8, 1, 1.2])
        ax.legend(frameon=False, fontsize=9, labelcolor=t["ink"], loc="lower right")
        ax.set_xlabel("Compaction point X (tokens)", color=t["muted"], fontsize=10)
        axis_name(ax, "Cost per unit of effectiveness, relative to never compacting", t)
        save(fig, out / f"model-cost-{theme}.svg")


def sensitivity(out, p, c, boot):
    """How far the best compaction point at β = 0 moves when each parameter measured in the sessions is
    half or twice its value, and across its 90% interval over `boot`, the sessions resampled. The
    prices are list prices, so they are left out. Each bar is labelled for a reader to point at."""
    base = fit.closed_form(p, c)  # exact, as the symbols table gives it, where fit.steady's grid rounds down

    def moved(name, value):
        """X* at β = 0 with one parameter set to `value`."""
        return fit.closed_form(replace(p, **{name: value}), c)

    params = [  # (symbol, its name in the symbols table with its value, field)
        ("K", f"context after a compaction ({tokens(p.K)})", "K"),
        ("g", f"growth of the context per request ({p.g / 1e3:.1f}k)", "g"),
        ("P", f"size of the system prompt and tools ({p.P / 1e3:.1f}k)", "P"),
        ("ℓ", f"share of requests that find the cache lapsed ({p.lapse:.2%})", "lapse"),
        ("N", f"output of the compaction request ({p.N / 1e3:.1f}k)", "N"),
    ]
    rows = []
    for sym, what, field in params:
        v = getattr(p, field)
        half, twice = moved(field, v / 2), moved(field, v * 2)
        ends = sorted(moved(field, q) for q in np.percentile([getattr(b, field) for b in boot], [5, 95]))
        rows.append((sym, what, half, twice, ends, field))
    rows.sort(key=lambda r: abs(r[3] - r[2]))
    labels = {f"sens-{field}": f"{sym}, {what}: {lo / 1e3:.0f}k at half, {hi / 1e3:.0f}k at twice, "
                               f"{ends[0] / 1e3:.0f}–{ends[1] / 1e3:.0f}k across its 90% interval"
              for sym, what, lo, hi, ends, field in rows}
    for sym, what, lo, hi, ends, _ in rows:
        print(f"model-sensitivity, {sym}: {lo / 1e3:.0f}k at half, {hi / 1e3:.0f}k at twice, "
              f"{ends[0] / 1e3:.0f}k to {ends[1] / 1e3:.0f}k across its 90% interval")

    for theme, t in THEMES.items():
        fit_c, color = t["fit"], t["cost"]
        light = tint(color, t["page"], 0.45 if theme == "light" else 0.7)
        fig, ax = plt.subplots(figsize=(9, 3.6))
        style(ax, t)
        ax.grid(False)
        ax.grid(axis="x", color=t["grid"], lw=0.8)
        ax.axvline(base, color=t["muted"], lw=1)
        for y, (sym, what, lo, hi, ends, field) in enumerate(rows):
            ax.plot([lo, hi], [y, y], color=light, lw=6, solid_capstyle="butt", gid=f"sens-{field}")
            ax.plot(ends, [y, y], color=color, lw=6, solid_capstyle="butt", zorder=2)
            ax.plot([lo], [y], "o", ms=7, mfc=t["page"], mec=color, mew=2, zorder=3)
            ax.plot([hi], [y], "o", ms=7, mfc=color, mec=t["page"], mew=1.5, zorder=3)
            ax.text(min(lo, hi) - 8_000, y, f"{min(lo, hi) / 1000:.0f}k", ha="right", va="center", fontsize=9,
                    color=t["ink"])
            ax.text(max(lo, hi) + 8_000, y, f"{max(lo, hi) / 1000:.0f}k", ha="left", va="center", fontsize=9,
                    color=t["ink"])
        ax.set_yticks(range(len(rows)), [f"{sym}, {what}" for sym, what, *_ in rows])
        for label in ax.get_yticklabels():
            label.set_color(fit_c)
        ax.set_xlim(100_000, 480_000)
        ax.set_xticks([100_000, 200_000, 300_000, 400_000])
        ax.xaxis.set_major_formatter(tokens)
        ax.text(base, len(rows) - 0.4, f"{round(base, -4) / 1000:.0f}k with every\nparameter as measured",
                ha="center", va="bottom", fontsize=9, color=t["muted"])
        keys = [plt.Line2D([], [], ls="", marker="o", ms=7, mfc=t["page"], mec=color, mew=2),
                plt.Line2D([], [], ls="", marker="o", ms=7, mfc=color, mec=t["page"], mew=1.5),
                Patch(color=color, lw=0)]
        ax.legend(keys, ["parameter halved", "parameter doubled", "across its 90% interval"], frameon=False,
                  fontsize=9, labelcolor=t["ink"], ncol=3, loc="lower right", bbox_to_anchor=(1, 1.0),
                  handletextpad=0.4, columnspacing=1.2, handlelength=1.2)
        ax.set_xlabel("Compaction point of lowest cost (tokens)", color=t["muted"], fontsize=10)
        # Every parameter here is measured, which its name's colour says, so the chart needs no key for it.
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
    0.6: a share of the work at one β > 0, the rest at β = 0, where the context doesn't help. For each
    mix and each accuracy, the classifier splits the work into a group to keep and a group to compact,
    each compacting at its own best point; the saving is against the best single threshold, and the x
    axis is the mutual information between the classifier's verdict and the kind of work, as a share
    of a perfect classifier's. A second chart, model-voi-point, draws the best compaction point of the
    group to compact against the same axis."""
    # Denser near 1, where a classifier's saving on rare, steep work climbs fastest.
    A = np.unique(np.round(np.concatenate([np.linspace(0.5, 1, 201), 1 - np.logspace(-4, -1.6, 80)]), 6))
    running = 0.003  # what running the classifier costs, as a share of the cost: the replay's 0.28%, rounded
    mixes = ((0.60, 1, 2), (0.30, 2, 3), (0.12, 5, 4))  # (share of the work at β, β, step of the β ramp)

    def H(q):
        q = np.clip(q, 1e-12, 1 - 1e-12)
        return -(q * np.log2(q) + (1 - q) * np.log2(1 - q))

    X, J0 = fit.curve(p, c, 0)
    lines = []
    for share, beta, step in mixes:
        Js = np.array([J0, fit.curve(p, c, beta)[1]])
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
        print(f"model-voi, {share:.0%} at β = {beta}: " + ", ".join(
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
        ends.append((running, "Running the classifier, 0.3%", t["ink"]))
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
    bill(out, S, p, c)
    average(out, p, c)
    effectiveness_curve(out)
    cost_curve(out, p, c)
    sensitivity(out, p, c, boot)
    thresholds(out, cost, effectiveness, BT, BX, p, c)
    voi(out, p, c)
