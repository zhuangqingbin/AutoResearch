# scan-market 周边提速包 + 行业 top3 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 `docs/specs/2026-07-12-scan-speed-perimeter-design.md` 落地 P1-P6 速度刀口(判断质量零改动)+ P7 确定性「看多行业 top3」。

**Architecture:** 纯编排/I-O 层:cache 增结算豁免 → 夜间预热 CLI+launchd;L3 evidence 走湖 + anns_d fast-fail;slim 批量 ThreadPool;gate2 带 meta 让 intel 提前;workflow 壳合并与生产者并行;stage_timing/效能表补全;市场层新增行业 healthy 分(单一事实源,回测脚本复用同一函数)。

**Tech Stack:** Python 3.12 + pandas + pytest(`uv run --no-sync`);workflow 为 `.claude/workflows/scan-market.js`(无 Node 依赖,harness 内执行)。

## Global Constraints

- 命令一律 `uv run --no-sync python -m ...`;测试 `uv run --no-sync python -m pytest`。
- 所有新旋钮缺省 = 现行为(唯一例外:`harvest_slim_batch` 默认 `workers=4`,spec §P3 已裁定);`LAKE_ASSUME_SETTLED` 未设置 = cache 逐字节现行为。
- 测试不得触真实 `context/`(tmp_path + 显式 root/path 注入;`tests/conftest.py` 已有 pinned/temperature/scan_config 隔离三件套,勿绕过)。
- workflow JS 禁 `Date.now()`/`Math.random()`;`bash()`/`gate()` 形参勿叫 `phase`(遮蔽全局)。
- 每 task 一个 commit,中文 conventional commit,结尾 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。
- P7 防锚定不变量:top3 标签**只**进 L5 小节与 sector_ledger;`market_context_block` / `sector_terrain_md` / `_l3_table.md` / `_l4_prompt_*` 一律不得出现。

---

### Task 1: P2a — harvest_l3_evidence 三端点走湖

**Files:**
- Modify: `autoresearch/scan/agents/l3_select.py`(`harvest_l3_evidence`,~543-580 行)
- Test: `tests/scan/test_l3_evidence_lake.py`(新建)

**Interfaces:**
- Consumes: `autoresearch.data.cache.get_or_fetch(endpoint, params, today=...)`(已存在;top_list/forecast/express 均已注册 policy key=date + B 级契约)
- Produces: `harvest_l3_evidence(date, codes, root)` 签名不变、产物 json 字节级不变;唯一行为差 = 取数经湖。

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_l3_evidence_lake.py
"""P2a:harvest_l3_evidence 走 get_or_fetch(湖),不再 _ts_call 裸调。"""
import json

import pandas as pd


def test_evidence_routes_through_lake(tmp_path, monkeypatch):
    calls: list[tuple[str, dict]] = []

    def fake_gof(endpoint, params, today=None, fetch=None):
        calls.append((endpoint, dict(params)))
        if endpoint == "top_list":
            return pd.DataFrame({"ts_code": ["000001.SZ"], "net_amount": [1.0]})
        return pd.DataFrame({"ts_code": ["000001.SZ"], "type": ["预增"]})

    import autoresearch.data.cache as cache
    monkeypatch.setattr(cache, "get_or_fetch", fake_gof)
    import autoresearch.data.tushare_source as ts
    monkeypatch.setattr(ts, "_pro", lambda: object())
    monkeypatch.setattr(ts, "resolve_momentum_dates", lambda pro, d: ("20260710", "", ""))
    monkeypatch.setattr(ts, "_trade_days", lambda pro, s, e: [f"202607{i:02d}" for i in range(1, 11)])

    from autoresearch.scan.agents.l3_select import harvest_l3_evidence
    ev = harvest_l3_evidence("2026-07-10", ["000001"], root=tmp_path)

    eps = {c[0] for c in calls}
    assert eps == {"top_list", "forecast", "express"}
    assert len([c for c in calls if c[0] == "forecast"]) == 10
    assert "_errors" not in ev
    saved = json.loads((tmp_path / "2026-07-10" / "L3_evidence" / "000001.json").read_text(encoding="utf-8"))
    assert saved["code"] == "000001" and "longhu" in saved and "forecast" in saved
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_l3_evidence_lake.py -v`
Expected: FAIL(`calls` 为空 —— 现实现走 `_ts_call` 裸调,fake_gof 未被触发)

- [ ] **Step 3: 最小实现**

`harvest_l3_evidence` 内改三处(函数签名/产物结构不动):

```python
    import json

    from autoresearch.data import cache as _cache          # 经模块属性调用,测试可 monkeypatch
    from autoresearch.data.tushare_source import _code6, _pro, _ts_call, resolve_momentum_dates
```

`_bulk` 的取数行 `df = _ts_call(fn)` 改为 `df = fn()`(fn 自带湖路由;`_ts_call` 的限频退避由 `sources.fetch` 内部承担),docstring 补一句:「2026-07-12 P2a:三端点改走 get_or_fetch(policy 早已注册)——已结算日湖命中零网络,预热(P1)可预拉」。三个调用点改为:

```python
    _bulk("longhu", lambda: _cache.get_or_fetch("top_list", {"trade_date": last}, today=date))
    ...
    for dd in _trade_days(pro, start, last)[-10:]:
        _bulk("forecast", lambda dd=dd: _cache.get_or_fetch("forecast", {"ann_date": dd}, today=date))
        _bulk("express", lambda dd=dd: _cache.get_or_fetch("express", {"ann_date": dd}, today=date))
```

(`_ts_call` import 若因此闲置则从该函数的局部 import 中删去,勿动模块其他用点。)

- [ ] **Step 4: 跑测试确认通过** — 同 Step 2 命令,Expected: PASS
- [ ] **Step 5: 回归相邻测试**

Run: `uv run --no-sync python -m pytest tests/scan -k "evidence or l3_select" -q`
Expected: 全 PASS

- [ ] **Step 6: Commit** — `feat(scan): P2a evidence 三端点走湖(top_list/forecast/express 经 get_or_fetch,已结算日零网络)`

---

### Task 2: P2b — anns_d 权限性 fast-fail

**Files:**
- Modify: `autoresearch/scan/agents/l3_news.py`(`harvest_l3_news`,121-147 行)
- Test: `tests/scan/test_l3_evidence_lake.py`(追加)

**Interfaces:**
- Produces: `harvest_l3_news` 签名/产物字节不变;权限类异常 1 次即 break,任意异常累计 ≥3 break。

- [ ] **Step 1: 写失败测试**

```python
def test_anns_permission_fast_fail(tmp_path, monkeypatch):
    n = {"calls": 0}

    def boom(endpoint, params, today=None):
        n["calls"] += 1
        raise RuntimeError("抱歉,您没有访问该接口的权限")

    import autoresearch.scan.agents.l3_news as ln
    monkeypatch.setattr(ln, "get_or_fetch", boom)
    monkeypatch.setattr(ln, "_trade_days_for", lambda date, lb: [f"202607{i:02d}" for i in range(1, 11)])
    buckets = ln.harvest_l3_news("2026-07-10", ["000001", "600000"], root=tmp_path)
    assert n["calls"] == 1                                  # 权限错 → 首日即 break
    assert buckets == {"000001": [], "600000": []}
    for c in ("000001", "600000"):                          # 空稿仍写(产物字节不变)
        assert (tmp_path / "2026-07-10" / "L3_news" / f"{c}.json").read_text(encoding="utf-8") == "[]"


def test_anns_transient_fail_capped_at_3(tmp_path, monkeypatch):
    n = {"calls": 0}

    def flaky(endpoint, params, today=None):
        n["calls"] += 1
        raise ConnectionError("timeout")

    import autoresearch.scan.agents.l3_news as ln
    monkeypatch.setattr(ln, "get_or_fetch", flaky)
    monkeypatch.setattr(ln, "_trade_days_for", lambda date, lb: [f"202607{i:02d}" for i in range(1, 11)])
    ln.harvest_l3_news("2026-07-10", ["000001"], root=tmp_path)
    assert n["calls"] == 3                                  # 任意异常有界:3 次封顶
```

- [ ] **Step 2: 确认失败** — `uv run --no-sync python -m pytest tests/scan/test_l3_evidence_lake.py -k anns -v` → FAIL(现实现 calls==10)
- [ ] **Step 3: 最小实现** — `harvest_l3_news` 循环体:

```python
    _PERM_MARKS = ("权限", "40203")
    fails = 0
    for dd in _trade_days_for(date, lookback_days):
        try:
            df = get_or_fetch("anns_d", {"ann_date": dd}, today=date)
        except Exception as e:  # noqa: BLE001 — 无权限/无端点 → 有界降级(P2b)
            fails += 1
            if any(m in repr(e) for m in _PERM_MARKS) or fails >= 3:
                break           # 权限错必然日日同错;瞬时错也别为 0 字节数据烧满 10×4 连退避
            continue
        if df is None or not len(df) or "ts_code" not in df.columns:
            continue
        ...(原样)
