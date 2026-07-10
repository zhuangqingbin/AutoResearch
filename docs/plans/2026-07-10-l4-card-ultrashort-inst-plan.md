# 波 1b+2 · L4 卡契约超短化 + 机构面引入 · 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** L4 决策卡的目标价/三情景/tripwires 改为 1~2 日超短语义(配 hi_2 触价校准与复用版本门),并给卡片引入「机构面」:卖方修正生产者 + presence-gated 简报行 + L3 rc 列 + 有界 WebSearch;四类机构因子用 T+2 尺重审,数据过门才谈接线。

**Architecture:** 卡片契约文本活在 `.claude/agents/l4-card.md` 与 `stock-research/lite-playbook.md` 双文件(契约锚由 `tests/test_agent_defs.py` 同源校验);模板加 `〔卡契约 v3·超短 1~2 日〕` 确定性标记行,`l4_reuse` 据此拒绝旧语义卡复用。机构面沿 `_cat_mark`/`_seat_mark` 的既有 presence-gated 模式:新生产者 `l4_card consensus` 落 `consensus.csv`,`_inst_mark` 组行,workflow l4-prep 链**生产者先于 prompts**(07-07 排序坑之鉴)。

**Tech Stack:** Python 3 + pandas + pytest(uv);tushare report_rc 缓存(`context/factor_lab/cache/report_rc/`)。

**前置:** `docs/plans/2026-07-10-ultrashort-t2-alignment-plan.md`(波 1a)全部完成——本计划消费 `hi_2_oc`/`fwd_2_oc` 列与新 weights。
**Spec:** `docs/specs/2026-07-10-ultrashort-t2-inst-progress-design.md` §2/§3。

## Global Constraints

- 一律 `uv run --no-sync python ...`,仓库根;每 task 收尾 ruff + 全量 pytest 绿才 commit;commit 中文 conventional + `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`;进度记 `.superpowers/sdd/progress.md`。
- **契约锚纪律**:`.claude/agents/l4-card.md` 与 `lite-playbook.md` 必须同步改(锚测试同源);`parse_rating/lint` 机读的契约行(`**Rating**`/`FINAL TRANSACTION PROPOSAL` 等)一字不动。
- **共享前缀纪律**:所有简报新行都在逐卡块(共享块之后),不碰 `write_dispatch_pack` 的固定标头/共享块顺序;`tests/scan/test_l4_prompt_cache_prefix.py` 必须保持绿(它红了=真前缀断裂,修排版不放宽断言)。
- presence-gated parity:缺 `consensus.csv`/无该票行 → 不加行不加噪。
- 机构面网查是**非零 token 的用户知情选择**(2026-07-10 确认),上界写死在 prompt(≤2 条/卡,触发有条件)。

---

### Task 1: 触价校准切 hi_2(按卡片 schema 日期分界,hi_10 降参考)

**Files:**
- Modify: `autoresearch/learning/buy_ledger.py`(`roll` 命中逻辑、`target_calibration`、render 文案)
- Modify: `autoresearch/learning/cross_calib.py:68-116`(`gate_stats` 主口径 ex5→ex2、触价同步)
- Test: `tests/learning/test_buy_ledger.py`、`tests/learning/test_cross_calib.py`

**Interfaces:**
- Consumes: attribution 的 `hi_2_oc`/`fwd_2_oc`(波 1a);`_target_ret`(不动)。
- Produces: `buy_ledger._SCHEMA_SWITCH = "2026-07-10"` 与 `target_hit_for(day: str, tr, attr_row) -> bool|None`(日期分界触价:switch 日起按 `hi_2_oc`,之前旧卡按 `hi_10_oc`——旧卡是 10 日语义,追溯按 hi_2 判是冤枉);`_COLS` 增 `hi_2`;`target_calibration` 同分界;`gate_stats` 主口径 `ex2` + 同款触价。cross_calib 复用 buy_ledger 的分界 helper(它已 lazy import `_read_attr/_target_ret`,加一个即可)。

- [ ] **Step 1: 写失败测试**

```python
# test_buy_ledger.py 追加
def test_target_hit_schema_switch():
    """switch 日前的卡按 hi_10 判(10日语义),switch 日起按 hi_2(超短语义)。"""
    import pandas as pd

    from autoresearch.learning import buy_ledger as bl

    row = pd.Series({"hi_2_oc": 0.02, "hi_10_oc": 0.08, "gap_d1": 0.0})
    tr = 0.05                                      # 目标 +5%
    assert bl.target_hit_for("2026-07-06", tr, row) is True    # 旧卡:hi_10=8% ≥ 5%
    assert bl.target_hit_for("2026-07-13", tr, row) is False   # 新卡:hi_2=2% < 5%
```

