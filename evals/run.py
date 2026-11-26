#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy>=2.1"]
# ///
"""Asks a checkout's question at each case's turn end, as the plugin's fork
asks it, and scores the verdicts.

    uv run --script evals/run.py [--question-from CHECKOUT ...] [--case ID ...] [--repeat N]
    uv run --script evals/run.py --report

Claude Code records each request a session sends in the session's transcript
(the `api-request`, `api-request-shape` and `api-request-blob` lines). A
case's turn end is the line of its reply in one of my sessions (`cases.json`),
and a run rebuilds the request that reply answered: its parameters, system
prompt and tools as recorded, and its messages, each checked against the hash
the record gives it. `$.model.fork` sends that request again with the turn's
reply and then its prompt after it, so a run sends the same straight to the
API, with the question `question()` in `<checkout>/hooks/register.ts` renders
for the case's recorded context size (`question.ts`). A tool call is answered
as the fork answers it, with the error the fork returns, and the request sent
again. Each result is appended to `results.jsonl` (or `--results`);
`--report` prints the table README.md shows.

Only I can run it, since it reads my transcripts from Claude Code's
`projects/` directory, as `model/fit.py` finds it. A case whose session
predates the request records (Claude Code before 2.1.304), or whose model
`fit.PRICES` doesn't price, is skipped.

Runs of one case go in sequence and share one cache write of its context, so
each run after the first costs about a twentieth of it. Cases run `--jobs` at a
time.

`--question-from` takes a checkout or a `register.ts` file, so a variant of the
question can be tried from a scratch copy before it is committed.

Credentials come from `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` when set,
or else from the local Claude Code login's access token (never its refresh
token): the macOS keychain item `Claude Code-credentials`, or
`~/.claude/.credentials.json`.
"""

import argparse
import getpass
import hashlib
import http.client
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "model"))
import fit  # noqa: E402

RESULTS = HERE / "results.jsonl"
# How long an idle case's session has sat untouched when it is asked: the
# plugin's wait on a one-hour cache (IDLE_MS).
IDLE_MINUTES = 55
# What the fork answers a tool call with.
DENIED = "A model fork cannot use tools"
# The requests a run may make, the first and one after each denied tool call,
# before it gives up.
REQUESTS = 5


