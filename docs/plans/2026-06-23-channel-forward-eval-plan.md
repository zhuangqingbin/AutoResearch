# 前向评估闭环(per-channel 归因 + 跨日 ledger)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给闭环学习层补上 recall channel 的前向归因——让"每一路召回(尤其 heat)有没有找到别人没找到的赢家"成为可累积的量化数字,渲染进 retro 报告。

**Architecture:** 在现有 `stage_eval.evaluate() → render → retro` 流上加纯函数 `channel_edge`(L1 段),复用 `binary_lift/rank_ic` 同款原语 + `retro.realized_returns` 的全市场 fwd;产物落 `context/scan/<date>/retro/channel_eval.csv`;再加独立小模块 `channel_ledger.py` 做跨日聚合。measure-only,不自动改 quota。

**Tech Stack:** Python / pandas / pytest;复用 `autoresearch.learning.stage_eval`、`autoresearch.learning.retro`。

## Global Constraints

- **零 LLM、单元测试零网络**:所有新函数是纯统计/IO,测试用合成 fixture + 显式注入 `realized`(`evaluate` 已支持 `realized=` 参数)。
- **measure-only**:不写回 / 不自动改 channel quota/floor。
- **excess 基准 = 全市场截面中位**:`mkt5 = realized["fwd_5_oc"].median()`、`mkt1 = realized["fwd_1_oo"].median()`(对整个 `realized` 取中位,非 recall 子集)。
- **buyable**:`realized` 缺 `buyable` 列时视作全 `True`;**均值/命中率只在 buyable 行上算**;`n_unbuyable` 单列计数。
- **复用常量**:`_RET_T5 = "fwd_5_oc"`、`_RET_T1 = "fwd_1_oo"`、`_code6`、`_as_bool`、`_read`、`rank_ic`、`RATINGS_5_TIER` 均在 `stage_eval.py` 现成。
- **code 一律 6 位 zfill**(`_code6`)。
- **空组 → None,不编 0**。

---

## File Structure

- **Modify** `autoresearch/learning/stage_eval.py`:加 `import re`;加纯函数 `channel_edge`、`_ratings_from_details`;`evaluate()` 加 L1 段 + L4 块 ratings 兜底 + 把 `outdir` 创建上提;`render_stage_eval` 加 L1 段。
- **Create** `autoresearch/learning/channel_ledger.py`:`roll` / `render` / `main`(跨日聚合 + CLI)。
- **Create** `tests/learning/test_channel_eval.py`:`channel_edge` 纯函数 + `evaluate` L1 集成 + `_ratings_from_details`。
- **Create** `tests/learning/test_channel_ledger.py`:`roll` 跨日 + `render` 样本少标注。
- **Modify** `.claude/skills/scan-market/screening-playbook.md`:附录 B 补 per-channel edge / ledger 读法。

---

## Task 1: `channel_edge` 纯函数(per-channel 前向归因)

**Files:**
- Modify: `autoresearch/learning/stage_eval.py`(在 `verdict_edge` 后、`rating_score` 前插入)
- Test: `tests/learning/test_channel_eval.py`(新建)

**Interfaces:**
- Consumes: `_code6`、`_as_bool`、`_RET_T5`、`_RET_T1`(同模块现成)。
- Produces: `channel_edge(recall: pd.DataFrame, realized: pd.DataFrame) -> pd.DataFrame`,每路一行,列固定为 `["channel","n_recalled","n_unique","n_unbuyable","mean_excess_t5","unique_excess_t5","mean_excess_t1","hit_rate_t5"]`,按 `unique_excess_t5` 降序(None 殿后)。

- [ ] **Step 1: 写失败测试**

新建 `tests/learning/test_channel_eval.py`:

```python
"""per-channel 前向归因 channel_edge + evaluate L1 段 + ratings 兜底。NO network(合成)。"""
from __future__ import annotations

import pandas as pd

from autoresearch.learning.stage_eval import channel_edge


def _recall():
    # 5 只,带 recall_channels provenance(| 分隔)
    return pd.DataFrame({
        "code": ["000001", "000002", "000003", "000004", "000005"],
        "recall_channels": ["composite|heat", "heat", "composite", "composite|momentum", "heat"],
        "n_channels": [2, 1, 1, 2, 1],
    })


def _realized():
    # 全市场(含 2 只未召回 000006/000007 以定全市场中位);000002 不可买
    return pd.DataFrame({
        "code": ["000001", "000002", "000003", "000004", "000005", "000006", "000007"],
        "fwd_1_oo": [0.05, 0.03, -0.02, 0.01, 0.04, 0.00, -0.01],
        "fwd_5_oc": [0.10, 0.06, -0.04, 0.02, 0.08, 0.00, -0.02],
        "buyable": [True, False, True, True, True, True, True],
    })


def test_channel_edge_unique_membership_buyable_excess():
    ce = channel_edge(_recall(), _realized())
    assert list(ce.columns) == ["channel", "n_recalled", "n_unique", "n_unbuyable",
                                "mean_excess_t5", "unique_excess_t5", "mean_excess_t1", "hit_rate_t5"]
    heat = ce[ce["channel"] == "heat"].iloc[0]
    # 全市场 fwd_5_oc 中位 = 0.02。heat members=000001/000002/000005,000002 不可买被剔。
    assert heat["n_recalled"] == 3 and heat["n_unique"] == 2 and heat["n_unbuyable"] == 1
    # mean_excess_t5(heat,buyable 000001/000005)=((0.10-0.02)+(0.08-0.02))/2 = 0.07
    assert abs(heat["mean_excess_t5"] - 0.07) < 1e-9
    # unique=只 heat 一路(000002 不可买、000005 可买)→ 仅 000005:0.08-0.02=0.06
    assert abs(heat["unique_excess_t5"] - 0.06) < 1e-9
    assert heat["hit_rate_t5"] == 1.0
    # composite unique=000003 一只:-0.04-0.02 = -0.06
    comp = ce[ce["channel"] == "composite"].iloc[0]
    assert abs(comp["unique_excess_t5"] - (-0.06)) < 1e-9
    # momentum 无独占票(000004 是 composite|momentum)→ unique_excess_t5 None
    mom = ce[ce["channel"] == "momentum"].iloc[0]
    assert mom["unique_excess_t5"] is None or pd.isna(mom["unique_excess_t5"])
    # 降序:heat(0.06) 排在 composite(-0.06) 前
    order = ce["channel"].tolist()
    assert order.index("heat") < order.index("composite")


def test_channel_edge_empty_or_no_provenance():
    assert channel_edge(pd.DataFrame(), _realized()).empty
    assert channel_edge(pd.DataFrame({"code": ["000001"]}), _realized()).empty  # 无 recall_channels 列
```

- [ ] **Step 2: 运行测试,确认失败**

Run: `uv run --no-sync python -m pytest tests/learning/test_channel_eval.py -q`
Expected: FAIL — `ImportError: cannot import name 'channel_edge'`

- [ ] **Step 3: 实现 `channel_edge`**

在 `autoresearch/learning/stage_eval.py` 的 `verdict_edge` 函数后插入:

```python
def channel_edge(recall: pd.DataFrame, realized: pd.DataFrame) -> pd.DataFrame:
    """L1 多路召回 provenance × 已实现 fwd → 每路一行的前向归因(纯函数,零网络)。

    recall:   L1_recall_top1000(需 code, recall_channels);realized:全市场(code, fwd_1_oo, fwd_5_oc, buyable)。
    excess = 个股 fwd − 全市场截面中位;均值/命中只在 buyable 行;unique = recall_channels 仅此一路(边际 alpha)。
    返回列固定,按 unique_excess_t5 降序(None 殿后)。
    """
    cols = ["channel", "n_recalled", "n_unique", "n_unbuyable",
            "mean_excess_t5", "unique_excess_t5", "mean_excess_t1", "hit_rate_t5"]
    if recall is None or not len(recall) or "recall_channels" not in recall.columns:
        return pd.DataFrame(columns=cols)
    r = _code6(recall)[["code", "recall_channels"]].copy()
    rl = _code6(realized).copy()
    for c in (_RET_T5, _RET_T1):
        rl[c] = pd.to_numeric(rl.get(c), errors="coerce")
    if "buyable" not in rl.columns:
        rl["buyable"] = True
    mkt5, mkt1 = rl[_RET_T5].median(), rl[_RET_T1].median()
    m = r.merge(rl[["code", _RET_T5, _RET_T1, "buyable"]], on="code", how="left")
    m["excess_t5"] = m[_RET_T5] - mkt5
    m["excess_t1"] = m[_RET_T1] - mkt1
    m["buyable"] = _as_bool(m["buyable"].fillna(True))
    m["chans"] = m["recall_channels"].fillna("").map(lambda s: set(str(s).split("|")) - {""})

    def _mean(s):
        s = s.dropna()
        return round(float(s.mean()), 4) if len(s) else None

    rows = []
    for c in sorted({x for cs in m["chans"] for x in cs}):
        members = m[m["chans"].map(lambda cs, c=c: c in cs)]
        unique = m[m["recall_channels"].astype(str) == c]
        mb, ub = members[members["buyable"]], unique[unique["buyable"]]
        ex = mb["excess_t5"].dropna()
        rows.append({
            "channel": c,
            "n_recalled": int(len(members)),
            "n_unique": int(len(unique)),
            "n_unbuyable": int((~members["buyable"]).sum()),
            "mean_excess_t5": _mean(mb["excess_t5"]),
            "unique_excess_t5": _mean(ub["excess_t5"]),
            "mean_excess_t1": _mean(mb["excess_t1"]),
            "hit_rate_t5": round(float((ex > 0).mean()), 4) if len(ex) else None,
        })
    out = pd.DataFrame(rows, columns=cols)
    return out.sort_values("unique_excess_t5", ascending=False, na_position="last").reset_index(drop=True)
```

