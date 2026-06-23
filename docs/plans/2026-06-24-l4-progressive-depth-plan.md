# L4 渐进深度 + 单 Opus subagent 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 scan-market L4 从「Tier-1 Sonnet 全判 → Tier-2 Opus 平反 → Tier-3 Opus 辩论」三层级联,改为「一只 finalist = 一个 Opus subagent」在 `analyze-ticker-lite` 内做渐进深度 DD + 早停。

**Architecture:** 删三层选择器(`batch_finalists`/`pick_downgrade_reviews`),加确定性漏斗简报组装(`compose_funnel_brief`,P0 定向),harvest slim 块重排成「表面前/深核后 + P4 分界标记」(支持渐进读盘),重写 `analyze-ticker-lite` playbook 为分阶段 DD(P0–P5)+ 早停点(②P3主/③P4击杀/①默认OFF),重写 scan L4 编排文档,assemble 改标签。代码层是 4 个确定性单元(全可离线测);skill/playbook 是文档重写;subagent 由本 session Claude 扮演(零付费 LLM API)。

**Tech Stack:** Python 3.13 / pandas / pytest;`uv run --no-sync python -m pytest`;`ruff`。

## Global Constraints

(每个 task 隐含遵守,逐字摘自 spec)

- **零付费 LLM API**:subagent = 本 session Claude;数据层走免费工具。
- **确定性层零 LLM**:漏斗简报组装、评级解析、买单名单、slim 重排、发布全是 pandas/确定性。
- **数字 grounded**:卡里数字回溯到漏斗简报的 L1/L2/L3 真值 或 已读 slim 块;未读块(尤其陷阱维)不引数字、不编;早停卡把未核维明写「未核·需深挖」。
- **早停只向下**:早停只能 ≤Hold;任何 Rating ≥ Overweight 必须走完 P4 + P5,绝不在早停点发买单(安全地板,结构性)。
- **防误杀铁律**:永远不在读到「翻盘牌」(催化/forward 估值/吸筹)之前早停 ⇒ 最早安全主早停 = P3 之后。
- **单测零网络**:确定性 helper 喂合成 fixture;不在单测起 subagent / 联网。
- **发布可解析**:每张卡含 `**Rating**`(五档)+ `FINAL TRANSACTION PROPOSAL`。

## 文件结构

| 文件 | 责任 | 改动 |
|---|---|---|
| `autoresearch/scan/agents/l4_card.py` | L4 确定性 helper | 删 `batch_finalists`/`pick_downgrade_reviews`;改 `pick_buy_candidates` docstring;加 `compose_funnel_brief` |
| `autoresearch/analyze/harvest.py` | slim/full 取数 | 加 `_reorder_slim_for_progressive` + slim 路径调用 |
| `tests/scan/test_agents.py` | l4_card 回归 | 删两函数测试;加 `compose_funnel_brief` 测试 |
| `tests/analyze/test_harvest_slim_order.py` | slim 重排回归(新) | 新建 |
| `.claude/skills/analyze-ticker-lite/lite-playbook.md` | 决策卡模板 | 重写为阶段流 + 早停 + 两卡模板 |
| `.claude/skills/analyze-ticker-lite/SKILL.md` | lite 流程 | 流程同步 |
| `.claude/skills/scan-market/SKILL.md` | scan 编排 | L4 节重写 |
| `.claude/skills/scan-market/screening-playbook.md` | scan 操作参考 | L4 节(行 78–120)重写 |
| `autoresearch/scan/assemble.py` | L5 发布 | `_funnel_rows`/`_stage_token_estimate` 标签改 |

---

### Task 1: `compose_funnel_brief`(P0 漏斗简报组装)

**Files:**
- Modify: `autoresearch/scan/agents/l4_card.py`(在 `pick_buylist` 后、rubric 段前加新函数 + 顶部 `from pathlib import Path` 已有)
- Test: `tests/scan/test_agents.py`

**Interfaces:**
- Produces: `compose_funnel_brief(code: str, scan_dir: Path | str) -> str` —— 读 `<scan_dir>/{L1_recall_top1000,L2_gbdt_top200,finalists}.csv` 该 code 行 → 紧凑 markdown 简报;缺产物/列降级占位(`—`),不抛。

- [ ] **Step 1: Write the failing test**

在 `tests/scan/test_agents.py` 末尾加:

