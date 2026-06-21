"""parquet lake cache — exists=hit, atomic write, settled-vs-today rule."""

import pandas as pd
import pytest

from autoresearch.data import cache


@pytest.fixture
def lake(tmp_path, monkeypatch):
    """Redirect the lake root to a tmp dir so tests never touch context/lake/."""
    root = tmp_path / "lake"
    monkeypatch.setattr(cache, "LAKE", root)
    return root


class _Counter:
    """A fake fetch that records call count and returns a fixed frame."""

    def __init__(self, frame):
        self.frame = frame
        self.calls = 0

    def __call__(self, endpoint, params):
        self.calls += 1
        return self.frame.copy()


def test_first_fetch_writes_then_second_call_hits(lake):
    fetch = _Counter(pd.DataFrame({"code": ["600000"], "close": [10.5]}))
    # settled past date → cacheable
    out1 = cache.get_or_fetch("daily", {"trade_date": "20240102"}, today="20260622", fetch=fetch)
    assert fetch.calls == 1
    assert out1["close"].iloc[0] == 10.5
    path = cache.lake_path("daily", {"trade_date": "20240102"})
    assert path.exists()
    assert path.suffix == ".parquet"

    out2 = cache.get_or_fetch("daily", {"trade_date": "20240102"}, today="20260622", fetch=fetch)
    assert fetch.calls == 1  # HIT, fetch NOT called again
    pd.testing.assert_frame_equal(out1.reset_index(drop=True), out2.reset_index(drop=True))


def test_empty_result_writes_empty_parquet_and_still_hits(lake):
    fetch = _Counter(pd.DataFrame())
    out = cache.get_or_fetch("daily", {"trade_date": "20240103"}, today="20260622", fetch=fetch)
    assert fetch.calls == 1
    assert out.empty
    assert cache.lake_path("daily", {"trade_date": "20240103"}).exists()
    # second call: existence == "fetched, empty" → hit, no refetch
    out2 = cache.get_or_fetch("daily", {"trade_date": "20240103"}, today="20260622", fetch=fetch)
    assert fetch.calls == 1
    assert out2.empty


def test_live_endpoint_never_caches(lake):
    fetch = _Counter(pd.DataFrame({"code": ["600000"]}))
    cache.get_or_fetch("stock_zh_a_spot_em", {}, today="20260622", fetch=fetch)
    cache.get_or_fetch("stock_zh_a_spot_em", {}, today="20260622", fetch=fetch)
    assert fetch.calls == 2  # always fetch, never written
    # nothing persisted under the live endpoint
    assert not (lake / "stock_zh_a_spot_em").exists()


def test_date_equal_today_is_fetched_fresh_not_written(lake):
    fetch = _Counter(pd.DataFrame({"code": ["600000"], "close": [9.9]}))
    out = cache.get_or_fetch("daily", {"trade_date": "20260622"}, today="20260622", fetch=fetch)
    assert fetch.calls == 1
    assert out["close"].iloc[0] == 9.9
    # unsettled (date >= today) → NOT written
    assert not cache.lake_path("daily", {"trade_date": "20260622"}).exists()
    # next call refetches (still unsettled)
    cache.get_or_fetch("daily", {"trade_date": "20260622"}, today="20260622", fetch=fetch)
    assert fetch.calls == 2


def test_future_date_is_unsettled_not_written(lake):
    fetch = _Counter(pd.DataFrame({"code": ["600000"]}))
    cache.get_or_fetch("daily", {"trade_date": "20260630"}, today="20260622", fetch=fetch)
    assert not cache.lake_path("daily", {"trade_date": "20260630"}).exists()


def test_as_of_key_includes_entity_and_fetch_day(lake):
    fetch = _Counter(pd.DataFrame({"holder_num": [12345]}))
    params = {"symbol": "600519", "as_of": "20260101"}
    cache.get_or_fetch("stock_zh_a_gdhs_detail_em", params, today="20260622", fetch=fetch)
    p = cache.lake_path("stock_zh_a_gdhs_detail_em", params)
    assert "600519@20260101" in p.stem
    assert p.exists()


def test_static_endpoint_keyed_static(lake):
    fetch = _Counter(pd.DataFrame({"ts_code": ["600000.SH"], "name": ["x"]}))
    cache.get_or_fetch("stock_basic", {"list_status": "L"}, today="20260622", fetch=fetch)
    p = cache.lake_path("stock_basic", {"list_status": "L"})
    assert p.stem == "static"
    assert p.exists()
    # second call hits regardless of params (static key is param-independent)
    cache.get_or_fetch("stock_basic", {"list_status": "L"}, today="20260622", fetch=fetch)
    assert fetch.calls == 1


def test_default_today_uses_compact_date(lake, monkeypatch):
    # when today is omitted it derives from date.today() in compact form; a clearly
    # past date must therefore be treated as settled and written.
    fetch = _Counter(pd.DataFrame({"x": [1]}))
    cache.get_or_fetch("daily", {"trade_date": "20200101"}, fetch=fetch)
    assert cache.lake_path("daily", {"trade_date": "20200101"}).exists()