def credential() -> dict[str, str]:
    """The headers a request authenticates with."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return {"x-api-key": os.environ["ANTHROPIC_API_KEY"]}
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if not token:
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
    return {"authorization": f"Bearer {token}"}


def digest(message: dict) -> str:
    """A message's hash, as Claude Code's request records name it."""
    text = json.dumps(
        message, separators=(",", ":"), sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(text.encode()).hexdigest()


def uncached(value):
    """`value` without the cache breakpoints the main thread placed."""
    if isinstance(value, dict):
        return {k: uncached(v) for k, v in value.items() if k != "cache_control"}
    if isinstance(value, list):
        return [uncached(v) for v in value]
    return value


def entries_of(case: dict) -> list[dict]:
    """The case's session, up to its turn's reply."""
    session, end = case["source"]["session"], case["source"]["line"]
    path = next(fit.PROJECTS.glob(f"*/{session}.jsonl"))
    entries = []
    with path.open() as f:
        for n, raw in enumerate(f):
            if n > end:
                break
            entries.append(json.loads(raw))
    return entries


def fork_request(entries: list[dict]) -> tuple[dict, dict, list[dict]]:
    """The recorded request the turn's reply answered, its shape (system prompt
    and tools), and its messages rebuilt, with the reply after them.

    A message the record holds no blob of is held by the transcript's lines: an
    assistant message by its lines merged by message id, a user message by a
    line or a run of them, one per result of parallel tool calls."""
    blobs = {
        e["hash"]: e["message"] for e in entries if e.get("type") == "api-request-blob"
    }
    shapes = {
        e["shapeHash"]: e["shape"]
        for e in entries
        if e.get("type") == "api-request-shape"
    }
    requests = {e["id"]: e for e in entries if e.get("type") == "api-request"}
    reply = entries[-1]
    if "requestRef" not in reply:
        raise LookupError("its session has no request records")
    request = requests[reply["requestRef"]]
    if request["params"]["model"] not in fit.PRICES:
        raise LookupError(f"fit.PRICES has no price for {request['params']['model']}")
    # The request's message hashes: its base's first `keep`, then its tail,
    # down a chain of bases hundreds of requests long.
    chain = [request]
    while chain[-1].get("base"):
        chain.append(requests[chain[-1]["base"]])
    hashes: list[str] = []
    for r in reversed(chain):
        hashes = hashes[: r.get("keep", 0)] + r["tail"]

    lines: dict[str, dict] = {}
    by_id: dict[str, list] = {}
    run: list[list] = []

    def add(message: dict) -> None:
        lines[digest(message)] = message

    for e in entries:
        if e.get("type") == "user" and isinstance(e.get("message"), dict):
            content = e["message"]["content"]
            add({"role": "user", "content": content})
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
                add({"role": "user", "content": content})
            run.append(content)
            for start in range(len(run) - 1):
                add({"role": "user", "content": [b for c in run[start:] for b in c]})
        elif e.get("type") == "assistant":
            run = []
            add({"role": "assistant", "content": e["message"]["content"]})
            by_id.setdefault(e["message"].get("id"), []).extend(e["message"]["content"])
    for content in by_id.values():
        add({"role": "assistant", "content": content})
    messages = []
    for i, h in enumerate(hashes):
        message = blobs.get(h) or lines.get(h)
        if message is None:
            raise LookupError(
                f"message {i} of the recorded request is in no blob or line"
            )
        messages.append(uncached(message))
    messages.append(
        {"role": "assistant", "content": uncached(by_id[reply["message"]["id"]])}
    )
    return request, shapes[request["shapeHash"]], messages


def body_of(request: dict, shape: dict, messages: list[dict], question: str) -> dict:
    """The fork's request: the recorded one, the reply, then the question, with
    the cache breakpoints on the system prompt and the reply."""
    system = uncached(shape["system"])
    system[-1] |= {"cache_control": {"type": "ephemeral"}}
    reply = messages[-1]
    reply = reply | {
        "content": [
            *reply["content"][:-1],
            reply["content"][-1] | {"cache_control": {"type": "ephemeral"}},
        ]
    }
    params = {k: v for k, v in request["params"].items() if k != "betas"}
    return params | {
        "system": system,
        "tools": uncached(shape["tools"]),
        "messages": [*messages[:-1], reply, {"role": "user", "content": question}],
        "stream": True,
    }


def stream(body: dict, headers: dict) -> tuple[list[dict], str | None, dict]:
    """One streamed request: the reply's content blocks, its stop reason, and its usage."""
    conn = http.client.HTTPSConnection("api.anthropic.com", timeout=900)
    conn.request("POST", "/v1/messages", json.dumps(body).encode(), headers)
    response = conn.getresponse()
    if response.status != 200:
        raise RuntimeError(f"{response.status}: {response.read()[:500]!r}")
    blocks, stop, usage, inputs = [], None, {}, {}
    for raw in response:
        line = raw.decode().strip()
        if not line.startswith("data: "):
            continue
        event = json.loads(line[6:])
        kind = event["type"]
        if kind == "message_start":
            usage = event["message"].get("usage") or {}
        elif kind == "content_block_start":
            blocks.append(dict(event["content_block"]))
        elif kind == "content_block_delta":
            block, delta = blocks[event["index"]], event["delta"]
            field = delta["type"].removesuffix("_delta")
            if field in ("text", "thinking", "signature"):
                block[field] = block.get(field, "") + delta[field]
            elif field == "input_json":
                inputs[event["index"]] = (
                    inputs.get(event["index"], "") + delta["partial_json"]
                )
            else:
                raise RuntimeError(f"a delta this runner can't read: {delta['type']}")
        elif kind == "content_block_stop" and event["index"] in inputs:
            blocks[event["index"]]["input"] = json.loads(
                inputs.pop(event["index"]) or "{}"
            )
        elif kind == "message_delta":
            stop = event["delta"].get("stop_reason")
            usage = usage | (event.get("usage") or {})
        elif kind == "error":
            raise RuntimeError(json.dumps(event)[:500])
    conn.close()
    return blocks, stop, usage


def ask(body: dict, headers: dict) -> tuple[str, dict]:
    """The fork's answer and what its requests used, summed: requests until one
    ends without a tool call. `context` is the first request's input."""
    used = {"requests": 0, "thinking": 0}
    while True:
        if used["requests"] == REQUESTS:
            raise RuntimeError(f"still calling tools after {REQUESTS} requests")
        blocks, stop, usage = stream(body, headers)
        used["requests"] += 1
        used.setdefault(
            "context",
            sum(
                usage.get(k) or 0
                for k in (
                    "input_tokens",
                    "cache_read_input_tokens",
                    "cache_creation_input_tokens",
                )
            ),
        )
        for k in (
            "input_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
            "output_tokens",
        ):
            used[k] = used.get(k, 0) + (usage.get(k) or 0)
        used["thinking"] += (usage.get("output_tokens_details") or {}).get(
            "thinking_tokens"
        ) or 0
        calls = [b for b in blocks if b["type"] == "tool_use"]
        if stop != "tool_use" or not calls:
            return "".join(
                b.get("text", "") for b in blocks if b["type"] == "text"
            ), used
        denied = [
            {
                "type": "tool_result",
                "tool_use_id": c["id"],
                "content": DENIED,
                "is_error": True,
            }
            for c in calls
        ]
        body = body | {
            "messages": body["messages"]
            + [
                {"role": "assistant", "content": blocks},
                {"role": "user", "content": denied},
            ]
        }


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

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(source), *args], capture_output=True, text=True
        ).stdout.strip()

    return f"{git('rev-parse', '--abbrev-ref', 'HEAD')}@{git('rev-parse', '--short', 'HEAD')}"


