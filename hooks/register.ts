// Compacts the conversation where the work ahead stops needing most of it,
// rather than only where the context fills.
//
// After each main-loop turn that answered with the context at FLOOR or more,
// the session's own model judges, over its prompt-cached transcript
// (`$.model.fork`), whether to compact, weighing what a compaction costs
// against the parts of the transcript the work ahead no longer needs, and
// the mod compacts if it says to. A session left idle at IDLE_FLOOR or more is
// asked again just before its prompt cache lapses, whether the cache lives an
// hour or five minutes, told that the next request would otherwise write the
// whole context to the cache again. Each decision shows in the transcript as
// a dim line the model never reads, so a session that stays large says why.
// The engine's auto-compaction at its threshold still runs as before, and
// covers a turn that grows past the floor before it ends.
//
// The judge is the session's model over the whole transcript rather than a
// small model over the last reply: which parts the work ahead still needs
// depends on the work, and the fork reads it from cache. The question gives
// the judge the costs and the saving, sized for the context it holds, to weigh
// rather than a rule, and never names the floor. Its figures are the user's own
// once `/sparkjoy:report` has fitted them. The mod runs only where a person is at the prompt: a `-p` run or
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

import type { EngineInterface, Register, SessionMessage, TurnUsage } from 'claude-code'

// Set while this process compacts. `$.state` outlives a reload of the mod but
// not the process, so a reload that finds a compaction announced leaves it to
// the copy already making it.
const COMPACTING = { plugin: 'sparkjoy', key: 'compacting' } as const

// The floors sparkjoy asks from and the figures the question states, as my
// sessions measured them (docs/cost-model.md, 7-15 Nov 2026).
// `/sparkjoy:report` fits the same figures to the user's own sessions and
// saves them (`FIGURES`), and sparkjoy uses those instead (`fitted`), falling
// back to these figure by figure. The costs follow that page's model and its table of
// symbols ("Request and compaction costs"), which are those of `terms()` in
// model/fit.py, and each key is the name model/report.py saves its figure
// under: a figure whose meaning changes takes a new name, so a file an older
// report saved falls back for it.
const AUTHOR = {
  // The judge is asked at a turn end only from FLOOR context tokens, and in
  // an idle session from IDLE_FLOOR: the pair of floors that minimizes the cost
  // of my sessions when docs/cost-model.md replays them ("Idle sessions"), which
  // the report finds for the user's (`floors` in model/fit.py). 250k also comes
  // within 0.2 points of the best turn-end threshold for any β up to 0.6
  // ("Does the model's simplification matter?"). Compacting just before the
  // cache lapses pays from about 160k on a one-hour cache and 172k on a
  // five-minute one, counting only the next request, whose cache write it
  // shrinks. Claude Code's own idle compaction acts only from 200k, only on a
  // one-hour cache, and only in a session someone is attending (a terminal, VS
  // Code, the desktop app), so a five-minute cache or a background session
  // (`claude --bg`, agent view) is left to this.
  FLOOR: 250_000,
  IDLE_FLOOR: 175_000,
  // What a token costs, in cached reads: on Opus and Sonnet 5.5 a read is
  // 0.05x the input price, a one-hour cache write 2x (w) and an output token
  // 5x (o). The report prices the model most of the user's requests used, at
  // the cache lifetime most of their sessions did.
  WRITE: 40,
  OUTPUT: 100,
  // The output tokens of the compaction request (N): the summary's text plus
  // its thinking. Transcripts log the summary's text, a median 3.5k tokens at
  // 4 characters each, but not the request that wrote it. The main-thread
  // requests whose visible output (text, and tool input as JSON) is half to
  // 1.5 times that log a median 1.61 times as many output tokens as they
  // show, thinking included, which makes about 5.6k.
  SUMMARY: 5_600,
  // The context after a compaction (K): the first request after it, plus the
  // files the next few requests read again. Across 141 compactions in my
  // sessions (7-16 Nov 2026) the first request carried a median 122k
  // tokens (quartiles 111k-128k): the opening prompt (system prompt, tools,
  // instructions; 85k), the summary, and the recent messages and files
  // re-attached (34k). The next few requests read about 20k more again than
  // after another turn end. Every later request reads K, and the requests
  // after the compaction make a cache write of all of it but P.
  //
  // Most of K beyond P is the user's own instructions and skills, so K varies
  // more between users than across these sessions, and the report fits it.
  // Published sizes of the first request after a compaction:
  // - 37k with little memory and few skills, on a 200k window
  //   (https://github.com/anthropics/claude-code/issues/87602);
  // - 99k with five skills attached again in full, on 200k
  //   (https://github.com/anthropics/claude-code/issues/82851);
  // - about 320k with a 248 KB rules directory, on 1M
  //   (https://github.com/anthropics/claude-code/issues/85489).
  // Of what Claude Code attaches again, only the skill listing scales with the
  // window, at 1% of it (https://code.claude.com/docs/en/skills), so a figure
  // from a 200k window carries over to 1M with about 8k more. Before Claude
  // Code 2.1.304 a compaction dropped the listing
  // (https://github.com/anthropics/claude-code/issues/82017), and my first
  // request after one carried a median 92k.
  K: 142_000,
  // The system prompt and tools (P), which Claude Code keeps in a cache
  // shared across sessions, so no request after a compaction makes a cache
  // write of them.
  P: 30_000,
  // The share of requests that find the prompt cache lapsed and make a cache
  // write of the whole context but P (ℓ), mostly after a gap of over an hour.
  LAPSE: 0.0056,
  // Requests in a turn: the median, and the mean, which a few long turns
  // raise.
  TURN_MEDIAN: 9,
  TURN_MEAN: 38,
}

