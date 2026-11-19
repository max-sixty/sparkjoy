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
    with paths from the repo root. GitHub lists a file's footnotes at its end, which in the README is
    past the collapsed section and outside it, so the doc's footnotes become numbered notes at the
    section's end."""
    body = doc.split("\n", 1)[1].lstrip()
    body = body.replace('"../', '"').replace("](../", "](")
    notes = dict(re.findall(r"^\[\^(\w+)\]: (.*?)(?:\n\n|\Z)", body, flags=re.M | re.S))
    body = re.sub(r"^\[\^\w+\]: .*?(?:\n\n|\Z)", "", body, flags=re.M | re.S)
    order = list(dict.fromkeys(re.findall(r"\[\^(\w+)\]", body)))
    body = re.sub(r"\[\^(\w+)\]", lambda m: f"<sup>{order.index(m[1]) + 1}</sup>", body)
    if order:
        body = body.rstrip() + "\n\n## Notes\n\n" + "\n".join(
            f"{n}. " + re.sub(r"\s*\n\s*", " ", notes[key]) for n, key in enumerate(order, 1))
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
