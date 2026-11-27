"""Cuts a case's request into parts and scores them by what the work after the
turn end used, for `share.py`.

A part is one block of the request (the system prompt, the tool definitions,
a text, a tool call, a tool's result, a thinking block), or one section of a
text block where Claude Code writes several sources into one: each file of
an instruction-files block, and each source of what it writes back after a
compaction (files re-attached, each skill's body, the skill and agent
listings, the deferred tools, MCP instructions, the environment). `kind_of`
names what each part holds, from how its text opens, as Claude Code writes
it.

`resolve` then sorts the parts a compaction puts back from those it drops,
as Claude Code does it (measured in `model/context.py`): the system prompt
and tools; the latest copy of each instruction file; each skill loaded in
the session, its latest copy cut to `SKILL_CHARS`, most recent first while
they fit in `SKILLS_CHARS`; and the listings and environment. Earlier
copies, and the rest of a skill past what comes back, are dropped like the
transcript, though the work loses nothing in an earlier copy, whose text
comes back in the later one.

`share.py` counts each kind's tokens with the API (`blank` takes a kind's
parts out of the request) and `sized` shares a kind's count among its parts
by characters, with each image counted on its own.

A labeller marks each part needed, helpful or neither, in several runs
(`share.py`), and `scored` turns the runs into shares of the context: what
the work needed, and needed or was helped by, of the whole context, and of
it what a compaction drops (`lost_*`), which is what the work then has to
read back and what the share is weighed against. It judges them by what the
session did next (`next_work`), one record per message, tool call or result,
which `shortened` cuts to the window, each value keeping its head and tail.
"""

import copy
import json
import re
from collections.abc import Iterable

# The kinds a compaction puts back.
RETURNS = {
    "system prompt",
    "tools",
    "tools loaded later",
    "instruction files",
    "skill body",
    "Claude Code's listings and environment",
}
# The kinds a compaction drops but puts back the same text of, in a later
# copy: what the work needs of them it doesn't read back.
COPIES = {"instruction files, an earlier copy", "skill body, an earlier copy"}
# The kinds merged for the report, in the order a reader meets them: first
# those a compaction puts back, then those it drops.
GROUPS = {
    "system prompt": "System prompt and tools",
    "tools": "System prompt and tools",
    "tools loaded later": "System prompt and tools",
    "instruction files": "Instruction files",
    "skill body": "Skill bodies",
    "Claude Code's listings and environment": "Claude Code's listings and environment",
    "instruction files, an earlier copy": "Earlier copies of what comes back",
    "skill body, an earlier copy": "Earlier copies of what comes back",
    "skill text that doesn't come back": "Skill text past what comes back",
    "summary of an earlier compaction": "What an earlier compaction carried",
    "files re-attached at an earlier compaction": "What an earlier compaction carried",
    "file reads": "File reads",
    "other tool output": "Other tool output",
    "subagent reports": "Other tool output",
    "model's tool calls": "The model's tool calls",
    "model's text": "The model's text",
    "thinking": "The model's thinking",
    "user's message": "The user's messages",
    "Leaf page input": "The user's messages",
    "Claude Code's reminders and notices": "Claude Code's reminders and notices",
}
ORDER = [*dict.fromkeys(GROUPS.values()), "Everything else"]
# A part's label across the runs, strongest first: needed in every run, or
# needed in one and helpful in another; needed or helpful in every run;
# needed or helpful in one run only; in none.
LABELS = ("needed", "helpful", "one run", "not needed")
# Claude Code puts back each skill loaded in the session cut to its first
# 20,000 characters, the most recent first, while they total 100,000: 742
# invoked_skills attachments from 2.1.325 to 2.1.332 never passed either.
SKILL_CHARS = 20_000
SKILLS_CHARS = 100_000

