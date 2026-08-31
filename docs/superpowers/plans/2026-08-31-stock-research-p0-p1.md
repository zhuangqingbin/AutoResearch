# stock-research P0+P1 Implementation Plan(取数契约化 × analyze 纳入 capsule)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 stock-research 的确定性取数层升级为「记账、锚时点、走湖」的证据层,并让独立跑的 analyze(full/lite)第一次拥有与 scan 同级的法证现场(capsule + 账本),同时修掉 scan↔lite 接缝上五处已坐实的解析脆弱点。

**Architecture:** 契约先行(analyze 词汇/产物进 `autoresearch/contracts/`,守卫逼齐)→ 机械修复(29 处降级记账、8 处 PIT、assemble 硬校验、解析容错)→ 结构工程(已登记端点走湖、harvest 模块拆分、capsule kind 化、transcript 绑定、账本骨架)。每步 byte-parity 或变异探针兜底。

**Tech Stack:** Python 3.13(uv venv)、pytest、tushare/akshare/yfinance、自研 `autoresearch.trace` capsule、Claude Code / Codex CLI 双引擎。

**Spec:** `docs/specs/2026-08-31-stock-research-optimization-design.md`(D1/D5.1/D6/D7/D8.3 = 本计划;D2/D3/D4/D8.1-2/D9 = P2/P3 另行计划)。裁决:Q6=不买付费权限;其余按稿内推荐。审计原件:`docs/research/2026-08-31-stock-research-optimization/`(A/B/C/W1–W5)。

## Global Constraints

- 一切 python 命令用 `uv run --no-sync python -m …`,在仓库根目录跑(否则 .env/依赖加载不到)。
- 全量测试基线 **4693 passed / 6 skipped / 0 failed**;每个任务结束跑 `uv run --no-sync python -m pytest -q -x` 相关目录、每波结束跑全量,不许带红提交。
- **B 类冻结(至 09-中)**:不得改 scan 判断层的输入/输出语义——`_l4_prompt_*` 内容、slim 块清单、L4 prompt、门阈值、E6 规则、评级映射一律不动。本计划全部 I/M 类。
- **新产物先登记再写码**:任何新文件名先进 `contracts.ARTIFACTS`(或白名单,只许减不许增),否则 drift 守卫红(`tests/contracts/test_registry_parity.py`)。
- **引擎隔离**:路径一律经 `autoresearch/common/workspace.py` 的函数;不得写裸 `context_claude`/`reports_claude` 字面量(`tests/common/test_workspace.py` 有 grep 探针);engine 不写 `.env`。
- **分层**(`tests/contracts/test_layering.py`,向上边只减不增):`contracts < common < data < trace < …/analyze < scan`。**analyze 不得 import scan**;analyze→trace/contracts/common/data 合法。
- **Q6 裁定**:不接 `anns_d` / `news src=cls` / `limit_list_ths`(付费);本计划不含任何新数据源接入(那是 P2 的 D2)。
- **`.claude/**` 改动 = 人批**:本计划触及 `.claude/skills/stock-research/SKILL.md`(T13)与 `.claude/settings.json`(T16),都在任务里明示 diff,由用户随任务 review 批准;不改任何 agent def 的 prompt 正文(那是 P2 的 D4/D8)。
- **premise-check 纪律**(仓 memory:「立案时写的诊断,动工一查 4/4 全错」):每个任务的 Step 1 都是核实前提;行号对不上就先修任务再动码,不硬套。
- 提交:每任务一 commit,消息按仓风格 `<type>(<scope>): <一句>`,结尾加 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。禁 `git push`。
- 真实取数验证(标 §LIVE 的步骤)需要网络与 tushare token;跑不通就如实标 BLOCKED,不许用假绿灯充数。

---

## Wave P0(契约 + 机械修复;T1→T8 顺序执行,T3–T7 相互独立可并行)

### Task 1: contracts 加 analyze 词汇 + `analyze_profile()`

**Files:**
- Modify: `autoresearch/contracts/stages.py`
- Create: `autoresearch/analyze/run_profile.py`
- Test: `tests/contracts/test_stage_vocabulary.py`(增)、`tests/analyze/test_run_profile.py`(新)

**Interfaces:**
- Produces: `contracts.stages.RUN_KINDS: tuple[str,...] = ("scan-market","stock-research")`;`ANALYZE_STAGES=("harvest","intel","write","assemble","publish")`;`ANALYZE_LITE_STAGES=("harvest","card")`;`ANALYZE_MODES=("FULL","LITE")`;`ANALYZE_ROLE_STAGES={"company-intel":"intel","us-intel":"intel"}`;`ANALYZE_CONDITIONAL_ROLES=frozenset({"company-intel","us-intel"})`(gated by 市场,缺席是事实不是洞)。
- Produces: `autoresearch.analyze.run_profile.analyze_profile(mode="FULL", business_status="SUCCEEDED", last_stage=None) -> RunProfile`(kind="stock-research";复用 `trace.capsule` 消费的同一 `RunProfile` dataclass 与 scan 的 `_BASE_RULES` 同款规则,但 `replayable_stages=()`——走湖前诚实为零)。
- Consumes: `scan/run_profile.py` 的 `RunProfile`/`ArtifactRule` dataclass——**先看它们定义在哪**:若在 `scan/run_profile.py` 内,把这两个 dataclass 搬进 `contracts/profiles.py` 并在 scan 侧留 re-export(analyze 不得 import scan)。

- [ ] **Step 1(核前提)**:`grep -n "class RunProfile\|class ArtifactRule" autoresearch/scan/run_profile.py autoresearch/trace/*.py autoresearch/contracts/*.py`。确认 dataclass 落点与 `scan_profile()` 的构造参数(本计划引用的签名快照见 spec §5 D6 与 C 报告 §1.3)。若已在 contracts,跳过搬家半步。
- [ ] **Step 2(搬声明,若需要)**:新建 `autoresearch/contracts/profiles.py`,把 `RunProfile`/`ArtifactRule` 原样搬入(不改字段);`scan/run_profile.py` 顶部改为 `from autoresearch.contracts.profiles import RunProfile, ArtifactRule`(保持旧名可导,全仓 import 不断)。跑 `uv run --no-sync python -m pytest tests/contracts tests/scan/test_run_profile*.py tests/trace -q` 确认绿。
- [ ] **Step 3(写失败测试)**:

```python
# tests/analyze/test_run_profile.py
from autoresearch.analyze.run_profile import analyze_profile
from autoresearch.contracts import stages as vocab

def test_analyze_vocab_registered():
    assert "stock-research" in vocab.RUN_KINDS
    assert vocab.ANALYZE_STAGES == ("harvest", "intel", "write", "assemble", "publish")
    assert vocab.ANALYZE_LITE_STAGES == ("harvest", "card")
    assert vocab.ANALYZE_MODES == ("FULL", "LITE")

def test_analyze_profile_full():
    p = analyze_profile(mode="FULL")
    assert p.kind == "stock-research"
    assert p.expected_stages == vocab.ANALYZE_STAGES
    assert p.replayable_stages == ()          # 走湖前诚实为零(T9 之后另议)
    assert "company-intel" in p.conditional_roles

def test_analyze_profile_lite_skips_intel():
    p = analyze_profile(mode="LITE")
    assert p.expected_stages == vocab.ANALYZE_LITE_STAGES
    assert p.agent_roles == ()                # lite 不派情报员(engine-playbook「lite 一律不派」)

def test_scan_vocab_untouched():
    # 既有 scan 词汇必须逐字未动(test_stage_vocabulary 用 `is` 锁副本,这里再锁个防呆)
    assert vocab.MODES == ("FULL", "FORCED_FULL", "SENTINEL_EMPTY", "SENTINEL_PINNED")
    assert vocab.STAGES[0] == "frame" and vocab.STAGES[-1] == "finalize"
```

- [ ] **Step 4**:`uv run --no-sync python -m pytest tests/analyze/test_run_profile.py -q` → FAIL(ImportError)。
- [ ] **Step 5(实现)**:`contracts/stages.py` 末尾**追加**(不动既有任何常量——`test_stage_vocabulary.py` 用 `is` 断言锁着它们):

```python
# ── stock-research(analyze)词汇(2026-08-31 D6.1;scan 词汇在上,一个字未动) ──
RUN_KINDS: tuple[str, ...] = ("scan-market", "stock-research")
ANALYZE_STAGES: tuple[str, ...] = ("harvest", "intel", "write", "assemble", "publish")
ANALYZE_LITE_STAGES: tuple[str, ...] = ("harvest", "card")
ANALYZE_MODES: tuple[str, ...] = ("FULL", "LITE")
ANALYZE_ROLE_STAGES: dict[str, str] = {"company-intel": "intel", "us-intel": "intel"}
ANALYZE_CONDITIONAL_ROLES: frozenset[str] = frozenset({"company-intel", "us-intel"})
```

`autoresearch/analyze/run_profile.py`:

```python
#!/usr/bin/env python3
"""stock-research 的证据 profile(D6.1)。analyze 不 import scan(分层棘轮)。"""
from __future__ import annotations

from autoresearch.contracts import stages as vocab
from autoresearch.contracts.profiles import RunProfile          # T1 Step2 落点
from autoresearch.scan.run_profile import _BASE_RULES as _SCAN_BASE_RULES  # ←若此行造成
# analyze→scan 向上边:把 _BASE_RULES 一并搬 contracts/profiles.py 并两侧 re-export,
# 规则内容零改动(它们全是 capsule 相对路径,kind 无关)。

TERMINAL = ("SUCCEEDED", "FAILED", "INTERRUPTED")

def analyze_profile(*, mode: str = "FULL", business_status: str = "SUCCEEDED",
                    last_stage: str | None = None) -> RunProfile:
    if mode not in vocab.ANALYZE_MODES:
        raise ValueError(f"unknown analyze mode: {mode!r}")
    if business_status not in TERMINAL:
        raise ValueError(f"unknown business status: {business_status!r}")
    lite = mode == "LITE"
    return RunProfile(
        kind="stock-research",
        expected_stages=vocab.ANALYZE_LITE_STAGES if lite else vocab.ANALYZE_STAGES,
        agent_roles=() if lite else tuple(vocab.ANALYZE_ROLE_STAGES),
        artifact_rules=_SCAN_BASE_RULES,
        replayable_stages=(),
        mode=mode, business_status=business_status, last_stage=last_stage,
        conditional_roles=vocab.ANALYZE_CONDITIONAL_ROLES,
    )
```

> 注意 import 注释:`_BASE_RULES` 若留在 scan 会造 analyze→scan 上边 → **必搬** `contracts/profiles.py`(内容零改,scan 侧 re-export)。搬完 `tests/contracts/test_layering.py` 必须仍绿且 `KNOWN_UPWARD` 无新增。
- [ ] **Step 6**:`uv run --no-sync python -m pytest tests/analyze/test_run_profile.py tests/contracts tests/scan/test_run_profile*.py -q` → PASS。
- [ ] **Step 7**:Commit `feat(contracts): stock-research 词汇 + analyze_profile(D6.1)`。

### Task 2: ARTIFACTS 登记 analyze 产物 + drift 守卫扩根

**Files:**
- Modify: `autoresearch/contracts/artifacts.py`、`tests/contracts/test_registry_parity.py`
- Test: 同上(守卫本身就是测试)

**Interfaces:**
- Produces: `ROOTS` 增 `("analyze_staging", "analyze_report")`(含义注释:`context_<engine>/analyze/<TICKER>_<YYYYMMDD>/` 与 `reports_<engine>/analyze/<run>/`);新增 ≥18 条 `Artifact` 行,`stage` 用 T1 的 analyze 词汇。
- Consumes: T1 的 `ANALYZE_STAGES`。

- [ ] **Step 1(核前提)**:`sed -n 229,271p autoresearch/contracts/artifacts.py` 看白名单现状;确认 `_slim.md`/`_slim_deep.md`/`lite-playbook.md` 是否仍在白名单(C 报告 §3.1 说在)。
- [ ] **Step 2(登记)**:在 `ARTIFACTS` 表尾追加(producer 按真实写者;`presence`:gated=按市场/档位;path 相对 root):

```python
    # ── stock-research(D6.1;root=analyze_staging 指 $CTX/analyze/<T>_<D>/,
    #    analyze_report 指 $RPT/analyze/<YYYYMMDD_HHMM>/) ──
    Artifact("analyze_full_context", "../<ticker>_<date>.md → 登记为 glob: *_????-??-??.md",
             "analyze_staging", "harvest", "analyze.harvest", "md", "gated",
             required_when="full"),
    # ↑ 实际写法:full context 落在 $CTX 根而非分段目录,登记 path 用相对 analyze_staging 的
    #   `../{TICKER}_{DATE}.md` 不可行(路径不许 ..)——改为给它单独 root=analyze_ctx
    #   (= context_root() 本身)并在 ROOTS 注释说明;实现者按此改,勿留 .. 路径。
    Artifact("analyze_slim", "{ticker}_{date}_slim.md", "analyze_ctx", "harvest",
             "analyze.harvest", "md", "gated", required_when="lite_or_scan"),
    Artifact("analyze_slim_deep", "{ticker}_{date}_slim_deep.md", "analyze_ctx", "harvest",
             "analyze.harvest", "md", "gated", required_when="lite_or_scan"),
    Artifact("analyze_sections", "1_analysts/*.md", "analyze_staging", "write",
             "stock-writer", "md", "always"),
    Artifact("analyze_research", "2_research/*.md", "analyze_staging", "write",
             "stock-writer", "md", "always"),
    Artifact("analyze_risk", "3_risk/*.md", "analyze_staging", "write",
             "stock-writer", "md", "always"),
    Artifact("analyze_portfolio", "4_portfolio/*.md", "analyze_staging", "write",
             "stock-writer", "md", "always"),
    Artifact("company_intel", "_company_intel.md", "analyze_staging", "intel",
             "company-intel", "md", "gated", required_when="full_ashare"),
    Artifact("us_intel", "_us_intel.md", "analyze_staging", "intel",
             "us-intel", "md", "gated", required_when="full_us"),
    Artifact("analyze_report_md", "*.md", "analyze_report", "assemble",
             "analyze.assemble", "md", "always"),
    Artifact("analyze_manifest", "manifest.json", "analyze_report", "assemble",
             "analyze.assemble", "json", "always"),
    Artifact("analyze_lite_card", "*_lite.md", "analyze_report", "card",
             "stock-writer", "md", "gated", required_when="lite"),
    Artifact("analyze_ledger_cards", "cards.csv", "analyze_ledger", "publish",
             "analyze.ledger", "csv", "gated", required_when="ledger"),
```

  同步:`ROOTS` 增 `"analyze_ctx", "analyze_staging", "analyze_report", "analyze_ledger"` 并在模块 docstring「根的含义」表补四行;从 `NON_ARTIFACT_LITERALS` **删去** `_slim.md`/`_slim_deep.md`(白名单只减不增的方向,守卫欢迎);glob/占位写法与表内既有行保持一致(先读几行既有 Artifact 对齐风格,`{ticker}` 类占位若表内不用就改成 glob `*_slim.md`)。
- [ ] **Step 3(守卫卫生)**:跑 `uv run --no-sync python -m pytest tests/contracts/test_registry_parity.py -q`——`test_registry_is_internally_consistent` 会逼:stage 必须 ∈ `stages.STAGES`。它今天只认 scan 词汇 → 改该测试的 stage 域为 `set(vocab.STAGES) | set(vocab.ANALYZE_STAGES)`(注释引 D6.1)。
- [ ] **Step 4(扩 drift 根)**:`_SCAN_ROOTS` 改为 `("autoresearch/scan", "autoresearch/trace", "autoresearch/analyze", ".claude/workflows")`。跑守卫 → 会红出 analyze 里未登记字面量清单;逐个处理:是产物 → 登记(回 Step 2 补行);不是产物(如 `readthrough_map.yaml`、`manifest.json` 在别根已登记同名 → 守卫按名字集合比对,同名即过)→ 进白名单前先想「它是不是就该登记」。**目标:守卫绿且白名单净增 ≤5 条,每条带理由注释。**
- [ ] **Step 5(变异探针)**:临时把 `analyze_manifest` 从表里注释掉 → 守卫必须红;还原。把这个探针写成测试:

```python
def test_analyze_artifacts_registered():
    names = {a.name for a in ARTIFACTS}
    assert {"analyze_slim", "analyze_report_md", "analyze_manifest",
            "company_intel", "us_intel"} <= names
```

- [ ] **Step 6**:全量 contracts 测试绿;Commit `feat(contracts): 登记 analyze 产物 + drift 守卫扩到 autoresearch/analyze(D6.1)`。

### Task 3: harvest 降级记账(29 处 T 级 → B 级)

**Files:**
- Modify: `autoresearch/analyze/harvest.py`(`_section` + main 各调用点)、`autoresearch/data/tushare_enrich.py`、`autoresearch/common/uzi_lenses.py`
- Test: `tests/analyze/test_harvest_degradation.py`(新)

**Interfaces:**
- Consumes: `autoresearch.data.contracts.record_degradation(endpoint, reason, *, key="", kind="degraded")`(签名已核实)与 `degradations()`。
- Produces: `_section(title, fn, *args, endpoint: str | None = None, **kwargs)`——异常时**先记账再写降级文本**;`harvest.main()` 尾部打印 `[数据降级账] N 条`。

- [ ] **Step 1(核前提)**:`sed -n 1643,1653p autoresearch/analyze/harvest.py` 确认 `_section` 形状(快照见本计划 Global 前调研);`grep -c "_section(" autoresearch/analyze/harvest.py` 得调用数(预期 ~30±)。
- [ ] **Step 2(失败测试)**:

```python
# tests/analyze/test_harvest_degradation.py
from autoresearch.analyze import harvest
from autoresearch.data import contracts as dc

def _boom():
    raise RuntimeError("vendor down")

def test_section_records_degradation():
    dc.clear_degradations()
    body = harvest._section("Ticker news", _boom, endpoint="stock_news_em")
    assert "_ERROR fetching this section" in body            # 原语义保留
    recs = dc.degradations()
    assert recs and recs[-1]["endpoint"] == "stock_news_em"  # 新:记了账

def test_section_without_endpoint_uses_slug():
    dc.clear_degradations()
    harvest._section("Global / macro news", _boom)
    assert dc.degradations()[-1]["endpoint"] == "analyze:global-macro-news"
```

- [ ] **Step 3** → FAIL(TypeError: unexpected keyword)。
- [ ] **Step 4(实现,中央收口)**:

```python
def _slug(title: str) -> str:
    return "analyze:" + re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")

def _section(title: str, fn, *args, endpoint: str | None = None, **kwargs) -> str:
    print(f"  - {title} ...", flush=True)
    try:
        out = fn.invoke(*args, **kwargs) if hasattr(fn, "invoke") else fn(*args, **kwargs)
        body = (out or "").strip() or "_(empty)_"
    except Exception as e:  # noqa: BLE001
        from autoresearch.data.contracts import record_degradation
        record_degradation(endpoint or _slug(title), f"{type(e).__name__}: {e}")
        body = f"_ERROR fetching this section: {e}_\n```\n{traceback.format_exc()}```"
    return f"\n## {title}\n\n{body}\n"
```

  然后给 `main()` 里**每个** `_section(` 调用补 `endpoint=`(端点名照 A 报告 §1 表的「数据源」列:已登记的用登记名 `stock_news_em`/`top_inst`/…,未登记的用 `analyze:yf-<块名>` 约定;不确定就用 `_slug` 缺省,宁可粗不可错)。
- [ ] **Step 5(内层吞异常点)**:`_section` 包不到的内层 try/except(A 报告点名):`tushare_enrich.py` 的 `ashare_market_context_ts` 各分块(4 处 `out.append(f"_tushare … 取数失败…")` 前加 `record_degradation("moneyflow"/"stk_factor_pro"/"cyq_perf"/"hk_hold", str(e), key=sym)`)、`ashare_shareholder_holdings`(holdernumber/pledge 2 处)、`corporate_calendar_ts`(forecast/express 2 处);`uzi_lenses.py` 的 `margin_trend_ts`/`lhb_seats`/`fina_indicator` 3 处 `return None` 前记账——**并把「非两融标的/无龙虎榜」与「取数炸」分开**:API 正常返回空帧 → `record_degradation(..., kind="legit_empty")`;异常 → 默认 kind。逐处贴近现场措辞,别改函数返回值语义。
- [ ] **Step 6(main 尾账)**:`main()` 写盘后追加:

```python
    from autoresearch.data.contracts import degradations, render as _deg_render
    degs = degradations()
    if degs:
        print(f"[数据降级账] {len(degs)} 条:{_deg_render(degs)}", flush=True)
```

- [ ] **Step 7**:测试绿 + 全量 `tests/analyze` 绿;§LIVE:`uv run --no-sync python -m autoresearch.analyze.harvest 300308 $(date +%F) --slim` 断网前后各跑一次,断网跑必须刷出成串 `[数据契约·B级降级]` 行。Commit `fix(analyze): harvest 降级全记账(29 处 T→B,D1.3)`。

### Task 4: PIT 8 处修复

**Files:**
- Modify: `autoresearch/analyze/harvest.py`、`autoresearch/data/tushare_enrich.py`、`autoresearch/common/uzi_lenses.py`
- Test: `tests/analyze/test_harvest_pit.py`(新)

**Interfaces:**
- Produces: 各函数新增/贯通 `curr_date` 参数;行为对「今天」跑 byte 不变,对「回填历史日」不再取未来。

- [ ] **Step 1(核前提,逐处)**:按下表 grep 核对行号与现状(对不上先修表):

