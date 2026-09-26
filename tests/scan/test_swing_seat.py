"""10 日观察席(影子)—— 2026-09-26 daily-engine §5 B3,批 5 Task 3。

判据围绕 Review Focus 写:

④ **空日不许整节消失** —— 没有 非📌 ∩ ≥Hold ∩ 入场≠禁止 的票时 §12 写「无」;生成失败
   写「未生成」—— 读者要分得清「没跑」与「没有」;
⑤ **不是决策** —— 观察席文案不得出现「BUY/买入/可买」(lint 锚);
另:每个数字都有 `_src` 出处(finalists.csv / _final_ratings.json / 卡面入场行 /
stage_rulers.csv),且真能在那个文件里找到同一个值。
"""
from __future__ import annotations

import csv
import json

import pytest

from autoresearch.scan import swing_seat as ss

_FINALISTS = [
    # code, name, lane, conviction, rating, entry line(None = 卡里没写入场行)
    ("600001", "甲", "trend", 60, "Hold", "允许"),
    ("600002", "乙", "reversion", 70, "Overweight", "条件(收复 10EMA)"),
    ("600003", "丙", "pinned", 90, "Hold", "允许"),        # 📌 → 不进席
    ("600004", "丁", "trend", 80, "Underweight", "禁止"),  # < Hold → 不进席
    ("600005", "戊", "growth", 65, "Hold", "禁止"),        # 入场=禁止 → 不进席
    ("600006", "己", "value", 50, "Hold", None),           # 没写入场行 → 进席(≠禁止)
]


def _card(rating: str, entry: str | None) -> str:
    lines = ["# 决策卡", "| 评级 | 现价 | 仓位 | 触发位 |", "|---|---|---|---|",
             f"| {rating} | 10 | — | — |"]
    if entry is not None:
        lines.append(f"**入场**: {entry}")
    lines.append(f"FINAL TRANSACTION PROPOSAL: **{rating.upper()}**")
    return "\n".join(lines) + "\n"


def _scan(tmp_path, rows=_FINALISTS):
    scan = tmp_path / "context_claude" / "scan" / "2026-09-17"
    (scan / "details").mkdir(parents=True)
    with (scan / "finalists.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["code", "name", "sector", "lane", "guard", "conviction"])
        for code, name, lane, conv, _r, _e in rows:
            w.writerow([code, name, "测试行业", lane, "", conv])
    (scan / "_final_ratings.json").write_text(
        json.dumps({code: rating for code, _n, _l, _c, rating, _e in rows}), encoding="utf-8")
    for code, _n, _l, _c, rating, entry in rows:
        (scan / "details" / f"{code}.md").write_text(_card(rating, entry), encoding="utf-8")
    return scan


def _stage_rulers(tmp_path, *, n_days: int, value: float = 0.0042,
                  status: str = "MATURE"):
    path = tmp_path / "views" / "stage_rulers.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "session,stage,metric,value,n_days,n_names,coverage,ci_low,ci_high,status,"
        "metric_definition_version\n"
        f"ALL,L3,{ss.READOUT_METRIC},{value},{n_days},300,0.98,-0.0031,0.0115,{status},"
        "g3.v1+block10\n"
        "ALL,L3,l3_finalist_minus_bench,-0.0003,40,1066,0.98,-0.0025,0.0021,MATURE,g3.v1\n",
        encoding="utf-8")
    return path


# ───────────────────────── 席位人口 ─────────────────────────

def test_seat_is_non_pinned_hold_plus_not_prohibited_sorted_by_conviction(tmp_path):
    seat = ss.build_swing_seat(_scan(tmp_path), stage_rulers_path=tmp_path / "absent.csv")
    assert [r["code"] for r in seat["rows"]] == ["600002", "600001", "600006"]
    assert seat["n"] == 3
    entries = {r["code"]: r["entry"] for r in seat["rows"]}
    assert entries == {"600002": "条件", "600001": "允许", "600006": "未写"}


def test_every_seat_number_is_traceable_to_its_source_file(tmp_path):
    """`_src` 同 brief 边表:逐行 field/value/file/locator,且真能在那个文件里找到同一个值。"""
    scan = _scan(tmp_path)
    seat = ss.build_swing_seat(scan, stage_rulers_path=_stage_rulers(tmp_path, n_days=45))
    ratings = json.loads((scan / "_final_ratings.json").read_text(encoding="utf-8"))
    with (scan / "finalists.csv").open(encoding="utf-8") as fh:
        conv = {r["code"]: r["conviction"] for r in csv.DictReader(fh)}
    by_field = {s["field"]: s for s in seat["_src"]}
    for row in seat["rows"]:
        code = row["code"]
        rating_src = by_field[f"seat.{code}.rating"]
        assert rating_src["file"] == "_final_ratings.json"
        assert rating_src["value"] == ratings[code] == row["rating"]
        conv_src = by_field[f"seat.{code}.conviction"]
        assert conv_src["file"] == "finalists.csv" and conv_src["value"] == conv[code]
        assert by_field[f"seat.{code}.entry"]["file"] == f"details/{code}.md"
    readout_src = by_field["seat.readout"]
    assert readout_src["file"] == "stage_rulers.csv"
    assert ss.READOUT_METRIC in readout_src["locator"]


