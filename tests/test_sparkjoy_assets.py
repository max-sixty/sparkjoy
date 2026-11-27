"""Publishes eval results, requests and images from two checkouts of sparkjoy
to a local copy of sparkjoy-assets, as model/sparkjoy_assets.py publishes them
to GitHub: `uvx pytest tests`."""

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
URL = "https://github.com/max-sixty/sparkjoy-assets.git"
CHART = "https://raw.githubusercontent.com/max-sixty/sparkjoy-assets/main/images/chart/{}.svg"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def sparkjoy(path: Path):
    """A checkout of sparkjoy holding model/sparkjoy_assets.py, whose one case's
    request is `seed` and whose README shows an image, and the module as that
    checkout imports it."""
    for directory in ("evals", "model"):
        (path / directory).mkdir(parents=True)
    shutil.copy(ROOT / "model/sparkjoy_assets.py", path / "model/sparkjoy_assets.py")
    (path / "evals/requests.json").write_text(json.dumps({"case": "seed"}))
    (path / "README.md").write_text(f"![A chart]({CHART.format('0' * 12)})\n")
    git(path, "init", "--quiet", "--initial-branch", path.name)
    git(path, "add", "README.md")
    git(path, "commit", "--quiet", "--message", "init")
    spec = importlib.util.spec_from_file_location(
        path.name, path / "model/sparkjoy_assets.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_two_checkouts_publish_to_sparkjoy_assets(tmp_path, monkeypatch):
    for key, value in {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }.items():
        monkeypatch.setenv(key, value)
    seed = tmp_path / "seed"
    (seed / "evals/results").mkdir(parents=True)
    (seed / "evals/requests/case").mkdir(parents=True)
    (seed / "evals/results/0-seed.jsonl").write_text('{"case": "seed"}\n')
    (seed / "evals/requests/case/seed.json.gz").write_text("seed")
    git(seed, "init", "--quiet", "--initial-branch", "main")
    git(seed, "add", "--all")
    git(seed, "commit", "--quiet", "--message", "seed")
    remote = tmp_path / "remote.git"
    git(tmp_path, "clone", "--quiet", "--bare", str(seed), str(remote))
    git(remote, "config", "uploadpack.allowFilter", "true")
    # The checkouts clone GitHub's URL, which this sends to the local copy.
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", f"url.file://{remote}.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", URL)
    # And pushes nowhere, until the first batch has failed to publish.
    monkeypatch.setenv("GIT_CONFIG_KEY_1", f"url.file://{tmp_path}/none.pushInsteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", URL)

    checkouts = [sparkjoy(tmp_path / name) for name in ("one", "other")]
    for checkout in checkouts:
        # Each rebuilds the case's request, then runs a batch.
        name = checkout.ROOT.name
        waiting = checkout.UNPUBLISHED / f"evals/requests/case/{name}.json.gz"
        waiting.parent.mkdir(parents=True)
        waiting.write_text(name)
        (waiting.parent / "notes.txt").write_text("not a request")
        with checkout.batch() as results:
            results.write(json.dumps({"case": name}) + "\n")
            # A publish meanwhile leaves the running batch's file alone.
            checkout.publish(checkout.RESULTS)
            assert Path(results.name).exists()
    one = checkouts[0]
    # A batch a signal stopped leaves its file unfinished and unlocked.
    killed = one.UNPUBLISHED / "evals/results/1-killed.jsonl.part"
    killed.write_text('{"case": "killed"}\n')
    assert one.read("evals/requests/case/seed.json.gz") == b"seed"
    with pytest.raises(subprocess.CalledProcessError):
        one.publish(one.RESULTS)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    # The other checkout's publish skips its first fetch, as though this one
    # pushed just after it, so its first push is rejected and it tries again.
    other = checkouts[1]
    other.fetch()
    fetch = other.fetch

    def stale() -> None:
        monkeypatch.setattr(other, "fetch", fetch)

    monkeypatch.setattr(other, "fetch", stale)
    for checkout in checkouts:
        checkout.publish(checkout.RESULTS)
    assert git(remote, "ls-tree", "--name-only", "main", "evals/requests/case/") == (
        "evals/requests/case/seed.json.gz"
    )
    for checkout in checkouts:
        checkout.publish()

    assert git(remote, "ls-tree", "-r", "--name-only", "main", "evals/requests/") == (
        "evals/requests/case/one.json.gz\n"
        "evals/requests/case/other.json.gz\n"
        "evals/requests/case/seed.json.gz"
    )
    # Both batches started in the same second, so either can sort first.
    assert sorted(json.loads(p.read_text())["case"] for p in one.results()) == [
        "killed",
        "one",
        "other",
        "seed",
    ]
    for checkout in checkouts:
        name = checkout.ROOT.name
        assert checkout.request("case") == name.encode()
        waiting = [p for p in checkout.UNPUBLISHED.rglob("*") if p.is_file()]
        assert [p.name for p in waiting] == ["notes.txt"]
    # A request is named for its JSON, so other bytes under a published name
    # leave main's copy as it is.
    regzipped = other.UNPUBLISHED / "evals/requests/case/one.json.gz"
    regzipped.write_text("one, gzipped by another Python")
    other.publish()
    assert git(remote, "show", "main:evals/requests/case/one.json.gz") == "one"
    assert other.request("case") == b"one"
    # Results are published once under each name.
    results = other.UNPUBLISHED / "evals/results/1-killed.jsonl"
    results.write_text('{"case": "another"}\n')
    with pytest.raises(SystemExit):
        other.publish(other.RESULTS)
    # An image is published under the hash of its bytes, and the README's link
    # to the image of its name points at it.
    chart = tmp_path / "chart.svg"
    chart.write_text("<svg/>")
    one.image(chart)
    one.publish(one.IMAGES)
    digest = hashlib.sha256(b"<svg/>").hexdigest()[:12]
    assert git(remote, "show", f"main:images/chart/{digest}.svg") == "<svg/>"
    assert (
        one.ROOT / "README.md"
    ).read_text() == f"![A chart]({CHART.format(digest)})\n"
