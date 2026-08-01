"""价格断言主语分类(Wave10 A3):三条真现场假阳 + 误排除保护 + 计量。合成,无网络。

验收的两侧必须同时成立(只测一侧就是把探针调成"永远不报"或"永远报"):
  · **能 catch**:真捏造/真自陈仍被认领;
  · **不会误 catch**:资金占比/板块指数/基准日/他票涨停不再被判成本票股价。
"""
from __future__ import annotations

import json

import pytest

from autoresearch.scan.price_claims import (
    SUBJECTS,
    UNKNOWN_RATE_TOLERANCE_PP,
    audit_card_text,
    check_unknown_rate,
    classify_price_claims,
    extract_price_claims,
    merge_summaries,
    replay,
)


def _subjects(text, *, name, code6, year=2026):
    return classify_price_claims(
        text, name=name, code6=code6, year_hint=year).counts


def _claimed(text, *, name, code6, year=2026):
    return [(c["date"], c.get("value"), c.get("dir"))
            for c in extract_price_claims(text, name=name, code6=code6,
                                          year_hint=year)]


# ────────────────── 三条 07-30/31 真现场(逐字) ──────────────────

def test_fund_ratio_is_not_own_price_000651():
    """`主力净额…8.04 亿/占比 +3.7%` —— 主语是资金占比,不是本票股价。"""
    sent = ("实读把**承接**这条推翻:主力净额较昨卡实读的 8.04 亿/占比 +3.7%"
            "(details/000651.md,2026-07-30)一日内 −75% 且占比翻负")
    assert _claimed(sent, name="格力电器", code6="000651") == []
    assert _subjects(sent, name="格力电器", code6="000651").get("ratio_share")


def test_sector_index_is_not_own_price_688766():
    """`A股存储指数 +6.6%` —— 具体指数名永远补不完,靠结构词「指数」定主语。"""
    sent = ('"存储周期资金"在 07-31 板块级确认(A股存储指数 +6.6%)但**本票资金四口径仍净出**')
    assert _claimed(sent, name="普冉股份", code6="688766") == []
    assert _subjects(sent, name="普冉股份", code6="688766").get("index")


def test_baseline_date_is_not_the_subject_date_600535():
    """`(vs 7/29 收 15.95,-0.44%)` —— −0.44% 说的是 7/30,7/29 只是参照系。

    这一条不是主语错,是**日期**错:主语判对了、日期挂错了,对账照样输出误指控。
    """
    sent = ("本票盘中冲 16.09 后收 15.88(vs 7/29 收 15.95,-0.44%),"
            "题材最强牌当日在板块兑现")
    assert _claimed(sent, name="天士力", code6="600535") == []
    assert _subjects(sent, name="天士力", code6="600535").get("baseline_reference")


def test_correctly_dated_same_claim_is_still_kept_600535():
    """同一张卡里正确挂日的那条必须**照常认领** —— 修假阳不能顺手把真阳一起杀掉。"""
    sent = "并新增反证:7/30 板块政策日 4 家涨停而本票 -0.44%"
    assert _claimed(sent, name="天士力", code6="600535") == [("20260730", -0.44, None)]


# ────────────────── 误排除保护(收紧类改动的必测侧) ──────────────────

def test_nearer_self_mark_beats_the_other_subject_keyword():
    """「板块普涨,本股涨 3%」—— `本股` 比 `板块` 近,那 3% 是本股的。"""
    sent = "2026-07-21 板块普涨,本股涨 3%"
    assert _claimed(sent, name="某某股份", code6="000001") == [("20260721", 3.0, None)]


def test_evidence_after_the_number_still_counts():
    """`那根 +16.4% 反抽` —— 证据在数字**之后**;只看左窗会把这条真自陈判成未知。"""
    sent = "(a) 2026-07-21 那根 +16.4% 反抽后仍创新低,证明本票反弹被系统性卖出"
    assert _claimed(sent, name="凯德石英", code6="920179") == [("20260721", 16.4, None)]


