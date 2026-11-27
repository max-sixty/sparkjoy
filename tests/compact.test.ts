import type { ModelUsage, On, TurnUsage } from 'claude-code'
import { type Engine, expect, mock, test } from 'claude-code/testing'
import { messageKey } from '../hooks/register'

const USAGE = {
  input_tokens: 0,
  output_tokens: 0,
  cache_creation_input_tokens: 0,
  cache_read_input_tokens: 0,
}
const COMPACT = '{"compact": true, "reason": "the PR merged"}'
const KEEP = '{"compact": false, "reason": "mid-debug"}'
const said = (text: string) => ({ role: 'user' as const, text, toolUses: [] })
const SUMMARY = { messages: [said('summary')] }
// The announcement of a compaction made while the conversation was the one
// message `m0`.
const ANNOUNCED = {
  'compacting:s1': { at: '489k tokens', messages: [messageKey(said('m0'))] },
}

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
const FIVE_MINUTES = {
  ...HOUR,
  ephemeral_5m_input_tokens: 3_000,
  ephemeral_1h_input_tokens: 0,
}

// The user's home, and the figures file `/sparkjoy report` saves in Claude
// Code's configuration directory there.
const HOME = '/home/u'
const FIGURES = `${HOME}/.claude/sparkjoy/figures.json`

// Stands for the engine beneath the mod: a context of `tokens` until `resize`
// changes it or a compaction leaves it unmeasured, the rate-limit windows
// `rateLimits` (a subscription's five-hour and weekly ones unless told
// otherwise), turns that end reporting `usage` (`HOUR` unless told
// otherwise), a fork that answers `verdict` after a turn and `idleVerdict`
// after an idle wait, each after `forkMs` (`null`: an empty reply) and
// reporting `forkUsage` (the whole context read from the cache unless told
// otherwise),
// compactions that take `compactMs` and fail `compactFailures` times before
// one succeeds, a turn whose end settles after `endMs`, a store holding
// `stored` (what an earlier process left), a conversation of user messages
// with the texts `conversation`, which a turn adds a message to and a
// compaction its summary, a process environment of `env` (`HOME` unless told
// otherwise), a file at `figuresAt` holding `saved` (none when undefined),
// settings that take each change the mod makes, and a record of what it was
// asked.
function engine(
  on: On,
  {
    tokens = 500_000,
    window = 1_000_000,
    verdict = COMPACT as string | null,
    idleVerdict = COMPACT as string | null,
    forkMs = 0,
    forkUsage = undefined as ModelUsage | undefined,
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
  // Each slash command run, registered, and prompt submitted.
  const commands: string[] = []
  const registered: string[] = []
  const prompts: string[] = []
  // Each change the mod made to its settings.
  const sets: { key: string; value: unknown }[] = []
  const logs: string[] = []

  mock.store(on, stored)
  on('session.id', () => ({ value: 's1' }))
  on('env.get', (_, e) => ({ value: env[e.name] }))
  on('fs.read', (_, e) => {
    if (e.path !== figuresAt || saved === undefined)
      throw new Error(`ENOENT: no such file or directory, open '${e.path}'`)
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
    // What the fork read: the context as it was sent.
    const usage = forkUsage ?? { ...USAGE, cache_read_input_tokens: size ?? 0 }
    await clock.sleep(forkMs)
    const answer = e.prompt.includes('minutes ago') ? idleVerdict : verdict
    if (answer === null)
      return { value: { isAnswered: false, reason: 'empty-reply', usage } }
    return { value: { isAnswered: true, text: answer, usage } }
  })
  // A plugin's `$.session.compact` reaches here as the plugin made it, with no
  // `trigger`; the engine stamps it `plugin`.
  on('session.compact', async (_, e) => {
    if (compactMs > 0) await clock.sleep(compactMs)
    if (failures-- > 0) throw new Error('a turn is in flight')
    if (e.trigger === undefined || e.trigger === 'plugin')
      compactions.push(e.instructions ?? '')
    // Claude Code measures the context again only at the next request.
    size = undefined
    messages.push(`summary ${compactions.length}`)
    return SUMMARY
  })
  on('command.run', (_, e) => {
    commands.push(e.command)
    return { text: '' }
  })
  on('command.register', (_, e) => {
    registered.push(e.name)
    return { value: { command: e.name } }
  })
  on('config.set', (_, e) => {
    sets.push({ key: e.key, value: e.value })
    return { value: e.value }
  })
  on('prompt.submit', (_, e) => {
    prompts.push(e.text)
    return { text: e.text }
  })

  // Runs one main-loop turn to its answer and lets what it starts settle.
  async function turn($: Engine, turnId: string, answer = 'Merged #12. ✅') {
    await $.turn.start({ text: 'go', turnId })
    await $.turn.complete({
      answer,
      durationMs: 1,
      isAborted: false,
      turnId,
      reason: 'answer',
      usage,
    })
    await clock.settle()
  }

  // Starts the session the mod hears, under the REPL unless told otherwise.
  async function begin($: Engine, isInteractive = true) {
    await $.session.start({
      cwd: '/',
      surface: isInteractive ? 'terminal' : null,
      isInteractive,
    })
  }

  const resize = (to: number | undefined) => (size = to)

  // Saves a figures file, as the report does, or removes it.
  const save = (text: string | undefined) => (saved = text)

  // Runs a slash command as the person typing it at the prompt would.
  const type = ($: Engine, command: string, args = '') =>
    $.command.run({
      command,
      args,
      origin: { kind: 'composer' },
      presentation: { isFullscreen: false, columns: 80 },
    })

  return {
    clock,
    forks,
    compactions,
    commands,
    registered,
    prompts,
    sets,
    logs,
    begin,
    turn,
    resize,
    save,
    type,
  }
}

// Figures the report fitted to a user on a five-minute cache, whose
// context after a compaction is smaller, and whose summary longer, than the
// author's.
const FITTED = JSON.stringify({
  WRITE: 25,
  OUTPUT: 100,
  SUMMARY: 8_000,
  K: 120_000,
  P: 20_000,
  LAPSE: 0.01,
  TURN_MEDIAN: 12,
})

// What the question says at 500k with the author's figures.
const AUTHORS = [
  'with 500k tokens of context. A compaction would cut it to about 142k,',
  'and pays for itself once the session runs 13 more requests that need nothing it dropped',
  'a turn runs a median of 9)',
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
  expect(forks[0]).toContain('with 500k tokens of context')
  expect(forks[0]).toContain('pays for itself once the session runs 13 more requests')
  expect(forks[1]).toContain('with 250k tokens of context')
  expect(forks[1]).toContain('pays for itself once the session runs 40 more requests')
})