```python
# ───────────────────────── L4 · P0:漏斗简报 ─────────────────────────


def _make_funnel_dir(tmp_path):
    """造 L1_recall / L2_gbdt / finalists 各 1 行(神火 000933)。"""
    d = tmp_path / "context/scan/2026-06-24"
    d.mkdir(parents=True)
    pd.DataFrame([{"code": "000933", "name": "神火股份", "industry": "工业金属",
                   "composite": 66.6, "n_channels": 3, "recall_channels": "共振|价值|成长",
                   "best_rank": 43, "score_momentum": 50, "score_fund_main": 60,
                   "score_growth": 70, "score_value": 80, "score_volprice": 40,
                   "score_chip": 55, "score_north": 0, "score_tech": 45,
                   "np_yoy": 223.0, "rev_yoy": 10.0, "roe": 17.3, "pe": 9.3, "pb": 1.2,
                   "dv_ratio": 3.19, "main_net_ratio": 0.87, "cmf_20": 0.1, "obv_mom_20": 0.2,
                   "rsi6": 55, "ma_bull": 1, "pct_60d": 12.0, "winner_rate": 1.1,
                   "chip_concentration": 0.3, "price_to_cost": 1.05, "hk_ratio": 0.0}],
                 ).to_csv(d / "L1_recall_top1000.csv", index=False)
    pd.DataFrame([{"code": "000933", "l2_rank": 132, "gbdt_score": 0.54}],
                 ).to_csv(d / "L2_gbdt_top200.csv", index=False)
    pd.DataFrame([{"ticker": "000933", "code": "000933", "name": "神火股份", "sector": "工业金属",
                   "lenses": "共振3路", "conviction": 90, "triage_lean": "看多",
                   "thesis": "3路共振·PE9.3低估·np+223", "risk": "煤铝价周期下行盈利回吐",
                   "catalyst": "无明确催化", "lane": "trend", "sentiment": "中性"}],
                 ).to_csv(d / "finalists.csv", index=False)
    return d


def test_compose_funnel_brief_has_channels_factors_thesis(tmp_path):
    brief = compose_funnel_brief("000933", _make_funnel_dir(tmp_path))
    assert "神火股份" in brief
    assert "命中 3 路" in brief                 # n_channels
    assert "conviction 90" in brief             # L3
    assert "3路共振·PE9.3低估" in brief          # L3 thesis
    assert "np_yoy 223" in brief                # L1 先验因子
    assert "gbdt_score 0.54" in brief           # L2


def test_compose_funnel_brief_degrades_missing_finalists(tmp_path):
    d = _make_funnel_dir(tmp_path)
    (d / "finalists.csv").unlink()
    brief = compose_funnel_brief("000933", d)   # 无 L3 → 不抛,仍出 L1 先验
    assert "神火股份" in brief and "np_yoy 223" in brief
```

并在文件顶部 import 块加入 `compose_funnel_brief`:

```python
from autoresearch.scan.agents.l4_card import (
    compose_funnel_brief,
    parse_ratings_from_details,
    pick_buy_candidates,
    pick_buylist,
    rubric_rating,
)
```

(注意:**删掉** `batch_finalists` 和 `pick_downgrade_reviews` 这两个 import —— Task 2 才删函数,但本步先改 import 会让 Task 1 测试因 ImportError 失败;故本步 import 只**加** `compose_funnel_brief`,**保留** `batch_finalists`/`pick_downgrade_reviews` 不动,留给 Task 2 删。)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync python -m pytest tests/scan/test_agents.py::test_compose_funnel_brief_has_channels_factors_thesis -q`
Expected: FAIL — `ImportError: cannot import name 'compose_funnel_brief'`

- [ ] **Step 3: Write minimal implementation**

在 `autoresearch/scan/agents/l4_card.py` 的 `pick_buylist` 函数之后、`# ── L4 · C:评级评分卡` 注释之前,插入:

```python
def compose_funnel_brief(code: str, scan_dir: Path | str) -> str:
    """L4 **P0 定向**:从漏斗产物(L1_recall/L2/finalists)拼该票紧凑简报 markdown。

    **只定向 + 给评分卡先验,不作早停依据**(信息薄,据此判=误杀)。subagent 据此知道
    「该重点核哪条」,判定来自 P1–P5 读到的 slim 真数据。缺产物/列降级占位(`—`),不抛。
    """
    base = Path(scan_dir)
    code6 = str(code).split(".")[0].zfill(6)

    def _row(fname: str) -> dict:
        p = base / fname
        if not p.exists():
            return {}
        df = pd.read_csv(p, dtype={"code": str})
        if "code" not in df.columns:
            return {}
        df["code"] = df["code"].astype(str).str.zfill(6)
        sub = df[df["code"] == code6]
        return sub.iloc[0].to_dict() if len(sub) else {}

    l1, l2, l3 = _row("L1_recall_top1000.csv"), _row("L2_gbdt_top200.csv"), _row("finalists.csv")

    def _g(d: dict, k: str, dflt: str = "—"):
        v = d.get(k, dflt)
        return dflt if v is None or (isinstance(v, float) and v != v) else v

    name = _g(l3, "name") if l3 else _g(l1, "name")
    lines = [
        f"## 漏斗简报 — {code6} {name}(L1/L2/L3 评价·定向用,**判定须读下方真数据**)",
        "",
        f"- **L1 召回**:命中 {_g(l1,'n_channels')} 路({_g(l1,'recall_channels')})｜"
        f"best_rank {_g(l1,'best_rank')}｜composite {_g(l1,'composite')}",
        f"- **L1 子分**:动量{_g(l1,'score_momentum')}·主力{_g(l1,'score_fund_main')}·"
        f"成长{_g(l1,'score_growth')}·价值{_g(l1,'score_value')}·量价{_g(l1,'score_volprice')}·"
        f"筹码{_g(l1,'score_chip')}·北向{_g(l1,'score_north')}·技术{_g(l1,'score_tech')}",
        f"- **基本面(先验)**:np_yoy {_g(l1,'np_yoy')}·rev_yoy {_g(l1,'rev_yoy')}·roe {_g(l1,'roe')}",
        f"- **估值(先验)**:pe {_g(l1,'pe')}·pb {_g(l1,'pb')}·股息 {_g(l1,'dv_ratio')}",
        f"- **资金/技术(先验)**:主力净占比 {_g(l1,'main_net_ratio')}·cmf20 {_g(l1,'cmf_20')}·"
        f"obv20 {_g(l1,'obv_mom_20')}·rsi6 {_g(l1,'rsi6')}·多头排列 {_g(l1,'ma_bull')}·pct60d {_g(l1,'pct_60d')}",
        f"- **筹码(先验)**:winner {_g(l1,'winner_rate')}·集中度 {_g(l1,'chip_concentration')}·"
        f"现价/成本 {_g(l1,'price_to_cost')}·北向占比 {_g(l1,'hk_ratio')}",
        f"- **L2**:gbdt_score {_g(l2,'gbdt_score')}(rank {_g(l2,'l2_rank')})",
        f"- **L3 入选**:conviction {_g(l3,'conviction')}·lane {_g(l3,'lane')}·情感 {_g(l3,'sentiment')}",
        f"  - 多头论点:{_g(l3,'thesis')}",
        f"  - 最大风险:{_g(l3,'risk')}",
        f"  - 催化:{_g(l3,'catalyst')}",
    ]
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --no-sync python -m pytest tests/scan/test_agents.py -k compose_funnel_brief -q`
Expected: PASS(2 passed)

- [ ] **Step 5: Commit**

