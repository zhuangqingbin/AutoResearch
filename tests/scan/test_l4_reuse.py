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


# ══ Wave7 B′-f:持仓豁免 + 相对市场的价格门 ═══════════════════════════════════
#
# 2026-07-27 实锤(两处独立盲区,同一晚同时发作):
#  ① 本模块对「持仓」零认知 —— `pinned` 在全文件零出现。它只对买入侧做了豁免
#     (≥OW 永不复用,「买点必须重研」),持仓侧没有对称豁免,于是 688766/601869 两只持仓
#     吃了 07-24 的旧卡,连带**绕过 pinned SELL 双复核**;`force_full: true` 只覆盖哨兵档,
#     管不到 reuse —— 两者是独立的两道口子。
#  ② 价格门量的是**绝对**涨跌幅:那天全市场中位 +2.67%、141 家涨停,688766 当日 -4.58%
#     (相对跑输 7.3pp、资金三线全负),却因绝对值差 0.4pp 没碰 5% 阈值被判「没动、可复用」。
#     普涨日里「没动」恰恰等于「大幅跑输」——代理量与被代理的东西反号。


def _mk_market(d, *, n=200, close=10.0):
    """给某日 staging 补一张全市场帧(_market_move 的基准来源)。"""
    pd.DataFrame([{"code": f"{i:06d}", "close": close} for i in range(1, n + 1)]).to_csv(
        d / "L1_scored_full.csv", index=False)


def test_pinned_never_reuses_even_when_all_other_gates_pass(tmp_path):
    """持仓永不复用 —— 与「≥OW 永不复用」对称:买点必须重研,卖/持判断同样必须当日重研。"""
    _mk(tmp_path, "2026-06-30", card=HOLD)
    d = _mk(tmp_path, "2026-07-02", close=102.0, anns=[])       # 其余条件全过(见 happy path)
    (d / "details" / f"{_C}.md").unlink(missing_ok=True)

    assert reuse_decision(_C, d)["reuse"] is True, "前提失效:这组条件本应可复用"

    dec = reuse_decision(_C, d, pinned=True)
    assert dec["reuse"] is False
    assert any("保送持仓" in r for r in dec["reasons"]), "否决理由要说人话,便于报告转述"


def test_underperformance_on_a_rally_day_breaks_reuse(tmp_path):
    """普涨日跑输 = 「动了」。绝对口径看不见,相对口径必须看见(688766 的真实形状)。"""
    prev = _mk(tmp_path, "2026-07-24", card=HOLD, close=100.0)
    _mk_market(prev, close=10.0)
    d = _mk(tmp_path, "2026-07-27", close=95.4, anns=[])        # 本票 -4.6%
    _mk_market(d, close=10.267)                                  # 全市场 +2.67%
    (d / "details" / f"{_C}.md").unlink(missing_ok=True)

    dec = reuse_decision(_C, d)

    assert dec["reuse"] is False, "普涨日跑输 7pp 仍判可复用 = 阈值量错了尺子"
    assert abs(dec["price_chg"] + 0.046) < 5e-3      # 绝对 -4.6%,本身没碰 5% 门
    assert dec["price_excess"] < -0.07               # 超额 ≈ -7.3pp,碰了
    assert any("超额" in r for r in dec["reasons"])


def test_moving_with_the_market_still_reuses(tmp_path):
    """反向保护:随大盘同步涨跌(超额≈0)不该被当成「动了」而重烧一张 Opus 卡。"""
    prev = _mk(tmp_path, "2026-07-24", card=HOLD, close=100.0)
    _mk_market(prev, close=10.0)
    d = _mk(tmp_path, "2026-07-27", close=106.0, anns=[])        # 本票 +6%(绝对已超 5%)
    _mk_market(d, close=10.6)                                    # 全市场也 +6%
    (d / "details" / f"{_C}.md").unlink(missing_ok=True)

    dec = reuse_decision(_C, d)

    assert dec["reuse"] is True, "绝对口径会误杀:+6% 超 5% 门,但它只是跟着大盘走"
    assert abs(dec["price_excess"]) < 1e-3


def test_missing_market_frame_falls_back_to_absolute_and_says_so(tmp_path):
    """基准不可得 → 回退绝对口径,但必须留痕(降级不留痕才是真病)。"""
    _mk(tmp_path, "2026-07-24", card=HOLD, close=100.0)          # 两日都不写 L1_scored_full
    d = _mk(tmp_path, "2026-07-27", close=93.0, anns=[])         # -7%,绝对口径也该否决
    (d / "details" / f"{_C}.md").unlink(missing_ok=True)

    dec = reuse_decision(_C, d)

    assert dec["reuse"] is False
    assert any("回退绝对口径" in r for r in dec["reasons"]), "降级没记账"


def test_reuse_pass_wires_pinned_from_config(tmp_path, monkeypatch):
    """生产者接线:`reuse_pass` 必须自己去读 pinned 真身 —— 只加形参不接线 = 死参数(FN-1 家族)。"""
    _mk(tmp_path, "2026-06-30", card=HOLD)
    d = _mk(tmp_path, "2026-07-02", close=102.0, anns=[])
    (d / "details" / f"{_C}.md").unlink(missing_ok=True)

    monkeypatch.setattr("autoresearch.scan.user_config.load_pinned",
                        lambda *a, **k: {"kept": [{"code": _C}], "expired": []})
    df = reuse_pass(d)

    assert list(df["reuse"]) == [False]
    assert "保送持仓" in df.iloc[0]["reasons"]
