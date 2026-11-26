#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# ///
"""Asks a checkout's question at each case's turn end, and scores the verdicts.

    uv run --script evals/run.py [--question-from CHECKOUT ...] [--case ID ...] [--repeat N] [--keep-reply]
    uv run --script evals/run.py --report

Each run rebuilds a case's fixture as a session in a scratch Claude Code
config, resumes it with `claude -p --resume <id> --fork-session`, and sends
the question that `question()` in `<checkout>/hooks/register.ts` renders for
the case's context size (`question.ts`), with the case's reply for a checkout
whose question quotes it. The child has no tools, no
MCP servers and no user instructions, skills or plugins. Each result is
appended to `results.jsonl` (or `--results`); `--report` prints the table
README.md shows.

The session stops before the turn's reply, as the plugin's fork does: the fork
replays the turn's last request, which ends before the reply. `--keep-reply`
ends it with the reply instead, so the question is the next message after the
model's own last word.

Runs of one case go in sequence from one directory and share one cache write
of its transcript, so each run after the first costs about a twentieth of it.
Claude Code puts the cache breakpoint on the last content block, so the
questions' common opening (up to the quoted reply, when they agree that far)
goes first in a request of its own, and every run then sends that opening and
the rest of its question as two blocks, reading the transcript and the opening
from the cache. `FORCE_PROMPT_CACHING_5M` keeps the write at the five-minute
price, which the runs refresh as they read it. Cases run `--jobs` at a time.

`--question-from` takes a checkout or a `register.ts` file, so a variant of the
question can be tried from a scratch copy before it is committed.

Credentials come from `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` when set,
or else from the local Claude Code login's access token (never its refresh
token): the macOS keychain item `Claude Code-credentials`, or
`~/.claude/.credentials.json`.
"""

import argparse
import getpass
import gzip
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

HERE = Path(__file__).parent
RESULTS = HERE / "results.jsonl"
MODEL = "claude-opus-5-5"
# The effort Claude Code's own sessions ran at in the fixtures.
EFFORT = "xhigh"
# How long an idle case's session has sat untouched when it is asked: the
# plugin's wait on a one-hour cache (IDLE_MS).
IDLE_MINUTES = 55


def access_token() -> dict[str, str]:
    """The credential a child authenticates with, as environment variables."""
    for name in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"):
        if os.environ.get(name):
            return {name: os.environ[name]}
    if sys.platform == "darwin":
        found = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                "Claude Code-credentials",
                "-a",
                os.environ.get("USER") or getpass.getuser(),
                "-w",
            ],
            capture_output=True,
            text=True,
        )
        blob = found.stdout if found.returncode == 0 else None
    else:
        path = Path.home() / ".claude/.credentials.json"
        blob = path.read_text() if path.is_file() else None
    token = (
        (json.loads(blob).get("claudeAiOauth") or {}).get("accessToken")
        if blob
        else None
    )
    if not token:
        sys.exit(
            "No credentials: set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY, or log in to Claude Code."
        )
    return {"CLAUDE_CODE_OAUTH_TOKEN": token}


# The text of the message Claude Code puts after a resumed session's last
# message when that message is the user's, such as a tool result.
NO_RESPONSE = "No response requested."


def added_on_resume(message: dict) -> bool:
    content = message["content"]
    if (
        isinstance(content, list)
        and len(content) == 1
        and content[0].get("type") == "text"
    ):
        content = content[0]["text"]
    return message["role"] == "assistant" and content == NO_RESPONSE


class Proxy(BaseHTTPRequestHandler):
    """Forwards the child's API requests, dropping the assistant message Claude Code
    adds on resuming a session that ends with a tool result. The plugin's fork sends
    no such message: its question follows the turn's last tool result."""

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path.split("?")[0] == "/v1/messages":
            request = json.loads(body)
            request["messages"] = [
                m for m in request["messages"] if not added_on_resume(m)
            ]
            body = json.dumps(request).encode()
        headers = {
            k: v
            for k, v in self.headers.items()
            if k.lower() not in ("host", "content-length", "connection")
        }
        upstream = http.client.HTTPSConnection("api.anthropic.com", timeout=900)
        upstream.request(
            "POST", self.path, body, headers | {"Content-Length": str(len(body))}
        )
        response = upstream.getresponse()
        self.send_response(response.status)
        for k, v in response.getheaders():
            if k.lower() not in ("transfer-encoding", "connection", "content-length"):
                self.send_header(k, v)
        self.send_header("Connection", "close")
        self.end_headers()
        while chunk := response.read1(65536):
            self.wfile.write(chunk)
            self.wfile.flush()
        upstream.close()

    def log_message(self, *_):
        pass


