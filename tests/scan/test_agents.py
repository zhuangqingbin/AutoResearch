"""L3/L4 确定性 helper 回归 —— 端口自 scan_pipeline._selftest()(Plan 4.1)。

覆盖(逐项对应原 selftest):
  - L3 紧凑表 / load_l3_input(证据摘要 lhb_n/has_forecast/has_express)/ l3_table_md
  - merge_l3_finalists_v2 趋势配额混合(一半 conviction + 一半 pct_60d)+ schema 列
  - L4 compose_funnel_brief(P0 简报)/ parse_ratings_from_details / pick_buy_candidates(买单 skeptic 名单)/ pick_buylist
  - rubric_rating 净分定档 + OW 门压 Hold(键名容错)
NO network. 纯确定性。
"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.scan.agents.l3_select import (
    l3_table_md,
    load_l3_input,
    merge_l3_finalists_v2,
)
from autoresearch.scan.agents.l4_card import (
    compose_funnel_brief,
    parse_ratings_from_details,
    pick_buy_candidates,
    pick_buylist,
    rubric_rating,
)

# ───────────────────────── L3:紧凑表 + 证据摘要 ─────────────────────────


def _make_l2_dir(tmp_path):
    """造 L2_gbdt_top200(200 行)+ 一个 L3_evidence/000000.json(龙虎榜2/预告1/无快报)。"""
    root = tmp_path / "context/scan"
    d = root / "2026-06-20"
    (d / "L3_evidence").mkdir(parents=True)
    rows = [{"code": f"{i:06d}", "name": f"s{i}", "industry": "电子", "composite": 100 - i * 0.1,
             "gbdt_score": 0.5, "score_momentum": 50, "score_fund_main": 40, "pct_60d": 10.0,
             "main_net_ratio": 0.01, "winner_rate": 30.0, "np_yoy": 50.0} for i in range(200)]
    pd.DataFrame(rows).to_csv(d / "L2_gbdt_top200.csv", index=False)
    (d / "L3_evidence" / "000000.json").write_text(
        json.dumps({"code": "000000", "longhu": [{"x": 1}, {"x": 2}], "forecast": [{"y": 1}]}),
        encoding="utf-8")
    return root


def test_load_l3_input_rows_and_evidence(tmp_path):
    root = _make_l2_dir(tmp_path)
    l3in = load_l3_input("2026-06-20", root=root)
    assert len(l3in) == 200, "load_l3_input 行数错"
    assert "lhb_n" in l3in.columns, "load_l3_input 缺证据列"
    row0 = l3in[l3in["code"] == "000000"].iloc[0]
    assert int(row0["lhb_n"]) == 2
    assert bool(row0["has_forecast"]) is True
    assert bool(row0["has_express"]) is False


def test_l3_table_md_has_cols_and_evidence(tmp_path):
    root = _make_l2_dir(tmp_path)
    md = l3_table_md("2026-06-20", root=root)
    assert "code" in md
    assert "000000" in md
    assert "lhb_n" in md


# ───────────────────────── L3:finalists 合并(趋势配额安全网) ─────────────────────────


def _judged_hybrid() -> pd.DataFrame:
    """5 只:000010 高conv趋势(net低)、000014 高pct_60d趋势(conv最低)、3 只 reversion(net高)。"""
    return pd.DataFrame({
        "code": ["000010", "000011", "000012", "000013", "000014"],
        "name": ["趋高conv", "回1", "回2", "回3", "趋高动量"],
        "sector": ["元件", "银行", "银行", "银行", "元件"], "lenses": ["动量"] * 5,
        "conviction": [75, 60, 58, 55, 50], "fragility": [50, 20, 20, 20, 45],
        "thesis": ["t"] * 5, "risk": ["r"] * 5, "catalyst": ["c"] * 5,
        "triage_lean": ["看多"] * 5, "triage_reason": ["x"] * 5,
        "lane": ["trend", "reversion", "reversion", "reversion", "trend"],
        "pct_60d": [60, 5, 5, 5, 300]})


def test_merge_l3_finalists_hybrid_quota_keeps_both_trend_halves():
    out3 = merge_l3_finalists_v2(_judged_hybrid(), target=3, trend_quota=2)  # quota 2 = 1 conv + 1 动量
    codes = set(out3["code"])
    assert "000010" in codes, "hybrid conviction 半未保住高conv趋势票"
    assert "000014" in codes, "hybrid 动量半未保住高pct_60d趋势票"
    assert len(out3) == 3, "finalists 数错"


def test_merge_l3_finalists_has_required_columns():
    out3 = merge_l3_finalists_v2(_judged_hybrid(), target=3, trend_quota=2)
    need = {"ticker", "code", "name", "sector", "conviction", "thesis", "risk", "catalyst", "lane"}
    assert need <= set(out3.columns), f"finalists 缺列 {need - set(out3.columns)}"


def test_merge_l3_finalists_carries_sentiment():
    j = _judged_hybrid().assign(sentiment=["利多", "中性", "中性", "中性", "利多"])
    out = merge_l3_finalists_v2(j, target=3, trend_quota=2)
    assert "sentiment" in out.columns


# ───────────────────────── L4:选择器(评级解析 + 买单 skeptic 名单) ─────────────────────────


def test_parse_ratings_from_details(tmp_path):
    dd = tmp_path / "details"
    dd.mkdir(parents=True)
    cards = {"000001": "Buy", "000002": "Overweight", "000003": "Hold",
             "000004": "Underweight", "000005": "Sell"}
    for code, rt in cards.items():
        (dd / f"{code}.md").write_text(
            f"# 决策卡\n**Rating**: {rt}\nFINAL TRANSACTION PROPOSAL: **HOLD**\n", encoding="utf-8")
    assert parse_ratings_from_details(dd) == cards


def test_pick_buy_candidates_is_buy_skeptic_list(tmp_path):
    """pick_buy_candidates(ratings) = 最终 ≥OW 买单 → 独立 skeptic 名单(语义改;集合不变)。"""
    got = {"000001": "Buy", "000002": "Overweight", "000003": "Hold",
           "000004": "Underweight", "000005": "Sell"}
    assert set(pick_buy_candidates(got)) == {"000001", "000002"}
    assert set(pick_buylist(got, floor="Overweight")) == {"000001", "000002"}
    assert set(pick_buylist(got, floor="Buy")) == {"000001"}


# ───────────────────────── L4 · C:rubric 评分卡 ─────────────────────────

_ALL_GATES = {"主力真在": True, "业绩真兑现": True, "估值不透支": True}


def test_rubric_rating_six_strong_is_buy():
    strong = {"基本面": "强", "估值": "强", "技术·资金": "强", "盈利质量": "强",
              "偿付(爆雷)": "强", "催化": "强"}  # net+6
    assert rubric_rating(strong, _ALL_GATES)[0] == "Buy"


def test_rubric_rating_net2_is_overweight():
    ow = {"基本面": "强", "估值": "中", "技术·资金": "强", "盈利质量": "中",
          "偿付(爆雷)": "中", "催化": "中"}  # net+2
    assert rubric_rating(ow, _ALL_GATES)[0] == "Overweight"


def test_rubric_rating_ow_gate_fail_pressed_to_hold():
    ow = {"基本面": "强", "估值": "中", "技术·资金": "强", "盈利质量": "中",
          "偿付(爆雷)": "中", "催化": "中"}  # net+2
    rating, why = rubric_rating(ow, {"主力真在": False, "业绩真兑现": True, "估值不透支": True})
    assert rating == "Hold"
    assert "压Hold" in why, f"净分+2但OW门缺一应压 Hold: {why}"


def test_rubric_rating_net0_is_hold():
    flat = dict.fromkeys(("基本面", "估值", "技术·资金", "盈利质量", "偿付(爆雷)", "催化"), "中")  # net0
    assert rubric_rating(flat, {})[0] == "Hold"


def test_rubric_rating_six_weak_is_sell_gate_does_not_rescue_downside():
    weak = dict.fromkeys(("基本面", "估值", "技术·资金", "盈利质量", "偿付(爆雷)", "催化"), "弱")  # net-6
    assert rubric_rating(weak, _ALL_GATES)[0] == "Sell", "门只压上行,不救下行"


# ───────────────────────── L4 · P0:漏斗简报 ─────────────────────────


def _make_funnel_dir(tmp_path):
    """造 L1_recall / L2_gbdt / finalists 各 1 行(神火 000933)。"""
    d = tmp_path / "context/scan/2026-06-24"
    d.mkdir(parents=True)
    pd.DataFrame([{"code": "000933", "name": "神火股份", "industry": "工业金属",
                   "composite": 66.6, "n_channels": 3, "recall_channels": "共振|价值|成长",
                   "best_rank": 43, "score_momentum": 50, "score_fund_main": 60,
                   "score_growth": 70, "score_value": 80, "score_volprice": 40,
                   "score_chip": 55, "score_north": 0, "score_tech": 45,
                   "np_yoy": 223.0, "rev_yoy": 10.0, "roe": 17.3, "pe": 9.3, "pb": 1.2,
                   "dv_ratio": 3.19, "main_net_ratio": 0.87, "cmf_20": 0.1, "obv_mom_20": 0.2,
                   "rsi6": 55, "ma_bull": 1, "pct_60d": 12.0, "winner_rate": 1.1,
                   "chip_concentration": 0.3, "price_to_cost": 1.05, "hk_ratio": 0.0}],
                 ).to_csv(d / "L1_recall_top1000.csv", index=False)
    pd.DataFrame([{"code": "000933", "l2_rank": 132, "gbdt_score": 0.54}],
                 ).to_csv(d / "L2_gbdt_top200.csv", index=False)
    pd.DataFrame([{"ticker": "000933", "code": "000933", "name": "神火股份", "sector": "工业金属",
                   "lenses": "共振3路", "conviction": 90, "triage_lean": "看多",
                   "thesis": "3路共振·PE9.3低估·np+223", "risk": "煤铝价周期下行盈利回吐",
                   "catalyst": "无明确催化", "lane": "trend", "sentiment": "中性"}],
                 ).to_csv(d / "finalists.csv", index=False)
    return d


def test_compose_funnel_brief_has_channels_factors_thesis(tmp_path):
    brief = compose_funnel_brief("000933", _make_funnel_dir(tmp_path))
    assert "神火股份" in brief
    assert "命中 3 路" in brief                 # n_channels
    assert "conviction 90" in brief             # L3
    assert "3路共振·PE9.3低估" in brief          # L3 thesis
    assert "np_yoy 223" in brief                # L1 先验因子
    assert "gbdt_score 0.54" in brief           # L2


def test_compose_funnel_brief_degrades_missing_finalists(tmp_path):
    d = _make_funnel_dir(tmp_path)
    (d / "finalists.csv").unlink()
    brief = compose_funnel_brief("000933", d)   # 无 L3 → 不抛,仍出 L1 先验
    assert "神火股份" in brief and "np_yoy 223" in brief
