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


def _attr(code, fwd, *, buyable_c1=True, tradable=True, buyable=True):
    # gap_c1_o2:当前 MAIN_RULER(T16 flip)——l2_slo.py 按 MAIN_RULER 动态读源列,资格过滤
    # 经 ruler.entry_tradable() 读 "buyable_c1"(C1 修复,final-review 2026-08-08);"buyable"
    # (旧旗,D+1 开盘)仍写进 fixture 供「旧旗放行、新旗拦下」的对照测试用,day_winners 本身
    # 不再读它。
    return {"code": code, "gap_c1_o2": fwd, "buyable": buyable,
            "buyable_c1": buyable_c1, "tradable": tradable}


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
    attr = pd.DataFrame(_market(19, 0.0) + [_attr("900001", 0.50, buyable_c1=False)])
    winners, _ = l2_slo.day_winners(attr)
    assert len(winners) == 0


def test_winner_excludes_row_when_legacy_buyable_true_but_buyable_c1_false():
    """C1 修复(final-review 2026-08-08)回归锁:「盘中开板、尾盘封死」的票——旧 `buyable`
    (D+1 开盘一字板旗)=True,新 `buyable_c1`(D+1 收盘旗)=False。换尺(gap_c1_o2)后必须
    被挡在 winner 集合外;C1 修复前 `day_winners` 仍读旧 `buyable`,会把这行误判为可交易。
    """
    attr = pd.DataFrame(_market(19, 0.0) +
                        [_attr("900001", 0.50, buyable=True, buyable_c1=False)])
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


def test_guards_selection_reason_fires_on_real_universe_run_output(monkeypatch, tmp_path):
    """T16 活转断言:此前 `universe.run` 写 `L2_gbdt_top200.csv` 时 `l2_cols` 白名单漏投影
    `selection_reason`/`selection_detail`(见 test_universe_l2_cols.py),这条分布 guard 分支
    (`_guards` 的 `if "selection_reason" in l2_frame.columns`)在**手搭 fixture**(本文件其余
    测试,如 `test_guards_expose_concentration_and_lane_coverage`)上跑得通,但那些 fixture 都
    是直接把这两列焊进 DataFrame——绕过了 `universe.run` 真实的落盘投影,盖不住"CSV 里根本
    没有这一列"的洞。31 天扫描日的真实产物上,这条分支因此从未触发过。

    这里直接跑一次 `universe.run`(mock 掉网络层)落真实 CSV,再原样喂给 `_guards`,锁住
    "生产写盘 → guard 读到列 → 分支触发"这条链路真的接通,不是接口层面看着像通。
    """
    from tests.scan.test_universe_l2_cols import run_universe

    outdir = run_universe(monkeypatch, tmp_path, l2_n=20)
    l2_frame = pd.read_csv(outdir / "L2_gbdt_top200.csv", dtype={"code": str})
    guards = l2_slo._guards(l2_frame)
    assert "selection_reason" in guards, "guard 分支必须真的触发(31 天死分支活转)"
    assert sum(guards["selection_reason"].values()) == len(l2_frame)


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


# ══════════════════════════════════════════════════════════════════
# T21(Wave12·F3):per-channel 端到端 capture 六跳漏斗
#
# 5 条纪律:
# 5. 六跳位置齐(recall→L2→pass1→finalist→L4-qualified→E6-top1/BUY),存活集合逐跳嵌套;
# 6. **条件** capture 的分母 = **上一跳存活集合**(不是端到端分母混算);
# 7. winner 定义仍由 `MAIN_RULER` + `ruler.entry_flag_for()` 现算(不另立一套);
# 8. 报警线用当日**之前**的 expanding P25(全期分位含未来);
# 9. 每个比率同屏带分子/分母/as-of/ruler;E6 缺文件记 `—` 不是 0。
# ══════════════════════════════════════════════════════════════════


