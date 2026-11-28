"""Runs model/bug.py on a synthetic transcript: `uvx pytest tests`. It needs uv."""

import subprocess
from pathlib import Path

from test_report import (
    ROOT,
    boundary,
    compact_summary,
    isolated,
    line,
    prompt,
    request,
    transcript,
)


def test_drafts_sparkjoys_lines_beside_the_requests_around_them(tmp_path):
    projects = transcript(
        tmp_path,
        [
            prompt("10:00:00Z", "fix the parser"),
            request("10:00:10Z", 290_000, 10_000),
            line(
                "10:00:20Z",
                "sparkjoy: Unreadable verdict at 300k tokens, after missing the prompt cache the "
                "turn had just written to (read 30k of 302k): <b>Keep</b>\n\nIt's half done.",
            ),
            line(
                "10:00:20Z",
                "sparkjoy: That looks like a bug: /sparkjoy bug drafts a report of it.",
            ),
            prompt("10:01:00Z", "now test it"),
            request("10:01:10Z", 300_000, 5_000),
            line(
                "10:56:21Z",
                "sparkjoy: Compacting at 305k tokens, idle 55 minutes, after missing the prompt "
                "cache (read 30k of 307k; /sparkjoy report lists the misses): the fix | its test landed",
            ),
            boundary("10:57:00Z", before=305_002),
            compact_summary("10:57:00Z"),
            prompt("12:00:00Z", "next, the lexer"),
            request("12:00:10Z", 30_000, 100_000),
        ],
        session="ab12cd34-0000",
    )
    run = subprocess.run(
        [
            "uv",
            "run",
            "--script",
            "--locked",
            ROOT / "model/bug.py",
            "ab12cd34-0000",
            "--projects",
            projects,
        ],
        capture_output=True,
        text=True,
        env=isolated(tmp_path, TZ="UTC", SPARKJOY_DATA=str(tmp_path / "plugin-data")),
        check=True,
    )
    path, where = run.stdout.splitlines()
    draft = Path(path).read_text()
    assert where == "File it at https://github.com/max-sixty/sparkjoy/issues/new"
    assert draft.startswith(
        "## What happened\n\n_What the user saw and expected, and which of the lines below it "
        "concerns._\n\n## Settings\n\n_What `/sparkjoy` says._\n\n## Evidence\n\n"
    )
    assert (
        ", Claude Code 2.1.330, " in draft
        and "The session ran on claude-opus-5-5, with a one-hour prompt cache." in draft
    )
    assert draft[draft.index("| Time | Line |") :] == (
        """\
| Time | Line | Request before | Request after |
| --- | --- | --- | --- |
| Nov 2 10:00 | sparkjoy: Unreadable verdict at 300k tokens, after missing the prompt cache the turn had just written to (read 30k of 302k): &lt;b&gt;Keep&lt;/b&gt;<br><br>It's half done. | 10 s earlier: 300k, 290k read from the cache | 50 s later: 305k, 300k read from the cache |
| Nov 2 10:00 | sparkjoy: That looks like a bug: /sparkjoy bug drafts a report of it. | 10 s earlier: 300k, 290k read from the cache | 50 s later: 305k, 300k read from the cache |
| Nov 2 10:56 | sparkjoy: Compacting at 305k tokens, idle 55 minutes, after missing the prompt cache (read 30k of 307k; /sparkjoy report lists the misses): the fix \\| its test landed | 55 min earlier: 305k, 300k read from the cache | 1 h 04 min later: 130k, 30k read from the cache |

Compactions since the first of those lines:

| Time | By | Context before |
| --- | --- | --- |
| Nov 2 10:57 | sparkjoy | 305k |

Drafted with `/sparkjoy bug`.
"""
    )
