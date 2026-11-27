#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy>=2.1"]
# ///
"""Writes each case's request, as the plugin's fork would send it, from my
Claude Code transcripts, for publishing.

    uv run --script evals/build.py [case-id ...]

Claude Code records each request a session sends in the session's transcript
(the `api-request`, `api-request-shape` and `api-request-blob` lines). A
case's turn end is the line of its reply in one of my sessions (`cases.json`).
`fork_request` rebuilds the request that reply answered: its parameters,
system prompt and tools as recorded, and its messages as
`fit.request_messages` rebuilds them, each checked against the hash the record
gives it, with the reply after them. `run.py` sends that with the question
after it, as `$.model.fork` does. Only I can run the build, since the
transcripts are mine; it is here to show what the requests hold and to add
cases.

The requests are published, in the evals' data repository
(`sparkjoy_assets.py`), so before writing one the build (`published`)
removes:

- each sentence that names my work outside open source, and the whole list
  item where that sentence opens one, such as a skill in Claude Code's
  listing. The terms are in a file of mine outside the repository (`TERMS`),
  one to a line, since a list here would name them, hashed or not;
- email addresses, other than those that name no person, private hostnames,
  page tokens and credentials;
- the images `OMITTED_IMAGES` names;
- every thinking block. Its signature carries reasoning the build can't read,
  and covers all that comes before it, so after any removal the API would
  drop it anyway.

It then searches every string again for the terms, as plain text rather than
words, and for Claude Code's own source, and stops on any it finds. It prints
how many sentences it removed from each request and how many images the
request holds. It can't read images, so each request waits, named for the hash
of its JSON, until `sparkjoy_assets.py publish` publishes it after a look at
each new image.
"""

import base64
import functools
import gzip
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "model"))
import fit  # noqa: E402
import sparkjoy_assets  # noqa: E402

TERMS = Path.home() / ".claude/sparkjoy/private-terms"

# The first 12 hex digits of sha256 of each image left out: two captures that
# show a local page's token.
OMITTED_IMAGES = {"d41e653d1e22", "6ecf715fb997"}
OMITTED_IMAGE = "[An image is omitted from this request.]"

THINKING = ("thinking", "redacted_thinking")

# Addresses that name no person.
PUBLIC_EMAIL = re.compile(
    r"(?i)^git@github\.com$|^noreply@|@users\.noreply\.github\.com$|@example\.com$|\.example$"
)
EMAIL = re.compile(
    r"\b[A-Za-z0-9](?:[A-Za-z0-9._%+-]*[A-Za-z0-9])?@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"
)
SCRUB = [
    (re.compile(r"\b[\w-]+\.tail[0-9a-f]{4,}\.ts\.net\b"), "localhost"),
    (re.compile(r"(?<=://)[\w-]+\.local\b"), "localhost"),
    (re.compile(r"([?&]t=)[A-Za-z0-9_-]{16,}"), r"\1<token>"),
    (re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{16,}"), "<api-key>"),
    (
        re.compile(
            r"\b(?:ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}"
        ),
        "<github-token>",
    ),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "<slack-token>"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "<aws-key>"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}"), "Bearer <token>"),
    (re.compile(r"\bop://[^\s\"'`)]+"), "<1password-reference>"),
]
# Claude Code ships as a bundle under its own license; a session that greps
# the installed binary pulls minified source into the transcript.
CLAUDE_CODE_SOURCE = re.compile(r"\btengu_\w+")

WORD = re.compile(r"[a-z0-9]+")
PARAGRAPH = re.compile(r"\n[ \t]*\n")
ITEM = re.compile(r"\n[ \t]*(?:[-*+]|\d+\.)[ \t]")
SENTENCE_END = re.compile(r"[.!?][\"')\]`*]*(?=\s|$)")


@functools.cache
def terms() -> frozenset[str]:
    """The private terms, each as its lowercase words joined by single spaces."""
    if not TERMS.is_file():
        sys.exit(f"{TERMS} is missing: it lists the terms the build removes.")
    lines = (
        " ".join(WORD.findall(line.lower())) for line in TERMS.read_text().splitlines()
    )
    return frozenset(line for line in lines if line)


