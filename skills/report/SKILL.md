---
name: report
description: Shows a page of what sparkjoy did in the user's Claude Code sessions, with each verdict, what its compactions saved, and the thresholds fitted to them. Use for "sparkjoy report", "what has sparkjoy done", or "how much did compacting save".
user-invocable: false
---

# sparkjoy report

Run the report script. It reads the user's transcripts in `~/.claude/projects` (or
`$CLAUDE_CONFIG_DIR/projects`), writes a self-contained HTML page to the temp
directory, prints a few lines of summary and the page's path, and opens the page:

```
uv run --script "${CLAUDE_PLUGIN_ROOT}/model/report.py" --figures
```

With `--figures` it also saves the floors sparkjoy asks from and the figures its
question states, such as the context a compaction leaves and the requests in a
turn, fitted to the same sessions, to `sparkjoy/figures.json` in the same
directory as `projects`. From the next turn end, the plugin uses them in place
of the author's, except for a floor the user set in sparkjoy's settings.

It covers the last 14 days. When the user names another window, pass it as
`--since` and `--until`, each an ISO date or time, read as local time unless it
carries an offset. Pass `--no-open` where no browser can open the page, such as
on a remote machine, and give the user the path.

It needs `uv`, which fetches Python and numpy on first use. If `uv` is missing,
point the user to https://docs.astral.sh/uv/ to install it. The script reads
every transcript modified in the window at low priority, which takes about half
a minute for two weeks of heavy use, and longer for a longer window.

Reply with the summary it printed and the page's path. Both open on the
result: what sparkjoy's compactions saved; or, where it made none, such as on
a first run, what compacting from the floors fitted to the same sessions would
have saved; or why it had nothing to do. The page then shows:

- **Sessions**: for each session where sparkjoy gave a verdict, its context per
  request with and without sparkjoy's compactions, and the cost of each.
- **Verdicts**: every verdict sparkjoy logged in the window, newest first,
  with the context size, whether a turn end or an idle session prompted it,
  whether its question missed the prompt cache, and the model's reason. The
  checks' cost in the savings prices each miss's unread input as a cache write. Under its old name, compact-when-done, it logged
  only the checks that compacted.
- **Thresholds**: the pair of floors, at turn ends and in idle sessions, that
  costs least for these sessions, beside sparkjoy's defaults of 250k and 175k
  and never compacting, with what each costs and what each saves per unit of
  work against never compacting, at the cost model's base case for how much
  past context is worth to the work.
- **Fitted figures**: the floors and figures it saved. A window without
  compactions has nothing to fit them to, and the plugin keeps the ones it
  had.
- **Idle returns**: each time a session left with 175k or more in context took
  a new message after an hour or more, and whether a compaction came in
  between.

The savings come from replaying each session's logged requests with sparkjoy's
compactions where it made them and without them, at list prices for the
session's model, so they are estimates; on a subscription the same tokens count
against usage limits rather than a bill. The page leaves out sessions that
Claude Code compacted before 200k, as it does in a 200k context window, since
sparkjoy acts only in sessions with a 1M window, and says so.
