#!/usr/bin/env python3
"""Wave11 B1:agents 子键闭集 —— 拼写错必须 raise,这是 user_config 存在的唯一理由的补全。"""
import json

import pytest

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
