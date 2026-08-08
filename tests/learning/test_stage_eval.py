import pandas as pd
import pytest

from autoresearch.learning import stage_eval


def test_selftest():
    assert stage_eval._selftest() == 0


def test_stage_eval_main_horizon_is_t2():
    """主口径契约:超短主尺跟随 MAIN_RULER —— 现 gap_c1_o2(2026-08-05 裁定 T16 换值,
    取代 2026-07-10 裁定的 fwd_2_oc;持仓 1~2 日的窗口本身不变)。"""
    assert stage_eval._RET_MAIN == "gap_c1_o2"


def _realized(codes, fwd):
    return pd.DataFrame({"code": codes, "fwd_1_oo": fwd, "gap_c1_o2": fwd, "fwd_5_oc": fwd})


def test_channel_edge_excludes_row_when_legacy_buyable_true_but_buyable_c1_false():
    """C1 修复(final-review 2026-08-08)回归锁:`channel_edge` 的入场旗必须跟随 MAIN_RULER
    选(entry_tradable),不是硬编码 "buyable"——旧旗(D+1 开盘)=True 但新旗 `buyable_c1`
    (D+1 收盘)=False 的票,换尺(gap_c1_o2)后不得计入 `mean_excess_t2`/`unique_excess_t2`
    这些均值分子(否则会把「收盘封死买不进」的票的超额收益错记成通道的功劳)。
    """
    recall = pd.DataFrame({
        "code": ["000001", "000002"],
        "recall_channels": ["momentum", "momentum"],
    })
    realized = pd.DataFrame({
        "code": ["000001", "000002"],
        "gap_c1_o2": [0.02, 0.50],
        "fwd_5_oc": [0.0, 0.0],
        "fwd_1_oo": [0.0, 0.0],
        "buyable": [True, True],           # 旧旗全放行
        "buyable_c1": [True, False],       # 000002:收盘封死,新旗拦下
    })
    out = stage_eval.channel_edge(recall, realized)
    row = out.set_index("channel").loc["momentum"]
    assert row["n_unbuyable"] == 1
    # market 基准 = median(0.02, 0.50) = 0.26;修复后 mb 只剩 000001 → mean_excess_t2=-0.24。
    # 若 C1 未修(旧 "buyable" 全放行),000002 会被计入 → mean 会被拉到 0.0(两者均值)。
    assert row["mean_excess_t2"] == pytest.approx(-0.24)


# ───────────────────────── Wave12-T12 · channel_eval.csv 增 ruler 列 + 渲染标题记档 ─────────────────────────


def test_channel_edge_output_includes_ruler_column():
    """channel_eval.csv(channel_edge 的落盘产物)此前无 ruler 列——`*_t2` 列名不改,
    但要能分辨这些超额值出自哪把尺(镜像 stage_eval.csv 的 _flat_csv 同款字段)。"""
    recall = pd.DataFrame({"code": ["000001"], "recall_channels": ["momentum"]})
    realized = pd.DataFrame({
        "code": ["000001"], "gap_c1_o2": [0.02], "fwd_5_oc": [0.0], "fwd_1_oo": [0.0],
        "buyable": [True], "buyable_c1": [True],
    })
    out = stage_eval.channel_edge(recall, realized)
    assert "ruler" in out.columns
    assert (out["ruler"] == stage_eval.MAIN_RULER).all()


def test_channel_edge_empty_frame_still_has_ruler_column():
    """空表(无 recall_channels 列/无行)也要带 ruler 列——schema 在有/无数据两分支下一致,
    下游 `ce.to_csv()` 不会因为分支不同产出不同表头。"""
    out = stage_eval.channel_edge(pd.DataFrame(columns=["code"]), pd.DataFrame())
    assert "ruler" in out.columns
    assert out.empty


def test_render_stage_eval_title_names_current_ruler():
    """T12:各阶段 agent edge 标题必须点名当前 MAIN_RULER,不能让"T+2"读者默认它是 fwd_2_oc。"""
    lines = stage_eval.render_stage_eval({"date": "2026-07-01", "n_realized": 0, "stages": {}})
    md = "\n".join(lines)
    assert stage_eval.MAIN_RULER in md


def test_l4_ratings_prefer_decision_record(tmp_path):
    from autoresearch.scan.decision_record import (
        DecisionRecord,
        write_decision_records,
    )

    day = tmp_path / "2026-07-28"
    (day / "details").mkdir(parents=True)
    (day / "details" / "000001.md").write_text(
        "**Rating**: Overweight\n",
        encoding="utf-8",
    )
    record = DecisionRecord.build(
        analysis_date=day.name,
        contract_hash=None,
        code="000001",
        source_rating="Overweight",
        rubric_rating="Overweight",
        gate_states={},
        early_stop=None,
        ensemble_ratings=[],
        final_rating="Hold",
        proposal="HOLD",
        reason="verify:降级",
        evidence_refs=[],
        first_rejection_stage="VERIFY",
    )
    write_decision_records(day, [record])

    assert stage_eval._ratings_from_details(day.name, scan_root=tmp_path) == {
        "000001": "Hold"
    }


