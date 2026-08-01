"""复核分歧的结构化事实(Wave10 A1):持仓保护规则可见 / 普通票 spread=1 静默。合成,无网络。

立案现场:2026-07-31 的 920179 —— `[Underweight,Sell,Sell]`、median=Sell、trigger=sell_review、
spread=1。单向阀按设计不把持仓评级改得更悲观(那一步是对的),但**报告里一个字都没有**。
本文件锁的是"这件事有名字、有记录、有一行字",不是改阀门(§7 非目标)。
"""
from __future__ import annotations

import json

from autoresearch.scan.decision_finalize import (
    DISSENT_HUMAN_REVIEW,
    DISSENT_PINNED_SELL_PROTECTION,
    _apply_ensemble_fold,
    _ensemble_dissent_lines,
    build_dissent_records,
    dissent_line,
    dump_dissent_records,
    load_dissent_records,
)


def _row(code="920179", *, lane="pinned", card="Underweight", final=None):
    return {"code": code, "lane": lane, "_source_rating": card,
            "rating": final if final is not None else card}


def _ens(median="Sell", *, trigger="sell_review", spread=1,
         ratings=("Underweight", "Sell", "Sell"), degraded=False):
    return {"median": median, "trigger": trigger, "spread": spread,
            "ratings": list(ratings), "degraded": degraded, "n_runs": len(ratings)}


# ────────────────── 阀门本身不动(§7 非目标) ──────────────────

def test_one_way_valve_behaviour_is_unchanged():
    """sell_review 仍然只向温和折 —— A1 不碰它,只让它的沉默变可见。"""
    assert _apply_ensemble_fold("Underweight", _ens("Sell")) == "Underweight"
    assert _apply_ensemble_fold("Sell", _ens("Underweight")) == "Underweight"
    # 默认 ow_review 仍只向下折
    assert _apply_ensemble_fold("Overweight", _ens("Hold", trigger="ow_review")) == "Hold"


# ────────────────── 持仓保护规则:出行 ──────────────────

def test_pinned_sell_review_dissent_is_recorded():
    records = build_dissent_records([_row()], {"920179": _ens()})
    assert len(records) == 1
    rec = records[0]
    assert rec.kind == DISSENT_PINNED_SELL_PROTECTION
    assert (rec.card_rating, rec.median_rating, rec.final_rating) == (
        "Underweight", "Sell", "Underweight")
    assert rec.lane == "pinned" and rec.trigger == "sell_review"


def test_pinned_dissent_line_wording():
    """措辞:说这是**持仓保护规则**的有意行为,不写「未折回」那种像 bug 的表达。"""
    line = dissent_line(build_dissent_records([_row()], {"920179": _ens()})[0])
    assert line.startswith("⚠️ 持仓保护规则")
    assert "复核中位 Sell" in line and "卡面 UW" in line
    assert "更悲观" in line and "单向阀未加重终评" in line
    assert "未折回" not in line and "bug" not in line.lower()


def test_line_reads_the_three_ratings_from_the_record_not_from_emap():
    """三个评级字段都来自结构化记录 —— 渲染层不许自己再推一遍(否则两处会分叉)。"""
    from autoresearch.scan.decision_finalize import DissentRecord

    rec = DissentRecord(
        schema_version=1, code="000001", lane="pinned", trigger="sell_review",
        card_rating="Hold", median_rating="Underweight", final_rating="Hold",
        ratings=("Hold", "Underweight", "Underweight"), spread=1, degraded=False,
        kind=DISSENT_PINNED_SELL_PROTECTION)
    line = dissent_line(rec)
    assert "复核中位 UW" in line and "卡面 Hold" in line and "终评 Hold" in line


# ────────────────── 不该出行的情形:静默 ──────────────────

def test_non_pinned_spread_one_stays_silent():
    """普通票的 1 档分歧照旧不出人裁行 —— A1 不是把噪声调大。"""
    assert build_dissent_records(
        [_row("300857", lane="")], {"300857": _ens()}) == []


def test_pinned_sell_review_without_divergence_stays_silent():
    """median == 卡面 → 没有分歧可言(07-31 的 300857 就是这种)。"""
    assert build_dissent_records(
        [_row("300857", card="Underweight")],
        {"300857": _ens(median="Underweight", spread=0,
                        ratings=("Underweight",) * 3)}) == []


