# 研究深度增强波 · P1(游资席位进卡 + rubric)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把已建但未接进 scan L4 决策卡的龙虎榜游资席位识别接上(成本可控),并让 rubric 明确要求 L4 读它——中性给数据,判断交回 L4 卡自身。

**Architecture:** 复用 `l4_card.fetch_pledge` 的"批量取数 + 跨 scan 日复用"模式做 `fetch_seats`(**关键成本修**:`top_inst` 是按日 bulk 端点,15 日表**只拉一次**再对全 finalists 过滤聚合,而非 `lhb_seats` 现有的逐票 ×15 ≈ 420 调用);席位落 `seats.csv`,`_seat_mark` presence-gated 注入 `compose_funnel_brief`(与 `_pledge_mark` 同构)。全部确定性、零 LLM、presence-gated、parity 不破。**不新增任何判断/陷阱旗层**——是否"陷阱"由 L4 卡读数据自判(它有全套 slim + 早停 + rubric)。

**Tech Stack:** Python 3(pandas / tushare `pro.top_inst`)· pytest · 现有 `autoresearch/scan/agents/l4_card.py`。

## Global Constraints

- **零 LLM / 确定性**:P1 全部是 pandas + tushare 取数 + 纯函数,不派 agent、不编数。
- **presence-gated + parity**:`seats.csv` 缺 → `_seat_mark` 返回 `""`(不加行、不抛);无 seats.csv 时现有卡输出字节不变。
- **成本可见 + 限频复用**:`top_inst` 15 日表按日 bulk 一次;近 7 日其他 scan 日已算的 code 直接复用(mirror `fetch_pledge` reuse_days=7)。
- **只给数据不代判**:席位是**技术·资金维的校准输入**,rubric 不得让席位单独定方向;机构上榜净买按 Phase A 实测标**反指**。
- **代码风格**:跟随 `l4_card.py` 现有约定(`# noqa: BLE001` 单票降级隔离、`dtype={"code": str}` + `.str.zfill(6)`、`_g`/`_l1_float` helper)。
- 测试延续现有 **739 绿**。

---

### Task 1: `fetch_seats` — 龙虎榜席位批量聚合 + 跨日复用 → `seats.csv`

**Files:**
- Modify: `autoresearch/scan/agents/l4_card.py`(在 `fetch_pledge` 后新增 `_tushare_seats_by_date` + `fetch_seats`;CLI `main()` 加 `seats` 子命令)
- Test: `tests/scan/test_l4_seats.py`(新建)

**Interfaces:**
- Produces:
  - `_tushare_seats_by_date(dates: list[str]) -> dict[str, pd.DataFrame]` —— 按 `YYYYMMDD` 日 bulk `pro.top_inst`,失败日跳过。
  - `fetch_seats(scan_dir: Path|str, codes=None, bulk_fn=None, reuse_days: int = 7, window_days: int = 20) -> pd.DataFrame` —— 列 `code,inst_net_wan,retail_net_wan,n_appear`;写 `scan_dir/seats.csv`;近 `reuse_days` 内 sibling scan 日 `seats.csv` 有的 code 直接复用。`bulk_fn` 注入供测(签名 `(dates:list[str])->dict[str,DataFrame]`)。
- Consumes: `finalists.csv`(`code` 列)、`autoresearch.data.tushare_source`(`_pro`/`_ts_call`/`_code6`/`_trade_days`/`resolve_momentum_dates`)。

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_l4_seats.py
import pandas as pd
from autoresearch.scan.agents.l4_card import fetch_seats


def _bulk_stub(dates):
    # 两天龙虎榜:600000 机构专用净买 +500万/-200万;300001 游资营业部 +80万
    frame = lambda rows: pd.DataFrame(rows)
    return {
        dates[0]: frame([{"ts_code": "600000.SH", "exalter": "机构专用", "net_buy": 5_000_000},
                         {"ts_code": "300001.SZ", "exalter": "某某营业部", "net_buy": 800_000}]),
        dates[-1]: frame([{"ts_code": "600000.SH", "exalter": "机构专用", "net_buy": -2_000_000}]),
    }