def private_spans(text: str) -> list[tuple[int, int]]:
    """Where in `text` the private terms are, as whole words."""
    words = [(m.start(), m.end(), m.group()) for m in WORD.finditer(text.lower())]
    longest = max(len(term.split()) for term in terms())
    spans = []
    for n in range(1, longest + 1):
        for i in range(len(words) - n + 1):
            if " ".join(w for _, _, w in words[i : i + n]) in terms():
                spans.append((words[i][0], words[i + n - 1][1]))
    return spans


def unit(text: str, start: int, end: int) -> tuple[int, int]:
    """The span to remove for a private term at `start:end`: its sentence, or
    the list item that sentence opens."""
    paragraph = max([0] + [m.end() for m in PARAGRAPH.finditer(text, 0, start)])
    # The text past `start` is searched too, as a sentence ends only where a
    # space or the end of the text follows.
    left = max(
        [paragraph]
        + [m.end() for m in SENTENCE_END.finditer(text, paragraph) if m.end() <= start]
    )
    items = [m for m in ITEM.finditer(text, 0, start)]
    stops = [len(text)] + [
        m.start() for p in (PARAGRAPH, ITEM) if (m := p.search(text, end))
    ]
    if items and items[-1].end() >= left:
        # The sentence opens a list item: the item goes, from its newline.
        return items[-1].start(), min(stops)
    sentence = SENTENCE_END.search(text, end)
    right = min(stops + ([sentence.end()] if sentence else []))
    if left == paragraph:
        # It opens its paragraph: the space after it goes too.
        while right < len(text) and text[right] in " \t\n":
            right += 1
    return left, right


def scrub_text(text: str, removed: list[str]) -> str:
    """`text` without its private sentences, which go on `removed`, and with
    emails, hostnames, tokens and credentials replaced."""
    while spans := private_spans(text):
        left, right = unit(text, *spans[0])
        removed.append(text[left:right])
        text = text[:left] + text[right:]
    text = EMAIL.sub(
        lambda m: m.group() if PUBLIC_EMAIL.search(m.group()) else "user@example.com",
        text,
    )
    for pattern, replacement in SCRUB:
        text = pattern.sub(replacement, text)
    return text


def published(request: dict) -> tuple[dict, list[str]]:
    """`request` as it can be published, and the sentences taken out of it."""
    removed = []
    return scrub(request, removed), removed


def scrub(value, removed: list[str]):
    """`value` with what mustn't be published removed from every string in it
    (`scrub_text`), the images OMITTED_IMAGES names replaced by a line saying
    so, and every thinking block dropped."""
    if isinstance(value, str):
        return scrub_text(value, removed)
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, dict) and item.get("type") in THINKING:
                continue
            item = scrub(item, removed)
            if (
                isinstance(item, dict)
                and item.get("type") == "text"
                and not item["text"].strip()
            ):
                continue
            out.append(item)
        return out
    if isinstance(value, dict):
        if value.get("type") == "image" and value["source"].get("type") == "base64":
            digest = hashlib.sha256(
                base64.b64decode(value["source"]["data"])
            ).hexdigest()
            if digest[:12] in OMITTED_IMAGES:
                return {"type": "text", "text": OMITTED_IMAGE}
            return value
        return {k: scrub(v, removed) for k, v in value.items()}
    return value


