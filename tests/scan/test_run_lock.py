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

import pytest

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


# ── the manual begin entry points enforce the lock in code (review I3) ────────────

@pytest.fixture
def held_lock(tmp_path, monkeypatch):
    from autoresearch.common import workspace as ws

    monkeypatch.setattr(ws, "context_root", lambda: tmp_path / "context_claude")
    held = run_lock.try_acquire(run_lock.lock_path(), note="scan_run")
    assert held is not None
    yield held
    held.release()


_CAPSULE_BEGIN = ["begin", "scan-market", "2026-09-28", "--engine", "claude",
                  "--legacy-reason", "manual session"]


def test_capsule_begin_scan_market_refuses_while_the_lock_is_held(held_lock, monkeypatch, capsys):
    from autoresearch.contracts import research_access
    from autoresearch.trace import capsule

    # This unit exercises the lock after the separate host capability preflight.
    monkeypatch.setattr(research_access, "require_legacy_access", lambda: None)

    monkeypatch.setattr(capsule, "begin_run", lambda *a, **k: pytest.fail("must not begin"))
    assert capsule.main(list(_CAPSULE_BEGIN)) == run_lock.EXIT_HELD
    err = capsys.readouterr().err
    assert f"pid {os.getpid()}" in err and "--ignore-scan-lock" in err


def test_capsule_begin_with_the_explicit_override_proceeds(held_lock, monkeypatch, capsys):
    from autoresearch.contracts import research_access
    from autoresearch.trace import capsule

    # This unit exercises the lock after the separate host capability preflight.
    monkeypatch.setattr(research_access, "require_legacy_access", lambda: None)

    def reached(*args, **kwargs):
        raise RuntimeError("reached begin_run")

    monkeypatch.setattr(capsule, "begin_run", reached)
    assert capsule.main([*_CAPSULE_BEGIN, "--ignore-scan-lock"]) == 2
    assert "reached begin_run" in capsys.readouterr().err


def _session_begin(tmp_path, monkeypatch, kind: str, *extra: str):
    from autoresearch.session_agent import __main__ as cli, origin

    monkeypatch.setenv("AUTORESEARCH_ENGINE", "claude")
    monkeypatch.delenv("AUTORESEARCH_RUN_ID", raising=False)
    begun = []
    monkeypatch.setattr(origin, "begin_via_entry",
                        lambda request, **kw: begun.append(request) or {"state": "READY"})
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"kind": kind, "analysis_date": "2026-09-28"}),
                       encoding="utf-8")
    return cli.main(["begin", "--request-file", str(request), *extra]), begun


def test_session_agent_begin_for_a_scan_refuses_while_the_lock_is_held(
        held_lock, tmp_path, monkeypatch, capsys):
    code, begun = _session_begin(tmp_path, monkeypatch, "scan-market")
    assert code == run_lock.EXIT_HELD and begun == []
    [error] = json.loads(capsys.readouterr().out)["errors"]
    assert error["code"] == "SCAN_LOCK_HELD" and f"pid {os.getpid()}" in error["message"]


def test_session_agent_begin_override_and_other_kinds_are_not_blocked(
        held_lock, tmp_path, monkeypatch, capsys):
    code, begun = _session_begin(tmp_path, monkeypatch, "scan-market", "--ignore-scan-lock")
    assert code == 0 and len(begun) == 1
    code, begun = _session_begin(tmp_path, monkeypatch, "stock-research")
    assert code == 0 and len(begun) == 1


def test_skill_step0_line_actually_stops_the_block_when_the_lock_is_held(tmp_path):
    """The SKILL line used to be `run_lock check  # 非 0=…` with no exit: the block went on
    to `capsule begin`. Run the real line under bash with a fake `uv` that reports 3."""
    skill = (REPO / ".claude" / "skills" / "scan-market" / "SKILL.md").read_text(encoding="utf-8")
    [line] = [row.strip() for row in skill.splitlines() if "run_lock check" in row]
    fake = tmp_path / "bin" / "uv"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    fake.chmod(0o755)
    proc = subprocess.run(["/bin/bash", "-c", f"{line}\necho AFTER-THE-LOCK-LINE"],
                          env={"PATH": f"{fake.parent}:/usr/bin:/bin"},
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == run_lock.EXIT_HELD
    assert "AFTER-THE-LOCK-LINE" not in proc.stdout and "拒绝开扫" in proc.stdout
