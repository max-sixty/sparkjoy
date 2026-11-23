#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy>=2.1", "matplotlib>=3.9"]
# ///
"""Fits the compaction model of docs/cost-model.md to your Claude Code sessions.

    uv run --script model/fit.py                        # the last 14 days of ~/.claude/projects
    uv run --script model/fit.py --model claude-fable-5-2 --ttl 5m
    uv run --script model/fit.py --since 2026-11-18T00:00Z --until 2026-11-23T00:00Z --charts assets

It reads the main thread of each interactive session (`claude -p` runs are
left out), measures the model's parameters, and prints them with 90% intervals
from resampling sessions. Then, at the base case BASE for β, the exponent by which a
request's effectiveness grows with its context, it prints the compaction threshold the
steady-state model gives and how it moves with each parameter and with β, the turn-end
threshold and the pair of floors that a replay of the sessions finds cheapest per
unit of effectiveness, and both at each model's prices. Only transcripts modified since
--since are opened, and the process lowers its own priority, since a full scan
of ~/.claude/projects is heavy.

The last is the doc's run. Its --charts also draws into assets/ the doc's charts,
which charts.py lists, and prints the figures the doc quotes from them.
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
PROJECTS = (
    Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
)
WINDOW = 1_000_000
AUTO = 965_000  # where Claude Code compacts a 1M window on its own
SPAN = 20  # requests over which re-reads after a compaction are counted
# β, the exponent of a request's effectiveness at context x, (x / WINDOW)^β: each doubling of the
# context makes a request 2^β times as effective, and at 0 every request is equally effective. No
# session measures it, so every figure takes this base case, where a request at 250k gets half as
# much done as one at 1M, and the sensitivity to β is reported beside the measured parameters'. The
# threshold chart draws the most efficient threshold at each of CHART_BETAS.
BASE = 0.5
CHART_BETAS = tuple(i / 20 for i in range(21))
# The base case where work differs, as (share of the turns, β): most work's context loses its worth
# quickly, and a quarter of it needs much more of its context. Its mean β is BASE. No session
# measures it either.
MIX = ((0.75, 0.25), (0.25, 1.25))
RIGHT = 0.9  # how often sparkjoy's judgment names the kind of work that comes next
STRETCH = 5  # turns in a row of one kind of work, on average
MIX_DRAWS = 10  # draws of which turns are of which kind, pooled
# Values of D, the detail a compaction drops, in dollars, for the table under "What does the model assume a
# compaction loses?" in docs/cost-model.md. The doc fits no value of D.
LOST = (0.25, 1, 4)
# From below K, where every turn end compacts, to just short of AUTO.
THRESHOLDS = np.arange(50_000, 960_001, 10_000)
DRAWS = 200  # bootstrap resamples of sessions
FLOOR, IDLE_FLOOR = (
    250_000,
    175_000,
)  # where sparkjoy asks, at a turn end and in an idle session (hooks/register.ts)
ASKS = (FLOOR, IDLE_FLOOR)  # as the arms that model sparkjoy pass to `replay`
IDLE = {
    3600: 55 * 60,
    300: 4 * 60,
}  # seconds idle before sparkjoy asks, by cache lifetime (IDLE_MS, hooks/register.ts)
# The line sparkjoy writes in the transcript for each verdict (`judge` and `afterTurn` in
# hooks/register.ts), under its current name and the one it had before it was published.
VERDICT = re.compile(
    r"(?P<plugin>sparkjoy|compact-when-done): (?:(?P<kind>Compacting|Would compact|Kept|Unreadable verdict|"
    r"No verdict|Dropped the verdict) at (?P<tokens>\d+)k tokens(?:, idle (?P<idle>\d+) minutes)?|Failed): "
    r"(?P<reason>.*)",
    re.S,
)
# The line it writes before a verdict whose question read less than nine tenths of its input from
# the prompt cache (MISSED in hooks/register.ts), which in an idle session goes on to say why.
MISSED = re.compile(
    r"sparkjoy: Missed the prompt cache at \d+k tokens(?:, idle \d+ minutes)?: "
    r"read (?P<read>\d+)k of (?P<input>\d+)k from it(?:\. .*)?"
)
# Every line sparkjoy writes in the transcript, under either name, and those Claude Code writes
# about it there.
LINE = ("sparkjoy: ", "compact-when-done: ")
KINDS = {
    "Compacting": "compacted",
    "Would compact": "would compact",
    "Kept": "kept",
    "Unreadable verdict": "unreadable",
    "No verdict": "no verdict",
    "Dropped the verdict": "dropped",
    None: "failed",
}
# Where a session's name comes from, in order of preference: one the user gave it, then
# the ones Claude Code generates. Failing all three, its first prompt names it.
TITLES = {
    "custom-title": "customTitle",
    "ai-title": "aiTitle",
    "agent-name": "agentName",
}

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
    idle: (
        int | None
    )  # minutes the session had sat idle, when it was asked for that reason
    reason: str
    missed: tuple | None = (
        None  # tokens of its question's input read from the cache, and in all, when it missed
    )


@dataclass
class Session:
    path: Path  # its transcript
    title: str
    model: str  # the model most of its requests used
    version: str  # the Claude Code release that wrote its last request
    background: bool  # it ran in the background (`claude --bg`), where Claude Code compacts no idle session itself
    verdicts: list  # what sparkjoy logged
    lines: list  # every line in `LINE`, as (seconds since the epoch, text)
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
    idle: (
        np.ndarray
    )  # seconds from the reply before to what started this request's turn, or 0
    rewrote: np.ndarray  # the log shows the whole context written again
    end: np.ndarray  # the request ended a turn (stop_reason end_turn)
    ttl: int  # the cache lifetime the session's writes used, in seconds
    durations: list  # seconds each logged compaction took
    reads: list  # the files each request read with the Read tool
    calls: list  # each request's tool calls, as (name, digest of the input, the file_path it names or None)
    failed: list  # each request's tool calls whose result was an error, as the result's opening text
    said: list  # what the user typed, as (seconds since the epoch, text, whether it came mid-turn)
    compactions: list  # (seconds since the epoch, "sparkjoy", "idle", "auto" or "user", tokens before)
    summaries: (
        list  # the text of each compaction's summary, in tokens at 4 characters each
    )
    # Each request's visible output, its text and tool calls' input as JSON, in tokens at 4
    # characters each; NaN for a request on a model other than the session's.
    visible: np.ndarray


@dataclass
class Prices:
    """Each kind of token in cached reads: uncached input, output, and a cache write at `ttl`."""

    input: float
    output: float
    write: float
    read: float  # $ per token


MODEL = "claude-opus-5-5"  # whose list prices the docs' figures are at


def prices(model, ttl):
    i, o, w5, w1, r = PRICES[model]
    return Prices(i / r, o / r, (w5 if ttl == 300 else w1) / r, r * 1e-6)


def window_args(ap):
    """The arguments that choose which sessions to read."""
    ap.add_argument(
        "--since",
        help="ISO time; a bound with no offset is local (default: 14 days ago)",
    )
    ap.add_argument("--until", help="ISO time (default: now)")
    projects_arg(ap)


def projects_arg(ap):
    """The argument that says where the transcripts are."""
    ap.add_argument(
        "--projects",
        type=Path,
        default=PROJECTS,
        help="where Claude Code keeps transcripts (default: %(default)s)",
    )


def read(a):
    """The sessions `window_args` chose, and the window, read at low priority where the system has
    one: Windows has no `os.nice`."""
    if hasattr(os, "nice"):
        os.nice(10)
    since = (
        datetime.fromisoformat(a.since).astimezone(UTC)
        if a.since
        else datetime.now(UTC) - timedelta(days=14)
    )
    until = (
        datetime.fromisoformat(a.until).astimezone(UTC)
        if a.until
        else datetime.now(UTC)
    )
    return load(a.projects, since, until), since, until


def usual(values):
    """The most common of `values`."""
    return Counter(values).most_common(1)[0][0]


def text_of(content):
    """The text of a message's content, which is a string or a list of blocks."""
    if isinstance(content, str):
        return content
    return "\n".join(
        b.get("text") or ""
        for b in content or []
        if isinstance(b, dict) and b.get("type") == "text"
    )


def typed(e):
    """The text of a user line the user typed, or None: a skill body, another agent's message, a
    compaction's summary, a task notification and a slash command are not."""
    if e.get("isMeta") or e.get("isCompactSummary"):
        return None
    text = text_of((e.get("message") or {}).get("content"))
    # Some releases log another session's message without isMeta.
    return (
        text
        if text and not text.startswith(("<", "Another Claude session sent a message"))
        else None
    )


def load(root, since, until):
    """The main thread of each interactive session in [since, until)."""
    # A subagent's transcript sits a level deeper. Sorted, so the resampling draws the same sessions
    # whatever order the filesystem lists them in.
    return [
        s
        for path in sorted(root.glob("*/*.jsonl"))
        if path.stat().st_mtime >= since.timestamp()
        and (s := transcript(path, since, until))
    ]


