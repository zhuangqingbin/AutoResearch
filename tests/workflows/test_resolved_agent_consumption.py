#!/usr/bin/env python3
"""Wave12-T33 修复轮 1(I2):workflow 真的**吃**了 resolved,而不只是源码里出现过那个词。

原来的 `tests/test_agent_defs.py::test_all_workflows_consume_resolved_agent_config` 只断言
js 源码里有 `resolved_agents` / `RESOLVED[role]` 这两个**字符串** —— 复核者点出它对
"有没有人喂给它"零鉴别力(消费者接了线、生产者没接,测试照绿),这话是对的;更要命的是
它对"resolved 到底有没有赢过 AGENT_DEFAULTS"也零鉴别力:把优先级写反,字符串照样在。

本文件用**运行期探针**补上:真跑一遍 workflow 函数体,拿一个"一被调用就记下 opts"的桩
`agent` 顶替 harness 注入的真派发,直接看第一次派发**实际带出去的 model/effort**。
同 `test_empty_config_guard.py` 的机制(AsyncFunction 编译 + 真执行),不是 `node --check`
——本仓实测那对 workflow js 是永不变红的假绿灯(ESM export + 顶层 return → node 直接跳过)。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
T1_REVIEW = ROOT / ".claude" / "workflows" / "t1-review.js"

_NODE = shutil.which("node")

# 桩 agent:记下第一次派发的 opts 后立刻抛错终止,再把 opts 打到 stdout。
_CAPTURE = textwrap.dedent("""
  const fs = require('fs');
  const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
  const src = fs.readFileSync(process.argv[1], 'utf8').replace(/^export const meta/m, 'const meta');
  const A = JSON.parse(process.argv[2]);
  let captured = null;
  const agent = (p, opts) => { if (captured === null) captured = opts || {}; throw new Error('STOP'); };
  const fn = new AsyncFunction('agent','parallel','pipeline','log','phase','args','budget','workflow', src);
  Promise.resolve(fn(agent, null, null, () => {}, () => {}, A, {total: null}, null))
    .then(() => {}, () => {})
    .then(() => { console.log(JSON.stringify(captured)); });
""")


def _first_dispatch(script: Path, args: dict) -> dict | None:
    r = subprocess.run(["node", "-e", _CAPTURE, str(script), json.dumps(args)],
                       capture_output=True, text=True, timeout=15, check=False)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


_ARGS_BASE = {"date": "2026-01-01"}


@pytest.mark.skipif(_NODE is None, reason="本机无 node,跳过(口径同 test_workflow_js_syntax.py)")
def test_t1_review_first_dispatch_takes_resolved_over_everything():
    """t1-review 的第一次派发(合诊)必须吃 **resolved** 的档位。

    三层各给一个**互不相同**的值,才看得出赢的是谁:
      resolved.t1_diag = max + sonnet   ← 应该赢
      cfg.agents.t1_diag = low          ← T33 之前的通道
      AGENT_DEFAULTS.t1_diag = high     ← 文件内兜底
    拿到 max 才说明 resolved 真的被消费了;拿到 low/high 都说明那条分支是死代码。
    """
    args = {**_ARGS_BASE, "cfg": {
        "agents": {"t1_diag": {"effort": "low"}},
        "resolved_agents": {"t1_diag": {"effort": "max", "model": "sonnet"}}}}
    opts = _first_dispatch(T1_REVIEW, args)
    assert opts is not None, "第一次派发没被捕获到 —— 探针或 workflow 结构变了"
    assert opts["effort"] == "max", f"resolved 没赢:实际 {opts.get('effort')!r}"
    assert opts["model"] == "sonnet", "resolved 里的 model 也必须带出去"
    assert opts["label"] == "diagnose:2026-01-01"


@pytest.mark.skipif(_NODE is None, reason="本机无 node,跳过")
def test_t1_review_falls_back_to_cfg_agents_when_resolved_absent():
    """没有 resolved(老编排)→ 回退 `cfg.agents`,行为不变(parity)。"""
    args = {**_ARGS_BASE, "cfg": {"agents": {"t1_diag": {"effort": "low"}}}}
    assert _first_dispatch(T1_REVIEW, args)["effort"] == "low"


@pytest.mark.skipif(_NODE is None, reason="本机无 node,跳过")
def test_t1_review_falls_back_to_agent_defaults_when_only_other_keys_present():
    """resolved 与 cfg.agents 都没这个 role → 落文件内 `AGENT_DEFAULTS`(high)。"""
    args = {**_ARGS_BASE, "cfg": {"l4_intel": {"enabled": True}}}
    assert _first_dispatch(T1_REVIEW, args)["effort"] == "high"


@pytest.mark.skipif(_NODE is None, reason="本机无 node,跳过")
def test_t1_review_resolved_does_not_leak_other_roles():
    """resolved 里只有 t1_synth 时,t1_diag 不得误取它的档位(按 role 取,不是整块吞)。"""
    args = {**_ARGS_BASE, "cfg": {
        "agents": {"t1_diag": {"effort": "low"}},
        "resolved_agents": {"t1_synth": {"effort": "max"}}}}
    assert _first_dispatch(T1_REVIEW, args)["effort"] == "low"


@pytest.mark.skipif(_NODE is None, reason="本机无 node,跳过")
def test_producer_and_consumer_agree_on_the_key_name():
    """生产者(Python CLI)与消费者(js)用的是**同一个键名**。

    拆半特性最常见的死法就是两边键名对不上而谁都不报错 —— 这里让 Python 侧真跑一次
    `resolve_agent_config`,把它挂在消费者约定的键上喂进 workflow,看档位有没有真的生效。
    """
    from autoresearch.scan.user_config import _AGENT_ROLES, resolve_agent_config

    agents = {role: {"effort": "high"} for role in sorted(_AGENT_ROLES)}
    agents["t1_diag"] = {"effort": "max"}
    agents["gp_shell"] = {"model": "sonnet", "effort": "low"}
    agents["gp_shell_json"] = {"model": "sonnet", "effort": "low"}
    resolved = resolve_agent_config({"agents": agents})

    args = {**_ARGS_BASE, "cfg": {"agents": {"t1_diag": {"effort": "low"}},
                                  "resolved_agents": resolved}}
    assert _first_dispatch(T1_REVIEW, args)["effort"] == "max", (
        "Python 侧 resolve 出来的档位没能通过约定键名到达派发点 —— 生产者/消费者对不上")
