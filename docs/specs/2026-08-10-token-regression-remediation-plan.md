# Token 回归修复(方案 B)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让「prompts 缺失整轮盲跑」在派发前被拦死、事故重派不再重付 intel,使干净单轮回到基线(≤$35 / ≤8M 加权)。

**Architecture:** 全部是编排/护栏改动:①任务簿(`l4_tasks.py`)对 prompt 产物上两道硬门(init 拒绝 + preflight BLOCK);②prompts 从 20 分钟长命令壳迁入 init 短壳(幂等自愈);③intel 同日 crash-resume 信号由 preflight 计算、l4-stock.js 消费;④两件记账/运维卫生。**不触任何评级/rubric/主尺/effort 语义。**

**Tech Stack:** Python 3(pandas 无涉)、pytest、Claude Code workflow js(无文件系统访问,验证用 AsyncFunction 探针)。

**Spec:** `docs/specs/2026-08-10-token-regression-remediation-design.md`(方案 B,用户 2026-08-10 裁定;含前提修正:LEGACY 分支在壳命令里、macOS 无 GNU timeout 故不加超时)。

## Global Constraints

- 一切 python 命令用 `uv run --no-sync`(venv-only 的 akshare/tushare/lightgbm 会被裸 uv 误删);仓库根目录运行。
- **不动**:评级/rubric 三门/主尺 `gap_c1_o2`/finalist cap/intel `max_queries`/派发模式(一次性全派,fb_20260714_003)/`AGENT_DEFAULTS` 值。
- **跨日复用是禁区**(用户裁定 R5,2026-07-29):本计划的续传仅同 analysis_date 且 ≤24h。
- workflow js 改动后**禁止**只跑 `node --check`(假绿灯):必须跑 `uv run --no-sync python -m pytest tests/test_workflow_js_syntax.py`(AsyncFunction 探针,已存在)。
- macOS(Darwin):无 GNU `timeout`,不得在壳命令里使用。
- 测试写在 `tests/scan/`、`tests/trace/`,风格照抄邻居(`tmp_path` 夹具、直调函数、合成数据零网络)。
- 每个 Task 一个 commit;commit message 末尾带 harness 规定的 `Co-Authored-By:` 行(用你自己的模型署名,不要照抄本文档里的示例署名)。

---

### Task 1: C1a — `initialize()` prompt 硬门(缺 → `ok:false` 不落盘)

**Files:**
- Modify: `autoresearch/scan/l4_tasks.py`(`_artifact` 在 :57 附近加 helper;`initialize` :178–238;CLI `main` 的 init 分支 :620–638)
- Modify: `tests/scan/test_l4_tasks_caps.py:29-32`(`_init` 夹具)
- Modify: `tests/scan/test_l4_tasks.py:40-53`(`_book` 夹具)
- Modify: `tests/scan/test_structural_audit.py:40-42`(`_book` 夹具;先看它上方 fixture 是否已写 prompt,已写则跳过)
- Test: `tests/scan/test_l4_tasks_gate.py`(新建)

**Interfaces:**
- Produces: `_prompt_file_ok(scan_dir: Path, code: str) -> bool`(Task 2 复用);`initialize()` 失败返回体 `{"ok": False, "reason": str, "missing_prompts": list[str], "path": str, "n": int, "codes": list, "effective_cap": 0, "dispatch_batches": []}`(键齐全,scan-market.js 的 TASKS schema `required:['ok','path','dispatch_batches']` 照样通过,靠 `!tasks.ok` throw)。
- Consumes: 现有 `_artifact`/`_new_task`/`_locked`。

- [ ] **Step 1: 写失败测试**

新建 `tests/scan/test_l4_tasks_gate.py`:

```python
"""C1 硬门(design 2026-08-10-token-regression-remediation-design.md):
prompts 缺失必须在派发前拒绝 —— init 拒初始化、preflight 拒认领。合成,零网络。"""
from __future__ import annotations

import json
from pathlib import Path

from autoresearch.scan import l4_tasks

DATE = "2026-08-07"


def _write_prompt(tmp_path, code, body="# 任务包\nx" * 10):
    scan = tmp_path / DATE
    scan.mkdir(parents=True, exist_ok=True)
    (scan / f"_l4_prompt_{code}.md").write_text(body, encoding="utf-8")


def _init(tmp_path, codes):
    return l4_tasks.initialize(
        DATE, list(codes), root=tmp_path, context_root=tmp_path / "ctx")


def test_init_refuses_when_any_prompt_missing(tmp_path):
    _write_prompt(tmp_path, "600000")           # 600001 故意不写
    r = _init(tmp_path, ["600000", "600001"])
    assert r["ok"] is False
    assert r["missing_prompts"] == ["600001"]
    assert r["dispatch_batches"] == []
    # 硬门语义:拒绝时不得落盘任务簿(带病账本比没有账本更坏)
    assert not (tmp_path / DATE / "_l4_tasks.json").exists()


def test_init_refuses_on_empty_prompt_file(tmp_path):
    _write_prompt(tmp_path, "600000", body="")   # 0 字节 = 等同缺失
    r = _init(tmp_path, ["600000"])
    assert r["ok"] is False and r["missing_prompts"] == ["600000"]


def test_init_ok_when_all_prompts_present(tmp_path):
    for c in ("600000", "600001"):
        _write_prompt(tmp_path, c)
    r = _init(tmp_path, ["600000", "600001"])
    assert r["ok"] is True and r["n"] == 2
    assert (tmp_path / DATE / "_l4_tasks.json").exists()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_l4_tasks_gate.py -v`
Expected: 前两条 FAIL(现行 initialize 恒 `ok:true`),第三条 PASS。

- [ ] **Step 3: 最小实现**

