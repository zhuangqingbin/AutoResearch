# 可买性对齐 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「为什么不可买」从漏斗里解掉：召回权重换成偏好档、L2 加落刀帽/健康 floor/行业席位、卡片写机读入场行、E6 池回到全部派发卡并分 A/R 两级、brief 印不可买归因；成功尺 = 10 个成功扫描日里 A 级 BUY 天数 ≥ 5。

**Architecture:** 全部改动在确定性 Python 层（`autoresearch/`）与两份 agent 定义（`.claude/agents/l4-card.md`、`.claude/skills/stock-research/lite-playbook.md`；Codex 的 `.codex/agents/l4_card.toml` 只引用前者，不改）。四批：批 0 缺陷修 → 批 1 权重档 → 批 2 L2 形状 → 批 3 卡入场行 + E6 v4 + 归因。每批独立提交、每批一根配置回滚杆、每个新旋钮三件套（白名单 + 消费点 + `tests/scan/test_config_knobs.py`）。

**Tech Stack:** Python 3.12 / pandas / pytest；运行统一用 `uv run --no-sync python -m pytest ...`；配置唯一事实源 `.claude/skills/scan-market/scan_config.jsonc`。

**Spec:** `docs/specs/2026-09-24-buyability-realignment-design.md`（本计划从它论证；执行者两份都读）。

## Global Constraints

- 主尺 `gap_c1_o2` 不换；不提 swing；不重开自动重标定；不恢复 L4 复用；不重建 pipeline 对象。
- `scan_config.jsonc` 是唯一参数事实源：新旋钮 = `user_config._SUB_WHITELIST` + `_KNOB_TYPES` + 消费点 `knob(...)` + `tests/scan/test_config_knobs.py`，缺一不上生产。
- 内建默认一律 = 旧行为（parity）；新行为只由生产配置文件打开；每批一根回滚杆。
- 引擎隔离：Python 侧共用；不写死 `context_claude/`，路径走 `autoresearch.common.workspace`。
- 新产物先登记 `autoresearch/contracts/artifacts.py` 的 `ARTIFACTS`，再写生产者；新生产者必须 `grep` 出真实调用点。
- 每个任务：先写失败测试 → 跑红 → 最小实现 → 跑绿 → 提交。全量 `uv run --no-sync python -m pytest -q tests/` 在每批末尾跑一次绿。ruff 干净（`uv run --no-sync ruff check autoresearch tests`）。
- 提交信息末尾加：`Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`。
- 与 spec 的四处差异（已回写 spec）：① 入场硬门与 A/R 分级同受 `relative_buy.tiering` 控制（一根杆）；② 不升 `CARD_SCHEMA_VERSION`（它是 ResearchCard JSON 的版本，与 md 入场行无关）；③ 归因行只印在 brief ③，summary 仪表盘镜像 brief；④ 行业席位在 L1 注入（镜像 pinned），L2 作保留行追加。

---

## 文件结构（先定边界，再拆任务）

| 文件 | 责任 | 批 |
|---|---|---|
| `autoresearch/scan/relative_buy.py` | E6：票级 data_a、入场门、A/R 分级、`tiering` 旋钮 | 0/3 |
| `autoresearch/scan/decision_finalize.py` | 盲卡标记 `mark_blind_cards` + 终评级/事实本过滤 | 0 |
| `autoresearch/scan/report_sections.py` | `build_summary` 调用盲卡标记 | 0 |
| `autoresearch/scan/brief.py` | 昨日 delta 跨 run、BUY 行实测文案、A/R 文案、归因行 | 0/3 |
| `autoresearch/scan/relative_facts.py` | 决策文件读模型加 `tier` | 3 |
| `autoresearch/scan/l4/intel_guard.py` + `self_review.py` | intel 软顶裁剪 + lint 识别已裁 | 0 |
| `autoresearch/scan/user_config.py` | 新旋钮白名单/类型 | 1/2/3 |
| `autoresearch/common/scoring.py` | `combine_group_scores`、`resolve_weights`、`preference_weights_doc`、`falling_knife_mask` | 1/2 |
| `autoresearch/scan/universe.py` | 权重档接线、落刀帽份额、行业席位 L1 注入、CSV 列 | 1/2 |
| `autoresearch/session_agent/domain_ops.py` | 身份快照记权重档 | 1 |
| `autoresearch/research/menu_replay.py`（新） | 离线重算 L1′/L2′ 形状（A1–A7） | 1/2 |
| `autoresearch/scan/recall/l2_stratify.py` | 落刀帽、`sector_seat` 保留行 | 2 |
| `autoresearch/scan/sector_seats.py`（新） | 行业席位挑选 | 2 |
| `autoresearch/scan/menu.py`、`l2_knife_audit.py` | 落刀谓词改调单源 | 2 |
| `autoresearch/scan/l3/merge.py`、`triage.py`、`prompt.py` | 席位剔刀、①c 强留、🏭 列、guard 标记 | 2 |
| `autoresearch/contracts/agent_output.py`、`scan/l4/parsers.py` | 卡入场行契约与解析 | 3 |
| `.claude/agents/l4-card.md`、`.claude/skills/stock-research/lite-playbook.md` | 模板加入场行 + 规则 | 3 |
| `autoresearch/scan/buyability.py`（新） | 不可买归因产物 | 3 |
| `autoresearch/scan/post_run.py` | 接 `tiering` 与 buyability | 3 |
| `autoresearch/scan/outcome.py`、`ledger_views.py`、`populations.py` | `buy_tier`、`n_buy_a`、`wall`、`e6_a_tier_day_share` | 3 |
| `autoresearch/contracts/artifacts.py` | 登记 `_blind_cards.json`、`_sector_seats.json`、`_buyability.json` | 0/2/3 |
| `.claude/skills/scan-market/scan_config.jsonc`、`STAGES.md`、`SKILL.md` | 生产配置值与文档 | 各批 |

---

# 批 0 · 缺陷修（无需裁定；回放 09-15/09-17 现场验证）

### Task 0: 提交工作树里 09-15 的 harvest 修法

**Files:**
- Commit only: `autoresearch/analyze/harvest.py`, `tests/analyze/test_harvest_slim_split.py`, `autoresearch/scan/l4/prompts.py`, `tests/scan/test_yesterday_echo.py`

**Interfaces:**
- Consumes: 工作树里已有但未提交的改动（`git status` 见 `M autoresearch/analyze/harvest.py` 等）。
- Produces: 干净的 harvest 基线，批 0 其余任务在其上提交。**不要**把 `.claude/agents/*`、`.claude/settings.json`、`.codex/**`、`AGENTS.md`、`scripts/hooks/*`、`docs/superpowers/plans/2026-09-14-*` 一起提交（那是另一条线的未完成工作，留给用户）。

- [ ] **Step 1: 看清这四个文件改了什么**

Run: `git diff --stat -- autoresearch/analyze/harvest.py tests/analyze/test_harvest_slim_split.py autoresearch/scan/l4/prompts.py tests/scan/test_yesterday_echo.py`
Expected: 四个文件各有 `+/-` 行；其余文件不在列表里。

- [ ] **Step 2: 跑这两个测试文件**

Run: `uv run --no-sync python -m pytest -q tests/analyze/test_harvest_slim_split.py tests/scan/test_yesterday_echo.py`
Expected: 全绿。红了就停下来把失败贴给用户，不要自己改 harvest 逻辑（那是 09-15 那条线的活）。

- [ ] **Step 3: 只提交这四个文件**

```bash
git add autoresearch/analyze/harvest.py tests/analyze/test_harvest_slim_split.py autoresearch/scan/l4/prompts.py tests/scan/test_yesterday_echo.py
git commit -m "fix(scan): scan L4 slim harvest runs as scan.l4.slim under the run staging

Regression from 262f058: harvest.main hardcoded stock.harvest, so the write
guard refused every scan L4 slim (09-14 run: 7/7 BLOCKED, no cards).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 1: 票级 `data_a`（一只票的 slim 失败不再连坐全天）

**Files:**
- Modify: `autoresearch/scan/relative_buy.py:426-447`（`_data_contract_ok`）、`:602-607`（`_hard_gate` ②）
- Test: `tests/scan/test_relative_buy.py`

**Interfaces:**
- Produces: `_data_contract_ok(scan) -> tuple[bool, str, frozenset[str]]`（第三项 = 票级失败码集）；`ctx["data_a"]` 变三元组，唯一消费者是 `_hard_gate`。

- [ ] **Step 1: 写三个失败测试（追加到 `tests/scan/test_relative_buy.py` 的 data_a 段落之后）**

```python
def _health(**stage_results):
    return {"date": DATE, "core_missing": [], "run_contract": {"status": "OK"},
            "stage_results": {"status": "OK", **stage_results},
            "decision_records": {"status": "OK"}}


def test_l4_stage_failure_vetoes_only_that_ticker(tmp_path):
    """票级 data_a(2026-09-24 §2.6-2):`l4_<code>` 失败只否决该票,其余候选照常。"""
    scan = _build_scan(tmp_path, _RANK_CANDS,
                       run_health=_health(failed=["l4_600188"], failed_data=["l4_600188"]))
    doc = build_decision(scan)
    by = _by_code(doc)
    assert by["600188"]["hard_gate"]["data_a"] is False
    assert all(by[c]["hard_gate"]["data_a"] for c in _RANK_ORDER if c != "600188")
    assert doc["buys"] and doc["buys"][0]["code"] == "002345"
    detail = [e for e in doc["excluded"] if e["code"] == "600188" and e["reason"] == "hard_gate.data_a"]
    assert detail and "l4_600188" in detail[0]["detail"]


def test_day_level_data_failure_still_vetoes_everyone(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS,
                       run_health=_health(failed=["gate2"], failed_data=["gate2"]))
    doc = build_decision(scan)
    assert all(not row["hard_gate"]["data_a"] for row in doc["candidates"])
    assert doc["blocked"] is True


def test_legacy_health_without_failed_data_keeps_v11_day_level_semantics(tmp_path):
    """历史 run_health 无 failed_data 键 → 旧口径:任一 failed(含 l4_*)全天否决,不改写历史判定。"""
    scan = _build_scan(tmp_path, _RANK_CANDS, run_health=_health(failed=["l4_600188"]))
    doc = build_decision(scan)
    assert all(not row["hard_gate"]["data_a"] for row in doc["candidates"])
```

- [ ] **Step 2: 跑红**

Run: `uv run --no-sync python -m pytest -q tests/scan/test_relative_buy.py -k "vetoes_only_that_ticker or still_vetoes_everyone or v11_day_level"`
Expected: 第一个 FAIL（`600188` 之外的候选 data_a 也为 False），另两个可能已绿。

- [ ] **Step 3: 改 `_data_contract_ok` 与 `_hard_gate`**

在 `relative_buy.py` 顶部 import 区确认有 `import re`（没有就加）。替换 `_data_contract_ok` 整个函数：

```python
_L4_STAGE_RE = re.compile(r"^l4_(\d{6})$")


def _data_contract_ok(scan: Path) -> tuple[bool, str, frozenset[str]]:
    """A 级数据契约:返回 (日级是否 OK, 日级原因, 票级失败码集)。

    v4.0(2026-09-24 §2.6-2):`stage_results.failed_data` 里 `l4_<code>` 形状的项是
    **单票** slim/卡失败,只否决该票;其余项仍是日级(全体连坐)。历史 `run_health` 无
    `failed_data` 键 → 保持 v1.1 旧口径(`failed` 任一项全天否决,不改写历史判定)。
    """
    empty: frozenset[str] = frozenset()
    health = _json_doc(scan / "run_health.json")
    if not isinstance(health, dict):
        return False, "run_health.json 缺失,A 级数据契约无从判定", empty
    if health.get("core_missing"):
        return False, f"core_missing={sorted(health['core_missing'])}", empty
    contract = health.get("run_contract") or {}
    if str(contract.get("status") or "") != "OK":
        return False, f"run_contract.status={contract.get('status')!r}", empty
    stages = health.get("stage_results") or {}
    if str(stages.get("status") or "") != "OK":
        return False, f"stage_results.status={stages.get('status')!r}(非 OK)", empty
    failed_data = stages.get("failed_data")
    per_ticker = empty
    if failed_data is None:            # 历史 run_health 无此键 → v1.1 旧口径(日级连坐)
        day_level = list(stages.get("failed") or [])
    else:
        per_ticker = frozenset(m.group(1) for s in failed_data
                               if (m := _L4_STAGE_RE.match(str(s))))
        day_level = [s for s in failed_data if not _L4_STAGE_RE.match(str(s))]
    if day_level:
        return False, f"stage_results.failed_data={sorted(day_level)}", per_ticker
    records = health.get("decision_records") or {}
    if str(records.get("status") or "") != "OK":
        return False, f"decision_records.status={records.get('status')!r}(非 OK)", per_ticker
    return True, "", per_ticker
```

`_hard_gate` 的 ② 段替换为：

```python
    # ② A 级数据契约:日级(全体)+ 票级(v4.0:`l4_<code>` 只否决该票)
    day_ok, day_reason, per_ticker = ctx["data_a"]
    if not day_ok:
        fail("data_a", day_reason)
    elif code in per_ticker:
        fail("data_a", f"stage l4_{code} 失败(票级 data_a,不连坐当日其他候选)")
    else:
        gates["data_a"] = True
```

- [ ] **Step 4: 跑绿 + 全文件**

Run: `uv run --no-sync python -m pytest -q tests/scan/test_relative_buy.py`
Expected: 全绿（既有 `test_data_contract_anomaly_fails_data_a_for_every_candidate` 用 `core_missing`，不受影响）。

- [ ] **Step 5: 用真实现场回放核对**

Run:
```bash
uv run --no-sync python - <<'EOF'
from pathlib import Path
from autoresearch.scan.relative_buy import build_decision
for d in ("20260915-0915_2242", "20260917-0917_2152"):
    doc = build_decision(Path(f"reports_claude/scan/{d}/trace/staging"), pool="composite", exclude_pinned=True, mode="active")
    print(d, doc["veto_accounting"]["by_gate"])
EOF
```
Expected: `data_a` 计数 09-17 从 11 降到 2（002444/600150）；**09-15 仍为 6**（2026-09-25 实测更正：该日 `failed_data` 还含一条真日级 `gate4`，全天连坐是正确行为，不是 bug）。数字不符先查 `run_health.json` 的 `failed_data` 内容再改代码。

- [ ] **Step 6: 提交**

```bash
git add autoresearch/scan/relative_buy.py tests/scan/test_relative_buy.py
git commit -m "fix(e6): per-ticker data_a — an l4_<code> slim failure no longer blocks the whole day

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: 盲卡不入账（slim 没到货的票不进终评级、事实本、账本）

**Files:**
- Modify: `autoresearch/scan/decision_finalize.py`（新增 `mark_blind_cards`、`_task_book_index`；`_dump_final_ratings` 与 `_build_decision_records` 跳过盲卡）
- Modify: `autoresearch/scan/report_sections.py:1118`（rows 建好后调用）、`:752`（`n_present` 口径）
- Modify: `autoresearch/contracts/artifacts.py`（登记 `_blind_cards.json`）
- Test: `tests/scan/test_blind_cards.py`（新）

**Interfaces:**
- Produces: `decision_finalize.mark_blind_cards(scan_dir: Path, rows: list[dict]) -> dict[str, dict]`；产物 `<scan_dir>/_blind_cards.json` = `{code: {"task_status", "slim_status", "reason": "DATA_INTEGRITY"}}`；行上 `blind_card=True`、`rating="—"`、`proposal="—"`、`target="⚠️数据不完整,未评级"`。

- [ ] **Step 1: 写失败测试 `tests/scan/test_blind_cards.py`**

```python
"""盲卡不入账(2026-09-24 §2.6-5):任务簿说 slim 没到货的票,卡再像样也不评级、不入事实本。"""
from __future__ import annotations

import json
from pathlib import Path

from autoresearch.scan.decision_finalize import (
    BLIND_CARDS_FILENAME, _dump_final_ratings, mark_blind_cards,
)


def _scan(tmp_path: Path) -> Path:
    scan = tmp_path / "2026-09-17"
    scan.mkdir()
    (scan / "_l4_tasks.json").write_text(json.dumps({"schema_version": 1, "tasks": {
        "600150": {"code": "600150", "status": "BLOCKED",
                   "artifacts": {"slim": {"status": "MISSING"}, "card": {"status": "PRESENT"}}},
        "600018": {"code": "600018", "status": "SUCCEEDED",
                   "artifacts": {"slim": {"status": "PRESENT"}, "card": {"status": "PRESENT"}}},
    }}), encoding="utf-8")
    return scan


def test_blind_card_is_marked_and_dropped_from_final_ratings(tmp_path):
    scan = _scan(tmp_path)
    rows = [{"code": "600150", "rating": "Hold", "proposal": "HOLD", "target": "—"},
            {"code": "600018", "rating": "Hold", "proposal": "HOLD", "target": "—"}]
    blind = mark_blind_cards(scan, rows)
    assert set(blind) == {"600150"}
    assert rows[0]["blind_card"] is True and rows[0]["rating"] == "—" and rows[0]["proposal"] == "—"
    assert rows[0]["target"] == "⚠️数据不完整,未评级"
    assert "blind_card" not in rows[1]
    doc = json.loads((scan / BLIND_CARDS_FILENAME).read_text(encoding="utf-8"))
    assert doc["600150"] == {"task_status": "BLOCKED", "slim_status": "MISSING", "reason": "DATA_INTEGRITY"}
    _dump_final_ratings(scan, rows)
    final = json.loads((scan / "_final_ratings.json").read_text(encoding="utf-8"))
    assert final == {"600018": "Hold"}


def test_no_task_book_means_no_blind_verdict(tmp_path):
    scan = tmp_path / "2026-09-17"
    scan.mkdir()
    rows = [{"code": "600150", "rating": "Hold", "proposal": "HOLD", "target": "—"}]
    assert mark_blind_cards(scan, rows) == {}
    assert rows[0]["rating"] == "Hold" and not (scan / BLIND_CARDS_FILENAME).exists()
```

- [ ] **Step 2: 跑红**

Run: `uv run --no-sync python -m pytest -q tests/scan/test_blind_cards.py`
Expected: ImportError（`mark_blind_cards` 不存在）。

- [ ] **Step 3: 实现（`decision_finalize.py`）**

在 `_dump_final_ratings` 上方加：

```python
BLIND_CARDS_FILENAME = "_blind_cards.json"


def _task_book_index(scan_dir: Path) -> dict[str, dict]:
    """`_l4_tasks.json` → {code6: task}。缺文件/坏文件 → {}(presence-gated:没有任务簿就没有盲卡判定)。"""
    path = Path(scan_dir) / "_l4_tasks.json"
    if not path.exists():
        return {}
    try:
        book = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 坏任务簿按「没有任务簿」处理,不猜
        return {}
    tasks = book.get("tasks") if isinstance(book, dict) else None
    if not isinstance(tasks, dict):
        return {}
    return {str(code).zfill(6): task for code, task in tasks.items() if isinstance(task, dict)}


def mark_blind_cards(scan_dir: Path, rows: list[dict]) -> dict[str, dict]:
    """盲卡(2026-09-24 §2.6-5)= 任务簿说 slim 没到货(status≠SUCCEEDED 或 artifacts.slim≠PRESENT)。

    确定性层过滤,不依赖 agent 守规矩(09-17 中国船舶「slim/deep 全缺无法验证」仍出了 Hold 卡入账)。
    行上打 `blind_card=True`,评级/提案改「—」,目标列写明原因;落 `_blind_cards.json`;
    `_dump_final_ratings` / `_build_decision_records` 据此跳过。返回 {code: 记录}。
    """
    tasks = _task_book_index(scan_dir)
    blind: dict[str, dict] = {}
    for r in rows:
        code = str(r.get("code", "")).zfill(6)
        task = tasks.get(code)
        if not task:
            continue
        status = str(task.get("status") or "")
        slim = str(((task.get("artifacts") or {}).get("slim") or {}).get("status") or "")
        if status == "SUCCEEDED" and slim == "PRESENT":
            continue
        r["blind_card"] = True
        r["rating"], r["proposal"] = "—", "—"
        r["target"] = "⚠️数据不完整,未评级"
        blind[code] = {"task_status": status, "slim_status": slim, "reason": "DATA_INTEGRITY"}
    if blind:
        with contextlib.suppress(Exception):
            (Path(scan_dir) / BLIND_CARDS_FILENAME).write_text(
                json.dumps(blind, ensure_ascii=False, indent=1), encoding="utf-8")
    return blind
```

`_dump_final_ratings` 里的字典推导改为：

```python
        out = {str(r.get("code", "")).zfill(6): r.get("rating", "—")
               for r in rows if r.get("code") and not r.get("blind_card")}
```

`_build_decision_records` 的 `for row in rows:` 循环第一行加：