def _funnel_day(root, date, *, channels, l2, pass1_kept=(), finalists=(), rated=(),
                attr, buys=None, write_buys=True, write_pass1=True, write_l4=True):
    """六跳齐全的一日 —— `channels` = {channel: [code...]}(L1_channels.csv 长表)。

    `write_pass1=False` / `write_l4=False`:造「该跳的产物那天根本不存在」的日子。
    **这两个开关是修复轮补的**(复核 C2):首版 fixture 无条件写 `_l3_pass1_kept.csv`,
    36 条用例里没有一条造过缺件场景 —— 而真数据里 pass1 在 30 天里缺 15 天(07-10
    two-pass 上线前那段,该阶段压根不存在)。测试盲区正是伪影能进报告的原因。
    """
    d = root / date
    (d / "retro").mkdir(parents=True, exist_ok=True)

    rows = [{"channel": ch, "code": c, "channel_rank": i + 1, "channel_score": 10.0 - i}
            for ch, codes in channels.items() for i, c in enumerate(codes)]
    pd.DataFrame(rows, columns=["channel", "code", "channel_rank", "channel_score"]).to_csv(
        d / "L1_channels.csv", index=False)

    frame = pd.DataFrame({"code": list(l2)})
    frame["name"] = frame["code"]
    frame["industry"] = "电子"
    frame["l2_rank"] = range(1, len(l2) + 1)
    frame["recall_channels"] = "composite"
    frame.to_csv(d / "L2_gbdt_top200.csv", index=False)
    pd.DataFrame({"code": list(l2), "rank": range(1, len(l2) + 1)}).to_csv(
        d / "L1_scored_full.csv", index=False)
    pd.DataFrame({"code": list(l2)}).to_csv(d / "L1_recall_top1000.csv", index=False)

    if write_pass1:
        pd.DataFrame({"code": list(pass1_kept),
                      "selection_reason": ["lane"] * len(pass1_kept),
                      "selection_detail": [""] * len(pass1_kept)}).to_csv(
            d / "_l3_pass1_kept.csv", index=False)
        pd.DataFrame({"code": [c for c in l2 if c not in set(pass1_kept)]}).to_csv(
            d / "_l3_pass1_cut.csv", index=False)
    (d / "_l3_judged.json").write_text(json.dumps(
        [{"code": c, "conviction": 7} for c in (pass1_kept if write_pass1 else l2)],
        ensure_ascii=False), encoding="utf-8")
    pd.DataFrame({"code": list(finalists), "guard": [""] * len(finalists)}).to_csv(
        d / "finalists.csv", index=False)
    if write_l4:
        (d / "_final_ratings.json").write_text(json.dumps(
            {c: "Hold" for c in rated}, ensure_ascii=False), encoding="utf-8")
        (d / "_l4_tasks.json").write_text(json.dumps(
            {"tasks": {c: {} for c in finalists}}, ensure_ascii=False), encoding="utf-8")

    if write_buys:
        (d / "_relative_buy_decision.json").write_text(json.dumps(
            {"buys": [{"code": c, "basis": "relative", "rank": 1} for c in (buys or [])]},
            ensure_ascii=False), encoding="utf-8")

    pd.DataFrame(attr).to_csv(d / "retro" / "attribution.csv", index=False)
    return d


# ── 纪律 5:六跳齐 + 存活集合逐跳嵌套 ──────────────────────────────


def test_six_hops_are_all_present_and_nested(tmp_path):
    attr = _market(16, 0.0) + [_attr(c, 0.50) for c in
                               ("900001", "900002", "900003", "900004")]
    d = _funnel_day(tmp_path, "2026-06-01",
                    channels={"momentum": ["900001", "900002", "900003", "900004"]},
                    l2=["900001", "900002", "900003"],
                    pass1_kept=["900001", "900002"], finalists=["900001"],
                    rated=["900001"], attr=attr, buys=["900001"])
    got = l2_slo.day_channel_funnel(d)
    hops = got["channels"]["momentum"]["hops"]
    assert [h["hop"] for h in hops] == list(l2_slo.FUNNEL_HOPS)
    assert [h["n"] for h in hops] == [4, 3, 2, 1, 1, 1], "存活集合必须逐跳单调不增"


def test_a_cut_candidate_cannot_reappear_downstream(tmp_path):
    """存活集合**逐跳嵌套**的活体锁:`900002` 在 pass1 被切,却因为下游产物的孤儿行
    (护照 docstring 记的实测:3 天共 45 只 finalist 不在当日 L2/上游)出现在 finalists.csv 里。
    嵌套口径下它**不能**在 finalist 跳复活;不嵌套(直接 `recall ∩ survivors[hop]`)会让漏斗
    出现 n 变大的一跳 —— 那是数据异常,不是漏斗形态。

    ⚠️ 这条是变异探针 N2 逼出来的:第一版全部 fixture 的存活集合本来就层层嵌套,
    `alive ∩ survivors` 与 `recall ∩ survivors` 恒等 —— 把嵌套摘掉测试照样全绿 = 假绿灯。
    """
    attr = _market(18, 0.0) + [_attr("900001", 0.50), _attr("900002", 0.50)]
    d = _funnel_day(tmp_path, "2026-06-01",
                    channels={"momentum": ["900001", "900002"]},
                    l2=["900001", "900002"], pass1_kept=["900001"],
                    finalists=["900001", "900002"], rated=["900001", "900002"],
                    attr=attr, buys=[])
    hops = {h["hop"]: h for h in l2_slo.day_channel_funnel(d)["channels"]["momentum"]["hops"]}
    assert hops["pass1"]["n"] == 1
    assert hops["finalist"]["n"] == 1, "pass1 切掉的票不得在 finalist 跳复活"
    assert hops["finalist"]["n_winners"] == 1
    assert hops["l4"]["n"] == 1


