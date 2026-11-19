"""Checks that the README's collapsed copy of docs/cost-model.md is current: `uvx pytest tests`."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_readme_holds_the_cost_model_as_written():
    result = subprocess.run([sys.executable, ROOT / "docs/embed.py", "--check"])
    assert result.returncode == 0, "run `python3 docs/embed.py` after editing docs/cost-model.md"
