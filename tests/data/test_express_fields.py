"""业绩快报字段语义契约(C-1 回归,2026-07-24 终审)。

两条病:
  A 语义 —— `yoy_net_profit` 是**去年同期净利润金额**,被当增速直接渲染
    → 活体 419 处 slim 印出 `净利同比 **+385673800.0%**` + 6 处 `+nan%`,
    并改写过决策(context/scan/2026-07-21/details/688766.md 把真实的 −30% 预减
    当成"与快报口径矛盾"降权)。
  B 时效 —— `pro.express(ts_code=...)` 不带 period 取全历史,`tail(1)` 拿到的
    可能是 20121231 的陈年快报,却被冠以"快报=未审计,早于正式财报"喂进卡。
"""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.data import tushare_enrich
from autoresearch.data.express_fields import (
    EXPRESS_MAX_MONTHS,
    express_expired,
    express_yoy_pct,
)

# ───────────────────────── 纯函数:同比自算 ─────────────────────────


@pytest.mark.unit
def test_express_yoy_pct_live_truth_688766():
    """活体真值:688766 普冉股份 20251231 快报(误读版本印的是 +292416600.0%)。

    自算 −28.8% 与该票 forecast 腿独立口径的"略减 −29.89%"吻合 = 自算路正确而非凑数。
    """
    assert express_yoy_pct(208232900.0, 292416600.0) == pytest.approx(-28.8, abs=0.1)


@pytest.mark.unit
@pytest.mark.parametrize("base", [0, 0.0, -5.0e7, None, float("nan")])
def test_express_yoy_pct_none_when_base_unusable(base):
    """去年同期 ≤0 → 增速无意义;缺/NaN → None(不得除零、不得放 NaN 进 format)。"""
    assert express_yoy_pct(1e8, base) is None


@pytest.mark.unit
def test_express_yoy_pct_none_when_current_missing():
    assert express_yoy_pct(None, 2.0e8) is None
    assert express_yoy_pct(float("nan"), 2.0e8) is None
    assert express_yoy_pct("x", 2.0e8) is None


@pytest.mark.unit
def test_express_yoy_pct_growth_is_percent_scale():
    """量级契约:2.5亿 vs 去年 2.0亿 = +25%(而非 +2.0e8%)。"""
    assert express_yoy_pct(2.5e8, 2.0e8) == pytest.approx(25.0)
    assert abs(express_yoy_pct(2.5e8, 2.0e8)) < 1e4     # 永远不该是 8 位数


# ───────────────────────── 纯函数:时效守卫 ─────────────────────────


@pytest.mark.unit
def test_express_expired_ancient_period_is_expired():
    assert express_expired("20121231", "2026-07-24") is True
    assert express_expired("20201231", "2026-07-24") is True


@pytest.mark.unit
def test_express_expired_recent_period_is_fresh():
    assert express_expired("20251231", "2026-07-24") is False      # 7 个月
    assert express_expired("20250630", "2026-07-24") is False      # 13 个月,仍在 15 内


@pytest.mark.unit
def test_express_expired_boundary_15_months():
    assert express_expired("20250430", "2026-07-24", max_months=15) is False   # 15 个月 = 不过期
    assert express_expired("20250331", "2026-07-24", max_months=15) is True    # 16 个月 = 过期


@pytest.mark.unit
def test_express_expired_unparseable_end_date_treated_as_expired():
    """无法证明其新鲜 = 不采用(降级留痕由调用方渲染,不静默丢)。"""
    for bad in (None, "", "nan", float("nan"), "2026", "abcdefgh"):
        assert express_expired(bad, "2026-07-24") is True


# ───────────────────────── 渲染腿:ashare_calendar_ts ─────────────────────────


