"""L5 整合(assemble)回归 —— 端口自 assemble_scan._selftest()(Plan 4.1),覆盖逐项保留。

一个 module-scoped fixture 造与原 selftest **完全相同**的合成 scan dir(meta/L1/L2/finalists/L4 卡/
中间推理件/verify.csv),跑 `assemble.run(d, run_date=2026-06-21, hhmm=0930)`,各 test 对发布产物断言:
  - 三段 summary(## 1 漏斗 / ## 2 各阶段 / ## 3 投资建议)
  - **逐阶段 buy-list 表**(L1召回/L2粗排/L3论点 列;#5·80 / ·g0.54;已删 代码/R:R/提案)
  - **token 估算段**(## 各阶段 token 消耗 / 确定性·GBDT)
  - Tier-3 多空辩论徽标(🛡️红队 / ⚠️降级 / ✅维持 / 多/空/共识明细)
  - 降级折回(甲 OW→Hold 踢出买单;丁 OW 维持不改)
  - run-folder 与 manifest 解耦(目录名=运行日 20260621_0930;manifest.analysis_date=数据日 d)
  - reasoning 归档(l3/l4/verify)+ 决策卡按名称发布(300476→甲.md)
NO network. 纯确定性。
"""
from __future__ import annotations

import csv
import json

import pytest

from autoresearch.scan import assemble

_DATA_DATE = "2026-06-20"
_RUN_DATE = "2026-06-21"
_HHMM = "0930"
_RUN_FOLDER = "20260621_0930"   # 目录名 = 运行日_HHMM(非数据日)


