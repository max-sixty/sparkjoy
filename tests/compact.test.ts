import type { ModelUsage, On, TurnUsage } from 'claude-code'
import { type Engine, expect, mock, test } from 'claude-code/testing'
import { messageKey } from '../hooks/register'

const USAGE = { input_tokens: 0, output_tokens: 0, cache_creation_input_tokens: 0, cache_read_input_tokens: 0 }
const COMPACT = '{"compact": true, "reason": "the PR merged"}'
const KEEP = '{"compact": false, "reason": "mid-debug"}'
const said = (text: string) => ({ role: 'user' as const, text, toolUses: [] })
const SUMMARY = { messages: [said('summary')] }
// The announcement of a compaction made while the conversation was the one
// message `m0`.
const ANNOUNCED = { 'compacting:s1': { at: '489k tokens', messages: [messageKey(said('m0'))] } }

// How long a session sits idle before it is asked, on a one-hour cache and on
// a five-minute one.
const IDLE = 55 * 60_000
const IDLE_5M = 4 * 60_000

// What a turn's requests wrote to the prompt cache: all of it for an hour, as
// on a subscription.
const HOUR = {
  ...USAGE,
  cache_creation_input_tokens: 3_000,
  ephemeral_5m_input_tokens: 0,
  ephemeral_1h_input_tokens: 3_000,
  model: 'claude-opus-5-5',
}

// All of it for five minutes, as on an API key.
const FIVE_MINUTES = { ...HOUR, ephemeral_5m_input_tokens: 3_000, ephemeral_1h_input_tokens: 0 }

// The user's home, and the figures file `/sparkjoy:report` saves in Claude
// Code's configuration directory there.
const HOME = '/home/u'
const FIGURES = `${HOME}/.claude/sparkjoy/figures.json`

