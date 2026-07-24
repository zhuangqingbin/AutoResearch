"""季度对账契约:express 优先/forecast 兜底/未披露 skip/幂等(Wave3 Task 6)。"""
import pandas as pd

from autoresearch.dossier import delta, reconcile, schema
from tests.dossier.test_delta import _mk_dossier


def _fake_fetch(express_df=None, forecast_df=None):
    def fetch(endpoint, params):
        assert params["ts_code"].endswith((".SZ", ".SH", ".BJ"))   # to_ts_code 路由过
        if endpoint == "express":
            return express_df if express_df is not None else pd.DataFrame()
        if endpoint == "forecast":
            return forecast_df if forecast_df is not None else pd.DataFrame()
        raise AssertionError(endpoint)
    return fetch


def test_reconcile_express_writes_s5_s8_and_frontmatter():
    p = _mk_dossier()
    df = pd.DataFrame([{"ann_date": "20260828", "n_income": 2.5e8,
                        "yoy_net_profit": 240.0, "diluted_eps": 0.85}])
    res = reconcile.reconcile_one("300857", "20260630", "2026-08-29",
                                  fetch=_fake_fetch(express_df=df))
    assert res["updated"] and res["kind"] == "express" and res["issues"] == []
    text = p.read_text(encoding="utf-8")
    s5 = delta.section_body(text, 4)
    assert "季度对账 20260630" in s5 and "净利 2.5亿" in s5 and "yoy +240.0%" in s5
    assert "季度对账 20260630" in delta.section_body(text, 7)      # §8 也留痕
    assert schema.parse_frontmatter(text)["last_delta"] == "2026-08-29"
    # 幂等:重跑不重复
    reconcile.reconcile_one("300857", "20260630", "2026-08-29",
                            fetch=_fake_fetch(express_df=df))
    assert p.read_text(encoding="utf-8").count("季度对账 20260630") == 2   # §5 一次 + §8 一次


def test_reconcile_forecast_fallback_and_undisclosed():
    _mk_dossier(code="002371")
    fdf = pd.DataFrame([{"ann_date": "20260815", "type": "预增",
                         "p_change_min": 30.0, "p_change_max": 50.0}])
    res = reconcile.reconcile_one("002371", "20260630", "2026-08-16",
                                  fetch=_fake_fetch(forecast_df=fdf))
    assert res["kind"] == "forecast"
    assert "+30%~+50%" in delta.section_body(
        schema.dossier_path("002371").read_text(encoding="utf-8"), 4)
    res2 = reconcile.reconcile_one("002371", "20261231", "2027-01-05",
                                   fetch=_fake_fetch())
    assert res2["skipped"] == "undisclosed"


def test_reconcile_presence_gated():
    assert reconcile.reconcile_one("999999", "20260630", "2026-08-29",
                                   fetch=_fake_fetch())["skipped"] == "no_dossier"


def test_reconcile_nan_fields_not_rendered():
    _mk_dossier(code="601869")
    df = pd.DataFrame([{"ann_date": "20260828", "n_income": float("nan"),
                        "yoy_net_profit": float("nan"), "diluted_eps": 0.5}])
    reconcile.reconcile_one("601869", "20260630", "2026-08-29",
                            fetch=_fake_fetch(express_df=df))
    s5 = delta.section_body(schema.dossier_path("601869").read_text(encoding="utf-8"), 4)
    assert "nan" not in s5 and "摊薄EPS 0.50" in s5      # NaN 穿 or-默认防线(Wave2 教训)
