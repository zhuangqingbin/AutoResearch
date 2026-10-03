"""配置标准 P0:「写进 scan_config.jsonc 的值必须真的到达消费点」(2026-09-27 审计逮到的分裂脑)。

每条用例对应审计里一处「生效但不按预期」:改值 → 消费点读到 → 还原。没有这些锁,
config 值只烤进契约、真消费者用代码默认的病会静默复发。
"""
from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import pytest

from autoresearch.contracts import scan_config as reg

REPO = Path(__file__).resolve().parents[2]


def _write_cfg(tmp_path, monkeypatch, cfg: dict) -> Path:
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", p)
    return p


# ───────────────────────── pinned.cap / ttl_days:分裂脑 ─────────────────────────


def test_load_pinned_without_explicit_cap_reads_the_config_cap(tmp_path, monkeypatch):
    """universe / l3 merge / l3 prompt 调 `load_pinned(date, path=…)` 不传 cap —— 它们拿到的
    必须是 config 的 cap,而不是代码默认 5。"""
    from autoresearch.scan.user_config import load_pinned

    _write_cfg(tmp_path, monkeypatch, {"pinned": {"cap": 1, "ttl_days": 10}})
    pinned = tmp_path / "pinned.jsonc"
    pinned.write_text(json.dumps([{"code": "000001"}, {"code": "000002"}]), encoding="utf-8")

    out = load_pinned("2026-09-27", path=pinned)
    assert [e["code"] for e in out["kept"]] == ["000001"]


def test_load_pinned_without_explicit_ttl_reads_the_config_ttl(tmp_path, monkeypatch):
    from autoresearch.scan.user_config import load_pinned

    _write_cfg(tmp_path, monkeypatch, {"pinned": {"cap": 5, "ttl_days": 1}})
    pinned = tmp_path / "pinned.jsonc"
    pinned.write_text(json.dumps([{"code": "000001", "added": "2026-09-24"}]), encoding="utf-8")

    out = load_pinned("2026-09-27", path=pinned)
    assert out["kept"] == [] and [e["code"] for e in out["expired"]] == ["000001"]


def test_load_pinned_explicit_cap_still_wins(tmp_path, monkeypatch):
    from autoresearch.scan.user_config import load_pinned

    _write_cfg(tmp_path, monkeypatch, {"pinned": {"cap": 1, "ttl_days": 10}})
    pinned = tmp_path / "pinned.jsonc"
    pinned.write_text(json.dumps([{"code": "000001"}, {"code": "000002"}]), encoding="utf-8")

    assert len(load_pinned("2026-09-27", path=pinned, cap=2)["kept"]) == 2


# ───────────────────────── budgets.concurrency:两个 web 帽零执行点;缺省三处不一 ─────────────────────────


def test_concurrency_caps_are_tushare_and_l4_stock_only():
    from autoresearch.scan import l4_tasks

    assert l4_tasks.REQUIRED_CAPS == tuple(reg.DEFAULT_CONCURRENCY)
    assert l4_tasks._normalize_caps({"tushare": 4, "l4_stock": 64}) == {"tushare": 4, "l4_stock": 64}
    with pytest.raises(ValueError, match="extra"):
        l4_tasks._normalize_caps({"tushare": 4, "web_search": 4, "web_fetch": 4, "l4_stock": 64})


def test_concurrency_defaults_are_one_value_in_three_places(tmp_path, monkeypatch):
    from autoresearch.scan import budget, l4_tasks
    from autoresearch.session_agent import mailbox_cli

    assert l4_tasks.DEFAULT_CAPS == reg.DEFAULT_CONCURRENCY
    assert budget.DEFAULT_BUDGETS["concurrency"] == reg.DEFAULT_CONCURRENCY
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", tmp_path / "nope.jsonc")
    assert mailbox_cli.resolve_max_parallel(object(), None) == reg.DEFAULT_CONCURRENCY["l4_stock"]


