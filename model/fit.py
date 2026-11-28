"""The compaction model of docs/cost-model.md, fitted to Claude Code sessions: reading the
transcripts, measuring the model's parameters, and replaying the sessions under other policies.
docs.py fits it to my sessions for the docs, and report.py to the user's.

It reads the main thread of each interactive session (`claude -p` runs are left out). Only
transcripts modified since the window's start are opened, and `read` lowers the process's
priority, since a full scan of ~/.claude/projects is heavy.
"""

import hashlib
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
# that tier's: the context after a compaction is above 100k, so nearly every request is.
PRICES = {
    "claude-opus-5-5": (4, 20, 5, 8, 0.20),
    "claude-sonnet-5-5": (2, 10, 2.5, 4, 0.10),
    "claude-fable-5-2": (8, 40, 10, 16, 0.20),
    "claude-fable-5-1": (10, 50, 12.5, 20, 0.25),
    "claude-opus-5": (5, 25, 6.25, 10, 0.50),
    "claude-haiku-5-5": (0.5, 2.5, 0.625, 1, 0.05),  # its long-context tier, above 100k
}
CONFIG = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
PROJECTS = CONFIG / "projects"
# sparkjoy's data folder, where hooks/register.ts keeps its log and /sparkjoy report saves the figures
# it reads: $SPARKJOY_DATA, which sparkjoy's skills set from the ${CLAUDE_PLUGIN_DATA} Claude Code
# substitutes in their text, or else the folder Claude Code gives sparkjoy installed from its own
# marketplace. Not $CLAUDE_PLUGIN_DATA itself: a session's shell can hold another plugin's.
DATA = Path(
    os.environ.get("SPARKJOY_DATA") or CONFIG / "plugins" / "data" / "sparkjoy-sparkjoy"
)
WINDOW = 1_000_000
AUTO = 965_000  # where Claude Code compacts a 1M window on its own
SPAN = 20  # requests over which re-reads after a compaction are counted
# What the first request after a compaction grew by, at most, as though the session hadn't compacted:
# its growth can't be told from the compacted prompt, so a new message's worth stands in for it.
NEW_MESSAGE = 4_000
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
# The line sparkjoy writes in the transcript for each verdict (`judge` in hooks/register.ts),
# under its current name and the one it had before it was published. A question that missed the
# prompt cache (MISSED there) says how much of its input it read from it, then may say more.
VERDICT = re.compile(
    r"(?P<plugin>sparkjoy|compact-when-done): (?:(?P<kind>Compacting|Would compact|Kept|Unreadable verdict|"
    r"No verdict|Dropped the verdict) at (?P<tokens>\d+)k tokens(?:, idle (?P<idle>\d+) minutes)?"
    r"(?:, after missing the prompt cache[^(]* \(read (?P<read>\d+)k of (?P<input>\d+)k(?:;[^)]*)?\))?"
    r"|Failed): (?P<reason>.*)",
    re.S,
)
# Every line sparkjoy writes in the transcript, under either name, and those Claude Code writes
# about it there.
LINE = ("sparkjoy: ", "compact-when-done: ")
# The line sparkjoy writes as it starts a compaction: its verdict to compact, or, after Claude Code
# restarted before one landed, that it is finishing it (`resume` in hooks/register.ts).
COMPACTING = re.compile(
    r"(?:sparkjoy|compact-when-done): (?:Compacting|Finishing the compaction) at "
)
# The row a typed `/compact` draws. Until 8 November 2026 sparkjoy compacted by running `/compact`,
# which drew one too, beside its line.
TYPED = "<command-name>/compact</command-name>"
# How long before a compaction starts each mark of its origin may have been logged, in seconds; a
# mark logged after it ended is another compaction's. A mark is placed by when it was logged rather
# than where: Claude Code can log the end of the turn a compaction follows, the mark among it, after
# the compaction's boundary. Of sparkjoy's 1,352 compactions in my sessions from 16 October to 27
# November 2026, all but 10 began within a second of its line and the rest within 60 s; 411 of the
# 421 typed `/compact` rows lie within a second of the start and the rest within 10 s; Claude
# Code's line about an idle compaction follows its boundary within 10 s.
BEFORE = {"sparkjoy": 60, "typed": 10}
IDLE_LINE = 10
KINDS = {
    "Compacting": "compacted",
    "Would compact": "would compact",
    "Kept": "kept",
    "Unreadable verdict": "unreadable",
    "No verdict": "no verdict",
    "Dropped the verdict": "dropped",
    "Failed": "failed",
}
# sparkjoy's log in its data folder (LOG in hooks/register.ts): a JSON line for each
# verdict, its `kind` the line's word for it, and for each compaction it starts and ends, in a file
# a session and a day. Where a session has one, it is what `transcript` reads sparkjoy's verdicts
# and compactions from; the lines and marks above stand for it in sessions from before it, and a
# line for a verdict whose record was never written.
LOG = DATA / "log"

