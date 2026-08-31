"""PIT 锚定 curr_date(D1.4):8 处回填历史日会静默取到未来数据的取数点。

每处一条纯函数级用例:合成/捕获参数证明「回填历史日不再取未来」;「今天」跑的行为
不受影响(过滤条件在 curr_date=今天 时天然不剔除任何真实历史行)。
"""
import pandas as pd
import pytest

from autoresearch.analyze import harvest
from autoresearch.common import uzi_lenses
from autoresearch.data import tushare_enrich, tushare_source


# ── 1. harvest.py ~1403 `^VIX` period="5d" → 锚 curr_date ──────────────────────

@pytest.mark.unit
def test_vix_latest_anchors_to_curr_date(monkeypatch):
    captured = {}
    idx = pd.to_datetime(["2026-08-25", "2026-08-27"])
    closes = pd.DataFrame({"Close": [15.0, 16.0]}, index=idx)

    class FakeTicker:
        def history(self, start=None, end=None):
            captured["start"], captured["end"] = start, end
            return closes

    monkeypatch.setattr(harvest.yf, "Ticker", lambda *a, **k: FakeTicker())
    v = harvest._vix_latest("2026-08-28")
    assert captured["start"] == "2026-08-21"    # curr_date - 7 天
    assert captured["end"] == "2026-08-29"      # curr_date + 1 天(与 _hist_returns 同一惯例)
    assert v == 16.0


# ── 2. uzi_lenses.py ~301 margin_trend_ts end=datetime.now() → 锚 curr_date ────

@pytest.mark.unit
def test_margin_trend_ts_anchors_end_date_to_curr_date(monkeypatch):
    captured = {}
    rows = pd.DataFrame({"trade_date": ["20260601", "20260615"],
                        "rzye": [1.0e8, 1.1e8], "rzrqye": [0.0, 0.0]})

    class FakePro:
        def margin_detail(self, ts_code, start_date, end_date, fields):
            captured["start_date"], captured["end_date"] = start_date, end_date
            return rows.copy()

    monkeypatch.setattr(tushare_source, "_pro", lambda: FakePro())
    out = uzi_lenses.margin_trend_ts("300308.SZ", curr_date="2026-06-20")
    assert captured["end_date"] == "20260620"
    assert out is not None and "1.10亿" in out


# ── 3. uzi_lenses.py ~273 fina_indicator iloc[-1] → 先按 end_date<=curr 过滤 ───

@pytest.mark.unit
def test_ashare_fundamentals_ts_respects_curr_date(monkeypatch):
    # 三期都是年报(1231)——2026 那期若不过滤会同时渗入「5年 ROE」趋势与「最新」行。
    fi = pd.DataFrame({"ts_code": ["300308.SZ"] * 3,
                       "end_date": ["20241231", "20251231", "20991231"],
                       "roe": [10.0, 11.0, 99.0],
                       "netprofit_margin": [5.0, 5.5, 77.0],
                       "grossprofit_margin": [20.0, 20.0, 20.0],
                       "debt_to_assets": [30.0, 30.0, 30.0],
                       "or_yoy": [1.0, 1.0, 1.0], "netprofit_yoy": [1.0, 1.0, 1.0]})

    class FakePro:
        def fina_indicator(self, ts_code, fields):
            return fi.copy()

        def dividend(self, ts_code, fields):
            return pd.DataFrame()

    monkeypatch.setattr(tushare_source, "_pro", lambda: FakePro())
    out = uzi_lenses.ashare_fundamentals_ts("300308.SZ", curr_date="2026-08-30")
    assert out is not None
    assert "99.0" not in out and "77.0" not in out    # 未来那期(20991231)不得渗入趋势或「最新」
    assert "2025:11.0%" in out                        # 20251231 才是 curr_date 之前最新一期


# ── 4. tushare_enrich.py ~135 stk_holdernumber 最新行 → 先按 ann_date<=curr 过滤 ─

@pytest.mark.unit
def test_ashare_shareholder_ts_holdernumber_respects_curr_date(monkeypatch):
    hn = pd.DataFrame({"end_date": ["20250630", "20251231", "20990101"],
                       "ann_date": ["20250715", "20260115", "20990110"],
                       "holder_num": [50000, 45000, 1]})

    class FakePro:
        def stk_holdernumber(self, ts_code):
            return hn.copy()

        def pledge_stat(self, ts_code):
            return pd.DataFrame()

    # tushare_enrich.py 在**模块顶层** `from ... import _pro`(非函数内懒导入)——
    # 补丁必须打在 tushare_enrich 自己的命名空间,打 tushare_source 不生效(真打过一次
    # 真网络:this is exactly why —— 见 batch-A-report.md 记录)。
    monkeypatch.setattr(tushare_enrich, "_pro", lambda: FakePro())
    out = tushare_enrich.ashare_shareholder_ts("300308.SZ", curr_date="2026-08-30")
    assert out is not None
    assert "20990101" not in out and "20990110" not in out
    assert "45,000" in out


# ── 5. tushare_enrich.py ~148 pledge_stat 最新行 → 先按 end_date<=curr 过滤 ─────

