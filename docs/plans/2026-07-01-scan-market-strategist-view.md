# scan-market 首席策略师市场研判 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 scan-market 引入一个"一次产出、L3/L4/L5 三处复用"的资深投资大师市场研判 —— L2 后由 Opus 首席策略师 subagent 写 `market_view.md`,L3/L4 读描述性地形做 regime 校准,L5 嵌研判 + 确定性漏斗读数尾注。

**Architecture:** 新增确定性模块 `autoresearch/scan/market.py`(零 LLM 聚合市场事实 + 派生注入块/尾注/回退);`assemble.py` 只读 `market_view.md` 并嵌入(保持零-LLM);`l4_card.compose_funnel_brief` 头部注入**描述性**地形块(防锚定护栏)。策略师 subagent 本身是 skill 编排步骤(prompt 模板落 playbook),不写进 Python。

**Tech Stack:** Python 3、pandas、pytest。复用 `autoresearch.common.regime.classify_regime`、`autoresearch.scan.agents.l4_card.parse_ratings_from_details`、`autoresearch.scan.assemble._load_verify/_apply_verify_downgrade`。

## Global Constraints

- **确定性层零-LLM**:`market.py` 与 `assemble.py` 全 pandas/stdlib,不预测、不编数;LLM 那步是策略师 subagent 写 staging 文件。
- **防锚定护栏**:喂 L3/L4 的地形块是**描述性**(regime/宽度/估值分散/板块红黑榜),**无个股方向 / 无操作指令**;每处附铁律文案"个股评级只由本股 rubric 三门决定"。
- **产出分层**:描述性内容(地形)→ L3/L4;规范性 + 结果依赖内容(操作建议、N买/0买、观察单)→ 仅 L5。
- **parity 不破**:无 `market_view.md` 且无 `L1_scored_full.csv` → 不加任何市场节,summary/brief 与旧行为逐字节一致。
- **market_pack 只读 `L1_scored_full.csv`**(全市场真宽度),**不回退** `L1_recall_top1000`(composite 偏置子集会扭曲 breadth)。
- **跨模块 import 全用函数级(lazy)**:`market↔assemble`、`market↔l4_card` 互引,只在函数体内 import,避免加载期 import cycle。
- 测试:合成 fixture、`tmp_path`、无网络;运行用 `uv run --no-sync pytest ...`。

---

### Task 1: `market.py` — `market_pack()` 市场事实聚合

**Files:**
- Create: `autoresearch/scan/market.py`
- Test: `tests/scan/test_market_pack.py`

**Interfaces:**
- Consumes: `autoresearch.common.regime.classify_regime`(返回 `RegimeState`,有 `.to_dict()`)。
- Produces: `market_pack(scan_dir: Path | str) -> dict`,keys `{"regime","breadth","valuation","money","sectors"}`,缺数据对应值 `None`。内部 helper `_num/_frac_of/_med/_quantile/_round` 供后续 Task 复用。

- [ ] **Step 1: Write the failing test**

```python
# tests/scan/test_market_pack.py
import pandas as pd
from autoresearch.scan.market import market_pack


def _mk(tmp_path, rows, sectors=None):
    d = tmp_path / "s"
    d.mkdir()
    pd.DataFrame(rows).to_csv(d / "L1_scored_full.csv", index=False)
    if sectors is not None:
        pd.DataFrame(sectors).to_csv(d / "sectors.csv", index=False)
    return d


def _rows(n_up, n_down):
    rows = []
    for i in range(n_up):
        rows.append({"code": f"{i:06d}", "above_ma60": 1.0, "ma_bull": 1.0, "pct_60d": 10.0,
                     "pct_ytd": 5.0, "pe": 30.0, "pb": 2.0, "main_net_ratio": 0.02, "cmf_20": 0.1})
    for i in range(n_down):
        rows.append({"code": f"{100 + i:06d}", "above_ma60": 0.0, "ma_bull": 0.0, "pct_60d": -25.0,
                     "pct_ytd": -30.0, "pe": 120.0, "pb": 5.0, "main_net_ratio": -0.03, "cmf_20": -0.2})
    return rows


def test_regime_and_breadth(tmp_path):
    pack = market_pack(_mk(tmp_path, _rows(8, 2)))   # breadth 0.8, med_mom>0 → trend
    assert pack["regime"]["label"] == "trend"
    assert pack["breadth"]["above_ma60"] == 0.8
    assert pack["breadth"]["up_60d"] == 0.8


def test_falling_knife_risk_off(tmp_path):
    pack = market_pack(_mk(tmp_path, _rows(2, 8)))   # breadth 0.2, med_mom<0 → risk_off
    assert pack["regime"]["label"] == "risk_off"
    assert pack["breadth"]["falling_knife"] == 0.8


def test_valuation_pe_positive_only(tmp_path):
    rows = _rows(5, 5)
    rows.append({"code": "999999", "above_ma60": 0.0, "pct_60d": -5.0, "pe": -10.0, "pb": 1.0})
    pack = market_pack(_mk(tmp_path, rows))
    assert pack["valuation"]["med_pe"] > 0           # 负 PE 被剔除


def test_sectors_red_black_ordering(tmp_path):
    secs = [
        {"industry": "半导体", "n_recall": 49, "median_composite": 26.6, "median_pct_60d": 114.1,
         "median_main_net_ratio": -0.002, "is_top": True},
        {"industry": "软件开发", "n_recall": 35, "median_composite": 61.0, "median_pct_60d": -24.6,
         "median_main_net_ratio": -0.02, "is_top": True},
        {"industry": "汽车零部件", "n_recall": 52, "median_composite": 62.8, "median_pct_60d": -25.3,
         "median_main_net_ratio": -0.018, "is_top": True},
    ]
    pack = market_pack(_mk(tmp_path, _rows(5, 5), sectors=secs))
    assert pack["sectors"]["red"][0]["industry"] == "半导体"        # 最高 median_pct_60d
    assert pack["sectors"]["black"][0]["industry"] == "汽车零部件"   # 最低 median_pct_60d


def test_missing_columns_degrade(tmp_path):
    pack = market_pack(_mk(tmp_path, [{"code": "000001", "pct_60d": 3.0}]))
    assert pack["regime"] is not None                # pct_60d>0 代理 breadth
    assert pack["valuation"]["med_pe"] is None
    assert pack["sectors"] is None


def test_no_l1_returns_empty(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    pack = market_pack(d)
    assert pack["regime"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/scan/test_market_pack.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'autoresearch.scan.market'`

