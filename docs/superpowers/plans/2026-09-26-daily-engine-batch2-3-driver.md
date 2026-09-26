# 日报引擎 · 批 2–3:session_v1 runner + 执行器(host / mailbox)+ 一场真跑 + Workflow 降级

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让一场扫描的编排从「JS Workflow + 141 个 Sonnet 壳」变成「Python runner 在进程内跑确定性步骤,只有 7 种判断角色才调模型」,在交互会话(host 模式)完成一场真扫,壳 agent = 0,brief/capsule 契约不变;两个 JS Workflow 降级为 fallback。

**Architecture:** 不建第四个编排对象。`session_agent` 已有冻结计划、`next/claim/execute/submit/finish` 环、L4 任务簿适配、评审矩阵、发布——缺的只是**自动转圈的 runner** 和**兑现推理任务的执行器**。本批新增 `session_agent/runner.py`(循环:`next` → 领 READY 任务 → 确定性任务进程内 `execute`,推理任务交给执行器 → `submit` → 直到 `DONE` → `finish`)与 `session_agent/executors/`(`base.py` 协议 + `mailbox.py` host 模式:请求/结果文件,交互会话或 Codex 会话派 Agent 兑现)。headless 执行器(`claude -p`)留批 4。探针已证:嵌套 `claude -p` 可行、`--agent` 装载项目 agent + hook(`docs/research/2026-09-26-headless-driver-probes.md`)。

**Tech Stack:** Python 3(threading / subprocess / fcntl)、pytest、现有 `trace.exec_capture`、`session_agent.*`;交互侧 Claude Code `Monitor` + `Agent` 工具;Codex 侧 shell。

**Spec:** `docs/superpowers/specs/2026-09-26-daily-engine-consolidation-design.md` §3.2–§3.3、§4 A1(A1-1…A1-9)、§6 C4。

## Global Constraints

- 驱动器内核 = `session_agent`(§3.3 首选);时间盒:Task 2 的审计 + Task 6 的一场真跑内跑通;两场内跑不通 → 停下写降级设计(薄 `scan/driver.py`),不在本计划里继续硬撑。
- 冻结窗内(buyability 批 4 十日真跑):本批不改任何召回/L3/L4/评级行为;真跑后 `capsule replay` 的确定性阶段必须与同日 legacy 产物 byte 相同(Task 6 步骤 4)。
- 邮箱协议:驱动器只认 `result` 文件,不认执行器自报;原子写(tmp + rename)+ `fcntl` 锁;瞬时错误分类复用 `contracts/retry.py`。
- 判断角色 7 种不变(`macro-brief` / `sector-brief` / `l3-rank`(+repair)/ `l4-intel` / `l4-card` / `l4-card` 复核);model/effort 由 `user_config.resolve_agent_bundle` 解释,执行器只透传。
- 不允许出现 `agentType: 'general-purpose'` 壳:runner 路径上一个都不能有(验收用 `token_usage.md` 的 general-purpose 行 = 0 锁死)。
- 两引擎:mailbox 执行器对 Claude/Codex 会话都可用(会话侧只是「读请求 → 派 agent → 写结果」);Codex 一场真跑是 Task 8。
- 提交末尾 `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`。

## Review Focus

1. **半途崩溃**:runner 在 `claim` 之后、`submit` 之前死掉 → 重启必须从 `next` 看到 WAITING/RUNNING 并按 `resume` 语义接管,不能重复派同一角色两次(Task 1 的 crash 测试)。
2. **执行器超时**:l4-card 30 分钟没结果 → runner 记 `TIMEOUT`(可重试一次)而不是永远等;超时后旧结果文件迟到必须被忽略(attempt 不匹配)(Task 3)。
3. **并发上限**:L4 每股 intel+card 并行,帽 = `budgets.concurrency.l4_stock`;超过帽的任务留在 READY 不领(Task 1)。
4. **交互会话误操作**:用户在 host 模式手工 `Agent` 派了但忘写结果 → runner 到期 TIMEOUT,日志里写明「等待 <task_id> 的结果文件 <path>」,而不是静默(Task 4)。
5. **老 run 兼容**:runner 只服务新 run;对已用 legacy Workflow 跑过的同日 staging 不得混用(Task 6 用新 run_id)。

---

### Task 1: runner 核心循环(确定性任务进程内执行)

**Files:**
- Create: `autoresearch/session_agent/runner.py`
- Create: `autoresearch/session_agent/executors/__init__.py`、`executors/base.py`
- Modify: `autoresearch/session_agent/__main__.py`(新增 `run` 子命令)
- Test: `tests/session_agent/test_runner.py`

