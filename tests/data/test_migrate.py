"""pkl → parquet lake migration — value-preserving + idempotent."""

import pandas as pd

from autoresearch.data import migrate_cache


def _make_pkl(cache_root, endpoint, day, frame):
    d = cache_root / endpoint
    d.mkdir(parents=True, exist_ok=True)
    fp = d / f"{day}.pkl"
    frame.to_pickle(fp)
    return fp


def test_migrate_preserves_values_dtypes_shape(tmp_path):
    cache_root = tmp_path / "factor_lab" / "cache"
    lake_root = tmp_path / "lake"
    orig = pd.DataFrame(
        {
            "ts_code": ["600000.SH", "000001.SZ"],
            "close": [10.5, 22.1],
            "vol": [1000, 2000],
        }
    )
    _make_pkl(cache_root, "daily", "20240102", orig)

    n = migrate_cache.migrate(cache_root=cache_root, lake_root=lake_root)
    assert n == 1

    out = lake_root / "daily" / "20240102.parquet"
    assert out.exists()
    got = pd.read_parquet(out)
    pd.testing.assert_frame_equal(got, orig)
    assert list(got.dtypes) == list(orig.dtypes)
    assert got.shape == orig.shape


def test_migrate_is_idempotent(tmp_path):
    cache_root = tmp_path / "factor_lab" / "cache"
    lake_root = tmp_path / "lake"
    _make_pkl(cache_root, "daily", "20240102", pd.DataFrame({"a": [1]}))

    first = migrate_cache.migrate(cache_root=cache_root, lake_root=lake_root)
    assert first == 1
    # second run: target exists → skipped (count of newly-written = 0)
    second = migrate_cache.migrate(cache_root=cache_root, lake_root=lake_root)
    assert second == 0


def test_migrate_static_key(tmp_path):
    cache_root = tmp_path / "factor_lab" / "cache"
    lake_root = tmp_path / "lake"
    _make_pkl(cache_root, "stock_basic", "static", pd.DataFrame({"ts_code": ["600000.SH"]}))
    migrate_cache.migrate(cache_root=cache_root, lake_root=lake_root)
    assert (lake_root / "stock_basic" / "static.parquet").exists()


def test_migrate_empty_frame(tmp_path):
    cache_root = tmp_path / "factor_lab" / "cache"
    lake_root = tmp_path / "lake"
    _make_pkl(cache_root, "moneyflow", "20240105", pd.DataFrame())
    n = migrate_cache.migrate(cache_root=cache_root, lake_root=lake_root)
    assert n == 1
    out = lake_root / "moneyflow" / "20240105.parquet"
    assert out.exists()
    assert pd.read_parquet(out).empty