def transcript(path, since=None, until=None):
    """The main thread of the session whose transcript is at `path`, in [since, until) or all of
    it, or None for a `claude -p` run or a session of one request."""
    # Compared with each entry's ISO timestamp, which lies between "0" and "~" when it has one.
    lo, hi = (
        t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S") if t else default
        for t, default in ((since, "0"), (until, "~"))
    )
    title_lines = tuple(f'{{"type":"{t}"' for t in TITLES)
    reqs, comps, prompts, verdicts, lines = {}, [], [], [], []
    entry, version, asked, missed, background = None, None, None, None, False
    titles, first, models, said, failed, summaries = {}, None, Counter(), [], {}, []
    for raw in path.open(errors="replace"):
        prompt = (
            '"type":"user"' in raw and '"tool_result"' not in raw
        )  # a message that starts a turn
        title = raw.startswith(title_lines)
        error = '"is_error":true' in raw
        queued = '"queued_command"' in raw  # a message typed while the turn ran
        summary = '"isCompactSummary":true' in raw  # its text may quote a tool result
        if not (
            prompt
            or title
            or error
            or queued
            or summary
            or '"assistant"' in raw
            or "compact_boundary" in raw
            or '"informational"' in raw
        ):
            continue
        try:
            e = json.loads(raw)
        except json.JSONDecodeError:
            continue
        # A session's name and first prompt carry no time, or come before the window.
        if title:
            titles[e["type"]] = e.get(TITLES[e["type"]])
            continue
        if prompt and first is None and e.get("type") == "user":
            first = typed(e)
        if not lo <= e.get("timestamp", "") < hi or e.get("isSidechain"):
            continue
        t = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")).timestamp()
        if e.get("isCompactSummary"):
            summaries.append(len(text_of((e.get("message") or {}).get("content"))) / 4)
        if prompt and e.get("type") == "user":
            prompts.append(t)
            if text := typed(e):
                said.append((t, text, False))
        elif e.get("type") == "attachment":
            # Plain text typed mid-turn is folded into the turn at its next tool result. Its
            # timestamp is when it was typed; another agent's message carries isMeta.
            a = e.get("attachment") or {}
            if (
                a.get("type") == "queued_command"
                and a.get("commandMode") == "prompt"
                and not a.get("isMeta")
            ):
                if text := text_of(a.get("prompt")):
                    said.append((t, text, True))
        elif error and e.get("type") == "user":
            for b in (e.get("message") or {}).get("content") or []:
                if (
                    isinstance(b, dict)
                    and b.get("type") == "tool_result"
                    and b.get("is_error")
                ):
                    c = b.get("content")
                    failed[b.get("tool_use_id")] = (
                        c if isinstance(c, str) else text_of(c)
                    )[:200]
        elif e.get("type") == "assistant":
            m = e.get("message") or {}
            u = m.get("usage")
            if not u or m.get("model") == "<synthetic>":
                continue
            entry = entry or e.get("entrypoint")
            background = background or e.get("sessionKind") == "bg"
            version = e.get("version") or version
            v = (
                u.get("input_tokens") or 0,
                u.get("cache_read_input_tokens") or 0,
                u.get("cache_creation_input_tokens") or 0,
                (u.get("cache_creation") or {}).get("ephemeral_5m_input_tokens") or 0,
                u.get("output_tokens") or 0,
            )
            # Claude Code logs each content block as an entry of its own,
            # each repeating the usage, which streaming grows.
            if m["id"] not in reqs:
                models[m.get("model")] += 1
            q = reqs.setdefault(
                m["id"], [t, (0,) * 5, False, set(), t, {}, {}, m.get("model")]
            )
            q[4] = t  # when its last block was logged
            q[1] = tuple(map(max, q[1], v))
            q[2] = q[2] or m.get("stop_reason") == "end_turn"
            for n, b in enumerate(m.get("content") or []):
                # Its visible characters, each block counted once however often it is logged.
                if b.get("type") == "text":
                    q[6][e.get("uuid"), n] = len(b.get("text") or "")
                elif b.get("type") == "tool_use":
                    i = b.get("input") or {}
                    q[5][b.get("id")] = (
                        b.get("name"),
                        hash(json.dumps(i, sort_keys=True)),
                        i.get("file_path"),
                    )
                    q[6][b.get("id")] = len(json.dumps(i))
                    if b.get("name") == "Read" and "file_path" in i:
                        q[3].add(i["file_path"])
        elif e.get("subtype") == "informational":
            text = str(e.get("content", ""))
            if text.startswith(LINE):
                lines.append((t, text))
            if line := MISSED.fullmatch(text):
                missed = int(line["read"]) * 1000, int(line["input"]) * 1000
            elif line := VERDICT.fullmatch(text):
                kind = KINDS[line["kind"]]
                verdicts.append(
                    Verdict(
                        t,
                        line["plugin"],
                        kind,
                        line["tokens"] and int(line["tokens"]) * 1000,
                        line["idle"] and int(line["idle"]),
                        line["reason"],
                        missed,
                    )
                )
                missed = None
                asked = t if kind == "compacted" else asked
            elif (
                text.startswith("Compacted while idle")
                and comps
                and t - comps[-1][0] < 10
            ):
                # Claude Code's idle compaction logs as an auto compaction, then says so.
                comps[-1][4] = "idle"
        elif e.get("subtype") == "compact_boundary":
            md = e.get("compactMetadata") or {}
            took = (md.get("durationMs") or 0) / 1000
            # sparkjoy starts compacting as soon as it writes its line. Of the 351 compactions
            # that followed one in my sessions (16 Oct to 15 Nov 2026), 349 began
            # within 2 s of it; the other two were the user's, 20 minutes or more later, after
            # a compaction of sparkjoy's that never ran.
            mod = asked is not None and t - took - asked < 60
            auto = md.get("trigger") == "auto"
            comps.append(
                [
                    t,
                    mod or auto,
                    took,
                    mod,
                    "sparkjoy" if mod else "auto" if auto else "user",
                    md.get("preTokens"),
                ]
            )
            asked = None
    if entry != "cli" or len(reqs) < 2:
        return None
    name = (
        next((titles[k] for k in TITLES if titles.get(k)), None) or first or "untitled"
    )
    return session(
        sorted(reqs.values(), key=lambda q: q[0]),
        comps,
        sorted(prompts),
        failed,
        path=path,
        title=" ".join(name.split()),
        model=models.most_common(1)[0][0],
        version=version,
        background=background,
        verdicts=verdicts,
        lines=lines,
        said=sorted(said),
        summaries=summaries,
    )


def session(reqs, comps, prompts, failed, **meta):
    n = len(reqs)
    t = np.array([q[0] for q in reqs])
    # After a turn ends, the session sits idle until the next message arrives (the user's,
    # or a notification's), and sparkjoy's timer runs from the reply to that message.
    idle = np.zeros(n)
    for j in range(1, n):
        if reqs[j - 1][2]:
            done = reqs[j - 1][4]
            k = np.searchsorted(prompts, done, side="right")
            idle[j] = (
                prompts[k] if k < len(prompts) and prompts[k] <= t[j] else t[j]
            ) - done
    inp, read, write, write5, out = (
        np.array([q[1][k] for q in reqs]) for k in range(5)
    )
    size = inp + read + write
    kind = np.full(n, NORMAL)
    kind[0] = FIRST
    sparkjoy = np.zeros(n, bool)
    for ct, policy, _, mod, _, _ in comps:
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
        recent[j + 1 : j + SPAN] = True
    return Session(
        **meta,
        t=t,
        sparkjoy=sparkjoy,
        kind=kind,
        size=size,
        read=read,
        write=write,
        inp=inp,
        out=out,
        grew=grew,
        recent=recent,
        gap=np.diff(t, prepend=t[0]),
        idle=idle,
        rewrote=(kind == NORMAL) & (write > 0.5 * size) & (size > 50_000),
        end=np.array([q[2] for q in reqs]),
        ttl=300 if 2 * write5.sum() >= write.sum() else 3600,
        durations=[c[2] for c in comps if c[2]],
        reads=[q[3] for q in reqs],
        calls=[list(q[5].values()) for q in reqs],
        failed=[[failed[i] for i in q[5] if i in failed] for q in reqs],
        compactions=[(c[0], c[4], c[5]) for c in comps],
        visible=np.array(
            [sum(q[6].values()) / 4 if q[7] == meta["model"] else np.nan for q in reqs]
        ),
    )


def lapsed(s, ttl):
    """Requests that write the whole context again when no compaction precedes them: the cache
    outlived (a gap over `ttl`), or, at the session's own lifetime, a rewrite the log shows for
    another reason. A POLICY request counts too, as each replayed arm drops its compaction."""
    return ((s.kind == NORMAL) | (s.kind == POLICY)) & (s.gap > ttl) | (
        s.rewrote & (ttl == s.ttl)
    )