- [ ] **Step 4: 运行测试,确认通过**

Run: `uv run --no-sync python -m pytest tests/learning/test_channel_eval.py -q`
Expected: PASS(2 passed)

- [ ] **Step 5: 提交**

```bash
git add autoresearch/learning/stage_eval.py tests/learning/test_channel_eval.py
git commit -m "feat(learning): channel_edge per-channel 前向归因(excess-vs-market·unique 边际·buyable-aware)"
```

---

## Task 2: `evaluate()` 加 L1 段 + 落 `channel_eval.csv`

**Files:**
- Modify: `autoresearch/learning/stage_eval.py`(`evaluate` 函数体)
- Test: `tests/learning/test_channel_eval.py`(追加)

**Interfaces:**
- Consumes: `channel_edge`(Task 1)、`rank_ic`、`_read`、`_code6`(现成)、`evaluate(date, scan_root=, realized=)`。
- Produces: `res["stages"]["L1"] = {"by_channel": [...records], "ic_n_channels_t5": float|None}`;文件 `context/scan/<date>/retro/channel_eval.csv`。

- [ ] **Step 1: 写失败测试**

追加到 `tests/learning/test_channel_eval.py`:

```python
def test_evaluate_writes_l1_channel_block(tmp_path):
    sdir = tmp_path / "2026-06-20"
    sdir.mkdir(parents=True)
    _recall().assign(composite=[90, 80, 70, 60, 50]).to_csv(sdir / "L1_recall_top1000.csv", index=False)
    from autoresearch.learning.stage_eval import evaluate
    res = evaluate("2026-06-20", scan_root=tmp_path, realized=_realized())
    assert "L1" in res["stages"]
    assert len(res["stages"]["L1"]["by_channel"]) >= 3              # composite/heat/momentum…
    assert "ic_n_channels_t5" in res["stages"]["L1"]
    assert (sdir / "retro" / "channel_eval.csv").exists()
    ce = pd.read_csv(sdir / "retro" / "channel_eval.csv")
    assert "unique_excess_t5" in ce.columns and "heat" in set(ce["channel"])
```

- [ ] **Step 2: 运行测试,确认失败**

Run: `uv run --no-sync python -m pytest tests/learning/test_channel_eval.py::test_evaluate_writes_l1_channel_block -q`
Expected: FAIL — `KeyError: 'L1'`(evaluate 尚无 L1 段)

- [ ] **Step 3: 改 `evaluate()`**

在 `autoresearch/learning/stage_eval.py` 的 `evaluate` 中。**(a)** 把现有结尾的 outdir 创建上提:删掉接近函数末尾的两行
```python
    outdir = sdir / "retro"
    outdir.mkdir(parents=True, exist_ok=True)
```
并在 `res: dict = {"date": date, ...}` 那行**之后**紧接插入(outdir 上提 + L1 段):

