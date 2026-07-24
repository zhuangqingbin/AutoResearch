"""workflow js 语法探针(Wave3.5 review R1 I-2):`node --check` 对本仓 workflow js 形态零鉴别力。

`.claude/workflows/*.js` 同时含 ESM `export const meta = {...}`(首行)与顶层 `return`
(各阶段收尾)。这个组合让 `node --check` 走它的"哪种模块"探测分支——探测本身会短路掉真正的
语法检查:故意打坏的括号/未闭合模板串,只要 `export` 还在场,`node --check` 依然 **exit 0**;
去掉 `export` 的同一份坏文件才会被它抓到(exit 1)。也就是说过往报告里出现的"`node --check`
语法通过"是一盏对这类文件永远不会变红的绿灯,不能当验证证据(同款教训见
`.superpowers/sdd/progress.md` "W35 教训"行)。

有鉴别力的探针 = 把文件顶行的 `export` 关键字剥掉(topLevel `return`/`const`/`await` 本就是
合法的函数体语句,只有 `export`/`import` 在函数体内不合法)后,用 `new AsyncFunction(body)`
编译——这只验证语法树能否解析,不执行(不认识 `args`/`agent`/`phase`/`log`/`parallel` 等
运行时全局也不会报错),真语法错误(括号/引号/模板串不闭合等)会让 `AsyncFunction` 构造器
真的抛 `SyntaxError`。

本机无 node → 整组跳过(不假通过;与既有 plan 文档"本机无 node 则跳过,靠人工重读 diff"的
口径一致)。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS_DIR = ROOT / ".claude" / "workflows"

_NODE = shutil.which("node")

# 只剥顶行 `export ` 关键字(本仓 workflow js 惯例 = 唯一一处 export,`export const meta = {`)。
# 换成普通 `const meta = {...}` 后,函数体内其余语句(顶层 return/await/const)全部合法。
_PROBE_JS = r"""
const fs = require('fs');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const path = process.argv[1];
const body = fs.readFileSync(path, 'utf8').replace(/^export\s+/m, '');
try {
  new AsyncFunction(body);
  process.exit(0);
} catch (e) {
  console.error(e.constructor.name + ': ' + e.message);
  process.exit(1);
}
"""


def _workflow_files() -> list[Path]:
    files = sorted(WORKFLOWS_DIR.glob("*.js"))
    assert files, f"未找到任何 workflow js:{WORKFLOWS_DIR}"
    return files


@pytest.mark.skipif(_NODE is None, reason="本机无 node,跳过(见模块 docstring)")
@pytest.mark.parametrize("path", _workflow_files(), ids=lambda p: p.name)
def test_workflow_js_compiles(path):
    """AsyncFunction body 编译探针:`.claude/workflows/*.js` 全部文件语法可解析。"""
    r = subprocess.run([_NODE, "-e", _PROBE_JS, "--", str(path)],
                        capture_output=True, text=True, timeout=10, check=False)
    assert r.returncode == 0, f"{path.name} 语法探针失败(AsyncFunction 编译报错):{r.stderr.strip()}"


@pytest.mark.skipif(_NODE is None, reason="本机无 node,跳过(见模块 docstring)")
def test_probe_has_discriminating_power_that_node_check_lacks(tmp_path):
    """反证探针本身有鉴别力,且(本机 node 版本上)`node --check` 对同一份坏文件确实盲
    (I-2 实证,原地自证)。

    造一份语法损坏的 workflow 文件(保留首行 `export const meta = {`,破坏后续一处模板串),
    本文件的 AsyncFunction 探针必须 exit 非 0(=抓到,硬性要求,不随 node 版本变化)。
    `node --check` 在本机(v25.7.0)上 exit 0(=盲,复现报告里的假绿灯)——但那是 node
    版本行为,不是探针的责任(老版本 node <~22 上 `node --check` 本就会正确抓到这类坏
    文件)。T2-R2-M-6(2026-07-24 终审同批建议):不再钉死 `node_check.returncode == 0`
    ——原强断言把本机 node 版本行为钉进了测试,换个 node 版本会让健康仓库直接变红;
    两者结论不同即可(探针必抓到、`node --check` 抓没抓到只作记录,不再要求它必须是 0)。
    """
    src = (WORKFLOWS_DIR / "l4-stock.js").read_text(encoding="utf-8")
    assert "const knownBase = dossierSummary" in src, "样本锚点漂移,先更新本探针测试"
    broken = src.replace("const knownBase = dossierSummary",
                          "const knownBase = dossierSummary(((", 1)
    broken_path = tmp_path / "broken-l4-stock.js"
    broken_path.write_text(broken, encoding="utf-8")

    node_check = subprocess.run([_NODE, "--check", str(broken_path)],
                                 capture_output=True, text=True, timeout=10, check=False)
    probe = subprocess.run([_NODE, "-e", _PROBE_JS, "--", str(broken_path)],
                            capture_output=True, text=True, timeout=10, check=False)
    assert probe.returncode != 0, "AsyncFunction 探针未能抓到故意打坏的语法——探针本身失效"
    # 唯一硬性要求:探针不劣于 `node --check`(node_check 抓到时探针不得反而没抓到)。
    # 不再钉死 `node_check.returncode == 0` 本身——那是本机 node 版本行为,换版本后
    # `node --check` 也可能自己抓到(两者结论相同,不代表探针失效)。
    assert not (node_check.returncode != 0 and probe.returncode == 0), (
        "反向失败:node --check 抓到了、探针反而没抓到——探针比 node --check 还弱")