```bash
git add autoresearch/scan/agents/l4_card.py tests/scan/test_agents.py
git commit -m "feat(scan-L4): compose_funnel_brief — P0 漏斗简报定向(L1/L2/L3 评价拼简报)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: 删 `batch_finalists` / `pick_downgrade_reviews` + `pick_buy_candidates` 改语义

**Files:**
- Modify: `autoresearch/scan/agents/l4_card.py:20-28`(删 `batch_finalists`)、`:67-85`(删 `pick_downgrade_reviews`)、`:47-52`(改 `pick_buy_candidates` docstring)
- Test: `tests/scan/test_agents.py`(删两测试 + 改 import + 改 docstring 注释)

**Interfaces:**
- Consumes: `pick_buy_candidates(ratings, include=("Buy","Overweight"))`(签名不变)
- Produces: 模块不再导出 `batch_finalists`/`pick_downgrade_reviews`;`pick_buy_candidates` 语义 = 「最终 ≥OW 买单 skeptic 名单」。

- [ ] **Step 1: Update the tests first (red via removal)**

在 `tests/scan/test_agents.py`:

(a) import 块删掉 `batch_finalists`、`pick_downgrade_reviews`(此刻应剩):

```python
from autoresearch.scan.agents.l4_card import (
    compose_funnel_brief,
    parse_ratings_from_details,
    pick_buy_candidates,
    pick_buylist,
    rubric_rating,
)
```

(b) 删整段 `test_batch_finalists_30_to_10_batches`(行 107–110)。
(c) 删整段 `test_pick_downgrade_reviews_conditional`(行 132–142)。
(d) 把 `test_pick_buy_candidates_and_buylist` 的注释/语义更新为「买单 skeptic 名单」(断言不变,仍 Buy/OW):

```python
def test_pick_buy_candidates_is_buy_skeptic_list(tmp_path):
    """pick_buy_candidates(ratings) = 最终 ≥OW 买单 → 独立 skeptic 名单(语义改;集合不变)。"""
    got = {"000001": "Buy", "000002": "Overweight", "000003": "Hold",
           "000004": "Underweight", "000005": "Sell"}
    assert set(pick_buy_candidates(got)) == {"000001", "000002"}
    assert set(pick_buylist(got, floor="Overweight")) == {"000001", "000002"}
    assert set(pick_buylist(got, floor="Buy")) == {"000001"}
```

(e) 顶部 docstring 第 6–7 行的「L4 batch_finalists(30→10 批)... / pick_downgrade_reviews 条件触发」覆盖说明删掉,改为:

```python
  - L4 compose_funnel_brief(P0 简报)/ parse_ratings_from_details / pick_buy_candidates(买单 skeptic 名单)/ pick_buylist
```

- [ ] **Step 2: Run to verify red**

Run: `uv run --no-sync python -m pytest tests/scan/test_agents.py -q`
Expected: FAIL — `ImportError`(import 已删 `batch_finalists`,但 `l4_card.py` 仍定义、其余引用消失)或收集错误。这是删除驱动的 red:测试已不引用旧函数。

- [ ] **Step 3: Delete the functions + fix docstring**

在 `autoresearch/scan/agents/l4_card.py`:

(a) 删整个 `def batch_finalists(...)`(含 docstring,行 20–28)。
(b) 删整个 `def pick_downgrade_reviews(...)`(含 docstring,行 67–85)。
(c) `pick_buy_candidates` docstring 改为:

```python
def pick_buy_candidates(ratings: dict[str, str],
                        include: tuple[str, ...] = ("Buy", "Overweight")) -> list[str]:
    """L4 **买单独立 skeptic 名单**:最终评级 ∈ include(Buy/OW)的发布买单,每只派一个
    独立 Opus skeptic 证伪(发布前红队)。早停只向下、买点必走 P4+P5 后才可能 ≥OW 到此。"""
    keep = set(include)
    return [c for c, r in ratings.items() if r in keep]
```

(d) 模块顶部 docstring 第 7–8 行提到 `batch_finalists`/`pick_downgrade_reviews` 的描述改为反映「单 Opus subagent 渐进深度 + compose_funnel_brief」。

- [ ] **Step 4: Run to verify green + no dangling refs**

```bash
uv run --no-sync python -m pytest tests/scan/test_agents.py -q
grep -rn "batch_finalists\|pick_downgrade_reviews" autoresearch/ tests/
```
Expected: pytest PASS;grep 在 `autoresearch/`+`tests/` **无输出**(skill 文档行 Task 6 处理)。

- [ ] **Step 5: Commit**

```bash
git add autoresearch/scan/agents/l4_card.py tests/scan/test_agents.py
git commit -m "refactor(scan-L4): 删 batch_finalists/pick_downgrade_reviews,pick_buy_candidates→买单 skeptic 名单

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: harvest slim 渐进读盘重排(表面前/深核后 + P4 标记)

**Files:**
- Modify: `autoresearch/analyze/harvest.py`(加 `_reorder_slim_for_progressive` + main() 写盘前调用)
- Test: `tests/analyze/test_harvest_slim_order.py`(新建)