# ───────────────────────── ① L2 段 IC 键名(pr_20260716_004) ─────────────────────────
# 生产者此前写 ic_gbdt_score_t1、渲染读 ic_l2_score_t1 → L2 IC 恒 None。
# 修:统一 ic_l2_score_t1(L2 是确定性分层采样器,gbdt 是遗留命名);渲染对老 dict 回退旧键。


def test_l2_ic_produced_under_l2_key_and_rendered(tmp_path):
    """合成数据走一遍生产→渲染:evaluate 写 ic_l2_score_t1 非 None,render 读到同一键。"""
    date = "2126-06-18"          # 远期合成日:保证不撞 repo 本地真实 reports/scan 产物
    sdir = tmp_path / date
    sdir.mkdir(parents=True)
    codes = [f"{i:06d}" for i in range(25)]
    pd.DataFrame({"code": codes}).to_csv(sdir / "L1_recall_top1000.csv", index=False)
    pd.DataFrame({"code": codes, "gbdt_score": list(range(25))}).to_csv(
        sdir / "L2_gbdt_top200.csv", index=False)
    res = stage_eval.evaluate(date, scan_root=tmp_path,
                              realized=_realized(codes, [i * 0.001 for i in range(25)]))
    l2 = res["stages"]["L2"]
    assert l2["ic_l2_score_t1"] is not None and l2["ic_l2_score_t1"] > 0.99   # 单调合成 → IC≈1
    assert "ic_gbdt_score_t1" not in l2                                       # 旧键不再生产
    rendered = "\n".join(stage_eval.render_stage_eval(res))
    assert "l2_score IC 1.0" in rendered and "l2_score IC None" not in rendered


def test_l2_ic_render_falls_back_to_legacy_key():
    """历史已落盘 dict(2026-07-17 前)只有旧键 ic_gbdt_score_t1 → 渲染回退读旧键,不恒 None。"""
    res = {"date": "2026-07-01", "n_realized": 100, "stages": {
        "L2": {"n_in": 200, "n_out": 800, "mean_in": 0.01, "mean_out": 0.0, "lift": 0.01,
               "ic_gbdt_score_t1": 0.42}}}
    rendered = "\n".join(stage_eval.render_stage_eval(res))
    assert "l2_score IC 0.42" in rendered


# ───────────────────── ④ L3 头条剔保送(pr_20260716_002,stage_eval 侧) ─────────────────────
# lane∈{pinned, watchlist_trigger, carryover} 的 finalist 不是 L3 选的票:
# 头条 lift 只算真选(保送行整行剔除,不进 in 也不进 out),escorted 单独回显 n/mean。


def _l3_staging(tmp_path, date, finalists: pd.DataFrame):
    sdir = tmp_path / date
    sdir.mkdir(parents=True)
    pd.DataFrame({"code": ["000001", "000002", "000003", "000009"],
                  "conviction": [90, 85, 60, 70],
                  "fragility": [10, 15, 50, 20]}).to_csv(sdir / "L3_judged_full.csv", index=False)
    finalists.to_csv(sdir / "finalists.csv", index=False)
    return _realized(["000001", "000002", "000003", "000009"], [0.05, 0.03, -0.01, -0.20])


def test_l3_headline_excludes_escort_lanes(tmp_path):
    """含 1 只 pinned 的合成 finalists → 头条指标不含它;escorted 照实回显 n=1。"""
    date = "2126-06-19"
    realized = _l3_staging(tmp_path, date, pd.DataFrame(
        {"code": ["000001", "000002", "000009"], "lane": ["healthy", "", "pinned"]}))
    res = stage_eval.evaluate(date, scan_root=tmp_path, realized=realized)
    d = res["stages"]["L3"]
    assert d["n_in"] == 2 and abs(d["mean_in"] - 0.04) < 1e-9    # 000009(−20%)未混入真选头条
    assert d["n_out"] == 1                                        # 保送行也不落进"落选"组
    assert d["escorted"] == {"n": 1, "n_realized": 1, "mean": -0.2}
    rendered = "\n".join(stage_eval.render_stage_eval(res))
    assert "保送" in rendered


def test_l3_without_lane_column_keeps_full_finalists(tmp_path):
    """老数据 finalists.csv 无 lane 列 → 退化态:全量当真选,行为不变,无 escorted 键。"""
    date = "2126-06-20"
    realized = _l3_staging(tmp_path, date, pd.DataFrame({"code": ["000001", "000009"]}))
    res = stage_eval.evaluate(date, scan_root=tmp_path, realized=realized)
    d = res["stages"]["L3"]
    assert d["n_in"] == 2 and abs(d["mean_in"] - (0.05 - 0.20) / 2) < 1e-9
    assert "escorted" not in d
