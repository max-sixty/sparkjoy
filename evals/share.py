#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy>=2.1", "matplotlib>=3.9"]
# ///
"""Asks the model, at each case's turn end, how much of its context might be
helpful for the work ahead, and checks the answer against what that work used.

    uv run --script evals/share.py [--case ID ...] [--repeat N] [--jobs N]
    uv run --script evals/share.py --build     # the made-up cases' requests, to publish
    uv run --script evals/share.py --report    # the tables, and the chart to publish

Whether compacting pays turns on that share: what the work ahead uses has to
be read back after a compaction, and each token read back is written to the
cache again. A run asks for it with `SHARE`, after the case's request as the
fork sends it (`run.body_of`), alone, since the plugin's question asks for no
share yet.

Two kinds of case check the answer:

- the turn ends in `cases.json`, but for the idle one, since the question
  says the turn just ended. `parts.py` cuts the request into its parts, and
  the same model, shown what the session did next (`next_work`), labels each
  part needed, helpful or neither (`LABEL`), in `RUNS` runs. Only I can
  label a case, since labelling reads my transcripts. Each label names the
  request and a hash of the question it was asked (`shown`), which holds
  the next work and the parts, and a case labelled on both is not labelled
  again;
- the turn ends `contrived.py` makes up, whose share is known by
  construction, from a turn end just after loading the skills the next step
  needs to one after the work moved on to something else.

Every run sends the published request (`sparkjoy_assets.py`), so the labels
name the parts of the request anyone can fetch. Each run's row goes to a
batch of its own, published to `evals/share/` in sparkjoy-assets when the
batch ends: the parts as kind and tokens, the labels' part ids, and the
share's answer. What the session did next, and what each part holds, stay
in `.tmp/share/`, where a labelled case's file shows them beside the labels
and the labeller's reasons. `--report` prints, for each case, the model's
share in each run, the shares the labels give, and the same of what a
compaction drops, and draws the request of each labelled case, its parts
coloured by their labels, light and dark, left waiting to publish.
"""

import argparse
import hashlib
import http.client
import json
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "model"))
import build  # noqa: E402
import charts  # noqa: E402
import contrived  # noqa: E402
import fit  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import parts  # noqa: E402
import run  # noqa: E402
import sparkjoy_assets  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

RUNS = 2  # label runs per case
NEXT_REQUESTS = 80  # requests of the session's next work the labeller is shown
DIRECTORY = "evals/share"
LOCAL = ROOT / ".tmp/share"

# TODO: the share should leave out what comes back after a compaction anyway,
# the system prompt, tools and instruction files. The cost model's K, the
# context after a compaction, already prices them, so counting them in the
# share charges them twice and leans the verdict toward keeping. The wording
# leaves that out for simplicity while the question is being pushed to keep
# more.
SHARE = """Estimate how much of this session's context might possibly be helpful for the work ahead, for deciding whether to compact it now. This request comes from the sparkjoy plugin, not from the user, and a program reads your answer.

Your turn just ended, with {size} tokens of context. Compacting replaces the transcript with a summary of about 5k tokens. Weigh:

- *For compacting: later requests read less.* Every later request stops reading the parts of this context that the work ahead won't use.
- *Against: what the work uses costs 40 times as much to read back.* Whatever the work ahead does use, such as files, skills or what was said earlier in the transcript, has to be read back once, and each token read back is written to the cache again, at 40 times the price of reading it from the cache.
- *Against: the summary loses detail.* The more complicated the work, the more it loses: exact output, what was tried and failed, a design or fix still taking shape. Weigh that against compacting in the middle of something.
- *Against: the summary costs something to write*, about as much as reading 500k tokens from the cache.
- *Against: it takes about a minute.* Weigh that if the user is waiting on the next turn.

**So the decision turns on the share of this context that might possibly be helpful for the work ahead.**

Generally models underweigh the chance of context being helpful. So lean toward counting it as helpful if there's a foreseeable case where it could be.

Answer with nothing but one JSON object that matches this schema, filling its fields in the order listed:

```json
{{
  "type": "object",
  "properties": {{
    "next": {{"type": "string", "description": "what most likely comes next, in a few words"}},
    "helpful": {{"type": "integer", "description": "the percentage of this context that might possibly be helpful for the work ahead, from 0 to 100"}}
  }},
  "required": ["next", "helpful"],
  "additionalProperties": false
}}
```"""

