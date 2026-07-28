"""Critical ArtifactIndex: status, content hashes, collections, and persistence."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from autoresearch.scan.artifacts import build_artifact_index, write_artifact_index

NOW = datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc)


def _by_name(index: dict) -> dict[str, dict]:
    return {row["name"]: row for row in index["artifacts"]}


def test_index_hashes_scan_and_report_artifacts(tmp_path):
    scan = tmp_path / "context" / "scan" / "2026-07-28"
    report = tmp_path / "reports" / "scan" / "20260728_1400"
    (scan / "details").mkdir(parents=True)
    report.mkdir(parents=True)
    (scan / "market_pack.json").write_text('{"breadth":{}}', encoding="utf-8")
    (scan / "details" / "000001.md").write_text("# card", encoding="utf-8")
    (scan / "details" / "000002.md").write_text("# card 2", encoding="utf-8")
    (scan / "stage_results").mkdir()
    (scan / "stage_results" / "gate1.json").write_text(
        '{"stage":"gate1"}',
        encoding="utf-8",
    )
    (scan / "decision_records.json").write_text(
        '{"records":[]}',
        encoding="utf-8",
    )
    (scan / "outbox").mkdir()
    (scan / "outbox" / "events.json").write_text(
        '{"events":[]}',
        encoding="utf-8",
    )
    (scan / "outbox" / "consumer_state.json").write_text(
        '{"receipts":[]}',
        encoding="utf-8",
    )
    (scan / "retro").mkdir()
    (scan / "retro" / "attribution.csv").write_text(
        "code,fwd_2_oc\n000001,0.1\n",
        encoding="utf-8",
    )
    (scan / "retro" / "rejection_attribution.csv").write_text(
        "code,first_rejection_stage\n000001,BOUGHT\n",
        encoding="utf-8",
    )
    (scan / "retro" / "abstention_verdict.json").write_text(
        '{"status":"NOT_ABSTAINED"}',
        encoding="utf-8",
    )
    (scan / "shadow").mkdir()
    (scan / "shadow" / "l3_audit_candidates.csv").write_text(
        "code\n000001\n",
        encoding="utf-8",
    )
    (report / "summary.md").write_text("# summary", encoding="utf-8")
    (report / "manifest.json").write_text(
        '{"analysis_date":"2026-07-28"}',
        encoding="utf-8",
    )

    index = build_artifact_index(scan, report_dir=report, now=NOW)
    rows = _by_name(index)

    assert index["schema_version"] == 1
    assert index["analysis_date"] == "2026-07-28"
    assert rows["market_pack"]["status"] == "PRESENT"
    assert len(rows["market_pack"]["content_hash"]) == 64
    assert rows["l4_cards"]["status"] == "PRESENT"
    assert len(rows["l4_cards"]["content_hash"]) == 64
    assert rows["summary"]["status"] == "PRESENT"
    assert rows["stage_results"]["status"] == "PRESENT"
    assert len(rows["stage_results"]["content_hash"]) == 64
    assert rows["decision_records"]["status"] == "PRESENT"
    assert len(rows["decision_records"]["content_hash"]) == 64
    assert rows["outbox_events"]["status"] == "PRESENT"
    assert rows["consumer_state"]["status"] == "PRESENT"
    assert rows["retro_attribution"]["status"] == "PRESENT"
    assert rows["rejection_attribution"]["status"] == "PRESENT"
    assert rows["abstention_verdict"]["status"] == "PRESENT"
    assert rows["l3_audit_candidates"]["status"] == "PRESENT"
    assert len(rows) == 26
    assert rows["finalists"]["status"] == "MISSING"
    assert rows["finalists"]["content_hash"] is None
    assert rows["market_pack"]["input_hash"] is None


def test_index_distinguishes_empty_from_missing(tmp_path):
    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    (scan / "finalists.csv").write_bytes(b"")
    rows = _by_name(build_artifact_index(scan, now=NOW))
    assert rows["finalists"]["status"] == "EMPTY"
    assert rows["l3_judged"]["status"] == "MISSING"


def test_write_index_is_atomic_and_carries_contract_identity(tmp_path):
    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    (scan / "run_contract.json").write_text(
        json.dumps({"run_id": "run-1", "contract_hash": "a" * 64}),
        encoding="utf-8",
    )
    path = write_artifact_index(scan, now=NOW)
    index = json.loads(path.read_text(encoding="utf-8"))
    assert path == scan / "artifact_index.json"
    assert index["run_id"] == "run-1"
    assert index["contract_hash"] == "a" * 64
    assert not (scan / "artifact_index.json.tmp").exists()
