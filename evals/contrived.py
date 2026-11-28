"""Turn ends made up for `share.py`, whose share of the context the work ahead
needs is known by construction. Each keeps a real case's system prompt, tools
and settings (`BASE`'s) and replaces its messages:

- skills-start: the user asks for a Leaf page; the model loads the Leaf skill
  and four of its references, and says it will now draft the page. All of
  the context counts.
- skills-done: the same, then the page is built and the user signs off to
  work on their tax return. Only the system prompt, the tools and the last
  exchange count.
- log-fixed: the user asks why CI failed; the model reads a 150k-character
  test log, finds the one failure, reads and fixes the short module it
  points to, and says it will push and watch CI. The log is spent; the module and the fix
  are what the work needs.

`labels` marks each case's parts as its construction says, and `should`
turns them into the ranges its share should fall in, from the parts' tokens
as `share.py` counts them: on what a compaction drops that the work needs
(the skill and the system prompt and tools come back after one, so at
skills-start that is the references and the exchange), and on all the
context, as the question asks (where the system prompt and tools always
count, about half of skills-done's).

`share.py --build` makes each request, reading the Leaf skill from the
installed plugin, and leaves it to publish, so every run sends the same one.
"""

import json
from pathlib import Path

BASE = "leaf-data-attrs-merged"
READS = [
    "references/page-authoring.md",
    "references/authoring-evidence.md",
    "references/threads.md",
    "references/authoring-revisions.md",
]

CASES = {
    "skills-start": {
        "what": "Just loaded the Leaf skill and four of its references; about to draft the page",
    },
    "skills-done": {
        "what": "The same, then the page built and the user off to their tax return",
    },
    "log-fixed": {
        "what": "A CI log read and its one failure fixed; next, push and watch CI",
    },
}
# How far a share may sit from what the construction gives, in points.
SLACK = 5


def labels(case: str, cut: list[dict]) -> dict:
    """What the work ahead needs and is helped by at each made-up turn end,
    as `share.py`'s labeller would mark it, by part id (`parts.parts_of`):

    - skills-start: everything, since the page is drafted from the skill and
      its references, as the user asked;
    - skills-done: the system prompt, the tools and the last exchange, about
      the tax return, which needs none of the page's work;
    - log-fixed: the system prompt, the tools, the module as read, its fix,
      and the reply that holds the fixed module, which pushing and watching CI
      need; the rest of the exchange helps, and the log is spent."""
    ids = [p["id"] for p in cut]
    always = [p["id"] for p in cut if p["kind"] in ("system prompt", "tools")]
    if case == "skills-start":
        return {"needed": ids, "helpful": []}
    if case == "skills-done":
        return {"needed": [*always, *ids[-2:]], "helpful": []}
    fix = [
        p["id"]
        for p in cut
        if p["desc"].startswith(
            ("result of Read: src/export/dates.py:", "Edit: src/export/dates.py ")
        )
    ]
    log = [p["id"] for p in cut if p["desc"].startswith("result of Bash: gh run view")]
    needed = [*always, *fix, ids[-1]]
    return {
        "needed": needed,
        "helpful": [i for i in ids if i not in needed and i not in log],
    }


def should(got: dict) -> tuple[tuple[int, int], tuple[int, int]]:
    """The range a made-up case's share should fall in, from `parts.scored`
    of its construction's labels: on what a compaction drops that the work
    needs, from needed to needed or helpful, and on all the context, as the
    question asks, each `SLACK` points wider."""

    def band(lo: float, hi: float) -> tuple[int, int]:
        return max(0, round(lo) - SLACK), min(100, round(hi) + SLACK)

    return (
        band(got["lost_needed"][0], got["lost_helpful"][0]),
        band(got["needed"][0], got["helpful"][0]),
    )


def skill_dir() -> Path:
    """The installed Leaf plugin's skill."""
    installed = json.loads(
        (Path.home() / ".claude/plugins/installed_plugins.json").read_text()
    )
    return Path(installed["plugins"]["leaf@leaf"][0]["installPath"]) / "skills/leaf"


def call(i, name, given):
    return {"type": "tool_use", "id": f"toolu_{i:02d}", "name": name, "input": given}


def result(i, text):
    return {"type": "tool_result", "tool_use_id": f"toolu_{i:02d}", "content": text}


def skills_turns(skill: Path) -> list[dict]:
    ask = {
        "role": "user",
        "content": "Build a Leaf page that compares the two compaction prompts' results on the sparkjoy evals, from .tmp/probe/pagedata10.json and pagedata11.json.",
    }
    calls = [call(1, "Skill", {"skill": "leaf:leaf"})] + [
        call(2 + k, "Read", {"file_path": str(skill / r)}) for k, r in enumerate(READS)
    ]
    results = [
        result(
            1,
            "Base directory for this skill: "
            + str(skill)
            + "\n\n"
            + (skill / "SKILL.md").read_text(),
        )
    ]
    results += [result(2 + k, (skill / r).read_text()) for k, r in enumerate(READS)]
    return [
        ask,
        {
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": "Loading the Leaf skill and the references for authoring a page.",
                },
                *calls,
            ],
        },
        {"role": "user", "content": results},
    ]


