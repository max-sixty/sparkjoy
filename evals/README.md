# Evals

These evals put sparkjoy's question to the model at 12 turn ends from the
author's Claude Code sessions on open-source work (Leaf, Worktrunk and tend),
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

They need `claude` (tested on 2.1.325), `bun` and `uv`, and a Claude Code login
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

`build.py` made each fixture in `fixtures/` from the author's transcript: the
conversation from the last compaction to the turn's reply, which is what a
resume sends the model, sanitized for publishing.

`run.py` writes the fixture as a session into a scratch `CLAUDE_CONFIG_DIR`,
with every `CLAUDE*` and `ANTHROPIC*` variable unset, and resumes it with
`claude -p --resume <id> --fork-session` on `claude-opus-5-5` at `xhigh` effort,
as the sessions ran, with no tools, MCP servers, skills or instructions.
`question.ts` renders the question by calling `question()` from the chosen
checkout's `hooks/register.ts`, so the evals ask exactly what the plugin asks.
It passes the case's recorded context size as `tokens`, for a question that
takes one. The model's reply is parsed as the plugin parses it.

A replay differs from the plugin's own fork in these ways:

- **The replayed context is smaller than the recorded one**, 43% to 70% of it
  (the results table's two size columns). The fixture leaves out the model's
  hidden thinking, which is encrypted and so can't be checked, and the author's
  instructions, skills and tool listings, and Claude Code's `-p` system prompt
  is shorter than an interactive one. A question that states the size is told
  the recorded one; one that doesn't sees only the replayed transcript.
- **The model reads its reply twice.** The fixture ends with the reply and the
  question quotes it, where the plugin's fork ends before the reply.
- **The model has no tools**, where the fork has the session's.
- **The question comes in two blocks** when a case has more than one run: its
  opening through the quoted reply, then the rest (`run.py` says why).

## Cases

Each case's label, its reason and the evidence for it (what the session did
next) are in `cases.json`.

| case | kind | recorded | expected | the moment |
|---|---|---|---|---|
| leaf-scaling-closeout | turn | 742k | compact | performance fixes landed and closed out |
| leaf-hover-closeout | turn | 736k | compact | reviewed work landed; the rest waits on the user's answers |
| leaf-followups-merged | turn | 643k | compact | both follow-ups merged, nothing open |
| leaf-scroll-fix-merged | turn | 608k | compact | a Firefox scroll fix merged and written up |
| worktrunk-release-tagged | turn | 479k | compact | a release tagged, a question about it answered |
| leaf-ring-test-rewrite-wait | turn | 681k | keep | waiting on a test rewrite before the final checks |
| leaf-send-hover-fix | turn | 570k | keep | mid-iteration on a UI fix the user keeps faulting |
| leaf-menu-design | turn | 551k | keep | a design exploration just rebuilt, awaiting reaction |
| tend-eval-rerun-wait | turn | 519k | keep | waiting on an eval rerun to turn into a PR |
| leaf-data-attrs-merged | turn | 296k | keep | three changes landed: a natural break, but small |
| worktrunk-repairs-merged | turn | 210k | keep | four repair PRs merged: a natural break, but small |
| leaf-ui-fixes-idle | idle | 290k | compact | idle 55 minutes after everything landed |

The two small natural breaks are the same kind of moment as the compact cases
above them. They are labelled keep because a compaction leaves about 142k, so
at 210k it saves each later request about 68k while making a cache write of
about 110k and losing detail. The idle case is labelled compact at 290k because its
cache lapses either way.

## Results

