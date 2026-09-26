# 日报引擎 · 批 4:headless 执行器 + launchd 调度 + 送达 + 五日无人值守验收

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交易日 21:20 由 launchd 触发,runner 用 `claude -p --agent <role>` 兑现全部判断任务,23:00 前把 `brief.md` 推到手机;人只读 brief,只在 FAILED 通知时介入;连续 5 个交易日成功。

**Architecture:** 在批 2–3 的 runner + 执行器协议之上加第二个执行器 `executors/headless_claude.py`:每个推理任务一个 `claude -p` 子进程(= 独立上下文,天然满足「独立复核」的 context 隔离),结果 JSON 自带 usage/成本,transcript 在 `~/.claude/projects/<slug>/<session-id>.jsonl`(探针 ③),用 `bind-host-evidence` 绑定成 `host-binding:<sha256>`。守护脚本 `scripts/scan_run.sh` 负责:等 tushare 灌齐 → `capsule begin` → `session_agent run --executor headless` → 送达 → 失败通知。送达首发 Bark(一条 curl,token 放 `.env`)。

**Tech Stack:** Python subprocess/threading、`claude` CLI 2.1.283(`-p --agent --effort --output-format json --json-schema --permission-mode bypassPermissions --session-id --max-turns`)、launchd plist、zsh、Bark HTTP。

**Spec:** `docs/superpowers/specs/2026-09-26-daily-engine-consolidation-design.md` §6 C1–C5;探针 `docs/research/2026-09-26-headless-driver-probes.md`。

## Global Constraints

- headless 只在 launchd 下跑;交互会话仍走 mailbox(批 3)。两者共用 runner,执行器可插拔。
- `claude -p` 调用必须带 `--session-id <uuid>`(transcript 可定位)、`--permission-mode bypassPermissions`(项目 hook 仍生效:探针②证实输入边界 hook 在 headless 下拦截越界 Read)、`--max-turns`(角色档位)、`--output-format json`。**不传** `--dangerously-skip-permissions`。
- 订阅额度:每场 headless 目标 ≤ $15 等价(`total_cost_usd` 之和);超预算只告警(现有 budgets 规则),不截候选。
- 失败策略:阶段级重试一次(runner 的 TIMEOUT 语义);再失败 → `capsule finalize FAILED` + 推送;**不自动改代码、不自动重跑第二场**。
- 并发锁:`flock $CTX/.scan_run.lock`,与人工会话互斥;人工会话开扫前若锁在 → 拒绝并提示。
- 送达失败不影响 run 状态,只落 `_delivery.json`。
- 凭证:Bark token 只在 `.env`(已 gitignore);代码里零字面量。
- 提交末尾 `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`。

## Review Focus

1. **`claude -p` 卡住不退出**(网络/限流):子进程必须有 `timeout=` 且超时 `kill()` 整个进程组,不能只 kill 父进程留下孙进程(Task 1 用 `start_new_session=True` + `os.killpg`)。
2. **结果 JSON 非法或 `is_error:true`**:视为本 attempt 失败,原样把 `result` 前 500 字进 `fail` 消息;不解析成功分支(Task 1)。
3. **产物没落盘但进程 0 退出**(agent 自称写了):执行器在返回前必须 `stat` 每个 `output_paths`,缺文件 = 失败(Task 1)。
4. **两场重叠**(21:20 那场没跑完,人工又开一场):锁必须让第二个进程立刻退出并打印持锁 pid(Task 3)。
5. **推送把 3KB brief 截断**:Bark 正文上限约 4KB;超限只推前 3000B + 报告路径,不报错(Task 4)。

---

### Task 1: headless 执行器

**Files:**
- Create: `autoresearch/session_agent/executors/headless_claude.py`
- Modify: `autoresearch/session_agent/__main__.py`(`run --executor headless`)
- Test: `tests/session_agent/test_headless_claude.py`(用假 `claude` 可执行文件 —— 测试写一个 shell 脚本到 tmp_path,按参数回不同 JSON)

**Interfaces:**
- Consumes: `executors.base.InferenceExecutor`(批 2 T1),`user_config.resolve_agent_bundle`,`hosts.base.render_request` 的请求 dict(`instruction_refs`/`input_artifact_ids`/`output_artifact_ids`/`role`),批 3 的 `_render_prompt`、`_agent_type`(从 `executors/mailbox.py` 提到 `executors/common.py` 共用)。
- Produces: `HeadlessClaudeExecutor(staging, *, claude_bin="claude", timeouts=DEFAULT_TIMEOUTS, max_turns=MAX_TURNS, transcript_root=~/.claude/projects/<slug>)`;`dispatch()` 返回 `{"outputs", "session_ref": <session-id>, "context_ref": <session-id>, "parent_context_ref": None, "evidence_refs": [], "usage": {...}, "cost_usd": float, "transcript_path": str}`;并把每次调用写 `<staging>/_headless/<task_id>.a<attempt>.json`(argv 脱敏、usage、cost、exit、耗时)。

