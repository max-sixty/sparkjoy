#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy>=2.1"]
# ///
"""Writes a page about what sparkjoy did in your Claude Code sessions, and opens it.

    uv run --script model/report.py                                  # the last 14 days
    uv run --script model/report.py --since 2026-11-01 --until 2026-11-08
    uv run --script model/report.py --no-open                        # write it without opening it

The page opens on what sparkjoy's compactions saved, or, before it has compacted, on what
compacting from the floors fitted to these sessions would have saved, or on why it had
nothing to do. It shows every verdict sparkjoy logged in the window, the thresholds the
cost model of docs/cost-model.md fits to the same sessions, and the returns to a large
context after an hour or more idle. It leaves out the sessions Claude Code compacted
before 200k, as in a 200k context window, where sparkjoy doesn't act. It reads, replays
and fits with fit.py, so it opens only the transcripts modified since --since, at low
priority.
With --figures it also saves, as JSON, the figures sparkjoy's question uses, fitted to
the same sessions, to sparkjoy/figures.json in Claude Code's configuration directory,
where hooks/register.ts reads them, or to the file it names. It prints a summary and the
page's path.
"""

import argparse
import base64
import html
import json
import tempfile
import warnings
import webbrowser
from collections import Counter
from datetime import datetime
from pathlib import Path

import fit
import numpy as np

RETURN = (
    3600  # seconds a session sits idle before taking it up again counts as a return
)
DOC = "https://github.com/max-sixty/sparkjoy/blob/main/docs/cost-model.md"
ASSETS = Path(__file__).resolve().parents[1] / "assets"
ROWS = 20  # the rows a long table shows until the reader opens the rest
# Claude Code compacts a 200k window on its own before the context reaches this, at about 165k
# in my sessions of January to March 2026, and its idle compaction starts here.
SMALL = 200_000
KINDS = list(fit.KINDS.values())
# What each verdict means, from `judge` in hooks/register.ts.
MEANING = {
    "compacted": "The model said to compact, and sparkjoy began compacting.",
    "would compact": "The model said to compact, and sparkjoy, set to classify, left the context as it was.",
    "kept": "The model said to keep going.",
    "unreadable": "The model's reply wasn't the JSON the question asks for.",
    "no verdict": "The model gave no reply.",
    "dropped": "The conversation moved on before the reply came, so sparkjoy set it aside.",
    "failed": "An error stopped the check.",
}
MISSED = (
    "Its question read less than nine tenths of the context from the prompt cache, so the rest cost a cache "
    "write. sparkjoy logs these from 27 November 2026 on."
)


def returns(S):
    """Each request that took up a context of IDLE_FLOOR or more after RETURN seconds idle, with
    that context, who compacted it in between, if anyone, and what the request's cache write of
    its prompt cost in dollars (None for a model with no price here)."""
    rows = []
    for s in S:
        c = fit.prices(s.model, s.ttl) if s.model in fit.PRICES else None
        for j in np.flatnonzero(
            np.roll(s.end, 1) & (s.gap >= RETURN)
        ):  # gap[0] is 0, so j > 0
            context = s.size[j - 1] + s.out[j - 1]
            if context >= fit.IDLE_FLOOR:
                by = (
                    "sparkjoy"
                    if s.sparkjoy[j]
                    else "Claude Code"
                    if s.kind[j] == fit.POLICY
                    else "/compact"
                    if s.kind[j] == fit.USER
                    else None
                )
                rows.append((s, j, context, by, c and s.write[j] * c.write * c.read))
    return sorted(rows, key=lambda r: -r[0].t[r[1]])


def follows(S):
    """What came after each compaction sparkjoy made, by its session's first request after it:
    "lapsed" where, without the compaction, that request would have found the prompt cache lapsed
    (fit.lapsed); "held" where it came sooner; and "none" where no request came by the window's
    end."""
    out = Counter()
    for s in S:
        lapsed = fit.lapsed(s, s.ttl)
        for t, by, _ in s.compactions:
            if by == "sparkjoy":
                j = np.searchsorted(s.t, t)
                out["none" if j == len(s.t) else "lapsed" if lapsed[j] else "held"] += 1
    return out


def cramped(s):
    """Claude Code compacted the session on its own before its context reached SMALL, as it does in
    a 200k context window, and sparkjoy gave no verdict in it. sparkjoy acts only in a 1M window
    (WINDOW in hooks/register.ts), and the replay, which lets the context grow to fit.AUTO, doesn't
    hold for a smaller one."""
    return (
        not s.verdicts
        and s.size.max() < SMALL
        and any(
            by == "auto" and 0 < (before or 0) < SMALL
            for _, by, before in s.compactions
        )
    )