`l4_tasks.py` 在 `_artifact`(:57)之后加:

```python
def _prompt_file_ok(scan_dir: Path, code: str) -> bool:
    """prompt 任务包在且非空 —— C1 硬门的唯一判据(直接 stat 文件,不信账本旧记录)。"""
    code6 = str(code).split(".")[0].zfill(6)
    p = Path(scan_dir) / f"_l4_prompt_{code6}.md"
    return p.is_file() and p.stat().st_size > 0
```

`initialize()` 里,在 `ordered = list(dict.fromkeys(...))` 之后、`with _locked(path):` **之前**插入(拒绝路径不进锁、零写盘):

```python
    # C1a(design 2026-08-10):prompts 是每张卡的任务包 —— 缺着派发 = 整轮盲跑
    # (2026-08-09 实跑 12 股 ≈$23 全废)。init 是派发前最后一个确定性闸口,在这里拒绝,
    # 一个 LLM token 都还没花。prompts 幂等(实测 3.5s),修复 = 重跑 prompts 再 init。
    missing_prompts = [c for c in ordered if not _prompt_file_ok(scan_dir, c)]
    if missing_prompts:
        return {
            "ok": False,
            "path": str(path),
            "n": len(ordered),
            "codes": ordered,
            "reason": (f"prompts 缺失 {len(missing_prompts)} 票 —— 拒绝初始化任务簿;"
                       f"先跑 `python -m autoresearch.scan.agents.l4_card prompts {date}`"),
            "missing_prompts": missing_prompts,
            "effective_cap": 0,
            "dispatch_batches": [],
        }
```

CLI `main` 的 init 分支(:637 `result["dispatch_batches"] = ...` 一行)改为条件挂载:

```python
        if result.get("ok"):
            result["dispatch_batches"] = dispatch_batches(result["path"])["batches"]
```

(失败体自带 `dispatch_batches: []`;`return 0 if result.get("ok") else 1` 现有行为使壳拿到 exit 1。)

- [ ] **Step 4: 跑新测试确认通过**

Run: `uv run --no-sync python -m pytest tests/scan/test_l4_tasks_gate.py -v`
Expected: 3 PASS

- [ ] **Step 5: 适配三个既有夹具(它们不写 prompt,现在会被硬门拒)**

`tests/scan/test_l4_tasks_caps.py` 的 `_init`(:29)改为:

```python
def _init(tmp_path, n=10, caps=None):
    codes = [f"{600000+i}" for i in range(n)]
    scan = tmp_path / "2026-08-06"
    scan.mkdir(parents=True, exist_ok=True)
    for c in codes:                                # C1a 硬门:prompts 必须在场
        (scan / f"_l4_prompt_{c}.md").write_text("# 任务包\n", encoding="utf-8")
    return l4_tasks.initialize("2026-08-06", codes, root=tmp_path,
                               context_root=tmp_path / "ctx", caps=caps)
```

`tests/scan/test_l4_tasks.py` 的 `_book`(:40)在 `return initialize(` 之前插入:

```python
    scan = tmp_path / DATE
    scan.mkdir(parents=True, exist_ok=True)
    for c in codes:
        (scan / f"_l4_prompt_{c}.md").write_text("# 任务包\n", encoding="utf-8")
```

`tests/scan/test_structural_audit.py` 的 `_book`(:40):先读它上方的产物 fixture(:20-37 一带)——若已写 `_l4_prompt_{code}.md` 则不动;未写则同样在 `_book` 里补一行。

- [ ] **Step 6: 全量跑受影响测试**

Run: `uv run --no-sync python -m pytest tests/scan/test_l4_tasks_gate.py tests/scan/test_l4_tasks_caps.py tests/scan/test_l4_tasks.py tests/scan/test_structural_audit.py tests/scan/test_l4_dispatch_pack.py -q`
Expected: 全 PASS(dispatch_pack 若因夹具同病变红,同法补 prompt 文件)。

- [ ] **Step 7: Commit**

```bash
git add autoresearch/scan/l4_tasks.py tests/scan/test_l4_tasks_gate.py tests/scan/test_l4_tasks_caps.py tests/scan/test_l4_tasks.py tests/scan/test_structural_audit.py tests/scan/test_l4_dispatch_pack.py
git commit -m "feat(scan): C1a init prompt 硬门 —— prompts 缺失拒绝初始化任务簿(防整轮盲跑)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: C1b — `preflight` 接管 bookless 分支 + PROMPT_MISSING BLOCK

**Files:**
- Modify: `autoresearch/scan/l4_tasks.py`(`preflight` :277 函数体头部)
- Test: `tests/scan/test_l4_tasks_gate.py`(追加)

**Interfaces:**
- Consumes: Task 1 的 `_prompt_file_ok`。
- Produces: `preflight()` 新返回可能:`{"ok": True, "code", "action": "BLOCKED", "attempt": 0, "reason": "PROMPT_MISSING"}`(有无任务簿都可能)与 `{"ok": True, "code", "action": "LEGACY", "attempt": 0, "reason": "NO_TASK_BOOK"}`(bookless 且 prompt 在)。**既有 RUN/SKIP/WAIT/BLOCKED 分支的行为与返回体逐字节不变**。Task 4 的 js 依赖 `action` 值域 `{RUN,SKIP,WAIT,BLOCKED,LEGACY}`。

- [ ] **Step 1: 写失败测试**(追加到 `tests/scan/test_l4_tasks_gate.py`)

```python
def test_preflight_blocks_on_missing_prompt_without_claiming(tmp_path):
    for c in ("600000",):
        _write_prompt(tmp_path, c)
    _init(tmp_path, ["600000"])
    book = tmp_path / DATE / "_l4_tasks.json"
    (tmp_path / DATE / "_l4_prompt_600000.md").unlink()   # init 后 prompt 被删(事故模拟)
    r = l4_tasks.preflight(book, "600000")
    assert r == {"ok": True, "code": "600000", "action": "BLOCKED",
                 "attempt": 0, "reason": "PROMPT_MISSING"}
    payload = json.loads(book.read_text(encoding="utf-8"))
    assert payload["tasks"]["600000"]["status"] == "PENDING"   # 未认领、未污染账本