def test_evidence_further_left_than_a_fixed_window():
    """`冲高 422.37 后收 389.64(**-4.36%**)` —— 证据在 12 字定窗之外。"""
    sent = "而 2026-07-24 该股高开 413.81、冲高 422.37 后收 389.64(**-4.36%**,收在日内低位)"
    assert _claimed(sent, name="普冉股份", code6="688766") == [("20260724", -4.36, None)]


def test_own_claim_survives_alongside_a_quoted_index_in_the_same_sentence():
    """本票自陈与转引的指数行情同句 —— 整句豁免会连真自陈一起扔掉,故转引走逐 % 判定。"""
    sent = ("2026-07-28 本票 +3.57%(已核·verified OHLCV)对照【网查·〔转引〕】"
            "银行指数当日 +1.43%")
    claimed = _claimed(sent, name="农业银行", code6="601288")
    assert [c[1] for c in claimed] == [3.57]
    assert _subjects(sent, name="农业银行", code6="601288").get("index") == 1


def test_relative_date_claim_is_not_pinned_to_the_absolute_date():
    """`个股次日 -6% 回吐` —— 主语日是 06-29 的**次日**,挂到 06-29 上就是假指控。"""
    sent = "06-29 眼科概念 25.67亿净流入+5.18% 但**个股次日 -6% 回吐**"
    assert _claimed(sent, name="华厦眼科", code6="301267") == []


def test_peer_limit_move_is_not_claimed_as_own():
    """「4 家涨停」是别的票涨停 —— 落到涨停分支时同样要判主语。

    句里刻意不放 `板块/指数` 这类别的主语词:只有「N 家涨停」这条规则救得了它,
    否则 `涨停` 本身就是价格证据、离自己距离为 0,会被判成本票涨停(变异测试实锤)。
    """
    sent = "2026-07-30 同行 4 家涨停,本票收平"
    assert _claimed(sent, name="天士力", code6="600535") == []
    # 同结构但主语是本票 → 必须照常认领,别把规则写成"见涨停就不认"
    own = "2026-07-30 本票涨停,量能配合"
    assert _claimed(own, name="天士力", code6="600535") == [("20260730", None, 1)]


def test_real_fabricated_limit_is_still_caught():
    """真捏造仍必须被逮住 —— 这是探针存在的理由(pr_20260714_006)。"""
    sent = "本股 2026-07-17 涨停,量能配合"
    assert _claimed(sent, name="赤峰黄金", code6="600988") == [("20260717", None, 1)]


# ────────────────── 计量契约 ──────────────────

def test_trailing_date_wins_over_nearest_preceding_date():
    """逐字取自 601869 07-24 真卡:`+5.65%(7/20)` 的日期在数字**之后**。

    配对模型默认"最近在前日期"(句首的 7/24),照那样挂就是把 7/20 的移动指控成 7/24 的。
    """
    sent = ("2026-07-24 硬理由=该股 ATR 39.59 = 现价 11.9%,"
            "近 5 个交易日实测单日振幅 +5.65%(7/20)")
    assert _claimed(sent, name="长飞光纤", code6="601869") == [("20260720", 5.65, None)]


def test_unknown_subject_is_counted_not_silently_dropped():
    """定不出主语 → 计量 + 留 snippet,**不告警**也**不静默丢**。"""
    # 列举里的第二、三个值左邻只有 `(7/20)、`,拿不到任何本票股价证据
    sent = ("2026-07-24 该股近 5 日实测单日振幅 +5.65%(7/20)、+6.38%(7/21)、"
            "-9.92%(7/22)")
    audit = classify_price_claims(sent, name="长飞光纤", code6="601869",
                                  year_hint=2026)
    assert audit.n_unknown >= 1
    assert audit.unknown_snippets, "未知主语必须留证据,否则等于静默丢弃"


