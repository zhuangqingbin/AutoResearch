from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

from autoresearch.common.atomic import canonical_json, sha256_bytes
from autoresearch.contracts.forensic import evidence_plan_hash
from autoresearch.contracts.session_plan import plan_hash
from autoresearch.session_agent.replay_registry import build_replay_plan
from autoresearch.trace.replay import REPLAY_ENV, execute_replay
from autoresearch.trace.source_receipts import record_response, replay_response

RUN_ID = "20260914T120000000000Z"


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(value) + "\n", encoding="utf-8")


def _ref(capsule: Path, artifact_id: str, relative: str, payload: bytes) -> dict:
    path = capsule / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return {
        "artifact_id": artifact_id,
        "sha256": sha256_bytes(payload),
        "captured_path": relative,
    }


def _task(
    task_id: str,
    *,
    kind: str,
    operation: str | None,
    dependencies: list[str],
    inputs: list[str],
    outputs: list[str],
) -> dict:
    return {
        "task_id": task_id,
        "kind": kind,
        "role": "stock.writer" if kind == "INFERENCE" else None,
        "operation": operation,
        "dependencies": dependencies,
        "input_artifact_ids": inputs,
        "output_artifact_ids": outputs,
        "expected_output_contract": "fixture.v1",
        "owner": "SESSION",
        "subject": None,
        "independent_context": False,
        "parent_task": None,
    }


def _capsule(
    tmp_path: Path,
    *,
    operation: str = "research.calculate",
    with_source: bool = False,
):
    capsule = tmp_path / "capsule"
    deterministic = _task(
        "calculate",
        kind="DETERMINISTIC",
        operation=operation,
        dependencies=[],
        inputs=["raw.input"],
        outputs=["calc.output"],
    )
    inference = _task(
        "write",
        kind="INFERENCE",
        operation=None,
        dependencies=["calculate"],
        inputs=["calc.output"],
        outputs=["model.output"],
    )
    session_plan = {
        "schema_version": 1,
        "engine": "codex",
        "run_id": RUN_ID,
        "run_kind": "stock-research",
        "requested_mode": "FULL",
        "analysis_date": "2026-09-14",
        "orchestration_version": "session_v1",
        "input_contract_hash": "a" * 64,
        "config_hash": "b" * 64,
        "host_profile_hash": "c" * 64,
        "roles_hash": "d" * 64,
        "tasks": [deterministic, inference],
        "task_templates": [],
        "plan_hash": "0" * 64,
    }
    session_plan["plan_hash"] = plan_hash(session_plan)
    _write_json(capsule / "identity/session/plan.json", session_plan)

    keys = [
        {
            "task_id": task["task_id"],
            "attempt": 1,
            "owner": "SESSION",
            "subject": None,
            "state": "SUCCEEDED",
            "superseded_by": None,
            "requirements": ["claim", "outputs", "accepted_receipt"],
        }
        for task in session_plan["tasks"]
    ]
    denominator = {
        "schema_version": 1,
        "engine": "codex",
        "run_id": RUN_ID,
        "plan_hash": session_plan["plan_hash"],
        "expansion_hashes": [],
        "task_keys": keys,
        "closure_cutoff": "2026-09-14T12:00:00Z",
        "scope": ["stock-research"],
        "evidence_plan_hash": "0" * 64,
    }
    denominator["evidence_plan_hash"] = evidence_plan_hash(denominator)
    _write_json(capsule / "evidence/evidence_plan.json", denominator)

    raw = _ref(capsule, "raw.input", "evidence/tasks/calculate/a1/inputs/raw.input", b"2\n")
    expected = _ref(
        capsule,
        "calc.output",
        "evidence/tasks/calculate/a1/outputs/calc.output",
        b"4\n",
    )
    model = _ref(
        capsule,
        "model.output",
        "evidence/tasks/write/a1/outputs/model.output",
        b"frozen model prose\n",
    )
    request = {
        "schema_version": 1,
        "task_id": "calculate",
        "attempt": 1,
        "operation": operation,
        "subject": None,
        "params": {
            "calculator_id": "financial_period_ratios.v1",
            "input_artifact_ids": ["raw.input"],
            "parameters": {"fixture": True},
        },
    }
    request_path = capsule / "evidence/attempt_records/calculate/a1/operation_request.json"
    _write_json(request_path, request)
    command = {
        "argv": ["python", "-m", "fixture"],
        "cwd": ".",
        "exit_code": 0,
        "signal": None,
        "stdout_sha256": "e" * 64,
        "stderr_sha256": "f" * 64,
        "operation_version": f"{operation}.v1",
    }
    handle = SimpleNamespace(capsule=capsule, engine="codex", run_id=RUN_ID)
    source_ids = []
    if with_source:
        receipt = record_response(
            handle,
            {
                "engine": "codex",
                "run_id": RUN_ID,
                "task_id": "calculate",
                "attempt": 1,
                "provider": "fixture",
                "endpoint": "price",
                "normalized_params": {"symbol": "600519.SS"},
                "started_at": "2026-09-14T11:59:00Z",
                "ended_at": "2026-09-14T11:59:01Z",
                "as_of": "2026-09-14",
                "available_at": "2026-09-14T11:59:01Z",
                "consumer_refs": [],
            },
            {"price": 2},
        )
        source_ids = [receipt["receipt_id"]]
    common = {
        "schema_version": 1,
        "engine": "codex",
        "run_id": RUN_ID,
        "attempt": 1,
        "owner": "SESSION",
        "subject": None,
        "claim_ref": None,
        "receipt_ref": None,
        "transcript_refs": [],
        "status": "PRESENT",
        "reasons": [],
    }
    _write_json(
        capsule / "evidence/tasks/calculate/a1/evidence.json",
        {
            **common,
            "task_id": "calculate",
            "input_refs": [raw],
            "output_refs": [expected],
            "command_ref": command,
            "source_receipt_ids": source_ids,
        },
    )
    _write_json(
        capsule / "evidence/tasks/write/a1/evidence.json",
        {
            **common,
            "task_id": "write",
            "input_refs": [expected],
            "output_refs": [model],
            "command_ref": None,
            "source_receipt_ids": [],
            "transcript_refs": [
                {
                    "engine": "codex",
                    "status": "PRESENT",
                    "role": "stock.writer",
                    "subject": None,
                    "invocation_id": "session-write-a1",
                    "session_ref": "session-fixture",
                    "start_ordinal": 1,
                    "end_ordinal": 2,
                    "captured_path": "agents/session/transcripts/write.json",
                    "sha256": "1" * 64,
                    "context_source": "MAIN",
                }
            ],
        },
    )
    _write_json(
        capsule / "identity/source_tree_manifest.json",
        {
            "schema_version": 1,
            "code_tree_hash": "2" * 64,
            "files": [],
            "file_count": 0,
            "total_bytes": 0,
        },
    )
    _write_json(
        capsule / "identity/runtime_manifest.json",
        {"schema_version": 1, "availability": {"status": "LOCAL_ENV_MATCHED", "reason": "fixture"}},
    )
    return handle, capsule


