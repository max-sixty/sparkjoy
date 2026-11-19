"""Copies docs/cost-model.md into the README's Cost model section, collapsed, so the model reads both
on its own page and in the README. Edit the doc, then run `python3 docs/embed.py`; `--check` exits 1
when the README's copy is out of date."""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
START = "<!-- Generated from docs/cost-model.md by docs/embed.py; edit that file. -->"
END = "<!-- End of the generated copy. -->"


def section(doc):
    """The doc as the README holds it: under the README's own `# Cost model`, so the doc's title goes,
    with paths from the repo root. GitHub gives headings inside a collapsed section no anchors, so a
    link to one of the doc's sections opens the doc's own page there. GitHub renders display maths only outside
    HTML blocks, and inline maths inside them too, so each display formula becomes a paragraph of
    inline maths in display style."""
    body = doc.split("\n", 1)[1].lstrip()
    body = re.sub(r"^```math\n(.*?)\n```$", lambda m: f"$`\\displaystyle {m[1]}`$", body, flags=re.M | re.S)
    body = body.replace('"../', '"').replace("](../", "](").replace("](#", "](docs/cost-model.md#")
    return f"{START}\n<details>\n<summary>The cost model</summary>\n\n{body.rstrip()}\n\n</details>\n{END}"


def embedded(readme, doc):
    start, end = readme.index(START), readme.index(END) + len(END)
    return readme[:start] + section(doc) + readme[end:]


if __name__ == "__main__":
    path = ROOT / "README.md"
    readme, new = path.read_text(), embedded(path.read_text(), (ROOT / "docs/cost-model.md").read_text())
    if "--check" in sys.argv:
        sys.exit(readme != new)
    path.write_text(new)
