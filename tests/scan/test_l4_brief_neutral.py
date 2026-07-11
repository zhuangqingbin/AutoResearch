"""L4 简报语义波(task-10):中性前提清单(conviction 后移防锚定)+ 逐卡 🔁 基率注入。

design: docs/specs/2026-07-11-funnel-p0p1-wave-plan.md Task 10;
        docs/specs/2026-07-11-funnel-six-questions-brainstorm.md §5.1/§5.2。

覆盖:
  - compose_funnel_brief:L3 thesis 改中性前提清单(前提1=thesis、前提2=mechanism,缺
    mechanism 整行省略),conviction/lane/情感 挪到「L3 元数据」行、排在前提清单之后
    (P0 防锚定:先读中性前提,读完 P1 真数字才看 L3 自己的确信度/方向标签)。
  - write_base_rates:cross_calib.flip_stats 高确信翻案率(per lane)落 `_l4_base_rates.json`,
    thin(n<10)条目直接不写(⚠禁注惯例)。
  - _base_rate_mark 经 compose_funnel_brief 逐卡注入:presence-gated(缺文件不挡简报)。

无网络,纯确定性。
"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.scan.agents import l4_card


def _make_funnel_dir(tmp_path, mechanism="板块轮动位+突破跟随,明日游资接力"):
    """镜像 tests/scan/test_agents.py::_make_funnel_dir(L1/L2/finalists 各 1 行,平安银行)。"""
    d = tmp_path / "context/scan/2026-07-11"
    d.mkdir(parents=True)
    pd.DataFrame([{"code": "000001", "name": "平安银行", "industry": "银行",
                   "composite": 66.6, "n_channels": 3, "recall_channels": "共振|价值|成长",
                   "best_rank": 43, "score_momentum": 50, "score_fund_main": 60,
                   "score_growth": 70, "score_value": 80, "score_volprice": 40,
                   "score_chip": 55, "score_north": 0, "score_tech": 45,
                   "np_yoy": 20.0, "rev_yoy": 10.0, "roe": 17.3, "pe": 9.3, "pb": 1.2,
                   "dv_ratio": 3.19, "main_net_ratio": 0.87, "cmf_20": 0.1, "obv_mom_20": 0.2,
                   "rsi6": 55, "ma_bull": 1, "pct_60d": 12.0, "winner_rate": 1.1,
                   "chip_concentration": 0.3, "price_to_cost": 1.05, "hk_ratio": 0.0}],
                 ).to_csv(d / "L1_recall_top1000.csv", index=False)
    pd.DataFrame([{"code": "000001", "l2_rank": 32, "gbdt_score": 0.61}],
                 ).to_csv(d / "L2_gbdt_top200.csv", index=False)
    row = {"ticker": "000001", "code": "000001", "name": "平安银行", "sector": "银行",
           "lenses": "共振3路", "conviction": 82, "triage_lean": "看多",
           "thesis": "3路共振·主力净流入·估值低位", "risk": "利率下行息差承压",
           "catalyst": "无明确催化", "lane": "trend", "sentiment": "中性"}
    if mechanism is not None:
        row["mechanism"] = mechanism
    pd.DataFrame([row]).to_csv(d / "finalists.csv", index=False)
    return d


# ───────────────────────── 中性前提清单(防锚定:conviction 后移) ─────────────────────────


def test_brief_premises_before_conviction(tmp_path):
    scan_dir = _make_funnel_dir(tmp_path)
    text = l4_card.compose_funnel_brief("000001", scan_dir)
    assert "前提清单" in text
    assert text.index("前提清单") < text.index("conviction")   # conviction 必须出现在前提之后


def test_brief_mechanism_rendered_as_premise2_when_present(tmp_path):
    scan_dir = _make_funnel_dir(tmp_path, mechanism="板块轮动位+突破跟随,明日游资接力")
    text = l4_card.compose_funnel_brief("000001", scan_dir)
    assert "前提2(兑现机制)" in text
    assert "板块轮动位+突破跟随,明日游资接力" in text
    assert "3路共振·主力净流入·估值低位" in text            # 前提1 = thesis 原文,未被顶掉


def test_brief_mechanism_line_omitted_when_field_missing(tmp_path):
    scan_dir = _make_funnel_dir(tmp_path, mechanism=None)   # 旧 finalists.csv 无 mechanism 列
    text = l4_card.compose_funnel_brief("000001", scan_dir)
    assert "前提2(兑现机制)" not in text
    assert "前提清单" in text                                # 其余不受影响


def test_brief_still_has_risk_and_catalyst_lines(tmp_path):
    """风险/催化两行是既有契约(未在本任务改动范围),必须原样保留。"""
    scan_dir = _make_funnel_dir(tmp_path)
    text = l4_card.compose_funnel_brief("000001", scan_dir)
    assert "最大风险" in text and "利率下行息差承压" in text
    assert "催化" in text


# ───────────────────────── write_base_rates ─────────────────────────


def test_write_base_rates_thin_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "autoresearch.learning.cross_calib.flip_stats",
        lambda **k: pd.DataFrame([
            {"lane": "trend", "n_hiconv": 52, "flip_rate": 0.33, "thin": False},
            {"lane": "value", "n_hiconv": 3, "flip_rate": 1.0, "thin": True}]))
    p = l4_card.write_base_rates(tmp_path)
    data = json.loads(p.read_text(encoding="utf-8"))
    assert "trend" in data["by_lane"] and "value" not in data["by_lane"]
    assert data["by_lane"]["trend"] == {"n": 52, "flip_rate": 0.33}


def test_write_base_rates_no_data_returns_none(tmp_path, monkeypatch):
    """flip_stats/buy_ledger 都空手(无现场)→ 不落盘垃圾文件,返回 None。"""
    monkeypatch.setattr("autoresearch.learning.cross_calib.flip_stats",
                        lambda **k: pd.DataFrame(columns=["lane", "n", "n_hiconv", "flip_rate", "thin"]))
    scan_dir = tmp_path / "context" / "scan" / "2026-07-11"    # 无真实兄弟目录,roll() 自然查无
    scan_dir.mkdir(parents=True)
    assert l4_card.write_base_rates(scan_dir) is None
    assert not (scan_dir / "_l4_base_rates.json").exists()


# ───────────────────────── 逐卡 🔁 基率注入(presence-gated) ─────────────────────────


def test_base_rate_mark_absent_without_file(tmp_path):
    scan_dir = _make_funnel_dir(tmp_path)
    text = l4_card.compose_funnel_brief("000001", scan_dir)
    assert "🔁 基率" not in text


def test_base_rate_mark_injected_for_matching_lane(tmp_path):
    scan_dir = _make_funnel_dir(tmp_path)      # finalists lane = trend
    (scan_dir / "_l4_base_rates.json").write_text(
        json.dumps({"by_lane": {"trend": {"n": 52, "flip_rate": 0.33}},
                    "by_rating": {"Overweight": {"n": 12, "mean_fwd2": -0.03, "win": 0.25}}}),
        encoding="utf-8")
    text = l4_card.compose_funnel_brief("000001", scan_dir)
    assert "🔁 基率" in text
    assert "trend" in text.split("🔁 基率")[1].split("\n")[0]
    assert "33%" in text
    assert "25%" in text                        # OW 胜率行也应注入(≤3 项之一)


def test_base_rate_mark_no_entry_for_lane_is_silent(tmp_path):
    """`_l4_base_rates.json` 在,但该票 lane 没有对应条目 → 至少不因 lane 缺失而报错;
    by_rating 若有 Overweight 条目仍可单独注入(两部分互相独立 presence-gate)。"""
    scan_dir = _make_funnel_dir(tmp_path)      # lane = trend,json 里只有 value
    (scan_dir / "_l4_base_rates.json").write_text(
        json.dumps({"by_lane": {"value": {"n": 40, "flip_rate": 0.1}}, "by_rating": {}}),
        encoding="utf-8")
    text = l4_card.compose_funnel_brief("000001", scan_dir)
    assert "🔁 基率" not in text                # lane 不匹配 + by_rating 空 → 整行不注(无内容可报)
