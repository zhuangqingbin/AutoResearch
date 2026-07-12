"""L5 整合(assemble)回归 —— 端口自 assemble_scan._selftest()(Plan 4.1),覆盖逐项保留。

一个 module-scoped fixture 造与原 selftest **完全相同**的合成 scan dir(meta/L1/L2/finalists/L4 卡/
中间推理件/verify.csv),跑 `assemble.run(d, run_date=2026-06-21, hhmm=0930)`,各 test 对发布产物断言:
  - 三段 summary(## 1 漏斗 / ## 2 各阶段 / ## 3 投资建议)
  - **逐阶段 buy-list 表**(L1召回/L2粗排/L3精排/L4研究 列;名次带分母 #5 + 列头 #/N + 列注;已删 代码/R:R/提案)
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
from pathlib import Path

import pandas as pd
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
    _ch = {"300476": "growth|heat|momentum", "600519": "composite|value",
           "002384": "momentum", "301117": "composite|growth"}   # 命中队列(随 keep 流到 L2 表)
    with (scan / "L2_gbdt_top200.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["l2_rank", "gbdt_score", "code", "name", "industry",
                                          "composite", "recall_channels"])
        w.writeheader()
        for code, _rk, comp, lr, g in l1l2:
            w.writerow({"l2_rank": lr, "gbdt_score": g, "code": code, "name": code,
                        "industry": "电子", "composite": comp, "recall_channels": _ch.get(code, "")})
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
    "L1召回(#/", "L2粗排(#/", "L3精排", "L4研究·结论", "#5", "列注",
    # token 估算段
    "## 各阶段耗时 & token 消耗", "落盘可测下界", "effort", "墙钟",   # 07-06:加 effort/墙钟列(耗时来自 _stage_timing.json)
    # Tier-3 多空辩论徽标 + 明细
    "🛡️红队", "🛡️ Tier-3 买单多空辩论", "⚠️降级", "✅维持",
    "估值已透支PE160", "AI光模块需求真切", "降级2/3",
])
def test_summary_contains_token(published, token):
    assert token in published["md"], f"summary 缺 '{token}'"


def test_token_table_intel_row(tmp_path):
    """token 估算表新增『L4 输入·情报』行(l4-intel 盲搜落稿计数;镜像『L4 输入·slim』行元组形状)。"""
    (tmp_path / "_l4_intel_000001.md").write_text("# 活体情报\n", encoding="utf-8")
    md = "\n".join(assemble._stage_token_estimate(tmp_path))
    assert "L4 输入·情报" in md


def test_per_stage_table_dropped_code_rr_proposal(published):
    """逐阶段 buy-list 表已删 代码/R:R/提案 列(只留 L1召回/L2粗排/L3精排/L4研究 等结论列)。"""
    md = published["md"]
    s3 = md.find("## 3. 投资建议")
    header = next((ln for ln in md[s3:].splitlines() if ln.lstrip().startswith("| #")), "")
    assert all(c in header for c in ("L1召回", "L2粗排", "L3精排", "L4研究")), f"逐阶段表头缺列: {header}"
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


def test_buylist_l1l2_cells_queue_and_score(published):
    """L1召回:#名次/N(列头)+ 命中队列(中文);L2粗排:#名次/N + gbdt 分。"""
    md = published["md"]
    s3 = md.find("## 3. 投资建议")
    header = next((ln for ln in md[s3:].splitlines() if ln.lstrip().startswith("| #")), "")
    assert "L1召回(#/" in header and "L2粗排(#/" in header, f"列头应带分母(#/N): {header}"
    row = next((ln for ln in md.splitlines() if "甲" in ln and ln.lstrip().startswith("|")), "")
    assert "#5" in row, f"甲 L1 名次应显示 #5: {row}"
    assert "成长" in row, f"L1召回应显示命中队列(growth→成长): {row}"
    assert "g0.5" in row, f"L2粗排应带 gbdt 分(0.54→g0.54): {row}"
    assert "·80" not in row, f"L1 不应再有裸 composite: {row}"
    assert "列注" in md, "应有列注解释名次/队列含义"


