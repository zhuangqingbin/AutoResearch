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