- [ ] **Step 3: Write minimal implementation**

```python
# autoresearch/scan/market.py
#!/usr/bin/env python3
"""scan-market · 市场级确定性聚合 —— 首席策略师数据包 + L3/L4 注入地形块 + L5 尾注/回退。

design: docs/specs/2026-07-01-scan-market-strategist-view-design.md

零 LLM。market_pack 从 L1_scored_full + sectors.csv 聚合"今日市场"事实(regime/宽度/估值分散/
资金/板块红黑榜);market_context_block 派生**描述性**地形块喂 L3/L4(防锚定:只描述不指令);
render_funnel_readout 给 L5 确定性漏斗读数尾注;render_fallback_pulse 给 market_view 缺失时回退。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from autoresearch.common.regime import classify_regime

_REGIME_ZH = {"trend": "趋势", "range": "震荡", "risk_off": "避险"}


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series([], dtype=float)
    return pd.to_numeric(df[col], errors="coerce")


def _frac_of(s: pd.Series, cond) -> float | None:
    """s 丢 NaN 后满足 cond 的占比(先 dropna 值再比较,对齐 classify_regime);空 → None。"""
    s = s.dropna()
    return round(float(cond(s).mean()), 4) if len(s) else None


def _med(s: pd.Series) -> float | None:
    s = s.dropna()
    return round(float(s.median()), 2) if len(s) else None


def _quantile(s: pd.Series, q: float) -> float | None:
    s = s.dropna()
    return round(float(s.quantile(q)), 2) if len(s) else None


def _round(v, nd: int = 2):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else round(f, nd)   # NaN → None


def _breadth(df: pd.DataFrame) -> dict:
    p60 = _num(df, "pct_60d")
    return {
        "above_ma60": _frac_of(_num(df, "above_ma60"), lambda x: x > 0),
        "ma_bull": _frac_of(_num(df, "ma_bull"), lambda x: x > 0),
        "med_pct_60d": _med(p60),
        "med_pct_ytd": _med(_num(df, "pct_ytd")),
        "falling_knife": _frac_of(p60, lambda x: x < -20),
        "up_60d": _frac_of(p60, lambda x: x > 0),
    }


def _valuation(df: pd.DataFrame) -> dict:
    pe = _num(df, "pe")
    pe_pos = pe[pe > 0]
    return {
        "med_pe": _med(pe_pos),
        "med_pb": _med(_num(df, "pb")),
        "pe_top_decile": _quantile(pe_pos, 0.90),
        "pe_gt_60": _frac_of(pe_pos, lambda x: x > 60),
    }


def _money(df: pd.DataFrame) -> dict:
    mnr = _num(df, "main_net_ratio")
    return {
        "main_pos": _frac_of(mnr, lambda x: x > 0),
        "med_main_ratio": _med(mnr),
        "cmf_pos": _frac_of(_num(df, "cmf_20"), lambda x: x > 0),
    }


def _sectors(sec: pd.DataFrame, n: int = 5) -> dict | None:
    if "median_pct_60d" not in sec.columns or not len(sec):
        return None
    s = sec.copy()
    s["_m"] = pd.to_numeric(s["median_pct_60d"], errors="coerce")
    s = s.dropna(subset=["_m"]).sort_values("_m", ascending=False)
    if not len(s):
        return None

    def _row(r) -> dict:
        return {"industry": r.get("industry"),
                "n_recall": int(r["n_recall"]) if pd.notna(r.get("n_recall")) else None,
                "median_composite": _round(r.get("median_composite")),
                "median_pct_60d": _round(r.get("median_pct_60d")),
                "median_main_net_ratio": _round(r.get("median_main_net_ratio"), 4)}

    red = [_row(r) for _, r in s.head(n).iterrows()]
    black = [_row(r) for _, r in s.tail(n).iloc[::-1].iterrows()]
    return {"red": red, "black": black}


def market_pack(scan_dir: Path | str) -> dict:
    """从 L1_scored_full.csv(全市场真宽度)+ sectors.csv 聚合今日市场事实。零 LLM。

    只读 L1_scored_full(**不回退** L1_recall_top1000:composite 偏置子集会扭曲 breadth)。
    缺文件/缺列 → 对应字段 None,不抛。
    """
    scan_dir = Path(scan_dir)
    pack: dict = {"regime": None, "breadth": None, "valuation": None, "money": None, "sectors": None}
    src = scan_dir / "L1_scored_full.csv"
    if src.exists():
        df = pd.read_csv(src)
        if len(df):
            pack["regime"] = classify_regime(df).to_dict()
            pack["breadth"] = _breadth(df)
            pack["valuation"] = _valuation(df)
            pack["money"] = _money(df)
    sec = scan_dir / "sectors.csv"
    if sec.exists():
        pack["sectors"] = _sectors(pd.read_csv(sec))
    return pack
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --no-sync pytest tests/scan/test_market_pack.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add autoresearch/scan/market.py tests/scan/test_market_pack.py
git commit -m "feat(scan): market_pack 确定性市场事实聚合(regime/宽度/估值/板块红黑榜)"
```

