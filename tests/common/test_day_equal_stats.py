#!/usr/bin/env python3
"""`common.stats.day_equal_bootstrap` —— 日等权点估计与区间是**同一个估计量**。

治的病:`date_cluster_bootstrap` 按日聚簇重采样,但重采样后对**行**取均值 —— 那是
行等权点估计。热闹的一天(100 只)会拿走 100 倍权重。而全稿判读的量是**日等权**均值。
两者在「每天行数不等」时不是同一个数,于是「均值」与「它的区间」描述的是两个不同的
估计对象。本文件锁的就是新原语只报一个估计量。

⚠️ 旧原语 `date_cluster_bootstrap` 的语义**不动**(它还有别的消费者按事件加权用它),
本文件最后一条用例把这条边界钉住:两个函数在同一份不平衡样本上必须给出**不同**的点估计。
"""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.common import stats


def unbalanced_frame() -> pd.DataFrame:
    """40 个交易日:前 20 日每天 100 行 +1pp,后 20 日每天 1 行 −1pp。

    日等权 → 0.0;行等权 → 1980/2020 ≈ +0.98。两个数差了一个数量级,任何一处口径
    错位都躲不过。
    """
    rows = []
    for i, day in enumerate(pd.bdate_range("2026-01-05", periods=40)):
        value, count = (1.0, 100) if i < 20 else (-1.0, 1)
        rows.extend({"date": day.strftime("%Y%m%d"), "value": value}
                    for _ in range(count))
    return pd.DataFrame(rows)


def test_day_equal_estimator_does_not_weight_busy_days_more():
    result = stats.day_equal_bootstrap(unbalanced_frame(), "value")
    assert result.point == pytest.approx(0.0)
    assert result.lo < 0 < result.hi
    assert result.n == 2020            # 有效事件行数:只描述覆盖
    assert result.n_clusters == 40     # 有效观测日数:这才是统计权重的单位


def test_replicating_one_days_complete_population_changes_no_estimate():
    """把某一天的**全部**事件等比例复制 → 点估计与区间都不动(日内复制不变性)。"""
    frame = unbalanced_frame()
    duplicated = pd.concat([frame, frame[frame["date"] == "20260105"]])
    a = stats.day_equal_bootstrap(frame, "value", seed=7)
    b = stats.day_equal_bootstrap(duplicated, "value", seed=7)
    assert (a.point, a.lo, a.hi) == (b.point, b.lo, b.hi)
    assert b.n > a.n                          # 覆盖变了
    assert a.n_clusters == b.n_clusters       # 权重没变


def test_empty_and_single_day_do_not_fabricate_intervals():
    empty = pd.DataFrame({"date": [], "value": []})
    a = stats.day_equal_bootstrap(empty, "value")
    assert (a.point, a.lo, a.hi, a.n, a.n_clusters) == (None, None, None, 0, 0)
    single = pd.DataFrame({"date": ["20260105", "20260105"], "value": [1, 3]})
    b = stats.day_equal_bootstrap(single, "value")
    # 单日有点估计,但没有跨日方差 → 给一个区间就是撒谎
    assert (b.point, b.lo, b.hi, b.n, b.n_clusters) == (2.0, None, None, 2, 1)


def test_invalid_rows_are_excluded_without_inventing_dates():
    frame = pd.DataFrame({
        "date": ["20260105", None, "20260106", ""],
        "value": [1.0, 9.0, "bad", 9.0],
    })
    result = stats.day_equal_bootstrap(frame, "value")
    # 只剩 (20260105, 1.0):日期缺失的行不会被塞进某一天,不可解析的值不会变成 0
    assert (result.point, result.n, result.n_clusters) == (1.0, 1, 1)
    assert result.lo is None and result.hi is None


def test_non_finite_values_are_rejected_not_silently_averaged():
    """±inf 是 A 级坏输入(收益的分母为零)。均值会被它吞掉,所以宁可炸。"""
    frame = pd.DataFrame({"date": ["20260105", "20260106"],
                          "value": [1.0, float("inf")]})
    with pytest.raises(ValueError):
        stats.day_equal_bootstrap(frame, "value")


def test_missing_columns_and_invalid_bootstrap_parameters_raise():
    frame = unbalanced_frame()
    with pytest.raises(ValueError):
        stats.day_equal_bootstrap(frame.drop(columns="date"), "value")
    with pytest.raises(ValueError):
        stats.day_equal_bootstrap(frame, "missing")
    with pytest.raises(ValueError):
        stats.day_equal_bootstrap(frame, "value", n_boot=0)
    # `True` 是 int 且 `True >= 1` —— 不单独拦 bool 的话它会静默跑成「重采样 1 次」。
    # (变异探针:去掉 isinstance(n_boot, bool) 这一腿,本行是唯一会变红的断言。)
    with pytest.raises(ValueError):
        stats.day_equal_bootstrap(frame, "value", n_boot=True)
    with pytest.raises(ValueError):
        stats.day_equal_bootstrap(frame, "value", alpha=1.0)


def test_fixed_seed_and_row_order_are_stable():
    """同 seed 同结果,且与行序无关。

    ⚠️ 这里能用**精确相等**只因为夹具里同一天的值全等(浮点求和顺序无影响)。一般情形
    只保证到 ulp 级 —— 别把这条用例读成「任何输入都逐位可复现」。
    """
    frame = unbalanced_frame()
    a = stats.day_equal_bootstrap(frame, "value", seed=13)
    b = stats.day_equal_bootstrap(frame.iloc[::-1], "value", seed=13)
    assert a == b


def test_method_string_carries_estimator_boot_count_and_seed():
    """方法串必须自报「日等权 + 重采样次数 + seed」—— 读数的可复现性全靠它。"""
    result = stats.day_equal_bootstrap(unbalanced_frame(), "value", n_boot=64, seed=99)
    assert result.method.startswith("day_equal/")
    assert "B=64" in result.method and "seed=99" in result.method


def test_old_event_weighted_primitive_retains_its_meaning():
    """旧原语不改语义:同一份样本上两个函数必须给出**不同**的点估计。"""
    frame = unbalanced_frame()
    old = stats.date_cluster_bootstrap(frame, "value")
    assert old.point == pytest.approx(1980 / 2020)
    new = stats.day_equal_bootstrap(frame, "value")
    assert new.point == pytest.approx(0.0)
