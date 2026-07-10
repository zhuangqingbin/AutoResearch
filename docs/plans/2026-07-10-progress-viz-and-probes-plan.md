# 进度可视双层 + 机构数据两探针 · 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** scan-market workflow 运行全程零 LLM token 可见进度(前置 log/逐卡计数/墙钟表激活),并跑通 report_rc 历史回填与 fund_portfolio 两个机构数据探针。

**Architecture:** 脚本层在 `.claude/workflows/scan-market.js` 加前置 log + pipeline 完成计数 + phase 归组(纯 JS 字符串,零 token);Python 层新建 `autoresearch/scan/stage_timing.py` 从产物 mtime 链推导各阶段墙钟并写 `_stage_timing.json`(绕开 workflow 沙箱禁 `Date.now()` 的根因),assemble 墙钟表即刻有数;探针只验证端点可行性,接线留给主波计划。

**Tech Stack:** Python 3 + pandas + pytest(uv 管理)、workflow JS(harness 沙箱)。

**Spec:** `docs/specs/2026-07-10-ultrashort-t2-inst-progress-design.md` §4(波 3)、§3.2/§3.4(两探针)。

## Global Constraints

- 一律 `uv run --no-sync python ...`,在仓库根目录跑(CLAUDE.md 约定,防误删 venv-only 依赖)。
- 每个 task 收尾:`uv run --no-sync python -m ruff check .` + `uv run --no-sync python -m pytest tests/ -q` 全绿才 commit(仓库门约定;当前基线 788+ 绿)。
- presence-gated parity:锚文件缺 → 对应 key 略过 → 渲染回退 `—`,不加噪、不抛。
- commit 走仓库中文 conventional 风格(`feat(scan): ...` / `test(scan): ...`),正文尾加 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。
- 执行进度记 `.superpowers/sdd/progress.md`(沿用现有头部六要素 + `Task N:` 段格式)。

---

### Task 1: `stage_timing.py` — mtime 推导墙钟(修 `_stage_timing.json` 有读无写)

**Files:**
- Create: `autoresearch/scan/stage_timing.py`
- Modify: `autoresearch/scan/assemble.py:180-187`(`_tmap` 读取块,行号近似,按下方 old 代码块精确匹配)
- Test: `tests/scan/test_stage_timing.py`

**Interfaces:**
- Consumes: `context/scan/<date>/` 下的 staging 产物 mtime(`_t0.json` 由 Task 2 产,缺则回退 `market_pack.json`)。
- Produces: `derive_stage_timing(det: Path) -> dict`、`ensure_stage_timing(det: Path) -> dict`(返回 `{key: {"wall_s": int}}`,key ∈ assemble 墙钟表消费的 `L0L1L2/策略师/行业brief/L3精排/L4slim/L4研究/总计`);`ensure_*` 合并写回 `_stage_timing.json`,**已有 key 优先**、never raises。

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_stage_timing.py
"""墙钟 mtime 推导契约:锚全在→7键齐;锚缺→键略过;负跨度略过;已有键优先且推导补缺写回。"""
import json
import os
import time
from pathlib import Path

from autoresearch.scan.stage_timing import derive_stage_timing, ensure_stage_timing


def _touch(p: Path, ts: float):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x", encoding="utf-8")
    os.utime(p, (ts, ts))


def _fixture(tmp_path: Path) -> Path:
    det = tmp_path / "context" / "scan" / "2026-07-10"
    t = time.time() - 10_000
    _touch(det / "_t0.json", t)
    _touch(det / "market_pack.json", t + 30)
    _touch(det / "market_view.md", t + 120)
    _touch(det / "L2_gbdt_top200.csv", t + 600)
    _touch(det / "sector_briefs" / "半导体.md", t + 900)
    _touch(det / "_l3_table.md", t + 960)
    _touch(det / "_l3_judged.json", t + 1800)
    _touch(det / "_l4_prompt_000001.md", t + 1900)
    _touch(det.parent.parent / "000001.SZ_2026-07-10_slim.md", t + 2200)
    _touch(det / "details" / "000001.md", t + 2500)
    return det