`test_cross_calib.py`:既有 gate_stats fixture 补 `fwd_2_oc/hi_2_oc` 列,断言输出含 `ex2` 且错杀判定用 ex2。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/learning/test_buy_ledger.py tests/learning/test_cross_calib.py -q`
Expected: FAIL(`target_hit_for` 不存在)。

- [ ] **Step 3: 实现**

buy_ledger 模块级加:

```python
_SCHEMA_SWITCH = "2026-07-10"   # 卡契约 v3(超短)生效日:此前卡=10日语义按 hi_10 判,此后按 hi_2


def target_hit_for(day: str, tr: float | None, row) -> bool | None:
    """日期分界触价命中:目标幅(close_D 基)rebase 到 o1 基,与对应窗口 MFE 比。"""
    if tr is None:
        return None
    import pandas as pd
    col = "hi_2_oc" if str(day) >= _SCHEMA_SWITCH else "hi_10_oc"
    hi = pd.to_numeric(pd.Series([row.get(col)]), errors="coerce").iloc[0]
    if pd.isna(hi):
        return None
    gap = pd.to_numeric(pd.Series([row.get("gap_d1")]), errors="coerce").iloc[0]
    t_entry = (1 + tr) / (1 + gap) - 1 if not pd.isna(gap) else tr
    return bool(hi >= t_entry)
```

`roll`:`_COLS` 在 `hi_10` 后加 `hi_2`;行 dict 加 `"hi_2": _a("hi_2_oc")`;hit 计算整段换 `hit = target_hit_for(d.name, tr, attr.loc[code]) if (attr is not None and code in attr.index) else None`(缺 hi 列回退收盘口径的旧分支删除——`target_hit_for` 缺值返 None,诚实标未成熟)。
`target_calibration`:hi/gap 读取段换同一 helper(`hit = target_hit_for(d.name, tr, attr.loc[code])`,None 跳过;`mfes` 取对应窗口列同款分界);docstring「hi_10_oc」→「日期分界:v3 起 hi_2_oc(2日 MFE),旧卡 hi_10_oc」;`calibration_line` 文案「10日触达率」→「触达率(v3 起 2 日窗)」。render 表头 `触价hi10` 后加 `hi_2` 列。
cross_calib `gate_stats`:`m5/ex5` 整组镜像成 `m2/ex2`(fwd_2_oc,主);rows dict `{"gate","ex2","ex5","hit"}`;`hit` 换 `buy_ledger.target_hit_for(d.name, tr, attr.loc[code])`;错杀判定 `miss = (ex2 > 0) & hit`;`_GATE_COLS` 加 `mean_ex2`(保留 mean_ex5)。

- [ ] **Step 4: 跑绿 + 全量回归**

Run: `uv run --no-sync python -m pytest tests/learning/ -q && uv run --no-sync python -m ruff check . && uv run --no-sync python -m pytest tests/ -q`
Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
git add autoresearch/learning/buy_ledger.py autoresearch/learning/cross_calib.py tests/learning/
git commit -m "feat(learning): 触价校准按卡契约日期分界切 hi_2(旧卡 hi_10 不冤枉)+ cross_calib 主口径 ex2

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: 卡片模板超短化(agent 定义 + lite-playbook 双文件同步)

**Files:**
- Modify: `.claude/agents/l4-card.md`(铁律 + 模板 A/B)
- Modify: `.claude/skills/stock-research/lite-playbook.md`(同步镜像)
- Modify: `.claude/skills/stock-research/SKILL.md`(lite 档一句话描述补超短)
- Test: `tests/test_agent_defs.py`(锚列表加新锚)

**Interfaces:**
- Consumes: 无代码依赖。
- Produces: 双文件新增契约锚 `卡契约 v3·超短 1~2 日`(标记行,Task 3 复用门/Task 6 lint 消费);模板时间语义全部 1~2 日。

- [ ] **Step 1: 先改锚测试(失败先行)**

`tests/test_agent_defs.py::test_l4_card_contract_anchors_synced` 的 anchors 列表追加两个:

```python
    anchors = ["进入P4倾向", "FINAL TRANSACTION PROPOSAL", "**Rating**",
               "早停只向下", "Rubric建议", "一段话研判", "L3 论点裁决",
               "已核数字摘录", "多写不多读", "龙虎榜席位", "活体新闻",
               "早停卡短格式", "卡契约 v3·超短 1~2 日", "超短口径",
               *(g for g in _OW_GATES)]
