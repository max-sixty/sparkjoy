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
coefficient α, it prints the compaction point the steady-state model gives and
the turn-end threshold that a replay of the sessions finds cheapest per unit
of work. Only transcripts modified since --since are opened, and the process
lowers its own priority, since a full scan of ~/.claude/projects is heavy.
"""

import argparse
import json
import os
import re
from collections import Counter
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

# $/MTok: input, output, 5-minute cache write, 1-hour cache write, cache read.
# https://platform.claude.com/docs/en/about-claude/pricing (November 2026). Of
# these only Claude Haiku 5.5 has a long-context tier, from 100k, and its row is
# that tier's: a compaction leaves the context above 100k, so nearly every request is.
PRICES = {
    "claude-opus-5-5": (4, 20, 5, 8, 0.20),
    "claude-sonnet-5-5": (2, 10, 2.5, 4, 0.10),
    "claude-fable-5-2": (8, 40, 10, 16, 0.20),
    "claude-fable-5-1": (10, 50, 12.5, 20, 0.25),
    "claude-opus-5": (5, 25, 6.25, 10, 0.50),
    "claude-haiku-5-5": (0.5, 2.5, 0.625, 1, 0.05),  # its long-context tier, above 100k
}
PROJECTS = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
WINDOW = 1_000_000
AUTO = 965_000  # where Claude Code compacts a 1M window on its own
SUMMARY = 4_000  # output tokens of the summary request; assumed, as transcripts don't log that request
SPAN = 20  # requests over which re-reads after a compaction are counted
ALPHAS = (0.8, 0.9, 1.0, 1.1, 1.2)
CHART_ALPHAS = tuple(round(k, 2) for k in np.arange(0.6, 2.01, 0.1))
# From below K, where every turn end compacts, to just short of AUTO.
THRESHOLDS = np.arange(50_000, 960_001, 10_000)
DRAWS = 200  # bootstrap resamples of sessions
FLOOR, IDLE_FLOOR = 400_000, 200_000  # where sparkjoy asks, at a turn end and in an idle session (hooks/register.ts)
IDLE = 11 / 12  # share of the cache's lifetime a session sits idle before it is asked (55 of 60 minutes)
# The line sparkjoy writes in the transcript for each verdict (`judge` and `afterTurn` in
# hooks/register.ts), under its current name and the one it had before it was published.
VERDICT = re.compile(r"(?P<plugin>sparkjoy|compact-when-done): (?:(?P<kind>Compacting|Kept|Unreadable verdict|No verdict|"
                     r"Dropped the verdict) at (?P<tokens>\d+)k tokens(?:, idle (?P<idle>\d+) minutes)?|Failed): "
                     r"(?P<reason>.*)", re.S)
KINDS = {"Compacting": "compacted", "Kept": "kept", "Unreadable verdict": "unreadable", "No verdict": "no verdict",
         "Dropped the verdict": "dropped", None: "failed"}
# Where a session's name comes from, in order of preference: one the user gave it, then
# the ones Claude Code generates. Failing all three, its first prompt names it.
TITLES = {"custom-title": "customTitle", "ai-title": "aiTitle", "agent-name": "agentName"}

# Request kinds. A FIRST request opens the session; a USER request is the first
# after a compaction the user typed, which every replayed arm keeps; a POLICY
# request is the first after one that sparkjoy or Claude Code's auto-compaction
# made, which each arm decides for itself; NORMAL is every other request.
FIRST, USER, POLICY, NORMAL = range(4)


@dataclass
class Verdict:
    """One line sparkjoy wrote in a session's transcript."""
    t: float  # seconds since the epoch
    plugin: str  # the name it was written under; as compact-when-done, it logged only compactions
    kind: str  # one of KINDS' values
    tokens: int | None  # the context it was asked at; a failure doesn't say
    idle: int | None  # minutes the session had sat idle, when it was asked for that reason
    reason: str