def test_derive_all_keys(tmp_path):
    tm = derive_stage_timing(_fixture(tmp_path))
    assert tm["L0L1L2"]["wall_s"] == 600          # _t0 → L2 csv
    assert tm["策略师"]["wall_s"] == 90            # pack → view
    assert tm["行业brief"]["wall_s"] == 300        # max(L2,view)=t+600 → brief
    assert tm["L3精排"]["wall_s"] == 840           # 表 → judged
    assert tm["L4slim"]["wall_s"] == 300           # prompts → slim
    assert tm["L4研究"]["wall_s"] == 600           # prompts → 卡
    assert tm["总计"]["wall_s"] == 2500            # _t0 → 最晚产物


def test_missing_anchor_skips_key(tmp_path):
    det = _fixture(tmp_path)
    (det / "_l3_judged.json").unlink()
    tm = derive_stage_timing(det)
    assert "L3精排" not in tm
    assert "L0L1L2" in tm                          # 其余键不连坐


def test_all_reused_cards_negative_span_skipped(tmp_path):
    det = _fixture(tmp_path)                        # 全复用卡:卡 mtime 早于 prompts → 负跨度
    old = (det / "_l4_prompt_000001.md").stat().st_mtime - 50
    os.utime(det / "details" / "000001.md", (old, old))
    assert "L4研究" not in derive_stage_timing(det)


def test_ensure_respects_existing_and_writes(tmp_path):
    det = _fixture(tmp_path)
    (det / "_stage_timing.json").write_text(json.dumps({"L3精排": {"wall_s": 7}}), encoding="utf-8")
    merged = ensure_stage_timing(det)
    assert merged["L3精排"]["wall_s"] == 7          # 编排/人工写的优先
    on_disk = json.loads((det / "_stage_timing.json").read_text(encoding="utf-8"))
    assert on_disk["L0L1L2"]["wall_s"] == 600       # 推导补缺已写回
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_stage_timing.py -v`
Expected: FAIL,`ModuleNotFoundError: No module named 'autoresearch.scan.stage_timing'`

- [ ] **Step 3: 实现 `stage_timing.py`**

```python
# autoresearch/scan/stage_timing.py
#!/usr/bin/env python3
"""墙钟推导:从 staging 产物 mtime 链推各阶段耗时,写 `_stage_timing.json`(零 LLM)。

why mtime:workflow 沙箱禁 `Date.now()`(docs/specs/2026-07-07-scan-market-workflow-plan.md:693),
编排层写不了计时;但每阶段产物的落盘时刻天然=该阶段结束时刻。推导只补缺席 key
(编排/人工写过的一律尊重);锚缺/跨度为负(如当日全复用卡)→ 略过该 key,渲染回退 `—`。
键与 assemble 墙钟表消费口径一致。「总计」= t0 → 最晚产物(不含 assemble 自身,诚实下界)。
design: docs/specs/2026-07-10-ultrashort-t2-inst-progress-design.md §4.2
"""
from __future__ import annotations

import json
from pathlib import Path


def _mt(p: Path) -> float | None:
    return p.stat().st_mtime if p.is_file() else None


def _mx(paths) -> float | None:
    ts = [p.stat().st_mtime for p in paths if p.is_file()]
    return max(ts) if ts else None


def _maxopt(*xs) -> float | None:
    xs = [x for x in xs if x]
    return max(xs) if xs else None


def derive_stage_timing(det: Path) -> dict:
    """从 mtime 锚推导 `{key: {"wall_s": int}}`;锚缺/负跨度 → 略过该 key。"""
    det = Path(det)
    t0 = _mt(det / "_t0.json") or _mt(det / "market_pack.json")
    pack = _mt(det / "market_pack.json")
    view = _mt(det / "market_view.md")
    l2 = _mt(det / "L2_gbdt_top200.csv")
    table = _mt(det / "_l3_table.md")
    judged = _mt(det / "_l3_judged.json")
    briefs = _mx((det / "sector_briefs").glob("*.md")) if (det / "sector_briefs").is_dir() else None
    prompts = _mx(det.glob("_l4_prompt_*.md"))
    cards = _mx((det / "details").glob("*.md")) if (det / "details").is_dir() else None
    slim_root = det.parent.parent
    slims = _mx(slim_root.glob(f"*_{det.name}_slim.md")) if slim_root.exists() else None

    spans = {
        "L0L1L2": (t0, l2),
        "策略师": (pack, view),
        "行业brief": (_maxopt(l2, view), briefs),     # L3 相位始于 Prelude barrier 之后
        "L3精排": (table, judged),
        "L4slim": (prompts, slims),
        "L4研究": (prompts, cards),
        "总计": (t0, _maxopt(cards, judged, briefs, l2)),
    }
    out: dict = {}
    for k, (a, b) in spans.items():
        if a and b and b >= a:
            out[k] = {"wall_s": int(b - a)}
    return out


