#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# ///
"""Builds each case's fixture from my Claude Code transcripts.

    uv run --script evals/build.py [case-id ...]

Reads `cases.json`, finds each case's session under `~/.claude/projects/`, and
writes `fixtures/<id>.jsonl.gz`: the conversation Claude Code would resume at
the case's turn end, sanitized for publishing. Only I can rerun it, since
the transcripts are mine; it is here to show what the fixtures hold and
to add cases. `check.py` then sweeps the result.

A fixture is the chain of entries from the turn's last reply back to the last
compaction, the part a resume sends the model, with:

- what the harness injected rather than what the conversation said removed:
  instruction files, memory, skill listings and skill bodies, system
  reminders, and environment and tool-listing attachments;
- the hidden thinking blocks removed, since they are encrypted and so can't
  be checked;
- images replaced by a placeholder;
- home paths, email addresses, credentials, page tokens and private hostnames
  replaced by placeholders, and any tool output naming private work omitted;
- every field but those a resume reads dropped.
"""

import gzip
import json
import re
import sys
from pathlib import Path

from check import private_terms, strings

HERE = Path(__file__).parent
PROJECTS = Path.home() / ".claude/projects"

# Attachments that carry the conversation: what the user typed mid-turn, page
# input a hook delivered, files the user attached or edited, and background
# results. Every other kind is the harness describing itself (instructions,
# skills, tools, environment), which a fresh session supplies anew.
KEPT_ATTACHMENTS = {
    "queued_command",
    "hook_additional_context",
    "edited_text_file",
    "file",
    "compact_file_reference",
    "task_status",
    "async_hook_response",
}

# The fields of an entry a resume reads; the runner adds the session's own.
FIELDS = {
    "type", "uuid", "parentUuid", "logicalParentUuid", "timestamp", "isSidechain",
    "isMeta", "isCompactSummary", "isVisibleInTranscriptOnly", "message",
    "attachment", "subtype", "content", "compactMetadata", "level", "rendered",
}

SYSTEM_REMINDER = re.compile(r"\s*<system-reminder>.*?</system-reminder>\s*", re.S)
OMITTED_SKILL = "[The skill's instructions are omitted from this fixture.]"
OMITTED_COMMAND = "[The command's instructions are omitted from this fixture.]"
OMITTED_OUTPUT = "[This output is omitted from the fixture.]"

