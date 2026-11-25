"""Measures what fills the context after a compaction, K - P in docs/cost-model.md, by source.

docs.py calls `measure(S, p)` with the sessions `fit.read` returns and `fit.measure`'s parameters
for them, and the figures it returns land in docs/figures.json's `context` section.

It reads the requests of the sessions fit.py reads, one transcript at a time. Transcripts log each
request as Claude Code sent it: an `api-request` record names its messages by hash, an
`api-request-blob` holds a message's content, and a message with no blob is rebuilt from the
transcript's user and assistant lines, whose hash (sha256 of the message's sorted, compact JSON)
names it. Each text block is split by the renderings of the attachment lines logged beside the
request, so every character goes to the attachment that put it there (the instruction files, the
skill bodies re-injected after a compaction, the skill and agent listings, files re-attached, hook
context and so on), or, failing that, to the block's own kind: the summary, kept messages, the
next message.

Characters become tokens by kind of content (prose, listings, file text, JSON, thinking), at
ratios fitted by non-negative least squares to the logged usage of the first requests after a
compaction that read the whole system prompt from the cache (`first_request_ratios`, with the
leave-one-out error of the written context as a share of it; a kind the fit counts as no tokens
has null characters per token). In the other first requests the system prompt changed across
the compaction or its cache lapsed, and what the messages don't account for is the part of it
written again.

`first_request` gives, for each source, its tokens per compaction in W, the context the first
request after a compaction writes (its size less its cache read): the median, the mean with its 90%
interval from resampling the first requests the ratios are fitted to, its share of the mean W and
the compactions it appears in; `written` is W itself.
What the sources leave of W is `system_prompt` where the system prompt changed or lapsed and
`unattributed` where it was cached, and `checks` compares the two groups, with the system
prompt's blocks from the third on (the session's) at the prose ratio. `fit_comparison` sets the
mean W beside fit.py's first request less P, and the mean W plus E beside its K - P.

E, the extra tokens the requests after a compaction add, is split over the same turns fit.py's
`excess` uses: each request's growth by source, from its new messages at ratios fitted to the growth
of later requests over the request before (`growth_ratios`), and the response before it at its
output tokens. `E_turns` and `E` give the mean tokens requests 2 to fit.SPAN add in turns of at
least fit.SPAN requests after a compaction and after a turn end, in all and by source; each
source's part of E, its `difference`, is `excess` of that source's growth, so the parts sum to
fit.py's E, in `E_total`. Its 90% intervals come from resampling sessions.
"""

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import fit
import numpy as np
from scipy.optimize import nnls

TAG = ("<system-reminder>\n", "\n</system-reminder>")
# Text no attachment line beside the request accounts for, by how it opens: reminders logged
# outside the request's span of lines, and text Claude Code writes without an attachment line.
OPENINGS = (
    ("This session is being continued from a previous conversation", "summary"),
    ("<system-reminder>\n<tools-used-before-summary>", "summary"),
    (
        "<system-reminder>\nCodebase and user instructions are shown below",
        "instructions",
    ),
    (
        "<system-reminder>\nAs you answer the user's questions, you can use the following context",
        "session_context",
    ),
    ("Concise output style is active", "output_style"),
    ("Today's date is", "date"),
    ("The following skills are available for use with the Skill tool", "skill_listing"),
)
FILE_HEADER = re.compile(
    r"Contents of (\S+) \([^)]*\):\n\n(.*?)(?=\n\nContents of \S+ \(|\n</system-reminder>|$)",
    re.S,
)


def digest(m):
    return hashlib.sha256(
        json.dumps(
            m, separators=(",", ":"), sort_keys=True, ensure_ascii=False
        ).encode()
    ).hexdigest()


def blocks_of(content):
    return (
        [{"type": "text", "text": content}]
        if isinstance(content, str)
        else list(content or [])
    )


