# Evals

These evals put sparkjoy's question to the model at 16 turn ends from my
Claude Code sessions on open-source work (Leaf, Worktrunk and tend),
and score its verdict against a label chosen with hindsight, from what the
session did next.

The cases test one claim: the right answer at a natural break depends on how
much the work ahead would carry. A finished piece of work at 700k should
compact, the same kind of moment at 200k–300k shouldn't, and a moment in the
middle of a task should keep at any size.

## Running them

```
uv run --script evals/run.py                                    # this checkout's question, each case once
uv run --script evals/run.py --question-from ../other-checkout  # another checkout's question
uv run --script evals/run.py --question-from a.ts --question-from b.ts  # two variants of register.ts
uv run --script evals/run.py --case leaf-menu-design --repeat 3
uv run --script evals/run.py --report                           # the results table, from results.jsonl
uv run --script evals/check.py                                  # the publishing sweep over the fixtures
```

They need `claude` (tested on 2.1.332), `bun` and `uv`, and a Claude Code login
or `ANTHROPIC_API_KEY`. `--question-from` can be given more than once, and
every run appends a row to `results.jsonl`.

A case writes its whole transcript to the cache once, about $5 per million
tokens on Opus 5.5, so $0.45 to $2.60 a case. Every run of it after that,
whichever question it asks, reads the transcript from the cache for $0.02 to
$0.25. Claude Code caches up to the last content block, so when there is more
than one run, the runner first sends the questions' shared opening (through
the quoted reply) alone, then each question as that opening and the rest of
it in a second block.

## How a run works

`build.py` made each fixture in `fixtures/` from my transcript: the
conversation from the last compaction to the turn's reply, which is what a
resume sends the model, sanitized for publishing.

`run.py` writes the fixture, without the turn's reply, as a session into a
scratch `CLAUDE_CONFIG_DIR`, with every `CLAUDE*` and `ANTHROPIC*` variable
unset, and resumes it with `claude -p --resume <id> --fork-session` on
`claude-opus-5-5` at `xhigh` effort, as the sessions ran, with Claude Code's
default tools under `dontAsk`, which denies any call that would ask permission,
and no MCP servers, skills or instructions.
`question.ts` renders the question by calling `question()` from the chosen
checkout's `hooks/register.ts`, so the evals ask exactly what the plugin asks.
It passes the case's recorded context size as `tokens`, for a question that
takes one. The model's reply is parsed as the plugin parses it.

The plugin's fork sends the turn's last request, which ends before the reply,
with the question after it. Claude Code resumes a session that ends with a tool
result by adding a message of its own after it, "No response requested.", which
the fork doesn't send. So `run.py` passes the child's requests through a local
proxy that drops that message.

A replay differs from the plugin's own fork in these ways:

- **The replayed context is smaller than the recorded one**, 43% to 70% of it
  (the results table's two size columns). The fixture leaves out the model's
  hidden thinking, which is encrypted and so can't be checked, and my
  instructions, skills and tool listings, and Claude Code's `-p` system prompt
  is shorter than an interactive one. A question that states the size is told
  the recorded one; one that doesn't sees only the replayed transcript.
- **The model has Claude Code's default tools**, where the fork has the
  session's, its MCP servers' included, with every call denied.
- **The question comes in two blocks** when a case has more than one run: its
  opening through the quoted reply, then the rest (`run.py` says why).

## Cases

Each case's label, its reason and the evidence for it (what the session did
next) are in `cases.json`.

| case                        | kind | recorded | expected | the moment                                                   |
| --------------------------- | ---- | -------- | -------- | ------------------------------------------------------------ |
| leaf-scaling-closeout       | turn | 742k     | compact  | performance fixes landed and closed out                      |
| leaf-hover-closeout         | turn | 736k     | compact  | reviewed work landed; the rest waits on the user's answers   |
| leaf-followups-merged       | turn | 643k     | compact  | both follow-ups merged, nothing open                         |
| leaf-scroll-fix-merged      | turn | 608k     | compact  | a Firefox scroll fix merged and written up                   |
| worktrunk-release-tagged    | turn | 479k     | compact  | a release tagged, a question about it answered               |
| leaf-ring-test-rewrite-wait | turn | 681k     | keep     | waiting on a test rewrite before the final checks            |
| leaf-send-hover-fix         | turn | 570k     | keep     | mid-iteration on a UI fix the user keeps faulting            |
| leaf-menu-design            | turn | 551k     | keep     | a design exploration just rebuilt, awaiting reaction         |
| tend-eval-rerun-wait        | turn | 519k     | keep     | waiting on an eval rerun to turn into a PR                   |
| tend-sandbox-bind-ask       | turn | 599k     | keep     | asks for a go-ahead before building more of a sandbox design |
| leaf-escape-stack-ask       | turn | 490k     | keep     | asks two design decisions before an experiment branch        |
| leaf-save-icon-merge-ask    | turn | 479k     | compact  | asks whether to open and merge a finished branch's PR        |
| leaf-panzoom-drop-ask       | turn | 403k     | compact  | asks whether to drop a library swap (a reset) or fix it      |
| leaf-data-attrs-merged      | turn | 296k     | keep     | three changes landed: a natural break, but small             |
| worktrunk-repairs-merged    | turn | 210k     | keep     | four repair PRs merged: a natural break, but small           |
| leaf-ui-fixes-idle          | idle | 290k     | compact  | idle 55 minutes after everything landed                      |

