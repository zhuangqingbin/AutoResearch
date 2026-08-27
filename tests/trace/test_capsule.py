"""Forensic capsule lifecycle: identity, containment, and append-only attempts."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

from autoresearch.common import workspace as ws
from autoresearch.scan.run_contract import load_run_contract
from autoresearch.trace.capsule import begin_run, checkpoint, load_run, main
from autoresearch.trace.events import verify_event_chain

DATE = "2026-08-27"
NOW = datetime(2026, 8, 27, 1, 2, 3, 456789, tzinfo=timezone.utc)
RUN_ID = "20260827T010203456789Z"


def _redirect_roots(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ws, "ENGINE", "codex")
    monkeypatch.setattr(ws, "context_root", lambda: tmp_path / "context_codex")
    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path / "reports_codex")
    monkeypatch.delenv("AUTORESEARCH_RUN_ID", raising=False)
    monkeypatch.setattr(
        "autoresearch.scan.user_config.DEFAULT_PINNED_PATH",
        tmp_path / "missing-pinned.jsonc",
    )


def _begin(tmp_path: Path, monkeypatch):
    _redirect_roots(monkeypatch, tmp_path)
    return begin_run("scan-market", DATE, "codex", {}, now=NOW)


def _events(handle) -> list[dict]:
    path = handle.capsule / "events/events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_begin_run_creates_active_spool_before_staging(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)

    assert handle.run_id == RUN_ID
    assert handle.workspace == tmp_path / "context_codex/scan_runs" / RUN_ID
    assert handle.staging == handle.workspace / "staging" / DATE
    assert handle.capsule == handle.workspace / "capsule"
    assert handle.staging.is_dir()
    state = json.loads((handle.workspace / "state.json").read_text(encoding="utf-8"))
    assert state["business_status"] == "ACTIVE"
    assert state["evidence_status"] == "PENDING"
    assert verify_event_chain(handle.capsule / "events/events.jsonl")["ok"] is True


def test_begin_writes_three_identical_verified_v3_contracts(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    paths = (
        handle.workspace / "run_contract.json",
        handle.staging / "run_contract.json",
        handle.capsule / "identity/run_contract.json",
    )
    assert len({path.read_bytes() for path in paths}) == 1
    assert all(load_run_contract(path) == handle.contract for path in paths)
    assert handle.contract.schema_version == 3
    assert handle.contract.workspace_path == str(handle.workspace)


def test_begin_collision_never_attaches_to_existing_run(tmp_path, monkeypatch):
    _begin(tmp_path, monkeypatch)
    with pytest.raises(FileExistsError):
        begin_run("scan-market", DATE, "codex", {}, now=NOW)


def test_load_run_rejects_unknown_traversal_and_identity_mismatch(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    assert load_run(handle.run_id) == handle
    for bad in ("../x", "unknown", "20260827T010203456780Z"):
        with pytest.raises((FileNotFoundError, ValueError, RuntimeError)):
            load_run(bad)

    state_path = handle.workspace / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["run_id"] = "20260827T010203456780Z"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(RuntimeError, match="state.*run_id"):
        load_run(handle.run_id)


def test_load_run_rejects_missing_run_started_event(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    (handle.capsule / "events/events.jsonl").unlink()
    with pytest.raises(RuntimeError, match="RUN_STARTED|event"):
        load_run(handle.run_id)


def test_checkpoint_never_overwrites_an_attempt(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    artifact = handle.staging / "_l3_judged.json"
    artifact.write_bytes(b"first")
    first = checkpoint(handle.run_id, "l3", "FAILED", [], {}, error="schema")
    second = checkpoint(
        handle.run_id, "l3", "SUCCEEDED", ["_l3_judged.json"], {}
    )

    assert first.attempt == 1 and second.attempt == 2
    first_result = handle.capsule / "stages/l3/attempt-1/result.json"
    second_result = handle.capsule / "stages/l3/attempt-2/result.json"
    assert first_result.is_file() and second_result.is_file()
    assert json.loads(first_result.read_text(encoding="utf-8"))["error"] == "schema"
    copied = handle.capsule / "products/staging/l3/attempt-2/_l3_judged.json"
    assert copied.read_bytes() == b"first"


def test_checkpoint_same_artifact_name_preserves_each_attempt_bytes(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    artifact = handle.staging / "product.json"
    artifact.write_bytes(b"one")
    checkpoint(handle.run_id, "gate1", "FAILED", [artifact], {}, error="bad")
    artifact.write_bytes(b"two")
    checkpoint(handle.run_id, "gate1", "SUCCEEDED", [artifact], {})
    products = handle.capsule / "products/staging/gate1"
    assert (products / "attempt-1/product.json").read_bytes() == b"one"
    assert (products / "attempt-2/product.json").read_bytes() == b"two"


def test_concurrent_checkpoints_allocate_unique_attempts(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)

    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(
            pool.map(
                lambda _: checkpoint(handle.run_id, "l2", "SUCCEEDED", [], {}),
                range(24),
            )
        )
    assert sorted(row.attempt for row in rows) == list(range(1, 25))
    assert len(list((handle.capsule / "stages/l2").glob("attempt-*/result.json"))) == 24
    assert verify_event_chain(handle.capsule / "events/events.jsonl")["ok"] is True


def test_checkpoint_emits_terminal_then_written_once_per_attempt(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    checkpoint(handle.run_id, "gate2", "DEGRADED", [], {"n": 0})
    relevant = [row for row in _events(handle) if row["stage"] == "gate2"]
    assert [(row["event_type"], row["attempt"]) for row in relevant] == [
        ("STAGE_COMPLETED", 1),
        ("CHECKPOINT_WRITTEN", 1),
    ]
    assert relevant[0]["payload"]["status"] == "DEGRADED"


@pytest.mark.parametrize(
    "stage,status,artifact",
    [
        ("../l3", "SUCCEEDED", None),
        ("L 3", "SUCCEEDED", None),
        ("l3", "RUNNING", None),
        ("l3", "SUCCEEDED", "../secret"),
        ("l3", "SUCCEEDED", "/outside"),
    ],
)
def test_checkpoint_rejects_unsafe_stage_status_and_artifact(
    tmp_path, monkeypatch, stage, status, artifact
):
    handle = _begin(tmp_path, monkeypatch)
    artifacts = [] if artifact is None else [artifact]
    with pytest.raises((TypeError, ValueError), match="stage|status|artifact"):
        checkpoint(handle.run_id, stage, status, artifacts, {})


def test_capsule_cli_emits_one_canonical_json_and_inspect_is_read_only(
    tmp_path, monkeypatch, capsys
):
    _redirect_roots(monkeypatch, tmp_path)
    config = tmp_path / "scan_config.jsonc"
    config.write_text("{}\n", encoding="utf-8")
    assert main([
        "begin", "scan-market", DATE, "--engine", "codex", "--config-file", str(config)
    ]) == 0
    begun_text = capsys.readouterr().out
    assert begun_text.count("\n") == 1
    begun = json.loads(begun_text)
    assert begun["run_id"]
    event_path = ws.scan_run_root(begun["run_id"]) / "capsule/events/events.jsonl"
    before = event_path.read_bytes()

    assert main(["inspect", begun["run_id"]]) == 0
    inspected_text = capsys.readouterr().out
    assert inspected_text.count("\n") == 1
    assert json.loads(inspected_text)["run_id"] == begun["run_id"]
    assert event_path.read_bytes() == before