// Stands for the engine beneath the mod: a context of `tokens` until `resize`
// changes it or a compaction leaves it unmeasured, the rate-limit windows
// `rateLimits` (a subscription's five-hour and weekly ones unless told
// otherwise), turns that end reporting `usage` (`HOUR` unless told
// otherwise), a fork that answers `verdict` after a turn and `idleVerdict`
// after an idle wait, each after `forkMs` (`null`: an empty reply) and
// reporting `forkUsage`,
// compactions that take `compactMs` and fail `compactFailures` times before
// one succeeds, a turn whose end settles after `endMs`, a store holding
// `stored` (what an earlier process left), a conversation of user messages
// with the texts `conversation`, which a turn adds a message to and a
// compaction its summary, a process environment of `env` (`HOME` unless told
// otherwise), a file at `figuresAt` holding `saved` (none when undefined),
// and a record of what it was asked.
function engine(
  on: On,
  {
    tokens = 500_000,
    window = 1_000_000,
    verdict = COMPACT as string | null,
    idleVerdict = COMPACT as string | null,
    forkMs = 0,
    forkUsage = USAGE as ModelUsage,
    endMs = 0,
    compactMs = 0,
    compactFailures = 0,
    stored = {} as Record<string, unknown>,
    conversation = ['m0'],
    rateLimits = [
      { kind: 'five_hour', percentUsed: 10 },
      { kind: 'seven_day', percentUsed: 30 },
    ],
    usage = HOUR as TurnUsage,
    env = { HOME } as Record<string, string>,
    figuresAt = FIGURES,
    saved = undefined as string | undefined,
  } = {},
) {
  const clock = mock.clock(on)
  let size: number | undefined = tokens
  let failures = compactFailures
  const messages = [...conversation]
  const forks: string[] = []
  // The instructions of each compaction the mod made, `''` for none.
  const compactions: string[] = []
  // Each slash command run.
  const commands: string[] = []
  const logs: string[] = []

  mock.store(on, stored)
  on('session.id', () => ({ value: 's1' }))
  on('env.get', (_, e) => ({ value: env[e.name] }))
  on('fs.read', (_, e) => {
    if (e.path !== figuresAt || saved === undefined) throw new Error(`ENOENT: no such file or directory, open '${e.path}'`)
    return { value: saved }
  })
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('turn.start', (_, e) => {
    messages.push(e.turnId)
    return { turnId: e.turnId }
  })
  on('session.messages', () => ({ value: messages.map(said) }))
  on('turn.complete', async (_, e) => {
    // A held wait resolves only on an advance, so a turn the test awaits
    // whole takes none.
    if (endMs > 0) await clock.sleep(endMs)
    return { text: e.answer }
  })
  on('session.end', (_, e) => ({ sessionId: e.sessionId }))
  on('ui.log', (_, e) => {
    if (e.to !== 'debug') logs.push(e.text)
    return { value: undefined }
  })
  on('session.usage', () => ({
    value: { startedAt: 0, rateLimits, context: { tokens: size, window } },
  }))
  on('model.fork', async (_, e) => {
    forks.push(e.prompt)
    await clock.sleep(forkMs)
    const answer = e.prompt.includes('minutes ago') ? idleVerdict : verdict
    if (answer === null) return { value: { isAnswered: false, reason: 'empty-reply', usage: forkUsage } }
    return { value: { isAnswered: true, text: answer, usage: forkUsage } }
  })
  // A plugin's `$.session.compact` reaches here as the plugin made it, with no
  // `trigger`; the engine stamps it `plugin`.
  on('session.compact', async (_, e) => {
    if (compactMs > 0) await clock.sleep(compactMs)
    if (failures-- > 0) throw new Error('summary unavailable')
    if (e.trigger === undefined || e.trigger === 'plugin') compactions.push(e.instructions ?? '')
    // Claude Code measures the context again only at the next request.
    size = undefined
    messages.push(`summary ${compactions.length}`)
    return SUMMARY
  })
  on('command.run', (_, e) => {
    commands.push(e.command)
    return { text: '' }
  })

  // Runs one main-loop turn to its answer and lets what it starts settle.
  async function turn($: Engine, turnId: string, answer = 'Merged #12. ✅') {
    await $.turn.start({ text: 'go', turnId })
    await $.turn.complete({ answer, durationMs: 1, isAborted: false, turnId, reason: 'answer', usage })
    await clock.settle()
  }

  // Starts the session the mod hears, under the REPL unless told otherwise.
  async function begin($: Engine, isInteractive = true) {
    await $.session.start({ cwd: '/', surface: isInteractive ? 'terminal' : null, isInteractive })
  }

  const resize = (to: number | undefined) => (size = to)

  // Saves a figures file, as the report does, or removes it.
  const save = (text: string | undefined) => (saved = text)

  // Runs a slash command as the person typing it at the prompt would.
  const type = ($: Engine, command: string) =>
    $.command.run({
      command,
      args: '',
      origin: { kind: 'composer' },
      presentation: { isFullscreen: false, columns: 80 },
    })

  return { clock, forks, compactions, commands, logs, begin, turn, resize, save, type }
}

// Figures the report fitted to a user on a five-minute cache, whose
// compactions leave less and write a longer summary than the author's.
const FITTED = JSON.stringify({
  WRITE: 25,
  OUTPUT: 100,
  SUMMARY: 8_000,
  K: 120_000,
  P: 20_000,
  LAPSE: 0.01,
  TURN_MEDIAN: 12,
  TURN_MEAN: 49.6,
})

// What the question says at 500k with the author's figures.
const AUTHORS = [
  'outputs about 6k tokens, thinking included.\n- Afterwards the context holds about 142k tokens',
  'all of it but the 30k of system prompt and tools, which stay cached.\n- A cache write costs as much as 40 cached reads, and an output token as much as 100.',
  'At 500k a compaction pays for itself after about 13 more requests',
  'A turn runs a median of 9 requests and a mean of 38,',
]

test('a finished turn compacts with no instructions and no /compact row', async ($, on) => {
  const { begin, forks, compactions, commands, logs, turn } = engine(on)
  await begin($)

  await turn($, 't1')

  expect(forks.length).toBe(1)
  expect(compactions).toEqual([''])
  expect(commands).toEqual([])
  expect(logs).toEqual(['Compacting at 500k tokens: the PR merged'])
})