# How a text block opens, for the kinds Claude Code writes into the messages.
OPENINGS = [
    (r"^This session is being continued", "summary of an earlier compaction"),
    (
        r"^(Called the Read tool|Note: .* was read before the last conversation|Result of calling the Read tool)",
        "files re-attached at an earlier compaction",
    ),
    (r"^(### Skill: .*\n.*\n+)?Base directory for this skill", "skill body"),
    (
        r"Codebase and user instructions are shown below|Instruction files were re-read|^Contents of \S+ \(",
        "instruction files",
    ),
    (
        r"^(<system-reminder>\n)?(As you answer the user's questions, you can use the following context"
        r"|The following skills (were invoked EARLIER|are available for use)"
        r"|The following (deferred )?tools (just became|are now) available"
        r"|Available agent types|# MCP Server Instructions|The following MCP servers"
        r"|# Environment\n|You have been invoked in the following environment"
        r"|# Output Style|# Concise Style)",
        "Claude Code's listings and environment",
    ),
    (r"leaf-delivery|Leaf has new input|Leaf needs attention", "Leaf page input"),
    (
        r"^(\[SYSTEM NOTIFICATION|<task-notification>|<system-reminder>|<command-|<local-command"
        r"|Concise output style is active|# Environment update|Note: .* changed on disk"
        r"|The task tools haven't been used|While bypass permissions|Today's date is)",
        "Claude Code's reminders and notices",
    ),
]
# Where Claude Code starts a new source inside a block it writes several
# into: each file of an instruction-files block, and each source of what it
# writes back after a compaction.
SECTION = re.compile(
    r"^(Contents of \S+ \("
    r"|Called the Read tool with the following input"
    r"|Note: \S+ was read before the last conversation"
    r"|The following skills were invoked EARLIER"
    r"|### Skill: "
    r"|The following tools just became available"
    r"|The following deferred tools are now available"
    r"|Available agent types for the Agent tool"
    r"|The following skills are available for use with the Skill tool"
    r"|# MCP Server Instructions"
    r"|# Environment\n"
    r"|# Output Style)",
    re.M,
)
INSTRUCTION_FILE = re.compile(r"^Contents of (\S+) \(", re.M)
# The kinds of user-message block that hold several sources.
SPLIT = {
    "instruction files",
    "files re-attached at an earlier compaction",
    "Claude Code's listings and environment",
}
SKILL_DIR = re.compile(r"Base directory for this skill: (\S+)")
# A command's opening move to a directory: `cd <dir> && ` or `cd <dir>; `.
CD = re.compile(r"""^cd\s+("[^"]*"|'[^']*'|\S+)\s*(&&|;)\s*""")
# The programs whose output is a file's text, when a command opens with one.
PRINTS_FILES = {"cat", "sed", "head", "tail", "nl"}


def describe_call(block: dict) -> str:
    """A tool call in a line: the tool, and what it was called on."""
    name, given = block["name"], block.get("input") or {}
    target = next(
        (
            given[k]
            for k in (
                "file_path",
                "command",
                "skill",
                "url",
                "pattern",
                "description",
                "prompt",
            )
            if given.get(k)
        ),
        json.dumps(given),
    )
    # Most commands open by moving to a worktree, whose path would leave no
    # room for the file a read names.
    target = CD.sub("", str(target))
    return f"{name}: {target[:160]}"


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    out = []
    for b in content or []:
        if b.get("type") == "text":
            out.append(b.get("text") or "")
        elif b.get("type") == "image":
            out.append("[image]")
        else:
            out.append(json.dumps(b))
    return "\n".join(out)


def blocks_of(message: dict) -> list[dict]:
    content = message["content"]
    return content if isinstance(content, list) else [{"type": "text", "text": content}]


# The next work's record of a compaction at the turn end itself, which
# `share.LABEL`'s "needed" reads: the work after it ran without the context.
AT_TURN_END = (
    "COMPACTED here, at the turn end: the work below ran without the context above"
)
# Where `shortened` cuts a value, between its head and tail.
CUT = " […] "


def next_work(entries: Iterable[dict]) -> list[dict]:
    """What the session did after a turn end, from its transcript's entries
    after it, up to its next compaction, where the context starts again
    whatever the turn end decided: one record for each thing the user typed,
    each text of the model's, each tool call with its input, and each
    result, each whole, for `shortened` to cut to the window. A compaction at
    the turn end itself, as when sparkjoy compacted there, doesn't end it,
    since the work after it is what the context would have served."""
    records, began, tools = [], False, {}
    for e in entries:
        t = e.get("timestamp", "")[:16]
        if e.get("type") == "system" and e.get("subtype") == "compact_boundary":
            if began:
                records.append(
                    {
                        "t": t,
                        "who": "compaction",
                        "text": "COMPACTED, and the session went on",
                    }
                )
                break
            records.append({"t": t, "who": "compaction", "text": AT_TURN_END})
            continue
        if e.get("isSidechain") or e.get("isCompactSummary"):
            continue
        if e.get("type") == "user" and not e.get("isMeta"):
            for b in blocks_of(e["message"]):
                text = b.get("text") or ""
                if b.get("type") == "text" and text and not text.startswith("<"):
                    records.append({"t": t, "who": "user", "text": text})
                elif b.get("type") == "tool_result":
                    record = {"t": t, "who": "result"}
                    if tool := tools.get(b.get("tool_use_id")):
                        record["tool"] = tool
                    if b.get("is_error"):
                        record["error"] = True
                    records.append(record | {"text": text_of(b.get("content"))})
        elif e.get("type") == "assistant":
            began = True
            for b in e["message"].get("content") or []:
                if b.get("type") == "text" and b.get("text"):
                    records.append({"t": t, "who": "assistant", "text": b["text"]})
                elif b.get("type") == "tool_use":
                    tools[b.get("id")] = b["name"]
                    records.append(
                        {
                            "t": t,
                            "who": "call",
                            "tool": b["name"],
                            "input": b.get("input") or {},
                        }
                    )
    return records