def ensure_stage_timing(det: Path) -> dict:
    """读 `_stage_timing.json`(已有 key 优先)+ mtime 推导补缺 → 合并写回。never raises。"""
    det = Path(det)
    fp = det / "_stage_timing.json"
    existing: dict = {}
    if fp.is_file():
        try:
            existing = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — 坏文件当空,推导重建
            existing = {}
    try:
        derived = derive_stage_timing(det)
    except Exception:  # noqa: BLE001 — 计时可选,不挡 assemble
        derived = {}
    merged = {**derived, **existing}
    if merged and merged != existing:
        try:
            fp.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    return merged
```

- [ ] **Step 4: assemble 接线(替换 `_tmap` 读取块)**

在 `autoresearch/scan/assemble.py` 找到这段(墙钟表前,约 :180 段落;上下文锚=注释行「每阶段墙钟(编排层写 _stage_timing.json…」):

```python
    import json as _json
    _tmap: dict = {}
    _tp = det / "_stage_timing.json"
    if _tp.is_file():
        try:
            _tmap = _json.loads(_tp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _tmap = {}
```

整块替换为:

```python
    from autoresearch.scan.stage_timing import ensure_stage_timing
    _tmap: dict = ensure_stage_timing(det)   # mtime 推导补缺 + 写回;编排写过的 key 优先
```

并把该表「合计」行注释里的「墙钟需编排写 _stage_timing.json」改为「墙钟 = mtime 推导下界(stage_timing.py)」——即 `lines.append(f"| **合计** | ...` 那行末尾的说明文案。

- [ ] **Step 5: 跑测试 + 全量回归**

Run: `uv run --no-sync python -m pytest tests/scan/test_stage_timing.py tests/scan/test_assemble.py -v`
Expected: 新测试 4 个 PASS,assemble 既有测试不红(墙钟缺锚时全 `—`,行为同旧)。
Run: `uv run --no-sync python -m ruff check . && uv run --no-sync python -m pytest tests/ -q`
Expected: 全绿。

- [ ] **Step 6: Commit**

```bash
git add autoresearch/scan/stage_timing.py autoresearch/scan/assemble.py tests/scan/test_stage_timing.py
git commit -m "feat(scan): 墙钟 mtime 推导写 _stage_timing(修有读无写·assemble 耗时表激活)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: prelude 写 `_t0.json` 起点锚

**Files:**
- Modify: `autoresearch/scan/prelude.py:47-49`(`run_prelude` 函数体开头)
- Test: `tests/scan/test_prelude_t0.py`

**Interfaces:**
- Consumes: 无(prelude 总入口自足)。
- Produces: `context/scan/<date>/_t0.json`(内容无关紧要,**mtime 即计时锚**);Task 1 的 `derive_stage_timing` 消费它。已存在不覆盖 → prelude-retry/重跑不重置起点。

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_prelude_t0.py
"""prelude t0 锚契约:入口即写、重跑不覆盖(retry 不重置计时起点)。全 skip 跑法零网络。"""
import time

from autoresearch.scan.prelude import run_prelude

_ALL = ("retro_refresh", "retro_pending", "consensus", "universe", "calendar",
        "watchlist", "catalyst", "menu", "ledgers")


def test_t0_written_once(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_prelude("2026-07-10", skip=_ALL)
    fp = tmp_path / "context" / "scan" / "2026-07-10" / "_t0.json"
    assert fp.exists()
    m1 = fp.stat().st_mtime
    time.sleep(0.05)
    run_prelude("2026-07-10", skip=_ALL)
    assert fp.stat().st_mtime == m1               # 不覆盖
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_prelude_t0.py -v`
Expected: FAIL,`assert fp.exists()` 不成立。

- [ ] **Step 3: 实现**

`prelude.py` 中 `run_prelude` 开头(`scan_dir = Path("context/scan") / date` 之后)插一行 `_write_t0(scan_dir)`,并在 `run_prelude` 定义上方加模块级 helper:

```python
def _write_t0(scan_dir: Path) -> None:
    """墙钟 t0 标记:mtime 即 stage_timing 的起点锚,内容仅自述。
    已存在不覆盖(prelude-retry/重跑不重置起点);失败不挡 prelude。"""
    try:
        scan_dir.mkdir(parents=True, exist_ok=True)
        fp = scan_dir / "_t0.json"
        if not fp.exists():
            fp.write_text('{"purpose": "stage_timing 起点锚(mtime)"}', encoding="utf-8")
    except Exception:  # noqa: BLE001 — 计时锚可选
        pass
```

```python
def run_prelude(date: str, regime_aware: bool = True, skip: tuple[str, ...] = ()) -> list[dict]:
    scan_dir = Path("context/scan") / date
    _write_t0(scan_dir)
```

- [ ] **Step 4: 跑测试 + 全量回归**

Run: `uv run --no-sync python -m pytest tests/scan/test_prelude_t0.py tests/scan/ -q`
Expected: 全绿(若 `run_prelude` 全 skip 路径在既有测试有覆盖,确认无副作用)。
Run: `uv run --no-sync python -m ruff check . && uv run --no-sync python -m pytest tests/ -q`
Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
git add autoresearch/scan/prelude.py tests/scan/test_prelude_t0.py
git commit -m "feat(scan): prelude 落 _t0.json 计时起点锚(mtime·重跑不覆盖)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: workflow 脚本层 — 前置 log + 逐张计数 + phase 归组

**Files:**
- Modify: `.claude/workflows/scan-market.js`(全文件 148 行,改动 ~30 行)

**Interfaces:**
- Consumes: 现有 `bash()/gate()` helper 与四个 phase;`plan.dispatch`/`sectors` 数组(计数来源)。
- Produces: 用户侧可见的 log 流(纯脚本字符串,零 LLM token);进度树里所有确定性步骤归入四相位分组。**不改任何命令/门语义。**

- [ ] **Step 1: helper 加 phase 透传**

`bash()`(:28-32)与 `gate()`(:44-48)各加第三/第四参数 `phase`,spread 进 agent opts:

```javascript
function bash(cmd, label, phase) {
  return agent(
    `在仓库根目录精确执行下面这条命令,然后只回报:退出码 + stdout 末 15 行。不要做别的、不要判断、不要解释。\n\n\`\`\`\n${cmd}\n\`\`\``,
    { agentType: 'general-purpose', effort: 'low', label, ...(phase ? { phase } : {}) })
}
```

```javascript
function gate(label, cmd, schema, phase) {
  return agent(
    `执行:\`${cmd}\`\n它会向 stdout 打印一行 JSON。把那行 JSON 原样作为你的结构化返回(字段不改、不增删)。`,
    { agentType: 'general-purpose', effort: 'high', label, schema, ...(phase ? { phase } : {}) })
}
```

- [ ] **Step 2: 全部调用点补 phase 实参**

按所在相位逐个补(label 不变):`frame`/`prelude/universe`/`l2-check`/`prelude-retry`/`GATE1` → `'Prelude'`;哨兵分支的 `assemble`/`GATE4` → `'Assemble'`;`sector-pack`/`l3-prepare`/`finalists`/`GATE2` → `'L3'`,且 `sector-list` agent(:91)opts 加 `phase: 'L3'`;`l4-prep`/`GATE3`/`dispatch-plan` → `'L4'`;末段 `assemble`/`GATE4` → `'Assemble'`。示例(其余同型):

```javascript
await bash(`mkdir -p ${SD} && ${R} autoresearch.scan.frame ${date} --json > ${SD}/market_pack.json`, 'frame', 'Prelude')
const g1 = await gate('GATE1', `${R} autoresearch.scan.gates gate1 ${date}`, GATE1, 'Prelude')
```

- [ ] **Step 3: 三大静默段前置 log(ETA 静态文案,只数动态)**

`phase('Prelude')` 之后、`frame` 之前:

```javascript
log('Prelude 开始:frame → [universe 全市场取数 ∥ market_view](取数历史 ~10m,完成即 GATE1)')
```

L3-rank agent(:104)之前:

```javascript
log(`L3 精排开始:通读 _l3_table(~200 只)比较式选 ~${g1.l4_budget}(effort max,历史 ~14m)`)
```

`l4-prep`(:116)之前:

```javascript
log('L4 派发包+slim 预取开始(reuse→旗源→prompts→slim,历史 ~10m)')
```

决策卡 `parallel`(:138)之前:

```javascript
log(`L4 并发:新派 ${plan.dispatch.length} 张(历史 ~7–15m)· 复用 ${(plan.reused || []).length} 张跳派发`)
```

- [ ] **Step 4: 逐张完成计数(闭包计数,零 token)**

行业 brief map(:99-101)加 `.then` 回执:

```javascript
  ...sectors.map((sec) => () => agent(
    `你是行业分析师。读 context/sector/${date}/${sec}.json 写 ${SD}/sector_briefs/${sec}.md,两段机器契约(## 地形段 喂 L3/L4 · ## 研判段 仅 L5,含 **行业方向** 行)。零新取数。`,
    { agentType: 'sector-brief', effort: 'high', label: `brief:${sec}`, phase: 'L3' })
    .then((r) => { log(`brief ✓ ${sec}`); return r })),
