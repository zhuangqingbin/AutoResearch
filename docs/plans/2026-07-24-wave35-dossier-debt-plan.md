# Wave 3.5:档案接线收尾波(清 Wave 3 自带负债)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 关掉 Wave 3 引入的两条真风险(陈旧 §4/§6 被「已覆盖」加冕为已核事实、intel 结构性盲从工具级降为指令级),补齐档案陈旧度探针,清完终审记账的小改。

**Architecture:** 全确定性零 LLM。δ 扩到 §4/§6(当日 staging 优先、缺数据不覆盖旧值);intel 的档案已知底改「**内嵌代替授权**」(dispatch meta 带摘要文本 → workflow 内嵌进 prompt → agent 收回 Read);`last_refresh` 补写者 + 90 日陈旧 lint;ledger 渲染与性能小改。

**Tech Stack:** Python 3 + pandas + pytest;`uv run --no-sync` 调用;`.claude/agents/*.md` 与 `.claude/workflows/*.js` 契约文件。

**需求源:** `.superpowers/sdd/final-review-wave3.md`(Round 1 的 I-2/I-3 + Round 2 记账)与 `docs/specs/2026-07-22-research-depth-dossier-design.md` ①表刷新列 / ④「情报站聚焦增量」/ 风险节「`last_refresh` 超 90 日 → lint warn」。

## Global Constraints

- **Parity 铁律**:无档案/未首覆/缺素材 → 行为逐字节不变;新字段缺省 = Wave 3 现行为。
- **对称守卫原则(Wave 3 T1 review 的教训,必须复用)**:刷新某节时若新素材缺,**保留旧值,不得用 `[数据缺,...]` 覆盖已有真内容**——「要么都更新要么都保留」。
- **降级留痕**(本波前两次都栽在这):跳过刷新/跳过内嵌必须可被观测(返回值或 stdout),不静默。
- **结构性盲**:l4-intel 恢复为**工具级**保证(无 Read/Grep/Glob);已知底靠 prompt 内嵌文本,不靠授权+自觉。
- **档案锚走 `schema` 常量**;异常上抛或按各层既有惯例吞,不得新增静默吞路。
- **超短 T+2 尺不变**;测试命令 `uv run --no-sync python -m pytest ...`;频繁 commit。

---

### Task 1: δ 扩到 §4/§6(当日 staging 优先 + 缺数据不覆盖)

**Files:**
- Modify: `autoresearch/dossier/delta.py`
- Test: `tests/dossier/test_delta.py`(追加)

**Interfaces:**
- Consumes: `builder._section4_body(staging_dir, code6, today) -> str`、`builder._section6_body(staging_dir, code6, today) -> str`、`builder._latest_staging_dir(scan_root) -> Path | None`、`builder._missing(today) -> str`、Task-1(Wave3)的 `replace_section/section_body`。
- Produces: `_refresh_staging_sections(text, code6, date, scan_root) -> str`(§4/§6 就地刷新;素材缺 → 原文返回)。

- [ ] **Step 1: 写失败测试**

`tests/dossier/test_delta.py` 追加(沿用文件顶部已有的 `_mk_dossier` helper):

