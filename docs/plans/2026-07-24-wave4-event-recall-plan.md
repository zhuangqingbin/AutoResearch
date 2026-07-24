# Wave 4:事件召回路(确定性)+ 新闻腿诚实化 + 影子仪器修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补上漏斗唯一的"事件驱动"召回缺口——用**已入湖、已带 `ts_code`、已有确定性分类**的三个公告端点做一路全市场事件召回;同时修好判它生死的仪器(影子不落逐路长表)与一条静默死了的新闻腿。

**Architecture:** 全确定性零 LLM。`l3_catalyst.catalyst_counts` 纯函数原样复用,取数从「L3 阶段 · L2-200 只」提前到「L1 之前 · 全市场」→ 事件计数列 merge 进 scored 帧 → 新 `@channel("event")` 注册但**默认不启用**;靠 `L1_channels.csv` 逐路长表 + `channel_audit` 的 `unique_excess_t2` 累计 ≥10 日裁决,与 accumulation 当年被裁同口径。

**Tech Stack:** Python 3 + pandas + pytest;`uv run --no-sync`;湖(`context/lake/`)优先零网络。

## 需求源与实证前提(为什么是这个设计,不是原计划的 LLM 新闻路)

原 spec Wave 4 写的是「新闻召回路影子版:LLM 只做名称+引句+事件类型,映射/对账全确定性」。2026-07-24 勘察后**三条实证推翻了它的前提**:

1. **动机被证伪**:07-21 反弹日的裁决点已兑现 —— 当日 ≥9.5% 的 350 只票 `fwd_2_oc` **−2.06%** vs 全市场 **+1.60%**(超额 **−3.67pp**,t=−11.91);其中科技类 170 只 **−3.25%**(超额 −4.85pp,t=−13.58);pass1 被切的 164 只超额 −0.33pp(t=−1.04 不显著)。**漏斗当天 7% 的召回率不是失明,是避开陷阱**。→ 「把当日大涨票召回来」是负价值,新召回路**不得**以当日涨幅为信号。
2. **LLM 无事可做**:全市场当日可批量拉的五个端点(`forecast`/`express`/`stk_holdertrade`/`repurchase`/`stk_surv`)**全部自带 `ts_code`**,不需要"名称→代码"映射;`type`/`in_de`/`proc` 字段已是中文事件标签,分类逻辑 `l3_catalyst.py:32-40` 早已确定性。唯一需要抽名称的 `stock_news_em` **只能单票查询**,不能做全市场召回源。
3. **仪器是坏的**:`write_shadow_variants`(`universe.py:253-300`)把 `recall_select` 的第二个返回值 `per_channel` 一律丢弃(`:283` `re9, _ = ...`;`:293` `recall20, _ = ...`),影子变体不落 `L1_channels.csv`;而裁决新路所需的 `unique_excess_t2` **必须**从该长表算(`channel_audit._load_day`,`channel_audit.py:196-208`)。**不修仪器就没法裁决新路。**

附带发现(Task 1):`harvest_l3_news`(`l3_news.py:116`)唯一数据源 `anns_d` **无权限**(活体报错「您没有接口(anns_d)访问权限」),`contracts.py:120-122` 已标其退役,但 `l3_news` 仍在调用它 → 每票写空列表、退出码 0、无告警 → `news_n`/`news_sent`/`news_head` 三列在 **07-14/07-17/07-21 三个扫描日全为零**,L3 agent 每天看着一个永远空的列。这是 2026-07-21 用户批评「报告里没有新闻分析」的**第二层根因**(第一层是空 config 事故)。

## Global Constraints

- **新信号入场纪律(既定,不得绕过)**:新路默认**不进** `scan_config.jsonc` 的 `recall_channels`;`unique_excess_t2` 累计 **≥10 个数据日**且为正才有资格提启用提案(与 accumulation 2026-07-11 被裁同口径、同命令 `python -m autoresearch.research.channel_audit`);IC 过硬前**不入 composite、不设门**。
- **不得以当日涨幅为召回信号**(上条实证 1);事件计数是"有没有发生这件事",不是"涨了多少"。
- **Parity 铁律**:新路不启用 → L0/L1/L2 输出逐字节不变;事件列 merge 失败 → 列缺失,各 channel 的 `gate_rank` 已有"缺 `score_col` → 空帧"降级契约。
- **降级留痕**(本项目连修四次的病):取数失败/端点无权限**必须有可见告警**,不得写空值后退出码 0。
- **湖优先零网络**:三端点已入湖 18 日;`get_or_fetch` 命中湖不发网络请求。新增取数**不得**触发全市场逐票网络查询。
- **A 级契约不动**:事件列在 `composite_score` **之后** merge 进 `scored`,不进 `build_market_frame`(避开 `check_market_frame` 的 A 级规模契约)。
- 测试命令 `uv run --no-sync python -m pytest ...`;ruff 干净;频繁 commit;直接 main。

---

