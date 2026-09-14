from __future__ import annotations

import json
import shutil

import pytest

from autoresearch.common.atomic import canonical_json, sha256_bytes
from autoresearch.contracts.session_plan import plan_hash
from autoresearch.session_agent import artifacts, service
from tests.session_agent.test_service import _handle, _request


def _inference_plan(request, handle, *, independent=False, role="stock.card"):
    task = {
        "task_id": "inference.one",
        "kind": "INFERENCE",
        "role": role,
        "operation": None,
        "dependencies": [],
        "input_artifact_ids": ["inference.input"],
        "output_artifact_ids": ["inference.output"],
        "expected_output_contract": "stock.lite.v1",
        "owner": "SESSION",
        "subject": "600519.SS",
        "independent_context": independent,
        "parent_task": None,
    }
    value = {
        "schema_version": 1,
        "engine": handle.engine,
        "run_id": handle.run_id,
        "run_kind": request["kind"],
        "requested_mode": request["requested_mode"],
        "analysis_date": request["analysis_date"],
        "orchestration_version": "session_v1",
        "input_contract_hash": handle.contract.contract_hash,
        "config_hash": sha256_bytes(canonical_json({}).encode("utf-8")),
        "host_profile_hash": sha256_bytes(
            canonical_json(request["host_profile"]).encode("utf-8")
        ),
        "roles_hash": "d" * 64,
        "tasks": [task],
        "task_templates": [],
        "plan_hash": "0" * 64,
    }
    value["plan_hash"] = plan_hash(value)
    return value


def _running_case(tmp_path, *, independent=False):
    handle = _handle(tmp_path)
    request = _request()
    if independent:
        request["host_profile"] = {
            **request["host_profile"],
            "independent_context": True,
        }
    output = handle.staging / "inference.md"
    input_path = handle.staging / "input.md"
    input_path.write_text("frozen task input", encoding="utf-8")

    def register(request, current, plan):
        artifacts.register_artifact(current, "inference.input", input_path, "READ")
        artifacts.register_artifact(current, "inference.output", output, "WRITE")

    service.begin(
        request,
        begin_capsule=lambda unused: handle,
        planner=lambda current, unused: _inference_plan(
            current, unused, independent=independent
        ),
        artifact_registrar=register,
    )
    claimed = service.claim(
        handle.run_id,
        "inference.one",
        1,
        handle_loader=lambda unused: handle,
        event_recorder=lambda *args, **kwargs: None,
    )
    return handle, output, claimed


def _rollout(tmp_path):
    source = tmp_path / "host" / "rollout.jsonl"
    source.parent.mkdir()
    fixture = (
        __import__("pathlib").Path(__file__).parents[1]
        / "trace/fixtures/codex/rollout.jsonl"
    )
    shutil.copyfile(fixture, source)
    return source


def _submission(claimed, output, handle, *, host_receipt_id):
    output.write_text(
        "**Rating**: Hold\nFINAL TRANSACTION PROPOSAL: HOLD\n",
        encoding="utf-8",
    )
    digest = artifacts.bind_artifact_hash(handle, "inference.output")["sha256"]
    return {
        "schema_version": 1,
        "envelope": claimed["result"]["envelope"],
        "plan_hash": claimed["result"]["plan_hash"],
        "outputs": [{"artifact_id": "inference.output", "sha256": digest}],
        "host_receipt_id": host_receipt_id,
    }