```python
def _mk_staging(root, day, code="300857", *, pledge=True, calendar=True):
    """造一天的 staging:pledge.csv(§4 料)+ calendar.csv(§6 料)。"""
    d = root / day
    d.mkdir(parents=True, exist_ok=True)
    if pledge:
        (d / "pledge.csv").write_text(
            f"code,pledge_ratio,end_date\n{code},41.5,2026-07-20\n", encoding="utf-8")
    if calendar:
        (d / "calendar.csv").write_text(
            f"code,ann_date,event\n{code},2026-08-28,中报预约披露\n", encoding="utf-8")
    return d


def test_delta_refreshes_section4_6_from_today_staging(tmp_path):
    from autoresearch.dossier import delta
    p = _mk_dossier()
    root = tmp_path / "scan"
    _mk_staging(root, "2026-07-24")
    delta.record_scan_delta("300857", "2026-07-24", rating="Hold", scan_root=root)
    text = p.read_text(encoding="utf-8")
    assert "41.5" in delta.section_body(text, 3)          # §4 拿到当日质押率
    assert "2026-07-24" in delta.section_body(text, 3)     # 标注素材来自哪个扫描日


def test_delta_section4_6_missing_material_keeps_old(tmp_path):
    """对称守卫:当日无 staging → 保留旧 §4/§6,不得写成 [数据缺,…]。"""
    from autoresearch.dossier import delta
    p = _mk_dossier()
    root = tmp_path / "scan"
    _mk_staging(root, "2026-07-24")
    delta.record_scan_delta("300857", "2026-07-24", rating="Hold", scan_root=root)
    before4 = delta.section_body(p.read_text(encoding="utf-8"), 3)
    assert "41.5" in before4
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    delta.record_scan_delta("300857", "2026-07-25", rating="Hold", scan_root=empty_root)
    after4 = delta.section_body(p.read_text(encoding="utf-8"), 3)
    assert after4 == before4                               # 旧真值原样保留
    assert "数据缺" not in after4


def test_delta_prefers_today_staging_over_latest(tmp_path):
    """当日目录存在即用当日,不回退到"最近有素材的日"(防拿旧快照冒充今天)。"""
    from autoresearch.dossier import delta
    p = _mk_dossier()
    root = tmp_path / "scan"
    old = _mk_staging(root, "2026-07-20")
    (old / "pledge.csv").write_text("code,pledge_ratio,end_date\n300857,99.9,2026-07-01\n",
                                    encoding="utf-8")
    _mk_staging(root, "2026-07-24")                        # 当日 41.5
    delta.record_scan_delta("300857", "2026-07-24", rating="Hold", scan_root=root)
    body4 = delta.section_body(p.read_text(encoding="utf-8"), 3)
    assert "41.5" in body4 and "99.9" not in body4
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/dossier/test_delta.py -x -q`
Expected: FAIL(§4 仍是建档日内容,断言 `41.5` 落空)

- [ ] **Step 3: 实现**

`delta.py` 在 `_append_eps_snapshot` 之后新增:

```python
def _staging_dir_for(scan_root: Path, date: str) -> Path | None:
    """δ 用的 staging 目录:**当日优先**(δ 跑在 assemble 尾,当日素材就在手),
    当日缺 → 回退 builder 的「最近有素材的日」;都无 → None。

    为什么不直接复用 builder 的 `_latest_staging_dir`:建档跑在任意时点、只能取最近;
    δ 跑在当日收尾,拿当日才是正解——否则会用旧快照冒充今天(spec ① §4/§6「每次 δ」)。
    """
    d = Path(scan_root) / date
    if any((d / f).exists() for f in builder._STAGING_FILES):
        return d
    return builder._latest_staging_dir(Path(scan_root))


def _refresh_staging_sections(text: str, code6: str, date: str,
                              scan_root: str | Path) -> str:
    """§4 筹码资金史 / §6 催化剂日历 就地刷新(spec ① 表:每次 δ)。

    **对称守卫**:新素材算不出真内容(返回 `_missing` 占位)→ 保留旧节,不覆盖
    (与 `_refresh_band` 同款;Wave3 T1 review 教训:半更新会制造节间自相矛盾)。
    """
    staging = _staging_dir_for(scan_root, date)
    if staging is None:
        return text
    stamp = staging.name
    miss = builder._missing(date)
    for idx, fn in ((3, builder._section4_body), (5, builder._section6_body)):
        body = fn(staging, code6, date)
        if not body or body == miss:          # 素材缺 → 保留旧值(不降级覆盖)
            continue
        text = replace_section(text, idx, f"_素材 as-of {stamp}_\n\n{body}")
    return text
```

`record_scan_delta` 里,在 `text = _append_eps_snapshot(text, pf)` 之后加一行:

```python
    text = _refresh_staging_sections(text, code6, date, scan_root)
```

顶部若尚未 import `builder` 则已有(Wave3 已 import)。

