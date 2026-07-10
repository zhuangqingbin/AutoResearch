"""机构面契约:consensus.csv 生产(缓存薄→空表)+ _inst_mark presence-gated。"""
from pathlib import Path

import pandas as pd

from autoresearch.scan.agents.l4_card import _inst_mark, fetch_consensus


def _mk_cache(root: Path, stems: list[str], code="000001", eps=1.0):
    d = root / "report_rc"
    d.mkdir(parents=True)
    for i, s in enumerate(stems):
        pd.to_pickle(pd.DataFrame({"ts_code": [f"{code}.SZ"] * 2, "quarter": ["2026Q4"] * 2,
                                   "eps": [eps + i * 0.1] * 2}), d / f"{s}.pkl")


def test_fetch_consensus_and_mark(tmp_path):
    cache = tmp_path / "cache"
    stems = [f"202606{d:02d}" for d in range(1, 31)]      # 30 天缓存,EPS 逐日上修
    _mk_cache(cache, stems)
    sd = tmp_path / "2026-06-30"
    sd.mkdir()
    df = fetch_consensus(sd, codes=["000001"], cache_root=cache)
    assert (sd / "consensus.csv").exists()
    row = df[df["code"] == "000001"].iloc[0]
    assert row["n_reports"] > 0 and row["eps_delta_pct"] > 0      # 上修为正
    line = _inst_mark(sd, "000001")
    assert "机构面" in line and "修正" in line


def test_inst_mark_presence_gated(tmp_path):
    sd = tmp_path / "2026-06-30"
    sd.mkdir()
    assert _inst_mark(sd, "000001") == ""                  # 无 consensus.csv → 不加行


def test_fetch_consensus_thin_cache_empty(tmp_path):
    cache = tmp_path / "cache"
    _mk_cache(cache, ["20260628", "20260629"])             # <10 天 → 空表(禁注)
    sd = tmp_path / "2026-06-30"
    sd.mkdir()
    df = fetch_consensus(sd, codes=["000001"], cache_root=cache)
    assert df.empty and not (sd / "consensus.csv").exists()


# ───────────────────────── 机构面第二行:基金重仓(条件分支,presence-gated,季度滞后) ─────────────────────────
# Plan 1 Task 6 探针裁决=可用(context/factor_lab/cache/probes/fund_portfolio_20260710.json):
# fund_portfolio 不支持按个股直查 → period(季度)批量拉 + 本地按 symbol 过滤反查。
# 端点在测试环境不可静态化(需真 tushare token/网络)→ 做成独立小函数(fetch_fund_hold)
# + 独立测试(本节),经 fetch_fn 注入 fixture,不打真网;不拖累上面卖方修正主线。


def test_fetch_fund_hold_aggregates_and_delta(tmp_path):
    from autoresearch.scan.agents.l4_card import fetch_fund_hold
    sd = tmp_path / "2026-06-30"
    sd.mkdir()

    def _fn(period):
        if period == "20260331":       # latest_reported_quarter("2026-06-30")
            return pd.DataFrame([
                {"ts_code": "005827.OF", "symbol": "000001.SZ", "mkv": 1.0e8},
                {"ts_code": "005828.OF", "symbol": "000001.SZ", "mkv": 2.0e8},
                {"ts_code": "005829.OF", "symbol": "999999.SZ", "mkv": 5.0e8},   # 非目标票
            ])
        if period == "20251231":       # prev_quarter("20260331")
            return pd.DataFrame([{"ts_code": "005827.OF", "symbol": "000001.SZ", "mkv": 1.0e8}])
        raise AssertionError(period)

    out = fetch_fund_hold(sd, codes=["000001"], fetch_fn=_fn)
    assert (sd / "fund_hold.csv").exists()
    row = out.set_index("code").loc["000001"]
    assert row["n_funds"] == 2                  # 两只基金持有(005827/005828)
    assert row["n_funds_delta"] == 1            # 本季 2 家 - 上季 1 家
    assert row["mkv_yi"] == 3.0                 # (1e8+2e8)/1e8


def test_fetch_fund_hold_no_prev_data_delta_is_nan(tmp_path):
    from autoresearch.scan.agents.l4_card import fetch_fund_hold
    sd = tmp_path / "2026-06-30"
    sd.mkdir()

    def _fn(period):
        if period == "20260331":
            return pd.DataFrame([{"ts_code": "005827.OF", "symbol": "000001.SZ", "mkv": 1.0e8}])
        return None                              # 上季数据缺(权限/未覆盖)

    out = fetch_fund_hold(sd, codes=["000001"], fetch_fn=_fn)
    row = out.set_index("code").loc["000001"]
    assert pd.isna(row["n_funds_delta"])


def test_fetch_fund_hold_no_coverage_empty(tmp_path):
    from autoresearch.scan.agents.l4_card import fetch_fund_hold
    sd = tmp_path / "2026-06-30"
    sd.mkdir()

    def _fn(period):
        return pd.DataFrame([{"ts_code": "005827.OF", "symbol": "999999.SZ", "mkv": 1.0e8}])

    out = fetch_fund_hold(sd, codes=["000001"], fetch_fn=_fn)
    assert out.empty and not (sd / "fund_hold.csv").exists()


def test_fetch_fund_hold_endpoint_failure_degrades(tmp_path):
    from autoresearch.scan.agents.l4_card import fetch_fund_hold
    sd = tmp_path / "2026-06-30"
    sd.mkdir()

    def _boom(period):
        raise RuntimeError("无权限")

    out = fetch_fund_hold(sd, codes=["000001"], fetch_fn=_boom)
    assert out.empty and not (sd / "fund_hold.csv").exists()


def test_fetch_fund_hold_defaults_codes_from_finalists(tmp_path):
    from autoresearch.scan.agents.l4_card import fetch_fund_hold
    sd = tmp_path / "2026-06-30"
    sd.mkdir()
    pd.DataFrame({"code": ["000001"]}).to_csv(sd / "finalists.csv", index=False)

    def _fn(period):
        return pd.DataFrame([{"ts_code": "005827.OF", "symbol": "000001.SZ", "mkv": 1.0e8}])

    out = fetch_fund_hold(sd, fetch_fn=_fn)
    assert list(out["code"]) == ["000001"]


def test_fund_mark_presence_gated_and_quarterly_lag_wording(tmp_path):
    from autoresearch.scan.agents.l4_card import _fund_mark
    assert _fund_mark(tmp_path, "000001") == ""        # 无 fund_hold.csv → 空
    pd.DataFrame([{"code": "000001", "n_funds": 5, "mkv_yi": 3.2, "n_funds_delta": 2}]).to_csv(
        tmp_path / "fund_hold.csv", index=False)
    line = _fund_mark(tmp_path, "000001")
    assert "机构面" in line and "季度滞后" in line and "5" in line
