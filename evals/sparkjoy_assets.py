#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# ///
"""Fetches and publishes the evals' data, which sparkjoy keeps in
max-sixty/sparkjoy-assets so that neither its history nor an install of the
plugin carries it.

    uv run --script evals/sparkjoy_assets.py publish

That repository only ever gains files, each under a name no other file
takes: each case's request at `evals/requests/<case>/<hash>.json.gz`, named
for the hash of its JSON, and each batch of runs' results at
`evals/results/<started>-<random>.jsonl`. So its `main` holds every file ever
published, and nothing pins a commit of it. `evals/requests.json` here names
the hash of the request each case sends.

`fetch()` clones the repository into `.tmp/sparkjoy-assets` without file
contents, then checks out only `evals/results/`. Offline, it reads the clone as
it is. `read()` fetches a request the first time it is asked for. A new file
waits in `.tmp/sparkjoy-assets-unpublished`, at its path in the repository,
until it is published:

- `run.py` writes a running batch's results to a `.part` file there, locked
  while the batch runs. When the batch ends, it renames the file and
  publishes it, with any results an earlier batch didn't publish. That
  includes a batch a signal stopped, whose lock went with its process;
- `build.py` writes each request there, and `publish` publishes it, with any
  waiting results, once each new image has had a look. It then points
  `evals/requests.json` at it, so that the file names only published requests.

A publish commits the waiting files onto the repository's `main`, in an index
of its own, pushes the commit, and deletes them. Since no two files share a
name, it never conflicts with another publish; where another moved `main`
before its push, it builds its commit again on top. Where `main` holds a
request's name already, it holds the request, whatever bytes another gzip
made of it; for results, a publish stops before it would replace `main`'s
copy. A push that fails for any other reason leaves the files waiting.
"""

import contextlib
import fcntl
import json
import os
import secrets
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

ROOT = Path(__file__).parents[1]
REPOSITORY = "https://github.com/max-sixty/sparkjoy-assets.git"
CLONE = ROOT / ".tmp/sparkjoy-assets"
UNPUBLISHED = ROOT / ".tmp/sparkjoy-assets-unpublished"
POINTERS = ROOT / "evals/requests.json"
# The waiting files a publish takes: a running batch writes to a `.part` file
# and renames it to match only once it ends.
REQUESTS = "evals/requests/*/*.json.gz"
RESULTS = "evals/results/*.jsonl"


def git(*args: str, cwd: Path = CLONE, **kwargs) -> str:
    """Run git in `cwd`, the clone by default, and return what it prints. Its
    errors go to stderr."""
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
        **kwargs,
    ).stdout.strip()


def fetch() -> None:
    """Bring the clone up to the repository's `main`, cloning it first if it
    isn't here."""
    if CLONE.is_dir():
        try:
            git("pull", "--quiet", "--ff-only")
        except subprocess.CalledProcessError:
            print(f"Reading {CLONE} as it is.", file=sys.stderr)
        return
    CLONE.parent.mkdir(exist_ok=True)
    # Cloned beside the target and moved into place once checked out, so an
    # interrupted clone leaves nothing behind.
    with tempfile.TemporaryDirectory(dir=CLONE.parent) as scratch:
        clone = Path(scratch) / "clone"
        subprocess.run(
            [
                "git",
                "clone",
                "--quiet",
                "--filter=blob:none",
                "--sparse",
                REPOSITORY,
                str(clone),
            ],
            check=True,
        )
        git("sparse-checkout", "set", "evals/results", cwd=clone)
        clone.rename(CLONE)


def read(path: str) -> bytes | None:
    """The file at `path` in the repository, waiting or published, or None
    where there is none."""
    if (UNPUBLISHED / path).is_file():
        return (UNPUBLISHED / path).read_bytes()
    for fresh in (False, True):
        if fresh or not CLONE.is_dir():
            fetch()
        found = subprocess.run(
            ["git", "-C", str(CLONE), "cat-file", "blob", f"HEAD:{path}"],
            capture_output=True,
        )
        if found.returncode == 0:
            return found.stdout
    return None


def request(case: str) -> bytes | None:
    """The gzipped request `evals/requests.json` names for `case`, or None."""
    digest = json.loads(POINTERS.read_text()).get(case)
    return digest and read(f"evals/requests/{case}/{digest}.json.gz")


def results() -> list[Path]:
    """Every batch's results, published, waiting or still running, oldest
    first."""
    fetch()
    files = [
        *(CLONE / "evals/results").glob("*.jsonl"),
        *(UNPUBLISHED / "evals/results").glob("*.jsonl*"),
    ]
    return sorted(files, key=lambda path: path.name)