- [ ] **Step 1: 写失败测试**

```python
# tests/session_agent/test_headless_claude.py
import json, os, stat, textwrap
import pytest
from autoresearch.session_agent.executors import headless_claude as hc


def _fake_claude(tmp_path, body: str) -> str:
    p = tmp_path / "claude"
    p.write_text("#!/bin/sh\n" + body, encoding="utf-8"); p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def _task(tmp_path):
    out = tmp_path / "staging" / "details" / "600000.md"
    return ({"task_id": "scan.l4.card.600000", "role": "scan.l4.card", "output_artifact_ids": ["card.600000"],
             "independent_context": False}, out)


def test_dispatch_parses_json_verifies_output_and_records_call(tmp_path, monkeypatch):
    task, out = _task(tmp_path)
    body = textwrap.dedent(f"""
        mkdir -p {out.parent}; printf 'x' > {out}
        echo '{{"is_error":false,"session_id":"abc","total_cost_usd":0.5,"usage":{{"output_tokens":9}},"result":"ok"}}'
    """)
    ex = hc.HeadlessClaudeExecutor(tmp_path / "staging", claude_bin=_fake_claude(tmp_path, body),
                                   timeouts={"scan.l4.card": 5}, transcript_root=tmp_path)
    monkeypatch.setattr(hc, "_artifact_path", lambda handle, a: out)
    res = ex.dispatch({"role": "scan.l4.card", "prompt": "p", "instruction_refs": [], "input_artifact_ids": [],
                       "output_artifact_ids": ["card.600000"], "config_role": "l4_card"},
                      task=task, attempt=1, handle=None)
    assert res["cost_usd"] == 0.5 and res["context_ref"] == res["session_ref"]
    rec = json.loads((tmp_path / "staging" / "_headless" / "scan.l4.card.600000.a1.json").read_text())
    assert rec["exit_code"] == 0 and "--agent" in rec["argv"] and "l4-card" in rec["argv"]


def test_dispatch_fails_when_output_missing_even_if_exit_zero(tmp_path, monkeypatch):
    task, out = _task(tmp_path)
    ex = hc.HeadlessClaudeExecutor(tmp_path / "staging", claude_bin=_fake_claude(tmp_path,
        'echo \'{"is_error":false,"session_id":"abc","total_cost_usd":0.1,"usage":{},"result":"wrote it"}\''),
        timeouts={"scan.l4.card": 5}, transcript_root=tmp_path)
    monkeypatch.setattr(hc, "_artifact_path", lambda handle, a: out)
    with pytest.raises(RuntimeError, match="产物未落盘"):
        ex.dispatch({"role": "scan.l4.card", "prompt": "p", "instruction_refs": [], "input_artifact_ids": [],
                     "output_artifact_ids": ["card.600000"], "config_role": "l4_card"}, task=task, attempt=1, handle=None)


def test_dispatch_times_out_and_kills_process_group(tmp_path, monkeypatch):
    task, out = _task(tmp_path)
    ex = hc.HeadlessClaudeExecutor(tmp_path / "staging", claude_bin=_fake_claude(tmp_path, "sleep 30 & wait"),
                                   timeouts={"scan.l4.card": 0.5}, transcript_root=tmp_path)
    monkeypatch.setattr(hc, "_artifact_path", lambda handle, a: out)
    with pytest.raises(TimeoutError):
        ex.dispatch({"role": "scan.l4.card", "prompt": "p", "instruction_refs": [], "input_artifact_ids": [],
                     "output_artifact_ids": ["card.600000"], "config_role": "l4_card"}, task=task, attempt=1, handle=None)


def test_is_error_json_is_failure_with_result_excerpt(tmp_path, monkeypatch):
    task, out = _task(tmp_path)
    ex = hc.HeadlessClaudeExecutor(tmp_path / "staging", claude_bin=_fake_claude(tmp_path,
        'echo \'{"is_error":true,"session_id":"abc","result":"rate limited"}\''),
        timeouts={"scan.l4.card": 5}, transcript_root=tmp_path)
    monkeypatch.setattr(hc, "_artifact_path", lambda handle, a: out)
    with pytest.raises(RuntimeError, match="rate limited"):
        ex.dispatch({"role": "scan.l4.card", "prompt": "p", "instruction_refs": [], "input_artifact_ids": [],
                     "output_artifact_ids": ["card.600000"], "config_role": "l4_card"}, task=task, attempt=1, handle=None)
```