- [ ] **Step 4: 跑测试通过 + 全量回归**

Run: `uv run --no-sync python -m pytest tests/dossier -x -q && uv run --no-sync python -m pytest -q`
Expected: 全 PASS。

- [ ] **Step 5: 注入块标 as-of(消掉「陈旧被加冕为已核」的最后一口)**

`autoresearch/scan/agents/l4_card.py` 的 `_dossier_summary_mark`,把 tail 改为(其余不动):

```python
        tail = (f"_档案全文按需 Read:`{p}`(§4 筹码/§6 催化随每日 δ 刷新,"
                "其余节为首覆/中报季全量);本卡必须含「**档案对账**」节:"
                "驱动变量哪个动了/风险矩阵哪条触发或解除/判例账本一行。_")
```

同步改 `tests/scan/test_l4_dossier_inject.py` 里断言该 tail 的用例(若断言的是子串「档案对账」则无需改,先跑测试确认)。

- [ ] **Step 6: Commit**

```bash
git add autoresearch/dossier/delta.py autoresearch/scan/agents/l4_card.py tests/
git commit -m "fix(dossier): δ 扩到 §4/§6(当日 staging 优先·缺料不覆盖旧值)+ 注入块标节刷新口径"
```

---

### Task 2: intel 已知底改「内嵌代替授权」,收回 Read

**Files:**
- Modify: `autoresearch/scan/agents/l4_card.py`(`dispatch_plan` 的 meta)
- Modify: `.claude/workflows/l4-stock.js`、`.claude/agents/l4-intel.md`
- Modify: `.claude/skills/scan-market/SKILL.md`(派发 args 模板)
- Test: `tests/scan/test_l4_dossier_inject.py`(追加)、`tests/test_agent_defs.py`(改回工具级断言)

**Interfaces:**
- Consumes: `schema.injectable_summary(code6) -> str`。
- Produces: `dispatch_plan(...)["meta"][code6]["dossier_summary"]`(str,无档案/不可注入 = `""`)。

- [ ] **Step 1: 写失败测试**

`tests/scan/test_l4_dossier_inject.py` 追加:

```python
def test_dispatch_meta_carries_dossier_summary(tmp_path):
    """intel 已知底走 meta 内嵌(不再靠给 agent 授权 Read)。"""
    from autoresearch.scan.agents.l4_card import dispatch_plan
    sd = tmp_path / "2026-07-24"
    sd.mkdir(parents=True)
    (sd / "finalists.csv").write_text(
        "code,name,sector\n300857,协创数据,消费电子\n002926,华西证券,非银金融\n",
        encoding="utf-8")
    (sd / "_l4_prompt_300857.md").write_text("x", encoding="utf-8")
    (sd / "_l4_prompt_002926.md").write_text("x", encoding="utf-8")
    _mk()                                            # 300857 已首覆(文件顶部 helper)
    plan = dispatch_plan("2026-07-24", root=tmp_path)
    assert "业务: 算力租赁" in plan["meta"]["300857"]["dossier_summary"]
    assert plan["meta"]["002926"]["dossier_summary"] == ""     # 无档案 → 空(parity)
```

`tests/test_agent_defs.py` 的 `test_l4_intel_def` 改回**工具级盲**断言(替换 Wave3 放宽的那两行 + docstring):

```python
def test_l4_intel_def():
    """l4-intel:sonnet·max 盲搜情报员;**结构性盲=工具级**(无 Read/Grep/Glob);六面契约锚在位。

    Wave3.5:档案已知底改由派发 prompt **内嵌摘要文本**提供(内嵌代替授权)——
    盲性回到工具级保证,不再靠"授权 Read + 人设自觉"(同目录躺着 _l3_table.md)。
    """
    text = _agent_text("l4-intel")
    head = text.split("---", 2)[1]
    assert "model: sonnet" in head and "effort: max" in head
    assert "WebSearch" in head and "WebFetch" in head and "Write" in head
    for banned in ("Read", "Grep", "Glob"):
        assert banned not in head, f"结构性盲:不得有 {banned}(可读/探索仓库)"
```

