# When to compact

In a Claude Code session, every model request reads the whole conversation
from the prompt cache, so a long context makes every request dearer. A
compaction replaces the conversation with a summary. It costs a summary and a
rewrite of the shorter context to the cache, and makes every later request
cheaper. This page models that trade, fits the model to the author's sessions,
and gives the context size at which to compact.

For the author's sessions on Opus 5.5 with a one-hour cache, the model says to
compact when the context reaches about **260k tokens**. In plain numbers: a
compaction costs about as much as reading 4.8M cached tokens, $0.95; it leaves
about 142k; each later request reads the difference less; and the context
grows back about 1.7k tokens a request.

| compacting at | each later request reads | pays for itself after | context grows back in |
| ------------- | ------------------------ | --------------------- | --------------------- |
| 200k          | 58k less                 | 71 requests           | 34 requests           |
| 260k          | 118k less                | 35 requests           | 69 requests           |
| 400k          | 258k less                | 16 requests           | 151 requests          |
| 700k          | 558k less                | 8 requests            | 327 requests          |

Compacting costs least where a compaction pays for itself in about half the
time the context takes to grow back. At 200k it hasn't paid by the time the
context is back; at 400k it paid for itself long before.

A turn is everything Claude does between the user's message and its reply: a
run of model requests, typically one per tool call. A compaction inside a turn
would interrupt work in progress, so sparkjoy, like a person typing
`/compact`, compacts only at the end of a turn. Turns are uneven, a median 9
requests and a mean 38, so a policy limited to turn ends does best compacting
earlier, from about **200k**: 30.2% less than never compacting, where every
turn end past 400k, sparkjoy's current floor, saves 25.1%. Compacting idle
sessions as well, from about 175k, raises the best turn-end threshold to 250k
and the saving to 30.9%. The answer depends on how context size changes what
each request gets done, which the model carries as a coefficient α. Across the
range 0.8 to 1.2 it moves little:

| α   | best point, compacting after any request | best threshold at turn ends | saved there | saved at 400k |
| --- | ---------------------------------------- | --------------------------- | ----------- | ------------- |
| 0.8 | 243k (235–252k)                          | 200k (190–210k)             | 36.1%       | 29.5%         |
| 0.9 | 250k (243–259k)                          | 200k (190–220k)             | 33.3%       | 27.4%         |
| 1.0 | 259k (251–269k)                          | 200k (200–240k)             | 30.2%       | 25.1%         |
| 1.1 | 271k (262–281k)                          | 240k (200–250k)             | 27.0%       | 22.6%         |
| 1.2 | 285k (276–296k)                          | 240k (210–250k)             | 23.7%       | 20.0%         |

The second column is the steady-state model's optimum, derived below. The
third comes from replaying the sessions with compaction only at turn ends.
"Saved" is the cost per unit of work against never compacting, where Claude
Code still compacts near the end of the window. Ranges are 90% intervals from
resampling the 85 sessions; for the replay, the resampling holds the fitted
compaction parameters fixed.

## The model

### Units

