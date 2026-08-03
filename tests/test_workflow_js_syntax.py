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


# ── 未定义调用探针(2026-08-03 事故)──────────────────────────────────
# 上面那个 AsyncFunction 探针只验**语法能否解析**,对「调用点抄过来了、helper 定义没跟过来」
# 天然盲:`gpJson(...)` 语法完全合法,错在运行时解析不到这个名字。2026-08-03 实跑一次性
# 撞出同一提交(99efe7d,Wave10 A2/A5)留下的**两个**同族缺口:
#   · scan-market.js:167 调 `gpJson(...)`,定义只在 l4-stock.js:42 —— 每次全扫都在 GATE1
#     之后立刻 `gpJson is not defined`,整条漏斗停在 L3 之前;
#   · l4-stock.js:160 调 `bash(...)`,本文件从未定义 —— 每只票都会在 Intel 相位后炸,
#     一张决策卡都出不来。
# 两处都写了 `.catch(() => null)` 却都没兜住:ReferenceError 是**同步**抛的,promise 根本
# 没生成,`.catch` 接不上。所以这类缺口在真跑之前零征兆,而真跑一次是几十分钟 + 真金 token。
#
# 探针做法:用一个真扫描器剥掉注释/字符串/模板串(含嵌套 `${}`),剥离时用空格替换、保留换行
# 所以行号不漂;然后收集 `标识符(` 形态的调用点,减去全文声明名 + workflow 运行时全局 + JS
# 内建。**刻意保守**:声明名的收集口径宁宽勿窄(漏报可以,误报不行)——本仓教训是天天报警的
# 检查会被无视,连带把真信号的公信力一起磨掉。
_REF_PROBE_JS = r"""
const fs = require('fs')
function strip(src) {
  let out = '', i = 0
  const n = src.length, stack = []
  const blank = (c) => (c === '\n' ? '\n' : ' ')
  while (i < n) {
    const top = stack.length ? stack[stack.length - 1] : null
    const c = src[i], c2 = src[i + 1]
    if (top && top.type === 'tpl') {
      if (c === '\\') { out += '  '; i += 2; continue }
      if (c === '`') { stack.pop(); out += ' '; i++; continue }
      if (c === '$' && c2 === '{') { stack.push({ type: 'expr', depth: 0 }); out += '  '; i += 2; continue }
      out += blank(c); i++; continue
    }
    if (c === '/' && c2 === '/') { while (i < n && src[i] !== '\n') { out += ' '; i++ } continue }
    if (c === '/' && c2 === '*') {
      out += '  '; i += 2
      while (i < n && !(src[i] === '*' && src[i + 1] === '/')) { out += blank(src[i]); i++ }
      out += '  '; i += 2; continue
    }
    if (c === "'" || c === '"') {
      const q = c; out += ' '; i++
      while (i < n && src[i] !== q) {
        if (src[i] === '\\') { out += '  '; i += 2; continue }
        out += blank(src[i]); i++
      }
      out += ' '; i++; continue
    }
    if (c === '`') { stack.push({ type: 'tpl' }); out += ' '; i++; continue }
    if (top && top.type === 'expr') {
      if (c === '{') { top.depth++; out += c; i++; continue }
      if (c === '}') {
        if (top.depth === 0) { stack.pop(); out += ' '; i++; continue }
        top.depth--; out += c; i++; continue
      }
    }
    out += c; i++
  }
  return out
}
const KEYWORDS = new Set(['if','for','while','switch','catch','return','typeof','await','new','function',
  'throw','else','do','in','of','yield','delete','void','instanceof','case','const','let','var','class',
  'try','finally','super','this','import','export','default'])
// Workflow 工具契约注入的运行时全局(见 Workflow tool 说明)
const WF_GLOBALS = new Set(['agent','parallel','pipeline','log','phase','args','budget','workflow'])
const JS_GLOBALS = new Set(['Object','Array','String','Number','Boolean','Math','JSON','Date','Promise',
  'Error','TypeError','RangeError','Set','Map','WeakMap','Symbol','RegExp','BigInt','parseInt','parseFloat',
  'isNaN','isFinite','encodeURIComponent','decodeURIComponent','console','require','process',
  'structuredClone','globalThis','Intl','Proxy','Reflect'])
function declaredNames(code) {
  const d = new Set()
  const add = (raw) => {
    const nm = String(raw).trim().replace(/^\.\.\./, '').split('=')[0].trim()
    if (/^[A-Za-z_$][\w$]*$/.test(nm) && !KEYWORDS.has(nm)) d.add(nm)
  }
  for (const m of code.matchAll(/\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)/g)) add(m[1])
  for (const m of code.matchAll(/\b(?:function\s*\*?|class)\s+([A-Za-z_$][\w$]*)/g)) add(m[1])
  for (const m of code.matchAll(/\bcatch\s*\(\s*([A-Za-z_$][\w$]*)/g)) add(m[1])
  for (const m of code.matchAll(/\(([^()]*)\)\s*=>/g)) m[1].split(',').forEach(add)
  for (const m of code.matchAll(/\bfunction\s*\*?\s*[A-Za-z_$\w]*\s*\(([^()]*)\)/g)) m[1].split(',').forEach(add)
  for (const m of code.matchAll(/(?:^|[^\w$.])([A-Za-z_$][\w$]*)\s*=>/gm)) add(m[1])
  for (const m of code.matchAll(/[[{]([^[\]{}]*)[\]}]\s*(?:=[^=>]|=>|\)\s*=>)/g)) {
    m[1].split(',').forEach((p) => add(p.split(':').pop()))
  }
  return d
}
const code = strip(fs.readFileSync(process.argv[1], 'utf8'))
const declared = declaredNames(code)
const missing = [], seen = new Set()
for (const m of code.matchAll(/(^|[^\w$.?])([A-Za-z_$][\w$]*)\s*\(/g)) {
  const nm = m[2]
  if (seen.has(nm) || KEYWORDS.has(nm) || declared.has(nm) || WF_GLOBALS.has(nm) || JS_GLOBALS.has(nm)) continue
  seen.add(nm)
  missing.push(nm + '@L' + code.slice(0, m.index).split('\n').length)
}
console.log(missing.join(', '))
process.exit(missing.length ? 2 : 0)
"""