def result_chars(c):
    """Characters of a tool result's content, a string or a list of blocks."""
    return (
        len(c)
        if isinstance(c, str)
        else sum(len(b.get("text") or "") for b in c or [] if b.get("type") == "text")
    )


def rendered(e):
    """An attachment line's rendered texts."""
    out = []
    for r in e.get("rendered") or []:
        c = r.get("content")
        if isinstance(c, list):
            c = "\n".join(
                x.get("text", "") if isinstance(x, dict) else str(x) for x in c
            )
        if isinstance(c, str) and c:
            out.append(c)
    return out


def source_of(e):
    """The source an attachment line's text belongs to."""
    a = e["attachment"]
    if a.get("type") != "hook_additional_context":
        return a.get("type")
    head = (rendered(e) or [""])[0][:300]
    event = a.get("hookEvent") or "?"
    if "hook additional context: Contents of " in head:
        return f"hook:{event}: instruction files"
    return f"hook:{event}: Leaf" if "Leaf" in head else f"hook:{event}"


def bash_verb(command):
    """The program a Bash command runs first, past `cd ... &&`, environment assignments and wrappers."""
    for part in re.split(r"&&|;|\n", command or ""):
        w = part.strip().split()
        while w and (
            re.fullmatch(r"[A-Z_][A-Z0-9_]*=\S*|\d+", w[0])
            or w[0] in ("timeout", "taskpolicy", "-b", "nice", "env", "time")
        ):
            w = w[1:]
        if w and w[0] != "cd":
            return w[0].split("/")[-1]
    return "cd"


def instruction_type(path):
    if path.endswith("MEMORY.md"):
        return "MEMORY.md"
    return "User" if path == str(Path.home() / ".claude/CLAUDE.md") else "Project"


def residual_source(rest, role, last_user):
    """The source of a user or system block's text that no attachment rendering accounts for."""
    r = rest.strip()
    if not r:
        return None
    for opening, src in OPENINGS:
        if r.startswith(opening):
            return src
    if r.startswith("Base directory for this skill:"):
        return "next message: skill body" if last_user else "kept: skill body"
    if (
        "<task-notification>" in r[:400]
        or "[SYSTEM NOTIFICATION - NOT USER INPUT]" in r[:200]
    ):
        return "next message: notification" if last_user else "kept: notification"
    if role == "system" or r.startswith("<system-reminder>"):
        return "reminder, unmatched"
    return "next message: typed" if last_user else "kept: user text"


