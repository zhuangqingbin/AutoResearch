"""Publication is recoverable without rerunning research or exposing partial state."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoresearch.common.atomic import canonical_json, sha256_bytes
from autoresearch.common.commit_chain import read_publication_chain
from autoresearch.common.published_state import read_committed_state
from autoresearch.contracts.publication import publication_bundle_hash
from autoresearch.trace.publication import (
    InjectedPublicationFault,
    execute_publication,
    load_publication_journal,
)

RUN_ID = "20260914T120000000000Z"
NOW = datetime(2026, 9, 14, 12, 3, tzinfo=timezone.utc)


def _bundle(report: bytes, state: bytes | None = None, *, run_id: str = RUN_ID) -> dict:
    files = [
        {
            "artifact_id": "macro.report",
            "relative_path": "report/macro.md",
            "sha256": sha256_bytes(report),
            "bytes": len(report),
            "media_type": "text/markdown",
        }
    ]
    mutations = []
    if state is not None:
        mutations.append(
            {
                "target_key": "macro.latest_state",
                "expected_before_hash": None,
                "after_artifact_id": "macro.state.candidate",
                "after_hash": sha256_bytes(state),
                "apply_policy": "ADVANCE_IF_NEWER",
            }
        )
    value = {
        "schema_version": 1,
        "engine": "codex",
        "run_id": run_id,
        "run_kind": "macro-research",
        "publication_id": "p1",
        "predecessor": None,
        "origin_hash": "a" * 64,
        "plan_hash": "b" * 64,
        "evidence_plan_hash": "c" * 64,
        "business_files": files,
        "state_mutations": mutations,
        "generated_at": NOW.isoformat().replace("+00:00", "Z"),
        "bundle_hash": "0" * 64,
    }
    value["bundle_hash"] = publication_bundle_hash(value)
    return value


def _case(tmp_path: Path, *, report: bytes = b"macro report\n", state: bytes | None = None):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    handle = SimpleNamespace(
        run_id=RUN_ID,
        engine="codex",
        workspace=workspace,
        contract=SimpleNamespace(run_kind="macro-research"),
    )
    payloads = {"macro.report": report}
    if state is not None:
        payloads["macro.state.candidate"] = state
    reads = []
    finalized = []

    def artifact_reader(artifact_id: str) -> bytes:
        reads.append(artifact_id)
        return payloads[artifact_id]

    def finalizer(canonical: Path):
        finalized.append(canonical)
        capsule = canonical / "capsule/verification"
        capsule.mkdir(parents=True, exist_ok=True)
        root_hash = sha256_bytes((canonical / "report/macro.md").read_bytes())
        (capsule / "ROOT.json").write_text(
            canonical_json({"root_hash": root_hash}), encoding="utf-8"
        )
        return {"root_hash": root_hash}

    return SimpleNamespace(
        handle=handle,
        bundle=_bundle(report, state),
        reports=tmp_path / "reports",
        states=tmp_path / "states",
        artifact_reader=artifact_reader,
        finalizer=finalizer,
        reads=reads,
        finalized=finalized,
    )


def _execute(case, **kwargs):
    return execute_publication(
        case.handle,
        case.bundle,
        artifact_reader=case.artifact_reader,
        reports_root=case.reports,
        state_root=case.states,
        finalizer=case.finalizer,
        now=NOW,
        **kwargs,
    )


@pytest.mark.parametrize("phase", ["BUNDLE_SEALED", "PROMOTED", "VIEWS_APPLIED"])
def test_resume_never_reexecutes_bundle_preparation(tmp_path: Path, phase: str) -> None:
    case = _case(tmp_path)
    with pytest.raises(InjectedPublicationFault, match=phase):
        _execute(case, fault_after=phase)
    reads = len(case.reads)

    receipt = _execute(case)

    assert receipt["state"] == "COMMITTED"
    assert len(case.reads) == reads
    assert len(case.finalized) == 1
    assert load_publication_journal(case.handle.workspace)["state"] == "COMMITTED"


def test_pending_state_is_invisible_until_commit(tmp_path: Path) -> None:
    old = json.dumps({"as_of": "2026-09-13", "value": "old"}).encode()
    new = json.dumps({"as_of": "2026-09-14", "value": "new"}).encode()
    case = _case(tmp_path, state=new)
    # Seed the predecessor as a committed publication.
    predecessor = _case(tmp_path / "old", report=old, state=old)
    predecessor.states = case.states
    predecessor.reports = case.reports
    predecessor.handle.run_id = "20260913T120000000000Z"
    predecessor.bundle = _bundle(old, old, run_id=predecessor.handle.run_id)
    _execute(predecessor)
    case.bundle["state_mutations"][0]["expected_before_hash"] = sha256_bytes(old)
    case.bundle["bundle_hash"] = publication_bundle_hash(case.bundle)

    with pytest.raises(InjectedPublicationFault):
        _execute(case, fault_after="VIEWS_APPLIED")

    assert read_committed_state(
        "macro.latest_state", state_root=case.states, reports_root=case.reports
    ) == json.loads(old)
    _execute(case)
    assert read_committed_state(
        "macro.latest_state", state_root=case.states, reports_root=case.reports
    ) == json.loads(new)


def test_older_macro_state_cannot_overwrite_a_committed_newer_version(tmp_path: Path) -> None:
    newer_state = json.dumps({"as_of": "2026-09-15", "value": "newer"}).encode()
    older_state = json.dumps({"as_of": "2026-09-14", "value": "older"}).encode()
    newer = _case(tmp_path / "newer", state=newer_state)
    _execute(newer)
    older = _case(tmp_path / "older", report=older_state, state=older_state)
    older.states = newer.states
    older.reports = newer.reports
    older.handle.run_id = "20260914T130000000000Z"
    older.bundle = _bundle(older_state, older_state, run_id=older.handle.run_id)

    receipt = _execute(older)

    assert receipt["state_effects"][0]["status"] == "SUPERSEDED_BY_NEWER"
    assert read_committed_state(
        "macro.latest_state", state_root=newer.states, reports_root=newer.reports
    ) == json.loads(newer_state)


def test_duplicate_finish_is_read_only_and_content_conflict_is_rejected(tmp_path: Path) -> None:
    case = _case(tmp_path)
    first = _execute(case)
    tree_before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    second = _execute(case)

    assert second == first
    assert len(case.finalized) == 1
    assert tree_before == {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    conflict = _bundle(b"changed\n")
    with pytest.raises(RuntimeError, match="bundle conflict"):
        execute_publication(
            case.handle,
            conflict,
            artifact_reader=lambda unused: b"changed\n",
            reports_root=case.reports,
            state_root=case.states,
            finalizer=case.finalizer,
            now=NOW,
        )


def test_failed_seal_or_promotion_does_not_create_canonical_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoresearch.trace import publication as publication_mod

    seal_case = _case(tmp_path / "seal")
    original_write = publication_mod.atomic_write_json

    def fail_manifest(path, value):
        if Path(path).name == "sealed_manifest.json":
            raise OSError("disk full")
        return original_write(path, value)

    monkeypatch.setattr(publication_mod, "atomic_write_json", fail_manifest)
    with pytest.raises(OSError, match="disk full"):
        _execute(seal_case)
    assert not list(seal_case.reports.glob("runs/**/*"))

    monkeypatch.setattr(publication_mod, "atomic_write_json", original_write)
    promote_case = _case(tmp_path / "promote")
    monkeypatch.setattr(
        publication_mod,
        "_promote_directory",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("no space")),
    )
    with pytest.raises(OSError, match="no space"):
        _execute(promote_case)
    assert not list(promote_case.reports.glob("runs/**/*"))


def test_receipt_write_failure_reuses_frozen_commit_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoresearch.trace import publication as publication_mod

    case = _case(tmp_path)
    original_write = publication_mod.atomic_write_json
    failed = False

    def fail_once(path, value):
        nonlocal failed
        target = Path(path)
        if target.parent.name == RUN_ID and not failed:
            failed = True
            raise OSError("receipt fsync failed")
        return original_write(path, value)

    monkeypatch.setattr(publication_mod, "atomic_write_json", fail_once)
    with pytest.raises(OSError, match="receipt fsync failed"):
        _execute(case)
    intent = load_publication_journal(case.handle.workspace)["commit_intent"]
    assert len(case.finalized) == 1

    receipt = execute_publication(
        case.handle,
        case.bundle,
        artifact_reader=case.artifact_reader,
        reports_root=case.reports,
        state_root=case.states,
        finalizer=case.finalizer,
        now=NOW + timedelta(hours=1),
    )

    assert len(case.finalized) == 1
    assert receipt["committed_at"] == intent["committed_at"]
    assert receipt["capsule_root_hash"] == intent["capsule_root_hash"]


def test_tampered_journal_and_invalid_receipt_fail_closed(tmp_path: Path) -> None:
    case = _case(tmp_path)
    _execute(case)
    journal_path = case.handle.workspace / "publication/journal.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    journal["bundle_hash"] = "f" * 64
    journal_path.write_text(canonical_json(journal), encoding="utf-8")
    with pytest.raises(ValueError, match="journal hash"):
        load_publication_journal(case.handle.workspace)

    # Restore the journal and corrupt the independently validated receipt.
    journal["bundle_hash"] = case.bundle["bundle_hash"]
    body = {key: value for key, value in journal.items() if key != "journal_hash"}
    journal["journal_hash"] = sha256_bytes(canonical_json(body).encode("utf-8"))
    journal_path.write_text(canonical_json(journal), encoding="utf-8")
    receipt_path = case.reports / "_publications" / RUN_ID / "p1.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["receipt_hash"] = "f" * 64
    receipt_path.write_text(canonical_json(receipt), encoding="utf-8")
    with pytest.raises(ValueError, match="receipt_hash mismatch"):
        _execute(case)


def test_corrupt_commit_chain_refuses_to_append(tmp_path: Path) -> None:
    first = _case(tmp_path / "first")
    _execute(first)
    chain = first.reports / "_publications/receipts.jsonl"
    with chain.open("ab") as stream:
        stream.write(b"{truncated")
    second = _case(tmp_path / "second")
    second.reports = first.reports
    second.handle.run_id = "20260914T130000000000Z"
    second.bundle = _bundle(b"second\n", run_id=second.handle.run_id)
    second.artifact_reader = lambda unused: b"second\n"

    with pytest.raises(RuntimeError, match="commit chain is corrupt"):
        _execute(second)


def test_concurrent_same_day_runs_append_one_valid_chain(tmp_path: Path) -> None:
    first = _case(tmp_path / "first", report=b"first\n")
    second = _case(tmp_path / "second", report=b"second\n")
    second.handle.run_id = "20260914T130000000000Z"
    second.bundle = _bundle(b"second\n", run_id=second.handle.run_id)
    first.reports = second.reports = tmp_path / "reports"
    first.states = second.states = tmp_path / "states"

    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = list(executor.map(_execute, (first, second)))

    assert {receipt["run_id"] for receipt in receipts} == {
        RUN_ID,
        second.handle.run_id,
    }
    chain = read_publication_chain(first.reports / "_publications/receipts.jsonl")
    assert len(chain) == 2
    assert chain[1]["previous_receipt_hash"] == chain[0]["receipt_hash"]


def test_compatibility_failure_keeps_committed_state_and_resumes_view_only(tmp_path: Path) -> None:
    state = json.dumps({"as_of": "2026-09-14", "value": "committed"}).encode()
    case = _case(tmp_path, state=state)
    attempts = []

    def compatibility(canonical, receipt):
        attempts.append(receipt["receipt_hash"])
        if len(attempts) == 1:
            raise OSError("pool mirror failed")

    with pytest.raises(OSError, match="pool mirror failed"):
        _execute(case, compatibility=compatibility)
    assert len(case.finalized) == 1
    assert read_committed_state(
        "macro.latest_state", state_root=case.states, reports_root=case.reports
    ) == json.loads(state)

    receipt = _execute(case, compatibility=compatibility)

    assert receipt["state"] == "COMMITTED"
    assert len(attempts) == 2
    assert len(case.finalized) == 1
    assert load_publication_journal(case.handle.workspace)["compatibility_status"] == "APPLIED"


def test_post_commit_business_file_update_is_detected(tmp_path: Path) -> None:
    case = _case(tmp_path)
    receipt = _execute(case)
    report = case.reports / receipt["canonical_path"] / "report/macro.md"
    report.write_text("mutated after commit\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="manifest mismatch"):
        _execute(case)


def test_shared_state_resolves_receipts_across_workflow_report_roots(tmp_path: Path) -> None:
    dossier_state = json.dumps({"as_of": "2026-09-13", "value": "dossier"}).encode()
    scan_state = json.dumps({"as_of": "2026-09-14", "value": "scan"}).encode()
    dossier = _case(tmp_path / "dossier", state=dossier_state)
    dossier.reports = tmp_path / "reports/dossiers"
    _execute(dossier)
    scan = _case(tmp_path / "scan", report=scan_state, state=scan_state)
    scan.handle.run_id = "20260914T130000000000Z"
    scan.bundle = _bundle(scan_state, scan_state, run_id=scan.handle.run_id)
    scan.bundle["state_mutations"][0]["expected_before_hash"] = sha256_bytes(dossier_state)
    scan.bundle["bundle_hash"] = publication_bundle_hash(scan.bundle)
    scan.reports = tmp_path / "reports/scan"
    scan.states = dossier.states

    _execute(scan)

    assert read_committed_state(
        "macro.latest_state",
        state_root=scan.states,
        reports_root=tmp_path / "reports/dossiers",
    ) == json.loads(scan_state)


def test_partial_multi_state_application_resumes_without_pointer_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoresearch.trace import publication as publication_mod

    first_state = json.dumps({"as_of": "2026-09-14", "value": "one"}).encode()
    second_state = json.dumps({"as_of": "2026-09-14", "value": "two"}).encode()
    case = _case(tmp_path, state=first_state)
    case.bundle["state_mutations"].append(
        {
            "target_key": "dossier.coverage_pool",
            "expected_before_hash": None,
            "after_artifact_id": "pool.state.candidate",
            "after_hash": sha256_bytes(second_state),
            "apply_policy": "CAS_REPLACE",
        }
    )
    case.bundle["bundle_hash"] = publication_bundle_hash(case.bundle)
    original_reader = case.artifact_reader
    case.artifact_reader = lambda artifact_id: (
        second_state if artifact_id == "pool.state.candidate" else original_reader(artifact_id)
    )
    original_stage = publication_mod.stage_state_mutation
    failed = False

    def fail_second(mutation, payload, **kwargs):
        nonlocal failed
        if mutation["target_key"] == "dossier.coverage_pool" and not failed:
            failed = True
            raise OSError("second state write failed")
        return original_stage(mutation, payload, **kwargs)

    monkeypatch.setattr(publication_mod, "stage_state_mutation", fail_second)
    with pytest.raises(OSError, match="second state write failed"):
        _execute(case)

    receipt = _execute(case)

    assert [effect["status"] for effect in receipt["state_effects"]] == ["APPLIED", "APPLIED"]


def test_receipt_survives_committed_journal_write_failure_and_repairs_on_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoresearch.trace import publication as publication_mod

    case = _case(tmp_path)
    original_write = publication_mod.atomic_write_json
    failed = False

    def fail_committed_journal(path, value):
        nonlocal failed
        if Path(path).name == "journal.json" and value.get("state") == "COMMITTED" and not failed:
            failed = True
            raise OSError("journal commit fsync failed")
        return original_write(path, value)

    monkeypatch.setattr(publication_mod, "atomic_write_json", fail_committed_journal)
    with pytest.raises(OSError, match="journal commit fsync failed"):
        _execute(case)
    assert (case.reports / "_publications" / RUN_ID / "p1.json").is_file()
    assert load_publication_journal(case.handle.workspace)["state"] == "VIEWS_APPLIED"

    receipt = _execute(case)

    assert receipt["state"] == "COMMITTED"
    assert load_publication_journal(case.handle.workspace)["state"] == "COMMITTED"
    assert len(case.finalized) == 1


def test_state_reader_rejects_standalone_receipt_missing_from_commit_chain(tmp_path: Path) -> None:
    state = json.dumps({"as_of": "2026-09-14", "value": "committed"}).encode()
    case = _case(tmp_path, state=state)
    _execute(case)
    (case.reports / "_publications/receipts.jsonl").unlink()

    assert (
        read_committed_state(
            "macro.latest_state", state_root=case.states, reports_root=case.reports
        )
        is None
    )
    with pytest.raises(RuntimeError, match="commit chain"):
        _execute(case)
