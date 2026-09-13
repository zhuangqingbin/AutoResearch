from __future__ import annotations

from autoresearch.session_agent.validation import (
    DomainValidationError,
    validate_registered_contract,
)
from autoresearch.session_agent.workflows.scan import build_scan_plan, l4_expansion

from .test_scan_prelude import context, request


def test_l4_expansion_has_one_external_owner_per_whole_ticket(tmp_path):
    plan = build_scan_plan(request(), context(tmp_path))
    expansion = l4_expansion(
        plan,
        {"mode": "FULL", "pinned_codes": []},
        [
            {
                "code": "600519",
                "ticker": "600519.SS",
                "name": "贵州茅台",
                "sector": "食品饮料",
                "pinned": False,
            }
        ],
        [{"artifact_id": "scan.finalists", "sha256": "e" * 64}],
        intel_enabled=True,
    )
    owners = [task for task in expansion["tasks"] if task["owner"] == "L4_TASKBOOK"]
    assert [task["task_id"] for task in owners] == ["l4.600519.a1"]
    children = [task for task in expansion["tasks"] if task["parent_task"]]
    assert children
    assert all(
        task["parent_task"]
        == {"owner": "L4_TASKBOOK", "subject": "600519", "attempt": 1}
        for task in children
    )
    intel = next(task for task in children if task["role"] == "scan.l4.intel")
    assert intel["input_artifact_ids"] == ["scan.l4.600519.a1.prompt"]


def test_disabled_intel_is_recorded_without_a_web_task(tmp_path):
    plan = build_scan_plan(request(), context(tmp_path))
    expansion = l4_expansion(
        plan,
        {"mode": "SENTINEL_PINNED", "pinned_codes": ["600519"]},
        [{"code": "600519", "pinned": True}],
        [{"artifact_id": "scan.finalists", "sha256": "e" * 64}],
        intel_enabled=False,
    )
    assert not any(task["role"] == "scan.l4.intel" for task in expansion["tasks"])
    assert any(task["operation"] == "scan.l4.intel.disabled" for task in expansion["tasks"])


def test_scan_intel_contract_requires_the_event_and_disclosure_sections(tmp_path, monkeypatch):
    task = {
        "role": "scan.l4.intel",
        "expected_output_contract": "scan.l4.intel.v1",
        "output_artifact_ids": ["scan.l4.600519.a1.intel"],
    }
    monkeypatch.setattr(
        "autoresearch.session_agent.validation._open_outputs",
        lambda handle, submission, spec: {
            "scan.l4.600519.a1.intel": "# 活体情报\n## 事件段\n无\n## 声明行\n网查 0 条"
        },
    )
    validate_registered_contract(object(), {}, task)
    monkeypatch.setattr(
        "autoresearch.session_agent.validation._open_outputs",
        lambda handle, submission, spec: {"scan.l4.600519.a1.intel": "# 活体情报"},
    )
    import pytest

    with pytest.raises(DomainValidationError, match="事件段"):
        validate_registered_contract(object(), {}, task)
