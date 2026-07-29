"""指数当日行情入湖 + 指数断言可对账(Wave8 W8-14)。

**动机**:2026-07-28 情报稿写「创业板指低开低走跌 7.35%」,`price_claims` 把它报成
不符 —— 但审计器手里只有**个股** OHLCV,拿个股涨跌去对指数断言,真伪根本裁不出来。
那条 warn 只能一直挂着,而我在向用户转述时也只能加一句「未经证实」。

湖里补六个宽基指数的当日行情后:
- `price_claims` 命中指数名/代码的断言改对指数湖裁真伪;
- 湖缺该指数 → 独立态 `UNVERIFIABLE`,**既不算通过也不算不符**(缺证据不是证据);
- `market_pack.today_slice` 多一行三大指数当日涨跌,策略师的盘面句从此可对账。

契约按 B 级:逐指数独立失败不连坐(同 `macro_cn.index_val_data` 先例),整块缺只降级留痕。
"""

from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.data.macro_cn import index_quote_data


class _FakePro:
    """按 ts_code 返回不同结果的假 pro;用来验"逐指数不连坐"。"""

    def __init__(self, table: dict[str, object]):
        self.table = table
        self.calls: list[str] = []

    def index_daily(self, ts_code=None, **kw):
        self.calls.append(ts_code)
        val = self.table.get(ts_code)
        if isinstance(val, Exception):
            raise val
        return val


def _frame(pct: float, close: float = 3000.0, day: str = "20260728") -> pd.DataFrame:
    return pd.DataFrame([{"trade_date": day, "close": close, "pct_chg": pct}])


def test_returns_pct_chg_per_index():
    pro = _FakePro({
        "000001.SH": _frame(-1.16), "399001.SZ": _frame(-4.52),
        "399006.SZ": _frame(-7.35), "000688.SH": _frame(-5.93),
        "000300.SH": _frame(-1.80), "000905.SH": _frame(-3.10),
    })

    out = index_quote_data(pro, "20260728")

    assert out["399006.SZ"]["pct_chg"] == pytest.approx(-7.35)
    assert out["399006.SZ"]["name"]
    assert out["000001.SH"]["pct_chg"] == pytest.approx(-1.16)
    assert len(pro.calls) == 6, "六个宽基指数都要拉"


def test_single_index_failure_does_not_cascade():
    """逐指数独立失败:一个炸了,其余照常有值(B 级契约,同 index_val 先例)。"""
    pro = _FakePro({
        "000001.SH": _frame(-1.16),
        "399006.SZ": RuntimeError("endpoint permission denied"),
        "399001.SZ": _frame(-4.52), "000688.SH": _frame(-5.93),
        "000300.SH": _frame(-1.80), "000905.SH": _frame(-3.10),
    })

    out = index_quote_data(pro, "20260728")

    assert out["000001.SH"]["pct_chg"] == pytest.approx(-1.16)
    assert out["399006.SZ"]["pct_chg"] is None
    assert "error" in out["399006.SZ"], "失败必须留痕,不能静默变成 None"


def test_empty_frame_is_none_not_zero():
    """空返回 → None,**不得**变成 0.0(0% 是一个断言,空是没有断言)。"""
    pro = _FakePro({c: pd.DataFrame() for c in (
        "000001.SH", "399001.SZ", "399006.SZ", "000688.SH", "000300.SH", "000905.SH")})

    out = index_quote_data(pro, "20260728")

    assert all(v["pct_chg"] is None for v in out.values())
    assert all(v.get("error") for v in out.values()), "空返回也要留痕"
