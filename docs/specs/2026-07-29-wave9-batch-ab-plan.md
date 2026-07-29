# Wave9 批A+批B 实施计划 —— P0 修缮 × 新闻完备 × 研报纵深

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把设计稿 `docs/specs/2026-07-29-wave9-report-depth-options-harness-design.md` 的批A(P0 修缮)与批B(新闻完备 + 研报级增强卡)落成代码。

**Architecture:** 三条线并行不交叉——(1) 数据面:公告流补一条 B 级兜底源,把「无权限 = 静默盲」变成「兜底承载 + 显式记账」;(2) 决策呈现面:tripwire 与 LLM 终评冲突时渲染结构化对峙框,系统不合并;(3) 研究深度面:退役 TTL 复用(新闻永远新鲜)、intel 超帽从整稿拒改按时效裁剪、满卡/早停卡增档案研报体。全部新逻辑落确定性 Python 模块 + pytest,LLM 侧只改 agent def 模板与任务包内容。

**Tech Stack:** Python 3.13 / pandas / pytest;`uv run --no-sync` 跑一切(venv-only 依赖 akshare/tushare/lightgbm);akshare(公告兜底源);markdown 契约文件(`.claude/agents/*.md`);workflow JS(`.claude/workflows/scan-market.js`)。

## Global Constraints

- **一切命令用 `uv run --no-sync`**,仓库根目录执行。禁止 `pip install` / `uv sync`(会删 venv-only 的 akshare/tushare/lightgbm)。
- **确定性层零 LLM**:本计划新增的全部 Python 模块不得调用任何 LLM;不编数、不预测。
- **数据契约分级**:A 级空 → 抛 `DataContractError` 阻断;**B 级空 → 降级但必须记账**(写 `_degraded` / run_health,禁止静默降级)。本计划新增数据源全部 **B 级**。
- **lake cache key 一律剥 `fields`**(窄表毒化前科:cache key 含 fields → 整组 NaN 静默失真)。
- **先补指令,再加严检查**:任何新 lint/probe 落地前,先把对应要求写进 `.claude/agents/*.md`;升格新契约必须同批调整行数/格式预算,否则预算会把新契约挤掉。
- **变异测试是验收硬要求**:每个新 probe/lint/gate 落地后,必须手工删掉被测行为跑一次,确认测试**变红**;不变红的测试零鉴别力,视为未完成。
- **性能/回滚开关不拥有评级**:任何开关都不得改 finalist cap、rubric 三门、`fwd_2_oc` 主尺或 BUY 数量。
- **A股代码归一走 `symbol_utils.to_ts_code`** 单一事实源(92xxxx 北交所坑反复复发)。
- **提交粒度**:每个 Task 末尾提交一次,commit message 末尾附
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`。
- **跑测试**:`uv run --no-sync pytest tests/ -q`;单文件 `uv run --no-sync pytest tests/scan/test_xxx.py -v`。**禁止 `pytest | tail`**(管道吞退出码)。
- 基线:本计划开工前 `git log -1` = `6e409b7`(Wave9 设计稿),测试全绿。

---

## File Structure

**新建:**
| 文件 | 职责 |
|---|---|
| `autoresearch/data/sources/anns_fallback.py` | 公告兜底源:akshare 拉公告标题流,归一成 L3_news 同构行 |
| `autoresearch/scan/l2_knife_audit.py` | L2 菜单落刀率归因取证(零 LLM,只出报表不改采样) |
| `tests/data/test_anns_fallback.py` | 兜底源归一/降级/空表契约 |
| `tests/scan/test_anns_probe.py` | anns 双源 probe 三态(expected/info/warn) |
| `tests/scan/test_tripwire_conflict.py` | 冲突判据 + 冲突框渲染 |
| `tests/scan/test_l2_knife_audit.py` | 落刀归因分解数学 |
| `tests/scan/test_yesterday_echo.py` | 昨卡回声抽取与注入位置 |
| `tests/scan/test_intel_trim.py` | intel 按时效窗裁剪 |
| `tests/scan/test_dossier_research_body.py` | 研报体 lint(有档案必有段/无档案必有声明行) |
| `tests/scan/test_news_headline.py` | 📰 头行渲染 |

**修改:**
| 文件 | 改什么 |
|---|---|
| `autoresearch/data/sources.py` → `sources/__init__.py` | **100% rename 转包**(0 行内容差异)——原计划误把 `data/sources` 当成包,它其实是平模块,不转包则 `sources/anns_fallback.py` 永远导不进来。4 处既有调用方(dossier/mainbz·prefetch·reconcile、data/cache)须回归验证 |
| `autoresearch/scan/health.py:56` | `anns_empty_rate` 旁增 `anns_source_status()`;`run_health` 增键 |
| `autoresearch/learning/self_review.py:333,484` | probe 4 改口三态 + docstring;新增研报体探针 |
| `autoresearch/scan/report_sections.py:329` | `_pinned_section` 增冲突框 |
| `autoresearch/scan/decision_finalize.py` | 新增 `tripwire_conflicts()` |
| `autoresearch/learning/pinned_ledger.py:23` | `_COLS` 增 `conflict` |
| `.claude/workflows/scan-market.js:251,258-311` | 删 `l4_reuse --apply`、删 `reused` 契约 |
| `autoresearch/scan/l4/dispatch.py` | `dispatch_plan` 删复用分流 |
| `autoresearch/scan/l4/prompts.py:51` | 昨卡回声块 + 档案四节内联 |
| `autoresearch/scan/l4/intel_guard.py:44` | 拒稿改裁稿 |
| `autoresearch/scan/publisher.py:19` | 📰 头行注入 |
| `.claude/agents/l4-card.md:87,124` | 研报体模板 + 行数预算 |
| `autoresearch/scan/post_run.py` | finalist 无档案插队建档 |
| `.claude/skills/scan-market/SKILL.md` + `STAGES.md` | 步骤 4 删复用行、复用节改退役 |

---

## Task 1: 公告兜底源(A-1 上半)

**Files:**
- Create: `autoresearch/data/sources/anns_fallback.py`
- Test: `tests/data/test_anns_fallback.py`

**Interfaces:**
- Produces: `fetch_anns(code6: str, date: str, *, limit: int = 20) -> list[dict]` —— 返回与 `L3_news/<code>.json` 同构的行 `{"ann_date": "YYYYMMDD", "title": str, "source": "em"}`;取数失败/无数据 → `[]`(B 级降级,不抛)。
- Produces: `SOURCE_TAG` —— **兜底源供应商标识**。值必须与实际连的接口一致:
  巨潮 `stock_zh_a_disclosure_report_cninfo` → `"cninfo"`;东财 → `"em"`。
  (本仓库既有惯例里 `"em"` 专指东财,见 `scan/universe.py` 的 `--source` choices;
  标签写错等于给未来的供应商质量归因埋假数据。)

- [ ] **Step 1: 写失败测试**

```python
# tests/data/test_anns_fallback.py
import pandas as pd
from autoresearch.data.sources import anns_fallback as af


def test_normalizes_rows_to_l3news_shape(monkeypatch):
    df = pd.DataFrame([
        {"代码": "000651", "名称": "格力电器", "公告标题": "关于回购股份的进展公告",
         "公告日期": "2026-07-29"},
        {"代码": "000651", "名称": "格力电器", "公告标题": "2026 年半年度报告披露提示",
         "公告日期": "2026-07-28"},
    ])
    monkeypatch.setattr(af, "_raw_notices", lambda code6, date: df)
    rows = af.fetch_anns("000651", "2026-07-29")
    tag = af.SOURCE_TAG
    assert rows == [
        {"ann_date": "20260729", "title": "关于回购股份的进展公告", "source": tag},
        {"ann_date": "20260728", "title": "2026 年半年度报告披露提示", "source": tag},
    ]


def test_drops_lookahead_rows(monkeypatch):
    """as-of 铁律:晚于分析日的公告一律丢弃(前视污染)。"""
    df = pd.DataFrame([
        {"公告标题": "未来公告", "公告日期": "2026-07-30"},
        {"公告标题": "当日公告", "公告日期": "2026-07-29"},
    ])
    monkeypatch.setattr(af, "_raw_notices", lambda code6, date: df)
    rows = af.fetch_anns("000651", "2026-07-29")
    assert [r["title"] for r in rows] == ["当日公告"]


def test_degrades_to_empty_on_source_error(monkeypatch):
    """B 级契约:取数炸了返回空,不抛异常阻断漏斗。"""
    def boom(code6, date):
        raise RuntimeError("network down")
    monkeypatch.setattr(af, "_raw_notices", boom)
    assert af.fetch_anns("000651", "2026-07-29") == []


def test_empty_frame_returns_empty(monkeypatch):
    monkeypatch.setattr(af, "_raw_notices", lambda code6, date: pd.DataFrame())
    assert af.fetch_anns("000651", "2026-07-29") == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest tests/data/test_anns_fallback.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autoresearch.data.sources.anns_fallback'`

- [ ] **Step 3: 写实现**

```python
# autoresearch/data/sources/anns_fallback.py
#!/usr/bin/env python3
"""公告标题流 **B 级兜底源**(Wave9 A-1)。

主源 tushare `anns_d` 自 2026-07-18 起无接口权限,`L3_news/` 整目录空稿
(2026-07-29 实测 `anns_empty_rate=1.0`),而 self_review 把它当 expected 放行 ——
"无权限"与"当日故障"在产物上长得一模一样,公告面**静默单腿**。

本模块提供 akshare 侧兜底:拉个股公告标题,归一成 `L3_news/<code>.json` 同构行。
**B 级契约**:取不到就返回空 + 由调用方记账降级,绝不抛异常阻断漏斗。
"""
from __future__ import annotations

import contextlib

SOURCE_TAG = "em"


def _raw_notices(code6: str, date: str):
    """原始取数(测试 monkeypatch 此函数;真身走 akshare)。"""
    import akshare as ak
    return ak.stock_notice_report(symbol="全部", date=date.replace("-", ""))


def _pick(row: dict, *keys: str) -> str:
    for k in keys:
        v = row.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def fetch_anns(code6: str, date: str, *, limit: int = 20) -> list[dict]:
    """→ `[{"ann_date": "YYYYMMDD", "title": str, "source": "em"}, ...]`,失败/无数据 → `[]`。

    as-of 铁律:`ann_date > date` 的行一律丢弃(前视污染)。
    """
    cut = date.replace("-", "")
    try:
        df = _raw_notices(code6, date)
    except Exception:  # noqa: BLE001 — B 级源:取数失败降级为空,由调用方记账
        return []
    if df is None or not len(df):
        return []

    rows: list[dict] = []
    for rec in df.to_dict("records"):
        title = _pick(rec, "公告标题", "title", "名称")
        raw_date = _pick(rec, "公告日期", "ann_date", "日期")
        if not title or not raw_date:
            continue
        ann = raw_date.replace("-", "")[:8]
        if not ann.isdigit() or ann > cut:      # as-of ≤ 分析日
            continue
        rows.append({"ann_date": ann, "title": title, "source": SOURCE_TAG})
        if len(rows) >= limit:
            break
    return rows


def probe() -> dict:
    """冒烟自检:接口在不在、字段名对不对。CLI `python -m ...anns_fallback` 调。"""
    out = {"ok": False, "source": SOURCE_TAG, "columns": [], "error": None}
    with contextlib.suppress(Exception):
        import akshare as ak
        df = ak.stock_notice_report(symbol="全部", date="20260729")
        out["columns"] = list(df.columns)[:12]
        out["ok"] = bool(len(df))
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(probe(), ensure_ascii=False))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run --no-sync pytest tests/data/test_anns_fallback.py -v`
Expected: 4 passed

