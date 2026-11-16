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
// the judge costs and a benefit to weigh rather than a rule, and never names
// the floor. The mod runs only where a person is at the prompt: a `-p` run or
// an SDK host keeps the engine's own compaction, so no script pays for a judge
// or finds its conversation changed.

import type { EngineInterface, Register } from 'claude-code'

// The judge is asked only from this many context tokens. A compaction leaves
// about 120k whatever it starts from in the author's sessions (system prompt,
// tools, instructions, the skills the session invoked, the summary and recent
// files). It costs a summary pass and a rewrite of what it keeps to the cache,
// and saves each later request what it removed, so the larger the context,
// the sooner it pays back. A replay of the author's sessions priced lower
// floors a few percent cheaper still; the floor sits above them because the
// replay can't see what a compaction costs the work after it.
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
    await judge($, say, async () => isCurrent(), `${thousands(tokens)} tokens`, question(reply))
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
    await judge($, say, isUntouched, `${thousands(tokens)} tokens, idle ${minutes} minutes`, question(reply, minutes))
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
// shows both versions verbatim.
function question(reply: string, idleMinutes?: number) {
  const opening =
    idleMinutes === undefined
      ? 'Your turn just ended with this reply to the user:'
      : `Your turn ended ${idleMinutes} minutes ago with this reply to the user, and nothing has happened since:`
  const ask =
    idleMinutes === undefined
      ? 'Decide whether to compact the conversation now, while the user reads that reply.'
      : "Decide whether to compact the conversation now. The prompt cache holding it lapses in a few minutes, and the first request after that writes the whole context to the cache again, which costs more than compacting now. So compact unless the work is in the middle of something that needs the transcript's detail."
  return `This message comes from the sparkjoy plugin, not from the user, and a program reads your reply. Use no tools.

${opening}

<reply>
${reply}
</reply>

${ask}

After a compaction the context holds the system prompt and instructions, a summary of the conversation, the latest messages verbatim (this reply among them), the files read most recently, the status of background agents and shells, and the transcript's path on disk, where exact output or wording can be looked up. What is lost is detail the summary leaves out, and the knowledge that it was there to look up.

A compaction costs a summary of the conversation, which takes many output tokens and about a minute. The cache no longer holds what the compaction carries over, such as the files read most recently, so the next request writes it all again.

It saves carrying the parts of the transcript the work ahead no longer needs, such as file reads, command output and diffs for finished work, and detours since resolved. Every later request reads whatever the context keeps.

Reply with one JSON object and nothing else:
{"compact": true or false, "reason": "one short line saying why"}`
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
