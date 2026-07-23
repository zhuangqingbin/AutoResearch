import pandas as pd

from autoresearch.dossier.mainbz import mainbz_latest


def _fake_fetch(endpoint, params):
    assert endpoint == "fina_mainbz" and params["type"] == "P"
    if params["period"] == "20251231":
        return pd.DataFrame([
            {"ts_code": "300857.SZ", "end_date": "20251231", "bz_item": "数据存储设备",
             "bz_sales": 4.49e9, "bz_profit": 8.0e8},
            {"ts_code": "300857.SZ", "end_date": "20251231", "bz_item": "智能算力产品及服务",
             "bz_sales": 2.76e9, "bz_profit": 6.1e8},
        ])
    return pd.DataFrame()          # 更早期无数据


def test_mainbz_latest_two_periods_desc():
    rows = mainbz_latest("300857", "2026-07-23", fetch=_fake_fetch)
    assert rows and rows[0]["period"] == "20251231"
    assert {r["bz_item"] for r in rows} == {"数据存储设备", "智能算力产品及服务"}


def test_mainbz_latest_fetch_crash_returns_empty():
    def boom(endpoint, params):
        raise RuntimeError("权限不足 40203")
    assert mainbz_latest("300857", "2026-07-23", fetch=boom) == []


def test_policy_and_contract_registered():
    from autoresearch.data.contracts import CONTRACTS
    from autoresearch.data.endpoints import policy
    assert policy("fina_mainbz")["source"] == "tushare"
    assert "fina_mainbz" in CONTRACTS


def test_ts_code_routing_via_single_source(monkeypatch):
    seen = {}

    def spy(endpoint, params):
        seen[params["ts_code"]] = True
        return pd.DataFrame()

    mainbz_latest("920819", "2026-07-23", fetch=spy)
    assert "920819.BJ" in seen and "920819.SH" not in seen


def test_recent_periods_disclosure_lag():
    from autoresearch.dossier.mainbz import _recent_periods
    assert _recent_periods("2026-01-15", 2)[:2] == ["20250630", "20241231"]
    assert _recent_periods("2026-05-01", 2)[:2] == ["20251231", "20250630"]
    assert _recent_periods("2026-07-23", 2)[:2] == ["20251231", "20250630"]
    assert _recent_periods("2026-09-10", 2)[:2] == ["20260630", "20251231"]


def test_nan_cells_sanitized():
    def f(endpoint, params):
        if params["period"] == "20251231":
            return pd.DataFrame([{"ts_code": "300857.SZ", "bz_item": "X",
                                  "bz_sales": float("nan"), "bz_profit": float("nan")}])
        return pd.DataFrame()
    rows = mainbz_latest("300857", "2026-07-23", fetch=f)
    assert rows[0]["bz_sales"] == 0.0 and rows[0]["bz_profit"] is None