def start_proxy() -> str:
    """Serves `Proxy` on a free local port; returns its base URL."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_port}"


def child_env(config: Path, credential: dict[str, str], proxy: str) -> dict[str, str]:
    """This environment without any Claude Code or Anthropic setting, pointed at `config` and `proxy`."""
    env = {
        k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "ANTHROPIC"))
    }
    return (
        env
        | credential
        | {
            "ANTHROPIC_BASE_URL": proxy,
            "CLAUDE_CONFIG_DIR": str(config),
            "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "FORCE_PROMPT_CACHING_5M": "1",
        }
    )


def project_dir(path: Path) -> str:
    """Claude Code's folder name for a working directory's sessions."""
    return "".join(c if c.isalnum() else "-" for c in str(path))


def load(case: dict) -> list[dict]:
    with gzip.open(HERE / "fixtures" / f"{case['id']}.jsonl.gz", "rt") as f:
        return [json.loads(line) for line in f]


def split_reply(entries: list[dict]) -> tuple[list[dict], str]:
    """The fixture without the assistant message it ends with, the turn's reply, and that reply's text."""
    last = entries[-1]["message"]["id"]
    first = next(
        i
        for i, e in enumerate(entries)
        if e["type"] == "assistant" and e["message"]["id"] == last
    )
    blocks = [b for e in entries[first:] for b in e["message"]["content"]]
    return entries[:first], "\n\n".join(
        b["text"] for b in blocks if b.get("type") == "text"
    )


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
            stamp = (
                (datetime.fromisoformat(entry["timestamp"]) + shift)
                .isoformat()
                .replace("+00:00", "Z")
            )
            extra = {
                "sessionId": session,
                "cwd": str(cwd),
                "userType": "external",
                "isSidechain": False,
            }
            f.write(
                json.dumps(entry | extra | {"timestamp": stamp}, ensure_ascii=False)
                + "\n"
            )
    return session


def register(source: Path) -> Path:
    return source if source.is_file() else source / "hooks/register.ts"


def render(source: Path, reply: str, case: dict) -> str:
    args = [
        "bun",
        str(HERE / "question.ts"),
        str(register(source)),
        str(case["tokens"]),
    ]
    if case["kind"] == "idle":
        args += ["--idle", str(IDLE_MINUTES)]
    return subprocess.run(
        args, input=reply, capture_output=True, text=True, check=True
    ).stdout


def parse(text: str) -> dict | None:
    """The verdict in `text`, read as the plugin reads it, or None."""
    try:
        value = json.loads(text[text.index("{") : text.rindex("}") + 1])
    except ValueError:
        return None
    ok = (
        isinstance(value, dict)
        and isinstance(value.get("compact"), bool)
        and isinstance(value.get("reason"), str)
    )
    return value if ok else None


def label(source: Path) -> str:
    """The checkout's branch and commit, e.g. `main@02beed1`, or a file's name."""
    if source.is_file():
        return source.stem
    checkout = source

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(checkout), *args], capture_output=True, text=True
        ).stdout.strip()

    return f"{git('rev-parse', '--abbrev-ref', 'HEAD')}@{git('rev-parse', '--short', 'HEAD')}"


def shared_opening(questions: list[str]) -> str:
    """The paragraphs every question opens with, through the quoted reply at most."""
    paragraphs = [q.split("\n\n") for q in questions]
    common = []
    for parts in zip(*paragraphs, strict=False):
        if len(set(parts)) > 1:
            break
        common.append(parts[0])
        if parts[0].endswith("</reply>"):
            break
    # Every question keeps a part of its own after the opening.
    while common and any(len(p) <= len(common) for p in paragraphs):
        common.pop()
    return "\n\n".join(common)


def send(
    blocks: list[str],
    session: str,
    config: Path,
    cwd: Path,
    credential: dict[str, str],
    proxy: str,
) -> dict:
    """One request on the staged session, its last message made of `blocks`; returns Claude Code's result."""
    message = {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": b} for b in blocks],
        },
    }
    proc = subprocess.run(
        [
            "claude",
            "-p",
            "--resume",
            session,
            "--fork-session",
            "--no-session-persistence",
            "--model",
            MODEL,
            "--effort",
            EFFORT,
            "--tools",
            "",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",
        ],
        input=json.dumps(message) + "\n",
        cwd=cwd,
        env=child_env(config, credential, proxy),
        capture_output=True,
        text=True,
        timeout=900,
    )
    events = [
        json.loads(line) for line in proc.stdout.splitlines() if line.startswith("{")
    ]
    # A resumed session can report a result for a notification it replays before
    # taking the message, so the answer is the last result.
    out = next((e for e in reversed(events) if e.get("type") == "result"), {})
    if out.get("is_error") or "result" not in out:
        raise RuntimeError(
            (out.get("result") or proc.stderr or proc.stdout).strip()[:500]
        )
    return out


