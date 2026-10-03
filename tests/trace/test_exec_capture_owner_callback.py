"""Bounded owner callbacks preserve capture evidence and child cleanup."""
import json
import os
import signal
import sys
import threading

import pytest
from test_exec_capture import _begin

from autoresearch.trace import exec_capture


def test_owner_callback_unblocks_an_actual_captured_child(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    marker = tmp_path / "release"
    owner = threading.get_ident()
    calls = []
    def callback(can_dispatch):
        assert threading.get_ident() == owner
        assert can_dispatch()
        calls.append(True)
        marker.write_text("owner processed inference")
    result = exec_capture.run_captured(handle, "l4", [sys.executable, "-c",
        "import pathlib,sys,time; p=pathlib.Path(sys.argv[1]); "
        "exec('while not p.exists(): time.sleep(.01)'); print(p.read_text())", str(marker)],
        "owner-callback", owner_callback=callback)
    assert result.exit_code == 0 and calls
    assert result.invocation["status"] == "COMPLETED"


@pytest.mark.parametrize("mode", ["exception", "signal"])
def test_owner_callback_failure_cleans_up_and_restores_signal_handlers(tmp_path, monkeypatch, mode):
    handle = _begin(tmp_path, monkeypatch)
    prior = signal.getsignal(signal.SIGTERM)
    calls = []
    def callback(can_dispatch):
        calls.append(True)
        if mode == "signal":
            os.kill(os.getpid(), signal.SIGTERM)
            assert not can_dispatch()
        else:
            raise RuntimeError("callback failed")
    expected = KeyboardInterrupt if mode == "signal" else RuntimeError
    with pytest.raises(expected):
        exec_capture.run_captured(handle, "l4", [sys.executable, "-c", "import time; time.sleep(5)"],
                                  "failed-callback", owner_callback=callback, termination_grace=.1)
    assert len(calls) == 1
    assert signal.getsignal(signal.SIGTERM) == prior
    index = json.loads((handle.capsule / "events/invocations.json").read_text())
    assert index["failed-callback"]["status"] == "FAILED"


def test_prepared_facts_capture_preserves_inputs_and_offline_replay(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    from autoresearch.common.atomic import sha256_file
    from autoresearch.session_agent import artifacts, card_facts, executor

    handle = _begin(tmp_path, monkeypatch)
    inputs = {}
    for key in ("raw.slim", "raw.deep", "research.frame"):
        path = handle.workspace / key
        path.write_text(key + " frozen bytes")
        artifacts.register_artifact(handle, key, path, "READ")
        inputs[key] = path
    task = {"kind": "DETERMINISTIC", "operation": "research.card.facts",
            "task_id": "facts", "subject": "600519", "input_artifact_ids": list(inputs)}
    result = executor.execute_operation(handle, task, 1, {}, owner_callback=lambda alive: None)
    assert result["status"] == "SUCCEEDED"
    assert result["prepared_output"] == card_facts.project_facts(
        "600519", {key: path.read_bytes() for key, path in inputs.items()})
    frozen = handle.capsule / "evidence/attempt_records/facts/a1/operation_request.json"
    assert json.loads(frozen.read_text())["operation"] == "research.card.facts"
    request = handle.capsule / result["prepared_request"]["ref"]
    assert sha256_file(request) == result["prepared_request"]["sha256"]
    replay_out = tmp_path / "replayed.json"
    replay_inputs = tmp_path / "replay_inputs"
    (replay_inputs / "artifacts").mkdir(parents=True)
    for key, path in inputs.items():
        (replay_inputs / "artifacts" / key).write_bytes(path.read_bytes())
    context = SimpleNamespace(operation_request=json.loads(frozen.read_text()),
                              inputs=replay_inputs,
                              output_path=lambda key: replay_out)
    card_facts.replay({"input_refs": [{"artifact_id": key} for key in inputs],
                       "expected_outputs": [{"artifact_id": "facts"}]}, context)
    assert json.loads(replay_out.read_text()) == result["prepared_output"]
    invocation = json.loads((handle.capsule / "events/invocations.json").read_text())[result["invocation_id"]]
    assert invocation["status"] == "COMPLETED"
    assert "--prepared-request" in invocation["argv"]


def test_normal_child_exit_during_callback_does_not_revoke_dispatch_permission(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    marker = tmp_path / "exit"
    children = []
    original = exec_capture.subprocess.Popen
    def spawn(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(exec_capture.subprocess, "Popen", spawn)
    def callback(can_dispatch):
        marker.write_text("finish normally")
        assert children[0].wait(timeout=2) == 0
        assert can_dispatch()  # normal completion is not cancellation after claim
    result = exec_capture.run_captured(handle, "l4", [sys.executable, "-c",
        "import pathlib,sys,time; p=pathlib.Path(sys.argv[1]); "
        "exec('while not p.exists(): time.sleep(.01)')", str(marker)],
        "normal-exit-callback", owner_callback=callback)
    assert result.exit_code == 0