(该函数余下的六面锚断言原样保留。)

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_l4_dossier_inject.py tests/test_agent_defs.py -x -q`
Expected: 两处 FAIL(meta 无该键;agent def 仍含 Read)

- [ ] **Step 3: `dispatch_plan` 的 meta 带摘要**

`l4_card.py` 的 `dispatch_plan` 里,给 `meta[code6]` 赋值处追加 `dossier_summary` 键(其余键不动):

```python
        meta[code6] = {"name": name, "sector": sector, "pinned": is_pinned,
                       # Wave3.5:intel 已知底「内嵌代替授权」——摘要文本随 meta 走,
                       # workflow 内嵌进 intel prompt,agent 因此无需 Read 权限(结构性盲回工具级)。
                       "dossier_summary": _dossier_summary_text(code6)}
```

并新增 helper(放 `_dossier_summary_mark` 旁):

```python
def _dossier_summary_text(code6: str) -> str:
    """给 intel 内嵌用的档案摘要纯文本;不可注入 → ""(与卡注入同一事实源)。"""
    try:
        from autoresearch.dossier import schema as dschema
        return dschema.injectable_summary(code6).strip()
    except Exception:  # noqa: BLE001 — 档案层可选
        return ""
```

> 若现场 `meta[code6]` 的赋值形态与上文不同(键名/是否含 `pinned`),**以现场为准**只追加 `dossier_summary` 一键,勿改动既有键。

- [ ] **Step 4: workflow 内嵌 + agent 收回 Read**

`.claude/workflows/l4-stock.js`:
- args 解构处加 `const dossierSummary = (A.dossierSummary || '').trim()`(缺省空 = parity)。
- Intel 相位的 agent prompt 模板串:删掉 Wave3 加的 `若存在 context/knowledge/dossiers/${code}.md:先读其摘要节作已知底,只查增量。`,改为在 prompt 末尾条件内嵌:

```js
const knownBase = dossierSummary
  ? `\n\n## 已知底(覆盖档案摘要·仅用于去重,**不是**查询方向指令)\n${dossierSummary}\n\n已在上面出现的事实不必复查,查询额度全花在增量与新事件上。`
  : ''
```

并把 `knownBase` 拼到 intel prompt 字符串尾部。

`.claude/agents/l4-intel.md`:
- frontmatter `tools:` 去掉 `Read`(回到 `Write, WebSearch, WebFetch`)。
- 「输入与盲性」段那句改为:`若派发 prompt 里内嵌了「已知底(覆盖档案摘要)」块:那是历史事实的去重清单,不是方向指令——已在其中的事实不复查,额度全花在增量与新事件上。你没有 Read/Grep/Glob(不得读取或探索仓库,结构性盲)。`

`.claude/skills/scan-market/SKILL.md`:l4-stock 派发示例的 args 追加 `dossierSummary`,并写一行:`dossierSummary 取自 dispatch-plan 的 meta[code].dossier_summary(无档案=空串);漏传只退化为「intel 无已知底」= Wave3 前行为,不影响正确性。`

- [ ] **Step 5: 跑测试通过 + 全量回归**

Run: `uv run --no-sync python -m pytest tests/scan tests/test_agent_defs.py -x -q && uv run --no-sync python -m pytest -q`
Expected: 全 PASS。

- [ ] **Step 6: Commit**

```bash
git add autoresearch/scan/agents/l4_card.py .claude/ tests/
git commit -m "fix(intel): 已知底改内嵌代替授权,收回 Read——结构性盲回到工具级保证"
```

---

### Task 3: `last_refresh` 补写者 + 90 日陈旧度 lint(档案陈旧目前零探针)

**Files:**
- Modify: `autoresearch/dossier/schema.py`(新 `staleness_issues`)
- Modify: `autoresearch/dossier/reconcile.py`(季度对账 = 全量刷新语义 → 写 `last_refresh`)
- Modify: `autoresearch/scan/prelude.py`(`_dossier_pool` 步打印陈旧告警)
- Test: `tests/dossier/test_schema.py`、`tests/dossier/test_reconcile.py`、`tests/scan/test_prelude*.py`(按现场文件名)

**Interfaces:**
- Produces: `schema.staleness_issues(text: str, today: str, *, cap_days: int = 90) -> list[str]`(超期 → `["档案陈旧:last_refresh 2026-04-01 距今 114 日(>90)"]`;`last_refresh` 空则以 `initiated` 计;两者皆空 → `[]`)。

- [ ] **Step 1: 写失败测试**

`tests/dossier/test_schema.py` 追加:

```python
def test_staleness_issues():
    from autoresearch.dossier import schema
    head = ("---\ncode: 300857\nname: x\nsector: x\npool_status: active\n"
            "entered: 2026-01-01\nentry_reason: pinned\ninitiated: {ini}\n"
            "last_refresh: {ref}\nlast_delta: 2026-07-24\n---\n")
    fresh = head.format(ini="2026-01-01", ref="2026-07-01")
    assert schema.staleness_issues(fresh, "2026-07-24") == []
    stale = head.format(ini="2026-01-01", ref="2026-03-01")
    iss = schema.staleness_issues(stale, "2026-07-24")
    assert len(iss) == 1 and "档案陈旧" in iss[0] and "2026-03-01" in iss[0]
    # last_refresh 空 → 退回 initiated 计龄
    no_ref = head.format(ini="2026-01-01", ref="null")
    assert schema.staleness_issues(no_ref, "2026-07-24")
    # 两者皆空 → 不报(骨架未首覆,归 pending_init 管)
    both = head.format(ini="null", ref="null")
    assert schema.staleness_issues(both, "2026-07-24") == []
