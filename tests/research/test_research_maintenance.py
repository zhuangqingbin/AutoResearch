import importlib
from pathlib import Path

import pytest


def test_unknown_model_is_not_stability():
    module = importlib.import_module("autoresearch.research.drift")
    common = {"observed_model": None, "requested_model": "same", "host_adapter_hash": "a",
              "source_schema_hash": "b", "prompt_hash": "c", "code_sha": "d"}
    result = module.drift_report(common, common)
    assert result["model_state"] == "UNKNOWN"
    assert result["canary_required"] is True


def test_real_model_or_source_change_requires_canary():
    module = importlib.import_module("autoresearch.research.drift")
    previous = {"observed_model": "a", "requested_model": "a", "host_adapter_hash": "a",
                "source_schema_hash": "b", "prompt_hash": "c", "code_sha": "d"}
    result = module.drift_report(previous, dict(previous, source_schema_hash="changed"))
    assert result["changed_fields"] == ["source_schema_hash"]
    assert result["canary_required"] is True
    assert result["automatic_prompt_update"] is False


def test_request_rejects_other_engine_before_reading(tmp_path):
    from autoresearch.common import workspace as ws
    from autoresearch.research.evidence_refs import read_request
    other = "claude" if ws.ENGINE == "codex" else "codex"
    with pytest.raises(ValueError, match="engine"):
        read_request(str(tmp_path / f"context_{other}" / "nonexistent.json"))


@pytest.mark.parametrize("name", ["scan-market", "stock-research", "macro-research", "sector-research"])
def test_skills_link_maintenance_owner(name):
    root = Path(__file__).resolve().parents[2]
    text = (root / ".claude/skills" / name / "SKILL.md").read_text()
    assert "docs/session-agent/research-quality-workflow.md" in text
