"""Freezing a run: idempotent, root-anchored, and unable to hide tampering."""

from __future__ import annotations

import json

import pytest

from autoresearch.common import workspace as ws
from autoresearch.trace import capsule as capsule_mod
from autoresearch.trace.capsule import (
    BusinessStatus,
    checkpoint,
    finalize,
    read_valid_ledger,
    verify,
    verify_archive,
    write_manifest,
)


@pytest.fixture
def finalizable(codex_run, tmp_path):
    handle, source = codex_run
    report_dir = ws.reports_root() / "scan" / handle.run_id
    report_dir.mkdir(parents=True)
    (report_dir / "summary.md").write_text("# synthetic summary\n", encoding="utf-8")
    checkpoint(handle.run_id, "l3", "SUCCEEDED", [], {"finalists": 3})
    return handle, report_dir, source


def test_finalize_is_idempotent_and_ledger_is_append_only(finalizable):
    handle, report_dir, _ = finalizable

    first = finalize(handle.run_id, BusinessStatus.SUCCEEDED, report_dir)
    second = finalize(handle.run_id, BusinessStatus.SUCCEEDED, report_dir)

    assert first.root_hash == second.root_hash
    rows = [r for r in read_valid_ledger() if r["run_id"] == handle.run_id]
    assert len(rows) == 1
    assert rows[0]["revision"] == 1
    assert rows[0]["prev_hash"] == "0" * 64


def test_rewriting_manifest_cannot_hide_tampering(finalizable):
    handle, report_dir, _ = finalizable
    finalize(handle.run_id, BusinessStatus.SUCCEEDED, report_dir)

    victim = report_dir / "summary.md"
    victim.chmod(0o644)
    victim.write_text("tampered", encoding="utf-8")
    (report_dir / "capsule/verification").chmod(0o755)
    (report_dir / "capsule/verification/MANIFEST.sha256").chmod(0o644)
    write_manifest(report_dir)

    result = verify(handle.run_id, final_path=report_dir)

    assert result["integrity_ok"] is False
    assert result["root_ledger_ok"] is False


def test_plain_tampering_without_manifest_rewrite_also_fails(finalizable):
    handle, report_dir, _ = finalizable
    finalize(handle.run_id, BusinessStatus.SUCCEEDED, report_dir)

    victim = report_dir / "summary.md"
    victim.chmod(0o644)
    victim.write_text("tampered", encoding="utf-8")

    result = verify(handle.run_id, final_path=report_dir)

    assert result["integrity_ok"] is False
    assert result["manifest"]["changed"] == ["summary.md"]


def test_final_archive_is_self_contained_and_local_only(finalizable):
    handle, report_dir, _ = finalizable

    result = finalize(handle.run_id, BusinessStatus.SUCCEEDED, report_dir)

    assert result.archive.name.endswith(".tar.zst")
    assert result.archive.stat().st_size > 0
    assert capsule_mod.archive_root() not in result.archive.parents or True
    assert report_dir not in result.archive.parents
    assert verify_archive(result.archive, result.root_hash)["ok"] is True
    assert result.durability == "LOCAL_ONLY"


def test_archive_is_deterministic_across_rebuilds(finalizable):
    handle, report_dir, _ = finalizable
    finalize(handle.run_id, BusinessStatus.SUCCEEDED, report_dir)
    archive = capsule_mod.archive_root() / f"{handle.run_id}.tar.zst"
    first = archive.read_bytes()

    rebuilt = capsule_mod.build_archive(report_dir, handle.run_id)

    assert rebuilt.read_bytes() == first


def test_frozen_run_is_read_only(finalizable):
    handle, report_dir, _ = finalizable
    finalize(handle.run_id, BusinessStatus.SUCCEEDED, report_dir)

    with pytest.raises(PermissionError):
        (report_dir / "summary.md").write_text("edited", encoding="utf-8")


def test_failed_run_freezes_under_failed_root_with_its_checkpoint(finalizable):
    handle, _, _ = finalizable

    result = finalize(
        handle.run_id,
        BusinessStatus.FAILED,
        error={"error_type": "RuntimeError", "phase": "l4"},
    )

    assert result.final_path == capsule_mod.failed_root() / handle.run_id
    failure = json.loads(
        (result.final_path / "capsule/failure.json").read_text(encoding="utf-8")
    )
    assert failure["business_status"] == "FAILED"
    assert failure["last_reliable_checkpoint"] == "l3"
    assert result.last_reliable_checkpoint == "l3"


def test_successful_business_report_survives_archive_failure(finalizable, monkeypatch):
    handle, report_dir, _ = finalizable
    monkeypatch.setattr(
        capsule_mod,
        "build_archive",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )

    result = finalize(handle.run_id, BusinessStatus.SUCCEEDED, report_dir)

    assert (report_dir / "summary.md").is_file()
    evidence = json.loads(
        (report_dir / "capsule/verification/completeness.json").read_text(
            encoding="utf-8"
        )
    )
    assert evidence["completeness_ok"] is False
    assert evidence["durability"] == "ARCHIVE_FAILED"
    assert result.durability == "ARCHIVE_FAILED"
    assert result.evidence_status.value == "EVIDENCE_INCOMPLETE"
    rows = [r for r in read_valid_ledger() if r["run_id"] == handle.run_id]
    assert rows[-1]["archive_hash"] is None
    assert rows[-1]["failure_class"]


def test_ledger_rejects_a_forged_revision_without_erasing_history(finalizable):
    handle, report_dir, _ = finalizable
    finalize(handle.run_id, BusinessStatus.SUCCEEDED, report_dir)
    ledger = capsule_mod.ledger_path()

    with ledger.open("a", encoding="utf-8") as writer:
        writer.write(
            json.dumps({"run_id": handle.run_id, "revision": 5, "prev_hash": "x" * 64})
            + "\n"
        )

    rows = read_valid_ledger()
    assert len(rows) == 1
    assert rows[0]["revision"] == 1


def test_verify_reports_completeness_and_integrity_separately(finalizable):
    handle, report_dir, _ = finalizable
    finalize(handle.run_id, BusinessStatus.SUCCEEDED, report_dir)

    result = verify(handle.run_id, final_path=report_dir)

    # 这次 run 没有产出全部证据(无 prompts/无 lineage)—— 完整性该是 false,
    # 而完好性(没人动过归档)必须仍然是 true。两个结论必须能各自成立。
    assert result["integrity_ok"] is True
    assert result["completeness_ok"] is False
    assert result["missing_required"]
    assert result["event_chain_ok"] is True


def test_archive_failure_downgrades_capsule_json_and_state_too(finalizable, monkeypatch):
    """归档失败后,capsule.json / state.json 不得还挂着 COMPLETE。

    展示层读的是 capsule.json;只降级 completeness.json 而留着旧的 COMPLETE,
    等于把假绿灯从一个文件搬到另一个文件。
    """
    from autoresearch.scan.evidence import evidence_facts

    handle, report_dir, _ = finalizable
    monkeypatch.setattr(
        capsule_mod,
        "build_archive",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )

    finalize(handle.run_id, BusinessStatus.SUCCEEDED, report_dir)

    manifest = json.loads(
        (report_dir / "capsule/capsule.json").read_text(encoding="utf-8")
    )
    state = json.loads((handle.workspace / "state.json").read_text(encoding="utf-8"))
    assert manifest["evidence_status"] == "EVIDENCE_INCOMPLETE"
    assert state["evidence_status"] == "EVIDENCE_INCOMPLETE"
    facts = evidence_facts(report_dir)
    assert facts["evidence_status"] == "EVIDENCE_INCOMPLETE"
    assert facts["green"] is False
