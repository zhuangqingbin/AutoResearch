# Phase 3 — L3 Opus-high + 公告情感 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** L3 精排加 tushare `anns_d` 公告情感 digest(入 lake 复用)；细化 holistic prompt 为 Opus-high + rubric + `sentiment` 输出并透传 finalists。

**Architecture:** 新确定性 helper `scan/agents/l3_news.py`(harvest + digest + 关键词词典)。harvest 走数据湖 `get_or_fetch("anns_d", {ann_date})`(按公告日不可变,L4/analyze 复用),并落 staging `L3_news/<code>.json`(供 `load_l3_input` 读,免网络可测)。digest 三列并进 L3 表;Opus-high holistic(编排层 `screening-playbook.md`)读情感 + Phase 2 的 channel provenance 选股,输出 `sentiment` 经 `merge_l3_finalists_v2` 透传。

**Tech Stack:** Python 3 · pandas · tushare(`anns_d`,经 `data/sources` 泛端点路由)· parquet lake · pytest。`uv run --no-sync`。

## Global Constraints
- **Claude 即情感引擎**:不跑 FinGPT、不做新闻全文 NLP;只用公告**标题**(紧凑)。
- harvest 网络路径**best-effort 降级**:无权限/无端点/空 → 各 code 空列表,digest 三列缺省,L3 表照常渲染。
- digest 紧凑(≤~40 字/行):`news_n`(int)+ `news_tags`(如 "利多×2\|利空×1")+ `news_head`(最新标题≤24 字)。
- `anns_d` 入湖按 `ann_date` 键(桶① eod 不可变),L4/analyze 可复用同缓存。
- 依赖 Phase 2:L2 产物带 `recall_channels/n_channels` provenance(L3 表透出供 holistic「几路共振」)。
- 所有测试 NO network(合成 anns / 合成 staging)。命令 `uv run --no-sync python -m pytest ...`。

---

### Task 1: `anns_d` 端点 + harvest + digest

**Files:**
- Modify: `autoresearch/data/endpoints.py:42-43`(加 `anns_d` 一行)
- Create: `autoresearch/scan/agents/l3_news.py`
- Test: `tests/scan/test_l3_news.py`

**Interfaces:**
- Produces: `news_digest(anns: list[dict]) -> {"news_n":int, "news_tags":str, "news_head":str}`；`harvest_l3_news(date:str, codes:list[str], root=None, lookback_days:int=10) -> dict[str,list]`(并写 `<root>/<date>/L3_news/<code>.json`)；`_EVENT_TAGS: dict`。

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_l3_news.py
"""公告情感 digest(确定性)+ harvest 降级(无网络)。"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.scan.agents import l3_news
from autoresearch.scan.agents.l3_news import harvest_l3_news, news_digest


def test_digest_empty_defaults():
    assert news_digest([]) == {"news_n": 0, "news_tags": "", "news_head": "—"}


def test_digest_counts_tags_and_latest_head():
    anns = [
        {"ann_date": "20260618", "title": "关于回购公司股份的进展公告"},      # 利多
        {"ann_date": "20260620", "title": "第一大股东减持计划"},            # 利空(最新)
        {"ann_date": "20260619", "title": "关于增持公司股份的公告"},        # 利多
        {"ann_date": "20260617", "title": "关于召开股东大会的通知"},        # 中性
    ]
    d = news_digest(anns)
    assert d["news_n"] == 4
    assert "利多×2" in d["news_tags"] and "利空×1" in d["news_tags"]
    assert d["news_head"].startswith("第一大股东减持")          # ann_date 最大者
    assert len(d["news_head"]) <= 24


def test_harvest_degrades_when_fetch_empty(monkeypatch, tmp_path):
    """get_or_fetch 抛错/空 → 各 code 空列表、写 staging、不抛。"""
    monkeypatch.setattr(l3_news, "_trade_days_for", lambda date, n: ["20260620", "20260619"])
    monkeypatch.setattr(l3_news, "get_or_fetch",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no permission")))
    out = harvest_l3_news("2026-06-20", ["000001", "600000"], root=tmp_path / "scan")
    assert out == {"000001": [], "600000": []}
    saved = json.loads((tmp_path / "scan" / "2026-06-20" / "L3_news" / "000001.json").read_text())
    assert saved == []


def test_harvest_buckets_by_code(monkeypatch, tmp_path):
    monkeypatch.setattr(l3_news, "_trade_days_for", lambda date, n: ["20260620"])
    fake = pd.DataFrame({"ts_code": ["000001.SZ", "600000.SH"], "ann_date": ["20260620", "20260620"],
                         "title": ["回购公告", "减持公告"]})
    monkeypatch.setattr(l3_news, "get_or_fetch", lambda *a, **k: fake.copy())
    out = harvest_l3_news("2026-06-20", ["000001", "600000"], root=tmp_path / "scan")
    assert len(out["000001"]) == 1 and out["000001"][0]["title"] == "回购公告"
    assert len(out["600000"]) == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_l3_news.py -q`
Expected: FAIL(ModuleNotFoundError: l3_news）

- [ ] **Step 3: 注册 anns_d 端点**

`autoresearch/data/endpoints.py` 在 `express` 行后加:
```python
    "anns_d": {"key": "date", "settle": "eod", "source": "tushare"},    # 信息披露公告(ann_date;标题情感)
```

- [ ] **Step 4: 实现 l3_news.py**

```python
# autoresearch/scan/agents/l3_news.py
#!/usr/bin/env python3
"""scan-market · L3 公告情感 —— tushare anns_d 标题 harvest + 紧凑 digest(FinGPT 情感即特征)。