def _snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_replay_plan_uses_evidence_plan_as_its_complete_denominator(tmp_path):
    handle, _capsule_root = _capsule(tmp_path)

    replay_plan = build_replay_plan(handle)

    assert [unit["task_id"] for unit in replay_plan["units"]] == ["calculate", "write"]
    assert replay_plan["units"][0]["mode"] == "COMPUTE"
    assert replay_plan["units"][1]["mode"] == "EVIDENCE_ONLY"
    assert replay_plan["units"][1]["dependencies"] == ["calculate:a1"]
    assert replay_plan["units"][0]["input_refs"][-1]["artifact_id"] == (
        "operation.request:calculate:a1"
    )


def test_noop_runner_cannot_pass_and_frozen_capsule_is_unchanged(tmp_path):
    handle, capsule = _capsule(tmp_path)
    replay_plan = build_replay_plan(handle)
    before = _snapshot(capsule)

    result = execute_replay(
        replay_plan,
        capsule,
        tmp_path / "audit",
        lambda unit, context: {"exit_code": 0, "effects": []},
    )

    assert result["compute_status"] != "FULL"
    assert "OUTPUT_NOT_PRODUCED:calculate:a1:calc.output" in result["missing"]
    assert result["required_units"] == 1
    assert result["executed_units"] == 1
    assert _snapshot(capsule) == before
    assert not (capsule / "verification/replay.json").exists()