```

`tests/dossier/test_reconcile.py` 追加:

```python
def test_reconcile_sets_last_refresh():
    """季度对账 = 报告期全量核对 → 写 last_refresh(spec:中报季强制全量刷新)。"""
    import pandas as pd
    from autoresearch.dossier import reconcile, schema
    from tests.dossier.test_delta import _mk_dossier
    _mk_dossier(code="300858")
    df = pd.DataFrame([{"ann_date": "20260828", "n_income": 2.5e8,
                        "yoy_net_profit": 2.0e8, "diluted_eps": 0.85}])

    def fetch(endpoint, params):
        return df if endpoint == "express" else pd.DataFrame()

    reconcile.reconcile_one("300858", "20260630", "2026-08-29", fetch=fetch)
    fm = schema.parse_frontmatter(schema.dossier_path("300858").read_text(encoding="utf-8"))
    assert fm["last_refresh"] == "2026-08-29" and fm["last_delta"] == "2026-08-29"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/dossier/test_schema.py tests/dossier/test_reconcile.py -x -q`
Expected: FAIL(`staleness_issues` 不存在;`last_refresh` 仍 null)

- [ ] **Step 3: 实现**

`schema.py` 追加(放 `lint_dossier` 之后):

```python
STALE_DAYS = 90     # 档案陈旧告警阈值(spec 风险节:last_refresh 超 90 日 → warn)


def staleness_issues(text: str, today: str, *, cap_days: int = STALE_DAYS) -> list[str]:
    """档案陈旧度探针:`last_refresh`(缺则退 `initiated`)距 today 超 cap_days → 一条 issue。

    与 `lint_dossier`(结构契约)分开:结构对但内容陈旧是另一类病,且需要"今天"这个
    外部输入才能判——不塞进纯结构 lint(规模检查与结构检查分开,repo 既有惯例)。
    两个日期都空 = 骨架未首覆,归 pending_init 管,不在此报。
    """
    from datetime import date as _date
    meta = parse_frontmatter(text)
    ref = meta.get("last_refresh") or meta.get("initiated")
    if not ref:
        return []
    try:
        y, m, d = (int(x) for x in str(ref).split("-"))
        ty, tm, td = (int(x) for x in str(today).split("-"))
        age = (_date(ty, tm, td) - _date(y, m, d)).days
    except Exception:  # noqa: BLE001 — 日期畸形不报陈旧(结构 lint 的事)
        return []
    if age > cap_days:
        return [f"档案陈旧:last_refresh {ref} 距今 {age} 日(>{cap_days})"]
    return []