```python
    outdir = sdir / "retro"
    outdir.mkdir(parents=True, exist_ok=True)

    # L1:多路召回 provenance × fwd → 每路边际超额 + n_channels 共振 IC
    recall_l1 = _read(sdir / "L1_recall_top1000.csv")
    if recall_l1 is not None and "recall_channels" in recall_l1.columns:
        recall_l1 = _code6(recall_l1)
        ce = channel_edge(recall_l1, realized)
        ce.to_csv(outdir / "channel_eval.csv", index=False)
        m1 = recall_l1.merge(_code6(realized), on="code", how="left")
        res["stages"]["L1"] = {"by_channel": ce.to_dict("records"),
                               "ic_n_channels_t5": rank_ic(m1, "n_channels", _RET_T5)}
```

(末尾仅保留 `_flat_csv(res).to_csv(outdir / "stage_eval.csv", index=False)` + `return res`,outdir 已在上方创建。)

- [ ] **Step 4: 运行测试,确认通过**

Run: `uv run --no-sync python -m pytest tests/learning/test_channel_eval.py -q`
Expected: PASS(3 passed)

- [ ] **Step 5: 提交**

```bash
git add autoresearch/learning/stage_eval.py tests/learning/test_channel_eval.py
git commit -m "feat(learning): evaluate 加 L1 段,落 channel_eval.csv + n_channels 共振 IC"
```

---

## Task 3: `render_stage_eval` 加 L1 段

**Files:**
- Modify: `autoresearch/learning/stage_eval.py`(`render_stage_eval`)
- Test: `tests/learning/test_channel_eval.py`(追加)

**Interfaces:**
- Consumes: `render_stage_eval(res: dict) -> list[str]`、`_pct`(现成)。
- Produces: 输出列表新增一行以 `- **L1 多路召回**` 开头的 markdown。

- [ ] **Step 1: 写失败测试**

追加:

```python
def test_render_has_l1_channel_section():
    from autoresearch.learning.stage_eval import render_stage_eval
    res = {"date": "2026-06-20", "n_realized": 5, "stages": {"L1": {
        "by_channel": [{"channel": "heat", "n_unique": 2, "unique_excess_t5": 0.06, "hit_rate_t5": 1.0},
                       {"channel": "composite", "n_unique": 1, "unique_excess_t5": -0.06, "hit_rate_t5": 0.0}],
        "ic_n_channels_t5": 0.12}}}
    md = "\n".join(render_stage_eval(res))
    assert "L1 多路召回" in md and "heat" in md and "+6.0%" in md
```

- [ ] **Step 2: 运行测试,确认失败**

Run: `uv run --no-sync python -m pytest tests/learning/test_channel_eval.py::test_render_has_l1_channel_section -q`
Expected: FAIL — 输出无 "L1 多路召回"

- [ ] **Step 3: 改 `render_stage_eval`**

在 `render_stage_eval` 里、`if "L2" in s:` 之前插入:

```python
    if "L1" in s:
        d = s["L1"]
        chans = sorted(d.get("by_channel", []),
                       key=lambda r: (r.get("unique_excess_t5") is None, -(r.get("unique_excess_t5") or 0)))
        head = "、".join(f"{r['channel']} 边际{_pct(r.get('unique_excess_t5'))}×{r.get('n_unique', 0)}"
                         f"(命中{_pct(r.get('hit_rate_t5'))})" for r in chans[:6])
        out.append(f"- **L1 多路召回**:各路边际超额(unique vs 全市场,T+5):{head or '—'}"
                   f";n_channels 共振 IC(T+5){d.get('ic_n_channels_t5')}"
                   f"  _(边际>0=该路找到别人没找到的赢家,值得留)_")
```

- [ ] **Step 4: 运行测试,确认通过**

Run: `uv run --no-sync python -m pytest tests/learning/test_channel_eval.py -q`
Expected: PASS(4 passed)

- [ ] **Step 5: 提交**

```bash
git add autoresearch/learning/stage_eval.py tests/learning/test_channel_eval.py
git commit -m "feat(learning): retro 报告渲染 L1 多路召回边际超额段"
```

---

## Task 4: `_ratings_from_details` 兜底 + 接入 L4 块

**Files:**
- Modify: `autoresearch/learning/stage_eval.py`(加 `import re`、`_ratings_from_details`、L4 块兜底)
- Test: `tests/learning/test_channel_eval.py`(追加)

**Interfaces:**
- Consumes: `RATINGS_5_TIER`、`Path`(现成)。
- Produces: `_ratings_from_details(date: str, scan_root: Path | None = None) -> dict[str, str]`(`{6位code: 五档评级}`)。

- [ ] **Step 1: 写失败测试**

追加:

```python
def test_ratings_from_details_parses_and_filters(tmp_path):
    from autoresearch.learning.stage_eval import _ratings_from_details
    d = tmp_path / "2026-06-20" / "details"
    d.mkdir(parents=True)
    (d / "000001.md").write_text("dash\n**Rating**: Overweight\n更多", encoding="utf-8")
    (d / "000002.md").write_text("**Rating**：Hold\n", encoding="utf-8")          # 全角冒号
    (d / "000003.md").write_text("**Rating**: Banana\n", encoding="utf-8")        # 非五档→剔
    out = _ratings_from_details("2026-06-20", scan_root=tmp_path)
    assert out == {"000001": "Overweight", "000002": "Hold"}


def test_ratings_from_details_missing_dir(tmp_path):
    from autoresearch.learning.stage_eval import _ratings_from_details
    assert _ratings_from_details("2026-06-20", scan_root=tmp_path) == {}
```

- [ ] **Step 2: 运行测试,确认失败**

Run: `uv run --no-sync python -m pytest tests/learning/test_channel_eval.py -k ratings_from_details -q`
Expected: FAIL — `ImportError: cannot import name '_ratings_from_details'`

- [ ] **Step 3: 实现 + 接入**

(a) 在 `stage_eval.py` 顶部 `import sys` 旁加 `import re`。
(b) 在 `rating_score` 函数后插入:

```python
def _ratings_from_details(date: str, scan_root: Path | None = None) -> dict[str, str]:
    """从 context/scan/<date>/details/<code>.md 解析 {6位code: 五档评级}(发布前兜底)。

    正则取 `**Rating**` 行(半/全角冒号);非五档 / 无文件 / 无目录 → 跳过该只。
    """
    scan_root = scan_root or Path("context/scan")
    ddir = scan_root / date / "details"
    if not ddir.exists():
        return {}
    valid = set(RATINGS_5_TIER)
    out: dict[str, str] = {}
    for p in sorted(ddir.glob("*.md")):
        code = p.stem[:6]
        if not code.isdigit():
            continue
        m = re.search(r"\*\*Rating\*\*[:：]?\s*([A-Za-z]+)", p.read_text(encoding="utf-8"))
        if m and m.group(1) in valid:
            out[code] = m.group(1)
    return out
```

(c) 在 `evaluate()` 的 L4 块,把
```python
    ratings = retro._buylist(date, report_root)
```
改为
```python
    ratings = retro._buylist(date, report_root) or _ratings_from_details(date, scan_root)
```

- [ ] **Step 4: 运行测试,确认通过**

Run: `uv run --no-sync python -m pytest tests/learning/test_channel_eval.py -q`
Expected: PASS(6 passed)

- [ ] **Step 5: 提交**

```bash
git add autoresearch/learning/stage_eval.py tests/learning/test_channel_eval.py
git commit -m "feat(learning): _ratings_from_details 兜底,L4 per-rating 在未发布时也能评估"
```

---

## Task 5: `channel_ledger.py` 跨日 rollup + CLI

**Files:**
- Create: `autoresearch/learning/channel_ledger.py`
- Test: `tests/learning/test_channel_ledger.py`(新建)

**Interfaces:**
- Consumes: 各 `context/scan/<date>/retro/channel_eval.csv`(Task 2 产物,列见 Task 1)。
- Produces: `roll(scan_root=None) -> pd.DataFrame`(列 `channel,n_days,sum_unique,mean_unique_excess_t5,mean_excess_t5,mean_hit_rate_t5`,按 `mean_unique_excess_t5` 降序);`render(ledger) -> list[str]`;`main() -> int`。

- [ ] **Step 1: 写失败测试**

新建 `tests/learning/test_channel_ledger.py`:

```python
"""跨日 channel ledger:聚合多日 channel_eval.csv → 每路滚动边际超额。NO network。"""
from __future__ import annotations

import pandas as pd

from autoresearch.learning.channel_ledger import render, roll


def _write_day(root, date, heat_ue, comp_ue):
    rd = root / date / "retro"
    rd.mkdir(parents=True)
    pd.DataFrame([
        {"channel": "heat", "n_recalled": 10, "n_unique": 3, "n_unbuyable": 0,
         "mean_excess_t5": heat_ue, "unique_excess_t5": heat_ue, "mean_excess_t1": 0.0, "hit_rate_t5": 0.6},
        {"channel": "composite", "n_recalled": 50, "n_unique": 5, "n_unbuyable": 0,
         "mean_excess_t5": comp_ue, "unique_excess_t5": comp_ue, "mean_excess_t1": 0.0, "hit_rate_t5": 0.4},
    ]).to_csv(rd / "channel_eval.csv", index=False)


def test_roll_aggregates_across_days(tmp_path):
    _write_day(tmp_path, "2026-06-18", 0.02, -0.01)
    _write_day(tmp_path, "2026-06-19", 0.04, -0.03)
    _write_day(tmp_path, "2026-06-20", 0.06, -0.02)
    led = roll(scan_root=tmp_path)
    heat = led[led["channel"] == "heat"].iloc[0]
    assert int(heat["n_days"]) == 3 and int(heat["sum_unique"]) == 9
    assert abs(heat["mean_unique_excess_t5"] - 0.04) < 1e-9          # mean(0.02,0.04,0.06)
    assert led.iloc[0]["channel"] == "heat"                          # 降序:heat 在 composite 前


def test_roll_empty_when_no_files(tmp_path):
    led = roll(scan_root=tmp_path)
    assert led.empty and "channel" in led.columns


def test_render_flags_thin_sample():
    led = pd.DataFrame([{"channel": "heat", "n_days": 2, "sum_unique": 4,
                         "mean_unique_excess_t5": 0.03, "mean_excess_t5": 0.02, "mean_hit_rate_t5": 0.6}])
    md = "\n".join(render(led))
    assert "⚠样本少" in md and "heat" in md and "+3.0%" in md
```

- [ ] **Step 2: 运行测试,确认失败**

Run: `uv run --no-sync python -m pytest tests/learning/test_channel_ledger.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'autoresearch.learning.channel_ledger'`

- [ ] **Step 3: 实现模块**

新建 `autoresearch/learning/channel_ledger.py`:

```python
#!/usr/bin/env python3
"""跨日聚合各 scan 日的 retro/channel_eval.csv → 每路滚动边际超额(单日是噪声,跨日才是信号)。零 LLM。

用法:
  uv run --no-sync python -m autoresearch.learning.channel_ledger     # 滚动 → reports/learning/channel_ledger.md
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

_COLS = ["channel", "n_days", "sum_unique", "mean_unique_excess_t5", "mean_excess_t5", "mean_hit_rate_t5"]


def roll(scan_root: Path | None = None) -> pd.DataFrame:
    """聚合 context/scan/*/retro/channel_eval.csv 跨日 → 每路滚动汇总(按边际超额降序)。"""
    scan_root = scan_root or Path("context/scan")
    frames = []
    for p in sorted(scan_root.glob("*/retro/channel_eval.csv")):
        try:
            df = pd.read_csv(p)
        except Exception:
            continue
        if "channel" in df.columns and len(df):
            frames.append(df)
    if not frames:
        return pd.DataFrame(columns=_COLS)
    alld = pd.concat(frames, ignore_index=True)
    for c in ("unique_excess_t5", "mean_excess_t5", "hit_rate_t5", "n_unique"):
        alld[c] = pd.to_numeric(alld.get(c), errors="coerce")
    out = alld.groupby("channel").agg(
        n_days=("channel", "size"),
        sum_unique=("n_unique", "sum"),
        mean_unique_excess_t5=("unique_excess_t5", "mean"),
        mean_excess_t5=("mean_excess_t5", "mean"),
        mean_hit_rate_t5=("hit_rate_t5", "mean"),
    ).reset_index()
    for c in ("mean_unique_excess_t5", "mean_excess_t5", "mean_hit_rate_t5"):
        out[c] = out[c].round(4)
    return out.sort_values("mean_unique_excess_t5", ascending=False, na_position="last").reset_index(drop=True)


def render(ledger: pd.DataFrame) -> list[str]:
    """ledger → markdown 表(每路近 N 日边际超额 + 命中;n_days<3 标 ⚠样本少)。"""
    out = ["# 召回各路前向边际超额(跨日 ledger)", ""]
    if ledger is None or not len(ledger):
        return out + ["_无 channel_eval 数据(需先 retro 评估出 fwd)_"]
    out += ["| 路 | 天数 | Σunique | 边际超额T5 | membership超额T5 | 命中率T5 |",
            "|---|---|---|---|---|---|"]

    def f(x):
        return "—" if x is None or pd.isna(x) else f"{x * 100:+.1f}%"

    for r in ledger.itertuples(index=False):
        thin = " ⚠样本少" if (r.n_days or 0) < 3 else ""
        out.append(f"| {r.channel}{thin} | {int(r.n_days)} | {int(r.sum_unique)} | "
                   f"{f(r.mean_unique_excess_t5)} | {f(r.mean_excess_t5)} | {f(r.mean_hit_rate_t5)} |")
    return out


def main() -> int:
    led = roll()
    body = "\n".join(render(led))
    outp = Path("reports/learning/channel_ledger.md")
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(body, encoding="utf-8")
    print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 运行测试,确认通过**

Run: `uv run --no-sync python -m pytest tests/learning/test_channel_ledger.py -q`
Expected: PASS(3 passed)

- [ ] **Step 5: 提交**

```bash
git add autoresearch/learning/channel_ledger.py tests/learning/test_channel_ledger.py
git commit -m "feat(learning): channel_ledger 跨日聚合每路前向边际超额 + CLI"
```

---

## Task 6: 文档 + 全量验证

**Files:**
- Modify: `.claude/skills/scan-market/screening-playbook.md`(附录 B 区)
- Modify: `.claude/skills/scan-retro/SKILL.md`(若存在;加一条指引)

- [ ] **Step 1: 更新 screening-playbook.md**

在 `screening-playbook.md` 附录 B(召回权重校准 / retro)区末尾追加:

```markdown
- **per-channel 前向归因(`stage_eval` L1 段 + `channel_ledger`)**:retro 评估每只召回票的 T+5 **截面超额**(个股 fwd − 全市场中位),按 `recall_channels` provenance 归到各路 → `context/scan/<date>/retro/channel_eval.csv`。头条看 **unique_excess_t5**(仅此一路独占票的超额 = 边际 alpha:这路有没有找到别人没找到的赢家),buyable-aware(D+1 买不进的剔出)。**单日是噪声**;跨日滚动:`python -m autoresearch.learning.channel_ledger` → `reports/learning/channel_ledger.md`(`n_days<3` 标 ⚠样本少)。**measure-only**:据此人/scan-retro 决定调不调某路 quota,不自动改。
```

- [ ] **Step 2: 更新 scan-retro skill(若存在)**

Run: `ls .claude/skills/scan-retro/SKILL.md 2>/dev/null && echo EXISTS || echo SKIP`
若 EXISTS:在其复盘步骤列表里加一条 bullet:

```markdown
- **per-channel edge**:跑 `python -m autoresearch.learning.channel_ledger` 看各路跨日边际超额(`reports/learning/channel_ledger.md`);某路 `unique_excess_t5` 持续为负且 `n_days≥3` → 考虑下调其 quota(人工决定,不自动)。
```
若 SKIP:跳过本步。

- [ ] **Step 3: 全量测试 + ruff**

Run: `uv run --no-sync python -m pytest tests/learning/ -q && uv run --no-sync ruff check autoresearch/learning/ tests/learning/`
Expected: all pass + `All checks passed!`

- [ ] **Step 4: 提交**

```bash
git add .claude/skills/scan-market/screening-playbook.md .claude/skills/scan-retro/SKILL.md
git commit -m "docs(scan): per-channel 前向归因 + channel_ledger 读法(playbook + scan-retro)"
```

- [ ] **Step 5: 完成开发分支**

**REQUIRED SUB-SKILL:** Use superpowers:finishing-a-development-branch(分支 `channel-forward-eval`;呈现合并/PR 选项)。

---

## Self-Review(写完即查)

**1. Spec 覆盖**:
- channel_edge(unique/membership/buyable/excess-vs-market/n_channels IC)→ Task 1+2 ✓
- evaluate L1 段 + channel_eval.csv → Task 2 ✓
- render L1 段 → Task 3 ✓
- _ratings_from_details 兜底 → Task 4 ✓
- channel_ledger 跨日 + n_days<3 标注 → Task 5 ✓
- 文档 → Task 6 ✓
- 非目标(不自动改 quota / horizon 仅 T1·T5)→ 全程未引入,符合 ✓

**2. 占位扫描**:无 TBD/TODO;每个改码步骤含完整代码。✓

**3. 类型一致**:`channel_edge` 返回列名(`unique_excess_t5` 等)在 Task 2 写 CSV、Task 3 render、Task 5 roll 三处引用一致;`_RET_T5`/`_RET_T1` 全程同名;`roll` 输出列 `mean_unique_excess_t5` 与 render 引用一致。✓