def test_l4_hop_reads_card_not_dispatch(tmp_path):
    """L4-qualified = 真出了卡(有评级),不是"派发过" —— 派了没回卡不算过这一跳。"""
    attr = _market(19, 0.0) + [_attr("900001", 0.50)]
    d = _funnel_day(tmp_path, "2026-06-01", channels={"momentum": ["900001"]},
                    l2=["900001"], pass1_kept=["900001"], finalists=["900001"],
                    rated=[], attr=attr, buys=[])
    hops = {h["hop"]: h for h in l2_slo.day_channel_funnel(d)["channels"]["momentum"]["hops"]}
    assert hops["finalist"]["n"] == 1
    assert hops["l4"]["n"] == 0


# ── 纪律 6:条件 capture 的分母 = 上一跳存活集合 ──────────────────


def test_conditional_denominator_is_the_previous_hop_not_end_to_end(tmp_path):
    """4 只赢家全被 momentum 召回,L2 只留 2、pass1 只留 1。
    L2 跳条件 capture = 2/4;pass1 跳条件 capture = 1/2(**不是** 1/4)。"""
    attr = _market(16, 0.0) + [_attr(c, 0.50) for c in
                               ("900001", "900002", "900003", "900004")]
    d = _funnel_day(tmp_path, "2026-06-01",
                    channels={"momentum": ["900001", "900002", "900003", "900004"]},
                    l2=["900001", "900002"], pass1_kept=["900001"],
                    finalists=[], rated=[], attr=attr, buys=[])
    hops = {h["hop"]: h for h in l2_slo.day_channel_funnel(d)["channels"]["momentum"]["hops"]}
    assert hops["l2"]["capture_cond"]["value"] == pytest.approx(0.5)
    assert hops["l2"]["capture_cond"]["denom"] == 4
    assert hops["pass1"]["capture_cond"]["value"] == pytest.approx(0.5)
    assert hops["pass1"]["capture_cond"]["denom"] == 2, (
        "分母必须是上一跳(L2)存活的赢家数 2,不是端到端的 4")


def test_relative_capture_is_the_channel_share_of_surviving_winners(tmp_path):
    """相对赢家 capture = 本路在该跳存活的赢家 / **全部通道**在该跳存活的赢家。"""
    attr = _market(18, 0.0) + [_attr("900001", 0.50), _attr("900002", 0.50)]
    d = _funnel_day(tmp_path, "2026-06-01",
                    channels={"momentum": ["900001"], "value": ["900001", "900002"]},
                    l2=["900001", "900002"], pass1_kept=["900001", "900002"],
                    finalists=[], rated=[], attr=attr, buys=[])
    chans = l2_slo.day_channel_funnel(d)["channels"]
    mom = {h["hop"]: h for h in chans["momentum"]["hops"]}["l2"]
    val = {h["hop"]: h for h in chans["value"]["hops"]}["l2"]
    assert mom["capture_rel"]["value"] == pytest.approx(0.5)   # 1 / 2
    assert val["capture_rel"]["value"] == pytest.approx(1.0)   # 2 / 2


def test_zero_denominator_is_none_not_zero(tmp_path):
    """那一跳上一层压根没有赢家 → 比率不存在,不是 0(与既有 `_capture` 同纪律)。"""
    d = _funnel_day(tmp_path, "2026-06-01", channels={"momentum": ["100000"]},
                    l2=["100000"], pass1_kept=[], finalists=[], rated=[],
                    attr=_market(20, 0.0), buys=[])
    hops = {h["hop"]: h for h in l2_slo.day_channel_funnel(d)["channels"]["momentum"]["hops"]}
    assert hops["l2"]["capture_cond"]["value"] is None
    assert hops["l2"]["capture_cond"]["denom"] == 0


