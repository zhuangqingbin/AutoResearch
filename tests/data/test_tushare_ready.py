"""assert_tushare_ready 单测 —— 盘后数据就绪硬门。fake pro,无网络。

空结果(端点已发布但当日无数据=跑太早)→ 抛错中止;
异常(权限/网络)→ 跳过不 hard-gate(沿用富因子缺权限降级语义)。
"""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.data.tushare_source import assert_tushare_ready


class _FakePro:
    def __init__(self, n=None, raise_on=()):
        self._n = n or {"daily": 5, "moneyflow": 5, "cyq_perf": 5}
        self._raise = set(raise_on)

    def _mk(self, ep):
        if ep in self._raise:
            raise RuntimeError("您没有访问该接口的权限")
        return pd.DataFrame({"ts_code": [f"{i:06d}.SZ" for i in range(self._n[ep])]})

    def daily(self, **kw):
        return self._mk("daily")

    def moneyflow(self, **kw):
        return self._mk("moneyflow")

    def cyq_perf(self, **kw):
        return self._mk("cyq_perf")


def test_all_present_no_raise():
    assert_tushare_ready(_FakePro(), "20260624")          # 三端点都有数据 → 不抛


def test_daily_empty_raises_and_aborts():
    with pytest.raises(RuntimeError, match="未就绪|daily"):
        assert_tushare_ready(_FakePro(n={"daily": 0, "moneyflow": 5, "cyq_perf": 5}), "20260624")


def test_moneyflow_empty_raises():
    with pytest.raises(RuntimeError, match="未就绪|moneyflow|主力"):
        assert_tushare_ready(_FakePro(n={"daily": 5, "moneyflow": 0, "cyq_perf": 5}), "20260624")


def test_permission_exception_does_not_block():
    # cyq_perf 抛权限异常(非空结果)→ 不据此 hard-gate(daily/moneyflow 有数据 → 放行)
    assert_tushare_ready(_FakePro(raise_on=("cyq_perf",)), "20260624")