def strings(value):
    """Every string in a decoded JSON value but images' data."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, dict) and value.get("type") != "image":
        for item in value.values():
            yield from strings(item)


def is_id(text: str, start: int, end: int) -> bool:
    """Whether `start:end` lies inside an opaque id, such as a tool call's: a
    run of 20 or more letters, digits and underscores that holds a digit."""
    while start > 0 and (text[start - 1].isalnum() or text[start - 1] == "_"):
        start -= 1
    while end < len(text) and (text[end].isalnum() or text[end] == "_"):
        end += 1
    token = text[start:end]
    return len(token) >= 20 and any(c.isdigit() for c in token)


def left_over(request: dict) -> list[str]:
    """What a search finds in `request` that mustn't be published: the private
    terms anywhere in the text, inside words too, emails that name a person,
    and Claude Code's source."""
    found = []
    joined = [r"[^a-z0-9]+".join(map(re.escape, term.split())) for term in terms()]
    term = re.compile("|".join(joined))
    for text in strings(request):
        lower = text.lower()
        found += [
            f"private term at {text[max(0, m.start() - 40) : m.end() + 40]!r}"
            for m in term.finditer(lower)
            if not is_id(lower, m.start(), m.end())
        ]
        found += [
            f"email {m.group()}"
            for m in EMAIL.finditer(text)
            if not PUBLIC_EMAIL.search(m.group())
        ]
        found += [
            f"Claude Code source at {text[max(0, m.start() - 40) : m.end() + 40]!r}"
            for m in CLAUDE_CODE_SOURCE.finditer(text)
        ]
    return found


def uncached(value):
    """`value` without the cache breakpoints the main thread placed."""
    if isinstance(value, dict):
        return {k: uncached(v) for k, v in value.items() if k != "cache_control"}
    if isinstance(value, list):
        return [uncached(v) for v in value]
    return value


def entries_of(case: dict) -> list[dict]:
    """The case's session, up to its turn's reply."""
    session, end = case["source"]["session"], case["source"]["line"]
    path = next(fit.PROJECTS.glob(f"*/{session}.jsonl"), None)
    if path is None:
        raise LookupError(f"no transcript of session {session}")
    entries = []
    with path.open() as f:
        for n, raw in enumerate(f):
            if n > end:
                break
            entries.append(json.loads(raw))
    return entries


def fork_request(entries: list[dict]) -> dict:
    """The recorded request the turn's reply answered, with the reply after its
    messages and the main thread's cache breakpoints removed."""
    shapes = {
        e["shapeHash"]: e["shape"]
        for e in entries
        if e.get("type") == "api-request-shape"
    }
    requests, messages = fit.request_messages(entries)
    reply = entries[-1]
    if "requestRef" not in reply:
        raise LookupError("its session has no request records")
    request = requests[reply["requestRef"]]
    r = request
    while r.get("base"):
        if r["base"] not in requests:
            raise LookupError(f"the record of request {r['base']} is missing")
        r = requests[r["base"]]
    rebuilt = messages(request["id"])
    if None in rebuilt:
        raise LookupError(
            f"message {rebuilt.index(None)} of the recorded request is in no blob or line"
        )
    blocks = [
        b
        for e in entries
        if e.get("type") == "assistant"
        and e["message"].get("id") == reply["message"]["id"]
        for b in e["message"]["content"]
    ]
    shape = shapes[request["shapeHash"]]
    return uncached(
        {
            # The Claude Code release that recorded the request.
            "version": request["version"],
            "params": request["params"],
            "system": shape["system"],
            "tools": shape["tools"],
            "messages": [*rebuilt, {"role": "assistant", "content": blocks}],
        }
    )


def images(value) -> int:
    if isinstance(value, list):
        return sum(images(v) for v in value)
    if isinstance(value, dict):
        return (
            1
            if value.get("type") == "image"
            else sum(images(v) for v in value.values())
        )
    return 0


def build(case: dict) -> Path:
    request, removed = published(fork_request(entries_of(case)))
    if found := left_over(request):
        raise SystemExit(f"{case['id']}: " + "\n".join(found))
    body = json.dumps(request, ensure_ascii=False).encode()
    target = sparkjoy_assets.wait(
        f"evals/requests/{case['id']}",
        body,
        # mtime=0, and no file name in the header, so that an unchanged
        # request writes the same bytes.
        gzip.compress(body, compresslevel=9, mtime=0),
        ".json.gz",
    )
    print(
        f"{case['id']}: {target.stat().st_size / 1e6:.1f} MB, {len(removed)} sentences"
        f" ({sum(map(len, removed))} characters) removed, {images(request)} images"
    )
    return target


def main() -> None:
    cases = json.loads((HERE / "cases.json").read_text())
    wanted = set(sys.argv[1:])
    for case in cases:
        if not wanted or case["id"] in wanted:
            build(case)


if __name__ == "__main__":
    main()
