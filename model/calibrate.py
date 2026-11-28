#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy>=2.1", "matplotlib>=3.9"]
# ///
"""Measures how trouble varies with the context in your sessions, and fits u, the share of the
context a compaction loses, from the extra wasted requests after a compaction.

    uv run --script model/calibrate.py --since 2026-09-17T00:00Z --until 2026-11-16T00:00Z \
        --costs-since 2026-11-18T00:00Z --costs-until 2026-11-23T00:00Z

Effectiveness isn't logged, so the script measures what lowers it, as trouble, and how trouble
varies with the context within a session. A request is in trouble when a tool call it made
failed or when it repeated a lookup its context already held; a turn is in trouble when the
user's next message corrects it or they interrupt it. Regressions with a fixed effect for each
session estimate how trouble changes with the context, and how much more of it follows a
compaction, which, priced at the cost model's ρ (fit.redo), gives u, with 90% intervals from
resampling sessions. Outcomes need many sessions, so the cost model that prices them can come from a
shorter, more recent window: the docs take docs.py's.

It writes its figures to the `calibrate` section of docs/figures.json, with the windows it read
(`window`, and `costs_window` for the cost model's). `outcomes` gives each outcome's mean, per
request or per turn weighted by its requests, and how it moves from the session's own mean per
e-fold of context, in the first fit.SPAN requests or the first turn after a compaction, and at the
session's start. A request is wasted when it failed or looked again; `failed_guard_aside` leaves out
requests whose failures were all the worktree guard's; it explored when every call it made was a
lookup. `corrected` counts CORRECTION's matches, which J scales to `corrections_share`, the share of
requests in turns the user corrected. Trouble is against REFERENCE's band. `loss` is what each
source's compactions lost, as wasted requests, dollars and u. `share` is the share threshold at a
turn end, by band and size, with the requests to the next reset from these sessions and the prices
and parameters from the cost model's: at u = 0, the weight λ of u, and at the u the user's and
Claude Code's compactions lose.
"""

import argparse
import re
from collections import defaultdict

import fit
import numpy as np

# A user's message that says the turn it answers went wrong.
CORRECTION = re.compile(
    r"""
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
""",
    re.I | re.X,
)
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
NOT_ANSWERS = re.compile(
    r"^(The \S+ plugin sent a message|\d+ background agents? (was|were) stopped|/\S+\s*$)"
)
LOOKUPS = {
    "Read",
    "Grep",
    "Glob",
}  # calls that only look, so a second identical one finds what the first did
EDITS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}
F = 1.0  # share of a corrected or interrupted turn's requests that are wasted; the script also tries 0 and 2
BINS = np.array([0, 200_000, 300_000, 400_000, 600_000, np.inf])
REFERENCE = 3  # 400k to 600k, where most compactions happen
# The half points searched for the saturating form, 1 - 2^(-x/s), which the comparison of forms fits
# beside the power law.
DRAWS = 200
# The share threshold at a turn end: the bands of context it is given for, with the requests to the
# next reset averaged over each, and the sizes it is given at, with those requests interpolated
# between the bands' means at their median contexts.
BANDS = ((200_000, 300_000), (300_000, 450_000), (450_000, 650_000), (650_000, 965_000))
SIZES = (230_000, 300_000, 400_000, 600_000, 800_000)
# A curve A (1 - FLOOR / x) under the fitted one, whose u the doc gives: a proposed wording of the
# question stated the threshold from a height of 0.25 and a floor of 212k.
LOWER = (0.25, 212_000)


def corrects(text):
    """Whether a user's message corrects the turn it answers, by CORRECTION."""
    return not text.startswith(INTERRUPTED) and bool(
        CORRECTION.search(QUOTED.sub("", PASTED.sub("", text)))
    )


