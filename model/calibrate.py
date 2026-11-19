#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy>=2.1", "matplotlib>=3.9"]
# ///
"""Measures how trouble varies with the context in your sessions, and fits D, the cost of the detail
a compaction drops. See "Calibration" in docs/cost-model.md.

    uv run --script model/calibrate.py --since 2026-09-17T00:00Z --until 2026-11-16T00:00Z \
        --costs-since 2026-11-07T12:00Z --costs-until 2026-11-16T00:00Z --charts assets

Effectiveness isn't logged, so the script measures what lowers it, as trouble, and how trouble
varies with the context within a session. A request is in trouble when a tool call it made
failed or when it repeated a lookup its context already held; a turn is in trouble when the
user's next message corrects it or they interrupt it. Regressions with a fixed effect for each
session estimate how trouble changes with the context, and how much more of it follows a
compaction, which gives D, with 90% intervals from resampling sessions. Outcomes need many sessions, so the
cost model that prices D and finds X* can come from a shorter, more recent window. With --charts
it also draws the doc's model-calibration chart into that directory: trouble by context band beside
what β would predict.
"""

import argparse
import re
from collections import defaultdict
from pathlib import Path

import numpy as np

import fit

# A user's message that says the turn it answers went wrong.
CORRECTION = re.compile(r"""
    ^\W*(no|nope|nah|wait|stop|hold\ on|hmm+|ugh)\b
  | \b(wrong|incorrect|mistaken|broken|broke|regress\w*)\b
  | \bnot\ (what|quite|right|correct|working|true|helpful|clear|enough)\b
  | \b(that's|that\ is|this\ is|it's|it\ is)\ not\b
  | \b(i|we)\ (said|asked|meant|told\ you|wanted|already)\b | \bmy\ (request|question)\b
  | \b(you|we)\ (didn't|did\ not|haven't|have\ not|forgot|missed|ignored|skipped)\b
  | \b(didn't|did)\ (you|we)\b | \bwhy\ (are|did|is|would|didn't|do|does|was|were|have|has)\b
  | \bstill\b | \b(instead|rather\ than)\b | \bshould(n't|\ not)?\ have\b
  | \b(confus\w*|unclear|don't\ understand|don't\ follow|makes\ no\ sense|doesn't\ make\ sense|don't\ know\ what)\b
  | \btoo\ (verbose|long|complex|complicated|much|many|wordy|dense)\b
  | \b(over-?complicat\w*|overly|epicycles?|workarounds?|patch(es|ing)|hacky?)\b
  | \b(doesn't|didn't|does\ not|did\ not|isn't|wasn't|won't)\ work | \bnot\ working\b
  | \b(undo|revert|roll\ back)\b | \b(i\ don't\ see|can't\ see|not\ showing|nothing\ (happens|changed))\b
  | \?\?
""", re.I | re.X)
# Scored blind against what the user said to 160 turns, labelled by hand, 80 it matched and 80 it
# didn't, drawn from 17 Sep to 16 Nov 2026 and weighted by those strata: it finds about half the
# corrections, and a third of what it matches isn't one. Misclassification that doesn't depend
# on the context shrinks a slope in matches by J = sensitivity + specificity - 1, so slopes are
# divided by J (90% interval 0.31 to 0.57 from resampling the labels, which J_SD carries into
# the resampling of sessions).
SENSITIVITY, SPECIFICITY = 0.52, 0.90
J = SENSITIVITY + SPECIFICITY - 1
J_SD = (0.57 - 0.31) / (2 * 1.645)
# What the user pasted or quoted is someone else's words: the agent's, a review's, a log's.
PASTED = re.compile(r"<pasted_content[^>]*>.*?(</pasted_content[^>]*>|$)", re.S)
QUOTED = re.compile(r"^\s*>.*$", re.M)
INTERRUPTED = "[Request interrupted by user"
# What arrives as a user's line but no person typed: a plugin's message, or Claude Code's notice
# that it stopped background agents. A message that is only a slash command answers nothing.
NOT_ANSWERS = re.compile(r"^(The \S+ plugin sent a message|\d+ background agents? (was|were) stopped|/\S+\s*$)")
LOOKUPS = {"Read", "Grep", "Glob"}  # calls that only look, so a second identical one finds what the first did
EDITS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}
F = 1.0  # share of a corrected or interrupted turn's requests that are wasted; the script also tries 0 and 2
BINS = np.array([0, 200_000, 300_000, 400_000, 600_000, np.inf])
REFERENCE = 3  # 400k to 600k, where most compactions happen
# The half points searched for the saturating form, 1 - 2^(-x/s), which the comparison of forms fits
# beside the power law.
BETAS = (0, 0.3, 0.6)  # values of β, the cost model's effectiveness exponent, to replay with D at the top of its interval
PREDICT = (0.1, 0.3, 0.6)  # values of β whose trouble by context band the results set beside the sessions'
DRAWS = 200


