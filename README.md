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

This is one of my sessions, replayed without the plugin for comparison. Every
request Claude makes re-reads the whole context, so the area under each line is
what the session paid to read. Each dot is a turn end where the plugin asked the
session's own model whether to compact. At the six filled dots it compacted, at
sizes from 417k to 728k, when a piece of work had ended:

> Long transcript of finished exploration, reverted attempts and diffs;
> remaining work is CI follow-up and the final closeout, which a summary
> covers. *(728k)*

> The task is waiting on the user's picks, and the context holds bulky finished
> probe output and file reads that later work won't need. *(417k)*

> The hierarchy PR is open and handed over. The follow-up work needs only the
> summary, not the transcript's diffs and logs. *(680k)*

At the five hollow dots the session went on without a compaction, and the
plugin that week didn't record why. Where the line climbs with no dot, either a
single long turn is still running, and the plugin waits for it to end, or the
context is still under 400k, where the plugin doesn't ask yet
([Limits](#limits)). This session cost 25% less with the plugin, against 23%
across all my sessions.

## Install

At the prompt of a Claude Code session:

```
/plugin install sparkjoy --marketplace max-sixty/sparkjoy
```

Answer `y` to add the marketplace, then choose the user scope to have it in
every session. It is tested on Claude Code 2.1.325, and
`claude plugin disable sparkjoy` turns it off.

## What it does

A turn is everything Claude does between your message and its reply, often
dozens of API requests.

- **After each turn**, the plugin asks the session's own model whether to
  compact now. The model reads the conversation from the prompt cache and
  answers with one line of JSON. On yes, the plugin compacts, which takes about
  a minute while you read the reply.
- **When a session sits idle for 55 minutes**, the plugin asks again, adding
  that the prompt cache is about to lapse and the next message would write the
  whole context back to it. The model then compacts unless the work is in the
  middle of something. Claude Code has its own
  [idle compaction](https://code.claude.com/docs/en/prompt-caching#compacting-the-conversation),
  but in my sessions it fired only in terminal sessions, never in background
  ones (`claude --bg`), which this covers. This runs on a Claude subscription
  only, where the cache lasts an hour; with an API key it lasts five minutes.
- **Each decision shows as a dim line in the transcript**, which the model never
  reads:

  ```
  sparkjoy: Compacting at 401k tokens: Long transcript of finished experiments, diffs and query output; …
  ```

  A no shows as `Kept at …` with its reason, so a session that stays large says
  why.

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

On a subscription the same tokens count against your usage limits instead of
your bill. Most of the large contexts left come from long turns, where the
plugin can't act until the turn ends. Of the 87 sessions, the 59 that reached
400k saved from 41% down to one that cost 19% more, half of them between 12%
and 31%.

**What a check costs.** A check is one request that re-reads the conversation
from the prompt cache and writes about a hundred tokens. A cached read costs
[0.05× the input price](https://platform.claude.com/docs/en/about-claude/pricing)
on Opus 5.5 and Sonnet 5.5, so a check costs about as much as one ordinary
request in the session. Only turns that end at 400k or more are checked: 198 of
the 36,251 requests, which came to $22. The model said yes at 122 of them.

**What a compaction costs and saves.** Over my 141 compactions, the first
request after one carried a median 122k tokens: an 85k opening prompt (system
prompt, tools, instructions), a summary of about 3k, and 34k of recent messages
and files read back in. That request writes the ~120k to the cache, and a cache
write costs as much as 40 cached reads, so a compaction costs about what
reading 5M cached tokens would. Each later request then reads 120k instead of
the whole context:

| compacting at | each later request reads | pays for itself after |
| ------------- | ------------------------ | --------------------- |
| 200k          | 80k less                 | about 65 requests     |
| 400k          | 280k less                | about 19 requests     |
| 700k          | 580k less                | about 9 requests      |

A turn in these sessions averaged about 36 requests. So at 200k a compaction
rarely pays, even when the work has reached a good place to stop: it writes
120k to the cache to save reading 80k a request. The question gives the model
this sum for the context it holds.

**Idle sessions.** In my background sessions over the same days, 92 returns
after an hour or more wrote 27.6M tokens back to the cache, about $220 at Opus
5.5's one-hour cache write price. Compacting each first would have cost about
$110 less, and the plugin asks first, so it will compact fewer of them. This
part of the plugin is new, so that figure is an estimate, and the replay above
leaves it out.

## How it decides

The session's own model makes the call, reading the whole transcript from the
prompt cache. Which parts of the transcript the work ahead still needs depends
on the work, and the session's model is the one that knows the work. The
question gives it the costs and the saving to weigh, sized for its context,
rather than a rule. An idle session gets the same question, told that the
prompt cache is about to lapse.

So far it says yes too readily. Across my sessions it was asked at 198 turn
ends and compacted at 122, at sizes up to 842k, half of them above 471k. At 49
of the other 76 its verdict never stood, because the next turn began first or
the plugin reloaded. Of the at most 149 verdicts that stood, 125 said compact.
The [evals](evals/README.md) put the question to it at 12 recorded turn
ends: it compacted at all of them, including four in the middle of a task and
two natural breaks at 210k and 296k, where a compaction pays back too slowly.

<details>
<summary>The question, verbatim, for a 500k context</summary>

```
This message comes from the sparkjoy plugin, not from the user, and a program reads your reply. Use no tools.

Your turn just ended with this reply to the user:

<reply>
…the reply…
</reply>

Decide whether to compact the conversation now, while the user reads that reply.

After a compaction the context holds the system prompt and instructions, a summary of the conversation, the latest messages verbatim (this reply among them), the files read most recently, the status of background agents and shells, and the transcript's path on disk, where exact output or wording can be looked up. What is lost is detail the summary leaves out, and the knowledge that it was there to look up.

A compaction takes about a minute and writes a summary of about 4k output tokens. Afterwards the context holds about 120k tokens, which the next request writes to the cache again. A cache write costs as much as 40 cached reads, and an output token as much as 100.

It saves carrying the parts of the transcript the work ahead no longer needs, such as file reads, command output and diffs for finished work, and detours since resolved. Every later request reads whatever the context keeps: about 120k after a compaction, against 500k now. At 500k a compaction pays for itself after about 14 more requests, as long as the work ahead doesn't need what it dropped; a turn of work often takes dozens of requests.

Reply with one JSON object and nothing else:
{"compact": true or false, "reason": "one short line saying why"}
```

Asked of an idle session, its second and fourth paragraphs read as below, and
its sixth stops after "against 500k now.":

```
Your turn ended 55 minutes ago with this reply to the user, and nothing has happened since:

Decide whether to compact the conversation now. The prompt cache holding it lapses in a few minutes, and the first request after that writes all 500k tokens to the cache again, against about 120k after a compaction. So compact unless the work is in the middle of something that needs the transcript's detail.
```

</details>

## Limits

- It asks only once the context reaches 400k, or 200k in an idle session,
  because the question says yes too readily ([How it decides](#how-it-decides)).
  At 400k a compaction pays back within a typical turn. A question that told a
  pause in the work from its end would let that floor go.
- It acts between turns only. A long autonomous turn can grow large before it
  ends; Claude Code's own compaction near the end of the window still bounds
  it.
- It is made for 1M-token context windows. On a 200k window, Claude Code
  compacts before the context reaches 400k.
- A compaction loses detail the summary leaves out. The replay below can't
  count what that costs the work that follows, such as re-reading a file.

## How the numbers were measured

The replay keeps every logged request fixed and moves only the compactions.
"With" uses the plugin's compactions where they happened; "without" removes them
and lets Claude Code compact near 965k, as it does on its own. Both price each
request at Opus 5.5 list prices with the session's own cache lifetime, and the
"with" replay comes within 2% of the cost the transcripts log ($4,013 against
$3,943). In these sessions the plugin also asked its question below 400k and
ignored the answer there; it no longer asks below 400k, so the replay prices
checks from 400k only.

## License

MIT
