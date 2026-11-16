#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# ///
"""Asks a checkout's question at each case's turn end, and scores the verdicts.

    uv run --script evals/run.py [--question-from CHECKOUT ...] [--case ID ...] [--repeat N]
    uv run --script evals/run.py --report

Each run rebuilds a case's fixture as a session in a scratch Claude Code
config, resumes it with `claude -p --resume <id> --fork-session`, and sends
the question that `question()` in `<checkout>/hooks/register.ts` renders for
the case's reply and context size (`question.ts`). The child has no tools, no
MCP servers and no user instructions, skills or plugins. Each result is
appended to `results.jsonl` (or `--results`); `--report` prints the table
README.md shows.

The fixture stops at the turn's reply, and the question repeats that reply, as
the plugin's does. The plugin's fork instead replays the turn's last request,
which ends before the reply, so here the model reads its reply twice.

Runs of one case go in sequence from one directory, so a repeat of the same
question reads the transcript from the prompt cache, at about a twentieth of
the first run's cost. Another checkout's question writes it again: the cache
breakpoint sits on the last message, which is the question.
`FORCE_PROMPT_CACHING_5M` keeps those writes at the five-minute price. Cases
run `--jobs` at a time.

Credentials come from `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` when set,
or else from the local Claude Code login's access token (never its refresh
token): the macOS keychain item `Claude Code-credentials`, or
`~/.claude/.credentials.json`.
"""

import argparse
import getpass
import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).parent
RESULTS = HERE / "results.jsonl"
MODEL = "claude-opus-5-5"
# The effort Claude Code's own sessions ran at in the fixtures.
EFFORT = "xhigh"
# How long an idle case's session has sat untouched when it is asked: the
# plugin's IDLE_MS.
IDLE_MINUTES = 55


def access_token() -> dict[str, str]:
    """The credential a child authenticates with, as environment variables."""
    for name in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"):
        if os.environ.get(name):
            return {name: os.environ[name]}
    if sys.platform == "darwin":
        found = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-a", os.environ.get("USER") or getpass.getuser(), "-w"],
            capture_output=True,
            text=True,
        )
        blob = found.stdout if found.returncode == 0 else None
    else:
        path = Path.home() / ".claude/.credentials.json"
        blob = path.read_text() if path.is_file() else None
    token = (json.loads(blob).get("claudeAiOauth") or {}).get("accessToken") if blob else None
    if not token:
        sys.exit("No credentials: set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY, or log in to Claude Code.")
    return {"CLAUDE_CODE_OAUTH_TOKEN": token}


def child_env(config: Path, credential: dict[str, str]) -> dict[str, str]:
    """This environment without any Claude Code or Anthropic setting, pointed at `config`."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "ANTHROPIC"))}
    return env | credential | {
        "CLAUDE_CONFIG_DIR": str(config),
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "FORCE_PROMPT_CACHING_5M": "1",
    }


def project_dir(path: Path) -> str:
    """Claude Code's folder name for a working directory's sessions."""
    return "".join(c if c.isalnum() else "-" for c in str(path))


def load(case: dict) -> list[dict]:
    with gzip.open(HERE / "fixtures" / f"{case['id']}.jsonl.gz", "rt") as f:
        return [json.loads(line) for line in f]


def reply_of(entries: list[dict]) -> str:
    """The text of the assistant message the fixture ends with."""
    last = entries[-1]["message"]["id"]
    blocks = [b for e in entries if e["type"] == "assistant" and e["message"]["id"] == last for b in e["message"]["content"]]
    return "\n\n".join(b["text"] for b in blocks if b.get("type") == "text")


def stage(entries: list[dict], case: dict, config: Path, cwd: Path) -> str:
    """Writes the fixture as a session Claude Code can resume from `cwd`; returns its id."""
    session = str(uuid.uuid4())
    # The turn ended a moment ago, or IDLE_MINUTES ago for an idle case.
    ago = timedelta(minutes=IDLE_MINUTES if case["kind"] == "idle" else 1)
    shift = datetime.now(UTC) - ago - datetime.fromisoformat(entries[-1]["timestamp"])
    folder = config / "projects" / project_dir(cwd)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / f"{session}.jsonl").open("w") as f:
        for entry in entries:
            stamp = (datetime.fromisoformat(entry["timestamp"]) + shift).isoformat().replace("+00:00", "Z")
            extra = {"sessionId": session, "cwd": str(cwd), "userType": "external", "isSidechain": False}
            f.write(json.dumps(entry | extra | {"timestamp": stamp}, ensure_ascii=False) + "\n")
    return session


def render(checkout: Path, reply: str, case: dict) -> str:
    args = ["bun", str(HERE / "question.ts"), str(checkout / "hooks/register.ts"), str(case["tokens"])]
    if case["kind"] == "idle":
        args.append(str(IDLE_MINUTES))
    return subprocess.run(args, input=reply, capture_output=True, text=True, check=True).stdout


def parse(text: str) -> dict | None:
    """The verdict in `text`, read as the plugin reads it, or None."""
    try:
        value = json.loads(text[text.index("{") : text.rindex("}") + 1])
    except ValueError:
        return None
    ok = isinstance(value, dict) and isinstance(value.get("compact"), bool) and isinstance(value.get("reason"), str)
    return value if ok else None


def label(checkout: Path) -> str:
    """The checkout's branch and commit, e.g. `main@02beed1`."""
    def git(*args):
        return subprocess.run(["git", "-C", str(checkout), *args], capture_output=True, text=True).stdout.strip()

    return f"{git('rev-parse', '--abbrev-ref', 'HEAD')}@{git('rev-parse', '--short', 'HEAD')}"


