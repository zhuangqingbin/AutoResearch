"""Unit tests for the macro harvester's pure helpers + constant specs.
Network blocks are smoke-run, not unit-tested (see plan)."""
import importlib.util
from pathlib import Path

import pytest

# Load the standalone script as a module (it is not a package).
_SPEC = importlib.util.spec_from_file_location(
    "harvest_macro", Path(__file__).resolve().parent.parent / "scripts" / "harvest_macro.py"
)
harvest_macro = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(harvest_macro)


@pytest.mark.unit
def test_pct_change_formats_signed_percent():
    assert harvest_macro._pct_change(100.0, 110.0) == "+10.00%"
    assert harvest_macro._pct_change(100.0, 90.0) == "-10.00%"
    assert harvest_macro._pct_change(0.0, 5.0) == "n/a"   # zero base -> n/a, never crash


@pytest.mark.unit
def test_constant_specs_cover_required_universe():
    # US policy rate + curve + inflation + labor are non-negotiable.
    for alias in ("fed_funds_rate", "10y_treasury", "yield_curve", "cpi", "unemployment"):
        assert alias in harvest_macro.US_FRED
    # Cross-asset basket must carry the asset universe the user named (incl. JPY + crypto).
    for label in ("USDCNY", "USDJPY", "Gold", "Bitcoin"):
        assert label in harvest_macro.CROSS_ASSET
    # International series are FRED raw IDs (uppercase), used via passthrough.
    assert all(v == v.upper() for v in harvest_macro.INTL_FRED.values())


@pytest.mark.unit
def test_recent_rows_picks_recent_end_regardless_of_sort_order():
    import pandas as pd
    asc = pd.DataFrame({"日期": ["2025-01", "2025-02", "2025-03"], "v": [1, 2, 3]})
    desc = pd.DataFrame({"月份": ["2025年03月份", "2025年02月份", "2025年01月份"], "v": [3, 2, 1]})
    # ascending frame: most-recent rows are at the tail
    assert harvest_macro._recent_rows(asc, n=2)["v"].tolist() == [2, 3]
    # descending frame (akshare PPI/PMI style): most-recent rows are at the head
    assert harvest_macro._recent_rows(desc, n=2)["v"].tolist() == [3, 2]
