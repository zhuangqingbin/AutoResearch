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
from autoresearch.trace import capsule as capsule_mod, identity as identity_mod
from autoresearch.trace.atomic import canonical_json, sha256_bytes
from autoresearch.trace.capsule import (
    begin_run,
    checkpoint,
    load_run,
    main,
    record_agent_boundary,
    record_controlled_agent_boundary,
)
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
    monkeypatch.setattr(
        capsule_mod,
        "snapshot_identity",
        lambda *args, **kwargs: {
            "ok": True,
            "components": {},
            "missing": [],
            "errors": [],
        },
    )


def _begin(tmp_path: Path, monkeypatch):
    _redirect_roots(monkeypatch, tmp_path)
    return begin_run("scan-market", DATE, "codex", {}, now=NOW)


def _events(handle) -> list[dict]:
    path = handle.capsule / "events/events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _begin_claude(tmp_path: Path, monkeypatch, **kwargs):
    """Begin a Claude-engine run against redirected roots."""
    _redirect_roots(monkeypatch, tmp_path)
    monkeypatch.setattr(ws, "ENGINE", "claude")
    monkeypatch.setattr(ws, "context_root", lambda: tmp_path / "context_claude")
    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path / "reports_claude")
    return begin_run("scan-market", DATE, "claude", {}, now=NOW, **kwargs)


def test_begin_run_self_binds_the_claude_harness_session(tmp_path, monkeypatch):
    """A Claude run must record which session it ran in, without an operator step.

    Regression: the 2026-09-01/09-02 production runs both wrote ``session_ref: null``
    because nothing ever called ``runctl bind``; ``usage_harvest`` then reported
    ``0 subagent · UNMEASURED`` for runs that really spent millions of tokens.
    """
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "5d26c487-dfe2-4351-ace2-ec52effc6d99")
    handle = _begin_claude(tmp_path, monkeypatch)

    assert handle.contract.session_ref == "5d26c487-dfe2-4351-ace2-ec52effc6d99"


