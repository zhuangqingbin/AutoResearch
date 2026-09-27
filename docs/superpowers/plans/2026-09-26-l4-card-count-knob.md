# L4 卡数旋钮(`l4.max_cards`)实施计划 —— 批 1.5

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `scan_config.jsonc` 里用**一个键** `l4.max_cards` 决定每场扫描最终进入 L4 决策卡的非 📌 票数,并让它在 Workflow 路径与 session_v1 路径上都真实生效,发布前有机器对账。

**Architecture:** 今天决定卡数的是四处互相不知道的数字:`menu.l4_budget`(五面旗 30/22/15)、`scan-market.js:456` 写死的 `Math.min(10, l4Budget)`、`l3.finalist_max`(10)、`l3.composite_seat.m`(3,不占名额)。本计划把算法收进一个纯函数 `scan/l4/card_count.effective_caps`,由 GATE1 算一次并回显(`l3cap`/`max_cards`),Workflow 与 session_agent 都只读 GATE1 结果;`write_finalists` 末尾加守卫⑩按 `max_cards` 截尾;`self_review` 加发布前对账探针;`l3.finalist_max` 退役(两个键管一个数 = 漂移源)。默认值 13 = 现行 10 + 3,逐字 parity,冻结窗内可合入。

**Tech Stack:** Python 3 / pandas / pytest(`uv run --no-sync pytest`),JS(`node --check`),JSONC。

**Spec:** `docs/superpowers/specs/2026-09-26-daily-engine-consolidation-design.md` §4 A6(2026-09-26 用户需求:「可以配置最终进入 L4 card 的个数,并确保真实能够按这个生效」)。

## Global Constraints

- 新参数三件套:`user_config.py` 白名单 + 类型 + 真实消费点 + 测试锁(`tests/scan/test_config_knobs.py`)。白名单外的键 load 即 raise。
- 默认值必须逐字 parity:不改 `scan_config.jsonc` 里的值时,finalists.csv / `_l4_tasks.json` / brief ② 与改动前字节相同(用 09-17 冻结 staging 回放验证)。
- 📌 保送持仓恒出卡、不占 `max_cards` 名额(用户持仓复核不能被卡数旋钮挤掉)。
- 优先级恒为:CLI 显式 flag > `scan_config.jsonc` > 代码内建默认(`DEFAULT_MAX_CARDS = 13`)。
- 单一算法:`effective_caps` 是 `l3cap`/`max_cards` 的唯一出处;`scan-market.js` 与 `session_agent/domain_ops.scan_l3_merge` 都不得再各算一遍。
- 两引擎同修:`.codex/` 无需改(agent toml 是指针;Codex 走同一 GATE1 结果)。
- 每个任务单独提交,末尾带 `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`。

## Review Focus

1. `max_cards < composite_seat.m` 时(用户把卡数设成 2,席位却要 3):守卫⑩必须把席位也截掉而不是溢出,且 `finalist_cap` 不得为 0 或负 → `effective_caps` 测试覆盖 `max_cards=2, m=3`。
2. `budget_flags=false` 时五面旗必须只留痕不生效:GATE1 结果里 `l4_budget` 仍是旗后值(展示/账本用),但 `l3cap` 不受它影响 → `gate1` 测试覆盖。
3. SENTINEL_PINNED 模式(`run_mode.write_pinned_finalists` 直接写只含 📌 的 finalists.csv)不经过 `write_finalists`,旋钮不得对它产生任何影响 → 探针对全 📌 任务簿必须静默。
4. 老 run 的 `gate1.json` 没有 `l3cap` 键:`domain_ops.scan_l3_merge` 与 JS 都要有兜底(回退 `l4_budget`)且留痕,不能 KeyError。
5. `l3.finalist_max` 仍写在用户 jsonc 里时必须**当场报错并指路**(不是静默忽略),否则用户以为改了 finalist_max 就会生效。

---

### Task 1: 纯函数 `effective_caps` + 配置三件套

**Files:**
- Create: `autoresearch/scan/l4/card_count.py`
- Modify: `autoresearch/scan/user_config.py:90-130`(白名单)、`:163-200`(`_KNOB_TYPES`)
- Modify: `.claude/skills/scan-market/scan_config.jsonc:243-247`(删 `finalist_max`)、`:286`前(新增 `l4` 块)
- Test: `tests/scan/test_card_count.py`(新)、`tests/scan/test_config_knobs.py`

**Interfaces:**
- Produces: `effective_caps(cfg: dict | None, l4_budget: int) -> dict` 返回键 `max_cards, budget_flags, seat_m, finalist_cap, l3cap`;常量 `DEFAULT_MAX_CARDS = 13`。

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_card_count.py
from __future__ import annotations

import pytest

from autoresearch.scan.l4.card_count import DEFAULT_MAX_CARDS, effective_caps


