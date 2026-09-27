# 日报引擎 · 批 6(冻结窗后):intel 死票门 `l4_intel.skip_when_dead`(A3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 对 slim 数字已经决绝为负的 finalist 不再派 Sonnet-max 活体情报(每张 ≈$0.5),卡按现规则回退卡内 ≤3 条有界网查;先离线回放 8 个真跑日证明谓词不会误伤,再上线;默认关(parity)。

**Architecture:** 一个谓词 + 一个旋钮 + 一个状态。谓词住 `scan/l4/intel_gate.py`(纯函数,读 L1 因子行);旋钮 `l4_intel.skip_when_dead`(三件套);状态复用 `intel_status.IntelStatus`(`acquisition="SKIPPED_DEAD"`,`availability_for_card="NONE"`,卡据此走缺 intel 回退,任务包标 `intel=skipped_dead`)。runner(批 2)与 legacy `l4-stock.js` 两条派发路径都在派 intel 前问谓词。

**Tech Stack:** pandas、pytest;两条派发路径(JS + runner)。

**Spec:** `docs/superpowers/specs/2026-09-26-daily-engine-consolidation-design.md` §4 A3。

## Global Constraints

- 冻结窗结束(buyability 批 4 十日真跑完成)前只做 Task 1–2(离线);Task 3 上线在窗后。
- 默认 `skip_when_dead: false` = 逐字 parity;开旋钮是用户动作。
- 谓词只读 L1 因子行(`main_inflow_yi`、`cmf_20`、`obv_mom_20`)+ 日历催化旗 + 📌;**不读卡、不读情报**;阈值住代码常量(行为归属),不进 config。
- 📌 保送票与 composite 证据席票**永不**跳过 intel(持仓复核与 E6 候选池不能少料)。
- 卡片契约不变:缺 intel 的回退规则已在 `l4-card.md`(≤3 条卡内网查),本批不改卡。
- 提交末尾 `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`。

## Review Focus

1. **数据缺失 ≠ 死**:三列任一 NaN → 谓词返回 False(不跳),并在状态 note 写「因子缺列」(Task 1)。
2. **盘后利空盲区**:跳过 intel 的票若当晚有 T0 负面(立案/问询),卡只能靠 ≤3 条卡内网查发现——离线回放必须统计「被跳过票中,intel 事件段曾出现净分 ≤−1 的 T0 行」的比例;>10% 则谓词不上线(Task 2 的停机规则)。
3. **状态可对账**:`intel_query_cap_lint`/`product_shape_lint` 的「intel 稿数 = 全 finalist 行」期望必须扣掉 `SKIPPED_DEAD` 的票,否则上线当天 lint 全红(Task 3)。
4. **两条路径一致**:JS 与 runner 对同一 staging 给出相同的跳过集合(用同一 Python CLI `intel_gate decide <date>` 落 `_intel_gate.json`,JS 与 runner 都只读它)(Task 3)。
5. **计量对账**:`usage_reconcile` 的 intel 期望数要读 `_intel_gate.json`(Task 3)。

---

### Task 1: 谓词 + 旋钮(离线可做)

**Files:**
- Create: `autoresearch/scan/l4/intel_gate.py`
- Modify: `autoresearch/scan/user_config.py`(`l4_intel` 子键加 `skip_when_dead`,类型 bool)
- Modify: `.claude/skills/scan-market/scan_config.jsonc`(`l4_intel` 块加 `"skip_when_dead": false` + 注释)
- Test: `tests/scan/test_intel_gate.py`、`tests/scan/test_config_knobs.py`

- [ ] **Step 1: 写失败测试**