```

Run: `uv run --no-sync python -m pytest tests/test_agent_defs.py -q` → Expected: FAIL(两文件都缺新锚)。

- [ ] **Step 2: 改 `.claude/agents/l4-card.md`(六处,契约行不动)**

① 铁律列表追加一条(放「简报只定向、不判」之后):

```markdown
- **超短口径**:持仓假设 1~2 交易日(D+1 开盘买→最迟 D+2 收盘卖)。目标价/三档情景/触发位全按此窗写——不写周线/月线叙事,catalyst 只算 2 日内能发酵的;时间框架恒填「1~2日」。
```

② 模板 A(早停卡)标题行下加标记行,并改仪表盘行:

```markdown
# 决策卡 — <代码> <名称> @ <date>  ·  〔早停·表面 DD〕
〔卡契约 v3·超短 1~2 日〕

## 决策仪表盘
| 评级 | 现价 | 时间框架 | 触发位 | 置信度 |
|---|---|---|---|---|
| **<五档≤Hold>** | <价> | 1~2日 | <隔日减/清条件> | <高/中/低> |
```

③ 模板 B(满卡)标题行下同加 `〔卡契约 v3·超短 1~2 日〕`;仪表盘注释行改:

```markdown
## 决策仪表盘(评级/现价/EV目标(+%,1~2日窗)/上行下行/R:R/时间框架=1~2日/仓位/触发位(隔日纪律)/置信度)
```

④ 三档情景行改:

```markdown
**三档情景(1~2日窗)**: Bull/Base/Bear → **EV**(对现价±%,D+1开→D+2收);**R:R <比>**
```

⑤ 催化&认错位行的失效段改 `失效:<价/指标/事件→隔日减仓>`;P5 流程表「三档 EV/R:R+预期差」→「三档 EV/R:R(1~2日窗)+预期差」;P3 行「有带日期的前瞻催化?」→「有 2 日内能发酵的带日期催化?」。
⑥ 早停卡短格式行数上限 35→36(标记行占一行):`早停卡正文 ≤36 行`。

- [ ] **Step 3: lite-playbook.md 同步镜像**

对 `.claude/skills/stock-research/lite-playbook.md` 做完全相同的六处修改(它是真值源;先重读该文件再编辑——skill 文档可能被外部改过)。`stock-research/SKILL.md` 里 lite 档描述句(grep `lite`)补一句「lite 卡=超短交易语义(1~2 日窗);full 深研报告不受此限」。

- [ ] **Step 4: 跑锚测试 + 文档契约**

Run: `uv run --no-sync python -m pytest tests/test_agent_defs.py tests/test_skill_docs_refs.py -q`
Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
git add .claude/agents/l4-card.md .claude/skills/stock-research/ tests/test_agent_defs.py
git commit -m "feat(scan): L4 卡契约 v3 超短化(1~2日窗·目标/情景/tripwire·双文件锚同步)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: l4_reuse 卡契约版本门(旧语义卡禁复用)

**Files:**
- Modify: `autoresearch/scan/l4_reuse.py:95-151`(`reuse_decision`)+ carryover 路径(`grep -n "carryover" autoresearch/scan/l4_reuse.py` 定位)
- Test: `tests/scan/test_l4_reuse.py`

**Interfaces:**
- Consumes: Task 2 的标记行 `〔卡契约 v3·超短 1~2 日〕`。
- Produces: 模块级 `_CARD_SCHEMA_MARK = "卡契约 v3"`;旧卡(无标记)→ `reuse=False`,reason=`旧契约卡(schema v3 前,禁复用)`;carryover 同门。升级日全部新派,超短卡与 swing 卡永不混用。

- [ ] **Step 1: 写失败测试**

```python
# test_l4_reuse.py 追加(沿用该文件既有 fixture 构造:前日 details/<code>.md + 当日 staging)
def test_old_schema_card_not_reused(tmp_path):
    <既有构造:一张满足全部复用条件的前卡,但正文不含「卡契约 v3」>
    out = reuse_decision("000001", sd)
    assert out["reuse"] is False
    assert any("旧契约卡" in r for r in out["reasons"])


def test_v3_card_reusable(tmp_path):
    <同上但前卡正文含「〔卡契约 v3·超短 1~2 日〕」>
    out = reuse_decision("000001", sd)
    assert not any("旧契约卡" in r for r in out["reasons"])
```

- [ ] **Step 2: 跑测试确认失败** → `uv run --no-sync python -m pytest tests/scan/test_l4_reuse.py -q`,Expected: FAIL。

- [ ] **Step 3: 实现**

模块级常量 + `reuse_decision` 在 `if "♻️" in text:` 检查之前插:

```python
_CARD_SCHEMA_MARK = "卡契约 v3"   # 2026-07-10 超短化;无标记的旧 swing 语义卡禁复用/禁 carryover


    if _CARD_SCHEMA_MARK not in text:
        out["reasons"].append("旧契约卡(schema v3 前,禁复用)")
