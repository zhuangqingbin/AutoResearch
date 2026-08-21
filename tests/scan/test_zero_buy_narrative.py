"""0买判词必须按真机制分桶:07-21 的「无一过 ≥OW 三门」是不实陈述(12 卡中 6 张早停)。"""
from __future__ import annotations

import json

from autoresearch.scan.market import render_funnel_readout

_HOLD = "**Rating**: Hold\nFINAL TRANSACTION PROPOSAL: **HOLD**\n"


def _day(tmp_path, stops: dict, n_cards: int = 3):
    d = tmp_path / "2026-07-25"
    (d / "details").mkdir(parents=True)
    # regime 取自 market_pack:判词里要拼「risk_off regime 下的纪律空仓」,缺文件会走回退口径
    (d / "market_pack.json").write_text(
        json.dumps({"regime": {"label": "risk_off"}}, ensure_ascii=False), encoding="utf-8")
    codes = ["000651", "300857", "000002"][:n_cards]
    (d / "finalists.csv").write_text(
        "code,name,lane\n" + "".join(f"{c},票{c},composite\n" for c in codes), encoding="utf-8")
    for c in codes:
        (d / "details" / f"{c}.md").write_text(f"# 决策卡 — {c}\n{_HOLD}", encoding="utf-8")
    (d / "_early_stop.json").write_text(json.dumps(stops, ensure_ascii=False), encoding="utf-8")
    return d


def test_zero_buy_reports_early_stop_bucket(tmp_path):
    d = _day(tmp_path, {"000651": {"phase": "P3", "reason": "资金流出"},
                        "300857": {"phase": "P3", "reason": "涨停追高"}})
    out = render_funnel_readout(d)
    assert "0 买" in out
    assert "早停 2" in out
    assert "满卡" in out
    assert "无一过" not in out          # 旧的不实判词必须消失
    assert "日级弃权裁决: **IMMATURE**" in out
    assert "非漏斗故障" not in out


def test_zero_buy_without_early_stop_file_is_honest(tmp_path):
    """无 _early_stop.json(旧日/未落)→ 明说口径未知,不得倒退回「无一过三门」。"""
    d = _day(tmp_path, {})
    (d / "_early_stop.json").unlink()
    out = render_funnel_readout(d)
    assert "0 买" in out
    assert "无一过" not in out
    assert "口径未知" in out


def test_zero_buy_rerun_can_show_verified_mature_verdict(tmp_path):
    from datetime import datetime, timezone

    import pandas as pd

    from autoresearch.learning.abstention_ledger import (
        write_abstention_verdict,
    )

    d = _day(tmp_path, {})
    rows = pd.DataFrame(
        [
            {
                "date": d.name,
                "code": "000651",
                "first_rejection_stage": "L4_RUBRIC_SCORE",
                "final_action": "ABSTAIN",
                "gate_state_quality": "COMPLETE",
                "buyable": True,
                "mature": True,
                "excess_2": -0.03,
                "opportunity": False,
            }
        ]
    )
    write_abstention_verdict(
        d,
        rows,
        now=datetime(2026, 7, 28, tzinfo=timezone.utc),
    )
    out = render_funnel_readout(d)
    assert "日级弃权裁决: **CORRECT**" in out


# ───────────────────────── E6 active:决策口径同源 brief ③(P0 低位转强波) ─────────────────────────


def _decision_doc(*, blocked=False):
    cands = [{"code": "002081", "name": "金螳螂", "sector": "装修装饰Ⅱ", "pinned": False,
              "eligible": True, "rank": 1, "relative_decision_score": 0.70,
              "faces": {}, "faces_missing": [], "research_rating": "Underweight",
              "expected_abs_gap": {"value": None, "status": "UNMEASURED", "n": 0}}]
    return {"schema_version": 1, "rule_version": "e6.v2.0", "mode": "active", "date": "2026-07-25",
            "ruler": "gap_c1_o2",
            "benchmark": {"market": {"column": "rel_gap_market", "n": 4276},
                          "sector": {"column": "rel_gap_sector", "n_sectors": 129}},
            "counts": {"candidates": 9, "eligible": 0 if blocked else 6, "buys": 0 if blocked else 1},
            "candidates": cands,
            "buys": [] if blocked else [{"code": "002081", "basis": "relative", "rank": 1}],
            "blocked": blocked,
            "blocked_reasons": [{"reason": "hard_gate.no_redflag", "n": 9}] if blocked else []}


def _activate(monkeypatch, d, doc, *, active=True):
    import autoresearch.scan.relative_buy as rb
    monkeypatch.setattr(rb, "is_active", lambda: active)
    (d / rb.DECISION_FILENAME).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def test_e6_active_with_buy_prints_decision_not_idle(tmp_path, monkeypatch):
    """08-20 病灶:brief ③ ✅ relative BUY 金螳螂,summary 同段却印「0 买…空仓观望」。"""
    d = _day(tmp_path, {"000651": {"phase": "P3", "reason": "资金流出"},
                        "300857": {"phase": "P3", "reason": "涨停追高"}})
    _activate(monkeypatch, d, _decision_doc())
    out = render_funnel_readout(d)
    assert "相对 BUY 1 只" in out and "金螳螂 002081" in out and "卡面 Underweight" in out
    assert "研究评级 ≥OW 0 只" in out
    assert "空仓观望" not in out and "无一进买单" not in out
    assert "早停 2" in out                      # 「为什么没有 ≥OW」机制拆分仍在
    assert "日级弃权裁决: **IMMATURE**" in out


def test_e6_active_blocked_prints_blocked(tmp_path, monkeypatch):
    d = _day(tmp_path, {})
    _activate(monkeypatch, d, _decision_doc(blocked=True))
    out = render_funnel_readout(d)
    assert "相对 BUY BLOCKED" in out and "hard_gate.no_redflag×9" in out
    assert "空仓观望" not in out


def test_e6_line_has_no_banned_phrase(tmp_path, monkeypatch):
    from autoresearch.scan.relative_facts import BANNED_RELATIVE_PHRASES
    d = _day(tmp_path, {})
    _activate(monkeypatch, d, _decision_doc())
    out = render_funnel_readout(d)
    for banned in BANNED_RELATIVE_PHRASES:
        assert banned not in out
    assert "不承诺绝对" in out


def test_e6_inactive_or_stale_decision_is_byte_identical_legacy(tmp_path, monkeypatch):
    """shadow 期 / 决策文件过期(date 不符)→ 与无文件时逐字节相同(parity 锁)。"""
    d = _day(tmp_path, {})
    import autoresearch.scan.relative_buy as rb
    monkeypatch.setattr(rb, "is_active", lambda: False)
    baseline = render_funnel_readout(d)
    (d / rb.DECISION_FILENAME).write_text(json.dumps(_decision_doc()), encoding="utf-8")
    assert render_funnel_readout(d) == baseline            # shadow:文件在也不读
    monkeypatch.setattr(rb, "is_active", lambda: True)
    stale = dict(_decision_doc(), date="2026-07-24")        # 昨天的文件 → load_decision 判 None
    (d / rb.DECISION_FILENAME).write_text(json.dumps(stale), encoding="utf-8")
    assert render_funnel_readout(d) == baseline
