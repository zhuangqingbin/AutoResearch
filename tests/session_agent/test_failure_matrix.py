from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from autoresearch.session_agent import service, store

from .test_service import _handle, _planner, _request


def test_interrupted_claim_remains_owned_and_is_not_reassigned(tmp_path):
    handle = _handle(tmp_path)
    service.begin(_request(), begin_capsule=lambda request: handle, planner=_planner)
    service.claim(handle.run_id, "step.one", 1, handle_loader=lambda run_id: handle)
    resumed = service.resume(handle.run_id, handle_loader=lambda run_id: handle)
    assert resumed["state"] == "WAITING"
    assert resumed["tasks"][0]["task_id"] == "step.one"
    with pytest.raises(RuntimeError, match="already claimed"):
        service.claim(handle.run_id, "step.one", 2, handle_loader=lambda run_id: handle)


def test_frozen_run_is_never_reactivated_by_resume():
    def frozen_loader(run_id):
        raise RuntimeError(f"run {run_id} is not ACTIVE: SUCCEEDED")

    with pytest.raises(RuntimeError, match="not ACTIVE"):
        service.resume("20260913T010203000000Z", handle_loader=frozen_loader)


def test_failed_attempt_is_frozen_before_a_retry_can_replace_owner_state(tmp_path):
    handle = _handle(tmp_path)
    service.begin(_request(), begin_capsule=lambda request: handle, planner=_planner)
    service.claim(handle.run_id, "step.one", 1, handle_loader=lambda run_id: handle)

    service.fail(
        handle.run_id,
        "step.one",
        1,
        "TIMEOUT",
        "captured timeout",
        handle_loader=lambda run_id: handle,
    )

    failure = json.loads(
        (
            handle.capsule
            / "evidence/attempt_records/step.one/a1/failure.json"
        ).read_text(encoding="utf-8")
    )
    assert failure["error"] == {"code": "TIMEOUT", "message": "captured timeout"}


def test_resume_probes_the_authoritative_deterministic_attempt(tmp_path, monkeypatch):
    handle = _handle(tmp_path)
    service.begin(
        _request(),
        begin_capsule=lambda request: handle,
        planner=_planner,
        artifact_registrar=lambda request, active, plan: None,
    )
    store_path = handle.workspace / "session/tasks.json"
    store.claim(store_path, "step.one", 1, "session-main")
    store.mark_failed(
        store_path, "step.one", 1, {"code": "retry"}, retryable=True
    )
    store.claim(store_path, "step.one", 2, "session-main")
    seen = []
    monkeypatch.setattr(
        service.executor,
        "probe_execution",
        lambda active, task_id, attempt: seen.append(attempt)
        or {"state": "RUNNING", "invocation_id": "x"},
    )
    service.resume(handle.run_id, handle_loader=lambda run_id: handle)
    assert seen == [2]


def test_successor_requires_a_terminal_same_engine_predecessor(tmp_path):
    handle = _handle(tmp_path)
    request = {**_request(), "predecessor_run_id": "20260912T010203000000Z"}
    active = SimpleNamespace(engine="codex", business_status="ACTIVE")
    with pytest.raises(ValueError, match="terminal"):
        service.begin(
            request,
            begin_capsule=lambda value: handle,
            planner=_planner,
            predecessor_loader=lambda run_id: active,
        )
    terminal = SimpleNamespace(engine="codex", business_status="SUCCEEDED")
    service.begin(
        request,
        begin_capsule=lambda value: handle,
        planner=_planner,
        predecessor_loader=lambda run_id: terminal,
    )
    frozen = json.loads((handle.workspace / "session/request.json").read_text())
    assert frozen["predecessor_run_id"] == request["predecessor_run_id"]


def test_successor_rejects_cross_engine_predecessor(tmp_path):
    handle = _handle(tmp_path)
    request = {**_request(), "predecessor_run_id": "20260912T010203000000Z"}
    predecessor = SimpleNamespace(engine="claude", business_status="SUCCEEDED")
    with pytest.raises(ValueError, match="engine"):
        service.begin(
            request,
            begin_capsule=lambda value: handle,
            planner=_planner,
            predecessor_loader=lambda run_id: predecessor,
        )