```

- [ ] **Step 4: 确认通过**;**Step 5:** `uv run --no-sync python -m pytest tests/scan -k news -q` 全绿
- [ ] **Step 6: Commit** — `feat(scan): P2b anns_d 权限性 fast-fail(首错即断/任意错3次封顶,产物字节不变)`

---

### Task 3: P3 — harvest_slim_batch ThreadPool

**Files:**
- Modify: `autoresearch/scan/agents/l4_card.py`(`harvest_slim_batch` 1125-1154 行 + CLI `--workers`)
- Test: `tests/scan/test_l4_helpers.py`(追加)

**Interfaces:**
- Produces: `harvest_slim_batch(date, root=None, min_bytes=8192, retries=1, harvest_fn=None, ctx_root=None, workers: int = 4) -> dict`(返回形状不变;failures 按 tickers 原序)。

- [ ] **Step 1: 写失败测试**

```python
def test_harvest_slim_batch_parallel_workers(tmp_path):
    import threading
    from autoresearch.scan.agents.l4_card import harvest_slim_batch
    scan_dir = tmp_path / "2026-07-10"
    scan_dir.mkdir(parents=True)
    (scan_dir / "_harvest_list.txt").write_text("AAA BBB CCC DDD", encoding="utf-8")
    lock, state = threading.Lock(), {"now": 0, "peak": 0}

    def fake_hv(t, dt):
        import time
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        time.sleep(0.05)
        p = tmp_path / f"{t}_{dt}_slim.md"
        p.write_text("x" * 10_000, encoding="utf-8")
        with lock:
            state["now"] -= 1
        return p

    res = harvest_slim_batch("2026-07-10", root=tmp_path, harvest_fn=fake_hv, workers=4)
    assert res["ok"] and res["n"] == 4 and res["failures"] == []
    assert state["peak"] >= 2                       # 真并发(串行时 peak==1)


def test_harvest_slim_batch_workers1_failures_ordered(tmp_path):
    from autoresearch.scan.agents.l4_card import harvest_slim_batch
    scan_dir = tmp_path / "2026-07-10"
    scan_dir.mkdir(parents=True)
    (scan_dir / "_harvest_list.txt").write_text("AAA 600000.SH BBB", encoding="utf-8")

    def tiny(t, dt):
        p = tmp_path / f"{t}_{dt}_slim.md"
        p.write_text("x", encoding="utf-8")
        return p

    res = harvest_slim_batch("2026-07-10", root=tmp_path, harvest_fn=tiny, workers=1)
    assert not res["ok"]
    assert [f["ticker"] for f in res["failures"]] == ["AAA", "600000.SH", "BBB"]
    assert res["failures"][1]["why"] == ".SH 未归一"
```

- [ ] **Step 2: 确认失败**(`workers` 参数不存在 → TypeError)
- [ ] **Step 3: 实现** — 循环体抽 `_one(t) -> dict | None`(`.SH` 检查、retries 循环、size 判定逻辑逐行照搬),然后:

```python
    if workers <= 1:
        results = [_one(t) for t in tickers]
    else:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(_one, tickers))       # map 保序 → failures 原序
    failures = [r for r in results if r]
    return {"ok": not failures, "n": len(tickers), "failures": failures}
```

docstring 追加:「workers=4 默认并发(spec §P3);subprocess 取数为 I/O 密集,限频靠 per-ticker retries 串行重试承担」。CLI:`ap.add_argument("--workers", type=int, default=4, help="slim 批量并发数(1=串行)")`;harvest-slim 分支改 `harvest_slim_batch(args.date, workers=args.workers)`。

- [ ] **Step 4: 确认通过**;**Step 5:** `uv run --no-sync python -m pytest tests/scan/test_l4_helpers.py -q` 全绿(既有 harvest_fn 注入测试在 workers=4 下线程安全,应原样绿;若有对串行顺序敏感的旧测试,改传 `workers=1` 并注明)
- [ ] **Step 6: Commit** — `feat(scan): P3 slim 批量 ThreadPool(默认4 workers·--workers 旋钮·failures 保序·GATE3 语义不变)`

---

### Task 4: P1a — cache 层 LAKE_ASSUME_SETTLED 结算豁免

**Files:**
- Modify: `autoresearch/data/cache.py`(`get_or_fetch` date 分支,158-165 行;顶部确保 `import os`)
- Test: `tests/data/test_cache.py`(追加)

**Interfaces:**
- Produces: env `LAKE_ASSUME_SETTLED=1` 且参数日 `d == today` → 走「拉取→契约→原子写」入湖;`d > today` 或 env 未设 → 现行为(拉而不写)。

- [ ] **Step 1: 写失败测试**(镜像本文件既有 tmp-LAKE/fetch 注入模式;三断言)

```python
def test_lake_assume_settled_writes_same_day(tmp_path, monkeypatch):
    import autoresearch.data.cache as cache
    monkeypatch.setattr(cache, "LAKE", tmp_path)
    monkeypatch.setenv("LAKE_ASSUME_SETTLED", "1")
    df = pd.DataFrame({"ts_code": [f"{i:06d}.SZ" for i in range(3100)],
                       "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
                       "amount": 1.0, "pct_chg": 0.0})
    out = cache.get_or_fetch("daily", {"trade_date": "20260710"}, today="2026-07-10",
                             fetch=lambda ep, p: df)
    assert len(out) == 3100
    assert (tmp_path / "daily" / "20260710.parquet").exists()      # 同日入湖(豁免生效)


def test_lake_assume_settled_never_writes_future(tmp_path, monkeypatch):
    import autoresearch.data.cache as cache
    monkeypatch.setattr(cache, "LAKE", tmp_path)
    monkeypatch.setenv("LAKE_ASSUME_SETTLED", "1")
    df = pd.DataFrame({"ts_code": ["000001.SZ"], "open": [1.0], "high": [1.0],
                       "low": [1.0], "close": [1.0], "amount": [1.0], "pct_chg": [0.0]})
    cache.get_or_fetch("top_list", {"trade_date": "20260711"}, today="2026-07-10",
                       fetch=lambda ep, p: df)
    assert not (tmp_path / "top_list" / "20260711.parquet").exists()   # 未来日恒不写


def test_no_env_same_day_not_written(tmp_path, monkeypatch):
    import autoresearch.data.cache as cache
    monkeypatch.setattr(cache, "LAKE", tmp_path)
    monkeypatch.delenv("LAKE_ASSUME_SETTLED", raising=False)
    df = pd.DataFrame({"ts_code": ["000001.SZ"], "open": [1.0], "high": [1.0],
                       "low": [1.0], "close": [1.0], "amount": [1.0], "pct_chg": [0.0]})
    cache.get_or_fetch("top_list", {"trade_date": "20260710"}, today="2026-07-10",
                       fetch=lambda ep, p: df)
    assert not (tmp_path / "top_list" / "20260710.parquet").exists()   # parity
```

(注:`daily` 是 A 级 min_rows≈3000,首个测试帧行数须 ≥3000;`top_list` 是 B 级,小帧可用。)

- [ ] **Step 2: 确认失败**(首测:parquet 不存在)
- [ ] **Step 3: 实现** — date 分支改:

```python
    if pol["key"] == "date":
        d = _compact(_first(params, _DATE_PARAM_KEYS))
        if d and d >= t:
            # 预热豁免(spec 2026-07-12-scan-speed-perimeter §P1):LAKE_ASSUME_SETTLED=1 且
            # d == today → 视为已结算,落到下方「拉取→契约→原子写」正常入湖(19:15 后 EOD 已发布,
            # 契约 min_rows 仍兜底);d > today(未来日)任何情况拒写。env 未设 = 现行为逐字节不变。
            if not (d == t and os.environ.get("LAKE_ASSUME_SETTLED") == "1"):
                return check(endpoint, fetch(endpoint, params), key=str(key), source="fetch", cols=False)