# ───────────────────────── 呈现层瘦身(2026-07-04) ─────────────────────────


def test_l4_brief_not_over_truncated():
    """L4 结论列 48→96:0买日『为何没买』是全部信息量,别腰斩。"""
    bear = "PB16极端加近4季连miss下共识未证·主力派发占比假象·破多头排列·震荡无垫·估值透支是核心杀点·中报八月下旬前无近端催化"
    text = f"**一行多空**: 多 xxx ｜ 空 {bear}\n"
    out = assemble._l4_brief(text, "Hold")
    assert bear.replace("、", "·") in out, f"96 字符内不应截断: {out}"


def test_buylist_header_drops_confidence(published):
    """置信度列 30 行全『中』零信息 → 删;置信度仍在 details 卡内。"""
    md = published["md"]
    s3 = md.find("## 3. 投资建议")
    header = next((ln for ln in md[s3:].splitlines() if ln.lstrip().startswith("| #")), "")
    assert "置信度" not in header, f"buy-list 表不应再有置信度列: {header}"


# ───────────────────────── 影子买单 CSV 污染防护 ─────────────────────────


def test_run_does_not_touch_real_shadow_csv(tmp_path):
    """assemble.run(tmp_scan_dir) 不应创建或修改真实 context/learning/shadow_buys.csv。"""
    from pathlib import Path

    # 记录真实 CSV 的初始状态
    real_csv = Path("context/learning/shadow_buys.csv")
    if real_csv.exists():
        original_mtime = real_csv.stat().st_mtime
        original_lines = len(real_csv.read_text(encoding="utf-8").splitlines())
    else:
        original_mtime = None
        original_lines = None

    # 在 tmp_path 运行 assemble
    root = tmp_path / "scan_l5"
    scan = _build_scan_dir(root)
    assemble.run(_DATA_DATE, scan_dir=scan, out_root=root / "reports/scan",
                 hhmm=_HHMM, run_date=_RUN_DATE)

    # 验证真实 CSV 未被修改
    if original_mtime is not None:
        # 文件存在过，检查未被修改
        assert real_csv.exists(), "真实 CSV 不应被删除"
        assert real_csv.stat().st_mtime == original_mtime, "真实 CSV 不应被修改"
        assert len(real_csv.read_text(encoding="utf-8").splitlines()) == original_lines, \
            "真实 CSV 行数不应改变"
    else:
        # 文件不存在过，检查仍不存在
        assert not real_csv.exists(), "真实 CSV 不应被创建"


def test_decision_text_zfills_short_ticker(tmp_path):
    """纵深防御:即便上游递来去零 ticker(2156),也要按 6 位零填找到 details/002156.md。"""
    from autoresearch.scan.assemble import _decision_text
    (tmp_path / "details").mkdir(parents=True)
    (tmp_path / "details" / "002156.md").write_text("# 决策卡 — 002156\n", encoding="utf-8")
    assert _decision_text(tmp_path, "2156") is not None
    assert _decision_text(tmp_path, "002156") is not None
    assert _decision_text(tmp_path, "999999") is None


# ───────────────────────── L3.5 闸影子(_l35_gate_shadow_line,plan A2 Task 3) ─────────────────────────
#
# design: docs/specs/2026-07-11-recall-gate-pinned-config-design.md §3
# plan:   docs/plans/2026-07-11-l35-gate-backtest-plan.md Task 3


def test_l35_gate_shadow_line_absent_cut_file_returns_empty(tmp_path):
    """presence-gated:无 _l35_cut.csv(passthrough 默认)→ "" 不加节,老路不破。"""
    assert assemble._l35_gate_shadow_line(tmp_path) == ""


