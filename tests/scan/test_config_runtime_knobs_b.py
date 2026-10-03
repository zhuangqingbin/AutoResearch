"""P3 扩容 · 运行时键(5b):session_agent / JS 壳 / dossier / observability(2026-09-27)。"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _cfg(tmp_path, monkeypatch, cfg: dict) -> None:
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", p)


def _nocfg(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", tmp_path / "nope.jsonc")


def test_session_cfg_follows_config(tmp_path, monkeypatch):
    from autoresearch.session_agent import config as sc
    from autoresearch.session_agent.executors import base, headless_claude, mailbox

    _cfg(tmp_path, monkeypatch, {"session": {"timeouts": {"mailbox": {"scan.l3": 10}, "fallback_s": 7},
                                             "max_turns": {"scan.l3": 3}, "tier_max_turns": {"relay": 2},
                                             "default_max_turns": 9,
                                             "mailbox": {"never_taken_factor": 2.0, "dead_heartbeats": 1},
                                             "max_attempts": 4, "runner": {"poll_seconds": 1.5}}})
    s = sc.session_cfg()
    assert s["timeouts"]["mailbox"]["scan.l3"] == 10 and s["timeouts"]["mailbox"]["scan.l4.card"] == 1800.0
    assert s["timeouts"]["headless"] == dict(headless_claude.HEADLESS_TIMEOUTS) and s["timeouts"]["fallback_s"] == 7
    assert s["max_turns"]["scan.l3"] == 3 and s["max_turns"]["scan.l4.card"] == 80
    assert s["tier_max_turns"]["relay"] == 2 and s["default_max_turns"] == 9
    assert s["mailbox"] == {"never_taken_factor": 2.0, "wait_s": 90.0, "dead_heartbeats": 1}
    assert s["max_attempts"] == 4 and s["runner"] == {"poll_seconds": 1.5, "max_rounds": 20000, "timeout_multiplier": 1.0}
    _nocfg(tmp_path, monkeypatch)
    s = sc.session_cfg()
    assert s["timeouts"]["mailbox"] == dict(base.DEFAULT_TIMEOUTS) and s["timeouts"]["fallback_s"] == base.FALLBACK_TIMEOUT
    assert s["mailbox"]["never_taken_factor"] == mailbox.NEVER_TAKEN_FACTOR and s["max_attempts"] == 2


def test_js_shells_read_config_with_registry_defaults():
    from autoresearch.contracts import scan_config as reg

    scan_js = (ROOT / ".claude/workflows/scan-market.js").read_text(encoding="utf-8")
    l4_js = (ROOT / ".claude/workflows/l4-stock.js").read_text(encoding="utf-8")
    assert "cfg.shells" in scan_js and "cfg.shells" in l4_js
    for key, js, text in (("detached_max_rounds_scan", scan_js, "scan-market.js"), ("detached_max_rounds_l4", l4_js, "l4-stock.js"),
                          ("wait_seconds", scan_js, "scan-market.js"), ("misses_lost", scan_js, "scan-market.js"),
                          ("trace_calls_per_target", scan_js, "scan-market.js"), ("tail_lines", scan_js, "scan-market.js")):
        entry = reg.find("shells", key)
        assert entry is not None, key
        m = re.search(rf"\b{key}\s*\?\?\s*([\d.]+)", js)
        assert m, f"{text} 未读 shells.{key}"
        assert float(m.group(1)) == float(entry.default), key


def test_dossier_cfg_follows_config(tmp_path, monkeypatch):
    from autoresearch.dossier import config as dc

    _cfg(tmp_path, monkeypatch, {"dossier": {"pool_cap": 3, "entry_min": 1, "stale_days": 7}})
    d = dc.dossier_cfg()
    assert d["pool_cap"] == 3 and d["entry_min"] == 1 and d["stale_days"] == 7
    assert d["recent_days"] == 20 and d["init_per_night"] == 3 and d["max_pending_age_days"] == 2
    assert d["throughput_window_days"] == 7 and d["intel_gap_max_lines"] == 2
    assert d["summary_cap"] == 3000 and d["research_body_cap"] == 12000
    _nocfg(tmp_path, monkeypatch)
    assert dc.dossier_cfg()["pool_cap"] == 30 and dc.dossier_cfg()["stale_days"] == 90


def test_observability_cfg_follows_config(tmp_path, monkeypatch):
    from autoresearch.scan import observability as ob

    _cfg(tmp_path, monkeypatch, {"observability": {"min_ledger_n": 1, "winner_decile": 0.5, "nan_warn": 0.1}})
    o = ob.observability_cfg()
    assert o["min_ledger_n"] == 1 and o["winner_decile"] == 0.5 and o["nan_warn"] == 0.1
    assert o["min_session_n"] == 20 and o["realized_min_n"] == 20
    assert o["swing_readout_min_days"] == 40 and o["swing_readout_min_clusters"] == 10
    assert o["menu_knife_tolerance"] == 0.06 and o["price_claim_tol_pp"] == 1.5 and o["unknown_rate_tolerance"] == 0.05
    _nocfg(tmp_path, monkeypatch)
    assert ob.observability_cfg()["min_ledger_n"] == 20