**Interfaces:**
- Produces: `class InferenceExecutor(Protocol): def dispatch(self, request: dict, *, task: dict, attempt: int, handle) -> dict`(返回 `{"outputs": [{"artifact_id","sha256"}], "session_ref": str, "context_ref": str, "parent_context_ref": str|None, "evidence_refs": list[str]}`);`runner.run_loop(run_id, executor, *, max_parallel=4, poll_seconds=5.0, max_rounds=5000, clock=time.monotonic) -> dict`(返回最终 `status` dict,含 `finished: bool`)。
- Consumes: `service.next/claim/execute/submit/finish/fail/resume`(签名见 `session_agent/service.py:250-1042`),`hosts.base.render_request`。

- [ ] **Step 1: 写失败测试**(用现有 `test.noop` 操作与合成计划;夹具照 `tests/session_agent/test_service.py` 的 `_begin_synthetic_run` 起一个只含 2 个确定性任务 + 1 个推理任务的 run)

```python
# tests/session_agent/test_runner.py
from __future__ import annotations

import json

from autoresearch.session_agent import runner, service
from tests.session_agent.conftest import begin_synthetic_run   # 若无此夹具,复制 test_service 的起 run 代码为 conftest 夹具


class _FakeExecutor:
    def __init__(self):
        self.calls = []

    def dispatch(self, request, *, task, attempt, handle):
        self.calls.append((task["task_id"], attempt))
        out_id = task["output_artifact_ids"][0]
        path = handle.artifact_path(out_id)            # 若 handle 无此法,用 artifacts.resolve_path(handle, out_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("**Rating**: Hold\nFINAL TRANSACTION PROPOSAL: **HOLD**\n", encoding="utf-8")
        return {"outputs": [{"artifact_id": out_id}], "session_ref": "fake-session",
                "context_ref": f"ctx-{task['task_id']}", "parent_context_ref": None,
                "evidence_refs": ["host-binding:" + "0" * 64]}


def test_run_loop_executes_deterministic_then_inference_then_finishes(synthetic_run):
    ex = _FakeExecutor()
    final = runner.run_loop(synthetic_run.run_id, ex, poll_seconds=0.01, max_rounds=50)
    assert final["status"] == "DONE" and final["finished"] is True
    assert [c[0] for c in ex.calls] == ["synthetic.inference"]          # 推理任务恰好派一次


def test_run_loop_resumes_without_redispatching_running_inference(synthetic_run):
    ex = _FakeExecutor()
    # 先手工 claim 推理任务,模拟上一进程在 submit 前崩溃
    service.claim(synthetic_run.run_id, "synthetic.inference", 1)
    final = runner.run_loop(synthetic_run.run_id, ex, poll_seconds=0.01, max_rounds=5)
    assert ex.calls == [] and final["status"] in {"WAITING", "BLOCKED"}   # 不重派,等 resume/fail 决策


def test_run_loop_respects_parallel_cap(synthetic_run_with_6_inference):
    seen = []
    class Ex(_FakeExecutor):
        def dispatch(self, request, *, task, attempt, handle):
            seen.append(len(runner._inflight))             # 派发瞬间的在飞数
            return super().dispatch(request, task=task, attempt=attempt, handle=handle)
    runner.run_loop(synthetic_run_with_6_inference.run_id, Ex(), max_parallel=2, poll_seconds=0.01)
    assert max(seen) <= 2
```

- [ ] **Step 2: 跑测试确认失败** — Run: `uv run --no-sync pytest tests/session_agent/test_runner.py -q`;Expected: FAIL(`ImportError: runner`)

- [ ] **Step 3: 实现**

```python
# autoresearch/session_agent/executors/base.py
"""推理任务执行器协议:runner 把渲染好的 host request 交给它,它负责让模型产出并回传 receipt 素材。"""
from __future__ import annotations

from typing import Protocol


class InferenceExecutor(Protocol):
    name: str

    def dispatch(self, request: dict, *, task: dict, attempt: int, handle) -> dict:
        """阻塞直到该任务产出落盘;返回 outputs/session_ref/context_ref/parent_context_ref/evidence_refs。
        抛 TimeoutError = 可重试一次;抛其它异常 = 本 attempt 失败。"""
        ...
```

