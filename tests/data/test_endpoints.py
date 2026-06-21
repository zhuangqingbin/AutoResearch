"""endpoint policy registry — settle/key/source classification."""

import pytest

from autoresearch.data import endpoints


def test_daily_is_settled_eod_tushare():
    pol = endpoints.policy("daily")
    assert pol["settle"] == "eod"
    assert pol["key"] == "date"
    assert pol["source"] == "tushare"


def test_spot_em_is_live_and_uncached():
    pol = endpoints.policy("stock_zh_a_spot_em")
    assert pol["settle"] == "live"
    assert pol["key"] is None
    assert pol["source"] == "akshare"


def test_as_of_snapshot_endpoint():
    # 股东户数 = per-fetch-day snapshot, keyed by entity@as_of
    pol = endpoints.policy("stock_zh_a_gdhs_detail_em")
    assert pol["key"] == "as_of"
    assert pol["settle"] == "eod"


def test_static_endpoint():
    pol = endpoints.policy("stock_basic")
    assert pol["key"] == "static"


def test_fred_source():
    # any FRED series id resolves to the fred source with eod settle
    pol = endpoints.policy("fred")
    assert pol["source"] == "fred"
    assert pol["settle"] == "eod"


def test_unknown_endpoint_raises_keyerror():
    with pytest.raises(KeyError):
        endpoints.policy("nope_not_a_real_endpoint")


def test_every_entry_is_wellformed():
    for name, pol in endpoints.ENDPOINTS.items():
        assert pol["key"] in {"date", "period", "as_of", "static", None}, name
        assert pol["settle"] in {"eod", "live"}, name
        assert pol["source"] in {"tushare", "akshare", "fred", "yfinance"}, name
        # live endpoints must not be keyed (they are never written to the lake)
        if pol["settle"] == "live":
            assert pol["key"] is None, name
