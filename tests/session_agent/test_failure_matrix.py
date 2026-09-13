from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from autoresearch.session_agent import service

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