def reached(s):
    """sparkjoy would have asked in the session: a turn end left its context at the floor at turn
    ends or above, or at the idle floor or above before it sat idle as long as sparkjoy waits."""
    context = s.size + s.out
    idle = (
        s.end[:-1] & (context[:-1] >= fit.IDLE_FLOOR) & (s.idle[1:] > fit.IDLE[s.ttl])
    )
    return bool((s.end & (context >= fit.FLOOR)).any() or idle.any())


def usual_model(S):
    """The model most of the sessions' requests used, of those with a price here, or Opus 5.5."""
    use = Counter()
    for s in S:
        if s.model in fit.PRICES:
            use[s.model] += len(s.size)
    return use.most_common(1)[0][0] if use else "claude-opus-5-5"


def thresholds(S, p, model, ttl):
    """The pair of floors, at turn ends and in idle sessions, that costs least for these sessions in
    the replay (fit.floors), at `model`'s prices; what the sessions cost in dollars compacting
    from it, from my floors and never; and what it and my floors save per unit of effectiveness
    against never compacting, at the base case for β."""
    c = fit.prices(model, ttl)
    floors, _ = fit.floors(S, p, c, ttl)
    # Cost and effectiveness, summed over the sessions, for each policy.
    arms = {
        name: [v.sum() for v in fit.arm(S, p, c, ttl, threshold, idle=idle)]
        for name, (threshold, idle) in (
            ("never", (np.inf, None)),
            ("fitted", floors),
            ("author", (fit.FLOOR, fit.IDLE_FLOOR)),
        )
    }
    unit = {name: cost / work for name, (cost, work) in arms.items()}
    return {
        "model": model,
        "ttl": ttl,
        "floors": floors,
        "cost": {name: cost * c.read for name, (cost, _) in arms.items()},
        "saved": 1 - unit["fitted"] / unit["never"],
        "saved_author": 1 - unit["author"] / unit["never"],
        "idle": fit.idle_break_even(p, c),
        "compactions": p.compactions,
    }


def tokens(n):
    return f"{n / 1e6:,.1f}M" if n >= 10_000_000 else f"{n / 1000:,.0f}k"


def usd(x):
    sign = "−" if x < 0 else ""
    return f"{sign}${abs(x):,.0f}" if abs(x) >= 10 else f"{sign}${abs(x):,.2f}"


def signed(share):
    n = round(share * 100)
    return f"{'−' if n < 0 else ''}{abs(n)}%"


def change(share):
    """A share saved, as "N% less", or as "N% more" when it is negative."""
    return f"{abs(share):.0%} {'less' if share >= 0 else 'more'}"


def when(t):
    d = datetime.fromtimestamp(t).astimezone()
    return f"{d:%b} {d.day} {d:%H:%M}"


def days(since, until):
    a, b = since.astimezone(), until.astimezone()
    return f"{a:%b} {a.day}{f', {a.year}' if a.year != b.year else ''} to {b:%b} {b.day}, {b.year}"