def test_preflight_legacy_when_no_book(tmp_path):
    _write_prompt(tmp_path, "600000")                     # prompt 在、任务簿不在(SENTINEL_PINNED 路)
    book = tmp_path / DATE / "_l4_tasks.json"
    r = l4_tasks.preflight(book, "600000")
    assert r["ok"] is True and r["action"] == "LEGACY" and r["reason"] == "NO_TASK_BOOK"


def test_preflight_blocks_on_missing_prompt_even_bookless(tmp_path):
    (tmp_path / DATE).mkdir(parents=True, exist_ok=True)  # 目录在、prompt 与任务簿都不在
    book = tmp_path / DATE / "_l4_tasks.json"
    r = l4_tasks.preflight(book, "600000")
    assert r["action"] == "BLOCKED" and r["reason"] == "PROMPT_MISSING"


def test_preflight_run_path_unchanged(tmp_path):
    _write_prompt(tmp_path, "600000")
    _init(tmp_path, ["600000"])
    book = tmp_path / DATE / "_l4_tasks.json"
    r = l4_tasks.preflight(book, "600000")
    assert r["action"] == "RUN" and r["attempt"] == 1      # 既有认领行为不变
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_l4_tasks_gate.py -v -k preflight`
Expected: 前三条 FAIL(bookless 现在直接 FileNotFoundError / 无 prompt 检查),第四条 PASS。

- [ ] **Step 3: 最小实现**

`preflight()` 函数体最前(`path = Path(book)`、`code6 = ...` 两行之后,`with _locked(path):` 之前)插入:

```python
    # C1b(design 2026-08-10):prompt 任务包是出卡的前提 —— 缺着认领 = 盲卡。
    # 无论有无任务簿都在这里拦(SENTINEL_PINNED 路不建账本,这是它唯一的每股闸口)。
    # 不认领、不写盘:BLOCK 是「别跑」,不是一次失败。
    scan_dir = path.parent
    if not _prompt_file_ok(scan_dir, code6):
        return {"ok": True, "code": code6, "action": "BLOCKED",
                "attempt": 0, "reason": "PROMPT_MISSING"}
    # bookless(直接单独重跑单股 workflow / SENTINEL_PINNED):python 接管原壳命令里的
    # `else echo LEGACY` 分支(壳零判断铁律)——行为与旧壳逐字节等价。
    if not path.exists():
        return {"ok": True, "code": code6, "action": "LEGACY",
                "attempt": 0, "reason": "NO_TASK_BOOK"}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run --no-sync python -m pytest tests/scan/test_l4_tasks_gate.py -v`
Expected: 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add autoresearch/scan/l4_tasks.py tests/scan/test_l4_tasks_gate.py
git commit -m "feat(scan): C1b preflight 验 prompt(缺→BLOCKED 不认领)+ python 接管 bookless LEGACY

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: C3 — `intel_status.resumable()` / `resumed` 字段 / preflight 输出 `intel_resume`

**Files:**
- Modify: `autoresearch/scan/l4/intel_status.py`(dataclass :52-66 加字段;文件尾部加 `resumable`/`mark_resumed`)
- Modify: `autoresearch/scan/l4_tasks.py`(`preflight` 的 RUN 与 LEGACY 返回体)
- Test: `tests/scan/test_intel_status.py`(追加)、`tests/scan/test_l4_tasks_gate.py`(追加)

**Interfaces:**
- Produces:
  - `intel_status.resumable(scan_dir, code, *, now: float | None = None) -> bool`
  - `intel_status.mark_resumed(scan_dir, code) -> None`
  - `IntelStatus.resumed: bool = False`(`to_dict()` 走 `asdict` 自动带上;`load_status` 对旧文件无此键 → dataclass 默认 False,向后兼容)
  - `preflight()` 的 RUN/LEGACY 返回体新增 `"intel_resume": bool`
- Consumes: 既有 `load_status`/`write_status`/`status_path`。

- [ ] **Step 1: 写失败测试**(追加到 `tests/scan/test_intel_status.py`)

```python
# ────────────────────── C3:同日断点续传(design 2026-08-10)──────────────────────
import os
import time

from autoresearch.scan.l4.intel_status import mark_resumed, resumable


def _resume_fixture(tmp_path, code="600000", acquisition="FULL",
                    availability="INTEL", error=None, draft=True):
    st = IntelStatus(code=code, acquisition=acquisition, guard="KEPT",
                     availability_for_card=availability, attempts=1, error_class=error)
    write_status(tmp_path, st)
    if draft:
        (tmp_path / f"_l4_intel_{code}.md").write_text("| 稿 |", encoding="utf-8")
    return st


def test_resumable_happy_path(tmp_path):
    _resume_fixture(tmp_path)
    assert resumable(tmp_path, "600000") is True


def test_resumable_rejects_missing_draft(tmp_path):
    _resume_fixture(tmp_path, draft=False)          # 只有 status 没有稿(如 .rejected.md 改名后)
    assert resumable(tmp_path, "600000") is False


def test_resumable_rejects_degraded_or_error(tmp_path):
    _resume_fixture(tmp_path, acquisition="DEGRADED", availability="CARD_FALLBACK")
    assert resumable(tmp_path, "600000") is False