test('the question sizes the saving for the context it asks about', async ($, on) => {
  const { begin, forks, turn, resize } = engine(on, { verdict: KEEP })
  await begin($)

  await turn($, 't1')
  resize(250_000)
  await turn($, 't2')

  // (500k read + 100 × 5.6k output + 39 × (142k − 30k) cache write) over the
  // 1.22 × (500k − 142k) each later request saves, rounded up.
  expect(forks[0]).toContain('At 500k a compaction pays for itself after about 13 more requests')
  expect(forks[1]).toContain('At 250k a compaction pays for itself after about 40 more requests')
  // 9 and 38 requests × that saving, over what a compaction costs.
  expect(forks[0]).toContain('keeping 500k through one more turn costs as much as about 0.7 compactions for a median turn and 3 for a mean one')
  expect(forks[1]).toContain('keeping 250k through one more turn costs as much as about 0.2 compactions for a median turn and 1.0 for a mean one')
})

test('the question states the figures the report fitted to the user’s sessions', async ($, on) => {
  const { begin, forks, turn } = engine(on, { verdict: KEEP, saved: FITTED })
  await begin($)

  await turn($, 't1')

  expect(forks[0]).toContain('outputs about 8k tokens, thinking included.\n- Afterwards the context holds about 120k tokens')
  expect(forks[0]).toContain('all of it but the 20k of system prompt and tools, which stay cached.\n- A cache write costs as much as 25 cached reads, and an output token as much as 100.')
  // (500k read + 100 × 8k output + 24 × (120k − 20k) cache write) over the
  // 1.24 × (500k − 120k) each later request saves, rounded up.
  expect(forks[0]).toContain('At 500k a compaction pays for itself after about 8 more requests')
  // 12 and 49.6 requests × that saving, over what a compaction costs: 1.53
  // and 6.3.
  expect(forks[0]).toContain('A turn runs a median of 12 requests and a mean of 50, so keeping 500k through one more turn costs as much as about 2 compactions for a median turn and 6 for a mean one')
})

test('an idle session’s question states the fitted figures too', async ($, on) => {
  const { begin, clock, forks, turn } = engine(on, { tokens: 200_000, saved: FITTED })
  await begin($)

  await turn($, 't1')
  await clock.advance(IDLE)
  await clock.settle()

  expect(forks[0]).toContain('makes a cache write of all but 20k of its 200k tokens, against about 100k after a compaction')
})

test('the figures are read from $CLAUDE_CONFIG_DIR where it is set', async ($, on) => {
  const env = { HOME, CLAUDE_CONFIG_DIR: '/work/claude' }
  const { begin, forks, turn } = engine(on, { verdict: KEEP, env, figuresAt: '/work/claude/sparkjoy/figures.json', saved: FITTED })
  await begin($)

  await turn($, 't1')

  expect(forks[0]).toContain('At 500k a compaction pays for itself after about 8 more requests')
})

test('the floors the report fitted decide where sparkjoy asks', async ($, on) => {
  const saved = JSON.stringify({ FLOOR: 150_000, IDLE_FLOOR: 120_000 })
  const { begin, clock, forks, turn, resize } = engine(on, { verdict: KEEP, tokens: 200_000, saved })
  await begin($)

  // Below the author's 250k at a turn end, above the fitted 150k.
  await turn($, 't1')
  expect(forks.length).toBe(1)

  // Below the fitted floor at a turn end, above the fitted idle floor.
  resize(130_000)
  await turn($, 't2')
  expect(forks.length).toBe(1)
  await clock.advance(IDLE)
  await clock.settle()
  expect(forks.length).toBe(2)
})

test('a report saved mid-session counts from the next question', async ($, on) => {
  const { begin, forks, turn, save } = engine(on, { verdict: KEEP })
  await begin($)

  await turn($, 't1')
  save(FITTED)
  await turn($, 't2')

  for (const text of AUTHORS) expect(forks[0]).toContain(text)
  expect(forks[1]).toContain('At 500k a compaction pays for itself after about 8 more requests')
})

