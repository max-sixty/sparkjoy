"""Copies docs/cost-model.md into the README's Cost model section, collapsed, so the model reads both
on its own page and in the README, and the doc's account of what sparkjoy saves into the README's
What it saves. Edit the doc, then run `python3 docs/embed.py`; `--check` exits 1 when the README's copies
are out of date."""

import re
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


def embedded(readme, doc):
    start, end = readme.index(START), readme.index(END) + len(END)
    readme = readme[:start] + section(doc) + readme[end:]
    saves = from_root(doc[doc.index(SAVES) + len(SAVES) : doc.index(SAVES_END)].strip())
    start = readme.index(SAVES_START)
    end = readme.index(SAVES_END, start) + len(SAVES_END)
    return f"{readme[:start]}{SAVES_START}\n\n{saves}\n\n{SAVES_END}{readme[end:]}"


if __name__ == "__main__":
    path = ROOT / "README.md"
    readme, new = (
        path.read_text(),
        embedded(path.read_text(), (ROOT / "docs/cost-model.md").read_text()),
    )
    if "--check" in sys.argv:
        sys.exit(readme != new)
    path.write_text(new)
