#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy>=2.1", "matplotlib>=3.9"]
# ///
"""Fits s, the context at which a request reaches half of full efficacy, and D, the cost of the
detail a compaction drops, to what happens in your sessions. See "Calibrating s" in
docs/when-to-compact.md.

    uv run --script model/calibrate.py --since 2026-09-17T00:00Z --until 2026-11-16T00:00Z \
        --costs-since 2026-11-07T12:00Z --costs-until 2026-11-16T00:00Z

Progress isn't logged, so the script measures what is lost of it, as trouble, and how trouble
varies with the context within a session. A request is in trouble when a tool call it made
failed or when it repeated a lookup its context already held; a turn is in trouble when the
user's next message corrects it or they interrupt it. Regressions with a fixed effect for each
session estimate how trouble changes with the context, which bounds s and gives α, the exponent
of a power law that would let long contexts hurt, and how much more of it follows a compaction,
which gives D, with 90% intervals from resampling sessions. Outcomes need many sessions, so the
cost model that prices D and finds X* can come from a shorter, more recent window.
"""

import argparse
import re
from collections import defaultdict

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
SCALES = np.arange(5_000, 600_001, 5_000)  # the saturating form's s, searched
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
         "age": np.arange(n), "failed": failed, "looked": looked, "wasted": failed | looked, "explored": explored,
         "guard": np.array([bool(f) and all("isolated in the worktree" in e for e in f) for f in s.failed])}
    r = {k: v[ok] for k, v in r.items()}
    turns = [(s.size[b - 1], after, by[a] if after else 0, a == 0 and s.kind[0] == fit.FIRST, a, b - a,
              any(corrects(t) for t in said), any(t.startswith(INTERRUPTED) for t in said))
             for a, b, after, said in answered(s) if s.size[b - 1] > 0]
    T = np.array(turns, float).reshape(-1, 8)
    for k, col in enumerate(("tx", "tpost", "tsrc", "tstart", "tage", "tlen", "corrected", "interrupted")):
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
    out["wasted, with age"] = within(d["wasted"], [lx, post, start, np.log1p(d["age"])], g)[0]
    for y in ("corrected", "interrupted"):
        out[y], out[y + " after"], out[y + " after 2"], out[y + " start"] = within(d[y], [tlx, p1, p2, d["tstart"]], tg, w)
    for y in ("corrected", "interrupted"):
        out[y + ", with age"] = within(d[y], [tlx, p1, p2, d["tstart"], np.log1p(d["tage"])], tg, w)[0]
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
    """α, the trouble by band, the saturating form's s and D from one set of coefficients e; m holds
    the sample's means, and cost(x) prices a request at context x for D."""
    w = m["wasted"] + f * (m["corrected"] / j + m["interrupted"])  # the mean share of requests wasted
    out = {"slope": trouble(e, j=j, f=f)}
    out["alpha"] = 1 - out["slope"] / (1 - w)
    out["alpha, with age"] = 1 - trouble(e, ", with age", j, f) / (1 - w)
    for g in (0.0, 2.0):
        wg = m["wasted"] + g * (m["corrected"] / j + m["interrupted"])
        out[f"alpha, F = {g:.0f}"] = 1 - trouble(e, j=j, f=g) / (1 - wg)
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


