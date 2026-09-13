from __future__ import annotations

from autoresearch.session_agent.workflows.scan import (
    build_scan_plan,
    l3_expansion,
    l3_repair_expansion,
)

from .test_scan_prelude import context, request


def test_full_l3_expansion_keeps_one_holistic_rank_task(tmp_path):
    plan = build_scan_plan(request(), context(tmp_path))
    expansion = l3_expansion(
        plan,
        {"mode": "FULL", "pinned_codes": []},
        ["半导体", "银行"],
        [
            {"artifact_id": "scan.run_mode", "sha256": "e" * 64},
            {"artifact_id": "scan.sector.list", "sha256": "f" * 64},
        ],
    )
    rank = [task for task in expansion["tasks"] if task["role"] == "scan.l3"]
    assert len(rank) == 1
    assert rank[0]["output_artifact_ids"] == ["scan.l3.judged"]
    assert "scan.market.pack" not in rank[0]["input_artifact_ids"]
    assert rank[0]["input_artifact_ids"][0:2] == [
        "scan.l3.table",
        "scan.market.view",
    ]
    assert [task["subject"] for task in expansion["tasks"] if task["role"] == "sector.brief"] == [
        "半导体",
        "银行",
    ]
    assert expansion["tasks"][-1]["task_id"] == "scan.l3.lint"
    assert not any(task["task_id"] == "scan.gate2" for task in expansion["tasks"])


def test_sentinel_l3_records_gate2_as_not_applicable(tmp_path):
    plan = build_scan_plan(request(), context(tmp_path))
    expansion = l3_expansion(
        plan,
        {"mode": "SENTINEL_EMPTY", "pinned_codes": []},
        [],
        [
            {"artifact_id": "scan.run_mode", "sha256": "e" * 64},
            {"artifact_id": "scan.sector.list", "sha256": "f" * 64},
        ],
    )
    assert [task["task_id"] for task in expansion["tasks"]] == ["scan.gate2"]
    assert expansion["tasks"][0]["operation"] == "scan.gate2.skip"


def test_clean_l3_lint_skips_repair_without_an_inference_call(tmp_path):
    plan = build_scan_plan(request(), context(tmp_path))
    expansion = l3_repair_expansion(
        plan,
        {"ok": True, "reason": "ok", "failures": []},
        [{"artifact_id": "scan.l3.validation", "sha256": "a" * 64}],
    )

    assert not any(task["kind"] == "INFERENCE" for task in expansion["tasks"])
    assert [task["task_id"] for task in expansion["tasks"]] == [
        "scan.l3.repair.skip",
        "scan.gate2",
    ]


def test_failed_l3_lint_expands_one_narrow_repair_then_gate2(tmp_path):
    plan = build_scan_plan(request(), context(tmp_path))
    expansion = l3_repair_expansion(
        plan,
        {
            "ok": False,
            "reason": "600519:99",
            "failures": [{"code": "600519", "token": "99", "position": 4}],
        },
        [{"artifact_id": "scan.l3.validation", "sha256": "b" * 64}],
    )

    repair = next(task for task in expansion["tasks"] if task["kind"] == "INFERENCE")
    assert repair["role"] == "scan.l3.repair"
    assert repair["input_artifact_ids"] == ["scan.l3.repair.prompt"]
    assert repair["output_artifact_ids"] == ["scan.l3.repair.patch"]
    assert [task["task_id"] for task in expansion["tasks"]][-2:] == [
        "scan.l3.repair.apply",
        "scan.gate2",
    ]