```

决策卡并发(:138-141)改为计数版(**语义不变**:仍 barrier + filter(Boolean)):

```javascript
let _done = 0
const fresh = (await parallel(plan.dispatch.map((code) => () => agent(
  `执行 ${SD}/_l4_prompt_${code}.md:先读整个任务包,再按其指令做渐进深度 DD + 早停,写决策卡到 ${SD}/details/${code}.md。最后返回该卡最终五档评级(code / rating / conviction)。`,
  { agentType: 'l4-card', effort: 'xhigh', label: `card:${code}`, phase: 'L4', schema: CARD })
  .then((r) => { _done += 1; log(`L4 卡 ${_done}/${plan.dispatch.length} ✓ ${code}${r ? ` → ${r.rating}` : '(无返回)'}`); return r }))))
  .filter(Boolean)
```

- [ ] **Step 5: 语法校验**

Run: `node --check .claude/workflows/scan-market.js`
Expected: 无输出(语法 OK)。再肉眼 diff 复核:**命令串/门 schema/返回结构一字未动**,只多了 log/phase。

- [ ] **Step 6: Commit**

```bash
git add .claude/workflows/scan-market.js
git commit -m "feat(scan): workflow 三静默段前置log+逐卡计数+phase归组(零token进度可视)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: SKILL 文档补 `/workflows` 提示一行

