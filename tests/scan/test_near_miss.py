"""near-miss 报告出口(Wave10 C3):聚合先行 / 前视隔离 / UNMEASURED 不静默。合成,无网络。

这一节最容易变成"绕门冲动放大器",所以测试重点不是"能显示",而是三条约束:
  · 主报告只给聚合,个股只在附录且带固定免责(§R6);
  · 历史战绩**只认报告日之前已成熟**的样本 —— 注入未来记录必须变红(§C3-3);
  · 拿不到就写 `UNMEASURED`,不静默消失(静默会被读成"历史上没错杀过")。
"""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.scan.near_miss import (
    DISCLAIMER,
    MATURITY_SCAN_DAYS,
    UNMEASURED,
    NearMissFacts,
    abstention_line,
    appendix_lines,
    build,
    mature_before,
    section,
    summary_line,
)

DAYS = ["2026-07-13", "2026-07-14", "2026-07-15", "2026-07-16", "2026-07-17"]


# ────────────────────────── 前视隔离 ──────────────────────────

def test_mature_before_needs_two_scan_days_of_gap():
    assert not mature_before("2026-07-16", "2026-07-17", DAYS)   # 只隔 0 天
    assert not mature_before("2026-07-14", "2026-07-16", DAYS)   # 只隔 1 天(07-15)
    assert mature_before("2026-07-13", "2026-07-16", DAYS)       # 隔 07-14/07-15
    assert MATURITY_SCAN_DAYS == 2


def test_signal_on_or_after_report_date_is_never_mature():
    assert not mature_before("2026-07-17", "2026-07-17", DAYS)
    assert not mature_before("2026-07-18", "2026-07-17", DAYS)   # 未来信号日


def test_missing_calendar_degrades_to_not_mature():
    """日历不全 → 判"未成熟"(少展示),不判"已成熟"(会从未来读数)。"""
    assert not mature_before("2026-07-13", "2026-07-16", [])


# ────────────────────────── 聚合行 ──────────────────────────

def _facts(**kw):
    base = {
        "date": "2026-07-31", "is_zero_buy": True,
        "shadow": [{"code": "000921", "name": "海信家电", "conviction": 74,
                    "binding": ["业绩真兑现"], "close": 27.39}],
        "gate_counts": {"主力真在": 1, "业绩真兑现": 1, "估值不透支": 0},
        "gate_history": {
            "主力真在": {"status": "OK", "false_kill": 20, "measured": 65,
                       "as_of": "2026-07-28"},
            "业绩真兑现": {"status": UNMEASURED, "reason": "报告日前无已成熟样本"},
            "估值不透支": {"status": "OK", "false_kill": 8, "measured": 36,
                       "as_of": "2026-07-28"},
        },
    }
    base.update(kw)
    return NearMissFacts(**base)


def test_summary_line_gives_gate_counts_and_a_denominator():
    line = summary_line(_facts())
    assert line.startswith("🎯 差一点")
    assert "主力真在 1" in line and "估值不透支 0" in line
    assert "FALSE_KILL 20/65" in line          # 分子分母同屏(§R5)
    assert "截至 2026-07-28" in line            # as-of 同屏


def test_unmeasured_history_is_shown_not_dropped():
    """拿不到历史战绩 → 明写 UNMEASURED。静默省略会被读成"这门从没错杀过"。"""
    assert f"业绩真兑现 {UNMEASURED}" in summary_line(_facts())


def test_summary_line_is_empty_when_not_zero_buy():
    assert summary_line(_facts(is_zero_buy=False)) == ""


def test_summary_line_is_empty_without_shadow_candidates():
    assert summary_line(_facts(shadow=[])) == ""


def test_summary_says_so_when_no_gate_was_the_blocker():
    line = summary_line(_facts(gate_counts=dict.fromkeys(
        ("主力真在", "业绩真兑现", "估值不透支"), 0)))
    assert "均非 OW 三门拦下" in line


# ────────────────────────── 附录:个案与免责 ──────────────────────────

def test_appendix_carries_the_fixed_disclaimer():
    lines = appendix_lines(_facts())
    body = "\n".join(lines)
    assert DISCLAIMER in body
    assert "未过门" in body and "输给" in body       # 固定标注,不许被改软


def test_appendix_is_its_own_section_not_a_buy_list():
    """§R6:near-miss 不得与正式 buy-list 排成同视觉层级。"""
    lines = appendix_lines(_facts())
    assert lines[0].startswith("### ")              # 独立小节
    assert "买入" not in lines[0] and "buy" not in lines[0].lower()


def test_appendix_empty_when_not_zero_buy():
    assert appendix_lines(_facts(is_zero_buy=False)) == []


def test_section_puts_aggregate_before_appendix():
    body = section(_facts())
    assert body.index(next(x for x in body if "🎯 差一点" in x)) < \
        body.index(next(x for x in body if "影子观察附录" in x))


# ────────────────────────── 弃权 banner ──────────────────────────

