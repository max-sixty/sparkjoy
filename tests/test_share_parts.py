"""Cuts a made-up request into parts, records what the session did next, and scores
labels on them, as evals/share.py does: `uvx pytest tests`. It needs uv."""

import importlib.util
import itertools
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("parts", ROOT / "evals/parts.py")
parts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parts)

INSTRUCTIONS = (
    "<system-reminder>\nCodebase and user instructions are shown below.\n\n"
    "Contents of /home/me/.claude/CLAUDE.md (user's private global instructions):\n\n"
    + "a" * 300
    + "\n\nContents of /w/leaf/AGENTS.md (project instructions):\n\n"
    + "b" * 200
    + "\n</system-reminder>"
)
REREAD = (
    "<system-reminder>\nInstruction files were re-read when this session started; "
    "each replaces its earlier copy:\n\n"
    "Contents of /w/leaf/AGENTS.md (project instructions):\n\n"
    + "c" * 250
    + "\n</system-reminder>"
)
# What Claude Code writes back after a compaction, in one system message.
WRITTEN_BACK = (
    'Called the Read tool with the following input: {"file_path":"/w/leaf/a.css"}\n'
    "Result of calling the Read tool:\n" + "x { }\n" * 30 + "\n"
    "The following skills were invoked EARLIER in this session.\n\n"
    "### Skill: review\nPath: userSettings:review\n\n"
    "Base directory for this skill: /home/me/.claude/skills/review\n\n"
    + "r"
    * 400
    + "\n\n---\n\n"
    "### Skill: writing-prose\nPath: userSettings:writing-prose\n\n"
    "Base directory for this skill: /home/me/.claude/skills/writing-prose\n\n"
    + "w"
    * 300
    + "\n\n"
    "The following skills are available for use with the Skill tool:\n\n- review: Reviews.\n\n"
    "# Environment\nYou have been invoked in the following environment:\n - cwd: /w/leaf\n"
)

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
                {"type": "text", "text": INSTRUCTIONS},
                {"type": "text", "text": "Fix the hover."},
            ],
        },
        {"role": "system", "content": [{"type": "text", "text": WRITTEN_BACK}]},
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "", "signature": "s" * 80},
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
                    "input": {"command": "cd /w/leaf && pnpm build | tail -5"},
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
                    "content": [{"type": "text", "text": "built"}],
                },
                {"type": "text", "text": REREAD},
            ],
        },
        {
            "role": "system",
            "content": [{"type": "text", "text": "Concise output style is active."}],
        },
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "The hover is fixed."}],
        },
    ],
}


def kinds(cut):
    return [(p["id"], p["kind"]) for p in cut]


def test_a_request_is_cut_into_its_blocks_and_the_sources_claude_code_writes_into_one():
    cut = parts.parts_of(REQUEST)
    assert kinds(cut) == [
        (0, "system prompt"),
        (1, "tools"),
        (2, "summary of an earlier compaction"),
        # The first copy of each instruction file; AGENTS.md is read again
        # later, so this copy is dropped.
        (3, "instruction files"),
        (4, "instruction files, an earlier copy"),
        (5, "user's message"),
        # What a compaction wrote back: a file, two skills, the listing and
        # the environment.
        (6, "files re-attached at an earlier compaction"),
        (7, "Claude Code's listings and environment"),
        (8, "skill body"),
        (9, "skill body"),
        (10, "Claude Code's listings and environment"),
        (11, "Claude Code's listings and environment"),
        (12, "thinking"),
        (13, "model's text"),
        (14, "model's tool calls"),
        (15, "model's tool calls"),
        (16, "file reads"),
        # `tail` after a build prints the build, not a file.
        (17, "other tool output"),
        (18, "instruction files"),
        (19, "Claude Code's reminders and notices"),
        (20, "model's text"),
    ]
    assert cut[3]["path"] == "/home/me/.claude/CLAUDE.md"
    assert cut[8]["skill"] == "/home/me/.claude/skills/review"
    assert cut[15]["desc"] == "Bash: pnpm build | tail -5"
    # The sections of one block together hold all of it.
    spans = [p["at"] for p in cut if p["at"][:2] == [1, 0]]
    assert spans[0][2] == 0 and spans[-1][3] == len(WRITTEN_BACK)
    assert all(a[3] == b[2] for a, b in itertools.pairwise(spans))