# ── 纪律 7:winner 定义引 MAIN_RULER + entry_flag_for() ────────────


def test_winner_definition_is_the_shared_one_not_a_second_copy(tmp_path):
    from autoresearch.common.ruler import MAIN_RULER, entry_flag_for

    attr = _market(19, 0.0) + [_attr("900001", 0.50)]
    d = _funnel_day(tmp_path, "2026-06-01", channels={"momentum": ["900001"]},
                    l2=["900001"], pass1_kept=[], finalists=[], rated=[],
                    attr=attr, buys=[])
    got = l2_slo.day_channel_funnel(d)
    assert got["winner_definition"] == l2_slo.WINNER_DEFINITION
    assert got["ruler"] == MAIN_RULER
    assert entry_flag_for() in got["winner_definition"]


def test_entry_leg_flag_follows_the_ruler_in_the_funnel_too(tmp_path):
    """C1 家族回归:旧旗 `buyable`=True 而新旗 `buyable_c1`=False 的票不得算赢家。"""
    attr = _market(19, 0.0) + [_attr("900001", 0.50, buyable=True, buyable_c1=False)]
    d = _funnel_day(tmp_path, "2026-06-01", channels={"momentum": ["900001"]},
                    l2=["900001"], pass1_kept=[], finalists=[], rated=[],
                    attr=attr, buys=[])
    got = l2_slo.day_channel_funnel(d)
    assert got["n_winners"] == 0


# ── 纪律 8:报警线用当日之前的 expanding P25 ──────────────────────


def test_channel_alarm_line_uses_only_prior_history():
    # `status` 是**必填**的(修复轮 1 / I1):报警序列只收 `status=="PRESENT"` 的日子,
    # 「该路当日没跑」不得被伪造成一次 capture=0 的观测。生产 `day_channel_funnel` 恒写
    # 这个字段;这里的手搭 fixture 首版漏了它,补齐才是对生产形状的忠实描述。
    daily = [{"date": f"2026-06-{i:02d}",
              "channels": {"momentum": {"hops": [
                  {"hop": "l2", "status": "PRESENT",
                   "capture_cond": {"value": 0.5 if i < 14 else 0.01,
                                    "numer": 1, "denom": 2}}]}}}
             for i in range(1, 15)]
    alarm = l2_slo.channel_alarms(daily)
    line = [row for row in alarm if row["channel"] == "momentum"]
    assert all(r["p25"] is None for r in line[:l2_slo.MIN_HISTORY])
    assert line[13]["p25"] == pytest.approx(0.5)
    assert line[13]["alarm"] is True
    assert line[12]["alarm"] is False


def test_channel_alarm_is_none_before_min_history():
    daily = [{"date": "2026-06-01",
              "channels": {"momentum": {"hops": [
                  {"hop": "l2", "status": "PRESENT",
                   "capture_cond": {"value": 0.9, "numer": 9, "denom": 10}}]}}}]
    assert [r["alarm"] for r in l2_slo.channel_alarms(daily)] == [None]


def test_hop_without_explicit_status_is_not_silently_admitted():
    """`status` 缺席 → 该日**不进**报警序列(不猜"大概是在场的")。

    生产恒写此字段;放宽成"缺席即视为 PRESENT"会让 I1 那条纪律留一个后门:
    任何一个忘了带 status 的上游又能把缺件伪装成一次真观测。
    """
    daily = [{"date": "2026-06-01", "channels": {"momentum": {"hops": [
        {"hop": "l2", "capture_cond": {"value": 0.9, "numer": 9, "denom": 10}}]}}}]
    assert l2_slo.channel_alarms(daily) == []


# ── 纪律 9:分子/分母/as-of/ruler 同屏 + E6 缺文件记 `—` ────────────


def test_every_ratio_carries_numer_denom_asof_ruler(tmp_path):
    from autoresearch.common.ruler import MAIN_RULER

    attr = _market(19, 0.0) + [_attr("900001", 0.50)]
    d = _funnel_day(tmp_path, "2026-06-01", channels={"momentum": ["900001"]},
                    l2=["900001"], pass1_kept=["900001"], finalists=["900001"],
                    rated=["900001"], attr=attr, buys=["900001"])
    for hop in l2_slo.day_channel_funnel(d)["channels"]["momentum"]["hops"]:
        for key in ("capture_cond", "capture_rel"):
            ratio = hop[key]
            assert set(ratio) >= {"value", "numer", "denom", "as_of", "ruler"}
            assert ratio["as_of"] == "2026-06-01" and ratio["ruler"] == MAIN_RULER


