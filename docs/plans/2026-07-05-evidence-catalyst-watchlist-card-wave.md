# 证据流+催化+观察单+卡片丰富化 wave — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 spec `docs/specs/2026-07-05-evidence-catalyst-watchlist-card-wave-design.md` 四个 workstream:A 影子组合成绩单 / B anns_d 断链修复+催化数据面 / C 观察单最后一公里 / D 决策卡输出侧丰富化,外加两项顺带修。

**Architecture:** 全部新信号 advisory、新参数默认关、新报告节 presence-gated(= parity 不破);确定性件(python)带合成无网络单测,LLM 件只改 playbook/agent 模板文本。三条数据流:催化事件(tushare 三端点→湖→L3 表/L4 简报→ledger 取证)、影子组合(买单/影子买单→lake daily→NAV 三线)、观察单 v2(分级状态机+日期锚+错过审计)。

**Tech Stack:** Python 3.12 + pandas + pyarrow(湖 parquet);pytest;tushare(经 `data.cache.get_or_fetch` 湖优先);Claude skill/agent markdown 模板。

## Global Constraints

- 一切命令用 `uv run --no-sync`,在仓库根目录跑(venv-only 依赖,勿裸 `python`/`pip`)。
- 新表列/新参数**默认关**(`cat_flag=False` 等),新报告节/行 **presence-gated**(staging/文件缺 → 不加)——现有 686 测试语义不得破。
- 新信号一律 **advisory**:不动 OW 三门、不动评级链路、不进 composite 权重。
- 机器契约行一字不动:`**Rating**`、`FINAL TRANSACTION PROPOSAL`、`**Rubric建议**`、`进入P4倾向`、`## 地形段`/`## 研判段`。
- 卡片 lint(`autoresearch/scan/self_review.py` 侧)**不加新规则**(D3:新段落是推荐模板段,非硬契约)。
- 测试全部合成 fixture、无网络(参照 `tests/scan/test_l3_dist_flag.py` / `tests/learning/test_buy_ledger.py` 风格);需要取数注入时走 `fetch_fn` 参数(参照 `l4_card.fetch_pledge`)。
- commit 中文 conventional(`feat(scan): …`/`fix(learning): …`),结尾带 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。
- `context/`、`reports/` 已 gitignore——运维回填产物不提交。
- 完成每个 task 后跑该 task 的测试;Task 12 跑全量 `uv run --no-sync python -m pytest -q`(当前基线 686 绿)。

---

### Task 1: B0 · anns_d 断链修复 —— 监管旗回退源

**背景**:当前 TUSHARE_TOKEN 无 `anns_d` 权限(实测),`context/scan/<date>/L3_news/*.json` 全为空列表 → `l3_table_md(reg_flag=True)` 的 `news_reg` 列永远空(监管旗聋)。修法:提取 `reg_hits_for_code`,L3_news 空/缺时回退读 `L3_webnews/<code>.json`(akshare 东财个股新闻,湖里有数据)。**情感列不回退**(`med_*` 列已承载 web 新闻情感,回退会双计)。

**Files:**
- Modify: `autoresearch/scan/agents/l3_news.py`(`reg_hits` 之后加 `reg_hits_for_code`)
- Modify: `autoresearch/scan/agents/l3_select.py:171-185`(`_reg` 内联函数改为调用新 helper)
- Test: `tests/scan/test_l3_news_fallback.py`(新建)

**Interfaces:**
- Produces: `l3_news.reg_hits_for_code(day_dir: Path, code: str) -> str`(Task 无后续依赖;l3_select 内部消费)

- [ ] **Step 1: 写失败测试**

```python
"""anns_d 断链修复:reg 监管旗 L3_news 优先、空/缺回退 L3_webnews。合成,无网络。

spec: docs/specs/2026-07-05-evidence-catalyst-watchlist-card-wave-design.md §WS-B0
"""
from __future__ import annotations

import json

from autoresearch.scan.agents.l3_news import reg_hits_for_code


def _put(day_dir, sub, code, items):
    d = day_dir / sub
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{code}.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


def test_fallback_to_webnews_when_anns_empty(tmp_path):
    _put(tmp_path, "L3_news", "000001", [])                              # anns_d 断链:空列表
    _put(tmp_path, "L3_webnews", "000001", [{"title": "关于收到问询函的公告", "ann_date": "2026-07-01"}])
    assert reg_hits_for_code(tmp_path, "000001") == "问询"


def test_anns_present_takes_priority(tmp_path):
    _put(tmp_path, "L3_news", "000002", [{"title": "立案调查进展", "ann_date": "2026-07-01"}])
    _put(tmp_path, "L3_webnews", "000002", [{"title": "关于收到问询函的公告", "ann_date": "2026-07-01"}])
    assert reg_hits_for_code(tmp_path, "000002") == "立案"               # 不混入 webnews


def test_both_missing_or_bad_json_empty(tmp_path):
    assert reg_hits_for_code(tmp_path, "000003") == ""
    (tmp_path / "L3_news").mkdir()
    (tmp_path / "L3_news" / "000004.json").write_text("{bad", encoding="utf-8")
    assert reg_hits_for_code(tmp_path, "000004") == ""
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_l3_news_fallback.py -q`
Expected: FAIL — `ImportError: cannot import name 'reg_hits_for_code'`

- [ ] **Step 3: 实现 `reg_hits_for_code`**(l3_news.py,`reg_hits` 函数之后插入)

```python
def reg_hits_for_code(day_dir: Path, code: str) -> str:
    """某票监管旗:L3_news(anns_d 公告)优先,空/缺回退 L3_webnews(东财个股新闻)。

    anns_d 无权限断链修复(spec 2026-07-05 wave §B0):公告线聋时监管词表退而扫新闻标题,
    有权限恢复后自动回到公告优先。坏 JSON/两处皆空 → ""(降级不抛)。
    """
    code6 = str(code).zfill(6)
    for sub in ("L3_news", "L3_webnews"):
        fp = Path(day_dir) / sub / f"{code6}.json"
        if not fp.exists():
            continue
        try:
            items = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — 坏 JSON 降级下一源
            continue
        if items:
            return reg_hits([a.get("title", "") for a in items])
    return ""
```

- [ ] **Step 4: l3_select 接线**——把 `l3_table_md` 里 reg_flag 分支的内联 `_reg`(l3_select.py 171-185 行)整段替换为:

```python
    if reg_flag and "code" in df.columns:
        from autoresearch.scan.agents.l3_news import reg_hits_for_code
        day_dir = (root or Path("context/scan")) / date
        df["news_reg"] = [reg_hits_for_code(day_dir, c) for c in df["code"]]
        cols = [*cols, "news_reg"]
        header += ["_⚠监管旗(news_reg):近 10 日公告命中 立案/问询/关注函/处罚/违规/诉讼/"
                   "监管/证监会/交易所。旗票论点**必须显式回应监管事项**,不得无视;独立检测器,"
                   "情感列口径不变(非利空词表变更)。_", ""]
```

(图例文案原样保留;删掉的 `_reg` 逻辑已被 helper 吸收,`news_dir`/`json` 局部引用一并清理——`l3_select.py` 顶部 `import json` 若再无他处使用则保留不动,勿顺手删。)

- [ ] **Step 5: 跑测试确认通过 + 既有 reg/dist 测试不破**

Run: `uv run --no-sync python -m pytest tests/scan/test_l3_news_fallback.py tests/scan/test_l3_dist_flag.py tests/scan/test_trap_flags.py -q`
Expected: 全 PASS(trap_flags 里 reg_flag 用例走 L3_news 有数据路径,优先级语义不变)

- [ ] **Step 6: Commit**

```bash
git add autoresearch/scan/agents/l3_news.py autoresearch/scan/agents/l3_select.py tests/scan/test_l3_news_fallback.py
git commit -m "fix(scan): 监管旗回退源——anns_d 断链时扫 L3_webnews 标题

实测 token 无 anns_d 权限,L3_news 全空 → reg_flag 上线起为聋。
reg_hits_for_code 公告优先/新闻回退;情感列不回退(med_* 已承载,防双计)。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: 数据病显性化 —— run_health 加 anns_empty_rate + northbound_probe

**Files:**
- Modify: `autoresearch/scan/health.py`(`nan_report` 之后加两个函数;`run_health` dict 加两键)
- Test: `tests/scan/test_health_probes.py`(新建)

**Interfaces:**
- Produces: `health.anns_empty_rate(scan_dir) -> float | None`;`health.northbound_probe(scan_dir) -> dict | None`;`run_health()` 返回 dict 新增键 `anns_empty_rate`、`northbound`

- [ ] **Step 1: 写失败测试**

```python
"""run_health 数据病探针:anns 空稿率 + northbound 通道空转读数。合成,无网络。

spec: 2026-07-05 wave §B0/顺带修 —— best-effort 降级必须配读数,数据病不许隐身。
"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.scan.health import anns_empty_rate, northbound_probe


def test_anns_empty_rate(tmp_path):
    assert anns_empty_rate(tmp_path) is None                     # 无 L3_news 目录 → None
    d = tmp_path / "L3_news"
    d.mkdir()
    (d / "000001.json").write_text("[]", encoding="utf-8")
    (d / "000002.json").write_text(json.dumps([{"title": "回购"}]), encoding="utf-8")
    assert anns_empty_rate(tmp_path) == 0.5
    (d / "000003.json").write_text("{bad", encoding="utf-8")     # 坏 JSON 记作空
    assert anns_empty_rate(tmp_path) == round(2 / 3, 3)


def test_northbound_probe(tmp_path):
    assert northbound_probe(tmp_path) is None                    # 无 recall staging → None
    pd.DataFrame([
        {"code": "000001", "recall_channels": "northbound|value", "hk_ratio": float("nan")},
        {"code": "000002", "recall_channels": "northbound", "hk_ratio": float("nan")},
        {"code": "000003", "recall_channels": "momentum", "hk_ratio": 1.2},
    ]).to_csv(tmp_path / "L1_recall_top1000.csv", index=False)
    nb = northbound_probe(tmp_path)
    assert nb == {"n": 2, "hk_nan": 1.0}                         # 北向召回票 hk 全 NaN = 空转坐实
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_health_probes.py -q`
Expected: FAIL — ImportError

- [ ] **Step 3: 实现**(health.py,`nan_report` 之后插入)

```python
def anns_empty_rate(scan_dir: Path) -> float | None:
    """L3_news 空稿率(=1.0 → anns_d 断链/无权限,公告情感与监管旗在裸奔)。无目录 → None。"""
    d = Path(scan_dir) / "L3_news"
    files = sorted(d.glob("*.json")) if d.is_dir() else []
    if not files:
        return None
    def _n(p: Path) -> int:
        try:
            v = json.loads(p.read_text(encoding="utf-8"))
            return len(v) if isinstance(v, list) else 0
        except Exception:  # noqa: BLE001 — 坏 JSON 记空
            return 0
    empty = sum(1 for p in files if _n(p) == 0)
    return round(empty / len(files), 3)


def northbound_probe(scan_dir: Path) -> dict | None:
    """northbound 召回通道空转读数:该路召回票数 + 其 hk_ratio NaN 率(=1.0 → quota 白占)。

    只取证不动结构(spec 2026-07-05 wave §顺带修);坐实后另走 proposal 人拍板。
    """
    df = _read(Path(scan_dir) / "L1_recall_top1000.csv")
    if df is None or "recall_channels" not in df.columns:
        return None
    sub = df[df["recall_channels"].astype(str).str.contains("northbound", na=False)]
    if not len(sub):
        return {"n": 0, "hk_nan": None}
    nanr = (round(float(pd.to_numeric(sub["hk_ratio"], errors="coerce").isna().mean()), 3)
            if "hk_ratio" in sub.columns else None)
    return {"n": int(len(sub)), "hk_nan": nanr}
```

再把 `run_health()` 返回 dict 里 `"nan_rates": rates, "degraded_fields": degraded,` 一行改为:

```python
            "nan_rates": rates, "degraded_fields": degraded,
            "anns_empty_rate": anns_empty_rate(scan_dir), "northbound": northbound_probe(scan_dir),
```

- [ ] **Step 4: 跑测试确认通过**(含既有 health 相关测试)

Run: `uv run --no-sync python -m pytest tests/scan/test_health_probes.py tests/scan -q -k "health or menu"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add autoresearch/scan/health.py tests/scan/test_health_probes.py
git commit -m "feat(scan): run_health 加 anns_empty_rate + northbound 空转探针

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: B1 · 催化三端点入湖 + l3_catalyst 聚合

**Files:**
- Modify: `autoresearch/data/endpoints.py`(公告类小节加 3 行)
- Create: `autoresearch/scan/agents/l3_catalyst.py`
- Test: `tests/scan/test_l3_catalyst.py`(新建)

**Interfaces:**
- Consumes: `data.cache.get_or_fetch(endpoint, params, today, fetch)`;`l3_news._trade_days_for(date, lookback_days)`;`data.tushare_source._code6(series)`
- Produces: `l3_catalyst.harvest_catalyst(date, codes, root=None, lookback_days=10, days=None, fetch_fn=None) -> pd.DataFrame`(列 `code,rep_impl,rep_plan,holder_in,holder_de,surv_n`,落 `<root>/<date>/L3_catalyst.csv`);`l3_catalyst.catalyst_counts(frames, want) -> pd.DataFrame`(纯函数);`l3_catalyst.cat_label(row: dict) -> str`

- [ ] **Step 1: endpoints.py 注册**——`"anns_d"` 行之后插入:

```python
    "stk_holdertrade": {"key": "date", "settle": "eod", "source": "tushare"},  # 股东增减持(ann_date;催化)
    "repurchase": {"key": "date", "settle": "eod", "source": "tushare"},       # 回购(ann_date;催化)
    "stk_surv": {"key": "date", "settle": "eod", "source": "tushare"},         # 机构调研(trade_date;催化)
```

(`_DATE_PARAM_KEYS = ("trade_date","date","ann_date","cal_date")` 已覆盖三端点的日期参数,无需改 cache.py。)

- [ ] **Step 2: 写失败测试**

```python
"""催化事件聚合(增减持/回购/调研)→ L3_catalyst.csv + cat 徽标。合成,fetch_fn 注入,无网络。