- [ ] **Step 5: 真接口冒烟(开放问题 §11-2 裁决点)**

Run: `uv run --no-sync python -m autoresearch.data.sources.anns_fallback`
Expected: 打印 JSON。**若 `ok=false` 或 `columns` 与 `_pick` 的候选键名对不上** → 换巨潮接口 `ak.stock_zh_a_disclosure_report_cninfo`,改 `_raw_notices` 与 `_pick` 键名,重跑 Step 4。**把实测到的 columns 抄进模块 docstring**(下次不用再猜)。

- [ ] **Step 6: 提交**

```bash
git add autoresearch/data/sources/anns_fallback.py tests/data/test_anns_fallback.py
git commit -m "feat(data): 公告标题流 B 级兜底源(akshare)—— 主源无权限时不再静默单腿(W9-A1a)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: anns 双源探针改口 + 权限提醒(A-1 下半)

**Files:**
- Modify: `autoresearch/scan/health.py`(`anns_empty_rate` 后新增 `anns_source_status`;`run_health` dict 增键)
- Modify: `autoresearch/learning/self_review.py:352`(docstring 第 4 条)、`:484-492`(probe 实现)
- Test: `tests/scan/test_anns_probe.py`

**Interfaces:**
- Consumes: Task 1 的 `anns_fallback.SOURCE_TAG`。
- Produces: `health.anns_source_status(scan_dir) -> dict` = `{"primary_empty_rate": float|None, "fallback_rows": int, "status": "ok"|"fallback"|"blind"}`。
  - `ok` = 主源有料;`fallback` = 主源空但兜底行 >0;`blind` = 双源皆空。
- Produces: `run_health` 新键 `anns_source_status`(同上 dict)。

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_anns_probe.py
import json

from autoresearch.learning.self_review import product_shape_lint
from autoresearch.scan import health


def _mk(tmp_path, news: dict[str, list]):
    d = tmp_path / "2026-07-29"
    (d / "L3_news").mkdir(parents=True)
    for code, rows in news.items():
        (d / "L3_news" / f"{code}.json").write_text(
            json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return d


def test_status_ok_when_primary_has_rows(tmp_path):
    d = _mk(tmp_path, {"000651": [{"ann_date": "20260729", "title": "x"}]})
    st = health.anns_source_status(d)
    assert st["status"] == "ok"


def test_status_fallback_when_only_fallback_rows(tmp_path):
    from autoresearch.data.sources.anns_fallback import SOURCE_TAG
    d = _mk(tmp_path, {"000651": [{"ann_date": "20260729", "title": "x", "source": SOURCE_TAG}],
                       "000333": []})
    st = health.anns_source_status(d)
    assert st["status"] == "fallback"
    assert st["fallback_rows"] == 1


def test_status_blind_when_both_empty(tmp_path):
    d = _mk(tmp_path, {"000651": [], "000333": []})
    st = health.anns_source_status(d)
    assert st["status"] == "blind"
    assert st["primary_empty_rate"] == 1.0


def test_probe_warns_on_blind_not_info(tmp_path):
    """核心回归:双源皆空必须是 warn,不再是 info/expected 放行。"""
    d = _mk(tmp_path, {"000651": [], "000333": []})
    (d / "run_health.json").write_text(json.dumps({
        "anns_empty_rate": 1.0,
        "anns_source_status": {"primary_empty_rate": 1.0, "fallback_rows": 0,
                               "status": "blind"}}), encoding="utf-8")
    hits = product_shape_lint(d, "2026-07-29")
    anns = [h for h in hits if "anns" in h["probe"]]
    assert anns and anns[0]["level"] == "warn"


def test_probe_info_when_fallback_carries(tmp_path):
    from autoresearch.data.sources.anns_fallback import SOURCE_TAG
    d = _mk(tmp_path, {"000651": [{"ann_date": "20260729", "title": "x", "source": SOURCE_TAG}]})
    (d / "run_health.json").write_text(json.dumps({
        "anns_empty_rate": 0.0,
        "anns_source_status": {"primary_empty_rate": 1.0, "fallback_rows": 1,
                               "status": "fallback"}}), encoding="utf-8")
    hits = product_shape_lint(d, "2026-07-29")
    anns = [h for h in hits if "anns" in h["probe"]]
    assert anns and anns[0]["level"] == "info"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest tests/scan/test_anns_probe.py -v`
Expected: FAIL — `AttributeError: module 'autoresearch.scan.health' has no attribute 'anns_source_status'`

> ⚠️ 跑之前先 `grep -n "probe" autoresearch/learning/self_review.py | head -3` 确认 `add()` 落的键名到底是 `probe` 还是别的;若不是 `probe`,把测试里的 `h["probe"]` 改成真实键名再跑 Step 2。

- [ ] **Step 3: 实现 `anns_source_status`**

在 `autoresearch/scan/health.py` 的 `anns_empty_rate` 函数后追加:

```python
def anns_source_status(scan_dir: Path) -> dict:
    """公告流双源状态(Wave9 A-1)。

    `anns_empty_rate` 只回答"空不空",回答不了"为什么空"——主源无权限与兜底也挂在产物
    上长得一样。本函数按行内 `source` 标签拆源:
      ok       = 有非兜底来源的行(主源活着)
      fallback = 主源无料但兜底源(`source=="em"`)扛住了
      blind    = 双源皆空 → **这是 warn,不是 expected**
    """
    from autoresearch.data.sources.anns_fallback import SOURCE_TAG as _FALLBACK_TAG

    d = Path(scan_dir) / "L3_news"
    files = sorted(d.glob("*.json")) if d.is_dir() else []
    if not files:
        return {"primary_empty_rate": None, "fallback_rows": 0, "status": "blind"}

    primary_rows = fallback_rows = 0
    empty_files = 0
    for p in files:
        try:
            v = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — 坏 JSON 记空
            v = []
        rows = v if isinstance(v, list) else []
        if not rows:
            empty_files += 1
        for r in rows:
            if isinstance(r, dict) and str(r.get("source", "")) == _FALLBACK_TAG:
                fallback_rows += 1
            else:
                primary_rows += 1

    if primary_rows:
        status = "ok"
    elif fallback_rows:
        status = "fallback"
    else:
        status = "blind"
    return {"primary_empty_rate": round(empty_files / len(files), 3),
            "fallback_rows": fallback_rows, "status": status}
```

在 `run_health` 组装处(`autoresearch/scan/health.py:715` 附近,`"anns_empty_rate": anns_rate,` 那一行下)加一行:

```python
            "anns_source_status": anns_source_status(scan_dir),
```

- [ ] **Step 4: 改 probe 判据**

`autoresearch/learning/self_review.py:484-492`,整段替换:

```python
    # 4) anns 双源探针(Wave9 A-1:存在性 ≠ 有效性 —— "无权限"曾与"当日故障"同形)
    st = health.get("anns_source_status") or {}
    status = str(st.get("status", "")) if st else ""
    if status == "blind":
        add("产物形状·anns双源盲", "warn",
            "公告面双源皆空(主源无权限 + 兜底源无料)—— 卡片公告证据仅剩 intel 单腿,"
            "非 expected;查兜底源可达性")
    elif status == "fallback":
        add("产物形状·anns兜底承载", "info",
            f"主源空,兜底源(em)承载 {st.get('fallback_rows', 0)} 行 —— 公告面在场,已记账")
    elif status == "" and health.get("anns_empty_rate") is not None:
        # 旧 run(无 anns_source_status 键)回落旧口径,不误报
        with contextlib.suppress(TypeError, ValueError):
            if float(health["anns_empty_rate"]) == 1.0:
                add("产物形状·anns去伪", "info",
                    "anns_empty_rate=1.0(旧 run 无双源状态键,按旧 expected 口径)")
```

同步改 `self_review.py:352` docstring 第 4 条:

```
    4. **anns 双源**(warn/info,Wave9 A-1):`anns_source_status.status`——`blind`(双源皆空)
       = **warn**,公告面只剩 intel 单腿;`fallback` = info(兜底承载,显式记账);`ok` 不出条。
       旧 run 无该键 → 回落 `anns_empty_rate` 旧 expected 口径,不追溯误报。
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run --no-sync pytest tests/scan/test_anns_probe.py tests/scan/test_health.py tests/scan/test_health_probes.py -v`
Expected: 全 passed(含旧 health 测试不回归)

- [ ] **Step 6: 变异测试(硬要求)**

把 Step 4 里 `if status == "blind":` 临时改成 `if False:`,跑
`uv run --no-sync pytest tests/scan/test_anns_probe.py::test_probe_warns_on_blind_not_info -v`
Expected: **FAIL**。确认后改回。若不 FAIL → 测试零鉴别力,回 Step 1 重写。

- [ ] **Step 7: 权限提醒行**

`autoresearch/scan/prelude.py` 汇总屏行渲染处,在 dossier_pool 行附近加(presence-gated,`status=="blind"` 才出):