def values(value) -> list[str]:
    """The strings in a record's `text` and `input`, which `shortened` may cut."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        value = value.values()
    elif not isinstance(value, list):
        return []
    return [s for v in value for s in values(v)]


def length(record: dict) -> int:
    return sum(map(len, values([record.get("text"), record.get("input")])))


def cut(value, half: int):
    """`value` with each string longer than twice `half` kept to its head
    and tail."""
    if isinstance(value, str):
        return (
            f"{value[:half]}{CUT}{value[len(value) - half :]}"
            if len(value) > 2 * half
            else value
        )
    if isinstance(value, dict):
        return {k: cut(v, half) for k, v in value.items()}
    if isinstance(value, list):
        return [cut(v, half) for v in value]
    return value


def common(lengths: list[int], budget: int) -> int | None:
    """The largest length every item longer than it is cut to so that
    `lengths` take `budget` in all, or None where they fit whole."""
    total = 0
    for i, n in enumerate(ordered := sorted(lengths)):
        if total + n * (len(ordered) - i) > budget:
            return max((budget - total) // (len(ordered) - i), 0)
        total += n
    return None


def shortened(records: list[dict], chars: int) -> list[dict]:
    """`records` with their `text` and `input` in about `chars` characters:
    each record longer than a common length is cut to it, the length the
    longest that fits, and within a record its longest values are cut first,
    each keeping its head and tail, so every record stays whole JSON."""
    if (limit := common([length(r) for r in records], chars)) is None:
        return records
    out = []
    for r in records:
        within = common(list(map(len, values([r.get("text"), r.get("input")]))), limit)
        if within is None:
            out.append(r)
            continue
        out.append(r | {k: cut(r[k], within // 2) for k in ("text", "input") if k in r})
    return out


def rendered(records: list[dict]) -> str:
    """The records as the labeller reads them, one JSON object per line."""
    return "\n".join(json.dumps(r, ensure_ascii=False) for r in records)


def sections(text: str, role: str) -> list[tuple[int, int]]:
    """Where each source starts and ends in a text block Claude Code writes
    several into (a system message, or an instruction-files or listing
    block); one span for any other block."""
    starts = [m.start() for m in SECTION.finditer(text)]
    if not starts or (role != "system" and text_kind(text, role) not in SPLIT):
        return [(0, len(text))]
    # What comes before the first source, such as a block's preamble, goes
    # with it.
    starts[0] = 0
    return list(zip(starts, [*starts[1:], len(text)], strict=True))


def parts_of(request: dict) -> list[dict]:
    """The request's parts in order, each with its `id`, `kind`, `chars`, the
    kind of `block` it is, a line describing it, where it is in the request
    (`at`: "system", "tools", or a message and block, and for a section its
    span) and, for text, its opening (`head`)."""
    parts = [
        {
            "kind": "system prompt",
            "block": "system prompt",
            "desc": "system prompt",
            "chars": sum(len(b.get("text", "")) for b in request["system"]),
            "at": "system",
        },
        {
            "kind": "tools",
            "block": "tools",
            "desc": f"{len(request['tools'])} tool definitions",
            "chars": len(json.dumps(request["tools"])),
            "at": "tools",
        },
    ]
    calls = {}
    for i, m in enumerate(request["messages"]):
        for j, b in enumerate(blocks_of(m)):
            t = b.get("type")
            at = [i, j]
            if t == "tool_use":
                calls[b["id"]] = b
                part = {
                    "kind": "model's tool calls",
                    "block": "tool call",
                    "desc": describe_call(b),
                    "chars": len(json.dumps(b.get("input"))),
                }
            elif t == "tool_result":
                body = text_of(b.get("content"))
                call = calls.get(b.get("tool_use_id"))
                said = describe_call(call) if call else "?"
                content = b.get("content")
                kind = tool_output_kind(call)
                if kind == "skill body" and not SKILL_DIR.search(body[:600]):
                    # Claude Code's Skill tool answers "Launching skill",
                    # and the body follows as text.
                    kind = "other tool output"
                part = {
                    "kind": kind,
                    "block": "tool result",
                    "desc": f"result of {said}",
                    "chars": len(body.replace("[image]", "")),
                    "head": body[:300],
                    "images": sum(
                        x.get("type") == "image"
                        for x in (content if isinstance(content, list) else [])
                    ),
                }
            elif t == "text":
                body = b.get("text") or ""
                for start, end in sections(body, m["role"]):
                    text = body[start:end]
                    kind = text_kind(text, m["role"])
                    part = {
                        "kind": kind,
                        "block": f"{m['role']} text",
                        "desc": text.lstrip()[:160].replace("\n", " "),
                        "chars": len(text),
                        "head": text[:300],
                        "at": at
                        if (start, end) == (0, len(body))
                        else [i, j, start, end],
                    }
                    if kind == "instruction files" and (
                        found := INSTRUCTION_FILE.search(text)
                    ):
                        part["path"] = found.group(1)
                        part["desc"] = f"instruction file {found.group(1)}"
                    if kind == "skill body" and (found := SKILL_DIR.search(text[:600])):
                        part["skill"] = found.group(1)
                    parts.append(part)
                continue
            elif t in ("thinking", "redacted_thinking"):
                part = {
                    "kind": "thinking",
                    "block": "thinking",
                    "desc": (b.get("thinking") or "").strip()[:160].replace("\n", " ")
                    or "thinking, its text not kept",
                    # The transcript keeps no thinking text; its signature
                    # grows with it.
                    "chars": len(b.get("thinking") or "")
                    or len(b.get("signature") or b.get("data") or ""),
                }
            elif t == "tool_addition":
                tool = (b.get("tool") or {}).get("definition") or {}
                part = {
                    "kind": "tools loaded later",
                    "block": "tool definition",
                    "desc": f"tool definition: {tool.get('name', '?')}",
                    "chars": len(json.dumps(b)),
                }
            elif t == "image":
                part = {
                    "kind": "other tool output",
                    "block": "image",
                    "desc": "image",
                    "chars": 0,
                    "images": 1,
                }
            else:
                part = {"kind": t or "?", "desc": t or "?", "chars": len(json.dumps(b))}
            part.setdefault("block", part["kind"])
            part.setdefault("at", at)
            parts.append(part)
    for p in parts:
        # A skill loaded by the Skill tool can arrive in its result.
        if (
            p["kind"] == "skill body"
            and "skill" not in p
            and (found := SKILL_DIR.search(p.get("head", "")))
        ):
            p["skill"] = found.group(1)
    return resolve(parts)


def text_kind(text: str, role: str) -> str:
    """What a text block, or a section of one, holds, from how it opens."""
    head = text.lstrip()[:400]
    # Claude Code wraps what it adds to a user message in a reminder tag.
    wrapped = head.startswith("<system-reminder>")
    head = head.removeprefix("<system-reminder>").lstrip()
    for pattern, kind in OPENINGS:
        if re.search(pattern, head):
            return kind
    if role == "assistant":
        return "model's text"
    if role == "user" and not wrapped:
        return "user's message"
    return "Claude Code's reminders and notices"


def programs(command: str) -> list[str]:
    """The program each step of a command runs first, a step being what
    `&&`, `;` or a new line separates; a pipe's later stages, which read
    what the first prints, don't count."""
    out = []
    for step in re.split(r"&&|;|\n", command):
        words = step.split("|", 1)[0].split()
        while words and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=\S*", words[0]):
            words = words[1:]
        if words and words[0] != "cd":
            out.append(words[0].rsplit("/", 1)[-1])
    return out


