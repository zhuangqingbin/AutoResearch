"""Scan-run mutual exclusion (batch 4 Task 3, Review Focus 4).

macOS has no ``flock(1)``; the lock is ``fcntl.flock`` on ``$CTX/.scan_run.lock`` held for
the whole unattended run. A second process must exit at once and name the holder pid.
Real subprocesses: the lock is per open file description, so in-process checks prove little.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from autoresearch.scan import run_lock

REPO = Path(__file__).resolve().parents[2]


def _holder_process(lock: Path, ready: Path) -> subprocess.Popen:
    code = (
        "import sys, time, pathlib\n"
        "from autoresearch.scan import run_lock\n"
        "held = run_lock.try_acquire(pathlib.Path(sys.argv[1]))\n"
        "assert held is not None\n"
        "pathlib.Path(sys.argv[2]).write_text('ok')\n"
        "time.sleep(30)\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", code, str(lock), str(ready)], cwd=REPO)
    deadline = time.monotonic() + 20
    while not ready.exists():
        assert time.monotonic() < deadline, "holder never acquired the lock"
        assert proc.poll() is None, "holder died"
        time.sleep(0.05)
    return proc


def test_second_process_exits_at_once_naming_the_holder_pid(tmp_path):
    lock = tmp_path / ".scan_run.lock"
    holder = _holder_process(lock, tmp_path / "ready")
    try:
        started = time.monotonic()
        check = subprocess.run(
            [sys.executable, "-m", "autoresearch.scan.run_lock", "check", "--lock", str(lock)],
            cwd=REPO, capture_output=True, text=True, timeout=30)
        assert time.monotonic() - started < 15
        assert check.returncode == run_lock.EXIT_HELD
        assert f"pid {holder.pid}" in check.stdout
    finally:
        holder.kill()
        holder.wait()


def test_lock_is_free_again_once_the_holder_dies(tmp_path):
    lock = tmp_path / ".scan_run.lock"
    holder = _holder_process(lock, tmp_path / "ready")
    holder.kill()
    holder.wait()
    assert run_lock.holder(lock) is None
    held = run_lock.try_acquire(lock)
    assert held is not None
    held.release()


def test_try_acquire_records_the_holder_and_release_frees_it(tmp_path):
    lock = tmp_path / ".scan_run.lock"
    held = run_lock.try_acquire(lock, note="scan_run 2026-09-28")
    assert held is not None
    info = json.loads(lock.read_text(encoding="utf-8"))
    assert info["pid"] == os.getpid() and info["note"] == "scan_run 2026-09-28"
    held.release()
    assert run_lock.holder(lock) is None


def test_check_on_a_free_lock_exits_zero(tmp_path, capsys):
    assert run_lock.main(["check", "--lock", str(tmp_path / ".scan_run.lock")]) == 0
    assert "free" in capsys.readouterr().out


def test_default_lock_lives_in_the_engine_context_root(monkeypatch, tmp_path):
    from autoresearch.common import workspace as ws

    monkeypatch.setattr(ws, "context_root", lambda: tmp_path / "context_claude")
    assert run_lock.lock_path() == tmp_path / "context_claude" / ".scan_run.lock"
