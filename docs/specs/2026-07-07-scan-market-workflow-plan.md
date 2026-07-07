# scan-market 全流程 Workflow 化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 scan-market 六段漏斗从"SKILL.md 手工清单"改为一个确定性 Workflow JS 脚本(`.claude/workflows/scan-market.js`),后台一次跑完,四道校验门取代人工检查点。

**Architecture:** 先在 Python 源头补 3 个确定性 CLI 入口(workflow 无 Bash/文件权限,只能派 agent 跑命令),把 3 个数据坑修死;再写 JS 脚本用 `agent()` 编排——确定性步骤走 `general-purpose` Bash-agent、判断步骤走现有 4 个 leaf agent(带契约级 `effort`)、分支控制值走 schema'd reader-agent。文件仍是数据总线。

**Tech Stack:** Python 3(pandas / argparse,venv-only akshare·tushare·lightgbm)· pytest · Claude Code Workflow(plain JS,无 TS)· 现有 agent-def(`.claude/agents/l3-rank|l4-card|buy-skeptic|sector-brief.md`)。

## Global Constraints

- **所有 Python 命令必须 `uv run --no-sync`**(不误删 venv-only 依赖),仓库根目录运行。
- **代码统一 6 位零填字符串**(`^\d{6}$`);读 CSV 的 code/ticker 列用 `dtype={"code":str}` 或 `.str.zfill(6)`。
- **Workflow 脚本是 plain JS**:无类型注解;`Date.now()`/`Math.random()`/argless `new Date()` 会抛错;无文件系统/Bash(只能 `agent()`);日期经 `args.date` 传入。
- **meta 必须是纯字面量**(无变量/函数/插值);`meta.phases` 标题与 `phase()` 调用逐字匹配。
- **718 现有测试必须持续绿**:`uv run --no-sync python -m pytest tests/ -q`。
- **buy-skeptic mode A(买单证伪)已于 07-06 移除**——L4 无 card→skeptic 阶段;仅保留 0 买日 mode-B 机会成本红队(抽检)。
- **确定性层零 LLM**;个股评级只由本卡 rubric 三门定;所有产出带"仅供研究,非投资建议"。
- **SKILL.md / STAGES.md 仍是语义真值源**;workflow 脚本注释引用它们,不复制人设。
- **默认中文输出。**

---

## File Structure