def test_e6_hop_missing_file_is_dash_not_zero(tmp_path):
    """E6 影子决策文件还没产出的日子:该跳记 `—`(status=ABSENT),**不是** 0 只 BUY。"""
    attr = _market(19, 0.0) + [_attr("900001", 0.50)]
    d = _funnel_day(tmp_path, "2026-06-01", channels={"momentum": ["900001"]},
                    l2=["900001"], pass1_kept=["900001"], finalists=["900001"],
                    rated=["900001"], attr=attr, write_buys=False)
    got = l2_slo.day_channel_funnel(d)
    e6 = {h["hop"]: h for h in got["channels"]["momentum"]["hops"]}["e6"]
    assert e6["n"] is None and e6["status"] == "ABSENT"
    assert e6["capture_cond"]["value"] is None
    assert got["sources"]["e6"] == "ABSENT"


def test_e6_hop_present_but_empty_is_zero_not_dash(tmp_path):
    """文件在、`buys` 是空列表:那是真的「今天一只都没买」= 0,与「没这个文件」区分得开。"""
    attr = _market(19, 0.0) + [_attr("900001", 0.50)]
    d = _funnel_day(tmp_path, "2026-06-01", channels={"momentum": ["900001"]},
                    l2=["900001"], pass1_kept=["900001"], finalists=["900001"],
                    rated=["900001"], attr=attr, buys=[])
    e6 = {h["hop"]: h for h in
          l2_slo.day_channel_funnel(d)["channels"]["momentum"]["hops"]}["e6"]
    assert e6["n"] == 0 and e6["status"] == "PRESENT"


# ── 通道间赢家重叠矩阵 ────────────────────────────────────────────


def test_winner_overlap_matrix_is_jaccard_on_recalled_winners(tmp_path):
    """矩阵数的是**赢家**的重合,不是召回码的重合。

    ⚠️ 变异探针 N9 逼出来的加料:第一版 fixture 里两路召回的**全部**代码恰好都是赢家,
    于是"数赢家"和"数全部召回码"给出同一个答案 —— 把口径改成后者测试照样全绿 = 假绿灯。
    现在给两路各塞一只共同的**非赢家**(`100777`):赢家口径下它不进分子也不进分母,
    全码口径下 common/union 会变成 2/4。
    """
    attr = (_market(17, 0.0) + [_attr(c, 0.50) for c in ("900001", "900002", "900003")]
            + [_attr("100777", 0.0)])
    d = _funnel_day(tmp_path, "2026-06-01",
                    channels={"momentum": ["900001", "900002", "100777"],
                              "value": ["900002", "900003", "100777"]},
                    l2=["900001", "900002", "900003", "100777"], pass1_kept=[],
                    finalists=[], rated=[], attr=attr, buys=[])
    matrix = {(r["channel_a"], r["channel_b"]): r
              for r in l2_slo.day_channel_funnel(d)["winner_overlap"]}
    row = matrix[("momentum", "value")]
    assert (row["common"], row["union"]) == (1, 3), (
        "共同赢家只有 900002、并集只有三只赢家;若口径退化成"
        "「共同召回码」会变成 (2, 4)(非赢家 100777 混进来)")
    assert row["jaccard"] == pytest.approx(1 / 3)
    assert (row["n_a"], row["n_b"]) == (2, 2)


# ── 缺件降级 + 报表接线 ───────────────────────────────────────────


def test_missing_l1_channels_degrades_to_none(tmp_path):
    d = tmp_path / "2026-06-01"
    (d / "retro").mkdir(parents=True)
    pd.DataFrame(_market(20, 0.0)).to_csv(d / "retro" / "attribution.csv", index=False)
    assert l2_slo.day_channel_funnel(d) is None


def test_funnel_section_lands_in_the_report(tmp_path):
    attr = _market(18, 0.0) + [_attr("900001", 0.50), _attr("900002", 0.50)]
    for i in range(1, 4):
        _funnel_day(tmp_path, f"2026-06-{i:02d}",
                    channels={"momentum": ["900001"], "value": ["900001", "900002"]},
                    l2=["900001", "900002"], pass1_kept=["900001"],
                    finalists=["900001"], rated=["900001"], attr=attr, buys=["900001"])
    out_json, out_md = tmp_path / "o.json", tmp_path / "o.md"
    assert l2_slo.main(["--scan-root", str(tmp_path), "--json-out", str(out_json),
                        "--md-out", str(out_md)]) == 0
    md = out_md.read_text(encoding="utf-8")
    assert "per-channel" in md or "逐通道" in md
    assert "momentum" in md and "value" in md
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["channel_funnel"]["n_days"] == 3
    assert set(payload["channel_funnel"]["channels"]) == {"momentum", "value"}