```

carryover 路径:grep 定位后加同一判定(无标记不 carryover,理由同)。docstring 记 why。

- [ ] **Step 4: 跑绿 + 全量回归** → `uv run --no-sync python -m pytest tests/scan/ -q && uv run --no-sync python -m ruff check . && uv run --no-sync python -m pytest tests/ -q`,Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
git add autoresearch/scan/l4_reuse.py tests/scan/test_l4_reuse.py
git commit -m "feat(scan): l4_reuse 卡契约版本门(v3 标记缺=禁复用/禁carryover,防超短swing混用)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: 机构面 — `l4_card consensus` 生产者 + `_inst_mark` 行 + L3 rc 列 + workflow 接线

**Files:**
- Modify: `autoresearch/scan/agents/l4_card.py`(新 `fetch_consensus` + `_inst_mark` + CLI 子命令 + `compose_funnel_brief` 插行)
- Modify: `autoresearch/scan/agents/l3_select.py`(L3 表 presence-gated `rc` 列——镜像 `cat` 列接线,`grep -n "cat_flag\|L3_catalyst" autoresearch/scan/agents/l3_select.py` 定位)
- Modify: `.claude/workflows/scan-market.js`(l4-prep 链插生产者,**先于 prompts**)
- Modify: `.claude/skills/scan-market/STAGES.md`(L4 四道闸文案 +卖方修正生产者)
- Test: `tests/scan/test_l4_card_inst.py`(新)+ l3 表测试补 rc 列断言

**Interfaces:**
- Consumes: `autoresearch/research/consensus.py` 的 `_load_span/_median_eps/consensus_delta`;report_rc 缓存(有多少用多少,presence-gated)。
- Produces: `fetch_consensus(scan_dir, codes=None, window=30, cache_root=None) -> pd.DataFrame` → `consensus.csv`(列 `code,n_reports,eps_delta_pct`);`_inst_mark(base, code6) -> str`(简报行);CLI `l4_card consensus <date>`;L3 表 `rc` 列(如 `+5%`/空)。

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_l4_card_inst.py(新文件)
"""机构面契约:consensus.csv 生产(缓存薄→空表)+ _inst_mark presence-gated。"""
from pathlib import Path

import pandas as pd

from autoresearch.scan.agents.l4_card import _inst_mark, fetch_consensus


def _mk_cache(root: Path, stems: list[str], code="000001", eps=1.0):
    d = root / "report_rc"
    d.mkdir(parents=True)
    for i, s in enumerate(stems):
        pd.to_pickle(pd.DataFrame({"ts_code": [f"{code}.SZ"] * 2, "quarter": ["2026Q4"] * 2,
                                   "eps": [eps + i * 0.1] * 2}), d / f"{s}.pkl")


def test_fetch_consensus_and_mark(tmp_path):
    cache = tmp_path / "cache"
    stems = [f"202606{d:02d}" for d in range(1, 31)]      # 30 天缓存,EPS 逐日上修
    _mk_cache(cache, stems)
    sd = tmp_path / "2026-06-30"
    sd.mkdir()
    df = fetch_consensus(sd, codes=["000001"], cache_root=cache)
    assert (sd / "consensus.csv").exists()
    row = df[df["code"] == "000001"].iloc[0]
    assert row["n_reports"] > 0 and row["eps_delta_pct"] > 0      # 上修为正
    line = _inst_mark(sd, "000001")
    assert "机构面" in line and "修正" in line


def test_inst_mark_presence_gated(tmp_path):
    sd = tmp_path / "2026-06-30"
    sd.mkdir()
    assert _inst_mark(sd, "000001") == ""                  # 无 consensus.csv → 不加行


def test_fetch_consensus_thin_cache_empty(tmp_path):
    cache = tmp_path / "cache"
    _mk_cache(cache, ["20260628", "20260629"])             # <10 天 → 空表(禁注)
    sd = tmp_path / "2026-06-30"
    sd.mkdir()
    df = fetch_consensus(sd, codes=["000001"], cache_root=cache)
    assert df.empty and not (sd / "consensus.csv").exists()
```

- [ ] **Step 2: 跑测试确认失败** → `uv run --no-sync python -m pytest tests/scan/test_l4_card_inst.py -q`,Expected: ImportError/FAIL。

- [ ] **Step 3: 实现 `fetch_consensus` + `_inst_mark`(l4_card.py,模式 mirror `fetch_pledge`/`_cat_mark`)**