def read_requests(path, s):
    """Each of the session's requests, in fit.py's order, as its messages' characters by source; None
    for a request the transcript has no record of. The first request after a compaction, and one with
    no base, counts all its messages; any other only those new since its base. Also each system
    prompt's blocks' characters, by shape."""
    lines = []
    for raw in path.open(errors="replace"):
        try:
            lines.append(json.loads(raw))
        except json.JSONDecodeError:
            lines.append({})
    blob, rebuilt, reqs, line_of, shapes = {}, {}, {}, {}, {}
    atts, bounds, tools, first_seen = [], [], {}, {}
    user_run, assistant_blocks = [], {}
    for i, e in enumerate(lines):
        kind = e.get("type")
        if kind == "api-request-blob":
            blob[e["hash"]] = e["message"]
        elif kind == "api-request":
            reqs[e["id"]], line_of[e["id"]] = e, i
        elif kind == "api-request-shape":
            shapes[e.get("shapeHash")] = [
                len(b.get("text") or "")
                for b in (e.get("shape") or {}).get("system") or []
            ]
        elif e.get("isSidechain"):
            continue
        elif kind == "attachment" and e.get("rendered"):
            atts.append((i, e))
        elif e.get("subtype") == "compact_boundary":
            bounds.append((i, e["timestamp"]))
        elif kind in ("user", "assistant"):
            # A message as sent can merge consecutive user lines, or an assistant message's blocks,
            # which the transcript logs one per line.
            m = e.get("message") or {}
            c = blocks_of(m.get("content"))
            rebuilt[digest({"role": m.get("role"), "content": m.get("content")})] = (
                m.get("role"),
                c,
            )
            if m.get("role") == "user":
                user_run = user_run + c
                rebuilt[digest({"role": "user", "content": user_run})] = (
                    "user",
                    user_run,
                )
            else:
                user_run = []
                blocks = assistant_blocks.setdefault(m.get("id"), [])
                blocks.extend(c)
                rebuilt[digest({"role": "assistant", "content": blocks})] = (
                    "assistant",
                    list(blocks),
                )
                for b in c:
                    if b.get("type") == "tool_use":
                        tools[b.get("id")] = (
                            b.get("name"),
                            b.get("input") or {},
                            e.get("timestamp"),
                        )
                # fit.transcript times a request by its message's first logged block.
                if (
                    m.get("usage")
                    and m.get("model") != "<synthetic>"
                    and m.get("id") not in first_seen
                ):
                    first_seen[m["id"]] = (
                        datetime.fromisoformat(
                            e["timestamp"].replace("Z", "+00:00")
                        ).timestamp(),
                        e.get("requestRef"),
                    )
    ref_at = {}
    for t, ref in first_seen.values():
        ref_at.setdefault(t, ref)
    reads = defaultdict(list)  # when the Read tool read each file
    for name, inp, ts in tools.values():
        if name == "Read" and inp.get("file_path") and ts:
            reads[inp["file_path"]].append(ts)
    bound_ts = [t for _, t in bounds]

    def read_kind(f, ts):
        earlier = [x for x in reads.get(f, []) if x < ts]
        if not earlier:
            return "Read: first read of the file"
        return (
            "Read: re-read across a compaction"
            if any(max(earlier) < b < ts for b in bound_ts)
            else "Read: re-read, no compaction since"
        )

    memo = {}

    def messages(rid):
        if rid not in memo:
            r = reqs[rid]
            base = (
                messages(r["base"])[: r.get("keep", 0)] if r.get("base") in reqs else []
            )
            memo[rid] = base + [
                blob.get(h)
                or (
                    {"role": rebuilt[h][0], "content": rebuilt[h][1]}
                    if h in rebuilt
                    else None
                )
                for h in r["tail"]
            ]
        return memo[rid]

    out = []
    for j in range(len(s.size)):
        ref = ref_at.get(s.t[j])
        r = reqs.get(ref)
        if r is None:
            out.append(None)
            continue
        L = line_of[ref]
        boundary = max((i for i, _ in bounds if i < L), default=0)
        whole = not r.get("base") or s.kind[j] in (fit.USER, fit.POLICY)
        msgs = messages(ref)
        tail = [
            m for m in (msgs if whole else msgs[r.get("keep", 0) :]) if m is not None
        ]
        lo = boundary if whole else line_of.get(r.get("base"), 0)
        # Each rendering with and without the reminder tags a system-role message drops, longest
        # first, so a short one can't take part of a longer one's text.
        pieces = sorted(
            (
                (source_of(e), txt, e)
                for i, e in atts
                if lo < i <= L + 5
                for raw in rendered(e)
                for txt in {
                    raw,
                    raw[len(TAG[0]) : -len(TAG[1])]
                    if raw.startswith(TAG[0]) and raw.endswith(TAG[1])
                    else raw,
                }
            ),
            key=lambda p: -len(p[1]),
        )
        chars, blocks = defaultdict(int), 0
        last_user = max(
            (k for k, m in enumerate(tail) if m.get("role") == "user"), default=-1
        )
        for k, m in enumerate(tail):
            for b in blocks_of(m.get("content")):
                blocks += 1
                kind = b.get("type")
                if kind == "text":
                    rest = b.get("text") or ""
                    for src, txt, e in pieces:
                        at = rest.find(txt) if txt and len(txt) <= len(rest) else -1
                        if at < 0:
                            continue
                        rest = rest[:at] + rest[at + len(txt) :]
                        if src == "instructions":
                            files = e["attachment"].get("files") or []
                            for f in files:
                                chars[
                                    "instructions:"
                                    + instruction_type(f.get("path") or "")
                                ] += len(f.get("content") or "")
                            chars["instructions:header"] += max(
                                len(txt)
                                - sum(len(f.get("content") or "") for f in files),
                                0,
                            )
                        else:
                            chars[src] += len(txt)
                    if m.get("role") == "assistant":
                        src = "kept: assistant text" if whole else "assistant text"
                    else:
                        src = residual_source(rest, m.get("role"), k == last_user)
                    if src == "instructions":
                        # Its attachment line fell outside the request's lines: split it by its file headers.
                        files = FILE_HEADER.findall(rest)
                        for p, body in files:
                            chars["instructions:" + instruction_type(p)] += len(body)
                        chars["instructions:header"] += len(rest) - sum(
                            len(body) for _, body in files
                        )
                    elif src:
                        chars[src] += len(rest)
                elif kind == "thinking":
                    chars["kept: thinking (signature)"] += len(
                        b.get("signature") or ""
                    ) + len(b.get("thinking") or "")
                elif kind == "redacted_thinking":
                    chars["kept: thinking (signature)"] += len(b.get("data") or "")
                elif kind == "tool_use":
                    chars[
                        "kept: assistant tool_use" if whole else "assistant tool_use"
                    ] += len(json.dumps(b.get("input"), ensure_ascii=False))
                elif kind == "tool_result":
                    name, inp, ts = tools.get(b.get("tool_use_id"), ("?", {}, None))
                    n = result_chars(b.get("content"))
                    if whole:
                        chars["kept: tool_result"] += n
                    elif name == "Read":
                        chars[read_kind(inp.get("file_path"), ts or "")] += n
                    elif name == "Bash":
                        chars[
                            f"tool_result: Bash: {bash_verb(inp.get('command'))}"
                        ] += n
                    else:
                        chars[f"tool_result: {name}"] += n
                elif kind == "tool_addition":
                    chars["deferred tool schemas"] += len(
                        json.dumps(b.get("tool"), ensure_ascii=False)
                    )
                else:
                    chars[f"block: {kind}"] += len(json.dumps(b, ensure_ascii=False))
        out.append(
            dict(
                id=ref,
                base=r.get("base"),
                keep=r.get("keep", 0),
                messages=len(msgs),
                shape=r.get("shapeHash"),
                chars=dict(chars),
                blocks=blocks,
            )
        )
    return out, shapes