def _build_scan_dir(root):
    """造与原 assemble_scan._selftest() 等价的 staging scan dir。返回 scan_dir Path。"""
    scan = root / "context/scan" / _DATA_DATE
    (scan / "details").mkdir(parents=True)
    (scan / "meta.json").write_text(json.dumps({
        "universe": 5483, "recall_n": 1000, "l2_n": 200, "l2_engine": "gbdt", "source": "tushare",
        "weights_source": "factor_lab.calibrate"}), encoding="utf-8")
    # L1 召回(概览用)
    with (scan / "L1_recall_top1000.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["code", "name", "industry", "composite"])
        w.writeheader()
        for i in range(20):
            w.writerow({"code": f"{300000 + i:06d}", "name": f"光{i}", "industry": "电子",
                        "composite": 90 - i})
    # L1 全量打分(_l1_cell 查 rank/composite)+ L2 粗排(_l2_cell 查 l2_rank/gbdt)
    l1l2 = [("300476", 5, 80, 2, 0.54), ("600519", 50, 60, 40, 0.49),
            ("002384", 8, 77, 6, 0.52), ("301117", 3, 82, 1, 0.55)]
    with (scan / "L1_scored_full.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["rank", "recalled", "code", "name", "industry", "composite",
                                          "winner_rate", "pct_60d", "rsi6"])
        w.writeheader()
        for code, rk, comp, _lr, _g in l1l2:
            w.writerow({"rank": rk, "recalled": True, "code": code, "name": code, "industry": "电子",
                        "composite": comp, "winner_rate": 60, "pct_60d": 80, "rsi6": 65})
    with (scan / "L2_gbdt_top200.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["l2_rank", "gbdt_score", "code", "name", "industry", "composite"])
        w.writeheader()
        for code, _rk, comp, lr, g in l1l2:
            w.writerow({"l2_rank": lr, "gbdt_score": g, "code": code, "name": code,
                        "industry": "电子", "composite": comp})
    # L3 精排 finalists(带 thesis/risk/catalyst)
    with (scan / "finalists.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ticker", "code", "name", "sector", "lenses", "conviction",
                                          "triage_lean", "triage_reason", "thesis", "risk", "catalyst"])
        w.writeheader()
        w.writerow({"ticker": "300476", "code": "300476", "name": "甲", "sector": "光模块",
                    "lenses": "动量", "conviction": "203", "triage_lean": "看多", "triage_reason": "加速",
                    "thesis": "AI 光模块需求超预期", "risk": "估值高", "catalyst": "Q2 财报"})
        w.writerow({"ticker": "600519", "code": "600519", "name": "乙", "sector": "白酒",
                    "lenses": "价值", "conviction": "125", "triage_lean": "中性", "triage_reason": "低估",
                    "thesis": "现金牛低估", "risk": "需求弱", "catalyst": "中报"})
        w.writerow({"ticker": "002384", "code": "002384", "name": "丙", "sector": "光模块",
                    "lenses": "动量", "conviction": "118", "triage_lean": "回避", "triage_reason": "过热",
                    "thesis": "x", "risk": "y", "catalyst": "z"})
        w.writerow({"ticker": "301117", "code": "301117", "name": "丁", "sector": "光模块",
                    "lenses": "动量", "conviction": "150", "triage_lean": "看多", "triage_reason": "稳健",
                    "thesis": "维持OW对照票", "risk": "无硬伤", "catalyst": "无"})
    # L4 决策卡(002384 故意缺卡 → 测降级;301117 OW+维持 对照不折回)
    for tk, rating, prop, rub in [("300476", "Overweight", "BUY", "Overweight"),
                                  ("600519", "Hold", "HOLD", "Hold"),
                                  ("301117", "Overweight", "BUY", "Overweight")]:
        (scan / "details" / f"{tk}.md").write_text(
            "# 决策卡\n## 决策仪表盘\n| 评级 | 现价 | EV目标 | R:R | 置信度 |\n|---|---|---|---|---|\n"
            f"| **{rating}** | 100元 | 130元(+30%) | 2.1:1 | 中 |\n\n"
            f"**Rubric建议**: {rub}(净分 +2,OW门 3/3)\n\n**Rating**: {rating}\n\n"
            f"FINAL TRANSACTION PROPOSAL: **{prop}**\n", encoding="utf-8")
    # 中间推理件(应归档到 trace/reasoning/{l3,l4};L2 已确定性化,无 LLM 留痕)
    for fn in ("_l3_judged_0.csv", "_l4_prompt.md", "_l4_batch_0.md"):
        (scan / fn).write_text("x", encoding="utf-8")
    # Tier-3 多空辩论:买单 300476 降级(带 bull+consensus)+ 多空中间稿(→ reasoning/verify/)
    (scan / "verify.csv").write_text(
        "code,verdict,bull,bear,trigger,consensus\n"
        '300476,降级,"AI光模块需求真切","估值已透支PE160","跌破120元","降级2/3(估值/资金)"\n'
        '301117,维持,"龙头卡位稀缺","无硬伤","继续持有","维持3/3"\n', encoding="utf-8")
    (scan / "_v_bull_300476.md").write_text("多头研究员稿", encoding="utf-8")
    (scan / "_v_300476.md").write_text("空头研究员稿", encoding="utf-8")
    return scan


@pytest.fixture(scope="module")
def published(tmp_path_factory):
    """跑 assemble.run 一次,返回 (out_base, summary_md, trace_dir)。run_date≠数据日,验证解耦。"""
    root = tmp_path_factory.mktemp("scan_l5")
    scan = _build_scan_dir(root)
    summary_path = assemble.run(_DATA_DATE, scan_dir=scan, out_root=root / "reports/scan",
                                hhmm=_HHMM, run_date=_RUN_DATE)
    out_base = root / "reports/scan" / _RUN_FOLDER
    md = summary_path.read_text(encoding="utf-8")
    return {"summary_path": summary_path, "out_base": out_base, "md": md, "trace": out_base / "trace"}


# ───────────────────────── run-folder / manifest 解耦 ─────────────────────────


def test_run_folder_uses_run_date_not_data_date(published):
    assert published["summary_path"].parent == published["out_base"], \
        f"发布目录应取运行日({_RUN_FOLDER})"


def test_manifest_records_data_date(published):
    mpath = published["out_base"] / "manifest.json"
    assert mpath.exists(), "manifest.json 缺"
    assert json.loads(mpath.read_text(encoding="utf-8")).get("analysis_date") == _DATA_DATE, \
        "manifest.analysis_date 应为数据日"


def test_trace_pipeline_artifacts_published(published):
    pdir = published["trace"]
    for fn in ("L1_recall_top1000.csv", "L2_gbdt_top200.csv", "L3_fine_finalists.csv", "funnel.md",
               "L0_universe_meta.json"):
        assert (pdir / fn).exists(), f"trace 缺 {fn}"


