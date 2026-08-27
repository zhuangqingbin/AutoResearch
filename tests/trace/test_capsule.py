"""Forensic capsule lifecycle: identity, containment, and append-only attempts."""
from __future__ import annotations

import json
import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

from autoresearch.common import workspace as ws
from autoresearch.scan.artifacts import ArtifactSpec
from autoresearch.scan.run_contract import load_run_contract
from autoresearch.trace import capsule as capsule_mod
from autoresearch.trace.atomic import canonical_json, sha256_bytes
from autoresearch.trace.capsule import begin_run, checkpoint, load_run, main
from autoresearch.trace.events import verify_event_chain

DATE = "2026-08-27"
NOW = datetime(2026, 8, 27, 1, 2, 3, 456789, tzinfo=timezone.utc)
RUN_ID = "20260827T010203456789Z"


def _multiprocess_checkpoint_worker(
    context_root: str,
    reports_root: str,
    run_id: str,
    start,
    results,
) -> None:
    os.environ["AUTORESEARCH_ENGINE"] = "codex"
    ws.ENGINE = "codex"
    ws.context_root = lambda: Path(context_root)
    ws.reports_root = lambda: Path(reports_root)
    start.wait(timeout=10)
    try:
        item = checkpoint(run_id, "multiprocess", "SUCCEEDED", [], {})
        results.put(("ok", item.attempt))
    except BaseException as exc:  # pragma: no cover - reported to the parent assertion
        results.put(("error", f"{type(exc).__name__}: {exc}"))


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


@pytest.mark.parametrize("component", ["staging", "capsule", "events_file"])
def test_load_run_rejects_symlinked_required_components(
    tmp_path, monkeypatch, component
):
    handle = _begin(tmp_path, monkeypatch)
    outside = tmp_path / f"outside-{component}"
    if component == "staging":
        original = handle.workspace / "staging"
        original.rename(outside)
        original.symlink_to(outside, target_is_directory=True)
    elif component == "capsule":
        handle.capsule.rename(outside)
        handle.capsule.symlink_to(outside, target_is_directory=True)
    else:
        event_path = handle.capsule / "events/events.jsonl"
        outside.write_bytes(event_path.read_bytes())
        event_path.unlink()
        event_path.symlink_to(outside)
    with pytest.raises((RuntimeError, ValueError), match="symlink|escape"):
        load_run(handle.run_id)


@pytest.mark.parametrize(
    "payload_key,replacement",
    [
        ("analysis_date", "2026-08-26"),
        ("contract_hash", "f" * 64),
        ("workspace", "context_codex/scan_runs/20260827T010203456780Z"),
    ],
)
def test_load_run_rejects_rehashed_wrong_run_started_identity(
    tmp_path, monkeypatch, payload_key, replacement
):
    handle = _begin(tmp_path, monkeypatch)
    event_path = handle.capsule / "events/events.jsonl"
    event = json.loads(event_path.read_text(encoding="utf-8"))
    event["payload"][payload_key] = replacement
    unsigned = {key: value for key, value in event.items() if key != "event_hash"}
    event["event_hash"] = sha256_bytes(canonical_json(unsigned).encode("utf-8"))
    event_path.write_text(canonical_json(event) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="RUN_STARTED"):
        load_run(handle.run_id)


def test_begin_invalid_config_never_publishes_workspace(tmp_path, monkeypatch):
    _redirect_roots(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="未知顶层键"):
        begin_run("scan-market", DATE, "codex", {"typo": True}, now=NOW)
    assert not ws.scan_run_root(RUN_ID).exists()


