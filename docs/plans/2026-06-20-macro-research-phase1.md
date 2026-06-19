# macro-research Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the data + skill scaffold so "研究全球宏观/中美宏观" harvests real regional-macro + cross-asset + A股中观 data with zero paid LLM API, and Claude (in-session) can produce the regional regime read + draft cross-asset & A股行业 allocation tables.

**Architecture:** Mirror the proven three-stage skeleton of `analyze-ticker` (`harvest_*.py` 取数 → Claude/playbook 推理 → `assemble_*.py` 组装+`parse_rating` 校验). Phase 1 builds the deterministic harvester (`scripts/harvest_macro.py`), a clone assembler with dual-table rating validation (`scripts/assemble_macro.py`), and the skill files (`SKILL.md` + `macro-playbook.md`). The Claude-reasoning report sections are produced at runtime by following the playbook — this plan delivers the scaffolding that makes that possible, not the prose.

**Tech Stack:** Python 3 (stdlib + `yfinance` + optional `akshare`), project dataflows (`tradingagents.agents.utils.agent_utils.get_macro_indicators` → FRED; `tradingagents.agents.utils.rating.parse_rating`), `uv` for the locked env, `pytest` for unit tests.

## Global Constraints

Copied verbatim from `docs/specs/2026-06-20-macro-research-design.md`. Every task implicitly includes these.

- **零付费 LLM API**:数据只走 FRED / akshare / yfinance / WebSearch;不实例化任何 LLM。
- **as-of 分析日,绝不取未来数据**:所有窗口 end = 分析日(默认今天)。
- **akshare 是 optional dep**:`import akshare` 失败 → 该块返回 `None`/降级 note,不崩;所有 akshare 调用走 `_ak_call`(重试+退避);失败 → 显式标注 + WebSearch 兜底指令,**绝不静默塌缩**(如把"逐日流向"塌成一个累计数)。
- **FRED 国际 series 走 raw-ID 透传**:`get_macro_indicators.invoke({"indicator": "<RAW_FRED_ID>", "curr_date": end})` 对任意合法 series 工作;**不改 `fred.py`**。
- **每个数字必出 context**;harvester 只取数不判断,判断留给 Claude(运行时按 playbook)。
- **运行一律 `uv run`、在仓库根目录**(否则 `.env`/依赖加载不到)。
- **测试**:`pytest`,`@pytest.mark.unit` 标记,网络全 mock(见 `tests/conftest.py` 的 autouse fixtures);跑 `uv run pytest tests/<file> -v`。
- **产物 gitignored**:`context/`、`reports/`。
- **五档评级**(`parse_rating`):`Buy | Overweight | Hold | Underweight | Sell`(语义:强超配/超配/中性/低配/强低配)。
- **标准库脚本是独立入口**:小工具(`_load_env`/`_ak_call`)在 `harvest_context.py` 与 `harvest_macro.py` 间**复制而非共享**(沿用本仓库现状,scripts 不作 package)。

---

## File Structure

| 文件 | 职责 | 新建/改 |
|---|---|---|
| `scripts/harvest_macro.py` | 零 LLM 取数:区域宏观(US/China/Global)+ 跨资产价 basket + A股中观骨架 → `context/macro/<date>/data.md` | 新建 |
| `scripts/assemble_macro.py` | 组装 `reports/macro/<date>/` 分段 → `macro_compass.md`;对 `decision.md` + `sector_map.md` 两表逐行 `parse_rating` 校验 | 新建 |
| `tests/test_harvest_macro.py` | 单测 harvester 的**纯函数 + 常量**(无网络) | 新建 |
| `tests/test_assemble_macro.py` | 单测 `parse_allocation` + 组装(tmp dir,无网络) | 新建 |
| `.claude/skills/macro-research/SKILL.md` | 触发词 + 6 步流程 + 何时用/不用 + 铁律 | 新建 |
| `.claude/skills/macro-research/macro-playbook.md` | 报告骨架 + agent 角色/输出格式 + 数据坑(Phase 1:区域读数 + 两张草表完整规格) | 新建 |

> 网络型 harvest 块**不写假单测**(mock 网络的 mock 无意义,且本仓库 `harvest_context.py` 即无单测——其 dataflows 单独测);它们由**冒烟运行**验证。纯函数与组装器**严格 TDD**。

---

## Task 1: harvester scaffold — env/imports/constants/pure helpers

**Files:**
- Create: `scripts/harvest_macro.py`
- Test: `tests/test_harvest_macro.py`

**Interfaces:**
- Produces: `ROOT: Path`; `US_FRED: list[str]`; `INTL_FRED: dict[str,str]`; `CROSS_ASSET: dict[str,str]`; `_load_env(env_path: Path) -> None`; `_ak_call(fn, tries=3, backoff=1.5)`; `_pct_change(first: float, last: float) -> str`; `_section(title: str, fn, *args, **kwargs) -> str`; `main() -> int`.

- [ ] **Step 1: Write the failing test** for the pure helper + constants.

Create `tests/test_harvest_macro.py`:
```python
"""Unit tests for the macro harvester's pure helpers + constant specs.
Network blocks are smoke-run, not unit-tested (see plan)."""
import importlib.util
from pathlib import Path

import pytest

# Load the standalone script as a module (it is not a package).
_SPEC = importlib.util.spec_from_file_location(
    "harvest_macro", Path(__file__).resolve().parent.parent / "scripts" / "harvest_macro.py"
)
harvest_macro = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(harvest_macro)


@pytest.mark.unit
def test_pct_change_formats_signed_percent():
    assert harvest_macro._pct_change(100.0, 110.0) == "+10.00%"
    assert harvest_macro._pct_change(100.0, 90.0) == "-10.00%"
    assert harvest_macro._pct_change(0.0, 5.0) == "n/a"   # zero base -> n/a, never crash


@pytest.mark.unit
def test_constant_specs_cover_required_universe():
    # US policy rate + curve + inflation + labor are non-negotiable.
    for alias in ("fed_funds_rate", "10y_treasury", "yield_curve", "cpi", "unemployment"):
        assert alias in harvest_macro.US_FRED
    # Cross-asset basket must carry the asset universe the user named (incl. JPY + crypto).
    for label in ("USDCNY", "USDJPY", "Gold", "Bitcoin"):
        assert label in harvest_macro.CROSS_ASSET
    # International series are FRED raw IDs (uppercase), used via passthrough.
    assert all(v == v.upper() for v in harvest_macro.INTL_FRED.values())
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_harvest_macro.py -v`
Expected: FAIL — `FileNotFoundError`/`ModuleNotFoundError` (scripts/harvest_macro.py does not exist yet).

