# Wave 2:档案地基(dossier foundation)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 spec `docs/specs/2026-07-22-research-depth-dossier-design.md` 的 Wave 2——常备覆盖池 + 个股档案(八节)+ 首覆 workflow,先对 4 只持仓建档;并入 Wave-1 遗留小件。

**Architecture:** 新包 `autoresearch/dossier/`(schema/lint、pool、mainbz、prefetch、builder 全确定性纯函数);LLM 只在 `.claude/workflows/dossier-init.js` 的一个首覆 agent(`.claude/agents/dossier-init.md`)。既有 `autoresearch/scan/dossier.py`(前科卡)**保留不动**,其 `stock_dossier()` 被 builder 复用为判例账本种子。档案落 `context/knowledge/dossiers/<code>.md`(gitignored)。全部 presence-gated;池空/无档案 = 现行为不变。

**Tech Stack:** Python 3.13 + pandas + pytest(现套件 1356 绿);tushare `fina_mainbz`(已探通:type=P 含 bz_item/bz_sales/bz_profit);同花顺 keyless fwd-EPS(`autoresearch/data/keyless.py` 已有);workflow JS。

## Global Constraints

- 一切 python 命令 `uv run --no-sync`;commit 尾行 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。
- **presence-gated**:池文件缺=首次创建;档案缺=下游走现行为;任何取数失败=降级留痕(`[数据缺,YYYY-MM-DD]`),**绝不抛异常阻断 prelude/prewarm/assemble**。
- 池规则(spec 已拍板):pinned/持仓即入;近 20 交易日**真选**(finalists lane≠pinned)计数 ≥2 自动入;连续 20 交易日未再入选且非 pinned → `retired`(档案保留);池帽 30 硬截(FIFO by last_selected)。
- 摘要注入帽:`## 摘要(注入用)` 节 ≤**3000 token**(估算口径=UTF-8 字节 ÷2.8,与 repo token 表同口径);六项结构锚(业务/驱动/带位/风险/催化/判例)缺一 lint warn。
- 首覆 LLM 段只在 Claude session 内跑(workflow);launchd/prewarm 只做确定性预取。
- 档案八节标题 = schema 常量,机器锚;`.claude/agents/dossier-init.md` 契约锚进 `tests/test_agent_defs.py`。
- 三情景是**方向框架**(驱动假设+可观察信号),不是 EPS 点估(spec 非目标)。

---

### Task 1: Wave-1 尾件清理(指数黑名单 + M-1/M-2/M-3)

**Files:**
- Modify: `autoresearch/scan/price_claims.py`(指数黑名单)
- Modify: `autoresearch/learning/self_review.py`(M-1:probe 8 docstring 措辞)
- Modify: `autoresearch/scan/agents/l4_card.py`(M-2:dispatch_plan 兜底分支补 `"pinned": False`)
- Modify: `autoresearch/scan/assemble.py`(M-3:`_ensemble_flag` docstring 改现)
- Modify: `.claude/skills/scan-market/SKILL.md`(M-3:双复核段补一句 degraded 语义)
- Test: `tests/scan/test_price_claims.py`(追加)、`tests/scan/test_l4_dispatch_pinned.py`(追加)

**Interfaces:**
- Produces: `price_claims._INDEX_NAMES`(元组常量);行为=句内 % 的**左邻 12 字**内出现指数名 → 该 % 弃(不认领给本票)。

- [ ] **Step 1: 追加失败测试(指数黑名单,含 07-21 真卡长句)**

```python
# tests/scan/test_price_claims.py 追加
def test_extract_skips_index_pct_in_long_sentence():
    # 07-21 协创真卡长句(、/,连接一个句号):句尾「个股」不得给句首科创50 的 +10% 背书
    text = ("实读确认了成长侧:7-19 中报预告 +247%~+340% 已落地、7-13 定增 80 亿过股东会、"
            "7-21 工信部算力标准催化 + 科创50 单日 +10% 半导体涨停潮,个股放量 +11.4%(量比 1.9)"
            "——催化与档案里「无带日期催化/深跌落刀」两条证伪点均已翻转。")
    claims = extract_price_claims(text, name=NAME, code6=CODE, year_hint=2026)
    assert [c["value"] for c in claims if c["kind"] == "pct"] == [11.4]  # 只留个股 +11.4,指数 +10 弃


def test_extract_skips_index_pct_variants():
    for idx in ("沪深300", "上证指数", "创业板指", "科创50", "北证50"):
        t = f"本股 7-21 随{idx} 上涨 3.2%。"
        assert extract_price_claims(t, name=NAME, code6=CODE, year_hint=2026) == [], idx
```

- [ ] **Step 2: 跑测确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_price_claims.py -q`
Expected: 2 new FAIL(长句测抽出 [10.0, 11.4] 或指数句被认领)

- [ ] **Step 3: 实现指数黑名单**

`price_claims.py` 加常量与判定(接进现有"% 候选逐个判定"的排除链——Wave1 修复后抽取器按 % 匹配点逐候选判定,把本判定加为一条排除规则,与情景/基本面排除同层):

```python
_INDEX_NAMES = ("科创50", "沪深300", "上证指数", "上证综指", "深证成指", "深成指",
                "创业板指", "北证50", "中证500", "中证1000", "恒生", "纳指", "标普")


def _near_index_name(sent: str, pct_pos: int, window: int = 12) -> bool:
    """% 候选左邻 window 字内出现指数名 → 该 % 属指数,不认领给本票。"""
    left = sent[max(0, pct_pos - window):pct_pos]
    return any(ix in left for ix in _INDEX_NAMES)
```

在 % 候选排除链(与 `_SCENARIO`/`_FUND` 判定同处)加 `if _near_index_name(sent, <该候选的 pct_pos>): continue`。窗口基准=候选数字起始位(Wave1 修复引入的 `_num_pos` 口径)。

- [ ] **Step 4: M-1/M-2/M-3 三处小修**

(a) M-2 —— `l4_card.py` `dispatch_plan` 的兜底分支(prompt 与 details 皆缺归 dispatch 的异常路,函数尾部)meta 赋值补键,与正常分支同形:

```python
            meta[code6] = {"name": _cell(r, "name"), "sector": _cell(r, "sector"),
                           "pinned": _cell(r, "lane").strip() == "pinned"}
```

对应测试追加:

```python
# tests/scan/test_l4_dispatch_pinned.py 追加
def test_dispatch_meta_fallback_branch_keeps_pinned_shape(tmp_path):
    sd = tmp_path / "2026-07-21"
    sd.mkdir(parents=True)
    # 兜底路:既无 _l4_prompt 也无 details/<code>.md
    (sd / "finalists.csv").write_text("code,name,sector,lane\n600350,山东高速,铁路公路,pinned\n",
                                      encoding="utf-8")
    plan = dispatch_plan("2026-07-21", root=tmp_path)
    assert plan["meta"]["600350"]["pinned"] is True
