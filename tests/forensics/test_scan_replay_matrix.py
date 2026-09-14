from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoresearch.contracts.operation_replay import operation_replay_classification
from autoresearch.session_agent import domain_ops
from autoresearch.session_agent.replay_adapters.common import safe_name
from autoresearch.session_agent.replay_adapters.scan import execute as execute_scan
from autoresearch.session_agent.workflows.scan import (
    build_scan_plan,
    l3_expansion,
    l3_repair_expansion,
    l4_expansion,
    review3_expansion,
    sector_expansion,
)


def _request(*, force_full: bool = False) -> dict:
    return {
        "schema_version": 1,
        "kind": "scan-market",
        "requested_mode": "AUTO",
        "analysis_date": "2026-09-14",
        "subject": None,
        "peers": [],
        "asset_type": None,
        "name": None,
        "force_full": force_full,
        "host_profile": {
            "schema_version": 1,
            "engine": "codex",
            "session_ref": "fixture",
            "adapter": "fixture",
            "capabilities": [],
        },
        "predecessor_run_id": None,
    }


def _handle(tmp_path: Path) -> SimpleNamespace:
    workspace = tmp_path / "run"
    staging = workspace / "staging"
    staging.mkdir(parents=True)
    request = _request()
    (workspace / "session").mkdir()
    (workspace / "session/request.json").write_text(json.dumps(request))
    return SimpleNamespace(
        run_id="20260914T040102000000Z",
        engine="codex",
        analysis_date="2026-09-14",
        workspace=workspace,
        staging=staging,
        contract=SimpleNamespace(
            contract_hash="a" * 64,
            config_hash="b" * 64,
            user_config={},
        ),
    )


def _tasks(expansion: dict) -> dict[str, dict]:
    return {task["task_id"]: task for task in expansion["tasks"]}


def _unit(task: dict) -> dict:
    return {
        **task,
        "attempt": 1,
        "input_refs": [
            {"artifact_id": artifact_id} for artifact_id in task["input_artifact_ids"]
        ],
        "expected_outputs": [
            {"artifact_id": artifact_id} for artifact_id in task["output_artifact_ids"]
        ],
    }


def test_scan_operation_catalog_has_honest_closed_replay_classes():
    assert operation_replay_classification("scan.frame") == "SOURCE_REPLAY"
    assert operation_replay_classification("scan.prelude") == "SOURCE_REPLAY"
    assert operation_replay_classification("scan.l3.prepare") == "SOURCE_REPLAY"
    assert operation_replay_classification("scan.l4.prepare") == "SOURCE_REPLAY"
    assert operation_replay_classification("scan.l4.slim") == "SOURCE_REPLAY"
    assert operation_replay_classification("scan.usage") == "SOURCE_REPLAY"
    assert operation_replay_classification("scan.observe") == "EFFECT_PLAN"
    assert operation_replay_classification("scan.l4.ticket") == "CONTROL_ONLY"


def test_scan_fixed_plan_passes_frozen_prelude_state_to_gate1(tmp_path):
    plan = build_scan_plan(_request(), _handle(tmp_path))
    tasks = _tasks(plan)
    assert "scan.prelude.bundle" in tasks["scan.prelude"]["output_artifact_ids"]
    assert "scan.prelude.bundle" in tasks["scan.gate1"]["input_artifact_ids"]


@pytest.mark.parametrize(
    "mode",
    ["FULL", "FORCED_FULL", "SENTINEL_EMPTY", "SENTINEL_PINNED"],
)
def test_scan_mode_carries_a_declared_state_chain_to_l5(tmp_path, mode):
    plan = build_scan_plan(_request(force_full=mode == "FORCED_FULL"), _handle(tmp_path))
    mode_value = {
        "mode": mode,
        "pinned_codes": ["600519"] if mode == "SENTINEL_PINNED" else [],
    }
    sector = sector_expansion(plan, mode_value, [])
    sector_task = next(iter(sector["tasks"]))
    assert "scan.prelude.bundle" in sector_task["input_artifact_ids"]

    l3 = l3_expansion(plan, mode_value, [], [])
    if mode in {"FULL", "FORCED_FULL"}:
        lint = _tasks(l3)["scan.l3.lint"]
        assert "scan.l3.source.bundle" in lint["input_artifact_ids"]
        assert "scan.l3.context.bundle" in lint["output_artifact_ids"]
        repair = l3_repair_expansion(plan, {"ok": True}, [])
        gate2 = _tasks(repair)["scan.gate2"]
        assert "scan.l3.final.bundle" in gate2["output_artifact_ids"]
        finalists = [{"code": "600519", "pinned": False}]
    else:
        gate2 = next(iter(l3["tasks"]))
        assert "scan.l3.final.bundle" in gate2["output_artifact_ids"]
        finalists = [] if mode == "SENTINEL_EMPTY" else [{"code": "600519", "pinned": True}]

    l4 = l4_expansion(plan, mode_value, finalists, [], intel_enabled=False)
    if mode == "SENTINEL_EMPTY":
        assert "scan.l4.source.bundle" in _tasks(l4)["scan.l4.skip"]["output_artifact_ids"]
        decision = {"decisions": []}
    else:
        prepare = _tasks(l4)["scan.l4.prepare"]
        assert "scan.l3.final.bundle" in prepare["input_artifact_ids"]
        assert "scan.l4.source.bundle" in prepare["output_artifact_ids"]
        decision = {
            "decisions": [
                {
                    "code": "600519",
                    "attempt": 1,
                    "rating": "Hold",
                    "review2_rating": None,
                    "same_tier": None,
                    "trigger": None,
                }
            ]
        }
    finish = review3_expansion(plan, decision, [])
    finish_tasks = _tasks(finish)
    complete = finish_tasks.get("scan.l4.complete")
    if complete is not None:
        assert "scan.l4.final.bundle" in complete["output_artifact_ids"]
    assemble = finish_tasks["scan.assemble"]
    assert "scan.l4.final.bundle" in assemble["input_artifact_ids"]
    assert "scan.report.build.bundle" in assemble["output_artifact_ids"]
    assert "scan.report.build.bundle" in finish_tasks["scan.gate4"]["input_artifact_ids"]
    assert "scan.report.build.bundle" in finish_tasks["scan.usage"]["input_artifact_ids"]
    assert "scan.report.build.bundle" in finish_tasks["scan.observe"]["input_artifact_ids"]