@pytest.mark.parametrize(
    "fault",
    ["layout", "state", "contract-1", "contract-2", "contract-3", "event"],
)
def test_begin_fault_after_allocation_leaves_recoverable_failure_marker(
    tmp_path, monkeypatch, fault
):
    _redirect_roots(monkeypatch, tmp_path)
    if fault == "layout":
        original = capsule_mod._create_run_layout
        calls = 0

        def fail_once(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("layout fault")
            return original(*args, **kwargs)

        monkeypatch.setattr(capsule_mod, "_create_run_layout", fail_once)
    elif fault == "state":
        original = capsule_mod._write_state
        calls = 0

        def fail_once(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("state fault")
            return original(*args, **kwargs)

        monkeypatch.setattr(capsule_mod, "_write_state", fail_once)
    elif fault.startswith("contract"):
        fail_at = int(fault.split("-")[1])
        original = capsule_mod.write_run_contract
        calls = 0

        def fail_nth(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == fail_at:
                raise OSError(f"contract {fail_at} fault")
            return original(*args, **kwargs)

        monkeypatch.setattr(capsule_mod, "write_run_contract", fail_nth)
    else:
        original = capsule_mod.append_event
        calls = 0

        def fail_once(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("event fault")
            return original(*args, **kwargs)

        monkeypatch.setattr(capsule_mod, "append_event", fail_once)

    workspace = ws.scan_run_root(RUN_ID)
    with pytest.raises(RuntimeError, match="recoverable workspace"):
        begin_run("scan-market", DATE, "codex", {}, now=NOW)
    marker = json.loads((workspace / "bootstrap_failure.json").read_text(encoding="utf-8"))
    state = json.loads((workspace / "state.json").read_text(encoding="utf-8"))
    assert marker["run_id"] == RUN_ID
    assert marker["error_type"] == "OSError"
    assert state["business_status"] == "FAILED"
    assert state["evidence_status"] == "EVIDENCE_INCOMPLETE"
    recovered = load_run(RUN_ID)
    assert recovered.workspace == workspace
    assert _events(recovered)[-1]["event_type"] == "STAGE_FAILED"


def test_checkpoint_rejects_bootstrap_failed_run_without_allocating_attempt(
    tmp_path, monkeypatch
):
    _redirect_roots(monkeypatch, tmp_path)
    original = capsule_mod.append_event
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("event fault")
        return original(*args, **kwargs)

    monkeypatch.setattr(capsule_mod, "append_event", fail_once)
    with pytest.raises(RuntimeError, match="recoverable workspace"):
        begin_run("scan-market", DATE, "codex", {}, now=NOW)
    handle = load_run(RUN_ID)
    events_before = (handle.capsule / "events/events.jsonl").read_bytes()

    with pytest.raises(RuntimeError, match="not ACTIVE|FAILED"):
        checkpoint(handle.run_id, "l2", "FAILED", [], {}, error="bootstrap")

    assert not (handle.capsule / "stages/l2").exists()
    assert not (handle.capsule / "products/staging/l2").exists()
    assert (handle.capsule / "events/events.jsonl").read_bytes() == events_before


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
    copied = handle.capsule / "products/staging/l3/attempt-2/scan/_l3_judged.json"
    assert copied.read_bytes() == b"first"


def test_checkpoint_same_artifact_name_preserves_each_attempt_bytes(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    artifact = handle.staging / "product.json"
    artifact.write_bytes(b"one")
    checkpoint(handle.run_id, "gate1", "FAILED", [artifact], {}, error="bad")
    artifact.write_bytes(b"two")
    checkpoint(handle.run_id, "gate1", "SUCCEEDED", [artifact], {})
    products = handle.capsule / "products/staging/gate1"
    assert (products / "attempt-1/scan/product.json").read_bytes() == b"one"
    assert (products / "attempt-2/scan/product.json").read_bytes() == b"two"


def test_checkpoint_rejects_symlinked_artifact_source(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    outside = tmp_path / "outside-artifact"
    outside.write_bytes(b"secret")
    linked = handle.staging / "linked.bin"
    linked.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        checkpoint(handle.run_id, "l2", "SUCCEEDED", [linked], {})


@pytest.mark.parametrize(
    "relative",
    [Path("stages/l2"), Path("products/staging/l2")],
)
def test_checkpoint_rejects_symlinked_destination_stage_without_outside_writes(
    tmp_path, monkeypatch, relative
):
    handle = _begin(tmp_path, monkeypatch)
    source = handle.staging / "L2_gbdt_top200.csv"
    source.write_bytes(b"code\n600000\n")
    outside = tmp_path / f"outside-{'-'.join(relative.parts)}"
    outside.mkdir()
    destination = handle.capsule / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.symlink_to(outside, target_is_directory=True)
    events_before = (handle.capsule / "events/events.jsonl").read_bytes()

    with pytest.raises(ValueError, match="symlink"):
        checkpoint(handle.run_id, "l2", "SUCCEEDED", ["l2"], {})

    assert list(outside.iterdir()) == []
    assert (handle.capsule / "events/events.jsonl").read_bytes() == events_before


def test_checkpoint_rejects_logical_and_literal_destination_collision_pre_attempt(
    tmp_path, monkeypatch
):
    handle = _begin(tmp_path, monkeypatch)
    (handle.staging / "L2_gbdt_top200.csv").write_bytes(b"code\n600000\n")
    events_before = (handle.capsule / "events/events.jsonl").read_bytes()

    with pytest.raises(ValueError, match="collision"):
        checkpoint(
            handle.run_id,
            "l2",
            "SUCCEEDED",
            ["l2", "L2_gbdt_top200.csv"],
            {},
        )

    assert not (handle.capsule / "stages/l2").exists()
    assert (handle.capsule / "events/events.jsonl").read_bytes() == events_before


def test_checkpoint_rejects_overlapping_spec_and_literal_pre_attempt(
    tmp_path, monkeypatch
):
    handle = _begin(tmp_path, monkeypatch)
    details = handle.staging / "details"
    details.mkdir()
    (details / "600000.md").write_text("card", encoding="utf-8")
    monkeypatch.setitem(
        capsule_mod._ARTIFACT_SPECS,
        "overlap",
        ArtifactSpec("overlap", 1, "test", "details/*.md"),
    )
    events_before = (handle.capsule / "events/events.jsonl").read_bytes()

    with pytest.raises(ValueError, match="collision"):
        checkpoint(
            handle.run_id,
            "l4",
            "SUCCEEDED",
            ["l4_cards", "overlap"],
            {},
        )

    assert not (handle.capsule / "stages/l4").exists()
    assert not (handle.capsule / "products/staging/l4").exists()
    assert (handle.capsule / "events/events.jsonl").read_bytes() == events_before


@pytest.mark.parametrize("initial", [b"", b"before"])
@pytest.mark.parametrize("mutation", ["populate", "replace"])
def test_checkpoint_detects_source_mutation_on_same_descriptor_without_completed_event(
    tmp_path, monkeypatch, initial, mutation
):
    handle = _begin(tmp_path, monkeypatch)
    source = handle.staging / "payload.bin"
    source.write_bytes(initial)
    source_inode = source.stat().st_ino
    original_fstat = capsule_mod.os.fstat
    mutated = False

    def mutate_after_first_source_fstat(fd):
        nonlocal mutated
        info = original_fstat(fd)
        if not mutated and info.st_ino == source_inode:
            mutated = True
            if mutation == "populate":
                source.write_bytes(b"after-is-longer")
            else:
                replacement = source.with_suffix(".replacement")
                replacement.write_bytes(b"replacement")
                replacement.replace(source)
        return info

    monkeypatch.setattr(capsule_mod.os, "fstat", mutate_after_first_source_fstat)
    events_before = (handle.capsule / "events/events.jsonl").read_bytes()
    with pytest.raises(RuntimeError, match="changed during capture"):
        checkpoint(handle.run_id, "l2", "SUCCEEDED", [source], {})
    assert mutated is True
    assert (handle.capsule / "events/events.jsonl").read_bytes() == events_before
    assert not (handle.capsule / "stages/l2/attempt-1/result.json").exists()


def test_checkpoint_rejects_ambiguous_relative_literal_across_roots(
    tmp_path, monkeypatch
):
    handle = _begin(tmp_path, monkeypatch)
    report = tmp_path / "reports_codex/scan/20260827_1200"
    report.mkdir(parents=True)
    (handle.staging / "same.json").write_text("scan", encoding="utf-8")
    (report / "same.json").write_text("report", encoding="utf-8")
    with pytest.raises(ValueError, match="ambiguous"):
        checkpoint(
            handle.run_id,
            "assemble",
            "SUCCEEDED",
            ["same.json"],
            {},
            report_dir=report,
        )


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
    # Event pairs may interleave globally.  Consumers join by (stage, attempt):
    # every attempt still has one terminal fact followed by one checkpoint fact.
    grouped = {}
    for event in _events(handle):
        if event["stage"] == "l2":
            grouped.setdefault(event["attempt"], []).append(event)
    assert set(grouped) == set(range(1, 25))
    for attempt, events in grouped.items():
        assert [event["event_type"] for event in events] == [
            "STAGE_COMPLETED",
            "CHECKPOINT_WRITTEN",
        ], attempt
        assert events[0]["seq"] < events[1]["seq"]


def test_multiprocess_first_use_allocates_all_attempts_without_directory_race(
    tmp_path, monkeypatch
):
    handle = _begin(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    process_count = 16
    process_context = multiprocessing.get_context("fork")
    start = process_context.Event()
    results = process_context.Queue()
    processes = [
        process_context.Process(
            target=_multiprocess_checkpoint_worker,
            args=(
                str(ws.context_root()),
                str(ws.reports_root()),
                handle.run_id,
                start,
                results,
            ),
        )
        for _ in range(process_count)
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    outcomes = [results.get(timeout=5) for _ in processes]

    assert [kind for kind, _ in outcomes] == ["ok"] * process_count
    assert sorted(attempt for _, attempt in outcomes) == list(
        range(1, process_count + 1)
    )
    assert len(
        list(
            (handle.capsule / "stages/multiprocess").glob(
                "attempt-*/result.json"
            )
        )
    ) == process_count
    chain = verify_event_chain(handle.capsule / "events/events.jsonl")
    assert chain["ok"] is True
    assert chain["n"] == 1 + (2 * process_count)
    assert list(outside.iterdir()) == []


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


@pytest.mark.parametrize(
    "created_at,updated_at",
    [
        ("2026-08-27T01:02:03.456789", "2026-08-27T01:02:03.456789Z"),
        ("2026-08-27T09:02:03.456789+08:00", "2026-08-27T01:02:03.456789Z"),
        ("2026-08-27T01:02:03Z", "2026-08-27T01:02:03.456789Z"),
        ("2026-08-27T01:02:04.456789Z", "2026-08-27T01:02:03.456789Z"),
    ],
)
def test_load_run_rejects_noncanonical_or_regressing_state_times(
    tmp_path, monkeypatch, created_at, updated_at
):
    handle = _begin(tmp_path, monkeypatch)
    state_path = handle.workspace / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update({"created_at": created_at, "updated_at": updated_at})
    state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(RuntimeError, match="state"):
        load_run(handle.run_id)


def test_load_run_rejects_state_created_at_different_from_contract(
    tmp_path, monkeypatch
):
    handle = _begin(tmp_path, monkeypatch)
    state_path = handle.workspace / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["created_at"] = "2026-08-27T01:02:04.456789Z"
    state["updated_at"] = state["created_at"]
    state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(RuntimeError, match="created_at"):
        load_run(handle.run_id)