```

(b) M-1 —— `self_review.py` probe 8 的注释块里"pr_20260714_006 型:intel 捏造涨停"改为"pr_20260714_006 同族(本探针读 staging 卡,不含 intel 附录;intel 侧断言由 assemble 发布层对账兜底)"。

(c) M-3 —— `assemble.py` `_ensemble_flag` docstring 里"退化时 workflow 端中位已取偏空侧"改为"degraded(复核 run 不齐)时 workflow 与本侧均不折回,仅 ens_flag 强制人裁展示";SKILL.md 步骤 4 双复核括注句尾补"(degraded=复核 run 不齐时不折回、报告强制人裁)"——插在 `(**cfg = 步骤 0.5 …事故注;**pinned = …不触发)` 括注之后同段。

- [ ] **Step 5: 跑测 + 回归 + Commit**

Run: `uv run --no-sync python -m pytest tests/scan/test_price_claims.py tests/scan/test_l4_dispatch_pinned.py -q` → 全 passed(price_claims 23 = 21+2)
Run: `uv run --no-sync python -m pytest tests/ -q` → 1359 级全绿

```bash
git add autoresearch/scan/price_claims.py autoresearch/learning/self_review.py autoresearch/scan/agents/l4_card.py autoresearch/scan/assemble.py .claude/skills/scan-market/SKILL.md tests/scan/test_price_claims.py tests/scan/test_l4_dispatch_pinned.py
git commit -m "fix(scan): Wave1 尾件——指数名黑名单(round3 立项:句级自指不再给指数%背书)+ M-1/2/3 小修

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: fina_mainbz 端点接入(B 级)

**Files:**
- Modify: `autoresearch/data/endpoints.py`(policy 注册表加 `fina_mainbz` 条目——先读该文件注册表形状,镜像 `forecast` 条目的字段;source=tushare、key 需含 `ts_code`+`period`+`type`、settle 与 `forecast` 同类)
- Modify: `autoresearch/data/contracts.py:97+`(CONTRACTS 加 B 级条目)
- Create: `autoresearch/dossier/__init__.py`(空)
- Create: `autoresearch/dossier/mainbz.py`
- Test: `tests/dossier/test_mainbz.py`(新目录,加空 `tests/dossier/__init__.py` 若 repo 惯例需要——先看 tests/ 下现有子目录有无 `__init__.py`,照抄惯例)

**Interfaces:**
- Produces: `mainbz_latest(code6: str, today: str, *, periods: int = 2, fetch=None) -> list[dict]` —— 近 `periods` 期分产品(type=P)拆分,每条 `{"period","bz_item","bz_sales","bz_profit"}`,按 period 降序;取数/权限失败 → `[]`(留痕交上层)。`fetch` 可注入(签名同 `sources.fetch`)。

- [ ] **Step 1: 写失败测试**

```python
# tests/dossier/test_mainbz.py
import pandas as pd

from autoresearch.dossier.mainbz import mainbz_latest


def _fake_fetch(endpoint, params):
    assert endpoint == "fina_mainbz" and params["type"] == "P"
    if params["period"] == "20251231":
        return pd.DataFrame([
            {"ts_code": "300857.SZ", "end_date": "20251231", "bz_item": "数据存储设备",
             "bz_sales": 4.49e9, "bz_profit": 8.0e8},
            {"ts_code": "300857.SZ", "end_date": "20251231", "bz_item": "智能算力产品及服务",
             "bz_sales": 2.76e9, "bz_profit": 6.1e8},
        ])
    return pd.DataFrame()          # 更早期无数据


def test_mainbz_latest_two_periods_desc():
    rows = mainbz_latest("300857", "2026-07-23", fetch=_fake_fetch)
    assert rows and rows[0]["period"] == "20251231"
    assert {r["bz_item"] for r in rows} == {"数据存储设备", "智能算力产品及服务"}


def test_mainbz_latest_fetch_crash_returns_empty():
    def boom(endpoint, params):
        raise RuntimeError("权限不足 40203")
    assert mainbz_latest("300857", "2026-07-23", fetch=boom) == []


def test_policy_and_contract_registered():
    from autoresearch.data.contracts import CONTRACTS
    from autoresearch.data.endpoints import policy
    assert policy("fina_mainbz")["source"] == "tushare"
    assert "fina_mainbz" in CONTRACTS
```

- [ ] **Step 2: 跑测确认失败**

Run: `uv run --no-sync python -m pytest tests/dossier/ -q`
Expected: FAIL(`ModuleNotFoundError: autoresearch.dossier`)

- [ ] **Step 3: 实现**

`contracts.py` B 级块加(照 `forecast` 行形状):

```python
    "fina_mainbz": _c(TIER_DEGRADE, note="分业务收入/利润(dossier 业务模型;小票/金融股披露口径可缺)",
                      empty_ok=True),
```

`endpoints.py`:读注册表,镜像 `forecast` 条目新增 `fina_mainbz`(source=tushare;key 字段含 ts_code/period/type;settle 同 forecast——具体字段名以该文件现有条目为准,不发明新字段)。

`autoresearch/dossier/mainbz.py`:

```python
"""fina_mainbz 分业务拆分(dossier 业务模型节的数据腿;确定性,B 级降级)。"""
from __future__ import annotations

import contextlib
from datetime import datetime


def _recent_periods(today: str, n: int) -> list[str]:
    """最近 n 个报告期(年报/中报:0630/1231),按新→旧。today=YYYY-MM-DD。"""
    y, m = int(today[:4]), int(today[5:7])
    ends: list[str] = []
    cur_y, cur_half = (y, 1) if m >= 7 else (y - 1, 2)   # 7 月起上一个可披露期=当年中报,否则去年年报
    for _ in range(n + 2):                                # 多备两期,容忍未披露
        ends.append(f"{cur_y}1231" if cur_half == 2 else f"{cur_y}0630")
        cur_y, cur_half = (cur_y - 1, 2) if cur_half == 1 else (cur_y, 1)
    return ends


def mainbz_latest(code6: str, today: str, *, periods: int = 2, fetch=None) -> list[dict]:
    if fetch is None:
        from autoresearch.data.sources import fetch as fetch
    ts_code = f"{code6}.SH" if code6.startswith(("6", "9")) else (
        f"{code6}.BJ" if code6.startswith(("4", "8")) else f"{code6}.SZ")
    out: list[dict] = []
    got = 0
    for period in _recent_periods(today, periods):
        if got >= periods:
            break
        with contextlib.suppress(Exception):
            df = fetch("fina_mainbz", {"ts_code": ts_code, "period": period, "type": "P"})
            if df is None or not len(df):
                continue
            for _, r in df.iterrows():
                out.append({"period": period, "bz_item": str(r.get("bz_item", "")),
                            "bz_sales": float(r.get("bz_sales") or 0.0),
                            "bz_profit": (None if r.get("bz_profit") is None else
                                          float(r.get("bz_profit") or 0.0))})
            got += 1
    return out
```

datetime import 若未用则删(以 ruff 为准)。

- [ ] **Step 4: 跑测确认通过 + Commit**

Run: `uv run --no-sync python -m pytest tests/dossier/ -q` → 3 passed

