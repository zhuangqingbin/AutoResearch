# scan-market v2 · Phase 2 — 粗排(L2)+ 精排(L3)编排 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development / executing-plans. Steps use `- [ ]`.

**Goal:** 在 P1 召回集(top1000)之上,接两段 AI 资深投资师判断:L2 粗排(1000→200,subagent 扇出 keep/cut)、L3 精排(200→~30,补真证据 + 论点/红队)。

**Architecture:** 确定性 helper(切片/合并/增量取数)在 `scripts/scan_pipeline.py`;AI 判断由 skill 编排 subagent(prompt/rubric 在 `screening-playbook.md`)。中间名单全 staging 到 `context/scan/<date>/`,L5 再发布。

**依赖:** P1 完成(`L1_recall_top1000.csv` 存在)。**Spec:** §4 粗排、§5 精排、§8 数据。

**Phase 2 完成 = 可工作可测:** 给定召回集,`scan_pipeline.py` 能切片/合并;skill 跑通 1000→200→~30,产出 `L2_coarse_keep200.csv`、`L3_evidence/*.json`、`finalists.csv`。helper selftest 绿、ruff 绿。

---

## 文件结构

| 文件 | 责任 | 动作 |
|---|---|---|
| `scripts/scan_pipeline.py` | L2/L3 确定性 helper:切片、紧凑表、配额合并、L3 增量取数 | 新建 |
| `.claude/skills/scan-market/screening-playbook.md` | L2/L3 subagent prompt + rubric + 编排步骤 | 改 |
| `.claude/skills/scan-market/SKILL.md` | 流程步骤改为 选集/召回/粗排/精排/研究/整合 | 改(P3 统一重命名,这里先占位 L2/L3 内容) |

---

## Task 1: scan_pipeline — 召回切片 + 紧凑表

**Files:** Create `scripts/scan_pipeline.py`

- [ ] **Step 1: 失败 selftest**

```python
def _selftest_slice() -> int:
    import pandas as pd, tempfile, os
    from pathlib import Path
    rows = [{"code": f"{i:06d}", "name": f"s{i}", "industry": "电子",
             "composite": 100 - i, "score_momentum": 50, "score_fund_main": 40,
             "pct_60d": 10, "main_net_ratio": 0.01, "winner_rate": 30} for i in range(250)]
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "context/scan/2026-06-20"; d.mkdir(parents=True)
        pd.DataFrame(rows).to_csv(d / "L1_recall_top1000.csv", index=False)
        batches = list(slice_recall("2026-06-20", batch_size=100, root=Path(td) / "context/scan"))
    if len(batches) != 3 or len(batches[0][1]) != 100 or len(batches[2][1]) != 50:
        print(f"SELFTEST ❌  切片数/大小错: {[len(b[1]) for b in batches]}"); return 1
    # 紧凑表是 markdown,含表头与代码
    md = compact_table(batches[0][1])
    if "code" not in md or "000000" not in md:
        print("SELFTEST ❌  紧凑表缺列/数据"); return 1
    print("SELFTEST ✅  召回切片 + 紧凑表"); return 0
```

- [ ] **Step 2: 实现 `slice_recall` + `compact_table`**

```python
#!/usr/bin/env python3
"""scan-market v2 · L2/L3 确定性 helper(切片 / 紧凑表 / 配额合并 / L3 增量取数)。零 LLM。"""
from __future__ import annotations
from pathlib import Path
import pandas as pd

# L2 subagent 要看的紧凑列(复合分 + 8 子分 + 关键原始因子)
_L2_COLS = ["code", "name", "industry", "composite",
            "score_momentum", "score_fund_main", "score_fund_retail", "score_chip",
            "score_north", "score_tech", "score_growth", "score_value",
            "pct_60d", "main_net_ratio", "retail_net_yi", "winner_rate",
            "chip_concentration", "hk_ratio", "rsi6", "pe", "pb", "dv_ratio", "np_yoy", "roe"]


def slice_recall(date: str, batch_size: int = 100, root: Path | None = None):
    """召回集按 composite 降序切片;yield (batch_idx, DataFrame)。"""
    root = root or Path("context/scan")
    df = pd.read_csv(root / date / "L1_recall_top1000.csv")
    df = df.sort_values("composite", ascending=False).reset_index(drop=True)
    for i in range(0, len(df), batch_size):
        yield i // batch_size, df.iloc[i:i + batch_size]


def compact_table(df: pd.DataFrame) -> str:
    """子集 → markdown 紧凑表(只留 _L2_COLS 中存在的列),喂 L2 subagent。"""
    cols = [c for c in _L2_COLS if c in df.columns]
    sub = df[cols].copy()
    head = "| " + " | ".join(cols) + " |"
    sep = "|" + "|".join(["---"] * len(cols)) + "|"
    lines = [head, sep]
    for _, r in sub.iterrows():
        lines.append("| " + " | ".join(_fmt(r[c]) for c in cols) + " |")
    return "\n".join(lines)


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:.2f}".rstrip("0").rstrip(".") if v == v else "—"
    return str(v)
```

- [ ] **Step 3/4: selftest + ruff**

