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

// What a compaction leaves, whatever it starts from: across 141 compactions in
// the author's sessions (7-16 Nov 2026) the first request after one carried a
// median 122k tokens (quartiles 111k-128k): the opening prompt (system prompt,
// tools, instructions; 85k), a summary of about 3k, and the recent messages
// and files re-attached (34k). The next request writes it to the cache again.
const KEPT = 120_000
// The summary's output, with some allowance for the pass around it.
const SUMMARY = 4_000
// What a token costs, in cached reads: on Opus and Sonnet 5.5 a read is 0.05x
// the input price, a one-hour cache write 2x and an output token 5x.
const WRITE = 40
const OUTPUT = 100

// The judge is asked only from this many context tokens. A compaction there
// pays for itself after about 19 requests (`payback`), within a typical turn;
// at 200k it takes about 65. The question tells the judge that figure for the
// context it holds; this floor stays until the evals show the judge weighs it
// well on smaller contexts.
const FLOOR = 400_000

// An idle session is asked from this many tokens, the size from which Claude
// Code's own idle compaction acts. That one runs only in a terminal session,
// so a background session (`claude --bg`, agent view) is left to this.
const IDLE_FLOOR = 200_000

// How long a session sits idle before it is asked: inside the hour a
// subscription's prompt cache lives, so the question and any summary still
// read the context from the cache.
const IDLE_MS = 55 * 60_000

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

    // A subscription caches for an hour and reports a five-hour rate-limit
    // window; an API key caches for five minutes, too short for this to pay.
    // The wait starts at the turn's end, whatever the judge does meanwhile.
    if (tokens >= IDLE_FLOOR && rateLimits.some(limit => limit.kind === 'five_hour')) {
      $.clock.after(IDLE_MS, () => void idle($, isCurrent, say, tokens, reply))
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
async function idle($: EngineInterface, isCurrent: () => boolean, say: (text: string) => void, tokens: number, reply: string) {
  const isUntouched = async () => isCurrent() && (await $.session.usage()).context.tokens === tokens
  try {
    if (!(await isUntouched())) return
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
  const kept = thousands(KEPT)
  const isIdle = idleMinutes !== undefined
  const opening = isIdle
    ? `Your turn ended ${idleMinutes} minutes ago with this reply to the user, and nothing has happened since:`
    : 'Your turn just ended with this reply to the user:'
  const ask = isIdle
    ? `Decide whether to compact the conversation now. The prompt cache holding it lapses in a few minutes, and the first request after that writes all ${now} tokens to the cache again, against about ${kept} after a compaction. So compact unless the next turn continues work that needs the transcript's detail.`
    : 'Decide whether to compact the conversation now, while the user reads that reply.'
  // Idle, the cache is about to go whatever the answer, so the sum is the one
  // the request above states.
  const sum = isIdle ? '' : ` At ${now} a compaction pays for itself after about ${payback(tokens)} more requests that need nothing it dropped.`
  return `This message comes from the sparkjoy plugin, not from the user, and a program reads your reply. Use no tools.

${opening}

<reply>
${reply}
</reply>

${ask}

Nearly every long transcript is mostly finished work, so that alone is no reason to compact. What decides it is whether the next turns will need detail a summary drops: exact output, what was tried and failed, what the user objected to and why, the state of a design or fix still taking shape. Work that iterates with the user on something just shown, or takes up a half-done fix or design, needs that detail; after a compaction it re-reads and re-runs what it can and loses the rest. Work that only reports a result, or starts something new, needs no more than a summary.

A compaction takes about a minute and writes a summary of about ${thousands(SUMMARY)} output tokens. Afterwards the context holds about ${kept} tokens, which the next request writes to the cache again. A cache write costs as much as ${WRITE} cached reads, and an output token as much as ${OUTPUT}.${sum}

Compact only when the next turns won't need such detail and enough requests will follow to repay it. Otherwise keep it: a later turn end, once that work is finished, is the place to compact.

Reply with one JSON object and nothing else, filling its fields in order:
{"next": "what most likely comes next, in a few words", "needs_detail": true or false, "compact": true or false, "reason": "one short line saying why"}`
}

// How many later requests a compaction at `tokens` takes to pay for itself:
// its summary and the rewrite of what it keeps, in cached reads, over what
// each later request reads less.
function payback(tokens: number) {
  return Math.ceil((OUTPUT * SUMMARY + WRITE * KEPT) / (tokens - KEPT))
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
