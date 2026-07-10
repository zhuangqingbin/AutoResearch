"""L4 卡片 TTL 复用:通过路径 + 各否决路径 + 写卡不覆盖。合成,无网络。

spec: docs/specs/2026-07-02-scan-l4-economy-design.md §1
"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.scan.l4_reuse import reuse_decision, reuse_pass, write_reused_card

_C = "000001"
HOLD = "# 决策卡\n**Rating**: Hold\n**一行多空**:多:x ｜ 空:y\n"
OW = "# 决策卡\n**Rating**: Overweight\n"


def _mk(root, date, card=None, close=100.0, regime="range", conviction=50.0,
        anns=None):
    d = root / date
    (d / "details").mkdir(parents=True)
    if card is not None:
        (d / "details" / f"{_C}.md").write_text(card, encoding="utf-8")
    pd.DataFrame([{"code": _C, "close": close}]).to_csv(d / "L1_recall_top1000.csv", index=False)
    (d / "meta.json").write_text(json.dumps({"regime": regime}), encoding="utf-8")
    pd.DataFrame([{"code": _C, "name": "甲", "conviction": conviction}]).to_csv(
        d / "finalists.csv", index=False)
    if anns is not None:
        (d / "L3_news").mkdir()
        (d / "L3_news" / f"{_C}.json").write_text(json.dumps(anns), encoding="utf-8")
    return d


def test_reuse_happy_path_and_write(tmp_path):
    _mk(tmp_path, "2026-06-30", card=HOLD)
    d = _mk(tmp_path, "2026-07-02", close=102.0, anns=[])       # Δ价2%、无公告
    # 今日 details 里还没有该票的卡(fixture 写的是 6-30 的)
    (d / "details" / f"{_C}.md").unlink(missing_ok=True)
    dec = reuse_decision(_C, d)
    assert dec["reuse"] and dec["prior_date"] == "2026-06-30" and dec["prior_rating"] == "Hold"
    assert dec["age_days"] == 2 and abs(dec["price_chg"] - 0.02) < 1e-9
    p = write_reused_card(_C, d, dec)
    text = p.read_text(encoding="utf-8")
    assert "♻️" in text and "复用卡" in text and "**Rating**: Hold" in text
    assert write_reused_card(_C, d, dec) is None                 # 已存在 → 不覆盖


def test_no_reuse_price_and_rating_and_age(tmp_path):
    _mk(tmp_path, "2026-06-30", card=HOLD)
    d = _mk(tmp_path, "2026-07-02", close=110.0, anns=[])
    (d / "details" / f"{_C}.md").unlink(missing_ok=True)
    dec = reuse_decision(_C, d)
    assert not dec["reuse"] and any("Δ价" in r for r in dec["reasons"])

    root2 = tmp_path / "b"
    _mk(root2, "2026-06-30", card=OW)
    d2 = _mk(root2, "2026-07-02", close=100.0, anns=[])
    (d2 / "details" / f"{_C}.md").unlink(missing_ok=True)
    dec2 = reuse_decision(_C, d2)
    assert not dec2["reuse"] and any("必重研" in r for r in dec2["reasons"])

    root3 = tmp_path / "c"
    _mk(root3, "2026-06-20", card=HOLD)
    d3 = _mk(root3, "2026-07-02", close=100.0, anns=[])
    (d3 / "details" / f"{_C}.md").unlink(missing_ok=True)
    dec3 = reuse_decision(_C, d3)
    assert not dec3["reuse"] and any("TTL" in r for r in dec3["reasons"])


def test_no_reuse_news_conviction_regime_chain(tmp_path):
    _mk(tmp_path, "2026-06-30", card=HOLD)
    d = _mk(tmp_path, "2026-07-02", close=100.0,
            anns=[{"ann_date": "20260701", "title": "重大合同"}])
    (d / "details" / f"{_C}.md").unlink(missing_ok=True)
    assert not reuse_decision(_C, d)["reuse"]                    # 新公告

    root2 = tmp_path / "b"
    _mk(root2, "2026-06-30", card=HOLD)
    d2 = _mk(root2, "2026-07-02", close=100.0, conviction=80.0, anns=[])
    (d2 / "details" / f"{_C}.md").unlink(missing_ok=True)
    assert not reuse_decision(_C, d2)["reuse"]                   # 强先验

    root3 = tmp_path / "c"
    _mk(root3, "2026-06-30", card=HOLD, regime="range")
    d3 = _mk(root3, "2026-07-02", close=100.0, regime="risk_off", anns=[])
    (d3 / "details" / f"{_C}.md").unlink(missing_ok=True)
    assert not reuse_decision(_C, d3)["reuse"]                   # regime 翻转

    root4 = tmp_path / "d"
    _mk(root4, "2026-06-30", card="♻️ 复用卡\n" + HOLD)
    d4 = _mk(root4, "2026-07-02", close=100.0, anns=[])
    (d4 / "details" / f"{_C}.md").unlink(missing_ok=True)
    assert not reuse_decision(_C, d4)["reuse"]                   # 禁链式复用


def test_reuse_pass_apply(tmp_path):
    _mk(tmp_path, "2026-06-30", card=HOLD)
    d = _mk(tmp_path, "2026-07-02", close=101.0, anns=[])
    (d / "details" / f"{_C}.md").unlink(missing_ok=True)
    df = reuse_pass(d, apply=True)
    assert len(df) == 1 and bool(df.iloc[0]["reuse"])
    assert (d / "details" / f"{_C}.md").exists()
    from autoresearch.scan.health import l4_phase_stats
    assert l4_phase_stats(d)["n_reused"] == 1                    # health 能识别复用卡


# ───────────────────────── 深否决豁免 + 菜单滞回(2026-07-04) ─────────────────────────

_GATED_HOLD = HOLD + ("\n**Rubric建议**: 表面4维净分 **-2/4** ｜ "
                      "OW三门 主力真在✗·业绩真兑现△·估值不透支✗ → 两门失守 → 建议 Hold\n")


def test_reuse_deep_reject_bypasses_conviction(tmp_path):
    """前卡 OW三门失守≥2 = 深否决 → 今日 L3 conviction 再高也复用(别为失真先验重烧 Opus)。"""
    _mk(tmp_path, "2026-06-30", card=_GATED_HOLD)
    d = _mk(tmp_path, "2026-07-02", close=100.0, conviction=85.0, anns=[])
    (d / "details" / f"{_C}.md").unlink(missing_ok=True)
    dec = reuse_decision(_C, d)
    assert dec["reuse"], dec["reasons"]
    assert any("豁免" in r for r in dec["reasons"])


def test_carryover_append(tmp_path):
    """菜单滞回:昨日 finalist(前卡 ≤Hold)∩ 今日 L2 但被 L3 换血 → 保席追加(lane=carryover),
    幂等;不在今日 L2 的不追。churn 90% 日复用率从 7% 上抬的入口。"""
    import pandas as pd

    from autoresearch.scan.l4_reuse import append_carryover
    prior = tmp_path / "2026-07-01"
    (prior / "details").mkdir(parents=True)
    pd.DataFrame([{"code": "000010", "name": "甲", "sector": "电子"},
                  {"code": "000020", "name": "乙", "sector": "电力"}]).to_csv(
        prior / "finalists.csv", index=False)
    (prior / "details" / "000010.md").write_text(HOLD, encoding="utf-8")
    (prior / "details" / "000020.md").write_text(HOLD, encoding="utf-8")

    today = tmp_path / "2026-07-02"
    (today / "details").mkdir(parents=True)
    pd.DataFrame([{"code": "000030", "name": "丙", "sector": "汽车"}]).to_csv(
        today / "finalists.csv", index=False)
    pd.DataFrame([{"code": "000010", "name": "甲", "industry": "电子", "l2_rank": 5},
                  {"code": "000030", "name": "丙", "industry": "汽车", "l2_rank": 1}]).to_csv(
        today / "L2_gbdt_top200.csv", index=False)

    assert append_carryover(today) == 1                      # 甲追加;乙不在今日 L2 → 不追
    fin = pd.read_csv(today / "finalists.csv", dtype={"code": str})
    row = fin[fin["code"].astype(str).str.zfill(6) == "000010"]
    assert len(row) == 1 and row.iloc[0]["lane"] == "carryover"
    assert append_carryover(today) == 0                      # 幂等


def test_carryover_append_preserves_ticker_leading_zeros(tmp_path):
    """finalists.csv 往返不许吃掉 `ticker` 前导零(002156→2156 → assemble 判「卡片缺失」)。

    2026-07-09 实跑:append_carryover 以 dtype={"code": str} 读回,`ticker` 被解析成 int64,
    002156/002049 两张真卡被 L5 报为缺失。旧 fixture 无 `ticker` 列 → 从没测到。
    """
    import pandas as pd

    from autoresearch.scan.l4_reuse import append_carryover
    prior = tmp_path / "2026-07-08"
    (prior / "details").mkdir(parents=True)
    pd.DataFrame([{"ticker": "000010", "code": "000010", "name": "甲"}]).to_csv(
        prior / "finalists.csv", index=False)
    (prior / "details" / "000010.md").write_text(HOLD, encoding="utf-8")

    today = tmp_path / "2026-07-09"
    (today / "details").mkdir(parents=True)
    pd.DataFrame([{"ticker": "002156", "code": "002156", "name": "通富微电"}]).to_csv(
        today / "finalists.csv", index=False)          # ← 生产形状:带 ticker 列
    pd.DataFrame([{"code": "000010", "name": "甲", "industry": "电子", "l2_rank": 5},
                  {"code": "002156", "name": "通富微电", "industry": "半导体", "l2_rank": 1}]).to_csv(
        today / "L2_gbdt_top200.csv", index=False)

    assert append_carryover(today) == 1
    fin = pd.read_csv(today / "finalists.csv", dtype=str)
    assert set(fin["ticker"]) == {"002156", "000010"}, f"ticker 丢前导零:{list(fin['ticker'])}"
    assert set(fin["code"]) == {"002156", "000010"}
