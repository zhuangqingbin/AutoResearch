# Wave11 实施计划(C→B→A→D)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 Wave11 四批:L4 全并发(C)→ agent 配置统一+生效对账(B)→ 评判尺切换隔夜尺 gap_c1_o2(A)→ skill 整备与死码清理(D)。

**Spec:** `docs/specs/2026-08-05-wave11-ruler-config-l4concurrency-skills-design.md`(含 08-06 D3 勘误)。现状证据:`docs/research/2026-08-05-selflearning-loop-audit.md`。

**Architecture:** 全部改动落在确定性层(Python CLI/账本)与编排层(workflow js/SKILL 文档);不新增任何 LLM 调用点。批A 的换尺采用「先常量化 parity、后单点换值」两段式,历史账本只追加列不改旧值。

**Tech Stack:** Python(pandas/fcntl)+ pytest;workflow JS(harness 内 AsyncFunction 语义);JSONC 配置。

## Global Constraints(每个 task 隐含)

- 仓库根目录;一切 Python 走 `uv run --no-sync python -m …`(venv-only 的 akshare/tushare/lightgbm)。
- 每次 commit 前全测绿:`uv run --no-sync python -m pytest -q`;**禁止 `pytest | tail`**(吞退出码前科)。
- 改任何 `.claude/workflows/*.js` 后必跑 AsyncFunction 探针(T10 步骤 5 给出命令);`node --check` 对这类文件是假绿灯,**禁用作判据**。
- 改任何 `.claude/skills|agents` 文档前先 Read 重读(会被外部改的前科)。
- **premise-check**:每 task 开工先 grep 确认所引函数/行仍在(本 wave 设计期已两次靠它逮错:GATE1 误诊、D3 死码误判)。
- `ruler.MAIN_RULER` 换值**只发生在 T16**;之前所有 commit 保持 `fwd_2_oc` 行为逐字节不变。
- commit 风格:`type(scope): 中文主旨`,尾行 `Co-Authored-By: Claude <noreply@anthropic.com>`。
- 铁律不破:确定性层零 LLM;性能开关不拥有评级;L3/L4 必须 subagent。

---

## 批C L4 全并发(T1–T5)

### Task 1: dispatch_cap 与资源帽解耦

**Files:**
- Modify: `autoresearch/scan/l4_tasks.py`(`DEFAULT_CAPS`、`initialize()` 的 effective_cap 块、`dispatch_batches()` 的 effective 计算与 docstring)
- Create: `tests/scan/test_l4_tasks_caps.py`

**Interfaces(produces):** `dispatch_batches()` 返回形状不变(`{ok, caps, effective_cap, batches, pending, running}`),但 `effective_cap` 语义改为**派发帽=caps.l4_stock**(不再 min 四帽、不再被 rate_limit_failures 收窄);pending 全量单批。

- [ ] **Step 1: 失败测试**

```python
# tests/scan/test_l4_tasks_caps.py
"""Wave11 T1:派发帽与资源帽解耦 —— effective_cap 只由 l4_stock 决定,批次一次全派。"""
from autoresearch.scan import l4_tasks


def _init(tmp_path, n=10, caps=None):
    codes = [f"{600000+i}" for i in range(n)]
    return l4_tasks.initialize("2026-08-06", codes, root=tmp_path,
                               context_root=tmp_path / "ctx", caps=caps)


def test_dispatch_cap_is_l4_stock_only(tmp_path):
    r = _init(tmp_path, n=10)          # DEFAULT_CAPS: l4_stock=64
    assert r["effective_cap"] == 64
    b = l4_tasks.dispatch_batches(tmp_path / "2026-08-06" / "_l4_tasks.json")
    assert len(b["batches"]) == 1 and len(b["batches"][0]) == 10   # 一次全派


def test_rate_limit_failures_no_longer_shrinks_dispatch(tmp_path):
    _init(tmp_path, n=6)
    book = tmp_path / "2026-08-06" / "_l4_tasks.json"
    import json
    payload = json.loads(book.read_text())
    payload["rate_limit_failures"] = 3
    book.write_text(json.dumps(payload))
    b = l4_tasks.dispatch_batches(book)
    assert b["effective_cap"] == 64            # 派发帽不缩;限频收窄的是 T2 的 tushare 槽
    assert len(b["batches"][0]) == 6
```

- [ ] **Step 2: 跑红** `uv run --no-sync python -m pytest tests/scan/test_l4_tasks_caps.py -q` → 现值 4,FAIL。
- [ ] **Step 3: 实现**(锚定函数名,勿信行号)

```python
# DEFAULT_CAPS:l4_stock 帽退出「资源」语义,升为派发帽,缺省 64=事实无上限
DEFAULT_CAPS = {"tushare": 4, "web_search": 4, "web_fetch": 4, "l4_stock": 64}

# initialize() 与 dispatch_batches() 中,原
#   effective = max(1, min(cap_values.values()) - int(payload.get("rate_limit_failures") or 0))
# 改为(两处同改;rate_limit_failures 保留字段,T2 的槽数消费它):
    effective = max(1, int(cap_values["l4_stock"]))
```

docstring 同步:「按四种独立资源帽的最小值稳定切批」→「派发帽=caps.l4_stock(一次全派);
tushare/web 资源帽由 prepare_slim 的操作级信号量执行(T2),限频事件收窄的是取数槽不是派发」。

- [ ] **Step 4: 跑绿** 同命令 PASS;再全量 `uv run --no-sync python -m pytest -q`(存量 test 若断言 effective_cap==4,按新语义更新——先读其 docstring 确认它锁的就是旧调度语义)。
- [ ] **Step 5: Commit** `feat(scan): Wave11-C1 派发帽与资源帽解耦 —— l4_stock 缺省 64 一次全派`

### Task 2: tushare 操作级信号量

**Files:**
- Modify: `autoresearch/scan/l4_tasks.py`(新增 `_tushare_slot()`;`prepare_slim()` 的 harvest 循环包裹;返回值加 `sem_wait_s`)
- Test: `tests/scan/test_l4_tasks_caps.py`(追加)

**Interfaces(produces):** `_tushare_slot(scan_dir: Path, k: int) -> ContextManager[int]`;`prepare_slim` 返回 dict 新增键 `sem_wait_s: float`。

- [ ] **Step 1: 失败测试**

```python
def test_tushare_slot_queues_when_full(tmp_path):
    import fcntl, threading, time
    sem_dir = tmp_path / "_sem"; sem_dir.mkdir()
    hold = (sem_dir / "tushare.0.lock").open("a+")
    fcntl.flock(hold, fcntl.LOCK_EX | fcntl.LOCK_NB)      # 外部占住 slot0
    got = {}
    with l4_tasks._tushare_slot(tmp_path, k=2) as slot:   # k=2 → 应立刻拿到 slot1
        got["slot"] = slot
    assert got["slot"] == 1
    hold.close()


def test_tushare_slot_waits_then_acquires(tmp_path, monkeypatch):
    import fcntl, threading, time
    sem_dir = tmp_path / "_sem"; sem_dir.mkdir()
    fh = (sem_dir / "tushare.0.lock").open("a+")
    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    threading.Timer(0.3, lambda: (fcntl.flock(fh, fcntl.LOCK_UN), fh.close())).start()
    t0 = time.monotonic()
    with l4_tasks._tushare_slot(tmp_path, k=1, poll_seconds=0.05) as slot:
        assert slot == 0
    assert time.monotonic() - t0 >= 0.25                  # 真排过队
```

- [ ] **Step 2: 跑红**(`_tushare_slot` 不存在)。
- [ ] **Step 3: 实现**

```python
@contextmanager
def _tushare_slot(scan_dir: Path, k: int, *, poll_seconds: float = 5.0,
                  heartbeat_seconds: int = 60) -> Iterator[int]:
    """K 槽 fcntl 信号量:只限 slim 取数,不限派发。持有者进程死亡 flock 自动释放,
    无需 mtime stale 回收。等待期每 heartbeat_seconds 打一行心跳 —— 08-05 事故的另一半药:
    安静的长等待会被上层(人或壳)误判「卡住」。"""
    sem_dir = scan_dir / "_sem"
    sem_dir.mkdir(parents=True, exist_ok=True)
    waited = 0.0
    while True:
        for slot in range(max(1, int(k))):
            fh = (sem_dir / f"tushare.{slot}.lock").open("a+")
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                fh.close()
                continue
            try:
                yield slot
                return
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
                fh.close()
        time.sleep(poll_seconds)
        waited += poll_seconds
        if waited % heartbeat_seconds < poll_seconds:
            print(f"[prepare_slim] 等 tushare 槽 {int(waited)}s(K={k})…", flush=True)
```