@dataclass
class Params:
    K: float  # tokens of context after a compaction: the first request after it, plus E
    # The extra tokens the next few requests read again, part of K, which the replay takes out
    # of the growth after each compaction it drops.
    E: float
    P: float  # tokens of the first request read from a cache shared across sessions (system prompt, tools)
    N: float  # output tokens of the compaction request: the summary's text plus its thinking
    rereads: float  # extra requests, of the SPAN after a compaction, that read a file it dropped
    g: float  # tokens the context gains per request
    out: float  # output tokens per request
    inp: float  # uncached input tokens per request
    lapse: float  # share of requests that write the whole context again
    lapse_turn_end: float  # share of the cache's lapses that come at a turn end
    turn: float  # requests per turn, mean
    turn_median: float
    turn_growth: float  # tokens per turn, mean
    idle_turns: (
        float  # share of turn ends followed by a gap longer than the cache lives
    )
    compactions: int
    duration: float  # median seconds a compaction took

    @property
    def first_request(self):
        """The first request's context after a compaction: K but E."""
        return self.K - self.E


def measure(S, ttl):
    first = [
        (s.size[j], s.read[j])
        for s in S
        for j in np.flatnonzero((s.kind == USER) | (s.kind == POLICY))
    ]
    normal = np.concatenate([s.kind == NORMAL for s in S])
    cat = lambda f: np.concatenate([f(s) for s in S])
    turns = [t for s in S for t in turn_list(s)]
    m = np.array([len(t[1]) for t in turns])
    gaps = cat(lambda s: np.append(s.gap[1:], 0)[s.end])
    outlived = cat(lambda s: np.isin(s.kind, (POLICY, NORMAL)) & (s.gap > ttl))
    E = excess(turns)
    return Params(
        K=float(np.median([a for a, _ in first])) + E,
        E=E,
        P=float(np.median([b for _, b in first])),
        N=summary_output(S)[0],
        # g leaves out the requests just after a compaction, whose extra reading E counts.
        rereads=rereads(S),
        g=float(cat(lambda s: s.grew)[normal & ~cat(lambda s: s.recent)].mean()),
        out=float(cat(lambda s: s.out).mean()),
        inp=float(cat(lambda s: s.inp).mean()),
        lapse=float(
            cat(lambda s: lapsed(s, ttl))[
                cat(lambda s: np.isin(s.kind, (POLICY, NORMAL)))
            ].mean()
        ),
        lapse_turn_end=float(cat(lambda s: np.roll(s.end, 1))[outlived].mean()),
        turn=float(m.mean()),
        turn_median=float(np.median(m)),
        turn_growth=float(np.mean([sum(t[1]) for t in turns])),
        idle_turns=float((gaps > ttl).mean()),
        compactions=len(first),
        duration=float(np.median([d for s in S for d in s.durations])),
    )


def summary_output(S):
    """N, the output tokens of the request that writes a compaction's summary. Transcripts log the
    summary's text but not that request, so its thinking is unknown. The requests of about the
    summary's size stand in for it: those on their session's model whose visible output is half to
    1.5 times the median summary text. N is that median times their median ratio of output tokens,
    thinking included, to visible output. Returns N, the text's median, the ratio, and how many
    summaries and requests they come from."""
    text = np.median([n for s in S for n in s.summaries])
    vis, out = (np.concatenate([getattr(s, f) for s in S]) for f in ("visible", "out"))
    near = (vis >= text / 2) & (vis <= 1.5 * text)
    ratio = np.median(out[near] / vis[near])
    return (
        float(text * ratio),
        float(text),
        float(ratio),
        sum(len(s.summaries) for s in S),
        int(near.sum()),
    )


def turn_list(s):
    """Each turn as (what it began after: "compaction", "turn" or "start", its requests' growth)."""
    turns, start = [], 0
    for j in np.flatnonzero(s.end):
        k = s.kind[start]
        after = (
            "start" if k == FIRST else "compaction" if k in (USER, POLICY) else "turn"
        )
        turns.append((after, s.grew[start : j + 1]))
        start = j + 1
    return turns


def excess(turns):
    """How much more the first SPAN requests of a turn grow when it begins after a
    compaction than when it begins after another turn."""
    span = {
        a: [g[1:SPAN].sum() for b, g in turns if b == a and len(g) >= SPAN]
        for a in ("compaction", "turn")
    }
    return (
        float(np.mean(span["compaction"]) - np.mean(span["turn"]))
        if all(span.values())
        else 0.0
    )


def rereads(S):
    """How many more of the SPAN requests after a compaction read a file the context had read before
    (one the compaction dropped) than of the SPAN after another turn end (one the context still holds)."""
    n = {"compaction": [], "turn": []}
    for s in S:
        resets, start = (s.kind == USER) | (s.kind == POLICY), 0
        for j in range(1, len(s.reads) - SPAN + 1):
            after = "compaction" if resets[j] else "turn" if s.end[j - 1] else None
            if (
                after
                and not resets[j + 1 : j + SPAN].any()
                and (before := set().union(*s.reads[start:j]))
            ):
                n[after].append(sum(bool(r & before) for r in s.reads[j : j + SPAN]))
            start = j if resets[j] else start
    return (
        float(np.mean(n["compaction"]) - np.mean(n["turn"])) if all(n.values()) else 0.0
    )


# The steady-state model. Costs are in cached reads; see docs/cost-model.md.


def terms(p, c):
    """c(x) = h x + c0 and F(x) = x + F0, from what a request and a compaction write and read:
    a request reads x and writes the g new tokens, or after a lapse everything but the shared
    prefix, x - P; the requests after a compaction write what it leaves but the shared prefix,
    K - P. Returns h, c0 and F0."""
    h = 1 + p.lapse * (c.write - 1)
    c0 = (
        c.output * p.out
        + c.input * p.inp
        + (c.write - 1) * ((1 - p.lapse) * p.g - p.lapse * p.P)
    )
    F0 = c.output * p.N + (c.write - 1) * (p.K - p.P)
    return h, c0, F0


def eta(x, beta):
    """The effectiveness of a request with context x, (x / WINDOW)^β: 1 at the full window, and at any x
    where β is 0."""
    return (np.asarray(x, float) / WINDOW) ** beta


def cycle(p, c, X, beta, lost=0.0, asks=None):
    """A cycle that compacts at X, as integrals over its context from K to X, so each is g times the sum
    over its (X - K) / g requests: its cost, the requests' c(x) plus g (F(X) + D) for the compaction, and
    its effectiveness, the requests' η(x) with exponent β. `lost` is D, what the detail a compaction
    drops costs the requests after it, in cached reads. With `asks`, sparkjoy's turn-end floor, the cost
    also counts its questions: one at each turn end from the floor, a turn growing the context by its
    measured mean growth per turn, and the one that compacts at X short of AUTO."""
    h, c0, F0 = terms(p, c)
    K, X = p.K, np.asarray(X, float)
    cost = p.g * (X + F0 + lost) + h * (X**2 - K**2) / 2 + c0 * (X - K)
    if asks is not None:
        q0 = c.output * CHECK_OUTPUT
        a = np.minimum(asks, X)
        cost = (
            cost
            + p.g * ((X**2 - a**2) / 2 + q0 * (X - a)) / p.turn_growth
            + np.where(X < AUTO, p.g * (X + q0), 0)
        )
    # The integral of (x / WINDOW)^β from K to X, which is X - K at β = 0.
    effectiveness = (
        WINDOW * ((X / WINDOW) ** (beta + 1) - (K / WINDOW) ** (beta + 1)) / (beta + 1)
    )
    return cost, effectiveness


def per_unit(p, c, X, beta, lost=0.0, asks=None):
    """J(X), the long-run cost per unit of effectiveness of compacting at X; at β = 0, a request's mean cost
    over the cycle, its share of the compaction included, and with `asks`, sparkjoy's questions."""
    cost, effectiveness = cycle(p, c, X, beta, lost, asks)
    return cost / effectiveness


def curve(p, c, beta, lost=0.0, step=1_000, asks=None):
    """J on a grid of X from K, or with `asks`, from sparkjoy's floor, below which it can't compact, to
    AUTO, every `step` tokens."""
    start = p.K + step if asks is None else asks
    X = np.append(np.arange(start, AUTO, float(step)), AUTO)
    return X, per_unit(p, c, X, beta, lost, asks)


def steady(p, c, beta, lost=0.0):
    """The compaction threshold that minimises cost per unit of effectiveness, to 100 tokens, which at β = 0
    is the closed form's; AUTO means not before Claude Code."""
    X, J = curve(p, c, beta, lost, step=100)
    return float(X[J.argmin()])


