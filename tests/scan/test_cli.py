"""scan CLI —— `run` 双产出(旧 staging + typed trace)+ capture/check 派发。NO network.

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §A(CLI)/§E(parity);Phase E6 §6。

`run` 必须**行为保持**:在 `context/scan/<date>/` 落旧 staging（L3/L4/L5 下游读的那套
CSV + meta.json），同时在 `reports/scan/<run_id>/` 落 typed trace。把取数入口 monkeypatch 成
固定合成 universe（同 test_parity）→ 无网络;`monkeypatch.chdir(tmp_path)` 让两套默认相对路径
都落在 tmp 下，断言文件齐全 + trace 段产物存在。capture/check 仅验证派发到 parity。
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

import autoresearch.research.factor_lab as factor_lab
from autoresearch.data import tushare_source
from autoresearch.scan import cli, universe as screen_market

DATE = "2026-06-20"


def _fixed_universe(n: int = 300) -> pd.DataFrame:
    """Canonical post-gate universe (the columns L1 composite_score reads). Frozen RNG."""
    rng = np.random.default_rng(20260620)
    inds = rng.choice(["半导体", "光模块", "白酒", "煤炭", "医药", "电力"], n)
    df = pd.DataFrame({
        "code": [f"{600000 + i:06d}" for i in range(n)],
        "name": [f"股票{i}" for i in range(n)],
        "industry": inds,
        "close": rng.uniform(5, 300, n),
        "mktcap_yi": rng.uniform(40, 4000, n),
        "amount_yi": rng.uniform(0.5, 200, n),       # >0 → clears recall gate
        "pct_1d": rng.uniform(-10, 10, n),
        "pct_60d": rng.uniform(-50, 300, n),
        "pct_ytd": rng.uniform(-60, 400, n),
        "vol_ratio": rng.uniform(0.3, 5, n),
        "turnover": rng.uniform(0.1, 30, n),
        "pe": rng.uniform(-50, 200, n),
        "pb": rng.uniform(0.5, 30, n),
        "rev": rng.uniform(1e8, 5e10, n),
        "np_": rng.uniform(-1e9, 5e9, n),
        "rev_yoy": rng.uniform(-40, 120, n),
        "np_yoy": rng.uniform(-100, 300, n),
        "np_qoq": rng.uniform(-50, 80, n),
        "roe": rng.uniform(-10, 35, n),
        "gross_margin": rng.uniform(5, 70, n),
        "cfo_ps": rng.uniform(-1, 3, n),
        "np_yoy_prev": rng.uniform(-100, 200, n),
        "rev_yoy_prev": rng.uniform(-40, 100, n),
        "main_inflow_yi": rng.uniform(-5, 8, n),
    })
    df["dv_ratio"] = rng.uniform(0, 6, n)
    df["ma_bull"] = rng.integers(0, 2, n).astype(float)
    df["above_ma60"] = rng.integers(0, 2, n).astype(float)
    df["rsi6"] = rng.uniform(10, 95, n)
    df["rsi12"] = rng.uniform(10, 95, n)
    df["winner_rate"] = rng.uniform(0, 100, n)
    df["cost_50pct"] = rng.uniform(5, 300, n)
    df["main_net_ratio"] = rng.uniform(-0.1, 0.1, n)
    df["retail_net_yi"] = rng.uniform(-2, 2, n)
    df["chip_concentration"] = rng.uniform(0.1, 2.0, n)
    df["price_to_cost"] = rng.uniform(0.7, 1.5, n)
    df["hk_ratio"] = rng.uniform(0, 30, n)
    df["cmf_20"] = rng.uniform(-0.5, 0.5, n)
    df["obv_mom_20"] = rng.uniform(-1, 1, n)
    df["is_st"] = False
    return df


@pytest.fixture
def offline_universe(monkeypatch, tmp_path):
    """Patch 取数入口 → fixed universe;chdir tmp 让默认相对路径(context/, reports/)落 tmp。"""
    uni = _fixed_universe()

    def _fake_fetch(date, cap_floor_yi=30.0, include_bj=False, **kw):
        return uni.copy()

    def _fake_vol(codes, analysis_date, lookback=20):
        return pd.DataFrame(columns=["code"])

    monkeypatch.setattr(tushare_source, "fetch_universe_tushare", _fake_fetch, raising=True)
    monkeypatch.setattr(screen_market, "_harvest_vol_series", _fake_vol, raising=True)
    monkeypatch.setattr(factor_lab, "GBDT_MODEL", "/nonexistent/gbdt_model.pkl", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_run_writes_legacy_staging_and_trace(offline_universe):
    """`run <date>` 双产出:context/scan/<date>/*.csv + meta.json，且 typed trace 段产物存在。"""
    rc = cli.main(["run", DATE, "--recall-n", "1000", "--l2-n", "200", "--source", "tushare"])
    assert rc == 0

    # ① 旧 staging（下游 L3/L4/L5 读这套）—— 与 screen_market.run 同一批文件，逐一齐全。
    staging = offline_universe / "context" / "scan" / DATE
    for fn in ("L1_recall_top1000.csv", "L1_scored_full.csv", "L2_gbdt_top200.csv",
               "sectors.csv", "meta.json"):
        assert (staging / fn).exists(), f"legacy staging 缺 {fn}"
    l2 = pd.read_csv(staging / "L2_gbdt_top200.csv")
    assert len(l2) == 200 and "l2_rank" in l2.columns
    meta = json.loads((staging / "meta.json").read_text(encoding="utf-8"))
    assert meta["analysis_date"] == DATE and meta["l2_n"] == 200

    # ② typed trace —— reports/scan/<run_id>/{manifest.json, stages/*.parquet}。
    runs = list((offline_universe / "reports" / "scan").iterdir())
    assert len(runs) == 1, f"期望恰一个 trace run 目录,得 {runs}"
    run_dir = runs[0]
    assert (run_dir / "manifest.json").exists()
    stages = {p.stem for p in (run_dir / "stages").glob("*.parquet")}
    assert {"L0_universe", "L1_recall", "L2_rank"} <= stages, f"trace 段缺,得 {stages}"


def test_run_exclude_bj_flag_threads_through(offline_universe):
    """`--exclude-bj` 收进 ScanConfig → manifest 的 config.include_bj=False（flag 串到底）。"""
    rc = cli.main(["run", DATE, "--exclude-bj"])
    assert rc == 0
    meta = json.loads(
        (offline_universe / "context" / "scan" / DATE / "meta.json").read_text(encoding="utf-8"))
    assert meta["include_bj"] is False


def test_capture_dispatches_to_parity(monkeypatch, offline_universe):
    """`capture <date> --golden DIR` 调 parity.capture(date, golden, config)。"""
    seen = {}

    def _fake_capture(date, out, *, config=None):
        seen.update(date=date, out=str(out), recall_n=config.recall_n)
        return {"L1_recall": out, "L2_rank": out}

    import autoresearch.scan.parity as parity
    monkeypatch.setattr(parity, "capture", _fake_capture, raising=True)
    golden = offline_universe / "golden_dir"
    rc = cli.main(["capture", DATE, "--golden", str(golden), "--recall-n", "777"])
    assert rc == 0
    assert seen["date"] == DATE and seen["out"] == str(golden) and seen["recall_n"] == 777


def test_check_dispatches_and_maps_ok_to_returncode(monkeypatch, offline_universe):
    """`check <date>` 调 parity.check;res.ok=True→rc0、False→rc1。"""
    import autoresearch.scan.parity as parity
    from autoresearch.scan.parity import ParityResult

    monkeypatch.setattr(parity, "check",
                        lambda date, golden, **kw: ParityResult(ok=True), raising=True)
    assert cli.main(["check", DATE, "--golden", str(offline_universe / "g")]) == 0

    bad = ParityResult(ok=False)
    bad.l2_set_diff = ["600001"]
    monkeypatch.setattr(parity, "check", lambda date, golden, **kw: bad, raising=True)
    assert cli.main(["check", DATE, "--golden", str(offline_universe / "g")]) == 1