### Task 1: 新闻腿诚实化 —— 停止调用已退役端点,断链必须可见

**Files:**
- Modify: `autoresearch/scan/agents/l3_news.py`
- Modify: `autoresearch/scan/agents/l3_select.py`(调用点 `:925-926` 与列契约 `:31`)
- Modify: `autoresearch/scan/health.py`(`anns_empty_rate` 语义随之调整)
- Test: `tests/scan/test_l3_news.py`(按现场文件名;无则新建)

**Interfaces:**
- Produces: `harvest_l3_news(date, codes, root=None, lookback_days=10) -> dict` 签名不变,但**行为改为**:检测到端点退役 → 立即返回空桶 + **打印一行显式告警**(不再逐日试探 10 次再 break)。
- `news_digest` 与 `news_sent`/`news_head` 列契约**不动**(被契约测试冻结),但 L3 表渲染侧对"全空"要有可见标注。

- [ ] **Step 1: 写失败测试**

`tests/scan/test_l3_news.py` 追加(文件不存在则新建,顶部 `import pytest` + 被测模块):

```python
def test_harvest_l3_news_retired_endpoint_is_loud(capsys, tmp_path):
    """anns_d 已退役(无权限):必须一次性识别 + 打印告警,不得静默写空。"""
    from autoresearch.scan.agents import l3_news

    calls = {"n": 0}

    def _boom(endpoint, params, today=None):
        calls["n"] += 1
        raise Exception("抱歉，您没有接口(anns_d)访问权限")

    l3_news.get_or_fetch = _boom          # module-attr 派发(与仓库既有 monkeypatch 惯例一致)
    out = l3_news.harvest_l3_news("2026-07-24", ["300857", "002371"], root=tmp_path)
    assert out == {"300857": [], "002371": []}
    assert calls["n"] <= 1, "权限错必然日日同错:不得逐日重试"
    cap = capsys.readouterr()
    assert "anns_d" in (cap.out + cap.err) and "退役" in (cap.out + cap.err), \
        "断链必须留痕(降级不留痕是本项目最忌的形态)"


def test_harvest_l3_news_writes_empty_buckets_still(tmp_path):
    """契约不变:仍为每只票落 json(下游 news_digest 依赖文件存在)。"""
    from autoresearch.scan.agents import l3_news
    l3_news.get_or_fetch = lambda *a, **k: (_ for _ in ()).throw(Exception("权限"))
    l3_news.harvest_l3_news("2026-07-24", ["300857"], root=tmp_path)
    assert (tmp_path / "2026-07-24" / "L3_news" / "300857.json").exists()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_l3_news.py -x -q`
Expected: FAIL(现实现无告警输出;`calls["n"]` 可能 >1)

- [ ] **Step 3: 实现**

`l3_news.py` 的 `harvest_l3_news`,把现有 `_PERM_MARKS` 有界降级块改为**先判退役、一次性告警**:

```python
    _PERM_MARKS = ("权限", "40203")
    fails = 0
    retired = False
    for dd in _trade_days_for(date, lookback_days):
        try:
            df = get_or_fetch("anns_d", {"ann_date": dd}, today=date)
        except Exception as e:  # noqa: BLE001 — 端点退役/无权限 → 一次性告警后停(降级必须留痕)
            fails += 1
            if any(m in repr(e) for m in _PERM_MARKS) or fails >= 3:
                retired = True
                break
            continue
        ...
    if retired:
        # contracts.py 已标 anns_d 退役(2026-07-18);此处让它在**运行时**也可见——
        # 静默写空桶正是 news_n/news_sent/news_head 三列连续多个扫描日全为 0 而无人察觉的原因。
        print(f"[l3_news] ⚠️ anns_d 已退役/无权限 → 公告标题流为空({len(want)} 只票的 "
              f"news_n/news_sent/news_head 本日全为缺省值),L3 情感列不可用。", file=sys.stderr)
```

顶部补 `import sys`。

`l3_select.py` 的 L3 表渲染:`news_sent`/`news_head` 两列在**整列全空**时,表头或表尾加一句可见标注(具体位置以现场 `l3_table_md` 的既有降级标注写法为准,照抄同款),例如 `_(公告情感列不可用:anns_d 已退役,详见 run_health)_`。**不要删列**(契约测试冻结)。

`health.py` 的 `anns_expected` 语义保持(`>=1.0` 为 expected),但把该项在 run_health 渲染里从"静默 expected"改为**显式一行**「公告标题流:不可用(anns_d 退役)」,让每次跑动都看得见。

- [ ] **Step 4: 跑测试通过 + 全量回归 + Commit**

Run: `uv run --no-sync python -m pytest tests/scan -x -q && uv run --no-sync python -m pytest -q && uv run --no-sync ruff check autoresearch tests`

```bash
git add autoresearch/scan/agents/l3_news.py autoresearch/scan/agents/l3_select.py autoresearch/scan/health.py tests/
git commit -m "fix(scan): 公告标题流断链改为显式告警(anns_d 退役后静默产零值三扫描日无人察觉)"
```

