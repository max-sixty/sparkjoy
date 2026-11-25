"""Checks that the README's copies of docs/cost-model.md and of the plugin's question are current:
`uvx pytest tests`."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_readme_holds_the_cost_model_and_question_as_written():
    result = subprocess.run([sys.executable, ROOT / "docs/embed.py", "--check"])
    assert result.returncode == 0, (
        "run `python3 docs/embed.py` after editing docs/cost-model.md or question() in hooks/register.ts"
    )