spec: 2026-07-05 wave §WS-B1/B2。07-05 实测三端点均有权限;07-03 病灶 30/30 卡"无明确催化"。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.scan.agents.l3_catalyst import cat_label, catalyst_counts, harvest_catalyst

_DATE = "2026-07-03"


def _fetch(endpoint: str, params: dict) -> pd.DataFrame:
    if endpoint == "stk_holdertrade":
        return pd.DataFrame([
            {"ts_code": "000001.SZ", "ann_date": params["ann_date"], "in_de": "IN"},
            {"ts_code": "000001.SZ", "ann_date": params["ann_date"], "in_de": "DE"},
            {"ts_code": "999999.SH", "ann_date": params["ann_date"], "in_de": "IN"},   # 非目标票,应被滤掉
        ])
    if endpoint == "repurchase":
        return pd.DataFrame([
            {"ts_code": "000001.SZ", "ann_date": params["ann_date"], "proc": "实施"},
            {"ts_code": "000002.SZ", "ann_date": params["ann_date"], "proc": "股东大会通过"},
        ])
    if endpoint == "stk_surv":
        return pd.DataFrame([{"ts_code": "000002.SZ", "trade_date": params["trade_date"]}])
    raise AssertionError(endpoint)


def test_harvest_and_counts(tmp_path):
    df = harvest_catalyst(_DATE, ["000001", "000002"], root=tmp_path,
                          days=["20260702", "20260703"], fetch_fn=_fetch)
    df = df.set_index("code")
    assert df.at["000001", "holder_in"] == 2 and df.at["000001", "holder_de"] == 2   # 2 天累计
    assert df.at["000001", "rep_impl"] == 2 and df.at["000001", "surv_n"] == 0
    assert df.at["000002", "rep_plan"] == 2 and df.at["000002", "surv_n"] == 2
    assert (tmp_path / _DATE / "L3_catalyst.csv").exists()                            # 落 staging


def test_cat_label():
    assert cat_label({"rep_impl": 1, "rep_plan": 0, "holder_in": 2, "holder_de": 1, "surv_n": 5}) \
        == "回购1(实施)·增持2·调研5·减持1"
    assert cat_label({"rep_impl": 0, "rep_plan": 1, "holder_in": 0, "holder_de": 0, "surv_n": 0}) \
        == "回购1(预案)"
    assert cat_label({"rep_impl": 0, "rep_plan": 0, "holder_in": 0, "holder_de": 0, "surv_n": 0}) == ""


def test_counts_pure_empty():
    out = catalyst_counts({"stk_holdertrade": [], "repurchase": [], "stk_surv": []}, {"000001"})
    assert list(out.columns) == ["code", "rep_impl", "rep_plan", "holder_in", "holder_de", "surv_n"]
    assert out.set_index("code").loc["000001"].sum() == 0
```

- [ ] **Step 3: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_l3_catalyst.py -q`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 4: 实现 `autoresearch/scan/agents/l3_catalyst.py`**

```python
#!/usr/bin/env python3
"""scan-market · 确定性催化事件面 —— 增减持/回购/机构调研 近10日计数(零 LLM,advisory)。

spec: docs/specs/2026-07-05-evidence-catalyst-watchlist-card-wave-design.md §WS-B1/B2。
07-03 病灶:30/30 卡"无明确催化"——不是判断弱,是探测盲(只有公告情感+日历)。本模块把
三个有权限端点(07-05 实测)聚成每票事件计数,进 L3 表 `cat` 列与 L4 简报(存在性≠方向,
禁则见消费端图例);alpha 取证在 catalyst_ledger,IC 过硬前不入 composite、不设门。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

# endpoint → (日期参数名, 计数规则见 catalyst_counts)
_ENDPOINTS = {"stk_holdertrade": "ann_date", "repurchase": "ann_date", "stk_surv": "trade_date"}
_COLS = ["code", "rep_impl", "rep_plan", "holder_in", "holder_de", "surv_n"]


def catalyst_counts(frames: dict[str, list[pd.DataFrame]], want: set[str]) -> pd.DataFrame:
    """{endpoint: [日帧,…]} → 每票事件计数帧(纯函数,可单测)。want=6位代码集合。"""
    from autoresearch.data.tushare_source import _code6
    acc = {c: dict.fromkeys(_COLS[1:], 0) for c in sorted(want)}

    def _rows(ep: str):
        for df in frames.get(ep, []):
            if df is None or not len(df) or "ts_code" not in df.columns:
                continue
            sub = df.assign(_c=_code6(df["ts_code"]))
            yield from sub[sub["_c"].isin(want)].to_dict("records")

    for r in _rows("stk_holdertrade"):
        key = "holder_in" if str(r.get("in_de", "")).upper() == "IN" else "holder_de"
        acc[r["_c"]][key] += 1
    for r in _rows("repurchase"):
        key = "rep_impl" if "实施" in str(r.get("proc", "")) else "rep_plan"
        acc[r["_c"]][key] += 1
    for r in _rows("stk_surv"):
        acc[r["_c"]]["surv_n"] += 1
    return pd.DataFrame([{"code": c, **v} for c, v in acc.items()], columns=_COLS)


def cat_label(row: dict) -> str:
    """事件计数 → 徽标(全零 → "")。顺序:回购(实施/预案)·增持·调研·减持。"""
    def _n(k: str) -> int:
        v = row.get(k, 0)
        try:
            return int(v) if v == v else 0        # NaN 安全
        except (TypeError, ValueError):
            return 0
    parts = []
    if _n("rep_impl"):
        parts.append(f"回购{_n('rep_impl')}(实施)")
    if _n("rep_plan"):
        parts.append(f"回购{_n('rep_plan')}(预案)")
    if _n("holder_in"):
        parts.append(f"增持{_n('holder_in')}")
    if _n("surv_n"):
        parts.append(f"调研{_n('surv_n')}")
    if _n("holder_de"):
        parts.append(f"减持{_n('holder_de')}")
    return "·".join(parts)


def harvest_catalyst(date: str, codes, root: Path | None = None, lookback_days: int = 10,
                     days: list[str] | None = None, fetch_fn=None) -> pd.DataFrame:
    """近 lookback_days 交易日三端点按日拉(湖优先)→ 计数 → 落 `<root>/<date>/L3_catalyst.csv`。

    best-effort:单日/单端点失败跳过(降级);days/fetch_fn 注入供离线测(fetch_fn 时绕湖直调)。
    """
    from autoresearch.data.cache import get_or_fetch
    from autoresearch.scan.agents.l3_news import _trade_days_for
    root = root or Path("context/scan")
    want = {str(c).zfill(6) for c in codes}
    days = days if days is not None else _trade_days_for(date, lookback_days)
    frames: dict[str, list[pd.DataFrame]] = {ep: [] for ep in _ENDPOINTS}
    for ep, dkey in _ENDPOINTS.items():
        for dd in days:
            try:
                df = (fetch_fn(ep, {dkey: dd}) if fetch_fn is not None
                      else get_or_fetch(ep, {dkey: dd}, today=date))
            except Exception:  # noqa: BLE001 — 无权限/限频 → 跳过该日(降级)
                continue
            frames[ep].append(df)
    out = catalyst_counts(frames, want)
    d = root / date
    d.mkdir(parents=True, exist_ok=True)
    out.to_csv(d / "L3_catalyst.csv", index=False)
    return out
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run --no-sync python -m pytest tests/scan/test_l3_catalyst.py -q`
Expected: PASS(3 个用例)

- [ ] **Step 6: Commit**

```bash
git add autoresearch/data/endpoints.py autoresearch/scan/agents/l3_catalyst.py tests/scan/test_l3_catalyst.py
git commit -m "feat(scan): 催化事件面——增减持/回购/调研三端点入湖+每票计数(advisory)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: B2 · L3 表 cat 列 + L4 简报催化行

**Files:**
- Modify: `autoresearch/scan/agents/l3_select.py`(`l3_table_md` 加 `cat_flag` 参数与分支)
- Modify: `autoresearch/scan/agents/l4_card.py`(`_pledge_mark` 之后加 `_cat_mark`;`compose_funnel_brief` 注入)
- Test: `tests/scan/test_l3_catalyst.py`(追加 2 个用例)

**Interfaces:**
- Consumes: Task 3 的 `L3_catalyst.csv` staging 与 `cat_label(row)`
- Produces: `l3_table_md(..., cat_flag: bool = False)`;简报行 `- **📣催化事件(近10日,事实)**:…`

- [ ] **Step 1: 追加失败测试**(test_l3_catalyst.py 末尾;`_mk`/`_row` 仿 test_l3_dist_flag.py)

```python
def _mk_scan(root, cat_rows):
    d = root / _DATE
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"code": "000001", "name": "甲", "industry": "电子", "composite": 80.0,
                   "main_net_ratio": 0.05, "pct_60d": 10.0, "pe": 30.0}]).to_csv(
        d / "L2_gbdt_top200.csv", index=False)
    pd.DataFrame(cat_rows).to_csv(d / "L3_catalyst.csv", index=False)
    return d


def test_l3_table_cat_flag_on_and_parity(tmp_path):
    from autoresearch.scan.agents.l3_select import l3_table_md
    _mk_scan(tmp_path, [{"code": "000001", "rep_impl": 1, "rep_plan": 0,
                         "holder_in": 2, "holder_de": 0, "surv_n": 3}])
    md = l3_table_md(_DATE, root=tmp_path, cat_flag=True)
    assert "cat" in md and "回购1(实施)·增持2·调研3" in md
    assert "📣催化列" in md and "减持≥2" in md                 # 图例 + 禁则
    assert "📣催化列" not in l3_table_md(_DATE, root=tmp_path)   # 默认关 = parity


def test_funnel_brief_cat_mark(tmp_path):
    from autoresearch.scan.agents.l4_card import compose_funnel_brief
    d = _mk_scan(tmp_path, [{"code": "000001", "rep_impl": 0, "rep_plan": 0,
                             "holder_in": 0, "holder_de": 2, "surv_n": 0}])
    pd.DataFrame([{"code": "000001", "name": "甲", "industry": "电子"}]).to_csv(
        d / "L1_recall_top1000.csv", index=False)
    brief = compose_funnel_brief("000001", d)
    assert "📣催化事件" in brief and "减持2" in brief
    (d / "L3_catalyst.csv").unlink()                             # presence-gated:无 staging 无行
    assert "📣催化事件" not in compose_funnel_brief("000001", d)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_l3_catalyst.py -q`
Expected: 新 2 用例 FAIL(`cat_flag` 未知参数 / 无催化行)

- [ ] **Step 3: l3_table_md 加 cat_flag**——签名改为:

```python
def l3_table_md(date: str, root: Path | None = None, delta: bool = False,
                shuffle_seed: int | None = None, sector_terrain: bool = False,
                dist_flag: bool = False, reg_flag: bool = False, cat_flag: bool = False) -> str:
```

docstring 末尾追加一段:

```python
    cat_flag=True:加 `cat` 列(近 10 日 回购/增持/调研/减持 事件计数徽标,staging
    `L3_catalyst.csv` 在才生效)+ 图例禁则——事件存在性≠方向确认(默认 False = 逐字 parity)。
    spec 2026-07-05 wave §B2。
```

reg_flag 分支之后(delta 分支之前)插入:

```python
    if cat_flag and "code" in df.columns:
        catp = (root or Path("context/scan")) / date / "L3_catalyst.csv"
        if catp.exists():
            from autoresearch.scan.agents.l3_catalyst import cat_label
            try:
                cf = pd.read_csv(catp, dtype={"code": str})
                cf["code"] = cf["code"].astype(str).str.zfill(6)
                lab = {r["code"]: cat_label(r) for r in cf.to_dict("records")}
            except Exception:  # noqa: BLE001 — 坏 staging 降级不加列
                lab = None
            if lab is not None:
                df["cat"] = [lab.get(str(c).zfill(6), "") for c in df["code"]]
                cols = [*cols, "cat"]
                header += ["_📣催化列(cat):近 10 日 回购/增持/机构调研/减持 事件计数(存在性"
                           "≠方向确认)。催化须与资金/基本面共振才可作论点支柱;**减持≥2 的票"
                           "论点必须显式回应**。_", ""]
```

- [ ] **Step 4: l4_card 加 `_cat_mark`**(`_pledge_mark` 之后):

```python
def _cat_mark(base: Path, code6: str) -> str:
    """催化事件行(presence-gated:`L3_catalyst.csv` 在且有非零计数才注)。spec 2026-07-05 §B2。"""
    p = base / "L3_catalyst.csv"
    if not p.exists():
        return ""
    try:
        from autoresearch.scan.agents.l3_catalyst import cat_label
        df = pd.read_csv(p, dtype={"code": str})
        df["code"] = df["code"].astype(str).str.zfill(6)
        sub = df[df["code"] == code6]
        lbl = cat_label(sub.iloc[0].to_dict()) if len(sub) else ""
    except Exception:  # noqa: BLE001 — 行可选,缺了不挡简报
        return ""
    if not lbl:
        return ""
    return (f"- **📣催化事件(近10日,事实)**:{lbl}(存在性≠方向确认;"
            f"与资金/基本面共振才可作论点支柱)")
```

`compose_funnel_brief` 里质押旗之后接线——把:

```python
    pm = _pledge_mark(base, code6)           # 质押旗:确定性预旗(pledge.csv 在才注,presence-gated)
    if pm:
        lines.append(pm)
```

替换为:

```python
    pm = _pledge_mark(base, code6)           # 质押旗:确定性预旗(pledge.csv 在才注,presence-gated)
    if pm:
        lines.append(pm)
    cm = _cat_mark(base, code6)              # 催化行:三端点事件计数(L3_catalyst.csv 在才注)
    if cm:
        lines.append(cm)
```

- [ ] **Step 5: 跑测试确认通过 + 表/简报既有测试回归**

Run: `uv run --no-sync python -m pytest tests/scan/test_l3_catalyst.py tests/scan/test_l3_dist_flag.py tests/scan/test_sentinel_tokens.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add autoresearch/scan/agents/l3_select.py autoresearch/scan/agents/l4_card.py tests/scan/test_l3_catalyst.py
git commit -m "feat(scan): L3 表 cat 催化列(默认关=parity)+ L4 简报催化事件行

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: B3 · catalyst_ledger 取证环 + prelude 接线

**Files:**
- Create: `autoresearch/learning/catalyst_ledger.py`
- Modify: `autoresearch/scan/prelude.py`(加 `_catalyst` 步 + `_ledgers` 扩)
- Test: `tests/learning/test_catalyst_ledger.py`(新建)

**Interfaces:**
- Consumes: `context/scan/<date>/L3_catalyst.csv` + `retro/attribution.csv`(列 `code,fwd_5_oc`)
- Produces: `catalyst_ledger.roll(scan_root=None) -> pd.DataFrame`(列 `date,n_flag,n_unflag,f5_flag,f5_unflag`);`render(df, min_n=30) -> list[str]`;`main()` 写 `reports/learning/catalyst_ledger.md`

- [ ] **Step 1: 写失败测试**

```python
"""催化旗票 vs 无旗票 fwd_5 对照(取证环;n<30 只记账不下结论)。合成,无网络。

spec: 2026-07-05 wave §WS-B3 —— IC 过硬前不入 composite、不设门。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.learning.catalyst_ledger import render, roll


def _mk_day(root, date):
    d = root / date
    (d / "retro").mkdir(parents=True)
    pd.DataFrame([
        {"code": "000001", "rep_impl": 1, "rep_plan": 0, "holder_in": 0, "holder_de": 0, "surv_n": 0},
        {"code": "000002", "rep_impl": 0, "rep_plan": 0, "holder_in": 0, "holder_de": 3, "surv_n": 0},
        {"code": "000003", "rep_impl": 0, "rep_plan": 0, "holder_in": 0, "holder_de": 0, "surv_n": 0},
    ]).to_csv(d / "L3_catalyst.csv", index=False)
    pd.DataFrame([
        {"code": "000001", "fwd_5_oc": 0.05},
        {"code": "000002", "fwd_5_oc": -0.02},
        {"code": "000003", "fwd_5_oc": 0.01},
    ]).to_csv(d / "retro" / "attribution.csv", index=False)


def test_roll_flag_vs_unflag(tmp_path):
    _mk_day(tmp_path, "2026-07-03")
    df = roll(tmp_path)
    assert len(df) == 1
    r = df.iloc[0]
    # 正催化旗 = rep_impl+rep_plan+holder_in+surv_n > 0 → 只有 000001;减持不算正催化
    assert r["n_flag"] == 1 and r["n_unflag"] == 2
    assert abs(r["f5_flag"] - 0.05) < 1e-9 and abs(r["f5_unflag"] - (-0.005)) < 1e-9


def test_render_thin_gate(tmp_path):
    _mk_day(tmp_path, "2026-07-03")
    text = "\n".join(render(roll(tmp_path)))
    assert "取证中" in text and "< 30" in text          # 样本薄 → 只记账,不下结论
    assert "催化旗" in text


def test_roll_empty(tmp_path):
    assert len(roll(tmp_path)) == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/learning/test_catalyst_ledger.py -q`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 实现 `autoresearch/learning/catalyst_ledger.py`**

```python
#!/usr/bin/env python3
"""催化取证 ledger —— 催化旗票 vs 无旗票的 fwd_5 对照(确定性,零 LLM)。

spec: 2026-07-05 wave §WS-B3。cat 列是 advisory 事件面;本 ledger 回答"带正催化事件的票
后市是否真的更好"。n(成熟对照)≥30 才可读数;IC 过硬(factor_lab 两半稳+符号一致)前
不入 composite、不设门——与 consensus 同姿势。

  uv run --no-sync python -m autoresearch.learning.catalyst_ledger  # → reports/learning/catalyst_ledger.md
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

_COLS = ["date", "n_flag", "n_unflag", "f5_flag", "f5_unflag"]
_POS = ["rep_impl", "rep_plan", "holder_in", "surv_n"]     # 正催化(减持不算)


def _day(d: Path) -> dict | None:
    cp, ap = d / "L3_catalyst.csv", d / "retro" / "attribution.csv"
    if not cp.exists() or not ap.exists():
        return None
    try:
        cat = pd.read_csv(cp, dtype={"code": str})
        attr = pd.read_csv(ap, dtype={"code": str})
    except Exception:  # noqa: BLE001
        return None
    if "fwd_5_oc" not in attr.columns or "code" not in cat.columns:
        return None
    cat["code"] = cat["code"].astype(str).str.zfill(6)
    attr["code"] = attr["code"].astype(str).str.zfill(6)
    pos_cols = [c for c in _POS if c in cat.columns]
    cat["_flag"] = cat[pos_cols].fillna(0).sum(axis=1) > 0 if pos_cols else False
    m = cat.merge(attr[["code", "fwd_5_oc"]], on="code", how="inner")
    m["fwd_5_oc"] = pd.to_numeric(m["fwd_5_oc"], errors="coerce")
    m = m.dropna(subset=["fwd_5_oc"])
    if not len(m):
        return None
    fl, un = m[m["_flag"]], m[~m["_flag"]]
    return {"date": d.name, "n_flag": int(len(fl)), "n_unflag": int(len(un)),
            "f5_flag": round(float(fl["fwd_5_oc"].mean()), 6) if len(fl) else None,
            "f5_unflag": round(float(un["fwd_5_oc"].mean()), 6) if len(un) else None}


def roll(scan_root: Path | str | None = None) -> pd.DataFrame:
    scan_root = Path(scan_root or "context/scan")
    if not scan_root.exists():
        return pd.DataFrame(columns=_COLS)
    rows = [r for d in sorted(p for p in scan_root.iterdir()
                              if p.is_dir() and p.name[:2] == "20")
            if (r := _day(d)) is not None]
    return pd.DataFrame(rows, columns=_COLS)


def render(df: pd.DataFrame, min_n: int = 30) -> list[str]:
    out = ["# 催化取证 ledger(催化旗票 vs 无旗票 fwd_5;advisory 事件面的前向对照)", ""]
    if df is None or not len(df):
        return out + ["_无现场(L3_catalyst.csv 或成熟 attribution 缺)_"]

    def f(x):
        return "—" if x is None or pd.isna(x) else f"{x * 100:+.2f}%"

    out += ["| 日期 | 旗票n | 无旗n | 旗fwd_5 | 无旗fwd_5 |", "|---|---|---|---|---|"]
    for r in df.itertuples(index=False):
        out.append(f"| {r.date} | {r.n_flag} | {r.n_unflag} | {f(r.f5_flag)} | {f(r.f5_unflag)} |")
    n = int(df["n_flag"].sum())
    if n < min_n:
        out += ["", f"- ⚠ **取证中**(旗票累计 n={n} < {min_n}):只记账不下结论;"
                    "IC 过硬前不入 composite、不设门。"]
    else:
        fl = pd.to_numeric(df["f5_flag"], errors="coerce").dropna()
        un = pd.to_numeric(df["f5_unflag"], errors="coerce").dropna()
        out += ["", f"- **汇总**(旗票 n={n}):旗 fwd_5 日均 {f(fl.mean())} vs 无旗 {f(un.mean())};"
                    "持续为正差 → 提 proposal(factor_lab IC 门验后再谈入线)。"]
    return out


def main() -> int:
    df = roll()
    out = Path("reports/learning/catalyst_ledger.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(render(df)) + "\n", encoding="utf-8")
    print(f"[catalyst_ledger] {len(df)} 日 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: prelude 接线**——`_watchlist` 与 `_menu` 之间加一步(`run_prelude` 内):

```python
    def _catalyst():
        import pandas as pd

        from autoresearch.scan.agents.l3_catalyst import harvest_catalyst
        p = scan_dir / "L2_gbdt_top200.csv"
        if not p.exists():
            return "跳过(无 L2 staging)"
        codes = pd.read_csv(p, dtype={"code": str})["code"].astype(str).str.zfill(6).tolist()
        df = harvest_catalyst(date, codes)
        pos = [c for c in ("rep_impl", "rep_plan", "holder_in", "surv_n") if c in df.columns]
        n = int((df[pos].fillna(0).sum(axis=1) > 0).sum()) if len(df) and pos else 0
        return f"催化旗 {n}/{len(df)} 只(回购/增持/调研)"