`prepare_slim` 集成:仅当 `defect` 非空(要真取数)时包裹 harvest 循环 ——

```python
    sem_wait = 0.0
    if defect:
        k = max(1, int((payload.get("caps") or DEFAULT_CAPS)["tushare"])
                - int(payload.get("rate_limit_failures") or 0))
        _t0 = time.monotonic()
        with _tushare_slot(path.parent, k):
            sem_wait = time.monotonic() - _t0
            for _ in range(max(0, retries) + 1):
                ...  # 原 harvest 循环整体内移,逻辑不变
```

返回 dict 增 `"sem_wait_s": round(sem_wait, 1)`;task book 写入处同步记
`current["sem_wait_s"] = ...`(湖命中路径为 0.0)。顶部 `import time`。

- [ ] **Step 4: 跑绿 + 全量绿。**
- [ ] **Step 5: Commit** `feat(scan): Wave11-C2 tushare 取数降 K 槽信号量(带等待心跳),限频收窄槽数不缩派发`

### Task 3: 首跑观测 `l4_tasks stats`

**Files:** Modify `autoresearch/scan/l4_tasks.py`(新增 `stats()` + CLI choices 加 `"stats"`);Test 追加同文件。

- [ ] **Step 1: 失败测试**

```python
def test_stats_counts_errors_and_wait(tmp_path):
    _init(tmp_path, n=3)
    book = tmp_path / "2026-08-06" / "_l4_tasks.json"
    import json
    p = json.loads(book.read_text())
    codes = list(p["tasks"])
    p["tasks"][codes[0]]["last_error_class"] = "RATE_LIMIT"
    p["tasks"][codes[1]]["sem_wait_s"] = 42.0
    book.write_text(json.dumps(p))
    s = l4_tasks.stats(book)
    assert s["error_classes"] == {"RATE_LIMIT": 1}
    assert s["sem_wait_max_s"] == 42.0
```

- [ ] **Step 2: 跑红。Step 3: 实现**(纯读账本:遍历 tasks 聚合 `last_error_class` 计数、`sem_wait_s` max/均值、`slim_attempts` 总数,返回 dict;CLI 打一行 JSON)。**Step 4: 跑绿+全量绿。**
- [ ] **Step 5: Commit** `feat(scan): Wave11-C4 l4_tasks stats —— 全并发首跑的限频/排队观测面`

### Task 4: SKILL.md 派发协议改写(全派)

**Files:** Modify `.claude/skills/scan-market/SKILL.md`(步骤 4 滑窗段);Modify `.claude/skills/scan-market/STAGES.md`(沿革注一行)。

- [ ] **Step 1: Read 重读两文件**(外部可能已改)。
- [ ] **Step 2: 步骤 4 中滑窗四条(「首轮并行派 effective_cap 只…把长的留到最后」)整段替换为:**

```markdown
   - **一次性全派**(Wave11-C,恢复 fb_20260714_003「别分 wave」原意):`l4_tasks batches`
     现返回单批全量 pending —— 主会话**一条消息 N 个 Workflow 调用**全部派出;📌 pinned 排
     列表最前只为 watch 可读性,无先后语义。tushare 取数由每票 prepare 内的 K 槽信号量排队
     (K=caps.tushare−限频扣减),intel/card 不排队即刻起跑。
   - **完成判据 = task_book 全 SUCCEEDED**(不变;`batches` 空 ∧ `running` 空才是完成态);
     收完成通知的回合只领不播(唤醒纪律不变)。单票失败只改本票状态;重放仍只派
     `l4_tasks batches` 返回的未完成票。
   - 回滚杆:`scan_config.jsonc` 设 `budgets.concurrency.l4_stock=4` 即回滑窗节奏
     (旧滑窗操作法见 git 本段历史,勿在此保留操作细节)。
   - 首跑后必看:`uv run --no-sync python -m autoresearch.scan.l4_tasks stats <date>`
     (RATE_LIMIT/排队等待读数;429 率 >10% 才考虑 stagger,YAGNI)。
```

- [ ] **Step 3: 验证** `grep -n "一次性全派\|每完成一只补派" .claude/skills/scan-market/SKILL.md` → 前者在、后者不在。
- [ ] **Step 4: Commit** `docs(skill): Wave11-C3 scan-market 派发协议改一次性全派(滑窗降回滚杆)`

### Task 5: 速度实验登记(registry)

**Files:** Create `docs/research/2026-08-06-wave11-experiment-specs.json`(内容=设计稿 §C5 JSON 原文);registry 落 `context/learning/experiments/registry.json`(CLI 写)。

- [ ] **Step 1: 落 spec JSON**(设计稿 C5 代码块原样,`id: exp_l4_full_parallel`)。
- [ ] **Step 2: 登记**

```bash
uv run --no-sync python -m autoresearch.learning.experiment_registry register \
  --spec docs/research/2026-08-06-wave11-experiment-specs.json
uv run --no-sync python -m autoresearch.learning.experiment_registry report
```

预期:report 出现 `exp_l4_full_parallel · PREREGISTERED`。(baseline 指针已有稳定基线则跳过
`baseline` 子命令;没有先按 registry `--help` 建。approve/activate 是**人**的动作,本计划不做。)
- [ ] **Step 3: Commit** `chore(learning): Wave11-C5 登记 exp_l4_full_parallel(PREREGISTERED,速度类)`

---

## 批B 配置统一 + 生效对账(T6–T10)

### Task 6: `_AGENT_ROLES` 闭集校验

**Files:** Modify `autoresearch/scan/user_config.py`(`load_user_config` 内加 agents 形状校验);Test: `tests/scan/test_user_config_roles.py`(新)。

**Interfaces(produces):** 常量 `_AGENT_ROLES`(T7 的 jsonc 键、T8 的 AGENT_DEFAULTS、T9 的 reconcile 全都以它为词表)。

- [ ] **Step 1: 失败测试**

```python
# tests/scan/test_user_config_roles.py
"""Wave11 B1:agents 子键闭集 —— 拼写错必须 raise,这是 user_config 存在的唯一理由的补全。"""
import json, pytest
from autoresearch.scan import user_config as uc


def _load(tmp_path, agents):
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"agents": agents}))
    return uc.load_user_config(p)


def test_unknown_role_raises(tmp_path):
    with pytest.raises(ValueError, match="t1diag"):
        _load(tmp_path, {"t1diag": {"effort": "high"}})          # 少写下划线=经典拼错


def test_known_roles_pass(tmp_path):
    cfg = _load(tmp_path, {"l4_card": {"effort": "max"}, "gp_shell": {"model": "sonnet"}})
    assert cfg["agents"]["l4_card"]["effort"] == "max"


def test_bad_effort_and_bad_subkey_raise(tmp_path):
    with pytest.raises(ValueError, match="effort"):
        _load(tmp_path, {"l4_card": {"effort": "ultra"}})
    with pytest.raises(ValueError, match="temperature"):
        _load(tmp_path, {"l4_card": {"temperature": 0.2}})
```

- [ ] **Step 2: 跑红。Step 3: 实现**

```python
_AGENT_ROLES = {
    "strategist", "sector_brief", "l3_rank", "l4_intel", "l4_card",
    "t1_diag", "t1_synth",
    "ens_review", "l3_repair", "dossier_init", "gp_shell", "gp_shell_json",
}
_EFFORTS = {"low", "medium", "high", "xhigh", "max"}
_MODELS = {"haiku", "sonnet", "opus"}

# load_user_config() 里 performance 校验旁追加:
    agents = cfg.get("agents")
    if agents is not None:
        if not isinstance(agents, dict):
            raise ValueError("scan_config.json 的 agents 必须是 object")
        unknown = sorted(set(agents) - _AGENT_ROLES)
        if unknown:
            raise ValueError(f"scan_config.json agents 含未知 role: {unknown}"
                             f"(闭集={sorted(_AGENT_ROLES)})")
        for role, spec in agents.items():
            bad = sorted(set(spec or {}) - {"model", "effort"})
            if bad:
                raise ValueError(f"agents.{role} 含未知子键: {bad}(只认 model/effort)")
            if "effort" in (spec or {}) and spec["effort"] not in _EFFORTS:
                raise ValueError(f"agents.{role}.effort={spec['effort']!r} 非法(∈{sorted(_EFFORTS)})")
            if "model" in (spec or {}) and spec["model"] not in _MODELS:
                raise ValueError(f"agents.{role}.model={spec['model']!r} 非法(∈{sorted(_MODELS)})")
```

