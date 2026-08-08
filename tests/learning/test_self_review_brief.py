"""brief 一致性 lint 回归(Wave12 T27 / 批C C3)。

五条(任务书 Step 1):
  ① brief 缺失 = fail
  ② >3,000B = fail
  ③ brief 数字与 `sources` 边表逐项对账 —— **篡改一个评级/基准读数必红**(变异验收)
  ④ brief 与 summary 的 BUY 数 / code / basis / 基准读数不一致 = fail
  ⑤ **仅 active 模式**:成功 run BUY_n<1 = fail;BLOCKED run 不得渲染成成功。
     影子期(mode=shadow)该检查**跳过并注明**——mode 从 `_relative_buy_decision.json` 读。

零网络;所有产物写 tmp_path(`conftest._forbid_production_report_writes` 护栏下必须通过)。
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from autoresearch.learning import self_review
from autoresearch.scan import brief
from autoresearch.scan.decision_record import DecisionRecord, write_decision_records
from autoresearch.scan.report_sections import inject_dashboard

_DATE = "2026-08-06"
_RUN = "20260806_2308"


def _decision(*, mode="shadow", blocked=False, buys=1) -> dict:
    cands = [{"code": "600018", "name": "上港集团", "sector": "航运港口", "pinned": False,
              "eligible": True, "rank": 1, "relative_decision_score": 0.795,
              "faces": {"target_align": 0.77, "recall_strength": 0.77,
                        "evidence": 0.77, "risk_safety": 0.86},
              "faces_missing": [], "research_rating": "Hold",
              "hard_gate": {"tradable": True, "data_a": True, "contract": True,
                            "no_redflag": True},
              "expected_abs_gap": {"value": None, "status": "UNMEASURED", "n": 0}}]
    rows = ([{"code": "600018", "basis": "relative", "rank": 1}]
            if (buys and not blocked) else [])
    return {"schema_version": 1, "rule_version": "e6.v1", "mode": mode, "date": _DATE,
            "ruler": "gap_c1_o2",
            "benchmark": {"market": {"column": "rel_gap_market", "n": 4237},
                          "sector": {"column": "rel_gap_sector", "n_sectors": 129}},
            "counts": {"candidates": 1, "eligible": 1, "excluded_rows": 0,
                       "buys": len(rows)},
            "candidates": cands, "buys": rows,
            "second_buy": {"fired": False, "reason": "v1 影子期无已验证阈值",
                           "threshold": None},
            "blocked": blocked,
            "blocked_reasons": ([{"reason": "hard_gate.no_redflag", "n": 1}]
                                if blocked else []),
            "excluded": []}


def _scan(root: Path, *, decision: dict | None = None) -> Path:
    scan = root / "context" / "scan" / _DATE
    (scan / "details").mkdir(parents=True)
    (scan / "meta.json").write_text(json.dumps({
        "analysis_date": _DATE, "universe_raw": 5496, "universe": 4237,
        "recall_n": 1000, "l2_n": 203, "regime": "range"}), encoding="utf-8")
    with (scan / "finalists.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["code", "name", "sector", "lane"])
        w.writeheader()
        w.writerow({"code": "600018", "name": "上港集团", "sector": "航运港口", "lane": ""})
        w.writerow({"code": "601688", "name": "华泰证券", "sector": "证券Ⅱ", "lane": "pinned"})
    ratings = {"600018": "Hold", "601688": "Hold"}
    (scan / "_final_ratings.json").write_text(json.dumps(ratings, ensure_ascii=False),
                                              encoding="utf-8")
    write_decision_records(scan, [
        DecisionRecord.build(
            analysis_date=_DATE, contract_hash=None, code=c, source_rating=r,
            rubric_rating=r, gate_states={"主力真在": "FAIL"}, early_stop=None,
            ensemble_ratings=[], final_rating=r, proposal="HOLD", reason="synthetic",
            evidence_refs=[], first_rejection_stage=None)
        for c, r in sorted(ratings.items())])
    (scan / "run_mode.json").write_text(json.dumps({"mode": "FORCED_FULL"}),
                                        encoding="utf-8")
    (scan / "run_health.json").write_text(json.dumps({
        "counts": {"l1_full": 4237, "recall": 1000, "l2": 203, "finalists": 2,
                   "cards": 2, "buys": 0},
        "churn": {"prev_date": "2026-08-05", "n_today": 2, "n_repeat": 1},
        "degraded_fields": []}, ensure_ascii=False), encoding="utf-8")
    (scan / brief.DECISION_FILENAME).write_text(
        json.dumps(decision or _decision(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8")
    return scan


def _publish(root: Path, scan: Path) -> Path:
    """生产同姿势:落 brief.md + 把 ①②③④ 注回 summary 的 managed 块。"""
    report = root / "reports" / "scan" / _RUN
    report.mkdir(parents=True, exist_ok=True)
    built = brief.build(scan, run_folder=_RUN)
    brief.write(scan, report, built=built)
    summary = ("# A股扫描 v2 — 2026-08-06\n\n"
               f"{'<!-- SCAN_DASHBOARD_START -->'}\n## 🧭 决策仪表盘\n\n占位\n"
               f"{'<!-- SCAN_DASHBOARD_END -->'}\n\n## 3. 投资建议\n\n## 诚实局限\n")
    (report / "summary.md").write_text(
        inject_dashboard(summary, brief.dashboard_block(built)), encoding="utf-8")
    return report


@pytest.fixture
def published(tmp_path):
    scan = _scan(tmp_path)
    return _publish(tmp_path, scan), scan


def _checks(rows) -> set[str]:
    return {r["check"] for r in rows}


def _fails(rows) -> list[dict]:
    return [r for r in rows if r["severity"] == "fail"]


# ───────────────────────────── 干净盘 ─────────────────────────────

def test_clean_publish_has_no_fail(published):
    rows = self_review.brief_lint(*published)
    assert not _fails(rows), f"干净盘不该 fail:{_fails(rows)}"


def test_shadow_mode_is_explicitly_noted_not_silently_skipped(published):
    """⑤ 影子期跳过 BUY≥1 契约检查,但**必须留痕**——静默跳过 = 死了也像活着(FN-1)。"""
    rows = self_review.brief_lint(*published)
    note = [r for r in rows if r["check"] == "brief·BUY契约(active 期)"]
    assert len(note) == 1
    assert note[0]["severity"] == "info"
    assert "shadow" in note[0]["detail"] and "跳过" in note[0]["detail"]


# ───────────────────────────── ① 缺失 / ② 超预算 ─────────────────────────────

def test_missing_brief_is_fail(published):
    report, scan = published
    (report / brief.BRIEF_FILENAME).unlink()
    rows = self_review.brief_lint(report, scan)
    assert "brief·缺失" in _checks(rows)
    assert _fails(rows)


def test_oversize_brief_is_fail(published):
    report, scan = published
    path = report / brief.BRIEF_FILENAME
    path.write_text(path.read_text(encoding="utf-8") + "填" * 3000, encoding="utf-8")
    rows = self_review.brief_lint(report, scan)
    hit = [r for r in rows if r["check"] == "brief·超预算"]
    assert hit and hit[0]["severity"] == "fail"
    assert "3000" in hit[0]["detail"]


# ───────────────────────────── ③ sources 逐项对账(变异验收) ─────────────────────────────

def test_tampered_rating_in_brief_turns_red(published):
    """**变异验收**:把 brief 里一个评级从 Hold 改成 Buy → 对账必红。"""
    report, scan = published
    path = report / brief.BRIEF_FILENAME
    path.write_text(path.read_text(encoding="utf-8")
                    .replace("华泰证券 601688 **Hold**", "华泰证券 601688 **Buy**"),
                    encoding="utf-8")
    rows = self_review.brief_lint(report, scan)
    hit = [r for r in rows if r["check"] == "brief·数字对账"]
    assert hit, "篡改评级后 sources 对账没有报警 —— 这条 lint 是零鉴别力的"
    assert hit[0]["severity"] == "fail"
    assert "pinned.601688" in " ".join(r["detail"] for r in hit)


def test_tampered_benchmark_reading_turns_red(published):
    report, scan = published
    path = report / brief.BRIEF_FILENAME
    path.write_text(path.read_text(encoding="utf-8").replace("4237", "9999"),
                    encoding="utf-8")
    rows = self_review.brief_lint(report, scan)
    assert "brief·数字对账" in _checks(rows)
    assert _fails(rows)


def test_stale_sources_table_turns_red(published):
    """边表自己过期(输入变了但没重生成)同样必红 —— lint **重算**一次,不只对自己。"""
    report, scan = published
    payload = json.loads((scan / brief.SOURCES_FILENAME).read_text(encoding="utf-8"))
    payload["rows"][0]["value"] = "篡改过的值"
    (scan / brief.SOURCES_FILENAME).write_text(json.dumps(payload, ensure_ascii=False),
                                               encoding="utf-8")
    rows = self_review.brief_lint(report, scan)
    assert "brief·边表过期" in _checks(rows)
    assert _fails(rows)


def test_missing_sources_table_is_fail(published):
    report, scan = published
    (scan / brief.SOURCES_FILENAME).unlink()
    rows = self_review.brief_lint(report, scan)
    assert "brief·边表缺失" in _checks(rows)
    assert _fails(rows)


def test_source_outside_whitelist_is_fail(published):
    report, scan = published
    payload = json.loads((scan / brief.SOURCES_FILENAME).read_text(encoding="utf-8"))
    payload["rows"][0]["file"] = "details/600018.md"
    (scan / brief.SOURCES_FILENAME).write_text(json.dumps(payload, ensure_ascii=False),
                                               encoding="utf-8")
    rows = self_review.brief_lint(report, scan)
    assert "brief·白名单外取数" in _checks(rows)


# ───────────────────────────── ④ brief ↔ summary ─────────────────────────────

def test_summary_not_carrying_dashboard_is_fail(published):
    """注入没跑(summary 还留占位)→ 两层报告说的不是同一件事。"""
    report, scan = published
    summary = report / "summary.md"
    summary.write_text(summary.read_text(encoding="utf-8")
                       .replace("生产 BUY 0 只", "生产 BUY 3 只"), encoding="utf-8")
    rows = self_review.brief_lint(report, scan)
    hit = [r for r in rows if r["check"] == "brief↔summary不一致"]
    assert hit and hit[0]["severity"] == "fail"
    assert "buys.production_n" in hit[0]["detail"]


def test_summary_missing_relative_code_is_fail(published):
    report, scan = published
    summary = report / "summary.md"
    summary.write_text(summary.read_text(encoding="utf-8").replace("600018", "600019"),
                       encoding="utf-8")
    rows = self_review.brief_lint(report, scan)
    assert "brief↔summary不一致" in _checks(rows)


def test_missing_summary_is_fail(published):
    report, scan = published
    (report / "summary.md").unlink()
    rows = self_review.brief_lint(report, scan)
    assert "brief↔summary不一致" in _checks(rows)


# ───────────────────────────── ⑤ active 期 BUY 契约 ─────────────────────────────

def test_active_mode_zero_buy_is_fail(tmp_path):
    scan = _scan(tmp_path, decision=_decision(mode="active", buys=0))
    report = _publish(tmp_path, scan)
    rows = self_review.brief_lint(report, scan)
    hit = [r for r in rows if r["check"] == "brief·BUY契约(active 期)"]
    assert hit and hit[0]["severity"] == "fail"
    assert "BUY_n" in hit[0]["detail"]


def test_active_mode_blocked_must_not_read_as_success(tmp_path):
    scan = _scan(tmp_path, decision=_decision(mode="active", blocked=True))
    report = _publish(tmp_path, scan)
    path = report / brief.BRIEF_FILENAME
    path.write_text(path.read_text(encoding="utf-8").replace("BLOCKED", "一切正常"),
                    encoding="utf-8")
    rows = self_review.brief_lint(report, scan)
    hit = [r for r in rows if r["check"] == "brief·BUY契约(active 期)"]
    assert hit and any(r["severity"] == "fail" for r in hit)
    assert any("BLOCKED" in r["detail"] for r in hit)


def test_active_mode_with_one_buy_is_clean(tmp_path):
    scan = _scan(tmp_path, decision=_decision(mode="active", buys=1))
    report = _publish(tmp_path, scan)
    rows = self_review.brief_lint(report, scan)
    assert not _fails(rows), f"active + 1 只 BUY 不该 fail:{_fails(rows)}"


def test_shadow_zero_buy_is_not_fail(tmp_path):
    """影子期 BLOCKED / 0 BUY **不是** fail —— 那条契约只在 active 生效(任务书 §4)。"""
    scan = _scan(tmp_path, decision=_decision(mode="shadow", blocked=True))
    report = _publish(tmp_path, scan)
    rows = self_review.brief_lint(report, scan)
    assert not _fails(rows), f"影子期不该因 0 BUY 变红:{_fails(rows)}"


# ───────────────────────────── 容错 ─────────────────────────────

def test_lint_is_wired_into_publisher(tmp_path, capsys):
    """接线真身断言(FN-1:生产者没接线 = 死码)。走 `assemble.run`,lint 结果必须
    进 `gate_fires.csv`(与 R3 门审计同一本账)并打给 CP7。"""
    from autoresearch.scan import assemble
    scan = _scan(tmp_path)
    assemble.run(_DATE, scan_dir=scan, out_root=tmp_path / "reports" / "scan",
                 hhmm="2308", run_date="2026-08-06")
    assert "[brief lint]" in capsys.readouterr().out, "CP7 拿不到 lint 读数"
    fired = list(csv.DictReader((scan / "gate_fires.csv").open(encoding="utf-8")))
    assert any(str(r.get("check", "")).startswith("brief·") for r in fired), \
        "lint 条目没进 gate_fires.csv —— 跑了等于没跑"


def test_never_raises_on_garbage(tmp_path):
    """lint 是发布后自检,坏输入只报条目、绝不抛(与本文件其余 lint 同惯例)。"""
    empty = tmp_path / "nothing"
    empty.mkdir()
    rows = self_review.brief_lint(empty, empty)
    assert isinstance(rows, list)
    assert "brief·缺失" in _checks(rows)