- [ ] **Step 2: 跑测试确认失败** — Run: `uv run --no-sync pytest tests/session_agent/test_headless_claude.py -q`;Expected: FAIL(`ImportError`)

- [ ] **Step 3: 实现**

```python
# autoresearch/session_agent/executors/headless_claude.py
"""headless 执行器:每个推理任务一个 `claude -p --agent <role>` 子进程(独立上下文)。

探针(docs/research/2026-09-26-headless-driver-probes.md):--agent 装载项目 agent 定义 + hook,
结果 JSON 自带 usage/total_cost_usd/session_id,transcript 在 ~/.claude/projects/<slug>/<session-id>.jsonl。
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
import uuid
from pathlib import Path

from autoresearch.session_agent.executors.common import agent_type_for, render_prompt

DEFAULT_TIMEOUTS = {"macro.brief": 900, "sector.brief": 600, "scan.l3": 2400, "scan.l3.repair": 600,
                    "scan.l4.intel": 900, "scan.l4.card": 1800, "scan.l4.review": 1800}
MAX_TURNS = {"macro.brief": 20, "sector.brief": 15, "scan.l3": 40, "scan.l3.repair": 15,
             "scan.l4.intel": 40, "scan.l4.card": 60, "scan.l4.review": 60}


def _artifact_path(handle, artifact_id: str) -> Path:
    from autoresearch.session_agent import artifacts
    return Path(artifacts.resolve_path(handle, artifact_id))


def _slug(repo_root: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(repo_root))


class HeadlessClaudeExecutor:
    name = "headless"

    def __init__(self, staging, *, claude_bin: str = "claude", timeouts: dict | None = None,
                 max_turns: dict | None = None, transcript_root: Path | None = None):
        self.staging = Path(staging); self.claude_bin = claude_bin
        self.timeouts = {**DEFAULT_TIMEOUTS, **(timeouts or {})}
        self.max_turns = {**MAX_TURNS, **(max_turns or {})}
        self.transcript_root = Path(transcript_root) if transcript_root else (
            Path.home() / ".claude" / "projects" / _slug(Path.cwd()))

    def dispatch(self, request: dict, *, task: dict, attempt: int, handle) -> dict:
        from autoresearch.scan.user_config import resolve_agent_bundle
        role = task["role"]; tid = task["task_id"]
        bundle = (resolve_agent_bundle().get("resolved") or {}).get(request.get("config_role") or "", {})
        session_id = str(uuid.uuid4())
        prompt = request.get("prompt") or render_prompt(request, task, handle)
        argv = [self.claude_bin, "-p", "--agent", agent_type_for(role), "--output-format", "json",
                "--permission-mode", "bypassPermissions", "--session-id", session_id,
                "--max-turns", str(self.max_turns.get(role, 40))]
        if bundle.get("effort"):
            argv += ["--effort", str(bundle["effort"])]
        if bundle.get("model"):
            argv += ["--model", str(bundle["model"])]
        argv.append(prompt)
        t0 = time.monotonic()
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                start_new_session=True, cwd=str(Path.cwd()))
        try:
            out, err = proc.communicate(timeout=self.timeouts.get(role, 1800))
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL); proc.communicate()
            self._record(tid, attempt, argv, None, -9, time.monotonic() - t0, error="timeout")
            raise TimeoutError(f"{tid}: claude -p 超时 {self.timeouts.get(role)}s(session {session_id})")
        elapsed = time.monotonic() - t0
        try:
            doc = json.loads(out.strip().splitlines()[-1]) if out.strip() else {}
        except json.JSONDecodeError:
            doc = {}
        self._record(tid, attempt, argv, doc, proc.returncode, elapsed, error=(err or "")[:500] or None)
        if proc.returncode != 0 or not doc or doc.get("is_error"):
            raise RuntimeError(f"{tid}: claude -p 失败 exit={proc.returncode} {str(doc.get('result') or err)[:500]}")
        missing = [str(_artifact_path(handle, a)) for a in task.get("output_artifact_ids", [])
                   if not _artifact_path(handle, a).is_file()]
        if missing:
            raise RuntimeError(f"{tid}: 产物未落盘 {missing}(agent 自称完成不算数)")
        sid = doc.get("session_id") or session_id
        return {"outputs": [{"artifact_id": a} for a in task.get("output_artifact_ids", [])],
                "session_ref": sid, "context_ref": sid, "parent_context_ref": None, "evidence_refs": [],
                "usage": doc.get("usage") or {}, "cost_usd": float(doc.get("total_cost_usd") or 0.0),
                "transcript_path": str(self.transcript_root / f"{sid}.jsonl")}

    def _record(self, tid, attempt, argv, doc, exit_code, elapsed, *, error=None):
        d = self.staging / "_headless"; d.mkdir(parents=True, exist_ok=True)
        safe_argv = argv[:-1] + ["<prompt %d chars>" % len(argv[-1])]
        (d / f"{tid}.a{attempt}.json").write_text(json.dumps({
            "argv": safe_argv, "exit_code": exit_code, "elapsed_s": round(elapsed, 1),
            "usage": (doc or {}).get("usage"), "cost_usd": (doc or {}).get("total_cost_usd"),
            "session_id": (doc or {}).get("session_id"), "error": error}, ensure_ascii=False, indent=1),
            encoding="utf-8")
```