def test_abstention_banner_shows_signal_date_not_a_relative_word():
    facts = _facts(abstention={
        "window_n": 8, "as_of": "2026-07-29",
        "counts": {"CORRECT": 0, "FALSE": 3, "NEUTRAL": 5, "IMMATURE": 0},
        "latest_false": {"signal_date": "2026-07-21", "codes": ["600188"],
                         "excess_2": 0.0564}})
    line = abstention_line(facts)
    assert "信号日 2026-07-21" in line and "600188" in line
    assert "+5.6pp" in line
    assert "FALSE 3/8" in line and "NEUTRAL 5/8" in line and "CORRECT 0/8" in line
    # §C3-4:不写死"次日/昨日"——成熟可能跨休市日
    assert "次日" not in line and "昨日" not in line


def test_abstention_banner_without_false_still_shows_the_denominator():
    """§C3-5:CORRECT/NEUTRAL 不逐案刷屏,但分母始终同屏。"""
    line = abstention_line(_facts(abstention={
        "window_n": 5, "as_of": "2026-07-29",
        "counts": {"CORRECT": 1, "FALSE": 0, "NEUTRAL": 4, "IMMATURE": 0}}))
    assert line.startswith("✅") and "FALSE 0/5" in line


def test_abstention_banner_marks_missing_excess_as_unmeasured():
    line = abstention_line(_facts(abstention={
        "window_n": 3, "as_of": "2026-07-29",
        "counts": {"CORRECT": 0, "FALSE": 1, "NEUTRAL": 2, "IMMATURE": 0},
        "latest_false": {"signal_date": "2026-07-21", "codes": ["600188"],
                         "excess_2": None}}))
    assert UNMEASURED in line


def test_abstention_banner_absent_without_data():
    assert abstention_line(_facts(abstention=None)) == ""


# ────────────────────────── 端到端:合成 scan root ──────────────────────────

def _mk_scan(root, date, *, buys=0, shadow=(), gates=None):
    day = root / date
    (day / "details").mkdir(parents=True, exist_ok=True)
    (day / "retro").mkdir(parents=True, exist_ok=True)
    import json
    (day / "_final_ratings.json").write_text(
        json.dumps({f"00000{i}": ("Overweight" if i < buys else "Hold")
                    for i in range(3)}), encoding="utf-8")
    for code in shadow:
        failed = (gates or {}).get(code, [])
        marks = " · ".join(
            f"{g} {'**✗**' if g in failed else '✓'}"
            for g in ("主力真在", "业绩真兑现", "估值不透支"))
        (day / "details" / f"{code}.md").write_text(
            f"# {code}\n\nOW三门 <{marks}> → **建议 Hold**\n", encoding="utf-8")
    return day


def test_end_to_end_zero_buy_day(tmp_path):
    day = _mk_scan(tmp_path, "2026-07-31", buys=0, shadow=("000921", "600285"),
                   gates={"000921": ["业绩真兑现"], "600285": ["主力真在"]})
    shadow_csv = tmp_path / "shadow.csv"
    pd.DataFrame([
        {"date": "2026-07-31", "code": "000921", "name": "海信家电",
         "conviction": 74, "binding": "", "close": 27.39},
        {"date": "2026-07-31", "code": "600285", "name": "羚锐制药",
         "conviction": 71, "binding": "", "close": 23.0},
    ]).to_csv(shadow_csv, index=False)

    facts = build(day, shadow_path=shadow_csv)
    assert facts.is_zero_buy and len(facts.shadow) == 2
    # binding 列在盘上是空的(历史产物),必须从卡面现算出来
    assert facts.gate_counts == {"主力真在": 1, "业绩真兑现": 1, "估值不透支": 0}


def test_end_to_end_non_zero_buy_day_has_no_near_miss(tmp_path):
    day = _mk_scan(tmp_path, "2026-07-31", buys=2, shadow=("000921",))
    shadow_csv = tmp_path / "shadow.csv"
    pd.DataFrame([{"date": "2026-07-31", "code": "000921", "name": "x",
                   "conviction": 70, "binding": "", "close": 1.0}]).to_csv(
        shadow_csv, index=False)
    facts = build(day, shadow_path=shadow_csv)
    assert not facts.is_zero_buy
    assert summary_line(facts) == "" and appendix_lines(facts) == []


def test_missing_shadow_file_is_graceful(tmp_path):
    day = _mk_scan(tmp_path, "2026-07-31")
    facts = build(day, shadow_path=tmp_path / "nope.csv")
    assert facts.shadow == [] and section(facts) == []


def test_gate_history_is_unmeasured_on_a_fresh_root(tmp_path):
    day = _mk_scan(tmp_path, "2026-07-31")
    facts = build(day, shadow_path=tmp_path / "nope.csv")
    assert all(v["status"] == UNMEASURED for v in facts.gate_history.values())


@pytest.mark.parametrize("report_date,expect_mature", [
    ("2026-07-16", False),      # 信号日 07-15 到报告日只隔 0 个扫描日
    ("2026-07-31", True),
])
def test_history_window_moves_with_the_report_date(report_date, expect_mature):
    """同一份 ledger,报告日不同 → 可用样本必须不同(否则就是从未来读数)。"""
    assert mature_before("2026-07-15", report_date,
                         [*DAYS, "2026-07-21", "2026-07-24", "2026-07-31"]) is expect_mature