LABEL = """This is not a request to continue the work. It comes from the sparkjoy plugin, which is measuring which parts of this context the work after this turn end used, to check its estimates. A program reads your answer.

Below is what the session actually did next, taken from its transcript, and then the parts of the context above, numbered in order, each with its kind, its size in tokens and its opening words.

Judge each part by the work that followed:
- "needed": the next work used it, or would have gone wrong or had to fetch it again without it. Count what the work relied on without reading it again, such as the state of a fix, a decision taken, or what the user asked for, as well as what it read.
- "helpful": it would have helped the next work, such as by saving a lookup or giving background to a decision, but the work could have done without it.
- Every other part is not needed.

What the session did next:

{next}

The parts:

{parts}

Answer with nothing but one JSON object: {{"needed": [part ids], "helpful": [part ids], "why": {{"<part id>": "a few words on what the next work used it for"}}}}, with "why" for the 20 largest parts you marked needed or helpful."""


def share_question(tokens: int) -> str:
    return SHARE.format(size=f"{round(tokens / 1000)}k")


def listing(request_parts: list[dict]) -> str:
    """The parts as the labeller is shown them, each by its kind of block."""
    return "\n".join(
        f"#{p['id']} {p['block']}, {p['tokens']} tokens: {p['desc'][:170]}"
        for p in request_parts
    )


def next_work(case: dict) -> list[str]:
    """What the session did over its `NEXT_REQUESTS` requests after the turn
    end: what the user typed, and the model's text and calls. It runs on
    through a compaction, since what the work reads back after one is what
    the compaction cost it."""
    path = next(fit.PROJECTS.glob(f"*/{case['source']['session']}.jsonl"), None)
    if path is None:
        raise LookupError(f"no transcript of session {case['source']['session']}")
    lines, seen = [], set()
    with path.open() as f:
        for n, raw in enumerate(f):
            if n <= case["source"]["line"]:
                continue
            e = json.loads(raw)
            if e.get("type") == "system" and e.get("subtype") == "compact_boundary":
                lines.append(f"[{e.get('timestamp', '')[:16]}] COMPACTED")
                continue
            if e.get("isSidechain") or e.get("isCompactSummary"):
                continue
            ts = e.get("timestamp", "")[:16]
            if e.get("type") == "user" and not e.get("isMeta"):
                c = e["message"]["content"]
                texts = (
                    [c]
                    if isinstance(c, str)
                    else [b.get("text") or "" for b in c if b.get("type") == "text"]
                )
                lines += [
                    f"[{ts}] USER: {t[:600]}"
                    for t in texts
                    if t and not t.startswith("<")
                ]
            elif e.get("type") == "assistant":
                seen.add(e["message"].get("id"))
                if len(seen) > NEXT_REQUESTS:
                    lines.append("[…the session went on]")
                    break
                for b in e["message"].get("content") or []:
                    if b.get("type") == "text" and b.get("text"):
                        lines.append(f"[{ts}] ASSISTANT: {b['text'][:400]}")
                    elif b.get("type") == "tool_use":
                        lines.append(f"[{ts}] CALL {parts.describe_call(b)}")
    return lines


def count(request: dict, headers: dict) -> int:
    """The tokens of `request`'s context, by the API's count."""
    body = run.body_of(request, ".")
    body = {
        k: body[k]
        for k in ("model", "system", "tools", "messages", "thinking")
        if k in body
    }
    conn = http.client.HTTPSConnection("api.anthropic.com", timeout=300)
    conn.request(
        "POST", "/v1/messages/count_tokens", json.dumps(body).encode(), headers
    )
    response = conn.getresponse()
    data = response.read()
    conn.close()
    if response.status != 200:
        raise RuntimeError(f"count {response.status}: {data[:300]!r}")
    return json.loads(data)["input_tokens"]


def sizes(request: dict, headers: dict) -> tuple[int, int, int]:
    """The request's tokens, its system prompt's and its tools'."""
    stub = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
    ]
    blank = [{"type": "text", "text": "."}]
    total = count(request, headers)
    with_tools = count(request | {"messages": stub}, headers)
    with_system = count(request | {"messages": stub, "tools": []}, headers)
    bare = count(request | {"messages": stub, "tools": [], "system": blank}, headers)
    return total, with_system - bare, with_tools - with_system


def answer(text: str) -> dict | None:
    try:
        value = json.loads(text[text.index("{") : text.rindex("}") + 1])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def ids(value) -> list[int] | None:
    """Part ids as the labeller gave them, such as 12 or "#12"."""
    if not isinstance(value, list):
        return None
    try:
        return sorted({int(str(v).lstrip("#")) for v in value})
    except ValueError:
        return None


def rows() -> list[dict]:
    return [
        json.loads(line)
        for path in sparkjoy_assets.results(DIRECTORY)
        for line in path.open()
    ]