```bash
git add autoresearch/data/endpoints.py autoresearch/data/contracts.py autoresearch/dossier/ tests/dossier/
git commit -m "feat(dossier): fina_mainbz 端点接入(B级降级)+ mainbz_latest 近两期分业务

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: dossier schema + 摘要 lint

**Files:**
- Create: `autoresearch/dossier/schema.py`
- Test: `tests/dossier/test_schema.py`

**Interfaces:**
- Produces(全 wave 消费,逐字):
  - `DOSSIER_DIR = Path("context/knowledge/dossiers")`;`dossier_path(code6) -> Path`
  - `SECTIONS: tuple[str, ...]` = 八节标题锚(逐字):`("## 1. 业务模型", "## 2. 盈利驱动与预测留档", "## 3. 估值带", "## 4. 筹码与资金结构史", "## 5. 风险矩阵", "## 6. 催化剂日历", "## 7. 判例账本", "## 8. 变化项日志")`;`SUMMARY_HEAD = "## 摘要(注入用)"`
  - `SUMMARY_ANCHORS: tuple[str, ...]` = `("业务:", "驱动:", "带位:", "风险:", "催化:", "判例:")`
  - `est_tokens(text: str) -> int`(UTF-8 字节 ÷2.8)
  - `parse_frontmatter(text) -> dict`(YAML 头;坏/缺 → `{}`)
  - `render_frontmatter(meta: dict) -> str`
  - `lint_dossier(text: str, cap: int = 3000) -> list[str]`(问题清单;空=合格):缺节锚逐条报、`SUMMARY_HEAD` 缺报、摘要段(从 SUMMARY_HEAD 到下一个 `## `)超 cap 报 `summary>cap`、摘要内 `SUMMARY_ANCHORS` 缺一报一

- [ ] **Step 1: 写失败测试**

```python
# tests/dossier/test_schema.py
from autoresearch.dossier.schema import (
    SECTIONS, SUMMARY_ANCHORS, SUMMARY_HEAD, est_tokens, lint_dossier,
    parse_frontmatter, render_frontmatter,
)

META = {"code": "300857", "name": "协创数据", "sector": "消费电子", "pool_status": "active",
        "entered": "2026-07-23", "entry_reason": "pinned", "initiated": None,
        "last_refresh": None, "last_delta": None}


def _ok_doc() -> str:
    summary = SUMMARY_HEAD + "\n" + "\n".join(f"- {a} x" for a in SUMMARY_ANCHORS) + "\n"
    body = "\n".join(f"{s}\n(内容)\n" for s in SECTIONS)
    return render_frontmatter(META) + "\n" + summary + "\n" + body


def test_frontmatter_roundtrip():
    doc = _ok_doc()
    meta = parse_frontmatter(doc)
    assert meta["code"] == "300857" and meta["entry_reason"] == "pinned"


def test_parse_frontmatter_garbage_empty():
    assert parse_frontmatter("no frontmatter here") == {}


def test_lint_ok_doc_clean():
    assert lint_dossier(_ok_doc()) == []


def test_lint_reports_missing_section_and_anchor():
    doc = _ok_doc().replace("## 5. 风险矩阵", "## 5. 风险").replace("- 判例: x\n", "")
    issues = lint_dossier(doc)
    assert any("风险矩阵" in i for i in issues) and any("判例" in i for i in issues)


def test_lint_summary_over_cap():
    doc = _ok_doc().replace("- 判例: x", "- 判例: " + "长" * 5000)
    assert any("summary>cap" in i for i in lint_dossier(doc))


def test_est_tokens_cjk():
    assert est_tokens("字" * 28) == 30      # 28字×3B=84B ÷2.8 = 30
```

- [ ] **Step 2: 跑测确认失败**

Run: `uv run --no-sync python -m pytest tests/dossier/test_schema.py -q` → FAIL(模块缺)

- [ ] **Step 3: 实现 schema.py**

```python
"""dossier 档案格式契约(八节锚+frontmatter+摘要 lint;确定性,零 LLM)。

spec: docs/specs/2026-07-22-research-depth-dossier-design.md ①。八节标题与摘要锚是
机器契约:builder 写、lint 校、L4 注入器(Wave 3)按锚裁剪——改动须同步三方。
"""
from __future__ import annotations

from pathlib import Path

DOSSIER_DIR = Path("context/knowledge/dossiers")

SECTIONS: tuple[str, ...] = (
    "## 1. 业务模型", "## 2. 盈利驱动与预测留档", "## 3. 估值带",
    "## 4. 筹码与资金结构史", "## 5. 风险矩阵", "## 6. 催化剂日历",
    "## 7. 判例账本", "## 8. 变化项日志",
)
SUMMARY_HEAD = "## 摘要(注入用)"
SUMMARY_ANCHORS: tuple[str, ...] = ("业务:", "驱动:", "带位:", "风险:", "催化:", "判例:")

_META_KEYS = ("code", "name", "sector", "pool_status", "entered", "entry_reason",
              "initiated", "last_refresh", "last_delta")


def dossier_path(code6: str) -> Path:
    return DOSSIER_DIR / f"{str(code6).zfill(6)}.md"


def est_tokens(text: str) -> int:
    return int(len(text.encode("utf-8")) / 2.8)


def render_frontmatter(meta: dict) -> str:
    lines = ["---"]
    for k in _META_KEYS:
        v = meta.get(k)
        lines.append(f"{k}: {'null' if v is None else v}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def parse_frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    out: dict = {}
    for ln in text[3:end].strip().splitlines():
        if ":" not in ln:
            continue
        k, v = ln.split(":", 1)
        v = v.strip()
        out[k.strip()] = None if v in ("null", "") else v
    return out


def _summary_block(text: str) -> str:
    i = text.find(SUMMARY_HEAD)
    if i < 0:
        return ""
    j = text.find("\n## ", i + len(SUMMARY_HEAD))
    return text[i:j] if j > 0 else text[i:]


def lint_dossier(text: str, cap: int = 3000) -> list[str]:
    issues = [f"缺节锚:{s}" for s in SECTIONS if s not in text]
    if SUMMARY_HEAD not in text:
        issues.append(f"缺节锚:{SUMMARY_HEAD}")
        return issues
    block = _summary_block(text)
    if est_tokens(block) > cap:
        issues.append(f"summary>cap({est_tokens(block)}>{cap})")
    issues += [f"摘要缺锚:{a}" for a in SUMMARY_ANCHORS if a not in block]
    return issues
```

- [ ] **Step 4: 跑测确认通过 + Commit**

Run: `uv run --no-sync python -m pytest tests/dossier/test_schema.py -q` → 6 passed

```bash
git add autoresearch/dossier/schema.py tests/dossier/test_schema.py
git commit -m "feat(dossier): schema——八节/摘要机器锚+frontmatter+lint(≤3k token 帽)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: 覆盖池 pool.py + CLI

**Files:**
- Create: `autoresearch/dossier/pool.py`
- Test: `tests/dossier/test_pool.py`

**Interfaces:**
- Produces:
  - `POOL_PATH = Path("context/knowledge/coverage_pool.json")`
  - `load_pool(path=None) -> dict`(缺/坏 → `{"stocks": {}, "cap": 30}`)
  - `refresh(today: str, *, scan_root="context/scan", pool_path=None, pinned_path=None) -> dict` —— 执行进出规则并落盘,返回 `{"entered": [...], "retired": [...], "revived": [...], "pending_init": [...], "n_active": int}`
  - `pending_init(pool: dict) -> list[str]`(active 且 `schema.dossier_path(code)` 不存在)
  - CLI:`python -m autoresearch.dossier.pool <today> [--add CODE --note X] [--remove CODE] [--status]`
- Consumes:`user_config.load_pinned(today)`(kept 条目 → 即入);`schema.dossier_path`

**池条目形状**(JSON):`{"stocks": {"300857": {"name": "", "status": "active|retired", "entered": "YYYY-MM-DD", "entry_reason": "pinned|finalist_2x|manual", "last_selected": "YYYY-MM-DD|null", "note": ""}}, "cap": 30, "as_of": "YYYY-MM-DD"}`

**规则精确化**(实现契约):
- 扫描目录集 = `scan_root` 下 `20*` 目录名降序取**最近 20 个**含 `finalists.csv` 的日期(=近 20 交易日近似,与 repo 其它处同口径)。
- 真选计数:该 20 日窗内 `finalists.csv` 行 `code` zfill 后等于该码且 `lane` strip 后 ∉ {"pinned"} 的**日数**;≥2 → 入池(`finalist_2x`),`last_selected`=最近命中日。
- pinned kept(load_pinned)→ 无条件 active(缺则入,`entry_reason=pinned`);active 票每次 refresh 更新 `last_selected`(若窗内有真选或 pinned 注入行)。
- retire:status=active 且非 pinned kept 且 `last_selected` 不在 20 日窗内(或为 null 且 entered 早于窗首日)→ `retired`;retired 票重新满足进条件 → `revived`(status 回 active)。
- cap:active 数 > 30 → 按 `last_selected` 升序(最久未选先退)把超额置 retired(pinned kept 永不被 cap 退)。

- [ ] **Step 1: 写失败测试**

```python
# tests/dossier/test_pool.py
import json
from pathlib import Path