def row_of(case: dict, source: Path, request: dict, text: str, used: dict) -> dict:
    verdict = parse(text)
    got = None if verdict is None else ("compact" if verdict["compact"] else "keep")
    read, write = used["cache_read_input_tokens"], used["cache_creation_input_tokens"]
    # A run's cache writes last five minutes.
    i, o, w5, _, r = fit.PRICES[request["params"]["model"]]
    row = {
        "case": case["id"],
        "question": label(source),
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "expected": case["expected"],
        "got": got,
        "reason": verdict["reason"] if verdict else text[:300],
        "context": used["context"],
        "cache_read": read,
        "output": used["output_tokens"],
        # Thinking tokens among the output; zero where the model answered
        # without thinking.
        "thinking": used["thinking"],
        "requests": used["requests"],
        "cost": round(
            (
                used["input_tokens"] * i
                + used["output_tokens"] * o
                + read * r
                + write * w5
            )
            / 1e6,
            4,
        ),
        # The Claude Code release that recorded the request.
        "client": request["version"],
    }
    # Whether the answer said the next turns need the transcript's detail, for
    # a question that asks.
    if verdict is not None and "needs_detail" in verdict:
        row["needs_detail"] = verdict["needs_detail"]
    return row


def run(
    cases: list[dict], sources: list[Path], repeat: int, jobs: int, results: Path
) -> None:
    auth = credential()

    def one_case(case: dict) -> list[dict]:
        try:
            request, shape, messages = fork_request(entries_of(case))
        except LookupError as error:
            print(f"SKIP {case['id']}: {error}", flush=True)
            return []
        headers = auth | {
            "anthropic-version": "2023-06-01",
            "anthropic-beta": ",".join(
                request["params"]["betas"]
                + (["oauth-2025-04-20"] if "authorization" in auth else [])
            ),
            "content-type": "application/json",
        }
        reply = "\n\n".join(
            b["text"] for b in messages[-1]["content"] if b["type"] == "text"
        )
        bodies = {
            source: body_of(request, shape, messages, render(source, reply, case))
            for source in sources
        }
        rows = []
        # The questions take turns, so drift over a batch falls on each alike.
        for _ in range(repeat):
            for source, body in bodies.items():
                try:
                    text, used = ask(body, headers)
                except (RuntimeError, OSError) as error:
                    print(f"ERROR {case['id']} [{label(source)}]: {error}", flush=True)
                    continue
                row = row_of(case, source, request, text, used)
                with results.open("a") as f:
                    f.write(json.dumps(row) + "\n")
                mark = "PASS" if row["got"] == row["expected"] else "FAIL"
                print(
                    f"{mark} {row['case']} [{row['question']}] expected {row['expected']}, got {row['got']} "
                    f"at {row['context'] // 1000}k (${row['cost']:.2f}): {row['reason']}",
                    flush=True,
                )
                rows.append(row)
        return rows

    with ThreadPoolExecutor(jobs) as pool:
        rows = [row for rows in pool.map(one_case, cases) for row in rows]
    print(
        f"{sum(r['got'] == r['expected'] for r in rows)}/{len(rows)} passed, ${sum(r['cost'] for r in rows):.2f}"
    )


def report(cases: list[dict], results: Path) -> None:
    """The results table: per case and question, how many runs matched the
    label, and how many of them thought before answering."""
    rows = [json.loads(line) for line in results.open()] if results.exists() else []
    questions = list(dict.fromkeys(r["question"] for r in rows))
    print("| case | kind | context | expected | " + " | ".join(questions) + " |")
    print("|---|---|---|---|" + "---|" * len(questions))
    for case in cases:
        mine = [r for r in rows if r["case"] == case["id"]]
        cells = []
        for q in questions:
            runs = [r for r in mine if r["question"] == q]
            passed = sum(r["got"] == case["expected"] for r in runs)
            thought = sum(r["thinking"] > 0 for r in runs)
            cells.append(f"{passed}/{len(runs)} ({thought} thought)" if runs else "")
        print(
            f"| {case['id']} | {case['kind']} | {case['tokens'] // 1000}k | {case['expected']} | "
            + " | ".join(cells)
            + " |"
        )
    for q in questions:
        runs = [r for r in rows if r["question"] == q]
        print(
            f"\n{q}: {sum(r['got'] == r['expected'] for r in runs)}/{len(runs)} runs passed, ${sum(r['cost'] for r in runs):.2f}"
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
    run(cases, sources, args.repeat, args.jobs, args.results)


if __name__ == "__main__":
    main()
