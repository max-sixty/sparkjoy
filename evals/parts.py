"""Cuts a case's request into parts and scores them by what the work after the
turn end used, for `share.py`.

A part is one block of the request: the system prompt, the tool definitions,
or one block of a message (a text, a tool call, a tool's result, an image).
`kind_of` names what each part holds, from how its text opens, as Claude Code
writes it: the instruction files, a skill's body, what an earlier compaction
carried, a file read, the model's own text. `sized` gives each part its
tokens: the system prompt and the tools as counted, and each message's part
its share by characters of what the messages hold, which is close enough to
weigh kinds but not exact for one part.

A labeller marks each part needed, helpful or neither, in several runs
(`share.py`), and `scored` turns the runs into shares of the context: what
the work needed, what it needed or was helped by, and the same of what a
compaction drops, which leaves out what Claude Code puts back after one
(`RETURNS`).
"""

import json
import re

# The kinds a compaction leaves in the context: the system prompt and tools,
# read from the cache, and the instruction files, which Claude Code writes
# again after the summary. The curve prices them in K, the context after a
# compaction, so the work never has to read them back.
RETURNS = {
    "system prompt",
    "tools",
    "instruction files (CLAUDE.md, AGENTS.md)",
    "instruction files re-read",
}
# The kinds merged for the report, in the order a reader meets them.
GROUPS = {
    "system prompt": "System prompt and tools",
    "tools": "System prompt and tools",
    "instruction files (CLAUDE.md, AGENTS.md)": "Instruction files, with re-reads",
    "instruction files re-read": "Instruction files, with re-reads",
    "files re-attached at an earlier compaction": "What an earlier compaction carried",
    "summary of an earlier compaction": "What an earlier compaction carried",
    "file reads": "File reads",
    "other tool output": "Other tool output",
    "model's tool calls": "The model's tool calls",
    "model's text": "The model's text",
    "user's message": "The user's messages",
}
ORDER = [*dict.fromkeys(GROUPS.values()), "Everything else"]
# A part's label across the runs, strongest first: needed in every run, or
# needed in one and helpful in another; needed or helpful in every run;
# needed or helpful in one run only; in none.
LABELS = ("needed", "helpful", "one run", "not needed")
# How a text block opens, for the kinds Claude Code writes into the messages.
OPENINGS = [
    (r"^This session is being continued", "summary of an earlier compaction"),
    (
        r"^(Called the Read tool|Note: .* was read before the last conversation|Result of calling the Read tool)",
        "files re-attached at an earlier compaction",
    ),
    (r"^Base directory for this skill", "skill body"),
    (
        r"Codebase and user instructions are shown below",
        "instruction files (CLAUDE.md, AGENTS.md)",
    ),
    (r"Instruction files were re-read", "instruction files re-read"),
    (r"leaf-delivery|Leaf has new input|Leaf needs attention", "Leaf page input"),
    (r"^\[SYSTEM NOTIFICATION|^<task-notification>", "task notifications"),
    (r"^<system-reminder>|^<command-|^<local-command", "other reminders"),
]
# A command's opening move to a directory: `cd <dir> && ` or `cd <dir>; `.
CD = re.compile(r"""^cd\s+("[^"]*"|'[^']*'|\S+)\s*(&&|;)\s*""")
# An image's characters, at about the messages' characters per token, so that
# it weighs about the 1.6k tokens the API counts for one.
IMAGE_CHARS = 1600 * 2.6


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