def test_scan_staging_bundle_round_trips_and_rejects_escape(tmp_path):
    source = tmp_path / "source"
    (source / "nested").mkdir(parents=True)
    (source / "L2_gbdt_top200.csv").write_bytes(b"code\n600519\n")
    (source / "nested/value.json").write_text('{"value": 1}')
    bundle = domain_ops.collect_scan_staging_bundle(source, phase="prelude")
    restored = tmp_path / "restored"
    domain_ops.restore_scan_staging_bundle(bundle, restored, expected_phase="prelude")
    assert (restored / "L2_gbdt_top200.csv").read_bytes() == b"code\n600519\n"
    assert (restored / "nested/value.json").read_text() == '{"value": 1}'

    poisoned = {**bundle, "files": {"../escape": next(iter(bundle["files"].values()))}}
    with pytest.raises(ValueError, match="unsafe scan bundle path"):
        domain_ops.restore_scan_staging_bundle(poisoned, restored, expected_phase="prelude")


def test_scan_state_bundle_excludes_session_control_and_report_build(tmp_path):
    staging = tmp_path / "staging"
    for relative in (
        "L2_gbdt_top200.csv",
        "session_outputs/gate1.json",
        "session_outputs/report_build/run/summary.md",
        ".session-agent.lock",
    ):
        path = staging / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative)
    bundle = domain_ops.collect_scan_staging_bundle(staging, phase="prelude")
    assert set(bundle["files"]) == {"L2_gbdt_top200.csv"}


