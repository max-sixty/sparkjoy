# Cost model

To understand what makes a good compaction policy, we can model how a policy
affects what a session costs and how much context its requests have. The model
finds when a session should compact, and what sparkjoy saves against Claude
Code's default.

## The models

As with any model, there's a trade-off between simplicity and completeness. A
simple model gives a formula that shows which parameters matter, and a fuller
one follows real sessions more closely. There are a few models, each in a
section below, and each adds something the one before it leaves out:

- **[Cost alone](#cost-alone).** It assumes a request gets as much done at any
  context, so only tokens count, and that the context grows by the same number
  of tokens with each request. The most efficient threshold, where a request
  costs least on average, then has a closed form.
- **[Cost and context](#cost-and-context).** It assumes a request gets more done
  with a larger context, in proportion to a power β of the context's size, with
  one β for all work. The most efficient threshold is then the one where the
  cost per unit of work done is lowest.
- **[Idle sessions](#idle-sessions).** It adds sessions that go idle. An idle
  session is worth compacting just before its prompt cache lapses, at a smaller
  context than a session in use.
- **[Mid-turn or at a turn end](#mid-turn-or-at-a-turn-end).** It assumes a
  compaction also wastes D requests on the detail it drops, more in the middle
  of a turn than at its end. Asking only at turn ends avoids the larger D, but
  during a long turn the context can grow past the most efficient threshold.
- **[A classifier](#a-classifier).** It assumes β and D vary with the work
  ahead. A classifier that tells work with a high β or D from work with a low
  one, as sparkjoy's question aims to, lets the session compact each kind of
  work where it costs least.
- **[A replay of my sessions](#a-replay-of-my-sessions).** It keeps every logged
  request and moves only the compactions, so it includes what the other models
  leave out: uneven growth, long turns, cache lapses and sessions that end.

## Results for my sessions

What sparkjoy saves against Claude Code's default, in my sessions of 18 to 22
November on Opus 5.5[^sessions]:

<!-- What sparkjoy saves -->

| policy                                                         | how much a request gets done with a smaller context                       | without Claude Code's idle compaction[^idle] | with it |
| -------------------------------------------------------------- | ------------------------------------------------------------------------- | -------------------------------------------- | ------- |
| sparkjoy as it ran                                             | as much as with a larger one                                              | 31%                                          | 24%     |
| compacting at turn ends from 250k and idle sessions from 175k  | the base case: half as much at 250k as at 1M                              | 11%                                          | 6%      |
| compacting at turn ends from 250k and idle sessions from 175k  | varies: a quarter of the work needs much more context, the rest much less | 0%                                           | −5%     |
| compacting where sparkjoy's judgment says, right 9 times in 10 | varies: a quarter of the work needs much more context, the rest much less | 10%                                          | 5%      |

Claude Code's default compacts near 965k, and where its idle compaction runs,
also an idle context from 200k. The last three rows replay my sessions ([A
replay of my sessions](#a-replay-of-my-sessions)). The last compacts where the
judgment says, from 280k for work that needs less context and from 900k for work
that needs more, the thresholds that cost least in hindsight ([A
classifier](#a-classifier)). How much less a request gets done with a smaller
context ([Cost and context](#cost-and-context)), and how often the judgment is
right, are assumed, not yet measured. Every row counts what sparkjoy's questions
cost.

Resampling my sessions gives the first row a 90% interval of 29–32% without
Claude Code's idle compaction and 22–26% with it. A single session's saving
varies more. Of the quarter of sessions that cost the most under Claude Code's
default, eight in ten saved 22% to 43% without its idle compaction, and 17% to
39% with it.

<!-- End of what sparkjoy saves. -->

## Costs

The models before the replay price a session as cycles, each a run of requests
and the compaction that ends it. This section gives what a request and a
compaction cost, in symbols and then in my sessions' figures.

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

| symbol | what it is                                                                                                                                                                                   |
| ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| X\*(β) | Most efficient threshold, the value of X that makes J lowest at a given β, in tokens. X\*(0) is the most efficient threshold when only cost counts. Where the β is clear, it is written X\*. |

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
  <img alt="Eight panels, two rows by four columns, in symbols without figures. Columns: compact early, compact at X*(0), compact late, and Claude Code's default, which compacts near the window's end. Top row, the context: a sawtooth rising from K to the column's X and dropping back to K, with many short teeth when compacting early and one long tooth under Claude Code's default. Bottom row, cost per request: the request's own cost, rising with the context, with its share of the compaction ending its cycle on top, thick when compacting early and thin when late. A line marks each column's mean cost per request, lowest at X*(0)." src="../assets/model-cycles-light.svg">
</picture>

### What a request and a compaction cost

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/model-inputs-dark.svg">
  <img alt="Two panels against the context x, from 0 to 965k, in dollars; parts named in orange are fitted to the sessions. Top, c(x), a request's cost by part, the fixed parts at the bottom: output and uncached input, and the cache write of the new tokens, about $0.027 together; above them the cache write of the context after a lapse and the cache read of the context, both growing; $0.059 in all at 139k and $0.192 at 700k. Bottom, F(x), a compaction's cost by part, about $1 at any context: the compaction request's output, $0.10; the cache write of the context after it, $0.82, in three bands by source, the summary and the rest of the context $0.17, files re-attached or read again $0.12, and skills, instructions and memory $0.52; and the cache read of the context, the only part that grows; $1.06 at 700k." src="../assets/model-inputs-light.svg">
</picture>

A compaction's cost hardly changes with the context it compacts, from \$0.95 at
139k to \$1.12 at 965k[^parts], since most of it, \$0.82, is the cache write of
the context it leaves. My skills, instruction files and memory make up \$0.52 of
that write. The system prompt and tools, P, stay in a cache Claude Code shares
across sessions, so a compaction doesn't write them again.

The larger the context, the sooner a compaction pays for itself. After a
compaction at 700k, each later request reads 561k fewer tokens, and the
compaction pays for itself within 8 requests; at 400k within 17, and at 200k
within 67:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/model-payback-dark.svg">
  <img alt="Later requests until a compaction pays for itself, against the context it compacts. The curve falls steeply from above 100 just past 139k, what a compaction leaves, to 67 requests at 200k, where each later request reads 61k less, 17 at 400k, reading 261k less, and 8 at 700k, reading 561k less, then flattens toward 6 near 965k." src="../assets/model-payback-light.svg">
</picture>

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

For my sessions, X\*(0) is 253k, with a 90% interval of 248–257k:

```math
X^*(0) = 139\text{k} + \sqrt{\frac{2 \cdot 1627 \cdot (4.62\text{M} +
139\text{k})}{1.19}} = 139\text{k} + 114\text{k} = 253\text{k}
```

Compacting there, a request costs \$0.086 on average, its share of the
compaction included, against \$0.159 under Claude Code's default.

## Cost and context

A request's effectiveness η(x) is how much it gets done with context x. If a
request gets more done with a larger context, compacting costs more than tokens,
since the requests after a compaction work with less context. The most efficient
threshold then minimizes J, a cycle's cost per unit of its effectiveness:

```math
J(X) = \frac{g\,F(X) + \int_K^X c(x)\,dx}{\int_K^X \eta(x)\,dx}
```

J is lowest at X\*(β), where one more request costs as much per unit of what it
gets done as the cycle's average. That request's cost is c(X) plus the g tokens
it adds to the compaction's read:

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
K, counts as getting as much done as one at K before any compaction. At β above
0, the requests after a summary that keeps everything the work needs get more
done than requests at K before any compaction, so compacting pays more than the
model says. A summary that drops detail the next turns need costs more, in a
mistake, a correction from the user or a step run again, which [Mid-turn or at a
turn end](#mid-turn-or-at-a-turn-end) adds as D wasted requests.

### Cost at each threshold

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/model-cost-dark.svg">
  <img alt="Two panels against the compaction threshold from 139k to 965k, each relative to Claude Code's default, compacting at 965k. Top, a request's cost and its effectiveness, each averaged over a cycle. The cost, its share of the compaction included, is the same at any β: it falls steeply from 139k to its lowest, 54% at 253k, and rises to 100% at 965k. The effectiveness, for β of 0, 0.25, 0.5 and 1 on one blue ramp, the base case, β = 0.5, drawn thicker, is flat at 100% for β = 0, and for the others rises from 72%, 52% and 25% at 139k to 100% at 965k. The cost line drops below the lines for β of 0, 0.25 and 0.5 at 155k, 167k and 204k and stays below them to 965k, and it is above the line for β = 1 everywhere. Bottom, their ratio, cost per unit of effectiveness, for the same β. The first three fall steeply from 139k to a minimum and rise to 100% at 965k; dots mark the minima: 54.1% at 253k for β = 0, 68.3% at 287k for 0.25 and 84.7% at 363k for 0.5. β = 1 stays above 100% and falls to it at 965k." src="../assets/model-cost-light.svg">
</picture>

At β = 0.5, the most efficient threshold X\*(0.5) is 363k, with a 90% interval
of 356–370k. Compacting there costs 15.3% less per unit of work than Claude
Code's default, and compacting anywhere from 299k to 454k comes within a point
of that.

### Sensitivity

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/model-sensitivity-dark.svg">
  <img alt="Horizontal bars at β = 0.5, one for each parameter measured in the sessions and one for β, from the most efficient threshold with the parameter halved (open dot) to the threshold with it doubled (filled dot), with a thick segment where the threshold lies across a measured parameter's 90% interval, and a vertical line at the most efficient threshold with every parameter as measured, 363k. β: 287k to 965k, where Claude Code compacts. K: 254k to 566k. g: 289k to 486k. ℓ: 379k to 337k. P: 378k to 330k. N: 358k to 374k." src="../assets/model-sensitivity-light.svg">
</picture>

Across their 90% intervals, the measured parameters move the most efficient
threshold by 9k at most. β moves it much further. At β = 0.25 it is 287k, and at
β = 1 no threshold below Claude Code's default costs less per unit of work than
the default.

That interval covers only my sessions, and K differs more between users, since
most of it beyond the shared system prompt is the user's own instructions and
skills. A published session with few of them
([anthropics/claude-code#87602](https://github.com/anthropics/claude-code/issues/87602))
would have a K of roughly 60–70k on a 1M window, which puts the most efficient
threshold near 250k. So `/sparkjoy report` fits K and the floors to each user's
sessions.

## Idle sessions

Once the cache lapses, an idle session's next request makes a cache write of the
whole context, compacted or not. If the session keeps its context, that request
costs P + w(x − P). Compacting first, while the cache still holds the context,
costs x + oN for the compaction request and then P + w(K − P). Counting only the
next request, which understates the saving, compacting first is cheaper when

```math
x > \frac{o\,N + w\,K}{w - 1}
```

For my sessions, compacting first pays from about 155k, or from 166k on the
five-minute cache, where a cache write costs 25 cached reads.

## Mid-turn or at a turn end

Claude Code compacts after whichever request fills the window, and
`/autocompact` after whichever request reaches its threshold, so most of their
compactions fall in the middle of a turn. sparkjoy asks only at turn ends, on
the assumption that in the middle of a turn the work ahead nearly always needs
the context's detail. During a long turn, the context can then grow far past the
most efficient threshold before the turn ends. `/autocompact` set above
sparkjoy's floor would compact during those long turns as a backstop, but in the
middle of a turn.

The model assumes a compaction wastes D requests on the detail it dropped, such
as failed tool calls, repeated lookups and corrections, each priced as a request
at K. D is largest while the work ahead continues what the context holds, as in
the middle of a turn, smaller at a turn end in the middle of a task, and close
to 0 once a piece of work is finished. Here each compaction in the middle of a
turn wastes ΔD more requests than one at a turn end.

β and D put a compaction's cost on different requests. Through β, every request
until the next compaction gets less done, and the more a compaction shrinks the
context, the less each gets done. The D wasted requests come right after a
compaction, whatever the context. So D can differ between two moments in one
piece of work, such as the middle of a turn and the turn end a few requests
later, while β is the same at both.

The table below [replays](#a-replay-of-my-sessions) my sessions at the base
case, β = 0.5, with each policy at its most efficient thresholds and also
compacting idle sessions from 175k. Its savings are against Claude Code's
default without its idle compaction.

| policy                                          | thresholds          | saves, at ΔD = 0 | compactions mid-turn | costs as much as compacting after any request once ΔD reaches |
| ----------------------------------------------- | ------------------- | ---------------- | -------------------- | ------------------------------------------------------------- |
| after any request                               | 390k                | 14.2%            | 312                  |                                                               |
| only at turn ends                               | 290k                | 12.4%            | 0                    | 8                                                             |
| at turn ends, with a backstop after any request | 350k, backstop 400k | 14.5%            | 194                  | never: it costs less at any ΔD                                |

No session of mine measures ΔD: only 1 of their 478 compactions came in the
middle of a turn, too few to measure what one costs.

## A classifier

The sections above take β and D to be the same for all work, so one threshold
suits all of it. Here they vary with the work. The model assumes some work leans
on its history more than other work, a difference in β, and that a compaction at
a turn end in the middle of a task drops more of what the next turns need than
one at a turn end that finishes a piece of work, a difference in D.

A classifier that tells these kinds of work apart can compact each where it
costs least. sparkjoy is such a classifier. At each turn end past its 250k
floor, it asks a model to read the context and say whether to keep it or compact
it. Each question is a request that reads the context, and every saving below
counts what the questions cost. With no information, a classifier can do no
better than one threshold for all the work, so each table below gives that case
too.

The model takes as inputs how widely β and D differ across work, and how often
the classifier is right. No session of mine measures them yet, so the sections
below give what a classifier saves across a range of each ([What the model needs
measured](#what-the-model-needs-measured)).

### Work that differs in β

β sets how much the requests between two compactions get done. So the classifier
sorts the work between compactions into two groups, by whether it judges that a
larger context lets the requests get much more done or only a little more, and
the session compacts each group's work at that group's own most efficient
threshold. With $`s_k`$ the share of the work of kind k, $`q_k`$ the share of it
the classifier says to compact, and $`J_k`$ the cost per unit of work of kind k
at its own β, from [Cost and context](#cost-and-context), the classifier's cost
per unit of work is

```math
J_\text{classifier} = \min_{X_1} \sum_k q_k\,s_k\,J_k(X_1) + \min_{X_0} \sum_k (1 - q_k)\,s_k\,J_k(X_0)
```

against $`J_\text{default} = \sum_k s_k\,J_k(965\text{k})`$ under Claude Code's
default, which the most efficient single threshold, $`J_\text{single} = \min_X
\sum_k s_k\,J_k(X)`$, never exceeds. Below, the classifier is right the same
share of the time in each group. The chart measures its accuracy by its
information: the mutual information between its verdict and which β the work
has, as a share of a perfect classifier's. A classifier right 9 times in 10 has
about half a perfect classifier's information, and one right 99 times in 100 has
about 90%.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/model-voi-dark.svg">
  <img alt="What a classifier saves against Claude Code's default, after what its questions cost, by its accuracy as a share of a perfect classifier's information, for 50% of the work at β = 1, 25% at β = 2 and 10% at β = 5, the rest at β = 0. With no information the β = 1 line starts at 1.3%, what the most efficient single threshold saves less the questions, and the other two at −1.6%, the questions' cost alone, since for them that threshold is Claude Code's own. A perfect classifier saves 15%, 23% and 26%. The β = 1 line climbs fastest at first, the β = 2 line crosses 0 at 15% of the information and then rises nearly straight, and the β = 5 line stays below 0 until about 45% of the information and reaches half its saving only at about 90%." src="../assets/model-voi-light.svg">
</picture>

Each line of the chart shows a mix of work with mean β 0.5, the base case, in
which one share of the work is at the β shown and the rest at β = 0. The table
adds the base case where work differs, which also has mean β 0.5. In it, three
quarters of the work is at β = 0.25, work that gets only a little more done with
a larger context, and a quarter is at β = 1.25, work that gets much more done
with one.

| mix                                                                   | most efficient single threshold | it saves | a perfect classifier saves | right 9 times in 10, a classifier saves | a classifier saves more than the single threshold once right |
| --------------------------------------------------------------------- | ------------------------------- | -------- | -------------------------- | --------------------------------------- | ------------------------------------------------------------ |
| 50% at β = 1                                                          | 638k                            | 3.0%     | 15.1%                      | 10.6%                                   | 66% of the time                                              |
| 25% at β = 2                                                          | Claude Code's default           | 0        | 22.9%                      | 8.9%                                    | 73%                                                          |
| 10% at β = 5, work that stalls without its history                    | Claude Code's default           | 0        | 26.3%                      | 0.1%                                    | 90%                                                          |
| 75% at β = 0.25 and 25% at β = 1.25, the base case where work differs | 617k                            | 3.9%     | 19.1%                      | 14.1%                                   | 64%                                                          |

The [replay](#a-replay-of-my-sessions) asks the classifier at turn ends, as
sparkjoy does, on the base case where work differs. Each session's turns come in
stretches of one kind of work, 5 turns long on average unless a row says
otherwise. At each turn end the classifier names the next turn's kind, and the
session compacts if its context has passed that kind's threshold. Each row that
compacts where the judgment says uses the pair of thresholds that costs least
for that row. An idle session compacts from 175k, or from the higher threshold
when the classifier expects β = 1.25 work next. No compaction loses detail the
work needs.

| against Claude Code's default                                                     | thresholds for β = 0.25 and β = 1.25 work | without Claude Code's idle compaction | with it |
| --------------------------------------------------------------------------------- | ----------------------------------------- | ------------------------------------- | ------- |
| compacting at every turn end past sparkjoy's floors                               | 250k for both                             | 0.0%                                  | −4.7%   |
| compacting where its judgment says, right 9 times in 10                           | 280k and 900k                             | 9.7%                                  | 4.9%    |
| compacting where its judgment says, right every time                              | 260k and 900k                             | 12.0%                                 | 7.2%    |
| compacting where its judgment says, right 9 times in 10, in stretches of 20 turns | 260k and 930k                             | 10.7%                                 | 5.6%    |

### Turn ends that differ in lost detail

D depends on whether the work right after a compaction continues what the
context holds, so here the classifier picks the turn end to compact at, rather
than sorting the work into groups. A share m of turn ends fall in the middle of
a task, where a compaction wastes D requests, each priced as a request at K,
\$0.059. The rest finish a piece of work, where a compaction wastes none.
Without a classifier, a compaction falls at a turn end of either kind and wastes
mD on average. A classifier right at a share $`a`$ of the turn ends of each kind
compacts only at turn ends it calls finished, so each of its compactions wastes

```math
\bar D(a) = D\,\frac{m\,(1-a)}{m\,(1-a) + (1-m)\,a}
```

and the session compacts at the threshold where J, with that cost added to each
compaction, is lowest. Waiting for a turn end the classifier calls finished
moves each compaction later. A lower floor makes up for the average wait. The
wait also varies around that average, but J changes little near its lowest
point, so the variation costs little, and the model leaves the wait out.

The table below takes the base case, with half of turn ends in the middle of a
task. Its savings are against Claude Code's default, whose compactions fall
mid-turn and each waste D.

| D, at a turn end in the middle of a task | most efficient single threshold | it saves | a perfect classifier saves | right 9 times in 10, a classifier saves | a classifier saves more than the single threshold once right |
| ---------------------------------------- | ------------------------------- | -------- | -------------------------- | --------------------------------------- | ------------------------------------------------------------ |
| 12 wasted requests, \$0.70               | 397k                            | 13.9%    | 14.8%                      | 14.4%                                   | 79% of the time                                              |
| 24, \$1.41                               | 427k                            | 12.8%    | 15.6%                      | 14.6%                                   | 67%                                                          |
| 48, \$2.81                               | 478k                            | 11.3%    | 17.0%                      | 15.3%                                   | 61%                                                          |
| 96, \$5.62                               | 560k                            | 9.6%     | 19.7%                      | 16.5%                                   | 58%                                                          |

At D = 12, a compaction wastes 6 requests on average, as many as my sessions'
compactions did ([Parameter values](#parameter-values)). A perfect classifier's
compactions lose none of that detail. So the more a compaction loses, the more a
classifier saves over the single threshold, since at each turn end it judges
right the session avoids all D wasted requests, while the session pays for a
question at every turn end it asks at.

Unlike this classifier, sparkjoy's question also states what keeping the context
costs, so the session's model can compact at a turn end in the middle of a task
once keeping costs more than the detail a compaction would drop.

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
  <img alt="Most efficient threshold against β from 0 to 1, with 90% bands. The steady-state model's, compacting after any request (grey), rises from 253k at β = 0 to 363k at 0.5, crosses 400k at 0.57 and reaches 965k, where Claude Code compacts, from 0.83. The replay's most efficient turn-end threshold (black) stays lower: 210k at β = 0, 220k from 0.1 to 0.3, 270k to 280k from 0.35 to 0.5, 330k at 0.6 and 860k at 0.85 and 0.9, then 890k. A vertical line marks the base case, β = 0.5, and a horizontal one 250k, sparkjoy's default floor." src="../assets/model-threshold-light.svg">
</picture>

The table below takes the base case and compacts no idle sessions. Its savings
are against Claude Code's default without its idle compaction.

| model                                          | most efficient threshold | saves |
| ---------------------------------------------- | ------------------------ | ----- |
| Cost and context, compacting after any request | 363k                     | 15.3% |
| the replay, compacting only at turn ends       | 280k                     | 11.8% |

The replay's threshold is lower, since during a long turn the context can grow
far past the threshold before the turn ends and the session can compact. It
saves less, since real sessions often end before their context fills the window.
Both assume a compaction loses no detail the work needs, and the replay compacts
at every turn end past the threshold with no question asked; sparkjoy instead
asks at each of them, and its answer decides where to compact.

### Floors

| policy, at the base case                                                         | saved without Claude Code's idle compaction | with it |
| -------------------------------------------------------------------------------- | ------------------------------------------- | ------- |
| turn ends from 250k and idle sessions from 175k, sparkjoy's default floors       | 11.5%                                       | 6.3%    |
| turn ends from 250k and idle sessions from 150k, the floors fitted by cost alone | 11.5%                                       | 6.3%    |
| turn ends from 300k and idle sessions from 150k, the most efficient pair         | 11.9%                                       | 6.7%    |

Compacting idle sessions gets much of the saving of a lower turn-end threshold:
with idle sessions from 150k, every turn-end floor from 250k to 450k comes
within a point of the most efficient pair. Each pair of floors pays for a
question at every turn end and idle session past it.

A floor is where sparkjoy starts asking, and its question decides at which turn
ends above the floor to compact. So sparkjoy puts its floors at the most
efficient thresholds that cost alone gives, since a β above 0 only raises them.
Its default floors, 250k and 175k, come from fitting cost alone to earlier
sessions of mine, and in these sessions they save as much as the pair fitted to
these sessions by cost alone, 250k and 150k.

On the five-minute cache, where the cache lapses after half of turn ends,
compacting at turn ends from 250k saves 18.8%, and also asking after 4 minutes
idle from 175k saves 20.3%.

## Parameter values

<details>
<summary>My sessions' value for each parameter, and its source</summary>

| parameter                                       | value                      | source                                                                                                                                                                                                                                                                                                                                      |
| ----------------------------------------------- | -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| K, context after a compaction                   | 139k                       | measured[^sessions], 90% interval 135–142k: the first request after a compaction holds 128k, and the next few requests read 11k of files again                                                                                                                                                                                              |
| g, growth of the context per request            | 1,627                      | measured, 1,585–1,667: the mean over the sessions' requests except the first few after a compaction, since K counts the files those read again                                                                                                                                                                                              |
| P, system prompt and tools                      | 33.5k                      | measured, 33.3–33.5k                                                                                                                                                                                                                                                                                                                        |
| ℓ, share of requests that find the cache lapsed | 0.48%                      | measured, 0.44–0.52%: the one-hour cache expires after an hour idle                                                                                                                                                                                                                                                                         |
| N, output of the compaction request             | 5.1k                       | measured, 5.0–5.3k: the summary's text is a median 3.3k tokens, and requests with about 3.3k tokens of visible output produce 1.56 times that in all, thinking included                                                                                                                                                                     |
| w, price of a token's cache write               | 40 cached reads            | list price, for the one-hour cache; a write to the five-minute cache costs 25                                                                                                                                                                                                                                                               |
| o, price of an output token                     | 100 cached reads           | list price                                                                                                                                                                                                                                                                                                                                  |
| β, growth of effectiveness with the context     | 0.5                        | assumed, since no session measures it: a request at 250k gets half as much done as one at 1M                                                                                                                                                                                                                                                |
| D, requests a compaction wastes                 | about 6                    | measured, very noisily, over two months of my sessions to 15 November, since it needs many: after a compaction, nearly always at a turn end or in an idle session, they had about 6 more wasted requests than usual, such as failed tool calls, repeated lookups and corrections. sparkjoy's question gives the session's model this figure |
| h, cost per request of a token in the context   | 1.19                       | derived                                                                                                                                                                                                                                                                                                                                     |
| c₀, fixed cost of a request                     | 128k cached reads, \$0.026 | derived                                                                                                                                                                                                                                                                                                                                     |
| F₀, fixed cost of a compaction                  | 4.62M cached reads, \$0.92 | derived                                                                                                                                                                                                                                                                                                                                     |

</details>

## Prices by model

<details>
<summary>Each model's prices, its fixed cost of a compaction, and its most efficient threshold</summary>

| model                 | a cached read, per 1M tokens | a cache write, in cached reads | an output token, in cached reads | a compaction's fixed cost | most efficient threshold, X\*(0.5) |
| --------------------- | ---------------------------- | ------------------------------ | -------------------------------- | ------------------------- | ---------------------------------- |
| Opus 5.5              | \$0.20                       | 40                             | 100                              | \$0.92                    | 363k                               |
| Sonnet 5.5            | \$0.10                       | 40                             | 100                              | \$0.46                    | 363k                               |
| Fable 5.2             | \$0.20                       | 80                             | 200                              | \$1.87                    | 485k                               |
| Haiku 5.5, above 100k | \$0.05                       | 20                             | 50                               | \$0.11                    | 286k                               |

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
assume a request gets as much done at any context. The closest studies, from
August to October 2026, sweep the threshold on benchmarks ([Satish et
al.](https://arxiv.org/abs/2609.32961), [Nguyen et
al.](https://arxiv.org/abs/2609.26779)) or measure what a compaction drops
([Salle et al.](https://arxiv.org/abs/2610.31778),
[Liu](https://arxiv.org/abs/2608.16370)). Salle et al. also find accuracy
similar across thresholds from 160k to 380k. None lets a request's effectiveness
depend on its context or limits compaction to turn ends.

</details>

<details>
<summary>Why a power law?</summary>

It has one parameter with a plain meaning: each doubling of the context makes a
request $`2^\beta`$ times as effective, wherever it starts. J depends on η only
from about 150k to 965k, a range of under three doublings. Over that range a
saturating form, levelling off once the context holds what the work needs, could
serve as well. The two differ on one question: does doubling an agent's context
from 400k to 800k improve its next steps about as much as doubling it from 100k
to 200k? Under the power law, both doublings improve them by the same factor.
Under a saturating form, the later doubling improves them less. A logarithm, η =
ln(x / 45k), gives a request at 150k the same effectiveness, relative to one at
1M, as β = 0.5 does, and puts the most efficient threshold at 389k, saving 17.4%
against Claude Code's default, where the power law puts it at 363k, saving
15.3%.

</details>

<details>
<summary>What would put the most efficient threshold near 700k?</summary>

A request at 700k costs about \$0.19, against about \$0.06 at 150k, so a
compaction there pays for its \$1.06 within 8 requests, fewer than a median
turn's 11. The most efficient threshold reaches 700k only if the requests after
a compaction get much less done, and even then compacting at 700k barely beats
Claude Code's default:

| what puts the most efficient threshold at 700k                           | effectiveness right after a compaction, against 1M | effectiveness per dollar against Claude Code's default |
| ------------------------------------------------------------------------ | -------------------------------------------------- | ------------------------------------------------------ |
| β = 0.76, with no detail lost                                            | 22%                                                | +0.9%                                                  |
| β = 0.5, and each compaction loses \$6.00 of detail, 102 requests' worth | 37%                                                | +2.8%                                                  |

</details>

[^sessions]:
    My 163 sessions of 18 to 22 November 2026, all on the one-hour cache.
    Every figure here comes from them except D's, and the measured parameters
    have 90% intervals from resampling sessions. Every cost is at Opus 5.5's
    list prices for the one-hour cache, unless a figure names the five-minute
    cache. The formulas count costs in cached reads, the price of reading one
    token from the prompt cache, \$0.20 per million tokens on Opus 5.5
    ([Prices by model](#prices-by-model) gives other models'). Replayed as
    sparkjoy ran, the sessions cost within 3% of what their transcripts log.
    On a subscription, the same tokens count against usage limits rather than
    a bill.

[^idle]:
    Claude Code's idle compaction compacts a conversation of 200k tokens or more
    just before its one-hour prompt cache expires. It runs only in an
    interactive session in a terminal, in VS Code's chat panel or in the
    desktop app's Code tab, and only while
    [`idleCompaction`](https://code.claude.com/docs/en/settings-reference#idlecompaction)
    is on, as it is by default. It doesn't run in a session started with
    `claude --bg` or from agent view (`claude agents`)
    ([#103866](https://github.com/anthropics/claude-code/issues/103866)). 144 of
    my 163 sessions ran in agent view.

[^parts]:
    Every part except the read of the context is measured from the 67,388
    requests of my sessions. The cache write after a compaction is split by
    the characters each source put into the first request after each of the
    478 compactions and into the requests after it, at characters per token
    fitted to their logged usage (`model/context.py`).