---

### Task 2: 全市场事件计数(`catalyst_counts` 复用,取数提到 L1 前)

**Files:**
- Create: `autoresearch/scan/events.py`
- Test: `tests/scan/test_events.py`

**Interfaces:**
- Consumes: `l3_catalyst.catalyst_counts(frames: dict[str, list[pd.DataFrame]], want: set[str]) -> pd.DataFrame`(列 `code, rep_impl, rep_plan, holder_in, holder_de, surv_n`)、`l3_catalyst._ENDPOINTS`、`l3_news._trade_days_for(date, n) -> list[str]`、`cache.get_or_fetch`。
- Produces:
  - `market_event_counts(date: str, *, lookback_days: int = 10, fetch_fn=None) -> pd.DataFrame`(全市场;湖优先;**不传 want** = 不过滤代码)
  - `EVENT_COLS: tuple[str, ...] = ("ev_rep_impl", "ev_rep_plan", "ev_holder_in", "ev_holder_de", "ev_surv_n", "ev_pos")`
  - `attach_event_cols(scored: pd.DataFrame, ev: pd.DataFrame) -> pd.DataFrame`(左连接;缺 → 0.0;`ev_pos` = 正催化加权和)

- [ ] **Step 1: 写失败测试**

`tests/scan/test_events.py`:

```python
"""全市场事件计数契约(Wave4 Task 2):湖优先、缺数据降级留痕、正催化口径。"""
import pandas as pd

from autoresearch.scan import events


def _frames():
    """三端点的日帧(字段名与真身一致:holdertrade 用 in_de,repurchase 用 proc)。"""
    return {
        "stk_holdertrade": pd.DataFrame(
            [{"ts_code": "300857.SZ", "in_de": "IN"}, {"ts_code": "002371.SZ", "in_de": "DE"}]),
        "repurchase": pd.DataFrame(
            [{"ts_code": "300857.SZ", "proc": "实施"}, {"ts_code": "600000.SH", "proc": "预案"}]),
        "stk_surv": pd.DataFrame(
            [{"ts_code": "300857.SZ"}, {"ts_code": "300857.SZ"}, {"ts_code": "002371.SZ"}]),
    }


def test_market_event_counts_whole_market_no_code_filter(monkeypatch):
    """全市场:不传 want,湖里有谁就算谁(与 L3 阶段只算 L2-200 的口径相反)。"""
    fr = _frames()
    monkeypatch.setattr(events, "_fetch_day",
                        lambda ep, day, date: fr.get(ep, pd.DataFrame()))
    ev = events.market_event_counts("2026-07-24", lookback_days=1)
    assert set(ev["code"]) == {"300857", "002371", "600000"}
    r = ev.set_index("code").loc["300857"]
    assert r["ev_holder_in"] == 1 and r["ev_rep_impl"] == 1 and r["ev_surv_n"] == 2
    assert ev.set_index("code").loc["002371"]["ev_holder_de"] == 1


def test_ev_pos_excludes_reduction(monkeypatch):
    """正催化口径与 catalyst_ledger._POS 对齐:减持不算正。"""
    fr = _frames()
    monkeypatch.setattr(events, "_fetch_day", lambda ep, day, date: fr.get(ep, pd.DataFrame()))
    ev = events.market_event_counts("2026-07-24", lookback_days=1).set_index("code")
    assert ev.loc["002371", "ev_pos"] == 1          # 只有 surv_n=1;holder_de 不计
    assert ev.loc["300857", "ev_pos"] == 4          # in 1 + impl 1 + surv 2


def test_market_event_counts_all_legs_fail_is_loud(monkeypatch, capsys):
    """三腿全失败 → 空帧 + 显式告警(降级留痕),不静默返回空。"""
    def _boom(ep, day, date):
        raise RuntimeError("no permission")
    monkeypatch.setattr(events, "_fetch_day", _boom)
    ev = events.market_event_counts("2026-07-24", lookback_days=1)
    assert list(ev.columns)[0] == "code" and len(ev) == 0
    assert "事件取数" in (capsys.readouterr().err)


def test_attach_event_cols_parity_and_fill():
    scored = pd.DataFrame({"code": ["300857", "999999"], "composite": [50.0, 60.0]})
    ev = pd.DataFrame({"code": ["300857"], "ev_rep_impl": [1.0], "ev_rep_plan": [0.0],
                       "ev_holder_in": [1.0], "ev_holder_de": [0.0], "ev_surv_n": [2.0],
                       "ev_pos": [4.0]})
    out = events.attach_event_cols(scored, ev)
    assert len(out) == 2 and out.set_index("code").loc["999999", "ev_pos"] == 0.0
    assert list(scored.columns) == ["code", "composite"]      # 不就地改入参
    # 空事件帧 → 全 0 列(parity:下游 channel 拿到列但恒 0 = 空帧降级)
    out2 = events.attach_event_cols(scored, pd.DataFrame({"code": []}))
    assert all(c in out2.columns for c in events.EVENT_COLS)
    assert out2["ev_pos"].sum() == 0.0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_events.py -x -q`
