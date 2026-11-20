#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy>=2.1"]
# ///
"""Writes a page about what sparkjoy did in your Claude Code sessions, and opens it.

    uv run --script model/report.py                                  # the last 14 days
    uv run --script model/report.py --since 2026-11-01 --until 2026-11-08
    uv run --script model/report.py --no-open                        # write it without opening it

The page shows every verdict sparkjoy logged in the window, what its compactions saved,
the returns to a large context after an hour or more idle, and the thresholds the cost
model of docs/cost-model.md fits to the same sessions. It reads, replays and fits
with fit.py, so it opens only the transcripts modified since --since, at low priority.
With --figures it also saves, as JSON, the figures sparkjoy's question states, fitted to
the same sessions, to sparkjoy/figures.json in Claude Code's configuration directory,
where hooks/register.ts reads them, or to the file it names. It prints a summary and the
page's path.
"""

import argparse
import html
import json
import subprocess
import sys
import tempfile
import warnings
from collections import Counter
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import numpy as np

import fit

CHECK_OUTPUT = 100  # output tokens of a check, which reads the context from the cache
RETURN = 3600  # seconds a session sits idle before taking it up again counts as a return
DOC = "https://github.com/max-sixty/sparkjoy/blob/main/docs/cost-model.md"
KINDS = list(fit.KINDS.values())
# What each verdict means, from `judge` in hooks/register.ts.
MEANING = {"compacted": "The model said to compact, and sparkjoy began compacting.",
           "kept": "The model said to keep going.",
           "unreadable": "The model's reply wasn't the JSON the question asks for.",
           "no verdict": "The model gave no reply.",
           "dropped": "The conversation moved on before the reply came, so sparkjoy set it aside.",
           "failed": "An error stopped the check."}


def replayed(s, p):
    """The session's cost in dollars with sparkjoy's compactions where it made them, its checks
    included, and without them, at its model's list prices (None for a model with no price
    here), and the context each request carried in each."""
    priced = s.model in fit.PRICES
    c = fit.prices(s.model if priced else "claude-opus-5-5", s.ttl)
    # As compactions the user typed, sparkjoy's stay where the log has them.
    ran, ran_trace = fit.replay(replace(s, kind=np.where(s.sparkjoy, fit.USER, s.kind)), p, c, np.inf, s.ttl)
    without, without_trace = fit.replay(s, p, c, np.inf, s.ttl)
    checks = sum(v.tokens + c.output * CHECK_OUTPUT for v in s.verdicts if v.tokens)
    dollars = ((ran + checks) * c.read, without * c.read, checks * c.read) if priced else None
    return dollars, ran_trace, without_trace


def returns(S):
    """Each request that took up a context of IDLE_FLOOR or more after RETURN seconds idle, with
    that context, who compacted it in between, if anyone, and what the request's cache write of
    its prompt cost in dollars (None for a model with no price here)."""
    rows = []
    for s in S:
        c = fit.prices(s.model, s.ttl) if s.model in fit.PRICES else None
        for j in np.flatnonzero(np.roll(s.end, 1) & (s.gap >= RETURN)):  # gap[0] is 0, so j > 0
            context = s.size[j - 1] + s.out[j - 1]
            if context >= fit.IDLE_FLOOR:
                by = ("sparkjoy" if s.sparkjoy[j] else "Claude Code" if s.kind[j] == fit.POLICY
                      else "/compact" if s.kind[j] == fit.USER else None)
                rows.append((s, j, context, by, c and s.write[j] * c.write * c.read))
    return sorted(rows, key=lambda r: -r[0].t[r[1]])


def usual_model(S):
    """The model most of the sessions' requests used, of those with a price here, or Opus 5.5."""
    use = Counter()
    for s in S:
        if s.model in fit.PRICES:
            use[s.model] += len(s.size)
    return use.most_common(1)[0][0] if use else "claude-opus-5-5"