```python
        if row.get("blind_card"):
            continue                      # 盲卡不进事实本(2026-09-24 §2.6-5)
```

确认文件顶部有 `import contextlib`、`import json`（`dump_dissent_records` 已用到 contextlib 与 json）。

- [ ] **Step 4: 接线 `report_sections.build_summary`**

在 `rows = [_finalist_row(scan_dir, fr) for fr in finals]`（约 :1118）之后紧接一行：

```python
    from autoresearch.scan.decision_finalize import mark_blind_cards
    mark_blind_cards(scan_dir, rows)          # 盲卡先标,再走 verify/ensemble/终评级落盘
```

`:752` 的 `n_present` 改为：

```python
    n_present = sum(1 for r in rows
                    if r.get("target") not in ("⚠️卡片缺失", "⚠️数据不完整,未评级"))
```

- [ ] **Step 5: 登记产物**

`contracts/artifacts.py` 的 `ARTIFACTS` 里，紧接 `Artifact("final_ratings", "_final_ratings.json", ...)` 那行之后加：

```python
    Artifact("blind_cards", "_blind_cards.json", "staging", "l5", "assemble", "json", "gated",
             required_when="任务簿有 status≠SUCCEEDED 或 slim≠PRESENT 的票"),
```

- [ ] **Step 6: 跑绿**

Run: `uv run --no-sync python -m pytest -q tests/scan/test_blind_cards.py tests/scan/test_assemble_watchlist_menu.py tests/contracts -q`
Expected: 全绿（contracts 目录若有「登记表 ↔ 白名单」不变量测试也应绿）。

- [ ] **Step 7: 提交**

```bash
git add autoresearch/scan/decision_finalize.py autoresearch/scan/report_sections.py autoresearch/contracts/artifacts.py tests/scan/test_blind_cards.py
git commit -m "fix(scan): blind cards (slim never landed) are marked, unrated and kept out of ratings/records

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: brief「昨日 delta」改跨 run 读者

**Files:**
- Modify: `autoresearch/scan/brief.py:321-330`（`collect_facts` 的 churn 段）+ 新增 `_prev_published`、`_is_live_scan`
- Modify: `autoresearch/scan/brief.py:98-112`（`_WHITELIST_SPEC`：manifest 读点）
- Test: `tests/scan/test_brief.py`

**Interfaces:**
- Produces: `_prev_published(date: str) -> tuple[str | None, dict[str, str], set[str]]`（上一场已发布 run 的数据日、终评级、finalist 码集）。

- [ ] **Step 1: 写失败测试（追加到 `tests/scan/test_brief.py`）**

```python
def test_delta_line_reads_previous_published_run_under_run_partition(tmp_path, monkeypatch, scan):
    """run 分区下 scan.parent 只装本场日期 → 改从已发布 run 目录找上一场(2026-09-24 批 0)。"""
    from autoresearch.common import workspace as ws
    from autoresearch.scan import brief as B
    reports = tmp_path / "reports" / "scan"
    prev = reports / "20260916-0916_2200"
    (prev / "trace" / "staging").mkdir(parents=True)
    (prev / "manifest.json").write_text(json.dumps({"analysis_date": "2026-09-16"}), encoding="utf-8")
    (prev / "trace" / "staging" / "_final_ratings.json").write_text(
        json.dumps({"600000": "Hold"}), encoding="utf-8")
    (prev / "trace" / "staging" / "finalists.csv").write_text("code,name\n600000,甲\n", encoding="utf-8")
    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path / "reports")
    live = tmp_path / "context" / "scan_runs" / "r1" / "staging" / scan.name
    shutil.copytree(scan, live)
    (live / "_final_ratings.json").write_text(json.dumps({"600000": "Underweight"}), encoding="utf-8")
    text = B.render(live)               # 现有 render 入口名以文件内既有测试为准
    assert "vs 2026-09-16" in text
    assert "600000 Hold→Underweight" in text
```

`scan` 是 `test_brief.py` 既有的合成 fixture；若它的 finalists 里没有 600000，先把它加进 fixture 或改用 fixture 里真实存在的一个码。`B.render` 用文件内既有测试调用的渲染入口名（grep `def test_` 里怎么拿到 brief 文本，照抄）。顶部补 `import shutil`、`import json`。

- [ ] **Step 2: 跑红**

Run: `uv run --no-sync python -m pytest -q tests/scan/test_brief.py -k previous_published_run`
Expected: FAIL（印「—(无上一扫描日)」）。

- [ ] **Step 3: 实现（`brief.py`）**

在 `_why_no_buy` 附近加：

```python
def _is_live_scan(scan: Path) -> bool:
    """只有生产现场(run 分区 staging 或活着的 scan_root 子目录)才去翻已发布 run;
    测试的 tmp 目录不是,行为逐字不变(也不会被开发机上的真实 run 污染)。"""
    try:
        return "scan_runs" in scan.resolve().parts or scan.parent.resolve() == ws.scan_root().resolve()
    except OSError:
        return False


def _prev_published(date: str) -> tuple[str | None, dict[str, str], set[str]]:
    """上一场**已发布** run(数据日 < date)的终评级与 finalist 码集。

    run 分区后 `run_health.churn` 恒 None(09-09→09-17 六场全印「无上一扫描日」),因为
    `health.finalist_churn` 只看 `scan_dir.parent`,那里只有本场自己的日期。改从
    `reports_<engine>/scan/` 的已发布 run 里找(`outcome.published_runs`)。找不到 → (None, {}, set())。
    """
    try:
        from autoresearch.scan.outcome import published_runs
        runs = published_runs()
    except Exception:  # noqa: BLE001 — 跨 run 读者是可选层,坏了只影响 ⑥ 那一行
        return None, {}, set()
    best: tuple[str, Path] | None = None
    for run in runs:
        manifest = _json(run / "manifest.json") or {}
        d = str(manifest.get("analysis_date") or "")
        if d and d < date and (best is None or d > best[0]):
            best = (d, run)
    if best is None:
        return None, {}, set()
    staging = best[1] / "trace" / "staging"
    ratings = _json(staging / "_final_ratings.json") or {}
    codes = {_code6(r.get("code")) for r in _rows(staging / "finalists.csv") if r.get("code")}
    return best[0], {_code6(k): v for k, v in ratings.items()}, codes
```

`collect_facts` 的 churn 段替换为：

```python
    churn = health.get("churn") or {}
    prev_date = churn.get("prev_date")
    prev_by_code: dict[str, str] = {}
    n_repeat, n_today = churn.get("n_repeat"), churn.get("n_today")
    if prev_date:
        prev = _json(root / str(prev_date) / "_final_ratings.json") or {}
        prev_by_code = {_code6(k): v for k, v in prev.items()}
    elif _is_live_scan(scan):
        prev_date, prev_by_code, prev_codes = _prev_published(date)
        if prev_date:
            today_codes = {_code6(r.get("code")) for r in finals if r.get("code")}
            n_repeat, n_today = len(prev_codes & today_codes), len(today_codes)
    changes: list[dict] = []
    for code in sorted(set(prev_by_code) & set(rating_by_code)):
        if prev_by_code[code] != rating_by_code[code]:
            changes.append({"code": code, "from": prev_by_code[code], "to": rating_by_code[code]})
```

并把返回字典里 `"delta"` 改为 `{"prev_date": prev_date, "n_repeat": n_repeat, "n_today": n_today, "changes": changes}`。确认 `brief.py` 已 `from autoresearch.common import workspace as ws`（没有就加）。

- [ ] **Step 4: 白名单**

`_WHITELIST_SPEC` 追加一行。先 `grep -n '"manifest.json"' autoresearch/contracts/artifacts.py`：若已登记（有 `Artifact("<name>", "manifest.json", ...)`）就写 `("artifact", "<name>")`，否则写 `("literal", "manifest.json")`。

- [ ] **Step 5: 跑绿**

Run: `uv run --no-sync python -m pytest -q tests/scan/test_brief.py tests/scan/test_self_review_brief.py`
Expected: 全绿（含 `test_whitelist_covers_every_file_read` / `test_whitelist_has_no_dead_entry`；若 dead-entry 测试要求 fixture 里真读到 manifest，把 Step 1 的已发布 run 夹具搬进共享 fixture）。

- [ ] **Step 6: 提交**

```bash
git add autoresearch/scan/brief.py tests/scan/test_brief.py
git commit -m "fix(brief): yesterday-delta reads the previous published run (run partition broke churn)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: BUY 行的「绝对 gap」与「证据」句改读账本真身

**Files:**
- Modify: `autoresearch/scan/brief.py:455-535`（`_buy_lines`）、`:560-568`（删 `_abs_gap_text`）、`collect_facts`（加 `e6_realized`）、`_WHITELIST_SPEC`（加账本）
- Test: `tests/scan/test_brief.py`

**Interfaces:**
- Produces: `_e6_realized_stats(reports_root: Path | None = None) -> dict` = `{"buy": {"n", "mean_pp", "win"}, "seat": {"n", "mean_pp", "win"}}`（n<20 时 `mean_pp`/`win` 为 None）；`_realized_text(stat: dict, label: str) -> str`。

- [ ] **Step 1: 写失败测试**

```python
def _ledger_csv(root: Path, rows: list[dict]) -> None:
    import csv
    p = root / "scan" / "_ledger" / "recommendations.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    cols = ["run_id", "analysis_date", "mode", "role", "e6_buy", "outcome_status",
            "actionability", "gap_c1_o2"]
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def test_buy_line_reports_realized_ledger_stats_not_a_stub(tmp_path, monkeypatch, scan_with_buy):
    """`expected_abs_gap` 是写死的 UNMEASURED stub;BUY 行改印账本 e6_buy 行的实测(n<20 只给 n)。"""
    from autoresearch.common import workspace as ws
    from autoresearch.scan import brief as B
    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path / "reports")
    _ledger_csv(tmp_path / "reports", [
        {"run_id": f"r{i}", "analysis_date": "2026-09-0%d" % (i + 1), "mode": "active", "role": "BUY",
         "e6_buy": "True", "outcome_status": "MATURE", "actionability": "ACTIONABLE",
         "gap_c1_o2": "-0.005"} for i in range(7)])
    text = B.render(scan_with_buy)
    assert "账本 BUY 实测 n=7,不足 20 不给区间" in text
    assert "样本不足禁止拍数" not in text
    assert "+0.14/+0.17pp" not in text
```

`scan_with_buy` = 文件内既有的「有 BUY 的合成 scan」fixture（grep `buys` 的 fixture 名照抄；没有就基于 `scan` 复制一份并写入带 `buys=[{"code": ...}]` 的 `_relative_buy_decision.json`）。

- [ ] **Step 2: 跑红**

Run: `uv run --no-sync python -m pytest -q tests/scan/test_brief.py -k realized_ledger_stats`
Expected: FAIL。

- [ ] **Step 3: 实现**

`brief.py` 加：

```python
_REALIZED_MIN_N = 20


def _e6_realized_stats(reports_root: Path | None = None) -> dict:
    """账本 `recommendations.csv` 里 E6 的**实测**:BUY 行(mode=active ∧ e6_buy)与席位行
    (role=composite_seat),都限 MATURE ∧ ACTIONABLE。n<20 只报 n(账本自己的 `ledger_line`
    纪律同款);n≥20 报均值 pp 与胜率。账本缺 → n=0。"""
    try:
        from autoresearch.scan.outcome import load_ledger
        rows = load_ledger(reports_root)
    except Exception:  # noqa: BLE001 — 账本是可选层
        rows = []

    def _stat(pred) -> dict:
        vals = []
        for r in rows:
            if not pred(r):
                continue
            if str(r.get("outcome_status")) != "MATURE" or str(r.get("actionability")) != "ACTIONABLE":
                continue
            try:
                vals.append(float(r.get("gap_c1_o2")))
            except (TypeError, ValueError):
                continue
        n = len(vals)
        if n < _REALIZED_MIN_N:
            return {"n": n, "mean_pp": None, "win": None}
        return {"n": n, "mean_pp": round(100 * sum(vals) / n, 2),
                "win": round(sum(1 for v in vals if v > 0) / n, 2)}

    return {
        "buy": _stat(lambda r: str(r.get("e6_buy")).lower() == "true" and str(r.get("mode")) == "active"),
        "seat": _stat(lambda r: str(r.get("role")) == "composite_seat"),
    }


def _realized_text(stat: dict, label: str) -> str:
    """n<20:只给 n;为负 → 固定 `弱市相对最优`(语义纪律②)。"""
    if stat.get("mean_pp") is None:
        return f"账本 {label} 实测 n={stat.get('n', 0)},不足 {_REALIZED_MIN_N} 不给区间"
    body = f"账本 {label} 实测 {stat['mean_pp']:+.2f}pp(n={stat['n']},胜率 {stat['win']:.0%},未扣成本)"
    if stat["mean_pp"] < 0:
        body += f" → **{WEAK_MARKET_PHRASE}**(相对 BUY 从不承诺绝对收益为正)"
    return body
```

`collect_facts` 返回字典加 `"e6_realized": _e6_realized_stats(),`。`_buy_lines` 里：`gap_txt = _abs_gap_text(rel)` 改为 `gap_txt = _realized_text(facts["e6_realized"]["buy"], "BUY")`；`text` 里 `f" · 绝对 gap {gap_txt}"` 改为 `f" · {gap_txt}"`；`_src(... "relative.abs_gap_status" ...)` 那行改为 `_src(src, "relative.realized_buy_n", facts["e6_realized"]["buy"]["n"], "recommendations.csv", "rows[e6_buy ∧ mode=active ∧ MATURE ∧ ACTIONABLE]", text)`。composite 池的 `exec_txt` 改为：

```python
        exec_txt = ("  ↳ 证据:当日 composite 分位最高的证据席 · L4 否决检查通过 · "
                    + _realized_text(facts["e6_realized"]["seat"], "席位")
                    + f" · {COMPOSITE_EXPECTATION}")
```

删除 `_abs_gap_text`。`_WHITELIST_SPEC` 加 `("artifact", "recommendations")`。

- [ ] **Step 4: 跑绿**

Run: `uv run --no-sync python -m pytest -q tests/scan/test_brief.py tests/scan/test_self_review_brief.py`
Expected: 全绿。若 `test_whitelist_has_no_dead_entry` 要求 fixture 真读到账本，在共享 fixture 里用 Step 1 的 `_ledger_csv` 写一份并 monkeypatch `ws.reports_root`。

- [ ] **Step 5: 提交**

```bash
git add autoresearch/scan/brief.py tests/scan/test_brief.py
git commit -m "fix(brief): BUY line prints realized ledger stats instead of the UNMEASURED stub and the fixed evidence template

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: intel 超 cap 20 的确定性裁剪 + lint 识别「已裁」

**Files:**
- Modify: `autoresearch/scan/l4/intel_guard.py`（`guard_intel` 加 `soft_cap`；新增 `configured_soft_cap`、`_mark_declaration`）
- Modify: `autoresearch/session_agent/domain_ops.py:1843`、`autoresearch/scan/l4/intel_status.py:425`（传 `soft_cap`）
- Modify: `autoresearch/scan/self_review.py`（`intel_query_cap_lint` 跳过已裁稿）
- Test: `tests/scan/test_intel_guard.py`（若已存在则追加；否则新建）

**Interfaces:**
- Produces: `guard_intel(scan_dir, code, *, hard_cap=30, soft_cap: int | None = None) -> dict`；`soft_cap` 下超限稿 `action="TRIMMED"`、声明行追加 `〔已裁·cap N〕`；`configured_soft_cap() -> int`（读 `l4_intel.max_queries`，内建 20）。

- [ ] **Step 1: 写失败测试**

```python
def _intel_doc(n_events: int, claimed: int) -> str:
    rows = "\n".join(f"| 2026-09-0{i % 9 + 1} | 背景 | 事件{i} | [x](https://x) | 0.0 |" for i in range(n_events))
    return ("# 活体情报 — 600018 上港集团 @ 2026-09-17\n\n## 事件段\n| 日期 | 时效窗 | 事件 | 源 | 净分 |\n|---|---|---|---|---|\n"
            + rows + f"\n\n## 声明行\n网查 {claimed} 条 ｜ T0面=无增量\n")


def test_soft_cap_trims_and_marks_declaration(tmp_path):
    from autoresearch.scan.l4.intel_guard import guard_intel, intel_path
    scan = tmp_path / "2026-09-17"; scan.mkdir()
    intel_path(scan, "600018").write_text(_intel_doc(14, 25), encoding="utf-8")
    res = guard_intel(scan, "600018", soft_cap=20)
    assert res["action"] == "TRIMMED" and res["soft_cap"] == 20 and res["dropped_rows"] == 4
    text = intel_path(scan, "600018").read_text(encoding="utf-8")
    assert "网查 25 条〔已裁·cap 20〕" in text
    assert (scan / "_l4_intel_600018.pretrim").exists()


def test_under_soft_cap_is_kept_untouched(tmp_path):
    from autoresearch.scan.l4.intel_guard import guard_intel, intel_path
    scan = tmp_path / "2026-09-17"; scan.mkdir()
    intel_path(scan, "600018").write_text(_intel_doc(5, 18), encoding="utf-8")
    assert guard_intel(scan, "600018", soft_cap=20)["action"] == "KEPT"


def test_query_cap_lint_skips_trimmed_reports(tmp_path):
    from autoresearch.scan.self_review import intel_query_cap_lint
    scan = tmp_path / "2026-09-17"; scan.mkdir()
    (scan / "_l4_intel_600018.md").write_text(_intel_doc(5, 25).replace("网查 25 条", "网查 25 条〔已裁·cap 20〕"), encoding="utf-8")
    (scan / "_l4_intel_600035.md").write_text(_intel_doc(5, 25), encoding="utf-8")
    hits = intel_query_cap_lint(scan, cap=20, web_budget_path=None)
    assert [h["code"] for h in hits if h.get("claimed")] == ["600035"]
```

第三个测试要求 `web_budget_path=None` 时走自报路；若 `_default_web_budget_path(scan)` 在 tmp 下返回非 None 让它走真值路，改传一个不存在的路径并断言 `kind=="unmeasured"` 之外仍有 600035 的自报条目（按函数现有分支写）。

- [ ] **Step 2: 跑红**

Run: `uv run --no-sync python -m pytest -q tests/scan/test_intel_guard.py -k "soft_cap or trimmed_reports"`
Expected: 前两个 FAIL（`soft_cap` 未知参数），第三个 FAIL（600018 也被报）。

- [ ] **Step 3: 实现 `intel_guard.py`**

模块常量与 helper（放在 `HARD_CAP_DEFAULT` 旁）：

```python
SOFT_TRIM_KEEP = 10
_TRIM_MARK = "〔已裁·cap {cap}〕"


def configured_soft_cap() -> int:
    """`l4_intel.max_queries`(scan_config 单源;缺 → 内建 20)。"""
    from autoresearch.scan.user_config import knob
    return int(knob("l4_intel", "max_queries", None, 20))


def _mark_declaration(text: str, claimed: int, cap: int) -> str:
    """声明行「网查 N 条」后追加已裁标记(一次),让 self_review 的限频 lint 认出确定性层已经动过手。"""
    mark = _TRIM_MARK.format(cap=cap)
    if mark in text:
        return text
    return re.sub(rf"(网查\s*{claimed}\s*条)", rf"\1{mark}", text, count=1)
```

`guard_intel` 签名加 `soft_cap: int | None = None`；在 `claimed = claimed_queries(text)` 之后、`if claimed > hard_cap:` 之前插入：

```python
    if soft_cap is not None and claimed > soft_cap and claimed <= hard_cap and _event_rows(text):
        # 软顶(2026-09-24 批 0):cap 20 此前是指令级、无强制力(pr_20260714_007),每场 4–11 稿
        # 自报 21–55 条。确定性层按时效裁到 ≤SOFT_TRIM_KEEP 事件行并在声明行留痕;不拒稿不拒票。
        trimmed, cut = trim_by_recency(text, keep=SOFT_TRIM_KEEP)
        trimmed = _mark_declaration(trimmed, claimed, soft_cap)
        pretrim_as = None
        if cut:
            pre = src.with_name(f"_l4_intel_{code}.pretrim")
            pre.write_text(text, encoding="utf-8")
            pretrim_as = pre.name
        src.write_text(trimmed, encoding="utf-8")
        claims_lint = _apply_claims_lint(src, trimmed, self_code=code, trade_date=trade_date)
        return {"ok": True, "code": code, "action": "TRIMMED", "claimed": claimed,
                "soft_cap": soft_cap, "hard_cap": hard_cap, "dropped_rows": cut,
                "pretrim_as": pretrim_as, "claims_lint": claims_lint,
                "claim_events": _extract_claim_events(src, trimmed, self_code=code, trade_date=trade_date)}