def _ref_probe(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run([_NODE, "-e", _REF_PROBE_JS, "--", str(path)],
                          capture_output=True, text=True, timeout=15, check=False)


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


@pytest.mark.skipif(_NODE is None, reason="本机无 node,跳过(见模块 docstring)")
@pytest.mark.parametrize("path", _workflow_files(), ids=lambda p: p.name)
def test_workflow_js_has_no_undefined_calls(path):
    """每个 workflow js 里被调用的标识符,必须在本文件声明或属于运行时全局。

    拦的是「调用点抄过来了、helper 定义留在兄弟文件」这类缺口(2026-08-03 双发,见上方注释)。
    真跑之前它零征兆,而真跑一次是几十分钟 + 真金 token。
    """
    r = _ref_probe(path)
    assert r.returncode == 0, (
        f"{path.name} 存在「被调用但未定义」的标识符:{r.stdout.strip()}\n"
        f"(若是本仓新引入的运行时全局,请加进探针的 WF_GLOBALS 白名单并说明来源;"
        f"若是探针误报,请先证明它不是漏接线再放宽口径)")


@pytest.mark.skipif(_NODE is None, reason="本机无 node,跳过(见模块 docstring)")
@pytest.mark.parametrize(("fname", "anchor"), [
    ("scan-market.js", "const gpJson = "),   # 2026-08-03 缺口①(真实复现)
    ("l4-stock.js", "const bash = "),        # 2026-08-03 缺口②(真实复现)
])
def test_ref_probe_catches_the_real_20260803_defects(tmp_path, fname, anchor):
    """反证鉴别力:把 helper 定义改名 → 调用点即成悬空引用,探针必须变红。

    这两条不是构造的假样本 —— 就是 2026-08-03 实跑撞上的那两个缺口,把定义摘掉即等价还原。
    探针抓不到其中任何一个,就等于本模块又多了一盏永不变红的绿灯(同款教训见模块 docstring)。
    """
    src = (WORKFLOWS_DIR / fname).read_text(encoding="utf-8")
    assert src.count(anchor) == 1, f"{fname} 的锚点 {anchor!r} 漂移或不唯一,先更新本测试"
    broken = tmp_path / f"broken-{fname}"
    # 只改名,不动结构:`const gpJson = ` → `const gpJsonRENAMED = `(语法仍合法,只有引用悬空)
    renamed = anchor.replace(" = ", "RENAMED = ")
    assert renamed != anchor and renamed.endswith("RENAMED = "), f"变异构造失败:{renamed!r}"
    broken.write_text(src.replace(anchor, renamed, 1), encoding="utf-8")

    syntax = subprocess.run([_NODE, "-e", _PROBE_JS, "--", str(broken)],
                            capture_output=True, text=True, timeout=10, check=False)
    ref = _ref_probe(broken)
    assert syntax.returncode == 0, "前提失守:改名后语法仍应合法(否则本反证证明不了引用层的鉴别力)"
    assert ref.returncode != 0, f"未定义调用探针未能抓到 {fname} 的悬空引用——探针失效"