from autoresearch.dossier import pool


def _mk_scan(root: Path, dates_with: dict[str, list[tuple[str, str]]]):
    """dates_with: {date: [(code, lane), ...]} → 造 finalists.csv。"""
    for d, rows in dates_with.items():
        sd = root / d
        sd.mkdir(parents=True, exist_ok=True)
        body = "code,name,sector,lane\n" + "\n".join(
            f"{c},N{c},X,{lane}" for c, lane in rows)
        (sd / "finalists.csv").write_text(body, encoding="utf-8")


def _pinned_file(p: Path, codes: list[str]):
    p.write_text(json.dumps([{"code": c} for c in codes]), encoding="utf-8")
    return p


def test_pinned_and_finalist2x_enter(tmp_path):
    scan = tmp_path / "scan"
    _mk_scan(scan, {"2026-07-21": [("002926", "healthy")],
                    "2026-07-22": [("002926", "momentum"), ("300857", "pinned")]})
    pp = _pinned_file(tmp_path / "pinned.jsonc", ["300857"])
    out = pool.refresh("2026-07-23", scan_root=scan, pool_path=tmp_path / "pool.json",
                       pinned_path=pp)
    assert set(out["entered"]) == {"002926", "300857"}   # 002926 真选×2;300857 pinned 即入
    saved = json.loads((tmp_path / "pool.json").read_text())
    assert saved["stocks"]["002926"]["entry_reason"] == "finalist_2x"
    assert saved["stocks"]["300857"]["entry_reason"] == "pinned"


def test_single_selection_not_enough(tmp_path):
    scan = tmp_path / "scan"
    _mk_scan(scan, {"2026-07-22": [("600350", "healthy")]})
    out = pool.refresh("2026-07-23", scan_root=scan, pool_path=tmp_path / "pool.json",
                       pinned_path=_pinned_file(tmp_path / "p.jsonc", []))
    assert out["entered"] == [] and out["n_active"] == 0


def test_retire_after_window(tmp_path):
    scan = tmp_path / "scan"
    # 21 个交易日:码 600188 只在最早一天真选过 → 已滑出 20 日窗 → retire
    dates = {f"2026-06-{d:02d}": [("999999", "healthy")] for d in range(1, 22)}
    dates["2026-05-30"] = [("600188", "healthy"), ("600188x", "x")]
    _mk_scan(scan, dates)
    pp = _pinned_file(tmp_path / "p.jsonc", [])
    pool_path = tmp_path / "pool.json"
    pool_path.write_text(json.dumps({"cap": 30, "stocks": {"600188": {
        "name": "兖矿", "status": "active", "entered": "2026-05-30",
        "entry_reason": "manual", "last_selected": "2026-05-30", "note": ""}}}),
        encoding="utf-8")
    out = pool.refresh("2026-06-30", scan_root=scan, pool_path=pool_path, pinned_path=pp)
    assert out["retired"] == ["600188"]


def test_pending_init_lists_active_without_dossier(tmp_path, monkeypatch):
    monkeypatch.setattr("autoresearch.dossier.schema.DOSSIER_DIR", tmp_path / "dossiers")
    p = {"cap": 30, "stocks": {"300857": {"status": "active"}, "601869": {"status": "retired"}}}
    assert pool.pending_init(p) == ["300857"]
```

- [ ] **Step 2: 跑测确认失败** → `uv run --no-sync python -m pytest tests/dossier/test_pool.py -q` FAIL

- [ ] **Step 3: 实现 pool.py**(按上方接口与规则精确化逐条实现;CLI `main(argv)`:无 flag=refresh+打印变动行,`--status` 打印 active/retired/pending_init 计数与清单,`--add/--remove` 手动路(add 记 entry_reason=manual);全函数 presence-gated,坏 json 当空池重建但**先备份** `coverage_pool.json.bak`)

实现骨架(核心 refresh,完整转写):

```python
"""常备覆盖池(spec ③;确定性,零 LLM)。进出规则见 plan Task 4 精确化。"""
from __future__ import annotations

import contextlib
import json
import shutil
from pathlib import Path

from autoresearch.dossier import schema

POOL_PATH = Path("context/knowledge/coverage_pool.json")


def load_pool(path: Path | None = None) -> dict:
    p = Path(path) if path else POOL_PATH
    with contextlib.suppress(Exception):
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, dict) and isinstance(d.get("stocks"), dict):
                d.setdefault("cap", 30)
                return d
            shutil.copy2(p, p.with_suffix(".json.bak"))   # 坏形状:备份后重建
    return {"stocks": {}, "cap": 30}


def _recent_scan_days(scan_root: Path, n: int = 20) -> list[str]:
    if not scan_root.exists():
        return []
    days = sorted((p.name for p in scan_root.iterdir()
                   if p.is_dir() and p.name[:2] == "20" and (p / "finalists.csv").exists()),
                  reverse=True)
    return days[:n]


def _selections(scan_root: Path, days: list[str]) -> dict[str, list[str]]:
    """code6 → 真选命中日列表(lane≠pinned)。"""
    import pandas as pd
    hits: dict[str, list[str]] = {}
    for d in days:
        with contextlib.suppress(Exception):
            df = pd.read_csv(scan_root / d / "finalists.csv", dtype={"code": str})
            if "code" not in df.columns:
                continue
            for _, r in df.iterrows():
                raw = str(r.get("code", "") or "").strip()
                if not raw or raw == "nan":
                    continue
                c = raw.split(".")[0].zfill(6)
                if str(r.get("lane", "") or "").strip() == "pinned":
                    hits.setdefault(c, hits.get(c, []))   # pinned 注入不计真选
                    continue
                hits.setdefault(c, []).append(d)
    return hits


def pending_init(pool: dict) -> list[str]:
    return sorted(c for c, s in pool.get("stocks", {}).items()
                  if s.get("status") == "active" and not schema.dossier_path(c).exists())