test('each figure the file lacks, or holds where the sums can’t use it, stays the author’s', async ($, on) => {
  // K at the floor, where no compaction asked about at a turn end would pay;
  // P above the author's K; the summary as text; the median turn kept.
  const saved = JSON.stringify({ K: 250_000, P: 150_000, SUMMARY: '8k', LAPSE: null, TURN_MEDIAN: 12 })
  const { begin, forks, turn } = engine(on, { verdict: KEEP, saved })
  await begin($)

  await turn($, 't1')

  for (const text of AUTHORS.slice(0, 3)) expect(forks[0]).toContain(text)
  // 12 requests × the author's saving, over what a compaction costs: 0.96.
  expect(forks[0]).toContain('A turn runs a median of 12 requests and a mean of 38, so keeping 500k through one more turn costs as much as about 1.0 compactions for a median turn and 3 for a mean one')
})

for (const [name, options] of [
  ['a figures file that isn’t JSON', { saved: '{"K": 120' }],
  ['a figures file that holds no object', { saved: 'null' }],
  ['a process with no home or configuration directory', { saved: FITTED, env: {} }],
] as const) {
  test(`${name} leaves the question the author’s`, async ($, on) => {
    const { begin, forks, turn } = engine(on, { verdict: KEEP, ...options })
    await begin($)

    await turn($, 't1')

    for (const text of AUTHORS) expect(forks[0]).toContain(text)
  })
}

test('a verdict to keep leaves the conversation alone and says why', async ($, on) => {
  const { begin, forks, compactions, logs, turn } = engine(on, { verdict: KEEP })
  await begin($)

  await turn($, 't1')

  expect(forks.length).toBe(1)
  expect(compactions).toEqual([])
  expect(logs).toEqual(['Kept at 500k tokens: mid-debug'])
})

test('below 250k the mod asks no judge and prints nothing', async ($, on) => {
  const { begin, forks, compactions, logs, turn, resize } = engine(on)
  await begin($)

  resize(249_000)
  await turn($, 't1')
  expect(forks).toEqual([])
  expect(logs).toEqual([])

  resize(250_000)
  await turn($, 't2')
  expect(forks.length).toBe(1)
  expect(compactions).toEqual([''])
})

test('the judge waits for every hook of the turn’s end', async ($, on) => {
  const { begin, clock, forks, compactions } = engine(on, { endMs: 500 })
  await begin($)

  await $.turn.start({ text: 'go', turnId: 't1' })
  const ended = $.turn.complete({ answer: 'done', durationMs: 1, isAborted: false, turnId: 't1', reason: 'answer' })
  await clock.advance(499)
  expect(forks).toEqual([])

  await clock.advance(1)
  await ended
  await clock.settle()
  expect(compactions).toEqual([''])
})

test('a turn begun while the turn’s end hooks run asks no judge', async ($, on) => {
  const { begin, clock, forks, compactions } = engine(on, { endMs: 500 })
  await begin($)

  await $.turn.start({ text: 'go', turnId: 't1' })
  const ended = $.turn.complete({ answer: 'done', durationMs: 1, isAborted: false, turnId: 't1', reason: 'answer' })
  await clock.advance(100)
  await $.turn.start({ text: 'a notification', turnId: 't2' })
  await clock.advance(400)
  await ended
  await clock.settle()

  expect(forks).toEqual([])
  expect(compactions).toEqual([])
})

test('a turn begun while the judge runs drops its verdict without a line in that turn', async ($, on) => {
  const { begin, clock, compactions, logs, turn } = engine(on, { forkMs: 1_000 })
  await begin($)

  await turn($, 't1')
  await $.turn.start({ text: 'next', turnId: 't2' })
  await clock.advance(1_000)

  expect(compactions).toEqual([])
  expect(logs).toEqual([])
})

test('a fork with no answer says so', async ($, on) => {
  const { begin, compactions, logs, turn } = engine(on, { verdict: null })
  await begin($)

  await turn($, 't1')

  expect(compactions).toEqual([])
  expect(logs).toEqual(['No verdict at 500k tokens: empty-reply'])
})

// What the question at 500k read and wrote: on a hit, all but the question
// itself from the cache; on a miss, only the system prompt and tools, which
// sit in a cache shared across sessions, writing the rest again.
const HIT = { ...USAGE, input_tokens: 1_500, cache_read_input_tokens: 500_000 }
const MISS = { ...USAGE, input_tokens: 1_500, cache_read_input_tokens: 30_000, cache_creation_input_tokens: 470_000 }