- [ ] **Step 3: Write the scaffold** `scripts/harvest_macro.py`.

```python
"""Deterministic macro + 中观 harvester for the "Claude-as-engine" workflow.

Top-down sibling of scripts/harvest_context.py. Harvests REGIONAL macro
(US via FRED aliases, China via akshare macro_china_*, Global via FRED
international series by raw ID), the CROSS-ASSET price basket (yfinance), and
A-share 中观 (sector fund-flow, Dragon-Tiger, limit-up sentiment, northbound),
then dumps every raw output to one markdown file. No LLM is instantiated — only
free vendors (yfinance keyless; FRED needs FRED_API_KEY; akshare optional).

Usage:
    python scripts/harvest_macro.py [YYYY-MM-DD]
"""
import os
import sys
import time
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_env(env_path: Path) -> None:
    """Minimal .env loader (no dependency); never overrides the real environment.
    Verbatim from scripts/harvest_context.py."""
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


_load_env(ROOT / ".env")

import yfinance as yf  # noqa: E402

from tradingagents.agents.utils.agent_utils import get_macro_indicators  # noqa: E402
from tradingagents.dataflows.config import set_config  # noqa: E402
from tradingagents.default_config import DEFAULT_CONFIG  # noqa: E402

# US macro — friendly aliases already resolved by fred.py.
US_FRED = [
    "fed_funds_rate", "2y_treasury", "10y_treasury", "yield_curve",
    "cpi", "core_cpi", "core_pce", "inflation_expectations",
    "unemployment", "nonfarm_payrolls", "initial_claims",
    "real_gdp", "industrial_production", "m2",
    "NFCI", "DFII10",   # financial conditions + 10y real yield (raw FRED IDs)
]
# Global outer layer — FRED international series by RAW ID (passthrough). Any ID
# that returns MACRO_DATA_UNAVAILABLE at smoke time is dropped (see Task 2).
INTL_FRED = {
    "China CPI (YoY index, OECD)": "CHNCPIALLMINMEI",
    "Japan CPI (index, OECD)": "JPNCPIALLMINMEI",
    "Euro Area deposit facility rate": "ECBDFR",
}
# Cross-asset price basket (yfinance). Label -> symbol.
CROSS_ASSET = {
    "US Dollar Index": "DX-Y.NYB", "USDCNY": "CNY=X", "USDJPY": "JPY=X",
    "Gold": "GC=F", "WTI Oil": "CL=F", "Copper": "HG=F",
    "UST 10y yield (x10)": "^TNX", "VIX": "^VIX",
    "S&P500 (SPY)": "SPY", "CSI300": "000300.SS", "Hang Seng": "^HSI",
    "Bitcoin": "BTC-USD", "Ether": "ETH-USD",
}


def _pct_change(first: float, last: float) -> str:
    """Signed percent change; 'n/a' when the base is zero (never raises)."""
    try:
        if float(first) == 0:
            return "n/a"
        return f"{(float(last) - float(first)) / float(first) * 100:+.2f}%"
    except (TypeError, ValueError):
        return "n/a"


def _ak_call(fn, tries: int = 3, backoff: float = 1.5):
    """Call a flaky akshare endpoint with retries + linear backoff.
    Verbatim from scripts/harvest_context.py."""
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            if i < tries - 1:
                time.sleep(backoff * (i + 1))
    raise last


def _section(title: str, fn, *args, **kwargs) -> str:
    """Run one data call, capturing output or a readable error per section.
    Verbatim from scripts/harvest_context.py."""
    print(f"  - {title} ...", flush=True)
    try:
        out = fn.invoke(*args, **kwargs) if hasattr(fn, "invoke") else fn(*args, **kwargs)
        body = (out or "").strip() or "_(empty)_"
    except Exception as e:  # noqa: BLE001 — one flaky vendor must not kill the harvest
        body = f"_ERROR fetching this section: {e}_\n```\n{traceback.format_exc()}```"
    return f"\n## {title}\n\n{body}\n"


def main() -> int:
    trade_date = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    datetime.strptime(trade_date, "%Y-%m-%d")  # validate / fail loud on bad date
    set_config(DEFAULT_CONFIG)
    end = trade_date

    print(f"[harvest-macro] @ {trade_date}", flush=True)
    parts: list[str] = [
        f"# Macro data context — {trade_date}\n",
        f"_Harvested {datetime.now().isoformat(timespec='seconds')} via project data tools "
        f"+ yfinance + akshare. No LLM used._\n",
    ]

    # Blocks are wired in Tasks 2–4.

    out_dir = ROOT / "context" / "macro" / trade_date
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "data.md"
    out_path.write_text("".join(parts), encoding="utf-8")
    print(f"\n[saved] {out_path}  ({out_path.stat().st_size:,} bytes)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_harvest_macro.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Smoke-run the scaffold**

Run: `uv run python scripts/harvest_macro.py 2026-06-20`
Expected: prints `[harvest-macro] @ 2026-06-20` and `[saved] .../context/macro/2026-06-20/data.md`; file exists with the header.

- [ ] **Step 6: Commit**

```bash
git add scripts/harvest_macro.py tests/test_harvest_macro.py
git commit -m "feat(macro): harvest_macro scaffold — env/constants/helpers + tests"
```

---

## Task 2: regional macro blocks (US / China / Global)

**Files:**
- Modify: `scripts/harvest_macro.py` (add three block functions + wire into `main`)

**Interfaces:**
- Consumes: `US_FRED`, `INTL_FRED`, `get_macro_indicators`, `_ak_call`, `_section`.
- Produces: `us_macro_block(curr_date: str) -> str`; `china_macro_block(curr_date: str) -> str`; `global_macro_block(curr_date: str) -> str`.

- [ ] **Step 1: Add the US + China + Global block functions** (after `_section`, before `main`).

```python
def us_macro_block(curr_date: str) -> str:
    """US regional macro: every US_FRED series via the project's FRED tool."""
    out = []
    for series in US_FRED:
        try:
            md = get_macro_indicators.invoke({"indicator": series, "curr_date": curr_date})
        except Exception as e:  # noqa: BLE001
            md = f"_({series} unavailable: {e})_"
        out.append(f"### {series}\n\n{md}")
    return "\n\n".join(out)