```

`reconcile.py` 的 `reconcile_one`,在写 `last_delta` 那行旁追加(未披露分支**不**写 `last_refresh` —— 没核到数不算全量刷新):

```python
    text = delta.set_frontmatter_key(text, "last_delta", today)
    text = delta.set_frontmatter_key(text, "last_refresh", today)   # 对账=报告期全量核对
```

`prelude.py` 的 `_dossier_pool` 步,在现有 nag 之后追加陈旧告警(presence-gated,无档案不打印):

```python
        stale: list[str] = []
        for c in sorted(pool.get("stocks", {})):
            p = dschema.dossier_path(c)
            if not p.exists():
                continue
            iss = dschema.staleness_issues(p.read_text(encoding="utf-8"), date)
            if iss:
                stale.append(f"{c}({iss[0].split('距今 ')[-1].split(' 日')[0]}日)")
        if stale:
            notes.append(f"🕰️ 档案陈旧 {len(stale)} 只(>90日未全量刷新):"
                         + "、".join(stale[:6]) + ("…" if len(stale) > 6 else ""))
```

> `notes`/`dschema` 的实际变量名与导入方式**以现场为准**(该步已有 nag 的追加模式,照抄同款)。

- [ ] **Step 4: 跑测试通过 + 全量回归**

Run: `uv run --no-sync python -m pytest tests/dossier tests/scan -x -q && uv run --no-sync python -m pytest -q`
Expected: 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add autoresearch/dossier/schema.py autoresearch/dossier/reconcile.py autoresearch/scan/prelude.py tests/
git commit -m "feat(dossier): 陈旧度探针(90日)+ 季度对账写 last_refresh + prelude 🕰️ 告警"
```

---

### Task 4: ledger 渲染三小改 + `retro_buckets` 只读两列

**Files:**
- Modify: `autoresearch/dossier/ledger.py`
- Test: `tests/dossier/test_ledger.py`(追加)

**Interfaces:** 签名不变;仅渲染文本与读取列变化。

- [ ] **Step 1: 写失败测试**

`tests/dossier/test_ledger.py` 追加:

```python
def test_render_discloses_pnl_sample_and_neutral(tmp_path):
    """M-2/M-3:样本量与中性数都要写出来(注入面读数不得含糊)。"""
    import json
    from autoresearch.dossier import ledger
    p = tmp_path / "t1.jsonl"
    rows = [
        {"t": "2026-07-14", "code": "300857", "rating": "Underweight",
         "verdict": "准", "excess_ind": -0.03, "sealed": False},
        {"t": "2026-07-15", "code": "300857", "rating": "Sell",
         "verdict": "准", "excess_ind": -0.02, "sealed": True},    # 计方向不计 pnl
        {"t": "2026-07-16", "code": "300857", "rating": "Underweight",
         "verdict": "中性", "excess_ind": 0.001, "sealed": False},
    ]
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                 encoding="utf-8")
    rec = ledger.code_track_record("300857", ledger_path=p)
    assert rec["n_dir"] == 3
    val = ledger.render_precedent_value(5, rec)
    assert "中性" in val                                   # M-3:注入面不省中性
    block = ledger.render_track_block("300857", scan_root=tmp_path / "nope",
                                      ledger_path=p)
    assert "pnl n=2" in block or "可实现 2 笔" in block      # M-2:披露 pnl 分母


def test_retro_buckets_reads_only_needed_columns(tmp_path, monkeypatch):
    """M-15:attribution.csv ≈5000×29,只需 code/bucket 两列。"""
    import pandas as pd
    from autoresearch.dossier import ledger
    rd = tmp_path / "2026-07-14" / "retro"
    rd.mkdir(parents=True)
    (rd / "attribution.csv").write_text(
        "code,name,bucket,fwd_2_oc\n300857,协创,recalled_cut,0.01\n", encoding="utf-8")
    seen = {}
    real = pd.read_csv

    def spy(path, **kw):
        seen.update(kw)
        return real(path, **kw)

    monkeypatch.setattr(pd, "read_csv", spy)
    assert ledger.retro_buckets("300857", scan_root=tmp_path) == {"recalled_cut": 1}
    assert set(seen.get("usecols") or []) == {"code", "bucket"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/dossier/test_ledger.py -x -q`
