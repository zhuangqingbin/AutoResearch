"""Forensic command capture: exact streams, lifecycle facts, and isolation."""

from __future__ import annotations

import gzip
import io
import json
import os
import signal
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

from autoresearch.common import workspace as ws
from autoresearch.trace import exec_capture as exec_mod
from autoresearch.trace.capsule import begin_run
from autoresearch.trace.exec_capture import main, run_captured

DATE = "2026-08-27"
NOW = datetime(2026, 8, 27, 1, 2, 3, 456789, tzinfo=timezone.utc)


def _begin(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ws, "ENGINE", "codex")
    monkeypatch.setattr(ws, "context_root", lambda: tmp_path / "context_codex")
    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path / "reports_codex")
    monkeypatch.setattr(
        "autoresearch.scan.user_config.DEFAULT_PINNED_PATH",
        tmp_path / "missing-pinned.jsonc",
    )
    monkeypatch.delenv("AUTORESEARCH_RUN_ID", raising=False)
    return begin_run("scan-market", DATE, "codex", {}, now=NOW)


def _events(handle) -> list[dict]:
    path = handle.capsule / "events/events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _gzip_bytes(path: Path) -> bytes:
    with gzip.open(path, "rb") as handle:
        return handle.read()


def test_capture_preserves_exact_argv_and_separate_binary_streams(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    script = "import os;os.write(1,b'out\\x00\\xff\\n');os.write(2,b'err\\x00\\xfe\\n')"

    result = run_captured(
        handle,
        stage="frame",
        argv=[sys.executable, "-c", script],
        invocation_id="inv-frame-1",
    )

    assert result.exit_code == 0
    index = json.loads((handle.capsule / "events/invocations.json").read_text(encoding="utf-8"))
    meta = index["inv-frame-1"]
    assert meta["argv"] == [sys.executable, "-c", script]
    assert meta["status"] == "COMPLETED"
    assert _gzip_bytes(handle.capsule / "logs/frame/inv-frame-1.stdout.log.gz") == b"out\x00\xff\n"
    assert _gzip_bytes(handle.capsule / "logs/frame/inv-frame-1.stderr.log.gz") == b"err\x00\xfe\n"
    gzip_header = (handle.capsule / "logs/frame/inv-frame-1.stdout.log.gz").read_bytes()[:10]
    assert gzip_header[3] & 0x08 == 0  # no unstable original-filename header
    assert gzip_header[4:8] == b"\x00\x00\x00\x00"
    assert [row["event_type"] for row in _events(handle)[-2:]] == [
        "COMMAND_STARTED",
        "COMMAND_COMPLETED",
    ]


def test_capture_records_nonzero_as_one_terminal_failure(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    result = run_captured(
        handle,
        stage="gate1",
        argv=[sys.executable, "-c", "raise SystemExit(23)"],
        invocation_id="gate1-attempt-1",
        attempt=1,
        subject="market",
    )

    assert result.exit_code == 23
    assert result.invocation["status"] == "FAILED"
    terminal = [row for row in _events(handle) if row["invocation_id"] == "gate1-attempt-1"]
    assert [row["event_type"] for row in terminal] == [
        "COMMAND_STARTED",
        "COMMAND_FAILED",
    ]
    assert terminal[-1]["payload"]["exit_code"] == 23


def test_capture_records_spawn_failure_without_inventing_child_exit(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    result = run_captured(
        handle,
        stage="frame",
        argv=[str(tmp_path / "definitely-missing-executable")],
        invocation_id="spawn-failure-1",
    )

    assert result.exit_code is None
    assert result.invocation["status"] == "FAILED"
    assert result.invocation["error"]["type"] == "FileNotFoundError"
    rows = [row for row in _events(handle) if row["invocation_id"] == "spawn-failure-1"]
    assert [row["event_type"] for row in rows] == [
        "COMMAND_STARTED",
        "COMMAND_FAILED",
    ]
    assert rows[-1]["payload"]["exit_code"] is None


def test_capture_records_secret_presence_without_any_secret_value(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    monkeypatch.setenv("TUSHARE_TOKEN", "super-secret-value")
    monkeypatch.setenv("VENDOR_API_KEY", "another-secret-value")
    result = run_captured(
        handle,
        stage="frame",
        argv=[sys.executable, "-c", "pass"],
        invocation_id="redaction-1",
    )

    encoded = json.dumps(result.invocation, ensure_ascii=False)
    assert result.invocation["environment"]["TUSHARE_TOKEN"] == {"present": True}
    assert result.invocation["environment"]["VENDOR_API_KEY"] == {"present": True}
    assert "super-secret-value" not in encoded
    assert "another-secret-value" not in encoded
    assert "super-secret-value" not in (handle.capsule / "events/invocations.json").read_text(
        encoding="utf-8"
    )


def test_capture_injects_run_stage_and_invocation_into_child(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    script = (
        "import json,os;"
        "print(json.dumps({k:os.environ[k] for k in "
        "['AUTORESEARCH_ENGINE','AUTORESEARCH_RUN_ID',"
        "'AUTORESEARCH_STAGE','AUTORESEARCH_INVOCATION_ID']},sort_keys=True))"
    )
    run_captured(
        handle,
        stage="frame",
        argv=[sys.executable, "-c", script],
        invocation_id="child-env-1",
    )

    child = json.loads(_gzip_bytes(handle.capsule / "logs/frame/child-env-1.stdout.log.gz"))
    assert child == {
        "AUTORESEARCH_ENGINE": "codex",
        "AUTORESEARCH_INVOCATION_ID": "child-env-1",
        "AUTORESEARCH_RUN_ID": handle.run_id,
        "AUTORESEARCH_STAGE": "frame",
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("stage", "../frame"),
        ("stage", 3),
        ("invocation_id", "inv/frame"),
        ("invocation_id", ""),
        ("subject", "../600000"),
        ("subject", 600000),
        ("attempt", 0),
        ("attempt", True),
    ],
)
def test_capture_rejects_unsafe_identity_before_writing_evidence(
    tmp_path, monkeypatch, field, value
):
    handle = _begin(tmp_path, monkeypatch)
    kwargs = {
        "stage": "frame",
        "argv": [sys.executable, "-c", "pass"],
        "invocation_id": "safe-1",
        "subject": None,
        "attempt": 1,
    }
    kwargs[field] = value
    before = (handle.capsule / "events/events.jsonl").read_bytes()

    with pytest.raises((TypeError, ValueError)):
        run_captured(handle, **kwargs)

    assert (handle.capsule / "events/events.jsonl").read_bytes() == before


@pytest.mark.parametrize("argv", ["echo unsafe", [], [""], [sys.executable, 3]])
def test_capture_requires_an_exact_non_shell_argument_vector(tmp_path, monkeypatch, argv):
    handle = _begin(tmp_path, monkeypatch)
    with pytest.raises((TypeError, ValueError)):
        run_captured(
            handle,
            stage="frame",
            argv=argv,
            invocation_id="argv-1",
        )


def test_duplicate_invocation_id_never_overwrites_prior_evidence(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    first = run_captured(
        handle,
        stage="frame",
        argv=[sys.executable, "-c", "print('first')"],
        invocation_id="duplicate-1",
    )
    before = json.loads((handle.capsule / "events/invocations.json").read_text(encoding="utf-8"))[
        "duplicate-1"
    ]

    with pytest.raises(ValueError, match="duplicate invocation_id"):
        run_captured(
            handle,
            stage="frame",
            argv=[sys.executable, "-c", "print('second')"],
            invocation_id="duplicate-1",
        )

    after = json.loads((handle.capsule / "events/invocations.json").read_text(encoding="utf-8"))[
        "duplicate-1"
    ]
    assert first.invocation == before == after
    assert _gzip_bytes(handle.capsule / "logs/frame/duplicate-1.stdout.log.gz") == b"first\n"


def test_concurrent_invocations_update_index_without_lost_rows(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)

    def capture(number: int):
        return run_captured(
            handle,
            stage="l4",
            argv=[sys.executable, "-c", f"print({number})"],
            invocation_id=f"l4-60000{number}-attempt-1",
            subject=f"60000{number}",
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(capture, range(4)))

    index = json.loads((handle.capsule / "events/invocations.json").read_text(encoding="utf-8"))
    assert set(index) == {f"l4-60000{i}-attempt-1" for i in range(4)}
    assert all(result.exit_code == 0 for result in results)


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX process groups")
def test_sigterm_is_forwarded_and_previous_handler_is_restored(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    previous = signal.getsignal(signal.SIGTERM)

    def prior_handler(signum, frame):  # pragma: no cover - must only be restored
        raise AssertionError("prior handler ran during capture")

    signal.signal(signal.SIGTERM, prior_handler)
    timer = threading.Timer(0.3, os.kill, args=(os.getpid(), signal.SIGTERM))
    timer.start()
    try:
        result = run_captured(
            handle,
            stage="prelude",
            argv=[sys.executable, "-c", "import time; time.sleep(10)"],
            invocation_id="signal-1",
        )
        assert result.exit_code == -signal.SIGTERM
        assert result.invocation["signal"] == signal.SIGTERM
        assert signal.getsignal(signal.SIGTERM) is prior_handler
    finally:
        timer.cancel()
        signal.signal(signal.SIGTERM, previous)


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX process groups")
def test_forwarded_signal_is_a_failure_even_when_child_exits_zero(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    script = (
        "import signal,time;"
        "signal.signal(signal.SIGTERM,lambda *_:exit(0));"
        "print('ready',flush=True);"
        "time.sleep(10)"
    )
    timer = threading.Timer(0.3, os.kill, args=(os.getpid(), signal.SIGTERM))
    timer.start()
    try:
        result = run_captured(
            handle,
            stage="prelude",
            argv=[sys.executable, "-c", script],
            invocation_id="signal-caught-1",
        )
    finally:
        timer.cancel()

    assert result.exit_code == 0
    assert result.invocation["signal"] == signal.SIGTERM
    assert result.invocation["status"] == "FAILED"
    assert _events(handle)[-1]["event_type"] == "COMMAND_FAILED"


def test_signal_handling_degrades_explicitly_outside_main_thread(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(
            run_captured,
            handle,
            stage="frame",
            argv=[sys.executable, "-c", "pass"],
            invocation_id="thread-1",
        ).result()
    assert result.invocation["signal_handling"] == {
        "installed": False,
        "reason": "not-main-thread",
    }


def test_signal_setup_failure_degrades_explicitly_and_still_runs(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    real_signal = signal.signal
    calls = 0

    def fail_first_install(signum, handler):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("signal API unavailable")
        return real_signal(signum, handler)

    monkeypatch.setattr(signal, "signal", fail_first_install)
    result = run_captured(
        handle,
        stage="frame",
        argv=[sys.executable, "-c", "print('still-ran')"],
        invocation_id="signal-degraded-1",
    )

    assert result.exit_code == 0
    assert result.invocation["signal_handling"] == {
        "installed": False,
        "reason": "signal-api-unavailable: ValueError",
    }


def test_log_destination_symlink_is_rejected_without_spawning(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    (handle.capsule / "logs").symlink_to(outside, target_is_directory=True)
    marker = tmp_path / "spawned"

    with pytest.raises(ValueError, match="symlink"):
        run_captured(
            handle,
            stage="frame",
            argv=[sys.executable, "-c", f"open({str(marker)!r},'w').write('bad')"],
            invocation_id="symlink-1",
        )
    assert not marker.exists()


def test_log_allocation_failure_leaves_no_started_command_or_temp(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    before = _events(handle)
    original = exec_mod._make_raw_temp
    calls = 0

    def fail_second(directory, stream_name):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk allocation failed")
        return original(directory, stream_name)

    monkeypatch.setattr(exec_mod, "_make_raw_temp", fail_second)
    with pytest.raises(OSError, match="disk allocation failed"):
        run_captured(
            handle,
            stage="frame",
            argv=[sys.executable, "-c", "print('must-not-start')"],
            invocation_id="allocation-failure-1",
        )

    assert _events(handle) == before
    index = handle.capsule / "events/invocations.json"
    assert not index.exists()
    assert not list((handle.capsule / "logs/frame").glob("*.tmp"))


def test_reader_thread_start_failure_terminates_child_and_records_failure(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    real_start = threading.Thread.start
    calls = 0

    def fail_first_start(thread):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("reader thread unavailable")
        return real_start(thread)

    monkeypatch.setattr(threading.Thread, "start", fail_first_start)
    result = run_captured(
        handle,
        stage="frame",
        argv=[sys.executable, "-c", "import time; time.sleep(10)"],
        invocation_id="reader-start-failure-1",
    )

    assert result.invocation["status"] == "FAILED"
    assert result.invocation["error"]["phase"] == "reader-start"
    assert [
        row["event_type"]
        for row in _events(handle)
        if row["invocation_id"] == "reader-start-failure-1"
    ] == ["COMMAND_STARTED", "COMMAND_FAILED"]


def test_reader_error_is_recorded_as_capture_failure(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    original = exec_mod._reader

    def fail_stdout_reader(pipe, raw_fd, parent_stream, errors, name):
        if name != "stdout":
            return original(pipe, raw_fd, parent_stream, errors, name)
        pipe.read()
        pipe.close()
        errors.append({"stream": name, "type": "OSError", "message": "simulated reader fault"})

    monkeypatch.setattr(exec_mod, "_reader", fail_stdout_reader)
    result = run_captured(
        handle,
        stage="frame",
        argv=[sys.executable, "-c", "print('captured reader fault')"],
        invocation_id="reader-failure-1",
    )

    assert result.exit_code == 0
    assert result.invocation["status"] == "FAILED"
    assert result.invocation["error"] == {
        "stream": "stdout",
        "type": "OSError",
        "message": "simulated reader fault",
    }
    assert _events(handle)[-1]["event_type"] == "COMMAND_FAILED"


def test_cli_requires_separator_and_returns_child_outcome(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    with pytest.raises(SystemExit):
        main(
            [
                "--run-id",
                handle.run_id,
                "--stage",
                "frame",
                "--invocation-id",
                "cli-missing-separator",
                sys.executable,
                "-c",
                "pass",
            ]
        )
    assert (
        main(
            [
                "--run-id",
                handle.run_id,
                "--stage",
                "frame",
                "--invocation-id",
                "cli-exit-1",
                "--",
                sys.executable,
                "-c",
                "raise SystemExit(19)",
            ]
        )
        == 19
    )