```python
# autoresearch/session_agent/runner.py
"""session_v1 自动转圈 runner:确定性任务进程内执行,推理任务交执行器,直到 DONE → finish。

只做调度,不做判断:任务图、attempt、回执、发布仍归 service/store/publication。
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import time

from autoresearch.session_agent import service
from autoresearch.session_agent.hosts.base import render_request
from autoresearch.trace.capsule import require_active_run

_inflight: dict[str, cf.Future] = {}


def _params_for(task: dict) -> dict:
    """确定性操作的参数:scan 全部操作无参数(tool-catalog),subject 由 task 自带;stock.harvest 从 request 冻结值取。"""
    from autoresearch.session_agent.operations import operation_spec
    spec = operation_spec(task["operation"])
    if not spec.get("params_schema", {}).get("required"):
        return {}
    raise RuntimeError(f"operation {task['operation']} 需要参数,runner 尚未登记参数来源")


def _dispatch_inference(run_id, task, attempt, executor, handle):
    claimed = service.claim(run_id, task["task_id"], attempt)
    request = claimed.get("request") or render_request(task, claimed["host_profile"])
    result = executor.dispatch(request, task=task, attempt=attempt, handle=handle)
    submission = {"schema_version": 1, "envelope": claimed["envelope"], "plan_hash": claimed["plan_hash"],
                  "outputs": result["outputs"], "host_receipt_id": None}
    receipt = None
    if task.get("independent_context"):
        receipt = {"schema_version": 1, "engine": claimed["host_profile"]["engine"],
                   "session_ref": result["session_ref"], "context_ref": result["context_ref"],
                   "parent_context_ref": result["parent_context_ref"], "task_id": task["task_id"],
                   "attempt": attempt, "completed": True, "evidence_refs": result["evidence_refs"]}
    return service.submit(run_id, submission, host_receipt=receipt)


def run_loop(run_id: str, executor, *, max_parallel: int = 4, poll_seconds: float = 5.0,
             max_rounds: int = 5000, clock=time.monotonic) -> dict:
    handle = require_active_run(run_id)
    pool = cf.ThreadPoolExecutor(max_workers=max_parallel)
    rounds = 0
    while rounds < max_rounds:
        rounds += 1
        state = service.next(run_id)
        status = state["status"]
        if status == "DONE":
            report = service.finish(run_id)
            return {**state, "finished": True, "report": report}
        if status == "BLOCKED":
            return {**state, "finished": False}
        for task in state.get("tasks", []):                      # READY 任务
            tid = task["task_id"]
            if tid in _inflight or len(_inflight) >= max_parallel:
                continue
            attempt = int(task.get("next_attempt") or 1)
            if task["kind"] == "DETERMINISTIC":
                service.claim(run_id, tid, attempt)
                service.execute(run_id, tid, attempt, _params_for(task))
            else:
                _inflight[tid] = pool.submit(_dispatch_inference, run_id, task, attempt, executor, handle)
        for tid, fut in list(_inflight.items()):
            if fut.done():
                _inflight.pop(tid)
                exc = fut.exception()
                if exc is not None:
                    cls = "TIMEOUT" if isinstance(exc, TimeoutError) else "AGENT_ERROR"
                    service.fail(run_id, tid, int(task_attempt(handle, tid)), cls, str(exc)[:500])
        if status == "WAITING" or _inflight:
            time.sleep(poll_seconds)
    return {"status": "TIMEOUT", "finished": False, "rounds": rounds}


def task_attempt(handle, task_id: str) -> int:
    from autoresearch.session_agent import store
    from autoresearch.session_agent.service import _store_path
    return int(store.read_entry(_store_path(handle), task_id)["attempt"])
```
(字段名 `request`/`envelope`/`plan_hash`/`host_profile`/`next_attempt` 以 `service.claim`/`status` 的**实际返回**为准——实施第一步先 `uv run --no-sync python -c "from autoresearch.session_agent import service; help(service.claim)"` 并读 `tests/session_agent/test_service.py` 的断言,把键名对齐;对不上的以 service 为准改 runner,不改 service。)

`__main__.py` 加 `run` 子命令:`--run-id`、`--executor {mailbox}`(headless 批 4 再加)、`--max-parallel`(缺省读 `budgets.concurrency.l4_stock`)、`--poll-seconds`。

- [ ] **Step 4: 跑测试确认通过** — Run: `uv run --no-sync pytest tests/session_agent/test_runner.py tests/session_agent/test_service.py -q`;Expected: PASS(注意本会话 37 个引擎环境红,见记忆 `claude-engine-session-agent-tests-red-20260926`,需 `AUTORESEARCH_ENGINE=codex` 跑 session_agent 目录)