def answered(s):
    """Each turn the user answered, as (its first request, the one after its last, 1 or 2 if it is the
    first or second turn after a compaction or else 0, what the user said). Turns end at a reply,
    or where the user's message cuts in. A turn's answers are what the user typed while it ran
    and the message that started the next one."""
    n = len(s.size)
    answers = [
        (t, text, mid) for t, text, mid in s.said if not NOT_ANSWERS.match(text.strip())
    ]
    starts = [0] + [
        j
        for j in range(1, n)
        if s.end[j - 1]
        or any(s.t[j - 1] < t <= s.t[j] for t, _, mid in s.said if not mid)
    ]
    compacted = None
    for k, a in enumerate(starts):
        b = starts[k + 1] if k + 1 < len(starts) else n
        compacted = k if s.kind[a] in (fit.USER, fit.POLICY) else compacted
        until = s.t[b] if b < n else np.inf
        said = [
            text
            for t, text, mid in answers
            if (s.t[a] if mid else s.t[b - 1]) < t <= until
        ]
        if said:
            yield (
                a,
                b,
                k - compacted + 1 if compacted is not None and k - compacted < 2 else 0,
                said,
            )


def rows(s):
    """One session's requests and answered turns as columns, leaving out requests that logged no
    context, which Claude Code sometimes writes."""
    n = len(s.size)
    # Who made the latest compaction, 1 sparkjoy or 2 anyone else, and src the same for the SPAN
    # requests after it, with the context it compacted.
    by, src, before = np.zeros(n, int), np.zeros(n, int), np.full(n, np.nan)
    for t, how, pre in s.compactions:
        j = np.searchsorted(s.t, t)
        if j < n and s.kind[j] in (fit.USER, fit.POLICY):
            by[j:] = src[j : j + fit.SPAN] = 1 if how == "sparkjoy" else 2
            before[j : j + fit.SPAN] = np.nan if pre is None else pre
    # A lookup repeated with no edit between that could change what it finds: none at all for a
    # search, none to the file for a Read. A command could change either; polling a log that a
    # command writes is the commonest repeat this lets through.
    looked, seen = np.zeros(n, bool), {}
    for j, calls in enumerate(s.calls):
        if s.kind[j] != fit.NORMAL:
            seen = {}
        lookups = {
            (name, digest): path for name, digest, path in calls if name in LOOKUPS
        }
        looked[j] = any(k in seen for k in lookups)
        if edited := {path for name, _, path in calls if name in EDITS}:
            seen = {k: v for k, v in seen.items() if v is not None and v not in edited}
        seen |= lookups
    failed = np.array([bool(f) for f in s.failed])
    explored = np.array(
        [
            bool(calls) and all(name in LOOKUPS for name, _, _ in calls)
            for calls in s.calls
        ]
    )
    ok = s.size > 0
    r = {
        "x": s.size,
        "src": src,
        "before": before,
        "start": (np.arange(n) < fit.SPAN) & (s.kind[0] == fit.FIRST),
        "failed": failed,
        "looked": looked,
        "wasted": failed | looked,
        "explored": explored,
        "guard": np.array(
            [
                bool(f) and all("isolated in the worktree" in e for e in f)
                for f in s.failed
            ]
        ),
    }
    r = {k: v[ok] for k, v in r.items()}
    turns = [
        (
            s.size[b - 1],
            after,
            by[a] if after else 0,
            a == 0 and s.kind[0] == fit.FIRST,
            b - a,
            any(corrects(t) for t in said),
            any(t.startswith(INTERRUPTED) for t in said),
        )
        for a, b, after, said in answered(s)
        if s.size[b - 1] > 0
    ]
    T = np.array(turns, float).reshape(-1, 7)
    for k, col in enumerate(
        ("tx", "tpost", "tsrc", "tstart", "tlen", "corrected", "interrupted")
    ):
        r[col] = T[:, k]
    return r