```

- [ ] **Step 4: 确认通过**;**Step 5:** `uv run --no-sync python -m pytest tests/data/test_cache.py -q` 全绿
- [ ] **Step 6: Commit** — `feat(data): P1a LAKE_ASSUME_SETTLED 结算豁免(仅 d==today 放行入湖·未来日恒拒·env 缺省 parity)`

---

### Task 5: P1b — prewarm 模块 + CLI

**Files:**
- Create: `autoresearch/scan/prewarm.py`
- Test: `tests/scan/test_prewarm.py`(新建)

**Interfaces:**
- Consumes: Task 4 的 env 豁免;Task 1 的湖路由(evidence 预拉);`build_market_frame` / `temperature.rollup` / `retro.recalibrate_and_log`(均已存在)。
- Produces: `latest_settled_trade_date(now: datetime | None) -> str`(YYYY-MM-DD);`run_prewarm(date=None, *, with_calibrate=False, now=None) -> dict`;产物 `context/scan/<date>/_prewarm.json`:`{"date","started_at","ended_at","steps":[{"step","ok","note"}]}`(epoch 秒;Task 10 消费)。

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_prewarm.py
"""P1b:夜间预热——结算日解析(19:15 门)+ 步骤编排 + _prewarm.json + env 生命周期。"""
import json
import os
from datetime import datetime


def _patch_tradedays(monkeypatch):
    import autoresearch.data.tushare_source as ts
    monkeypatch.setattr(ts, "_pro", lambda: object())
    monkeypatch.setattr(ts, "_trade_days",
                        lambda pro, s, e: [d for d in ("20260709", "20260710") if d <= e.replace("-", "")])


def test_latest_settled_before_1915_falls_back(monkeypatch):
    _patch_tradedays(monkeypatch)
    from autoresearch.scan.prewarm import latest_settled_trade_date
    assert latest_settled_trade_date(datetime(2026, 7, 10, 18, 0)) == "2026-07-09"
    assert latest_settled_trade_date(datetime(2026, 7, 10, 19, 30)) == "2026-07-10"


def test_run_prewarm_writes_manifest_and_env_lifecycle(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                      # context/scan/<date> 落 tmp
    _patch_tradedays(monkeypatch)
    import autoresearch.scan.prewarm as pw
    monkeypatch.setattr(pw, "_frame_lake", lambda date: "帧 4000 只已入湖")
    monkeypatch.setattr(pw, "_prewarm_evidence", lambda date: "21 次端点预拉")
    monkeypatch.setattr(pw, "_temperature", lambda date: "1 行")
    res = pw.run_prewarm(now=datetime(2026, 7, 10, 19, 30))
    assert res["date"] == "2026-07-10" and res["ok"]
    assert os.environ.get("LAKE_ASSUME_SETTLED") is None            # 收尾必清
    j = json.loads((tmp_path / "context/scan/2026-07-10/_prewarm.json").read_text(encoding="utf-8"))
    assert j["ended_at"] >= j["started_at"]
    assert [s["step"] for s in j["steps"]] == ["frame_lake", "evidence_lake", "temperature"]


def test_run_prewarm_past_date_no_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _patch_tradedays(monkeypatch)
    import autoresearch.scan.prewarm as pw
    seen = {}
    monkeypatch.setattr(pw, "_frame_lake",
                        lambda date: seen.setdefault("env", os.environ.get("LAKE_ASSUME_SETTLED")))
    monkeypatch.setattr(pw, "_prewarm_evidence", lambda date: "")
    monkeypatch.setattr(pw, "_temperature", lambda date: "")
    pw.run_prewarm(date="2026-07-09", now=datetime(2026, 7, 10, 19, 30))
    assert seen["env"] is None                       # 目标日≠今天 → 不设豁免
```

- [ ] **Step 2: 确认失败**(模块不存在)
- [ ] **Step 3: 实现** `autoresearch/scan/prewarm.py`(步骤函数拆成模块级 `_frame_lake/_prewarm_evidence/_temperature`,测试可 patch):

```python
#!/usr/bin/env python3
"""scan-market · 夜间预热(确定性,零 LLM)——把「点火→出报告」最贵的取数段挪到 19:30 后台。

design: docs/specs/2026-07-12-scan-speed-perimeter-design.md §P1。
- 解析最近**已结算**交易日(交易日历;今天是交易日且本地时间 ≥19:15 → 今天,否则上一交易日);
- 目标日 == 今天时设 LAKE_ASSUME_SETTLED=1(cache 层仅对 d==today 放行入湖,未来日恒拒;
  完整性守卫 = 既有契约层:get_or_fetch「拉取→check→原子写」,A 级空/残缺抛且拒写,湖零污染);
- build_market_frame 全市场取数入湖(daily×20 + 快照端点)→ L3 evidence 三端点预拉(P2a 已走湖)
  → temperature rollup → 写 _prewarm.json(stage_timing「预热」行消费);
- calibrate **默认不跑**:夜跑自动 recalibrate 会在不扫描的日子也改 weights + 记 changelog,
  污染 DSR-lite trial 计数(P0-6)——`--with-calibrate` 手动旋钮。
幂等:湖已有该日数据 → 全程命中秒退。失败退出码非零、不阻断(晚间扫描回落现路径)。
  uv run --no-sync python -m autoresearch.scan.prewarm            # 自动选日
  uv run --no-sync python -m autoresearch.scan.prewarm 2026-07-10 --with-calibrate
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

_SETTLE_HHMM = 19 * 60 + 15    # 当日 EOD 视为已结算的最早本地时刻(19:15;spec §P1 依据)


def latest_settled_trade_date(now: datetime | None = None) -> str:
    """最近已结算交易日(YYYY-MM-DD):今天是交易日且 now≥19:15 → 今天;否则上一交易日。"""
    from autoresearch.data.tushare_source import _pro, _trade_days
    now = now or datetime.now()
    days = _trade_days(_pro(), (now - timedelta(days=30)).strftime("%Y%m%d"), now.strftime("%Y%m%d"))
    if not days:
        raise RuntimeError("trade_cal 取不到交易日(token/网络?)")
    if days[-1] == now.strftime("%Y%m%d") and now.hour * 60 + now.minute < _SETTLE_HHMM:
        days = days[:-1]
    if not days:
        raise RuntimeError("近 30 天无已结算交易日")
    d = days[-1]
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


def _frame_lake(date: str) -> str:
    from autoresearch.scan.frame import build_market_frame
    _, counts = build_market_frame(date)
    return f"帧 {counts['after_gate_a']} 只(L0 {counts['universe']})已入湖"


def _prewarm_evidence(date: str) -> str:
    """L3 evidence 三端点按日预拉(B 级:空=真实空,单端点失败不挡预热)。"""
    from autoresearch.data import cache as _cache
    from autoresearch.data.tushare_source import _pro, _trade_days, resolve_momentum_dates
    pro = _pro()
    last = resolve_momentum_dates(pro, date)[0]
    start = (datetime.strptime(last, "%Y%m%d") - timedelta(days=30)).strftime("%Y%m%d")
    n = 0
    for ep, params in ([("top_list", {"trade_date": last})]
                       + [(e, {"ann_date": dd}) for dd in _trade_days(pro, start, last)[-10:]
                          for e in ("forecast", "express")]):
        try:
            _cache.get_or_fetch(ep, params, today=date)
            n += 1
        except Exception:  # noqa: BLE001 — B 级增强,单端点失败不挡
            pass
    return f"{n} 次端点预拉"


def _temperature(date: str) -> str:
    from autoresearch.scan.temperature import rollup
    out = rollup(date, date)
    return f"{len(out)} 行" if len(out) else "无新增"


def run_prewarm(date: str | None = None, *, with_calibrate: bool = False,
                now: datetime | None = None) -> dict:
    now = now or datetime.now()
    date = date or latest_settled_trade_date(now)
    scan_dir = Path("context/scan") / date
    scan_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    steps: list[dict] = []
    set_env = date == now.strftime("%Y-%m-%d")
    if set_env:
        os.environ["LAKE_ASSUME_SETTLED"] = "1"

    def _step(name: str, fn) -> None:
        try:
            steps.append({"step": name, "ok": True, "note": str(fn(date) or "")})
        except Exception as e:  # noqa: BLE001 — 单步失败记录继续,末尾以 ok 汇总定退出码
            steps.append({"step": name, "ok": False, "note": f"{type(e).__name__}: {e}"})
            print(f"[prewarm] ✗ {name}: {e}", file=sys.stderr)

    try:
        _step("frame_lake", _frame_lake)
        _step("evidence_lake", _prewarm_evidence)
        _step("temperature", _temperature)
        if with_calibrate:
            def _calib(d):
                from autoresearch.learning.retro import recalibrate_and_log
                return f"weights 重标定:{str(recalibrate_and_log(d))[:80]}"
            _step("calibrate", _calib)
    finally:
        if set_env:
            os.environ.pop("LAKE_ASSUME_SETTLED", None)
    (scan_dir / "_prewarm.json").write_text(json.dumps(
        {"date": date, "started_at": started, "ended_at": time.time(), "steps": steps},
        ensure_ascii=False, indent=1), encoding="utf-8")
    ok = all(s["ok"] for s in steps)
    print(f"[prewarm] {date} {'✓' if ok else '✗'} · "
          + " · ".join(f"{s['step']}{'✓' if s['ok'] else '✗'} {s['note']}" for s in steps))
    return {"date": date, "ok": ok, "steps": steps}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="scan 夜间预热(确定性,零 LLM;launchd 19:30 或手动)")
    ap.add_argument("date", nargs="?", default=None, help="缺省=最近已结算交易日")
    ap.add_argument("--with-calibrate", action="store_true",
                    help="附带 recalibrate_and_log(默认关:防污染 changelog/DSR 计数)")
    args = ap.parse_args(argv)
    return 0 if run_prewarm(args.date, with_calibrate=args.with_calibrate)["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 确认通过**;**Step 5:** `uv run --no-sync python -m pytest tests/scan/test_prewarm.py -q` 全绿
- [ ] **Step 6: Commit** — `feat(scan): P1b 夜间预热 CLI(结算日解析19:15门·frame/evidence/温度入湖·calibrate默认关防DSR计数污染·_prewarm.json计时)`

---

### Task 6: P1c — prewarm.sh + launchd plist + SKILL 文档

**Files:**
- Create: `scripts/prewarm.sh`、`scripts/com.tradingagents.scan-prewarm.plist`
- Modify: `.claude/skills/scan-market/SKILL.md`(prelude 一节追加一段)

- [ ] **Step 1: 写 `scripts/prewarm.sh`**(`chmod +x`)

```bash
#!/bin/zsh -l
# scan-market 夜间预热(launchd 交易日 19:30 调;手动同命令)。-l 载入用户 profile 拿 TUSHARE_TOKEN。
cd "$(dirname "$0:A")/.." || exit 1
exec uv run --no-sync python -m autoresearch.scan.prewarm "$@"
```

- [ ] **Step 2: 写 plist 模板**(`__REPO__` 占位,安装时 sed 替换)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.tradingagents.scan-prewarm</string>
  <key>ProgramArguments</key><array>
    <string>/bin/zsh</string><string>-lc</string><string>__REPO__/scripts/prewarm.sh</string>
  </array>
  <key>StartCalendarInterval</key><array>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>19</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>19</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>19</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>19</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>19</integer><key>Minute</key><integer>30</integer></dict>
  </array>
  <key>StandardOutPath</key><string>/tmp/scan-prewarm.log</string>
  <key>StandardErrorPath</key><string>/tmp/scan-prewarm.err.log</string>
</dict></plist>
```

