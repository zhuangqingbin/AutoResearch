"""session_v1 路径的 L3 merge 只读 GATE1 回显的卡数(2026-09-26 l4.max_cards)。"""
from __future__ import annotations

import json
from types import SimpleNamespace

from autoresearch.session_agent import domain_ops


def _wire(tmp_path, monkeypatch, gate1: dict):
    staging = tmp_path / "run/staging/2026-09-17"
    (staging / "session_outputs").mkdir(parents=True)
    handle = SimpleNamespace(staging=staging, analysis_date="2026-09-17")
    calls = {}
    monkeypatch.setattr(domain_ops, "_use_frozen_scan_runtime_inputs", lambda current: None)
    monkeypatch.setattr(domain_ops, "_text", lambda current, artifact_id: json.dumps(gate1))
    monkeypatch.setattr(domain_ops, "_write_scan_bundle", lambda *a, **k: None)
    monkeypatch.setattr("autoresearch.scan.l3.merge.write_finalists",
                        lambda date, budget, root, judged_path: calls.update(write_budget=budget))

    def fake_gate2(scan_dir, budget=30, **kw):
        calls["gate2_budget"] = budget
        return {"ok": True, "gate": "gate2", "finalists": [], "n": 0, "meta": {}}

    monkeypatch.setattr("autoresearch.scan.gates.gate2", fake_gate2)
    monkeypatch.setattr("autoresearch.scan.gates.record_gate_stage_result", lambda *a, **k: None)
    return handle, staging, calls


def test_l3_merge_reads_gate1_l3cap_and_gates_on_max_cards(tmp_path, monkeypatch):
    handle, staging, calls = _wire(tmp_path, monkeypatch,
                                   {"ok": True, "l4_budget": 30, "l3cap": 5, "max_cards": 8})
    domain_ops.scan_l3_merge(handle)
    assert calls == {"write_budget": 5, "gate2_budget": 8}
    assert not (staging / "session_outputs/l3_merge_note.json").exists()


def test_l3_merge_old_gate1_without_l3cap_falls_back_with_note(tmp_path, monkeypatch):
    """老 run 的 gate1.json 没有 l3cap/max_cards:回退旗后预算(与改动前同),并留痕。"""
    handle, staging, calls = _wire(tmp_path, monkeypatch, {"ok": True, "l4_budget": 30})
    domain_ops.scan_l3_merge(handle)
    assert calls == {"write_budget": 30, "gate2_budget": 30}
    note = json.loads((staging / "session_outputs/l3_merge_note.json").read_text(encoding="utf-8"))
    assert note["fallback"] == "l4_budget"
