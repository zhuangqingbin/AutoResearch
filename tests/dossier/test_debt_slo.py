"""档案欠账 SLO(Wave10 A10):三条稳态判据 / 两本账不混数 / 不判≠通过。合成,无网络。"""
from __future__ import annotations

import json

import pytest

from autoresearch.dossier import debt_slo


@pytest.fixture
def pool_file(tmp_path):
    def _write(stocks: dict) -> str:
        path = tmp_path / "pool.json"
        path.write_text(json.dumps({"stocks": stocks, "cap": 30, "as_of": "2026-08-01"}),
                        encoding="utf-8")
        return str(path)
    return _write


def _stock(entered, status="active"):
    return {"name": "", "status": status, "entered": entered,
            "entry_reason": "test", "last_selected": None}


@pytest.fixture
def no_dossiers(monkeypatch, tmp_path):
    """默认:池里没有任何票建过档 → 全是 pending,零消化。"""
    monkeypatch.setattr(debt_slo, "_built_on", lambda code: None)
    monkeypatch.setattr(debt_slo, "reconcile_overdue",
                        lambda today, pool_path=None: [])
    return tmp_path


# ────────────────────────── 判据 ①:最老等待时长 ──────────────────────────

def test_age_slo_measures_the_oldest_not_the_count(pool_file, no_dossiers, monkeypatch):
    """判据是「最老的一只等了多久」,不是「有几只在等」—— 20 只等 1 天不是问题,
    1 只等 10 天是。"""
    monkeypatch.setattr(debt_slo, "pending_init",
                        lambda pool: ["A", "B"], raising=False)
    from autoresearch.dossier import pool as _pool
    monkeypatch.setattr(_pool, "pending_init", lambda pool: ["A", "B"])

    slo = debt_slo.compute("2026-08-01", pool_path=pool_file({
        "A": _stock("2026-07-31"), "B": _stock("2026-07-20")}))
    assert slo["oldest_pending_code"] == "B"
    assert slo["oldest_pending_age_days"] == 12
    assert slo["meets_age"] is False
    assert slo["pending_n"] == 2


def test_age_slo_passes_inside_48h(pool_file, no_dossiers, monkeypatch):
    from autoresearch.dossier import pool as _pool
    monkeypatch.setattr(_pool, "pending_init", lambda pool: ["A"])
    slo = debt_slo.compute("2026-08-01", pool_path=pool_file({"A": _stock("2026-07-30")}))
    assert slo["oldest_pending_age_days"] == 2 and slo["meets_age"] is True


def test_empty_pending_is_not_a_failure(pool_file, no_dossiers, monkeypatch):
    """没人在等 → ①这条不该拖累总判(此前的「清零」口径正是在这里产生假红)。"""
    from autoresearch.dossier import pool as _pool
    monkeypatch.setattr(_pool, "pending_init", lambda pool: [])
    slo = debt_slo.compute("2026-08-01", pool_path=pool_file({"A": _stock("2026-07-01")}))
    assert slo["pending_n"] == 0 and slo["ok"] is True


# ────────────────────────── 判据 ②:两本账不混数 ──────────────────────────

def test_reconcile_overdue_is_counted_separately_from_pending(pool_file, monkeypatch):
    """「还没建档」与「建过档但没做季度对账」治法和责任人都不同,加在一起没人能行动。"""
    from autoresearch.dossier import pool as _pool
    monkeypatch.setattr(_pool, "pending_init", lambda pool: ["A"])
    monkeypatch.setattr(debt_slo, "_built_on", lambda code: None)
    monkeypatch.setattr(debt_slo, "reconcile_overdue",
                        lambda today, pool_path=None: ["X", "Y", "Z"])

    slo = debt_slo.compute("2026-08-01", pool_path=pool_file({"A": _stock("2026-07-31")}))
    assert slo["pending_n"] == 1
    assert slo["reconcile_overdue_n"] == 3
    assert slo["meets_overdue"] is False
    assert "X" in slo["reconcile_overdue"]          # 名单可行动,不只是个数


# ────────────────────────── 判据 ③:吞吐不欠速 ──────────────────────────

