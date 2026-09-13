#!/usr/bin/env python3
"""Wave11 B1:agents 子键闭集 —— 拼写错必须 raise,这是 user_config 存在的唯一理由的补全。"""
import json

import pytest

from autoresearch.common import workspace as ws
from autoresearch.scan import user_config as uc


def _load(tmp_path, agents):
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"agents": agents}))
    return uc.load_user_config(p)


def test_unknown_role_raises(tmp_path):
    with pytest.raises(ValueError, match="t1diag"):
        _load(tmp_path, {"t1diag": {"effort": "high"}})          # 少写下划线=经典拼错


def test_known_roles_pass(tmp_path):
    cfg = _load(tmp_path, {"l4_card": {"effort": "max"}, "gp_shell": {"model": "sonnet"}})
    assert cfg["agents"]["l4_card"]["effort"] == "max"


def test_bad_effort_and_bad_subkey_raise(tmp_path):
    with pytest.raises(ValueError, match="effort"):
        _load(tmp_path, {"l4_card": {"effort": "ultra"}})
    with pytest.raises(ValueError, match="temperature"):
        _load(tmp_path, {"l4_card": {"temperature": 0.2}})


def test_bad_model_raises(tmp_path):
    """brief 未列但任务约束 #4 明确要求 model 值也要校验——effort 有反例,model 不能没有。"""
    with pytest.raises(ValueError, match="model"):
        _load(tmp_path, {"l4_card": {"model": "gpt4"}})


@pytest.mark.parametrize("field,bad_value", [
    ("effort", ["max"]),
    ("effort", {"a": 1}),
    ("model", ["opus"]),
    ("model", {"a": 1}),
])
def test_unhashable_field_value_raises_cleanly(tmp_path, field, bad_value):
    """model/effort 的值若是不可哈希类型(list/dict)——同 bug class 的漏网(review
    task-7-review.md「新发现 2」):`spec["effort"] not in _EFFORTS` 在值不可哈希时会
    裸崩 `TypeError: unhashable type`,不是本文件一贯的干净 `ValueError`。

    修法与 spec 本身的 `isinstance(spec, dict)` 同款——做集合成员判断前先校验值是
    `str`,否则带 role 名 + 字段名 + 合法取值集合 raise。
    """
    with pytest.raises(ValueError) as excinfo:
        _load(tmp_path, {"l4_card": {field: bad_value}})
    msg = str(excinfo.value)
    assert "l4_card" in msg
    assert field in msg


@pytest.mark.parametrize("bad_agents", ["oops", True, 5, ["a", "b"]])
def test_agents_not_dict_raises(tmp_path, bad_agents):
    """agents 顶层值本身不是 object(字符串/布尔/数字/列表)→ 干净 ValueError,不是裸 TypeError。

    这条分支(`isinstance(agents, dict)`)在 f387b81 落地时全仓零测试覆盖——补上。
    精确匹配 "必须是 object"(而非只匹配宽松的 "agents")是关键:agents="oops" 时若
    isinstance 分支被拿掉,后续 `set(agents)` 会把字符串拆成单字符集合,落进下一条
    "未知 role" 分支照样抛 ValueError 且消息也含 "agents"——弱匹配会让这条分支的
    变异逃逸(误判"测试还在保护",其实保护的是隔壁分支)。
    """
    with pytest.raises(ValueError, match="必须是 object"):
        _load(tmp_path, bad_agents)


@pytest.mark.parametrize("bad_spec", [True, 5, ["model", "effort"]])
def test_role_spec_non_dict_raises_cleanly(tmp_path, bad_spec):
    """role spec 是真值但不可迭代的标量(bool/int)或碰巧集合运算能过的 list → 干净 ValueError。

    修前实测复现:
      {"gp_shell": true}            → TypeError: 'bool' object is not iterable
      {"l4_card": 5}                → TypeError: 'int' object is not iterable
      {"l4_card": ["model","effort"]} → 集合运算侥幸通过,随后 spec["effort"] 才炸
                                         TypeError: list indices must be integers…
    三者现在必须统一走 `agents.{role} 必须是 object` 的清爽 raise,不再是裸 TypeError。
    """
    with pytest.raises(ValueError, match="l4_card"):
        _load(tmp_path, {"l4_card": bad_spec})