def thresholds(S, p, model, ttl):
    """The pair of floors, at turn ends and in idle sessions, that costs least for these sessions in
    the replay with β = 0, at `model`'s prices, and what it and my floors save against
    never compacting."""
    c = fit.prices(model, ttl)
    floors, pairs = fit.floors(S, p, c, ttl)
    never = sum(fit.replay(s, p, c, np.inf, ttl)[0] for s in S)
    author = sum(fit.replay(s, p, c, fit.FLOOR, ttl, idle=fit.IDLE_FLOOR)[0] for s in S)
    return {"model": model, "ttl": ttl, "floors": floors, "saved": 1 - pairs[floors] / never,
            "saved_author": 1 - author / never, "steady": fit.steady(p, c),
            "idle": fit.idle_break_even(p, c), "compactions": p.compactions}


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
    h, m = divmod(round(seconds / 60), 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"


def model_name(model):
    family, *version = model.removeprefix("claude-").split("-")
    return f"{family.title()} {'.'.join(version)}"


# The figures sparkjoy's question states, by the names of their constants in hooks/register.ts,
# each with how the page and the summary name and show it.
FIGURES = (("FLOOR", "floor at turn ends", tokens),
           ("IDLE_FLOOR", "floor in idle sessions", tokens),
           ("K", "context after a compaction", tokens),
           ("P", "system prompt and tools within it", tokens),
           ("SUMMARY", "output of the request that writes the summary", tokens),
           ("TURN_MEDIAN", "median requests per turn", "{:.0f}".format),
           ("TURN_MEAN", "mean requests per turn", "{:.0f}".format),
           ("LAPSE", "requests that find the prompt cache lapsed", "{:.2%}".format),
           ("WRITE", "cached reads a cache write costs", "{:g}".format),
           ("OUTPUT", "cached reads an output token costs", "{:g}".format))


def figures(p, c, floors=None):
    """The figures sparkjoy's question states, fitted to these sessions at the prices `c`, by
    the names of their constants in hooks/register.ts. Any the window couldn't measure is left
    out, and the question states the one in `AUTHOR` instead. So is WASTED, the extra wasted
    requests after a compaction: calibrate.py measures it over 60 days, and over a report's
    window it varies across resamples by more than its own size. `floors` is the pair sparkjoy
    asks from, at turn ends and in idle sessions, where the report fitted one."""
    fitted = {**dict(zip(("FLOOR", "IDLE_FLOOR"), floors or ())), "K": p.K, "P": p.P, "SUMMARY": p.N, "TURN_MEDIAN": p.turn_median, "TURN_MEAN": p.turn,
              "LAPSE": p.lapse, "WRITE": c.write, "OUTPUT": c.output}
    return {k: v for k, v in fitted.items() if np.isfinite(v)}


def esc(text):
    return html.escape(str(text), quote=True)


def name(s, limit=60):
    title = s.title if len(s.title) <= limit else s.title[:limit - 1].rstrip() + "…"
    return f'<span title="{esc(s.title)}">{esc(title)}</span>'


def anchor(heading):
    return heading.lower().replace(" ", "-")


def h2(heading):
    return f'<h2 id="{anchor(heading)}">{heading}</h2>'


def mark(kind):
    return f'<span class="mark {kind.replace(" ", "-")}" aria-hidden="true"></span>'


def at(v):
    return "" if v.tokens is None else f"idle {v.idle} min" if v.idle else "turn end"


def table(head, rows, numeric=(), wrap=()):
    """A table whose `numeric` columns align right and whose `wrap` columns may break lines."""
    cls = lambda i: " class=num" if i in numeric else " class=wrap" if i in wrap else ""
    th = "".join(f"<th{cls(i)}>{h}</th>" for i, h in enumerate(head))
    body = "".join("<tr>" + "".join(f"<td{cls(i)}>{c}</td>" for i, c in enumerate(row)) + "</tr>" for row in rows)
    return f'<div class="scroll"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def sparkline(s, ran, without, w=240, h=44):
    """The context each request carried, with sparkjoy's compactions and without them, and a dot
    at each verdict: the README's chart, small."""
    n = len(ran)
    x = lambda j: f"{j / max(n - 1, 1) * (w - 6) + 3:.0f}"
    y = lambda v: f"{h - 3 - min(v, fit.WINDOW) / fit.WINDOW * (h - 6):.0f}"
    # One point per pixel the line moves to, which keeps a page of many sessions light.
    line = lambda trace: " ".join(dict.fromkeys(f"{x(j)},{y(trace[j])}" for j in range(n)))
    dots = "".join(
        f'<circle class="dot {v.kind.replace(" ", "-")}" cx="{x(j)}" cy="{y(ran[j])}" r="3">'
        f"<title>{esc(f'{v.kind.capitalize()}, {when(v.t)}: {v.reason}')}</title></circle>"
        for v in s.verdicts for j in [max(int(np.searchsorted(s.t, v.t)) - 1, 0)])
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
            f'aria-label="Context per request, with and without sparkjoy">'
            f'<line class="floor" x1="0" x2="{w}" y1="{y(fit.FLOOR)}" y2="{y(fit.FLOOR)}"/>'
            f'<polyline class="without" points="{line(without)}"/><polyline class="with" points="{line(ran)}"/>'
            f"{dots}</svg>")