def tool_output_kind(call: dict | None) -> str:
    """What a tool's result holds, from the call it answers."""
    name = (call or {}).get("name")
    command = str(((call or {}).get("input") or {}).get("command") or "")
    if name == "Read" or (name == "Bash" and PRINTS_FILES & set(programs(command))):
        return "file reads"
    if name == "Skill":
        return "skill body"
    if name == "Agent":
        return "subagent reports"
    return "other tool output"


def resolve(parts: list[dict]) -> list[dict]:
    """Mark what a compaction puts back: the latest copy of each instruction
    file, and the skills Claude Code re-injects, splitting a skill's latest
    copy where it is cut; then number the parts."""
    latest = {}
    for k, p in enumerate(parts):
        if p["kind"] == "instruction files":
            latest[p.get("path") or k] = k
    for k, p in enumerate(parts):
        if p["kind"] == "instruction files" and latest.get(p.get("path") or k) != k:
            p["kind"] = "instruction files, an earlier copy"
    # Each skill's latest copy, and how recently it was loaded: the skills
    # written back after an earlier compaction come most recent first, all
    # before anything loaded since.
    latest, recency = {}, {}
    together = {}
    for k, p in enumerate(parts):
        if p["kind"] == "skill body":
            together.setdefault(tuple(p["at"][:2]), []).append(k)
    for ks in together.values():
        for pos, k in enumerate(ks):
            name = parts[k].get("skill") or k
            if name in latest:
                parts[latest[name]]["kind"] = "skill body, an earlier copy"
            latest[name] = k
            recency[name] = ks[0] + (len(ks) - pos) / (len(ks) + 1)
    room, cut = SKILLS_CHARS, {}
    for name in sorted(latest, key=recency.get, reverse=True):
        k = latest[name]
        size = min(parts[k]["chars"], SKILL_CHARS)
        if size > room:
            parts[k]["kind"] = "skill text that doesn't come back"
            continue
        room -= size
        if parts[k]["chars"] > SKILL_CHARS and parts[k]["block"].endswith(" text"):
            cut[k] = SKILL_CHARS
    out = []
    for k, p in enumerate(parts):
        if k in cut:
            start, end = (
                (p["at"][2], p["at"][3]) if len(p["at"]) == 4 else (0, p["chars"])
            )
            keep = cut[k]
            out.append(p | {"chars": keep, "at": [*p["at"][:2], start, start + keep]})
            out.append(
                p
                | {
                    "kind": "skill text that doesn't come back",
                    "desc": f"the rest of {p.get('skill', 'a skill')}, past its first {SKILL_CHARS:,} characters",
                    "chars": p["chars"] - keep,
                    "at": [*p["at"][:2], start + keep, end],
                }
            )
        else:
            out.append(p)
    for i, p in enumerate(out):
        p["id"] = i
    return out


