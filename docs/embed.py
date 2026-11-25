"""Writes the README's copies of what other files own: docs/cost-model.md, collapsed in the Cost model
section, so the model reads both on its own page and in the README; the doc's account of what sparkjoy
saves in Cost saving; and in The prompt, the question `question()` in hooks/register.ts asks at a turn end
and in an idle session, rendered by evals/question.ts with bun. Edit the owning file, then run
`python3 docs/embed.py`; `--check` exits 1 when the README's copies are out of date."""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
START = "<!-- Generated from docs/cost-model.md by docs/embed.py; edit that file. -->"
END = "<!-- End of the generated copy. -->"


def from_root(text):
    """`text` from the doc, with paths from the repo root, and links to the doc's sections opening the
    doc's own page there."""
    return (
        text.replace('"../', '"')
        .replace("](../", "](")
        .replace("](#", "](docs/cost-model.md#")
    )


def section(doc):
    """The doc as the README holds it: under the README's own `# Cost model`, so the doc's title goes,
    with paths from the repo root. GitHub gives headings inside a collapsed section no anchors, so a
    link to one of the doc's sections opens the doc's own page there. GitHub renders display maths only outside
    HTML blocks, and inline maths inside them too, so each display formula becomes a paragraph of
    inline maths in display style."""
    body = doc.split("\n", 1)[1].lstrip()
    body = re.sub(
        r"^```math\n(.*?)\n```$",
        lambda m: f"$`\\displaystyle {m[1]}`$",
        body,
        flags=re.M | re.S,
    )
    body = from_root(body)
    return f"{START}\n<details>\n<summary>The cost model</summary>\n\n{body.rstrip()}\n\n</details>\n{END}"


# The doc marks its account of what sparkjoy saves with these lines, and the README's copy of it sits
# between the generated ones.
SAVES, SAVES_END = (
    "<!-- What sparkjoy saves -->",
    "<!-- End of what sparkjoy saves. -->",
)
SAVES_START = "<!-- Generated from docs/cost-model.md's Results by docs/embed.py; edit that file. -->"


# Each copy of the question sits between one of these lines and QUESTION_END, as question() renders it
# for the arguments evals/question.ts takes after the context's size. `$CUSTOM_INSTRUCTIONS` stands
# where a user's `instructions` go.
QUESTIONS = {
    "<!-- Generated from question() in hooks/register.ts, at a turn end, by docs/embed.py; edit that"
    " function. -->": ["500000"],
    "<!-- Generated from question() in hooks/register.ts, in an idle session, by docs/embed.py; edit"
    " that function. -->": ["500000", "--idle", "55"],
}
QUESTION_END = "<!-- End of the generated question. -->"


def question(args):
    """The question in a fence of four backticks, which holds the fence of three inside it."""
    text = subprocess.run(
        [
            "bun",
            ROOT / "evals/question.ts",
            ROOT / "hooks/register.ts",
            *args,
            "--instructions",
            "$CUSTOM_INSTRUCTIONS",
        ],
        input="",
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return f"````\n{text}\n````"


def between(text, start, end, body):
    """`text` with what stands between the lines `start` and `end` replaced by `body`."""
    first = text.index(start)
    last = text.index(end, first) + len(end)
    return f"{text[:first]}{start}\n\n{body}\n\n{end}{text[last:]}"


def embedded(readme, doc):
    start, end = readme.index(START), readme.index(END) + len(END)
    readme = readme[:start] + section(doc) + readme[end:]
    saves = from_root(doc[doc.index(SAVES) + len(SAVES) : doc.index(SAVES_END)].strip())
    readme = between(readme, SAVES_START, SAVES_END, saves)
    for marker, args in QUESTIONS.items():
        readme = between(readme, marker, QUESTION_END, question(args))
    return readme


if __name__ == "__main__":
    path = ROOT / "README.md"
    readme, new = (
        path.read_text(),
        embedded(path.read_text(), (ROOT / "docs/cost-model.md").read_text()),
    )
    if "--check" in sys.argv:
        sys.exit(readme != new)
    path.write_text(new)