- [ ] **Step 5: 提交** — `git commit -m "feat(session_agent): runner loop — deterministic in-process, inference via executor protocol"`

---

### Task 2: 扫描计划 vs `l4-stock.js` 分支审计(时间盒:半天)

**Files:**
- Create: `docs/research/2026-09-2x-session-plan-vs-workflow-audit.md`
- Test: 无(审计产出清单;发现的每个缺口进 Task 5)

- [ ] **Step 1**: 列出 `l4-stock.js:300-561` 的全部分支(preflight SKIP/BLOCKED/WAIT、intel 3 次重试与瞬时错误集、intel_resume、intel_guard REJECTED、slim LOST/TIMEOUT/DATA_INTEGRITY、card 无返回、ow_review/sell_review 触发条件、同档早止、ensemble dump、task success/failure 错误类、recordL4 阶段记录)。
- [ ] **Step 2**: 对每条在 `session_agent/domain_ops.py`(`scan_l4_*`、`scan_review_*`)与 `operations.py` 目录里找对应操作;写成三列表:分支 / session_v1 对应 / 缺口。
- [ ] **Step 3**: 同样对 `scan-market.js:311-560`(frame 重试、strategist pack 补投、prelude 重试、GATE1 四态、sector 复用列表、L3 lint→repair→apply、GATE2、l4-prep 四生产者并行、prompts、task book init)。
- [ ] **Step 4**: 缺口分级:**阻断**(真跑必撞)/ **降级可接受**(记账即可)/ **无关**。阻断项进 Task 5;文档提交。

---

### Task 3: mailbox 执行器(host 模式)

**Files:**
- Create: `autoresearch/session_agent/executors/mailbox.py`
- Create: `autoresearch/session_agent/mailbox_cli.py`(会话侧命令:`wait` / `complete`)
- Modify: `autoresearch/session_agent/__main__.py`(`mailbox` 子命令组)
- Modify: `autoresearch/contracts/artifacts.py`(登记 `_dispatch/` 目录为 run 内产物)
- Test: `tests/session_agent/test_mailbox.py`

**Interfaces:**
- Produces: 请求文件 `<staging>/_dispatch/<task_id>.a<attempt>.request.json`:`{"schema_version":1,"task_id","attempt","role","config_role","agent_type","model","effort","prompt","instruction_refs","input_paths","output_paths","timeout_seconds"}`;结果文件 `<task_id>.a<attempt>.result.json`:`{"schema_version":1,"task_id","attempt","ok","session_ref","context_ref","parent_context_ref","transcript_path","error"}`;`MailboxExecutor(staging, timeouts: dict[str,int])`。
- 会话侧 CLI:`python -m autoresearch.session_agent mailbox wait --run-id R [--timeout 600]` 阻塞到出现未处理请求,打印其 JSON(一次一个);`mailbox complete --run-id R --task-id T --attempt N --session-ref S --context-ref C [--parent-context-ref P] [--transcript-path F] [--error E]` 写结果文件。

- [ ] **Step 1: 写失败测试**

```python
def test_mailbox_roundtrip_and_stale_result_ignored(tmp_path):
    ex = mailbox.MailboxExecutor(tmp_path, timeouts={"scan.l4.card": 2}, poll_seconds=0.05)
    task = {"task_id": "scan.l4.card.600000", "role": "scan.l4.card", "output_artifact_ids": ["card.600000"],
            "independent_context": False}
    # 迟到的旧 attempt 结果先落盘,必须被忽略
    (tmp_path / "_dispatch").mkdir()
    (tmp_path / "_dispatch" / "scan.l4.card.600000.a1.result.json").write_text(
        json.dumps({"schema_version": 1, "task_id": task["task_id"], "attempt": 1, "ok": True,
                    "session_ref": "old", "context_ref": "old", "parent_context_ref": None}), encoding="utf-8")
    import threading
    def answer():
        req = mailbox.wait_request(tmp_path, timeout=2)
        assert req["attempt"] == 2
        mailbox.write_result(tmp_path, task["task_id"], 2, ok=True, session_ref="s", context_ref="c",
                             parent_context_ref=None, transcript_path=None, error=None)
    t = threading.Thread(target=answer); t.start()
    res = ex.dispatch({"task_id": task["task_id"], "prompt": "p", "instruction_refs": [], "input_artifact_ids": [],
                       "output_artifact_ids": task["output_artifact_ids"], "role": task["role"]},
                      task=task, attempt=2, handle=_FakeHandle(tmp_path))
    t.join()
    assert res["session_ref"] == "s"


def test_mailbox_times_out_with_path_in_message(tmp_path):
    ex = mailbox.MailboxExecutor(tmp_path, timeouts={"scan.l4.card": 0.2}, poll_seconds=0.05)
    task = {"task_id": "scan.l4.card.600000", "role": "scan.l4.card", "output_artifact_ids": ["x"], "independent_context": False}
    with pytest.raises(TimeoutError, match="_dispatch/scan.l4.card.600000.a1.result.json"):
        ex.dispatch({"task_id": task["task_id"], "prompt": "p", "role": task["role"], "instruction_refs": [],
                     "input_artifact_ids": [], "output_artifact_ids": ["x"]}, task=task, attempt=1, handle=_FakeHandle(tmp_path))
```