Expected: FAIL(`No module named 'autoresearch.scan.events'`)

- [ ] **Step 3: 实现 `autoresearch/scan/events.py`**

```python
"""全市场事件计数(Wave4 Task 2;确定性,零 LLM)。

plan: docs/plans/2026-07-24-wave4-event-recall-plan.md。

与 `agents/l3_catalyst.py` 的关系:**分类逻辑 100% 复用它的 `catalyst_counts` 纯函数**
(增减持 in_de / 回购 proc / 调研计数,已确定性、已单测),本模块只改两件事——
①覆盖面:L2-200 → **全市场**(不传 want 过滤);②时机:L3 阶段 → **L1 之前**
(要能把没进 L2 的票也捞出来,否则事件召回路无意义)。

三端点均已入湖(`ann_date`/`trade_date` 键,eod 不可变)→ `get_or_fetch` 湖命中零网络。
降级:单端点单日失败跳过;**三腿全失败 → 空帧 + stderr 告警**(降级必须留痕)。
"""
from __future__ import annotations

import sys

import pandas as pd

from autoresearch.scan.agents.l3_catalyst import _ENDPOINTS, catalyst_counts

EVENT_COLS: tuple[str, ...] = ("ev_rep_impl", "ev_rep_plan", "ev_holder_in",
                               "ev_holder_de", "ev_surv_n", "ev_pos")
# 正催化口径与 learning/catalyst_ledger._POS 对齐(减持不算正)——同一口径两处消费,勿分叉。
_POS_SRC = ("rep_impl", "rep_plan", "holder_in", "surv_n")
_RENAME = {"rep_impl": "ev_rep_impl", "rep_plan": "ev_rep_plan",
           "holder_in": "ev_holder_in", "holder_de": "ev_holder_de", "surv_n": "ev_surv_n"}


def _empty() -> pd.DataFrame:
    return pd.DataFrame({"code": pd.Series(dtype=str),
                         **{c: pd.Series(dtype=float) for c in EVENT_COLS}})


def _fetch_day(endpoint: str, day: str, date: str) -> pd.DataFrame:
    """单端点单日取数(湖优先);module-attr 派发便于测试注入。"""
    from autoresearch.data.cache import get_or_fetch
    return get_or_fetch(endpoint, {_ENDPOINTS[endpoint]: day}, today=date)


def market_event_counts(date: str, *, lookback_days: int = 10, fetch_fn=None) -> pd.DataFrame:
    """近 lookback_days 交易日三端点全市场事件计数;列 = code + EVENT_COLS。"""
    from autoresearch.scan.agents.l3_news import _trade_days_for
    fetch = fetch_fn or _fetch_day
    frames: dict[str, list[pd.DataFrame]] = {ep: [] for ep in _ENDPOINTS}
    ok = 0
    for day in _trade_days_for(date, lookback_days):
        for ep in _ENDPOINTS:
            try:
                df = fetch(ep, day, date)
            except Exception:  # noqa: BLE001 — 单端点单日失败跳过,余下照拉
                continue
            if df is not None and len(df):
                frames[ep].append(df)
                ok += 1
    if not ok:
        print(f"[events] ⚠️ 事件取数三腿全失败({date},近 {lookback_days} 交易日)"
              "→ 事件列全 0,事件召回路本日等同停用。", file=sys.stderr)
        return _empty()

    want = {str(c).split(".")[0].zfill(6)
            for ep in frames for df in frames[ep] for c in df.get("ts_code", [])}
    if not want:
        return _empty()
    cnt = catalyst_counts(frames, want)
    if cnt is None or not len(cnt):
        return _empty()
    out = cnt.rename(columns=_RENAME)
    for c in EVENT_COLS:
        if c not in out.columns:
            out[c] = 0.0
    out["ev_pos"] = sum(out[_RENAME[k]].fillna(0.0) for k in _POS_SRC)
    return out[["code", *EVENT_COLS]]


def attach_event_cols(scored: pd.DataFrame, ev: pd.DataFrame) -> pd.DataFrame:
    """事件列左连接进 scored(不就地改入参);缺 → 0.0。

    挂在 `composite_score` **之后**——不进 `build_market_frame`,避开 A 级规模契约。
    """
    out = scored.copy()
    out["code"] = out["code"].astype(str).str.zfill(6)
    if ev is None or not len(ev):
        for c in EVENT_COLS:
            out[c] = 0.0
        return out
    e = ev.copy()
    e["code"] = e["code"].astype(str).str.zfill(6)
    out = out.merge(e[["code", *[c for c in EVENT_COLS if c in e.columns]]],
                    on="code", how="left")
    for c in EVENT_COLS:
        out[c] = out[c].fillna(0.0) if c in out.columns else 0.0
    return out
```

- [ ] **Step 4: 跑测试通过 + 全量回归 + Commit**

