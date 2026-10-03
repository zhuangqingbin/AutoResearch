"""agent 定义 frontmatter 的 model / effort 与 scan_config 同源(2026-10-03 钉版)。

背景:mailbox 执行器由宿主的 Agent 工具派发,模型与 effort 取自 `.claude/agents/<x>.md` 的
frontmatter(Agent 工具的 `model` 入参只接受别名);headless 执行器取自 `scan_config` 解析值。
两处各写一遍又没有任何东西比对它们,2026-09-22 `opus` 别名换代时就是从这条缝漏过去的。
"""
from __future__ import annotations

import copy
import shutil
from pathlib import Path

import pytest

from autoresearch.scan import agent_frontmatter as af, user_config as uc
from autoresearch.trace.pricing import KNOWN_MODEL_IDS

ROOT = Path(__file__).resolve().parents[2]
PROD = ROOT / ".claude" / "skills" / "scan-market" / "scan_config.jsonc"
AGENTS = ROOT / ".claude" / "agents"
RELAY = {"gp_shell", "gp_shell_json"}


def _prod() -> dict:
    return uc.load_user_config(PROD)


def _copy_agents(tmp_path: Path) -> Path:
    target = tmp_path / "agents"
    shutil.copytree(AGENTS, target)
    return target


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{path.name}: 夹具前提不成立,frontmatter 里没有 {old!r}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_production_frontmatter_is_in_sync_with_scan_config():
    assert af.check(_prod(), agents_dir=AGENTS) == []


def test_every_claude_inference_role_is_pinned_to_a_priced_full_id():
    resolved = uc.resolve_agent_config(_prod(), engine="claude")
    for role, spec in sorted(resolved.items()):
        if role in RELAY:
            continue
        assert spec.get("model") in KNOWN_MODEL_IDS, (
            f"{role}: model={spec.get('model')!r} 不是价表认识的全 ID —— 别名由 Claude Code "
            "客户端解析,会随自动升级移动")


def test_every_agent_definition_is_owned_by_a_configured_role():
    assert {p.stem for p in AGENTS.glob("*.md")} == set(af.expected(_prod()))


def test_intel_roles_stay_on_sonnet_and_judgement_roles_on_opus():
    want = af.expected(_prod())
    assert want["l4-card"] == {"model": "claude-opus-5-5", "effort": "max", "maxTurns": "80",
                               "omitClaudeMd": "true", "roles": ["l4_card", "ens_review"]}
    assert want["l4-intel"]["model"] == "claude-sonnet-5-5"
    assert want["sector-brief"] == {"model": "claude-opus-5-5", "effort": "xhigh", "maxTurns": "30",
                                    "omitClaudeMd": "true", "roles": ["sector_brief"]}
    assert want["l3-repair"]["effort"] == "medium"


def test_alias_in_frontmatter_is_a_violation(tmp_path):
    agents = _copy_agents(tmp_path)
    _edit(agents / "l4-card.md", "model: claude-opus-5-5", "model: opus")
    assert af.check(_prod(), agents_dir=agents) == [
        "l4-card.md: model 是 'opus',scan_config 解析为 'claude-opus-5-5'(role l4_card)"]


def test_effort_drift_is_a_violation(tmp_path):
    agents = _copy_agents(tmp_path)
    _edit(agents / "l4-intel.md", "effort: max", "effort: high")
    assert af.check(_prod(), agents_dir=agents) == [
        "l4-intel.md: effort 是 'high',scan_config 解析为 'max'(role l4_intel)"]


def test_roles_sharing_one_definition_must_resolve_identically():
    cfg = copy.deepcopy(_prod())
    cfg["agent_engines"]["claude"]["role_overrides"]["ens_review"] = {"effort": "xhigh"}
    violations = af.check(cfg, agents_dir=AGENTS)
    assert violations == [
        "l4-card.md: 共用该定义的 role 解析结果不同(l4_card={'model': 'claude-opus-5-5', "
        "'effort': 'max'} / ens_review={'model': 'claude-opus-5-5', 'effort': 'xhigh'});"
        "mailbox 执行器只读 frontmatter,分不开"]


def test_role_without_a_pinned_model_is_a_violation():
    cfg = copy.deepcopy(_prod())
    del cfg["agent_engines"]["claude"]["tiers"]["repair"]["model"]
    assert af.check(cfg, agents_dir=AGENTS) == [
        "l3-repair.md: scan_config 没有给 role l3_repair 钉模型(agent_engines.claude)"]


