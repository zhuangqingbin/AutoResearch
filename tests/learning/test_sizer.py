"""S3 纸面 sizer(shadow_buys 的 sized 轨):edge 映射 / 组合波动目标缩放 / cap / presence-gated
回退等权。合成 lake 分区,无网络。

spec: 2026-07-11 六问 brainstorm §8 拍板(方案 C)+ W1(2026-07-12);公式见 sizer.py 模块 docstring。
"""
from __future__ import annotations

import math
import statistics

import pandas as pd
import pytest

from autoresearch.learning.sizer import (
    annualized_vol,
    edge,
    size_shadow_signals,
    size_weights,
    trailing_stats,
)

# ───────────────────────── edge() ─────────────────────────


def test_edge_at_or_below_floor_is_zero():
    assert edge(55) == 0.0
    assert edge(30) == 0.0
    assert edge(0) == 0.0


def test_edge_linear_mapping():
    assert edge(77.5) == pytest.approx(0.125)          # (77.5-55)/45=0.5 × 0.25
    assert edge(100) == pytest.approx(0.25)             # 满分,clip 顶 × 0.25
    assert edge(145) == pytest.approx(0.25)             # 超过 100 依旧 clip 在 1.0


def test_edge_missing_or_unparseable_is_zero():
    assert edge(None) == 0.0
    assert edge("") == 0.0
    assert edge("abc") == 0.0
    assert edge(float("nan")) == 0.0


# ───────────────────────── annualized_vol() ─────────────────────────


def test_annualized_vol_matches_independent_calc():
    vals = [1.0, -1.0]                                  # 百分比:+1%/-1%
    expected = statistics.stdev([0.01, -0.01]) * math.sqrt(252)
    assert annualized_vol(vals) == pytest.approx(expected)


def test_annualized_vol_insufficient_samples_is_none():
    assert annualized_vol([1.0]) is None
    assert annualized_vol([]) is None


def test_annualized_vol_all_nan_is_none():
    assert annualized_vol([float("nan"), float("nan")]) is None


def test_annualized_vol_zero_variance_is_none():
    assert annualized_vol([0.0, 0.0, 0.0]) is None


def test_annualized_vol_only_uses_trailing_window():
    # 前 5 个极端值 + 后 20 个全 0 → window=20 只取后 20 个(std=0)→ None,证明窗口生效。
    assert annualized_vol([1000.0] * 5 + [0.0] * 20, window=20) is None


# ───────────────────────── size_weights() ─────────────────────────


def test_size_weights_empty_picks_is_empty():
    assert size_weights([]) == {}


def test_size_weights_single_pick_ignores_conviction_magnitude_above_floor():
    """n=1 有效 pick:组合波动目标缩放让唯一候选的仓位只取决于 vol_target/vol,与
    conviction 高低(只要过 55 门槛)无关——"先定风险预算、再切分"的必然推论,
    详见 sizer.py 模块 docstring 第 3 步"已知特性"。用 vol=0.5 保证不触发 40% cap。
    """
    w_lo = size_weights([{"code": "000001", "conviction": 56, "vol": 0.5}])["000001"]
    w_hi = size_weights([{"code": "000001", "conviction": 100, "vol": 0.5}])["000001"]
    assert w_lo == pytest.approx(w_hi)
    assert w_lo == pytest.approx(0.15 / 0.5)             # = vol_target / vol


def test_size_weights_multi_pick_tilts_by_relative_conviction():
    """多票日:conviction 通过 raw_i=edge_i/vol_i 的相对大小决定各票切走的风险预算比例
    (同 vol 时,权重比例 = edge 比例)。"""
    picks = [{"code": "000001", "conviction": 100, "vol": 0.5},    # edge=0.25
             {"code": "000002", "conviction": 70, "vol": 0.5}]      # edge=1/3*0.25=0.08333...
    w = size_weights(picks)
    assert w["000001"] == pytest.approx(3 * w["000002"])         # edge 比 = 0.25/0.08333=3
    edge_a, edge_b = 0.25, (70 - 55) / 45 * 0.25
    k = 0.15 / math.sqrt(edge_a ** 2 + edge_b ** 2)
    assert w["000001"] == pytest.approx(k * edge_a / 0.5)
    assert w["000002"] == pytest.approx(k * edge_b / 0.5)
    assert w["000001"] < 0.40 and w["000002"] < 0.40              # 均未触顶,纯公式效应