def test_default_is_parity_with_today():
    """默认 13 = finalist_max 10 + composite m 3;l3cap 与改动前 Math.min(10, budget) 逐字同值。"""
    assert DEFAULT_MAX_CARDS == 13
    cfg = {"l3": {"composite_seat": {"enabled": True, "m": 3}}}
    for budget, want in ((30, 10), (22, 10), (15, 10), (8, 8)):
        assert effective_caps(cfg, budget)["l3cap"] == want


def test_max_cards_binds_below_budget():
    cfg = {"l4": {"max_cards": 5}, "l3": {"composite_seat": {"enabled": True, "m": 3}}}
    caps = effective_caps(cfg, 30)
    assert caps == {"max_cards": 5, "budget_flags": True, "seat_m": 3,
                    "finalist_cap": 2, "l3cap": 2}


def test_max_cards_smaller_than_seats_keeps_positive_cap():
    cfg = {"l4": {"max_cards": 2}, "l3": {"composite_seat": {"enabled": True, "m": 3}}}
    caps = effective_caps(cfg, 30)
    assert caps["finalist_cap"] == 1 and caps["l3cap"] == 1    # 永不为 0/负


def test_budget_flags_false_ignores_menu_budget():
    cfg = {"l4": {"max_cards": 20, "budget_flags": False},
           "l3": {"composite_seat": {"enabled": True, "m": 3}}}
    assert effective_caps(cfg, 15)["l3cap"] == 17               # 旗压到 15 也不理


def test_seats_disabled_do_not_reserve():
    cfg = {"l4": {"max_cards": 8}, "l3": {"composite_seat": {"enabled": False, "m": 3}}}
    assert effective_caps(cfg, 30) == {"max_cards": 8, "budget_flags": True, "seat_m": 0,
                                       "finalist_cap": 8, "l3cap": 8}
```

并在 `tests/scan/test_config_knobs.py` 的 `test_new_blocks_whitelisted` 的 `raw` 里加 `"l4": {"max_cards": 13, "budget_flags": True}`,在 `test_knob_type_violations_raise` 参数里加 `{"l4": {"max_cards": 0}}` 与 `{"l4": {"budget_flags": "yes"}}`,再加:

```python
def test_retired_finalist_max_is_rejected_with_pointer(tmp_path):
    """两个键管一个数 = 漂移源:l3.finalist_max 退役,写了要当场指路到 l4.max_cards。"""
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"l3": {"finalist_max": 10}}), encoding="utf-8")
    with pytest.raises(ValueError, match="l4.max_cards"):
        load_user_config(p)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest tests/scan/test_card_count.py tests/scan/test_config_knobs.py -q`
Expected: FAIL(`ModuleNotFoundError: autoresearch.scan.l4.card_count`;`l4` 顶层键未知)

- [ ] **Step 3: 实现**

```python
# autoresearch/scan/l4/card_count.py
"""L4 卡数的唯一算法(2026-09-26 用户需求:一个键决定最终进 L4 卡的票数,且真实生效)。

四处曾各管一段:menu.l4_budget(五面旗)/ scan-market.js 写死的 min(10, budget)/
l3.finalist_max / composite_seat.m(不占名额)。现在只有这里算,GATE1 回显,消费方只读。
"""
from __future__ import annotations

DEFAULT_MAX_CARDS = 13      # = 退役前 finalist_max 10 + composite m 3(逐字 parity)


def effective_caps(cfg: dict | None, l4_budget: int) -> dict:
    """返回 {max_cards, budget_flags, seat_m, finalist_cap, l3cap}。

    - `max_cards`:非 📌 卡上限(含 composite 席位);📌 持仓恒出卡、不占额。
    - `finalist_cap` = max(1, max_cards − seat_m):留给 L3 finalist tier 的名额。
    - `l3cap`:传给 l3-rank / `write_finalists` / GATE2 的预算;`budget_flags=true` 时再与
      `menu.l4_budget`(五面旗只降不升)取小,`false` 时忽略旗。永不为 0。
    """
    from autoresearch.scan.l3.merge import composite_seat_cfg

    l4 = (cfg or {}).get("l4") or {}
    max_cards = int(l4.get("max_cards", DEFAULT_MAX_CARDS))
    budget_flags = bool(l4.get("budget_flags", True))
    seat_on, seat_m = composite_seat_cfg(cfg)
    seat_m = int(seat_m) if seat_on else 0
    finalist_cap = max(1, max_cards - seat_m)
    l3cap = min(finalist_cap, int(l4_budget)) if budget_flags else finalist_cap
    return {"max_cards": max_cards, "budget_flags": budget_flags, "seat_m": seat_m,
            "finalist_cap": finalist_cap, "l3cap": max(1, int(l3cap))}