```

确认 `intel_guard.py` 顶部有 `import re`（已有 `_CLAIM_RE`，应已导入）。

- [ ] **Step 4: 两个调用点传软顶**

`session_agent/domain_ops.py:1843`：`result = guard_intel(scan_dir, code6)` → `result = guard_intel(scan_dir, code6, soft_cap=configured_soft_cap())`（同文件 import 处加 `configured_soft_cap`）。`scan/l4/intel_status.py:425` 同样改。

- [ ] **Step 5: lint 识别已裁**

`self_review.intel_query_cap_lint` 的自报循环里，`m = re.search(r"网查\s*(\d+)\s*条", text)` 之后：

```python
        if m is not None and "〔已裁·cap" in text:
            claimed_all.append(int(m.group(1)))   # 计入总数,但不再当指令级违规上报
            continue
```

- [ ] **Step 6: 跑绿**

Run: `uv run --no-sync python -m pytest -q tests/scan/test_intel_guard.py tests/scan/test_self_review_brief.py tests/scan -k "intel" `
Expected: 全绿。

- [ ] **Step 7: 提交**

```bash
git add autoresearch/scan/l4/intel_guard.py autoresearch/scan/l4/intel_status.py autoresearch/session_agent/domain_ops.py autoresearch/scan/self_review.py tests/scan/test_intel_guard.py
git commit -m "feat(intel): deterministic soft-cap trim at l4_intel.max_queries; lint recognizes trimmed reports

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 8: 批 0 收尾**

Run: `uv run --no-sync python -m pytest -q tests/ && uv run --no-sync ruff check autoresearch tests`
Expected: 全绿、ruff 0。

---

# 批 1 · 召回权重换成偏好档（裁定②；回滚杆 `funnel.weight_profile="calibrated"`）

### Task 6: 配置三件套 `funnel.weight_profile` / `funnel.preference_weights`

**Files:**
- Modify: `autoresearch/scan/user_config.py:106-110`（`_SUB_WHITELIST["funnel"]`）、`:141-165`（`_KNOB_TYPES` + 类型函数）
- Test: `tests/scan/test_config_knobs.py`

**Interfaces:**
- Produces: 两个键通过 `load_user_config` 校验；`preference_weights` 必须恰含 `scoring._GROUPS` 十键、值为有限数。

- [ ] **Step 1: 写失败测试（追加）**

```python
def test_weight_profile_keys_whitelisted(tmp_path):
    raw = {"funnel": {"weight_profile": "preference", "preference_weights": {
        "momentum": 0.20, "tech": 0.15, "volprice": 0.15, "fund_main": 0.15, "chip": 0.05,
        "north": 0.05, "growth": 0.05, "value": 0.05, "fund_retail": -0.05, "rz": 0.0}}}
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps(raw), encoding="utf-8")
    assert load_user_config(p) == raw


@pytest.mark.parametrize("bad", ["prefer", "", 1, None])
def test_weight_profile_must_be_calibrated_or_preference(tmp_path, bad):
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"funnel": {"weight_profile": bad}}), encoding="utf-8")
    with pytest.raises(ValueError, match="weight_profile"):
        load_user_config(p)


@pytest.mark.parametrize("pw", [
    {"momentum": 0.2},                                   # 缺键
    {"momentum": 0.2, "tech": 0.1, "volprice": 0.1, "fund_main": 0.1, "chip": 0.0, "north": 0.0,
     "growth": 0.0, "value": 0.0, "fund_retail": 0.0, "rz": 0.0, "extra": 1.0},   # 多键
    {"momentum": math.nan, "tech": 0.1, "volprice": 0.1, "fund_main": 0.1, "chip": 0.0, "north": 0.0,
     "growth": 0.0, "value": 0.0, "fund_retail": 0.0, "rz": 0.0},                 # 非有限
])
def test_preference_weights_must_be_exactly_the_ten_groups(tmp_path, pw):
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"funnel": {"preference_weights": pw}}), encoding="utf-8")
    with pytest.raises(ValueError, match="preference_weights"):
        load_user_config(p)
```

- [ ] **Step 2: 跑红**

Run: `uv run --no-sync python -m pytest -q tests/scan/test_config_knobs.py -k "weight_profile or preference_weights"`
Expected: 第一个 FAIL（未知子键 raise）。

- [ ] **Step 3: 实现**

`_SUB_WHITELIST["funnel"]` 集合加 `"weight_profile", "preference_weights"`。类型函数区加：

```python
def _t_profile(v): return v in {"calibrated", "preference"}


def _t_pref_weights(v):
    from autoresearch.common.scoring import _GROUPS
    if not isinstance(v, dict) or set(v) != set(_GROUPS):
        return False
    return all(_t_num(x) and math.isfinite(x) for x in v.values())
```

`_KNOB_TYPES` 加：

```python
    # 召回权重档(2026-09-24 §2.1):"calibrated"=读 weights.json(旧行为,回滚杆)/
    # "preference"=固定偏好档,十组权重就在下面这个键里(唯一事实源,无自动重标定)。
    ("funnel", "weight_profile"): (_t_profile, "calibrated|preference"),
    ("funnel", "preference_weights"): (_t_pref_weights, "object:恰含 scoring._GROUPS 十键的有限数"),
```

`validate_user_config` 报错信息用的是 `f"{blk}.{key}"`（既有格式），`match="weight_profile"` / `match="preference_weights"` 自然命中。

- [ ] **Step 4: 跑绿并提交**

Run: `uv run --no-sync python -m pytest -q tests/scan/test_config_knobs.py`

```bash
git add autoresearch/scan/user_config.py tests/scan/test_config_knobs.py
git commit -m "feat(config): funnel.weight_profile / preference_weights knobs (whitelist + types)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: `scoring.combine_group_scores` 抽取 + `preference_weights_doc` + `resolve_weights`

**Files:**
- Modify: `autoresearch/common/scoring.py`（`composite_score` 拆成 `_factor_groups` + `combine_group_scores`；新增三个函数与两个常量）
- Test: `tests/common/test_scoring.py`

**Interfaces:**
- Produces:
  - `combine_group_scores(df: pd.DataFrame, groups: dict[str, pd.Series], weights: dict) -> pd.Series`（0–100 的 composite，含过热惩罚与吸筹加成；`composite_score` 改调它）
  - `PREFERENCE_PROFILE = "preference"`、`CALIBRATED_PROFILE = "calibrated"`
  - `preference_weights_doc(pw: dict[str, float]) -> dict`（`{"meta": {...}, "weights": {"__global__": {...}}}`）
  - `resolve_weights(frame, *, profile: str, preference_weights: dict | None, regime_aware: bool, path: str | None = None) -> tuple[dict, str | None]`

- [ ] **Step 1: 写失败测试（追加到 `tests/common/test_scoring.py`）**

```python
def test_combine_group_scores_is_what_composite_score_uses():
    """抽取后 composite_score 必须逐值等于 combine_group_scores(_factor_groups(df))(变异探针:改任一权重必须变红)。"""
    from autoresearch.common.scoring import combine_group_scores
    df = _synthetic(150)
    w = _PRIOR_WEIGHTS
    a = composite_score(df, w)["composite"]
    b = combine_group_scores(df, _factor_groups(df), w).clip(lower=0, upper=100).round(1)
    pd.testing.assert_series_equal(a, b, check_names=False)
    w2 = {"meta": {}, "weights": {"__global__": {**w["weights"]["__global__"], "momentum": -0.10}}}
    assert not composite_score(df, w2)["composite"].equals(a)


def test_preference_weights_doc_shape_and_validation():
    from autoresearch.common.scoring import preference_weights_doc
    pw = dict.fromkeys(_GROUPS, 0.0); pw["momentum"] = 0.2
    doc = preference_weights_doc(pw)
    assert doc["weights"]["__global__"] == pw
    assert doc["meta"]["profile"] == "preference" and doc["meta"]["regime_applied"] is None
    assert doc["meta"]["source"] == "profile:preference" and len(doc["meta"]["config_sha256"]) == 64
    with pytest.raises(ValueError):
        preference_weights_doc({"momentum": 0.2})


def test_resolve_weights_preference_ignores_regime_and_file(tmp_path):
    from autoresearch.common.scoring import resolve_weights
    pw = dict.fromkeys(_GROUPS, 0.0); pw["value"] = 0.3
    doc, regime = resolve_weights(_synthetic(50), profile="preference", preference_weights=pw,
                                  regime_aware=True, path=str(tmp_path / "absent.json"))
    assert regime is None and doc["meta"]["profile"] == "preference"


def test_resolve_weights_calibrated_delegates_to_pick_weights(tmp_path):
    from autoresearch.common.scoring import resolve_weights
    doc, regime = resolve_weights(_synthetic(50), profile="calibrated", preference_weights=None,
                                  regime_aware=False, path=str(tmp_path / "absent.json"))
    assert doc["meta"]["source"].startswith("prior") and regime is None
    with pytest.raises(ValueError):
        resolve_weights(_synthetic(50), profile="bogus", preference_weights=None, regime_aware=False)
```

顶部确认 `import pytest`。

- [ ] **Step 2: 跑红**

Run: `uv run --no-sync python -m pytest -q tests/common/test_scoring.py -k "combine_group or preference_weights_doc or resolve_weights"`
Expected: ImportError。

- [ ] **Step 3: 实现（`scoring.py`）**

把 `composite_score` 里从 `wmap = weights.get("weights", {})` 到 `comp100 = comp100 + accum...*5` 的整段搬进新函数，`composite_score` 只留三行：

```python
def combine_group_scores(df: pd.DataFrame, groups: dict[str, pd.Series], weights: dict) -> pd.Series:
    """十组分位 → 0–100 composite(Σ(组分位−0.5)×行业权重 / Σ|w| → 50±50;过热 −8;吸筹 +5)。

    2026-09-24 从 `composite_score` 抽出:`research/menu_replay` 用已落盘的 `score_<group>` 列
    重算 composite 时必须与生产**同一段数学**,不允许第二套实现(两套迟早给出两个数)。
    返回未截断、未取整的 Series;`composite_score` 负责 clip/round 与 `score_*` 列。
    """
    wmap = weights.get("weights", {})
    glob = wmap.get("__global__", {})
    ind = df["industry"] if "industry" in df.columns else pd.Series("", index=df.index)
    comp = pd.Series(0.0, index=df.index)
    wabs = pd.Series(0.0, index=df.index)
    for name, s in groups.items():
        w = ind.map(lambda x, n=name: float(wmap.get(x, {}).get(n, glob.get(n, 0.0))))
        comp += (s - 0.5).fillna(0.0) * w
        wabs += s.notna().astype(float) * w.abs()
    raw = comp / wabs.replace(0, np.nan)
    comp100 = 50 + 50 * raw.clip(-1, 1)
    if "pct_60d" in df.columns:                      # 过热抑制(原文照搬)
        high_mom = _pct(df["pct_60d"]) > 0.90
        exhausted = pd.Series(False, index=df.index)
        if "rsi6" in df.columns:
            exhausted = exhausted | (_num(df["rsi6"]) > 80)
        if "winner_rate" in df.columns:
            exhausted = exhausted | (_num(df["winner_rate"]) > 85)
        comp100 = comp100 - (high_mom & exhausted).fillna(False).astype(float) * 8
    if "vol_ratio" in df.columns:                    # 吸筹加成(原文照搬)
        low_pos = pd.Series(False, index=df.index)
        if "winner_rate" in df.columns:
            low_pos = low_pos | (_num(df["winner_rate"]) < 40)
        if "price_to_cost" in df.columns:
            low_pos = low_pos | (_num(df["price_to_cost"]) < 1.0)
        not_high = (_num(df["pct_60d"]) < 20) if "pct_60d" in df.columns else pd.Series(True, index=df.index)
        main_ok = (_num(df["main_net_ratio"]) >= 0) if "main_net_ratio" in df.columns else pd.Series(True, index=df.index)
        accum = (_num(df["vol_ratio"]) >= 1.5) & low_pos & not_high & main_ok
        comp100 = comp100 + accum.fillna(False).astype(float) * 5
    return comp100


def composite_score(df: pd.DataFrame, weights: dict) -> pd.DataFrame:
    """行业条件化复合分(docstring 原文保留)。"""
    groups = _factor_groups(df)
    out = df.copy()
    for name, s in groups.items():
        out[f"score_{name}"] = (s * 100).round(1)
    out["composite"] = combine_group_scores(df, groups, weights).clip(lower=0, upper=100).round(1)
    return out
```

注意原实现里 `score_*` 列在循环中写、`comp` 与 `wabs` 用同一个循环——拆开后数学不变（两个循环遍历同一 `groups`）。再加：

```python
PREFERENCE_PROFILE = "preference"
CALIBRATED_PROFILE = "calibrated"


def preference_weights_doc(pw: dict[str, float]) -> dict:
    """偏好档权重 → 与 weights.json 同形的权重文档(仅 `__global__`,无行业覆盖、无 regime 块)。

    2026-09-24 §2.1:符号 = 产品偏好,量级 = 裁定,**没有自动重标定**。键集必须恰为 `_GROUPS`。
    `meta.config_sha256` 让 `weights_used.json` 能证明当天用的是哪一份数。
    """
    import hashlib
    import json as _json
    import math as _math
    missing = [g for g in _GROUPS if g not in pw]
    extra = [k for k in pw if k not in _GROUPS]
    if missing or extra:
        raise ValueError(f"preference_weights 键集须恰为 {_GROUPS}:缺 {missing} 多 {extra}")
    vals = {g: float(pw[g]) for g in _GROUPS}
    if any(not _math.isfinite(v) for v in vals.values()):
        raise ValueError("preference_weights 含非有限值")
    digest = hashlib.sha256(_json.dumps(vals, sort_keys=True).encode("utf-8")).hexdigest()
    return {"meta": {"source": f"profile:{PREFERENCE_PROFILE}", "profile": PREFERENCE_PROFILE,
                     "regime_applied": None, "config_sha256": digest},
            "weights": {"__global__": vals}}


def resolve_weights(frame: pd.DataFrame, *, profile: str, preference_weights: dict | None,
                    regime_aware: bool, path: str | None = None) -> tuple[dict, str | None]:
    """L1 权重的**唯一**入口(2026-09-24 §2.1):preference → 固定档、不看 regime、不读文件;
    calibrated → 原 `pick_weights`(逐字 parity)。别的值 → ValueError(错型不静默生效)。"""
    if profile == PREFERENCE_PROFILE:
        if not preference_weights:
            raise ValueError("weight_profile=preference 但 funnel.preference_weights 缺失")
        return preference_weights_doc(preference_weights), None
    if profile != CALIBRATED_PROFILE:
        raise ValueError(f"weight_profile 只接受 {CALIBRATED_PROFILE!r}/{PREFERENCE_PROFILE!r};收到 {profile!r}")
    return pick_weights(frame, regime_aware, **({"path": path} if path else {}))
```

- [ ] **Step 4: 跑绿（含既有 parity 测试）**

Run: `uv run --no-sync python -m pytest -q tests/common/test_scoring.py tests/common/test_scoring_distortion.py tests/scan/test_recall_merge.py tests/scan/test_recall_channels.py tests/scan/test_pinned.py`
Expected: 全绿。（`tests/scan/test_parity.py` 不存在——2026-07-13 commit 174ffd7 随 typed-trace 死簇一起删除；这里列的是真正在生产形状的帧上跑 `composite_score` 的守卫。）

- [ ] **Step 5: 提交**

```bash
git add autoresearch/common/scoring.py tests/common/test_scoring.py
git commit -m "feat(scoring): combine_group_scores extraction + preference weight profile + resolve_weights

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: `universe.run` 接线 + `weights_used`/`meta` 记档 + session 身份快照

**Files:**
- Modify: `autoresearch/scan/universe.py:296-350`（形参 + knob + `resolve_weights`）、`:432-440`（meta 加 `weight_profile`）
- Modify: `autoresearch/session_agent/domain_ops.py:1308-1330`（`_freeze_scan_runtime_inputs`）
- Modify: `.claude/skills/scan-market/scan_config.jsonc`（`funnel` 块加两键，生产开 preference）
- Test: `tests/scan/test_universe_weight_profile.py`（新）

**Interfaces:**
- Produces: `run(..., weight_profile: str | None = None, preference_weights: dict | None = None)`；`meta.json.weight_profile`；`weights_used.json.meta.profile`；`_session_inputs/L1_weight_profile.json`（preference 档时）。

- [ ] **Step 1: 写失败测试**

```python
"""universe.run 的权重档接线(2026-09-24 §2.1)。零网络,夹具同 test_universe_l2_cols。"""
from __future__ import annotations

import json

import pandas as pd

from tests.scan._synth_universe import synth_universe

DATE = "2026-09-17"
PW = {"momentum": 0.20, "tech": 0.15, "volprice": 0.15, "fund_main": 0.15, "chip": 0.05,
      "north": 0.05, "growth": 0.05, "value": 0.05, "fund_retail": -0.05, "rz": 0.0}


def _run(monkeypatch, tmp_path, cfg: dict):
    from autoresearch.scan import events as ev_mod, universe as U
    uni = synth_universe(n=300, seed=3)
    monkeypatch.setattr(U, "build_market_frame",
                        lambda *a, **k: (uni.copy(), {"universe_raw": len(uni), "universe": len(uni)}))
    monkeypatch.setattr(ev_mod, "market_event_counts", lambda *a, **k: pd.DataFrame({"code": []}))
    monkeypatch.setattr(U, "_luc", lambda: cfg, raising=False)
    monkeypatch.setattr("autoresearch.scan.user_config.load_user_config", lambda *a, **k: cfg)
    outdir = tmp_path / DATE
    U.run(DATE, outdir=outdir, recall_n=60, l2_n=20, recall_mode="multi",
          recall_channels=["composite", "momentum", "value"],
          weights_path=str(tmp_path / "no-such-weights.json"))
    return outdir


def test_preference_profile_is_recorded_in_meta_and_weights_used(monkeypatch, tmp_path):
    out = _run(monkeypatch, tmp_path, {"funnel": {"weight_profile": "preference", "preference_weights": PW}})
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    used = json.loads((out / "weights_used.json").read_text(encoding="utf-8"))
    assert meta["weight_profile"] == "preference" and meta["weights_source"] == "profile:preference"
    assert used["meta"]["profile"] == "preference" and used["weights"]["__global__"] == PW


def test_default_profile_is_calibrated_parity(monkeypatch, tmp_path):
    out = _run(monkeypatch, tmp_path, {})
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["weight_profile"] == "calibrated" and meta["weights_source"].startswith("prior")
```

`_luc` 的 monkeypatch 视 `universe.run` 内部怎么取配置而定：它是 `from autoresearch.scan.user_config import knob, load_user_config as _luc` 的**函数内**导入，所以只 patch `autoresearch.scan.user_config.load_user_config` 那一行即可，`U._luc` 那行可删。

- [ ] **Step 2: 跑红**

Run: `uv run --no-sync python -m pytest -q tests/scan/test_universe_weight_profile.py`
Expected: KeyError `weight_profile`。

- [ ] **Step 3: 实现**

`universe.run` 形参列表末尾加 `weight_profile: str | None = None, preference_weights: dict | None = None`；knob 区加：

```python
    weight_profile = str(knob("funnel", "weight_profile", weight_profile, "calibrated", cfg=_ucfg))
    preference_weights = knob("funnel", "preference_weights", preference_weights, None, cfg=_ucfg)
```

`weights, _regime = pick_weights(uni, regime_aware, **({"path": weights_path} if weights_path else {}))` 改为：

```python
    from autoresearch.common.scoring import resolve_weights
    weights, _regime = resolve_weights(uni, profile=weight_profile, preference_weights=preference_weights,
                                       regime_aware=regime_aware, path=weights_path)
```

`meta.json` 字典加 `"weight_profile": weight_profile,`（放 `"weights_source"` 旁）。

`domain_ops._freeze_scan_runtime_inputs` 的 `atomic_write_json(target / "manifest.json", value)` 之前加：

```python
    try:                                            # 2026-09-24 §2.1:preference 档时快照实际生效的权重文档
        cfg = user_config.load_user_config() or {}
        funnel = cfg.get("funnel") or {}
        if funnel.get("weight_profile") == "preference":
            from autoresearch.common.scoring import preference_weights_doc
            atomic_write_json(target / "L1_weight_profile.json",
                              preference_weights_doc(funnel.get("preference_weights") or {}))
            present["L1_weight_profile.json"] = True
    except Exception as exc:  # noqa: BLE001 — 身份快照失败不挡 session,但要留痕
        present["L1_weight_profile.json"] = False
        value_error = repr(exc)
```

并把 `value = {"schema_version": 1, "present": present}` 改成 `value = {"schema_version": 1, "present": present, "weight_profile_error": locals().get("value_error")}`（无错时为 None）。

- [ ] **Step 4: 生产配置**