def blank(request: dict, cut: list[dict], ids) -> dict:
    """`request` with the parts `ids` of its parts `cut` taken out, leaving
    the blocks they were in."""
    r = copy.deepcopy(request)
    spans = {}
    for p in (cut[i] for i in ids):
        at = p["at"]
        if at == "system":
            r["system"] = [b | {"text": "."} for b in r["system"]]
        elif at == "tools":
            r["tools"] = []
        elif len(at) == 4:
            spans.setdefault((at[0], at[1]), []).append((at[2], at[3]))
        else:
            spans[(at[0], at[1])] = None
    for (i, j), cuts in spans.items():
        m = r["messages"][i]
        if isinstance(m["content"], str):
            m["content"] = [{"type": "text", "text": m["content"]}]
        b = m["content"][j]
        t = b.get("type")
        if t == "text":
            text = b["text"]
            if cuts is None:
                text = ""
            else:
                for start, end in sorted(cuts, reverse=True):
                    text = text[:start] + text[end:]
            m["content"][j] = b | {"text": text or "."}
        elif t == "tool_result":
            m["content"][j] = {k: v for k, v in b.items() if k != "content"} | {
                "content": "."
            }
        elif t == "tool_use":
            m["content"][j] = b | {"input": {}}
        elif t == "image":
            m["content"][j] = {"type": "text", "text": "."}
        elif t in ("thinking", "redacted_thinking", "tool_addition"):
            m["content"][j] = None
    for m in r["messages"]:
        if isinstance(m["content"], list) and None in m["content"]:
            m["content"] = [b for b in m["content"] if b is not None] or [
                {"type": "text", "text": "."}
            ]
    return r


def images_of(request: dict, part: dict) -> list[dict]:
    """The images a part holds, as blocks to count."""
    at = part["at"]
    if not part.get("images") or not isinstance(at, list):
        return []
    b = blocks_of(request["messages"][at[0]])[at[1]]
    content = b.get("content") if b.get("type") == "tool_result" else [b]
    return [x for x in content if isinstance(x, dict) and x.get("type") == "image"]