- [ ] **Step 4: 跑绿 + 全量绿**(现行 scan_config.jsonc 的 7 个 role 全在闭集 → 不破)。
- [ ] **Step 5: Commit** `feat(scan): Wave11-B1 agents role 闭集校验 —— 拼错即 raise 不再静默失效`

### Task 7: scan_config.jsonc 增 roles 段

**Files:** Modify `.claude/skills/scan-market/scan_config.jsonc`(agents 块尾追加)。

- [ ] **Step 1: Read 重读;在 `"t1_synth"` 行后追加:**

```jsonc
    // ── Wave11-B 新收口的 5 个 role(缺键=用 workflow AGENT_DEFAULTS,语义见各注)──
    "ens_review":   { "effort": "xhigh" },   // ≥OW/SELL 双复核 run2/3(此前借 l4_card 档;独立收口)
    "l3_repair":    { "effort": "medium" },  // L3 数字自修(可选增益,挂了不阻断)
    "dossier_init": { "effort": "max" },     // 档案首覆(≤3 只/晚,单票 10-20min)
    // 壳类缺省 sonnet·low:08-05 事故——haiku 壳遇长命令转后台会 pkill 生产作业;
    // 要省钱显式改回 haiku,但那是你的选择不是缺省。
    "gp_shell":      { "model": "sonnet", "effort": "low" },  // 命令壳(bash/stageGate/recordL4)
    "gp_shell_json": { "model": "sonnet", "effort": "low" }   // JSON 壳(gate/gpJson/taskGate/ens-dump/lint)
```

- [ ] **Step 2: 验证** `uv run --no-sync python -m autoresearch.scan.user_config | uv run --no-sync python -c "import json,sys; d=json.load(sys.stdin); assert 'gp_shell' in d['agents'], d['agents'].keys(); print('ok')"`
- [ ] **Step 3: Commit** `feat(config): Wave11-B3 scan_config 收口 5 个新 role(含壳类 sonnet·low 缺省)`

### Task 8: 三个 workflow 统一 `AGENT_DEFAULTS + AG(role)`

**Files:** Modify `.claude/workflows/scan-market.js`、`.claude/workflows/l4-stock.js`、`.claude/workflows/dossier-init.js`。

**Interfaces(produces):** 每个 js 顶部
`const AGENT_DEFAULTS = {...}` 与 `const AG = (role) => ({ ...(AGENT_DEFAULTS[role] || {}), ...((cfg.agents || {})[role] || {}) })`;调用点写法 `{ agentType: 'general-purpose', ...AG('gp_shell'), label, ... }`。

- [ ] **Step 1: Read 重读三文件**,登记全部内联 model/effort 调用点(设计稿 B1 表为底,现状为准)。
- [ ] **Step 2: scan-market.js**:cfg 常量定义后加

```js
// Wave11-B2:model/effort 单一事实源=scan_config.agents(闭集见 user_config._AGENT_ROLES);
// 本表=缺键回退值。调用点禁止内联字面量(product_shape_lint 会查)。
const AGENT_DEFAULTS = {
  gp_shell:      { model: 'sonnet', effort: 'low' },
  gp_shell_json: { model: 'sonnet', effort: 'low' },
  l3_repair:     { effort: 'medium' },     // model 缺省=l3-rank frontmatter(opus)
  strategist:    { effort: 'high' },
  sector_brief:  { effort: 'high' },
  l3_rank:       { effort: 'max' },
}
const AG = (role) => ({ ...(AGENT_DEFAULTS[role] || {}), ...((cfg.agents || {})[role] || {}) })
```

改造调用点(逐个,grep `model: '` 清零):`bash()`/`stageGate()` → `...AG('gp_shell')`;
`gate()`/`gpJson()` → `...AG('gp_shell_json')`;`L3-lint-fix` → `...AG('l3_repair')`;
strategist/sector_brief/l3_rank 三处现有 `cfg.agents?.X?.effort ?? '…'` 写法统一改 `...AG('X')`。
- [ ] **Step 3: l4-stock.js** 同款表(roles: gp_shell/gp_shell_json/l4_intel/l4_card/ens_review),
`recordL4`/`bash` → gp_shell;`taskGate`/`gpJson`/`ens-dump` → gp_shell_json;intel/card 现有写法
→ `...AG('l4_intel')`/`...AG('l4_card')`;**复核 run2/3(现借 l4_card 档的那处)→ `...AG('ens_review')`**,
`AGENT_DEFAULTS.ens_review = { effort: 'xhigh' }`。
- [ ] **Step 4: dossier-init.js**:该 workflow 现无 cfg 通道 —— args 解析处加
`const cfg = (typeof args === 'string' && args ? (JSON.parse(args).cfg || {}) : ((args && args.cfg) || {}))`
+ 同款表(roles: gp_shell/gp_shell_json/dossier_init);`init:${code}` → `...AG('dossier_init')`
(缺省 `{ effort: 'max' }`)。scan-market SKILL 步骤 6 的派发说明补一句「可带 args.cfg」。
- [ ] **Step 5: AsyncFunction 探针(三文件)**

```bash
node -e '
const fs=require("fs");
const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
let bad=0;
for(const f of [".claude/workflows/scan-market.js",".claude/workflows/l4-stock.js",".claude/workflows/dossier-init.js"]){
  let src=fs.readFileSync(f,"utf8").replace(/^export const meta/m,"const meta");
  try{ new AsyncFunction("agent","parallel","pipeline","log","phase","args","budget","workflow",src);
       console.log("OK",f);}catch(e){bad=1;console.log("FAIL",f,e.message);}
}
process.exit(bad);'
```

- [ ] **Step 6: 内联字面量清零断言** `grep -n "model: '" .claude/workflows/*.js | grep -v AGENT_DEFAULTS` → 只允许出现在各文件 AGENT_DEFAULTS 表内(即命中 0 行)。
- [ ] **Step 7: Commit** `refactor(workflow): Wave11-B2 三 workflow 统一 AGENT_DEFAULTS+AG(role),调用点内联字面量清零`

### Task 9: `trace.usage_reconcile` 生效对账

**Files:**
- Create: `autoresearch/trace/usage_reconcile.py`
- Modify: `autoresearch/learning/self_review.py`(新 check)、`.claude/skills/scan-market/SKILL.md`(CP7 批次第五条命令)
- Create: `tests/trace/test_usage_reconcile.py`

**Interfaces(produces):** CLI `python -m autoresearch.trace.usage_reconcile <date> [--json-out PATH]`;
产物 `context/scan/<date>/_usage_reconcile.json`(`{date, ok, mismatches:[{agent,field,expected,actual}], wire_breaks:[role], checked}`);
streak 账本 `context/learning/usage_reconcile.jsonl` 追加一行同形 JSON。

**时序事实(如实声明,勿"修")**:GATE4/self_review 跑在 usage_harvest **之前**,所以当日
对账结论进不了当日 GATE4 —— self_review 的新 check 读**最近一份既有** `_usage_reconcile.json`
(通常=上一 run),当日结论由 CP7 第五条命令直接打给人看。

- [ ] **Step 1: 失败测试**

