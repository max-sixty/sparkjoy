// Says when a newer sparkjoy is out that Claude Code won't install on its own,
// at most once a day.
//
// Claude Code installs a newer version of a plugin only when it refreshes the
// plugin's marketplace: when the user asks, or, for a marketplace with auto-update
// on, in the background after a session's first message. Auto-update is off by
// default for every marketplace but Anthropic's own, and a marketplace can't turn
// it on. Otherwise Claude Code doesn't look for a newer version, so an install
// with auto-update off stays at the commit it was installed at
// (https://code.claude.com/docs/en/plugins/loading.md, "When auto-update runs").
//
// So when a session with a person at the prompt starts, and auto-update is off
// for sparkjoy's marketplace, sparkjoy asks GitHub for the commit on main. If
// that isn't the commit the session runs, it says so in the transcript, with the
// command that updates it and how to turn auto-update on. It checks at most once
// a day across sessions, so it says so at most once a day.

import type { EngineInterface } from 'claude-code'

const REPOSITORY = 'max-sixty/sparkjoy'

// When the last check began, in `$.store`, as milliseconds since the epoch. A
// check that fails is not tried again until the next day either.
const CHECKED = 'update-check'

const DAY = 24 * 60 * 60_000

export async function checkForUpdate($: EngineInterface) {
  try {
    const install = installOf($.plugin.root)
    if (install === null || (await isUpdatingOff($))) return
    const now = await $.clock.now()
    const checked = await $.store.get(CHECKED)
    if (typeof checked === 'number' && now - checked < DAY) return
    await $.store.set(CHECKED, now)
    if (await isAutoUpdating($, install)) return
    const latest = await mainCommit($)
    if (latest.startsWith(install.commit)) return
    const { commit, marketplace } = install
    $.ui.log(
      `A newer version is out. /plugin marketplace update ${marketplace} updates it. To keep it up to date, open /plugin, select ${marketplace} on the Marketplaces tab, and choose Turn on auto-update. What changed: https://github.com/${REPOSITORY}/compare/${commit}...main`,
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

// Whether the user has turned off Claude Code's plugin updates.
// DISABLE_UPDATES, DISABLE_AUTOUPDATER and
// CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC each turn them off unless
// FORCE_AUTOUPDATE_PLUGINS is set, and hide the auto-update setting. Claude Code
// reads a variable as set when it is 1, true, yes or on.
async function isUpdatingOff($: EngineInterface) {
  const isSet = (value: string | undefined) =>
    ['1', 'true', 'yes', 'on'].includes((value ?? '').toLowerCase().trim())
  const isOff =
    isSet(await $.env.get('DISABLE_UPDATES')) ||
    isSet(await $.env.get('DISABLE_AUTOUPDATER')) ||
    isSet(await $.env.get('CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC'))
  return isOff && !isSet(await $.env.get('FORCE_AUTOUPDATE_PLUGINS'))
}

// Whether auto-update is on for the install's marketplace, by the first of these
// that says: the marketplace's entry under `extraKnownMarketplaces` in the
// settings, then its entry in known_marketplaces.json, which the setting in
// /plugin writes. Where neither says, it is off, as for every marketplace but
// Anthropic's (https://code.claude.com/docs/en/plugins/loading.md, "Which
// marketplaces and plugins auto-update").
async function isAutoUpdating($: EngineInterface, { plugins, marketplace }: Install) {
  const said = (marketplaces: unknown) => {
    const entries = marketplaces as Record<string, { autoUpdate?: unknown }> | undefined
    const value = entries?.[marketplace]?.autoUpdate
    return typeof value === 'boolean' ? value : undefined
  }
  return (
    said(await $.settings.read({ key: 'extraKnownMarketplaces' })) ??
    said(JSON.parse(await $.fs.read(`${plugins}/known_marketplaces.json`))) ??
    false
  )
}

// The commit on main, by GitHub's API, which refuses a request with no
// User-Agent.
async function mainCommit($: EngineInterface) {
  const response = await $.http.fetch(
    `https://api.github.com/repos/${REPOSITORY}/commits/main`,
    { headers: { accept: 'application/vnd.github.sha', 'user-agent': 'sparkjoy' } },
  )
  const commit = response.text.trim()
  if (!response.ok || !/^[0-9a-f]{40}$/.test(commit))
    throw new Error(`GitHub answered ${response.status}: ${commit.slice(0, 200)}`)
  return commit
}
