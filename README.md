# sparkjoy

A Claude Code plugin that compacts the conversation when a piece of work is
finished, rather than when the context window fills. At the end of each turn
it puts Marie Kondo's question to the session's own model: does the work ahead
still need this transcript, or can it go?

> [!NOTE]
> Not ready for general use yet. I run it in all my sessions, and the question
> it asks is still being tuned; expect it to change.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/session-dark.svg">
  <img alt="Context per request across one session. With sparkjoy, dots mark the 11 turn ends where the plugin asked whether to compact: at 5 the session kept going, and at 6 it compacted, at sizes from 417k to 728k tokens. Without it, the context climbs to about 960k before Claude Code compacts." src="assets/session-light.svg">
</picture>

One of my sessions, replayed without the plugin for comparison. Every request
re-reads the whole context, so the area under each line is what the session
paid to read. At each dot the plugin asked the session's model whether to
compact, and at the six filled ones it did, when a piece of work had ended:

> Long transcript of finished exploration, reverted attempts and diffs;
> remaining work is CI follow-up and the final closeout, which a summary
> covers. *(728k)*

> The hierarchy PR is open and handed over. The follow-up work needs only the
> summary, not the transcript's diffs and logs. *(680k)*

At the hollow dots the session went on without a compaction; the plugin that
week didn't record why. Where the line climbs with no dot, a long turn is still
running or the context is under 400k, the plugin's floor that week
([Limits](#limits)). This session cost 25% less with the plugin, and all my
sessions 23% less.

## Install

At the prompt of a Claude Code session:

```
/plugin install sparkjoy --marketplace max-sixty/sparkjoy
```

Answer `y` to add the marketplace, and choose the user scope to have it in
every session. It is tested on Claude Code 2.1.325;
`claude plugin disable sparkjoy` turns it off.

## What it does

A turn is everything Claude does between your message and its reply, often
dozens of requests.

- **After each turn**, the plugin asks the session's own model, which reads the
  conversation from the prompt cache, whether to compact now
  ([the prompt](#the-prompt)). On yes it compacts, which takes about a minute
  while you read the reply.
- **When a session sits idle for 55 minutes**, it asks again, adding that the
  prompt cache is about to lapse and the next message would make a cache write
  of the whole context. Claude Code's own
  [idle compaction](https://code.claude.com/docs/en/prompt-caching#compacting-the-conversation)
  skips background sessions (`claude --bg`): in mine it fired in none of 358
  idle gaps of 55 minutes or more. This needs a Claude subscription, where the
  cache lasts an hour, and a computer that stays awake: a session the
  computer slept through is left alone, its cache already gone.
- **Each decision shows as a dim line in the transcript**, which the model never
  reads, with its reason:

  ```
  sparkjoy: Compacting at 401k tokens: Long transcript of finished experiments, diffs and query output; …
  ```

Claude Code's own compaction near the end of the window still runs, and
`claude -p` and the Agent SDK are left alone.

## What it saves

I replayed all 87 of my Claude Code sessions from 7 to 15 November 2026, 36,251
requests, with and without the plugin:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/cost-dark.svg">
  <img alt="Replayed cost: $5,214 without the plugin, $4,013 with it, of which the checks are $22." src="assets/cost-light.svg">
</picture>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/context-dark.svg">
  <img alt="Share of requests at each context size. With the plugin, most requests carry 100k to 450k tokens; without it, a long tail runs to 1M." src="assets/context-light.svg">
</picture>

|                                 | without | with          |
| ------------------------------- | ------- | ------------- |
| cost, at Opus 5.5 list prices   | $5,214  | $4,013 (−23%) |
| mean context per request        | 462k    | 337k          |
| requests at 600k tokens or more | 28%     | 6%            |

On a subscription the same tokens count against your usage limits instead.
The large contexts left come mostly from long turns, which the plugin can't
act on until they end. Of the 59 sessions that reached 400k, half saved between 12% and 31%, and
one cost 19% more.

**What a check costs.** A check is one request: a cache read of the conversation
and about a hundred output tokens. A cached read costs
[0.05× the input price](https://platform.claude.com/docs/en/about-claude/pricing)
on Opus 5.5 and Sonnet 5.5, so a check costs about as much as an ordinary
request. The 198 turns that ended at 400k or more were checked, for $22 in
all, and 122 of them compacted.

**What a compaction costs and saves.** After my 141 compactions, the first
request carried a median 122k tokens: an 85k opening prompt (system prompt,
tools, instructions), a summary of about 3k, and 34k of recent messages and
files. The requests after it read about 20k more again, mostly files the
summary left out. Of those 142k, all but the 30k system prompt and tools,
which stay in a cache shared across sessions, take a cache write, at 39 cached
reads a token more than reading them. With the summary's output, that costs
about what reading 4.8M cached tokens would, $0.95 on Opus 5.5, besides the
summary's read of the context. Each later request then reads 142k plus what it
has added since, instead of the whole context. A token held costs 1.22 cached
reads a request, counting the 0.56% of requests that find the cache lapsed and
make a cache write of it again. The context grows about 1.7k tokens a request,
so it takes many requests to grow back:

| compacting at | each later request reads | pays for itself after | context grows back in |
| ------------- | ------------------------ | --------------------- | --------------------- |
| 200k          | 58k less                 | 71 requests           | 34 requests           |
| 400k          | 258k less                | 17 requests           | 151 requests          |
| 700k          | 558k less                | 9 requests            | 327 requests          |

A compaction repays over every request until the context grows back, not
within one turn. Compacting costs least where it pays for itself in about half
the time the context takes to grow back. [When to compact](docs/when-to-compact.md) models this and
fits it to the same sessions: compacting at every turn end past about 200k
costs least, saving 30.2% against 25.1% past 400k. If more context helps a
request, up to a limit set by s, the context at which a request gets half as
much done as it could, and the sessions put s at no more than about 100k, the
best turn-end threshold rises to 250k. The question gives the model the payback for its
context.

**Idle sessions.** In my background sessions over the same days, 92 returns
after an hour or more made cache writes of 27.6M tokens, about $220.
Compacting first would have cost about $110 less; the plugin asks first, so it
will compact fewer of them. This part is new, so that figure is an estimate,
and the replay above leaves it out.

## Report

`/sparkjoy:report` writes a page about your own sessions of the last 14 days
and opens it: every verdict sparkjoy logged, what its compactions saved
by the [replay](#how-the-numbers-were-measured), the sessions you came back to
after an hour idle, and the thresholds the
[cost model](docs/when-to-compact.md) fits to them. It needs
[uv](https://docs.astral.sh/uv/).

```
/sparkjoy:report
```

## How it decides

The session's own model makes the call, reading the whole transcript from the
prompt cache, since which parts the work ahead needs depends on the work. The
question names what decides it, whether the next turns will need detail a
summary drops, and gives the costs, sized for the context.

The first question said yes too readily. Of its at most 149 live verdicts that
stood, 125 said compact; the other 49 of 198 checks never stood, because the
next turn began first or the plugin reloaded. In the
[evals](evals/README.md), it compacted at all 12 recorded turn ends, mid-task
ones included, reasoning that the transcript was mostly finished work, as
nearly any long transcript is.

The second question said that settles nothing, and asked whether the next
turns need detail a summary drops. In the evals it kept at all four mid-task
turn ends and compacted at the finished ones, but its verdict didn't change
with the context's size, and live it compacted at only 20 of its first 84
turn ends, from 401k to 911k.

The question now also says what keeping costs, which grows with the context,
and what compactions cost the requests after them in my sessions, and asks the
model to weigh the two. In the evals it compacts at every finished turn end,
as the [cost model](docs/when-to-compact.md) recommends past about 250k, and
in 5 of 8 runs at the mid-task ones, 519k–681k.

## The prompt

`question()` in [`hooks/register.ts`](hooks/register.ts) writes it, filled in
for the session's context size and the reply the turn ended with.

<details>
<summary>At the end of a turn, for a 500k context</summary>

```
This message comes from the sparkjoy plugin, not from the user, and a program reads your reply. Use no tools.

Your turn just ended with this reply to the user:

<reply>
…the reply…

</reply>

Decide whether to compact the conversation now, while the user reads that reply.

Nearly every long transcript is mostly finished work, so that alone is no reason to compact. What decides it is whether the next turns will need detail a summary drops: exact output, what was tried and failed, what the user objected to and why, the state of a design or fix still taking shape. Work that iterates with the user on something just shown, or takes up a half-done fix or design, needs that detail; after a compaction it re-reads and re-runs what it can and loses the rest. Work that only reports a result, or starts something new, needs no more than a summary.

A compaction takes about a minute and writes a summary of about 4k output tokens. Afterwards the context holds about 122k tokens, which the next request writes to the cache again, all but the 30k of system prompt and tools, which stay cached. A cache write costs as much as 40 cached reads, and an output token as much as 100. At 500k a compaction pays for itself after about 13 more requests that need nothing it dropped.

Keeping costs too, and more the larger the context: every later request reads all 500k tokens, against about 142k after a compaction, and keeping only puts the compaction off to a later turn end. A turn runs a median of 9 requests and a mean of 38, so keeping 500k through one more turn costs as much as about 0.7 compactions for a median turn and 3 for a mean one.

After a compaction, past sessions read about 20k tokens of files again and had about 6 more wasted requests than usual: failed tool calls, repeated lookups and corrections. Those counts miss detail lost without such a failure, such as a design's rejected options or what the user likes.

Weigh what keeping costs against what losing the detail would cost the next turns. The larger the context, the more detail a compaction can drop and still pay.

Reply with one JSON object and nothing else, filling its fields in order:
{"next": "what most likely comes next, in a few words", "needs_detail": true or false, "compact": true or false, "reason": "one short line saying why"}
```

</details>

<details>
<summary>In a session idle for 55 minutes, for a 500k context</summary>

```
This message comes from the sparkjoy plugin, not from the user, and a program reads your reply. Use no tools.

Your turn ended 55 minutes ago with this reply to the user, and nothing has happened since:

<reply>
…the reply…

</reply>

Decide whether to compact the conversation now. The prompt cache holding it lapses in a few minutes, and the first request after that writes all but 30k of its 500k tokens to the cache again, against about 92k after a compaction. So compact unless the next turn continues work that needs the transcript's detail.

Nearly every long transcript is mostly finished work, so that alone is no reason to compact. What decides it is whether the next turns will need detail a summary drops: exact output, what was tried and failed, what the user objected to and why, the state of a design or fix still taking shape. Work that iterates with the user on something just shown, or takes up a half-done fix or design, needs that detail; after a compaction it re-reads and re-runs what it can and loses the rest. Work that only reports a result, or starts something new, needs no more than a summary.

A compaction takes about a minute and writes a summary of about 4k output tokens. Afterwards the context holds about 122k tokens, which the next request writes to the cache again, all but the 30k of system prompt and tools, which stay cached. A cache write costs as much as 40 cached reads, and an output token as much as 100.

After a compaction, past sessions read about 20k tokens of files again and had about 6 more wasted requests than usual: failed tool calls, repeated lookups and corrections. Those counts miss detail lost without such a failure, such as a design's rejected options or what the user likes.

Weigh what keeping costs against what losing the detail would cost the next turns. The larger the context, the more detail a compaction can drop and still pay.

Reply with one JSON object and nothing else, filling its fields in order:
{"next": "what most likely comes next, in a few words", "needs_detail": true or false, "compact": true or false, "reason": "one short line saying why"}
```

</details>

## Limits

- It asks only from 250k, or 175k in an idle session: the pair of floors that
  costs least when my sessions are replayed
  ([When to compact](docs/when-to-compact.md)), taking every check to say yes.
- The question's figures for a turn's length and for what a compaction costs
  the requests after it come from my sessions, not yours.
- It acts between turns only. Claude Code's own compaction near the end of the
  window still bounds a long turn.
- It is made for 1M-token context windows; on a 200k window, Claude Code
  compacts before the context reaches 250k.
- A compaction loses detail the summary leaves out, and the replay can't count
  what that costs the work after it, such as re-reading a file.

## How the numbers were measured

The replay keeps every logged request fixed and moves only the compactions:
"with" keeps the plugin's, and "without" removes them and lets Claude Code
compact near 965k, as it does on its own. Both price each request at Opus 5.5
list prices with the session's own cache lifetime, and "with" comes within 2%
of the cost the transcripts log ($4,013 against $3,943).

## License

MIT