@contextlib.contextmanager
def batch() -> Iterator[TextIO]:
    """The file a new batch of runs writes its results to, locked while the
    batch runs, and made ready to publish when it ends."""
    directory = UNPUBLISHED / "evals/results"
    directory.mkdir(parents=True, exist_ok=True)
    started = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    part = directory / f"{started}-{secrets.token_hex(3)}.jsonl.part"
    with part.open("a") as file:
        fcntl.flock(file, fcntl.LOCK_EX)
        try:
            yield file
        finally:
            file.flush()
            finish(part)


def finish(part: Path) -> None:
    """Make a batch's results ready to publish, or drop them if it has none."""
    if part.stat().st_size:
        part.rename(part.with_suffix(""))
    else:
        part.unlink()


def abandoned() -> None:
    """Finish each batch whose process died without finishing it, such as one
    a signal stopped: the lock on its file went with the process."""
    for part in (UNPUBLISHED / "evals/results").glob("*.jsonl.part"):
        with part.open("a") as file:
            try:
                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                continue
            finish(part)


def publish(*patterns: str) -> None:
    """Publish the waiting files `patterns` match, by default every request
    and batch of results."""
    abandoned()
    files = sorted(
        file
        for pattern in patterns or (REQUESTS, RESULTS)
        for file in UNPUBLISHED.glob(pattern)
    )
    if not files:
        print("Nothing to publish.")
        return
    paths = [str(p.relative_to(UNPUBLISHED)) for p in files]
    push(files, paths)
    if requests := [p.split("/")[2:] for p in paths if p.startswith("evals/requests/")]:
        pointers = json.loads(POINTERS.read_text())
        for case, name in requests:
            pointers[case] = name.removesuffix(".json.gz")
        POINTERS.write_text(json.dumps(pointers, indent=2) + "\n")
    for file in files:
        file.unlink()
    print(f"Published {', '.join(paths)}.")


def push(files: list[Path], paths: list[str]) -> None:
    """Add the waiting files to main at their paths, and push the commit. Where
    another publish moved main first, build the commit again on top of it."""
    while True:
        fetch()
        listed = git(
            "ls-tree", "--format=%(path)%x09%(objectname)", "HEAD", "--", *paths
        )
        held = dict(line.split("\t") for line in listed.splitlines())
        # A request is named for its JSON, so main holds it already where it
        # holds the name, whatever bytes another gzip made of it.
        adding = [
            (file, path)
            for file, path in zip(files, paths, strict=True)
            if not (path in held and path.startswith("evals/requests/"))
        ]
        if not adding:
            return
        blobs = git("hash-object", "-w", *(str(f) for f, _ in adding)).splitlines()
        entries = [(path, blob) for (_, path), blob in zip(adding, blobs, strict=True)]
        if changed := [path for path, blob in entries if held.get(path, blob) != blob]:
            # Each name is published once, so that main only ever gains files.
            sys.exit(f"Main holds other copies of {', '.join(changed)}.")
        with tempfile.TemporaryDirectory() as scratch:
            env = os.environ | {"GIT_INDEX_FILE": f"{scratch}/index"}
            git("read-tree", "HEAD", env=env)
            lines = "".join(f"100644 {blob}\t{path}\n" for path, blob in entries)
            git("update-index", "--index-info", input=lines, env=env)
            tree = git("write-tree", env=env)
        # A publish whose push went through may have stopped before it deleted
        # the files, so main can hold them already.
        if tree == git("rev-parse", "HEAD^{tree}"):
            return
        branch = git("rev-parse", "--abbrev-ref", "HEAD", cwd=ROOT)
        message = f"Publish from sparkjoy {branch}"
        commit = git("commit-tree", tree, "-p", "HEAD", "-m", message)
        try:
            git(
                "push",
                "--quiet",
                "origin",
                f"{commit}:refs/heads/main",
                # Fail rather than ask for a login: only the owner can push.
                env=os.environ | {"GIT_TERMINAL_PROMPT": "0"},
            )
        except subprocess.CalledProcessError:
            git("fetch", "--quiet", "origin", "main")
            if git("rev-parse", "FETCH_HEAD") == git("rev-parse", "HEAD"):
                raise
            # Fails, and so stops the retries, where main was rewritten.
            git("merge", "--quiet", "--ff-only", "FETCH_HEAD")
            continue
        git("merge", "--quiet", "--ff-only", commit)
        return


if __name__ == "__main__":
    if sys.argv[1:] != ["publish"]:
        sys.exit(__doc__.split("\n\n")[1])
    publish()
