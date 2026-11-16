# When to compact

Every request in a Claude Code session reads the whole conversation from the
prompt cache, so a long context makes every request dearer. A compaction
replaces the conversation with a summary: it costs a summary and a rewrite of
the shorter context to the cache, and makes every later request cheaper. This
page models that trade, fits the model to the author's sessions, and gives the
context size at which to compact.

For the author's sessions on Opus 5.5 with a one-hour cache, the model says to
compact at the first turn end past about **210k tokens**. That costs 28% less
than never compacting; sparkjoy's current floor of 400k costs 23% less. The
answer depends on how context size changes what each request gets done, which
the model carries as a coefficient κ, and it moves little across the range
asked about:

| κ   | best point, compacting after any request | best threshold at turn ends | saved there | saved at 400k |
| --- | ---------------------------------------- | --------------------------- | ----------- | ------------- |
| 0.8 | 246k (238–257k)                          | 200k (190–210k)             | 33%         | 27%           |
| 0.9 | 254k (245–265k)                          | 210k (200–220k)             | 31%         | 25%           |
| 1.0 | 264k (255–276k)                          | 210k (200–250k)             | 28%         | 23%           |
| 1.1 | 276k (267–288k)                          | 210k (210–280k)             | 25%         | 20%           |
| 1.2 | 292k (282–305k)                          | 250k (230–290k)             | 21%         | 18%           |

The second column is the model's closed-form optimum; the third comes from
replaying the sessions, since sparkjoy can compact only between turns. "Saved"
is the cost per unit of work against never compacting, where Claude Code still
compacts near the end of the window. Ranges are 90% intervals from resampling
the sessions.

## The model

### Units