Expected: FAIL(渲染无中性/无 pnl 分母;read_csv 无 usecols)

- [ ] **Step 3: 实现**

`ledger.py` 三处改动:

1. `code_track_record` 的返回追加 `"n_pnl": len(pnl)`(docstring 同步说明:`n_dir` 含 sealed、`n_pnl` 是可实现样本数)。
2. `render_precedent_value` 的 tail 改为:

```python
    tail = (f";t1 方向 {rec['n_dir']} 笔 准{rec['right']}/不准{rec['wrong']}"
            f"/中性{rec.get('neutral', 0)}")
    if rec.get("avg_pp") is not None:
        tail += f",顺方向超额均值 {rec['avg_pp']:+.1f}pp(pnl n={rec.get('n_pnl', 0)})"
```

3. `render_track_block` 的 t1 行 avg 段同步带 `(pnl n={rec.get('n_pnl', 0)})`。
4. `retro_buckets` 的 `pd.read_csv(...)` 追加 `usecols=["code", "bucket"]`(套在现有 try 内:老 CSV 若缺 `bucket` 列会抛 ValueError → 现有 `except` 跳过该日,行为安全)。
5. `ledger.py` 顶部 docstring 的「口径与 `render_ledger_report` 对齐」一句改为:`pnl 口径与 render_ledger_report 对齐;**方向计数 n_dir 含 sealed 是本档案的有意口径**(一字板不影响"方向判对没判对",只影响"吃不吃得到")。`(M-4)

- [ ] **Step 4: 跑测试通过 + 全量回归**

Run: `uv run --no-sync python -m pytest tests/dossier -x -q && uv run --no-sync python -m pytest -q`
Expected: 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add autoresearch/dossier/ledger.py tests/dossier/test_ledger.py
git commit -m "fix(dossier): 判例渲染披露 pnl 分母与中性数 + retro_buckets 只读两列 + 口径 docstring 校正"
```

---

### Task 5: 机制对操作者可见(STAGES.md/SKILL 补档案链)+ 控制端冒烟收尾

**Files:**
- Modify: `.claude/skills/scan-market/STAGES.md`、`.claude/skills/scan-market/SKILL.md`
- Modify: `docs/plans/2026-07-24-wave35-dossier-debt-plan.md`(实录回填)
- Test: `tests/test_agent_defs.py` 或现场的文档 lint 测试(按现场)

- [ ] **Step 1: STAGES.md 补档案机制段**

先整读 `.claude/skills/scan-market/STAGES.md`,找到 L4 段落(现只有一行旧的 `scan/dossier.py 个股档案注入 L4`),在其后补一段(照该文件既有行文风格):

```markdown
- **覆盖档案链(Wave2/3)**:`context/knowledge/coverage_pool.json` 池(prelude 日检)→
  `context/knowledge/dossiers/<code>.md` 八节档案(`dossier-init` 首覆)→ L4 prompt 注入
  「📚 覆盖档案摘要」(`schema.injectable_summary` 四门,与卡 lint 同源)→ 卡写「档案对账」节
  (`self_review` 分档探针)→ assemble 尾 `delta.record_scan_deltas` 回写 §4/§6/§8 + 摘要机算行
  → 季度对账 `python -m autoresearch.dossier.reconcile <period>`(prelude 📐 提醒;
  未披露也落痕)。全链 presence-gated:无档案 = Wave1 前行为。
