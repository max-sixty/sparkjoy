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
  values: { idle, instructions },
} = parseArgs({
  allowPositionals: true,
  options: { idle: { type: 'string' }, instructions: { type: 'string' } },
})
const copy = resolve(dirname(registerPath), `.question-${process.pid}.ts`)
writeFileSync(copy, `${readFileSync(registerPath, 'utf8')}\nexport { question }\n`)
const { question } = await import(copy).finally(() => unlinkSync(copy))

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
