"""prelude 集成:覆盖池日检步(dossier_pool)。合成,pool.refresh 打桩,零网络。

design: docs/specs/2026-07-03-scan-run-reliability-design.md §2(Task 5,承接 Task 4 覆盖池)
"""
from __future__ import annotations

from autoresearch.scan import prelude
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


# ───────── 季度对账提醒(I-1:reconcile CLI 零调用点零提醒,FN-1 家族) ─────────


def _write_pool(path, codes, status="active"):
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"stocks": {c: {"status": status, "entered": "2026-07-01"} for c in codes},
         "cap": 30}, ensure_ascii=False), encoding="utf-8")
    return path


def test_reconcile_nag_flags_pool_stock_without_period_mark(tmp_path):
    """已建档 active 票 §5 无「季度对账 <period>」→ 一行当日件建议(含可复制命令)。"""
    from tests.dossier.test_delta import _mk_dossier
    _mk_dossier(code="300857")
    pp = _write_pool(tmp_path / "pool.json", ["300857"])
    line = prelude.dossier_reconcile_nag("2026-07-24", pool_path=pp)
    # 2026-07-24 落 5~8 月窗 → 最近应已披露报告期 = 去年年报(_recent_periods 同款滞后)
    assert "📐 季度对账待跑:1 只(period=20251231)" in line
    assert "python -m autoresearch.dossier.reconcile 20251231" in line


def test_reconcile_nag_silent_after_reconciled(tmp_path):
    """§5 已有该期对账痕迹 → 不再催(幂等,不制造常驻噪声)。"""
    from autoresearch.dossier import delta, schema
    from tests.dossier.test_delta import _mk_dossier
    p = _mk_dossier(code="300857")
    text = p.read_text(encoding="utf-8")
    body5 = delta.section_body(text, 4)
    p.write_text(delta.replace_section(text, 4, body5 + "- **季度对账 20251231**(2026-07-24 记)\n"),
                 encoding="utf-8")
    assert schema.lint_dossier(p.read_text(encoding="utf-8")) == []
    pp = _write_pool(tmp_path / "pool.json", ["300857"])
    assert prelude.dossier_reconcile_nag("2026-07-24", pool_path=pp) == ""


def test_reconcile_nag_presence_gated(tmp_path):
    """池空 / 未建档 / 未首覆 / 已退池 → 全部静默(presence-gated,不空催)。"""
    from tests.dossier.test_delta import _mk_dossier
    assert prelude.dossier_reconcile_nag("2026-07-24",
                                         pool_path=tmp_path / "nope.json") == ""
    pp = _write_pool(tmp_path / "p1.json", ["300857"])          # 池里有但无档案
    assert prelude.dossier_reconcile_nag("2026-07-24", pool_path=pp) == ""
    _mk_dossier(code="600000", initiated=False)                 # 骨架票(未首覆)
    pp2 = _write_pool(tmp_path / "p2.json", ["600000"])
    assert prelude.dossier_reconcile_nag("2026-07-24", pool_path=pp2) == ""
    _mk_dossier(code="002371")                                  # 已首覆但已退池
    pp3 = _write_pool(tmp_path / "p3.json", ["002371"], status="retired")
    assert prelude.dossier_reconcile_nag("2026-07-24", pool_path=pp3) == ""


def test_prelude_dossier_pool_note_carries_reconcile_nag(tmp_path, monkeypatch):
    """接线:提醒真的出现在 prelude 汇总的 dossier_pool 行里(生产者必须被消费)。"""
    from tests.dossier.test_delta import _mk_dossier
    monkeypatch.chdir(tmp_path)
    _mk_dossier(code="300857")
    _write_pool(tmp_path / "context" / "knowledge" / "coverage_pool.json", ["300857"])

    def fake_refresh(today, **kw):
        return {"entered": [], "retired": [], "revived": [],
                "pending_init": [], "n_active": 1}
    monkeypatch.setattr("autoresearch.dossier.pool.refresh", fake_refresh)

    results = run_prelude("2026-07-24", skip=_SKIP_ALL_BUT_DOSSIER_POOL)
    note = next(r for r in results if r["step"] == "dossier_pool")["note"]
    assert "池 1 active" in note                       # 原有内容不丢
    assert "📐 季度对账待跑:1 只(period=20251231)" in note
