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
//
// A compaction the mod announces still happens if the process ends before it
// finishes. Claude Code's background daemon restarts a session's process when
// the session looks idle and runs an older version than the daemon, as every
// background session does after an update. A busy session first looks idle at
// the end of a turn, which is when the mod compacts. So the mod keeps the
// announcement in `$.store` until the compaction settles, and the next process
// finishes it if the conversation hasn't moved on.

import type { EngineInterface, Register, SessionMessage } from 'claude-code'

// Set while this process compacts. `$.state` outlives a reload of the mod but
// not the process, so a reload that finds a compaction announced leaves it to
// the copy already making it.
const COMPACTING = { plugin: 'sparkjoy', key: 'compacting' } as const

// What the author's sessions measured (docs/cost-model.md, 7-15 Nov 2026),
// which the question gives every user as a guide to what keeping costs and
// what a compaction loses. The costs follow that page's model and its table of
// symbols ("Request and compaction costs"), which are those of `terms()` in
// model/fit.py.
// TODO: fit these to the user's own sessions, as model/fit.py and
// model/calibrate.py do for the author's, and state those instead.

// What a token costs, in cached reads: on Opus and Sonnet 5.5 a read is 0.05x
// the input price, a one-hour cache write 2x (w) and an output token 5x (o).
const WRITE = 40
const OUTPUT = 100
// The output tokens of the compaction request (N): the summary's text plus
// its thinking. Transcripts log the summary's text, a median 3.5k tokens at 4
// characters each, but not the request that wrote it. The main-thread
// requests whose visible output (text, and tool input as JSON) is half to 1.5
// times that log a median 1.61 times as many output tokens as they show,
// thinking included, which makes about 5.6k.
const SUMMARY = 5_600
// The context after a compaction (K): the first request after it, plus the
// files the next few requests read again. Across 141 compactions in the
// author's sessions (7-16 Nov 2026) the first request carried a median 122k
// tokens (quartiles 111k-128k): the opening prompt (system prompt, tools,
// instructions; 85k), the summary, and the recent messages and files
// re-attached (34k). The next few requests read about 20k more again than
// after another turn end. Every later request reads K, and the requests after
// the compaction make a cache write of all of it but P.
const K = 142_000
// The system prompt and tools (P), which Claude Code keeps in a cache shared
// across sessions, so no request after a compaction makes a cache write of
// them.
const P = 30_000
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
// docs/cost-model.md replays the author's sessions ("Idle sessions"). 250k
// also comes within 0.2 points of the best turn-end threshold for any β up to
// 0.6 ("Does the model's simplification matter?"). A compaction there pays for
// itself after about 40 requests (`payback`); the question tells the judge
// that figure for the context it holds.
const FLOOR = 250_000

// An idle session is asked from this many tokens. Compacting just before the
// cache lapses pays from about 160k, counting only the next request, whose
// cache write it shrinks. Claude Code's own idle compaction acts only from
// 200k, and only in a terminal session, so a background session
// (`claude --bg`, agent view) is left to this.
const IDLE_FLOOR = 175_000

// The mod acts only in a session whose model has a context window of at least
// this many tokens. The floors and the question's figures come from sessions
// with a 1M window, and on a 200k one Claude Code compacts before the context
// reaches FLOOR.
const WINDOW = 1_000_000

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

  on('session.start', async ($, e, next) => {
    watch.isInteractive = e.isInteractive
    const result = await next(e)
    if (watch.isInteractive) $.clock.after(0, () => void resume($))
    return result
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
    if (tokens === undefined || context.window < WINDOW || !isCurrent()) return

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
    await compact($, at)
  } else if (verdict) {
    say(`Kept at ${at}: ${verdict.reason}`)
  } else if (answer.isAnswered) {
    say(`Unreadable verdict at ${at}: ${answer.text}`)
  } else {
    say(`No verdict at ${at}: ${answer.reason}`)
  }
}

// A compaction under way, as `$.store` keeps it for the next process: `at`
// names the moment, and `messages` the keys of the conversation's messages
// then (`messageKey`).
type Announcement = { at: string; messages: string[] }

// Compacts, keeping the announcement in `$.store` under the session's id until
// the compaction settles, for `resume` to finish if the process ends first.
// The flag goes up before the announcement and down after it, so a reload in
// between never finds an announcement unflagged. A reload during the
// compaction stops this copy before it clears either, so the flag stays up
// until the process ends, and the announcement until `resume` clears it.
async function compact($: EngineInterface, at: string) {
  const key = `compacting:${await $.session.id()}`
  await $.state.set(COMPACTING, true)
  try {
    const announcement: Announcement = { at, messages: await messageKeys($) }
    await $.store.set(key, announcement)
    await $.session.compact()
  } finally {
    await $.store.delete(key)
    await $.state.set(COMPACTING, false)
  }
}

