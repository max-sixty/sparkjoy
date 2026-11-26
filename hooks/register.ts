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
// whole context to the cache again. From CEILING the mod compacts without
// asking, since the judge keeps a context the work ahead needs at any size.
// Each decision shows in the transcript as a dim line the model never reads,
// so a session that stays large says why. The engine's auto-compaction at its
// threshold still runs as before, and covers a turn that grows past the floor
// before it ends.
//
// The judge is the session's model over the whole transcript rather than a
// small model over the last reply: which parts the work ahead still needs
// depends on the work, and the fork reads it from cache. The question gives
// the judge what a compaction would save at the context it holds and what a
// summary drops, and never names the floor. Its figures are the user's own
// once `/sparkjoy report` has fitted them. The mod runs only where a person is
// at the prompt: a `-p` run or an SDK host keeps the engine's own compaction, so
// no script pays for a judge or finds its conversation changed.
//
// The user's settings (`userConfig` in .claude-plugin/plugin.json) set the mod's
// mode, the floors it asks from, and instructions of their own for the
// question. In the mode `classify` the mod asks as it does when on and shows
// each verdict, but compacts nothing, so a user can watch what it would do
// before letting it act. `/sparkjoy` shows the settings, sets the mode, runs
// the report, and drafts a bug report, which the line after any bug the mod
// logs suggests.
//
// A compaction the mod announces still happens if the process ends before it
// finishes. Claude Code's background daemon restarts a session's process when
// the session looks idle and runs an older version than the daemon, as every
// background session does after an update. A busy session first looks idle at
// the end of a turn, which is when the mod compacts. So the mod keeps the
// announcement in `$.store` until the compaction settles, and the next process
// finishes it if the conversation hasn't moved on.

import type {
  CommandRunResult,
  EngineInterface,
  PluginOptions,
  Register,
  SessionMessage,
  TurnUsage,
} from 'claude-code'
import { checkForUpdate } from './update'

// Set while this process compacts. `$.state` outlives a reload of the mod but
// not the process, so a reload that finds a compaction announced leaves it to
// the copy already making it.
const COMPACTING = { plugin: 'sparkjoy', key: 'compacting' } as const

// The floors sparkjoy asks from and the figures the question's sums use, as my
// sessions of 7-15 Nov 2026 measured them (docs/cost-model.md); FLOOR's
// comment gives what my sessions of 18-22 Nov 2026 say of the floors.
// `/sparkjoy report` fits the same figures to the user's own sessions and
// saves them (`FIGURES`), and sparkjoy uses those instead (`fitted`), falling
// back to these figure by figure. A floor the user set in their settings wins
// over both. The costs follow that page's model and its table of
// symbols ("Costs"), which are those of `terms()` in
// model/fit.py, and each key is the name model/report.py saves its figure
// under: a figure whose meaning changes takes a new name, so a file an older
// report saved falls back for it.
const AUTHOR = {
  // The judge is asked at a turn end only from FLOOR context tokens, and in
  // an idle session from IDLE_FLOOR. In docs/cost-model.md's replay of my
  // sessions of 18-22 Nov 2026 ("Floors"), the pair that costs least counting cost alone is
  // 250k and 150k; this one saves as much at the model's base case for β,
  // 0.4 points short of the most efficient pair. The pair is fitted by cost
  // alone, since the question decides above it. The report finds the pair
  // that minimizes the cost of the user's sessions (`floors` in
  // model/fit.py). Compacting just before the cache lapses pays from about
  // 155k on a one-hour cache and 166k on a five-minute one, counting only the
  // next request, whose cache write it
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
  // The median requests in a turn.
  TURN_MEDIAN: 9,
}

type Figures = typeof AUTHOR

// The file `/sparkjoy report` saves the user's figures to, under Claude Code's
// configuration directory, as model/report.py names it. Its home would be the
// plugin's data folder, but Claude Code before 2.1.326 gives a hooks module no
// way to find that folder, and refuses at load a module that calls
// `$.process.hookEnv()`, the API that names it.
// TODO: save and read it in that folder once every release CI tests has
// `$.process.hookEnv()`.
const FIGURES = 'sparkjoy/figures.json'