def test_role_spec_none_still_means_use_defaults(tmp_path):
    """spec=None(JSON null)不是本次修复的目标场景——`spec or {}` 早就防住,行为保持不变。"""
    cfg = _load(tmp_path, {"l4_card": None})
    assert cfg["agents"]["l4_card"] is None


# ═══════════ Wave12-T33:resolved agent config(materialize + 四条 fail-fast)═══════════
#
# 这一组的靶子是 2026-07-21 事故:配置真身是 `.jsonc` 却按 `.json` 去找,查无 → 传了空
# config → intel 被静默关掉、全体 agent 掉回缺省 effort,而**报告上看不出来**。
# 所以下面每一条"该 raise"的断言都不是洁癖,是那次事故的结构性防线。

_FULL_AGENTS = {role: {"effort": "high"} for role in sorted(uc._AGENT_ROLES)}


def _full(**over):
    agents = {k: dict(v) for k, v in _FULL_AGENTS.items()}
    agents.update(over)
    return {"agents": agents}


def test_resolved_lists_all_ten_roles():
    resolved = uc.resolve_agent_config(_full())
    assert set(resolved) == uc._AGENT_ROLES
    assert len(resolved) == 10, f"闭集应为 10 role,实际 {len(resolved)}"


def test_resolved_empty_cfg_raises():
    """空 `{}` 一律 raise —— 「什么都没配」不能悄悄全用缺省(07-21 事故的那一半)。"""
    with pytest.raises(ValueError, match="为空"):
        uc.resolve_agent_config({})


def test_resolved_empty_agents_block_raises():
    with pytest.raises(ValueError, match="agents 为空"):
        uc.resolve_agent_config({"funnel": {}})


def test_resolved_unknown_role_raises():
    cfg = _full()
    cfg["agents"]["t1diag"] = {"effort": "high"}      # 拼写错:t1_diag → t1diag
    with pytest.raises(ValueError, match="未知 role"):
        uc.resolve_agent_config(cfg)


def test_resolved_unknown_field_raises():
    with pytest.raises(ValueError, match="未知子键"):
        uc.resolve_agent_config(_full(l4_card={"effort": "max", "temperature": 0.7}))


@pytest.mark.parametrize("bad", [{"effort": "ultra"}, {"model": "gpt"}])
def test_resolved_illegal_enum_raises(bad):
    with pytest.raises(ValueError, match="非法"):
        uc.resolve_agent_config(_full(l4_card={**bad}))


def test_resolved_missing_required_role_raises_and_names_it():
    cfg = _full()
    cfg["agents"].pop("ens_review")
    cfg["agents"].pop("gp_shell_json")
    with pytest.raises(ValueError) as e:
        uc.resolve_agent_config(cfg)
    msg = str(e.value)
    assert "缺生产必填 role" in msg
    assert "ens_review" in msg and "gp_shell_json" in msg, "必须一次报全缺哪几个,别一个一个抛"


def test_resolved_partial_mode_allows_missing_for_local_orchestration():
    """`require_all=False`(局部编排场景;历史唯一场景 scan-retro 拉 t1-review 已于 D3 退役,
    机制原样保留供未来局部编排复用)仍校验写了的、不要求写全。"""
    resolved = uc.resolve_agent_config({"agents": {"l3_repair": {"effort": "max"}}},
                                        require_all=False)
    assert resolved == {"l3_repair": {"effort": "max"}}


def test_resolved_model_key_absent_for_judgement_roles_is_load_bearing():
    """判断类 role **不得**被补出 model 键 —— 补了 workflow 就会显式传 model,
    回退链第三层(agent def frontmatter)从此永远吃不到。那是行为变更,不是重构。"""
    resolved = uc.resolve_agent_config(_full())
    for role in ("l3_rank", "l4_card", "l4_intel", "strategist", "sector_brief",
                 "ens_review", "l3_repair", "dossier_init"):
        assert "model" not in resolved[role], f"{role} 不该有 model 键(要落 frontmatter)"
    for role in ("gp_shell", "gp_shell_json"):
        assert resolved[role]["model"] == "sonnet", "壳类缺省必须钉 sonnet(08-05 事故)"


