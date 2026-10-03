"""情报分层的证据(2026-10-03 B6 / Q6):两段初判里,不读情报的初评被情报改动了多少。

问题是「六面全量情报只给硬门后幸存者与 📌,其余只查 T0+24h」会不会丢决策 —— 证据来自
two-stage-v1 的 `changes.json`:初评(不读情报)→ 终评(读情报)改了哪些字段、引用的新证据是不是情报。
"""
from __future__ import annotations

import json

from autoresearch.research import intel_tiering as it


def _attempt(staging, code, changes):
    path = staging / "session_attempts" / code / "a1"
    path.mkdir(parents=True)
    (path / "changes.json").write_text(json.dumps(changes), encoding="utf-8")


def test_changes_are_split_by_survivors_and_attributed_to_intel(tmp_path):
    staging = tmp_path / "20261008-1008_2201" / "trace" / "staging"
    _attempt(staging, "600001", {"changed_fields": ["rating", "gates.主力真在"],
                                 "new_evidence_refs": ["scan.l4.600001.a1.intel_doc"]})
    _attempt(staging, "600002", {"changed_fields": [], "new_evidence_refs": []})
    _attempt(staging, "600003", {"changed_fields": ["dimensions.催化"],
                                 "new_evidence_refs": ["scan.l4.600003.a1.slim"]})
    (staging / "_relative_buy_decision.json").write_text(json.dumps({"candidates": [
        {"code": "600001", "eligible": True}, {"code": "600002", "eligible": False},
        {"code": "600003", "eligible": False}]}), encoding="utf-8")

    rows = it.readout(tmp_path)

    by_code = {r["code"]: r for r in rows}
    assert by_code["600001"] == {"run": "20261008-1008_2201", "code": "600001", "survivor": True,
                                 "changed": True, "rating_changed": True, "intel_cited": True,
                                 "n_changed": 2}
    assert by_code["600003"]["intel_cited"] is False
    summary = it.summarize(rows)
    assert summary["survivor"] == {"n": 1, "changed": 1, "rating_changed": 1, "intel_cited": 1}
    assert summary["rejected"] == {"n": 2, "changed": 1, "rating_changed": 0, "intel_cited": 0}


def test_no_two_stage_records_means_an_empty_readout(tmp_path):
    assert it.readout(tmp_path) == []
    assert it.summarize([])["survivor"]["n"] == 0
