// Prints the question a checkout of sparkjoy asks, for one case, so the evals
// ask exactly what the plugin would.
//
//   bun evals/question.ts <checkout>/hooks/register.ts <tokens> [idle-minutes] < reply
//
// `question()` isn't exported, so this imports a copy of register.ts that
// exports it. It passes each parameter by name: `reply`, `tokens` (the
// context's size), `idleMinutes` and `figures`, so a checkout whose question
// takes fewer of them is called with just those. `figures` is left out, so
// the question states the author's: the cases come from his sessions.

import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const [registerPath, tokens, idleMinutes] = process.argv.slice(2)
const copy = join(mkdtempSync(join(tmpdir(), 'sparkjoy-question-')), 'register.ts')
writeFileSync(copy, `${readFileSync(registerPath, 'utf8')}\nexport { question }\n`)
const { question } = await import(copy)

const params = /^function question\(([^)]*)\)/.exec(question.toString())![1]
const names = params.split(',').map(p => p.trim().split(/[\s:=?]/)[0])
const values: Record<string, unknown> = {
  reply: await Bun.stdin.text(),
  tokens: Number(tokens),
  idleMinutes: idleMinutes === undefined ? undefined : Number(idleMinutes),
  figures: undefined,
}
const unknown = names.filter(name => !(name in values))
if (unknown.length > 0) throw new Error(`question() takes parameters this script doesn't know: ${unknown}`)
process.stdout.write(question(...names.map(name => values[name])))