class _AdapterContext:
    def __init__(self, root: Path, unit: dict, request: dict, values: dict[str, bytes]):
        self.unit = unit
        self.replay_run_id = "20260914T040102000000Z"
        self.inputs = root / "inputs"
        self.work = root / "work"
        self.outputs = root / "outputs"
        self.effects = root / "effects"
        self.source_receipts = ()
        self.operation_request = {
            "task_id": unit["task_id"],
            "attempt": 1,
            "operation": unit["operation"],
            "frozen_clock": "2026-09-14T04:01:02Z",
        }
        self.env = {
            "AUTORESEARCH_ENGINE": "codex",
            "AUTORESEARCH_FROZEN_CLOCK": "2026-09-14T04:01:02Z",
        }
        for directory in (
            self.inputs / "artifacts",
            self.work,
            self.outputs,
            self.effects,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        (self.inputs / "artifacts" / safe_name("session.request")).write_text(
            json.dumps(request)
        )
        for artifact_id, payload in values.items():
            (self.inputs / "artifacts" / safe_name(artifact_id)).write_bytes(payload)

    def output_path(self, artifact_id: str) -> Path:
        target = self.outputs / safe_name(artifact_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target


@pytest.mark.parametrize(
    ("mode", "pinned", "expected_codes"),
    [
        ("SENTINEL_EMPTY", [], []),
        ("SENTINEL_PINNED", ["600519"], ["600519"]),
    ],
)
def test_sentinel_gate2_replays_from_declared_bundle_only(
    tmp_path, mode, pinned, expected_codes
):
    source = tmp_path / "source"
    source.mkdir()
    (source / "L2_gbdt_top200.csv").write_text(
        "code,name,industry,gbdt_score\n600519,贵州茅台,食品饮料,88\n"
    )
    prelude = domain_ops.collect_scan_staging_bundle(source, phase="prelude")
    request = _request()
    plan = build_scan_plan(request, _handle(tmp_path / "plan"))
    expansion = l3_expansion(
        plan,
        {"mode": mode, "pinned_codes": pinned},
        [],
        [],
    )
    task = expansion["tasks"][0]
    unit = _unit(task)
    values = {
        "scan.run_mode": json.dumps(
            {
                "schema_version": 1,
                "mode": mode,
                "sentinel_reason": "fixture",
                "pinned_snapshot_hash": "a" * 64,
                "pinned_codes": pinned,
                "has_selection_conclusion": False,
                "has_l3_judgment": False,
                "created_at": "2026-09-14T04:01:02Z",
                "contract_hash": "a" * 64,
            }
        ).encode(),
        "scan.sector.list": json.dumps(
            {"schema_version": 1, "mode": mode, "sectors": []}
        ).encode(),
        "scan.prelude.bundle": (json.dumps(prelude, sort_keys=True) + "\n").encode(),
        "scan.market.view": b"fixture market view\n",
    }
    context = _AdapterContext(tmp_path / f"adapter-{mode}", unit, request, values)

    execute_scan(unit, context)

    finalists = context.output_path("scan.finalists").read_text()
    assert all(code in finalists for code in expected_codes)
    gate2 = json.loads(context.output_path("scan.gate2.result").read_text())
    assert gate2["status"] == "SKIPPED_NOT_APPLICABLE"
    final_bundle = json.loads(context.output_path("scan.l3.final.bundle").read_text())
    assert final_bundle["phase"] == "l3_final"


def test_full_l3_lint_skip_and_gate2_execute_from_declared_inputs(tmp_path):
    source = tmp_path / "full-source"
    (source / "_session_inputs").mkdir(parents=True)
    (source / "L2_gbdt_top200.csv").write_text(
        "code,name,industry,pct_60d,pct_1d,composite,gbdt_score\n"
        "600519,贵州茅台,食品饮料,12.0,1.0,80,88\n"
    )
    (source / "_session_inputs/scan_config.jsonc").write_text("{}\n")
    (source / "_session_inputs/pinned.jsonc").write_text("[]\n")
    (source / "_session_inputs/manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "present": {
                    "scan_config.jsonc": True,
                    "pinned.jsonc": True,
                    "L1_weights.json": False,
                },
            }
        )
    )
    source_bundle = domain_ops.collect_scan_staging_bundle(source, phase="l3_source")
    judged = [
        {
            "code": "600519",
            "name": "贵州茅台",
            "sector": "食品饮料",
            "lane": "value",
            "conviction": 72,
            "fragility": 40,
            "triage_lean": "标配",
            "thesis": "现金流质量稳定",
            "risk": "需求走弱",
            "catalyst": "中报窗口",
            "lenses": "价值",
            "sentiment": "中性",
            "finalist": True,
        }
    ]
    request = _request()
    plan = build_scan_plan(request, _handle(tmp_path / "full-plan"))
    l3 = l3_expansion(plan, {"mode": "FULL", "pinned_codes": []}, [], [])
    lint_unit = _unit(_tasks(l3)["scan.l3.lint"])
    lint_values = {
        "scan.l3.judged": json.dumps(judged, ensure_ascii=False).encode(),
        "scan.market.pack": b"{}\n",
        "scan.l3.source.bundle": json.dumps(source_bundle).encode(),
        "scan.sector.list": json.dumps(
            {"schema_version": 1, "mode": "FULL", "sectors": []}
        ).encode(),
    }
    lint_context = _AdapterContext(tmp_path / "full-lint", lint_unit, request, lint_values)
    execute_scan(lint_unit, lint_context)
    validation = json.loads(lint_context.output_path("scan.l3.validation").read_text())
    assert validation["ok"] is True

    repair = l3_repair_expansion(plan, validation, [])
    repair_unit = _unit(_tasks(repair)["scan.l3.repair.skip"])
    repair_values = {
        "scan.l3.validation": lint_context.output_path("scan.l3.validation").read_bytes(),
        "scan.l3.judged": json.dumps(judged, ensure_ascii=False).encode(),
    }
    repair_context = _AdapterContext(
        tmp_path / "full-repair", repair_unit, request, repair_values
    )
    execute_scan(repair_unit, repair_context)

    gate2_unit = _unit(_tasks(repair)["scan.gate2"])
    gate2_values = {
        "scan.l3.effective.judged": repair_context.output_path(
            "scan.l3.effective.judged"
        ).read_bytes(),
        "scan.l3.validation": lint_context.output_path("scan.l3.validation").read_bytes(),
        "scan.l3.repair.result": repair_context.output_path(
            "scan.l3.repair.result"
        ).read_bytes(),
        "scan.gate1.result": json.dumps({"ok": True, "l4_budget": 5}).encode(),
        "scan.run_mode": json.dumps({"schema_version": 1, "mode": "FULL"}).encode(),
        "scan.l3.context.bundle": lint_context.output_path(
            "scan.l3.context.bundle"
        ).read_bytes(),
    }
    gate2_context = _AdapterContext(tmp_path / "full-gate2", gate2_unit, request, gate2_values)
    execute_scan(gate2_unit, gate2_context)

    result = json.loads(gate2_context.output_path("scan.gate2.result").read_text())
    assert result["ok"] is True
    assert result["finalists"] == ["600519"]
    assert "600519" in gate2_context.output_path("scan.finalists").read_text()
    assert json.loads(
        gate2_context.output_path("scan.l3.final.bundle").read_text()
    )["phase"] == "l3_final"