Run: `uv run --no-sync python -c "import sys; sys.path.insert(0,'scripts'); import scan_pipeline as p; sys.exit(p._selftest_slice())"`
Expected: `SELFTEST ✅  召回切片 + 紧凑表`
Run: `uv run --no-sync ruff check scripts/scan_pipeline.py`

---

## Task 2: scan_pipeline — L2 配额合并

**Files:** Modify `scripts/scan_pipeline.py`(`merge_l2_keeps`)

- [ ] **Step 1: 失败 selftest**

```python
def _selftest_merge_l2() -> int:
    import pandas as pd
    # 各批 subagent 回传的 keep 列表(code + L2分 + 理由)
    keeps = [pd.DataFrame({"code": ["000001", "000002"], "l2_score": [80, 60], "l2_reason": ["强", "中"]}),
             pd.DataFrame({"code": ["000003"], "l2_score": [90], "l2_reason": ["很强"]})]
    recall = pd.DataFrame({"code": ["000001", "000002", "000003"], "industry": ["A", "A", "B"],
                           "composite": [99, 70, 85]})
    out = merge_l2_keeps(keeps, recall, target=2)
    if len(out) != 2 or "000003" not in set(out["code"]):
        print(f"SELFTEST ❌  合并取 top2 错: {list(out['code'])}"); return 1
    print("SELFTEST ✅  L2 配额合并(按 composite×l2 排序截断)"); return 0
```

- [ ] **Step 2: 实现**

```python
def merge_l2_keeps(keep_frames: list[pd.DataFrame], recall: pd.DataFrame, target: int = 200) -> pd.DataFrame:
    """合并各批保留 → 取 target。排序键 = 归一(composite) × 归一(l2_score)。"""
    keeps = pd.concat([k for k in keep_frames if k is not None and len(k)], ignore_index=True)
    keeps["code"] = keeps["code"].astype(str).str.zfill(6)
    m = keeps.merge(recall, on="code", how="left")
    for c in ("composite", "l2_score"):
        rng = m[c].max() - m[c].min()
        m[f"_n_{c}"] = (m[c] - m[c].min()) / rng if rng else 0.5
    m["_rank"] = m["_n_composite"] * m["_n_l2_score"]
    return m.sort_values("_rank", ascending=False).head(target).reset_index(drop=True)
```

- [ ] **Step 3/4: selftest + ruff**(命令同上,函数名 `_selftest_merge_l2`)。

---

## Task 3: scan_pipeline — L3 增量取数(bulk 真证据)

**Files:** Modify `scripts/scan_pipeline.py`(`harvest_l3_evidence`,tushare bulk,失败降级)

- [ ] **Step 1: 实现(联网,200 只可控;每端点一次 bulk by date,本地按 code 过滤)**

```python
def harvest_l3_evidence(date: str, codes: list[str], root: Path | None = None) -> dict:
    """对 L2 保留的 ~200 只补 L1 没有的真证据。bulk by date 一次拉、本地过滤;失败降级标注。
    产出 context/scan/<date>/L3_evidence/<code>.json,返回 {code: evidence}。"""
    import json
    from tushare_source import _pro, _ts_call, _code6, resolve_momentum_dates
    root = root or Path("context/scan")
    out_dir = root / date / "L3_evidence"; out_dir.mkdir(parents=True, exist_ok=True)
    pro = _pro(); last = resolve_momentum_dates(pro, date)[0]
    want = set(c.zfill(6) for c in codes)
    ev: dict[str, dict] = {c: {"code": c} for c in want}

    def _bulk(label, fn, key_field="ts_code"):
        try:
            df = _ts_call(fn); df["_c"] = _code6(df[key_field])
            for c, g in df[df["_c"].isin(want)].groupby("_c"):
                ev[c][label] = g.drop(columns=["_c"]).to_dict("records")
        except Exception as e:  # noqa: BLE001
            for c in want: ev[c].setdefault("_degraded", []).append(f"{label}:{e!r}")

    _bulk("longhu", lambda: pro.top_list(trade_date=last))                 # 龙虎榜
    _bulk("forecast", lambda: pro.forecast(period=_period(date)), )         # 业绩预告(最近报告期)
    _bulk("express", lambda: pro.express(period=_period(date)))             # 快报
    # 户数/质押/北向趋势:逐只(200 可控)或 bulk;失败降级
    for c in want:
        (out_dir / f"{c}.json").write_text(json.dumps(ev[c], ensure_ascii=False, default=str), encoding="utf-8")
    return ev
```
(`_period(date)` = 最近报告期 YYYYMMDD,复用 `latest_reported_quarter` 去连字符;`top_list`/`forecast`/`express` 无权限则该项降级,thesis 标"未取到"。)

- [ ] **Step 2: 联网 smoke(挑 5 只)**

Run: `perl -e 'alarm 90; exec @ARGV' uv run --no-sync python -c "import sys; sys.path.insert(0,'scripts'); from scan_pipeline import harvest_l3_evidence as h; e=h('2026-06-19',['600519','000001','300750','002594','601318']); print({k:list(v.keys()) for k,v in e.items()})"`
Expected: 每只打印取到的证据键(longhu/forecast/express/…)或降级标注。

