"""三旧尺结论 gap 重验(Wave12-T30 / 设计稿 E2)—— 三节算法锁定,全离线合成数据。

被锁的是**算法**,不是读数:分桶边界、超额基准的人口、相位归属、成熟度判定。真数据读数落
`docs/research/2026-08-08-overnight-evidence-gap.md`,那份报告可以随数据变;这里的期望值是
手算的常数,变了就是算法被改了。

分桶边界的变异探针是本文件的头等大事:①节的结论(追当日大涨在隔夜尺下是正是负)直接决定
下游 E4b 通道的设计空间,而「把 ≥9.5% 写成 >9.5%」这种一字之差会静默改变涨停票的归属
(A 股主板涨停恰好落在 10.0%,创业板 20.0%,边界票不是零星几只)。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from autoresearch.research import overnight_evidence as oe

# ───────────────────────── ① 当日涨幅分桶 × 次日 gap 超额 ─────────────────────────


def test_chase_bucket_labels_are_left_closed():
    """分桶边界:≥9.5 / [5, 9.5) / [2, 5) / <2 —— **左闭右开**,边界值归上一档。"""
    pct = pd.Series([10.0, 9.5, 9.49, 5.0, 4.99, 2.0, 1.99, -3.0])
    got = list(oe.chase_bucket(pct))
    assert got == ["≥9.5%", "≥9.5%", "5%~9.5%", "5%~9.5%", "2%~5%", "2%~5%", "其余", "其余"]


def test_chase_bucket_nan_is_unbucketed():
    """当日涨幅缺失(停牌/新股无前收)→ 不进任何桶,不许折进「其余」凑样本。"""
    got = oe.chase_bucket(pd.Series([np.nan, 3.0]))
    assert pd.isna(got.iloc[0])
    assert got.iloc[1] == "2%~5%"


def _synthetic_day() -> tuple[pd.DataFrame, pd.DataFrame]:
    """当日涨幅帧 + gap 帧。E 是 T+1 收盘封涨停(买不进)→ 必须整只剔除。"""
    pct = pd.DataFrame({"code": ["A", "B", "C", "D", "E"],
                        "pct_chg": [10.0, 6.0, 3.0, 0.0, 10.0]})
    gap = pd.DataFrame({"code": ["A", "B", "C", "D", "E"],
                        "gap_c1_o2": [0.02, 0.01, 0.00, -0.01, 0.05],
                        "eligible_gap": [True, True, True, True, False]})
    return pct, gap


def test_day_chase_rows_hand_computed_excess():
    """手算:合格集 {A,B,C,D} 等权均值 = (0.02+0.01+0.00−0.01)/4 = **0.005**;
    `rel_gap_market` = 个股 gap − 0.005。"""
    pct, gap = _synthetic_day()
    rows = oe.day_chase_rows("20260105", pct, gap).set_index("code")
    assert rows.loc["A", "rel_gap_market"] == pytest.approx(0.015)
    assert rows.loc["B", "rel_gap_market"] == pytest.approx(0.005)
    assert rows.loc["C", "rel_gap_market"] == pytest.approx(-0.005)
    assert rows.loc["D", "rel_gap_market"] == pytest.approx(-0.015)
    assert list(rows["bucket"]) == ["≥9.5%", "5%~9.5%", "2%~5%", "其余"]


def test_day_chase_rows_drops_unbuyable_from_both_numerator_and_benchmark():
    """**入场旗单点**:T+1 收盘封涨停的 E 既不进分桶人口,也不进基准分母。

    这条是 C1 家训的直系后代 —— 若 E 只从分子剔、留在分母里,基准会被一个"买不进的
    +5% 隔夜"抬高,所有桶的超额被系统性压低。
    """
    pct, gap = _synthetic_day()
    rows = oe.day_chase_rows("20260105", pct, gap)
    assert "E" not in set(rows["code"])
    # 若 E 混进基准,均值会变成 (0.02+0.01+0+(-0.01)+0.05)/5 = 0.014 → A 的超额变 0.006
    assert rows.set_index("code").loc["A", "rel_gap_market"] == pytest.approx(0.015)


def test_entry_exclusion_counts_big_movers_and_the_buyable_subset():
    """人口口径必须和读数一起报:合成日里 A、E 都 ≥9.5%,但 E 在 T+1 收盘封板买不进
    → (2, 1)。手算,变异靶子 = 把 `edge` 或合格集判据写错。"""
    pct, gap = _synthetic_day()
    assert oe.entry_exclusion(pct, gap) == (2, 1)


def test_entry_exclusion_edge_is_left_closed_and_matches_top_bucket():
    """默认 `edge` 必须**就是**最高桶的边界 —— 两处各写一个数字迟早会漂。"""
    assert oe.entry_exclusion.__defaults__[0] == oe.CHASE_EDGES[0]
    pct = pd.DataFrame({"code": ["A", "B"], "pct_chg": [9.5, 9.49]})
    gap = pd.DataFrame({"code": ["A", "B"], "gap_c1_o2": [0.01, 0.01],
                        "eligible_gap": [True, True]})
    assert oe.entry_exclusion(pct, gap) == (1, 1)


def test_entry_exclusion_empty_inputs():
    assert oe.entry_exclusion(pd.DataFrame(), pd.DataFrame()) == (0, 0)


def test_chase_rows_all_accumulates_exclusion_into_caller_list(tmp_path, monkeypatch):
    """`excl` 累加器必须真被写 —— 否则报告里的人口口径行会永远是 0/0(死统计)。"""
    pct, gap = _synthetic_day()
    monkeypatch.setattr(oe.rc, "lake_days", lambda lake=None: ["20260105", "20260106", "20260107"])
    monkeypatch.setattr(oe.rc, "_read_lake_cols", lambda d, c, lake=None: pct)
    monkeypatch.setattr(oe.rc, "gap_frame", lambda d, days=None, lake=None: gap)
    excl = [0, 0]
    rows = oe.chase_rows_all(excl=excl)
    assert len(rows) == 4                       # 只有 20260105 可用(需 D+2)
    assert excl == [2, 1]


def test_day_chase_rows_carries_date_for_cluster_bootstrap():
    """每行必须带 `date` —— 区间按**扫描日**聚簇重采样,行级 bootstrap 会把区间做窄到假。"""
    pct, gap = _synthetic_day()
    rows = oe.day_chase_rows("20260105", pct, gap)
    assert set(rows["date"]) == {"20260105"}


def test_bucket_intervals_point_and_cluster_count():
    """两日合成:桶点估计 = 该桶全部行的均值;`n_days` = 该桶覆盖的**日**数(cluster 数)。"""
    rows = pd.DataFrame({
        "date": ["d1", "d1", "d2", "d2"],
        "bucket": ["≥9.5%", "其余", "≥9.5%", "其余"],
        "rel_gap_market": [0.02, -0.01, 0.04, -0.03],
    })
    tbl = oe.bucket_intervals(rows).set_index("bucket")
    assert tbl.loc["≥9.5%", "point"] == pytest.approx(0.03)
    assert tbl.loc["≥9.5%", "n_days"] == 2
    assert tbl.loc["其余", "point"] == pytest.approx(-0.02)


def test_bucket_intervals_marks_immature_below_subgroup_floor():
    """统一成熟门不放松:细分 `n_days < 10` → `IMMATURE`,不外推、不当结论用。"""
    rows = pd.DataFrame({"date": [f"d{i}" for i in range(9)],
                         "bucket": ["≥9.5%"] * 9, "rel_gap_market": [0.01] * 9})
    tbl = oe.bucket_intervals(rows)
    assert tbl["maturity"].iloc[0] == "IMMATURE"
    rows2 = pd.DataFrame({"date": [f"d{i}" for i in range(12)],
                          "bucket": ["≥9.5%"] * 12,
                          "rel_gap_market": list(np.linspace(-0.01, 0.03, 12))})
    assert oe.bucket_intervals(rows2)["maturity"].iloc[0] == "MATURE"


def test_bucket_intervals_ci_excludes_zero_only_when_it_should():
    """区间跨 0 → `crosses_zero=True`;全同号且量级稳 → False。三态纪律,不把"不显著"讲成"等价"。"""
    same = pd.DataFrame({"date": [f"d{i}" for i in range(20)], "bucket": ["x"] * 20,
                         "rel_gap_market": [-0.02] * 20})
    assert bool(oe.bucket_intervals(same)["crosses_zero"].iloc[0]) is False
    rng = np.random.default_rng(3)
    mixed = pd.DataFrame({"date": [f"d{i}" for i in range(20)], "bucket": ["x"] * 20,
                          "rel_gap_market": rng.normal(0, 0.05, 20)})
    assert bool(oe.bucket_intervals(mixed)["crosses_zero"].iloc[0]) is True


# ───────────────────────── ② 通道 × 相位 ─────────────────────────


def _attr(with_gap: bool) -> pd.DataFrame:
    base = {"code": ["000001", "000002", "000003"], "fwd_2_oc": [0.01, -0.02, 0.03]}
    if with_gap:
        base["gap_c1_o2"] = [0.01, -0.01, 0.02]
        base["buyable_c1"] = [True, True, True]
    return pd.DataFrame(base)


def test_repair_attribution_native_when_main_ruler_present():
    out, status = oe.repair_attribution("2026-07-01", _attr(True))
    assert status == "native"
    assert out["gap_c1_o2"].notna().all()


def test_repair_attribution_fills_from_lake_when_column_absent(monkeypatch):
    """**C1 修复的核心**:历史 attribution 缺主尺列 → 用湖现算补齐,而不是整天丢掉。

    实测 3 天(2026-06-18 / 06-22 / 07-07)只有旧尺列,retro 回填漏了它们。
    """
    gap = pd.DataFrame({"code": ["000001", "000002", "000003"],
                        "gap_c1_o2": [0.05, -0.05, 0.0],
                        "buyable_c1": [True, False, True],
                        "eligible_gap": [True, False, True]})
    monkeypatch.setattr(oe.rc, "gap_frame", lambda d, days=None, lake=None: gap)
    out, status = oe.repair_attribution("2026-06-18", _attr(False))
    assert status == "lake_filled"
    assert list(out["gap_c1_o2"]) == [0.05, -0.05, 0.0]
    assert "buyable_c1" in out.columns


def test_repair_attribution_unusable_when_lake_also_empty(monkeypatch):
    """湖也补不出来 → `unusable`,**显式计数**,不静默跳过。"""
    monkeypatch.setattr(oe.rc, "gap_frame",
                        lambda d, days=None, lake=None: pd.DataFrame(columns=["code"]))
    _, status = oe.repair_attribution("2026-06-18", _attr(False))
    assert status == "unusable"


def test_repair_attribution_does_not_mutate_input(monkeypatch):
    """历史产物只在内存里补,输入帧不得被就地改(铁律:历史产物不改写)。

    code 故意用 **ts_code 形态**(`000001.SZ`)—— 用已经规范化的 6 位码写这条测试是没有
    鉴别力的:`code` 列的规范化对它们是恒等变换,就地改与拷贝改**结果一样**,探针照过。
    (这条正是被变异探针 R2 逮到的自曝盲区。)
    """
    gap = pd.DataFrame({"code": ["000001"], "gap_c1_o2": [0.05],
                        "buyable_c1": [True], "eligible_gap": [True]})
    monkeypatch.setattr(oe.rc, "gap_frame", lambda d, days=None, lake=None: gap)
    src = pd.DataFrame({"code": ["000001.SZ", "000002.SZ"], "fwd_2_oc": [0.01, -0.02]})
    before = src.copy()
    out, status = oe.repair_attribution("2026-06-18", src)
    assert status == "lake_filled"
    pd.testing.assert_frame_equal(src, before), "输入帧被就地改了"
    assert list(out["code"]) == ["000001", "000002"], "拷贝上才做规范化"


def test_channel_phase_rows_accounts_for_every_day(monkeypatch, tmp_path):
    """② 的人口必须有分母:native / lake_filled / unusable 逐类计数,丢弃的日子点名。

    这是 C1 的回归锁 —— 原实现靠末尾 `dropna` 把缺主尺列的整天抹掉,零告警零计数,
    报告却写「覆盖 28 个扫描日」像是普查。
    """
    ca = pytest.importorskip("autoresearch.research.channel_audit")
    ch = pd.DataFrame({"channel": ["momentum", "momentum"], "code": ["000001", "000002"]})
    loaded = {"2026-06-18": (ch, _attr(False)), "2026-06-23": (ch, _attr(True))}
    monkeypatch.setattr(ca, "_scan_dates", lambda root, days: list(loaded))
    monkeypatch.setattr(ca, "_load_day", lambda root, d, variant=None: loaded[d])
    monkeypatch.setattr(oe.rc, "lake_days", lambda lake=None: [])
    monkeypatch.setattr(oe.rc, "gap_frame",
                        lambda d, days=None, lake=None: pd.DataFrame(columns=["code"]))
    cov: dict = {}
    oe.channel_phase_rows(tmp_path, phases={"2026-06-18": "高潮", "2026-06-23": "退潮"}, cov=cov)
    assert cov["n_dirs"] == 2 and cov["n_loaded"] == 2
    assert cov["n_native"] == 1
    assert cov["n_unusable"] == 1
    assert cov["dropped"] == ["2026-06-18"], "补不出来的日子必须被点名,不是静默消失"


def test_phase_side_partition_matches_0804_recipe():
    """相位归属**逐字照抄** 2026-08-04 报告口径 A:发酵/高潮/修复=上涨侧,退潮/冰点=回撤侧。"""
    assert oe.phase_side("发酵") == "上涨" and oe.phase_side("高潮") == "上涨"
    assert oe.phase_side("修复") == "上涨"
    assert oe.phase_side("退潮") == "回撤" and oe.phase_side("冰点") == "回撤"
    assert oe.phase_side("未知") == "未知"


def test_phase_side_recovery_variants_for_sensitivity():
    """敏感性口径 B/C(「修复」归回撤 / 剔除修复)—— 08-04 报告 §3 的三口径,一个都不能少。"""
    assert oe.phase_side("修复", recovery="down") == "回撤"
    assert oe.phase_side("修复", recovery="drop") == "未知"


def test_side_intervals_splits_and_counts_days():
    """按侧聚合:点估计 = 该侧全部日读数均值,n = 该侧**日**数。"""
    daily = pd.DataFrame({
        "date": ["d1", "d2", "d3", "d4"],
        "phase": ["高潮", "发酵", "退潮", "冰点"],
        "ux": [-0.03, -0.01, 0.00, 0.02],
    })
    tbl = oe.side_intervals(daily, "ux").set_index("side")
    assert tbl.loc["上涨", "point"] == pytest.approx(-0.02)
    assert tbl.loc["上涨", "n_days"] == 2
    assert tbl.loc["回撤", "point"] == pytest.approx(0.01)


# ───────────────────────── ③ 温度计相位 × 次日市场 gap ─────────────────────────


def test_market_gap_by_day_is_eligible_equal_weight():
    """市场 gap = 当日**可执行**全集等权均值(买不进的票不进基准)。手算 = 0.005。"""
    _, gap = _synthetic_day()
    assert oe.market_gap_of(gap) == pytest.approx(0.005)


def test_market_gap_of_empty_is_none():
    empty = pd.DataFrame(columns=["code", "gap_c1_o2", "eligible_gap"])
    assert oe.market_gap_of(empty) is None


def test_phase_gap_table_is_per_phase_not_per_side():
    """③ 节按**五相位**逐相位报(不是两侧)—— 它要回答的是"隔夜 edge 是不是相位现象"。"""
    rows = pd.DataFrame({"date": [f"d{i}" for i in range(15)],
                         "phase": ["高潮"] * 5 + ["退潮"] * 5 + ["冰点"] * 5,
                         "market_gap": [0.01] * 5 + [-0.01] * 5 + [0.03] * 5})
    tbl = oe.phase_gap_table(rows)
    assert set(tbl["phase"]) == {"高潮", "退潮", "冰点"}
    got = tbl.set_index("phase")
    assert got.loc["冰点", "point"] == pytest.approx(0.03)
    assert (got["maturity"] == "IMMATURE").all(), "每相位仅 5 日 → 全部 IMMATURE"


# ───────────────────────── 报告体裁(顶行铁律) ─────────────────────────


def test_render_pins_the_shadow_only_banner():
    """报告顶部必须写死影子取证声明 —— gap 尺下追涨若不再为负,极易被误读成「可以追涨了」。

    这一行不是文案洁癖:它是「负结果不能靠一份报告翻案」这条制度的**载体**,
    删掉它测试必须红。
    """
    md = oe.render(oe._empty_result())
    head = "\n".join(md.splitlines()[:12])
    assert "影子取证专用" in head
    assert "生产铁律未变" in head
    assert "实验注册表" in head


def test_render_lists_three_downstream_triggers():
    """报告尾必须逐条给三个下游触发器状态(E4b / F4 / E3),缺一条就是断了调度链。"""
    md = oe.render(oe._empty_result())
    assert "E4b" in md and "F4" in md and "E3" in md


def test_render_never_prints_literal_none():
    """空 result 也要读得通:缺失的计数印「—」,不许把 `None` 字面量漏进报告正文。"""
    md = oe.render(oe._empty_result())
    assert "None" not in md


# ───────────────────────── 裁决器(报告结论由数据算,不是手写散文) ─────────────────────────


def test_iv_treats_nan_bounds_as_missing_not_as_a_computed_interval():
    """`to_dict("records")` 会把浮点列里的 `None` 静默变成 `NaN` —— 只判 `is None` 的渲染
    会把「单日无区间」印成 `[—, —] 跨 0`,读起来像"算过了但不显著"。真数据上 ③节的
    「冰点」相位(n_days=1)正是这一行。"""
    txt = oe._iv({"point": -0.0017, "lo": float("nan"), "hi": float("nan"),
                  "n_days": 1, "crosses_zero": True})
    assert "无跨日方差" in txt and "跨 0" not in txt


def _sides(up_pt, up_lo, up_hi, dn_lo, dn_hi, n=14):
    return [{"side": "回撤", "point": 0.0, "lo": dn_lo, "hi": dn_hi, "n_days": n,
             "crosses_zero": dn_lo < 0 < dn_hi, "maturity": "MATURE"},
            {"side": "上涨", "point": up_pt, "lo": up_lo, "hi": up_hi, "n_days": n,
             "crosses_zero": up_lo < 0 < up_hi, "maturity": "MATURE"}]


def test_side_verdict_confirmed_needs_both_legs_of_the_old_claim():
    """08-04 的命题是「上涨侧整区间为负 ∧ 回撤侧跨 0」—— 两条同时成立才 CONFIRMED。"""
    assert oe._side_verdict(_sides(-0.005, -0.009, -0.002, -0.002, 0.002))[0] == "CONFIRMED"


def test_side_verdict_unknown_when_up_side_ci_crosses_zero():
    """上涨侧区间跨 0 → `UNKNOWN`,**不是** CONFIRMED。

    这是本文件最重要的一条判别:点估计仍为负、但区间跨 0 时,把它读成"旧结论仍成立"
    正是 `stats` 模块三态纪律要治的病(「不显著」被当成「等价」讲的镜像)。
    """
    assert oe._side_verdict(_sides(-0.004, -0.015, 0.009, -0.004, 0.003))[0] == "UNKNOWN"


def test_side_verdict_partial_when_both_sides_negative():
    """两侧都不跨 0 → 不是"相位条件性",是全时为负 → `PARTIAL`(处方完全不同)。"""
    assert oe._side_verdict(_sides(-0.005, -0.009, -0.002, -0.008, -0.001))[0] == "PARTIAL"


def test_side_verdict_immature_blocks_any_conclusion():
    recs = _sides(-0.005, -0.009, -0.002, -0.002, 0.002, n=9)
    for r in recs:
        r["maturity"] = "IMMATURE"
    assert oe._side_verdict(recs)[0] == "IMMATURE"


def _phase_rows(spec: dict) -> pd.DataFrame:
    """{phase: [逐日 market_gap]} → 逐日长表。"""
    out = []
    for ph, vals in spec.items():
        for i, v in enumerate(vals):
            out.append({"date": f"{ph}{i}", "phase": ph, "market_gap": v})
    return pd.DataFrame(out)


def _phase_recs(spec: dict) -> list[dict]:
    return [{"phase": ph, "point": float(np.mean(v)), "lo": -0.05, "hi": 0.05,
             "n_days": len(v), "maturity": "MATURE" if len(v) >= 10 else "IMMATURE"}
            for ph, v in spec.items()]


def test_thermo_verdict_uses_difference_interval_not_ci_overlap():
    """③节判据 = **两两均值差**的区间是否含 0,不是边际 CI 重不重叠。

    构造:两组边际区间被我**故意写成大幅重叠**([-0.05, 0.05]),但两组真值分离很远
    → 差值区间不含 0 → 必须判 `CONDITIONAL`。旧的重叠判据在这里会误判成「分辨不开」。
    """
    spec = {"高潮": [0.05] * 14, "退潮": [-0.05] * 14}
    v, txt = oe._thermo_verdict(_phase_recs(spec), _phase_rows(spec))
    assert v == "CONDITIONAL", txt


def test_thermo_verdict_unknown_not_a_positive_negative_label():
    """分辨不开 → `UNKNOWN`(证据不足),**不得**是 `NOT_CONDITIONAL` 这种肯定式否定标签。

    本文件方法论写死「不显著 ≠ 等价」,②节对 heat 严格照办;③节不能有第二套标准。
    """
    rng = np.random.default_rng(5)
    spec = {"高潮": list(rng.normal(0, 0.02, 22)), "退潮": list(rng.normal(0, 0.02, 30))}
    v, txt = oe._thermo_verdict(_phase_recs(spec), _phase_rows(spec))
    assert v == "UNKNOWN"
    assert "NOT_CONDITIONAL" not in v
    assert "证据不足" in txt or "功效不足" in txt


def test_thermo_verdict_never_cites_point_spread_as_evidence():
    """**不许**用「点估计极差仅 X」当分辨不开的理由 —— 同一份报告①节把 0.14pp 当显著效应,
    ③节拿 0.15pp 当「小到不必管」,是同一文档内自相矛盾(review I2c)。"""
    rng = np.random.default_rng(5)
    spec = {"高潮": list(rng.normal(0, 0.02, 22)), "退潮": list(rng.normal(0, 0.02, 30))}
    _, txt = oe._thermo_verdict(_phase_recs(spec), _phase_rows(spec))
    assert "极差" not in txt


def test_phase_delta_interval_hand_checked_separation():
    """差值区间:两组常数 → 点估计 = 差,区间退化且不含 0。"""
    rows = _phase_rows({"a": [0.02] * 12, "b": [-0.01] * 12})
    d = oe.phase_delta_interval(rows, "a", "b")
    assert d["point"] == pytest.approx(0.03)
    assert d["crosses_zero"] is False


def test_phase_delta_interval_too_few_days_is_none():
    rows = _phase_rows({"a": [0.02], "b": [-0.01] * 12})
    assert oe.phase_delta_interval(rows, "a", "b")["crosses_zero"] is None


def test_bucket_intervals_keeps_unknown_bucket_labels():
    """未知桶标签**必须被追加**,不能静默丢弃 —— 这条修复此前只有一个用例靠 IndexError
    间接守着,谁把那个用例的 `bucket` 改成合法值,修复就静默失去测试(review M3)。"""
    rows = pd.DataFrame({"date": ["d1", "d2"], "bucket": ["≥9.5%", "天外飞仙"],
                         "rel_gap_market": [0.01, 0.02]})
    tbl = oe.bucket_intervals(rows)
    assert set(tbl["bucket"]) == {"≥9.5%", "天外飞仙"}
    assert list(tbl["bucket"])[0] == "≥9.5%", "已知桶在前,未知桶追加在后"


def test_phase_gap_table_keeps_unknown_phase_labels():
    """同款未知相位追加逻辑此前**完全没有测试**(review M3)。"""
    rows = pd.DataFrame({"date": ["d1", "d2", "d3"], "phase": ["高潮", "妖股期", "退潮"],
                         "market_gap": [0.01, 0.02, -0.01]})
    tbl = oe.phase_gap_table(rows)
    assert set(tbl["phase"]) == {"高潮", "妖股期", "退潮"}
    assert list(tbl["phase"])[-1] == "妖股期", "未知相位追加在固定序之后"


def _mature_but_no_interval() -> pd.DataFrame:
    """15 个日子有行,但**只有 1 天有非空值** —— 区间只能由那 1 天产生。

    这是 ①节独有的可达路径:`bucket_intervals` 是三条聚合路径里**唯一不预过滤 NaN** 的
    (`side_intervals` / `phase_gap_table` 都先 `dropna`),所以「行的日子」与「有值的日子」
    在这里会分家。
    """
    return pd.DataFrame({"date": [f"d{i}" for i in range(15)],
                         "bucket": [oe.CHASE_LABELS[0]] * 15,
                         "rel_gap_market": [-0.05] + [np.nan] * 14})


def test_chase_verdict_refuses_direction_without_an_interval():
    """**无区间 → 不得给方向性裁决**(修复轮2 的唯一 Important)。

    `_chase_verdict` 产出的正是 ①节那个 `NEGATIVE` —— 三个结论里唯一被判「可以引用去
    指导生产」的那个。修复前它会在没有任何跨日方差的情况下吐出自信的 `NEGATIVE`,
    渲染出的句子自相矛盾:同一句既写「无跨日方差,不产区间」又写 NEGATIVE。
    这违反本报告自己立的家规「不显著 ≠ 等价」。
    """
    rec = oe.bucket_intervals(_mature_but_no_interval()).to_dict("records")
    v, txt = oe._chase_verdict(rec)
    assert v != "NEGATIVE", f"无区间却给了方向性裁决:{v} / {txt}"
    assert v in ("UNKNOWN", "IMMATURE")


def test_chase_verdict_guards_a_mature_row_that_has_no_interval():
    """直接喂 `_chase_verdict` 一个 **MATURE 但无区间** 的记录 → 必须是 `UNKNOWN`。

    为什么这条不能只靠上面那个端到端用例:根因修好之后,`bucket_intervals` **再也造不出**
    这种行(n_days 现在数的是区间真正用到的日子),端到端用例会走 IMMATURE 分支,
    于是守卫本身**一个测试都没有** —— 变异探针 S1 首轮正是这么活下来的。

    而这个形状**真实可达**:`render()` 会被喂 JSON 载入的 result,由**旧代码**产出的
    `result.json` 里就是 `n_days=15 / maturity=MATURE / lo=null`。守卫护的是这条路。
    """
    stale = [{"bucket": oe.CHASE_LABELS[0], "n_days": 15, "n_obs": 15, "point": -0.05,
              "lo": None, "hi": None, "crosses_zero": None, "maturity": "MATURE"}]
    v, txt = oe._chase_verdict(stale)
    assert v == "UNKNOWN", f"MATURE 但无区间却给了 {v}"
    assert "没有区间" in txt
    # JSON 往返把 None 变成 NaN 的那一版也必须挡住
    nan_ver = [dict(stale[0], lo=float("nan"), hi=float("nan"))]
    assert oe._chase_verdict(nan_ver)[0] == "UNKNOWN"


def test_chase_verdict_still_negative_on_a_real_interval():
    """守卫不得误伤:有真区间且整区间为负 → 仍是 `NEGATIVE`(防"修成一律 UNKNOWN")。"""
    rows = pd.DataFrame({"date": [f"d{i}" for i in range(15)],
                         "bucket": [oe.CHASE_LABELS[0]] * 15,
                         "rel_gap_market": [-0.05] * 15})
    assert oe._chase_verdict(oe.bucket_intervals(rows).to_dict("records"))[0] == "NEGATIVE"


def test_interval_row_reports_the_sample_the_interval_actually_used():
    """`n_days`/`n_obs` 必须是**区间真正用到的**样本,不是"有行的日子"。

    根因锁:修复前 `n_days` 数的是 `sub["date"].nunique()`(含整天全 NaN 的日子),于是
    一行可以自称 `n_days=15 · MATURE`,而区间其实只由 1 天产生 —— 成熟度标签因此失真,
    也正是这条让 `_chase_verdict` 的缺口变得可达。
    """
    rec = oe.bucket_intervals(_mature_but_no_interval()).to_dict("records")[0]
    assert rec["n_days"] == 1, "只有 1 天有值,不能自称 15 天"
    assert rec["n_obs"] == 1
    assert rec["maturity"] == "IMMATURE", "1 天的证据不得挂 MATURE"


def test_crosses_zero_of_boundary_touching_interval_counts_as_crossing():
    """区间**端点恰好是 0** → 算跨 0(不能排除 0)。

    这一条与 `stats.Interval.excludes_zero`(`lo > 0 or hi < 0`)**逐例等价** —— 判定
    「跨没跨 0」在仓库里只应有一套语义;写成严格不等号会在端点上与 `stats` 打架。
    (变异探针 S7 首轮存活正是因为没有端点用例。)
    """
    from autoresearch.common.stats import Interval

    for lo, hi in [(0.0, 0.02), (-0.02, 0.0), (0.0, 0.0),
                   (-0.02, 0.03), (0.01, 0.02), (-0.03, -0.01)]:
        mine = oe.crosses_zero_of({"lo": lo, "hi": hi})
        theirs = not Interval(0.0, lo, hi, 9, 9, "t").excludes_zero
        assert mine is theirs, f"[{lo}, {hi}] 与 stats 不一致:{mine} vs {theirs}"


def test_iv_derives_crossing_from_bounds_not_from_the_field():
    """渲染的「跨 0 / 整区间同号」由 lo/hi 现算,**不信** `crosses_zero` 字段 ——
    同一件事两个真相来源迟早打架(自查:该语义的最后一处冗余,已消除)。"""
    lying = {"point": -0.01, "lo": -0.02, "hi": -0.005, "n_days": 12, "crosses_zero": True}
    assert "整区间同号" in oe._iv(lying)
    lying2 = {"point": 0.0, "lo": -0.02, "hi": 0.02, "n_days": 12, "crosses_zero": False}
    assert "跨 0" in oe._iv(lying2)


def test_interval_row_marks_crosses_zero_unknown_when_no_interval():
    """**数据层**修复(review M4):没有区间时 `crosses_zero` 必须是 None(未知),
    不是 `True`。`--json` 导出的字段会被下游直接读,修在展示层不算修。"""
    one = pd.DataFrame({"date": ["d1"] * 5, "v": [0.01] * 5})
    row = oe._interval_row(one, "v")
    assert row["n_days"] == 1
    assert row["lo"] is None and row["hi"] is None
    assert row["crosses_zero"] is None


def test_side_verdict_immature_when_a_side_has_no_interval():
    """某侧无区间 → IMMATURE,不得因为 `crosses_zero=None` 是假值就滑进 CONFIRMED。"""
    recs = [{"side": "回撤", "point": 0.0, "lo": None, "hi": None, "n_days": 14,
             "crosses_zero": None, "maturity": "MATURE"},
            {"side": "上涨", "point": -0.005, "lo": -0.009, "hi": -0.002, "n_days": 14,
             "crosses_zero": False, "maturity": "MATURE"}]
    assert oe._side_verdict(recs)[0] == "IMMATURE"


def test_side_thin_uses_scan_day_floor_not_subgroup_floor():
    """薄样本判据是 ≥20 真实扫描日(`MATURITY_MIN_SCAN_DAYS`),不是细分门的 10 ——
    过 10 只说明「可以有读数」,不等于「够格改生产配额」(review I1)。"""
    thin = _sides(-0.005, -0.009, -0.002, -0.002, 0.002, n=14)
    assert oe._side_thin(thin) is True
    ok = _sides(-0.005, -0.009, -0.002, -0.002, 0.002, n=25)
    assert oe._side_thin(ok) is False


def test_thermo_verdict_ignores_interval_less_phases():
    """n_days=1 的相位(无区间)不得参与比较 —— 否则一个没有区间的点估计能凭空「分开」两相位。"""
    recs = [{"phase": "冰点", "point": -0.0017, "lo": float("nan"), "hi": float("nan"),
             "n_days": 1, "maturity": "IMMATURE"},
            {"phase": "高潮", "point": -0.0019, "lo": -0.0036, "hi": -0.0002,
             "n_days": 22, "maturity": "MATURE"}]
    assert oe._thermo_verdict(recs)[0] == "IMMATURE"


def test_thermo_verdict_ignores_immature_phase_even_with_a_valid_interval():
    """成熟门是**独立**一道:n_days=3 的相位即使算得出区间、且与成熟相位互不重叠,
    也不得据此宣布「隔夜 edge 是相位现象」。

    (这条补的是变异探针 N12 的盲区:上一条用的 IMMATURE 行同时没有区间,于是
    `_missing` 那道过滤就足以让测试通过,成熟门本身其实没被测到 —— 两个过滤器
    叠在同一个用例上 = 其中一个可以被删掉而测试不变红。)
    """
    recs = [{"phase": "修复", "point": 0.02, "lo": 0.015, "hi": 0.025,
             "n_days": 3, "maturity": "IMMATURE"},
            {"phase": "高潮", "point": -0.0019, "lo": -0.0036, "hi": -0.0002,
             "n_days": 22, "maturity": "MATURE"}]
    assert oe._thermo_verdict(recs)[0] == "IMMATURE"
