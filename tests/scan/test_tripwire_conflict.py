import pandas as pd

from autoresearch.scan import decision_finalize as df
from autoresearch.scan import report_sections as rs


def test_conflict_when_tripwire_fires_and_rating_not_sell(monkeypatch):
    monkeypatch.setattr(df, "_tripwire_hits", lambda *a, **k: [
        {"code": "300857", "kind": "price", "raw": "[价格线] close < 210.01 → 清仓",
         "detail": "收盘 205.00 < 210.01 → 清仓", "card_date": "2026-07-28"}])
    out = df.tripwire_conflicts("ignored", "2026-07-29",
                                [{"code": "300857", "rating": "Underweight"}])
    assert "300857" in out
    assert out["300857"]["rating"] == "Underweight"
    assert "205.00" in out["300857"]["tripwire_detail"]


def test_no_conflict_when_rating_is_sell(monkeypatch):
    """终评已是 Sell = 两把尺子同向,不构成冲突。"""
    monkeypatch.setattr(df, "_tripwire_hits", lambda *a, **k: [
        {"code": "920179", "kind": "price", "raw": "r", "detail": "d", "card_date": "x"}])
    out = df.tripwire_conflicts("ignored", "2026-07-29",
                                [{"code": "920179", "rating": "Sell"}])
    assert out == {}


def test_no_conflict_when_no_tripwire(monkeypatch):
    monkeypatch.setattr(df, "_tripwire_hits", lambda *a, **k: [])
    out = df.tripwire_conflicts("ignored", "2026-07-29",
                                [{"code": "688766", "rating": "Hold"}])
    assert out == {}


def test_date_kind_hits_are_not_conflicts(monkeypatch):
    """日期线/事件旗是提醒,不是与评级对立的价格判据。"""
    monkeypatch.setattr(df, "_tripwire_hits", lambda *a, **k: [
        {"code": "300857", "kind": "date", "raw": "r", "detail": "d", "card_date": "x"}])
    out = df.tripwire_conflicts("ignored", "2026-07-29",
                                [{"code": "300857", "rating": "Hold"}])
    assert out == {}


def test_conflict_block_renders_two_rulers():
    block = rs._conflict_block({"300857": {
        "tripwire_detail": "收盘 205.00 < 210.01 → 清仓",
        "tripwire_raw": "[价格线] close < 210.01 → 清仓",
        "rating": "Underweight"}})
    assert "300857" in block
    assert "收盘 205.00 < 210.01" in block
    assert "Underweight" in block
    assert "系统不合并" in block


def test_conflict_block_empty_when_no_conflict():
    assert rs._conflict_block({}) == ""


def test_multiple_price_hits_are_all_kept_not_just_first(monkeypatch):
    """同票多条价格线同时触发 —— 全部收录/全部渲染,不许静默丢弃更紧急的那条
    (复核 Wave9 A-2 Minor→must-fix:旧版 `out.setdefault` 只留先出现的一条,真实场景
    300857 07-28 卡同时挂「跌破210.01清仓」+「跌破196.73已应清仓」,后者更紧急却会被吞)。
    """
    monkeypatch.setattr(df, "_tripwire_hits", lambda *a, **k: [
        {"code": "300857", "kind": "price", "raw": "[价格线] close < 210.01 → 清仓",
         "detail": "收盘 205.00 < 210.01 → 清仓", "card_date": "2026-07-28"},
        {"code": "300857", "kind": "price",
         "raw": "[价格线] close < 196.73 → 已应清仓,若仍持有立即处置",
         "detail": "收盘 205.00 < 196.73 → 已应清仓,若仍持有立即处置", "card_date": "2026-07-28"}])
    out = df.tripwire_conflicts("ignored", "2026-07-29",
                                [{"code": "300857", "rating": "Underweight"}])
    assert len(out["300857"]["all_hits"]) == 2
    assert "210.01" in out["300857"]["tripwire_detail"]
    assert "196.73" in out["300857"]["tripwire_detail"]

    block = rs._conflict_block(out)
    assert "210.01" in block
    assert "196.73" in block
    assert "已应清仓" in block