def pointers() -> dict[str, str]:
    return json.loads(sparkjoy_assets.POINTERS.read_text())


def run_cases(names: list[str], repeat: int, jobs: int) -> None:
    cases = {c["id"]: c for c in json.loads((HERE / "cases.json").read_text())}
    auth = run.credential()
    sparkjoy_assets.fetch()
    # The share's question, named by its wording.
    question = hashlib.sha256(SHARE.encode()).hexdigest()[:8]
    earlier = rows()
    spent = []

    def labelled(case: str, digest: str, shown: str) -> int:
        return sum(
            r["kind"] == "label"
            and r["case"] == case
            and r["request"] == digest
            and r.get("shown") == shown
            and r["needed"] is not None
            for r in earlier
        )

    def write(row: dict) -> None:
        results.write(json.dumps(row) + "\n")
        results.flush()
        spent.append(row.get("cost", 0))

    def one(name: str) -> None:
        digest = pointers().get(name)
        found = run.request_of({"id": name}, True)
        if found is None or digest is None:
            print(f"SKIP {name}: no published request", flush=True)
            return
        request, _ = found
        model = request["params"]["model"]
        if model not in fit.PRICES:
            print(f"SKIP {name}: fit.PRICES has no price for {model}", flush=True)
            return
        headers = run.headers_of(request, auth)
        x, system, tools = sizes(request, headers)
        base = {"case": name, "request": digest}
        if case := cases.get(name):
            try:
                then = next_work(case)
            except LookupError as error:
                print(f"NO LABELS {name}: {error}", flush=True)
            else:
                cut = parts.sized(parts.parts_of(request), system, tools, x)
                asked = LABEL.format(next="\n".join(then), parts=listing(cut))
                shown = hashlib.sha256(asked.encode()).hexdigest()[:12]
                if labelled(name, digest, shown) < RUNS:
                    label(
                        base | {"shown": shown},
                        request,
                        headers,
                        x,
                        cut,
                        asked,
                        then,
                        write,
                    )
        for _ in range(repeat):
            try:
                text, used = run.ask(run.body_of(request, share_question(x)), headers)
            except (RuntimeError, OSError) as error:
                print(f"ERROR {name} [share]: {error}", flush=True)
                continue
            got = answer(text) or {}
            helpful = (
                got.get("helpful") if isinstance(got.get("helpful"), int) else None
            )
            write(
                base
                | {
                    "kind": "share",
                    "question": question,
                    "at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "context": x,
                    "helpful": helpful,
                    "next": got.get("next") if helpful is not None else text[:300],
                    "cache_read": used["cache_read_input_tokens"],
                    "output": used["output_tokens"],
                    "thinking": used["thinking"],
                    "cost": round(run.cost_of(used, model), 4),
                }
            )
            print(
                f"{name} share {helpful}% at {x // 1000}k (${run.cost_of(used, model):.2f})",
                flush=True,
            )

    try:
        with (
            sparkjoy_assets.batch(DIRECTORY) as results,
            ThreadPoolExecutor(jobs) as pool,
        ):
            list(pool.map(one, names))
        print(f"${sum(spent):.2f}")
    finally:
        sparkjoy_assets.publish(sparkjoy_assets.RESULTS)


def label(base, request, headers, x, cut, asked, then, write) -> None:
    """Label the request's parts `cut` by what the session did next (`then`),
    asking `asked` `RUNS` times, and keep what each part holds and the
    labeller's reasons in `LOCAL`."""
    model = request["params"]["model"]
    write(
        base
        | {
            "kind": "parts",
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "context": x,
            "parts": [[p["kind"], p["tokens"]] for p in cut],
        }
    )
    body = run.body_of(request, asked)
    runs = []
    for _ in range(RUNS):
        try:
            text, used = run.ask(body, headers)
        except (RuntimeError, OSError) as error:
            print(f"ERROR {base['case']} [label]: {error}", flush=True)
            continue
        got = answer(text) or {}
        needed, helpful = ids(got.get("needed")), ids(got.get("helpful"))
        ok = needed is not None and helpful is not None
        runs.append(
            {
                "needed": needed,
                "helpful": helpful,
                "why": got.get("why"),
                "text": None if ok else text[:2000],
            }
        )
        write(
            base
            | {
                "kind": "label",
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
                "needed": needed if ok else None,
                "helpful": helpful if ok else None,
                "output": used["output_tokens"],
                "thinking": used["thinking"],
                "cost": round(run.cost_of(used, model), 4),
            }
        )
        if ok:
            got = parts.scored([(p["kind"], p["tokens"]) for p in cut], [runs[-1]])
            print(
                f"{base['case']} labels: needed {got['needed'][0]}%, needed or helpful {got['helpful'][0]}% "
                f"(${run.cost_of(used, model):.2f})",
                flush=True,
            )
        else:
            print(f"{base['case']} labels unreadable: {text[:200]!r}", flush=True)
    LOCAL.mkdir(parents=True, exist_ok=True)
    (LOCAL / f"{base['case']}-{base['request']}.json").write_text(
        json.dumps(
            {**base, "context": x, "parts": cut, "next": then, "runs": runs}, indent=1
        )
    )