- [ ] **Step 3: SKILL.md prelude 节(第 52 行 `autoresearch.scan.prelude` 命令附近)追加**

```markdown
- **夜间预热(可选,spec 2026-07-12 §P1)**:交易日 19:30 launchd 自动 `scripts/prewarm.sh`(= `python -m autoresearch.scan.prewarm`,湖预拉+温度;calibrate 默认不跑防污染 changelog/DSR 计数)。安装:
  `sed "s|__REPO__|$PWD|" scripts/com.tradingagents.scan-prewarm.plist > ~/Library/LaunchAgents/com.tradingagents.scan-prewarm.plist && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tradingagents.scan-prewarm.plist`;验证 `launchctl list | grep scan-prewarm`。跑过预热的日子,开扫时 universe/L3 evidence 全湖命中。
```

- [ ] **Step 4: 冒烟** — `./scripts/prewarm.sh 2026-07-10`(过去已结算日:不设 env、全湖命中应 <2m 完成;确认 `context/scan/2026-07-10/_prewarm.json` 生成且 steps 全 ok)
- [ ] **Step 5: Commit** — `feat(scan): P1c prewarm.sh + launchd plist(交易日19:30)+ SKILL 安装说明`

---

### Task 7: P4a — gate2 返回 meta{code:{name,sector}}

**Files:**
- Modify: `autoresearch/scan/gates.py`(`gate2`,80-81 行)
- Test: `tests/scan/test_gates.py`(追加)

**Interfaces:**
- Produces: gate2 成功 JSON 增 `"meta": {code: {"name": str, "sector": str}}`(缺列 → 空串;失败 JSON 不变)。workflow(Task 9)据此在 GATE2 后立即派 intel。

- [ ] **Step 1: 写失败测试**

```python
def test_gate2_returns_meta(tmp_path):
    import pandas as pd
    from autoresearch.scan.gates import gate2
    scan_dir = tmp_path
    pd.DataFrame({"code": ["603259", "000567"], "ticker": ["603259", "000567"],
                  "name": ["药明康德", "海德股份"], "sector": ["医疗服务", "多元金融"],
                  "lane": ["trend", "value"]}).to_csv(scan_dir / "finalists.csv", index=False)
    res = gate2(scan_dir, budget=10)
    assert res["ok"]
    assert res["meta"]["603259"] == {"name": "药明康德", "sector": "医疗服务"}
    assert set(res["meta"]) == {"603259", "000567"}


def test_gate2_meta_missing_cols_empty_strings(tmp_path):
    import pandas as pd
    from autoresearch.scan.gates import gate2
    pd.DataFrame({"code": ["603259"], "ticker": ["603259"], "lane": ["trend"]}
                 ).to_csv(tmp_path / "finalists.csv", index=False)
    res = gate2(tmp_path, budget=10)
    assert res["ok"] and res["meta"]["603259"] == {"name": "", "sector": ""}
```

- [ ] **Step 2: 确认失败**;**Step 3: 实现** — gate2 return 前:

```python
    def _s(r, col):
        v = r.get(col)
        return "" if v is None or (isinstance(v, float) and v != v) else str(v)

    meta = {str(r["code"]): {"name": _s(r, "name"), "sector": _s(r, "sector")}
            for _, r in df.iterrows()}
    return {"ok": True, "gate": "gate2", "reason": "ok", "finalists": codes,
            "n": int(len(df)), "meta": meta}
```

- [ ] **Step 4/5: 测试通过 + `pytest tests/scan/test_gates.py -q` 全绿**
- [ ] **Step 6: Commit** — `feat(scan): P4a gate2 JSON 增 meta{code:{name,sector}}(intel GATE2 后即发的数据前提)`

---

### Task 8: P4b — l4_intel.max_queries 配置键

**Files:**
- Modify: `autoresearch/scan/user_config.py`(86 行 `_SUB_WHITELIST["l4_intel"]`)、`.claude/skills/scan-market/scan_config.jsonc`(29 行 l4_intel 块)
- Test: `tests/scan/test_user_config.py`(追加)

- [ ] **Step 1: 失败测试**

```python
def test_l4_intel_max_queries_allowed(tmp_path):
    from autoresearch.scan.user_config import load_user_config
    p = tmp_path / "scan_config.json"
    p.write_text('{"l4_intel": {"enabled": true, "max_queries": 10}}', encoding="utf-8")
    cfg = load_user_config(p)
    assert cfg["l4_intel"] == {"enabled": True, "max_queries": 10}


def test_l4_intel_unknown_subkey_still_raises(tmp_path):
    import pytest
    from autoresearch.scan.user_config import load_user_config
    p = tmp_path / "scan_config.json"
    p.write_text('{"l4_intel": {"enabled": true, "max_query": 10}}', encoding="utf-8")
    with pytest.raises(ValueError, match="max_query"):
        load_user_config(p)
```

- [ ] **Step 2: 确认失败**;**Step 3: 实现** — `_SUB_WHITELIST` 改 `"l4_intel": {"enabled", "max_queries"}`;scan_config.jsonc 的 `"l4_intel": { "enabled": true }` 行改:

```jsonc
  "l4_intel": { "enabled": true, "max_queries": 15 },   // 每票盲搜查询上限(首跑实测>8m 再拧,spec §P4③)
```

- [ ] **Step 4/5: 通过 + `pytest tests/scan/test_user_config.py -q` 全绿**
- [ ] **Step 6: Commit** — `feat(scan): P4b l4_intel.max_queries 白名单+config(默认15=现状)`

---

### Task 9: P4c+P5 — workflow 编排改造(intel 提前 / prep 并行 / 壳合并)

**Files:**
- Modify: `.claude/workflows/scan-market.js`

前置:通读现文件(222 行,本 task 改四处)。**不变量**:`l4_reuse` 最前、`prompts` 最后(07-07 排序坑);gate 失败 JSON 只含 `{ok,reason}`;schema 顶层必须 object。

- [ ] **Step 1: meta.phases 更新**(4-9 行)——L3/L4 detail 改为:

```js
    { title: 'L3', detail: '[sector-briefs ∥ 证据harvest] → L3-rank → finalists+GATE2(合并壳)' },
    { title: 'L4', detail: '情报站(GATE2 后即发)∥ [l4-prep(生产者并行) → slim-harvest] → 决策卡并发' },
```

- [ ] **Step 2: 壳合并①** — 删除 88 行 `bash('sector-pack')` 与 91-97 行 `sectorsRes` agent,合成一个 gate(schema 收敛 `{ok, sectors}`;`gate()` 的 prompt 文案顺带改为「把它打印的**最后一行 JSON** 原样作为你的结构化返回」——多 CLI 链式输出时唯一 JSON 在末行):

```js
const SECTORS = { type: 'object', required: ['ok', 'sectors'],
  properties: { ok: { type: 'boolean' }, sectors: { type: 'array', items: { type: 'string' } } } }
const sectorsRes = await gate('sector-pack+list',
  `${R} autoresearch.sector.reuse ${date} --apply; ${R} autoresearch.sector.pack ${date}; ` +
  `uv run --no-sync python -c "import json,glob,os;d='context/sector/${date}';b='${SD}/sector_briefs';` +
  `print(json.dumps({'ok':True,'sectors':sorted(os.path.splitext(os.path.basename(p))[0] ` +
  `for p in glob.glob(d+'/*.json') if not os.path.exists(os.path.join(b,os.path.splitext(os.path.basename(p))[0]+'.md')))}))"`,
  SECTORS, 'L3')
if (!sectorsRes) throw new Error('sector-pack+list 无返回(schema/API 失败)—— 不静默降级为"无行业 brief"')
const sectors = sectorsRes.sectors || []
```

- [ ] **Step 3: 壳合并②** — GATE2 schema 增 `meta: { type: 'object' }`;删除 124 行 `bash('finalists')`,GATE2 gate 的 cmd 改为链式:

```js
const g2 = await gate('GATE2',
  `${R} autoresearch.scan.agents.l3_select finalists ${date} --budget ${l3cap} && ` +
  `${R} autoresearch.scan.gates gate2 ${date} --budget ${l3cap}`, GATE2, 'L3')
```

- [ ] **Step 4: intel 提前 + prep 并行** — 把 149-166 行的 intelThunks/parallel 块整体重排为:GATE2 成功后**立即启动** intel(agent() 即刻返回 promise,不 await);l4-prep 四生产者 shell 并行;barrier 收在 GATE3+intel:

```js
// 活体情报站(spec §P4②):GATE2 后即发——盲于 L3 论点,只需 code/name/sector/date(g2.meta),
// 与 l4-prep + GATE3 slim 全窗重叠。carryover 复用票的情报可能白跑(近期 reuse=0,接受并 log)。
const intelOn = !!(cfg.l4_intel && cfg.l4_intel.enabled)
const maxQ = (cfg.l4_intel && cfg.l4_intel.max_queries) || 15
const INTEL = { type: 'object', required: ['code'],
  properties: { code: { type: 'string' }, events: { type: 'integer' } } }