def test_l35_gate_shadow_line_cut_present_no_attribution_yet(tmp_path):
    """同日 assemble(L4 后、retro 前,T+2 fwd 未成熟):只报 cut 只数 + 成熟中注记。"""
    pd.DataFrame({"code": ["000002", "000003"], "rank": [2, 3], "conviction": [10, 5],
                 "lane": ["trend", "trend"],
                 "reason": ["conviction<floor(50)"] * 2}).to_csv(tmp_path / "_l35_cut.csv", index=False)
    line = assemble._l35_gate_shadow_line(tmp_path)
    assert "cut 2 只" in line and "成熟中" in line


def test_l35_gate_shadow_line_with_attribution_shows_means(tmp_path):
    """retro 已补跑(attribution.csv 在场)→ 一行报 cut vs picked 的 fwd_2 均值对照。"""
    pd.DataFrame({"code": ["000002"], "rank": [2], "conviction": [10], "lane": ["trend"],
                 "reason": ["conviction<floor(50)"]}).to_csv(tmp_path / "_l35_cut.csv", index=False)
    pd.DataFrame({"code": ["000001"]}).to_csv(tmp_path / "finalists.csv", index=False)
    (tmp_path / "retro").mkdir()
    pd.DataFrame({"code": ["000001", "000002"], "fwd_2_oc": [0.05, -0.02]}).to_csv(
        tmp_path / "retro" / "attribution.csv", index=False)
    line = assemble._l35_gate_shadow_line(tmp_path)
    assert "cut 1 只" in line
    assert "-2.00%" in line, f"cut 侧均值应显示 -2.00%: {line}"
    assert "+5.00%" in line, f"picked 侧均值应显示 +5.00%: {line}"


def test_build_summary_includes_l35_line_when_cut_file_present(tmp_path):
    """集成:build_summary 输出含「L3.5 闸影子」一行(scan_dir 有 _l35_cut.csv 时)。"""
    root = tmp_path / "scan_l5_l35"
    scan = _build_scan_dir(root)
    pd.DataFrame({"code": ["300476"], "rank": [1], "conviction": [10], "lane": ["trend"],
                 "reason": ["conviction<floor(50)"]}).to_csv(scan / "_l35_cut.csv", index=False)
    md = assemble.build_summary(scan, _DATA_DATE, _HHMM, "folder")
    assert "L3.5 闸影子" in md


def test_published_default_fixture_has_no_l35_shadow_line(published):
    """反向 parity 检查:默认合成 scan dir(无 _l35_cut.csv)不应出现 L3.5 闸影子节。"""
    assert "L3.5 闸影子" not in published["md"]


# ───────────────────────── P0-2:_final_ratings.json(assemble.py 单写者 T2) ─────────────────────────
#
# design: docs/specs/2026-07-12-selflearning-optimization-brainstorm.md §4 P0-2
# STAGES: .claude/skills/scan-market/STAGES.md 开放线头 #6


def test_final_ratings_json_written_after_publish(tmp_path):
    root = tmp_path / "scan_l5_fr"
    scan = _build_scan_dir(root)
    assemble.run(_DATA_DATE, scan_dir=scan, out_root=root / "reports/scan",
                hhmm=_HHMM, run_date=_RUN_DATE)
    fp = scan / "_final_ratings.json"
    assert fp.exists()
    data = json.loads(fp.read_text(encoding="utf-8"))
    assert data == {"300476": "Hold", "600519": "Hold", "002384": "—", "301117": "Overweight"}, data


def test_final_ratings_json_reflects_verify_downgrade_not_card_face(tmp_path):
    """300476(甲)卡面 Overweight,但 verify.csv 判『降级』→ _final_ratings.json 必须落 Hold
    (终评级),不能是卡面残留的 Overweight —— 这正是坏账③要修的行为(STAGES 线头 #6)。"""
    root = tmp_path / "scan_l5_fr2"
    scan = _build_scan_dir(root)
    card_text = (scan / "details" / "300476.md").read_text(encoding="utf-8")
    assert "**Overweight**" in card_text, "夹具前提:甲卡面应仍是 Overweight(折回前)"
    assemble.run(_DATA_DATE, scan_dir=scan, out_root=root / "reports/scan",
                hhmm=_HHMM, run_date=_RUN_DATE)
    data = json.loads((scan / "_final_ratings.json").read_text(encoding="utf-8"))
    assert data["300476"] == "Hold"