def corrects(text):
    """Whether a user's message corrects the turn it answers, by CORRECTION."""
    return not text.startswith(INTERRUPTED) and bool(CORRECTION.search(QUOTED.sub("", PASTED.sub("", text))))


def answered(s):
    """Each turn the user answered, as (its first request, the one after its last, 1 or 2 if it is the
    first or second turn after a compaction or else 0, what the user said). Turns end at a reply,
    or where the user's message cuts in. A turn's answers are what the user typed while it ran
    and the message that started the next one."""
    n = len(s.size)
    answers = [(t, text, mid) for t, text, mid in s.said if not NOT_ANSWERS.match(text.strip())]
    starts = [0] + [j for j in range(1, n) if s.end[j - 1] or any(
        s.t[j - 1] < t <= s.t[j] for t, _, mid in s.said if not mid)]
    compacted = None
    for k, a in enumerate(starts):
        b = starts[k + 1] if k + 1 < len(starts) else n
        compacted = k if s.kind[a] in (fit.USER, fit.POLICY) else compacted
        until = s.t[b] if b < n else np.inf
        said = [text for t, text, mid in answers if (s.t[a] if mid else s.t[b - 1]) < t <= until]
        if said:
            yield a, b, k - compacted + 1 if compacted is not None and k - compacted < 2 else 0, said


def rows(s):
    """One session's requests and answered turns as columns, leaving out requests that logged no
    context, which Claude Code sometimes writes."""
    n = len(s.size)
    # Who made the latest compaction, 1 sparkjoy or 2 anyone else, and src the same for the SPAN
    # requests after it.
    by, src = np.zeros(n, int), np.zeros(n, int)
    for t, how, _ in s.compactions:
        j = np.searchsorted(s.t, t)
        if j < n and s.kind[j] in (fit.USER, fit.POLICY):
            by[j:] = src[j:j + fit.SPAN] = 1 if how == "sparkjoy" else 2
    # A lookup repeated with no edit between that could change what it finds: none at all for a
    # search, none to the file for a Read. A command could change either; polling a log that a
    # command writes is the commonest repeat this lets through.
    looked, seen = np.zeros(n, bool), {}
    for j, calls in enumerate(s.calls):
        if s.kind[j] != fit.NORMAL:
            seen = {}
        lookups = {(name, digest): path for name, digest, path in calls if name in LOOKUPS}
        looked[j] = any(k in seen for k in lookups)
        if edited := {path for name, _, path in calls if name in EDITS}:
            seen = {k: v for k, v in seen.items() if v is not None and v not in edited}
        seen |= lookups
    failed = np.array([bool(f) for f in s.failed])
    explored = np.array([bool(calls) and all(name in LOOKUPS for name, _, _ in calls) for calls in s.calls])
    ok = s.size > 0
    r = {"x": s.size, "src": src, "start": (np.arange(n) < fit.SPAN) & (s.kind[0] == fit.FIRST),
         "failed": failed, "looked": looked, "wasted": failed | looked, "explored": explored,
         "guard": np.array([bool(f) and all("isolated in the worktree" in e for e in f) for f in s.failed])}
    r = {k: v[ok] for k, v in r.items()}
    turns = [(s.size[b - 1], after, by[a] if after else 0, a == 0 and s.kind[0] == fit.FIRST, b - a,
              any(corrects(t) for t in said), any(t.startswith(INTERRUPTED) for t in said))
             for a, b, after, said in answered(s) if s.size[b - 1] > 0]
    T = np.array(turns, float).reshape(-1, 7)
    for k, col in enumerate(("tx", "tpost", "tsrc", "tstart", "tlen", "corrected", "interrupted")):
        r[col] = T[:, k]
    return r


