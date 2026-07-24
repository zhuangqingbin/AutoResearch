"""channel_audit 通道整编报告单测 —— 累计T+2账本 + 召回集Jaccard重叠矩阵。合成 staging,无网络。

覆盖 spec docs/specs/2026-07-11-recall-gate-pinned-config-design.md §2.3:
  - day_channel_stats:单日 L1_channels(长表)× attribution(fwd_2_oc)→ 每路 mean/unique_excess_t2 + hit_rate_t2
  - cumulative_ledger:跨日简单日频均值(同 channel_ledger.roll 口径),n_days<10 标 thin(⚠薄样本)
  - jaccard_matrix:每路跨日 union 去重代码集合的两两 Jaccard(共同召回码/并集)
  - consolidation_notes:仅陈述数据(高重叠对 + 负 unique_excess_t2 路),不改配置
  - audit():端到端读两日合成 staging 文件(真实列名 channel/code、code/fwd_2_oc)
"""
from __future__ import annotations

import inspect

import pandas as pd

from autoresearch.research.channel_audit import (
    _load_day,
    audit,
    consolidation_notes,
    cumulative_ledger,
    day_channel_stats,
    jaccard_matrix,
    render,
)

# ── 两日合成数据(两路 chan_a/chan_b:000001 两日皆重叠,其余各自独占)──
#   day1 median(0.05,0.09,0.01)=0.05 → excess: 000001=0.00 000002=+0.04 000003=-0.04
#   day2 median(0.03,0.10,-0.02)=0.03 → excess: 000001=0.00 000004=+0.07 000005=-0.05
_DAY1_CHANNELS = pd.DataFrame([
    {"channel": "chan_a", "code": "000001", "channel_rank": 1, "channel_score": 9.0},
    {"channel": "chan_a", "code": "000002", "channel_rank": 2, "channel_score": 8.0},
    {"channel": "chan_b", "code": "000001", "channel_rank": 1, "channel_score": 7.0},
    {"channel": "chan_b", "code": "000003", "channel_rank": 2, "channel_score": 6.0},
])
_DAY1_ATTR = pd.DataFrame([
    {"code": "000001", "fwd_2_oc": 0.05},
    {"code": "000002", "fwd_2_oc": 0.09},
    {"code": "000003", "fwd_2_oc": 0.01},
])
_DAY2_CHANNELS = pd.DataFrame([
    {"channel": "chan_a", "code": "000001", "channel_rank": 1, "channel_score": 9.5},
    {"channel": "chan_a", "code": "000004", "channel_rank": 2, "channel_score": 8.5},
    {"channel": "chan_b", "code": "000001", "channel_rank": 1, "channel_score": 7.5},
    {"channel": "chan_b", "code": "000005", "channel_rank": 2, "channel_score": 6.5},
])
_DAY2_ATTR = pd.DataFrame([
    {"code": "000001", "fwd_2_oc": 0.03},
    {"code": "000004", "fwd_2_oc": 0.10},
    {"code": "000005", "fwd_2_oc": -0.02},
])


def _daily():
    return {
        "2026-06-20": day_channel_stats(_DAY1_CHANNELS, _DAY1_ATTR),
        "2026-06-21": day_channel_stats(_DAY2_CHANNELS, _DAY2_ATTR),
    }


# ───────────────────────── day_channel_stats(纯函数,单日) ─────────────────────────


def test_day_channel_stats_excess_unique_hit():
    day1 = day_channel_stats(_DAY1_CHANNELS, _DAY1_ATTR)
    a = day1[day1["channel"] == "chan_a"].iloc[0]
    b = day1[day1["channel"] == "chan_b"].iloc[0]
    # chan_a members=000001(excess0.0)/000002(excess+0.04);000001 也被 chan_b 召回→非 unique
    assert a["n_recalled"] == 2 and a["n_unique"] == 1
    assert abs(a["mean_excess_t2"] - 0.02) < 1e-6         # mean(0.00, 0.04)
    assert abs(a["unique_excess_t2"] - 0.04) < 1e-6        # 仅 000002
    assert abs(a["hit_rate_t2"] - 0.5) < 1e-9              # 2 members 中 1 个 >0
    # chan_b members=000001(excess0.0)/000003(excess-0.04)
    assert abs(b["mean_excess_t2"] - (-0.02)) < 1e-6
    assert abs(b["unique_excess_t2"] - (-0.04)) < 1e-6
    assert b["hit_rate_t2"] == 0.0


