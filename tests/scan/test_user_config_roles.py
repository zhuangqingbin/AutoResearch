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