def sized(
    parts: list[dict],
    counts: dict,
    images: dict | None = None,
    total: int | None = None,
) -> list[dict]:
    """Give each part its `tokens`: each kind's count in `counts` shared
    among its parts by characters, plus the tokens of each part's images in
    `images` (by part id). With `total`, the messages' parts are scaled so
    that all the parts add up to it, the rest being what the messages' and
    blocks' framing takes."""
    images = images or {}
    chars, pictures, many = {}, {}, {}
    for p in parts:
        k = p["kind"]
        chars[k] = chars.get(k, 0) + p["chars"]
        pictures[k] = pictures.get(k, 0) + images.get(p["id"], 0)
        many[k] = many.get(k, 0) + 1
    for p in parts:
        k = p["kind"]
        text = max(counts.get(k, 0) - pictures[k], 0)
        share = p["chars"] / chars[k] if chars[k] else 1 / many[k]
        p["tokens"] = text * share + images.get(p["id"], 0)
    if total is not None:
        fixed = sum(p["tokens"] for p in parts if p["at"] in ("system", "tools"))
        rest = (
            sum(p["tokens"] for p in parts if p["at"] not in ("system", "tools")) or 1
        )
        scale = (total - fixed) / rest
        for p in parts:
            if p["at"] not in ("system", "tools"):
                p["tokens"] *= scale
    for p in parts:
        p["tokens"] = round(p["tokens"])
    return parts


def label_of(needed: float, helpful: float) -> str:
    """A part's label from the share of runs that marked it needed, and the
    share that marked it helpful."""
    if needed == 1 or (needed > 0 and helpful > 0):
        return "needed"
    if needed + helpful >= 1:
        return "helpful"
    return "one run" if needed + helpful > 0 else "not needed"


def scored(parts: list[tuple[str, int]], runs: list[dict]) -> dict:
    """What the runs' labels make of the context, each as a percentage.
    `parts` are (kind, tokens) in order; each run is {"needed": [ids],
    "helpful": [ids]}.

    `lost_needed` and `lost_helpful` give each run's share of the context
    that a compaction drops and the work ahead needed, and needed or was
    helped by: what it then has to read back, which the share is weighed
    against. An earlier copy of what comes back (`COPIES`) isn't lost,
    since the work finds its text in the later one. `needed` and `helpful` give the same of the whole context, as
    the question asks it. `dropped` is the share of the context a compaction
    drops, and `dropped_needed` and `dropped_helpful` the share of that the
    runs marked needed, and needed or helpful, on average. `labels` gives
    each part's label, and `kinds` each group's size and share needed, and
    needed or helpful, of the context, on average, and whether a compaction
    puts it back."""
    total = sum(tokens for _, tokens in parts) or 1
    marks = [(set(r["needed"]), set(r["helpful"])) for r in runs]
    n = len(runs) or 1
    per_run = [[0, 0, 0, 0] for _ in runs]
    kinds = {g: [0, 0.0, 0.0] for g in ORDER}
    dropped = [0, 0.0, 0.0]
    labels = []
    for i, (kind, tokens) in enumerate(parts):
        need = sum(i in a for a, _ in marks) / n
        helps = sum(i in b and i not in a for a, b in marks) / n
        labels.append(label_of(need, helps))
        gone = kind not in RETURNS
        lost = gone and kind not in COPIES
        for k, (a, b) in enumerate(marks):
            per_run[k][0] += tokens * (i in a)
            per_run[k][1] += tokens * (i in a or i in b)
            per_run[k][2] += tokens * (i in a) * lost
            per_run[k][3] += tokens * (i in a or i in b) * lost
        for acc in (
            kinds[GROUPS.get(kind, "Everything else")],
            *((dropped,) if gone else ()),
        ):
            acc[0] += tokens
            acc[1] += need * tokens
            acc[2] += (need + helps) * tokens

    def pct(v, of=total):
        return round(100 * v / of, 1) if of else 0.0

    returns = {GROUPS[k] for k in RETURNS}
    return {
        "lost_needed": [pct(r[2]) for r in per_run],
        "lost_helpful": [pct(r[3]) for r in per_run],
        "needed": [pct(r[0]) for r in per_run],
        "helpful": [pct(r[1]) for r in per_run],
        "dropped": pct(dropped[0]),
        "dropped_needed": pct(dropped[1], dropped[0]),
        "dropped_helpful": pct(dropped[2], dropped[0]),
        "labels": labels,
        "kinds": {
            g: {
                "size": pct(size),
                "needed": pct(need),
                "helpful": pct(helps),
                "returns": g in returns,
            }
            for g, (size, need, helps) in kinds.items()
        },
    }