---

### Task 2: `market_context_block()` — L3/L4 描述性地形块(防锚定)

**Files:**
- Modify: `autoresearch/scan/market.py`(追加函数)
- Test: `tests/scan/test_market_context_block.py`

**Interfaces:**
- Consumes: `market_pack()` 的 dict 结构(Task 1)。
- Produces: `market_context_block(pack: dict, industry: str | None = None) -> str`(markdown;只用 regime/breadth/valuation/money/sectors 段,**不含结果/操作/个股方向**)。

- [ ] **Step 1: Write the failing test**

```python
# tests/scan/test_market_context_block.py
from autoresearch.scan.market import market_context_block

_PACK = {
    "regime": {"label": "risk_off", "breadth": 0.27, "med_mom": -13.0, "n": 4000},
    "breadth": {"above_ma60": 0.27, "med_pct_60d": -13.0, "falling_knife": 0.42, "up_60d": 0.19},
    "valuation": {"med_pe": 34.0, "med_pb": 2.1, "pe_top_decile": 137.0, "pe_gt_60": 0.18},
    "money": {"main_pos": 0.28, "med_main_ratio": -0.01, "cmf_pos": 0.31},
    "sectors": {"red": [{"industry": "半导体", "median_pct_60d": 114.1}],
                "black": [{"industry": "汽车零部件", "median_pct_60d": -25.3}]},
}


def test_block_describes_regime_and_sectors():
    b = market_context_block(_PACK)
    assert "避险" in b and "半导体" in b and "汽车零部件" in b


def test_block_has_no_directives():
    b = market_context_block(_PACK)
    for bad in ("买入", "卖出", "仓位", "操作建议", "0 买"):
        assert bad not in b
    assert "个股评级只由本股 rubric 三门决定" in b       # 反锚定护栏文案


def test_block_sector_rank_when_industry_given():
    b = market_context_block(_PACK, industry="半导体")
    assert "本股所在板块" in b and "强势" in b


def test_empty_pack_safe():
    assert isinstance(market_context_block({}), str)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/scan/test_market_context_block.py -q`
Expected: FAIL — `ImportError: cannot import name 'market_context_block'`

- [ ] **Step 3: Write minimal implementation**

追加到 `autoresearch/scan/market.py`:

