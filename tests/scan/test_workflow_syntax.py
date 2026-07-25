"""workflow JS 语法探针接进 pytest(Wave3.5 教训:`node --check` 对本仓 workflow 零鉴别力)。

2026-07-25 复验:往 scan-market.js 追加 `const broken = {{{` 后,`node --check` 仍 exit 0,
而本探针 exit 1。ESM(顶层 export + 顶层 await/return)会让 --check 跳过函数体解析 ——
那是一盏永远不会变红的绿灯,所以守卫必须走 AsyncFunction 构造器。
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="需要 node")

WF = Path(".claude/workflows")


def _check(p: Path):
    from scripts.check_workflow_js import check
    return check(p)


@pytest.mark.parametrize("name", sorted(p.name for p in WF.glob("*.js")))
def test_workflow_js_parses(name):
    ok, err = _check(WF / name)
    assert ok, f"{name} 语法错误:\n{err}"


def test_probe_actually_catches_broken_js(tmp_path):
    """探针自检:坏语法必须真的被逮到(否则这些绿灯全是装饰)。"""
    bad = tmp_path / "broken.js"
    bad.write_text("export const meta = { name: 'x' }\nconst broken = {{{\n", encoding="utf-8")
    ok, _ = _check(bad)
    assert not ok, "探针对明显的坏语法都不报错 —— 它没有鉴别力"