def test_runner_receives_captured_params_but_never_expected_paths(tmp_path):
    handle, capsule = _capsule(tmp_path)
    replay_plan = build_replay_plan(handle)
    observed = {}

    def runner(unit, context):
        observed["params"] = context.operation_request["params"]
        observed["fields"] = set(vars(context))
        context.output_path("calc.output").write_bytes(b"4\n")
        return {"exit_code": 0, "effects": [], "isolation_status": "ENFORCED"}

    result = execute_replay(replay_plan, capsule, tmp_path / "audit", runner)

    assert observed["params"]["parameters"] == {"fixture": True}
    assert "expected" not in observed["fields"]
    assert "expected_paths" not in observed["fields"]
    assert result["unit_results"][0]["status"] == "MATCH"
    # An in-process test runner is useful for unit tests but is not a strict sandbox proof.
    assert result["isolation_status"] == "UNKNOWN"
    assert result["compute_status"] == "PARTIAL"


def test_source_runner_receives_exact_receipt_codec_and_payload(tmp_path):
    handle, capsule = _capsule(
        tmp_path,
        operation="stock.harvest",
        with_source=True,
    )
    replay_plan = build_replay_plan(handle)
    observed = {}

    def runner(unit, context):
        receipt = context.source_receipts[0]
        observed["codec"] = receipt["codec"]
        observed["payload"] = replay_response(
            Path(context.env[REPLAY_ENV]), receipt["receipt_id"]
        )
        context.output_path("calc.output").write_bytes(b"4\n")
        return {"exit_code": 0, "effects": []}

    result = execute_replay(replay_plan, capsule, tmp_path / "audit", runner)

    assert replay_plan["units"][0]["mode"] == "SOURCE_REPLAY"
    assert observed == {"codec": "json.canonical.v1", "payload": {"price": 2}}
    assert result["unit_results"][0]["status"] == "MATCH"


def test_unknown_required_operation_is_reported_not_skipped(tmp_path):
    handle, capsule = _capsule(tmp_path, operation="future.operation")
    replay_plan = build_replay_plan(handle)
    calls = []

    result = execute_replay(
        replay_plan,
        capsule,
        tmp_path / "audit",
        lambda unit, context: calls.append(unit) or {"exit_code": 0, "effects": []},
    )

    assert calls == []
    assert result["required_units"] == 1
    assert result["executed_units"] == 0
    assert result["unit_results"][0]["status"] == "UNSUPPORTED"
    assert "UNSUPPORTED_OPERATION:future.operation" in result["missing"]


def test_recorded_failure_is_reexecuted_and_keeps_its_own_attempt(tmp_path):
    handle, capsule = _capsule(tmp_path)
    denominator_path = capsule / "evidence/evidence_plan.json"
    denominator = json.loads(denominator_path.read_text(encoding="utf-8"))
    denominator["task_keys"][0].update(
        {"state": "FAILED", "requirements": ["claim", "command_capture"]}
    )
    denominator["evidence_plan_hash"] = evidence_plan_hash(denominator)
    _write_json(denominator_path, denominator)
    evidence_path = capsule / "evidence/tasks/calculate/a1/evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["output_refs"] = []
    _write_json(evidence_path, evidence)
    failure = {
        "schema_version": 1,
        "task_id": "calculate",
        "attempt": 1,
        "error": {"code": "TIMEOUT", "message": "fixture timeout"},
    }
    _write_json(
        capsule / "evidence/attempt_records/calculate/a1/failure.json",
        failure,
    )
    replay_plan = build_replay_plan(handle)

    result = execute_replay(
        replay_plan,
        capsule,
        tmp_path / "audit",
        lambda unit, context: {
            "exit_code": 75,
            "error": failure["error"],
            "effects": [],
        },
    )

    first = result["unit_results"][0]
    assert first["unit_id"] == "calculate:a1"
    assert first["status"] == "EXPECTED_FAILURE"
    assert first["matched"] is True


def test_model_output_is_reinjected_read_only_and_never_executed(tmp_path):
    handle, capsule = _capsule(tmp_path)
    replay_plan = build_replay_plan(handle)
    called = []

    def runner(unit, context):
        called.append(unit["task_id"])
        context.output_path("calc.output").write_bytes(b"4\n")
        return {"exit_code": 0, "effects": []}

    result = execute_replay(replay_plan, capsule, tmp_path / "audit", runner)

    assert called == ["calculate"]
    model_result = next(row for row in result["unit_results"] if row["unit_id"] == "write:a1")
    assert model_result["status"] == "EVIDENCE_ONLY"
    reinjected = tmp_path / "audit/inputs/reinjected/write%3Aa1/model.output"
    assert reinjected.read_bytes() == b"frozen model prose\n"
    assert reinjected.stat().st_mode & 0o222 == 0