| # | 现场 | 病 | 修法 |
|---|---|---|---|
| 1 | `harvest.py:~1403` `^VIX` `period="5d"` | 不锚 curr_date | 改 `start=(d-7天), end=curr_date` 的 history 调用,取 ≤curr_date 最后两行 |
| 2 | `uzi_lenses.py:~301` `margin_trend_ts` `end=datetime.now()` | 同 | `end=curr_date`(调用点已有 curr_date 可传) |
| 3 | `uzi_lenses.py:~273` `fina_indicator` `iloc[-1]` | 取最新期 | 先 `df[df["end_date"] <= curr_yyyymmdd]` 再取尾 |
| 4 | `tushare_enrich.py:~135` `stk_holdernumber` 最新行 | 同 | `ann_date <= curr` 过滤 |
| 5 | `tushare_enrich.py:~148` `pledge_stat` 最新行 | 同 | `end_date <= curr` 过滤 |
| 6 | `tushare_enrich.py:~170` `forecast` 取最新 ann_date | 同 | `ann_date <= curr` 过滤(express 已有 15 月守卫,补同过滤) |
| 7 | `harvest.py:1124-1190` earnings_quality 三张表 `_latest` 第 0 列 | 未过 filter_financials_by_date | 复用 `stockstats_utils.filter_financials_by_date`(#20 同款)后取列 |
| 8 | `harvest.py:1466-1528` solvency 同病 | 同 | 同 7 |

  另:`.info` 实时类(#19/#30 等)**不改取数**,在块首行加一句 `_as-of: 运行时刻(实时字段,不可回放)_`——诚实标注,不假装 PIT。
- [ ] **Step 2(失败测试)**:每处一条纯函数级用例,合成 DataFrame 里塞一行未来日期,断言被滤掉。示例(其余同型,逐处写全,不许「similar to」):

```python
# tests/analyze/test_harvest_pit.py
import pandas as pd
from autoresearch.common import uzi_lenses

def test_fina_indicator_respects_curr_date(monkeypatch):
    df = pd.DataFrame({"end_date": ["20241231", "20250630", "20991231"],
                       "roe": [10.0, 11.0, 99.0]})
    monkeypatch.setattr(uzi_lenses, "_fetch_fina_indicator", lambda *a, **k: df)
    out = uzi_lenses.ashare_native_financials("300308.SZ", curr_date="2026-08-30")
    assert "99.0" not in (out or "")
```

  (若函数没有可 monkeypatch 的取数缝,先做 5 行的提缝重构:`_fetch_xxx()` 独立出来——这本身就是本任务交付的一半。)
- [ ] **Step 3** → FAIL;**Step 4** 按表逐处实现;**Step 5** → PASS + `tests/analyze` 全绿。
- [ ] **Step 6**:§LIVE 同日双跑 parity:修前存 `git stash` 基线 md,修后同日重跑 `diff`——**「今天」跑必须逐字节相同**(PIT 修复只影响回填);不同即回归,查明再继续。
- [ ] **Step 7**:Commit `fix(analyze): 8 处 PIT 锚定 curr_date(D1.4)`。

### Task 5: assemble 硬校验 + parse_rating strict 档 + manifest v2

**Files:**
- Modify: `autoresearch/agents/utils/rating.py`、`autoresearch/analyze/assemble.py`
- Test: `tests/analyze/test_assemble.py`(增)、`tests/test_rating.py`(找到 parse_rating 现有测试文件,`grep -rl "parse_rating" tests/ | head`)

**Interfaces:**
- Produces: `parse_rating(text, *, strict: bool = False) -> str | None`——strict=True 时只认 `contracts.agent_output` 里 L4_CARD 的 `**Rating**:` keyed 行,匹配不到返回 **None**(不再默默 "Hold");strict=False 完全保持现行为(含全文兜底)。
- Produces: assemble 校验失败 → 非零退出:缺 `**Rating**` 行、缺 `FINAL TRANSACTION PROPOSAL: **BUY|HOLD|SELL**` 行 → `return 1` 并打印缺哪行。
- Produces: `manifest.json` v2:原 6 键 + `{"schema_version": 2, "engine": ws.ENGINE, "run_id": None, "context_file": "<harvest md 相对路径或 null>", "degradations": <int>, "rating": "<五档>", "proposal": "<BUY|HOLD|SELL>"}`(run_id 由 T13 填,先 None)。
- Fix: `_ashare_name_from_context` 的日期格式 bug(`300308.SZ_20260830` 目录 → 找 `300308.SZ_2026-08-30.md`)。

- [ ] **Step 1(核前提)**:`sed -n 25,50p autoresearch/agents/utils/rating.py`;`sed -n 120,135p autoresearch/analyze/assemble.py`(`_ashare_name_from_context`);确认 A 报告 §3.3 的 bug 复现:`python -c "…"` 用 `context_claude/analyze/300308.SZ_20260830` 真目录调它,看返回 None。
- [ ] **Step 2(失败测试)**:

```python
def test_parse_rating_strict_requires_keyed_line():
    from autoresearch.agents.utils.rating import parse_rating
    prose = "我们认为它不是 Buy,更接近观望。"
    assert parse_rating(prose) == "Buy" or parse_rating(prose)  # 旧行为:兜底(记录现状)
    assert parse_rating(prose, strict=True) is None             # 新:strict 不猜

def test_parse_rating_strict_reads_keyed():
    assert parse_rating("**Rating**: Underweight", strict=True) == "Underweight"

def test_assemble_fails_without_final_proposal(tmp_path):
    # 铺 11 个必需文件,decision.md 故意缺 FINAL TRANSACTION 行 → main() 返回 1
    ...  # 用 tests/analyze/test_assemble.py 既有夹具工厂,复制其铺文件辅助函数

def test_ashare_name_from_context_matches_dash_date(tmp_path):
    root = tmp_path / "300308.SZ_20260830"; root.mkdir()
    (tmp_path / "300308.SZ_2026-08-30.md").write_text("# Data context — 300308.SZ\n中际旭创",
                                                      encoding="utf-8")
    from autoresearch.analyze.assemble import _ashare_name_from_context
    assert _ashare_name_from_context("300308.SZ", root)  # 修前 None,修后取到名
```

- [ ] **Step 3** → FAIL;**Step 4(实现)**:rating.py 加 strict 分支(pattern 从 `contracts.agent_output` 取 L4_CARD 的 Rating field.pattern,不再手写第二份正则);assemble main 在 `parse_rating` 处改:

```python
    decision_text = _read(root, DECISION_REL)
    rating = parse_rating(decision_text, strict=True)
    proposal_m = re.search(r"FINAL TRANSACTION PROPOSAL:\s*\*\*(BUY|HOLD|SELL)\*\*",
                           decision_text)
    if rating is None or not proposal_m:
        print("[CONTRACT] decision.md 缺契约行:"
              + ("`**Rating**: <五档>` " if rating is None else "")
              + ("`FINAL TRANSACTION PROPOSAL: **…**`" if not proposal_m else ""))
        return 1
```

  manifest 写 v2 字段(`degradations` 读 `data.contracts.degradations()` 长度——同进程无值时 0;`context_file` 探测 `ws.context_root()/f"{ticker}_{dash_date}.md"` 存在即记相对路径)。`_ashare_name_from_context` 内把 `root.name` 的 `YYYYMMDD` 转 `YYYY-MM-DD` 再拼文件名。
- [ ] **Step 5** → PASS + `tests/analyze` 全绿(既有 test_assemble 若依赖软校验会红——按新契约更新它们,并在 commit msg 里写明「校验从软转硬」)。
- [ ] **Step 6**:Commit `feat(analyze): assemble 硬校验 + parse_rating strict + manifest v2(D1.6/D8.3)`。

### Task 6: harvest 正确性修复(带 parity 允许清单)

**Files:**
- Modify: `autoresearch/analyze/harvest.py`
- Test: `tests/analyze/test_harvest.py`(增)

**Interfaces / 修复清单(每条=一个子步:失败测试→实现→绿)**:
1. **A 股新闻窗+去重**:akshare `stock_news_em` 路(`harvest.py:1217-1238`)按 `news_start≤日期≤end` 过滤 + 按(标题)去重 + 按时间倒序;测试用合成 DataFrame(含窗外行与重复行)断言输出条数。
2. **fwd-PE 补价**:`consensus_eps_block` 调用点(`:2042`)把同进程已取的 snapshot close 传进 `price=`;测试:给定 eps 与 price 断言输出含 `fwd-PE` 列。
3. **gnews 中文查询**:A 股时 `gnews_block` 查询词优先用 `--name`/identity 里的中文简称,fallback 英文 longName(`:957,2034`);测试:断言 A 股 query 含中文名。
4. **AUTORESEARCH_OFFLINE**:`main()` 开头 `if os.environ.get("AUTORESEARCH_OFFLINE"): print(...); return 2`(与 `yf_options` 既有语义对齐——先 `grep -n "AUTORESEARCH_OFFLINE" autoresearch/data/sources/*.py` 对齐措辞);测试 monkeypatch env 断言 rc=2 且零网络调用。
5. **`_SLIM_ANCHORS` 双侧锁**:新增测试(harvest 侧)断言 slim 渲染包含 scan 依赖的四个标题——**从 `autoresearch/scan/l4/producers.py` 把 `_SLIM_ANCHORS` 常量搬进 `contracts/agent_output.py`(名 `SLIM_ANCHORS`),producers 改 import**(scan→contracts 合法),harvest 测试也 import 同源;变异探针:改一个标题字符串 → 两侧测试都红。
6. **L1 列名契约**:harvest 读 L1 的 16 列(`:1710-1770`)提成 `contracts/agent_output.py` 的 `L1_REUSE_COLUMNS: tuple[str,...]`,harvest 与(grep 找到的)L1 生产者测试同引;缺列时的静默回退改为回退 + `record_degradation("L1_scored_full", "列缺失: …", kind="legit_empty")`。

- [ ] **Step 1(核前提)**:逐条 grep 行号;`grep -rn "_SLIM_ANCHORS" autoresearch/ tests/`。
- [ ] **Step 2–7**:按清单 1–6 各走 失败测试→实现→绿(测试全部写进 `test_harvest.py`,函数名 `test_news_window_filter` / `test_news_dedup` / `test_fwd_pe_uses_snapshot_close` / `test_gnews_query_chinese` / `test_offline_short_circuits` / `test_slim_anchors_single_source` / `test_l1_reuse_columns_single_source`)。
- [ ] **Step 8(parity)**:§LIVE 同日双跑 diff,**允许清单**=新闻块(窗过滤会删旧条)、fwd-PE 行(新增)、gnews 节(query 变)——其余块必须逐字节同;把允许清单写进 commit msg。
- [ ] **Step 9**:Commit `fix(analyze): 新闻窗/去重·fwd-PE·gnews 中文·OFFLINE·slim 锚单源(D1.6)`。

### Task 7: scan 侧解析脆弱点五修(D8.3)

**Files:**
- Modify: `autoresearch/scan/l4/parsers.py`、`autoresearch/scan/relative_buy.py`、`autoresearch/scan/decision_finalize.py`、`autoresearch/scan/outcome.py`、`autoresearch/contracts/agent_output.py`
- Test: `tests/scan/test_gate_status_tolerant.py`(增)、`tests/scan/test_early_stop_parse.py`(增)、`tests/contracts/test_registry_parity.py`(增)

**Interfaces:**
- ①gate 记号容错:`_mark_after` 认 `✓ ✗ ✔ ✘ ×`(`✔`→`✓`,`✘/×`→`✗`);段内无任何记号时 `gate_status` 返回 **None**(不再"首段全 False=三门全 PASS")——**核对下游**:`grep -n "gate_status(" autoresearch/scan/*.py` 逐个消费者确认 None 已是合法输入(早停卡本来就返回 None)。
- ②红灯死条目:`REDFLAG_EARLY_STOP_REASONS` 删除 `"监管/审计红灯"`(七词表外,逐字实现永不命中——纯死码删除,行为零变);加 ratchet 测试 `REDFLAG_EARLY_STOP_REASONS <= set(agent_output.STOP_REASONS)`。
- ③ensemble 静默吞 → 响亮失败:`_load_ensemble` 的 `except Exception: continue` 改为:重命名坏文件为 `<p>.bad` + `print(f"[ensemble] 坏 JSON 已隔离: {p.name}", file=sys.stderr)` + 视同 `degraded=True, spread=1` 的合成记录进 `out`(强制 ens_flag 人裁展示,分歧不静默消失——与 `_ensemble_flag` 现语义一致)。
- ④rating strict-with-warn:`parsers._finalist_row` 里 `parse_rating(text)` 改为先 `strict=True`;None 时走旧宽松路 **并** `print(f"[card-contract] {code} Rating 行缺失,已启用全文兜底", file=sys.stderr)`(行为读数不变、可观测性+1;硬切等 P2 D8.2)。
- ⑤执行线阈值单源:`contracts/agent_output.py` 加 `EXEC_LINE_MAX_PCT_1D = 3.0` / `EXEC_LINE_MAX_POS_IN_RANGE = 0.7`;`outcome.py` 的两常量改为 re-export 引用;新测试断言 `lite-playbook.md` 与 `l4-card.md` 文本里出现的 `3.0`/`0.7` 与常量一致(doc-lint 型,防散文漂移)。

- [ ] **Step 1(核前提)**:逐处 sed 确认(快照已录:`parsers.py:295-335`、`relative_buy.py:175-190`、`decision_finalize.py:100-135`、`outcome.py:69-72`)。
- [ ] **Step 2(失败测试,逐条)**:

```python
def test_gate_marks_tolerant_glyphs():
    from autoresearch.scan.l4.parsers import gate_status
    seg = "OW三门 主力真在 ✘·业绩真兑现 ✔·估值不透支 ×"
    st = gate_status(seg)
    assert st == {"主力真在": True, "业绩真兑现": False, "估值不透支": True}  # True=✗失守

def test_gate_status_unmarked_returns_none():
    from autoresearch.scan.l4.parsers import gate_status
    assert gate_status("OW三门 主力真在·业绩真兑现·估值不透支(散文提及,无记号)") is None

def test_redflag_reasons_subset_of_closed_set():
    from autoresearch.scan.relative_buy import REDFLAG_EARLY_STOP_REASONS
    from autoresearch.contracts.agent_output import STOP_REASONS
    assert REDFLAG_EARLY_STOP_REASONS <= set(STOP_REASONS)

def test_load_ensemble_quarantines_bad_json(tmp_path, capsys):
    (tmp_path / "_ensemble_300308.json").write_text("{oops", encoding="utf-8")
    from autoresearch.scan.decision_finalize import _load_ensemble
    out = _load_ensemble(tmp_path)
    assert (tmp_path / "_ensemble_300308.json.bad").exists()
    assert out["300308"]["degraded"] is True          # 强制人裁,不静默

def test_exec_thresholds_single_source():
    from autoresearch.contracts import agent_output as ao
    from autoresearch.scan import outcome
    assert outcome.EXEC_MAX_PCT_1D is ao.EXEC_LINE_MAX_PCT_1D
```

- [ ] **Step 3** → FAIL;**Step 4** 按 Interfaces 逐条实现(①的 None 语义改动要跑全量 `tests/scan` 并逐个红点确认是「测试锁了旧的错误语义」才改测试);**Step 5** → 全绿。
- [ ] **Step 6**:对 08-26 真 run 回放兜底:`uv run --no-sync python - <<'PY'` 脚本对 `reports_claude/scan/20260826_2120/details/*.md` 逐卡跑新旧 `gate_status`/`parse_rating` 对比,**打印差异行**——预期:真卡全部无差(它们用的是规范 ✓✗);有差即回归,停下查。
- [ ] **Step 7**:Commit `fix(scan): 门记号容错·红灯死条目·ensemble 响亮失败·rating strict-warn·执行线单源(D8.3)`。

### Task 8: analyze 账本骨架 + nightly-close 第 5 步 + slim 污染防护

**Files:**
- Create: `autoresearch/analyze/ledger.py`
- Modify: `scripts/nightly_close.sh`、`autoresearch/analyze/harvest.py`(`_output_dir`)、`tests/scan/test_web_budget_wiring.py`(nightly 头注释对齐测试)
- Test: `tests/analyze/test_ledger.py`(新)、`tests/analyze/test_harvest.py`(增)

**Interfaces:**
- Produces: `analyze.ledger` CLI:`ingest`(扫 `ws.reports_root()/"analyze"/*/manifest.json` + `*_lite.md`,新条目 append 进 `ws.reports_root()/"analyze"/"_ledger"/cards.csv`;幂等键 `(run_dir, file)`)与 `fill`(对已 ingest 行按 `common.ruler.MAIN_RULER`(gap_c1_o2)补 T+1/T+2 事后列,D+2 成熟才算;full 报告另补 `fwd_5/10/20`)。列:`ingested_at, run_dir, file, ticker, name, market, tier(full|lite), analysis_date, engine, rating, proposal, ev_pct, scenario_p_bull, scenario_p_base, scenario_p_bear, gap_c1_o2, fwd_5, fwd_10, fwd_20, matured`。评级/情景从 md 解析:rating 用 `parse_rating(strict=True)`(缺→行留空+`contract_ok=false` 列);情景概率行 P2 的 D4.9 才有,先全空——列先立好,**不改卡模板**。
- Produces: nightly_close.sh 第 5 步 `step "analyze ledger" autoresearch.analyze.ledger nightly`(= ingest + fill 连跑);**同步更新脚本头注释清单**(它有测试钉着两边一致——先跑 `uv run --no-sync python -m pytest tests/scan/test_web_budget_wiring.py -q` 看锁法)。
- Fix: `harvest._output_dir` 独立跑不再写进活跃 scan run:slim 无 `--out-dir` 时一律落 `ws.context_root()`(scan 的全部调用点都显式传 `--out-dir`——Step 1 验证此前提)。

- [ ] **Step 1(核前提)**:`grep -rn "analyze.harvest" autoresearch/scan/ .claude/workflows/ | grep -v test` 确认每个 scan 调用点都带 `--out-dir`(B 报告 §5 已核:producers `:286-294` 与 l4_tasks 传参;再亲核一遍)。若有不带的 → 先给那个调用点补 `--out-dir`,再改缺省。
- [ ] **Step 2(失败测试)**:

```python
# tests/analyze/test_ledger.py
def test_ingest_and_fill_roundtrip(tmp_path, monkeypatch):
    # 铺一个假 reports/analyze/20260830_1738/{manifest.json,中际旭创.md}
    # manifest v2 + decision 文本含 **Rating**: Hold / FINAL … **HOLD**
    # monkeypatch ws.reports_root → tmp_path;跑 ingest 两次 → cards.csv 恰 1 行(幂等)
    # fill 用 monkeypatch 的假湖收益函数 → gap_c1_o2 列被填、matured 翻 true
    ...

def test_standalone_slim_never_writes_into_run(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", "20260830T120000000000Z")
    from autoresearch.analyze.harvest import _output_dir
    out = _output_dir("2026-08-30", slim=True, explicit=None)
    assert "scan_runs" not in str(out)        # 修前会命中 run staging(B §5 污染面)
```

  (ingest/fill 测试写全:铺文件的辅助直接放测试文件里 ~30 行,不引生产夹具。)
- [ ] **Step 3** → FAIL;**Step 4** 实现 `ledger.py`(~150 行:csv append via `csv` 模块 + 文件锁 `portalocker`? 不引新依赖——用 `os.open(O_CREAT|O_EXCL)` 锁文件模式,照 `scan/outcome.py` 的 upsert 写法先 `grep -n "LEDGER_CSV" autoresearch/scan/outcome.py` 抄同款原子写);`_output_dir` 改缺省;nightly 加步+头注释。
- [ ] **Step 5** → PASS + `tests/scan/test_web_budget_wiring.py` 绿(头注释对齐)。
- [ ] **Step 6**:§LIVE `bash scripts/nightly_close.sh` 手跑一次看第 5 步 ok 行(空账本也要 ok)。
- [ ] **Step 7**:Commit `feat(analyze): 结果账本骨架 + nightly 第5步 + 独立 slim 不入 run 现场(D5.1/D8.5)`。

**── P0 收尾门**:全量 pytest 绿;`git log --oneline` 8 个干净 commit;把「29 处记账清单」「PIT 8 处」「允许清单」的实际数字回填进设计稿 §5 各 D 的验收栏(动稿仅追加「实施记录」小节,不改正文)。

---

## Wave P1(走湖 + capsule;T9→T10→T11 顺序,T12→T13→T14→T15 顺序,两串可并行,T16 随时)

### Task 9: 已登记 tushare/akshare 端点改走湖(get_or_fetch)

**Files:**
- Modify: `autoresearch/data/tushare_enrich.py`、`autoresearch/common/uzi_lenses.py`、`autoresearch/analyze/harvest.py`
- Test: `tests/analyze/test_harvest_lake.py`(新)

**Interfaces:**
- Consumes: `data.cache.get_or_fetch(endpoint, params, today=None, fetch=None) -> DataFrame`(命中湖→读;否则 sources.fetch→契约校验→原子写;`AUTORESEARCH_RUN_ID` 在场时自动进 `lineage/reads.jsonl`)。
- **范围裁定(写死,防实现者跑偏)**:只改「**endpoints.py 已登记且 `data/sources` 已有 fetcher** 的 DataFrame 形态端点」。目标清单(逐个在 Step 1 验证 fetcher 在不在):`moneyflow`、`stk_factor_pro`、`cyq_perf`、`hk_hold`、`margin_detail`、`top_inst`、`stk_holdernumber`、`pledge_stat`、`forecast`、`express`、`stock_news_em`、`stock_restricted_release_queue_em`、`stock_lhb_stock_statistic_em`、`stock_zh_a_gdhs_detail_em`。**不碰**:yfinance/.info/EDGAR/gnews/options(非 DataFrame 或 snapshot 语义,留直调+T3 记账;它们在 D6.5 的完整性口径里是 L0/L1 级——这是 spec D1「全走湖」在现实约束下的显式收窄,记进实施记录)。
- Produces: 端点若是 date 键全市场帧(如 `moneyflow`),harvest 侧改为 `get_or_fetch(ep, {"date": d}) → df[df.ts_code==tc]`(与 scan 同湖共享,扫描日免费命中);若 fetcher 只支持逐票参数则按登记的 key 传参。**行为红线**:输出 md 数字必须与直调一致(同日 parity)。

- [ ] **Step 1(核前提,逐端点)**:`grep -n "\"<ep>\"" autoresearch/data/endpoints.py && grep -rn "def fetch\|<ep>" autoresearch/data/sources/ | grep <ep>` 填一张「端点 | key | fetcher 有无 | 参数形状」表进任务笔记;**fetcher 缺的端点从本任务范围剔除并记名**(不现场造 fetcher——那是 P2 D2 的事)。
- [ ] **Step 2(失败测试)**:

```python
# tests/analyze/test_harvest_lake.py
def test_moneyflow_goes_through_lake(monkeypatch):
    calls = []
    def fake_gof(endpoint, params, today=None, fetch=None):
        calls.append(endpoint)
        return _synth_moneyflow_df()          # 测试内合成 10 日帧
    monkeypatch.setattr("autoresearch.data.cache.get_or_fetch", fake_gof)
    from autoresearch.data.tushare_enrich import ashare_market_context_ts
    out = ashare_market_context_ts("300308.SZ", "2026-08-28")
    assert "moneyflow" in calls               # 修前:直调 pro.moneyflow,列表为空
    assert "主力资金流" in (out or "")
```

  同型用例覆盖清单内每个端点的第一读者(一端点一测,函数名 `test_<ep>_goes_through_lake`)。
- [ ] **Step 3** → FAIL;**Step 4** 逐端点改写(直调 lambda 挪进 `fetch=` 参数只在 fetcher 缺参数形状时用;能走 sources 缺省就不传 fetch);**Step 5** → PASS。
- [ ] **Step 6(§LIVE parity + lineage)**:同日双跑 diff = 逐字节同;然后 `AUTORESEARCH_RUN_ID=<用 T13 之前的临时法:export 一个假 id 会被 validate 拒——改为跑在 T13 完成后补验,此处先记 TODO 到 T13 Step 7>`。parity 先行,lineage 计数验证挂 T13。
- [ ] **Step 7**:Commit `refactor(analyze): 14 端点改走 cache.get_or_fetch(D1.1;范围收窄记录在案)`。

### Task 10: harvest.py 模块拆分 + (market,tier) 派发表

**Files:**
- Create: `autoresearch/analyze/blocks_external.py`(≈`harvest.py:152-980`)、`blocks_ashare.py`(akshare 老路+tushare 包装+UZI 包装+L1 复用渲染)、`blocks_us.py`(分析师/日历/同业/做空/US regime)、`blocks_statements.py`(盈利质量/偿付)、`slim_io.py`(L1 行读取+二段式落盘)
- Modify: `autoresearch/analyze/harvest.py`(只剩 env/常量/`_section`/`_slug`/派发表/main)
- Test: `tests/analyze/test_harvest_dispatch.py`(新)

**Interfaces:**
- Produces: `HARVEST_PLAN: dict[tuple[str, str], tuple[str, ...]]`——键 `(market ∈ {"ashare","us","crypto","other"}, tier ∈ {"full","slim"})`,值=有序块名 tuple;`main()` 按表循环调 `BLOCKS[name]`(名→callable 注册表)。这张表就是 SKILL.md:38 手抄清单的机器真身。
- 红线:**纯搬家**——函数体逐字节不动(允许 import 行变化);拆完同日双跑 **byte-identical**(无允许清单)。

- [ ] **Step 1(核前提)**:`grep -n "^def \|^# ---" autoresearch/analyze/harvest.py | head -90` 对照 A 报告 §6 的 11 段边界表,确认切点行号。
- [ ] **Step 2(失败测试)**:

```python
def test_dispatch_table_covers_both_tiers():
    from autoresearch.analyze.harvest import HARVEST_PLAN
    assert ("ashare", "slim") in HARVEST_PLAN and ("us", "full") in HARVEST_PLAN
    slim = HARVEST_PLAN[("ashare", "slim")]
    assert "price_history_400d" not in slim         # slim 不拉 400 天(既有语义)
    assert "verified_snapshot" in slim

def test_every_plan_block_is_registered():
    from autoresearch.analyze.harvest import HARVEST_PLAN, BLOCKS
    for blocks in HARVEST_PLAN.values():
        for b in blocks:
            assert b in BLOCKS, f"派发表引用未注册块 {b}"
```

- [ ] **Step 3** → FAIL;**Step 4** 搬家+建表(main 的 12 个 if 逐个翻译成表行;翻译时对照 A §3.1 的 slim/full 差异清单逐块核对,漏一块 parity 会逮);**Step 5** → PASS + `tests/analyze` 全绿。
- [ ] **Step 6(§LIVE)**:同日双跑 **full 与 slim 各一票**,md5 逐字节同;`wc -l autoresearch/analyze/harvest.py` 应 <500。
- [ ] **Step 7**:Commit `refactor(analyze): harvest 拆五模块 + (market,tier) 派发表单源(D1.1)`。

### Task 11: full 档瘦身(OHLCV 60 日+52 周、指标末值化)

**Files:**
- Modify: `autoresearch/analyze/harvest.py`(派发表)、`autoresearch/analyze/blocks_us.py` 或对应块模块
- Test: `tests/analyze/test_harvest.py`(增)

**Interfaces:**
- Produces: 块 `price_history_400d` 替换为 `price_history_compact`:近 60 交易日日线表 + 52 周周线聚合表(O=首日开,H/L=周极值,C=末日收,V=和;由同一 OHLCV 源 DataFrame 纯函数聚合);12 个 `## <ind> values` 30 天序列块替换为一张「指标末值+5 日前值+方向」汇总表(序列本身移入 deep 附件文件 `<TICKER>_<date>_indicators.md`,按需读——**新文件先登记 ARTIFACTS**)。
- 这是**有意的 golden 变化**(Q7 已裁):commit msg 记预期字节降幅;lite/slim 完全不动。

- [ ] **Step 1(核前提)**:量基线:`wc -c context_claude/300308.SZ_2026-08-30.md`(69,065B)与其中 OHLCV+指标块字节(A 报告:≈45%)。
- [ ] **Step 2(失败测试)**:合成 400 行 OHLCV DataFrame → `weekly_aggregate(df)` 返回 52±1 行且 H=max/L=min/V=sum 逐列断言;`indicator_summary_table(...)` 输出行数=指标数。
- [ ] **Step 3** → FAIL;**Step 4** 实现(聚合用 pandas `resample("W-FRI")` on DatetimeIndex;注意 tushare/yfinance 两路的日期列名差——先 grep 现块的列名);**Step 5** → PASS。
- [ ] **Step 6(§LIVE)**:同票重跑 full,新 context 字节应 ≈40–55KB(降 ~30–45%);肉眼核对周线表末行=最近周。
- [ ] **Step 7**:Commit `feat(analyze): full context 瘦身——OHLCV 60d+52w·指标末值表(D1.5,Q7)`。

### Task 12: capsule kind 化(七处 scan 字面量 → run_kind 派发)

**Files:**
- Modify: `autoresearch/trace/capsule.py`、`autoresearch/common/workspace.py`、`autoresearch/trace/completeness.py`、`autoresearch/trace/replay.py`、`autoresearch/scan/run_contract.py`(声明搬家)
- Create: `autoresearch/contracts/run_identity.py`(RunContract dataclass + 序列化/哈希,scan 侧 re-export)
- Test: `tests/trace/test_capsule_kinds.py`(新)+ 既有 `tests/trace` 全量

**Interfaces:**
- Produces: `workspace.run_root(kind: str, run_id: str | None = None) -> Path`——`{"scan-market": "scan_runs", "stock-research": "analyze_runs"}`;`scan_run_root` 变 `run_root("scan-market", …)` 别名(签名不变,全仓调用不断)。
- Produces: `capsule.begin_run(kind, …, bootstrap=None)`——`kind` 校验改为 `kind in contracts.stages.RUN_KINDS`;`bootstrap` 缺省时按 kind 内部派发:scan→`scan.run_bootstrap.prepare_scan_run`(现状边,已在 KNOWN_UPWARD),stock-research→**必须由调用方传入**(trace 不 import analyze,防新上边;T13 的 runctl 传)。
- Produces: 七处字面量逐一 kind 化——`load_run` 根(`capsule.py:534`)、`_validate_report_dir`(`:920`,analyze 认 `reports_root()/"analyze"`)、`ledger_path/failed_root/archive_root/repairs_root`(`:1983-1992,2947-2948`,analyze 前缀 `reports_<eng>/analyze/…`)、`_resolve_run_mode`(`:2308-2345`,kind=stock-research 时读 `contract.config_echo["mode"]`,不找 `run_mode.json`)、finalize profile 选择(`:2490-2494`)与 `completeness.profile_from_capsule`(`completeness.py:319-333`)按 `contract.run_kind` 派发到 `analyze_profile`(**用延迟 import `importlib` 按 kind 取 profile 工厂注册表**,注册表放 `contracts/profiles.py`:`PROFILE_FACTORIES: dict[str, str] = {"scan-market": "autoresearch.scan.run_profile:scan_profile", "stock-research": "autoresearch.analyze.run_profile:analyze_profile"}`,trace 只读字符串再 import——分层守卫按 AST 查静态 import,动态注册表不产生新边;在注册表旁注释此设计意图)。
- Produces: `contracts/run_identity.py` = `scan/run_contract.py` 的 RunContract dataclass + `_hash_payload` + (de)serialize **原样搬入**;`scan/run_contract.py` 顶部 re-export 全部旧名。**红线**:v1/v2/v3 契约哈希逐字节兼容(既有测试锁着——先 `grep -rln "run_contract" tests/ | head` 找到并全跑)。
- `replay.default_stage_specs`:kind=stock-research 时返回空表(replayable_stages=(),T1 已诚实声明);不动 scan 表。

- [ ] **Step 1(核前提)**:逐处 sed 核七个行号(C 报告 §2.2 表);`grep -rn "from autoresearch.scan.run_contract import\|scan.run_contract" autoresearch/ | grep -v scan/` 列出 trace 侧现有 import(搬家后这些改指 contracts,**KNOWN_UPWARD 相应行删除**——向上边净减,棘轮方向正确)。
- [ ] **Step 2(失败测试)**:

```python
# tests/trace/test_capsule_kinds.py(用 tests/trace/conftest.py 既有夹具起临时 ws)
def test_run_root_by_kind(tmp_ws):
    from autoresearch.common import workspace as ws
    assert "analyze_runs" in str(ws.run_root("stock-research", "X" * 22))
    assert ws.scan_run_root("X" * 22) == ws.run_root("scan-market", "X" * 22)

def test_begin_run_accepts_stock_research(tmp_ws, analyze_bootstrap):
    from autoresearch.trace import capsule
    h = capsule.begin_run("stock-research", "2026-08-30", tmp_ws.ENGINE,
                          config={"mode": "FULL", "ticker": "300308.SZ"},
                          bootstrap=analyze_bootstrap)
    assert h.contract.run_kind == "stock-research"
    assert "analyze_runs" in str(h.workspace)

def test_finalize_uses_analyze_profile(tmp_ws, analyze_run_happy_path):
    # 合成:begin → checkpoint(harvest) → checkpoint(assemble) → finalize
    # 断言 verification/expected.json 的 stages == ANALYZE_STAGES 且不含 "l4"
    ...
```

  (`analyze_bootstrap` 夹具:最小 `prepare_analyze_run` 桩,T13 落真身前先在 conftest 造 20 行桩——契约字段齐即可。)
- [ ] **Step 3** → FAIL;**Step 4** 按 Interfaces 实现(顺序:搬 run_identity → run_root → begin_run → 七处 → profile 注册表);**Step 5** → `tests/trace` + `tests/scan` + `tests/contracts` 全绿(scan 一条不许红——kind 化对 scan 是恒等变换)。
- [ ] **Step 6(变异探针)**:把注册表里 stock-research 行注释掉 → `test_finalize_uses_analyze_profile` 必须红;还原。
- [ ] **Step 7**:Commit `feat(trace): capsule 去 scan 化——run_kind 派发七处 + RunContract 声明进 contracts(D6.2)`。

### Task 13: analyze 运行接线(runctl:begin/checkpoint/bind/finalize)+ SKILL 更新

**Files:**
- Create: `autoresearch/analyze/run_bootstrap.py`、`autoresearch/analyze/runctl.py`
- Modify: `autoresearch/analyze/harvest.py`、`autoresearch/analyze/assemble.py`(进程内 checkpoint)、`.claude/skills/stock-research/SKILL.md`(**人批**)
- Test: `tests/analyze/test_runctl.py`(新)

**Interfaces:**
- Produces: `prepare_analyze_run(analysis_date, *, config: dict, run_id, engine, workspace_path, session_ref, now) -> RunContract`——字段:`run_kind="stock-research"`、`config_echo={"mode": "FULL"|"LITE", "ticker": …}`、git/prompt 哈希复用 `contracts.run_identity` 的同款 helper(T12 搬过去的)。
- Produces: CLI `python -m autoresearch.analyze.runctl <verb>`:
  - `begin <ticker> <date> --mode FULL|LITE [--session-ref <id>]` → 打印 `RUN_ID=…`(调 `capsule.begin_run("stock-research", …, bootstrap=prepare_analyze_run)`);
  - `bind <run_id> <transcript_path> --role main|company-intel|us-intel [--invocation-id I]`(薄包 `capsule` 既有 bind CLI 语义;Codex 加 `--from-ordinal/--to-ordinal` 透传);
  - `finalize <run_id> [--report-dir <path>] [--status SUCCEEDED|FAILED]` → capsule.finalize + 若 report-dir 给出,把其 `manifest.json` 的 `run_id` 字段回填(manifest v2 的 None → 真值;**finalize 前**回填,发布目录冻结语义不破)。
- Produces: harvest/assemble 在 `AUTORESEARCH_RUN_ID` 在场时进程内 checkpoint(照 `scan/stage_result.py:191-217` 的形状**在 runctl 里实现 25 行等价函数** `record_stage(stage, inputs, outputs, result)`,直接调 `capsule.checkpoint`——不 import scan):harvest 收尾 `record_stage("harvest", …, outputs=[写出的文件])`;assemble 收尾 `record_stage("assemble", …)`。
- SKILL.md 改动(人批 diff,full 档流程节追加一小节,lite 同理):

```markdown
### 现场留存(可选但推荐;D6)
0. 开场:`uv run --no-sync python -m autoresearch.analyze.runctl begin <TICKER> <date> --mode FULL --session-ref <本会话 sessionId>` → `export AUTORESEARCH_RUN_ID=<回显>`
5.5 组装后:`… runctl bind <RUN_ID> ~/.claude/projects/<slug>/<sessionId>/subagents/agent-<情报员id>.jsonl --role company-intel`(有情报员时)
6.5 收尾:`… runctl finalize <RUN_ID> --report-dir $RPT/analyze/<YYYYMMDD_HHMM>`
不开 RUN_ID 时一切照旧(零留痕,与今天相同)。
```

- [ ] **Step 1(核前提)**:`grep -n "bind-transcript\|def checkpoint" autoresearch/trace/capsule.py | head` 确认 CLI 动词与 checkpoint 签名;`sed -n 191,217p autoresearch/scan/stage_result.py` 抄形状。
- [ ] **Step 2(失败测试)**:`test_runctl_begin_finalize_roundtrip(tmp_ws)`——begin→假 harvest 产物落 staging→record_stage×2→finalize→`capsule.verify` 三结论可读且 `integrity_ok is True`;`test_lite_mode_expected_stages`——LITE run 的 expected 不含 "intel"/"write"。
- [ ] **Step 3** → FAIL;**Step 4** 实现;**Step 5** → PASS + 全量 `tests/analyze tests/trace` 绿。
- [ ] **Step 6(§LIVE,含 T9 的补验)**:真跑一票 slim:`begin` → `AUTORESEARCH_RUN_ID=… harvest 300857 <date> --slim` → `finalize`;然后 `wc -l <run>/capsule/lineage/reads.jsonl` **≥ T9 清单端点数**(走湖读点第一次进 lineage——这是 T9 Step 6 挂到这里的验收);`capsule verify <run_id>` 输出三结论。
- [ ] **Step 7**:SKILL.md diff 提请用户过目(计划执行者在 PR/commit 描述里贴 diff);Commit `feat(analyze): runctl begin/bind/finalize + harvest/assemble checkpoint(D6.3/6.4)`。

### Task 14: adapter 四修(usage session_ref · Codex web_search · 收割盲区 · stock_full D-5)

**Files:**
- Modify: `autoresearch/trace/usage_harvest.py`、`autoresearch/trace/transcripts/codex.py`、`autoresearch/trace/capsule.py`(归档面)、`autoresearch/analyze/runctl.py`(finalize 调 D-5)
- Test: `tests/trace/test_transcript_adapters.py`(增)、`tests/trace/test_capsule_d5_wiring.py`(增)

**Interfaces:**
- ①`collect_run`:`RunIdentity(run_id=…, engine=…, session_ref=contract.session_ref)`——从 run 的 contract 读(`load_run(run_id).contract.session_ref`;None 保持现状返回 UNMEASURED 行,不是 [])。测试:合成 run + 假 projects 目录 → 有 session_ref 时 locate 到 main transcript。
- ②Codex `web_search_call`:从 `_SKIPPED_RESPONSE_ITEMS` 移除;normalize 分支:`response_item.web_search_call` → `add("tool_request", {"name": "web_search", "input": payload.get("action") or {}, "tool_call_id": payload.get("call_id") or payload.get("id")})`;`event_msg` 分支加 `web_search_end` → `add("tool_result", {"name": "web_search", "tool_call_id": payload.get("call_id"), "output": {"query": payload.get("query"), "results": payload.get("results")}})`。测试:往 `tests/trace/fixtures/codex/rollout.jsonl` 追加两行合成 web_search 记录(照 W4 §3.1 实拆字段)→ normalize 后 items 含 name="web_search" 的 request/result 对;再断言 `capsule._external_tool_rows` 能吐出该行(`web_search` 不在 `LOCAL_TOOL_NAMES`——核实,若在则从表中移除并注释)。
- ③收割盲区:`_archive_bound_transcripts` 归档 main transcript 时**顺带**归档 `<session>/tool-results/*`(gzip 进 `agents/tool_results/`,mtime=0 同纪律)——只在绑定了 role=main 的 Claude transcript 时触发;subagents 无需新码(locate 已枚举,绑定即归档)。测试:合成 session 目录含 `tool-results/x.txt` → finalize 后 capsule 里存在 `agents/tool_results/x.txt.gz`。
- ④stock_full D-5:runctl `finalize` 在 capsule.finalize 之后(它内部已含 `_materialize_external_evidence`——**Step 1 核实**它对非 scan kind 是否也跑;若 kind 检查挡住,把该子步的 kind 依赖去掉,B 级失败不阻断语义照旧)。验收:真跑 full 一票后 `<run>/capsule/lineage/web_budget.json` 存在且 `measurement` ∈ {MEASURED, UNMEASURED}(绑定了情报员 transcript 时应 MEASURED)。
- [ ] **Step 1(核前提)**:`sed -n 2348,2400p autoresearch/trace/capsule.py`(D-5 子步的 kind 依赖);`grep -n "LOCAL_TOOL_NAMES" -A20 autoresearch/trace/transcripts/base.py`。
- [ ] **Step 2–5**:四修各走 失败测试→实现→绿(测试名 `test_collect_run_reads_contract_session_ref` / `test_codex_web_search_becomes_tool_items` / `test_tool_results_spill_archived` / `test_stock_full_web_budget_materialized`)。
- [ ] **Step 6**:全量 `tests/trace` 绿;Commit `fix(trace): usage 走 contract.session_ref·Codex 网查入账·tool-results 收割·stock_full D-5(D6.4)`。

### Task 15: 完整性 L0/L1/L2 分级 + 逃逸口封堵

**Files:**
- Modify: `autoresearch/trace/completeness.py`、`autoresearch/analyze/run_profile.py`、`autoresearch/trace/identity.py`、`autoresearch/analyze/runctl.py`
- Test: `tests/trace/test_completeness.py`(增)

**Interfaces:**
- Produces: `ArtifactRule` 增可选字段 `evidence_level: str = "L0"`(L0=存在性/L1=摘要视图/L2=原文)——**只加字段不改判定**;`completeness.evaluate` 输出里新增 `levels` 汇总:`{"L0": {"required": n, "hit": m}, …}`(按规则的 level 分桶计数,报告用「X% at L2」);scan profile 规则全默认 L0(行为零变)。analyze profile 给 `external_tools` 相关规则标 L1(harness 视图)——为 P2 的 fetch 工具(L2)留位。
- Produces: 逃逸口:runctl 若 `engine=="codex"`,begin 时把 `~/.codex/config.toml` 里 `web_search` 配置值探测出来写进 `identity/environment.json` 附加键 `codex_web_search_mode`(探测失败写 "UNKNOWN";**只读不改用户 config**);runctl 文档字符串明示「codex exec 驱动 analyze 时禁 `--ephemeral`」+ begin 检查 `CODEX_*` 在场而 rollout 目录当日无新文件时打 warn(尽力而为,B 级)。
- [ ] **Step 1(核前提)**:`grep -n "class ArtifactRule" autoresearch/contracts/profiles.py`(T1/T12 后的落点);`sed -n 247,316p autoresearch/trace/completeness.py`。
- [ ] **Step 2(失败测试)**:`test_completeness_levels_bucketed`——合成 expected(两条 L0 一条 L1,命中 2/3)→ evaluate 输出 `levels["L1"]["hit"] == 0`;`test_scan_rules_default_l0`——scan_profile 全部规则 level=="L0"(行为守恒探针)。
- [ ] **Step 3–5**:实现→绿→全量 `tests/trace` 绿。
- [ ] **Step 6**:Commit `feat(trace): 完整性 L0/L1/L2 分级(默认 L0 行为守恒)+ codex 逃逸口记录(D6.5)`。

### Task 16: Claude 网查留痕 hook(PostToolUse 兜底,L1)

**Files:**
- Create: `scripts/hooks/webtrace_posttool.py`
- Modify: `.claude/settings.json`(**人批**;若无此文件则创建,只含 hooks 键)
- Test: `tests/analyze/test_webtrace_hook.py`(新;直接调脚本 main 喂 stdin)

**Interfaces:**
- Produces: hook 脚本——stdin 收 Claude PostToolUse JSON(`tool_name ∈ {WebFetch, WebSearch}`、`tool_input`、`tool_response`、`session_id`);`AUTORESEARCH_RUN_ID` 环境不在场 → 立即 exit 0(零成本);在场 → append 一行 JSON 到 `<run_root>/capsule/lineage/external_tools_hook.jsonl`(**独立文件**,不写 capsule 主账 `external_tools.jsonl`——那是 finalize 物化的,双写会破幂等;finalize 已冻结时 hook 落到 run 外 `$RPT/analyze/_ledger/webtrace/<run_id>.jsonl`,append-only)。行 schema:`{ts, session_id, tool, url_or_query, response_chars, capture_level: "HOOK_L1"}`——**不存 response 正文**(hook 拿到的也只是摘要视图;正文的 L2 主路是 P2 的 fetch 工具)。
- settings diff(人批):

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "WebFetch|WebSearch",
        "hooks": [{ "type": "command",
                    "command": "uv run --no-sync python scripts/hooks/webtrace_posttool.py" }]
      }
    ]
  }
}
```

- [ ] **Step 1(核前提)**:`ls .claude/settings.json 2>/dev/null; cat .claude/settings.json 2>/dev/null` 看现状(有既有 hooks 则合并,勿覆盖别人的键)。
- [ ] **Step 2(失败测试)**:`test_hook_noop_without_run_id`(无 env → exit 0 且零写盘)/`test_hook_appends_line`(monkeypatch env+tmp 根 → 喂一段 WebFetch JSON → 文件多一行且字段齐)。
- [ ] **Step 3–5**:实现(~60 行,含 run 目录探测:`run_root("stock-research")` 与 `run_root("scan-market")` 都试,capsule 目录存在且未冻结才写 run 内)→绿。
- [ ] **Step 6(§LIVE)**:本会话手动触发一次 WebFetch(任意 URL),核 `_ledger/webtrace/` 或 run 内文件出现一行。
- [ ] **Step 7**:Commit `feat(hooks): WebFetch/WebSearch PostToolUse 留痕兜底(L1,D7.2)`。

**── P1 收尾门(整波验收)**:
1. 全量 pytest 绿(基线 4693+新增,0 失败);`tests/contracts/test_layering.py` 的 KNOWN_UPWARD **净减 ≥1**(run_contract 搬家)。
2. §LIVE 端到端一遍:`runctl begin`(FULL)→ harvest(走湖)→ 手写最小 11 段(可复制 08-30 真稿)→ assemble → `runctl finalize` → `capsule verify` 三结论 + `web_budget.json` 在 + manifest.run_id 非空 + `ledger ingest` 收到行。
3. 把实测数字(lineage 行数/context 瘦身字节/降级账条数)回填设计稿「实施记录」小节;更新记忆(`stock-research-optimization-brainstorm-20260831.md` 追加实施状态行)。
4. **不 push;不动 scan 判断层任何文件的语义**(`git diff --stat` 里出现 `scan/l4/prompts.py`/`l3/prompt.py`/agent def = 越界,回滚)。

---

## Self-Review 记录(计划作者自查)

- **Spec 覆盖**:D1→T3/T4/T5/T6/T9/T10/T11;D5.1→T8;D6.1→T1/T2;D6.2→T12;D6.3→T13;D6.4→T13/T14;D6.5→T15;D7.2→T16;D8.3→T7;D8.5→T8。**不在本计划**(声明性排除):D2(新源)、D3(exec_check)、D4(卡 v5)、D5.2-4(校准/翻转探针)、D7.1(fetch 工具 L2)、D8.1/2/4(card.json/收口/模板单源)、D9(Codex 叶子)——全部 P2/P3,等本波落地后按 spec §6 另出计划。
- **占位扫描**:T8 Step 2 的 `...` 两处已括注「写全,不引生产夹具」;T12 Step 2 第三用例 `...` 已括注合成路径——执行者须落全文,不许照抄省略号。其余无 TBD。
- **类型一致**:`run_root(kind, run_id)`(T12)与 T13/T16 引用一致;`analyze_profile(mode=…)`(T1)与 T12 注册表引用一致;`record_stage` 只在 T13 定义并被 T13 自用;`EXEC_LINE_MAX_*`(T7)命名全计划唯一。
- **已知风险**:①T9 范围内某端点无 fetcher → 按任务内规则剔除并记名,不现场造;②T12 触碰 trace 核心,scan 全量测试是安全网,红一条即停;③行号漂移(工作树未提交改动在场)→ 每任务 Step 1 的 premise-check 是硬门。