```python
from autoresearch.scan.l4 import intel_gate as ig


def _row(**kw):
    base = {"main_inflow_yi": -0.8, "cmf_20": -0.05, "obv_mom_20": -0.02, "has_catalyst": False,
            "pinned": False, "guard": ""}
    base.update(kw); return base


def test_dead_when_all_three_negative_and_no_catalyst():
    assert ig.is_dead(_row()) is True


def test_alive_if_any_leg_non_negative_or_catalyst_or_pinned_or_seat():
    assert ig.is_dead(_row(cmf_20=0.01)) is False
    assert ig.is_dead(_row(has_catalyst=True)) is False
    assert ig.is_dead(_row(pinned=True)) is False
    assert ig.is_dead(_row(guard="composite_seat")) is False


def test_missing_factor_is_not_dead_and_says_why():
    verdict = ig.decide_row(_row(cmf_20=float("nan")))
    assert verdict.dead is False and "缺列" in verdict.note


def test_decide_writes_gate_file_only_when_knob_on(tmp_path, monkeypatch):
    d = tmp_path / "2026-09-17"; d.mkdir()
    pd.DataFrame([{"code": "600000", "lane": "trend", "guard": ""}]).to_csv(d / "finalists.csv", index=False)
    pd.DataFrame([{"code": "600000", "main_inflow_yi": -1, "cmf_20": -0.1, "obv_mom_20": -0.1}]).to_csv(d / "L1_scored_full.csv", index=False)
    monkeypatch.setattr("autoresearch.scan.user_config.load_user_config",
                        lambda path=None: {"l4_intel": {"enabled": True, "max_queries": 20, "skip_when_dead": False}})
    assert ig.decide(d) == {"enabled": False, "skipped": [], "checked": 1}
    assert not (d / "_intel_gate.json").exists()                    # 关着 = 逐字 parity,不落文件
    monkeypatch.setattr("autoresearch.scan.user_config.load_user_config",
                        lambda path=None: {"l4_intel": {"enabled": True, "max_queries": 20, "skip_when_dead": True}})
    res = ig.decide(d)
    assert res["skipped"] == ["600000"] and json.loads((d / "_intel_gate.json").read_text())["skipped"] == ["600000"]
```

- [ ] **Step 2: RED**;**Step 3: 实现**

```python
# autoresearch/scan/l4/intel_gate.py
"""intel 死票门(A3):slim 数字已决绝为负的 finalist 不派活体情报;默认关(parity)。"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

DEAD_MAIN_INFLOW_YI_MAX = 0.0    # 主力绝对净额 < 0
DEAD_CMF_MAX = 0.0               # cmf_20 < 0
DEAD_OBV_MAX = 0.0               # obv_mom_20 < 0


@dataclass(frozen=True)
class Verdict:
    dead: bool
    note: str


def decide_row(row: dict) -> Verdict:
    if bool(row.get("pinned")) or str(row.get("guard") or "") == "composite_seat":
        return Verdict(False, "📌/证据席恒派 intel")
    if bool(row.get("has_catalyst")):
        return Verdict(False, "带日期催化,派 intel")
    vals = []
    for k in ("main_inflow_yi", "cmf_20", "obv_mom_20"):
        v = row.get(k)
        try:
            f = float(v)
        except (TypeError, ValueError):
            return Verdict(False, f"因子缺列 {k},不判死")
        if math.isnan(f):
            return Verdict(False, f"因子缺列 {k},不判死")
        vals.append(f)
    dead = vals[0] < DEAD_MAIN_INFLOW_YI_MAX and vals[1] < DEAD_CMF_MAX and vals[2] < DEAD_OBV_MAX
    return Verdict(dead, "三线同负且无催化" if dead else "有一线不为负")


def is_dead(row: dict) -> bool:
    return decide_row(row).dead


def decide(scan_dir: Path | str) -> dict:
    """按 finalists.csv × L1 因子行 × 日历催化旗判死;旋钮开才落 `_intel_gate.json`。"""
    from autoresearch.scan.user_config import load_user_config
    sd = Path(scan_dir)
    knob = bool(((load_user_config().get("l4_intel") or {}).get("skip_when_dead", False)))
    fin = pd.read_csv(sd / "finalists.csv", dtype={"code": str}) if (sd / "finalists.csv").exists() else pd.DataFrame()
    l1 = pd.read_csv(sd / "L1_scored_full.csv", dtype={"code": str}) if (sd / "L1_scored_full.csv").exists() else pd.DataFrame()
    cat = _catalyst_codes(sd)
    skipped, notes = [], {}
    for _, r in fin.iterrows():
        code = str(r["code"]).zfill(6)
        f = l1[l1["code"].astype(str).str.zfill(6) == code]
        row = {**(f.iloc[0].to_dict() if len(f) else {}), "pinned": str(r.get("lane", "")) == "pinned",
               "guard": r.get("guard", ""), "has_catalyst": code in cat}
        v = decide_row(row); notes[code] = v.note
        if v.dead:
            skipped.append(code)
    result = {"enabled": knob, "skipped": skipped if knob else [], "checked": int(len(fin)), "notes": notes}
    if knob:
        (sd / "_intel_gate.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return {k: result[k] for k in ("enabled", "skipped", "checked")}


def _catalyst_codes(sd: Path) -> set[str]:
    p = sd / "L3_catalyst.csv"
    if not p.exists():
        return set()
    df = pd.read_csv(p, dtype={"code": str})
    num = df.drop(columns=["code"], errors="ignore").apply(pd.to_numeric, errors="coerce").fillna(0)
    return set(df.loc[num.sum(axis=1) > 0, "code"].astype(str).str.zfill(6))
```
(`has_catalyst` 的来源 `L3_catalyst.csv` 列名以 `scan/agents/l3_catalyst.py` 实际输出为准,实施前 `head -2` 看一眼。)