def test_write_and_load_roundtrip(tmp_path):
    scan = _scan(tmp_path)
    seat = ss.build_swing_seat(scan, stage_rulers_path=tmp_path / "absent.csv")
    path = ss.write_swing_seat(scan, seat)
    assert path == scan / ss.SEAT_FILENAME
    assert ss.load_swing_seat(scan) == seat


# ───────────────────────── ④ 空日 / 未生成 ─────────────────────────

def test_empty_seat_says_none_and_the_section_never_disappears(tmp_path):
    rows = [r for r in _FINALISTS if r[4] != "Hold" or r[2] == "pinned" or r[5] == "禁止"]
    rows = [r for r in rows if r[4] != "Overweight"]
    seat = ss.build_swing_seat(_scan(tmp_path, rows), stage_rulers_path=tmp_path / "absent.csv")
    assert seat["n"] == 0
    text = "\n".join(ss.render_section(seat))
    assert text.startswith(ss.SECTION_TITLE)
    assert "无" in text and "|" not in text                 # 没有表,只有「无」


def test_missing_seat_renders_not_generated_not_none():
    """生成失败(`{}`)≠ 没有票:写「未生成」,绝不写「无」冒充空日。"""
    text = "\n".join(ss.render_section({}))
    assert text.startswith(ss.SECTION_TITLE)
    assert "未生成" in text and "\n无" not in text


# ───────────────────────── 读数行 ─────────────────────────

def test_readout_below_forty_days_says_thin_without_a_number(tmp_path):
    seat = ss.build_swing_seat(_scan(tmp_path), stage_rulers_path=_stage_rulers(tmp_path, n_days=36))
    line = ss.render_section(seat)[-1]
    assert "样本不足" in line and "36" in line and "pp" not in line


def test_readout_at_forty_days_prints_the_pp_value(tmp_path):
    seat = ss.build_swing_seat(_scan(tmp_path), stage_rulers_path=_stage_rulers(tmp_path, n_days=45))
    line = ss.render_section(seat)[-1]
    assert "+0.42pp" in line and "45" in line


def test_readout_absent_stage_rulers_is_named_not_faked(tmp_path):
    seat = ss.build_swing_seat(_scan(tmp_path), stage_rulers_path=tmp_path / "absent.csv")
    assert seat["readout"]["status"] == "ABSENT"
    assert "stage_rulers" in ss.render_section(seat)[-1]


# ───────────────────────── ⑤ 措辞 ─────────────────────────

def test_seat_wording_never_says_buy(tmp_path):
    seat = ss.build_swing_seat(_scan(tmp_path), stage_rulers_path=_stage_rulers(tmp_path, n_days=45))
    for text in ("\n".join(ss.render_section(seat)), ss.brief_text(seat),
                 ss.brief_text(seat, compact=True), ss.brief_text(None)):
        assert ss.banned_words(text) == [], text


def test_banned_words_catches_each_forbidden_phrase():
    assert ss.banned_words("今日可买 3 只") == ["可买"]
    assert ss.banned_words("建议买入") == ["买入"]
    assert ss.banned_words("relative BUY") == ["BUY"]


# ───────────────────────── brief_lint 探针 ─────────────────────────

def _report(tmp_path, summary_seat: str, *, with_pointer: bool = True):
    report = tmp_path / "report"
    report.mkdir()
    brief_md = "# 速读\n**① 市场**:震荡\n" + (
        "**⑦ 10 日观察席(影子)**:1 只 → summary §12\n" if with_pointer else "")
    (report / "brief.md").write_text(brief_md, encoding="utf-8")
    (report / "summary.md").write_text(
        "# A股扫描\n\n" + summary_seat + "\n## 诚实局限\n仅供研究\n", encoding="utf-8")
    return report


def _lint_checks(report):
    from autoresearch.scan.self_review import brief_lint

    return {r["check"]: r for r in brief_lint(report)}


def test_brief_lint_flags_buy_wording_inside_the_seat_section(tmp_path):
    report = _report(tmp_path, f"{ss.SECTION_TITLE}\n| 600001 | 甲 | Hold | 允许 | 60 |\n"
                               "今日可买入 1 只\n")
    checks = _lint_checks(report)
    assert checks["观察席·措辞"]["severity"] == "warn"
    assert "买入" in checks["观察席·措辞"]["detail"]


def test_brief_lint_is_quiet_on_a_clean_seat_section(tmp_path):
    report = _report(tmp_path, f"{ss.SECTION_TITLE}\n无\n")
    checks = _lint_checks(report)
    assert "观察席·措辞" not in checks and "观察席·缺节" not in checks


def test_brief_lint_flags_a_pointer_without_its_section(tmp_path):
    """brief 印了 ⑦ 指针而 summary 没有 §12 —— 读者分不清「没跑」与「没有」。"""
    checks = _lint_checks(_report(tmp_path, "## 行动\n无\n"))
    assert checks["观察席·缺节"]["severity"] == "warn"


@pytest.mark.parametrize("word", ["BUY", "买入", "可买"])
def test_banned_word_list_is_the_lint_single_source(word):
    """lint 与渲染器读同一张禁词表(`swing_seat.BANNED_WORDS`),不各写一份。"""
    assert word in ss.BANNED_WORDS