- [ ] **Step 4: 跑测试确认通过**;**Step 5: 提交** — `git commit -m "feat(session_agent): headless claude executor — one claude -p per inference task, output verified, calls recorded"`

---

### Task 2: 计量与证据:headless 会话进 usage_harvest 与 host 绑定

**Files:**
- Modify: `autoresearch/trace/usage_harvest.py`(新增 `--transcripts-from <staging>/_headless/*.json` 读 `session_id` → transcript 路径列表;`dispatcher` 列 `headless`)
- Modify: `autoresearch/session_agent/runner.py`(推理任务 submit 前若执行器返回 `transcript_path`,调 `service` 的 host 绑定:`bind-host-evidence` 等价函数,把 `host-binding:<sha256>` 放进 receipt)
- Test: `tests/trace/test_usage_harvest_headless.py`、`tests/session_agent/test_runner.py`(补 evidence 断言)

- [ ] **Step 1**: 失败测试:给 3 个假 `_headless/*.json`(含 session_id)与对应假 transcript(含 `message.usage`),`usage_harvest --transcripts-from` 输出表含 3 行、`dispatcher=headless`、成本合计等于三份 `total_cost_usd` 之和。
- [ ] **Step 2**: RED;**Step 3**: 实现(读 `_headless/*.json` 的 `session_id` 拼 transcript 路径;缺文件写 `UNMEASURED` 行,不写 $0);**Step 4**: GREEN;**Step 5**: 提交。

---

### Task 3: 守护脚本 + launchd

**Files:**
- Create: `scripts/scan_run.sh`
- Create: `scripts/com.tradingagents.scan-run.plist`(模板,`__REPO__` 占位同 prewarm)
- Modify: `scripts/nightly_close.sh`(改由 scan_run 成功后串行调用,或 plist 挪到 23:30;二选一,本计划选 **挪到 23:30**,plist 时间改一处)
- Modify: `autoresearch/scan/prewarm.py`(抽出 `stk_factor_pro` 就绪探针为 `autoresearch/scan/readiness.py: factor_rows_ready(date, *, min_rows=5300, stable_polls=2, interval_s=300, deadline="22:30") -> bool`)
- Test: `tests/scan/test_readiness.py`;`tests/scan/test_scan_run_script.py`(shell 脚本静态检查:`bash -n`、含 `flock`、含 `capsule begin`、含 `--executor headless`、失败分支含 `finalize --business-status FAILED`)

- [ ] **Step 1: 写失败测试**(脚本静态锚 + readiness 单测:行数从 5200→5300→5300 才 ready;deadline 到未稳定 → False)
- [ ] **Step 2: RED**;**Step 3: 实现**

