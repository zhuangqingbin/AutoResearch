from __future__ import annotations

import json

import pytest

from autoresearch.session_agent import artifacts, service

from .test_service import _handle, _planner, _request


def test_lite_resume_preserves_input_identity(tmp_path):
    handle = _handle(tmp_path)
    service.begin(_request(), begin_capsule=lambda request: handle, planner=_planner)
    slim = handle.staging / "one.txt"
    artifacts.register_artifact(handle, "step.one.output", slim, "WRITE")
    service.claim(handle.run_id, "step.one", 1, handle_loader=lambda run_id: handle)
    slim.write_text("2026-09-13 input")
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
    before = json.loads((handle.workspace / "session/artifacts.json").read_text())
    service.resume(handle.run_id, handle_loader=lambda run_id: handle)
    after = json.loads((handle.workspace / "session/artifacts.json").read_text())
    assert before == after


def test_claimed_research_rejects_input_changed_in_place(tmp_path):
    handle = _handle(tmp_path)
    service.begin(_request(), begin_capsule=lambda request: handle, planner=_planner)
    slim = handle.staging / "one.txt"
    card = handle.staging / "two.md"
    artifacts.register_artifact(handle, "step.one.output", slim, "WRITE")
    artifacts.register_artifact(handle, "step.two.output", card, "WRITE")
    service.claim(handle.run_id, "step.one", 1, handle_loader=lambda run_id: handle)
    slim.write_text("600519.SS 2026-09-13 verified financials")
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
    slim.write_text("000858.SZ financials replaced the claimed input")
    card.write_text("**Rating**: Hold\nFINAL TRANSACTION PROPOSAL: HOLD\n")
    digest = artifacts.bind_artifact_hash(handle, "step.two.output")["sha256"]
    submission = {
        "schema_version": 1,
        "envelope": claimed["result"]["envelope"],
        "plan_hash": claimed["result"]["plan_hash"],
        "outputs": [{"artifact_id": "step.two.output", "sha256": digest}],
        "host_receipt_id": None,
    }
    with pytest.raises(artifacts.ArtifactConflict, match="content changed"):
        service.submit(
            handle.run_id,
            submission,
            handle_loader=lambda run_id: handle,
            validator=lambda submitted, task: None,
            event_recorder=lambda *args, **kwargs: None,
        )


def test_no_data_never_reaches_publication(tmp_path):
    handle = _handle(tmp_path)
    service.begin(_request(), begin_capsule=lambda request: handle, planner=_planner)
    service.claim(handle.run_id, "step.one", 1, handle_loader=lambda run_id: handle)
    failed = service.execute(
        handle.run_id,
        "step.one",
        1,
        {"message": "ok"},
        handle_loader=lambda run_id: handle,
        runner=lambda *args, **kwargs: type(
            "Result", (), {"exit_code": 2, "invocation": {"status": "FAILED"}}
        )(),
    )
    assert failed["state"] == "BLOCKED"
    published = []
    with pytest.raises(RuntimeError, match="incomplete"):
        service.finish(
            handle.run_id,
            handle_loader=lambda run_id: handle,
            publisher=lambda current: published.append(current),
        )
    assert published == []
