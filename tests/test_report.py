"""Runs model/report.py on synthetic transcripts: `uvx pytest tests`. It needs uv."""

import json
import os
import subprocess
from itertools import count
from pathlib import Path

ROOT = Path(__file__).parents[1]
DAY = "2026-11-02T"
ids = count()


def request(time, read, write, model="claude-opus-5-5", text="Done.", output=500):
    """A main-thread request that ends its turn, with `read + write` tokens of context."""
    usage = {
        "input_tokens": 2,
        "cache_read_input_tokens": read,
        "cache_creation_input_tokens": write,
        "cache_creation": {"ephemeral_1h_input_tokens": write},
        "output_tokens": output,
    }
    return {
        "type": "assistant",
        "timestamp": DAY + time,
        "isSidechain": False,
        "entrypoint": "cli",
        "version": "2.1.330",
        "message": {
            "model": model,
            "id": f"msg_{next(ids)}",
            "usage": usage,
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": text}],
        },
    }


def prompt(time, text):
    return {
        "type": "user",
        "timestamp": DAY + time,
        "isSidechain": False,
        "message": {"role": "user", "content": text},
    }


def compact_summary(time):
    """The summary Claude Code writes after a compaction: 1,000 tokens at 4 characters each."""
    return {
        "type": "user",
        "timestamp": DAY + time,
        "isSidechain": False,
        "isCompactSummary": True,
        "message": {"role": "user", "content": "x" * 4_000},
    }


def line(time, text):
    """A dim line in the transcript, as a mod's `$.ui.log` writes it."""
    return {
        "type": "system",
        "subtype": "informational",
        "timestamp": DAY + time,
        "isSidechain": False,
        "content": text,
    }


def boundary(time, trigger="manual", before=None):
    """A compaction's boundary, with the context it compacted, `before`, where given."""
    metadata = {"trigger": trigger, "durationMs": 60_000}
    return {
        "type": "system",
        "subtype": "compact_boundary",
        "timestamp": DAY + time,
        "isSidechain": False,
        "compactMetadata": metadata | ({"preTokens": before} if before else {}),
    }


def transcript(tmp_path, entries, session="session"):
    """Writes `entries` as Claude Code writes the transcript of the session with the id `session`,
    under the projects directory in `tmp_path`, and returns that directory."""
    path = tmp_path / "projects" / "project" / f"{session}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(e, separators=(",", ":")) + "\n" for e in entries)
    )
    return tmp_path / "projects"


def report(tmp_path, entries, *args):
    projects = transcript(tmp_path, entries)
    run = subprocess.run(
        [
            "uv",
            "run",
            "--script",
            "--locked",
            ROOT / "model/report.py",
            "--projects",
            projects,
            "--since",
            "2026-11-01T00:00Z",
            "--until",
            "2026-11-03T00:00Z",
            "--no-open",
            *args,
        ],
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], "TMPDIR": str(tmp_path)},
        check=True,
    )
    *summary, page = run.stdout.splitlines()
    return summary, Path(page).read_text()


def session_with_compactions():
    """A session with a verdict of each kind but two, compactions by sparkjoy, by its old name and by
    the user, and three returns after an hour or more idle."""
    return [
        {"type": "ai-title", "aiTitle": "Fix the parser", "sessionId": "s"},
        prompt("10:00:00Z", "fix the parser"),
        request("10:00:10Z", 290_000, 10_000),
        line("10:00:20Z", "sparkjoy: Kept at 300k tokens: the fix is half done"),
        line("10:00:30Z", "sparkjoy: hooks.json changed — reloaded"),
        prompt("10:01:00Z", "now test it"),
        # A reply the summary's size, which prices the compaction request's thinking.
        request("10:01:10Z", 300_000, 150_000, text="y" * 4_000, output=1_500),
        line(
            "10:01:20Z", "compact-when-done: Compacting at 450k tokens: the fix landed"
        ),
        boundary("10:02:00Z"),
        compact_summary("10:02:00Z"),
        prompt("10:05:00Z", "next, the lexer"),
        request("10:05:10Z", 30_000, 210_000),
        line("10:05:20Z", "sparkjoy: No verdict at 240k tokens: empty-reply"),
        prompt("12:30:00Z", "back again"),  # after two and a half hours, at 240k
        request("12:30:10Z", 30_000, 230_000),
        line(
            "13:25:20Z",
            "sparkjoy: Compacting at 260k tokens, idle 55 minutes, after missing the prompt cache "
            "(read 30k of 262k): the lexer is done",
        ),
        boundary("13:26:00Z"),
        compact_summary("13:26:00Z"),
        prompt("15:00:00Z", "one more thing"),  # after sparkjoy's compaction
        request("15:00:10Z", 30_000, 195_000),
        line("15:00:20Z", "sparkjoy: Failed: Error: the fork failed"),
        line(
            "15:00:40Z",
            "sparkjoy: Compacting at 226k tokens, after missing the prompt cache its turn had just "
            "written to (read 30k of 227k): all done",
        ),  # a compaction that never ran
        line(
            "15:00:40Z",
            "sparkjoy: That looks like a bug: /sparkjoy bug drafts a report of it.",
        ),
        # A verdict to compact in the mode `classify`, just before the user's own compaction.
        line("15:29:50Z", "sparkjoy: Would compact at 226k tokens: the lexer is done"),
        prompt("15:30:00Z", "<command-name>/compact</command-name>"),
        boundary("15:31:00Z"),
        compact_summary("15:31:00Z"),
        prompt("17:00:00Z", "and again"),  # after the user's compaction, not sparkjoy's
        request("17:00:10Z", 30_000, 90_000),
    ]


