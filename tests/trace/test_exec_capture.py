"""Forensic command capture: exact streams, lifecycle facts, and isolation."""

from __future__ import annotations

import gzip
import io
import json
import os
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from multiprocessing import get_context
from pathlib import Path

import pytest

from autoresearch.common import workspace as ws
from autoresearch.trace import exec_capture as exec_mod
from autoresearch.trace.capsule import begin_run
from autoresearch.trace.exec_capture import main, run_captured

DATE = "2026-08-27"
NOW = datetime(2026, 8, 27, 1, 2, 3, 456789, tzinfo=timezone.utc)
ERROR_FIELDS = {
    "classification",
    "category",
    "summary",
    "message",
    "exception_type",
    "type",
    "phase",
    "traceback",
}


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


def _assert_terminal_error_agrees(handle, result, invocation_id: str) -> dict:
    error = result.invocation["error"]
    assert set(error) == ERROR_FIELDS
    assert all(error[key] for key in ERROR_FIELDS - {"traceback"})
    terminal = [
        row
        for row in _events(handle)
        if row["invocation_id"] == invocation_id and row["event_type"] == "COMMAND_FAILED"
    ]
    assert len(terminal) == 1
    assert terminal[0]["payload"]["error"] == error
    index = json.loads((handle.capsule / "events/invocations.json").read_text(encoding="utf-8"))
    assert index[invocation_id]["error"] == error
    return error


def _multiprocess_capture(handle, number: int) -> None:
    run_captured(
        handle,
        stage="l4",
        argv=[sys.executable, "-c", f"print({number})"],
        invocation_id=f"mp-l4-60000{number}-attempt-1",
        subject=f"60000{number}",
    )


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
    error = _assert_terminal_error_agrees(handle, result, "gate1-attempt-1")
    assert error == {
        "classification": "PROCESS_NONZERO_EXIT",
        "category": "PROCESS",
        "summary": "child exited with code 23",
        "message": "child exited with code 23",
        "exception_type": "ProcessExit",
        "type": "ProcessExit",
        "phase": "wait",
        "traceback": None,
    }


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
    error = _assert_terminal_error_agrees(handle, result, "spawn-failure-1")
    assert error["classification"] == "SPAWN_FAILURE"
    assert error["category"] == "WRAPPER"
    assert error["exception_type"] == "FileNotFoundError"
    assert error["phase"] == "spawn"
    assert "Traceback (most recent call last)" in error["traceback"]
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