**Interfaces:**
- Produces: `_reorder_slim_for_progressive(parts: list[str]) -> list[str]` —— 把深核块(标题含 `Income statement`/`Earnings quality`/`Solvency`)移到 `<!-- P4 深核分界 -->` 标记之后,表面块保序在前;无深核块则原样返回。纯函数。

- [ ] **Step 1: Write the failing test**

新建 `tests/analyze/test_harvest_slim_order.py`:

```python
"""slim 渐进读盘:表面块在前、深核块(P4)在后 + 分界标记。NO network(合成 parts)。"""
from __future__ import annotations

from autoresearch.analyze.harvest import _P4_MARKER, _reorder_slim_for_progressive


def _parts():
    return [
        "# Data context — 000933\n",                       # 头(表面)
        "\n## Verified market snapshot (source of truth)\n\n…\n",   # 表面
        "\n## Income statement (quarterly)\n\n…长表…\n",            # 深核
        "\n## 量价形态/吸筹·多日资金流 (UZI·复用L1)\n\n…\n",        # 表面
        "\n## Earnings quality / forensics (v3)\n\n…\n",           # 深核
        "\n## Solvency & refinancing (v4)\n\n…\n",                 # 深核
        "\n## A股卖方一致预期 EPS / fwd-PE (同花顺·keyless)\n\n…\n", # 表面(fwd PE)
    ]


def test_reorder_puts_deep_after_marker():
    out = _reorder_slim_for_progressive(_parts())
    joined = "".join(out)
    assert _P4_MARKER in joined
    mi = joined.index(_P4_MARKER)
    # 表面块在标记前
    assert joined.index("Verified market snapshot") < mi
    assert joined.index("量价形态") < mi
    assert joined.index("fwd-PE") < mi
    # 深核块在标记后
    assert joined.index("Income statement") > mi
    assert joined.index("Earnings quality") > mi
    assert joined.index("Solvency") > mi


def test_reorder_preserves_surface_order():
    out = _reorder_slim_for_progressive(_parts())
    joined = "".join(out)
    assert joined.index("market snapshot") < joined.index("量价形态") < joined.index("fwd-PE")


def test_reorder_noop_when_no_deep_blocks():
    surface = ["# head\n", "\n## Verified market snapshot\n\n…\n", "\n## Tradeability\n\n…\n"]
    assert _reorder_slim_for_progressive(surface) == surface       # 无深核 → 原样,不插标记
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync python -m pytest tests/analyze/test_harvest_slim_order.py -q`
Expected: FAIL — `ImportError: cannot import name '_P4_MARKER'`

- [ ] **Step 3: Write minimal implementation**

在 `autoresearch/analyze/harvest.py` 的 `def _section(...)`(行 840 附近)之后加:

```python
_P4_DEEP_TITLES = ("Income statement", "Earnings quality", "Solvency")
_P4_MARKER = ("\n<!-- P4 深核分界(早停在此之前 return;表面 DD〔P1–P3:快照/资金/量价/财报/估值/"
              "fwd PE/新闻/日历〕已在上方;以下为陷阱核 P4) -->\n")


def _reorder_slim_for_progressive(parts: list[str]) -> list[str]:
    """slim 渐进读盘:深核块(P4 陷阱维:利润表全表/盈利质量/偿付)移到 P4 分界标记之后,
    表面块保序在前。subagent 读到标记为止做 P1–P3,主早停②则不读标记后;survivor 才读。
    无深核块 → 原样返回(不插标记,老路不破)。纯函数,可离线测。"""
    def _is_deep(p: str) -> bool:
        head = p[:120]
        return any(f"## {t}" in head for t in _P4_DEEP_TITLES)

    deep = [p for p in parts if _is_deep(p)]
    if not deep:
        return parts
    surface = [p for p in parts if not _is_deep(p)]
    return surface + [_P4_MARKER] + deep
```

然后在 `main()` 写盘处(行 1190–1191),改为先重排:

```python
    out_path = out_dir / f"{ticker}_{trade_date}{'_slim' if slim else ''}.md"
    if slim:
        parts = _reorder_slim_for_progressive(parts)
    out_path.write_text("".join(parts), encoding="utf-8")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --no-sync python -m pytest tests/analyze/test_harvest_slim_order.py -q`
Expected: PASS(3 passed)

- [ ] **Step 5: Commit**