def ask(case: dict, checkout: Path, credential: dict[str, str]) -> dict:
    """One run of one case against one checkout's question."""
    entries = load(case)
    question = render(checkout, reply_of(entries), case)
    # One fixed directory per case, so that every run of it gets the same
    # system prompt, which names the working directory, and reads the
    # transcript from the cache. Resolved, since Claude Code files a session
    # under its directory's real path (`/private/var/…` on macOS).
    scratch = Path(tempfile.gettempdir()).resolve() / "sparkjoy-evals" / case["id"]
    shutil.rmtree(scratch, ignore_errors=True)
    try:
        config, cwd = scratch / "config", scratch / "work"
        cwd.mkdir(parents=True)
        session = stage(entries, case, config, cwd)
        proc = subprocess.run(
            [
                "claude", "-p", "--resume", session, "--fork-session", "--no-session-persistence",
                "--model", MODEL, "--effort", EFFORT, "--tools", "", "--strict-mcp-config",
                "--disable-slash-commands", "--output-format", "json",
            ],
            input=question,
            cwd=cwd,
            env=child_env(config, credential),
            capture_output=True,
            text=True,
            timeout=900,
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    out = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else {}
    if out.get("is_error") or "result" not in out:
        raise RuntimeError((out.get("result") or proc.stderr or proc.stdout).strip()[:500])
    usage = out.get("usage") or {}
    verdict = parse(out.get("result") or "")
    got = None if verdict is None else ("compact" if verdict["compact"] else "keep")
    return {
        "case": case["id"],
        "question": label(checkout),
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "expected": case["expected"],
        "got": got,
        "reason": verdict["reason"] if verdict else (out.get("result") or proc.stderr)[:300],
        "context": sum(usage.get(k, 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")),
        "cache_read": usage.get("cache_read_input_tokens", 0),
        "cost": out.get("total_cost_usd"),
    }


def run(cases: list[dict], checkouts: list[Path], repeat: int, jobs: int, results: Path) -> None:
    credential = access_token()

    def one_case(case: dict) -> list[dict]:
        rows = []
        for checkout in checkouts:
            for _ in range(repeat):
                try:
                    row = ask(case, checkout, credential)
                except (RuntimeError, subprocess.SubprocessError) as error:
                    print(f"ERROR {case['id']} [{label(checkout)}]: {error}", flush=True)
                    continue
                with results.open("a") as f:
                    f.write(json.dumps(row) + "\n")
                mark = "PASS" if row["got"] == row["expected"] else "FAIL"
                print(f"{mark} {row['case']} [{row['question']}] expected {row['expected']}, got {row['got']} "
                      f"at {row['context'] // 1000}k (${row['cost'] or 0:.2f}): {row['reason']}", flush=True)
                rows.append(row)
        return rows

    with ThreadPoolExecutor(jobs) as pool:
        rows = [row for rows in pool.map(one_case, cases) for row in rows]
    print(f"{sum(r['got'] == r['expected'] for r in rows)}/{len(rows)} passed, ${sum(r['cost'] or 0 for r in rows):.2f}")


def report(cases: list[dict], results: Path) -> None:
    """The results table: per case and question, how many runs matched the label."""
    rows = [json.loads(line) for line in results.open()] if results.exists() else []
    questions = list(dict.fromkeys(r["question"] for r in rows))
    print("| case | kind | recorded | replayed | expected | " + " | ".join(questions) + " |")
    print("|---|---|---|---|---|" + "---|" * len(questions))
    for case in cases:
        mine = [r for r in rows if r["case"] == case["id"]]
        replayed = max((r["context"] for r in mine), default=0)
        cells = []
        for q in questions:
            runs = [r for r in mine if r["question"] == q]
            passed = sum(r["got"] == case["expected"] for r in runs)
            cells.append(f"{passed}/{len(runs)}" if runs else "")
        print(f"| {case['id']} | {case['kind']} | {case['tokens'] // 1000}k | {replayed // 1000}k | {case['expected']} | " + " | ".join(cells) + " |")
    for q in questions:
        runs = [r for r in rows if r["question"] == q]
        print(f"\n{q}: {sum(r['got'] == r['expected'] for r in runs)}/{len(runs)} runs passed, ${sum(r['cost'] or 0 for r in runs):.2f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--question-from", type=Path, action="append", help="checkout whose question to ask (default: this one)")
    parser.add_argument("--case", action="append", help="case id to run (default: all)")
    parser.add_argument("--repeat", type=int, default=1, help="runs per case and question")
    parser.add_argument("--jobs", type=int, default=3, help="cases run at once")
    parser.add_argument("--report", action="store_true", help="print the results table and exit")
    parser.add_argument("--results", type=Path, default=RESULTS, help="the file runs append to and --report reads")
    args = parser.parse_args()
    cases = json.loads((HERE / "cases.json").read_text())
    if args.case:
        unknown = set(args.case) - {c["id"] for c in cases}
        if unknown:
            sys.exit(f"Unknown cases: {sorted(unknown)}")
        cases = [c for c in cases if c["id"] in args.case]
    if args.report:
        return report(cases, args.results)
    checkouts = [p.expanduser().resolve() for p in args.question_from or [HERE.parent]]
    run(cases, checkouts, args.repeat, args.jobs, args.results)


if __name__ == "__main__":
    main()