def test_counts_verdicts_and_returns(tmp_path):
    summary, page = report(tmp_path, session_with_compactions())
    # The page opens on the same paragraphs, with the counts of each kind of verdict after the first.
    assert summary == [
        "Cost $5.31 more, 90% more than these sessions would have cost without sparkjoy's compactions.",
        "7 verdicts in 1 of 1 sessions: 3 compacted, 1 would compact, 1 kept, 0 unreadable, "
        "1 no verdict, 0 dropped, 1 failed; 2 missed the prompt cache",
        "Replayed at the list prices of each session's model, with Claude Code compacting on its own "
        "when the window fills and, in a session you had open, an idle context from 200k, these "
        "sessions cost $11 with "
        "sparkjoy's compactions, $3.70 of it for its checks, against $5.93 without them.",
        "Asking from 350k at turn ends and 250k in idle sessions costs least in this session's "
        "replay, of the floors it tried.",
        "None of sparkjoy's 2 compactions ended a session. Of the 2, the next request came once "
        "before the prompt cache lapsed, and once after it lapsed.",
        "3 returns after an hour or more idle to sessions left with 175k or more in context; 2 had "
        "been compacted in between (1 by /compact, 1 by sparkjoy). The other 1 wrote 230k tokens to "
        "the cache again, for $1.84.",
    ]
    assert '<p class="lead"><b>Cost $5.31 more</b>, 90% more' in page
    assert '<span class="mark would-compact"' in page
    assert "Fix the parser" in page
    assert "hooks.json" not in page
    assert (
        "2 missed the prompt cache" in page
        and "missed the cache (read 30k of 227k)" in page
        and "missed the cache (read 30k of 262k)" in page
    )
    # The lockup, its dots in the accent, and the icon as the favicon.
    assert '<svg class="logo"' in page and page.count('class="accent"') == 3
    assert '<link rel="icon"' in page


def test_a_compaction_after_the_last_request_ends_its_session_and_costs_its_summary(
    tmp_path,
):
    """The same session, closed right after one more compaction of sparkjoy's. The replay with
    sparkjoy's compactions prices that compaction's request, which raises what they cost by more
    than the question before it."""
    summary, _ = report(
        tmp_path,
        [
            *session_with_compactions(),
            line(
                "17:00:20Z", "sparkjoy: Compacting at 120k tokens: the lexer has landed"
            ),
            boundary("17:01:00Z"),
            compact_summary("17:01:00Z"),
        ],
    )
    assert summary[0] == (
        "Cost $5.39 more, 91% more than these sessions would have cost without sparkjoy's "
        "compactions."
    )
    assert (
        "1 of sparkjoy's 3 compactions ended a session, with no request after it by the window's "
        "end. Of the other 2, the next request came once before the prompt cache lapsed, and once "
        "after it lapsed." in summary
    )


def test_a_first_run_leads_with_what_the_fitted_floors_would_have_saved(tmp_path):
    """Before sparkjoy has run: the same session, with the user's compactions and none of
    sparkjoy's lines."""
    lines = ("sparkjoy: ", "compact-when-done: ")
    summary, page = report(
        tmp_path,
        [
            e
            for e in session_with_compactions()
            if not str(e.get("content", "")).startswith(lines)
        ],
    )
    assert summary[:2] == [
        "Compacting at every turn end from 350k and in every idle session from 250k would have left "
        "this session's cost at $7.58, replayed at Opus 5.5 list prices.",
        "sparkjoy logged no verdict in this session, which reached one of its default floors: 250k "
        "at a turn end, or 175k before the session sat idle.",
    ]
    assert "<b>$7.58</b>" in page and "Verdicts" not in page