def within(y, X, g, w=None):
    """Weighted least squares of y on the columns of X with a fixed effect for each group in g."""
    w = np.ones(len(y)) if w is None else w
    sw = np.bincount(g, w)
    demean = lambda v: v - (np.bincount(g, w * v) / np.where(sw > 0, sw, 1))[g]
    yd, Xd = demean(y.astype(float)), np.column_stack([demean(c.astype(float)) for c in X])
    r = np.sqrt(w)
    return np.linalg.lstsq(Xd * r[:, None], yd * r, rcond=None)[0]


def frame(R):
    """The sessions' columns concatenated, with each row's session in g (requests) and tg (turns)."""
    d = {k: np.concatenate([r[k] for r in R]) for k in R[0]}
    d["g"] = np.concatenate([np.full(len(r["x"]), i) for i, r in enumerate(R)])
    d["tg"] = np.concatenate([np.full(len(r["tx"]), i) for i, r in enumerate(R)])
    return d


def coefficients(d):
    """Every regression the results need, as a flat dict of numbers. Requests: an outcome on the
    log of the context, the SPAN requests after a compaction (by whom, for "by"), and the SPAN
    that start the session. Turns, weighted by their requests so a coefficient reads as a share
    of requests: the same, with the first and second turns after a compaction."""
    g, tg, w = d["g"], d["tg"], d["tlen"]
    lx, tlx = np.log(d["x"] / fit.WINDOW), np.log(d["tx"] / fit.WINDOW)
    post, start = d["src"] > 0, d["start"]
    p1, p2 = d["tpost"] == 1, d["tpost"] == 2
    out = {}
    for y in ("wasted", "failed", "looked", "explored"):
        out[y], out[y + " after"], out[y + " start"] = within(d[y], [lx, post, start], g)
    out["failed, guard aside"], out["failed, guard aside after"], out["failed, guard aside start"] = within(
        d["failed"] & ~d["guard"], [lx, post, start], g)
    for y in ("corrected", "interrupted"):
        out[y], out[y + " after"], out[y + " after 2"], out[y + " start"] = within(d[y], [tlx, p1, p2, d["tstart"]], tg, w)
    # By who compacted.
    b = within(d["wasted"], [lx, d["src"] == 1, d["src"] == 2, start], g)
    out["wasted after sparkjoy"], out["wasted after other"] = b[1], b[2]
    for y in ("corrected", "interrupted"):
        b = within(d[y], [tlx, p1 & (d["tsrc"] == 1), p1 & (d["tsrc"] == 2), p2 & (d["tsrc"] == 1),
                          p2 & (d["tsrc"] == 2), d["tstart"]], tg, w)
        out[y + " after sparkjoy"], out[y + " after other"] = b[1], b[2]
        out[y + " after 2 sparkjoy"], out[y + " after 2 other"] = b[3], b[4]
    # Trouble by context band, against the reference band.
    band, tband = np.digitize(d["x"], BINS) - 1, np.digitize(d["tx"], BINS) - 1
    others = [k for k in range(len(BINS) - 1) if k != REFERENCE]
    for y, dummies, X, gg, ww in (("wasted", band, [post, start], g, None),
                                  ("corrected", tband, [p1, p2, d["tstart"]], tg, w),
                                  ("interrupted", tband, [p1, p2, d["tstart"]], tg, w)):
        b = within(d[y], [dummies == k for k in others] + X, gg, ww)
        for k, v in zip(others, b):
            out[f"{y} band {k}"] = v
        out[f"{y} band {REFERENCE}"] = 0.0
    return out


def trouble(e, key="", j=J, f=F):
    """The share of requests wasted, combining requests and turns: a corrected or interrupted turn
    wastes f of its requests, and corrections are scaled from matches by j. A failed call inside a
    corrected turn counts twice, which at these rates adds under a point to the mean."""
    return e["wasted" + key] + f * (e["corrected" + key] / j + e["interrupted" + key])


def derived(e, m, j=J, f=F, cost=None):
    """The trouble per e-fold of context and by band, and D, from one set of coefficients e; m holds
    the sample's means, and cost(x) prices a request at context x for D."""
    w = m["wasted"] + f * (m["corrected"] / j + m["interrupted"])  # the mean share of requests wasted
    out = {"slope": trouble(e, j=j, f=f)}
    out["profile"] = np.array([trouble(e, f" band {k}", j, f) for k in range(len(BINS) - 1)])
    out["w"], out["band x"] = w, m["band x"]
    # D: the extra trouble over each source's own requests after its compactions, priced at its
    # own context there.
    for key in ("", " sparkjoy", " other"):
        n = (e["wasted after" + key] * m["post requests" + key]
             + f * (e["corrected after" + key] / j + e["interrupted after" + key]) * m["turn 1" + key]
             + f * (e["corrected after 2" + key] / j + e["interrupted after 2" + key]) * m["turn 2" + key])
        out["D requests" + key] = n
        out["D" + key] = n * cost(m["post x" + key]) if cost else np.nan
    return out