# ───────────────────────── Wave9 final-fix I-2:比对前一日的卡,不是今日新卡 ─────────────────────────
# final-review 发现:全部既有测试都 monkeypatch `_tripwire_hits`,100% 合成,从没在真实文件
# 形状上验证过。真正的缺陷是唯一调用点(`build_summary`,今日卡已写完之后)拿今日收盘去比
# 一条 LLM 今天刚写的线——几乎自洽,测不出真实分歧。下面这条不 mock `_tripwire_hits`,直接
# 用真实 07-29/300857 场景的最小复刻(前一日卡线 210.01 + 今日收盘 205.00 + 今日新卡把线
# 改写成 194.73~233.27)走全链路。


def test_uses_prior_day_card_not_todays_own_card(tmp_path):
    """回归钉子(final-review Important-2,07-29/300857 原地重放):assemble 在**今日卡
    已写完之后**才调 `tripwire_conflicts`——若比对含今日的"最新卡",拿到的正是 LLM 今天
    (手里已有今日收盘)刚写的线,自洽,测不出冲突。真实 07-29 冲突成立,是因为当时"在用"
    的线来自 07-28 的卡(210.01);07-29 新卡把线改写成 194.73~233.27,把 205.00 这个
    收盘价包在带内。判据必须是**严格早于今日**的最新卡,不是今日新卡。"""
    scan_root = tmp_path / "scan"
    prior = scan_root / "2026-07-28"
    (prior / "details").mkdir(parents=True)
    (prior / "details" / "300857.md").write_text(
        "〔卡契约 v3〕\n# 决策卡 — 300857 协创数据\n**Rating**: Underweight\n**盯梢线**\n"
        "- [价格线] close < 210.01 → 清仓\n", encoding="utf-8")

    today = scan_root / "2026-07-29"
    (today / "details").mkdir(parents=True)
    (today / "details" / "300857.md").write_text(
        "〔卡契约 v3〕\n# 决策卡 — 300857 协创数据\n**Rating**: Underweight\n**盯梢线**\n"
        "- [价格线] close < 194.73 → 清仓\n"
        "- [价格线] close >= 233.27 → 本 UW 失效,改持\n", encoding="utf-8")
    pd.DataFrame([{"code": "300857", "close": 205.00}]).to_csv(
        today / "L1_scored_full.csv", index=False)

    out = df.tripwire_conflicts(today, "2026-07-29", [{"code": "300857", "rating": "Underweight"}])
    assert "300857" in out, ("应比对 07-28 卡的旧线(210.01),而非今日 07-29 新写的线"
                             "(194.73~233.27 把 205.00 包在带内、测不出冲突)")
    assert "210.01" in out["300857"]["tripwire_detail"]
    assert "194.73" not in out["300857"]["tripwire_detail"]
    assert out["300857"]["all_hits"][0]["card_date"] == "2026-07-28"

    block = rs._conflict_block(out)
    assert "2026-07-28" in block, "渲染必须让读者知道这条线来自哪一天的卡"


def test_no_conflict_when_no_prior_card_exists(tmp_path):
    """首次覆盖该票(没有更早的卡)→ 安静返回空,不报错(final-fix 任务书明确要求)。"""
    scan_root = tmp_path / "scan"
    today = scan_root / "2026-07-29"
    (today / "details").mkdir(parents=True)
    (today / "details" / "300857.md").write_text(
        "〔卡契约 v3〕\n# 决策卡 — 300857 协创数据\n**Rating**: Underweight\n**盯梢线**\n"
        "- [价格线] close < 999.0 → 清仓\n", encoding="utf-8")
    pd.DataFrame([{"code": "300857", "close": 205.00}]).to_csv(
        today / "L1_scored_full.csv", index=False)

    out = df.tripwire_conflicts(today, "2026-07-29", [{"code": "300857", "rating": "Underweight"}])
    assert out == {}
