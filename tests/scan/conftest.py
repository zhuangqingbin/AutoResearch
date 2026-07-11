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