```

`all_steps` 列表改为(在 watchlist 后插入 catalyst):

```python
    all_steps = [("retro_refresh", _refresh), ("retro_pending", _pending),
                 ("consensus", _consensus), ("universe", _universe), ("calendar", _calendar),
                 ("watchlist", _watchlist), ("catalyst", _catalyst), ("menu", _menu),
                 ("ledgers", _ledgers)]
```

`_ledgers` 函数体改为(paper_nav 在 Task 7 才创建,本 task **只加 catalyst_ledger**,Task 7 Step 5 再升级):

```python
    def _ledgers():
        from autoresearch.learning import buy_ledger, catalyst_ledger, cross_calib, journal
        journal.main()
        buy_ledger.main()
        cross_calib.main()
        catalyst_ledger.main()
        return "journal + buy_ledger + cross_calib + catalyst 已刷新"
```

- [ ] **Step 5: 跑测试确认通过 + prelude 既有测试**

Run: `uv run --no-sync python -m pytest tests/learning/test_catalyst_ledger.py tests/scan/test_prelude.py -q`
Expected: PASS(test_prelude 若断言步骤名单,把 `catalyst` 补进期望列表)

- [ ] **Step 6: Commit**

```bash
git add autoresearch/learning/catalyst_ledger.py autoresearch/scan/prelude.py tests/learning/test_catalyst_ledger.py tests/scan/test_prelude.py
git commit -m "feat(learning): catalyst_ledger 取证环 + prelude 催化步/刷新接线

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: A2 · shadow_buys 影子买单记账

**Files:**
- Create: `autoresearch/learning/shadow_buys.py`
- Modify: `autoresearch/scan/assemble.py:857-861`(sector_ledger 块后加记账 hook)
- Test: `tests/learning/test_shadow_buys.py`(新建)

**Interfaces:**
- Consumes: `health.final_ratings(scan_dir)`;`l4_card.pick_opportunity_candidates(ratings, scan_dir, k)`;`assemble.gate_status(text)`(binding 门解析,已有共享函数)
- Produces: `shadow_buys.record(scan_dir, path="context/learning/shadow_buys.csv", k=3) -> int`(幂等);`shadow_buys.backfill(scan_root, path) -> int`;csv 列 `date,code,name,conviction,binding,close`

- [ ] **Step 1: 写失败测试**

```python
"""影子买单记账:每日 top-k Hold(conviction 序)入 csv,幂等;回填历史。合成,无网络。

spec: 2026-07-05 wave §WS-A2 —— "如果门不拦,系统最想买的 3 只";评级基率/NAV 影子线的米仓。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.learning.shadow_buys import backfill, record

# 门名与 ✓/✗ 必须紧邻(gate_status 解析:门名后一字符即判;真实卡格式如「主力真在✗」)
_CARD = ("# 决策卡\n\n**Rubric建议**: 6 维净分 +1/6 ｜ OW三门 主力真在✗·业绩真兑现✓·"
         "估值不透支✓ → **建议 Hold**\n**Rating**: Hold\nFINAL TRANSACTION PROPOSAL: **HOLD**\n")


def _mk_day(root, date, codes=("000001", "000002", "000003", "000004")):
    d = root / date
    (d / "details").mkdir(parents=True)
    pd.DataFrame([{"code": c, "name": f"N{c}", "conviction": 90 - i * 10}
                  for i, c in enumerate(codes)]).to_csv(d / "finalists.csv", index=False)
    for c in codes:
        (d / "details" / f"{c}.md").write_text(_CARD, encoding="utf-8")
    pd.DataFrame([{"code": c, "close": 10.0 + i} for i, c in enumerate(codes)]).to_csv(
        d / "L1_scored_full.csv", index=False)
    return d


def test_record_topk_and_idempotent(tmp_path):
    d = _mk_day(tmp_path / "scan", "2026-07-03")
    out = tmp_path / "shadow_buys.csv"
    assert record(d, path=out, k=3) == 3
    df = pd.read_csv(out, dtype={"code": str})
    assert list(df["code"]) == ["000001", "000002", "000003"]        # conviction 降序 top-3
    assert df.iloc[0]["close"] == 10.0 and "主力真在" in df.iloc[0]["binding"]
    assert record(d, path=out, k=3) == 0                             # 幂等
    assert len(pd.read_csv(out)) == 3


def test_backfill_walks_days(tmp_path):
    root = tmp_path / "scan"
    _mk_day(root, "2026-07-02")
    _mk_day(root, "2026-07-03")
    out = tmp_path / "shadow_buys.csv"
    assert backfill(scan_root=root, path=out) == 6
    assert len(pd.read_csv(out)) == 6
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/learning/test_shadow_buys.py -q`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 实现 `autoresearch/learning/shadow_buys.py`**

```python
#!/usr/bin/env python3
"""影子买单 —— 每日 conviction top-k Hold 的确定性记账(零 LLM,不改评级不进报告)。

spec: 2026-07-05 wave §WS-A2。语义:"如果门不拦,系统最想买的 k 只"。与机会成本红队正交
(红队=0买日 2 只 LLM 深核进观察单;本模块=每日纯记账广度)。消费端:paper_nav 影子线、
评级基率样本池。`真实线 − 影子线` = 门的价值的日频读数。

  uv run --no-sync python -m autoresearch.learning.shadow_buys   # 回填全部历史 scan 日
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

_COLS = ["date", "code", "name", "conviction", "binding", "close"]
_PATH = Path("context/learning/shadow_buys.csv")


def _load(path: Path) -> pd.DataFrame:
    if not Path(path).exists():
        return pd.DataFrame(columns=_COLS)
    df = pd.read_csv(path, dtype={"code": str}).fillna("")
    df["code"] = df["code"].astype(str).str.zfill(6)
    return df


def _binding(scan_dir: Path, code: str) -> str:
    """卡片 OW 三门里 ✗ 的门名(压评级的那道);解析失败/无 → ""。

    注意 `assemble.gate_status` 语义:返回 {门: **是否✗失守**}(True=失守),非"是否通过"。
    """
    p = Path(scan_dir) / "details" / f"{code}.md"
    if not p.exists():
        return ""
    try:
        from autoresearch.scan.assemble import gate_status
        gates = gate_status(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return ""
    if not gates:
        return ""
    return "|".join(k for k, failed in gates.items() if failed)


def record(scan_dir: Path | str, path: Path | str = _PATH, k: int = 3) -> int:
    """该 scan 日 top-k Hold(L3 conviction 序)入账;(date,code) 幂等。返回新增行数。"""
    from autoresearch.scan.agents.l4_card import pick_opportunity_candidates
    from autoresearch.scan.health import final_ratings
    scan_dir, path = Path(scan_dir), Path(path)
    ratings = final_ratings(scan_dir)
    picks = pick_opportunity_candidates(ratings, scan_dir, k=k)
    if not picks:
        return 0
    date = scan_dir.name
    fin, closes = {}, {}
    fp, lp = scan_dir / "finalists.csv", scan_dir / "L1_scored_full.csv"
    if fp.exists():
        f = pd.read_csv(fp, dtype={"code": str})
        f["code"] = f["code"].astype(str).str.zfill(6)
        fin = {r["code"]: r for r in f.to_dict("records")}
    if lp.exists():
        l1 = pd.read_csv(lp, dtype={"code": str})
        if {"code", "close"} <= set(l1.columns):
            closes = dict(zip(l1["code"].astype(str).str.zfill(6),
                              pd.to_numeric(l1["close"], errors="coerce"), strict=False))
    old = _load(path)
    seen = set(zip(old["date"], old["code"], strict=False)) if len(old) else set()
    rows = []
    for code in picks:
        if (date, code) in seen:
            continue
        fr = fin.get(code, {})
        cl = closes.get(code)
        rows.append({"date": date, "code": code, "name": fr.get("name", ""),
                     "conviction": fr.get("conviction", ""), "binding": _binding(scan_dir, code),
                     "close": None if cl is None or pd.isna(cl) else float(cl)})
    if not rows:
        return 0
    out = pd.concat([old, pd.DataFrame(rows)], ignore_index=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False)
    return len(rows)


def backfill(scan_root: Path | str = "context/scan", path: Path | str = _PATH) -> int:
    """对全部历史 scan 日 record(幂等)——上线即让影子线有 13 日底仓数据。"""
    scan_root = Path(scan_root)
    if not scan_root.exists():
        return 0
    return sum(record(d, path=path)
               for d in sorted(p for p in scan_root.iterdir()
                               if p.is_dir() and p.name[:2] == "20"))


def main() -> int:
    n = backfill()
    print(f"[shadow_buys] 回填 {n} 行 → {_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: assemble 记账 hook**——`run()` 里 sector_ledger 块(assemble.py:857-861)后追加:

```python
    with contextlib.suppress(Exception):               # 影子买单记账(spec 2026-07-05 wave §A2,失败不阻发布)
        from autoresearch.learning.shadow_buys import record as _shadow_record
        n_sh = _shadow_record(scan_dir)
        if n_sh:
            print(f"[shadow_buys] 记 {n_sh} 只影子买单 → context/learning/shadow_buys.csv")
```

- [ ] **Step 5: 跑测试确认通过 + assemble 回归**

Run: `uv run --no-sync python -m pytest tests/learning/test_shadow_buys.py tests/scan/test_assemble.py -q`
Expected: PASS(assemble 测试用 tmp scan_dir,record 读不到 finalists 也安全返回 0)

- [ ] **Step 6: Commit**

```bash
git add autoresearch/learning/shadow_buys.py autoresearch/scan/assemble.py tests/learning/test_shadow_buys.py
git commit -m "feat(learning): 影子买单记账——每日 top-3 Hold 入 csv(评级基率/NAV 影子线米仓)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: A1 · paper_nav 三线净值

**Files:**
- Create: `autoresearch/learning/paper_nav.py`
- Modify: `autoresearch/scan/prelude.py`(`_ledgers` 加 paper_nav)
- Modify: `autoresearch/scan/assemble.py:644-645`(观察单节前插 NAV 一行,presence-gated)
- Test: `tests/learning/test_paper_nav.py`(新建)

**Interfaces:**
- Consumes: `context/lake/daily/<YYYYMMDD>.parquet`(列 ts_code/open/close/pct_chg);`health.final_ratings`;Task 6 的 `shadow_buys.csv`
- Produces: `paper_nav.simulate(signals, prices, days, slot=0.10, hold=10) -> tuple[pd.Series, list[str]]`(纯函数);`paper_nav.main()` 写 `reports/learning/paper_nav.md` + `reports/learning/paper_nav_summary.txt`(单行,assemble 消费)

- [ ] **Step 1: 写失败测试**

```python
"""paper_nav 事件组合模拟:固定 10% 槽/持 10 日/次日开盘进出;三线渲染。合成,无网络。

spec: 2026-07-05 wave §WS-A1。规则零判断可复现;信号日非交易日(06-19 孤儿键)跳过。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.learning.paper_nav import market_nav_from_returns, render, simulate

_DAYS = ["20260701", "20260702", "20260703"]


def test_simulate_one_signal_math():
    # 信号 07-01 → 07-02 开盘 10 建仓(10% 槽) → hold=1 → 07-03 开盘 11 平仓 = 槽赚 10% = NAV +1%
    prices = {("20260702", "000001"): (10.0, 11.0), ("20260703", "000001"): (11.0, 12.0)}
    nav, skipped = simulate([{"date": "2026-07-01", "code": "000001"}], prices, _DAYS, hold=1)
    assert abs(nav.iloc[0] - 1.0) < 1e-9
    assert abs(nav.iloc[1] - 1.01) < 1e-9          # 收盘 11 估值:0.9 + 0.01*11
    assert abs(nav.iloc[2] - 1.01) < 1e-9          # 开盘 11 平仓落袋
    assert skipped == []


def test_simulate_orphan_and_missing_price():
    prices = {("20260702", "000001"): (10.0, 10.0)}
    nav, skipped = simulate([{"date": "2026-06-19", "code": "000001"},      # 非交易日 → 跳过
                             {"date": "2026-07-02", "code": "000009"}],     # 入场日无价 → 跳过
                            prices, _DAYS, hold=1)
    assert (nav == 1.0).all()
    assert len(skipped) == 2 and "孤儿" in skipped[0]


def test_market_nav_and_render():
    mkt = market_nav_from_returns([0.01, -0.02, 0.0], _DAYS)
    assert abs(mkt.iloc[1] - 1.01 * 0.98) < 1e-9
    flat = pd.Series([1.0] * 3, index=_DAYS)
    text = "\n".join(render(_DAYS, flat, flat, mkt, n_real=1, n_shadow=3, skipped=[]))
    assert "真实" in text and "影子" in text and "市场" in text and "20260703" in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/learning/test_paper_nav.py -q`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 实现 `autoresearch/learning/paper_nav.py`**