design: docs/specs/2026-06-22-l3-opus-sentiment-design.md §架构。
确定性、零 LLM:harvest 入湖(按 ann_date 不可变,L4 复用)+ 落 staging;digest 把每股近期公告
压成「数 + 方向标签 + 最新标题」。情感方向最终由 Opus 在 holistic 内细化(标题可中性/反讽)。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from autoresearch.data.cache import get_or_fetch

# 标题关键词 → 方向(粗;Claude 细化)。覆盖 A 股最常见材料事件。
_EVENT_TAGS = {
    "利多": ["回购", "增持", "中标", "股权激励", "业绩预增", "预增", "预盈", "扭亏",
             "定增", "重组", "收购", "签约", "订单", "获批", "中标"],
    "利空": ["减持", "质押", "问询", "关注函", "立案", "商誉减值", "业绩预减", "预减",
             "预亏", "退市", "违规", "诉讼", "处罚", "冻结", "终止"],
}


def _tag(title: str) -> str:
    for label, kws in _EVENT_TAGS.items():
        if any(kw in title for kw in kws):
            return label
    return ""


def news_digest(anns: list[dict]) -> dict:
    """近期公告 list → {news_n, news_tags("利多×2|利空×1"), news_head(最新标题≤24)}。空→缺省。"""
    if not anns:
        return {"news_n": 0, "news_tags": "", "news_head": "—"}
    counts: dict[str, int] = {}
    for a in anns:
        lab = _tag(str(a.get("title", "")))
        if lab:
            counts[lab] = counts.get(lab, 0) + 1
    tags = "|".join(f"{k}×{v}" for k, v in counts.items())
    latest = max(anns, key=lambda a: str(a.get("ann_date", "")))
    head = str(latest.get("title", ""))[:24] or "—"
    return {"news_n": len(anns), "news_tags": tags, "news_head": head}


def _trade_days_for(date: str, lookback_days: int) -> list[str]:
    """最近 lookback_days 个交易日(YYYYMMDD)。失败 → 空(harvest 据此降级)。"""
    try:
        from autoresearch.data.tushare_source import _pro, _trade_days, resolve_momentum_dates
        pro = _pro()
        last = resolve_momentum_dates(pro, date)[0]
        start = (datetime.strptime(last, "%Y%m%d") - timedelta(days=30)).strftime("%Y%m%d")
        return _trade_days(pro, start, last)[-lookback_days:]
    except Exception:  # noqa: BLE001
        return []


