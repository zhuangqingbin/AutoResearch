from pathlib import Path

import tomllib

from autoresearch.scan import user_config as uc

ROOT = Path(__file__).resolve().parents[1]


def test_codex_project_agents_cover_roles_and_match_resolved_profile():
    cfg = uc.load_user_config(ROOT / ".claude/skills/scan-market/scan_config.jsonc")
    resolved = uc.resolve_agent_config(cfg, engine="codex")
    paths = sorted((ROOT / ".codex/agents").glob("*.toml"))
    assert {p.stem for p in paths} == set(uc._AGENT_ROLES)
    for path in paths:
        doc = tomllib.loads(path.read_text(encoding="utf-8"))
        spec = resolved[path.stem]
        assert doc["name"] and doc["description"] and doc["developer_instructions"]
        assert doc["model"] == spec["model"]
        assert doc["model_reasoning_effort"] == spec["reasoning_effort"]
    intel = tomllib.loads((ROOT / ".codex/agents/l4_intel.toml").read_text(encoding="utf-8"))
    assert "live web search" in intel["developer_instructions"]