Run: `uv run --no-sync python -m pytest tests/scan/test_events.py -x -q && uv run --no-sync python -m pytest -q`

```bash
git add autoresearch/scan/events.py tests/scan/test_events.py
git commit -m "feat(scan): 全市场事件计数(复用 catalyst_counts;覆盖面 200→全市场·时机 L3→L1 前)"
```

---

### Task 3: `event` 召回路注册 + 接进 `universe.run`(默认不启用)

**Files:**
- Modify: `autoresearch/scan/recall/channels.py`(新 `@channel("event")`)
- Modify: `autoresearch/scan/universe.py`(`run()` 里 merge 事件列)
- Modify: `autoresearch/scan/recall/l2_stratify.py`(新桶 + floor)
- Test: `tests/scan/test_recall_event_channel.py`

**Interfaces:**
- Consumes: `events.market_event_counts` / `events.attach_event_cols` / `events.EVENT_COLS`、`recall.base.gate_rank`、`recall.registry.channel`。
- Produces: 注册名 `"event"`,`ChannelSpec(quota=80, floor=20)`;`scored` 帧新增 `EVENT_COLS` 六列。

- [ ] **Step 1: 写失败测试**

`tests/scan/test_recall_event_channel.py`:

```python
"""event 召回路契约(Wave4 Task 3):事件门 + 非涨幅排序 + 缺列降级 + 默认不启用。"""
import pandas as pd

from autoresearch.scan.recall import CHANNEL_DEFAULTS, build, registered_channels


def _frame(n=6):
    return pd.DataFrame({
        "code": [f"00000{i}" for i in range(n)],
        "composite": [50.0 + i for i in range(n)],
        "pct_1d": [10.0, 9.5, 0.2, -1.0, 0.5, 0.1],     # 涨幅:不得成为排序依据
        "amount_yi": [5.0] * n,
        "ev_pos": [0.0, 0.0, 5.0, 3.0, 1.0, 0.0],
        "ev_rep_impl": [0.0, 0.0, 1.0, 0.0, 0.0, 0.0],
        "ev_holder_in": [0.0, 0.0, 1.0, 1.0, 0.0, 0.0],
        "ev_surv_n": [0.0, 0.0, 3.0, 2.0, 1.0, 0.0],
        "ev_rep_plan": [0.0] * n, "ev_holder_de": [0.0] * n,
    })


def test_event_channel_registered_with_spec():
    assert "event" in registered_channels()
    spec = CHANNEL_DEFAULTS["event"]
    assert spec.quota == 80 and spec.floor == 20


def test_event_channel_gates_on_events_not_price():
    out = build("event")(_frame(), "2026-07-24", 10)
    got = list(out["code"])
    assert "000000" not in got and "000001" not in got, "涨幅最大但无事件 → 不得召回"
    assert got[0] == "000002", "按 ev_pos 降序(5 > 3 > 1)"
    assert got[:3] == ["000002", "000003", "000004"]


def test_event_channel_missing_cols_degrades_to_empty():
    """事件列缺失(取数全失败)→ 空帧,与其余 10 路同款降级契约。"""
    f = _frame().drop(columns=["ev_pos"])
    out = build("event")(f, "2026-07-24", 10)
    assert len(out) == 0 and list(out.columns) == ["code", "channel_rank", "channel_score"]


def test_event_channel_all_zero_degrades_to_empty():
    """事件列在但全 0(三腿失败后 attach 填 0)→ 空帧,不得召回一堆零事件票。"""
    f = _frame()
    f["ev_pos"] = 0.0
    out = build("event")(f, "2026-07-24", 10)
    assert len(out) == 0


def test_event_not_enabled_by_default():
    """新信号入场纪律:默认不进生产 recall_channels(scan_config 未列 = 不启用)。"""
    import json
    import re
    from pathlib import Path
    raw = Path(".claude/skills/scan-market/scan_config.jsonc").read_text(encoding="utf-8")
    cfg = json.loads(re.sub(r"//.*", "", raw))
    assert "event" not in (cfg.get("funnel", {}).get("recall_channels") or []), \
        "event 路须累计 ≥10 日 unique_excess_t2 为正、经人批才可启用"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_recall_event_channel.py -x -q`
Expected: FAIL(`event` 未注册)

- [ ] **Step 3: 实现**

`channels.py` 末尾新增(照抄 `healthy` 的结构):

