"""l2_eval.forward_compare 单测 —— stratified vs champion 前向对照核心。合成 panel,无网络。

覆盖 spec §B:
  - stratified top-N 指标(mean_fwd/hit/n)算对
  - 注入"按真信号排序"的 champion_fn → champion mean_fwd ≥ stratified + delta 存在
  - 无 champion(store 无)→ champion=None(优雅降级)
  - ScanConfig.l2_engine 默认 stratified(parity)
"""
from __future__ import annotations

import pandas as pd

from autoresearch.research.l2_eval import forward_compare
from autoresearch.scan.config import ScanConfig


def _panel(n=300):
    """composite 随 i 降序(与 fwd 无关);signal == fwd_5_oc(champion 若按 signal 排即抓到高 fwd)。"""
    rows = []
    for i in range(n):
        signal = (i % 50) / 50.0
        rows.append({"code": f"{i:06d}", "composite": float(n - i), "industry": "半导体",
                     "recall_channels": "composite", "pct_60d": 1.0,
                     "fwd_5_oc": signal, "signal": signal})
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
