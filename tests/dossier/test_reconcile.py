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
    # yoy_net_profit=去年同期净利润金额(2.0亿),非增长率;n_income=2.5亿 → 自算 +25.0%
    df = pd.DataFrame([{"ann_date": "20260828", "n_income": 2.5e8,
                        "yoy_net_profit": 2.0e8, "diluted_eps": 0.85}])
    res = reconcile.reconcile_one("300857", "20260630", "2026-08-29",
                                  fetch=_fake_fetch(express_df=df))
    assert res["updated"] and res["kind"] == "express" and res["issues"] == []
    text = p.read_text(encoding="utf-8")
    s5 = delta.section_body(text, 4)
    assert "季度对账 20260630" in s5 and "净利 2.5亿" in s5 and "yoy +25.0%" in s5
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


def test_reconcile_forecast_nan_type_not_rendered():
    """forecast 兜底路:type=NaN 且 p_change 双缺 → 字符串腿 NaN 不得渲染(Important-1 回归)。"""
    _mk_dossier(code="002415")
    fdf = pd.DataFrame([{"ann_date": float("nan"), "type": float("nan"),
                         "p_change_min": float("nan"), "p_change_max": float("nan")}])
    res = reconcile.reconcile_one("002415", "20260630", "2026-08-16",
                                  fetch=_fake_fetch(forecast_df=fdf))
    assert res["kind"] == "forecast"
    text = schema.dossier_path("002415").read_text(encoding="utf-8")
    s5 = delta.section_body(text, 4)
    s8 = delta.section_body(text, 7)
    assert "预告类型 —" in s5
    assert "nan" not in s5.lower() and "nan" not in s8.lower()


def test_reconcile_express_picks_latest_ann_date_not_first_row():
    """多行修正公告:旧行放 df 第 0 位,须按 ann_date 降序锁最新披露(Important-2 回归)。"""
    _mk_dossier(code="000725")
    df = pd.DataFrame([
        {"ann_date": "20260815", "n_income": 1e8, "yoy_net_profit": 0.8e8, "diluted_eps": 0.20},
        {"ann_date": "20260828", "n_income": 2.5e8, "yoy_net_profit": 2.0e8, "diluted_eps": 0.85},
    ])
    res = reconcile.reconcile_one("000725", "20260630", "2026-08-29",
                                  fetch=_fake_fetch(express_df=df))
    assert res["updated"]
    s5 = delta.section_body(schema.dossier_path("000725").read_text(encoding="utf-8"), 4)
    assert "净利 2.5亿" in s5 and "yoy +25.0%" in s5
    assert "20260828" in s5
    assert "净利 1.0亿" not in s5 and "20260815" not in s5


def test_reconcile_express_yoy_self_computed_from_live_values():
    """活体真值回归(688766 兰剑智能 20251231 快报,2026-07-24 逮出):yoy_net_profit
    是去年同期净利润金额(元)非增长率,须自算 (n_income/base-1)*100;真值 -28.8% 与该票
    forecast 腿独立口径的"略减 -29.89%"吻合,证明自算路正确而非巧合凑数。
    """
    _mk_dossier(code="688766")
    df = pd.DataFrame([{"ann_date": "20260129", "n_income": 208232900.0,
                        "yoy_net_profit": 292416600.0, "diluted_eps": 1.41,
                        "diluted_roe": 9.0}])
    res = reconcile.reconcile_one("688766", "20251231", "2026-01-30",
                                  fetch=_fake_fetch(express_df=df))
    assert res["updated"]
    s5 = delta.section_body(schema.dossier_path("688766").read_text(encoding="utf-8"), 4)
    assert "yoy -28.8%" in s5
    assert "净利 2.1亿" in s5
    assert "ROE 9.0%" in s5
    assert "292416600" not in s5


def test_reconcile_yoy_base_zero_or_negative_shows_not_applicable_no_div_zero():
    """去年同期净利润 <=0(为零或亏损)→ 增速无意义,只报金额,不出现除零/inf/nan。"""
    _mk_dossier(code="688767")
    df_zero = pd.DataFrame([{"ann_date": "20260828", "n_income": 1e8,
                             "yoy_net_profit": 0.0, "diluted_eps": 0.5}])
    reconcile.reconcile_one("688767", "20260630", "2026-08-29",
                            fetch=_fake_fetch(express_df=df_zero))
    s5_zero = delta.section_body(schema.dossier_path("688767").read_text(encoding="utf-8"), 4)
    assert "增速不适用" in s5_zero
    assert "去年同期 0.0亿" in s5_zero
    assert "inf" not in s5_zero.lower() and "nan" not in s5_zero.lower()

    _mk_dossier(code="688768")
    df_neg = pd.DataFrame([{"ann_date": "20260828", "n_income": 1e8,
                            "yoy_net_profit": -5.0e7, "diluted_eps": 0.5}])
    reconcile.reconcile_one("688768", "20260630", "2026-08-29",
                            fetch=_fake_fetch(express_df=df_neg))
    s5_neg = delta.section_body(schema.dossier_path("688768").read_text(encoding="utf-8"), 4)
    assert "增速不适用" in s5_neg
    assert "inf" not in s5_neg.lower() and "nan" not in s5_neg.lower()


def test_reconcile_yoy_missing_base_no_yoy_segment_but_profit_still_renders():
    """yoy_net_profit 字段缺(未披露该字段)→ 不渲染 yoy/去年同期段,净利仍正常渲染。"""
    _mk_dossier(code="688769")
    df = pd.DataFrame([{"ann_date": "20260828", "n_income": 1e8, "diluted_eps": 0.5}])
    res = reconcile.reconcile_one("688769", "20260630", "2026-08-29",
                                  fetch=_fake_fetch(express_df=df))
    assert res["updated"]
    s5 = delta.section_body(schema.dossier_path("688769").read_text(encoding="utf-8"), 4)
    assert "净利 1.0亿" in s5
    assert "yoy" not in s5
    assert "增速不适用" not in s5