```python
@channel("event", quota=80, floor=20,
         desc="公告事件(回购实施/增持/机构调研近10日;确定性分类,非涨幅信号)")
def event(frame, date, k):
    """事件驱动召回(Wave4)——补漏斗唯一的"有实质公告但价格还没反应"缺口。

    **不用当日涨幅**:2026-07-24 实证,07-21 当日 ≥9.5% 的 350 只票 fwd_2_oc −2.06%
    vs 全市场 +1.60%(超额 −3.67pp,t=−11.91)——追当日大涨是负价值。本路只问
    "近 10 交易日有没有发生正催化事件",排序按事件强度 `ev_pos`(减持不计正)。

    缺列 / 整列全 0(事件取数三腿全失败)→ 空帧降级(与其余 10 路同契约)。
    **默认不启用**:须 `channel_audit` 的 unique_excess_t2 累计 ≥10 日为正 + 人批
    才进 scan_config.funnel.recall_channels(与 accumulation 2026-07-11 被裁同纪律)。
    """
    if "ev_pos" not in frame.columns:
        return gate_rank(frame, None, "ev_pos", k)          # 缺列 → 空帧
    mask = frame["ev_pos"].fillna(0.0) > 0
    if not bool(mask.any()):
        return gate_rank(frame, None, "__no_event__", k)    # 全 0 → 空帧(不召回零事件票)
    return gate_rank(frame, mask, "ev_pos", k)
```

`universe.py` 的 `run()`,在 `scored = composite_score(uni, weights)` 之后、`recall_select(...)` 之前插入:

```python
    with contextlib.suppress(Exception):   # Wave4:事件列(湖优先);失败=列缺失,event 路自动空帧降级
        from autoresearch.scan.events import attach_event_cols, market_event_counts
        scored = attach_event_cols(scored, market_event_counts(analysis_date))
```

(`contextlib` 若未导入则在函数内 `import contextlib`;此处 suppress 是**有痕的**——`market_event_counts` 内部三腿全失败已打 stderr 告警。)

`l2_stratify.py` 的 `STYLE_CHANNELS` 加桶、`DEFAULT_FLOORS` 加 floor:

```python
STYLE_CHANNELS = {..., "事件": ("event",)}
DEFAULT_FLOORS = {..., "事件": 10}
```

> 注:`reversal_confirm` 至今不在任何桶里(既有欠账),本 task **不顺手修**——那会改变现有 L2 采样分布、污染 event 路的影子对照。单独记账。

- [ ] **Step 4: 跑测试通过 + 全量回归 + Commit**

Run: `uv run --no-sync python -m pytest tests/scan -x -q && uv run --no-sync python -m pytest -q && uv run --no-sync ruff check autoresearch tests`

```bash
git add autoresearch/scan/recall/ autoresearch/scan/universe.py tests/
git commit -m "feat(recall): event 召回路(事件门·非涨幅排序·默认不启用)+ L2 事件桶"
```

---

### Task 4: 修影子仪器 —— 影子变体落 `L1_channels.csv`,让 `unique_excess_t2` 可算

**Files:**
- Modify: `autoresearch/scan/universe.py`(`write_shadow_variants`)
- Modify: `autoresearch/research/channel_audit.py`(可读影子长表)
- Test: `tests/scan/test_shadow.py`(追加)

**Interfaces:**
- Produces: `context/scan/<date>/shadow/L1_channels_<variant>.csv`(与主 `L1_channels.csv` 同列:`channel, code, channel_rank, channel_score`);新影子变体 `plus_event`。

- [ ] **Step 1: 写失败测试**

`tests/scan/test_shadow.py` 追加:

```python
def test_shadow_variants_persist_per_channel(tmp_path, monkeypatch):
    """仪器修复:影子变体必须落逐路长表,否则 unique_excess_t2 无从算起
    (accumulation 2026-07-11 被裁用的就是该指标)。"""
    import pandas as pd
    from autoresearch.scan import universe as U

    scored = pd.DataFrame({
        "code": [f"{i:06d}" for i in range(30)],
        "name": [f"n{i}" for i in range(30)],
        "composite": [float(90 - i) for i in range(30)],
        "industry": ["电子"] * 30,
        "ev_pos": [3.0 if i % 5 == 0 else 0.0 for i in range(30)],
        "main_net_ratio": [0.1] * 30, "cmf_20": [0.1] * 30, "pct_60d": [5.0] * 30,
        "amount_yi": [5.0] * 30, "mktcap_yi": [80.0] * 30,
    })
    recall, per = U.recall_select(scored, "2026-07-24", 20, "multi", ["composite"])
    out = tmp_path / "2026-07-24"
    out.mkdir(parents=True)
    U.write_shadow_variants(out, scored, recall, "2026-07-24", 20, 10, None, 1.0,
                            list(scored.columns), recall_channels=["composite"])
    sh = out / "shadow"
    names = {p.name for p in sh.glob("L1_channels_*.csv")}
    assert names, "影子变体未落逐路长表(per_channel 被丢弃)"
    one = pd.read_csv(sorted(sh.glob("L1_channels_*.csv"))[0])
    assert set(["channel", "code", "channel_rank", "channel_score"]).issubset(one.columns)


def test_shadow_plus_event_variant(tmp_path):
    """新增 plus_event 变体 = 现启用路 + event(少一路的镜像:pre_healthy 是多一路的反面)。"""
    import pandas as pd
    from autoresearch.scan import universe as U
    scored = pd.DataFrame({
        "code": [f"{i:06d}" for i in range(30)],
        "name": [f"n{i}" for i in range(30)],
        "composite": [float(90 - i) for i in range(30)],
        "industry": ["电子"] * 30,
        "ev_pos": [3.0 if i > 25 else 0.0 for i in range(30)],   # 事件票 composite 排名靠后
        "main_net_ratio": [0.1] * 30, "cmf_20": [0.1] * 30, "pct_60d": [5.0] * 30,
        "amount_yi": [5.0] * 30, "mktcap_yi": [80.0] * 30,
    })
    recall, _ = U.recall_select(scored, "2026-07-24", 20, "multi", ["composite"])
    out = tmp_path / "2026-07-24"
    out.mkdir(parents=True)
    made = U.write_shadow_variants(out, scored, recall, "2026-07-24", 20, 10, None, 1.0,
                                   list(scored.columns), recall_channels=["composite"])
    assert "plus_event" in made
    codes = set(pd.read_csv(out / "shadow" / "L2_plus_event.csv",
                            dtype={"code": str})["code"].str.zfill(6))
    assert {"000026", "000027"} & codes, "事件票应被 plus_event 变体捞进 L2"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_shadow.py -x -q`