test('a question read from the prompt cache adds no line', async ($, on) => {
  const { begin, logs, turn } = engine(on, { forkUsage: HIT })
  await begin($)

  await turn($, 't1')

  expect(logs).toEqual(['Compacting at 500k tokens: the PR merged'])
})

test('a question that missed the prompt cache says so on a line of its own', async ($, on) => {
  const { begin, compactions, logs, turn } = engine(on, { forkUsage: MISS })
  await begin($)

  await turn($, 't1')

  expect(compactions).toEqual([''])
  expect(logs).toEqual(['Missed the prompt cache at 500k tokens: read 30k of 502k from it', 'Compacting at 500k tokens: the PR merged'])
})

test('a compaction that fails says so, and is tried again once the session sits idle', async ($, on) => {
  const { begin, clock, compactions, logs, turn } = engine(on, { compactFailures: 1 })
  await begin($)

  await turn($, 't1')

  // The test engine skips a hook that throws and rejects with its own error.
  expect(logs.length).toBe(2)
  expect(logs[0]).toBe('Compacting at 500k tokens: the PR merged')
  expect(logs[1]).toStartWith('Failed: ')

  await clock.advance(IDLE)
  await clock.settle()
  expect(compactions).toEqual([''])
})

test('a compaction whose process ended first is finished when the next process starts', async ($, on) => {
  // What a process left that ended mid-compaction, as one does when Claude
  // Code's background daemon restarts it after an update.
  const { begin, clock, compactions, forks, logs } = engine(on, { stored: ANNOUNCED })

  await begin($)
  await clock.settle()

  expect(forks).toEqual([])
  expect(compactions).toEqual([''])
  expect(logs).toEqual(['Finishing the compaction at 489k tokens: Claude Code restarted before it landed'])

  // Settled, it leaves nothing for a later start to finish.
  await begin($)
  await clock.settle()
  expect(compactions).toEqual([''])
})

test('an announcement a turn has moved past is dropped', async ($, on) => {
  const { begin, clock, compactions, logs } = engine(on, { stored: ANNOUNCED, conversation: ['m0', 't1'] })

  await begin($)
  await clock.settle()

  expect(compactions).toEqual([])
  expect(logs).toEqual([])
})

test('an announcement whose compaction landed is dropped, though it kept the newest message', async ($, on) => {
  // As a reload of the mod during a compaction leaves it: the next process
  // reads the compaction's summary and the message it kept.
  const { begin, clock, compactions, logs } = engine(on, { stored: ANNOUNCED, conversation: ['summary', 'm0'] })

  await begin($)
  await clock.settle()

  expect(compactions).toEqual([])
  expect(logs).toEqual([])
})

test('a start while the mod compacts, as a reload of the mod raises, starts no second compaction', async ($, on) => {
  const { begin, clock, compactions, turn } = engine(on, { compactMs: 1_000 })
  await begin($)

  // The kit reruns the same registration rather than reloading it; across a
  // real reload only `$.state` carries the flag.
  await turn($, 't1')
  await begin($)
  await clock.advance(1_000)
  await clock.settle()

  expect(compactions).toEqual([''])
})

test('a session with nobody at the prompt leaves an unfinished compaction alone', async ($, on) => {
  const { begin, clock, compactions } = engine(on, { stored: ANNOUNCED })

  await begin($, false)
  await clock.settle()

  expect(compactions).toEqual([])
})

test('a /compact typed while the judge runs drops its verdict', async ($, on) => {
  const { begin, clock, compactions, turn, type } = engine(on, { forkMs: 1_000 })
  await begin($)

  await turn($, 't1')
  await type($, 'compact')
  await clock.advance(1_000)

  expect(compactions).toEqual([])
})

test('another slash command while the judge runs leaves its verdict standing', async ($, on) => {
  const { begin, clock, compactions, turn, type } = engine(on, { forkMs: 1_000 })
  await begin($)

  await turn($, 't1')
  await type($, 'context')
  await clock.advance(1_000)

  expect(compactions).toEqual([''])
})

