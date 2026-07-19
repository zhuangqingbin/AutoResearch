"""L4 卡片 TTL 复用:通过路径 + 各否决路径 + 写卡不覆盖。合成,无网络。

spec: docs/specs/2026-07-02-scan-l4-economy-design.md §1
"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.scan.l4_reuse import reuse_decision, reuse_pass, write_reused_card

_C = "000001"
HOLD = "〔卡契约 v3·超短 1~2 日〕\n# 决策卡\n**Rating**: Hold\n**一行多空**:多:x ｜ 空:y\n"
HOLD_OLD = "# 决策卡\n**Rating**: Hold\n**一行多空**:多:x ｜ 空:y\n"  # 旧卡:无 v3 标记
OW = "〔卡契约 v3·超短 1~2 日〕\n# 决策卡\n**Rating**: Overweight\n"


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


# ───────────────────────── 深否决豁免(2026-07-04) ─────────────────────────
# 注:本节原有「菜单滞回(carryover)」两个测试,随 carryover 于 2026-07-16 退役一并删除
# (pr_20260716_006)。「finalists.csv 往返保 ticker 前导零」这条回归护栏由下方
# test_read_finalists_preserves_ticker_leading_zeros 直接锁 artifacts.read_finalists 契约
#(原经 watchlist.append_express 往返锁,append_express 随观察单模块清理一并删除)。丢了它,
# 000062→"62" 那类前导零坑(assemble glob 匹配不到卡片→误判缺卡→self_review 挡发布)就没人看着了。


def test_read_finalists_preserves_ticker_leading_zeros(tmp_path):
    from autoresearch.scan.artifacts import read_finalists
    fp = tmp_path / "finalists.csv"
    # 磁盘上代码列已丢前导零(002156→2156,如 int64 往返回写);读口必须补回 6 位零填。
    fp.write_text("ticker,code,name\n2156,2156,x\n300476,300476,y\n", encoding="utf-8")
    fin = read_finalists(fp)
    assert set(fin["ticker"]) == {"002156", "300476"}
    assert set(fin["code"]) == {"002156", "300476"}


_GATED_HOLD = ("〔卡契约 v3·超短 1~2 日〕\n# 决策卡\n**Rating**: Hold\n**一行多空**:多:x ｜ 空:y\n"
              "\n**Rubric建议**: 表面4维净分 **-2/4** ｜ "
              "OW三门 主力真在✗·业绩真兑现△·估值不透支✗ → 两门失守 → 建议 Hold\n")


def test_reuse_deep_reject_bypasses_conviction(tmp_path):
    """前卡 OW三门失守≥2 = 深否决 → 今日 L3 conviction 再高也复用(别为失真先验重烧 Opus)。"""
    _mk(tmp_path, "2026-06-30", card=_GATED_HOLD)
    d = _mk(tmp_path, "2026-07-02", close=100.0, conviction=85.0, anns=[])
    (d / "details" / f"{_C}.md").unlink(missing_ok=True)
    dec = reuse_decision(_C, d)
    assert dec["reuse"], dec["reasons"]
    assert any("豁免" in r for r in dec["reasons"])


# ───────────────────────── 卡契约 v3 版本门(2026-07-10) ─────────────────────────


def test_old_schema_card_not_reused(tmp_path):
    """前卡无「卡契约 v3」标记 → 旧语义卡禁复用(防超短/swing 混用)。"""
    _mk(tmp_path, "2026-06-30", card=HOLD_OLD)  # 旧卡:无 v3 标记
    d = _mk(tmp_path, "2026-07-02", close=101.0, anns=[])
    (d / "details" / f"{_C}.md").unlink(missing_ok=True)
    out = reuse_decision(_C, d)
    assert out["reuse"] is False
    assert any("旧契约卡" in r for r in out["reasons"])


def test_v3_card_reusable(tmp_path):
    """前卡正文含「〔卡契约 v3·超短 1~2 日〕」→ 不因 schema 被否决。"""
    card_v3 = "〔卡契约 v3·超短 1~2 日〕\n# 决策卡\n**Rating**: Hold\n**一行多空**:多:x ｜ 空:y\n"
    _mk(tmp_path, "2026-06-30", card=card_v3)  # v3 卡
    d = _mk(tmp_path, "2026-07-02", close=101.0, anns=[])
    (d / "details" / f"{_C}.md").unlink(missing_ok=True)
    out = reuse_decision(_C, d)
    assert not any("旧契约卡" in r for r in out["reasons"])