CSS = """
:root { color-scheme: light dark; --page: #ffffff; --ink: #0b0b0b; --muted: #52514e; --rule: #e4e3df;
  --tile: #f6f5f2; --with: #2a78d6; --without: #9a998f; }
@media (prefers-color-scheme: dark) { :root { --page: #0d1117; --ink: #f0efec; --muted: #a9a89f;
  --rule: #30302d; --tile: #161b22; --with: #3987e5; --without: #6f6e68; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--ink);
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }
main { max-width: 1000px; margin: 0 auto; padding: 32px 16px 64px; }
h1 { font-size: 24px; margin: 0; }
h2 { font-size: 18px; margin: 40px 0 8px; }
p { margin: 8px 0; max-width: 72ch; }
a { color: var(--with); }
.sub, .note { color: var(--muted); }
.note { font-size: 13px; }
.tiles { display: flex; flex-wrap: wrap; gap: 8px; margin: 20px 0 8px; }
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
.mark { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; vertical-align: 0;
  background: var(--muted); }
.mark.compacted { background: var(--with); }
.mark.kept { background: none; box-shadow: inset 0 0 0 1.5px var(--with); }
.spark { display: block; }
.spark polyline { fill: none; stroke-width: 1.5; stroke-linejoin: round; }
.spark .with { stroke: var(--with); }
.spark .without { stroke: var(--without); }
.spark .floor { stroke: var(--muted); stroke-opacity: 0.5; stroke-dasharray: 3 3; }
.spark .dot { fill: var(--muted); stroke: var(--page); stroke-width: 1.5; }
.spark .dot.compacted { fill: var(--with); }
.spark .dot.kept { fill: var(--page); stroke: var(--with); }
.legend { display: flex; gap: 16px; flex-wrap: wrap; color: var(--muted); font-size: 13px; margin: 4px 0 8px; }
.legend i { display: inline-block; width: 16px; height: 0; border-top: 2px solid; vertical-align: 4px; margin-right: 6px; }
"""


