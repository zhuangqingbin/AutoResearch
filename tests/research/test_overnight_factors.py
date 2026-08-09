"""隔夜因子第一批(Wave12-T29 / 设计稿 E3)—— 算法锁定 + PIT 断言,全离线合成数据。

三个新因子(`lhb_net_ratio_broker` / `limit_ladder` / `sealed_strength`)的公式与人口定义
在这里被手算期望值钉死;每个因子另配一条 **PIT 断言**:信号日 D 的读数只能由 ≤D 的数据决定,
把 D+1 塞成极端反向值,D 的读数必须**逐位不变**(否则就是前视)。

为什么 PIT 要用"毒化 D+1"而不是"检查代码里写没写 D":后者是读代码,前者是读行为——
把 `f"{D}"` 手滑成 `P[idx+1]` 这类错误只有行为断言逮得住(`wave35-mutation-testing` 家训:
"把这段删掉测试会红吗")。

主尺 `gap_c1_o2`(`autoresearch.common.ruler.MAIN_RULER`);本文件不涉及前瞻收益计算,
只锁**信号侧**的三个因子列。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import autoresearch.research.factor_lab as fl

# ───────────────────────── ① lhb_seat_net:席位分腿 ─────────────────────────


def _ti_frame() -> pd.DataFrame:
    """合成 top_inst 明细:一只票四种席位混排 + 一只只有营业部 + 一只只有机构。"""
    return pd.DataFrame({
        "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ", "000001.SZ",
                    "000002.SZ", "000003.SZ"],
        "exalter": ["机构专用", "机构专用", "国泰海通证券股份有限公司武汉紫阳东路证券营业部",
                    "深股通专用",
                    "华泰证券股份有限公司深圳益田路荣超商务中心证券营业部",
                    "机构专用"],
        "buy": [100.0, 0.0, 60.0, 1000.0, 5.0, 7.0],
        "sell": [30.0, 30.0, 10.0, 0.0, 25.0, 0.0],
        "net_buy": [100.0, -30.0, 50.0, 1000.0, -20.0, 7.0],
    })


def test_lhb_seat_net_splits_inst_and_broker_hand_computed():
    """手算:000001 机构 100+(−30)=70、营业部 50(深股通 1000 **不进**营业部腿)。"""
    out = fl.lhb_seat_net(_ti_frame()).set_index("code")
    assert out.loc["000001", "inst_net"] == pytest.approx(70.0)
    assert out.loc["000001", "broker_net"] == pytest.approx(50.0)


def test_lhb_seat_net_absent_class_is_nan_not_zero():
    """某类席位在该票**完全缺席** → NaN(未知/不适用),不是 0。

    语义与既有 `lhb_inst_net` 一致(groupby 后 left-merge,缺席即 NaN);写 0 会把
    "这票上榜时没有机构席位"伪造成"机构净买恰好为 0",污染 IC 的人口定义。
    """
    out = fl.lhb_seat_net(_ti_frame()).set_index("code")
    assert np.isnan(out.loc["000002", "inst_net"])          # 只有营业部
    assert out.loc["000002", "broker_net"] == pytest.approx(-20.0)
    assert out.loc["000003", "inst_net"] == pytest.approx(7.0)
    assert np.isnan(out.loc["000003", "broker_net"])        # 只有机构


def test_lhb_seat_net_northbound_excluded_from_both_legs():
    """北向通道(深/沪股通专用)既不是机构席位也不是营业部 —— 两腿都不含它。

    变异探针的靶子:把北向并进营业部腿,000001 的 broker_net 会从 50 变成 1050。
    """
    out = fl.lhb_seat_net(_ti_frame()).set_index("code")
    total = fl._num(_ti_frame()["net_buy"]).sum()           # 1107.0
    legs = out[["inst_net", "broker_net"]].sum().sum()      # 70+50-20+7 = 107.0
    assert total - legs == pytest.approx(1000.0), "北向 1000 必须落在两腿之外"


def test_lhb_seat_net_inst_leg_reproduces_legacy_formula():
    """新函数的机构腿 == 既有 `lhb_inst_net` 的内联口径(逐值),防"顺手改掉历史读数"。"""
    ti = _ti_frame()
    t = ti.assign(code=fl._code6(ti["ts_code"]))
    inst = t[t["exalter"].astype(str).str.contains("机构专用", na=False)]
    legacy = inst.groupby("code")["net_buy"].sum()
    new = fl.lhb_seat_net(ti).set_index("code")["inst_net"].dropna()
    pd.testing.assert_series_equal(new.sort_index(), legacy.sort_index(),
                                   check_names=False, check_dtype=False)


def test_lhb_seat_net_empty_input_is_empty_frame():
    out = fl.lhb_seat_net(pd.DataFrame(columns=["ts_code", "exalter", "net_buy"]))
    assert list(out.columns) == ["code", "inst_net", "broker_net"]
    assert len(out) == 0


# ───────────────────────── ② sealed_strength:封板强度 ─────────────────────────


def _piv_for_sealed() -> tuple[dict, list[str]]:
    """两日价格面板。D=20260105 各种封板形态;D+1=20260106 **反向毒化**(PIT 探针用)。

    昨收统一 10.00 → 主板板价 11.00、创业板(30xxxx)板价 12.00。
    """
    codes = ["000001",       # 一字板:high=low=close=11 → 振幅 0 → strength 1.0
             "000002",       # 封板但盘中下探到 10.50 → 振幅 5% → 1 − 5/10 = 0.5
             "000003",       # 封板但盘中下探到 9.00 → 振幅 20% → 1 − 20/10 = −1.0
             "000004",       # 涨停价但尾盘炸板(close 10.5 < high 11) → 非封板 → NaN
             "000005",       # 只涨 5% → 非封板 → NaN
             "300001"]       # 创业板一字板 20cm:close=high=low=12 → strength 1.0
    D, D1 = "20260105", "20260106"
    frames = {
        "close":   {D: [11.0, 11.0, 11.0, 10.5, 10.5, 12.0], D1: [9.0, 9.0, 9.0, 9.0, 9.0, 9.0]},
        "high":    {D: [11.0, 11.0, 11.0, 11.0, 10.6, 12.0], D1: [9.0, 9.0, 9.0, 9.0, 9.0, 9.0]},
        "low":     {D: [11.0, 10.5, 9.00, 10.0, 10.4, 12.0], D1: [9.0, 9.0, 9.0, 9.0, 9.0, 9.0]},
        "open":    {D: [11.0, 10.8, 10.0, 10.2, 10.4, 12.0], D1: [9.0, 9.0, 9.0, 9.0, 9.0, 9.0]},
        "pct_chg": {D: [10.0, 10.0, 10.0, 5.00, 5.00, 20.0], D1: [-10.0] * 6},
        "amount":  {D: [1e5] * 6, D1: [1e5] * 6},
    }
    piv = {k: pd.DataFrame(v, index=pd.Index(codes, name="code")) for k, v in frames.items()}
    return piv, [D, D1]


def test_sealed_strength_hand_computed():
    """手算四档:一字 1.0 / 振幅 5% → 0.5 / 振幅 20% → −1.0 / 创业板一字 1.0。"""
    piv, (D, _) = _piv_for_sealed()
    s = fl.sealed_strength(piv, D)
    assert s["000001"] == pytest.approx(1.0)
    assert s["000002"] == pytest.approx(0.5)
    assert s["000003"] == pytest.approx(-1.0)
    assert s["300001"] == pytest.approx(1.0)


def test_sealed_strength_nan_for_unsealed_population():
    """人口 = **仅当日封板票**。炸板(close<high)与未达板幅的票一律 NaN,不是 0。"""
    piv, (D, _) = _piv_for_sealed()
    s = fl.sealed_strength(piv, D)
    assert np.isnan(s["000004"]), "尾盘炸板不属于封板人口"
    assert np.isnan(s["000005"]), "只涨 5% 不属于封板人口"


def test_sealed_strength_board_limit_is_per_code():
    """创业板用 20cm 板幅归一 —— 若误用主板 10%,同样振幅会算出完全不同的强度。

    构造:300001 昨收 10 → 收 12(+20%),盘中最低 11 → 振幅 10%。
    正确(lim=20):1 − 10/20 = **0.5**;错用 lim=10 则是 1 − 10/10 = 0.0。
    """
    piv, (D, _) = _piv_for_sealed()
    piv["low"].loc["300001", D] = 11.0
    s = fl.sealed_strength(piv, D)
    assert s["300001"] == pytest.approx(0.5)


def test_sealed_strength_pit_next_day_is_invisible():
    """**PIT**:D+1 全线跌停(close=high=low=9,pct_chg=−10)也不能动 D 的读数。"""
    piv, (D, D1) = _piv_for_sealed()
    before = fl.sealed_strength(piv, D).copy()
    for k in piv:
        piv[k][D1] = [-999.0] * len(piv[k])          # D+1 彻底毒化
    after = fl.sealed_strength(piv, D)
    pd.testing.assert_series_equal(before, after, check_names=False)


# ───────────────────────── ③ limit_ladder:连板高度 ─────────────────────────


def _write_limit_partition(lake_root, day: str, rows: list[dict]) -> None:
    d = lake_root / "limit_list_d"
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(d / f"{day}.parquet", index=False)


def test_limit_ladder_reads_only_up_rows(tmp_path):
    """人口 = 当日**涨停**(limit=='U');炸板 Z / 跌停 D 不产读数。"""
    _write_limit_partition(tmp_path, "20260105", [
        {"ts_code": "000001.SZ", "limit": "U", "limit_times": 3.0},
        {"ts_code": "000002.SZ", "limit": "U", "limit_times": 1.0},
        # 炸板/跌停行**故意带上非空 limit_times**:真数据里它们目前恰好是 NaN,若靠
        # `limit_times.notna()` 过滤人口,今天看起来一样、端点哪天补上数就会把炸板票
        # 混进涨停人口 —— 这条用例让"按 limit=='U' 过滤"与"按 NaN 过滤"可区分。
        {"ts_code": "000003.SZ", "limit": "Z", "limit_times": 5.0},      # 炸板
        {"ts_code": "000004.SZ", "limit": "D", "limit_times": 2.0},      # 跌停
    ])
    out = fl.limit_ladder_frame("20260105", lake_root=tmp_path).set_index("code")
    assert out.loc["000001", "limit_ladder"] == pytest.approx(3.0)
    assert out.loc["000002", "limit_ladder"] == pytest.approx(1.0)
    assert "000003" not in out.index and "000004" not in out.index


def test_limit_ladder_one_row_per_code(tmp_path):
    """同一票多行(端点偶发重复)→ 只出一行,取最大高度;否则下游 merge 会把帧撑长、
    毁掉 `amt_pos` 与 `f` 的位置对齐。"""
    _write_limit_partition(tmp_path, "20260105", [
        {"ts_code": "000001.SZ", "limit": "U", "limit_times": 2.0},
        {"ts_code": "000001.SZ", "limit": "U", "limit_times": 3.0},
    ])
    out = fl.limit_ladder_frame("20260105", lake_root=tmp_path)
    assert len(out) == 1
    assert out["limit_ladder"].iloc[0] == pytest.approx(3.0)


def test_limit_ladder_missing_partition_is_empty_not_raise(tmp_path):
    """湖只覆盖 78/132 成型日 —— 缺分区必须安静返回空表(该日无此因子),不得抛。"""
    out = fl.limit_ladder_frame("20250523", lake_root=tmp_path)
    assert list(out.columns) == ["code", "limit_ladder"]
    assert len(out) == 0


def test_limit_ladder_pit_next_day_partition_is_invisible(tmp_path):
    """**PIT**:D+1 分区把同一批票写成 9 连板,D 的读数必须仍是 D 分区的值。"""
    _write_limit_partition(tmp_path, "20260105", [
        {"ts_code": "000001.SZ", "limit": "U", "limit_times": 1.0}])
    _write_limit_partition(tmp_path, "20260106", [
        {"ts_code": "000001.SZ", "limit": "U", "limit_times": 9.0}])
    out = fl.limit_ladder_frame("20260105", lake_root=tmp_path).set_index("code")
    assert out.loc["000001", "limit_ladder"] == pytest.approx(1.0)


# ───────────────────────── ④ 注册 + 端到端接线 ─────────────────────────


def test_new_factors_registered_in_candidates():
    """三个新列必须挂进 `CANDIDATES` —— 那是 gap 晋升判据族读得到它们的**唯一**入口。

    (同 `test_factor_lab.py` 的反转三因子注册测试:生产者建了列、消费者不认识,
    是本仓库 FN-1 家族最常见的病。)
    """
    cand = dict(fl.CANDIDATES)
    for name in ("lhb_net_ratio_broker", "limit_ladder", "sealed_strength"):
        assert name in cand, f"{name} 未注册进 CANDIDATES(gap 判据族读不到它)"
        assert cand[name] == +1, f"{name} 的预注册方向假设是 +1(见 2026-08-08 报告 §0.3)"


def _synth_cache(tmp_path, n: int = 320, n_days: int = 15):
    """最小可跑的 factor_lab 缓存:n 只票 × n_days 个面板日,D 取最后一天。"""
    codes = [f"{i:06d}" for i in range(1, n + 1)]
    ts = [f"{c}.SZ" for c in codes]
    P = [f"202601{d:02d}" for d in range(1, n_days + 1)]
    D = P[-1]
    rng = np.random.default_rng(11)
    rows = []
    for d in P:
        px = 10.0 + rng.normal(0, 0.2, n)
        rows.append(pd.DataFrame({"ts_code": ts, "trade_date": d, "open": px, "high": px * 1.01,
                                  "low": px * 0.99, "close": px, "pct_chg": rng.normal(0, 1, n),
                                  "amount": np.full(n, 5e5)}))
    long = pd.concat(rows, ignore_index=True)
    long["code"] = fl._code6(long["ts_code"])
    piv = {f: long.pivot_table(index="code", columns="trade_date", values=f)
           for f in ("open", "high", "low", "close", "pct_chg", "amount")}
    cache = tmp_path / "cache"
    for ep, frame in {
        "daily_basic": pd.DataFrame({"ts_code": ts, "close": 10.0, "turnover_rate": 2.0,
                                     "volume_ratio": 1.1, "pe_ttm": 20.0, "pb": 2.0,
                                     "dv_ratio": 1.0, "total_mv": 1e6, "circ_mv": 8e5}),
        "stk_factor_pro": pd.DataFrame({"ts_code": ts, "close": 10.0, "ma_qfq_5": 9.9,
                                        "ma_qfq_10": 9.8, "ma_qfq_20": 9.7, "ma_qfq_60": 9.6,
                                        "rsi_qfq_6": 55.0, "rsi_qfq_12": 52.0, "macd_qfq": 0.1}),
        "cyq_perf": pd.DataFrame({"ts_code": ts, "winner_rate": 50.0, "cost_15pct": 9.0,
                                  "cost_50pct": 10.0, "cost_85pct": 11.0, "weight_avg": 10.0}),
        "moneyflow": pd.DataFrame({"ts_code": ts, "buy_sm_amount": 100.0, "sell_sm_amount": 90.0,
                                   "buy_lg_amount": 200.0, "sell_lg_amount": 150.0,
                                   "buy_elg_amount": 50.0, "sell_elg_amount": 40.0,
                                   "net_mf_amount": 70.0}),
        # 龙虎榜:头两只票上榜(机构 + 营业部 + 北向)
        "top_inst": pd.DataFrame({
            "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ", "000002.SZ"],
            "exalter": ["机构专用", "某某证券营业部", "深股通专用", "另一家证券营业部"],
            "buy": [10.0, 20.0, 999.0, 5.0], "sell": [4.0, 5.0, 0.0, 1.0],
            "net_buy": [6.0, 15.0, 999.0, 4.0]}),
        # 两融(既有 `rz_buy_intensity` 的源;供其 PIT 断言用)
        "margin_detail": pd.DataFrame({"ts_code": ts, "rzye": 1e7, "rqye": 0.0,
                                       "rzmre": 2.5e7, "rzche": 0.0, "rzrqye": 1e7}),
    }.items():
        (cache / ep).mkdir(parents=True, exist_ok=True)
        frame.to_pickle(cache / ep / f"{D}.pkl")
    basic = pd.DataFrame({"code": codes, "name": [f"票{i}" for i in range(n)],
                          "list_date": "20200101", "market": "主板", "industry": "银行"})
    return cache, piv, P, D, basic


def test_factor_frame_wires_three_new_columns(tmp_path, monkeypatch):
    """**接线活体**:三个新列真的出现在 `factor_frame` 的产物里,且数值可手算复核。

    这条测的是"生产者接了线"——`pinned-l3-discarded-forcefullcard-deadcode` 家族的病
    (函数写好了、`CANDIDATES` 也挂了,但 `factor_frame` 里没人调用)只有它逮得住。
    """
    cache, piv, P, D, basic = _synth_cache(tmp_path)
    monkeypatch.setattr(fl, "CACHE", cache)
    monkeypatch.setattr(fl, "LAKE_ROOT", tmp_path / "lake")
    _write_limit_partition(tmp_path / "lake", D, [
        {"ts_code": "000002.SZ", "limit": "U", "limit_times": 4.0}])
    # 000003 做成主板一字板(昨收 10 → 11),供 sealed_strength 断言
    prev = P[-2]
    for col, val in (("close", 11.0), ("high", 11.0), ("low", 11.0), ("open", 11.0)):
        piv[col].loc["000003", D] = val
    piv["close"].loc["000003", prev] = 10.0
    piv["pct_chg"].loc["000003", D] = 10.0

    fr = fl.factor_frame(D, piv, P, basic, cap_floor=30.0, fwd=10)
    assert fr is not None
    for col in ("lhb_net_ratio_broker", "limit_ladder", "sealed_strength"):
        assert col in fr.columns, f"factor_frame 没产出 {col}(生产者未接线)"
    row = fr.set_index("code")
    amt_yuan = 5e5 * 1e3                                    # 千元 → 元
    assert row.loc["000001", "lhb_net_ratio_broker"] == pytest.approx(15.0 / amt_yuan)
    assert row.loc["000002", "limit_ladder"] == pytest.approx(4.0)
    assert row.loc["000003", "sealed_strength"] == pytest.approx(1.0)
    # 人口:未上榜/未涨停/未封板的票是 NaN,不是 0
    assert np.isnan(row.loc["000010", "lhb_net_ratio_broker"])
    assert np.isnan(row.loc["000010", "limit_ladder"])
    assert np.isnan(row.loc["000010", "sealed_strength"])


def test_factor_frame_survives_absent_optional_sources(tmp_path, monkeypatch):
    """湖缺 `limit_list_d` 分区 / 当日无龙虎榜 → 帧照出,对应列全 NaN(降级不炸,也不留痕造假)。"""
    cache, piv, P, D, basic = _synth_cache(tmp_path)
    (cache / "top_inst" / f"{D}.pkl").unlink()
    monkeypatch.setattr(fl, "CACHE", cache)
    monkeypatch.setattr(fl, "LAKE_ROOT", tmp_path / "lake")       # 湖目录根本不存在
    fr = fl.factor_frame(D, piv, P, basic, cap_floor=30.0, fwd=10)
    assert fr is not None
    assert fr["limit_ladder"].isna().all()
    assert "lhb_net_ratio_broker" not in fr.columns or fr["lhb_net_ratio_broker"].isna().all()


def test_rz_buy_intensity_pit_next_day_margin_is_invisible(tmp_path, monkeypatch):
    """**PIT · `rz_buy_intensity`**(任务书 Step 1 说的是「每因子」,上轮漏了这个既有列)。

    D+1 的 `margin_detail` 快照写成极端值,D 的读数必须逐位不变 —— 它是**重验**对象而不是
    新建列,但「只重验」不等于「不必证明它无前视」。
    """
    cache, piv, P, D, basic = _synth_cache(tmp_path)
    monkeypatch.setattr(fl, "CACHE", cache)
    monkeypatch.setattr(fl, "LAKE_ROOT", tmp_path / "lake")
    before = fl.factor_frame(D, piv, P, basic, cap_floor=30.0, fwd=10)
    assert before is not None
    base = before.set_index("code")["rz_buy_intensity"].copy()
    # 手算:rzmre 2.5e7 / (成交额 5e5 千元 = 5e8 元) = 0.05
    assert base.loc["000001"] == pytest.approx(0.05)
    nxt = f"{int(D) + 1}"
    poison = pd.read_pickle(cache / "margin_detail" / f"{D}.pkl").assign(rzmre=9.9e12)
    poison.to_pickle(cache / "margin_detail" / f"{nxt}.pkl")
    after = fl.factor_frame(D, piv, P, basic, cap_floor=30.0, fwd=10)
    pd.testing.assert_series_equal(base, after.set_index("code")["rz_buy_intensity"],
                                   check_names=False)
