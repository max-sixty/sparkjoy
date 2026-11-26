# Evals

These evals put sparkjoy's question to the model at 13 turn ends from my
Claude Code sessions on open-source work (Leaf, Worktrunk and tend), and score
its verdict against a label chosen with hindsight, from what the session did
next.

The cases test one claim: the right answer at a natural break depends on how
much the work ahead would carry. A finished piece of work at 700k should
compact, the same kind of moment at 200k–300k shouldn't, and a moment in the
middle of a task should keep at any size.

## Running them

```
uv run --script evals/run.py                                    # this checkout's question, each case once
uv run --script evals/run.py --question-from ../other-checkout  # another checkout's question
uv run --script evals/run.py --question-from a.ts --question-from b.ts  # two variants of register.ts
uv run --script evals/run.py --case leaf-menu-design --repeat 4
uv run --script evals/run.py --report                           # the results table, from results.jsonl
```

They need `bun` and `uv`, a Claude Code login or `ANTHROPIC_API_KEY`, and my
transcripts, so only I can run them. `--question-from` can be given more than
once, and every run appends a row to `results.jsonl`.

A case's first run writes its whole context to the prompt cache, $1 to $3.70 at
$5 per million tokens on Opus 5.5. Every run of it after that, whichever
question it asks, reads the context from the cache for about $0.12.

## How a run works

The plugin asks through `$.model.fork`, which sends the main thread's last
request again (its parameters, system prompt, tools and messages), then the
turn's reply, then the question as one user message. The fork thinks as the
session does, at `xhigh` effort on Opus 5.5, and answers any tool call with the
error "A model fork cannot use tools", after which the model answers in a
further request.

Claude Code records each request a session sends in its transcript:
`api-request` lines hold the parameters and the hashes of the messages,
`api-request-shape` lines the system prompt and tools, and `api-request-blob`
lines the messages the transcript's own lines don't hold. A case is a line of
the turn's reply in one of my sessions (`cases.json`). `run.py` finds the
request that reply answered, rebuilds its messages from the blobs and the
transcript's lines, checks each against the hash the record gives it, adds the
reply and the question, and sends that straight to the API. `question.ts`
renders the question by calling `question()` from the chosen checkout's
`hooks/register.ts`, for the case's recorded context size, so the evals ask
exactly what the plugin asks. The model's reply is parsed as the plugin parses
it.

I checked the rebuilt request against a fork's captured live, on 25 November
2026 with Claude Code 2.1.332: a session with a mod that forks once at its first
turn end, its requests recorded as the SDK serialized them. The rebuilt request
matched the live fork's in its parameters, system prompt, tools and every
message. Sent to the API, it read from the prompt cache the same 66,496 tokens
the live fork had, so the API found the same bytes up to the question.

Until this change the runner instead resumed a sanitized copy of each
transcript with `claude -p --resume`. That request differed from the fork's:
Claude Code's `-p` system prompt, no instructions, skills or reminders of mine,
so 43% to 70% of the recorded context, no turn's reply, and the environment
Claude Code adds on resuming. The same question in the same hour thought before
answering in 33 of 52 of those runs, and in none of 52 as the fork asks it, so
the earlier findings about thinking described that harness more than the
plugin. Its results and fixtures are in the history at 60bae01.

## Cases

Each case's label, its reason and the evidence for it (what the session did
next) are in `cases.json`.

| case                        | kind | context | expected | the moment                                                 |
| --------------------------- | ---- | ------- | -------- | ---------------------------------------------------------- |
| leaf-scaling-closeout       | turn | 742k    | compact  | performance fixes landed and closed out                    |
| leaf-hover-closeout         | turn | 736k    | compact  | reviewed work landed; the rest waits on the user's answers |
| leaf-followups-merged       | turn | 643k    | compact  | both follow-ups merged, nothing open                       |
| leaf-scroll-fix-merged      | turn | 608k    | compact  | a Firefox scroll fix merged and written up                 |
| worktrunk-release-tagged    | turn | 479k    | compact  | a release tagged, a question about it answered             |
| leaf-ring-test-rewrite-wait | turn | 681k    | keep     | waiting on a test rewrite before the final checks          |
| leaf-send-hover-fix         | turn | 570k    | keep     | mid-iteration on a UI fix the user keeps faulting          |
| leaf-menu-design            | turn | 551k    | keep     | a design exploration just rebuilt, awaiting reaction       |
| tend-eval-rerun-wait        | turn | 519k    | keep     | waiting on an eval rerun to turn into a PR                 |
| leaf-save-icon-merge-ask    | turn | 479k    | compact  | asks whether to open and merge a finished branch's PR      |
| leaf-data-attrs-merged      | turn | 296k    | keep     | three changes landed: a natural break, but small           |
| worktrunk-repairs-merged    | turn | 210k    | keep     | four repair PRs merged: a natural break, but small         |
| leaf-ui-fixes-idle          | idle | 290k    | compact  | idle 55 minutes after everything landed                    |

