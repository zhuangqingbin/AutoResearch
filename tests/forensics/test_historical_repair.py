from __future__ import annotations

from autoresearch.common import workspace as ws
from autoresearch.trace import capsule
from autoresearch.trace.capsule import BusinessStatus, checkpoint, finalize, repair
from autoresearch.trace.verification import verify_report
from tests.forensic_fixtures import begin_fixture_run


def test_historical_evidence_revision_never_rewrites_or_rebinds_base_report(
    tmp_path, monkeypatch
):
    handle = begin_fixture_run(tmp_path, monkeypatch)
    report_dir = ws.run_reports_root("scan-market") / handle.run_id
    report_dir.mkdir(parents=True)
    report = report_dir / "summary.md"
    report.write_text("# original 10:40 report\n", encoding="utf-8")
    checkpoint(handle.run_id, "l3", "SUCCEEDED", [], {"finalists": 1})
    base = finalize(handle.run_id, BusinessStatus.SUCCEEDED, report_dir)
    report_bytes = report.read_bytes()

    restored = tmp_path / "late-evidence"
    (restored / "agents/raw").mkdir(parents=True)
    (restored / "agents/raw/recovered.jsonl.gz").write_bytes(b"late transcript")
    revision = repair(
        handle.run_id,
        reason="historical transcript recovered after publication",
        source=restored,
    )

    assert report.read_bytes() == report_bytes
    assert capsule._load_root(report_dir)["root_hash"] == base.root_hash
    assert revision.base_root_hash == base.root_hash
    assert revision.composite_root_hash != base.root_hash
    assert verify_report(report, expected_run_id=handle.run_id)["report_covered"] is True


def test_unbound_business_revision_is_not_a_historical_repair(tmp_path, monkeypatch):
    handle = begin_fixture_run(tmp_path, monkeypatch)
    report_dir = ws.run_reports_root("scan-market") / handle.run_id
    report_dir.mkdir(parents=True)
    report = report_dir / "summary.md"
    report.write_text("# original\n", encoding="utf-8")
    checkpoint(handle.run_id, "l3", "SUCCEEDED", [], {})
    finalize(handle.run_id, BusinessStatus.SUCCEEDED, report_dir)
    changed = tmp_path / "10_41/summary.md"
    changed.parent.mkdir()
    changed.write_text("# changed business report\n")

    result = verify_report(changed, expected_run_id=handle.run_id)

    assert "UNBOUND_REPORT" in result["missing"]
    assert not (capsule.repairs_root() / handle.run_id).exists()