def test_summary_arithmetic_is_closed():
    text = ("2026-07-21 板块普涨,本股涨 3%。"
            "2026-07-21 本股主力净额占比 +7.5%。"
            "2026-07-21 该股近 5 日振幅 +5.65%(7/20)")
    audit = classify_price_claims(text, name="某某", code6="000001", year_hint=2026)
    s = audit.summary()
    assert s["n_candidate"] == s["n_own"] + s["n_excluded"] + s["n_unknown"]
    assert set(s["subjects"]) <= set(SUBJECTS)


def test_audit_card_text_carries_the_counters():
    res = audit_card_text("2026-07-21 主力净额占比 +7.5%,本股无异动",
                          name="某某", code6="000001", date="2026-07-21",
                          bars_fn=lambda *a, **k: {})
    assert res["n_claims"] == 0 and res["mismatches"] == []
    assert res["n_candidate"] >= 1 and res["n_own"] == 0
    assert "subjects" in res


def test_audit_card_text_bad_date_returns_empty_counters():
    res = audit_card_text("x", name="n", code6="000001", date="",
                          bars_fn=lambda *a, **k: {})
    assert res["n_candidate"] == 0 and res["unknown_rate"] is None


def test_merge_summaries_denominator_is_candidates_not_cards():
    merged = merge_summaries([
        {"subjects": {"own_stock_price": 2, "UNKNOWN_SUBJECT": 1}},
        {"subjects": {"index": 3}},
    ])
    assert merged["n_cards"] == 2 and merged["n_candidate"] == 6
    assert merged["n_own"] == 2 and merged["n_unknown"] == 1 and merged["n_excluded"] == 3
    assert abs(merged["unknown_rate"] - 1 / 6) < 5e-5   # 落盘保留 4 位


def test_merge_summaries_empty_is_graceful():
    merged = merge_summaries([])
    assert merged["n_candidate"] == 0 and merged["unknown_rate"] is None


# ────────────────── unknown-rate 基线 ──────────────────

def test_check_unknown_rate_tolerance():
    base = 0.05
    assert check_unknown_rate(base + UNKNOWN_RATE_TOLERANCE_PP, base)[0]
    assert not check_unknown_rate(base + UNKNOWN_RATE_TOLERANCE_PP + 1e-6, base)[0]


def test_check_unknown_rate_missing_side_is_unmeasured_not_pass():
    ok, note = check_unknown_rate(None, 0.05)
    assert ok and "UNMEASURED" in note      # 不判 ≠ 通过:措辞必须说清
    ok2, note2 = check_unknown_rate(0.9, None)
    assert ok2 and "UNMEASURED" in note2


def test_replay_on_synthetic_scan_root(tmp_path):
    import pandas as pd

    day = tmp_path / "2026-07-21"
    (day / "details").mkdir(parents=True)
    pd.DataFrame([{"code": "000001", "name": "某某"}]).to_csv(
        day / "finalists.csv", index=False)
    (day / "details" / "000001.md").write_text(
        "2026-07-21 板块普涨,本股涨 3%。2026-07-21 本股主力净额占比 +7.5%",
        encoding="utf-8")
    result = replay(tmp_path)
    assert result["days"] == ["2026-07-21"] and result["n_cards"] == 1
    assert result["n_own"] == 1 and result["subjects"].get("ratio_share") == 1
    assert result["per_day"]["2026-07-21"]["n_candidate"] == result["n_candidate"]


def test_replay_empty_root_is_graceful(tmp_path):
    assert replay(tmp_path)["n_candidate"] == 0


@pytest.mark.parametrize("frozen", ["docs/research/2026-08-01-wave10-price-claim-baseline.json"])
def test_frozen_baseline_is_readable_and_sane(frozen):
    from pathlib import Path

    path = Path(frozen)
    if not path.exists():
        pytest.skip("基线快照不在(冻结命令未跑)")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["n_candidate"] == (
        data["n_own"] + data["n_excluded"] + data["n_unknown"])
    assert 0 <= data["unknown_rate"] <= 1
    assert set(data["subjects"]) <= set(SUBJECTS)
