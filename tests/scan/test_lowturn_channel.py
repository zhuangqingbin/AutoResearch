"""L1 `lowturn` 路(2026-08-22 批 A)—— 门=单一谓词、排序=rc_score、L2 独立桶、缺列空帧。

立案:2026-08-21 首跑实测 lowturn 旗全帧 120 → L1 17 → **L2 0**,L3 侧整套空转
(design 2026-08-22-funnel-shape-after-lowturn-first-run §1.1)。本文件锁住「生产者在场」。
合成帧,零网络、零配置依赖(阈值显式传或用内建默认)。
"""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.common.turnup import LOWTURN_DEFAULTS, lowturn_mask
from autoresearch.scan.recall import CHANNEL_DEFAULTS, build, registered_channels
from autoresearch.scan.recall.l2_stratify import (
    DEFAULT_FLOORS,
    STYLE_CHANNELS,
    effective_floors,
)


def _row(code, *, name="甲", dist_high_60=-30.0, pct_60d=0.0, pct_5d=2.0,
         vol_ratio_20=2.0, above_ma20=1.0, ma5_gt_ma10=1.0,
         main_inflow_yi=1.0, cmf_20=0.1, main_net_ratio=0.1, rsi6=60.0,
         days_no_new_low=20.0, vol_ma5_prev=1.0, vol_ma20_prev=2.0, dist_low_60=30.0):
    """默认行 = 低位转强旗亮(且非健康上涨:pct_60d=0 不满足 0<pct_60d<40)。"""
    return dict(code=code, name=name, dist_high_60=dist_high_60, pct_60d=pct_60d,
                pct_5d=pct_5d, vol_ratio_20=vol_ratio_20, above_ma20=above_ma20,
                ma5_gt_ma10=ma5_gt_ma10, main_inflow_yi=main_inflow_yi, cmf_20=cmf_20,
                main_net_ratio=main_net_ratio, rsi6=rsi6, days_no_new_low=days_no_new_low,
                vol_ma5_prev=vol_ma5_prev, vol_ma20_prev=vol_ma20_prev,
                dist_low_60=dist_low_60, composite=50.0)


def _frame(rows):
    return pd.DataFrame(rows)


def test_registered_with_quota_and_floor():
    assert "lowturn" in registered_channels()
    spec = CHANNEL_DEFAULTS["lowturn"]
    assert (spec.quota, spec.floor) == (120, 40)


def test_gate_is_single_source_of_truth_lowturn_mask():
    """门必须逐行等于 `turnup.lowturn_mask` —— 不许通道自造第二套谓词。"""
    rows = [_row("000001"),                                  # 旗亮
            _row("000002", pct_5d=-1.0),                     # 转强✗
            _row("000003", vol_ratio_20=1.0),                # 放量✗
            _row("000004", above_ma20=0.0),                  # 站回 MA20✗
            _row("000005", dist_high_60=-5.0),               # 低位✗
            _row("000006", main_inflow_yi=-1.0, cmf_20=-0.1),  # 资金✗
            _row("000007", pct_60d=20.0, main_net_ratio=0.1, cmf_20=0.1),  # 健康上涨 → 互斥✗
            _row("000008", name="ST甲")]                     # ST✗
    f = _frame(rows)
    expected = set(f.loc[lowturn_mask(f, LOWTURN_DEFAULTS).values, "code"])
    got = set(build("lowturn")(f, "2026-08-22", 50)["code"])
    assert got == expected == {"000001"}


def test_orders_by_reversal_confirm_score_desc():
    """排序 = lens_reversal_confirm 的 reversal_confirm_score(同模块两档,零新数学)。"""
    from autoresearch.common.scoring import lens_reversal_confirm

    rows = [_row(f"00000{i}", dist_high_60=-20.0 - 5 * i, vol_ratio_20=1.2 + 0.5 * i)
            for i in range(1, 5)]
    f = _frame(rows)
    out = build("lowturn")(f, "2026-08-22", 50)
    g = lens_reversal_confirm(f)
    want = (g.loc[lowturn_mask(f, LOWTURN_DEFAULTS).values]
            .sort_values("reversal_confirm_score", ascending=False)["code"].tolist())
    assert out["code"].tolist() == want
    assert out["channel_score"].is_monotonic_decreasing


def test_truncates_to_k():
    f = _frame([_row(f"{i:06d}") for i in range(20)])
    assert len(build("lowturn")(f, "2026-08-22", 7)) == 7


@pytest.mark.parametrize("drop", ["dist_high_60", "pct_5d", "vol_ratio_20", "above_ma20"])
def test_missing_core_column_degrades_to_empty(drop):
    f = _frame([_row(f"{i:06d}") for i in range(5)]).drop(columns=[drop])
    out = build("lowturn")(f, "2026-08-22", 50)
    assert out.empty and list(out.columns) == ["code", "channel_rank", "channel_score"]


def test_empty_frame_degrades_to_empty():
    out = build("lowturn")(pd.DataFrame(), "2026-08-22", 50)
    assert out.empty and list(out.columns) == ["code", "channel_rank", "channel_score"]


def test_no_row_passes_gate_returns_empty():
    f = _frame([_row(f"{i:06d}", pct_5d=-3.0) for i in range(5)])
    assert build("lowturn")(f, "2026-08-22", 50).empty


def test_l2_bucket_is_independent_with_floor_8():
    """翻转昨稿 R5:独立桶,不与「反转」共用 floor(两个谓词是两群票,§1.1)。"""
    assert STYLE_CHANNELS["低位转强"] == ("lowturn",)
    assert DEFAULT_FLOORS["低位转强"] == 8
    assert "lowturn" not in STYLE_CHANNELS["反转"]


def test_cfg_comes_from_l3_lowturn_block(monkeypatch):
    """阈值单一事实源 = scan_config 的 `l3.lowturn`(与 L3 旗共用),不是通道自带常量。"""
    monkeypatch.setattr("autoresearch.scan.user_config.load_user_config",
                        lambda *a, **k: {"l3": {"lowturn": {"min_pct_5d": 5.0}}})
    f = _frame([_row("000001", pct_5d=2.0), _row("000002", pct_5d=9.0)])
    assert build("lowturn")(f, "2026-08-22", 50)["code"].tolist() == ["000002"]


def test_effective_floors_zeroes_disabled_buckets_only():
    got = effective_floors(DEFAULT_FLOORS, ["composite", "momentum", "heat", "lowturn"])
    assert got["趋势"] == DEFAULT_FLOORS["趋势"]        # momentum/heat 在 → 保留
    assert got["低位转强"] == DEFAULT_FLOORS["低位转强"]  # lowturn 在 → 保留
    assert got["健康"] == 0 and got["反转"] == 0 and got["价值"] == 0


def test_effective_floors_none_is_parity():
    assert effective_floors(DEFAULT_FLOORS, None) is DEFAULT_FLOORS


def test_disabling_lowturn_channel_zeroes_its_bucket():
    """回滚杆:从 recall_channels 摘掉 lowturn → 桶 floor 自动 0(不必手工改 DEFAULT_FLOORS)。"""
    assert effective_floors(DEFAULT_FLOORS, ["composite", "momentum", "healthy"])["低位转强"] == 0
