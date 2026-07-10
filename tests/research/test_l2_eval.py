"""l2_eval.forward_compare 单测 —— stratified vs champion 前向对照核心。合成 panel,无网络。

覆盖 spec §B:
  - stratified top-N 指标(mean_fwd/hit/n)算对
  - 注入"按真信号排序"的 champion_fn → champion mean_fwd ≥ stratified + delta 存在
  - 无 champion(store 无)→ champion=None(优雅降级)
  - ScanConfig.l2_engine 默认 stratified(parity)
  - label_col 默认超短主尺 fwd_2_oc(2026-07-10 用户裁定)
"""
from __future__ import annotations

import pandas as pd

from autoresearch.research.l2_eval import forward_compare
from autoresearch.scan.config import ScanConfig


def _panel(n=300):
    """composite 随 i 降序(与 fwd 无关);signal == fwd_2_oc == fwd_5_oc(champion 若按 signal 排即抓到高 fwd)。"""
    rows = []
    for i in range(n):
        signal = (i % 50) / 50.0
        rows.append({"code": f"{i:06d}", "composite": float(n - i), "industry": "半导体",
                     "recall_channels": "composite", "pct_60d": 1.0,
                     "fwd_2_oc": signal, "fwd_5_oc": signal, "signal": signal})
    return pd.DataFrame(rows)


def test_stratified_metrics_present():
    out = forward_compare(_panel(), l2_n=50)
    assert out["stratified"]["n"] == 50
    assert "mean_fwd" in out["stratified"] and "hit" in out["stratified"]


def test_champion_beats_when_ranks_by_signal():
    out = forward_compare(_panel(), l2_n=50, champion_fn=lambda df: df["signal"].to_numpy())
    assert out["champion"] is not None
    assert out["champion"]["mean_fwd"] >= out["stratified"]["mean_fwd"]
    assert "delta" in out and out["delta"] >= 0


def test_no_champion_degrades_gracefully():
    out = forward_compare(_panel(), l2_n=50, l2_model="nonexistent_champ_xyz")
    assert out["champion"] is None                    # store 无 champion → 不崩
    assert out["stratified"]["n"] == 50


def test_config_l2_engine_default_stratified():
    assert ScanConfig().l2_engine == "stratified"


def test_forward_compare_default_label_t2():
    """主口径契约(brief Step 1 逐字):label_col 默认 = fwd_2_oc。"""
    import inspect

    assert inspect.signature(forward_compare).parameters["label_col"].default == "fwd_2_oc"


def test_forward_compare_reads_fwd2_by_default_not_fwd5():
    """行为证明(不止签名):不传 label_col 时真读 fwd_2_oc 列,非巧合与 fwd_5_oc 同值。"""
    panel = _panel().assign(fwd_2_oc=0.2, fwd_5_oc=0.0)   # 故意让两列不同
    out = forward_compare(panel, l2_n=50)
    assert abs(out["stratified"]["mean_fwd"] - 0.2) < 1e-9
