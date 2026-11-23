// Says when a newer sparkjoy is out that Claude Code won't install on its own,
// once a day.
//
// Claude Code installs a newer version of a plugin only when it refreshes the
// plugin's marketplace: when the user asks, or, for a marketplace with auto-update
// on, in the background after a session's first message. Auto-update is off by
// default for a marketplace added from GitHub, and a marketplace can't turn it on.
// Otherwise Claude Code doesn't look for a newer version, so an install with
// auto-update off stays at the commit it was installed at
// (https://code.claude.com/docs/en/plugins/loading.md, "When auto-update runs").
//
// So when a session with a person at the prompt starts, where sparkjoy was
// installed from its own repository's marketplace and auto-update is off for it,
// sparkjoy asks GitHub how far the commit the session runs is behind the
// repository's default branch. If it is behind, sparkjoy says by how many days
// and commits in the transcript, with the command that updates it, and suggests
// asking Claude to turn auto-update on, which Claude does by setting `autoUpdate`
// on the marketplace's entry. It asks GitHub once a day across sessions, so it
// says so once a day. Two sessions that start within the same few milliseconds
// can both ask, as `$.store` has no write that only one of them can win.

import type { EngineInterface } from 'claude-code'

const REPOSITORY = 'max-sixty/sparkjoy'

// When sparkjoy last asked GitHub, in `$.store`, as milliseconds since the epoch.
// A request that fails is not tried again until the next day either.
const ASKED = 'update-check'

const DAY = 24 * 60 * 60_000

export async function checkForUpdate($: EngineInterface) {
  try {
    const install = installOf($.plugin.root)
    if (install === null || (await isQuiet($))) return
    if (!(await isUpdatedByHand($, install))) return
    const now = await $.clock.now()
    const asked = await $.store.get(ASKED)
    if (typeof asked === 'number' && now - asked < DAY) return
    await $.store.set(ASKED, now)
    const behind = await newerCommits($, install.commit)
    if (behind === null) return
    const days = Math.floor((now - behind.since) / DAY)
    const age = days === 0 ? 'less than a day' : days === 1 ? '1 day' : `${days} days`
    const commits = behind.count === 1 ? '1 commit' : `${behind.count} commits`
    $.ui.log(
      `This version is ${age} out of date, ${commits} behind. /plugin marketplace update ${install.marketplace} updates it. To keep it up to date, ask Claude to turn on auto-update for sparkjoy.`,
    )
  } catch (error) {
    $.ui.log(`Checking for a newer version failed: ${error}`, { to: 'debug' })
  }
}

// Where Claude Code installed the copy at `root`, from the path it gives an
// install: <plugins>/cache/<marketplace>/<plugin>/<version>. sparkjoy's manifest
// sets no version, so the version is the first 12 characters of the commit
// installed (https://code.claude.com/docs/en/plugins/loading.md, "Find plugins
// on disk" and "How Claude Code computes the version"). Null for a copy that
// Claude Code reads from a folder, as with `--plugin-dir`, which no update moves,
// and for an install named by a version the manifest sets: setting one turns the
// check off until it compares versions.
const INSTALL = /^(.*)[\\/]cache[\\/]([^\\/]+)[\\/][^\\/]+[\\/]([0-9a-f]{12})$/

export function installOf(root: string) {
  const match = INSTALL.exec(root)
  if (match === null) return null
  // Every group takes part in every match.
  const [, plugins, marketplace, commit] = match as unknown as [
    string,
    string,
    string,
    string,
  ]
  return { plugins, marketplace, commit }
}

type Install = NonNullable<ReturnType<typeof installOf>>

// Whether the user has told Claude Code not to look for updates
// (DISABLE_UPDATES, DISABLE_AUTOUPDATER) or not to make requests it doesn't need
// (CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC). Claude Code reads the last as set
// at any value; sparkjoy reads all three that way.
async function isQuiet($: EngineInterface) {
  return Boolean(
    (await $.env.get('DISABLE_UPDATES')) ||
    (await $.env.get('DISABLE_AUTOUPDATER')) ||
    (await $.env.get('CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC')),
  )
}

// A marketplace's entry in a source of settings' `extraKnownMarketplaces`, or in
// known_marketplaces.json, which records each marketplace Claude Code has
// fetched.
type Entry = { source?: unknown; autoUpdate?: unknown } | undefined

// Whether the install comes from sparkjoy's own marketplace on its default
// branch, with auto-update off where the user can turn it on. Claude Code takes
// `autoUpdate` from the marketplace's entry in the settings, then from its entry
// in known_marketplaces.json, which the item in /plugin writes, and otherwise
// leaves it off (https://code.claude.com/docs/en/plugins/loading.md, "Which
// marketplaces and plugins auto-update"). It is false where Claude Code may
// install a commit other than the default branch's: a marketplace from another
// repository, such as a fork or a mirror, or one that names a branch or tag. It
// is false too where managed settings or `--settings` set `autoUpdate`, which
// /plugin can't change.
async function isUpdatedByHand($: EngineInterface, { plugins, marketplace }: Install) {
  const entry = (marketplaces: unknown) =>
    (marketplaces as Record<string, Entry> | undefined)?.[marketplace]
  const autoUpdate = (e: Entry) =>
    typeof e?.autoUpdate === 'boolean' ? e.autoUpdate : undefined
  for (const source of ['policy', 'flag'] as const) {
    const settings = await $.settings.read({ source })
    if (autoUpdate(entry(settings.extraKnownMarketplaces)) !== undefined) return false
  }
  const declared = entry(await $.settings.read({ key: 'extraKnownMarketplaces' }))
  const known = entry(JSON.parse(await $.fs.read(`${plugins}/known_marketplaces.json`)))
  return (
    isOwnRepository(known?.source) &&
    (declared === undefined || isOwnRepository(declared.source)) &&
    !(autoUpdate(declared) ?? autoUpdate(known) ?? false)
  )
}

// Whether a marketplace source, as Claude Code records it, is sparkjoy's
// repository on its default branch: `github` names the repository, `git` its URL,
// and either names a branch or tag as `ref`.
function isOwnRepository(source: unknown) {
  const { source: kind, repo, url, ref } = (source ?? {}) as Record<string, unknown>
  const isOwn =
    kind === 'github'
      ? typeof repo === 'string' && repo.toLowerCase() === REPOSITORY
      : kind === 'git' && typeof url === 'string' && GIT_URL.test(url)
  return isOwn && ref === undefined
}

const GIT_URL =
  /^(?:https:\/\/github\.com\/|git@github\.com:)max-sixty\/sparkjoy(?:\.git)?\/?$/i

// The commits on the repository's default branch that `commit` lacks: how many,
// and when the first of them was made, in milliseconds since the epoch. Null
// when it lacks none. GitHub's API refuses a request with no User-Agent, and it
// lists a comparison's commits oldest first, so a page of one holds the first.
async function newerCommits($: EngineInterface, commit: string) {
  const response = await $.http.fetch(
    `https://api.github.com/repos/${REPOSITORY}/compare/${commit}...HEAD?per_page=1`,
    { headers: { accept: 'application/vnd.github+json', 'user-agent': 'sparkjoy' } },
  )
  if (!response.ok)
    throw new Error(
      `GitHub answered ${response.status}: ${response.text.slice(0, 200)}`,
    )
  const { ahead_by: count, commits } = JSON.parse(response.text) as {
    ahead_by: number
    commits: { commit: { committer: { date: string } } }[]
  }
  const first = commits[0]
  if (first === undefined) return null
  return { count, since: Date.parse(first.commit.committer.date) }
}