def harvest_l3_news(date: str, codes, root: Path | None = None, lookback_days: int = 10) -> dict:
    """对 codes 拉最近 ~lookback_days 公告(anns_d 按 ann_date 入湖)→ 按 code 分桶 + 落 staging。

    best-effort:任一 ann_date 拉取失败 → 跳过该日;全失败 → 各 code 空列表。返回 {code: [anns]}。
    """
    from autoresearch.data.tushare_source import _code6
    root = root or Path("context/scan")
    out_dir = root / date / "L3_news"
    out_dir.mkdir(parents=True, exist_ok=True)
    want = {str(c).zfill(6) for c in codes}
    buckets: dict[str, list] = {c: [] for c in want}

    for dd in _trade_days_for(date, lookback_days):
        try:
            df = get_or_fetch("anns_d", {"ann_date": dd}, today=date)
        except Exception:  # noqa: BLE001 — 无权限/无端点 → 跳过该日(降级)
            continue
        if df is None or not len(df) or "ts_code" not in df.columns:
            continue
        df = df.assign(_c=_code6(df["ts_code"]))
        for c, g in df[df["_c"].isin(want)].groupby("_c"):
            buckets[c].extend(g.drop(columns=["_c"]).to_dict("records"))

    for c in want:
        (out_dir / f"{c}.json").write_text(json.dumps(buckets[c], ensure_ascii=False, default=str),
                                           encoding="utf-8")
    return buckets
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run --no-sync python -m pytest tests/scan/test_l3_news.py -q`
Expected: PASS(4 passed）

- [ ] **Step 6: 提交**

```bash
git add autoresearch/data/endpoints.py autoresearch/scan/agents/l3_news.py tests/scan/test_l3_news.py
git commit -m "feat(l3): anns_d 公告情感 harvest + digest(入湖复用,降级)(Phase 3 Task 1)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: L3 表并入情感 digest + recall provenance

**Files:**
- Modify: `autoresearch/scan/agents/l3_select.py:19-24`(`_L3_COLS` 扩列)、`:47-76`(`load_l3_input` 读 L3_news + digest;`l3_table_md` cols)
- Test: `tests/scan/test_l3_news_table.py`