```python
#!/usr/bin/env python3
"""影子组合成绩单 —— 真实/影子/市场三条 NAV(确定性,零 LLM,零新端点)。

spec: 2026-07-05 wave §WS-A1。规则(零判断可复现):每笔买单信号日**次日开盘**建仓,固定占
当时 NAV 的 10% 槽;持有 10 个交易日后次日开盘平仓(无价顺延);无持仓=现金。三条线:
真实(≥OW 买单,buy_ledger 同源)/ 影子(shadow_buys.csv)/ 市场(全市场等权日收益,
与 zero_buy_ledger 口径同族)。`真实 − 影子` = 门的价值。涨跌停可成交性不模拟(诚实局限)。

  uv run --no-sync python -m autoresearch.learning.paper_nav   # → reports/learning/paper_nav.md
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

_LAKE_DAILY = Path("context/lake/daily")
_START = "20260618"          # 首个 scan 日;之前的湖数据不进成绩单


def trade_days(start: str = _START, lake: Path | None = None) -> list[str]:
    lake = Path(lake or _LAKE_DAILY)
    if not lake.exists():
        return []
    return sorted(p.stem for p in lake.glob("*.parquet")
                  if len(p.stem) == 8 and p.stem.isdigit() and p.stem >= start)


def load_prices(codes: set[str], days: list[str], lake: Path | None = None) -> dict:
    """{(day, code6): (open, close)}——只读涉及票,NaN → None。"""
    from autoresearch.data.tushare_source import _code6
    lake = Path(lake or _LAKE_DAILY)
    want = {str(c).zfill(6) for c in codes}
    out: dict[tuple[str, str], tuple[float | None, float | None]] = {}
    if not want:
        return out
    for d in days:
        p = lake / f"{d}.parquet"
        if not p.exists():
            continue
        try:
            df = pd.read_parquet(p, columns=["ts_code", "open", "close"])
        except Exception:  # noqa: BLE001 — 坏分区跳过
            continue
        df = df.assign(_c=_code6(df["ts_code"]))
        for r in df[df["_c"].isin(want)].to_dict("records"):
            o = None if pd.isna(r["open"]) else float(r["open"])
            c = None if pd.isna(r["close"]) else float(r["close"])
            out[(d, r["_c"])] = (o, c)
    return out


def simulate(signals: list[dict], prices: dict, days: list[str],
             slot: float = 0.10, hold: int = 10) -> tuple[pd.Series, list[str]]:
    """事件组合模拟(纯函数)。signals=[{date, code}](date 兼容 YYYY-MM-DD / YYYYMMDD)。

    次日开盘建仓(slot×当时NAV,现金不足取剩余);exit=entry 后第 hold 个交易日开盘
    (无 open 顺延);持仓按最新可得 close 估值(停牌沿用)。信号日非交易日 → 跳过并记行。
    """
    idx = {d: i for i, d in enumerate(days)}
    entries: dict[int, list[str]] = {}
    skipped: list[str] = []
    for s in signals:
        d = str(s["date"]).replace("-", "")
        code = str(s["code"]).zfill(6)
        if d not in idx:
            skipped.append(f"{d} {code}(信号日非交易日,孤儿键跳过)")
            continue
        i = idx[d] + 1
        if i >= len(days):
            skipped.append(f"{d} {code}(次日未到,待成熟)")
            continue
        entries.setdefault(i, []).append(code)
    cash, nav = 1.0, 1.0
    pos: list[dict] = []
    navs: list[float] = []
    for i, d in enumerate(days):
        keep = []
        for p in pos:                                     # ① 到期平仓(无 open 顺延)
            o = prices.get((d, p["code"]), (None, None))[0]
            if p["exit_i"] <= i and o is not None:
                cash += p["shares"] * o
            else:
                keep.append(p)
        pos = keep
        for code in entries.get(i, ()):                   # ② 建仓
            o = prices.get((d, code), (None, None))[0]
            if o is None:
                skipped.append(f"{d} {code}(入场日无价,跳过)")
                continue
            cost = min(slot * nav, cash)
            if cost <= 1e-12:
                skipped.append(f"{d} {code}(现金槽满,跳过)")
                continue
            cash -= cost
            pos.append({"code": code, "shares": cost / o, "exit_i": i + hold, "last_close": o})
        mv = 0.0
        for p in pos:                                     # ③ 收盘估值(停牌沿用 last_close)
            c = prices.get((d, p["code"]), (None, None))[1]
            if c is not None:
                p["last_close"] = c
            mv += p["shares"] * p["last_close"]
        nav = cash + mv
        navs.append(round(nav, 6))
    return pd.Series(navs, index=list(days), name="nav"), skipped


def market_nav_from_returns(rets: list[float], days: list[str]) -> pd.Series:
    nav, navs = 1.0, []
    for r in rets:
        nav *= 1 + r
        navs.append(round(nav, 6))
    return pd.Series(navs, index=list(days), name="mkt")


def market_nav(days: list[str], lake: Path | None = None) -> pd.Series:
    """全市场等权日收益累乘(daily.pct_chg 均值;缺分区记 0)。"""
    lake = Path(lake or _LAKE_DAILY)
    rets = []
    for d in days:
        p = lake / f"{d}.parquet"
        r = 0.0
        if p.exists():
            try:
                s = pd.to_numeric(pd.read_parquet(p, columns=["pct_chg"])["pct_chg"],
                                  errors="coerce").dropna()
                r = float(s.mean()) / 100.0 if len(s) else 0.0
            except Exception:  # noqa: BLE001
                r = 0.0
        rets.append(r)
    return market_nav_from_returns(rets, days)


def real_signals(scan_root: Path | str | None = None) -> list[dict]:
    """≥OW 买单信号(verify 折回后,与 buy_ledger 同口径)。"""
    from autoresearch.scan.health import final_ratings
    scan_root = Path(scan_root or "context/scan")
    sig: list[dict] = []
    if not scan_root.exists():
        return sig
    for d in sorted(p for p in scan_root.iterdir() if p.is_dir() and p.name[:2] == "20"):
        sig += [{"date": d.name, "code": c} for c, r in final_ratings(d).items()
                if r in ("Buy", "Overweight")]
    return sig


def shadow_signals(path: Path | str = "context/learning/shadow_buys.csv") -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    df = pd.read_csv(p, dtype={"code": str})
    return [{"date": r["date"], "code": str(r["code"]).zfill(6)} for r in df.to_dict("records")]


def render(days: list[str], real: pd.Series, shadow: pd.Series, mkt: pd.Series,
           n_real: int, n_shadow: int, skipped: list[str]) -> list[str]:
    out = ["# 影子组合成绩单(paper NAV;10% 固定槽·持10交易日·次日开盘进出)", "",
           "| 日期 | 真实线 | 影子线 | 市场等权 |", "|---|---|---|---|"]
    out += [f"| {d} | {real[d]:.4f} | {shadow[d]:.4f} | {mkt[d]:.4f} |" for d in days]
    if len(days):
        last = days[-1]
        out += ["", f"- **截至 {last}**:真实 {real[last] - 1:+.2%}({n_real} 笔)"
                    f" vs 影子 {shadow[last] - 1:+.2%}({n_shadow} 笔)"
                    f" vs 市场 {mkt[last] - 1:+.2%};`真实 − 影子` = 门的价值。"]
    if skipped:
        out += ["", "## 未入组信号"] + [f"- {s}" for s in skipped]
    out += ["", "_涨跌停/停牌可成交性未模拟;仅供研究,非投资建议。_"]
    return out


def summary_line(days, real, shadow, mkt, n_real, n_shadow) -> str:
    if not len(days):
        return ""
    last = days[-1]
    return (f"**📈 影子组合成绩单**(起 {days[0]}):真实 {real[last] - 1:+.2%}({n_real}笔)"
            f" vs 影子 {shadow[last] - 1:+.2%}({n_shadow}笔) vs 市场等权 {mkt[last] - 1:+.2%}"
            f"——`真实−影子`=门的价值(明细 reports/learning/paper_nav.md)")


def main() -> int:
    days = trade_days()
    outp = Path("reports/learning/paper_nav.md")
    outp.parent.mkdir(parents=True, exist_ok=True)
    if not days:
        outp.write_text("# 影子组合成绩单\n\n_湖 daily 分区缺,无法结算_\n", encoding="utf-8")
        print("[paper_nav] 湖 daily 缺 → 空稿")
        return 0
    rs, ss = real_signals(), shadow_signals()
    codes = {s["code"] for s in rs} | {s["code"] for s in ss}
    prices = load_prices(codes, days)
    real, sk1 = simulate(rs, prices, days)
    shadow, sk2 = simulate(ss, prices, days)
    mkt = market_nav(days)
    outp.write_text("\n".join(render(days, real, shadow, mkt, len(rs), len(ss), sk1 + sk2)) + "\n",
                    encoding="utf-8")
    line = summary_line(days, real, shadow, mkt, len(rs), len(ss))
    Path("reports/learning/paper_nav_summary.txt").write_text(line + "\n", encoding="utf-8")
    print(f"[paper_nav] {len(days)} 日 × (真实{len(rs)}/影子{len(ss)}) → {outp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run --no-sync python -m pytest tests/learning/test_paper_nav.py -q`
Expected: PASS

- [ ] **Step 5: prelude `_ledgers` 加 paper_nav**(Task 5 已留位)——函数体改为:

```python
    def _ledgers():
        from autoresearch.learning import buy_ledger, catalyst_ledger, cross_calib, journal, paper_nav
        journal.main()
        buy_ledger.main()
        cross_calib.main()
        catalyst_ledger.main()
        paper_nav.main()
        return "journal + buy_ledger + cross_calib + catalyst + paper_nav 已刷新"
```

- [ ] **Step 6: assemble summary 一行**。**与 spec 的一处有意偏差**:spec 写"嵌『组合视角』节",实施放在**观察单节之前**(成绩单是读者最先要看的读数,且此处有逐字锚点可精确插入;组合视角节由 `_portfolio_note`/`_position_overlay` 动态拼装无稳定锚)。build_summary 里观察单块之前(assemble.py:644),把:

```python
    # ── 观察单日检(上移:触发/临近是读者最先要看的可操作项,别压在行业研判之下)──
    ws = scan_dir / "watchlist_status.csv"
```

替换为:

```python
    # ── 影子组合成绩单一行(spec 2026-07-05 wave §A1;presence-gated:文件缺 → 不加)──
    pn = Path("reports/learning/paper_nav_summary.txt")
    if pn.exists():
        try:
            nav_line = pn.read_text(encoding="utf-8").strip()
        except Exception:  # noqa: BLE001
            nav_line = ""
        if nav_line:
            out += [nav_line, ""]

    # ── 观察单日检(上移:触发/临近是读者最先要看的可操作项,别压在行业研判之下)──
    ws = scan_dir / "watchlist_status.csv"
```

- [ ] **Step 7: 回归 + 实跑回填验证(真数据)**

Run:
```bash
uv run --no-sync python -m pytest tests/learning tests/scan/test_prelude.py tests/scan/test_assemble.py -q
uv run --no-sync python -m autoresearch.learning.shadow_buys
uv run --no-sync python -m autoresearch.learning.paper_nav
head -8 reports/learning/paper_nav.md && cat reports/learning/paper_nav_summary.txt
```
Expected: 测试 PASS;shadow_buys 回填 ~30+ 行(13 scan 日 × ≤3);paper_nav.md 出三线表;summary 单行含"真实/影子/市场"(真实线应 ≈ 微偏离 1.0——只有 06-22 一笔可入组,06-19 五笔走孤儿跳过行)。

- [ ] **Step 8: Commit**