- [ ] **Step 2: 跑测试确认失败** — Expected: FAIL(`ImportError`)

- [ ] **Step 3: 实现**

```python
# autoresearch/session_agent/executors/mailbox.py
"""host 模式执行器:runner 写请求文件,交互会话(Claude Code / Codex)读请求→派 agent→写结果文件。

驱动器只认 result 文件(不认会话自报);attempt 不匹配的旧结果忽略;超时抛 TimeoutError(runner 可重试一次)。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

DEFAULT_TIMEOUTS = {"macro.brief": 900, "sector.brief": 600, "scan.l3": 2400, "scan.l3.repair": 600,
                    "scan.l4.intel": 900, "scan.l4.card": 1800, "scan.l4.review": 1800}


def _dir(staging: Path) -> Path:
    d = Path(staging) / "_dispatch"; d.mkdir(parents=True, exist_ok=True); return d


def _atomic_write(path: Path, payload: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def write_result(staging, task_id, attempt, *, ok, session_ref, context_ref, parent_context_ref,
                 transcript_path, error) -> Path:
    p = _dir(Path(staging)) / f"{task_id}.a{attempt}.result.json"
    _atomic_write(p, {"schema_version": 1, "task_id": task_id, "attempt": int(attempt), "ok": bool(ok),
                      "session_ref": session_ref, "context_ref": context_ref,
                      "parent_context_ref": parent_context_ref, "transcript_path": transcript_path,
                      "error": error, "written_at": time.time()})
    return p


def wait_request(staging, *, timeout: float = 600.0, poll_seconds: float = 1.0) -> dict | None:
    """会话侧:阻塞直到出现一个尚无 result 的 request;返回其 JSON(带 request_path)。"""
    d = _dir(Path(staging)); deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for p in sorted(d.glob("*.request.json")):
            if not p.with_name(p.name.replace(".request.json", ".result.json")).exists():
                doc = json.loads(p.read_text(encoding="utf-8")); doc["request_path"] = str(p); return doc
        time.sleep(poll_seconds)
    return None


class MailboxExecutor:
    name = "mailbox"

    def __init__(self, staging, *, timeouts: dict | None = None, poll_seconds: float = 2.0):
        self.staging = Path(staging); self.timeouts = {**DEFAULT_TIMEOUTS, **(timeouts or {})}
        self.poll_seconds = poll_seconds

    def dispatch(self, request: dict, *, task: dict, attempt: int, handle) -> dict:
        from autoresearch.scan.user_config import resolve_agent_bundle   # model/effort 唯一解释
        tid = task["task_id"]; role = task["role"]
        bundle = resolve_agent_bundle().get("resolved", {})
        cfg_role = (request.get("config_role") or role.split(".")[-1])
        req_path = _dir(self.staging) / f"{tid}.a{attempt}.request.json"
        _atomic_write(req_path, {"schema_version": 1, "task_id": tid, "attempt": int(attempt), "role": role,
                                 "agent_type": _agent_type(role), **bundle.get(cfg_role, {}),
                                 "prompt": request.get("prompt") or _render_prompt(request, task, handle),
                                 "instruction_refs": request.get("instruction_refs", []),
                                 "input_paths": _paths(handle, request.get("input_artifact_ids", [])),
                                 "output_paths": _paths(handle, task.get("output_artifact_ids", [])),
                                 "timeout_seconds": self.timeouts.get(role, 1800)})
        res_path = req_path.with_name(req_path.name.replace(".request.json", ".result.json"))
        deadline = time.monotonic() + self.timeouts.get(role, 1800)
        while time.monotonic() < deadline:
            if res_path.exists():
                doc = json.loads(res_path.read_text(encoding="utf-8"))
                if int(doc.get("attempt", -1)) == int(attempt):
                    if not doc.get("ok"):
                        raise RuntimeError(f"executor reported failure: {doc.get('error')}")
                    return {"outputs": [{"artifact_id": a} for a in task.get("output_artifact_ids", [])],
                            "session_ref": doc["session_ref"], "context_ref": doc["context_ref"],
                            "parent_context_ref": doc.get("parent_context_ref"),
                            "evidence_refs": [f"transcript:{doc.get('transcript_path')}"] if doc.get("transcript_path") else []}
            time.sleep(self.poll_seconds)
        raise TimeoutError(f"等待 {tid} 的结果文件超时:{res_path}")


_AGENT_TYPES = {"macro.brief": "macro-brief", "sector.brief": "sector-brief", "scan.l3": "l3-rank",
                "scan.l3.repair": "l3-rank", "scan.l4.intel": "l4-intel", "scan.l4.card": "l4-card",
                "scan.l4.review": "l4-card"}


def _agent_type(role: str) -> str:
    return _AGENT_TYPES[role]


def _paths(handle, artifact_ids):
    from autoresearch.session_agent import artifacts
    return [str(artifacts.resolve_path(handle, a)) for a in artifact_ids]


def _render_prompt(request, task, handle) -> str:
    from autoresearch.session_agent.executors.common import render_prompt
    return render_prompt(request, task, handle)
```

