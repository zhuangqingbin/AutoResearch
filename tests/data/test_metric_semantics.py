"""Moneyflow field semantics must not assert investor identity."""
import importlib
from pathlib import Path

import pandas as pd
import pytest

from autoresearch.data import tushare_enrich, tushare_source


def _semantics():
    assert importlib.util.find_spec("autoresearch.data.metric_semantics") is not None
    return importlib.import_module("autoresearch.data.metric_semantics").METRICS


def test_registered_net_flow_is_not_an_institution_identity_field():
    metric = _semantics()["tushare.moneyflow.net_mf_amount"]
    assert metric["display_name"] == "主动买卖单净流入"
    assert metric["unit"] == "万元"
    assert metric["identity_evidence"] is False
    assert metric["canonical_fields"] == ("main_inflow_yi",)
    assert "机构身份" in metric["does_not_establish"]


def test_order_size_proxies_have_separate_definitions():
    metrics = _semantics()
    large = metrics["tushare.moneyflow.large_order_net"]
    small = metrics["tushare.moneyflow.small_order_net"]
    assert large["identity_evidence"] is small["identity_evidence"] is False
    assert large["canonical_fields"] == ("main_net_yi",)
    assert small["canonical_fields"] == ("retail_net_yi",)
    assert "net_mf_amount" not in large["source_fields"]
    with pytest.raises(TypeError):
        large["identity_evidence"] = True


def test_rendered_moneyflow_retains_value_and_discloses_scope(monkeypatch):
    monkeypatch.setattr(tushare_enrich, "_pro", lambda: object())
    monkeypatch.setattr(tushare_enrich, "_last_trade", lambda *_: "20260929")
    monkeypatch.setattr(tushare_enrich, "_trade_days", lambda *_: ["20260929"])
    frame = pd.DataFrame({"ts_code": ["600000.SH"], "trade_date": ["20260929"],
                          "net_mf_amount": [1234.0]})
    monkeypatch.setattr(tushare_enrich, "_lake_market_day",
                        lambda endpoint, *_: frame if endpoint == "moneyflow" else pd.DataFrame())
    text = tushare_enrich.ashare_market_context_ts("600000.SH", "2026-09-29")
    assert "主动买卖单净流入" in text
    assert "不能确认机构身份" in text
    assert "+0.12 亿" in text
    assert "主力净流入" not in text


def test_canonical_numeric_columns_keep_their_values(monkeypatch):
    frame = pd.DataFrame({"ts_code": ["600000.SH"], "net_mf_amount": [1234.0],
                          "buy_lg_amount": [100.0], "buy_elg_amount": [200.0],
                          "sell_lg_amount": [50.0], "sell_elg_amount": [25.0],
                          "buy_sm_amount": [75.0], "sell_sm_amount": [125.0]})
    monkeypatch.setattr(tushare_source, "_lake_fetch", lambda *_: frame)
    out = tushare_source._fetch_moneyflow_struct("20260929", "2026-09-29")
    assert out.iloc[0]["main_inflow_yi"] == pytest.approx(0.1234)
    assert out.iloc[0]["main_net_yi"] == pytest.approx(0.0225)
    assert out.iloc[0]["retail_net_yi"] == pytest.approx(-0.005)


@pytest.mark.parametrize("role", ["l3-rank", "l4-card", "sector-brief"])
def test_roles_separate_moneyflow_proxy_from_institution_identity(role):
    text = (Path(__file__).resolve().parents[2] / f".claude/agents/{role}.md").read_text()
    assert "不能确认机构身份" in text
