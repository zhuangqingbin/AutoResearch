"""scan 测试共享隔离。

**DEFAULT_PINNED_PATH 隔离(autouse)**:`universe.run`/`assemble`/`l3_select` 在不传 `pinned_path`
时读默认 `.claude/skills/scan-market/pinned.jsonc`(FN-1 修复后生产入口真读它)。开发者本地可能有
**真实保送票**(如 300033),会被非隔离测试注入 L1 → 污染 parity/召回断言。此处把默认路径指向一个
保证不存在的路径,让所有 scan 测试默认"无保送"(=parity);要测 pinned 的用例照旧传显式 `pinned_path=`
(显式参数优先,不受本 fixture 影响)。
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_default_pinned(monkeypatch):
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PINNED_PATH",
                        Path("/nonexistent/tests-no-real-pinned.jsonc"))


@pytest.fixture(autouse=True)
def _isolate_temperature_csv(monkeypatch):
    """镜像 _isolate_default_pinned:context/learning/temperature.csv 由真实 prelude 增量落盘
    (gitignored、按日期键),tmp_path 隔离测试不该读到——否则硬编码 analysis_date 的既有断言会被
    开发机真数据污染(如 2026-07-02 行使 assemble 多出 🌡 行)。要测温度的用例自行 monkeypatch
    CSV_PATH(测试体内的 setattr 后执行,优先生效)。"""
    monkeypatch.setattr("autoresearch.scan.temperature.CSV_PATH",
                        Path("/nonexistent/tests-no-real-temperature.csv"))