def test_sessions_on_a_200k_window_say_sparkjoy_cannot_act(tmp_path):
    """An account on a 200k window: one session Claude Code compacted at 165k, and another the
    user compacted before it got that far, which shows no window of its own."""
    transcript(
        tmp_path,
        [
            prompt("09:00:00Z", "plan it"),
            request("09:00:10Z", 70_000, 10_000),
            prompt("09:05:00Z", "<command-name>/compact</command-name>"),
            boundary("09:06:00Z"),
            compact_summary("09:06:00Z"),
            prompt("09:10:00Z", "carry on"),
            request("09:10:10Z", 20_000, 10_000),
        ],
        session="other",
    )
    summary, page = report(
        tmp_path,
        [
            prompt("10:00:00Z", "start"),
            request("10:00:10Z", 150_000, 15_000),
            boundary("10:01:00Z", trigger="auto", before=165_000),
            compact_summary("10:01:00Z"),
            prompt("10:05:00Z", "go on"),
            request("10:05:10Z", 20_000, 30_000),
        ],
    )
    assert summary == [
        "sparkjoy can't act in these 2 sessions. Claude Code compacted 1 of them on its own before "
        "200k, as it does in a 200k context window or where auto-compact is set to start earlier, "
        "and none of them passed 200k. sparkjoy acts only in sessions with a 1M window, from 175k."
    ]
    assert "Thresholds" not in page


def test_a_session_below_the_floor_had_nothing_to_ask(tmp_path):
    summary, _ = report(
        tmp_path,
        [
            prompt("10:00:00Z", "hello"),
            request("10:00:10Z", 0, 20_000),
            prompt("10:01:00Z", "and again"),
            request("10:01:10Z", 20_000, 1_000),
        ],
    )
    assert summary == [
        "No session reached sparkjoy's default floors: 250k at a turn end, or 175k before the "
        "session sat idle."
    ]


def test_saves_the_figures_the_question_uses(tmp_path):
    figures = tmp_path / "data" / "figures.json"  # in a folder the report makes
    summary, page = report(tmp_path, session_with_compactions(), "--figures", figures)
    # K is the median first request after the three compactions, 240k, 225k and 120k, as no turn is long
    # enough to measure what they read again; P what those requests read from the cache; SUMMARY the
    # 1k-token summary times the 1.5 output per visible token of the reply its size. Every request ends
    # its turn, and two of the four between the first request and the user's compaction came more than
    # an hour after the one before, past the cache's lifetime. Opus 5.5 prices that hour-long cache.
    # The floors are the replay's cheapest pair on fit.py's grids, counting the questions each asks.
    assert json.loads(figures.read_text()) == {
        "FLOOR": 350_000,
        "IDLE_FLOOR": 250_000,
        "K": 225_002,
        "P": 30_000,
        "SUMMARY": 1_500,
        "TURN_MEDIAN": 1,
        "LAPSE": 0.5,
        "WRITE": 40,
        "OUTPUT": 100,
    }
    assert (
        "Asking from 350k at turn ends and 250k in idle sessions costs least in this session's "
        "replay, of the floors it tried, and sparkjoy asks from them from the next turn end, "
        "except where you set your own."
    ) in summary
    assert summary[-1].startswith(
        "Fitted figures: floor at turn ends 350k; floor in idle sessions 250k; context after a "
        "compaction 225k;"
    )
    assert (
        "sparkjoy asks from these floors, except where you set a floor of your own, and its question"
        in page
    )


def test_saves_no_figures_without_compactions(tmp_path):
    figures = tmp_path / "figures.json"
    summary, page = report(
        tmp_path,
        [prompt("10:00:00Z", "hello"), request("10:00:10Z", 0, 20_000)],
        "--figures",
        figures,
    )
    assert not figures.exists()
    # A session of one request is no session the report reads.
    assert summary[0] == (
        "No sessions to report. No interactive Claude Code session of more than one request in "
        f"{tmp_path / 'projects'} ran in this window."
    )
    assert (
        summary[-1]
        == "sparkjoy keeps the floors and figures it has: the window holds no compactions to fit them to"
    )
    assert "its question uses these figures" not in page


def test_a_figures_file_it_cannot_save_leaves_the_page(tmp_path):
    (tmp_path / "data").write_text("")  # a file where the figures' folder would go
    summary, page = report(
        tmp_path,
        session_with_compactions(),
        "--figures",
        tmp_path / "data" / "figures.json",
    )
    assert summary[-1].startswith(
        "sparkjoy keeps the floors and figures it has: saving them failed: "
    )
    assert "its question uses these figures" not in page and "Fix the parser" in page