# ---- Kinds of content, each with its own characters per token ----

LISTINGS = ("skill_listing", "agent_listing_delta", "deferred_tools_delta")
FIRST_KINDS = ["prose", "file_text", "json", "thinking", "listings"]
TAIL_KINDS = ["prose", "file_text", "tool_output", "json"]
ASSISTANT = (
    "assistant text",
    "assistant tool_use",
    "kept: thinking (signature)",
)  # counted by the response's output tokens


def first_kind(src):
    if src in ("file", "kept: tool_result"):
        return "file_text"
    if src in ("deferred tool schemas", "kept: assistant tool_use"):
        return "json"
    if "thinking" in src:
        return "thinking"
    return "listings" if src in LISTINGS else "prose"


def tail_kind(src):
    if src.startswith("Read:") or src == "file":
        return "file_text"
    if src.startswith("tool_result:"):
        return "tool_output"
    return "json" if src == "deferred tool schemas" else "prose"


def bash(src, verbs=None):
    """Whether a source is the output of a Bash command running one of `verbs` (any, for None)."""
    return src.startswith("tool_result: Bash: ") and (
        verbs is None or src.rsplit(": ", 1)[1] in verbs
    )


# The parts of the first request after a compaction, and of the growth that makes up E: each a key
# of the figures and which sources it takes. The first that matches takes it.
FIRST_PARTS = [
    ("summary", lambda k: k == "summary"),
    ("skill_bodies", lambda k: k == "invoked_skills"),  # re-injected
    ("skill_listing", lambda k: k == "skill_listing"),
    ("user_claude_md", lambda k: k == "instructions:User"),  # ~/.claude/CLAUDE.md
    # The project's AGENTS.md, CLAUDE.md and their imports.
    ("project_instructions", lambda k: k == "instructions:Project"),
    ("memory_index", lambda k: k == "instructions:MEMORY.md"),
    ("instructions_wrapper", lambda k: k.startswith("instructions:")),
    ("files", lambda k: k in ("file", "compact_file_reference")),  # re-attached
    ("agent_listing", lambda k: k == "agent_listing_delta"),
    ("mcp_instructions", lambda k: k == "mcp_instructions_delta"),
    # Deferred tools' names, and the schemas of those loaded.
    (
        "deferred_tools",
        lambda k: k in ("deferred_tools_delta", "deferred tool schemas"),
    ),
    ("kept_messages", lambda k: k.startswith("kept:")),  # thinking dropped
    # The next message: typed, a command or a notification.
    ("next_message", lambda k: k.startswith("next message:")),
    ("hooks", lambda k: k.startswith("hook:")),  # hook additional context
    # The other reminders: environment, git status, output style, date, tasks.
    ("other_reminders", lambda k: True),
]
FIRST_REST = ["system_prompt", "unattributed"]
E_PARTS = [
    # The response before: its text, tool calls and thinking.
    ("response_before", lambda k: k == "_out"),
    # Reads of a file read before the compaction, of one read since it, and of one not read before.
    ("read_across_compaction", lambda k: k == "Read: re-read across a compaction"),
    ("read_since_compaction", lambda k: k == "Read: re-read, no compaction since"),
    ("read_new_file", lambda k: k == "Read: first read of the file"),
    (
        "bash_printing_files",
        lambda k: bash(k, ("sed", "cat", "head", "tail", "awk", "nl", "less", "bat")),
    ),
    (
        "bash_searching",
        lambda k: bash(k, ("grep", "rg", "ls", "find", "fd", "wc", "jq", "tree")),
    ),
    ("bash_git", lambda k: bash(k, ("git", "gh"))),
    ("bash_other", lambda k: bash(k)),  # tests, scripts, leaf, ...
    ("other_tools", lambda k: k.startswith("tool_result:")),
    ("skill_bodies", lambda k: "skill body" in k),  # loaded
    # Subdirectories' AGENTS.md, which a PostToolUse hook adds.
    ("subdirectory_agents_md", lambda k: k.endswith(": instruction files")),
    ("leaf_hooks", lambda k: k.startswith("hook:")),
    # Messages: typed, queued, notifications.
    (
        "messages",
        lambda k: k.startswith(("next message:", "kept:")) or k == "queued_command",
    ),
    ("other_reminders", lambda k: True),  # and other attachments
]
E_REST = ["unattributed"]  # growth not matched to a logged tail