def test_final_ratings_json_maintained_ow_keeps_overweight(tmp_path):
    """301117(丁)verify.csv 判『维持』→ 终评级仍是 Overweight,不误折。"""
    root = tmp_path / "scan_l5_fr3"
    scan = _build_scan_dir(root)
    assemble.run(_DATA_DATE, scan_dir=scan, out_root=root / "reports/scan",
                hhmm=_HHMM, run_date=_RUN_DATE)
    data = json.loads((scan / "_final_ratings.json").read_text(encoding="utf-8"))
    assert data["301117"] == "Overweight"


# ───────────────────────── P0-4:process_scores.csv 接线(assemble.run 侧) ─────────────────────────


def test_process_scores_csv_present_in_fresh_publish(tmp_path):
    """assemble.run() 应把过程分 checklist 落 <scan_dir>/process_scores.csv(presence-gated,
    finalists.csv 在场即写)。"""
    root = tmp_path / "scan_l5_ps"
    scan = _build_scan_dir(root)
    assemble.run(_DATA_DATE, scan_dir=scan, out_root=root / "reports/scan",
                hhmm=_HHMM, run_date=_RUN_DATE)
    p = scan / "process_scores.csv"
    assert p.exists()
    df = pd.read_csv(p, dtype={"code": str})
    assert set(df["code"]) == {"300476", "600519", "002384", "301117"}
    assert "process_score" in df.columns


# ───────────────────────── P0-1(c):precedents.build_index 挂 is_real 后处理 ─────────────────────────


def test_run_does_not_touch_real_precedents_db(tmp_path):
    """assemble.run(tmp_scan_dir) 不应创建或修改真实 context/knowledge/precedents.db
    (is_real=False 时不触发 precedents.build_index;镜像 test_run_does_not_touch_real_shadow_csv)。"""
    real_db = Path("context/knowledge/precedents.db")
    original_mtime = real_db.stat().st_mtime if real_db.exists() else None

    root = tmp_path / "scan_l5_prec"
    scan = _build_scan_dir(root)
    assemble.run(_DATA_DATE, scan_dir=scan, out_root=root / "reports/scan",
                hhmm=_HHMM, run_date=_RUN_DATE)

    if original_mtime is not None:
        assert real_db.stat().st_mtime == original_mtime, "真实 precedents.db 不应被修改"
    else:
        assert not real_db.exists(), "真实 precedents.db 不应被创建"


def test_is_real_publish_calls_precedents_build_index(tmp_path, monkeypatch):
    """is_real 分支(scan_dir 解析为 context/scan/<date> 本尊)应调用一次
    `precedents.build_index`(P0-1(c))。

    `is_real` 判据硬编码相对路径比较(`Path("context/scan")/analysis_date`),无参数可覆盖,
    只能靠 chdir 到全新 tmp_path 触发正分支(全沙盒,不碰真实仓库;对照上面的负分支测试)。
    build_index 本体替换为计数桩,避免真建 sqlite 索引、不受环境 FTS5 差异影响。
    """
    monkeypatch.chdir(tmp_path)
    scan = _build_scan_dir(tmp_path)   # root=tmp_path(已 chdir)→ scan = tmp_path/context/scan/<date>
    calls: list[tuple] = []
    monkeypatch.setattr("autoresearch.learning.precedents.build_index",
                        lambda *a, **k: calls.append((a, k)) or {"dates_indexed": ["x"]})

    assemble.run(_DATA_DATE, scan_dir=scan, out_root=tmp_path / "reports/scan",
                hhmm=_HHMM, run_date=_RUN_DATE)

    assert calls, "is_real 发布应调用 precedents.build_index 一次"
