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

A second eval, `share.py`, asks the model at the same turn ends how much of
its context the work ahead needs, and checks the answer against what the work
used ([How much of the context the work ahead needs](#how-much-of-the-context-the-work-ahead-needs)).

## Running them

```
uv run --script evals/run.py                                    # this checkout's question, each case once
uv run --script evals/run.py --question-from ../other-checkout  # another checkout's question
uv run --script evals/run.py --question-from a.ts --question-from b.ts  # two variants of register.ts
uv run --script evals/run.py --case leaf-menu-design --repeat 4
uv run --script evals/run.py --told recorded --told 900000      # also as though the context held 900k
uv run --script evals/run.py --report                           # the results table, from every batch's results
uv run --script model/sparkjoy_assets.py publish                # publish rebuilt requests, and results still waiting
```

They need `bun`, `uv` and `git`, and a Claude Code login or
`ANTHROPIC_API_KEY`. `--question-from` and `--told` can each be given more than
once.

The published requests and every run's results are kept in
[sparkjoy-assets](https://github.com/max-sixty/sparkjoy-assets), so that
neither this repository's history nor an install of the plugin carries them.
That repository only gains files. Each request is named for the hash of its
JSON, and `requests.json` here names the one each case sends. Each batch of
runs adds a file of its results, and `--report` reads every batch's. The
scripts clone it into `.tmp/sparkjoy-assets` the first time they need it, and
fetch each request when a run first asks for it.

A batch publishes its results to sparkjoy-assets when it ends, along with
those of any earlier batch that didn't, such as one that was killed. `build.py`
leaves each request it writes in `.tmp/sparkjoy-assets-unpublished` until
`model/sparkjoy_assets.py publish` publishes it and points `requests.json` at
it, so that each new image gets a look first; commit `requests.json` with the
change that uses the request. Publishing needs push access to sparkjoy-assets;
without it, results wait in `.tmp/sparkjoy-assets-unpublished` too.

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
and otherwise sends the one `requests.json` names in sparkjoy-assets; it adds
the question and sends the whole straight to the API.
`question.ts` renders the question by calling `question()` from the chosen checkout's
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
requests a compaction takes to pay for itself and that keeping only puts it off, then what a summary drops and
which work needs it, then "Weigh the saving against what losing that detail
would cost the next turns."
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
of the context's size. A case holds one piece of work fixed, so asking it with
other sizes stated (`--told`) shows how the verdict moves with the size alone,
and `--report` prints a table of compactions by stated size for each question
asked at several. On 26 November, three wordings stated 300k, 500k, 700k and
900k at the five mid-task turn ends (the four labelled keep, and
leaf-menu-build-wait) and at two that finished a piece of work
(worktrunk-release-tagged and leaf-data-attrs-merged), twice each, for $39,
with 97% of the input read from the cache. Each cell counts the runs that
compacted:

| wording                                                                                                                                                                                                               | turn ends | 300k | 500k  | 700k  | 900k  |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------- | ---- | ----- | ----- | ----- |
| `summarized`, the question as it stands                                                                                                                                                                               | mid-task  | 0/10 | 0/10  | 2/10  | 0/10  |
|                                                                                                                                                                                                                       | finished  | 4/4  | 4/4   | 4/4   | 4/4   |
| `sweep-weigh`: says how many times more each request reads than it would after a compaction, and after how many requests a compaction pays for itself; asks how many requests remain and how many a compaction wastes | mid-task  | 9/10 | 10/10 | 10/10 | 10/10 |
|                                                                                                                                                                                                                       | finished  | 4/4  | 4/4   | 4/4   | 4/4   |
| `sweep-estimate`: states no size or cost, so its columns are repeats, and asks the same two estimates                                                                                                                 | mid-task  | 0/10 | 0/10  | 0/10  | 0/10  |
|                                                                                                                                                                                                                       | finished  | 4/4  | 3/4   | 2/4   | 3/4   |

No wording moved a turn end's verdict in step with the size it stated. Only at
tend-eval-rerun-wait did a verdict move at all: `summarized` compacted in both
runs at 700k and kept at the other sizes, and `sweep-weigh` kept in one of its
two runs at 300k, each after thinking. Elsewhere a wording moves every size at
once. The estimates of the requests a compaction would waste,
asked without the size or the costs, were steady across runs and ordered by
the work: 1 to 2 where three changes had landed, 4 to 6 after a release, 6 to
10 while waiting on an eval rerun to turn into a PR, and 12 to 15 in the middle
of a design or fix. Told the costs, the model estimated more requests left in
the session, up to four times as many, and enough for a compaction to pay for
itself in all but one of its 56 runs.

Earlier, in one batch on 26 November, five wordings put the size
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

## How much of the context the work ahead needs

Whether compacting pays turns on how much of the context the work ahead will
use: after a compaction, whatever it uses has to be read back, and each token
read back is written to the cache again. `share.py` asks the model for that
share at each turn end and checks the answer against what the session did
next.

```
uv run --script evals/share.py                  # every case: labels where they're missing, and the share twice
uv run --script evals/share.py --case leaf-send-hover-fix --repeat 4
uv run --script evals/share.py --report         # the tables below, and the chart, waiting to publish
uv run --script evals/share.py --build          # the made-up cases' requests, waiting to publish
uv run --script model/sparkjoy_assets.py publish
```

The question (`SHARE` in `share.py`) asks only for "the percentage of this
context that might possibly be helpful for the work ahead", after the case's
published request, and adds that models generally underweigh the chance of
context being helpful, so to lean toward counting it. The plugin's question
asks for no share yet, so this one belongs to the eval.

Two kinds of case check the answer. At each turn end in `cases.json` but the
idle one, `parts.py` cuts the request into parts: the system prompt, the
tools, and each block of each message, such as a text, a tool call or a
tool's result. The same model, after the same request, is shown what the
session did over its next 80 requests, through any compaction, and marks each
part needed, helpful or neither, in two runs. Labelling reads what came next
from my transcripts, so only I can label a case. From the labels come the
share the work needed, the share it needed or was helped by, and the same two
of what a compaction drops. A compaction leaves the system prompt and tools,
which the first request after it reads from the cache, and the instruction
files, which Claude Code writes again after the summary. The cost model
counts both in the context after a compaction, so the work never has to read
them back.

The three made-up turn ends in `contrived.py` keep leaf-data-attrs-merged's
system prompt, tools and settings, with new messages whose share is known by
construction: just after loading the Leaf skill and four of its references to
draft a page, which should count nearly all of it; the same once the page is
built and the user has moved on to their tax return, which should count
little; and after a 150k-character CI log was read and its one failure fixed,
where only the short module and the fix count.

A labelled case costs its context's cache write, $1 to $3.40, and about $1
more for the two label runs and two shares. The results, from 27 November
2026, are percentages of the context, each the range of two runs. "Before the
line" is the question without its sentence about leaning toward counting
context as helpful:

| case                        | context | the model's share | before the line | needed | needed or helpful | of what a compaction drops: needed | needed or helpful |
| --------------------------- | ------- | ----------------- | --------------- | ------ | ----------------- | ---------------------------------- | ----------------- |
| leaf-scaling-closeout       | 663k    | 20–25%            | 12%             | 9–10%  | 23–26%            | 1%                                 | 17%               |
| leaf-hover-closeout         | 686k    | 12–15%            | 8%              | 11–21% | 26–27%            | 7%                                 | 18%               |
| leaf-followups-merged       | 587k    | 12–15%            | 8%              | 19–31% | 36%               | 15%                                | 28%               |
| leaf-scroll-fix-merged      | 465k    | 30–35%            | 12%             | 14%    | 54%               | 1%                                 | 47%               |
| worktrunk-release-tagged    | 437k    | 20–30%            | 12%             | 17–20% | 39–44%            | 7%                                 | 33%               |
| leaf-send-hover-fix         | 487k    | 30%               | 15–20%          | 24–25% | 43–48%            | 5%                                 | 29%               |
| leaf-menu-design            | 435k    | 45%               | 25–30%          | 43–45% | 65–70%            | 35%                                | 62%               |
| tend-eval-rerun-wait        | 470k    | 35–40%            | 18–20%          | 35–36% | 49%               | 22%                                | 38%               |
| leaf-ring-test-rewrite-wait | 525k    | 35%               | 18–25%          | 17–19% | 44–48%            | 6%                                 | 39%               |
| leaf-save-icon-merge-ask    | 409k    | 25–30%            | 12%             | 21–26% | 27–35%            | 9%                                 | 16%               |
| worktrunk-repairs-merged    | 198k    | 15–20%            | 12%             | 36–62% | 40–67%            | 29%                                | 36%               |
| leaf-data-attrs-merged      | 269k    | 20–30%            | 8–10%           | 24–29% | 57%               | 3%                                 | 43%               |
| leaf-menu-build-wait        | 685k    | 30–35%            | 25%             | 18–35% | 36–55%            | 19%                                | 41%               |

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/max-sixty/sparkjoy-assets/main/images/share-parts-dark/62922deb0e20.svg">
  <img alt="Each of the 13 labelled turn ends' requests as a bar of its parts in order, from 198k to 686k tokens, shaded by whether the next work needed each part. Every bar opens with needed blocks, the system prompt, tools and instruction files, and most of the rest is not needed. Beside each bar, the model's share against the share needed or helpful in hindsight, from 22% against 24% at leaf-scaling-closeout to 18% against 54% at worktrunk-repairs-merged, and below it at every case." src="https://raw.githubusercontent.com/max-sixty/sparkjoy-assets/main/images/share-parts-light/ccf2d251ce8c.svg">
</picture>

The line raised the model's share at every case, from 8%–30% to 12%–45%.
It still falls under the share the labels mark needed or helpful at every
case, by 2 to 36 points. It lies between that and the share they mark needed
at 9 of the 13, and under both at leaf-hover-closeout, leaf-followups-merged,
worktrunk-repairs-merged and leaf-data-attrs-merged.

Of what a compaction drops, the labels mark 16% to 33% needed or helpful at
the finished turn ends, but for leaf-scroll-fix-merged's 47%, 29% to 62% at
the mid-task ones, and 36% and 43% at the two small natural breaks. The model
gives the finished turn ends 12% to 35% of the context and the mid-task ones
30% to 45%, but at the small breaks, where the labels mark 40% to 67% of the
context needed or helpful, it gives 15% to 30%.

Half of each context is the model's tool calls and their output, and the next
work needed 4% of the context from them, or 12% counting what helped. The
mean over the 13, as a percentage of the context:

| kind                               | size  | needed | needed or helpful |
| ---------------------------------- | ----- | ------ | ----------------- |
| System prompt and tools            | 7.5%  | 7.5%   | 7.5%              |
| Instruction files, with re-reads   | 8.4%  | 7.6%   | 8.1%              |
| What an earlier compaction carried | 9.9%  | 2.0%   | 8.8%              |
| File reads                         | 20.6% | 1.9%   | 7.2%              |
| Other tool output                  | 9.6%  | 0.3%   | 1.1%              |
| The model's tool calls             | 19.9% | 1.7%   | 3.2%              |
| The model's text                   | 12.5% | 2.9%   | 5.5%              |
| The user's messages                | 0.5%  | 0.0%   | 0.1%              |
| Everything else                    | 11.2% | 1.5%   | 2.8%              |

At the made-up turn ends, both questions gave a share in range in every run:

| made-up case | the turn end                                                                   | context | should be | the model's share | before the line |
| ------------ | ------------------------------------------------------------------------------ | ------- | --------- | ----------------- | --------------- |
| skills-start | Just loaded the Leaf skill and four of its references; about to draft the page | 57k     | 70–100%   | 85–90%            | 80%             |
| skills-done  | The same, then the page built and the user off to their tax return             | 57k     | 0–20%     | 15%               | 5–20%           |
| log-fixed    | A CI log read and its one failure fixed; next, push and watch CI               | 108k    | 0–10%     | 6–8%              | 3–4%            |

The labeller is the model under test, told what came next, and what it marks
is what the next work used, which needn't be all it needed. Its two runs at a
case differ by up to 26 points in the share needed, at
worktrunk-repairs-merged, where one run marks what an earlier compaction
carried, 27% of the context, needed and the other doesn't. The system prompt and
tools take the tokens the API counts for them, and every other part takes its
share of the rest by its characters.
