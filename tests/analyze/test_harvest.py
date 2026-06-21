"""Unit tests for autoresearch.analyze.harvest pure helpers (L1-row coercion, board limits, benchmarks)."""
import pytest

from autoresearch.analyze import harvest


@pytest.mark.unit
def test_l1_float_coerces_and_rejects_nan():
    assert harvest._l1_float({"x": "12.5"}, "x") == 12.5
    assert harvest._l1_float({"x": 3}, "x") == 3.0
    assert harvest._l1_float({"x": None}, "x") is None
    assert harvest._l1_float({"x": "abc"}, "x") is None
    assert harvest._l1_float({"x": float("nan")}, "x") is None
    assert harvest._l1_float({}, "missing") is None


@pytest.mark.unit
def test_l1_flag_parses_boolish():
    assert harvest._l1_flag({"f": "是"}, "f") is True
    assert harvest._l1_flag({"f": "1"}, "f") is True
    assert harvest._l1_flag({"f": "true"}, "f") is True
    assert harvest._l1_flag({"f": 0}, "f") is False
    assert harvest._l1_flag({"f": "否"}, "f") is False
    assert harvest._l1_flag({"f": None}, "f") is None
    assert harvest._l1_flag({"f": float("nan")}, "f") is None


@pytest.mark.unit
def test_board_limit_bands_by_board():
    assert harvest._board_limit("830799.BJ")[1] == 0.30
    assert harvest._board_limit("300750.SZ")[1] == 0.20   # ChiNext
    assert harvest._board_limit("688981.SS")[1] == 0.20   # STAR
    assert harvest._board_limit("600519.SS")[1] == 0.10   # main board
    assert harvest._board_limit("AAPL")[1] is None         # US: no daily limit


@pytest.mark.unit
def test_benchmarks_by_market():
    assert harvest._benchmarks("600519.SS") == ["000300.SS"]
    assert "159915.SZ" in harvest._benchmarks("300750.SZ")   # ChiNext gets the ETF too
    assert harvest._benchmarks("NVDA") == ["SPY", "SOXX"]    # has a sector ETF
    assert harvest._benchmarks("KO") == ["SPY"]               # no mapped sector ETF


@pytest.mark.unit
def test_is_ashare_suffix_detection():
    assert harvest._is_ashare("600519.SS")
    assert harvest._is_ashare("000001.SZ")
    assert harvest._is_ashare("830799.BJ")
    assert not harvest._is_ashare("NVDA")
    assert not harvest._is_ashare("0700.HK")
