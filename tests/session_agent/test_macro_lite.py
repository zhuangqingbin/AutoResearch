from __future__ import annotations

import json

import pytest

from autoresearch.session_agent import artifacts
from autoresearch.session_agent.domain_ops import macro_lite_prepare, macro_lite_validate
from autoresearch.session_agent.workflows.macro import build_macro_plan

from .test_macro import _context, _request
from .test_service import _handle


def test_macro_lite_only_builds_frame_write_publish_chain(tmp_path):
    plan = build_macro_plan(_request("LITE"), _context(tmp_path))
    assert [task["task_id"] for task in plan["tasks"]] == [
        "macro.frame",
        "macro.brief",
        "macro.lite.validate",
        "macro.publish",
    ]
    assert not any("regional" in task["task_id"] for task in plan["tasks"])


def test_macro_lite_frame_uses_freshness_gate_and_projection(tmp_path):
    handle = _handle(tmp_path)
    request = _request("LITE")
    session = handle.workspace / "session"
    session.mkdir()
    (session / "request.json").write_text(json.dumps(request))
    source = handle.staging / "session_inputs/market_pack.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps({"regime": {"label": "range"}, "sector_healthy_top3": ["电子"]}))
    artifacts.register_artifact(handle, "macro.market_pack", source, "READ")
    artifacts.register_artifact(
        handle,
        "macro.strategist_pack",
        handle.staging / "session_outputs/strategist_pack.json",
        "WRITE",
    )
    result = macro_lite_prepare(handle, macro_state_path=tmp_path / "missing.json")
    assert "sector_healthy_top3" not in result["pack"]
    assert result["macro_state"] is None
    assert "无 macro_state" in result["macro_state_note"]


def test_macro_lite_validator_requires_all_six_sections(tmp_path):
    handle = _handle(tmp_path)
    path = handle.staging / "session_outputs/market_view.md"
    path.parent.mkdir(parents=True)
    path.write_text("1. **一句话定调**: range\n2. **市场结构**: 平稳\n")
    artifacts.register_artifact(handle, "macro.market_view", path, "WRITE")
    artifacts.bind_artifact_hash(handle, "macro.market_view")
    artifacts.register_artifact(
        handle,
        "macro.lite.validation",
        handle.staging / "session_outputs/macro.lite.validation.json",
        "WRITE",
    )
    with pytest.raises(RuntimeError, match="six sections"):
        macro_lite_validate(handle)

