from __future__ import annotations

import json

from autoresearch.session_agent import artifacts, plan as plan_service, service, store
from autoresearch.session_agent.workflows.scan import (
    build_scan_plan,
    l3_expansion,
    l3_repair_expansion,
    register_scan_expansion_artifacts,
    sector_expansion,
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


def test_failed_optional_l3_repair_preserves_judged_and_releases_gate2(tmp_path):
    handle = context(tmp_path / "context_codex/scan_runs/20260913T010203000000Z")
    handle.workspace.mkdir(parents=True)
    handle.staging.mkdir(parents=True)
    handle.analysis_date = "2026-09-13"
    handle.capsule = handle.workspace / "capsule"
    handle.capsule.mkdir()
    handle.contract.run_kind = "scan-market"
    plan = build_scan_plan(request(), handle)
    session = handle.workspace / "session"
    session.mkdir()
    (session / "request.json").write_text(json.dumps(request()))
    (session / "plan.json").write_text(json.dumps(plan))
    store.initialize(session / "tasks.json", plan)

    sectors = sector_expansion(
        plan,
        {"mode": "FULL", "pinned_codes": []},
        {"artifact_id": "scan.run_mode", "sha256": "0" * 64},
    )
    plan_service.persist_expansion(
        session, plan, sectors, existing_tasks=plan["tasks"]
    )
    store.register_tasks(
        session / "tasks.json", sectors["tasks"], plan_hash=plan["plan_hash"]
    )
    l3 = l3_expansion(
        plan,
        {"mode": "FULL", "pinned_codes": []},
        [],
        [
            {"artifact_id": "scan.run_mode", "sha256": "a" * 64},
            {"artifact_id": "scan.sector.list", "sha256": "b" * 64},
        ],
    )
    plan_service.persist_expansion(
        session, plan, l3, existing_tasks=[*plan["tasks"], *sectors["tasks"]]
    )
    store.register_tasks(session / "tasks.json", l3["tasks"], plan_hash=plan["plan_hash"])
    repair = l3_repair_expansion(
        plan,
        {
            "ok": False,
            "reason": "600519:99",
            "failures": [{"code": "600519", "token": "99", "position": 4}],
        },
        [{"artifact_id": "scan.l3.validation", "sha256": "c" * 64}],
    )
    plan_service.persist_expansion(
        session,
        plan,
        repair,
        existing_tasks=[*plan["tasks"], *sectors["tasks"], *l3["tasks"]],
    )
    store.register_tasks(
        session / "tasks.json", repair["tasks"], plan_hash=plan["plan_hash"]
    )
    (handle.staging / "_l3_repair_prompt.md").write_text("repair one row")
    (handle.staging / "_l3_repair_pack.json").write_text(
        json.dumps({"failures": [{"code": "600519"}]})
    )
    # This historical v1 fixture models a completed L3 lint. Freeze its actual
    # gate inputs so READY checks exercise the same artifact boundary as a run.
    original_judged = [{"code": "600519", "thesis": "original judgment"}]
    frozen_inputs = {
        "scan.l3.judged": ("_l3_judged.json", original_judged),
        "scan.l3.validation": ("session_outputs/l3.validation.json", {"ok": False}),
        "scan.l3.context.bundle": ("session_outputs/l3.context.bundle.json", {"schema_version": 1}),
        "scan.gate1.result": ("session_outputs/gate1.json", {"ok": True, "l4_budget": 13}),
        "scan.run_mode": ("run_mode.json", {"mode": "FULL", "pinned_codes": []}),
    }
    for artifact_id, (relative, value) in frozen_inputs.items():
        source = handle.staging / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(json.dumps(value))
        if artifact_id in {"scan.gate1.result", "scan.run_mode"}:
            artifacts.register_artifact(handle, artifact_id, source, "READ")
    register_scan_expansion_artifacts(request(), handle, repair)
    task_store = json.loads((session / "tasks.json").read_text())
    task_store["tasks"]["scan.l3.lint"]["state"] = "SUCCEEDED"
    (session / "tasks.json").write_text(json.dumps(task_store))
    store.claim(session / "tasks.json", "scan.l3.repair", 1, "session-main")

    result = service.fail(
        handle.run_id,
        "scan.l3.repair",
        1,
        "CONNECTION_LOST",
        "closed mid-response",
        handle_loader=lambda unused: handle,
    )

    states = store.read_states(session / "tasks.json")
    assert states["scan.l3.repair"] == "SUPERSEDED"
    assert states["scan.l3.repair.apply"] == "SUPERSEDED"
    assert any(task["task_id"] == "scan.gate2" for task in result["tasks"])
    degraded = json.loads(
        (handle.staging / "session_outputs/l3.repair.json").read_text()
    )
    assert degraded["status"] == "DEGRADED"
    assert degraded["preserved_original"] is True
    assert json.loads((handle.staging / "_l3_effective_judged.json").read_text()) == original_judged
    assert artifacts.snapshot_artifact(handle, "scan.l3.repair.result")["sha256"]
