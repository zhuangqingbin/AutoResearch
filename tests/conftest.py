"""Shared pytest fixtures that prevent CI hangs when API keys are absent."""

import os

import pytest


def pytest_configure(config):
    for marker in ("unit", "integration", "smoke"):
        config.addinivalue_line("markers", f"{marker}: {marker}-level tests")


_API_KEY_ENV_VARS = (
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "ANTHROPIC_API_KEY",
    "XAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
    "DASHSCOPE_CN_API_KEY",
    "ZHIPU_API_KEY",
    "ZHIPU_CN_API_KEY",
    "MINIMAX_API_KEY",
    "MINIMAX_CN_API_KEY",
    "OPENROUTER_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "ALPHA_VANTAGE_API_KEY",
)


@pytest.fixture(autouse=True)
def _dummy_api_keys(monkeypatch):
    for env_var in _API_KEY_ENV_VARS:
        monkeypatch.setenv(env_var, os.environ.get(env_var, "placeholder"))


@pytest.fixture(autouse=True)
def _no_row_checks(monkeypatch):
    """关掉数据契约的**规模性**检查(行数腰斩线),全局。

    单测用合成小 fixture(几十~几百只股票)是常态,拿生产的全市场行数线(3000+)去卡它全是误报
    ——golden parity 的 600 只帧就是这么被打挂的。

    **结构性**检查(空帧 / 缺列 / 整列全 NaN)**仍然全开**,不受此开关影响:那是无论数据规模都
    成立的 bug,也正是真实事故的形态(窄表毒化的签名恰恰是"行数够、但缺 high/low/amount")。
    行数逻辑本身由 `tests/data/test_contracts.py` 显式打开开关来测。
    """
    import autoresearch.data.contracts as contracts

    monkeypatch.setattr(contracts, "CHECK_ROWS", False)


@pytest.fixture(autouse=True)
def _isolate_config():
    """Reset the global dataflows config before and after each test.

    ``set_config`` merges (it never clears keys absent from the override), so a
    test that sets e.g. ``tool_vendors`` would otherwise leak into later tests
    and make routing behavior order-dependent. Replace the global outright so
    every test starts from a clean DEFAULT_CONFIG.
    """
    import copy

    import autoresearch.dataflows.config as config_module
    import autoresearch.default_config as default_config

    config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)
    yield
    config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)


@pytest.fixture(autouse=True)
def _isolate_dossier_dir(tmp_path, monkeypatch):
    """dossier 层隔离(Wave3):防任何测试读写真实 context/knowledge/dossiers。

    module-attr 派发(builder._load_prefetch 读 prefetch.PREFETCH_DIR、schema.dossier_path
    读 schema.DOSSIER_DIR 均为调用时取值)→ monkeypatch 生效;两常量独立(PREFETCH_DIR
    在 import 时由 DOSSIER_DIR 计算,patch 前者不会带动后者),必须双 patch。
    """
    from autoresearch.dossier import prefetch as _pf, schema as _sch
    d = tmp_path / "_dossiers"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(_sch, "DOSSIER_DIR", d)
    monkeypatch.setattr(_pf, "PREFETCH_DIR", d / "_prefetch")
    yield