def test_day_channel_stats_empty_when_no_channel_column():
    out = day_channel_stats(pd.DataFrame({"code": ["000001"]}), _DAY1_ATTR)
    assert out.empty


# ───────────────────────── cumulative_ledger(跨日聚合) ─────────────────────────


def test_cumulative_ledger_aggregates_and_flags_thin():
    ledger = cumulative_ledger(_daily())
    assert set(ledger["channel"]) == {"chan_a", "chan_b"}
    a = ledger[ledger["channel"] == "chan_a"].iloc[0]
    b = ledger[ledger["channel"] == "chan_b"].iloc[0]
    assert int(a["n_days"]) == 2 and int(b["n_days"]) == 2
    assert bool(a["thin"]) is True and bool(b["thin"]) is True          # 2 < 10 门槛
    assert abs(a["unique_excess_t2"] - 0.055) < 1e-6    # mean(0.04, 0.07)
    assert abs(b["unique_excess_t2"] - (-0.045)) < 1e-6  # mean(-0.04, -0.05)
    assert abs(a["mean_excess_t2"] - 0.0275) < 1e-6      # mean(0.02, 0.035)
    # 降序:chan_a(unique+0.055) 排 chan_b(-0.045) 前
    order = ledger["channel"].tolist()
    assert order.index("chan_a") < order.index("chan_b")


def test_cumulative_ledger_empty_when_no_days():
    led = cumulative_ledger({})
    assert led.empty and "channel" in led.columns


def test_cumulative_ledger_not_thin_when_enough_days():
    daily = {f"2026-06-{10 + i:02d}": _daily()["2026-06-20"] for i in range(12)}
    led = cumulative_ledger(daily)
    assert (~led["thin"]).all()                          # 12 ≥ 10 门槛,均不薄样本


# ───────────────────────── jaccard_matrix(两两召回集重叠) ─────────────────────────


def test_jaccard_matrix_common_and_union():
    jac = jaccard_matrix(_daily())
    assert len(jac) == 1                                   # 仅两路 → 一对
    row = jac.iloc[0]
    assert {row["channel_a"], row["channel_b"]} == {"chan_a", "chan_b"}
    # chan_a 累计代码={000001,000002,000004};chan_b 累计代码={000001,000003,000005}
    assert row["common"] == 1                              # 仅 000001
    assert row["union"] == 5
    assert abs(row["jaccard"] - 0.2) < 1e-6


def test_jaccard_matrix_empty_when_single_channel():
    daily = {"2026-06-20": day_channel_stats(_DAY1_CHANNELS[_DAY1_CHANNELS["channel"] == "chan_a"], _DAY1_ATTR)}
    jac = jaccard_matrix(daily)
    assert jac.empty


# ───────────────────────── consolidation_notes(仅陈述数据) ─────────────────────────


def test_consolidation_notes_flags_overlap_and_negative_unique():
    ledger = pd.DataFrame([
        {"channel": "trend", "n_days": 12, "mean_excess_t2": 0.01, "unique_excess_t2": 0.02,
         "hit_rate_t2": 0.55, "thin": False},
        {"channel": "heat", "n_days": 12, "mean_excess_t2": -0.01, "unique_excess_t2": -0.03,
         "hit_rate_t2": 0.40, "thin": False},
    ])
    jac = pd.DataFrame([
        {"channel_a": "trend", "channel_b": "heat", "common": 80, "union": 120, "jaccard": 0.6667},
    ])
    notes = consolidation_notes(ledger, jac)
    joined = "\n".join(notes)
    assert "trend" in joined and "heat" in joined and "0.67" in joined
    assert "退役" in joined or "整编" in joined            # 负 unique_excess 那行点名建议


