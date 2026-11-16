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
running or the context is under 400k ([Limits](#limits)). This session cost
25% less with the plugin, and all my sessions 23% less.

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
  prompt cache is about to lapse and the next message would write the whole
  context back to it. Claude Code's own
  [idle compaction](https://code.claude.com/docs/en/prompt-caching#compacting-the-conversation)
  skips background sessions (`claude --bg`): in mine it fired in none of 358
  idle gaps of 55 minutes or more. This needs a Claude subscription, where the
  cache lasts an hour.
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

**What a check costs.** A check is one request that reads the conversation from
the cache and writes about a hundred tokens. A cached read costs
[0.05× the input price](https://platform.claude.com/docs/en/about-claude/pricing)
on Opus 5.5 and Sonnet 5.5, so a check costs about as much as an ordinary
request. The 198 turns that ended at 400k or more were checked, for $22 in
all, and 122 of them compacted.

**What a compaction costs and saves.** After my 141 compactions, the first
request carried a median 122k tokens: an 85k opening prompt (system prompt,
tools, instructions), a summary of about 3k, and 34k of recent messages and
files. That request writes the ~120k to the cache, at 40 times the price of a
cached read, so a compaction costs about what reading 5M cached tokens would.
Each later request then reads 120k instead of the whole context:

| compacting at | each later request reads | pays for itself after |
| ------------- | ------------------------ | --------------------- |
| 200k          | 80k less                 | about 65 requests     |
| 400k          | 280k less                | about 19 requests     |
| 700k          | 580k less                | about 9 requests      |

A compaction repays over every request until the context grows back, not
within one turn. [When to compact](docs/when-to-compact.md) models this and
fits it to the same sessions: compacting at every turn end past about 200k
costs least, saving 30.2% against 25.1% past 400k. A coefficient for how
context size changes the model's work, from 0.8 to 1.2, moves that by tens of
thousands of tokens. The question gives the model the payback for its
context.

**Idle sessions.** In my background sessions over the same days, 92 returns
after an hour or more wrote 27.6M tokens back to the cache, about $220.
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

The question now says that settles nothing. In the evals it keeps at all four
mid-task turn ends and compacts at all six finished ones. It also compacts at
natural breaks at 210k and 296k, which the evals label keep, though by the
cost model compacting there pays.

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

A compaction takes about a minute and writes a summary of about 4k output tokens. Afterwards the context holds about 120k tokens, which the next request writes to the cache again. A cache write costs as much as 40 cached reads, and an output token as much as 100. At 500k a compaction pays for itself after about 14 more requests that need nothing it dropped.

Compact only when the next turns won't need such detail and enough requests will follow to repay it. Otherwise keep it: a later turn end, once that work is finished, is the place to compact.

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

Decide whether to compact the conversation now. The prompt cache holding it lapses in a few minutes, and the first request after that writes all 500k tokens to the cache again, against about 120k after a compaction. So compact unless the next turn continues work that needs the transcript's detail.

Nearly every long transcript is mostly finished work, so that alone is no reason to compact. What decides it is whether the next turns will need detail a summary drops: exact output, what was tried and failed, what the user objected to and why, the state of a design or fix still taking shape. Work that iterates with the user on something just shown, or takes up a half-done fix or design, needs that detail; after a compaction it re-reads and re-runs what it can and loses the rest. Work that only reports a result, or starts something new, needs no more than a summary.

A compaction takes about a minute and writes a summary of about 4k output tokens. Afterwards the context holds about 120k tokens, which the next request writes to the cache again. A cache write costs as much as 40 cached reads, and an output token as much as 100.

Compact only when the next turns won't need such detail and enough requests will follow to repay it. Otherwise keep it: a later turn end, once that work is finished, is the place to compact.

Reply with one JSON object and nothing else, filling its fields in order:
{"next": "what most likely comes next, in a few words", "needs_detail": true or false, "compact": true or false, "reason": "one short line saying why"}
```

</details>

## Limits

- It asks only from 400k, or 200k in an idle session. On cost alone the best
  thresholds are lower, about 200–250k at turn ends and 150–175k when idle
  ([When to compact](docs/when-to-compact.md)); the higher floors limit what a
  wrong verdict costs while the question is new.
- It acts between turns only. Claude Code's own compaction near the end of the
  window still bounds a long turn.
- It is made for 1M-token context windows; on a 200k window, Claude Code
  compacts before the context reaches 400k.
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