**Files:**
- Modify: `.claude/skills/scan-market/SKILL.md:41`(编排真身指针段末尾)

**Interfaces:**
- Consumes/Produces: 纯文档;守 `tests/test_skill_docs_refs.py`(反引号引用勿悬空)与 `tests/test_agent_defs.py::test_skill_docs_wire_agent_types`。

- [ ] **Step 1: 加一句提示**

SKILL.md `> **编排真身 = ...**` 那段末尾追加一句(同段内,不新起小节、不引用不存在的 `*.md`):

```
workflow 后台跑时随时用 `/workflows` 看实时进度树(逐卡 spinner + log 计数);各阶段墙钟收尾自动落 `_stage_timing.json`(mtime 推导)。
```

- [ ] **Step 2: 文档契约测试**

Run: `uv run --no-sync python -m pytest tests/test_skill_docs_refs.py tests/test_agent_defs.py -q`
Expected: 全绿。

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/scan-market/SKILL.md
git commit -m "docs(scan): SKILL 补 /workflows 实时进度树提示一行

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: report_rc 历史回填 — 探针 + `backfill` 子命令

**Files:**
- Modify: `autoresearch/research/consensus.py`(加 `backfill()` + CLI mode)
- Test: `tests/research/test_consensus_backfill.py`

**Interfaces:**
- Consumes: 既有 `pull(date, cache_root)`、`_dir(cache_root)`;`autoresearch.data.tushare_source._trade_days(pro, start, end)`。
- Produces: `backfill(start: str, end: str, cache_root=None, max_calls: int|None=None, sleep_s: float=0.0, pull_fn=pull, days_fn=None) -> dict`(`{"pulled": int, "skipped": int, "stopped_by": str|None}`);CLI `python -m autoresearch.research.consensus backfill <start> <end> [--max-calls N] [--sleep S]`。skip-existing → 幂等可续跑。主波计划(波 2)依赖回填后的 `status()["n_days"] ≥ 60` 开 IC 门。

- [ ] **Step 1: 探针先行(手跑,记录结果)**

Run: `uv run --no-sync python -m autoresearch.research.consensus pull 2026-05-15`
Expected 两种之一:`[consensus] 20260515 rows=<N>0>` = **历史可拉,继续 Step 2-6**;若抛权限/空数据异常 = 历史不可拉,**跳到 Step 7 只记档**(backfill 代码照做——对"漏拉补拉"仍有用,但不承诺提前开门)。
紧接着**限频实测**:立刻再跑 `uv run --no-sync python -m autoresearch.research.consensus pull 2026-05-16`,记录第二次是否报限频(决定 backfill 推荐用法:`--sleep 3700` 慢灌 vs 直接批量)。两次输出原文记入 `.superpowers/sdd/progress.md`。