def test_orphan_agent_definition_is_a_violation(tmp_path):
    agents = _copy_agents(tmp_path)
    shutil.copy(agents / "l4-card.md", agents / "rogue.md")
    assert af.check(_prod(), agents_dir=agents) == [
        "rogue.md: 没有任何已登记 role 使用该 agent 定义(contracts.agent_roles.PHYSICAL_AGENTS)"]


def test_missing_agent_definition_is_a_violation(tmp_path):
    agents = _copy_agents(tmp_path)
    (agents / "l3-repair.md").unlink()
    assert af.check(_prod(), agents_dir=agents) == ["l3-repair.md: agent 定义缺失"]


def test_write_rewrites_only_the_two_frontmatter_lines(tmp_path):
    agents = _copy_agents(tmp_path)
    original = (agents / "l4-card.md").read_text(encoding="utf-8")
    _edit(agents / "l4-card.md", "model: claude-opus-5-5", "model: opus")
    _edit(agents / "l4-card.md", "effort: max", "effort: low")

    changed = af.write(_prod(), agents_dir=agents)

    assert changed == ["l4-card.md"]
    assert (agents / "l4-card.md").read_text(encoding="utf-8") == original
    assert af.check(_prod(), agents_dir=agents) == []
    assert af.write(_prod(), agents_dir=agents) == []          # 幂等


def test_write_never_touches_a_model_line_in_the_body(tmp_path):
    agents = _copy_agents(tmp_path)
    path = agents / "l3-repair.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nmodel: opus\neffort: low\n",
                    encoding="utf-8")
    _edit(path, "model: claude-opus-5-5", "model: opus")

    af.write(_prod(), agents_dir=agents)

    text = path.read_text(encoding="utf-8")
    assert text.endswith("\nmodel: opus\neffort: low\n")       # 正文原样
    assert text.split("---", 2)[1].count("model: claude-opus-5-5") == 1


@pytest.mark.parametrize("bad", ["claude-opus-9", "claude-opus-5-6", "gpt-5.6-sol", "Opus", ""])
def test_unknown_claude_model_is_rejected_at_config_load(bad):
    cfg = copy.deepcopy(_prod())
    cfg["agent_engines"]["claude"]["tiers"]["critical"]["model"] = bad
    with pytest.raises(ValueError, match="model"):
        uc.validate_user_config(cfg)


def test_alias_is_still_a_legal_config_value_for_non_production_fixtures():
    """别名在**语法上**仍合法(旧夹具、局部编排用);生产配置的钉版由上面两条测试锁。"""
    cfg = copy.deepcopy(_prod())
    cfg["agent_engines"]["claude"]["tiers"]["critical"]["model"] = "opus"
    uc.validate_user_config(cfg)


def test_cli_check_reports_and_exits_nonzero_on_drift(tmp_path, capsys):
    agents = _copy_agents(tmp_path)
    assert af.main(["--check", "--config", str(PROD), "--agents-dir", str(agents)]) == 0
    _edit(agents / "macro-brief.md", "effort: max", "effort: medium")
    assert af.main(["--check", "--config", str(PROD), "--agents-dir", str(agents)]) == 1
    assert "macro-brief.md: effort 是 'medium'" in capsys.readouterr().out
    assert af.main(["--write", "--config", str(PROD), "--agents-dir", str(agents)]) == 0
    assert af.main(["--check", "--config", str(PROD), "--agents-dir", str(agents)]) == 0


# ── Codex 对应物:`.codex/agents/<role>.toml` 的 model / model_reasoning_effort ────────

CODEX = ROOT / ".codex" / "agents"


def _copy_codex(tmp_path: Path) -> Path:
    target = tmp_path / "codex_agents"
    shutil.copytree(CODEX, target)
    return target


def test_production_codex_definitions_are_in_sync_with_scan_config():
    assert af.check_codex(_prod(), agents_dir=CODEX) == []


def test_codex_model_and_effort_drift_are_violations(tmp_path):
    agents = _copy_codex(tmp_path)
    _edit(agents / "l4_card.toml", 'model = "gpt-5.6-sol"', 'model = "gpt-5.6-terra"')
    _edit(agents / "sector_brief.toml", 'model_reasoning_effort = "high"',
          'model_reasoning_effort = "xhigh"')

    got = af.check_codex(_prod(), agents_dir=agents)

    assert "l4_card.toml: model 是 'gpt-5.6-terra',scan_config 解析为 'gpt-5.6-sol'" in got
    assert ("sector_brief.toml: model_reasoning_effort 是 'xhigh',scan_config 解析为 'high'"
            in got)