def test_session_agent_l4_prepare_passes_config_caps_into_the_taskbook():
    """Python 宿主建任务簿时必须把 budgets.concurrency 传进去(此前恒用内建缺省)。"""
    src = (REPO / "autoresearch/session_agent/domain_ops.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "attr", getattr(n.func, "id", "")) == "initialize_tickets"]
    assert calls, "domain_ops 里找不到 initialize_tickets 调用"
    assert all(any(kw.arg == "caps" for kw in c.keywords) for c in calls)


# ───────────────────────── l4_intel.max_queries:四处缺省 ─────────────────────────


def test_intel_max_queries_default_is_one_constant_everywhere():
    from autoresearch.scan import self_review
    from autoresearch.scan.l4 import intel_guard
    from autoresearch.session_agent import dispatch

    assert intel_guard.DEFAULT_MAX_QUERIES == reg.DEFAULT_INTEL_MAX_QUERIES
    assert inspect.signature(self_review.intel_query_cap_lint).parameters["cap"].default \
        == reg.DEFAULT_INTEL_MAX_QUERIES
    assert dispatch.intel_prompt_cap({}) == reg.DEFAULT_INTEL_MAX_QUERIES
    assert dispatch.intel_prompt_cap({"intel_max_queries": 7}) == 7


# ───────────────────────── sector.reuse_ttl_days:Python 宿主不传 ─────────────────────────


def test_session_agent_passes_reuse_ttl_from_config():
    src = (REPO / "autoresearch/session_agent/domain_ops.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "attr", getattr(n.func, "id", "")) == "find_reusable"]
    assert len(calls) >= 2, "domain_ops 里 find_reusable 调用点少于两处,审计对象变了"
    assert all(any(kw.arg == "ttl_days" for kw in c.keywords) for c in calls)


# ───────────────────────── sector.max_briefs:不是真上限 ─────────────────────────


def _sector_scan_dir(tmp_path) -> Path:
    d = tmp_path / "scan"
    d.mkdir()
    (d / "sectors.csv").write_text(
        "industry,median_pct_60d\n红一,9\n红二,8\n红三,7\n红四,6\n", encoding="utf-8")
    (d / "market_pack.json").write_text(json.dumps({"sector_healthy_top3": [
        {"industry": "看多A"}, {"industry": "看多B"}, {"industry": "看多C"}]}), encoding="utf-8")
    return d


def test_max_briefs_is_a_hard_cap_when_healthy_top3_extra_is_off(tmp_path):
    from autoresearch.sector.pack import select_briefing_sectors

    d = _sector_scan_dir(tmp_path)
    inds, prov = select_briefing_sectors(d, k=2, wl_path=tmp_path / "none.csv", healthy_top3_extra=False)
    assert len(inds) == 2 and inds == ["红一", "红二"]


def test_healthy_top3_extra_on_keeps_the_legacy_overflow(tmp_path):
    from autoresearch.sector.pack import select_briefing_sectors

    d = _sector_scan_dir(tmp_path)
    inds, prov = select_briefing_sectors(d, k=2, wl_path=tmp_path / "none.csv", healthy_top3_extra=True)
    assert inds == ["红一", "红二", "看多A", "看多B", "看多C"]
    assert {prov[i] for i in inds[2:]} == {"top3看多"}


def test_healthy_top3_extra_reads_config_when_not_passed(tmp_path, monkeypatch):
    from autoresearch.sector.pack import select_briefing_sectors

    _write_cfg(tmp_path, monkeypatch, {"sector": {"healthy_top3_extra": False}})
    d = _sector_scan_dir(tmp_path)
    inds, _ = select_briefing_sectors(d, k=2, wl_path=tmp_path / "none.csv")
    assert len(inds) == 2


# ───────────────────────── relative_buy.activate_date:死键 ─────────────────────────


def test_activate_date_is_retired(tmp_path, monkeypatch):
    from autoresearch.scan.user_config import load_user_config

    p = _write_cfg(tmp_path, monkeypatch, {"relative_buy": {"activate_date": "2026-08-19"}})
    with pytest.raises(ValueError, match="未知子键"):
        load_user_config(p)


def test_configured_relative_buy_is_a_four_tuple(tmp_path, monkeypatch):
    from autoresearch.scan.relative_buy import configured_relative_buy

    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", tmp_path / "nope.jsonc")
    assert configured_relative_buy() == ("shadow", False, "finalists", False)


# ───────────────────────── preference_weights 十组名:契约层单源 ─────────────────────────


def test_preference_groups_live_in_the_contract_and_scoring_reads_them():
    from autoresearch.common import scoring

    assert scoring._GROUPS is reg.PREFERENCE_GROUPS
    assert len(reg.PREFERENCE_GROUPS) == 10