- [ ] **Step 2: 写失败测试**

```python
# tests/research/test_consensus_backfill.py
"""backfill 契约:skip-existing 幂等 / max_calls 分片 / 异常停不丢缓存。全注入,零网络。"""
import pandas as pd

from autoresearch.research import consensus


def _days(start, end):
    return ["20260701", "20260702", "20260703"]


def test_skip_existing_and_cap(tmp_path):
    root = tmp_path / "report_rc"
    root.mkdir(parents=True)
    pd.to_pickle(pd.DataFrame({"ts_code": ["000001.SZ"]}), root / "20260701.pkl")
    calls = []
    res = consensus.backfill("2026-07-01", "2026-07-03", cache_root=tmp_path,
                             max_calls=1, pull_fn=lambda d, c=None: calls.append(d),
                             days_fn=_days)
    assert res == {"pulled": 1, "skipped": 1, "stopped_by": "max_calls"}
    assert calls == ["2026-07-02"]                 # 已缓存跳过,cap 停在第三天前


def test_error_stops_resumable(tmp_path):
    def boom(d, c=None):
        raise RuntimeError("每小时最多访问该接口1次")
    res = consensus.backfill("2026-07-01", "2026-07-03", cache_root=tmp_path,
                             pull_fn=boom, days_fn=_days)
    assert res["pulled"] == 0
    assert res["stopped_by"].startswith("error")   # 停下可续跑,不抛穿
```

- [ ] **Step 3: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/research/test_consensus_backfill.py -v`
Expected: FAIL,`AttributeError: module ... has no attribute 'backfill'`

- [ ] **Step 4: 实现 `backfill` + CLI**

`consensus.py` 中 `status()` 之后加:

```python
def backfill(start: str, end: str, cache_root: Path | None = None,
             max_calls: int | None = None, sleep_s: float = 0.0,
             pull_fn=None, days_fn=None) -> dict:
    """按交易日回补 report_rc 缓存(skip-existing → 幂等,可反复续跑)。

    2026-07-10 探针裁决"历史按日回补是否可行"(见 progress.md);限频应对:
    `--max-calls` 分片 + `--sleep` 节流;撞异常打印续跑提示后停,已落缓存不丢。
    """
    _pull = pull_fn or pull
    if days_fn is not None:
        days = days_fn(start, end)
    else:
        import autoresearch.research.factor_lab as fl
        from autoresearch.data.tushare_source import _trade_days
        days = _trade_days(fl._pro(), start.replace("-", ""), end.replace("-", ""))
    root = _dir(cache_root)
    pulled = skipped = 0
    stopped_by = None
    for d in days:
        d8 = str(d).replace("-", "")
        if (root / f"{d8}.pkl").exists():
            skipped += 1
            continue
        if max_calls is not None and pulled >= max_calls:
            stopped_by = "max_calls"
            break
        try:
            _pull(f"{d8[:4]}-{d8[4:6]}-{d8[6:]}", cache_root)
        except Exception as e:  # noqa: BLE001 — 限频/网络:停下可续跑
            stopped_by = f"error: {e}"
            print(f"[consensus] {d8} 拉取失败({e})→ 停;已缓存不丢,续跑同命令即可")
            break
        pulled += 1
        if sleep_s:
            import time
            time.sleep(sleep_s)
    print(f"[consensus] backfill: +{pulled} pulled, {skipped} skipped"
          + (f", stopped_by={stopped_by}" if stopped_by else ""))
    return {"pulled": pulled, "skipped": skipped, "stopped_by": stopped_by}
```

`main()` 改为三模式(保持既有两模式行为不变):

```python
def main(argv: list[str] | None = None) -> int:
    import argparse
    from datetime import date as _date
    ap = argparse.ArgumentParser(description="卖方一致预期前向积累(report_rc)")
    ap.add_argument("mode", choices=["pull", "status", "backfill"])
    ap.add_argument("date", nargs="?", help="pull 的日期 / backfill 的 start(YYYY-MM-DD)")
    ap.add_argument("end", nargs="?", help="backfill 的 end(YYYY-MM-DD)")
    ap.add_argument("--max-calls", type=int, default=None)
    ap.add_argument("--sleep", type=float, default=0.0)
    args = ap.parse_args(argv)
    if args.mode == "pull":
        pull(args.date or _date.today().isoformat())
    elif args.mode == "backfill":
        if not (args.date and args.end):
            ap.error("backfill 需要 start end 两个日期")
        backfill(args.date, args.end, max_calls=args.max_calls, sleep_s=args.sleep)
    else:
        print(status())
    return 0