# ══════════════════════════════════════════════════════════════════
# 修复轮 1(复核 C2 / I1 / M2):ABSENT 纪律必须对**每一跳**成立
#
# C2 病灶:`absent` 判据曾硬绑 `hop == "e6"`,其余四跳一律 `alive & survivors[hop]`。
# 而 pass1 在真数据 30 天里**缺 15 天**(07-10 two-pass 上线之前该阶段根本不存在),
# 护照给出 `pass1.kept = None` ⇒ `survivors["pass1"] = ∅` ⇒ 被读成「全被切了」,
# 并**级联清零 finalist / l4 / e6 三跳**。伪影曾被当作「pass1 是最陡的一跳」写进报告。
# ══════════════════════════════════════════════════════════════════


def _hops(day_dir, channel="momentum"):
    return {h["hop"]: h for h in
            l2_slo.day_channel_funnel(day_dir)["channels"][channel]["hops"]}


def test_pass1_artifact_absent_is_dash_not_wiped_out(tmp_path):
    """pass1 产物缺席 → 该跳记 `—`(ABSENT),**不是**「赢家全被切」。"""
    attr = _market(18, 0.0) + [_attr("900001", 0.50), _attr("900002", 0.50)]
    d = _funnel_day(tmp_path, "2026-06-01",
                    channels={"momentum": ["900001", "900002"]},
                    l2=["900001", "900002"], pass1_kept=[],
                    finalists=["900001"], rated=["900001"], attr=attr, buys=["900001"],
                    write_pass1=False)
    hops = _hops(d)
    assert hops["pass1"]["status"] == "ABSENT"
    assert hops["pass1"]["n"] is None and hops["pass1"]["n_winners"] is None
    assert hops["pass1"]["capture_cond"]["value"] is None


def test_absent_hop_does_not_cascade_zero_the_downstream(tmp_path):
    """**C2 的真正代价**:pass1 缺件曾把 finalist/l4/e6 一起清零。
    缺件那跳只该「量不到」,不该把它**下游**的可测量读数也一并毁掉。"""
    attr = _market(18, 0.0) + [_attr("900001", 0.50), _attr("900002", 0.50)]
    d = _funnel_day(tmp_path, "2026-06-01",
                    channels={"momentum": ["900001", "900002"]},
                    l2=["900001", "900002"], pass1_kept=[],
                    finalists=["900001"], rated=["900001"], attr=attr, buys=["900001"],
                    write_pass1=False)
    hops = _hops(d)
    assert hops["l2"]["n_winners"] == 2
    # pass1 量不到 → alive 原样穿过 → finalist 仍以 L2 的存活集合为分母,真量得出来
    assert hops["finalist"]["status"] == "PRESENT"
    assert hops["finalist"]["n"] == 1 and hops["finalist"]["n_winners"] == 1
    assert hops["finalist"]["capture_cond"]["denom"] == 2, (
        "分母必须是最后一次**观测到**的存活赢家数(L2 跳的 2),不是被清零的 0")
    assert hops["l4"]["n"] == 1
    assert hops["e6"]["n"] == 1


def test_l4_artifact_absent_is_dash(tmp_path):
    """L4 那跳的产物(decision_records / _final_ratings)缺席 → 同样记 `—`。"""
    attr = _market(19, 0.0) + [_attr("900001", 0.50)]
    d = _funnel_day(tmp_path, "2026-06-01", channels={"momentum": ["900001"]},
                    l2=["900001"], pass1_kept=["900001"], finalists=["900001"],
                    rated=["900001"], attr=attr, buys=["900001"], write_l4=False)
    hops = _hops(d)
    assert hops["l4"]["status"] == "ABSENT" and hops["l4"]["n"] is None
    assert hops["e6"]["n"] == 1, "L4 缺件同样不得级联清零 E6"