```

- [ ] **Step 2: SKILL.md 档案维护段**

在 SKILL.md 的收尾/维护相关段落补三行命令(若已有 reconcile 行则只补其余两行):

```markdown
- 建档队列消化(≤3 只/晚):`python -m autoresearch.dossier.pool <date> --status` 看 pending_init,
  逐票派 `dossier-init` workflow。
- 季度对账(中报/年报披露后):`uv run --no-sync python -m autoresearch.dossier.reconcile <period>`。
- 档案陈旧(prelude 🕰️ 行):>90 日未全量刷新 → 该票下次扫描后补一次 `dossier-init --force` 或对账。
```

- [ ] **Step 3: 文档接线测试**

若 `tests/test_agent_defs.py` 已有 `test_l4_intel_wired_in_docs` 这类"文档接线"测试,照其形态追加一条,断言 STAGES.md 含 `coverage_pool` 与 `dossier.reconcile`;否则新建同风格测试。

- [ ] **Step 4: 控制端活体冒烟(自跑,不派 subagent)**

```bash
uv run --no-sync python -c "
from autoresearch.dossier.delta import record_scan_deltas
from autoresearch.dossier import delta, schema
n = record_scan_deltas('context/scan/2026-07-21', '2026-07-21')
print('deltas:', n)
for c in ('300857','688766'):
    t = schema.dossier_path(c).read_text(encoding='utf-8')
    print(c, 'lint=', schema.lint_dossier(t),
          '| stale=', schema.staleness_issues(t, '2026-07-24'))
    print('  §4:', delta.section_body(t,3).strip()[:120])
    print('  §6:', delta.section_body(t,5).strip()[:120])"
```

人工核:§4/§6 是否已带 `_素材 as-of <扫描日>_` 且内容来自真 staging(不是建档日快照);lint 与 stale 读数照实记录。再跑一次确认幂等(md5 前后一致)。

`dispatch_plan` 的 meta 抽查:

```bash
uv run --no-sync python -c "
from autoresearch.scan.agents.l4_card import dispatch_plan
p = dispatch_plan('2026-07-21')
m = p['meta']
cov = [c for c,v in m.items() if v.get('dossier_summary')]
print('meta 票数:', len(m), '| 带档案摘要:', cov)
if cov: print('样本前 120 字:', m[cov[0]]['dossier_summary'][:120])"
```

- [ ] **Step 5: 全量回归 + ruff + 实录回填 + Commit**

Run: `uv run --no-sync python -m pytest -q && uv run --no-sync ruff check autoresearch tests`

本计划尾部追加 `## 冒烟实录(2026-07-24)` 记 Step 4 真实读数(含任何 skip/降级),`.superpowers/sdd/progress.md` 收口。

```bash
git add .claude/ docs/plans tests/
git commit -m "docs(scan): STAGES/SKILL 补覆盖档案全链 + Wave3.5 冒烟实录回填"
```

---

## Self-Review(已跑)

1. **需求覆盖**:终审 Round-1 的 I-2(§4/§6)=T1、I-3(intel 内嵌)=T2、M-13(`last_refresh`+陈旧)=T3、M-2/M-3/M-4/M-15(ledger)=T4、M-17(STAGES)=T5。**未纳入**:M-11(确定性 δ 素材)、M-12(对账偏差数)——两者是增量功能非负债,留 Wave 4 与新闻召回路一并排期;M-1/M-5/M-6/M-7/M-9/M-10/M-14/M-16 终审已裁「放行」,不动。
2. **Placeholder 扫描**:无 TBD;三处「以现场为准」都给了定位锚与不变量(只追加不改既有键 / 照抄同款追加模式 / 先跑测试确认),不是留白。
3. **类型一致性**:`staleness_issues -> list[str]` 与 `lint_dossier` 同形;`code_track_record` 新增 `n_pnl` 后三个渲染消费者已同步;`dossier_summary` 键在 meta/js/SKILL 三处同名。
4. **风险自查**:T2 引入「又一个必传 args」——缓解写进 SKILL(漏传只退化为 Wave3 前行为,非错误结果),且与 `pinned` 漏传(SELL 双复核断链)后果等级不同,不需要新探针。