The two small natural breaks are the same kind of moment as the compact cases
above them. They are labelled keep because the context after a compaction is about 142k, so
at 210k it saves each later request about 68k while making a cache write of
about 110k and losing detail. The idle case is labelled compact at 290k because its
cache lapses either way.

## Results

From `run.py --report` on 25 November 2026, on Claude Code 2.1.332, with Claude
Code's default tools in each request ([How a run works](#how-a-run-works)).
Each cell counts the runs that matched the label, and in brackets the runs in
which the model thought before it answered. `main@6f2013a-tools` is the
question as it stands: `fork-no-reply` below, reworded, with its answer's
format given as a JSON Schema. `detail-only-tools` is that question without its
sections of costs and the paragraph that asks the model to weigh them, so it
asks only whether the next turns will need detail a summary drops.
`haste-firm-tools` closes the question with "Don't think before you answer.
Reply at once with the JSON object alone: this is a quick judgment, and every
tool is denied for this request." `deliberate-tools` closes it instead with
"Think it over before you answer: what comes next, what it would need from the
transcript, and what keeping and compacting would each cost."
`main@6f2013a-tools` and `haste-firm-tools` ran in two batches at every case
and a third at seven, `detail-only-tools` in two and `deliberate-tools` in one,
with four runs of each case in each batch, all within an hour.

| case                        | recorded | replayed | expected | main@6f2013a-tools | detail-only-tools | haste-firm-tools  | deliberate-tools |
| --------------------------- | -------- | -------- | -------- | ------------------ | ----------------- | ----------------- | ---------------- |
| leaf-scaling-closeout       | 742k     | 538k     | compact  | 8/8 (8 thought)    | 8/8 (8 thought)   | 8/8 (0 thought)   | 4/4 (4 thought)  |
| leaf-hover-closeout         | 736k     | 508k     | compact  | 8/8 (0 thought)    | 8/8 (0 thought)   | 8/8 (0 thought)   | 4/4 (4 thought)  |
| leaf-followups-merged       | 643k     | 421k     | compact  | 8/8 (4 thought)    | 8/8 (6 thought)   | 8/8 (0 thought)   | 4/4 (4 thought)  |
| leaf-scroll-fix-merged      | 608k     | 347k     | compact  | 8/8 (0 thought)    | 8/8 (1 thought)   | 8/8 (0 thought)   | 4/4 (4 thought)  |
| worktrunk-release-tagged    | 479k     | 331k     | compact  | 8/8 (3 thought)    | 8/8 (4 thought)   | 8/8 (0 thought)   | 4/4 (4 thought)  |
| leaf-send-hover-fix         | 570k     | 297k     | keep     | 2/12 (12 thought)  | 8/8 (2 thought)   | 9/12 (5 thought)  | 0/4 (4 thought)  |
| leaf-menu-design            | 551k     | 297k     | keep     | 6/12 (6 thought)   | 8/8 (1 thought)   | 11/12 (1 thought) | 0/4 (4 thought)  |
| tend-eval-rerun-wait        | 519k     | 298k     | keep     | 0/12 (12 thought)  | 7/8 (8 thought)   | 8/12 (4 thought)  | 0/4 (4 thought)  |
| leaf-ring-test-rewrite-wait | 681k     | 372k     | keep     | 1/12 (5 thought)   | 8/8 (0 thought)   | 10/12 (0 thought) | 0/4 (4 thought)  |
| tend-sandbox-bind-ask       | 599k     | 364k     | keep     | 12/12 (11 thought) | 8/8 (8 thought)   | 12/12 (1 thought) | 4/4 (4 thought)  |
| leaf-escape-stack-ask       | 490k     | 233k     | keep     | 8/8 (8 thought)    | 8/8 (1 thought)   | 8/8 (1 thought)   | 4/4 (4 thought)  |
| leaf-save-icon-merge-ask    | 479k     | 270k     | compact  | 11/12 (3 thought)  | 8/8 (6 thought)   | 12/12 (0 thought) | 4/4 (4 thought)  |
| leaf-panzoom-drop-ask       | 403k     | 224k     | compact  | 11/12 (12 thought) | 0/8 (5 thought)   | 1/12 (2 thought)  | 4/4 (4 thought)  |
| worktrunk-repairs-merged    | 210k     | 110k     | keep     | 0/8 (8 thought)    | 0/8 (7 thought)   | 0/8 (8 thought)   | 0/4 (4 thought)  |
| leaf-data-attrs-merged      | 296k     | 166k     | keep     | 0/8 (8 thought)    | 0/8 (7 thought)   | 0/8 (0 thought)   | 0/4 (4 thought)  |
| leaf-ui-fixes-idle (idle)   | 290k     | 174k     | compact  | 8/8 (3 thought)    | 8/8 (8 thought)   | 8/8 (1 thought)   | 4/4 (4 thought)  |