```python
# tests/trace/test_usage_reconcile.py
"""Wave11 B4:配置期望 × usage_harvest 实测逐 role 对账 —— 「配置生效」从口头变成可断言。"""
import json
from autoresearch.trace import usage_reconcile as ur

ECHO = {"agents": {"l4_card": {"effort": "max"}, "l4_intel": {"effort": "max"},
                   "gp_shell": {"model": "sonnet", "effort": "low"},
                   "gp_shell_json": {"model": "sonnet", "effort": "low"}}}
ROWS = [
    {"role": "subagent", "agent": "l4-card", "model": "claude-opus-5", "effort": "max", "status": "SUCCEEDED"},
    {"role": "subagent", "agent": "general-purpose", "model": "claude-sonnet-5", "effort": "low", "status": "SUCCEEDED"},
]


def _run(tmp_path, echo, rows):
    d = tmp_path / "context/scan/2026-08-06"; d.mkdir(parents=True)
    (d / "user_config_echo.json").write_text(json.dumps(echo))
    (d / "_token_usage.json").write_text(json.dumps({"rows": rows}))
    return ur.reconcile("2026-08-06", root=tmp_path)


def test_clean_run_ok(tmp_path):
    r = _run(tmp_path, ECHO, ROWS)
    assert r["ok"] and not r["mismatches"]


def test_effort_mutation_caught(tmp_path):
    rows = [dict(ROWS[0], effort="low")] + ROWS[1:]        # 变异:l4_card 实测 low
    r = _run(tmp_path, ECHO, rows)
    assert not r["ok"]
    assert any(m["agent"] == "l4-card" and m["field"] == "effort" for m in r["mismatches"])


def test_wire_break_detected(tmp_path):
    r = _run(tmp_path, ECHO, ROWS[1:])                     # config 写了 l4_card,当日无实测行
    assert "l4_card" in r["wire_breaks"]
```

- [ ] **Step 2: 跑红。Step 3: 实现**(核心逻辑;model 归一 `claude-opus-5→opus` 前缀匹配)

```python
AGENTTYPE_TO_ROLE = {"l3-rank": "l3_rank", "l4-card": "l4_card", "l4-intel": "l4_intel",
                     "macro-brief": "strategist", "sector-brief": "sector_brief",
                     "dossier-init": "dossier_init"}
_EXPECT_PRESENT = ("l3_rank", "l4_card", "l4_intel", "strategist", "sector_brief")  # 全扫日应在场


def _frontmatter(agent_type: str) -> dict:
    """读 .claude/agents/<type>.md frontmatter 的 model:/effort:(运行时读,不做镜像表防漂移)。"""


def _norm_model(raw: str) -> str:
    for k in ("haiku", "sonnet", "opus"):
        if k in (raw or ""):
            return k
    return raw or "?"


def reconcile(date: str, root=None) -> dict:
    base = Path(root) if root else Path(".")
    scan = base / "context" / "scan" / date
    echo = json.loads((scan / "user_config_echo.json").read_text())
    rows = json.loads((scan / "_token_usage.json").read_text()).get("rows") or []
    agents_cfg = echo.get("agents") or {}
    mismatches, seen_types = [], set()
    gp_allowed = set()
    for shell in ("gp_shell", "gp_shell_json"):
        spec = {**{"model": "sonnet", "effort": "low"}, **(agents_cfg.get(shell) or {})}
        gp_allowed.add((spec["model"], spec["effort"]))
    for r in rows:
        if r.get("role") != "subagent":
            continue
        atype = r.get("agent") or ""
        seen_types.add(atype)
        got = (_norm_model(r.get("model")), r.get("effort") or "(unset)")
        if atype in AGENTTYPE_TO_ROLE:
            role = AGENTTYPE_TO_ROLE[atype]
            exp = {**_frontmatter(atype), **(agents_cfg.get(role) or {})}
            for field in ("model", "effort"):
                if field in exp and exp[field] != got[0 if field == "model" else 1]:
                    mismatches.append({"agent": atype, "field": field,
                                       "expected": exp[field],
                                       "actual": got[0 if field == "model" else 1]})
        elif atype == "general-purpose" and got not in gp_allowed:
            mismatches.append({"agent": atype, "field": "model+effort",
                               "expected": sorted(gp_allowed), "actual": list(got)})
    wire_breaks = [role for role in _EXPECT_PRESENT if role in agents_cfg
                   and not any(AGENTTYPE_TO_ROLE.get(t) == role for t in seen_types)]
    out = {"date": date, "ok": not mismatches and not wire_breaks,
           "mismatches": mismatches, "wire_breaks": wire_breaks, "checked": len(rows)}
    return out
    # CLI 包装:--json-out 落 _usage_reconcile.json;追加 context/learning/usage_reconcile.jsonl;
    # stdout 打 md 表(壳类行注明「集合断言,label 不入 harvest」);exit 恒 0 —— 报表不毙人。
```

- [ ] **Step 4: self_review 新 check**(`self_review.py`,与既有 check 同形):读
`context/scan/*/​_usage_reconcile.json` 最新一份;`ok=false` → warn「配置-实测不符(<date>)」;
且 `usage_reconcile.jsonl` 末两行皆 `ok=false` → severity 升 fail(连续两日=系统性,承
warn 升 binding 惯例)。测试:tmp fixture 两行 jsonl → fail。
- [ ] **Step 5: SKILL.md CP7 批次** `usage_harvest` 与 `post_run` 之间插入
`uv run --no-sync python -m autoresearch.trace.usage_reconcile <date> --json-out context/scan/<date>/_usage_reconcile.json && \`。
- [ ] **Step 6: 全量绿;Commit** `feat(trace): Wave11-B4 usage_reconcile 配置生效对账(变异可逮/断线可见/连续两日升 fail)`

### Task 10: 空配置 fail fast

**Files:** Modify `.claude/workflows/scan-market.js`、`.claude/workflows/l4-stock.js`(cfg 解析后);Modify `.claude/skills/scan-market/SKILL.md`(0.5 节「配置必传」段);Create `tests/workflows/test_empty_config_guard.py`。

- [ ] **Step 1: 两 js 的 cfg 常量后插入**

```js
// Wave11-B5(07-21 事故根治):空 cfg = 静默关 intel + 全体掉回缺省 effort,且当时无人知晓。
// 结构性拒绝替代文档叮嘱;确需空跑(离线试装)显式传 args.allow_empty_config=true。
const _allowEmpty = !!(typeof args === 'string' && args ? JSON.parse(args).allow_empty_config
                       : (args && args.allow_empty_config))
if (!Object.keys(cfg).length && !_allowEmpty) {
  throw new Error('args.config 为空 —— 会静默关 intel/降 effort(07-21 事故)。传 allow_empty_config:true 才可空跑。')
}
```

- [ ] **Step 2: 可执行探针测试**(pytest 壳起 node,AsyncFunction 真执行到 throw)

```python
# tests/workflows/test_empty_config_guard.py
"""Wave11 B5:空 config 必须在任何 agent 派发前抛错 —— 用桩 agent 证明「先于一切派发」。"""
import subprocess, textwrap

_PROBE = textwrap.dedent("""
  const fs = require('fs');
  const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
  let src = fs.readFileSync(process.argv[1], 'utf8').replace(/^export const meta/m, 'const meta');
  const agent = () => { throw new Error('AGENT_CALLED_BEFORE_GUARD'); };
  const fn = new AsyncFunction('agent','parallel','pipeline','log','phase','args','budget','workflow', src);
  // args 带 code:l4-stock.js 自己的「date/code 必填」校验在 cfg guard 之前,不带 code 会抛错但
  // 消息不含「为空」→ 测试假红。scan-market.js 忽略多余键,同一 args 两文件通用。
  fn(agent, null, null, () => {}, () => {}, {date: '2026-01-01', code: '600000', name: 'X', sector: 'Y'}, {total: null}, null)
    .then(() => { console.log('NO_THROW'); process.exit(1); })
    .catch(e => { console.log(e.message); process.exit(/为空/.test(e.message) ? 0 : 1); });
""")


def _probe(path):
    return subprocess.run(["node", "-e", _PROBE, path], capture_output=True, text=True)


def test_scan_market_guards_empty_config():
    r = _probe(".claude/workflows/scan-market.js")
    assert r.returncode == 0, r.stdout + r.stderr


def test_l4_stock_guards_empty_config():
    r = _probe(".claude/workflows/l4-stock.js")
    assert r.returncode == 0, r.stdout + r.stderr