Costs are counted in cached reads, the price of reading one token from the
prompt cache. Every other kind of token is a multiple of it
([pricing](https://platform.claude.com/docs/en/about-claude/pricing),
November 2026):

| one token of           | symbol | Opus 5.5, Sonnet 5.5 | Fable 5.2 |
| ---------------------- | ------ | -------------------- | --------- |
| cache read             |        | 1                    | 1         |
| uncached input         | u      | 20                   | 40        |
| output                 | o      | 100                  | 200       |
| cache write, 5 minutes | w      | 25                   | 50        |
| cache write, 1 hour    | w      | 40                   | 80        |

A cached read costs $0.20 per million tokens on Opus 5.5 and Fable 5.2, and
$0.10 on Sonnet 5.5. None of the three charges more for long prompts. Claude
Code caches for an hour on a subscription and for five minutes with an API key.

### What a request costs

A request with context x reads the context from the cache, writes the g tokens
added since the request before it, and produces ō output tokens. A share ℓ of
requests find the cache lapsed or invalidated, and write the whole context
again instead, except the system prompt and tools (P tokens), which Claude
Code keeps in a cache shared across sessions:

```math
c(x) = x + (w-1)\big[(1-\ell)\,g + \ell\,(x-P)\big] + o\,\bar{o} + u\,\bar{\imath} \;=\; h\,x + c_0
```

with h = 1 + ℓ(w−1), the cost of holding a token for a request, and
c₀ = oō + uī + (w−1)[(1−ℓ)g − ℓP], the part of a request's cost that doesn't
grow with the context. ī is the uncached input, a couple of tokens.

### What a compaction costs

```math
F(x) = x + o\,S + (w-1)\,(K' - P)
```

- The summary request reads the context x from the cache and writes a summary
  of S output tokens.
- The context then restarts from K′ tokens, and every one of them but the P in
  the shared cache is written to the cache again. K′ has two parts, measured
  separately: the K tokens of the first request after the compaction (the
  system prompt, tools and instructions, the summary, and the recent messages
  and files Claude Code attaches again), and the E tokens the requests after
  it read again because the summary left them out, such as a file read
  before. The model needs only their sum.

F₀ = oS + (w−1)(K′ − P) is the part of F that doesn't depend on x.

### Work and the context efficiency coefficient

The size of the context can change how much a request gets done. A larger
context can help, because more of the work's history is at hand, or hurt,
because models attend less reliably over long inputs. The model has a request
with context x do

```math
\eta(x) = (x/W)^{\alpha - 1}
```

units of work, where W is the 1M-token window and α is the context efficiency
coefficient. At α = 1 every request does the same work, whatever its context.
Below 1 smaller contexts do more, and above 1 larger ones do. At α = 0.9 a
request with 200k of context does 15% more than one with 800k; at α = 1.2 it
does 24% less. Only ratios of η enter the results, so the choice of W drops
out.

### The best compaction point

Suppose a session compacts each time its context reaches X. Its context grows
from K′ to X at g tokens per request, then starts again. By the renewal-reward
theorem ([Ross](https://shop.elsevier.com/books/introduction-to-probability-models/ross/978-0-443-18761-2),
ch. 7), its long-run cost per unit of work is one cycle's cost over one
cycle's work:

```math
J(X) = \frac{g\,F(X) + \int_{K'}^{X} c(x)\,dx}{\int_{K'}^{X} \eta(x)\,dx}
```

Setting dJ/dX to zero gives the condition for the best point X\*:

```math
\frac{c(X^*) + g}{\eta(X^*)} = J(X^*)
```

Compact when the next request's cost per unit of work reaches the average over
the cycle, compaction included. (The g is the next request's growth, which the
summary would also read.) It is the rule for replacing a machine whose running
cost rises with age: replace it when its marginal cost reaches its average
cost ([Kletke 1969](https://ageconsearch.umn.edu/record/29853/files/01010045.pdf)).

At α = 1 the condition solves in closed form:

```math
X^* = K' + \sqrt{\frac{2\,g\,(F_0 + K')}{h}}
```

This is Harris's
[economic order quantity](https://doi.org/10.1287/opre.38.6.947) (1913): the
context is stock that costs h cached reads per token per request to hold, and a
compaction is an order with fixed cost F₀ + K′. Two properties follow.

- At X\*, a compaction pays for itself in about half the requests the context
  takes to grow back to X\*, as the table at the top shows. The yardstick for
  payback is the regrowth time, not the length of a turn.
- Cost is flat near X\*. Compacting at twice the optimal growth X\* − K′, near
  380k here, raises the part of the cost that depends on X by 25%, which is 8%
  of a request's whole cost.

For α ≠ 1, `model/fit.py` solves the condition numerically. There c₀ matters:
output and new tokens cost the same per request at any context, but a request's
work doesn't, so they weigh on the choice only when α ≠ 1.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/model-sawtooth-dark.svg">
  <img alt="Context per request under the fitted model. Compacting at X* = 259k gives a sawtooth between K' = 142k and 259k, each tooth 69 requests long; never compacting climbs in a straight line to 965k, where Claude Code compacts. The area under each line is what its requests read." src="../assets/model-sawtooth-light.svg">
</picture>

## Fitted parameters

From the author's 85 interactive sessions of more than one request, 7 to 15
November 2026, 36,249 main-thread requests, priced at Opus 5.5 with the
one-hour cache the sessions used:

| parameter                               | value                    | 90% interval | how                                                                                |
| --------------------------------------- | ------------------------ | ------------ | ---------------------------------------------------------------------------------- |
| K, context after a compaction           | 122k                     | 120–125k     | median first request after 135 compactions                                         |
| P, read from the shared cache           | 30k                      | 30–31k       | that request's cache reads                                                         |
| E, extra tokens after a compaction      | 20k                      | 15–26k       | growth over the first 20 requests of a turn after a compaction, minus the same after another turn |
| g, growth per request                   | 1,706                    | 1,615–1,805  | mean, leaving out the 20 requests after each compaction                            |
| ō, output per request                   | 746                      | 699–792      | mean                                                                               |
| ℓ, requests that rewrite the context    | 0.56%                    | 0.51–0.63%   | 18% of turn ends are followed by a gap of over an hour                             |
| S, summary output                       | 4,000                    | 3,300–8,000  | the summary's text is a median 3,300 (chars/4); its thinking isn't logged, and the pass's median 58 s at this account's output speed allows up to about 8,000 |
| h, c₀, F₀, K′                           | 1.22, 134k, 4.77M, 142k  |              | derived; F₀ is $0.95                                                               |

At α = 1, X\* is 260k by the closed form above (259k on the script's 1k grid).
Without E it would be 229k; without the lapses in h, 272k; with S at 8,000,
264k. E likely leans high, since the turns after a compaction more
often start new work, which reads more files anyway.

Two measurements bear on what the model assumes:

- **Re-reading.** Over the 20 requests after a compaction, whatever turns they
  fall in, 0.24 more requests read a file that had been read before the
  compaction than, after an ordinary turn end, read a file read earlier in the
  context (90%: 0.04 to 0.50). Claude Code attaches the files a session used
  last, which may be why the excess is small.
- **Growth.** g falls from 2.3k tokens per request at 100–200k to 1.5k at
  400–500k, where the model takes it as constant. The replay below uses each
  request's own growth.

## Results

### α moves the point little

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/model-threshold-dark.svg">
  <img alt="Best compaction point against α from 0.6 to 2. The steady-state point rises slowly from 231k at α = 0.6 to 285k at 1.2, crosses 400k near α = 1.55 and reaches 965k, Claude Code's own compaction, near 1.8. The replay's best turn-end threshold stays lower, at 190k to 290k up to α = 1.6." src="../assets/model-threshold-light.svg">
</picture>

Across α from 0.8 to 1.2 the best point moves from 243k to 285k. Reading cost
grows in proportion to the context, and work per request only as x^(α−1), so a
coefficient near 1 barely shifts the balance. For the point to reach 400k, α
has to be 1.55, where a request at 800k does about twice the work of one at
200k. In the steady state, compacting before Claude Code does never pays above
α = 1.82; the replay still finds a fraction of a percent to save at 850k–930k.

The range 0.8 to 1.2 is an assumption, not a measurement. Long-context
retrieval benchmarks give a sense of scale: if work tracked their scores, α
would sit just under 1. Anthropic's system cards report MRCR v2 (8 needles)
for [Opus 4.6](https://anthropic.com/claude-opus-4-6-system-card) falling from
91.9 at 256k to 78.3 at 1M, which is α ≈ 0.88, and GraphWalks BFS for
[Opus 4.8](https://anthropic.com/claude-opus-4-8-system-card) falling from
85.9 to 68.1, α ≈ 0.83. Retrieval measures much less than getting work done.

### Turn ends lower the threshold

A turn runs a median of 9 requests but a mean of 38, because a few run to
hundreds, so one turn can carry the context far past X\* before the next
chance to compact. The best threshold at turn ends therefore sits below X\*.

The replay measures how far. It keeps every logged request, with its growth,
output and timing, and moves only the compactions: those sparkjoy or Claude
Code's auto-compaction made are removed, those the user typed stay, and each
arm compacts by its own rule. It charges E at once at each compaction an arm
makes, and takes E out of the growth of the requests after each one it
removes. Compacting after any request, the replay's best point is 260k, beside
the steady state's 259k: uneven growth, real lapses and sessions ending
together move the optimum little, though they may offset one another.
Compacting only at turn ends moves it to 200k.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/model-cost-dark.svg">
  <img alt="Replayed cost per unit of work against the turn-end threshold, for α of 0.8, 1 and 1.2. Under 142k, what a compaction leaves, every turn end compacts and the curves run flat at 70% to 86%. Each falls to a flat minimum (200k at 64%, 200k at 70%, 240k at 76% of the cost of never compacting) and rises steadily to 100% near 950k." src="../assets/model-cost-light.svg">
</picture>

At α = 1, any threshold from 170k to 280k comes within one percentage point of
the best. A threshold of 400k costs 7% more per unit of work than the best,
and 600k costs 24% more.

Compacting too early can't run away at turn ends. Under K′, every turn end
compacts, and that still costs about a fifth less than never compacting at α = 1,
because a turn grows the context by 68k on average. In the steady state, where
a compaction could follow any request, compacting at 150k would cost 3.2 times
as much per unit of work as compacting at X\*: a compaction every four
requests or so.

### Lost detail

The model prices a compaction in tokens. Its other cost is work the summary no
longer supports: a mistake, a correction from the user, a step re-run. If that
costs D cached reads on average, D adds to F₀, and X\* − K′ grows with
√(F₀ + K′ + D). At α = 1, X\* reaches 400k when D is 18.8M cached reads, $3.75
at Opus 5.5 prices: 3.6 times the compaction's own cost at 400k, or the price
of writing 481k tokens again, about 24 times the E measured. LangWatch's
author [found](https://langwatch.ai/blog/context-tax-when-to-compact) that in
their sessions, 42% of their messages in the five steps after a compaction
carried a correction, matched by phrase, against 18% otherwise.

D varies from one turn end to the next, which a threshold can't see.
sparkjoy's question asks the session's model whether the next turns will need
detail a summary drops; in the model's terms, it estimates D for the moment at
hand.

### Idle sessions

When a session sits idle until its cache lapses, the next request writes the
whole context again, compacted or not. Keeping costs that request
P + w(x − P). Compacting first, while the cache still holds the context, costs
the summary, x + oS, and then P + w(K′ − P). Compacting is cheaper when

```math
x > \frac{o\,S + w\,K'}{w - 1} \approx 156\text{k}
```

counting the next request alone; the cheaper requests after it only add to the
saving. sparkjoy asks once a session has sat 55 minutes since its last reply
without a new message. The replay runs that timer from each reply to the next
message, and takes a session's last reply to be followed by such a wait.
Replayed at α = 1, with every policy compacting wherever it allows:

| policy                                                   | saved |
| -------------------------------------------------------- | ----- |
| turn ends from 400k, sparkjoy's current floor            | 25.1% |
| and idle sessions from 200k, its idle floor              | 28.5% |
| turn ends from 200k, the best without idle compaction    | 30.2% |
| turn ends from 250k and idle sessions from 175k, the best pair | 30.9% |

The pairs come from a grid of turn-end thresholds in steps of 50k and idle
thresholds in steps of 25k; every pair of 200k–250k at turn ends and 150k–175k
idle comes within 0.3 points of the best. 18% of turn ends here are followed by
an hour's gap, so idle compaction does much of what a lower turn-end threshold
would: sparkjoy's two floors together save 2.4 points less than the best pair.

### Other prices and cache lifetimes

The same sessions, priced again, at α = 1:

| prices               | cache     | ℓ     | h    | X\*  | best at turn ends | saved | idle break-even |
| -------------------- | --------- | ----- | ---- | ---- | ----------------- | ----- | --------------- |
| Opus 5.5, Sonnet 5.5 | 1 hour    | 0.56% | 1.22 | 259k | 200k              | 30.2% | 156k            |
| Opus 5.5, Sonnet 5.5 | 5 minutes | 2.33% | 1.56 | 226k | 160k              | 37.3% | 165k            |
| Fable 5.2            | 1 hour    | 0.56% | 1.44 | 294k | 250k              | 26.9% | 154k            |
| Fable 5.2            | 5 minutes | 2.33% | 2.14 | 243k | 180k              | 36.7% | 162k            |

A Fable 5.2 cache write costs twice as many cached reads, which raises F₀ and
with it X\*. A five-minute cache lapses after 2.3% of requests, 57% of them at
turn ends, which makes a large context dearer to hold and lowers X\*.

## What it implies for sparkjoy

At α near 1, the model supports asking at turn ends from 200k–250k rather than
400k, and in idle sessions from 150k–175k rather than 200k. The question's
payback sum misleads when it is set beside the length of a turn. A compaction
is repaid over every request until the context grows back, (x − K′)/g of them,
and at a turn end it pays even before its payback falls to half of that,
because the next chance to compact may be hundreds of requests away. What a
threshold can't see, whether the next turns need detail a summary drops, stays
with the question.

## What the model leaves out

- **Which moment it is.** The threshold treats every turn end alike; the lost
  detail D above is what differs between them.
- **sparkjoy's check.** The replay compacts wherever its threshold allows, so
  it stands for a check that always says yes. Each real check costs about one
  request at the current context, and one that keeps saves nothing. The
  README's replay of the plugin as it ran, with its checks and the turn ends
  where it kept, saves 23%.
- **Time.** A compaction takes a median 62 s, which sparkjoy spends while the
  user reads the reply.
- **Subagents,** whose contexts are separate.
- **Subscription limits,** whose weighting of token kinds isn't published. If
  it follows API prices, the answer is the same.
- **Work beyond α.** The replay keeps every logged request, so each arm does
  the same work by construction, and only α lets context size change it.

## Prior work

- **Long contexts degrade.**
  [Lost in the Middle](https://aclanthology.org/2024.tacl-1.9/),
  [RULER](https://arxiv.org/abs/2404.06654),
  [NoLiMa](https://arxiv.org/abs/2502.05167),
  Chroma's [Context Rot](https://www.trychroma.com/research/context-rot), and
  [Du et al.](https://arxiv.org/abs/2510.05381), who find accuracy falls with
  length even when retrieval is perfect. Anthropic's
  [context engineering essay](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
  treats context as "a finite resource with diminishing marginal returns".
- **Compaction against cost.**
  [LangWatch](https://langwatch.ai/research/finding-the-optimal-context-window)
  fit the cost per productive step against the threshold on one engineer's
  sessions, with a rediscovery penalty for each compaction, and found 220k, or
  314k with twice the penalty; it recommends 250k to 450k by task.
  [Relace](https://relace.ai/blog/compact) argues for compacting on a cache
  miss, as for idle sessions above, and
  [exe.dev](https://blog.exe.dev/expensively-quadratic) shows cache reads come
  to dominate a long conversation's cost.
- **Other agents** compact at a fixed share of the window:
  [Codex CLI](https://github.com/openai/codex/blob/83a23c09366d82cd0cb469c64c3e55d04d71c87a/codex-rs/protocol/src/openai_models.rs#L579-L590)
  at 90%,
  [Gemini CLI](https://github.com/google-gemini/gemini-cli/blob/1603e9bab6c0527dfddb3215677a29ebf482e40f/packages/core/src/context/chatCompressionService.ts#L45)
  at 50%, [Amp](https://ampcode.com/news/neo) at 90%, and
  [Claude Code](https://code.claude.com/docs/en/model-config#default-auto-compact-thresholds)
  at about 967k of 1M, or
  [from 200k](https://code.claude.com/docs/en/settings-reference#idlecompaction)
  in a session idle for an hour. The Claude API's
  [server-side compaction](https://platform.claude.com/docs/en/build-with-claude/compaction-threshold)
  defaults to 150k.

## Fitting it to your sessions

```
uv run --script model/fit.py                                                       # the last 14 days
uv run --script model/fit.py --since 2026-11-07T12:00Z --until 2026-11-16T00:00Z   # this page's sessions
uv run --script model/fit.py --model claude-fable-5-2 --ttl 5m                     # other prices and cache
uv run --script model/fit.py --charts assets                                       # redraw the charts
```

The script reads the main thread of each interactive session in
`~/.claude/projects`, measures the parameters above, resamples sessions for the
intervals, and prints the figures on this page. It opens only transcripts
modified since `--since`, at low priority. A fit needs some compactions in the
window, to measure K and E.
