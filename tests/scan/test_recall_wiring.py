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
    monkeypatch.setattr("autoresearch.scan.frame._harvest_vol_series",
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


def test_universe_run_wires_pinned_into_l1(patched, tmp_path):
    """FN-1 回归:run() 必须 load_pinned 并强注 L1——保送票(召回会漏)不得空降 L4。"""
    import json
    out = tmp_path / "scan"
    pj = tmp_path / "pinned.json"
    pj.write_text(json.dumps([{"code": "600123", "note": "手工保送测试"}]), encoding="utf-8")
    smu.run(DATE, recall_n=300, l2_n=100, outdir=out, recall_mode="multi", pinned_path=pj)
    l1 = pd.read_csv(out / "L1_recall_top1000.csv", dtype={"code": str})
    row = l1[l1["code"] == "600123"]
    # FN-1 核心契约:保送票被强注进 L1(标记 recall_channels="pinned"),不再在 finalists 层空降 L4
    assert len(row) == 1 and row.iloc[0]["recall_channels"] == "pinned", "保送票未强注进 L1(FN-1)"
    # pinned 布尔列持久化进 artifact(复盘可见哪些是保送票)
    assert "pinned" in l1.columns and bool(row.iloc[0]["pinned"]), "L1 artifact 缺 pinned 列"
    # 且随 L1 流入 L2(被 L3 真判的前提;若缺则仍是空降)
    l2 = pd.read_csv(out / "L2_gbdt_top200.csv", dtype={"code": str})
    assert "600123" in set(l2["code"]), "保送票未随 L1 流入 L2(仍会空降 L4)"


def test_universe_run_no_pinned_file_parity(patched, tmp_path):
    """无 pinned.json(缺文件)→ 召回不受影响(parity):数量不变、不因保送多出行。"""
    out = tmp_path / "scan"
    smu.run(DATE, recall_n=300, l2_n=100, outdir=out, recall_mode="multi",
            pinned_path=tmp_path / "nope.json")
    l1 = pd.read_csv(out / "L1_recall_top1000.csv", dtype={"code": str})
    assert len(l1) == 300      # 缺 pinned.json → load_pinned kept=[] → 无强注,召回数量不变
    assert "pinned" not in l1.columns   # presence-gated:无保送→不加 pinned 列(parity)


def test_l1recall_stage_multi_writes_channels(patched, tmp_path):
    store = TraceStore(tmp_path / "trace")
    ctx = RunContext(analysis_date=DATE, trace=store,
                     config=ScanConfig(recall_n=300, l2_n=100, recall_mode="multi"))
    Pipeline().run(ctx)
    l1 = store.get_df(ctx.run_id, schema.L1_RECALL)
    assert "n_channels" in l1.columns and len(l1) == 300
    chans = store.get_df(ctx.run_id, schema.L1_CHANNELS)
    assert set(chans["channel"].unique()) and "code" in chans.columns


# ───────────────── 数据降级落盘(接线验证:FN-1 教训"接了 ≠ 生效") ─────────────────


def test_universe_run_writes_degraded_json_when_tier_b_degrades(patched, tmp_path):
    """B 级降级 → `degraded.json` 落进当日 staging(assemble 据此在报告里显示一行)。

    走**真的 `universe.run`**,不是单独测那三行落盘代码 —— FN-1 三连的教训:
    "接了参数/写了生产者 ≠ 它真的在生产路径上被调用"。
    """
    import json

    from autoresearch.data import contracts

    contracts.clear_degradations()
    contracts.record_degradation("hk_hold", "空返回(该日无北向数据)→ north 组置空", key="20260709")
    try:
        out = tmp_path / "scan_deg"
        smu.run(DATE, recall_n=300, l2_n=100, outdir=out, recall_mode="multi")
        recs = json.loads((out / "degraded.json").read_text(encoding="utf-8"))
        assert recs[0]["endpoint"] == "hk_hold"
    finally:
        contracts.clear_degradations()


def test_universe_run_no_degraded_json_when_all_data_clean(patched, tmp_path):
    """presence-gated:无降级 → 不落文件(不给报告加噪音)。"""
    from autoresearch.data import contracts

    contracts.clear_degradations()
    out = tmp_path / "scan_clean"
    smu.run(DATE, recall_n=300, l2_n=100, outdir=out, recall_mode="multi")
    assert not (out / "degraded.json").exists()