type Figures = typeof AUTHOR

// The wasted requests (failed tool calls, repeated lookups, corrections)
// beyond the usual after a compaction the user or Claude Code made, which
// model/calibrate.py measured over 60 days of my sessions. After sparkjoy's,
// which came where a piece of work ended, there were fewer wasted requests
// than usual. The report doesn't fit it to the user's sessions: over the
// report's two weeks, resampling the sessions moves it by more than its own
// size.
const WASTED = 6

// The file `/sparkjoy:report` saves the user's figures to, under Claude Code's
// configuration directory, as model/report.py names it. Its home would be the
// plugin's data folder, but Claude Code before 2.1.326 gives a hooks module no
// way to find that folder, and refuses at load a module that calls
// `$.process.hookEnv()`, the API that names it.
// TODO: save and read it in that folder once every release CI tests has
// `$.process.hookEnv()`.
const FIGURES = 'sparkjoy/figures.json'

// The mod acts only in a session whose model has a context window of at least
// this many tokens. The floors and figures in `AUTHOR` come from sessions
// with a 1M window, and on a 200k one Claude Code compacts before the context
// reaches FLOOR.
const WINDOW = 1_000_000

// How long a session sits idle before it is asked, by the lifetime of the
// prompt cache its last turn wrote to: inside that lifetime, so the question
// reads the context from the cache, and its read keeps the cache alive for the
// summary after it. On a five-minute cache that leaves a minute of margin, the
// lateness LATE_MS allows. Of my turn ends followed by more than 4 minutes
// idle, 95% ran past 5, so the question seldom comes just before the user
// returns to a warm cache.
const IDLE_MS = { '1h': 55 * 60_000, '5m': 4 * 60_000 }

// A wait that ends later than this after it was due slept with the machine,
// and the prompt cache lapsed meanwhile, so asking would pay to write the
// whole context to the cache again. Claude Code's own idle compaction gives up
// at the same point. A mock clock can't hold a wait while its time moves on, so
// no test reaches this.
const LATE_MS = 60_000

// The share of the fork's input not read from the prompt cache past which the
// question missed it: a hit leaves only the question uncached and a miss most
// of the context, so any share between separates them.
const MISSED = 0.1

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
      $.clock.after(0, () => void afterTurn($, watch, changes, e.usage))
    }
    return result
  })
}

async function afterTurn($: EngineInterface, watch: Watch, changes: number, usage?: TurnUsage) {
  // A line about a conversation that has moved on would land inside the turn
  // now running, so it goes to the debug log.
  const isCurrent = () => watch.changes === changes
  const say = (text: string) => $.ui.log(text, isCurrent() ? undefined : { to: 'debug' })
  try {
    const { context } = await $.session.usage()
    const tokens = context.tokens
    if (tokens === undefined || context.window < WINDOW || !isCurrent()) return
    const figures = await fitted($)

    // An idle session is asked after a turn whose cache writes all had one
    // lifetime, an hour or five minutes, once it has sat idle for the wait
    // IDLE_MS sets for that lifetime. Claude Code caches for an hour on a
    // subscription, and on an API key or Bedrock where the user opts in;
    // otherwise, under usage credits and behind gateways, it caches for five
    // minutes. A turn that wrote nothing to the cache, wrote for both
    // lifetimes, or whose provider names no lifetime, leaves the session
    // alone. The wait starts at the turn's end, whatever the judge does
    // meanwhile.
    const hour = usage?.ephemeral_1h_input_tokens ?? 0
    const fiveMinutes = usage?.ephemeral_5m_input_tokens ?? 0
    const wait = hour > 0 && fiveMinutes === 0 ? IDLE_MS['1h'] : fiveMinutes > 0 && hour === 0 ? IDLE_MS['5m'] : undefined
    if (tokens >= figures.IDLE_FLOOR && wait !== undefined) {
      const due = (await $.clock.now()) + wait
      $.clock.after(wait, () => void idle($, isCurrent, say, tokens, due, wait / 60_000))
    }
    if (tokens < figures.FLOOR) return
    const prompt = question(tokens, undefined, figures)
    await judge($, say, async () => isCurrent(), `${thousands(tokens)} tokens`, prompt)
  } catch (error) {
    say(`Failed: ${error}`)
  }
}

