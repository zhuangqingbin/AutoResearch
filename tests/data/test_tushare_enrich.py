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
def test_tscode_bare_code_uses_leading_digit_not_sh_default():
    # 回归:裸 6 位码曾默认 .SH(l4_card._tushare_pledge 喂裸码)→ 深市/北交所
    # 质押查错交易所恒空。修复后按首位判交易所(92xxxx 北交所优先)。
    assert tushare_enrich._tscode("000001") == "000001.SZ"
    assert tushare_enrich._tscode("300750") == "300750.SZ"
    assert tushare_enrich._tscode("830799") == "830799.BJ"
    assert tushare_enrich._tscode("920000") == "920000.BJ"
    assert tushare_enrich._tscode("600519") == "600519.SH"


@pytest.mark.unit
def test_num_coerces_non_numeric_to_nan():
    import pandas as pd
    s = tushare_enrich._num(pd.Series(["1.5", "x", "3"]))
    assert s.iloc[0] == 1.5
    assert pd.isna(s.iloc[1])
    assert s.iloc[2] == 3.0