def test_fetch_seats_aggregates_inst_vs_retail(tmp_path):
    d = tmp_path / "2026-07-07"
    d.mkdir()
    pd.DataFrame({"code": ["600000", "300001"]}).to_csv(d / "finalists.csv", index=False)
    out = fetch_seats(d, bulk_fn=lambda dates: _bulk_stub(["20260701", "20260707"]))
    row = out.set_index("code").loc["600000"]
    assert row["inst_net_wan"] == 300      # (5_000_000 - 2_000_000)/1e4
    assert row["n_appear"] == 2
    assert (d / "seats.csv").exists()
    r2 = out.set_index("code").loc["300001"]
    assert r2["retail_net_wan"] == 80 and r2["inst_net_wan"] == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_l4_seats.py -q`
Expected: FAIL — `ImportError: cannot import name 'fetch_seats'`

- [ ] **Step 3: 实现 `_tushare_seats_by_date` + `fetch_seats`**（贴在 `l4_card.py` 的 `fetch_pledge` 之后）

```python
def _tushare_seats_by_date(dates: list[str]) -> dict[str, pd.DataFrame]:
    """按 trade_date bulk 龙虎榜机构明细(一天一调,非逐票)。date=YYYYMMDD。"""
    from autoresearch.data.tushare_source import _pro, _ts_call
    pro = _pro()
    out: dict[str, pd.DataFrame] = {}
    for d in dates:
        try:
            df = _ts_call(lambda d=d: pro.top_inst(trade_date=d))
        except Exception:  # noqa: BLE001 — 单日降级隔离
            df = None
        if df is not None and len(df):
            out[d] = df
    return out


def fetch_seats(scan_dir, codes=None, bulk_fn=None, reuse_days: int = 7,
                window_days: int = 20) -> pd.DataFrame:
    """finalists 龙虎榜机构 vs 游资席位聚合 → `seats.csv`(code,inst_net_wan,retail_net_wan,n_appear)。

    成本控制:`top_inst` 按日 bulk **一次**再对全 finalists 过滤聚合(非 lhb_seats 逐票×15);
    近 reuse_days 内其他 scan 日已算的 code 直接复用。mirror `fetch_pledge`。零 LLM。
    """
    from datetime import datetime, timedelta

    from autoresearch.data.tushare_source import _code6, _pro, _trade_days, resolve_momentum_dates
    scan_dir = Path(scan_dir)
    cols = ["code", "inst_net_wan", "retail_net_wan", "n_appear"]
    if codes is None:
        fp = scan_dir / "finalists.csv"
        if not fp.exists():
            return pd.DataFrame(columns=cols)
        codes = pd.read_csv(fp, dtype={"code": str})["code"].tolist()
    want = [str(c).split(".")[0].zfill(6) for c in codes]

    def _d(name: str):
        try:
            return datetime.strptime(name, "%Y-%m-%d")
        except ValueError:
            return None

    today = _d(scan_dir.name)
    rows: dict[str, dict] = {}
    # 1) 跨 scan 日复用(mirror fetch_pledge)
    if today is not None and scan_dir.parent.exists():
        for sib in sorted((p for p in scan_dir.parent.iterdir() if p.is_dir()), reverse=True):
            sd = _d(sib.name)
            if sd is None or sib == scan_dir or not 0 <= (today - sd).days <= reuse_days:
                continue
            pp = sib / "seats.csv"
            if not pp.exists():
                continue
            try:
                prev = pd.read_csv(pp, dtype={"code": str})
            except Exception:  # noqa: BLE001
                continue
            prev["code"] = prev["code"].astype(str).str.zfill(6)
            for _, r in prev.iterrows():
                c = r["code"]
                if c in want and c not in rows:
                    rows[c] = {k: r.get(k) for k in cols}
    missing = [c for c in want if c not in rows]
    # 2) 缺的:按日 bulk 一次,聚合全 missing
    if missing:
        try:
            pro = _pro()
            last = resolve_momentum_dates(pro, scan_dir.name)[0]
            start = (datetime.strptime(last, "%Y%m%d") - timedelta(days=window_days)).strftime("%Y%m%d")
            dates = _trade_days(pro, start, last)[-15:]
        except Exception:  # noqa: BLE001
            dates = []
        frames = (bulk_fn or _tushare_seats_by_date)(dates) if dates else {}
        agg = {c: {"inst": 0.0, "retail": 0.0, "n": 0} for c in missing}
        for df in frames.values():
            if df is None or not len(df):
                continue
            c6 = _code6(df["ts_code"])
            for c in missing:
                sub = df[c6 == c]
                if not len(sub):
                    continue
                agg[c]["n"] += 1
                for _, r in sub.iterrows():
                    net = float(r.get("net_buy") or 0)
                    if "机构专用" in str(r.get("exalter", "")):
                        agg[c]["inst"] += net
                    else:
                        agg[c]["retail"] += net
        for c in missing:
            a = agg[c]
            rows[c] = {"code": c, "inst_net_wan": round(a["inst"] / 1e4, 0),
                       "retail_net_wan": round(a["retail"] / 1e4, 0), "n_appear": a["n"]}
    out = pd.DataFrame([rows[c] for c in want if c in rows], columns=cols)
    out.to_csv(scan_dir / "seats.csv", index=False)
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run --no-sync python -m pytest tests/scan/test_l4_seats.py -q`
Expected: PASS

- [ ] **Step 5: 加 `seats` CLI 子命令**（`main()` 的 argparse 分派里,与 `pledge` 并列）

```python
    # 在 pledge 分支旁:
    if cmd == "seats":
        df = fetch_seats(scan_dir)
        n_inst = int((df["inst_net_wan"] > 0).sum()) if len(df) else 0
        print(f"[l4_card seats] {len(df)} 票落 seats.csv(机构净买>0 {n_inst} 票=Phase A 反指候选)")
        return 0