def refresh(today: str, *, scan_root: str | Path = "context/scan",
            pool_path: Path | None = None, pinned_path: Path | None = None) -> dict:
    scan_root = Path(scan_root)
    pool = load_pool(pool_path)
    stocks = pool["stocks"]
    days = _recent_scan_days(scan_root)
    window_first = days[-1] if days else None
    sel = _selections(scan_root, days)

    from autoresearch.scan.user_config import load_pinned
    kept: dict[str, str] = {}
    with contextlib.suppress(Exception):
        kw = {"path": pinned_path} if pinned_path else {}
        for e in load_pinned(today, **kw).get("kept", []):
            kept[str(e.get("code", "")).zfill(6)] = str(e.get("note", "") or "")

    entered, retired, revived = [], [], []

    def _touch(c: str, reason: str, note: str = ""):
        st = stocks.get(c)
        last = max(sel.get(c, []), default=None)
        if st is None:
            stocks[c] = {"name": "", "status": "active", "entered": today,
                         "entry_reason": reason, "last_selected": last, "note": note}
            entered.append(c)
        else:
            if st.get("status") == "retired":
                st["status"] = "active"
                revived.append(c)
            if last and (st.get("last_selected") or "") < last:
                st["last_selected"] = last

    for c, note in kept.items():                     # pinned 即入/保活
        _touch(c, "pinned", note)
    for c, ds in sel.items():                        # 真选 ≥2 入
        if len(ds) >= 2:
            _touch(c, "finalist_2x")
        elif c in stocks and stocks[c].get("status") == "active":
            _touch(c, stocks[c].get("entry_reason", "manual"))   # 已在池:单次也刷新 last_selected

    for c, st in stocks.items():                     # 退池
        if st.get("status") != "active" or c in kept:
            continue
        last = st.get("last_selected")
        out_of_window = (last is None and st.get("entered", "") < (window_first or today)) or \
                        (last is not None and window_first is not None and last < window_first)
        if out_of_window:
            st["status"] = "retired"
            retired.append(c)

    actives = [c for c, s in stocks.items() if s.get("status") == "active"]
    if len(actives) > pool.get("cap", 30):           # cap FIFO(pinned 永不被 cap 退)
        evictable = sorted((c for c in actives if c not in kept),
                           key=lambda c: stocks[c].get("last_selected") or "")
        for c in evictable[:len(actives) - pool["cap"]]:
            stocks[c]["status"] = "retired"
            retired.append(c)

    pool["as_of"] = today
    p = Path(pool_path) if pool_path else POOL_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(pool, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"entered": sorted(entered), "retired": sorted(retired), "revived": sorted(revived),
            "pending_init": pending_init(pool),
            "n_active": sum(1 for s in stocks.values() if s.get("status") == "active")}
```

加 `main(argv)`(argparse:today 位置参 + `--status/--add/--remove/--note`)与 `if __name__ == "__main__":`。

- [ ] **Step 4: 跑测 + Commit**

Run: `uv run --no-sync python -m pytest tests/dossier/ -q` → 全 passed

```bash
git add autoresearch/dossier/pool.py tests/dossier/test_pool.py
git commit -m "feat(dossier): 覆盖池 pool——pinned即入/20日真选≥2入/20日未选退/cap30 FIFO+CLI

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: prelude 集成(池日检步)

**Files:**
- Modify: `autoresearch/scan/prelude.py`(`run_prelude` 内加 `_dossier_pool` 步 + `all_steps` 末尾加 `("dossier_pool", _dossier_pool)`)
- Test: `tests/scan/test_prelude_pool.py`(新)

**Interfaces:**
- Consumes: `autoresearch.dossier.pool.refresh(date)`
- Produces: prelude 步返回字符串一行:`池 N active · 进X退Y复Z · 待建档 M 只(code,…)`(变动为 0 时短形 `池 N active · 无变动 · 待建档 M`)

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_prelude_pool.py
from autoresearch.scan import prelude


def test_prelude_has_dossier_pool_step(monkeypatch):
    calls = {}
    def fake_refresh(today, **kw):
        calls["today"] = today
        return {"entered": ["300857"], "retired": [], "revived": [],
                "pending_init": ["300857"], "n_active": 1}
    monkeypatch.setattr("autoresearch.dossier.pool.refresh", fake_refresh)
    # 只跑本步:skip 其余全部
    names = [n for n, _ in prelude_steps()]
    res = prelude.run_prelude("2026-07-23", skip=tuple(n for n in names if n != "dossier_pool"))
    row = next(r for r in res if r.get("step") == "dossier_pool")
    assert calls["today"] == "2026-07-23" and row["ok"] and "300857" in str(row.get("note", ""))


def prelude_steps():
    """从 run_prelude 源码取步名清单的轻替身:直接跑一次全 skip 不现实,
    此处按已知步名硬编码并在断言里兜底(新步缺席时 next() 会 StopIteration 失败)。"""
    return [(n, None) for n in ("retro_refresh", "retro_pending", "t1_pending", "learning_health",
                                "consensus", "temperature", "universe", "calendar", "catalyst",
                                "menu", "ledgers", "dossier_pool")]
```

(若 `run_prelude` 的返回行键名与 `{"step","ok","note"}` 不符,以 `_run_steps` 真实现为准调测试断言——先读该函数。)

- [ ] **Step 2: 跑测确认失败** → FAIL(无 dossier_pool 步)

- [ ] **Step 3: 实现**(prelude.py `run_prelude` 内,`_ledgers` 之后):

```python
    def _dossier_pool():
        from autoresearch.dossier import pool
        out = pool.refresh(date)
        delta = f"进{len(out['entered'])}退{len(out['retired'])}复{len(out['revived'])}"
        pend = out["pending_init"]
        pend_txt = f"待建档 {len(pend)} 只({','.join(pend[:6])})" if pend else "待建档 0"
        moved = out["entered"] or out["retired"] or out["revived"]
        return (f"池 {out['n_active']} active · {delta if moved else '无变动'} · {pend_txt}")
```

`all_steps` 末尾加 `("dossier_pool", _dossier_pool)`。

- [ ] **Step 4: 跑测 + 回归 + Commit**

Run: `uv run --no-sync python -m pytest tests/scan/test_prelude_pool.py tests/scan/ -q` → 全 passed

```bash
git add autoresearch/scan/prelude.py tests/scan/test_prelude_pool.py
git commit -m "feat(scan): prelude 集成覆盖池日检步(dossier_pool,presence-gated)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: 确定性预取 prefetch + prewarm 挂钩

**Files:**
- Create: `autoresearch/dossier/prefetch.py`
- Modify: `autoresearch/scan/prewarm.py`(`run_prewarm` 加 `_step("dossier_prefetch", ...)`)
- Test: `tests/dossier/test_prefetch.py`

**Interfaces:**
- Produces:
  - `PREFETCH_DIR = schema.DOSSIER_DIR / "_prefetch"`
  - `prefetch_one(code6: str, today: str, *, fetch=None, ths_fn=None, band_fn=None) -> dict` —— 写 `_prefetch/<code6>.json` 并返回 `{"code","asof","mainbz","fwd_eps","val_band"}`;每腿独立降级(失败该键为 `None`/`[]` + `notes` 列表留痕)
  - `prefetch_pool(today, *, codes=None) -> dict`(codes 缺省=池 active;返回 {code: ok_bool})
- Consumes: `mainbz_latest`(Task 2);`autoresearch/data/keyless.py` 的同花顺一致预期公开函数(先 grep 该文件公开 API——`fwd_eps(df, year)` 需要上游 df,找到模块里"取数+组装"的入口函数(约 :51-106 区间,如 ths consensus block 构造),以其真名调用;若入口耦合 markdown 输出,则取其内部取数函数,**不重写取数逻辑**);估值带 `band_fn` 缺省实现:`sources.fetch("daily_basic", {"ts_code": ..., "start_date": 3年前, "end_date": today, "fields 剥离"})`(单票历史轻拉,**不入湖**——lake key 是按日整市场,单票历史属建档一次性数据),算 pe_ttm/pb 的 P25/P50/P75 与现值分位