The two small natural breaks are the same kind of moment as the compact cases
above them. They are labelled keep because the context after a compaction is
about 142k, so at 210k it saves each later request about 68k while making a
cache write of about 110k and losing detail. The idle case is labelled compact
at 290k because its cache lapses either way.

Three earlier cases came from sessions that predate the request records, so no
run can send them what the fork sent: two turn ends that stop to ask, where the
next turn needed detail the ask leaves out, and one that stops to ask where the
ask holds all the next turn needed.

## Results

From `run.py --report` on 25 November 2026. Each cell counts the runs
that matched the label, and in brackets those in which the model thought before
it answered. `main@2e1755e` is the question published until this change, which
states each figure behind the costs and lists what a compaction, keeping the
context and lost detail each cost. `summarized` is the question as it stands:
the context's size and the requests a compaction takes to pay for itself, what
a summary drops and which work needs it, then "Weigh the saving against what
losing that detail would cost the next turns." `summarized-no-fields` asks
only for `compact` and `reason`. The three ran in one batch, four runs of each
case each, taking turns.

| case                        | context | expected | main@2e1755e    | summarized      | summarized-no-fields |
| --------------------------- | ------- | -------- | --------------- | --------------- | -------------------- |
| leaf-scaling-closeout       | 742k    | compact  | 4/4 (0 thought) | 4/4 (0 thought) | 4/4 (0 thought)      |
| leaf-hover-closeout         | 736k    | compact  | 4/4 (0 thought) | 4/4 (0 thought) | 4/4 (0 thought)      |
| leaf-followups-merged       | 643k    | compact  | 4/4 (0 thought) | 4/4 (0 thought) | 4/4 (0 thought)      |
| leaf-scroll-fix-merged      | 608k    | compact  | 4/4 (0 thought) | 4/4 (0 thought) | 4/4 (0 thought)      |
| worktrunk-release-tagged    | 479k    | compact  | 4/4 (0 thought) | 4/4 (0 thought) | 4/4 (4 thought)      |
| leaf-send-hover-fix         | 570k    | keep     | 0/4 (0 thought) | 4/4 (0 thought) | 1/4 (3 thought)      |
| leaf-menu-design            | 551k    | keep     | 0/4 (0 thought) | 4/4 (0 thought) | 1/4 (3 thought)      |
| tend-eval-rerun-wait        | 519k    | keep     | 0/4 (0 thought) | 2/4 (2 thought) | 0/4 (4 thought)      |
| leaf-ring-test-rewrite-wait | 681k    | keep     | 0/4 (0 thought) | 3/4 (1 thought) | 1/4 (3 thought)      |
| leaf-save-icon-merge-ask    | 479k    | compact  | 4/4 (0 thought) | 4/4 (0 thought) | 4/4 (2 thought)      |
| worktrunk-repairs-merged    | 210k    | keep     | 0/4 (0 thought) | 0/4 (0 thought) | 0/4 (0 thought)      |
| leaf-data-attrs-merged      | 296k    | keep     | 0/4 (0 thought) | 0/4 (0 thought) | 0/4 (0 thought)      |
| leaf-ui-fixes-idle (idle)   | 290k    | compact  | 4/4 (0 thought) | 4/4 (0 thought) | 4/4 (0 thought)      |