```bash
git add autoresearch/learning/paper_nav.py autoresearch/scan/prelude.py autoresearch/scan/assemble.py tests/learning/test_paper_nav.py
git commit -m "feat(learning): paper_nav 三线净值(真实/影子/市场)+ summary 一行注入

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: C1+C2+C3 · watchlist v2(提醒态/by_date/错过审计)

**Files:**
- Modify: `autoresearch/scan/watchlist.py`(状态机/词表/列/渲染/🆕Δ/CLI)
- Modify: `autoresearch/learning/watchlist_ledger.py`(加 born→今 巡检节)
- Test: `tests/scan/test_watchlist.py`(追加用例;既有用例不改——k=1 仍"临近")
- Test: `tests/learning/test_watchlist_ledger.py`(追加 1 用例)

**Interfaces:**
- Produces: conds 新 kind `{"kind":"by_date","date":"YYYY-MM-DD","text":…}`;`check()` 输出新列 `k,n,since_born,fire`;新状态 `提醒(k/n)`(k≥2);`WATCHLIST_COLS` 尾加 `born_price`;CLI `python -m autoresearch.scan.watchlist backfill|migrate`
- Consumes: `context/lake/daily/<YYYYMMDD>.parquet`(backfill born_price)

- [ ] **Step 1: 追加失败测试**(tests/scan/test_watchlist.py 末尾)

```python
def test_check_remind_gradient_and_by_date():
    wl = pd.DataFrame([
        _wl("000010", [{"kind": "close_above", "value": 10}, {"kind": "ma_bull"},
                       {"kind": "money_pos"}]),                                   # 2/3 yes → 提醒(2/3)
        _wl("000011", [{"kind": "close_above", "value": 10},
                       {"kind": "by_date", "date": "2026-07-04", "text": "中报"}]),  # 机判全 yes+日期锚 → 触发(待人工项)
    ])
    l1 = _l1([
        {"code": "000010", "close": 11.0, "ma_bull": 1, "main_net_ratio": -0.1, "cmf_20": 0.1},
        {"code": "000011", "close": 11.0, "ma_bull": 1, "main_net_ratio": 0.1, "cmf_20": 0.1},
    ])
    st = check(wl, l1, "2026-07-02").set_index("code")
    assert st.at["000010", "status"] == "提醒(2/3)"
    assert st.at["000010", "k"] == 2 and st.at["000010", "n"] == 3
    assert st.at["000011", "status"] == "触发(待人工项)"
    assert "by_date:2026-07-04(⏰临期)" in st.at["000011", "detail"]      # T-3 内标临期
    st2 = check(wl, l1, "2026-07-06").set_index("code")
    assert "⏰已到期待确认" in st2.at["000011", "detail"]


def test_since_born_and_fire():
    row = _wl("000012", [{"kind": "ma_bull"}])
    row["born_price"] = "10.0"
    wl = pd.DataFrame([row])
    l1 = _l1([{"code": "000012", "close": 12.0, "ma_bull": 0,
               "main_net_ratio": 0.1, "cmf_20": 0.1}])
    st = check(wl, l1, "2026-07-02").set_index("code")
    assert abs(st.at["000012", "since_born"] - 0.20) < 1e-9
    assert bool(st.at["000012", "fire"])                                  # +20% 未触发 → 🔥
    s = render_watchlist_block(check(wl, l1, "2026-07-02"))
    assert "🔥" in s and "+20%" in s and "stock-research lite" in s        # C4 文案同步换名


def test_backfill_born_price_from_lake(tmp_path):
    import pandas as _pd

    from autoresearch.scan.watchlist import backfill_born_price
    lake = tmp_path / "daily"
    lake.mkdir()
    _pd.DataFrame([{"ts_code": "300476.SZ", "open": 300.0, "close": 310.0}]).to_parquet(
        lake / "20260630.parquet", index=False)
    wl_path = tmp_path / "watchlist.csv"
    _pd.DataFrame([{**_wl("300476", [{"kind": "ma_bull"}]), "born": "2026-06-30"}]).to_csv(
        wl_path, index=False)
    assert backfill_born_price(path=wl_path, lake=lake) == 1
    wl = load_watchlist(wl_path)
    assert float(wl.iloc[0]["born_price"]) == 310.0


def test_mark_new_vs_prev_day():
    from autoresearch.scan.watchlist import mark_new
    today = pd.DataFrame([{"code": "000001", "k": 2, "n": 3}, {"code": "000002", "k": 1, "n": 2}])
    prev = pd.DataFrame([{"code": "000001", "k": 1, "n": 3}, {"code": "000002", "k": 1, "n": 2}])
    out = mark_new(today, prev).set_index("code")
    assert bool(out.at["000001", "new_k"]) and not bool(out.at["000002", "new_k"])
    out2 = mark_new(today, None).set_index("code")            # 无前日 → 全 False(防首日全🆕噪声)
    assert not out2["new_k"].any()


def test_migrate_manual_dates_to_by_date(tmp_path):
    import json as _json

    import pandas as _pd

    from autoresearch.scan.watchlist import migrate_by_date
    row = _wl("000013", [{"kind": "money_pos"},
                         {"kind": "manual", "text": "08-29中报净利同比转正"}])
    wl_path = tmp_path / "watchlist.csv"
    _pd.DataFrame([row]).to_csv(wl_path, index=False)
    assert migrate_by_date(path=wl_path) == 1
    conds = _json.loads(load_watchlist(wl_path).iloc[0]["conds"])
    bd = [c for c in conds if c["kind"] == "by_date"]
    assert bd and bd[0]["date"] == "2026-08-29" and "中报" in bd[0]["text"]
    assert migrate_by_date(path=wl_path) == 0                             # 幂等
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_watchlist.py -q`
Expected: 新 4 用例 FAIL(提醒态/by_date/backfill/migrate 未实现);旧 5 用例 PASS

- [ ] **Step 3: 实现 watchlist.py v2**。逐处改:

3a. 常量:

```python
WATCHLIST_COLS = ("code", "name", "born", "expiry", "source", "narrative",
                  "conds", "invalidation", "note", "born_price")
_FIRE_TH = 0.15     # 未触发已 +15% → 🔥 错过审计旗
```

删除 `_STATUS_ORDER`,以函数替代(放 `_EXPIRY_DAYS` 之后):

```python
def _status_rank(s: str) -> int:
    s = str(s)
    if s.startswith("触发(待人工项)"):
        return 1
    if s.startswith("触发"):
        return 0
    if s.startswith("提醒"):
        return 2
    return {"临近": 3, "待触发": 4, "失效": 5}.get(s, 9)
```

3b. `_eval_cond` 在 `if kind == "manual"` 之后加:

```python
    if kind == "by_date":
        return "manual"          # 日期锚人工项:机器不判真伪,只在 _label 标 ⏰临期/到期
```

3c. `_label` 换签名并加分支:

```python
def _label(cond: dict, date: str | None = None) -> str:
    k = cond.get("kind", "?")
    if k in ("close_above", "close_below"):
        return f"{k}:{cond.get('value')}"
    if k == "manual":
        return f"manual:{cond.get('text', '')}"
    if k == "by_date":
        due, txt = str(cond.get("date", "")), cond.get("text", "")
        mark = ""
        if date and due:
            if date > due:
                mark = "(⏰已到期待确认)"
            elif (pd.Timestamp(due) - pd.Timestamp(date)).days <= 3:
                mark = "(⏰临期)"
        return f"by_date:{due}{mark}:{txt}"
    return k
```

3d. `check()` 整函数替换为:

```python
def check(wl: pd.DataFrame, l1_full: pd.DataFrame, date: str) -> pd.DataFrame:
    """逐条目判定 → [code,name,status,detail,narrative,born,expiry,k,n,since_born,fire]。纯函数。

    状态梯度(spec 2026-07-05 wave §C1):机判全 yes=触发(带人工项则待人工);k≥2=提醒(k/n);
    k=1=临近;k=0=待触发;invalidation/过期=失效。by_date/manual 不计入机判分母。
    since_born=close/born_price−1(错过审计);fire=未触发且 since_born≥+15%。
    """
    l1 = l1_full.copy()
    if "code" in l1.columns:
        l1["code"] = l1["code"].astype(str).str.zfill(6)
        l1 = l1.set_index("code")
    out = []
    for _, r in wl.iterrows():
        code = str(r["code"]).zfill(6)
        row = l1.loc[code] if code in l1.index else None
        conds = json.loads(r.get("conds") or "[]")
        inval = json.loads(r.get("invalidation") or "[]")
        verdicts = [(c, _eval_cond(c, row)) for c in conds]
        inval_hit = any(_eval_cond(c, row) == "yes" for c in inval)
        expired = bool(r.get("expiry")) and date > str(r.get("expiry"))
        machine = [v for c, v in verdicts if c.get("kind") not in ("manual", "by_date")]
        has_manual = any(c.get("kind") in ("manual", "by_date") for c, _ in verdicts)
        k, n = sum(1 for v in machine if v == "yes"), len(machine)
        if inval_hit or expired:
            status = "失效"
        elif machine and k == n:
            status = "触发(待人工项)" if has_manual else "触发"
        elif k >= 2:
            status = f"提醒({k}/{n})"
        elif k == 1:
            status = "临近"
        else:
            status = "待触发"
        bp = pd.to_numeric(pd.Series([r.get("born_price")]), errors="coerce").iloc[0]
        close = None
        if row is not None:
            close = pd.to_numeric(pd.Series([row.get("close")]), errors="coerce").iloc[0]
        since = (round(float(close) / float(bp) - 1.0, 4)
                 if bp and not pd.isna(bp) and close is not None and not pd.isna(close) else None)
        fire = bool(since is not None and since >= _FIRE_TH and not status.startswith("触发"))
        detail = ";".join(f"{_label(c, date)}={v}" for c, v in verdicts) or "(无机判条件)"
        out.append({"code": code, "name": r.get("name", ""), "status": status, "detail": detail,
                    "narrative": r.get("narrative", ""), "born": r.get("born", ""),
                    "expiry": r.get("expiry", ""), "k": k, "n": n,
                    "since_born": since, "fire": fire})
    return pd.DataFrame(out, columns=["code", "name", "status", "detail", "narrative",
                                      "born", "expiry", "k", "n", "since_born", "fire"])
```

3e. `ingest_verify` 落 `born_price`——`rows = [...]` 之前加收盘映射,行 dict 加键:

```python
    closes: dict[str, float] = {}
    lp = scan_dir / "L1_scored_full.csv"
    if lp.exists():
        try:
            l1 = pd.read_csv(lp, dtype={"code": str})
            if {"code", "close"} <= set(l1.columns):
                closes = dict(zip(l1["code"].astype(str).str.zfill(6),
                                  pd.to_numeric(l1["close"], errors="coerce"), strict=False))
        except Exception:  # noqa: BLE001
            closes = {}
    rows = [{"code": str(r["code"]).zfill(6), "name": r.get("name", ""), "born": born,
             "expiry": expiry, "source": "skeptic", "narrative": r.get("trigger", ""),
             "conds": "[]", "invalidation": "[]", "note": "",
             "born_price": ("" if pd.isna(closes.get(str(r["code"]).zfill(6), float("nan")))
                            else closes.get(str(r["code"]).zfill(6)))}
            for _, r in v.iterrows() if (str(r["code"]).zfill(6), born) not in seen]
```

3f. `render_watchlist_block` 整函数替换:

```python
def render_watchlist_block(status: pd.DataFrame) -> str:
    """L5 嵌入块:触发置顶,失效垫底;born→今 = 错过审计列;空 → ""。"""
    if status is None or not len(status):
        return ""
    s = status.copy()
    s["_o"] = s["status"].map(_status_rank)
    s = s.sort_values(["_o", "code"], kind="stable")
    lines = ["### 👀 观察单日检", "",
             "| 状态 | 股票 | born→今 | 条件明细 | 触发叙事 | 到期 |", "|---|---|---|---|---|---|"]
    for _, r in s.iterrows():
        st = str(r["status"])
        if st.startswith("触发(待人工项)"):
            mark = "🔔 **触发**(待人工项)"
        elif st.startswith("触发"):
            mark = "🔔 **触发**"
        elif st.startswith("提醒"):
            mark = f"🟠 {st}"
        else:
            mark = {"临近": "🟡 临近", "失效": "⚫ 失效"}.get(st, st)
        if bool(r.get("new_k")):
            mark = "🆕 " + mark            # Δ新达成:较前日新满足了机判条件(spec §C1 噪声控制)
        sb = r.get("since_born")
        sb_txt = "—" if sb is None or pd.isna(sb) else f"{float(sb):+.0%}"
        if bool(r.get("fire")):
            sb_txt += " 🔥"
        lines.append(f"| {mark} | {r['name']}({r['code']}) | {sb_txt} | {r['detail']} "
                     f"| {r['narrative']} | {r['expiry']} |")
    lines.append("")
    lines.append("_触发≠自动升级评级:按 stock-research lite 档复核(拟下重注可对该票升 full 档),"
                 "评级仍由 rubric 三门定;🔥=未触发已 +15%(条件可能太保守,candidate 进 proposals)。_")
    return "\n".join(lines) + "\n"