def part_of(parts, src):
    return next(i for i, (_, test) in enumerate(parts) if test(src))


def fit_ratios(A, y):
    """Tokens per character of each column, by non-negative least squares."""
    return nnls(np.asarray(A, float), np.asarray(y, float))[0]


def characters_per_token(kinds, ratio):
    """Each kind's characters per token, from its tokens per character; inf for none."""
    return {k: 1 / t if t else np.inf for k, t in zip(kinds, ratio, strict=False)}


def measure(S, p):
    """The figures of what fills the context after a compaction, for the sessions `S` that
    `fit.read` returns and `fit.measure`'s parameters `p` for them, as the module docstring
    describes them."""
    R, shapes = [], {}
    for s in S:
        reqs, sh = read_requests(s.path, s)
        R.append(reqs)
        shapes.update(sh)
    rng = np.random.default_rng(0)

    # ---- 1. The first request after a compaction ----
    first = []  # (session, index, request, its system prompt was all read from the cache)
    for si, s in enumerate(S):
        for j in np.flatnonzero((s.kind == fit.USER) | (s.kind == fit.POLICY)):
            r, prev = R[si][j], R[si][j - 1]
            cached = bool(
                prev
                and r
                and prev["shape"] == r["shape"]
                and s.t[j] - s.t[j - 1] < 3600
                and s.read[j] > 0
            )
            first.append((s, j, r, cached))
    assert all(r for _, _, r, _ in first), "a first request without a request record"
    W = np.array([s.size[j] - s.read[j] for s, j, _, _ in first], float)
    cached = np.array([c for *_, c in first])
    X = np.array(
        [
            [
                sum(n for k, n in r["chars"].items() if first_kind(k) == kind)
                for kind in FIRST_KINDS
            ]
            for _, _, r, _ in first
        ],
        float,
    )
    ratio = fit_ratios(X[cached], W[cached])
    loo = [
        (
            W[cached][i]
            - X[cached][i]
            @ fit_ratios(np.delete(X[cached], i, 0), np.delete(W[cached], i))
        )
        / W[cached][i]
        for i in range(cached.sum())
    ]

    def first_table(ratio):
        T = np.zeros((len(first), len(FIRST_PARTS) + 2))
        tau = dict(zip(FIRST_KINDS, ratio, strict=True))
        for i, (_, _, r, c) in enumerate(first):
            for k, n in r["chars"].items():
                T[i, part_of(FIRST_PARTS, k)] += n * tau[first_kind(k)]
            T[i, len(FIRST_PARTS) + c] = W[i] - T[i, : len(FIRST_PARTS)].sum()
        return T

    T = first_table(ratio)
    ci = np.percentile(
        [
            first_table(fit_ratios(X[cached][d], W[cached][d])).mean(0)
            for d in (
                rng.integers(0, cached.sum(), cached.sum()) for _ in range(fit.DRAWS)
            )
        ],
        [5, 95],
        axis=0,
    )
    read = np.array([s.read[j] for s, j, _, _ in first], float)
    session_block = np.array(
        [sum(shapes[r["shape"]][2:]) for _, _, r, _ in first], float
    )
    rest = T[:, len(FIRST_PARTS) :].sum(1)
    summary = np.array([r["chars"].get("summary", 0) for _, _, r, _ in first])
    first_keys = [k for k, _ in FIRST_PARTS] + FIRST_REST

    # ---- 2. E ----
    def exact(prev, r):
        """r's messages are prev's plus its own, so its growth over prev is theirs."""
        return bool(
            prev
            and r
            and r["base"] == prev["id"]
            and r["shape"] == prev["shape"]
            and r["keep"] == prev["messages"]
        )

    XB, yB = [], []
    for s, reqs in zip(S, R, strict=True):
        for j in range(1, len(reqs)):
            if s.kind[j] == fit.NORMAL and exact(reqs[j - 1], reqs[j]):
                XB.append(
                    [
                        sum(
                            n
                            for k, n in reqs[j]["chars"].items()
                            if k not in ASSISTANT and tail_kind(k) == kind
                        )
                        for kind in TAIL_KINDS
                    ]
                    + [s.out[j - 1]]
                )
                yB.append(s.size[j] - s.size[j - 1])
    XB, yB = np.array(XB, float), np.array(yB, float)
    ratioB = fit_ratios(XB, yB)
    tauB = dict(zip(TAIL_KINDS, ratioB, strict=False))

    # Each request's growth (fit.py's grew) by source; what its logged tail doesn't account for is unattributed.
    turns = []  # per session, fit.turn_list's turns with each request's growth by source
    for s, reqs in zip(S, R, strict=True):
        G = np.zeros((len(s.size), len(E_PARTS) + 1))
        for j in range(1, len(reqs)):
            if s.kind[j] == fit.NORMAL and exact(reqs[j - 1], reqs[j]):
                G[j, 0] = s.out[j - 1] * ratioB[-1]
                for k, n in reqs[j]["chars"].items():
                    if k not in ASSISTANT:
                        G[j, part_of(E_PARTS, k)] += n * tauB[tail_kind(k)]
        G[:, -1] = s.grew - G[:, :-1].sum(1)
        turns.append(fit.turn_list(SimpleNamespace(end=s.end, kind=s.kind, grew=G)))

    def e_parts(ts):
        return np.array(
            [
                fit.excess([(after, g[:, c]) for after, g in ts])
                for c in range(len(E_PARTS) + 1)
            ]
        )

    E = e_parts([t for ts in turns for t in ts])
    assert abs(E.sum() - p.E) < 1e-6 * max(abs(p.E), 1), (
        f"E's parts sum to {E.sum():,.2f}, not fit.py's E {p.E:,.2f}"
    )
    eci = np.percentile(
        [
            e_parts([t for i in rng.integers(0, len(S), len(S)) for t in turns[i]])
            for _ in range(fit.DRAWS)
        ],
        [5, 95],
        axis=0,
    )
    # What requests 2 to SPAN of each kind of turn add, whose difference `excess` takes.
    added = {
        after: np.array(
            [
                g[1 : fit.SPAN].sum(0)
                for ts in turns
                for b, g in ts
                if b == after and len(g) >= fit.SPAN
            ]
        )
        for after in ("compaction", "turn")
    }
    C, N = added["compaction"], added["turn"]
    e_keys = [k for k, _ in E_PARTS] + E_REST

    return {
        "compactions": len(first),
        "sessions": len(S),
        "first_request_ratios": {
            "compactions": cached.sum(),
            "characters_per_token": characters_per_token(FIRST_KINDS, ratio),
            "median_abs_relative_error": np.median(np.abs(loo)),
            "p90_abs_relative_error": np.percentile(np.abs(loo), 90),
        },
        "first_request": {
            k: {
                "median": np.median(T[:, i]),
                "mean": {"value": T[:, i].mean(), "interval_90": ci[:, i]},
                "share": T[:, i].mean() / W.mean(),
                "compactions": (T[:, i] != 0).sum(),
            }
            for i, k in enumerate(first_keys)
        },
        "written": {"median": np.median(W), "mean": W.mean()},
        "checks": {
            "cached": {
                "remainder_median": np.median(rest[cached]),
                "remainder_mean": rest[cached].mean(),
                "remainder_p90_abs": np.percentile(np.abs(rest[cached]), 90),
                "cache_read_median": np.median(read[cached]),
            },
            "changed_or_lapsed": {
                "compactions": (~cached).sum(),
                "reading_nothing": (read == 0).sum(),
                "remainder_median": np.median(rest[~cached]),
                "session_block_median": np.median(session_block[~cached]) * ratio[0],
                "cache_read_median": np.median(read[~cached]),
            },
        },
        "summary_median_at_4_characters_per_token": np.median(summary) / 4,
        "fit_comparison": {
            "first_request": p.first_request,
            "P": p.P,
            "first_request_minus_P": p.first_request - p.P,
            "mean_written_plus_E": W.mean() + p.E,
            "K_minus_P": p.K - p.P,
        },
        "growth_ratios": {
            "requests": len(yB),
            "characters_per_token": characters_per_token(TAIL_KINDS, ratioB),
            "response_tokens_per_output_token": ratioB[-1],
            "median_abs_error": np.median(np.abs(yB - XB @ ratioB)),
        },
        "E_turns": {
            "after_compaction": {"turns": len(C), "tokens_added": C.sum(1).mean()},
            "after_turn_end": {"turns": len(N), "tokens_added": N.sum(1).mean()},
        },
        "E": {
            k: {
                "after_compaction": C[:, i].mean(),
                "after_turn_end": N[:, i].mean(),
                "difference": {"value": E[i], "interval_90": eci[:, i]},
            }
            for i, k in enumerate(e_keys)
        },
        "E_total": E.sum(),
    }