- [ ] **Step 1: 写失败测试**

```python
# tests/dossier/test_prefetch.py
import json

from autoresearch.dossier import prefetch, schema


def test_prefetch_one_all_legs(tmp_path, monkeypatch):
    monkeypatch.setattr(schema, "DOSSIER_DIR", tmp_path)
    monkeypatch.setattr(prefetch, "PREFETCH_DIR", tmp_path / "_prefetch")
    out = prefetch.prefetch_one(
        "300857", "2026-07-23",
        fetch=lambda e, p: (_ for _ in ()).throw(RuntimeError("no net")),  # mainbz 腿挂
        ths_fn=lambda code6, today: {"fwd_eps_2026": 5.0, "asof": today},
        band_fn=lambda code6, today: {"pe_p25": 30.0, "pe_p50": 45.0, "pe_p75": 70.0,
                                      "pe_now": 59.9, "pb_p50": 8.0})
    assert out["mainbz"] == [] and "mainbz" in " ".join(out["notes"])   # 降级留痕
    assert out["fwd_eps"]["fwd_eps_2026"] == 5.0
    assert out["val_band"]["pe_p50"] == 45.0
    saved = json.loads((tmp_path / "_prefetch" / "300857.json").read_text())
    assert saved["code"] == "300857" and saved["asof"] == "2026-07-23"


def test_prefetch_pool_uses_active(tmp_path, monkeypatch):
    monkeypatch.setattr(prefetch, "PREFETCH_DIR", tmp_path / "_prefetch")
    monkeypatch.setattr("autoresearch.dossier.pool.load_pool",
                        lambda path=None: {"stocks": {"600350": {"status": "active"},
                                                      "601869": {"status": "retired"}}})
    called = []
    monkeypatch.setattr(prefetch, "prefetch_one",
                        lambda c, t, **kw: called.append(c) or {"code": c})
    prefetch.prefetch_pool("2026-07-23")
    assert called == ["600350"]
```

- [ ] **Step 2: 跑测确认失败** → FAIL

- [ ] **Step 3: 实现 prefetch.py**(三腿各 `contextlib.suppress` + notes 留痕;`prefetch_one` 末尾 `PREFETCH_DIR.mkdir(parents=True, exist_ok=True)` 后原子写 json;`prefetch_pool` 逐码调用,单码失败不断链)+ prewarm 挂钩:

```python
        # prewarm.py run_prewarm 的 steps 序列尾部
        def _dossier_prefetch(d):
            from autoresearch.dossier.prefetch import prefetch_pool
            r = prefetch_pool(d)
            return f"池预取 {sum(1 for v in r.values() if v)}/{len(r)}"
        _step("dossier_prefetch", _dossier_prefetch)
```

- [ ] **Step 4: 跑测 + 回归 + Commit**

Run: `uv run --no-sync python -m pytest tests/dossier/ tests/scan/ -q` → 全 passed

```bash
git add autoresearch/dossier/prefetch.py autoresearch/scan/prewarm.py tests/dossier/test_prefetch.py
git commit -m "feat(dossier): 确定性预取(mainbz/fwd-EPS/估值带,三腿独立降级)+prewarm 挂钩

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: builder 骨架(确定性建档)

**Files:**
- Create: `autoresearch/dossier/builder.py`
- Test: `tests/dossier/test_builder.py`

**Interfaces:**
- Produces:
  - `build_skeleton(code6: str, today: str, *, name: str = "", sector: str = "", scan_root="context/scan", force: bool = False) -> dict` —— 返回 `{"path", "created": bool, "issues": [...]}`。档案已存在且非 force → `created=False` 原文不动。骨架内容:
    - frontmatter(schema `_META_KEYS`,`initiated=None`)
    - `## 摘要(注入用)`:六锚行,机算部分现值(带位/判例),叙事锚占位 `(待首覆)`
    - 八节:§1/§2/§5 放 `<!-- LLM:待首覆 -->` 锚+prefetch 表格素材(mainbz 表/fwd-EPS 行);§3 估值带确定性成表(prefetch val_band);§4 从当日 staging `seats.csv`/`pledge.csv` presence-gated 抄行;§6 从 `calendar.csv` presence-gated 抄该码行;§7 = `autoresearch.scan.dossier.render_dossier(code6)` 输出(前科机制复用,空则"(无入围史)");§8 空日志节 + 首行 `- <today> 建档`
  - `render_summary_calc(prefetch_data, precedent_n) -> dict[str, str]`(机算锚行:`带位:`/`判例:` 的实值)
  - CLI `python -m autoresearch.dossier.builder <code> <today> [--name --sector --force]`
- Consumes: schema 全部锚常量、`prefetch` 落盘 json(缺 → 素材处留 `[数据缺,<today>]`)、`scan.dossier.render_dossier`

- [ ] **Step 1: 写失败测试**

```python
# tests/dossier/test_builder.py
import json

from autoresearch.dossier import builder, schema


def _prefetch_file(tmp_path, code="300857"):
    d = tmp_path / "_prefetch"
    d.mkdir(parents=True)
    (d / f"{code}.json").write_text(json.dumps({
        "code": code, "asof": "2026-07-23",
        "mainbz": [{"period": "20251231", "bz_item": "数据存储设备",
                    "bz_sales": 4.49e9, "bz_profit": 8.0e8}],
        "fwd_eps": {"fwd_eps_2026": 5.0}, "val_band": {"pe_p25": 30, "pe_p50": 45,
                                                        "pe_p75": 70, "pe_now": 59.9},
        "notes": []}), encoding="utf-8")


def test_build_skeleton_full_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(schema, "DOSSIER_DIR", tmp_path)
    monkeypatch.setattr("autoresearch.dossier.prefetch.PREFETCH_DIR", tmp_path / "_prefetch")
    monkeypatch.setattr("autoresearch.scan.dossier.render_dossier", lambda c, **kw: "### 📁 个股档案(近 2 次入围)\n- x\n")
    _prefetch_file(tmp_path)
    out = builder.build_skeleton("300857", "2026-07-23", name="协创数据", sector="消费电子",
                                 scan_root=tmp_path / "noscan")
    assert out["created"] is True
    text = (tmp_path / "300857.md").read_text(encoding="utf-8")
    for s in schema.SECTIONS:
        assert s in text
    assert schema.SUMMARY_HEAD in text and "数据存储设备" in text and "<!-- LLM:待首覆 -->" in text
    assert "个股档案" in text                       # §7 前科种子
    # lint:骨架允许叙事锚"(待首覆)"占位 → 六锚字符串在场即可
    assert builder_lint_clean(text)


def builder_lint_clean(text):
    return schema.lint_dossier(text) == []


def test_build_skeleton_idempotent_no_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(schema, "DOSSIER_DIR", tmp_path)
    monkeypatch.setattr("autoresearch.dossier.prefetch.PREFETCH_DIR", tmp_path / "_prefetch")
    monkeypatch.setattr("autoresearch.scan.dossier.render_dossier", lambda c, **kw: "")
    _prefetch_file(tmp_path)
    builder.build_skeleton("300857", "2026-07-23", scan_root=tmp_path / "noscan")
    (tmp_path / "300857.md").write_text("人工改过", encoding="utf-8")
    out = builder.build_skeleton("300857", "2026-07-24", scan_root=tmp_path / "noscan")
    assert out["created"] is False
    assert (tmp_path / "300857.md").read_text(encoding="utf-8") == "人工改过"


def test_build_skeleton_missing_prefetch_leaves_trace(tmp_path, monkeypatch):
    monkeypatch.setattr(schema, "DOSSIER_DIR", tmp_path)
    monkeypatch.setattr("autoresearch.dossier.prefetch.PREFETCH_DIR", tmp_path / "_prefetch")
    monkeypatch.setattr("autoresearch.scan.dossier.render_dossier", lambda c, **kw: "")
    out = builder.build_skeleton("600350", "2026-07-23", scan_root=tmp_path / "noscan")
    text = (tmp_path / "600350.md").read_text(encoding="utf-8")
    assert out["created"] and "[数据缺,2026-07-23]" in text
```