def test_resumable_rejects_stale_over_24h(tmp_path):
    _resume_fixture(tmp_path)
    old = time.time() - 25 * 3600                    # 「隔了几天重放同一历史扫描日」场景
    for name in ("_l4_intel_600000.md", "_l4_intel_status_600000.json"):
        os.utime(tmp_path / name, (old, old))
    assert resumable(tmp_path, "600000") is False


def test_mark_resumed_roundtrip_and_backward_compat(tmp_path):
    _resume_fixture(tmp_path)
    mark_resumed(tmp_path, "600000")
    st = load_status(tmp_path, "600000")
    assert st is not None and st.resumed is True
    # 旧文件无 resumed 键 → 默认 False(load_status 不得因新字段挂掉)
    raw = json.loads((tmp_path / "_l4_intel_status_600000.json").read_text())
    raw.pop("resumed")
    (tmp_path / "_l4_intel_status_600000.json").write_text(json.dumps(raw))
    st2 = load_status(tmp_path, "600000")
    assert st2 is not None and st2.resumed is False
```

追加到 `tests/scan/test_l4_tasks_gate.py`:

```python
def test_preflight_run_carries_intel_resume(tmp_path):
    from autoresearch.scan.l4.intel_status import IntelStatus, write_status
    _write_prompt(tmp_path, "600000")
    _init(tmp_path, ["600000"])
    scan = tmp_path / DATE
    write_status(scan, IntelStatus(code="600000", acquisition="FULL", guard="KEPT",
                                   availability_for_card="INTEL", attempts=1))
    (scan / "_l4_intel_600000.md").write_text("| 稿 |", encoding="utf-8")
    r = l4_tasks.preflight(scan / "_l4_tasks.json", "600000")
    assert r["action"] == "RUN" and r["intel_resume"] is True
    # 披露:被消费的续传要在 status 里留痕
    from autoresearch.scan.l4.intel_status import load_status
    assert load_status(scan, "600000").resumed is True


def test_preflight_intel_resume_false_when_no_status(tmp_path):
    _write_prompt(tmp_path, "600000")
    _init(tmp_path, ["600000"])
    r = l4_tasks.preflight(tmp_path / DATE / "_l4_tasks.json", "600000")
    assert r["action"] == "RUN" and r["intel_resume"] is False   # 正常首跑 parity
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_intel_status.py -v -k "resum" ; uv run --no-sync python -m pytest tests/scan/test_l4_tasks_gate.py -v -k intel_resume`
Expected: 全 FAIL(`ImportError: resumable`)。

- [ ] **Step 3: 实现 intel_status 侧**

dataclass 字段区(`note: str = ""` 之后)加一行:

```python
    resumed: bool = False   # C3:同日 crash-resume 消费过本稿(披露用,不改任何判定)
```

文件尾部(`main` 之前)加:

```python
# ────────────────────── C3:同日断点续传(design 2026-08-10)──────────────────────
RESUME_MAX_AGE_S = 24 * 3600   # 同 analysis_date 且 ≤24h;跨日/重放历史日 → 必须重盲搜


def _draft_path(scan_dir: Path | str, code: str) -> Path:
    code6 = str(code).split(".")[0].zfill(6)
    return Path(scan_dir) / f"_l4_intel_{code6}.md"


def resumable(scan_dir: Path | str, code: str, *, now: float | None = None) -> bool:
    """同日 crash-resume 判定:稿在 + status 说这稿可用 + 两者都不陈旧。

    与 R5(跨日卡 TTL 复用退役)的边界:这里只认「本 analysis_date 目录里、24h 内、
    guard 未拒」的稿 —— 语义与任务簿对卡的 VERIFIED_SUCCESS 跳过同族(crash-resume),
    不是跨日新鲜度妥协。条件不满足一律 False(重盲搜),失败闭合。
    """
    st = load_status(scan_dir, code)
    if st is None or st.acquisition != "FULL":
        return False
    if st.availability_for_card != "INTEL" or st.error_class:
        return False
    draft = _draft_path(scan_dir, code)
    if not draft.is_file() or draft.stat().st_size == 0:
        return False
    import time as _time
    ref = _time.time() if now is None else now
    for p in (draft, status_path(scan_dir, code)):
        if ref - p.stat().st_mtime > RESUME_MAX_AGE_S:
            return False
    return True


def mark_resumed(scan_dir: Path | str, code: str) -> None:
    """把「这稿被续传消费过」落进 status(报告/T1 可见,不伪装成新鲜盲搜)。"""
    st = load_status(scan_dir, code)
    if st is None:
        return
    st.resumed = True
    write_status(scan_dir, st)
```

- [ ] **Step 4: 实现 preflight 侧**

`l4_tasks.py` `preflight()`:在 Task 2 插入的 bookless-LEGACY return **之前**算一次(两个出口共用):

```python
    def _intel_resume() -> bool:
        # 续传是省钱件不是正确性件:判定失败一律不续传(失败闭合)。但**降级必须留痕** ——
        # 项目铁律「降级不留痕才是真病」:裸 except Exception 会把 resumable() 里的真 bug
        # (打错字/schema 变更)吞成"今天恰好没得续传",省不到钱还没人知道。故只吞 IO/解析类,
        # 且吞之前先喊一声。
        from autoresearch.scan.l4.intel_status import mark_resumed, resumable
        try:
            if not resumable(scan_dir, code6):
                return False
            mark_resumed(scan_dir, code6)   # 披露先于消费;幂等
            return True
        except (OSError, ValueError, TypeError, KeyError) as exc:
            print(f"[l4_tasks] intel 续传判定失败({type(exc).__name__}: {exc})→ 本票照常盲搜",
                  file=sys.stderr)
            return False