```

- [ ] **Step 3: 跑红→绿**(guard 未加时 stdout=AGENT_CALLED… 或 NO_THROW → 红)。
- [ ] **Step 4: SKILL 0.5 节** 「⚠️ 配置必传」段末补:「Wave11 起为结构性强制:空 config 直接
throw,离线试装用 `allow_empty_config:true`」。
- [ ] **Step 5: T8 的 AsyncFunction 探针复跑 + 全量绿;Commit** `feat(workflow): Wave11-B5 空 config fail fast(节点可执行探针锁行为)`

---

## 批A 评判尺切换(T11–T19)

### Task 11: `common/ruler.py` + `forward_returns` 新列(parity 值)

**Files:**
- Create: `autoresearch/common/ruler.py`
- Modify: `autoresearch/research/factor_lab.py`(`forward_returns` 增列;`FWDS` 注册)
- Create: `tests/research/test_ruler_gap.py`

**Interfaces(produces):** `ruler.MAIN_RULER`(**本 task 初值 = "fwd_2_oc"**,T16 才换值)、
`ruler.GAP_CLIP = 0.31`、`ruler.TOUCH_COL`、`ruler.SCHEMA_SWITCH_V4`;
`forward_returns` 新列 `gap_c1_o2 / buyable_c1 / unsellable_o2`。

- [ ] **Step 1: ruler.py 全文**

```python
#!/usr/bin/env python3
"""主评判尺单点(Wave11 批A)。换尺 = 改 MAIN_RULER 一行;严禁在消费点散写列名字符串。

gap_c1_o2 = open[D+2]/close[D+1] − 1(2026-08-05 用户裁定:T+1 收盘买 → T+2 开盘卖,隔夜)。
沿革:fwd_2_oc(2026-07-10 裁定)→ gap_c1_o2(2026-08-05 裁定);旧列降参考不删。
"""
MAIN_RULER = "fwd_2_oc"      # T16 换 "gap_c1_o2";在那之前保持 parity
GAP_CLIP = 0.31              # 单日板极值:主板10/创业科创20/北交所30cm,取最宽+容差
ENTRY_FLAG = "buyable_c1"    # T+1 收盘封涨停=买不进 → 剔样本
EXIT_FLAG = "unsellable_o2"  # T+2 一字跌停开=卖不出 → 标旗不剔(剔了会美化)
TOUCH_COL = "gap_c1_o2"      # 隔夜窗唯一实现价=T+2 开 → 触价尺=gap 本身(设计稿 touch_o2 的去重简化)
SCHEMA_SWITCH_V4 = "9999-12-31"   # 卡契约 v4 日期分界;T17 执行日绑定真值,在那之前不影响任何旧卡判定
```

- [ ] **Step 2: 失败测试**

```python
# tests/research/test_ruler_gap.py
"""Wave11 A1:隔夜尺三列 —— 腿别搞错(gap≠oo≠oc),可执行域按收盘封板/一字跌停开。"""
import pandas as pd
from autoresearch.research.factor_lab import forward_returns


def _piv(rows):
    df = pd.DataFrame(rows, columns=["code", "date", "open", "high", "low", "close", "pct_chg"])
    return {f: df.pivot_table(index="code", columns="date", values=f)
            for f in ("open", "high", "low", "close", "pct_chg")}


P = ["D0", "D1", "D2"]

def test_gap_legs_exact():
    piv = _piv([("000001", "D0", 9.0, 9.9, 8.9, 9.5, 1.0),
                ("000001", "D1", 9.6, 10.2, 9.4, 10.0, 5.26),    # c1=10.0
                ("000001", "D2", 10.5, 11.0, 10.3, 10.8, 8.0)])  # o2=10.5
    r = forward_returns(piv, P, "D0", 10)
    assert abs(r.loc["000001", "gap_c1_o2"] - 0.05) < 1e-9       # 10.5/10.0−1;错腿(c2/c1=8%)会红


def test_buyable_c1_limit_up_close():
    piv = _piv([("300999", "D0", 10.0, 10.0, 10.0, 10.0, 0.0),
                ("300999", "D1", 11.0, 12.0, 11.0, 12.0, 20.0),  # 20cm 收盘=最高=封板
                ("300999", "D2", 12.5, 12.6, 12.4, 12.5, 4.2)])
    r = forward_returns(piv, P, "D0", 10)
    assert not bool(r.loc["300999", "buyable_c1"])


def test_unsellable_o2_one_line_down_open():
    piv = _piv([("600002", "D0", 10.0, 10.1, 9.9, 10.0, 0.0),
                ("600002", "D1", 10.0, 10.1, 9.9, 10.0, 0.0),    # c1=10.0(10cm)
                ("600002", "D2", 9.0, 9.3, 9.0, 9.1, -9.0)])     # o2=9.0=low=跌停开
    r = forward_returns(piv, P, "D0", 10)
    assert bool(r.loc["600002", "unsellable_o2"])
    assert abs(r.loc["600002", "gap_c1_o2"] + 0.10) < 1e-9       # 亏损留样本
```

- [ ] **Step 3: 跑红。Step 4: 实现**(`forward_returns` 内 `sealed` 判定后追加;`lim`/`col` 复用)

```python
    # 复用函数体既有的 pc1/h1/lim/c1(sealed 判定一带定义过),勿重复定义;只补 o2/l2。
    o2, l2 = col(o, 2), col(piv["low"], 2)
    res["gap_c1_o2"] = o2 / c1 - 1.0     # 隔夜主尺(2026-08-05 裁定):T+1 收买 → T+2 开卖
    # 买腿可执行:T+1 收盘未封涨停(收盘≈日高 且 当日涨幅≈板)—— 封板收盘买不进
    buy_sealed = (pc1 >= lim * 0.98) & (c1 >= h1 - 1e-6)
    res["buyable_c1"] = ~buy_sealed.fillna(False)
    # 卖腿受限:T+2 一字跌停开(开≈日低 且 开盘较 c1 跌≈板)—— 标旗不剔
    open_limit_dn = (o2 <= l2 + 1e-6) & (o2 <= c1 * (1 - lim * 0.98 / 100.0))
    res["unsellable_o2"] = open_limit_dn.fillna(False)
```

`FWDS` 追加 `"gap_c1_o2"`;IC 用尺处对 gap 列 `clip(-ruler.GAP_CLIP, ruler.GAP_CLIP)`
(在 `fwd_2_oc` 的 clip 分支旁并列一支,import ruler)。
- [ ] **Step 5: 跑绿。Step 6: 变异验鉴别力(不提交)**:临时把 `o2 / c1` 改 `col(c,2) / c1`,
`test_gap_legs_exact` 必须红;再临时翻符号,同测必须红;还原后绿。此步在 commit message 里记一行「变异体 2/2 被逮」。
- [ ] **Step 7: 全量绿;Commit** `feat(research): Wave11-A1 隔夜尺 gap_c1_o2 三列 + ruler 单点常量(MAIN_RULER 暂持 fwd_2_oc parity)`

### Task 12: 消费点常量化 sweep(parity commit)

**Files:** Modify 下表全部「改常量」文件(每处 `"fwd_2_oc"` 主尺字面量 → `from autoresearch.common.ruler import MAIN_RULER`);不改任何数值行为。

| 文件 | 处置 |
|---|---|
| research/factor_lab.py(默认 label_col×3 处)/consensus.py/channel_audit.py/candidates.py/sector_top3_backtest.py | 改常量 |
| learning/retro.py(成熟判据+归因主列)/stage_eval.py/channel_ledger.py/zero_buy_ledger.py/abstention_ledger.py/ensemble_ledger.py/gate_attribution.py/gate_ledger.py/l3_audit_ledger.py/catalyst_ledger.py/pinned_ledger.py/earlystop_ledger.py/rejection_attribution.py/cross_calib.py/lesson_yield.py/precedents.py/sentinel_audit.py/l3_marginal.py/evidence_manifest.py | 改常量 |
| scan/l2_slo.py/report_sections.py/recall/channels.py | 改常量(channels 内属校准说明注释的,更注释) |
| learning/buy_ledger.py | **只改**主尺读处;`_hi_col_for` 触价分界是 v4 域,T17 处理 |
| learning/t1_review.py | **不动**(自有 cc1/oc1 尺;gap 终判=T18) |
| learning/paper_nav.py | **不动**(T14 处理) |
| learning/shrink_replay.py | 跳过(T22 候删) |

- [ ] **Step 1: parity 底片**(改代码**前**):

```bash
W=$(mktemp -d); D=2026-07-31
uv run --no-sync python -m autoresearch.learning.retro attribute $D
md5 -q context/scan/$D/retro/attribution.csv > $W/before.txt
uv run --no-sync python -m autoresearch.learning.stage_eval $D >> $W/before.txt 2>&1 || true
echo "W=$W 记下备后比"
```

- [ ] **Step 2: 逐文件 sweep**(表为准;每文件先 `grep -n fwd_2_oc <file>` 逐处判「主尺/参考列/注释」)。
- [ ] **Step 3: parity 复核**:重跑 Step 1 两条生成命令,md5 与 `$W/before.txt` **逐字节一致**;
`grep -rn '"fwd_2_oc"' autoresearch --include='*.py' | grep -v "ruler.py\|参考尺\|hi_\|fwd_5\|fwd_10\|t1_review\|paper_nav\|shrink_replay"` → 残留逐条说明或清零。
- [ ] **Step 4: 全量绿;Commit** `refactor: Wave11-A2 主尺消费点常量化(MAIN_RULER 仍=fwd_2_oc,parity 有底片)`

### Task 13: 账本历史回填 gap 列

**Files:** Modify `autoresearch/learning/retro.py`(`refresh_attributions` 触发条件)、`autoresearch/learning/t1_review.py`(`backfill_day` 增列)+ 账本写行处加 `"ruler": MAIN_RULER` tag;Test: `tests/learning/test_gap_backfill.py`(新)。

- [ ] **Step 1: premise-check**:`grep -n "realized_returns\|forward_returns" autoresearch/learning/retro.py` 确认归因取数确实透传 forward_returns 全列(gap 列 T11 后自然在)。
- [ ] **Step 2: 失败测试**

```python
# tests/learning/test_gap_backfill.py
"""Wave11 A3:历史 attribution 只追加 gap 列 —— 旧列旧值不动,幂等。"""
import pandas as pd
from autoresearch.learning import retro