The question as it stands gives one verdict in nearly every run at every case
but the four mid-task ones. There it compacts in 39 of 48 runs, and how often
varies by case: it kept in 6 of 12 runs at leaf-menu-design and in none of 12
at tend-eval-rerun-wait. `detail-only-tools` kept at the mid-task cases in 31
of 32 runs and gave every other case one verdict in every run. In all 128 of
its runs it compacted exactly when it answered that the next turns won't need
the detail. It matches the label at 13 of the 16 cases. It compacts at both
small natural breaks, though sparkjoy asks only from 250k, so it would never
ask at the 210k one, and it keeps at leaf-panzoom-drop-ask ([Turn ends that
stop to ask](#turn-ends-that-stop-to-ask)). `haste-firm-tools` keeps at the
mid-task cases more often than the question as it stands, in 38 of 48 runs,
though not in every run, and `deliberate-tools` compacts at all of them.

### Thinking

Claude Code runs Opus 5.5 with adaptive thinking, so at each request the model
decides whether to think before it answers. The plugin's fork thinks as the
session does, and so does a run here, at `xhigh` effort.

With the costs in the question, the verdict at the four mid-task cases goes
with that decision. Across every question with them on 25 November, 4 of the
115 runs there that thought with tools in the request kept the context, and 89
of the 109 that didn't think kept it. Without tools, none of 53 that thought
kept it, and 84 of 107 that didn't. These runs can't tell whether thinking
leads the model to compact or whether it thinks when it leans that way. Asked
without the costs, the model keeps at those cases whether or not it thinks:
`detail-only-tools` kept in 10 of the 11 runs there that thought and in all 21
that didn't.

The model's answer to `needs_detail` holds steady where its verdict doesn't. At
the mid-task cases, `main@6f2013a-tools` answered that the next turns will need
the detail in 23 of its 32 runs that record the field, yet kept the context in
only 4. So the costs, weighed most when the model thinks, are what move the
verdict there.

How often the model thinks at a case moves with small changes. With tools in
the request, `haste` (`main@6f2013a` closed with "You have no tools here.
Answer straight away, without thinking it over.") kept at leaf-menu-design
without thinking in 4 of 4 runs, then thought and compacted in 4 of 4 a minute
later, in a batch that sent it as two blocks beside three other questions.
Sending the question as one block or two makes no difference overall:
`main@6f2013a-tools-one-block` and `-two-blocks`, run alternately at the
mid-task cases, kept in 6 and 8 of 32 runs. Without tools, at leaf-menu-design
`main@6f2013a` thought in all 4 runs and compacted, while
`fork-no-reply-rerun`, the question of 20 November run again beside it, thought
in none of 8 and kept in all of them. Run again on 25 November, `fork-no-reply`
kept at the mid-task cases in 17 of 32 runs, against 23 of 32, 24 of 32 and 27
of 64 in the three batches of 20 November's afternoon. The last two of those
record output tokens, though not thinking: a keep there came with a median of
about 85, and a compaction with about 340. On 25 November an answer without
thinking took about 85. The batch of that morning that kept in none of 16
recorded no output tokens, so whether its model thought more often isn't known.
Nor does one test pin it on the client: run alternately in a later batch,
Claude Code 2.1.329 and 2.1.330 kept at the mid-task cases in 14 and 13 of 32
runs.

The request's own settings don't settle whether the model thinks. Without
tools, at the four mid-task cases and leaf-panzoom-drop-ask, `main@6f2013a`
sent as one block thought in 6 of 20 runs; with thinking enabled and a budget
of 4,000 tokens (`-thinking-enabled`), in 10 of 20; and at `low` effort
(`-effort-low`), in 9 of 20. Opus 5.5 refuses thinking disabled.

| case                        | expected | fork-no-reply-rerun | main@6f2013a-one-block | main@6f2013a-thinking-enabled | main@6f2013a-effort-low |
| --------------------------- | -------- | ------------------- | ---------------------- | ----------------------------- | ----------------------- |
| leaf-send-hover-fix         | keep     | 8/8 (0 thought)     | 4/4 (0 thought)        | 4/4 (0 thought)               | 4/4 (0 thought)         |
| leaf-menu-design            | keep     | 8/8 (0 thought)     | 2/4 (2 thought)        | 2/4 (2 thought)               | 1/4 (3 thought)         |
| tend-eval-rerun-wait        | keep     | 0/8 (8 thought)     | 3/4 (1 thought)        | 0/4 (4 thought)               | 0/4 (3 thought)         |
| leaf-ring-test-rewrite-wait | keep     | 1/8 (0 thought)     | 0/4 (2 thought)        | 0/4 (3 thought)               | 0/4 (2 thought)         |
| leaf-panzoom-drop-ask       | compact  |                     | 1/4 (1 thought)        | 1/4 (1 thought)               | 1/4 (1 thought)         |

Nor do the words that ask for haste, once tools are in the request. Without
tools, `haste` thought in 9 of its 96 runs, none of them at a mid-task case,
and kept at the mid-task cases in all 32. With tools, it thought in 18 of 60
and kept there in 18 of 32, and `haste-firm-tools` thought in 23 of 156.
`deliberate` thought in 62 of 64 runs without tools and 64 of 64 with them. It
compacts at every case but the two turn ends that stop to ask about detail the
next turn needed. That is close to compacting at every turn end from 250k, and
the [cost model](../docs/cost-model.md) puts the cheapest turn-end threshold
between 210k and 280k, depending on β, when a compaction loses no detail.

Each row of `results.jsonl` records the Claude Code version that ran it from
the afternoon of 20 November, its thinking tokens from 25 November, and its
`needs_detail` from the runs with tools. A question that leaves thinking to the
model compares with another only in runs made in the same batch.

### Without tools

Until 25 November the runner gave the model no tools. These runs, from that
day, ask the same questions without them; `haste` is `main@6f2013a` closed with
"You have no tools here. Answer straight away, without thinking it over.", and
`haste-no-fields` is `haste` asking only for `compact` and `reason`.
`main@6f2013a`, `haste` and `deliberate` ran four times at every case, and
`haste` four more times at the mid-task cases and the ones that stop to ask,
beside `haste-no-fields`, a few minutes later.

| case                        | recorded | replayed | expected | main@6f2013a    | haste           | deliberate      | haste-no-fields |
| --------------------------- | -------- | -------- | -------- | --------------- | --------------- | --------------- | --------------- |
| leaf-scaling-closeout       | 742k     | 516k     | compact  | 4/4 (1 thought) | 4/4 (0 thought) | 4/4 (4 thought) |                 |
| leaf-hover-closeout         | 736k     | 488k     | compact  | 4/4 (4 thought) | 4/4 (0 thought) | 4/4 (4 thought) |                 |
| leaf-followups-merged       | 643k     | 402k     | compact  | 4/4 (0 thought) | 4/4 (0 thought) | 4/4 (2 thought) |                 |
| leaf-scroll-fix-merged      | 608k     | 324k     | compact  | 4/4 (4 thought) | 4/4 (0 thought) | 4/4 (4 thought) |                 |
| worktrunk-release-tagged    | 479k     | 312k     | compact  | 4/4 (2 thought) | 4/4 (0 thought) | 4/4 (4 thought) |                 |
| leaf-send-hover-fix         | 570k     | 273k     | keep     | 4/4 (0 thought) | 8/8 (0 thought) | 0/4 (4 thought) | 4/4 (0 thought) |
| leaf-menu-design            | 551k     | 276k     | keep     | 0/4 (4 thought) | 8/8 (0 thought) | 0/4 (4 thought) | 4/4 (0 thought) |
| tend-eval-rerun-wait        | 519k     | 275k     | keep     | 2/4 (2 thought) | 8/8 (0 thought) | 0/4 (4 thought) | 0/4 (1 thought) |
| leaf-ring-test-rewrite-wait | 681k     | 348k     | keep     | 1/4 (0 thought) | 8/8 (0 thought) | 0/4 (4 thought) | 0/4 (0 thought) |
| tend-sandbox-bind-ask       | 599k     | 344k     | keep     | 4/4 (4 thought) | 8/8 (6 thought) | 4/4 (4 thought) | 4/4 (4 thought) |
| leaf-escape-stack-ask       | 490k     | 214k     | keep     | 4/4 (4 thought) | 8/8 (0 thought) | 4/4 (4 thought) | 4/4 (0 thought) |
| leaf-save-icon-merge-ask    | 479k     | 253k     | compact  | 4/4 (0 thought) | 8/8 (0 thought) | 4/4 (4 thought) | 4/4 (0 thought) |
| leaf-panzoom-drop-ask       | 403k     | 204k     | compact  | 0/4 (0 thought) | 0/8 (0 thought) | 4/4 (4 thought) | 0/4 (0 thought) |
| worktrunk-repairs-merged    | 210k     | 89k      | keep     | 0/4 (0 thought) | 0/4 (0 thought) | 0/4 (4 thought) |                 |
| leaf-data-attrs-merged      | 296k     | 148k     | keep     | 0/4 (4 thought) | 0/4 (3 thought) | 0/4 (4 thought) |                 |
| leaf-ui-fixes-idle (idle)   | 290k     | 154k     | compact  | 4/4 (3 thought) | 4/4 (0 thought) | 4/4 (4 thought) |                 |

## Earlier questions

From `run.py --report` on 16 to 21 November 2026; each cell counts the runs
that matched the label. `main` is the question published at 02beed1;
`sized-question` (cdfc871) adds the context's size, its size after a compaction
and how many requests it takes to pay back; `detail` (0915ee8) says what
decides it; `measured` (1f004a4, 17 November) weighs what keeping costs
against what a compaction loses ([Context size](#context-size));
`model-costs` (75fa52f) is `measured` with its costs from the cost model;
`one-figure` (88ca497) states the compaction request's output as measured and
one figure for the context after a compaction; `noisy` (0465741, 18 November, run
again on 19 November) opens the wasted-request figure with "It's very noisy and
hard to judge, but as some indication"; `goal-first` (b4b3308, 19 November)
opens with the request's goal and lists the costs; `no-fields` is
`goal-first` asking only for `compact` and `reason`. From `fork` on (19
November), the session stops before the reply, as the plugin's fork does
([How a run works](#how-a-run-works)), where every earlier column ran with the
reply at the end of the transcript as well as in the question. `fork` is
`goal-first`; `fork-no-fields` is `no-fields`; `fork-no-reply` is `goal-first`
without the quoted reply, so the model sees no reply at
all; `fork-neither` is `fork-no-reply` asking only for `compact` and `reason`.
`reason-first`, `why-first` and `consider` (20 November) are `fork-no-reply`
asking only for fields the hook reads, as [Fields the hook doesn't
read](#fields-the-hook-doesnt-read) describes. `reply-message` (20 November) is
`fork-no-reply` run with `--keep-reply`, so the session ends with the turn's
reply and the question follows it. `fork-no-reply` ran in four batches, which
kept at the mid-task cases in 6 of 16, 0 of 16, 23 of 32 and 24 of 32 runs
([Thinking](#thinking)). The four cases that stop to ask ran only in the
fourth, which ran as `power-law@929a9bd`, the same question.
A fifth batch, which compared two clients and kept at the mid-task cases in 27
of 64 runs, came after the table.

| case                        | recorded | replayed | expected | main | sized-question | detail | measured | model-costs | one-figure | noisy | goal-first | no-fields | fork | fork-no-fields | fork-no-reply | fork-neither | reply-message |
| --------------------------- | -------- | -------- | -------- | ---- | -------------- | ------ | -------- | ----------- | ---------- | ----- | ---------- | --------- | ---- | -------------- | ------------- | ------------ | ------------- |
| leaf-scaling-closeout       | 742k     | 520k     | compact  | 4/4  | 3/3            | 3/3    | 2/2      | 2/2         | 2/2        | 4/4   | 2/2        | 2/2       | 2/2  |                | 2/2           | 2/2          | 2/2           |
| leaf-hover-closeout         | 736k     | 488k     | compact  | 3/3  | 3/3            | 3/3    | 2/2      | 2/2         | 2/2        | 4/4   | 2/2        | 2/2       | 2/2  |                | 2/2           | 2/2          | 2/2           |
| leaf-followups-merged       | 643k     | 405k     | compact  | 3/3  | 3/3            | 3/3    | 2/2      | 2/2         | 2/2        | 4/4   | 2/2        | 2/2       | 2/2  |                | 2/2           | 2/2          | 2/2           |
| leaf-scroll-fix-merged      | 608k     | 326k     | compact  | 3/3  | 3/3            | 3/3    | 2/2      | 2/2         | 2/2        | 4/4   | 2/2        | 2/2       | 2/2  |                | 2/2           | 2/2          | 2/2           |
| worktrunk-release-tagged    | 479k     | 312k     | compact  | 3/3  | 3/3            | 4/4    | 2/2      | 2/2         | 2/2        | 4/4   | 2/2        | 2/2       | 2/2  |                | 2/2           | 2/2          | 2/2           |
| leaf-ring-test-rewrite-wait | 681k     | 349k     | keep     | 0/3  | 0/3            | 3/3    | 0/2      | 1/2         | 0/2        | 0/4   | 1/2        | 0/2       | 0/4  | 0/4            | 9/24          | 0/4          | 1/8           |
| leaf-send-hover-fix         | 570k     | 278k     | keep     | 0/3  | 0/3            | 4/4    | 0/2      | 0/2         | 1/2        | 0/4   | 0/2        | 0/2       | 0/4  | 0/4            | 18/24         | 0/4          | 8/8           |
| leaf-menu-design            | 551k     | 279k     | keep     | 0/3  | 0/3            | 4/4    | 0/2      | 2/2         | 2/2        | 4/4   | 2/2        | 0/2       | 4/4  | 2/4            | 19/24         | 0/4          | 8/8           |
| tend-eval-rerun-wait        | 519k     | 279k     | keep     | 0/3  | 0/3            | 4/4    | 0/2      | 0/2         | 0/2        | 1/4   | 0/2        | 0/2       | 0/4  | 0/4            | 7/24          | 0/4          | 7/8           |
| leaf-data-attrs-merged      | 296k     | 150k     | keep     | 0/3  | 0/3            | 0/4    | 0/2      | 0/2         | 0/2        | 0/4   | 0/2        | 0/2       | 0/2  |                | 0/2           | 0/2          |               |
| worktrunk-repairs-merged    | 210k     | 89k      | keep     | 0/3  | 0/3            | 0/4    | 0/2      | 0/2         | 0/2        | 0/4   | 0/2        | 0/2       | 0/2  |                | 0/2           | 0/2          |               |
| tend-sandbox-bind-ask       | 599k     |          | keep     |      |                |        |          |             |            |       |            |           |      |                | 8/8           |              | 6/8           |
| leaf-escape-stack-ask       | 490k     |          | keep     |      |                |        |          |             |            |       |            |           |      |                | 8/8           |              | 7/8           |
| leaf-save-icon-merge-ask    | 479k     |          | compact  |      |                |        |          |             |            |       |            |           |      |                | 8/8           |              | 8/8           |
| leaf-panzoom-drop-ask       | 403k     |          | compact  |      |                |        |          |             |            |       |            |           |      |                | 0/8           |              | 0/8           |
| leaf-ui-fixes-idle (idle)   | 290k     | 156k     | compact  | 3/3  | 3/3            | 4/4    | 2/2      | 2/2         | 2/2        | 4/4   | 2/2        | 2/2       | 2/2  |                | 2/2           | 2/2          | 2/2           |

The first two questions said compact in all 73 runs. Their reasons agree: the
context is mostly finished work, and what the next step needs is in the
summary, the files on disk or the transcript. That is true of nearly any long
transcript, so it settled nothing.

`detail` says so, and names what decides it: whether the next turns will need
detail a summary drops. Work iterating on something just shown needs that
detail, and work that reports a result or starts a new task needs only a
summary. It asks for what comes next and
whether that needs the detail before the verdict. It keeps at all four
mid-task cases and still compacts at the six finished ones, the release whose
run is still going included.

It still compacts at the two small natural breaks. Its reasons there never
weigh the cost; they say the next work is new, so a summary covers it. Two
variants that framed the cost differently compacted there too, in all six
runs: one asked for the requests ahead before the next natural break; the
other put the scale of a turn (about 36 requests) beside the payback, and said
that keeping only defers the compaction to a later turn end.

The labels at those two cases deserve a second look:

- **At 296k, a compaction pays by the question's own sum.** It pays back
  after about 30 requests, and a turn in my sessions averages about 36. That case's keep label rests on the next task building on the change
  just landed, not on the cost.
- **At 210k it pays back after about 58 requests**, under two average turns,
  so the keep label holds only if the next stretch of work is short.

The [cost model](../docs/cost-model.md) goes further:
a compaction repays over every request until the context grows back, so at a
natural break both cases should compact, and the most efficient threshold at turn ends
is about 200k.

The mid-task keep labels encode the rule that mid-task moments keep, not an
observed cost: at leaf-ring-test-rewrite-wait the plugin compacted live, and
the session finished the work within the hour.

## Context size

The `detail` question's verdict doesn't change with the size it states. On 17
November each turn-end case ran twice with the question telling the model the
context was 200k, where a compaction pays back after 65 requests, and twice
telling it 950k, where it pays back after 7. Each arm was a copy of
`register.ts` that sets `tokens` at the top of `question()`, passed with
`--question-from`. The verdicts at the two sizes matched in 21 of 22 pairs, and
the one that differed kept at 950k. The reasons mention size only in verdicts
that compact, and at 200k one still called the context very large. Finished
work compacts at every size, and work in progress keeps.

The question's rule explains this. Size reaches the verdict only through its
clause "enough requests will follow to repay it", and from the 400k floor a
compaction pays back within 19 requests, about half an average turn. So whether
the next turns need the detail decides the verdict on its own.

Without the figures below, a version that also states what keeping costs
through a turn, and asks the model to weigh the two, compacted the mid-task
cases in 4 of 8 runs told 300k and in all 8 told 700k.

`measured` is that version with figures from my sessions, as the
[README's prompt](../README.md#the-prompt) shows: what keeping costs through a
median turn of 9 requests and a mean one of 38, and what a compaction cost the
requests after it, about 20k tokens read again and about 6 more wasted
requests, with a note of what those counts miss. At the cases' recorded sizes
it compacts at every turn end, the four mid-task ones at 519k–681k included,
where `detail` kept at all of them. That matches the
[cost model](../docs/cost-model.md), where compacting at every turn
end from about 250k costs least when a compaction loses nothing. Its response to size is noisy: on the mid-task cases it kept in 3 of
8 runs told 450k and in 5 of 8 told 750k. A first wording, which said a
compaction "cost the requests after it little", compacted in 46 of 48 runs.

`model-costs` is `measured` with the costs it states from the cost model
([e36d81d](https://github.com/max-sixty/sparkjoy/commit/e36d81d)): the context
after a compaction, 122k, of which all but 30k takes a cache write, against 142k
for the requests after it, and a payback rounded up, such as 13 requests at
500k where `measured` said 14. It compacts at the same cases, and keeps in 3 of
the 8 mid-task runs (both at leaf-menu-design, one at
leaf-ring-test-rewrite-wait), against none for `measured`. With two runs a
case, that difference is within the noise above (Fisher's exact test, p = 0.2).

`one-figure` states the compaction request's output as measured, about 6k tokens
with its thinking, where `model-costs` gave a 4k summary, and gives one figure
for the context after a compaction, 142k, the files read again after it included. Its
paybacks run about 3% longer, still 13 requests at 500k. It compacts at the
same cases, and also keeps in 3 of the 8 mid-task runs, though not all at the
same cases: both at leaf-menu-design and one at leaf-send-hover-fix.

`noisy` opens the wasted-request figure with "It's very noisy and hard to judge,
but as some indication", as in the cost model. It compacts at the same cases
and keeps in 5 of the 16 mid-task runs over two days: all four at
leaf-menu-design and one at tend-eval-rerun-wait.

`goal-first` opens with "The goal of this request is to decide whether to
compact this session now", then quotes the reply, and lists what a compaction,
keeping the context and lost detail each cost. Run beside `noisy` on 19
November, it keeps in 3 of the 8 mid-task runs, as `noisy` did that day: both
at leaf-menu-design and one at leaf-ring-test-rewrite-wait, where `noisy` kept
once at tend-eval-rerun-wait. Both compact at every other case.

`no-fields` drops `next` and `needs_detail`, which the hook never reads. It
compacts in all 24 runs. At leaf-menu-design, which `model-costs`, `one-figure`,
`noisy` and `goal-first` kept in all 10 of their runs, it compacts in both
(Fisher's exact test, p = 0.015). Over the four mid-task cases it keeps in 0 of
8 runs against 3 for `goal-first` (p = 0.2).

With the session stopped before the reply, `fork` keeps in 4 of the 16 mid-task
runs, all at leaf-menu-design, and `fork-no-fields` in 2, both there (p = 0.65).
Pooled with the runs on 19 November above, the question with the fields keeps
in 7 of 24 mid-task runs against 2 of 24 without them (p = 0.14), and at
leaf-menu-design in 6 of 6 against 2 of 6 (p = 0.06).

`fork-no-reply` gives the same verdicts as `fork` at every finished case, the
idle one and both small breaks, and keeps in 6 of the 16 mid-task runs against
4 (p = 0.7). Quoting the reply changes no verdict the evals can measure: in
each of the four mid-task cases the turn's tool calls and results already show
where the work stands.

`reply-message` kept in 24 of the 32 mid-task runs, and `fork-no-reply`, run in
the same quarter hour, in 23 of 32 (p = 1.0). Ending the session with the
turn's reply changed no verdict the evals can measure, as quoting the reply
didn't. Both compacted at every finished case and the idle one.

`fork-neither` gives the same verdicts as `fork-no-reply` at every finished
case, the idle one and both small breaks, but keeps in none of the 16 mid-task
runs, against 6 (p = 0.02). The fields are the only difference between the
two questions, so asking for them keeps the context mid-task, most of all at
leaf-menu-design. In the runs that stop before the reply, the question kept
there in 8 of 8 runs with the fields and in 2 of 8 without them (p = 0.007).

### Fields the hook doesn't read

The hook reads only `compact` and `reason`. On 20 November three questions
asked for nothing else, each `fork-no-reply` with its answer paragraph changed:

- `reason-first` asks for `reason` before `compact`, as "one short line: what
  most likely comes next, and whether it needs detail a summary drops";
- `why-first` asks for a plain `reason` before `compact`;
- `consider` asks the model, before it answers, to "consider what most likely
  comes next and whether it needs detail a summary drops", then for `compact`
  and `reason`.

Each compacted at every finished case it ran, and at the four mid-task turn
ends they kept in 1, 0 and 0 of 24 runs. `fork-no-reply`, run beside them, kept
in none of its 16. Comparing only runs made in the same batch, the questions
with the fields kept at the mid-task cases in 16 of 64 runs and those without
them in 3 of 96 (p = 0.03, an exact test within batches). In the batch of 20
November's morning neither kept: 0 of 16 with the fields and 1 of 72 without.

The runner sends each question as two text blocks, where the plugin sends one
(`run.py` says why). Sending `fork-no-reply` as one block made no clear
difference at leaf-menu-design and leaf-send-hover-fix: it kept in 1 of 8
runs, against 2 of 8 as two blocks in the same hour.

### Turn ends that stop to ask

Four cases stop where the turn asks the user before going on. At
`tend-sandbox-bind-ask` and `leaf-escape-stack-ask` the next turn drew on detail
the ask doesn't restate, so they keep; at `leaf-save-icon-merge-ask` and
`leaf-panzoom-drop-ask` the ask holds all the next turn needed, and a
compaction keeps the turn's final reply word for word, so they compact. In the
afternoon batch of 20 November, `fork-no-reply` matched three of the four in
all 8 runs. At
`leaf-panzoom-drop-ask` it kept in all 8, with reasons such as "half-done
Panzoom fix with six precise reviewer findings … needs exact detail"; the label
rests on what the user did next, a `/compact` one turn later and then "do #3".

`tend-sandbox-bind-ask`'s turn made no tool calls, so the replayed request
ends on the user's message before it, and the model never sees the ask. Run
with `--keep-reply`, so that it does, the question kept in 6 of 8 runs there,
7 of 8 at `leaf-escape-stack-ask`, and as before at the other two.

## Against the live plugin

Whether the harness answers as the live plugin does is still open. Sanitizing
isn't the difference on the one case tried: on tend-eval-rerun-wait, replaying
the unsanitized transcript (its thinking, instructions and skills back in, 406k
replayed) or giving the model Claude Code's default tools each still compacted,
in four runs.

The live plugin can't settle it yet, because until sparkjoy it wrote only its
compactions into the transcript and sent each keep to a debug log that isn't
kept. From 7 to 15 November it asked at 198 turn ends and compacted at 122. The
transcripts account for the other 76 like this:

| what followed the turn end                                          | turn ends |
| ------------------------------------------------------------------- | --------- |
| the next turn began within 45 seconds, before a verdict could stand | 28        |
| the plugin's files changed and it reloaded within a minute          | 21        |
| a "Compacting" line, but no compaction                              | 3         |
| nothing: the model kept, or the check failed                        | 24        |

So the model said compact at 125 of the at most 149 verdicts that stood. At
four of the 24 silent turn ends, including leaf-scroll-fix-merged, the harness
compacted in 11 of 12 runs. Either it leans toward compacting, or the live
checks there failed. sparkjoy writes every verdict into the transcript, so a
few days of use will give live verdicts to compare the harness against.

## Where this leaves them

The question as it stands gives one verdict in nearly every run at every case
but the four mid-task ones, where it compacts in most runs, against the labels,
at a rate that moves with whether the model thinks. Asked without the costs,
the model keeps there in 31 of 32 runs and gives every other case one verdict,
and its verdict is then its answer to whether the next turns will need the
detail. Told to think it over, it compacts at the mid-task cases and at
leaf-panzoom-drop-ask. These cases can't say which the plugin should ask. At
the mid-task cases the labels say keep and the cost model says compact, and at
leaf-panzoom-drop-ask both say compact.

Some labels are themselves uncertain. Each records what the session did next,
which needn't be what it needed: at three of the four mid-task cases the next
work rested on files, a branch or a result that arrives anyway, and at
`leaf-panzoom-drop-ask` I compacted and the work went through, though the turn
stopped with a fix half done. The labels, and the question with them, are
working guesses to revise as live verdicts show what each kind of turn end
loses.

The mid-task keep labels encode a rule, that work in progress
keeps, rather than a measured cost, while the
[cost model](../docs/cost-model.md) finds compacting at any turn end
from about 250k cheapest when a compaction loses nothing. What a compaction
loses is very noisy and hard to judge, but as some indication, my
sessions had about 6 more wasted requests than usual after one.

Live verdicts graded by what followed each one can settle it, since they would
measure the detail lost at each kind of turn end. Every verdict logged before
25 November came from a question that left thinking to the model, so the
comparison waits for verdicts under the question as it stands.

## Fixtures

The 16 fixtures take 4.4 MB, gzipped. Before publishing, `build.py` removed:

- everything the harness injected rather than the conversation said: system
  reminders, instruction files, memory, skill listings, skill and command
  bodies (a placeholder says one was loaded), and environment, tool-listing
  and permission attachments;
- the hidden thinking blocks, and images (a placeholder marks each);
- home paths (`~` instead), email addresses (`user@example.com`), credentials
  of every common form, local page tokens and private hostnames;
- any tool output naming my work outside open source, replaced by
  a placeholder;
- every field of an entry but those a resume reads.

`check.py` fails on any home path, email address, credential, private
hostname or phone number left in a fixture, on text read out of Claude Code's
own binary, and on any word or phrase from a
list of my private terms, which it holds only as hashes so that it
doesn't publish them itself. It passes on the fixtures as committed.
