"""持仓卡 lint(2026-10-03 B9,只观测):📌 评级翻转却没有引用新事实 → warn。

📌 持仓每天满卡重评,连续两场评级翻转率 38%(复盘稿 §3.8)。有新事实就该翻;没有新事实的
翻转是判断噪声。这里只标出来,不改评级、不改决策(07-29「不要任何复用」裁定不动,Q7 待裁)。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoresearch.scan import self_review

PREV = ("2026-09-28", {"688981": "Underweight", "300750": "Hold", "600000": "Hold"})


def _scan(tmp_path: Path, ratings: dict, cards: dict, pinned=("688981", "300750")) -> Path:
    scan = tmp_path / "2026-09-29"
    (scan / "details").mkdir(parents=True)
    rows = ["code,name,lane"] + [f"{c},名{c},{'pinned' if c in pinned else 'healthy'}"
                                 for c in ratings]
    (scan / "finalists.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (scan / "_final_ratings.json").write_text(json.dumps(ratings), encoding="utf-8")
    for code, body in cards.items():
        (scan / "details" / f"{code}.md").write_text(body, encoding="utf-8")
    return scan


def _card(change: str) -> str:
    return f"# 卡\n\n**变化项(vs 昨卡 2026-09-28 UW)**:{change}\n\n**Rating**: Hold\n"


def test_a_flip_without_any_new_fact_is_flagged(tmp_path):
    scan = _scan(tmp_path, {"688981": "Hold", "300750": "Hold"},
                 {"688981": _card("基本面无变化,情绪回暖。"), "300750": _card("同昨卡。")})

    got = self_review.pinned_flip_lint(scan, previous=PREV)

    assert [(r["check"], r["severity"], r["code"]) for r in got] == [
        ("持仓卡·评级翻转无新事实", "warn", "688981")]
    assert "Underweight → Hold" in got[0]["detail"] and "2026-09-28" in got[0]["detail"]


@pytest.mark.parametrize("change", [
    "① 09-29 收 117.51,昨卡价格线未触发",          # 当日日期(MM-DD)
    "公司 2026-09-29 晚间公告回购完成",             # 完整日期
    "9月29日 互动易确认订单",                       # 中文日期
    "24h 外盘负向(intel)",                          # 当日情报引用
    "①今日 -1.10% 收 119.18,第 4 根连阴",           # 真实前科(09-10 中芯国际):无日期串的当日事实
    "②本卡首次完成 P4 深核,读到归母利润仅占 40%",   # 第一次读到的证据也是新事实
])
def test_a_flip_citing_a_fact_from_after_the_previous_run_passes(tmp_path, change):
    scan = _scan(tmp_path, {"688981": "Hold", "300750": "Hold"},
                 {"688981": _card(change), "300750": _card("同昨卡。")})
    assert self_review.pinned_flip_lint(scan, previous=PREV) == []


@pytest.mark.parametrize("change", [
    "沿用昨卡 2026-09-28 的判断",                  # 上一场当天的日期不是新事实
    "T+2=10/8 节后首日开盘离场",                    # 计划里的未来日期不是已发生的事实
])
def test_dates_that_are_not_new_facts_do_not_count(tmp_path, change):
    scan = _scan(tmp_path, {"688981": "Hold", "300750": "Hold"},
                 {"688981": _card(change), "300750": _card("同昨卡。")})
    assert [r["code"] for r in self_review.pinned_flip_lint(scan, previous=PREV)] == ["688981"]


def test_a_flip_with_no_change_section_at_all_is_flagged(tmp_path):
    scan = _scan(tmp_path, {"688981": "Hold", "300750": "Hold"},
                 {"688981": "# 卡\n\n09-29 收 117.51\n\n**Rating**: Hold\n",
                  "300750": _card("同昨卡。")})
    got = self_review.pinned_flip_lint(scan, previous=PREV)
    assert [r["code"] for r in got] == ["688981"] and "变化项" in got[0]["detail"]


def test_only_pinned_cards_with_a_previous_rating_are_checked(tmp_path):
    scan = _scan(tmp_path, {"688981": "Underweight", "300750": "Hold", "600000": "Sell",
                            "601000": "Hold"},
                 {c: _card("无") for c in ("688981", "300750", "600000", "601000")})
    # 688981/300750 没翻;600000 翻了但不是 📌;601000 上一场没有评级
    assert self_review.pinned_flip_lint(scan, previous=PREV) == []


def test_no_previous_run_means_nothing_to_compare(tmp_path):
    scan = _scan(tmp_path, {"688981": "Hold"}, {"688981": _card("无")})
    assert self_review.pinned_flip_lint(scan, previous=(None, {})) == []


def test_the_lint_runs_inside_the_review_extras(tmp_path, monkeypatch):
    from autoresearch.scan import report_sections

    scan = _scan(tmp_path, {"688981": "Hold"}, {"688981": _card("无")})
    monkeypatch.setattr(self_review, "_previous_pinned_ratings", lambda _scan: PREV)

    checks = {row["check"] for row in report_sections._review_extras(scan)}

    assert "持仓卡·评级翻转无新事实" in checks


def test_the_heading_form_of_the_change_section_is_read_too(tmp_path):
    """真实前科(09-17 中芯国际卡):增量写在 `## vs 昨卡(…)增量证据` 标题节里,不是粗体标签行。"""
    card = ("# 卡\n\n## vs 昨卡(2026-09-28 UW)增量证据\n"
            "- 价格:09-28 收 114.86 → 09-29 收 120.90(+5.26%)\n\n## 持仓管理\n无\n")
    scan = _scan(tmp_path, {"688981": "Hold", "300750": "Hold"},
                 {"688981": card, "300750": _card("同昨卡。")})
    assert self_review.pinned_flip_lint(scan, previous=PREV) == []
    stale = card.replace("09-29 收 120.90(+5.26%)", "无新信息")
    (scan / "details" / "688981.md").write_text(stale, encoding="utf-8")
    assert [r["code"] for r in self_review.pinned_flip_lint(scan, previous=PREV)] == ["688981"]


def test_the_echo_reconciliation_heading_counts_as_a_change_section(tmp_path):
    """真实前科(09-15 中芯国际卡):`## 昨卡回声对账(vs 2026-09-11 Underweight → 今 Hold)`。"""
    card = ("# 卡\n\n## 昨卡回声对账(vs 2026-09-28 Underweight → 今 Hold)\n"
            "- 09-29 收 114.07 跌破布林下轨\n\n## 持仓管理\n无\n")
    scan = _scan(tmp_path, {"688981": "Hold", "300750": "Hold"},
                 {"688981": card, "300750": _card("同昨卡。")})
    assert self_review.pinned_flip_lint(scan, previous=PREV) == []
