import json
import shutil
import subprocess
from pathlib import Path

import pytest

from autoresearch.common.research_prompts import (
    END,
    START,
    legacy_template_block,
    render_research_prompt,
)

ROOT = Path(__file__).resolve().parents[2]


def test_generated_legacy_wording_has_one_owner_and_keeps_capability_guard():
    text = (ROOT / ".claude/workflows/l4-stock.js").read_text()
    assert text[text.index(START) : text.index(END) + len(END)] == legacy_template_block()
    assert "C4_LEGACY_GUARD" in text
    assert "researchPrompt('scan.l4.card'" in text
    assert "researchPrompt('scan.l4.review'" in text


@pytest.mark.parametrize(
    ("role", "values"),
    [
        ("scan.l4.card", {"prompt": "test/任务.md", "output": "attempts/a1/card.md"}),
        (
            "scan.l4.review",
            {"prompt": "test/'task'.md", "output": "attempts/a2/review.md", "run_index": 3},
        ),
    ],
)
def test_javascript_and_python_actual_renderers_match(role, values):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node unavailable; generated block equality is checked separately")
    program = (
        legacy_template_block()
        + "\nprocess.stdout.write(researchPrompt("
        + json.dumps(role)
        + ","
        + json.dumps(values)
        + "))"
    )
    result = subprocess.run(
        [node, "--input-type=module", "-e", program], capture_output=True, text=True, check=True
    )
    assert result.stdout == render_research_prompt(role, **values)


def test_missing_template_argument_rejected():
    with pytest.raises(ValueError):
        render_research_prompt("scan.l4.card", prompt="task.md")