```python
def _pct(x) -> str:
    return f"{x:.0%}" if isinstance(x, (int, float)) and not isinstance(x, bool) else "—"


def _sign(x) -> str:
    return f"{x:+.1f}%" if isinstance(x, (int, float)) and not isinstance(x, bool) else "—"


def _sector_rank(industry: str, secs: dict) -> str:
    for r in secs.get("red", []):
        if r.get("industry") == industry:
            return f"{industry} 属**强势**端(中位60日动量 {_sign(r.get('median_pct_60d'))})"
    for r in secs.get("black", []):
        if r.get("industry") == industry:
            return f"{industry} 属**弱势**端(中位60日动量 {_sign(r.get('median_pct_60d'))})"
    return f"{industry}(非红黑榜极端)"


def market_context_block(pack: dict, industry: str | None = None) -> str:
    """L3/L4 注入的**描述性市场地形**块(防锚定:只陈述结构事实,无操作/个股方向指令)。"""
    reg = pack.get("regime") or {}
    br = pack.get("breadth") or {}
    val = pack.get("valuation") or {}
    mon = pack.get("money") or {}
    secs = pack.get("sectors") or {}
    zh = _REGIME_ZH.get(reg.get("label"), reg.get("label") or "—")
    lines = ["## 市场地形(背景校准 · 非选股指令)",
             f"- **regime**:{zh}(breadth {_pct(br.get('above_ma60'))}·中位60日动量 "
             f"{_sign(br.get('med_pct_60d'))}·落刀面 {_pct(br.get('falling_knife'))})",
             f"- **估值分散**:中位 PE {val.get('med_pe')}·上十分位 PE {val.get('pe_top_decile')}"
             f"(贵端 PE>60 占比 {_pct(val.get('pe_gt_60'))})",
             f"- **资金**:主力净流入为正占比 {_pct(mon.get('main_pos'))}·CMF>0 占比 {_pct(mon.get('cmf_pos'))}"]
    if secs.get("red"):
        lines.append("- **强势板块**:" + "、".join(
            f"{r['industry']}({_sign(r.get('median_pct_60d'))})" for r in secs["red"][:3]))
    if secs.get("black"):
        lines.append("- **弱势板块**:" + "、".join(
            f"{r['industry']}({_sign(r.get('median_pct_60d'))})" for r in secs["black"][:3]))
    if industry and secs:
        lines.append(f"- **本股所在板块**:{_sector_rank(industry, secs)}")
    lines.append("- 用途:据此校准估值/资金门严格度;**个股评级只由本股 rubric 三门决定,"
                 "大盘看空不压个股、看多不松门**。")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --no-sync pytest tests/scan/test_market_context_block.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add autoresearch/scan/market.py tests/scan/test_market_context_block.py
git commit -m "feat(scan): market_context_block 描述性地形块(L3/L4 注入·防锚定护栏)"
```

---

### Task 3: `render_fallback_pulse()` + `render_funnel_readout()` — L5 渲染件

**Files:**
- Modify: `autoresearch/scan/market.py`(追加两函数 + `_names` helper)
- Test: `tests/scan/test_market_renderers.py`

**Interfaces:**
- Consumes: `market_pack()`(Task 1);`autoresearch.scan.agents.l4_card.parse_ratings_from_details`;`autoresearch.scan.assemble._load_verify` / `_apply_verify_downgrade`(**函数级 lazy import**,避免 cycle)。
- Produces: `render_fallback_pulse(pack: dict) -> str`;`render_funnel_readout(scan_dir: Path | str) -> str`。

- [ ] **Step 1: Write the failing test**

```python
# tests/scan/test_market_renderers.py
import pandas as pd
from autoresearch.scan.market import render_fallback_pulse, render_funnel_readout


def test_fallback_pulse_from_pack():
    pack = {"regime": {"label": "risk_off"},
            "breadth": {"above_ma60": 0.27, "med_pct_60d": -13.0, "falling_knife": 0.42},
            "sectors": {"red": [{"industry": "半导体"}], "black": [{"industry": "软件开发"}]}}
    s = render_fallback_pulse(pack)
    assert "避险" in s and "半导体" in s


def test_fallback_pulse_empty_regime():
    assert render_fallback_pulse({"regime": None}) == ""


def _mk_details(tmp_path, cards, verify_rows=None, finalists=None, l1=None):
    d = tmp_path / "s"
    (d / "details").mkdir(parents=True)
    for code, rating in cards.items():
        (d / "details" / f"{code}.md").write_text(f"## 卡\n**Rating**: {rating}\n", encoding="utf-8")
    if finalists:
        pd.DataFrame(finalists).to_csv(d / "finalists.csv", index=False)
    if verify_rows is not None:
        pd.DataFrame(verify_rows).to_csv(d / "verify.csv", index=False)
    if l1 is not None:
        pd.DataFrame(l1).to_csv(d / "L1_scored_full.csv", index=False)
    return d


def test_funnel_readout_zero_buy(tmp_path):
    d = _mk_details(tmp_path, {"000001": "Hold", "000002": "Underweight"},
                    l1=[{"code": "000001", "above_ma60": 0.0, "pct_60d": -25.0}] * 4)
    s = render_funnel_readout(d)
    assert "0 买" in s and "避险" in s


def test_funnel_readout_with_buys(tmp_path):
    d = _mk_details(tmp_path, {"000001": "Overweight", "000002": "Hold"},
                    finalists=[{"code": "000001", "name": "测试股"}])
    s = render_funnel_readout(d)
    assert "1 买" in s and "测试股" in s


def test_funnel_readout_verify_downgrade(tmp_path):
    d = _mk_details(tmp_path, {"000001": "Overweight"},
                    verify_rows=[{"code": "000001", "verdict": "降级", "bull": "", "bear": "PB高",
                                  "trigger": "中报", "consensus": ""}],
                    finalists=[{"code": "000001", "name": "胜宏"}])
    s = render_funnel_readout(d)
    assert "0 买" in s and "观察单" in s     # OW 被 skeptic 降级 → 剔出买单、进观察单
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/scan/test_market_renderers.py -q`
Expected: FAIL — `ImportError: cannot import name 'render_fallback_pulse'`

