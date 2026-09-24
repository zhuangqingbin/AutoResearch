"""盲卡不入账(2026-09-24 §2.6-5):任务簿说 slim 没到货的票,卡再像样也不评级、不入事实本。"""
from __future__ import annotations

import json
from pathlib import Path

from autoresearch.scan.decision_finalize import (
    BLIND_CARDS_FILENAME, _dump_final_ratings, mark_blind_cards,
)


def _scan(tmp_path: Path) -> Path:
    scan = tmp_path / "2026-09-17"
    scan.mkdir()
    (scan / "_l4_tasks.json").write_text(json.dumps({"schema_version": 1, "tasks": {
        "600150": {"code": "600150", "status": "BLOCKED",
                   "artifacts": {"slim": {"status": "MISSING"}, "card": {"status": "PRESENT"}}},
        "600018": {"code": "600018", "status": "SUCCEEDED",
                   "artifacts": {"slim": {"status": "PRESENT"}, "card": {"status": "PRESENT"}}},
    }}), encoding="utf-8")
    return scan


def test_blind_card_is_marked_and_dropped_from_final_ratings(tmp_path):
    scan = _scan(tmp_path)
    rows = [{"code": "600150", "rating": "Hold", "proposal": "HOLD", "target": "—"},
            {"code": "600018", "rating": "Hold", "proposal": "HOLD", "target": "—"}]
    blind = mark_blind_cards(scan, rows)
    assert set(blind) == {"600150"}
    assert rows[0]["blind_card"] is True and rows[0]["rating"] == "—" and rows[0]["proposal"] == "—"
    assert rows[0]["target"] == "⚠️数据不完整,未评级"
    assert "blind_card" not in rows[1]
    doc = json.loads((scan / BLIND_CARDS_FILENAME).read_text(encoding="utf-8"))
    assert doc["600150"] == {"task_status": "BLOCKED", "slim_status": "MISSING", "reason": "DATA_INTEGRITY"}
    _dump_final_ratings(scan, rows)
    final = json.loads((scan / "_final_ratings.json").read_text(encoding="utf-8"))
    assert final == {"600018": "Hold"}


def test_no_task_book_means_no_blind_verdict(tmp_path):
    scan = tmp_path / "2026-09-17"
    scan.mkdir()
    rows = [{"code": "600150", "rating": "Hold", "proposal": "HOLD", "target": "—"}]
    assert mark_blind_cards(scan, rows) == {}
    assert rows[0]["rating"] == "Hold" and not (scan / BLIND_CARDS_FILENAME).exists()