```

3g′. **🆕 Δ新达成(C1 噪声控制)**——`check` 之后加两个函数,并把 `run_check` 里 `st = check(...)` 一行后接 `st = mark_new(st, _prev_status(scan_dir))`:

```python
def _prev_status(scan_dir: Path) -> pd.DataFrame | None:
    """上一 scan 日的 watchlist_status(比对 k 增量用);无 → None。"""
    scan_dir = Path(scan_dir)
    prevs = sorted((p for p in scan_dir.parent.iterdir()
                    if p.is_dir() and p.name[:2] == "20" and p.name < scan_dir.name
                    and (p / "watchlist_status.csv").exists()), reverse=True)
    if not prevs:
        return None
    try:
        return pd.read_csv(prevs[0] / "watchlist_status.csv", dtype={"code": str})
    except Exception:  # noqa: BLE001
        return None


def mark_new(st: pd.DataFrame, prev: pd.DataFrame | None) -> pd.DataFrame:
    """加 `new_k` 列:较前日**新达成**机判条件(k 增)→ True。无前日/前日无 k 列 → 全 False
    (防首日全🆕噪声)。持续满足的条目不再置顶播报,只常规行显示——spec §C1。"""
    s = st.copy()
    if prev is None or "k" not in getattr(prev, "columns", ()):
        s["new_k"] = False
        return s
    pk = dict(zip(prev["code"].astype(str).str.zfill(6),
                  pd.to_numeric(prev["k"], errors="coerce").fillna(0), strict=False))
    s["new_k"] = [float(r.get("k") or 0) > float(pk.get(str(r["code"]).zfill(6),
                                                        float(r.get("k") or 0)))
                  for r in s.to_dict("records")]
    return s
```

3g″. **watchlist_ledger 巡检节(C3)**——`autoresearch/learning/watchlist_ledger.py` 的 `render` 之后加:

```python
def monitoring_section(scan_root: Path | None = None) -> list[str]:
    """最新一日 watchlist_status 的 born→今 巡检(错过审计;spec 2026-07-05 wave §C3)。

    ledger 主表要等首个触发样本;本节让 ledger 从第一天就有读数——在监控条目此刻涨了多少。
    """
    scan_root = Path(scan_root or "context/scan")
    days = sorted(scan_root.glob("*/watchlist_status.csv"), reverse=True)
    if not days:
        return []
    try:
        st = pd.read_csv(days[0], dtype={"code": str})
    except Exception:  # noqa: BLE001
        return []
    if "since_born" not in st.columns:
        return []
    sub = st.dropna(subset=["since_born"])
    if not len(sub):
        return []
    out = ["", f"## 在监控 born→今(错过审计;{days[0].parent.name})",
           "| 股票 | 状态 | born→今 |", "|---|---|---|"]
    for r in sub.to_dict("records"):
        fire = " 🔥" if bool(r.get("fire")) else ""
        out.append(f"| {r.get('name', '')}({r['code']}) | {r.get('status', '')} "
                   f"| {float(r['since_born']):+.0%}{fire} |")
    return out
```

其 `main()` 里 `out.write_text("\n".join(render(ledger)) + "\n", ...)` 改为
`out.write_text("\n".join(render(ledger) + monitoring_section()) + "\n", encoding="utf-8")`。
对应测试追加到 `tests/learning/test_watchlist_ledger.py`:

```python
def test_monitoring_section_born_to_date(tmp_path):
    import pandas as pd

    from autoresearch.learning.watchlist_ledger import monitoring_section
    d = tmp_path / "2026-07-03"
    d.mkdir(parents=True)
    pd.DataFrame([{"code": "300476", "name": "胜宏科技", "status": "待触发",
                   "since_born": 0.22, "fire": True}]).to_csv(d / "watchlist_status.csv", index=False)
    text = "\n".join(monitoring_section(tmp_path))
    assert "born→今" in text and "+22%" in text and "🔥" in text
    assert monitoring_section(tmp_path / "nope") == []
```

3g. 文件末尾加 backfill / migrate / main:

```python
def backfill_born_price(path: Path | str = "context/watchlist.csv",
                        lake: Path | str = "context/lake/daily") -> int:
    """存量条目补 born_price(born 日 lake close);已有值/湖缺该日 → 跳过。返回补的行数。"""
    from autoresearch.data.tushare_source import _code6
    p, lake = Path(path), Path(lake)
    wl = load_watchlist(p)
    if not len(wl):
        return 0
    if "born_price" not in wl.columns:
        wl["born_price"] = ""
    n = 0
    for i, r in wl.iterrows():
        if str(r.get("born_price", "")).strip():
            continue
        day = str(r.get("born", "")).replace("-", "")
        fp = lake / f"{day}.parquet"
        if not fp.exists():
            continue
        try:
            df = pd.read_parquet(fp, columns=["ts_code", "close"])
            df = df.assign(_c=_code6(df["ts_code"]))
            sub = df[df["_c"] == str(r["code"]).zfill(6)]
            if len(sub) and not pd.isna(sub.iloc[0]["close"]):
                wl.at[i, "born_price"] = float(sub.iloc[0]["close"])
                n += 1
        except Exception:  # noqa: BLE001 — 单行降级
            continue
    if n:
        wl.to_csv(p, index=False)
    return n