// The user's settings, from the fields `userConfig` declares in
// .claude-plugin/plugin.json. Off, the mod asks nothing and compacts nothing,
// and `/sparkjoy` still answers, so it can turn the mod back on. Claude Code
// passes a number or text field left unset as "", which reads here as no floor
// and no instructions, and a mode outside the field's options as unset, which
// reads here as on.
type Mode = 'on' | 'classify' | 'off'
type Settings = { mode: Mode; floor?: number; idleFloor?: number; instructions: string }

function settingsOf(options: PluginOptions): Settings {
  const number = (value: unknown) => (typeof value === 'number' ? value : undefined)
  return {
    mode: options.mode === 'classify' || options.mode === 'off' ? options.mode : 'on',
    floor: number(options.floor),
    idleFloor: number(options.idle_floor),
    instructions:
      typeof options.instructions === 'string' ? options.instructions.trim() : '',
  }
}

// Where each floor comes from, in the words `/sparkjoy` uses.
const SOURCES = {
  setting: 'your setting',
  report: "the report's fit",
  author: 'the default',
}

type Source = keyof typeof SOURCES

// `/sparkjoy`, registered at each session's start whether the mod is on or off.
const COMMAND = {
  name: 'sparkjoy',
  description:
    "Show sparkjoy's settings, set its mode (on, classify or off), open its report, or draft a bug report",
  argumentHint: '[on|classify|off|report|bug]',
}

// What `/sparkjoy` says of each mode: on setting it, as the word for it when a
// setting is refused, and in its status, given the floors it asks from.
const MODES: Record<
  Mode,
  { set: string; state: string; status: (floors: string) => string }
> = {
  on: {
    set: 'On from the next turn end.',
    state: 'on',
    status: floors => `On. It asks whether to compact ${floors}.`,
  },
  classify: {
    set: 'Classifying from the next turn end: it shows each verdict and compacts nothing until /sparkjoy on.',
    state: 'classifying',
    status: floors =>
      `Classifying. It asks whether to compact ${floors}, and shows each verdict without compacting.`,
  },
  off: {
    set: 'Off. It asks nothing and compacts nothing until /sparkjoy on.',
    state: 'off',
    status: floors =>
      `Off, so it asks nothing and compacts nothing. When on, it asks ${floors}.`,
  },
}

// The mod acts only in a session whose model has a context window of at least
// this many tokens. The floors and figures in `AUTHOR` come from sessions
// with a 1M window, and on a 200k one Claude Code compacts before the context
// reaches FLOOR.
const WINDOW = 1_000_000

// From this many context tokens the mod compacts at a turn end, or before an
// idle session's cache lapses, without asking. The judge keeps the context
// wherever the work ahead needs its detail, whatever the question says of the
// context's size, its cost or how soon Claude Code compacts on its own
// (evals/README.md, "Size"), so a long task would otherwise run on until
// Claude Code compacts it in the middle of a turn, near 965k. A turn that
// ends below CEILING can still grow that far: of three such compactions in my
// sessions of 25 Oct-25 Nov 2026, this would have come first at two. In
// docs/cost-model.md's replay of sessions judged turn end by turn end ("Work
// that differs"), work that needs its history costs least compacted from
// 900k, and other work from about 280k.
const CEILING = 900_000

// The verdict past CEILING.
const PAST_CEILING: Verdict = {
  compact: true,
  reason: `past ${thousands(CEILING)}, where even work that needs the detail compacts`,
}

// How long a session sits idle before it is asked, by the lifetime of the
// prompt cache its last turn wrote to: inside that lifetime, so the question
// reads the context from the cache, and its read keeps the cache alive for the
// summary after it. On a five-minute cache that leaves a minute of margin, the
// lateness LATE_MS allows. Of my turn ends followed by more than 4 minutes
// idle, 95% ran past 5, so the question seldom comes just before the user
// returns to a warm cache.
//
// The cache belongs to the account's organization, so once Claude Code signs
// in to another account the question finds it gone and pays to write the
// context to it again, as the user's next request would have. All 3 of my
// idle questions that missed it (21-22 Nov 2026) followed such a switch.
// Otherwise the API keeps it for the hour: of my requests 5 to 60 minutes
// after one they extended unchanged, on the same account, 11 of 2,392 found
// it gone (1-22 Nov 2026).
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