def test_resolved_config_overrides_fallback():
    resolved = uc.resolve_agent_config(_full(l4_card={"effort": "max"},
                                              gp_shell={"model": "haiku", "effort": "low"}))
    assert resolved["l4_card"] == {"effort": "max"}
    assert resolved["gp_shell"] == {"model": "haiku", "effort": "low"}


def test_materialize_writes_resolved_artifact(tmp_path):
    out = uc.materialize_agent_config("2026-08-09", _full(), root=tmp_path)
    assert out == tmp_path / ws.scan_root() / "2026-08-09" / uc.RESOLVED_FILENAME
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema_version"] == uc.RESOLVED_SCHEMA_VERSION
    assert payload["date"] == "2026-08-09"
    assert set(payload["roles"]) == uc._AGENT_ROLES
    assert uc.load_resolved_agent_config(out.parent) == payload["roles"]


def test_load_resolved_is_presence_gated(tmp_path):
    """缺文件 / 坏文件 → `{}`(由调用方决定要不要炸),绝不抛。"""
    assert uc.load_resolved_agent_config(tmp_path) == {}
    (tmp_path / uc.RESOLVED_FILENAME).write_text("{not json", encoding="utf-8")
    assert uc.load_resolved_agent_config(tmp_path) == {}


def test_production_scan_config_resolves_cleanly():
    """活体验收:真的生产 `scan_config.jsonc` 必须能通过全部四条 fail-fast。

    这条是本组唯一读真文件的测试 —— 前面那些都在造 fixture,造得出来不代表生产那份合格。
    生产文件真缺了某个 role,这条会红,而不是等到扫描当天才发现。

    ⚠️ 必须显式给路径:`tests/scan/conftest.py` 有个 autouse fixture 把 `DEFAULT_PATH`
    指向 tmp,不给路径读到的是空配置,这条测试就会变成"测了个寂寞"(首跑实测已撞到)。
    """
    from pathlib import Path as _Path
    prod = (_Path(__file__).resolve().parents[2]
            / ".claude" / "skills" / "scan-market" / "scan_config.jsonc")
    resolved = uc.resolve_agent_config(uc.load_user_config(prod))
    assert set(resolved) == uc._AGENT_ROLES
    assert resolved["l4_card"]["effort"] == "max"
    assert resolved["gp_shell"] == {"model": "sonnet", "effort": "low"}
    codex = uc.resolve_agent_config(uc.load_user_config(prod), engine="codex")
    assert codex["l4_card"] == {"model": "gpt-5.6-sol", "reasoning_effort": "xhigh"}
    assert codex["l4_intel"]["web_search"] == "live"
    runtime_cfg = {**uc.load_user_config(prod), "engine": "codex"}
    assert uc.resolve_agent_bundle(runtime_cfg, engine="codex")["roles"] == codex


#: 迁移前(role→{model,effort} 直写)的 Claude 解析结果 —— 2026-09-13 迁到 role→tier +
#: `agent_engines` 那天,在 main 上跑同一个 `resolve_agent_config` 取的真身。
#: 搬迁类改动的 parity 要两条腿:**同对象**(都是 resolved dict)+ **搬迁前 golden**。
#: 只断言"新配置能解析"证明不了等价 —— 一个 tier 表填错,它照样解析得干干净净。
_CLAUDE_GOLDEN_BEFORE_TIER_MIGRATION = {
    "dossier_init": {"effort": "max"},
    "ens_review": {"effort": "max"},
    "gp_shell": {"effort": "low", "model": "sonnet"},
    "gp_shell_json": {"effort": "low", "model": "sonnet"},
    "l3_rank": {"effort": "max"},
    "l3_repair": {"effort": "medium"},
    "l4_card": {"effort": "max"},
    "l4_intel": {"effort": "max"},
    "sector_brief": {"effort": "xhigh"},
    "strategist": {"effort": "max"},
}


def test_claude_roles_resolve_exactly_as_before_the_tier_migration():
    """双引擎改造不许动 Claude 侧一个字:十个 role 的解析结果必须与迁移前逐字段相同。

    这些值直接决定每个 agent 跑在什么 effort 上 —— 判断类 role 悄悄从 max 掉到 xhigh,
    产物照样长得像回事,账单也照样出得来,没有任何门会喊。
    """
    from pathlib import Path as _Path
    prod = (_Path(__file__).resolve().parents[2]
            / ".claude" / "skills" / "scan-market" / "scan_config.jsonc")
    resolved = uc.resolve_agent_config(uc.load_user_config(prod))
    assert resolved == _CLAUDE_GOLDEN_BEFORE_TIER_MIGRATION
    # 判断类 role **不得**出现 model 键:那是 agent def frontmatter 的地盘(opus/sonnet),
    # 配置里写死会把 frontmatter 压掉 —— 同族前科见 test_resolved_model_key_absent...
    for role in ("l3_rank", "l4_card", "l4_intel", "strategist"):
        assert "model" not in resolved[role]