def means(d):
    """The sample's mean rates, the mean context in each band, and, after each source's compactions,
    the requests in its SPAN window and first two turns and their mean context."""
    band = np.digitize(d["x"], BINS) - 1
    m = {"wasted": d["wasted"].mean(), "corrected": np.average(d["corrected"], weights=d["tlen"]) - (1 - SPECIFICITY),
         "interrupted": np.average(d["interrupted"], weights=d["tlen"]),
         "band x": np.array([d["x"][band == k].mean() for k in range(len(BINS) - 1)])}
    for key, src in (("", d["src"] > 0), (" sparkjoy", d["src"] == 1), (" other", d["src"] == 2)):
        tsrc = d["tsrc"] > 0 if key == "" else d["tsrc"] == (1 if key == " sparkjoy" else 2)
        windows = np.flatnonzero(src & ~np.roll(src, 1))
        m["post requests" + key] = src.sum() / max(len(windows), 1)
        m["post x" + key] = d["x"][src].mean()
        m["turn 1" + key] = d["tlen"][(d["tpost"] == 1) & tsrc].mean()
        m["turn 2" + key] = d["tlen"][(d["tpost"] == 2) & tsrc].mean()
    return m


def interval(values, q=(5, 95)):
    return np.percentile(np.array(values, float), q, axis=0)


def predicted(x, w, band_x, beta):
    """The trouble at context x, against the reference band, that β predicts if a request's
    effectiveness falls only as trouble rises, η ∝ 1 - trouble, with mean trouble w:
    (1 - w)(1 - η(x) / η(reference band))."""
    return (1 - w) * (1 - fit.eta(x, beta) / fit.eta(band_x[REFERENCE], beta))