// What the line after one reporting a bug, in sparkjoy or in the Claude Code
// it runs on, says.
const BUG = 'That looks like a bug: /sparkjoy bug drafts a report of it.'

type Verdict = { compact: boolean; reason: string }

type Watch = {
  // Whether the mod asks in this session: it is on or classifying, and a
  // person is at the prompt.
  isActive: boolean
  // Counts what extended or replaced the main conversation: a turn begun, a
  // `/compact`, a /clear or resume. A verdict stands only while the count is
  // the one its turn ended at.
  changes: number
}

export const register: Register = (on, options) => {
  const settings = settingsOf(options)
  const watch: Watch = { isActive: false, changes: 0 }

  on('session.start', async ($, e, next) => {
    watch.isActive = settings.mode !== 'off' && e.isInteractive
    const result = await next(e)
    if (watch.isActive && settings.mode === 'on') $.clock.after(0, () => void resume($))
    if (e.isInteractive) $.clock.after(0, () => void checkForUpdate($))
    await $.command.register(COMMAND)
    return result
  })

  on('command.run', { command: 'sparkjoy' }, ($, e) => command($, e.args, settings))

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
    if (watch.isActive && e.agentId === undefined && e.reason === 'answer') {
      $.clock.after(0, () => void afterTurn($, settings, watch, changes, e.usage))
    }
    return result
  })
}

async function afterTurn(
  $: EngineInterface,
  settings: Settings,
  watch: Watch,
  changes: number,
  usage?: TurnUsage,
) {
  // A line about a conversation that has moved on would land inside the turn
  // now running, so it goes to the debug log.
  const isCurrent = () => watch.changes === changes
  const say = (text: string) =>
    $.ui.log(text, isCurrent() ? undefined : { to: 'debug' })
  try {
    const { context } = await $.session.usage()
    const tokens = context.tokens
    if (tokens === undefined || context.window < WINDOW || !isCurrent()) return
    const { figures } = await fitted($, settings)

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
    const wait =
      hour > 0 && fiveMinutes === 0
        ? IDLE_MS['1h']
        : fiveMinutes > 0 && hour === 0
          ? IDLE_MS['5m']
          : undefined
    if (tokens >= figures.IDLE_FLOOR && wait !== undefined) {
      const due = (await $.clock.now()) + wait
      $.clock.after(
        wait,
        () => void idle($, settings, isCurrent, say, tokens, due, wait / 60_000),
      )
    }
    if (tokens < figures.FLOOR) return
    const at = `${thousands(tokens)} tokens`
    if (tokens >= CEILING) {
      if (isCurrent()) await act($, settings, say, at, PAST_CEILING)
      return
    }
    const prompt = question(tokens, undefined, figures, settings.instructions)
    // A turn that wrote to the cache left it seconds old, so a question that
    // misses it had a prompt that differed from the turn's, which is a bug. A
    // turn that wrote nothing left nothing to read.
    const sayMiss =
      (usage?.cache_creation_input_tokens ?? 0) > 0
        ? (line: string) => sayBug(say, line)
        : say
    await judge($, settings, say, async () => isCurrent(), at, prompt, sayMiss)
  } catch (error) {
    sayBug(say, `Failed: ${error}`)
  }
}

// Asks a session nobody has touched since the turn that set this timer again,
// now that its prompt cache is about to lapse: no turn has begun since, and
// the context is the size it was then. After any compaction, the judge's or
// Claude Code's own idle one, the context has no size until the next request.
// `minutes` is how long it has sat idle.
async function idle(
  $: EngineInterface,
  settings: Settings,
  isCurrent: () => boolean,
  say: (text: string) => void,
  tokens: number,
  due: number,
  minutes: number,
) {
  const isUntouched = async () =>
    isCurrent() && (await $.session.usage()).context.tokens === tokens
  try {
    if ((await $.clock.now()) > due + LATE_MS || !(await isUntouched())) return
    const at = `${thousands(tokens)} tokens, idle ${minutes} minutes`
    if (tokens >= CEILING) return await act($, settings, say, at, PAST_CEILING)
    const prompt = question(
      tokens,
      minutes,
      (await fitted($, settings)).figures,
      settings.instructions,
    )
    // Claude Code may have signed in to another account meanwhile (IDLE_MS),
    // or, as the wait runs from the turn's end, a last request that ran long
    // may have used up the five-minute cache's margin.
    await judge($, settings, say, isUntouched, at, prompt, line =>
      say(`${line}. Signing in to another account while idle drops the cache.`),
    )
  } catch (error) {
    sayBug(say, `Failed: ${error}`)
  }
}