- [ ] **Step 3: Write minimal implementation**

追加到 `autoresearch/scan/market.py`:

```python
def render_fallback_pulse(pack: dict) -> str:
    """market_view.md 缺失时的确定性市场脉搏(2–3 行);无 regime → 空串。"""
    reg = pack.get("regime") or {}
    if not reg:
        return ""
    br = pack.get("breadth") or {}
    secs = pack.get("sectors") or {}
    zh = _REGIME_ZH.get(reg.get("label"), reg.get("label") or "—")
    lines = [f"**市场脉搏(确定性回退)**:{zh} regime — breadth {_pct(br.get('above_ma60'))}·"
             f"中位60日动量 {_sign(br.get('med_pct_60d'))}·落刀面 {_pct(br.get('falling_knife'))}。"]
    if secs.get("red") and secs.get("black"):
        red = "、".join(r["industry"] for r in secs["red"][:3])
        black = "、".join(r["industry"] for r in secs["black"][:3])
        lines.append(f"强势:{red};弱势:{black}。")
    lines.append("_(未生成首席策略师研判 market_view.md → 回退确定性脉搏)_")
    return "\n".join(lines) + "\n"


def _names(scan_dir: Path, codes) -> str:
    """code → 名称(读 finalists.csv);缺 → code。"""
    f = Path(scan_dir) / "finalists.csv"
    m: dict = {}
    if f.exists():
        fdf = pd.read_csv(f, dtype={"code": str})
        for _, r in fdf.iterrows():
            m[str(r["code"]).zfill(6)] = r.get("name")
    return "、".join(f"{m.get(str(c).zfill(6)) or c}({str(c).zfill(6)})" for c in codes)


def render_funnel_readout(scan_dir: Path | str) -> str:
    """L5 确定性漏斗读数尾注:今日买单(≥OW,含 verify 折回)/ 观察单(skeptic 降级)。

    无决策卡 → 空串。verify 折回口径复用 assemble(降级=降一档、否决=至少 Hold)。
    """
    from autoresearch.scan.agents.l4_card import parse_ratings_from_details   # lazy:避免 import cycle
    from autoresearch.scan.assemble import _apply_verify_downgrade, _load_verify

    scan_dir = Path(scan_dir)
    ratings = parse_ratings_from_details(scan_dir / "details")
    if not ratings:
        return ""
    vmap = _load_verify(scan_dir)
    final: dict = {}
    for code, r in ratings.items():
        v = vmap.get(str(code).zfill(6))
        final[code] = (_apply_verify_downgrade(r, v["verdict"])
                       if v and v["verdict"] in ("降级", "否决") else r)
    buys = [c for c, r in final.items() if r in ("Buy", "Overweight")]
    lines = ["", "### 📉 今日漏斗读数"]
    if buys:
        lines.append(f"- **{len(buys)} 买**(≥OW):{_names(scan_dir, buys)}")
    else:
        reg = (market_pack(scan_dir).get("regime") or {}).get("label")
        zh = _REGIME_ZH.get(reg, reg or "")
        lines.append(f"- **0 买**:{len(final)} 只 finalist 深核后无一过 ≥OW 三门 —— "
                     f"{zh}regime 下的纪律空仓观望,非漏斗故障。")
    downgraded = [c for c, v in vmap.items() if v["verdict"] == "降级"]
    if downgraded:
        lines.append(f"- **观察单**:{_names(scan_dir, downgraded)}(skeptic 降级,待触发复核)")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --no-sync pytest tests/scan/test_market_renderers.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add autoresearch/scan/market.py tests/scan/test_market_renderers.py
git commit -m "feat(scan): L5 渲染件 render_fallback_pulse + render_funnel_readout(漏斗读数尾注)"
```

---

### Task 4: `assemble.py` — 嵌入 `market_view.md` + 回退脉搏

**Files:**
- Modify: `autoresearch/scan/assemble.py`(新 `_load_market_view` + `build_summary` 插入市场节)
- Test: `tests/scan/test_market_view_embed.py`

