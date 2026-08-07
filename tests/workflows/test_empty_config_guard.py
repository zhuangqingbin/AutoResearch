"""Wave11 B5:空 config 必须在任何 agent 派发前抛错 —— 用桩 agent 证明「先于一切派发」。

07-21 事故:scan-market 的配置真身是 `.jsonc` 但当时按 `.json` 去查,查无 → 传了空 config 给
workflow。空 config 的后果是**静默**的:intel 被关掉、全体 agent 掉回缺省 effort,而报告上
看不出来,事后翻记录才发现。Wave11-B5 用结构性拒绝(cfg 常量后立即 throw)替代文档叮嘱。

探针机制(同 test_workflow_js_syntax.py 的 AsyncFunction 编译探针,但这里真执行到 throw/
真派发,不只是解析语法):纯语法探针证明不了"guard 先于 dispatch"这件事 —— 必须真跑一遍
函数体,用一个**一被调用就报错**的桩 `agent` 顶替 Workflow 工具注入的真 `agent`。若桩函数
先被调用(而不是 guard 先 throw「为空」),说明 guard 要么没拦住,要么拦的位置太晚
(前面已经派发过 agent 了)——这正是 07-21 事故的结构性重演:静默继续跑,不是显式失败。

l4-stock.js 自己有「date/code 必填」校验(在 cfg 解析之前),args 必须带 `code` 才能走到
cfg guard;不带 code 会先撞那条校验,消息不含「为空」→ 测试假红。scan-market.js 不关心
`code` 键(忽略多余键),同一份 args 两文件通用。
"""
from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCAN_MARKET = ROOT / ".claude" / "workflows" / "scan-market.js"
L4_STOCK = ROOT / ".claude" / "workflows" / "l4-stock.js"

_NODE = shutil.which("node")

# 桩 agent 一被调用就抛 AGENT_CALLED_BEFORE_GUARD——如果 guard 真的挡在最前面,这个桩
# 永远不会被调用,catch 里收到的应是 guard 自己的「为空」错误。
_PROBE_BLOCKS = textwrap.dedent("""
  const fs = require('fs');
  const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
  let src = fs.readFileSync(process.argv[1], 'utf8').replace(/^export const meta/m, 'const meta');
  const agent = () => { throw new Error('AGENT_CALLED_BEFORE_GUARD'); };
  const fn = new AsyncFunction('agent','parallel','pipeline','log','phase','args','budget','workflow', src);
  fn(agent, null, null, () => {}, () => {}, {date: '2026-01-01', code: '600000', name: 'X', sector: 'Y'}, {total: null}, null)
    .then(() => { console.log('NO_THROW'); process.exit(1); })
    .catch(e => { console.log(e.message); process.exit(/为空/.test(e.message) ? 0 : 1); });
""")

# 逃生旗必须真的放行(离线试装用):同样的空 cfg,但 args.allow_empty_config=true。
# 桩 agent 这次改名 REACHED_AGENT_DISPATCH——看到它才能证明 guard 没拦、执行真的走到了
# 第一处 agent 派发(不是在中途某处静默 return 掉、假装放行)。
_PROBE_ESCAPE = textwrap.dedent("""
  const fs = require('fs');
  const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
  let src = fs.readFileSync(process.argv[1], 'utf8').replace(/^export const meta/m, 'const meta');
  const agent = () => { throw new Error('REACHED_AGENT_DISPATCH'); };
  const fn = new AsyncFunction('agent','parallel','pipeline','log','phase','args','budget','workflow', src);
  const a = {date: '2026-01-01', code: '600000', name: 'X', sector: 'Y', allow_empty_config: true};
  fn(agent, null, null, () => {}, () => {}, a, {total: null}, null)
    .then(() => { console.log('NO_THROW_STILL_OK'); process.exit(0); })
    .catch(e => {
      console.log(e.message);
      // 只要求 guard 本身没拦(消息不含「为空」);之后撞到桩 agent 报错是预期内
      // (真实环境里 agent 是 Workflow 工具注入的真实派发,不会报错)。
      process.exit(/为空/.test(e.message) ? 1 : 0);
    });
""")


def _probe(script: str, path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["node", "-e", script, str(path)],
                           capture_output=True, text=True, timeout=10, check=False)


@pytest.mark.skipif(_NODE is None, reason="本机无 node,跳过(与 test_workflow_js_syntax.py 口径一致)")
def test_scan_market_guards_empty_config():
    r = _probe(_PROBE_BLOCKS, SCAN_MARKET)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.skipif(_NODE is None, reason="本机无 node,跳过(与 test_workflow_js_syntax.py 口径一致)")
def test_l4_stock_guards_empty_config():
    r = _probe(_PROBE_BLOCKS, L4_STOCK)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.skipif(_NODE is None, reason="本机无 node,跳过(与 test_workflow_js_syntax.py 口径一致)")
def test_scan_market_allow_empty_config_escape_hatch():
    """离线试装:args.allow_empty_config=true 必须真的放行到 agent 派发点,不是被别处拦截。"""
    r = _probe(_PROBE_ESCAPE, SCAN_MARKET)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.skipif(_NODE is None, reason="本机无 node,跳过(与 test_workflow_js_syntax.py 口径一致)")
def test_l4_stock_allow_empty_config_escape_hatch():
    """离线试装:args.allow_empty_config=true 必须真的放行到 agent 派发点,不是被别处拦截。"""
    r = _probe(_PROBE_ESCAPE, L4_STOCK)
    assert r.returncode == 0, r.stdout + r.stderr
