#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# ///
"""Fails if any fixture holds something that mustn't be published.

    uv run --script evals/check.py [fixture.jsonl.gz ...]

With no arguments it checks every fixture in `evals/fixtures/`. It prints each
hit with the fixture, the line and the text around it, and exits 1 on any.

It looks for home paths, email addresses, credentials and private hostnames by
pattern, and for words from the author's work outside open source by hash, so
this file doesn't name them: every run of one to three words in the text is
hashed and compared against `PRIVATE`.
"""

import gzip
import hashlib
import json
import re
import sys
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"

# The first 16 hex digits of sha256 of each private term, words lowercased and
# joined by single spaces.
PRIVATE = {
    "c46c8cd790f14759",  # 1-gram
    "84c138c531182b7a",  # 1-gram
    "f1a273fd2bbce7c4",  # 1-gram
    "b53dec8cfdda32c5",  # 1-gram
    "dcc8d7506b63db23",  # 1-gram
    "ebde709e306badca",  # 1-gram
    "9a84716ad16e0950",  # 3-gram
    "dfaac2e7f42ffb39",  # 2-gram
    "7497a650ed7adc47",  # 2-gram
    "6212ae3a4f7a32c0",  # 3-gram
    "67cc4eae595f8455",  # 2-gram
}

# Addresses that name no person; `build.py` writes the first for every other.
PUBLIC_EMAILS = {"user@example.com", "git@github.com", "noreply@anthropic.com", "noreply@github.com"}

PATTERNS = {
    # GitHub Actions' runner home is the same on every machine.
    "home path": re.compile(r"/(?:Users|home)/(?!runner\b)[a-z][\w.-]*|-Users-(?!USER-)[a-z]\w*-"),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"),
    "api key": re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{16,}"),
    "github token": re.compile(r"\b(?:ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}"),
    "slack token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "aws key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "bearer token": re.compile(r"\bBearer\s+(?!<)[A-Za-z0-9._~+/-]{20,}"),
    "1Password reference": re.compile(r"\bop://"),
    "private hostname": re.compile(r"\.ts\.net\b|\.tail[0-9a-f]{4,}\b|https?://[\w-]+\.local\b"),
    # Claude Code ships as a bundle under its own license; a session that
    # greps the installed binary pulls minified source into the transcript.
    "Claude Code source": re.compile(r"\btengu_\w+|\bstrings\b[^\n]{0,60}\bclaude\b|\bcc\.strings\b"),
    "page token": re.compile(r"[?&]t=(?!<)[A-Za-z0-9_-]{16,}"),
    "phone number": re.compile(r"(?<![\w.])(?:\+\d{1,3}[ .-])?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?![\w.])"),
}

WORD = re.compile(r"[a-z0-9]+")


def private_terms(text: str) -> list[str]:
    words = WORD.findall(text.lower())
    hits = []
    for n in (1, 2, 3):
        for i in range(len(words) - n + 1):
            gram = " ".join(words[i : i + n])
            if hashlib.sha256(gram.encode()).hexdigest()[:16] in PRIVATE:
                hits.append(gram)
    return hits


def hits(text: str) -> list[tuple[str, str]]:
    found = [("private term", t) for t in private_terms(text)]
    for name, pattern in PATTERNS.items():
        for m in pattern.finditer(text):
            if name == "email" and m.group(0).lower() in PUBLIC_EMAILS:
                continue
            start = max(0, m.start() - 40)
            found.append((name, text[start : m.end() + 40].replace("\n", " ")))
    return found


def strings(value):
    """Every string in a decoded JSON value, keys included."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from strings(item)


def check(path: Path) -> int:
    count = 0
    with gzip.open(path, "rt") as f:
        for n, line in enumerate(f, 1):
            # Decoded, so that an escape (`\n`, `@`) neither hides a
            # match nor makes one.
            found = {hit for text in strings(json.loads(line)) for hit in hits(text)}
            for name, context in sorted(found):
                print(f"{path.name}:{n}: {name}: {context}")
            count += len(found)
    return count


def main() -> None:
    paths = [Path(p) for p in sys.argv[1:]] or sorted(FIXTURES.glob("*.jsonl.gz"))
    total = sum(check(p) for p in paths)
    print(f"{total} hits in {len(paths)} fixtures")
    sys.exit(1 if total else 0)


if __name__ == "__main__":
    main()
