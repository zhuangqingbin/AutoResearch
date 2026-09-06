#!/usr/bin/env python3
"""隔夜普查两个入口(core / CLI)的统计口径必须同源。

治的病:`core.cell_stats` 的 `mean_pp` 是日等权,而它的区间来自行等权 bootstrap;CLI 的
`_stat_cell` 事后又用 `day_equal_ci` **覆盖**了那个区间来补偿。于是同一个数有两处实现、
两个默认 seed,底层单独用就是错的。收敛后 core 一次算对,CLI 不再覆盖。

⚠️ 边界:**CLI 既有的日等权数值是对的**(补偿有效),所以本文件既锁「core 变对」,也锁
「CLI 的数不变」—— 后者用**不依赖新实现**的手算期望值,否则两边一起错还能一起绿。
"""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.common import stats
from autoresearch.research.overnight_census import __main__ as cli
from autoresearch.research.overnight_census import core


def uneven_events() -> pd.DataFrame:
    """40 日:前 20 日每天 100 行 +1pp,后 20 日每天 1 行 −1pp。日等权 0,行等权 ≈ +0.98。"""
    rows = []
    for i, day in enumerate(pd.bdate_range("2026-01-05", periods=40)):
        value, count = (1.0, 100) if i < 20 else (-1.0, 1)
        rows.extend({"date": day.strftime("%Y%m%d"), "gap_pp": value}
                    for _ in range(count))
    return pd.DataFrame(rows)


def uneven_varied_events() -> pd.DataFrame:
    """同样的不平衡日结构,但**日均值逐日变化** —— 换 seed 才会换区间的那份夹具。"""
    rows = []
    for i, day in enumerate(pd.bdate_range("2026-01-05", periods=40)):
        count = 100 if i < 20 else 1
        value = ((i * 37) % 100) / 10.0 - 5.0
        rows.extend({"date": day.strftime("%Y%m%d"), "gap_pp": value}
                    for _ in range(count))
    return pd.DataFrame(rows)


# ─────────────────────── core:均值与区间同口径 ───────────────────────


def test_core_mean_and_ci_describe_same_estimator():
    frame = uneven_events()
    result = core.cell_stats(frame, value_col="gap_pp", seed=19)
    expected = stats.day_equal_bootstrap(frame, "gap_pp", seed=19)
    assert result["mean_pp"] == pytest.approx(expected.point)
    assert (result["ci_low_pp"], result["ci_high_pp"]) == (expected.lo, expected.hi)
    # 日等权中心在 0 → 区间必须跨 0。行等权区间会整段落在 +0.96 一带(远离均值 0)。
    assert result["ci_low_pp"] < 0 < result["ci_high_pp"]
    assert result["n_events"] == 2020 and result["n_days"] == 40


def test_core_interval_is_not_centred_on_the_row_weighted_mean():
    """反向锁:行等权中心(≈ +0.98)必须落在新区间**之外**。"""
    frame = uneven_events()
    result = core.cell_stats(frame, value_col="gap_pp", seed=19)
    row_equal = stats.date_cluster_bootstrap(frame, "gap_pp", seed=19).point
    assert row_equal == pytest.approx(1980 / 2020)
    assert result["ci_high_pp"] < row_equal


def test_core_and_existing_cli_agree_with_same_seed():
    frame = uneven_events()
    result = core.cell_stats(frame, value_col="gap_pp", seed=stats.DEFAULT_SEED)
    lo, hi, method = cli.day_equal_ci(frame)
    assert (result["ci_low_pp"], result["ci_high_pp"]) == (lo, hi)
    assert method == "date_cluster_bootstrap(day-equal)"


def test_non_finite_returns_are_rejected():
    """行为变更(记账在案):inf 过去会静默流进均值,现在阻断。A 级坏输入不降级。"""
    frame = pd.DataFrame({"date": ["20260105"], "gap_pp": [float("inf")]})
    with pytest.raises(ValueError):
        core.cell_stats(frame, value_col="gap_pp")


def test_core_still_reports_statistics_version():
    """换估计量必须留版本,否则新旧读数会被直接拼成趋势。"""
    assert core.STATISTICS_VERSION.startswith("overnight.day_equal.")


