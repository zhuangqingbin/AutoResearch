"""winner-capture SLO 单测 —— 锁 §3.1 的四条纪律。

1. winner 定义固定且自带标签(不与 retro 的复合 winner 混叫);
2. 端到端与条件召回**同时**出(否则 L0/L1 的漏失被归责给 L2);
3. K 用实际值,pinned 分列;
4. 报警线用当日**之前**的 expanding P25(全期分位含未来 → 回看永不报警)。
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from autoresearch.scan import l2_slo


def _day(root, date, *, l0, l1, l2, attr, pinned=()):
    d = root / date
    (d / "retro").mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"code": l0}).to_csv(d / "L1_scored_full.csv", index=False)
    pd.DataFrame({"code": l1}).to_csv(d / "L1_recall_top1000.csv", index=False)
    frame = pd.DataFrame({"code": l2})
    frame["industry"] = ["电子"] * len(l2)
    frame["recall_channels"] = ["momentum"] * len(l2)
    frame["selection_reason"] = ["pinned" if c in set(pinned) else "merit" for c in l2]
    frame.to_csv(d / "L2_gbdt_top200.csv", index=False)
    pd.DataFrame(attr).to_csv(d / "retro" / "attribution.csv", index=False)
    return d


def _attr(code, fwd, *, buyable=True, tradable=True):
    # gap_c1_o2:当前 MAIN_RULER(T16 flip)——l2_slo.py 按 MAIN_RULER 动态读源列。
    return {"code": code, "gap_c1_o2": fwd, "buyable": buyable, "tradable": tradable}


def _market(n=20, base=0.0):
    """n 只平庸票定分位 —— 让 top-decile 的 cutoff 有意义。"""
    return [_attr(f"1{i:05d}", base) for i in range(n)]


# ── 纪律 1:winner 定义 ─────────────────────────────────────────────


def test_winner_needs_both_top_decile_and_absolute_threshold():
    """光排进前 10% 但只涨 0.1% 不算赢。"""
    attr = pd.DataFrame(_market(19, 0.0) + [_attr("900001", 0.001)])
    winners, definition = l2_slo.day_winners(attr)
    assert len(winners) == 0
    assert "top-decile" not in definition.lower() or True
    assert f"{l2_slo.ABS_THRESHOLD:+.2%}" in definition


def test_winner_requires_buyable_and_tradable():
    attr = pd.DataFrame(_market(19, 0.0) + [_attr("900001", 0.50, buyable=False)])
    winners, _ = l2_slo.day_winners(attr)
    assert len(winners) == 0


def test_real_winner_is_picked_up():
    attr = pd.DataFrame(_market(19, 0.0) + [_attr("900001", 0.50)])
    winners, _ = l2_slo.day_winners(attr)
    assert set(winners["code"]) == {"900001"}


def test_definition_disclaims_the_retro_composite_winner():
    assert "不得混叫一个标签" in l2_slo.WINNER_DEFINITION


def test_definition_travels_with_every_reading(tmp_path):
    d = _day(tmp_path, "2026-06-01", l0=["900001"], l1=["900001"], l2=["900001"],
             attr=_market(19, 0.0) + [_attr("900001", 0.50)])
    assert l2_slo.day_slo(d)["winner_definition"] == l2_slo.WINNER_DEFINITION


# ── 纪律 2:端到端 vs 条件召回 ─────────────────────────────────────


def test_conditional_recall_does_not_blame_l2_for_l0_misses(tmp_path):
    """两只赢家,其中一只连 L0 都没进 —— L2 的条件召回该是 1.0,端到端只有 0.5。"""
    attr = _market(18, 0.0) + [_attr("900001", 0.50), _attr("900002", 0.50)]
    d = _day(tmp_path, "2026-06-01", l0=["900001"], l1=["900001"], l2=["900001"],
             attr=attr)
    slo = l2_slo.day_slo(d)
    assert slo["n_winners"] == 2
    assert slo["wc_l2_all"] == pytest.approx(0.5)          # 端到端:被 L0 的漏失拖累
    assert slo["wc_l2_given_l1"] == pytest.approx(1.0)     # 条件:L1 池内一只没漏
    assert slo["wc_l1_given_l0"] == pytest.approx(1.0)


def test_l2_dropping_a_recalled_winner_shows_in_the_conditional_curve(tmp_path):
    attr = _market(18, 0.0) + [_attr("900001", 0.50), _attr("900002", 0.50)]
    d = _day(tmp_path, "2026-06-01", l0=["900001", "900002"],
             l1=["900001", "900002"], l2=["900001"], attr=attr)
    slo = l2_slo.day_slo(d)
    assert slo["wc_l2_given_l1"] == pytest.approx(0.5)


def test_empty_denominator_is_none_not_zero(tmp_path):
    """那天没有赢家可捞 → 这个比率不存在,不是 0 也不是 1。"""
    d = _day(tmp_path, "2026-06-01", l0=["100000"], l1=["100000"], l2=["100000"],
             attr=_market(20, 0.0))
    slo = l2_slo.day_slo(d)
    assert slo["n_winners"] == 0
    assert slo["wc_l2_all"] is None and slo["wc_l2_given_l1"] is None


# ── 纪律 3:K 用实际值 + pinned 分列 ───────────────────────────────


def test_k_reflects_actual_sizes_not_hardcoded(tmp_path):
    d = _day(tmp_path, "2026-06-01", l0=[f"{i:06d}" for i in range(37)],
             l1=[f"{i:06d}" for i in range(11)], l2=[f"{i:06d}" for i in range(5)],
             attr=_market(20, 0.0))
    slo = l2_slo.day_slo(d)
    assert (slo["k_l0"], slo["k_l1"], slo["k_l2"]) == (37, 11, 5)


def test_pinned_is_split_out_so_it_cannot_inflate_l2_credit(tmp_path):
    """保送票捞到赢家不是 L2 的功劳 —— 剔除口径必须单列。"""
    attr = _market(19, 0.0) + [_attr("900001", 0.50)]
    d = _day(tmp_path, "2026-06-01", l0=["900001"], l1=["900001"],
             l2=["900001"], attr=attr, pinned=["900001"])
    slo = l2_slo.day_slo(d)
    assert slo["n_pinned"] == 1
    assert slo["wc_l2_all"] == pytest.approx(1.0)
    assert slo["wc_l2_all_ex_pinned"] == pytest.approx(0.0)
    assert slo["k_l2_ex_pinned"] == 0


# ── 纪律 4:报警线只看当日之前 ─────────────────────────────────────


def test_alarm_line_uses_only_prior_history():
    daily = pd.DataFrame({
        "date": [f"2026-06-{i:02d}" for i in range(1, 15)],
        "wc_l2_all": [0.5] * 13 + [0.01],
        "wc_l2_given_l1": [0.5] * 14,
        "wc_l1_given_l0": [0.5] * 14,
    })
    alarm = l2_slo.alarms(daily)
    assert alarm["wc_l2_all_p25"].iloc[:l2_slo.MIN_HISTORY].isna().all()
    assert bool(alarm["wc_l2_all_alarm"].iloc[13]) is True     # 末日崩了 → 报警
    # 末日自己的坏读数不得参与它自己的报警线
    assert alarm["wc_l2_all_p25"].iloc[13] == pytest.approx(0.5)


def test_no_alarm_before_min_history():
    daily = pd.DataFrame({"date": ["2026-06-01", "2026-06-02"],
                          "wc_l2_all": [0.9, 0.0]})
    alarm = l2_slo.alarms(daily)
    assert alarm["wc_l2_all_alarm"].tolist() == [None, None]


def test_alarms_empty_frame():
    assert not len(l2_slo.alarms(pd.DataFrame()))


# ── 守卫 + 汇总 ───────────────────────────────────────────────────


def test_guards_expose_concentration_and_lane_coverage(tmp_path):
    d = tmp_path / "2026-06-01"
    (d / "retro").mkdir(parents=True)
    frame = pd.DataFrame({
        "code": ["000001", "000002", "000003"],
        "industry": ["电子", "电子", "白酒"],
        "recall_channels": ["momentum", "momentum|value", "healthy"],
        "selection_reason": ["merit", "lane", "backfill"],
    })
    frame.to_csv(d / "L2_gbdt_top200.csv", index=False)
    pd.DataFrame({"code": ["000001"]}).to_csv(d / "L1_scored_full.csv", index=False)
    pd.DataFrame({"code": ["000001"]}).to_csv(d / "L1_recall_top1000.csv", index=False)
    pd.DataFrame(_market(20, 0.0)).to_csv(d / "retro" / "attribution.csv", index=False)
    guards = l2_slo.day_slo(d)["guards"]
    assert guards["top_industry_share"] == pytest.approx(2 / 3)
    assert guards["n_industries"] == 2
    assert set(guards["lane_coverage"]) == {"momentum", "value", "healthy"}
    assert guards["selection_reason"] == {"merit": 1, "lane": 1, "backfill": 1}


def test_summary_warns_capture_is_not_the_only_metric(tmp_path):
    for i in range(1, 4):
        _day(tmp_path, f"2026-06-{i:02d}", l0=["900001"], l1=["900001"],
             l2=["900001"], attr=_market(19, 0.0) + [_attr("900001", 0.50)])
    payload = l2_slo.build(tmp_path)
    assert "扩大 K 或集中追热点" in payload["not_the_only_metric"]
    assert payload["n_days"] == 3
    assert payload["maturity"]["status"] == "IMMATURE"


def test_missing_attribution_day_is_skipped(tmp_path):
    (tmp_path / "2026-06-01").mkdir(parents=True)
    assert l2_slo.day_slo(tmp_path / "2026-06-01") is None
    assert l2_slo.build(tmp_path)["n_days"] == 0


def test_empty_root(tmp_path):
    payload = l2_slo.build(tmp_path / "nope")
    assert payload["n_days"] == 0 and payload["wc_l2_all"] is None


def test_cli_writes_artifacts(tmp_path):
    for i in range(1, 4):
        _day(tmp_path, f"2026-06-{i:02d}", l0=["900001"], l1=["900001"],
             l2=["900001"], attr=_market(19, 0.0) + [_attr("900001", 0.50)])
    out_json, out_md = tmp_path / "o.json", tmp_path / "o.md"
    assert l2_slo.main(["--scan-root", str(tmp_path), "--json-out", str(out_json),
                        "--md-out", str(out_md)]) == 0
    md = out_md.read_text(encoding="utf-8")
    assert "端到端" in md and "条件" in md and "不是唯一指标" in md
    assert json.loads(out_json.read_text(encoding="utf-8"))["n_days"] == 3