def row_of(case: dict, source: Path, out: dict, claude: str) -> dict:
    usage = out.get("usage") or {}
    verdict = parse(out.get("result") or "")
    got = None if verdict is None else ("compact" if verdict["compact"] else "keep")
    row = {
        "case": case["id"],
        "question": label(source),
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "expected": case["expected"],
        "got": got,
        "reason": verdict["reason"] if verdict else (out.get("result") or "")[:300],
        "context": sum(
            usage.get(k, 0)
            for k in (
                "input_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
            )
        ),
        "cache_read": usage.get("cache_read_input_tokens", 0),
        "output": usage.get("output_tokens", 0),
        "cost": out.get("total_cost_usd"),
        "claude": claude,
    }
    # The thinking tokens among the output, where the client reports them, so
    # that a row without them reads as unmeasured rather than as no thinking.
    details = usage.get("output_tokens_details") or {}
    if "thinking_tokens" in details:
        row["thinking"] = details["thinking_tokens"]
    return row


def run(
    cases: list[dict],
    sources: list[Path],
    repeat: int,
    jobs: int,
    results: Path,
    keep_reply: bool,
) -> None:
    credential, proxy = access_token(), start_proxy()
    # The mid-task verdicts have moved between batches of the same question and
    # transcripts, with how often the model thinks before answering, so each
    # row records the client that ran it and its thinking tokens.
    claude = subprocess.run(
        ["claude", "--version"], capture_output=True, text=True, check=True
    ).stdout.split()[0]

    def one_case(case: dict) -> list[dict]:
        entries = load(case)
        before, reply = split_reply(entries)
        if not keep_reply:
            entries = before
        questions = {source: render(source, reply, case) for source in sources}
        opening = shared_opening(list(questions.values()))
        # One fixed directory per case, so that every run of it gets the same
        # system prompt, which names the working directory. Resolved, since
        # Claude Code files a session under its directory's real path
        # (`/private/var/…` on macOS).
        scratch = Path(tempfile.gettempdir()).resolve() / "sparkjoy-evals" / case["id"]
        shutil.rmtree(scratch, ignore_errors=True)
        rows, primed = [], 0.0
        try:
            config, cwd = scratch / "config", scratch / "work"
            cwd.mkdir(parents=True)
            session = stage(entries, case, config, cwd)
            if opening and len(sources) * repeat > 1:
                primed = (
                    send([opening], session, config, cwd, credential, proxy).get(
                        "total_cost_usd"
                    )
                    or 0.0
                )
            for source, question in questions.items():
                blocks = (
                    [opening, question[len(opening) :].lstrip("\n")]
                    if opening
                    else [question]
                )
                for _ in range(repeat):
                    try:
                        row = row_of(
                            case,
                            source,
                            send(blocks, session, config, cwd, credential, proxy),
                            claude,
                        )
                    except (RuntimeError, subprocess.SubprocessError) as error:
                        print(
                            f"ERROR {case['id']} [{label(source)}]: {error}", flush=True
                        )
                        continue
                    # The first run carries the cache write.
                    if primed:
                        row["cost"], primed = (row["cost"] or 0) + primed, 0.0
                    with results.open("a") as f:
                        f.write(json.dumps(row) + "\n")
                    mark = "PASS" if row["got"] == row["expected"] else "FAIL"
                    print(
                        f"{mark} {row['case']} [{row['question']}] expected {row['expected']}, got {row['got']} "
                        f"at {row['context'] // 1000}k (${row['cost'] or 0:.2f}): {row['reason']}",
                        flush=True,
                    )
                    rows.append(row)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        return rows

    with ThreadPoolExecutor(jobs) as pool:
        rows = [row for rows in pool.map(one_case, cases) for row in rows]
    print(
        f"{sum(r['got'] == r['expected'] for r in rows)}/{len(rows)} passed, ${sum(r['cost'] or 0 for r in rows):.2f}"
    )