**Interfaces:**
- Consumes: `market.market_pack / render_fallback_pulse / render_funnel_readout`(**函数级 lazy import**)。
- Produces: summary 顶部(regime 行后、`## 1. 漏斗` 前)的市场节。

- [ ] **Step 1: Write the failing test**

```python
# tests/scan/test_market_view_embed.py
import pandas as pd
from autoresearch.scan.assemble import build_summary


def _min_scan(tmp_path, with_view):
    d = tmp_path / "s"
    d.mkdir()
    (d / "meta.json").write_text("{}", encoding="utf-8")
    (d / "finalists.csv").write_text("code,name,sector\n", encoding="utf-8")
    if with_view:
        (d / "market_view.md").write_text("## 定调\n避险哑铃,半导体拥挤。\n", encoding="utf-8")
    return d


def test_embed_when_view_present(tmp_path):
    md = build_summary(_min_scan(tmp_path, True), "2026-06-30", "2314", "20260630_2314")
    assert "## 📈 今日 A 股市场(首席策略师视角)" in md
    assert "避险哑铃" in md


def test_fallback_when_view_absent_and_market_data(tmp_path):
    d = _min_scan(tmp_path, False)
    pd.DataFrame([{"code": "1", "above_ma60": 0.0, "pct_60d": -25.0}] * 5).to_csv(
        d / "L1_scored_full.csv", index=False)
    md = build_summary(d, "2026-06-30", "2314", "20260630_2314")
    assert "市场脉搏(确定性回退)" in md


def test_no_market_section_when_no_data(tmp_path):
    md = build_summary(_min_scan(tmp_path, False), "2026-06-30", "2314", "20260630_2314")
    assert "今日 A 股市场" not in md          # 老路:无市场数据不加节
    assert "## 1. 漏斗(数量)" in md          # 其余照旧
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/scan/test_market_view_embed.py -q`
Expected: FAIL — `test_embed_when_view_present` 断言 `## 📈 今日 A 股市场` 不在输出。

- [ ] **Step 3: Write minimal implementation**

在 `autoresearch/scan/assemble.py` 加 `_load_market_view`(放 `regime_and_drift` 上方即可):

```python
def _load_market_view(scan_dir: Path) -> str:
    """读 L2 后策略师写的 market_view.md staging(缺 → '')。assemble 仍零-LLM(只读文件)。"""
    p = scan_dir / "market_view.md"
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""
```

在 `build_summary` 里,把现有:

```python
    if regime_line:
        out.append(regime_line + "\n")

    # ── 1. 漏斗数量 ──
```

改成(在两者之间插入市场节):

```python
    if regime_line:
        out.append(regime_line + "\n")

    # ── 市场研判(首席策略师视角;策略师未写则回退确定性脉搏)──
    mv = _load_market_view(scan_dir)
    if mv:
        from autoresearch.scan.market import render_funnel_readout   # lazy:避免 import cycle
        out += ["## 📈 今日 A 股市场(首席策略师视角)\n", mv, ""]
        readout = render_funnel_readout(scan_dir)
        if readout:
            out += [readout]
    else:
        from autoresearch.scan.market import market_pack, render_fallback_pulse
        pulse = render_fallback_pulse(market_pack(scan_dir))
        if pulse:
            out += ["## 📈 今日 A 股市场\n", pulse, ""]

    # ── 1. 漏斗数量 ──
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --no-sync pytest tests/scan/test_market_view_embed.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Run existing assemble tests (parity)**

Run: `uv run --no-sync pytest tests/scan/test_assemble.py -q`
Expected: PASS —— 现有 fixture 不含 `L1_scored_full.csv` 的宽度列时 `market_pack` 返回 regime=None → 不加市场节 → 逐字节不变。**若某 fixture 触发了脉搏**(含 L1 宽度数据),说明是新特性生效:更新该断言接受市场节(而非回滚特性)。

- [ ] **Step 6: Commit**

```bash
git add autoresearch/scan/assemble.py tests/scan/test_market_view_embed.py
git commit -m "feat(scan): L5 summary 嵌入 market_view 研判 + 漏斗读数尾注 + 回退脉搏"
```

---

### Task 5: `l4_card.compose_funnel_brief` — 注入市场地形块

**Files:**
- Modify: `autoresearch/scan/agents/l4_card.py:59-106`(`compose_funnel_brief` 返回前 prepend 地形块)
- Test: `tests/scan/test_l4_brief_market_ctx.py`
- Verify: `tests/scan/test_agents.py`(现有 brief 测试)

**Interfaces:**
- Consumes: `market.market_pack / market_context_block`(**函数级 lazy import**)。
- Produces: `compose_funnel_brief` 输出头部多一段 `## 市场地形...`(有市场数据时);无数据 → 与旧输出逐字节一致。

