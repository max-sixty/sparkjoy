#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy>=2.1"]
# ///
"""Asks a checkout's question at each case's turn end, as the plugin's fork
asks it, and scores the verdicts.

    uv run --script evals/run.py [--question-from CHECKOUT ...] [--case ID ...] [--repeat N] [--told TOKENS] [--published]
    uv run --script evals/run.py --report

A case's request is the one the turn's reply answered, with the reply after
it. `$.model.fork` sends that request again with its prompt after the reply,
so a run sends the same straight to the API, with the question `question()`
in `<checkout>/hooks/register.ts` renders for the case's recorded context size
(`question.ts`), or for `--told` tokens. Where the case's session is on this
machine, a run rebuilds the request from its transcript, as the live fork
sends it (`build.fork_request`); elsewhere, or with `--published`, it sends the
one published in the evals' data repository (`sparkjoy_assets.py`), without
the thinking and the words the build took out of it. Each row records which it
sent. A tool call is answered as the fork answers it, with the error the fork
returns, and the request sent again. Each result is appended to a file of
the batch's results, which the batch publishes to that repository's
`evals/results/` when it ends (`sparkjoy_assets.batch`). `--report` prints the table
README.md shows from every batch's results, and for each question asked at
several `--told` sizes, how often it compacted at each.

Runs of one case go in sequence and share one cache write of its context, so
each run after the first costs about a twentieth of it, whatever question it
asks. Cases run `--jobs` at a time.

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
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "model"))
import build  # noqa: E402
import fit  # noqa: E402
import sparkjoy_assets  # noqa: E402

# How long an idle case's session has sat untouched when it is asked: the
# plugin's wait on a one-hour cache (IDLE_MS).
IDLE_MINUTES = 55
# What the fork answers a tool call with.
DENIED = "A model fork cannot use tools"
# The requests a run may make, the first and one after each denied tool call,
# before it gives up.
MAX_REQUESTS = 5


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


def body_of(request: dict, question: str) -> dict:
    """The fork's request: the recorded one, the reply, then the question, with
    the cache breakpoints on the system prompt and the reply."""
    system = [*request["system"]]
    system[-1] = system[-1] | {"cache_control": {"type": "ephemeral"}}
    *messages, reply = request["messages"]
    reply = reply | {
        "content": [
            *reply["content"][:-1],
            reply["content"][-1] | {"cache_control": {"type": "ephemeral"}},
        ]
    }
    params = {k: v for k, v in request["params"].items() if k != "betas"}
    return params | {
        "system": system,
        "tools": request["tools"],
        "messages": [*messages, reply, {"role": "user", "content": question}],
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
        if used["requests"] == MAX_REQUESTS:
            raise RuntimeError(f"still calling tools after {MAX_REQUESTS} requests")
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


def render(source: Path, reply: str, case: dict, told: int | None) -> str:
    args = [
        "bun",
        str(HERE / "question.ts"),
        str(register(source)),
        str(told or case["tokens"]),
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


def label(source: Path, told: int | None = None) -> str:
    """The checkout's branch and commit, e.g. `main@02beed1`, or a file's name,
    with the size the question states where it isn't the case's own, as in
    `main@02beed1-told-900k`."""
    suffix = f"-told-{told // 1000}k" if told else ""
    if source.is_file():
        return source.stem + suffix

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(source), *args], capture_output=True, text=True
        ).stdout.strip()

    return f"{git('rev-parse', '--abbrev-ref', 'HEAD')}@{git('rev-parse', '--short', 'HEAD')}{suffix}"


def request_of(case: dict, published: bool) -> tuple[dict, str] | None:
    """The case's request, and where it came from: rebuilt from the session's
    transcript where it is on this machine, as the live fork sends it, or else
    as published, without what the build took out of it (build.py). None
    where there is neither."""
    if not published:
        try:
            return build.fork_request(build.entries_of(case)), "transcript"
        except LookupError:
            pass
    found = sparkjoy_assets.request(case["id"])
    return (json.loads(gzip.decompress(found)), "published") if found else None


def row_of(
    case: dict, question: str, request: dict, origin: str, text: str, used: dict
) -> dict:
    verdict = parse(text)
    got = None if verdict is None else ("compact" if verdict["compact"] else "keep")
    read, write = used["cache_read_input_tokens"], used["cache_creation_input_tokens"]
    # A run's cache writes last five minutes.
    i, o, w5, _, r = fit.PRICES[request["params"]["model"]]
    row = {
        "case": case["id"],
        "question": question,
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
        "request": origin,
    }
    # The answer's other fields, such as whether the next turns need the
    # transcript's detail, for a question that asks for them.
    if verdict is not None:
        row |= {k: v for k, v in verdict.items() if k not in ("compact", "reason")}
    return row


def run(
    cases: list[dict],
    sources: list[Path],
    told: list[int | None],
    published: bool,
    repeat: int,
    jobs: int,
) -> None:
    labels = [label(source, size) for source in sources for size in told]
    if len(set(labels)) < len(labels):
        sys.exit(
            f"Two questions would share a label: {labels}. Give their files other names."
        )
    auth = credential()
    # Before the cases' threads start, so that none of them clones.
    sparkjoy_assets.fetch()

    def one_case(case: dict) -> list[dict]:
        if (found := request_of(case, published)) is None:
            print(f"SKIP {case['id']}: no transcript or published request", flush=True)
            return []
        request, origin = found
        if request["params"]["model"] not in fit.PRICES:
            print(
                f"SKIP {case['id']}: fit.PRICES has no price for its model", flush=True
            )
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
            b["text"] for b in request["messages"][-1]["content"] if b["type"] == "text"
        )
        bodies = {
            label(source, size): body_of(request, render(source, reply, case, size))
            for source in sources
            for size in told
        }
        rows = []
        # The questions take turns, so drift over a batch falls on each alike.
        for _ in range(repeat):
            for question, body in bodies.items():
                try:
                    text, used = ask(body, headers)
                except (RuntimeError, OSError) as error:
                    print(f"ERROR {case['id']} [{question}]: {error}", flush=True)
                    continue
                row = row_of(case, question, request, origin, text, used)
                results.write(json.dumps(row) + "\n")
                results.flush()
                mark = "PASS" if row["got"] == row["expected"] else "FAIL"
                print(
                    f"{mark} {row['case']} [{row['question']}] expected {row['expected']}, got {row['got']} "
                    f"at {row['context'] // 1000}k (${row['cost']:.2f}): {row['reason']}",
                    flush=True,
                )
                rows.append(row)
        return rows

    try:
        with sparkjoy_assets.batch() as results, ThreadPoolExecutor(jobs) as pool:
            rows = [row for rows in pool.map(one_case, cases) for row in rows]
        print(
            f"{sum(r['got'] == r['expected'] for r in rows)}/{len(rows)} passed, ${sum(r['cost'] for r in rows):.2f}"
        )
    finally:
        # The batch's results, with any an earlier batch didn't publish; a
        # rebuilt request waits for a look first.
        try:
            sparkjoy_assets.publish(sparkjoy_assets.RESULTS)
        except subprocess.CalledProcessError:
            print(
                f"The results weren't published, and wait in {sparkjoy_assets.UNPUBLISHED}."
            )


def column(row: dict) -> str:
    """The question a row asked, marked where it sent a published request."""
    return row["question"] + (
        " (published)" if row.get("request") == "published" else ""
    )


def report(cases: list[dict]) -> None:
    """The results table: per case and question, how many runs matched the
    label, and how many of them thought before answering."""
    rows = [
        json.loads(line) for path in sparkjoy_assets.results() for line in path.open()
    ]
    questions = list(dict.fromkeys(column(r) for r in rows))
    print("| case | kind | context | expected | " + " | ".join(questions) + " |")
    print("|---|---|---|---|" + "---|" * len(questions))
    for case in cases:
        mine = [r for r in rows if r["case"] == case["id"]]
        cells = []
        for q in questions:
            runs = [r for r in mine if column(r) == q]
            passed = sum(r["got"] == case["expected"] for r in runs)
            thought = sum(r["thinking"] > 0 for r in runs)
            cells.append(f"{passed}/{len(runs)} ({thought} thought)" if runs else "")
        print(
            f"| {case['id']} | {case['kind']} | {case['tokens'] // 1000}k | {case['expected']} | "
            + " | ".join(cells)
            + " |"
        )
    # Scored against the labels as they stand, at the cases listed.
    expected = {case["id"]: case["expected"] for case in cases}
    for q in questions:
        runs = [r for r in rows if column(r) == q and r["case"] in expected]
        print(
            f"\n{q}: {sum(r['got'] == expected[r['case']] for r in runs)}/{len(runs)} runs passed, ${sum(r['cost'] for r in runs):.2f}"
        )
    by_size(cases, rows)


def by_size(cases: list[dict], rows: list[dict]) -> None:
    """For each question asked at several `--told` sizes, how many runs at each
    case compacted at each size. Each case holds its work fixed, so a question
    that weighs the context's size compacts more often the larger the size it
    states."""
    told: dict[str, dict[tuple[str, int], list[dict]]] = {}
    for r in rows:
        m = re.fullmatch(r"(.*)-told-(\d+)k(.*)", column(r))
        if m:
            told.setdefault(m[1] + m[3], {}).setdefault(
                (r["case"], int(m[2])), []
            ).append(r)
    for question, runs in told.items():
        sizes = sorted({size for _, size in runs})
        if len(sizes) < 2:
            continue
        print(f"\n{question}, runs that compacted at each size it stated:\n")
        print("| case | " + " | ".join(f"{s}k" for s in sizes) + " |")
        print("|---|" + "---|" * len(sizes))
        for case in cases:
            cells = [runs.get((case["id"], s), []) for s in sizes]
            if any(cells):
                print(
                    f"| {case['id']} | "
                    + " | ".join(
                        f"{sum(r['got'] == 'compact' for r in c)}/{len(c)}" if c else ""
                        for c in cells
                    )
                    + " |"
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
        "--told",
        action="append",
        help="context size the question states, in tokens, or `recorded` for the case's own (default: recorded)",
    )
    parser.add_argument(
        "--published",
        action="store_true",
        help="send the published requests even where the transcripts are on this machine",
    )
    parser.add_argument("--jobs", type=int, default=3, help="cases run at once")
    parser.add_argument(
        "--report", action="store_true", help="print the results table and exit"
    )
    args = parser.parse_args()
    cases = json.loads((HERE / "cases.json").read_text())
    if args.case:
        unknown = set(args.case) - {c["id"] for c in cases}
        if unknown:
            sys.exit(f"Unknown cases: {sorted(unknown)}")
        cases = [c for c in cases if c["id"] in args.case]
    if args.report:
        return report(cases)
    sources = [p.expanduser().resolve() for p in args.question_from or [HERE.parent]]
    told = [None if t == "recorded" else int(t) for t in args.told or ["recorded"]]
    run(cases, sources, told, args.published, args.repeat, args.jobs)


if __name__ == "__main__":
    main()
