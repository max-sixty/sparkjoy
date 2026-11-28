# Evals

These evals put sparkjoy's question to the model at 14 turn ends from my
Claude Code sessions on open-source work (Leaf, Worktrunk and tend), and score
its verdict against a label chosen with hindsight, from what the session did
next.

The cases test one claim: the right answer at a natural break depends on how
much the work ahead would carry. A finished piece of work at 700k should
compact, the same kind of moment at 200k–300k shouldn't, and a moment in the
middle of a task should keep, unless the context is so near the 965k where
Claude Code compacts on its own that keeping would carry the detail through
little more work.

A second eval, `share.py`, asks the model at the same turn ends how much of
its context the work ahead needs, and checks the answer against what the work
used ([How much of the context the work ahead needs](#how-much-of-the-context-the-work-ahead-needs)).

## Running them

```
uv run --script evals/run.py                                    # this checkout's question, each case once
uv run --script evals/run.py --question-from ../other-checkout  # another checkout's question
uv run --script evals/run.py --question-from a.ts --question-from b.ts  # two variants of register.ts
uv run --script evals/run.py --case leaf-menu-design --repeat 4
uv run --script evals/run.py --told recorded --told 500000      # also as though the context held 500k
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
`hooks/register.ts`, for the case's `tokens`: what the reply's request held, not
counting the reply, which is the size the plugin states (`build.py` checks it
against the transcript). It states the figures the plugin ships (`AUTHOR`), so
the evals ask what the plugin asks a user whose own report hasn't saved
figures of theirs. The model's reply is parsed as the plugin parses it. A run
asks only where the checkout's plugin asks: from its floor, 250k at a turn end
and 175k in an idle session. `--report` marks the runs an older batch made
below those sizes "not asked" and counts them in no total.

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
| leaf-scaling-closeout       | turn | 739k    | compact  | performance fixes landed and closed out                    |
| leaf-hover-closeout         | turn | 735k    | compact  | reviewed work landed; the rest waits on the user's answers |
| leaf-followups-merged       | turn | 641k    | compact  | both follow-ups merged, nothing open                       |
| leaf-scroll-fix-merged      | turn | 604k    | compact  | a Firefox scroll fix merged and written up                 |
| worktrunk-release-tagged    | turn | 479k    | compact  | a release tagged, a question about it answered             |
| leaf-menu-build-wait        | turn | 853k    | compact  | waiting on stills and a test migration in the menu build   |
| leaf-ring-test-rewrite-wait | turn | 681k    | keep     | waiting on a test rewrite before the final checks          |
| leaf-send-hover-fix         | turn | 569k    | keep     | mid-iteration on a UI fix the user keeps faulting          |
| leaf-menu-design            | turn | 550k    | keep     | a design exploration just rebuilt, awaiting reaction       |
| tend-eval-rerun-wait        | turn | 519k    | keep     | waiting on an eval rerun to turn into a PR                 |
| leaf-save-icon-merge-ask    | turn | 479k    | compact  | asks whether to open and merge a finished branch's PR      |
| leaf-data-attrs-merged      | turn | 294k    | keep     | three changes landed: a natural break, but small           |
| worktrunk-repairs-merged    | turn | 210k    | keep     | four repair PRs merged: a natural break, but small         |
| leaf-ui-fixes-idle          | idle | 289k    | compact  | idle 55 minutes after everything landed                    |

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
would cost the next turns." `detail-only` drops the
costs and asks only whether the next turns will need detail a summary drops.
`main@2e1755e` is the question published before this change, which states each
figure behind the costs and lists what a compaction, keeping the context and
lost detail each cost.

| case                        | context | expected | summarized      | detail-only     | main@2e1755e    |
| --------------------------- | ------- | -------- | --------------- | --------------- | --------------- |
| leaf-scaling-closeout       | 739k    | compact  | 4/4 (0 thought) | 4/4 (0 thought) | 4/4 (0 thought) |
| leaf-hover-closeout         | 735k    | compact  | 4/4 (0 thought) | 4/4 (0 thought) | 4/4 (0 thought) |
| leaf-followups-merged       | 641k    | compact  | 4/4 (0 thought) | 4/4 (0 thought) | 4/4 (0 thought) |
| leaf-scroll-fix-merged      | 604k    | compact  | 4/4 (0 thought) | 4/4 (0 thought) | 4/4 (0 thought) |
| worktrunk-release-tagged    | 479k    | compact  | 4/4 (0 thought) | 4/4 (0 thought) | 4/4 (0 thought) |
| leaf-menu-build-wait        | 853k    | compact  | 0/2 (0 thought) |                 |                 |
| leaf-ring-test-rewrite-wait | 681k    | keep     | 6/6 (0 thought) | 4/4 (0 thought) | 0/4 (0 thought) |
| leaf-send-hover-fix         | 569k    | keep     | 6/6 (0 thought) | 4/4 (0 thought) | 0/4 (0 thought) |
| leaf-menu-design            | 550k    | keep     | 6/6 (0 thought) | 4/4 (0 thought) | 0/4 (0 thought) |
| tend-eval-rerun-wait        | 519k    | keep     | 4/6 (4 thought) | 4/4 (2 thought) | 0/4 (0 thought) |
| leaf-save-icon-merge-ask    | 479k    | compact  | 4/4 (0 thought) | 4/4 (0 thought) | 4/4 (0 thought) |
| leaf-data-attrs-merged      | 294k    | keep     | 0/4 (0 thought) | 0/4 (0 thought) | 0/4 (0 thought) |
| worktrunk-repairs-merged    | 210k    | keep     | not asked       | not asked       | not asked       |
| leaf-ui-fixes-idle (idle)   | 289k    | compact  | 4/4 (0 thought) | 4/4 (0 thought) | 4/4 (0 thought) |

In all, `summarized` matched the label in 50 of 58 runs, `detail-only` in 44 of
48 and `main@2e1755e` in 28 of 48.

The published question compacted at the four mid-task turn ends from 519k to
681k in all 16 runs, and in all 16 of a batch before it, without thinking. In
12 of the 16 it answered that the next turns would need the detail, then
compacted anyway. The summarized question kept there in 22 of 24 runs, and
gave every other case the same verdicts but leaf-menu-build-wait, where it
kept at 853k against its label ([Size](#size)). Its two misses are
at tend-eval-rerun-wait, where the next turn opened a PR from the rerun's
report and the run's earlier findings, which the runs that compact say a
summary would carry. Both followed thinking.

All three compact at leaf-data-attrs-merged, the 294k natural break, against
its label. sparkjoy doesn't ask at the 210k one, so its runs count in no total.

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
Claude Code compacted it. So no wording tried makes the model trade the detail
for the context's size.

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
is finished, so a long task can keep its context until Claude Code compacts it
in the middle of a turn. Whether the size should outweigh the detail is the gap
between the labels and the [cost model](../docs/cost-model.md): the mid-task
keep labels encode a rule, that work in progress keeps, while the cost model
finds compacting at any turn end from about 250k cheapest when a compaction
loses nothing. What a compaction loses is very noisy and hard to judge, but as
some indication, my sessions had about 6 more wasted requests than usual after
one. The share eval below labels it part by part: of what a compaction would
have dropped, the next work needed or was helped by 5% to 31% of the context,
a median 17% at the turn ends labelled keep and 11% at those labelled
compact. So the keep labels rest on that loss costing more than reading it back,
which neither eval prices.

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
use: after a compaction, whatever it uses of what the compaction dropped has
to be read back, and each token read back is written to the cache again.
`share.py` asks the model for that share at each turn end and checks the
answer against what the session did next.

```
uv run --script evals/share.py                  # every case: labels where they're missing, and the share twice
uv run --script evals/share.py --case leaf-send-hover-fix --repeat 4
uv run --script evals/share.py --report         # the tables below, and the chart, waiting to publish
uv run --script evals/share.py --build          # the made-up cases' requests, waiting to publish
uv run --script model/sparkjoy_assets.py publish
```

The question (`SHARE` in `share.py`) asks only for "the percentage of this
context that might possibly be helpful for the work ahead", stating the size
the plugin states, and adds that models generally underweigh the chance of
context being helpful, so to lean toward counting it. The plugin's question
asks for no share yet, so this one belongs to the eval. A turn end's run
sends the request rebuilt from its transcript, thinking and all, as the live
fork sends it; elsewhere it sends the published one.

Two kinds of case check the answer. At each turn end in `cases.json` but the
idle one, `parts.py` cuts the request into parts: the system prompt, the
tools, each block of each message, and each source Claude Code writes into
one block, such as each instruction file, or each skill and listing it
writes back after a compaction. Each kind of part takes the tokens the API
counts for it, and its parts share them by characters. The same model,
after the same request, is shown what the session did next as JSON Lines:
one record for each thing the user typed, at the prompt or while the session
worked, each comment, reply or choice of theirs on a Leaf page, and each
text the model wrote, tool call with its input, result and compaction. The
records run on through any compaction until the context, grown from the turn
end's by what each later request added, would have passed the 1M window, or
to the session's end; the first request after a compaction counts as
growing by its cache write, up to 4k, since the compacted prompt hides what
it added. The labeller sees as much of that as the window holds beside the
request: each record past a common length is cut to it, its longest values
first, each keeping its head and tail, so every record stays whole JSON.
Where even every value cut doesn't fit, it sees as many of the earliest
records as fit with each cut to the median record's length, and a record
counting the rest. It is shown the parts a line each, with its kind, tokens
and opening words, and parts that hold the same content, such as the reminder
of the output style that Claude Code repeats at each turn, share one line
that names all their ids, so that marking the line marks each of them. It
then marks each part needed, helpful or neither, in two runs; an answer that
marks no part needed counts as unreadable, since the work after any turn end
uses the system prompt and tools, and is asked again. Where the session compacted, at the turn end
itself, as at six of the 13, or later in the work, the work after the
compaction ran without the context, so a part it then read, searched for or
ran again counts as needed: fetching it again is what losing it cost. Told
only that the session compacted there, the labeller marked such a part not
needed, since the work had read it afresh. Labelling reads what came next
from my transcripts, so only I can label a case.

What the share is weighed against is what a compaction drops that the work
needed ("lost" below), as a share of the whole context, since the cost model
already counts what comes back in the context after a compaction. A
compaction puts back the system prompt and tools, the latest copy of each
instruction file, the skills loaded in the session (each cut to 20,000
characters, the most recent first while they total 100,000, which none of
the 742 re-injections in my transcripts of 21 to 27 November passed), and
the skill, agent and tool listings and the environment. An earlier copy of an instruction
file or skill is dropped, but what the work needs of it comes back in the
later copy. The question itself asks for the share of all the context,
which the last two columns give.

The three made-up turn ends in `contrived.py` keep leaf-data-attrs-merged's
system prompt, tools and settings, with new messages whose labels are known
by construction (`contrived.labels`): just after loading the Leaf skill and
four of its references to draft a page, where all of it counts; the same once
the page is built and the user has moved on to their tax return, where only
the system prompt, tools and last exchange count; and after a 150k-character
CI log was read and its one failure fixed, where the module, the fix and the
reply holding it count. Their ranges come from those labels, five points
wider each way.

A labelled case's first label run costs its context's cache write and the
next work, $1.20 to $4.80, and its second $0.30 to $1.60; two shares cost
about $1 more. The results, from 27 November 2026, are percentages of the
context, each the range of two runs. "Before
the line" is the question without its sentence about leaning toward counting
context as helpful. Both are asked of the request rebuilt from the
transcript, as are the labels:

| case                        | context | the model's share | before the line | lost: needed | lost: needed or helpful | all: needed | all: needed or helpful |
| --------------------------- | ------- | ----------------- | --------------- | ------------ | ----------------------- | ----------- | ---------------------- |
| leaf-scaling-closeout       | 739k    | 25–30%            | 12%             | 3–4%         | 4–6%                    | 10–11%      | 13–15%                 |
| leaf-hover-closeout         | 735k    | 12–15%            | 8–10%           | 4–5%         | 9–10%                   | 14–15%      | 22–23%                 |
| leaf-followups-merged       | 641k    | 15%               | 8%              | 6%           | 10–11%                  | 15–16%      | 27%                    |
| leaf-scroll-fix-merged      | 604k    | 35%               | 12%             | 16%          | 24–25%                  | 26%         | 39–40%                 |
| worktrunk-release-tagged    | 479k    | 30%               | 12%             | 7%           | 15–16%                  | 19%         | 32%                    |
| leaf-menu-build-wait        | 853k    | 35%               | 20%             | 19%          | 30–32%                  | 26%         | 39–41%                 |
| leaf-save-icon-merge-ask    | 479k    | 25%               | 10–12%          | 2–3%         | 4–6%                    | 13–17%      | 23%                    |
| leaf-ring-test-rewrite-wait | 681k    | 35%               | 25%             | 7%           | 15–16%                  | 16%         | 28%                    |
| leaf-send-hover-fix         | 569k    | 30%               | 15%             | 5–8%         | 7–19%                   | 19–25%      | 26–47%                 |
| leaf-menu-design            | 550k    | 45–55%            | 20–25%          | 10–13%       | 22–26%                  | 25–28%      | 38–42%                 |
| tend-eval-rerun-wait        | 519k    | 35%               | 20%             | 10–16%       | 18–32%                  | 24–36%      | 43–57%                 |
| leaf-data-attrs-merged      | 294k    | 20%               | 8%              | 3–6%         | 11–12%                  | 27–29%      | 43–46%                 |
| worktrunk-repairs-merged    | 210k    | 20%               | 10%             | 9–11%        | 16–18%                  | 37–38%      | 53–59%                 |

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/max-sixty/sparkjoy-assets/main/images/share-parts-dark/ea0445fd3f23.svg">
  <img alt="Each of the 13 labelled turn ends' requests as a bar of its parts in order, from 210k to 853k tokens, shaded by whether the next work needed each part and hatched where a compaction puts it back. Every bar opens with hatched, needed blocks, the system prompt, tools, instruction files and skills, and most of the rest is not needed. Beside each bar, the model's share, the share a compaction drops that the work needed or was helped by, and the same of all the context, from 28%, 5% and 14% at leaf-scaling-closeout to 20%, 17% and 56% at worktrunk-repairs-merged." src="https://raw.githubusercontent.com/max-sixty/sparkjoy-assets/main/images/share-parts-light/542652c34e89.svg">
</picture>

With the line, the model's share is above what a compaction drops that the
work needed or was helped by at all 13 turn ends, by 3 to 26 points:
furthest at leaf-menu-design (50% against 24%) and leaf-scaling-closeout
(28% against 5%), and least at worktrunk-repairs-merged (20% against 17%).
The line raised the share at every case, by 4 to 28 points. Before it, the
share was within 5 points of what a compaction drops that the work needed or
was helped by at 6 of the 13, under it at 9 and over at 4, and at most 13
points off, at leaf-scroll-fix-merged. Against all the context, as the
question asks, the share with the line is within 10 points at 7 of the 13,
10 and 14 points over at leaf-menu-design and leaf-scaling-closeout, 12 and
15 under at leaf-followups-merged and tend-eval-rerun-wait, and 25 and 36
points under at the two small natural breaks, where what comes back after a
compaction, which the work needed, is a third of the context.

The hindsight figures tell the labels apart only loosely: what a compaction
drops that the work needed or was helped by is a median 17% at the turn ends
labelled keep and 11% at those labelled compact, from 11% to 25% at the
first and from 5% to 31% at the second.

The mean over the 13, with each kind's share needed, and needed or helpful,
of its own size:

| kind                                   | a compaction | size  | needed | needed or helpful |
| -------------------------------------- | ------------ | ----- | ------ | ----------------- |
| System prompt and tools                | puts it back | 6.9%  | 95%    | 95%               |
| Instruction files                      | puts it back | 5.2%  | 73%    | 98%               |
| Skill bodies                           | puts it back | 4.4%  | 27%    | 72%               |
| Claude Code's listings and environment | puts it back | 5.1%  | 31%    | 58%               |
| Earlier copies of what comes back      | drops it     | 1.3%  | 29%    | 70%               |
| Skill text past what comes back        | drops it     | 0.2%  | 45%    | 55%               |
| What an earlier compaction carried     | drops it     | 1.3%  | 32%    | 62%               |
| File reads                             | drops it     | 15.0% | 18%    | 38%               |
| Other tool output                      | drops it     | 13.5% | 6%     | 20%               |
| The model's tool calls                 | drops it     | 19.5% | 13%    | 18%               |
| The model's text                       | drops it     | 1.3%  | 48%    | 75%               |
| The model's thinking                   | drops it     | 13.7% | 0%     | 0%                |
| The user's messages                    | drops it     | 1.0%  | 20%    | 32%               |
| Claude Code's reminders and notices    | drops it     | 11.6% | 10%    | 17%               |

At the made-up turn ends the model's share reads as a share of the
conversation: it leaves out the system prompt and tools, which "this
context" includes, and counts the skill, which comes back. So it falls
outside both ranges at skills-start and skills-done, and half its runs at
log-fixed fall in the range on what's lost:

| made-up case | the turn end                                                                   | context | should be: lost | of all  | the model's share | before the line |
| ------------ | ------------------------------------------------------------------------------ | ------- | --------------- | ------- | ----------------- | --------------- |
| skills-start | Just loaded the Leaf skill and four of its references; about to draft the page | 57k     | 30–40%          | 95–100% | 85–90%            | 80–88%          |
| skills-done  | The same, then the page built and the user off to their tax return             | 58k     | 0–5%            | 53–63%  | 10–25%            | 5–20%           |
| log-fixed    | A CI log read and its one failure fixed; next, push and watch CI               | 108k    | 0–6%            | 26–36%  | 5–8%              | 3–4%            |

The labeller is the model under test, told what came next, and what it marks
is what the next work used, which needn't be all it needed. Its two runs at
a case differ by up to 6 points in what a compaction drops that the work
needed, and 14 counting what helped, both at tend-eval-rerun-wait, whose next
work now runs 101 requests to the session's end rather than 16 to the user's
compaction minutes after the turn end. At leaf-send-hover-fix, now 292
requests rather than 27, they differ by 3 and 12 points, and elsewhere by at
most 3 and 4. It sees every record of the next work at all 13 turn ends, and
every character at 10: the longest values are cut to leave 97% of the
characters at leaf-scaling-closeout, 96% at leaf-hover-closeout and 44% at
leaf-menu-build-wait, whose 853k context leaves the least of the window. It
can't judge the model's thinking, a seventh of each context, since
transcripts keep only its signature, so it marks almost none of it. And it
leans toward the first copy of an instruction file it meets: at
leaf-send-hover-fix, it marks the earliest copy of CLAUDE.md needed in both
runs and the latest in one.