- [ ] **Step 1: Write the failing test**

```python
# tests/scan/test_l4_brief_market_ctx.py
import pandas as pd
from autoresearch.scan.agents.l4_card import compose_funnel_brief


def _scan(tmp_path, with_market):
    d = tmp_path / "s"
    d.mkdir()
    pd.DataFrame([{"code": "000001", "name": "测试股", "industry": "半导体", "composite": 50,
                   "n_channels": 3, "recall_channels": "momentum", "pct_60d": 100.0, "pe": 120.0}]
                 ).to_csv(d / "L1_recall_top1000.csv", index=False)
    if with_market:
        pd.DataFrame([{"code": "000001", "above_ma60": 1.0, "pct_60d": 100.0, "pe": 120.0}] * 5
                     ).to_csv(d / "L1_scored_full.csv", index=False)
        pd.DataFrame([{"industry": "半导体", "n_recall": 49, "median_composite": 26.6,
                       "median_pct_60d": 114.1, "median_main_net_ratio": -0.002, "is_top": True}]
                     ).to_csv(d / "sectors.csv", index=False)
    return d


def test_brief_has_market_terrain_when_data(tmp_path):
    b = compose_funnel_brief("000001", _scan(tmp_path, True))
    assert "市场地形" in b and "本股所在板块" in b


def test_brief_unchanged_without_market_data(tmp_path):
    b = compose_funnel_brief("000001", _scan(tmp_path, False))   # 只有 L1_recall,无 L1_scored_full
    assert "市场地形" not in b
    assert "漏斗简报" in b                                        # 老 brief 照旧
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/scan/test_l4_brief_market_ctx.py -q`
Expected: FAIL — `test_brief_has_market_terrain_when_data` 找不到 "市场地形"。

- [ ] **Step 3: Write minimal implementation**

在 `autoresearch/scan/agents/l4_card.py` 加 helper(放 `compose_funnel_brief` 上方):

```python
def _market_ctx(scan_dir, industry) -> str:
    """本股所在市场地形块(有 L1_scored_full 才注入;失败静默降级空串)。lazy import 避免 cycle。"""
    try:
        from autoresearch.scan.market import market_context_block, market_pack
        pack = market_pack(scan_dir)
        if not pack.get("regime"):
            return ""
        return market_context_block(pack, industry=industry)
    except Exception:   # noqa: BLE001 —— 市场层可选,缺了不挡简报
        return ""
```

把 `compose_funnel_brief` 结尾:

```python
    return "\n".join(lines) + "\n"
```

改成:

```python
    brief = "\n".join(lines) + "\n"
    ctx = _market_ctx(base, l3.get("industry") or l3.get("sector") or l1.get("industry"))
    return (ctx + "\n" + brief) if ctx else brief
```

(`base` = 函数内已有的 `Path(scan_dir)`;`l1`/`l3` = 已解析的行 dict。)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --no-sync pytest tests/scan/test_l4_brief_market_ctx.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Run existing agents tests (parity)**

Run: `uv run --no-sync pytest tests/scan/test_agents.py -q`
Expected: PASS —— 现有 `compose_funnel_brief` fixture 若只含 `L1_recall_top1000`(无 `L1_scored_full`)→ `market_pack` regime=None → 无前缀 → 逐字节不变。**若 fixture 含 `L1_scored_full` 宽度数据**触发前缀:更新该断言接受地形前缀(新特性)。

- [ ] **Step 6: Commit**

```bash
git add autoresearch/scan/agents/l4_card.py tests/scan/test_l4_brief_market_ctx.py
git commit -m "feat(scan): L4 简报注入市场地形块(regime 校准·防锚定护栏)"
```

---

### Task 6: skill 文档 — 策略师步骤 + prompt 模板 + 注入指令

**Files:**
- Modify: `.claude/skills/scan-market/SKILL.md`(流程表 + 买单 skeptic 与 L5 之间加"首席策略师"步骤)
- Modify: `.claude/skills/scan-market/screening-playbook.md`(策略师 prompt 模板 + L3/L4 注入指令)

**Interfaces:**
- Consumes: `market.market_pack`(策略师读的数据包)、`market_view.md` 契约(Task 4 嵌入端)。
- Produces: 人读的编排指令(无代码测试)。

- [ ] **Step 1: SKILL.md —— 流程表加策略师行**

在 SKILL.md 的六段表(L4 行之后、L5 行之前)插:

```markdown
| **首席策略师** | 市场研判 | **Opus·单 agent** | L2 后读 `market_pack` 写 `market_view.md`(定调/结构/红黑榜/操作基调);地形段喂 L3/L4 校准、全文进 L5 | 1 份 | 小 |
```

- [ ] **Step 2: SKILL.md —— 流程步骤加策略师段**