@dataclass
class Session:
    title: str
    model: str  # the model most of its requests used
    verdicts: list  # what sparkjoy logged
    t: np.ndarray  # when each request started, in seconds since the epoch
    sparkjoy: np.ndarray  # the request is the first after a compaction sparkjoy made
    kind: np.ndarray  # one of the request kinds above
    size: np.ndarray  # prompt tokens: uncached input + cache read + cache write
    read: np.ndarray  # cache-read tokens
    write: np.ndarray  # cache-write tokens
    inp: np.ndarray  # uncached input tokens
    out: np.ndarray  # output tokens
    grew: np.ndarray  # tokens the context gained since the request before
    recent: np.ndarray  # among the SPAN requests after a compaction
    gap: np.ndarray  # seconds since the request before
    idle: np.ndarray  # seconds from the reply before to what started this request's turn, or 0
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


def window_args(ap):
    """The arguments that choose which sessions to read."""
    ap.add_argument("--since", help="ISO time; a bound with no offset is local (default: 14 days ago)")
    ap.add_argument("--until", help="ISO time (default: now)")
    ap.add_argument("--projects", type=Path, default=PROJECTS, help="where Claude Code keeps transcripts (default: %(default)s)")


def read(a):
    """The sessions `window_args` chose, and the window, read at low priority."""
    os.nice(10)
    since = datetime.fromisoformat(a.since).astimezone(UTC) if a.since else datetime.now(UTC) - timedelta(days=14)
    until = datetime.fromisoformat(a.until).astimezone(UTC) if a.until else datetime.now(UTC)
    return load(a.projects, since, until), since, until


def usual(values):
    """The most common of `values`."""
    return Counter(values).most_common(1)[0][0]