def test_recorded_allowlisted_environment_redacts_embedded_secret_values(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    secret = "duplicated-secret-token"
    monkeypatch.setenv("TUSHARE_TOKEN", secret)
    monkeypatch.setenv("PATH", f"/safe/bin:{secret}:/more/bin")
    monkeypatch.setenv("PYTHONPATH", f"/project/{secret}/src")

    result = run_captured(
        handle,
        stage="frame",
        argv=[sys.executable, "-c", "pass"],
        invocation_id="embedded-env-redaction-1",
    )

    encoded = json.dumps(result.invocation, ensure_ascii=False)
    assert secret not in encoded
    assert result.invocation["environment"]["TUSHARE_TOKEN"] == {"present": True}
    assert result.invocation["environment"]["PATH"] == "/safe/bin:[REDACTED]:/more/bin"
    assert result.invocation["environment"]["PYTHONPATH"] == "/project/[REDACTED]/src"


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


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
@pytest.mark.parametrize("encoding", ["gz", "raw"])
@pytest.mark.parametrize("target_kind", ["file", "symlink"])
def test_preexisting_final_log_target_is_rejected_before_started_or_child(
    tmp_path, monkeypatch, stream, encoding, target_kind
):
    handle = _begin(tmp_path, monkeypatch)
    log_dir = handle.capsule / "logs/frame"
    log_dir.mkdir(parents=True, exist_ok=True)
    destination = log_dir / f"owned-target-1.{stream}.log.{encoding}"
    if target_kind == "file":
        destination.write_bytes(b"STALE")
    else:
        outside = tmp_path / f"outside-{stream}-{encoding}"
        outside.write_bytes(b"STALE")
        destination.symlink_to(outside)
    marker = tmp_path / "child-started"
    before = _events(handle)

    with pytest.raises((FileExistsError, ValueError), match="evidence"):
        run_captured(
            handle,
            stage="frame",
            argv=[sys.executable, "-c", f"open({str(marker)!r},'w').write('bad')"],
            invocation_id="owned-target-1",
        )

    assert not marker.exists()
    assert _events(handle) == before
    assert destination.is_symlink() or destination.read_bytes() == b"STALE"


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


@pytest.mark.skipif(os.name == "nt", reason="requires fork and POSIX file locking")
def test_multiprocess_invocations_update_index_without_lost_rows(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    context = get_context("fork")
    processes = [
        context.Process(target=_multiprocess_capture, args=(handle, number)) for number in range(4)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)
        assert not process.is_alive()
        assert process.exitcode == 0

    index = json.loads((handle.capsule / "events/invocations.json").read_text(encoding="utf-8"))
    assert set(index) == {f"mp-l4-60000{i}-attempt-1" for i in range(4)}


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX process groups")
def test_descendant_retaining_pipes_is_bounded_and_recorded(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    script = (
        "import subprocess,sys;"
        "subprocess.Popen([sys.executable,'-c','import time;time.sleep(5)']);"
        "print('direct-child-done')"
    )
    started = time.monotonic()

    result = run_captured(
        handle,
        stage="frame",
        argv=[sys.executable, "-c", script],
        invocation_id="retained-pipes-1",
        drain_grace=0.1,
        termination_grace=0.1,
    )

    assert time.monotonic() - started < 2
    assert result.exit_code == 0
    assert result.invocation["status"] == "FAILED"
    assert any(
        error["classification"] == "PIPE_DRAIN_TIMEOUT"
        for error in result.invocation["capture_errors"]
    )
    assert not any(
        thread.name.startswith("exec-capture-retained-pipes-1") and thread.is_alive()
        for thread in threading.enumerate()
    )


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX process groups")
def test_first_signal_kills_term_ignoring_child_after_grace(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    script = "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(5)"
    timer = threading.Timer(0.15, os.kill, args=(os.getpid(), signal.SIGTERM))
    timer.start()
    started = time.monotonic()
    try:
        result = run_captured(
            handle,
            stage="prelude",
            argv=[sys.executable, "-c", script],
            invocation_id="ignore-term-1",
            drain_grace=0.1,
            termination_grace=0.15,
        )
    finally:
        timer.cancel()

    assert time.monotonic() - started < 2
    assert result.exit_code == -signal.SIGKILL
    assert result.invocation["forwarded_signals"] == [signal.SIGTERM]


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX process groups")
def test_second_signal_escalates_to_sigkill_immediately(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    script = "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(5)"
    timers = [
        threading.Timer(delay, os.kill, args=(os.getpid(), signal.SIGTERM))
        for delay in (0.15, 0.25)
    ]
    for timer in timers:
        timer.start()
    started = time.monotonic()
    try:
        result = run_captured(
            handle,
            stage="prelude",
            argv=[sys.executable, "-c", script],
            invocation_id="double-signal-1",
            drain_grace=0.1,
            termination_grace=1.0,
        )
    finally:
        for timer in timers:
            timer.cancel()

    assert time.monotonic() - started < 1
    assert result.exit_code == -signal.SIGKILL
    assert result.invocation["forwarded_signals"] == [signal.SIGTERM, signal.SIGTERM]


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
        error = _assert_terminal_error_agrees(handle, result, "signal-1")
        assert error["classification"] == "PROCESS_SIGNAL"
        assert error["traceback"] is None
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
    error = _assert_terminal_error_agrees(handle, result, "signal-caught-1")
    assert error["classification"] == "PROCESS_SIGNAL"
    assert error["traceback"] is None


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
        errors.append(
            exec_mod._exception_error(
                OSError("simulated reader fault"),
                classification="STREAM_CAPTURE_FAILURE",
                category="CAPTURE",
                phase="stdout-reader",
                traceback_text="Traceback (simulated reader)",
            )
        )

    monkeypatch.setattr(exec_mod, "_reader", fail_stdout_reader)
    result = run_captured(
        handle,
        stage="frame",
        argv=[sys.executable, "-c", "print('captured reader fault')"],
        invocation_id="reader-failure-1",
    )

    assert result.exit_code == 0
    assert result.invocation["status"] == "FAILED"
    error = _assert_terminal_error_agrees(handle, result, "reader-failure-1")
    assert error["classification"] == "STREAM_CAPTURE_FAILURE"
    assert error["phase"] == "stdout-reader"
    assert error["traceback"] == "Traceback (simulated reader)"
    assert _events(handle)[-1]["event_type"] == "COMMAND_FAILED"


def test_gzip_failure_has_structured_traceback_and_agrees_with_event(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    secret = "gzip-super-secret-value"
    monkeypatch.setenv("TUSHARE_TOKEN", secret)
    original = exec_mod._gzip_raw
    calls = 0

    def fail_stdout(raw_path, destination):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError(f"gzip evidence failed: {secret}")
        return original(raw_path, destination)

    monkeypatch.setattr(exec_mod, "_gzip_raw", fail_stdout)
    result = run_captured(
        handle,
        stage="frame",
        argv=[sys.executable, "-c", "print('output')"],
        invocation_id="gzip-failure-1",
    )

    error = _assert_terminal_error_agrees(handle, result, "gzip-failure-1")
    assert error["classification"] == "LOG_COMPRESSION_FAILURE"
    assert error["category"] == "CAPTURE"
    assert error["phase"] == "stdout-gzip"
    assert "Traceback (most recent call last)" in error["traceback"]
    encoded = json.dumps(result.invocation, ensure_ascii=False)
    assert secret not in encoded
    assert "[REDACTED]" in encoded


@pytest.mark.parametrize("failed_stream", ["stdout", "stderr"])
def test_gzip_failure_retains_exact_raw_stream_and_points_metadata_to_it(
    tmp_path, monkeypatch, failed_stream
):
    handle = _begin(tmp_path, monkeypatch)
    original = exec_mod._gzip_raw

    def fail_selected(raw_path, destination):
        if f".{failed_stream}.log.gz" in destination.name:
            raise OSError(f"{failed_stream} compression unavailable")
        return original(raw_path, destination)

    monkeypatch.setattr(exec_mod, "_gzip_raw", fail_selected)
    result = run_captured(
        handle,
        stage="frame",
        argv=[
            sys.executable,
            "-c",
            "import os;os.write(1,b'OUT\\x00\\xff');os.write(2,b'ERR\\x00\\xfe')",
        ],
        invocation_id=f"raw-fallback-{failed_stream}",
    )

    failed_ref = result.invocation[f"{failed_stream}_log"]
    failed_path = handle.capsule / failed_ref
    assert failed_ref.endswith(f".{failed_stream}.log.raw")
    assert result.invocation[f"{failed_stream}_log_encoding"] == "raw"
    assert failed_path.is_file()
    expected = b"OUT\x00\xff" if failed_stream == "stdout" else b"ERR\x00\xfe"
    assert failed_path.read_bytes() == expected
    other = "stderr" if failed_stream == "stdout" else "stdout"
    assert result.invocation[f"{other}_log_encoding"] == "gzip"
    assert (handle.capsule / result.invocation[f"{other}_log"]).is_file()
    terminal = [
        row
        for row in _events(handle)
        if row["invocation_id"] == f"raw-fallback-{failed_stream}"
        and row["event_type"] == "COMMAND_FAILED"
    ]
    assert terminal[0]["payload"][f"{failed_stream}_log"] == failed_ref
    assert terminal[0]["payload"][f"{failed_stream}_log_encoding"] == "raw"


def test_raw_fallback_post_commit_error_still_points_to_existing_evidence(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    real_gzip = exec_mod._gzip_raw
    real_sync = exec_mod._fsync_directory
    stdout_fallback_syncs = 0

    def fail_gzip(raw_path, destination):
        if ".stdout.log.gz" in destination.name:
            raise OSError("compression unavailable")
        return real_gzip(raw_path, destination)

    def fail_directory_sync(path):
        nonlocal stdout_fallback_syncs
        stdout_fallback_syncs += 1
        if stdout_fallback_syncs == 2:
            raise OSError("raw-temp cleanup durability acknowledgement unavailable")
        return real_sync(path)

    monkeypatch.setattr(exec_mod, "_gzip_raw", fail_gzip)
    monkeypatch.setattr(exec_mod, "_fsync_directory", fail_directory_sync)
    result = run_captured(
        handle,
        stage="frame",
        argv=[sys.executable, "-c", "print('durable bytes')"],
        invocation_id="raw-post-commit-error-1",
    )

    evidence = handle.capsule / result.invocation["stdout_log"]
    assert evidence.is_file()
    assert result.invocation["stdout_log_encoding"] == "raw"


def test_raw_publish_race_never_claims_stale_target_as_this_invocations_bytes(
    tmp_path, monkeypatch
):
    handle = _begin(tmp_path, monkeypatch)
    real_gzip = exec_mod._gzip_raw
    real_link = exec_mod.os.link

    def fail_stdout_gzip(raw_path, destination):
        if ".stdout.log.gz" in destination.name:
            raise OSError("stdout compression unavailable")
        return real_gzip(raw_path, destination)

    def race_stdout_publish(source, destination, *args, **kwargs):
        destination = Path(destination)
        if ".stdout.log.raw" in destination.name:
            destination.write_bytes(b"STALE")
            raise FileExistsError("racer owns stable raw target")
        return real_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(exec_mod, "_gzip_raw", fail_stdout_gzip)
    monkeypatch.setattr(exec_mod.os, "link", race_stdout_publish)
    result = run_captured(
        handle,
        stage="frame",
        argv=[sys.executable, "-c", "import os;os.write(1,b'FRESH')"],
        invocation_id="raw-publish-race-1",
    )

    stable = handle.capsule / "logs/frame/raw-publish-race-1.stdout.log.raw"
    evidence = handle.capsule / result.invocation["stdout_log"]
    assert stable.read_bytes() == b"STALE"
    assert evidence != stable
    assert evidence.is_file()
    assert evidence.read_bytes() == b"FRESH"
    assert result.invocation["stdout_log_encoding"] == "raw-temp"


def test_terminal_index_failure_is_recovered_as_structured_failure(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    original = exec_mod._finish_invocation
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("index write failed")
        return original(*args, **kwargs)

    monkeypatch.setattr(exec_mod, "_finish_invocation", fail_once)
    result = run_captured(
        handle,
        stage="frame",
        argv=[sys.executable, "-c", "pass"],
        invocation_id="index-failure-1",
    )

    error = _assert_terminal_error_agrees(handle, result, "index-failure-1")
    assert error["classification"] == "INDEX_WRITE_FAILURE"
    assert error["category"] == "PERSISTENCE"
    assert error["phase"] == "invocation-index"
    assert "Traceback (most recent call last)" in error["traceback"]


def test_terminal_event_failure_is_retried_as_matching_failed_evidence(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    original = exec_mod.append_event
    failed = False

    def fail_first_terminal(path, **fields):
        nonlocal failed
        if fields["event_type"] in {"COMMAND_COMPLETED", "COMMAND_FAILED"} and not failed:
            failed = True
            raise OSError("terminal event write failed")
        return original(path, **fields)

    monkeypatch.setattr(exec_mod, "append_event", fail_first_terminal)
    result = run_captured(
        handle,
        stage="frame",
        argv=[sys.executable, "-c", "pass"],
        invocation_id="event-failure-1",
    )

    error = _assert_terminal_error_agrees(handle, result, "event-failure-1")
    assert error["classification"] == "EVENT_WRITE_FAILURE"
    assert error["category"] == "PERSISTENCE"
    assert error["phase"] == "terminal-event"
    assert "Traceback (most recent call last)" in error["traceback"]


def test_signal_handlers_restore_after_logs_index_and_terminal_event(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    previous_int = signal.getsignal(signal.SIGINT)
    previous_term = signal.getsignal(signal.SIGTERM)

    def prior_int(signum, frame):
        return None

    def prior_term(signum, frame):
        return None

    signal.signal(signal.SIGINT, prior_int)
    signal.signal(signal.SIGTERM, prior_term)
    order = []
    real_signal = signal.signal
    real_gzip = exec_mod._gzip_raw
    real_finish = exec_mod._finish_invocation
    real_append = exec_mod.append_event

    def traced_signal(signum, handler):
        if handler in {prior_int, prior_term}:
            order.append("restore")
        return real_signal(signum, handler)

    def traced_gzip(*args, **kwargs):
        order.append("gzip")
        return real_gzip(*args, **kwargs)

    def traced_finish(*args, **kwargs):
        order.append("index")
        return real_finish(*args, **kwargs)

    def traced_append(path, **fields):
        if fields["event_type"] in {"COMMAND_COMPLETED", "COMMAND_FAILED"}:
            order.append("terminal-event")
        return real_append(path, **fields)

    monkeypatch.setattr(signal, "signal", traced_signal)
    monkeypatch.setattr(exec_mod, "_gzip_raw", traced_gzip)
    monkeypatch.setattr(exec_mod, "_finish_invocation", traced_finish)
    monkeypatch.setattr(exec_mod, "append_event", traced_append)
    try:
        run_captured(
            handle,
            stage="frame",
            argv=[sys.executable, "-c", "pass"],
            invocation_id="restore-order-1",
        )
        assert order.index("restore") > order.index("terminal-event")
        assert order.index("restore") > max(
            index for index, item in enumerate(order) if item in {"gzip", "index"}
        )
        assert signal.getsignal(signal.SIGINT) is prior_int
        assert signal.getsignal(signal.SIGTERM) is prior_term
    finally:
        real_signal(signal.SIGINT, previous_int)
        real_signal(signal.SIGTERM, previous_term)


def test_signal_handlers_restore_when_terminal_finalization_raises(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    previous_int = signal.getsignal(signal.SIGINT)
    previous_term = signal.getsignal(signal.SIGTERM)

    def fail_terminal(*args, **kwargs):
        raise RuntimeError("terminal persistence unavailable")

    monkeypatch.setattr(exec_mod, "_finish_invocation", fail_terminal)
    monkeypatch.setattr(exec_mod, "_replace_invocation", fail_terminal)
    with pytest.raises(RuntimeError, match="terminal persistence unavailable"):
        run_captured(
            handle,
            stage="frame",
            argv=[sys.executable, "-c", "pass"],
            invocation_id="restore-failure-1",
        )
    assert signal.getsignal(signal.SIGINT) == previous_int
    assert signal.getsignal(signal.SIGTERM) == previous_term


def test_signal_handler_restore_failure_compensates_plain_success_evidence(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    previous_term = signal.getsignal(signal.SIGTERM)
    real_signal = signal.signal
    term_installed = False

    def fail_term_restore(signum, handler):
        nonlocal term_installed
        if signum == signal.SIGTERM:
            if not term_installed:
                term_installed = True
            elif handler == previous_term:
                raise OSError("cannot restore SIGTERM handler")
        return real_signal(signum, handler)

    monkeypatch.setattr(signal, "signal", fail_term_restore)
    try:
        with pytest.raises(exec_mod.EvidenceFinalizationError) as raised:
            run_captured(
                handle,
                stage="frame",
                argv=[sys.executable, "-c", "pass"],
                invocation_id="restore-compensation-1",
            )
    finally:
        real_signal(signal.SIGTERM, previous_term)

    assert any(error["phase"] == "restore_signal_handlers" for error in raised.value.errors)
    rows = [row for row in _events(handle) if row["invocation_id"] == "restore-compensation-1"]
    assert [row["event_type"] for row in rows] == [
        "COMMAND_STARTED",
        "COMMAND_COMPLETED",
        "COMMAND_EVIDENCE_FAILED",
    ]
    terminals = [row for row in rows if row["event_type"] in exec_mod.COMMAND_TERMINAL_EVENTS]
    assert len(terminals) == 1
    correction = rows[-1]
    assert correction["payload"]["error"]["phase"] == "restore_signal_handlers"
    assert correction["payload"]["supersedes_seq"] == terminals[0]["seq"]
    index = json.loads((handle.capsule / "events/invocations.json").read_text(encoding="utf-8"))
    assert index["restore-compensation-1"]["status"] == "EVIDENCE_FAILED"
    assert index["restore-compensation-1"]["business_status"] == "COMPLETED"
    assert index["restore-compensation-1"]["evidence_status"] == "FAILED"
    assert index["restore-compensation-1"]["exit_code"] == 0


def test_active_base_exception_is_preserved_with_signal_restore_failure(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    real_start = threading.Thread.start
    real_signal = signal.signal
    previous_term = signal.getsignal(signal.SIGTERM)
    starts = 0
    term_installed = False

    def interrupt_first_reader(thread):
        nonlocal starts
        starts += 1
        if starts == 1:
            raise KeyboardInterrupt("active reader-start interrupt")
        return real_start(thread)

    def fail_term_restore(signum, handler):
        nonlocal term_installed
        if signum == signal.SIGTERM:
            if not term_installed:
                term_installed = True
            elif handler == previous_term:
                raise OSError("cannot restore handler during interrupt")
        return real_signal(signum, handler)

    monkeypatch.setattr(threading.Thread, "start", interrupt_first_reader)
    monkeypatch.setattr(signal, "signal", fail_term_restore)
    try:
        with pytest.raises(exec_mod.EvidenceFinalizationError) as raised:
            run_captured(
                handle,
                stage="frame",
                argv=[sys.executable, "-c", "import time;time.sleep(2)"],
                invocation_id="active-exception-restore-1",
                termination_grace=0.1,
            )
    finally:
        real_signal(signal.SIGTERM, previous_term)

    assert any(isinstance(exc, KeyboardInterrupt) for exc in raised.value.exceptions)
    rows = [row for row in _events(handle) if row["invocation_id"] == "active-exception-restore-1"]
    assert len([row for row in rows if row["event_type"] in exec_mod.COMMAND_TERMINAL_EVENTS]) == 1
    assert rows[-1]["event_type"] == "COMMAND_EVIDENCE_FAILED"


def test_restore_failure_after_child_failure_adds_nonterminal_correction_only(
    tmp_path, monkeypatch
):
    handle = _begin(tmp_path, monkeypatch)
    previous_term = signal.getsignal(signal.SIGTERM)
    real_signal = signal.signal
    term_installed = False

    def fail_term_restore(signum, handler):
        nonlocal term_installed
        if signum == signal.SIGTERM:
            if not term_installed:
                term_installed = True
            elif handler == previous_term:
                raise OSError("cannot restore SIGTERM after child failure")
        return real_signal(signum, handler)

    monkeypatch.setattr(signal, "signal", fail_term_restore)
    try:
        with pytest.raises(exec_mod.EvidenceFinalizationError):
            run_captured(
                handle,
                stage="frame",
                argv=[sys.executable, "-c", "raise SystemExit(7)"],
                invocation_id="failed-restore-correction-1",
            )
    finally:
        real_signal(signal.SIGTERM, previous_term)

    rows = [row for row in _events(handle) if row["invocation_id"] == "failed-restore-correction-1"]
    terminals = [row for row in rows if row["event_type"] in exec_mod.COMMAND_TERMINAL_EVENTS]
    assert [row["event_type"] for row in rows] == [
        "COMMAND_STARTED",
        "COMMAND_FAILED",
        "COMMAND_EVIDENCE_FAILED",
    ]
    assert len(terminals) == 1
    assert rows[-1]["payload"]["supersedes_seq"] == terminals[0]["seq"]
    index = json.loads((handle.capsule / "events/invocations.json").read_text())
    record = index["failed-restore-correction-1"]
    assert record["status"] == "EVIDENCE_FAILED"
    assert record["business_status"] == "FAILED"
    assert record["exit_code"] == 7


def test_index_terminal_failure_still_appends_failed_event_before_signal_restore(
    tmp_path, monkeypatch
):
    handle = _begin(tmp_path, monkeypatch)
    order = []
    real_append = exec_mod.append_event
    real_signal = signal.signal

    def fail_finish(*args, **kwargs):
        order.append("index-finish")
        raise OSError("primary index persistence failed")

    def fail_replace(*args, **kwargs):
        order.append("index-replace")
        raise RuntimeError("fallback index persistence failed")

    def traced_append(path, **fields):
        if fields["event_type"] in {"COMMAND_COMPLETED", "COMMAND_FAILED"}:
            order.append("terminal-event")
        return real_append(path, **fields)

    installed_handlers = set()

    def traced_signal(signum, handler):
        if signum in installed_handlers:
            order.append("restore")
        else:
            installed_handlers.add(signum)
        return real_signal(signum, handler)

    monkeypatch.setattr(exec_mod, "_finish_invocation", fail_finish)
    monkeypatch.setattr(exec_mod, "_replace_invocation", fail_replace)
    monkeypatch.setattr(exec_mod, "append_event", traced_append)
    monkeypatch.setattr(signal, "signal", traced_signal)

    with pytest.raises(RuntimeError) as raised:
        run_captured(
            handle,
            stage="frame",
            argv=[sys.executable, "-c", "pass"],
            invocation_id="index-terminal-failure-1",
        )

    assert order[:3] == ["index-finish", "index-replace", "terminal-event"]
    assert order.index("terminal-event") < order.index("restore")
    assert [error["classification"] for error in raised.value.errors] == [
        "INDEX_WRITE_FAILURE",
        "INDEX_WRITE_FAILURE",
    ]
    terminal = [
        row
        for row in _events(handle)
        if row["invocation_id"] == "index-terminal-failure-1"
        and row["event_type"] == "COMMAND_FAILED"
    ]
    assert len(terminal) == 1
    assert terminal[0]["payload"]["error"]["classification"] == "INDEX_WRITE_FAILURE"


def test_index_and_event_terminal_failures_are_raised_without_masking_primary(
    tmp_path, monkeypatch
):
    handle = _begin(tmp_path, monkeypatch)
    real_append = exec_mod.append_event
    terminal_attempts = 0

    def fail_index(*args, **kwargs):
        raise OSError("index unavailable")

    def fail_terminal(path, **fields):
        nonlocal terminal_attempts
        if fields["event_type"] in {"COMMAND_COMPLETED", "COMMAND_FAILED"}:
            terminal_attempts += 1
            raise OSError(f"terminal event unavailable attempt {terminal_attempts}")
        return real_append(path, **fields)

    monkeypatch.setattr(exec_mod, "_finish_invocation", fail_index)
    monkeypatch.setattr(exec_mod, "_replace_invocation", fail_index)
    monkeypatch.setattr(exec_mod, "append_event", fail_terminal)

    with pytest.raises(RuntimeError) as raised:
        run_captured(
            handle,
            stage="frame",
            argv=[sys.executable, "-c", "pass"],
            invocation_id="index-event-terminal-failure-1",
        )

    classifications = [error["classification"] for error in raised.value.errors]
    assert classifications[:2] == ["INDEX_WRITE_FAILURE", "INDEX_WRITE_FAILURE"]
    assert "EVENT_WRITE_FAILURE" in classifications
    assert raised.value.errors[0]["message"] == "index unavailable"
    assert "index unavailable" in str(raised.value)
    assert terminal_attempts >= 1


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
