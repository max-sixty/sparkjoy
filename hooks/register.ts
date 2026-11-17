// Compacts the conversation where the work ahead stops needing most of it,
// rather than only where the context fills.
//
// After each main-loop turn that answered with the context at FLOOR or more,
// the session's own model judges, over its prompt-cached transcript
// (`$.model.fork`), whether to compact, weighing what a compaction costs
// against the parts of the transcript the work ahead no longer needs; on yes,
// the mod compacts. A session left idle at IDLE_FLOOR or more is asked again
// just before its prompt cache lapses, told that the next request would
// otherwise write the whole context to the cache again. Each decision shows in the transcript as a
// dim line the model never reads, so a session that stays large says why. The
// engine's auto-compaction at its threshold still runs as before, and covers a
// turn that grows past the floor before it ends.
//
// The judge is the session's model over the whole transcript rather than a
// small model over the last reply: which parts the work ahead still needs
// depends on the work, and the fork reads it from cache. The question gives
// the judge the costs and the saving, sized for the context it holds, to weigh
// rather than a rule, and never names the floor. The mod runs only where a person is at the prompt: a `-p` run or
// an SDK host keeps the engine's own compaction, so no script pays for a judge
// or finds its conversation changed.

import type { EngineInterface, Register } from 'claude-code'

// What the author's sessions measured (docs/when-to-compact.md, 7-15 Nov
// 2026), which the question gives every user as a guide to what keeping costs
// and what a compaction loses. The costs follow that page's model ("The
// model") and fitted parameters ("Fitted parameters"), with the symbols of
// `terms()` in model/fit.py: K is the first request after a compaction, and
// K + E, what the page calls K, is what the requests after it hold.
// TODO: fit these to the user's own sessions, as model/fit.py and
// model/calibrate.py do for the author's, and state those instead.

// What a token costs, in cached reads: on Opus and Sonnet 5.5 a read is 0.05x
// the input price, a one-hour cache write 2x (w) and an output token 5x (o).
const WRITE = 40
const OUTPUT = 100
// The summary's output (S), with some allowance for the pass around it.
const SUMMARY = 4_000
// What a compaction leaves, whatever it starts from (K): across 141
// compactions in the author's sessions (7-16 Nov 2026) the first request after
// one carried a median 122k tokens (quartiles 111k-128k): the opening prompt
// (system prompt, tools, instructions; 85k), a summary of about 3k, and the
// recent messages and files re-attached (34k).
const K = 122_000
// Of those, the system prompt and tools (P), which Claude Code keeps in a
// cache shared across sessions, so the next request makes a cache write of
// only the rest.
const P = 30_000
// What the requests after a compaction read again (E), mostly files the
// summary left out: a cache write of its own, and part of what each later
// request reads.
const E = 20_000
// The share of requests that find the prompt cache lapsed and make a cache
// write of the whole context but P (ℓ), mostly after a gap of over an hour.
const LAPSE = 0.0056
// Requests in a turn: the median, and the mean, which a few long turns raise.
const TURN_MEDIAN = 9
const TURN_MEAN = 38
// The wasted requests (failed tool calls, repeated lookups, corrections)
// beyond the usual after a compaction the user or Claude Code made. After
// sparkjoy's, which came where a piece of work ended, there were fewer wasted
// requests than usual.
const WASTED = 6

// The judge is asked at a turn end only from this many context tokens, and in
// an idle session from IDLE_FLOOR: the pair of floors that costs least when
// docs/when-to-compact.md replays the author's sessions ("Idle sessions").
// 250k also comes within 0.2 points of the best turn-end threshold for any s
// up to 200k ("What it implies for sparkjoy"). A compaction there pays for
// itself after about 39 requests (`payback`); the question tells the judge
// that figure for the context it holds.
const FLOOR = 250_000

// An idle session is asked from this many tokens. Compacting just before the
// cache lapses pays from about 156k, counting only the next request, whose
// cache write it shrinks. Claude Code's own idle compaction acts only from
// 200k, and only in a terminal session, so a background session
// (`claude --bg`, agent view) is left to this.
const IDLE_FLOOR = 175_000

// How long a session sits idle before it is asked: inside the hour a
// subscription's prompt cache lives, so the question and any summary still
// read the context from the cache.
const IDLE_MS = 55 * 60_000

// A wait that ends later than this after it was due slept with the machine,
// and the prompt cache lapsed meanwhile, so asking would pay to write the
// whole context to the cache again. Claude Code's own idle compaction gives up
// at the same point. A mock clock can't hold a wait while its time moves on, so
// no test reaches this.
const LATE_MS = 60_000

type Verdict = { compact: boolean; reason: string }

type Watch = {
  isInteractive: boolean
  // Counts what extended or replaced the main conversation: a turn begun, a
  // `/compact`, a /clear or resume. A verdict stands only while the count is
  // the one its turn ended at.
  changes: number
}