`scan_config.jsonc` 的 `funnel` 块加（三段式旁注）：

```jsonc
    // ── 召回权重档(2026-09-24 可买性对齐 §2.1)──
    // 【生效点】scoring.resolve_weights ← universe.run(knob funnel.weight_profile/preference_weights)。
    // ⚖️ 证据:weights.json(08-19,gap_c1_o2 IC 校准)range 块 momentum/tech/volprice/fund_main 全负
    //   → composite 实为超卖分(09-17 L1 池 spearman 对 pct_20d −0.80),落刀逐级叠加(L0 23%→L2 40%)。
    //   偏好档符号 = 「上涨趋势 + 有支撑 + 主力真在 + 散户不拥挤」,量级是裁定不是拟合,无自动重标定。
    // 回滚杆 = "weight_profile": "calibrated"(一行;preference_weights 留着无害)。
    "weight_profile": "preference",
    "preference_weights": { "momentum": 0.20, "tech": 0.15, "volprice": 0.15, "fund_main": 0.15,
                            "chip": 0.05, "north": 0.05, "growth": 0.05, "value": 0.05,
                            "fund_retail": -0.05, "rz": 0.0 },
```

- [ ] **Step 5: 跑绿**

Run: `uv run --no-sync python -m pytest -q tests/scan/test_universe_weight_profile.py tests/scan/test_universe_l2_cols.py tests/scan/test_universe_stdout_counts.py tests/scan/test_config_knobs.py tests/session_agent -q`
Expected: 全绿。

- [ ] **Step 6: 提交**

```bash
git add autoresearch/scan/universe.py autoresearch/session_agent/domain_ops.py .claude/skills/scan-market/scan_config.jsonc tests/scan/test_universe_weight_profile.py
git commit -m "feat(recall): universe.run resolves L1 weights via weight_profile; production switches to the preference profile

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: 离线重算工具 `research/menu_replay.py` + A1–A3 读数

**Files:**
- Create: `autoresearch/research/menu_replay.py`
- Test: `tests/research/test_menu_replay.py`（新；目录已存在则直接放）

**Interfaces:**
- Produces:
  - `load_staging(staging: Path) -> dict` = `{"full": DataFrame(L1_scored_full), "channels": DataFrame(L1_channels), "l2": DataFrame(L2_gbdt_top200)}`
  - `recompute_composite(full: pd.DataFrame, weights_doc: dict) -> pd.Series`（用 `score_<group>`/100 + `combine_group_scores`）
  - `replay_l1(full, channels_long, composite_new, *, composite_quota=400) -> pd.DataFrame`（其余各路留底名单 ∪ 新 composite 前 quota；带 `recall_channels`/`n_channels`/`composite`）
  - `replay_l2(l1p, *, l2_n=200, floors=None, sector_cap=0.20, knife_cap_share=None, enabled_channels=None, sector_seats=None) -> pd.DataFrame`（批 2 再接 `knife_cap_share`/`sector_seats`，本任务先透传 None）
  - `metrics(full, l1p, l2p, *, l1_old, l2_old) -> dict`（A1 spearman、A2 前 20 落刀、A3 L1′ vs L1 落刀差；A4–A7 批 2 补）
  - CLI `python -m autoresearch.research.menu_replay --staging <dir>... --profile preference|calibrated --out <dir>`（`--out` 必填，落 `menu_replay.csv` + `menu_replay.md`；不写生产目录）

- [ ] **Step 1: 写失败测试**

```python
"""menu_replay:用已落盘的 score_<group> 列复刻生产 composite(同一段数学),再重放 L1′/L2′ 形状。"""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.common.scoring import _GROUPS, _PRIOR_WEIGHTS, composite_score
from tests.scan._synth_universe import synth_universe


def _saved_full():
    """模拟 L1_scored_full.csv:composite_score 的输出 + rank/recalled 列。"""
    df = composite_score(synth_universe(n=300, seed=5), _PRIOR_WEIGHTS)
    df = df.sort_values("composite", ascending=False).reset_index(drop=True)
    df.insert(0, "rank", range(1, len(df) + 1))
    df.insert(1, "recalled", df["rank"] <= 120)
    return df


def test_recompute_composite_matches_production_within_csv_rounding():
    from autoresearch.research.menu_replay import recompute_composite
    full = _saved_full()
    again = recompute_composite(full, _PRIOR_WEIGHTS)
    assert (again - full["composite"]).abs().max() <= 0.06     # score_* 落盘取 1 位小数


def test_recompute_composite_changes_with_a_flipped_weight():
    from autoresearch.research.menu_replay import recompute_composite
    full = _saved_full()
    w2 = {"meta": {}, "weights": {"__global__": {**_PRIOR_WEIGHTS["weights"]["__global__"], "momentum": -0.30}}}
    assert (recompute_composite(full, w2) - full["composite"]).abs().max() > 1.0


def test_replay_l1_keeps_other_channels_and_swaps_composite_top(tmp_path):
    from autoresearch.research.menu_replay import replay_l1
    full = _saved_full()
    channels = pd.DataFrame({"channel": ["value"] * 10 + ["composite"] * 20,
                             "code": list(full["code"].iloc[200:210]) + list(full["code"].iloc[:20]),
                             "channel_rank": list(range(1, 11)) + list(range(1, 21)),
                             "channel_score": 1.0})
    new = -full["composite"]                          # 完全反转的新分
    l1p = replay_l1(full, channels, new, composite_quota=20)
    assert set(full["code"].iloc[200:210]) <= set(l1p["code"])          # value 路保留
    assert set(full["code"].iloc[-20:]) <= set(l1p["code"])             # 新 composite 前 20 = 旧倒数 20
    assert not (set(full["code"].iloc[:20]) & set(l1p["code"]))          # 旧 composite 前 20 出局
    assert {"recall_channels", "n_channels", "composite"} <= set(l1p.columns)
```

- [ ] **Step 2: 跑红**

Run: `uv run --no-sync python -m pytest -q tests/research/test_menu_replay.py`
Expected: ImportError。

- [ ] **Step 3: 实现 `autoresearch/research/menu_replay.py`**

```python
#!/usr/bin/env python3
"""L1′/L2′ 离线重算(2026-09-24 可买性对齐 §3.1;只读 staging,产物只落 `--out`)。

用已落盘的 `L1_scored_full.csv`(L0 全帧,含 `score_<group>` 列)+ `L1_channels.csv`(各路留底
名单)+ 生产同一段数学 `scoring.combine_group_scores` 重算 composite,再按 `select_l2` 重放菜单,
量 A1–A7。不改任何生产产物;不走网络。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from autoresearch.common.scoring import (
    _GROUPS, combine_group_scores, falling_knife_mask, healthy_riser_mask, preference_weights_doc,
)
from autoresearch.scan.recall.l2_stratify import select_l2

COMPOSITE_QUOTA = 400          # `recall/channels.py` 的 composite 通道 quota(留底名单里没有 quota,这里显式给)


def load_staging(staging: Path) -> dict:
    s = Path(staging)
    return {"full": pd.read_csv(s / "L1_scored_full.csv", dtype={"code": str}),
            "channels": pd.read_csv(s / "L1_channels.csv", dtype={"code": str}),
            "l2": pd.read_csv(s / "L2_gbdt_top200.csv", dtype={"code": str})}


def recompute_composite(full: pd.DataFrame, weights_doc: dict) -> pd.Series:
    groups = {g: (pd.to_numeric(full[f"score_{g}"], errors="coerce") / 100.0
                  if f"score_{g}" in full.columns else pd.Series(np.nan, index=full.index))
              for g in _GROUPS}
    return combine_group_scores(full, groups, weights_doc).clip(lower=0, upper=100).round(1)


def replay_l1(full: pd.DataFrame, channels_long: pd.DataFrame, composite_new: pd.Series,
              *, composite_quota: int = COMPOSITE_QUOTA) -> pd.DataFrame:
    """L1′ = 其余各路留底名单 ∪ 新 composite 前 quota。不做 quota_union 的 floor 裁剪(近似:
    L1 是并集不是排序,份额类指标对规模不敏感;并集大小写进 metrics 供人看)。"""
    f = full.copy()
    f["code"] = f["code"].astype(str).str.zfill(6)
    f["composite"] = composite_new.to_numpy()
    ch = channels_long.copy()
    ch["code"] = ch["code"].astype(str).str.zfill(6)
    others = ch[ch["channel"].astype(str) != "composite"]
    chan_of: dict[str, set[str]] = {}
    for code, name in zip(others["code"], others["channel"].astype(str), strict=True):
        chan_of.setdefault(code, set()).add(name)
    top = f.sort_values(["composite", "code"], ascending=[False, True]).head(int(composite_quota))
    for code in top["code"]:
        chan_of.setdefault(code, set()).add("composite")
    keep = f[f["code"].isin(chan_of)].copy()
    keep["recall_channels"] = keep["code"].map(lambda c: "|".join(sorted(chan_of[c])))
    keep["n_channels"] = keep["code"].map(lambda c: len(chan_of[c]))
    return keep.sort_values(["composite", "code"], ascending=[False, True]).reset_index(drop=True)


def replay_l2(l1p: pd.DataFrame, *, l2_n: int = 200, floors: dict | None = None,
              sector_cap: float = 0.20, knife_cap_share: float | None = None,
              enabled_channels=None) -> pd.DataFrame:
    l2, _engine = select_l2(l1p, l2_n, floors=floors, sector_cap_frac=sector_cap,
                            enabled_channels=enabled_channels, knife_cap_share=knife_cap_share)
    return l2


def _share(mask: pd.Series | None) -> float | None:
    return None if mask is None or not len(mask) else float(mask.fillna(False).mean())


def metrics(full: pd.DataFrame, l1p: pd.DataFrame, l2p: pd.DataFrame, *,
            l1_old: pd.DataFrame, l2_old: pd.DataFrame) -> dict:
    """A1–A5(§3.1)。A6/A7 由批 2 的席位/落刀帽列现算(缺列 → None)。"""
    comp = pd.to_numeric(l1p["composite"], errors="coerce")
    a1 = float(comp.corr(pd.to_numeric(l1p["pct_20d"], errors="coerce"), method="spearman")) \
        if "pct_20d" in l1p.columns else None
    top20 = l1p.sort_values("composite", ascending=False).head(20)
    return {
        "n_l1_new": int(len(l1p)), "n_l1_old": int(len(l1_old)),
        "A1_spearman_composite_pct20d": a1,
        "A2_top20_knife": _share(falling_knife_mask(top20)),
        "A3_l1_knife_new": _share(falling_knife_mask(l1p)),
        "A3_l1_knife_old": _share(falling_knife_mask(l1_old)),
        "A4_l2_knife_new": _share(falling_knife_mask(l2p)),
        "A4_l2_knife_old": _share(falling_knife_mask(l2_old)),
        "L0_knife": _share(falling_knife_mask(full)),
        "A5_l2_healthy_new": _share(healthy_riser_mask(l2p)),
        "L0_healthy": _share(healthy_riser_mask(full)),
        "A6_sector_seats": int(l2p["sector_seat"].fillna(False).astype(bool).sum()) if "sector_seat" in l2p.columns else None,
        "A7_knife_cap_swaps": int((l2p["selection_detail"].astype(str) == "knife_cap").sum()) if "selection_detail" in l2p.columns else None,
    }


def run_one(staging: Path, weights_doc: dict, *, floors: dict | None, knife_cap: bool,
            enabled_channels=None) -> dict:
    data = load_staging(staging)
    full, l1_old, l2_old = data["full"], data["full"][data["full"]["recalled"].astype(bool)], data["l2"]
    comp_new = recompute_composite(full, weights_doc)
    l1p = replay_l1(full, data["channels"], comp_new)
    share = _share(falling_knife_mask(full)) if knife_cap else None
    l2p = replay_l2(l1p, floors=floors, knife_cap_share=share, enabled_channels=enabled_channels)
    return {"staging": str(staging), **metrics(full, l1p, l2p, l1_old=l1_old, l2_old=l2_old)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="L1′/L2′ 离线重算(只读 staging;产物只落 --out)")
    ap.add_argument("--staging", nargs="+", required=True, help="staging 目录(含 L1_scored_full/L1_channels/L2_gbdt_top200)")
    ap.add_argument("--profile", choices=["preference", "calibrated"], default="preference")
    ap.add_argument("--config", default=".claude/skills/scan-market/scan_config.jsonc")
    ap.add_argument("--knife-cap", action="store_true")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    from autoresearch.scan.user_config import load_user_config
    cfg = load_user_config(args.config) or {}
    funnel, l2cfg = cfg.get("funnel") or {}, cfg.get("l2") or {}
    if args.profile == "preference":
        weights_doc = preference_weights_doc(funnel.get("preference_weights") or {})
    else:
        from autoresearch.common.scoring import _load_weights
        weights_doc = _load_weights(regime="range")
    rows = [run_one(Path(s), weights_doc, floors=l2cfg.get("floors"), knife_cap=args.knife_cap,
                    enabled_channels=funnel.get("recall_channels")) for s in args.staging]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows)
    frame.to_csv(out / "menu_replay.csv", index=False)
    (out / "menu_replay.md").write_text(
        f"# menu_replay · profile={args.profile} · knife_cap={args.knife_cap}\n\n"
        + frame.to_markdown(index=False) + "\n", encoding="utf-8")
    (out / "weights_doc.json").write_text(json.dumps(weights_doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(frame.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`select_l2(..., knife_cap_share=...)` 与 `falling_knife_mask` 在批 2（Task 10/11）才出现：本任务先把 `replay_l2` 里的 `knife_cap_share` 参数**不传**（写 `select_l2(l1p, l2_n, floors=floors, sector_cap_frac=sector_cap, enabled_channels=enabled_channels)`），`metrics` 里的 `falling_knife_mask`/`healthy_riser_mask` 先用本地实现 `lambda df: pd.to_numeric(df["pct_60d"], errors="coerce") < -20`（批 2 Task 10 落地后改回 import）。`frame.to_markdown` 需要 `tabulate`；若环境无该包，改成手写 `|` 表。

- [ ] **Step 4: 跑绿**

Run: `uv run --no-sync python -m pytest -q tests/research/test_menu_replay.py`

- [ ] **Step 5: 真跑七日读数（只读，产物落 scratchpad）**

Run:
```bash
S=$(for d in 2026-09-01 2026-09-07 2026-09-09 2026-09-10 2026-09-11 2026-09-15 2026-09-17; do ls -d context_claude/scan_runs/*/staging/$d | tail -1; done)
uv run --no-sync python -m autoresearch.research.menu_replay --staging $S --profile preference --out /tmp/menu_replay_b1
```
Expected（A1–A3 门，§3.1）：七日全部 `A1 > 0.3`、`A2 < 0.20`、`A3_l1_knife_new ≤ A3_l1_knife_old − 0.05`（七日中位）。不达标 → 回到 Task 8 调 `preference_weights` 量级（只改配置数字，记录在提交信息里），不改代码。把七日读数抄进 `docs/specs/2026-09-24-buyability-realignment-design.md` §3.1 表新增「批 1 实测」列。

- [ ] **Step 6: 提交**

```bash
git add autoresearch/research/menu_replay.py tests/research/test_menu_replay.py docs/specs/2026-09-24-buyability-realignment-design.md
git commit -m "feat(research): menu_replay — offline L1'/L2' shape replay with production composite math (A1–A3)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 7: 批 1 收尾**

Run: `uv run --no-sync python -m pytest -q tests/ && uv run --no-sync ruff check autoresearch tests`

---

# 批 2 · L2 形状：落刀帽、健康 floor、行业席位、席位剔刀（裁定③④；回滚杆 `l2.knife_cap=false` / 删 `l2.floors` / `l2.sector_seats.enabled=false`）

### Task 10: 落刀谓词单一事实源 `scoring.falling_knife_mask`

**Files:**
- Modify: `autoresearch/common/scoring.py`（`healthy_riser_mask` 旁新增）
- Modify: `autoresearch/scan/menu.py:31-36`（`_knife_share`）、`autoresearch/scan/l2_knife_audit.py:38-44`（`_KNIFE`/`_rate`）
- Modify: `autoresearch/research/menu_replay.py`（改回 import 单源）
- Test: `tests/common/test_scoring.py`、`tests/scan/test_menu_health.py`（既有用例必须原样绿）

**Interfaces:**
- Produces: `KNIFE_PCT_60D = -20.0`；`falling_knife_mask(frame: pd.DataFrame, thresh: float = KNIFE_PCT_60D) -> pd.Series | None`（缺 `pct_60d` → None；NaN 行 False）。

- [ ] **Step 1: 写失败测试（`tests/common/test_scoring.py` 追加）**

```python
def test_falling_knife_mask_single_source():
    from autoresearch.common.scoring import KNIFE_PCT_60D, falling_knife_mask
    df = pd.DataFrame({"pct_60d": [-30.0, -20.0, -19.9, float("nan"), 5.0]})
    m = falling_knife_mask(df)
    assert KNIFE_PCT_60D == -20.0
    assert m.tolist() == [True, False, False, False, False]
    assert falling_knife_mask(pd.DataFrame({"x": [1]})) is None
```

- [ ] **Step 2: 跑红** — `uv run --no-sync python -m pytest -q tests/common/test_scoring.py -k falling_knife`

- [ ] **Step 3: 实现**

`scoring.py`（`healthy_riser_mask` 上方）：

```python
KNIFE_PCT_60D = -20.0


def falling_knife_mask(frame: pd.DataFrame, thresh: float = KNIFE_PCT_60D) -> pd.Series | None:
    """落刀谓词(**单一事实源**,2026-09-24 §2.2):pct_60d < −20。菜单体检 `menu._knife_share`、
    `l2_knife_audit`、L2 落刀帽、两类席位剔刀全部改调本函数。缺列 → None(调用方降级);NaN 行 False。
    L3 的 B 条散文另含「无主力」,那是判断层的口径,不在此收口。"""
    if "pct_60d" not in frame.columns:
        return None
    return _num(frame["pct_60d"]) < thresh
```

`menu._knife_share` 改为（分母保持「非 NaN」，与既有 `40%` 用例 parity）：

```python
def _knife_share(df: pd.DataFrame) -> float | None:
    from autoresearch.common.scoring import falling_knife_mask
    m = falling_knife_mask(df)
    p = _num(df, "pct_60d")
    if m is None or p is None or not p.notna().any():
        return None
    return float(m[p.notna()].mean())
```

`l2_knife_audit.py`：删 `_KNIFE = -20.0`，`_rate` 改为：

```python
def _rate(df: pd.DataFrame, thresh: float = KNIFE_PCT_60D) -> float | None:
    if df is None or not len(df):
        return None
    m = falling_knife_mask(df, thresh)
    p = pd.to_numeric(df["pct_60d"], errors="coerce") if "pct_60d" in df.columns else None
    if m is None or p is None or not p.notna().any():
        return None
    return float(m[p.notna()].mean())
```

并在文件顶部 `from autoresearch.common.scoring import KNIFE_PCT_60D, falling_knife_mask`；`knife_rates(..., thresh=_KNIFE)` 的默认改 `KNIFE_PCT_60D`。`menu_replay.py` 的本地 lambda 改回 `from autoresearch.common.scoring import falling_knife_mask, healthy_riser_mask`。

- [ ] **Step 4: 跑绿并提交**

Run: `uv run --no-sync python -m pytest -q tests/common/test_scoring.py tests/scan/test_menu_health.py tests/research/test_menu_replay.py tests/scan -k knife`

```bash
git add autoresearch/common/scoring.py autoresearch/scan/menu.py autoresearch/scan/l2_knife_audit.py autoresearch/research/menu_replay.py tests/common/test_scoring.py
git commit -m "refactor(scan): single-source falling_knife_mask for menu health, knife audit and replay

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: L2 落刀帽（`stratified_l2` 配额 + `select_l2` 透传 + `universe.run` 接线 + `l2.knife_cap` 旋钮）

**Files:**
- Modify: `autoresearch/scan/recall/l2_stratify.py`（`stratified_l2`/`select_l2` 加 `knife_cap_share`；新常量）
- Modify: `autoresearch/scan/universe.py`（knob、份额、meta）
- Modify: `autoresearch/scan/user_config.py`（`l2.knife_cap` bool）
- Test: `tests/scan/test_l2_stratify.py`、`tests/scan/test_config_knobs.py`

**Interfaces:**
- Produces: `stratified_l2(..., knife_cap_share: float | None = None)`、`select_l2(..., knife_cap_share=None)`；`KNIFE_CAP_EXEMPT_STYLES = frozenset({"反转", "低位转强"})`；`meta.json.l2_knife_cap_share`（关 → null）；顶上的行 `selection_detail="knife_cap"`。

- [ ] **Step 1: 写失败测试（`tests/scan/test_l2_stratify.py` 追加）**

```python
def _universe_with_knives(n=600, seed=11):
    rng = np.random.default_rng(seed)
    df = _universe(n, seed)
    df["pct_60d"] = rng.uniform(-60, 60, n)            # 约一半落刀
    return df


def test_knife_cap_none_is_parity():
    df = _universe_with_knives()
    a = stratified_l2(df, l2_n=200)
    b = stratified_l2(df, l2_n=200, knife_cap_share=None)
    pd.testing.assert_frame_equal(a, b)


def test_knife_cap_limits_merit_and_backfill_but_exempts_reversal_buckets():
    from autoresearch.scan.recall.l2_stratify import KNIFE_CAP_EXEMPT_STYLES
    df = _universe_with_knives()
    out = stratified_l2(df, l2_n=200, knife_cap_share=0.10)
    knife = out["pct_60d"] < -20
    merit = out["selection_reason"].isin(["merit", "backfill"])
    assert knife[merit].mean() <= 0.10 + 1 / merit.sum()          # 配额取整误差
    lane_rows = out[(out["selection_reason"] == "lane") & ~out["selection_detail"].isin(KNIFE_CAP_EXEMPT_STYLES)]
    if len(lane_rows):
        assert (lane_rows["pct_60d"] < -20).mean() <= 0.10 + 1 / len(lane_rows)
    assert (out["selection_detail"] == "knife_cap").sum() > 0     # 会变的量:真有非落刀行顶上来
    assert len(out) == 200 and out["code"].is_unique


def test_knife_cap_share_one_is_no_op():
    df = _universe_with_knives()
    a = stratified_l2(df, l2_n=200)
    b = stratified_l2(df, l2_n=200, knife_cap_share=1.0)
    assert set(a["code"]) == set(b["code"])
```

（`_universe` 是文件内既有 fixture 函数。）

- [ ] **Step 2: 跑红** — `uv run --no-sync python -m pytest -q tests/scan/test_l2_stratify.py -k knife_cap`

- [ ] **Step 3: 实现 `l2_stratify.py`**

顶部 import 加 `from autoresearch.common.scoring import falling_knife_mask`；`DEFAULT_FLOORS` 下加：

```python
#: 落刀帽豁免桶(2026-09-24 §2.2):这两桶的语义就是「跌过、在转」(L3 的 lowturn 例外条同源);
#: 其余桶(含趋势/成长——实测落刀率 44%/33%,heat 路按成交额不看方向)与 merit/回填一起受帽。
KNIFE_CAP_EXEMPT_STYLES: frozenset[str] = frozenset({"反转", "低位转强"})


def _knife_quota(share: float | None, n: int) -> int | None:
    """某一步名额里允许的落刀行数;share=None → None = 不设帽(parity)。"""
    if share is None:
        return None
    return int(round(max(0.0, min(1.0, float(share))) * n))
```

`stratified_l2` 签名末尾加 `knife_cap_share: float | None = None`；在 `sel: list[int] = []` 之前加：

```python
    knife = falling_knife_mask(r) if knife_cap_share is not None else None
    knife = knife.fillna(False).astype(bool) if knife is not None else None
    owed = {"merit": 0, "backfill": 0, "lane": 0}     # 被帽跳过的落刀行数 → 顶上的非落刀行记 knife_cap
    taken = {"merit": 0, "backfill": 0}
    lane_taken: dict[str, int] = {}

    def _knife_pass(idx: int, step: str, quota: int | None, style: str | None = None) -> bool:
        """帽判定:非落刀恒过;落刀行在配额内过(计数),超配额跳过(记 owed)。"""
        if knife is None or not knife.iloc[idx] or quota is None:
            return True
        if style is not None:
            if style in KNIFE_CAP_EXEMPT_STYLES:
                return True
            n_taken = lane_taken.get(style, 0)
            if n_taken >= quota:
                owed["lane"] += 1
                return False
            lane_taken[style] = n_taken + 1
            return True
        if taken[step] >= quota:
            owed[step] += 1
            return False
        taken[step] += 1
        return True

    def _detail_for(step: str, idx: int) -> str:
        if knife is not None and not knife.iloc[idx] and owed[step] > 0:
            owed[step] -= 1
            return "knife_cap"
        return ""
```

三处选择循环改成：

```python
    merit_quota = _knife_quota(knife_cap_share, merit_need)
    for idx in order:                                # ② merit 核(sn top,过 cap,过落刀帽)
        if len(sel) >= merit_need:
            break
        if idx not in sel_set and _ok(idx) and _knife_pass(idx, "merit", merit_quota):
            _add(idx, "merit", _detail_for("merit", idx))

    for st in sorted(floors, key=lambda s: -floors[s]):   # ③ floor 补(大 floor 先;非豁免桶受帽)
        m = masks[st]
        have = sum(1 for i in sel if m.iloc[i])
        need = floors[st] - have
        st_quota = _knife_quota(knife_cap_share, floors[st])
        for idx in order:
            if need <= 0:
                break
            if idx not in sel_set and m.iloc[idx] and _ok(idx) and _knife_pass(idx, "lane", st_quota, st):
                _add(idx, "lane", st)
                need -= 1

    backfill_need = l2_n - len(sel)
    backfill_quota = _knife_quota(knife_cap_share, max(0, backfill_need))
    if len(sel) < l2_n:                              # ④ 回填到 l2_n(过 cap,过落刀帽)
        for idx in order:
            if len(sel) >= l2_n:
                break
            if idx not in sel_set and _ok(idx) and _knife_pass(idx, "backfill", backfill_quota):
                _add(idx, "backfill", _detail_for("backfill", idx))
```

（floor 桶的 `selection_detail` 仍写桶名——桶名是既有消费者的契约；lane 步的 `owed["lane"]` 只作计数不写 detail。）「cap 卡死 → 松 cap 兜底」那段不动（它是最后的凑数腿，不受帽）。`select_l2` 签名加 `knife_cap_share: float | None = None` 并透传给 `stratified_l2(...)`。

- [ ] **Step 4: `universe.run` 接线 + 旋钮**

`user_config._SUB_WHITELIST["l2"]` 加 `"knife_cap"`；`_KNOB_TYPES` 加 `("l2", "knife_cap"): (_t_bool, "boolean")`。`universe.run` 形参加 `l2_knife_cap: bool | None = None`；knob 区加 `l2_knife_cap = bool(knob("l2", "knife_cap", l2_knife_cap, False, cfg=_ucfg))`；`select_l2(...)` 调用前加：

```python
    from autoresearch.common.scoring import falling_knife_mask
    _knife = falling_knife_mask(scored) if l2_knife_cap else None
    l2_knife_cap_share = (float(_knife.fillna(False).mean()) if _knife is not None else None)
```

调用改为 `select_l2(recall, l2_n, floors=l2_floors, sector_cap_frac=l2_sector_cap, enabled_channels=_enabled, knife_cap_share=l2_knife_cap_share)`；`meta.json` 加 `"l2_knife_cap_share": l2_knife_cap_share,`。`tests/scan/test_config_knobs.py` 的 `test_new_blocks_whitelisted` 的 `"l2"` 块加 `"knife_cap": True`。

- [ ] **Step 5: 跑绿并提交**

Run: `uv run --no-sync python -m pytest -q tests/scan/test_l2_stratify.py tests/scan/test_universe_l2_cols.py tests/scan/test_config_knobs.py tests/scan/test_l2_regime_cap.py tests/scan/test_l2_regime_wiring_probe.py`

```bash
git add autoresearch/scan/recall/l2_stratify.py autoresearch/scan/universe.py autoresearch/scan/user_config.py tests/scan/test_l2_stratify.py tests/scan/test_config_knobs.py
git commit -m "feat(l2): knife cap — merit/backfill/non-reversal floors admit falling knives only up to the L0 share

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: 生产配置：floors 与落刀帽打开

**Files:**
- Modify: `.claude/skills/scan-market/scan_config.jsonc`（`l2` 块）
- Modify: `.claude/skills/scan-market/STAGES.md`（L2 节一段）
- Test: `tests/scan/test_config_knobs.py`（生产文件能 load）

- [ ] **Step 1: 写失败测试**

```python
def test_production_config_l2_shape_knobs():
    """生产配置(2026-09-24 §2.2):健康 25 / 反转 6 / 低位转强 6,落刀帽开。"""
    from pathlib import Path
    cfg = load_user_config(Path(".claude/skills/scan-market/scan_config.jsonc"))
    l2 = cfg["l2"]
    assert l2["knife_cap"] is True
    assert l2["floors"]["健康"] == 25 and l2["floors"]["反转"] == 6 and l2["floors"]["低位转强"] == 6
    assert sum(l2["floors"].values()) == 103
```

- [ ] **Step 2: 改配置**

`l2` 块改为：

```jsonc
  "l2": {
    "sector_cap": 0.20,        // 任一申万一级 ≤ 20%(=40/200);≥1.0=关
    // ── 落刀帽 + floor(2026-09-24 可买性对齐 §2.2)──
    // 【生效点】universe.run(knob l2.knife_cap → L0 落刀占比 → select_l2 knife_cap_share)+
    //   recall/l2_stratify.stratified_l2(merit/回填/非豁免桶各按份额设配额;反转/低位转强豁免)。
    // ⚖️ 证据:09-01→09-17 七场 L2 落刀 27–56% vs L0 15–27%(恒 ≈2×);floor 桶落刀率
    //   健康 0 / 成长 33 / 趋势 44 / 低位转强 54 / 反转 67(%)。健康桶供给 L1 8–11% ≈ 80–115 行,floor 25 够。
    // 回滚杆 = "knife_cap": false + 删掉 "floors"(回 DEFAULT_FLOORS 101)。
    "knife_cap": true,
    "floors": { "趋势": 20, "健康": 25, "反转": 6, "价值": 12, "成长": 12, "吸筹": 12,
                "主力": 10, "低位转强": 6, "事件": 0 }
  },
```

`STAGES.md` L2 节末尾加一段「落刀帽与 floor(2026-09-24)」：三行说明（份额 = L0 当日落刀占比；豁免反转/低位转强；`selection_detail=knife_cap` 是顶上来的行）。

- [ ] **Step 3: 跑绿并提交**

Run: `uv run --no-sync python -m pytest -q tests/scan/test_config_knobs.py tests/test_skill_docs_refs.py`

```bash
git add .claude/skills/scan-market/scan_config.jsonc .claude/skills/scan-market/STAGES.md tests/scan/test_config_knobs.py
git commit -m "feat(config): L2 knife cap on; floors 健康 25 / 反转 6 / 低位转强 6

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: 行业席位（挑选模块 + L1 注入 + L2 保留行 + CSV 列 + 产物登记 + 旋钮）

**Files:**
- Create: `autoresearch/scan/sector_seats.py`
- Modify: `autoresearch/scan/universe.py`（`_inject_sector_seats_l1`、run 接线、`keep`/`l2_cols` 加列、落 `_sector_seats.json`）
- Modify: `autoresearch/scan/recall/l2_stratify.py:202-241`（`select_l2` 处理 `sector_seat` 行）
- Modify: `autoresearch/scan/user_config.py`（`l2.sector_seats` dict）、`autoresearch/contracts/artifacts.py`（登记）
- Test: `tests/scan/test_sector_seats.py`（新）、`tests/scan/test_l2_stratify.py`、`tests/scan/test_config_knobs.py`

**Interfaces:**
- Produces:
  - `sector_seats.pick_sector_seats(frame, *, per_sector=2, max_sectors=3, exclude=None) -> list[dict]`，元素 `{"code", "name", "industry", "rank_in_sector", "composite"}`
  - `universe._inject_sector_seats_l1(recall, scored, seats) -> pd.DataFrame`（列 `sector_seat: bool`、`sector_seat_industry: str`）
  - `select_l2`：`sector_seat` 行不进竞争、追加末尾、`l2_lane_reserved=True`、`selection_reason="sector_seat"`、`selection_detail=<industry>`
  - 产物 `_sector_seats.json` = `{"schema_version": 1, "date", "seats": [...]}`

- [ ] **Step 1: 写失败测试 `tests/scan/test_sector_seats.py`**

```python
"""行业席位(2026-09-24 §2.3):healthy top3 行业内、非落刀的健康上涨成员,≤per_sector/行业,直通 L1→L2。"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from tests.scan._synth_universe import synth_universe