```python
    # 📡 公告主源无权限提醒(Wave9 A-1;blind 才出,fallback/ok 不打扰)
    with contextlib.suppress(Exception):
        from autoresearch.scan.health import anns_source_status
        if anns_source_status(scan_dir).get("status") == "blind":
            lines.append("  📡 公告双源皆空 —— 主源 anns_d 无权限且兜底源无料;"
                         "查 `python -m autoresearch.data.sources.anns_fallback` 冒烟")
```

- [ ] **Step 8: 全量测试 + 提交**

Run: `uv run --no-sync pytest tests/ -q`
Expected: 全绿(基线 1952+ 通过,0 失败)

```bash
git add autoresearch/scan/health.py autoresearch/learning/self_review.py \
        autoresearch/scan/prelude.py tests/scan/test_anns_probe.py
git commit -m "fix(scan): anns 探针从「空=expected」改双源三态 —— blind 是 warn 不是放行(W9-A1b)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: tripwire vs LLM 终评冲突卡(A-2)

**Files:**
- Modify: `autoresearch/scan/decision_finalize.py`(新增 `tripwire_conflicts`)
- Modify: `autoresearch/scan/report_sections.py:329`(`_pinned_section` 增冲突框)、`:646`(调用点传参)
- Modify: `autoresearch/learning/pinned_ledger.py:23`(`_COLS` 增 `conflict`)
- Test: `tests/scan/test_tripwire_conflict.py`

**Interfaces:**
- Consumes: `autoresearch.learning.tripwire_watch.check(date, codes, scan_root) -> list[dict]`(每条含 `code`/`kind`/`raw`/`detail`/`card_date`)。
- Produces: `decision_finalize.tripwire_conflicts(scan_dir, analysis_date, pinned_rows) -> dict[str, dict]` —— key = code6,value = `{"tripwire_detail": str, "tripwire_raw": str, "rating": str}`;仅收录 **kind=="price" 且 rating != "Sell"** 的票。
- Produces: `report_sections._conflict_block(conflicts) -> str`(空 dict → `""`)。

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_tripwire_conflict.py
from autoresearch.scan import decision_finalize as df
from autoresearch.scan import report_sections as rs


def test_conflict_when_tripwire_fires_and_rating_not_sell(monkeypatch):
    monkeypatch.setattr(df, "_tripwire_hits", lambda *a, **k: [
        {"code": "300857", "kind": "price", "raw": "[价格线] close < 210.01 → 清仓",
         "detail": "收盘 205.00 < 210.01 → 清仓", "card_date": "2026-07-28"}])
    out = df.tripwire_conflicts("ignored", "2026-07-29",
                                [{"code": "300857", "rating": "Underweight"}])
    assert "300857" in out
    assert out["300857"]["rating"] == "Underweight"
    assert "205.00" in out["300857"]["tripwire_detail"]


def test_no_conflict_when_rating_is_sell(monkeypatch):
    """终评已是 Sell = 两把尺子同向,不构成冲突。"""
    monkeypatch.setattr(df, "_tripwire_hits", lambda *a, **k: [
        {"code": "920179", "kind": "price", "raw": "r", "detail": "d", "card_date": "x"}])
    out = df.tripwire_conflicts("ignored", "2026-07-29",
                                [{"code": "920179", "rating": "Sell"}])
    assert out == {}


def test_no_conflict_when_no_tripwire(monkeypatch):
    monkeypatch.setattr(df, "_tripwire_hits", lambda *a, **k: [])
    out = df.tripwire_conflicts("ignored", "2026-07-29",
                                [{"code": "688766", "rating": "Hold"}])
    assert out == {}


def test_date_kind_hits_are_not_conflicts(monkeypatch):
    """日期线/事件旗是提醒,不是与评级对立的价格判据。"""
    monkeypatch.setattr(df, "_tripwire_hits", lambda *a, **k: [
        {"code": "300857", "kind": "date", "raw": "r", "detail": "d", "card_date": "x"}])
    out = df.tripwire_conflicts("ignored", "2026-07-29",
                                [{"code": "300857", "rating": "Hold"}])
    assert out == {}


def test_conflict_block_renders_two_rulers():
    block = rs._conflict_block({"300857": {
        "tripwire_detail": "收盘 205.00 < 210.01 → 清仓",
        "tripwire_raw": "[价格线] close < 210.01 → 清仓",
        "rating": "Underweight"}})
    assert "300857" in block
    assert "收盘 205.00 < 210.01" in block
    assert "Underweight" in block
    assert "系统不合并" in block


def test_conflict_block_empty_when_no_conflict():
    assert rs._conflict_block({}) == ""
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest tests/scan/test_tripwire_conflict.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'tripwire_conflicts'`

- [ ] **Step 3: 实现判据**

`autoresearch/scan/decision_finalize.py` 末尾追加:

```python
def _tripwire_hits(scan_dir, analysis_date: str, codes: list[str]) -> list[dict]:
    """薄封装(测试 monkeypatch 此函数,不去 mock 整个 tripwire_watch)。"""
    from autoresearch.learning import tripwire_watch
    root = Path(scan_dir).parent if scan_dir else Path("context/scan")
    return tripwire_watch.check(analysis_date, codes=codes, scan_root=root)


def tripwire_conflicts(scan_dir, analysis_date: str,
                       pinned_rows: list[dict]) -> dict[str, dict]:
    """确定性尺(tripwire 价格线)与 LLM 终评的**冲突集**(Wave9 A-2)。

    冲突 = 保送票当日 **价格线** tripwire 触发 ∧ 终评 ≠ Sell。
    (2026-07-29 实测:300857 tripwire 判"清仓"、双复核终评 Underweight="减仓",
     报告里两头各说各话、无裁决材料 —— 本函数只**判定并呈现**冲突,**绝不合并**。)

    `date`/`event` 型命中是提醒(披露日临近/新闻旗),与评级不构成对立,不收。
    """
    rows = [r for r in (pinned_rows or []) if r.get("code")]
    if not rows:
        return {}
    rating_of = {str(r["code"]).zfill(6): str(r.get("rating", "") or "") for r in rows}
    try:
        hits = _tripwire_hits(scan_dir, analysis_date, list(rating_of))
    except Exception:  # noqa: BLE001 — advisory 层,坏了不挡发布
        return {}

    out: dict[str, dict] = {}
    for h in hits:
        if h.get("kind") != "price":
            continue
        code = str(h.get("code", "")).zfill(6)
        rating = rating_of.get(code, "")
        if not rating or rating == "Sell":
            continue
        out.setdefault(code, {"tripwire_detail": str(h.get("detail", "")),
                              "tripwire_raw": str(h.get("raw", "")),
                              "rating": rating})
    return out
```

> 若 `decision_finalize.py` 顶部没有 `from pathlib import Path`,补上。

- [ ] **Step 4: 实现冲突框渲染**

`autoresearch/scan/report_sections.py`,在 `_pinned_section`(:329)**之前**插入:

```python
def _conflict_block(conflicts: dict[str, dict]) -> str:
    """⚖️ 两尺分歧框(Wave9 A-2)——presence-gated,无冲突返回空串。

    呈现契约,**不是**合并规则:确定性价格线与 LLM 基本面终评测的不是同一件事,
    系统把两边的判据、来源、失效条件并排摆出来,由人裁。
    """
    if not conflicts:
        return ""
    lines = ["", "### ⚖️ 两尺分歧(确定性盯梢线 vs LLM 终评)", "",
             "| 票 | tripwire(价格尺) | LLM 终评(基本面尺) |", "|---|---|---|"]
    for code, c in sorted(conflicts.items()):
        lines.append(f"| {code} | {c['tripwire_detail']} | **{c['rating']}**"
                     f"(满卡 DD + 双复核折回) |")
    lines += ["", "| | 判据来源 | 失效条件 |", "|---|---|---|",
              "| 价格尺 | 你在决策卡写下的盯梢线,只看收盘价、不看基本面 | 收盘收复线上 |",
              "| 基本面尺 | 当日满卡尽调 + ≥OW/SELL 双复核中位 | 复核依据的驱动被证伪 |",
              "",
              "**两把尺子测的不是同一件事,系统不合并——人裁。**", ""]
    return "\n".join(lines)
```


在 `_pinned_section` 签名末尾加参数 `conflicts: dict | None = None`,并在 `if pinned_rows:` 分支的表格之后插入:

```python
        lines.append(_conflict_block(conflicts or {}))
```

`report_sections.py:646` 调用点改为:

```python
    from autoresearch.scan.decision_finalize import tripwire_conflicts
    _conf = tripwire_conflicts(scan_dir, analysis_date, pinned_rows)
    pin_sec = _pinned_section(scan_dir, analysis_date, pinned_rows, l1_full, l2_top,
                              ch_map, vmap, n_l1, n_l2, pinned_path=pinned_path,
                              conflicts=_conf)
```