@pytest.mark.unit
def test_ashare_shareholder_ts_pledge_respects_curr_date(monkeypatch):
    pl = pd.DataFrame({"end_date": ["20250630", "20990101"], "pledge_ratio": [12.5, 88.8]})

    class FakePro:
        def stk_holdernumber(self, ts_code):
            return pd.DataFrame()

        def pledge_stat(self, ts_code):
            return pl.copy()

    monkeypatch.setattr(tushare_enrich, "_pro", lambda: FakePro())
    out = tushare_enrich.ashare_shareholder_ts("300308.SZ", curr_date="2026-08-30")
    assert out is not None
    assert "88.8" not in out
    assert "12.5" in out


# ── 6a. tushare_enrich.py ~170 forecast 取最新 ann_date → 先按 ann_date<=curr 过滤

@pytest.mark.unit
def test_ashare_calendar_ts_forecast_respects_curr_date(monkeypatch):
    fc = pd.DataFrame({"ann_date": ["20250715", "20990101"],
                       "end_date": ["20250630", "20991231"],
                       "type": ["预增", "预增"],
                       "p_change_min": [10.0, 900.0], "p_change_max": [20.0, 950.0],
                       "change_reason": ["需求旺盛", "未来事件"]})

    class FakePro:
        def forecast(self, ts_code):
            return fc.copy()

        def express(self, ts_code):
            return pd.DataFrame()

    monkeypatch.setattr(tushare_enrich, "_pro", lambda: FakePro())
    out = tushare_enrich.ashare_calendar_ts("300308.SZ", "2026-08-30")
    assert out is not None
    assert "900" not in out and "950" not in out
    assert "+10%~+20%" in out


# ── 6b. express 同款(brief:"补同过滤")────────────────────────────────────────

@pytest.mark.unit
def test_ashare_calendar_ts_express_respects_curr_date(monkeypatch):
    ex = pd.DataFrame({"ann_date": ["20250715", "20990101"],
                       "end_date": ["20250630", "20991231"],
                       "n_income": [1.0e8, 50.0e8],
                       "yoy_net_profit": [0.8e8, 1.0e8],
                       "diluted_roe": [8.0, 50.0]})

    class FakePro:
        def forecast(self, ts_code):
            return pd.DataFrame()

        def express(self, ts_code):
            return ex.copy()

    monkeypatch.setattr(tushare_enrich, "_pro", lambda: FakePro())
    out = tushare_enrich.ashare_calendar_ts("300308.SZ", "2026-08-30")
    assert out is not None
    assert "20991231" not in out
    assert "20250630" in out


# ── 7. harvest.py earnings_quality_metrics 三张表 → filter_financials_by_date ──

@pytest.mark.unit
def test_earnings_quality_respects_curr_date(monkeypatch):
    idx = ["Net Income"]
    future_col, past_col = pd.Timestamp("2026-12-31"), pd.Timestamp("2025-09-30")
    inc = pd.DataFrame({future_col: [999.0], past_col: [42.0]}, index=idx)
    empty = pd.DataFrame()

    class FakeTicker:
        quarterly_income_stmt = inc
        quarterly_cashflow = empty
        quarterly_balance_sheet = empty

    monkeypatch.setattr(harvest.yf, "Ticker", lambda *a, **k: FakeTicker())
    out = harvest.earnings_quality_metrics("AAPL", curr_date="2026-01-01")
    assert "999" not in out
    assert "42" in out


# ── 8. harvest.py solvency_block 同病 → filter_financials_by_date ──────────────

@pytest.mark.unit
def test_solvency_respects_curr_date(monkeypatch):
    idx = ["Total Debt"]
    future_col, past_col = pd.Timestamp("2026-12-31"), pd.Timestamp("2025-09-30")
    bs = pd.DataFrame({future_col: [8888.0], past_col: [123.0]}, index=idx)
    empty = pd.DataFrame()

    class FakeTicker:
        quarterly_balance_sheet = bs
        quarterly_income_stmt = empty

    monkeypatch.setattr(harvest.yf, "Ticker", lambda *a, **k: FakeTicker())
    out = harvest.solvency_block("AAPL", curr_date="2026-01-01")
    assert "8888" not in out
    assert "123" in out


# ── 附:.info 实时类不改取数,只诚实标注「不可回放」(#19/#30 等)────────────────

@pytest.mark.unit
def test_realtime_info_blocks_carry_as_of_disclaimer(monkeypatch):
    class FakeTicker:
        info = {"shortPercentOfFloat": 0.05, "heldPercentInstitutions": 0.3}
        institutional_holders = None
        major_holders = None

    monkeypatch.setattr(harvest.yf, "Ticker", lambda *a, **k: FakeTicker())
    out = harvest.ownership_short("AAPL")
    assert out.strip().startswith("_as-of: 运行时刻")

    class FakeTicker2:
        info = {"currentPrice": 123.4}

    monkeypatch.setattr(harvest.yf, "Ticker", lambda *a, **k: FakeTicker2())
    out2 = harvest.analyst_consensus("AAPL")
    assert out2.strip().startswith("_as-of: 运行时刻")