def lost_for(p, c, X, beta):
    """D, in cached reads, that puts the steady-state most efficient threshold at X, or None where none
    does. At the most efficient threshold one more request's cost, c(X) + g, per unit of its effectiveness
    η(X), equals J(X), which solved for D gives this; it is negative where the threshold lies beyond X
    with no lost detail. Near β = 1 that condition marks J's maximum rather than its minimum, and no D
    puts the threshold at X."""
    h, c0, _ = terms(p, c)
    cost, effectiveness = cycle(p, c, X, beta)
    D = ((h * X + c0 + p.g) * effectiveness / eta(X, beta) - cost) / p.g
    return D if abs(steady(p, c, beta, D) - X) < 1_000 else None


def points(p, c):
    """The compaction thresholds the cycles chart draws, which counts cost alone: 175k, the most efficient threshold
    counting cost alone, rounded, 400k, and Claude Code's own."""
    return (175_000, round(closed_form(p, c), -4), 400_000, AUTO)


# The parameters measured in the sessions that the most efficient threshold depends on, with their symbols. The prices
# are list prices.
MEASURED = {"K": "K", "g": "g", "P": "P", "lapse": "ℓ", "N": "N"}


def sensitivity(p, c, boot, beta):
    """For each of MEASURED, the steady-state most efficient threshold with the parameter halved and doubled, and at
    each end of its 90% interval over `boot`, the sessions resampled, in order; then the same for β,
    under "β", with None for the interval, since β isn't measured."""
    rows = {}
    for field in MEASURED:
        v = getattr(p, field)
        ends = np.percentile([getattr(b, field) for b in boot], [5, 95])
        half, twice, *interval = [
            steady(replace(p, **{field: x}), c, beta) for x in (v / 2, v * 2, *ends)
        ]
        rows[field] = half, twice, sorted(interval)
    rows["β"] = steady(p, c, beta / 2), steady(p, c, 2 * beta), None
    return rows


def log_steady(p, c, beta, at=150_000):
    """The steady state with η = ln(x / x0) in place of the power law, x0 set so that a request at `at`
    gets as much done against one at the full window as under the power law with exponent `beta`:
    x0, the most efficient threshold, and J there against Claude Code's default."""
    target = (at / WINDOW) ** beta
    lo, hi = 1.0, float(at)
    for _ in range(
        100
    ):  # ln(at / x0) / ln(WINDOW / x0) falls from near 1 to 0 as x0 rises to `at`
        x0 = (lo + hi) / 2
        lo, hi = (
            (x0, hi) if np.log(at / x0) / np.log(WINDOW / x0) > target else (lo, x0)
        )
    X = np.append(np.arange(p.K + 100, AUTO, 100.0), AUTO)
    F = lambda x: x * np.log(x / x0) - x  # the integral of ln(x / x0)
    J = cycle(p, c, X, 0)[0] / (F(X) - F(p.K))
    return x0, float(X[J.argmin()]), J.min() / J[-1]


def closed_form(p, c):
    """X* where every request is equally effective (β = 0): K + sqrt(2 g (F0 + K) / h)."""
    h, _, F0 = terms(p, c)
    return p.K + np.sqrt(2 * p.g * (F0 + p.K) / h)


def idle_break_even(p, c):
    """The context above which compacting just before the cache lapses costs less than a
    cache write of the context, counting only the next request."""
    return (c.output * p.N + c.write * p.K) / (c.write - 1)


CHECK_OUTPUT = 100  # output tokens of a question


def question(size, c, read=None):
    """A question's cost in cached reads: its input, the context of `size` tokens, read from the cache, or
    where only `read` of them were, the rest priced as a cache write; and its output."""
    read = size if read is None else read
    return read + c.write * (size - read) + c.output * CHECK_OUTPUT


# The replay: every logged request stays, with its growth, output and timing,
# and only the compactions move.


def replay(s, p, c, threshold, ttl, every=False, idle=None, lost=0.0, asks=None):
    """One session's cost under a policy, with the context each request then carried and what
    each request cost, a compaction before it included.
    The policy compacts at the end of any turn (or with `every`, after any request) whose
    context reaches `threshold`, and with `idle`, at any turn end from that size after which
    the session sits idle for IDLE[ttl] seconds, as sparkjoy's timer does. Each
    compaction the policy or Claude Code makes also costs `lost`, D in cached reads. Each of
    `threshold`, `idle` and `lost` can instead hold one value per request: the threshold at a turn
    end after that request, the threshold if the session then sits idle, and what a compaction
    after it costs. With `asks`, sparkjoy's floors at a turn end and in an idle session (None for no idle
    floor), each request's cost also counts the `question` sparkjoy asks after it: at a turn end from
    the first floor, and once the session has sat idle, from the second, unless the turn end
    compacted. An idle question that keeps the context keeps its cache alive for `ttl` more."""
    K, P = p.K, p.P
    threshold, lost = (np.broadcast_to(v, len(s.size)) for v in (threshold, lost))
    idle = None if idle is None else np.broadcast_to(idle, len(s.size))
    lapse = lapsed(s, ttl)
    # Each compaction an arm makes leaves K, the E tokens read again included, all at once, so
    # the requests after a dropped one give up E of their growth, the earliest first, where the
    # re-reading falls.
    growth = s.grew.astype(float)
    for j in np.flatnonzero(s.kind == POLICY):
        left = p.E
        for i in range(j + 1, min(j + SPAN, len(growth))):
            take = min(growth[i], left)
            growth[i], left = growth[i] - take, left - take
    trace, paid = np.empty(len(s.size)), np.empty(len(s.size))
    cost, ctx, pending, ended = 0.0, 0.0, False, False
    for j in range(len(s.size)):
        before = cost
        kind, grew, inp, out = s.kind[j], growth[j], s.inp[j], s.out[j]
        if kind in (FIRST, USER):
            if kind == USER:
                cost += ctx + c.output * p.N
            ctx = s.size[j]
            cost += s.read[j] + c.write * s.write[j] + c.input * inp + c.output * out
        else:
            idled = s.idle[j] > IDLE[ttl]
            asked = (
                asks is not None
                and asks[1] is not None
                and idled
                and not pending
                and ctx >= asks[1]
            )
            if asked:
                cost += question(ctx, c)
            # An idle question that keeps the context refreshes the cache, which then lives `ttl`
            # from the question, IDLE[ttl] after the turn end. A rewrite the log shows within `ttl`
            # had another cause, which the question doesn't prevent.
            cold = lapse[j] and not (asked and ttl < s.gap[j] <= IDLE[ttl] + ttl)
            if pending or (idle is not None and ctx >= idle[j - 1] and idled):
                cost += (
                    ctx + c.output * p.N + lost[j - 1]
                )  # read from the cache, which the turn end left warm
                # A compaction leaves K, or less where the context held less to begin with,
                # which happens only below K, at a session's first turn ends.
                left = min(K, ctx)
                ctx, written = left + grew, left - P + grew
            else:
                ctx += grew
                written = ctx - P if cold else grew
            if ctx >= AUTO:
                # Claude Code compacts before sending, so after a lapse its summary reads uncached input.
                cost += (
                    (P + c.input * (ctx - P) if cold else ctx)
                    + c.output * p.N
                    + lost[j - 1]
                )
                ctx, written = K + grew, K - P + grew
            cost += (
                max(ctx - written - inp, 0)
                + c.write * written
                + c.input * inp
                + c.output * out
            )
        ended = bool(s.end[j])
        if asks is not None and ended and ctx + out >= asks[0]:
            cost += question(ctx + out, c)
        trace[j], paid[j] = ctx, cost - before
        pending = (every or ended) and ctx + out >= threshold[j]
    # A compaction after the session's last turn still runs its summary, and so does an idle
    # one, with its question: the policy can't know the session is over, and takes it to have sat idle.
    after = 0.0
    if (
        asks is not None
        and asks[1] is not None
        and ended
        and not pending
        and ctx >= asks[1]
    ):
        after += question(ctx, c)
    if pending or (idle is not None and ended and ctx >= idle[-1]):
        after += ctx + c.output * p.N
    cost += after
    paid[-1] += after
    return cost, trace, paid


def arm(S, p, c, ttl, threshold, betas=(BASE,), **kw):
    """Each session's cost under one policy, `replay`'s with these arguments, and its effectiveness, the sum
    of its requests' η, for each β in `betas`."""
    cost, effectiveness = np.empty(len(S)), np.empty((len(S), len(betas)))
    for i, s in enumerate(S):
        cost[i], trace, _ = replay(s, p, c, threshold, ttl, **kw)
        effectiveness[i] = [eta(trace, b).sum() for b in betas]
    return cost, effectiveness


def grid(S, p, c, ttl, betas=CHART_BETAS, **kw):
    """cost[i, t] and effectiveness[i, t, k] for each session i, threshold t and β in `betas`, plus
    Claude Code's default, compacting only near AUTO, as the last threshold."""
    arms = [arm(S, p, c, ttl, t, betas, **kw) for t in [*THRESHOLDS, np.inf]]
    return np.stack([a[0] for a in arms], 1), np.stack([a[1] for a in arms], 1)


