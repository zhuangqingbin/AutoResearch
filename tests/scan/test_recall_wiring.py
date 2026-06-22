"""Phase 2 接线:universe.run multi 产 provenance + L1_channels;composite 走旧路径;
L1Recall stage multi 写 3 产物。NO network(patch universe 取数 + vol_series)。"""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.data import tushare_source
from autoresearch.scan import universe as smu
from autoresearch.scan.config import ScanConfig
from autoresearch.scan.context import RunContext
from autoresearch.scan.pipeline import Pipeline
from autoresearch.trace import schema
from autoresearch.trace.store import TraceStore
from tests.scan._synth_universe import synth_universe

DATE = "2026-06-20"


@pytest.fixture
def patched(monkeypatch):
    uni = synth_universe(n=600, seed=7)
    monkeypatch.setattr(tushare_source, "fetch_universe_tushare",
                        lambda *a, **k: uni.copy(), raising=True)
    monkeypatch.setattr(smu, "_harvest_vol_series",
                        lambda codes, d, lookback=20: pd.DataFrame(columns=["code"]), raising=True)
    import autoresearch.research.factor_lab as fl
    monkeypatch.setattr(fl, "GBDT_MODEL", "/nonexistent/x.pkl", raising=False)
    return uni


def test_universe_run_multi_writes_provenance_and_channels(patched, tmp_path):
    out = tmp_path / "scan"
    smu.run(DATE, recall_n=300, l2_n=100, outdir=out, recall_mode="multi")
    l1 = pd.read_csv(out / "L1_recall_top1000.csv", dtype={"code": str})
    assert "recall_channels" in l1.columns and "n_channels" in l1.columns
    assert len(l1) == 300
    assert (out / "L1_channels.csv").exists()


def test_universe_run_composite_mode_no_provenance(patched, tmp_path):
    out = tmp_path / "scan_c"
    smu.run(DATE, recall_n=300, l2_n=100, outdir=out, recall_mode="composite")
    l1 = pd.read_csv(out / "L1_recall_top1000.csv", dtype={"code": str})
    assert "recall_channels" not in l1.columns        # 旧路径不带 provenance
    assert l1["composite"].is_monotonic_decreasing    # 纯 composite 降序


def test_l1recall_stage_multi_writes_channels(patched, tmp_path):
    store = TraceStore(tmp_path / "trace")
    ctx = RunContext(analysis_date=DATE, trace=store,
                     config=ScanConfig(recall_n=300, l2_n=100, recall_mode="multi"))
    Pipeline().run(ctx)
    l1 = store.get_df(ctx.run_id, schema.L1_RECALL)
    assert "n_channels" in l1.columns and len(l1) == 300
    chans = store.get_df(ctx.run_id, schema.L1_CHANNELS)
    assert set(chans["channel"].unique()) and "code" in chans.columns