**Interfaces:**
- Consumes: `news_digest`(Task 1)；L2 产物的 `recall_channels/n_channels`(Phase 2)。
- Produces: `load_l3_input` 返回帧含 `news_n/news_tags/news_head`(+ `n_channels/recall_channels` 若存在)；`l3_table_md` 表含这些列。

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_l3_news_table.py
"""L3 表并入 news digest + recall provenance(缺则降级)。NO network。"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.scan.agents.l3_select import l3_table_md, load_l3_input


def _make_l2(tmp_path, with_news=True, with_prov=True):
    d = tmp_path / "context/scan" / "2026-06-20"
    (d / "L3_news").mkdir(parents=True)
    rows = []
    for i in range(3):
        r = {"code": f"{i:06d}", "name": f"s{i}", "industry": "电子", "composite": 90 - i,
             "gbdt_score": 0.5, "score_momentum": 50, "pct_60d": 10.0, "main_net_ratio": 0.01,
             "winner_rate": 30.0, "np_yoy": 50.0}
        if with_prov:
            r["n_channels"] = 3 - i
            r["recall_channels"] = "composite|momentum"
        rows.append(r)
    pd.DataFrame(rows).to_csv(d / "L2_gbdt_top200.csv", index=False)
    if with_news:
        (d / "L3_news" / "000000.json").write_text(json.dumps(
            [{"ann_date": "20260620", "title": "关于回购公司股份的公告"}]), encoding="utf-8")
        for c in ("000001", "000002"):
            (d / "L3_news" / f"{c}.json").write_text("[]", encoding="utf-8")
    return tmp_path / "context/scan"


def test_load_l3_input_merges_news_digest(tmp_path):
    root = _make_l2(tmp_path)
    df = load_l3_input("2026-06-20", root=root)
    assert {"news_n", "news_tags", "news_head"} <= set(df.columns)
    row0 = df[df["code"] == "000000"].iloc[0]
    assert int(row0["news_n"]) == 1 and "利多" in str(row0["news_tags"])


def test_load_l3_input_degrades_without_news(tmp_path):
    root = _make_l2(tmp_path, with_news=False)
    df = load_l3_input("2026-06-20", root=root)
    assert {"news_n", "news_tags", "news_head"} <= set(df.columns)   # 列在,缺省 0/""/—
    assert int(df.iloc[0]["news_n"]) == 0


def test_l3_table_md_shows_news_and_provenance(tmp_path):
    root = _make_l2(tmp_path)
    md = l3_table_md("2026-06-20", root=root)
    assert "news_tags" in md and "n_channels" in md
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_l3_news_table.py -q`
Expected: FAIL(load_l3_input 无 news 列）

- [ ] **Step 3: 扩 `_L3_COLS` + `load_l3_input` 读 news**

`autoresearch/scan/agents/l3_select.py`:`_L3_COLS` 末尾(`"roe"` 后)加:
```python
            "n_channels", "recall_channels",          # Phase 2 召回 provenance(几路共振)
            "news_n", "news_tags", "news_head"]        # Phase 3 公告情感 digest
```
(把原结尾 `"roe"]` 改为 `"roe",` 再接上面续行。)

在 `load_l3_input` 的 `return df` 前(L3_evidence 合并之后)加 news 合并:
```python
    news_dir = root / date / "L3_news"
    from autoresearch.scan.agents.l3_news import news_digest
    drows = []
    for c in df["code"]:
        fp = news_dir / f"{c}.json"
        anns = json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else []
        drows.append({"code": c, **news_digest(anns)})
    df = df.merge(pd.DataFrame(drows), on="code", how="left")
```
(`json` 已在文件顶部 import;若否则在函数内 `import json`。)

- [ ] **Step 4: `l3_table_md` 列含 news/provenance**

`l3_table_md` 的 cols 拼接处把:
```python
    cols = [*_L3_COLS] + [c for c in ("lhb_n", "has_forecast", "has_express") if c in df.columns]
```
改为(news/provenance 已在 `_L3_COLS`,`compact_table` 会自动只取存在列 → 无需特判;保留证据列特判即可):
```python
    cols = [*_L3_COLS] + [c for c in ("lhb_n", "has_forecast", "has_express") if c in df.columns]
    cols = [c for c in cols if c in df.columns]   # 缺列(无 provenance/news)自动跳过
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run --no-sync python -m pytest tests/scan/test_l3_news_table.py -q`
Expected: PASS(3 passed）

- [ ] **Step 6: 回归既有 L3 测试**

Run: `uv run --no-sync python -m pytest tests/scan/test_agents.py -q`
Expected: PASS（既有 load_l3_input/l3_table_md 测试不破——news 列缺省补上,旧断言仍成立）

- [ ] **Step 7: 提交**

```bash
git add autoresearch/scan/agents/l3_select.py tests/scan/test_l3_news_table.py
git commit -m "feat(l3): L3 表并入公告情感 digest + recall provenance(Phase 3 Task 2)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `sentiment` 透传 finalists

**Files:**
- Modify: `autoresearch/scan/agents/l3_select.py:160-163`(`merge_l3_finalists_v2` 输出 cols 加 `sentiment`)
- Test: `tests/scan/test_agents.py`(加一断言)

**Interfaces:**
- Produces: `merge_l3_finalists_v2` 输出列含 `sentiment`(若 judged 帧有该列)。

- [ ] **Step 1: 写失败测试**

在 `tests/scan/test_agents.py` 末尾加:
```python
def test_merge_l3_finalists_carries_sentiment():
    j = _judged_hybrid().assign(sentiment=["利多", "中性", "中性", "中性", "利多"])
    out = merge_l3_finalists_v2(j, target=3, trend_quota=2)
    assert "sentiment" in out.columns
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_agents.py::test_merge_l3_finalists_carries_sentiment -q`
Expected: FAIL(sentiment 不在输出列）

- [ ] **Step 3: 加 sentiment 到输出 cols**

`autoresearch/scan/agents/l3_select.py::merge_l3_finalists_v2` 的 `cols` 列表加 `"sentiment"`:
```python
    cols = ["ticker", "code", "name", "sector", "lenses", "conviction",
            "triage_lean", "triage_reason", "thesis", "risk", "catalyst", "lane", "sentiment"]
```
(`return out[[c for c in cols if c in out.columns]]` 已过滤不存在列 → 无 sentiment 时不报错。)

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run --no-sync python -m pytest tests/scan/test_agents.py -q`
Expected: PASS（全绿）

- [ ] **Step 5: 提交**

```bash
git add autoresearch/scan/agents/l3_select.py tests/scan/test_agents.py
git commit -m "feat(l3): sentiment 透传 finalists(Phase 3 Task 3)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: holistic prompt 升 Opus-high + rubric + FinGPT 映射文档

**Files:**
- Modify: `.claude/skills/scan-market/screening-playbook.md`(L3 段)
- Modify: `.claude/skills/scan-market/SKILL.md`(L3 行 + 流程 4)

- [ ] **Step 1: 更新 screening-playbook.md 的 L3 段**

把 L3 精排叙述改为:
- **取数**:先 `harvest_l3_evidence`(龙虎榜/预告/快报)+ **`harvest_l3_news`(公告情感,入湖)**;再 `l3_table_md` 出 ~200 行表(含 9 子分 + 证据 + **news_tags/news_head** + **n_channels/recall_channels**)。
- **模型**:holistic 选股 subagent = **`Agent(model='opus')` + high reasoning**(1 次通看全表)。
- **rubric**(显式 5 维,反羊群):①channel 共振(`n_channels`)②资金确认(main_net_ratio/lhb_n)③基本面支撑(growth/value 子分)④**情感**(news_tags/news_head 材料事件)⑤脆弱度(过热/利空)。要求跨 lane 选、给「为何此刻」。
- **输出**:每只 `conviction/fragility/thesis/risk/catalyst/lane` **+ `sentiment`(利多/中性/利空 + 一句依据)**;`merge_l3_finalists_v2` 透传。
- 末尾加 **FinGPT 映射**小节(借「情感即特征」;不跑其模型;`anns_d`=FinNLP 连接器的免费等价;market-feedback 验证留 learning/retro)。

- [ ] **Step 2: 更新 SKILL.md**

六段表 L3 行:「增量真证据 + 论点/红队(Sonnet)」→「**Opus-high holistic**:增量真证据 + **公告情感** + channel 共振 → 论点/红队/sentiment」。流程 4 命令补 `harvest_l3_news`。前置「默认中文」处注明 L3 升 Opus-high。

- [ ] **Step 3: 提交**

```bash
git add .claude/skills/scan-market/screening-playbook.md .claude/skills/scan-market/SKILL.md
git commit -m "docs(scan): L3 Opus-high + 情感 rubric + FinGPT 映射(Phase 3 Task 4)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5(可选低优先): L4/analyze 复用 anns_d 缓存

**Files:**
- Modify: `autoresearch/analyze/harvest.py`(slim context 处加读 lake `anns_d` digest)

- [ ] **Step 1**:在 analyze 取数的新闻/情感节加一行复用 `harvest_l3_news` 或直接 `get_or_fetch("anns_d", {ann_date})` 读已缓存公告 → 决策卡情感节。仅当 analyze 当前无公告情感时补;有则跳过本 task。
- [ ] **Step 2**:提交 `feat(analyze): 复用 anns_d 公告情感缓存(Phase 3 Task 5)`。

> 本 task 标**可选**:Phase 3 核心价值(L3 情感)已由 Task 1–4 交付;L4 复用是增益,视 analyze 现状决定做不做。

---

## 收尾验证(全部 task 后)
- [ ] `uv run --no-sync python -m pytest tests/ -q` → 全绿。
- [ ] `uv run --no-sync ruff check autoresearch/scan/agents/l3_news.py autoresearch/scan/agents/l3_select.py autoresearch/data/endpoints.py` → All checks passed。
- [ ] 无权限 token 下:`harvest_l3_news` 返回空桶、`load_l3_input` news 列缺省、`l3_table_md` 正常渲染(降级验证)。

## Self-Review(写完即查)
- **Spec 覆盖**:anns_d harvest 入湖(Task 1)✓ · digest(Task 1)✓ · L3 表并入 + provenance 合流(Task 2)✓ · sentiment 透传(Task 3)✓ · Opus-high + rubric + FinGPT 映射(Task 4)✓ · L4 复用可选(Task 5)✓ · 降级(Task 1/2 测试)✓。
- **类型一致**:`news_digest(anns)->{news_n,news_tags,news_head}` / `harvest_l3_news(date,codes,root,lookback_days)->dict[str,list]` / `_EVENT_TAGS` —— 全文一致;`load_l3_input` 合并键 `code`、merge how=left 一致。
- **placeholder 扫描**:无 TBD;每步含完整代码/命令/期望(Task 5 显式标可选,非 placeholder)。
- **依赖**:Task 2 用到 Phase 2 的 provenance 列 → 确认 Phase 2 先落地(本计划在 Phase 2 之后执行)。