def _frame():
    df = synth_universe(n=400, seed=9)
    rng = np.random.default_rng(1)
    # 让「电力」成为合格行业:主力净比全正、60 日温和上涨、cmf 正、pe 正
    m = df["industry"] == "电力"
    df.loc[m, "main_net_ratio"] = rng.uniform(0.01, 0.08, m.sum())
    df.loc[m, "pct_60d"] = rng.uniform(2, 30, m.sum())
    df.loc[m, "cmf_20"] = rng.uniform(0.02, 0.4, m.sum())
    df.loc[m, "pe"] = rng.uniform(8, 40, m.sum())
    df.loc[m, "pct_1d"] = 1.0
    df["composite"] = rng.uniform(0, 100, len(df))
    # 埋一只落刀 + 一只当日涨停在电力里,都不得入席
    idx = df.index[m][:2]
    df.loc[idx[0], "pct_60d"] = -35.0
    df.loc[idx[1], "pct_1d"] = 10.0
    return df


def test_pick_sector_seats_only_healthy_non_knife_members():
    from autoresearch.common.scoring import falling_knife_mask, healthy_riser_mask
    from autoresearch.scan.sector_seats import pick_sector_seats
    df = _frame()
    seats = pick_sector_seats(df, per_sector=2, max_sectors=3)
    assert seats and all(s["industry"] == "电力" for s in seats if s["industry"] == "电力")
    codes = {s["code"] for s in seats}
    sub = df[df["code"].isin(codes)]
    assert healthy_riser_mask(sub).all() and not falling_knife_mask(sub).any()
    assert (sub["pct_1d"] < 9.5).all()
    per_ind = pd.Series([s["industry"] for s in seats]).value_counts()
    assert per_ind.max() <= 2


def test_pick_sector_seats_degrades_to_empty_without_columns():
    from autoresearch.scan.sector_seats import pick_sector_seats
    assert pick_sector_seats(pd.DataFrame({"code": ["000001"]})) == []


def test_select_l2_appends_sector_seat_rows_as_reserved():
    from autoresearch.scan.recall.l2_stratify import select_l2
    from tests.scan.test_l2_stratify import _universe
    df = _universe(600)
    df["sector_seat"] = False
    df["sector_seat_industry"] = ""
    df.loc[df.index[:3], ["sector_seat", "sector_seat_industry"]] = [True, "电力"]
    seat_codes = set(df.loc[df.index[:3], "code"])
    out, _ = select_l2(df, 200)
    assert len(out) == 203
    tail = out.tail(3)
    assert set(tail["code"]) == seat_codes
    assert (tail["selection_reason"] == "sector_seat").all() and (tail["selection_detail"] == "电力").all()
    assert tail["l2_lane_reserved"].all()
    assert out["code"].is_unique


def test_universe_run_writes_sector_seats_and_columns(monkeypatch, tmp_path):
    from autoresearch.scan import events as ev_mod, universe as U
    uni = _frame().drop(columns=["composite"])
    monkeypatch.setattr(U, "build_market_frame",
                        lambda *a, **k: (uni.copy(), {"universe_raw": len(uni), "universe": len(uni)}))
    monkeypatch.setattr(ev_mod, "market_event_counts", lambda *a, **k: pd.DataFrame({"code": []}))
    cfg = {"l2": {"sector_seats": {"enabled": True, "per_sector": 2, "max_sectors": 3}}}
    monkeypatch.setattr("autoresearch.scan.user_config.load_user_config", lambda *a, **k: cfg)
    outdir = tmp_path / "2026-09-17"
    U.run("2026-09-17", outdir=outdir, recall_n=60, l2_n=20, recall_mode="multi",
          recall_channels=["composite", "momentum", "value"],
          weights_path=str(tmp_path / "no-such-weights.json"))
    doc = json.loads((outdir / "_sector_seats.json").read_text(encoding="utf-8"))
    assert doc["seats"]
    l2 = pd.read_csv(outdir / "L2_gbdt_top200.csv", dtype={"code": str})
    assert "sector_seat" in l2.columns
    assert set(l2.loc[l2["sector_seat"].astype(bool), "code"]) == {s["code"] for s in doc["seats"]}