```

同步改模块 docstring:若 Step 1 探针证明历史可拉,把「历史按日回补(~282 天)不可行」句改为「历史按日回补**可行**(2026-07-10 探针实证,见 backfill;限频用 --sleep/--max-calls 应对)」;探针失败则句尾加「(2026-07-10 复测仍不可行)」。用法段追加一行:
`  uv run --no-sync python -m autoresearch.research.consensus backfill 2026-04-01 2026-07-09 [--max-calls 10 --sleep 3700]`

- [ ] **Step 5: 跑测试 + 全量回归**

Run: `uv run --no-sync python -m pytest tests/research/ -q && uv run --no-sync python -m ruff check . && uv run --no-sync python -m pytest tests/ -q`
Expected: 全绿。

- [ ] **Step 6: 真回填开跑(探针可拉才做)**

Run: `uv run --no-sync python -m autoresearch.research.consensus backfill 2026-04-01 2026-07-09 --max-calls 10`
Expected: `+10 pulled ... stopped_by=max_calls`(或限频报错即停)。按 Step 1 实测的限频节奏决定后续:能连拉 → 一次跑完;限频 1/h → 建议用户后台 `--sleep 3700` 慢灌或分日续跑。跑后 `... consensus status` 记录 `n_days` 进 progress.md。**60 日凑齐后的 IC 验门属主波计划(波 2),本任务不做。**

- [ ] **Step 7: Commit**

```bash
git add autoresearch/research/consensus.py tests/research/test_consensus_backfill.py
git commit -m "feat(research): consensus backfill 子命令(历史回填探针+幂等续跑·report_rc 60日门提速)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: fund_portfolio 探针(只验证,不接线)

**Files:**
- Create: `context/factor_lab/cache/probes/fund_portfolio_20260710.json`(探针裁决记录,gitignored 目录;结论同步进 `.superpowers/sdd/progress.md`)

**Interfaces:**
- Consumes: tushare `pro.fund_portfolio`(经 `_ts_call` 限频包装)。
- Produces: 可行性裁决(rows/字段/报错原文)。主波计划(波 2)的「基金重仓 Δ 入 L4 行」任务读此裁决决定做/弃。

- [ ] **Step 1: 跑探针(三种参数形态)**

```bash
uv run --no-sync python - <<'PY'
import json
import autoresearch.research.factor_lab as fl
from autoresearch.data.tushare_source import _ts_call
pro = fl._pro()
out = {}
for kw in ({"ann_date": "20260422"}, {"ts_code": "005827.OF"}, {"symbol": "600519.SH"}):
    key = ",".join(f"{k}={v}" for k, v in kw.items())
    try:
        df = _ts_call(lambda kw=kw: pro.fund_portfolio(**kw))
        out[key] = {"rows": 0 if df is None else len(df),
                    "cols": [] if df is None or not len(df) else list(df.columns)[:12]}
    except Exception as e:
        out[key] = {"error": str(e)[:200]}
print(json.dumps(out, ensure_ascii=False, indent=1))
PY
```

Expected: 打印三形态各自 rows/cols 或 error 原文。

- [ ] **Step 2: 落裁决档**

把 Step 1 输出原样存 `context/factor_lab/cache/probes/fund_portfolio_20260710.json`(目录不存在先 `mkdir -p`),并在 `.superpowers/sdd/progress.md` 记一行裁决:`可用(哪种参数形态+季度锚)` 或 `不可用(error 摘要)→ 波2 弃该行`。**本任务无代码无 commit**(裁决档在 gitignored 区)。

---

## 执行顺序与依赖

Task 1 → Task 2(t0 锚被 Task 1 消费,但两者测试互不依赖,顺序执行即可)→ Task 3 → Task 4;Task 5、Task 6 与前四个完全独立,可穿插。全部完成后:**下一次真实扫描即是验收**(log 流全程可见 + 墙钟表有数;spec §5 验收④)。
