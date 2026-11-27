---
name: bug
description: Drafts a bug report on sparkjoy from the session's transcript, and files it in sparkjoy's repository once the user agrees. Use for "/sparkjoy bug", "report a sparkjoy bug", or "file this with sparkjoy".
user-invocable: false
---

# sparkjoy bug report

`/sparkjoy bug` asks you to run this skill, and gives you the session's id and
sparkjoy's settings as `/sparkjoy` states them. If the user asked in their own
words instead, ask them to type `/sparkjoy bug`, which gives you both.

Draft the report with the script, passing the session's id. It writes the draft
to the temp directory, and prints its path and where to file it:

```
uv run --script --locked "${CLAUDE_PLUGIN_ROOT}/model/bug.py" <session id>
```

It needs `uv`, which fetches Python and numpy on first use. If `uv` is missing,
point the user to https://docs.astral.sh/uv/ to install it.

Read the draft. Its evidence lists the last lines sparkjoy wrote in the session,
which never reach your context, each beside the requests before and after it.
Fill in its two sections in italics:

- **What happened**: what the user says went wrong and expected, in their
  words, and the line it concerns. If they gave no account, describe the latest
  line of sparkjoy's that suggests `/sparkjoy bug`, or that sparkjoy followed
  with one that does, and ask whether that's the one.
- **Settings**: the quoted settings, whole.

Then show the user the draft's path, the title you'd give the issue, the
"What happened" section, and the lines of sparkjoy's it quotes. Tell them that
the reasons in those lines and any instructions in the settings describe their
work, so they can cut any before the report goes public. File
it only once they agree, with the repository the script printed:

```
gh issue create --repo <owner>/<repository> --title "<title>" --body-file <draft>
```

Where `gh` is missing or not signed in, give the user the draft's path and the
URL the script printed, to paste it there. Reply with the issue's URL once it
is filed.