// Asks the session's model whether to compact, and compacts if it says to and
// the mod is on, saying too, with `sayMiss`, when the question missed the
// prompt cache. The verdict stands only while `stands` holds once the model
// has answered; `at` names the moment in each line.
async function judge(
  $: EngineInterface,
  settings: Settings,
  say: (text: string) => void,
  stands: () => Promise<boolean>,
  at: string,
  prompt: string,
  sayMiss: (text: string) => void,
) {
  const answer = await $.model.fork({ prompt })
  // A line of its own, so the verdict's line, which model/fit.py parses, keeps
  // its reason clean. A fork that made no request reports no usage.
  if ('usage' in answer) {
    const { usage } = answer
    const read = usage.cache_read_input_tokens
    const input = usage.input_tokens + read + usage.cache_creation_input_tokens
    if (input - read > MISSED * input)
      sayMiss(
        `Missed the prompt cache at ${at}: read ${thousands(read)} of ${thousands(input)} from it`,
      )
  }
  if (!(await stands()))
    return say(`Dropped the verdict at ${at}: the conversation moved on`)
  const verdict = answer.isAnswered ? parse(answer.text) : undefined
  if (verdict) {
    await act($, settings, say, at, verdict)
  } else if (answer.isAnswered) {
    sayBug(say, `Unreadable verdict at ${at}: ${answer.text}`)
  } else {
    say(`No verdict at ${at}: ${answer.reason}`)
  }
}