SCRUB = [
    (re.compile(r"/Users/[a-z][\w.-]*"), "~"),
    (re.compile(r"-Users-[a-z]\w*(?=[-/])"), "-Users-USER"),
    (re.compile(r"pytest-of-[a-z]\w*"), "pytest-of-USER"),
    (re.compile(r"\b(?!git@github\.com\b)(?!noreply@)[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"), "user@example.com"),
    (re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{16,}"), "<api-key>"),
    (re.compile(r"\b(?:ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}"), "<github-token>"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}"), "Bearer <token>"),
    (re.compile(r"\bop://[^\s\"'`)]+"), "<1password-reference>"),
    (re.compile(r"\b[\w-]+\.tail[0-9a-f]{4,}\.ts\.net\b"), "localhost"),
    (re.compile(r"(?<=://)[\w-]+\.local\b"), "localhost"),
    (re.compile(r"([?&]t=)[A-Za-z0-9_-]{16,}"), r"\1<token>"),
]


def scrub(value):
    """`value` with every string in it passed through SCRUB."""
    if isinstance(value, str):
        for pattern, replacement in SCRUB:
            value = pattern.sub(replacement, value)
        return value
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    return value


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")


def clean_blocks(blocks: list) -> list:
    """User content blocks without system reminders or images."""
    out = []
    for block in blocks:
        kind = block.get("type")
        if kind == "image":
            out.append({"type": "text", "text": "[An image is omitted from this fixture.]"})
        elif kind == "text":
            text = SYSTEM_REMINDER.sub("\n", block["text"]).strip()
            if text:
                out.append(block | {"text": text})
        elif kind == "tool_result":
            inner = block.get("content")
            if isinstance(inner, list):
                inner = clean_blocks(inner)
            elif isinstance(inner, str):
                inner = SYSTEM_REMINDER.sub("\n", inner).strip()
            out.append(block | {"content": inner})
        else:
            out.append(block)
    return out


def chain(path: Path, end: int) -> list[dict]:
    """The entries from the last compaction to line `end`, in order."""
    by_uuid = {}
    with path.open("rb") as f:
        for n, raw in enumerate(f):
            if n > end:
                break
            entry = json.loads(raw)
            if entry.get("uuid") and entry.get("type") in ("user", "assistant", "attachment", "system"):
                by_uuid[entry["uuid"]] = entry
                if n == end:
                    leaf = entry
    entries = []
    entry = leaf
    while entry is not None:
        entries.append(entry)
        if entry.get("type") == "system" and entry.get("subtype") == "compact_boundary":
            break
        entry = by_uuid.get(entry.get("parentUuid"))
    return entries[::-1]


def sanitize(entries: list[dict]) -> list[dict]:
    """The entries a fixture keeps, each cleaned, re-parented past those it drops."""
    out = []
    parent = None
    after_command = False
    for entry in entries:
        kind = entry["type"]
        keep = True
        if kind == "system":
            keep = entry.get("subtype") == "compact_boundary"
        elif kind == "attachment":
            keep = entry["attachment"].get("type") in KEPT_ATTACHMENTS
        elif kind == "assistant":
            blocks = [b for b in entry["message"]["content"] if b.get("type") not in ("thinking", "redacted_thinking")]
            entry = entry | {"message": entry["message"] | {"content": blocks}}
            keep = bool(blocks)
        elif kind == "user":
            content = entry["message"]["content"]
            text = text_of(content)
            if entry.get("isMeta") and text.startswith("Base directory for this skill:"):
                header = text.split("\n", 1)[0]
                content = [{"type": "text", "text": f"{header}\n\n{OMITTED_SKILL}"}]
            elif entry.get("isMeta") and after_command and isinstance(content, list):
                content = [{"type": "text", "text": OMITTED_COMMAND}]
            elif isinstance(content, list):
                content = clean_blocks(content)
            else:
                content = SYSTEM_REMINDER.sub("\n", content).strip()
            entry = entry | {"message": entry["message"] | {"content": content}}
            keep = bool(content)
            after_command = "<command-name>" in text[:300] and "<local-command" not in text
        if not keep:
            continue
        entry = {k: v for k, v in entry.items() if k in FIELDS}
        if entry["type"] != "system":
            entry["parentUuid"] = parent
        parent = entry["uuid"]
        out.append(omit_private(scrub(entry)))
    return out


def omit_private(entry: dict) -> dict:
    """`entry` with any tool output that names private work omitted.

    Words of the conversation itself are left for me to settle by hand: this
    stops the build on one.
    """
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, list):
        for block in content:
            if block.get("type") == "tool_result" and private_terms("\n".join(strings(block.get("content")))):
                block["content"] = OMITTED_OUTPUT
    if hits := private_terms("\n".join(strings(entry))):
        raise SystemExit(f"{entry['uuid']} names private work outside a tool's output: {hits}")
    return entry


def build(case: dict) -> Path:
    session = case["source"]["session"]
    (path,) = PROJECTS.glob(f"*/{session}.jsonl")
    entries = sanitize(chain(path, case["source"]["line"]))
    last = entries[-1]
    assert last["type"] == "assistant" and last["message"].get("stop_reason") == "end_turn", case["id"]
    target = HERE / "fixtures" / f"{case['id']}.jsonl.gz"
    with gzip.open(target, "wt", compresslevel=9) as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return target


def main() -> None:
    cases = json.loads((HERE / "cases.json").read_text())
    wanted = set(sys.argv[1:])
    for case in cases:
        if not wanted or case["id"] in wanted:
            target = build(case)
            print(f"{case['id']}: {target.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