def build_contrived() -> None:
    """Leave each made-up case's request waiting to publish."""
    found = run.request_of({"id": contrived.BASE}, True)
    if found is None:
        sys.exit(f"no published request of {contrived.BASE}")
    skill = contrived.skill_dir()
    for name in contrived.CASES:
        build.write(name, contrived.request(name, found[0], skill))
    print("Publish them with `uv run --script model/sparkjoy_assets.py publish`.")


def span(values: list[float]) -> str:
    if not values:
        return ""
    lo, hi = round(min(values)), round(max(values))
    return f"{lo}%" if lo == hi else f"{lo}–{hi}%"


def report() -> None:
    every = rows()
    digests = pointers()
    cases = [
        c for c in json.loads((HERE / "cases.json").read_text()) if c["kind"] == "turn"
    ]

    def mine(name, kind):
        return [
            r
            for r in every
            if r["case"] == name
            and r["kind"] == kind
            and r["request"] == digests.get(name)
        ]

    questions = list(
        dict.fromkeys(r["question"] for r in every if r["kind"] == "share")
    )
    print(
        "| case | context | "
        + " | ".join(f"model's share ({q})" for q in questions)
        + " | needed | needed or helpful | of what a compaction drops: needed | needed or helpful |"
    )
    print("|---|---|" + "---|" * len(questions) + "---|---|---|---|")
    drawn, kinds = [], []
    for case in cases:
        name = case["id"]
        shares = mine(name, "share")
        # The latest labelling: its parts, and the labels made with the same
        # question.
        cut = [r for r in mine(name, "parts") if r.get("shown")][-1:]
        runs = [
            r
            for r in mine(name, "label")
            if cut and r.get("shown") == cut[0]["shown"] and r["needed"] is not None
        ]
        context = (cut or shares or [None])[0]
        cells = [
            span(
                [
                    r["helpful"]
                    for r in shares
                    if r["question"] == q and r["helpful"] is not None
                ]
            )
            for q in questions
        ]
        scores = ["", "", "", ""]
        if cut and runs:
            got = parts.scored(cut[0]["parts"], runs)
            scores = [
                span(got["needed"]),
                span(got["helpful"]),
                f"{got['dropped_needed']:.0f}%",
                f"{got['dropped_helpful']:.0f}%",
            ]
            # The chart shows the share the latest question gave.
            model = [
                r["helpful"]
                for r in shares
                if r["question"] == questions[-1] and r["helpful"] is not None
            ]
            drawn.append((name, cut[0]["parts"], got, model))
            kinds.append(got["kinds"])
        size = (
            f"{context['context'] // 1000}k"
            if context
            else f"{case['tokens'] // 1000}k"
        )
        print(f"| {name} | {size} | " + " | ".join(cells + scores) + " |")
    if kinds:
        print(
            f"\nBy kind, the mean over the {len(kinds)} labelled cases, as percentages of the context:\n"
        )
        print("| kind | size | needed | needed or helpful |")
        print("|---|---|---|---|")
        for g in parts.ORDER:
            size, need, helps = (
                sum(k[g][f] for k in kinds) / len(kinds)
                for f in ("size", "needed", "helpful")
            )
            print(f"| {g} | {size:.1f}% | {need:.1f}% | {helps:.1f}% |")
    # Each question's share at the made-up cases, with how many of its runs
    # fell in the range the case should get.
    print(
        "\n| made-up case | the turn end | context | should be | "
        + " | ".join(f"model's share ({q})" for q in questions)
        + " |"
    )
    print("|---|---|---|---|" + "---|" * len(questions))
    for name, made in contrived.CASES.items():
        shares = [r for r in mine(name, "share") if r["helpful"] is not None]
        lo, hi = made["should"]
        cells = []
        for q in questions:
            got = [r["helpful"] for r in shares if r["question"] == q]
            inside = sum(lo <= v <= hi for v in got)
            cells.append(f"{span(got)} ({inside}/{len(got)} in range)" if got else "")
        size = f"{shares[0]['context'] // 1000}k" if shares else ""
        print(
            f"| {name} | {made['what']} | {size} | {lo}–{hi}% | "
            + " | ".join(cells)
            + " |"
        )
    spent = sum(r.get("cost", 0) for r in every)
    print(f"\n{len(every)} rows, ${spent:.2f} in all")
    if drawn:
        draw(drawn)