def global_macro_block(curr_date: str) -> str:
    """Global outer layer: FRED international series by raw ID (passthrough).
    Series that FRED does not carry return MACRO_DATA_UNAVAILABLE — kept inline so
    the build-time smoke run can spot and drop them."""
    out = []
    for label, series_id in INTL_FRED.items():
        try:
            md = get_macro_indicators.invoke({"indicator": series_id, "curr_date": curr_date})
        except Exception as e:  # noqa: BLE001
            md = f"_({series_id} unavailable: {e})_"
        out.append(f"### {label} ({series_id})\n\n{md}")
    out.append(
        "\n_BOJ/ECB forward guidance, EM policy rates, and any series returning "
        "MACRO_DATA_UNAVAILABLE above → fetch via WebSearch at reasoning time, tag '实时网查'._"
    )
    return "\n\n".join(out)


def china_macro_block(curr_date: str) -> str:
    """China regional macro via akshare macro_china_* (OPTIONAL dep). Defensive:
    endpoint/column drift across akshare versions → degrade + WebSearch directive,
    never silently collapse."""
    try:
        import akshare as ak
    except ImportError:
        return ("_akshare 未安装(`uv add akshare`)→ 中国宏观走 WebSearch:CPI/PPI/PMI(官+财新)/"
                "社融·M2/LPR/外储/进出口/GDP/工增/社零/地产投资,标『实时网查』。_")
    # (label, callable) — each guarded independently so one bad endpoint can't kill the block.
    specs = [
        ("CPI 当月同比", lambda: ak.macro_china_cpi_monthly()),
        ("PPI 当月同比", lambda: ak.macro_china_ppi()),
        ("制造业 PMI", lambda: ak.macro_china_pmi()),
        ("社融规模存量", lambda: ak.macro_china_shrzgm()),
        ("货币供应 M2", lambda: ak.macro_china_money_supply()),
        ("LPR 利率", lambda: ak.macro_china_lpr()),
    ]
    out = []
    for label, fn in specs:
        try:
            df = _ak_call(fn)
            out.append(f"### {label}\n\n```\n{df.tail(6).to_string(index=False)}\n```")
        except Exception as e:  # noqa: BLE001
            out.append(f"### {label}\n\n_取数失败({e})→ WebSearch『{label} 最新』,标『实时网查』。_")
    return "\n\n".join(out)
```

- [ ] **Step 2: Wire the three blocks into `main`** — insert after the `parts = [...]` header block, before the `out_dir` write:

```python
    print("[regional macro]", flush=True)
    parts.append(_section("US macro (FRED)", us_macro_block, end))
    parts.append(_section("China macro (akshare macro_china)", china_macro_block, end))
    parts.append(_section("Global outer layer (FRED international + WebSearch)", global_macro_block, end))
```

- [ ] **Step 3: Smoke-run and verify regional data lands**

Run: `uv run python scripts/harvest_macro.py 2026-06-20`
Then inspect: `grep -c "## US macro\|## China macro\|## Global outer" context/macro/2026-06-20/data.md`
Expected: `3`. Open the file and confirm US FRED series show real numbers (e.g. `## FRED:` headers), and **note any INTL_FRED id that printed `MACRO_DATA_UNAVAILABLE`** — drop or replace it in `INTL_FRED`, then re-run.

- [ ] **Step 4: Verify FRED key + lookahead** — confirm `.env` has `FRED_API_KEY`; confirm no observation dates exceed `2026-06-20` in the US section (FRED tool is lookahead-safe by `curr_date`).

- [ ] **Step 5: Commit**

```bash
git add scripts/harvest_macro.py
git commit -m "feat(macro): regional macro blocks — US(FRED)/China(akshare)/Global(intl)"
```

---

## Task 3: cross-asset price basket block

**Files:**
- Modify: `scripts/harvest_macro.py` (add `_basket_table` pure helper + `cross_asset_block` + wire `main`)
- Modify: `tests/test_harvest_macro.py` (TDD `_basket_table`)

**Interfaces:**
- Consumes: `CROSS_ASSET`, `_pct_change`, `yf`.
- Produces: `_basket_table(rows: list[dict]) -> str` (pure); `cross_asset_block(curr_date: str) -> str`.

- [ ] **Step 1: Write the failing test** for the pure table formatter. Append to `tests/test_harvest_macro.py`:

```python
@pytest.mark.unit
def test_basket_table_renders_rows_and_handles_missing():
    rows = [
        {"label": "Gold", "symbol": "GC=F", "last": 2400.0, "chg_1m": "+3.10%", "chg_ytd": "+15.00%"},
        {"label": "USDCNY", "symbol": "CNY=X", "last": None, "chg_1m": "n/a", "chg_ytd": "n/a"},
    ]
    table = harvest_macro._basket_table(rows)
    assert "| Gold | GC=F | 2400.0 | +3.10% | +15.00% |" in table
    assert "| USDCNY | CNY=X | n/a | n/a | n/a |" in table   # None last -> 'n/a', no crash
    assert table.startswith("| Asset | Symbol |")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_harvest_macro.py::test_basket_table_renders_rows_and_handles_missing -v`
Expected: FAIL — `AttributeError: module 'harvest_macro' has no attribute '_basket_table'`.

- [ ] **Step 3: Add `_basket_table` + `cross_asset_block`** to `scripts/harvest_macro.py`:

```python
def _basket_table(rows: list[dict]) -> str:
    """Render the cross-asset basket as a markdown table (pure; None -> 'n/a')."""
    head = "| Asset | Symbol | Last | Δ1m | ΔYTD |\n|---|---|---:|---:|---:|"
    body = [
        f"| {r['label']} | {r['symbol']} | {'n/a' if r['last'] is None else r['last']} "
        f"| {r['chg_1m']} | {r['chg_ytd']} |"
        for r in rows
    ]
    return head + "\n" + "\n".join(body)


def cross_asset_block(curr_date: str) -> str:
    """Cross-asset price basket via yfinance: last + 1-month + YTD change.
    Windows end at curr_date (lookahead-safe)."""
    end = datetime.strptime(curr_date, "%Y-%m-%d")
    start = (end - timedelta(days=400)).strftime("%Y-%m-%d")
    ytd_anchor = f"{end.year}-01-01"
    rows = []
    for label, symbol in CROSS_ASSET.items():
        last = chg_1m = chg_ytd = None
        try:
            hist = yf.Ticker(symbol).history(start=start, end=curr_date)["Close"].dropna()
            if len(hist):
                last = round(float(hist.iloc[-1]), 4)
                m_ago = hist[hist.index <= (end - timedelta(days=30)).strftime("%Y-%m-%d")]
                ytd = hist[hist.index >= ytd_anchor]
                chg_1m = _pct_change(float(m_ago.iloc[-1]), last) if len(m_ago) else "n/a"
                chg_ytd = _pct_change(float(ytd.iloc[0]), last) if len(ytd) else "n/a"
        except Exception:  # noqa: BLE001 — degrade per-symbol
            pass
        rows.append({"label": label, "symbol": symbol,
                     "last": last, "chg_1m": chg_1m or "n/a", "chg_ytd": chg_ytd or "n/a"})
    return _basket_table(rows)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_harvest_macro.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Wire into `main`** — after the regional macro block:

```python
    print("[cross-asset]", flush=True)
    parts.append(_section("Cross-asset price basket (yfinance)", cross_asset_block, end))
```

- [ ] **Step 6: Smoke-run and verify**

Run: `uv run python scripts/harvest_macro.py 2026-06-20`
Then: `grep -A4 "Cross-asset price basket" context/macro/2026-06-20/data.md`
Expected: a table with real `Last` values for Gold/USDCNY/USDJPY/Bitcoin etc. (some futures may be `n/a` off-hours — acceptable).

- [ ] **Step 7: Commit**

```bash
git add scripts/harvest_macro.py tests/test_harvest_macro.py
git commit -m "feat(macro): cross-asset price basket (yfinance) + table formatter"
```

---

## Task 4: A股中观骨架 block (sector flow / 龙虎榜 / 涨停 / 北向)

**Files:**
- Modify: `scripts/harvest_macro.py` (add `meso_ashare_block` + wire `main`)

**Interfaces:**
- Consumes: `_ak_call`, `_section`.
- Produces: `meso_ashare_block(curr_date: str) -> str`.

> Phase 1 ships the **骨架**: sector fund-flow rank, Dragon-Tiger (游资) recent stats, limit-up sentiment, northbound summary. Concept/题材, 两融, ETF, style indices, industry-PE percentiles are Phase 2.

- [ ] **Step 1: Add `meso_ashare_block`** to `scripts/harvest_macro.py`:

```python
def meso_ashare_block(curr_date: str) -> str:
    """A-share 中观骨架 via akshare (OPTIONAL dep): sector fund-flow, Dragon-Tiger
    (游资), limit-up sentiment, northbound summary. Each guarded independently;
    failures degrade to an explicit WebSearch directive, never silent collapse."""
    try:
        import akshare as ak
    except ImportError:
        return ("_akshare 未安装 → A股中观走 WebSearch:行业资金流入流出排名 / 龙虎榜游资 / "
                "涨停家数·连板 / 北向资金,标『实时网查』。_")
    out = []
    # 1) 行业资金流排名(主力净流入)
    try:
        ff = _ak_call(lambda: ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流"))
        top = ff.head(8); bot = ff.tail(5)
        out.append("**行业主力资金流(今日)**\n\n```\n" + top.to_string(index=False)
                   + "\n... (净流出尾部)\n" + bot.to_string(index=False) + "\n```")
    except Exception as e:  # noqa: BLE001
        out.append(f"_行业资金流取数失败({e})→ WebSearch『今日行业主力资金净流入排名』,标『实时网查』。_")
    # 2) 龙虎榜 / 游资(近三月统计)
    try:
        lhb = _ak_call(lambda: ak.stock_lhb_stock_statistic_em(symbol="近三月"))
        out.append("**龙虎榜活跃个股(近三月,游资/机构席位线索)**\n\n```\n"
                   + lhb.head(12).to_string(index=False) + "\n```")
    except Exception as e:  # noqa: BLE001
        out.append(f"_龙虎榜取数失败({e})→ WebSearch『近期龙虎榜 游资 营业部』,标『实时网查』。_")
    # 3) 涨停情绪(回看最近有数据的交易日)
    try:
        base = datetime.strptime(curr_date, "%Y-%m-%d")
        zt = used = None
        for back in range(6):
            d = (base - timedelta(days=back)).strftime("%Y%m%d")
            try:
                z = ak.stock_zt_pool_em(date=d)
            except Exception:
                z = None
            if z is not None and len(z):
                zt, used = z, d
                break
        if zt is not None:
            maxlb = int(zt["连板数"].astype(int).max())
            hot = "、".join(f"{k}({v})" for k, v in zt["所属行业"].value_counts().head(5).items())
            out.append(f"**涨停情绪({used})**:涨停 **{len(zt)}** 家、最高 **{maxlb} 连板**;"
                       f"涨停最集中行业:{hot}。(涨停多+连板高=情绪亢奋;少=退潮)")
    except Exception as e:  # noqa: BLE001
        out.append(f"_涨停池取数失败({e})→ WebSearch『今日涨停家数 最高连板 涨停行业』,标『实时网查』。_")
    # 4) 北向资金(汇总;个股实时披露 2024-08 已停)
    try:
        nb = _ak_call(lambda: ak.stock_hsgt_fund_flow_summary_em())
        out.append("**北向资金(汇总;注:个股实时披露 2024-08 已停,仅汇总/板块/季度口径)**\n\n```\n"
                   + nb.tail(8).to_string(index=False) + "\n```")
    except Exception as e:  # noqa: BLE001
        out.append(f"_北向资金取数失败({e})→ WebSearch『北向资金 今日净流入 行业』,标『实时网查』。_")
    return "\n\n".join(out)
```

- [ ] **Step 2: Wire into `main`** — after the cross-asset block:

```python
    print("[A股中观]", flush=True)
    parts.append(_section("A股中观骨架 (行业资金/游资/涨停情绪/北向)", meso_ashare_block, end))
```

- [ ] **Step 3: Smoke-run and verify**

Run: `uv add akshare` (if not present), then `uv run python scripts/harvest_macro.py 2026-06-20`
Then: `grep -A2 "A股中观骨架" context/macro/2026-06-20/data.md`
Expected: section present; with akshare installed, at least one of 行业资金流/龙虎榜/涨停/北向 shows real data. With endpoints down, confirm each shows its **WebSearch directive** (not a silent blank).

- [ ] **Step 4: Commit**

```bash
git add scripts/harvest_macro.py
git commit -m "feat(macro): A股中观骨架 — sector flow/龙虎榜/涨停/北向 (defensive)"
```

---

## Task 5: assemble_macro.py — dual-table parse_rating validation

**Files:**
- Create: `scripts/assemble_macro.py`
- Test: `tests/test_assemble_macro.py`

**Interfaces:**
- Consumes: `tradingagents.agents.utils.rating.parse_rating`.
- Produces: `parse_allocation(text: str) -> dict[str, str]`; `main() -> int` (CLI: `assemble_macro.py reports/macro/<date>`).

- [ ] **Step 1: Write the failing test** for `parse_allocation`. Create `tests/test_assemble_macro.py`:

```python
"""Unit tests for assemble_macro: keyed-row allocation parsing + assembly."""
import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "assemble_macro", Path(__file__).resolve().parent.parent / "scripts" / "assemble_macro.py"
)
assemble_macro = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(assemble_macro)