- [ ] **Step 2: 跑测确认失败** → FAIL

- [ ] **Step 3: 实现 builder.py**(读 prefetch json → 逐节渲染字符串拼装;§3 表:`| 分位 | PE | ... | P25 45 …` + `当前 pe_now 落带位 <P50~P75>` 一句机算;摘要 `带位:` 行同源;§7 调 `render_dossier(code6, exclude=None)`;写盘前 `schema.lint_dossier` 自检,issues non-empty 时仍写盘但回传 issues(骨架期六叙事锚以"(待首覆)"占位形式在场,lint 应 clean);CLI 同前例)

- [ ] **Step 4: 跑测 + Commit**

Run: `uv run --no-sync python -m pytest tests/dossier/ -q` → 全 passed

```bash
git add autoresearch/dossier/builder.py tests/dossier/test_builder.py
git commit -m "feat(dossier): builder 确定性建档骨架(八节+摘要机算+前科种子+降级留痕,幂等不覆盖)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: 首覆 agent def + dossier-init workflow

**Files:**
- Create: `.claude/agents/dossier-init.md`
- Create: `.claude/workflows/dossier-init.js`
- Modify: `tests/test_agent_defs.py`(AGENTS 清单加 dossier-init + workflow 文本锚测试)
- Modify: `.claude/skills/stock-research/SKILL.md`(档位路由节加一行:首覆建档模式指针)
- Modify: `.claude/skills/stock-research/engine-playbook.md`(尾部加「首覆建档(dossier)扩展」小节 6-8 行:四 LLM 节职责 + 指向 agent def 为真值源)

**Interfaces:**
- Consumes: Task 7 `python -m autoresearch.dossier.builder <code> <today>`、Task 6 prefetch json、`context/<ticker>.<SS|SZ>_<date>_slim.md`(+`_slim_deep.md`)若在
- Produces: workflow args `{date, code, name, sector}`;返回 `{code, initiated: bool, issues: [...]}`;agent 只改档案的 `<!-- LLM:待首覆 -->` 节与摘要叙事锚,**确定性节数字不改**

- [ ] **Step 1: 锚测试(先失败)**

```python
# tests/test_agent_defs.py 追加(AGENTS 清单若为显式列表,把 "dossier-init" 加入同列表)
def test_dossier_init_workflow_anchors():
    js = (ROOT / ".claude" / "workflows" / "dossier-init.js").read_text(encoding="utf-8")
    for a in ("dossier-init", "builder", "lint", "LLM:待首覆"):
        assert a in js, f"dossier-init.js 缺锚「{a}」"


def test_dossier_init_agent_def():
    p = ROOT / ".claude" / "agents" / "dossier-init.md"
    text = p.read_text(encoding="utf-8")
    for a in ("model: opus", "三情景", "证伪触发点", "断言分级", "不改确定性节"):
        assert a in text, f"dossier-init.md 缺契约锚「{a}」"
```

Run: `uv run --no-sync python -m pytest tests/test_agent_defs.py -q` → 2 new FAIL

- [ ] **Step 2: 写 `.claude/agents/dossier-init.md`**(全文如下,frontmatter 照 repo 惯例)

```markdown
---
name: dossier-init
description: 常备覆盖档案首覆研究员(券商 initiation 单人版)。读确定性骨架+prefetch+slim/deep,填档案四个 LLM 节(业务模型叙事/盈利驱动三情景/风险矩阵/摘要叙事)。由 dossier-init workflow 派发,一票一 context。
model: opus
effort: max
tools: Read, Grep, Glob, Write, WebSearch, WebFetch
---

你是常备覆盖档案的首覆研究员:对一只 A 股建立**可增量维护的深度档案**(券商 standing coverage 的 initiation)。真值源 spec `docs/specs/2026-07-22-research-depth-dossier-design.md` ①②。

## 输入
派发 prompt 给你:代码/名称/行业/日期 + 档案骨架路径(`context/knowledge/dossiers/<code>.md`,确定性节已填)+ prefetch json 路径 + slim/deep 路径(可能缺)。先读骨架与 prefetch,再读 slim(有 deep 读 deep 的 forensics 块)。

## 你只写四处(铁律)
1. **§1 业务模型**的 `<!-- LLM:待首覆 -->` 处:基于骨架里的 mainbz 分业务表写收入驱动公式(量×价/订单/产能,逐业务一行)+ 产业链上下游映射(供应商/客户/竞品,能给代码给代码);表格数字**引用骨架现值,不改不编**。
2. **§2 盈利驱动**的 `<!-- LLM:待首覆 -->` 处:3~5 个关键驱动变量(各配可观察信号源);**三情景方向框架**(Bull/Base/Bear 各=驱动假设+触发信号+可证伪观察点,**禁 EPS 点估**);fwd-EPS 快照行引用骨架现值。
3. **§5 风险矩阵**的 `<!-- LLM:待首覆 -->` 处:CFO/NI 史、监管/审计前科、商誉/质押、大股东行为(数据出自 slim deep/骨架;缺=「[数据缺]」不编);**每条风险必须带证伪触发点**(什么数字/事件出现即该风险兑现或解除)。
4. **摘要(注入用)**:把 `业务:`/`驱动:`/`风险:`/`催化:` 四条叙事锚从「(待首覆)」改为各 ≤60 字实句;`带位:`/`判例:` 机算行**不动**。

## 铁律
- **不改确定性节**:§3/§4/§6/§7/§8 与所有既有数字一个字不动;frontmatter 只把 `initiated: null` 改为分析日。
- **断言分级**(同 l4-card 契约):网查事实须`「原文引句≤30字」+来源+日期`;推断明写「推断」;价格类断言只允许出自骨架/slim 已核数字。
- 网查有界:全档 ≤4 条 WebSearch(年报业务细节/产业链核实用),每条落源+日期,as-of≤分析日。
- 超短交易尺**不属于档案**:档案写结构与驱动,不写 1~2 日操作(那是 L4 卡的事)。
- 写完自检:`## 摘要(注入用)` 段估算 ≤3000 token(UTF-8 字节÷2.8);超了先压摘要。
- 最终回传只报:code / initiated / 摘要 token 估 / 你留下的最大不确定项一行。
```

- [ ] **Step 3: 写 `.claude/workflows/dossier-init.js`**

```js
export const meta = {
  name: 'dossier-init',
  description: '单票首覆建档:确定性骨架(builder)→ Opus 首覆 agent 填四 LLM 节 → lint 校验;池内 pending_init 逐票拉起(spec 2026-07-22 ②)',
  phases: [
    { title: 'Skeleton', detail: 'prefetch(若缺)+ builder 骨架(幂等)' },
    { title: 'Initiate', detail: 'dossier-init agent 填 LLM 节(不改确定性节)' },
    { title: 'Lint', detail: 'schema.lint_dossier 校验 + frontmatter initiated 核' },
  ],
}