# Where the columns of shares right of the bars end, in fractions of the plot's width.
COLUMNS = (1.08, 1.19)


def draw(cases: list[tuple[str, list, dict, list[int]]]) -> None:
    """Each labelled case's request as a bar, its parts coloured by their
    labels, with the model's share and the labels' share needed or helpful
    beside it, each the mean of its runs; light and dark, left waiting to
    publish."""
    strength = {"needed": 0.85, "helpful": 0.5, "one run": 0.25, "not needed": 0.08}
    names = {
        "needed": "needed",
        "helpful": "helpful",
        "one run": "needed or helpful in one run",
        "not needed": "not needed",
    }
    with tempfile.TemporaryDirectory() as scratch:
        for theme, t in charts.THEMES.items():
            colours = {
                k: charts.tint(t["ink"], t["page"], v) for k, v in strength.items()
            }
            fig, ax = plt.subplots(figsize=(8.4, 0.42 * len(cases) + 1.3))
            charts.style(ax, t)
            ax.grid(axis="y", visible=False)
            ax.grid(axis="x", color=t["grid"], lw=0.8)
            for row, (_, cut, got, model) in enumerate(cases):
                segments, at = [], 0
                for (_, tokens), label in zip(cut, got["labels"], strict=True):
                    if segments and segments[-1][2] == label:
                        segments[-1][1] += tokens
                    else:
                        segments.append([at, tokens, label])
                    at += tokens
                ax.broken_barh(
                    [(a, w) for a, w, _ in segments],
                    (row - 0.34, 0.68),
                    facecolors=[colours[k] for _, _, k in segments],
                    linewidth=0,
                )
                mean = sum(got["helpful"]) / len(got["helpful"])
                said = f"{sum(model) / len(model):.0f}%" if model else "–"
                for x, text in zip(COLUMNS, (said, f"{mean:.0f}%"), strict=True):
                    ax.annotate(
                        text,
                        (x, row),
                        xycoords=("axes fraction", "data"),
                        ha="right",
                        va="center",
                        fontsize=9,
                        color=t["ink"],
                    )
            for x, text in zip(COLUMNS, ("model", "hindsight"), strict=True):
                ax.annotate(
                    text,
                    (x, -0.85),
                    xycoords=("axes fraction", "data"),
                    ha="right",
                    va="center",
                    fontsize=9,
                    color=t["muted"],
                )
            ax.set_yticks(
                range(len(cases)), [name for name, *_ in cases], color=t["ink"]
            )
            ax.set_ylim(len(cases) - 0.5, -1.2)
            ax.xaxis.set_major_formatter(charts.tokens)
            ax.set_xlim(0, None)
            ax.set_xlabel("tokens of context, in order", color=t["muted"], fontsize=10)
            ax.legend(
                handles=[Patch(color=colours[k], label=names[k]) for k in strength],
                loc="lower left",
                bbox_to_anchor=(0, 1.01),
                ncols=4,
                frameon=False,
                fontsize=9,
                labelcolor=t["ink"],
                handlelength=1,
            )
            path = Path(scratch) / f"share-parts-{theme}.svg"
            charts.save(fig, path)
            print(f"waiting to publish: {sparkjoy_assets.image(path)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--case",
        action="append",
        help="case id to run (default: every turn end and made-up case)",
    )
    parser.add_argument(
        "--repeat", type=int, default=2, help="times to ask the share at each case"
    )
    parser.add_argument("--jobs", type=int, default=3, help="cases run at once")
    parser.add_argument(
        "--build",
        action="store_true",
        help="make the made-up cases' requests, to publish",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="print the tables, draw the chart, and exit",
    )
    args = parser.parse_args()
    if args.build:
        return build_contrived()
    if args.report:
        return report()
    turns = [
        c["id"]
        for c in json.loads((HERE / "cases.json").read_text())
        if c["kind"] == "turn"
    ]
    known = [*turns, *contrived.CASES]
    if args.case and (unknown := set(args.case) - set(known)):
        sys.exit(f"Unknown cases: {sorted(unknown)}")
    run_cases(args.case or known, args.repeat, args.jobs)


if __name__ == "__main__":
    main()