# Where a session's name comes from, in order of preference: one the user gave it, then
# the ones Claude Code generates. Failing all three, its first prompt names it.
TITLES = {
    "custom-title": "customTitle",
    "ai-title": "aiTitle",
    "agent-name": "agentName",
}

# Request kinds. A FIRST request opens the session; a USER request is the first
# after a compaction no replayed arm decides, which every arm keeps: one the user
# typed, or one another tool made; a POLICY request is the first after one that
# sparkjoy or Claude Code's auto-compaction made, which each arm decides for
# itself; NORMAL is every other request.
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
    done: np.ndarray  # when the last block of each request's reply was logged
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
    compactions: list  # (seconds since the epoch, its origin (`origin`), tokens before)
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


# Every figure the README and docs/cost-model.md quote, by section: docs.py writes all but the
# section calibrate.py writes.
DOC_FIGURES = Path(__file__).parents[1] / "docs" / "figures.json"


def jsonable(v):
    """`v` as JSON holds it: NumPy values as Python's, and an infinite threshold, one never reached,
    as null."""
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, np.ndarray)):
        return [jsonable(x) for x in v]
    if v is None or isinstance(v, (bool, str)):
        return v
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, (int, np.integer)):
        return int(v)
    v = float(v)
    return None if np.isinf(v) else v


def save_figures(sections):
    """Write `sections`, as {name: figures}, into DOC_FIGURES, replacing those sections and keeping
    the others. A list of numbers, such as an interval, takes one line."""
    data = json.loads(DOC_FIGURES.read_text()) if DOC_FIGURES.exists() else {}
    data.update(jsonable(sections))
    text = json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False)
    text = re.sub(
        r"\[\n\s+([-\d.e+,\snul]+?)\n\s*\]",
        lambda m: "[" + ", ".join(v.strip() for v in m[1].split(",")) + "]",
        text,
    )
    DOC_FIGURES.write_text(text + "\n")


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


def digest(message):
    """A message's hash, as Claude Code's request records name it: sha256 of its JSON, keys sorted
    and compact."""
    return hashlib.sha256(
        json.dumps(
            message, separators=(",", ":"), sort_keys=True, ensure_ascii=False
        ).encode()
    ).hexdigest()