```

> 注:`sys` 已在 `l4_tasks.py` 顶部导入则直接用;未导入则补 `import sys`。**不要**改回裸 `except Exception` —— 那正是本仓 `data-contracts-fail-fast` 铁律点名的病。

LEGACY 返回体加 `"intel_resume": _intel_resume()`;函数末尾的 RUN 返回体(`"reason": reason or "PENDING"` 那个 dict)同样加 `"intel_resume": _intel_resume()`。SKIP/WAIT/BLOCKED 不加(那些分支不出卡)。

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run --no-sync python -m pytest tests/scan/test_intel_status.py tests/scan/test_l4_tasks_gate.py -q`
Expected: 全 PASS。

- [ ] **Step 6: Commit**

```bash
git add autoresearch/scan/l4/intel_status.py autoresearch/scan/l4_tasks.py tests/scan/test_intel_status.py tests/scan/test_l4_tasks_gate.py
git commit -m "feat(scan): C3 intel 同日断点续传 —— resumable/mark_resumed + preflight 输出 intel_resume(≤24h,跨日永不,R5 原判不动)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: C2 + C1b/C3 的 workflow js 接线

**Files:**
- Modify: `.claude/workflows/scan-market.js`(:358-366 l4-prep 壳;:381 legacy GATE3;:397-398 init 壳与 throw)
- Modify: `.claude/workflows/l4-stock.js`(:101-103 TASK_ACTION;:122-126 preflight 壳;:185 intel 派发;:187-213 intel 后处理)
- Test: 既有 `tests/test_workflow_js_syntax.py`(AsyncFunction 探针 + 定义/调用点核对,自动扫全部 workflow)

**Interfaces:**
- Consumes: Task 2/3 的 preflight 返回体(`action ∈ {RUN,SKIP,WAIT,BLOCKED,LEGACY}`、`intel_resume`)。
- Produces: 无新接口;行为变化 = prompts 自愈重建 + init 失败带 reason 抛出 + 续传时跳过 intel 三步。

- [ ] **Step 1: scan-market.js 三处**

:360-366 的 l4-prep 命令,删掉末尾 prompts(`wait; ` 后直接结束):

```js
  `${R} autoresearch.scan.agents.l4_card shared ${date}; ` +
  `( ${R} autoresearch.scan.agents.l4_card pledge ${date} || true ) & ` +
  `( ${R} autoresearch.scan.agents.l4_card seats ${date} || true ) & ` +
  `( ${R} autoresearch.scan.calendar ${date} || true ) & ` +
  `( ${R} autoresearch.scan.agents.l4_card consensus ${date} || true ) & ` +
  `wait`, 'l4-prep', 'L4-prep')
```

:381 legacy GATE3(`streaming_l4=false` 回滚路)命令头部前置 prompts:

```js
  const g3 = await gate('GATE3', `${R} autoresearch.scan.agents.l4_card prompts ${date} && ${R} autoresearch.scan.agents.l4_card harvest-slim ${date}`, G3, 'L4-prep')
```

:397-398 init 壳命令前置 prompts + throw 带上 reason(C1a 的拒绝理由必须到人眼前):

```js
  const tasks = await gate('l4-tasks-init',
    `${R} autoresearch.scan.agents.l4_card prompts ${date} && ${R} autoresearch.scan.l4_tasks init ${date}`,
    TASKS, 'L4-prep')
  if (!tasks || !tasks.ok) throw new Error(
    `L4 task book 初始化失败:${tasks && tasks.reason ? tasks.reason : 'agent 无返回'}` +
    `${tasks && tasks.missing_prompts ? ` missing=${tasks.missing_prompts.join('/')}` : ''}`)
```

> 注:prompts CLI 打印的是人读行不是 JSON,init 的 JSON 是 stdout 最后一行 —— gate 壳「取最后一行 JSON」契约不受影响。prompts 幂等(实测 3.49s),重复执行零风险 —— 这正是自愈:哪怕 l4-prep 壳整个被 harness 回收,这里也会重建 12 份任务包再验证。

- [ ] **Step 2: l4-stock.js 四处**

:101-103 TASK_ACTION 加可选字段:

```js
const TASK_ACTION = { type: 'object', required: ['ok', 'action'],
  properties: { ok: { type: 'boolean' }, action: { type: 'string' },
    attempt: { type: 'integer' }, reason: { type: 'string' },
    intel_resume: { type: 'boolean' } } }
```

:122-126 preflight 从 taskGate(壳内 if-else)换 gpJson 直调(bookless 分支已被 python 接管,Task 2):

```js
// C1b(2026-08-10):bookless 的 LEGACY 分支移入 python(壳零判断)——preflight 现在
// 无论有无任务簿都能回答,且缺 prompt 一律 BLOCKED(盲卡在这里绝育)。
const taskPreflight = await gpJson(
  `${R} autoresearch.scan.l4_tasks preflight ${code} ${date}`,
  `task-preflight:${code}`,
  TASK_ACTION,
)
```

(其后 :127-139 的 SKIP/BLOCKED/WAIT/`trackedTask` 判断全部不动;`action==='LEGACY'` 落进 `trackedTask=false`,与旧壳行为逐字节等价。)

:139 之后加一行:

```js
const intelResume = !!(taskPreflight && taskPreflight.intel_resume)
```

:185 intel 派发条件(parallel 里)由 `intelOn` 改为 `intelOn && !intelResume`:

```js
  ...(intelOn && !intelResume ? [() => intelLeg().then((r) => { intelResult = r; return r })] : []),