def within(y, X, g, w=None):
    """Weighted least squares of y on the columns of X with a fixed effect for each group in g."""
    w = np.ones(len(y)) if w is None else w
    sw = np.bincount(g, w)
    demean = lambda v: v - (np.bincount(g, w * v) / np.where(sw > 0, sw, 1))[g]
    yd, Xd = (
        demean(y.astype(float)),
        np.column_stack([demean(c.astype(float)) for c in X]),
    )
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
        out[y], out[y + " after"], out[y + " start"] = within(
            d[y], [lx, post, start], g
        )
    (
        out["failed, guard aside"],
        out["failed, guard aside after"],
        out["failed, guard aside start"],
    ) = within(d["failed"] & ~d["guard"], [lx, post, start], g)
    for y in ("corrected", "interrupted"):
        out[y], out[y + " after"], out[y + " after 2"], out[y + " start"] = within(
            d[y], [tlx, p1, p2, d["tstart"]], tg, w
        )
    # By who compacted.
    b = within(d["wasted"], [lx, d["src"] == 1, d["src"] == 2, start], g)
    out["wasted after sparkjoy"], out["wasted after other"] = b[1], b[2]
    for y in ("corrected", "interrupted"):
        b = within(
            d[y],
            [
                tlx,
                p1 & (d["tsrc"] == 1),
                p1 & (d["tsrc"] == 2),
                p2 & (d["tsrc"] == 1),
                p2 & (d["tsrc"] == 2),
                d["tstart"],
            ],
            tg,
            w,
        )
        out[y + " after sparkjoy"], out[y + " after other"] = b[1], b[2]
        out[y + " after 2 sparkjoy"], out[y + " after 2 other"] = b[3], b[4]
    # Trouble by context band, against the reference band.
    band, tband = np.digitize(d["x"], BINS) - 1, np.digitize(d["tx"], BINS) - 1
    others = [k for k in range(len(BINS) - 1) if k != REFERENCE]
    for y, dummies, X, gg, ww in (
        ("wasted", band, [post, start], g, None),
        ("corrected", tband, [p1, p2, d["tstart"]], tg, w),
        ("interrupted", tband, [p1, p2, d["tstart"]], tg, w),
    ):
        b = within(d[y], [dummies == k for k in others] + X, gg, ww)
        for k, v in zip(others, b, strict=False):
            out[f"{y} band {k}"] = v
        out[f"{y} band {REFERENCE}"] = 0.0
    return out


def trouble(e, key="", j=J, f=F):
    """The share of requests wasted, combining requests and turns: a corrected or interrupted turn
    wastes f of its requests, and corrections are scaled from matches by j. A failed call inside a
    corrected turn counts twice, which at these rates adds under a point to the mean."""
    return e["wasted" + key] + f * (e["corrected" + key] / j + e["interrupted" + key])


def derived(e, m, cost, rho, j=J, f=F):
    """The trouble per e-fold of context and by band, and what a compaction loses, from one set of
    coefficients e; m holds the sample's means, cost(x) prices a request at context x, and ρ
    (fit.redo) turns what the requests cost into u."""
    w = m["wasted"] + f * (
        m["corrected"] / j + m["interrupted"]
    )  # the mean share of requests wasted
    out = {"slope": trouble(e, j=j, f=f)}
    out["profile"] = np.array(
        [trouble(e, f" band {k}", j, f) for k in range(len(BINS) - 1)]
    )
    out["w"], out["band x"] = w, m["band x"]
    # What a compaction loses: the extra trouble over each source's own requests after its
    # compactions, priced at its own context there. u is that loss over ρ x, with x the context
    # the compactions compacted, so redoing u x tokens at ρ each costs as much.
    for key in ("", " sparkjoy", " other"):
        n = (
            e["wasted after" + key] * m["post requests" + key]
            + f
            * (e["corrected after" + key] / j + e["interrupted after" + key])
            * m["turn 1" + key]
            + f
            * (e["corrected after 2" + key] / j + e["interrupted after 2" + key])
            * m["turn 2" + key]
        )
        out["extra requests" + key] = n
        out["loss" + key] = n * cost(m["post x" + key])
        out["u" + key] = out["loss" + key] / (rho * m["before x" + key])
    return out


def means(d):
    """The sample's mean rates, the mean context in each band, and, after each source's compactions,
    the requests in its SPAN window and first two turns, their mean context, and the mean context the
    compactions compacted."""
    band = np.digitize(d["x"], BINS) - 1
    m = {
        "wasted": d["wasted"].mean(),
        "corrected": np.average(d["corrected"], weights=d["tlen"]) - (1 - SPECIFICITY),
        "interrupted": np.average(d["interrupted"], weights=d["tlen"]),
        "band x": np.array([d["x"][band == k].mean() for k in range(len(BINS) - 1)]),
    }
    for key, src in (
        ("", d["src"] > 0),
        (" sparkjoy", d["src"] == 1),
        (" other", d["src"] == 2),
    ):
        tsrc = (
            d["tsrc"] > 0
            if key == ""
            else d["tsrc"] == (1 if key == " sparkjoy" else 2)
        )
        windows = np.flatnonzero(src & ~np.roll(src, 1))
        m["post requests" + key] = src.sum() / max(len(windows), 1)
        m["post x" + key] = d["x"][src].mean()
        m["before x" + key] = np.nanmean(d["before"][windows])
        m["turn 1" + key] = d["tlen"][(d["tpost"] == 1) & tsrc].mean()
        m["turn 2" + key] = d["tlen"][(d["tpost"] == 2) & tsrc].mean()
    return m


