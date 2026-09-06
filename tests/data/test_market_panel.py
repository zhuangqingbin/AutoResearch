"""E4:湖读取搬到 `data/market_panel.py` —— IO 归 data 层,纯计算归 common。"""
import pandas as pd
import pytest

from autoresearch.data import market_panel


def _write(day_dir, day, rows):
    pd.DataFrame(rows, columns=["ts_code", "open", "high", "low", "close", "pct_chg", "amount"]) \
        .to_parquet(day_dir / f"{day}.parquet")


@pytest.fixture()
def lake_daily(tmp_path):
    d = tmp_path / "daily"
    d.mkdir()
    _write(d, "20260901", [("600000.SH", 10.0, 10.2, 9.8, 10.1, 1.0, 5000.0),
                           ("000001.SZ", 20.0, 20.4, 19.6, 20.2, 1.0, 8000.0)])
    _write(d, "20260902", [("600000.SH", 10.1, 10.5, 10.0, 10.4, 3.0, 6000.0)])
    (d / "notadate.parquet").write_bytes(b"")          # 非日期文件名:必须被忽略
    return d


def test_lake_trade_days_is_filename_order_only(lake_daily):
    assert market_panel.lake_trade_days(lake_daily) == ["20260901", "20260902"]


def test_lake_trade_days_skips_missing_root(tmp_path):
    assert market_panel.lake_trade_days(tmp_path / "nope") == []


def test_load_lake_pivots_zfills_code_and_keeps_six_fields(lake_daily):
    piv = market_panel.load_lake_pivots(["20260901", "20260902"], lake_daily)
    assert set(piv) == {"open", "high", "low", "close", "pct_chg", "amount"}
    assert list(piv["close"].index) == ["000001", "600000"]      # ts_code → 6 位代码
    assert piv["close"].loc["600000", "20260902"] == 10.4
    assert pd.isna(piv["close"].loc["000001", "20260902"])       # 当日无该票 → NaN,不补零


def test_load_lake_pivots_missing_day_is_skipped_not_faked(lake_daily):
    piv = market_panel.load_lake_pivots(["20260901", "20260903"], lake_daily)
    assert list(piv["close"].columns) == ["20260901"]


def test_load_lake_pivots_returns_empty_dict_when_nothing_loaded(lake_daily):
    assert market_panel.load_lake_pivots(["20260808"], lake_daily) == {}