Expected: FAIL(无 `L1_channels_*.csv`;无 `plus_event`)

- [ ] **Step 3: 实现**

`universe.py` 的 `write_shadow_variants`:

1. 建一个落盘小工具(函数内定义即可):

```python
    def _dump_per_channel(vname: str, pc) -> None:
        """影子变体的逐路长表(Wave4 仪器修复)。

        原实现把 `recall_select` 的 per_channel 一律丢弃(`re9, _ = ...`),导致影子只能
        拿到"L2 名单多捕几个赢家"的粗读数,拿不到 `unique_excess_t2` —— 而后者正是
        accumulation 2026-07-11 被裁决退役所用的指标。没有它,新召回路无法按同口径裁决。
        """
        if pc is not None and len(pc):
            pc.to_csv(sh / f"L1_channels_{vname}.csv", index=False)
```

2. `pre_healthy` 分支的 `re9, _ = recall_select(...)` 改为接住并落盘;`capfloor20` 分支同理。
3. **`pre_healthy` 的既有 bug 顺手修**:`old` 取 `registered_channels()`(全 11 路)而非当日实际启用的路 → 反事实里混进了已停用的 `accumulation`/`northbound`。改为:

```python
        base_names = list(recall_channels or registered_channels())
        old = [n for n in base_names if n != "healthy"]
```

4. 新增 `plus_event` 变体(镜像 `pre_healthy`,方向相反):

```python
    if recall_mode == "multi":
        base_names = list(recall_channels or registered_channels())
        if "event" not in base_names:
            plus = [*base_names, "event"]
            with contextlib.suppress(Exception):
                re_p, pc_p = recall_select(scored, analysis_date, recall_n, "multi", plus,
                                           channel_quotas=channel_quotas,
                                           channel_floors=channel_floors)
                variants["plus_event"], _ = select_l2(re_p, l2_n, floors=l2_floors,
                                                      sector_cap_frac=l2_sector_cap)
                _dump_per_channel("plus_event", pc_p)
```

`channel_audit.py`:`_load_day` 增加可选影子读取——新增 CLI flag `--variant <name>`,读 `shadow/L1_channels_<name>.csv` 替代主 `L1_channels.csv`(其余口径一字不改),使影子路能用**同一套** `unique_excess_t2` 计算。缺该文件 → 跳过该日(与现行缺文件行为一致)。

- [ ] **Step 4: 跑测试通过 + 全量回归 + Commit**

Run: `uv run --no-sync python -m pytest tests/scan tests/research -x -q && uv run --no-sync python -m pytest -q`

```bash
git add autoresearch/scan/universe.py autoresearch/research/channel_audit.py tests/
git commit -m "fix(shadow): 影子变体落逐路长表(unique_excess_t2 可算)+ plus_event 变体 + pre_healthy 反事实混入停用路修复"
```

---

### Task 5: 控制端活体冒烟 + 影子起跑 + 文档/记账(控制端自跑,不派 subagent)

**Files:**
- Modify: `.claude/skills/scan-market/STAGES.md`(L1 通道表加 event 行 + 影子节)
- Modify: `docs/plans/2026-07-24-wave4-event-recall-plan.md`(实录回填)
- Modify: `.superpowers/sdd/progress.md`

- [ ] **Step 1: 事件取数活体冒烟(真湖)**

```bash
uv run --no-sync python -c "
from autoresearch.scan.events import market_event_counts, EVENT_COLS
ev = market_event_counts('2026-07-21')
print('事件票数:', len(ev), '| 列:', list(ev.columns))
print(ev.sort_values('ev_pos', ascending=False).head(8).to_string(index=False))
print('ev_pos>0 的票数:', int((ev['ev_pos']>0).sum()))"
```