Costs are counted in cached reads, the price of reading one token from the
prompt cache. Every other kind of token is a multiple of it
([pricing](https://platform.claude.com/docs/en/about-claude/pricing),
November 2026):

| one token of            | symbol | Opus 5.5, Sonnet 5.5 | Fable 5.2 |
| ----------------------- | ------ | -------------------- | --------- |
| cache read              |        | 1                    | 1         |
| uncached input          | u      | 20                   | 40        |
| output                  | o      | 100                  | 200       |
| cache write, 5 minutes  | w      | 25                   | 50        |
| cache write, 1 hour     | w      | 40                   | 80        |

A cached read costs $0.20 per million tokens on Opus 5.5 and Fable 5.2, and
$0.10 on Sonnet 5.5. None of the three charges more for long prompts. Claude
Code caches for an hour on a subscription and for five minutes with an API key.

### What a request costs

A request with context x reads the context from the cache, writes the g tokens
added since the request before it, and produces ō output tokens:

```math
c(x) = h\,x + c_0, \qquad h = 1 + \ell\,(w-1), \qquad c_0 = o\,\bar{o} + u\,\bar{\imath} + (w-1)\,g
```

A share ℓ of requests find the cache lapsed or invalidated and write the whole
context again, which h counts. ī is the uncached input, a couple of tokens. c₀
is the part of a request's cost that doesn't grow with the context.

### What a compaction costs

```math
F(x) = x + o\,S + (w-1)\,(K - P + E)
```

- The summary request reads the context x from the cache and writes a summary
  of S output tokens.
- The next request starts from K tokens: the system prompt, tools and
  instructions, the summary, and the recent messages and files Claude Code
  attaches again. Claude Code reads P of them, the system prompt and tools,
  from a cache shared across sessions, and writes the other K − P again.
- The requests after that read E tokens they would not have needed without the
  compaction, mostly files the summary left out.

The context then grows again from K′ = K + E. F₀ = oS + (w−1)(K − P + E) is
the part of F that doesn't depend on x.

### Work and the context efficiency coefficient

The size of the context can change how much a request gets done. A larger
context can help, because more of the work's history is at hand, or hurt,
because models attend less reliably over long inputs. The model has a request
with context x do

```math
\eta(x) = (x/W)^{\kappa - 1}
```

units of work, where W is the 1M-token window and κ is the context efficiency
coefficient. At κ = 1 every request does the same work, whatever its context.
Below 1 smaller contexts do more, and above 1 larger ones do. At κ = 0.9 a
request with 200k of context does 15% more than one with 800k; at κ = 1.2 it
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

At κ = 1 the condition solves in closed form:

```math
X^* = K' + \sqrt{\frac{2\,g\,(F_0 + K')}{h}}
```

This is Harris's
[economic order quantity](https://doi.org/10.1287/opre.38.6.947) (1913): the
context is stock that costs h cached reads per token per request to hold, and a
compaction is an order with fixed cost F₀ + K′. Two properties follow.

- At X\*, a compaction pays for itself in about half the requests the context
  takes to grow back to X\*. At 300k, in the fitted sessions, a compaction pays
  back after 27 requests and the context takes 89 to grow back, so compacting
  pays; at 200k the figures are 74 and 32, so it doesn't. Payback is to be
  compared with the regrowth time, not with the length of a turn.
- Cost is flat near X\*. Compacting at twice or half the optimal growth Δ\* =
  X\* − K′ raises the cost that depends on X by 25%, which in the fitted
  sessions is 8% of a request's total.

For κ ≠ 1, `model/fit.py` solves the condition numerically. There c₀ matters:
output and new tokens cost the same per request at any context, but a request's
work doesn't, so they weigh on the choice only when κ ≠ 1.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/model-sawtooth-dark.svg">
  <img alt="Context per request under the fitted model. Compacting at X* = 264k gives a sawtooth between K' = 142k and 264k, each tooth 69 requests long; never compacting climbs in a straight line to 965k, where Claude Code compacts. The area under each line is what its requests read." src="../assets/model-sawtooth-light.svg">
</picture>

## Fitted parameters

From the author's 85 interactive sessions of more than one request, 7 to 15
November 2026, 36,249 main-thread requests, priced at Opus 5.5 with the
one-hour cache the sessions used:

| parameter                                    | value              | 90% interval | how                                                                          |
| -------------------------------------------- | ------------------ | ------------ | ---------------------------------------------------------------------------- |
| K, context after a compaction                | 122k               | 120–126k     | median first request after 135 compactions                                   |
| P, read from the shared cache                | 30k                | 30–31k       | that request's cache reads                                                   |
| E, extra tokens read after a compaction      | 20k                | 14–25k       | first 20 requests of turns after a compaction, less those after another turn |
| g, growth per request                        | 1,776              | 1,673–1,897  | mean, across requests                                                        |
| ō, output per request                        | 746                | 695–798      | mean                                                                         |
| ℓ, requests that rewrite the context         | 0.44%              | 0.39–0.49%   | 18% of turn ends are followed by a gap of over an hour                       |
| S, summary output                            | 4,000              | assumed      | the summary request isn't logged                                             |
| h, c₀, F₀, K′                                | 1.17, 144k, 4.77M, 142k |         | derived; F₀ is $0.95                                                         |

Each term moves X\* at κ = 1, from 264k, by: E, 31k (without it, 234k); the
lapses in h, 10k; the assumed S, 5k if it doubled. Two checks bear on what the
model assumes:

- **E covers the re-reading.** Of the 20 requests after a compaction, 0.24
  more re-read a file from the dropped context than re-read an earlier file
  after an ordinary turn end (90%: 0.04 to 0.44). Claude Code attaches the
  files a session used last, so a compaction costs about a quarter of a request
  in re-reading.
- **g isn't constant.** It falls from 2.3k tokens per request at 100–200k to
  1.4k at 400–500k. The replay uses each request's own growth, and its best
  point, compacting after any request, is 270k, beside the formula's 264k.

## Results

### κ moves the point little

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/model-threshold-dark.svg">
  <img alt="Best compaction point against κ from 0.6 to 2. The steady-state point rises slowly from 234k at κ = 0.6 to 292k at 1.2, crosses 400k near κ = 1.5 and reaches 965k, Claude Code's own compaction, near 1.8. The replay's best turn-end threshold stays lower, between 180k and 290k up to κ = 1.6." src="../assets/model-threshold-light.svg">
</picture>

Across κ from 0.8 to 1.2 the best point moves from 246k to 292k. Reading cost
grows in proportion to the context, and work per request only as x^(κ−1), so a
coefficient near 1 barely shifts the balance. For the point to reach 400k, κ
has to be 1.52, where a request at 800k does twice the work of one at 200k.
Above κ = 1.81, compacting before Claude Code does never pays.

Benchmarks of long-context retrieval put κ just under 1, if work tracked their
scores. Anthropic's system cards report MRCR v2 (8 needles) for
[Opus 4.6](https://anthropic.com/claude-opus-4-6-system-card) falling from 91.9
at 256k to 78.3 at 1M, which is κ ≈ 0.88, and GraphWalks BFS for
[Opus 4.8](https://anthropic.com/claude-opus-4-8-system-card) falling from 85.9
to 68.1, κ ≈ 0.83. Retrieval measures less than getting work done, and studies
of agents disagree on whether long contexts hurt them: on SWE-bench Verified,
[longer contexts go with lower success](https://arxiv.org/abs/2602.16069), while
[LoCoBench-Agent](https://arxiv.org/abs/2511.13998) finds agents robust to
length. Across the range these numbers span, κ moves the threshold by tens of
thousands of tokens.

### Turn ends lower the threshold

sparkjoy compacts only between turns. A turn runs a median of 9 requests but a
mean of 38, because a few run to hundreds, so one turn can carry the context
far past X\* before the next chance to compact. The best threshold at turn ends
therefore sits below X\*.

The replay measures how far. It keeps every logged request, with its growth,
output and timing, and moves only the compactions: those sparkjoy or Claude
Code's auto-compaction made are removed, those the user typed stay, and each
arm compacts by its own rule. Compacting after any request, the replay's best
point is 270k, so the steady-state model holds on real sessions, and session
ends hardly matter: a session spans about six cycles. Compacting only at turn
ends, the best threshold falls to 210k.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/model-cost-dark.svg">
  <img alt="Replayed cost per unit of work against the turn-end threshold, for κ of 0.8, 1 and 1.2. Each curve falls to a flat minimum (200k at 67%, 210k at 72%, 250k at 79% of the cost of never compacting) and rises steadily to 100% near 950k. sparkjoy asks from 400k." src="../assets/model-cost-light.svg">
</picture>

At κ = 1, any threshold from 170k to 290k comes within one percentage point of
the best. A threshold of 400k costs 7% more per unit of work than the best,
close to the 8% the closed form predicts, and 600k costs 22% more.

### Lost detail

The model prices a compaction in tokens. Its other cost is work the summary no
longer supports: a mistake, a correction from the user, a step re-run. If that
costs D cached reads on average, D adds to F₀, and X\* grows with
√(F₀ + K′ + D). At κ = 1, X\* reaches 400k when D is 17.0M cached reads, $3.40
at Opus 5.5 prices: 3.3 times the compaction's own cost at 400k, or the price
of writing 436k tokens of files again, about 20 times the E measured. In his
own sessions, LangWatch's author
[found](https://langwatch.ai/blog/context-tax-when-to-compact) that 42% of his
messages in the five steps after a compaction carried a correction, matched by
phrase, against 18% otherwise.

D varies from one turn end to the next, which a threshold can't see.
sparkjoy's question asks the session's model whether the next turns will need
detail a summary drops; in the model's terms, it estimates D for the moment at
hand.

### Idle sessions

When a session sits idle until its cache lapses, the next request writes the
whole context again, compacted or not. Keeping costs that request
P + w(x − P); compacting first, while the cache still holds the context, costs
the summary, x + oS, and then P + w(K′ − P). Compacting is cheaper when

```math
x > \frac{o\,S + w\,K'}{w - 1} \approx 156\text{k}
```

counting the next request alone; the cheaper requests after it only add to the
saving. In the replay, at κ = 1:

| policy                                          | saved |
| ----------------------------------------------- | ----- |
| turn ends from 400k, sparkjoy's current floor   | 22.6% |
| and idle sessions from 200k, its idle floor     | 26.7% |
| turn ends from 210k                             | 27.7% |
| and idle sessions from 156k                     | 28.3% |

18% of turn ends here are followed by an hour's gap, so idle compaction does
most of what a lower turn-end threshold would. With both in place, sparkjoy's
floors save 1.6 points less than the best ones.

### Other prices and cache lifetimes

The same sessions, priced again, at κ = 1:

| prices               | cache     | ℓ     | h    | X\*  | best at turn ends | saved | idle break-even |
| -------------------- | --------- | ----- | ---- | ---- | ----------------- | ----- | --------------- |
| Opus 5.5, Sonnet 5.5 | 1 hour    | 0.44% | 1.17 | 264k | 210k              | 27.7% | 156k            |
| Opus 5.5, Sonnet 5.5 | 5 minutes | 2.08% | 1.50 | 230k | 180k              | 34.7% | 165k            |
| Fable 5.2            | 1 hour    | 0.44% | 1.35 | 302k | 280k              | 23.7% | 154k            |
| Fable 5.2            | 5 minutes | 2.08% | 2.02 | 248k | 180k              | 33.6% | 162k            |

A Fable 5.2 cache write costs twice as many cached reads, which raises F₀ and
with it X\*. A five-minute cache lapses after 2% of requests, half of them at
turn ends, which makes a large context dearer to hold and lowers X\*.

## What the model leaves out

- **Which moment it is.** The threshold treats every turn end alike; the lost
  detail D above is what differs between them.
- **Time.** A compaction takes a median 62 s, which sparkjoy spends while the
  user reads the reply.
- **Subagents,** whose contexts are separate.
- **Subscription limits,** whose weighting of token kinds isn't published. If
  it follows API prices, the answer is the same.
- **Work beyond κ.** The replay keeps every logged request, so each arm does
  the same work by construction, and only κ lets context size change it.

## Prior work

- **The economics.** Harris's order quantity, Kletke's replacement rule, and
  the renewal-reward theorem in Ross, all cited above.
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
uv run --script model/fit.py                                                   # the last 14 days
uv run --script model/fit.py --since 2026-11-07T12:00Z --until 2026-11-16T00:00Z   # this page's sessions
uv run --script model/fit.py --model claude-fable-5-2 --ttl 5m                 # other prices and cache lifetime
uv run --script model/fit.py --charts assets                                   # redraw the charts
```

The script reads the main thread of each interactive session in
`~/.claude/projects`, measures the parameters above, resamples sessions for the
intervals, and prints every number on this page. It opens only transcripts
modified since `--since`, at low priority. A fit needs some compactions in the
window, to measure K and E.