def chart(out, x, profile, sessions):
    """Draws model-calibration into `out`: trouble by context band against the reference band, with
    90% intervals, beside what each β in PREDICT would predict. `x` is `derived`'s results, `profile`
    the 90% interval of their profile, and `sessions` how many sessions they come from."""
    import charts
    from charts import plt

    bx, prof = x["band x"], x["profile"]
    lo, hi = profile
    grid = np.linspace(20_000, 900_000, 200)
    names = [f"{BINS[k] / 1e3:.0f}–{BINS[k + 1] / 1e3:.0f}k" if BINS[k + 1] != np.inf else f"{BINS[k] / 1e3:.0f}k+"
             for k in range(len(bx))]

    def points(v, _=None):
        """A difference in percentage points, signed, with a true minus."""
        return f"{v * 100:+.0f}".replace("-", "−") if round(v * 100) else "0"

    out.mkdir(parents=True, exist_ok=True)
    for theme, t in charts.THEMES.items():
        fit_c = t["fit"]
        fig, ax = plt.subplots(figsize=(9, 3.8))
        charts.style(ax, t)
        for beta in PREDICT:
            y = predicted(grid, x["w"], bx, beta)
            keep = grid >= 110_000
            ax.plot(grid[keep], y[keep], color=charts.shade(t, beta), lw=2)
            ax.text(grid[keep][0] - 8_000, y[keep][0], f"if β were {beta:g}", ha="right", va="center", fontsize=9,
                    color=t["ink"])
        # The reference band is 0 by construction, so it is drawn open and named rather than as a measurement.
        rest = np.arange(len(bx)) != REFERENCE
        ax.errorbar(bx[rest], prof[rest], yerr=[(prof - lo)[rest], (hi - prof)[rest]], fmt="o", ms=7, color=t["ink"],
                    mec=t["page"], mew=1.5, elinewidth=1.4, capsize=0, zorder=3)
        ax.plot([bx[REFERENCE]], [0], "o", ms=7, mfc=t["page"], mec=t["ink"], mew=1.5, zorder=3)
        ax.text(bx[REFERENCE], -0.06, "reference", ha="center", va="top", fontsize=9, color=t["muted"])
        ax.text(bx[0] + 25_000, prof[0], points(prof[0]), va="center", fontsize=9, color=fit_c)
        ax.set_xlim(-60_000, 950_000)
        ax.set_xticks(bx, names)
        ax.set_ylim(-1.0, 0.55)
        ax.set_yticks([-1, -0.75, -0.5, -0.25, 0, 0.25, 0.5])
        ax.yaxis.set_major_formatter(points)
        ax.set_xlabel("Context, by band (each drawn at its mean)", color=t["muted"], fontsize=10)
        charts.axis_name(ax, "Requests wasted, in percentage points against the 400–600k band", t)
        handles = [plt.Line2D([], [], color=t["ink"], marker="o", ls="", ms=7, mec=t["page"])]
        legend = fig.legend(handles, [f"measured in {sessions} sessions, with 90% intervals"],
                            frameon=False, fontsize=9, loc="upper left", bbox_to_anchor=(0.1, -0.02))
        legend.get_texts()[0].set_color(fit_c)
        charts.save(fig, out / f"model-calibration-{theme}.svg")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    fit.window_args(ap)
    ap.add_argument("--costs-since", help="fit the cost model to the sessions from here instead (ISO time)")
    ap.add_argument("--costs-until", help="and to here (ISO time)")
    ap.add_argument("--model", default="claude-opus-5-5", choices=fit.PRICES, help="whose prices to fit")
    ap.add_argument("--no-replay", action="store_true", help="skip replaying the sessions with D at the top of its interval")
    ap.add_argument("--charts", type=Path, help="directory to draw the doc's calibration chart into")
    a = ap.parse_args()
    S, since, until = fit.read(a)
    # Outcomes need many sessions; the cost model can come from fewer, more recent ones.
    C = fit.read(argparse.Namespace(since=a.costs_since, until=a.costs_until, projects=a.projects))[0] \
        if a.costs_since else S
    ttl = fit.usual(s.ttl for s in C)
    c = fit.prices(a.model, ttl)
    p = fit.measure(C, ttl)
    h, c0, F0 = fit.terms(p, c)
    R = [rows(s) for s in S]
    d = frame(R)
    m = means(d)
    e = coefficients(d)
    price = lambda z: h * z + c0  # a request at context z, in cached reads
    x = derived(e, m, cost=price)
    rng = np.random.default_rng(0)
    boot_e, boot_x = [], []
    for _ in range(DRAWS):
        dd = frame([R[i] for i in rng.integers(0, len(R), len(R))])
        boot_e.append(coefficients(dd))
        # J is uncertain too: draw it to match its 90% interval from resampling the labels.
        j = float(np.clip(rng.normal(J, J_SD), 0.15, 0.85))
        boot_x.append(derived(boot_e[-1], means(dd), j=j, cost=price))
    E = {k: interval([b[k] for b in boot_e]) for k in e}
    X = {k: interval([b[k] for b in boot_x]) for k in x}

    print(f"{len(S)} sessions, {len(d['x']):,} requests, {len(d['tx']):,} turns the user answered, "
          f"{since:%Y-%m-%d %H:%M}Z to {until:%Y-%m-%d %H:%M}Z")
    if a.costs_since:
        print(f"cost model from {len(C)} sessions, {a.costs_since} to {a.costs_until or 'now'}")
    by = defaultdict(list)
    for s in S:
        for _, how, pre in s.compactions:
            by[how].append(pre or 0)
    print("\ncompactions   n   context before: median (10th to 90th percentile)")
    for how, v in sorted(by.items(), key=lambda kv: -len(kv[1])):
        lo, mid, hi = np.percentile(v, [10, 50, 90]) / 1e3
        print(f"  {how:9s} {len(v):4d}   {mid:.0f}k ({lo:.0f}k to {hi:.0f}k)")

    pct = lambda k, C=E, v=e: f"{v[k]:+.2%} ({C[k][0]:+.2%} to {C[k][1]:+.2%})"
    print("\nper request or turn, against the session's own mean (90%)")
    print("                       mean    per e-fold of context        after a compaction*           at the session's start*")
    for name, mean in (("wasted", d["wasted"].mean()), ("failed", d["failed"].mean()),
                       ("failed, guard aside", (d["failed"] & ~d["guard"]).mean()), ("looked", d["looked"].mean()),
                       ("explored", d["explored"].mean()),
                       ("corrected", np.average(d["corrected"], weights=d["tlen"])),
                       ("interrupted", np.average(d["interrupted"], weights=d["tlen"]))):
        print(f"  {name:20s} {mean:6.2%}   {pct(name):28s} {pct(name + ' after'):29s} {pct(name + ' start')}")
    print(f"  * the first {fit.SPAN} requests, or the first turn. Corrections are CORRECTION's matches, weighted by "
          f"requests;\n    J = {J:.2f} scales them to corrections, {m['corrected'] / J:.1%} of requests. Explored: "
          f"every call it made was a lookup.")

    w = x["w"]
    print(f"\ntrouble, the share of requests wasted, by context band against {BINS[REFERENCE] / 1e3:.0f}k to "
          f"{BINS[REFERENCE + 1] / 1e3:.0f}k (90%)")
    for k in range(len(BINS) - 1):
        hi = "" if BINS[k + 1] == np.inf else f"{BINS[k + 1] / 1e3:.0f}k"
        print(f"  {BINS[k] / 1e3:4.0f}k to {hi:5s} (mean {m['band x'][k] / 1e3:3.0f}k)  "
              f"{x['profile'][k]:+.2%} ({X['profile'][0][k]:+.2%} to {X['profile'][1][k]:+.2%})")
    print(f"  per e-fold: {pct('slope', X, x)}; mean trouble {w:.1%}")

    print()
    for beta in PREDICT:
        print(f"  the cost model at β = {beta:g} would need {predicted(m['band x'][0], w, m['band x'], beta):+.1%} "
              f"trouble below 200k against 400k to 600k")

    print(f"\nD, from the extra trouble in each source's first {fit.SPAN} requests and two turns after a compaction:")
    for key, label in (("", "all"), (" sparkjoy", "sparkjoy's"), (" other", "the user's and Claude Code's")):
        (lo, hi), (dlo, dhi) = X["D requests" + key], X["D" + key]
        print(f"  {label:28s} {x['D requests' + key]:+6.2f} requests ({lo:+.2f} to {hi:+.2f}; turns of "
              f"{m['turn 1' + key]:.0f} and {m['turn 2' + key]:.0f} at {m['post x' + key] / 1e3:.0f}k), "
              f"D = ${x['D' + key] * c.read:+.2f} ({dlo * c.read:+.2f} to {dhi * c.read:+.2f})")
    # The fitted D is near zero, and below zero after sparkjoy's compactions, which come where a piece
    # of work ends. A threshold compacts anywhere, so the results take D as 0 or at the top of its
    # interval, where the user's and Claude Code's compactions reach.
    high = max(X["D"][1], X["D other"][1])
    need = (400_000 - p.K) ** 2 * h / (2 * p.g) - p.K - F0
    print(f"  X* reaches 400k at β = 0 with D = {need / 1e6:.1f}M reads (${need * c.read:.2f}), "
          f"{need / price(m['post x']):.0f} wasted requests per compaction")
    # A LangWatch-style comparison, without the session effects or the context.
    first, rest = d["tpost"] == 1, d["tpost"] == 0
    raw = lambda sel: ((d["corrected"][sel].mean() - (1 - SPECIFICITY)) / J, sel.sum())
    (r1, n1), (r0, n0) = raw(first), raw(rest)
    print(f"  corrections answering the first turn after a compaction: {r1:.0%} of {n1}; after other turns: {r0:.0%} of {n0}")

    k = lambda v: f"{v / 1e3:.0f}k"
    print(f"\nsteady-state X* with D = 0, then with D at the top of its interval, ${high * c.read:.2f}: " + ", ".join(
        f"β = {beta:g}: {k(fit.steady(p, c, beta))}, {k(fit.steady(p, c, beta, high))}" for beta in BETAS))
    if not a.no_replay:
        for lost, label in ((0.0, "D = 0"), (high, "D at the top of its interval")):
            cost, effectiveness = fit.grid(C, p, c, ttl, betas=BETAS, lost=lost)
            T, saved = fit.best(cost, effectiveness)
            at = list(fit.THRESHOLDS).index(400_000)  # sparkjoy's first floor, which the doc compares against
            floor = cost[:, at].sum() / effectiveness[:, at].sum(0) / (cost[:, -1].sum() / effectiveness[:, -1].sum(0))
            print(f"best turn-end threshold, replayed, {label}: " + ", ".join(
                f"β = {beta:g}: {k(T[i])}, saving {1 - saved[i]:.1%} (400k: {1 - floor[i]:.1%})" for i, beta in enumerate(BETAS)))
    if a.charts:
        chart(a.charts, x, X["profile"], len(S))


if __name__ == "__main__":
    main()