```

`user_config.py`:`_TOP_WHITELIST` 加 `"l4"`;`_SUB_WHITELIST["l4"] = {"max_cards", "budget_flags"}`;`_SUB_WHITELIST["l3"]` 去掉 `"finalist_max"`;`_KNOB_TYPES` 加 `("l4", "max_cards"): (_t_posint, "正整数")`、`("l4", "budget_flags"): (_t_bool, "boolean")`;在 `load_user_config` 的未知子键检查之前加:

```python
    if "finalist_max" in ((cfg.get("l3") or {}) if isinstance(cfg.get("l3"), dict) else {}):
        raise ValueError("scan_config.json 的 l3.finalist_max 已退役(2026-09-26):卡数只由 l4.max_cards 决定,"
                         "请删除 finalist_max 并改用 l4.max_cards(默认 13 = 原 10 + composite 3)")
```

`scan_config.jsonc`:删除 `"finalist_max": 10,` 行;在 `"l4_intel"` 块之前加:

```jsonc
  // ── L4 · 卡数(2026-09-26 用户需求:最终进入 L4 卡的个数可配置、且真实生效)──────
  // 【生效点】scan/l4/card_count.effective_caps(唯一算法)→ ① gates.gate1 回显 l3cap/max_cards
  //   (scan-market.js 与 session_agent 都只读 GATE1 结果;原 JS 写死的 Math.min(10, …) 已删)
  //   ② l3/merge.write_finalists 守卫⑩:非 📌 行(含 composite 席位)总数 ≤ max_cards,超出按
  //   「席位优先、再按 conviction」截尾进 bench(guard="max_cards")③ self_review.l4_card_count_lint
  //   发布前对账:任务簿非 📌 票数 > max_cards → warn「配置未生效」。
  // 语义:max_cards = 非 📌 卡上限;📌 持仓恒出卡不占额。budget_flags=true 再与 menu.l4_budget
  //   (五面旗只降不升)取小;false 忽略旗恒按 max_cards。原 l3.finalist_max 已退役(写了即报错)。
  // 默认 13 = 原 finalist_max 10 + composite m 3(逐字 parity)。改小立刻生效;改大同时看 pass1_target。
  "l4": { "max_cards": 13, "budget_flags": true },
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run --no-sync pytest tests/scan/test_card_count.py tests/scan/test_config_knobs.py tests/scan/test_user_config.py tests/scan/test_run_contract.py -q`
Expected: PASS(run contract 金样本若锁了 config 键集需同步更新夹具,按测试报错逐一改)

- [ ] **Step 5: 提交**

```bash
git add autoresearch/scan/l4/card_count.py autoresearch/scan/user_config.py .claude/skills/scan-market/scan_config.jsonc tests/scan/test_card_count.py tests/scan/test_config_knobs.py
git commit -m "feat(scan): l4.max_cards — single algorithm for the L4 card count; retire l3.finalist_max"
```

---

### Task 2: GATE1 回显 `l3cap` / `max_cards`,两条编排路径只读它

**Files:**
- Modify: `autoresearch/scan/gates.py:40-58`(`gate1`)
- Modify: `.claude/workflows/scan-market.js:451-456`
- Modify: `autoresearch/session_agent/domain_ops.py:1633-1657`(`scan_l3_merge`)
- Test: `tests/scan/test_gates.py`(若无则新建)、`tests/test_agent_defs.py`

**Interfaces:**
- Consumes: Task 1 的 `effective_caps`。
- Produces: GATE1 结果新增键 `l3cap: int`、`max_cards: int`、`budget_flags: bool`;`STAGE_RESULT.metrics` 同步带出(`record_gate_stage_result` 已透传整个 dict)。

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_gates.py(追加)
def test_gate1_echoes_l3cap_from_effective_caps(tmp_path, monkeypatch):
    d = tmp_path / "2026-09-17"; d.mkdir()
    pd.DataFrame({"code": ["000001", "600000"]}).to_csv(d / "L2_gbdt_top200.csv", index=False)
    monkeypatch.setattr("autoresearch.scan.user_config.load_user_config",
                        lambda path=None: {"l4": {"max_cards": 5},
                                           "l3": {"composite_seat": {"enabled": True, "m": 3}}})
    monkeypatch.setattr("autoresearch.scan.menu.l4_budget", lambda scan_dir, **kw: (30, "菜单健康"))
    monkeypatch.setattr("autoresearch.scan.menu.sentinel_advice", lambda scan_dir, **kw: ("full", "ok"))
    res = gates.gate1(d)
    assert res["ok"] and res["l4_budget"] == 30
    assert res["l3cap"] == 2 and res["max_cards"] == 5 and res["budget_flags"] is True


def test_gate1_budget_flags_false_keeps_budget_for_display_only(tmp_path, monkeypatch):
    d = tmp_path / "2026-09-17"; d.mkdir()
    pd.DataFrame({"code": ["000001"]}).to_csv(d / "L2_gbdt_top200.csv", index=False)
    monkeypatch.setattr("autoresearch.scan.user_config.load_user_config",
                        lambda path=None: {"l4": {"max_cards": 20, "budget_flags": False},
                                           "l3": {"composite_seat": {"enabled": False, "m": 3}}})
    monkeypatch.setattr("autoresearch.scan.menu.l4_budget", lambda scan_dir, **kw: (15, "⚠️ 两旗"))
    monkeypatch.setattr("autoresearch.scan.menu.sentinel_advice", lambda scan_dir, **kw: ("full", "ok"))
    res = gates.gate1(d)
    assert res["l4_budget"] == 15 and res["l3cap"] == 20      # 旗只留痕,不压 l3cap
```