def test_size_weights_conviction_at_or_below_floor_gets_zero_not_fallback():
    """conviction ≤ 55(edge=0)但 vol 数据本身可得 → sized 权重=0,不是回退等权
    (这是刻意的分歧信号,不同于"数据缺失回退")。"""
    w = size_weights([{"code": "000001", "conviction": 50, "vol": 0.3}])
    assert w["000001"] == 0.0
    w2 = size_weights([{"code": "000001", "conviction": 55, "vol": 0.3}])
    assert w2["000001"] == 0.0


def test_size_weights_missing_vol_falls_back_to_equal_slot():
    w = size_weights([{"code": "000001", "conviction": 90, "vol": None}])
    assert w["000001"] == 0.10
    w2 = size_weights([{"code": "000001", "conviction": 90, "vol": None}], equal_slot=0.20)
    assert w2["000001"] == 0.20


def test_size_weights_mixed_batch_fallback_isolated_from_sized():
    """一票缺 vol 回退等权,另一票有效数据独立按 n=1 公式定价(不受回退票牵连)。"""
    picks = [{"code": "000001", "conviction": 90, "vol": 0.5},
             {"code": "000002", "conviction": 90, "vol": None}]
    w = size_weights(picks)
    assert w["000002"] == 0.10
    assert w["000001"] == pytest.approx(0.15 / 0.5)


def test_size_weights_name_cap_binds_without_liquidity_data():
    # 低 vol + 满 conviction → 未加 cap 前 raw 仓位远超 40%(0.15/0.1=1.5)。
    w = size_weights([{"code": "000001", "conviction": 100, "vol": 0.1}])
    assert w["000001"] == pytest.approx(0.40)


def test_size_weights_liquidity_cap_tighter_than_name_cap():
    # avg_amount=10,000,000 元 → liq_cap = 10e6*0.005/1e7 = 0.005,远紧于 40% 硬顶。
    w = size_weights([{"code": "000001", "conviction": 100, "vol": 0.1,
                        "avg_amount": 10_000_000.0}])
    assert w["000001"] == pytest.approx(0.005)


def test_size_weights_liquidity_cap_not_binding_when_amount_generous():
    # avg_amount 足够大(liq_cap 远超 40%)→ 40% 硬顶接管。
    w = size_weights([{"code": "000001", "conviction": 100, "vol": 0.1,
                        "avg_amount": 10_000_000_000.0}])
    assert w["000001"] == pytest.approx(0.40)


# ───────────────────────── trailing_stats() ─────────────────────────


def _mk_lake(tmp_path, days, code_rows, drop_amount_on=()):
    """code_rows: {date: {code: (pct_chg, amount)}}; drop_amount_on: 该日期整份不带 amount 列。"""
    lake = tmp_path / "lake"
    lake.mkdir()
    for d in days:
        rows = code_rows.get(d, {})
        recs = [{"ts_code": f"{c}.SZ", "pct_chg": v[0], "amount": v[1]}
                for c, v in rows.items()]
        df = pd.DataFrame(recs, columns=["ts_code", "pct_chg", "amount"])
        if d in drop_amount_on:
            df = df.drop(columns=["amount"])
        df.to_parquet(lake / f"{d}.parquet")
    return lake