@pytest.mark.unit
def test_parse_allocation_extracts_every_keyed_row():
    text = (
        "**跨资产配置表**\n\n"
        "- OVERALL 风险档: **Rating**: Hold — 中性偏防御\n"
        "- 美债: **Rating**: Overweight — 衰退对冲\n"
        "- USD: **Rating**: Underweight — 降息在即\n"
        "- 加密(BTC): **Rating**: Buy — 流动性+美元替代\n"
    )
    alloc = assemble_macro.parse_allocation(text)
    assert alloc["OVERALL 风险档"] == "Hold"
    assert alloc["美债"] == "Overweight"
    assert alloc["USD"] == "Underweight"
    assert alloc["加密(BTC)"] == "Buy"


@pytest.mark.unit
def test_parse_allocation_ignores_non_rating_lines():
    text = "# 标题\n普通段落,无评级。\n- 美股: 走强但拥挤\n"
    assert assemble_macro.parse_allocation(text) == {}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_assemble_macro.py -v`
Expected: FAIL — file/module not found.

- [ ] **Step 3: Create `scripts/assemble_macro.py`** (clone of `scripts/assemble_report.py` structure; section lists swapped + `parse_allocation` added).

```python
"""Assemble macro-research per-agent markdown into one macro_compass.md.

Two-tier like assemble_report.py, plus a 中观 tier:
  ▸ 决策主线   decision / variant / crossfire / calendar / premortem (+debate)
  ▸ 中观落地   sector_map / flows / sentiment / themes
  ▸ 证据附录   regional(us/china/global) · crossasset(rates/fx/equities/commodities/crypto[/credit])
               · sino-us(divergence/desync/geopolitics/relative) · meso_evidence(industry_cycle)

The decision (cross-asset) and sector_map (A股行业) tables each carry one keyed
`- <KEY>: **Rating**: <band>` line per row; parse_allocation runs the project's
parse_rating on each so all five-band tilts stay machine-checked.

Usage:
    python scripts/assemble_macro.py reports/macro/<YYYY-MM-DD>
"""
import re
import sys
from datetime import datetime
from pathlib import Path

from tradingagents.agents.utils.rating import parse_rating

DECISION_REL = "1_spine/decision.md"
SECTOR_MAP_REL = "2_meso/sector_map.md"

SPINE = [
    ("S2 · 投资逻辑 & 预期差", [("Variant View", "1_spine/variant.md", False)]),
    ("S3 · 中美对撞 & 情景矩阵", [("Crossfire & Scenarios", "1_spine/crossfire.md", False)]),
    ("S4 · 催化剂日历 & 触发位", [("Catalyst Calendar", "1_spine/calendar.md", False)]),
    ("S5 · 风险 · 认错 · 监控", [
        ("Pre-Mortem & Monitoring", "1_spine/premortem.md", False),
        ("Risk Debate", "1_spine/debate.md", True),
    ]),
]
MESO = [
    ("M1 · A股行业配置图", [("Sector Allocation Map", "2_meso/sector_map.md", False)]),
    ("M2 · 资金 & 游资", [("Flows & Hot Money", "2_meso/flows.md", False)]),
    ("M3 · 情绪周期 & 涨停结构", [("Sentiment Cycle", "2_meso/sentiment.md", False)]),
    ("M4 · 题材 & 风格轮动", [("Themes & Style", "2_meso/themes.md", False)]),
]
APPENDIX = [
    ("A · 区域宏观", [
        ("United States", "3_regional/us.md", False),
        ("China", "3_regional/china.md", False),
        ("Global (EU/Japan/EM)", "3_regional/global.md", False),
    ]),
    ("B · 跨资产 & 传导", [
        ("Rates & Central Banks", "4_crossasset/rates.md", False),
        ("FX (USD/CNY/JPY)", "4_crossasset/fx.md", False),
        ("Equities (US vs A/H)", "4_crossasset/equities.md", False),
        ("Commodities & Gold", "4_crossasset/commodities.md", False),
        ("Crypto", "4_crossasset/crypto.md", False),
        ("Credit & Liquidity", "4_crossasset/credit.md", True),
    ]),
    ("C · 中美专题", [
        ("Monetary Divergence", "5_sinous/divergence.md", False),
        ("Growth/Inflation Desync", "5_sinous/desync.md", False),
        ("Trade / Tariff / Geopolitics", "5_sinous/geopolitics.md", False),
        ("Relative Assets & Flows", "5_sinous/relative.md", False),
    ]),
    ("D · 中观明细", [
        ("Industry Cycle Bridge", "6_meso_evidence/industry_cycle.md", True),
    ]),
]

