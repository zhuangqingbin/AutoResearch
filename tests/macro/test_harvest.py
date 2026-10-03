"""Unit tests for the macro harvester's pure helpers + constant specs.
Network blocks are smoke-run, not unit-tested (see plan)."""
import pytest

from autoresearch.macro import harvest as harvest_macro


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


@pytest.mark.unit
def test_basket_table_renders_rows_and_handles_missing():
    rows = [
        {"label": "Gold", "symbol": "GC=F", "last": 2400.0, "chg_1m": "+3.10%", "chg_ytd": "+15.00%"},
        {"label": "USDCNY", "symbol": "CNY=X", "last": None, "chg_1m": "n/a", "chg_ytd": "n/a"},
    ]
    table = harvest_macro._basket_table(rows)
    assert "| Gold | GC=F | 2400.0 | +3.10% | +15.00% |" in table
    assert "| USDCNY | CNY=X | n/a | n/a | n/a |" in table   # None last -> 'n/a', no crash
    assert table.startswith("| Asset | Symbol |")


@pytest.mark.parametrize("block", ["us_macro_block", "global_macro_block"])
def test_fred_blocks_forward_vintage_and_cutoff(monkeypatch, block):
    from unittest.mock import Mock
    tool = Mock()
    tool.invoke.return_value = "macro"
    monkeypatch.setattr(harvest_macro, "get_macro_indicators", tool)
    getattr(harvest_macro, block)("2025-07-15", vintage_date="2025-07-14", knowledge_cutoff="2025-07-14")
    for call in tool.invoke.call_args_list:
        assert call.args[0]["vintage_date"] == "2025-07-14"
        assert call.args[0]["knowledge_cutoff"] == "2025-07-14"


def test_snapshot_and_cli_forward_fred_options(monkeypatch, tmp_path):
    from unittest.mock import Mock
    for name in ("china_macro_block", "cross_asset_block", "meso_ashare_best"):
        monkeypatch.setattr(harvest_macro, name, lambda d: "other")
    us = Mock(spec=lambda date, **kwargs: None, return_value="US")
    global_ = Mock(spec=lambda date, **kwargs: None, return_value="global")
    monkeypatch.setattr(harvest_macro, "us_macro_block", us)
    monkeypatch.setattr(harvest_macro, "global_macro_block", global_)
    monkeypatch.setattr(harvest_macro, "global_tape_payload", lambda d: {"ok": False})
    monkeypatch.setattr(harvest_macro, "overseas_calendar_payload", lambda d: {"ok": False})
    snapshot = harvest_macro.collect_harvest_snapshot("2025-07-15", scan_root=tmp_path, vintage_date="2025-07-14", knowledge_cutoff="2025-07-14")
    us.assert_called_once_with("2025-07-15", vintage_date="2025-07-14", knowledge_cutoff="2025-07-14")
    global_.assert_called_once_with("2025-07-15", vintage_date="2025-07-14", knowledge_cutoff="2025-07-14")
    collector = Mock(return_value=snapshot)
    monkeypatch.setattr(harvest_macro, "collect_harvest_snapshot", collector)
    assert harvest_macro.main(["2025-07-15", "--output-dir", str(tmp_path), "--vintage-date", "2025-07-14", "--knowledge-cutoff", "2025-07-14"]) == 0
    collector.assert_called_once_with("2025-07-15", vintage_date="2025-07-14", knowledge_cutoff="2025-07-14")