def request_messages(lines):
    """The requests a transcript records, by id, and a function giving a request's messages in
    order, each None where neither a blob nor the transcript's lines hold it.

    An `api-request` record lists its messages as its base's first `keep` and then the hashes of its
    `tail`. An `api-request-blob` holds a message by its hash; the rest are rebuilt from the user and
    assistant lines, whose hash names them: an assistant message from its lines merged by message
    id, a user message from one line or a run of consecutive ones, as for the results of parallel
    tool calls."""
    blobs, rebuilt, requests = {}, {}, {}
    run, by_id = [], {}

    def add(message):
        rebuilt[digest(message)] = message

    for e in lines:
        kind = e.get("type")
        if kind == "api-request-blob":
            blobs[e["hash"]] = e["message"]
        elif kind == "api-request":
            requests[e["id"]] = e
        elif (
            kind in ("user", "assistant")
            and isinstance(e.get("message"), dict)
            and not e.get("isSidechain")
        ):
            m = e["message"]
            content = m.get("content")
            add({"role": m.get("role"), "content": content})
            blocks = (
                [{"type": "text", "text": content}]
                if isinstance(content, str)
                else list(content or [])
            )
            if m.get("role") == "user":
                run.append(blocks)
                for start in range(len(run)):
                    add(
                        {"role": "user", "content": [b for c in run[start:] for b in c]}
                    )
            else:
                run = []
                merged = by_id.setdefault(m.get("id"), [])
                merged.extend(blocks)
                add({"role": "assistant", "content": list(merged)})
    memo = {}

    def messages(rid):
        # Down the chain of bases without recursion, since it runs hundreds of requests deep.
        chain, r = [], rid
        while r in requests and r not in memo:
            chain.append(r)
            r = requests[r].get("base")
        for r in reversed(chain):
            record = requests[r]
            base = record.get("base")
            head = memo[base][: record.get("keep", 0)] if base in memo else []
            memo[r] = head + [blobs.get(h) or rebuilt.get(h) for h in record["tail"]]
        return memo[rid]

    return requests, messages


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
    reqs, prompts, verdicts, lines = {}, [], [], []
    entry, version, background = None, None, False
    # When each mark of a compaction's origin was logged, and each compaction's boundary.
    marks, bounds = {"sparkjoy": [], "typed": [], "idle": []}, []
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
            content = (e.get("message") or {}).get("content")
            if isinstance(content, str) and content.lstrip().startswith(TYPED):
                marks["typed"].append(t)
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
            if COMPACTING.match(text):
                marks["sparkjoy"].append(t)
            elif text.startswith("Compacted while idle"):
                # Claude Code's idle compaction logs as an auto compaction, then says so.
                marks["idle"].append(t)
            if line := VERDICT.fullmatch(text):
                kind = KINDS[line["kind"] or "Failed"]
                verdicts.append(
                    Verdict(
                        t,
                        line["plugin"],
                        kind,
                        line["tokens"] and int(line["tokens"]) * 1000,
                        line["idle"] and int(line["idle"]),
                        line["reason"],
                        line["read"]
                        and (int(line["read"]) * 1000, int(line["input"]) * 1000),
                    )
                )
        elif e.get("subtype") == "compact_boundary":
            md = e.get("compactMetadata") or {}
            bounds.append(
                (
                    t,
                    (md.get("durationMs") or 0) / 1000,
                    md.get("trigger"),
                    md.get("preTokens"),
                )
            )
    if entry != "cli" or len(reqs) < 2:
        return None
    name = (
        next((titles[k] for k in TITLES if titles.get(k)), None) or first or "untitled"
    )
    records = logged(path, lo, hi)
    if records:
        # A verdict's record stands for its line, timed within a second of it. A line whose record
        # was never written, as when the process exited with the write still queued, still counts.
        recorded = [logged_verdict(r) for r in records if r["event"] == "verdict"]
        verdicts = [
            v
            for v in verdicts
            if not any(r.kind == v.kind and abs(r.t - v.t) <= 1 for r in recorded)
        ] + recorded
        verdicts.sort(key=lambda v: v.t)
    comps = [
        (t, took, origin(t - took, t, trigger, marks, spans(records)), before)
        for t, took, trigger, before in bounds
    ]
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


def logged(path, lo="0", hi="~"):
    """The records sparkjoy logged in [lo, hi), ISO times, for the session whose transcript is at
    `path`, oldest first."""
    found = []
    for f in (LOG / path.stem).glob("*.jsonl"):
        for raw in f.open(errors="replace"):
            try:
                r = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if lo <= r.get("t", "") < hi:
                found.append(r)
    return sorted(found, key=lambda r: r["t"])