```
并在子命令 help 串里加 `"seats = finalists 龙虎榜席位聚合 → seats.csv(_seat_mark 注简报);"`。

- [ ] **Step 6: 跑全 scan 测试 + ruff**

Run: `uv run --no-sync python -m pytest tests/scan -q && uv run --no-sync ruff check autoresearch/scan/agents/l4_card.py tests/scan/test_l4_seats.py`
Expected: PASS + `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add autoresearch/scan/agents/l4_card.py tests/scan/test_l4_seats.py
git commit -m "feat(scan): fetch_seats 龙虎榜席位批量聚合+跨日复用(游资识别进卡·成本修 15调/轮)"
```

---

### Task 2: `_seat_mark` — 席位识别 presence-gated 注入 L4 简报

**Files:**
- Modify: `autoresearch/scan/agents/l4_card.py`(新增 `_seat_mark`,mirror `_pledge_mark`;`compose_funnel_brief` 的筹码先验行后注入)
- Test: `tests/scan/test_l4_seats.py`(追加)

**Interfaces:**
- Produces: `_seat_mark(base: Path, code6: str) -> str` —— `seats.csv` 在且该 code 有上榜(`n_appear>0`)才返回一行 markdown(含机构净买 + Phase A 反指标注 + 游资净买);否则 `""`。
- Consumes: Task 1 的 `seats.csv`。

- [ ] **Step 1: 写失败测试**

```python
from autoresearch.scan.agents.l4_card import _seat_mark


def test_seat_mark_flags_inst_contra_indicator(tmp_path):
    pd.DataFrame({"code": ["600000"], "inst_net_wan": [300.0],
                  "retail_net_wan": [80.0], "n_appear": [2]}).to_csv(tmp_path / "seats.csv", index=False)
    s = _seat_mark(tmp_path, "600000")
    assert "机构" in s and "反指" in s and "游资" in s
    assert _seat_mark(tmp_path, "000999") == ""      # 未上榜 → 空
    assert _seat_mark(tmp_path / "nope", "600000") == ""  # 无 seats.csv → 空
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_l4_seats.py::test_seat_mark_flags_inst_contra_indicator -q`
Expected: FAIL — `ImportError: cannot import name '_seat_mark'`

- [ ] **Step 3: 实现 `_seat_mark`**（贴在 `_pledge_mark` 之后）

```python
def _seat_mark(base: Path, code6: str) -> str:
    """龙虎榜席位行(presence-gated:seats.csv 在且该票近窗口上榜才注)。
    机构净买>0 标 Phase A 反指(机构上榜买后续偏弱);游资净买作接力信号。缺档/未上榜 → ""。"""
    p = Path(base) / "seats.csv"
    if not p.exists():
        return ""
    try:
        df = pd.read_csv(p, dtype={"code": str})
        df["code"] = df["code"].astype(str).str.zfill(6)
        sub = df[df["code"] == code6]
        if not len(sub):
            return ""
        r = sub.iloc[0]
        if float(r.get("n_appear") or 0) <= 0:
            return ""
        inst, retail = float(r.get("inst_net_wan") or 0), float(r.get("retail_net_wan") or 0)
    except Exception:  # noqa: BLE001
        return ""
    contra = "（⚠️Phase A:机构上榜净买后续 T+1~10 偏弱=反指,勿当强利好）" if inst > 0 else ""
    return (f"·龙虎榜近窗口上榜 {int(float(r['n_appear']))} 次:机构净买 {inst:+.0f}万{contra}、"
            f"游资/营业部净买 {retail:+.0f}万")