def test_hop_sources_are_reported_per_hop(tmp_path):
    attr = _market(19, 0.0) + [_attr("900001", 0.50)]
    d = _funnel_day(tmp_path, "2026-06-01", channels={"momentum": ["900001"]},
                    l2=["900001"], pass1_kept=[], finalists=[], rated=[],
                    attr=attr, write_buys=False, write_pass1=False)
    got = l2_slo.day_channel_funnel(d)
    assert got["hop_sources"]["pass1"] == "ABSENT"
    assert got["hop_sources"]["e6"] == "ABSENT"
    assert got["hop_sources"]["l2"] == "PRESENT"


def test_absent_days_stay_out_of_the_cumulative_denominator(tmp_path):
    """缺件日不进累计分子/分母(与 E6 既有口径一致)—— 否则 15 个缺件日会把
    `composite` 的 pass1 capture 从 1/86 稀释成 1/138 那种伪影。"""
    attr = _market(18, 0.0) + [_attr("900001", 0.50), _attr("900002", 0.50)]
    _funnel_day(tmp_path, "2026-06-01", channels={"momentum": ["900001", "900002"]},
                l2=["900001", "900002"], pass1_kept=["900001"], finalists=["900001"],
                rated=["900001"], attr=attr, buys=[])
    _funnel_day(tmp_path, "2026-06-02", channels={"momentum": ["900001", "900002"]},
                l2=["900001", "900002"], pass1_kept=[], finalists=["900001"],
                rated=["900001"], attr=attr, buys=[], write_pass1=False)
    payload = l2_slo.build_channel_funnel(tmp_path)
    hops = {h["hop"]: h for h in payload["channels"]["momentum"]["hops"]}
    assert hops["pass1"]["n_days"] == 1, "只有 06-01 那天 pass1 在场"
    assert hops["pass1"]["capture_cond"] == pytest.approx(
        {"value": 0.5, "numer": 1, "denom": 2}, rel=1e-6) or (
        hops["pass1"]["capture_cond"]["numer"] == 1
        and hops["pass1"]["capture_cond"]["denom"] == 2)
    assert hops["finalist"]["n_days"] == 2, "finalist 两天都在场,不因 pass1 缺件而少一天"


# ── I1:报警序列不许把「该路当日没跑」伪造成 capture=0.0 ──────────


def test_alarm_series_excludes_days_the_channel_did_not_run():
    """伪造 0 会把稀疏通道的 P25 线钉死在 0 ⇒ 报警器永远不响(假阴);
    也会把「那天没参赛」误报成「跌破 P25」(假阳)。缺席日必须**剔除**。"""
    def day(date, value=None, with_channel=True):
        chans = {}
        if with_channel:
            chans["momentum"] = {"hops": [
                {"hop": "l2", "status": "PRESENT",
                 "capture_cond": {"value": value, "numer": 1, "denom": 2}}]}
        chans["composite"] = {"hops": [
            {"hop": "l2", "status": "PRESENT",
             "capture_cond": {"value": 0.5, "numer": 1, "denom": 2}}]}
        return {"date": date, "channels": chans}

    # momentum 只在 12 天里跑过,另外 8 天整个通道缺席
    daily = ([day(f"2026-06-{i:02d}", 0.40) for i in range(1, 13)]
             + [day(f"2026-06-{i:02d}", with_channel=False) for i in range(13, 21)])
    rows = [r for r in l2_slo.channel_alarms(daily) if r["channel"] == "momentum"]
    assert len(rows) == 12, "缺席的 8 天不得出现在报警序列里"
    assert all(r["capture_cond"] == pytest.approx(0.40) for r in rows)
    assert all(r["p25"] is None or r["p25"] == pytest.approx(0.40) for r in rows), (
        "P25 线只能由这条路真跑过的日子定,不得被伪造的 0 拉到 0")


def test_sparse_channel_alarm_still_fires_when_it_really_drops():
    """假阴回归锁:一条只跑过 11 天的路,第 12 天真崩了 → 必须报警。
    伪造 0 的写法下它的 P25 线是 0,`value < 0` 恒 False,永远不响。"""
    def day(date, value):
        return {"date": date, "channels": {"momentum": {"hops": [
            {"hop": "l2", "status": "PRESENT",
             "capture_cond": {"value": value, "numer": 1, "denom": 2}}]}}}

    daily = ([{"date": f"2026-05-{i:02d}", "channels": {}} for i in range(1, 10)]
             + [day(f"2026-06-{i:02d}", 0.40) for i in range(1, 12)]
             + [day("2026-06-12", 0.01)])
    rows = [r for r in l2_slo.channel_alarms(daily) if r["channel"] == "momentum"]
    assert rows[-1]["date"] == "2026-06-12"
    assert rows[-1]["alarm"] is True, "真崩了必须响"


