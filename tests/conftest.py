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
def _isolate_degradation_ledger():
    """Reset the process-global B-tier degradation ledger around every test.

    `autoresearch.data.contracts._DEGRADED` 是一个**进程级 list**,谁都能往里 append,
    从来没人在测试之间清过。于是任何一个「跑完一趟会记降级」的测试都会污染它后面
    所有读 `degradations()` 的测试 —— 而这类失败**只在特定执行顺序下出现**。

    真事故(2026-08-31 实测,merge-base `6a5a566` 就在,不是本波引入):
    `pytest tests/trace tests/analyze` → `test_assemble_writes_manifest_v2_fields`
    断言 `manifest["degradations"] == 0` 却拿到 **22** —— 那 22 条是
    `tests/trace/test_finalization.py` 里每一趟 finalize 经
    `capsule._resolve_run_mode → _degrade_evidence` 记下的 `capsule.run_mode` 降级。
    单独跑 `tests/analyze` 永远是绿的,所以它伪装成「偶发」。

    修在这里而不是在受害者那边加防御:**受害者不止一个**(`tests/scan` 同样能触发),
    而且「降级不留痕才是真病」—— 生产侧的记账一行都不能少,该隔离的是测试进程。
    位置紧挨 `_isolate_config`:那条修的是另一个同族进程级全局(`dataflows.config._config`)。

    仓里已有 8 个测试文件手写了自己的 `clear_degradations()` fixture —— 它们正是在
    绕开这个缺失的全局隔离。留着不动(幂等,且有几个还要断言具体内容)。
    """
    import autoresearch.data.contracts as contracts

    contracts.clear_degradations()
    yield
    contracts.clear_degradations()


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