// Asks a session nobody has touched since the turn that set this timer again,
// now that its prompt cache is about to lapse: no turn has begun since, and
// the context is the size it was then. After any compaction, the judge's or
// Claude Code's own idle one, the context has no size until the next request.
// `minutes` is how long it has sat idle.
async function idle(
  $: EngineInterface,
  isCurrent: () => boolean,
  say: (text: string) => void,
  tokens: number,
  due: number,
  minutes: number,
) {
  const isUntouched = async () => isCurrent() && (await $.session.usage()).context.tokens === tokens
  try {
    if ((await $.clock.now()) > due + LATE_MS || !(await isUntouched())) return
    const prompt = question(tokens, minutes, await fitted($))
    await judge($, say, isUntouched, `${thousands(tokens)} tokens, idle ${minutes} minutes`, prompt)
  } catch (error) {
    say(`Failed: ${error}`)
  }
}

// Asks the session's model whether to compact, and compacts if it says to,
// saying too when the question missed the prompt cache. The verdict stands
// only while `stands` holds once the model has answered; `at` names the moment
// in each line.
async function judge(
  $: EngineInterface,
  say: (text: string) => void,
  stands: () => Promise<boolean>,
  at: string,
  prompt: string,
) {
  const answer = await $.model.fork({ prompt })
  // A line of its own, so the verdict's line, which model/fit.py parses, keeps
  // its reason clean. A fork that made no request reports no usage.
  if ('usage' in answer) {
    const { usage } = answer
    const read = usage.cache_read_input_tokens
    const input = usage.input_tokens + read + usage.cache_creation_input_tokens
    if (input - read > MISSED * input) say(`Missed the prompt cache at ${at}: read ${thousands(read)} of ${thousands(input)} from it`)
  }
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

// The question opens with its goal, then lists what a compaction, keeping the
// context and lost detail each cost. Asked of an idle session, it says the
// prompt cache is about to lapse, so keeping costs the cache write that
// follows. README.md shows both versions verbatim with the figures in `AUTHOR`,
// for a 500k context.
//
// The fork replays the turn's last request, which ends before the reply the
// turn closed with, and the question doesn't quote the reply: the tool calls
// and results before it show where the work stands, and in the evals quoting it
// made no measurable difference to the verdicts. The answer gives what most
// likely comes next and whether that needs the transcript's detail before
// `compact`, though the hook reads only `compact` and `reason`. In the evals,
// questions with those two fields kept the context in 13 of 56 runs at the four
// mid-task turn ends, and questions without them in 2 of 88, among them ones
// that ask for `reason` first or ask the model to consider what comes next.
function question(tokens: number, idleMinutes?: number, figures: Figures = AUTHOR) {
  const { WRITE, OUTPUT, SUMMARY, K, P, TURN_MEDIAN, TURN_MEAN } = figures
  const now = thousands(tokens)
  const isIdle = idleMinutes !== undefined
  const goal = isIdle
    ? 'The goal of this request is to decide whether to compact this session now, before its prompt cache lapses.'
    : 'The goal of this request is to decide whether to compact this session now.'
  const opening = isIdle
    ? `Your turn ended ${idleMinutes} minutes ago, and nothing has happened since.`
    : 'Your turn just ended.'
  // Idle, the cache is about to go whatever the answer, so a compaction needs
  // no payback sum, and keeping costs the one cache write the lapse brings.
  const sum = isIdle ? '' : `\n- At ${now} a compaction pays for itself after about ${payback(tokens, figures)} more requests that need nothing it dropped.`
  const keeping = isIdle
    ? `What keeping costs now:
- The prompt cache holding the conversation lapses within a few minutes.
- The first request after that makes a cache write of all but ${thousands(P)} of its ${now} tokens, against about ${thousands(K - P)} after a compaction.

So compact unless the next turn continues work that needs the transcript's detail.`
    : `What keeping costs, more the larger the context:
- Every later request reads all ${now} tokens, against about ${thousands(K)} after a compaction.
- Keeping only puts the compaction off to a later turn end.
- A turn runs a median of ${Math.round(TURN_MEDIAN)} requests and a mean of ${Math.round(TURN_MEAN)}, so keeping ${now} through one more turn costs as much as about ${turnCost(tokens, TURN_MEDIAN, figures)} compactions for a median turn and ${turnCost(tokens, TURN_MEAN, figures)} for a mean one.`
  return `${goal} It comes from the sparkjoy plugin, not from the user, and a program reads your answer. Use no tools.

${opening}

Nearly every long transcript is mostly finished work, so that alone is no reason to compact. What decides it is whether the next turns will need detail a summary drops: exact output, what was tried and failed, what the user objected to and why, the state of a design or fix still taking shape. Work that iterates with the user on something just shown, or takes up a half-done fix or design, needs that detail; after a compaction it re-reads and re-runs what it can and loses the rest. Work that only reports a result, or starts something new, needs no more than a summary.

What a compaction costs:
- It takes about a minute, and the request that writes its summary outputs about ${thousands(SUMMARY)} tokens, thinking included.
- Afterwards the context holds about ${thousands(K)} tokens, counting the files the next requests read again. Those requests make a cache write of all of it but the ${thousands(P)} of system prompt and tools, which stay cached.
- A cache write costs as much as ${WRITE} cached reads, and an output token as much as ${OUTPUT}.${sum}

${keeping}

What lost detail costs:
- It's very noisy and hard to judge, but as some indication, past sessions had about ${WASTED} more wasted requests than usual after a compaction: failed tool calls, repeated lookups and corrections.
- That count misses detail lost without such a failure, such as a design's rejected options or what the user likes.

Weigh what keeping costs against what losing the detail would cost the next turns. The larger the context, the more detail a compaction can drop and still pay.

Answer with one JSON object and nothing else, filling its fields in order:
{"next": "what most likely comes next, in a few words", "needs_detail": true or false, "compact": true or false, "reason": "one short line saying why"}`
}

// How many later requests a compaction at `tokens` takes to pay for itself:
// its cost over what each later request saves.
function payback(tokens: number, figures: Figures) {
  return Math.ceil(compaction(tokens, figures) / saving(tokens, figures))
}

// What keeping `tokens` through a turn of `requests` costs, in compactions:
// what its requests cost beyond what they would after one.
function turnCost(tokens: number, requests: number, figures: Figures) {
  const n = (requests * saving(tokens, figures)) / compaction(tokens, figures)
  return n < 1.5 ? n.toFixed(1) : `${Math.round(n)}`
}

// What a compaction at `tokens` costs, in cached reads, F(x) = x + F0: the
// compaction request reads the context and outputs the summary, then the
// requests after it make a cache write of K but P. A cache write costs
// WRITE - 1 more than the read it stands in for.
function compaction(tokens: number, { WRITE, OUTPUT, SUMMARY, K, P }: Figures) {
  return tokens + OUTPUT * SUMMARY + (WRITE - 1) * (K - P)
}

// What each request after a compaction at `tokens` saves, in cached reads: it
// reads tokens - K less, and a token held costs h = 1 + ℓ(w - 1) reads a
// request, counting the lapses after which it takes a cache write again.
function saving(tokens: number, { WRITE, K, LAPSE }: Figures) {
  return (1 + LAPSE * (WRITE - 1)) * (tokens - K)
}

// The floors and figures `/sparkjoy:report` last saved. Each is `AUTHOR`'s
// where the file lacks it or holds a value the question's sums can't use: a
// floor outside the window, a cache write no dearer than a read, a compaction
// that leaves no more than the cached system prompt and tools or at least the
// FLOOR the question is asked from at a turn end, a lapse share outside 0 to
// 1, or a turn of under one request. Read for each question, so a report run mid-session
// counts from the next one; a configuration directory or file that can't be
// found or read leaves every figure at `AUTHOR`'s. The directory is
// $CLAUDE_CONFIG_DIR, or ~/.claude, as Claude Code and model/fit.py find it.
async function fitted($: EngineInterface): Promise<Figures> {
  let saved: Record<string, unknown> | undefined
  try {
    const home = await $.env.get('HOME')
    const config = (await $.env.get('CLAUDE_CONFIG_DIR')) || (home && `${home}/.claude`)
    if (config) saved = JSON.parse(await $.fs.read(`${config}/${FIGURES}`))
  } catch {
    return AUTHOR
  }
  const figures = { ...AUTHOR }
  // FLOOR before K, and K before P, so each is held below the one before.
  const take = (name: keyof Figures, isValid: (value: number) => boolean) => {
    const value = saved?.[name]
    if (typeof value === 'number' && Number.isFinite(value) && isValid(value)) figures[name] = value
  }
  take('FLOOR', v => v > 0 && v < WINDOW)
  take('IDLE_FLOOR', v => v > 0 && v < WINDOW)
  take('WRITE', v => v > 1)
  take('OUTPUT', v => v > 0)
  take('SUMMARY', v => v > 0)
  take('K', v => v > 0 && v < figures.FLOOR)
  take('P', v => v >= 0 && v < figures.K)
  take('LAPSE', v => v >= 0 && v <= 1)
  take('TURN_MEDIAN', v => v >= 1)
  take('TURN_MEAN', v => v >= 1)
  return figures
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