def migrate_by_date(path: Path | str = "context/watchlist.csv") -> int:
    """conds 里带 MM-DD 日期的 manual → by_date(年份取 born 年;幂等)。返回改的条目数。"""
    import re
    p = Path(path)
    wl = load_watchlist(p)
    if not len(wl):
        return 0
    n = 0
    for i, r in wl.iterrows():
        try:
            conds = json.loads(r.get("conds") or "[]")
        except Exception:  # noqa: BLE001
            continue
        year = str(r.get("born", ""))[:4] or "2026"
        changed = False
        for c in conds:
            if c.get("kind") != "manual":
                continue
            m = re.search(r"(\d{2})-(\d{2})", str(c.get("text", "")))
            if not m:
                continue
            c["kind"], c["date"] = "by_date", f"{year}-{m.group(1)}-{m.group(2)}"
            changed = True
        if changed:
            wl.at[i, "conds"] = json.dumps(conds, ensure_ascii=False)
            n += 1
    if n:
        wl.to_csv(p, index=False)
    return n


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="观察单维护 CLI")
    ap.add_argument("cmd", choices=["backfill", "migrate"], help="backfill=补born_price;migrate=manual带日期→by_date")
    args = ap.parse_args(argv)
    n = backfill_born_price() if args.cmd == "backfill" else migrate_by_date()
    print(f"[watchlist] {args.cmd}: {n} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑测试确认通过(新旧全部)**

Run: `uv run --no-sync python -m pytest tests/scan/test_watchlist.py tests/scan/test_assemble_watchlist_menu.py tests/learning/test_watchlist_ledger.py -q`
Expected: 全 PASS(旧用例 k=1 仍"临近";render 对旧列缺失帧用 `.get` 容错)

- [ ] **Step 5: Commit**

```bash
git add autoresearch/scan/watchlist.py tests/scan/test_watchlist.py
git commit -m "feat(scan): 观察单 v2——提醒(k/n)分级/by_date 日期锚/since_born 错过审计🔥/CLI

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: C-ops · 存量观察单迁移 + 真数据验证(不产码,产运维产物)

- [ ] **Step 1: 迁移与回填**

```bash
cp context/watchlist.csv context/watchlist.csv.bak
uv run --no-sync python -m autoresearch.scan.watchlist migrate
uv run --no-sync python -m autoresearch.scan.watchlist backfill
```
Expected: migrate ≈5 行(胜宏/汇川/柳工/九联/东北证券/普洛中带 MM-DD 的 manual 条全转);backfill ≈6 行(born 日湖 close 补齐)。

- [ ] **Step 2: 对最近 staging 实跑日检**

```bash
uv run --no-sync python -c "import autoresearch.scan.watchlist as w; print(w.run_check('2026-07-03','context/scan/2026-07-03').to_string())"
```
Expected: 出 `k/n/since_born/fire` 列;汇川类条目状态呈 `提醒(k/n)` 或 `临近`;胜宏 `since_born` 有数。人工核对 2 行与 `context/watchlist.csv.bak` 语义一致后删 bak。**若与预期不符,回滚 bak 并回 Task 8 修**。

- [ ] **Step 3: Commit(无——context/ gitignored,本 task 无代码变更)**

---

### Task 10: WS-D · 决策卡模板 v2(多写不多读)

**Files:**
- Modify: `tests/test_agent_defs.py:36-46`(契约锚先加 = 失败测试)
- Modify: `.claude/skills/stock-research/lite-playbook.md`(两卡模板 + 压缩纪律)
- Modify: `.claude/agents/l4-card.md`(同步烤入)

**Interfaces:**
- Produces: 新推荐段落锚(非机器契约):`一段话研判`、`L3论点裁决`、`已核数字摘录`、`多写不多读`——`test_agent_defs.test_l4_card_contract_anchors_synced` 同源校验两份文档

- [ ] **Step 1: 契约锚先行(失败测试)**——test_agent_defs.py 的 anchors 列表改为:

```python
    anchors = ["进入P4倾向", "FINAL TRANSACTION PROPOSAL", "**Rating**",
               "早停只向下", "Rubric建议", "一段话研判", "L3 论点裁决",
               "已核数字摘录", "多写不多读", *(g for g in _OW_GATES)]
```

(锚「L3 论点裁决」带空格,与两份文档的 `## L3 论点裁决` 标题逐字一致。)

Run: `uv run --no-sync python -m pytest tests/test_agent_defs.py -q`
Expected: FAIL(两份文档均缺新锚)

- [ ] **Step 2: lite-playbook.md 早停卡模板升级**——把「### A. 早停卡」代码块整体替换为:

````markdown
### A. 早停卡(②/① 触发;~1.2–1.8K 输出,零深 WebSearch、零三档建模;**多写不多读**)

```
# 决策卡 — <代码> <名称> @ <date>  ·  〔早停·表面 DD〕

## 决策仪表盘
| 评级 | 现价 | 时间框架 | 触发位 | 置信度 |
|---|---|---|---|---|
| **<五档≤Hold>** | <价> | <月> | <减/清条件> | <高/中/低> |

## 一段话研判(120–200 字连贯叙事)
<这是什么生意/需求驱动 → L3 为什么选它(引 conviction 与核心论点)→ 实读 P1–P3 推翻或
确认了什么(引具体数字)→ 为什么停在这一档 + 什么条件下重估。>

## L3 论点裁决(拆前提逐条判)
| L3 前提(引原文) | 裁决 | 实读证据(一句) |
|---|---|---|
| <资金/估值/成长/催化前提,2–4 条> | ✓/✗ | <slim 数字> |

## 维度评分卡(表面 4 维 + 陷阱 2 维标未核;每维 2–3 条证据 bullet)
| 维度 | 评分 | 证据 |
|---|---|---|
| 基本面 | 强/中/弱 | • np_yoy/rev_yoy 与质地一句 • ROE 趋势一句 • <第三条可选> |
| 估值 | 强/中/弱 | • pe/pb vs 行业位置 • fwd PE vs TTM 一句 |
| 技术·资金 | 强/中/弱 | • 60日走势轮廓与关键位 • 主力/CMF/OBV 三线各自读数 • winner/户数一句 |
| 催化 | 强/中/弱 | • 近14天新闻落到日期 • 下一闸门(日历) |
| 盈利质量 | **未核** | 需深挖(早停未读 CFO) |
| 偿付(爆雷) | **未核** | 需深挖(早停未读 质押/商誉) |

## 已核数字摘录(纯誊写已读 slim,防编数便复查)
| 现价 | pe | fwd-PE | pb | np_yoy | rev_yoy | roe | 主力净占比 | cmf20 | obv20 | winner | 户数趋势 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| <…12 格,缺写 —> |

(若有档案:**变化项(vs 档案)**:<增量>)
**Rubric建议**: 表面 4 维净分 <±n>/4 ｜ 早停因:<≤20字 为何此点否决·翻盘牌已翻开> → **建议 <Rating ≤ Hold>**
**Rating**: <Hold|Underweight|Sell> ← 必须 = Rubric建议
**一行多空**: 多 <…> ｜ 空 <…>
FINAL TRANSACTION PROPOSAL: **<HOLD|SELL>**
置信度: <高/中/低> ｜ _早停于 P<1|3>:表面 DD 判定非买点,未做深核;Claude 推理产出,仅供研究,非投资建议。_
```

> **误杀保险**:若漏斗简报 conviction ≥ 80 却要停在 ≤Hold,在 Rubric建议行**多写一句「为何推翻 L3 高 conviction」**——不许高 conviction 票被静默早停。
````

满卡模板(### B)在 `## 决策仪表盘` 表格之后、`## 维度评分卡` 之前插入同款 `## 一段话研判` 与 `## L3 论点裁决` 两节(文本同上);评分卡「一句话依据」列名改「证据(2–3 bullet)」。

「压缩纪律 + Grounded」小节第一条之后插入:

```markdown
- **多写不多读(丰富化铁律)**:一段话研判/L3论点裁决/证据 bullet/数字摘录全部只用**已读**
  的简报与 slim 块——**读盘边界一毫米不动**(P4 分界纪律、WebSearch 只给 survivor 照旧),
  禁止以"写丰富"为由多读深核块或加检索。丰富化只发生在输出侧(早停卡 ~1.2–1.8K、满卡 ~3K)。
```

- [ ] **Step 3: l4-card.md 同步**——「卡片模板」节 A 卡代码块内,`## 决策仪表盘` 表格后插入:

```
## 一段话研判(120–200 字:什么生意→L3 为何选→实读推翻/确认了什么→为何停这一档)
## L3 论点裁决(L3 前提 2–4 条 × ✓/✗ × 一句实读证据 的小表)
```

「维度评分卡」行改为 `## 维度评分卡(表面 4 维 + 陷阱 2 维标未核;每维 2–3 条证据 bullet)`;其后加一行 `## 已核数字摘录(≤12 格关键数字纯誊写:价/pe/fwd-PE/pb/np_yoy/rev_yoy/roe/主力/cmf/obv/winner/户数)`。B 卡代码块 `## 维度评分卡(6 维齐全…)` 之前插入同两行。「压缩纪律」节末尾追加:

```
**多写不多读**:丰富化(一段话研判/裁决表/bullet/数字摘录)全在输出侧,只用已读块;读盘边界(P4 分界/WebSearch 纪律)一毫米不动。早停卡 ~1.2–1.8K、满卡 ~3K。
```

- [ ] **Step 4: 跑契约测试确认通过**

Run: `uv run --no-sync python -m pytest tests/test_agent_defs.py -q`
Expected: PASS(锚在两份文档均出现;机器契约行未动)

- [ ] **Step 5: Commit**

```bash
git add tests/test_agent_defs.py .claude/skills/stock-research/lite-playbook.md .claude/agents/l4-card.md
git commit -m "feat(skills): 决策卡模板 v2——一段话研判/L3裁决表/bullet评分卡/数字摘录(多写不多读)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 11: 顺带修 journal 发布后刷新 + A3 早停抽检 helper

**Files:**
- Modify: `autoresearch/scan/assemble.py`(run() 末尾,Task 6 hook 之后)
- Modify: `autoresearch/scan/agents/l4_card.py`(`pick_sentinel_candidates` 之后加 `pick_earlystop_audit`)
- Test: `tests/scan/test_l4_card_picks.py`(新建;若已有 pick_* 测试文件则追加)

**Interfaces:**
- Produces: `l4_card.pick_earlystop_audit(scan_dir, k=2, seed=None) -> list[str]`(确定性抽样早停卡 code;seed 缺省 = int(数据日 YYYYMMDD))

- [ ] **Step 1: 写失败测试**

```python
"""早停抽检对象挑选:确定性随机(seed=日期),只抽早停卡、排除复用卡。合成,无网络。

spec: 2026-07-05 wave §WS-A3(opt-in)——23 张早停弃单无人复核的单边质检补口。
"""
from __future__ import annotations

from autoresearch.scan.agents.l4_card import pick_earlystop_audit

_STOP = "# 决策卡\n**Rubric建议**: … ｜ 早停因:x → **建议 Hold**\n**Rating**: Hold\n"
_REUSE = "♻️ 复用 2026-07-01 卡\n" + _STOP
_FULL = "# 决策卡\n进入P4倾向: Hold\n**Rating**: Hold\n"


def _mk(root):
    d = root / "2026-07-03" / "details"
    d.mkdir(parents=True)
    for c, t in [("000001", _STOP), ("000002", _STOP), ("000003", _STOP),
                 ("000004", _REUSE), ("000005", _FULL)]:
        (d / f"{c}.md").write_text(t, encoding="utf-8")
    return root / "2026-07-03"


def test_pick_earlystop_deterministic_and_filters(tmp_path):
    sd = _mk(tmp_path)
    picks = pick_earlystop_audit(sd, k=2)
    assert len(picks) == 2
    assert set(picks) <= {"000001", "000002", "000003"}          # 复用/满卡不抽
    assert picks == pick_earlystop_audit(sd, k=2)                # 同日同 seed 确定性
    assert pick_earlystop_audit(tmp_path / "nope", k=2) == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_l4_card_picks.py -q`
Expected: FAIL — ImportError

- [ ] **Step 3: 实现 `pick_earlystop_audit`**(l4_card.py,`pick_sentinel_candidates` 之后)

```python
def pick_earlystop_audit(scan_dir, k: int = 2, seed: int | None = None) -> list[str]:
    """早停抽检对象(opt-in;spec 2026-07-05 wave §A3):当日早停卡里确定性抽 k 张,
    派独立复核 agent 只读「深核分界后块 + 早停卡 + 简报」判误杀;产出进 proposals 不改评级。

    seed 缺省 = 数据日整数(同日重跑同名单);复用卡(♻️)与满卡(进入P4倾向)不抽。
    """
    import random
    from pathlib import Path
    scan_dir = Path(scan_dir)
    base = scan_dir / "details"
    if not base.is_dir():
        return []
    stops = []
    for p in sorted(base.glob("*.md")):
        text = p.read_text(encoding="utf-8")
        if "♻️" in text or "进入P4倾向" in text:
            continue
        if "早停因" in text:
            stops.append(p.stem)
    if not stops:
        return []
    if seed is None:
        digits = "".join(ch for ch in scan_dir.name if ch.isdigit())
        seed = int(digits or "0")
    rng = random.Random(seed)
    return sorted(rng.sample(stops, min(k, len(stops))))
```

- [ ] **Step 4: assemble run() 加 journal 刷新**——Task 6 的 shadow_buys 块之后追加:

```python
    if scan_dir == Path("context/scan") / analysis_date:   # 真实现场才刷新日记(测试 tmp 目录不触发)
        with contextlib.suppress(Exception):
            from autoresearch.learning import journal as _journal
            _journal.main()
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run --no-sync python -m pytest tests/scan/test_l4_card_picks.py tests/scan/test_assemble.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add autoresearch/scan/agents/l4_card.py autoresearch/scan/assemble.py tests/scan/test_l4_card_picks.py
git commit -m "feat(scan): 早停抽检对象挑选(opt-in)+ assemble 发布后刷新 journal

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 12: 文档同步(SKILL/STAGES)+ 全量回归

**Files:**
- Modify: `.claude/skills/scan-market/SKILL.md`
- Modify: `.claude/skills/scan-market/STAGES.md`

- [ ] **Step 1: SKILL.md 三处**

① 步骤 3 中 `l3_table_md(..., dist_flag=True, reg_flag=True) 推荐常开` 的括号内容末尾,监管旗句后追加:

```
;**📣催化列** `cat_flag=True` 推荐常开(07-05 新:近10日 回购/增持/机构调研/减持 事件计数,prelude `catalyst` 步已按 L2 名单预 harvest `L3_catalyst.csv`;存在性≠方向,减持≥2 的票论点必须显式回应;默认关=parity)
```

② 步骤 0 prelude 描述里 `journal+buy_ledger+cross_calib 刷新` 改为 `journal+buy_ledger+cross_calib+catalyst_ledger+paper_nav 刷新(📈 影子成绩单三线:真实/影子/市场,`真实−影子`=门的价值)`,并在「观察单日检(🔔 触发置顶警报)」后加 `(v2:提醒(k/n) 分级/⏰by_date 临期/🔥since_born≥+15% 错过旗)`。

③ 步骤 4 买单 skeptic 小节之后加一条:

```
   - **早停抽检(opt-in,默认不跑;07-06 OTEL 成本数据后再定常开)**:0 买日 `l4_card.pick_earlystop_audit(scan_dir, k=2)` 抽 2 张早停卡,各派一个独立 `Agent(model='opus')` 复核员——**只读**该卡 + 漏斗简报 + slim `<!-- P4 深核分界 -->` 之后的块(早停 agent 没读的部分,~10k/张),回答「深核块里有无翻案证据」;verdict 落 `_es_audit_<code>.md`,"误杀嫌疑" 由编排写 proposals。**不改评级**——这是弃单侧的质检对称(买单有 skeptic,23 张早停卡此前无人看)。
```

- [ ] **Step 2: STAGES.md 两处**

① 「闭环层」表格末尾加三行:

```
| `paper_nav`(07-05 wave) | **影子组合成绩单**:真实(≥OW)/影子(top-3 Hold)/市场等权 三线 NAV(10% 槽·持10日·次日开盘进出);`真实−影子`=门的价值;summary 置顶一行 | 回填起 06-18;06-19 孤儿键跳过 |
| `shadow_buys`(07-05 wave) | 每日 conviction top-3 Hold 确定性记账(assemble 自动)→ NAV 影子线 + 评级基率样本池;与机会成本红队正交 | 历史回填 ~30 行 |
| `catalyst_ledger`(07-05 wave) | 催化旗票 vs 无旗票 fwd_5 对照(**n≥30 才读数**);IC 过硬前不入 composite | 零积累起步 |
```

② 「开放线头」加一条:

```
6. anns_d 无接口权限(07-05 实测):公告情感列空、监管旗走 L3_webnews 回退(`reg_hits_for_code`);run_health `anns_empty_rate`=1.0 即该态。权限开通/替代公告端点待核;northbound 通道 hk_ratio NaN=100% 空转读数(`northbound_probe`)取证中,quota 不动待 proposal。
```

- [ ] **Step 3: 全量回归**

Run: `uv run --no-sync python -m pytest -q`
Expected: 全绿(基线 686 + 本 wave 新增 ~25,0 fail)。若 `tests/scan/test_cli.py`/`test_parity.py` 类 parity 测试失败 → 检查是否有默认开的行为泄漏(本计划所有新参数默认关、新节 presence-gated,失败即实现有误,回对应 task 修)。

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/scan-market/SKILL.md .claude/skills/scan-market/STAGES.md
git commit -m "docs(scan): SKILL/STAGES 同步 07-05 wave(催化列/影子成绩单/观察单v2/早停抽检)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 验收清单(对照 spec §验收,下一真实 scan 日)

1. `reports/learning/paper_nav.md` 三线出数 + summary 出 📈 一行;`shadow_buys.csv` 每日 +3;
2. L3 表出现 `cat` 列且图例禁则在;`run_health.json` 有 `anns_empty_rate`(修复前 =1.0)与 `northbound`;
3. 观察单出现 `提醒(k/n)` / `since_born` / ⏰ / 🔥;存量 6 条 by_date 迁移完成;
4. 决策卡呈 v2 模板(一段话研判/L3 裁决表/bullet 评分卡/数字摘录),早停卡 ≤2K、满卡 ≤3.5K;
5. 卡片 lint 0 新增 warn 类型;pytest 全绿。