test('a /clear while the judge runs drops its verdict', async ($, on) => {
  const { begin, clock, compactions, turn } = engine(on, { forkMs: 1_000 })
  await begin($)

  await turn($, 't1')
  await $.session.end({ reason: 'clear', sessionId: 's1', resume: { id: '' } })
  await clock.advance(1_000)

  expect(compactions).toEqual([])
})

test('an interrupted turn and a subagent’s turn ask no judge', async ($, on) => {
  const { begin, clock, forks } = engine(on)
  await begin($)

  await $.turn.start({ text: 'go', turnId: 't1' })
  await $.turn.complete({ answer: '', durationMs: 1, isAborted: true, turnId: 't1', reason: 'aborted' })
  await $.turn.start({ text: 'go', turnId: 's1', agentId: 'a1' })
  await $.turn.complete({ answer: 'done', durationMs: 1, isAborted: false, turnId: 's1', agentId: 'a1', reason: 'answer' })
  await clock.settle()

  expect(forks).toEqual([])
})

test('a session with nobody at the prompt asks no judge', async ($, on) => {
  const { begin, forks, turn } = engine(on)
  await begin($, false)

  await turn($, 't1')

  expect(forks).toEqual([])
})

test('a session left idle is asked again before its prompt cache lapses', async ($, on) => {
  const { begin, clock, compactions, forks, logs, turn } = engine(on, { tokens: 200_000 })
  await begin($)

  await turn($, 't1')
  await clock.advance(IDLE - 1)
  expect(forks).toEqual([])

  await clock.advance(1)
  await clock.settle()
  expect(forks.length).toBe(1)
  expect(forks[0]).toContain('makes a cache write of all but 30k of its 200k tokens, against about 112k after a compaction')
  expect(forks[0]).not.toContain('pays for itself')
  expect(compactions).toEqual([''])
  expect(logs).toEqual(['Compacting at 200k tokens, idle 55 minutes: the PR merged'])
})

test('an idle session the model keeps is left as it is', async ($, on) => {
  const { begin, clock, compactions, logs, turn } = engine(on, { tokens: 200_000, idleVerdict: KEEP })
  await begin($)

  await turn($, 't1')
  await clock.advance(IDLE)
  await clock.settle()

  expect(compactions).toEqual([])
  expect(logs).toEqual(['Kept at 200k tokens, idle 55 minutes: mid-debug'])
})

test('a session kept at its turn’s end is asked again once it sits idle', async ($, on) => {
  const { begin, clock, compactions, logs, turn } = engine(on, { verdict: KEEP })
  await begin($)

  await turn($, 't1')
  await clock.advance(IDLE)
  await clock.settle()

  expect(compactions).toEqual([''])
  expect(logs).toEqual(['Kept at 500k tokens: mid-debug', 'Compacting at 500k tokens, idle 55 minutes: the PR merged'])
})

test('a compaction while the idle question runs drops its verdict', async ($, on) => {
  const { begin, clock, compactions, logs, turn, resize } = engine(on, { tokens: 200_000, forkMs: 1_000 })
  await begin($)

  await turn($, 't1')
  await clock.advance(IDLE + 500)
  resize(undefined)
  await clock.advance(500)
  await clock.settle()

  expect(compactions).toEqual([])
  expect(logs).toEqual(['Dropped the verdict at 200k tokens, idle 55 minutes: the conversation moved on'])
})

test('a compaction at the turn’s end leaves nothing for idleness to do', async ($, on) => {
  const { begin, clock, compactions, turn } = engine(on)
  await begin($)

  await turn($, 't1')
  await clock.advance(IDLE)
  await clock.settle()

  expect(compactions).toEqual([''])
})

test('a turn begun meanwhile restarts the wait', async ($, on) => {
  const { begin, clock, compactions, turn } = engine(on, { tokens: 200_000 })
  await begin($)

  await turn($, 't1')
  await clock.advance(30 * 60_000)
  await turn($, 't2')
  await clock.advance(IDLE - 1)
  expect(compactions).toEqual([])

  await clock.advance(1)
  await clock.settle()
  expect(compactions).toEqual([''])
})