```python
# tests/test_agent_defs.py(追加)
def test_scan_workflow_reads_l3cap_from_gate1_not_a_literal():
    """卡数唯一算法在 Python(card_count.effective_caps);JS 只许读 GATE1 回显。变异:写回 Math.min(10 → 红。"""
    js = (ROOT / ".claude" / "workflows" / "scan-market.js").read_text(encoding="utf-8")
    assert "Math.min(10" not in js, "scan-market.js 仍写死 finalist 上限 10"
    assert "g1m.l3cap" in js, "scan-market.js 未读 GATE1 的 l3cap"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest tests/scan/test_gates.py tests/test_agent_defs.py::test_scan_workflow_reads_l3cap_from_gate1_not_a_literal -q`
Expected: FAIL(`KeyError: 'l3cap'`;JS 断言红)

- [ ] **Step 3: 实现**

`gates.gate1` 末尾:

```python
    from autoresearch.scan.l4.card_count import effective_caps
    from autoresearch.scan.user_config import load_user_config

    caps = effective_caps(load_user_config(), int(budget))
    return {"ok": True, "gate": "gate1", "reason": "ok", "sentinel_level": level,
            "sentinel_reason": sentinel_reason,
            "l4_budget": int(budget), "l2_n": int(len(df)),
            "l3cap": caps["l3cap"], "max_cards": caps["max_cards"],
            "budget_flags": caps["budget_flags"]}
```

`scan-market.js:451-456` 改为:

```js
const l4Budget = Number(g1m.l4_budget)
if (!Number.isInteger(l4Budget) || l4Budget <= 0) {
  throw new Error(`GATE1 未给出可用的 l4_budget(得到 ${JSON.stringify(g1m.l4_budget)})`)
}
// 卡数唯一算法在 Python(scan/l4/card_count.effective_caps),GATE1 回显;这里只读,不再 Math.min(10, …)。
const l3cap = Number(g1m.l3cap)
if (!Number.isInteger(l3cap) || l3cap <= 0) {
  throw new Error(`GATE1 未给出可用的 l3cap(得到 ${JSON.stringify(g1m.l3cap)})—— 升级后的 gates.gate1 必回显它`)
}
log(`GATE1 ✓ sentinel=${g1m.sentinel_level} · L4预算=${l4Budget} · 卡上限 max_cards=${g1m.max_cards} → l3cap=${l3cap}`)
```

`domain_ops.scan_l3_merge`:

```python
    gate1 = json.loads(_text(current, "scan.gate1.result"))
    budget = gate1.get("l3cap", gate1.get("l4_budget"))     # 老 run 无 l3cap → 回退旗后预算(留痕)
    if "l3cap" not in gate1:
        _note(current, "scan.l3.merge", "gate1 缺 l3cap(老 run),回退 l4_budget")
    if type(budget) is not int or budget < 1:
        raise RuntimeError("invalid frozen GATE1 l3cap/l4_budget")
```
(`_note` 若不存在,用现有的 `atomic_write_json(... / "session_outputs/l3_merge_note.json", {...})` 落一行留痕。)

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run --no-sync pytest tests/scan/test_gates.py tests/test_agent_defs.py tests/session_agent -q -k "gate or l3cap or merge"` 且 `node --check .claude/workflows/scan-market.js`
Expected: PASS;node 无输出

- [ ] **Step 5: 提交**

```bash
git add autoresearch/scan/gates.py .claude/workflows/scan-market.js autoresearch/session_agent/domain_ops.py tests/scan/test_gates.py tests/test_agent_defs.py
git commit -m "feat(scan): GATE1 echoes l3cap/max_cards; workflow and session_agent read it instead of a literal 10"
```

---

### Task 3: `write_finalists` 守卫⑩ —— 非 📌 行总数 ≤ `max_cards`

**Files:**
- Modify: `autoresearch/scan/l3/merge.py:567-680`(`write_finalists`)
- Test: `tests/scan/test_finalists_writer.py`

**Interfaces:**
- Consumes: `effective_caps`(读 `load_user_config()`),`merge_l3_finalists_v3(jd, budget, finalist_max)`。
- Produces: `write_finalists` 返回 dict 新增 `max_cards: int`、`max_cards_cut_n: int`、`max_cards_cut: list[str]`;bench 行 `guard="max_cards"`。

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_finalists_writer.py(追加)
def _judged_n(n: int, conv0: int = 90):
    return [{"code": f"{600000 + i:06d}", "name": f"N{i}", "sector": "S", "lenses": "a",
             "conviction": conv0 - i, "fragility": "f", "thesis": "t 1", "mechanism": "m",
             "risk": "r", "catalyst": "c", "triage_lean": "Hold", "lane": "trend",
             "pct_60d": 1.0, "sentiment": "中性", "finalist": True} for i in range(n)]


def test_max_cards_trims_non_pinned_rows_seats_first(tmp_path, monkeypatch):
    base = tmp_path; d = base / "2026-09-17"; d.mkdir()
    (d / "_l3_judged.json").write_text(json.dumps(_judged_n(12)), encoding="utf-8")
    l2 = pd.DataFrame({"code": [f"{600000 + i:06d}" for i in range(12)] + ["000001", "000002", "000003"],
                       "gbdt_score": [0.1] * 12 + [0.9, 0.8, 0.7], "pct_1d": 0.0})
    l2.to_csv(d / "L2_gbdt_top200.csv", index=False)
    monkeypatch.setattr("autoresearch.scan.user_config.load_user_config",
                        lambda path=None: {"l4": {"max_cards": 5},
                                           "l3": {"composite_seat": {"enabled": True, "m": 3}}})
    res = write_finalists("2026-09-17", budget=2, root=base)          # l3cap=2(=5−3)
    fin = pd.read_csv(d / "finalists.csv", dtype={"code": str})
    assert len(fin) == 5 and res["max_cards"] == 5
    assert set(fin[fin["guard"] == "composite_seat"]["code"]) == {"000001", "000002", "000003"}
    assert list(fin[fin["guard"] != "composite_seat"]["code"]) == ["600000", "600001"]
    bench = pd.read_csv(d / "_l3_bench.csv", dtype={"code": str})
    assert (bench["guard"] == "max_cards").sum() == 0                  # 名额恰好配平,无二次截尾


def test_max_cards_below_seats_cuts_seats_too_and_keeps_pinned(tmp_path, monkeypatch):
    base = tmp_path; d = base / "2026-09-17"; d.mkdir()
    (d / "_l3_judged.json").write_text(json.dumps(_judged_n(4)), encoding="utf-8")
    l2 = pd.DataFrame({"code": [f"{600000 + i:06d}" for i in range(4)] + ["000001", "000002", "000003"],
                       "gbdt_score": [0.1] * 4 + [0.9, 0.8, 0.7], "pct_1d": 0.0})
    l2.to_csv(d / "L2_gbdt_top200.csv", index=False)
    pin = tmp_path / "pinned.jsonc"
    pin.write_text(json.dumps({"pinned": [{"code": "688981", "note": "持仓"}]}), encoding="utf-8")
    monkeypatch.setattr("autoresearch.scan.user_config.load_user_config",
                        lambda path=None: {"l4": {"max_cards": 2},
                                           "l3": {"composite_seat": {"enabled": True, "m": 3}}})
    res = write_finalists("2026-09-17", budget=1, root=base, pinned_path=pin)
    fin = pd.read_csv(d / "finalists.csv", dtype={"code": str})
    non_pinned = fin[fin["lane"] != "pinned"]
    assert len(non_pinned) == 2 and "688981" in set(fin["code"])     # 📌 不占额、不被截
    assert res["max_cards_cut_n"] >= 1
    bench = pd.read_csv(d / "_l3_bench.csv", dtype={"code": str})
    assert (bench["guard"] == "max_cards").sum() == res["max_cards_cut_n"]


def test_default_max_cards_is_parity(tmp_path, monkeypatch):
    """不写 l4 块 = 13 = 原 10 + 3:12 只 finalist + 3 席 → 10 + 3。"""
    base = tmp_path; d = base / "2026-09-17"; d.mkdir()
    (d / "_l3_judged.json").write_text(json.dumps(_judged_n(12)), encoding="utf-8")
    l2 = pd.DataFrame({"code": [f"{600000 + i:06d}" for i in range(12)] + ["000001", "000002", "000003"],
                       "gbdt_score": [0.1] * 12 + [0.9, 0.8, 0.7], "pct_1d": 0.0})
    l2.to_csv(d / "L2_gbdt_top200.csv", index=False)
    monkeypatch.setattr("autoresearch.scan.user_config.load_user_config",
                        lambda path=None: {"l3": {"composite_seat": {"enabled": True, "m": 3}}})
    write_finalists("2026-09-17", budget=10, root=base)
    fin = pd.read_csv(d / "finalists.csv", dtype={"code": str})
    assert len(fin) == 13
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest tests/scan/test_finalists_writer.py -q -k max_cards`
Expected: FAIL(`KeyError: 'max_cards'`;行数不等)

- [ ] **Step 3: 实现**(在 `write_finalists` 里:把 `finalist_max` 读取换成 `effective_caps`;pinned 注入之后、写盘之前插入守卫⑩)

