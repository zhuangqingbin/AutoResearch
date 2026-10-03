"""三个 legacy workflow 对 config 的两条共同纪律(2026-09-27 配置标准 P0)。

07-21 事故:传 `{}` 当 config 会静默关 intel + 降 effort。scan-market.js / l4-stock.js 早就
直接 throw,dossier-init.js 一直没有这道守卫,而且不读 `cfg.engine`(另两个都读)。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ("scan-market.js", "l4-stock.js", "dossier-init.js")


@pytest.mark.parametrize("name", WORKFLOWS)
def test_workflow_refuses_an_empty_config_unless_told_otherwise(name):
    src = (ROOT / ".claude/workflows" / name).read_text(encoding="utf-8")
    assert "allow_empty_config" in src, f"{name}: 空 config 必须 throw(07-21 事故守卫)"
    assert re.search(r"throw new Error\('args\.(config|cfg) 为空", src), name


@pytest.mark.parametrize("name", WORKFLOWS)
def test_workflow_reads_engine_from_config_echo(name):
    src = (ROOT / ".claude/workflows" / name).read_text(encoding="utf-8")
    assert re.search(r"cfg\.engine|A\.cfg\s*&&\s*A\.cfg\.engine", src), f"{name}: 要读 config 回显的 engine"