```bash
git add autoresearch/analyze/harvest.py tests/analyze/test_harvest_slim_order.py
git commit -m "feat(harvest): slim 渐进读盘块重排 — 表面前/深核后 + P4 分界标记

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: 重写 `analyze-ticker-lite/lite-playbook.md`(阶段流 + 早停 + 两卡模板)

**Files:**
- Modify: `.claude/skills/analyze-ticker-lite/lite-playbook.md`(全文重写,约束见下)

**Interfaces:** 无代码;产物是 subagent 读的 playbook。**两张卡模板都含 `**Rating**`(五档)+ `FINAL TRANSACTION PROPOSAL`**(`parse_rating`/`assemble` 依赖)。

- [ ] **Step 1: 重写 playbook**

保留原有「数据坑 + 五档评级 + UZI 增量块」说明,**新增/替换**为下面结构(完整 spec 见 `docs/specs/2026-06-24-l4-progressive-depth-design.md` §3/§5/§6):

1. **顶部加「渐进深度 + 早停」总则**:
   - P0 读漏斗简报(顶部)= **定向**,不作判据(信息薄=误杀);判定来自 P1–P5 读到的真数据。
   - **防误杀铁律**:不在读到翻盘牌(催化/forward PE/吸筹)前早停 ⇒ 主早停 = P3 后。
   - **早停只向下**:早停 ≤Hold;任何 ≥OW 必走 P4+P5。
   - **渐进读盘**:slim 顶部是简报 + 表面块,`<!-- P4 深核分界 -->` 后才是深核块;**读到分界为止做 P1–P3**,主早停②停笔则分界后不读,survivor 才读后半。

2. **阶段流(P0–P5)表**(逐字采 spec §3 的表:每阶段读什么/回答/填哪维)。

3. **早停点(spec §5)**:
   - **① P1 后**(极端狗票快速通道,**默认 OFF**:仅简报无催化 + P1 实读资金决绝派发或高位 + np_yoy 深负三者同时才停)。
   - **② P3 后(主早停,默认开)**:表面 4 维(技术资金/基本面/估值/催化)加不起买点 → 早停卡,跳 P4/P5。
   - **③ P4 后(击杀买点)**:陷阱(CFO 负/高质押/商誉雷/周期顶)命中 → 降级/否决。

4. **评分卡映射**:否决只需 4 表面维(P1–P3);确认买点需 6 维齐全(+ P4 陷阱维)。陷阱维是买点否决项,只对 survivor 核。

5. **两卡模板**:
   - **早停卡**(逐字采 spec §6 模板 A:仪表盘〔评级/价/时间框架/触发位/置信度〕+ 4 表面维评分卡 + 2 陷阱维标「未核」+ Rubric建议 + **Rating** ≤Hold + 一行多空 + `FINAL TRANSACTION PROPOSAL` + 早停标注)。
   - **满卡**(survivor,= 现有全卡:仪表盘 + 6 维评分卡 + Rubric + Rating + 三档 EV/R:R + 预期差 + **多空对撞〔P5 强制空头压测:先写最强 bear case + 「什么情况下我就错了」,评级须扛住才 ≥OW〕** + 催化&认错位 + A股富化行 + proposal)。

6. **Grounded 纪律**:早停卡数字只引简报 L1/L2/L3 真值 + 已读 slim 块;陷阱维写「未核」不写数字。满卡数字出自读过的 slim 块。

- [ ] **Step 2: 结构校验**

```bash
F=.claude/skills/analyze-ticker-lite/lite-playbook.md
grep -c "P4 深核分界\|主早停\|早停卡\|未核\|FINAL TRANSACTION PROPOSAL\|防误杀" "$F"
grep -c "Rating" "$F"
```
Expected: 第一条 ≥6 命中;`Rating` ≥2(两卡模板各一)。

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/analyze-ticker-lite/lite-playbook.md
git commit -m "docs(lite): 重写 playbook 为渐进深度 DD + 早停 + 早停卡/满卡双模板

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: 同步 `analyze-ticker-lite/SKILL.md` 流程

**Files:**
- Modify: `.claude/skills/analyze-ticker-lite/SKILL.md`(「流程(3 步)」节)

- [ ] **Step 1: 改流程节**

把「## 流程(3 步)」改为反映渐进深度:
- Step 1 slim 取数不变(`harvest --slim`),补一句:**slim 已重排「表面前/深核后 + `<!-- P4 深核分界 -->`」,被 scan L4 调用时顶部前置漏斗简报**。
- Step 2 改为「**渐进 DD + 早停**:P0 读简报定向 → P1–P3 读表面块填 4 表面维 →【主早停②:非买点 → 出早停卡止】→ survivor 读分界后 P4 陷阱核【③击杀】→ P5 满卡。早停只向下,≥OW 必走 P4+P5」。落点不变(独立跑 `reports/analyze/...`;scan 调用 staging `context/scan/<date>/details/<ticker>.md`)。
- 「## 铁律」加一条:**早停建立在已读真数据,不据漏斗简报判(防误杀);早停卡陷阱维标「未核」**。

- [ ] **Step 2: 校验**

```bash
grep -c "早停\|渐进\|P4 深核分界\|防误杀" .claude/skills/analyze-ticker-lite/SKILL.md
```
Expected: ≥3 命中。

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/analyze-ticker-lite/SKILL.md
git commit -m "docs(lite): SKILL 流程同步渐进深度 + 早停

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: 重写 scan `SKILL.md` + `screening-playbook.md` 的 L4 节

**Files:**
- Modify: `.claude/skills/scan-market/SKILL.md`(L4 步、阶段表行 53–55)
- Modify: `.claude/skills/scan-market/screening-playbook.md`(漏斗一图 + 行 78–120 的 Tier-1/2/3 三节)

- [ ] **Step 1: 改 screening-playbook.md**

- 「## 漏斗一图」L4 行 + 「成本级联」段:把「Tier-1 Sonnet 全判 / Tier-2 Opus 平反 / Tier-3 多空辩论」改为「**L4 研究·一只=一个 Opus subagent 渐进深度 DD + 早停**(P0 简报→P1–P3 表面→主早停②→P4 陷阱核→③击杀→P5 满卡)+ **买单独立 skeptic**」。
- 删原「## L4 研究(委托 analyze-ticker-lite,三层成本级联)」+「**Tier-1**」+「**Tier-2**」三节(行 78–101),替换为一节「**## L4 研究(一只 = 一个 Opus subagent · 渐进深度 + 早停)**」:
  - 对 `finalists.csv` 每只:`compose_funnel_brief(code, scan_dir)` 拼简报 → **前置到该票 `harvest --slim` 产出的 slim 顶部** → 一个 `Agent(model='opus')` 跑 analyze-ticker-lite(读其 `lite-playbook.md`)。
  - **~29 个 subagent 一条消息并发派发**(非顺序);每个独立 context,只回传 评级/目标/R:R/早停与否。
  - 早停规则、防误杀铁律、安全地板照 lite-playbook(引用)。
  - 回卡后 `ratings = parse_ratings_from_details(...)`。
- 保留「## Tier-3 买点候选多空辩论」节但**改名/收口为「## 买单独立 skeptic(发布前红队)」**:`candidates = pick_buy_candidates(ratings)`(最终 ≥OW)每只派一个独立 `Agent(model='opus')` 证伪 → `verify.csv`(机制不变:`code,verdict,bull,bear,trigger,consensus`,assemble 折回评级)。删「多头研究员」那半(survivor 的 P5 自压已是多头;skeptic 只演空头),主线 PM 3 透镜裁判不变。
- 「## L5 整合」`_funnel_rows` 引擎列描述随之更新(见 Task 7)。

- [ ] **Step 2: 改 scan SKILL.md**

阶段表 L4 行 + 流程步 5(行 53–55)把 Tier-1/2/3 三条改为两条:
- **L4 · 研究 · 一只=一个 Opus subagent**:`compose_funnel_brief` 拼简报前置 slim 顶 → `Agent(model='opus')` 跑 analyze-ticker-lite(渐进深度 + 早停;早停只向下、≥OW 必走 P4+P5)。
- **买单 skeptic**:`pick_buy_candidates(ratings)`(≥OW)每只独立 Opus 证伪 → `verify.csv`。

阶段表 L4 引擎列 `Sonnet+Opus` 改 `Opus`;L4.5 行保留(买单 skeptic)。

- [ ] **Step 3: 校验无旧引用**

```bash
grep -rn "batch_finalists\|pick_downgrade_reviews\|Tier-1\|Tier-2" .claude/skills/scan-market/
```
Expected: 无输出(或仅「设计沿革」历史段提及,可保留)。

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/scan-market/SKILL.md .claude/skills/scan-market/screening-playbook.md
git commit -m "docs(scan): L4 节重写为单 Opus subagent 渐进深度 + 早停 + 买单 skeptic

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: `assemble.py` 标签同步(funnel 行 + token 表)

**Files:**
- Modify: `autoresearch/scan/assemble.py:202`(`_funnel_rows` L4 行)、`:251-253`(`_stage_token_estimate` 行标签)

**Interfaces:** 纯字符串标签;`_load_verify`/`_verify_badge`/`_apply_verify_downgrade`/`_verify_detail`/`_archive_reasoning` **不动**(买单 skeptic 复用 verify.csv 机制 + `_v_*` 归档)。

- [ ] **Step 1: 改 `_funnel_rows` L4 行(行 202)**

```python
        f"| L4 | 研究 | {n_cards} 卡 | Opus | 一只=一个 Opus subagent 渐进深度 DD + 早停 + 买单 skeptic |",