export const register: Register = on => {
  const watch: Watch = { isInteractive: false, changes: 0 }

  on('session.start', ($, e, next) => {
    watch.isInteractive = e.isInteractive
    return next(e)
  })

  on('turn.start', ($, e, next) => {
    if (e.agentId === undefined) watch.changes++
    return next(e)
  })

  // A `/compact` typed while the judge runs. An auto-compaction runs inside a
  // turn, which `turn.start` has counted. `session.compact` would name each
  // compaction itself, but on a machine with managed settings
  // cc-plugin-sec-default@builtin sends that event past every user plugin, so a
  // hook on it never runs there and Claude Code tells the user it was skipped.
  on('command.run', { command: 'compact' }, ($, e, next) => {
    watch.changes++
    return next(e)
  })

  on('session.end', ($, e, next) => {
    watch.changes++
    return next(e)
  })

  on('turn.complete', async ($, e, next) => {
    // Counted before the hooks beneath run: the engine can start queued work,
    // such as a notification's turn, while they do.
    const changes = watch.changes
    const result = await next(e)
    // The judge runs outside the turn, on a timer of its own, so the turn
    // ends without waiting for it.
    if (watch.isInteractive && e.agentId === undefined && e.reason === 'answer') {
      $.clock.after(0, () => void afterTurn($, watch, changes, e.answer))
    }
    return result
  })
}

async function afterTurn($: EngineInterface, watch: Watch, changes: number, reply: string) {
  // A line about a conversation that has moved on would land inside the turn
  // now running, so it goes to the debug log.
  const isCurrent = () => watch.changes === changes
  const say = (text: string) => $.ui.log(text, isCurrent() ? undefined : { to: 'debug' })
  try {
    const { context, rateLimits } = await $.session.usage()
    const tokens = context.tokens
    if (tokens === undefined || !isCurrent()) return

    // A subscription caches for an hour and reports at least one rate-limit
    // window, though not always a five-hour one: some plans report only the
    // weekly window. An API key reports none and caches for five minutes, too
    // short for this to pay. The wait starts at the turn's end, whatever the
    // judge does meanwhile.
    // TODO: gate on the cache lifetime the main thread's requests asked for,
    // as Claude Code's own idle compaction does, rather than on rate limits,
    // which misread usage credits and gateways (both cache five minutes). The
    // mod API gives it only for subagents (`$.agent.list()`'s
    // `lastRequest.cacheTtl`). An `http.fetch` hook can read `"ttl":"1h"` from
    // the engine's request bodies, but the test engine can't raise one, so the
    // idle tests could no longer arm the timer.
    if (tokens >= IDLE_FLOOR && rateLimits.length > 0) {
      const due = (await $.clock.now()) + IDLE_MS
      $.clock.after(IDLE_MS, () => void idle($, isCurrent, say, tokens, reply, due))
    }
    if (tokens < FLOOR) return
    await judge($, say, async () => isCurrent(), `${thousands(tokens)} tokens`, question(reply, tokens))
  } catch (error) {
    say(`Failed: ${error}`)
  }
}

// Asks a session nobody has touched since the turn that set this timer again,
// now that its prompt cache is about to lapse: no turn has begun since, and
// the context is the size it was then. After any compaction, the judge's or
// Claude Code's own idle one, the context has no size until the next request.
async function idle($: EngineInterface, isCurrent: () => boolean, say: (text: string) => void, tokens: number, reply: string, due: number) {
  const isUntouched = async () => isCurrent() && (await $.session.usage()).context.tokens === tokens
  try {
    if ((await $.clock.now()) > due + LATE_MS || !(await isUntouched())) return
    const minutes = IDLE_MS / 60_000
    await judge($, say, isUntouched, `${thousands(tokens)} tokens, idle ${minutes} minutes`, question(reply, tokens, minutes))
  } catch (error) {
    say(`Failed: ${error}`)
  }
}

// Asks the session's model whether to compact, and compacts on yes. The
// verdict stands only while `stands` holds once the model has answered; `at`
// names the moment in each line.
async function judge(
  $: EngineInterface,
  say: (text: string) => void,
  stands: () => Promise<boolean>,
  at: string,
  prompt: string,
) {
  const answer = await $.model.fork({ prompt })
  if (!(await stands())) return say(`Dropped the verdict at ${at}: the conversation moved on`)
  const verdict = answer.isAnswered ? parse(answer.text) : undefined
  if (verdict?.compact) {
    // `$.session.compact` draws no `/compact` row, so neither the user nor
    // the model reads the compaction as one the user typed. A hook's veto
    // draws its own line. It rejects while a turn runs, which `stands` rules
    // out for any turn begun since the one asked about ended.
    say(`Compacting at ${at}: ${verdict.reason}`)
    await $.session.compact()
  } else if (verdict) {
    say(`Kept at ${at}: ${verdict.reason}`)
  } else if (answer.isAnswered) {
    say(`Unreadable verdict at ${at}: ${answer.text}`)
  } else {
    say(`No verdict at ${at}: ${answer.reason}`)
  }
}