# ─────────────────── CLI:数值不变 + 不再二次覆盖 ───────────────────


def test_cli_preserves_old_daily_weighted_formula():
    """期望值**手算**(groupby + 旧原语),不调新实现 —— 否则两边一起错还能一起绿。"""
    frame = uneven_events()
    daily = frame.groupby("date", as_index=False)["gap_pp"].mean()
    expected = stats.date_cluster_bootstrap(daily, "gap_pp")
    lo, hi, method = cli.day_equal_ci(frame)
    assert (lo, hi) == (expected.lo, expected.hi)
    assert method == "date_cluster_bootstrap(day-equal)"


def test_cli_stat_cell_uses_correct_core_result_without_second_override(monkeypatch):
    frame = uneven_events()
    expected = core.cell_stats(frame, value_col="gap_pp", seed=stats.DEFAULT_SEED)

    def no_compensation(*args, **kwargs):
        raise AssertionError("CLI 不许再算一份区间去覆盖 core 的结果")

    monkeypatch.setattr(cli, "day_equal_ci", no_compensation)
    result = cli._stat_cell({
        "family": "synthetic", "kind": "main", "timing": "R",
        "sample_kind": "event", "rows": frame,
    })
    actual = result["stats"]
    assert (actual["ci_low_pp"], actual["ci_high_pp"]) == (
        expected["ci_low_pp"], expected["ci_high_pp"],
    )
    assert actual["ci_method"] == "date_cluster_bootstrap(day-equal)"


def test_cli_keeps_its_historical_seed_not_cores():
    """CLI 必须继续用公共层 seed(20260803)—— 08-28 读数印的就是它。

    两个入口各有默认 seed 是历史事实(core 20260828 / 公共层 20260803)。`_stat_cell`
    若少传 seed,数字会静默换一批。本用例是唯一会因此变红的断言。

    ⚠️ 夹具必须用**日均值有变化**的那份:`uneven_events` 的日值全是 ±1.0,重采样分布离散
    到两个 seed 的分位数恰好重合 —— 拿它写这条断言等于写了个永不变红的绿灯(本波实测)。
    """
    frame = uneven_varied_events()
    assert stats.DEFAULT_SEED != core.DEFAULT_SEED
    published = cli._stat_cell({
        "family": "synthetic", "kind": "main", "timing": "R",
        "sample_kind": "event", "rows": frame,
    })["stats"]
    cli_seeded = core.cell_stats(frame, value_col="gap_pp", seed=stats.DEFAULT_SEED)
    core_seeded = core.cell_stats(frame, value_col="gap_pp", seed=core.DEFAULT_SEED)
    assert (published["ci_low_pp"], published["ci_high_pp"]) == (
        cli_seeded["ci_low_pp"], cli_seeded["ci_high_pp"])
    assert (published["ci_low_pp"], published["ci_high_pp"]) != (
        core_seeded["ci_low_pp"], core_seeded["ci_high_pp"])


def test_cli_empty_wrapper_remains_compatible():
    assert cli.day_equal_ci(None) == (
        None, None, "date_cluster_bootstrap(day-equal, n_days<2 → 无区间)",
    )


def test_cli_stat_cell_survives_a_missing_rows_frame():
    """`rows=None` 的格照旧出全 None 读数,不抛 —— 空格不是失败。"""
    out = cli._stat_cell({"family": "synthetic", "kind": "main", "timing": "R",
                          "sample_kind": "event", "rows": None})
    st = out["stats"]
    assert st["n_events"] == 0 and st["mean_pp"] is None
    assert st["ci_low_pp"] is None and st["ci_high_pp"] is None
    assert st["ci_method"].endswith("n_days<2 → 无区间)")
    assert out["state"] == cli.THIN


def test_cli_oracle_classification_remains_separate_from_statistics():
    result = cli._stat_cell({
        "family": "synthetic", "kind": "main", "timing": "X",
        "sample_kind": "event", "rows": uneven_events(),
    })
    assert result["actionability"] == cli.X_ORACLE
    assert result["verdict"] != cli.POS_HIST