```

- [ ] **Step 2: 跑红** — `uv run --no-sync python -m pytest -q tests/scan/test_sector_seats.py`

- [ ] **Step 3: 实现 `autoresearch/scan/sector_seats.py`**

```python
#!/usr/bin/env python3
"""行业席位(2026-09-24 可买性对齐 §2.3;裁定③ Claude 决定:接上涨侧、席位制、不做排序驱动)。

当日 `market.sector_healthy_top3`(资格门:n≥8 ∧ 资金门 ∧ 60 日中位 > −20 ∧ 倒 U 动量)入围的
行业里,取 `healthy_riser_mask ∧ ¬falling_knife_mask` 的成员,按当日 composite(偏好档)降序每行业
≤`per_sector` 只。剔 📌 / ST / 当日涨幅 ≥ CHASE_1D_PCT / 调用方 `exclude`。确定性、零 LLM。
它只保证这几只**被 L3 看见**(L1 强注、L2 保留行、pass1 强留),L3 的 B 条照常适用、不抬评级。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.common.scoring import falling_knife_mask, healthy_riser_mask
from autoresearch.scan.l3.merge import CHASE_1D_PCT, _is_st
from autoresearch.scan.market import sector_healthy_top3

SECTOR_SEATS_FILENAME = "_sector_seats.json"
SECTOR_SEAT_GUARD = "sector_seat"


def pick_sector_seats(frame: pd.DataFrame | None, *, per_sector: int = 2, max_sectors: int = 3,
                      exclude: set[str] | None = None) -> list[dict]:
    need = {"code", "industry", "composite"}
    if frame is None or not len(frame) or not need.issubset(frame.columns):
        return []
    healthy, knife = healthy_riser_mask(frame), falling_knife_mask(frame)
    if healthy is None or knife is None:
        return []
    top = sector_healthy_top3(frame, k=int(max_sectors))
    if not top:
        return []
    d = frame.copy()
    d["code"] = d["code"].astype(str).str.zfill(6)
    keep = healthy.fillna(False) & ~knife.fillna(False)
    if "name" in d.columns:
        keep &= ~d["name"].map(_is_st)
    if "pct_1d" in d.columns:
        keep &= ~(pd.to_numeric(d["pct_1d"], errors="coerce") >= CHASE_1D_PCT)
    if "pinned" in d.columns:
        keep &= ~d["pinned"].map(lambda v: bool(v) if pd.notna(v) else False)
    if exclude:
        keep &= ~d["code"].isin({str(c).zfill(6) for c in exclude})
    d = d.loc[keep]
    out: list[dict] = []
    for row in top:
        ind = str(row["industry"])
        members = d[d["industry"].astype(str) == ind].copy()
        members["_c"] = pd.to_numeric(members["composite"], errors="coerce")
        members = (members.dropna(subset=["_c"])
                   .sort_values(["_c", "code"], ascending=[False, True]).head(int(per_sector)))
        for k, (_, m) in enumerate(members.iterrows(), start=1):
            out.append({"code": m["code"], "name": str(m.get("name", "") or ""), "industry": ind,
                        "rank_in_sector": k, "composite": float(m["_c"])})
    return out
```

- [ ] **Step 4: `universe.py`：注入 + 接线 + 列**

`_inject_pinned_l1` 下方加：

```python
def _inject_sector_seats_l1(recall: pd.DataFrame, scored: pd.DataFrame,
                            seats: list[dict]) -> pd.DataFrame:
    """行业席位 L1 强注(镜像 `_inject_pinned_l1`):已在 recall 只打标;不在 → 从 scored 取真实行。
    `seats` 空 → 原样返回(parity)。"""
    if not seats:
        return recall
    out = recall.copy()
    out["code"] = out["code"].astype(str).str.zfill(6)
    out["sector_seat"] = False
    out["sector_seat_industry"] = ""
    have = set(out["code"])
    scored_z = scored.assign(code=scored["code"].astype(str).str.zfill(6))
    new_rows: list[pd.DataFrame] = []
    for seat in seats:
        code = str(seat["code"]).zfill(6)
        if code in have:
            m = out["code"] == code
            out.loc[m, "sector_seat"] = True
            out.loc[m, "sector_seat_industry"] = seat["industry"]
            continue
        hit = scored_z[scored_z["code"] == code]
        if not len(hit):
            continue
        row = hit.iloc[[0]].copy()
        row["sector_seat"] = True
        row["sector_seat_industry"] = seat["industry"]
        if "recall_channels" in out.columns:
            row["recall_channels"] = "sector_seat"
        if "n_channels" in out.columns:
            row["n_channels"] = 0
        if "best_rank" in out.columns:
            row["best_rank"] = None
        new_rows.append(row)
        have.add(code)
    if new_rows:
        out = pd.concat([out, *new_rows], ignore_index=True, sort=False)
    return out
```

`run()` 形参加 `l2_sector_seats: dict | None = None`；knob 区加：

```python
    l2_sector_seats = dict(knob("l2", "sector_seats", l2_sector_seats, {}, cfg=_ucfg) or {})
```

`recall, per_channel = recall_select(...)` 之后加：

```python
    sector_seats: list[dict] = []
    if bool(l2_sector_seats.get("enabled", False)):
        from autoresearch.scan.sector_seats import SECTOR_SEATS_FILENAME, pick_sector_seats
        sector_seats = pick_sector_seats(
            scored, per_sector=int(l2_sector_seats.get("per_sector", 2)),
            max_sectors=int(l2_sector_seats.get("max_sectors", 3)),
            exclude={str(p["code"]).split(".")[0].zfill(6) for p in pinned})
        recall = _inject_sector_seats_l1(recall, scored, sector_seats)
        (outdir_for_seats := (outdir or ws.scan_root() / analysis_date)).mkdir(parents=True, exist_ok=True)
        (outdir_for_seats / SECTOR_SEATS_FILENAME).write_text(json.dumps(
            {"schema_version": 1, "date": analysis_date, "seats": sector_seats},
            ensure_ascii=False, indent=1), encoding="utf-8")
```

（`outdir` 在下方才赋默认值；这里先算一次同样的默认路径，或把 `outdir = outdir or ws.scan_root() / analysis_date` 那一行上移到 recall 之前——二选一，上移更干净。）`keep` 列表末尾的 presence-gated 那行加 `"sector_seat", "sector_seat_industry"`；`l2_cols` 已含 `*keep`，自然投影。`meta.json` 加 `"sector_seat_n": len(sector_seats),`。

- [ ] **Step 5: `select_l2` 处理席位行**

`select_l2` 开头的 pinned 抽出逻辑后加席位抽出（席位与 pinned 同码时 pinned 优先，已被抽走就不再算席位）：

```python
    has_seat = "sector_seat" in rest.columns and bool(rest["sector_seat"].fillna(False).astype(bool).any())
    if has_seat:
        smask = rest["sector_seat"].fillna(False).astype(bool)
        seat_rows = rest[smask].copy()
        rest = rest[~smask].copy()
```

在 `l2.insert(0, "l2_rank", ...)` 之后、pinned 追加**之前**加：

```python
    if has_seat:
        seat_rows.insert(0, "l2_rank", range(len(l2) + 1, len(l2) + 1 + len(seat_rows)))
        seat_rows["l2_lane_reserved"] = True
        seat_rows["selection_reason"] = "sector_seat"
        seat_rows["selection_detail"] = seat_rows["sector_seat_industry"].astype(str) \
            if "sector_seat_industry" in seat_rows.columns else ""
        l2 = pd.concat([l2, seat_rows], ignore_index=True, sort=False)
```

（pinned 追加段里 `range(len(l2) + 1, ...)` 用的是追加后的 `len(l2)`，编号自然接续。）`L2_SELECTION_REASONS` 元组加 `"sector_seat"`。

- [ ] **Step 6: 旋钮与登记**

`user_config._SUB_WHITELIST["l2"]` 加 `"sector_seats"`；`_KNOB_TYPES` 加 `("l2", "sector_seats"): (_t_dict, "object:{enabled,per_sector,max_sectors}")`。`contracts/artifacts.py` 在 `Artifact("l2", "L2_gbdt_top200.csv", ...)` 之后加：

```python
    Artifact("sector_seats", "_sector_seats.json", "staging", "prelude", "universe", "json", "gated",
             required_when="l2.sector_seats.enabled"),
```

`tests/scan/test_config_knobs.py::test_new_blocks_whitelisted` 的 `l2` 块加 `"sector_seats": {"enabled": True, "per_sector": 2, "max_sectors": 3}`。

- [ ] **Step 7: 跑绿并提交**

Run: `uv run --no-sync python -m pytest -q tests/scan/test_sector_seats.py tests/scan/test_l2_stratify.py tests/scan/test_universe_l2_cols.py tests/scan/test_config_knobs.py tests/scan/test_l3_merge_v3.py tests/contracts -q`

```bash
git add autoresearch/scan/sector_seats.py autoresearch/scan/universe.py autoresearch/scan/recall/l2_stratify.py autoresearch/scan/user_config.py autoresearch/contracts/artifacts.py tests/scan/test_sector_seats.py tests/scan/test_config_knobs.py
git commit -m "feat(l2): sector seats — healthy top-3 industries' non-knife healthy risers ride L1→L2 as reserved rows

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 14: L3 看见席位（🏭 列 + pass1 ①c 强留 + finalists guard 标记 + 账本 role）

**Files:**
- Modify: `autoresearch/scan/l3/prompt.py:336-345`（pinned 列块之后加 seat 列）
- Modify: `autoresearch/scan/l3/triage.py:140-157`（①b 之后加 ①c）
- Modify: `autoresearch/scan/l3/merge.py:611-625`（`write_finalists` 标 guard）
- Modify: `autoresearch/scan/outcome.py:236-242`（`run_facts` role）
- Test: `tests/scan/test_sector_seat_l3.py`（新）

**Interfaces:**
- Produces: L3 表列 `seat`（值 `🏭`/空）+ 图例；`triage` 的 `selection_reason="conviction_guard"`、`selection_detail="sector_seat"`；`finalists.csv.guard == "sector_seat"`（仅 guard 为空时）；账本 `role == "sector_seat"`。

- [ ] **Step 1: 写失败测试**

```python
"""行业席位在 L3 侧的三处可见性(2026-09-24 §2.3)。"""
from __future__ import annotations

import json

import pandas as pd


def _l2(n=30):
    df = pd.DataFrame({"code": [f"{600000 + i:06d}" for i in range(n)], "name": [f"票{i}" for i in range(n)],
                       "industry": "电力", "composite": list(range(n, 0, -1)), "gbdt_score": list(range(n, 0, -1)),
                       "recall_channels": "value", "n_channels": 1, "pct_1d": 0.5})
    df["sector_seat"] = False
    df.loc[df.index[-2:], "sector_seat"] = True          # composite 最低的两只 = 席位(不靠分进表)
    return df


def test_triage_keeps_sector_seats_as_protected_mandatory():
    from autoresearch.scan.l3.triage import triage_l2_for_l3
    kept, cut = triage_l2_for_l3(_l2(), target=5)
    seat_codes = {"600028", "600029"}
    assert seat_codes <= set(kept["code"])
    marks = kept[kept["code"].isin(seat_codes)]
    assert (marks["selection_reason"] == "conviction_guard").all()
    assert (marks["selection_detail"] == "sector_seat").all()


def test_write_finalists_marks_sector_seat_guard(tmp_path):
    from autoresearch.scan.l3.merge import write_finalists
    scan = tmp_path / "2026-09-17"
    scan.mkdir()
    judged = [{"code": "600001", "name": "甲", "sector": "电力", "conviction": 70, "finalist": True,
               "thesis": "t", "mechanism": "m", "risk": "r"},
              {"code": "600002", "name": "乙", "sector": "电力", "conviction": 66, "finalist": True,
               "thesis": "t", "mechanism": "m", "risk": "r"}]
    (scan / "_l3_judged.json").write_text(json.dumps(judged, ensure_ascii=False), encoding="utf-8")
    (scan / "_sector_seats.json").write_text(json.dumps({"schema_version": 1, "date": "2026-09-17",
                                                          "seats": [{"code": "600002", "industry": "电力"}]}),
                                             encoding="utf-8")
    write_finalists("2026-09-17", root=tmp_path)
    fin = pd.read_csv(scan / "finalists.csv", dtype={"code": str})
    assert fin.set_index("code").loc["600002", "guard"] == "sector_seat"
    assert fin.set_index("code").loc["600001", "guard"] != "sector_seat"


def test_run_facts_role_sector_seat(tmp_path):
    from autoresearch.scan.outcome import run_facts
    run = tmp_path / "20260917-0917_2152"
    (run / "trace" / "staging").mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps({"analysis_date": "2026-09-17"}), encoding="utf-8")
    (run / "trace" / "staging" / "finalists.csv").write_text(
        "code,name,sector,guard,lane\n600002,乙,电力,sector_seat,value\n", encoding="utf-8")
    facts = run_facts(run)
    assert facts["rows"]["600002"]["role"] == "sector_seat"
```

`write_finalists` 需要 `composite_seat_cfg()` 读配置——测试环境若读到生产配置开启席位 m=3 且无 L2 表，`pick_composite_seats(None, ...)` 返回 `[]`，不影响。

- [ ] **Step 2: 跑红** — `uv run --no-sync python -m pytest -q tests/scan/test_sector_seat_l3.py`

- [ ] **Step 3: 实现**

`triage.py` ①b 块之后加：

```python
    if "sector_seat" in d.columns:                       # ①c 行业席位强留(2026-09-24 §2.3)
        seat2 = d["sector_seat"].map(lambda v: bool(v) if pd.notna(v) else False)
        for i in d.index[seat2]:
            is_seat.loc[i] = True                        # 与 ①b 同属受保护集(超 target 不被截尾)
            if not mandatory.loc[i]:
                mandatory.loc[i] = True
                _mark(i, "conviction_guard", "sector_seat")
```

`prompt.py` pinned 列块之后加：

```python
    if "sector_seat" in df.columns and df["sector_seat"].fillna(False).astype(bool).any():
        df["seat"] = df["sector_seat"].map(lambda v: "🏭" if bool(v) else "")
        cols = [*cols, "seat"]
        header += ["_🏭(seat列):行业席位——当日 healthy top3 行业内的非落刀健康上涨成员,确定性直通到本表;"
                   "B 条照常适用,不因席位抬评级;它只保证「被看见」。_", ""]
```

`merge.write_finalists` 在 composite 席位注入之后、pinned 注入之前加：

```python
    seats_doc = scan_dir / "_sector_seats.json"
    if seats_doc.exists():                                  # 行业席位 guard 留痕(2026-09-24 §2.3)
        with contextlib.suppress(Exception):
            sector_codes = {str(s["code"]).zfill(6) for s in
                            (json.loads(seats_doc.read_text(encoding="utf-8")).get("seats") or [])}
            if sector_codes and "code" in fin.columns:
                if "guard" not in fin.columns:
                    fin["guard"] = ""
                m = fin["code"].astype(str).str.zfill(6).isin(sector_codes) & (fin["guard"].fillna("") == "")
                fin.loc[m, "guard"] = "sector_seat"
```

`outcome.run_facts` 的 role 三元改为：

```python
            "role": ("pinned" if lane == "pinned" else
                     "composite_seat" if guard == "composite_seat" else
                     "sector_seat" if guard == "sector_seat" else "finalist"),
```

- [ ] **Step 4: 跑绿并提交**

Run: `uv run --no-sync python -m pytest -q tests/scan/test_sector_seat_l3.py tests/scan/test_l3_merge_v3.py tests/scan -k "triage or l3_prompt or run_facts"`

```bash
git add autoresearch/scan/l3/prompt.py autoresearch/scan/l3/triage.py autoresearch/scan/l3/merge.py autoresearch/scan/outcome.py tests/scan/test_sector_seat_l3.py
git commit -m "feat(l3): sector seats are visible (🏭 column), protected in pass1, guard-marked in finalists and the ledger

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 15: 守卫⑨ composite 席位不接刀

**Files:**
- Modify: `autoresearch/scan/l3/merge.py:68-100`（`pick_composite_seats`）
- Test: `tests/scan/test_l3_merge_v3.py`

- [ ] **Step 1: 写失败测试（追加）**

```python
def test_composite_seats_exclude_falling_knives():
    from autoresearch.scan.l3.merge import pick_composite_seats
    l2 = pd.DataFrame({"code": ["000001", "000002", "000003", "000004"], "name": list("甲乙丙丁"),
                       "industry": "电力", "gbdt_score": [90.0, 80.0, 70.0, 60.0],
                       "pct_1d": 1.0, "pct_60d": [-35.0, 5.0, -21.0, 8.0]})
    assert [s["code"] for s in pick_composite_seats(l2, 3)] == ["000002", "000004"]
```

- [ ] **Step 2: 跑红** — `uv run --no-sync python -m pytest -q tests/scan/test_l3_merge_v3.py -k falling_knives`

- [ ] **Step 3: 实现**

`pick_composite_seats` 的 `if exclude:` 之前加：

```python
    from autoresearch.common.scoring import falling_knife_mask
    knife = falling_knife_mask(d)                        # 席位不接刀(2026-09-24 §2.4):19 席位 18 张 UW/Sell 的病根
    if knife is not None:
        keep &= ~knife.fillna(False)
```

docstring 的「剔」句加「落刀(pct_60d<−20)」。

- [ ] **Step 4: 跑绿并提交**

Run: `uv run --no-sync python -m pytest -q tests/scan/test_l3_merge_v3.py tests/scan/test_relative_buy.py`

```bash
git add autoresearch/scan/l3/merge.py tests/scan/test_l3_merge_v3.py
git commit -m "fix(l3): composite seats no longer admit falling knives

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 16: menu_replay 接落刀帽与席位，出 A4–A7 读数

**Files:**
- Modify: `autoresearch/research/menu_replay.py`（`replay_l2` 传 `knife_cap_share`；`run_one` 席位注入）
- Test: `tests/research/test_menu_replay.py`

- [ ] **Step 1: 写失败测试（追加）**

```python
def test_replay_l2_applies_knife_cap_and_sector_seats():
    from autoresearch.research.menu_replay import replay_l1, replay_l2, recompute_composite
    full = _saved_full()
    channels = pd.DataFrame({"channel": "value", "code": full["code"].iloc[:50], "channel_rank": range(1, 51), "channel_score": 1.0})
    l1p = replay_l1(full, channels, recompute_composite(full, _PRIOR_WEIGHTS), composite_quota=100)
    seats = [{"code": full["code"].iloc[-1], "industry": "电力"}]
    l2p = replay_l2(l1p, l2_n=40, knife_cap_share=0.05, sector_seats=seats, full=full)
    assert "sector_seat" in l2p.columns and l2p["sector_seat"].astype(bool).sum() == 1
    assert len(l2p) == 41
```

- [ ] **Step 2: 实现**

`replay_l2` 签名加 `sector_seats: list[dict] | None = None, full: pd.DataFrame | None = None`；体内在 `select_l2` 之前：

```python
    if sector_seats:
        from autoresearch.scan.universe import _inject_sector_seats_l1
        l1p = _inject_sector_seats_l1(l1p, full if full is not None else l1p, sector_seats)
```

并把 `select_l2(...)` 调用加 `knife_cap_share=knife_cap_share`。`run_one` 加形参 `sector_seats_cfg: dict | None`，用 `pick_sector_seats(full_with_new_composite, ...)`（`full` 复制一份把 `composite` 换成 `comp_new`）产席位再传给 `replay_l2`。CLI 加 `--sector-seats`（读 `l2.sector_seats` 配置块）。

- [ ] **Step 3: 跑绿；真跑七日 A4–A7**

Run: `uv run --no-sync python -m pytest -q tests/research/test_menu_replay.py`

Run:
```bash
S=$(for d in 2026-09-01 2026-09-07 2026-09-09 2026-09-10 2026-09-11 2026-09-15 2026-09-17; do ls -d context_claude/scan_runs/*/staging/$d | tail -1; done)
uv run --no-sync python -m autoresearch.research.menu_replay --staging $S --profile preference --knife-cap --sector-seats --out /tmp/menu_replay_b2
```
Expected（§3.1）：`A4_l2_knife_new ≤ L0_knife + 0.06`、`A5_l2_healthy_new ≥ L0_healthy`、`A6_sector_seats ≥ 2`（有入围行业的日子）、`A7 > 0`（七日合计）。不达标 → 调 `l2.floors`/`sector_seats` 配置值，不改代码；读数抄进 spec §3.1「批 2 实测」列。

- [ ] **Step 4: 提交 + 批 2 收尾**

```bash
git add autoresearch/research/menu_replay.py tests/research/test_menu_replay.py docs/specs/2026-09-24-buyability-realignment-design.md
git commit -m "feat(research): menu_replay applies knife cap and sector seats (A4–A7 readings)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
uv run --no-sync python -m pytest -q tests/ && uv run --no-sync ruff check autoresearch tests
```

---

# 批 3 · 卡片入场行 + E6 v4.0 分级 + 不可买归因（裁定①④；回滚杆 `relative_buy.tiering=false` + `pool="composite"`）

### Task 17: 卡契约 `entry` 字段 + 解析器入场行优先

**Files:**
- Modify: `autoresearch/contracts/agent_output.py:137-151`（`L4_CARD.fields` 加 `entry`）
- Modify: `autoresearch/scan/l4/parsers.py:530-535`（`_empty_card_context` 加 `entry_source`）、`:565-580`（`_parse_card_context_impl` 入场行优先）
- Modify: `autoresearch/scan/relative_buy.py:274-279`（`FIELD_USAGE.display_only.fields` 加 `card_context.entry_source`）
- Test: `tests/scan/test_parsers_card_context.py`

**Interfaces:**
- Produces: 契约行 `**入场**: 允许 | 禁止 | 条件(<一句>)`；`card_context.entry_stance ∈ {ALLOWED, PROHIBITED, CONDITIONAL, UNKNOWN}` 不变；新增 `card_context.entry_source ∈ {"line", "prose", None}`。

- [ ] **Step 1: 写失败测试（追加）**

```python
@pytest.mark.parametrize(("line", "stance"), [
    ("**入场**: 允许", "ALLOWED"),
    ("**入场**: 禁止", "PROHIBITED"),
    ("**入场**: 条件(收复 10EMA 才考虑)", "CONDITIONAL"),
    ("**入场**：允许", "ALLOWED"),
])
def test_entry_line_wins_over_prose(line, stance):
    """机读入场行(2026-09-24 §2.5)优先于仓位/触发位散文推断;仓位写 0% 也不能压过它。"""
    text = "\n".join([
        "# 决策卡 — 600018 上港集团 @ 2026-09-17",
        "| 评级 | 现价 | 仓位 | 触发位 |",
        "|---|---|---|---|",
        "| Hold | 5.43 | 0% | 不建仓 |",
        line,
        "FINAL TRANSACTION PROPOSAL: **HOLD**",
    ])
    got = parse_card_context(text)
    assert got["entry_stance"] == stance and got["entry_source"] == "line"


def test_without_entry_line_prose_inference_is_kept_and_labelled():
    text = "\n".join([
        "# 决策卡", "| 评级 | 现价 | 仓位 |", "|---|---|---|", "| Hold | 10 | 不新开仓 |",
        "FINAL TRANSACTION PROPOSAL: **HOLD**",
    ])
    got = parse_card_context(text)
    assert got["entry_stance"] == "PROHIBITED" and got["entry_source"] == "prose"


def test_l4_card_contract_has_entry_field():
    from autoresearch.contracts.agent_output import L4_CARD
    import re
    f = L4_CARD.field("entry")
    assert f.required is False
    assert re.search(f.pattern, "**入场**: 条件(x)").group(1) == "条件"
```

既有断言整份 `card_context` 字典相等的用例（如有）要把 `"entry_source"` 加进期望字典。

- [ ] **Step 2: 跑红** — `uv run --no-sync python -m pytest -q tests/scan/test_parsers_card_context.py -k "entry_line or prose_inference or contract_has_entry"`

- [ ] **Step 3: 实现**

`agent_output.L4_CARD.fields` 在 `exec_line_pos` 之前加：

```python
        Field("entry", r"\*\*入场\*\*[:：]\s*(允许|禁止|条件)", False,
              "机读入场行(2026-09-24 §2.5):T+1 尾盘按执行线能否新开仓;与五档评级语义分离。"
              "早停卡只许 禁止|条件;满卡三选一。缺行 → parsers 按散文推断并记 entry_source=prose"),
```

`parsers.py` 常量区加：

```python
_ENTRY_LINE_RE = re.compile(r"\*\*入场\*\*[:：]\s*(允许|禁止|条件)")
_ENTRY_LINE_STANCE = {"允许": "ALLOWED", "禁止": "PROHIBITED", "条件": "CONDITIONAL"}
```

`_empty_card_context` 返回字典加 `"entry_source": None,`。`_parse_card_context_impl` 里把

```python
    stance, conflict = _entry_stance(position_raw, trigger_raw)
    if conflict:
        ...
```

改为：

```python
    line_m = _ENTRY_LINE_RE.search(body)
    if line_m:                                   # 机读入场行优先(2026-09-24 §2.5)
        stance, conflict, entry_source = _ENTRY_LINE_STANCE[line_m.group(1)], False, "line"
    else:
        stance, conflict = _entry_stance(position_raw, trigger_raw)
        entry_source = "prose"
    if conflict:
        parse_errors.append(
            "entry_stance: 否定/零仓位证据与允许新开仓证据同时出现,保守记 PROHIBITED")
```

返回字典加 `"entry_source": entry_source,`。`relative_buy.FIELD_USAGE["display_only"]["fields"]` 加 `"card_context.entry_source"`。

- [ ] **Step 4: 跑绿并提交**

Run: `uv run --no-sync python -m pytest -q tests/scan/test_parsers_card_context.py tests/scan/test_relative_buy.py tests/contracts -q`

```bash
git add autoresearch/contracts/agent_output.py autoresearch/scan/l4/parsers.py autoresearch/scan/relative_buy.py tests/scan/test_parsers_card_context.py
git commit -m "feat(card): machine-readable entry line (允许|禁止|条件) with prose fallback

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 18: agent 定义：模板加入场行 + 规则 + 锚测试 + 自检

**Files:**
- Modify: `.claude/agents/l4-card.md:61-63`（评级节加规则）、`:98-107`（早停模板）、`:150-153`（满卡模板）
- Modify: `.claude/skills/stock-research/lite-playbook.md`（对应三处：评分卡映射节、模板 A、模板 B）
- Modify: `tests/test_agent_defs.py:44-57`（anchors 加 `"**入场**:"`）
- Modify: `autoresearch/scan/self_review.py:283-330`（`card_contract_lint` 加入场行缺失 warn）
- Test: `tests/test_agent_defs.py`、`tests/scan/test_self_review_brief.py`

- [ ] **Step 1: 写失败测试**

`tests/test_agent_defs.py` 的 anchors 列表加 `"**入场**:"`、`"入场行"`。`tests/scan/test_self_review_brief.py`（或 self_review 相关测试文件）追加：

```python
def test_card_contract_lint_flags_missing_entry_line(tmp_path):
    from autoresearch.scan.self_review import card_contract_lint
    base = tmp_path / "details"; base.mkdir(parents=True)
    (base / "600018.md").write_text("# 决策卡 — 600018 上港 @ 2026-09-17  ·  〔早停·表面 DD〕\n"
                                    "**早停**: 停于 P3 ｜ 停因:资金流出\nFINAL TRANSACTION PROPOSAL: **HOLD**\n", encoding="utf-8")
    (base / "600035.md").write_text("# 决策卡 — 600035 楚天 @ 2026-09-17  ·  〔早停·表面 DD〕\n"
                                    "**入场**: 禁止\n**早停**: 停于 P3 ｜ 停因:资金流出\nFINAL TRANSACTION PROPOSAL: **HOLD**\n", encoding="utf-8")
    hits = [h for h in card_contract_lint(tmp_path) if h["check"] == "卡片契约·入场行缺失"]
    assert [h["code"] for h in hits] == ["600018"] and hits[0]["severity"] == "warn"
```

- [ ] **Step 2: 跑红** — `uv run --no-sync python -m pytest -q tests/test_agent_defs.py tests/scan/test_self_review_brief.py -k "anchors or entry_line"`

- [ ] **Step 3: 改两份定义（同一段文字，两边逐字同步）**

在 `l4-card.md` 「## 评级 = 评分卡派生(非 gestalt)」节末尾加一段（lite-playbook「## 评分卡映射」节同样加）：

```
**入场行(2026-09-24 新增,机读契约,所有卡必写)**:`**入场**: 允许 | 禁止 | 条件(<一句前置条件>)`。它与五档评级**语义分离**:评级答「值不值得持有」,入场答「T+1 尾盘按执行线能不能新开仓」。规则:
- 满卡:≥Overweight → `允许`(入场否决条件另写在触发位);Hold → `允许` **仅当** EV 目标带中枢 ≥ +0.3% ∧ R:R ≥ 1.0 ∧ OW 三门至少两门 ✓ ∧ 情报 T0/24h 无负面,否则 `条件(...)` 或 `禁止`;Underweight/Sell → `禁止`。
- 早停卡:只能写 `禁止` 或 `条件(...)`,**不得写允许**(早停 = 翻盘牌翻完仍加不起买点;A 级 BUY 只来自走完 P4/P5 的卡)。
- 📌 持仓卡照写(持仓管理节另答持有者怎么办)。E6 v4 据此分级:允许 → A 级候选;禁止 → 硬否决;条件/缺行 → 只能当 R 级。
```

模板 A（早停卡）在 `**一行多空**: ...` 与 `FINAL TRANSACTION PROPOSAL` 之间加一行：

```
**入场**: <禁止|条件(<一句>)>   ← 机读契约(2026-09-24);早停卡不得写「允许」
```

模板 B（满卡）在 `**T+2 开盘应对预案**` 段之后、`**盯梢线**` 之前加：

```
**入场**: <允许|禁止|条件(<一句>)>   ← 机读契约(2026-09-24);规则见「评级 = 评分卡派生」节末段
```

- [ ] **Step 4: 自检 lint**

`self_review.card_contract_lint` 循环内，`if "♻️" in text and "复用" in text: continue` 之后加：

```python
        if "**入场**:" not in text and "**入场**：" not in text:
            out.append({"check": "卡片契约·入场行缺失", "severity": "warn", "code": code,
                        "detail": f"{code} 卡缺『**入场**: 允许|禁止|条件』行(E6 v4 A/R 分级的机读依据;缺行只能当 R 级)"})
```

- [ ] **Step 5: 跑绿并提交**

Run: `uv run --no-sync python -m pytest -q tests/test_agent_defs.py tests/test_codex_agent_defs.py tests/test_skill_docs_refs.py tests/scan/test_self_review_brief.py`

```bash
git add .claude/agents/l4-card.md .claude/skills/stock-research/lite-playbook.md tests/test_agent_defs.py autoresearch/scan/self_review.py tests/scan/test_self_review_brief.py
git commit -m "feat(l4-card): entry line in both templates + rubric rule; self_review warns when missing

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 19: E6 v4.0：入场硬门 + A/R 分级 + `tiering` 旋钮 + 接线

**Files:**
- Modify: `autoresearch/scan/relative_buy.py`（`RULE_VERSION`、`build_decision` 签名与选择段、`_hard_gate` ④、`_render_why`、输出键、`configured_relative_buy` 五元组、`write_decision`/`verify_decision`/`safe_*` 透传）
- Modify: `autoresearch/scan/post_run.py:859-867`（五元组 + 透传）
- Modify: `autoresearch/scan/user_config.py`（`relative_buy.tiering` bool）
- Modify: `.claude/skills/scan-market/scan_config.jsonc`（`relative_buy`：`pool` 改 `finalists`、加 `tiering: true`）
- Test: `tests/scan/test_relative_buy.py`、`tests/scan/test_config_knobs.py`

**Interfaces:**
- Produces: `build_decision(..., tiering: bool = False)`；`tiering=True` 时 ①`entry_stance == "PROHIBITED"` → `hard_gate.no_redflag` 否决；②A 级 = `eligible ∧ ¬pinned ∧ entry_stance == "ALLOWED"`，R 级 = 其余 eligible ∧ ¬pinned；排序均沿用 v3 finalists 键；`buys[0]` 加 `tier`/`basis ∈ {card_backed, relative_forced}`；顶层 `tiering: bool`、`tier_counts: {"A": n, "R": n} | None`；`tiering=False` = v3 逐字。`configured_relative_buy() -> (mode, exclude_pinned, activate_date, pool, tiering)`；`configured_tiering() -> bool`。

- [ ] **Step 1: 写失败测试（追加）**

```python
def _card(entry_line: str | None, rating: str = "Hold") -> str:
    lines = [f"# 决策卡 — 000000 x @ {DATE}", "| 评级 | 现价 | EV目标 | R:R | 仓位 | 触发位 |",
             "|---|---|---|---|---|---|", f"| {rating} | 10 | +0.5% | 1.2 | 5% | 涨>3% 放弃 |"]
    if entry_line:
        lines.append(entry_line)
    lines.append("FINAL TRANSACTION PROPOSAL: **HOLD**")
    return "\n".join(lines)


def _snapshot(**cards: str) -> dict:
    return {code: {"text": text} for code, text in cards.items()}


def test_tiering_off_is_v3_verbatim_even_with_prohibited_card(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    snap = _snapshot(**{"002345": _card("**入场**: 禁止")})
    doc = build_decision(scan, card_snapshot=snap)                      # tiering 默认 False
    assert doc["buys"][0]["code"] == "002345" and "tier" not in doc["buys"][0]
    assert doc["tiering"] is False and doc["tier_counts"] is None
    assert any(c["type"] == "card_says_prohibited" for c in doc["conflicts"])


def test_tiering_prohibited_card_is_hard_gated_out(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    snap = _snapshot(**{"002345": _card("**入场**: 禁止")})
    doc = build_decision(scan, card_snapshot=snap, tiering=True)
    by = _by_code(doc)
    assert by["002345"]["hard_gate"]["no_redflag"] is False
    assert doc["buys"][0]["code"] == "000034" and doc["buys"][0]["tier"] == "R"
    assert doc["buys"][0]["basis"] == "relative_forced" and doc["conflicts"] == []


def test_tiering_allowed_card_wins_as_A_tier_even_if_lower_scored(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    snap = _snapshot(**{"601699": _card("**入场**: 允许"), "002345": _card("**入场**: 条件(收复均线)")})
    doc = build_decision(scan, card_snapshot=snap, tiering=True)
    assert doc["buys"][0] == {"code": "601699", "basis": "card_backed", "rank": 1, "tier": "A"}
    assert doc["tier_counts"] == {"A": 1, "R": 3}
    assert "A 级 1 只" in doc["why"]


def test_tiering_all_prohibited_is_blocked_not_forced(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    snap = _snapshot(**{c: _card("**入场**: 禁止") for c in _RANK_ORDER})
    doc = build_decision(scan, card_snapshot=snap, tiering=True)
    assert doc["blocked"] is True and doc["buys"] == []
    assert {r["reason"] for r in doc["blocked_reasons"]} == {"hard_gate.no_redflag"}


def test_rule_version_is_v4():
    from autoresearch.scan.relative_buy import RULE_VERSION
    assert RULE_VERSION == "e6.v4.0"
```

若既有 `test_rule_version_is_pinned_and_reaches_the_written_product` 写死 `"e6.v3.0"`，改为引用常量。`tests/scan/test_config_knobs.py` 加 `("relative_buy", {"tiering": True})` 进白名单 round-trip 用例。

- [ ] **Step 2: 跑红** — `uv run --no-sync python -m pytest -q tests/scan/test_relative_buy.py -k "tiering or rule_version_is_v4"`

- [ ] **Step 3: 实现（`relative_buy.py`）**

`RULE_VERSION = "e6.v4.0"`，旁注：

```python
# v4.0 = v3.0 + `tiering` 开关(2026-09-24 可买性对齐 §2.6):开 → ①卡面入场=禁止进 no_redflag 硬门;
# ②BUY 分 A 级(卡面允许入场)/R 级(其余 eligible,裁定①「成功日 ≥1 只」的强制相对)。
# 关(默认)→ v3.0 逐字(golden `test_tiering_off_is_v3_verbatim_even_with_prohibited_card`)。
# 票级 data_a(批 0)不受开关控制:那是缺陷修,不是规则。
```

`build_decision` 签名加 `tiering: bool = False`；候选循环前先算卡上下文（避免二次解析）：

```python
    card_ctx = {entry["code"]: _card_context_for(entry["code"], card_snapshot) for entry in entries}
    ctx["card_context"] = card_ctx
    ctx["tiering"] = tiering
```

候选字典的 `"card_context": _card_context_for(code, card_snapshot)` 改 `"card_context": card_ctx[code]`。`_hard_gate` ④ 在 `elif stop_reason in REDFLAG_EARLY_STOP_REASONS:` 之前插入：

```python
    elif ctx.get("tiering") and ctx["card_context"].get(code, {}).get("entry_stance") == "PROHIBITED":
        fail("no_redflag", "卡面入场=禁止(entry_stance=PROHIBITED;v4.0 tiering)")
```

选择段：`buys = ([{"code": buy_pool[0]["code"], ...}] if buy_pool else [])` 改为：

```python
    tier_counts: dict[str, int] | None = None
    if tiering:
        a_pool = [row for row in buy_pool if row["card_context"].get("entry_stance") == "ALLOWED"]
        r_pool = [row for row in buy_pool if row["card_context"].get("entry_stance") != "ALLOWED"]
        tier_counts = {"A": len(a_pool), "R": len(r_pool)}
        if a_pool:
            buys = [{"code": a_pool[0]["code"], "basis": "card_backed", "rank": 1, "tier": "A"}]
        elif r_pool:
            buys = [{"code": r_pool[0]["code"], "basis": "relative_forced", "rank": 1, "tier": "R"}]
        else:
            buys = []
    else:
        buys = ([{"code": buy_pool[0]["code"], "basis": "relative", "rank": 1}] if buy_pool else [])
```

`_render_why` 加形参 `tier_counts: dict | None = None, tier: str | None = None`，在 `parts` 末尾（冲突之前）加：

```python
    if tier_counts is not None:
        parts.append(f"分级:A 级 {tier_counts['A']} 只 / R 级 {tier_counts['R']} 只,选中 {tier} 级。")
```

调用处传 `tier_counts=tier_counts, tier=(buys[0].get("tier") if buys else None)`。输出字典加 `"tiering": tiering, "tier_counts": tier_counts,`（放 `"pool"` 旁）。`configured_relative_buy` 返回五元组（末位 `bool(block.get("tiering", False))`），加 `def configured_tiering() -> bool: return configured_relative_buy()[4]`；`write_decision`/`verify_decision`/`safe_write_decision`/`safe_verify_decision` 各加 `tiering: bool = False` 并透传给 `build_decision`。`post_run.py:859` 改 `_rb_mode, _rb_exclude_pinned, _, _rb_pool, _rb_tiering = configured_relative_buy()`，两处 `safe_*` 调用加 `tiering=_rb_tiering`。`user_config`：`_SUB_WHITELIST["relative_buy"]` 加 `"tiering"`，`_KNOB_TYPES` 加 `("relative_buy", "tiering"): (_t_bool, "boolean")`。

- [ ] **Step 4: 生产配置**

`scan_config.jsonc` 的 `relative_buy` 行改为：

```jsonc
  // 2026-09-24 可买性对齐 §2.6:池回到全部派发卡(席位含在内);tiering 开 = 入场门 + A/R 分级。
  // ⚖️ 证据:pool=composite 19 席位卡面 UW 15/Sell 3/Hold 1;7 笔 active BUY −0.55pp 胜 2/7。
  // 回滚杆 = "tiering": false(v3 逐字)+ "pool": "composite"。
  "relative_buy": { "mode": "active", "exclude_pinned": true, "activate_date": "2026-08-19",
                    "pool": "finalists", "tiering": true },
```

- [ ] **Step 5: 真实现场 parity(tiering=false 必须与盘上 v3 决策逐字一致)**

Run:
```bash
uv run --no-sync python - <<'EOF2'
import json
from pathlib import Path
from autoresearch.scan.relative_buy import build_decision
NEW_KEYS = {"tiering", "tier_counts", "rule_version"}
for d in ("20260909-0909_2209", "20260910-0910_2211", "20260911-0912_1248"):
    st = Path(f"reports_claude/scan/{d}/trace/staging")
    disk = json.loads((st / "_relative_buy_decision.json").read_text(encoding="utf-8"))
    now = build_decision(st, mode="active", exclude_pinned=True, pool="composite", tiering=False)
    for k in ("buys", "blocked", "blocked_reasons", "counts"):
        assert now[k] == disk[k], (d, k, now[k], disk[k])
    assert now["candidates"][0]["hard_gate"].keys() == disk["candidates"][0]["hard_gate"].keys()
    print(d, "parity OK; new keys:", sorted(set(now) - set(disk)))
EOF2
```
Expected: 三场各印 `parity OK`（09-15/09-17 两场因票级 data_a 是缺陷修，`blocked_reasons` 会变，不在此 parity 集）。不一致先看 `card_context` 解析是否改变了 `hard_gate`（tiering=False 下不该改）。

- [ ] **Step 6: 跑绿并提交**

Run: `uv run --no-sync python -m pytest -q tests/scan/test_relative_buy.py tests/scan/test_config_knobs.py tests/scan -k "post_run or relative"`

```bash
git add autoresearch/scan/relative_buy.py autoresearch/scan/post_run.py autoresearch/scan/user_config.py .claude/skills/scan-market/scan_config.jsonc tests/scan/test_relative_buy.py tests/scan/test_config_knobs.py
git commit -m "feat(e6): v4.0 — entry hard gate + A/R tiers behind relative_buy.tiering; pool back to finalists

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 20: brief/relative_facts 的 A/R 文案

**Files:**
- Modify: `autoresearch/scan/relative_facts.py`（加 `tier`）
- Modify: `autoresearch/scan/brief.py:474-480`（tag 文案）
- Test: `tests/scan/test_brief.py`

- [ ] **Step 1: 写失败测试（追加）**

```python
def test_buy_line_prints_tier_label(scan_with_buy):
    from autoresearch.scan import brief as B
    p = scan_with_buy / "_relative_buy_decision.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["buys"][0].update({"tier": "R", "basis": "relative_forced"})
    doc["tiering"], doc["tier_counts"] = True, {"A": 0, "R": 2}
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    text = B.render(scan_with_buy)
    assert "🟥 **R 级·卡面无买点·强制相对(裁定①)**" in text
    doc["buys"][0].update({"tier": "A", "basis": "card_backed"})
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    assert "**A 级·卡面允许入场**" in B.render(scan_with_buy)
```

- [ ] **Step 2: 实现**

`relative_facts.relative_facts` 返回字典加 `"tier": (buys[0].get("tier") if buys else None),`。`brief._buy_lines` 在 `tag = (...)` 之后加：

```python
    tier = rel.get("tier")
    if tier == "A":
        tag += " · **A 级·卡面允许入场**"
    elif tier == "R":
        tag += " · 🟥 **R 级·卡面无买点·强制相对(裁定①)**"
```

并新增一行 `_src(src, "relative.tier", tier, DECISION_FILENAME, "buys[0].tier", text)`（放在既有 `relative.code` 那行旁）。

- [ ] **Step 3: 跑绿并提交**

Run: `uv run --no-sync python -m pytest -q tests/scan/test_brief.py tests/scan/test_self_review_brief.py`

```bash
git add autoresearch/scan/relative_facts.py autoresearch/scan/brief.py tests/scan/test_brief.py
git commit -m "feat(brief): BUY line shows the E6 tier (A card-backed / R forced relative)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 21: 不可买归因 `scan/buyability.py` + 登记 + post_run 接线 + brief 归因行

**Files:**
- Create: `autoresearch/scan/buyability.py`
- Modify: `autoresearch/contracts/artifacts.py`（登记 `_buyability.json`）、`autoresearch/scan/post_run.py:867`（决策写完后调用）、`autoresearch/scan/brief.py`（facts + 白名单 + ③ 一行）
- Test: `tests/scan/test_buyability.py`（新）、`tests/scan/test_brief.py`

**Interfaces:**
- Produces: `build_buyability(scan_dir: Path) -> dict`（schema 见 spec §2.7）；`write_buyability(scan_dir) -> Path`；`safe_write_buyability(scan_dir) -> Path | None`（不抛）；`BUYABILITY_FILENAME = "_buyability.json"`；`WALLS = ("menu", "cards", "gates", "none")`；`MENU_KNIFE_TOLERANCE = 0.06`。

- [ ] **Step 1: 写失败测试 `tests/scan/test_buyability.py`**

```python
"""不可买归因(2026-09-24 §2.7):三堵墙 menu / cards / gates,第一堵撞上的就是 wall。"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from autoresearch.scan.buyability import BUYABILITY_FILENAME, build_buyability, write_buyability