test('the question states the figures the report fitted to the user’s sessions', async ($, on) => {
  const { begin, forks, turn } = engine(on, { verdict: KEEP, saved: FITTED })
  await begin($)

  await turn($, 't1')

  expect(forks[0]).toContain('A compaction would cut it to about 120k,')
  // (500k read + 100 × 8k output + 24 × (120k − 20k) cache write) over the
  // 1.24 × (500k − 120k) each later request saves, rounded up.
  expect(forks[0]).toContain('pays for itself once the session runs 8 more requests')
  expect(forks[0]).toContain('a turn runs a median of 12)')
})

test('an idle session’s question states the fitted figures too', async ($, on) => {
  const { begin, clock, forks, turn } = engine(on, { tokens: 200_000, saved: FITTED })
  await begin($)

  await turn($, 't1')
  await clock.advance(IDLE)
  await clock.settle()

  expect(forks[0]).toContain(
    'the next request will write all but 20k of it to the cache again, against about 100k after a compaction',
  )
})

test('the figures are read from $CLAUDE_CONFIG_DIR where it is set', async ($, on) => {
  const env = { HOME, CLAUDE_CONFIG_DIR: '/work/claude' }
  const { begin, forks, turn } = engine(on, {
    verdict: KEEP,
    env,
    figuresAt: '/work/claude/sparkjoy/figures.json',
    saved: FITTED,
  })
  await begin($)

  await turn($, 't1')

  expect(forks[0]).toContain('pays for itself once the session runs 8 more requests')
})

