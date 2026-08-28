#!/usr/bin/env python3
"""`overnight_census.core` 纯函数层 —— 零网络、零真实湖(全部合成 DataFrame / tmp_path)。

对应契约「测试要求」与设计稿 §4.6 里属于 core 的几条:成交层真值表(含 142959 边界)、
席位四分类与匹配率口径、除权恒等式、四态判读边界(**样本门先判**)、按日等权聚合,
以及两条**变异探针** —— 把常量改坏后断言必须变红(本仓判例:「绿灯不等于有灯」)。
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from autoresearch.research.overnight_census import core

# ───────────────────────── 合成夹具 ─────────────────────────


def _frame(rows: list[tuple[str, float]]) -> pd.DataFrame:
    """`[(date, gap_pp), …]` → 面板片段。"""
    return pd.DataFrame(rows, columns=["date", "gap_pp"])


def _year_frame(year_means: dict[str, float], *, days: int = 20,
                per_day: int = 5) -> pd.DataFrame:
    """每年 `days` 个交易日、每日 `per_day` 行、日均值恒等于 `year_means[year]` 的合成格。

    行数与日数都刻意配到事件族样本门之上(4 年 × 20 日 = 80 日 > 60;400 行 > 300),
    这样判读结果的差异只可能来自符号稳定性,而不是被样本门挡下。
    """
    rows = []
    for year, value in year_means.items():
        for day in range(1, days + 1):
            rows.extend([(f"{year}03{day:02d}", value)] * per_day)
    return _frame(rows)


# ═══════════════════════ 1 · 成交层真值表(§3.4)═══════════════════════

# (limit, last_time, open_times, fd_amount, amount, 期望层, 说明)
_TIER_CASES = [
    ("Z", None, None, None, None, "T0", "炸板:其余字段全缺也必成交"),
    ("Z", "092500", 0, 9.0e8, 1.0e9, "T0", "炸板优先于任何 U 侧条件"),
    ("z", None, None, None, None, "T0", "大小写不敏感"),
    ("U", "143000", 0, 9.0e8, 1.0e9, "T1", "last_time 恰 143000 → 尾盘封"),
    ("U", "142959", 0, 9.0e8, 1.0e9, "T2", "边界:早一秒就不算尾盘封"),
    ("U", "153000", 0, 9.0e8, 1.0e9, "T1", "收盘前才封"),
    ("U", "092500", 0, 9.0e8, 1.0e9, "T2", "早封 + 厚封单 = 不可成交"),
    ("U", "092500", 1, 9.0e8, 1.0e9, "T1", "open_times=1 → 开过板"),
    ("U", "092500", 2, 9.0e8, 1.0e9, "T1", "open_times≥1 即可"),
    ("U", "092500", 0, 100.0, 1000.0, "T1", "fd/amount 恰 0.10 → 封单薄"),
    ("U", "092500", 0, 101.0, 1000.0, "T2", "fd/amount 0.101 → 封单厚"),
    ("U", 93000, 0, 9.0e8, 1.0e9, "T2", "int 5 位:zfill 成 093000,不是 930000"),
    ("U", 143000, 0, 9.0e8, 1.0e9, "T1", "int 6 位"),
    ("U", 143000.0, 0, 9.0e8, 1.0e9, "T1", "float 整数值"),
    ("U", "abc", 0, 9.0e8, 1.0e9, "T2", "时间不可解析 → 该条件 False,不当 True"),
    ("U", np.nan, np.nan, np.nan, np.nan, "T2", "三个条件全不可知 → T2(不静默当 T1)"),
    ("U", None, None, None, None, "T2", "全缺 → T2"),
    ("U", "092500", 0, 100.0, 0.0, "T2", "amount<=0 → 封单比无意义,记 False"),
    ("U", "092500", 0, 100.0, -5.0, "T2", "amount 负同上"),
    ("U", "092500", 0, None, 1.0e9, "T2", "fd 缺 → 记 False"),
    ("U", "092500", 0, 100.0, None, "T2", "amount 缺 → 记 False"),
    ("u", "143000", 0, 9.0e8, 1.0e9, "T1", "小写 u"),
    ("D", "143000", 5, 1.0, 1.0e9, None, "跌停:本层不适用"),
    (None, "143000", 5, 1.0, 1.0e9, None, "limit 缺失 → None"),
    (np.nan, "143000", 5, 1.0, 1.0e9, None, "limit NaN → None"),
    ("", "143000", 5, 1.0, 1.0e9, None, "limit 空串 → None"),
    ("X", "143000", 5, 1.0, 1.0e9, None, "未知代码 → None,不猜"),
]


@pytest.mark.parametrize("limit,last_time,open_times,fd,amount,expected,why", _TIER_CASES)
def test_exec_tier_truth_table(limit, last_time, open_times, fd, amount, expected, why):
    assert core.exec_tier(limit, last_time, open_times, fd, amount) == expected, why


def test_exec_tier_missing_never_upgrades_to_t1():
    """纪律 2 的专项锁:U + 三条件全不可解析,只能是 T2。

    把「不知道」折叠成「是」的话,一字板会整批变成「可能成交」,F3 的上界读数直接变假。
    """
    for bad in (None, np.nan, "", "  ", "not-a-time", -1):
        assert core.exec_tier("U", bad, bad, bad, bad) == "T2"
    # 超过 6 位的时间是坏数据,不是「很晚」:1430000 不得被读成尾盘封
    assert core.exec_tier("U", 1_430_000, 0, 9.0e8, 1.0e9) == "T2"
    assert core.exec_tier("U", "14300", 0, 9.0e8, 1.0e9) == "T2"      # 014300 = 01:43


# ═══════════════════════ 2 · 席位四分类与匹配率(§3.5)═══════════════════════

_YOUZI = {"中信证券股份有限公司上海溧阳路证券营业部", "东方财富证券股份有限公司拉萨"}


def test_classify_seat_four_way():
    assert core.classify_seat("机构专用", _YOUZI) == "inst"
    assert core.classify_seat("  机构专用  ", _YOUZI) == "inst"
    assert core.classify_seat("沪股通专用", _YOUZI) == "north"
    assert core.classify_seat("深股通专用", _YOUZI) == "north"
    assert core.classify_seat("中信证券股份有限公司上海溧阳路证券营业部", _YOUZI) == "youzi"
    assert core.classify_seat("华泰证券股份有限公司总部", _YOUZI) == "other"


def test_classify_seat_youzi_is_substring_match():
    """游资走**子串**:同一营业部在湖里有多种写法,名单只存得下一种。"""
    assert core.classify_seat(
        "东方财富证券股份有限公司拉萨金融城南环路证券营业部", _YOUZI) == "youzi"
    assert core.classify_seat(
        "东方财富证券股份有限公司拉萨东环路第二证券营业部", _YOUZI) == "youzi"
    assert core.classify_seat("  中信证券股份有限公司上海溧阳路证券营业部 ", _YOUZI) == "youzi"
    # 名单项两端空白不影响匹配
    assert core.classify_seat("某某上海溧阳路证券营业部", {"  上海溧阳路  "}) == "youzi"


def test_classify_seat_inst_is_exact_not_substring():
    """真实湖里存在**带「机构专用」四字的营业部名**(中信建投北京商务中心区,464 日 11 行)。

    用子串判 inst 会把它误算成匿名机构席 —— 这是照实测数据钉死的口径。
    """
    name = "中信建投证券股份有限公司北京商务中心区机构专用证券营业部"
    assert core.classify_seat(name, _YOUZI) == "other"
    assert core.classify_seat("某某沪股通专用证券营业部", _YOUZI) == "other"


def test_classify_seat_order_inst_north_youzi_other():
    """判定顺序:名单里混进 `机构专用` / `股通专用` 也抢不走 inst/north。"""
    polluted = {"机构专用", "股通专用"} | _YOUZI
    assert core.classify_seat("机构专用", polluted) == "inst"
    assert core.classify_seat("深股通专用", polluted) == "north"
    assert core.classify_seat("沪股通专用", polluted) == "north"


def test_classify_seat_missing_and_empty_keyword():
    assert core.classify_seat(None, _YOUZI) == "other"
    assert core.classify_seat("", _YOUZI) == "other"
    assert core.classify_seat("   ", _YOUZI) == "other"
    assert core.classify_seat(np.nan, _YOUZI) == "other"
    # 空串是任何字符串的子串:必须不参与匹配,否则全表变游资
    assert core.classify_seat("华泰证券股份有限公司总部", {"", "   "}) == "other"
    assert core.classify_seat("华泰证券股份有限公司总部", set()) == "other"


def test_seat_coverage_denominator_excludes_inst_and_north():
    """`match_rate` 分母 = 非机构非北向行;inst/north 再多也不稀释名单质量。"""
    rows = (["机构专用"] * 2 + ["沪股通专用"] + ["深股通专用"]
            + ["中信证券股份有限公司上海溧阳路证券营业部"] * 3
            + ["某某证券股份有限公司某路证券营业部"] * 7)
    cov = core.seat_coverage(rows, _YOUZI)
    assert cov == {"n": 14, "n_youzi": 3, "n_inst": 2, "n_north": 2,
                   "match_rate": pytest.approx(0.30), "fallback": False}

    padded = core.seat_coverage(rows + ["机构专用"] * 100 + ["深股通专用"] * 50, _YOUZI)
    assert padded["match_rate"] == pytest.approx(0.30)      # 分母没变
    assert padded["n"] == 164 and padded["n_inst"] == 102 and padded["n_north"] == 52


def test_seat_coverage_fallback_boundary():
    """恰 0.30 不回退;0.30 以下回退(`SEAT_MATCH_MIN` 是「低于则回退」)。"""
    hit = "中信证券股份有限公司上海溧阳路证券营业部"
    miss = "某某证券股份有限公司某路证券营业部"
    assert core.seat_coverage([hit] * 3 + [miss] * 7, _YOUZI)["fallback"] is False
    below = core.seat_coverage([hit] * 2 + [miss] * 8, _YOUZI)
    assert below["match_rate"] == pytest.approx(0.20) and below["fallback"] is True


def test_seat_coverage_empty_and_none_rows():
    empty = core.seat_coverage([], _YOUZI)
    assert empty["n"] == 0 and empty["match_rate"] == 0.0 and empty["fallback"] is True
    # 分母 0(全是机构/北向)→ 0.0,不是 NaN、也不是除零
    all_inst = core.seat_coverage(["机构专用", "沪股通专用"], _YOUZI)
    assert all_inst["match_rate"] == 0.0 and all_inst["n"] == 2
    # None 行照样计入 n 与分母(它是湖里真实存在的行)
    with_none = core.seat_coverage([None, None, None, None], _YOUZI)
    assert with_none["n"] == 4 and with_none["match_rate"] == 0.0


def test_seat_coverage_accepts_pandas_series():
    series = pd.Series(["机构专用", "中信证券股份有限公司上海溧阳路证券营业部", "别的营业部"])
    assert core.seat_coverage(series, _YOUZI)["n_youzi"] == 1


# ═══════════════════════ 3 · 席位名单资产 ═══════════════════════


def test_load_youzi_seats_snapshot():
    names = core.load_youzi_seats()
    assert len(names) >= 20
    assert "中信证券股份有限公司上海溧阳路证券营业部" in names
    assert "东方财富证券股份有限公司拉萨" in names
    assert all(n == n.strip() and n for n in names)


def test_asset_unmapped_aliases_are_not_matchable():
    """抽不出营业部名的绰号只留痕,**不进 names** —— 否则它们永远匹配不上还虚增名单长度。"""
    payload = json.loads(core.ASSET_PATH.read_text(encoding="utf-8"))
    assert payload["_unmapped_aliases"]
    for alias in payload["_unmapped_aliases"]:
        assert alias not in payload["names"]
    # 券商级模糊词绝不能进 names:子串匹配会把整家券商算成游资
    for broker in ("银河证券", "招商证券", "中信证券", "华鑫证券"):
        assert broker not in payload["names"]


def test_load_youzi_seats_custom_path_and_bad_file(tmp_path):
    good = tmp_path / "seats.json"
    good.write_text(json.dumps({"names": [" A ", "B", "", "  "]}), encoding="utf-8")
    assert core.load_youzi_seats(good) == {"A", "B"}

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"names": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        core.load_youzi_seats(bad)        # 空名单必须炸,不能悄悄把全体判成 other
    with pytest.raises(OSError):
        core.load_youzi_seats(tmp_path / "nope.json")


# ═══════════════════════ 4 · 除权除息机械校正(F1c)═══════════════════════


def test_adjust_ex_div_pure_cash():
    """每股派 0.5 元、收盘 10.0 → 理论开盘 9.5,校正后 gap 恰 0。"""
    assert core.adjust_ex_div(9.5, 10.0, 0.0, 0.5) == pytest.approx(0.0)


def test_adjust_ex_div_pure_stock():
    """10 送 5(stk_div=0.5)、收盘 15.0 → 理论开盘 10.0,校正后 gap 恰 0。"""
    assert core.adjust_ex_div(10.0, 15.0, 0.5, 0.0) == pytest.approx(0.0)


def test_adjust_ex_div_mixed():
    """送转 + 现金混合:(10×1.5 + 0.3)/15 − 1 = +2.0pp。"""
    assert core.adjust_ex_div(10.0, 15.0, 0.5, 0.3) == pytest.approx(2.0)


def test_adjust_ex_div_returns_pp_not_decimal():
    assert core.adjust_ex_div(9.6, 10.0, 0.0, 0.5) == pytest.approx(1.0)   # 0.01 → 1.0pp


def test_adjust_ex_div_missing_and_nonpositive_close():
    assert core.adjust_ex_div(10.0, 0.0, 0.0, 0.0) is None
    assert core.adjust_ex_div(10.0, -3.0, 0.0, 0.0) is None
    assert core.adjust_ex_div(10.0, None, 0.0, 0.0) is None
    assert core.adjust_ex_div(10.0, np.nan, 0.0, 0.0) is None
    # open_t2 缺失也必须 None:当 0 会算出 −100pp 的假暴跌
    assert core.adjust_ex_div(None, 10.0, 0.0, 0.5) is None
    assert core.adjust_ex_div(np.nan, 10.0, 0.0, 0.5) is None
    # 送转/现金缺失才允许按 0(没有送转就是没有送转)
    assert core.adjust_ex_div(10.2, 10.0, None, None) == pytest.approx(2.0)


# ═══════════════════════ 5 · cell_stats 聚合(§2.3)═══════════════════════


def test_cell_stats_day_equal_not_row_equal():
    """一天 100 只 + 另一天 1 只:两天各占一半权重。

    行等权会给出 10/101 ≈ 0.099;日等权是 (0 + 10)/2 = 5.0。`n_events` 只描述覆盖。
    """
    rows = [("20220301", 0.0)] * 100 + [("20220302", 10.0)]
    stats = core.cell_stats(_frame(rows), value_col="gap_pp")
    assert stats["n_events"] == 101 and stats["n_days"] == 2
    assert stats["mean_pp"] == pytest.approx(5.0)
    assert stats["mean_pp"] != pytest.approx(10.0 / 101, abs=1e-6)
    assert stats["median_pp"] == pytest.approx(5.0)      # 两日均值 [0,10] 的中位
    assert stats["hit"] == pytest.approx(0.5)            # 逐日均值 >0 的**日**占比
    assert stats["freq_per_week"] == pytest.approx(5.0 * 101 / 2)


def test_cell_stats_single_day_has_mean_but_no_interval():
    """单日:没有跨日方差 → 区间 None。给一个点冒充区间才是错的。"""
    stats = core.cell_stats(_frame([("20220301", 1.0), ("20220301", 3.0)]),
                            value_col="gap_pp")
    assert stats["n_days"] == 1 and stats["mean_pp"] == pytest.approx(2.0)
    assert stats["ci_low_pp"] is None and stats["ci_high_pp"] is None
    assert stats["half1_pp"] is None and stats["half2_pp"] is None
    assert stats["halves_sign_ok"] is False
    assert stats["freq_per_week"] is None


def test_cell_stats_empty_frame_does_not_raise():
    stats = core.cell_stats(_frame([]), value_col="gap_pp", sample_kind="wide")
    assert stats["n_events"] == 0 and stats["n_days"] == 0
    for key in ("mean_pp", "median_pp", "hit", "ci_low_pp", "ci_high_pp", "net_pp",
                "half1_pp", "half2_pp", "freq_per_week"):
        assert stats[key] is None, key
    assert stats["yearly"] == dict.fromkeys(("2022", "2023", "2024", "2025", "2026"))
    assert stats["yearly_sign_ok"] is False and stats["halves_sign_ok"] is False
    assert stats["sample_kind"] == "wide"
    assert core.judge(stats) == core.THIN


def test_cell_stats_all_values_nan_is_empty_not_crash():
    frame = _frame([("20220301", np.nan), ("20220302", np.nan)])
    assert core.cell_stats(frame, value_col="gap_pp")["n_events"] == 0


def test_cell_stats_drops_unusable_rows_only():
    frame = pd.DataFrame({"date": ["20220301", "20220301", None, "20220302"],
                          "gap_pp": [1.0, np.nan, 5.0, 3.0]})
    stats = core.cell_stats(frame, value_col="gap_pp")
    assert stats["n_events"] == 2 and stats["n_days"] == 2
    assert stats["mean_pp"] == pytest.approx(2.0)


def test_cell_stats_missing_column_raises():
    """缺 date 列时 bootstrap 会退化成「每行一簇」给出窄到假的区间 —— 宁可炸。"""
    with pytest.raises(ValueError):
        core.cell_stats(_frame([("20220301", 1.0)]), value_col="gap_pp", date_col="d")
    with pytest.raises(ValueError):
        core.cell_stats(_frame([("20220301", 1.0)]), value_col="nope")
    with pytest.raises(ValueError):
        core.cell_stats(None, value_col="gap_pp")
    with pytest.raises(ValueError):
        core.cell_stats(_frame([("20220301", 1.0)]), value_col="gap_pp", sample_kind="huge")


def test_cell_stats_net_is_gross_minus_cost():
    stats = core.cell_stats(_year_frame({"2022": 0.6, "2023": 0.6, "2024": 0.6,
                                         "2025": 0.6}), value_col="gap_pp")
    assert stats["mean_pp"] == pytest.approx(0.6)
    assert stats["net_pp"] == pytest.approx(0.6 - core.COST_PP)


def test_cell_stats_yearly_and_halves():
    """2026 有读数、进表,但**不进** `yearly_sign_ok`(它只看 `JUDGE_YEARS` 四年)。"""
    stats = core.cell_stats(_year_frame({"2022": 1.0, "2023": 1.0, "2024": 1.0,
                                         "2025": 1.0, "2026": -0.2}),
                            value_col="gap_pp")
    assert set(stats["yearly"]) >= {"2022", "2023", "2024", "2025", "2026"}
    assert stats["yearly"]["2022"] == pytest.approx(1.0)
    assert stats["yearly"]["2026"] == pytest.approx(-0.2)     # 逆号的 2026 照样出数
    assert stats["mean_pp"] == pytest.approx(0.76)            # 但它进了全期均值
    assert stats["yearly_sign_ok"] is True                    # 4/4 判读年与均值同号
    assert stats["halves_sign_ok"] is True


def test_cell_stats_yearly_sign_needs_enough_years_with_data():
    """只有 2 个判读年有数据、且都同号 → 仍是 False(2 年不叫「逐年稳定」)。"""
    stats = core.cell_stats(_year_frame({"2024": 1.0, "2025": 1.0}), value_col="gap_pp")
    assert stats["yearly"]["2022"] is None and stats["yearly"]["2023"] is None
    assert stats["yearly_sign_ok"] is False


def test_cell_stats_halves_sign_split():
    """前半正、后半负 → `halves_sign_ok=False`(全期均值仍为正也不行)。"""
    stats = core.cell_stats(_year_frame({"2022": 3.0, "2023": 3.0,
                                         "2024": -1.0, "2025": -1.0}),
                            value_col="gap_pp")
    assert stats["mean_pp"] == pytest.approx(1.0)
    assert stats["half1_pp"] == pytest.approx(3.0)
    assert stats["half2_pp"] == pytest.approx(-1.0)
    assert stats["halves_sign_ok"] is False


def test_cell_stats_interval_is_deterministic_and_seed_is_plumbed():
    """同输入同区间(判据不许每跑一次换个数),且 `seed` 真的传到了 bootstrap。"""
    frame = _frame([(f"2022{1 + i // 28:02d}{1 + i % 28:02d}", ((i * 37) % 100) / 10.0 - 4.0)
                    for i in range(80)])
    a = core.cell_stats(frame, value_col="gap_pp")
    b = core.cell_stats(frame, value_col="gap_pp")
    assert (a["ci_low_pp"], a["ci_high_pp"]) == (b["ci_low_pp"], b["ci_high_pp"])
    assert a["ci_low_pp"] is not None and a["ci_low_pp"] < a["mean_pp"] < a["ci_high_pp"]
    other = core.cell_stats(frame, value_col="gap_pp", seed=1)
    assert other["ci_low_pp"] != a["ci_low_pp"]      # 换种子换重采样 → 换区间
    assert other["mean_pp"] == pytest.approx(a["mean_pp"])   # 但点估计与种子无关


# ═══════════════════════ 6 · 四态判读边界(§2.4)═══════════════════════


def _st(**over) -> dict:
    base = {"sample_kind": "event", "n_events": 400, "n_days": 80,
            "ci_low_pp": 0.5, "ci_high_pp": 0.9,
            "yearly_sign_ok": True, "halves_sign_ok": True}
    base.update(over)
    return base


def test_judge_positive_boundary():
    assert core.judge(_st(ci_low_pp=0.15)) == core.POS          # 恰 0.15 → 正证据
    assert core.judge(_st(ci_low_pp=0.1499)) == core.UNPROVEN   # 差一点就不是


def test_judge_needs_all_three_lamps():
    assert core.judge(_st(yearly_sign_ok=False)) == core.UNPROVEN
    assert core.judge(_st(halves_sign_ok=False)) == core.UNPROVEN
    assert core.judge(_st(ci_low_pp=None, ci_high_pp=None)) == core.UNPROVEN


def test_judge_sample_gate_comes_first():
    """样本门先判:299 个事件时**哪怕 CI 好到离谱**也只报「样本不足」。"""
    assert core.judge(_st(n_events=299, ci_low_pp=5.0, ci_high_pp=9.0)) == core.THIN
    assert core.judge(_st(n_events=300)) == core.POS
    assert core.judge(_st(n_days=59, ci_low_pp=5.0)) == core.THIN
    assert core.judge(_st(n_days=60)) == core.POS
    # 显著负同样让位于样本门
    assert core.judge(_st(n_events=10, ci_low_pp=-9.0, ci_high_pp=-5.0)) == core.THIN


def test_judge_wide_family_uses_day_gate_only():
    assert core.judge(_st(sample_kind="wide", n_events=1, n_days=200)) == core.POS
    assert core.judge(_st(sample_kind="wide", n_events=99_999, n_days=199)) == core.THIN


def test_judge_significant_negative():
    assert core.judge(_st(ci_low_pp=-0.9, ci_high_pp=-0.01)) == core.NEG
    assert core.judge(_st(ci_low_pp=-0.9, ci_high_pp=0.0)) == core.UNPROVEN   # 上界 0 不算负
    assert core.judge(_st(ci_low_pp=-0.9, ci_high_pp=0.05)) == core.UNPROVEN


def test_judge_unknown_sample_kind_raises():
    with pytest.raises(ValueError):
        core.judge(_st(sample_kind="whatever"))
    with pytest.raises(ValueError):
        core.judge({})


def test_judge_end_to_end_on_synthetic_cell():
    """整条链路:合成一个稳定为正的格 → cell_stats → judge 应给正证据。"""
    stats = core.cell_stats(_year_frame({"2022": 1.4, "2023": 1.6, "2024": 1.5,
                                         "2025": 1.5}), value_col="gap_pp")
    assert stats["ci_low_pp"] is not None and stats["ci_low_pp"] >= core.CI_LOWER_PP
    assert core.judge(stats) == core.POS


# ═══════════════════════ 7 · 变异探针(常量改坏 → 断言必须变红)═══════════════════════
#
# 本仓判例「绿灯不等于有灯」:改完先问「把这段删掉/改坏,测试会红吗」。下面两条把那个问句
# 写成可执行的断言 —— 先证明常量完好时断言成立,再 monkeypatch 改坏常量并**要求断言抛出**。


def _assert_net_pp_is_gross_minus_15bps(frame) -> None:
    """被探针检验的那条断言本体(单独抽出来,才能对它 `pytest.raises`)。"""
    stats = core.cell_stats(frame, value_col="gap_pp", sample_kind="wide")
    assert stats["mean_pp"] == pytest.approx(0.60)
    assert stats["net_pp"] == pytest.approx(0.45)


def test_mutation_probe_cost_pp(monkeypatch):
    """探针①:`COST_PP` 置 0 → `net_pp` 断言必须变红。"""
    frame = _year_frame({"2022": 0.60, "2023": 0.60, "2024": 0.60, "2025": 0.60})
    _assert_net_pp_is_gross_minus_15bps(frame)                 # 常量完好:成立

    monkeypatch.setattr(core, "COST_PP", 0.0)
    with pytest.raises(AssertionError):
        _assert_net_pp_is_gross_minus_15bps(frame)             # 常量坏掉:必须失败
    assert core.cell_stats(frame, value_col="gap_pp",
                           sample_kind="wide")["net_pp"] == pytest.approx(0.60)


def _assert_two_of_four_years_is_unproven(frame) -> None:
    """被探针检验的那条断言本体:2/4 同号 → 稳定性灯灭 → 判「未证」。"""
    stats = core.cell_stats(frame, value_col="gap_pp", sample_kind="event")
    assert stats["yearly_sign_ok"] is False
    assert core.judge(stats) == core.UNPROVEN


def test_mutation_probe_min_years_same_sign(monkeypatch):
    """探针②:`MIN_YEARS_SAME_SIGN` 降到 1 → 「2/4 同号应判未证」必须变红。

    夹具刻意让两半都为正、CI 下界远高于 0.15、样本门也过 —— 于是判读结果**只**由逐年
    符号这一盏灯决定:灯坏了,格子立刻从「未证」翻成「正证据」。
    """
    frame = _year_frame({"2022": 1.5, "2023": -0.3, "2024": 1.5, "2025": -0.3})
    intact = core.cell_stats(frame, value_col="gap_pp", sample_kind="event")
    assert intact["n_events"] >= core.MIN_EVENTS and intact["n_days"] >= core.MIN_DAYS_EVENT
    assert intact["halves_sign_ok"] is True
    assert intact["ci_low_pp"] >= core.CI_LOWER_PP
    _assert_two_of_four_years_is_unproven(frame)               # 常量完好:成立

    monkeypatch.setattr(core, "MIN_YEARS_SAME_SIGN", 1)
    with pytest.raises(AssertionError):
        _assert_two_of_four_years_is_unproven(frame)           # 常量坏掉:必须失败
    broken = core.cell_stats(frame, value_col="gap_pp", sample_kind="event")
    assert broken["yearly_sign_ok"] is True and core.judge(broken) == core.POS