```python
    from autoresearch.scan.l4.card_count import effective_caps
    from autoresearch.scan.user_config import load_user_config
    cfg = load_user_config()
    caps = effective_caps(cfg, budget)
    fin, bench = merge_l3_finalists_v3(jd, budget=budget, finalist_max=caps["finalist_cap"])
    ...(守卫⑨ 与 pinned 注入照旧)...
    # 守卫⑩ max_cards(2026-09-26):非 📌 行(含 composite 席位)总数 ≤ max_cards。
    # 保留序:席位优先(证据层直通、E6 候选池依赖)→ conviction 降序;被截行进 bench,guard="max_cards"。
    fin, bench, cut_codes = apply_max_cards(fin, bench, caps["max_cards"])
    ...
    return {..., "max_cards": caps["max_cards"], "max_cards_cut_n": len(cut_codes),
            "max_cards_cut": cut_codes}
```

```python
def apply_max_cards(fin: pd.DataFrame, bench: pd.DataFrame, max_cards: int
                    ) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """守卫⑩:非 📌 行总数 ≤ max_cards;超出按(席位优先, conviction 降序)截尾进 bench。"""
    if "code" not in fin.columns or max_cards <= 0 and len(fin) == 0:
        return fin, bench, []
    lane = fin["lane"].fillna("") if "lane" in fin.columns else pd.Series("", index=fin.index)
    guard = fin["guard"].fillna("") if "guard" in fin.columns else pd.Series("", index=fin.index)
    pinned_mask = lane.astype(str).eq("pinned")
    others = fin[~pinned_mask].copy()
    if len(others) <= max_cards:
        return fin, bench, []
    conv = pd.to_numeric(others.get("conviction"), errors="coerce").fillna(-1)
    is_seat = guard[~pinned_mask].astype(str).eq(COMPOSITE_SEAT_GUARD).astype(int)
    order = others.assign(_seat=is_seat.values, _conv=conv.values) \
                  .sort_values(["_seat", "_conv"], ascending=[False, False], kind="stable")
    keep_idx = list(order.index[:max_cards]); cut_idx = list(order.index[max_cards:])
    cut = fin.loc[cut_idx].copy()
    cut["guard"] = "max_cards"
    kept = pd.concat([fin[pinned_mask], fin.loc[keep_idx]]).loc[fin.index.intersection(
        list(fin[pinned_mask].index) + keep_idx)]        # 保持原相对顺序
    bench_out = pd.concat([bench, cut.drop(columns=["_seat", "_conv"], errors="ignore")],
                          ignore_index=True)
    return kept.reset_index(drop=True), bench_out, [str(c).zfill(6) for c in cut["code"]]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run --no-sync pytest tests/scan/test_finalists_writer.py tests/scan/test_l3_merge_v3.py tests/scan/test_l4_dispatch_pack.py -q`
Expected: PASS(`test_cap_is_min_of_finalist_max_and_budget` 仍测函数参数语义,不受影响)

- [ ] **Step 5: 提交**

```bash
git add autoresearch/scan/l3/merge.py tests/scan/test_finalists_writer.py
git commit -m "feat(scan): guard ⑩ max_cards in write_finalists — non-pinned rows (seats included) capped by l4.max_cards"
```

---

### Task 4: 发布前对账探针 `l4_card_count_lint`

**Files:**
- Modify: `autoresearch/scan/self_review.py`(新函数 + 挂进 `product_shape_lint` 末尾)
- Test: `tests/scan/test_product_shape_lint.py`

**Interfaces:**
- Consumes: `_l4_tasks.json`(`tasks[code].meta.pinned` 或 `pinned` 字段——按 `l4_tasks._new_task` 实际字段名读,写实现前 `grep -n '"pinned"' autoresearch/scan/l4_tasks.py`),`load_user_config()["l4"]["max_cards"]`。
- Produces: 行 `{"check": "L4 卡数·超配置上限", "severity": "warn", "detail": "...", "code": ""}`;配平或无任务簿 → 不产行。

- [ ] **Step 1: 写失败测试**

