#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy>=2.1", "matplotlib>=3.9"]
# ///
"""Fits the compaction model of docs/when-to-compact.md to your Claude Code sessions.

    uv run --script model/fit.py                        # the last 14 days of ~/.claude/projects
    uv run --script model/fit.py --since 2026-11-07T12:00Z --until 2026-11-16T00:00Z
    uv run --script model/fit.py --model claude-fable-5-2 --ttl 5m
    uv run --script model/fit.py --charts assets        # also redraw the doc's charts

It reads the main thread of each interactive session (`claude -p` runs are
left out), measures the model's parameters, and prints them with 90% intervals
from resampling sessions. Then, for each value of the context efficiency
coefficient κ, it prints the compaction point the steady-state model gives and
the turn-end threshold that a replay of the sessions finds cheapest per unit
of work. Only transcripts modified since --since are opened, and the process
lowers its own priority, since a full scan of ~/.claude/projects is heavy.
"""

import argparse
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

# $/MTok: input, output, 5-minute cache write, 1-hour cache write, cache read.
# https://platform.claude.com/docs/en/about-claude/pricing (November 2026). None
# of these has a long-context tier; Claude Haiku 5.5 does, from 100k, which is
# below where a compaction leaves the context, so it changes no decision here.
PRICES = {
    "claude-opus-5-5": (4, 20, 5, 8, 0.20),
    "claude-sonnet-5-5": (2, 10, 2.5, 4, 0.10),
    "claude-fable-5-2": (8, 40, 10, 16, 0.20),
}
WINDOW = 1_000_000
AUTO = 965_000  # where Claude Code compacts a 1M window on its own
SUMMARY = 4_000  # output tokens of the summary request; assumed, as transcripts don't log that request
SPAN = 20  # requests over which re-reads after a compaction are counted
KAPPAS = (0.8, 0.9, 1.0, 1.1, 1.2)
CHART_KAPPAS = tuple(round(k, 2) for k in np.arange(0.6, 2.01, 0.1))
THRESHOLDS = np.arange(130_000, 960_001, 10_000)
DRAWS = 200  # bootstrap resamples of sessions
MOD_LINE = ("sparkjoy: Compacting at", "compact-when-done: Compacting at")

# Request kinds. A FIRST request opens the session; a USER request is the first
# after a compaction the user typed, which every replayed arm keeps; a POLICY
# request is the first after one that sparkjoy or Claude Code's auto-compaction
# made, which each arm decides for itself; NORMAL is every other request.
FIRST, USER, POLICY, NORMAL = range(4)


@dataclass
class Session:
    kind: np.ndarray  # one of the request kinds above
    size: np.ndarray  # prompt tokens: uncached input + cache read + cache write
    read: np.ndarray  # cache-read tokens
    write: np.ndarray  # cache-write tokens
    inp: np.ndarray  # uncached input tokens
    out: np.ndarray  # output tokens
    grew: np.ndarray  # tokens the context gained since the request before
    gap: np.ndarray  # seconds since the request before
    rewrote: np.ndarray  # the log shows the whole context written again
    end: np.ndarray  # the request ended a turn (stop_reason end_turn)
    ttl: int  # the cache lifetime the session's writes used, in seconds
    durations: list  # seconds each logged compaction took
    reads: list  # the files each request read with the Read tool


@dataclass
class Prices:
    """Each kind of token in cached reads: uncached input, output, and a cache write at `ttl`."""
    input: float
    output: float
    write: float
    read: float  # $ per token


def prices(model, ttl):
    i, o, w5, w1, r = PRICES[model]
    return Prices(i / r, o / r, (w5 if ttl == 300 else w1) / r, r * 1e-6)