```

- [ ] **Step 2: 改 `_stage_token_estimate`(行 244,251–253)**

`l4t2 = list(det.glob("_l4_tier2_*"))` 现在恒空(无 Tier-2),保留无害;把三行 stage 标签改为:

```python
        ("L4 研究", "Opus", len(cards), _b(cards) + _b(l4t1), f"{len(cards)} 张卡(早停卡/满卡)"),
        ("L4 买单 skeptic", "Opus", len(verify), _b(verify), "≥OW 买单独立证伪"),
```

(删原「L4·T2 平反」行;若 `l4t1`/`l4t2` 变量此后未用,一并删其赋值行避免 ruff F841。)

- [ ] **Step 3: 跑 assemble 相关测试 + ruff**

```bash
uv run --no-sync python -m pytest tests/scan/ -q
uv run --no-sync ruff check autoresearch/scan/assemble.py
```
Expected: PASS;ruff clean(无 F841 未用变量)。

- [ ] **Step 4: Commit**

```bash
git add autoresearch/scan/assemble.py
git commit -m "docs(scan-assemble): funnel/token 表标签同步单 Opus subagent + 买单 skeptic

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: 全量验证 + 收尾

**Files:** 无(验证 + finishing-branch)

- [ ] **Step 1: 全量测试**

