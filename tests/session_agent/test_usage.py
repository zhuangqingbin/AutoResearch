from __future__ import annotations

from autoresearch.session_agent import service

from .test_service import _handle, _planner, _request


def test_handoff_request_is_not_counted_as_a_transcript(tmp_path):
    handle = _handle(tmp_path)
    service.begin(_request(), begin_capsule=lambda request: handle, planner=_planner)
    service.claim(
        handle.run_id,
        "step.one",
        1,
        handle_loader=lambda run_id: handle,
        event_recorder=lambda *args, **kwargs: None,
    )
    assert not (handle.capsule / "agents/bindings.jsonl").exists()
    assert not (handle.capsule / "usage/_token_usage.json").exists()