def best(cost, effectiveness, rows=slice(None)):
    """Per β: the threshold with the least cost per unit of effectiveness (inf for leaving it to Claude Code's
    default near AUTO), and that cost against Claude Code's default."""
    J = cost[rows].sum(0)[:, None] / effectiveness[rows].sum(0)
    t = J.argmin(0)
    return np.append(THRESHOLDS, np.inf)[t], J[t, np.arange(J.shape[1])] / J[-1]


# The floors sparkjoy can ask from, at a turn end and in an idle session.
TURN_FLOORS = range(100_000, 400_001, 50_000)
IDLE_FLOORS = range(100_000, 250_001, 25_000)


def pairs(S, p, c, ttl, turns=TURN_FLOORS, idles=IDLE_FLOORS, betas=(BASE,)):
    """Each pair of floors' cost and effectiveness at each β in `betas`, summed over the sessions,
    compacting wherever they allow and paying the questions they ask, keyed (turn-end floor, idle floor);
    an idle floor of None compacts no idle session."""
    return {
        (t, i): tuple(
            v.sum(0) for v in arm(S, p, c, ttl, t, betas, idle=i, asks=(t, i))
        )
        for t in turns
        for i in idles
    }


def floors(S, p, c, ttl):
    """The pair of floors, at a turn end and in an idle session, that costs least in the replay,
    compacting wherever they allow, and every pair's cost. A floor is where sparkjoy starts asking,
    and the question decides above it, so it is fitted by cost alone, which puts it at the lowest best
    point any β gives. `/sparkjoy report` fits it to the user's sessions; sparkjoy's FLOOR and IDLE_FLOOR
    are the defaults it falls back on."""
    cost = {key: v[0] for key, v in pairs(S, p, c, ttl, betas=()).items()}
    return min(cost, key=cost.get), cost


# Where Claude Code compacts an idle session on its own: from 200k, just before a one-hour cache
# lapses, and only in a session someone is attending, not a background one (hooks/register.ts,
# IDLE_FLOOR's comment).
CC_IDLE = 200_000


def check(v, c):
    """A logged question's cost in cached reads, `question`'s at the context it was asked at, with the
    part a logged miss didn't read from the cache."""
    read, size = v.missed or (v.tokens, v.tokens)
    return question(size, c, read)


def as_run(s, p, opened=None, model=None):
    """The session's cost in dollars with sparkjoy's compactions where it made them, its questions
    included, and without them, at its model's list prices, or `model`'s (None for a model with no
    price here), and the context each request carried in each. Claude Code compacts on its own near
    the window in both, and on a one-hour cache in a session someone has open, also an idle context
    from CC_IDLE; `opened` says whether the session was open, and by default it is where it ran in
    the foreground."""
    model = model or s.model
    priced = model in PRICES
    c = prices(model if priced else "claude-opus-5-5", s.ttl)
    opened = not s.background if opened is None else opened
    idle = CC_IDLE if opened and s.ttl == 3600 else None
    # As compactions the user typed, sparkjoy's stay where the log has them.
    ran, ran_trace, _ = replay(
        replace(s, kind=np.where(s.sparkjoy, USER, s.kind)),
        p,
        c,
        np.inf,
        s.ttl,
        idle=idle,
    )
    without, without_trace, _ = replay(s, p, c, np.inf, s.ttl, idle=idle)
    checks = sum(check(v, c) for v in s.verdicts if v.tokens)
    dollars = (
        ((ran + checks) * c.read, without * c.read, checks * c.read) if priced else None
    )
    return dollars, ran_trace, without_trace


# The policies `moments` compares, as (turn-end threshold, any-request threshold), inf for none:
# after any request as `/autocompact` does, only at turn ends, and at turn ends with `/autocompact`
# set higher as a backstop for long turns. The backstop pairs take a coarser grid, as there are many.
MOMENTS = (
    [(t, t) for t in THRESHOLDS]
    + [(t, np.inf) for t in THRESHOLDS]
    + [(t, y) for t in THRESHOLDS[::5] for y in THRESHOLDS[::5] if y > t]
)


def moments(S, p, c, ttl, betas, policies=MOMENTS):
    """For each policy, keyed (turn-end threshold, any-request threshold): the cost summed over the
    sessions, the compactions made in the middle of a turn, Claude Code's own included, and the
    effectiveness at each β in `betas`, all also compacting idle sessions from IDLE_FLOOR. Cost grows by
    D with each mid-turn compaction, so a caller charges D, what a mid-turn compaction loses beyond one
    at a turn end, as cost + D * mid."""
    out = {}
    for turn, anywhere in policies:
        cost = mid = 0.0
        effectiveness = np.zeros(len(betas))
        for s in S:
            threshold = np.where(s.end, min(turn, anywhere), anywhere)
            plain, trace, _ = replay(
                s, p, c, threshold, ttl, every=True, idle=IDLE_FLOOR
            )
            charged, _, _ = replay(
                s,
                p,
                c,
                threshold,
                ttl,
                every=True,
                idle=IDLE_FLOOR,
                lost=(~s.end).astype(float),
            )
            cost, mid = cost + plain, mid + charged - plain
            effectiveness += [eta(trace, b).sum() for b in betas]
        out[turn, anywhere] = cost, mid, effectiveness
    return out


def kinds(S, rng, mix=MIX, stretch=STRETCH, right=RIGHT):
    """One draw of which kind of work, an index into `mix`, each session's turns do, and what
    sparkjoy's judgment says of them: per session, the kind of each request's turn, and at each
    request the kind the judgment names for the turn after it, which only a turn end uses. The turns
    come in stretches of one kind, each as long as a geometric draw with mean `stretch` and of a kind
    drawn by the mix's shares. The judgment names the next turn's kind rightly `right` of the time,
    and otherwise the other kind."""
    shares = [share for share, _ in mix]
    out = []
    for s in S:
        turn = np.concatenate([[0], np.cumsum(s.end)[:-1]])  # each request's turn
        starts = rng.random(turn[-1] + 2) < 1 / stretch  # up to the turn after the last
        starts[0] = True
        kind = rng.choice(len(mix), len(starts), p=shares)[np.cumsum(starts) - 1]
        ahead = kind[turn + 1]
        out.append(
            (kind[turn], np.where(rng.random(len(turn)) < right, ahead, 1 - ahead))
        )
    return out


def mixed(
    S,
    p,
    c,
    ttl,
    draws,
    threshold,
    mix=MIX,
    judged=False,
    every=False,
    idle=IDLE_FLOOR,
    claude_idle=None,
    asks=None,
):
    """Each kind of work's cost and effectiveness, summed over the sessions and `draws` of `kinds`,
    a request's cost going to its own kind, with the questions `asks` makes as `replay` counts them.
    With `judged`, a turn end compacts from threshold[k] where the judgment names kind k for the next
    turn; an idle session then compacts from `idle` where it names the kind with the lowest
    threshold, and otherwise from that kind's threshold, as sparkjoy's idle question keeps a context
    the work ahead needs, unless Claude Code's own idle compaction in a session someone has open, from
    `claude_idle`, compacts it first. Otherwise every turn end compacts from `threshold`, or with
    `every`, every request, as `/autocompact` does, and every idle session from `idle`."""
    betas = np.array([beta for _, beta in mix])
    cost, effectiveness = np.zeros(len(mix)), np.zeros(len(mix))
    for i, s in enumerate(S):
        if not judged:
            _, trace, paid = replay(s, p, c, threshold, ttl, every, idle, asks=asks)
        for draw in draws:
            kind, said = draw[i]
            if judged:
                at = np.asarray(threshold, float)
                idles = (
                    None if idle is None else np.where(at == at.min(), idle, at)[said]
                )
                if claude_idle is not None:
                    idles = np.minimum(np.inf if idles is None else idles, claude_idle)
                _, trace, paid = replay(s, p, c, at[said], ttl, idle=idles, asks=asks)
            cost += np.bincount(kind, paid, len(mix))
            effectiveness += np.bincount(kind, eta(trace, betas[kind]), len(mix))
    return cost, effectiveness


def shared(cost, effectiveness, mix=MIX):
    """Cost per unit of work, with each kind's share of the work fixed at its share in `mix`: the
    shares' sum of each kind's cost per unit of its effectiveness, as under "Work that differs in β"."""
    return sum(share * cost[k] / effectiveness[k] for k, (share, _) in enumerate(mix))


def judging(S, p, c, ttl, draws, start, mix=MIX, idle=IDLE_FLOOR):
    """The pair of turn-end thresholds, one per kind of work the judgment names, with the least cost
    per unit of work in `mixed`, found one threshold at a time from `start`, first every 50k and then
    on THRESHOLDS within 40k of each, from FLOOR, below which sparkjoy doesn't ask, and leaving it to
    Claude Code's default near AUTO; and that cost."""
    seen = {}

    def J(pair):
        if pair not in seen:
            seen[pair] = shared(
                *mixed(
                    S, p, c, ttl, draws, pair, mix, judged=True, idle=idle, asks=ASKS
                ),
                mix,
            )
        return seen[pair]

    def search(pair, options):
        while True:
            moved = pair
            for k in range(len(pair)):
                pair = min(((*pair[:k], x, *pair[k + 1 :]) for x in options), key=J)
            if pair == moved:
                return pair

    options = THRESHOLDS[THRESHOLDS >= FLOOR]
    pair = search(tuple(start), [*options[options % 50_000 == 0], np.inf])
    near = [x for x in options if min(abs(x - y) for y in pair) <= 40_000]
    pair = search(pair, [*near, np.inf])
    return pair, J(pair)