def page(S, since, until, verdicts, sessions, saving, rows, fitted, unfit, saved):
    zone = datetime.now().astimezone().strftime("%Z")
    head = f"{days(since, until)}, times in {zone} · {len(S)} sessions · {sum(len(s.size) for s in S):,} requests"
    parts = [f"<h1>sparkjoy</h1><p class=sub>{head}</p>"]
    counts = Counter(v.kind for _, v in verdicts)
    if verdicts:
        idle = sum(1 for _, v in verdicts if v.idle)
        tile = lambda n, label, title="": f'<div class="tile" title="{title}"><b>{n:,}</b><span>{label}</span></div>'
        tiles = "".join(tile(counts[k], k, MEANING[k]) for k in KINDS if counts[k])
        none = " · ".join(f'<span title="{MEANING[k]}">0 {k}</span>' for k in KINDS if not counts[k])
        old = sum(v.plugin == "compact-when-done" for _, v in verdicts)
        parts.append(f'<div class="tiles">{tile(len(verdicts), "verdicts")}{tiles}</div><p class=note>'
                     f"{len(verdicts) - idle - counts['failed']:,} at turn ends, {idle:,} in idle sessions"
                     f"{f' · {none}' if none else ''}</p>")
        if old:
            parts.append(f"<p class=note>{old:,} of them came from the plugin under its old name, compact-when-done, "
                         "which logged only the checks that compacted, so its other verdicts, and what those checks "
                         "cost, are missing here.</p>")
    else:
        parts.append("<p>No verdicts yet; sessions pick sparkjoy up when they restart.</p>")

    summary = []
    unpriced = Counter(model_name(s.model) for s, r in sessions if s.sparkjoy.any() and not r[0])
    if saving:
        ran, without, checks = saving
        n = sum(unpriced.values())
        left = (f" {n} {'session' if n == 1 else 'sessions'} where it compacted, on {', '.join(unpriced)}, "
                f"{'is' if n == 1 else 'are'} left out, as this report has no price for that model."
                if unpriced else "")
        d = without - ran
        head = (f"<b>Saved {usd(d)}</b>, {d / without:.0%} of what these sessions would have cost" if d >= 0 else
                f"<b>Cost {usd(-d)} more</b>, {-d / without:.0%} more than these sessions would have cost")
        summary.append(f"<p>{head} without sparkjoy's compactions. Replayed at list prices, they cost "
                       f"{usd(without)} without them and {usd(ran)} with them, including {usd(checks)} for its "
                       f"checks.{left}</p>")
    elif unpriced:
        summary.append(f"<p>sparkjoy compacted only in sessions on {', '.join(unpriced)}, which this report has "
                       "no price for.</p>")
    else:
        summary.append("<p>Nothing saved yet: sparkjoy made no compaction in this window.</p>")
    lapsed = [r for r in rows if r[3] is None]
    by = Counter(r[3] for r in rows if r[3])
    first = ", ".join(f"{n} by {who}" for who, n in by.most_common())
    rewrote = sum(r[0].write[r[1]] for r in lapsed)
    spent = sum(r[4] for r in lapsed if r[4])
    summary.append(f"<p><b>{len(rows)} returns</b> after an hour or more idle to sessions left with "
                   f"{tokens(fit.IDLE_FLOOR)} or more in context; {len(rows) - len(lapsed)} had been compacted in "
                   f"between{f' ({first})' if first else ''}."
                   + (f" The other {len(lapsed)} wrote {tokens(rewrote)} tokens to the cache again, for "
                      f"{usd(spent)}." if lapsed else "") + "</p>")
    if fitted:
        turn, idle = fitted["floors"]
        summary.append(f"<p><b>Asking from {tokens(turn)} at turn ends and {tokens(idle)} in idle sessions</b> "
                       f"minimizes the cost of these sessions, {change(fitted['saved'])} than never compacting.</p>")
    sections = ["Sessions", "Verdicts"] * bool(verdicts) + ["Idle returns", "Thresholds"] + ["Saved figures"] * bool(saved)
    summary.append("<p class=note>" + " · ".join(f'<a href="#{anchor(h)}">{h}</a>' for h in sections) + "</p>")
    parts.append(f'<div class="summary">{"".join(summary)}</div>')

    if verdicts:
        parts.append(h2("Sessions"))
        parts.append('<p class="legend"><span><i style="color: var(--with)"></i>with sparkjoy</span>'
                     '<span><i style="color: var(--without)"></i>without its compactions; Claude Code\'s and '
                     "yours still run</span>"
                     f"<span>{mark('compacted')}compacted {mark('kept')}kept {mark('failed')}other verdict</span>"
                     f"<span>context per request, 0 to 1M, dashed at {tokens(fit.FLOOR)}</span></p>")
        acted = sorted((sr for sr in sessions if sr[0].verdicts),
                       key=lambda sr: -(sr[1][0][1] - sr[1][0][0]) if sr[1][0] else 0)
        parts.append(table(["Session", "Context", "Verdicts", "With", "Without", "Saved"], [
            (name(s), sparkline(s, r[1], r[2]),
             ", ".join(f"{n} {k}" for k in KINDS if (n := sum(v.kind == k for v in s.verdicts))),
             *((usd(r[0][0]), usd(r[0][1]), f"{usd(r[0][1] - r[0][0])} ({signed((r[0][1] - r[0][0]) / r[0][1])})")
               if r[0] else ("", "", "")))
            for s, r in acted], numeric=(3, 4, 5), wrap=(0,)))

        parts.append(h2("Verdicts"))
        parts.append(table(["When", "Session", "Context", "Verdict", "At", "Reason"], [
            (when(v.t), name(s, 40), "" if v.tokens is None else tokens(v.tokens), mark(v.kind) + v.kind, at(v),
             esc(v.reason)) for s, v in verdicts], numeric=(2,), wrap=(5,)))

    parts.append(h2("Idle returns"))
    parts.append(f"<p>Each time a session left with {tokens(fit.IDLE_FLOOR)} or more in context took a new message "
                 "after an hour or more, past the prompt cache's lifetime. Without a compaction in between, the "
                 "message's first request writes the whole context to the cache again.</p>")
    if rows:
        parts.append(table(["When", "Session", "Idle", "Left at", "Compacted first", "Wrote", "Cost"], [
            (when(s.t[j]), name(s), duration(s.gap[j]), tokens(context), who or "no", tokens(s.write[j]),
             "" if dollars is None else usd(dollars)) for s, j, context, who, dollars in rows],
            numeric=(2, 3, 5, 6)))

    parts.append(h2("Thresholds"))
    if fitted:
        f = fitted
        turn, idle = f["floors"]
        parts.append('<div class="narrow">' + table(["Ask from", "The author's floors", "Fitted to these sessions"], [
            ("At turn ends", tokens(fit.FLOOR), tokens(turn)),
            ("In idle sessions", tokens(fit.IDLE_FLOOR), tokens(idle)),
        ], numeric=(1, 2)) + "</div>")
        parts.append(
            f"<p>Compacting from the fitted floors would cost {change(f['saved'])} than never compacting, and from "
            f"the author's {change(f['saved_author'])}. These replays compact wherever the floors allow, as if every "
            f"question said to. In an idle session, compacting before the cache lapses breaks even from "
            f"{tokens(f['idle'])}. Compacting after any request rather than only at turn ends, the best point would "
            f"be {tokens(f['steady'])}.</p>"
            f"<p class=note>Fitted to {len(S)} sessions and {f['compactions']} compactions, at {model_name(f['model'])} "
            f"prices with a {'5-minute' if f['ttl'] == 300 else '1-hour'} cache, taking a request to get as much "
            f'done at any context size. The <a href="{DOC}">cost model</a> explains these figures.</p>')
    else:
        parts.append(f"<p>{unfit}</p>")

    if saved:
        parts.append(h2("Saved figures"))
        parts.append("<p>From the next turn end or idle session, sparkjoy asks from these floors and its question "
                     "states these figures, fitted to these sessions, in place of its author's.</p>")
        parts.append('<div class="narrow">' + table(["Figure", "These sessions"], [
            (label.capitalize(), show(saved[key])) for key, label, show in FIGURES if key in saved], numeric=(1,))
                     + "</div>")
        missing = [label for key, label, _ in FIGURES if key not in saved]
        parts.append("<p class=note>" + (f"The window measured no {' or '.join(missing)}, so the question states "
                                         "its author's. " if missing else "")
                     + "The extra wasted requests after a compaction stay the author's: measuring them takes "
                     "months of sessions.</p>")
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>sparkjoy report</title><style>{CSS}</style></head><body><main>{''.join(parts)}</main></body></html>")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    fit.window_args(ap)
    ap.add_argument("--no-open", action="store_true", help="write the page without opening it")
    # The file hooks/register.ts reads (FIGURES there), in Claude Code's configuration directory.
    ap.add_argument("--figures", type=Path, nargs="?", const=fit.PROJECTS.parent / "sparkjoy" / "figures.json",
                    help="also save the figures sparkjoy's question states, fitted to these sessions, to this "
                    "JSON file; with no file named, to the one the plugin reads")
    a = ap.parse_args()
    S, since, until = fit.read(a)
    verdicts = sorted(((s, v) for s in S for v in s.verdicts), key=lambda sv: -sv[1].t)
    p = fitted = saved = None
    unfit = "The cost model needs compactions in the window to measure what one leaves."
    kept = "the window holds no compactions to fit them to"  # why the question keeps the figures it has
    if any(((s.kind == fit.USER) | (s.kind == fit.POLICY)).any() for s in S):
        ttl = fit.usual(s.ttl for s in S)
        model = usual_model(S)
        with warnings.catch_warnings():  # a short window leaves some parameters without data
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
            except OSError as e:  # such as a sandbox that keeps writes to the working directory
                kept = f"saving them failed: {e}"
    # The replay prices each compaction from K, P and N.
    replays = p is not None and np.isfinite([p.K, p.P, p.N]).all()
    sessions = [(s, replayed(s, p)) for s in S] if replays else [(s, (None, s.size, s.size)) for s in S]
    priced = [r[0] for _, r in sessions if r[0]]
    # With, without and the checks, summed over the sessions with a price.
    saving = tuple(map(sum, zip(*priced))) if priced and any(s.sparkjoy.any() for s in S) else None
    rows = returns(S)

    # A file of its own, readable by this user alone: the page quotes prompts and reasons.
    fd, path = tempfile.mkstemp(prefix="sparkjoy-report-", suffix=".html")
    with open(fd, "w") as out:
        out.write(page(S, since, until, verdicts, sessions, saving, rows, fitted, unfit, saved))
    counts = Counter(v.kind for _, v in verdicts)
    print(f"{len(verdicts)} verdicts in {len({id(s) for s, _ in verdicts})} of {len(S)} sessions: "
          + ", ".join(f"{counts[k]} {k}" for k in KINDS))
    if saving:
        ran, without, _ = saving
        d = without - ran
        print(f"{'saved' if d >= 0 else 'lost'} {usd(abs(d))} ({abs(d) / without:.0%}): {usd(ran)} with its compactions "
              f"against {usd(without)} without, replayed at list prices")
    print(f"{len(rows)} returns after an hour idle to sessions left at {tokens(fit.IDLE_FLOOR)} or more, "
          f"{sum(1 for r in rows if r[3])} compacted in between")
    if fitted:
        turn, idle = fitted["floors"]
        print(f"cheapest floors {tokens(turn)} at turn ends and {tokens(idle)} in idle sessions, "
              f"{change(fitted['saved'])} than never compacting (the author's {tokens(fit.FLOOR)} and "
              f"{tokens(fit.IDLE_FLOOR)}: {change(fitted['saved_author'])})")
    if saved:
        print("sparkjoy now asks from these sessions' floors and states their figures: "
              + "; ".join(f"{label} {show(saved[key])}" for key, label, show in FIGURES if key in saved))
    elif a.figures:
        print(f"sparkjoy keeps the floors and figures it has: {kept}")
    print(path)
    if not a.no_open:
        try:
            subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", path], check=False)
        except FileNotFoundError:  # no desktop to open it on; the path above is the way to it
            pass


if __name__ == "__main__":
    main()
