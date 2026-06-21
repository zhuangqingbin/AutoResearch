"""Unit tests for autoresearch.macro.tushare_macro pure helpers (date math, percentile)."""
import pytest

from autoresearch.macro import tushare_macro


@pytest.mark.unit
def test_back_subtracts_calendar_days():
    assert tushare_macro._back("20260620", 16) == "20260604"
    assert tushare_macro._back("20260101", 1) == "20251231"


@pytest.mark.unit
def test_pctile_ranks_current_against_history():
    import pandas as pd
    hist = pd.Series([10.0, 20.0, 30.0, 40.0])
    assert tushare_macro._pctile(hist, 25.0) == 50.0    # 2 of 4 below 25
    assert tushare_macro._pctile(hist, 5.0) == 0.0       # nothing below
    import math
    assert math.isnan(tushare_macro._pctile(pd.Series([], dtype=float), 5.0))   # empty -> nan