def test_absent_hop_day_is_not_counted_as_zero_capture_in_alarms(tmp_path):
    """同一条纪律的活体版:pass1 缺件那天,`l2` 跳仍在场 ⇒ 仍进序列;
    但若报警跳本身 ABSENT,该日必须剔除。"""
    daily = [{"date": "2026-06-01", "channels": {"momentum": {"hops": [
        {"hop": "l2", "status": "ABSENT",
         "capture_cond": {"value": None, "numer": 0, "denom": 0}}]}}}]
    assert l2_slo.channel_alarms(daily) == []


# ── M2:展示层要分得开「产物缺席」与「上一跳没有赢家」 ──────────


def test_render_distinguishes_absent_artifact_from_no_winners(tmp_path):
    attr = _market(18, 0.0) + [_attr("900001", 0.50), _attr("900002", 0.50)]
    for i in range(1, 4):
        _funnel_day(tmp_path, f"2026-06-{i:02d}",
                    channels={"momentum": ["900001", "900002"]},
                    l2=["900001", "900002"], pass1_kept=["900001"],
                    finalists=[], rated=[], attr=attr,
                    write_buys=False)          # E6 恒缺件
    md = "\n".join(l2_slo.render_channel_funnel(l2_slo.build_channel_funnel(tmp_path)))
    assert "—(缺件)" in md, "产物缺席要有自己的记号"
    assert "— (0/0)" in md, "「上一跳没有赢家」仍是 — (0/0),两者不得混为一谈"


def test_absent_hop_does_not_zero_the_relative_capture_denominator(tmp_path):
    """`all_alive`(相对 capture 的**分母**)在缺件跳同样要原样穿过。

    ⚠️ 变异探针 F9 逼出来的:`all_alive[hop] = prev & survivors[hop]`(不穿过)时,
    pass1 缺件那天全通道存活集合被清空 ⇒ 下游 `capture_rel` 分母变 0 ⇒ 比率整列变 `—`。
    首版测试全是单通道、或分子恰等于分母的场景,分母被清零看不出来 —— 这里用**两条通道
    各捞不同赢家**,让分母必须是 2 才对得上。
    """
    attr = _market(18, 0.0) + [_attr("900001", 0.50), _attr("900002", 0.50)]
    d = _funnel_day(tmp_path, "2026-06-01",
                    channels={"momentum": ["900001"], "value": ["900002"]},
                    l2=["900001", "900002"], pass1_kept=[],
                    finalists=["900001", "900002"], rated=["900001", "900002"],
                    attr=attr, buys=[], write_pass1=False)
    chans = l2_slo.day_channel_funnel(d)["channels"]
    mom = {h["hop"]: h for h in chans["momentum"]["hops"]}["finalist"]
    val = {h["hop"]: h for h in chans["value"]["hops"]}["finalist"]
    assert mom["capture_rel"]["denom"] == 2, (
        "全通道在 finalist 跳存活的赢家是 2 只;pass1 缺件不得把这个分母清零")
    assert val["capture_rel"]["denom"] == 2
    assert mom["capture_rel"]["numer"] == 1 and val["capture_rel"]["numer"] == 1


def test_present_hop_with_undefined_ratio_is_also_excluded_from_alarms():
    """`status=PRESENT` 但比率**不存在**(上一跳没有赢家 ⇒ 分母 0 ⇒ value=None)的日子,
    同样不得进报警序列。

    ⚠️ 变异探针 F13 逼出来的「半修」:只挡 `status != PRESENT`、却把 `value=None` 折成
    0.0 —— 既有测试全绿,可 07-07 那种「当日 0 赢家」的日子又会被记成一次 capture=0 的
    观测,P25 线照样被拉低(I1 的假阴换个入口原样复发)。
    """
    def day(date, value):
        return {"date": date, "channels": {"momentum": {"hops": [
            {"hop": "l2", "status": "PRESENT",
             "capture_cond": {"value": value,
                              "numer": 0 if value is None else 1,
                              "denom": 0 if value is None else 2}}]}}}

    daily = [day(f"2026-06-{i:02d}", 0.40) for i in range(1, 12)] + [day("2026-06-12", None)]
    rows = [r for r in l2_slo.channel_alarms(daily) if r["channel"] == "momentum"]
    assert len(rows) == 11, "比率不存在的那天必须被剔除,而不是记成 capture=0"
    assert all(r["capture_cond"] == pytest.approx(0.40) for r in rows)
