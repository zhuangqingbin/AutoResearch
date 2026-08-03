"""consensus 自动预注册单测 —— §4.5 的触发条款 + 「会变的量」断言。

§0.3-4 的判例:权重自动重标定连续 4 次 NO-OP,闭环唯一自动腿空转两周无人察觉。
所以自动的腿必须有一个**会变的量**做断言,否则它死了也像活着。
"""
from __future__ import annotations

import json

import pytest

from autoresearch.research import consensus as cs


def _seed_status(tmp_path, snapshots):
    path = cs._history_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for snap in snapshots:
            fh.write(json.dumps(snap, ensure_ascii=False) + "\n")


# ── 触发条款:三条缺一不可 ──────────────────────────────────────


def test_thin_history_holds(tmp_path):
    trigger = cs.prereg_trigger(0.05, 0.04, 0.06, tmp_path)
    assert trigger["status"] == "HOLD"
    assert any("n_days" in r for r in trigger["unmet"])


def test_weak_ic_holds(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "status", lambda _r=None: {"n_days": 80})
    trigger = cs.prereg_trigger(0.01, 0.01, 0.01, tmp_path)
    assert trigger["status"] == "HOLD"
    assert any("|IC|" in r for r in trigger["unmet"])


def test_opposite_half_signs_hold(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "status", lambda _r=None: {"n_days": 80})
    trigger = cs.prereg_trigger(0.05, 0.08, -0.02, tmp_path)
    assert trigger["status"] == "HOLD"
    assert any("反号" in r for r in trigger["unmet"])


def test_missing_ic_holds(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "status", lambda _r=None: {"n_days": 80})
    trigger = cs.prereg_trigger(None, None, None, tmp_path)
    assert trigger["status"] == "HOLD"
    assert any("IC 缺失" in r for r in trigger["unmet"])


def test_all_three_conditions_trigger(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "status", lambda _r=None: {"n_days": 80})
    trigger = cs.prereg_trigger(0.05, 0.04, 0.06, tmp_path)
    assert trigger["status"] == "TRIGGERED" and trigger["unmet"] == []


def test_conditions_are_written_down_not_ad_hoc(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "status", lambda _r=None: {"n_days": 80})
    conditions = cs.prereg_trigger(0.05, 0.04, 0.06, tmp_path)["conditions"]
    assert conditions == {"min_days": 60, "min_abs_ic": 0.02, "halves_same_sign": True}


def test_trigger_only_preregisters_never_activates(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "status", lambda _r=None: {"n_days": 80})
    trigger = cs.prereg_trigger(0.05, 0.04, 0.06, tmp_path)
    assert trigger["human_approval_required"] is True
    assert "注册 ≠ 激活" in trigger["note"]


# ── 「会变的量」断言 ────────────────────────────────────────────


def test_growth_assertion_needs_two_points(tmp_path):
    assert cs.assert_still_growing(tmp_path)["status"] == "INSUFFICIENT_HISTORY"
    _seed_status(tmp_path, [{"date": "2026-08-01", "n_days": 10}])
    result = cs.assert_still_growing(tmp_path)
    assert result["status"] == "INSUFFICIENT_HISTORY"
    assert "不是" in result["note"]           # 判不出 ≠ 没增长


def test_growth_assertion_detects_a_dead_leg(tmp_path):
    """n 不增长 = 这条腿可能已经死了 —— 必须被渲染出来。"""
    _seed_status(tmp_path, [{"date": "2026-07-28", "n_days": 42},
                            {"date": "2026-08-04", "n_days": 42}])
    result = cs.assert_still_growing(tmp_path)
    assert result["status"] == "STALLED" and result["delta"] == 0
    assert "空转两周" in result["note"]


def test_growth_assertion_passes_when_it_grows(tmp_path):
    _seed_status(tmp_path, [{"date": "2026-07-28", "n_days": 42},
                            {"date": "2026-08-04", "n_days": 47}])
    result = cs.assert_still_growing(tmp_path)
    assert result["status"] == "GROWING" and result["delta"] == 5


def test_same_day_snapshots_cannot_prove_growth(tmp_path):
    _seed_status(tmp_path, [{"date": "2026-08-04", "n_days": 42},
                            {"date": "2026-08-04", "n_days": 42}])
    assert cs.assert_still_growing(tmp_path)["status"] == "INSUFFICIENT_HISTORY"


def test_growth_assertion_rides_along_with_the_trigger(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "status", lambda _r=None: {"n_days": 80})
    _seed_status(tmp_path, [{"date": "2026-07-28", "n_days": 70},
                            {"date": "2026-08-04", "n_days": 70}])
    trigger = cs.prereg_trigger(0.05, 0.04, 0.06, tmp_path)
    assert trigger["growth_assertion"]["status"] == "STALLED"


def test_record_status_appends(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "status",
                        lambda _r=None: {"n_days": 5, "n_stocks": 9, "last": "20260804"})
    cs.record_status("2026-08-04", tmp_path)
    cs.record_status("2026-08-05", tmp_path)
    assert len(cs.read_status_history(tmp_path)) == 2


def test_corrupt_history_lines_are_skipped(tmp_path):
    path = cs._history_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"date": "2026-08-01", "n_days": 1}\n{ nope\n', encoding="utf-8")
    assert len(cs.read_status_history(tmp_path)) == 1


# ── spec 生成 ──────────────────────────────────────────────────


def test_hold_refuses_to_build_a_half_spec(tmp_path):
    trigger = cs.prereg_trigger(0.001, 0.001, 0.001, tmp_path)
    with pytest.raises(ValueError, match="未触发"):
        cs.build_spec(trigger, start="2026-08-04", expires="2026-12-31")


def test_triggered_spec_registers_cleanly(tmp_path, monkeypatch):
    from autoresearch.learning import experiment_registry as reg

    monkeypatch.setattr(cs, "status", lambda _r=None: {"n_days": 80})
    trigger = cs.prereg_trigger(0.05, 0.04, 0.06, tmp_path)
    spec = cs.build_spec(trigger, start="2026-08-04", expires="2026-12-31")

    path = tmp_path / "registry.json"
    reg.set_stable_baseline(path, name="base", pointer="p", approved_by="qa",
                            content_hash="a" * 64)
    record = reg.register_experiment(path, spec)
    assert record["status"] == "PREREGISTERED"
    assert record["definition"]["min_units"] == cs.PREREG_MIN_DAYS


def test_render_trigger_surfaces_the_growth_assertion(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "status", lambda _r=None: {"n_days": 80})
    _seed_status(tmp_path, [{"date": "2026-07-28", "n_days": 70},
                            {"date": "2026-08-04", "n_days": 70}])
    md = cs.render_trigger(cs.prereg_trigger(0.05, 0.04, 0.06, tmp_path))
    assert "STALLED" in md and "TRIGGERED" in md