// Acts on a verdict: compacts if it says to and the mod is on, and says so.
async function act(
  $: EngineInterface,
  { mode }: Settings,
  say: (text: string) => void,
  at: string,
  verdict: Verdict,
) {
  if (verdict.compact && mode === 'classify') {
    say(`Would compact at ${at}: ${verdict.reason}`)
  } else if (verdict.compact) {
    // `$.session.compact` draws no `/compact` row, so neither the user nor
    // the model reads the compaction as one the user typed. A hook's veto
    // draws its own line.
    say(`Compacting at ${at}: ${verdict.reason}`)
    await compact($, say, at)
  } else {
    say(`Kept at ${at}: ${verdict.reason}`)
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
//
// The engine rejects a compaction while a turn runs, and a turn can begin
// after the caller checked that none had: a queued notification starts one
// some tenths of a second before its `turn.start` reaches this mod. That is no
// bug, since the next turn end asks again, so `say` gives the engine's reason
// without calling it one.
async function compact($: EngineInterface, say: (text: string) => void, at: string) {
  const key = `compacting:${await $.session.id()}`
  await $.state.set(COMPACTING, true)
  try {
    const announcement: Announcement = { at, messages: await messageKeys($) }
    await $.store.set(key, announcement)
    await $.session.compact().catch(error => say(`Didn't compact at ${at}: ${error}`))
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
    if (now.length === 0 || !now.every(message => held.has(message)))
      return await $.store.delete(key)
    $.ui.log(
      `Finishing the compaction at ${announced.at}: Claude Code restarted before it landed`,
    )
    await compact($, text => $.ui.log(text), announced.at)
  } catch (error) {
    sayBug(text => $.ui.log(text), `Failed: ${error}`)
  }
}

// Says `text`, a line reporting a bug, and how to report it.
function sayBug(say: (text: string) => void, text: string) {
  say(text)
  say(BUG)
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
  const calls = [...message.toolUses, ...(message.toolResults ?? [])].map(
    call => call.tool_use_id,
  )
  return calls.length > 0
    ? `${message.role} ${calls.join(' ')}`
    : hash(`${message.role} ${message.text}`)
}

// FNV-1a. At 32 bits, a new message takes the key of one of 4096 held about
// once in a million.
function hash(text: string) {
  let h = 0x811c9dc5
  for (let i = 0; i < text.length; i++)
    h = Math.imul(h ^ text.charCodeAt(i), 0x01000193)
  return (h >>> 0).toString(36)
}

// The question gives the context's size and what a compaction would save,
// summed into one figure, the requests it takes to pay for itself, and says
// keeping only puts the compaction off; then what a summary drops, and which
// work needs that; then asks the model to weigh the two. Asked of an idle
// session, it says the prompt cache is about to lapse instead, so keeping
// costs the cache write that follows. README.md shows both
// versions with the figures in `AUTHOR`, for a 500k context, and
// `$CUSTOM_INSTRUCTIONS` where the user's `instructions` add a paragraph of
// their own. They change nothing else, so the question keeps its figures and
// the format the hook parses whatever they say.
//
// An earlier question stated every figure behind the sum, and the cost of each
// side in its own list. Asked as the fork asks it, at the turn ends in the
// middle of a task it compacted in all 16 runs, though in 12 it answered that
// the next turns would need the detail. This one keeps there in 22 of 24, and
// its verdict follows that answer whatever it says of the context's size
// (evals/README.md, "Size"), which is why CEILING exists.
// The fork sends the turn's last request with the turn's reply after it, so
// the question needn't quote the reply. The answer gives what most likely
// comes next and whether that needs the transcript's detail before `compact`,
// though the hook reads only `compact` and `reason` (evals/README.md, "Fields
// the hook doesn't read").
function question(
  tokens: number,
  idleMinutes?: number,
  figures: Figures = AUTHOR,
  instructions = '',
) {
  const { K, P, TURN_MEDIAN } = figures
  const now = thousands(tokens)
  const isIdle = idleMinutes !== undefined
  const goal = isIdle
    ? 'Decide whether to compact this session now, before its prompt cache lapses.'
    : 'Decide whether to compact this session now.'
  const requests = payback(tokens, figures)
  const situation = isIdle
    ? `Your turn ended ${idleMinutes} minutes ago, and nothing has happened since. The context holds ${now} tokens, and the prompt cache holding it lapses within a few minutes, so the next request will write all but ${thousands(P)} of it to the cache again, against about ${thousands(K - P)} after a compaction.`
    : `Your turn just ended, with ${now} tokens of context. A compaction would cut it to about ${thousands(K)}, and pays for itself once the session runs ${requests} more requests that need nothing it dropped (a turn runs a median of ${Math.round(TURN_MEDIAN)}). Keeping only puts the compaction off: Claude Code compacts on its own, in the middle of a turn, once the context nears its limit.`
  const decide = isIdle
    ? 'Compact unless the next turn continues work that needs that detail.'
    : 'Weigh the saving against what losing that detail would cost the next turns.'
  const added = instructions
    ? `\n\nThe user added these instructions for this decision:\n${instructions}`
    : ''
  return `${goal} This request comes from the sparkjoy plugin, not from the user, and a program reads your answer.

${situation}

A summary drops detail: exact output, what was tried and failed, what the user objected to and why, the state of a design or fix still taking shape. Work that iterates with the user on something just shown, or takes up a half-done fix or design, needs that detail; after a compaction it re-reads and re-runs what it can and loses the rest. Work that only reports a result, or starts something new, needs no more than a summary. Nearly every long transcript is mostly finished work, so that alone is no reason to compact.

${decide}${added}

Answer with nothing but one JSON object that matches this schema, filling its fields in the order listed:

\`\`\`json
{
  "type": "object",
  "properties": {
    "next": {"type": "string", "description": "what most likely comes next, in a few words"},
    "needs_detail": {"type": "boolean", "description": "whether the next turns will need detail a summary drops"},
    "compact": {"type": "boolean", "description": "whether to compact now"},
    "reason": {"type": "string", "description": "one short line saying why"}
  },
  "required": ["next", "needs_detail", "compact", "reason"],
  "additionalProperties": false
}
\`\`\``
}

// How many later requests a compaction at `tokens` takes to pay for itself:
// its cost over what each later request saves.
function payback(tokens: number, figures: Figures) {
  return Math.ceil(compaction(tokens, figures) / saving(tokens, figures))
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

// The floors and figures sparkjoy uses, and where each floor comes from. A
// floor the user set wins over the one `/sparkjoy report` last saved, and each
// figure the report saved wins over `AUTHOR`'s. A source whose value the
// question's sums can't use gives way to the next: a floor from CEILING up,
// a cache write no dearer than a read, a context after a compaction no larger
// than the cached system prompt and tools or at least the FLOOR the question
// is asked from at a turn end, a lapse share outside 0 to 1, or a turn of under
// one request. The user's floor at a turn end must also lie above the context
// after a compaction, where a compaction can pay for itself.
// Read for each question, so a report run mid-session counts from the next one;
// a configuration directory or file that can't be found or read leaves every
// figure but the user's floors at `AUTHOR`'s. The directory is
// $CLAUDE_CONFIG_DIR, or ~/.claude, as Claude Code and model/fit.py find it:
// Windows names the home %USERPROFILE% and sets no HOME.
async function fitted($: EngineInterface, settings: Settings) {
  let saved: Record<string, unknown> | undefined
  try {
    const home = (await $.env.get('HOME')) || (await $.env.get('USERPROFILE'))
    const config = (await $.env.get('CLAUDE_CONFIG_DIR')) || (home && `${home}/.claude`)
    if (config) saved = JSON.parse(await $.fs.read(`${config}/${FIGURES}`))
  } catch {
    saved = undefined
  }
  const sources = {
    report: saved,
    setting: { FLOOR: settings.floor, IDLE_FLOOR: settings.idleFloor } as Record<
      string,
      unknown
    >,
  }
  const value = (
    name: keyof Figures,
    isValid: (value: number) => boolean,
    source: 'report' | 'setting',
  ) => {
    const v = sources[source]?.[name]
    return typeof v === 'number' && Number.isFinite(v) && isValid(v) ? v : undefined
  }
  const figures = { ...AUTHOR }
  const from: Record<'FLOOR' | 'IDLE_FLOOR', Source> = {
    FLOOR: 'author',
    IDLE_FLOOR: 'author',
  }
  const take = (
    name: keyof Figures,
    isValid: (value: number) => boolean,
    source: 'report' | 'setting' = 'report',
  ) => {
    const v = value(name, isValid, source)
    if (v === undefined) return
    figures[name] = v
    if (name === 'FLOOR' || name === 'IDLE_FLOOR') from[name] = source
  }
  // A floor from CEILING up would leave the mod nothing to ask.
  const belowCeiling = (v: number) => v > 0 && v < CEILING
  const below = (floor: number) => (v: number) => v > 0 && v < floor
  // The K the question states with the turn-end floor `floor`.
  const contextAfter = (floor: number) => value('K', below(floor), 'report') ?? AUTHOR.K
  // FLOOR before K, and K before P, so each is held below the one before; the
  // user's FLOOR is held above its K.
  take('FLOOR', belowCeiling)
  take('FLOOR', v => belowCeiling(v) && v > contextAfter(v), 'setting')
  take('IDLE_FLOOR', belowCeiling)
  take('IDLE_FLOOR', belowCeiling, 'setting')
  take('WRITE', v => v > 1)
  take('OUTPUT', v => v > 0)
  take('SUMMARY', v => v > 0)
  take('K', below(figures.FLOOR))
  take('P', v => v >= 0 && v < figures.K)
  take('LAPSE', v => v >= 0 && v <= 1)
  take('TURN_MEDIAN', v => v >= 1)
  return { figures, from }
}

// Answers `/sparkjoy`. Bare, it says how the mod stands. `on`, `classify` and
// `off` set the mode, and Claude Code then reloads the mod with it. `report`
// and `bug` ask the model to run the skill of that name, which its frontmatter
// keeps out of the slash menu, so this is the skill's one entry there while
// the model still runs it when the user asks for it in their own words. The
// bug report's skill is told the session's id, which finds its transcript, and
// the settings `status` states, which no one file holds. A prompt submitted
// while the command's hook runs would wait on that hook, so it goes on a timer.
// Claude Code shows each answer after the plugin's name, `sparkjoy:`, so no
// answer names it again.
async function command(
  $: EngineInterface,
  args: string,
  settings: Settings,
): Promise<CommandRunResult> {
  const word = args.trim().split(/\s+/)[0]
  if (word === '') return { text: [...(await status($, settings)), HOW].join('\n') }
  if (word === 'on' || word === 'classify' || word === 'off') {
    // `$.config` names a plugin's field `<plugin>.<field>`.
    const { deny } = await $.config.set({ key: 'sparkjoy.mode', value: word })
    if (deny) return { text: `Still ${MODES[settings.mode].state}: ${deny}` }
    return { text: MODES[word].set }
  }
  if (word === 'report' || word === 'bug') {
    const context =
      word === 'bug'
        ? ` The session's id is ${await $.session.id()}. /sparkjoy states its settings as:\n${(await status($, settings, true)).map(line => `> ${line}`).join('\n')}`
        : ''
    const text = `The user typed /sparkjoy ${args.trim()}. Run the sparkjoy:${word} skill for them.${context}`
    $.clock.after(
      0,
      () => void $.prompt.submit({ text }).catch(error => $.ui.log(`Failed: ${error}`)),
    )
    return {
      text:
        word === 'bug'
          ? 'Asking the model to draft a bug report.'
          : 'Asking the model to run the report.',
    }
  }
  return {
    text: `/sparkjoy takes on, classify, off, report or bug, not "${word}", or nothing to show its settings.`,
  }
}

// What `/sparkjoy` says of the mod's settings, a line each: its mode and the
// floors it asks from, with where each comes from, whether this session's window
// leaves it nothing to ask, and whether the question carries the user's
// instructions, quoting their start or, `isWhole`, all of them. Bare,
// `/sparkjoy` adds HOW. A user whose sessions run on a smaller window would
// otherwise see the mod on and never see it act.
async function status($: EngineInterface, settings: Settings, isWhole = false) {
  const { figures, from } = await fitted($, settings)
  const { window } = (await $.session.usage()).context
  const windowLine =
    window < WINDOW
      ? [
          `This session's context window is ${thousands(window)}, and sparkjoy acts only in sessions with a 1M window, so it asks nothing here.`,
        ]
      : []
  const floor = (name: 'FLOOR' | 'IDLE_FLOOR', set: number | undefined) => {
    const ignored =
      set !== undefined && from[name] !== 'setting'
        ? `, as your ${thousands(set)} is out of range`
        : ''
    return `${thousands(figures[name])} (${SOURCES[from[name]]}${ignored})`
  }
  const floors = `at a turn end from ${floor('FLOOR', settings.floor)} and in an idle session from ${floor('IDLE_FLOOR', settings.idleFloor)}`
  const { mode, instructions } = settings
  // The first 60 characters, cut at a space.
  const excerpt =
    !isWhole && instructions.length > 60
      ? `${instructions.slice(0, 61).replace(/\s+\S*$/, '')}…`
      : instructions
  const ceilingLine =
    mode === 'off' || window < WINDOW
      ? []
      : [`From ${thousands(CEILING)} it doesn't ask: its verdict is to compact.`]
  return [
    MODES[mode].status(floors),
    ...ceilingLine,
    ...windowLine,
    instructions
      ? `Its question carries your instructions: "${excerpt}"`
      : 'Its question carries no instructions of yours.',
  ]
}

// How to change the settings, the last line of what `/sparkjoy` says bare.
const HOW =
  '/sparkjoy on compacts when the model says to, /sparkjoy classify only shows its verdicts, and /sparkjoy off asks nothing. /config sets the floors and instructions.'

function parse(text: string): Verdict | undefined {
  try {
    const value = JSON.parse(text.slice(text.indexOf('{'), text.lastIndexOf('}') + 1))
    const isVerdict =
      typeof value.compact === 'boolean' && typeof value.reason === 'string'
    return isVerdict ? value : undefined
  } catch {
    return undefined
  }
}

function thousands(tokens: number) {
  return `${Math.round(tokens / 1000)}k`
}
