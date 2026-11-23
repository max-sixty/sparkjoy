# Cost model

To understand what makes a good compaction policy, we can model how a policy
affects what a session costs and how much context its requests have. The model
finds when a session should compact, and what sparkjoy saves by judging the work
ahead, against compacting every session at one set size as `/autocompact` does.

## The models

As with any model, there's a trade-off between simplicity and completeness. A
simple model gives a formula that shows which parameters matter, and a fuller
one follows real sessions more closely. There are a few models, each in a
section below, and each adds something the one before it leaves out:

- **[Cost alone](#cost-alone).** It assumes a request gets as much done at any
  context, so only tokens count, and that the context grows by the same number
  of tokens with each request. The most efficient threshold, where a request
  costs least on average, then has a closed form.
- **[Cost and context](#cost-and-context).** It assumes a larger context gets
  more done, by a power β of its size, with one β for all work. The most
  efficient threshold is then the one where the cost per unit of work done is
  lowest.
- **[Idle sessions](#idle-sessions).** It adds sessions that go idle. Compacting
  just before the prompt cache lapses pays at a smaller context.
- **[Mid-turn or at a turn end](#mid-turn-or-at-a-turn-end).** It assumes a
  compaction also wastes D requests on the detail it drops, more in the middle
  of a turn than at its end. Asking only at turn ends avoids the larger D, but
  lets a long turn carry the context past the most efficient threshold.
- **[A classifier](#a-classifier).** It assumes β and D vary with the work
  ahead. A question that tells these kinds of work apart, as sparkjoy's aims to,
  lets each compact where it costs least, and is worth what it saves against the
  most efficient single threshold.
- **[A replay of my sessions](#a-replay-of-my-sessions).** It keeps every logged
  request and moves only the compactions, so it includes what the other models
  leave out: uneven growth, long turns, cache lapses and sessions that end.

## Results for my sessions

For my sessions on Opus 5.5[^prices], what sparkjoy's question saves against
the most efficient single threshold, if it is right 9 times in 10 ([A
classifier](#a-classifier)):

| the work ahead                                                       | in                                      | saves |
| -------------------------------------------------------------------- | --------------------------------------- | ----- |
| ¾ at β = 0.25, ¼ at β = 1.25                                         | the steady state                        | 11.6% |
| ¾ at β = 0.25, ¼ at β = 1.25                                         | a replay, in a background session       | 9.9%  |
| ¾ at β = 0.25, ¼ at β = 1.25                                         | a replay, in a session someone has open | 3.7%  |
| half of turn ends in the middle of a task, wasting 12 requests there | the steady state                        | 1.9%  |
| half of turn ends in the middle of a task, wasting 96 requests there | the steady state                        | 9.0%  |

In a session someone has open, Claude Code compacts idle contexts itself, which
leaves the question less to save. No session of mine yet measures the work ahead
or how often the question is right.

|                                                                                  | at the base case, β = 0.5         |
| -------------------------------------------------------------------------------- | --------------------------------- |
| most efficient threshold, compacting after any request                           | 376k, or 262k counting cost alone |
| points less saved asking only at turn ends than after any request, in the replay | 0.9                               |
| extra requests a mid-turn compaction must waste for turn ends to pay             | 7                                 |

These come from [Cost alone](#cost-alone), [Cost and context](#cost-and-context)
and [Mid-turn or at a turn end](#mid-turn-or-at-a-turn-end).

- A compaction costs about \$1 at any context size ([What a request and a
  compaction cost](#what-a-request-and-a-compaction-cost)).
- Just before an idle session's cache lapses, compacting pays from about 160k
  ([Idle sessions](#idle-sessions)).
- The default floors, 250k at a turn end and 175k in an idle session, cost least
  in a replay, counting cost alone ([Floors](#floors)).

## Costs

The models before the replay price a session as cycles, each a run of requests
and the compaction that ends it. This section gives what each costs, in symbols
and then in my sessions' figures.

<details>
<summary>The model's symbols</summary>

**Variables**

| symbol | what it is                                                                                  |
| ------ | ------------------------------------------------------------------------------------------- |
| x      | Context of a request, in tokens. Over each cycle between compactions, it grows from K to X. |
| X      | Context at which the session compacts, in tokens.                                           |

**Functions of the variables**

| symbol | what it is                                                                                                                                                                                                              |
| ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| c(x)   | Cost of a request with context x, in cached reads: $`c(x) = h\,x + c_0`$.                                                                                                                                               |
| F(X)   | Cost of a compaction at context X, in cached reads: $`F(X) = F_0 + X`$, since the compaction request reads the whole context.                                                                                           |
| η(x)   | Effectiveness of a request with context x, how much it gets done, relative to a request with the full 1M window: $`\eta(x) = (x / 1\text{M})^\beta`$.                                                                   |
| J(X)   | Cost per unit of effectiveness when the session compacts at X, in cached reads, a cost-effectiveness ratio. It is the cost of a cycle's requests and its compaction, divided by the sum of its requests' effectiveness. |

**Parameters**

| symbol | what it is                                                                                                                                                                                                         |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| K      | Context after a compaction, in tokens. It is the first request's context (the system prompt and tools, instructions, the summary, and recent messages and files), plus the files the next few requests read again. |
| g      | Growth of the context per request, in tokens, from tool results, files read and the model's own output.                                                                                                            |
| P      | Size of the system prompt and tools, in tokens. Claude Code keeps this part of K in a cache shared across sessions, so it stays cached through a compaction.                                                       |
| ℓ      | Share of requests that find the cache lapsed, and so make a cache write of the whole context. The cache lapses when it expires or is invalidated.                                                                  |
| N      | Output of the compaction request, in tokens: the summary's text plus its thinking.                                                                                                                                 |
| w      | Price of one token's cache write, in cached reads.                                                                                                                                                                 |
| o      | Price of one output token, in cached reads.                                                                                                                                                                        |
| β      | Growth of a request's effectiveness with its context: each doubling of the context makes a request $`2^\beta`$ times as effective.                                                                                 |
| D      | Requests a compaction wastes on the detail it dropped, such as failed tool calls, repeated lookups and corrections. Each costs as much as a request at K, so the compaction costs $`F(X) + D\,c(K)`$ in all.       |
| h      | Cost per request of one token in the context, in cached reads. It is one read, plus the chance that the cache has lapsed times the extra cost of the token's cache write: $`h = 1 + \ell\,(w - 1)`$.               |
| c₀     | Fixed cost of a request, the part that doesn't grow with its context, in cached reads. It is the request's output, its uncached input, and the cache write of its g new tokens.                                    |
| F₀     | Fixed cost of a compaction, in cached reads. It is the compaction request's output, plus the extra cost of the cache write of the K − P tokens outside the shared cache: $`F_0 = oN + (w - 1)(K - P)`$.            |

**Result**

| symbol | what it is                                                                                                                                               |
| ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| X\*(β) | Most efficient threshold, the value of X that makes J lowest at a given β, in tokens. X\*(0) counts cost alone. Where the β is clear, it is written X\*. |

</details>

The models take each cycle to start at K, right after a compaction, and to grow
by g tokens with each request until the session compacts at X. A request with
context x costs

```math
c(x) = h\,x + c_0, \qquad h = 1 + \ell\,(w - 1)
```

Each token in the context costs one cached read. In the share ℓ of requests
that find the cache lapsed, the token is a cache write instead, at w cached
reads, w − 1 more than the read. So h is what one token in the context costs a
request on average.
c₀ is the part that doesn't grow with the context: the request's output, its
uncached input, and the cache write of its g new tokens.

A compaction at X reads the whole context and outputs N tokens of summary and
thinking, at o cached reads each. The requests after it make a cache write of
the K tokens left after it, all but the P tokens of system prompt and tools,
which Claude Code keeps in a cache shared across sessions:

```math
F(X) = F_0 + X, \qquad F_0 = o\,N + (w - 1)(K - P)
```

[Parameter values](#parameter-values) gives my sessions' value for each
parameter and where it comes from.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/model-cycles-dark.svg">
  <img alt="Eight panels, two rows by four columns, in symbols without figures. Columns: compact early, compact at X*(0), compact late, and never compact, where Claude Code compacts near the window's end. Top row, the context: a sawtooth rising from K to the column's X and dropping back to K, with many short teeth when compacting early and one long tooth when never compacting. Bottom row, cost per request: the request's own cost, rising with the context, with its share of the compaction ending its cycle on top, thick when compacting early and thin when late. A line marks each column's mean cost per request, lowest at X*(0)." src="../assets/model-cycles-light.svg">
</picture>

### What a request and a compaction cost

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/model-inputs-dark.svg">
  <img alt="Two panels against the context x, from 0 to 965k, in dollars; parts named in orange are fitted to the sessions. Top, c(x), a request's cost by part, the fixed parts at the bottom: output and uncached input, and the cache write of the new tokens, about $0.028 together; above them the cache write of the context after a lapse and the cache read of the context, both growing; $0.062 in all at 142k and $0.197 at 700k. Bottom, F(x), a compaction's cost by part, about $1 at any context: the compaction request's output, $0.11; the cache write of the context after it, $0.87, in three bands by source, the summary and the rest of the context $0.17, files re-attached or read again $0.19, and skills, instructions and memory $0.51; and the cache read of the context, the only part that grows; $1.13 at 700k." src="../assets/model-inputs-light.svg">
</picture>

A compaction's cost hardly changes with the context it compacts, from \$1.01 at
142k to \$1.18 at 965k[^parts], since most of it, \$0.87, is the cache write of
the context it leaves. My skills, instruction files and memory make up \$0.51
of that write. The system prompt and tools, P, stay in a cache Claude Code
shares across sessions, so a compaction doesn't write them again.

The larger the context, the sooner a compaction pays for itself. After a
compaction at 700k, each later request reads 558k fewer tokens, and the
compaction pays for itself within 9 requests; at 400k within 17, and at 200k
within 74:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/model-payback-dark.svg">
  <img alt="Later requests until a compaction pays for itself, against the context it compacts. The curve falls steeply from above 100 just past 142k, what a compaction leaves, to 74 requests at 200k, where each later request reads 58k less, 17 at 400k, reading 258k less, and 9 at 700k, reading 558k less, then flattens toward 6 near 965k." src="../assets/model-payback-light.svg">
</picture>

<details>
<summary>Cost by kind of token</summary>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/model-bill-dark.svg">
  <img alt="One bar of the sessions' cost by kind of token: output, thinking included, 14%; cache writes 25%; cache reads 61%." src="../assets/model-bill-light.svg">
</picture>

Output is a small share because each request reads its whole context
again[^bill]. On average, a request's 746 output tokens cost as much as 75k
cached reads, and its context holds 335k tokens.

| kind of token             | share of the cost       | share of the cost from compactions                                                  |
| ------------------------- | ----------------------- | ----------------------------------------------------------------------------------- |
| output, thinking included | 14%                     | 0.4%, the summary                                                                   |
| cache writes              | 25%                     | 2.4%, the cache write of the compacted context on the request after each compaction |
| cache reads               | 61%                     | 0.35%, the compaction request's read of the context                                 |
| uncached input            | 0.01%, too thin to draw |                                                                                     |
| all                       | 100%                    | 3.2%                                                                                |

</details>

## Cost alone

Every request reads the whole context, so a session that compacts late pays to
read a large context on every request, and one that compacts early pays for more
compactions. A request's average cost over a cycle, its share of the compaction
included, is lowest where it equals the marginal cost, the cost of one more
request before compacting, as in Harris's economic order quantity. That
threshold, X\*(0), has a closed form:

```math
X^*(0) = K + \sqrt{\frac{2g\,(F_0 + K)}{h}}
```

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/model-average-dark.svg">
  <img alt="Cost per request against the compaction threshold X, in symbols without figures. The average, a request's cost with its share of the compaction included, falls steeply from just above K to its minimum at X*(0), then rises slowly to Claude Code's default near the window's end, where a hollow dot marks it. The marginal cost, a straight grey line from K, rises through the minimum, where a filled dot labelled min(cost) at X*(0) marks the crossing." src="../assets/model-average-light.svg">
</picture>

For my sessions, X\*(0) is 262k, with a 90% interval of 253–272k:

```math
X^*(0) = 142\text{k} + \sqrt{\frac{2 \cdot 1706 \cdot (4.93\text{M} +
142\text{k})}{1.22}} = 142\text{k} + 119\text{k} = 262\text{k}
```

Compacting there, a request costs \$0.091 on average, its share of the
compaction included, against \$0.164 when the session never compacts.

## Cost and context

A request's effectiveness η(x) is how much it gets done with context x. If a
larger context helps the work, compacting costs more than tokens, since the
requests after a compaction work with less context. The most efficient threshold
then minimizes J, a cycle's cost per unit of its effectiveness:

```math
J(X) = \frac{g\,F(X) + \int_K^X c(x)\,dx}{\int_K^X \eta(x)\,dx}
```

J is lowest at X\*(β), where one more request's cost, c(X) plus the g tokens it
adds to the compaction's read, per unit of what that request gets done, equals
the cycle's average:

```math
\frac{c(X^*) + g}{\eta(X^*)} = J(X^*)
```

The model takes η to be a power of the context, relative to a request with the
full 1M window, so each doubling of the context multiplies it by $`2^\beta`$:

```math
\eta(x) = \left(\frac{x}{1\text{M}}\right)^\beta
```

β sets how much the work gets from a larger context. Work that builds on its
history, such as a design that takes shape over many turns, has a larger β than
work that needs only a summary of what came before.

At β = 0, where a request gets as much done at any context, J is the average
cost under [Cost alone](#cost-alone), and the condition above gives its closed
form, X\*(0). That is an extreme simplification: it says a request at 150k gets
as much done as one at 1M. No session measures β, so this document's figures
take a base case of β = 0.5, where a request at 250k gets half as much done as
one at 1M, and [Sensitivity](#sensitivity) shows how the most efficient
threshold moves with β.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/model-effectiveness-dark.svg">
  <img alt="Effectiveness η(x), relative to a request at 1M, against the context x, for β of 0, 0.25, 0.5 and 1 on one blue ramp, the base case, β = 0.5, drawn thicker. β = 0 is flat at 100%; the others rise from 0 to 100% at 1M, β = 1 in a straight line." src="../assets/model-effectiveness-light.svg">
</picture>

η depends only on the context's size, so a request right after a compaction, at
K, counts as getting as much done as one at K before any compaction.

### Cost at each threshold

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/model-cost-dark.svg">
  <img alt="Two panels against the compaction threshold from 142k to 965k, each relative to never compacting, where Claude Code compacts at 965k. Top, a request's cost and its effectiveness, each averaged over a cycle. The cost, its share of the compaction included, is the same at any β: it falls steeply from 142k to its lowest, 55% at 262k, and rises to 100% at 965k. The effectiveness, for β of 0, 0.25, 0.5 and 1 on one blue ramp, the base case, β = 0.5, drawn thicker, is flat at 100% for β = 0, and for the others rises from 73%, 52% and 26% at 142k to 100% at 965k. The cost line drops below the lines for β of 0, 0.25 and 0.5 at 160k, 173k and 212k and stays below them to 965k, and it is above the line for β = 1 everywhere. Bottom, their ratio, cost per unit of effectiveness, for the same β. The first three fall steeply from 142k to a minimum and rise to 100% at 965k; dots mark the minima: 55.4% at 262k for β = 0, 69.4% at 297k for 0.25 and 85.5% at 376k for 0.5. β = 1 stays above 100% and falls to it at 965k." src="../assets/model-cost-light.svg">
</picture>

At β = 0.5, the most efficient threshold X\*(0.5) is 376k, with a 90% interval
of 362–392k. Compacting there costs 14.5% less per unit of work than never
compacting, and compacting anywhere from 310k to 469k comes within a point of
that. Counting cost alone, X\*(0) is 262k.

A request's mean cost over a cycle doesn't depend on β, but what it gets done
does:

| compact at                                                     | requests a cycle | mean cost per request | cost per unit of work at β = 0.5, against never compacting |
| -------------------------------------------------------------- | ---------------- | --------------------- | ---------------------------------------------------------- |
| 175k                                                           | 19               | \$0.119               | 132.0%                                                     |
| 262k, X\*(0), the most efficient threshold counting cost alone | 70               | \$0.091               | 89.6%                                                      |
| 376k, X\*(0.5), the most efficient threshold at the base case  | 137              | \$0.098               | 85.5%                                                      |
| 600k                                                           | 268              | \$0.121               | 89.5%                                                      |
| never, leaving it to Claude Code at 965k                       | 482              | \$0.164               | 100%                                                       |

### Sensitivity

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/model-sensitivity-dark.svg">
  <img alt="Horizontal bars at β = 0.5, one for each parameter measured in the sessions and one for β, from the most efficient threshold with the parameter halved (open dot) to the threshold with it doubled (filled dot), with a thick segment where the threshold lies across a measured parameter's 90% interval, and a vertical line at the most efficient threshold with every parameter as measured, 376k. β: 297k to 965k, where Claude Code compacts. K: 266k to 583k. g: 299k to 503k. ℓ: 395k to 347k. P: 389k to 346k. N: 370k to 387k." src="../assets/model-sensitivity-light.svg">
</picture>

Across their 90% intervals, K and g move the most efficient threshold most, by
18k and 15k. Each other parameter moves it by at most 7k. β moves it much
further: at 0.25 the most efficient threshold is 297k, and at 1 no compaction
short of Claude Code's own pays.

That interval covers only my sessions, and K differs more between users, since
most of it beyond the shared system prompt is the user's own instructions and
skills. A published session with few of them
([anthropics/claude-code#87602](https://github.com/anthropics/claude-code/issues/87602))
would have a K of roughly 60–70k on a 1M window, which puts the most efficient
threshold near 250k. So `/sparkjoy report` fits K and the floors to each user's
sessions.

The most efficient threshold reaches 400k if each compaction loses \$0.25 of
detail, the cost of some 4 wasted requests, or, with no detail lost, at β =
0.55.

## Idle sessions

Once the cache lapses, an idle session's next request makes a cache write of the
whole context, compacted or not. If the session keeps its context, that request
costs P + w(x − P). Compacting first, while the cache still holds the context,
costs x + oN for the compaction request and then P + w(K − P). Counting only the
next request, which understates the saving, compacting first is cheaper when

```math
x > \frac{o\,N + w\,K}{w - 1}
```

For my sessions, compacting first pays from about 160k, or from 172k on the
five-minute cache, where a cache write costs 25 cached reads.

## Mid-turn or at a turn end

Claude Code compacts after whichever request fills the window, and
`/autocompact` after whichever request reaches its threshold, so most of their
compactions fall in the middle of a turn. sparkjoy asks only at turn ends, on
the assumption that in the middle of a turn the work ahead nearly always needs
the context's detail. A long turn can then carry the context far past the most
efficient threshold before it ends. `/autocompact` set above sparkjoy's floor
would compact those long turns as a backstop, but in their middle.

The model assumes a compaction wastes D requests on the detail it dropped, such
as failed tool calls, repeated lookups and corrections, each priced as a request
at K. D is largest while the work ahead continues what the context holds, as in
the middle of a turn, smaller at a turn end in the middle of a task, and close
to 0 once a piece of work is finished. Here each compaction in the middle of a
turn wastes ΔD more requests than one at a turn end.

β and D differ in which requests pay for a compaction. β's cost falls on every
request until the next compaction, and grows the more a compaction shrinks the
context. D's falls on the few requests right after a compaction, whatever the
context. So D can differ between two moments in one piece of work, such as the
middle of a turn and the turn end a few requests later, while β is the same at
both.

In the [replay](#a-replay-of-my-sessions) at the base case, β = 0.5, with each
policy at its most efficient thresholds and also compacting idle sessions from
175k:

| policy                                          | thresholds          | saves against never compacting, at ΔD = 0 | compactions mid-turn | costs as much as compacting after any request once ΔD reaches |
| ----------------------------------------------- | ------------------- | ----------------------------------------- | -------------------- | ------------------------------------------------------------- |
| after any request                               | 370k                | 14.9%                                     | 169                  |                                                               |
| only at turn ends                               | 390k                | 14.0%                                     | 1                    | 7                                                             |
| at turn ends, with a backstop after any request | 400k, backstop 450k | 15.3%                                     | 79                   | never: it costs less at any ΔD                                |

No session of mine measures ΔD: only 17 of their 1,399 compactions came in the
middle of a turn, too few to measure what one costs.

## A classifier

The sections above take β and D to be the same for all work, so one threshold
suits all of it. Here they vary with the work. The model assumes some work leans
on its history more than other work, a difference in β, and that a compaction at
a turn end in the middle of a task drops more of what the next turns need than
one at a turn end that finishes a piece of work, a difference in D.

A classifier that tells these apart lets each compact where it costs least.
sparkjoy's question is one: at each turn end above its floor, it reads the
context and says whether to keep it or compact it. The expected value of a
classifier's information is the cost it saves against the most efficient single
threshold, as a share of the whole cost, output included. For a classifier that
is always right, this is the expected value of perfect information.

The model takes as inputs how widely work differs and how often the classifier
is right. No session of mine measures them yet, so the sections below give what
a classifier saves across a range of each ([What the model needs
measured](#what-the-model-needs-measured)).

### Work that differs in β

β belongs to the work between two compactions: it sets how much the requests in
between get done. So the classifier sorts the work into two groups, by whether
it judges that the context helps the work, and each group compacts at its own
most efficient threshold. With $`s_k`$ the share of the work of kind k, $`q_k`$
the share of it the classifier says to compact, and $`J_k`$ the cost per unit of
work of kind k at its own β, from [Cost and context](#cost-and-context), the
classifier's cost per unit of work is

```math
J_\text{classifier} = \min_{X_1} \sum_k q_k\,s_k\,J_k(X_1) + \min_{X_0} \sum_k (1 - q_k)\,s_k\,J_k(X_0)
```

against $`J_\text{single} = \min_X \sum_k s_k\,J_k(X)`$, and it saves
$`1 - J_\text{classifier} / J_\text{single}`$. Below, the classifier is right
the same share of the time in each group. The chart measures its accuracy by its
information: the mutual information between its verdict and whether the context
helps the work, as a share of a perfect classifier's. A classifier right 9 times
in 10 has about half a perfect classifier's information, and one right 99 times
in 100 has about 90%.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/model-voi-dark.svg">
  <img alt="What a classifier saves against the most efficient single threshold, by its accuracy as a share of a perfect classifier's information, for 50% of the work at β = 1, 25% at β = 2 and 10% at β = 5, the rest at β = 0. A perfect classifier saves 13.5%, 23% and 27%. The β = 1 line climbs fastest at first, the β = 2 line is nearly straight, and the β = 5 line stays near 0 until about 30% of the information and reaches half its saving only at about 90%. A thin band along 0 marks what running the classifier costs, 0.4%." src="../assets/model-voi-light.svg">
</picture>

Each line of the chart is a mix of work with mean β 0.5, the base case, with
one share at the β shown and the rest at β = 0. The band along 0 is the cost of
running the classifier, about 0.4%. The table adds the base case where work
differs, which also has mean β 0.5. Its three quarters at β = 0.25 stand for
work whose context loses its worth quickly but not entirely, and its quarter at
β = 1.25 for work that needs much more of its context.

| mix                                                                   | most efficient single threshold | a perfect classifier saves | right 9 times in 10, a classifier saves | a classifier pays once right |
| --------------------------------------------------------------------- | ------------------------------- | -------------------------- | --------------------------------------- | ---------------------------- |
| all at β = 0.5                                                        | 376k                            | 0                          | 0                                       |                              |
| 50% at β = 1                                                          | 648k                            | 13.5%                      | 9%                                      | 58% of the time              |
| 25% at β = 2                                                          | never compacting                | 23%                        | 10%                                     | 67%                          |
| 10% at β = 5, work that stalls without its history                    | never compacting                | 27%                        | 2%                                      | 85%                          |
| 75% at β = 0.25 and 25% at β = 1.25, the base case where work differs | 626k                            | 16.4%                      | 11.6%                                   | 57%                          |

<details>
<summary>Where a classifier compacts</summary>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/model-voi-point-dark.svg">
  <img alt="The compaction threshold of the work a classifier says to compact, from 262k to 965k, against its accuracy as a share of a perfect classifier's information, for the same three mixes. For 50% at β = 1 it starts at 648k, the most efficient single threshold, and falls to 318k at about half the information. For 25% at β = 2 it stays at 965k until 3% of the information, then falls to 456k at about half. For 10% at β = 5 it stays at 965k until 22%, and is still at 826k at 45%. All three reach 262k with a perfect classifier." src="../assets/model-voi-point-light.svg">
</picture>

The work a classifier says to keep compacts at 965k at all but the lowest
accuracies. At β = 5, a unit of work compacted at 262k costs 206 times as much
as when the session never compacts, so compacting any of that mix pays only once
a classifier has 22% of a perfect classifier's information.

</details>

| work mixed with β = 0, in any share | the most a perfect classifier saves                                                 |
| ----------------------------------- | ----------------------------------------------------------------------------------- |
| at β = 1                            | 15%                                                                                 |
| at β = 2                            | 31%                                                                                 |
| at β = 5                            | 40%                                                                                 |
| at any β                            | 45%, what β = 0 work saves at its most efficient threshold against never compacting |

At a given mean β, a perfect classifier saves most when a small share of the
work has β around 5 to 10. With 2% of the work at β = 30 it saves 17%. A unit
of that work's effectiveness costs 77 times one at β = 0 even when the session
never compacts, and no classifier lowers that part of the cost.

The [replay](#a-replay-of-my-sessions) asks the classifier at turn ends, as
sparkjoy does. Each session's turns come in stretches of one kind of work, 5
turns long on average. At each turn end the classifier names the next turn's
kind, right 9 times in 10, and the session compacts if its context has passed
that kind's threshold. It also compacts idle sessions from 175k, but keeps an
idle context below 800k when it expects β = 1.25 work next. No compaction loses
detail the work needs. Claude Code compacts idle contexts only in a session
someone has open, so `/autocompact` gets those compactions there, from 175k in
the replay, and not in a background session.

| policy, each at its most efficient thresholds                                          | saves against never compacting |
| -------------------------------------------------------------------------------------- | ------------------------------ |
| `/autocompact` in a background session, after any request from 680k                    | 0.4%                           |
| `/autocompact` in a session someone has open, where only idle compactions pay          | 6.8%                           |
| turn ends from 830k, and idle sessions                                                 | 7.2%                           |
| the classifier, at turn ends from 370k or 800k by the kind it names, and idle sessions | 10.3%                          |

| the classifier against `/autocompact`                     | saves |
| --------------------------------------------------------- | ----- |
| in a background session                                   | 9.9%  |
| in a session someone has open                             | 3.7%  |
| in a session someone has open, right every time           | 5.1%  |
| in a session someone has open, with stretches of 20 turns | 3.9%  |

In a session someone has open, Claude Code's idle compactions already save 6.8%,
which leaves the classifier well short of the steady state's 11.6%.

### Turn ends that differ in lost detail

D depends on whether the work right after a compaction continues what the
context holds, so here the classifier picks the turn end to compact at, rather
than sorting the work into groups. A share m of turn ends fall in the middle of
a task, where a compaction wastes D requests, each priced as a request at K,
\$0.062. The rest finish a piece of work, where a compaction wastes none.
Without a classifier, a compaction falls at a turn end of either kind and wastes
mD on average. A classifier right at a share $`a`$ of the turn ends of each kind
compacts only at turn ends it calls finished, so each of its compactions wastes

```math
\bar D(a) = D\,\frac{m\,(1-a)}{m\,(1-a) + (1-m)\,a}
```

and the session compacts at the threshold where J, with that cost added to each
compaction, is lowest. Waiting for a turn end the classifier calls finished
moves each compaction later. A lower floor makes up for the average wait, and J
changes little near its lowest point, so the spread around it costs little, and
the model leaves the wait out.

At the base case, with half of turn ends in the middle of a task:

| D, at a turn end in the middle of a task | most efficient single threshold | a perfect classifier saves | right 9 times in 10, a classifier saves | a classifier pays once right |
| ---------------------------------------- | ------------------------------- | -------------------------- | --------------------------------------- | ---------------------------- |
| 12 wasted requests, \$0.74               | 411k                            | 2.5%                       | 1.9%                                    | 59% of the time              |
| 24, \$1.48                               | 442k                            | 4.5%                       | 3.5%                                    | 55%                          |
| 48, \$2.95                               | 494k                            | 7.7%                       | 5.8%                                    | 53%                          |
| 96, \$5.91                               | 579k                            | 12.4%                      | 9.0%                                    | 52%                          |

At D = 12, a compaction wastes 6 requests on average, as many as my sessions'
compactions did ([Mid-turn or at a turn end](#mid-turn-or-at-a-turn-end)).
A perfect classifier saves all that the lost detail adds to the cost, since its
compactions lose none. A classifier pays from little better than chance, since
each turn end it gets right avoids D outright.

sparkjoy's question goes further than this classifier. It states what keeping
the context costs, so the session's model can compact at a turn end in the
middle of a task once keeping costs more than the detail a compaction would
drop.

### What the model needs measured

| what the model takes as input                         | in the measured sessions                                                                                                                                                                                  |
| ----------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| how widely β varies across work                       | Not measured. Continuing the same work once from a compaction and once from the whole context, and comparing each pair, would measure it.                                                                 |
| the share of turn ends in the middle of a task, and D | My sessions' compactions cost about 6 wasted requests on average, but nothing separates turn ends in the middle of a task from those that finish a piece of work. The same pairs would measure D at each. |
| how often the classifier is right                     | Not measured. Grading sparkjoy's verdicts by what happened next, such as whether the turns after a compaction re-read files it dropped, would measure it.                                                 |

## A replay of my sessions

The replay runs my sessions again under each compaction policy. It keeps every
logged request and moves only the compactions.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/model-threshold-dark.svg">
  <img alt="Most efficient threshold against β from 0 to 1, with 90% bands. The steady-state model's, compacting after any request (grey), rises from 262k at β = 0 to 376k at 0.5, crosses 400k at 0.55 and reaches 965k, where Claude Code compacts, from 0.85. The replay's most efficient turn-end threshold (black) stays lower: 200k at β = 0, 240k to 250k from 0.1 to 0.45, 280k at 0.5, 290k at 0.6 and 530k at 0.85, then 850k to 960k. A vertical line marks the base case, β = 0.5, and a horizontal one 250k, sparkjoy's default floor." src="../assets/model-threshold-light.svg">
</picture>

When the replay may compact after any request, its most efficient threshold at
the base case is 350k, against 376k under [Cost and context](#cost-and-context),
so uneven growth, cache lapses and sessions ending move it 26k lower. Its most
efficient turn-end threshold is lower still, 280k, since turns run a median of 9
requests but a mean of 38, and one long turn can carry the context far past the
most efficient threshold before the next turn end. For β from 0.1 to 0.6 the
turn-end threshold is 240k–290k, and at β = 0 it is 200k. Each assumes a
compaction loses no detail the work needs.

Compacting at turn ends from 280k saves 12.5% against never compacting, and so
does compacting from 250k, sparkjoy's default floor. From 400k it saves 11.2%.
These savings are below the 14.5% that compacting at 376k saves under [Cost and
context](#cost-and-context), because real sessions without compaction end before
their context reaches 965k. Each saving assumes a classifier that always says to
compact. sparkjoy's question instead decides at which turn ends past the floor
to compact.

### Floors

| policy                                                                               | saved |
| ------------------------------------------------------------------------------------ | ----- |
| idle sessions from 200k, as Claude Code compacts on its own in a session that's open | 8.5%  |
| turn ends from 400k                                                                  | 11.2% |
| turn ends from 400k and idle sessions from 200k                                      | 13.8% |
| turn ends from 280k, the most efficient threshold alone                              | 12.5% |
| turn ends from 250k and idle sessions from 175k, sparkjoy's default floors           | 13.1% |
| turn ends from 400k and idle sessions from 175k, the most efficient pair of floors   | 13.9% |

Compacting idle sessions gets much of the saving of a lower turn-end threshold,
since 18% of turn ends are followed by an hour's gap. At the base case, the
default floors come 0.8 points short of the most efficient pair, and with idle
sessions from 175k, every turn-end floor from 250k to 400k comes within a point
of it. sparkjoy fits its floors by cost alone. A floor is where sparkjoy starts
asking, and its question decides at which turn ends above the floor to compact,
so the floor sits at the most efficient threshold that cost alone gives, the
lowest any β gives. For my sessions that is the default pair. Claude Code's own
idle compaction, from 200k in a session someone has open, saves 8.5% alone, so
in such a session the default floors save 5.0% against Claude Code as it runs.

On the five-minute cache, priced at its own rates, the cache lapses after 52%
of turn ends, and 95% of pauses past 4 minutes run past 5. There, compacting at
turn ends from 250k saves 19.4%, and also asking after 4 minutes idle from 175k
saves 20.4%.

## Parameter values

<details>
<summary>My sessions' value for each parameter, and its source</summary>

| parameter                                       | value                      | source                                                                                                                                                                  |
| ----------------------------------------------- | -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| K, context after a compaction                   | 142k                       | measured[^sessions], 90% interval 137–147k: the first request after a compaction holds 122k, and the next few requests read 20k of files again                          |
| g, growth of the context per request            | 1,706                      | measured, 1,615–1,805: the mean over the sessions' requests except the first few after a compaction, since K counts the files those read again                          |
| P, system prompt and tools                      | 30.4k                      | measured, 30.4–31.0k                                                                                                                                                    |
| ℓ, share of requests that find the cache lapsed | 0.56%                      | measured, 0.51–0.63%: the one-hour cache expires after an hour idle                                                                                                     |
| N, output of the compaction request             | 5.6k                       | measured, 5.2–5.9k: the summary's text is a median 3.5k tokens, and requests with about 3.5k tokens of visible output produce 1.61 times that in all, thinking included |
| w, price of a token's cache write               | 40 cached reads            | list price, for the one-hour cache; a write to the five-minute cache costs 25                                                                                           |
| o, price of an output token                     | 100 cached reads           | list price                                                                                                                                                              |
| β, growth of effectiveness with the context     | 0.5                        | assumed, since no session measures it: a request at 250k gets half as much done as one at 1M                                                                            |
| h, cost per request of a token in the context   | 1.22                       | derived                                                                                                                                                                 |
| c₀, fixed cost of a request                     | 134k cached reads, \$0.027 | derived                                                                                                                                                                 |
| F₀, fixed cost of a compaction                  | 4.93M cached reads, \$0.99 | derived                                                                                                                                                                 |

</details>

## Prices by model

<details>
<summary>Each model's prices, its fixed cost of a compaction, and its most efficient threshold</summary>

| model                 | a cached read, per 1M tokens | a cache write, in cached reads | an output token, in cached reads | a compaction's fixed cost | most efficient threshold, X\*(0.5) |
| --------------------- | ---------------------------- | ------------------------------ | -------------------------------- | ------------------------- | ---------------------------------- |
| Opus 5.5              | \$0.20                       | 40                             | 100                              | \$0.99                    | 376k                               |
| Sonnet 5.5            | \$0.10                       | 40                             | 100                              | \$0.49                    | 376k                               |
| Fable 5.2             | \$0.20                       | 80                             | 200                              | \$1.99                    | 496k                               |
| Haiku 5.5, above 100k | \$0.05                       | 20                             | 50                               | \$0.12                    | 297k                               |

Prices for the one-hour cache. Every other section's figures are for Opus 5.5.

</details>

## Fitting it to your sessions

`/sparkjoy report` fits the model to your sessions of the last 14 days, and its
Thresholds section gives the pair of floors that minimizes their cost. From then
on, sparkjoy asks once the context reaches those floors, unless you set floors
of your own, and its question states the parameters the report measured in
place of mine. The fit needs some compactions in those sessions, to measure K
and N.

## Questions about the model

<details>
<summary>Has anyone modelled this before?</summary>

The closest cost model, [Cheng's](https://doi.org/10.5281/zenodo.21236704) from
July 2026, treats when to restart a prompt-cached session as an economic order
quantity and as ski rental, and prices the rebuild at the cache-write rate. It
and the closest cost rule,
[Nadir's](https://getnadir.com/blog/mask-dont-summarize-agent-context-cache-cost/),
leave out quality. The closest studies, from August to October 2026, sweep the
threshold on benchmarks ([Satish et al.](https://arxiv.org/abs/2609.32961),
[Nguyen et al.](https://arxiv.org/abs/2609.26779)) or measure what a compaction
drops ([Salle et al.](https://arxiv.org/abs/2610.31778),
[Liu](https://arxiv.org/abs/2608.16370)). Salle et al. also find accuracy
similar across thresholds from 160k to 380k. None lets a request's effectiveness
depend on its context or limits compaction to turn ends.

</details>

<details>
<summary>Why a power law?</summary>

It has one parameter with a plain meaning: each doubling of the context makes a
request $`2^\beta`$ times as effective, wherever it starts. The cost uses only
the curve from about 150k to 965k, under three doublings. Over that range a
saturating form, levelling off once the context holds what the work needs, could
serve as well. The two differ on one question: does doubling an agent's context
from 400k to 800k improve its next steps about as much as doubling it from 100k
to 200k? Under the power law, both doublings improve them by the same factor.
Under a saturating form, the later doubling improves them less. A logarithm, η =
ln(x / 45k), gives a request at 150k as much against one at 1M as β = 0.5 does,
and puts the most efficient threshold at 398k, saving 16.8% against never
compacting, where the power law puts it at 376k, saving 14.5%.

</details>

<details>
<summary>Does the power law give context diminishing returns?</summary>

Yes, with β below 1: a token at 400k adds less than one at 100k, half as much at
β = 0.5 and 29% as much at β = 0.1. Every token is worth the same only at β = 1.

</details>

<details>
<summary>What does the model assume a compaction loses?</summary>

The model makes a request's effectiveness depend only on its context's size. A
request right after a compaction, whose 142k tokens hold a summary of the work
so far, counts as getting as much done as a request at 142k before the session's
first compaction.

At β above 0, a summary can do better than that. If it keeps everything the work
needs, the requests after the compaction get as much done as with the whole
transcript. The model counts them as getting less, so compacting pays more than
the model says. A summary that drops detail the next turns need costs more, in a
mistake, a correction from the user or a step run again. The model adds that
cost as D wasted requests per compaction, and the most efficient threshold above
is for D = 0.

| D per compaction                          | most efficient threshold |
| ----------------------------------------- | ------------------------ |
| 0, a summary that keeps everything needed | 376k                     |
| 4 wasted requests, \$0.25                 | 400k                     |
| 16, \$1                                   | 461k                     |
| 65, \$4                                   | 629k                     |

</details>

<details>
<summary>What would put the most efficient threshold near 700k?</summary>

A request at 700k costs about \$0.20, against about \$0.06 at 150k, so a
compaction there pays for its \$1.13 within 9 requests, the length of a median
turn. The most efficient threshold reaches 700k only if the requests after a
compaction get much less done, and even then compacting at 700k barely beats
never compacting:

| what puts the most efficient threshold at 700k                          | effectiveness right after a compaction, against 1M | effectiveness per dollar against never compacting |
| ----------------------------------------------------------------------- | -------------------------------------------------- | ------------------------------------------------- |
| β = 0.76, with no detail lost (most efficient threshold 721k)           | 23%                                                | +0.8%                                             |
| β = 0.5, and each compaction loses \$5.68 of detail, 98 requests' worth | 38%                                                | +2.7%                                             |

</details>

[^prices]:
    Dollars are at Opus 5.5's prices for the one-hour cache. The formulas
    count costs in cached reads, the price of reading one token from the prompt
    cache, \$0.20 per million tokens on Opus 5.5. [Prices by
    model](#prices-by-model) gives other models' prices.

[^sessions]:
    The measured parameters come from my sessions of 7 to 15 November,
    with 90% intervals from resampling sessions.

[^parts]:
    Every part except the read of the context is measured from 36,249
    requests of my sessions. The cache write after a compaction is split by
    the characters each source put into the first request after each of the
    135 compactions and into the requests after it, at characters per token
    fitted to their logged usage (`model/context.py`).

[^bill]:
    Each of the sessions' 36,000 requests is priced from its logged usage
    at Opus 5.5. A compaction's own request isn't logged, so its cost comes
    from the model.
