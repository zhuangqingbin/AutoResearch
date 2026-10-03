"""P2 扩容 · signals / funnel / l2 / sector / calendar 行为键(2026-09-27)。

common 层谓词(scoring / regime)经 `contracts.scan_config.knob` 读 config —— 分层守卫不许它们 import scan。
"""
from __future__ import annotations

import json

import pandas as pd


def _cfg(tmp_path, monkeypatch, cfg: dict) -> None:
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", p)


# ───────────────────────── signals(common 层) ─────────────────────────


def test_falling_knife_and_healthy_band_follow_config(tmp_path, monkeypatch):
    from autoresearch.common import scoring

    frame = pd.DataFrame({"pct_60d": [-25.0, -15.0, 30.0, 45.0], "main_net_ratio": [1, 1, 1, 1], "cmf_20": [1, 1, 1, 1]})
    _cfg(tmp_path, monkeypatch, {"signals": {"knife_pct_60d": -10.0, "healthy_pct60_range": [0, 50]}})
    assert scoring.falling_knife_mask(frame).tolist() == [True, True, False, False]
    assert scoring.healthy_riser_mask(frame).tolist() == [False, False, True, True]
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", tmp_path / "nope.jsonc")
    assert scoring.falling_knife_mask(frame).tolist() == [True, False, False, False]
    assert scoring.healthy_riser_mask(frame).tolist() == [False, False, True, False]


def test_distortion_pledge_and_regime_thresholds_follow_config(tmp_path, monkeypatch):
    from autoresearch.common import regime, scoring

    _cfg(tmp_path, monkeypatch, {"signals": {"main_flow_distortion": {"ratio": 0.5}, "pledge_pct": {"high": 10, "warn": 5},
                                             "regime_thresholds": {"risk_on": 0.10}}})
    assert scoring.main_net_distortion_label(0.1, 5.0) == ""           # ratio_min 0.5 → 0.1 不算
    assert scoring.pledge_flag_label(12) != "" and scoring.pledge_flag_label(7) != ""
    frame = pd.DataFrame({"above_ma60": [1, 1, 0, 0, 0, 0, 0, 0, 0, 0], "pct_60d": [5.0] * 10})
    assert regime.classify_regime(frame).label == "trend"                # breadth 0.2 ≥ 0.10
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", tmp_path / "nope.jsonc")
    assert scoring.pledge_flag_label(12) == "" and regime.classify_regime(frame).label == "range"


def test_temperature_bands_follow_config(tmp_path, monkeypatch):
    from autoresearch.scan import temperature

    _cfg(tmp_path, monkeypatch, {"signals": {"temperature_bands": {"hot": 50}}})
    assert temperature.phase(55.0, None, None) == "高潮"
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", tmp_path / "nope.jsonc")
    assert temperature.phase(55.0, None, None) == "发酵"


def test_healthy_sector_qualification_follows_config(tmp_path, monkeypatch):
    from autoresearch.scan import market

    _cfg(tmp_path, monkeypatch, {"signals": {"healthy_sectors": {"min_members": 2, "top_k": 1}}})
    q = market.healthy_sectors_cfg()
    assert q["min_members"] == 2 and q["top_k"] == 1 and q["knife_median_min"] == -20.0
    assert q["mom_center"] == 10.0 and q["mom_half"] == 15.0 and q["main_pos_min"] == 0.5


# ───────────────────────── funnel / l2 ─────────────────────────


def test_funnel_extras_follow_config(tmp_path, monkeypatch):
    from autoresearch.scan import frame as scan_frame
    from autoresearch.scan.recall import channels

    _cfg(tmp_path, monkeypatch, {"funnel": {"heat_weights": {"turnover": 0.3}, "panel_lookback_days": 90,
                                            "panel_min_days": {"vol": 5}, "event_lookback_days": 4, "lens_top_n": 7}})
    assert channels.heat_weights() == {"turnover": 0.3, "vol_ratio": 0.10}
    assert scan_frame.panel_cfg() == {"lookback_days": 90, "min_days": {"vol": 5, "turnup": 40}}
    from autoresearch.scan import universe
    assert universe.event_lookback_days() == 4 and universe.lens_top_n() == 7
    assert universe.configured_recall_mode() == "multi"


def test_knife_cap_exempt_styles_follow_config(tmp_path, monkeypatch):
    from autoresearch.scan.recall import l2_stratify

    _cfg(tmp_path, monkeypatch, {"l2": {"knife_cap_exempt_styles": ["价值"]}})
    assert l2_stratify.knife_cap_exempt_styles() == frozenset({"价值"})
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", tmp_path / "nope.jsonc")
    assert l2_stratify.knife_cap_exempt_styles() == l2_stratify.KNIFE_CAP_EXEMPT_STYLES


# ───────────────────────── sector / calendar ─────────────────────────


def test_sector_pack_knobs_follow_config(tmp_path, monkeypatch):
    from autoresearch.sector import pack, reuse

    _cfg(tmp_path, monkeypatch, {"sector": {"reuse_mom_shift_pp": 1.0, "red_top_n": 1, "leaders_n": 2,
                                            "terrain_max_rows": 5, "readthrough_max": 1}})
    s = pack.sector_cfg()
    assert s == {"red_top_n": 1, "l2_conc_top_n": 3, "leaders_n": 2, "terrain_max_rows": 5, "readthrough_max": 1}
    assert reuse.configured_mom_shift_pp() == 1.0
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", tmp_path / "nope.jsonc")
    assert reuse.configured_mom_shift_pp() == 3.0 and pack.sector_cfg()["leaders_n"] == 5


def test_calendar_windows_follow_config(tmp_path, monkeypatch):
    from autoresearch.scan import calendar, index_events

    _cfg(tmp_path, monkeypatch, {"calendar": {"horizon_days": 7, "unlock_flag": {"min_ratio_pct": 9.0},
                                              "section": {"window_days": 3}, "index_post_window": 1}})
    c = calendar.calendar_cfg()
    assert c["horizon_days"] == 7
    assert c["unlock_flag"] == {"within_days": 30, "min_ratio_pct": 9.0}
    assert c["section"] == {"window_days": 3, "big_ratio_pct": 5.0}
    assert index_events.post_window() == 1
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", tmp_path / "nope.jsonc")
    assert calendar.calendar_cfg()["horizon_days"] == 35 and index_events.post_window() == 3