def test_throughput_compares_seven_day_digest_against_intake(pool_file, monkeypatch):
    from autoresearch.dossier import pool as _pool
    monkeypatch.setattr(_pool, "pending_init", lambda pool: [])
    monkeypatch.setattr(debt_slo, "reconcile_overdue",
                        lambda today, pool_path=None: [])
    built = {"A": "2026-07-30", "B": "2026-07-29"}
    monkeypatch.setattr(debt_slo, "_built_on", lambda code: built.get(code))

    slo = debt_slo.compute("2026-08-01", pool_path=pool_file({
        "A": _stock("2026-07-28"), "B": _stock("2026-07-28"),
        "C": _stock("2026-07-29"),
    }))
    assert slo["added_7d"] == 3 and slo["digested_7d"] == 2
    assert slo["meets_throughput"] is False


def test_throughput_passes_when_digest_keeps_up(pool_file, monkeypatch):
    from autoresearch.dossier import pool as _pool
    monkeypatch.setattr(_pool, "pending_init", lambda pool: [])
    monkeypatch.setattr(debt_slo, "reconcile_overdue",
                        lambda today, pool_path=None: [])
    monkeypatch.setattr(debt_slo, "_built_on", lambda code: "2026-07-30")
    slo = debt_slo.compute("2026-08-01", pool_path=pool_file({"A": _stock("2026-07-29")}))
    assert slo["digested_7d"] >= slo["added_7d"] and slo["meets_throughput"] is True


def test_stale_entries_outside_the_window_do_not_count(pool_file, monkeypatch):
    """7 日窗之外的入池/建档都不算 —— 否则这条会随历史越积越"好看"。"""
    from autoresearch.dossier import pool as _pool
    monkeypatch.setattr(_pool, "pending_init", lambda pool: [])
    monkeypatch.setattr(debt_slo, "reconcile_overdue",
                        lambda today, pool_path=None: [])
    monkeypatch.setattr(debt_slo, "_built_on", lambda code: "2026-01-01")
    slo = debt_slo.compute("2026-08-01", pool_path=pool_file({"A": _stock("2026-01-01")}))
    assert slo["added_7d"] == 0 and slo["digested_7d"] == 0


# ────────────────────────── 渲染 / 不判≠通过 ──────────────────────────

def test_render_says_steady_state_not_zero(pool_file, no_dossiers, monkeypatch):
    from autoresearch.dossier import pool as _pool
    monkeypatch.setattr(_pool, "pending_init", lambda pool: [])
    line = debt_slo.render(debt_slo.compute("2026-08-01", pool_path=pool_file({})))
    assert "清零不是目标,稳态才是" in line
    assert "帽 ≤3/晚" in line


def test_render_marks_each_failing_criterion(pool_file, monkeypatch):
    from autoresearch.dossier import pool as _pool
    monkeypatch.setattr(_pool, "pending_init", lambda pool: ["A"])
    monkeypatch.setattr(debt_slo, "_built_on", lambda code: None)
    monkeypatch.setattr(debt_slo, "reconcile_overdue",
                        lambda today, pool_path=None: ["X"])
    line = debt_slo.render(debt_slo.compute(
        "2026-08-01", pool_path=pool_file({"A": _stock("2026-07-01")})))
    assert line.startswith("🚨")
    assert line.count("🚨") >= 3          # 总判 + 至少两条分项各自标红


def test_unparseable_entered_date_yields_unknown_not_pass(pool_file, monkeypatch):
    """日期畸形 → 不判(None),**不算通过** —— 不判和通过是两件事。"""
    from autoresearch.dossier import pool as _pool
    monkeypatch.setattr(_pool, "pending_init", lambda pool: ["A"])
    monkeypatch.setattr(debt_slo, "_built_on", lambda code: None)
    monkeypatch.setattr(debt_slo, "reconcile_overdue",
                        lambda today, pool_path=None: [])
    slo = debt_slo.compute("2026-08-01", pool_path=pool_file({"A": _stock("坏日期")}))
    assert slo["meets_age"] is None and slo["ok"] is None
    assert "—" in debt_slo.render(slo)


def test_cap_is_unchanged():
    """§A10:帽 ≤3/晚不变 —— SLO 是改成功判据,不是改吞吐上限。"""
    assert debt_slo.NIGHTLY_CAP == 3
    assert debt_slo.MAX_PENDING_AGE_DAYS == 2
    assert debt_slo.THROUGHPUT_WINDOW_DAYS == 7
