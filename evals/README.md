# Evals

These evals put sparkjoy's question to the model at 14 turn ends from my
Claude Code sessions on open-source work (Leaf, Worktrunk and tend), and score
its verdict against a label chosen with hindsight, from what the session did
next.

The cases test one claim: the right answer at a natural break depends on how
much the work ahead would carry. A finished piece of work at 700k should
compact, the same kind of moment at 200k–300k shouldn't, and a moment in the
middle of a task should keep, up to 900k, from where sparkjoy compacts without
asking.

## Running them

```
uv run --script evals/run.py                                    # this checkout's question, each case once
uv run --script evals/run.py --question-from ../other-checkout  # another checkout's question
uv run --script evals/run.py --question-from a.ts --question-from b.ts  # two variants of register.ts
uv run --script evals/run.py --case leaf-menu-design --repeat 4
uv run --script evals/run.py --told recorded --told 900000      # also as though the context held 900k
uv run --script evals/run.py --report                           # the results table, from results.jsonl
```

They need `bun` and `uv`, and a Claude Code login or `ANTHROPIC_API_KEY`.
`--question-from` and `--told` can each be given more than once, and every run
appends a row to `results.jsonl`.

A case's first run writes its whole context to the prompt cache, $1 to $4.30 at
$5 per million tokens on Opus 5.5. Every run of it after that, within five
minutes and whichever question it asks, reads the context from the cache for
about $0.04 to $0.18. So a batch costs little more for trying several
questions or sizes at once, and runs each case's in turn.

## How a run works

The plugin asks through `$.model.fork`, which sends the main thread's last
request again (its parameters, system prompt, tools and messages), then the
turn's reply, then the question as one user message. The fork thinks as the
session does, at its effort (`xhigh` in nine of the cases, `high` in
five), and answers any tool call with the error "A model fork cannot use
tools", after which the model answers in a further request.

Claude Code records each request a session sends in its transcript:
`api-request` lines hold the parameters and the hashes of the messages,
`api-request-shape` lines the system prompt and tools, and `api-request-blob`
lines the messages the transcript's own lines don't hold. A case is a line of
the turn's reply in one of my sessions (`cases.json`). `build.py` finds the
request that reply answered, rebuilds its messages from the blobs and the
transcript's lines, checks each against the hash the record gives it, and adds
the reply. `run.py` does that wherever the case's transcript is on the machine,
and otherwise sends the request published in `requests/<case>.json.gz`; it adds
the question and sends the whole straight to the API. `question.ts`
renders the question by calling `question()` from the chosen checkout's
`hooks/register.ts`, for the case's recorded context size, so the evals ask
exactly what the plugin asks. The model's reply is parsed as the plugin parses
it.