def test_trailing_stats_vol_and_avg_amount(tmp_path):
    days = [f"202601{d:02d}" for d in range(1, 21)]        # 恰好 20 个"交易日"(= window,免切片歧义)
    degraded_day = days[9]                                  # 该日无 amount 列(schema 退化)
    pcts = [2.0 if i % 2 == 0 else -2.0 for i in range(20)]  # 000001 交替 +-2%(10 对)
    rows = {d: {"000001": (pcts[i], 50_000.0), "000002": (0.0, 50_000.0)}   # 千元
            for i, d in enumerate(days)}
    lake = _mk_lake(tmp_path, days, rows, drop_amount_on=(degraded_day,))
    out = trailing_stats(["000001", "000002", "000003"], days[-1], lake=lake, window=20)

    # 000001:交替 +-2% → 有波动;000002:恒 0% → std=0 → vol=None;000003:从未出现。
    assert out["000001"]["vol"] is not None
    expected_vol = statistics.stdev([p / 100.0 for p in pcts]) * math.sqrt(252)
    assert out["000001"]["vol"] == pytest.approx(expected_vol)
    assert out["000002"]["vol"] is None
    assert out["000003"]["vol"] is None
    assert out["000003"]["avg_amount"] is None

    # avg_amount 千元→元:50_000 千元 = 50_000_000 元(退化日不贡献,但仍是常数不影响均值)。
    assert out["000001"]["avg_amount"] == pytest.approx(50_000_000.0)
    assert out["000002"]["avg_amount"] == pytest.approx(50_000_000.0)


def test_trailing_stats_missing_lake_dir_returns_none_map(tmp_path):
    out = trailing_stats(["000001"], "20260101", lake=tmp_path / "nope")
    assert out["000001"] == {"vol": None, "avg_amount": None}


def test_trailing_stats_window_respects_trailing_slice(tmp_path):
    days = [f"202601{d:02d}" for d in range(1, 26)]         # 25 天
    rows = {}
    for i, d in enumerate(days):
        # 前 5 天极端 20%,后 20 天恒 0%——window=20 应只看后 20 天(std=0→None)。
        pct = 20.0 if i < 5 else 0.0
        rows[d] = {"000001": (pct, 1000.0)}
    lake = _mk_lake(tmp_path, days, rows)
    out = trailing_stats(["000001"], days[-1], lake=lake, window=20)
    assert out["000001"]["vol"] is None


# ───────────────────────── size_shadow_signals() ─────────────────────────


def test_size_shadow_signals_attaches_weight_per_day_group(tmp_path):
    days = [f"202601{d:02d}" for d in range(1, 22)]
    rows = {}
    for i, d in enumerate(days):
        pct = 2.0 if i % 2 == 0 else -2.0
        # 巨额成交额(千元)→ 流动性 cap 远不 binding,只让 40%/edge 差异生效(不与本测试意图相扰)。
        rows[d] = {"000001": (pct, 1e10), "000002": (pct, 1e10)}
    lake = _mk_lake(tmp_path, days, rows)
    signal_date = days[-1]
    signals = [{"date": signal_date, "code": "000001", "conviction": 100},
                {"date": signal_date, "code": "000002", "conviction": 70}]
    out = size_shadow_signals(signals, lake=lake)
    by_code = {s["code"]: s["weight"] for s in out}
    assert by_code["000001"] > by_code["000002"] > 0        # 高 conviction 切走更大份额(或封顶更高)
    assert all("weight" in s for s in out)


def test_size_shadow_signals_whole_day_falls_back_when_no_lake_data(tmp_path):
    signals = [{"date": "2026-01-01", "code": "000001", "conviction": 90},
               {"date": "2026-01-01", "code": "000002", "conviction": 60}]
    out = size_shadow_signals(signals, lake=tmp_path / "empty_lake")
    assert all(s["weight"] == 0.10 for s in out)             # 全天无 vol 数据 → 整日退化等权


def test_size_shadow_signals_groups_by_date_independently(tmp_path):
    days = [f"202601{d:02d}" for d in range(1, 22)]
    rows = {d: {"000001": (2.0 if i % 2 == 0 else -2.0, 50_000.0)}
            for i, d in enumerate(days)}
    lake = _mk_lake(tmp_path, days, rows)
    signals = [{"date": days[-1], "code": "000001", "conviction": 100},
               # 该日早于湖内任何分区(无匹配文件)→ 该组独立回退,不受 000001 那组影响。
               {"date": "20251201", "code": "000002", "conviction": 90}]
    out = size_shadow_signals(signals, lake=lake)
    by_code = {s["code"]: s["weight"] for s in out}
    assert by_code["000002"] == 0.10                          # 该日期无任何湖分区 → 回退等权
    assert by_code["000001"] != 0.10                          # 该日有效 → 走公式(大概率非 0.10)