def interval(values, q=(5, 95)):
    return np.percentile(np.array(values, float), q, axis=0)


def share(S, p, c, u):
    """The share threshold at a turn end (fit.share_threshold), with the requests to the next reset
    from S and the prices and parameters from p and c: with no detail lost beyond what is read back,
    the weight λ of what is lost (fit.weight), and the threshold less λ u, where a compaction also
    loses the share u. Each band's requests to the reset have a 90% interval from resampling sessions;
    each size's are interpolated between the bands', and give the u that puts the threshold on LOWER."""
    x, n, k = fit.to_reset(S)
    h, _, F0 = fit.terms(p, c)
    star = lambda z, m: fit.share_threshold(p, c, z, m)
    rng = np.random.default_rng(0)
    bands = []
    for lo, hi in BANDS:
        sel = (x >= lo) & (x < hi)
        # Each session's requests to the reset and turn ends in the band, to resample whole sessions.
        ids, at = np.unique(k[sel], return_inverse=True)
        total, ends = np.bincount(at, n[sel]), np.bincount(at)
        boot = []
        for _ in range(DRAWS):
            pick = rng.integers(0, len(ids), len(ids))
            boot.append(total[pick].sum() / ends[pick].sum())
        mean, mid = n[sel].mean(), np.median(x[sel])
        bands.append(
            {
                "from": lo,
                "to": hi,
                "turn_ends": int(sel.sum()),
                "sessions": len(ids),
                "requests_to_reset": {"value": mean, "interval_90": interval(boot)},
                "median_context": mid,
                "threshold": {
                    "value": star(mid, mean),
                    "interval_90": star(mid, interval(boot)),
                },
                "weight": fit.weight(p, c, mean),
                "threshold_at_u": star(mid, mean) - fit.weight(p, c, mean) * u,
            }
        )
    sizes = []
    for z in SIZES:
        m = np.interp(
            z,
            [b["median_context"] for b in bands],
            [b["requests_to_reset"]["value"] for b in bands],
        )
        lower = LOWER[0] * (1 - LOWER[1] / z)
        weight = fit.weight(p, c, m)
        u_lower = (star(z, m) - lower) / weight
        sizes.append(
            {
                "context": z,
                "requests_to_reset": m,
                "threshold": star(z, m),
                "weight": weight,
                "threshold_at_u": star(z, m) - weight * u,
                "lower": lower,
                "u_for_lower": u_lower,
                # What a compaction at z that loses that share costs to redo.
                "u_for_lower_usd": fit.redo(p, c) * u_lower * z * c.read,
            }
        )
    mean = n.mean()
    return {
        "turn_ends": len(x),
        "sessions": len(np.unique(k)),
        "requests_to_reset": mean,
        "rho": fit.redo(p, c),
        "u": u,
        "bands": bands,
        "sizes": sizes,
        "form": {
            "A": (mean * h - 1) / (mean * h + c.write - 1),
            "FLOOR": (mean * h * p.K + F0) / (mean * h - 1),
        },
        "lower": {"A": LOWER[0], "FLOOR": LOWER[1]},
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    fit.window_args(ap)
    ap.add_argument(
        "--costs-since",
        help="fit the cost model to the sessions from here instead (ISO time)",
    )
    ap.add_argument("--costs-until", help="and to here (ISO time)")
    ap.add_argument(
        "--model",
        default="claude-opus-5-5",
        choices=fit.PRICES,
        help="whose prices to fit",
    )
    a = ap.parse_args()
    S, since, until = fit.read(a)
    # Outcomes need many sessions; the cost model can come from fewer, more recent ones.
    C, costs_since, costs_until = (
        fit.read(
            argparse.Namespace(
                since=a.costs_since, until=a.costs_until, projects=a.projects
            )
        )
        if a.costs_since
        else (S, since, until)
    )
    ttl = fit.usual(s.ttl for s in C)
    c = fit.prices(a.model, ttl)
    p = fit.measure(C, ttl)
    h, c0, _ = fit.terms(p, c)
    R = [rows(s) for s in S]
    d = frame(R)
    m = means(d)
    e = coefficients(d)
    price = lambda z: h * z + c0  # a request at context z, in cached reads
    rho = fit.redo(p, c)
    x = derived(e, m, price, rho)
    rng = np.random.default_rng(0)
    boot_e, boot_x = [], []
    for _ in range(DRAWS):
        dd = frame([R[i] for i in rng.integers(0, len(R), len(R))])
        boot_e.append(coefficients(dd))
        # J is uncertain too: draw it to match its 90% interval from resampling the labels.
        j = float(np.clip(rng.normal(J, J_SD), 0.15, 0.85))
        boot_x.append(derived(boot_e[-1], means(dd), price, rho, j=j))
    E = {k: interval([b[k] for b in boot_e]) for k in e}
    X = {k: interval([b[k] for b in boot_x]) for k in x}
    est = lambda k, v=e, ci=E: {"value": v[k], "interval_90": ci[k]}
    stamp = lambda t: f"{t:%Y-%m-%dT%H:%M}Z"

    figures = {
        "window": {
            "since": stamp(since),
            "until": stamp(until),
            "sessions": len(S),
            "requests": len(d["x"]),
            "answered_turns": len(d["tx"]),
        }
    }
    if a.costs_since:
        figures["costs_window"] = {
            "since": stamp(costs_since),
            "until": stamp(costs_until),
            "sessions": len(C),
        }
    by = defaultdict(list)
    for s in S:
        for _, how, pre in s.compactions:
            by[how].append(pre or 0)
    figures["compactions"] = {}
    for how, v in sorted(by.items(), key=lambda kv: -len(kv[1])):
        lo, mid, hi = np.percentile(v, [10, 50, 90])
        figures["compactions"][how] = {
            "count": len(v),
            "context_before": {"median": mid, "p10": lo, "p90": hi},
        }

    figures["outcomes"] = {
        name.replace(",", "").replace(" ", "_"): {
            "mean": mean,
            "per_e_fold": est(name),
            "after_compaction": est(name + " after"),
            "at_session_start": est(name + " start"),
        }
        for name, mean in (
            ("wasted", d["wasted"].mean()),
            ("failed", d["failed"].mean()),
            ("failed, guard aside", (d["failed"] & ~d["guard"]).mean()),
            ("looked", d["looked"].mean()),
            ("explored", d["explored"].mean()),
            ("corrected", np.average(d["corrected"], weights=d["tlen"])),
            ("interrupted", np.average(d["interrupted"], weights=d["tlen"])),
        )
    }
    figures["corrections_share"] = m["corrected"] / J

    figures["trouble"] = {
        "bands": [
            {
                "from": BINS[k],
                "to": BINS[k + 1],
                "mean_context": m["band x"][k],
                "against_reference": {
                    "value": x["profile"][k],
                    "interval_90": X["profile"][:, k],
                },
            }
            for k in range(len(BINS) - 1)
        ],
        "per_e_fold": est("slope", x, X),
        "mean": x["w"],
    }

    figures["loss"] = {
        name: {
            "wasted_requests": est("extra requests" + key, x, X),
            "first_turn_requests": m["turn 1" + key],
            "second_turn_requests": m["turn 2" + key],
            "mean_context": m["post x" + key],
            "context_compacted": m["before x" + key],
            "cost_usd": {
                "value": x["loss" + key] * c.read,
                "interval_90": X["loss" + key] * c.read,
            },
            "u": est("u" + key, x, X),
        }
        for key, name in (
            ("", "all"),
            (" sparkjoy", "sparkjoy"),
            (" other", "user_and_claude_code"),
        )
    }
    # A LangWatch-style comparison, without the session effects or the context.
    first, rest = d["tpost"] == 1, d["tpost"] == 0
    raw = lambda sel: {
        "share": (d["corrected"][sel].mean() - (1 - SPECIFICITY)) / J,
        "turns": sel.sum(),
    }
    figures["raw_corrections"] = {
        "first_turn_after_compaction": raw(first),
        "other_turns": raw(rest),
    }

    # At the u the user's and Claude Code's compactions lose, which the doc's parameters give.
    figures["share"] = share(S, p, c, x["u other"])

    fit.save_figures({"calibrate": figures})
    print(f"wrote the calibrate section of {fit.DOC_FIGURES}")


if __name__ == "__main__":
    main()