def test_a_skill_comes_back_cut_and_only_while_the_skills_fit(monkeypatch):
    monkeypatch.setattr(parts, "SKILL_CHARS", 200)
    monkeypatch.setattr(parts, "SKILLS_CHARS", 300)
    cut = parts.parts_of(REQUEST)
    skills = [
        (p["kind"], p.get("skill", "").rsplit("/", 1)[-1], p["chars"])
        for p in cut
        if "skill" in p
    ]
    # review, written back first, is the more recent: its first 200
    # characters come back, and writing-prose's 200 no longer fit.
    assert skills == [
        ("skill body", "review", 200),
        ("skill text that doesn't come back", "review", skills[1][2]),
        ("skill text that doesn't come back", "writing-prose", skills[2][2]),
    ]


def test_blanking_parts_takes_out_their_text_and_leaves_the_rest():
    cut = parts.parts_of(REQUEST)
    ids = [
        p["id"] for p in cut if p["kind"] in ("skill body", "thinking", "file reads")
    ]
    out = parts.blank(REQUEST, cut, ids)
    back = out["messages"][1]["content"][0]["text"]
    assert "rrrr" not in back and "wwww" not in back and "x { }" in back
    assert [b["type"] for b in out["messages"][2]["content"]] == [
        "text",
        "tool_use",
        "tool_use",
    ]
    assert out["messages"][3]["content"][0]["content"] == "."
    assert REQUEST["messages"][1]["content"][0]["text"] == WRITTEN_BACK


def test_a_kind_count_is_shared_by_characters_and_images_count_on_their_own():
    cut = parts.parts_of(REQUEST)
    counts = {p["kind"]: 1000 for p in cut}
    got = parts.sized(cut, counts, images={16: 300})
    calls = [p for p in got if p["kind"] == "model's tool calls"]
    assert sum(p["tokens"] for p in calls) == 1000
    # The file read is the kind's only part: its text takes what its image
    # leaves.
    assert got[16]["tokens"] == 1000
    total = sum(p["tokens"] for p in parts.sized(cut, counts, total=50_000))
    assert abs(total - 50_000) <= len(cut)


