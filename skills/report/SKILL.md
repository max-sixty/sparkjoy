---
name: report
description: Shows a page of what sparkjoy did in the user's Claude Code sessions, with each verdict, what its compactions saved, and the thresholds fitted to them. Use for "sparkjoy report", "what has sparkjoy done", or "how much did compacting save".
---

# sparkjoy report

Run the report script. It reads the user's transcripts in `~/.claude/projects` (or
`$CLAUDE_CONFIG_DIR/projects`), writes a self-contained HTML page to the temp
directory, prints a few lines of summary and the page's path, and opens the page:

```
uv run --script "${CLAUDE_PLUGIN_ROOT}/model/report.py"
```

It covers the last 14 days. When the user names another window, pass it as
`--since` and `--until`, each an ISO date or time, read as local time unless it
carries an offset. Pass `--no-open` where no browser can open the page, such as
on a remote machine, and give the user the path.

It needs `uv`, which fetches Python and numpy on first use. If `uv` is missing,
point the user to https://docs.astral.sh/uv/ to install it. The script reads
every transcript modified in the window at low priority, which takes about half
a minute for two weeks of heavy use, and longer for a longer window.

Reply with the summary it printed and the page's path. The page shows:

- **Sessions**: for each session where sparkjoy gave a verdict, its context per
  request with and without sparkjoy's compactions, and the cost of each.
- **Verdicts**: every verdict sparkjoy logged in the window, newest first,
  with the context size, whether a turn end or an idle session prompted it,
  and the model's reason. Under its old name, compact-when-done, it logged
  only the checks that compacted.
- **Idle returns**: each time a session left with 175k or more in context took
  a new message after an hour or more, and whether a compaction came in
  between.
- **Thresholds**: the turn-end threshold the cost model fits to these sessions,
  beside the 250k and 175k sparkjoy asks from.

The savings come from replaying each session's logged requests with sparkjoy's
compactions where it made them and without them, at list prices for the
session's model, so they are estimates; on a subscription the same tokens count
against usage limits rather than a bill. With no verdicts yet, the page says so:
a session picks sparkjoy up when it restarts.
