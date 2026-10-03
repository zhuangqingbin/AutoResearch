from __future__ import annotations

import json

import pytest

from autoresearch.session_agent import artifacts, service

from .test_service import _handle, _planner, _request


def test_begin_mirrors_frozen_identity_into_capsule(tmp_path):
    handle = _handle(tmp_path)
    service.begin(_request(), begin_capsule=lambda request: handle, planner=_planner)
    identity = handle.capsule / "identity" / "session"
    assert json.loads((identity / "request.json").read_text()) == _request()
    assert json.loads((identity / "plan.json").read_text())["plan_hash"]
    assert json.loads((identity / "roles.json").read_text())["roles_hash"]


def test_inference_boundaries_emit_only_after_claim_and_submit(tmp_path):
    handle = _handle(tmp_path)
    service.begin(_request(), begin_capsule=lambda request: handle, planner=_planner)
    output_one = handle.staging / "one.txt"
    output_two = handle.staging / "two.md"
    artifacts.register_artifact(handle, "step.one.output", output_one, "WRITE")
    artifacts.register_artifact(handle, "step.two.output", output_two, "WRITE")
    events = []
    service.claim(
        handle.run_id,
        "step.one",
        1,
        handle_loader=lambda run_id: handle,
        event_recorder=lambda *args, **kwargs: events.append((args, kwargs)),
    )
    assert events == []
    output_one.write_text("ok")
    service.execute(
        handle.run_id,
        "step.one",
        1,
        {"message": "ok"},
        handle_loader=lambda run_id: handle,
        runner=lambda *args, **kwargs: type(
            "Result", (), {"exit_code": 0, "invocation": {"status": "COMPLETED"}}
        )(),
    )
    claimed = service.claim(
        handle.run_id,
        "step.two",
        1,
        handle_loader=lambda run_id: handle,
        event_recorder=lambda *args, **kwargs: events.append((args, kwargs)),
    )
    assert [item[0][1] for item in events] == ["AGENT_DISPATCHED"]
    request_files = list((handle.capsule / "agents/session/requests").glob("*.json"))
    assert len(request_files) == 1

    from pathlib import Path
    output_two = Path(claimed['result']['claim_receipt']['output_paths']['step.two.output'])
    output_two.write_text("**Rating**: Hold\nFINAL TRANSACTION PROPOSAL: HOLD\n")
    from autoresearch.common.atomic import sha256_bytes
    digest = sha256_bytes(output_two.read_bytes())
    submission = {
        "schema_version": 1,
        "envelope": claimed["result"]["envelope"],
        "plan_hash": claimed["result"]["plan_hash"],
        "outputs": [{"artifact_id": "step.two.output", "sha256": digest}],
        "host_receipt_id": None,
    }
    service.submit(
        handle.run_id,
        submission,
        handle_loader=lambda run_id: handle,
        validator=lambda value, task: None,
        event_recorder=lambda *args, **kwargs: events.append((args, kwargs)),
    )
    assert [item[0][1] for item in events] == [
        "AGENT_DISPATCHED",
        "AGENT_COMPLETED",
    ]


def test_resume_repairs_terminal_event_after_event_append_failure(tmp_path):
    handle = _handle(tmp_path)
    service.begin(_request(), begin_capsule=lambda request: handle, planner=_planner)
    output_one = handle.staging / "one.txt"
    output_two = handle.staging / "two.md"
    artifacts.register_artifact(handle, "step.one.output", output_one, "WRITE")
    artifacts.register_artifact(handle, "step.two.output", output_two, "WRITE")
    service.claim(handle.run_id, "step.one", 1, handle_loader=lambda run_id: handle)
    output_one.write_text("ok")
    service.execute(
        handle.run_id,
        "step.one",
        1,
        {"message": "ok"},
        handle_loader=lambda run_id: handle,
        runner=lambda *args, **kwargs: type(
            "Result", (), {"exit_code": 0, "invocation": {"status": "COMPLETED"}}
        )(),
    )
    claimed = service.claim(
        handle.run_id,
        "step.two",
        1,
        handle_loader=lambda run_id: handle,
        event_recorder=lambda *args, **kwargs: None,
    )
    from pathlib import Path
    output_two = Path(claimed['result']['claim_receipt']['output_paths']['step.two.output'])
    output_two.write_text("hold")
    from autoresearch.common.atomic import sha256_bytes
    digest = sha256_bytes(output_two.read_bytes())
    submission = {
        "schema_version": 1,
        "envelope": claimed["result"]["envelope"],
        "plan_hash": claimed["result"]["plan_hash"],
        "outputs": [{"artifact_id": "step.two.output", "sha256": digest}],
        "host_receipt_id": None,
    }

    def fail_event(*args, **kwargs):
        raise OSError("event chain unavailable")

    with pytest.raises(OSError, match="event chain"):
        service.submit(
            handle.run_id,
            submission,
            handle_loader=lambda run_id: handle,
            validator=lambda value, task: None,
            event_recorder=fail_event,
        )
    repaired = []
    result = service.resume(
        handle.run_id,
        handle_loader=lambda run_id: handle,
        event_recorder=lambda *args, **kwargs: repaired.append((args, kwargs)),
    )
    assert result["state"] == "DONE"
    assert [item[0][1] for item in repaired] == ["AGENT_COMPLETED"]