def parts_of(request: dict) -> list[dict]:
    """The request's parts in order, each with its `id`, `kind`, `chars`, the
    kind of `block` it is, a line describing it and, for text, its opening
    (`head`)."""
    parts = [
        {
            "kind": "system prompt",
            "block": "system prompt",
            "desc": "system prompt",
            "chars": sum(len(b.get("text", "")) for b in request["system"]),
        },
        {
            "kind": "tools",
            "block": "tools",
            "desc": f"{len(request['tools'])} tool definitions",
            "chars": len(json.dumps(request["tools"])),
        },
    ]
    calls = {}
    for m in request["messages"]:
        content = m["content"]
        blocks = (
            content
            if isinstance(content, list)
            else [{"type": "text", "text": content}]
        )
        for b in blocks:
            t = b.get("type")
            if t == "tool_use":
                calls[b["id"]] = describe_call(b)
                part = {
                    "kind": "model's tool calls",
                    "block": "tool call",
                    "desc": calls[b["id"]],
                    "chars": len(json.dumps(b.get("input"))),
                }
            elif t == "tool_result":
                body = text_of(b.get("content"))
                call = calls.get(b.get("tool_use_id"), "?")
                part = {
                    "kind": tool_output_kind(call),
                    "block": "tool result",
                    "desc": f"result of {call}",
                    "chars": len(body),
                    "head": body[:300],
                }
            elif t == "text":
                body = b.get("text") or ""
                part = {
                    "kind": text_kind(body, m["role"]),
                    "block": f"{m['role']} text",
                    "desc": body[:160].replace("\n", " "),
                    "chars": len(body),
                    "head": body[:300],
                }
            elif t in ("thinking", "redacted_thinking"):
                part = {
                    "kind": "thinking",
                    "desc": "thinking",
                    "chars": len(b.get("thinking", "")),
                }
            elif t == "image":
                part = {"kind": "image", "desc": "image", "chars": IMAGE_CHARS}
            else:
                part = {"kind": t or "?", "desc": t or "?", "chars": len(json.dumps(b))}
            part.setdefault("block", part["kind"])
            parts.append(part)
    for i, part in enumerate(parts):
        part["id"] = i
    return parts


def text_kind(text: str, role: str) -> str:
    """What a text block holds, from how it opens."""
    head = text.lstrip()[:400]
    for pattern, kind in OPENINGS:
        if re.search(pattern, head):
            return kind
    return "user's message" if role == "user" else "model's text"


def tool_output_kind(call: str) -> str:
    """What a tool's result holds, from the call it answers."""
    if call.startswith("Read:") or re.match(
        r"Bash: .*\b(cat|sed -n|head|tail|nl)\b", call
    ):
        return "file reads"
    if call.startswith("Skill:"):
        return "skill body"
    if call.startswith("Agent:"):
        return "subagent reports"
    return "other tool output"


def sized(parts: list[dict], system: int, tools: int, total: int) -> list[dict]:
    """Give each part its `tokens`: the system prompt and tools as counted,
    and the messages' parts `total - system - tools` between them by
    characters."""
    rest = [p for p in parts if p["kind"] not in ("system prompt", "tools")]
    chars = sum(p["chars"] for p in rest) or 1
    for p in parts:
        p["tokens"] = (
            system
            if p["kind"] == "system prompt"
            else tools
            if p["kind"] == "tools"
            else round(p["chars"] * (total - system - tools) / chars)
        )
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

    `needed` and `helpful` give each run's share of the context it marked
    needed, and needed or helpful. `dropped` is the share of the context a
    compaction drops, and `dropped_needed` and `dropped_helpful` the share of
    that the runs marked needed, and needed or helpful, on average. `labels`
    gives each part's label, and `kinds` each group's size and share needed,
    and needed or helpful, of the context, on average."""
    total = sum(tokens for _, tokens in parts) or 1
    marks = [(set(r["needed"]), set(r["helpful"])) for r in runs]
    n = len(runs)
    per_run = [[0, 0] for _ in runs]
    kinds = {g: [0, 0.0, 0.0] for g in ORDER}
    dropped = [0, 0.0, 0.0]
    labels = []
    for i, (kind, tokens) in enumerate(parts):
        need = sum(i in a for a, _ in marks) / n
        helps = sum(i in b and i not in a for a, b in marks) / n
        labels.append(label_of(need, helps))
        for k, (a, b) in enumerate(marks):
            per_run[k][0] += tokens * (i in a)
            per_run[k][1] += tokens * (i in a or i in b)
        for acc in (
            kinds[GROUPS.get(kind, "Everything else")],
            *(() if kind in RETURNS else (dropped,)),
        ):
            acc[0] += tokens
            acc[1] += need * tokens
            acc[2] += (need + helps) * tokens

    def pct(v, of=total):
        return round(100 * v / of, 1) if of else 0.0

    return {
        "needed": [pct(a) for a, _ in per_run],
        "helpful": [pct(b) for _, b in per_run],
        "dropped": pct(dropped[0]),
        "dropped_needed": pct(dropped[1], dropped[0]),
        "dropped_helpful": pct(dropped[2], dropped[0]),
        "labels": labels,
        "kinds": {
            g: {"size": pct(size), "needed": pct(need), "helpful": pct(helps)}
            for g, (size, need, helps) in kinds.items()
        },
    }