def _frame(n, knife_share, healthy_share):
    k = int(n * knife_share); h = int(n * healthy_share)
    return pd.DataFrame({"code": [f"{600000 + i:06d}" for i in range(n)], "industry": "电力",
                         "pct_60d": [-30.0] * k + [10.0] * (n - k),
                         "main_net_ratio": [-0.01] * (n - h) + [0.02] * h,
                         "cmf_20": [-0.1] * (n - h) + [0.1] * h})


def _scan(tmp_path, *, l2_knife=0.40, l0_knife=0.23, stances=("ALLOWED",), gates_ok=True, tier="A", blind=0):
    scan = tmp_path / "2026-09-17"; scan.mkdir()
    _frame(1000, l0_knife, 0.11).to_csv(scan / "L1_scored_full.csv", index=False)
    l2 = _frame(200, l2_knife, 0.08); l2["sector_seat"] = False
    l2.to_csv(scan / "L2_gbdt_top200.csv", index=False)
    pd.DataFrame({"code": ["600001"], "guard": ["composite_seat"]}).to_csv(scan / "finalists.csv", index=False)
    cands = [{"code": f"6000{i:02d}", "eligible": gates_ok, "pinned": False,
              "hard_gate": {"tradable": True, "data_a": gates_ok, "contract": True, "no_redflag": True},
              "card_context": {"entry_stance": s}} for i, s in enumerate(stances)]
    doc = {"blocked": not gates_ok, "buys": ([{"code": "600000", "tier": tier}] if gates_ok else []),
           "candidates": cands,
           "excluded": ([] if gates_ok else [{"code": c["code"], "reason": "hard_gate.data_a", "detail": f"stage l4_{c['code']} 失败(票级 data_a)"} for c in cands]),
           "veto_accounting": {"by_gate": {"data_a": 0 if gates_ok else len(cands), "contract": 0, "no_redflag": 0, "tradable": 0}}}
    (scan / "_relative_buy_decision.json").write_text(json.dumps(doc), encoding="utf-8")
    if blind:
        (scan / "_blind_cards.json").write_text(json.dumps({f"7000{i:02d}": {} for i in range(blind)}), encoding="utf-8")
    return scan


