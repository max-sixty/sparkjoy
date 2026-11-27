// Prints the question a checkout of sparkjoy asks, for one case, so the evals
// and the README's copies of the question show exactly what the plugin asks.
//
//   bun evals/question.ts <checkout>/hooks/register.ts <tokens> [--idle <minutes>] [--instructions <text>] < reply
//
// `question()` isn't exported, so this imports a copy of register.ts that
// exports it, written beside the original so that the copy's imports of the
// files next to it resolve, and removed once imported. It passes each
// parameter by name: `reply`, `tokens` (the context's size), `idleMinutes`,
// `figures` and `instructions`, so a checkout whose question takes fewer of
// them is called with just those. `figures` is left out, so the question
// states the figures in `AUTHOR`, which come from my sessions, as the cases do.

import { readFileSync, unlinkSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { parseArgs } from 'node:util'

const {
  positionals: [registerPath, tokens],
  values: { idle, instructions, bounds },
} = parseArgs({
  allowPositionals: true,
  options: {
    idle: { type: 'string' },
    instructions: { type: 'string' },
    bounds: { type: 'boolean' },
  },
})
const source = readFileSync(registerPath, 'utf8')
// A checkout from before the plugin's figures has no AUTHOR.
const has = (name: string) => new RegExp(`^const ${name}\\b`, 'm').test(source)
const copy = resolve(dirname(registerPath), `.question-${process.pid}.ts`)
writeFileSync(
  copy,
  `${source}
export { question }
export const BOUNDS = {
  floor: ${has('AUTHOR') ? 'AUTHOR.FLOOR' : 'null'},
  idleFloor: ${has('AUTHOR') ? 'AUTHOR.IDLE_FLOOR' : 'null'},
}
`,
)
const { question, BOUNDS } = await import(copy).finally(() => unlinkSync(copy))

// With --bounds, the sizes from which the checkout asks at all, as JSON:
// `floor` at a turn end and `idleFloor` in an idle session. A bound the
// checkout doesn't have is null.
if (bounds) {
  process.stdout.write(JSON.stringify(BOUNDS))
  process.exit(0)
}

const params = /^function question\(([^)]*)\)/.exec(question.toString())![1]
const names = params.split(',').map(p => p.trim().split(/[\s:=?]/)[0])
const values: Record<string, unknown> = {
  reply: await Bun.stdin.text(),
  tokens: Number(tokens),
  idleMinutes: idle === undefined ? undefined : Number(idle),
  figures: undefined,
  instructions,
}
const unknown = names.filter(name => !(name in values))
if (unknown.length > 0)
  throw new Error(`question() takes parameters this script doesn't know: ${unknown}`)
process.stdout.write(question(...names.map(name => values[name])))