def saves(S, model, draws, judge_at):
    """What sparkjoy saves against Claude Code's default, as the cost model's Results for my sessions and the
    README's What it saves give it: for a five-minute cache, and a one-hour cache in a background session and
    in an open one, where Claude Code also compacts every idle context from CC_IDLE. Each is, compacting at
    every turn end from FLOOR and every idle session from IDLE_FLOOR, the saving for the same requests (β = 0)
    and for the same work at BASE; then on MIX, over `draws` of `kinds`, compacting so, and compacting where
    the judgment says, from the pair of thresholds `judge_at` holds for the cache lifetime, found by
    `judging` where it holds none; and that pair. Every saving counts the questions sparkjoy asks, from
    its floors."""
    out = {}
    for t, where, claude in (
        (300, "five-minute cache", None),
        (3600, "one-hour cache, background", None),
        (3600, "one-hour cache, open", CC_IDLE),
    ):
        q, cq = measure(S, t), prices(model, t)
        if t not in judge_at:
            judge_at[t], _ = judging(S, q, cq, t, draws, (400_000, 800_000))
        cf, ef = arm(S, q, cq, t, FLOOR, (0.0, BASE), idle=IDLE_FLOOR, asks=ASKS)
        cn, en = arm(S, q, cq, t, np.inf, (0.0, BASE), idle=claude)
        same, work = (
            1 - cf.sum() / ef[:, i].sum() / (cn.sum() / en[:, i].sum()) for i in (0, 1)
        )
        default = shared(*mixed(S, q, cq, t, draws, np.inf, idle=claude))
        at_floors = shared(
            *mixed(S, q, cq, t, draws, FLOOR, idle=IDLE_FLOOR, asks=ASKS)
        )
        judged = shared(
            *mixed(
                S,
                q,
                cq,
                t,
                draws,
                judge_at[t],
                judged=True,
                claude_idle=claude,
                asks=ASKS,
            )
        )
        out[where] = (
            same,
            work,
            1 - at_floors / default,
            1 - judged / default,
            judge_at[t],
        )
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    window_args(ap)
    ap.add_argument(
        "--model", default=MODEL, choices=PRICES, help="whose prices to fit"
    )
    ap.add_argument(
        "--ttl",
        choices=("5m", "1h"),
        help="cache lifetime to price (default: the one most sessions used)",
    )
    ap.add_argument(
        "--charts", type=Path, help="directory to draw the doc's charts into"
    )
    a = ap.parse_args()
    S, since, until = read(a)
    ttl = {"5m": 300, "1h": 3600}[a.ttl] if a.ttl else usual(s.ttl for s in S)
    c = prices(a.model, ttl)
    p = measure(S, ttl)
    rng = np.random.default_rng(0)
    draws = [rng.integers(0, len(S), len(S)) for _ in range(DRAWS)]
    boot = [measure([S[i] for i in d], ttl) for d in draws]

    print(
        f"{len(S)} sessions, {sum(len(s.size) for s in S):,} requests, {since:%Y-%m-%d %H:%M}Z to {until:%Y-%m-%d %H:%M}Z; "
        f"{a.model} prices, {ttl // 60}-minute cache: a write is {c.write:.0f} reads, an output token {c.output:.0f}"
    )
    print(
        "\nparameter                                                    value   90% interval"
    )
    for name, label, scale in (
        ("K", "context after a compaction (tokens)", 1),
        ("first_request", "  the first request after one", 1),
        ("P", "    of it read from the shared cache", 1),
        ("E", f"  extra tokens read again in the {SPAN} requests after one", 1),
        ("N", "output of the compaction request, thinking included", 1),
        ("rereads", f"extra requests of those {SPAN} re-reading a dropped file", 1),
        ("g", "growth per request", 1),
        ("out", "output per request", 1),
        ("lapse", "requests that rewrite the context (%)", 100),
        ("turn", "requests per turn, mean", 1),
        ("turn_median", "requests per turn, median", 1),
        ("turn_growth", "growth per turn", 1),
        ("idle_turns", "turn ends the cache doesn't outlive (%)", 100),
    ):
        v, (lo, hi) = (
            getattr(p, name) * scale,
            np.percentile([getattr(b, name) for b in boot], [5, 95]) * scale,
        )
        f = ",.0f" if abs(v) >= 100 else ".2f"
        print(f"  {label:54s} {v:9{f}}   {lo:{f}} to {hi:{f}}")
    h, c0, F0 = terms(p, c)
    _, text, ratio, summaries, near = summary_output(S)
    print(
        f"  compactions {p.compactions}, median {p.duration:.0f} s each; N is the summary's text, a median {text:,.0f} "
        f"of {summaries}, times {ratio:.2f},\n    the median output over visible output of {near:,} requests with "
        f"{text / 2:,.0f} to {1.5 * text:,.0f} visible"
    )
    size, grew = (
        np.concatenate([getattr(s, f)[s.kind == NORMAL] for s in S])
        for f in ("size", "grew")
    )
    print(
        "  growth per request by context:",
        ", ".join(
            f"{lo // 1000}-{lo // 1000 + 100}k {grew[(size >= lo) & (size < lo + 100_000)].mean():,.0f}"
            for lo in range(100_000, 800_000, 100_000)
        ),
    )
    k = lambda x: "never" if x == np.inf else f"{x / 1e3:.0f}k"
    span = lambda v: f"{k(min(v))}-{k(max(v))}" if min(v) != max(v) else k(v[0])
    pct = lambda v: f"{v:.1%}"
    ib = list(CHART_BETAS).index(BASE)
    BX = np.array([[steady(b, c, beta) for beta in CHART_BETAS] for b in boot])
    lo, hi = np.percentile([closed_form(b, c) for b in boot], [5, 95])
    print(
        f"\nh = {h:.3f}, c0 = {c0 / 1e3:,.0f}k, F0 = {F0 / 1e6:.2f}M (${F0 * c.read:.2f}); closed form at β = 0: "
        f"X* = {closed_form(p, c) / 1e3:.0f}k ({k(lo)}-{k(hi)}); idle break-even {idle_break_even(p, c) / 1e3:.0f}k"
    )

    X0 = steady(p, c, BASE)
    J0 = lambda x, d=0.0: (
        per_unit(p, c, x, BASE, d) / per_unit(p, c, AUTO, BASE, d)
    )  # J against Claude Code's default
    print(
        f"\nthe steady state at β = {BASE:g}, compacting at any request; a request at 250k gets "
        f"{eta(250_000, BASE):.0%} as much done as one at 1M, at 150k {eta(150_000, BASE):.0%}, at K {eta(p.K, BASE):.0%}"
    )
    print(
        f"  most efficient threshold X* {k(X0)} ({span(np.percentile(BX[:, ib], [5, 95]))}), saving {1 - J0(X0):.1%} against Claude "
        f"Code's default; within a point of that from {k(min(x for x in range(150_000, AUTO, 1_000) if J0(x) <= J0(X0) + 0.01))} "
        f"to {k(max(x for x in range(150_000, AUTO, 1_000) if J0(x) <= J0(X0) + 0.01))}"
    )
    print(
        f"  without lapses {k(steady(replace(p, lapse=0), c, BASE))}; with twice the summary "
        f"{k(steady(replace(p, N=2 * p.N), c, BASE))}; with K of "
        + " and ".join(
            f"{k(K)} {k(steady(replace(p, K=K), c, BASE))}" for K in (60_000, 70_000)
        )
        + ", as for a user with few instructions"
    )
    for field, (half, twice, interval) in sensitivity(p, c, boot, BASE).items():
        print(
            f"  with {MEASURED.get(field, field)} halved, then doubled: {k(half)} to {k(twice)}"
            + (f"; across its 90% interval {span(interval)}" if interval else "")
        )
    x0, Xl, Jl = log_steady(p, c, BASE)
    print(
        f"  with η = ln(x / {x0 / 1e3:.0f}k), a request at 150k as effective against 1M as at β = {BASE:g}: "
        f"{k(Xl)}, saving {1 - Jl:.1%}"
    )
    print(
        "  a cycle compacting at "
        + ", ".join(
            f"{k(x)} has {(x - p.K) / p.g:.0f} requests at ${per_unit(p, c, x, 0) * c.read:.3f} each on average, "
            f"J {J0(x):.1%} of Claude Code's default"
            for x in (175_000, closed_form(p, c), X0, 600_000, AUTO)
        )
    )
    for d in LOST:
        print(
            f"  with each compaction losing ${d:.2f} of detail: most efficient threshold {k(steady(p, c, BASE, d / c.read))}"
        )
    for x in (400_000, 700_000):
        d = lost_for(p, c, x, BASE)
        print(
            f"  D, the detail a compaction loses, that puts the most efficient threshold at {k(x)}: "
            + (
                "none"
                if d is None
                else f"${d * c.read:.2f}, effectiveness per dollar there "
                f"{1 / J0(x, d) - 1:+.1%} against Claude Code's default"
            )
        )
    for level in (300_000, 400_000, 600_000, 700_000, AUTO):
        beta = next(
            (b for b in np.arange(0, 5.001, 0.01) if steady(p, c, b) >= level), None
        )
        if beta is None:
            print(f"  the most efficient threshold reaches {k(level)} at no β below 5")
            continue
        X = steady(p, c, beta)
        print(
            f"  the most efficient threshold reaches {k(level)} at β = {beta:.2f} ({k(X)}): effectiveness after a compaction "
            f"{eta(p.K, beta):.0%} of 1M's, effectiveness per dollar {per_unit(p, c, AUTO, beta) / per_unit(p, c, X, beta) - 1:+.1%} "
            f"against Claude Code's default"
        )

    print("\nreplaying...", flush=True)
    cost, effectiveness = grid(S, p, c, ttl)
    cost_r, work_r = grid(S, p, c, ttl, betas=(BASE,), every=True)
    T, saved = best(cost, effectiveness)
    Tr, _ = best(cost_r, work_r)
    BT = np.array([best(cost, effectiveness, d)[0] for d in draws])
    at = {x: list(THRESHOLDS).index(x) for x in THRESHOLDS}
    J = cost.sum(0)[:, None] / effectiveness.sum(0)
    Jb = J[:, ib] / J[-1, ib]  # at the base case, against Claude Code's default
    low, high = np.percentile(BT[:, ib], [5, 95], method="nearest")
    print(f"\nthe replay at β = {BASE:g}")
    print(
        f"  compacting after any request, most efficient threshold {k(Tr[0])}, against the steady state's {k(X0)}"
    )
    print(
        f"  at turn ends, most efficient threshold {k(T[ib])} ({k(low)}-{k(high)}*), saving {1 - saved[ib]:.1%}; from 250k "
        f"{1 - Jb[at[250_000]]:.1%}; from 400k {1 - Jb[at[400_000]]:.1%}"
    )
    print("  * resampling sessions with K, P and N held at their fitted values")
    print(
        f"  thresholds within a point of the best: {span(THRESHOLDS[Jb[:-1] <= Jb[:-1].min() + 0.01])}"
    )
    for x in (400_000, 600_000):
        print(
            f"  compacting at {k(x)} costs more than the best, in the steady state "
            f"{per_unit(p, c, x, BASE) / per_unit(p, c, X0, BASE) - 1:.1%}, in the replay at turn ends "
            f"{Jb[at[x]] / Jb.min() - 1:.1%}"
        )
    print(
        "  most efficient turn-end threshold, by β: "
        + ", ".join(f"{b:g} {k(t)}" for b, t in zip(CHART_BETAS, T, strict=True))
    )
    gave = lambda x: (
        (J[at[x]] / J[-1] - saved) * 100
    )  # points of saving a turn-end threshold of x gives up, by β
    for x in (250_000, 400_000):
        print(
            f"  points {k(x)} gives up against the most efficient turn-end threshold, by β: "
            + ", ".join(
                f"{beta:g} {v:.1f}"
                for beta, v in zip(CHART_BETAS, gave(x), strict=True)
            )
        )
    print(
        "\n  context   pays back after   grows back in   (requests, counting cost alone)"
    )
    for x in (200_000, 250_000, 300_000, 400_000, 600_000):
        print(
            f"  {x / 1e3:5.0f}k   {np.ceil((x + F0) / (h * (x - p.K))):10.0f}   {(x - p.K) / p.g:14.0f}"
        )

    # Turn-end and idle floors chosen together at the base case, the turn-end floors running to
    # compacting only idle sessions; and the pair `floors` fits by cost alone.
    turns, idles = (*range(100_000, 600_001, 50_000), np.inf), (None, *IDLE_FLOORS)
    never = cost[:, -1].sum()
    Jn = never / effectiveness[:, -1, ib].sum()
    kept = {
        key: 1 - v[0] / v[1][0] / Jn
        for key, v in pairs(S, p, c, ttl, turns, idles).items()
    }  # saved
    # Claude Code's own idle compaction, which asks nothing, in a session someone has open.
    cc_cost, cc_work = arm(S, p, c, ttl, np.inf, (BASE,), idle=CC_IDLE)
    claude_open = 1 - cc_cost.sum() / cc_work.sum() / Jn
    top = max(kept, key=kept.get)
    short = {
        key: (kept[top] - v) * 100 for key, v in kept.items()
    }  # points of saving given up against the best
    near = [key for key in kept if short[key] <= 1]
    cheapest, _ = floors(S, p, c, ttl)
    print(
        f"\npolicy, compacting wherever it allows: saved at β = {BASE:g} (Claude Code's default, ${never * c.read:,.0f})"
    )
    for (t, i), label in (
        ((FLOOR, None), f"turn ends from {k(FLOOR)} (sparkjoy's FLOOR)"),
        (
            (FLOOR, IDLE_FLOOR),
            f"  and idle sessions from {k(IDLE_FLOOR)} (its IDLE_FLOOR)",
        ),
        ((400_000, None), "turn ends from 400k"),
        ((400_000, 200_000), "  and idle sessions from 200k"),
        ((200_000, None), "turn ends from 200k"),
    ):
        opened = (
            f"   open {pct(1 - (1 - kept[t, i]) / (1 - claude_open))}"
            if i is not None and i <= CC_IDLE
            else ""
        )
        print(f"  {label:58s} {pct(kept[t, i])}{opened}")
    print(
        f"  {'turn ends from the replay best alone, ' + k(T[ib]):58s} {pct(1 - saved[ib])}"
    )
    print(
        f"  {'the most efficient pair of floors, ' + k(top[0]) + ' and ' + (k(top[1]) if top[1] else '-'):58s} {pct(kept[top])}"
    )
    print(
        f"  points sparkjoy's floors give up against it: {short[FLOOR, IDLE_FLOOR]:.2f}"
    )
    print(
        f"  pairs within a point of it: turn-end floors {span([t for t, _ in near])}, idle floors "
        f"{span([i or np.inf for _, i in near])} ('never' for none)"
    )
    print(
        f"  the pair fitted by cost alone (`floors`, as the report fits): {k(cheapest[0])} and {k(cheapest[1])}"
    )
    print(
        f"  saved by pair at β = {BASE:g} (rows: turn-end floor; columns: idle floor, '-' for none):"
    )
    print("        " + " ".join(f"{x:>6}" for x in ("-", *map(k, IDLE_FLOORS))))
    for t in turns:
        print(f"  {k(t):>5} " + " ".join(f"{kept[t, j]:6.1%}" for j in idles))
    print(
        f"  {'idle sessions only, from ' + k(CC_IDLE) + ', as Claude Code compacts on its own':58s} {pct(claude_open)}"
    )
    # The first row of the table below: sparkjoy's compactions as they ran, against Claude Code alone, in
    # each session as it ran and as if every session were background or open.
    for opened, label in (
        (None, "each session as it ran"),
        (False, "as if every session were in the background"),
        (True, "as if every session were open"),
    ):
        runs = [d for s in S if (d := as_run(s, p, opened, a.model)[0])]
        ran, without, checks = (sum(d[i] for d in runs) for i in (0, 1, 2))
        print(
            f"sparkjoy as it ran, {label}: saved {1 - ran / without:.1%}; {len(runs)} sessions, "
            f"${ran:,.0f} with sparkjoy, its questions ${checks:,.0f} ({checks / ran:.2%}), ${without:,.0f} without"
        )

    # Mid-turn or at a turn end: each policy at its most efficient thresholds, with each mid-turn compaction
    # charged D wasted requests beyond what one at a turn end loses, each priced as a request at K.
    betas = (0.0, BASE)
    mo = moments(S, p, c, ttl, betas)
    wasted = h * p.K + c0
    unit = lambda key, i, D: (
        (mo[key][0] + D * wasted * mo[key][1]) / mo[key][2][i]
    )  # cost per unit of work
    nothing = [
        cost[:, -1].sum() / effectiveness[:, -1, list(CHART_BETAS).index(b)].sum()
        for b in betas
    ]
    places = {
        "after any request": lambda t, y: t == y,
        "only at turn ends": lambda t, y: y == np.inf,
        "at turn ends with a backstop": lambda t, y: t < y < np.inf,
    }
    lowest = lambda kind, i, D: min(
        (key for key in mo if places[kind](*key)), key=lambda key: unit(key, i, D)
    )
    print(
        f"\nafter any request, only at turn ends, or at turn ends with a backstop after any request, all compacting "
        f"idle sessions from {k(IDLE_FLOOR)}, each at its most efficient thresholds; a wasted request ${wasted * c.read:.3f}"
    )
    for i, b in enumerate(betas):
        for kind in places:
            turn, anywhere = key = lowest(kind, i, 0)
            print(
                f"  β = {b:g}, {kind}: turn ends from {k(turn)}, any request from "
                f"{'-' if anywhere == np.inf else k(anywhere)}, saving {1 - unit(key, i, 0) / nothing[i]:.1%}, "
                f"{mo[key][1]:.0f} mid-turn compactions"
            )
        for kind in ("only at turn ends", "at turn ends with a backstop"):
            even = next(
                (
                    D
                    for D in range(101)
                    if unit(lowest(kind, i, D), i, D)
                    <= unit(lowest("after any request", i, D), i, D)
                ),
                None,
            )
            print(
                f"    {kind} costs as little as after any request once a mid-turn compaction loses "
                + (
                    "over 100 wasted requests"
                    if even is None
                    else f"{even} wasted requests (${even * wasted * c.read:.2f})"
                )
            )
        print(
            f"    from sparkjoy's FLOOR, {k(FLOOR)}, by backstop: "
            + ", ".join(
                f"{k(y)} {1 - unit((FLOOR, y), i, 0) / nothing[i]:.1%} with {mo[FLOOR, y][1]:.0f} mid-turn"
                for t, y in mo
                if t == FLOOR and FLOOR < y < np.inf
            )
        )
        print(
            "    after any request, by threshold: "
            + ", ".join(
                f"{k(t)} {1 - unit((t, y), i, 0) / nothing[i]:.1%} with {mo[t, y][1]:.0f} mid-turn"
                for t, y in mo
                if t == y and t % 50_000 == 0 and t >= 200_000
            )
        )
        for D in (2, 6):
            turn, anywhere = key = lowest("at turn ends with a backstop", i, D)
            other = lowest("after any request", i, D)
            print(
                f"    losing {D}: with a backstop, turn ends from {k(turn)} and any request from {k(anywhere)}, saving "
                f"{1 - unit(key, i, D) / nothing[i]:.1%} with {mo[key][1]:.0f} mid-turn compactions; after any request "
                f"from {k(other[0])}, {1 - unit(other, i, D) / nothing[i]:.1%}"
            )

    # Work that differs: the base case's mix of work, the judgment naming each turn end's next kind. Every
    # saving is against Claude Code's default, which compacts near the window, and in a session someone has
    # open on the one-hour cache, also every idle context from CC_IDLE, the judgment's included. The judgment
    # doesn't know which kind of session it is in, so it compacts from the same pair of thresholds in both.
    print(
        f"\nwhere work differs: {', '.join(f'{share:.0%} of turns at β = {b:g}' for share, b in MIX)}, in stretches "
        f"of {STRETCH} turns on average, {MIX_DRAWS} draws; each kind's cost per unit of its own work, weighted by its "
        f"share; every policy of sparkjoy's pays the questions it asks from {k(FLOOR)}; saved against Claude Code's default"
    )
    # The steady state on the same mix, each kind running whole cycles, as under "Work that differs in β",
    # where Claude Code's default compacts at AUTO, the last point of `curve`.
    X = curve(p, c, 0)[0]
    Js = np.array([curve(p, c, b)[1] for _, b in MIX])
    Jq = np.array([curve(p, c, b, asks=FLOOR)[1] for _, b in MIX])  # with its questions
    w = np.array([share for share, _ in MIX])
    single = (w[:, None] * Js).sum(0)

    def judged(a):  # J with the judgment right a of the time in each kind
        keep = np.array([1 - a, a]) * w
        return (keep[:, None] * Jq).sum(0).min() + ((w - keep)[:, None] * Jq).sum(
            0
        ).min()

    at_floor = sum(share * per_unit(p, c, FLOOR, b, asks=FLOOR) for share, b in MIX)
    print(
        f"  the steady state: compacting at {k(FLOOR)} saves {1 - at_floor / single[-1]:.1%}; at the most efficient "
        f"single threshold, {k(X[single.argmin()])}, {1 - single.min() / single[-1]:.1%}; the judgment "
        f"{1 - judged(RIGHT) / single[-1]:.1%} right {RIGHT:.0%} of the time, {1 - judged(1) / single[-1]:.1%} right "
        f"every time, and saves more than that single threshold from "
        f"{next(a for a in np.arange(0.5, 1.0001, 0.005) if judged(a) < single.min()):.1%} right"
    )
    judge_at = {}  # the judgment's thresholds by cache lifetime, right RIGHT of the time
    for label, stretch, right in (
        (f"right {RIGHT:.0%} of the time", STRETCH, RIGHT),
        ("right every time", STRETCH, 1.0),
        ("stretches of 20 turns", 20, RIGHT),
    ):
        rng = np.random.default_rng(0)
        draws = [kinds(S, rng, MIX, stretch, right) for _ in range(MIX_DRAWS)]
        pair, _ = judging(S, p, c, ttl, draws, (400_000, 800_000))
        judge_at.setdefault(ttl, pair)
        at_floors = shared(
            *mixed(S, p, c, ttl, draws, FLOOR, idle=IDLE_FLOOR, asks=ASKS)
        )
        saved = {}
        for where, claude in (("background", None), ("open", CC_IDLE)):
            default = shared(*mixed(S, p, c, ttl, draws, np.inf, idle=claude))
            Jj = shared(
                *mixed(
                    S,
                    p,
                    c,
                    ttl,
                    draws,
                    pair,
                    judged=True,
                    claude_idle=claude,
                    asks=ASKS,
                )
            )
            saved[where] = 1 - at_floors / default, 1 - Jj / default
        print(
            f"  the replay, {label}: the judgment, compacting from {k(pair[0])} or {k(pair[1])}, saves "
            f"{saved['background'][1]:.1%} in a background session and {saved['open'][1]:.1%} in an open one; "
            f"compacting at every turn end from sparkjoy's floors, {saved['background'][0]:.1%} and {saved['open'][0]:.1%}"
        )

    # The table the cost model's Results for my sessions gives and the README's What it saves quotes.
    rng = np.random.default_rng(0)
    draws = [kinds(S, rng) for _ in range(MIX_DRAWS)]
    print(
        f"\nsaved against Claude Code's default, compacting from sparkjoy's floors, {k(FLOOR)} at a turn end and "
        f"{k(IDLE_FLOOR)} idle, its questions included; where work differs, at every turn end from them, then with "
        f"the judgment right {RIGHT:.0%} of the time"
    )
    for where, (same, work, differs, judged, pair) in saves(
        S, a.model, draws, judge_at
    ).items():
        print(
            f"  {where:27s} same requests {same:.1%}; same work at β = {BASE:g} {work:.1%}; where work differs "
            f"{differs:.1%}, with the judgment {judged:.1%} (from {k(pair[0])} or {k(pair[1])})"
        )
    gaps = np.concatenate(
        [np.append(s.gap[1:], 0)[s.end] for s in S]
    )  # from each turn end to the next request
    measured = {t: measure(S, t) for t in (3600, 300)}
    for model in (
        "claude-opus-5-5",
        "claude-sonnet-5-5",
        "claude-fable-5-2",
        "claude-haiku-5-5",
    ):
        for t in (3600, 300):
            q, cq = measured[t], prices(model, t)
            gc, ge = grid(S, q, cq, t, betas=(BASE,))
            (tt,), (sv,) = best(gc, ge)
            never_q = gc[:, -1].sum() / ge[:, -1, 0].sum()
            floor_q = {
                i: v[0] / v[1][0] / never_q
                for (_, i), v in pairs(
                    S, q, cq, t, (FLOOR,), (None, IDLE_FLOOR)
                ).items()
            }
            print(
                f"\n{model} prices, {t // 60}-minute cache, at β = {BASE:g}: lapses {q.lapse:.2%} "
                f"({q.lapse_turn_end:.0%} at turn ends); h {terms(q, cq)[0]:.2f}; F0 ${terms(q, cq)[2] * cq.read:.2f}; "
                f"idle break-even {k(idle_break_even(q, cq))}; the cache lapses after {q.idle_turns:.0%} of turn ends, "
                f"and {np.mean(gaps[gaps > IDLE[t]] > t):.0%} of pauses past {IDLE[t] // 60} minutes outlive it"
            )
            print(
                f"  steady X* {k(steady(q, cq, BASE))}; replay at turn ends, most efficient threshold {k(tt)}, saving "
                f"{1 - sv:.1%}; from {k(FLOOR)} at turn ends {1 - floor_q[None]:.1%}, and idle sessions from "
                f"{k(IDLE_FLOOR)} {1 - floor_q[IDLE_FLOOR]:.1%}"
            )

    if a.charts:
        import charts

        charts.draw(a.charts, S, p, c, boot, cost, effectiveness, BT, BX)


if __name__ == "__main__":
    main()