// The fork replays the turn's last request, which ends before the reply the
// turn closed with, so the question carries the reply whole. Asked of an idle
// session, it says so and that the prompt cache is about to lapse. README.md
// shows both versions verbatim, for a 500k context.
function question(reply: string, tokens: number, idleMinutes?: number) {
  const now = thousands(tokens)
  const isIdle = idleMinutes !== undefined
  const opening = isIdle
    ? `Your turn ended ${idleMinutes} minutes ago with this reply to the user, and nothing has happened since:`
    : 'Your turn just ended with this reply to the user:'
  const ask = isIdle
    ? `Decide whether to compact the conversation now. The prompt cache holding it lapses in a few minutes, and the first request after that writes all but ${thousands(P)} of its ${now} tokens to the cache again, against about ${thousands(K - P)} after a compaction. So compact unless the next turn continues work that needs the transcript's detail.`
    : 'Decide whether to compact the conversation now, while the user reads that reply.'
  // Idle, the cache is about to go whatever the answer, so the sums are the one
  // the request above states.
  const sum = isIdle ? '' : ` At ${now} a compaction pays for itself after about ${payback(tokens)} more requests that need nothing it dropped.`
  const keeping = isIdle
    ? ''
    : `\n\nKeeping costs too, and more the larger the context: every later request reads all ${now} tokens, against about ${thousands(K + E)} after a compaction, and keeping only puts the compaction off to a later turn end. A turn runs a median of ${TURN_MEDIAN} requests and a mean of ${TURN_MEAN}, so keeping ${now} through one more turn costs as much as about ${turnCost(tokens, TURN_MEDIAN)} compactions for a median turn and ${turnCost(tokens, TURN_MEAN)} for a mean one.`
  return `This message comes from the sparkjoy plugin, not from the user, and a program reads your reply. Use no tools.

${opening}

<reply>
${reply}
</reply>

${ask}

Nearly every long transcript is mostly finished work, so that alone is no reason to compact. What decides it is whether the next turns will need detail a summary drops: exact output, what was tried and failed, what the user objected to and why, the state of a design or fix still taking shape. Work that iterates with the user on something just shown, or takes up a half-done fix or design, needs that detail; after a compaction it re-reads and re-runs what it can and loses the rest. Work that only reports a result, or starts something new, needs no more than a summary.

A compaction takes about a minute and writes a summary of about ${thousands(SUMMARY)} output tokens. Afterwards the context holds about ${thousands(K)} tokens, which the next request writes to the cache again, all but the ${thousands(P)} of system prompt and tools, which stay cached. A cache write costs as much as ${WRITE} cached reads, and an output token as much as ${OUTPUT}.${sum}${keeping}

After a compaction, past sessions read about ${thousands(E)} tokens of files again and had about ${WASTED} more wasted requests than usual: failed tool calls, repeated lookups and corrections. Those counts miss detail lost without such a failure, such as a design's rejected options or what the user likes.

Weigh what keeping costs against what losing the detail would cost the next turns. The larger the context, the more detail a compaction can drop and still pay.

Reply with one JSON object and nothing else, filling its fields in order:
{"next": "what most likely comes next, in a few words", "needs_detail": true or false, "compact": true or false, "reason": "one short line saying why"}`
}

// How many later requests a compaction at `tokens` takes to pay for itself:
// its cost over what each later request saves.
function payback(tokens: number) {
  return Math.ceil(compaction(tokens) / saving(tokens))
}

// What keeping `tokens` through a turn of `requests` costs, in compactions:
// what its requests cost beyond what they would after one.
function turnCost(tokens: number, requests: number) {
  const n = (requests * saving(tokens)) / compaction(tokens)
  return n < 1.5 ? n.toFixed(1) : `${Math.round(n)}`
}

// What a compaction at `tokens` costs, in cached reads, F(x) = x + F0: the
// summary request reads the context and outputs the summary, then the
// requests after it make a cache write of what they hold but P, the E tokens
// read again included. A cache write costs WRITE - 1 more than the read it
// stands in for.
function compaction(tokens: number) {
  return tokens + OUTPUT * SUMMARY + (WRITE - 1) * (K - P + E)
}

// What each request after a compaction at `tokens` saves, in cached reads: it
// reads tokens - (K + E) less, and a token held costs h = 1 + ℓ(w - 1) reads
// a request, counting the lapses after which it takes a cache write again.
function saving(tokens: number) {
  return (1 + LAPSE * (WRITE - 1)) * (tokens - K - E)
}

function parse(text: string): Verdict | undefined {
  try {
    const value = JSON.parse(text.slice(text.indexOf('{'), text.lastIndexOf('}') + 1))
    const isVerdict = typeof value.compact === 'boolean' && typeof value.reason === 'string'
    return isVerdict ? value : undefined
  } catch {
    return undefined
  }
}

function thousands(tokens: number) {
  return `${Math.round(tokens / 1000)}k`
}