```python
def test_l4_card_count_lint_warns_when_dispatch_exceeds_max_cards(tmp_path, monkeypatch):
    d = tmp_path / "2026-09-17"; d.mkdir()
    book = {"schema_version": 1, "date": "2026-09-17",
            "tasks": {f"{600000 + i:06d}": {"status": "SUCCEEDED", "pinned": False} for i in range(6)}}
    book["tasks"]["688981"] = {"status": "SUCCEEDED", "pinned": True}
    (d / "_l4_tasks.json").write_text(json.dumps(book), encoding="utf-8")
    monkeypatch.setattr("autoresearch.scan.user_config.load_user_config",
                        lambda path=None: {"l4": {"max_cards": 5}})
    rows = self_review.l4_card_count_lint(d)
    assert len(rows) == 1 and rows[0]["check"] == "L4 卡数·超配置上限" and rows[0]["severity"] == "warn"
    assert "6" in rows[0]["detail"] and "5" in rows[0]["detail"]


def test_l4_card_count_lint_silent_when_within_cap_or_all_pinned(tmp_path, monkeypatch):
    d = tmp_path / "2026-09-17"; d.mkdir()
    book = {"schema_version": 1, "date": "2026-09-17",
            "tasks": {"688981": {"status": "SUCCEEDED", "pinned": True},
                      "300750": {"status": "SUCCEEDED", "pinned": True}}}
    (d / "_l4_tasks.json").write_text(json.dumps(book), encoding="utf-8")
    monkeypatch.setattr("autoresearch.scan.user_config.load_user_config",
                        lambda path=None: {"l4": {"max_cards": 1}})
    assert self_review.l4_card_count_lint(d) == []          # SENTINEL_PINNED:全 📌,旋钮不管
```

- [ ] **Step 2: 跑测试确认失败** — Run: `uv run --no-sync pytest tests/scan/test_product_shape_lint.py -q -k card_count`;Expected: FAIL(`AttributeError`)

- [ ] **Step 3: 实现**

```python
def l4_card_count_lint(scan_dir) -> list[dict]:
    """L4 卡数对账(2026-09-26):任务簿里非 📌 票数 > l4.max_cards → warn「配置未生效」。

    只读任务簿(派发事实),不读 finalists.csv(意图);全 📌(哨兵持仓档)静默。
    """
    from autoresearch.scan.l4.card_count import DEFAULT_MAX_CARDS
    from autoresearch.scan.user_config import load_user_config

    p = Path(scan_dir) / "_l4_tasks.json"
    if not p.is_file():
        return []
    try:
        tasks = (json.loads(p.read_text(encoding="utf-8")).get("tasks") or {})
    except Exception:  # noqa: BLE001
        return []
    non_pinned = [c for c, t in tasks.items() if not bool((t or {}).get("pinned"))]
    if not non_pinned:
        return []
    max_cards = int(((load_user_config().get("l4") or {}).get("max_cards", DEFAULT_MAX_CARDS)))
    if len(non_pinned) <= max_cards:
        return []
    return [{"check": "L4 卡数·超配置上限", "severity": "warn", "code": "",
             "detail": f"任务簿非📌票 {len(non_pinned)} > l4.max_cards {max_cards}:"
                       f"守卫⑩未生效或任务簿被手工追加({'/'.join(sorted(non_pinned)[:6])}…)"}]
```
并在 `product_shape_lint` 末尾 `out += l4_card_count_lint(scan_dir)`。

- [ ] **Step 4: 跑测试确认通过** — Run: `uv run --no-sync pytest tests/scan/test_product_shape_lint.py tests/scan/test_self_review*.py -q`;Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add autoresearch/scan/self_review.py tests/scan/test_product_shape_lint.py
git commit -m "feat(scan): self_review probe — dispatched non-pinned L4 cards must not exceed l4.max_cards"
```

---

### Task 5: 离线回放验证(真 staging,写 scratch)+ 文档

**Files:**
- Create: `autoresearch/scan/l4/card_count.py` 增 `main()`(`replay` 子命令)
- Modify: `.claude/skills/scan-market/SKILL.md`「配置」节、`STAGES.md` L3 节第 5 条与 L4 节、`docs/ops/scan-ops.md`「L4 派发节奏」
- Test: `tests/scan/test_card_count.py`(CLI 用例)

- [ ] **Step 1: 写失败测试**

```python
def test_replay_cli_copies_staging_and_reports_counts(tmp_path, monkeypatch, capsys):
    src = tmp_path / "src" / "2026-09-17"; src.mkdir(parents=True)
    (src / "_l3_judged.json").write_text(json.dumps(_judged_n(12)), encoding="utf-8")
    pd.DataFrame({"code": [f"{600000 + i:06d}" for i in range(12)], "gbdt_score": 0.1, "pct_1d": 0.0}
                 ).to_csv(src / "L2_gbdt_top200.csv", index=False)
    monkeypatch.setattr("autoresearch.scan.user_config.load_user_config",
                        lambda path=None: {"l3": {"composite_seat": {"enabled": False, "m": 3}}})
    from autoresearch.scan.l4 import card_count
    rc = card_count.main(["replay", str(src), "--max-cards", "5", "--out", str(tmp_path / "out")])
    assert rc == 0
    got = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert got["finalists_non_pinned"] == 5 and got["out"].startswith(str(tmp_path / "out"))
    assert not (src / "finalists.csv").exists()        # 只写 scratch,不碰源 staging