// Finishes a compaction announced in a process that ended before it settled,
// while every message of the conversation is one it held then. A turn adds
// messages, and so does a compaction, whose summary is a message even where it
// keeps the newest ones. An announcement the conversation has moved past, such
// as one a reload left, is only cleared.
async function resume($: EngineInterface) {
  try {
    const key = `compacting:${await $.session.id()}`
    const announced = (await $.store.get(key)) as Announcement | undefined
    if (announced === undefined || (await $.state.get(COMPACTING)).value) return
    const held = new Set(announced.messages)
    const now = await messageKeys($)
    if (now.length === 0 || !now.every(message => held.has(message))) return await $.store.delete(key)
    $.ui.log(`Finishing the compaction at ${announced.at}: Claude Code restarted before it landed`)
    await compact($, announced.at)
  } catch (error) {
    $.ui.log(`Failed: ${error}`)
  }
}

// The keys of the conversation's messages, of the newest 4096, the most
// `$.session.messages` returns. A process reads the conversation from the
// latest compaction before it started, and keeps what it read through its own
// compactions. So until a message is added, the messages a new process reads
// are among those the process before it read.
async function messageKeys($: EngineInterface) {
  return (await $.session.messages()).map(messageKey)
}

// A message's key, the same in each process that reads it, whichever release
// it runs. Claude Code before 2.1.326 gives no message its id, and refuses the
// `last` argument that makes a later release give it. So a message with tool
// calls or their results is keyed by the ids the Messages API gave the calls,
// and any other by a hash of its role and text. A turn whose messages all
// match earlier ones in role and text, such as a second "ok" answered word for
// word as the first was, leaves the announcement standing, and the next
// process finishes the compaction.
// TODO: key by the id a read naming `last` gives, once every release CI tests
// takes `last`.
export function messageKey(message: SessionMessage) {
  const calls = [...message.toolUses, ...(message.toolResults ?? [])].map(call => call.tool_use_id)
  return calls.length > 0 ? `${message.role} ${calls.join(' ')}` : hash(`${message.role} ${message.text}`)
}

// FNV-1a. At 32 bits, a new message takes the key of one of 4096 held about
// once in a million.
function hash(text: string) {
  let h = 0x811c9dc5
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 0x01000193)
  return (h >>> 0).toString(36)
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
    : `\n\nKeeping costs too, and more the larger the context: every later request reads all ${now} tokens, against about ${thousands(K)} after a compaction, and keeping only puts the compaction off to a later turn end. A turn runs a median of ${TURN_MEDIAN} requests and a mean of ${TURN_MEAN}, so keeping ${now} through one more turn costs as much as about ${turnCost(tokens, TURN_MEDIAN)} compactions for a median turn and ${turnCost(tokens, TURN_MEAN)} for a mean one.`
  return `This message comes from the sparkjoy plugin, not from the user, and a program reads your reply. Use no tools.

${opening}

<reply>
${reply}
</reply>

${ask}

Nearly every long transcript is mostly finished work, so that alone is no reason to compact. What decides it is whether the next turns will need detail a summary drops: exact output, what was tried and failed, what the user objected to and why, the state of a design or fix still taking shape. Work that iterates with the user on something just shown, or takes up a half-done fix or design, needs that detail; after a compaction it re-reads and re-runs what it can and loses the rest. Work that only reports a result, or starts something new, needs no more than a summary.

A compaction takes about a minute, and the request that writes its summary outputs about ${thousands(SUMMARY)} tokens, thinking included. Afterwards the context holds about ${thousands(K)} tokens, counting the files the next requests read again, and those requests write it to the cache again, all but the ${thousands(P)} of system prompt and tools, which stay cached. A cache write costs as much as ${WRITE} cached reads, and an output token as much as ${OUTPUT}.${sum}${keeping}

After a compaction, past sessions had about ${WASTED} more wasted requests than usual: failed tool calls, repeated lookups and corrections. That count misses detail lost without such a failure, such as a design's rejected options or what the user likes.

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
// compaction request reads the context and outputs the summary, then the
// requests after it make a cache write of K but P. A cache write costs
// WRITE - 1 more than the read it stands in for.
function compaction(tokens: number) {
  return tokens + OUTPUT * SUMMARY + (WRITE - 1) * (K - P)
}

// What each request after a compaction at `tokens` saves, in cached reads: it
// reads tokens - K less, and a token held costs h = 1 + ℓ(w - 1) reads a
// request, counting the lapses after which it takes a cache write again.
function saving(tokens: number) {
  return (1 + LAPSE * (WRITE - 1)) * (tokens - K)
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