def load(root, since, until):
    """The main thread of each interactive session in [since, until)."""
    lo, hi = (t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S") for t in (since, until))
    title_lines = tuple(f'{{"type":"{t}"' for t in TITLES)
    sessions = []
    # A subagent's transcript sits a level deeper. Sorted, so the resampling draws the same sessions
    # whatever order the filesystem lists them in.
    for path in sorted(root.glob("*/*.jsonl")):
        if path.stat().st_mtime < since.timestamp():
            continue
        reqs, comps, prompts, verdicts, entry, asked = {}, [], [], [], None, None
        titles, first, models = {}, None, Counter()
        for raw in path.open(errors="replace"):
            prompt = '"type":"user"' in raw and '"tool_result"' not in raw  # a message that starts a turn
            title = raw.startswith(title_lines)
            if not (prompt or title or '"assistant"' in raw or "compact_boundary" in raw or '"informational"' in raw):
                continue
            try:
                e = json.loads(raw)
            except json.JSONDecodeError:
                continue
            # A session's name and first prompt carry no time, or come before the window.
            if title:
                titles[e["type"]] = e.get(TITLES[e["type"]])
                continue
            if prompt and first is None and e.get("type") == "user" and not e.get("isMeta") and not e.get("isCompactSummary"):
                c = (e.get("message") or {}).get("content")
                text = c if isinstance(c, str) else next((b.get("text") for b in c or [] if b.get("type") == "text"), None)
                first = text if text and not text.startswith("<") else None
            if not lo <= e.get("timestamp", "") < hi or e.get("isSidechain"):
                continue
            t = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")).timestamp()
            if prompt and e.get("type") == "user":
                prompts.append(t)
            elif e.get("type") == "assistant":
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
                if m["id"] not in reqs:
                    models[m.get("model")] += 1
                q = reqs.setdefault(m["id"], [t, (0,) * 5, False, set(), t])
                q[4] = t  # when its last block was logged
                q[1] = tuple(map(max, q[1], v))
                q[2] = q[2] or m.get("stop_reason") == "end_turn"
                q[3] |= {b["input"]["file_path"] for b in m.get("content") or []
                         if b.get("type") == "tool_use" and b.get("name") == "Read" and "file_path" in (b.get("input") or {})}
            elif e.get("subtype") == "informational" and (line := VERDICT.fullmatch(str(e.get("content", "")))):
                kind = KINDS[line["kind"]]
                verdicts.append(Verdict(t, line["plugin"], kind, line["tokens"] and int(line["tokens"]) * 1000,
                                        line["idle"] and int(line["idle"]), line["reason"]))
                asked = t if kind == "compacted" else asked
            elif e.get("subtype") == "compact_boundary":
                md = e.get("compactMetadata") or {}
                took = (md.get("durationMs") or 0) / 1000
                # sparkjoy starts compacting as soon as it writes its line. Of the 351 compactions
                # that followed one in the author's sessions (16 Oct to 15 Nov 2026), 349 began
                # within 2 s of it; the other two were the user's, 20 minutes or more later, after
                # a compaction of sparkjoy's that never ran.
                mod = asked is not None and t - took - asked < 60
                comps.append((t, mod or md.get("trigger") == "auto", took, mod))
                asked = None
        if entry == "cli" and len(reqs) > 1:
            name = next((titles[k] for k in TITLES if titles.get(k)), None) or first or "untitled"
            sessions.append(session(sorted(reqs.values(), key=lambda q: q[0]), comps, sorted(prompts),
                                    title=" ".join(name.split()), model=models.most_common(1)[0][0], verdicts=verdicts))
    return sessions


def session(reqs, comps, prompts, **meta):
    n = len(reqs)
    t = np.array([q[0] for q in reqs])
    # After a turn ends, the session sits idle until the next message arrives (the user's,
    # or a notification's), and sparkjoy's timer runs from the reply to that message.
    idle = np.zeros(n)
    for j in range(1, n):
        if reqs[j - 1][2]:
            done = reqs[j - 1][4]
            k = np.searchsorted(prompts, done, side="right")
            idle[j] = (prompts[k] if k < len(prompts) and prompts[k] <= t[j] else t[j]) - done
    inp, read, write, write5, out = (np.array([q[1][k] for q in reqs]) for k in range(5))
    size = inp + read + write
    kind = np.full(n, NORMAL)
    kind[0] = FIRST
    sparkjoy = np.zeros(n, bool)
    for ct, policy, _, mod in comps:
        j = np.searchsorted(t, ct)  # the first request after the compaction
        if 0 < j < n:
            kind[j] = POLICY if policy else USER
            sparkjoy[j] = mod
    grew = np.maximum(np.diff(size, prepend=0), 0)
    grew[kind != NORMAL] = 0
    # Across a compaction the request's growth can't be told from the compacted
    # prompt; a new message's worth stands in for it.
    grew[kind == POLICY] = np.minimum(write[kind == POLICY], 4_000)
    # The requests after a compaction, whose growth includes what it made them read again.
    recent = np.zeros(n, bool)
    for j in np.flatnonzero((kind == USER) | (kind == POLICY)):
        recent[j + 1:j + SPAN] = True
    return Session(**meta, t=t, sparkjoy=sparkjoy, kind=kind, size=size, read=read, write=write, inp=inp, out=out,
                   grew=grew, recent=recent, gap=np.diff(t, prepend=t[0]), idle=idle,
                   rewrote=(kind == NORMAL) & (write > 0.5 * size) & (size > 50_000),
                   end=np.array([q[2] for q in reqs]), ttl=300 if 2 * write5.sum() >= write.sum() else 3600,
                   durations=[d for _, _, d, _ in comps if d], reads=[q[3] for q in reqs])


def lapsed(s, ttl):
    """Requests that write the whole context again when no compaction precedes them: the cache
    outlived (a gap over `ttl`), or, at the session's own lifetime, a rewrite the log shows for
    another reason. A POLICY request counts too, as each replayed arm drops its compaction."""
    return ((s.kind == NORMAL) | (s.kind == POLICY)) & (s.gap > ttl) | (s.rewrote & (ttl == s.ttl))


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
    lapse_turn_end: float  # share of the cache's lapses that come at a turn end
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
    outlived = cat(lambda s: np.isin(s.kind, (POLICY, NORMAL)) & (s.gap > ttl))
    return Params(
        K=float(np.median([a for a, _ in first])), P=float(np.median([b for _, b in first])),
        # g leaves out the requests just after a compaction, whose extra reading E counts.
        E=excess(turns), rereads=rereads(S), g=float(cat(lambda s: s.grew)[normal & ~cat(lambda s: s.recent)].mean()),
        out=float(cat(lambda s: s.out).mean()), inp=float(cat(lambda s: s.inp).mean()),
        lapse=float(cat(lambda s: lapsed(s, ttl))[cat(lambda s: np.isin(s.kind, (POLICY, NORMAL)))].mean()),
        lapse_turn_end=float(cat(lambda s: np.roll(s.end, 1))[outlived].mean()),
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
    """c(x) = h x + c0 and F(x) = x + F0, from what a request and a compaction write and read:
    a request reads x and writes the g new tokens, or after a lapse everything but the shared
    prefix, x - P; a compaction's next request writes K - P, and later ones E more.
    Returns h, c0, F0 and K', where a compaction leaves the context."""
    h = 1 + p.lapse * (c.write - 1)
    c0 = c.output * p.out + c.input * p.inp + (c.write - 1) * ((1 - p.lapse) * p.g - p.lapse * p.P)
    F0 = c.output * SUMMARY + (c.write - 1) * (p.K - p.P + p.E)
    return h, c0, F0, p.K + p.E


def curve(p, c, alpha):
    """J(X), the long-run cost per unit of work of compacting at X, on a grid of X from K' to AUTO."""
    h, c0, F0, K1 = terms(p, c)
    X = np.append(np.arange(K1 + 1_000, AUTO, 1_000.0), AUTO)
    cost = p.g * (X + F0) + h * (X**2 - K1**2) / 2 + c0 * (X - K1)
    work = WINDOW / alpha * ((X / WINDOW) ** alpha - (K1 / WINDOW) ** alpha)
    return X, cost / work


def steady(p, c, alpha):
    """The compaction point that minimises cost per unit of work; AUTO means not before Claude Code."""
    X, J = curve(p, c, alpha)
    return float(X[J.argmin()])


def closed_form(p, c):
    """X* at α = 1: K' + sqrt(2 g (F0 + K') / h)."""
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
    context reaches `threshold`, and with `idle`, at any turn end from that size after which
    the session sits idle for IDLE of the cache's lifetime, as sparkjoy's timer does."""
    K1, P = p.K + p.E, p.P
    lapse = lapsed(s, ttl)
    # Each arm charges E, all at once, at each compaction it makes, so the requests after a
    # dropped one give up E of their growth, the earliest first, where the re-reading falls.
    growth = s.grew.astype(float)
    for j in np.flatnonzero(s.kind == POLICY):
        left = p.E
        for i in range(j + 1, min(j + SPAN, len(growth))):
            take = min(growth[i], left)
            growth[i], left = growth[i] - take, left - take
    trace = np.empty(len(s.size))
    cost, ctx, pending, ended = 0.0, 0.0, False, False
    for j in range(len(s.size)):
        kind, grew, inp, out = s.kind[j], growth[j], s.inp[j], s.out[j]
        if kind in (FIRST, USER):
            if kind == USER:
                cost += ctx + c.output * SUMMARY
            ctx = s.size[j]
            cost += s.read[j] + c.write * s.write[j] + c.input * inp + c.output * out
        else:
            if pending or (idle is not None and ctx >= idle and s.idle[j] > IDLE * ttl):
                cost += ctx + c.output * SUMMARY  # read from the cache, which the turn end left warm
                # A compaction leaves K', or less where the context held less to begin with,
                # which happens only below K', at a session's first turn ends.
                left = min(K1, ctx)
                ctx, written = left + grew, left - P + grew
            else:
                ctx += grew
                written = ctx - P if lapse[j] else grew
            if ctx >= AUTO:
                # Claude Code compacts before sending, so after a lapse its summary reads uncached input.
                cost += (P + c.input * (ctx - P) if lapse[j] else ctx) + c.output * SUMMARY
                ctx, written = K1 + grew, K1 - P + grew
            cost += max(ctx - written - inp, 0) + c.write * written + c.input * inp + c.output * out
        trace[j] = ctx
        ended = bool(s.end[j])
        pending = (every or ended) and ctx + out >= threshold
    # A compaction after the session's last turn still runs its summary, and so does an idle
    # one: the policy can't know the session is over, and takes it to have sat idle.
    if pending or (idle is not None and ended and ctx >= idle):
        cost += ctx + c.output * SUMMARY
    return cost, trace


def grid(S, p, c, ttl, **kw):
    """cost[s, t] and work[s, t, k] for each session, threshold and α in CHART_ALPHAS, plus
    the never-compacting arm as the last threshold."""
    ks = np.array(CHART_ALPHAS)
    cost = np.empty((len(S), len(THRESHOLDS) + 1))
    work = np.empty((len(S), len(THRESHOLDS) + 1, len(ks)))
    for i, s in enumerate(S):
        for t, threshold in enumerate([*THRESHOLDS, np.inf]):
            cost[i, t], trace = replay(s, p, c, threshold, ttl, **kw)
            work[i, t] = ((trace[:, None] / WINDOW) ** (ks - 1)).sum(0)
    return cost, work


def best(cost, work, rows=slice(None)):
    """Per α: the threshold with the least cost per unit of work (inf for never compacting short
    of AUTO), and that cost against never compacting."""
    J = cost[rows].sum(0)[:, None] / work[rows].sum(0)
    t = J.argmin(0)
    return np.append(THRESHOLDS, np.inf)[t], J[t, np.arange(J.shape[1])] / J[-1]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    window_args(ap)
    ap.add_argument("--model", default="claude-opus-5-5", choices=PRICES, help="whose prices to fit")
    ap.add_argument("--ttl", choices=("5m", "1h"), help="cache lifetime to price (default: the one most sessions used)")
    ap.add_argument("--charts", type=Path, help="directory to draw the doc's charts into")
    a = ap.parse_args()
    S, since, until = read(a)
    ttl = {"5m": 300, "1h": 3600}[a.ttl] if a.ttl else usual(s.ttl for s in S)
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
    print(f"\nh = {h:.3f}, c0 = {c0 / 1e3:,.0f}k, F0 = {F0 / 1e6:.2f}M (${F0 * c.read:.2f}), K' = {K1 / 1e3:.0f}k; "
          f"closed form at α=1: X* = {closed_form(p, c) / 1e3:.0f}k; idle break-even {idle_break_even(p, c) / 1e3:.0f}k")
    print(f"X* at α=1 without E: {closed_form(replace(p, E=0), c) / 1e3:.0f}k; without lapses: "
          f"{closed_form(replace(p, lapse=0), c) / 1e3:.0f}k; with twice the summary: "
          f"{(K1 + np.sqrt(2 * p.g * (F0 + c.output * SUMMARY + K1) / h)) / 1e3:.0f}k")

    print("\nreplaying...", flush=True)
    ks = list(CHART_ALPHAS)
    one = ks.index(1.0)
    cost, work = grid(S, p, c, ttl)
    cost_r, work_r = grid(S, p, c, ttl, every=True)
    T, saved = best(cost, work)
    Tr, _ = best(cost_r, work_r)
    BT = np.array([best(cost, work, d)[0] for d in draws])
    BX = np.array([[steady(b, c, k) for k in CHART_ALPHAS] for b in boot])
    at = {x: list(THRESHOLDS).index(x) for x in THRESHOLDS}
    J = cost.sum(0)[:, None] / work.sum(0)
    k = lambda x: "never" if x == np.inf else f"{x / 1e3:.0f}k"
    print("\n  α    steady X* (90%)   replay at any request   replay at turn ends (90%*)   saved   saved at 400k")
    for alpha in ALPHAS:
        i = ks.index(alpha)
        lo, hi = np.percentile(BX[:, i], [5, 95])
        tlo, thi = np.percentile(BT[:, i], [5, 95], method="nearest")
        print(f"  {alpha:.1f}  {k(steady(p, c, alpha)):>5} ({k(lo)}-{k(hi)})    {k(Tr[i]):>5}                  "
              f"{k(T[i]):>5} ({k(tlo)}-{k(thi)})              {1 - saved[i]:.1%}   {1 - J[at[400_000], i] / J[-1, i]:.1%}")
    print("  * resampling sessions with K, P and E held at their fitted values")
    print("\n  the steady-state point reaches", end="")
    for level in (300_000, 400_000, 600_000, AUTO):
        alpha = next((x for x in np.arange(0.5, 3.0, 0.01) if steady(p, c, x) >= level), None)
        print(f" {level // 1000}k at α = {alpha:.2f};" if alpha else f" {level // 1000}k at no α below 3;", end="")
    h, _, F0, K1 = terms(p, c)
    D = (400_000 - K1) ** 2 * h / (2 * p.g) - K1 - F0
    print(f"\n  at α = 1 it reaches 400k if a compaction also costs {D / 1e6:.1f}M reads (${D * c.read:.2f}) more, "
          f"{D / (400_000 + F0):.1f} times its own cost there, the price of writing {D / (c.write - 1) / 1e3:.0f}k tokens again")

    X, Js = curve(p, c, 1.0)
    r = J[:-1, one] / J[-1, one]
    flat = THRESHOLDS[r <= r.min() + 0.01]
    print(f"\n  at α = 1, turn-end thresholds within a point of the best: {k(flat.min())} to {k(flat.max())}")
    for x in (400_000, 600_000):
        print(f"  compacting at {k(x)} costs more per unit of work than the best by {Js[np.abs(X - x).argmin()] / Js.min() - 1:.1%} "
              f"in the steady state, {J[at[x], one] / J[:, one].min() - 1:.1%} in the replay at turn ends")
    print("\n  context   pays back after   grows back in   (requests, α = 1)")
    for x in (200_000, 250_000, 300_000, 400_000, 600_000):
        print(f"  {x / 1e3:5.0f}k   {(x + F0) / (h * (x - K1)):10.0f}   {(x - K1) / p.g:14.0f}")

    def arm(threshold, idle=None):
        return sum(replay(s, p, c, threshold, ttl, idle=idle)[0] for s in S)

    never = cost[:, -1].sum()
    # Turn-end and idle thresholds chosen together.
    pairs = {(t, i): arm(t, i) for t in range(150_000, 450_001, 50_000) for i in (None, *range(150_000, 300_001, 25_000))}
    (bt, bi), low = min(pairs.items(), key=lambda kv: kv[1])
    print(f"\n  policy at α = 1, compacting wherever it allows      saved   (never compacting: ${never * c.read:,.0f})")
    for label, threshold, idle in ((f"turn ends from {k(FLOOR)} (sparkjoy's FLOOR)", FLOOR, None),
                                   (f"  and idle sessions from {k(IDLE_FLOOR)} (its IDLE_FLOOR)", FLOOR, IDLE_FLOOR),
                                   (f"turn ends from {k(T[one])} (the replay's best alone)", T[one], None),
                                   (f"turn ends from {k(bt)}, " + ("no idle compaction" if bi is None else
                                    f"idle sessions from {k(bi)}") + " (best pair)", bt, bi)):
        print(f"  {label:50s} {1 - pairs.get((threshold, idle), arm(threshold, idle)) / never:6.1%}")
    print("  saved by pair (rows: turn-end threshold; columns: idle threshold, '-' for none):")
    print("        " + " ".join(f"{'-' if i is None else k(i):>6}" for i in (None, *range(150_000, 300_001, 25_000))))
    for t in range(150_000, 450_001, 50_000):
        print(f"  {k(t):>5} " + " ".join(f"{1 - pairs[t, i] / never:6.1%}" for i in (None, *range(150_000, 300_001, 25_000))))

    print("\n  prices             cache   lapses (at turn ends)   h      steady X*   replay at turn ends   saved   idle break-even   (α = 1)")
    for model in ("claude-opus-5-5", "claude-fable-5-2"):
        for t in (3600, 300):
            q, cq = measure(S, t), prices(model, t)
            tt, sv = best(*grid(S, q, cq, t))
            print(f"  {model:17s}  {t // 60:3d}m   {q.lapse:6.2%} ({q.lapse_turn_end:4.0%})          {terms(q, cq)[0]:5.2f}  "
                  f"{k(steady(q, cq, 1.0)):>6}   {k(tt[one]):>10}          {1 - sv[one]:6.1%}   {k(idle_break_even(q, cq)):>8}")


    if a.charts:
        import charts
        charts.draw(a.charts, p, c, cost, work, BT, BX)


if __name__ == "__main__":
    main()
