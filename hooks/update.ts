// Says when a newer sparkjoy is out that Claude Code won't install on its own,
// at most once a day.
//
// Claude Code installs a newer version of a plugin only when it refreshes the
// plugin's marketplace: when the user asks, or, for a marketplace with auto-update
// on, in the background after a session's first message. Auto-update is off by
// default for a marketplace added from GitHub, and a marketplace can't turn it on.
// Otherwise Claude Code doesn't look for a newer version, so an install with
// auto-update off stays at the commit it was installed at
// (https://code.claude.com/docs/en/plugins/loading.md, "When auto-update runs").
//
// So when a session with a person at the prompt starts, and auto-update is off
// for sparkjoy's marketplace, sparkjoy asks GitHub for the commit the
// marketplace's source now names. If that isn't the commit the session runs, it
// says so in the transcript, with the command that updates it and how to turn
// auto-update on. It asks GitHub at most once a day across sessions, so it says
// so at most once a day. Two sessions that start within the same few
// milliseconds can both ask, as `$.store` has no write that only one of them can
// win.

import type { EngineInterface } from 'claude-code'

// When sparkjoy last asked GitHub, in `$.store`, as milliseconds since the epoch.
// A request that fails is not tried again until the next day either.
const ASKED = 'update-check'

const DAY = 24 * 60 * 60_000

export async function checkForUpdate($: EngineInterface) {
  try {
    const install = installOf($.plugin.root)
    if (install === null || (await isQuiet($))) return
    const source = await sourceToCheck($, install)
    if (source === null) return
    const now = await $.clock.now()
    const asked = await $.store.get(ASKED)
    if (typeof asked === 'number' && now - asked < DAY) return
    await $.store.set(ASKED, now)
    const latest = await commitOf($, source)
    if (latest.startsWith(install.commit)) return
    const { commit, marketplace } = install
    $.ui.log(
      `A newer version is out. /plugin marketplace update ${marketplace} updates it. To keep it up to date, open /plugin, select ${marketplace} on the Marketplaces tab, and choose Turn on auto-update. What changed: https://github.com/${source.repo}/compare/${commit}...${latest.slice(0, 12)}`,
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

// A marketplace's entry in the settings' `extraKnownMarketplaces` or in
// known_marketplaces.json, which records each marketplace Claude Code has
// fetched: its source, and whether it auto-updates.
type Entry = { source?: Source; autoUpdate?: unknown } | undefined
type Source = { source?: unknown; repo?: unknown; url?: unknown; ref?: unknown }

// The GitHub repository and ref the install's marketplace tracks, where auto-update
// is off for it and the user can turn it on. Claude Code takes `autoUpdate` from
// the marketplace's entry in the settings, then from its entry in
// known_marketplaces.json, which the item in /plugin writes, and otherwise leaves
// it off (https://code.claude.com/docs/en/plugins/loading.md, "Which marketplaces
// and plugins auto-update"). Null where auto-update is on, where the
// organization's managed settings set it either way, and where the source isn't
// a GitHub repository.
async function sourceToCheck($: EngineInterface, { plugins, marketplace }: Install) {
  const entry = (marketplaces: unknown) =>
    (marketplaces as Record<string, Entry> | undefined)?.[marketplace]
  const autoUpdate = (e: Entry) =>
    typeof e?.autoUpdate === 'boolean' ? e.autoUpdate : undefined
  const policy = await $.settings.read({ source: 'policy' })
  if (autoUpdate(entry(policy.extraKnownMarketplaces)) !== undefined) return null
  const declared = entry(await $.settings.read({ key: 'extraKnownMarketplaces' }))
  const known = entry(JSON.parse(await $.fs.read(`${plugins}/known_marketplaces.json`)))
  if (autoUpdate(declared) ?? autoUpdate(known) ?? false) return null
  return gitHubOf(known?.source)
}

// A marketplace source as Claude Code records it: `github` names the repository,
// `git` its URL, and either may name a branch or tag as `ref`, without which it
// tracks the default branch.
function gitHubOf(source: Source | undefined) {
  const repo =
    source?.source === 'github' && typeof source.repo === 'string'
      ? source.repo
      : source?.source === 'git' && typeof source.url === 'string'
        ? /(?:^|[/@])github\.com[/:]([^/]+\/[^/]+?)(?:\.git)?\/?$/.exec(source.url)?.[1]
        : undefined
  if (repo === undefined) return null
  return { repo, ref: typeof source?.ref === 'string' ? source.ref : 'HEAD' }
}

// The commit `ref` names in the repository now, by GitHub's API, which refuses
// a request with no User-Agent.
async function commitOf(
  $: EngineInterface,
  { repo, ref }: { repo: string; ref: string },
) {
  const response = await $.http.fetch(
    `https://api.github.com/repos/${repo}/commits/${encodeURIComponent(ref)}`,
    { headers: { accept: 'application/vnd.github.sha', 'user-agent': 'sparkjoy' } },
  )
  const commit = response.text.trim()
  if (!response.ok || !/^[0-9a-f]{40}$/.test(commit))
    throw new Error(`GitHub answered ${response.status}: ${commit.slice(0, 200)}`)
  return commit
}