The requests are published, so `build.py` takes out of them what shouldn't be:
each sentence about my work outside open source, all of them in my
instructions and Claude Code's skill listing, email addresses, private
hostnames, page tokens, and two images that show a local page's token. It stops if a check for
any of these finds one left. Only I can run it, since it reads my transcripts,
so only I can add a case; anyone can run the evals, on the published requests,
which differ from the live fork's in lacking its thinking
([Published requests](#published-requests)).

I checked the rebuilt request against a fork's captured live, on 25 November
2026 with Claude Code 2.1.332: a session with a mod that forks once at its first
turn end, its requests recorded as the SDK serialized them. The rebuilt request
matched the live fork's model, token limit, thinking, effort, system prompt,
tools and messages; one system-role message came as a list of one text block
where the fork sent a string. It leaves out the billing header, `metadata`,
`safeguards`, `data_flow`, `diagnostics` and the fork's cache breakpoints. Sent to the API, it
was billed the same 891 input tokens as the live fork and read from the prompt
cache the same 66,496, so none of what it leaves out reaches the model.

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
| leaf-menu-build-wait        | turn | 853k    | compact  | waiting on stills and a test migration in the menu build   |
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

From `run.py --report`, on requests rebuilt from the transcripts as the live
fork sends them, on 25 and 26 November 2026. Each cell counts the runs that
matched the label, and in brackets those in which the model thought before it
answered. `summarized` is the question as it stands: the context's size, the
requests a compaction takes to pay for itself and that keeping only puts it
off, then what a summary drops and which work needs it, then "Weigh the saving
against what losing that detail would cost the next turns."
`summarized-told-900k` is that question telling the model the context is 900k,
where a compaction pays for itself after 7 requests. `detail-only` drops the
costs and asks only whether the next turns will need detail a summary drops.
`main@2e1755e` is the question published before this change, which states each
figure behind the costs and lists what a compaction, keeping the context and
lost detail each cost.

| case                        | context | expected | summarized      | summarized-told-900k | detail-only     | main@2e1755e    |
| --------------------------- | ------- | -------- | --------------- | -------------------- | --------------- | --------------- |
| leaf-scaling-closeout       | 742k    | compact  | 4/4 (0 thought) | 4/4 (0 thought)      | 4/4 (0 thought) | 4/4 (0 thought) |
| leaf-hover-closeout         | 736k    | compact  | 4/4 (0 thought) | 4/4 (0 thought)      | 4/4 (0 thought) | 4/4 (0 thought) |
| leaf-followups-merged       | 643k    | compact  | 4/4 (0 thought) | 4/4 (0 thought)      | 4/4 (0 thought) | 4/4 (0 thought) |
| leaf-scroll-fix-merged      | 608k    | compact  | 4/4 (0 thought) | 4/4 (0 thought)      | 4/4 (0 thought) | 4/4 (0 thought) |
| worktrunk-release-tagged    | 479k    | compact  | 4/4 (0 thought) | 4/4 (0 thought)      | 4/4 (0 thought) | 4/4 (0 thought) |
| leaf-menu-build-wait        | 853k    | compact  | 0/2 (0 thought) |                      |                 |                 |
| leaf-ring-test-rewrite-wait | 681k    | keep     | 6/6 (0 thought) | 6/6 (0 thought)      | 4/4 (0 thought) | 0/4 (0 thought) |
| leaf-send-hover-fix         | 570k    | keep     | 6/6 (0 thought) | 5/6 (0 thought)      | 4/4 (0 thought) | 0/4 (0 thought) |
| leaf-menu-design            | 551k    | keep     | 6/6 (0 thought) | 6/6 (0 thought)      | 4/4 (0 thought) | 0/4 (0 thought) |
| tend-eval-rerun-wait        | 519k    | keep     | 4/6 (4 thought) | 6/6 (0 thought)      | 4/4 (2 thought) | 0/4 (0 thought) |
| leaf-save-icon-merge-ask    | 479k    | compact  | 4/4 (0 thought) | 4/4 (0 thought)      | 4/4 (0 thought) | 4/4 (0 thought) |
| leaf-data-attrs-merged      | 296k    | keep     | 0/4 (0 thought) | 0/4 (0 thought)      | 0/4 (0 thought) | 0/4 (0 thought) |
| worktrunk-repairs-merged    | 210k    | keep     | 0/4 (0 thought) | 0/4 (0 thought)      | 0/4 (0 thought) | 0/4 (0 thought) |
| leaf-ui-fixes-idle (idle)   | 290k    | compact  | 4/4 (0 thought) | 4/4 (0 thought)      | 4/4 (0 thought) | 4/4 (0 thought) |

The published question compacted at the four mid-task turn ends from 519k to
681k in all 16 runs, and in all 16 of a batch before it, without thinking. In
12 of the 16 it answered that the next turns would need the detail, then
compacted anyway. The summarized question kept there in 22 of 24 runs, and
gave every other case the same verdicts but leaf-menu-build-wait, where it
kept at 853k against its label ([Size](#size)). Its two misses are
at tend-eval-rerun-wait, where the next turn opened a PR from the rerun's
report and the run's earlier findings, which the runs that compact say a
summary would carry. Both followed thinking.

All three compact at the two small natural breaks, against their labels,
though sparkjoy asks only from 250k, so it would never ask at the 210k one.

### Size

The question's verdict follows its answer about the detail, whatever it says
of the context's size. In one batch on 26 November, five wordings put the size
to it in different ways, at the mid-task turn ends as recorded and told the
context was 900k, and at two real turn ends near the end of leaf-menu-design's
session, at 853k (leaf-menu-build-wait) and 928k, where the build went on
until Claude Code compacted it in the middle of a turn at 968k. The last two
wordings ran at two of the four mid-task turn ends (tend-eval-rerun-wait and
leaf-menu-design) and at 853k only:

| wording                                                                                   | 519k–681k, as recorded | told 900k | 853k and 928k |
| ----------------------------------------------------------------------------------------- | ---------------------- | --------- | ------------- |
| `summarized`                                                                              | 0/8 compacted          | 0/8       | 0/4           |
| `runway`: adds that Claude Code compacts on its own at 965k, in about N requests          | 0/8                    | 2/8       | 0/4           |
| `runway-finish`: and to keep only if the work that needs the detail will end before then  | 0/8                    | 2/8       | 0/4           |
| `deliberate`: asks it to work out what keeping costs until then against the detail, first | 1/4                    | 1/4       | 0/2           |
| `cost-field`: asks for a field stating that trade-off before the verdict                  | 0/4                    | 2/4       | 0/2           |

Where a run answered whether the next turns need the detail, its verdict was
the opposite in 59 of 60. Asked how many more requests the work under way
needs the detail for, it answered 6 to 30 at the two real turn ends past 850k,
though from the first of them the build ran about 90 more requests before
Claude Code compacted it. So no wording
tried makes the model trade the detail for the context's size, and the plugin
applies the size itself: from 900k (`CEILING` in `hooks/register.ts`) it
compacts at a turn end without asking, where the [cost model](../docs/cost-model.md)
finds work that needs its history cheapest compacted. Since the plugin asks
nothing there, no case sits past 900k.

### Published requests

The published requests lack every thinking block, which the build drops
(`build.py`). Without them the model thinks first far more often: with the same
questions at the same cases, in 39 of 60 runs against 6 of 60 on the
transcripts' requests, and it compacted in 28 against 4. The summarized
question compacted at the mid-task turn ends in 3 of 8 runs, told 900k in 6
of 8, and at 853k and 928k in 4 of 4. So a run on the published requests compacts more often than the
live fork would, and the tables here come from the transcripts. `--report`
lists those runs as `(published)`.

### Earlier wordings

An earlier wording of the summarized question said a compaction "costs about
as much as N requests", where N is its cost over what each later request saves,
and that it "saves more the longer the session goes on", in place of the
sentence that keeping only puts it off. At the mid-task cases:

| version                                              | as recorded            | told 300k | told 900k |
| ---------------------------------------------------- | ---------------------- | --------- | --------- |
| that wording                                         | 12/16, and 13/16 later | 16/16     | 11/16     |
| that wording, closed with a rule (below)             | 14/16                  | 16/16     | 12/16     |
| that wording, asking only for `compact` and `reason` | 3/16                   | —         | —         |
| `detail-only`, in the first batch                    | 16/16                  | —         | —         |

The rule was "Keep the context if the next turns will need that detail, and
compact if they won't." Every version gave the same verdicts as `summarized`
at the other cases, but for one run of `detail-only` in the first batch that
kept at leaf-save-icon-merge-ask. The first batch and the earlier wording's
runs told another size ran from the prototype of `run.py`, whose requests read the same cache entries
`run.py`'s did, so they sent the same bytes through the turn's reply.

### Fields the hook doesn't read

The hook reads only `compact` and `reason`, yet the question asks for `next`
and `needs_detail` first. Without them, the earlier wording kept at the
mid-task cases in 3 of 16 runs against 13 of 16 with them in the same batch
(Fisher's exact test, p = 0.001), and thought before answering in 13 of those
16, where the version with the fields thought in 3.

## Against the live plugin

A run now sends the request the live fork sends, so a verdict sparkjoy logged
at a real turn end is a sample of what a run there measures, once the
question it asked is the one the run asks. sparkjoy writes every verdict into
the transcript, so turn ends asked under the question as it stands will give
live verdicts to compare against, and to grade by what each session did next.

## Where this leaves them

The question keeps in the middle of a task and compacts where a piece of work
is finished, and from 900k the plugin compacts whatever it would answer, so a
long task no longer keeps its context through a turn end from 900k. A turn that
starts below 900k can still grow until Claude Code compacts it in the middle:
of three such compactions in my sessions of the last month, the plugin would
have come first at two. Below 900k, whether the size should outweigh the detail is the gap
between the labels and the [cost model](../docs/cost-model.md): the mid-task
keep labels encode a rule, that work in progress keeps, while the cost model
finds compacting at any turn end from about 250k cheapest when a compaction
loses nothing. What a compaction loses is very noisy and hard to judge, but as
some indication, my sessions had about 6 more wasted requests than usual after
one.

Some labels are themselves uncertain. Each records what the session did next,
which needn't be what it needed: at three of the four mid-task cases up to 681k the next
work rested on files, a branch or a result that arrives anyway. Live verdicts
graded by what followed each one can settle it, since they would measure the
detail lost at each kind of turn end.

Runs on the published requests compact more often than the live fork would,
so what a change to the question does to the plugin shows only on the
transcripts' requests.