def test_begin_run_keeps_an_explicit_session_ref_over_the_environment(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "5d26c487-dfe2-4351-ace2-ec52effc6d99")
    handle = _begin_claude(tmp_path, monkeypatch, session_ref="explicit-0000-1111-2222")

    assert handle.contract.session_ref == "explicit-0000-1111-2222"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "not a session id",
        "../../etc/passwd",
        " 5d26c487-dfe2-4351-ace2-ec52effc6d99 ",
    ],
)
def test_begin_run_drops_a_malformed_harness_session_ref(tmp_path, monkeypatch, value):
    """A wrong binding is worse than none: it would meter somebody else's transcript."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", value)
    handle = _begin_claude(tmp_path, monkeypatch)

    assert handle.contract.session_ref is None


def test_begin_run_does_not_give_codex_a_claude_session_ref(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "5d26c487-dfe2-4351-ace2-ec52effc6d99")
    handle = _begin(tmp_path, monkeypatch)

    assert handle.contract.session_ref is None


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


def test_begin_snapshots_identity_after_run_started_and_records_success(tmp_path, monkeypatch):
    _redirect_roots(monkeypatch, tmp_path)
    observed = {}

    def snapshot(repo_root, out, **identity):
        event_path = out.parent / "events/events.jsonl"
        observed["events_at_snapshot"] = [
            json.loads(line)["event_type"]
            for line in event_path.read_text(encoding="utf-8").splitlines()
        ]
        observed["identity"] = identity
        (out / "environment.json").write_text('{"engine":"codex"}\n', encoding="utf-8")
        return {
            "ok": True,
            "components": {"environment": {"status": "SUCCESS"}},
            "missing": [],
            "errors": [],
        }

    monkeypatch.setattr(capsule_mod, "snapshot_identity", snapshot)

    handle = begin_run("scan-market", DATE, "codex", {}, now=NOW)

    assert observed["events_at_snapshot"] == ["RUN_STARTED"]
    assert observed["identity"]["engine"] == "codex"
    assert [event["event_type"] for event in _events(handle)] == [
        "RUN_STARTED",
        "IDENTITY_SNAPSHOTTED",
    ]
    state = json.loads((handle.workspace / "state.json").read_text(encoding="utf-8"))
    assert (state["business_status"], state["evidence_status"]) == (
        "ACTIVE",
        "PENDING",
    )


def test_begin_consumes_authoritative_cleanup_warning_from_snapshot_loader(
    tmp_path, monkeypatch
):
    _redirect_roots(monkeypatch, tmp_path)

    def snapshot(repo_root, out, **identity):
        base = {
            "schema_version": 1,
            "ok": True,
            "redaction_rule_version": 2,
            "components": {},
            "missing": [],
            "errors": [],
        }
        warning = {
            "schema_version": 1,
            "status": "PARTIAL",
            "failures": [{"phase": "cleanup_fsync", "error_type": "OSError"}],
            "stale_backup_count": 1,
            "stale_generation_count": 0,
        }
        (out / "snapshot_result.json").write_text(
            canonical_json(base) + "\n", encoding="utf-8"
        )
        (out / "snapshot_cleanup_warning.json").write_text(
            canonical_json(warning) + "\n", encoding="utf-8"
        )
        identity_mod._write_snapshot_inventory(out, environ={})
        return base

    monkeypatch.setattr(capsule_mod, "snapshot_identity", snapshot)

    handle = begin_run("scan-market", DATE, "codex", {}, now=NOW)

    events = _events(handle)
    assert [event["event_type"] for event in events] == [
        "RUN_STARTED",
        "IDENTITY_SNAPSHOTTED",
        "EVIDENCE_MISSING",
    ]
    assert events[-1]["payload"]["ok"] is False
    assert events[-1]["payload"]["components"]["snapshot_cleanup"] == "PARTIAL"


def test_partial_identity_failure_keeps_run_loadable_and_emits_redacted_gap(tmp_path, monkeypatch):
    _redirect_roots(monkeypatch, tmp_path)
    secret = "sk-live-abcdefghijklmnopqrstuvwxyz123456"
    monkeypatch.setenv("OPENAI_API_KEY", secret)

    def partial(*args, **kwargs):
        return {
            "ok": False,
            "components": {
                "git_patch": {
                    "status": "MISSING",
                    "errors": [f"unsafe payload {secret}"],
                },
                "environment": {"status": "SUCCESS", "errors": []},
            },
            "missing": ["git_patch"],
            "errors": [f"unsafe payload {secret}"],
        }

    monkeypatch.setattr(capsule_mod, "snapshot_identity", partial)

    handle = begin_run("scan-market", DATE, "codex", {}, now=NOW)

    assert load_run(handle.run_id) == handle
    events = _events(handle)
    assert events[0]["event_type"] == "RUN_STARTED"
    assert [event["event_type"] for event in events[1:]] == [
        "IDENTITY_SNAPSHOTTED",
        "EVIDENCE_MISSING",
    ]
    state = json.loads((handle.workspace / "state.json").read_text(encoding="utf-8"))
    assert (state["business_status"], state["evidence_status"]) == (
        "ACTIVE",
        "PENDING",
    )
    assert secret not in canonical_json(events)
    assert events[-1]["payload"]["missing"] == ["git_patch"]


def test_identity_event_payload_falls_back_wholesale_for_opaque_material(
    tmp_path, monkeypatch
):
    _redirect_roots(monkeypatch, tmp_path)
    candidate = "M7pQ2xV9nK4rT8wL6cD3sF1hJ5uB0yE7aG9mN2qR"
    monkeypatch.setattr(
        capsule_mod,
        "snapshot_identity",
        lambda *args, **kwargs: {
            "ok": False,
            "components": {},
            "missing": [candidate],
            "errors": [{"component": "identity", "message": candidate}],
        },
    )

    handle = begin_run("scan-market", DATE, "codex", {}, now=NOW)

    serialized = canonical_json(_events(handle))
    assert candidate not in serialized
    assert _events(handle)[-1]["payload"]["missing"] == ["identity_snapshot"]


def test_unexpected_identity_exception_does_not_turn_business_run_failed(tmp_path, monkeypatch):
    _redirect_roots(monkeypatch, tmp_path)
    secret = "sk-live-abcdefghijklmnopqrstuvwxyz123456"
    monkeypatch.setenv("OPENAI_API_KEY", secret)

    def broken(*args, **kwargs):
        raise OSError(f"identity unavailable {secret}")

    monkeypatch.setattr(capsule_mod, "snapshot_identity", broken)

    handle = begin_run("scan-market", DATE, "codex", {}, now=NOW)

    assert load_run(handle.run_id) == handle
    events = _events(handle)
    assert [event["event_type"] for event in events] == [
        "RUN_STARTED",
        "EVIDENCE_MISSING",
    ]
    assert secret not in canonical_json(events)
    assert events[-1]["payload"]["missing"] == ["identity_snapshot"]
    state = json.loads((handle.workspace / "state.json").read_text(encoding="utf-8"))
    assert state["business_status"] == "ACTIVE"
    assert state["evidence_status"] == "PENDING"


def test_identity_success_event_append_failure_persists_gap_marker_and_event(
    tmp_path, monkeypatch
):
    _redirect_roots(monkeypatch, tmp_path)
    original = capsule_mod.append_event

    def fail_snapshot_event(*args, **kwargs):
        if kwargs.get("event_type") == "IDENTITY_SNAPSHOTTED":
            raise OSError("event append fault")
        return original(*args, **kwargs)

    monkeypatch.setattr(capsule_mod, "append_event", fail_snapshot_event)

    handle = begin_run("scan-market", DATE, "codex", {}, now=NOW)

    assert load_run(handle.run_id) == handle
    assert [event["event_type"] for event in _events(handle)] == [
        "RUN_STARTED",
        "EVIDENCE_MISSING",
    ]
    assert _events(handle)[-1]["payload"]["missing"] == [
        "identity_snapshot_event"
    ]
    marker = json.loads(
        (handle.capsule / "identity/identity_event_failure.json").read_text(
            encoding="utf-8"
        )
    )
    assert marker["failures"] == [
        {
            "attempted_event": "IDENTITY_SNAPSHOTTED",
            "component": "identity_events",
            "error_type": "OSError",
        }
    ]


def test_identity_missing_event_append_failure_persists_gap_marker(tmp_path, monkeypatch):
    _redirect_roots(monkeypatch, tmp_path)
    monkeypatch.setattr(
        capsule_mod,
        "snapshot_identity",
        lambda *args, **kwargs: {
            "ok": False,
            "components": {"git_patch": {"status": "MISSING"}},
            "missing": ["git_patch"],
            "errors": [],
        },
    )
    original = capsule_mod.append_event

    def fail_gap_event(*args, **kwargs):
        if kwargs.get("event_type") == "EVIDENCE_MISSING":
            raise OSError("gap append fault")
        return original(*args, **kwargs)

    monkeypatch.setattr(capsule_mod, "append_event", fail_gap_event)

    handle = begin_run("scan-market", DATE, "codex", {}, now=NOW)

    assert load_run(handle.run_id) == handle
    assert [event["event_type"] for event in _events(handle)] == [
        "RUN_STARTED",
        "IDENTITY_SNAPSHOTTED",
    ]
    marker = json.loads(
        (handle.capsule / "identity/identity_event_failure.json").read_text(
            encoding="utf-8"
        )
    )
    assert marker["failures"][0]["attempted_event"] == "EVIDENCE_MISSING"
    assert marker["failures"][0]["error_type"] == "OSError"


def test_identity_success_event_and_marker_double_fault_never_aborts_begin(
    tmp_path, monkeypatch, capsys
):
    _redirect_roots(monkeypatch, tmp_path)
    original_append = capsule_mod.append_event
    original_atomic = capsule_mod.atomic_write_json

    def fail_snapshot_event(*args, **kwargs):
        if kwargs.get("event_type") == "IDENTITY_SNAPSHOTTED":
            raise OSError("event fault")
        return original_append(*args, **kwargs)

    def fail_marker(path, value):
        if Path(path).name == "identity_event_failure.json":
            raise OSError("marker fault")
        return original_atomic(path, value)

    monkeypatch.setattr(capsule_mod, "append_event", fail_snapshot_event)
    monkeypatch.setattr(capsule_mod, "atomic_write_json", fail_marker)

    handle = begin_run("scan-market", DATE, "codex", {}, now=NOW)

    assert load_run(handle.run_id) == handle
    assert [event["event_type"] for event in _events(handle)] == [
        "RUN_STARTED",
        "EVIDENCE_MISSING",
    ]
    warning = capsys.readouterr().err
    assert warning == "identity evidence persistence degraded\n"
    state = json.loads((handle.workspace / "state.json").read_text(encoding="utf-8"))
    assert state["business_status"] == "ACTIVE"
    assert state["evidence_status"] == "PENDING"


def test_identity_gap_event_and_marker_double_fault_never_aborts_begin(
    tmp_path, monkeypatch, capsys
):
    _redirect_roots(monkeypatch, tmp_path)
    monkeypatch.setattr(
        capsule_mod,
        "snapshot_identity",
        lambda *args, **kwargs: {
            "ok": False,
            "components": {"git_patch": {"status": "MISSING"}},
            "missing": ["git_patch"],
            "errors": [],
        },
    )
    original_append = capsule_mod.append_event
    original_atomic = capsule_mod.atomic_write_json

    def fail_gap_event(*args, **kwargs):
        if kwargs.get("event_type") == "EVIDENCE_MISSING":
            raise OSError("gap fault")
        return original_append(*args, **kwargs)

    def fail_marker(path, value):
        if Path(path).name == "identity_event_failure.json":
            raise OSError("marker fault")
        return original_atomic(path, value)

    monkeypatch.setattr(capsule_mod, "append_event", fail_gap_event)
    monkeypatch.setattr(capsule_mod, "atomic_write_json", fail_marker)

    handle = begin_run("scan-market", DATE, "codex", {}, now=NOW)

    assert load_run(handle.run_id) == handle
    assert [event["event_type"] for event in _events(handle)] == [
        "RUN_STARTED",
        "IDENTITY_SNAPSHOTTED",
    ]
    assert capsys.readouterr().err == "identity evidence persistence degraded\n"
    state = json.loads((handle.workspace / "state.json").read_text(encoding="utf-8"))
    assert state["business_status"] == "ACTIVE"
    assert state["evidence_status"] == "PENDING"


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
    events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
    events[0]["payload"][payload_key] = replacement
    for index, event in enumerate(events):
        if index:
            event["prev_hash"] = events[index - 1]["event_hash"]
        unsigned = {key: value for key, value in event.items() if key != "event_hash"}
        event["event_hash"] = sha256_bytes(canonical_json(unsigned).encode("utf-8"))
    event_path.write_text(
        "".join(canonical_json(event) + "\n" for event in events), encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="RUN_STARTED"):
        load_run(handle.run_id)


def test_begin_invalid_config_never_publishes_workspace(tmp_path, monkeypatch):
    _redirect_roots(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="未知顶层键"):
        begin_run("scan-market", DATE, "codex", {"typo": True}, now=NOW)
    assert not ws.scan_run_root(RUN_ID).exists()


def test_begin_rejects_nested_user_config_secret_before_workspace(
    tmp_path, monkeypatch
):
    _redirect_roots(monkeypatch, tmp_path)
    secret = "sk-live-abcdefghijklmnopqrstuvwxyz123456"
    config = {"l2": {"floors": {"api_key": secret}}}

    with pytest.raises(ValueError, match="secret material") as raised:
        begin_run("scan-market", DATE, "codex", config, now=NOW)

    assert secret not in str(raised.value)
    assert not ws.scan_run_root(RUN_ID).exists()


def test_begin_rejects_secret_from_effective_pinned_contract_before_workspace(
    tmp_path, monkeypatch
):
    _redirect_roots(monkeypatch, tmp_path)
    secret = "sk-live-abcdefghijklmnopqrstuvwxyz123456"
    pinned = tmp_path / "pinned.jsonc"
    pinned.write_text(
        json.dumps(
            [
                {
                    "code": "600000",
                    "note": f"Bearer {secret}",
                    "added": DATE,
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "autoresearch.scan.user_config.DEFAULT_PINNED_PATH", pinned
    )

    with pytest.raises(ValueError, match="secret material") as raised:
        begin_run("scan-market", DATE, "codex", {}, now=NOW)

    assert secret not in str(raised.value)
    assert not ws.scan_run_root(RUN_ID).exists()


def test_begin_rejects_known_env_secret_embedded_under_safe_contract_key(
    tmp_path, monkeypatch
):
    _redirect_roots(monkeypatch, tmp_path)
    secret = "plain-secret-value"
    monkeypatch.setenv("TUSHARE_TOKEN", secret)
    config = {"l2": {"floors": {"comment": f"prefix::{secret}::suffix"}}}

    with pytest.raises(ValueError, match="secret material") as raised:
        begin_run("scan-market", DATE, "codex", config, now=NOW)

    assert secret not in str(raised.value)
    assert not ws.scan_run_root(RUN_ID).exists()


def test_begin_rejects_slack_token_under_safe_contract_key_before_workspace(
    tmp_path, monkeypatch
):
    _redirect_roots(monkeypatch, tmp_path)
    token = "xoxb-123456789012-123456789012-abcdefghijklmnopqrstuvwx"
    config = {"l2": {"floors": {"comment": f"diagnostic::{token}"}}}

    with pytest.raises(ValueError, match="secret material") as raised:
        begin_run("scan-market", DATE, "codex", config, now=NOW)

    assert token not in str(raised.value)
    assert not ws.scan_run_root(RUN_ID).exists()


def test_begin_rejects_uri_credentials_before_workspace_allocation(tmp_path, monkeypatch):
    _redirect_roots(monkeypatch, tmp_path)
    connection = "postgresql://runner:correct-horse-battery@db.internal:5432/app"
    config = {"l2": {"floors": {"comment": connection}}}

    with pytest.raises(ValueError, match="secret material") as raised:
        begin_run("scan-market", DATE, "codex", config, now=NOW)

    assert connection not in str(raised.value)
    assert not ws.scan_run_root(RUN_ID).exists()


@pytest.mark.parametrize("scheme", ["mssql+pyodbc", "amqps"])
def test_begin_rejects_generic_uri_credentials_before_workspace(
    tmp_path, monkeypatch, scheme
):
    _redirect_roots(monkeypatch, tmp_path)
    connection = f"{scheme}://runner:correct-horse@service.internal/app"
    config = {"l2": {"floors": {"comment": connection}}}

    with pytest.raises(ValueError, match="secret material") as raised:
        begin_run("scan-market", DATE, "codex", config, now=NOW)

    assert connection not in str(raised.value)
    assert not ws.scan_run_root(RUN_ID).exists()


def test_begin_rejects_empty_username_uri_credentials_before_workspace(
    tmp_path, monkeypatch
):
    _redirect_roots(monkeypatch, tmp_path)
    connection = "custom+driver://:correct-horse@service.internal/app"
    config = {"l2": {"floors": {"comment": connection}}}

    with pytest.raises(ValueError, match="secret material") as raised:
        begin_run("scan-market", DATE, "codex", config, now=NOW)

    assert connection not in str(raised.value)
    assert not ws.scan_run_root(RUN_ID).exists()


def test_begin_contract_gate_allows_safe_hashes_run_ids_and_session_ids(
    tmp_path, monkeypatch
):
    _redirect_roots(monkeypatch, tmp_path)

    handle = begin_run(
        "scan-market",
        DATE,
        "codex",
        {"l2": {"floors": {"config_hash": "a" * 64, "run_id": RUN_ID}}},
        now=NOW,
        session_ref="01a03dbe-7173-76a3-ac96-919ae6936e71",
    )

    assert load_run(handle.run_id) == handle


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


def test_bootstrap_failure_metadata_and_exception_never_leak_secret(
    tmp_path, monkeypatch
):
    _redirect_roots(monkeypatch, tmp_path)
    secret = "plain-secret-value"
    monkeypatch.setenv("TUSHARE_TOKEN", secret)

    def fail_layout(*args, **kwargs):
        raise OSError(f"layout failed with {secret}")

    monkeypatch.setattr(capsule_mod, "_create_run_layout", fail_layout)

    with pytest.raises(RuntimeError, match="recoverable workspace") as raised:
        begin_run("scan-market", DATE, "codex", {}, now=NOW)

    workspace = ws.scan_run_root(RUN_ID)
    marker = (workspace / "bootstrap_failure.json").read_text(encoding="utf-8")
    assert secret not in str(raised.value)
    assert raised.value.__cause__ is None
    assert secret not in marker


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


def test_checkpoint_records_literal_missing_at_resolution_and_completes_attempt(
    tmp_path, monkeypatch
):
    handle = _begin(tmp_path, monkeypatch)

    item = checkpoint(
        handle.run_id,
        "l2",
        "SUCCEEDED",
        ["never-created.json"],
        {},
    )

    result_path = handle.capsule / "stages/l2/attempt-1/result.json"
    outputs = json.loads(
        (handle.capsule / "stages/l2/attempt-1/outputs.json").read_text(
            encoding="utf-8"
        )
    )["artifacts"]
    assert item.attempt == 1
    assert result_path.is_file()
    assert outputs == [
        {
            "bytes": None,
            "logical_id": None,
            "path": "never-created.json",
            "pattern": None,
            "root": "scan",
            "sha256": None,
            "status": "MISSING",
        }
    ]
    assert [event["event_type"] for event in _events(handle)[-2:]] == [
        "STAGE_COMPLETED",
        "CHECKPOINT_WRITTEN",
    ]


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
    assert chain["n"] == 2 + (2 * process_count)
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
    # Exercise historical CLI serialization after an explicitly simulated entry gate.
    from autoresearch.contracts import research_access
    monkeypatch.setattr(research_access, 'require_legacy_access',
                        lambda *args: {'status': 'SIMULATED_LEGACY_BODY'})
    _redirect_roots(monkeypatch, tmp_path)
    config = tmp_path / "scan_config.jsonc"
    config.write_text("{}\n", encoding="utf-8")
    assert main([
        "begin", "scan-market", DATE, "--engine", "codex", "--config-file", str(config),
        "--legacy-reason", "direct-capsule-cli-test",
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


def test_agent_boundary_records_authoritative_binding_and_structured_result(
    tmp_path, monkeypatch
):
    handle = _begin(tmp_path, monkeypatch)
    record_agent_boundary(
        handle.run_id,
        "AGENT_DISPATCHED",
        role="l4-card",
        subject="600000",
        invocation_id="l4-card-600000-1",
        attempt=1,
    )

    event = record_agent_boundary(
        handle.run_id,
        "AGENT_COMPLETED",
        role="l4-card",
        subject="600000",
        invocation_id="l4-card-600000-1",
        attempt=1,
        result={"status": "returned", "rating": "Hold"},
    )

    assert event["event_type"] == "AGENT_COMPLETED"
    assert event["invocation_id"] == "l4-card-600000-1"
    assert event["subject"] == "600000"
    assert event["attempt"] == 1
    assert event["payload"] == {
        "error": None,
        "result": {"rating": "Hold", "status": "returned"},
        "role": "l4-card",
    }
    assert verify_event_chain(handle.capsule / "events/events.jsonl")["ok"] is True


def test_agent_payload_sanitizes_credential_in_dictionary_key(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    credential = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"

    event = record_agent_boundary(
        handle.run_id,
        "AGENT_DISPATCHED",
        role="l4-card",
        subject="600000",
        invocation_id="l4-card-600000-key",
        attempt=1,
        result={f"header::{credential}": "present"},
    )

    serialized = canonical_json(event)
    assert credential not in serialized
    assert event["payload"]["result"] == {"header::[REDACTED]": "present"}


def test_agent_payload_post_scan_rejects_unredacted_opaque_secret(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    candidate = "M7pQ2xV9nK4rT8wL6cD3sF1hJ5uB0yE7aG9mN2qR"
    before = (handle.capsule / "events/events.jsonl").read_bytes()

    with pytest.raises(ValueError, match="secret material") as raised:
        record_agent_boundary(
            handle.run_id,
            "AGENT_DISPATCHED",
            role="l4-card",
            subject="600000",
            invocation_id="l4-card-600000-opaque",
            attempt=1,
            result={"comment": candidate},
        )

    assert candidate not in str(raised.value)
    assert (handle.capsule / "events/events.jsonl").read_bytes() == before


def test_controlled_agent_boundary_self_registers_control_and_target(
    tmp_path, monkeypatch
):
    handle = _begin(tmp_path, monkeypatch)

    result = record_controlled_agent_boundary(
        handle.run_id,
        "AGENT_DISPATCHED",
        role="l4-card",
        subject="600000",
        invocation_id="l4-card-600000-1",
        attempt=1,
        control_invocation_id="trace-control-l4-card-600000-1-dispatched",
        result={"status": "queued"},
    )

    events = _events(handle)[-3:]
    assert [event["event_type"] for event in events] == [
        "AGENT_DISPATCHED",
        "AGENT_DISPATCHED",
        "AGENT_COMPLETED",
    ]
    assert [event["invocation_id"] for event in events] == [
        "trace-control-l4-card-600000-1-dispatched",
        "l4-card-600000-1",
        "trace-control-l4-card-600000-1-dispatched",
    ]
    assert events[0]["payload"]["role"] == "trace-control"
    assert events[1]["payload"]["role"] == "l4-card"
    assert events[2]["payload"]["role"] == "trace-control"
    assert result["event"] == events[1]
    assert result["control_events"] == [events[0], events[2]]


def test_controlled_agent_boundary_records_self_failure_when_target_append_fails(
    tmp_path, monkeypatch
):
    handle = _begin(tmp_path, monkeypatch)
    record_agent_boundary(
        handle.run_id,
        "AGENT_DISPATCHED",
        role="l4-card",
        subject="600000",
        invocation_id="l4-card-600000-1",
        attempt=1,
    )
    original = capsule_mod.append_guarded_event

    def fail_target(path, **fields):
        if fields["invocation_id"] == "l4-card-600000-1":
            raise OSError("target event fault")
        return original(path, **fields)

    monkeypatch.setattr(capsule_mod, "append_guarded_event", fail_target)
    with pytest.raises(OSError, match="target event fault"):
        record_controlled_agent_boundary(
            handle.run_id,
            "AGENT_COMPLETED",
            role="l4-card",
            subject="600000",
            invocation_id="l4-card-600000-1",
            attempt=1,
            control_invocation_id="trace-control-l4-card-600000-1-completed",
            result={"status": "returned"},
        )

    events = _events(handle)[-2:]
    assert [event["event_type"] for event in events] == [
        "AGENT_DISPATCHED",
        "AGENT_FAILED",
    ]
    assert all(
        event["invocation_id"] == "trace-control-l4-card-600000-1-completed"
        for event in events
    )
    assert events[-1]["payload"]["error"]["error_type"] == "OSError"


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("event_type", "AGENT_STARTED", "event_type"),
        ("role", "", "role"),
        ("role", "l4 card", "role"),
        ("subject", "../600000", "subject"),
        ("invocation_id", "l4-card;id", "invocation_id"),
        ("attempt", 0, "attempt"),
        ("attempt", True, "attempt"),
    ],
)
def test_agent_boundary_rejects_unsafe_or_ambiguous_binding(
    tmp_path, monkeypatch, field, value, match
):
    handle = _begin(tmp_path, monkeypatch)
    kwargs = {
        "event_type": "AGENT_DISPATCHED",
        "role": "l4-card",
        "subject": "600000",
        "invocation_id": "l4-card-600000-1",
        "attempt": 1,
    }
    kwargs[field] = value

    with pytest.raises((TypeError, ValueError), match=match):
        record_agent_boundary(handle.run_id, **kwargs)


def test_agent_boundary_rejects_terminal_run(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    state_path = handle.workspace / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["business_status"] = "FAILED"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(RuntimeError, match="not ACTIVE"):
        record_agent_boundary(
            handle.run_id,
            "AGENT_FAILED",
            role="l4-card",
            subject="600000",
            invocation_id="l4-card-600000-1",
            attempt=1,
            error={"error_type": "RuntimeError"},
        )


def test_agent_boundary_rejects_ambient_run_mismatch(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", "20260827T010203456780Z")

    with pytest.raises(ValueError, match="AUTORESEARCH_RUN_ID.*does not match"):
        record_agent_boundary(
            handle.run_id,
            "AGENT_DISPATCHED",
            role="l4-card",
            subject="600000",
            invocation_id="l4-card-600000-1",
            attempt=1,
        )


def test_agent_failure_redacts_secret_values_before_append(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    secret = "abcdefghijklmnopqrstuvwxyz123456"
    monkeypatch.setenv("TUSHARE_TOKEN", secret)
    record_agent_boundary(
        handle.run_id,
        "AGENT_DISPATCHED",
        role="l4-card",
        subject="600000",
        invocation_id="l4-card-600000-1",
        attempt=1,
    )

    event = record_agent_boundary(
        handle.run_id,
        "AGENT_FAILED",
        role="l4-card",
        subject="600000",
        invocation_id="l4-card-600000-1",
        attempt=1,
        error={
            "authorization": f"Bearer {secret}",
            "message": f"request failed with opaque credential {secret}",
        },
    )

    encoded = json.dumps(event, ensure_ascii=False)
    assert secret not in encoded
    assert encoded.count("[REDACTED]") >= 2


@pytest.mark.parametrize(
    "event_type,result,error,match",
    [
        ("AGENT_DISPATCHED", None, {"type": "x"}, "DISPATCHED.*error"),
        ("AGENT_COMPLETED", None, {"type": "x"}, "COMPLETED.*error"),
        ("AGENT_FAILED", None, None, "FAILED.*error"),
        ("AGENT_FAILED", {"status": "bad"}, {"type": "x"}, "FAILED.*result"),
    ],
)
def test_agent_boundary_enforces_event_payload_rules(
    tmp_path, monkeypatch, event_type, result, error, match
):
    handle = _begin(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match=match):
        record_agent_boundary(
            handle.run_id,
            event_type,
            role="l4-card",
            subject="600000",
            invocation_id="l4-card-600000-1",
            attempt=1,
            result=result,
            error=error,
        )


def test_agent_lifecycle_is_ordered_idempotent_and_conflict_safe(
    tmp_path, monkeypatch
):
    handle = _begin(tmp_path, monkeypatch)
    kwargs = {
        "role": "l4-card",
        "subject": "600000",
        "invocation_id": "l4-card-600000-1",
        "attempt": 1,
    }
    before = (handle.capsule / "events/events.jsonl").read_bytes()
    with pytest.raises(ValueError, match="requires.*dispatch"):
        record_agent_boundary(
            handle.run_id,
            "AGENT_COMPLETED",
            **kwargs,
            result={"status": "returned"},
        )
    assert (handle.capsule / "events/events.jsonl").read_bytes() == before

    dispatched = record_agent_boundary(
        handle.run_id, "AGENT_DISPATCHED", **kwargs, result={"status": "queued"}
    )
    duplicate_dispatch = record_agent_boundary(
        handle.run_id, "AGENT_DISPATCHED", **kwargs, result={"status": "queued"}
    )
    assert duplicate_dispatch == dispatched
    with pytest.raises(ValueError, match="conflicting.*dispatch"):
        record_agent_boundary(
            handle.run_id,
            "AGENT_DISPATCHED",
            **kwargs,
            result={"status": "different"},
        )

    completed = record_agent_boundary(
        handle.run_id,
        "AGENT_COMPLETED",
        **kwargs,
        result={"status": "returned"},
    )
    duplicate_terminal = record_agent_boundary(
        handle.run_id,
        "AGENT_COMPLETED",
        **kwargs,
        result={"status": "returned"},
    )
    assert duplicate_terminal == completed
    with pytest.raises(ValueError, match="conflicting.*terminal"):
        record_agent_boundary(
            handle.run_id,
            "AGENT_FAILED",
            **kwargs,
            error={"error_type": "late"},
        )
    assert [
        event["event_type"] for event in _events(handle) if event["invocation_id"] == kwargs["invocation_id"]
    ] == ["AGENT_DISPATCHED", "AGENT_COMPLETED"]


@pytest.mark.parametrize(
    "field,value",
    [("role", "other-role"), ("subject", "600001"), ("attempt", 2)],
)
def test_agent_terminal_rejects_binding_drift(tmp_path, monkeypatch, field, value):
    handle = _begin(tmp_path, monkeypatch)
    kwargs = {
        "role": "l4-card",
        "subject": "600000",
        "invocation_id": "l4-card-600000-1",
        "attempt": 1,
    }
    record_agent_boundary(handle.run_id, "AGENT_DISPATCHED", **kwargs)
    kwargs[field] = value

    with pytest.raises(ValueError, match="binding"):
        record_agent_boundary(
            handle.run_id,
            "AGENT_COMPLETED",
            **kwargs,
            result={"status": "returned"},
        )


def test_agent_terminal_rejects_stage_drift(tmp_path, monkeypatch):
    handle = _begin(tmp_path, monkeypatch)
    kwargs = {
        "role": "l4-card",
        "subject": "600000",
        "invocation_id": "l4-card-600000-1",
        "attempt": 1,
    }
    monkeypatch.setenv("AUTORESEARCH_STAGE", "l4")
    record_agent_boundary(handle.run_id, "AGENT_DISPATCHED", **kwargs)
    monkeypatch.setenv("AUTORESEARCH_STAGE", "l5")

    with pytest.raises(ValueError, match="binding"):
        record_agent_boundary(
            handle.run_id,
            "AGENT_COMPLETED",
            **kwargs,
            result={"status": "returned"},
        )


def test_agent_lifecycle_concurrency_has_one_dispatch_and_one_terminal(
    tmp_path, monkeypatch
):
    handle = _begin(tmp_path, monkeypatch)
    kwargs = {
        "role": "l4-card",
        "subject": "600000",
        "invocation_id": "l4-card-600000-1",
        "attempt": 1,
    }
    with ThreadPoolExecutor(max_workers=8) as pool:
        dispatches = list(
            pool.map(
                lambda _: record_agent_boundary(
                    handle.run_id, "AGENT_DISPATCHED", **kwargs
                ),
                range(8),
            )
        )
    assert len({event["event_hash"] for event in dispatches}) == 1

    def terminal(event_type):
        if event_type == "AGENT_COMPLETED":
            return record_agent_boundary(
                handle.run_id,
                event_type,
                **kwargs,
                result={"status": "returned"},
            )
        return record_agent_boundary(
            handle.run_id,
            event_type,
            **kwargs,
            error={"error_type": "RuntimeError"},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(terminal, kind) for kind in ("AGENT_COMPLETED", "AGENT_FAILED")]
    outcomes = []
    for future in futures:
        try:
            outcomes.append(future.result()["event_type"])
        except ValueError:
            outcomes.append("REJECTED")
    assert outcomes.count("REJECTED") == 1
    agent_events = [
        event for event in _events(handle) if event["invocation_id"] == kwargs["invocation_id"]
    ]
    assert len(agent_events) == 2
    assert agent_events[0]["event_type"] == "AGENT_DISPATCHED"


def test_agent_event_cli_emits_one_canonical_json_and_honest_failure(
    tmp_path, monkeypatch, capsys
):
    handle = _begin(tmp_path, monkeypatch)
    assert main([
        "agent-event",
        handle.run_id,
        "AGENT_DISPATCHED",
        "--role",
        "l4-card",
        "--subject",
        "600000",
        "--invocation-id",
        "l4-card-600000-1",
        "--attempt",
        "1",
        "--control-invocation-id",
        "trace-control-l4-card-600000-1-dispatched",
        "--result-json",
        '{"status":"queued"}',
    ]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    success = json.loads(captured.out)
    assert success["ok"] is True
    assert success["event"]["event_type"] == "AGENT_DISPATCHED"
    assert [event["event_type"] for event in success["control_events"]] == [
        "AGENT_DISPATCHED",
        "AGENT_COMPLETED",
    ]
    assert captured.out == canonical_json(success) + "\n"

    assert main([
        "agent-event",
        handle.run_id,
        "AGENT_COMPLETED",
        "--role",
        "l4-card",
        "--subject",
        "600000",
        "--invocation-id",
        "bad;id",
        "--attempt",
        "1",
    ]) == 2
    captured = capsys.readouterr()
    assert "invalid invocation_id" in captured.err
    assert json.loads(captured.out)["ok"] is False


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


def test_begin_freezes_current_card_rules_before_research(tmp_path, monkeypatch):
    from autoresearch.contracts.profiles import CURRENT_CARD_RULES
    from autoresearch.trace.completeness import card_rules_from_capsule

    handle = _begin(tmp_path, monkeypatch)
    assert card_rules_from_capsule(handle.capsule) == CURRENT_CARD_RULES
    profile = json.loads((handle.capsule / "verification/profile.json").read_text())
    assert profile["card_source"] == "legacy_md"