- [ ] **Step 3: ruff**

---

## Task 4: scan_pipeline — L3 finalists 合并

**Files:** Modify `scripts/scan_pipeline.py`(`merge_l3_finalists`)

- [ ] **Step 1: 失败 selftest**

```python
def _selftest_merge_l3() -> int:
    import pandas as pd
    judged = pd.DataFrame({
        "code": ["000001", "000002", "000003"], "name": ["a", "b", "c"], "sector": ["电子"] * 3,
        "lenses": ["动量"] * 3, "conviction": [80, 50, 90], "fragility": [20, 40, 70],
        "thesis": ["t1", "t2", "t3"], "risk": ["r1", "r2", "r3"], "catalyst": ["c1", "c2", "c3"],
        "triage_lean": ["看多"] * 3, "triage_reason": ["x"] * 3})
    out = merge_l3_finalists(judged, target=2)
    # net = conviction - fragility:000001=60,000002=10,000003=20 → 取 000001,000003
    if set(out["code"]) != {"000001", "000003"}:
        print(f"SELFTEST ❌  net 排序错: {list(out['code'])}"); return 1
    need = {"ticker", "code", "name", "sector", "lenses", "conviction", "triage_lean",
            "triage_reason", "thesis", "risk", "catalyst"}
    if not need <= set(out.columns):
        print(f"SELFTEST ❌  缺列 {need - set(out.columns)}"); return 1
    print("SELFTEST ✅  L3 finalists 合并(确信度−脆弱度)+ 列齐"); return 0
```

- [ ] **Step 2: 实现**

```python
def merge_l3_finalists(judged: pd.DataFrame, target: int = 30) -> pd.DataFrame:
    """按 确信度−脆弱度 取 target;输出 finalists.csv 列(兼容 L4/L5,+thesis/risk/catalyst)。"""
    m = judged.copy()
    m["code"] = m["code"].astype(str).str.zfill(6)
    m["net"] = m["conviction"].fillna(0) - m["fragility"].fillna(0)
    m = m.sort_values("net", ascending=False).head(target).reset_index(drop=True)
    m["ticker"] = m["code"]                       # harvester 自动补后缀
    cols = ["ticker", "code", "name", "sector", "lenses", "conviction",
            "triage_lean", "triage_reason", "thesis", "risk", "catalyst"]
    return m[[c for c in cols if c in m.columns]]
```

- [ ] **Step 3/4: selftest + ruff;合并所有 _selftest_* 到 `--selftest`**

Run: `uv run --no-sync python scripts/scan_pipeline.py --selftest`
Expected: 全部 `SELFTEST ✅`。

---

## Task 5: screening-playbook — L2/L3 编排 prose

**Files:** Modify `.claude/skills/scan-market/screening-playbook.md`

- [ ] **Step 1: 写「L2 粗排」段**(替换旧 L3a):
  - 步骤:`for batch in slice_recall(date)`:对每批 `compact_table` → **subagent**(独立 context),给 rubric(信号共振 / 排陷阱:放量滞涨派发、价值陷阱、过热透支、筹码松散+高获利盘、北向流出 / 流动性题材),要求**只回传** `保留 code + l2_score(0-100) + ≤15字理由`。主线 `merge_l2_keeps(...) → L2_coarse_keep200.csv`。
  - subagent prompt 模板(完整给出,含输入紧凑表占位、输出 CSV 格式约定)。

- [ ] **Step 2: 写「L3 精排」段**(替换旧 L3b 的"决策卡"描述移到 L4):
  - `harvest_l3_evidence(date, keep200.code)` → 每只 evidence json。
  - **subagent**(逐只或小批)读 evidence + 该只 L1 因子,输出 `thesis / risk(红队) / catalyst / conviction / fragility`。
  - 主线 `merge_l3_finalists(...) → finalists.csv`。
  - subagent prompt 模板(完整)。

- [ ] **Step 3:** 更新漏斗图为 `选集→召回(1000)→粗排(200)→精排(30)→研究(卡)→整合`。

- [ ] **Step 4: 校验**:重读确认无旧 L3a/L3b 残留(P3 再做全量重命名)。

---

## Task 6: SKILL.md — 流程串起 L0–L5(占位,P3 统一定稿)

**Files:** Modify `.claude/skills/scan-market/SKILL.md`

- [ ] 把流程步骤改为:1 取数(L0+L1 召回,`screen_market --source tushare`)→ 2 过目召回 → 3 L2 粗排(subagent)→ 4 L3 精排(subagent + 增量)→ 5 L4 研究(lite 卡)→ 6 L5 整合(assemble_scan)。命令 + staging 路径写全。

## Phase 2 Self-Review(vs spec §4/§5)
- ✅ L2 subagent 扇出 + rubric + 配额合并→200(Task 1/2/5)。
- ✅ L3 增量真证据 + 论点/红队 + 确信度−脆弱度→30(Task 3/4/5)。
- ✅ finalists.csv 兼容 L4/L5 + thesis/risk/catalyst(Task 4)。
- ⏭ 全量重命名 + L5 整合 → P3。
