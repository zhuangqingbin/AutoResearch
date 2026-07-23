"""prelude 集成:覆盖池日检步(dossier_pool)。合成,pool.refresh 打桩,零网络。

design: docs/specs/2026-07-03-scan-run-reliability-design.md §2(Task 5,承接 Task 4 覆盖池)
"""
from __future__ import annotations

from autoresearch.scan.prelude import run_prelude

# all_steps 除 dossier_pool 外的全部步名——从源码 autoresearch/scan/prelude.py:run_prelude
# 现场抄录(2026-07-23,11 个):retro_refresh/retro_pending/t1_pending/learning_health/
# consensus/temperature/universe/calendar/catalyst/menu/ledgers。
_SKIP_ALL_BUT_DOSSIER_POOL = ("retro_refresh", "retro_pending", "t1_pending", "learning_health",
                              "consensus", "temperature", "universe", "calendar", "catalyst",
                              "menu", "ledgers")


def test_prelude_has_dossier_pool_step(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)   # 隔离 _write_t0 落盘
    calls = {}

    def fake_refresh(today, **kw):
        calls["today"] = today
        return {"entered": ["300857"], "retired": [], "revived": [],
                "pending_init": ["300857"], "n_active": 1}
    monkeypatch.setattr("autoresearch.dossier.pool.refresh", fake_refresh)

    results = run_prelude("2026-07-23", skip=_SKIP_ALL_BUT_DOSSIER_POOL)
    row = next(r for r in results if r["step"] == "dossier_pool")
    assert calls["today"] == "2026-07-23"
    assert row["ok"] is True
    assert "300857" in row["note"]
    assert "池 1 active" in row["note"]
    assert "进1退0复0" in row["note"]
    assert "待建档 1 只(300857)" in row["note"]


def test_prelude_dossier_pool_no_movement_short_form(tmp_path, monkeypatch):
    """entered/retired/revived 全空 → 短形「无变动」,且待建档 0 不带括号列表。"""
    monkeypatch.chdir(tmp_path)

    def fake_refresh(today, **kw):
        return {"entered": [], "retired": [], "revived": [],
                "pending_init": [], "n_active": 5}
    monkeypatch.setattr("autoresearch.dossier.pool.refresh", fake_refresh)

    results = run_prelude("2026-07-23", skip=_SKIP_ALL_BUT_DOSSIER_POOL)
    row = next(r for r in results if r["step"] == "dossier_pool")
    assert row["ok"] is True
    assert "池 5 active" in row["note"]
    assert "无变动" in row["note"]
    assert "待建档 0" in row["note"]
    assert "进0退0复0" not in row["note"]


def test_prelude_dossier_pool_step_failure_does_not_block(tmp_path, monkeypatch):
    """pool.refresh 抛异常 → 本步 ok=False 但不阻断 run_prelude 本身(既有铁律:各步失败不连坐)。"""
    monkeypatch.chdir(tmp_path)

    def boom(today, **kw):
        raise RuntimeError("池损坏")
    monkeypatch.setattr("autoresearch.dossier.pool.refresh", boom)

    results = run_prelude("2026-07-23", skip=_SKIP_ALL_BUT_DOSSIER_POOL)
    row = next(r for r in results if r["step"] == "dossier_pool")
    assert row["ok"] is False
    assert "池损坏" in row["note"]


def test_prelude_dossier_pool_skip_still_works(tmp_path, monkeypatch):
    """skip 机制对新步名生效:skip=("dossier_pool",) → 结果里没有该步(其余步也全 skip,只验缺席)。"""
    monkeypatch.chdir(tmp_path)   # 隔离 _write_t0 落盘
    all_names = _SKIP_ALL_BUT_DOSSIER_POOL + ("dossier_pool",)
    results = run_prelude("2026-07-23", skip=all_names)
    assert results == []