def test_summary_published_to_run_folder(published):
    assert (published["out_base"] / "summary.md").exists(), "summary.md 未发布到 <运行日_HHMM>/"


def test_details_published_by_stock_name(published):
    out_base = published["out_base"]
    assert (out_base / "details" / "甲.md").exists(), "决策卡未按名称发布(details/甲.md)"
    assert not (out_base / "details" / "300476.md").exists(), "发布层不应再用 ticker.md"


def test_reasoning_archived(published):
    rdir = published["trace"] / "reasoning"
    for stage, fn in [("l3", "_l3_judged_0.csv"), ("l4", "_l4_prompt.md"), ("l4", "_l4_batch_0.md"),
                      ("verify", "verify.csv"), ("verify", "_v_300476.md")]:
        assert (rdir / stage / fn).exists(), f"reasoning 归档缺 {stage}/{fn}"


# ───────────────────────── 三段 summary + 逐阶段表 + token 段 + Tier-3 徽标 ─────────────────────────


@pytest.mark.parametrize("token", [
    # 三段标题 + 漏斗计数 + 各段名
    "## 1. 漏斗", "## 2. 各阶段", "## 3. 投资建议", "5483", "1000",
    "选集", "召回", "粗排", "精排", "Overweight", "+30%", "⚠️卡片缺失",
    "AI 光模块需求超预期", "组合视角",
    # 逐阶段结论列(per-stage buy-list 表)
    "L1召回", "L2粗排", "L3论点·确信", "#5·80", "·g0.54",
    # token 估算段
    "## 各阶段 token 消耗", "确定性·GBDT",
    # Tier-3 多空辩论徽标 + 明细
    "🛡️红队", "🛡️ Tier-3 买单多空辩论", "⚠️降级", "✅维持",
    "估值已透支PE160", "AI光模块需求真切", "降级2/3",
])
def test_summary_contains_token(published, token):
    assert token in published["md"], f"summary 缺 '{token}'"


def test_per_stage_table_dropped_code_rr_proposal(published):
    """逐阶段 buy-list 表已删 代码/R:R/提案 列(只留 L1召回/L2粗排/L3论点 等结论列)。"""
    md = published["md"]
    s3 = md.find("## 3. 投资建议")
    header = next((ln for ln in md[s3:].splitlines() if ln.lstrip().startswith("| #")), "")
    assert "L1召回" in header and "L2粗排" in header and "L3论点" in header, f"逐阶段表头缺列: {header}"
    assert "R:R" not in header, f"逐阶段表不应有 R:R 列: {header}"
    assert "提案" not in header, f"逐阶段表不应有 提案 列: {header}"
    assert "代码" not in header, f"逐阶段表不应有 代码 列: {header}"


# ───────────────────────── buy-list 排序 + Tier-3 折回评级 ─────────────────────────


def test_buylist_sorted_by_rating_then_conviction(published):
    """丁(OW维持)< 甲(Hold降级,conv203)< 乙(Hold,conv125)< 丙(缺卡)。"""
    md = published["md"]
    s3 = md.find("## 3. 投资建议")
    ords = [md.find(n, s3) for n in ("丁", "甲", "乙", "丙")]
    assert ords[0] < ords[1] < ords[2] < ords[3], f"buy-list 排序错(应 丁<甲<乙<丙): {ords}"


def test_downgrade_folds_back_rating(published):
    """300476(甲,Tier-3 降级)OW→Hold 踢出买单(按名称定位行,代码列已删)。"""
    md = published["md"]
    row476 = next((ln for ln in md.splitlines() if "甲" in ln and ln.lstrip().startswith("|")), "")
    assert "Overweight" not in row476 and "Hold" in row476, f"降级未折回(甲 应 OW→Hold): {row476}"


def test_maintained_keeps_rating(published):
    """301117(丁,Tier-3 维持)留 OW,不改评级。"""
    md = published["md"]
    row117 = next((ln for ln in md.splitlines() if "丁" in ln and ln.lstrip().startswith("|")), "")
    assert "Overweight" in row117, f"维持不应改评级(丁 应留 OW): {row117}"