def load(root, since, until):
    """The main thread of each interactive session in [since, until)."""
    lo, hi = (t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S") for t in (since, until))
    sessions = []
    for path in root.glob("*/*.jsonl"):  # a subagent's transcript sits a level deeper
        if path.stat().st_mtime < since.timestamp():
            continue
        reqs, comps, entry, mod = {}, [], None, False
        for raw in path.open(errors="replace"):
            if '"assistant"' not in raw and "compact_boundary" not in raw and "Compacting at" not in raw:
                continue
            try:
                e = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not lo <= e.get("timestamp", "") < hi or e.get("isSidechain"):
                continue
            t = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")).timestamp()
            if e.get("type") == "assistant":
                m = e.get("message") or {}
                u = m.get("usage")
                if not u or m.get("model") == "<synthetic>":
                    continue
                entry = entry or e.get("entrypoint")
                v = (u.get("input_tokens") or 0, u.get("cache_read_input_tokens") or 0,
                     u.get("cache_creation_input_tokens") or 0,
                     (u.get("cache_creation") or {}).get("ephemeral_5m_input_tokens") or 0, u.get("output_tokens") or 0)
                # Claude Code logs each content block as an entry of its own,
                # each repeating the usage, which streaming grows.
                q = reqs.setdefault(m["id"], [t, (0,) * 5, False, set()])
                q[1] = tuple(map(max, q[1], v))
                q[2] = q[2] or m.get("stop_reason") == "end_turn"
                q[3] |= {b["input"]["file_path"] for b in m.get("content") or []
                         if b.get("type") == "tool_use" and b.get("name") == "Read" and "file_path" in (b.get("input") or {})}
            elif e.get("subtype") == "informational" and str(e.get("content", "")).startswith(MOD_LINE):
                mod = True
            elif e.get("subtype") == "compact_boundary":
                md = e.get("compactMetadata") or {}
                comps.append((t, mod or md.get("trigger") == "auto", (md.get("durationMs") or 0) / 1000))
                mod = False
        if entry == "cli" and len(reqs) > 1:
            sessions.append(session(sorted(reqs.values(), key=lambda q: q[0]), comps))
    return sessions


def session(reqs, comps):
    n = len(reqs)
    t = np.array([q[0] for q in reqs])
    inp, read, write, write5, out = (np.array([q[1][k] for q in reqs]) for k in range(5))
    size = inp + read + write
    kind = np.full(n, NORMAL)
    kind[0] = FIRST
    for ct, policy, _ in comps:
        j = np.searchsorted(t, ct)  # the first request after the compaction
        if 0 < j < n:
            kind[j] = POLICY if policy else USER
    grew = np.maximum(np.diff(size, prepend=0), 0)
    grew[kind != NORMAL] = 0
    # Across a compaction the request's growth can't be told from the compacted
    # prompt; a new message's worth stands in for it.
    grew[kind == POLICY] = np.minimum(write[kind == POLICY], 4_000)
    return Session(kind=kind, size=size, read=read, write=write, inp=inp, out=out, grew=grew,
                   gap=np.diff(t, prepend=t[0]), rewrote=(kind == NORMAL) & (write > 0.5 * size) & (size > 50_000),
                   end=np.array([q[2] for q in reqs]), ttl=300 if 2 * write5.sum() >= write.sum() else 3600,
                   durations=[d for _, _, d in comps if d], reads=[q[3] for q in reqs])


def lapsed(s, ttl):
    """Requests that write the whole context again: the cache outlived (a gap over `ttl`), or,
    at the session's own lifetime, a rewrite the log shows for another reason."""
    return (s.kind == NORMAL) & ((s.gap > ttl) | (s.rewrote & (ttl == s.ttl)))


@dataclass
class Params:
    K: float  # context right after a compaction
    P: float  # the part of it read from a cache shared across sessions (system prompt, tools)
    E: float  # extra tokens re-read in the requests after a compaction
    rereads: float  # extra requests, of the SPAN after a compaction, that read a file it dropped
    g: float  # tokens the context gains per request
    out: float  # output tokens per request
    inp: float  # uncached input tokens per request
    lapse: float  # share of requests that write the whole context again
    turn: float  # requests per turn, mean
    turn_median: float
    turn_growth: float  # tokens per turn, mean
    idle_turns: float  # share of turn ends followed by a gap longer than the cache lives
    compactions: int
    duration: float  # median seconds a compaction took


def measure(S, ttl):
    first = [(s.size[j], s.read[j]) for s in S for j in np.flatnonzero((s.kind == USER) | (s.kind == POLICY))]
    normal = np.concatenate([s.kind == NORMAL for s in S])
    cat = lambda f: np.concatenate([f(s) for s in S])
    turns = [t for s in S for t in turn_list(s)]
    m = np.array([len(t[1]) for t in turns])
    gaps = cat(lambda s: np.append(s.gap[1:], 0)[s.end])
    return Params(
        K=float(np.median([a for a, _ in first])), P=float(np.median([b for _, b in first])),
        E=excess(turns), rereads=rereads(S), g=float(cat(lambda s: s.grew)[normal].mean()),
        out=float(cat(lambda s: s.out).mean()), inp=float(cat(lambda s: s.inp).mean()),
        lapse=float(cat(lambda s: lapsed(s, ttl))[normal].mean()),
        turn=float(m.mean()), turn_median=float(np.median(m)),
        turn_growth=float(np.mean([sum(t[1]) for t in turns])), idle_turns=float((gaps > ttl).mean()),
        compactions=len(first), duration=float(np.median([d for s in S for d in s.durations])))


def turn_list(s):
    """Each turn as (what it began after: "compaction", "turn" or "start", its requests' growth)."""
    turns, start = [], 0
    for j in np.flatnonzero(s.end):
        k = s.kind[start]
        after = "start" if k == FIRST else "compaction" if k in (USER, POLICY) else "turn"
        turns.append((after, s.grew[start:j + 1]))
        start = j + 1
    return turns


def excess(turns):
    """How much more the first SPAN requests of a turn grow when it begins after a
    compaction than when it begins after another turn."""
    span = {a: [g[1:SPAN].sum() for b, g in turns if b == a and len(g) >= SPAN]
            for a in ("compaction", "turn")}
    return float(np.mean(span["compaction"]) - np.mean(span["turn"])) if all(span.values()) else 0.0


def rereads(S):
    """How many more of the SPAN requests after a compaction read a file the context had read before
    (one the compaction dropped) than of the SPAN after another turn end (one the context still holds)."""
    n = {"compaction": [], "turn": []}
    for s in S:
        resets, start = (s.kind == USER) | (s.kind == POLICY), 0
        for j in range(1, len(s.reads) - SPAN + 1):
            after = "compaction" if resets[j] else "turn" if s.end[j - 1] else None
            if after and not resets[j + 1:j + SPAN].any() and (before := set().union(*s.reads[start:j])):
                n[after].append(sum(bool(r & before) for r in s.reads[j:j + SPAN]))
            start = j if resets[j] else start
    return float(np.mean(n["compaction"]) - np.mean(n["turn"])) if all(n.values()) else 0.0


# The steady-state model. Costs are in cached reads; see docs/when-to-compact.md.

def terms(p, c):
    """The holding rate h, the per-request cost c0 that doesn't grow with the context,
    the compaction's fixed cost F0, and where a compaction leaves the context, K'."""
    h = 1 + p.lapse * (c.write - 1)
    c0 = c.output * p.out + c.input * p.inp + (c.write - 1) * p.g
    F0 = c.output * SUMMARY + (c.write - 1) * (p.K - p.P + p.E)
    return h, c0, F0, p.K + p.E


def steady(p, c, kappa):
    """The compaction point that minimises cost per unit of work, and that cost against never
    compacting short of AUTO, for a context growing g per request from K'."""
    h, c0, F0, K1 = terms(p, c)
    X = np.append(np.arange(K1 + 1_000, AUTO, 1_000.0), AUTO)
    cost = p.g * (X + F0) + h * (X**2 - K1**2) / 2 + c0 * (X - K1)
    work = WINDOW / kappa * ((X / WINDOW) ** kappa - (K1 / WINDOW) ** kappa)
    J = cost / work
    return float(X[J.argmin()]), float(J.min() / J[-1])


def closed_form(p, c):
    """X* at κ = 1: K' + sqrt(2 g (F0 + K') / h)."""
    h, _, F0, K1 = terms(p, c)
    return K1 + np.sqrt(2 * p.g * (F0 + K1) / h)


def idle_break_even(p, c):
    """The context above which compacting just before the cache lapses costs less than
    writing the context again, counting only the next request."""
    _, _, _, K1 = terms(p, c)
    return (c.output * SUMMARY + c.write * K1) / (c.write - 1)


# The replay: every logged request stays, with its growth, output and timing,
# and only the compactions move.

def replay(s, p, c, threshold, ttl, every=False, idle=None):
    """One session's cost under a policy, with the context each request then carried.
    The policy compacts at the end of any turn (or with `every`, after any request) whose
    context reaches `threshold`, and with `idle`, before a cache lapse at a turn end from that size."""
    K1, P = p.K + p.E, p.P
    lapse = lapsed(s, ttl)
    trace = np.empty(len(s.size))
    cost, ctx, pending, ended = 0.0, 0.0, False, False
    for j in range(len(s.size)):
        kind, grew, inp, out = s.kind[j], s.grew[j], s.inp[j], s.out[j]
        if kind in (FIRST, USER):
            if kind == USER:
                cost += ctx + c.output * SUMMARY
            ctx = s.size[j]
            cost += s.read[j] + c.write * s.write[j] + c.input * inp + c.output * out
        else:
            if pending or (idle is not None and lapse[j] and ended and ctx >= idle):
                cost += ctx + c.output * SUMMARY
                ctx, written = K1 + grew, K1 - P + grew
            else:
                ctx += grew
                written = ctx - P if lapse[j] else grew
            if ctx >= AUTO:
                cost += ctx + c.output * SUMMARY
                ctx, written = K1 + grew, K1 - P + grew
            cost += max(ctx - written - inp, 0) + c.write * written + c.input * inp + c.output * out
        trace[j] = ctx
        ended = bool(s.end[j])
        pending = (every or ended) and ctx + out >= threshold
    if pending:  # a compaction after the session's last turn still runs its summary
        cost += ctx + c.output * SUMMARY
    return cost, trace


def grid(S, p, c, ttl, **kw):
    """cost[s, t] and work[s, t, k] for each session, threshold and κ in CHART_KAPPAS, plus
    the never-compacting arm as the last threshold."""
    ks = np.array(CHART_KAPPAS)
    cost = np.empty((len(S), len(THRESHOLDS) + 1))
    work = np.empty((len(S), len(THRESHOLDS) + 1, len(ks)))
    for i, s in enumerate(S):
        for t, threshold in enumerate([*THRESHOLDS, np.inf]):
            cost[i, t], trace = replay(s, p, c, threshold, ttl, **kw)
            work[i, t] = ((trace[:, None] / WINDOW) ** (ks - 1)).sum(0)
    return cost, work


def best(cost, work, rows=slice(None)):
    """Per κ: the threshold with the least cost per unit of work, and that cost against never compacting."""
    J = cost[rows].sum(0)[:, None] / work[rows].sum(0)
    t = J[:-1].argmin(0)
    return THRESHOLDS[t], J[t, np.arange(J.shape[1])] / J[-1]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--since", help="ISO time; a bound with no offset is local (default: 14 days ago)")
    ap.add_argument("--until", help="ISO time (default: now)")
    ap.add_argument("--projects", type=Path, default=Path.home() / ".claude/projects")
    ap.add_argument("--model", default="claude-opus-5-5", choices=PRICES, help="whose prices to fit")
    ap.add_argument("--ttl", choices=("5m", "1h"), help="cache lifetime to price (default: each session's own)")
    ap.add_argument("--charts", type=Path, help="directory to draw the doc's charts into")
    a = ap.parse_args()
    os.nice(10)
    since = datetime.fromisoformat(a.since).astimezone(UTC) if a.since else datetime.now(UTC) - timedelta(days=14)
    until = datetime.fromisoformat(a.until).astimezone(UTC) if a.until else datetime.now(UTC)
    S = load(a.projects, since, until)
    ttls = [s.ttl for s in S]
    ttl = {"5m": 300, "1h": 3600}[a.ttl] if a.ttl else max(set(ttls), key=ttls.count)
    c = prices(a.model, ttl)
    p = measure(S, ttl)
    rng = np.random.default_rng(0)
    draws = [rng.integers(0, len(S), len(S)) for _ in range(DRAWS)]
    boot = [measure([S[i] for i in d], ttl) for d in draws]

    print(f"{len(S)} sessions, {sum(len(s.size) for s in S):,} requests, {since:%Y-%m-%d %H:%M}Z to {until:%Y-%m-%d %H:%M}Z; "
          f"{a.model} prices, {ttl // 60}-minute cache: a write is {c.write:.0f} reads, an output token {c.output:.0f}")
    print("\nparameter                                                    value   90% interval")
    for name, label, scale in (("K", "context after a compaction (tokens)", 1), ("P", "  of it read from the shared cache", 1),
                               ("E", f"extra tokens re-read in the {SPAN} requests after one", 1),
                               ("rereads", f"extra requests of those {SPAN} re-reading a dropped file", 1),
                               ("g", "growth per request", 1), ("out", "output per request", 1),
                               ("lapse", "requests that rewrite the context (%)", 100),
                               ("turn", "requests per turn, mean", 1), ("turn_median", "requests per turn, median", 1),
                               ("turn_growth", "growth per turn", 1), ("idle_turns", "turn ends the cache doesn't outlive (%)", 100)):
        v, (lo, hi) = getattr(p, name) * scale, np.percentile([getattr(b, name) for b in boot], [5, 95]) * scale
        f = ",.0f" if abs(v) >= 100 else ".2f"
        print(f"  {label:54s} {v:9{f}}   {lo:{f}} to {hi:{f}}")
    h, c0, F0, K1 = terms(p, c)
    print(f"  compactions {p.compactions}, median {p.duration:.0f} s each; summary output {SUMMARY:,} (assumed)")
    size, grew = (np.concatenate([getattr(s, f)[s.kind == NORMAL] for s in S]) for f in ("size", "grew"))
    print("  growth per request by context:", ", ".join(
        f"{lo // 1000}-{lo // 1000 + 100}k {grew[(size >= lo) & (size < lo + 100_000)].mean():,.0f}"
        for lo in range(100_000, 800_000, 100_000)))
    print(f"\nh = {h:.3f}, c0 = {c0 / 1e3:,.0f}k, F0 = {F0 / 1e6:.2f}M, K' = {K1 / 1e3:.0f}k reads; "
          f"closed form at κ=1: X* = {closed_form(p, c) / 1e3:.0f}k; idle break-even {idle_break_even(p, c) / 1e3:.0f}k")

    print("\nreplaying...", flush=True)
    ks = list(CHART_KAPPAS)
    one = ks.index(1.0)
    cost, work = grid(S, p, c, ttl)
    cost_r, work_r = grid(S, p, c, ttl, every=True)
    T, saved = best(cost, work)
    Tr, _ = best(cost_r, work_r)
    BT = np.array([best(cost, work, d)[0] for d in draws])
    BX = np.array([[steady(b, c, k)[0] for k in CHART_KAPPAS] for b in boot])
    at = {x: list(THRESHOLDS).index(x) for x in THRESHOLDS}
    J = cost.sum(0)[:, None] / work.sum(0)
    print("\n  κ    steady X* (90%)   replay at any request   replay at turn ends (90%)   saved   saved at 400k")
    for k in KAPPAS:
        i = ks.index(k)
        x, _ = steady(p, c, k)
        lo, hi = np.percentile(BX[:, i], [5, 95]) / 1e3
        tlo, thi = np.percentile(BT[:, i], [5, 95]) / 1e3
        print(f"  {k:.1f}  {x / 1e3:4.0f}k ({lo:.0f}-{hi:.0f}k)      {Tr[i] / 1e3:4.0f}k                  "
              f"{T[i] / 1e3:4.0f}k ({tlo:.0f}-{thi:.0f}k)              {1 - saved[i]:.1%}   {1 - J[at[400_000], i] / J[-1, i]:.1%}")
    print("\n  the steady-state point reaches", end="")
    for level in (300_000, 400_000, 600_000, AUTO):
        k = next((k for k in np.arange(0.5, 3.0, 0.01) if steady(p, c, k)[0] >= level), None)
        print(f" {level // 1000}k at κ = {k:.2f};" if k else f" {level // 1000}k at no κ below 3;", end="")
    h, _, F0, K1 = terms(p, c)
    D = (400_000 - K1) ** 2 * h / (2 * p.g) - K1 - F0
    print(f"\n  at κ = 1 it reaches 400k if a compaction also costs {D / 1e6:.1f}M reads (${D * c.read:.2f}) more, "
          f"the price of writing {D / (c.write - 1) / 1e3:.0f}k tokens again")

    print("\n  context   pays back after   grows back in   (requests, κ = 1)")
    for x in (200_000, 250_000, 300_000, 400_000, 600_000):
        print(f"  {x / 1e3:5.0f}k   {(x + F0) / (h * (x - K1)):10.0f}   {(x - K1) / p.g:14.0f}")

    def arm(threshold, idle=None):
        return sum(replay(s, p, c, threshold, ttl, idle=idle)[0] for s in S)

    never = cost[:, -1].sum()
    print(f"\n  policy at κ = 1                                   saved   (never compacting: ${never * c.read:,.0f})")
    for label, threshold, idle in (("turn ends from 400k (sparkjoy's FLOOR)", 400_000, None),
                                   ("  and idle from 200k (its IDLE_FLOOR)", 400_000, 200_000),
                                   (f"turn ends from {T[one] / 1e3:.0f}k (the replay's best)", T[one], None),
                                   (f"  and idle from {idle_break_even(p, c) / 1e3:.0f}k (the break-even)", T[one], idle_break_even(p, c))):
        print(f"  {label:48s} {1 - arm(threshold, idle) / never:6.1%}")

    print("\n  prices             cache   lapses   h      steady X*   replay at turn ends   saved   idle break-even   (κ = 1)")
    for model in ("claude-opus-5-5", "claude-fable-5-2"):
        for t in (3600, 300):
            q, cq = measure(S, t), prices(model, t)
            tt, sv = best(*grid(S, q, cq, t))
            print(f"  {model:17s}  {t // 60:3d}m   {q.lapse:6.2%}  {terms(q, cq)[0]:5.2f}  {steady(q, cq, 1.0)[0] / 1e3:6.0f}k   "
                  f"{tt[one] / 1e3:10.0f}k          {1 - sv[one]:6.1%}   {idle_break_even(q, cq) / 1e3:8.0f}k")

    if a.charts:
        import charts
        charts.draw(a.charts, p, c, cost, work, BT, BX)


if __name__ == "__main__":
    main()
