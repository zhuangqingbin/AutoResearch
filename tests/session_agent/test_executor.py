from __future__ import annotations

from types import SimpleNamespace

import pytest

from autoresearch.session_agent import executor


def _handle(tmp_path):
    capsule = tmp_path / "capsule"
    (capsule / "events").mkdir(parents=True)
    return SimpleNamespace(
        workspace=tmp_path,
        capsule=capsule,
        engine="codex",
        run_id="20260913T010203000000Z",
    )


def _task():
    return {
        "task_id": "test.noop",
        "kind": "DETERMINISTIC",
        "operation": "test.noop",
        "subject": None,
    }


def test_execute_passes_exact_argv_and_identity_to_capture(tmp_path):
    calls = []

    def runner(handle, stage, argv, invocation_id, attempt, subject):
        calls.append((stage, argv, invocation_id, attempt, subject))
        return SimpleNamespace(exit_code=0, invocation={"status": "COMPLETED"})

    result = executor.execute_operation(
        _handle(tmp_path), _task(), 1, {"message": "ok"}, runner=runner
    )
    assert result["status"] == "SUCCEEDED"
    assert calls == [(
        "session", executor.build_argv("test.noop", {"message": "ok"}),
        "session-test-noop-a1", 1, None,
    )]


def test_nonzero_exit_is_not_success(tmp_path):
    def runner(*args, **kwargs):
        return SimpleNamespace(exit_code=23, invocation={"status": "FAILED"})

    result = executor.execute_operation(
        _handle(tmp_path), _task(), 1, {"message": "ok"}, runner=runner
    )
    assert result["status"] == "FAILED"
    assert result["exit_code"] == 23


def test_live_record_blocks_duplicate_execution(tmp_path, monkeypatch):
    monkeypatch.setattr(executor, "probe_execution", lambda *args: {"state": "RUNNING"})
    with pytest.raises(executor.OperationRunning):
        executor.execute_operation(
            _handle(tmp_path), _task(), 1, {"message": "ok"}, runner=lambda *a, **k: None
        )

