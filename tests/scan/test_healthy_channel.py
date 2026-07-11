"""healthy 召回通道 + L2 健康桶 + pre_healthy 影子反事实。合成,无网络。

spec: docs/specs/2026-07-03-scan-healthy-channel-design.md
"""
from __future__ import annotations

import pandas as pd

from autoresearch.common.scoring import healthy_riser_mask
from autoresearch.scan.recall import build, registered_channels
from autoresearch.scan.recall.registry import CHANNEL_DEFAULTS


def _frame():
    rows = [
        # 健康上涨:温和涨 + 主力+ + cmf+(共振强度递减)
        {"code": "000001", "pct_60d": 15.0, "main_net_ratio": 0.08, "cmf_20": 0.30},
        {"code": "000002", "pct_60d": 30.0, "main_net_ratio": 0.05, "cmf_20": 0.10},
        {"code": "000003", "pct_60d": 5.0, "main_net_ratio": 0.01, "cmf_20": 0.02},
        # 不健康:过热 / 下跌 / 主力流出 / cmf 负
        {"code": "000004", "pct_60d": 80.0, "main_net_ratio": 0.09, "cmf_20": 0.40},
        {"code": "000005", "pct_60d": -10.0, "main_net_ratio": 0.05, "cmf_20": 0.20},
        {"code": "000006", "pct_60d": 20.0, "main_net_ratio": -0.01, "cmf_20": 0.20},
        {"code": "000007", "pct_60d": 20.0, "main_net_ratio": 0.05, "cmf_20": -0.05},
    ]
    return pd.DataFrame(rows)


def test_mask_single_source_of_truth():
    m = healthy_riser_mask(_frame())
    assert list(_frame()[m]["code"]) == ["000001", "000002", "000003"]
    assert healthy_riser_mask(pd.DataFrame({"pct_60d": [1.0]})) is None   # 缺列 → None
    from autoresearch.scan.menu import _healthy
    assert _healthy(_frame()) == 3                                        # menu 复用同一谓词


def test_healthy_channel_gate_and_order():
    out = build("healthy")(_frame(), "2026-07-03", 10)
    assert list(out["code"]) == ["000001", "000002", "000003"]            # 共振强度序
    assert len(build("healthy")(_frame(), "2026-07-03", 2)) == 2         # 截 top-k
    empty = build("healthy")(pd.DataFrame({"code": ["1"], "pct_60d": [5.0]}), "d", 5)
    assert len(empty) == 0                                                # 缺列 → 空帧降级


def test_registered_with_quota():
    assert "healthy" in registered_channels()
    assert CHANNEL_DEFAULTS["healthy"].quota == 150 and CHANNEL_DEFAULTS["healthy"].floor == 40


def test_l2_healthy_bucket_floor():
    """健康桶 floor:sn 排名垫底的 healthy 票被救回 ≥15 只(l2_lane_reserved)。"""
    from autoresearch.scan.recall.l2_stratify import DEFAULT_FLOORS, stratified_l2
    assert DEFAULT_FLOORS["健康"] == 15
    rows = []
    for i in range(120):                       # 高分非健康票(占满 merit 核)
        rows.append({"code": f"6{i:05d}", "industry": f"行业{i % 12}", "composite": 90 - i * 0.1,
                     "recall_channels": "composite", "pct_60d": -30.0})
    for i in range(30):                        # 低分 healthy 票(全靠桶救)
        rows.append({"code": f"3{i:05d}", "industry": f"行业{i % 12}", "composite": 10 - i * 0.1,
                     "recall_channels": "healthy|main_fund", "pct_60d": 15.0})
    out = stratified_l2(pd.DataFrame(rows), l2_n=100)
    got = out[out["recall_channels"].str.contains("healthy")]
    assert len(got) >= 15 and got["l2_lane_reserved"].all()


def test_shadow_pre_healthy_counterfactual(tmp_path, monkeypatch):
    """write_shadow_variants:4 变体落盘;pre_healthy 的召回不含 healthy 标。

    capfloor20(Task 8)真重跑 L0(build_market_frame)→ 唯一需要 mock 取数入口的变体,
    镜像 tests/scan/test_recall_wiring.py 的 `patched` fixture 姿势,保持本文件"NO network"契约。
    """
    import sys
    sys.path.insert(0, "tests/scan")
    from _synth_universe import synth_universe

    from autoresearch.common.scoring import _PRIOR_WEIGHTS, composite_score
    from autoresearch.data import tushare_source
    from autoresearch.scan.universe import recall_select, write_shadow_variants
    uni = synth_universe(300)
    monkeypatch.setattr(tushare_source, "fetch_universe_tushare",
                        lambda *a, **k: uni.copy(), raising=True)
    monkeypatch.setattr("autoresearch.scan.frame._harvest_vol_series",
                        lambda codes, d, lookback=20: pd.DataFrame(columns=["code"]), raising=True)
    scored = composite_score(uni, _PRIOR_WEIGHTS)
    recall, _ = recall_select(scored, "2026-07-03", 150, "multi", None)
    names = write_shadow_variants(tmp_path, scored, recall, "2026-07-03", 150, 60,
                                  None, 0.20, ["l2_rank", "code", "recall_channels", "composite"])
    assert set(names) == {"nostrat", "nocap", "pre_healthy", "capfloor20"}
    pre = pd.read_csv(tmp_path / "shadow" / "L2_pre_healthy.csv")
    assert len(pre) and not pre["recall_channels"].fillna("").str.contains("healthy").any()
    assert (tmp_path / "shadow" / "L2_nostrat.csv").exists()
    assert (tmp_path / "shadow" / "L2_capfloor20.csv").exists()


def test_shadow_capfloor20_failure_does_not_block_free_variants(tmp_path, monkeypatch):
    """capfloor20 重取数失败(如网络异常)→ 其余零成本变体仍正常落盘,不被牵连(try/except 隔离)。"""
    import sys
    sys.path.insert(0, "tests/scan")
    from _synth_universe import synth_universe

    import autoresearch.scan.universe as smu
    from autoresearch.common.scoring import _PRIOR_WEIGHTS, composite_score
    uni = synth_universe(300)

    def _boom(*a, **k):
        raise RuntimeError("网络不可用(模拟)")

    monkeypatch.setattr(smu, "build_market_frame", _boom, raising=True)
    scored = composite_score(uni, _PRIOR_WEIGHTS)
    recall, _ = smu.recall_select(scored, "2026-07-03", 150, "multi", None)
    names = smu.write_shadow_variants(tmp_path, scored, recall, "2026-07-03", 150, 60,
                                      None, 0.20, ["l2_rank", "code", "recall_channels", "composite"])
    assert set(names) == {"nostrat", "nocap", "pre_healthy"}   # capfloor20 静默跳过,不进 variants
    assert (tmp_path / "shadow" / "L2_nostrat.csv").exists()
    assert (tmp_path / "shadow" / "L2_pre_healthy.csv").exists()
    assert not (tmp_path / "shadow" / "L2_capfloor20.csv").exists()