```

- [ ] **Step 4: 注入 `compose_funnel_brief`**（在 `l4_card.py:239-240` 的 `**筹码(先验)**` 行拼接处追加 `_seat_mark`;`base`/`code6` 在该函数作用域已有,复用 `_pledge_mark(base, code6)` 同参）

```python
        # 原筹码先验行末尾追加席位(presence-gated,空则不变):
        f"·北向占比 {_g(l1,'hk_ratio')}" + _seat_mark(base, code6),
```

- [ ] **Step 5: 跑测试确认通过 + 契约测试未破**

Run: `uv run --no-sync python -m pytest tests/scan/test_l4_seats.py tests/scan/test_l4_helpers.py -q`
Expected: PASS（`_seat_mark` 无 seats.csv 时返回 `""` → 现有简报快照不变,parity 不破）

- [ ] **Step 6: Commit**

```bash
git add autoresearch/scan/agents/l4_card.py tests/scan/test_l4_seats.py
git commit -m "feat(scan): _seat_mark 席位识别 presence-gated 注 L4 简报(游资/机构反指进卡)"
```

---

### Task 3: rubric — l4-card 评分卡明确要求读龙虎榜席位

**Files:**
- Modify: `.claude/agents/l4-card.md`（`## 流程 P0–P5` 表的 P1 行 + `## 铁律` 段）
- Modify: `.claude/skills/stock-research/lite-playbook.md`（真值源同步 anchor,契约测试要求二者同源）
- Test: `tests/test_agent_defs.py`（`test_l4_card_contract_anchors_synced` 已有;新锚同步 playbook）

**Interfaces:**
- Consumes: 无(纯文档 + 契约锚)。**不改早停逻辑 / 评级映射 / OW 三门**。

- [ ] **Step 1: 在 `l4-card.md` P1 行补席位**（`| P1 现状核 | ... | 技术·资金 |` 行的"读什么"列末尾加 `+ 龙虎榜席位(机构/游资净买)`）

- [ ] **Step 2: 在 `## 铁律` 段加一条**

```
- **龙虎榜席位**(简报若带「龙虎榜近窗口上榜」行):机构上榜净买按 Phase A 实测默认**反指**(后续 T+1~10 偏弱,勿当强利好);游资/营业部净买作接力信号。席位只作**技术·资金维校准**,不单独定方向、不越过 rubric 三门。
```

- [ ] **Step 3: 同步 `lite-playbook.md`**（把 Step 2 同一条铁律加进 playbook 对应段,保持契约测试 `test_l4_card_contract_anchors_synced` 要求的"agent 定义与真值源同源"）

- [ ] **Step 4: 把新铁律设为契约锚**,在 `tests/test_agent_defs.py` 的 `anchors` 列表加 `"龙虎榜席位"`;跑:

Run: `uv run --no-sync python -m pytest tests/test_agent_defs.py -q`
Expected: PASS（agent 与 playbook 均含 `龙虎榜席位`）

- [ ] **Step 5: Commit**

```bash
git add .claude/agents/l4-card.md .claude/skills/stock-research/lite-playbook.md tests/test_agent_defs.py
git commit -m "feat(scan): l4-card rubric 要求读龙虎榜席位(机构反指/游资接力;契约锚同步 playbook)"
```

---

## Self-Review

- **Spec coverage(P1 部分)**:A 席位=Task 1+2 ✓;D rubric=Task 3 ✓;**B 筹码分布=已实现**(`harvest.py:948-955` + `l4_card.py:239`,无 task,plan 顶部已注)。**C 跨源陷阱旗已按用户决定砍掉**(不重造 `trap_signals` 已有的东西、不重立判断层——是否陷阱由 L4 卡读数据自判)。#1 新闻 / macro-brief 在 P2/P3 独立 plan(见下)。
- **成本**:Task 1 的 `top_inst` 按日 bulk(≤15 调/轮)+ 跨日复用,已解 lhb_seats 逐票×15 的 ~420 调用;真实墙钟 P4 冒烟核。
- **Placeholder scan**:无 TBD;每步含真实代码/命令/期望。
- **Type consistency**:`seats.csv` 列 `code,inst_net_wan,retail_net_wan,n_appear` 在 Task 1 定义、Task 2 消费一致;`_seat_mark` 读同列。
- **parity**:所有注入 presence-gated(seats.csv 缺→空串),无 seats.csv 时现有卡输出不变。

## 后续 plan(P1 交付后分别写)
- **P2 macro-brief**:新建 `.claude/agents/macro-brief.md`(WebSearch)+ workflow 改派 + `test_agent_defs` 契约。
- **P3 新闻活体调研**:`l4-card` P3 有界 WebSearch 子步 + sector/macro-brief WebSearch + 预算 cap + token 表行 + as-of helper 单测。
