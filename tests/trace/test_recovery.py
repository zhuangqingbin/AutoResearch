"""Stale-run recovery must not confuse 'slow' with 'dead', or reuse a pid."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from autoresearch.trace import capsule as capsule_mod, process_probe
from autoresearch.trace.capsule import (
    BusinessStatus,
    checkpoint,
    lease_is_live,
    recover_stale_runs,
    refresh_heartbeat,
)

FIXTURE_NOW = datetime(2026, 8, 27, 1, 2, 3, 456789, tzinfo=timezone.utc)


def _lease(handle) -> dict:
    return json.loads(
        (handle.workspace / "state.json").read_text(encoding="utf-8")
    )["lease"]


def test_begin_run_takes_a_lease_so_a_run_is_never_ownerless(codex_run):
    handle, _ = codex_run

    lease = _lease(handle)

    assert lease["pid"] > 0
    assert lease["hostname"]
    assert lease["heartbeat"]


def test_heartbeat_is_rate_limited_but_forceable(codex_run):
    handle, _ = codex_run
    first = _lease(handle)["heartbeat"]

    refresh_heartbeat(handle.run_id, now=FIXTURE_NOW + timedelta(seconds=5))
    assert _lease(handle)["heartbeat"] == first

    refresh_heartbeat(handle.run_id, now=FIXTURE_NOW + timedelta(seconds=45))
    assert _lease(handle)["heartbeat"] != first


def test_live_pid_with_a_different_start_time_is_stale(codex_run, monkeypatch):
    handle, _ = codex_run
    lease = _lease(handle)
    monkeypatch.setattr(process_probe, "pid_exists", lambda pid: True)
    monkeypatch.setattr(process_probe, "started_at", lambda pid: "a different boot")

    assert lease_is_live(lease) is False


def test_a_live_process_keeps_its_run(codex_run, monkeypatch):
    handle, _ = codex_run
    monkeypatch.setattr(process_probe, "matches", lambda lease: True)

    results = recover_stale_runs(
        now=FIXTURE_NOW + timedelta(hours=1), stale_after=timedelta(minutes=5)
    )

    assert results == []


def test_a_recent_heartbeat_keeps_its_run_even_if_the_process_is_gone(
    codex_run, monkeypatch
):
    handle, _ = codex_run
    monkeypatch.setattr(process_probe, "matches", lambda lease: False)

    results = recover_stale_runs(
        now=FIXTURE_NOW + timedelta(minutes=1), stale_after=timedelta(minutes=5)
    )

    assert results == []


def test_recover_stale_active_run_as_interrupted(codex_run, monkeypatch):
    handle, _ = codex_run
    checkpoint(handle.run_id, "l3", "SUCCEEDED", [], {})
    monkeypatch.setattr(process_probe, "matches", lambda lease: False)

    results = recover_stale_runs(
        now=FIXTURE_NOW + timedelta(minutes=6), stale_after=timedelta(minutes=5)
    )

    assert [item.business_status for item in results] == ["INTERRUPTED"]
    failure = json.loads(
        (results[0].final_path / "capsule/failure.json").read_text(encoding="utf-8")
    )
    assert failure["last_reliable_checkpoint"] == "l3"
    assert results[0].final_path.parent == capsule_mod.failed_root()


def test_recovery_never_removes_the_spool_it_froze(codex_run, monkeypatch):
    handle, _ = codex_run
    monkeypatch.setattr(process_probe, "matches", lambda lease: False)

    recover_stale_runs(
        now=FIXTURE_NOW + timedelta(minutes=6), stale_after=timedelta(minutes=5)
    )

    assert handle.workspace.is_dir()
    state = json.loads((handle.workspace / "state.json").read_text(encoding="utf-8"))
    assert state["business_status"] == BusinessStatus.INTERRUPTED.value


def test_recovery_failure_is_reported_and_does_not_block_the_next_run(
    codex_run, monkeypatch, capsys
):
    handle, _ = codex_run
    monkeypatch.setattr(process_probe, "matches", lambda lease: False)
    monkeypatch.setattr(
        capsule_mod,
        "finalize",
        lambda *a, **k: (_ for _ in ()).throw(OSError("read-only reports root")),
    )

    results = capsule_mod.recover_stale_runs_quietly(
        now=FIXTURE_NOW + timedelta(minutes=6), stale_after=timedelta(minutes=5)
    )

    assert results[0].finalized is False
    assert "未能冻结" in capsys.readouterr().out
    assert handle.workspace.is_dir()