The published question compacted at the four mid-task turn ends in all 16
runs, and in all 16 of an earlier batch the same day, without thinking. In 12
of the 16 it answered that the next turns would need the detail, then
compacted anyway: weighed against the itemized costs, the detail lost. The
summarized question kept there in 13 of 16, and in 12 of 16 in the earlier
batch, so 25 of 32 against none of 32. At every other case the two gave the
same verdict in every run. Its misses are at tend-eval-rerun-wait, where the
next turn opened a PR from the rerun's report and the run's earlier findings,
which the runs that compact say a summary would carry, and once at
leaf-ring-test-rewrite-wait. Across both batches it compacted in all 7 mid-task
runs that thought first, and kept in all 25 that didn't.

Both compact at the two small natural breaks, against their labels, though
sparkjoy asks only from 250k, so it would never ask at the 210k one.

### Other closings

In the earlier batch, beside `main@2e1755e-first` and `summarized-first`, two
other versions ran four times at each case. `summarized-keep-rule` closes
instead with "Keep the context if the next turns will need that detail, and
compact if they won't." `detail-only` drops the costs and asks only whether
the next turns will need detail a summary drops. At the mid-task cases:

| case                        | main@2e1755e-first | summarized-first | summarized-keep-rule | detail-only     |
| --------------------------- | ------------------ | ---------------- | -------------------- | --------------- |
| leaf-send-hover-fix         | 0/4 (0 thought)    | 4/4 (0 thought)  | 4/4 (0 thought)      | 4/4 (0 thought) |
| leaf-menu-design            | 0/4 (0 thought)    | 4/4 (0 thought)  | 4/4 (0 thought)      | 4/4 (0 thought) |
| tend-eval-rerun-wait        | 0/4 (0 thought)    | 0/4 (4 thought)  | 2/4 (4 thought)      | 4/4 (0 thought) |
| leaf-ring-test-rewrite-wait | 0/4 (0 thought)    | 4/4 (0 thought)  | 4/4 (0 thought)      | 4/4 (0 thought) |

At every other case all four gave the same verdicts, but for one run of
`detail-only` that kept at leaf-save-icon-merge-ask. In all 156 runs of the
last three, the verdict was the opposite of the answer to whether the next
turns need the detail, as it was in all but one of `summarized`'s 52 in the
later batch. So the costs reach the verdict mainly through that answer, at
tend-eval-rerun-wait.

### Context size

The same two summarized versions, telling the model a context of 300k or 900k
at the mid-task cases in place of the recorded 519k–681k, where a compaction
pays for itself after 28 or 7 requests:

| told                   | summarized | summarized-keep-rule |
| ---------------------- | ---------- | -------------------- |
| 300k                   | 16/16      | 16/16                |
| as recorded, 519k–681k | 12/16      | 14/16                |
| 900k                   | 11/16      | 12/16                |

So the stated size moves the verdict at the margin, through
tend-eval-rerun-wait and one run each at two other cases, and the clear
mid-task cases keep at any size.

### Fields the hook doesn't read

The hook reads only `compact` and `reason`, yet the question asks for `next`
and `needs_detail` first. Without them, `summarized-no-fields` kept at the
mid-task cases in 3 of 16 runs against 13 of 16 (Fisher's exact test,
p = 0.001), and thought before answering in 13 of those 16, where the question
with the fields thought in 3.

## Against the live plugin

A run now sends the request the live fork sends, so a verdict sparkjoy logged
at a real turn end is a sample of what a run there measures, once the
question it asked is the one the run asks. sparkjoy writes every verdict into
the transcript, so turn ends asked under the question as it stands will give
live verdicts to compare against, and to grade by what each session did next.

## Where this leaves them

The summarized question keeps in the middle of a task in most runs and gives
one verdict in every run at the other cases, where the published question
compacted at every turn end. It matches the label in most runs at 10 of the
13 cases; at the other three, the two small natural breaks and
tend-eval-rerun-wait, it compacts.

Some labels are themselves uncertain. Each records what the session did next,
which needn't be what it needed: at three of the four mid-task cases the next
work rested on files, a branch or a result that arrives anyway. The mid-task
keep labels encode a rule, that work in progress keeps, rather than a measured
cost, while the [cost model](../docs/cost-model.md) finds compacting at any
turn end from about 250k cheapest when a compaction loses nothing. What a
compaction loses is very noisy and hard to judge, but as some indication, my
sessions had about 6 more wasted requests than usual after one. Live verdicts
graded by what followed each one can settle it, since they would measure the
detail lost at each kind of turn end.
