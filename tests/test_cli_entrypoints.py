"""CLI 入口冒烟 —— 防「模块能 import、命令跑不起来」型断裂(Wave8 W8-4)。

**为什么需要这层**:2026-07-28 首航 run 收尾时 `python -m autoresearch.scan.assemble`
直接 NameError —— Wave4 重构 `48b2e0d` 把实现抽进 `publisher`/`report_sections` 后,
适配器里 `main()` 仍用 `argparse.ArgumentParser` 却漏了 `import argparse`。
当时全量 **1952 passed**,一个都没红:模块 import 得动(argparse 只在 `main()` 里用),
而**没有任何测试跑过 CLI 入口**。「绿灯不等于有灯」的 CLI 层版本。

**两层契约**:

- **Tier 1 · 全部 `__main__` 模块必须 import 成功** —— 逮 import 期断裂
  (循环 import、删掉的符号仍被引用、依赖缺失)。
- **Tier 2 · 今天能 `--help` 的模块必须一直能** —— 逮 `main()` 体内的断裂。
  assemble 那个 bug 正落在这层:`--help` 会走进 `main()` 触发 NameError。

Tier 2 的名单是 **实测快照做的回归锁**,不是「应该支持 --help」的主张。判据故意不用
「文件里有没有 argparse」——`learning.retro` 手写 `--help` 分支却不用 argparse,
按那个启发式会被漏掉。`_NO_HELP_CLI` 里的豁免逐条注明**为什么**它合法地不认 `--help`,
新增模块若想进豁免必须同样给理由(而不是"测试红了就加进来")。
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_PKG_ROOT = _REPO_ROOT / "autoresearch"

# ── 合法地不认 `--help` 的 sys.argv 风格 CLI(2026-07-29 逐个读源码确认)──
# 这些模块直接切 sys.argv 取位置参数,`--help` 会被当成数据。它们仍受 Tier 1 约束。
_NO_HELP_CLI: dict[str, str] = {
    "autoresearch.analyze.assemble":
        "main() 取 sys.argv[1:] 当位置参数;参数不合法时打 usage 退 1",
    "autoresearch.analyze.harvest":
        "main() 手工拆 flags/pos(sys.argv);--help 落入 flags 后按缺位置参数退 1",
    "autoresearch.macro.assemble":
        "main() 用 sys.argv[1] 当 scan_dir;len<2 时打 usage 退 1",
    "autoresearch.macro.tushare_macro":
        "__main__ 块直接 sys.argv[1] 当日期 → int('help') ValueError",
}


def _main_modules() -> list[str]:
    """全仓带 `if __name__ == '__main__'` 的模块(= 可被 `python -m` 调起的入口)。"""
    out: list[str] = []
    for path in sorted(_PKG_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "if __name__" not in text:
            continue
        out.append(str(path.relative_to(_REPO_ROOT)).replace("/", ".")[: -len(".py")])
    return out


_ALL_MODULES = _main_modules()
_HELP_MODULES = [m for m in _ALL_MODULES if m not in _NO_HELP_CLI]


def _run(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *args],
        cwd=_REPO_ROOT,
        capture_output=True,
        timeout=timeout,
    )


def test_module_discovery_is_not_empty() -> None:
    """自检:发现逻辑坏掉时整个套件会静默变成 0 个用例(永不变红的绿灯)。"""
    assert len(_ALL_MODULES) >= 60, f"只发现 {len(_ALL_MODULES)} 个入口,发现逻辑可能坏了"
    assert "autoresearch.scan.assemble" in _ALL_MODULES


def test_no_help_exemptions_all_exist() -> None:
    """豁免名单不得含已删除/改名的模块(防豁免表变成僵尸)。"""
    stale = sorted(set(_NO_HELP_CLI) - set(_ALL_MODULES))
    assert not stale, f"豁免名单里有不存在的模块:{stale}"


@pytest.mark.parametrize("module", _ALL_MODULES)
def test_module_imports(module: str) -> None:
    """Tier 1:每个入口模块必须 import 成功。"""
    proc = _run(["-c", f"import {module}"])
    assert proc.returncode == 0, (
        f"import {module} 失败(rc={proc.returncode}):\n"
        f"{proc.stderr.decode(errors='replace')[-1500:]}"
    )


@pytest.mark.parametrize("module", _HELP_MODULES)
def test_cli_help_exits_zero(module: str) -> None:
    """Tier 2:`python -m <mod> --help` 必须退出 0。

    这层逮的是 `main()` 体内的断裂 —— import 期看不出来、`--help` 一走就炸
    (2026-07-28 的 `assemble.py` 缺 `import argparse` 即此形)。
    """
    proc = _run(["-m", module, "--help"])
    assert proc.returncode == 0, (
        f"python -m {module} --help 失败(rc={proc.returncode})。\n"
        f"若该模块**合法地**不解析 --help(sys.argv 风格),把它加进 _NO_HELP_CLI 并写明理由;\n"
        f"否则这是真断裂。stderr:\n{proc.stderr.decode(errors='replace')[-1500:]}"
    )