- [ ] **Step 4: GREEN**;**Step 5: 提交** — `feat(scan): intel dead-ticket gate predicate + l4_intel.skip_when_dead knob (default off)`

---

### Task 2: 离线回放 8 个真跑日(证据门,离线可做)

**Files:**
- Create: `autoresearch/scan/l4/intel_gate.py` 增 `replay` 子命令(`intel_gate replay <scan_dir>...`:对每个 run 的 staging 判死,并对照该 run 的 `_l4_intel_<code>.md` 事件段与 `details/<code>.md` 终评级)
- Create: `docs/research/2026-1x-intel-gate-replay.md`
- Test: `tests/scan/test_intel_gate.py`(replay 输出列:`run, code, dead, final_rating, intel_t0_negative, intel_events`)

- [ ] **Step 1**: 失败测试(合成两票:一票 dead 且 intel 有 T0 −2 行 → `intel_t0_negative=True`;汇总行 `n_dead / n_dead_t0_negative / n_dead_rated_ge_hold`)。
- [ ] **Step 2**: RED;**Step 3**: 实现(只读 `reports_claude/scan/<run>/details` 与 `trace/staging` 或 `context_claude/scan_runs/*/staging`;写 stdout CSV);**Step 4**: GREEN;**Step 5**: 真跑回放 8 日:

```bash
for r in reports_claude/scan/202609*_*/; do echo $r; done
uv run --no-sync python -m autoresearch.scan.l4.intel_gate replay reports_claude/scan/20260917-0917_2152/trace/staging ... > /tmp/claude-scratch/intel_gate_replay.csv
```
**停机规则(预注册,写在文档开头)**:`n_dead_t0_negative / n_dead > 10%` 或 `n_dead_rated_ge_hold / n_dead > 5%` → 谓词不上线(Task 3 不做);否则记录「预计每场省 intel 张数 = n_dead / n_runs」。

---

### Task 3: 上线接线(冻结窗后)

**Files:**
- Modify: `autoresearch/scan/agents/l4_card.py`(`prompts` 子命令前跑 `intel_gate.decide`;任务包在跳过票写一行 `- 活体情报:**已按死票门跳过**(intel=skipped_dead),按缺 intel 规则 ≤3 条卡内网查`)
- Modify: `.claude/workflows/l4-stock.js:355-405`(派 intel 前读 `_intel_gate.json`,命中 → 不派,`intel_status --skipped-dead` 落状态)
- Modify: `autoresearch/session_agent/domain_ops.py`(`scan_l4_prepare` 同款;`scan.l4.intel.status` 接受 `SKIPPED_DEAD`)
- Modify: `autoresearch/scan/l4/intel_status.py`(`acquisition="SKIPPED_DEAD"`)、`autoresearch/scan/self_review.py`(intel 稿数期望扣除跳过集合)、`autoresearch/trace/usage_reconcile.py`(intel 期望数同)
- Test: `tests/scan/test_intel_status.py`、`tests/scan/test_product_shape_lint.py`、`tests/scan/test_l4_dispatch_pack.py`、`tests/trace/test_usage_reconcile.py`、`tests/test_agent_defs.py`(JS 锚 `_intel_gate.json`)

- [ ] 每处一个 RED→GREEN→提交;最后一场真跑(旋钮开)对照:P3 早停率、intel 张数、成本;写读数进文档与记忆。

---

## 自检

- 覆盖 A3 设计三点(旋钮/谓词/离线验收)✓;Review Focus 1–5 各在 T1/T2/T3 有测试或停机规则。
- 不做:改卡片契约;改 rubric;改 Hold 四条件。
