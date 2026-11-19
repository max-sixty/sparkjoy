# sparkjoy

A Claude Code plugin that compacts the conversation when a piece of work is
finished, rather than when the context window fills. At the end of each turn
it puts Marie Kondo's question to the session's own model: does this
transcript spark joy? That is, does the work ahead still need it, or can it go?

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
every session. It is tested daily on the latest and stable Claude Code
releases; `claude plugin disable sparkjoy` turns it off.

## What it does

A turn is everything Claude does between your message and its reply, often
dozens of requests.

- **After each turn**, the plugin asks the session's own model, which reads the
  conversation from the prompt cache, whether to compact now
  ([the prompt](#the-prompt)). On yes it compacts, which takes about a minute
  while you read the reply. If Claude Code restarts the session before the
  compaction finishes, as it does to each background session after an update,
  the plugin compacts when the session starts again.
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

- **`/sparkjoy:report`** writes a page about your own sessions of the last 14
  days and opens it: every verdict, what the compactions saved, and the
  thresholds the [cost model](#cost-model) fits to them. From then on the
  question states the figures it fitted in place of mine. It needs
  [uv](https://docs.astral.sh/uv/).

Claude Code's own compaction near the end of the window still runs, and
`claude -p` and the Agent SDK are left alone.

The top of my report:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/report-dark.png">
  <img alt="A sparkjoy report for 4 to 18 November: 505 verdicts, 374 compacted and 131 kept; saved $3,208, 22% of what the sessions would have cost; then a table of sessions, each with its context per request with and without sparkjoy's compactions, its verdicts and its cost." src="assets/report-light.png">
</picture>

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

Of the 59 sessions that reached 400k, half saved between 12% and 31%, and one
cost 19% more. On a subscription the same tokens count against your usage
limits instead.

A check is one request, which reads the conversation from the cache and writes
about a hundred tokens. The 198 checks, at turn ends of 400k or more, cost $22
in all, and 122 of them compacted.

A compaction costs about $0.99 on Opus 5.5, mostly to write to the cache the
142k tokens it leaves. Every later request then reads less:

| compacting at | each later request reads | pays for itself after | context grows back in |
| ------------- | ------------------------ | --------------------- | --------------------- |
| 200k          | 58k less                 | 74 requests           | 34 requests           |
| 400k          | 258k less                | 17 requests           | 151 requests          |
| 700k          | 558k less                | 9 requests            | 327 requests          |

## How it decides

The session's own model makes the call, reading the whole transcript from the
prompt cache, since which parts the work ahead needs depends on the work. The
question tells it that a transcript of mostly finished work is no reason by
itself to compact, since nearly every long transcript is one. What decides it
is whether the next turns will need detail a summary drops, such as exact
output, failed attempts or a design still taking shape.

The question also gives both costs, sized for the context: what a compaction
costs and how many requests it takes to pay for itself, and what keeping the
context through another turn costs, which grows with the context. The model
weighs the two and answers with its verdict and a one-line reason. In the
[evals](evals/README.md) it compacts at every finished turn end, as the
[cost model](#cost-model) recommends past about 250k, and at the
mid-task ones, from 519k to 681k, in 5 of 8 runs.

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

A compaction takes about a minute, and the request that writes its summary outputs about 6k tokens, thinking included. Afterwards the context holds about 142k tokens, counting the files the next requests read again, and those requests write it to the cache again, all but the 30k of system prompt and tools, which stay cached. A cache write costs as much as 40 cached reads, and an output token as much as 100. At 500k a compaction pays for itself after about 13 more requests that need nothing it dropped.

Keeping costs too, and more the larger the context: every later request reads all 500k tokens, against about 142k after a compaction, and keeping only puts the compaction off to a later turn end. A turn runs a median of 9 requests and a mean of 38, so keeping 500k through one more turn costs as much as about 0.7 compactions for a median turn and 3 for a mean one.

After a compaction, past sessions had about 6 more wasted requests than usual: failed tool calls, repeated lookups and corrections. That count misses detail lost without such a failure, such as a design's rejected options or what the user likes.

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

Decide whether to compact the conversation now. The prompt cache holding it lapses in a few minutes, and the first request after that writes all but 30k of its 500k tokens to the cache again, against about 112k after a compaction. So compact unless the next turn continues work that needs the transcript's detail.

Nearly every long transcript is mostly finished work, so that alone is no reason to compact. What decides it is whether the next turns will need detail a summary drops: exact output, what was tried and failed, what the user objected to and why, the state of a design or fix still taking shape. Work that iterates with the user on something just shown, or takes up a half-done fix or design, needs that detail; after a compaction it re-reads and re-runs what it can and loses the rest. Work that only reports a result, or starts something new, needs no more than a summary.

A compaction takes about a minute, and the request that writes its summary outputs about 6k tokens, thinking included. Afterwards the context holds about 142k tokens, counting the files the next requests read again, and those requests write it to the cache again, all but the 30k of system prompt and tools, which stay cached. A cache write costs as much as 40 cached reads, and an output token as much as 100.

After a compaction, past sessions had about 6 more wasted requests than usual: failed tool calls, repeated lookups and corrections. That count misses detail lost without such a failure, such as a design's rejected options or what the user likes.

Weigh what keeping costs against what losing the detail would cost the next turns. The larger the context, the more detail a compaction can drop and still pay.

Reply with one JSON object and nothing else, filling its fields in order:
{"next": "what most likely comes next, in a few words", "needs_detail": true or false, "compact": true or false, "reason": "one short line saying why"}
```

</details>

## Limits

- It asks only from 250k, or 175k in an idle session: the pair of floors that
  costs least when my sessions are replayed
  ([Cost model](#cost-model)), taking every check to say yes.
- Until you run `/sparkjoy:report`, the question's figures for a turn's length
  and for what a compaction costs come from my sessions. The report fits them
  to yours, all but the wasted requests after a compaction, which take months
  of sessions to measure.
- It acts between turns only. Claude Code's own compaction near the end of the
  window still bounds a long turn.
- It acts only in sessions with a 1M-token context window, the size its
  floors and figures come from. On a 200k window, Claude Code compacts before
  the context reaches 250k.

## How the numbers were measured

The replay keeps every logged request fixed and moves only the compactions:
"with" keeps the plugin's, and "without" removes them and lets Claude Code
compact near 965k, as it does on its own. Both price each request at Opus 5.5
list prices with the session's own cache lifetime, and "with" comes within 2%
of the cost the transcripts log ($4,013 against $3,943).

## Cost model

sparkjoy's floors come from a model of what keeping a context and compacting it
cost, fitted to my sessions. It is also on [a page of its own](docs/cost-model.md).

<!-- Generated from docs/cost-model.md by docs/embed.py; edit that file. -->
<details>
<summary>The cost model</summary>

The cost model finds when to compact from the context's size alone, without
knowing whether the work ahead needs what the context holds. Its answer sets
sparkjoy's floor. At each turn end past the floor, sparkjoy asks whether the
next turns need the detail a summary would drop, and [Value of
information](#value-of-information) models what a good answer is worth. For
Opus 5.5 with the one-hour cache, compacting costs least when the context reaches about
**260k**<sup>1</sup>.

### Trade-off

Every request reads the whole context from the cache, so each request costs more
as the context grows. Costs here are in CRTs, cached-read tokens. One CRT is the
price of reading one token from the prompt cache, and [the table at the
end](#prices-by-model) gives that price for each model. A compaction cuts the
context back to about 150k<sup>2</sup>, but costs about 5M CRTs<sup>3</sup> plus one
read of the context. Compacting early keeps requests small, but the session
compacts often; compacting late means fewer compactions but larger requests.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/model-cycles-dark.svg">
  <img alt="Eight panels, two rows by four columns, each over 600 requests. Columns: compact at 175k, at 260k, at 400k, and never compact, where Claude Code compacts at 965k. Top row, the context in tokens: a sawtooth from 142k up to 175k every 19 requests, to 260k every 69, to 400k every 151 and to 965k every 482. Bottom row, CRTs per request: the request's own cost, with its share of the compaction ending its cycle on top, thick at 175k and thin at 965k. A line marks each column's mean cost per request: 600k (73% of never compacting), 450k (55%), 500k (61%) and 820k (100%)." src="assets/model-cycles-light.svg">
</picture>

The model at four compaction points over 600 requests. The top row is the context,
which grows from about 150k by about 1.7k tokens a request<sup>4</sup> and drops
back at each compaction. The bottom row is each request's cost, with its share
of the compaction that ends its cycle on top. Compacting at 175k, a compaction
comes every 20 or so requests, and its share is nearly half of each request's
cost. Never compacting leaves compaction to Claude Code at 965k, and the
requests just before that cost 1.3M CRTs each.

### Request and compaction costs

A request costs about 300k CRTs at 150k and 1M at 700k, mostly for the cache
read of its context. A compaction costs about 5M CRTs at any context size, most
of it the cache write of the context it leaves. Every part except the read of
the context is measured from 36,249 of my requests.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/model-inputs-dark.svg">
  <img alt="Two panels against the context x, from 0 to 965k, in CRTs; parts named in orange are fitted to my sessions. Top, c(x), a request's cost by part, the fixed parts at the bottom: output and uncached input, and the cache write of the new tokens, about 140k together; above them the cache write of the context after a lapse and the cache read of the context, both growing; 310k in all at 142k and 990k at 700k. Bottom, F(x), a compaction's cost by part, about 5M at any context: the compaction request's output, 0.6M, the cache write of the context after it, 4.4M, and the cache read of the context, the only part that grows; 5.6M at 700k." src="assets/model-inputs-light.svg">
</picture>

Each cost by part, against the context. In the first panel, a request's
output, uncached input and the cache write of the tokens it adds come to about
140k CRTs at any context. The rest grows with the context: its cache read, and
the cache write of the whole context on the 0.56% of requests that find the
cache lapsed. In the second, 4.4M of a compaction's 5M CRTs is the cache write
of the context after it. Only the system prompt and tools, cached across
sessions, escape that write.

<details>
<summary>What share of my sessions' bill each kind of token took</summary>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/model-bill-dark.svg">
  <img alt="One bar of my sessions' bill by kind of token: output, thinking included, 14%, cache writes 25%, cache reads 61%." src="assets/model-bill-light.svg">
</picture>

My sessions' 36,000 requests, each priced from its logged usage at Opus
5.5. Uncached input, 0.01%, is too thin to draw. Output is a small share because
each request reads its whole context again: its 746 output tokens cost as much
as 75k cached reads, against a context of 335k tokens on average. Compactions
account for 3.2% of the bill, spread over these kinds. Most of it, 2.4%, is the
cache write of the compacted context, on the request after each compaction. The
compaction's own request isn't logged, so its tokens come from the model: it
reads the context it compacts, 0.35%, and writes the summary, 0.4%.

</details>

The table lists every symbol the model uses. The variables are the contexts it
ranges over: x, a request's context, which grows over each cycle, and X, the
compaction point, which the model chooses. The parameters are numbers the model
takes as given: measured in my sessions, set by list price, assumed, or derived
from the others. The functions give cost and effectiveness at each context, and
the result is the best compaction point, X\*. [Sensitivity](#sensitivity) shows
how far each measured parameter moves it.

**Variables**

| symbol | what it is |
|----|----|
| x | Context of a request, in tokens. Over each cycle between compactions, it grows from K to X. |
| X | Context at which the session compacts, in tokens. The model chooses it. |

**Functions of the variables**

| symbol | what it is |
|----|----|
| c(x) | Cost of a request with context x, in CRTs: $`c(x) = h\,x + c_0`$. |
| F(X) | Cost of a compaction at context X, in CRTs: $`F(X) = F_0 + X`$, since the compaction request reads the whole context. |
| η(x) | Effectiveness of a request with context x, relative to a request with the full 1M window: $`\eta(x) = (x / 1\text{M})^\beta`$. A request's effectiveness is how much it gets done. |
| J(X) | Cost per unit of effectiveness when the session compacts at X, in CRTs, a cost-effectiveness ratio as health economics uses the term. It is a cycle's cost, its requests' and its compaction's, divided by the cycle's effectiveness, the sum of its requests'. |

**Parameters**

| symbol | what it is | value | source |
|----|----|----|----|
| K | Context after a compaction, in tokens. It is the first request's context (122k: the system prompt and tools, instructions, the summary, and recent messages and files), plus the files the next few requests read again (20k). | 142k | measured, 90% interval 137–147k |
| g | Growth of the context per request, in tokens, from tool results, files read and the model's own output. It is the mean over my sessions' requests except the first few after a compaction, since K counts the files those read again. | 1,706 | measured, 1,615–1,805 |
| P | Size of the system prompt and tools, in tokens. Claude Code keeps this part of K in a cache shared across sessions, so it stays cached through a compaction. | 30.4k | measured, 30.4–31.0k |
| ℓ | Share of requests that find the cache lapsed, and so make a cache write of the whole context. The cache lapses when it expires after an hour idle or is invalidated. | 0.56% | measured, 0.51–0.63% |
| N | Output of the compaction request, in tokens: the summary's text plus its thinking. The text is a median 3.5k tokens, and requests that show about that much output produce 1.61 times as much in all, thinking included. | 5.6k | measured, 5.2–5.9k |
| w | Price of one token's cache write to the one-hour cache, in CRTs. A write to the five-minute cache costs 25. | 40 | list price |
| o | Price of one output token, in CRTs. | 100 | list price |
| β | Growth of a request's effectiveness with its context: each doubling of the context makes a request $`2^\beta`$ times as effective. | 0 | assumed |
| h | Cost per request of one token in the context, in CRTs. It is one read, plus the chance that the cache has lapsed times the extra cost of the token's cache write: $`h = 1 + \ell\,(w - 1)`$. | 1.22 | derived |
| c₀ | Fixed cost of a request, the part that doesn't grow with its context, in CRTs. It is the request's output, its uncached input, and the cache write of its g new tokens. | 134k | derived |
| F₀ | Fixed cost of a compaction, in CRTs. It is the compaction request's output, plus the extra cost of the cache write of the K − P tokens outside the shared cache: $`F_0 = oN + (w - 1)(K - P)`$. | 4.93M | derived |

**Result**

| symbol | what it is                                 | value | source  |
|--------|--------------------------------------------|-------|---------|
| X\*    | Value of X that makes J lowest, in tokens. | 261k  | derived |

### Best compaction point

A session that compacts at X follows the sawtooth above. Its context grows from
K to X by g tokens a request, then a compaction costs F(X) and the cycle starts
again. If a request gets as much done at any context, the best X is the one that
minimizes a request's cost averaged over the cycle, including its share of the
compaction:

```math
X^* = K + \sqrt{\frac{2g\,(F_0 + K)}{h}} = 142\text{k} + 119\text{k} = 262\text{k}
```

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/model-average-dark.svg">
  <img alt="Cost per request, in CRTs, against the compaction point X, from 142k to 965k. The average cost per request, its share of the compaction included, falls steeply from 142k to its minimum, about 450k at 260k, then rises to 820k at 965k; a dot marks the minimum. The marginal cost per request, a straight grey line, rises from 310k at 142k to 1.3M at 965k and crosses the average at its minimum." src="assets/model-average-light.svg">
</picture>

A request's cost in the model, averaged over a cycle, against the compaction point.
Each column of the chart above is one point on this curve: 600k CRTs a request
at 175k, 455k at 260k, 500k at 400k and 820k when never compacting. Below 260k
the compaction's share raises the average steeply; above it, larger requests do.
The straight line is the marginal cost per request. While it is below the
average, waiting one more request pulls the average down; once it is above,
waiting pushes the average up. So the average is lowest where the two cross.
This is Harris's economic order quantity, in which the cheapest batch is the one
whose last unit costs the average.

### Context size and effectiveness

260k is the best point only if a request gets as much done at 150k as at 900k.
If more context helps, a compaction also costs effectiveness, because the
requests after it work with less, and the best point comes later. The model
assumes a request's effectiveness, how much it gets done, grows as a power of
its context, so each doubling multiplies it by $`2^\beta`$. At β = 0 a request is
equally effective at any context; at β = 1 its effectiveness grows in proportion
to the context.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/model-effectiveness-dark.svg">
  <img alt="Effectiveness η(x), relative to a request at 1M, against the context x, for β of 0, 0.3, 0.6 and 1, on one blue ramp from light to dark. β = 0, equally effective at any context, is flat at 100%. The others rise from 0 to 100% at 1M, β = 1 in a straight line; at 142k they are at 56%, 31% and 14%." src="assets/model-effectiveness-light.svg">
</picture>

A request's effectiveness in the model, against its context, as a share of a
request's with the full 1M window. A request right after a compaction, at about
150k, gets about 55% as much done as one at 1M with β = 0.3, 30% with 0.6 and
15% with 1.

The best point is then the X that minimizes the cost per unit of effectiveness,
a cycle's cost divided by its effectiveness, the sum of its requests',

```math
J(X) = \frac{g\,F(X) + \int_K^X c(x)\,dx}{\int_K^X \eta(x)\,dx}
```

where $`c(x)`$ is the cost of a request with context x, $`F(X)`$ the cost of
compacting at X and $`\eta(x)`$ a request's effectiveness. At β = 0 the denominator
counts requests, and J is the average cost drawn in [the chart under Best
compaction point](#best-compaction-point).

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/model-cost-dark.svg">
  <img alt="Cost per unit of effectiveness relative to never compacting, against the compaction point from 142k to 965k, for β of 0, 0.3, 0.6 and 1. The first three fall steeply from 142k to a minimum and rise to 100% at 965k; dots mark the minima: 55% at about 260k for β = 0, 72% at 310k for 0.3 and 92% at 440k for 0.6. β = 1 stays above 100% and falls to it at 965k." src="assets/model-cost-light.svg">
</picture>

J against the compaction point, as a share of J when never compacting.
The larger β is, the more effectiveness a compaction costs, so the minimum comes
later and saves less: 45% at about 260k for β = 0, 28% at 310k for 0.3, and 8%
at 440k for 0.6. At β = 1, compacting anywhere before Claude Code's own
compaction costs more than it saves.

### Sensitivity

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/model-sensitivity-dark.svg">
  <img alt="Horizontal bars, one for each parameter measured in my sessions, from the best compaction point at β = 0 with the parameter halved (open dot) to the point with it doubled (filled dot), with a solid segment where the point lies across the parameter's 90% interval, around a line at about 260k, the point with every parameter as measured: K, 150k to 459k, and 254k to 269k across its interval; g, 227k to 311k, and 258k to 265k; P, 268k to 247k, and 261k to 262k; ℓ, 267k to 252k, and 260k to 263k; N, 258k to 268k, and 261k to 262k." src="assets/model-sensitivity-light.svg">
</picture>

How far the best point at β = 0, about 260k, moves when a parameter
measured in my sessions is halved or doubled, and across its 90% interval from
resampling sessions. K, the context after a compaction, moves it most, because K
sets both where the context restarts and how much each compaction's cache write
holds. Across its 90% interval, though, K moves the point only from 254k to
269k, and each other parameter by less than 10k.

The assumptions about the work move the best point more. It reaches 400k only at
β = 0.55, where a request at 200k gets 68% as much done as one at 400k, or if
the detail each compaction loses costs about 19M CRTs, some 64 wasted requests.

### Does the model's simplification matter?

In the model, a compaction can follow any request, and the context grows
smoothly. A replay of my sessions from 7 to 15 November tests both: it keeps
every logged request and moves only the compactions. When it can compact after
any request, the replay's best point is 260k, against the model's 261k, so
uneven growth, cache lapses and sessions ending move it little. Compacting only
at turn ends, as sparkjoy does, moves the best point earlier. Turns run a median
of 9 requests but a mean of 38, so one turn can carry the context far past the
best point before the next chance to compact.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/model-threshold-dark.svg">
  <img alt="Best compaction point against β from 0 to 1, with 90% bands. The steady-state point, compacting at any request (grey), rises from about 260k at β = 0 to 310k at 0.3, crosses 400k at 0.55 and reaches 965k, where Claude Code compacts, from 0.85. The replay's best turn-end threshold (black) stays lower: 200k at β = 0, 240k to 250k from 0.1 to 0.45, 290k at 0.6 and 530k at 0.85, then 850k to 960k. A label marks 250k, where sparkjoy asks from." src="assets/model-threshold-light.svg">
</picture>

The model's best point against β when compacting after any request, and the
replay's when compacting only at turn ends, with 90% bands from resampling
sessions. Up to β = 0.6, the best turn-end threshold stays between 200k and
290k.

| β   | best point | best at turn ends | saved there | from 250k, sparkjoy's floor | from 400k |
|-----|------------|-------------------|-------------|-----------------------------|-----------|
| 0   | 261k       | 200k              | 30.0%       | 29.8%                       | 25.0%     |
| 0.3 | 307k       | 250k              | 20.1%       | 20.1%                       | 17.2%     |
| 0.6 | 444k       | 290k              | 8.5%        | 8.3%                        | 7.9%      |

For any β up to 0.6, asking from 250k saves at most 0.2 points less than the
best turn-end threshold, while asking from 400k saves 5 points less at β = 0.
These savings are below the model's steady-state 45% because, without
compaction, real sessions end before their context reaches 965k. Every figure
assumes a check that always says to compact; sparkjoy's question decides which
turn ends past the floor to use.

### Idle sessions

When a session sits idle until its cache lapses, the next request makes a cache
write of the whole context, compacted or not. Keeping costs that request
P + w(x − P). Compacting first, while the cache still holds the context, costs
the compaction request, x + oN, and then P + w(K − P). Compacting is cheaper
when

```math
x > \frac{o\,N + w\,K}{w - 1} \approx 160\text{k}
```

counting the next request alone; the cheaper requests after it only add to the
saving. sparkjoy asks once a session has sat 55 minutes since its last reply
without a new message. The replay runs that timer from each reply to the next
message, and takes a session's last reply to be followed by such a wait.
Replayed at β = 0, with every policy compacting wherever it allows:

| policy                                                | saved |
| ----------------------------------------------------- | ----- |
| turn ends from 400k                                   | 25.0% |
| turn ends from 400k and idle sessions from 200k       | 28.4% |
| turn ends from 200k, the best without idle compaction | 30.0% |
| turn ends from 250k and idle sessions from 175k       | 30.7% |

The last pair, sparkjoy's floors, is the best on a grid of turn-end thresholds
in steps of 50k and idle thresholds in steps of 25k; every pair of 200k–250k at
turn ends and 150k–175k idle comes within 0.3 points of it. 18% of turn ends
are followed by an hour's gap, so idle compaction does much of what a lower
turn-end threshold would.

### Value of information

Everything above sets one compaction threshold for all work, as if nothing were
known about how much the next work needs its context. If a check could tell
kinds of work apart, each kind could compact at its own best point. The expected
value of that information is the cost it saves against the best single
threshold, as a share of the whole cost, output tokens included, and a perfect
check's is the expected value of perfect information. It is zero when every kind
has the same best point, and it grows as their best points spread apart and as
cost rises more steeply away from them. sparkjoy's question, asked at each turn
end, is such a check.

Kinds of work can differ first in how much a larger context helps them, while
the sections above give all the work one β. What a check saves then depends on
how β varies across the work, and on how much the check's verdict tells about
it. The chart splits the work in two: a share whose context helps it, and the
rest, at β = 0, which gains nothing from its context. Each mix keeps the mean β
at 0.6: 60% of the work at β = 1, where the context helps modestly; 30% at β =
2, where it helps a lot; or 12% at β = 5, work that stalls without its history.
When all the work has the same β, a check has nothing to sort and saves nothing.

The more β varies, the later the best single threshold: 440k when all the work
has β = 0.6, 790k with 60% of it at β = 1, and never compacting with 30% at β =
2 or 12% at β = 5. At 440k, β = 1 work costs 3.4 times as much per unit of
effectiveness as β = 0 work, so the work with high β makes up more of the cost
than of the effectiveness, and the single threshold moves toward its best point.
A check splits the work into a group to keep and a group to compact, each
compacting at its own best point. Its accuracy is the share of the work it puts
in the right group, the same share in each group, and at 50% it sorts no better
than a coin flip. The chart measures a check instead by the mutual information
between its verdict, keep or compact, and whether the context helps the work, as
a share of a perfect check's, which spreads out the last points of accuracy: a
check right 9 times in 10 carries about half a perfect check's information, and
one right 99 times in 100 about 90%.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/model-voi-dark.svg">
  <img alt="The expected value of a check's information, the cost it saves against the best single threshold, by the mutual information between the check's verdict and whether the context helps the work, as a share of a perfect check's, for 60% of the work at β = 1, 30% at β = 2 and 12% at β = 5, the rest at β = 0. A perfect check saves 11%, 20% and 24%. The β = 1 line climbs fastest at first, the β = 2 line is nearly straight, and the β = 5 line stays at 0 until about 30% of the information and reaches half its saving only at 90%. A thin band along 0 marks what the checks cost, 0.3%." src="assets/model-voi-light.svg">
</picture>

What a check saves in the model on three mixes of work with the same mean β, 0.6, by
how much its verdict tells about whether the context helps the work. Each mix is
a share of the work at one β and the rest at β = 0. The band along 0 is what the
checks themselves cost.

A perfect check saves 11% with 60% of the work at β = 1, 20% with 30% at β = 2,
and 24% with 12% at β = 5. The lines bend further as β rises: half the saving
takes 39% of the information at β = 1, 63% at β = 2 and 90% at β = 5. The costly
error is sending work that needs its context to be compacted, and each unit of β
makes it about 3.5 times dearer: compacted at 261k, a unit of work costs 1.5
times what it does never compacting at β = 1, 4.8 times at β = 2 and 208 times
at β = 5. So the group the check says to compact has to be purer before
compacting it pays, and until it is the check saves nothing, up to 29% of the
information at β = 5. Right 9 times in 10, a check saves 7%, 7% and 1%. The
checks themselves cost about 0.3% in the replay below, so a check pays for
itself once it is right 57%, 71% and 87% of the time. The most a perfect check
can save rises with how far β reaches: 15% on any mix of β = 0 and 1, 31% of 0
and 2, 40% of 0 and 5. On no mix can it save more than 45%, what β = 0 work
saves at its best point against never compacting, since never compacting is
always one choice of single threshold. At a given mean, the saving is largest
when a small share of the work has β around 5 to 10, as in the 12% at β = 5. A
rarer and steeper share saves less, 17% with 2% at β = 30, because a unit of its
effectiveness costs 77 times one at β = 0 even when never compacting, and no
check lowers that part of the cost.

I haven't yet measured how accurate sparkjoy's question is at this. Grading each
of its verdicts by what happened next, such as whether the turns after a
compaction re-read files it dropped, would measure it.

Kinds of work can also differ in how much detail a compaction loses. Right after
a piece of work lands, the next turns need little of what the context holds; in
the middle of one, they need much of it. To price telling these apart, I
replayed my sessions with 30% of turn ends marked mid-task, where a compaction
loses detail, and the rest finished, where it loses none. A check that is right
at a share a of turn ends then compacts at a turn end it calls finished once the
context passes a floor, and at any turn end once it passes a higher ceiling. For
each a, the replay picks the floor and ceiling that cost least.

In the replay, at the detail measured so far, 1.8M CRTs lost per compaction on
average, or about 6 wasted requests, a perfect check saves 0.8% against the best
threshold without one, and its checks cost 0.3%. A check that called turn ends
as sparkjoy's question did on the evals, compacting at every finished one and
keeping in 3 of 8 mid-task runs, would save 0.1%, less than its checks cost. The
evals are two runs of each of a handful of cases, not yet compared with live
verdicts. A perfect check would save 3.1% if compactions lost 5.6M CRTs, the top
of the measurement's 90% interval, and 12% at 18.6M, the loss that would on its
own move the best point to 400k. No check can save more than the lost detail
costs in all, which at 1.8M CRTs per compaction is 2% of the cost under the best
threshold, since real sessions end and so compact less often than the steady
state does.

The modelled check doesn't see the context's size, so the replay needs the
ceiling: without it, a session waiting for a finished turn end could carry a
large context for many turns. sparkjoy's question does see the size, since it
states what keeping the context costs, so sparkjoy can compact mid-task once
that cost exceeds the detail a compaction would lose. On the evals, its response
to size is still noisy.

Telling the work apart is therefore worth a check in two cases: work whose β
varies widely, with a check that is right most of the time, and compactions that
each lose several times the 1.8M CRTs of detail measured so far. I haven't
measured how much β varies across my work, so the first case is open. The second
doesn't appear in what I have measured, but the count of wasted requests misses
some losses, such as a design's rejected options or what the user likes, and a
measure of those could change this result.

### Questions about the model

<details>
<summary>What would put the best point near 700k?</summary>

In any model where 700k is best, compacting there barely beats never compacting.
A request at 700k costs about 1M CRTs, against about 300k at 150k, so a
compaction there recovers its 5.6M CRTs within 9 requests, a median turn.
Waiting longer pays only if the requests after a compaction get much less done.
A request's effectiveness must then still be climbing steeply at 700k and be low
at 150k, and each compaction costs nearly as much effectiveness as it saves in
tokens:

| what puts the best point at 700k | effectiveness right after a compaction | effectiveness per CRT against never compacting |
|----|----|----|
| β = 0.75 (best point 690k) | 23% of 1M's | +1.1% |
| β = 0, and each compaction loses 106M CRTs of detail, 365 requests' worth | 100% | +5.3% |

My sessions look like neither case. Requests at 150k would get about 30% as much
done as those at 700k, yet requests below 200k go wrong no more often than those
at 400–600k. A late compaction can still be right when the next turns need
detail a summary would drop, which sparkjoy's question judges at each turn end.

</details>

<details>
<summary>What does the model assume a compaction loses?</summary>

The best point of 260k charges a compaction only its tokens, as if the summary
kept everything the next turns need. A summary that drops detail costs more: a
mistake, a correction from me, a step run again. The model charges that as D per
compaction. In my sessions D is about 1.8M CRTs (−0.7M to +5.6M), about 6 wasted
requests, after my own `/compact`s and Claude Code's ([Calibration against my
sessions](#calibration-against-my-sessions)). The larger D, the later the best
point and the more a check that tells turn ends apart is worth:

| D per compaction | best point, β = 0 | best point, β = 0.6 | a perfect check saves, β = 0 |
|----|----|----|----|
| 0, a summary that keeps everything needed | 260k | 440k | 0 |
| 1.8M CRTs, as measured | 280k | 480k | 0.8% |
| 5.6M, the top of its interval | 315k | 550k | 3.1% |
| 18.6M, about 64 wasted requests | 400k | 720k | 12% |

The measure counts only losses that show within two turns as a failed tool call,
a repeated lookup or a correction. It misses a design's rejected options, what I
like, harm that comes later, and my own time, so the true D could be larger.

</details>

<details>
<summary>Why a power law?</summary>

It has one parameter with a plain meaning: each doubling of the context makes a
request $`2^\beta`$ times as effective, wherever it starts. It can't hold at small
contexts, since at β = 0.1 a request with 1k tokens would get half as much done
as one at 1M. The cost uses only the curve from about 150k to 965k, under three
doublings. Over that range a saturating form could serve as well, in which a
request gets almost nothing done with almost no context and its effectiveness
levels off once the context holds what the work needs. One question for someone
who runs agents tells the two apart: does doubling an agent's context from 400k
to 800k improve its next steps about as much as doubling it from 100k to 200k,
or much less? The power law predicts about as much.

</details>

<details>
<summary>Does more context have diminishing returns?</summary>

Per token, yes, with β below 1: a token at 400k adds less than one at 100k, half
as much at β = 0.5 and 29% as much at β = 0.1. Per doubling, no, since each
doubling makes a request $`2^\beta`$ times as effective at any size. Every token is
worth the same only at β = 1.

</details>

<details>
<summary>Has anyone modelled this before?</summary>

No one has published a model of when to compact. This autumn's studies sweep the
threshold on benchmarks ([Satish et al.](https://arxiv.org/abs/2609.32961),
[CliffCompaction](https://arxiv.org/abs/2609.26779)) or measure what a
compaction drops ([Salle et al.](https://arxiv.org/abs/2610.31778),
[Liu](https://arxiv.org/abs/2608.16370)). The closest cost rule,
[Nadir's](https://getnadir.com/blog/mask-dont-summarize-agent-context-cache-cost/),
leaves out quality. None lets a request's effectiveness depend on its context,
prices the cache write, or limits compaction to turn ends.

</details>

### Calibration against my sessions

Effectiveness isn't logged, but what goes wrong is: a failed tool call, a lookup
repeated while its result is still in context, a turn I correct or interrupt.
Over 60 days, 993 sessions and 311k requests, requests below 200k go wrong no
more often than those at 400–600k in the same session. The difference in the
share of requests wasted is −13 points (−37 to +5), where β = 0.3 would predict
21 points more.

| fitted | value (90%) | what it implies |
|----|----|----|
| D, detail a compaction loses | −0.8M CRTs (−5.4M to +3.7M) over all my compactions; 1.8M (−0.7M to +5.6M) after my own `/compact`s and Claude Code's | 400k would need 18.6M CRTs per compaction, about 64 wasted requests |

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/model-calibration-dark.svg">
  <img alt="The share of requests wasted in five context bands, against requests at 400k to 600k, in percentage points with 90% intervals, measured in 993 of my sessions over 60 days: 0 to 200k, −13 (−37 to +5); 200k to 300k, +7 (−15 to +32); 300k to 400k, +7 (−18 to +30); 600k and over, −37 (−97 to −5). Blue curves give what an assumed β would predict, falling from the left to 0 at 400k to 600k: at the first band, +8 for β = 0.1, +21 for 0.3 and +36 for 0.6." src="assets/model-calibration-light.svg">
</picture>

The points are the share of requests wasted in each context band of 993
sessions, against the same session's requests at 400–600k, with 90% intervals
from resampling sessions. The curves are what an assumed β would predict if a
request's effectiveness fell as the share wasted rose. Past 600k the share
wasted is lower than any β predicts, though its interval is wide.

I compacted later than the best point: my own `/compact`s, 664 of them and
mostly from before sparkjoy, came at a median 413k, and sparkjoy's at 453k
because of its floor at the time. Nothing measured shows that compacting later
pays, but the evidence is weak. These signals see only visible waste, so a
request at a large context that goes right and gets more done wouldn't show.
Within a session the context grows as a piece of work goes on, so the share
wasted at each context size partly reflects the work's phase. And D was measured
where compactions were chosen, at natural breaks.

### Prices by model

The table gives a CRT's price on each model, and the other prices in CRTs, for
the one-hour cache. The figures above are for Opus 5.5. Where a model's cache
write and output cost more CRTs, a compaction costs more too, and the best point
moves later.

| model | 1M CRTs | a token's cache write, in CRTs | an output token, in CRTs | a compaction's fixed cost | best point |
|----|----|----|----|----|----|
| Opus 5.5 | \$0.20 | 40 | 100 | 4.9M CRTs, \$0.99 | about 260k |
| Sonnet 5.5 | \$0.10 | 40 | 100 | 4.9M CRTs, \$0.49 | about 260k |
| Fable 5.2 | \$0.20 | 80 | 200 | 10M CRTs, \$1.99 | about 300k |
| Haiku 5.5, above 100k | \$0.05 | 20 | 50 | 2.4M CRTs, \$0.12 | about 230k |

### Fitting it to your sessions

`/sparkjoy:report` fits the model to your sessions of the last 14 days. Its
Thresholds section gives the floors that cost least for them, and from then on
the plugin's question states the parameters it measured in place of mine. A
fit needs some compactions in the window, to measure K and N.

My own figures and charts come from `model/fit.py` and `model/calibrate.py`:

```
# The figures and charts above, from my sessions of 7 to 15 November
uv run --script model/fit.py --since 2026-11-07T12:00Z --until 2026-11-16T00:00Z \
  --charts assets

# The same at other prices and cache lifetimes
uv run --script model/fit.py --since 2026-11-07T12:00Z --until 2026-11-16T00:00Z \
  --model claude-fable-5-2 --ttl 5m

# The calibration, D and its chart, over 60 days of sessions
uv run --script model/calibrate.py --since 2026-09-17T00:00Z --until 2026-11-16T00:00Z \
  --costs-since 2026-11-07T12:00Z --costs-until 2026-11-16T00:00Z --charts assets
```

They read the main thread of each interactive session in `~/.claude/projects`,
opening only transcripts modified since `--since`, at low priority, and
resample sessions for the intervals.

### Notes

1. X\*, the best compaction point: 262k from the closed form under [Best compaction point](#best-compaction-point), and 253–271k, its 90% interval from resampling sessions.
2. K, the context after a compaction: 142k tokens (90% interval 137–147k), the first request's context, 122k, plus 20k of files the next requests read again.
3. F₀, a compaction's fixed cost: 4.93M CRTs, the compaction request's 5.6k output tokens and the cache write of the context after it.
4. g, the growth of the context per request: 1,706 tokens, the mean over my requests, leaving out the first few after each compaction.

</details>
<!-- End of the generated copy. -->

## License

MIT