SPINE_BANNER = "**═══════════ 决策主线 · Decision Spine(读它就能配置)═══════════**"
MESO_BANNER = "**═══════════ 中观落地 · A股行业/资金/情绪═══════════**"
APPENDIX_BANNER = "**═══════════ 证据附录 · Evidence Appendix═══════════**"


def parse_allocation(text: str) -> dict:
    """Extract every keyed allocation rating. Each row: `- <KEY>: **Rating**: <band>`.
    parse_rating runs on the single line, so each row's band is machine-checked."""
    out = {}
    for line in text.splitlines():
        if "**Rating**" not in line and "Rating:" not in line:
            continue
        m = re.match(r"\s*[-*]\s*(.+?)\s*[::]", line)
        if not m:
            continue
        out[m.group(1).strip()] = parse_rating(line)
    return out


def _slug(title: str) -> str:
    s = re.sub(r"[^\w\s-]", "", title.strip().lower())
    return re.sub(r"\s+", "-", s)


def _anchored(tag: str, title: str, body: str = "") -> str:
    block = f'\n<a id="{_slug(title)}"></a>\n\n{tag} {title}\n'
    return f"{block}\n{body}\n" if body else block


def _present(root: Path, items):
    return [(name, rel) for name, rel, _ in items if (root / rel).exists()]


