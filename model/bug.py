#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy>=2.1"]
# ///
"""Drafts a bug report on sparkjoy from one session's transcript.

    uv run --script --locked model/bug.py <session id>

Its dependencies are locked in bug.py.lock, which `uv lock --script model/bug.py` updates.

It writes the draft, in Markdown, to the temp directory and prints its path and the
repository to file it in. The draft has a section for the user's account of what went
wrong and one for sparkjoy's settings, which the model fills in, and then the evidence:
the releases of sparkjoy and Claude Code, the last lines sparkjoy wrote in the session,
each beside the session's requests before and after it, and the session's compactions.
It quotes nothing of the conversation but sparkjoy's lines, whose reasons describe the
work, and gives only counts of tokens for the requests.
"""

import argparse
import html
import json
import platform
import tempfile
from datetime import datetime
from pathlib import Path

import fit
import numpy as np
from report import duration, tokens, when

ROOT = Path(__file__).resolve().parents[1]
LINES = 20  # the most recent lines of sparkjoy's the draft quotes
TTL = {3600: "one-hour", 300: "five-minute"}


def request(s, j, t):
    """The session's `j`th request, beside a line written at `t`."""
    if not 0 <= j < len(s.t):
        return "none"
    side = "earlier" if s.t[j] <= t else "later"
    return f"{duration(abs(s.t[j] - t))} {side}: {tokens(s.size[j])}, {tokens(s.read[j])} read from the cache"


def draft(s):
    """The bug report's Markdown, with placeholders for what only the user and the model know."""
    shown = s.lines[-LINES:]
    zone = datetime.fromtimestamp(shown[0][0] if shown else s.t[-1]).astimezone()
    rows = []
    for t, text in shown:
        # The last request before the line.
        j = int(np.searchsorted(s.t, t, side="right")) - 1
        # A table cell is one line of Markdown, which renders HTML: a model's reply can hold
        # several lines, a pipe ends the cell, and a tag would vanish.
        cell = html.escape(text, quote=False).replace("|", r"\|").replace("\n", "<br>")
        rows.append(
            f"| {when(t)} | {cell} | {request(s, j, t)} | {request(s, j + 1, t)} |"
        )
    since = shown[0][0] if shown else -np.inf
    compactions = [
        f"| {when(t)} | {by} | {tokens(before) if before else 'unknown'} |"
        for t, by, before in s.compactions
        if t >= since
    ]
    return "\n".join(
        [
            "## What happened",
            "",
            "_What the user saw and expected, and which of the lines below it concerns._",
            "",
            "## Settings",
            "",
            "_What `/sparkjoy` says._",
            "",
            "## Evidence",
            "",
            f"sparkjoy {ROOT.name}, Claude Code {s.version}, {platform.platform(terse=True)}. "
            f"The session ran on {s.model}, with a {TTL[s.ttl]} prompt cache.",
            "",
            f"The session's last {len(shown)} lines from sparkjoy, in local time ({zone:%Z}), each with "
            "the session's request that started before it and the one after it: how far from the "
            "line it started, its context, and how much of that it read from the prompt cache.",
            "",
            "| Time | Line | Request before | Request after |",
            "| --- | --- | --- | --- |",
            *rows,
            "",
            *(
                [
                    "Compactions since the first of those lines:",
                    "",
                    "| Time | By | Context before |",
                    "| --- | --- | --- |",
                    *compactions,
                    "",
                ]
                if compactions
                else []
            ),
            "Drafted with `/sparkjoy bug`.",
            "",
        ]
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("session", help="the session's id")
    fit.projects_arg(ap)
    a = ap.parse_args()
    paths = list(a.projects.glob(f"*/{a.session}.jsonl"))
    if not paths:
        raise SystemExit(f"No transcript of session {a.session} in {a.projects}")
    s = fit.transcript(paths[0])
    if s is None:
        raise SystemExit(
            f"{paths[0]} holds no interactive session of more than one request"
        )
    fd, path = tempfile.mkstemp(prefix=f"sparkjoy-bug-{a.session[:8]}-", suffix=".md")
    with open(fd, "w") as f:
        f.write(draft(s))
    manifest = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
    print(path)
    print(f"File it at {manifest['repository']}/issues/new")


if __name__ == "__main__":
    main()