def test_refresh_adds_gap_cols_without_touching_old(tmp_path, monkeypatch):
    d = tmp_path / "2026-07-31" / "retro"
    d.mkdir(parents=True)
    old = pd.DataFrame({"code": ["000001"], "fwd_2_oc": [0.02], "bucket": ["hit"]})
    (d / "attribution.csv").write_text(old.to_csv(index=False))
    fake = pd.DataFrame({"code": ["000001"], "fwd_2_oc": [0.02], "gap_c1_o2": [0.05],
                         "buyable_c1": [True], "unsellable_o2": [False]})
    monkeypatch.setattr(retro, "realized_returns", lambda *a, **k: fake)
    retro.refresh_attributions(scan_root=tmp_path)          # 触发判据:缺 gap_c1_o2 列
    got = pd.read_csv(d / "attribution.csv")
    assert {"gap_c1_o2", "buyable_c1", "unsellable_o2"} <= set(got.columns)
    assert got["fwd_2_oc"].tolist() == [0.02] and got["bucket"].tolist() == ["hit"]
    before = (d / "attribution.csv").read_bytes()
    retro.refresh_attributions(scan_root=tmp_path)          # 幂等:第二跑零改动
    assert (d / "attribution.csv").read_bytes() == before
```

(refresh 遍历 scan_root 子目录的既有判据若还要求报告在场,fixture 按 Step 1 读到的真判据补
哨兵文件——**以源码为准,别让 fixture 撒谎**。)t1 侧同型:`backfill_day` 后 scorecard 增列、
旧列不动;价格假源抄 `tests/learning/test_t1_review.py` 既有 monkeypatch 形状。
- [ ] **Step 3: 实现**:`refresh_attributions` 的「需要刷新」判据追加
`or "gap_c1_o2" not in cols`;写回用 merge-on-code 只**新增列**;账本 jsonl 写行处
(`retro` 落账 + `t1_review` ledger 行)加 `"ruler": MAIN_RULER`(历史行不回写 tag,读侧
`row.get("ruler", "fwd_2_oc")` 兜底——旧行诚实标旧尺)。
- [ ] **Step 4: 真回填**:`uv run --no-sync python -m autoresearch.learning.retro refresh` 后抽
2026-07-28/07-31 各 2 票,用湖 OHLC 手工核 gap 值(命令写进 commit message)。
- [ ] **Step 5: 全量绿;Commit** `feat(learning): Wave11-A3 账本回填 gap 列(追加不改旧值)+ ruler tag(旧行读侧兜底 fwd_2_oc)`

### Task 14: paper_nav 隔夜模式

**Files:** Modify `autoresearch/learning/paper_nav.py`(`simulate` 加 `mode="oc"|"gap"`;主表调用切 gap、hold=2 降对照);Test: `tests/learning/test_paper_nav.py`(追加)。

- [ ] **Step 1: 先读** `paper_nav.py:59 simulate()` 全体(入场/出场取价行是哪两句)。
- [ ] **Step 2: 失败测试**(合成 3 日价:signal 日 T,c1=10、o2=10.5 → gap 模式单票 NAV=+5%;
oc 模式按现行=c2/o1;两模式同一 fixture 双断言,防「改了 gap 顺手弄坏 oc」)。
- [ ] **Step 3: 实现**:`simulate(..., mode="oc")`;`mode=="gap"` 时入场价=signal 日+1 收盘、
出场价=+2 开盘、区间外持币;渲染层主表三线(真实/影子/sized)+市场等权全部跑 `mode="gap"`,
hold=2/hold=10 两副表保留连续性。表头注「主表=隔夜尺(08-05 裁定);hold=2 为旧主尺对照」。
- [ ] **Step 4: 全量绿;Commit** `feat(learning): Wave11-A9 paper_nav 隔夜主表(hold=2 降对照)`

### Task 15: 两尺对照报告(30 日认知底片)

**Files:** Create `autoresearch/research/ruler_compare.py`(CLI+selftest);产物
`docs/research/2026-08-XX-ruler-gap-vs-oc-baseline.md`(XX=执行日)。

- [ ] **Step 1: 实现四节**(自包含,只读现成产物,不改任何 ledger 模块):
  ①逐日市场均值/买单/影子买单在两尺下的均值与门价值(读 attribution.csv 双列 + shadow_buys.csv);
  ②九路召回 unique 超额两尺排序(读 L1_channels.csv + 双列);
  ③L3 真选 edge 两尺(finalists 真选 vs bench,读 _l3_judged/finalists + 双列);
  ④弃权日裁决翻转数(abstention 判据在 gap 列下重算,列出翻转日)。
  尾节「作废/待重验清单」:两尺符号相反的历史结论逐条点名(至少覆盖:value 路第一、
  momentum 相位条件性、0买日空仓正确性)。
- [ ] **Step 2: selftest**(合成两日数据断言四节数值);跑真 30 日落盘。
- [ ] **Step 3: Commit** `research: Wave11-A8 两尺对照报告(gap vs fwd_2_oc,30 日)——换值前认知底片`

### Task 16: 换值 flip + 权重腿 + regimes 修缮

**Files:** Modify `autoresearch/common/ruler.py`(`MAIN_RULER = "gap_c1_o2"`)、
`autoresearch/learning/retro.py`(`recalibrate_and_log` 的 `log_change` 调用加 `label_col=MAIN_RULER`)、
`autoresearch/learning/changelog_ledger.py`(heartbeat 行渲染 label_col);weights 产物变更。

- [ ] **Step 1: 快照留底** 记录 `context/factor_lab/weights.json` 当前 sha 于 commit message。
- [ ] **Step 2: flip** `MAIN_RULER = "gap_c1_o2"`;全量测试(涉及主尺数值的既有测试若断言
fwd_2_oc 具体值,先读其 docstring:锁「主尺行为」的改喂 gap 预期,锁「fwd_2_oc 列本身」的
改显式列名——**不许为过测把主尺断言删掉**)。
- [ ] **Step 3: 重校准**

```bash
uv run --no-sync python -c "from autoresearch.learning.retro import recalibrate_and_log as r; import json; print(json.dumps(r('$(date +%F)'), ensure_ascii=False))"
uv run --no-sync python -c "import autoresearch.research.factor_lab as fl; print(fl.calibrate_regimes())"
```

- [ ] **Step 4: regimes 二选一裁定**(病灶②清偿,不许留第三态):两半符号一致门**过** →
regimes 块落盘,`--regime-aware` 维持;**不过** → weights 留 flat,并从
`.claude/skills/scan-market/SKILL.md` 步骤 1 命令与 `autoresearch/scan/prelude.py` 的
universe 调用里摘掉 `--regime-aware`(grep 两处),汇总屏那条 B 级降级从此消失。执行的
分支与门读数写进 commit message。
- [ ] **Step 5: 心跳断言** `uv run --no-sync python -m autoresearch.learning.changelog_ledger`
渲染含 `label_col=gap_c1_o2`(会变的量,防「换了个寂寞」)。
- [ ] **Step 6: Commit** `feat!: Wave11-A4 主尺换值 gap_c1_o2 + 权重按新尺重校准(快照 <sha>;regimes 裁定=<分支>)`

### Task 17: 卡契约 v4(代码侧)

**Files:** Modify `autoresearch/common/ruler.py`(`SCHEMA_SWITCH_V4` 绑执行日,如 "2026-08-07")、
`autoresearch/learning/buy_ledger.py`(`_hi_col_for` 三段)、📐 目标校准生产处
(`grep -rn "目标价校准\|target_calib" autoresearch/learning autoresearch/scan/l4` 定位)、
`autoresearch/learning/self_review.py`(v4 卡缺口径声明 → warn);Test: `tests/learning/test_card_v4_switch.py`(新)。

- [ ] **Step 1: 失败测试**

```python
from autoresearch.learning.buy_ledger import _hi_col_for
from autoresearch.common import ruler