def test_codex_orphan_and_missing_definitions_are_violations(tmp_path):
    agents = _copy_codex(tmp_path)
    (agents / "l3_rank.toml").unlink()
    (agents / "stray.toml").write_text('name = "stray"\nmodel = "x"\n', encoding="utf-8")

    got = af.check_codex(_prod(), agents_dir=agents)

    assert "l3_rank.toml: agent 定义缺失" in got
    assert any(line.startswith("stray.toml: 没有任何已登记 role") for line in got)


def test_codex_write_rewrites_only_the_header_lines(tmp_path):
    """`developer_instructions` 是多行字符串,里面以 `model… = ` 开头的行也绝不能被改。"""
    agents = _copy_codex(tmp_path)
    path = agents / "l4_card.toml"
    _edit(path, 'model = "gpt-5.6-sol"', 'model = "gpt-5.6-terra"')
    _edit(path, 'model_reasoning_effort = "xhigh"\n', "")          # 头部没有 effort 行
    path.write_text(path.read_text(encoding="utf-8").replace(
        '"""C4', '"""\nmodel_reasoning_effort = "low"\nC4', 1), encoding="utf-8")

    assert af.write_codex(_prod(), agents_dir=agents) == ["l4_card.toml"]

    text = path.read_text(encoding="utf-8")
    assert text.count('model = "gpt-5.6-sol"') == 1
    assert '\nmodel_reasoning_effort = "low"\nC4' in text   # 正文里的那一行原样保留
    # 头部缺的那一行不由 write 补(结构性问题交给 check 报),也绝不改到正文里去。
    assert af.check_codex(_prod(), agents_dir=agents) == [
        "l4_card.toml: model_reasoning_effort 是 None,scan_config 解析为 'xhigh'"]
    assert af.write_codex(_prod(), agents_dir=agents) == []  # 幂等


def test_cli_checks_both_engines_by_default(tmp_path, capsys):
    claude, codex = _copy_agents(tmp_path), _copy_codex(tmp_path)
    args = ["--check", "--config", str(PROD), "--agents-dir", str(claude),
            "--codex-agents-dir", str(codex)]
    assert af.main(args) == 0
    _edit(codex / "strategist.toml", 'model_reasoning_effort = "xhigh"',
          'model_reasoning_effort = "low"')
    assert af.main(args) == 1
    assert "strategist.toml: model_reasoning_effort 是 'low'" in capsys.readouterr().out


# ── B8(2026-10-03):轮数上限与 CLAUDE.md 省略 —— mailbox 派发此前没有任何轮数上限 ────────


@pytest.mark.parametrize("agent,turns", [
    ("l4-card", "80"),        # scan.l4.card / scan.l4.review / stock.card 取最大
    ("l4-intel", "64"),
    ("sector-brief", "30"),
    ("l3-repair", "30"),
    ("macro-brief", "50"),
    ("l3-rank", "64"),
])
def test_max_turns_mirror_the_headless_role_caps(agent, turns):
    assert af.expected(_prod())[agent]["maxTurns"] == turns


def test_a_config_override_moves_the_mirrored_cap():
    cfg = copy.deepcopy(_prod())
    cfg.setdefault("session", {})["max_turns"] = {"sector.brief": 20}
    assert af.expected(cfg)["sector-brief"]["maxTurns"] == "20"


def test_every_research_agent_omits_claude_md():
    """CLAUDE.md 讲的是编排与入口,研究 agent 一个字都用不上;省掉它每个子 agent 约 2.5K token。"""
    assert all(spec["omitClaudeMd"] == "true" for spec in af.expected(_prod()).values())


def test_write_inserts_keys_the_frontmatter_does_not_have_yet(tmp_path):
    agents = _copy_agents(tmp_path)
    path = agents / "l4-intel.md"
    text = path.read_text(encoding="utf-8")
    for key in ("maxTurns", "omitClaudeMd"):
        text = "\n".join(line for line in text.split("\n") if not line.startswith(f"{key}:"))
    path.write_text(text, encoding="utf-8")
    assert any("maxTurns" in line for line in af.check(_prod(), agents_dir=agents))

    assert "l4-intel.md" in af.write(_prod(), agents_dir=agents)

    front = path.read_text(encoding="utf-8").split("\n---", 1)[0]
    assert "\nmaxTurns: 64" in front and "\nomitClaudeMd: true" in front
    assert af.check(_prod(), agents_dir=agents) == []