const intelPromises = intelOn ? g2.finalists.map((code) => agent(
  `活体情报采集:${code} ${(g2.meta?.[code]?.name) || ''}(${(g2.meta?.[code]?.sector) || '行业未知'})· 分析日 ${date}。按你的人设六面全查(≤${maxQ} 条),写 ${SD}/_l4_intel_${code}.md;返回 code 与事件行数 events。`,
  { agentType: 'l4-intel', effort: cfg.agents?.l4_intel?.effort ?? 'max',
    ...(cfg.agents?.l4_intel?.model ? { model: cfg.agents.l4_intel.model } : {}),
    label: `intel:${code}`, phase: 'L4', schema: INTEL })) : []
if (intelOn) log(`🕵️ 情报站并行:${g2.finalists.length} 票盲搜(GATE2 后即发,≤${maxQ} 查/票)`)

phase('L4')
log('L4 派发包+slim 预取开始(reuse→[四生产者并行]→prompts→slim)')
await bash(
  `${R} autoresearch.scan.l4_reuse ${date} --apply --carryover; ` +
  `( ${R} autoresearch.scan.agents.l4_card pledge ${date} || true ) & ` +
  `( ${R} autoresearch.scan.agents.l4_card seats ${date} || true ) & ` +
  `( ${R} autoresearch.scan.calendar ${date} || true ) & ` +
  `( ${R} autoresearch.scan.agents.l4_card consensus ${date} || true ) & ` +
  `wait; ` +
  `${R} autoresearch.scan.agents.l4_card prompts ${date}`, 'l4-prep', 'L4')