```python
# autoresearch/session_agent/executors/common.py
"""两种执行器共用:角色 → agent 类型;派发 prompt 模板(与 legacy Workflow 逐字同款,别改词)。"""
from __future__ import annotations

from pathlib import Path

AGENT_TYPES = {"macro.brief": "macro-brief", "sector.brief": "sector-brief", "scan.l3": "l3-rank",
               "scan.l3.repair": "l3-rank", "scan.l4.intel": "l4-intel", "scan.l4.card": "l4-card",
               "scan.l4.review": "l4-card"}


def agent_type_for(role: str) -> str:
    return AGENT_TYPES[role]


def render_prompt(request: dict, task: dict, handle) -> str:
    """模板逐字取自 legacy Workflow(行号为 2026-09-26 main):
    macro.brief   ← scan-market.js:376   sector.brief ← scan-market.js:483
    scan.l3       ← scan-market.js:494   scan.l3.repair ← scan-market.js:520
    scan.l4.intel ← l4-stock.js:373      scan.l4.card ← l4-stock.js:468
    scan.l4.review← l4-stock.js:503(run{i} 用 attempt 与 subject 拼)
    实施时用 `sed -n '<行>p'` 把 JS 模板字面量原样复制进下面各分支;只把 ${SD}/${CTX}/${date}/${code}
    等占位换成 handle.staging / handle.context_root / handle.analysis_date / task["subject"]。
    """
    sd = Path(handle.staging); date = handle.analysis_date; subject = task.get("subject") or ""
    role = task["role"]
    if role == "macro.brief":
        return (f"读 {sd}/strategist_pack.json 的 pack 段,按你的人设写 {sd}/market_view.md"
                f"(六小节;前3描述性地形、后2仅 L5)。数字只出自该文件,不编;个股不评级、不锚定卡片。")
    if role == "sector.brief":
        sec = subject
        return (f"你是行业分析师。读 {handle.context_root}/sector/{date}/{sec}.json 写 {sd}/sector_briefs/{sec}.md,"
                f"单段机器契约(## 地形段 喂 L3/L4;纯事实性,不含方向判断)。零新取数。")
    if role == "scan.l3":
        l3cap = int(request.get("l3cap") or 10)      # 由 GATE1 回显(批 1.5 后是 l3cap 键)
        return _verbatim("scan-market.js", 494, sd=sd, date=date, l3cap=l3cap)
    if role == "scan.l3.repair":
        return (f"Read {sd}/_l3_repair_prompt.md,只处理其中列出的失败票;按文件内 schema 用 Write 写 "
                f"{sd}/_l3_repair_patch.json。不要读取任何全量 L3 输入或输出文件。")
    if role == "scan.l4.intel":
        meta = request.get("meta") or {}
        known = meta.get("dossier_summary") or ""
        known_base = (f"\n\n## 已知底(覆盖档案摘要·仅用于去重,**不是**查询方向指令)\n{known}\n\n"
                      f"已在上面出现的事实不必复查,查询额度全花在增量与新事件上。") if known else ""
        return (f"活体情报采集:{subject} {meta.get('name','')}({meta.get('sector','')})· 分析日 {date}。"
                f"按你的人设六面全查(≤{request.get('max_queries', 20)} 条),写 {sd}/_l4_intel_{subject}.md;"
                f"返回 code 与事件行数 events。{known_base}")
    if role == "scan.l4.card":
        return _verbatim("l4-stock.js", 468, sd=sd, code=subject)
    if role == "scan.l4.review":
        i = int(request.get("review_index") or 2)
        return _verbatim("l4-stock.js", 503, sd=sd, code=subject, i=i)
    raise KeyError(role)


def _verbatim(js: str, line: int, **fill) -> str:
    """占位:实施 Task 3 时把该行的模板字面量粘成 f-string 常量(见 docstring),本函数随即删除。
    留这个函数只是为了让 Task 3 的测试先红(`NotImplementedError`),防止漏搬。"""
    raise NotImplementedError(f"copy template from .claude/workflows/{js}:{line}")
```
(`evidence_refs` 的正式格式 `host-binding:<sha256>` 由 `bind-host-evidence` 产;host 模式下由会话在 `mailbox complete` 前调用 `bind-host-evidence --transcript-file <subagent jsonl>` 并把返回值填入 `--evidence-ref`;`write_result` 与 `dispatch` 相应多一个 `evidence_refs` 字段。)

