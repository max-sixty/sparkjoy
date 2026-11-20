"""Runs model/report.py on synthetic transcripts: `uvx pytest tests`. It needs uv."""

import json
import subprocess
from itertools import count
from pathlib import Path

ROOT = Path(__file__).parents[1]
DAY = "2026-11-02T"
ids = count()


def request(time, read, write, model="claude-opus-5-5", text="Done.", output=500):
    """A main-thread request that ends its turn, with `read + write` tokens of context."""
    usage = {"input_tokens": 2, "cache_read_input_tokens": read, "cache_creation_input_tokens": write,
             "cache_creation": {"ephemeral_1h_input_tokens": write}, "output_tokens": output}
    return {"type": "assistant", "timestamp": DAY + time, "isSidechain": False, "entrypoint": "cli",
            "message": {"model": model, "id": f"msg_{next(ids)}", "usage": usage, "stop_reason": "end_turn",
                        "content": [{"type": "text", "text": text}]}}


def prompt(time, text):
    return {"type": "user", "timestamp": DAY + time, "isSidechain": False, "message": {"role": "user", "content": text}}


def compact_summary(time):
    """The summary Claude Code writes after a compaction: 1,000 tokens at 4 characters each."""
    return {"type": "user", "timestamp": DAY + time, "isSidechain": False, "isCompactSummary": True,
            "message": {"role": "user", "content": "x" * 4_000}}


def line(time, text):
    """A dim line in the transcript, as a mod's `$.ui.log` writes it."""
    return {"type": "system", "subtype": "informational", "timestamp": DAY + time, "isSidechain": False,
            "content": text}


def boundary(time, trigger="manual"):
    return {"type": "system", "subtype": "compact_boundary", "timestamp": DAY + time, "isSidechain": False,
            "compactMetadata": {"trigger": trigger, "durationMs": 60_000}}


def report(tmp_path, entries, *args):
    session = tmp_path / "projects" / "project" / "session.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text("".join(json.dumps(e, separators=(",", ":")) + "\n" for e in entries))  # as Claude Code writes it
    run = subprocess.run(
        ["uv", "run", "--script", ROOT / "model/report.py", "--projects", tmp_path / "projects",
         "--since", "2026-11-01T00:00Z", "--until", "2026-11-03T00:00Z", "--no-open", *args],
        capture_output=True, text=True, env={"PATH": subprocess.os.environ["PATH"], "TMPDIR": str(tmp_path)},
        check=True)
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
        line("10:01:20Z", "compact-when-done: Compacting at 450k tokens: the fix landed"),
        boundary("10:02:00Z"),
        compact_summary("10:02:00Z"),
        prompt("10:05:00Z", "next, the lexer"),
        request("10:05:10Z", 30_000, 210_000),
        line("10:05:20Z", "sparkjoy: No verdict at 240k tokens: empty-reply"),
        prompt("12:30:00Z", "back again"),  # after two and a half hours, at 240k
        request("12:30:10Z", 30_000, 230_000),
        line("13:25:20Z", "sparkjoy: Compacting at 260k tokens, idle 55 minutes: the lexer is done"),
        boundary("13:26:00Z"),
        compact_summary("13:26:00Z"),
        prompt("15:00:00Z", "one more thing"),  # after sparkjoy's compaction
        request("15:00:10Z", 30_000, 195_000),
        line("15:00:20Z", "sparkjoy: Failed: Error: the fork failed"),
        line("15:00:39Z", "sparkjoy: Missed the prompt cache at 226k tokens: read 30k of 227k from it"),
        line("15:00:40Z", "sparkjoy: Compacting at 226k tokens: all done"),  # a compaction that never ran
        prompt("15:30:00Z", "<command-name>/compact</command-name>"),
        boundary("15:31:00Z"),
        compact_summary("15:31:00Z"),
        prompt("17:00:00Z", "and again"),  # after the user's compaction, not sparkjoy's
        request("17:00:10Z", 30_000, 90_000),
    ]


def test_counts_verdicts_and_returns(tmp_path):
    summary, page = report(tmp_path, session_with_compactions())
    assert summary[0] == ("6 verdicts in 1 of 1 sessions: 3 compacted, 1 kept, 0 unreadable, 1 no verdict, 0 dropped, "
                          "1 failed; 1 missed the prompt cache")
    assert "3 returns after an hour idle to sessions left at 175k or more, 2 compacted in between" in summary
    assert "Replayed at list prices" in page
    assert "1 by sparkjoy" in page and "1 by /compact" in page
    assert "Fix the parser" in page
    assert "hooks.json" not in page
    assert "1 missed the prompt cache" in page and "missed the cache (read 30k of 227k)" in page


def test_no_verdicts_yet(tmp_path):
    summary, page = report(tmp_path, [prompt("10:00:00Z", "hello"), request("10:00:10Z", 0, 20_000),
                                      prompt("10:01:00Z", "and again"), request("10:01:10Z", 20_000, 1_000)])
    assert summary[0] == ("0 verdicts in 0 of 1 sessions: 0 compacted, 0 kept, 0 unreadable, 0 no verdict, 0 dropped, "
                          "0 failed; 0 missed the prompt cache")
    assert "No verdicts yet; sessions pick sparkjoy up when they restart." in page


def test_saves_the_figures_the_question_states(tmp_path):
    figures = tmp_path / "data" / "figures.json"  # in a folder the report makes
    summary, page = report(tmp_path, session_with_compactions(), "--figures", figures)
    # K is the median first request after the three compactions, 240k, 225k and 120k, as no turn is long
    # enough to measure what they read again; P what those requests read from the cache; SUMMARY the
    # 1k-token summary times the 1.5 output per visible token of the reply its size. Every request ends
    # its turn, and two of the four between the first request and the user's compaction came more than
    # an hour after the one before, past the cache's lifetime. Opus 5.5 prices that hour-long cache.
    # The floors are the replay's cheapest pair on fit.py's grids.
    assert json.loads(figures.read_text()) == {"FLOOR": 350_000, "IDLE_FLOOR": 125_000, "K": 225_002, "P": 30_000,
                                               "SUMMARY": 1_500, "TURN_MEDIAN": 1, "TURN_MEAN": 1, "LAPSE": 0.5,
                                               "WRITE": 40, "OUTPUT": 100}
    assert summary[-1].startswith("sparkjoy now asks from these sessions' floors and states their figures: floor at "
                                  "turn ends 350k; floor in idle sessions 125k; context after a compaction 225k;")
    assert "sparkjoy asks from these floors and its question states these figures" in page


def test_saves_no_figures_without_compactions(tmp_path):
    figures = tmp_path / "figures.json"
    summary, page = report(tmp_path, [prompt("10:00:00Z", "hello"), request("10:00:10Z", 0, 20_000)],
                           "--figures", figures)
    assert not figures.exists()
    assert summary[-1] == "sparkjoy keeps the floors and figures it has: the window holds no compactions to fit them to"
    assert "its question states these figures" not in page


def test_a_figures_file_it_cannot_save_leaves_the_page(tmp_path):
    (tmp_path / "data").write_text("")  # a file where the figures' folder would go
    summary, page = report(tmp_path, session_with_compactions(), "--figures", tmp_path / "data" / "figures.json")
    assert summary[-1].startswith("sparkjoy keeps the floors and figures it has: saving them failed: ")
    assert "its question states these figures" not in page and "Fix the parser" in page
