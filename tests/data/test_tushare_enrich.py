"""Unit tests for autoresearch.data.tushare_enrich pure helpers (symbol mapping)."""
import pytest

from autoresearch.data import tushare_enrich


@pytest.mark.unit
def test_tscode_maps_yfinance_suffix_to_tushare():
    # normalize_symbol's .SS/.SZ/.BJ -> tushare .SH/.SZ/.BJ
    assert tushare_enrich._tscode("600519.SS") == "600519.SH"
    assert tushare_enrich._tscode("000001.SZ") == "000001.SZ"
    assert tushare_enrich._tscode("830799.BJ") == "830799.BJ"


@pytest.mark.unit
def test_num_coerces_non_numeric_to_nan():
    import pandas as pd
    s = tushare_enrich._num(pd.Series(["1.5", "x", "3"]))
    assert s.iloc[0] == 1.5
    assert pd.isna(s.iloc[1])
    assert s.iloc[2] == 3.0
