"""P2 扩容 · L4 块 + sentinel:预算五面旗 / 哨兵阈 / rubric / 简报窗 / producers 窗 / slim 地板 / 复核 / intel(2026-09-27)。

每键:写进 config → 消费点读到(默认 = 迁前常量,逐字 parity)。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _cfg(tmp_path, monkeypatch, cfg: dict) -> None:
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", p)


# ───────────────────────── l4.budget ─────────────────────────


def test_budget_cfg_deep_merges(tmp_path, monkeypatch):
    from autoresearch.scan import menu

    _cfg(tmp_path, monkeypatch, {"l4": {"budget": {"base": 20, "flags": {"healthy_min": 5}, "tiers": {"multi": 0.4}}}})
    b = menu.budget_cfg()
    assert b["base"] == 20 and b["floor"] == 12
    assert b["flags"]["healthy_min"] == 5 and b["flags"]["knife_share_max"] == 0.60
    assert b["tiers"] == {"one_flag": 0.75, "multi": 0.4, "hard_min": 8, "hard_div": 3}


def test_l4_budget_uses_config_base_and_flag_thresholds(tmp_path, monkeypatch):
    from autoresearch.scan import menu

    scan_dir = tmp_path / "2026-09-26"
    scan_dir.mkdir()
    (scan_dir / "L2_gbdt_top200.csv").write_text("code\n000001\n", encoding="utf-8")
    monkeypatch.setattr(menu, "_knife_share", lambda df: 0.10)
    monkeypatch.setattr(menu, "_healthy", lambda df: 3)
    monkeypatch.setattr(menu, "zero_buy_streak", lambda scan_dir, lookback=None: 0)
    _cfg(tmp_path, monkeypatch, {"l4": {"budget": {"base": 20, "flags": {"healthy_min": 5}}}})
    n, why = menu.l4_budget(scan_dir)
    assert n == 15 and "健康涨仅3只" in why                     # 一面旗 → round(20 × 0.75)
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", tmp_path / "nope.jsonc")
    assert menu.l4_budget(scan_dir)[0] == 30                     # 内建:健康 3 > 2,无旗 → 基准 30


# ───────────────────────── sentinel ─────────────────────────


def test_sentinel_thresholds_follow_config(tmp_path, monkeypatch):
    from autoresearch.scan import menu

    _cfg(tmp_path, monkeypatch, {"sentinel": {"auto_below": 0.10, "consider_below": 0.20}})
    assert menu.sentinel_thresholds() == (0.10, 0.20)
    level, _ = menu._sentinel_verdict(0.04, "range", *menu.sentinel_thresholds())
    assert level != "full"
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", tmp_path / "nope.jsonc")
    assert menu.sentinel_thresholds() == (0.03, 0.05)


# ───────────────────────── l4.rubric ─────────────────────────


def test_rubric_bands_and_force_full_follow_config(tmp_path, monkeypatch):
    from autoresearch.scan.l4 import rubric

    _cfg(tmp_path, monkeypatch, {"l4": {"rubric": {"rating_bands": {"Buy": 1},
                                                    "force_full": {"conviction_min": 50}}}})
    dims = {d: "中" for d in rubric._RUBRIC_DIMS}
    dims[rubric._RUBRIC_DIMS[0]] = "强"
    gates = {g: True for g in rubric._OW_GATES}
    assert rubric.rubric_rating(dims, gates)[0] == "Buy"      # 净分 +1 ≥ 配置的 Buy 档 1
    assert rubric.force_full_card({"conviction": 60, "n_channels": 4}) is True
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", tmp_path / "nope.jsonc")
    assert rubric.rubric_rating(dims, gates)[0] == "Hold"
    assert rubric.force_full_card({"conviction": 60, "n_channels": 4}) is False


# ───────────────────────── l4.brief / l4.producers / l4.slim ─────────────────────────


def test_brief_windows_follow_config(tmp_path, monkeypatch):
    from autoresearch.scan import dossier
    from autoresearch.scan.l4 import prompts

    _cfg(tmp_path, monkeypatch, {"l4": {"brief": {"dossier_days": 3, "echo_lookback_days": 2}}})
    assert dossier.configured_max_days() == 3
    assert prompts.configured_echo_lookback() == 2
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", tmp_path / "nope.jsonc")
    assert dossier.configured_max_days() == 10 and prompts.configured_echo_lookback() == 5


def test_producer_windows_follow_config(tmp_path, monkeypatch):
    from autoresearch.scan.l4 import producers

    _cfg(tmp_path, monkeypatch, {"l4": {"producers": {"lhb_window_days": 9, "consensus_min_days": 4}}})
    p = producers.producers_cfg()
    assert p["lhb_window_days"] == 9 and p["consensus_min_days"] == 4
    assert p["pledge_reuse_days"] == 7 and p["lhb_reuse_days"] == 7 and p["lhb_tail"] == 15
    assert p["consensus_window_days"] == 30


def test_slim_min_bytes_follows_config_and_reaches_the_prompt(tmp_path, monkeypatch):
    from autoresearch.scan.l4 import producers, prompts

    _cfg(tmp_path, monkeypatch, {"l4": {"slim": {"min_bytes": 2048}}})
    assert producers.slim_min_bytes() == 2048
    assert "2KB" in prompts.slim_hint()
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", tmp_path / "nope.jsonc")
    assert producers.slim_min_bytes() == 4096 and "4KB" in prompts.slim_hint()


# ───────────────────────── l4.review ─────────────────────────


def test_review_trigger_and_escalation_follow_config(tmp_path, monkeypatch):
    from autoresearch.scan import decision_finalize as df

    _cfg(tmp_path, monkeypatch, {"l4": {"review": {"ow_ratings": ["Hold"], "sell_review_pinned_only": False,
                                                    "spread_escalate": 1}}})
    assert df.review_trigger("Hold", "HOLD", pinned=False) == "ow_review"
    assert df.review_trigger("Underweight", "SELL", pinned=False) == "sell_review"
    assert df._ensemble_flag({"spread": 1}) is True
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", tmp_path / "nope.jsonc")
    assert df.review_trigger("Hold", "HOLD", pinned=False) is None
    assert df.review_trigger("Overweight", "HOLD", pinned=False) == "ow_review"
    assert df.review_trigger("Underweight", "SELL", pinned=False) is None
    assert df.review_trigger("Underweight", "SELL", pinned=True) == "sell_review"
    assert df._ensemble_flag({"spread": 1}) is False and df._ensemble_flag({"spread": 2}) is True


# ───────────────────────── l4_intel ─────────────────────────


def test_intel_caps_and_windows_follow_config(tmp_path, monkeypatch):
    from autoresearch.scan.l4 import intel_gate, intel_guard, intel_status

    _cfg(tmp_path, monkeypatch, {"l4_intel": {"hard_cap": 12, "soft_trim_keep": 4, "stale_gap_days": 2,
                                              "max_attempts": 1, "resume_max_age_s": 60,
                                              "dead_gate": {"cmf_max": -1.5}}})
    assert intel_guard.guard_cfg() == {"hard_cap": 12, "soft_trim_keep": 4}
    assert intel_status.status_cfg() == {"stale_gap_days": 2, "max_attempts": 1, "resume_max_age_s": 60}
    assert dict(intel_gate.dead_gate_factors()) == {"main_inflow_yi": 0.0, "cmf_20": -1.5, "obv_mom_20": 0.0}
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", tmp_path / "nope.jsonc")
    assert intel_guard.guard_cfg() == {"hard_cap": 30, "soft_trim_keep": 10}
    assert intel_status.status_cfg() == {"stale_gap_days": 7, "max_attempts": 3, "resume_max_age_s": 86400}