def test_labels_score_what_a_compaction_drops_that_the_work_needed():
    cut = [
        ("system prompt", 100),
        ("instruction files", 100),
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
    # Of the whole context, what a compaction drops and the work needed:
    # the user's message in the first run, nothing in the second.
    assert got["lost_needed"] == [10.0, 0.0]
    assert got["lost_helpful"] == [50.0, 10.0]
    assert got["needed"] == [30.0, 20.0]
    assert got["helpful"] == [70.0, 30.0]
    assert got["dropped"] == 80.0
    assert got["dropped_needed"] == 6.2
    assert got["dropped_helpful"] == 37.5
    assert got["kinds"]["File reads"] == {
        "size": 40.0,
        "needed": 0.0,
        "helpful": 20.0,
        "returns": False,
    }
    assert got["kinds"]["System prompt and tools"]["returns"]


def test_an_earlier_copy_of_what_comes_back_is_dropped_but_not_lost():
    cut = [
        ("instruction files, an earlier copy", 300),
        ("instruction files", 300),
        ("file reads", 400),
    ]
    got = parts.scored(cut, [{"needed": [0, 2], "helpful": []}])
    assert got["dropped"] == 70.0
    assert got["lost_needed"] == [40.0]


def test_a_command_reads_a_file_where_a_step_of_it_prints_one():
    def kind(command):
        return parts.tool_output_kind({"name": "Bash", "input": {"command": command}})

    assert kind("cd /w/leaf && sed -n 124,160p browser/menu.css") == "file reads"
    assert kind("wc -l SKILL.md && cat SKILL.md") == "file reads"
    assert kind("P=/w/page && sed -n 1,150p $P/index.html") == "file reads"
    assert kind("pnpm build | tail -5") == "other tool output"
    assert kind("gh pr view 5244 --json body | head -60") == "other tool output"


def test_the_next_work_records_what_the_user_and_other_agents_sent_and_each_call():
    def user(t, content, origin=None, **more):
        entry = {"type": "user", "timestamp": t, "message": {"content": content}}
        return entry | ({"origin": {"kind": origin}} if origin else {}) | more

    def attached(t, attachment):
        return {"type": "attachment", "timestamp": t, "attachment": attachment}

    def compaction(t):
        return {"type": "system", "subtype": "compact_boundary", "timestamp": t}

    # A Leaf delivery as the hook adds it, with an event of the agent's
    # beside the user's and text after it.
    delivery = {
        "format": "leaf-delivery/15",
        "batches": [
            {
                "threads": [{"id": "t1", "messages": []}],
                "events": [
                    {"kind": "comment", "author": "user", "text": "why a box?"},
                    {"kind": "reply", "author": "agent", "text": "It frames it."},
                    {
                        "kind": "action",
                        "author": "user",
                        "detail": {"value": ["drop"]},
                        "says": {"drop": "Drop the box"},
                        "seq": 7,
                    },
                ],
            }
        ],
    }
    entries = [
        # sparkjoy asks for the compaction at the turn end, which the work
        # after it runs without.
        user("2026-11-04T17:16:00Z", "/compact", "plugin"),
        compaction("2026-11-04T17:17:05Z"),
        user("", "summary", isCompactSummary=True),
        user("", "caveat", isMeta=True),
        user("2026-11-04T18:16:00Z", "check the leaf", "human"),
        attached(
            "2026-11-04T18:16:01Z",
            {
                "type": "hook_additional_context",
                "content": [
                    "Leaf has new input for your turn.\n"
                    + json.dumps(delivery)
                    + "\nLeaf's turn-end check found nothing."
                ],
            },
        ),
        attached(
            "2026-11-04T18:16:02Z",
            {"type": "hook_additional_context", "content": ["Leaf: your status"]},
        ),
        {
            "type": "assistant",
            "timestamp": "2026-11-04T18:16:30Z",
            "message": {
                "content": [
                    {"type": "text", "text": "Reading it."},
                    {
                        "type": "tool_use",
                        "id": "a",
                        "name": "Read",
                        "input": {"file_path": "/w/SKILL.md"},
                    },
                ]
            },
        },
        {
            "type": "assistant",
            "isSidechain": True,
            "message": {"content": [{"type": "text", "text": "aside"}]},
        },
        attached(
            "2026-11-04T18:16:40Z",
            {
                "type": "queued_command",
                "prompt": "and the tests",
                "origin": {"kind": "human"},
            },
        ),
        attached(
            "2026-11-04T18:16:41Z",
            {
                "type": "queued_command",
                "prompt": "CI passed",
                "isMeta": True,
                "origin": {"kind": "peer"},
            },
        ),
        user(
            "2026-11-04T18:17:00Z",
            [
                {
                    "type": "tool_result",
                    "tool_use_id": "a",
                    "is_error": True,
                    "content": [{"type": "text", "text": "no such file"}],
                },
                {"type": "text", "text": "<system-reminder>x</system-reminder>"},
            ],
        ),
        user("2026-11-04T18:18:00Z", "<task-notification>", "task-notification"),
        # Older releases log another session's message with no origin.
        user("2026-11-04T18:18:30Z", "Another Claude session sent a message: done"),
        user(
            "2026-11-04T18:19:00Z",
            "<command-message>merge-pr</command-message>",
            "human",
        ),
        # A compaction later in the work doesn't end it.
        compaction("2026-11-04T19:00:00Z"),
        user("2026-11-04T19:01:00Z", "after", "human"),
    ]
    assert parts.next_work(iter(entries)) == [
        {"t": "2026-11-04T17:17", "kind": "compaction"},
        {"t": "2026-11-04T18:16", "kind": "user", "text": "check the leaf"},
        {
            "t": "2026-11-04T18:16",
            "kind": "page",
            "event": "comment",
            "text": "why a box?",
        },
        {
            "t": "2026-11-04T18:16",
            "kind": "page",
            "event": "action",
            "detail": {"value": ["drop"]},
            "says": {"drop": "Drop the box"},
        },
        {"t": "2026-11-04T18:16", "kind": "assistant", "text": "Reading it."},
        {
            "t": "2026-11-04T18:16",
            "kind": "call",
            "tool": "Read",
            "input": {"file_path": "/w/SKILL.md"},
        },
        {"t": "2026-11-04T18:16", "kind": "mid-turn", "text": "and the tests"},
        {"t": "2026-11-04T18:16", "kind": "agent", "text": "CI passed"},
        {
            "t": "2026-11-04T18:17",
            "kind": "result",
            "tool": "Read",
            "error": True,
            "text": "no such file",
        },
        {"t": "2026-11-04T18:18", "kind": "agent", "text": "<task-notification>"},
        {
            "t": "2026-11-04T18:18",
            "kind": "agent",
            "text": "Another Claude session sent a message: done",
        },
        {
            "t": "2026-11-04T18:19",
            "kind": "user",
            "text": "<command-message>merge-pr</command-message>",
        },
        {"t": "2026-11-04T19:00", "kind": "compaction"},
        {"t": "2026-11-04T19:01", "kind": "user", "text": "after"},
    ]


def test_the_next_work_runs_through_compactions_until_its_context_would_pass_the_window(
    tmp_path,
):
    def at(t):
        return f"2026-11-04T{t}Z"

    def request(t, size, write, text):
        """A main-thread request with `size` tokens of context, `write` of them
        written to the cache."""
        usage = {
            "input_tokens": 0,
            "cache_read_input_tokens": size - write,
            "cache_creation_input_tokens": write,
            "output_tokens": 10,
        }
        return {
            "type": "assistant",
            "timestamp": at(t),
            "isSidechain": False,
            "entrypoint": "cli",
            "message": {
                "model": "claude-opus-5-5",
                "id": f"msg_{t}",
                "usage": usage,
                "content": [{"type": "text", "text": text}],
            },
        }

    def prompt(t, text, origin="human"):
        return {
            "type": "user",
            "timestamp": at(t),
            "isSidechain": False,
            "origin": {"kind": origin},
            "message": {"role": "user", "content": text},
        }

    entries = [
        prompt("09:00:00", "start"),
        request("09:00:10", 860_000, 860_000, "Started."),
        prompt("10:00:00", "build it"),
        request("10:00:10", 900_000, 40_000, "Built."),  # the turn end, at 900k
        prompt("10:05:00", "now test it"),
        request("10:05:10", 930_000, 30_000, "Tested."),  # 930k
        prompt("10:06:00", "/compact", "plugin"),
        {"type": "system", "subtype": "compact_boundary", "timestamp": at("10:07:00")},
        {
            "type": "user",
            "timestamp": at("10:07:00"),
            "isSidechain": False,
            "isCompactSummary": True,
            "message": {"role": "user", "content": "summary"},
        },
        prompt("10:08:00", "and the docs"),
        # The compacted prompt hides what the request added: 4k stands in.
        request("10:08:10", 50_000, 45_000, "Writing them."),  # 934k
        request("10:09:10", 110_000, 60_000, "Written."),  # 994k
        # The prompt of the request that passes the window goes with it.
        prompt("10:10:00", "one more"),
        request("10:10:10", 120_000, 10_000, "Past the window."),  # 1,004k
        prompt("10:11:00", "later still"),
    ]
    transcript = tmp_path / "projects" / "project" / "s.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        "".join(json.dumps(e, separators=(",", ":")) + "\n" for e in entries)
    )
    case = {"id": "c", "tokens": 900_000, "source": {"session": "s", "line": 3}}
    code = (
        "import json, sys; sys.path.insert(0, 'evals'); import share; "
        "print(json.dumps(share.next_work(json.loads(sys.argv[1]))))"
    )
    run = subprocess.run(
        [
            *("uv", "run", "--no-project", "--with-requirements", "evals/share.py"),
            *("python", "-c", code, json.dumps(case)),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={
            "PATH": os.environ["PATH"],
            "TMPDIR": str(tmp_path),
            "CLAUDE_CONFIG_DIR": str(tmp_path),
            "SPARKJOY_DATA": str(tmp_path / "data"),
        },
        check=True,
    )
    assert json.loads(run.stdout) == [
        {"t": "2026-11-04T10:05", "kind": "user", "text": "now test it"},
        {"t": "2026-11-04T10:05", "kind": "assistant", "text": "Tested."},
        {"t": "2026-11-04T10:07", "kind": "compaction"},
        {"t": "2026-11-04T10:08", "kind": "user", "text": "and the docs"},
        {"t": "2026-11-04T10:08", "kind": "assistant", "text": "Writing them."},
        {"t": "2026-11-04T10:09", "kind": "assistant", "text": "Written."},
    ]


def test_parts_that_hold_the_same_share_a_line_and_a_mark_on_it():
    def call(command):
        return {
            "type": "tool_use",
            "id": "x",
            "name": "Bash",
            "input": {"command": command},
        }

    def edit(old):
        given = {"file_path": "/w/x.css", "old_string": old, "new_string": ""}
        return {"type": "tool_use", "id": "y", "name": "Edit", "input": given}

    def result(text):
        return {"type": "tool_result", "tool_use_id": "x", "content": text}

    # An idle wait polled three times, the first two polls alike, and an
    # edit of one file twice, which reads the same but holds different text.
    request = {
        "system": [{"type": "text", "text": "You are Claude Code."}],
        "tools": [],
        "messages": [
            {"role": "assistant", "content": [call("leaf wait /p")]},
            {"role": "user", "content": [result("nothing new")]},
            {"role": "assistant", "content": [call("leaf wait /p")]},
            {"role": "user", "content": [result("nothing new")]},
            {"role": "assistant", "content": [call("leaf wait /p")]},
            {"role": "user", "content": [result("a comment")]},
            {"role": "assistant", "content": [edit("a {}")]},
            {"role": "assistant", "content": [edit("b {}")]},
        ],
    }
    cut = parts.parts_of(request)
    for p in cut:
        p["tokens"] = 10
    assert parts.listing(cut).splitlines() == [
        "#0 system prompt, 10 tokens: system prompt",
        "#1 tools, 10 tokens: 0 tool definitions",
        "#2, #4, #6 tool call ×3, 30 tokens: Bash: leaf wait /p",
        "#3, #5 tool result ×2, 20 tokens: result of Bash: leaf wait /p",
        "#7 tool result, 10 tokens: result of Bash: leaf wait /p",
        "#8 tool call, 10 tokens: Edit: /w/x.css",
        "#9 tool call, 10 tokens: Edit: /w/x.css",
    ]
    # Any one id of a line marks every part on it, and no other.
    assert parts.marked([5, 0], cut) == [0, 3, 5]
    assert parts.marked([99], cut) == []


def test_records_past_what_the_window_holds_are_counted_in_one_record():
    records = [
        {"t": "2026-11-08T18:28", "kind": "user", "text": "u" * 100},
        {
            "t": "2026-11-08T18:28",
            "kind": "call",
            "tool": "Read",
            "input": {"file_path": "/w/SKILL.md"},
        },
        {"t": "2026-11-08T18:29", "kind": "result", "tool": "Read", "text": "r" * 5000},
        {"t": "2026-11-08T18:29", "kind": "assistant", "text": "a" * 100},
        {"t": "2026-11-08T19:00", "kind": "compaction"},
    ]
    whole = len(parts.rendered(records))
    assert parts.omitted(records, whole, 5000) == records
    # Measured with each record cut to the limit, the long result takes no
    # more room than the others, so the records up to and with the one that
    # crosses the length stay, whole, and one record counts the rest.
    each = [len(parts.rendered([parts.capped(r, 100)])) + 1 for r in records]
    got = parts.omitted(records, sum(each[:3]) + 10, 100)
    assert got == [*records[:4], {"kind": "omitted", "records": 1}]
    assert parts.omitted(records, sum(each[:3]) + 10, 5000)[-1] == {
        "kind": "omitted",
        "records": 2,
    }
    for line in parts.rendered(got).splitlines():
        json.loads(line)


def test_shortening_cuts_long_values_and_keeps_every_record_whole_json():
    records = [
        {"t": "2026-11-04T18:16", "kind": "user", "text": "short"},
        {
            "t": "2026-11-04T18:16",
            "kind": "call",
            "tool": "Edit",
            "input": {
                "file_path": "/w/a.css",
                "old_string": "o" * 400,
                "new_string": "n" * 600,
                "replace_all": False,
            },
        },
        {
            "t": "2026-11-04T18:17",
            "kind": "result",
            "tool": "Edit",
            "text": "head\n" + "r" * 2000 + "\ntail",
        },
        {
            "t": "2026-11-04T18:18",
            "kind": "page",
            "event": "action",
            "detail": {"value": ["drop"]},
            "says": {"drop": "Drop the box " + "s" * 1500},
        },
    ]
    assert parts.shortened(records, 10_000) == records
    got = parts.shortened(records, 400)
    assert got[0] == records[0]
    for before, after in zip(records, got, strict=True):
        assert json.loads(parts.rendered([after])) == after
        assert {k: v for k, v in after.items() if k not in parts.CONTENT} == {
            k: v for k, v in before.items() if k not in parts.CONTENT
        }
    # Within a record the longest values are cut first, each to its head and tail.
    call = got[1]["input"]
    assert call["file_path"] == "/w/a.css" and call["replace_all"] is False
    assert call["old_string"].startswith("o") and parts.CUT in call["old_string"]
    assert got[2]["text"].startswith("head") and got[2]["text"].endswith("tail")
    assert got[3]["detail"] == {"value": ["drop"]}
    says = got[3]["says"]["drop"]
    assert says.startswith("Drop the box") and parts.CUT in says
    assert sum(map(parts.length, got)) <= 400 + 4 * len(parts.CUT)
    # Fewer characters never give longer records.
    assert sum(map(parts.length, parts.shortened(records, 200))) < sum(
        map(parts.length, got)
    )