def report(cases: list[dict], results: Path) -> None:
    """The results table: per case and question, how many runs matched the label."""
    rows = [json.loads(line) for line in results.open()] if results.exists() else []
    questions = list(dict.fromkeys(r["question"] for r in rows))
    print(
        "| case | kind | recorded | replayed | expected | "
        + " | ".join(questions)
        + " |"
    )
    print("|---|---|---|---|---|" + "---|" * len(questions))
    for case in cases:
        mine = [r for r in rows if r["case"] == case["id"]]
        # The latest run's, since the client's own prompt changes its size.
        replayed = mine[-1]["context"] if mine else 0
        cells = []
        for q in questions:
            runs = [r for r in mine if r["question"] == q]
            passed = sum(r["got"] == case["expected"] for r in runs)
            cell = f"{passed}/{len(runs)}" if runs else ""
            # Rows from before 25 November 2026 don't record thinking.
            if recorded := [r for r in runs if "thinking" in r]:
                thought = sum(bool(r["thinking"]) for r in recorded)
                cell += (
                    f" ({thought} thought)"
                    if len(recorded) == len(runs)
                    else f" ({thought} of {len(recorded)} thought)"
                )
            cells.append(cell)
        print(
            f"| {case['id']} | {case['kind']} | {case['tokens'] // 1000}k | {replayed // 1000}k | {case['expected']} | "
            + " | ".join(cells)
            + " |"
        )
    for q in questions:
        runs = [r for r in rows if r["question"] == q]
        print(
            f"\n{q}: {sum(r['got'] == r['expected'] for r in runs)}/{len(runs)} runs passed, ${sum(r['cost'] or 0 for r in runs):.2f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--question-from",
        type=Path,
        action="append",
        help="checkout, or register.ts file, whose question to ask (default: this checkout)",
    )
    parser.add_argument("--case", action="append", help="case id to run (default: all)")
    parser.add_argument(
        "--repeat", type=int, default=1, help="runs per case and question"
    )
    parser.add_argument(
        "--keep-reply",
        action="store_true",
        help="end the session with the turn's reply, so the question follows it",
    )
    parser.add_argument("--jobs", type=int, default=3, help="cases run at once")
    parser.add_argument(
        "--report", action="store_true", help="print the results table and exit"
    )
    parser.add_argument(
        "--results",
        type=Path,
        default=RESULTS,
        help="the file runs append to and --report reads",
    )
    args = parser.parse_args()
    cases = json.loads((HERE / "cases.json").read_text())
    if args.case:
        unknown = set(args.case) - {c["id"] for c in cases}
        if unknown:
            sys.exit(f"Unknown cases: {sorted(unknown)}")
        cases = [c for c in cases if c["id"] in args.case]
    if args.report:
        return report(cases, args.results)
    sources = [p.expanduser().resolve() for p in args.question_from or [HERE.parent]]
    run(cases, sources, args.repeat, args.jobs, args.results, args.keep_reply)


if __name__ == "__main__":
    main()