```python
def fetch_consensus(scan_dir: Path | str, codes=None, window: int = 30,
                    cache_root: Path | None = None) -> pd.DataFrame:
    """finalists 卖方一致预期修正 → `consensus.csv`(code,n_reports,eps_delta_pct)。零 LLM。

    从 report_rc 缓存(consensus.pull/backfill 积累)取分析日前最近 `window` 个缓存日,
    前后对半为两窗,算 FY 一致 EPS 中位修正(research/consensus.consensus_delta)。
    缓存日 <10 → 空表不落盘(样本太薄禁注,presence-gated)。advisory:不进分、不设门。
    """
    scan_dir = Path(scan_dir)
    date = scan_dir.name
    from autoresearch.research.consensus import _dir, _load_span, consensus_delta
    stems = sorted(p.stem for p in _dir(cache_root).glob("*.pkl")
                   if p.stem <= date.replace("-", ""))[-window:]
    if len(stems) < 10:
        return pd.DataFrame(columns=["code", "n_reports", "eps_delta_pct"])
    half = len(stems) // 2
    old_span, new_span = (stems[0], stems[half - 1]), (stems[half], stems[-1])
    fy = date[:4]
    delta = consensus_delta(date, old_span, new_span, fy, cache_root=cache_root)
    recent = _load_span(new_span, cache_root)
    recent["code"] = recent["ts_code"].astype(str).str[:6]
    n_rep = recent.groupby("code").size().rename("n_reports")
    out = delta.merge(n_rep, on="code", how="left")
    out["n_reports"] = out["n_reports"].fillna(0).astype(int)
    if codes is not None:
        want = {str(c).split(".")[0].zfill(6) for c in codes}
        out = out[out["code"].isin(want)]
    out = out[["code", "n_reports", "eps_delta_pct"]].reset_index(drop=True)
    if len(out):
        out.to_csv(scan_dir / "consensus.csv", index=False)
    return out


def _inst_mark(base: Path, code6: str) -> str:
    """机构面行(presence-gated:consensus.csv 在且该票有行才注)。advisory 事实,非方向。"""
    p = Path(base) / "consensus.csv"
    if not p.exists():
        return ""
    try:
        df = pd.read_csv(p, dtype={"code": str})
        df["code"] = df["code"].astype(str).str.zfill(6)
        sub = df[df["code"] == code6]
        if not len(sub):
            return ""
        r = sub.iloc[0]
        d = float(pd.to_numeric(pd.Series([r.get("eps_delta_pct")]), errors="coerce").iloc[0])
        n = int(r.get("n_reports") or 0)
    except Exception:  # noqa: BLE001 — 行可选,缺了不挡简报
        return ""
    if pd.isna(d) or n <= 0:
        return ""
    arrow = "上修" if d > 0 else ("下修" if d < 0 else "持平")
    return (f"- **机构面(卖方,近窗)**:研报 {n} 篇;FY 一致 EPS {arrow} {d:+.1f}%"
            f"(窗口对比,advisory 存在性≠方向;与资金/基本面共振才可作论点支柱)")
```

`compose_funnel_brief` 在 `cm = _cat_mark(...)` 块之后插:

```python
    im = _inst_mark(base, code6)             # 机构面:卖方修正(consensus.csv 在才注,presence-gated)
    if im:
        lines.append(im)
```

CLI:l4_card 的子命令分发处(grep `"pledge"` 定位 argparse/mode 分发)镜像加 `consensus` 子命令 → `fetch_consensus(scan_dir, codes=<finalists codes>)`(codes 读取姿势同 pledge 子命令)。
**基金重仓行(条件)**:读 Plan 1 Task 6 的探针裁决 `context/factor_lab/cache/probes/fund_portfolio_20260710.json`——裁决「可用」才在本任务内加 `fund_hold.csv` 生产 + `_inst_mark` 第二行(季度滞后恒 advisory);「不可用」则跳过并在 progress.md 记一行,**不写死代码**。

- [ ] **Step 4: L3 表 rc 列(镜像 cat 列)**

`l3_select.py` 里 grep `cat_flag`/`L3_catalyst` 找到 cat 列注入点,镜像加 presence-gated `rc` 列:读 `consensus.csv`,值 = `f"{eps_delta_pct:+.0f}%"`(无行→空串);表头列窄(≤6 字符)。l3 表测试补一条:有 consensus.csv 时 rc 列有值、无文件时列不出现(parity)。

- [ ] **Step 5: workflow 接线(生产者先于 prompts)**

`.claude/workflows/scan-market.js` l4-prep 命令串里 `calendar` 之后、`prompts` 之前插一段:

```javascript
  `${R} autoresearch.scan.agents.l4_card consensus ${date} || true; ` +
```

