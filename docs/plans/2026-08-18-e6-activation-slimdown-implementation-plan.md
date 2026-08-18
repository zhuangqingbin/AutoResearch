# 出手线转正 × 学习层大减法 —— 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 E6 相对 BUY 可被人批转正（修 data_a 连坐 + task-book 收尾 + 切换包），并把账本判死的学习层机器物理删除（registry 家族/自动重标定/t1-LLM 腿/研判段等 13 件）。

**Architecture:** 六批次严格顺序：0 提交 B档基线 → 1 P0 代码（mode 仍 shadow）→ 2 **用户裁决门** + 切换包上线 → 3 删除 D1-D7+D13 → 4 D8-D12 → 5 活体验收。批内 task 顺序执行、逐 task 独立 commit。

**Tech Stack:** Python 3（uv run --no-sync）+ pytest；workflow 为 .claude/workflows/*.js（ESM，改动须过 AsyncFunction 解析探针，`node --check` 是假绿灯）。

**Spec:** `docs/specs/2026-08-18-e6-activation-learning-slimdown-design.md`（执行者必读；§6 裁决表是批次 2 的门）

## Global Constraints

- 测试命令统一 `uv run --no-sync python -m pytest tests/ -x -q`（全量）；单测 `uv run --no-sync python -m pytest <path>::<name> -v`。
- **数据文件一律归档不删**：`git mv` 不适用（全部 gitignored），用 `mkdir -p archive/20260818 && mv <file> archive/20260818/`。
- **动态 import 表纪律**：`nightly_close.py:94-124` 与 `prelude.py:459-482` 的账本表是字符串拼名 import，grep 静态 import 查不到——删任何 learning 模块必改这两张表 + `tests/learning/test_nightly_close.py`、`tests/scan/test_prelude.py` 的表断言。
- **双职测试只摘用例不整删**（各 task 已点名哪些文件双职）。
- workflow js 每次改动后跑 AsyncFunction 探针（T4.2 Step 1 给出脚本，改任何 js 都用它）。
- **同名异物勿误伤**：`factor_lab.promotion_sortcol/ic_promotion_table`、`feedback_store.promotion_candidates`、`style_spread.PROMOTION_GATE`、`t1_review.promote_candidates`——与 experiment_registry 家族无关（T3.1/T3.3 有防误伤 grep 步）。
- 红线不碰：L0-L2 漏斗、L3/L4/L5 主链、门与早停语义、L4 复用任何形式、菜单 carryover、观察单复活、L3.5、ensemble 复用式省钱。
- 批次 2 的 T2.5 是**硬门**：未拿到用户对 §6 裁决表的逐项裁定，不得翻 `mode=active`、不得执行任何 ⚖ 前缀删除（D1/D2/D3/D6/D13、A7、A8）。
- commit 信息风格照旧仓惯例（中文、`feat(scan):`/`fix(trace):`/`refactor(learning):` 前缀），尾行 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。

---

## 批次 0 · 基线

### Task 0.1: 提交 B档实施波

**Files:** 工作区现有 190 个已修改文件（08-13 B档：复盘自改 skill 能力下线，已实施未提交）

- [ ] **Step 1: 确认全量测试绿**

Run: `uv run --no-sync python -m pytest tests/ -q`
Expected: 全绿（记忆基线 3994 passed；允许数字略有出入但 0 failed）

- [ ] **Step 2: 检查暂存范围**

Run: `git status --short | grep -v "^??" | wc -l` → 应 ≈190；`git diff --stat | tail -1` → ≈ +1609/−1498。
确认无意外新增文件混入（`git status --short | grep "^??"` 里只应有本计划无关的临时物，若有则不 add）。

- [ ] **Step 3: 提交**

```bash
git add -u
git commit -m "feat(learning): B档实施——复盘自改 skill 能力下线 + prompt_patch 退役 + 毕业提名制(3994绿)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 批次 1 · P0 代码（E6 仍 shadow）

### Task 1.1: fail_class 单一事实源（E1a 之一）

**Files:**
- Create: `autoresearch/common/failclass.py`
- Test: `tests/common/test_failclass.py`

**Interfaces:**
- Produces: `fail_class(check: str) -> str`（返回 `"data" | "hygiene" | "metering"`）；`EXEMPT_PREFIXES: dict[str, str]`。放 `common/`（而非 spec 初稿说的 self_review.py）是为避免 scan↔learning 循环 import——单一事实源语义不变。

- [ ] **Step 1: 写失败测试**

```python
# tests/common/test_failclass.py
from autoresearch.common.failclass import fail_class


def test_hygiene_prefix():
    assert fail_class("产物形状·退役符号指令性引用") == "hygiene"
    assert fail_class("产物形状·旧尺裸写") == "hygiene"


def test_metering_prefix():
    assert fail_class("usage_reconcile·配置-实测不符") == "metering"


def test_unknown_defaults_to_data():
    # fail-safe:未登记的检查一律按 data 连坐;想豁免必须显式进 EXEMPT_PREFIXES
    assert fail_class("价格断言与OHLCV不符") == "data"
    assert fail_class("brief③生产BUY行与决策文件不符") == "data"
    assert fail_class("") == "data"
    assert fail_class(None) == "data"
```

- [ ] **Step 2: 跑测确认失败**（ModuleNotFoundError）
- [ ] **Step 3: 实现**

```python
# autoresearch/common/failclass.py
"""gate4 fail 行的门类判定 —— E1a(2026-08-18 设计稿 §3)。

决定一条 self_review fail 是否连坐当日相对 BUY(`relative_buy._data_contract_ok`
的日级 data_a 门)。白名单口径:只有明确与「当日数据可信度无关」的两类前缀豁免;
未登记的 check 一律按 "data" 处理 —— fail-safe:新检查默认连坐,想豁免必须显式登记。
立案:08-07/10/11 三天文档卫生 lint + 计量病经 gate4 团灭全天 BUY(spec §2.1)。
"""
from __future__ import annotations

#: 前缀 → 类别。key 必须与 `learning/self_review.py` 各 check 名的「组前缀」逐字一致。
EXEMPT_PREFIXES: dict[str, str] = {
    "产物形状·": "hygiene",           # 退役符号引用/skill内联字面量/旧尺裸写 等 repo 文档病
    "usage_reconcile·": "metering",   # token 计量对账 streak,与市场数据无关
}


def fail_class(check: object) -> str:
    text = str(check or "")
    for prefix, cls in EXEMPT_PREFIXES.items():
        if text.startswith(prefix):
            return cls
    return "data"
```

- [ ] **Step 4: 跑测通过** → **Step 5: Commit** `feat(common): E1a fail_class 单一事实源(hygiene/metering 白名单,默认 data 连坐)`

### Task 1.2: run_health 增 failed_data（E1a 之二）

**Files:**
- Modify: `autoresearch/scan/health.py:381-427`（`stage_results_health`）
- Test: `tests/scan/test_health.py`（已存在则追加用例；不存在则新建）

**Interfaces:**
- Consumes: `autoresearch.common.failclass.fail_class`
- Produces: `stage_results_health()` 返回 dict 新增键 `"failed_data": list[str]`（有 data 类失败的 stage 名；gate4 以 gate_fires.csv 的 fail 行分类，其余 FAILED stage 一律计入）。`empty` 分支同步加 `"failed_data": []`。

- [ ] **Step 1: 写失败测试**（fixture 用 tmp_path 造最小 scan 目录）

```python
# tests/scan/test_health.py 追加
import csv, json
from pathlib import Path
from autoresearch.scan.health import stage_results_health


def _mk_stage(scan: Path, stage: str, status: str, contract_hash: str = "h1"):
    d = scan / "stage_results"; d.mkdir(parents=True, exist_ok=True)
    # 按 load_stage_result 的 schema 写最小合法文件 —— 先读
    # autoresearch/scan/stage_result.py 的字段名(stage/status/contract_hash/…),
    # 与 tests/scan/ 下既有 stage_result fixture 保持同构(有现成 helper 就复用)。
    (d / f"{stage}.json").write_text(json.dumps({
        "schema_version": 1, "stage": stage, "status": status,
        "contract_hash": contract_hash, "started_at": "2026-08-18T00:00:00",
        "finished_at": "2026-08-18T00:00:01", "error": None, "metrics": {},
    }), encoding="utf-8")


def _mk_fires(scan: Path, rows):
    with (scan / "gate_fires.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["date", "code", "check", "severity", "detail"])
        w.writeheader()
        for r in rows:
            w.writerow(r)


def test_gate4_hygiene_only_not_in_failed_data(tmp_path):
    _mk_stage(tmp_path, "gate4", "FAILED")
    _mk_fires(tmp_path, [{"date": "2026-08-18", "code": "", "severity": "fail",
                          "check": "产物形状·退役符号指令性引用", "detail": "x"}])
    got = stage_results_health(tmp_path)
    assert "gate4" in got["failed"]          # 原义不动:发布卫生照旧 FAILED
    assert "gate4" not in got["failed_data"]  # 但不连坐 data_a


def test_gate4_with_data_fail_in_failed_data(tmp_path):
    _mk_stage(tmp_path, "gate4", "FAILED")
    _mk_fires(tmp_path, [
        {"date": "2026-08-18", "code": "", "severity": "fail",
         "check": "产物形状·旧尺裸写", "detail": "x"},
        {"date": "2026-08-18", "code": "600188", "severity": "fail",
         "check": "价格断言与OHLCV不符", "detail": "y"},
    ])
    assert "gate4" in stage_results_health(tmp_path)["failed_data"]


def test_gate4_failed_but_fires_missing_is_data(tmp_path):
    # fail-safe:gate4 FAILED 却查不到 fire 行(文件缺失/无 fail 行) → 按 data 连坐
    _mk_stage(tmp_path, "gate4", "FAILED")
    assert "gate4" in stage_results_health(tmp_path)["failed_data"]


def test_non_gate4_failed_always_data(tmp_path):
    _mk_stage(tmp_path, "assemble", "FAILED")
    got = stage_results_health(tmp_path)
    assert got["failed"] == ["assemble"] and got["failed_data"] == ["assemble"]
```

注意：Step 1 动工前先读 `autoresearch/scan/stage_result.py` 的 `load_stage_result` 真实字段（上面 `_mk_stage` 的字段名按它修正；tests/scan 下已有同类 fixture 就直接复用其 helper——**探针必须真的能构造出合法 StageResult**，否则四个用例全在测 fixture 而不是 health）。

- [ ] **Step 2: 跑测确认失败**（KeyError: 'failed_data'）
- [ ] **Step 3: 实现** —— `health.py` 内：

```python
def _gate4_has_data_fail(scan: Path) -> bool | None:
    """gate4 FAILED 时查 gate_fires.csv 是否有 data 类 fail 行;查不到 → None(按 data 处理)。"""
    import csv as _csv
    from autoresearch.common.failclass import fail_class
    path = Path(scan) / "gate_fires.csv"
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8", newline="") as fh:
            rows = list(_csv.DictReader(fh))
    except (OSError, _csv.Error):
        return None
    fails = [r for r in rows if str(r.get("severity") or "") == "fail"]
    if not fails:
        return None
    return any(fail_class(r.get("check")) == "data" for r in fails)
```

`stage_results_health` 的 return 前插入（并在 `empty` dict 加 `"failed_data": []`）：

```python
    failed = sorted(r.stage for r in results if r.status == "FAILED")
    failed_data = []
    for stage in failed:
        if stage == "gate4" and _gate4_has_data_fail(scan) is False:
            continue
        failed_data.append(stage)
```

（return dict 的 `"failed"` 改用上面的 `failed` 变量，新增 `"failed_data": failed_data`。）

- [ ] **Step 4: 跑测通过 + 全量绿** → **Step 5: Commit** `feat(scan): E1a run_health 增 failed_data(gate4 卫生/计量失败不进 data 类)`

### Task 1.3: `_data_contract_ok` 改读 failed_data（E1a 之三）

**Files:**
- Modify: `autoresearch/scan/relative_buy.py:123`（RULE_VERSION）、`:301-305`
- Test: `tests/scan/test_relative_buy.py`（追加）

**Interfaces:**
- Produces: `_data_contract_ok` 语义 = `stage_results.failed_data`（键缺失时回退 `failed`，历史 run_health 兼容）；`RULE_VERSION = "e6.v1.2"`。

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_relative_buy.py 追加
import json
from autoresearch.scan.relative_buy import _data_contract_ok


def _health(tmp_path, stages):
    (tmp_path / "run_health.json").write_text(json.dumps({
        "core_missing": [],
        "run_contract": {"status": "OK"},
        "stage_results": stages,
        "decision_records": {"status": "OK"},
    }), encoding="utf-8")


def test_hygiene_only_gate4_passes_data_a(tmp_path):
    _health(tmp_path, {"status": "OK", "failed": ["gate4"], "failed_data": []})
    ok, why = _data_contract_ok(tmp_path)
    assert ok, why


def test_data_fail_blocks(tmp_path):
    _health(tmp_path, {"status": "OK", "failed": ["gate4"], "failed_data": ["gate4"]})
    assert not _data_contract_ok(tmp_path)[0]


def test_legacy_health_without_failed_data_keeps_old_semantics(tmp_path):
    _health(tmp_path, {"status": "OK", "failed": ["gate4"]})
    assert not _data_contract_ok(tmp_path)[0]  # 回退旧口径:failed 非空即拒
```

- [ ] **Step 2: 跑测确认失败**（第一个用例 assert ok 失败）
- [ ] **Step 3: 实现** —— `relative_buy.py:304-305` 两行替换为：

```python
    failed_data = stages.get("failed_data")
    if failed_data is None:            # 历史 run_health 无此键 → 保持 v1.1 旧口径
        failed_data = stages.get("failed")
    if failed_data:
        return False, f"stage_results.failed_data={sorted(failed_data)}"
```

`RULE_VERSION = "e6.v1.2"`，并在 `:124-130` 注释块下追加一段：

```python
# v1.2 = v1.1 + data_a 第 4 判改读 `stage_results.failed_data`(E1a,2026-08-18 设计稿 §3):
# 文档卫生(产物形状·*)与计量病(usage_reconcile·*)类 gate4 失败不再连坐当日 BUY;
# 数据类失败照旧团灭。历史 run_health 无 failed_data 键 → 回退旧口径,历史判定不改写。
# 打分/选择语义零改动。
```

- [ ] **Step 4: 跑测通过 + 全量绿** → **Step 5: Commit** `feat(scan): E1a data_a 改读 failed_data,RULE_VERSION e6.v1.2`

### Task 1.4: `l4_tasks reconcile` 收尾自愈（E1b 之一）

**Files:**
- Modify: `autoresearch/scan/l4_tasks.py`（`mark_success` 之后新增函数；`main():666` choices 加 `"reconcile"`）
- Test: `tests/scan/test_l4_tasks.py`（追加；先读该文件找现成的 book fixture helper 复用）

**Interfaces:**
- Produces: `reconcile(book: Path|str, *, now=None) -> dict`，返回 `{"ok": True, "recovered": [code…], "skipped": [{"code","missing"}…]}`；补记的 task 行带 `"recovered": True` + status SUCCEEDED + 现算 content_hash。CLI：`python -m autoresearch.scan.l4_tasks reconcile <DATE> [--root …]`（DATE 语义同 init/batches/stats）。

- [ ] **Step 1: 写失败测试**（复用 test_l4_tasks.py 既有「建 book + 造三产物文件」的 fixture 写法；以下三用例）

```python
def test_reconcile_recovers_running_with_artifacts_on_disk(book_with_running_task):
    # fixture:一票 status=RUNNING,prompt/slim/card 三产物齐且 slim ≥4096B
    from autoresearch.scan import l4_tasks
    got = l4_tasks.reconcile(book_with_running_task)
    assert got["ok"] and got["recovered"] == ["600188"]
    _, payload = l4_tasks._read(book_with_running_task)
    task = payload["tasks"]["600188"]
    assert task["status"] == "SUCCEEDED" and task["recovered"] is True
    assert payload["tasks"]["600188"]["artifacts"]["card"]["content_hash"]


def test_reconcile_skips_when_card_missing(book_with_running_task_no_card):
    from autoresearch.scan import l4_tasks
    got = l4_tasks.reconcile(book_with_running_task_no_card)
    assert got["recovered"] == [] and got["skipped"][0]["missing"] == ["card"]
    _, payload = l4_tasks._read(book_with_running_task_no_card)
    assert payload["tasks"]["600188"]["status"] == "RUNNING"


def test_reconcile_idempotent_on_succeeded(book_all_succeeded):
    from autoresearch.scan import l4_tasks
    assert l4_tasks.reconcile(book_all_succeeded)["recovered"] == []
```

- [ ] **Step 2: 跑测确认失败**（AttributeError: reconcile）
- [ ] **Step 3: 实现**（放 `mark_success` 之后）

```python
def reconcile(book: Path | str, *, now: datetime | None = None) -> dict:
    """收尾自愈(E1b):卡已在盘而 book 仍非 SUCCEEDED 的票,按盘上事实补记。

    只认盘上产物:prompt/slim/card 三件齐 + slim 合格才补记(content_hash 现算,
    绝不编造);缺产物的票原样保留 —— contract 门拦它拦得对。补记行打
    `recovered=True`,账目可辨。幂等:SUCCEEDED 行直接跳过。
    立案:2026-08-12 九票卡全在盘、book 全 RUNNING → E6 contract 团灭(spec §2.1)。
    """
    from autoresearch.scan.l4.producers import _slim_defect

    path = Path(book)
    recovered: list[str] = []
    skipped: list[dict] = []
    with _locked(path):
        _, payload = _read(path)
        for code6 in sorted(payload["tasks"]):
            task = payload["tasks"][code6]
            if task.get("status") == "SUCCEEDED":
                continue
            refs = task.get("artifacts") or {}
            missing = []
            for name in ("prompt", "slim", "card"):
                p = Path(str((refs.get(name) or {}).get("path") or ""))
                if not p.is_file() or p.stat().st_size == 0:
                    missing.append(name)
            if not missing:
                _, defect = _slim_defect(Path(refs["slim"]["path"]), 4096)
                if defect:
                    missing = [f"slim:{defect}"]
            if missing:
                skipped.append({"code": code6, "missing": missing})
                continue
            for name in ("prompt", "slim", "card"):
                p = Path(refs[name]["path"])
                refs[name] = _artifact(p, content_hash=_sha256(p))
            task["status"] = "SUCCEEDED"
            task["recovered"] = True
            task["last_error_class"] = None
            task["last_error"] = None
            task["updated_at"] = _stamp(now)
            recovered.append(code6)
        if recovered:
            _atomic_write(path, payload)
    return {"ok": True, "recovered": recovered, "skipped": skipped}
```

CLI：`main()` 的 choices 列表加 `"reconcile"`；分发处（`args.cmd == "batches"` 分支旁）加：

```python
    elif args.cmd == "reconcile":
        result = reconcile(_book_path(args.first, args.root))
```

（`first` 即 DATE，与 batches/stats 同型；help 文案 `:668` 的 `init/batches/stats:DATE` 改为 `init/batches/stats/reconcile:DATE`。）

- [ ] **Step 4: 跑测通过 + 全量绿** → **Step 5: Commit** `feat(scan): E1b l4_tasks reconcile 收尾自愈(盘上事实补记,recovered 标记)`

### Task 1.5: post_run 挂 reconcile（E1b 之二）

**Files:**
- Modify: `autoresearch/scan/post_run.py`（`safe_write_passport(scan)` 调用之前，约 `:621`）
- Test: `tests/scan/test_post_run.py` 或 test_relative_buy 集成用例（追加）

**Interfaces:**
- Consumes: `l4_tasks.reconcile`。挂在 `publish_run_observation` 内、护照/决策现算**之前**——比 spec 初稿写的 publisher 挂点更下游一站，assemble 与 CP7 事后重跑两条入口都被覆盖（护照/决策"现算"读的就是自愈后的 book）。

- [ ] **Step 1: 写失败测试**：fixture 造「book RUNNING + 三产物在盘」的最小 scan 目录，调 `publish_run_observation`（或其可测切片）后断言 `_relative_buy_decision.json` 里该票 `hard_gate.contract == true`。若 publish_run_observation 依赖过多（它还要 decision_records 等），退而断言：调用后 book 中该票 status==SUCCEEDED（证明挂点生效），contract 门通过性由 Task 1.4 用例覆盖。
- [ ] **Step 2: 确认失败** → **Step 3: 实现**：在 `from autoresearch.scan.passport import safe_write_passport` 之前插入：

```python
    # E1b(2026-08-18 设计稿):护照/相对决策**现算**之前先对 task-book 做收尾自愈 ——
    # 卡在盘而 book 停 RUNNING 的票按盘上事实补记(recovered 标记),防 08-12 型
    # contract 团灭。失败只打一行,不阻断发布(与护照/决策同一失败纪律)。
    try:
        from autoresearch.scan.l4_tasks import _book_path, reconcile
        _rec = reconcile(_book_path(scan.name, None))
        if _rec.get("recovered"):
            print(f"[l4_tasks] reconcile 补记 {len(_rec['recovered'])} 票: "
                  f"{','.join(_rec['recovered'])}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"[l4_tasks] reconcile 失败: {type(exc).__name__}: {exc}", file=sys.stderr)
```

（`_book_path(scan.name, None)` 的签名先读 `l4_tasks.py` 确认——若它要 `(date, root)` 且 root=None 默认工作区，就照上；不符则按真实签名改。book 不存在时 `_read` 会抛 → 被 except 吞成一行，符合「影子件失败不阻断」纪律。）

- [ ] **Step 4: 跑测通过 + 全量绿** → **Step 5: Commit** `feat(scan): E1b post_run 决策现算前先 reconcile task-book`

### Task 1.6: brief_lint 增「相对BUY同源」data 类检查（E4）

**Files:**
- Modify: `autoresearch/learning/self_review.py`（`brief_lint` :1182-1302 内新增第⑥件事；`BRIEF_LINT_SEVERITY` :1161-1172 登记）
- Test: `tests/learning/test_self_review.py`（追加）

**Interfaces:**
- Produces: 新 check 名 `"brief③相对BUY与决策文件不同源"`（fail=data 类——名字**不得**以 `产物形状·`/`usage_reconcile·` 开头，否则被 Task 1.1 豁免出 data 类）。判据：`_relative_buy_decision.json` 存在时，其 `buys[].code` 集合必须与 brief ③ 段渲染出的相对 BUY 代码集合一致（含空对空）；decision 缺失 → warn 不 fail（缺席已由 data_a 的 inputs 面负责）。

- [ ] **Step 1: 写失败测试**：fixture 造 brief.md（③ 段含「相对 BUY …600188」行）+ decision 文件 buys=[000001] → 断言 brief_lint 结果含该 check 且 severity=fail；buys 一致 → 无该 fail。实现前先读 `brief.py:635-690` `_buy_lines` 确认 ③ 段相对 BUY 行的稳定锚（🕶/✅ 标 + 六位代码正则 `\b\d{6}\b`），测试用同一锚。
- [ ] **Step 2: 确认失败** → **Step 3: 实现**：在 brief_lint 第⑤件事（:1281-1301）之后按同型结构追加；从 brief 文本抽 ③ 段代码集合用与 `_BRIEF_DECISION_FIELDS` 同样的「渲染片段比对」思路——直接复用 `_brief.build` 重算的 ③ 段（:1241-1251 已有重算基建）与 decision 文件对比。
- [ ] **Step 4: 跑测通过 + 全量绿** → **Step 5: Commit** `feat(learning): E4 brief_lint 增相对BUY同源检查(data类)`

### Task 1.7: 08-11 hash 失配并案调查（E1b 之三，产出研究笔记）

**Files:**
- Create: `docs/research/2026-08-19-taskbook-hash-mismatch-0811.md`
- Modify（仅当裁定为噪音时）: `autoresearch/scan/structural_audit.py:56-63`（豁免类别）+ 其测试

- [ ] **Step 1: 取证**

```bash
uv run --no-sync python - <<'EOF'
import json
book = json.load(open('context_claude/scan/2026-08-11/_l4_tasks.json'))
for code, t in sorted(book['tasks'].items()):
    print(code, t.get('status'), t.get('updated_at'), (t.get('last_error') or '')[:60])
audit = book.get('structural_audit') or book.get('audit') or []
print(json.dumps(audit if isinstance(audit, list) else list(audit)[:5], ensure_ascii=False)[:2000])
EOF
uv run --no-sync python -m autoresearch.scan.structural_audit | head -40
```

- [ ] **Step 2: 裁定并落笔记**：27 条 ARTIFACT_HASH_MISMATCH 属于 (a) 同日重跑把产物重写（内容变、hash 变——REBUILDABLE 噪音）还是 (b) mark_success 与产物写入竞态（真病）。笔记必须含逐票时间线证据。
- [ ] **Step 3（仅 a 分支）**：`structural_audit.py:56-63` 现有 prompt 豁免旁增加「同日 slim/card 重建且新 hash 与盘上现文件一致 → REBUILDABLE 不计失败」，配一条测试；(b) 分支则在笔记里立修复项并**升级为本批新 task**（停下来把方案报给用户）。
- [ ] **Step 4: Commit** `docs(research): 08-11 taskbook hash 失配 27 连归因(+豁免/竞态修复)`

### Task 1.8: 批次 1 活体验收

- [ ] **Step 1**: 全量测试绿。
- [ ] **Step 2**: 对最近一个真实扫描日重跑事后件（只读性重算）：`uv run --no-sync python -m autoresearch.scan.post_run <date> observe --report-dir <对应 run 目录>`，确认 `_relative_buy_decision.json` 重写成功、`rule_version=="e6.v1.2"`、无非数据性 BLOCKED（若当日本有真数据病则如实保留）。
- [ ] **Step 3**: 下一个自然扫描日观察 E6 影子行为正常（不阻塞本计划推进批次 2 的裁决准备，但 mode 翻 active 前至少要有 1 个鲜活日经过 E1a/E1b 代码）。

---

## 批次 2 · 裁决门 + 切换包

### Task 2.1: relative_buy 配置块（三件套）

**Files:**
- Modify: `autoresearch/scan/user_config.py:90-139`（`_TOP_WHITELIST`/`_SUB_WHITELIST`/`_KNOB_TYPES`）
- Modify: `.claude/skills/scan-market/scan_config.jsonc`（新块，**先写 shadow**）
- Test: `tests/scan/test_user_config.py`（追加）

**Interfaces:**
- Produces: `load_user_config()` 接受顶层键 `relative_buy`，子键 `{mode, exclude_pinned}`；类型：mode ∈ {"shadow","active"}（新增 `_t_rbmode`），exclude_pinned bool。消费点＝Task 2.2 的 post_run 读取。

- [ ] **Step 1: 写失败测试**

```python
def test_relative_buy_block_accepted(tmp_cfg):
    cfg = load_from(tmp_cfg, {"relative_buy": {"mode": "shadow", "exclude_pinned": True}})
    assert cfg["relative_buy"]["mode"] == "shadow"


def test_relative_buy_bad_mode_raises(tmp_cfg):
    with pytest.raises(ValueError):
        load_from(tmp_cfg, {"relative_buy": {"mode": "live"}})


def test_relative_buy_unknown_subkey_raises(tmp_cfg):
    with pytest.raises(ValueError):
        load_from(tmp_cfg, {"relative_buy": {"mode": "shadow", "ttl": 3}})
```

（`load_from`/`tmp_cfg` 用该测试文件既有的写临时 config 的 helper；没有就写一个 3 行的。）

- [ ] **Step 2: 确认失败** → **Step 3: 实现**：`_TOP_WHITELIST` 加 `"relative_buy"`；`_SUB_WHITELIST` 加 `"relative_buy": {"mode", "exclude_pinned"}`；`def _t_rbmode(v): return v in {"shadow", "active"}`；`_KNOB_TYPES` 加 `("relative_buy","mode"): (_t_rbmode, "shadow|active")`、`("relative_buy","exclude_pinned"): (_t_bool, "boolean")`。scan_config.jsonc 按其分节惯例插入（带【生效点】注释，SKILL.md「配置单一事实源」节同步一行）：

```jsonc
  // ── 相对 BUY(E6)──【生效点:scan/post_run.publish_run_observation → relative_buy.write_decision】
  // mode: shadow=影子只记账;active=正式接管 BUY(裁决表 A1 批 GO 后才可翻,翻回即回滚)
  // exclude_pinned: true=📌 持仓不参与当日 BUY 选择(保送不算判例;裁决表 A2)
  "relative_buy": { "mode": "shadow", "exclude_pinned": true },
```

- [ ] **Step 4: 跑测通过 + 全量绿** → **Step 5: Commit** `feat(scan): relative_buy 配置块三件套(mode/exclude_pinned,默认 shadow)`

### Task 2.2: build_decision 支持 active + exclude_pinned（RULE_VERSION e6.v2.0）

**Files:**
- Modify: `autoresearch/scan/relative_buy.py`（`:123` 版本、`:509-511` mode 硬拒、`:562-589` BUY 选择、`:596-660` 输出 dict）
- Modify: `autoresearch/scan/post_run.py`（`safe_write_decision(scan)` 处传 config 解析出的 mode/exclude_pinned）
- Test: `tests/scan/test_relative_buy.py`（追加）

**Interfaces:**
- Produces: `build_decision(scan_dir, date=None, mode=MODE_SHADOW, exclude_pinned=False)`；`MODE_ACTIVE = "active"`；输出 dict 新增顶层 `"exclude_pinned": bool`；exclude_pinned=True 时 `buys` 取 `eligible` 中第一个 `pinned=False` 的行，且每个被跳过的 pinned 合格行往 `excluded` 追加 `{"code", "reason": "pinned_holding", "detail": "📌 持仓不参与相对 BUY(保送不算判例)"}`（`rank` 字段照旧按全体 eligible 排，观测语义不变）；`blocked` 按「非📌合格是否为空」判。`write_decision`/`safe_write_decision` 透传两参。

- [ ] **Step 1: 写失败测试**（用该文件既有的「造最小 scan 目录 → build_decision」fixture；核心用例）

```python
def test_active_mode_accepted(minimal_scan):
    doc = build_decision(minimal_scan, mode="active")
    assert doc["mode"] == "active" and doc["rule_version"] == "e6.v2.0"


def test_exclude_pinned_picks_first_nonpinned(scan_with_pinned_rank1):
    # fixture:两只合格,rank1 是 pinned,rank2 非 pinned
    doc = build_decision(scan_with_pinned_rank1, exclude_pinned=True)
    assert doc["buys"][0]["code"] == RANK2_CODE
    assert any(r["reason"] == "pinned_holding" for r in doc["excluded"])
    assert doc["exclude_pinned"] is True


def test_exclude_pinned_all_pinned_blocks(scan_all_eligible_pinned):
    doc = build_decision(scan_all_eligible_pinned, exclude_pinned=True)
    assert doc["blocked"] and doc["buys"] == []
```

- [ ] **Step 2: 确认失败** → **Step 3: 实现**：
  - `MODE_ACTIVE = "active"`；`:509-511` 改 `if mode not in {MODE_SHADOW, MODE_ACTIVE}: raise ValueError(...)`。
  - BUY 选择（`:572-574`）改：

```python
    buy_pool = ([row for row in eligible if not row["pinned"]]
                if exclude_pinned else eligible)
    for row in eligible:
        if exclude_pinned and row["pinned"]:
            excluded.append({"code": row["code"], "reason": "pinned_holding",
                             "detail": "📌 持仓不参与相对 BUY(保送不算判例)"})
    buys = ([{"code": buy_pool[0]["code"], "basis": "relative", "rank": 1}]
            if buy_pool else [])
```

  `blocked = not buys` 不变（excluded 里 pinned_holding 行会自然进 blocked_reasons 分桶）。输出 dict 顶层加 `"exclude_pinned": exclude_pinned`。
  - `RULE_VERSION = "e6.v2.0"` + 注释段（列 v2.0 = mode 参数开放 + exclude_pinned + 输出新键；打分/四面算法逐字不动）。
  - post_run 调用处改：

```python
    from autoresearch.scan.relative_buy import safe_write_decision
    from autoresearch.scan.user_config import load_user_config
    _rb = (load_user_config().get("relative_buy") or {})
    safe_write_decision(scan, mode=str(_rb.get("mode") or "shadow"),
                        exclude_pinned=bool(_rb.get("exclude_pinned", False)))
```

  （`safe_write_decision` 签名同步扩参透传。）
  - **检查 `learning/relative_ledger.py:128-149` `_contract_errors`**：它锁 `mode=shadow`——改为接受 `{"shadow","active"}`（保留其余不变量：每日≤1、basis=relative、ruler、BUY 必在候选表），加一条测试。这是 U6「记分册=本账本」在 active 期继续记账的前提。
- [ ] **Step 4: 跑测通过 + 全量绿** → **Step 5: Commit** `feat(scan): E2 相对BUY支持 active+exclude_pinned,RULE_VERSION e6.v2.0`

### Task 2.3: preflight 体检 CLI

**Files:**
- Modify: `autoresearch/scan/relative_buy.py`（文件尾 CLI；现有 `main`/argparse 结构先读 `:684` 之后再挂）
- Test: `tests/scan/test_relative_buy.py`（追加一条冒烟）

- [ ] **Step 1-4（TDD 同上）**：新 verb `preflight`：打印 JSON = `{"summary": relative_ledger.summarize(), "last_3": <relative_buy.jsonl 尾 3 行的 date/status/code>, "contract_errors_recent": <尾 14 行 contract_error 计数>}`。测试：对 fixture 账本断言键在场。
- [ ] **Step 5: Commit** `feat(scan): E2 preflight 体检 verb(转正前人读)`

### Task 2.4: 切换包消费者（active 时生效，shadow 期零行为变化）

**Files:**
- Modify: `autoresearch/scan/decision_finalize.py:24-30,264`；`autoresearch/scan/report_sections.py:332,355,732`；`autoresearch/scan/health.py:245-247`；`autoresearch/scan/brief.py:639-690`；`autoresearch/learning/zero_buy_ledger.py`（roll+render）；`autoresearch/learning/buy_ledger.py`（render 加 legacy 横幅）
- Test: 各自测试文件追加（`tests/scan/test_decision_finalize.py`、`test_report_sections.py`、`test_brief.py`、`tests/learning/test_zero_buy_ledger.py`）

**Interfaces:**
- Produces: 统一读法 helper——`relative_buy.load_decision(scan_dir) -> dict | None`（读盘上 `_relative_buy_decision.json`；新增小函数，供消费者用，**不**现算）。各消费点行为：`decision.mode=="active"` 时 BUY 口径=decision `buys[]`（brief ✅ 行、组合视角 n、count_buys、self_review flow.buys_n、decision_finalize 的 proposal 对 buys 内票标 "BUY"、qualified 判定改按 decision）；`mode!="active"` 或文件缺失 → 现行为逐字不变。zero_buy_ledger：读各日 decision 文件，遇到第一个 `mode=="active"` 的日期起停止产新行，render 顶部加一行 `> ⚠️ legacy:E6 转正(<date>)后本账冻结,后续零买语义见 relative_buy.md`。buy_ledger render 同款横幅。
- 每个消费点一条「active fixture」测试 + 一条「shadow 不变」回归测试。**先写 shadow 回归测试并确认现状绿**，再动代码——这是防止影子期行为漂移的锚。

- [ ] **Step 1: shadow 回归锚测试先行** → **Step 2: 逐消费点 TDD**（顺序：load_decision helper → brief → report_sections → health → decision_finalize → zero_buy/buy_ledger）→ **Step 3: 全量绿**
- [ ] **Step 4: Commit（可拆 2-3 个）** `feat(scan): E3 切换包——active 时 BUY 口径统一读决策文件(shadow 零变化)`

### Task 2.5: 裁决会话（硬门 —— STOP）

**此 task 不写代码。执行者到此必须停下，把 spec §6 的 A/B/C/D 四组裁决表逐项呈给用户（用 AskUserQuestion 或对话），记录逐项裁定：**

- [ ] **Step 1**: 呈表并收齐裁定（A1-A9、B1-B3、C1-C3、D 组 20 条）。
- [ ] **Step 2**: 裁定写入 `context_claude/knowledge/changelog.jsonl`（走 feedback skill 的裁决通道；每项一行：id、verdict、date、依据指针=spec §6）。
- [ ] **Step 3**: 按裁定执行本批配置项：
  - A1 GO → `scan_config.jsonc` `relative_buy.mode` → `"active"`（独立 commit：`feat(scan): E6 转正——mode=active(用户裁定 <date>)`）；翻之前跑 `preflight` verb 并把输出贴给用户过目。
  - B1 → `scan_config.jsonc:91` `channel_quotas` 改 `{"value": 312, "heat": 112, "main_fund": 150, "momentum": 188, "healthy": 112, "growth": 112}`（momentum/healthy/growth 三键从代码默认显式上浮到 config——**先 grep `channel_quotas` 消费点确认「config 缺键=用代码默认」的合并语义**，把最终六键回读进 SKILL.md 配置表；accumulation 幽灵行跳过）。
  - B3 → `scan_config.jsonc:88` `recall_channels` 删 `"reversal_confirm"`；reopen 条件（vol_ratio_20 接入 L1 帧后重开 A/B）写进 STAGES.md「开放线头」节。
  - C1/C2 → 用 lessons 的既有 retire 通道（`feedback_store` 的 retire_lesson；grep 其 CLI/函数签名后逐条执行 + changelog 记账）；C3 关账。
  - D 组 → 逐条把 verdict 写回 `proposals.jsonl`（status: applied/rejected/resolved 按 feedback_store 的既有状态词表；grep `_MOOT_TERMS`/status 枚举确认拼写）。「实施」类中属于本计划后续 task 的（D13 三条、conviction 标度、rz 置 0）在对应 task 落地，此处只记 accepted。
- [ ] **Step 4**: conviction 标度校验（pr_20260717_005，用户批后）：`l4-stock.js` 回传 conviction 的 schema 处加 `"minimum": 0, "maximum": 100`（先 grep `conviction` 定位 TASKS/schema 声明；js 改动过 AsyncFunction 探针）。rz 置 0（pr_20260727_002，用户批后）：`context_claude/factor_lab/weights.json` 中 rz 权重改 0，changelog 记账一行（weights 是 gitignored 数据——记账行就是审计痕）。
- [ ] **Step 5: Commit** 每个配置改动独立 commit。

### Task 2.6: R-F1 replay delta 报告 + 吸筹 floor 12→0（B2 批后）

**Files:**
- Create: `docs/research/2026-08-19-accumulation-floor-removal-delta.md`
- Modify: `autoresearch/scan/recall/l2_stratify.py:36-37`（`DEFAULT_FLOORS` 吸筹 12→0）
- Test: `tests/scan/` 下锁 DEFAULT_FLOORS 的测试同步（grep `"吸筹"` tests/）

- [ ] **Step 1**: 跑 replay VariantSpec 对照出菜单 delta（工具现成：`autoresearch/research/replay.py`；grep `VariantSpec` 用法与 `capfloor20` 影子先例，对最近一个完整扫描日出「floor12 vs floor0」的 L2 菜单差异表：进出名单 + merit_need 变化 107→119 预期核对）。
- [ ] **Step 2**: 报告呈用户（Task 2.5 已批 B2 的前提下确认名单 delta 无意外）→ 改 `DEFAULT_FLOORS` → 测试同步 → 全量绿。
- [ ] **Step 3: Commit** `feat(scan): R-F1 吸筹死配额 floor 12→0(replay delta 报告随附,用户批)`

---

## 批次 3 · 删除（⚖ 项以 Task 2.5 裁定为准；每 task 一个 commit）

> 每个删除 task 的固定收尾三步（下面不再重复写）：
> (a) `grep -rn "<被删符号>" autoresearch/ .claude/ tests/ docs/ --include="*.py" --include="*.js" --include="*.md"` 逐个确认零残留（docs 的历史 specs **不改写**，命中历史 spec 属正常——只清 STAGES/SKILL 等活文档）；
> (b) `uv run --no-sync python -m pytest tests/ -q` 全量绿；
> (c) 独立 commit（refactor(learning)/refactor(scan) 前缀 + 「用户裁定 2026-08-XX」字样）。

### Task 3.1: D1 experiment_registry 家族整删（裁决 A3）

**Files:**
- Delete: `autoresearch/learning/experiment_registry.py`、`promotion.py`、`rollback_watch.py`、`experiment_template.py`、`mainflow5d.py`
- Delete(tests 单职): `tests/learning/test_experiment_registry.py`、`test_experiment_promotion.py`、`test_rollback_watch.py`、`test_experiment_activation.py`、`test_experiment_cli_report.py`、`test_experiment_template.py`、`test_mainflow5d.py`、`tests/research/test_consensus_prereg.py`
- Modify: `autoresearch/learning/nightly_close.py`（删 `_exp_observe` 函数与其在 run() 步骤表的项：七步 `["retro_refresh","t1_backfill","t1_gap_finalize","tripwire","ledgers","exp_observe","news_flash"]` → 六步）；`autoresearch/learning/evidence_manifest.py:646` 起 `_add_registry` 段与 registry_inventory 字段；`autoresearch/scan/gate0.py:112-126` `assert_may_block` + CLI `--mode BLOCKING` 分支（BLOCKING 模式一并退役，prelude 只用 ADVISORY）；`autoresearch/learning/gate_recal.py:398-428` `register()` + `--register` flag；`autoresearch/research/consensus.py:255`、`candidates.py:148` 摘 registry 调用
- Modify(tests 双职): `tests/learning/test_nightly_close.py`（步骤表断言六步）、`tests/scan/test_l2_regime_wiring_probe.py:43-62`（治理证据源换：断言 `scan_config.jsonc` 的 `funnel.regime_aware` 键在场且 `git log --oneline -1 -- .claude/skills/scan-market/scan_config.jsonc` 能给出显式提交——即「regime wiring 不得静默开启」改锁 config+提交痕，探针代码随附在测试文件注释）、`tests/learning/test_evidence_manifest.py`、`tests/learning/test_gate_recal.py`（摘 register 用例）
- Modify(docs): `.claude/skills/scan-market/SKILL.md:177` 段、`STAGES.md` 「实验晋升与回滚控制面」节（:236-256）→ 替换为 4 行「治理模型（2026-08-18 起）：challenger=影子账本呈证 → proposals 人批 → 开发会话改 config/代码；无自动晋升机器」
- Archive: `mkdir -p archive/20260818 && mv context_claude/learning/experiments archive/20260818/ && mv context_claude/learning/monitors.json context_claude/learning/exp1_mainflow5d.jsonl archive/20260818/`

- [ ] **Step 1: 防误伤 grep**：`grep -rn "promotion" autoresearch/ | grep -v experiment | grep -v rollback` → 确认 factor_lab/feedback_store/style_spread/t1_review 的同名物**不在**本次删除面。
- [ ] **Step 2: 按上表删除与修改**（顺序：先拆 4 个挂点 → 删 5 个模块 → 删单职测试 → 改双职测试 → 改 docs → 归档数据）。
- [ ] **Step 3: 固定收尾三步**。Commit: `refactor(learning): D1 experiment_registry 家族整删(5实验全冻/零生产调用,用户裁定)`

### Task 3.2: D2 权重自动重标定退役（裁决 A4）

**Files:**
- Modify: `autoresearch/learning/retro.py:1150-1187`（删 `recalibrate_and_log` + `top_weight_changes`）；`autoresearch/scan/prewarm.py:169-188`（删 `--calibrate`）；`autoresearch/learning/feedback_store.py`（删 `log_change`(:641)/`snapshot_weights`(:793)/`rollback_weights`(:803)——**先 grep 三函数全部调用者**：Task 2.5 的裁决记账走的是 feedback 的哪个函数？若裁决记账依赖 `log_change`，则保留 `log_change` 只删 snapshot/rollback，并在 commit 信息记偏差）
- Delete: `autoresearch/learning/changelog_ledger.py` + `tests/learning/test_changelog_ledger.py`
- Modify: `nightly_close.py:110` `_ledgers` 表删 `"changelog_ledger"`；`prelude.py:476-482` 表同删；`prelude.py:388-389` `_learning_health` heartbeat 行删；`.claude/skills/scan-retro/retro-playbook.md:50` 该步删 + SKILL description 摘 "auto weight recalibration"
- Modify(tests): `test_nightly_close.py`/`test_prelude.py` 账本表断言；`tests/research/test_factor_lab.py:383-401` 摘 `test_recalibrate_and_log_calls_extend_before_calibrate`（其余 factor_lab 用例保留——calibrate 降为人工 CLI 仍活）；`tests/learning/test_feedback_store.py` 摘对应用例
- Archive: `mv context_claude/factor_lab/weights.*.json archive/20260818/`（sha 快照 4 份；`weights.json` 本体**原地保留**——`pick_weights` 生产读侧）

- [ ] **Step 1**: 删除面 grep 确认 `recalibrate_and_log` 调用者只有 playbook 与 prewarm（+测试）。
- [ ] **Step 2**: 按表执行。**保留**：`factor_lab.calibrate/calibrate_regime/extend_plan`（人工 CLI）、`common/scoring.py:423 pick_weights`、`weights.json`、replay PIT。
- [ ] **Step 3: 固定收尾三步**。Commit: `refactor(learning): D2 权重自动重标定退役(17版ΔIC+0.0004;读侧/人工CLI保留,用户裁定)`

### Task 3.3: D3 t1-review LLM 腿退役（裁决 A5；role 12→10）

**Files:**
- Delete: `.claude/workflows/t1-review.js`
- Modify: `autoresearch/learning/t1_review.py`（删 `finalize`(:655-687)、`load_candidates`(:416)/`upsert_candidates`(:423)/`promote_candidates`(:451)、`build_and_stage` 的打包尾巴(:617-652 中 agents_cfg/open_candidates/rows 透传——内核 `build_scorecard`+`_stage` 保留)、CLI `build --json`/`finalize` verb）
- Modify: `.claude/skills/scan-market/scan_config.jsonc:50-51`（删 t1_diag/t1_synth 两行；:36 注释 12→10）；`autoresearch/scan/user_config.py:146-150`（`_AGENT_ROLES` 删两名 → 10 个）、`:181-183`（`_ROLE_FALLBACK` 删 t1 两行）、`:142-143` 注释、`main()` docstring `:457-476` t1 装载链整段；`autoresearch/trace/usage_reconcile.py:78-80` 注释
- Modify: `.claude/skills/scan-retro/SKILL.md`（快环节 :20-27 整段改「快环=nightly 自动确定性回补(t1_backfill/gap_finalize),无 LLM 段;深诊按需人开」+ description 去 t1-review workflow 字样）；STAGES.md 相应引用
- Modify(tests): `tests/learning/test_t1_review.py` 摘 build_and_stage/finalize/candidates 用例（scorecard/verdict/账本用例保留）；`tests/test_agent_defs.py` 摘 t1-review.js 的 AGENT_DEFAULTS 断言（**保留** scan-market/l4-stock/dossier-init 三张表的断言）；`tests/trace/test_usage_reconcile.py` 若引 t1 role 同步
- Archive: `mv context_claude/learning/t1_candidates.jsonl archive/20260818/`
- **保留清单（grep 确认动后仍被引用）**: `build_scorecard/render_scorecard_md/_stage/append_ledger/verdict/next_trade_day/_fetch_prices/_fetch_gap_prices/pending_pairs/backfill_day/gap_finalize_pending/_industry_neutral_gap/_update_ledger_gap/ledger_tail_summary/render_t1_calibration_block/render_ledger_report/mark_done/_NON_GENUINE_LANES` + nightly 的 `t1_backfill`/`t1_gap_finalize` 两步。

- [ ] **Step 1-3: 固定套路**（改完先单跑 `uv run --no-sync python -m pytest tests/learning/test_t1_review.py tests/scan/test_user_config.py -q` 再全量）。Commit: `refactor(learning): D3 t1-review LLM 腿退役(22日仅8合格终判;确定性侧全保留;role 12→10,用户裁定)`

### Task 3.4: D4 macro 周度 harvest 空转腿

- [ ] **Step 1**: `launchctl bootout gui/$(id -u) com.tradingagents.macro-harvest 2>/dev/null; rm -f ~/Library/LaunchAgents/com.tradingagents.macro-harvest.plist`
- [ ] **Step 2**: `git rm scripts/macro-harvest.sh scripts/com.tradingagents.macro-harvest.plist`；`.claude/skills/macro-research/SKILL.md` + playbook 注明「无周度 cron；full 档全手动（harvest → LLM 节 → assemble → state）」。
- [ ] **Step 3**: `mv context_claude/macro/2026-08-02 context_claude/macro/2026-08-09 context_claude/macro/2026-08-16 archive/20260818/`（孤儿 data.md）。**保留** `macro/harvest.py`、`state.py`、frame/prelude presence-gated 读侧及其测试。
- [ ] **Step 4: 固定收尾三步**。Commit: `refactor(macro): D4 周度 harvest 空转腿退役(桥断3周,full 档全手动)`

### Task 3.5: D5 L3_evidence 空壳产线

- [ ] **Step 1**: 删 `autoresearch/scan/l3/evidence.py` 的 `harvest_l3_evidence`(:44-83) + `l3/prompt.py:378` 调用行与 `:10` import 名；**保留** `load_l3_input`(:12-42) presence-gated 读侧与 prompt 三列渲染（历史目录仍可读；新日自然缺省）。
- [ ] **Step 2**: 删 `tests/scan/test_l3_evidence_lake.py`（单职）；`tests/scan/test_l3_prepare.py:29-37`、`tests/scan/test_agents.py:29-40` 双职 fixture 改为「预置盘上 evidence 文件」（不再调 harvest）。
- [ ] **Step 3**: 在 `l3/prompt.py` `_delta_filter` (:149-152) 处加一行注释：「2026-08-18 起 evidence 产线退役,本判据对新日恒缺省——历史行为保留」。
- [ ] **Step 4: 固定收尾三步**。Commit: `refactor(scan): D5 L3_evidence 空壳产线退役(196-201/203 空壳;读侧保留)`

### Task 3.6: D6 sector-brief 研判段砍除（裁决 A6；四处联动）

- [ ] **Step 1**: `autoresearch/sector/brief.py` 摘 `VIEW_HDR/extract_view/parse_direction/_DIR_RE`（保 `TERRAIN_HDR/extract_terrain/render_terrain_block`）。
- [ ] **Step 2**: `.claude/agents/sector-brief.md:26-27,35-36`（研判段模板与方向行）改单段契约；`.claude/skills/sector-research/sector-playbook.md` lite(:27-28,35) 与 full(:47) 同步；`scan-market.js:285` prompt 文案摘「## 研判段 仅 L5」（js 改动过 AsyncFunction 探针）。
- [ ] **Step 3**: `report_sections.py:579-615` `_sector_view_section` 整删 + `:982` 调用点删（`SECTOR_SECTION_MAX_BYTES`/`_terrain_gist` 若仅它用则同删——grep 确认）；L5 行业方向由 `market.py:259-267` 确定性 top3 节独扛。
- [ ] **Step 4**: `learning/sector_ledger.py` 删 `record_calls`(:42-54) + `publisher.py:447-451` 挂点（`record_top3` 与其挂点保留）；`tests/learning/test_sector_ledger.py` 摘 brief-call 用例（top3 用例保留，`test_sector_top3.py` 不动）；`tests/scan/test_report_sections.py:120-147` 摘 `_sector_view_section` 用例；`tests/sector/test_brief_inject.py`（地形段注 L4）**必须仍绿**。
- [ ] **Step 5: 固定收尾三步**。Commit: `refactor(sector): D6 研判段砍除(只落一行字;行业方向由确定性 top3 独扛,用户裁定)`

### Task 3.7: D7 watchlist 残余

- [ ] **Step 1**: `autoresearch/sector/pack.py` 删 `_WS_WATCHLIST`(:28) + `select_briefing_sectors` 的 wl_path 形参与 `_add(..., "观察单")` 分支（~10-15 行）；`tests/sector/test_pack.py:90` 用例摘除；STAGES.md:108/206 两处文案。
- [ ] **Step 2**: `mv context_claude/watchlist.csv archive/20260818/`。
- [ ] **Step 3**: **不动**：`watchlist_trigger` lane 常量（`gates.py:34`、`t1_review.py:57`）、`test_prelude.py:91-113`、`test_journal.py:14-41`、`self_review.py` 墓碑白名单。
- [ ] **Step 4: 固定收尾三步**。Commit: `refactor(sector): D7 watchlist 最后残余摘除(lane 常量保留)`

### Task 3.8: D13 intel 可信度硬化（裁决 A9）

**Files:**
- Modify: `autoresearch/scan/l4/intel_guard.py`（新增 `lint_claims`；`guard_intel` 尾部串接）
- Modify: `.claude/agents/l4-intel.md:40`（他票对账豁免句）与 `.claude/agents/l4-card.md`（三条固化规则）
- Test: `tests/scan/test_intel_guard.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
def test_price_claim_without_url_annotated():
    text = "- [T0] 同板块龙头XX科技(300001)今日涨停,梯队升温\n"
    out, meta = lint_claims(text, self_code="600188", trade_date="20260818",
                            pct_lookup=lambda code, d: 3.1)
    assert "〔未核·缺URL〕" in out and meta["no_url"] == 1


def test_peer_limitup_claim_reconciled_against_lake():
    text = ("- [T0] XX科技(300001)涨停 https://example.com/a\n")
    out, meta = lint_claims(text, self_code="600188", trade_date="20260818",
                            pct_lookup=lambda code, d: 2.0)   # 湖里当日只涨 2%
    assert "〔未核·与湖不符〕" in out and meta["mismatch"] == 1


def test_verified_claim_untouched():
    text = "- [T0] XX科技(300001)涨停 https://example.com/a\n"
    out, meta = lint_claims(text, self_code="600188", trade_date="20260818",
                            pct_lookup=lambda code, d: 10.02)
    assert out == text and meta["mismatch"] == 0 and meta["no_url"] == 0
```

- [ ] **Step 2: 确认失败** → **Step 3: 实现**（红线不变：**只标注不拒稿**，退出码恒 0）：

```python
_CLAIM_RE = re.compile(r"(涨停|连板)")
_CODE_RE = re.compile(r"\((\d{6})\)")
_URL_RE = re.compile(r"https?://")


def _lake_pct_chg(code6: str, yyyymmdd: str) -> float | None:
    """lake/daily/<日>.parquet 查单票 pct_chg;缺分区/缺票 → None(无从对账,只标缺URL不标不符)。"""
    import pandas as pd
    from autoresearch.common import workspace as ws
    part = ws.repo_root() / "lake" / "daily" / f"{yyyymmdd}.parquet"
    if not part.is_file():
        return None
    df = pd.read_parquet(part, columns=["ts_code", "pct_chg"])
    hit = df[df["ts_code"].str.startswith(code6)]
    return float(hit["pct_chg"].iloc[0]) if len(hit) else None


def lint_claims(text: str, *, self_code: str, trade_date: str,
                pct_lookup=None) -> tuple[str, dict]:
    """他票涨停/连板断言的确定性 lint(D13):缺 URL 标〔未核·缺URL〕;
    带 URL 但湖 pct_chg < 9 标〔未核·与湖不符〕。只标注不拒稿(红线:只拒稿不拒票,
    本函数连稿都不拒)。pct_lookup 可注入(测试);缺省走 lake。"""
    lookup = pct_lookup or _lake_pct_chg
    meta = {"no_url": 0, "mismatch": 0}
    out_lines = []
    for line in text.splitlines(keepends=True):
        marked = line
        if _CLAIM_RE.search(line):
            codes = [c for c in _CODE_RE.findall(line) if c != self_code]
            if codes:
                if not _URL_RE.search(line):
                    meta["no_url"] += 1
                    marked = line.rstrip("\n") + " 〔未核·缺URL〕\n"
                else:
                    pct = lookup(codes[0], trade_date)
                    if pct is not None and pct < 9.0:
                        meta["mismatch"] += 1
                        marked = line.rstrip("\n") + " 〔未核·与湖不符〕\n"
        out_lines.append(marked)
    return "".join(out_lines), meta
```

`guard_intel` 在 OK/TRIMMED 返回路径前对稿件跑 `lint_claims`（`trade_date` 由 scan_dir 目录名转 `YYYYMMDD`；标注后原地覆写稿件——覆写前按 W9-B2-fix 同款惯例把原稿留 `.orig` 侧车，`:27-30` 注释有先例），返回 dict 增 `"claims_lint": meta`。

- [ ] **Step 4**: `.claude/agents/l4-intel.md:40` 的「这些是题材强度证据，不与本票 OHLCV 对账」改为「他票涨停/连板断言必须带原文 URL；缺 URL 或与湖收盘不符的会被机器标〔未核〕——未核断言不得作为梯队强度证据计入」。`.claude/agents/l4-card.md` 判断纪律区追加三行（pr_20260810_001/pr_20260811_001/pr_20260811_002 固化）：

```markdown
- 上游商品/运价指数作论据须按产业链位置定符号(煤企/煤电一体为正、纯火电为反、港口不吃运价);链层催化排出隔夜窗。
- winner≥80% 作空头论据必须以「当日跑输板块」为必要触发,「题材活跃延续期」为排除项。
- 买入论点押具名催化(数据/事件)时,先查该数据是否已提前落地;已落地且为负 → 判「兑现机制作废」,直接 UW 并早停。
```

- [ ] **Step 5: 跑测通过 + 全量绿 + 固定收尾**。Commit: `feat(scan): D13 intel 他票断言确定性 lint(缺URL/与湖不符标未核)+卡纪律三条固化`

---

## 批次 4 · 杆/壳/purge/备份/池

### Task 4.1: D8 abstention v1 拔杆

- [ ] **Step 1: 两日重跑一致复核**：挑 2026-07-16 与 2026-08-06 两日，`uv run --no-sync python -m autoresearch.learning.abstention_ledger` 跑两遍，diff 两遍的 `reports_claude/learning/abstention_ledger.md` 对应行——必须逐字一致。
- [ ] **Step 2**: 删 v1 生产列/代码（`abstention_ledger.py` 内 grep `v1`/`recall_ceiling_n` 右侧诊断列 + v1 status headline；:480 墓碑注释兑现）+ 测试同步；历史报告不改写。
- [ ] **Step 3: 固定收尾三步**。Commit: `refactor(learning): D8 abstention v1 拔杆(v2 19/19 成熟,两日重跑一致)`

### Task 4.2: D9 gp 壳合并

- [ ] **Step 1: AsyncFunction 探针脚本先行**（本批所有 js 改动的验收器；放 `scripts/probe_workflow_js.mjs`）：

```js
// scripts/probe_workflow_js.mjs —— node --check 对 ESM+顶层return 是假绿灯(判例 wave35),
// 用 AsyncFunction 真解析。用法:node scripts/probe_workflow_js.mjs .claude/workflows/*.js
import { readFileSync } from "node:fs";
const AsyncFunction = Object.getPrototypeOf(async () => {}).constructor;
let bad = 0;
for (const path of process.argv.slice(2)) {
  let src = readFileSync(path, "utf8")
    .replace(/^export const meta =/m, "const meta =");
  try { new AsyncFunction(src); console.log("OK", path); }
  catch (e) { bad++; console.error("FAIL", path, e.message); }
}
process.exit(bad ? 1 : 0);
```

- [ ] **Step 2: python 侧合并子命令**：`autoresearch/scan/l4/intel_status.py`（或 intel_status 真身所在模块——grep `intel_status` 定位）新增 `intel_finish`：顺序执行 guard→status normalize，打一行合并 JSON `{"guard": <guard_intel 返回>, "status": <intel_status 返回>}`；`l4_tasks init` 的 CLI 返回已含 `dispatch_batches`（:694-695 现成），确认 scan-market.js #23 可直接消费其输出后合并 #21+#23。
- [ ] **Step 3: scan-market.js 合并组 A/B/C/D/E**（spec §4-D9；每合并一组跑一次探针 + `git diff` 自查变量插值未破坏）。**保持 role 不变**（仍 gp_shell/gp_shell_json），只减 spawn 次数。
- [ ] **Step 4: l4-stock.js 合并组 G/H(/I 视 A7 裁定)**。
- [ ] **Step 5**: `node scripts/probe_workflow_js.mjs .claude/workflows/*.js` 全 OK + 全量 pytest 绿（test_agent_defs 的 AST 锁只看 AGENT_DEFAULTS 表，不受 spawn 数影响）。Commit: `perf(workflows): D9 壳合并——scan-market -4~5 spawn/日,l4-stock -2/股(AsyncFunction 探针)`

### Task 4.3: D10 staging purge CLI

**Files:**
- Create: `autoresearch/ops/__init__.py`、`autoresearch/ops/purge.py`
- Modify: `autoresearch/scan/prelude.py`（汇总屏加一行 dry-run 提示；紧邻 `_learning_health` 输出区）
- Test: `tests/ops/test_purge.py`

**Interfaces:**
- Produces: `python -m autoresearch.ops.purge [--apply] [--root …]`。规则（写死为模块常量，白名单式）：
  - `context_root()` 顶层 `*_slim*.md` mtime >14 天 → 删；
  - `context_root()/scan/<date>/` 中仅 4 件：`L1_scored_full.csv`、`L1_recall_top1000.csv`、`L3_evidence/`、`L3_news/`，date < 今日−10 个**扫描日**（按 scan/ 下现存日期目录排序数第 10 个，不查交易日历）→ 删；
  - `scan/<date>/t1_review/` 仅当该日 `t1_review.jsonl` 里 final_verdict 已回填 → 删；
  - `_NEVER = ("retro", "_l4_tasks.json", "finalists.csv", "meta.json", "details", "_ensemble_", "shadow", "_candidate_passport.json", "stage_results", "_token_usage.json")` 命中即跳过（防呆双保险：即使规则写错也删不到账本原料）。
  - 缺 `--apply` 时只打印逐条 would-delete 与合计字节。
- [ ] **Step 1: 写失败测试**（tmp workspace fixture：造 12 个日目录 + 顶层 slim；断言 dry-run 清单、--apply 后 4 大件删而 `retro/`/`_l4_tasks.json` 在、第 10 个日目录内不删）→ **Step 2-4: TDD 常规** → **Step 5: Commit** `feat(ops): D10 staging purge(文件类白名单+永不触碰账本原料)`

### Task 4.4: D11 知识资产 nightly 备份

**Files:**
- Modify: `autoresearch/learning/nightly_close.py`（run() 步骤表末位加 `_backup`）
- Test: `tests/learning/test_nightly_close.py`（步骤表断言 + `_backup` 单测）

- [ ] **Step 1: 写失败测试**（步骤表断言尾项 `"backup"`；`_backup()` 对 tmp workspace 产出 `backups/knowledge_<date>.tar.gz` 且第 15 份最旧被清）
- [ ] **Step 2-3: 实现**

```python
    def _backup() -> str:
        """D11:不可重算知识资产(判例库/档案/账本)每日 tar,保 14 份。唯一新增自动步(数据保护)。"""
        import tarfile
        root = ws.context_root()
        dest = ws.repo_root() / "backups"
        dest.mkdir(exist_ok=True)
        out = dest / f"knowledge_{today}.tar.gz"
        with tarfile.open(out, "w:gz") as tar:
            for rel in ("knowledge",):
                p = root / rel
                if p.exists():
                    tar.add(p, arcname=rel)
            for f in sorted((root / "learning").glob("*.jsonl")):
                tar.add(f, arcname=f"learning/{f.name}")
            wj = root / "factor_lab" / "weights.json"
            if wj.exists():
                tar.add(wj, arcname="factor_lab/weights.json")
        kept = sorted(dest.glob("knowledge_*.tar.gz"))
        for old in kept[:-14]:
            old.unlink()
        return f"{out.name}({out.stat().st_size // 1024}KB),存 {min(len(kept),14)} 份"
```

（`ws.repo_root()` 若无此 API 则 grep workspace.py 用其真名；`backups/` 加进 `.gitignore`。`today` 用 run() 里既有的日期变量名。）

- [ ] **Step 4-5: 跑测 + Commit** `feat(learning): D11 知识资产 nightly 备份(tar 保14份)`

### Task 4.5: D12 dossier 池收口（裁决 A8）

**Files:**
- Modify: `autoresearch/dossier/pool.py`（新增 `prune` —— 先读该模块现有 API 与 coverage_pool.json 的真实 schema）
- Test: `tests/dossier/test_pool.py`（追加）

- [ ] **Step 1**: 读 `pool.py` 与 `context_claude/knowledge/coverage_pool.json`，确认字段（stocks/cap/pending_init 的真实结构与已建档标记）。
- [ ] **Step 2: TDD 实现 `prune(pool, keep=cap)`**：保留顺序 = 📌 pinned 全保 → 已建档且近 30 日 finalist 出现 ≥1 次 → 近 30 日 finalist 出现频次降序 → 截断到 cap=30；被剪的 pending_init 一并销案（写回 pool json + 打印销案清单）。CLI verb 挂该模块既有入口。
- [ ] **Step 3**: 执行 `prune`（真数据），把「保留 30 + 销案清单 + 剩余真缺口 N 只」打给用户过目；缺口补建走既有 dossier-init workflow 逐票拉起（$1.92/份），**FAILED 的票单票重派一次**（07-27 批 24% FAILED 无重试的教训——重派后仍 FAILED 记入 pool 的 failed 名单不再自动重试）。
- [ ] **Step 4: 固定收尾三步**。Commit: `feat(dossier): D12 池收口 89→30(📌+finalist频次)+缺口补建带单次重试`

---

## 批次 5 · 活体验收（连续 3 个真实扫描日）

### Task 5.1: 验收清单（逐日勾）

- [ ] 每成功扫描日 `_relative_buy_decision.json`：`mode=="active"`、恰 1 只 BUY（或 `blocked=true` 且 `blocked_reasons` 全为真红旗/真数据故障——**不允许**出现 `产物形状·`/`usage_reconcile·` 引发的 data_a BLOCKED）。
- [ ] summary/brief 头条 = 相对 BUY（✅ 标）；brief_lint 零 fail；「buy-list N 只」处显示真 BUY 数。
- [ ] `reports_claude/learning/relative_buy.md`：📌/非📌 分层栏目在场（Task 2.4 若未覆盖 render 分层，在此补——`relative_ledger.render` 按 jsonl 行的 pinned 旗分两组汇总，账本 jsonl 行本就带 code，join decision 文件的 candidates.pinned）；IMMATURE 横幅仍在（<20 决策日）。
- [ ] 日成本回 ≤$35 带（`token_usage.md` 三日均值）；spawn 数较基线降 ≥25（`_token_usage.json` agent 计数）。
- [ ] `backups/knowledge_<date>.tar.gz` 三日各一份；nightly 六步 + backup 全绿（`journal.md` 无新增欠账 nag）。
- [ ] 验收记录落 `docs/research/2026-08-XX-e6-activation-liveness.md`（三日逐项读数），呈用户收波。

---

## Self-review 备忘（计划作者已核）

- spec §3 E0-E5 → Task 0.1/1.1-1.8/2.1-2.4；§4 D1-D13 → Task 3.1-3.8/4.1-4.5；§6 → Task 2.5/2.6；§7 批次映射一致；§8 回滚=逐 commit revert + config 单行。
- 类型/签名一致性：`fail_class` 消费于 `health._gate4_has_data_fail`；`failed_data` 生产(T1.2)/消费(T1.3)同名；`reconcile` 生产(T1.4)/挂点(T1.5)同名；`load_decision`(T2.4) 仅盘读不现算；`exclude_pinned` 贯穿 T2.1(config)/T2.2(决策)/T5.1(验收)。
- 已知不确定点均已内嵌「先读真源再写」步骤（stage_result schema、_book_path 签名、feedback_store.log_change 是否被裁决记账依赖、pool.py schema、intel_status 模块名）——执行者遇实源不符时按真源改并在 commit 信息记偏差，**不得静默跳过**。