Run: `uv run --no-sync python -m pytest -q`
Expected: 全绿(原 391 测试 − 删 2 + 加 5 ≈ 394,具体数以实际为准;无 FAIL/ERROR)。

- [ ] **Step 2: ruff 全量**

Run: `uv run --no-sync ruff check autoresearch/ tests/`
Expected: clean(若有 import 残留/未用变量,修掉再重跑)。

- [ ] **Step 3: 无悬挂引用终检**

```bash
grep -rn "batch_finalists\|pick_downgrade_reviews" autoresearch/ tests/ .claude/skills/scan-market/SKILL.md .claude/skills/scan-market/screening-playbook.md
```
Expected: 无输出(历史「设计沿革」段如有提及可忽略)。

- [ ] **Step 4: finishing-a-development-branch**

Announce: "I'm using the finishing-a-development-branch skill to complete this work." 然后按该 skill:验证测试 → present 选项(merge/PR/keep/discard)→ 执行。

---

## 自检(写完计划回看 spec)

**1. Spec 覆盖**:§3 阶段流→Task 4/5/6;§4 漏斗简报→Task 1;§5 早停点→Task 4;§6 两卡模板→Task 4;§7 买单 skeptic→Task 6;§8 编排改动→Task 2/6;§9 成本=文档说明(Task 4/6 含);§10 文件结构→全 Task;slim 重排(§3 渐进读盘 + §8 harvest)→Task 3;assemble→Task 7。**无遗漏**。

**2. Placeholder 扫描**:代码步均含完整代码;文档步含结构 + 关键模板 inline + grep 校验。无 TBD/「类似 Task N」。

**3. 类型一致**:`compose_funnel_brief(code, scan_dir)→str`、`pick_buy_candidates(ratings)→list[str]`、`_reorder_slim_for_progressive(parts)→list[str]`、`_P4_MARKER:str` 在 Task 1/2/3 定义,Task 4/6/7 引用一致。`pick_buy_candidates` 签名跨 Task 不变(仅 docstring 改)。
