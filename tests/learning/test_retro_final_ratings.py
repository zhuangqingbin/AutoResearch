"""P0-2 attribution 终评级单测(坏账③修复)—— `_buylist` 优先 join `_final_ratings.json`,
缺文件回退卡面解析;契约测试:折回卡的 attribution 评级 == 终评级。零 LLM/无网络。

design: docs/specs/2026-07-12-selflearning-optimization-brainstorm.md §4 P0-2;
plan:   docs/plans/2026-07-12-selflearning-p0-plan.md T2;
STAGES: .claude/skills/scan-market/STAGES.md 开放线头 #6。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from autoresearch.learning import retro


def _mk_report(report_root: Path, folder: str, analysis_date: str, code: str,
               name: str, card_rating: str) -> Path:
    """已发布报告(details/<名称>.md,卡面评级=card_rating,模拟 fold 前的原始判断)。"""
    rdir = report_root / folder
    (rdir / "details").mkdir(parents=True)
    (rdir / "manifest.json").write_text(json.dumps({"analysis_date": analysis_date}), encoding="utf-8")
    (rdir / "details" / f"{name}.md").write_text(
        f"# 决策卡 — {code} {name} @ {analysis_date}\n\n**Rating**: {card_rating}\n"
        f"FINAL TRANSACTION PROPOSAL: **{'BUY' if card_rating in ('Buy', 'Overweight') else 'HOLD'}**\n",
        encoding="utf-8")
    return rdir


# ───────────────────────── _buylist:presence-gated 优先 join ─────────────────────────


def test_buylist_prefers_final_ratings_json_over_card_face(tmp_path):
    """scan_dir 有 _final_ratings.json(终评级 Hold)时,即便已发布卡面仍是 Overweight(折回前
    残留),_buylist 也应返回 Hold —— 这正是坏账③要修的行为。"""
    report_root = tmp_path / "reports/scan"
    scan_dir = tmp_path / "context/scan/2026-07-08"
    scan_dir.mkdir(parents=True)
    _mk_report(report_root, "20260708_2300", "2026-07-08", "300476", "甲", "Overweight")
    (scan_dir / "_final_ratings.json").write_text(json.dumps({"300476": "Hold"}), encoding="utf-8")

    bl = retro._buylist("2026-07-08", report_root=report_root, scan_dir=scan_dir)
    assert bl["300476"] == "Hold", f"应取终评级 Hold,而非卡面 Overweight: {bl}"


def test_buylist_prefers_decision_record_over_legacy_and_card(tmp_path):
    from autoresearch.scan.decision_record import (
        DecisionRecord,
        write_decision_records,
    )

    report_root = tmp_path / "reports/scan"
    scan_dir = tmp_path / "context/scan/2026-07-08"
    scan_dir.mkdir(parents=True)
    _mk_report(
        report_root,
        "20260708_2300",
        "2026-07-08",
        "300476",
        "甲",
        "Overweight",
    )
    (scan_dir / "_final_ratings.json").write_text(
        json.dumps({"300476": "Overweight"}),
        encoding="utf-8",
    )
    record = DecisionRecord.build(
        analysis_date=scan_dir.name,
        contract_hash=None,
        code="300476",
        source_rating="Overweight",
        rubric_rating="Overweight",
        gate_states={},
        early_stop=None,
        ensemble_ratings=["Overweight", "Hold"],
        final_rating="Hold",
        proposal="HOLD",
        reason="ensemble:Hold",
        evidence_refs=[],
        first_rejection_stage="ENSEMBLE",
    )
    write_decision_records(scan_dir, [record])

    assert retro._buylist(
        "2026-07-08",
        report_root=report_root,
        scan_dir=scan_dir,
    ) == {"300476": "Hold"}


def test_buylist_falls_back_to_card_face_when_final_ratings_missing(tmp_path):
    """无 _final_ratings.json(旧日期/该功能上线前)→ 回退卡面解析,现行为不变。"""
    report_root = tmp_path / "reports/scan"
    scan_dir = tmp_path / "context/scan/2026-07-08"
    scan_dir.mkdir(parents=True)
    _mk_report(report_root, "20260708_2300", "2026-07-08", "300476", "甲", "Overweight")

    bl = retro._buylist("2026-07-08", report_root=report_root, scan_dir=scan_dir)
    assert bl["300476"] == "Overweight"


def test_buylist_falls_back_when_final_ratings_json_is_malformed(tmp_path):
    """坏 json → 回退卡面解析,不炸。"""
    report_root = tmp_path / "reports/scan"
    scan_dir = tmp_path / "context/scan/2026-07-08"
    scan_dir.mkdir(parents=True)
    _mk_report(report_root, "20260708_2300", "2026-07-08", "300476", "甲", "Overweight")
    (scan_dir / "_final_ratings.json").write_text("{not valid json", encoding="utf-8")

    bl = retro._buylist("2026-07-08", report_root=report_root, scan_dir=scan_dir)
    assert bl["300476"] == "Overweight"


def test_buylist_falls_back_when_final_ratings_json_empty_dict(tmp_path):
    """空字典(如 finalists 为空的边界日)→ 回退卡面解析,不返回空 buylist 吞掉真实评级。"""
    report_root = tmp_path / "reports/scan"
    scan_dir = tmp_path / "context/scan/2026-07-08"
    scan_dir.mkdir(parents=True)
    _mk_report(report_root, "20260708_2300", "2026-07-08", "300476", "甲", "Overweight")
    (scan_dir / "_final_ratings.json").write_text("{}", encoding="utf-8")

    bl = retro._buylist("2026-07-08", report_root=report_root, scan_dir=scan_dir)
    assert bl["300476"] == "Overweight"


def test_buylist_without_scan_dir_arg_uses_card_face_unchanged(tmp_path):
    """向后兼容:不传 scan_dir(如 stage_eval.py 既有调用点)→ 行为完全不变。"""
    report_root = tmp_path / "reports/scan"
    _mk_report(report_root, "20260708_2300", "2026-07-08", "300476", "甲", "Overweight")
    bl = retro._buylist("2026-07-08", report_root=report_root)
    assert bl["300476"] == "Overweight"


# ───────────────────────── 契约测试:折回卡的 attribution 评级 == 终评级 ─────────────────────────


def test_attribution_rating_equals_final_folded_rating_not_card_face(tmp_path):
    """端到端契约:300476 卡面 Overweight,但 Tier-3/ensemble 已把它折回 Hold(assemble 落
    _final_ratings.json)——attribute_frame 消费 _buylist 的结果后,attribution 的 rating
    必须是 Hold,bought 必须是 False(Hold 不算买单)。"""
    report_root = tmp_path / "reports/scan"
    scan_dir = tmp_path / "context/scan/2026-07-08"
    scan_dir.mkdir(parents=True)
    _mk_report(report_root, "20260708_2300", "2026-07-08", "300476", "甲", "Overweight")
    (scan_dir / "_final_ratings.json").write_text(json.dumps({"300476": "Hold"}), encoding="utf-8")

    bl = retro._buylist("2026-07-08", report_root=report_root, scan_dir=scan_dir)
    l1 = pd.DataFrame({"code": ["300476"], "composite": [80.0]})
    realized = pd.DataFrame({"code": ["300476"], "fwd_1_oo": [0.01], "fwd_2_oc": [0.01],
                             "fwd_5_oc": [0.02], "buyable": [True]})

    attr = retro.attribute_frame(l1, realized, bl)
    row = attr[attr["code"] == "300476"].iloc[0]
    assert row["rating"] == "Hold", f"attribution 评级应=终评级 Hold: {row['rating']}"
    assert not bool(row["bought"]), "Hold 不应被记为买单(bought 应 False)"


def test_attribution_rating_reflects_maintained_ow_when_not_folded(tmp_path):
    """对照:未被折回的票(_final_ratings.json 仍是 Overweight)→ attribution 正常记买单。"""
    report_root = tmp_path / "reports/scan"
    scan_dir = tmp_path / "context/scan/2026-07-08"
    scan_dir.mkdir(parents=True)
    _mk_report(report_root, "20260708_2300", "2026-07-08", "301117", "丁", "Overweight")
    (scan_dir / "_final_ratings.json").write_text(json.dumps({"301117": "Overweight"}), encoding="utf-8")

    bl = retro._buylist("2026-07-08", report_root=report_root, scan_dir=scan_dir)
    l1 = pd.DataFrame({"code": ["301117"], "composite": [80.0]})
    realized = pd.DataFrame({"code": ["301117"], "fwd_1_oo": [0.01], "fwd_2_oc": [0.01],
                             "fwd_5_oc": [0.02], "buyable": [True]})

    attr = retro.attribute_frame(l1, realized, bl)
    row = attr[attr["code"] == "301117"].iloc[0]
    assert row["rating"] == "Overweight"
    assert bool(row["bought"])