```

:187-213 intel 后处理三步(log/guard/status)包进续传分支 —— 把现有 `if (!intelOn) {...} else {...}` 与其后的 `await bash(...intel_status...)` 整段改为:

```js
if (!intelOn) {
  log(`intel 关(config l4_intel.enabled=false)→ 直接出卡`)
} else if (intelResume) {
  // C3:同日 crash-resume —— 稿 + status 已在盘上且 ≤24h(preflight 验过并已 mark_resumed),
  // 重盲搜只是把同一晚的六面查询再付一遍(2026-08-09 实测 34 次里 22 次是重复 ≈$15)。
  // guard/intel-status 也跳过:它们的产物就是上一轮落的那两份,重写只会抹掉 attempts 痕迹。
  log(`🕵️ intel ♻ ${code} 同日续传(稿+status 验证通过,盲搜跳过;status.resumed=true 已披露)`)
} else {
  log(intelResult ? `🕵️ intel ✓ ${code}(events=${intelResult.events ?? '?'})` : `🕵️ intel ✗ ${code}(缺稿,卡自动回退卡内网查)`)
  if (intelResult) {
    const g = await gpJson(
      `${R} autoresearch.scan.l4.intel_guard ${date} ${code}`,
      `intel-guard:${code}`, INTEL_GUARD)
      .catch((e) => { log(`⚠️ intel-guard ✗ ${code}:${e && e.message ? e.message : e}(放行)`); return null })
    if (g && g.action === 'REJECTED') {
      log(`🚫 intel 拒稿 ${code}:自报 ${g.claimed} 条 > 硬顶(留档 ${g.kept_as});卡回退卡内网查`)
    } else if (g && g.warn === 'unreported') {
      log(`⚠️ intel ${code} 未自报查询数(无法对账,不拒稿)`)
    }
  }
  await bash(
    `${R} autoresearch.scan.l4.intel_status ${date} ${code} --normalize` +
    `${intelOn ? '' : ' --disabled'}${intelAttempts > 1 ? ` --attempts ${intelAttempts}` : ''}` +
    `${intelResult ? '' : (intelError ? ` --error-class ${intelError}` : '')}`,
    `intel-status:${code}`, 'Intel').catch(() => null)
}
```

> ⚠️ 迁移时保持 guard/status 两段**原文照搬**(上面已是现文全文,不是示意);唯一的语义变化是包了 `else if (intelResume)` 分支。`intel-status` 壳原先在 if/else 外无条件跑,现进 else —— 因为续传时状态文件已在盘上且带 attempts 痕迹,重写反而失真。

- [ ] **Step 3: 跑 AsyncFunction 探针(js 唯一可信绿灯)**

Run: `uv run --no-sync python -m pytest tests/test_workflow_js_syntax.py -v`
Expected: 全 PASS。(家训:`node --check` 对 workflow js 是假绿灯,禁用。)

- [ ] **Step 4: grep 自证没有遗漏的 prompts 调用点**

Run: `grep -n "l4_card prompts" .claude/workflows/*.js`
Expected: 恰 3 处 —— scan-market.js 的 SENTINEL_PINNED(:239,原样保留)、legacy GATE3(新)、l4-tasks-init 壳(新);l4-prep 长命令里**不再出现**。

- [ ] **Step 5: Commit**

```bash
git add .claude/workflows/scan-market.js .claude/workflows/l4-stock.js
git commit -m "feat(workflows): C2 prompts 迁入 init 短壳自愈 + C1b preflight 直调 + C3 intel 续传消费

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: C4-1 — usage_harvest 对 killed-early transcript 的 role 兜底

**Files:**
- Modify: `autoresearch/trace/usage_harvest.py`(`usage_of` :54-115,`tot["agent"]` 赋值行 :112 前加兜底)
- Test: `tests/trace/test_usage_harvest.py`(追加)

**Interfaces:**
- Produces: `usage_of()` 对「jsonl 里一行 `attributionAgent` 都没有」的 transcript,回退读同名 `agent-X.meta.json` 的 `agentType` 作 agent 标签(2026-08-09 实测 39 份 limit-killed 稿全中此病,报表落 `(未标注)` 并触发 usage_reconcile 的 `unknown_agent_type` 噪音)。其余行为(status/计费/去重)不变。

- [ ] **Step 1: 写失败测试**(追加到 `tests/trace/test_usage_harvest.py`,沿用该文件既有的 transcript 合成夹具风格;若无现成夹具则用下面的自足版本)

```python
def test_usage_of_falls_back_to_meta_json_agent_type(tmp_path):
    """limit-killed 稿:jsonl 无任何 attributionAgent 行 → 从旁 sibling meta.json 取 agentType。"""
    import json as _json
    from autoresearch.trace.usage_harvest import usage_of
    p = tmp_path / "agent-abc123.jsonl"
    p.write_text("", encoding="utf-8")                       # 被杀在首次 API 响应前:空稿
    (tmp_path / "agent-abc123.meta.json").write_text(
        _json.dumps({"agentType": "l4-card", "spawnDepth": 1, "model": "opus"}),
        encoding="utf-8")
    row = usage_of(p)
    assert row["agent"] == "l4-card"


def test_usage_of_unlabeled_when_no_meta(tmp_path):
    from autoresearch.trace.usage_harvest import usage_of
    p = tmp_path / "agent-nometa.jsonl"
    p.write_text("", encoding="utf-8")
    assert usage_of(p)["agent"] == "(未标注)"                 # 兜底的兜底不变
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/trace/test_usage_harvest.py -v -k meta_json`
Expected: 第一条 FAIL(现为 `(未标注)`),第二条 PASS。

- [ ] **Step 3: 最小实现**

`usage_harvest.py` 在 `usage_of` 上方加 helper:

```python
def _meta_agent(path: Path) -> str | None:
    """transcript 旁的 `agent-X.meta.json`(harness 派发时落)→ agentType。

    limit-killed 的稿 jsonl 里连一行带 attributionAgent 的记录都没有(2026-08-09
    实测 39 份),但 meta.json 是派发时写的、必在 —— 账目身份不该跟着 API 死亡一起丢。
    """
    meta = path.with_name(path.name.replace(".jsonl", ".meta.json"))
    if not meta.is_file():
        return None
    import contextlib
    import json as _json
    with contextlib.suppress(Exception):
        return (_json.loads(meta.read_text(encoding="utf-8")) or {}).get("agentType") or None
    return None
```

`usage_of` 里 `tot["agent"] = "(主会话)" if role == "main" else (agent or "(未标注)")` 一行改为:

```python
    tot["agent"] = ("(主会话)" if role == "main"
                    else (agent or _meta_agent(path) or "(未标注)"))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run --no-sync python -m pytest tests/trace/test_usage_harvest.py tests/trace/test_usage_reconcile.py -q`
Expected: 全 PASS(reconcile 一起跑,防 agent 标签语义联动回归)。

- [ ] **Step 5: Commit**

```bash
git add autoresearch/trace/usage_harvest.py tests/trace/test_usage_harvest.py
git commit -m "fix(trace): usage_harvest 对 killed-early 稿回退 meta.json agentType(消 39 份「未标注」噪音)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: C4-2 — l4_watch 双挂 pid 锁

**Files:**
- Modify: `autoresearch/scan/l4_watch.py`(`main` :267 起,`--watch` 分支进入轮询前)
- Test: `tests/scan/test_l4_watch_lock.py`(新建;若已有 l4_watch 测试文件则追加)

**Interfaces:**
- Produces: `acquire_watch_lock(scan_dir: Path | str) -> Path`(活锁 → `SystemExit(2)`;死锁残骸接管)与 `release_watch_lock(path: Path) -> None`。锁文件 `outbox/l4_watch.lock`,与既有 cursor(`outbox/l4_watch_cursor.json`)同目录、互不相扰。

- [ ] **Step 1: 写失败测试**(新建 `tests/scan/test_l4_watch_lock.py`)

```python
"""C4-2(design 2026-08-10):同日第二个 --watch 必须拒绝 —— 双挂会共用 cursor 重播事件
(2026-08-09 实测双 Monitor 重播 4 条 = 4 次多余的主会话全上下文唤醒)。"""
from __future__ import annotations

import json
import os

import pytest

from autoresearch.scan.l4_watch import acquire_watch_lock, release_watch_lock


def test_second_watcher_refused_while_first_alive(tmp_path):
    acquire_watch_lock(tmp_path)                     # 本进程 = 活着的第一个 watcher
    with pytest.raises(SystemExit):
        acquire_watch_lock(tmp_path)


def test_dead_lock_is_taken_over(tmp_path):
    lock = tmp_path / "outbox" / "l4_watch.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": 99999999}), encoding="utf-8")   # 不存在的 pid
    path = acquire_watch_lock(tmp_path)              # 残骸 → 接管,不炸
    assert json.loads(path.read_text())["pid"] == os.getpid()


def test_release_removes_lock(tmp_path):
    path = acquire_watch_lock(tmp_path)
    release_watch_lock(path)
    assert not path.exists()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_l4_watch_lock.py -v`
Expected: 全 FAIL(ImportError)。

- [ ] **Step 3: 最小实现**

`l4_watch.py`(`cursor_path` 附近)加:

```python
def watch_lock_path(scan_dir: Path | str) -> Path:
    return Path(scan_dir) / "outbox" / "l4_watch.lock"


def acquire_watch_lock(scan_dir: Path | str) -> Path:
    """同日只许一个活 watcher:双挂共用 cursor 会把同一事件播两遍(= 双倍主会话唤醒)。

    锁 = `{"pid": ...}`;pid 已死(ProcessLookupError)→ 残骸,接管;pid 活着(含
    PermissionError = 别人的活进程)→ SystemExit(2),要求先停旧的。
    """
    import contextlib
    path = watch_lock_path(scan_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        pid = 0
        with contextlib.suppress(Exception):
            pid = int((json.loads(path.read_text(encoding="utf-8")) or {}).get("pid") or 0)
        if pid > 0:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                pass                                  # 死锁残骸 → 接管
            except PermissionError:
                raise SystemExit(2)                   # 别人的活进程,同样拒绝
            else:
                print(f"✗ 已有活 watcher(pid={pid})—— 拒绝双挂;先 TaskStop 旧 Monitor 再挂新的。",
                      file=sys.stderr)
                raise SystemExit(2)
    path.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    return path


def release_watch_lock(path: Path) -> None:
    import contextlib
    with contextlib.suppress(FileNotFoundError):
        Path(path).unlink()
```

(文件顶部确认已 `import json, os, sys` —— 缺则补。)

`main()` 里 `--watch` 分支:进入 `while True:` 轮询**之前**加、并包 finally:

```python
    lock = acquire_watch_lock(scan_dir) if args.watch else None
    try:
        ...(既有轮询循环整体缩进不变,置于 try 内)...
    finally:
        if lock is not None:
            release_watch_lock(lock)
```

> 实现注:非 `--watch` 的单次模式不上锁(只读一遍就退,双跑无重播风险——cursor 仍然去重)。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run --no-sync python -m pytest tests/scan/test_l4_watch_lock.py -q`
Expected: 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add autoresearch/scan/l4_watch.py tests/scan/test_l4_watch_lock.py
git commit -m "fix(scan): l4_watch --watch 加 pid 锁拒双挂(双 Monitor 重播=双倍主会话唤醒)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: 文档 —— STAGES.md 运维注(preflight=认领)+ 硬门/续传条目

**Files:**
- Modify: `.claude/skills/scan-market/STAGES.md`(「运维细节」节尾 + L4 节「派发三步」)

- [ ] **Step 1: 「运维细节」节尾追加**

```markdown
### l4_tasks 子命令语义(2026-08-10)

- **`preflight` 是认领不是只读探针**:调用即可能把该票置 RUNNING 抢锁(2026-08-09 主会话
  误用作"验证"抢走 600276,靠 `failure --error-class TIMEOUT` 释放)。人工看状态用
  `l4_tasks stats <date>`(纯读)或直接读 `_l4_tasks.json`。
- **prompt 硬门**(C1,design 2026-08-10-token-regression-remediation-design.md):
  `init` 见任一 `_l4_prompt_*.md` 缺失/空 → `ok:false` 拒建任务簿(修复=重跑
  `l4_card prompts` 再 init,幂等 ~3.5s);`preflight` 缺 prompt → `BLOCKED/PROMPT_MISSING`
  不认领。救火杆:没有 `--allow-missing-prompts` 这种口子,**修产物,别修门**。
- **intel 同日续传**(C3):事故重派时 preflight 对「稿+status 完好且 ≤24h」的票返回
  `intel_resume:true`,l4-stock 跳过重盲搜并在 status 落 `resumed:true` 披露。跨日/重放
  历史日不满足条件 → 照常盲搜;强制重搜 = 删该票 `_l4_intel_*` 两个文件。
```

- [ ] **Step 2: L4 节「派发三步」的第 2 步补一句**

在「初始化 `_l4_tasks.json`」句后追加:`init 前置 prompts 落稿并对其做硬门校验(缺→ok:false 整线停在派发前,2026-08-10 C1/C2)。`

- [ ] **Step 3: 自查退役符号 lint**(GATE4 的 `产物形状·退役符号指令性引用` 探针会扫 STAGES.md)

Run: `uv run --no-sync python -c "from autoresearch.learning import self_review; print('ok')"` 后跑一次 `uv run --no-sync python -m pytest tests/ -q -k "product_shape or brief_lint" --no-header | tail -3`
Expected: 不因新增文案引入新的 fail(新文案不含退役符号)。

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/scan-market/STAGES.md
git commit -m "docs(scan): STAGES 运维注 —— preflight=认领语义 + prompt 硬门 + intel 同日续传

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: 全量回归 + 事故注入演练(验收 §5A)

**Files:** 无新文件(演练在 scratch 目录)。

- [ ] **Step 1: 全量测试**

Run: `uv run --no-sync python -m pytest tests/ -q 2>&1 | tail -5`
Expected: 全绿(基线 3937+,新增 ~15 条)。

- [ ] **Step 2: 变异探针(绿灯必须能变红)**

临时注释掉 `l4_tasks.py` 里 C1a 的 `if missing_prompts:` 整块 → `uv run --no-sync python -m pytest tests/scan/test_l4_tasks_gate.py -q` **必须红**(`test_init_refuses_*` FAIL)→ 恢复代码 → 重跑必须绿。对 C1b 的 prompt 检查块同法一次。任一步"注释后仍绿" = 测试零鉴别力,停下修测试。

- [ ] **Step 3: CLI 级事故注入演练**(scratch 目录,零 LLM)

```bash
cd /Users/qingbin.zhuang/Personal/TradingAgents
SCRATCH=$(mktemp -d); mkdir -p "$SCRATCH/2026-01-05"
# 无 prompt → init 必须 ok:false + exit 1(注意:CLI init 会调 dispatch_plan,需真 finalists;
# 故 CLI 级演练用函数级替代)
uv run --no-sync python -c "
from autoresearch.scan import l4_tasks
r = l4_tasks.initialize('2026-01-05', ['600000'], root='$SCRATCH', context_root='$SCRATCH/ctx')
assert r['ok'] is False and r['missing_prompts'] == ['600000'], r
print('C1a 演练 ✓ 拒绝:', r['reason'])
r2 = l4_tasks.preflight('$SCRATCH/2026-01-05/_l4_tasks.json', '600000')
assert r2['action'] == 'BLOCKED' and r2['reason'] == 'PROMPT_MISSING', r2
print('C1b 演练 ✓ BLOCKED')
"
```

Expected: 两行 ✓。

- [ ] **Step 4: 下次真实扫描的活体验收清单**(记入 run 前检查,不在本波执行)

1. l4-prep 壳日志无 prompts 字样;`l4-tasks-init` 壳输出含 `[l4_card prompts] 12 份`;
2. 干净单轮读数 ≤$35 / ≤8M 加权 / subagents ≤130 / 主会话 ≤$6(连续 3 净 run,§5B);
3. 若发生重派:♻ 续传行出现、`_l4_intel_status_*.json` 带 `resumed:true`、intel 次数不翻倍;
4. `usage_reconcile` 不再报 `unknown_agent_type:(未标注)`。

- [ ] **Step 5: 收尾 commit(若前四步产生任何修补)**

```bash
git add -A && git commit -m "test(scan): C1-C4 变异探针与事故注入演练收尾

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Self-Review 记录(计划完成后自查)

- **Spec 覆盖**:C1a→Task1、C1b→Task2+4、C2→Task4、C3→Task3+4、C4-1→Task5、C4-2→Task6、运维注→Task7、验收 §5→Task8;设计 §6 不做清单无对应 task(正确:它们本来就不做)。
- **占位符**:无 TBD/「同 Task N」;Task 4 的 guard/status 段为现文全文照搬。
- **类型一致**:`_prompt_file_ok(scan_dir, code)` Task1 定义、Task2 消费;`intel_resume` 字段名 python/js/schema 三处一致;`resumable/mark_resumed` 签名 Task3 定义、preflight 与测试同签名消费。
- **已知开放点**(不阻塞):Task1 Step5 的 test_structural_audit 夹具需现场确认其上方 fixture 是否已写 prompt;Task5 若 `tests/trace/test_usage_harvest.py` 有现成 transcript 夹具,优先沿用其风格。