- [ ] **Step 4: 跑测试确认通过** — Run: `uv run --no-sync pytest tests/session_agent/test_mailbox.py -q`;Expected: PASS
- [ ] **Step 5: 提交** — `git commit -m "feat(session_agent): mailbox executor + session-side wait/complete CLI (host mode)"`

---

### Task 4: 交互会话 host 循环写进 skill(scan-market)

**Files:**
- Modify: `.claude/skills/scan-market/SKILL.md`「流程」节(新增「session_v1 runner 路径」,legacy Workflow 路径整段移到 `docs/ops/scan-ops.md`「legacy Workflow 路径」)
- Modify: `docs/session-agent/README.md`(host 循环三条命令)
- Test: `tests/test_agent_defs.py`(锚:SKILL 含 `session_agent run --executor mailbox`、`mailbox wait`、`mailbox complete`;不含 `Workflow({scriptPath` 活指令)

- [ ] **Step 1: 写失败测试**

```python
def test_scan_skill_teaches_runner_host_loop_not_workflow_dispatch():
    skill = (SKILLS / "scan-market" / "SKILL.md").read_text(encoding="utf-8")
    for a in ("session_agent run --executor mailbox", "mailbox wait", "mailbox complete", "_dispatch/"):
        assert a in skill, f"SKILL.md 缺 host 循环锚「{a}」"
    live = [ln for ln in skill.splitlines() if "Workflow({scriptPath" in ln and "legacy" not in ln and "沿革" not in ln]
    assert not live, "SKILL.md 仍把 Workflow 派发当活指令"
```

- [ ] **Step 2: 跑测试确认失败**;**Step 3: 改写 SKILL.md 流程节**为:

```markdown
0. 开场(同前:trade_date → capsule begin → RUN_ID)。
1. **起 runner(后台)**:`uv run --no-sync python -m autoresearch.session_agent run --run-id "$RUN_ID" --executor mailbox --max-parallel 8 > $CTX/scan_runs/$RUN_ID/runner.log 2>&1 &`(用 Bash 的 run_in_background;runner 自己跑 frame/prelude/GATE1/sector pack/L3 prepare/lint/GATE2/L4 prep/任务簿/L5/finalize,零 agent)。
2. **host 循环**(重复直到 runner 日志出现 `finished`):`uv run --no-sync python -m autoresearch.session_agent mailbox wait --run-id "$RUN_ID" --timeout 900` → 得到一个请求 JSON(agent_type/model/effort/prompt/output_paths)→ 用 `Agent(subagent_type=<agent_type>, prompt=<prompt>)` 派发(model/effort 已由 runner 解释在请求里)→ 拿到回传后 `bind-host-evidence …`(可选,复核任务必填)→ `mailbox complete --run-id "$RUN_ID" --task-id <task_id> --attempt <n> --session-ref <本会话 id> --context-ref <subagent id> [--parent-context-ref <本会话 id>]`。**并行**:`wait` 一次只给一个请求,但可以连续 `wait` 多次后在一条消息里同时派多个 Agent,再逐个 `complete`。
3. CP0–CP7 播报不变(素材路径同前);CP5 的 `l4_watch` 照挂。
4. runner `finished` 后:brief 转播 + `verify-report --level full`。
```
legacy 段落原样搬到 `docs/ops/scan-ops.md`「legacy Workflow 路径(LEGACY_ORCHESTRATION_FALLBACK)」。