```

- [ ] **Step 2: 跑测试确认失败** — Expected: FAIL(`AttributeError: main`)

- [ ] **Step 3: 实现 `main`**:`replay <staging_dir> --max-cards N [--budget B] --out <dir>`:`shutil.copytree(staging, out/<date>)`,用 `cfg = {**load_user_config(), "l4": {"max_cards": N, "budget_flags": False}}` 经 `monkeypatch` 式注入(`with mock.patch("autoresearch.scan.user_config.load_user_config", lambda path=None: cfg)`)调 `write_finalists(date, budget=effective_caps(cfg, B or 30)["l3cap"], root=out)`,打印 `{"out", "finalists_non_pinned", "finalists_pinned", "max_cards_cut_n"}` 一行 JSON。**禁止写源目录**(路径断言:`out` 不得等于或包含 `staging`)。

- [ ] **Step 4: 真 staging 回放**(只读源,写 scratch):

```bash
S=$(ls -d context_claude/scan_runs/*/staging/2026-09-17 | tail -1)
uv run --no-sync python -m autoresearch.scan.l4.card_count replay "$S" --max-cards 5 --out /tmp/claude-scratch/max_cards_5
uv run --no-sync python -m autoresearch.scan.l4.card_count replay "$S" --max-cards 13 --out /tmp/claude-scratch/max_cards_13
```
Expected:第一行 `finalists_non_pinned == 5`;第二行与源 `finalists.csv` 的非 📌 行数相同且 `diff <(sort $S/finalists.csv) <(sort /tmp/claude-scratch/max_cards_13/2026-09-17/finalists.csv)` 为空(parity 证据)。把两行 JSON 与 diff 结果贴进 `docs/research/2026-09-26-headless-driver-probes.md` 末尾「max_cards 回放」小节。

- [ ] **Step 5: 文档**:SKILL.md「配置」节加一行「**L4 卡数**:`l4.max_cards`(非 📌 上限,默认 13;`budget_flags:false` 忽略五面旗);`l3.finalist_max` 已退役」;STAGES.md L3 第 5 条 `③cap(=min(finalist_max,当日 l4_budget)…)` 改为 `③cap(=GATE1 回显的 l3cap,来自 l4.max_cards − 席位数,见 scan/l4/card_count)`,并在守卫⑨之后加 `⑩ max_cards(非 📌 总数 ≤ l4.max_cards,席位优先、conviction 截尾进 bench)`;L4 节「派发三步」第 2 步加「任务簿票数 = finalists.csv 行数,发布前 `l4_card_count_lint` 对账」;`docs/ops/scan-ops.md`「L4 派发节奏」加同一句。跑 `uv run --no-sync pytest tests/test_agent_defs.py tests/test_doc_budgets.py tests/test_skill_docs_refs.py -q` → PASS(STAGES/SKILL 预算内)。

- [ ] **Step 6: 提交**

```bash
git add autoresearch/scan/l4/card_count.py tests/scan/test_card_count.py .claude/skills/scan-market/SKILL.md .claude/skills/scan-market/STAGES.md docs/ops/scan-ops.md docs/research/2026-09-26-headless-driver-probes.md
git commit -m "feat(scan): max_cards replay CLI + docs; parity verified on 2026-09-17 staging"
```

---

### Task 6: 真跑验收(下一场扫描,干净会话)

**Files:** 无代码;记录到 `docs/research/2026-09-26-headless-driver-probes.md`「max_cards 真跑」小节与记忆。

- [ ] **Step 1**: 扫描前把 `scan_config.jsonc` 的 `l4.max_cards` 改成与当天想要的数(首验建议 **8**,明显区别于 13),提交该配置改动。
- [ ] **Step 2**: 按 SKILL.md 正常开扫(Workflow 路径)。CP1 播报里 GATE1 行必须出现 `卡上限 max_cards=8 → l3cap=5`。
- [ ] **Step 3**: L4 派发后 `uv run --no-sync python -m autoresearch.scan.l4_tasks stats <date>`:非 📌 票数 == 8(或更少,当 L3 够格不足时);brief ② `L4 N 卡` 的 N == 任务簿票数;`gate_fires.csv` 无「L4 卡数·超配置上限」行;`token_usage.md` 里 `l4-card` 行数 == N(+复核)。
- [ ] **Step 4**: 三处数字一致 → 在文档记「真跑生效:<date> max_cards=8 → 卡 N=8」;任一不一致 → 按 `superpowers:systematic-debugging` 立案,不改配置凑数。
- [ ] **Step 5**: 验收后把 `max_cards` 改回用户想要的长期值并提交。

---

## 自检(计划作者)

- 覆盖:配置三件套 ✓(T1)、单一算法 ✓(T1)、两条编排路径 ✓(T2)、finalists 截尾 ✓(T3)、发布对账 ✓(T4)、parity 证据 ✓(T5)、真跑 ✓(T6)、Review Focus 1–5 各有测试或步骤。
- 未做:`menu.l4_budget` 的 base/floor 30/12 仍是代码常量(它们是「旗」的档位,不是卡数;需要时另开旋钮)。