def epoch(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def logged_verdict(r):
    """A verdict from its record in sparkjoy's log."""
    u = r.get("usage")
    read = u and u["cache_read_input_tokens"]
    return Verdict(
        epoch(r["t"]),
        "sparkjoy",
        KINDS[r["kind"]],
        r.get("tokens"),
        r.get("idle_minutes"),
        r["reason"],
        (read, u["input_tokens"] + read + u["cache_creation_input_tokens"])
        if r.get("missed")
        else None,
    )


def spans(records):
    """Each compaction sparkjoy logged, from its start to its end, in seconds since the epoch. A
    start whose process ended before its end leaves none; the next process logs its own."""
    made, start = [], None
    for r in records:
        if r["event"] != "compaction":
            continue
        if r["phase"] == "start":
            start = epoch(r["t"])
        elif start is not None:
            made.append((start, epoch(r["t"])))
            start = None
    return made


# The origins of the compactions each replayed arm decides for itself (POLICY); the rest, "user"
# and "other", every arm keeps (USER).
DECIDED = ("sparkjoy", "auto", "idle")


def origin(start, end, trigger, marks, made=()):
    """Who made a compaction that ran from `start` to `end`: "sparkjoy", where it lies within
    one sparkjoy logged (`made`, each from its start to its end) or its line marks it; "user", by a
    typed `/compact` row; Claude Code's own, "idle" where it said so and "auto" otherwise; or
    "other", another tool, such as a mod under test, where nothing marks it. `marks` holds the times
    the transcript logged each kind of mark."""

    def near(kind):
        return any(start - BEFORE[kind] <= m <= end for m in marks[kind])

    if any(a - 1 <= start and end <= b + 1 for a, b in made) or near("sparkjoy"):
        return "sparkjoy"
    if near("typed"):
        return "user"
    if trigger == "auto":
        idle = any(end - 1 <= m <= end + IDLE_LINE for m in marks["idle"])
        return "idle" if idle else "auto"
    return "other"


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
    for ct, _, by, _ in comps:
        j = np.searchsorted(t, ct)  # the first request after the compaction
        if 0 < j < n:
            kind[j] = POLICY if by in DECIDED else USER
            sparkjoy[j] = by == "sparkjoy"
    grew = np.maximum(np.diff(size, prepend=0), 0)
    grew[kind != NORMAL] = 0
    grew[kind == POLICY] = np.minimum(write[kind == POLICY], NEW_MESSAGE)
    # The requests after a compaction, whose growth includes what it made them read again.
    recent = np.zeros(n, bool)
    for j in np.flatnonzero((kind == USER) | (kind == POLICY)):
        recent[j + 1 : j + SPAN] = True
    return Session(
        **meta,
        t=t,
        done=np.array([q[4] for q in reqs]),
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
        durations=[took for _, took, _, _ in comps if took],
        reads=[q[3] for q in reqs],
        calls=[list(q[5].values()) for q in reqs],
        failed=[[failed[i] for i in q[5] if i in failed] for q in reqs],
        compactions=[(ct, by, before) for ct, _, by, before in comps],
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
    prefix, x - P; the requests after a compaction write the context after it but the shared
    prefix, K - P. Returns h, c0 and F0."""
    h = 1 + p.lapse * (c.write - 1)
    c0 = (
        c.output * p.out
        + c.input * p.inp
        + (c.write - 1) * ((1 - p.lapse) * p.g - p.lapse * p.P)
    )
    F0 = c.output * p.N + (c.write - 1) * (p.K - p.P)
    return h, c0, F0


def redo(p, c):
    """ρ, what redoing one token of the work a compaction lost costs, in cached reads: a request just
    after a compaction costs c(K) and adds g tokens, so ρ = c(K) / g. A compaction at context x that
    loses u, the share of x that is unfinished state its summary can't carry, then costs ρ u x more."""
    h, c0, _ = terms(p, c)
    return (h * p.K + c0) / p.g


def eta(x, beta):
    """The effectiveness of a request with context x, (x / WINDOW)^β: 1 at the full window, and at any x
    where β is 0."""
    return (np.asarray(x, float) / WINDOW) ** beta


def cycle(p, c, X, beta, u=0.0, asks=None):
    """A cycle that compacts at X, as integrals over its context from K to X, so each is g times the sum
    over its (X - K) / g requests: its cost, the requests' c(x) plus g (F(X) + ρ u X) for the compaction,
    and its effectiveness, the requests' η(x) with exponent β. `u` is the share of the context a
    compaction loses (`redo`). With `asks`, sparkjoy's turn-end floor, the cost also counts its
    questions: one at each turn end from the floor, a turn growing the context by its measured mean
    growth per turn, and the one that compacts at X short of AUTO."""
    h, c0, F0 = terms(p, c)
    K, X = p.K, np.asarray(X, float)
    cost = p.g * (X + F0 + redo(p, c) * u * X) + h * (X**2 - K**2) / 2 + c0 * (X - K)
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


def per_unit(p, c, X, beta, u=0.0, asks=None):
    """J(X), the long-run cost per unit of effectiveness of compacting at X; at β = 0, a request's mean cost
    over the cycle, its share of the compaction included, and with `asks`, sparkjoy's questions."""
    cost, effectiveness = cycle(p, c, X, beta, u, asks)
    return cost / effectiveness


def curve(p, c, beta, u=0.0, step=1_000, asks=None):
    """J on a grid of X from K, or with `asks`, from sparkjoy's floor, below which it can't compact, to
    AUTO, every `step` tokens."""
    start = p.K + step if asks is None else asks
    X = np.append(np.arange(start, AUTO, float(step)), AUTO)
    return X, per_unit(p, c, X, beta, u, asks)


def steady(p, c, beta, u=0.0):
    """The compaction threshold that minimises cost per unit of effectiveness, to 100 tokens, which at β = 0
    is the closed form's; AUTO means not before Claude Code."""
    X, J = curve(p, c, beta, u, step=100)
    return float(X[J.argmin()])


def u_for(p, c, X, beta):
    """The u that puts the steady-state most efficient threshold at X, or None where none does. At the
    most efficient threshold the cost of compacting one request later, c(X) + g (1 + ρ u), per unit of
    that request's effectiveness η(X), equals J(X), which solved for u gives this; it is negative where
    the threshold lies beyond X when a compaction loses nothing. Near β = 1 that condition marks J's
    maximum rather than its minimum, and no u puts the threshold at X."""
    h, c0, _ = terms(p, c)
    cost, effectiveness = cycle(p, c, X, beta)
    n = eta(X, beta)
    u = (cost * n - (h * X + c0 + p.g) * effectiveness) / (
        p.g * redo(p, c) * (effectiveness - X * n)
    )
    return u if abs(steady(p, c, beta, u) - X) < 1_000 else None


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


# The decision at a turn end, given the share of the context the work ahead needs.


def to_reset(S, low=200_000):
    """Each turn end at a context of `low` or more, as arrays of its context, the requests until its
    session's next reset (the next compaction, or the session's end), and the session's index. A turn
    end a compaction ended at once is left out: how long its session would have run on had it kept is
    unobserved, not zero."""
    x, n, k = [], [], []
    for i, s in enumerate(S):
        resets = np.flatnonzero((s.kind == USER) | (s.kind == POLICY))
        for j in np.flatnonzero(s.end & (s.size >= low)):
            later = resets[resets > j]
            left = (later[0] if len(later) else len(s.size)) - (j + 1)
            if len(later) and left == 0:
                continue
            x.append(s.size[j])
            n.append(left)
            k.append(i)
    return np.array(x, float), np.array(n, float), np.array(k)


def share_threshold(p, c, x, n):
    """s*(x), the share of a context x that the work ahead needs under which compacting at a turn end
    costs less over the n requests to the session's next reset, when the compaction loses nothing
    beyond what is read back. Keeping, they read x each, n h x. Compacting costs F(x) = x + F0, and
    they read K and what the work reads back, the share s of x, written once:
    x + F0 + n h K + s x (n h + w - 1). With one n, s*(x) = A (1 - FLOOR / x), with
    A = (n h - 1) / (n h + w - 1) and FLOOR = (n h K + F0) / (n h - 1). A compaction that also loses u
    (`redo`) costs ρ u x more, so it pays when s + λ u < s*(x) (`weight`)."""
    h, _, F0 = terms(p, c)
    return (n * h * (x - p.K) - x - F0) / (x * (n * h + c.write - 1))


def weight(p, c, n):
    """λ = ρ / (n h + w - 1), what a share u lost weighs against the share s read back at a turn end
    with n requests to the session's next reset: ρ u x against s x (n h + w - 1)."""
    h, _, _ = terms(p, c)
    return redo(p, c) / (n * h + c.write - 1)


CHECK_OUTPUT = 100  # output tokens of a question


def question(size, c, read=None):
    """A question's cost in cached reads: its input, the context of `size` tokens, read from the cache, or
    where only `read` of them were, the rest priced as a cache write; and its output."""
    read = size if read is None else read
    return read + c.write * (size - read) + c.output * CHECK_OUTPUT


# The replay: every logged request stays, with its growth, output and timing,
# and only the compactions move.


def replay(s, p, c, threshold, ttl, every=False, idle=None, asks=None):
    """One session's cost under a policy, with the context each request then carried, what each
    request cost, a compaction before it included, and the context each compaction the policy or
    Claude Code made before a request compacted (0 where none did), from which a caller prices
    what the compactions lost (`redo`).
    The policy compacts at the end of any turn (or with `every`, after any request) whose
    context reaches `threshold`, and with `idle`, at any turn end from that size after which
    the session sits idle for IDLE[ttl] seconds, as sparkjoy's timer does. Each of `threshold`
    and `idle` can instead hold one value per request: the threshold at a turn end after that
    request, and the threshold if the session then sits idle. With `asks`, sparkjoy's floors at a
    turn end and in an idle session (None for no idle floor), each request's cost also counts the
    `question` sparkjoy asks after it: at a turn end from the first floor, and once the session has
    sat idle, from the second, unless the turn end compacted. An idle question that keeps the
    context keeps its cache alive for `ttl` more."""
    K, P = p.K, p.P
    threshold = np.broadcast_to(threshold, len(s.size))
    idle = None if idle is None else np.broadcast_to(idle, len(s.size))
    lapse = lapsed(s, ttl)
    # Each compaction an arm makes brings the context to K, the E tokens read again included, all
    # at once, so
    # the requests after a dropped one give up E of their growth, the earliest first, where the
    # re-reading falls.
    growth = s.grew.astype(float)
    for j in np.flatnonzero(s.kind == POLICY):
        left = p.E
        for i in range(j + 1, min(j + SPAN, len(growth))):
            take = min(growth[i], left)
            growth[i], left = growth[i] - take, left - take
    trace, paid, compacted = (
        np.empty(len(s.size)),
        np.empty(len(s.size)),
        np.zeros(len(s.size)),
    )
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
                # The compaction reads the context from the cache, which the turn end left warm.
                cost += ctx + c.output * p.N
                compacted[j] += ctx
                # After a compaction the context is K, or less where it held less to begin with,
                # which happens only below K, at a session's first turn ends.
                left = min(K, ctx)
                ctx, written = left + grew, left - P + grew
            else:
                ctx += grew
                written = ctx - P if cold else grew
            if ctx >= AUTO:
                # Claude Code compacts before sending, so after a lapse its summary reads uncached input.
                cost += (P + c.input * (ctx - P) if cold else ctx) + c.output * p.N
                compacted[j] += ctx
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
    return cost, trace, paid, compacted


def arm(S, p, c, ttl, threshold, betas=(BASE,), **kw):
    """Each session's cost under one policy, `replay`'s with these arguments, and its effectiveness, the sum
    of its requests' η, for each β in `betas`."""
    cost, effectiveness = np.empty(len(S)), np.empty((len(S), len(betas)))
    for i, s in enumerate(S):
        cost[i], trace, _, _ = replay(s, p, c, threshold, ttl, **kw)
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
    # As compactions the user typed, sparkjoy's stay where the log has them. One after the
    # session's last request has no request after it to mark, so a threshold of 0 after that
    # request stands in for it, and the replay prices its summary.
    last = np.full(len(s.size), np.inf)
    if any(by == "sparkjoy" and t > s.t[-1] for t, by, _ in s.compactions):
        last[-1] = 0
    ran, ran_trace, _, _ = replay(
        replace(s, kind=np.where(s.sparkjoy, USER, s.kind)),
        p,
        c,
        last,
        s.ttl,
        idle=idle,
    )
    without, without_trace, _, _ = replay(s, p, c, np.inf, s.ttl, idle=idle)
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
    sessions; the compactions made in the middle of a turn, Claude Code's own included, and the
    contexts they compacted, summed; and the effectiveness at each β in `betas`, all also compacting
    idle sessions from IDLE_FLOOR. A mid-turn compaction at x that loses Δu more than one at a turn
    end costs ρ Δu x more (`redo`), so a caller charges Δu as cost + ρ Δu * compacted."""
    out = {}
    for turn, anywhere in policies:
        cost = mid = compacted = 0.0
        effectiveness = np.zeros(len(betas))
        for s in S:
            threshold = np.where(s.end, min(turn, anywhere), anywhere)
            spent, trace, _, at = replay(
                s, p, c, threshold, ttl, every=True, idle=IDLE_FLOOR
            )
            # A compaction before request j falls mid-turn where request j - 1 ended no turn.
            during = at[1:][~s.end[:-1]]
            cost, mid = cost + spent, mid + (during > 0).sum()
            compacted += during.sum()
            effectiveness += [eta(trace, b).sum() for b in betas]
        out[turn, anywhere] = cost, mid, compacted, effectiveness
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
            _, trace, paid, _ = replay(s, p, c, threshold, ttl, every, idle, asks=asks)
        for draw in draws:
            kind, said = draw[i]
            if judged:
                at = np.asarray(threshold, float)
                idles = (
                    None if idle is None else np.where(at == at.min(), idle, at)[said]
                )
                if claude_idle is not None:
                    idles = np.minimum(np.inf if idles is None else idles, claude_idle)
                _, trace, paid, _ = replay(
                    s, p, c, at[said], ttl, idle=idles, asks=asks
                )
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
