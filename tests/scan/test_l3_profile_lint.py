"""L3 确定性三件:行语义指纹 pf / thesis 数字机检 lint / lane 分块渲染(plan Task 9)。

07-08 诊断:L3 通看 ~200 行×22 列裸浮点,误读自见数据 22/31——三件缓解:
①row_profile 把关键因子压成定性短语列 pf(读词不读裸浮点);②lint_judged 确定性核对
thesis 引用的数字是否真在该票行数值列里(workflow 打回一次自修);③l3_table_md 按
lane 分块渲染去位置偏差。NO network,纯确定性。
"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.scan.agents import l3_select as L

# ───────────────────────── ① row_profile(行语义指纹,纯函数) ─────────────────────────


def test_row_profile_words():
    r = {"pct_60d": 45.0, "vol_ratio": 2.5, "main_net_ratio": 1.2, "cmf_20": 0.1,
         "obv_mom_20": 0.2, "pe": 15.0, "winner_rate": 95.0, "rsi6": 85.0}
    p = L.row_profile(r)
    assert p == "高位·放量·主力+·PE低·满盈利⚠·超买"


def test_row_profile_missing_fields_skip_dimension_not_crash():
    """字段缺失/NaN → 该维度不出现,不冤枉、不编造、不抛异常。"""
    assert L.row_profile({}) == ""
    assert L.row_profile({"pct_60d": float("nan")}) == ""
    # 只给位置一维 → 只出一个词
    assert L.row_profile({"pct_60d": -50.0}) == "深跌"
    assert L.row_profile({"pct_60d": 5.0}) == "低位"


def test_row_profile_main_divergence_word():
    """主力背离:main_net_ratio 与 cmf_20/obv_mom_20 方向相反(同向判的反面)。"""
    # main 为正,但资金指标(cmf/obv)明确为负 → 背离
    assert L.row_profile({"main_net_ratio": 0.5, "cmf_20": -0.1, "obv_mom_20": -0.2}) == "主力背离"
    # main 为负,但资金指标明确为正 → 背离
    assert L.row_profile({"main_net_ratio": -0.5, "cmf_20": 0.1, "obv_mom_20": 0.2}) == "主力背离"
    # main 恰为 0 → 主力平
    assert L.row_profile({"main_net_ratio": 0.0}) == "主力平"
    # main 为负且资金指标同向为负 → 主力-
    assert L.row_profile({"main_net_ratio": -0.3, "cmf_20": -0.1}) == "主力−"


def test_row_profile_deterministic_pure():
    """同输入同输出(硬约束:禁 wall-clock/随机)。"""
    r = {"pct_60d": 20.0, "vol_ratio": 3.0, "pe": 80.0, "winner_rate": 10.0, "rsi6": 15.0}
    assert L.row_profile(r) == L.row_profile(dict(r))


def test_l3_table_has_pf_column_and_value(tmp_path):
    """pf 不是 flag 位:恒出现在 _L3_COLS(name 后),l3_table_md 组表时对每行算。"""
    d = tmp_path / "2026-07-09"
    d.mkdir(parents=True)
    pd.DataFrame([{"code": "000001", "name": "甲", "industry": "电子", "composite": 80.0,
                   "pct_60d": 45.0, "vol_ratio": 2.5, "main_net_ratio": 1.2,
                   "cmf_20": 0.1, "obv_mom_20": 0.2, "pe": 15.0,
                   "winner_rate": 95.0, "rsi6": 85.0}]).to_csv(
        d / "L2_gbdt_top200.csv", index=False)
    md = L.l3_table_md("2026-07-09", root=tmp_path)
    assert "| pf |" in md                                    # 列头恒在(非 flag 位)
    row_line = next(ln for ln in md.splitlines() if ln.startswith("| 000001"))
    assert "高位·放量·主力+·PE低·满盈利⚠·超买" in row_line


# ───────────────────────── ② lint_judged(thesis 数字机检) ─────────────────────────


def test_lint_judged_catches_misquote(tmp_path):
    d = tmp_path / "2026-07-09"
    d.mkdir(parents=True)
    (d / "_l3_table.md").write_text("stub", encoding="utf-8")
    pd.DataFrame({"code": ["000001"], "pct_60d": [12.0], "pe": [30.0]}).to_csv(
        d / "L2_gbdt_top200.csv", index=False)
    (d / "_l3_judged.json").write_text(json.dumps([
        {"code": "000001", "thesis": "60日涨12%,PE 30 合理", "catalyst": ""}]), encoding="utf-8")
    assert L.lint_judged("2026-07-09", root=tmp_path)["ok"] is True
    (d / "_l3_judged.json").write_text(json.dumps([
        {"code": "000001", "thesis": "60日涨35%,PE 30", "catalyst": ""}]), encoding="utf-8")
    res = L.lint_judged("2026-07-09", root=tmp_path)
    assert res["ok"] is False and "000001" in res["reason"] and "35" in res["reason"]


def test_lint_judged_tolerance_percent_decimal_interchange(tmp_path):
    """±1% 相对或 ±0.1 绝对容差;百分数与小数互认(main_net_ratio 存小数,thesis 写百分数)。"""
    d = tmp_path / "2026-07-09"
    d.mkdir(parents=True)
    pd.DataFrame({"code": ["000001"], "main_net_ratio": [0.05], "pe": [30.0]}).to_csv(
        d / "L2_gbdt_top200.csv", index=False)
    (d / "_l3_judged.json").write_text(json.dumps([
        {"code": "000001", "thesis": "主力净流入5%,PE 30", "catalyst": ""}]), encoding="utf-8")
    assert L.lint_judged("2026-07-09", root=tmp_path)["ok"] is True
    # ±0.1 绝对容差内也算过(30.05 vs 30)
    (d / "_l3_judged.json").write_text(json.dumps([
        {"code": "000001", "thesis": "PE 30.05", "catalyst": ""}]), encoding="utf-8")
    assert L.lint_judged("2026-07-09", root=tmp_path)["ok"] is True


def test_lint_judged_excludes_year_code_date_tokens(tmp_path):
    """4 位年份 / 6 位代码 / 07-15 形日期 不算待核实数字,即便无处可核对也不误报。"""
    d = tmp_path / "2026-07-09"
    d.mkdir(parents=True)
    pd.DataFrame({"code": ["000001"], "pct_60d": [12.0]}).to_csv(
        d / "L2_gbdt_top200.csv", index=False)
    (d / "_l3_judged.json").write_text(json.dumps([
        {"code": "000001", "thesis": "000001 在 2026 年报(07-15 披露)兑现预期,60日涨12%",
         "catalyst": ""}]), encoding="utf-8")
    res = L.lint_judged("2026-07-09", root=tmp_path)
    assert res["ok"] is True


def test_lint_judged_catalyst_field_also_recognized(tmp_path):
    """thesis 引用的数字若在 catalyst 字段里能找到,也算过(不要求只在数值列)。"""
    d = tmp_path / "2026-07-09"
    d.mkdir(parents=True)
    pd.DataFrame({"code": ["000001"], "pe": [30.0]}).to_csv(
        d / "L2_gbdt_top200.csv", index=False)
    (d / "_l3_judged.json").write_text(json.dumps([
        {"code": "000001", "thesis": "预计净利+58%", "catalyst": "业绩预告净利+58%"}]),
        encoding="utf-8")
    assert L.lint_judged("2026-07-09", root=tmp_path)["ok"] is True


def test_lint_judged_ignores_digits_embedded_in_column_identifiers(tmp_path):
    """thesis 直接引用列名(如 `pct_60d`/`rsi6`/`cmf_20`)时,标识符里嵌的数字不是待核实
    数值——07-08 真实数据冒烟逮到:"pct_60d +21.95" 的 60、"rsi6 52.51" 的 6 被错当数字核对
    (真实 pct_60d/rsi6 值恰不等于 60/6,误报)。"""
    d = tmp_path / "2026-07-09"
    d.mkdir(parents=True)
    pd.DataFrame({"code": ["000001"], "pct_60d": [21.95], "rsi6": [52.51]}).to_csv(
        d / "L2_gbdt_top200.csv", index=False)
    (d / "_l3_judged.json").write_text(json.dumps([
        {"code": "000001", "thesis": "pct_60d +21.95 温和,rsi6 52.51 不超买", "catalyst": ""}]),
        encoding="utf-8")
    res = L.lint_judged("2026-07-09", root=tmp_path)
    assert res["ok"] is True, res["reason"]


def test_lint_judged_missing_judged_file_not_ok(tmp_path):
    d = tmp_path / "2026-07-09"
    d.mkdir(parents=True)
    res = L.lint_judged("2026-07-09", root=tmp_path)
    assert res["ok"] is False and "reason" in res


def test_cli_lint_subcommand(tmp_path, capsys):
    d = tmp_path / "2026-07-09"
    d.mkdir(parents=True)
    pd.DataFrame({"code": ["000001"], "pct_60d": [12.0], "pe": [30.0]}).to_csv(
        d / "L2_gbdt_top200.csv", index=False)
    (d / "_l3_judged.json").write_text(json.dumps([
        {"code": "000001", "thesis": "PE 30 合理", "catalyst": ""}]), encoding="utf-8")
    rc = L.main(["lint", "2026-07-09", "--root", str(tmp_path)])
    payload = json.loads(capsys.readouterr().out.strip())
    assert rc == 0 and payload["ok"] is True


def test_cli_lint_subcommand_nonzero_exit_on_fail(tmp_path, capsys):
    d = tmp_path / "2026-07-09"
    d.mkdir(parents=True)
    pd.DataFrame({"code": ["000001"], "pct_60d": [12.0]}).to_csv(
        d / "L2_gbdt_top200.csv", index=False)
    (d / "_l3_judged.json").write_text(json.dumps([
        {"code": "000001", "thesis": "涨99%", "catalyst": ""}]), encoding="utf-8")
    rc = L.main(["lint", "2026-07-09", "--root", str(tmp_path)])
    payload = json.loads(capsys.readouterr().out.strip())
    assert rc == 1 and payload["ok"] is False and "99" in payload["reason"]


# ───────────────────────── ③ lane 分块渲染(去位置偏差) ─────────────────────────


def _lane_row(code, composite, channels="momentum", reserved=False, name="甲"):
    return {"code": code, "name": name, "industry": "电子", "composite": composite,
            "recall_channels": channels, "l2_lane_reserved": reserved,
            "pct_60d": 10.0, "pe": 30.0}


def test_lane_blocks_default_off_is_parity(tmp_path):
    d = tmp_path / "2026-07-09"
    d.mkdir(parents=True)
    pd.DataFrame([_lane_row("000001", 80.0),
                  _lane_row("000002", 90.0, channels="value", reserved=True, name="乙")]
                 ).to_csv(d / "L2_gbdt_top200.csv", index=False)
    off = L.l3_table_md("2026-07-09", root=tmp_path)
    assert "### lane:" not in off and "render_order=lane_blocks" not in off


def test_lane_blocks_groups_floor_and_channel_floor_last(tmp_path):
    d = tmp_path / "2026-07-09"
    d.mkdir(parents=True)
    pd.DataFrame([_lane_row("000001", 80.0, channels="momentum", reserved=False),
                  _lane_row("000002", 90.0, channels="value", reserved=True, name="乙")]
                 ).to_csv(d / "L2_gbdt_top200.csv", index=False)
    on = L.l3_table_md("2026-07-09", root=tmp_path, lane_blocks=True)
    assert "### lane:momentum" in on
    assert "### lane:floor" in on
    assert "render_order=lane_blocks" in on
    assert "000001" in on and "000002" in on                 # 两行都在(只分块,不丢票)
    assert on.index("### lane:momentum") < on.index("### lane:floor")   # floor 殿后


def test_lane_blocks_sorted_by_composite_desc_within_block(tmp_path):
    d = tmp_path / "2026-07-09"
    d.mkdir(parents=True)
    pd.DataFrame([_lane_row("000001", 50.0, channels="momentum", name="低"),
                  _lane_row("000002", 90.0, channels="momentum", name="高")]
                 ).to_csv(d / "L2_gbdt_top200.csv", index=False)
    on = L.l3_table_md("2026-07-09", root=tmp_path, lane_blocks=True)
    assert on.index("000002") < on.index("000001")            # composite 90 排在 50 前


def test_lane_blocks_missing_lane_cols_falls_back_gracefully(tmp_path):
    """无 l2_lane_reserved/recall_channels 列(旧 staging)→ 不崩,全归一个兜底块。"""
    d = tmp_path / "2026-07-09"
    d.mkdir(parents=True)
    pd.DataFrame([{"code": "000001", "name": "甲", "industry": "电子", "composite": 80.0}]
                 ).to_csv(d / "L2_gbdt_top200.csv", index=False)
    on = L.l3_table_md("2026-07-09", root=tmp_path, lane_blocks=True)
    assert "### lane:" in on and "000001" in on


def test_prepare_l3_table_wires_lane_blocks(tmp_path, monkeypatch):
    """生产入口 prepare_l3_table 实际打开 lane_blocks(镜像其它质量 flag 的接线方式)。"""
    base = tmp_path / "context" / "scan"
    d = base / "2026-07-09"
    (d / "L3_news").mkdir(parents=True)
    pd.DataFrame([_lane_row("000001", 80.0), _lane_row("000002", 60.0, channels="value", name="乙")]
                 ).to_csv(d / "L2_gbdt_top200.csv", index=False)
    for c in ("000001", "000002"):
        (d / "L3_news" / f"{c}.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr("autoresearch.scan.agents.l3_select.harvest_l3_evidence", lambda *a, **k: {})
    monkeypatch.setattr("autoresearch.scan.agents.l3_news.harvest_l3_news", lambda *a, **k: {})
    L.prepare_l3_table("2026-07-09", root=base)
    text = (d / "_l3_table.md").read_text(encoding="utf-8")
    assert "### lane:" in text and "render_order=lane_blocks" in text
