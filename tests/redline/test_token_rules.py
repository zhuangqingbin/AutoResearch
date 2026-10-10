"""Token growth guard M1 / M2 / R3 / R13: the edit-time rules R10–R13 and the static BOM.

Each test is a "must turn red" case: delete the rule and it fails.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from autoresearch.common import workspace as ws
from autoresearch.scan import token_bom, token_rules
from autoresearch.scan.user_config import load_user_config


@pytest.fixture
def live_cfg(tmp_path, monkeypatch):
    # empty readout dirs → the BOM calibrates from the two committed seeds
    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path / "reports_claude")
    return load_user_config()


def _with(cfg: dict, block: str, key: str, value) -> dict:
    out = json.loads(json.dumps(cfg))
    out.setdefault(block, {})[key] = value
    return out


# ── M1 / R11 ─────────────────────────────────────────────────────────────────────

def test_bom_under_the_live_config_is_under_both_lines(live_cfg):
    claude, codex = token_bom.estimate("claude", live_cfg), token_bom.estimate("codex", live_cfg)
    assert claude["total"] == pytest.approx(37.1, abs=0.2) and claude["total"] < claude["line"]
    # 10-08: 85 points before the preamble trim; the 10-10 probe measured -5,960 tokens per call
    assert 60 < codex["total"] < 85 and codex["total"] < codex["line"]
    assert all(item["prefix_delta_tokens"] == -5960 for item in codex["roles"].values())
    assert token_rules.lint_bom(live_cfg) == []


def test_doubling_the_card_budget_turns_r11_red_on_both_engines(live_cfg):
    cfg = _with(live_cfg, "l4", "max_cards", 10)
    problems = token_rules.lint_bom(cfg)
    assert {where for _, where, _ in problems} == {"budgets.declared.claude_run_usd",
                                                   "budgets.declared.codex_run_window_points"}


def test_switching_intel_off_drops_its_cost(live_cfg):
    cfg = _with(live_cfg, "l4_intel", "enabled", False)
    assert token_bom.estimate("claude", cfg)["roles"]["scan.l4.intel"]["cost"] == 0


def test_turning_claude_auto_memory_back_on_costs_more(live_cfg):
    cfg = json.loads(json.dumps(live_cfg))
    cfg["session"]["context"]["claude_auto_memory"] = True
    assert token_bom.estimate("claude", cfg)["total"] > token_bom.estimate("claude", live_cfg)["total"]


def test_real_readouts_replace_the_seeds(live_cfg, tmp_path):
    folder = tmp_path / "reports_claude" / "_ops" / "redline"
    folder.mkdir(parents=True)
    seed = next(r for r in token_bom.seeds()["readouts"] if r["engine"] == "claude")
    real = {**seed, "seed": False, "run_id": "20261011T130000000000Z", "usage_status": "MEASURED", "verdict": "PASS",
            "roles": {role: {**slot, "usd": slot["usd"] / 2} for role, slot in seed["roles"].items()}}
    (folder / f"{real['run_id']}.json").write_text(json.dumps(real), encoding="utf-8")
    bom = token_bom.estimate("claude", live_cfg)
    assert bom["sources"] == [{"run_id": real["run_id"], "seed": False, "total": pytest.approx(18.55, abs=0.1)}]


# ── M2 / R10 ─────────────────────────────────────────────────────────────────────

def test_agent_files_need_a_registered_budget_and_must_fit_it(tmp_path):
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "l4-card.md").write_text("x" * 120, encoding="utf-8")
    (agents / "new-role.md").write_text("y" * 10, encoding="utf-8")
    cfg = {"budgets": {"agent_chars": {"l4-card": 100, "gone": 50}}}
    found = {(where, message.split(":")[0][:6]) for _, where, message in
             token_rules.lint_agent_chars(cfg, agents_dir=agents)}
    wheres = {where for where, _ in found}
    assert wheres == {"budgets.agent_chars.l4-card", "budgets.agent_chars.new-role", "budgets.agent_chars.gone"}


def test_the_live_agent_files_fit_their_budgets(live_cfg):
    assert token_rules.lint_agent_chars(live_cfg) == []


# ── R12 tier lock ────────────────────────────────────────────────────────────────

def test_the_live_tiers_match_the_lock(live_cfg):
    assert token_rules.lint_tier_lock(live_cfg) == []


def _lock(tmp_path: Path, cfg: dict) -> Path:
    path = tmp_path / "tier_lock.json"
    token_rules.add_missing(cfg, lock_path=path)
    return path


def test_a_tier_change_without_an_equivalence_readout_is_red(live_cfg, tmp_path):
    path = _lock(tmp_path, live_cfg)
    changed = json.loads(json.dumps(live_cfg))
    changed["agent_engines"]["claude"].setdefault("role_overrides", {}).setdefault("l4_card", {})["effort"] = "high"
    [(rule, key, _)] = token_rules.lint_tier_lock(changed, lock_path=path, repo_root=tmp_path)
    assert (rule, key) == ("R12", "claude:l4_card")
    lock = json.loads(path.read_text(encoding="utf-8"))
    lock["entries"]["claude:l4_card"].append({"model": "claude-opus-5-5", "effort": "high", "since": "2026-10-11",
                                              "equivalence_ref": "docs/research/b1.md"})
    path.write_text(json.dumps(lock), encoding="utf-8")
    missing_ref = token_rules.lint_tier_lock(changed, lock_path=path, repo_root=tmp_path)
    assert [key for _, key, _ in missing_ref] == ["claude:l4_card"]          # the readout file does not exist
    (tmp_path / "docs/research").mkdir(parents=True)
    (tmp_path / "docs/research/b1.md").write_text("EQUIVALENT", encoding="utf-8")
    assert token_rules.lint_tier_lock(changed, lock_path=path, repo_root=tmp_path) == []


def test_a_new_role_or_a_dropped_role_must_be_reflected_in_the_lock(live_cfg, tmp_path):
    path = tmp_path / "tier_lock.json"
    path.write_text(json.dumps({"schema_version": 1, "entries": {"claude:ghost": [{"model": "m", "effort": "e"}]}}),
                    encoding="utf-8")
    keys = {key for _, key, _ in token_rules.lint_tier_lock(live_cfg, lock_path=path, repo_root=tmp_path)}
    assert "claude:ghost" in keys and "claude:l4_card" in keys


# ── R13 only-down ratchet ────────────────────────────────────────────────────────

def test_raising_a_declared_line_above_git_head_is_red(tmp_path):
    from autoresearch.contracts import scan_config as reg

    config = tmp_path / reg.CONFIG_PATH
    config.parent.mkdir(parents=True)
    config.write_text('{ "budgets": { "declared": { "claude_run_usd": 41.0 } } }  // c\n', encoding="utf-8")
    for argv in (["git", "init", "-q"], ["git", "add", "."],
                 ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "x"]):
        subprocess.run(argv, cwd=tmp_path, check=True)
    lower = {"budgets": {"declared": {"claude_run_usd": 38.0}}}
    higher = {"budgets": {"declared": {"claude_run_usd": 45.0}}}
    assert token_rules.lint_declared_ratchet(lower, config, repo_root=tmp_path) == []
    [(rule, where, _)] = token_rules.lint_declared_ratchet(higher, config, repo_root=tmp_path)
    assert (rule, where) == ("R13", "budgets.declared.claude_run_usd")
    assert token_rules.lint_declared_ratchet(higher, tmp_path / "elsewhere.jsonc", repo_root=tmp_path) == []


# ── R14 every role declares its decision consumer ────────────────────────────────

def test_the_live_roles_all_declare_a_known_consumer(live_cfg):
    assert token_rules.lint_role_consumers(live_cfg) == []


def test_a_role_without_or_with_an_unknown_consumer_is_red(live_cfg):
    cfg = json.loads(json.dumps(live_cfg))
    cfg["agents"]["l4_intel"].pop("consumer")
    cfg["agents"]["l4_card"]["consumer"] = "scan.l4.nowhere"
    cfg["agents"]["gp_shell"]["consumer"] = "legacy:.claude/workflows/gone.js"
    wheres = {where for _, where, _ in token_rules.lint_role_consumers(cfg)}
    assert wheres == {"agents.l4_intel.consumer", "agents.l4_card.consumer", "agents.gp_shell.consumer"}