def test_wall_menu_when_l2_knife_exceeds_l0_plus_tolerance(tmp_path):
    doc = build_buyability(_scan(tmp_path, l2_knife=0.40, l0_knife=0.23))
    assert doc["wall"] == "menu" and doc["menu"]["l2_knife"] == 0.40 and doc["menu"]["composite_seats"] == 1


def test_wall_cards_when_menu_ok_but_no_allowed_card(tmp_path):
    doc = build_buyability(_scan(tmp_path, l2_knife=0.25, stances=("CONDITIONAL", "UNKNOWN", "PROHIBITED"), tier="R"))
    assert doc["wall"] == "cards" and doc["cards"] == {"n": 3, "allowed": 0, "conditional": 1, "prohibited": 1, "unknown": 1, "blind": 0}


def test_wall_gates_when_allowed_cards_all_vetoed(tmp_path):
    doc = build_buyability(_scan(tmp_path, l2_knife=0.25, stances=("ALLOWED",), gates_ok=False))
    assert doc["wall"] == "gates" and doc["gates"]["data_a_ticker"] == 1 and doc["buy"]["blocked"] is True


def test_wall_none_when_a_tier_buy_exists(tmp_path):
    doc = build_buyability(_scan(tmp_path, l2_knife=0.25, stances=("ALLOWED",), tier="A"))
    assert doc["wall"] == "none" and doc["buy"] == {"tier": "A", "code": "600000", "blocked": False}


def test_write_buyability_lands_the_file(tmp_path):
    scan = _scan(tmp_path, l2_knife=0.25)
    p = write_buyability(scan)
    assert p == scan / BUYABILITY_FILENAME and json.loads(p.read_text(encoding="utf-8"))["schema_version"] == 1
```

- [ ] **Step 2: 跑红** — `uv run --no-sync python -m pytest -q tests/scan/test_buyability.py`

- [ ] **Step 3: 实现 `autoresearch/scan/buyability.py`**

```python
#!/usr/bin/env python3
"""不可买归因(2026-09-24 可买性对齐 §2.7;裁定④「把为什么不可买解掉」的可见面)。

零 LLM,`post_run.observe` 在 `relative_buy` 决策写完之后跑。三堵墙按序判定,第一堵撞上的就是
`wall`:menu(L2 落刀 > L0 + 6pp 或 L2 健康 < L0 健康)→ cards(菜单过关但没有一张卡允许入场)→
gates(有允许入场的卡但全被硬门否决)→ none(出了 A 级)。产物 `_buyability.json`;brief ③ 印一行。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

from autoresearch.common.scoring import falling_knife_mask, healthy_riser_mask

BUYABILITY_FILENAME = "_buyability.json"
WALLS = ("menu", "cards", "gates", "none")
MENU_KNIFE_TOLERANCE = 0.06          # 与 spec §3.1 A4 同一门:豁免两桶 ≤12 行 = 6pp


def _csv(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    try:
        return pd.read_csv(path, dtype={"code": str})
    except Exception:  # noqa: BLE001 — 坏文件按缺席处理,不猜
        return None


def _json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return doc if isinstance(doc, dict) else None


def _share(mask: pd.Series | None) -> float | None:
    if mask is None or not len(mask):
        return None
    return round(float(mask.fillna(False).mean()), 4)


def build_buyability(scan_dir: Path | str) -> dict:
    scan = Path(scan_dir)
    l0, l2 = _csv(scan / "L1_scored_full.csv"), _csv(scan / "L2_gbdt_top200.csv")
    fins = _csv(scan / "finalists.csv")
    decision = _json(scan / "_relative_buy_decision.json") or {}
    blind = _json(scan / "_blind_cards.json") or {}

    menu = {
        "l2_knife": _share(falling_knife_mask(l2)) if l2 is not None else None,
        "l0_knife": _share(falling_knife_mask(l0)) if l0 is not None else None,
        "l2_healthy": _share(healthy_riser_mask(l2)) if l2 is not None else None,
        "l0_healthy": _share(healthy_riser_mask(l0)) if l0 is not None else None,
        "sector_seats": int(l2["sector_seat"].fillna(False).astype(bool).sum())
        if l2 is not None and "sector_seat" in l2.columns else 0,
        "composite_seats": int((fins["guard"].astype(str) == "composite_seat").sum())
        if fins is not None and "guard" in fins.columns else 0,
    }
    cands = [c for c in (decision.get("candidates") or []) if isinstance(c, dict) and not c.get("pinned")]
    stance = [str((c.get("card_context") or {}).get("entry_stance") or "UNKNOWN") for c in cands]
    cards = {"n": len(cands), "allowed": stance.count("ALLOWED"), "conditional": stance.count("CONDITIONAL"),
             "prohibited": stance.count("PROHIBITED"),
             "unknown": len(stance) - stance.count("ALLOWED") - stance.count("CONDITIONAL") - stance.count("PROHIBITED"),
             "blind": len(blind)}
    by_gate = ((decision.get("veto_accounting") or {}).get("by_gate") or {})
    excluded = decision.get("excluded") or []
    data_a_ticker = sum(1 for e in excluded if e.get("reason") == "hard_gate.data_a" and "票级" in str(e.get("detail", "")))
    gates = {"data_a_day": max(0, int(by_gate.get("data_a", 0)) - data_a_ticker), "data_a_ticker": data_a_ticker,
             "contract": int(by_gate.get("contract", 0)), "no_redflag": int(by_gate.get("no_redflag", 0))}
    buys = decision.get("buys") or []
    buy = {"tier": (buys[0].get("tier") if buys else None), "code": (buys[0].get("code") if buys else None),
           "blocked": bool(decision.get("blocked"))}

    menu_bad = ((menu["l2_knife"] is not None and menu["l0_knife"] is not None
                 and menu["l2_knife"] > menu["l0_knife"] + MENU_KNIFE_TOLERANCE)
                or (menu["l2_healthy"] is not None and menu["l0_healthy"] is not None
                    and menu["l2_healthy"] < menu["l0_healthy"]))
    allowed_eligible = any(c.get("eligible") and str((c.get("card_context") or {}).get("entry_stance")) == "ALLOWED"
                           for c in cands)
    if menu_bad:
        wall = "menu"
    elif cards["allowed"] == 0:
        wall = "cards"
    elif not allowed_eligible:
        wall = "gates"
    else:
        wall = "none"
    return {"schema_version": 1, "date": scan.name, "menu": menu, "cards": cards, "gates": gates,
            "buy": buy, "wall": wall}


def write_buyability(scan_dir: Path | str) -> Path:
    scan = Path(scan_dir)
    doc = build_buyability(scan)
    path = scan / BUYABILITY_FILENAME
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return path


def safe_write_buyability(scan_dir: Path | str) -> Path | None:
    """观测腿:失败只打一行,不阻断发布。"""
    try:
        return write_buyability(scan_dir)
    except Exception as exc:  # noqa: BLE001 — 观测腿不得让 observe 失败,但必须留痕
        print(f"[buyability] 写入失败: {exc!r}", file=sys.stderr)
        return None
```

`test_wall_none_when_a_tier_buy_exists` 里 `data_a_ticker` 的判定依赖 Task 1 的 detail 文案含「票级」——保持一致。

- [ ] **Step 4: 登记 + 接线 + brief**

`artifacts.py` 在 `relative_buy_decision` 那行后加：

```python
    Artifact("buyability", "_buyability.json", "staging", "observe", "buyability", "json", "always"),
```

`post_run.py` 在 `safe_verify_decision(...)`/`safe_write_decision(...)` 的 if/else 之后加：

```python
    from autoresearch.scan.buyability import safe_write_buyability
    safe_write_buyability(scan)          # 不可买归因(2026-09-24 §2.7):读决策文件,必须在它之后
```

`brief.py`：`_WHITELIST_SPEC` 加 `("artifact", "buyability")`；`collect_facts` 加 `"buyability": _json(scan / "_buyability.json") or {},`；`_buy_lines` 末尾（`return lines` 前）加：

```python
    ba = facts.get("buyability") or {}
    if ba.get("wall"):
        m, c, g = ba.get("menu") or {}, ba.get("cards") or {}, ba.get("gates") or {}
        pct = lambda v: "—" if v is None else f"{v:.0%}"   # noqa: E731
        text = (f"不可买归因:**{ba['wall']}** ｜ 菜单 落刀 L2 {pct(m.get('l2_knife'))}/L0 {pct(m.get('l0_knife'))}"
                f" · 健康 {pct(m.get('l2_healthy'))}/{pct(m.get('l0_healthy'))} · 席位 行业 {m.get('sector_seats', 0)}/证据 {m.get('composite_seats', 0)}"
                f" ｜ 卡 允许 {c.get('allowed', 0)}/条件 {c.get('conditional', 0)}/禁止 {c.get('prohibited', 0)}/未知 {c.get('unknown', 0)}/盲 {c.get('blind', 0)}"
                f" ｜ 门 data_a 日级 {g.get('data_a_day', 0)}·票级 {g.get('data_a_ticker', 0)} · contract {g.get('contract', 0)} · redflag {g.get('no_redflag', 0)}")
        _src(src, "buyability.wall", ba["wall"], "_buyability.json", "wall", text)
        _src(src, "buyability.l2_knife", m.get("l2_knife"), "_buyability.json", "menu.l2_knife", text)
        _src(src, "buyability.allowed", c.get("allowed"), "_buyability.json", "cards.allowed", text)
        lines.append("  " + text)
```

（brief 有 ≤3000B 预算 lint：这一行约 220B，若 `test_brief` 的字节预算测试红了，把「席位」段落删掉先保四个主数。）`tests/scan/test_brief.py` 加一个用例：fixture 写入 `_buyability.json`（`wall="cards"`）→ 渲染含「不可买归因:**cards**」。

- [ ] **Step 5: 跑绿并提交**

Run: `uv run --no-sync python -m pytest -q tests/scan/test_buyability.py tests/scan/test_brief.py tests/scan/test_self_review_brief.py tests/contracts -q tests/scan -k post_run`

```bash
git add autoresearch/scan/buyability.py autoresearch/contracts/artifacts.py autoresearch/scan/post_run.py autoresearch/scan/brief.py tests/scan/test_buyability.py tests/scan/test_brief.py
git commit -m "feat(scan): buyability attribution (menu/cards/gates walls) written after the E6 decision and printed in brief ③

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 22: 账本 `buy_tier` + runs.csv `n_buy_a`/`wall` + `e6_a_tier_day_share`

**Files:**
- Modify: `autoresearch/scan/outcome.py:120-140`（`LEDGER_COLUMNS`）、`:258-262`（`run_facts`）、`:651-654`（行复制键）
- Modify: `autoresearch/scan/ledger_views.py:105-114`（`RUNS_COLUMNS`）、`:239-256`（`_run_counts`）、`:299-310`（行组装）
- Modify: `autoresearch/scan/populations.py:143-147`（`FLAG_COLUMNS` 旁加 `buy_tier`）、`:500-508`（`_e6_index` 返回 tier）、`:668-682`（行字典）、`:1078-1082`（新指标）
- Test: `tests/scan/test_outcome_buy_tier.py`（新）、`tests/scan/test_ledger_views*.py`、`tests/scan/test_populations*.py`（既有文件名以 `ls tests/scan | grep -i "ledger_views\|populations"` 为准）

**Interfaces:**
- Produces: 账本列 `buy_tier`（A/R/空）；`runs.csv` 列 `n_buy_a`、`wall`；`stage_rulers.csv` 行 `E6/e6_a_tier_day_share`（value = 该日 BUY 是否 A 级，ALL 行 = 池化均值）。

- [ ] **Step 1: 写失败测试 `tests/scan/test_outcome_buy_tier.py`**

```python
import json


def _run(tmp_path, tier):
    run = tmp_path / "20260917-0917_2152"
    st = run / "trace" / "staging"; st.mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps({"analysis_date": "2026-09-17"}), encoding="utf-8")
    st.joinpath("finalists.csv").write_text("code,name,sector,guard,lane\n600001,甲,电力,,value\n", encoding="utf-8")
    st.joinpath("_relative_buy_decision.json").write_text(json.dumps({
        "mode": "active", "rule_version": "e6.v4.0",
        "buys": [{"code": "600001", "tier": tier, "basis": "card_backed", "rank": 1}],
        "candidates": [{"code": "600001", "rank": 1, "eligible": True}]}), encoding="utf-8")
    st.joinpath("_buyability.json").write_text(json.dumps({"wall": "none"}), encoding="utf-8")
    return run


def test_run_facts_carries_buy_tier(tmp_path):
    from autoresearch.scan.outcome import LEDGER_COLUMNS, run_facts
    row = run_facts(_run(tmp_path, "A"))["rows"]["600001"]
    assert row["e6_buy"] is True and row["buy_tier"] == "A"
    assert "buy_tier" in LEDGER_COLUMNS


def test_runs_view_counts_a_tier_and_wall(tmp_path):
    from autoresearch.scan.ledger_views import RUNS_COLUMNS, _run_counts, _wall_of
    assert {"n_buy_a", "wall"} <= set(RUNS_COLUMNS)
    run = _run(tmp_path, "A")
    assert _run_counts(run, "FULL") == (1, 1, 1)
    assert _wall_of(run) == "none"
```

- [ ] **Step 2: 跑红** — `uv run --no-sync python -m pytest -q tests/scan/test_outcome_buy_tier.py`

- [ ] **Step 3: 实现**

`outcome.py`：`LEDGER_COLUMNS` 在 `"e6_buy"` 后加 `"buy_tier"`；`run_facts` 里 `buys = {...}` 旁加 `tiers = {_z6(b.get("code")): b.get("tier") for b in (decision.get("buys") or [])}`，循环末尾 `row["e6_buy"] = code in buys` 后加 `row["buy_tier"] = tiers.get(code) if code in buys else None`；`:651-654` 的键元组加 `"buy_tier"`。

`ledger_views.py`：`RUNS_COLUMNS` 在 `"n_buy"` 后加 `"n_buy_a", "wall"`；`_run_counts` 返回三元组 `(n_finalist, n_buy, n_buy_a)`，其中 `n_buy_a = sum(1 for row in rows.values() if row.get("e6_buy") and row.get("buy_tier") == "A")`，哨兵日 `(0, 0, 0)`，读不到 `(None, None, None)`；新增：

```python
def _wall_of(run_dir: Path) -> str | None:
    """`_buyability.json.wall`(2026-09-24 §2.7);缺 → None(老 run 没有它)。"""
    doc = _read_json(run_dir / "trace" / "staging", "_buyability.json")
    return str(doc.get("wall")) if isinstance(doc, dict) and doc.get("wall") else None
```

行组装改 `n_finalist, n_buy, n_buy_a = _run_counts(run, run_mode)`，字典加 `"n_buy_a": n_buy_a, "wall": _wall_of(run),`。（`_read_json(base, *names)` 是文件内既有读取原语；若签名不同照既有用法改。）

`populations.py`：`_e6_index` 返回 `(cand, buys, tiers, has)`（`tiers = {_z6(b["code"]): b.get("tier") for b in buys_list}`），调用点 `e6_cand, e6_buys, e6_tiers, has_e6 = _e6_index(src)`；行字典加 `"buy_tier": (e6_tiers.get(code) if (has_e6 and code in e6_buys) else None),`；`FLAG_COLUMNS` 不动（它是布尔旗集合），在建表列清单里加 `"buy_tier"`（字符串列；找到 `FLAG_COLUMNS` 被用来建 DataFrame 列的位置，把 `buy_tier` 加进非旗列清单）。指标：在 `rows += _paired_metric(sessions, "E6", "e6_buy_minus_pool", ...)` 之后加：

```python
    a_days = []                                       # E6 A 级天数占比(2026-09-24 §2.7 的成功尺)
    for session in sorted(sessions):
        t = sessions[session]
        buy_rows = t[t["is_buy"].fillna(False).astype(bool)]
        if not len(buy_rows) or "buy_tier" not in t.columns:
            continue
        a_days.append({"session": session, "value": float((buy_rows["buy_tier"] == "A").any()),
                       "n_names": int(len(buy_rows)), "coverage": None})
    rows += _ratio_metric(a_days, "E6", "e6_a_tier_day_share")
```

- [ ] **Step 4: 跑绿并提交**

Run: `uv run --no-sync python -m pytest -q tests/scan/test_outcome_buy_tier.py tests/scan -k "outcome or ledger_views or populations"`

```bash
git add autoresearch/scan/outcome.py autoresearch/scan/ledger_views.py autoresearch/scan/populations.py tests/scan/test_outcome_buy_tier.py
git commit -m "feat(ledger): buy_tier column, runs.csv n_buy_a/wall, E6 a-tier day share ruler

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 23: 文档同步 + 批 3 收尾 + 批 4 真跑验收清单

**Files:**
- Modify: `.claude/skills/scan-market/STAGES.md`（L1 权重来源、L2 帽/floor/席位、L3 🏭、E6 v4 分级、brief 归因行）
- Modify: `.claude/skills/scan-market/SKILL.md`（配置节新键；步骤 5 观测段提一句 `_buyability.json`）
- Modify: `AGENTS.md`（若有 L4 卡契约行清单，加入场行一句）
- Test: `tests/test_skill_docs_refs.py`（文档引用探针）

- [ ] **Step 1: 改文档**

STAGES.md 各节各加一段（≤6 行），内容照 spec §2.1–§2.7 的「改什么」摘要；SKILL.md 配置节列出 `funnel.weight_profile`、`funnel.preference_weights`、`l2.knife_cap`、`l2.floors`、`l2.sector_seats`、`relative_buy.tiering` 六键与各自回滚杆。

- [ ] **Step 2: 全量绿 + ruff**

Run: `uv run --no-sync python -m pytest -q tests/ && uv run --no-sync ruff check autoresearch tests`
Expected: 全绿、ruff 0。

- [ ] **Step 3: 变异探针抽查（每批一次，批 3 补做）**

任选三处：① 注释掉 `_hard_gate` ④ 的入场门一行 → `test_tiering_prohibited_card_is_hard_gated_out` 必红；② `KNIFE_CAP_EXEMPT_STYLES` 改成空集 → `test_knife_cap_limits_merit_and_backfill_but_exempts_reversal_buckets` 是否仍绿（若仍绿，给豁免桶补一条断言）；③ `preference_weights_doc` 去掉键集校验 → `test_preference_weights_doc_shape_and_validation` 必红。改回后再跑一遍全量。

- [ ] **Step 4: 提交**

```bash
git add .claude/skills/scan-market/STAGES.md .claude/skills/scan-market/SKILL.md AGENTS.md
git commit -m "docs(scan): buyability realignment — weights profile, L2 cap/floors/seats, card entry line, E6 tiers, attribution

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 5: 批 4 真跑验收（不是代码任务；10 个成功扫描日，两引擎各自记）**

每场跑完读 `brief.md` ③ 的归因行与 `_ledger/views/runs.csv`：

| 门 | 判据 | 读哪 |
|---|---|---|
| L1 | A 级 BUY 天数 ≥ 5/10 | `runs.csv.n_buy_a`、`stage_rulers.csv` 的 `E6/e6_a_tier_day_share` |
| L2 | `wall=="menu"` ≤ 2/10 | `runs.csv.wall` |
| L3 | finalist 隔夜 \|gap\| 90 分位 > 1.0pp | `recommendations.csv.gap_c1_o2`（role=finalist） |
| L4 | L2 落刀 ≤ L0 落刀 + 6pp | `_buyability.json.menu` |
| L5 | 单场成本 ≤ $46 中位 | `token_usage.md` |
| L6 | `conflicts.card_says_prohibited` = 0 | `_relative_buy_decision.json` |

期间不改规则;任一门连续 3 场不达标 → 停下来把读数交给用户裁,不自行调参。