- [ ] **Step 4**: `uv run --no-sync pytest tests/test_agent_defs.py tests/test_skill_docs_refs.py tests/test_doc_budgets.py -q` → PASS(SKILL.md 预算 17KB 内;超了把 CP 表以外的说明再压)。
- [ ] **Step 5: 提交** — `git commit -m "docs(scan): SKILL.md teaches the session_v1 runner + mailbox host loop; legacy workflow path moves to ops doc"`

---

### Task 5: 审计缺口修补(来自 Task 2 的「阻断」项)

**Files:** 视 Task 2 清单而定,预期落在 `session_agent/domain_ops.py`(`scan_l4_*`)、`session_agent/workflows.py`(scan 模板)、`session_agent/legacy_scan.py`。
- 每个缺口一个子任务:失败测试(合成 run 复现该分支)→ 实现 → 通过 → 提交。已知候选(写计划时从 JS 读出):① intel 三次瞬时重试 + `intel_resume`;② `intel_guard` REJECTED 后卡仍派(回退卡内网查);③ ow/sell 双复核触发规则与同档早止(`scan.review.decide` 已有,核对 `sell_review` 只向温和折回);④ slim 的 K 槽 tushare 信号量在 runner 并发下仍生效(`prepare_slim` 内已有,验证即可)。

---

### Task 6: 一场真跑(host 模式,干净新会话)= A1-0 ⑤

- [ ] **Step 1**: 交易日 21:10 后、stk_factor_pro 灌齐;新会话;按 Task 4 的 SKILL 流程跑 **09-2x** 数据日;记录墙钟。
- [ ] **Step 2**: 验收断言(全部机器读数):`token_usage.md` 的 `general-purpose` 行 **= 0**;研究 agent 数 = 1 macro + K sector + 1–2 l3 + N intel + N card(+复核);`capsule verify` 三结论;`verify-report --level full` 四项;brief 六节齐。
- [ ] **Step 3**: 成本对照:与 09-17 场($40.48)比,目标 ≤ $22(壳 $13.5 + 主会话大部分消失)。
- [ ] **Step 4**: 确定性对账:同日若也有 legacy 产物(不建议同日双跑;用 `capsule replay` 对本 run 冻结输入重放 L0–L2/L5)→ byte 相同。
- [ ] **Step 5**: 读数写 `docs/research/2026-09-2x-session-plan-vs-workflow-audit.md` 末尾与记忆;失败 → `superpowers:systematic-debugging`,修完**换新 run_id 新会话**再跑(会话纪律)。两场内不通 → 触发 §3.3 降级设计,本计划止于此。

---

### Task 7: Workflow 降级为 fallback + 计量口径

**Files:**
- Modify: `.claude/workflows/scan-market.js:1-10`、`l4-stock.js:1-10`(头注 `LEGACY_ORCHESTRATION_FALLBACK`,一句指路 runner)
- Modify: `docs/session-agent/acceptance.md`(scan FULL 真实 run_id 登记;host 模式 PASS)
- Modify: `autoresearch/trace/usage_harvest.py`(host 模式下 subagent 目录口径不变;确认 `_dispatch/` 不被当 transcript)
- Test: `tests/test_agent_defs.py::test_l4_stock_workflow_*` 现有锚保持(JS 仍在);新增 `test_workflows_are_labeled_fallback`。

- [ ] 步骤同 Task 4 的 RED→GREEN→提交模式;提交信息 `chore(workflows): label JS workflows as LEGACY_ORCHESTRATION_FALLBACK after runner real run`。

---

### Task 8: Codex 会话一场真跑(host 模式)

- [ ] `AUTORESEARCH_ENGINE=codex`,Codex 会话按同一 SKILL 流程(Codex 用 shell 跑 `mailbox wait/complete`,派其 project agent);验收断言同 Task 6;登记 acceptance.md。Codex 独有的 `.codex/hooks.json` 重开一次才生效(记忆 `codex-hooks-mechanics-20260915`)。

---

## 自检

- 覆盖 spec A1-1(邮箱)✓ T3、A1-2/3/4(确定性阶段 + L4 链 + L5 = session_agent 既有 + T2/T5 补洞)、A1-5 ✓ T4、A1-6(边界事件由 service 记)、A1-7 计量 ✓ T7、A1-8 ✓ T7、A1-9 ✓ T8、A1-0 ⑤ ✓ T6。
- 未做(有意):headless 执行器(批 4);薄驱动器降级设计(只在 T6 两场不通时写)。