def _read(root: Path, rel: str) -> str:
    return (root / rel).read_text(encoding="utf-8").strip()


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    root = Path(sys.argv[1])

    required = [DECISION_REL] + [
        rel for _, items in (SPINE + MESO + APPENDIX) for _, rel, opt in items if not opt
    ]
    missing = [rel for rel in required if not (root / rel).exists()]
    if missing:
        print("[MISSING] 必需分段文件不存在,请先写齐核心 agent 文件再组装:")
        for rel in missing:
            print(f"  - {root / rel}")
        return 1

    out = [f"# Macro Research Report: {root.name}\n",
           f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ",
           "_Engine: Claude (in-session), zero paid LLM API. "
           "Data: FRED + akshare + yfinance._\n"]

    skipped = [rel for _, items in (SPINE + MESO + APPENDIX)
               for _, rel, opt in items if opt and not (root / rel).exists()]

    out.append("\n---\n\n" + SPINE_BANNER + "\n")
    out.append(_anchored("##", "S1 · 执行摘要 · 配置决策", _read(root, DECISION_REL)))
    for title, present in [(t, p) for t, items in SPINE if (p := _present(root, items))]:
        if len(present) == 1:
            out.append(_anchored("##", title, _read(root, present[0][1])))
        else:
            out.append(_anchored("##", title))
            for name, rel in present:
                out.append(_anchored("###", name, _read(root, rel)))

    out.append("\n---\n\n" + MESO_BANNER + "\n")
    for title, present in [(t, p) for t, items in MESO if (p := _present(root, items))]:
        out.append(_anchored("##", title, _read(root, present[0][1])))

    out.append("\n---\n\n" + APPENDIX_BANNER + "\n")
    for title, present in [(t, p) for t, items in APPENDIX if (p := _present(root, items))]:
        out.append(_anchored("##", title))
        for name, rel in present:
            out.append(_anchored("###", name, _read(root, rel)))

    (root / "macro_compass.md").write_text("\n".join(out), encoding="utf-8")
    print(f"[assembled] {root / 'macro_compass.md'}")

    alloc = parse_allocation(_read(root, DECISION_REL))
    print(f"[parse_rating → cross-asset ({len(alloc)})] {alloc}")
    if (root / SECTOR_MAP_REL).exists():
        sectors = parse_allocation(_read(root, SECTOR_MAP_REL))
        print(f"[parse_rating → A股 sectors ({len(sectors)})] {sectors}")
    if skipped:
        print("[note] 跳过未提供的可选分段: " + ", ".join(skipped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Write the assembly integration test.** Append to `tests/test_assemble_macro.py`:

```python
def _write(root: Path, rel: str, body: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


@pytest.mark.unit
def test_main_reports_missing_when_core_files_absent(tmp_path, capsys):
    import sys
    argv = sys.argv
    sys.argv = ["assemble_macro.py", str(tmp_path)]
    try:
        ret = assemble_macro.main()
    finally:
        sys.argv = argv
    assert ret == 1
    assert "[MISSING]" in capsys.readouterr().out


@pytest.mark.unit
def test_main_assembles_and_validates_both_tables(tmp_path, capsys):
    # minimal required set
    _write(tmp_path, "1_spine/decision.md",
           "**配置表**\n- OVERALL 风险档: **Rating**: Hold\n- 美债: **Rating**: Overweight\n")
    for rel in ("1_spine/variant.md", "1_spine/crossfire.md", "1_spine/calendar.md",
                "1_spine/premortem.md", "2_meso/flows.md", "2_meso/sentiment.md",
                "2_meso/themes.md", "3_regional/us.md", "3_regional/china.md",
                "3_regional/global.md", "4_crossasset/rates.md", "4_crossasset/fx.md",
                "4_crossasset/equities.md", "4_crossasset/commodities.md",
                "4_crossasset/crypto.md", "5_sinous/divergence.md", "5_sinous/desync.md",
                "5_sinous/geopolitics.md", "5_sinous/relative.md"):
        _write(tmp_path, rel, f"stub {rel}")
    _write(tmp_path, "2_meso/sector_map.md",
           "**行业表**\n- 电子: **Rating**: Overweight\n- 地产: **Rating**: Underweight\n")
    import sys
    argv = sys.argv
    sys.argv = ["assemble_macro.py", str(tmp_path)]
    try:
        ret = assemble_macro.main()
    finally:
        sys.argv = argv
    out = capsys.readouterr().out
    assert ret == 0
    assert (tmp_path / "macro_compass.md").exists()
    assert "cross-asset (2)" in out          # OVERALL + 美债
    assert "A股 sectors (2)" in out           # 电子 + 地产
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_assemble_macro.py -v`
Expected: PASS (4 passed).

- [ ] **Step 6: Commit**

```bash
git add scripts/assemble_macro.py tests/test_assemble_macro.py
git commit -m "feat(macro): assemble_macro — 3-tier assembly + dual-table parse_rating"
```

---

## Task 6: SKILL.md

**Files:**
- Create: `.claude/skills/macro-research/SKILL.md`

- [ ] **Step 1: Write the skill entry** (frontmatter + 6-step flow). Mirror `analyze-ticker/SKILL.md` tone.

```markdown
---
name: macro-research
description: Use when the user wants top-down GLOBAL + 中美 macro research that ends in cross-asset allocation tilts AND A股 sector/中观 read — e.g. "研究全球宏观", "中美宏观现在怎么看", "现在该超配什么资产", "A股哪些行业值得配", "give me a macro regime + asset allocation view". NOT for one named ticker (use analyze-ticker) or a full A-share stock screen (use scan-market). Project-local skill.
---

# macro-research — 在 session 内零付费 API 跑全球+中美宏观 + A股中观 → 配置

## 核心原理
宏观研究 = `确定性数据(免费)` + `多 agent 推理(本来要钱)`。本 skill 调项目数据工具取真宏观/中观数据(FRED/akshare/yfinance),把推理换成 Claude(本 session)——零 LLM API,产出 regime 判断 + 跨资产配置表 + A股行业配置表。

## 何时用 / 不用
- ✅ 自上而下的宏观/中美/中观研究,收在跨资产 + A股行业的超-中-低配。
- ❌ 单只票 → analyze-ticker;❌ 全 A股选股 → scan-market。

## 前置
- 仓库根目录运行;`.env` 需 `FRED_API_KEY`。A股中观需 `uv add akshare`。报告默认中文。

## 流程(6 步)
1. **取数(零 LLM)**:`uv run python scripts/harvest_macro.py [YYYY-MM-DD]` → `context/macro/<date>/data.md`(区域宏观 + 跨资产 basket + A股中观骨架)。
2. **读 context**:分页读 `context/macro/<date>/data.md`,锁定 US/China/Global 宏观、跨资产价、A股中观(行业资金/游资/涨停/北向)。
3. **读 playbook**:读本目录 `macro-playbook.md` 拿报告骨架 + 各 agent 角色/输出格式 + 数据坑,不回翻代码。
4. **扮演各 agent**:按 playbook 顺序逐段产出到 `reports/macro/<date>/`(目录结构见 playbook)。**每个数字必出 context;判断性内容(情景概率/政策路径)显式标『判断』或『实时网查』。**
5. **组装+校验**:`uv run python scripts/assemble_macro.py reports/macro/<date>` → `macro_compass.md`,并对跨资产表 + A股行业表逐行打印 `parse_rating` 信号。`[MISSING]` 则补齐必需分段。
6. **汇报**:regime 判断 + 两张配置表(关键超/低配 + 触发位)+ 诚实局限。

## 铁律(防幻觉)
- 每个价格/宏观数字出自 context;实时网查数标来源/日期。
- 宏观判断性内容(情景概率、政策路径、央行反应函数)显式标注,不冒充确定性数据。
- 分析窗口钉死分析日,绝不用未来数据。
- 中美对撞 / Risk Debate 必须有真实张力。
- 北向个股实时披露 2024-08 已停 → 中观北向只用汇总/板块口径,标 staleness。
- 收尾写明:这是 Claude 推理产出、非自动引擎;仅供研究,非投资建议。

## 常见坑
- 必须 `uv run` + 仓库根目录。
- akshare 版本漂/限流 → harvester 已防御降级 + WebSearch 兜底;context 出现『取数失败 → WebSearch』时,推理阶段务必网查补回,别静默跳过。
- FRED 国际 series 若 `MACRO_DATA_UNAVAILABLE` → 该指标走 WebSearch,标『实时网查』。
```

- [ ] **Step 2: Verify the skill is discoverable** — confirm the file path is `.claude/skills/macro-research/SKILL.md` and frontmatter `name:` matches the directory.

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/macro-research/SKILL.md
git commit -m "feat(macro): add macro-research SKILL.md (trigger + 6-step flow)"
```

---

## Task 7: macro-playbook.md

**Files:**
- Create: `.claude/skills/macro-research/macro-playbook.md`

> Phase 1 must FULLY specify: the file-mapping (so it matches `assemble_macro.py`), the two allocation-table formats (keyed `**Rating**` rows), the regional-read agent roles, and the data 坑. The S2–S5 + M2–M4 + C-lens prose specs can be terse here and expanded in Phase 2 — but the file list and table formats are load-bearing now.

- [ ] **Step 1: Write the playbook.** It MUST keep the file map identical to `assemble_macro.py`'s section lists.

```markdown
# macro-playbook — agent 蒸馏参考 + 报告骨架(Phase 1)

> 读完这份就不用回翻代码。报告 = 决策主线 + 中观落地 + 证据附录三层。

## 输出文件映射(须与 assemble_macro.py 一致)
```
reports/macro/<date>/
  1_spine/      decision.md  variant.md  crossfire.md  calendar.md  premortem.md  debate.md(opt)
  2_meso/       sector_map.md  flows.md  sentiment.md  themes.md
  3_regional/   us.md  china.md  global.md
  4_crossasset/ rates.md  fx.md  equities.md  commodities.md  crypto.md  credit.md(opt)
  5_sinous/     divergence.md  desync.md  geopolitics.md  relative.md
  6_meso_evidence/  industry_cycle.md(opt)
```
**必需**:decision variant crossfire calendar premortem · sector_map flows sentiment themes · us china global · rates fx equities commodities crypto · divergence desync geopolitics relative。
**optional**:debate credit industry_cycle。

## 两张配置表的机器可读约定(关键)
`decision.md`(跨资产)与 `sector_map.md`(A股行业)在表后,**每行补一行**:
`- <KEY>: **Rating**: <Buy|Overweight|Hold|Underweight|Sell> — <一句依据(落 context 数字)>`
- 跨资产 KEY:`OVERALL 风险档 / 美债 / 美股 / A股·港股 / USD / CNY / JPY / 黄金 / 大宗 / 加密(BTC) / 信用`。
- A股行业 KEY:申万一级行业名。
- 5 档语义:Buy=强超配 / Overweight=超配 / Hold=中性 / Underweight=低配 / Sell=强低配。
- assemble 对每行单独跑 parse_rating(无"首标签胜出"碰撞,因每行只有一个标签)。

## 决策主线 — S1 decision.md(Phase 1 重点)
顶部两张表 + 摘要:
1. **宏观仪表盘**(一行):regime 象限(增长×通胀)/ 美政策档 / 中政策档 / 全球流动性 / 风险偏好档 / 关键假设 / 置信度。
2. **跨资产配置表**(每行:5 档倾向 + 关键驱动 + 主要表达 + 触发/失效位)+ 表后的 keyed `**Rating**` 行(含 `OVERALL 风险档`)。
3. **执行摘要** 2–4 句。

## 中观落地 — M1 sector_map.md(Phase 1 重点)
申万一级行业排名表:相对强度(1/5/20日)+ 主力净流入(来自 context 行业资金流)+ 北向变化(标 staleness)+ 估值方向 → 每行业 5 档倾向 + 表后 keyed `**Rating**` 行。
> M2 flows / M3 sentiment / M4 themes:Phase 1 可据 context 中观骨架写**精简版**(资金&游资逐条、涨停情绪档位、题材/风格一句),Phase 2 补全。

## 区域读数 — A 区域宏观(Phase 1 重点)
- `us.md`:增长/通胀/就业/金融条件/政策路径 — 全部数字出 context 的 US FRED 段;判断 Fed 反应函数标『判断』。
- `china.md`:增长/通胀/信用/政策/地产 — 出 context China 段;akshare 缺失项走 WebSearch 标『实时网查』。
- `global.md`:欧/日/EM;日本重点(BOJ/JPY/套息)。BOJ/ECB 前瞻走 WebSearch。

## 综合段(Phase 1 可精简,Phase 2 展开)
- S2 variant.md:市场 price-in 什么 vs 我们哪里不同 vs 何时收敛。
- S3 crossfire.md:中美对撞表(货币分化/增长错位/贸易地缘/相对资产 四行)+ 增长×通胀四象限情景 + 概率。
- S4 calendar.md:FOMC/CPI/NFP · 中国 PMI/社融/LPR/NPC · BOJ/ECB · 关税地缘,每条标方向 + 是否=调仓触发。
- S5 premortem.md:红队 3–4 死因 + 早期预警位 + 配置监控 KPI 表。

## 证据附录 B/C(Phase 1 可精简)
- B 跨资产:rates/fx/equities/commodities/crypto 各一段,数字出 context 的跨资产 basket;credit 可选。
- C 中美四专题:divergence/desync/geopolitics/relative,对应 S3 四行的详证。

## 全员通用标准
- 每段结尾一行:`置信度: 高/中/低 ｜ 最大不确定项: …`。
- 每个数字出 context;判断/网查显式标注。
- as-of 分析日,无未来数据。

## 已知数据坑
1. FRED 国际 series 若 MACRO_DATA_UNAVAILABLE → WebSearch,标『实时网查』。
2. akshare 中观端点版本漂/限流 → context 已留 WebSearch 指令,推理阶段补回,别静默跳过。
3. 北向个股实时披露 2024-08 已停 → 只用汇总/板块/季度口径,标 staleness。
4. 跨资产相关性随 regime 漂移(通胀期股债翻正)→ 配置表声明当前相关性假设。
5. 期货(GC=F/CL=F)盘后可能 n/a → 用现货 ETF 或标注时点。
```

- [ ] **Step 2: Cross-check** — open `scripts/assemble_macro.py` and confirm every `*_REL` path + required file in `SPINE/MESO/APPENDIX` appears in the playbook's file map (and vice-versa). They MUST match exactly.

- [ ] **Step 3: End-to-end dry check** — create a throwaway `reports/macro/2026-06-20/` with the required stub files (one keyed `**Rating**` row each in decision.md + sector_map.md) and run `uv run python scripts/assemble_macro.py reports/macro/2026-06-20`. Expected: `[assembled]` + two `parse_rating →` lines. Delete the throwaway dir after.

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/macro-research/macro-playbook.md
git commit -m "feat(macro): add macro-playbook.md (skeleton + table formats + 坑)"
```

---

## Self-Review

**1. Spec coverage (Phase 1 scope):**
- harvest_macro.py 区域宏观(US/China/Global) → Task 2 ✓
- 跨资产 basket(含 JPY + crypto) → Task 3 ✓
- A股中观骨架(行业资金/游资/涨停/北向) → Task 4 ✓
- assemble_macro 双表 parse_rating 校验 → Task 5 ✓
- SKILL.md + playbook(文件映射 + 两表格式 + 区域读数 + 坑) → Tasks 6–7 ✓
- Phase 2 (B 跨资产详证 / C 中美四专题 / M2–M4 full / S2–S5 full / 两融·ETF·风格·概念·行业PE 中观) — **out of Phase 1 by design** (spec §10).

**2. Placeholder scan:** No "TBD/TODO/handle edge cases" — every code step shows full code; network blocks have explicit degrade text. INTL_FRED ids carry a build-time drop/verify step (Task 2 Step 3), not a placeholder.

**3. Type consistency:** `_basket_table(rows: list[dict])` keys (`label/symbol/last/chg_1m/chg_ytd`) match `cross_asset_block`'s row dict ✓. `parse_allocation` keyed-row format (`- <KEY>: **Rating**: <band>`) is identical in Task 5 (parser), Task 5 tests, and Task 7 playbook ✓. `assemble_macro` `DECISION_REL`/`SECTOR_MAP_REL`/section `*_REL` paths match Task 7 file map ✓. `get_macro_indicators.invoke({"indicator","curr_date"})` matches harvest_context usage ✓.

---

## Execution Handoff

**Plan complete and saved to `docs/plans/2026-06-20-macro-research-phase1.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