def duration(seconds):
    if round(seconds) < 60:
        return f"{round(seconds)} s"
    h, m = divmod(round(seconds / 60), 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"


def model_name(model):
    family, *version = model.removeprefix("claude-").split("-")
    return f"{family.title()} {'.'.join(version)}"


# The figures sparkjoy's question uses, by the names of their constants in hooks/register.ts,
# each with how the page and the summary name and show it.
FIGURES = (
    ("FLOOR", "floor at turn ends", tokens),
    ("IDLE_FLOOR", "floor in idle sessions", tokens),
    ("K", "context after a compaction", tokens),
    ("P", "system prompt and tools within it", tokens),
    ("SUMMARY", "output of the request that writes the summary", tokens),
    ("TURN_MEDIAN", "median requests per turn", "{:.0f}".format),
    ("LAPSE", "requests that find the prompt cache lapsed", "{:.2%}".format),
    ("WRITE", "cached reads a cache write costs", "{:g}".format),
    ("OUTPUT", "cached reads an output token costs", "{:g}".format),
)


def figures(p, c, floors=None):
    """The figures sparkjoy's question uses, fitted to these sessions at the prices `c`, by
    the names of their constants in hooks/register.ts. Any the window couldn't measure is left
    out, and the question uses the one in `AUTHOR` instead. `floors` is the pair sparkjoy
    asks from, at turn ends and in idle sessions, where the report fitted one."""
    fitted = {
        **dict(zip(("FLOOR", "IDLE_FLOOR"), floors or (), strict=False)),
        "K": p.K,
        "P": p.P,
        "SUMMARY": p.N,
        "TURN_MEDIAN": p.turn_median,
        "LAPSE": p.lapse,
        "WRITE": c.write,
        "OUTPUT": c.output,
    }
    return {k: v for k, v in fitted.items() if np.isfinite(v)}


def esc(text):
    return html.escape(str(text), quote=True)


def bold(text):
    """A summary paragraph as HTML, with what it puts between ** in bold."""
    return "".join(
        f"<b>{esc(part)}</b>" if i % 2 else esc(part)
        for i, part in enumerate(text.split("**"))
    )


def logo():
    """sparkjoy's lockup from assets/, its brackets and name in the page's ink and its dots in the
    accent, so it follows the page's colour scheme as the README's pair of lockups does."""
    svg = (ASSETS / "logo-light.svg").read_text()
    return (
        svg.replace('fill="#1f2328"', 'fill="currentColor"')
        .replace('fill="#bf397a"', 'class="accent"')
        .replace("<svg ", '<svg class="logo" role="img" aria-label="sparkjoy" ', 1)
    )


def icon():
    """The plugin's icon, the mark on a pink tile, as the page's favicon."""
    svg = base64.b64encode((ASSETS / "icon.svg").read_bytes()).decode()
    return (
        f'<link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,{svg}">'
    )


def more(n, label):
    """The toggle above a table of `n` rows that shows the rows past the first ROWS, which the CSS
    hides while it is closed."""
    if n <= ROWS:
        return ""
    return (
        f'<details class="more"><summary><span class="all">Show all {n:,} {label}</span>'
        f'<span class="first">Show the first {ROWS}</span></summary></details>'
    )


def name(s, limit=60):
    title = s.title if len(s.title) <= limit else s.title[: limit - 1].rstrip() + "…"
    return f'<span title="{esc(s.title)}">{esc(title)}</span>'


def anchor(heading):
    return heading.lower().replace(" ", "-")


def h2(heading):
    return f'<h2 id="{anchor(heading)}">{heading}</h2>'


def mark(kind):
    return f'<span class="mark {kind.replace(" ", "-")}" aria-hidden="true"></span>'


def at(v):
    where = "" if v.tokens is None else f"idle {v.idle} min" if v.idle else "turn end"
    if v.missed:
        where += (
            f", missed the cache (read {tokens(v.missed[0])} of {tokens(v.missed[1])})"
        )
    return where


def table(head, rows, numeric=(), wrap=()):
    """A table whose `numeric` columns align right and whose `wrap` columns may break lines."""
    cls = lambda i: " class=num" if i in numeric else " class=wrap" if i in wrap else ""
    th = "".join(f"<th{cls(i)}>{h}</th>" for i, h in enumerate(head))
    body = "".join(
        "<tr>" + "".join(f"<td{cls(i)}>{c}</td>" for i, c in enumerate(row)) + "</tr>"
        for row in rows
    )
    return f'<div class="scroll"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def sparkline(s, ran, without, w=240, h=44):
    """The context each request carried, with sparkjoy's compactions and without them, and a dot
    at each verdict: the README's chart, small."""
    n = len(ran)
    x = lambda j: f"{j / max(n - 1, 1) * (w - 6) + 3:.0f}"
    y = lambda v: f"{h - 3 - min(v, fit.WINDOW) / fit.WINDOW * (h - 6):.0f}"
    # One point per pixel the line moves to, which keeps a page of many sessions light.
    line = lambda trace: " ".join(
        dict.fromkeys(f"{x(j)},{y(trace[j])}" for j in range(n))
    )
    dots = "".join(
        f'<circle class="dot {v.kind.replace(" ", "-")}" cx="{x(j)}" cy="{y(ran[j])}" r="3">'
        f"<title>{esc(f'{v.kind.capitalize()}, {when(v.t)}: {v.reason}')}</title></circle>"
        for v in s.verdicts
        for j in [max(int(np.searchsorted(s.t, v.t)) - 1, 0)]
    )
    return (
        f'<svg class="spark" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
        f'aria-label="Context per request, with and without sparkjoy">'
        f'<line class="floor" x1="0" x2="{w}" y1="{y(fit.FLOOR)}" y2="{y(fit.FLOOR)}"/>'
        f'<polyline class="without" points="{line(without)}"/><polyline class="with" points="{line(ran)}"/>'
        f"{dots}</svg>"
    )


CSS = """
:root { color-scheme: light dark; --page: #ffffff; --ink: #0b0b0b; --muted: #52514e; --rule: #e4e3df;
  --tile: #f6f5f2; --with: #bf397a; --without: #d97757; --accent: #bf397a; }
@media (prefers-color-scheme: dark) { :root { --page: #0d1117; --ink: #f0efec; --muted: #a9a89f;
  --rule: #30302d; --tile: #161b22; --with: #bf397a; --without: #d57353; --accent: #bf397a; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--ink);
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }
main { max-width: 1000px; margin: 0 auto; padding: 32px 16px 64px; }
h1 { margin: 0; line-height: 0; }
.logo { height: 36px; width: auto; max-width: 100%; }
.logo .accent { fill: var(--accent); }
h2 { font-size: 18px; margin: 40px 0 8px; }
p { margin: 8px 0; max-width: 72ch; }
a { color: var(--with); }
.sub, .note { color: var(--muted); }
.sub { margin-top: 12px; }
.note { font-size: 13px; }
.lead { font-size: 19px; line-height: 1.45; margin: 24px 0 12px; }
.tiles { display: flex; flex-wrap: wrap; gap: 8px; margin: 16px 0 8px; }
.tile { background: var(--tile); border-radius: 6px; padding: 8px 14px; min-width: 104px; }
.tile b { display: block; font-size: 22px; font-variant-numeric: tabular-nums; }
.tile span { color: var(--muted); font-size: 13px; }
.tile.zero b { color: var(--muted); font-weight: 400; }
.summary { border-top: 1px solid var(--rule); margin-top: 16px; padding-top: 8px; }
.summary p b { font-variant-numeric: tabular-nums; }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; font-size: 13px; width: 100%; }
th { text-align: left; font-weight: 600; color: var(--muted); border-bottom: 1px solid var(--rule); padding: 6px 8px; white-space: nowrap; }
td { border-bottom: 1px solid var(--rule); padding: 6px 8px; vertical-align: top; white-space: nowrap; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
td.wrap { white-space: normal; min-width: 160px; }
.narrow table { width: auto; }
.narrow th, .narrow td:first-child { white-space: normal; }
.mark { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; vertical-align: 0;
  background: var(--muted); }
.mark.compacted { background: var(--with); }
.mark.would-compact { background: color-mix(in srgb, var(--with) 40%, var(--page)); }
.mark.kept { background: none; box-shadow: inset 0 0 0 1.5px var(--with); }
.spark { display: block; }
.spark polyline { fill: none; stroke-width: 1.5; stroke-linejoin: round; }
.spark .with { stroke: var(--with); }
.spark .without { stroke: var(--without); }
.spark .floor { stroke: var(--muted); stroke-opacity: 0.5; stroke-dasharray: 3 3; }
.spark .dot { fill: var(--muted); stroke: var(--page); stroke-width: 1.5; }
.spark .dot.compacted { fill: var(--with); }
.spark .dot.would-compact { fill: color-mix(in srgb, var(--with) 40%, var(--page)); }
.spark .dot.kept { fill: var(--page); stroke: var(--with); }
.legend { display: flex; gap: 16px; flex-wrap: wrap; color: var(--muted); font-size: 13px; margin: 4px 0 8px; }
.legend i { display: inline-block; width: 16px; height: 0; border-top: 2px solid; vertical-align: 4px; margin-right: 6px; }
.more summary { color: var(--with); cursor: pointer; font-size: 13px; margin: 8px 0; }
.more[open] .all, .more:not([open]) .first { display: none; }
"""
# Each long table shows its first ROWS rows until the toggle above it opens.
CSS += f".more:not([open]) + .scroll tbody tr:nth-child(n+{ROWS + 1}) {{ display: none; }}\n"


def summary(found, S, verdicts, saving, unpriced, rows, fitted, saved, projects):
    """The paragraphs that open the page and make up the summary the script prints, with what
    reads in bold between **. The first states the result: what sparkjoy's compactions saved; or,
    where it made none, what compacting from the fitted floors would have saved; or why it had
    nothing to do."""
    if not found:
        return [
            f"**No sessions to report.** No interactive Claude Code session of more than one "
            f"request in {projects} ran in this window."
        ]
    out = []
    n, small = len(S), len(found) - len(S)
    these = "this session's" if n == 1 else f"these {n:,} sessions'"
    times = lambda k: "once" if k == 1 else f"{k:,} times"
    # The compactions sparkjoy made that the log shows ran.
    compacted = sum(by == "sparkjoy" for s in S for _, by, _ in s.compactions)
    if saving:
        ran, without, checks = saving
        k = sum(unpriced.values())
        left = (
            f" {k} {'session' if k == 1 else 'sessions'} where it compacted, on {', '.join(unpriced)}, "
            f"{'is' if k == 1 else 'are'} left out, as this report has no price for that model."
            if unpriced
            else ""
        )
        d = without - ran
        out.append(
            (
                f"**Saved {usd(d)}**, {d / without:.0%} of what these sessions would have cost"
                if d >= 0
                else f"**Cost {usd(-d)} more**, {-d / without:.0%} more than these sessions would have cost"
            )
            + " without sparkjoy's compactions."
        )
        out.append(
            f"Replayed at the list prices of each session's model, with Claude Code compacting on its own "
            f"when the window fills and, in a session you had open, an idle context from "
            f"{tokens(fit.CC_IDLE)}, these sessions cost {usd(ran)} with "
            f"sparkjoy's compactions, "
            f"{usd(checks)} of it for its checks, against {usd(without)} without them.{left}"
        )
    elif unpriced:
        out.append(
            f"sparkjoy compacted only in sessions on {', '.join(unpriced)}, which this report has "
            "no price for."
        )
    elif compacted:
        out.append(
            f"sparkjoy compacted {times(compacted)} in this window, and too few requests followed "
            "to replay what that saved."
        )
    elif fitted:
        turn, idle = fitted["floors"]
        never, cost = fitted["cost"]["never"], fitted["cost"]["fitted"]
        pct = round(100 * (1 - cost / never))
        out.append(
            f"Compacting at every turn end from {tokens(turn)} and in every idle session from "
            f"{tokens(idle)} would have "
            + (
                f"{'cut' if pct > 0 else 'raised'} {these} cost **from {usd(never)} to "
                f"{usd(cost)}** ({abs(pct)}%)"
                if pct
                else f"left {these} cost at **{usd(never)}**"
            )
            + f", replayed at {model_name(fitted['model'])} list prices."
        )
    elif verdicts:
        out.append(
            f"sparkjoy gave {len(verdicts):,} {'verdict' if len(verdicts) == 1 else 'verdicts'} in "
            "this window and compacted nothing."
        )
    # Claude Code compacting before SMALL shows a 200k window, or auto-compact set to start
    # earlier, which looks the same in the log.
    cause = (
        f"on its own before {tokens(SMALL)}, as it does in a 200k context window or where "
        "auto-compact is set to start earlier"
    )
    needs = f"sparkjoy acts only in sessions with a 1M window, from {tokens(fit.IDLE_FLOOR)}."
    if small and not S:
        k = sum(map(cramped, found))
        out.append(
            f"**sparkjoy can't act in {'this session' if small == 1 else f'these {small:,} sessions'}.** "
            + (
                f"Claude Code compacted {'it' if small == 1 else 'them'} {cause}."
                if k == small
                else f"Claude Code compacted {k:,} of them {cause}, and none of them passed "
                f"{tokens(SMALL)}."
            )
            + f" {needs}"
        )
    elif small:
        out.append(
            f"Claude Code compacted {small:,} of these {len(found):,} sessions {cause}, so this page "
            f"leaves {'it' if small == 1 else 'them'} out. {needs}"
        )
    if not verdicts and S:
        k = sum(map(reached, S))
        floors = (
            f"{tokens(fit.FLOOR)} at a turn end, or {tokens(fit.IDLE_FLOOR)} before the session "
            "sat idle"
        )
        other = "the other" if small else "these"
        out.append(
            f"No {'other ' if small else ''}session reached sparkjoy's default floors: {floors}."
            if not k
            else f"sparkjoy logged no verdict in {'the other' if small else 'this'} session, which "
            f"reached one of its default floors: {floors}."
            if n == 1
            else f"sparkjoy logged no verdicts in {other} sessions. {k:,} of {other} {n:,} reached "
            f"one of its default floors: {floors}."
        )
    if fitted and (saving or unpriced or compacted):
        turn, idle = fitted["floors"]
        out.append(
            f"**Asking from {tokens(turn)} at turn ends and {tokens(idle)} in idle sessions** costs "
            f"least in {these} replay, of the floors it tried"
            + (
                ", and sparkjoy asks from them from the next turn end, except where you set your own."
                if saved
                else "."
            )
        )
    elif fitted and saved:
        turn, idle = fitted["floors"]
        out.append(
            f"From the next turn end, sparkjoy asks from {tokens(turn)} at turn ends and "
            f"{tokens(idle)} in idle sessions, except where you set your own."
        )
    if compacted:
        after = follows(S)
        k = after["none"]
        noun = "compaction" if compacted == 1 else "compactions"
        out.append(
            (
                f"**{k:,} of sparkjoy's {compacted:,} {noun} ended a session**, with no request "
                f"after {'it' if k == 1 else 'them'} by the window's end."
                if k
                else f"**None of sparkjoy's {compacted:,} {noun} ended a session.**"
            )
            + (
                f" Of the {'other ' if k else ''}{compacted - k:,}, the next request came "
                f"{times(after['held'])} before the prompt cache lapsed, and "
                f"{times(after['lapsed'])} after it lapsed."
                if compacted > k
                else ""
            )
        )
    if rows:
        lapsed = [r for r in rows if r[3] is None]
        by = Counter(r[3] for r in rows if r[3])
        first = ", ".join(f"{k} by {who}" for who, k in by.most_common())
        out.append(
            f"**{len(rows):,} returns** after an hour or more idle to sessions left with "
            f"{tokens(fit.IDLE_FLOOR)} or more in context; {len(rows) - len(lapsed):,} had been "
            f"compacted in between{f' ({first})' if first else ''}."
            + (
                f" The other {len(lapsed):,} wrote {tokens(sum(r[0].write[r[1]] for r in lapsed))} "
                f"tokens to the cache again, for {usd(sum(r[4] for r in lapsed if r[4]))}."
                if lapsed
                else ""
            )
        )
    return out


def page(
    found, S, since, until, verdicts, sessions, opening, rows, fitted, unfit, saved
):
    zone = datetime.now().astimezone().strftime("%Z")
    head = (
        f"{days(since, until)}, times in {zone} · {len(found):,} {'session' if len(found) == 1 else 'sessions'}"
        f" · {sum(len(s.size) for s in found):,} requests"
    )
    parts = [f"<h1>{logo()}</h1><p class=sub>{head}</p>"]
    lead, *rest = opening
    parts.append(f'<div class="summary"><p class="lead">{bold(lead)}</p>')
    counts = Counter(v.kind for _, v in verdicts)
    if verdicts:
        idle = sum(1 for _, v in verdicts if v.idle)
        tile = lambda n, label, title="": (
            f'<div class="tile" title="{title}"><b>{n:,}</b><span>{label}</span></div>'
        )
        tiles = "".join(tile(counts[k], k, MEANING[k]) for k in KINDS if counts[k])
        old = sum(v.plugin == "compact-when-done" for _, v in verdicts)
        missed = sum(1 for _, v in verdicts if v.missed)
        parts.append(
            f'<div class="tiles">{tile(len(verdicts), "verdicts")}{tiles}</div><p class=note>'
            f"In {len({id(s) for s, _ in verdicts}):,} of {len(found):,} sessions · "
            f"{len(verdicts) - idle - counts['failed']:,} at turn ends, {idle:,} in idle sessions · "
            f'<span title="{MISSED}">{missed:,} missed the prompt cache</span></p>'
        )
        if old:
            parts.append(
                f"<p class=note>{old:,} of these verdicts came from the plugin under its old name, compact-when-done, "
                "which logged only the checks that compacted, so its other verdicts, and what those checks "
                "cost, are missing here.</p>"
            )
    parts.extend(f"<p>{bold(p)}</p>" for p in rest)
    sections = (
        ["Sessions", "Verdicts"] * bool(verdicts)
        + ["Thresholds"] * bool(S)
        + ["Fitted figures"] * bool(saved)
        + ["Idle returns"] * bool(rows)
    )
    if sections:
        parts.append(
            "<p class=note>"
            + " · ".join(f'<a href="#{anchor(h)}">{h}</a>' for h in sections)
            + "</p>"
        )
    parts.append("</div>")

    if verdicts:
        parts.append(h2("Sessions"))
        # Only a user who set sparkjoy to classify has these.
        would = (
            f"{mark('would compact')}would compact " if counts["would compact"] else ""
        )
        parts.append(
            '<p class="legend"><span><i style="color: var(--with)"></i>with sparkjoy</span>'
            '<span><i style="color: var(--without)"></i>without its compactions; Claude Code\'s and '
            "yours still run</span>"
            f"<span>{mark('compacted')}compacted {would}{mark('kept')}kept {mark('failed')}other verdict</span>"
            f"<span>context per request, 0 to 1M, dashed at {tokens(fit.FLOOR)}</span></p>"
        )
        acted = sorted(
            (sr for sr in sessions if sr[0].verdicts),
            key=lambda sr: -(sr[1][0][1] - sr[1][0][0]) if sr[1][0] else 0,
        )
        parts.append(
            more(len(acted), "sessions, by what sparkjoy saved")
            + table(
                ["Session", "Context", "Verdicts", "With", "Without", "Saved"],
                [
                    (
                        name(s),
                        sparkline(s, r[1], r[2]),
                        ", ".join(
                            f"{n} {k}"
                            for k in KINDS
                            if (n := sum(v.kind == k for v in s.verdicts))
                        ),
                        *(
                            (
                                usd(r[0][0]),
                                usd(r[0][1]),
                                f"{usd(r[0][1] - r[0][0])} ({signed((r[0][1] - r[0][0]) / r[0][1])})",
                            )
                            if r[0]
                            else ("", "", "")
                        ),
                    )
                    for s, r in acted
                ],
                numeric=(3, 4, 5),
                wrap=(0,),
            )
        )

        parts.append(h2("Verdicts"))
        parts.append(
            more(len(verdicts), "verdicts, newest first")
            + table(
                ["When", "Session", "Context", "Verdict", "At", "Reason"],
                [
                    (
                        when(v.t),
                        name(s, 40),
                        "" if v.tokens is None else tokens(v.tokens),
                        mark(v.kind) + v.kind,
                        at(v),
                        esc(v.reason),
                    )
                    for s, v in verdicts
                ],
                numeric=(2,),
                wrap=(5,),
            )
        )

    if S:
        parts.append(h2("Thresholds"))
        if fitted:
            f = fitted
            turn, idle = f["floors"]
            parts.append(
                '<div class="narrow">'
                + table(
                    [
                        "",
                        "Never compacting",
                        "sparkjoy's defaults",
                        "Fitted to these sessions",
                    ],
                    [
                        ("At turn ends", "—", tokens(fit.FLOOR), tokens(turn)),
                        ("In idle sessions", "—", tokens(fit.IDLE_FLOOR), tokens(idle)),
                        (
                            "Cost",
                            *(usd(f["cost"][k]) for k in ("never", "author", "fitted")),
                        ),
                        (
                            "Cost per unit of work",
                            "—",
                            change(f["saved_author"]),
                            change(f["saved"]),
                        ),
                    ],
                    numeric=(1, 2, 3),
                )
                + "</div>"
            )
            parts.append(
                f"<p>Each column replays these sessions at {model_name(f['model'])} list prices, compacting "
                f"at every turn end and in every idle session from its floors, as if each question said to. "
                f"Never compacting keeps only Claude "
                f"Code's compaction when the window fills, and your /compact. Of the floors tried, the "
                f"fitted ones cost least. The last row compares each with never compacting by cost per unit "
                f"of work, which also counts how much less a request gets done with a smaller context: it takes a request at "
                f"250k to get half as much done as one at 1M, the cost model's base case, "
                f"β = {fit.BASE:g}.</p>"
                f"<p class=note>Fitted to {len(S):,} sessions and the {f['compactions']:,} compactions in "
                f"them, with a {'5-minute' if f['ttl'] == 300 else '1-hour'} cache. The floors are fitted "
                f"by cost alone, since sparkjoy only starts asking at them. In an idle session, compacting "
                f"before the cache lapses breaks even from {tokens(f['idle'])}. The "
                f'<a href="{DOC}">cost model</a> explains these figures.</p>'
            )
        else:
            parts.append(f"<p>{unfit}</p>")

    if saved:
        parts.append(h2("Fitted figures"))
        parts.append(
            "<p>From the next turn end or idle session, sparkjoy asks from these floors, except where you "
            "set a floor of your own, and its question uses these figures, fitted to these sessions, in "
            "place of its defaults.</p>"
        )
        parts.append(
            '<div class="narrow">'
            + table(
                ["Figure", "These sessions"],
                [
                    (label.capitalize(), show(saved[key]))
                    for key, label, show in FIGURES
                    if key in saved
                ],
                numeric=(1,),
            )
            + "</div>"
        )
        missing = [label for key, label, _ in FIGURES if key not in saved]
        if missing:
            parts.append(
                f"<p class=note>The window measured no {' or '.join(missing)}, so the question "
                "uses its default.</p>"
            )

    if rows:
        parts.append(h2("Idle returns"))
        parts.append(
            f"<p>Each time a session left with {tokens(fit.IDLE_FLOOR)} or more in context took a new message "
            "after an hour or more, past the prompt cache's lifetime. Without a compaction in between, the "
            "message's first request writes the whole context to the cache again.</p>"
        )
        parts.append(
            more(len(rows), "returns, newest first")
            + table(
                [
                    "When",
                    "Session",
                    "Idle",
                    "Left at",
                    "Compacted first",
                    "Wrote",
                    "Cost",
                ],
                [
                    (
                        when(s.t[j]),
                        name(s),
                        duration(s.gap[j]),
                        tokens(context),
                        who or "no",
                        tokens(s.write[j]),
                        "" if dollars is None else usd(dollars),
                    )
                    for s, j, context, who, dollars in rows
                ],
                numeric=(2, 3, 5, 6),
            )
        )
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>sparkjoy report</title>{icon()}<style>{CSS}</style></head><body><main>{''.join(parts)}</main></body></html>"
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    fit.window_args(ap)
    ap.add_argument(
        "--no-open", action="store_true", help="write the page without opening it"
    )
    # The file hooks/register.ts reads (FIGURES there), in Claude Code's configuration directory.
    ap.add_argument(
        "--figures",
        type=Path,
        nargs="?",
        const=fit.PROJECTS.parent / "sparkjoy" / "figures.json",
        help="also save the figures sparkjoy's question uses, fitted to these sessions, to this "
        "JSON file; with no file named, to the one the plugin reads",
    )
    a = ap.parse_args()
    found, since, until = fit.read(a)
    # The page leaves out the sessions sparkjoy can't act in, for which the replay doesn't hold:
    # those Claude Code compacted as in a 200k window, and all of them where the others show no
    # sign of a larger one, as a verdict or a context past SMALL would.
    S = [s for s in found if not cramped(s)]
    if len(S) < len(found) and not any(s.verdicts or s.size.max() >= SMALL for s in S):
        S = []
    verdicts = sorted(((s, v) for s in S for v in s.verdicts), key=lambda sv: -sv[1].t)
    p = fitted = saved = None
    unfit = (
        "Floors can be fitted once these sessions include a compaction, which shows how large "
        "the context is after one."
    )
    kept = "the window holds no compactions to fit them to"  # why the question keeps the figures it has
    if any(((s.kind == fit.USER) | (s.kind == fit.POLICY)).any() for s in S):
        ttl = fit.usual(s.ttl for s in S)
        model = usual_model(S)
        with (
            warnings.catch_warnings()
        ):  # a short window leaves some parameters without data
            warnings.simplefilter("ignore", RuntimeWarning)
            p = fit.measure(S, ttl)
        if np.isfinite([p.K, p.P, p.N, p.g, p.out, p.lapse]).all():
            fitted = thresholds(S, p, model, ttl)
        unfit = "The window holds too few requests to fit the cost model."
        if a.figures:
            values = figures(p, fit.prices(model, ttl), fitted and fitted["floors"])
            try:
                a.figures.parent.mkdir(parents=True, exist_ok=True)
                a.figures.write_text(json.dumps(values))
                saved = values
            except (
                OSError
            ) as e:  # such as a sandbox that keeps writes to the working directory
                kept = f"saving them failed: {e}"
    # The replay prices each compaction from K, P and N.
    replays = p is not None and np.isfinite([p.K, p.P, p.N]).all()
    sessions = (
        [(s, fit.as_run(s, p)) for s in S]
        if replays
        else [(s, (None, s.size, s.size)) for s in S]
    )
    priced = [r[0] for _, r in sessions if r[0]]
    # With, without and the checks, summed over the sessions with a price.
    saving = (
        tuple(map(sum, zip(*priced, strict=True)))
        if any(r[0] and s.sparkjoy.any() for s, r in sessions)
        else None
    )
    unpriced = Counter(
        model_name(s.model) for s in S if s.sparkjoy.any() and s.model not in fit.PRICES
    )
    rows = returns(S)
    opening = summary(
        found, S, verdicts, saving, unpriced, rows, fitted, saved, a.projects
    )

    # A file of its own, readable by this user alone: the page quotes prompts and reasons.
    fd, path = tempfile.mkstemp(prefix="sparkjoy-report-", suffix=".html")
    with open(fd, "w") as out:
        out.write(
            page(
                found,
                S,
                since,
                until,
                verdicts,
                sessions,
                opening,
                rows,
                fitted,
                unfit,
                saved,
            )
        )
    # The page's opening as plain text, with the count of each kind of verdict after the first
    # paragraph, as the page puts its tiles there.
    lead, *rest = (paragraph.replace("**", "") for paragraph in opening)
    print(lead)
    if verdicts:
        counts = Counter(v.kind for _, v in verdicts)
        print(
            f"{len(verdicts)} {'verdict' if len(verdicts) == 1 else 'verdicts'} in "
            f"{len({id(s) for s, _ in verdicts})} of {len(found)} sessions: "
            + ", ".join(f"{counts[k]} {k}" for k in KINDS)
            + f"; {sum(1 for _, v in verdicts if v.missed)} missed the prompt cache"
        )
    for paragraph in rest:
        print(paragraph)
    if saved:
        print(
            "Fitted figures: "
            + "; ".join(
                f"{label} {show(saved[key])}"
                for key, label, show in FIGURES
                if key in saved
            )
        )
    elif a.figures:
        print(f"sparkjoy keeps the floors and figures it has: {kept}")
    print(path)
    if not a.no_open:  # with no desktop to open it on, the path above is the way to it
        webbrowser.open(Path(path).as_uri())


if __name__ == "__main__":
    main()