def test_consolidation_notes_no_signal_when_low_overlap_and_positive():
    ledger = pd.DataFrame([
        {"channel": "a", "n_days": 12, "mean_excess_t2": 0.01, "unique_excess_t2": 0.02,
         "hit_rate_t2": 0.55, "thin": False},
    ])
    jac = pd.DataFrame(columns=["channel_a", "channel_b", "common", "union", "jaccard"])
    notes = consolidation_notes(ledger, jac)
    assert len(notes) == 1 and "无" in notes[0]


# ───────────────────────── audit() 端到端(真实 staging 文件读取) ─────────────────────────


def _write_day(root, date, channels_df, attr_df):
    d = root / date
    d.mkdir(parents=True)
    channels_df.to_csv(d / "L1_channels.csv", index=False)
    (d / "retro").mkdir()
    attr_df.to_csv(d / "retro" / "attribution.csv", index=False)


def test_audit_end_to_end_reads_staging_files(tmp_path):
    _write_day(tmp_path, "2026-06-20", _DAY1_CHANNELS, _DAY1_ATTR)
    _write_day(tmp_path, "2026-06-21", _DAY2_CHANNELS, _DAY2_ATTR)
    result = audit(scan_root=tmp_path, days=30)
    assert result["dates"] == ["2026-06-20", "2026-06-21"]
    ledger = result["ledger"]
    order = ledger["channel"].tolist()
    assert order.index("chan_a") < order.index("chan_b")
    a = ledger[ledger["channel"] == "chan_a"].iloc[0]
    assert abs(a["unique_excess_t2"] - 0.055) < 1e-6
    jac = result["jaccard"]
    assert jac.iloc[0]["common"] == 1 and jac.iloc[0]["union"] == 5
    assert abs(jac.iloc[0]["jaccard"] - 0.2) < 1e-6
    assert isinstance(result["notes"], list) and len(result["notes"]) >= 1


def test_audit_days_window_limits_and_skips_incomplete(tmp_path):
    _write_day(tmp_path, "2026-06-18", _DAY1_CHANNELS, _DAY1_ATTR)
    _write_day(tmp_path, "2026-06-20", _DAY1_CHANNELS, _DAY1_ATTR)
    _write_day(tmp_path, "2026-06-21", _DAY2_CHANNELS, _DAY2_ATTR)
    # 06-19 只有 channels 无 attribution(不完整目录)→ 应被静默跳过
    incomplete = tmp_path / "2026-06-19"
    incomplete.mkdir()
    _DAY1_CHANNELS.to_csv(incomplete / "L1_channels.csv", index=False)
    result = audit(scan_root=tmp_path, days=2)
    assert result["dates"] == ["2026-06-20", "2026-06-21"]   # 只取最近两个完整日


def test_audit_empty_root_no_crash(tmp_path):
    result = audit(scan_root=tmp_path / "nonexistent", days=30)
    assert result["dates"] == []
    assert result["ledger"].empty and result["jaccard"].empty


def test_audit_days_default_is_30():
    assert inspect.signature(audit).parameters["days"].default == 30


# ───────────────────────── _load_day/audit(variant=...) 影子长表读取(Wave4 Task4 仪器修复) ─────────────────────────
# 目的:让影子召回路(如 plus_event/pre_healthy)也能用同一套 unique_excess_t2 计算裁决——
# 主/影子 channels 内容故意不同(DAY1 vs DAY2),这样"读对文件"与"读错文件"的结果不可能撞车。


def test_load_day_variant_reads_shadow_file_not_main(tmp_path):
    """variant= 给定 → 读 shadow/L1_channels_<variant>.csv,不读主 L1_channels.csv。"""
    d = tmp_path / "2026-06-20"
    d.mkdir(parents=True)
    _DAY1_CHANNELS.to_csv(d / "L1_channels.csv", index=False)                          # 主文件(不应被读到)
    (d / "shadow").mkdir()
    _DAY2_CHANNELS.to_csv(d / "shadow" / "L1_channels_plus_event.csv", index=False)    # 影子(应被读到)
    (d / "retro").mkdir()
    _DAY1_ATTR.to_csv(d / "retro" / "attribution.csv", index=False)
    loaded = _load_day(tmp_path, "2026-06-20", variant="plus_event")
    assert loaded is not None
    ch, _ = loaded
    assert set(ch["code"]) == {"000001", "000004", "000005"}   # DAY2_CHANNELS 的码,不是 DAY1 的 {1,2,3}