```zsh
#!/bin/zsh -l
# scripts/scan_run.sh —— launchd 交易日 21:20:等湖灌齐 → capsule begin → runner(headless)→ 送达 → 通知。
set -u
cd "$(dirname "$0:A")/.." || exit 1
export AUTORESEARCH_ENGINE=claude
LOG_DIR=reports_claude/_ops; mkdir -p "$LOG_DIR"
DATE=$(uv run --no-sync python -m autoresearch.scan.trade_date) || exit 0      # 非交易日:静默退出
LOG="$LOG_DIR/scan_run_${DATE}.log"; exec >>"$LOG" 2>&1
echo "[scan-run] $(date '+%F %T') start date=$DATE"
exec 9>"context_claude/.scan_run.lock"
if ! flock -n 9; then echo "[scan-run] 另一场在跑(锁被持有),退出"; exit 0; fi
uv run --no-sync python -m autoresearch.scan.readiness "$DATE" --deadline 22:30 || { echo "[scan-run] 湖未灌齐,放弃"; scripts/notify.sh "扫描 $DATE 未开:tushare 未灌齐"; exit 1; }
RUN_JSON=$(uv run --no-sync python -m autoresearch.trace.capsule begin scan-market "$DATE" --engine claude \
  --config-file .claude/skills/scan-market/scan_config.jsonc --legacy-reason "headless runner(session_v1)") || exit 1
RUN_ID=$(printf '%s' "$RUN_JSON" | jq -r .run_id); export AUTORESEARCH_RUN_ID="$RUN_ID"
if uv run --no-sync python -m autoresearch.session_agent run --run-id "$RUN_ID" --executor headless; then
  REPORT=$(uv run --no-sync python -m autoresearch.session_agent status --run-id "$RUN_ID" | jq -r .canonical_report_dir)
  uv run --no-sync python -m autoresearch.scan.delivery "$REPORT/brief.md" --run-id "$RUN_ID"
else
  uv run --no-sync python -m autoresearch.trace.capsule finalize "$RUN_ID" --business-status FAILED \
    --error-json '{"stage":"runner","message":"see log"}' || true
  scripts/notify.sh "扫描 $DATE FAILED · run $RUN_ID · 日志 $LOG"
  exit 1
fi
echo "[scan-run] $(date '+%F %T') done"
```
plist:Weekday 1–5、Hour 21、Minute 20;`StandardOutPath` 指 `/tmp/scan-run.log`;安装命令写进 `docs/ops/scan-ops.md`。`nightly_close` plist 改 23:30。

- [ ] **Step 4: GREEN**;**Step 5: 提交** — `git commit -m "feat(ops): scan_run.sh + launchd 21:20 headless run; readiness probe; nightly_close moves to 23:30"`

---

### Task 4: 送达(Bark 首发;mail / file 备选)

**Files:**
- Create: `autoresearch/scan/delivery.py`(`send(brief_path, *, run_id, channel=None) -> dict`;channel 读 `scan_config.jsonc` 新块 `delivery: {channel: "none|bark|mail|file", file_dir: "..."}`;三件套进 `user_config.py`)
- Create: `scripts/notify.sh`(FAILED 通知,复用同一 channel)
- Test: `tests/scan/test_delivery.py`(bark:monkeypatch `urllib.request.urlopen`,断言 URL 含 token 来自 env、正文 ≤ 3000B 截断 + 附路径;file:写入目录;none:落 `_delivery.json` 但不发)

- [ ] **Step 1**: 失败测试(三 channel + 截断 + token 缺失时报错不发);**Step 2**: RED;**Step 3**: 实现(`BARK_TOKEN` 从 `os.environ`;`.env` 由现有加载路径带入;URL `https://api.day.app/<token>/<title>/<body>` 走 POST JSON 更稳);每次落 `<report_dir>/_delivery.json`;**Step 4**: GREEN;**Step 5**: 提交。

---

### Task 5: 首场 headless 真跑(人工触发脚本,不等 launchd)

- [ ] **Step 1**: 交易日 21:20 后手动 `scripts/scan_run.sh`;人不开任何会话。
- [ ] **Step 2**: 验收:brief 推送到达;`token_usage.md` 有 `dispatcher=headless` 行、general-purpose = 0、成本 ≤ $15 等价;`capsule verify` 三结论;`verify-report --level full` 四项;`_headless/*.json` 每个推理任务一份且 exit 0。
- [ ] **Step 3**: 读数写 `docs/research/2026-09-2x-headless-first-run.md`;失败 → systematic-debugging;修完次日再跑。

---

### Task 6: 五个交易日无人值守验收 + 收口

- [ ] **Step 1**: `launchctl bootstrap` 装 plist;连续 5 个交易日不开会话;每日早上只看推送与 `_ops/scan_run_<date>.log`。
- [ ] **Step 2**: 每日核对:23:00 前收到 brief;`completeness_ok`;人工介入 0。任一日 FAILED → 修复后计数归零。
- [ ] **Step 3**: 5/5 后:`docs/session-agent/acceptance.md` 登记 scan FULL 真实 run_id(headless);SKILL.md 加「无人值守模式」一节(≤10 行:plist 装卸、日志位置、手动触发、失败后怎么办);记忆更新。
- [ ] **Step 4**: 提交 — `docs(ops): five-day unattended acceptance recorded`

---

## 自检

- 覆盖 C0 ✓(探针已做)、C1 ✓ T1–T2、C2 ✓ T3、C3 ✓ T4、C5 ✓ T5–T6、C4(Codex headless 不做,接口留)✓。
- Review Focus 1–5 各在 T1/T3/T4 有测试。