def test_hi_col_three_way():
    assert _hi_col_for("2026-07-09") == "hi_10_oc"          # v3 前旧 swing 卡
    assert _hi_col_for("2026-07-15") == "hi_2_oc"           # v3 超短卡
    assert _hi_col_for(ruler.SCHEMA_SWITCH_V4) == ruler.TOUCH_COL   # v4 起触价=T+2 开
```

- [ ] **Step 2: 实现**:`_hi_col_for` 引 `ruler.SCHEMA_SWITCH_V4`/`ruler.TOUCH_COL` 三段返回;
📐 生产处按同分界取列并把当日件文案改「目标带 vs T+2 开盘(v4)」,`hi_2_oc` 读数以
「(参考:2 日盘中触达 …)」双列过渡 ≥20 交易日;self_review 增 check:v4 分界日起的卡缺
`〔卡契约 v4·隔夜 c1→o2〕` 标记行 → warn(标记行本体 T24 进模板)。
- [ ] **Step 3: 全量绿;Commit** `feat(learning): Wave11-A5 卡契约 v4 代码侧 —— 触价三段分界+📐 改隔夜口径+v4 声明检查`

### Task 18: t1_review gap 终判 + nightly 接线

**Files:** Modify `autoresearch/learning/t1_review.py`(新 `gap_finalize_pending(today)`)、
`autoresearch/learning/nightly_close.py`(steps 加 `t1_gap_finalize`);Test: `tests/learning/test_t1_review.py`(追加)。

- [ ] **Step 1: 先读** `t1_review.py` 的 `finalize()`(:613)与 ledger 整替写法、
`tests/learning/test_t1_review.py` 的 fake 价格 fixture。
- [ ] **Step 2: 失败测试**:合成 T 日 scorecard(cc1 判「准」),D+2 价格使 gap 为负超阈 →
`gap_finalize_pending` 后 scorecard 增列 `gap_c1_o2/z_gap/final_verdict="不准"`,ledger 行
被整替且含双 verdict;幂等重跑 0 改动。
- [ ] **Step 3: 实现**:扫描 `context/scan/*/t1_review/scorecard.csv` 中缺 `final_verdict`
且 T+2 价已发布的日;z 法/阈值复用 v2 常量(`_Z_DIR/_Z_SURPRISE/_MIN_EXCESS`,行业中性同法,
尺=gap);模块头两尺说明改:「终评尺=gap_c1_o2(08-05 裁定);cc1 降 D+1 初判尺」。
nightly_close `run()` 的 steps 元组在 `t1_backfill` 后插 `("t1_gap_finalize", _t1_gap_finalize)`:

```python
    def _t1_gap_finalize() -> str:
        from autoresearch.learning import t1_review
        n = t1_review.gap_finalize_pending(today)
        return f"gap 终判回填 {n} 日" if n else "无待终判日"
```

- [ ] **Step 4: t1-review workflow prompt**(`.claude/workflows/t1-review.js` 合诊 prompt 段)
补一句:「终评尺=隔夜 gap;初判(cc1)与终判相反的票必须解释隔夜发生了什么」。AsyncFunction 探针。
- [ ] **Step 5: 全量绿;Commit** `feat(learning): Wave11-A6 t1 快环 gap 终判(D+2 nightly 回填,准不准以 gap 为准)`

### Task 19: retro pending 拆分 + scan-retro 批量诊断

**Files:** Modify `autoresearch/learning/retro.py`(新 `attribution_pending()`;CLI `pending` 双段输出)、
`autoresearch/scan/prelude.py`(建议行文案,grep「已备料但未收尾」定位)、
`.claude/skills/scan-retro/SKILL.md` + `retro-playbook.md`(批量补诊断节);Test 追加。

- [ ] **Step 1: 失败测试**(fixture 哨兵文件以 `pending_days` 真判据为准——先读 retro.py:429-460)

```python
def _mk_day(root, day, *, attr, done):
    d = root / day
    (d / "retro").mkdir(parents=True)
    # 按 pending_days 判据补「有 L1 面板 + 有报告」的哨兵文件(Step 0 读源码后填真实文件名);
    if attr:
        (d / "retro" / "attribution.csv").write_text("code\n000001\n")
    if done:
        (d / "retro" / "done.json").write_text("{}")


def test_pending_split(tmp_path, monkeypatch):
    monkeypatch.setattr(retro, "_fwd_realized", lambda day, **k: True, raising=False)  # 名以源码为准
    _mk_day(tmp_path, "2026-08-01", attr=False, done=False)
    _mk_day(tmp_path, "2026-08-02", attr=True, done=False)
    _mk_day(tmp_path, "2026-08-03", attr=True, done=True)
    assert retro.attribution_pending(scan_root=tmp_path) == ["2026-08-01"]
    assert retro.pending_days(scan_root=tmp_path) == ["2026-08-01", "2026-08-02"]
```

CLI `pending` 输出含「归因欠账」「诊断欠账(已备料)」两段且各列对号(capsys 断言两个子串)。
- [ ] **Step 2: 实现**(`pending_days` 语义与名字不动,docstring 注明=诊断欠账;CLI 分段打印)。
- [ ] **Step 3: scan-retro 文档**加节:

```markdown
## 批量补诊断(欠账 ≥2 日时)
一次 t1-review/retro 诊断 workflow 吃 ≤5 日的 retro_input.md 合诊(跨日看系统性病因;
>5 日分批)。每日诊断落该日 retro/ 后立即 `mark_done`(触发 decay_lessons);
清账判据 = `retro pending` 的「诊断欠账」段为空。触发词:「补复盘欠账」。
```

- [ ] **Step 4: 首验**:对现存 6 日欠账实跑一轮批量补诊断(两批:5+1),`retro pending` 诊断段清零。
- [ ] **Step 5: 全量绿;Commit** `feat(learning): Wave11-A7 retro 欠账拆两账 + scan-retro 批量补诊断(首验清 6 日)`

---

## 批D Skill 整备(T20–T24)

### Task 20: 六 skill description 重写

**Files:** Modify 六个 `.claude/skills/*/SKILL.md` 的 frontmatter `description:`(**先 Read 重读**)。新文案(定稿,原样落):

- **scan-market**:`Use when the user wants to scan the WHOLE A-share market to discover buy-worthy stocks AND strong sectors — 「扫描全A股」「全市场选股」「哪些板块值得买」「find the best A-share buys」. Deterministic L0-L2 funnel + Claude L3/L4/L5; artifacts → reports/scan/<run_id>/. NOT for: one named ticker (→ stock-research; 持仓单票复核走其 lite 档), reviewing a past scan day (→ scan-retro), cross-asset macro (→ macro-research). Project-local.`
- **stock-research**:`Two-tier single-ticker research. FULL deep-dive report by default (「研究 NVDA」「分析 600519.SS」, peers ok); LITE decision card (5-tier rating + 隔夜口径 R:R + tripwires) when speed is asked (「快速看一眼」「出张决策卡」) — lite is also the workhorse scan-market L4 invokes per finalist and the pinned-holdings review path on sentinel days. NOT for whole-market scans (→ scan-market) or macro (→ macro-research). Project-local.`
- **macro-research**:`Top-down GLOBAL + 中美 macro → cross-asset tilts AND A股行业配置 read (「研究全球宏观」「现在该超配什么资产」). Also owns the LITE 市场研判 daily brief: invoked by scan-market Stage 0 or 「今天大盘怎么看」, writes market_view.md from the deterministic market_pack. NOT for one ticker (→ stock-research), a full A-share screen (→ scan-market), or single-industry depth (→ sector-research). Project-local.`
- **sector-research**:`Single A-share INDUSTRY (申万一级) research — 景气度/产业链/竞争格局/资金地形/龙头映射 (「研究半导体行业」「创新药板块怎么样」). Also owns the LITE sector brief scan-market invokes at Stage 1: a two-段 machine contract (地形段喂 L3/L4;研判段仅 L5). NOT for one ticker (→ stock-research), whole-market (→ scan-market), cross-asset (→ macro-research). Project-local.`
- **scan-retro**:`Two review loops for prior scan-market days: FAST t1_review (D+1 initial + D+2 gap final verdict, per-card judgment accuracy via t1-review workflow) and SLOW retro (D+2, funnel recall attribution + auto weight recalibration + lessons). Triggers: /retro, 「复盘昨天的扫描」「为什么没选到X」, scan-market finding unreviewed days, or 「补复盘欠账」(batch diagnosis, ≤5 days/run). scan-market only. Project-local.`
- **feedback**:`Capture user reactions to research output (correction/complaint/praise/「记住」「这个评级错了」「你漏了X」) into the closed-loop store, and adjudicate pending pr_* proposals (「裁决提案」) — distils lessons that feed future scans. Works across scan-market / stock-research / macro-research. Project-local.`

- [ ] **Step 1: 逐文件替换;Step 2: 触发冒烟**:新开会话说「扫描全A股」「快速看一眼 600519」
「补复盘欠账」各应命中对应 skill(记录在 commit message,人工验证)。
- [ ] **Step 3: Commit** `docs(skill): Wave11-D1 六 skill description 重写(触发/反触发/被调关系)`

### Task 21: SKILL.md 瘦身 + 退役标记处置台账

**Files:** Modify `.claude/skills/scan-market/SKILL.md`(→≤14KB)与 `STAGES.md`(接收沿革);
Create `docs/research/2026-08-XX-skill-tombstone-ledger.md`(处置台账)。

- [ ] **Step 1: 生成台账底稿** `grep -rn "已退役\|已移除\|勿再跑\|已废弃" .claude/skills --include='*.md'`(现值 22 处)逐行三分类:①防复发墓碑(留原位)②纯沿革(删句,git 留档)③引用已死符号的活指令(=bug,改)。台账落盘并 commit——**先台账后动刀**。
- [ ] **Step 2: 按台账执行**;SKILL.md 搬迁对象=沿革叙事/参数快照/历史实测段 → STAGES.md 对应节;`wc -c` 验 ≤14336。
- [ ] **Step 3: 全量绿(锚测试可能引用 SKILL 文段,红了先读测试 docstring);Commit** `docs(skill): Wave11-D2 SKILL 瘦身+22 处退役标记处置(台账在 docs/research)`

### Task 22: 死码删除(3 模块,协议四步)

**Files:** Delete `autoresearch/learning/{wave10_experiments,process_backfill,shrink_replay}.py` 及
`tests/learning/test_{wave10_experiments,process_backfill,shrink_replay}.py`;
Modify `autoresearch/learning/nightly_close.py`(names 表加注)。

- [ ] **Step 1: 协议①** 逐个读三个 test 的 docstring 查双职——若某 test 顺带锁了活契约,该
模块降级「只删生产文件、test 改造保留契约」并在 commit 说明。
- [ ] **Step 2: 协议③ 裸名复核**(勿省——D3 首版就是在这栽的):
`for m in wave10_experiments process_backfill shrink_replay; do grep -rn "$m" autoresearch .claude docs ~/Library/LaunchAgents --include='*' 2>/dev/null | grep -v "learning/$m.py\|test_$m\|docs/plans/2026-07\|docs/specs/2026-08-05\|本计划"; done` → 除历史文档外零命中。
- [ ] **Step 3: 删除 + nightly 注**:`_ledgers` 的 names 列表上方加
`# ⚠️ 动态调用面:本表以字符串拼名 import —— 删任何 learning 模块前先查这张表(2026-08-06 D3 勘误教训)`。
- [ ] **Step 4: 协议④** 全量绿 + `uv run --no-sync python -m autoresearch.learning.nightly_close`
一次真跑(汇总行无新增 ✗)。
- [ ] **Step 5: Commit** `chore(learning): Wave11-D3 删 3 个零引用模块(裸名复核过;gate_recal/l3_marginal/sentinel_audit 为 nightly 活体,勿删)`