From `run.py --report` on 16 November 2026; each cell counts the runs that
matched the label. `main` is the question published at 02beed1;
`sized-question` (cdfc871) adds the context's size, what a compaction leaves
and how many requests it takes to pay back; `detail` (0915ee8) says what
decides it; `measured` (1f004a4, 17 November) weighs what keeping costs
against what a compaction loses ([Context size](#context-size)); `now` (75fa52f,
the question now) is `measured` with its costs from the cost model.

| case | recorded | replayed | expected | main | sized-question | detail | measured | now |
|---|---|---|---|---|---|---|---|---|
| leaf-scaling-closeout | 742k | 520k | compact | 4/4 | 3/3 | 3/3 | 2/2 | 2/2 |
| leaf-hover-closeout | 736k | 488k | compact | 3/3 | 3/3 | 3/3 | 2/2 | 2/2 |
| leaf-followups-merged | 643k | 405k | compact | 3/3 | 3/3 | 3/3 | 2/2 | 2/2 |
| leaf-scroll-fix-merged | 608k | 326k | compact | 3/3 | 3/3 | 3/3 | 2/2 | 2/2 |
| worktrunk-release-tagged | 479k | 312k | compact | 3/3 | 3/3 | 4/4 | 2/2 | 2/2 |
| leaf-ring-test-rewrite-wait | 681k | 349k | keep | 0/3 | 0/3 | 3/3 | 0/2 | 1/2 |
| leaf-send-hover-fix | 570k | 278k | keep | 0/3 | 0/3 | 4/4 | 0/2 | 0/2 |
| leaf-menu-design | 551k | 279k | keep | 0/3 | 0/3 | 4/4 | 0/2 | 2/2 |
| tend-eval-rerun-wait | 519k | 279k | keep | 0/3 | 0/3 | 4/4 | 0/2 | 0/2 |
| leaf-data-attrs-merged | 296k | 150k | keep | 0/3 | 0/3 | 0/4 | 0/2 | 0/2 |
| worktrunk-repairs-merged | 210k | 89k | keep | 0/3 | 0/3 | 0/4 | 0/2 | 0/2 |
| leaf-ui-fixes-idle (idle) | 290k | 156k | compact | 3/3 | 3/3 | 4/4 | 2/2 | 2/2 |

The first two questions said compact in all 73 runs. Their reasons agree: the
context is mostly finished work, and what the next step needs is in the
summary, the files on disk or the transcript. That is true of nearly any long
transcript, so it settled nothing.

`detail` says so, and names what does decide it: whether the next turns will
need detail a summary drops, as work iterating on something just shown does,
and a result to report or a new task doesn't. It asks for what comes next and
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
  after about 30 requests, and a turn in the author's sessions averages about
  36. That case's keep label rests on the next task building on the change
  just landed, not on the cost.
- **At 210k it pays back after about 58 requests**, under two average turns,
  so the keep label holds only if the next stretch of work is short.

The cost model in [When to compact](../docs/when-to-compact.md) goes further:
a compaction repays over every request until the context grows back, so at a
natural break both cases should compact, and the best threshold at turn ends
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

`measured` is that version with figures from the author's sessions, as the
[README's prompt](../README.md#the-prompt) shows: what keeping costs through a
median turn of 9 requests and a mean one of 38, and what a compaction cost the
requests after it, about 20k tokens read again and about 6 more wasted
requests, with a note of what those counts miss. At the cases' recorded sizes
it compacts at every turn end, the four mid-task ones at 519k–681k included,
where `detail` kept at all of them. That matches the cost model in
[When to compact](../docs/when-to-compact.md), where compacting at every turn
end from about 250k costs least and the detail lost was small where it could be
measured. Its response to size is noisy: on the mid-task cases it kept in 3 of
8 runs told 450k and in 5 of 8 told 750k. A first wording, which said a
compaction "cost the requests after it little", compacted in 46 of 48 runs.

`now` is `measured` with the costs it states from the cost model
([e36d81d](https://github.com/max-sixty/sparkjoy/commit/e36d81d)): what a
compaction leaves, 122k, of which all but 30k takes a cache write, against 142k
for the requests after it, and a payback rounded up, such as 13 requests at
500k where `measured` said 14. It compacts at the same cases, and keeps in 3 of
the 8 mid-task runs (both at leaf-menu-design, one at
leaf-ring-test-rewrite-wait), against none for `measured`. With two runs a
case, that difference is within the noise above (Fisher's exact test, p = 0.2).

## Against the live plugin

Whether the harness answers as the live plugin does is still open. Sanitizing
isn't the difference on the one case tried: on tend-eval-rerun-wait, replaying
the unsanitized transcript (its thinking, instructions and skills back in, 406k
replayed), giving the model Claude Code's default tools, or cutting the fixture
before the reply as the plugin's fork does each still compacted, in four runs.

The live plugin can't settle it yet, because until sparkjoy it wrote only its
compactions into the transcript and sent each keep to a debug log that isn't
kept. From 7 to 15 November it asked at 198 turn ends and compacted at 122. The
transcripts account for the other 76 like this:

| what followed the turn end | turn ends |
|---|---|
| the next turn began within 45 seconds, before a verdict could stand | 28 |
| the plugin's files changed and it reloaded within a minute | 21 |
| a "Compacting" line, but no compaction | 3 |
| nothing: the model kept, or the check failed | 24 |

So the model said compact at 125 of the at most 149 verdicts that stood. At
four of the 24 silent turn ends, including leaf-scroll-fix-merged, the harness
compacted in 11 of 12 runs. Either it leans toward compacting, or the live
checks there failed. sparkjoy writes every verdict into the transcript, so a
few days of use will give live verdicts to compare the harness against.

## Fixtures

The 12 fixtures take 3.5 MB, gzipped. Before publishing, `build.py` removed:

- everything the harness injected rather than the conversation said: system
  reminders, instruction files, memory, skill listings, skill and command
  bodies (a placeholder says one was loaded), and environment, tool-listing
  and permission attachments;
- the hidden thinking blocks, and images (a placeholder marks each);
- home paths (`~` instead), email addresses (`user@example.com`), credentials
  of every common form, local page tokens and private hostnames;
- any tool output naming the author's work outside open source, replaced by
  a placeholder;
- every field of an entry but those a resume reads.

`check.py` fails on any home path, email address, credential, private
hostname or phone number left in a fixture, on text read out of Claude Code's
own binary, and on any word or phrase from a
list of the author's private terms, which it holds only as hashes so that it
doesn't publish them itself. It passes on the fixtures as committed.