> 调用点原本的实参顺序照抄现状,只**追加** `conflicts=_conf`;`pinned_path` 是否已在实参里以现场为准。

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run --no-sync pytest tests/scan/test_tripwire_conflict.py tests/scan/test_assemble_pinned.py -v`
Expected: 全 passed

- [ ] **Step 6: 账本加列**

`autoresearch/learning/pinned_ledger.py:23`,`_COLS` 末尾加 `"conflict"`;`roll()` 组行处对每行补
`"conflict": ""`(无冲突)或 `"tripwire_vs_rating"`(有)。数据源:读该日 `reports/scan/*/summary.md` 不可靠,改读运行期落盘 —— 在 Task 3 Step 4 的调用点顺手把 `_conf` 落盘:

```python
    with contextlib.suppress(Exception):
        (scan_dir / "_tripwire_conflicts.json").write_text(
            json.dumps(_conf, ensure_ascii=False), encoding="utf-8")
```

`pinned_ledger.roll()` 读该文件填列。

- [ ] **Step 7: 活体重放验收**

Run:
```bash
uv run --no-sync python -m autoresearch.scan.assemble 2026-07-29 2>&1 | tail -3
grep -A6 "两尺分歧" reports/scan/$(ls -t reports/scan | head -1)/summary.md
```
Expected: 出现 300857 的冲突框(该日 tripwire 触发 + 终评 Underweight)。**若没出现**:先 `cat context/scan/2026-07-29/_tripwire_conflicts.json` 看判据是否空,再回查 `pinned_rows` 里 rating 字段的真实键名。

- [ ] **Step 8: 全量测试 + 提交**

Run: `uv run --no-sync pytest tests/ -q`

```bash
git add autoresearch/scan/decision_finalize.py autoresearch/scan/report_sections.py \
        autoresearch/learning/pinned_ledger.py tests/scan/test_tripwire_conflict.py
git commit -m "feat(scan): 两尺分歧框 —— tripwire 价格线 vs LLM 终评并排呈现,系统不合并(W9-A2)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: L2 菜单落刀归因取证(A-3,只取证不动刀)

**Files:**
- Create: `autoresearch/scan/l2_knife_audit.py`
- Test: `tests/scan/test_l2_knife_audit.py`

**Interfaces:**
- Produces: `knife_rates(l1_df, l2_df, *, thresh: float = -20.0) -> dict` = `{"market": float, "menu": float, "main": float, "floor": float, "n_main": int, "n_floor": int}`。
  - `main` = L2 中 `l2_lane_reserved` 为假的行(主排序入选);`floor` = 为真的行(风格桶救回)。
- Produces: `audit(dates: list[str], root="context/scan") -> pd.DataFrame`,列 `["date","market","menu","main","floor","n_main","n_floor"]`。
- Produces: CLI `python -m autoresearch.scan.l2_knife_audit --days 20`。

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_l2_knife_audit.py
import pandas as pd

from autoresearch.scan import l2_knife_audit as ka


def test_splits_menu_rate_into_main_and_floor():
    l1 = pd.DataFrame({"code": list("abcdefgh"),
                       "pct_60d": [-30, -25, -10, -5, -30, -25, -10, -5]})
    l2 = pd.DataFrame({"code": ["a", "b", "c", "e", "f", "g"],
                       "pct_60d": [-30, -25, -10, -30, -25, -10],
                       "l2_lane_reserved": [False, False, False, True, True, True]})
    r = ka.knife_rates(l1, l2)
    assert r["market"] == 0.5            # 8 只里 4 只 < -20
    assert r["menu"] == 4 / 6            # 6 只里 4 只 < -20
    assert r["main"] == 2 / 3
    assert r["floor"] == 2 / 3
    assert r["n_main"] == 3 and r["n_floor"] == 3


def test_missing_reserved_column_puts_all_in_main():
    """老 run 无 l2_lane_reserved 列:全算主排序,floor 记 None 而不是编 0。"""
    l1 = pd.DataFrame({"code": ["a", "b"], "pct_60d": [-30, -5]})
    l2 = pd.DataFrame({"code": ["a"], "pct_60d": [-30]})
    r = ka.knife_rates(l1, l2)
    assert r["n_floor"] == 0
    assert r["floor"] is None


def test_empty_frames_return_none_not_zero():
    r = ka.knife_rates(pd.DataFrame(), pd.DataFrame())
    assert r["market"] is None and r["menu"] is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest tests/scan/test_l2_knife_audit.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 写实现**

```python
# autoresearch/scan/l2_knife_audit.py
#!/usr/bin/env python3
"""L2 菜单「落刀偏斜」归因取证(Wave9 A-3;确定性,零 LLM,**不改采样生产行为**)。

2026-07-29 实测:菜单落刀面(pct_60d < −20)75% vs 全市场 43% —— 分层采样把菜单
配得比市场更"向下"。但**立案时写的诊断动工一查 4/4 全错**是本 repo 的一等坑,所以
本模块只做一件事:把那个 75% **分解**成「主排序入选」与「风格桶 floor 救回」两桶,
让下一步的实验立案有真读数可依。

三必问(动手前自答,写在报表页首):
  1. 量错对象?—— 菜单落刀高会不会只是反映 risk_off 期 L1 召回池本身更落刀
     (故报表同时出 L1 池落刀率,不只对全市场比)。
  2. 时序不对?—— 菜单是当日切面,与同日 L1 对照,不跨日比。
  3. 已经有了?—— menu_health 已有菜单落刀读数;本模块只加**归因分解**,不重造。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

_KNIFE = -20.0


def _rate(df: pd.DataFrame, thresh: float) -> float | None:
    if df is None or not len(df) or "pct_60d" not in df.columns:
        return None
    s = pd.to_numeric(df["pct_60d"], errors="coerce").dropna()
    return None if not len(s) else float((s < thresh).mean())


def knife_rates(l1_df: pd.DataFrame, l2_df: pd.DataFrame, *,
                thresh: float = _KNIFE) -> dict:
    """落刀率四联:全市场(=L1 池)/ 菜单 / 菜单-主排序 / 菜单-floor 救回。"""
    out = {"market": _rate(l1_df, thresh), "menu": _rate(l2_df, thresh),
           "main": None, "floor": None, "n_main": 0, "n_floor": 0}
    if l2_df is None or not len(l2_df):
        return out
    if "l2_lane_reserved" not in l2_df.columns:
        out["n_main"] = len(l2_df)
        out["main"] = out["menu"]
        return out
    flag = l2_df["l2_lane_reserved"].astype(str).str.lower().isin(["true", "1", "yes"])
    main, floor = l2_df[~flag], l2_df[flag]
    out.update(main=_rate(main, thresh), floor=_rate(floor, thresh),
               n_main=int(len(main)), n_floor=int(len(floor)))
    return out


def audit(dates: list[str], root: Path | str = "context/scan") -> pd.DataFrame:
    rows = []
    for d in dates:
        sd = Path(root) / d
        try:
            l1 = pd.read_csv(sd / "L1_recall_top1000.csv", dtype={"code": str})
            l2 = pd.read_csv(sd / "L2_gbdt_top200.csv", dtype={"code": str})
        except Exception:  # noqa: BLE001 — 缺日跳过,不阻断
            continue
        rows.append({"date": d, **knife_rates(l1, l2)})
    return pd.DataFrame(rows)


def render(df: pd.DataFrame) -> str:
    if not len(df):
        return "# L2 落刀归因\n\n_无可用扫描日。_\n"
    def _f(v):
        return "—" if v is None or pd.isna(v) else f"{float(v):.0%}"
    lines = ["# L2 菜单落刀归因(Wave9 A-3 取证 · 不改生产采样)", "",
             "> 三必问见模块 docstring。`market` = **L1 召回池**口径(非全 A),",
             "> 以避免拿池外票当对照(量错对象)。", "",
             "| 日期 | L1池 | 菜单 | 主排序 | floor救回 | n_main | n_floor |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for r in df.to_dict("records"):
        lines.append(f"| {r['date']} | {_f(r['market'])} | {_f(r['menu'])} | "
                     f"{_f(r['main'])} | {_f(r['floor'])} | {r['n_main']} | {r['n_floor']} |")
    med = {c: df[c].dropna().median() if df[c].notna().any() else None
           for c in ("market", "menu", "main", "floor")}
    lines += ["", f"**中位**:L1池 {_f(med['market'])} · 菜单 {_f(med['menu'])} · "
                  f"主排序 {_f(med['main'])} · floor {_f(med['floor'])}", "",
              "_读法:若 floor 桶落刀率显著高于主排序,偏斜来自风格桶定义;若两桶接近,",
              "则菜单落刀只是 L1 池本身的映射,**不构成对分层采样的指控**。_"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="L2 菜单落刀归因取证(只出报表,不改采样)")
    ap.add_argument("--days", type=int, default=20, help="回看最近 N 个有产物的扫描日")
    ap.add_argument("--root", default="context/scan")
    ap.add_argument("--out", default="reports/learning/l2_knife_audit.md")
    args = ap.parse_args(argv)

    root = Path(args.root)
    dates = sorted([p.name for p in root.iterdir()
                    if p.is_dir() and (p / "L2_gbdt_top200.csv").exists()])[-args.days:]
    df = audit(dates, root)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(df), encoding="utf-8")
    df.to_csv(out.with_suffix(".csv"), index=False)
    print(json.dumps({"ok": True, "days": len(df), "out": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run --no-sync pytest tests/scan/test_l2_knife_audit.py -v`
Expected: 3 passed

- [ ] **Step 5: 真数据取证**

Run: `uv run --no-sync python -m autoresearch.scan.l2_knife_audit --days 20 && cat reports/learning/l2_knife_audit.md`
Expected: 报表出数。**读结论**:floor 桶落刀率是否显著高于主排序?

- [ ] **Step 6: 按取证结果立案(不动生产)**

只有 Step 5 显示 **floor 桶落刀率 − 主排序落刀率 ≥ 10pp**(中位口径)才立案:

```bash
uv run --no-sync python -m autoresearch.learning.experiment_registry register \
  --spec /dev/stdin <<'JSON'
{"id": "l2_floor_knife_v1", "family": "l2_sampling",
 "hypothesis": "风格桶 floor 的桶定义把落刀票系统性救回,导致菜单落刀面高于 L1 池",
 "change": "floor 桶增加 pct_60d > -20 前置条件(影子)",
 "primary_metric": "menu_knife_rate", "guard_metrics": ["l2_diversity", "winner_capture"],
 "shadow_days_required": 10, "baseline_ref": "stable_l2_stratify"}
JSON
```

若差值 < 10pp:**不立案**,在 `reports/learning/l2_knife_audit.md` 末尾追加一行
`_结论:floor 与主排序落刀率无显著差异,「分层采样制造落刀偏斜」不成立;菜单落刀 = L1 池映射。_`
—— 负结果同样是交付物,写下来防止下次重做。

- [ ] **Step 7: 提交**

```bash
git add autoresearch/scan/l2_knife_audit.py tests/scan/test_l2_knife_audit.py \
        reports/learning/l2_knife_audit.md 2>/dev/null || true
git commit -m "feat(scan): L2 菜单落刀归因取证 —— 分解主排序 vs floor 救回,先取证不动采样(W9-A3)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

> ⚠️ `reports/` 已 gitignore —— `git add` 会被忽略,属预期,不要为此 `-f` 强加。

---

## Task 5: TTL 复用退役(B-1a,裁定 R5)

**Files:**
- Modify: `.claude/workflows/scan-market.js:251`(删 `l4_reuse --apply`)、`:258-263`(PLAN schema 删 `reused`)、`:296`、`:310-311`(返回契约删 `reused`)
- Modify: `autoresearch/scan/l4/dispatch.py`(`dispatch_plan` 删复用分流)
- Modify: `autoresearch/learning/self_review.py`(probe 3 intel 稿数期望式删复用项)
- Modify: `.claude/skills/scan-market/SKILL.md`、`.claude/skills/scan-market/STAGES.md`
- Test: 改 `tests/scan/test_dispatch_plan.py`

**Interfaces:**
- Produces: `dispatch_plan(date, root) -> {"dispatch": [code...], "meta": {...}}` —— **不再有 `reused` 键**。

- [ ] **Step 1: 读现状,改测试**

先 `sed -n '1,80p' autoresearch/scan/l4/dispatch.py` 看 `dispatch_plan` 真身,再改 `tests/scan/test_dispatch_plan.py`:把断言 `reused` 的用例改成断言**不存在**该键,且**全部** finalist 都进 `dispatch`:

```python
def test_dispatch_plan_has_no_reuse_key(tmp_path, ...):
    """Wave9 R5:TTL 复用整体退役,每只 finalist 每日全量重研。"""
    plan = dispatch_plan("2026-07-29", root=...)
    assert "reused" not in plan
    assert set(plan["dispatch"]) == {"000651", "300857"}   # 含曾可复用的票
```

> 具体 fixture 沿用该文件现有构造方式,不要新造一套。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest tests/scan/test_dispatch_plan.py -v`
Expected: FAIL(现实现仍返回 `reused`)

- [ ] **Step 3: 改 `dispatch_plan`**

删掉 `dispatch.py` 里读 `♻️` 复用卡 / 调 `l4_reuse` 的分流分支,让全部 finalist 进 `dispatch`;返回 dict 删 `reused` 键。在函数 docstring 顶部加一行:

```
    Wave9 R5(2026-07-29 用户裁定「不要任何复用」):TTL 复用整体退役 —— 复用票不跑
    intel、新闻冻在源卡日(07-29 实测 601211),且复用门的"无新公告"判据在 anns 无
    权限日是盲的。评级稳定性改由**昨卡回声**(见 l4/prompts.py)承接。
```

- [ ] **Step 4: 改 workflow JS**

`.claude/workflows/scan-market.js`:
1. `:251` 删整行 `` `${R} autoresearch.scan.l4_reuse ${date} --apply; ` + ``
2. `:245` 的 log 文案 `L4-prep:reuse→[四生产者并行]→...` 改为 `L4-prep:[四生产者并行]→...`
3. `:258-262` PLAN schema 删 `reused` 属性
4. `:296` log 删 `· 复用 ... 张跳派发` 尾巴
5. `:310-311` 返回对象删 `reused: plan.reused || [],`
6. `:132` 哨兵档早退分支的 `reused: []` 一并删

- [ ] **Step 5: JS 语法探针(硬要求)**

`node --check` 对本仓 workflow js **零鉴别力**(ESM export + 顶层 return → node 跳过检查,写坏仍 exit 0)。改用 AsyncFunction 探针:

```bash
node -e "
const fs=require('fs');
const src=fs.readFileSync('.claude/workflows/scan-market.js','utf8')
  .replace(/^export const meta/m,'const meta');
new (Object.getPrototypeOf(async function(){}).constructor)(
  'agent','parallel','pipeline','log','phase','args','budget','workflow',src);
console.log('SYNTAX OK');
"
```
Expected: `SYNTAX OK`

- [ ] **Step 6: 改 probe 3(intel 稿数期望)**

`self_review.py:472-482`:`expect` 不再减 `n_reuse`,整段简化为 `expect, cal = n_rows, f"finalist 行 {n_rows}(含保送;R5 后无复用)"`;删 `reused` 集合的读取。docstring 第 3 条同步改口。

- [ ] **Step 7: 改 skill 文档**

- `SKILL.md` 步骤 4 代码块删 `uv run --no-sync python -m autoresearch.scan.l4_reuse <date> --apply` 整行;流程节顶部「♻️ 复用」相关描述删除。
- `STAGES.md` L4 节的复用小节整段改为:
  `**TTL 复用:已退役**(2026-07-29 用户裁定 R5)。曾因复用票不跑 intel 造成新闻冻结(07-29 实测 601211 新闻停在 07-27),且复用门"无新公告"判据在 anns 无权限日全盲。评级稳定性由**昨卡回声**承接(见 Task 6)。`

> ⚠️ skill 文档会被外部改 —— **编辑前重读该文件**,不要凭本计划的行号盲改。

- [ ] **Step 8: 跑测试 + 提交**

Run: `uv run --no-sync pytest tests/ -q`
Expected: 全绿(若 `tests/scan/test_l4_reuse*.py` 存在且失败 → 那些用例锁的是 `l4_reuse` 模块本身,模块此刻仍在仓库里、只是不再被编排调用,测试应仍绿;**若它们锁的是"编排会调用复用"这一契约,才需要删** —— 删前读 docstring 查双职)

```bash
git add .claude/workflows/scan-market.js autoresearch/scan/l4/dispatch.py \
        autoresearch/learning/self_review.py tests/scan/test_dispatch_plan.py \
        .claude/skills/scan-market/SKILL.md .claude/skills/scan-market/STAGES.md
git commit -m "feat(scan)!: TTL 复用退役 —— 每只 finalist 每日全量重研,新闻不再冻在源卡日(W9-B1a,用户裁定 R5)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: 昨卡回声(B-1b)

**Files:**
- Modify: `autoresearch/scan/l4/prompts.py`(新增 `yesterday_echo`,注入 `write_dispatch_pack` 的逐票段)
- Test: `tests/scan/test_yesterday_echo.py`

**Interfaces:**
- Consumes: 已发布报告 `reports/scan/<run>/details/<名称>.md` 与 `manifest.json`(数据日)。
- Produces: `yesterday_echo(code6: str, name: str, analysis_date: str, *, lookback_days: int = 5, reports_root="reports/scan") -> str` —— 3 行 markdown 块;无历史卡 → `""`。

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_yesterday_echo.py
import json

from autoresearch.scan.l4 import prompts


def _mk_run(root, run_id, data_date, name, body):
    d = root / run_id
    (d / "details").mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({"analysis_date": data_date}),
                                     encoding="utf-8")
    (d / "details" / f"{name}.md").write_text(body, encoding="utf-8")


CARD = """# 决策卡 — 601211 国泰海通 @ 2026-07-27
**Rating**: Hold
**一行多空**: 多 fwd-PE 11.2x ｜ 空 利好已见光死
- [价格线] close < 18.72 → 隔日减仓
"""


def test_echo_extracts_three_lines(tmp_path):
    _mk_run(tmp_path, "20260727_2100", "2026-07-27", "国泰海通", CARD)
    echo = prompts.yesterday_echo("601211", "国泰海通", "2026-07-29",
                                  reports_root=tmp_path)
    assert "2026-07-27" in echo
    assert "Hold" in echo
    assert "见光死" in echo
    assert "18.72" in echo
    assert "增量证据" in echo          # 防锚定文案必须在


def test_echo_empty_when_no_history(tmp_path):
    assert prompts.yesterday_echo("601211", "国泰海通", "2026-07-29",
                                  reports_root=tmp_path) == ""


def test_echo_respects_lookback_window(tmp_path):
    _mk_run(tmp_path, "20260701_2100", "2026-07-01", "国泰海通", CARD)
    assert prompts.yesterday_echo("601211", "国泰海通", "2026-07-29",
                                  lookback_days=5, reports_root=tmp_path) == ""


def test_echo_picks_most_recent_run(tmp_path):
    _mk_run(tmp_path, "20260727_2100", "2026-07-27", "国泰海通", CARD)
    _mk_run(tmp_path, "20260728_2100", "2026-07-28", "国泰海通",
            CARD.replace("Hold", "Underweight").replace("07-27", "07-28"))
    echo = prompts.yesterday_echo("601211", "国泰海通", "2026-07-29",
                                  reports_root=tmp_path)
    assert "Underweight" in echo and "Hold" not in echo
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest tests/scan/test_yesterday_echo.py -v`
Expected: FAIL — `AttributeError: ... has no attribute 'yesterday_echo'`

- [ ] **Step 3: 写实现**

`autoresearch/scan/l4/prompts.py` 追加:

```python
_ECHO_RATING = re.compile(r"^\*\*Rating\*\*:\s*(.+)$", re.M)
_ECHO_LS = re.compile(r"^\*\*一行多空\*\*:\s*(.+)$", re.M)
_ECHO_WIRE = re.compile(r"^-?\s*\[价格线\][^\n]*$", re.M)


def yesterday_echo(code6: str, name: str, analysis_date: str, *,
                   lookback_days: int = 5, reports_root="reports/scan") -> str:
    """昨卡回声(Wave9 B-1b):最近 ≤N 日已发布卡的 3 行摘要,注入任务包逐票段。

    R5 退役 TTL 复用后,"评级稳定性"不再靠**跳过研究**获得,而靠**记忆**:研究员知道
    昨天怎么判,今天写增量。**防锚定**:回声是历史判断,不是今日默认值 —— 翻覆合法,
    但必须写明触发翻覆的增量证据。
    """
    from datetime import datetime, timedelta
    root = Path(reports_root)
    if not root.is_dir():
        return ""
    try:
        cut = datetime.strptime(analysis_date, "%Y-%m-%d") - timedelta(days=lookback_days)
    except ValueError:
        return ""

    best: tuple[str, str] | None = None      # (data_date, card_text)
    for run in sorted(root.iterdir(), reverse=True):
        if not run.is_dir():
            continue
        try:
            dd = str(json.loads((run / "manifest.json").read_text(
                encoding="utf-8")).get("analysis_date", ""))
            when = datetime.strptime(dd, "%Y-%m-%d")
        except Exception:  # noqa: BLE001
            continue
        if when < cut or dd >= analysis_date:
            continue
        card = run / "details" / f"{name}.md"
        if not card.exists():
            card = run / "details" / f"{name}_{code6}.md"
        if not card.exists():
            continue
        with contextlib.suppress(OSError):
            text = card.read_text(encoding="utf-8")
            if best is None or dd > best[0]:
                best = (dd, text)
    if best is None:
        return ""

    dd, text = best
    rating = (_ECHO_RATING.search(text) or [None, "—"])[1].strip()
    ls = (_ECHO_LS.search(text) or [None, "—"])[1].strip()
    wires = _ECHO_WIRE.findall(text)[:2]
    lines = [f"## 昨卡回声(最近一次已发布判断 @ {dd})",
             f"- 评级:**{rating}**",
             f"- 一行多空:{ls}"]
    if wires:
        lines.append(f"- 盯梢线:{' ｜ '.join(w.strip('- ').strip() for w in wires)}")
    lines.append("> 历史判断**非今日默认值**;若今日翻覆,必须在卡里写明触发翻覆的"
                 "**增量证据**(新数字/新事件),不得只换措辞。")
    return "\n".join(lines) + "\n"
```

> 顶部按需补 `import contextlib, json, re` 与 `from pathlib import Path`。

- [ ] **Step 4: 接线进任务包**

`write_dispatch_pack`(:51)逐票组装处,把回声块拼进**逐票段**(与 `dossier_content` 同层),
**必须在共享前缀之后** —— prompt cache 前缀第 1 字节断裂会让全部 L4 卡 cache miss(token-economy-p0 前科)。在写盘前:

```python
            echo = yesterday_echo(code6, str(row.get("name", "") or ""), date)
```
并把 `echo` 追加到该票 prompt 的差异段末尾(紧邻 `dossier_content` 之后)。

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run --no-sync pytest tests/scan/test_yesterday_echo.py tests/scan/test_context_blocks.py -v`
Expected: 全 passed

- [ ] **Step 6: 前缀 byte-identical 回归**

Run: `uv run --no-sync pytest tests/scan/ -k "prompt or cache or context_block" -v`
Expected: 全 passed。**若有 byte-identical 契约测试变红** → 回声块插错位置(插到共享前缀里了),移到逐票段。

- [ ] **Step 7: 活体验收**

```bash
uv run --no-sync python -m autoresearch.scan.agents.l4_card prompts 2026-07-29
grep -l "昨卡回声" context/scan/2026-07-29/_l4_prompt_*.md | head
```
Expected: 至少 601211(07-27 有卡)命中。

- [ ] **Step 8: 提交**

```bash
git add autoresearch/scan/l4/prompts.py tests/scan/test_yesterday_echo.py
git commit -m "feat(scan): 昨卡回声 —— 复用退役后由记忆承接评级稳定性,翻覆须给增量证据(W9-B1b)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: intel 拒稿改按时效裁稿(B-2)

**Files:**
- Modify: `autoresearch/scan/l4/intel_guard.py:44`(`guard_intel` 超硬顶分支)
- Test: `tests/scan/test_intel_guard.py`(扩)、新增 `tests/scan/test_intel_trim.py`

**Interfaces:**
- Produces: `trim_by_recency(text: str, *, keep: int = 10) -> tuple[str, int]` —— 按 `T0 → 24h → 催化挂 → 背景 → >1周` 保留事件行,返回 `(裁剪后全文, 被砍行数)`。
- Produces: `guard_intel(...)` 的 `action` 新增 `"TRIMMED"`;`"REJECTED"` 只留给不可解析稿。

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_intel_trim.py
from autoresearch.scan.l4 import intel_guard as ig

DRAFT = """# 活体情报 — 000651 格力 @ 2026-07-29

## 事件段
| 日期 | 时效窗 | 事件 | 源 | 净分 |
|---|---|---|---|---|
| 2026-07-29 | T0 | 盘后推出 R290 | http://a | 0.0 |
| 2026-07-29 | 24h | 铜价新高 | http://b | -0.5 |
| 2026-07-25 | 背景 | 排产走弱 | http://c | -0.5 |
| 2026-06-04 | >1周 | 明骏减持完成 | http://d | 0.0 |
| 2026-08-27 | 催化挂 | 中报披露 | http://e | 0.0 |

## 题材段
归属:白电

## 声明行
网查 36 条 ｜ T0面=有增量
"""


def test_trim_keeps_t0_24h_catalyst_drops_background():
    out, cut = ig.trim_by_recency(DRAFT, keep=3)
    assert "R290" in out and "铜价新高" in out and "中报披露" in out
    assert "排产走弱" not in out and "明骏减持" not in out
    assert cut == 2


def test_trim_preserves_non_event_sections():
    out, _ = ig.trim_by_recency(DRAFT, keep=3)
    assert "## 题材段" in out and "归属:白电" in out and "## 声明行" in out


def test_trim_noop_when_within_keep():
    out, cut = ig.trim_by_recency(DRAFT, keep=10)
    assert cut == 0 and out == DRAFT


def test_guard_trims_instead_of_rejecting(tmp_path):
    (tmp_path / "_l4_intel_000651.md").write_text(DRAFT, encoding="utf-8")
    r = ig.guard_intel(tmp_path, "000651", hard_cap=30)
    assert r["action"] == "TRIMMED"
    assert (tmp_path / "_l4_intel_000651.md").exists()
    assert not (tmp_path / "_l4_intel_000651.rejected.md").exists()
    body = (tmp_path / "_l4_intel_000651.md").read_text(encoding="utf-8")
    assert "已裁剪" in body and "R290" in body


def test_guard_rejects_unparseable_draft(tmp_path):
    (tmp_path / "_l4_intel_000333.md").write_text(
        "# 活体情报\n\n网查 40 条\n(事件段表损坏)\n", encoding="utf-8")
    r = ig.guard_intel(tmp_path, "000333", hard_cap=30)
    assert r["action"] == "REJECTED"
    assert (tmp_path / "_l4_intel_000333.rejected.md").exists()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest tests/scan/test_intel_trim.py -v`
Expected: FAIL — `AttributeError: ... has no attribute 'trim_by_recency'`

- [ ] **Step 3: 写实现**

`intel_guard.py` 追加,并改 `guard_intel` 的超硬顶分支:

```python
_WINDOW_RANK = {"T0": 0, "24h": 1, "催化挂": 2, "背景": 3, ">1周": 4}
_EVENT_ROW = re.compile(r"^\|\s*\d{4}-\d{2}-\d{2}\s*\|")


def _window_of(row: str) -> int:
    cells = [c.strip() for c in row.strip().strip("|").split("|")]
    win = cells[1] if len(cells) > 1 else ""
    return _WINDOW_RANK.get(win, 3)          # 认不出的窗按"背景"降权,不当 T0


def trim_by_recency(text: str, *, keep: int = 10) -> tuple[str, int]:
    """按时效窗优先级把事件段裁到 ≤keep 行 → (新全文, 被砍行数)。

    Wave9 B-2:超硬顶从"整稿拒"改"按时效裁"——整稿拒会把 **T0 增量**(明天开盘唯一
    还没被定价的东西)一并扔掉,2026-07-29 实测 002546/603893 两票新闻面因此变薄。
    硬顶的牙还在(超帽内容照样丢),只是丢弃顺序按时效价值排。
    """
    lines = text.splitlines()
    idx = [i for i, ln in enumerate(lines) if _EVENT_ROW.match(ln)]
    if len(idx) <= keep:
        return text, 0
    ranked = sorted(idx, key=lambda i: (_window_of(lines[i]), i))
    drop = set(ranked[keep:])
    out = [ln for i, ln in enumerate(lines) if i not in drop]
    return "\n".join(out) + ("\n" if text.endswith("\n") else ""), len(drop)
```

`guard_intel` 里 `if claimed > hard_cap:` 分支整段替换:

```python
    if claimed > hard_cap:
        trimmed, cut = trim_by_recency(text)
        if cut == 0:
            # 事件段解析不出 → 稿件结构不可信,照旧整拒(REJECTED 的新语义)
            dst = src.with_name(f"_l4_intel_{code}.rejected.md")
            src.replace(dst)
            return {"ok": False, "code": code, "action": "REJECTED",
                    "claimed": claimed, "hard_cap": hard_cap, "kept_as": dst.name,
                    "note": "事件段不可解析,整稿拒;card 回退卡内网查"}
        stamp = (f"〔已裁剪·自报 {claimed} 超硬顶 {hard_cap}·"
                 f"按时效窗保留 T0/24h/催化挂,砍 {cut} 行〕\n")
        src.write_text(stamp + trimmed, encoding="utf-8")
        return {"ok": True, "code": code, "action": "TRIMMED",
                "claimed": claimed, "hard_cap": hard_cap, "dropped_rows": cut,
                "note": "T0/24h 增量保留;card 照常读 intel"}
```

模块 docstring 顶部补一段说明本次改动(为什么拒稿 → 裁稿)。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run --no-sync pytest tests/scan/test_intel_trim.py tests/scan/test_intel_guard.py -v`
Expected: 全 passed(旧 `test_intel_guard.py` 里断言 `REJECTED` 的用例可能需改成 `TRIMMED` —— 改之前读用例 docstring 确认它锁的是"超帽要有后果"还是"必须整拒")

- [ ] **Step 5: 变异测试**

把 `ranked = sorted(...)` 的 key 改成 `lambda i: i`(退化成按出现顺序砍),跑
`uv run --no-sync pytest tests/scan/test_intel_trim.py::test_trim_keeps_t0_24h_catalyst_drops_background -v`
Expected: **FAIL**。确认后改回。

- [ ] **Step 6: 真稿回放**

```bash
cp context/scan/2026-07-29/_l4_intel_002546.rejected.md /tmp/w9/_l4_intel_002546.md
uv run --no-sync python -m autoresearch.scan.l4.intel_guard 2026-07-29 002546 --scan-dir /tmp/w9
grep -c "^| 2026-" /tmp/w9/_l4_intel_002546.md
```
Expected: `action=TRIMMED`,且裁剪后事件行 ≤10、T0/24h 行仍在。

- [ ] **Step 7: 提交**

```bash
git add autoresearch/scan/l4/intel_guard.py tests/scan/test_intel_trim.py \
        tests/scan/test_intel_guard.py
git commit -m "fix(scan): intel 超硬顶从整稿拒改按时效裁 —— T0/24h 增量不再被一并扔掉(W9-B2)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: 档案研报体(B-3)

**Files:**
- Modify: `.claude/agents/l4-card.md:87-125`(满卡模板增段、早停卡增微研报块、行数预算)
- Modify: `autoresearch/scan/l4/prompts.py`(档案四节全文内联,替代仅摘要)
- Modify: `autoresearch/learning/self_review.py:333`(`product_shape_lint` 增探针 10)
- Test: `tests/scan/test_dossier_research_body.py`

**Interfaces:**
- Consumes: `autoresearch.dossier.schema.dossier_path(code6)`、`autoresearch.scan.dossier.render_dossier`。
- Produces: `product_shape_lint` 新探针 `产物形状·研报体缺失`(warn)。

- [ ] **Step 1: 先补指令(纪律:先指令,后检查)**

`.claude/agents/l4-card.md` 满卡模板(B 节)在 `## 维度评分卡` 之后插入:

```
## 研报体(档案δ)  ← **有档案票必填**;无档案票改写一行「研报体:档案未建(已插队今晚建档)」
- **业务**:<一句话生意模型(誊自档案§1)> + <今日增量:有则一句,无则「无增量」>
- **驱动三情景**:<驱动1/2/3 各一行,**每行标注「今日动了没」**;誊自档案§2,不重算>
- **带位**:<当前 PE/PB 落在档案§7 的哪个带位;越带须一句为什么>
- **风险矩阵**:<档案§5 的 top3,**每条标「今日变了/解除了/未变」**>
```

早停卡(A 节)在 `## L3 论点裁决` 之后插入:

```
## 微研报(≤8 行;有档案票必填,无档案写一行「档案未建」)
- 业务一句 ｜ 带位一行 ｜ 风险 top2(全誊自档案,零现场成本)
```

**行数预算同批改**(否则预算把新契约挤掉):`## 压缩纪律` 节
- `早停卡正文 ≤36 行` → `早停卡正文 ≤44 行`
- `早停卡 ~1.2–1.8K、满卡 ~3K` → `早停卡 ~1.6–2.2K、满卡 ~4.5K`(两处都改:压缩纪律段与「多写不多读」段)

在「多写不多读」段末尾补一句:
`研报体素材**全部来自已注入的档案块**,不新增任何读盘/网查——它是输出侧丰富化,读盘边界一毫米不动。`

- [ ] **Step 2: 写失败测试**

```python
# tests/scan/test_dossier_research_body.py
from autoresearch.learning.self_review import product_shape_lint


def _mk(tmp_path, card_body: str, has_dossier: bool):
    d = tmp_path / "2026-07-29"
    (d / "details").mkdir(parents=True)
    (d / "details" / "000651.md").write_text(card_body, encoding="utf-8")
    (d / "finalists.csv").write_text("code,name,lane\n000651,格力电器,healthy\n",
                                     encoding="utf-8")
    (d / "_dossier_present.json").write_text(
        '["000651"]' if has_dossier else "[]", encoding="utf-8")
    return d


FULL = """# 决策卡 — 000651 格力电器 @ 2026-07-29
## 维度评分卡
| 基本面 | 强 |
**Rating**: Hold
"""


def test_warns_when_dossier_exists_but_no_research_body(tmp_path):
    d = _mk(tmp_path, FULL, has_dossier=True)
    hits = product_shape_lint(d, "2026-07-29")
    assert any("研报体" in h["probe"] for h in hits)


def test_no_warn_when_research_body_present(tmp_path):
    d = _mk(tmp_path, FULL + "\n## 研报体(档案δ)\n- **业务**:空调\n", has_dossier=True)
    hits = product_shape_lint(d, "2026-07-29")
    assert not any("研报体" in h["probe"] for h in hits)


def test_no_warn_when_no_dossier_but_declared(tmp_path):
    d = _mk(tmp_path, FULL + "\n研报体:档案未建(已插队今晚建档)\n", has_dossier=False)
    hits = product_shape_lint(d, "2026-07-29")
    assert not any("研报体" in h["probe"] for h in hits)


def test_warns_when_no_dossier_and_no_declaration(tmp_path):
    d = _mk(tmp_path, FULL, has_dossier=False)
    hits = product_shape_lint(d, "2026-07-29")
    assert any("研报体" in h["probe"] for h in hits)
```

- [ ] **Step 3: 跑测试确认失败**

Run: `uv run --no-sync pytest tests/scan/test_dossier_research_body.py -v`
Expected: FAIL(4 个用例里至少 2 个失败)

- [ ] **Step 4: 档案四节内联**

`autoresearch/scan/l4/prompts.py:192-206`,`dossier_parts` 组装处:现在只放 `_dossier_summary_mark`(~600B 摘要)+ `render_dossier` 历史。改为额外内联档案 §1/§2/§5/§7 全文:

```python
                # Wave9 B-3:研报体的素材侧 —— 摘要 600B 撑不起研报体,内联四节全文
                # (~4-8KB/票,相对 170KB slim 可忽略);选内联而非让 agent 自己 Read,
                # 读盘边界与工具调用方差都不动。
                with contextlib.suppress(Exception):
                    from autoresearch.dossier.schema import dossier_sections
                    secs = dossier_sections(code6, keys=("§1", "§2", "§5", "§7"))
                    if secs:
                        dossier_parts.append("### 档案节选(研报体素材)\n" + secs)
```

> `dossier_sections(code6, keys)` 若不存在 → 在 `autoresearch/dossier/schema.py` 里实现:读档案 md,按 `## §N` 标题切段,返回选中段拼接;找不到返回 `""`。**先 `grep -n "def " autoresearch/dossier/schema.py` 看有没有现成切段函数,有就复用不要重造。**

- [ ] **Step 5: 落 `_dossier_present.json`**

`prompts.write_dispatch_pack` 末尾把"本次哪些票有档案"落盘(lint 要读):

```python
    with contextlib.suppress(Exception):
        (Path(scan_dir) / "_dossier_present.json").write_text(
            json.dumps(sorted(_with_dossier), ensure_ascii=False), encoding="utf-8")
```
(`_with_dossier` = 循环里 `dossier_sources` 非空的 code 集合)

- [ ] **Step 6: 加 lint 探针**

`self_review.py` 的 `product_shape_lint` 末尾追加探针 10:

```python
    # 10) 研报体在场(Wave9 B-3;先补指令后加检查 —— agent def 已写要求)
    with contextlib.suppress(Exception):
        present = set(json.loads(
            (scan_dir / "_dossier_present.json").read_text(encoding="utf-8")))
        for fr in fin_rows:
            code = str(fr.get("code", "")).zfill(6)
            card = scan_dir / "details" / f"{code}.md"
            if not card.exists():
                continue
            txt = card.read_text(encoding="utf-8")
            if code in present:
                if "研报体(档案δ)" not in txt and "微研报" not in txt:
                    add("产物形状·研报体缺失", "warn",
                        f"{code} 有档案却无研报体段 —— 素材已注入任务包但卡没写")
            elif "档案未建" not in txt:
                add("产物形状·研报体缺失", "warn",
                    f"{code} 无档案且未写缺档声明行")
```

docstring 编号表加第 10 条。

- [ ] **Step 7: 跑测试确认通过 + 变异测试**

Run: `uv run --no-sync pytest tests/scan/test_dossier_research_body.py tests/scan/test_card_lint.py -v`
Expected: 全 passed

变异:把探针 10 的 `add(...)` 两处注释掉 → `test_warns_when_dossier_exists_but_no_research_body` 必须 **FAIL**。确认后改回。

- [ ] **Step 8: agent def 契约测试**

Run: `uv run --no-sync pytest tests/test_agent_defs.py -v`
Expected: passed(该文件锁 agent def 结构;若它断言了行数/段名清单,同步更新)

- [ ] **Step 9: 提交**

```bash
git add .claude/agents/l4-card.md autoresearch/scan/l4/prompts.py \
        autoresearch/dossier/schema.py autoresearch/learning/self_review.py \
        tests/scan/test_dossier_research_body.py
git commit -m "feat(scan): 决策卡增档案研报体 —— 四节内联 + 满卡/早停卡模板 + lint 探针 + 行数预算同调(W9-B3)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: finalist 无档案插队建档(B-3 续,裁定 R6)

**Files:**
- Modify: `autoresearch/scan/post_run.py`(新增 `enqueue_finalist_dossiers`)
- Modify: `autoresearch/dossier/pool.py`(消化队列按 `priority` 降序)
- Test: `tests/dossier/test_finalist_enqueue.py`

**Interfaces:**
- Produces: `post_run.enqueue_finalist_dossiers(scan_dir, analysis_date) -> list[str]` —— 新入队的 code6 列表。
- Produces: `coverage_pool.json` 的 pending 条目新增字段 `{"priority": "finalist", "last_seen": "YYYY-MM-DD"}`。

- [ ] **Step 1: 写失败测试**

```python
# tests/dossier/test_finalist_enqueue.py
import json

from autoresearch.scan import post_run


def test_enqueues_finalists_without_dossier(tmp_path, monkeypatch):
    d = tmp_path / "2026-07-29"
    d.mkdir(parents=True)
    (d / "finalists.csv").write_text(
        "code,name\n920179,凯德石英\n000651,格力电器\n", encoding="utf-8")
    (d / "_dossier_present.json").write_text('["000651"]', encoding="utf-8")
    pool = tmp_path / "coverage_pool.json"
    pool.write_text(json.dumps({"pending_init": []}), encoding="utf-8")
    monkeypatch.setattr(post_run, "_pool_path", lambda: pool)

    added = post_run.enqueue_finalist_dossiers(d, "2026-07-29")
    assert added == ["920179"]
    entry = json.loads(pool.read_text(encoding="utf-8"))["pending_init"][0]
    assert entry["code"] == "920179"
    assert entry["priority"] == "finalist"
    assert entry["last_seen"] == "2026-07-29"


def test_idempotent_refreshes_last_seen(tmp_path, monkeypatch):
    d = tmp_path / "2026-07-30"
    d.mkdir(parents=True)
    (d / "finalists.csv").write_text("code,name\n920179,凯德石英\n", encoding="utf-8")
    (d / "_dossier_present.json").write_text("[]", encoding="utf-8")
    pool = tmp_path / "coverage_pool.json"
    pool.write_text(json.dumps({"pending_init": [
        {"code": "920179", "priority": "finalist", "last_seen": "2026-07-29"}]}),
        encoding="utf-8")
    monkeypatch.setattr(post_run, "_pool_path", lambda: pool)

    added = post_run.enqueue_finalist_dossiers(d, "2026-07-30")
    assert added == []
    entries = json.loads(pool.read_text(encoding="utf-8"))["pending_init"]
    assert len(entries) == 1 and entries[0]["last_seen"] == "2026-07-30"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest tests/dossier/test_finalist_enqueue.py -v`
Expected: FAIL — `AttributeError: ... has no attribute 'enqueue_finalist_dossiers'`

- [ ] **Step 3: 写实现**

`autoresearch/scan/post_run.py` 追加(先 `grep -n "coverage_pool" autoresearch/dossier/pool.py` 确认真实路径常量,复用它而非硬编码):

```python
def _pool_path() -> Path:
    from autoresearch.dossier import pool
    return Path(pool.POOL_PATH)          # 若常量名不同,以 pool.py 现场为准


def enqueue_finalist_dossiers(scan_dir, analysis_date: str) -> list[str]:
    """当日无档案的 finalist 插队进建档队列(Wave9 R6)。

    总帽 ≤3 只/晚**不变**,只改**顺序**:高频入围票先覆盖,下次再遇即有料。
    幂等:已在队列的票只刷新 `last_seen`,不重复入队。
    """
    sd = Path(scan_dir)
    try:
        present = set(json.loads((sd / "_dossier_present.json").read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001
        present = set()
    codes: list[str] = []
    with contextlib.suppress(Exception):
        import csv
        with (sd / "finalists.csv").open(encoding="utf-8") as fh:
            codes = [str(r.get("code", "")).zfill(6) for r in csv.DictReader(fh)
                     if r.get("code")]
    want = [c for c in codes if c not in present]
    if not want:
        return []

    p = _pool_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    pend = data.setdefault("pending_init", [])
    have = {str(e.get("code", "")).zfill(6): e for e in pend if isinstance(e, dict)}

    added: list[str] = []
    for c in want:
        if c in have:
            have[c]["last_seen"] = analysis_date
            have[c].setdefault("priority", "finalist")
        else:
            pend.append({"code": c, "priority": "finalist", "last_seen": analysis_date})
            added.append(c)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return added
```

在 `post_run` 的 `observe` 子命令流程里调用一次,并打印 `[dossier] 插队 N 只:...`。

- [ ] **Step 4: 消化顺序按 priority**

`autoresearch/dossier/pool.py` 里出 `pending_init` 队列的函数,排序键改为
`(0 if e.get("priority") == "finalist" else 1, e.get("last_seen", ""))` —— finalist 优先,同级按 last_seen 升序(先来先建)。**总帽 ≤3 不动。**

- [ ] **Step 5: 跑测试 + 提交**

Run: `uv run --no-sync pytest tests/dossier/ tests/scan/test_dossier.py -v`
Expected: 全 passed

```bash
git add autoresearch/scan/post_run.py autoresearch/dossier/pool.py \
        tests/dossier/test_finalist_enqueue.py
git commit -m "feat(dossier): 无档案 finalist 盘后插队建档(帽 ≤3 不变,只改顺序)(W9-B3b,裁定 R6)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: 📰 新闻头行导航(B-4)

**Files:**
- Modify: `autoresearch/scan/publisher.py:19`(`_publish_details` 注入头行)
- Test: `tests/scan/test_news_headline.py`

**Interfaces:**
- Produces: `publisher._news_headline(intel_path: Path) -> str` —— 一行 markdown;intel 缺席 → `📰 情报缺席(未启用或已拒稿)`。

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_news_headline.py
from pathlib import Path

from autoresearch.scan import publisher

DRAFT = """# 活体情报 — 000651 @ 2026-07-29
| 日期 | 时效窗 | 事件 | 源 | 净分 |
|---|---|---|---|---|
| 2026-07-29 | T0 | 盘后 R290 | http://a | 0.0 |
| 2026-07-29 | 24h | 铜价 | http://b | -0.5 |
| 2026-07-25 | 背景 | 排产 | http://c | -0.5 |
"""


def test_counts_t0_and_24h(tmp_path):
    p = tmp_path / "_l4_intel_000651.md"
    p.write_text(DRAFT, encoding="utf-8")
    line = publisher._news_headline(p)
    assert "T0 增量:1 条" in line
    assert "24h 2 条" in line or "24h 1 条" in line   # 见下方口径说明


def test_zero_t0_says_no_increment(tmp_path):
    p = tmp_path / "_l4_intel_x.md"
    p.write_text(DRAFT.replace("| T0 |", "| 背景 |"), encoding="utf-8")
    assert "盘后无增量" in publisher._news_headline(p)


def test_absent_intel(tmp_path):
    assert "情报缺席" in publisher._news_headline(tmp_path / "nope.md")
```

> **口径说明**:`24h N 条` 只数 `时效窗 == 24h` 的行(不含 T0)。上面测试写成 `or` 是为了让实现者显式选定,实现完成后**把该断言收紧成单一值** `assert "24h 1 条" in line`。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest tests/scan/test_news_headline.py -v`
Expected: FAIL — `AttributeError: ... has no attribute '_news_headline'`

- [ ] **Step 3: 写实现**

`autoresearch/scan/publisher.py` 追加:

```python
_INTEL_ROW = re.compile(r"^\|\s*\d{4}-\d{2}-\d{2}\s*\|\s*([^|]+?)\s*\|")


def _news_headline(intel_path: Path) -> str:
    """卡头 📰 导航行(Wave9 B-4):新闻别藏在卡尾。"""
    try:
        text = intel_path.read_text(encoding="utf-8")
    except OSError:
        return "📰 情报缺席(未启用或已拒稿)—— 本卡新闻依据见卡内网查段"
    wins = _INTEL_ROW.findall(text)
    t0 = sum(1 for w in wins if w.strip() == "T0")
    h24 = sum(1 for w in wins if w.strip() == "24h")
    head = f"T0 增量:{t0} 条" if t0 else "T0 盘后无增量"
    return f"📰 {head} · 24h {h24} 条 · 详见文末情报附录"
```

`_publish_details` 里 `shutil.copy2(card, dst)` **之后**、intel 附录拼接**之前**插入:

```python
        # 📰 头行:插在标题行后(Wave9 B-4)
        with contextlib.suppress(Exception):
            body = dst.read_text(encoding="utf-8")
            head_line = _news_headline(scan_dir / f"_l4_intel_{code}.md")
            parts = body.split("\n", 1)
            if parts and parts[0].startswith("#"):
                dst.write_text(f"{parts[0]}\n\n{head_line}\n"
                               + (parts[1] if len(parts) > 1 else ""), encoding="utf-8")
```

> ⚠️ 顶部按需补 `import re`。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run --no-sync pytest tests/scan/test_news_headline.py -v`
Expected: 3 passed。然后按 Step 1 的口径说明**收紧** `test_counts_t0_and_24h` 的 `or` 断言,重跑。

- [ ] **Step 5: 活体重放**

```bash
uv run --no-sync python -m autoresearch.scan.assemble 2026-07-29 2>&1 | tail -2
head -4 "reports/scan/$(ls -t reports/scan | head -1)/details/格力电器.md"
```
Expected: 第 3 行是 📰 行且 `T0 增量:1 条`(格力 07-29 19:46 R290 那条)。

- [ ] **Step 6: 全量测试 + 提交**

Run: `uv run --no-sync pytest tests/ -q`
Expected: 全绿

```bash
git add autoresearch/scan/publisher.py tests/scan/test_news_headline.py
git commit -m "feat(scan): detail 卡头 📰 新闻导航行 —— T0/24h 增量条数上浮到卡头(W9-B4)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## 收尾验收(全部 10 个 Task 完成后)

- [ ] **全量测试**:`uv run --no-sync pytest tests/ -q` → 全绿,且**通过数 > 基线 1952**(新增约 30 个用例)
- [ ] **JS 探针**:Task 5 Step 5 的 AsyncFunction 探针再跑一次 → `SYNTAX OK`
- [ ] **活体验收清单**(下次真扫描盯这几条):
  1. 无 ♻️ 复用卡,每只 finalist 都有当日 intel(复用退役生效)
  2. 曾复用的票(如 601211)任务包含「昨卡回声」块
  3. 有档案票的卡带「研报体(档案δ)」段;无档案票带缺档声明行
  4. 每张 detail 卡头有 📰 行
  5. intel 超帽稿出现〔已裁剪〕戳而非 `.rejected.md`
  6. 若 tripwire 与终评分歧 → summary 出「⚖️ 两尺分歧」框
  7. `run_health.json` 有 `anns_source_status` 键
  8. 盘后 `coverage_pool.json` 里无档案 finalist 带 `priority: finalist`
- [ ] **计量对照**:跑一次 `usage_harvest`,对照 07-29 的 $31.82 —— 预期 +$3~5(复用退役 + 研报体输出),**若 >+$10 需查是不是档案内联把 prompt 撑爆了**
- [ ] **文档同步**:SKILL.md / STAGES.md 已改(Task 5 Step 7);设计稿 §2-3 标注「已实施」

---

## Self-Review 记录

**1. Spec 覆盖检查**(设计稿 §2 批A + §3 批B 逐条):
- A-1 兜底源 → Task 1;A-1 probe 改口 → Task 2;A-1 权限探针 → Task 2 Step 7 ✓
- A-2 冲突判据/渲染/账本 → Task 3 Steps 3/4/6 ✓
- A-3 取证脚本/三必问/立案不动刀 → Task 4 ✓
- B-1 摘接线/昨卡回声/模块处置/文档同步 → Task 5 + Task 6 ✓
  - ⚠️ 设计稿 B-1.3「`l4_reuse.py` 进死码候补」**本计划不删模块**(只摘编排接线),留独立死码波 —— 符合「删 test 先查双职」纪律
- B-2 裁稿 → Task 7 ✓
- B-3 档案内联/满卡段/早停块/行数预算/lint/插队建档 → Task 8 + Task 9 ✓
- B-4 📰 头行 → Task 10 ✓

**2. 占位符扫描**:无 TBD/TODO/「实现细节待定」。首稿曾在 Task 3 Step 4 的渲染代码里留下一处乱码占位,已清除 —— 计划文档不该含刻意的地雷。

**3. 类型一致性**:
- `anns_source_status` 的返回键 `{primary_empty_rate, fallback_rows, status}` 在 Task 2 的实现、测试、probe 三处一致 ✓
- `tripwire_conflicts` 返回 `dict[str, dict]`,`_conflict_block` 消费同一形状 ✓
- `trim_by_recency` 返回 `(str, int)`,`guard_intel` 按此解包 ✓
- `yesterday_echo` 返回 `str`(空串 = 无历史),接线处直接拼接 ✓
- `enqueue_finalist_dossiers` 返回 `list[str]` ✓

**4. 已知不确定点**(实现时以现场为准,计划已在对应 Step 标注):
- `self_review.add()` 落的键名是否为 `probe`(Task 2 Step 2 注)
- `pool.POOL_PATH` 常量真名(Task 9 Step 3 注)
- `dossier.schema` 是否已有切段函数(Task 8 Step 4 注)
- `stock_notice_report` 的真实列名(Task 1 Step 5 冒烟裁决)