def _dual():
    roles = {role: {"tier": "critical"} for role in sorted(uc._AGENT_ROLES)}
    return {
        "agents": roles,
        "agent_engines": {
            "claude": {"tiers": {"critical": {"effort": "max"}}},
            "codex": {"tiers": {"critical": {
                "model": "gpt-5.6-sol", "reasoning_effort": "ultra",
                "fallback": {"model": "gpt-5.6-terra", "reasoning_effort": "high"},
            }}, "role_overrides": {"l4_intel": {"web_search": "live"}}},
        },
    }


def test_dual_schema_resolves_engine_specific_vocabulary(tmp_path):
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps(_dual()), encoding="utf-8")
    cfg = uc.load_user_config(p)

    claude = uc.resolve_agent_config(cfg, engine="claude")
    codex = uc.resolve_agent_config(cfg, engine="codex")
    assert claude["l3_rank"] == {"effort": "max"}
    assert codex["l3_rank"] == {"model": "gpt-5.6-sol", "reasoning_effort": "ultra"}
    assert codex["l4_intel"]["web_search"] == "live"


def test_codex_capability_mismatch_uses_only_declared_supported_fallback():
    capabilities = {
        "gpt-5.6-sol": {"reasoning_efforts": ["low", "medium"], "web_search": True},
        "gpt-5.6-terra": {"reasoning_efforts": ["high"], "web_search": True},
    }
    bundle = uc.resolve_agent_bundle(_dual(), engine="codex", capabilities=capabilities)

    assert bundle["capability_status"] == "FALLBACK_APPLIED"
    assert len(bundle["capability_mismatches"]) == len(uc._AGENT_ROLES)
    assert bundle["declared_roles"]["l3_rank"]["reasoning_effort"] == "ultra"
    assert bundle["roles"]["l3_rank"] == {
        "model": "gpt-5.6-terra", "reasoning_effort": "high",
    }
    assert bundle["roles"]["l4_intel"]["web_search"] == "live"


def test_dual_schema_rejects_unknown_tier_and_engine_fields(tmp_path):
    bad_tier = _dual()
    bad_tier["agents"]["l3_rank"] = {"tier": "missing"}
    with pytest.raises(ValueError, match="tier"):
        uc.resolve_agent_config(bad_tier, engine="codex")

    bad_field = _dual()
    bad_field["agent_engines"]["codex"]["tiers"]["critical"]["effort"] = "high"
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps(bad_field), encoding="utf-8")
    with pytest.raises(ValueError, match="reasoning_effort"):
        uc.load_user_config(p)


def test_materialized_bundle_separates_declared_runtime_and_resolved(tmp_path):
    capabilities = {"gpt-5.6-sol": {"reasoning_efforts": ["ultra"], "web_search": True}}
    bundle = uc.resolve_agent_bundle(_dual(), engine="codex", capabilities=capabilities)
    out = uc.materialize_agent_config(
        "2026-09-13", _dual(), root=tmp_path, engine="codex", bundle=bundle,
    )
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["engine"] == "codex"
    assert payload["declared_roles"] == bundle["declared_roles"]
    assert payload["runtime_capabilities"] == capabilities
    assert payload["roles"] == bundle["roles"]


def test_codex_runtime_capabilities_are_loaded_from_model_cache(tmp_path):
    cache = tmp_path / "models_cache.json"
    cache.write_text(json.dumps({"models": [{
        "slug": "gpt-test", "supported_reasoning_levels": [
            {"effort": "low"}, {"effort": "ultra"}],
        "web_search_tool_type": "text_and_image",
    }]}), encoding="utf-8")
    assert uc.load_codex_capabilities(cache) == {
        "gpt-test": {"reasoning_efforts": ["low", "ultra"], "web_search": True}
    }
