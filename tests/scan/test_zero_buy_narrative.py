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


def test_zero_buy_without_early_stop_file_is_honest(tmp_path):
    """无 _early_stop.json(旧日/未落)→ 明说口径未知,不得倒退回「无一过三门」。"""
    d = _day(tmp_path, {})
    (d / "_early_stop.json").unlink()
    out = render_funnel_readout(d)
    assert "0 买" in out
    assert "无一过" not in out
    assert "口径未知" in out
