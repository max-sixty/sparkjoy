import type { On } from 'claude-code'
import { type Engine, expect, mock, test } from 'claude-code/testing'

const USAGE = { input_tokens: 0, output_tokens: 0, cache_creation_input_tokens: 0, cache_read_input_tokens: 0 }
const COMPACT = '{"compact": true, "reason": "the PR merged"}'
const KEEP = '{"compact": false, "reason": "mid-debug"}'
const SUMMARY = { messages: [{ role: 'user' as const, text: 'summary', toolUses: [] }] }

const IDLE = 55 * 60_000

// Stands for the engine beneath the mod: a context of `tokens` until `resize`
// changes it or a compaction leaves it unmeasured, the rate-limit windows
// `rateLimits` (a subscription's five-hour and weekly ones unless told
// otherwise), a fork that answers `verdict` after a turn and `idleVerdict`
// after an idle wait, each after `forkMs` (`null`: an empty reply),
// compactions that fail `compactFailures` times before one succeeds, a turn
// whose end settles after `endMs`, and a record of what it was asked.
function engine(
  on: On,
  {
    tokens = 500_000,
    verdict = COMPACT as string | null,
    idleVerdict = COMPACT as string | null,
    forkMs = 0,
    endMs = 0,
    compactFailures = 0,
    rateLimits = [
      { kind: 'five_hour', percentUsed: 10 },
      { kind: 'seven_day', percentUsed: 30 },
    ],
  } = {},
) {
  const clock = mock.clock(on)
  let size: number | undefined = tokens
  let failures = compactFailures
  const forks: string[] = []
  // The instructions of each compaction the mod made, `''` for none.
  const compactions: string[] = []
  // Each slash command run.
  const commands: string[] = []
  const logs: string[] = []

  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('turn.start', (_, e) => ({ turnId: e.turnId }))
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
    value: { startedAt: 0, rateLimits, context: { tokens: size, window: 1_000_000 } },
  }))
  on('model.fork', async (_, e) => {
    forks.push(e.prompt)
    await clock.sleep(forkMs)
    const answer = e.prompt.includes('minutes ago') ? idleVerdict : verdict
    if (answer === null) return { value: { isAnswered: false, reason: 'empty-reply', usage: USAGE } }
    return { value: { isAnswered: true, text: answer, usage: USAGE } }
  })
  // A plugin's `$.session.compact` reaches here as the plugin made it, with no
  // `trigger`; the engine stamps it `plugin`.
  on('session.compact', (_, e) => {
    if (failures-- > 0) throw new Error('summary unavailable')
    if (e.trigger === undefined || e.trigger === 'plugin') compactions.push(e.instructions ?? '')
    // Claude Code measures the context again only at the next request.
    size = undefined
    return SUMMARY
  })
  on('command.run', (_, e) => {
    commands.push(e.command)
    return { text: '' }
  })

  // Runs one main-loop turn to its answer and lets what it starts settle.
  async function turn($: Engine, turnId: string, answer = 'Merged #12. ✅') {
    await $.turn.start({ text: 'go', turnId })
    await $.turn.complete({ answer, durationMs: 1, isAborted: false, turnId, reason: 'answer' })
    await clock.settle()
  }

  // Starts the session the mod hears, under the REPL unless told otherwise.
  async function begin($: Engine, isInteractive = true) {
    await $.session.start({ cwd: '/', surface: isInteractive ? 'terminal' : null, isInteractive })
  }

  const resize = (to: number | undefined) => (size = to)

  // Runs a slash command as the person typing it at the prompt would.
  const type = ($: Engine, command: string) =>
    $.command.run({
      command,
      args: '',
      origin: { kind: 'composer' },
      presentation: { isFullscreen: false, columns: 80 },
    })

  return { clock, forks, compactions, commands, logs, begin, turn, resize, type }
}

test('a finished turn compacts with no instructions and no /compact row', async ($, on) => {
  const { begin, forks, compactions, commands, logs, turn } = engine(on)
  await begin($)

  await turn($, 't1', 'Merged #13. ✅')

  expect(forks.length).toBe(1)
  expect(forks[0]).toContain('Merged #13. ✅')
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

  // (500k read + 100 × 4k summary + 39 × (122k − 30k + 20k) cache write) over
  // the 1.22 × (500k − 142k) each later request saves, rounded up.
  expect(forks[0]).toContain('At 500k a compaction pays for itself after about 13 more requests')
  expect(forks[1]).toContain('At 250k a compaction pays for itself after about 39 more requests')
  // 9 and 38 requests × that saving, over what a compaction costs.
  expect(forks[0]).toContain('keeping 500k through one more turn costs as much as about 0.7 compactions for a median turn and 3 for a mean one')
  expect(forks[1]).toContain('keeping 250k through one more turn costs as much as about 0.2 compactions for a median turn and 1.0 for a mean one')
})

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

  await turn($, 't1', 'Merged #13. ✅')
  await clock.advance(IDLE - 1)
  expect(forks).toEqual([])

  await clock.advance(1)
  await clock.settle()
  expect(forks.length).toBe(1)
  expect(forks[0]).toContain('Merged #13. ✅')
  expect(forks[0]).toContain('writes all but 30k of its 200k tokens to the cache again, against about 92k after a compaction')
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

test('an idle session on a plan with only a weekly limit is asked too', async ($, on) => {
  const rateLimits = [{ kind: 'seven_day', percentUsed: 85 }]
  const { begin, clock, compactions, turn } = engine(on, { tokens: 200_000, rateLimits })
  await begin($)

  await turn($, 't1')
  await clock.advance(IDLE)
  await clock.settle()

  expect(compactions).toEqual([''])
})

test('an idle session on an API key is left as it is', async ($, on) => {
  const { begin, clock, compactions, turn } = engine(on, { tokens: 200_000, rateLimits: [] })
  await begin($)

  await turn($, 't1')
  await clock.advance(IDLE)
  await clock.settle()

  expect(compactions).toEqual([])
})