| 文件 | 责任 | 本计划动作 |
|---|---|---|
| `autoresearch/scan/agents/l3_select.py` | L3 表构建 + finalist 合并 | **改**:加 `write_finalists()`(bug#1)+ `prepare_l3_table()` + `main()` CLI(`finalists`/`prepare`) |
| `autoresearch/scan/agents/l4_card.py` | L4 派发包 + pledge | **改**:加 `harvest_slim_batch()`(bug#3=GATE3)+ `main` 加 `harvest-slim` 子命令 |
| `autoresearch/scan/gates.py` | workflow 校验门(GATE1/2/4 + 红队门) | **建** |
| `.claude/workflows/scan-market.js` | 全流程编排脚本 | **建** |
| `tests/scan/test_finalists_writer.py` | Task 1 测试 | **建** |
| `tests/scan/test_l3_prepare.py` | Task 1 prepare 测试 | **建** |
| `tests/scan/test_harvest_slim.py` | Task 2 测试 | **建** |
| `tests/scan/test_gates.py` | Task 3 测试 | **建** |
| `.claude/skills/scan-market/SKILL.md` | 编排规格 | **改**(Task 5:加 workflow 指针) |

现有 leaf agent-def(`l3-rank`/`l4-card`/`buy-skeptic`/`sector-brief`)与 `menu.py`(`l4_budget`/`sentinel_advice`/`should_run_opportunity_redteam`)、`assemble.py`(self_review 硬门 + `dump_gate_fires`)、`prelude.py`、`frame.py`、`universe.py`、`sector/{reuse,pack}.py` **不改**,原样被 workflow 调用。

---

## Task 1: l3_select CLI —— 确定性 finalists 写盘(修 bug#1)+ L3 表准备

**Files:**
- Modify: `autoresearch/scan/agents/l3_select.py`(加 `write_finalists`、`prepare_l3_table`、`main`)
- Test: `tests/scan/test_finalists_writer.py`(建)、`tests/scan/test_l3_prepare.py`(建)

**Interfaces:**
- Consumes: `merge_l3_finalists_v2(judged, target)`(现有,l3_select.py:279)、`harvest_l3_evidence(date,codes,root)`(:238)、`harvest_l3_news(date,codes,root)`(l3_news.py:121)、`l3_table_md(date,root,delta,...)`(:141)。`_l3_judged.json`(l3-rank agent 落,~28 元素,字段 code/name/sector/lane/conviction/fragility/triage_lean/thesis/risk/catalyst/lenses/sentiment)、`L2_gbdt_top200.csv`。
- Produces: `write_finalists(date, budget=30, root=None) -> {"judged_n":int,"finalists_n":int}`(写 `L3_judged_full.csv` + `finalists.csv`,**代码全 6 位**);`prepare_l3_table(date, root=None, delta=True, do_harvest=True) -> {"codes":int,"table_bytes":int}`(写 `_l3_table.md`)。CLI:`python -m autoresearch.scan.agents.l3_select {finalists|prepare} <date> [--budget N] [--root PATH]`。

- [ ] **Step 1: 写失败测试 —— finalists 前导零存活(bug#1 回归)**

`tests/scan/test_finalists_writer.py`:
```python
import csv
import json

import pandas as pd

from autoresearch.scan.agents.l3_select import write_finalists


def _judged():
    # agent 的 JSON 可能把 code 写成 int 62 或 str "000063",两种都要能救回
    return [
        {"code": 62, "name": "华东电脑", "sector": "计算机", "lane": "value",
         "conviction": 72, "fragility": 40, "triage_lean": "标配",
         "thesis": "t", "risk": "r", "catalyst": "c", "lenses": "价值", "sentiment": "中性"},
        {"code": "000063", "name": "中兴通讯", "sector": "通信", "lane": "trend",
         "conviction": 66, "fragility": 30, "triage_lean": "标配",
         "thesis": "t", "risk": "r", "catalyst": "c", "lenses": "趋势", "sentiment": "偏多"},
    ]


def test_write_finalists_preserves_leading_zeros(tmp_path):
    base = tmp_path / "context" / "scan"
    d = base / "2026-07-07"
    d.mkdir(parents=True)
    (d / "_l3_judged.json").write_text(json.dumps(_judged()), encoding="utf-8")
    pd.DataFrame({"code": ["000062", "000063"], "pct_60d": [12.0, 8.0]}).to_csv(
        d / "L2_gbdt_top200.csv", index=False)

    res = write_finalists("2026-07-07", budget=5, root=base)

    assert res["finalists_n"] == 2
    rows = list(csv.DictReader((d / "finalists.csv").open(encoding="utf-8")))
    assert {r["code"] for r in rows} == {"000062", "000063"}      # 前导零存活
    assert all(r["ticker"] == r["code"] for r in rows)             # ticker 与 code 同 6 位
```

- [ ] **Step 2: 运行,确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_finalists_writer.py -q`
Expected: FAIL —— `ImportError: cannot import name 'write_finalists'`

- [ ] **Step 3: 实现 `write_finalists` + `prepare_l3_table` + `main`**

在 `autoresearch/scan/agents/l3_select.py` 末尾加(文件已 import `json`、`pd`、`from pathlib import Path`;若缺 `import json` 则在顶部补):
```python
def write_finalists(date: str, budget: int = 30, root: Path | None = None) -> dict:
    """确定性写 finalists.csv + L3_judged_full.csv(workflow L3 后确定性入口,取代手工 glue)。

    读 l3-rank agent 落的 _l3_judged.json → 从 L2 回填 pct_60d(供 merge 混合配额)
    → merge_l3_finalists_v2 → 写盘。**全程 6 位零填**,修 000062→62 的 CSV 往返坑。
    """
    base = Path(root) if root else Path("context/scan")
    scan_dir = base / date
    picks = json.loads((scan_dir / "_l3_judged.json").read_text(encoding="utf-8"))
    jd = pd.DataFrame(picks)
    if jd.empty or "code" not in jd.columns:
        raise ValueError(f"_l3_judged.json 空或缺 code 列:{scan_dir / '_l3_judged.json'}")
    jd["code"] = jd["code"].astype(str).str.zfill(6)
    l2p = scan_dir / "L2_gbdt_top200.csv"
    if l2p.exists() and "pct_60d" not in jd.columns:
        l2 = pd.read_csv(l2p, dtype={"code": str})
        l2["code"] = l2["code"].astype(str).str.zfill(6)
        if "pct_60d" in l2.columns:
            jd = jd.merge(l2[["code", "pct_60d"]], on="code", how="left")
    jd.to_csv(scan_dir / "L3_judged_full.csv", index=False)       # 全量判断(retro/assemble/trace)
    fin = merge_l3_finalists_v2(jd, target=budget)                # 内部 zfill code + ticker=code
    fin.to_csv(scan_dir / "finalists.csv", index=False)
    return {"judged_n": int(len(jd)), "finalists_n": int(len(fin))}


def prepare_l3_table(date: str, root: Path | None = None, delta: bool = True,
                     do_harvest: bool = True) -> dict:
    """L3 精排前的确定性件:harvest 证据/公告情感 + 构建紧凑表 → 写 _l3_table.md(l3-rank agent 读)。"""
    base = Path(root) if root else Path("context/scan")
    scan_dir = base / date
    l2 = pd.read_csv(scan_dir / "L2_gbdt_top200.csv", dtype={"code": str})
    codes = l2["code"].astype(str).str.zfill(6).tolist()
    if do_harvest:
        harvest_l3_evidence(date, codes, root=base)
        harvest_l3_news(date, codes, root=base)
    md = l3_table_md(date, root=base, delta=delta, dist_flag=True, reg_flag=True,
                     cat_flag=True, sector_terrain=True)
    (scan_dir / "_l3_table.md").write_text(md, encoding="utf-8")
    return {"codes": len(codes), "table_bytes": len(md)}


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="l3_select")
    ap.add_argument("cmd", choices=["finalists", "prepare"])
    ap.add_argument("date")
    ap.add_argument("--budget", type=int, default=30)
    ap.add_argument("--root", default=None)
    a = ap.parse_args(argv)
    if a.cmd == "finalists":
        res = write_finalists(a.date, budget=a.budget, root=a.root)
        print(f"[l3_select finalists] judged {res['judged_n']} → finalists {res['finalists_n']}")
    else:
        res = prepare_l3_table(a.date, root=a.root)
        print(f"[l3_select prepare] codes {res['codes']} → _l3_table.md {res['table_bytes']}B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 运行,确认 finalists 测试通过**

Run: `uv run --no-sync python -m pytest tests/scan/test_finalists_writer.py -q`
Expected: PASS

- [ ] **Step 5: 写 prepare 表构建测试(不触网:`do_harvest=False` + 预置证据)**

`tests/scan/test_l3_prepare.py`(复用 `test_l3_news_table.py` 的 L2/news 预置法):
```python
import json

import pandas as pd

from autoresearch.scan.agents.l3_select import prepare_l3_table


def test_prepare_writes_l3_table(tmp_path):
    base = tmp_path / "context" / "scan"
    d = base / "2026-06-20"
    (d / "L3_news").mkdir(parents=True)
    pd.DataFrame([{"code": f"{i:06d}", "name": f"s{i}", "industry": "电子", "composite": 90 - i,
                   "gbdt_score": 0.5, "pct_60d": 10.0, "main_net_ratio": 0.01,
                   "winner_rate": 30.0, "np_yoy": 50.0, "n_channels": 2,
                   "recall_channels": "composite"} for i in range(3)]).to_csv(
        d / "L2_gbdt_top200.csv", index=False)
    for c in ("000000", "000001", "000002"):
        (d / "L3_news" / f"{c}.json").write_text("[]", encoding="utf-8")

    res = prepare_l3_table("2026-06-20", root=base, do_harvest=False)

    assert res["codes"] == 3 and res["table_bytes"] > 0
    assert (d / "_l3_table.md").exists()
```

- [ ] **Step 6: 运行两个测试文件 + 全量回归**

Run: `uv run --no-sync python -m pytest tests/scan/test_finalists_writer.py tests/scan/test_l3_prepare.py -q && uv run --no-sync python -m pytest tests/ -q`
Expected: PASS · 全量 720 passed(718 + 2 新文件)

- [ ] **Step 7: 提交**

```bash
git add autoresearch/scan/agents/l3_select.py tests/scan/test_finalists_writer.py tests/scan/test_l3_prepare.py
git commit -m "feat(scan): l3_select CLI(finalists 确定性写盘修前导零坑 + prepare 建 L3 表)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: l4_card harvest-slim —— 失败响亮的批量 slim(修 bug#3 = GATE 3)

**Files:**
- Modify: `autoresearch/scan/agents/l4_card.py`(加 `harvest_slim_batch`;`main` 的 `choices` 加 `"harvest-slim"`)
- Test: `tests/scan/test_harvest_slim.py`(建)

**Interfaces:**
- Consumes: `_harvest_list.txt`(`l4_card prompts` 已产,yfinance 归一后缀)。
- Produces: `harvest_slim_batch(date, root=None, min_bytes=10240, retries=1, harvest_fn=None, ctx_root=None) -> {"ok":bool,"n":int,"failures":[{"ticker","bytes","why"}]}`。CLI:`python -m autoresearch.scan.agents.l4_card harvest-slim <date>` → 打印一行 JSON;有失败则 **exit 1**。

- [ ] **Step 1: 写失败测试**

`tests/scan/test_harvest_slim.py`:
```python
from autoresearch.scan.agents.l4_card import harvest_slim_batch


def _setup(tmp_path, tickers):
    d = tmp_path / "2026-07-07"
    d.mkdir(parents=True)
    (d / "_harvest_list.txt").write_text("\n".join(tickers), encoding="utf-8")
    return d


def test_harvest_slim_flags_undersized(tmp_path):
    _setup(tmp_path, ["600584.SS", "000062.SZ"])
    sizes = {"600584.SS": 20_000, "000062.SZ": 2_000}      # 000062 太小 = 失败

    def fake(t, dt):
        p = tmp_path / f"{t}_{dt}_slim.md"
        p.write_text("x" * sizes[t], encoding="utf-8")
        return p

    res = harvest_slim_batch("2026-07-07", root=tmp_path, retries=0, harvest_fn=fake)
    assert res["ok"] is False
    assert [f["ticker"] for f in res["failures"]] == ["000062.SZ"]


def test_harvest_slim_all_ok(tmp_path):
    _setup(tmp_path, ["600584.SS"])

    def fake(t, dt):
        p = tmp_path / f"{t}_{dt}_slim.md"
        p.write_text("x" * 20_000, encoding="utf-8")
        return p

    res = harvest_slim_batch("2026-07-07", root=tmp_path, retries=0, harvest_fn=fake)
    assert res["ok"] is True and res["failures"] == []


def test_harvest_slim_catches_sh_suffix(tmp_path):
    _setup(tmp_path, ["600584.SH"])                        # 归一漏网 → 直接判失败
    res = harvest_slim_batch("2026-07-07", root=tmp_path, retries=0,
                             harvest_fn=lambda t, dt: None)
    assert res["ok"] is False and ".SH" in res["failures"][0]["why"]
```

- [ ] **Step 2: 运行,确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_harvest_slim.py -q`
Expected: FAIL —— `ImportError: cannot import name 'harvest_slim_batch'`

- [ ] **Step 3: 实现 `harvest_slim_batch` + 接 CLI**

在 `autoresearch/scan/agents/l4_card.py` 加(顶部若无则补 `import sys`、`from pathlib import Path`):
```python
def _default_harvest_slim(ticker: str, date: str, ctx_root: Path) -> Path:
    import subprocess

    subprocess.run(
        [sys.executable, "-m", "autoresearch.analyze.harvest", ticker, date, "stock", "--slim"],
        check=False)
    return ctx_root / f"{ticker}_{date}_slim.md"


def harvest_slim_batch(date: str, root: Path | None = None, min_bytes: int = 10_240,
                       retries: int = 1, harvest_fn=None, ctx_root: Path | None = None) -> dict:
    """按 _harvest_list.txt 批量 harvest slim,**失败响亮**(修 603799 静默失败坑 = GATE 3)。

    07-06 教训:slim >10KB 才可信。offender 重试 `retries` 次仍小/异常/含 .SH → 记失败。
    harvest_fn(ticker, date)->Path 可注入(测试用),默认 shell 到 analyze.harvest --slim。
    """
    base = Path(root) if root else Path("context/scan")
    scan_dir = base / date
    ctx = ctx_root or Path("context")
    tickers = [t for t in (scan_dir / "_harvest_list.txt").read_text(encoding="utf-8").split() if t]
    hv = harvest_fn or (lambda t, dt: _default_harvest_slim(t, dt, ctx))
    failures = []
    for t in tickers:
        if ".SH" in t:                                    # 归一漏网(GATE 3 防线)
            failures.append({"ticker": t, "bytes": -1, "why": ".SH 未归一"})
            continue
        size = 0
        for _ in range(retries + 1):
            try:
                p = hv(t, date)
                size = p.stat().st_size if p and Path(p).exists() else 0
            except Exception:                             # noqa: BLE001
                size = 0
            if size >= min_bytes:
                break
        if size < min_bytes:
            failures.append({"ticker": t, "bytes": int(size), "why": f"<{min_bytes}B"})
    return {"ok": not failures, "n": len(tickers), "failures": failures}
```

在 `main`(l4_card.py:508)把 `choices=["prompts", "pledge"]` 改为 `choices=["prompts", "pledge", "harvest-slim"]`,并在分派处加(命令用 `-` 时 argparse 收 `harvest-slim`):
```python
    if args.cmd == "harvest-slim":
        import json
        res = harvest_slim_batch(args.date)
        print(json.dumps({"ok": res["ok"],
                          "reason": ("ok" if res["ok"]
                                     else f"{len(res['failures'])}/{res['n']} slim 失败:"
                                          + ", ".join(f"{f['ticker']}({f['bytes']}B)"
                                                      for f in res["failures"])),
                          "failures": res["failures"]}, ensure_ascii=False))
        return 0 if res["ok"] else 1
```

- [ ] **Step 4: 运行,确认通过 + 全量回归**

Run: `uv run --no-sync python -m pytest tests/scan/test_harvest_slim.py -q && uv run --no-sync python -m pytest tests/ -q`
Expected: PASS · 全量 723 passed

- [ ] **Step 5: 提交**

```bash
git add autoresearch/scan/agents/l4_card.py tests/scan/test_harvest_slim.py
git commit -m "feat(scan): l4_card harvest-slim 批量 slim 失败响亮(修 603799 静默失败 = GATE3)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: gates.py —— 确定性校验门 GATE 1/2/4 + 红队门

**Files:**
- Create: `autoresearch/scan/gates.py`
- Test: `tests/scan/test_gates.py`(建)

**Interfaces:**
- Consumes: `L2_gbdt_top200.csv`、`finalists.csv`、`gate_fires.csv`(assemble 落,`self_review.dump_gate_fires`,字段 `date,code,check,severity,detail`);`menu.sentinel_advice(scan_dir)->(str,str)`、`menu.l4_budget(scan_dir)->(int,str)`、`menu.should_run_opportunity_redteam(scan_dir)->(bool,str)`。
- Produces: `gate1(scan_dir)->{ok,sentinel_level,l4_budget,...}`、`gate2(scan_dir,budget)->{ok,finalists:[str],n,...}`、`gate4(scan_dir)->{ok,reason,...}`、`redteam_check(scan_dir)->{run,reason}`。CLI:`python -m autoresearch.scan.gates {gate1|gate2|gate4|redteam} <date> [--budget N] [--root PATH]` → 打印一行 JSON,`ok`/`run` 为假则 exit 1。

- [ ] **Step 1: 写失败测试**

`tests/scan/test_gates.py`:
```python
import csv

import pandas as pd

from autoresearch.scan.gates import gate1, gate2, gate4


def test_gate1_flags_bad_codes(tmp_path):
    # 前导零已丢(62 而非 000062)→ 必须拦
    pd.DataFrame({"code": [62, 63], "pct_60d": [1.0, 2.0], "main_net_ratio": [0.0, 0.0],
                  "cmf_20": [0.0, 0.0]}).to_csv(tmp_path / "L2_gbdt_top200.csv", index=False)
    assert gate1(tmp_path)["ok"] is False


def test_gate1_missing_l2(tmp_path):
    assert gate1(tmp_path)["ok"] is False


def test_gate2_ok_returns_finalists(tmp_path):
    pd.DataFrame({"code": ["000062", "600584"], "ticker": ["000062", "600584.SS"]}).to_csv(
        tmp_path / "finalists.csv", index=False)
    r = gate2(tmp_path, budget=30)
    assert r["ok"] is True and r["finalists"] == ["000062", "600584"] and r["n"] == 2


def test_gate2_over_budget(tmp_path):
    pd.DataFrame({"code": [f"{i:06d}" for i in range(5)]}).to_csv(
        tmp_path / "finalists.csv", index=False)
    assert gate2(tmp_path, budget=3)["ok"] is False


def _gate_fires(tmp_path, rows):
    with (tmp_path / "gate_fires.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "code", "check", "severity", "detail"])
        w.writeheader()
        for r in rows:
            w.writerow(r)


def test_gate4_passes_when_no_fail(tmp_path):
    _gate_fires(tmp_path, [])                                    # 空表 = 自检通过
    assert gate4(tmp_path)["ok"] is True


def test_gate4_fails_on_fail_row(tmp_path):
    _gate_fires(tmp_path, [{"date": "2026-07-07", "code": "", "check": "覆盖率不足",
                            "severity": "fail", "detail": "卡片 5/20"}])
    assert gate4(tmp_path)["ok"] is False


def test_gate4_missing_file(tmp_path):
    assert gate4(tmp_path)["ok"] is False                        # assemble 没跑
```

- [ ] **Step 2: 运行,确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_gates.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'autoresearch.scan.gates'`

- [ ] **Step 3: 实现 gates.py**

`autoresearch/scan/gates.py`:
```python
"""scan-market workflow 校验门(确定性,零 LLM)。workflow 经 Bash-agent 调,读 JSON 分支。

GATE1 = prelude 后数据体检(L2 非空 + 代码 6 位)+ 返回 sentinel/budget;
GATE2 = finalists 定稿后(代码 6 位 + count≤budget)+ 返回名单;
GATE4 = assemble 后 self_review 硬门(gate_fires.csv 无 severity=fail)。
GATE3(slim>10KB / 无 .SH)由 l4_card harvest-slim 自身承担。redteam = 0买日抽检门。
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pandas as pd

_CODE_RE = re.compile(r"^\d{6}$")


def _codes_ok(codes) -> bool:
    return len(codes) > 0 and all(bool(_CODE_RE.match(str(c))) for c in codes)


def gate1(scan_dir: Path) -> dict:
    scan_dir = Path(scan_dir)
    l2 = scan_dir / "L2_gbdt_top200.csv"
    if not l2.exists():
        return {"ok": False, "gate": "gate1", "reason": "L2_gbdt_top200.csv 缺失(universe 未跑?)"}
    df = pd.read_csv(l2, dtype={"code": str})
    if df.empty:
        return {"ok": False, "gate": "gate1", "reason": "L2 为空"}
    if not _codes_ok(df["code"].astype(str)):
        return {"ok": False, "gate": "gate1", "reason": "L2 代码非 6 位(前导零坑)"}
    from autoresearch.scan.menu import l4_budget, sentinel_advice

    level, _ = sentinel_advice(scan_dir)
    budget, _ = l4_budget(scan_dir)
    return {"ok": True, "gate": "gate1", "reason": "ok", "sentinel_level": level,
            "l4_budget": int(budget), "l2_n": int(len(df))}


def gate2(scan_dir: Path, budget: int = 30) -> dict:
    scan_dir = Path(scan_dir)
    fp = scan_dir / "finalists.csv"
    if not fp.exists():
        return {"ok": False, "gate": "gate2", "reason": "finalists.csv 缺失"}
    df = pd.read_csv(fp, dtype={"code": str, "ticker": str})
    if df.empty:
        return {"ok": False, "gate": "gate2", "reason": "finalists 空"}
    codes = df["code"].astype(str).str.zfill(6)
    if not _codes_ok(codes):
        return {"ok": False, "gate": "gate2", "reason": "finalist 代码非 6 位(前导零坑)"}
    if len(df) > budget:
        return {"ok": False, "gate": "gate2", "reason": f"finalists {len(df)} > budget {budget}"}
    return {"ok": True, "gate": "gate2", "reason": "ok", "finalists": codes.tolist(),
            "n": int(len(df))}


def gate4(scan_dir: Path) -> dict:
    scan_dir = Path(scan_dir)
    gf = scan_dir / "gate_fires.csv"
    if not gf.exists():
        return {"ok": False, "gate": "gate4", "reason": "gate_fires.csv 缺失(assemble 未跑?)"}
    with gf.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    fails = [r for r in rows if r.get("severity") == "fail"]
    if fails:
        detail = "; ".join(f"{r['check']}:{r['detail']}" for r in fails)
        return {"ok": False, "gate": "gate4", "reason": f"self_review fail×{len(fails)} — {detail}"}
    return {"ok": True, "gate": "gate4", "reason": "self_review 通过", "n_checks": len(rows)}


def redteam_check(scan_dir: Path) -> dict:
    from autoresearch.scan.menu import should_run_opportunity_redteam

    run, reason = should_run_opportunity_redteam(Path(scan_dir))
    return {"run": bool(run), "reason": reason}


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="gates")
    ap.add_argument("gate", choices=["gate1", "gate2", "gate4", "redteam"])
    ap.add_argument("date")
    ap.add_argument("--budget", type=int, default=30)
    ap.add_argument("--root", default=None)
    a = ap.parse_args(argv)
    base = Path(a.root) if a.root else Path("context/scan")
    scan_dir = base / a.date
    res = {"gate1": lambda: gate1(scan_dir),
           "gate2": lambda: gate2(scan_dir, budget=a.budget),
           "gate4": lambda: gate4(scan_dir),
           "redteam": lambda: redteam_check(scan_dir)}[a.gate]()
    print(json.dumps(res, ensure_ascii=False))
    ok = res.get("ok", res.get("run"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 运行,确认通过 + 全量回归**

Run: `uv run --no-sync python -m pytest tests/scan/test_gates.py -q && uv run --no-sync python -m pytest tests/ -q`
Expected: PASS · 全量 730 passed

- [ ] **Step 5: 提交**

```bash
git add autoresearch/scan/gates.py tests/scan/test_gates.py
git commit -m "feat(scan): gates.py 确定性校验门(GATE1/2/4 + 红队抽检门)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: workflow 脚本 `.claude/workflows/scan-market.js`

**Files:**
- Create: `.claude/workflows/scan-market.js`

**Interfaces:**
- Consumes: Task 1–3 的 CLI(`l3_select prepare/finalists`、`l4_card harvest-slim`、`gates gate1/gate2/gate4/redteam`)、现有确定性 CLI(`frame`、`prelude`、`sector.reuse/pack`、`l4_reuse`、`l4_card prompts/pledge`、`calendar`、`assemble`)、leaf agents(`l3-rank`/`l4-card`/`buy-skeptic`/`sector-brief`)。`args.date`(必填)。
- Produces: 后台跑完 `reports/scan/<run>/`;返回 `{date, mode, finalists, cards, buys, isZeroBuy, published}`。

> **说明:** 本任务是新建单个 JS 文件,无 pytest 单测(JS 无框架);正确性由 Task 3 的门单测 + Task 5 首次真跑保证。步骤 = 写文件 → 结构自检 → 提交。

- [ ] **Step 1: 写 `.claude/workflows/scan-market.js`**

```javascript
export const meta = {
  name: 'scan-market',
  description: '全 A股六段漏斗:一个确定性 workflow 编排全流程 + 四校验门(prelude→市场/行业→L3→L4→assemble)',
  phases: [
    { title: 'Prelude', detail: 'frame → [universe/L0-L2 ∥ market_view] → GATE1' },
    { title: 'L3', detail: '[sector-briefs ∥ 证据harvest] → L3-rank → finalists → GATE2' },
    { title: 'L4', detail: 'slim-harvest(GATE3) → 决策卡并发' },
    { title: 'Assemble', detail: '0买红队 → assemble → GATE4' },
  ],
}

// ── 输入 & 常量 ──────────────────────────────────────────────────
const date = args && args.date
if (!date) throw new Error('args.date 必填,如 {date:"2026-07-07"}')
const R = 'uv run --no-sync python -m'
const SD = `context/scan/${date}`

// 确定性命令 → general-purpose Bash-agent(只跑命令、回报退出码,不判断)
function bash(cmd, label) {
  return agent(
    `在仓库根目录精确执行下面这条命令,然后只回报:退出码 + stdout 末 15 行。不要做别的、不要判断、不要解释。\n\n\`\`\`\n${cmd}\n\`\`\``,
    { agentType: 'general-purpose', effort: 'low', label })
}
// 门 CLI → Bash-agent + schema(把 CLI 打印的 JSON 原样带回)
const GATE1 = { type: 'object', required: ['ok', 'sentinel_level', 'l4_budget'],
  properties: { ok: { type: 'boolean' }, reason: { type: 'string' },
    sentinel_level: { type: 'string' }, l4_budget: { type: 'integer' } } }
const GATE2 = { type: 'object', required: ['ok', 'finalists'],
  properties: { ok: { type: 'boolean' }, reason: { type: 'string' },
    finalists: { type: 'array', items: { type: 'string' } }, n: { type: 'integer' } } }
const OK = { type: 'object', required: ['ok'],
  properties: { ok: { type: 'boolean' }, reason: { type: 'string' } } }
const RT = { type: 'object', required: ['run'],
  properties: { run: { type: 'boolean' }, reason: { type: 'string' } } }
function gate(label, cmd, schema) {
  return agent(
    `执行:\`${cmd}\`\n它会向 stdout 打印一行 JSON。把那行 JSON 原样作为你的结构化返回(字段不改、不增删)。`,
    { agentType: 'general-purpose', effort: 'low', label, schema })
}

// ── Phase Prelude ───────────────────────────────────────────────
phase('Prelude')
// frame 先行:pack 存盘 + 取数入湖(prelude/universe 随后湖命中不重拉)
await bash(`mkdir -p ${SD} && ${R} autoresearch.scan.frame ${date} --json > ${SD}/market_pack.json`, 'frame')
// universe(确定性)∥ market_view(macro-lite 判断)—— barrier
await parallel([
  () => bash(`${R} autoresearch.scan.prelude ${date}`, 'prelude/universe'),
  () => agent(
    `你是首席策略师。按 macro-research lite 档(模板见 .claude/skills/macro-research/macro-playbook.md 末节「lite 档:市场研判」)读 ${SD}/market_pack.json,写 ${SD}/market_view.md(定调/结构/红黑榜/操作基调)。数字只出自 pack,不编数;个股不评级。`,
    { agentType: 'claude', model: 'opus', effort: 'medium', label: 'market_view', phase: 'Prelude' }),
])
const g1 = await gate('GATE1', `${R} autoresearch.scan.gates gate1 ${date}`, GATE1)
if (!g1 || !g1.ok) throw new Error(`GATE1 失败:${g1 ? g1.reason : 'agent 无返回'}`)
log(`GATE1 ✓ sentinel=${g1.sentinel_level} · L4预算=${g1.l4_budget}`)

// ── 哨兵档:材料枯竭 → 跳过 sector/L3/L4 ─────────────────────────
if (g1.sentinel_level === 'sentinel') {
  log('哨兵档 → 跳过 L3/L4,只出观察单/红队/assemble')
  await bash(`${R} autoresearch.scan.assemble ${date}`, 'assemble')
  const g4s = await gate('GATE4', `${R} autoresearch.scan.gates gate4 ${date}`, OK)
  if (!g4s || !g4s.ok) throw new Error(`GATE4(哨兵)失败:${g4s ? g4s.reason : 'no return'}`)
  return { date, mode: 'sentinel', finalists: 0, cards: 0, buys: [], isZeroBuy: true, published: true }
}

// ── Phase L3 ────────────────────────────────────────────────────
phase('L3')
// 中观行业 pack(确定性)先行,再 [sector-briefs ∥ L3 表准备] barrier
await bash(`${R} autoresearch.sector.reuse ${date} --apply; ${R} autoresearch.sector.pack ${date}`, 'sector-pack')
const sectors = await agent(
  `列出目录 context/sector/${date}/ 下所有 *.json 文件的文件名去扩展名(= 行业名)。只返回 JSON 字符串数组;目录不存在或空则返回 []。`,
  { agentType: 'general-purpose', effort: 'low', label: 'sector-list',
    schema: { type: 'array', items: { type: 'string' } } }) || []
await parallel([
  () => bash(`${R} autoresearch.scan.agents.l3_select prepare ${date}`, 'l3-prepare'),
  ...sectors.map((sec) => () => agent(
    `你是行业分析师。读 context/sector/${date}/${sec}.json 写 ${SD}/sector_briefs/${sec}.md,两段机器契约(## 地形段 喂 L3/L4 · ## 研判段 仅 L5,含 **行业方向** 行)。零新取数。`,
    { agentType: 'sector-brief', effort: 'low', label: `brief:${sec}`, phase: 'L3' })),
])
// L3 holistic 精排(唯一 max-effort 判断核心)
await agent(
  `L3 精排 · 日期 ${date} · 目标约 ${g1.l4_budget} 只。文件在 ${SD}/:_l3_table.md(~200 表)、market_view.md(§1-3 地形)、sector_briefs/(地形段)。按你的人设(5 维 rubric + 硬约束 A/B/C/D)比较式精排,写 ${SD}/_l3_judged.json。`,
  { agentType: 'l3-rank', effort: 'max', label: 'L3-rank', phase: 'L3' })
// 确定性写 finalists(修前导零)+ GATE2
await bash(`${R} autoresearch.scan.agents.l3_select finalists ${date} --budget ${g1.l4_budget}`, 'finalists')
const g2 = await gate('GATE2', `${R} autoresearch.scan.gates gate2 ${date} --budget ${g1.l4_budget}`, GATE2)
if (!g2 || !g2.ok) throw new Error(`GATE2 失败:${g2 ? g2.reason : 'no return'}`)
log(`GATE2 ✓ finalists=${g2.n}`)

// ── Phase L4 ────────────────────────────────────────────────────
phase('L4')
// 派发包(确定性):TTL复用+carryover → prompts(.SH 归一)→ pledge → calendar
await bash(
  `${R} autoresearch.scan.l4_reuse ${date} --apply --carryover; ` +
  `${R} autoresearch.scan.agents.l4_card prompts ${date}; ` +
  `${R} autoresearch.scan.agents.l4_card pledge ${date} || true; ` +
  `${R} autoresearch.scan.calendar ${date} || true`, 'l4-prep')
// GATE3:批量 slim 失败响亮(harvest-slim 打印 JSON + 非零退出)
const g3 = await gate('GATE3', `${R} autoresearch.scan.agents.l4_card harvest-slim ${date}`, OK)
if (!g3 || !g3.ok) throw new Error(`GATE3 失败(slim<10KB 或 .SH):${g3 ? g3.reason : 'no return'}`)
log('GATE3 ✓ 全 slim >10KB')
// 决策卡:全部 finalist 一次并发(barrier —— 红队需全部评级才知是否 0 买)
const CARD = { type: 'object', required: ['code', 'rating'],
  properties: { code: { type: 'string' }, rating: { type: 'string' }, conviction: { type: 'number' } } }
const cards = (await parallel(g2.finalists.map((code) => () => agent(
  `执行 ${SD}/_l4_prompt_${code}.md:先读整个任务包,再按其指令做渐进深度 DD + 早停,写决策卡到 ${SD}/details/${code}.md。最后返回该卡最终五档评级(code / rating / conviction)。`,
  { agentType: 'l4-card', effort: 'medium', label: `card:${code}`, phase: 'L4', schema: CARD }))))
  .filter(Boolean)
const isOW = (r) => /(overweight|\bbuy\b|增持|买入)/i.test(r || '')
const buys = cards.filter((c) => isOW(c.rating)).map((c) => c.code)
const isZeroBuy = buys.length === 0
log(`L4 ✓ ${cards.length} 卡 · ≥OW ${buys.length} · ${isZeroBuy ? '0买日' : '有买单'}`)

// ── Phase Assemble ──────────────────────────────────────────────
phase('Assemble')
// 0 买日:机会成本红队(抽检门 + conviction 最高的 2 个 Hold),产出只进观察单
if (isZeroBuy) {
  const rt = await gate('redteam-gate', `${R} autoresearch.scan.gates redteam ${date}`, RT)
  const holds = cards
    .filter((c) => !isOW(c.rating) && typeof c.conviction === 'number')
    .sort((a, b) => b.conviction - a.conviction).slice(0, 2)
  if (rt && rt.run && holds.length) {
    log(`机会成本红队 ×${holds.length}(${rt.reason})`)
    await parallel(holds.map((h) => () => agent(
      `机会成本红队(模式B=多方)。攻"压 ${h.code} 评级的那道 binding gate"是否太紧:读 ${SD}/details/${h.code}.md + slim,给翻转触发(观察单词表 close_above/ma_bull/money_pos/by_date),写 ${SD}/_v_${h.code}.md。不改评级、不喊单。`,
      { agentType: 'buy-skeptic', effort: 'high', label: `redteam:${h.code}`, phase: 'Assemble' })))
  } else {
    log(`机会成本红队跳过(${rt ? rt.reason : '无候选'})`)
  }
}
// L5 整合(内含 self_review 硬门 + dump gate_fires)+ GATE4
await bash(`${R} autoresearch.scan.assemble ${date}`, 'assemble')
const g4 = await gate('GATE4', `${R} autoresearch.scan.gates gate4 ${date}`, OK)
if (!g4 || !g4.ok) throw new Error(`GATE4 失败(self_review 未通过):${g4 ? g4.reason : 'no return'}`)
log('GATE4 ✓ self_review 通过')

return { date, mode: 'full', finalists: g2.n, cards: cards.length, buys, isZeroBuy, published: true }
```

> **v1 已知取舍(honest,`log()` 会显式说明):** ① 观察单触发直通车(`watchlist append_express`)、L4 前"同链行业补漏"未纳入 v1 —— 后续增强;② `_stage_timing.json` 无法在脚本内产出(`Date.now()` 不可用)→ summary 墙钟列显示 `—`(presence-gated),Task 5 由主循环从 `/workflows` 计时回填后重跑 assemble(可选)。

- [ ] **Step 2: 结构自检**

因脚本只在 Workflow 运行时解析,这里做静态检查:
- meta 是纯字面量,`phases` 四个 title(Prelude/L3/L4/Assemble)与 `phase()` 调用逐字一致 ✓
- 无 `Date.now()`/`Math.random()`/argless `new Date()` ✓
- 所有 `agent()` 命令都是 `uv run --no-sync python -m …` 或现有 agentType ✓
- 每个 gate 调用后都 `if (!g || !g.ok) throw` 硬拦 ✓

肉眼过一遍上面四条;若用 node 在手边可 `node --check`(仅语法,ESM `export`/顶层 await 需 `.mjs`,非必需)。

- [ ] **Step 3: 提交**

```bash
git add .claude/workflows/scan-market.js
git commit -m "feat(scan): scan-market workflow 脚本(全流程编排 + 四校验门 + 哨兵/0买分支)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: 首次真跑 + 对基线核账 + SKILL.md 指针(P4,操作性)

**Files:**
- Modify: `.claude/skills/scan-market/SKILL.md`(加 workflow 指针)

> **说明:** 本任务需 Workflow 工具真跑一次(~45–60 min 后台)+ 人工核账,不能由普通 subagent 代跑。在**用户下次要扫描时**执行。

- [ ] **Step 1: 预检**

```bash
git status -s                 # 工作树干净
uv run --no-sync python -m pytest tests/ -q   # 730 passed
uv run --no-sync python -m autoresearch.learning.retro pending   # 无待诊断日(有则先 scan-retro 补)
```

- [ ] **Step 2: 后台真跑 workflow**

用 Workflow 工具:`Workflow({ name: 'scan-market', args: { date: '<下个交易日 YYYY-MM-DD>' } })`。用 `/workflows` 看实时进度。跑完收到 `<task-notification>`。

- [ ] **Step 3: 核账(对 07-06 手工基线)**

跑完读返回值 + `reports/scan/<run>/summary.md`,逐条核:
- 四门是否都 ✓ 放行(任何 GATE throw = 中止,读报错定位)。
- 前导零:`reports/scan/<run>/details/` 里 0 开头代码的卡片是否都在(**07-06 是 5/20 缺失**)。
- slim:无 NO_DATA 盲卡(GATE3 应已拦)。
- finalists 数 ≈ `l4_budget`;buy-list / 0买判定与手工路径同构。
- 墙钟(若回填了 `_stage_timing.json`)对 07-06 基线(总 65m:L3 19m / L4 14m / slim 10m / 红队 9m)—— L3=max-effort 可能变慢,表瘦身 37% 抵消;L4=medium-effort 若生效应更快。

- [ ] **Step 4:(可选)回填墙钟并重跑 assemble**

从 `/workflows` 或 task 通知读各阶段时长 → 写 `context/scan/<date>/_stage_timing.json`(键 `L0L1L2/策略师/行业brief/L3精排/L4研究/L4slim/红队/总计`,值秒)→ `uv run --no-sync python -m autoresearch.scan.assemble <date>` 重出报告(墙钟列填充)。

- [ ] **Step 5: SKILL.md 加 workflow 指针**

在 `.claude/skills/scan-market/SKILL.md` 的「## 流程(6 段)」标题下、步骤 0 之前插入:
```markdown
> **默认路径(2026-07-07 起):** 整条漏斗已 workflow 化 —— `Workflow({name:'scan-market', args:{date:'YYYY-MM-DD'}})` 后台一次跑完(frame→prelude→[市场∥行业]→L3→L4→assemble),四道校验门(GATE1 数据体检 / GATE2 finalist / GATE3 slim / GATE4 self_review)自动拦坑,脚本 `.claude/workflows/scan-market.js`。下方 0–5 手工分步保留作**规格真值源 + 兜底**(workflow 有坏天可回退手工跑);语义/铁律以下文为准。
```

- [ ] **Step 6: 提交文档 + 收尾**

```bash
git add .claude/skills/scan-market/SKILL.md
git commit -m "docs(scan): SKILL.md 加 workflow 默认路径指针(手工分步转规格兜底)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```
真跑验收通过后,汇报用户:workflow 落地 + 首跑核账结果(四门 / 前导零 / 墙钟对比 / buy-list)。

---

## Self-Review(plan vs spec)

**1. Spec 覆盖:**
- §2 目标"一个 workflow 后台跑完" → Task 4 ✓;"契约级 effort" → Task 4 各 agent `{effort}` ✓;"四校验门" → GATE1/2/4 Task 3、GATE3 Task 2 ✓;"三数据坑源头修" → bug#1 Task 1、bug#3 Task 2、bug#2 已修(仅 GATE3 防线,Task 2)✓;"可复现/resume" → Workflow `resumeFromRunId`(Task 5 备注)✓。
- §4.2 DAG 全阶段 → Task 4 逐段落地(frame/[prelude∥market_view]/GATE1/哨兵分支/[sector∥prepare]/L3-rank/finalists/GATE2/l4-prep/GATE3/cards/0买红队/assemble/GATE4)✓。
- §7 SKILL 共存 → Task 5 Step 5 ✓。
- §6 分期 P1/P2/P3/P4 → Task 1-3(P1)/ Task 4(P2)/ Task 3 门单测(P3 的正确性骨架)/ Task 5(P4)✓。

**2. Placeholder 扫描:** 无 TBD/TODO;每个代码步含完整代码;v1 取舍(express/行业补漏/stage_timing)显式列出并 `log()`,非隐藏缺口。

**3. 类型一致:** `write_finalists`/`prepare_l3_table`/`harvest_slim_batch`/`gate1|2|4`/`redteam_check` 的签名在 Task 内定义、Task 4 JS 调用处逐一对齐(CLI 名 `finalists`/`prepare`/`harvest-slim`/`gate1..`/`redteam`;JS schema 字段 `ok`/`sentinel_level`/`l4_budget`/`finalists`/`run` 与 gates.py 返回键一致)✓。

**偏离 spec 记录(有意改进):** spec §4.3 把门写成"reader-agent";plan 精化为**确定性 gate CLI(gates.py)+ Bash-agent 带回 JSON** —— 更便宜、可单测、确定,优于 agent 眼判。已在 spec §4.3 语义范围内(门仍返回 JSON 判据),不需回改 spec。