### Task 23: 文档 lint(退役符号 + 内联字面量)

**Files:** Modify product_shape_lint 模块(先 `grep -rln "product_shape_lint" autoresearch` 定位真身)+ 其测试文件(`tests/learning/test_product_shape_lint.py`)。

- [ ] **Step 1: 失败测试**:fixture md 含 `python -m autoresearch.scan.progress`(指令性引用)→ fail;
含「observe_watchlist 已退役,勿再跑」(墓碑,同行有退役标记)→ pass;fixture js 含
`model: 'haiku'` 于 AGENT_DEFAULTS 外 → fail。
- [ ] **Step 2: 实现**:`RETIRED_SYMBOLS = ["observe_watchlist", "scan.progress", "l4_reuse",
"stable_context_blocks", "sector_brief_mode", "redteam_prob", "analyze-ticker",
"watchlist_trigger"]`;规则=行含符号 ∧ 本行无 `已退役|已移除|勿再|已废弃|退役` 标记 → fail
(墓碑白名单按行,不按文件);js 规则=`model:|effort:` 字面量出现在 `AGENT_DEFAULTS` 块外 → fail。
扫描域 `.claude/{skills,agents,workflows}`。挂进该 lint 现有 CLI/调用位(读真身后接)。
- [ ] **Step 3: 全量绿(现有 .claude 树须先过——红了回 T21 台账补处置);Commit** `feat(lint): Wave11-D4 退役符号指令性引用 + workflow 内联字面量 进 product_shape_lint`

### Task 24: 卡模板 v4 文案(依赖 T17)

**Files:** Modify `.claude/agents/l4-card.md` 与 `.claude/skills/stock-research/lite-playbook.md`(**先 Read 重读**);锚测试(`grep -rln "test_l4_prompt_cache_prefix" tests` 定位)按其自有更新协议重锚。

- [ ] **Step 1: 两模板的评级/目标段改隔夜口径**,机器契约标记行(两文件一字不差):

```markdown
〔卡契约 v4·隔夜 c1→o2〕目标带与 EV 均指 T+2 开盘;止损=T+1 尾盘入场否决条件;
开盘预案三分支:高开兑现 / 平开按带 / 低开(跌停开=卖不出,预案必须先写)。
```

三情景 R:R 表头「D+2 收盘」→「T+2 开盘」;tripwire 段加口径声明行(self_review T17 检查的
就是本标记行)。
- [ ] **Step 2: 锚测试重锚**(读该测试 docstring 按其固定流程更新期望字节;**不许**为过测放宽前缀断言——它锁的是 prompt cache 前缀稳定性)。
- [ ] **Step 3: 全量绿;Commit** `docs(card): Wave11-D5 卡契约 v4 模板(隔夜口径+机器标记行),锚测试重锚`

---

## 验收总表(全批完成的定义)

| 批 | 硬验收 |
|---|---|
| C | 首个真实扫描日:一条消息全派、task_book 全 SUCCEEDED、`l4_tasks stats` 无 RATE_LIMIT 风暴、L4 段墙钟对照 76m51s 基线;registry 里 exp_l4_full_parallel=PREREGISTERED |
| B | 拼错 role raise;CP7 第五条 reconcile 全 ✓;变异 config 被逮(测试);空 cfg throw(node 探针) |
| A | 变异体 2/2 被逮;A2 parity 底片 byte 一致;抽样 gap 值手核 ✓;changelog 现 label_col=gap_c1_o2;对照报告落盘含「作废/待重验清单」;6 日诊断欠账清零;首个 v4 卡带标记行 |
| D | 六 description 落稿+触发冒烟;台账 22/22;3 模块删净全测绿+nightly 真跑无新 ✗;lint 对已知退役符号能红 |

## 回滚杆速查

| 批 | 杆 |
|---|---|
| C | `scan_config.budgets.concurrency.l4_stock=4` |
| B | 删 scan_config roles 段(AGENT_DEFAULTS parity);reconcile/guard 独立 revert |
| A | `ruler.MAIN_RULER="fwd_2_oc"` + weights 快照 sha 恢复 + `SCHEMA_SWITCH_V4="9999-12-31"` |
| D | 纯文档/删除各自 git revert |