`node --check .claude/workflows/scan-market.js` 过。STAGES.md「派发前四道确定性闸」句的 ③ 改为「席位/催化/日历/**卖方修正(consensus)**生产者先行落稿」。

- [ ] **Step 6: 跑绿 + 全量回归** → `uv run --no-sync python -m pytest tests/scan/ -q && uv run --no-sync python -m ruff check . && uv run --no-sync python -m pytest tests/ -q`,Expected: 全绿(prefix 契约测试必须仍绿——机构面行在逐卡块,不碰共享前缀)。

- [ ] **Step 7: Commit**

```bash
git add autoresearch/scan/agents/l4_card.py autoresearch/scan/agents/l3_select.py .claude/workflows/scan-market.js .claude/skills/scan-market/STAGES.md tests/scan/
git commit -m "feat(scan): 机构面进卡(卖方修正生产者+_inst_mark+L3 rc列+workflow接线,presence-gated)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: L4 卡机构面有界 WebSearch 指令

**Files:**
- Modify: `.claude/agents/l4-card.md` + `.claude/skills/stock-research/lite-playbook.md`(双文件同步)
- Test: `tests/test_agent_defs.py`(锚列表加 `机构面网查`)

**Interfaces:** 纯 prompt 契约;token 上界 ≤2 条/卡且触发有条件(用户 2026-07-10 知情确认)。

- [ ] **Step 1: 锚测试先行**:anchors 列表再加 `"机构面网查"`,跑 → FAIL。
- [ ] **Step 2: 双文件铁律追加一条**(放「P3 活体新闻(有界)」之后):

```markdown
- **机构面网查(有界)**:仅当简报带「机构面」行、或你已进入 P4 时,可发 **≤2 条**定向 WebSearch(`<名称> 研报 评级 近1月` / `<名称> 机构调研`)。结果必落来源+日期(as-of≤分析日),只作旁证——不得替代简报数据行、不单独改评级、不越过 rubric 三门。与 P3 活体新闻的 ≤3 条**分开计数**(全卡网查硬上界 5 条)。
```

- [ ] **Step 3: 跑绿** → `uv run --no-sync python -m pytest tests/test_agent_defs.py -q`,Expected: PASS。
- [ ] **Step 4: Commit**

```bash
git add .claude/agents/l4-card.md .claude/skills/stock-research/lite-playbook.md tests/test_agent_defs.py
git commit -m "feat(scan): L4 卡机构面有界网查指令(≤2条·条件触发·与P3新闻分开计数)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: self_review lint 与早停行数契约同步 + 前缀契约复核

**Files:**
- Modify: `autoresearch/learning/self_review.py`(卡片契约 lint 若含行数/模板断言则同步 v3;`grep -n "35\|早停卡\|模板" autoresearch/learning/self_review.py` 定位)
- Test: `tests/learning/test_self_review.py`、`tests/scan/test_l4_prompt_cache_prefix.py`(只跑,不改断言)

**Interfaces:** lint 认识 v3 卡(标记行不算违规、行数上限 36);prefix 契约不动。

- [ ] **Step 1: 定位 lint 规则** → 上述 grep;若 lint 有「早停卡 ≤35 行」类检查,上限 +1 并允许标记行;若有模板节清单,加「卡契约 v3」为可选行。若 grep 无相关规则 → 本任务只跑复核,记 progress.md「lint 无行数断言,无需改」。
- [ ] **Step 2: 复核两契约**:`uv run --no-sync python -m pytest tests/learning/test_self_review.py tests/scan/test_l4_prompt_cache_prefix.py -v` → Expected: 全绿。
- [ ] **Step 3: 全量回归 + Commit**(若有代码改动):

```bash
git add autoresearch/learning/self_review.py tests/learning/
git commit -m "chore(learning): self_review lint 认识卡契约 v3(标记行/行数上限同步)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: 四类机构因子 T+2 尺重审(读数裁决,不自动接线)

**Files:**
- 产物: `.superpowers/sdd/progress.md` 读数记录 + (条件)`context/knowledge/proposals.jsonl` 新提案

**Interfaces:**
- Consumes: 波 1a 后的 `factor_lab eval`(主排序/半样本/十分位已是 fwd_2_oc 尺)。
- Produces: `lhb_inst_net / rz_ratio / rz_buy_intensity / block_premium / block_intensity / hk_ratio` 六行的 T+2 读数与过门裁决。**过门 = IC_h1/IC_h2 同号 ∧ |ICIR_fwd_2_oc| 排进前半 ∧ decile spread_t ≥ 2**(沿 consensus.py 门约定);过门者只落**接线提案**(open,人批),不在本计划改 L1 `_GROUPS`。

- [ ] **Step 1: 跑评估**

Run: `uv run --no-sync python -m autoresearch.research.factor_lab eval`
Expected: 打印按 `ICIR_fwd_2_oc` 降序的 IC 表 + 十分位表(落 `context/factor_lab/out/ic_table.csv`、`decile_table.csv`)。若成型日缓存过旧,先 `factor_lab harvest` 再 eval。

- [ ] **Step 2: 抽六因子行记档**

Run: `uv run --no-sync python -c "import pandas as pd;t=pd.read_csv('context/factor_lab/out/ic_table.csv');d=pd.read_csv('context/factor_lab/out/decile_table.csv');f=['lhb_inst_net','rz_ratio','rz_buy_intensity','block_premium','block_intensity','hk_ratio'];print(t[t.factor.isin(f)].to_string());print(d[d.factor.isin(f)].to_string())"`
把两表原文 + 逐因子过门裁决(✓/✗ + 一句依据)记 progress.md。**诚实预期**:慢信号大概率仍不过门——不过门 = 维持 L4 advisory 可见性(Task 4/波 1a 已给),不是白做。

- [ ] **Step 3: (条件)过门者落接线提案**

对过门因子 append proposals.jsonl 一条(status=open,kind=factor,diff_sketch=「scoring._GROUPS 加组 + tushare_source 线上取数端点 + calibrate 重算」),**留人批**;无过门者则 progress.md 记「全部未过门,重审闭环完成」。

---

### Task 8(条件): report_rc 60 日 IC 验门(`consensus ic` 子命令)

**前置判定:** `uv run --no-sync python -m autoresearch.research.consensus status` 的 `n_days ≥ 60`(靠计划 1 的 backfill/日拉积累)。**未满 → 本任务整体跳过**,progress.md 记当前 n_days 与预计成熟日,后续到期再执行本任务(计划文本留在此,不改)。

**Files:**
- Modify: `autoresearch/research/consensus.py`(加 `ic_check` + CLI mode `ic`)
- Test: `tests/research/test_consensus_ic.py`(新,合成缓存+注入价格)

**Interfaces:**
- Consumes: report_rc 缓存 ≥60 日;factor_lab daily 缓存价格(`fl._cache/load_price_pivots/forward_returns`)。
- Produces: `ic_check(window: int = 15, cache_root=None, price_fn=None) -> dict`(`{"n_days","ic_mean","icir","ic_h1","ic_h2","verdict"}`,verdict ∈ pass/fail/thin);CLI `consensus ic`。**pass 才落 L1 接线提案(人批);fail/thin → rc 列/机构面行维持 advisory。**

- [ ] **Step 1: 写失败测试**

```python
# tests/research/test_consensus_ic.py(新)
"""ic_check 契约:逐日 eps_delta vs fwd_2_oc 的 rank-IC;价格注入零网络;薄样本 → thin。"""
import pandas as pd

from autoresearch.research import consensus


def _cache(tmp_path, n_days=70, n_codes=40):
    d = tmp_path / "report_rc"
    d.mkdir(parents=True)
    stems = [f"2026{m:02d}{dd:02d}" for m in range(1, 4) for dd in range(1, 29)][:n_days]
    for i, s in enumerate(stems):
        rows = [{"ts_code": f"{c:06d}.SZ", "quarter": "2026Q4", "eps": 1.0 + (c % 7) * 0.01 * i}
                for c in range(n_codes)]
        pd.to_pickle(pd.DataFrame(rows), d / f"{s}.pkl")
    return stems


def test_ic_check_runs_and_verdicts(tmp_path):
    stems = _cache(tmp_path)

    def price_fn(day, codes):                        # 注入前向收益:与 eps 斜率同向 → IC 应为正
        return pd.Series([(int(c) % 7) * 0.001 for c in codes], index=codes)

    res = consensus.ic_check(cache_root=tmp_path, price_fn=price_fn)
    assert res["n_days"] >= 30 and res["ic_mean"] > 0
    assert res["verdict"] in ("pass", "fail", "thin")


def test_ic_check_thin(tmp_path):
    _cache(tmp_path, n_days=20)
    res = consensus.ic_check(cache_root=tmp_path, price_fn=lambda d, c: pd.Series(0.0, index=c))
    assert res["verdict"] == "thin"
```

- [ ] **Step 2: 跑测试确认失败** → Expected: `ic_check` 不存在。

- [ ] **Step 3: 实现 `ic_check`**

```python
def ic_check(window: int = 15, cache_root: Path | None = None, price_fn=None,
             min_days: int = 40) -> dict:
    """逐日「eps_delta(前 window 窗 vs 近 window 窗)vs fwd_2_oc」rank-IC → 60 日门裁决。

    price_fn(day8, codes)->Series 可注入(测试);默认走 factor_lab 缓存算真 fwd_2_oc。
    verdict:pass=两半同号 ∧ |ICIR|≥0.3 ∧ n_days≥min_days;n_days<min_days=thin;否则 fail。
    """
    stems = sorted(p.stem for p in _dir(cache_root).glob("*.pkl"))
    ics = []
    for i in range(2 * window, len(stems) - 3):      # 留 D+2 成熟余量
        d = stems[i]
        old = (stems[i - 2 * window], stems[i - window - 1])
        new = (stems[i - window], stems[i - 1])
        delta = consensus_delta(d, old, new, d[:4], cache_root=cache_root)
        if len(delta) < 30:
            continue
        delta = delta.dropna(subset=["eps_delta_pct"]).set_index("code")
        if price_fn is not None:
            fwd = price_fn(d, list(delta.index))
        else:
            import autoresearch.research.factor_lab as fl
            from autoresearch.data.tushare_source import _trade_days
            pro = fl._pro()
            P = _trade_days(pro, d, None) or []      # 若 helper 需 end 参数,取 d 往后 5 个交易日
            P = P[:4]
            if len(P) < 3:
                continue
            for day in P:
                fl._cache("daily", day, fl._fetch(pro, "daily", day))
            piv = fl.load_price_pivots(P)
            fwd = fl.forward_returns(piv, P, d, 3)["fwd_2_oc"]
        both = delta.join(fwd.rename("fwd"), how="inner").dropna()
        if len(both) < 30:
            continue
        ics.append(float(both["eps_delta_pct"].rank().corr(both["fwd"].rank())))
    n = len(ics)
    if not n:
        return {"n_days": 0, "verdict": "thin"}
    s = pd.Series(ics)
    h = n // 2
    icir = float(s.mean() / (s.std() + 1e-9))
    res = {"n_days": n, "ic_mean": round(float(s.mean()), 4), "icir": round(icir, 3),
           "ic_h1": round(float(s[:h].mean()), 4), "ic_h2": round(float(s[h:].mean()), 4)}
    if n < min_days:
        res["verdict"] = "thin"
    elif res["ic_h1"] * res["ic_h2"] > 0 and abs(icir) >= 0.3:
        res["verdict"] = "pass"
    else:
        res["verdict"] = "fail"
    return res
```

CLI mode 加 `"ic"` → `print(ic_check())`。注:`_trade_days` 的实参形态以真实签名为准(`grep -n "def _trade_days" autoresearch/data/tushare_source.py`),真跑分支若签名不匹配按实调整——测试走 price_fn 注入,不依赖该分支。

- [ ] **Step 4: 跑绿 + 真跑记档**

Run: `uv run --no-sync python -m pytest tests/research/ -q && uv run --no-sync python -m ruff check . && uv run --no-sync python -m pytest tests/ -q`
Run: `uv run --no-sync python -m autoresearch.research.consensus ic` → 结果 + verdict 记 progress.md;pass → 落 L1 接线提案(open,人批);fail → rc/机构面行维持 advisory(已值回票价)。

- [ ] **Step 5: Commit**

```bash
git add autoresearch/research/consensus.py tests/research/test_consensus_ic.py
git commit -m "feat(research): consensus ic 60日验门(eps修正 vs fwd_2_oc·两半稳+ICIR,pass才提接线)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: P4 冒烟 —— 单票端到端验 v3 卡 + 机构面渲染

**Files:** 无代码;真跑验收(spec §5 ②③)。

- [ ] **Step 1**: 挑最近一个有 finalists 的 scan 日(或今天新起一个只含 1-2 票的迷你目录),依序跑 `l4_card pledge/seats/consensus/prompts <date>`,cat `_l4_prompt_<code>.md` 确认:共享块在前、逐卡块含机构面行(有数据票)。
- [ ] **Step 2**: 派一张真 l4-card(单票,`subagent_type='l4-card'`)→ 检查卡片:标记行 `〔卡契约 v3·超短 1~2 日〕` 在、时间框架=1~2日、三档情景 1~2 日窗、网查(若触发)带来源+日期且 ≤2 条。
- [ ] **Step 3**: `parse_rating` 解析该卡无回归;progress.md 记冒烟结论。**下一次全量真实扫描 = 本波正式验收**(spec §5 全五条)。

---

## 执行顺序与依赖

Task 1(hi_2 校准)→ 2(模板)→ 3(复用门)→ 4(机构面)→ 5(网查)→ 6(lint/前缀复核)→ 7(重审裁决)→ 8(条件:report_rc 门)→ 9(冒烟)。Task 7/8 与 2-6 无代码耦合,可在 Task 1 后穿插;Task 9 必须最后。

## 不做(边界)

- 机构因子进 L1 `_GROUPS`/召回路 = **只出提案人批**,本计划不改线上打分。
- full 深研报告(stock-research full 档)语义不动。
- L5 报告叙事超短语气细调 → 看首次真跑样张再说(spec §7)。
