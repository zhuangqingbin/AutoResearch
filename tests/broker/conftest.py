"""broker 测试共用夹具:一行合成 RAW 帧(数值全假,账户只用别名)。"""
from __future__ import annotations

import pandas as pd
import pytest


def _raw_row(**over) -> dict:
    base = {
        "account": "gtht", "trade_date": "20260826", "trade_time": "09:31:05", "code": "1",
        "name": "平安银行", "biz_type": "证券买入", "price": "12.34", "qty": "100",
        "amount": "1,234.00", "commission": "5", "stamp_tax": "0", "transfer_fee": "0.01",
        "other_fee": "", "net_amount": "-1239.01", "balance_after": "100", "trade_id": "",
    }
    base.update(over)
    return base


@pytest.fixture
def raw():
    """`raw(**over)` → 单行 RAW 帧;`raw.rows(a, b)` → 多行。"""
    def make(**over) -> pd.DataFrame:
        return pd.DataFrame([_raw_row(**over)])
    make.rows = lambda *dicts: pd.DataFrame([_raw_row(**d) for d in dicts])
    return make
