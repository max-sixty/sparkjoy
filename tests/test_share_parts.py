"""Cuts a made-up request into parts and scores labels on them, as evals/share.py does:
`uvx pytest tests`."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("parts", ROOT / "evals/parts.py")
parts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parts)

REQUEST = {
    "system": [{"type": "text", "text": "You are Claude Code."}],
    "tools": [
        {"name": "Read", "input_schema": {}},
        {"name": "Bash", "input_schema": {}},
    ],
    "messages": [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "This session is being continued from a previous conversation. "
                    * 10,
                },
                {
                    "type": "text",
                    "text": "<system-reminder>\nCodebase and user instructions are shown below.\n"
                    + "x" * 400,
                },
                {"type": "text", "text": "Fix the hover."},
            ],
        },
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Reading the stylesheet."},
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Read",
                    "input": {"file_path": "theme.css"},
                },
                {
                    "type": "tool_use",
                    "id": "t2",
                    "name": "Bash",
                    "input": {"command": "git log -3"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": "a { color: red }\n" * 20,
                },
                {
                    "type": "tool_result",
                    "tool_use_id": "t2",
                    "content": [{"type": "text", "text": "abc123 Fix"}],
                },
            ],
        },
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "The hover is fixed."}],
        },
    ],
}


def test_a_request_is_cut_into_its_blocks_each_with_its_kind():
    cut = parts.parts_of(REQUEST)
    assert [(p["id"], p["kind"], p["block"]) for p in cut] == [
        (0, "system prompt", "system prompt"),
        (1, "tools", "tools"),
        (2, "summary of an earlier compaction", "user text"),
        (3, "instruction files (CLAUDE.md, AGENTS.md)", "user text"),
        (4, "user's message", "user text"),
        (5, "model's text", "assistant text"),
        (6, "model's tool calls", "tool call"),
        (7, "model's tool calls", "tool call"),
        (8, "file reads", "tool result"),
        (9, "other tool output", "tool result"),
        (10, "model's text", "assistant text"),
    ]
    assert cut[6]["desc"] == "Read: theme.css"
    assert cut[9]["desc"] == "result of Bash: git log -3"


def test_a_call_is_described_by_what_it_acts_on_without_the_move_to_its_directory():
    call = {
        "name": "Bash",
        "input": {"command": "cd /w/leaf.composer && sed -n 124,160p browser/menu.css"},
    }
    assert parts.describe_call(call) == "Bash: sed -n 124,160p browser/menu.css"
    assert parts.tool_output_kind(parts.describe_call(call)) == "file reads"


def test_the_system_prompt_and_tools_take_their_count_and_the_messages_share_the_rest():
    cut = parts.sized(parts.parts_of(REQUEST), system=900, tools=1100, total=10_000)
    assert [p["tokens"] for p in cut[:2]] == [900, 1100]
    rest = cut[2:]
    assert abs(sum(p["tokens"] for p in rest) - 8000) <= len(rest)
    # By characters: the file read holds 340 of them, the message 14.
    assert cut[8]["tokens"] > 20 * cut[4]["tokens"]


def test_labels_score_the_context_and_what_a_compaction_drops():
    cut = [
        ("system prompt", 100),
        ("instruction files (CLAUDE.md, AGENTS.md)", 100),
        ("file reads", 400),
        ("other tool output", 300),
        ("user's message", 100),
    ]
    runs = [
        {"needed": [0, 1, 4], "helpful": [2]},
        {"needed": [0, 1], "helpful": [4]},
    ]
    got = parts.scored(cut, runs)
    assert got["labels"] == ["needed", "needed", "one run", "not needed", "needed"]
    assert got["needed"] == [30.0, 20.0]
    assert got["helpful"] == [70.0, 30.0]
    # A compaction keeps the system prompt and instruction files: it drops 800
    # of the 1,000 tokens, of which the runs needed 100 and 0, and needed or
    # helped by 500 and 100.
    assert got["dropped"] == 80.0
    assert got["dropped_needed"] == 6.2
    assert got["dropped_helpful"] == 37.5
    assert got["kinds"]["File reads"] == {"size": 40.0, "needed": 0.0, "helpful": 20.0}
    assert got["kinds"]["System prompt and tools"] == {
        "size": 10.0,
        "needed": 10.0,
        "helpful": 10.0,
    }
