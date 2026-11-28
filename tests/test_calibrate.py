"""Runs model/calibrate.py on a synthetic transcript: `uvx pytest tests`. It needs uv."""

import subprocess

from test_report import ROOT, prompt, request, transcript


def test_empty_band_stops_before_the_fits(tmp_path):
    """A window with no turn end in the share's top band, 650k to 965k, stops at once with a message
    naming the band, rather than failing to write NaN figures at the end."""
    figures = ROOT / "docs/figures.json"
    before = figures.read_text()
    entries = []
    for i, size in enumerate(range(210_000, 640_000, 30_000)):
        entries += [
            prompt(f"10:{i:02d}:00.000Z", "Go on."),
            request(f"10:{i:02d}:30.000Z", size - 1_000, 1_000),
        ]
    run = subprocess.run(
        [
            *("uv", "run", "--script", ROOT / "model/calibrate.py"),
            *("--projects", transcript(tmp_path, entries)),
            *("--since", "2026-11-01T00:00Z", "--until", "2026-11-03T00:00Z"),
        ],
        capture_output=True,
        text=True,
    )
    assert run.returncode != 0
    assert "650k to 965k" in run.stderr
    assert "--since and --until" in run.stderr
    assert figures.read_text() == before