MODULE = '''"""Parse the dates in an export's rows."""

from datetime import datetime, timezone


def parse(value: str) -> datetime:
    """A row's timestamp, in UTC."""
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def rows(lines):
    for line in lines:
        name, when, amount = line.split(",")
        yield name, parse(when), float(amount)
'''
NAIVE = 'return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")'
AWARE = (
    'return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)'
)


def log() -> str:
    lines = [
        "============================= test session starts =============================",
        "platform linux -- Python 3.12.7, pytest-8.3.3, pluggy-1.5.0",
        "collected 2412 items",
        "",
    ]
    for k in range(2400):
        mod = ["test_export", "test_rows", "test_ledger", "test_report", "test_cli"][
            k % 5
        ]
        lines.append(
            f"tests/{mod}.py::test_case_{k:04d} PASSED                                     [{k * 100 // 2412:3d}%]"
        )
        if k == 1717:
            lines.append(
                "tests/test_rows.py::test_parse_is_utc FAILED                                [ 71%]"
            )
    lines += [
        "",
        "=================================== FAILURES ===================================",
        "______________________________ test_parse_is_utc _______________________________",
        "    def test_parse_is_utc():",
        '>       assert parse("2026-03-01 12:00:00").tzinfo is timezone.utc',
        "E       AssertionError: assert None is datetime.timezone.utc",
        "E        +  where None = datetime.datetime(2026, 3, 1, 12, 0).tzinfo",
        "src/export/dates.py:8: AssertionError",
        "=========================== short test summary info ============================",
        "FAILED tests/test_rows.py::test_parse_is_utc - AssertionError: assert None is datetime.timezone.utc",
        "================= 1 failed, 2411 passed in 214.07s (0:03:34) =================",
    ]
    return "\n".join(lines)


def log_turns() -> list[dict]:
    fixed = MODULE.replace(NAIVE, AWARE)
    return [
        {
            "role": "user",
            "content": "CI failed on the export PR. Find out why and fix it.",
        },
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Reading the failed job's log."},
                call(1, "Bash", {"command": "gh run view 9182736455 --log-failed"}),
            ],
        },
        {"role": "user", "content": [result(1, log())]},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": "One test fails: `parse` returns a naive datetime. Reading the module.",
                },
                call(2, "Read", {"file_path": "src/export/dates.py"}),
            ],
        },
        {"role": "user", "content": [result(2, MODULE)]},
        {
            "role": "assistant",
            "content": [
                call(
                    3,
                    "Edit",
                    {
                        "file_path": "src/export/dates.py",
                        "old_string": NAIVE,
                        "new_string": AWARE,
                    },
                )
            ],
        },
        {
            "role": "user",
            "content": [
                result(3, "The file src/export/dates.py has been updated successfully.")
            ],
        },
        {
            "role": "assistant",
            "content": [
                call(4, "Bash", {"command": "uv run pytest tests/test_rows.py -q"})
            ],
        },
        {
            "role": "user",
            "content": [
                result(
                    4,
                    "........................................ [100%]\n412 passed in 31.20s",
                )
            ],
        },
        {
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": "Fixed: `parse` now marks its timestamps as UTC, which `test_parse_is_utc` expects; the 412 row tests pass. Next I'll push the fix and watch CI.\n\nThe module now reads:\n\n```python\n"
                    + fixed
                    + "```",
                }
            ],
        },
    ]


def messages(case: str, skill: Path) -> list[dict]:
    """The case's messages, ending with the turn's reply."""
    if case == "log-fixed":
        return log_turns()
    turns = skills_turns(skill)
    if case == "skills-start":
        return [
            *turns,
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "I've read the Leaf skill and its authoring references. Next I'll draft the page: a table of the two prompts' results by circumstance, and a chart of the share against the context's size, following the page-authoring rules for tables and charts.",
                    }
                ],
            },
        ]
    return [
        *turns,
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Drafting the page."},
                call(
                    10,
                    "Write",
                    {
                        "file_path": "/Users/me/.local/state/leaf/pages/prompts/index.html",
                        "content": "<!doctype html>…",
                    },
                ),
            ],
        },
        {"role": "user", "content": [result(10, "File created successfully.")]},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": "The page is up: http://127.0.0.1:64827/?t=abc",
                }
            ],
        },
        {
            "role": "user",
            "content": "Perfect, that's everything I needed for sparkjoy. Next I want to work on my tax return, which has nothing to do with this.",
        },
        {
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": "Glad it helps. Tell me where the tax documents are and what you need from them.",
                }
            ],
        },
    ]


def request(case: str, base: dict, skill: Path) -> dict:
    """The case's request: `base`'s settings, system prompt and tools, and the
    case's messages."""
    return base | {"messages": messages(case, skill)}