def test_load_day_variant_missing_file_returns_none(tmp_path):
    """指定 variant 但该日没跑过该影子(无 shadow/ 目录或无对应文件)→ None,与主文件缺失同行为。"""
    d = tmp_path / "2026-06-20"
    d.mkdir(parents=True)
    _DAY1_CHANNELS.to_csv(d / "L1_channels.csv", index=False)
    (d / "retro").mkdir()
    _DAY1_ATTR.to_csv(d / "retro" / "attribution.csv", index=False)
    assert _load_day(tmp_path, "2026-06-20", variant="plus_event") is None


def test_load_day_no_variant_reads_main_file_unchanged(tmp_path):
    """variant=None(默认,现行为)→ 其余口径一字不改,仍读主 L1_channels.csv。"""
    d = tmp_path / "2026-06-20"
    d.mkdir(parents=True)
    _DAY1_CHANNELS.to_csv(d / "L1_channels.csv", index=False)
    (d / "retro").mkdir()
    _DAY1_ATTR.to_csv(d / "retro" / "attribution.csv", index=False)
    loaded = _load_day(tmp_path, "2026-06-20")
    assert loaded is not None
    ch, _ = loaded
    assert set(ch["code"]) == {"000001", "000002", "000003"}   # DAY1_CHANNELS 的码


def test_audit_variant_end_to_end_uses_shadow_ledger(tmp_path):
    """audit(variant=...) 端到端:账本用影子长表配同日 attribution 算出,与主文件配出的不同
    (chan_a 的 unique_excess_t2 只有读到 DAY2_CHANNELS 才是 +0.07;读到 DAY1_CHANNELS 会是 +0.04
    或因码对不上 attribution 而是 None——两者都不等于 0.07,故此断言能鉴别读对读错)。"""
    d = tmp_path / "2026-06-20"
    d.mkdir(parents=True)
    _DAY1_CHANNELS.to_csv(d / "L1_channels.csv", index=False)
    (d / "shadow").mkdir()
    _DAY2_CHANNELS.to_csv(d / "shadow" / "L1_channels_plus_event.csv", index=False)
    (d / "retro").mkdir()
    _DAY2_ATTR.to_csv(d / "retro" / "attribution.csv", index=False)
    result = audit(scan_root=tmp_path, days=30, variant="plus_event")
    assert result["dates"] == ["2026-06-20"]
    a = result["ledger"][result["ledger"]["channel"] == "chan_a"].iloc[0]
    assert abs(a["unique_excess_t2"] - 0.07) < 1e-6


def test_audit_variant_missing_shadow_skips_day(tmp_path):
    """指定 variant 但当日未产出该影子文件(未跑过该变体)→ 跳过该日,不报错。"""
    _write_day(tmp_path, "2026-06-20", _DAY1_CHANNELS, _DAY1_ATTR)     # 只有主文件,无 shadow/
    result = audit(scan_root=tmp_path, days=30, variant="plus_event")
    assert result["dates"] == []


# ───────────────────────── render(markdown 三节) ─────────────────────────


def test_render_has_three_sections_and_thin_marker():
    result = {
        "dates": ["2026-06-20", "2026-06-21"],
        "ledger": cumulative_ledger(_daily()),
        "jaccard": jaccard_matrix(_daily()),
        "notes": ["无高重叠对 / 无负 unique_excess_t2 路——暂无整编信号"],
    }
    md = "\n".join(render(result))
    assert "① 各路累计 T+2 账本" in md
    assert "② 两两召回集 Jaccard 重叠矩阵" in md
    assert "③" in md and "unique_excess_t2" in md
    assert "⚠薄样本" in md                    # thin=True(2 天 < 10)
    assert "chan_a" in md and "chan_b" in md


def test_render_no_data_placeholder():
    md = "\n".join(render({"dates": [], "ledger": pd.DataFrame(), "jaccard": pd.DataFrame(), "notes": []}))
    assert "无数据" in md