class _FakePro:
    """注入用假 pro:只实现 express/forecast 两个端点(_ts_call 无异常 → 零 sleep)。"""

    def __init__(self, express_df=None, forecast_df=None):
        self._ex = express_df if express_df is not None else pd.DataFrame()
        self._fc = forecast_df if forecast_df is not None else pd.DataFrame()

    def express(self, **kw):
        return self._ex

    def forecast(self, **kw):
        return self._fc


def _render(monkeypatch, express_df, curr_date="2026-07-24", sym="688766.SS") -> str:
    monkeypatch.setattr(tushare_enrich, "_pro", lambda: _FakePro(express_df=express_df))
    return tushare_enrich.ashare_calendar_ts(sym, curr_date) or ""


@pytest.mark.unit
def test_calendar_express_renders_self_computed_yoy_not_amount(monkeypatch):
    """活体真值走完整渲染腿:不得出现 8 位数百分比,同比是合理量级。"""
    df = pd.DataFrame([{"ann_date": "20260129", "end_date": "20251231",
                        "n_income": 208232900.0, "yoy_net_profit": 292416600.0,
                        "diluted_roe": 9.0}])
    txt = _render(monkeypatch, df)
    assert "净利同比 **-28.8%**" in txt
    assert "292416600" not in txt
    assert "摊薄ROE 9.00%" in txt and "净利 2.08亿" in txt
    assert "业绩快报(tushare,20251231)" in txt


@pytest.mark.unit
def test_calendar_express_no_eight_digit_percent_and_no_nan_percent(monkeypatch):
    """yoy base 缺/NaN、ROE NaN → 不渲染同比段、不出 `nan%`,净利仍渲染。"""
    df = pd.DataFrame([{"ann_date": "20260129", "end_date": "20251231",
                        "n_income": 1.0e8, "yoy_net_profit": float("nan"),
                        "diluted_roe": float("nan")}])
    txt = _render(monkeypatch, df)
    assert "nan" not in txt.lower()
    assert "净利同比" not in txt
    assert "净利 1.00亿" in txt


@pytest.mark.unit
def test_calendar_express_stale_period_takes_expired_branch(monkeypatch):
    """陈年快报(20121231):不当快报渲染,改降级留痕一行(不空写不静默)。"""
    df = pd.DataFrame([{"ann_date": "20130228", "end_date": "20121231",
                        "n_income": 3.1e7, "yoy_net_profit": 1.0e5,
                        "diluted_roe": 16.32}])
    txt = _render(monkeypatch, df)
    assert f"_业绩快报:最新一期 20121231 已过期(>{EXPRESS_MAX_MONTHS}个月),不采用_" in txt
    assert "业绩快报(tushare,20121231)" not in txt      # 不冒充"早于正式财报"的前瞻信号
    assert "%" not in txt


@pytest.mark.unit
def test_calendar_express_picks_latest_ann_date(monkeypatch):
    """多行(含修正公告):按 ann_date 取最新那条(既有行为,回归锁定)。"""
    df = pd.DataFrame([
        {"ann_date": "20260129", "end_date": "20251231", "n_income": 1.0e8,
         "yoy_net_profit": 2.0e8, "diluted_roe": 4.0},
        {"ann_date": "20260227", "end_date": "20251231", "n_income": 2.08e8,
         "yoy_net_profit": 2.92e8, "diluted_roe": 9.0},
    ])
    txt = _render(monkeypatch, df)
    assert "净利 2.08亿" in txt and "摊薄ROE 9.00%" in txt
    assert "-50.0%" not in txt          # 旧行(1.0亿/2.0亿)的同比不得胜出


@pytest.mark.unit
def test_calendar_express_all_key_fields_missing_leaves_trace(monkeypatch):
    """关键字段全缺 → 留痕行而非空写(降级留痕契约)。"""
    df = pd.DataFrame([{"ann_date": "20260129", "end_date": "20251231",
                        "n_income": None, "yoy_net_profit": None, "diluted_roe": None}])
    txt = _render(monkeypatch, df)
    assert "_业绩快报(20251231):关键字段全缺,不渲染_" in txt