预期:近 10 交易日三端点湖命中(18 日已入湖),事件票数应在**几十到几百**量级。若为 0 → 查是不是 `_trade_days_for` 的日期窗与湖内文件日期不重合(湖最新 20260716),照实记录。

- [ ] **Step 2: event 路召回冒烟(不改生产)**

```bash
uv run --no-sync python -c "
import pandas as pd
from autoresearch.scan.events import market_event_counts, attach_event_cols
from autoresearch.scan.recall import build
sc = pd.read_csv('context/scan/2026-07-21/L1_scored_full.csv', dtype={'code':str})
sc['code']=sc['code'].str.zfill(6)
sc = attach_event_cols(sc, market_event_counts('2026-07-21'))
out = build('event')(sc, '2026-07-21', 80)
print('event 路召回:', len(out))
print(out.head(10).merge(sc[['code','name','composite','ev_pos']], on='code').to_string(index=False))
rec = pd.read_csv('context/scan/2026-07-21/L1_recall_top1000.csv', dtype={'code':str})
rec['code']=rec['code'].str.zfill(6)
new = set(out['code']) - set(rec['code'])
print('若启用,新增召回(现有 9 路没召到的):', len(new))"
```

- [ ] **Step 3: 前瞻性回看(该路会不会有 edge 的第一手读数)**

```bash
uv run --no-sync python -c "
import pandas as pd, scipy.stats as st
from autoresearch.scan.events import market_event_counts, attach_event_cols
a = pd.read_csv('context/scan/2026-07-21/retro/attribution.csv', dtype={'code':str})
a['code']=a['code'].str.zfill(6)
ev = market_event_counts('2026-07-21')
m = a.merge(ev, on='code', how='left').fillna({'ev_pos':0.0})
hi, lo = m[m['ev_pos']>0], m[m['ev_pos']==0]
t,p = st.ttest_ind(hi['fwd_2_oc'].dropna(), lo['fwd_2_oc'].dropna(), equal_var=False)
print('有正催化 n=%d fwd_2 %+.2f%% | 无 n=%d %+.2f%% | 差 %+.2fpp t=%.2f p=%.3f' % (
  len(hi),100*hi['fwd_2_oc'].mean(),len(lo),100*lo['fwd_2_oc'].mean(),
  100*(hi['fwd_2_oc'].mean()-lo['fwd_2_oc'].mean()),t,p))"
```

**照实记录,不粉饰**。这是 n=1 日,**无论正负都不改生产**(纪律:≥10 日 `unique_excess_t2`)。若显著为负 → 立即记账为负结果候选,别等攒满 10 日。

- [ ] **Step 4: 文档 + 记账**

- `STAGES.md` 的 L1 通道表加 `event` 行(quota 80/floor 20,标注**默认停用·取证中**),影子节加 `plus_event` 与"影子现在也落逐路长表"。
- 本计划尾部追加 `## 冒烟实录(2026-07-24)`,记 Step 1-3 的**真实读数**与任何降级。
- `.superpowers/sdd/progress.md` 收口。
- **建 proposal**:`pr_20260724_001`「event 召回路启用」,状态 `pending`,判据写死「`channel_audit --variant plus_event` 的 `unique_excess_t2` 累计 ≥10 数据日且 >0」。

- [ ] **Step 5: 全量回归 + Commit**

```bash
uv run --no-sync python -m pytest -q && uv run --no-sync ruff check autoresearch tests
git add .claude/skills/scan-market/STAGES.md docs/plans .superpowers/sdd/progress.md
git commit -m "docs(scan): Wave4 event 路接线文档 + 冒烟实录 + pr_20260724_001 取证判据"
```

---

## Self-Review(已跑)

1. **需求覆盖**:原 spec Wave 4「新闻召回路」的**可实现部分**(全市场事件驱动召回)由 T2/T3 覆盖;**LLM 抽取部分被实证判定为无用武之地**(端点自带 ts_code),明确记为不做并写明理由。附带修掉一条静默死了的新闻腿(T1)与判它生死的仪器(T4)。
2. **Placeholder 扫描**:无 TBD;T4 对 `channel_audit._load_day` 的改动给了明确接口(`--variant` flag + 读 `shadow/L1_channels_<name>.csv`),实现细节以现场既有 `_load_day` 结构为准。
3. **类型一致性**:`EVENT_COLS` 六列在 events/channels/l2_stratify/测试四处同名;`ev_pos` 的口径与 `catalyst_ledger._POS` 显式对齐(注释标明勿分叉)。
4. **纪律自查**:新路**默认不启用**并有测试锁死(`test_event_not_enabled_by_default`);判据、命令、门槛(≥10 日、`unique_excess_t2`)全部写死在 proposal 里,与 accumulation 当年被裁同口径——**防止"建好就用"绕过入场纪律**。
5. **已知不做**:`reversal_confirm` 无 L2 桶的既有欠账(改它会污染 event 路的影子对照);`stock_news_em` 的全市场覆盖问题(单票查询,无解,除非另找数据源)。