在"## 流程(6 段)"里,L2(step 1)之后、L3(step 3)之前,插入:

```markdown
2.5. **首席策略师市场研判(L2 后,L3 前)**:`python -m autoresearch.scan.market` 数据已就绪(`market_pack(scan_dir)`)→ 派**一个 `Agent(model='opus')`** 以资深 A 股投资大师口吻读数据包写 `context/scan/<date>/market_view.md`(模板见 `screening-playbook.md`)。**地形段(regime/红黑榜/估值分散)前置进 L3 prompt + 每张 L4 卡简报**(`compose_funnel_brief` 自动注入);**操作基调/漏斗读数只进 L5**。铁律:数字出自 `market_pack`、个股评级只由 rubric 三门定(大盘不锚个股)。
```

- [ ] **Step 3: screening-playbook.md —— 加策略师 prompt 模板**

在 playbook 末尾追加:

```markdown
## 首席策略师市场研判 prompt 模板(L2 后)

> 你是一名资深 A 股投资大师 / 首席策略师。以下是今日全市场确定性数据包(`market_pack`,数字不可编造)。
> 写一段 ~300–400 字的市场研判 `market_view.md`,6 小节:
> 1. **一句话定调**(regime + 结构 + 情绪,如"避险哑铃");
> 2. **市场结构**(宽度 / 主力资金 / 估值分散哑铃两端);
> 3. **板块红黑榜**(强 top3 / 弱 bottom3,各一句 why);
> 4. **操作基调**(基于 regime 的整体仓位姿态;规范性,仅 L5 用);
> 5. **关注**(催化日历:中报窗口/政策会议/解禁);
> 6. 收尾"仅供研究,非投资建议"。
> 前 3 节是**描述性地形**(会喂 L3/L4 做校准,不得含个股买卖指令);第 4–5 节是**规范性 + 前瞻**(仅进 L5 报告)。
> 数据包:<粘贴 market_pack(scan_dir) 的 JSON>
```

- [ ] **Step 4: screening-playbook.md —— 加 L3 注入指令**

在 L3 holistic prompt 模板处补一行:

```markdown
- **市场地形前置**:L3 prompt 顶部先贴 `market_view.md` 的地形段(或 `market_context_block(market_pack(scan_dir))`),让 holistic 通看时按 regime 加权资金确认/避落刀。**只作校准,选股仍由 5 维 rubric 定。**
```

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/scan-market/SKILL.md .claude/skills/scan-market/screening-playbook.md
git commit -m "docs(scan): SKILL/playbook 加首席策略师步骤 + prompt 模板 + L3/L4 注入指令"
```

---

### Task 7: 全量回归 + spec 勾稽

**Files:** 无新增(验证)。

- [ ] **Step 1: 跑全 scan 测试**

Run: `uv run --no-sync pytest tests/scan/ tests/common/ -q`
Expected: PASS（含新 4 个测试文件 + 现有 test_assemble/test_agents 全绿)。

- [ ] **Step 2: ruff**

Run: `uv run --no-sync ruff check autoresearch/scan/market.py autoresearch/scan/assemble.py autoresearch/scan/agents/l4_card.py tests/scan/test_market_pack.py tests/scan/test_market_context_block.py tests/scan/test_market_renderers.py tests/scan/test_market_view_embed.py tests/scan/test_l4_brief_market_ctx.py`
Expected: All checks passed（如有 import 排序/行宽 → `ruff check --fix` 后重跑）。

- [ ] **Step 3: 勾稽 spec 覆盖**

对照 `docs/specs/2026-07-01-scan-market-strategist-view-design.md` §3 组件逐条打勾:`market_pack`(T1)、`market_context_block`(T2)、`render_funnel_readout`+`render_fallback_pulse`(T3)、L5 嵌入(T4)、L4 注入(T5)、策略师步骤/prompt(T6)。§1 约束(零-LLM/防锚定/产出分层/parity)由各 Task 的 parity step + `test_block_has_no_directives` 覆盖。

- [ ] **Step 4: Commit(若 ruff 有 fix)**

```bash
git add -A && git commit -m "chore(scan): 市场研判特性 ruff + 回归收尾"
```

## Notes

- **spec 偏差(有意)**:spec §3.1 曾把 `funnel/buylist/watchlist` 列进 `market_pack`;实现改由独立的 `render_funnel_readout(scan_dir)` 直接从 details/verify 算(避免 pack 双模、职责更清)。`market_pack` 收敛为纯市场结构。
- **策略师 subagent 不写 Python**:它是 skill 编排的一个 `Agent(model='opus')` 步骤(prompt 在 playbook);本 plan 只落它读的数据包(`market_pack`)+ 它写的契约文件(`market_view.md`)+ 三处消费端。
- **import cycle 规避**:`market↔assemble`、`market↔l4_card` 全部函数级 import;模块加载期无环。