def test_stock_without_ensemble_record_is_skipped():
    assert build_dissent_records([_row()], {}) == []


def test_unknown_rating_words_do_not_produce_a_line():
    """脏数据(不在五档词表)→ 不出行,也不抛 —— 与折回函数同款容忍。"""
    assert build_dissent_records(
        [_row(card="???")], {"920179": _ens(median="Sell")}) == []


# ────────────────── 人裁分歧(spread≥2)仍然照旧 ──────────────────

def test_human_review_dissent_still_produced_for_spread_two():
    records = build_dissent_records(
        [_row("000001", lane="")],
        {"000001": _ens(median="Hold", trigger="ow_review", spread=2,
                        ratings=("Overweight", "Hold", "Underweight"))})
    assert len(records) == 1 and records[0].kind == DISSENT_HUMAN_REVIEW
    assert dissent_line(records[0]).startswith("🎭 买单复核分歧")


def test_degraded_with_spread_is_human_review():
    records = build_dissent_records(
        [_row("000001", lane="")],
        {"000001": _ens(spread=1, degraded=True)})
    assert records and records[0].kind == DISSENT_HUMAN_REVIEW


# ────────────────── 老调用点不破 ──────────────────

def test_dissent_lines_without_rows_matches_the_pre_a1_output():
    """不传 `rows` → 逐字保持 A1 之前的行为(assemble 等老调用点)。"""
    emap = {"000001": _ens(median="Hold", trigger="ow_review", spread=2,
                           ratings=("Overweight", "Hold", "Underweight"))}
    legacy = _ensemble_dissent_lines(emap)
    assert legacy == ["🎭 买单复核分歧:000001 3 run="
                      "['Overweight', 'Hold', 'Underweight'],已按中位折回,建议人工复核"]
    # 持仓分歧在不传 rows 时看不到(它需要 lane/卡面评级)
    assert _ensemble_dissent_lines({"920179": _ens()}) == []


def test_dissent_lines_with_rows_adds_the_pinned_line():
    lines = _ensemble_dissent_lines({"920179": _ens()}, [_row()])
    assert len(lines) == 1 and lines[0].startswith("⚠️ 持仓保护规则")


# ────────────────── 落盘 / 回读 ──────────────────

def test_records_roundtrip_through_disk(tmp_path):
    records = build_dissent_records([_row()], {"920179": _ens()})
    dump_dissent_records(tmp_path, records)
    payload = json.loads((tmp_path / "dissent_records.json").read_text(encoding="utf-8"))
    assert payload[0]["kind"] == DISSENT_PINNED_SELL_PROTECTION
    loaded = load_dissent_records(tmp_path)
    assert loaded["920179"]["median_rating"] == "Sell"


def test_load_is_presence_gated(tmp_path):
    assert load_dissent_records(tmp_path) == {}
    (tmp_path / "dissent_records.json").write_text("{ broken", encoding="utf-8")
    assert load_dissent_records(tmp_path) == {}


def test_publisher_renders_the_head_line_from_the_record(tmp_path):
    from autoresearch.scan.publisher import _dissent_head

    records = build_dissent_records([_row()], {"920179": _ens()})
    dump_dissent_records(tmp_path, records)
    head = _dissent_head(load_dissent_records(tmp_path)["920179"])
    assert head == dissent_line(records[0])


def test_publisher_is_wired_to_read_the_records():
    """接线守卫:publisher 必须读结构化记录 —— 只写了模块没接线 = 卡头仍然零痕迹。"""
    from pathlib import Path

    src = Path("autoresearch/scan/publisher.py").read_text(encoding="utf-8")
    assert "load_dissent_records" in src and "_dissent_head(rec)" in src


def test_report_sections_is_wired_to_dump_the_records():
    from pathlib import Path

    src = Path("autoresearch/scan/report_sections.py").read_text(encoding="utf-8")
    assert "dump_dissent_records(scan_dir, build_dissent_records(rows, emap))" in src
    assert "_ensemble_dissent_lines(emap, rows)" in src