def shapes(b, weight):
    """Fits the forms of η to one set of trouble by band, b from derived(), each band weighted by
    `weight`, the inverse of its resampled variance: the saturating form's best s, and the
    weighted squared error of a flat η, the power law and the saturating form. Under η ∝ 1 - w,
    a form predicts trouble in each band, against the reference, of (1 - w)(1 - η/η_ref)."""
    profile, w, x = b["profile"], b["w"], b["band x"]
    sse = lambda pred: (weight * (profile - pred) ** 2).sum()
    fits = []
    for s in SCALES:
        eta = fit.eta(x, s)
        fits.append(sse((1 - w) * (1 - eta / eta[REFERENCE])))
    lnx = np.log(x / x[REFERENCE])
    # The power law's pred is linear in its coefficient, (1 - w)(1 - (x/x_ref)^(α-1)) ≈ -(1 - w)(α - 1) ln(x/x_ref).
    k = (weight * profile * lnx).sum() / (weight * lnx * lnx).sum()
    return {"scale": SCALES[int(np.argmin(fits))], "sse saturating": min(fits), "sse flat": sse(0 * profile),
            "sse power": sse(k * lnx)}


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


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    fit.window_args(ap)
    ap.add_argument("--costs-since", help="fit the cost model to the sessions from here instead (ISO time)")
    ap.add_argument("--costs-until", help="and to here (ISO time)")
    ap.add_argument("--model", default="claude-opus-5-5", choices=fit.PRICES, help="whose prices to fit")
    ap.add_argument("--no-replay", action="store_true", help="skip replaying the sessions under the fitted s and D")
    a = ap.parse_args()
    S, since, until = fit.read(a)
    # Outcomes need many sessions; the cost model can come from fewer, more recent ones.
    C = fit.read(argparse.Namespace(since=a.costs_since, until=a.costs_until, projects=a.projects))[0] \
        if a.costs_since else S
    ttl = fit.usual(s.ttl for s in C)
    c = fit.prices(a.model, ttl)
    p = fit.measure(C, ttl)
    h, c0, F0, K1 = fit.terms(p, c)
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
    # The bands' trouble varies a great deal in how well it is measured, so the forms are fitted
    # with each band weighted by its resampled variance.
    var = np.var([b["profile"] for b in boot_x], axis=0)
    weight = np.where(var > 0, 1 / np.where(var > 0, var, 1), 0)
    x |= shapes(x, weight)
    for b in boot_x:
        b |= shapes(b, weight)
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

    w = m["wasted"] + F * (m["corrected"] / J + m["interrupted"])
    print(f"\ntrouble, the share of requests wasted, by context band against {BINS[REFERENCE] / 1e3:.0f}k to "
          f"{BINS[REFERENCE + 1] / 1e3:.0f}k (90%)")
    for k in range(len(BINS) - 1):
        hi = "" if BINS[k + 1] == np.inf else f"{BINS[k + 1] / 1e3:.0f}k"
        print(f"  {BINS[k] / 1e3:4.0f}k to {hi:5s} (mean {m['band x'][k] / 1e3:3.0f}k)  "
              f"{x['profile'][k]:+.2%} ({X['profile'][0][k]:+.2%} to {X['profile'][1][k]:+.2%})")
    print(f"  per e-fold: {pct('slope', X, x)}; mean trouble {w:.1%}")

    ci = lambda key: f"{x[key]:.3f} ({X[key][0]:.3f} to {X[key][1]:.3f})"
    print(f"\npower law, α = {ci('alpha')}; controlling for requests since the session started, {ci('alpha, with age')}; "
          f"counting a corrected turn as none of its requests wasted, {ci('alpha, F = 0')}, or twice them, "
          f"{ci('alpha, F = 2')}")
    s_lo, s_hi = X["scale"]
    print(f"saturating form: s = {x['scale'] / 1e3:.0f}k (90%: {s_lo / 1e3:.0f}k to {s_hi / 1e3:.0f}k); squared error over the "
          f"bands, weighted: flat {x['sse flat']:.1f}, power {x['sse power']:.1f}, saturating {x['sse saturating']:.1f}")
    for s in (50_000, 100_000, 200_000):
        eta = fit.eta(m["band x"], s)
        print(f"  s = {s / 1e3:.0f}k would need {(1 - w) * (1 - eta[0] / eta[REFERENCE]):+.1%} trouble below 200k "
              f"against 400k to 600k")

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
    need = (400_000 - K1) ** 2 * h / (2 * p.g) - K1 - F0
    print(f"  X* reaches 400k at s = 0 with D = {need / 1e6:.1f}M reads (${need * c.read:.2f}), "
          f"{need / price(m['post x']):.0f} wasted requests per compaction")
    # A LangWatch-style comparison, without the session effects or the context.
    first, rest = d["tpost"] == 1, d["tpost"] == 0
    raw = lambda sel: ((d["corrected"][sel].mean() - (1 - SPECIFICITY)) / J, sel.sum())
    (r1, n1), (r0, n0) = raw(first), raw(rest)
    print(f"  corrections answering the first turn after a compaction: {r1:.0%} of {n1}; after other turns: {r0:.0%} of {n0}")

    k = lambda v: f"{v / 1e3:.0f}k"
    print(f"\nsteady-state X*: s = 0, D = 0: {k(fit.steady(p, c))}; fitted s: {k(fit.steady(p, c, x['scale']))} "
          f"(at its 95th percentile, {k(fit.steady(p, c, s_hi))}); with D at the top of its interval, "
          f"${high * c.read:.2f}: {k(fit.steady(p, c, x['scale'], high))}")
    if not a.no_replay:
        ks = (x["scale"], s_hi)
        for lost, label in ((0.0, "D = 0"), (high, "D at the top of its interval")):
            cost, progress = fit.grid(C, p, c, ttl, scales=ks, lost=lost)
            T, saved = fit.best(cost, progress)
            at = list(fit.THRESHOLDS).index(fit.FLOOR)
            floor = cost[:, at].sum() / progress[:, at].sum(0) / (cost[:, -1].sum() / progress[:, -1].sum(0))
            print(f"best turn-end threshold, replayed, {label}: " + ", ".join(
                f"s = {k(q)}: {k(T[i])}, saving {1 - saved[i]:.1%} (400k: {1 - floor[i]:.1%})" for i, q in enumerate(ks)))


if __name__ == "__main__":
    main()