// args: {date, code, name, sector}
const A = (typeof args === 'string' && args ? JSON.parse(args) : args) || {}
const { date, code } = A
if (!date || !code) throw new Error('args.date/args.code 必填')
const name = A.name || ''
const sector = A.sector || ''
const R = 'uv run --no-sync python -m'
const DP = `context/knowledge/dossiers/${code}.md`

function bash(cmd, label, ph) {
  return agent(
    `在仓库根目录精确执行下面这条命令,然后只回报:退出码 + stdout 末 10 行。不要做别的。\n\n\`\`\`\n${cmd}\n\`\`\``,
    { agentType: 'general-purpose', effort: 'low', label, phase: ph })
}

phase('Skeleton')
await bash(`${R} autoresearch.dossier.prefetch ${code} ${date} || true; ` +
           `${R} autoresearch.dossier.builder ${code} ${date} --name "${name}" --sector "${sector}"`,
           `skeleton:${code}`, 'Skeleton')

phase('Initiate')
const INIT = { type: 'object', required: ['code'],
  properties: { code: { type: 'string' }, initiated: { type: 'boolean' },
    summary_tokens: { type: 'number' }, uncertainty: { type: 'string' } } }
const r = await agent(
  `首覆建档:${code} ${name}(${sector})· 分析日 ${date}。骨架:${DP};prefetch:context/knowledge/dossiers/_prefetch/${code}.json;slim 若在:context/${code}.*_${date}_slim.md(Glob 找,含 _slim_deep)。按你的人设只填四个 LLM 节(<!-- LLM:待首覆 --> 处)与摘要叙事锚,不改确定性节。返回 code/initiated/summary_tokens/uncertainty。`,
  { agentType: 'dossier-init', effort: 'max', label: `init:${code}`, phase: 'Initiate', schema: INIT })
if (!r || !r.initiated) return { code, initiated: false, issues: ['agent 未完成或未回传'] }

phase('Lint')
const LINT = { type: 'object', required: ['ok'],
  properties: { ok: { type: 'boolean' }, reason: { type: 'string' } } }
const lint = await agent(
  `执行:\`${R.replace(' -m', '')} -c "from autoresearch.dossier import schema; import json, pathlib; t=pathlib.Path('${DP}').read_text(encoding='utf-8'); iss=schema.lint_dossier(t); print(json.dumps({'ok': not iss, 'reason': ';'.join(iss)[:200]}, ensure_ascii=False))"\`\n它打印一行 JSON,把最后一行 JSON 原样作为结构化返回。`,
  { agentType: 'general-purpose', effort: 'low', label: `lint:${code}`, phase: 'Lint', schema: LINT })
return { code, initiated: true, issues: lint && !lint.ok ? [lint.reason] : [] }
```

(`prefetch` 模块需支持 `python -m autoresearch.dossier.prefetch <code> <date>` 单码 CLI——在 Task 6 的 prefetch.py 已有 `prefetch_one`;本 task 给它补 `main(argv)` 两行入口,如缺则此处补上并在报告注明。)

- [ ] **Step 4: 文档两处**(stock-research SKILL 档位路由节尾加一行:`- **首覆建档**(覆盖池 pending_init → dossier-init workflow):见 spec 2026-07-22 ②,agent 真值源 .claude/agents/dossier-init.md`;engine-playbook 尾加小节指向同处)

- [ ] **Step 5: 跑锚测试 + 全回归 + node --check + Commit**

Run: `uv run --no-sync python -m pytest tests/test_agent_defs.py tests/ -q` → 全 passed;`node --check .claude/workflows/dossier-init.js` → OK

```bash
git add .claude/agents/dossier-init.md .claude/workflows/dossier-init.js tests/test_agent_defs.py .claude/skills/stock-research/SKILL.md .claude/skills/stock-research/engine-playbook.md autoresearch/dossier/prefetch.py
git commit -m "feat(dossier): 首覆 agent(dossier-init)+workflow——骨架→Opus四节→lint(契约锚入测)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: 端到端——4 只持仓建档(控制端自跑)

**Files:** 无代码改动(执行+验收任务;plan/台账回填)

**控制端(主会话)执行,不派 implementer**:

- [ ] **Step 1:** `uv run --no-sync python -m autoresearch.dossier.pool 2026-07-23` → 确认 4 只持仓(002371/300857/688766/601869)入池且在 `pending_init`(pinned 07-25 到期前仍 kept)。
- [ ] **Step 2:** 预取:`uv run --no-sync python -m autoresearch.dossier.prefetch <code> 2026-07-23` ×4(或 prefetch_pool);抽查 `_prefetch/*.json` 四腿完整度(mainbz 已探通 300857/601869;002371/688766 若缺留痕)。
- [ ] **Step 3:** 一条消息 4 个 `Workflow({scriptPath: '.claude/workflows/dossier-init.js', args: {date: '2026-07-23', code, name, sector}})` 并行拉起(slim 用 07-21 的仍在 context/,agent 会 Glob;缺就缺,留痕)。
- [ ] **Step 4:** 验收:4 份 `context/knowledge/dossiers/<code>.md` — `lint_dossier` 全空、frontmatter `initiated=2026-07-23`、摘要 ≤3k、§7 判例账本有 07-21 入围史、三情景无 EPS 点估、每条风险带证伪触发点(抽读 2 份人工核)。
- [ ] **Step 5:** `uv run --no-sync python -m autoresearch.dossier.pool 2026-07-23 --status` → `pending_init` 清空;plan 尾追加 `## Wave2 建档实录`;台账收官行;commit plan。

---

## 计划自审(已跑)

- **Spec 覆盖**:①schema+渲染器+摘要 lint(T3/T7)、③池管理+prelude(T4/T5)、②首覆 workflow+确定性预取+prewarm(T6/T8)、"先对 4 只持仓建档"(T9)、Wave-1 遗留(T1)。**Wave 2 验收三条**:4 份档案八节齐(T9-4)/摘要 ≤3k(schema lint+T9-4)/夜批不阻塞扫描(prefetch 在 prewarm、LLM 段在 session,T6/T8)。④L4 增量注入与⑦判例聚合刷新属 Wave 3,不在本 plan。
- **占位扫描**:T7 Step3 与 T4 Step3 的"按接口逐条实现"均已附核心完整代码或精确契约+测试;T8 agent def/js 全文在场;无 TBD。
- **类型一致**:`schema.SECTIONS/SUMMARY_HEAD/SUMMARY_ANCHORS/est_tokens/lint_dossier/dossier_path` T3 定义、T4(pending_init)/T7(builder)/T8(js lint 调用)消费一致;`pool.refresh` 返回键 T5 消费一致;`prefetch_one(code6, today, *, fetch, ths_fn, band_fn)` T6 定义、T8 CLI 消费;`mainbz_latest` T2→T6。
- **已知边界**:pinned 07-25 到期后 4 持仓靠"真选计数/手动"续池(池规则如实);keyless ths 入口函数名以源码为准(T6 明示 grep 步骤,不发明);`fina_mainbz` policy 字段镜像 forecast 条目(T2 明示);北交所 ts_code 后缀 4/8 开头 → .BJ(T2 已含)。