def test_bound_task_segment_enters_the_same_evidence_closure(tmp_path):
    from autoresearch.session_agent.host_evidence import bind_task_transcript

    handle, output, claimed = _running_case(tmp_path)
    bound = bind_task_transcript(
        handle.run_id,
        "inference.one",
        1,
        _rollout(tmp_path),
        context_ref="context-main",
        parent_context_ref=None,
        session_ref="session-main",
        start_ordinal=0,
        end_ordinal=13,
        context_source="MAIN",
        handle_loader=lambda unused: handle,
    )
    receipt = {
        "schema_version": 1,
        "engine": "codex",
        "session_ref": "session-main",
        "context_ref": "context-main",
        "parent_context_ref": None,
        "task_id": "inference.one",
        "attempt": 1,
        "completed": True,
        "evidence_refs": [bound["evidence_ref"]],
    }
    receipt_id = sha256_bytes(canonical_json(receipt).encode("utf-8"))

    service.submit(
        handle.run_id,
        _submission(claimed, output, handle, host_receipt_id=receipt_id),
        host_receipt=receipt,
        handle_loader=lambda unused: handle,
        validator=lambda value, task: None,
        event_recorder=lambda *args, **kwargs: None,
    )
    from autoresearch.session_agent.evidence import materialize_evidence

    closure = materialize_evidence(handle)
    task = json.loads(
        (handle.capsule / "evidence/tasks/inference.one/a1/evidence.json").read_text(
            encoding="utf-8"
        )
    )

    assert closure["completeness_ok"] is True
    assert task["transcript_refs"][0]["context_source"] == "MAIN"
    assert bound["tool_call_ids"]


def test_host_receipt_cannot_cite_a_nonexistent_binding(tmp_path):
    handle, output, claimed = _running_case(tmp_path, independent=True)
    receipt = {
        "schema_version": 1,
        "engine": "codex",
        "session_ref": "session-review",
        "context_ref": "context-review",
        "parent_context_ref": "context-main",
        "task_id": "inference.one",
        "attempt": 1,
        "completed": True,
        "evidence_refs": [f"host-binding:{'a' * 64}"],
    }
    receipt_id = sha256_bytes(canonical_json(receipt).encode("utf-8"))

    with pytest.raises(ValueError, match="evidence binding"):
        service.submit(
            handle.run_id,
            _submission(claimed, output, handle, host_receipt_id=receipt_id),
            host_receipt=receipt,
            handle_loader=lambda unused: handle,
            validator=lambda value, task: None,
            event_recorder=lambda *args, **kwargs: None,
        )


def test_begin_registers_declared_main_transcript_source(tmp_path):
    handle = _handle(tmp_path)
    source = _rollout(tmp_path)
    request = _request()
    request["host_profile"] = {
        **request["host_profile"],
        "evidence_refs": [f"transcript-file:{source}"],
    }
    input_path = handle.staging / "input.md"
    input_path.write_text("frozen task input", encoding="utf-8")

    def register(request, current, plan):
        artifacts.register_artifact(current, "inference.input", input_path, "READ")
        artifacts.register_artifact(
            current, "inference.output", current.staging / "inference.md", "WRITE"
        )

    service.begin(
        request,
        begin_capsule=lambda unused: handle,
        planner=_inference_plan,
        artifact_registrar=register,
    )
    registration = json.loads(
        (handle.capsule / "identity/session/host_evidence.json").read_text(
            encoding="utf-8"
        )
    )

    assert registration["main_transcript"]["status"] == "REGISTERED"
    assert registration["main_transcript"]["source_path"] == str(source.resolve())


def test_main_session_tools_after_begin_are_captured_in_run_scope(tmp_path):
    from autoresearch.session_agent.host_evidence import capture_main_context

    handle = _handle(tmp_path)
    source = tmp_path / "host" / "main.jsonl"
    source.parent.mkdir()
    source.write_text("", encoding="utf-8")
    request = _request()
    request["host_profile"] = {
        **request["host_profile"],
        "evidence_refs": [f"transcript-file:{source}"],
    }
    input_path = handle.staging / "input.md"
    input_path.write_text("frozen task input", encoding="utf-8")

    def register(request, current, plan):
        artifacts.register_artifact(current, "inference.input", input_path, "READ")
        artifacts.register_artifact(
            current, "inference.output", current.staging / "inference.md", "WRITE"
        )

    service.begin(
        request,
        begin_capsule=lambda unused: handle,
        planner=_inference_plan,
        artifact_registrar=register,
    )
    fixture = (
        __import__("pathlib").Path(__file__).parents[1]
        / "trace/fixtures/codex/rollout.jsonl"
    )
    source.write_bytes(fixture.read_bytes())

    captured = capture_main_context(handle)

    assert captured["status"] == "PRESENT"
    assert captured["start_ordinal"] == 0
    assert captured["tool_call_ids"]
    assert (handle.capsule / captured["raw_path"]).is_file()