test('a turn still running at the deadline is left alone', async ($, on) => {
  const { begin, clock, compactions, logs, turn } = engine(on, { tokens: 200_000 })
  await begin($)

  await turn($, 't1')
  await clock.advance(30 * 60_000)
  await $.turn.start({ text: 'go', turnId: 't2' })
  await clock.advance(30 * 60_000)
  await clock.settle()

  expect(compactions).toEqual([])
  expect(logs).toEqual([])
})

test('the idle wait runs from the turn’s end, however long the judge takes', async ($, on) => {
  const { begin, clock, forks, turn } = engine(on, { verdict: KEEP, forkMs: 10 * 60_000 })
  await begin($)

  await turn($, 't1')
  await clock.advance(IDLE - 1)
  expect(forks.length).toBe(1)

  await clock.advance(1)
  await clock.settle()
  expect(forks.length).toBe(2)
})

test('a compaction meanwhile, such as Claude Code’s own idle one, is not repeated', async ($, on) => {
  const { begin, clock, compactions, turn, resize } = engine(on, { tokens: 200_000 })
  await begin($)

  await turn($, 't1')
  await clock.advance(IDLE - 60_000)
  resize(undefined)
  await clock.advance(60_000)
  await clock.settle()

  expect(compactions).toEqual([])
})

test('an idle session below 175k is left as it is', async ($, on) => {
  const { begin, clock, compactions, turn, resize } = engine(on, { tokens: 174_000 })
  await begin($)

  await turn($, 't1')
  await clock.advance(IDLE)
  await clock.settle()
  expect(compactions).toEqual([])

  resize(175_000)
  await turn($, 't2')
  await clock.advance(IDLE)
  await clock.settle()
  expect(compactions).toEqual([''])
})

test('an idle session on a one-hour cache is asked without a subscription', async ($, on) => {
  // An API key that opted in to the one-hour cache reports no rate limits.
  const { begin, clock, compactions, turn } = engine(on, { tokens: 200_000, rateLimits: [] })
  await begin($)

  await turn($, 't1')
  await clock.advance(IDLE)
  await clock.settle()

  expect(compactions).toEqual([''])
})

test('an idle session on a five-minute cache is asked after 4 minutes', async ($, on) => {
  const { begin, clock, forks, logs, turn } = engine(on, { tokens: 200_000, usage: FIVE_MINUTES, idleVerdict: KEEP })
  await begin($)

  await turn($, 't1')
  await clock.advance(IDLE_5M - 1)
  expect(forks).toEqual([])

  await clock.advance(1)
  await clock.settle()
  expect(forks.length).toBe(1)
  expect(forks[0]).toContain('Your turn ended 4 minutes ago')
  expect(forks[0]).toContain('The prompt cache holding the conversation lapses within a few minutes.')

  // Kept, the context stays as it was, and nothing asks again at 55 minutes.
  await clock.advance(IDLE)
  await clock.settle()
  expect(forks.length).toBe(1)
  expect(logs).toEqual(['Kept at 200k tokens, idle 4 minutes: mid-debug'])
})

for (const [name, usage] of [
  // A turn that ran onto usage credits partway, whose later requests cached
  // for five minutes.
  ['wrote to the cache for both lifetimes', { ...HOUR, ephemeral_5m_input_tokens: 1_000, ephemeral_1h_input_tokens: 2_000 }],
  ['wrote nothing to the cache', { ...USAGE, model: HOUR.model }],
  // A provider that names no lifetime reports only the whole write.
  ['named no cache lifetime', { ...USAGE, cache_creation_input_tokens: 3_000, model: HOUR.model }],
] as const) {
  test(`an idle session whose turn ${name} is left as it is`, async ($, on) => {
    const { begin, clock, forks, turn } = engine(on, { tokens: 200_000, usage })
    await begin($)

    await turn($, 't1')
    await clock.advance(IDLE)
    await clock.settle()

    expect(forks).toEqual([])
  })
}

test('a session with a 200k context window is left as it is, idle or not', async ($, on) => {
  const { begin, clock, forks, logs, turn } = engine(on, { tokens: 180_000, window: 200_000 })
  await begin($)

  await turn($, 't1')
  await clock.advance(IDLE)
  await clock.settle()

  expect(forks).toEqual([])
  expect(logs).toEqual([])
})