```

dispatch-plan gate 原样保留;原 `const [g3, ...intelRes] = await parallel([...intelThunks])` 改:

```js
const [g3, ...intelRes] = await parallel([
  () => gate('GATE3', `${R} autoresearch.scan.agents.l4_card harvest-slim ${date}`, OK, 'L4'),
  ...intelPromises.map((p) => () => p),
])
```

`if (intelOn) log(...)` 的完成行分母改 `g2.finalists.length`。注意:`phase('L4')` 从原位置(131 行)后移到 intel 启动之后——intel agent 调用带显式 `phase: 'L4'`,分组不受全局 phase 状态影响(workflow 工具注明的 races 规避法)。

- [ ] **Step 5: 语法自检** — `node --check .claude/workflows/scan-market.js`(本机无 node 则跳过,靠人工重读 diff:重点看模板串内的反引号/引号配对与 `${}` 转义)
- [ ] **Step 6: Commit** — `feat(workflow): P4c+P5 intel GATE2后即发(g2.meta)+l4-prep四生产者并行+sector-list/finalists 壳合并(-3 spawn)·reuse前prompts后不变量保持`

---

### Task 10: P6a — stage_timing 增 预热/ensemble/assemble

**Files:**
- Modify: `autoresearch/scan/stage_timing.py`(`derive_stage_timing`)
- Test: `tests/scan/test_stage_timing.py`(无则新建;有则追加)

**Interfaces:**
- Produces: `_stage_timing.json` 新 key:`预热`(读 `_prewarm.json` 内容的 started/ended,非 mtime)、`ensemble`(卡 max-mtime → `ensemble/*.md` max-mtime)、`assemble`(max(卡,ensemble) → 推导时刻,诚实注「截至本表」);`总计` 终锚并入 ensemble。

- [ ] **Step 1: 失败测试**

```python
# tests/scan/test_stage_timing.py 追加(新建则含通常头)
import json
import os
import time


def test_stage_timing_prewarm_ensemble_assemble(tmp_path):
    from autoresearch.scan.stage_timing import derive_stage_timing
    det = tmp_path
    t0 = time.time() - 3600
    (det / "_t0.json").write_text("{}", encoding="utf-8")
    os.utime(det / "_t0.json", (t0, t0))
    (det / "_prewarm.json").write_text(json.dumps(
        {"date": "2026-07-10", "started_at": t0 - 900, "ended_at": t0 - 300, "steps": []}),
        encoding="utf-8")
    (det / "details").mkdir()
    card = det / "details" / "600000.md"
    card.write_text("x", encoding="utf-8")
    os.utime(card, (t0 + 1800, t0 + 1800))
    (det / "ensemble").mkdir()
    ens = det / "ensemble" / "600000.run2.md"
    ens.write_text("x", encoding="utf-8")
    os.utime(ens, (t0 + 2100, t0 + 2100))
    out = derive_stage_timing(det)
    assert out["预热"]["wall_s"] == 600
    assert out["ensemble"]["wall_s"] == 300
    assert out["assemble"]["wall_s"] >= 0          # ensemble → now(截至推导)
    assert out["总计"]["wall_s"] >= 2100           # 终锚并入 ensemble
```

- [ ] **Step 2: 确认失败**;**Step 3: 实现** — `derive_stage_timing` 追加(docstring 补三行说明;顶部**新增 `import time`**,json 已有):

```python
    ens = _mx((det / "ensemble").glob("*.md")) if (det / "ensemble").is_dir() else None

    spans = {
        ...(原样),
        "ensemble": (cards, ens),
        "assemble": (_maxopt(ens, cards, judged), time.time() if (ens or cards or judged) else None),
        "总计": (t0, _maxopt(ens, cards, judged, briefs, l2)),
    }
    out: dict = {}
    ...(原样)
    pw = det / "_prewarm.json"                      # 预热:读内容(epoch),非 mtime 链
    if pw.is_file():
        try:
            j = json.loads(pw.read_text(encoding="utf-8"))
            w = int(float(j["ended_at"]) - float(j["started_at"]))
            if w >= 0:
                out["预热"] = {"wall_s": w}
        except Exception:  # noqa: BLE001 — 计时可选
            pass
    return out
```

(注:`assemble` 的 end 锚 = 推导时刻,`ensure_stage_timing` 的「已有 key 优先」保证它只在首次 assemble 时定格,复渲染不漂移。)

- [ ] **Step 4/5: 通过 + `pytest tests/scan/test_stage_timing.py tests/scan/test_assemble.py -q` 全绿**
- [ ] **Step 6: Commit** — `feat(scan): P6a stage_timing 增 预热(_prewarm.json内容)/ensemble/assemble 三行·总计终锚并入 ensemble`

---

### Task 11: P6b — 效能表 effort/model 读 echo + 预热/ensemble/assemble 行

**Files:**
- Modify: `autoresearch/scan/assemble.py`(`_stage_token_estimate`,433-503 行)
- Test: `tests/scan/test_assemble.py`(追加)

**Interfaces:**
- Consumes: `context/scan/<date>/user_config_echo.json`(frame --json 已落)、Task 10 计时键。
- Produces: 效能表 effort/引擎列 = echo 实际值(无 echo → 现硬编码值 parity);新增 预热/L4 买单ensemble/整合 assemble 三行。

- [ ] **Step 1: 失败测试**

```python
def test_stage_table_effort_from_echo_and_new_rows(tmp_path):
    import json
    from autoresearch.scan.assemble import _stage_token_estimate
    det = tmp_path
    (det / "user_config_echo.json").write_text(json.dumps({"agents": {
        "l4_card": {"effort": "xhigh"}, "sector_brief": {"effort": "high", "model": "sonnet"},
        "strategist": {"effort": "high"}, "l3_rank": {"effort": "max"}}}), encoding="utf-8")
    (det / "_prewarm.json").write_text(json.dumps(
        {"date": "x", "started_at": 0.0, "ended_at": 60.0, "steps": []}), encoding="utf-8")
    (det / "ensemble").mkdir()
    (det / "ensemble" / "600000.run2.md").write_text("y" * 280, encoding="utf-8")
    text = "\n".join(_stage_token_estimate(det))
    l4_row = next(ln for ln in text.splitlines() if ln.startswith("| L4 研究"))
    assert "xhigh" in l4_row and "medium" not in l4_row
    brief_row = next(ln for ln in text.splitlines() if "行业brief" in ln)
    assert "Sonnet" in brief_row and "high" in brief_row
    assert "| 预热(夜间)" in text and "| L4 买单ensemble" in text and "| 整合 assemble" in text


def test_stage_table_no_echo_parity(tmp_path):
    from autoresearch.scan.assemble import _stage_token_estimate
    text = "\n".join(_stage_token_estimate(tmp_path))
    l4_row = next(ln for ln in text.splitlines() if ln.startswith("| L4 研究"))
    assert "medium" in l4_row                        # 无 echo → 旧现值(parity)
    assert "| 预热(夜间)" not in text               # presence-gated:无 _prewarm.json 不加行
```

- [ ] **Step 2: 确认失败**;**Step 3: 实现** — `_stage_token_estimate` 内 rows 之前加:

```python
    echo_agents: dict = {}
    try:
        import json as _json
        echo_agents = (_json.loads((det / "user_config_echo.json").read_text(encoding="utf-8"))
                       .get("agents") or {})
    except Exception:  # noqa: BLE001 — 无 echo = 旧硬编码现值(parity)
        echo_agents = {}

    def _eff(key: str, default: str) -> str:
        v = (echo_agents.get(key) or {}).get("effort")
        return str(v) if v else default

    def _eng(key: str, default: str) -> str:
        m = (echo_agents.get(key) or {}).get("model")
        return {"sonnet": "Sonnet", "opus": "Opus", "haiku": "Haiku"}.get(str(m).lower(), str(m)) \
            if m else default

    ens_files = sorted((det / "ensemble").glob("*.md")) if (det / "ensemble").is_dir() else []
    has_prewarm = (det / "_prewarm.json").is_file()
```

rows 列表改为(effort/引擎经 `_eff/_eng`;新行 presence-gated):

```python
    rows = [
        *([("预热(夜间)", "确定性", "—", "预热", 0, 0, "lake/evidence/温度预拉(_prewarm.json)")]
          if has_prewarm else []),
        ("L0/L1/L2", "确定性", "—", "L0L1L2", 0, 0, "纯 pandas,零 LLM"),
        ("旁路 策略师", _eng("strategist", "Opus"), _eff("strategist", "session"), "策略师",
         1 if strat else 0, _b(strat), "market_pack → market_view.md"),
        ("旁路 行业brief", _eng("sector_brief", "Opus"), _eff("sector_brief", "low"), "行业brief",
         len(sbriefs), _b(sbriefs), "sector pack → sector_briefs/*.md(♻️TTL 复用亦计字节)"),
        ("L3 精排", _eng("l3_rank", "Opus·holistic"), _eff("l3_rank", "max"), "L3精排",
         1 if l3 else 0, _b(l3), "通看全表选 finalists(输入表落 `_l3_table.md` 才计入)"),
        ("L4 研究", _eng("l4_card", "Opus"), _eff("l4_card", "medium"), "L4研究", len(cards),
         _b(cards) + _b(l4t1), f"{len(cards)} 张卡(早停/满卡/复用;每卡 prompt 落 `_l4_prompt_*` 才计入)"),
        *([("L4 买单ensemble", _eng("l4_card", "Opus"), _eff("l4_card", "medium"), "ensemble",
            len(ens_files), _b(ens_files), "≥OW 追加 run2/3 取中位(仅有买日)")] if ens_files else []),
        ("L4 输入·slim", "—(输入侧)", "—", "L4slim", len(slims), _b(slims),
         "harvest --slim 落稿(每卡 subagent 读入;≈4.8KB 空稿=NO_DATA 亦计=真实浪费)"),
        ("L4 输入·情报", _eng("l4_intel", "Sonnet"), _eff("l4_intel", "max"), "L4intel",
         len(intels), _b(intels),
         "l4-intel 盲搜落稿(1 文件=1 sonnet 会话;网查计费经 OTEL,此处**未计非零**;未启用=0;不计入 LLM 调用合计)"),
        ("L4 新闻网查", "WebSearch", "—", "L4news", 0, 0,
         "P3 有界活体新闻(≤3/卡)+ sector/macro 网查(≤2)——无落盘 artifact,token 计费经 OTEL/`/usage`,此处**未计非零**"),
        ("整合 assemble", "确定性", "—", "assemble", 0, 0, "L5 组装 + self_review(截至本表渲染)"),
    ]
```

(`L4 买单ensemble` 行的 calls 与 L4 研究同为真 LLM 调用 → 计入 `tot_calls` 的现有条件 `name.startswith("L4 输入")` 排除法天然正确,勿改。)

- [ ] **Step 4/5: 通过 + `pytest tests/scan/test_assemble.py -q` 全绿**(既有效能表相关断言若锚死旧 effort 字面,更新为 echo 语义)
- [ ] **Step 6: Commit** — `feat(scan): P6b 效能表 effort/引擎读 user_config_echo(修表面medium实际xhigh失真)+预热/ensemble/assemble 三行`

---

### Task 12: P7a+b — 行业 healthy 分 + pack 块 + L5 小节 + ledger 分账 + brief 派发并集

**Files:**
- Modify: `autoresearch/scan/market.py`(score/top3/渲染)、`autoresearch/learning/sector_ledger.py`(record_top3 + seen 按 source)、`autoresearch/sector/pack.py`(`select_briefing_sectors` 第四来源+cap 语义)、`autoresearch/scan/assemble.py`(L5 小节 + ledger 接线)
- Test: `tests/scan/test_sector_top3.py`(新建)

**Interfaces:**
- Produces:
  - `market.sector_healthy_table(df) -> DataFrame | None`(列:industry/n/med_main_ratio/main_pos/healthy_share/med_pct_60d/med_pe/pe_gt_60/qualified/score);
  - `market.sector_healthy_top3(df, k=3) -> list[dict]`(dict 键同上,数值经 `_round`);
  - `market_pack`/`market_pack_from_frame` 增 `pack["sector_healthy_top3"]`;
  - `market.render_sector_top3(pack) -> str`(空 → "");
  - `sector_ledger.record_top3(date, industries, path=LEDGER_PATH) -> int`(source=deterministic_top3,`(date,industry,source)` 幂等);
  - `select_briefing_sectors`:基础来源仍 cap=k,top3 追加不占 cap(≤k+3)。
- 常量:`_TOP3_MIN_N=8`、`_TOP3_KNIFE=-20.0`、`_TOP3_MOM_CENTER=10.0`、`_TOP3_MOM_HALF=15.0`(spec §P7 带参)。

- [ ] **Step 1: 失败测试**

```python
# tests/scan/test_sector_top3.py
"""P7:确定性看多行业 top3 —— 资格门/倒U动量/防锚定/ledger 分账/brief 并集。"""
import json

import pandas as pd


def _frame():
    # 行业A:资金+健康+温和动量+便宜(应第一);行业B:资金门死(主力中位<0 且 +占比<50%);
    # 行业C:落刀(<-20);行业D:n<8。每行业 10 只(D 只 3 只)。
    def block(ind, n, mnr, p60, cmf, pe):
        return pd.DataFrame({"industry": ind, "code": [f"{hash(ind) % 90 + 10}{i:04d}" for i in range(n)],
                             "main_net_ratio": mnr, "pct_60d": p60, "cmf_20": cmf, "pe": pe,
                             "above_ma60": 1.0, "ma_bull": 0.0})   # classify_regime 消费列,防 pack 级测试炸
    return pd.concat([
        block("行业A", 10, 0.02, 12.0, 0.1, 20.0),
        block("行业B", 10, -0.05, 15.0, 0.1, 25.0),
        block("行业C", 10, 0.03, -30.0, 0.1, 10.0),
        block("行业D", 3, 0.05, 10.0, 0.2, 15.0),
    ], ignore_index=True)


def test_top3_gates_and_order():
    from autoresearch.scan.market import sector_healthy_top3
    rows = sector_healthy_top3(_frame())
    assert [r["industry"] for r in rows] == ["行业A"]      # B 资金门/C 落刀/D n<8 全被拦,宁缺毋滥
    assert rows[0]["n"] == 10 and rows[0]["med_pct_60d"] == 12.0


def test_top3_missing_cols_none():
    from autoresearch.scan.market import sector_healthy_table
    assert sector_healthy_table(pd.DataFrame({"industry": ["x"]})) is None


def test_pack_and_render_and_anti_anchor():
    from autoresearch.scan.market import market_context_block, market_pack_from_frame, render_sector_top3
    pack = market_pack_from_frame(_frame(), date=None)
    assert pack["sector_healthy_top3"][0]["industry"] == "行业A"
    md = render_sector_top3(pack)
    assert "🎯 看多行业 top3" in md and "行业A" in md
    assert "看多行业" not in market_context_block(pack)     # 防锚定:L3/L4 地形块无 top3 痕迹
    assert render_sector_top3({}) == ""


def test_ledger_record_top3_idempotent_and_separate(tmp_path):
    from autoresearch.learning.sector_ledger import record_calls, record_top3, _load
    p = tmp_path / "sector_calls.jsonl"
    assert record_top3("2026-07-10", ["行业A", "行业B"], path=p) == 2
    assert record_top3("2026-07-10", ["行业A"], path=p) == 0          # 幂等
    d = tmp_path / "scan" / "sector_briefs"
    d.mkdir(parents=True)
    (d / "行业A.md").write_text("**行业方向**: 看多 — x", encoding="utf-8")
    assert record_calls(tmp_path / "scan", "2026-07-10", path=p) == 1  # brief 与 top3 分账不互斥
    rows = _load(p)
    assert {r["source"] for r in rows} == {"deterministic_top3", "brief"}


def test_briefing_sectors_union_top3(tmp_path):
    from autoresearch.sector.pack import select_briefing_sectors
    scan_dir = tmp_path
    pd.DataFrame({"industry": [f"热{i}" for i in range(8)],
                  "median_pct_60d": range(8, 0, -1), "n_recall": 5}
                 ).to_csv(scan_dir / "sectors.csv", index=False)
    (scan_dir / "market_pack.json").write_text(json.dumps(
        {"sector_healthy_top3": [{"industry": "冷门X"}, {"industry": "热2"}]}), encoding="utf-8")
    inds, prov = select_briefing_sectors(scan_dir, k=3, wl_path=tmp_path / "nope.csv")
    assert inds[:3] == ["热0", "热1", "热2"]           # 红榜降序 top3;基础来源仍 cap=k
    assert "冷门X" in inds and prov["冷门X"] == "top3看多"   # top3 追加不占 cap
    assert len(inds) == 4                              # 热2 已在基础集,去重
```

- [ ] **Step 2: 确认失败**;**Step 3: 实现四个文件**

`market.py`(`_sectors_from_frame` 之后加;模块顶部常量区加四常量):

```python
_TOP3_MIN_N = 8            # 资格门①:成分数(剔 n=1 噪声行业)
_TOP3_KNIFE = -20.0        # 资格门③:非落刀(60日中位)
_TOP3_MOM_CENTER = 10.0    # 倒U 动量带中心
_TOP3_MOM_HALF = 15.0      # 倒U 半宽


def sector_healthy_table(df: pd.DataFrame) -> pd.DataFrame | None:
    """P7 行业级 healthy 组件表(spec 2026-07-12-scan-speed-perimeter §P7,零 LLM)。

    资格门(先过门再排序):n≥_TOP3_MIN_N ∧ 资金门(主力净比中位>0 或 为正占比≥50%)∧
    非落刀(60日中位>_TOP3_KNIFE)。过门行业按四组件 rank-sum 等权(score 越小越好):
    资金(净比中位+为正占比)/ 健康占比(healthy_riser_mask 单一事实源)/ 估值(中位PE低+
    PE>60 占比低)/ 动量(倒U:|中位60日−center|/half 越小越好——不追拥挤链不接刀)。
    缺列/空帧 → None(presence-gated)。
    """
    need = ("industry", "pct_60d", "main_net_ratio", "cmf_20", "pe")
    if df is None or not len(df) or not all(c in df.columns for c in need):
        return None
    from autoresearch.common.scoring import healthy_riser_mask
    d = df.copy()
    hm = healthy_riser_mask(d)
    d["_healthy"] = hm.astype(float) if hm is not None else 0.0
    d["_pe"], d["_mnr"], d["_p60"] = _num(d, "pe"), _num(d, "main_net_ratio"), _num(d, "pct_60d")
    g = d.groupby(d["industry"].astype(str))

    def _agg(fn):
        return g.apply(fn).to_numpy()

    t = pd.DataFrame({
        "industry": list(g.size().index),
        "n": g.size().to_numpy(),
        "med_main_ratio": _agg(lambda s: float(s["_mnr"].median()) if s["_mnr"].notna().any() else float("nan")),
        "main_pos": _agg(lambda s: float((s["_mnr"].dropna() > 0).mean()) if s["_mnr"].notna().any() else float("nan")),
        "healthy_share": _agg(lambda s: float(s["_healthy"].mean())),
        "med_pct_60d": _agg(lambda s: float(s["_p60"].median()) if s["_p60"].notna().any() else float("nan")),
        "med_pe": _agg(lambda s: float(s.loc[s["_pe"] > 0, "_pe"].median()) if (s["_pe"] > 0).any() else float("nan")),
        "pe_gt_60": _agg(lambda s: float((s.loc[s["_pe"] > 0, "_pe"] > 60).mean()) if (s["_pe"] > 0).any() else float("nan")),
    })
    t["qualified"] = ((t["n"] >= _TOP3_MIN_N)
                      & ((t["med_main_ratio"] > 0) | (t["main_pos"] >= 0.5))
                      & (t["med_pct_60d"] > _TOP3_KNIFE))
    t["score"] = float("nan")
    q = t[t["qualified"]]
    if len(q):
        comp = pd.DataFrame({
            "fund": (q["med_main_ratio"].rank(ascending=False) + q["main_pos"].rank(ascending=False)) / 2,
            "health": q["healthy_share"].rank(ascending=False),
            "valuation": (q["med_pe"].rank(ascending=True) + q["pe_gt_60"].rank(ascending=True)) / 2,
            "momentum": ((q["med_pct_60d"] - _TOP3_MOM_CENTER).abs() / _TOP3_MOM_HALF).rank(ascending=True),
        })
        t.loc[q.index, "score"] = comp.mean(axis=1)
    return t.sort_values("score", na_position="last").reset_index(drop=True)


def sector_healthy_top3(df: pd.DataFrame, k: int = 3) -> list[dict]:
    """过门行业按 score 取前 k(不足 k 出几个是几个,宁缺毋滥);缺列 → []。"""
    t = sector_healthy_table(df)
    if t is None:
        return []
    out = []
    for _, r in t[t["qualified"] & t["score"].notna()].head(k).iterrows():
        out.append({"industry": r["industry"], "n": int(r["n"]),
                    "med_main_ratio": _round(r["med_main_ratio"], 4),
                    "main_pos": _round(r["main_pos"], 2),
                    "healthy_share": _round(r["healthy_share"], 2),
                    "med_pct_60d": _round(r["med_pct_60d"]),
                    "med_pe": _round(r["med_pe"], 1),
                    "pe_gt_60": _round(r["pe_gt_60"], 2)})
    return out


def render_sector_top3(pack: dict) -> str:
    """L5 小节「🎯 看多行业 top3」(仅 L5 + sector_ledger;防锚定:不喂 L3/L4)。空 → ''。"""
    rows = pack.get("sector_healthy_top3") or []
    if not rows:
        return ""
    lines = ["## 🎯 看多行业 top3(确定性 healthy 分 · 零 LLM)",
             "_资格门:n≥8 ∧ 资金门(主力净比中位>0 或 为正占比≥50%)∧ 非落刀(60日中位>−20%);"
             "四组件 rank-sum 等权(资金/健康占比/估值/倒U动量,带参见 market.py 常量)。"
             "无论点;证伪点 = 分数构成反转(资金转负/健康占比塌/进入拥挤带)。"
             "只进 L5 与 sector_ledger(source=deterministic_top3),不喂 L3/L4。_", "",
             "| # | 行业 | n | 主力净比中位 | 主力+占比 | 健康占比 | 60日中位% | 中位PE | PE>60占比 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        lines.append(f"| {i} | {r['industry']} | {r['n']} | {r['med_main_ratio']} | {r['main_pos']} | "
                     f"{r['healthy_share']} | {r['med_pct_60d']} | {r['med_pe']} | {r['pe_gt_60']} |")
    return "\n".join(lines) + "\n"
```

`market_pack()`:`if len(df):` 块末尾加 `pack["sector_healthy_top3"] = sector_healthy_top3(df)`;`market_pack_from_frame()`:`pack["sectors"] = ...` 行后加 `pack["sector_healthy_top3"] = sector_healthy_top3(frame)`。

`sector_ledger.py`:`record_calls` 的 seen 集合改三元组 `(c.get("date"), c.get("industry"), c.get("source", "brief"))`、membership 判断改 `(date, p.stem, "brief") in seen`,行 dict 已含 `"source": "brief"` 不变;新增:

```python
def record_top3(date: str, industries: list[str], path: Path | str = LEDGER_PATH) -> int:
    """P7 确定性 top3 → 看多 call(source=deterministic_top3,与 brief 分账;三元组幂等)。"""
    if not industries:
        return 0
    path = Path(path)
    seen = {(c.get("date"), c.get("industry"), c.get("source", "brief")) for c in _load(path)}
    rows = [{"date": date, "industry": str(i), "direction": "看多",
             "source": "deterministic_top3", "realized_pct": None, "horizon": None}
            for i in industries if (date, str(i), "deterministic_top3") not in seen]
    if rows:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(rows)
```

`render_report` 聚合键改 `f"{direction}·{source}"`:`by_dir.setdefault(f"{c.get('direction', '—')}·{c.get('source', 'brief')}", ...)`(报表列头「方向」改「方向·来源」)。

`pack.py` `select_briefing_sectors`:watchlist 块之后、cap 之前加第四来源并改 cap 语义(函数 docstring 补一行「top3看多 追加不占 k」):

```python
    mp = scan_dir / "market_pack.json"                       # P7:确定性看多 top3(追加,不占 k)
    if mp.exists():
        try:
            for r in (json.loads(mp.read_text(encoding="utf-8")).get("sector_healthy_top3") or [])[:3]:
                _add(r.get("industry"), "top3看多")
        except Exception:  # noqa: BLE001 — 新增来源,坏 pack 不挡行业选择
            pass
    base = [i for i, tag in prov.items() if tag != "top3看多"][:k]
    extra = [i for i in prov if prov[i] == "top3看多" and i not in base]
    inds = base + extra
    return inds, {i: prov[i] for i in inds}
```

(`pack.py` 顶部确认已 `import json`,缺则加。)

`assemble.py` 两处接线:① `build_summary` 内 `pin_sec = _pinned_section(...)` 追加块(~874-876 行)之后,**镜像 pin_sec 的追加习惯**(读该处上下文,用同一累积变量)插:

```python
    # ── 🎯 看多行业 top3(P7:确定性零 LLM;presence-gated,失败不挡发布)──
    try:
        from autoresearch.scan.market import market_pack as _mp2, render_sector_top3
        top3_sec = render_sector_top3(_mp2(scan_dir))
    except Exception:  # noqa: BLE001
        top3_sec = ""
```

② 发布区 `record_calls` 那个 suppress 块(~1138-1142 行)之后加同款:

```python
        with contextlib.suppress(Exception):       # P7:top3 看多记账(分账,失败不阻发布)
            from autoresearch.learning.sector_ledger import record_top3
            from autoresearch.scan.market import market_pack as _mp3
            inds3 = [r["industry"] for r in (_mp3(scan_dir).get("sector_healthy_top3") or [])]
            n3 = record_top3(analysis_date, inds3)      # date 变量名与相邻 record_calls 调用一致,以现场为准
            if n3:
                print(f"[sector_ledger] 记 top3 看多 {n3} 条(source=deterministic_top3)")
```

- [ ] **Step 4: 通过**;**Step 5: 回归** — `uv run --no-sync python -m pytest tests/scan tests/learning -q` 全绿(sector_ledger 既有测试若锚死 render_report 旧列头,按「方向·来源」更新)
- [ ] **Step 6: Commit** — `feat(scan): P7 确定性看多行业top3——healthy分(三资格门+倒U动量+rank-sum)入 market_pack·L5小节·sector_ledger分账·brief派发并集不占cap·防锚定(L3/L4零痕迹)`

---

### Task 13: P7c — 一次性回算脚本 + 真跑读数落 spec

**Files:**
- Create: `autoresearch/research/sector_top3_backtest.py`
- Modify: `docs/specs/2026-07-12-scan-speed-perimeter-design.md`(§P7 验收前置追加读数)
- Test: 无单测(一次性读数脚本,遵守「不建常驻 harness」裁定);正确性靠复用 `sector_healthy_top3` 单一事实源 + Task 12 的单测。

- [ ] **Step 1: 写脚本**(全部数据出 `context/factor_lab/cache`(daily/daily_basic/moneyflow 逐日 pkl + stock_basic/static.pkl 的 industry);分数函数**直接 import market.sector_healthy_top3**,不复制公式):

```python
#!/usr/bin/env python3
"""P7 一次性回算:逐日 top3 → 成分等权 fwd_2_oc 中位 vs 合格宇宙中位 → 超额(零 LLM)。

design: docs/specs/2026-07-12-scan-speed-perimeter-design.md §P7 验收前置。
一次性读数脚本(非常驻 harness——遵守 gate_backtest 已删的裁定);分数 = 生产同一函数
`market.sector_healthy_top3`(单一事实源,带参改动自动同步)。数据 = factor_lab CACHE
(daily/daily_basic/moneyflow 逐日 pkl + stock_basic/static.pkl 的 industry)。

  uv run --no-sync python -m autoresearch.research.sector_top3_backtest --days 60
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from autoresearch.research.factor_lab import (
    CACHE,
    _moneyflow_struct_cols,
    forward_returns,
    load_price_pivots,
)
from autoresearch.data.tushare_source import _code6
from autoresearch.scan.market import sector_healthy_top3


def _num(s):
    return pd.to_numeric(s, errors="coerce")


def _day_frame(D: str, piv: dict, P: list[str], industry: pd.DataFrame) -> pd.DataFrame | None:
    """D 日横截面:industry/pct_60d/main_net_ratio/cmf_20/pe(与生产 frame 同名列,喂同一分数函数)。"""
    idx = P.index(D)
    if idx < 60:
        return None
    fp_db, fp_mf = CACHE / "daily_basic" / f"{D}.pkl", CACHE / "moneyflow" / f"{D}.pkl"
    if not fp_db.exists() or not fp_mf.exists():
        return None
    db, mf = pd.read_pickle(fp_db), pd.read_pickle(fp_mf)
    if db.empty or mf.empty:
        return None
    f = pd.DataFrame({"code": _code6(db["ts_code"]), "pe": _num(db["pe_ttm"])})
    C = piv["close"]
    f["pct_60d"] = ((C[D] / C[P[idx - 60]] - 1.0) * 100).reindex(f["code"]).to_numpy()
    win = P[idx - 19:idx + 1]
    import autoresearch.common.vol_series as vs
    H, L, Cc, A = (piv[k][win] for k in ("high", "low", "close", "amount"))
    f["cmf_20"] = vs.cmf(H, L, Cc, A, win).reindex(f["code"]).to_numpy()
    flow = _moneyflow_struct_cols(mf)
    f = f.merge(flow[["code", "main_net_yi"]], on="code", how="left")
    amt_yi = piv["amount"][D].reindex(f["code"]).to_numpy() / 1e5
    f["main_net_ratio"] = f["main_net_yi"] / np.where(amt_yi > 0, amt_yi, np.nan)
    f = f.merge(industry, on="code", how="left")
    return f.dropna(subset=["industry"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P7 top3 一次性回算(fwd_2_oc 超额)")
    ap.add_argument("--days", type=int, default=60)
    args = ap.parse_args(argv)
    sb = pd.read_pickle(CACHE / "stock_basic" / "static.pkl")
    if "industry" not in sb.columns:
        raise SystemExit("static.pkl 无 industry 列 —— 先跑 factor_lab harvest 刷新 stock_basic")
    industry = pd.DataFrame({"code": _code6(sb["ts_code"]), "industry": sb["industry"].astype(str)})
    P = sorted(p.stem for p in (CACHE / "daily").glob("*.pkl"))
    piv = load_price_pivots(P)
    rows = []
    for D in [d for d in P if P.index(d) >= 60 and P.index(d) + 2 < len(P)][-args.days:]:
        f = _day_frame(D, piv, P, industry)
        if f is None or not len(f):
            continue
        top3 = sector_healthy_top3(f)
        if not top3:
            rows.append({"date": D, "top3": "", "n_top3": 0})
            continue
        names = [r["industry"] for r in top3]
        fr = forward_returns(piv, P, D, fwd=10)["fwd_2_oc"]
        in_top3 = f["industry"].isin(names)
        top_ret = fr.reindex(f.loc[in_top3, "code"]).median() * 100
        mkt_ret = fr.reindex(f["code"]).median() * 100
        rows.append({"date": D, "top3": "|".join(names), "n_top3": len(names),
                     "top3_med_fwd2": round(float(top_ret), 3),
                     "mkt_med_fwd2": round(float(mkt_ret), 3),
                     "excess": round(float(top_ret - mkt_ret), 3)})
    out = pd.DataFrame(rows)
    dst = Path("reports/research/sector_top3_backtest.csv")
    dst.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(dst, index=False)
    m = out.dropna(subset=["excess"]) if "excess" in out.columns else out.iloc[0:0]
    if len(m):
        print(f"[top3-backtest] n={len(m)} 日 · 平均超额 {m['excess'].mean():+.3f}pp · "
              f"命中率 {(m['excess'] > 0).mean():.0%} · 中位 {m['excess'].median():+.3f}pp → {dst}")
    else:
        print(f"[top3-backtest] 无可算日(CACHE 覆盖不足)→ {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: 真跑** — `uv run --no-sync python -m autoresearch.research.sector_top3_backtest --days 60`
  Expected: 打印 n 日/平均超额/命中率;csv 落 `reports/research/`。CACHE 覆盖不足(<20 可算日)→ 如实降 days 并在读数旁注明覆盖。
- [ ] **Step 3: 读数落 spec** — 在设计稿 §P7「验收前置」条目下追加一行:`读数(YYYY-MM-DD 回算,n=?日):平均超额 ?pp · 命中率 ?% · 中位 ?pp——判读留用户`。
- [ ] **Step 4: Commit** — `feat(research): P7c top3 一次性回算脚本(复用生产分数函数)+ 首版读数落 spec`

---

### Task 14: 收尾 — 全量回归 + 真实命令冒烟

- [ ] **Step 1: 全量测试** — `uv run --no-sync python -m pytest -q` → 全绿(基线 1393+ 新增)
- [ ] **Step 2: prepare 冒烟(不碰真 staging)** —

```bash
mkdir -p /tmp/speedsmoke/2026-07-10
cp context/scan/2026-07-10/L2_gbdt_top200.csv /tmp/speedsmoke/2026-07-10/
time uv run --no-sync python -m autoresearch.scan.agents.l3_select prepare 2026-07-10 --root /tmp/speedsmoke
```

Expected: 完成 <2m(evidence 湖命中 + anns fast-fail;对照本次真跑同段 ~6m);`/tmp/speedsmoke/2026-07-10/_l3_table.md` 生成。
- [ ] **Step 3: slim 冒烟** — `time uv run --no-sync python -m autoresearch.scan.agents.l4_card harvest-slim 2026-07-10`(staging `_harvest_list.txt` 在位;11 票 wall 应从 ~6m 降至 ~1.5-2m;GATE3 JSON ok)
- [ ] **Step 4: prewarm 冒烟** — Task 6 已跑;补跑一次验证幂等(第二次应 <30s)
- [ ] **Step 5: 验收清单核对**(spec §4):新旋钮缺省 parity ✓ / P2 产物字节不变 ✓(diff /tmp/speedsmoke 与 staging 的 `_l3_table.md` 表体,允许 pass1 行外零差异)/ 防锚定 grep:`grep -L "看多行业" context/scan/2026-07-10/_l3_table.md context/scan/2026-07-10/_l4_prompt_*.md`(全部命中 = 无痕迹)
- [ ] **Step 6: Commit** — `chore(scan): 周边提速包+top3 收尾(全量回归+prepare/slim/prewarm 冒烟读数)`

## 下次真跑验收(不在本计划内,记入 spec §4)

`_stage_timing.json` 对照 spec §3 账目;效能表 effort 列 = echo;intel 首跑墙钟(>8m → 拧 max_queries);summary 出现「🎯 看多行业 top3」小节且 sector_ledger 有 deterministic_top3 行;`launchctl list` 可见 prewarm + 次日 19:30 `_prewarm.json` mtime 佐证。
