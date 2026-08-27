"""Phase 0 —— build_market_frame / market_pack_from_frame / sentinel_advice_from_frame。NO network。

design: docs/specs/2026-07-03-research-skills-altitude-refactor-design.md §5.1 / §7 Phase 0。
锚:帧入口与 scan-dir 入口**同谓词同口径**(帧 = L1_scored_full 的前身)——宏观 lite(Stage 0)
与盘前哨兵预告吃的读数,必须和 scan 内正式读数一致;缺列降级不抛。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from autoresearch.common import workspace as ws
from autoresearch.scan import frame as scan_frame
from autoresearch.scan.market import market_pack, market_pack_from_frame
from autoresearch.scan.menu import sentinel_advice, sentinel_advice_from_frame
from autoresearch.scan.run_contract import load_run_contract
from autoresearch.trace.capsule import begin_run
from tests.scan._synth_universe import synth_universe

DATE = "2026-07-03"


# ───────────────────────── build_market_frame ─────────────────────────


def test_build_market_frame_gate_zfill_counts(monkeypatch):
    uni = synth_universe(n=50, seed=3)
    uni.loc[0, "amount_yi"] = 0.0          # 无流动性 → 轻门剔
    uni.loc[1, "close"] = float("nan")     # 无价 → 轻门剔
    uni.loc[2, "code"] = "1"               # 帧内 code 需 zfill
    monkeypatch.setattr("autoresearch.data.tushare_source.fetch_universe_tushare",
                        lambda *a, **k: uni.copy(), raising=True)
    monkeypatch.setattr("autoresearch.scan.frame._harvest_vol_series",
                        lambda codes, d, lookback=20: pd.DataFrame(columns=["code"]), raising=True)
    frame, counts = scan_frame.build_market_frame(DATE)
    assert counts["universe"] == 50 and counts["after_gate_a"] == 48
    assert "universe_raw" in counts
    assert len(frame) == 48
    assert (frame["code"].str.len() == 6).all()


def test_build_market_frame_no_vol_series(monkeypatch):
    """vol_series=False → 不碰 _harvest_vol_series(盘前省时;healthy 缺 cmf 自会降级)。"""
    uni = synth_universe(n=20, seed=4).drop(columns=["cmf_20", "obv_mom_20"])
    monkeypatch.setattr("autoresearch.data.tushare_source.fetch_universe_tushare",
                        lambda *a, **k: uni.copy(), raising=True)

    def _boom(*a, **k):
        raise AssertionError("vol_series=False 不应取多日量价")

    monkeypatch.setattr("autoresearch.scan.frame._harvest_vol_series", _boom, raising=True)
    frame, _ = scan_frame.build_market_frame(DATE, vol_series=False)
    assert "cmf_20" not in frame.columns


# ───────────────────────── market_pack:帧入口 ≡ scan-dir 入口 ─────────────────────────


def test_market_pack_from_frame_matches_scandir(tmp_path):
    """同一份数据:帧入口四段(regime/breadth/valuation/money)≡ L1_scored_full 入口。"""
    df = synth_universe(n=300, seed=11)
    df.to_csv(tmp_path / "L1_scored_full.csv", index=False)
    via_dir = market_pack(tmp_path)
    via_frame = market_pack_from_frame(df)
    for key in ("regime", "breadth", "valuation", "money"):
        assert via_frame[key] == via_dir[key], key
    # sectors:scan-dir 无 sectors.csv → None;帧入口由 groupby 生成(打分列缺省 None,描述性可缺)
    assert via_dir["sectors"] is None
    secs = via_frame["sectors"]
    assert secs and secs["red"] and secs["black"]
    assert secs["red"][0]["median_composite"] is None
    assert secs["red"][0]["n_recall"] >= 1


def test_market_pack_from_frame_empty():
    empty = {"regime": None, "breadth": None, "valuation": None, "money": None, "sectors": None}
    assert market_pack_from_frame(pd.DataFrame()) == empty
    assert market_pack_from_frame(None) == empty


# ───────────────────────── sentinel:帧入口 ≡ scan-dir 入口 ─────────────────────────


def _write_scandir(tmp_path, df, regime=None):
    df.to_csv(tmp_path / "L1_scored_full.csv", index=False)
    if regime is not None:
        (tmp_path / "meta.json").write_text(json.dumps({"regime": regime}), encoding="utf-8")


def test_sentinel_frame_sentinel_when_no_healthy(tmp_path):
    """全市场健康=0 → 两入口同判 sentinel(材料枯竭)。"""
    df = synth_universe(n=200, seed=5)
    df["pct_60d"] = -30.0                                 # 无一健康上涨
    _write_scandir(tmp_path, df)
    assert sentinel_advice(tmp_path)[0] == "sentinel"
    assert sentinel_advice_from_frame(df)[0] == "sentinel"


def test_sentinel_frame_full_when_rich(tmp_path):
    df = synth_universe(n=200, seed=6)
    df["pct_60d"], df["main_net_ratio"], df["cmf_20"] = 20.0, 0.05, 0.3   # 全员健康
    _write_scandir(tmp_path, df)
    assert sentinel_advice(tmp_path)[0] == "full"
    assert sentinel_advice_from_frame(df)[0] == "full"


def test_sentinel_frame_midband_risk_off(tmp_path):
    """中带(3–5%)+ risk_off → consider(2026-07-08 放宽:删掉 risk_off 升级档,不再 auto-skip);
    帧入口 regime 现算 ≡ scan-dir 读 meta 的同一标签(两入口同判)。"""
    df = synth_universe(n=200, seed=7)
    df["pct_60d"], df["main_net_ratio"], df["cmf_20"] = -30.0, -0.05, -0.1
    df["above_ma60"] = 0.0                                # breadth=0 + med_mom<0 → risk_off
    healthy = df.index[:8]                                # 8/200 = 4% ∈ (3%,5%)
    df.loc[healthy, ["pct_60d", "main_net_ratio", "cmf_20"]] = [20.0, 0.05, 0.3]
    _write_scandir(tmp_path, df, regime="risk_off")
    assert sentinel_advice(tmp_path)[0] == "consider"
    assert sentinel_advice_from_frame(df)[0] == "consider"


def test_sentinel_frame_missing_col_degrades():
    df = synth_universe(n=50, seed=8).drop(columns=["cmf_20"])
    level, reason = sentinel_advice_from_frame(df)
    assert level == "full" and "降级" in reason


# ───────────────────────── CLI 盘前预告 ─────────────────────────


def test_frame_cli_smoke(monkeypatch, capsys, tmp_path):
    df = synth_universe(n=100, seed=9)
    monkeypatch.setattr(scan_frame, "build_market_frame",
                        lambda d, **kw: (df, {"universe_raw": 100, "universe": 100,
                                              "after_gate_a": 100}))
    monkeypatch.setattr("autoresearch.macro.state.load_macro_state",   # 隔离真实 context/macro
                        lambda today, regime_today=None, path=None:
                        (None, "无 macro_state.json → 只用日频 pack"), raising=True)
    monkeypatch.chdir(tmp_path)   # 隔离真实 context/scan/<date>(Plan A3 T1 起 --json 落 echo 文件)
    rc = scan_frame.main([DATE, "--json"])
    captured = capsys.readouterr()
    out, err = captured.out, captured.err
    assert rc == 0
    # 信息行走 stderr(2026-07-11 修:--json 时 stdout 须是纯 JSON,不再混日志——见 test_frame_json_clean.py)
    assert "[sentinel·盘前预告]" in err
    assert "[macro_state]" in err         # Phase 2:宏观视图新鲜度一行
    assert "[sentinel·盘前预告]" not in out
    assert "[macro_state]" not in out
    assert '"breadth"' in out            # --json 打印 market_pack(宏观 lite 输入)
    assert '"macro_state_note"' in out   # 捆绑进 JSON,缺文件 → null + note(presence-gated)
    assert '"user_config"' in out        # Plan A3 T1:用户配置层回显(缺文件 → {})
    contract = json.loads(
        (tmp_path / ws.scan_root() / DATE / "run_contract.json").read_text(
            encoding="utf-8"
        )
    )
    assert contract["stage_budgets"]["cache_hit_min"] == 0.85
    assert contract["stage_budgets"]["min_real_scans"] == 10
    assert contract["stage_budgets"]["concurrency"]["tushare"] == 4


def _patch_active_frame(monkeypatch, df):
    calls = []

    def _build(d, **kwargs):
        calls.append((d, kwargs))
        return df, {"universe_raw": len(df), "universe": len(df), "after_gate_a": len(df)}

    monkeypatch.setattr(scan_frame, "build_market_frame", _build)
    monkeypatch.setattr(
        "autoresearch.data.macro_cn.write_macro_cn", lambda d: Path("macro.json")
    )
    monkeypatch.setattr(
        "autoresearch.macro.state.load_macro_state",
        lambda today, regime_today=None, path=None: (None, "none"),
    )
    return calls


def test_frame_reuses_bootstrap_contract_instead_of_minting_second_identity(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(ws, "ENGINE", "codex")
    monkeypatch.setattr(ws, "context_root", lambda: tmp_path / "context_codex")
    monkeypatch.setattr(
        "autoresearch.scan.user_config.DEFAULT_PINNED_PATH",
        tmp_path / "missing-pinned.jsonc",
    )
    handle = begin_run(
        "scan-market",
        "2026-08-27",
        "codex",
        {},
        now=datetime(2026, 8, 27, 1, 2, 3, 456789, tzinfo=timezone.utc),
    )
    contract_before = (handle.staging / "run_contract.json").read_bytes()
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", handle.run_id)
    df = synth_universe(n=30, seed=99)
    _patch_active_frame(monkeypatch, df)

    assert scan_frame.main(["2026-08-27", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    loaded = load_run_contract(handle.staging / "run_contract.json")
    assert loaded.run_id == handle.run_id
    assert loaded.contract_hash == handle.contract.contract_hash
    assert payload["run_contract"] == handle.contract.short_ref()
    assert (handle.staging / "run_contract.json").read_bytes() == contract_before


def test_frame_refuses_active_run_without_contract_before_any_fetch(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(ws, "ENGINE", "codex")
    monkeypatch.setattr(ws, "context_root", lambda: tmp_path / "context_codex")
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", "20260827T010203456789Z")
    monkeypatch.setattr(
        scan_frame,
        "build_market_frame",
        lambda *a, **k: pytest.fail("market fetch happened before contract validation"),
    )
    with pytest.raises(RuntimeError, match="RunContract v3"):
        scan_frame.main(["2026-08-27", "--json"])


def test_frame_active_run_uses_frozen_effective_data_policy(
    tmp_path, monkeypatch, capsys
):
    from autoresearch.scan import user_config as uc

    monkeypatch.setattr(ws, "ENGINE", "codex")
    monkeypatch.setattr(ws, "context_root", lambda: tmp_path / "context_codex")
    monkeypatch.setattr(uc, "DEFAULT_PINNED_PATH", tmp_path / "missing-pinned.jsonc")
    config = tmp_path / "scan_config.jsonc"
    config.write_text(
        json.dumps({"l0": {"source": "em", "cap_floor_yi": 42, "include_bj": False}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(uc, "DEFAULT_PATH", config)
    handle = begin_run("scan-market", "2026-08-27", "codex", config)
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", handle.run_id)
    calls = _patch_active_frame(monkeypatch, synth_universe(n=30, seed=100))

    assert scan_frame.main(["2026-08-27", "--json"]) == 0
    capsys.readouterr()
    assert calls == [
        (
            "2026-08-27",
            {"cap_floor_yi": 42.0, "include_bj": False, "source": "em"},
        )
    ]


def test_frame_active_run_uses_custom_begin_config_without_reloading_default(
    tmp_path, monkeypatch, capsys
):
    from autoresearch.scan import user_config as uc

    monkeypatch.setattr(ws, "ENGINE", "codex")
    monkeypatch.setattr(ws, "context_root", lambda: tmp_path / "context_codex")
    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path / "reports_codex")
    monkeypatch.setattr(uc, "DEFAULT_PINNED_PATH", tmp_path / "missing-pinned.jsonc")
    custom = tmp_path / "custom-scan-config.jsonc"
    custom.write_text(
        json.dumps({"l0": {"source": "em", "cap_floor_yi": 55, "include_bj": False}}),
        encoding="utf-8",
    )
    # DEFAULT_PATH deliberately remains the isolated missing config from conftest.
    handle = begin_run("scan-market", "2026-08-27", "codex", custom)
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", handle.run_id)
    calls = _patch_active_frame(monkeypatch, synth_universe(n=30, seed=101))
    assert scan_frame.main(["2026-08-27", "--json"]) == 0
    capsys.readouterr()
    assert calls[0][1] == {
        "cap_floor_yi": 55.0,
        "include_bj": False,
        "source": "em",
    }


# ───────────────────────── _harvest_vol_series:60 日面板(P1 低位转强波) ─────────────────────────


def _fake_daily_world(monkeypatch, n_days: int, n_codes: int = 8, seed: int = 0):
    """伪造 tushare 日历 + 湖:`get_or_fetch('daily', {'trade_date': d})` 返回当日全市场日线。"""
    from datetime import date as _date, timedelta as _td

    import numpy as np

    rng = np.random.default_rng(seed)
    # 真实连续日历日(生产 `_harvest_vol_series` 会对 `last` 做 strptime 反推 start 窗口,
    # 假日期串如 20260332 会炸进 A 级契约异常 —— 夹具必须给合法日期)
    d0 = _date(2026, 3, 1)
    days = [(d0 + _td(days=i)).strftime("%Y%m%d") for i in range(n_days)]
    codes = [f"{600000 + i:06d}" for i in range(n_codes)]
    world = {}
    for d in days:
        close = rng.uniform(5, 50, n_codes)
        world[d] = pd.DataFrame({"ts_code": [f"{c}.SH" for c in codes], "trade_date": d,
                                 "high": close * 1.02, "low": close * 0.98, "close": close,
                                 "amount": rng.uniform(1e4, 1e6, n_codes)})
    monkeypatch.setattr("autoresearch.data.tushare_source._pro", lambda: object(), raising=True)
    monkeypatch.setattr("autoresearch.data.tushare_source.resolve_momentum_dates",
                        lambda pro, d: (days[-1], days[0], days[0]), raising=True)
    monkeypatch.setattr("autoresearch.data.tushare_source._trade_days",
                        lambda pro, start, last: days, raising=True)
    monkeypatch.setattr("autoresearch.data.cache.get_or_fetch",
                        lambda endpoint, params, today=None: world[params["trade_date"]].copy(),
                        raising=True)
    return codes


def test_harvest_vol_series_lookback60_keeps_20d_factors_identical(monkeypatch):
    """lookback 20→60 后,cmf_20/obv_mom_20/price_vs_vwap_20/breakout_vol_20 逐元素不变(byte 契约),
    且追加 turnup.PANEL_COLS 十列。"""
    from autoresearch.common import turnup
    from autoresearch.data import contracts

    codes = _fake_daily_world(monkeypatch, n_days=70)
    contracts.clear_degradations()
    a = scan_frame._harvest_vol_series(codes, "2026-05-20", lookback=60).set_index("code")
    b = scan_frame._harvest_vol_series(codes, "2026-05-20", lookback=20).set_index("code")
    for col in ("cmf_20", "obv_mom_20", "price_vs_vwap_20", "breakout_vol_20"):
        pd.testing.assert_series_equal(a[col], b[col], check_names=False)
    assert set(turnup.PANEL_COLS) <= set(a.columns)
    assert a["vol_ratio_20"].notna().all() and a["above_ma20"].isin([0.0, 1.0]).all()
    assert scan_frame._PANEL_LOOKBACK == 60 and scan_frame._harvest_vol_series.__defaults__[0] == 60


def test_harvest_vol_series_short_panel_degrades_not_raises(monkeypatch):
    """面板 <_TURNUP_MIN_DAYS:十列整列 NaN + B 级降级记账(key=turnup_panel),不抛、四个 20 日列照算。"""
    from autoresearch.common import turnup
    from autoresearch.data import contracts

    codes = _fake_daily_world(monkeypatch, n_days=25)
    contracts.clear_degradations()
    out = scan_frame._harvest_vol_series(codes, "2026-05-20", lookback=60)
    assert out["cmf_20"].notna().any()
    assert out[list(turnup.PANEL_COLS)].isna().all().all()
    keys = [d.get("key") for d in contracts.degradations()]
    assert "turnup_panel" in keys