test('the floors the report fitted decide where sparkjoy asks', async ($, on) => {
  const saved = JSON.stringify({ FLOOR: 150_000, IDLE_FLOOR: 120_000 })
  const { begin, clock, forks, turn, resize } = engine(on, {
    verdict: KEEP,
    tokens: 200_000,
    saved,
  })
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
  expect(forks[1]).toContain('pays for itself once the session runs 8 more requests')
})

test('each figure the file lacks, or holds where the sums can’t use it, stays the author’s', async ($, on) => {
  // K at the floor, where no compaction asked about at a turn end would pay;
  // P above the author's K; the summary as text; the median turn kept.
  const saved = JSON.stringify({
    K: 250_000,
    P: 150_000,
    SUMMARY: '8k',
    LAPSE: null,
    TURN_MEDIAN: 12,
  })
  const { begin, forks, turn } = engine(on, { verdict: KEEP, saved })
  await begin($)

  await turn($, 't1')

  for (const text of AUTHORS.slice(0, 2)) expect(forks[0]).toContain(text)
  expect(forks[0]).toContain('a turn runs a median of 12)')
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

test(
  'the floors the user set win over the ones the report fitted',
  { options: { floor: 300_000, idle_floor: 250_000 } },
  async ($, on) => {
    const saved = JSON.stringify({ FLOOR: 150_000, IDLE_FLOOR: 120_000 })
    const { begin, clock, forks, turn, resize } = engine(on, {
      verdict: KEEP,
      idleVerdict: KEEP,
      tokens: 280_000,
      saved,
    })
    await begin($)

    // Above the fitted floor at a turn end and below the set one; above the set
    // idle floor.
    await turn($, 't1')
    expect(forks).toEqual([])
    await clock.advance(IDLE)
    await clock.settle()
    expect(forks.length).toBe(1)

    // Above the fitted idle floor, below the set one.
    resize(240_000)
    await turn($, 't2')
    await clock.advance(IDLE)
    await clock.settle()
    expect(forks.length).toBe(1)
  },
)

test(
  'a floor the user set at or below the context after a compaction gives way to the default',
  { options: { floor: 140_000 } },
  async ($, on) => {
    const { begin, forks, turn, type } = engine(on, { verdict: KEEP, tokens: 200_000 })
    await begin($)

    // Below the author's 250k, above the 140k set, which is below the author's
    // 142k after a compaction.
    await turn($, 't1')
    expect(forks).toEqual([])
    const { text } = await type($, 'sparkjoy')
    expect(text).toContain(
      'at a turn end from 250k (the default, as your 140k is out of range)',
    )
  },
)

test(
  'a floor the user set above the report’s measure of the context after a compaction keeps that measure',
  { options: { floor: 400_000 } },
  async ($, on) => {
    // A report that measured K, above the default floor, but fitted no floors.
    const saved = JSON.stringify({ K: 300_000, P: 30_000 })
    const { begin, forks, turn } = engine(on, { verdict: KEEP, saved })
    await begin($)

    await turn($, 't1')

    expect(forks[0]).toContain('A compaction would cut it to about 300k,')
  },
)

// What the question says between what it asks to decide and the answer's
// format.
const INSTRUCTIONS =
  'Keep the context while a pull request awaits review, and compact once it merges.'
const ADDED = `\n\nThe user added these instructions for this decision:\n${INSTRUCTIONS}\n\nAnswer with`

test(
  'the user’s instructions join both questions before the answer’s format',
  { options: { instructions: INSTRUCTIONS } },
  async ($, on) => {
    const { begin, clock, forks, turn } = engine(on, {
      verdict: KEEP,
      idleVerdict: KEEP,
    })
    await begin($)

    await turn($, 't1')
    await clock.advance(IDLE)
    await clock.settle()

    expect(forks.length).toBe(2)
    for (const fork of forks) expect(fork).toContain(ADDED)
  },
)

for (const [name, options] of [
  ['unset', {}],
  ['empty', { instructions: '' }],
] as const) {
  test(
    `instructions ${name} leave the question as it was`,
    { options },
    async ($, on) => {
      const { begin, forks, turn } = engine(on, { verdict: KEEP })
      await begin($)

      await turn($, 't1')

      expect(forks[0]).toContain('cost the next turns.\n\nAnswer with')
    },
  )
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
  const ended = $.turn.complete({
    answer: 'done',
    durationMs: 1,
    isAborted: false,
    turnId: 't1',
    reason: 'answer',
  })
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
  const ended = $.turn.complete({
    answer: 'done',
    durationMs: 1,
    isAborted: false,
    turnId: 't1',
    reason: 'answer',
  })
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

test('a fork whose request was refused, and so read nothing, says nothing of the cache', async ($, on) => {
  const { begin, logs, turn } = engine(on, { verdict: null, forkUsage: USAGE })
  await begin($)

  await turn($, 't1')

  expect(logs).toEqual(['No verdict at 500k tokens: empty-reply'])
})

// What the question at 500k read and wrote: on a hit, all but the question
// itself from the cache; on a miss, only the system prompt and tools, which
// sit in a cache shared across sessions, writing the rest again.
const HIT = { ...USAGE, input_tokens: 1_500, cache_read_input_tokens: 500_000 }
const MISS = {
  ...USAGE,
  input_tokens: 1_500,
  cache_read_input_tokens: 30_000,
  cache_creation_input_tokens: 470_000,
}

test('a question read from the prompt cache says nothing of it', async ($, on) => {
  const { begin, logs, turn } = engine(on, { forkUsage: HIT })
  await begin($)

  await turn($, 't1')

  expect(logs).toEqual(['Compacting at 500k tokens: the PR merged'])
})

test('a question that read the whole context from the cache hit it, however long the reply after it', async ($, on) => {
  const { begin, logs, turn } = engine(on, {
    forkUsage: { ...USAGE, input_tokens: 80_000, cache_read_input_tokens: 500_000 },
  })
  await begin($)

  await turn($, 't1')

  expect(logs).toEqual(['Compacting at 500k tokens: the PR merged'])
})

// The verdict from 900k context tokens.
const PAST_CEILING = 'past 900k, where even work that needs the detail compacts'

test('from 900k a turn end compacts without asking', async ($, on) => {
  const { begin, compactions, forks, logs, turn } = engine(on, {
    tokens: 920_000,
    verdict: KEEP,
  })
  await begin($)

  await turn($, 't1')

  expect(forks).toEqual([])
  expect(compactions).toEqual([''])
  expect(logs).toEqual([`Compacting at 920k tokens, without asking: ${PAST_CEILING}`])
})

test(
  'classifying, from 900k a turn end and an idle session say they would compact without asking',
  { options: { mode: 'classify' } },
  async ($, on) => {
    const { begin, clock, compactions, forks, logs, turn } = engine(on, {
      tokens: 920_000,
      verdict: KEEP,
      idleVerdict: KEEP,
    })
    await begin($)

    await turn($, 't1')
    await clock.advance(IDLE)
    await clock.settle()

    expect(forks).toEqual([])
    expect(compactions).toEqual([])
    expect(logs).toEqual([
      `Would compact at 920k tokens, without asking: ${PAST_CEILING}`,
      `Would compact at 920k tokens, idle 55 minutes, without asking: ${PAST_CEILING}`,
    ])
  },
)

// The line sparkjoy adds after one reporting a bug.
const BUG = 'That looks like a bug: /sparkjoy bug drafts a report of it.'

test('a question at a turn’s end that missed the prompt cache says so in its verdict’s line and calls it a bug', async ($, on) => {
  const { begin, compactions, logs, turn } = engine(on, { forkUsage: MISS })
  await begin($)

  await turn($, 't1')

  expect(compactions).toEqual([''])
  expect(logs).toEqual([
    'Compacting at 500k tokens, after missing the prompt cache the turn had just written to (read 30k of 502k): the PR merged',
    BUG,
  ])
})

test('an unreadable verdict from a question that missed the prompt cache calls it a bug once', async ($, on) => {
  const { begin, logs, turn } = engine(on, { forkUsage: MISS, verdict: 'compact it' })
  await begin($)

  await turn($, 't1')

  expect(logs).toEqual([
    'Unreadable verdict at 500k tokens, after missing the prompt cache the turn had just written to (read 30k of 502k): compact it',
    BUG,
  ])
})

test('a question after a turn that wrote nothing to the cache says it missed, but not that it’s a bug', async ($, on) => {
  const { begin, logs, turn } = engine(on, {
    forkUsage: MISS,
    usage: { ...HOUR, cache_creation_input_tokens: 0, ephemeral_1h_input_tokens: 0 },
  })
  await begin($)

  await turn($, 't1')

  expect(logs).toEqual([
    'Compacting at 500k tokens, after missing the prompt cache (read 30k of 502k): the PR merged',
  ])
})

test('an unreadable verdict says so and calls it a bug', async ($, on) => {
  const { begin, compactions, logs, turn } = engine(on, { verdict: 'compact it' })
  await begin($)

  await turn($, 't1')

  expect(compactions).toEqual([])
  expect(logs).toEqual(['Unreadable verdict at 500k tokens: compact it', BUG])
})

test('an idle question that missed the prompt cache says so in its verdict’s line, and not that it’s a bug', async ($, on) => {
  const { begin, clock, logs, turn } = engine(on, {
    tokens: 200_000,
    forkUsage: { ...MISS, cache_creation_input_tokens: 170_000 },
  })
  await begin($)

  await turn($, 't1')
  await clock.advance(IDLE)
  await clock.settle()

  expect(logs).toEqual([
    'Compacting at 200k tokens, idle 55 minutes, after missing the prompt cache (read 30k of 202k; /sparkjoy report lists the misses): the PR merged',
  ])
})

test('a compaction the engine refuses says why, and is tried again once the session sits idle', async ($, on) => {
  const { begin, clock, compactions, logs, turn } = engine(on, { compactFailures: 1 })
  await begin($)

  await turn($, 't1')

  // The test engine skips a hook that throws and rejects with its own error.
  expect(logs.length).toBe(2)
  expect(logs[0]).toBe('Compacting at 500k tokens: the PR merged')
  expect(logs[1]).toStartWith("Didn't compact at 500k tokens: ")

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
  expect(logs).toEqual([
    'Finishing the compaction at 489k tokens: Claude Code restarted before it landed',
  ])

  // Settled, it leaves nothing for a later start to finish.
  await begin($)
  await clock.settle()
  expect(compactions).toEqual([''])
})

test('a compaction to finish that the engine refuses says why', async ($, on) => {
  const { begin, clock, logs } = engine(on, { stored: ANNOUNCED, compactFailures: 1 })

  await begin($)
  await clock.settle()

  expect(logs.length).toBe(2)
  expect(logs[1]).toStartWith("Didn't compact at 489k tokens: ")
})

test('an announcement a turn has moved past is dropped', async ($, on) => {
  const { begin, clock, compactions, logs } = engine(on, {
    stored: ANNOUNCED,
    conversation: ['m0', 't1'],
  })

  await begin($)
  await clock.settle()

  expect(compactions).toEqual([])
  expect(logs).toEqual([])
})

test('an announcement whose compaction landed is dropped, though it kept the newest message', async ($, on) => {
  // As a reload of the mod during a compaction leaves it: the next process
  // reads the compaction's summary and the message it kept.
  const { begin, clock, compactions, logs } = engine(on, {
    stored: ANNOUNCED,
    conversation: ['summary', 'm0'],
  })

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
  await $.turn.complete({
    answer: '',
    durationMs: 1,
    isAborted: true,
    turnId: 't1',
    reason: 'aborted',
  })
  await $.turn.start({ text: 'go', turnId: 's1', agentId: 'a1' })
  await $.turn.complete({
    answer: 'done',
    durationMs: 1,
    isAborted: false,
    turnId: 's1',
    agentId: 'a1',
    reason: 'answer',
  })
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
  const { begin, clock, compactions, forks, logs, turn } = engine(on, {
    tokens: 200_000,
  })
  await begin($)

  await turn($, 't1')
  await clock.advance(IDLE - 1)
  expect(forks).toEqual([])

  await clock.advance(1)
  await clock.settle()
  expect(forks.length).toBe(1)
  expect(forks[0]).toContain(
    'the next request will write all but 30k of it to the cache again, against about 112k after a compaction',
  )
  expect(forks[0]).not.toContain('pays for itself')
  expect(compactions).toEqual([''])
  expect(logs).toEqual(['Compacting at 200k tokens, idle 55 minutes: the PR merged'])
})

test('an idle session the model keeps is left as it is', async ($, on) => {
  const { begin, clock, compactions, logs, turn } = engine(on, {
    tokens: 200_000,
    idleVerdict: KEEP,
  })
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
  expect(logs).toEqual([
    'Kept at 500k tokens: mid-debug',
    'Compacting at 500k tokens, idle 55 minutes: the PR merged',
  ])
})

test('a compaction while the idle question runs drops its verdict', async ($, on) => {
  const { begin, clock, compactions, logs, turn, resize } = engine(on, {
    tokens: 200_000,
    forkMs: 1_000,
  })
  await begin($)

  await turn($, 't1')
  await clock.advance(IDLE + 500)
  resize(undefined)
  await clock.advance(500)
  await clock.settle()

  expect(compactions).toEqual([])
  expect(logs).toEqual([
    'Dropped the verdict at 200k tokens, idle 55 minutes: the conversation moved on',
  ])
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
  const { begin, clock, forks, turn } = engine(on, {
    verdict: KEEP,
    forkMs: 10 * 60_000,
  })
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
  const { begin, clock, compactions, turn } = engine(on, {
    tokens: 200_000,
    rateLimits: [],
  })
  await begin($)

  await turn($, 't1')
  await clock.advance(IDLE)
  await clock.settle()

  expect(compactions).toEqual([''])
})

test('an idle session on a five-minute cache is asked after 4 minutes', async ($, on) => {
  const { begin, clock, forks, logs, turn } = engine(on, {
    tokens: 200_000,
    usage: FIVE_MINUTES,
    idleVerdict: KEEP,
  })
  await begin($)

  await turn($, 't1')
  await clock.advance(IDLE_5M - 1)
  expect(forks).toEqual([])

  await clock.advance(1)
  await clock.settle()
  expect(forks.length).toBe(1)
  expect(forks[0]).toContain('Your turn ended 4 minutes ago')
  expect(forks[0]).toContain('the prompt cache holding it lapses within a few minutes')

  // Kept, the context stays as it was, and nothing asks again at 55 minutes.
  await clock.advance(IDLE)
  await clock.settle()
  expect(forks.length).toBe(1)
  expect(logs).toEqual(['Kept at 200k tokens, idle 4 minutes: mid-debug'])
})

for (const [name, usage] of [
  // A turn that ran onto usage credits partway, whose later requests cached
  // for five minutes.
  [
    'wrote to the cache for both lifetimes',
    { ...HOUR, ephemeral_5m_input_tokens: 1_000, ephemeral_1h_input_tokens: 2_000 },
  ],
  ['wrote nothing to the cache', { ...USAGE, model: HOUR.model }],
  // A provider that names no lifetime reports only the whole write.
  [
    'named no cache lifetime',
    { ...USAGE, cache_creation_input_tokens: 3_000, model: HOUR.model },
  ],
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
  const { begin, clock, forks, logs, turn } = engine(on, {
    tokens: 180_000,
    window: 200_000,
  })
  await begin($)

  await turn($, 't1')
  await clock.advance(IDLE)
  await clock.settle()

  expect(forks).toEqual([])
  expect(logs).toEqual([])
})

test(
  'off, sparkjoy asks nothing and compacts nothing, and /sparkjoy still answers',
  { options: { mode: 'off' } },
  async ($, on) => {
    const { begin, clock, compactions, forks, logs, registered, turn } = engine(on, {
      stored: ANNOUNCED,
    })
    await begin($)
    await clock.settle()

    await turn($, 't1')
    await clock.advance(IDLE)
    await clock.settle()

    expect(forks).toEqual([])
    expect(compactions).toEqual([])
    expect(logs).toEqual([])
    expect(registered).toEqual(['sparkjoy'])
  },
)

test(
  'classifying, sparkjoy asks at turn ends and in idle sessions and shows each verdict, but compacts nothing',
  { options: { mode: 'classify' } },
  async ($, on) => {
    const { begin, clock, compactions, forks, logs, turn } = engine(on, {
      stored: ANNOUNCED,
    })
    await begin($)
    await clock.settle()

    await turn($, 't1')
    await clock.advance(IDLE)
    await clock.settle()

    expect(forks.length).toBe(2)
    expect(compactions).toEqual([])
    expect(logs).toEqual([
      'Would compact at 500k tokens: the PR merged',
      'Would compact at 500k tokens, idle 55 minutes: the PR merged',
    ])
  },
)

test(
  'classifying, a verdict to keep reads as it does when on',
  { options: { mode: 'classify' } },
  async ($, on) => {
    const { begin, logs, turn } = engine(on, { verdict: KEEP })
    await begin($)

    await turn($, 't1')

    expect(logs).toEqual(['Kept at 500k tokens: mid-debug'])
  },
)

test('/sparkjoy off sets the mode off', async ($, on) => {
  const { begin, sets, type } = engine(on)
  await begin($)

  const { text } = await type($, 'sparkjoy', 'off')

  expect(sets).toEqual([{ key: 'sparkjoy.mode', value: 'off' }])
  expect(text).toBe('Off. It asks nothing and compacts nothing until /sparkjoy on.')
})

test('/sparkjoy classify sets the mode to classify', async ($, on) => {
  const { begin, sets, type } = engine(on)
  await begin($)

  const { text } = await type($, 'sparkjoy', 'classify')

  expect(sets).toEqual([{ key: 'sparkjoy.mode', value: 'classify' }])
  expect(text).toBe(
    'Classifying from the next turn end: it shows each verdict and compacts nothing until /sparkjoy on.',
  )
})

test('/sparkjoy on sets the mode on', { options: { mode: 'off' } }, async ($, on) => {
  const { begin, sets, type } = engine(on)
  await begin($)

  const { text } = await type($, 'sparkjoy', 'on')

  expect(sets).toEqual([{ key: 'sparkjoy.mode', value: 'on' }])
  expect(text).toBe('On from the next turn end.')
})

test(
  '/sparkjoy names each floor and where it comes from',
  { options: { floor: 300_000 } },
  async ($, on) => {
    const { begin, type, save } = engine(on, {
      saved: JSON.stringify({ IDLE_FLOOR: 120_000 }),
    })
    await begin($)

    const { text } = await type($, 'sparkjoy')
    save(undefined)
    const authors = await type($, 'sparkjoy')

    expect(text).toBe(
      "On. It asks whether to compact at a turn end from 300k (your setting) and in an idle session from 120k (the report's fit).\n" +
        "From 900k it doesn't ask: its verdict is to compact.\n" +
        'Its question carries no instructions of yours.\n' +
        '/sparkjoy on compacts when the model says to, /sparkjoy classify only shows its verdicts, and /sparkjoy off asks nothing. /config sets the floors and instructions.',
    )
    expect(authors.text).toContain('in an idle session from 175k (the default).')
  },
)

test(
  '/sparkjoy says a floor from 900k is out of range, since from there sparkjoy compacts without asking',
  { options: { floor: 950_000 } },
  async ($, on) => {
    const { begin, type } = engine(on)
    await begin($)

    const { text } = await type($, 'sparkjoy')

    expect(text).toContain(
      'at a turn end from 250k (the default, as your 950k is out of range)',
    )
  },
)

test('/sparkjoy says when the session’s window leaves it nothing to ask', async ($, on) => {
  const { begin, type } = engine(on, { tokens: 180_000, window: 200_000 })
  await begin($)

  const { text } = await type($, 'sparkjoy')

  expect(text?.split('\n')[1]).toBe(
    "This session's context window is 200k, and sparkjoy acts only in sessions with a 1M window, so it asks nothing here.",
  )
})

test('on Windows, sparkjoy finds the report’s figures under %USERPROFILE%', async ($, on) => {
  const profile = '/Users/u'
  const { begin, type } = engine(on, {
    env: { USERPROFILE: profile },
    figuresAt: `${profile}/.claude/sparkjoy/figures.json`,
    saved: JSON.stringify({ IDLE_FLOOR: 120_000 }),
  })
  await begin($)

  const { text } = await type($, 'sparkjoy')

  expect(text).toContain("in an idle session from 120k (the report's fit).")
})

test(
  '/sparkjoy says sparkjoy is classifying',
  { options: { mode: 'classify' } },
  async ($, on) => {
    const { begin, type } = engine(on)
    await begin($)

    const { text } = await type($, 'sparkjoy')

    expect(text?.split('\n')[0]).toBe(
      'Classifying. It asks whether to compact at a turn end from 250k (the default) and in an idle session from 175k (the default), and shows each verdict without compacting.',
    )
  },
)

test(
  '/sparkjoy says sparkjoy is off and quotes the start of the user’s instructions',
  { options: { mode: 'off', instructions: INSTRUCTIONS } },
  async ($, on) => {
    const { begin, type } = engine(on)
    await begin($)

    const { text } = await type($, 'sparkjoy')

    expect(text).toBe(
      'Off, so it asks nothing and compacts nothing. When on, it asks at a turn end from 250k (the default) and in an idle session from 175k (the default).\n' +
        'Its question carries your instructions: "Keep the context while a pull request awaits review, and…"\n' +
        '/sparkjoy on compacts when the model says to, /sparkjoy classify only shows its verdicts, and /sparkjoy off asks nothing. /config sets the floors and instructions.',
    )
  },
)

test('/sparkjoy report asks the model to run the report skill with what the user typed', async ($, on) => {
  const { begin, clock, prompts, type } = engine(on)
  await begin($)

  const { text } = await type($, 'sparkjoy', 'report since Monday')
  await clock.settle()

  expect(text).toBe('Asking the model to run the report.')
  expect(prompts).toEqual([
    'The user typed /sparkjoy report since Monday. Run the sparkjoy:report skill for them.',
  ])
})

test(
  '/sparkjoy bug asks the model to run the bug skill with the session and its settings, instructions whole',
  { options: { instructions: INSTRUCTIONS } },
  async ($, on) => {
    const { begin, clock, prompts, type } = engine(on)
    await begin($)

    const { text } = await type($, 'sparkjoy', 'bug it compacted mid-debug')
    await clock.settle()

    expect(text).toBe('Asking the model to draft a bug report.')
    expect(prompts).toEqual([
      "The user typed /sparkjoy bug it compacted mid-debug. Run the sparkjoy:bug skill for them. The session's id is s1. /sparkjoy states its settings as:\n" +
        '> On. It asks whether to compact at a turn end from 250k (the default) and in an idle session from 175k (the default).\n' +
        "> From 900k it doesn't ask: its verdict is to compact.\n" +
        `> Its question carries your instructions: "${INSTRUCTIONS}"`,
    ])
  },
)

test('/sparkjoy with another word says what it takes', async ($, on) => {
  const { begin, sets, type } = engine(on)
  await begin($)

  const { text } = await type($, 'sparkjoy', 'disable')

  expect(sets).toEqual([])
  expect(text).toBe(
    '/sparkjoy takes on, classify, off, report or bug, not "disable", or nothing to show its settings.',
  )
})
