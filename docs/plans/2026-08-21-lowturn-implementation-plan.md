# 低位转强 —— 召回补腿 × L3 第三画像 × 出手线展示对齐 —— 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **状态(2026-08-21)**:批次 0/1/2a/G0/2b/3/4 **已全部实施**(分支 `feat/lowturn-recall-l3-picture`,19 commits,4075 绿 / 基线 4003)。Gate 0 裁决 = **PROCEED**(读数与三条必读结论见 `docs/research/2026-08-21-lowturn-precheck.md` §3)。**仅剩批次 5 活体验收**——需要一个真实扫描日,下次跑 scan-market 时逐条核。

**Goal:** 让漏斗能把「跌过、已站回均线、放量、主力转正」的低位转强票送到 L4/E6 候选池（而不是被 L3 硬约束整体翻掉），同时让 summary 的「0 买」段与 E6 相对 BUY 同源；每一步都先有确定性证据（Gate 0 零 LLM 回测）再改 LLM 输入。

**Architecture:** 八个批次严格顺序：0 展示层同源 → 1 数据腿（`common/turnup.py` 单一实现 + 60 日面板 + 10 新列）→ 2a 修 `lens_reversal_confirm` 的门 → **G0 前置证伪 + 用户裁决门** → 2b 重开通道 + 反转桶 + 恒空探针 + 配置/文档 → 3 L3 第三画像（旗 / pass1 强留 / 守卫⑥ / `l3-rank.md`）→ 4 双尺分账 → 5 活体验收。批内 task 顺序执行、逐 task 独立 commit、每 task 自带红→绿测试。

**Tech Stack:** Python 3（`uv run --no-sync`）+ pandas + pytest；配置 `.claude/skills/scan-market/scan_config.jsonc`（白名单 `autoresearch/scan/user_config.py`）；agent def `.claude/agents/l3-rank.md`（会话启动装载，改完下个 session 生效）。

**Spec:** `docs/specs/2026-08-21-lowturn-recall-l3-picture-display-design.md`（执行者必读；§10 裁决表 R1–R12 的默认项即本计划取值）

## Global Constraints

- 测试命令统一 `uv run --no-sync python -m pytest tests/ -q`（全量，动工前先跑一次取当日基线；08-19 读数 4003 passed）；单测 `uv run --no-sync python -m pytest <path>::<name> -v`。**`pytest | tail` 会吞退出码**，别管道。
- 配置三件套（C4）：任何新 config 键 = `user_config.py` 白名单 + 真实消费点 + `tests/scan/test_user_config.py`/`test_config_knobs.py` 测试锁；jsonc 里每键标【生效点】；`SKILL.md` 配置表同步一行。
- `.claude/**` 只在本开发会话改（C3）；`tests/test_agent_defs.py:110-119` 七个锚不得丢；改 `l3-rank.md` 后提醒用户：**下个 session 才生效**。
- **E6 不碰**（C5）：`relative_buy.py` 的打分/选择/硬门/`RULE_VERSION` 一字不改；本计划只读它的产物、只改报告渲染与账本渲染。
- **parity 纪律**：每个新 flag/新列默认关 = 逐字 parity（`l3_table_md(lowturn_flag=False)`、`LOWTURN_DEFAULTS["enabled"]=False`、`lookback=60` 下 20 日四列逐元素等于旧值）。任何「默认开」都只发生在 jsonc 里、且在 G0 之后。
- **变异探针家训**：每个新谓词/lint 至少做一次「删掉这段实现，对应测试必须变红」的手工变异（各 task 末步点名）。
- **Gate 0 是硬门**：未拿到 `docs/research/<日期>-lowturn-precheck.md` 的裁决（PROCEED / SPARSE→放宽一档 / STOP_P3）与用户确认前，批次 3（P3）不得动工；STOP_P3 时批次 2b 仍可做（通道只作 L2 多样性并继续账本），批次 3 整体跳过并在 spec §14 记负结果。
- 与 spec 的四处**有意偏离**（都是减少事实源，不是偷工）：① `above_ma20`/`ma5_gt_ma10` 由 `turnup` 的 60 日 close 面板算（同序列同口径，研究/生产一致），**不**改 `tushare_source._fetch_factors`；② `reversal_confirm` 的门只住在 `scoring.lens_reversal_confirm`（已是单一实现），`turnup` 不再复制一份谓词；③ 恒空探针恒 `warn`（`fail` 会触发 GATE4 阻断发布，探针职责是可见性不是停机），连续 ≥3 日在 detail 加 🔴 前缀；④ P4 的双尺观察报告放在 `research/lowturn_precheck.py --live`（只读报告），不改 `l3_audit_ledger`（那本账量的是 bench 篮，不是 finalist lane）。
- commit 信息风格照旧仓惯例（中文、`feat(scan):`/`fix(common):`/`docs(research):` 前缀），尾行 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。工作区里用户自己改的 `.claude/skills/scan-market/pinned.jsonc` **不要带进任何 commit**（逐文件 `git add`，不用 `git add -u`）。

---

## 文件地图（本计划新建 / 修改）

| 文件 | 职责 | 批次 |
|---|---|---|
| `autoresearch/scan/relative_facts.py`（新） | 相对 BUY 事实读模型 + 禁词常量（brief/summary 共用） | 0 |
| `autoresearch/scan/brief.py` | 改为 import 上述模块（parity） | 0 |
| `autoresearch/scan/market.py` | `render_funnel_readout` E6 分支 | 0 |
| `autoresearch/common/turnup.py`（新） | 面板因子 `panel_factors` + 画像谓词 `lowturn_flag/label/mask` + `LOWTURN_DEFAULTS` | 1 |
| `autoresearch/research/factor_lab.py` | `reversal_confirm_factors` 委托 `turnup` | 1 |
| `autoresearch/scan/frame.py` | `lookback` 60、20 日组窗口锁定、面板列挂载、B 级降级记账 | 1 |
| `autoresearch/scan/universe.py` / `scan/health.py` / `scan/artifacts.py` / `tests/scan/_synth_universe.py` | 10 新列入 `keep` / NaN 体检 / `l1_full` schema 1→2 / 合成帧 | 1 |
| `autoresearch/common/scoring.py` | `lens_reversal_confirm` ②③ 段修门 | 2a |
| `autoresearch/research/lowturn_precheck.py`（新） | Gate 0 回测 + `--live` 双尺观察报告 | G0 / 4 |
| `docs/research/<日期>-lowturn-precheck.md`（新）/ `docs/research/factor-backlog.md` | 先写假设与停机规则，后贴读数 | G0 |
| `autoresearch/scan/recall/l2_stratify.py` | `STYLE_CHANNELS["反转"]` 加 `reversal_confirm` | 2b |
| `autoresearch/learning/self_review.py` / `scan/report_sections.py` | `channel_liveness_lint` + 接线 | 2b |
| `.claude/skills/scan-market/scan_config.jsonc` / `STAGES.md` / `SKILL.md` | 重开通道、配额、文档 | 2b / 3 |
| `autoresearch/scan/user_config.py` | `l3.lowturn` 白名单 + 类型 | 3 |
| `autoresearch/scan/l3/prompt.py` / `l3/triage.py` / `l3/merge.py` | 旗列 + 强留 + 守卫⑥ | 3 |
| `.claude/agents/l3-rank.md` / `tests/test_agent_defs.py` | B 条例外 + G 条 + lane 枚举 + 锚 | 3 |
| `autoresearch/learning/relative_ledger.py` | 逐日表 `lane` 列 | 4 |

---

## 批次 0 · P0 展示层同源

### Task 0.1: 抽出相对 BUY 读模型 `scan/relative_facts.py`

**Files:**
- Create: `autoresearch/scan/relative_facts.py`
- Modify: `autoresearch/scan/brief.py:50`（import 行）、`:61-63`、`:110-112`（四个常量）、`:486-533`（`_relative_facts` 函数体）
- Test: `tests/scan/test_relative_facts.py`（新）

**Interfaces:**
- Produces: `relative_facts(decision: dict | None) -> dict`（字段与现 `brief._relative_facts` 逐键相同：`present/mode/rule_version/blocked/blocked_reasons/code/basis/name/rank/score/research_rating/n_eligible/n_candidates/market_column/decision_pool_n/eval_population/sector_column/n_sectors/abs_gap_status/abs_gap_value/abs_gap_n/hard_reject/ruler`）；常量 `BANNED_RELATIVE_PHRASES`、`WEAK_MARKET_PHRASE`、`REL_MARKET_POPULATION`、`DECISION_POOL_LABEL`。
- brief 对外属性 `brief.BANNED_RELATIVE_PHRASES` / `brief.WEAK_MARKET_PHRASE` / `brief.REL_MARKET_POPULATION` / `brief.DECISION_POOL_LABEL` / `brief._relative_facts` **保留同名**（`tests/scan/test_brief.py:226 test_semantic_constants_are_pinned_literals` 直接读它们）。

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_relative_facts.py
"""相对 BUY 事实读模型(brief ③ 与 summary 漏斗读数共用的唯一解析器;P0 低位转强波)。"""
from __future__ import annotations

from autoresearch.scan import brief, relative_facts as rf


def _doc(*, blocked=False):
    cands = [{"code": "002081", "name": "金螳螂", "sector": "装修装饰Ⅱ", "pinned": False,
              "eligible": True, "rank": 1, "relative_decision_score": 0.70,
              "faces": {}, "faces_missing": [], "research_rating": "Underweight",
              "expected_abs_gap": {"value": None, "status": "UNMEASURED", "n": 0}},
             {"code": "600211", "name": "西藏药业", "sector": "生物制品", "pinned": False,
              "eligible": False, "rank": None, "relative_decision_score": None,
              "faces": {}, "faces_missing": [], "research_rating": "Underweight",
              "expected_abs_gap": {"value": None, "status": "UNMEASURED", "n": 0}}]
    return {"mode": "active", "rule_version": "e6.v2.0", "ruler": "gap_c1_o2",
            "benchmark": {"market": {"column": "rel_gap_market", "n": 4276},
                          "sector": {"column": "rel_gap_sector", "n_sectors": 129}},
            "counts": {"candidates": 9, "eligible": 0 if blocked else 6},
            "candidates": cands,
            "buys": [] if blocked else [{"code": "002081", "basis": "relative", "rank": 1}],
            "blocked": blocked,
            "blocked_reasons": [{"reason": "hard_gate.no_redflag", "n": 9}] if blocked else []}


def test_missing_decision_is_explicitly_absent():
    assert rf.relative_facts(None) == {"present": False, "mode": None}


def test_buy_fields():
    out = rf.relative_facts(_doc())
    assert out["present"] and out["mode"] == "active" and not out["blocked"]
    assert out["code"] == "002081" and out["name"] == "金螳螂" and out["research_rating"] == "Underweight"
    assert out["rank"] == 1 and out["n_eligible"] == 6 and out["n_candidates"] == 9
    assert out["hard_reject"] == 1            # 不合格候选数
    assert out["abs_gap_status"] == "UNMEASURED" and out["ruler"] == "gap_c1_o2"


def test_blocked_fields():
    out = rf.relative_facts(_doc(blocked=True))
    assert out["blocked"] and out["blocked_reasons"] == ["hard_gate.no_redflag×9"]
    assert out["code"] is None


def test_brief_reexports_same_objects():
    """brief 不得再持有自己的一份常量/解析器 —— 同名属性必须是同一个对象(防两处各漂)。"""
    assert brief._relative_facts is rf.relative_facts
    assert brief.BANNED_RELATIVE_PHRASES is rf.BANNED_RELATIVE_PHRASES
    assert brief.WEAK_MARKET_PHRASE == rf.WEAK_MARKET_PHRASE == "弱市相对最优"
    assert brief.REL_MARKET_POPULATION is rf.REL_MARKET_POPULATION
    assert brief.DECISION_POOL_LABEL is rf.DECISION_POOL_LABEL
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_relative_facts.py -v`
Expected: FAIL（`ImportError: cannot import name 'relative_facts'`）

- [ ] **Step 3: 新建模块**

```python
# autoresearch/scan/relative_facts.py
#!/usr/bin/env python3
"""相对 BUY 事实读模型 —— brief ③ 与 summary「📉 今日漏斗读数」共用的**唯一**解析器。

2026-08-21 低位转强波 P0:此前 `_relative_facts` 只住在 brief.py,summary 侧 `market.py`
仍按旧绝对门(卡面 ≥OW)数买单并印「0 买·空仓观望」,E6 active 后两处在同一份报告里
打架(08-20 实跑:brief ③ ✅ relative BUY 金螳螂 vs summary「0 买…空仓观望」)。修法不是
再写一个解析器——brief↔summary 一起错一起绿的前科(memory
`brief-reads-provisional-relative-buy`)——而是把读模型抽到这里,两边 import 同一个函数、
同一张禁词表。本模块只依赖 `common.ruler`,不反向依赖 brief/market(防环)。
"""
from __future__ import annotations

from autoresearch.common.ruler import MAIN_RULER, REL_MARKET, REL_SECTOR

#: 相对 BUY 区禁词 —— 出现任意一个即语义越权(它从不承诺绝对方向)。
BANNED_RELATIVE_PHRASES = ("预计上涨", "预计绝对上涨", "预期上涨", "看涨", "必涨", "稳赚")
#: 绝对 gap 为负时**固定**用这句话,不许换措辞。
WEAK_MARKET_PHRASE = "弱市相对最优"
REL_MARKET_POPULATION = "全市场可交易"
DECISION_POOL_LABEL = "L0 可交易"


def relative_facts(decision: dict | None) -> dict:
    """`_relative_buy_decision.json` → ③ 影子/正式 BUY 行需要的字段。缺文件 → `present=False`
    (**显式说「未生成」**,不静默省行——静默会被读成「今天没有相对 BUY」)。"""
    if not isinstance(decision, dict):
        return {"present": False, "mode": None}
    bench = decision.get("benchmark") or {}
    market, sector = bench.get("market") or {}, bench.get("sector") or {}
    buys = decision.get("buys") or []
    by_code = {row.get("code"): row for row in (decision.get("candidates") or [])
               if isinstance(row, dict)}
    top = by_code.get(buys[0]["code"]) if buys else None
    gap = (top or {}).get("expected_abs_gap") or {}
    hard_reject = sum(1 for row in (decision.get("candidates") or [])
                      if isinstance(row, dict) and not row.get("eligible"))
    reasons = [f"{r.get('reason')}×{r.get('n')}"
               for r in (decision.get("blocked_reasons") or []) if isinstance(r, dict)]
    return {
        "present": True,
        "mode": decision.get("mode"),
        "rule_version": decision.get("rule_version"),
        "blocked": bool(decision.get("blocked")),
        "blocked_reasons": reasons,
        "code": (buys[0]["code"] if buys else None),
        "basis": (buys[0].get("basis") if buys else None),
        "name": (top or {}).get("name"),
        "rank": (top or {}).get("rank"),
        "score": (top or {}).get("relative_decision_score"),
        "research_rating": (top or {}).get("research_rating"),
        "n_eligible": (decision.get("counts") or {}).get("eligible"),
        "n_candidates": (decision.get("counts") or {}).get("candidates"),
        "market_column": market.get("column") or REL_MARKET,
        "decision_pool_n": market.get("n"),
        "eval_population": market.get("eval_population") or REL_MARKET_POPULATION,
        "sector_column": sector.get("column") or REL_SECTOR,
        "n_sectors": market.get("n_sectors") or sector.get("n_sectors"),
        "abs_gap_status": gap.get("status", "UNMEASURED"),
        "abs_gap_value": gap.get("value"),
        "abs_gap_n": gap.get("n", 0),
        "hard_reject": hard_reject,
        "ruler": decision.get("ruler") or MAIN_RULER,
    }
```

- [ ] **Step 4: brief.py 改为 import（三处）**

在 `brief.py:50` 的 `from autoresearch.scan.relative_buy import DECISION_FILENAME, MODE_SHADOW` 下面加：

```python
from autoresearch.scan.relative_facts import (  # P0 低位转强波:读模型/禁词单一事实源(summary 同源)
    BANNED_RELATIVE_PHRASES,
    DECISION_POOL_LABEL,
    REL_MARKET_POPULATION,
    WEAK_MARKET_PHRASE,
    relative_facts,
)
```

删除 `brief.py:61` `REL_MARKET_POPULATION = ...`、`:63` `DECISION_POOL_LABEL = ...`、`:110` `BANNED_RELATIVE_PHRASES = (...)`、`:112` `WEAK_MARKET_PHRASE = ...` 四行（保留它们上方的 `#:` 注释可一并删）。把 `:486-533` 整个 `def _relative_facts(...)` 函数体替换为一行别名：

```python
_relative_facts = relative_facts   # 真身在 scan/relative_facts.py(brief/summary 共用);保留旧名给内部调用点
```

- [ ] **Step 5: 跑测试**

Run: `uv run --no-sync python -m pytest tests/scan/test_relative_facts.py tests/scan/test_brief.py -q`
Expected: 全绿（brief 全量 parity：它的字节输出不变）

- [ ] **Step 6: Commit**

```bash
git add autoresearch/scan/relative_facts.py autoresearch/scan/brief.py tests/scan/test_relative_facts.py
git commit -m "refactor(scan): 相对 BUY 读模型与禁词抽到 relative_facts.py(brief/summary 单一事实源,parity)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 0.2: `render_funnel_readout` 的 E6 分支

**Files:**
- Modify: `autoresearch/scan/market.py:546-585`（`render_funnel_readout`；新增私有函数 `_e6_readout_line`）
- Test: `tests/scan/test_zero_buy_narrative.py`（追加）

**Interfaces:**
- Consumes: `relative_buy.is_active()`（config `relative_buy.mode=="active"`）、`relative_buy.load_decision(scan_dir)`（日期不符/缺席 → `None`）、`relative_facts.relative_facts`。
- Produces: E6 active 且当日决策文件在场时「📉 今日漏斗读数」首行改为 `- **研究评级 ≥OW N 只**(证据,非决策)· **相对 BUY 1 只**:<name> <code>(卡面 <rating>;basis=relative,只承诺「当日全集内相对最优」,不承诺绝对收益为正)—— 决策口径见 🧭 ③`；BLOCKED 时 `- **相对 BUY BLOCKED**(全部候选被硬资格否决:<reasons>)· **研究评级 ≥OW N 只**(证据,非决策)`；其余路径逐字 parity。

- [ ] **Step 1: 写失败测试（追加到 `tests/scan/test_zero_buy_narrative.py` 末尾）**

```python
# ───────────────────────── E6 active:决策口径同源 brief ③(P0 低位转强波) ─────────────────────────


def _decision_doc(*, blocked=False):
    cands = [{"code": "002081", "name": "金螳螂", "sector": "装修装饰Ⅱ", "pinned": False,
              "eligible": True, "rank": 1, "relative_decision_score": 0.70,
              "faces": {}, "faces_missing": [], "research_rating": "Underweight",
              "expected_abs_gap": {"value": None, "status": "UNMEASURED", "n": 0}}]
    return {"schema_version": 1, "rule_version": "e6.v2.0", "mode": "active", "date": "2026-07-25",
            "ruler": "gap_c1_o2",
            "benchmark": {"market": {"column": "rel_gap_market", "n": 4276},
                          "sector": {"column": "rel_gap_sector", "n_sectors": 129}},
            "counts": {"candidates": 9, "eligible": 0 if blocked else 6, "buys": 0 if blocked else 1},
            "candidates": cands,
            "buys": [] if blocked else [{"code": "002081", "basis": "relative", "rank": 1}],
            "blocked": blocked,
            "blocked_reasons": [{"reason": "hard_gate.no_redflag", "n": 9}] if blocked else []}


def _activate(monkeypatch, d, doc, *, active=True):
    import autoresearch.scan.relative_buy as rb
    monkeypatch.setattr(rb, "is_active", lambda: active)
    (d / rb.DECISION_FILENAME).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def test_e6_active_with_buy_prints_decision_not_idle(tmp_path, monkeypatch):
    """08-20 病灶:brief ③ ✅ relative BUY 金螳螂,summary 同段却印「0 买…空仓观望」。"""
    d = _day(tmp_path, {"000651": {"phase": "P3", "reason": "资金流出"},
                        "300857": {"phase": "P3", "reason": "涨停追高"}})
    _activate(monkeypatch, d, _decision_doc())
    out = render_funnel_readout(d)
    assert "相对 BUY 1 只" in out and "金螳螂 002081" in out and "卡面 Underweight" in out
    assert "研究评级 ≥OW 0 只" in out
    assert "空仓观望" not in out and "无一进买单" not in out
    assert "早停 2" in out                      # 「为什么没有 ≥OW」机制拆分仍在
    assert "日级弃权裁决: **IMMATURE**" in out


def test_e6_active_blocked_prints_blocked(tmp_path, monkeypatch):
    d = _day(tmp_path, {})
    _activate(monkeypatch, d, _decision_doc(blocked=True))
    out = render_funnel_readout(d)
    assert "相对 BUY BLOCKED" in out and "hard_gate.no_redflag×9" in out
    assert "空仓观望" not in out


def test_e6_line_has_no_banned_phrase(tmp_path, monkeypatch):
    from autoresearch.scan.relative_facts import BANNED_RELATIVE_PHRASES
    d = _day(tmp_path, {})
    _activate(monkeypatch, d, _decision_doc())
    out = render_funnel_readout(d)
    for banned in BANNED_RELATIVE_PHRASES:
        assert banned not in out
    assert "不承诺绝对" in out


def test_e6_inactive_or_stale_decision_is_byte_identical_legacy(tmp_path, monkeypatch):
    """shadow 期 / 决策文件过期(date 不符)→ 与无文件时逐字节相同(parity 锁)。"""
    d = _day(tmp_path, {})
    import autoresearch.scan.relative_buy as rb
    monkeypatch.setattr(rb, "is_active", lambda: False)
    baseline = render_funnel_readout(d)
    (d / rb.DECISION_FILENAME).write_text(json.dumps(_decision_doc()), encoding="utf-8")
    assert render_funnel_readout(d) == baseline            # shadow:文件在也不读
    monkeypatch.setattr(rb, "is_active", lambda: True)
    stale = dict(_decision_doc(), date="2026-07-24")        # 昨天的文件 → load_decision 判 None
    (d / rb.DECISION_FILENAME).write_text(json.dumps(stale), encoding="utf-8")
    assert render_funnel_readout(d) == baseline
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_zero_buy_narrative.py -v`
Expected: 新 4 条 FAIL（输出里没有「相对 BUY」），旧 3 条 PASS

- [ ] **Step 3: 实现**

在 `market.py` 的 `render_funnel_readout` 之前加：

```python
def _e6_readout_line(scan_dir: Path, *, n_ow: int) -> str | None:
    """E6 active 且当日决策文件在场 → 「研究评级(证据)· 相对 BUY(决策)」一行;否则 None(走 legacy)。

    读模型与 brief ③ 同源(`relative_facts`),措辞同受 `BANNED_RELATIVE_PHRASES` 约束(测试锁)。
    决策文件缺席/过期(`load_decision` 判 None)= 不能断言今天的 BUY → 诚实走 legacy 段,不编。
    """
    from autoresearch.scan.relative_buy import is_active, load_decision
    from autoresearch.scan.relative_facts import relative_facts

    if not is_active():
        return None
    doc = load_decision(scan_dir)
    rel = relative_facts(doc if isinstance(doc, dict) else None)
    if not rel.get("present") or rel.get("mode") != "active":
        return None
    evidence = f"**研究评级 ≥OW {n_ow} 只**(证据,非决策)"
    if rel.get("blocked"):
        why = "、".join(rel.get("blocked_reasons") or []) or "无分桶"
        return f"- **相对 BUY BLOCKED**(全部候选被硬资格否决:{why})· {evidence}"
    return (f"- {evidence} · **相对 BUY 1 只**:{rel.get('name') or '—'} {rel.get('code') or '—'}"
            f"(卡面 {rel.get('research_rating') or '—'};basis={rel.get('basis')},"
            f"只承诺「当日全集内相对最优」,不承诺绝对收益为正)—— 决策口径见 🧭 ③")
```

把 `render_funnel_readout` 里从 `buys = [...]` 到 `if downgraded:` 之前的段落改为：

```python
    buys = [c for c, r in final.items() if r in ("Buy", "Overweight")]
    lines = ["", "### 📉 今日漏斗读数"]
    e6_line = _e6_readout_line(scan_dir, n_ow=len(buys))
    if e6_line is not None:                 # E6 active:BUY 由决策文件独家拥有,研究评级只是证据
        lines.append(e6_line)
        if not buys:
            lines.append(f"  - 为什么没有 ≥OW 卡:{_zero_buy_mechanism(scan_dir, len(final))}")
            lines.append(f"  - {_abstention_verdict_line(scan_dir)}")
    elif buys:
        lines.append(f"- **{len(buys)} 买**(≥OW):{_names(scan_dir, buys)}")
    else:
        reg = (market_pack(scan_dir).get("regime") or {}).get("label")
        zh = _REGIME_ZH.get(reg, reg or "")
        lines.append(f"- **0 买**:{len(final)} 只 finalist 深核后无一进买单 —— "
                     f"{zh} regime 下当前采取空仓观望。")
        lines.append(f"  - 机制拆分:{_zero_buy_mechanism(scan_dir, len(final))}")
        lines.append(f"  - {_abstention_verdict_line(scan_dir)}")
```

（`elif buys:` 与 `else:` 两段是原文原样搬下来，一个字不改。）

- [ ] **Step 4: 跑测试**

Run: `uv run --no-sync python -m pytest tests/scan/test_zero_buy_narrative.py tests/scan/test_market_renderers.py tests/scan/test_assemble_slim0buy.py -q`
Expected: 全绿

- [ ] **Step 5: 变异探针（手工，不提交）**

把 `if not is_active(): return None` 临时改成 `if is_active(): return None`，跑 Step 4 → `test_e6_active_with_buy_prints_decision_not_idle` 必须红；改回。

- [ ] **Step 6: Commit**

```bash
git add autoresearch/scan/market.py tests/scan/test_zero_buy_narrative.py
git commit -m "feat(report): 漏斗读数段与 E6 相对 BUY 同源(active 期不再印「0 买·空仓观望」)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 批次 1 · P1 数据腿

### Task 1.1: `common/turnup.py` 面板因子（单一实现）

**Files:**
- Create: `autoresearch/common/turnup.py`
- Test: `tests/common/test_turnup.py`（新）

**Interfaces:**
- Produces: `panel_factors(piv: dict[str, pd.DataFrame], dates: list[str]) -> pd.DataFrame`（index=code，列 = `PANEL_COLS`）；`PANEL_COLS = ("vol_ratio_20","dist_low_60","dist_high_60","days_no_new_low","vol_ma5_prev","vol_ma20_prev","pct_5d","pct_20d","above_ma20","ma5_gt_ma10")`。`piv` 键 `close/amount/low` 必需，`high` 可选（缺 → `dist_high_60` 全 NaN）；`dates` 升序、全部 ≤D、`D = dates[-1]`。

- [ ] **Step 1: 写失败测试**

```python
# tests/common/test_turnup.py
"""低位转强单一实现:面板因子(镜像 factor_lab 三因子 + 七个新列)与画像谓词。合成,无网络。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from autoresearch.common import turnup


def _piv(rows: dict[str, list[float]], dates: list[str], *, amount=None, low=None, high=None):
    close = pd.DataFrame.from_dict(rows, orient="index", columns=dates, dtype=float)
    out = {"close": close,
           "amount": pd.DataFrame.from_dict(amount, orient="index", columns=dates, dtype=float)
           if amount else close.copy() * 0 + 1.0,
           "low": pd.DataFrame.from_dict(low, orient="index", columns=dates, dtype=float)
           if low else close.copy()}
    if high is not None:
        out["high"] = pd.DataFrame.from_dict(high, orient="index", columns=dates, dtype=float)
    return out


def test_vol_ratio_20_mirrors_factor_lab_definition():
    P = [f"e{i}" for i in range(1, 22)]
    amt = {"A": [1.0] * 20 + [3.0]}
    out = turnup.panel_factors(_piv({"A": [10.0] * 21}, P, amount=amt), P)
    assert np.isclose(out.loc["A", "vol_ratio_20"], 3.0 / ((19 + 3) / 20))


def test_dist_low_and_days_no_new_low_mirror_factor_lab():
    P = ["d1", "d2", "d3", "d4", "d5", "d6"]
    low = {"A": [10, 9, 9.5, 9.2, 9.0, 9.1], "B": [5, 6, 7, 8, 9, 10]}
    out = turnup.panel_factors(_piv(low, P, low=low), P)
    assert np.isclose(out.loc["A", "dist_low_60"], (9.1 / 9.0 - 1.0) * 100)
    assert out.loc["A", "days_no_new_low"] == 1 and out.loc["B", "days_no_new_low"] == 5
    out_d1 = turnup.panel_factors(_piv(low, P, low=low), P[:1])
    assert out_d1.loc["A", "days_no_new_low"] == 0


def test_dist_high_60_nonpositive_and_value():
    P = ["d1", "d2", "d3"]
    high = {"A": [20.0, 18.0, 16.0]}
    out = turnup.panel_factors(_piv({"A": [15.0, 14.0, 15.0]}, P, high=high), P)
    assert out.loc["A", "dist_high_60"] <= 0
    assert np.isclose(out.loc["A", "dist_high_60"], (15.0 / 20.0 - 1.0) * 100)


def test_dist_high_60_nan_without_high_pivot():
    P = ["d1", "d2"]
    out = turnup.panel_factors(_piv({"A": [1.0, 2.0]}, P), P)
    assert np.isnan(out.loc["A", "dist_high_60"])


def test_vol_ma_prev_excludes_D_and_needs_full_window():
    P = [f"d{i}" for i in range(1, 23)]                      # 22 日
    amt = {"A": [1.0] * 16 + [2.0] * 5 + [100.0]}             # 最后 5 个「前日」=2,D 日巨量 100
    out = turnup.panel_factors(_piv({"A": [10.0] * 22}, P, amount=amt), P)
    assert np.isclose(out.loc["A", "vol_ma5_prev"], 2.0)      # 不含 D 的 100
    assert np.isclose(out.loc["A", "vol_ma20_prev"], (15 * 1.0 + 5 * 2.0) / 20)
    short = turnup.panel_factors(_piv({"A": [10.0] * 5}, P[:5], amount={"A": [1.0] * 5}), P[:5])
    assert np.isnan(short.loc["A", "vol_ma5_prev"]) and np.isnan(short.loc["A", "vol_ma20_prev"])


def test_pct_5d_20d_values_and_short_window_nan():
    P = [f"d{i}" for i in range(1, 22)]
    close = {"A": [100.0] * 16 + [100.0, 101.0, 102.0, 103.0, 110.0]}
    out = turnup.panel_factors(_piv(close, P), P)
    assert np.isclose(out.loc["A", "pct_5d"], (110.0 / 100.0 - 1) * 100)   # close[D]/close[D-5]
    assert np.isclose(out.loc["A", "pct_20d"], (110.0 / 100.0 - 1) * 100)
    short = turnup.panel_factors(_piv({"A": [1.0, 2.0, 3.0]}, P[:3]), P[:3])
    assert np.isnan(short.loc["A", "pct_5d"])


def test_above_ma20_and_ma5_gt_ma10_from_same_close_panel():
    P = [f"d{i}" for i in range(1, 21)]
    rising = {"A": list(np.linspace(10, 20, 20))}             # 单边上涨:close>MA20,MA5>MA10
    falling = {"B": list(np.linspace(20, 10, 20))}
    out = turnup.panel_factors(_piv({**rising, **falling}, P), P)
    assert out.loc["A", "above_ma20"] == 1.0 and out.loc["A", "ma5_gt_ma10"] == 1.0
    assert out.loc["B", "above_ma20"] == 0.0 and out.loc["B", "ma5_gt_ma10"] == 0.0
    short = turnup.panel_factors(_piv({"A": [1.0] * 9}, P[:9]), P[:9])
    assert np.isnan(short.loc["A", "above_ma20"]) and np.isnan(short.loc["A", "ma5_gt_ma10"])


def test_panel_cols_contract_and_empty_dates_raise():
    P = ["d1", "d2"]
    out = turnup.panel_factors(_piv({"A": [1.0, 2.0]}, P), P)
    assert list(out.columns) == list(turnup.PANEL_COLS) and out.index.name == "code"
    with pytest.raises(ValueError):
        turnup.panel_factors(_piv({"A": [1.0, 2.0]}, P), [])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/common/test_turnup.py -v`
Expected: FAIL（`ModuleNotFoundError: autoresearch.common.turnup`）

- [ ] **Step 3: 实现**

```python
# autoresearch/common/turnup.py
#!/usr/bin/env python3
"""低位转强 —— 面板因子 + 画像谓词的**单一实现**(L1 帧装配 / factor_lab 研究 / L3 旗三处共用)。

design: docs/specs/2026-08-21-lowturn-recall-l3-picture-display-design.md §4.1 / §6.1。
仓库已两次为「两层各造一套词表」付过学费(`l2_stratify.py:39-52`、`l3/triage.py:8-10`),
所以这里只有一份数学,三个消费者 import。

面板因子(输入 = code×date pivot,列为交易日升序且全部 ≤D,D = dates[-1];严格无前视):
  vol_ratio_20       D 日成交额 / 近 20 日(含 D)均成交额(镜像 factor_lab.reversal_confirm_factors)
  dist_low_60        (close[D] / 60 日滚动最低 low − 1)×100,恒 ≥0
  dist_high_60       (close[D] / 60 日滚动最高 high − 1)×100,恒 ≤0(「跌过」的广义判据;缺 high → NaN)
  days_no_new_low    截至 D 连续未创 60 日新低天数(D 当日创新低 → 0)
  vol_ma5_prev / vol_ma20_prev   截止 **D−1** 的 5/20 日均成交额(起爆前是否缩量;不含 D;窗口不足 → NaN)
  pct_5d / pct_20d   close[D]/close[D−5]−1、close[D]/close[D−20]−1(%;窗口不足 → NaN)
  above_ma20 / ma5_gt_ma10   由**同一 close 面板**算的 MA20 / MA5 / MA10(同序列同口径,不与
                     tushare 复权 MA 混用;窗口不足 → NaN)。

画像谓词 `lowturn_flag(row, cfg)`:低位 ∧ 转强 ∧ 放量 ∧ 资金 ∧ ¬健康上涨 ∧ ¬落刀 ∧ 非 ST/退,
阈值来自 `LOWTURN_DEFAULTS`(jsonc `l3.lowturn` 覆盖);与 `reversal_confirm` 通道门
(`scoring.lens_reversal_confirm`,仍是那里的单一实现)是**同一组列的两档**,不是两套词表。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

PANEL_COLS = ("vol_ratio_20", "dist_low_60", "dist_high_60", "days_no_new_low",
              "vol_ma5_prev", "vol_ma20_prev", "pct_5d", "pct_20d", "above_ma20", "ma5_gt_ma10")


def _cols(piv: pd.DataFrame, dates: list[str]) -> pd.DataFrame:
    return piv.reindex(columns=dates)


def panel_factors(piv: dict, dates: list[str]) -> pd.DataFrame:
    dates = list(dates)
    if not dates:
        raise ValueError("panel_factors: dates 为空")
    D = dates[-1]
    C, A, L = piv["close"], piv["amount"], piv["low"]
    H = piv.get("high")
    codes = C.index
    out = pd.DataFrame(index=codes)
    out.index.name = "code"
    close_d = _cols(C, [D]).iloc[:, 0]

    win20 = dates[-20:]
    denom20 = _cols(A, win20).mean(axis=1).replace(0, np.nan)
    out["vol_ratio_20"] = _cols(A, [D]).iloc[:, 0] / denom20

    low_hist = _cols(L, dates)
    roll_min60 = low_hist.T.rolling(60, min_periods=1).min().T
    out["dist_low_60"] = (close_d / roll_min60[D] - 1.0) * 100

    if H is not None:
        roll_max60 = _cols(H, dates).T.rolling(60, min_periods=1).max().T
        out["dist_high_60"] = (close_d / roll_max60[D] - 1.0) * 100
    else:
        out["dist_high_60"] = np.nan

    is_new_low = (low_hist <= roll_min60 + 1e-9).to_numpy()
    out["days_no_new_low"] = is_new_low[:, ::-1].argmax(axis=1).astype(float)

    nan = pd.Series(np.nan, index=codes)
    out["vol_ma5_prev"] = _cols(A, dates[-6:-1]).mean(axis=1) if len(dates) >= 6 else nan
    out["vol_ma20_prev"] = _cols(A, dates[-21:-1]).mean(axis=1) if len(dates) >= 21 else nan

    def _lag_pct(k: int) -> pd.Series:
        if len(dates) < k + 1:
            return nan
        return (close_d / _cols(C, [dates[-(k + 1)]]).iloc[:, 0] - 1.0) * 100

    out["pct_5d"] = _lag_pct(5)
    out["pct_20d"] = _lag_pct(20)

    def _ma(k: int) -> pd.Series:
        return _cols(C, dates[-k:]).mean(axis=1) if len(dates) >= k else nan

    ma5, ma10, ma20 = _ma(5), _ma(10), _ma(20)
    out["above_ma20"] = (close_d > ma20).where(ma20.notna() & close_d.notna()).astype(float)
    out["ma5_gt_ma10"] = (ma5 > ma10).where(ma5.notna() & ma10.notna()).astype(float)
    return out[list(PANEL_COLS)]
```

- [ ] **Step 4: 跑测试**

Run: `uv run --no-sync python -m pytest tests/common/test_turnup.py -v`
Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add autoresearch/common/turnup.py tests/common/test_turnup.py
git commit -m "feat(common): turnup 面板因子单一实现(vol_ratio_20/dist_low_60/dist_high_60/…/above_ma20)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 1.2: `factor_lab.reversal_confirm_factors` 委托 `turnup`（等值锁先行）

**Files:**
- Modify: `autoresearch/research/factor_lab.py:336-376`
- Test: `tests/research/test_factor_lab.py`（追加等值测试；现有 `:269-320` 四条须保持绿）

- [ ] **Step 1: 写等值测试（旧实现逐字复制进测试当对照）**

```python
# 追加到 tests/research/test_factor_lab.py 末尾
def _legacy_reversal_confirm_factors(piv: dict, P: list[str], D: str) -> pd.DataFrame:
    """2026-08-21 前的实现,逐字复制作对照(委托 turnup 后数值不得漂移)。"""
    idx = P.index(D)
    A, L, C = piv["amount"], piv["low"], piv["close"]
    codes = C.index
    out = pd.DataFrame(index=codes)
    win20 = P[max(0, idx - 19):idx + 1]
    denom20 = A.reindex(columns=win20).mean(axis=1).replace(0, np.nan)
    amtD = A.reindex(columns=[D]).iloc[:, 0]
    out["vol_ratio_20"] = amtD / denom20
    hist = P[:idx + 1]
    low_hist = L.reindex(columns=hist)
    roll_min60 = low_hist.T.rolling(60, min_periods=1).min().T
    low60_D = roll_min60.reindex(columns=[D]).iloc[:, 0]
    closeD = C.reindex(columns=[D]).iloc[:, 0]
    out["dist_low_60"] = (closeD / low60_D - 1.0) * 100
    is_new_low = (low_hist <= roll_min60 + 1e-9).to_numpy()
    rev = is_new_low[:, ::-1]
    out["days_no_new_low"] = rev.argmax(axis=1).astype(float)
    return out


def test_reversal_confirm_factors_delegation_is_value_identical():
    """委托 turnup.panel_factors 后三因子逐元素等于旧实现(含 NaN 位置;80 日 × 30 码随机面板)。"""
    rng = np.random.default_rng(7)
    P = [f"{20260101 + i}" for i in range(80)]
    codes = [f"{600000 + i:06d}" for i in range(30)]
    close = pd.DataFrame(rng.uniform(5, 50, (30, 80)), index=codes, columns=P)
    low = close * rng.uniform(0.95, 1.0, (30, 80))
    amount = pd.DataFrame(rng.uniform(0, 1e6, (30, 80)), index=codes, columns=P)
    amount.iloc[3, 70:] = np.nan                     # 缺值位置也要一致
    piv = {"close": close, "low": low, "amount": amount}
    for D in (P[0], P[19], P[59], P[79]):
        old = _legacy_reversal_confirm_factors(piv, P, D)
        new = fl.reversal_confirm_factors(piv, P, D)
        pd.testing.assert_frame_equal(new[old.columns], old, check_names=False)
```

- [ ] **Step 2: 跑测试确认（此时两者都是旧实现 → 绿；这是基线）**

Run: `uv run --no-sync python -m pytest tests/research/test_factor_lab.py -q -k reversal_confirm`
Expected: 全绿

- [ ] **Step 3: 改为委托**

把 `factor_lab.py:336-376` 的函数体替换为：

```python
def reversal_confirm_factors(piv: dict, P: list[str], D: str) -> pd.DataFrame:
    """反转确认三因子(Plan A1-T2):`vol_ratio_20` / `dist_low_60` / `days_no_new_low`。

    2026-08-21 起**委托 `common.turnup.panel_factors`**(L1 帧、研究面板、L3 旗共用的单一实现);
    定义与 2026-07-11 原实现逐元素相同(`tests/research/test_factor_lab.py::
    test_reversal_confirm_factors_delegation_is_value_identical` 锁)。窗口严格 ≤D(无前视)。
    """
    from autoresearch.common import turnup
    idx = P.index(D)
    sub = {k: piv[k] for k in ("high", "low", "close", "amount") if k in piv}
    out = turnup.panel_factors(sub, P[:idx + 1])
    return out[["vol_ratio_20", "dist_low_60", "days_no_new_low"]]
```

- [ ] **Step 4: 跑测试**

Run: `uv run --no-sync python -m pytest tests/research/test_factor_lab.py -q`
Expected: 全绿（等值锁 + 原四条）

- [ ] **Step 5: Commit**

```bash
git add autoresearch/research/factor_lab.py tests/research/test_factor_lab.py
git commit -m "refactor(research): reversal_confirm_factors 委托 turnup(等值锁,研究读数不漂移)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 1.3: `turnup.lowturn_flag` 画像谓词

**Files:**
- Modify: `autoresearch/common/turnup.py`（追加）
- Test: `tests/common/test_turnup.py`（追加）

**Interfaces:**
- Produces: `LOWTURN_DEFAULTS: dict`、`LOWTURN_LABEL = "转强"`、`lowturn_flag(row, cfg=None) -> bool`、`lowturn_label(row, cfg=None) -> str`、`lowturn_mask(frame, cfg=None) -> pd.Series[bool]`。`row` 支持 `dict` 与 `pd.Series`（`.get`）。需要的行字段：`dist_high_60, pct_60d, pct_5d, above_ma20, ma5_gt_ma10, vol_ratio_20, main_inflow_yi, cmf_20, main_net_ratio`，可选 `name`。任一必需字段缺/NaN → `False`（不冤枉也不放行）。

- [ ] **Step 1: 写失败测试（追加）**

```python
# ───────────────────────── lowturn 画像谓词 ─────────────────────────


def _lt_row(**kw):
    base = {"name": "甲", "dist_high_60": -22.0, "pct_60d": -12.0, "pct_5d": 4.0,
            "above_ma20": 1.0, "ma5_gt_ma10": 1.0, "vol_ratio_20": 1.6,
            "main_inflow_yi": 0.8, "cmf_20": 0.05, "main_net_ratio": 0.03}
    base.update(kw)
    return base


def test_lowturn_perfect_row_is_flagged():
    assert turnup.lowturn_flag(_lt_row()) is True
    assert turnup.lowturn_label(_lt_row()) == "转强"


@pytest.mark.parametrize("kw", [
    {"dist_high_60": -8.0},            # 离高点太近 = 没跌过
    {"pct_60d": 12.0},                 # 60 日已涨回去
    {"pct_5d": -1.0},                  # 近 5 日不是正
    {"above_ma20": 0.0},               # 没站回 MA20
    {"ma5_gt_ma10": 0.0},              # 短均线没拐头
    {"vol_ratio_20": 1.1},             # 没放量
    {"main_inflow_yi": -0.2, "cmf_20": -0.01},   # 资金两腿皆负
    {"pct_60d": -40.0, "main_inflow_yi": 0.0, "cmf_20": 0.1},   # 落刀:深跌且无主力
    {"name": "ST甲"}, {"name": "甲退"},
    {"vol_ratio_20": float("nan")}, {"dist_high_60": None},
])
def test_lowturn_single_violation_unflags(kw):
    assert turnup.lowturn_flag(_lt_row(**kw)) is False


def test_lowturn_excludes_healthy_riser():
    """与健康上涨互斥(分账干净):0<pct_60d<40 ∧ main_net_ratio>0 ∧ cmf_20>0 的票归 healthy。"""
    row = _lt_row(pct_60d=5.0, dist_high_60=-16.0)            # 60 日正、主力占比正、cmf 正 = 健康上涨
    assert turnup.lowturn_flag(row) is False
    assert turnup.lowturn_flag(_lt_row(pct_60d=5.0, dist_high_60=-16.0, main_net_ratio=-0.01)) is True


def test_lowturn_thresholds_come_from_cfg():
    assert turnup.lowturn_flag(_lt_row(vol_ratio_20=1.1), {"min_vol_ratio_20": 1.0}) is True
    assert turnup.lowturn_flag(_lt_row(above_ma20=0.0), {"require_above_ma20": False}) is True
    assert turnup.lowturn_flag(_lt_row(main_inflow_yi=-1.0), {"fund": "cmf"}) is True
    assert turnup.lowturn_flag(_lt_row(cmf_20=-1.0), {"fund": "main"}) is True
    with pytest.raises(ValueError):
        turnup.lowturn_flag(_lt_row(), {"fund": "bogus"})


def test_lowturn_mask_matches_rowwise_and_healthy_agrees_with_scoring():
    from autoresearch.common.scoring import healthy_riser_mask
    from tests.scan._synth_universe import synth_universe
    df = synth_universe(n=300, seed=5)
    rng = np.random.default_rng(5)
    df["dist_high_60"] = rng.uniform(-60, 0, len(df))
    df["pct_5d"] = rng.uniform(-10, 10, len(df))
    df["above_ma20"] = rng.integers(0, 2, len(df)).astype(float)
    df["ma5_gt_ma10"] = rng.integers(0, 2, len(df)).astype(float)
    df["vol_ratio_20"] = rng.uniform(0.3, 3, len(df))
    mask = turnup.lowturn_mask(df)
    assert mask.dtype == bool and len(mask) == len(df)
    assert mask.tolist() == [turnup.lowturn_flag(r) for _, r in df.iterrows()]
    healthy = healthy_riser_mask(df)
    assert not (mask & healthy).any()                         # 互斥
    rowwise = pd.Series([turnup._is_healthy_row(r) for _, r in df.iterrows()], index=df.index)
    assert rowwise.equals(healthy.astype(bool))               # 行级判定与 scoring 同阈值
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/common/test_turnup.py -v -k lowturn`
Expected: FAIL（`AttributeError: lowturn_flag`）

- [ ] **Step 3: 实现（追加到 `turnup.py`）**

```python
# ───────────────────────── 画像谓词:低位转强 ─────────────────────────

#: 代码内建默认;jsonc `l3.lowturn` 覆盖(白名单见 scan/user_config.py)。`enabled` 内建 False = parity。
LOWTURN_DEFAULTS: dict = {
    "enabled": False,
    "max_dist_high_60": -15.0,     # 低位:距 60 日高 ≥15%(仍在水下)
    "max_pct_60d": 10.0,           #   且 60 日涨幅 <10(没涨回去)
    "min_vol_ratio_20": 1.2,       # 放量(通道硬门 1.5 的放宽档;L3 还有 agent 复核)
    "min_pct_5d": 0.0,             # 近 5 日为正(转强)
    "require_above_ma20": True,    # 站回 MA20
    "require_ma5_gt_ma10": True,   # 短均线拐头
    "fund": "main_or_cmf",         # 主力净额>0 或 cmf_20>0(资金转正);可选 main | cmf
    "knife_pct_60d": -35.0,        # 落刀:60 日跌超此值且主力不为正 → 不接
    "pass1_cap": 8,                # L3 pass1 强留上限(保护 40 席预算;消费点 l3/triage.py)
}
LOWTURN_LABEL = "转强"
_FUND_RULES = ("main_or_cmf", "main", "cmf")


def _f(row, key: str) -> float | None:
    v = row.get(key) if hasattr(row, "get") else None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if v != v else v          # NaN → None


def _is_healthy_row(row) -> bool:
    """健康上涨(行级)—— 与 `scoring.healthy_riser_mask` **同阈值**:0<pct_60d<40 ∧ main_net_ratio>0 ∧ cmf_20>0。
    (测试锁:tests/common/test_turnup.py 对随机帧逐行比对两者相等。)"""
    p, m, c = _f(row, "pct_60d"), _f(row, "main_net_ratio"), _f(row, "cmf_20")
    return p is not None and m is not None and c is not None and 0 < p < 40 and m > 0 and c > 0


def lowturn_flag(row, cfg: dict | None = None) -> bool:
    """低位转强 = 低位 ∧ 转强 ∧ 放量 ∧ 资金 ∧ ¬健康上涨 ∧ ¬落刀 ∧ 非 ST/退。必需字段缺/NaN → False。"""
    c = {**LOWTURN_DEFAULTS, **(cfg or {})}
    if c["fund"] not in _FUND_RULES:
        raise ValueError(f"lowturn.fund 未知取值 {c['fund']!r}(可选 {'|'.join(_FUND_RULES)})")
    name = str(row.get("name") or "") if hasattr(row, "get") else ""
    if "ST" in name.upper() or "退" in name:
        return False
    dh, p60 = _f(row, "dist_high_60"), _f(row, "pct_60d")
    if dh is None or p60 is None or dh > c["max_dist_high_60"] or p60 >= c["max_pct_60d"]:
        return False                                            # 低位:跌过且没涨回去
    inflow, cmf = _f(row, "main_inflow_yi"), _f(row, "cmf_20")
    if p60 < c["knife_pct_60d"] and (inflow is None or inflow <= 0):
        return False                                            # 落刀:深跌且无主力,不接
    p5 = _f(row, "pct_5d")
    if p5 is None or p5 <= c["min_pct_5d"]:
        return False                                            # 转强:近 5 日为正
    if c["require_above_ma20"] and (_f(row, "above_ma20") or 0.0) <= 0:
        return False
    if c["require_ma5_gt_ma10"] and (_f(row, "ma5_gt_ma10") or 0.0) <= 0:
        return False
    vr = _f(row, "vol_ratio_20")
    if vr is None or vr < c["min_vol_ratio_20"]:
        return False                                            # 放量
    fund_ok = {"main_or_cmf": (inflow or 0.0) > 0 or (cmf or 0.0) > 0,
               "main": (inflow or 0.0) > 0,
               "cmf": (cmf or 0.0) > 0}[c["fund"]]
    if not fund_ok:
        return False                                            # 资金转正
    return not _is_healthy_row(row)                             # 与健康上涨互斥(分账干净)


def lowturn_label(row, cfg: dict | None = None) -> str:
    return LOWTURN_LABEL if lowturn_flag(row, cfg) else ""


def lowturn_mask(frame: pd.DataFrame, cfg: dict | None = None) -> pd.Series:
    """帧级旗(pass1 强留 / 回测用);空帧 → 空 bool Series。"""
    if frame is None or not len(frame):
        return pd.Series(dtype=bool)
    return frame.apply(lambda r: lowturn_flag(r, cfg), axis=1).astype(bool)
```

- [ ] **Step 4: 跑测试**

Run: `uv run --no-sync python -m pytest tests/common/test_turnup.py -v`
Expected: 全绿

- [ ] **Step 5: 变异探针（手工）**

临时删掉 `if c["require_above_ma20"] ...: return False` 两行 → `test_lowturn_single_violation_unflags[kw5]`（above_ma20=0）必须红；恢复。

- [ ] **Step 6: Commit**

```bash
git add autoresearch/common/turnup.py tests/common/test_turnup.py
git commit -m "feat(common): lowturn 低位转强画像谓词(阈值可配,与 healthy 互斥,NaN 不放行)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 1.4: `frame._harvest_vol_series` 60 日面板 + 20 日组等值锁 + B 级降级

**Files:**
- Modify: `autoresearch/scan/frame.py:44-117`
- Test: `tests/scan/test_frame.py`（追加）

**Interfaces:**
- `_harvest_vol_series(codes, analysis_date, lookback: int = _PANEL_LOOKBACK)`，`_PANEL_LOOKBACK = 60`、`_TURNUP_MIN_DAYS = 40`、`_VOL_MIN_DAYS = 10` 不变。返回帧在原四列之外追加 `turnup.PANEL_COLS`；面板 <40 日 → 十列全 NaN 且 `contracts.record_degradation("daily", ..., key="turnup_panel")`。
- 现有 `tests/scan/test_frame.py:30` 的 monkeypatch `lambda codes, d, lookback=20: ...` 仍兼容（kwarg 名不变）。

- [ ] **Step 1: 写失败测试（追加到 `tests/scan/test_frame.py`）**

```python
# ───────────────────────── _harvest_vol_series:60 日面板(P1 低位转强波) ─────────────────────────


def _fake_daily_world(monkeypatch, n_days: int, n_codes: int = 8, seed: int = 0):
    """伪造 tushare 日历 + 湖:`get_or_fetch('daily', {'trade_date': d})` 返回当日全市场日线。"""
    import numpy as np

    rng = np.random.default_rng(seed)
    days = [f"{20260301 + i:08d}" for i in range(n_days)]          # 字符串单调即可,不必真日历
    codes = [f"{600000 + i:06d}" for i in range(n_codes)]
    world = {}
    for d in days:
        close = rng.uniform(5, 50, n_codes)
        world[d] = pd.DataFrame({"ts_code": [f"{c}.SH" for c in codes], "trade_date": d,
                                 "high": close * 1.02, "low": close * 0.98, "close": close,
                                 "amount": rng.uniform(1e4, 1e6, n_codes)})
    monkeypatch.setattr("autoresearch.data.tushare_source._pro", lambda: object(), raising=True)
    monkeypatch.setattr("autoresearch.data.tushare_source.resolve_momentum_dates",
                        lambda pro, d: (days[-1], days[0], days[0]), raising=True)
    monkeypatch.setattr("autoresearch.data.tushare_source._trade_days",
                        lambda pro, start, last: days, raising=True)
    monkeypatch.setattr("autoresearch.data.cache.get_or_fetch",
                        lambda endpoint, params, today=None: world[params["trade_date"]].copy(),
                        raising=True)
    return codes


def test_harvest_vol_series_lookback60_keeps_20d_factors_identical(monkeypatch):
    """lookback 20→60 后,cmf_20/obv_mom_20/price_vs_vwap_20/breakout_vol_20 逐元素不变(byte 契约),
    且追加 turnup.PANEL_COLS 十列。"""
    from autoresearch.common import turnup
    from autoresearch.data import contracts

    codes = _fake_daily_world(monkeypatch, n_days=70)
    contracts.clear_degradations()
    a = scan_frame._harvest_vol_series(codes, "2026-05-20", lookback=60).set_index("code")
    b = scan_frame._harvest_vol_series(codes, "2026-05-20", lookback=20).set_index("code")
    for col in ("cmf_20", "obv_mom_20", "price_vs_vwap_20", "breakout_vol_20"):
        pd.testing.assert_series_equal(a[col], b[col], check_names=False)
    assert set(turnup.PANEL_COLS) <= set(a.columns)
    assert a["vol_ratio_20"].notna().all() and a["above_ma20"].isin([0.0, 1.0]).all()
    assert scan_frame._PANEL_LOOKBACK == 60 and scan_frame._harvest_vol_series.__defaults__[0] == 60


def test_harvest_vol_series_short_panel_degrades_not_raises(monkeypatch):
    """面板 <_TURNUP_MIN_DAYS:十列整列 NaN + B 级降级记账(key=turnup_panel),不抛、四个 20 日列照算。"""
    from autoresearch.common import turnup
    from autoresearch.data import contracts

    codes = _fake_daily_world(monkeypatch, n_days=25)
    contracts.clear_degradations()
    out = scan_frame._harvest_vol_series(codes, "2026-05-20", lookback=60)
    assert out["cmf_20"].notna().any()
    assert out[list(turnup.PANEL_COLS)].isna().all().all()
    keys = [d.get("key") for d in contracts.degradations()]
    assert "turnup_panel" in keys
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_frame.py -v -k harvest_vol_series`
Expected: 第一条 FAIL（无 `_PANEL_LOOKBACK` / 无十列），第二条 FAIL（无降级记录）

- [ ] **Step 3: 实现**

`frame.py:44` 附近常量改为：

```python
_VOL_MIN_DAYS = 10          # 少于 10 个交易日算不出 20 日 CMF/OBV —— 与原 `len(days) < 10` 同阈值
_PANEL_LOOKBACK = 60        # 2026-08-21 低位转强波:20→60。20 日组**仍只喂最后 20 列**(等值锁见 tests/scan/test_frame.py)
_TURNUP_MIN_DAYS = 40       # 低位转强面板列(turnup.PANEL_COLS)的 B 级底线:不足 → 整列 NaN + record_degradation,不阻断
```

函数签名 `def _harvest_vol_series(codes, analysis_date: str, lookback: int = _PANEL_LOOKBACK) -> pd.DataFrame:`，docstring 首段追加一句「2026-08-21 起拉 60 日：前四列窗口仍是最后 20 日（逐元素不变），另算 `turnup.PANEL_COLS`（B 级，<40 日降级不阻断）」。函数末尾 `win = sorted(...)` 起替换为：

```python
    long = pd.concat(recs, ignore_index=True)
    piv = {f: long.pivot_table(index="code", columns="date", values=f)
           for f in ("high", "low", "close", "amount")}
    win = sorted(piv["close"].columns)
    win20 = win[-20:]                                   # 20 日组窗口不变(byte-identical 契约)
    H, L, C, A = (piv[f][win20] for f in ("high", "low", "close", "amount"))
    out = pd.DataFrame({"code": list(C.index)})
    out["cmf_20"] = vol_series.cmf(H, L, C, A, win20).to_numpy()
    out["obv_mom_20"] = vol_series.obv_momentum(C, A, win20).to_numpy()
    out["price_vs_vwap_20"] = vol_series.price_vs_vwap(H, L, C, A, win20).to_numpy()
    out["breakout_vol_20"] = vol_series.breakout_on_volume(C, A, win20).to_numpy()
    from autoresearch.common import turnup
    if len(win) >= _TURNUP_MIN_DAYS:                    # 低位转强面板列(B 级增强,不进 A 级出帧契约)
        tp = turnup.panel_factors(piv, win)
        out = out.merge(tp, left_on="code", right_index=True, how="left")
    else:
        from autoresearch.data.contracts import record_degradation
        record_degradation("daily", f"低位转强面板仅 {len(win)} 个交易日(<{_TURNUP_MIN_DAYS}),"
                           f"{'/'.join(turnup.PANEL_COLS[:3])}… 整列缺省(B 级,不阻断)", key="turnup_panel")
        for c in turnup.PANEL_COLS:
            out[c] = np.nan
    return out
```

（`frame.py` 顶部若无 `import numpy as np` 则加。）

- [ ] **Step 4: 跑测试**

Run: `uv run --no-sync python -m pytest tests/scan/test_frame.py tests/scan/test_parity.py -q`
Expected: 全绿（parity 测试 mock 掉 `_harvest_vol_series`，不受影响）

- [ ] **Step 5: Commit**

```bash
git add autoresearch/scan/frame.py tests/scan/test_frame.py
git commit -m "feat(frame): 60 日量价面板(20 日组逐元素不变)+ turnup 十列挂载(B 级,<40 日降级记账)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 1.5: 十列入 `keep` 白名单 + 体检列 + 合成帧

**Files:**
- Modify: `autoresearch/scan/universe.py:476-483`、`autoresearch/scan/health.py:28-30`、`tests/scan/_synth_universe.py`
- Test: `tests/scan/test_universe_l2_cols.py`（追加）

- [ ] **Step 1: 写失败测试（追加）**

```python
def test_l1_and_l2_csv_carry_turnup_panel_cols(monkeypatch, tmp_path):
    """2026-08-21 低位转强波:turnup.PANEL_COLS 十列算了就必须落盘(keep 白名单是唯一出口;
    price_vs_vwap_20/breakout_vol_20 至今『算了没落』就是反例)。L2 csv 复用 keep,随之带出。"""
    from autoresearch.common.turnup import PANEL_COLS
    outdir = run_universe(monkeypatch, tmp_path)
    for fname in ("L1_scored_full.csv", "L1_recall_top1000.csv", "L2_gbdt_top200.csv"):
        header = set(pd.read_csv(outdir / fname, nrows=0).columns)
        assert set(PANEL_COLS) <= header, f"{fname} 缺 {set(PANEL_COLS) - header}"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_universe_l2_cols.py -v`
Expected: 新条 FAIL（合成帧没这些列 / keep 没投影）

- [ ] **Step 3: 实现三处**

`tests/scan/_synth_universe.py` 的 DataFrame 字典末尾（`"is_st": False,` 前）追加：

```python
        # 2026-08-21 低位转强波:turnup.PANEL_COLS(生产由 frame._harvest_vol_series 60 日面板算出)
        "vol_ratio_20": rng.uniform(0.3, 3.0, n), "dist_low_60": rng.uniform(0, 60, n),
        "dist_high_60": rng.uniform(-60, 0, n), "days_no_new_low": rng.integers(0, 60, n).astype(float),
        "vol_ma5_prev": rng.uniform(1e4, 1e6, n), "vol_ma20_prev": rng.uniform(1e4, 1e6, n),
        "pct_5d": rng.uniform(-15, 15, n), "pct_20d": rng.uniform(-30, 30, n),
        "above_ma20": rng.integers(0, 2, n).astype(float), "ma5_gt_ma10": rng.integers(0, 2, n).astype(float),
```

`universe.py:476-483` 的 `keep` 列表末尾 `"ma_bull", "above_ma60"])` 改为：

```python
               "ma_bull", "above_ma60",
               *PANEL_COLS])          # 2026-08-21 低位转强波:turnup 十列(B 级;缺列 presence-gated 不出现)
```

并在 `universe.py` 顶部 import 区加 `from autoresearch.common.turnup import PANEL_COLS`。

`health.py:28-30` `_FACTOR_COLS` 追加 `"vol_ratio_20"`（列表末尾）。

`artifacts.py:39` `ArtifactSpec("l1_full", 1, "universe", "L1_scored_full.csv")` 的版本 1→**2**（列集是契约的一部分；bump 让 manifest 能区分新旧帧）。先 `grep -rn "l1_full" tests/` 看有没有测试锁版本号为 1：有则按 2 更新并在 commit 信息写明；`tests/scan/test_artifacts.py:77` 锁的是 index 的 `schema_version`，与此无关。

- [ ] **Step 4: 跑测试**

Run: `uv run --no-sync python -m pytest tests/scan/test_universe_l2_cols.py tests/scan/test_recall_channels.py tests/scan/test_l2_stratify.py tests/scan/test_health.py tests/scan/test_artifacts.py tests/scan/test_parity.py -q`
Expected: 全绿（合成帧加列不影响 8 路契约；parity 金样若对列集敏感会红——若红，读其断言：它锁的是**名单**不是列集，按需更新金样说明并在 commit 里写明）

- [ ] **Step 5: Commit**

```bash
git add autoresearch/scan/universe.py autoresearch/scan/health.py autoresearch/scan/artifacts.py tests/scan/_synth_universe.py tests/scan/test_universe_l2_cols.py
git commit -m "feat(universe): turnup 十列入 keep 白名单(L1/L2 csv 落盘;l1_full schema v2)+ NaN 体检列 + 合成帧

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 批次 2a · 修 `lens_reversal_confirm` 的门（G0 之前，回测要用修后的门）

### Task 2a.1: ②缩量看 D−1、RSI 上限 85；③起爆用 above_ma20∧ma5_gt_ma10

**Files:**
- Modify: `autoresearch/common/scoring.py:194-275`
- Test: `tests/common/test_scoring.py:158-224`（改 fixture + 新增）、`tests/scan/test_recall_channels.py:94-128`（改 fixture）

**Interfaces:**
- `lens_reversal_confirm(df)` 输出列名不变（`reversal_confirm_score/gate/signals`）。③ 硬门改读 `vol_ratio_20 ≥ 1.5 ∧ above_ma20 > 0 ∧ ma5_gt_ma10 > 0`（三列任一缺 → 整段 False，**不** presence-gated）；② 读 `vol_ma5_prev < vol_ma20_prev`（presence-gated）与 `20 ≤ rsi6 ≤ 85`；`ma_bull` 不再参与。

- [ ] **Step 1: 改 fixture + 写新测试**

`tests/common/test_scoring.py:165-174` `_confirm_row` 默认值改为：

```python
    row = {
        "code": "600001", "name": "股票甲",
        "pct_60d": -30.0, "dist_low_60": 8.0,
        "days_no_new_low": 15.0, "rsi6": 35.0,
        "vol_ma5_prev": 1.0, "vol_ma20_prev": 1.5,        # 起爆前缩量(D−1 截止)
        "vol_ratio_20": 2.0, "above_ma20": 1.0, "ma5_gt_ma10": 1.0,
        "ma_bull": 0.0,                                    # 60 日跌 30% 的票起爆日不可能全多头排列——门不得再要它
    }
```

`test_reversal_confirm_rejects_no_trend_break_hard_gate` 改为 `_confirm_row(above_ma20=0.0)`，docstring 改「只③的 above_ma20=0（未站回 20 日线）→ 硬门必拒」。追加：

```python
def test_reversal_confirm_does_not_require_full_ma_bull_alignment():
    """2026-08-21 修门:旧③用 ma_bull(MA5>MA10>MA20>MA60)当『站上 MA20』代理,与①(60 日跌≥25%)
    定义互斥 → 通道即使接上 vol_ratio_20 也恒近空。新③只要求站回 MA20 ∧ MA5>MA10。"""
    g = lens_reversal_confirm(_confirm_frame([_confirm_row(ma_bull=0.0)]))
    assert bool(g["reversal_confirm_gate"].iloc[0]) is True


def test_reversal_confirm_rejects_when_short_ma_not_turned():
    g = lens_reversal_confirm(_confirm_frame([_confirm_row(ma5_gt_ma10=0.0)]))
    assert bool(g["reversal_confirm_gate"].iloc[0]) is False


def test_reversal_confirm_shrink_measured_before_breakout_day():
    """②缩量看 D−1 截止的 5/20 日均量:起爆日巨量不再把『缩量企稳』顶成 False;前 5 日没缩量 → 拒。"""
    g = lens_reversal_confirm(_confirm_frame([_confirm_row(vol_ma5_prev=2.0, vol_ma20_prev=1.0)]))
    assert bool(g["reversal_confirm_gate"].iloc[0]) is False
    g2 = lens_reversal_confirm(_confirm_frame([_confirm_row()]).drop(columns=["vol_ma5_prev", "vol_ma20_prev"]))
    assert bool(g2["reversal_confirm_gate"].iloc[0]) is True        # 缺列 presence-gated 不拦(与①②既有约定一致)


def test_reversal_confirm_rsi_band_20_to_85():
    assert bool(lens_reversal_confirm(_confirm_frame([_confirm_row(rsi6=70.0)]))["reversal_confirm_gate"].iloc[0]) is True
    assert bool(lens_reversal_confirm(_confirm_frame([_confirm_row(rsi6=90.0)]))["reversal_confirm_gate"].iloc[0]) is False
    assert bool(lens_reversal_confirm(_confirm_frame([_confirm_row(rsi6=15.0)]))["reversal_confirm_gate"].iloc[0]) is False


def test_reversal_confirm_missing_above_ma20_column_rejects_all():
    frame = _confirm_frame([_confirm_row()]).drop(columns=["above_ma20"])
    assert not lens_reversal_confirm(frame)["reversal_confirm_gate"].any()
```

`tests/scan/test_recall_channels.py:98-102` `_perfect_confirm_row` 同步改为同一套字段（`vol_ma5_prev/vol_ma20_prev/above_ma20/ma5_gt_ma10`，`ma_bull: 0.0`）。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/common/test_scoring.py tests/scan/test_recall_channels.py -q -k reversal_confirm`
Expected: `does_not_require_full_ma_bull` / `shrink_measured_before` / `rsi_band` / `missing_above_ma20` / channel 三案例 FAIL

- [ ] **Step 3: 实现（`scoring.py:238-262` 替换）**

```python
    # ② 衰竭企稳(30):三子条件独立 presence-gated——gate 侧列缺→显式 True(不拦),
    # score 侧列缺→NaN(交 _blend 重新归一)。缩量看 **D−1 截止**的 vol_ma5_prev/vol_ma20_prev
    # (2026-08-21 修门:两者若含 D 日,起爆日巨量会把 5 日均量顶上去,恰在起爆日判 False)。
    has_days = "days_no_new_low" in g.columns
    days_ok = (_num(g["days_no_new_low"]) >= 10) if has_days else pd.Series(True, index=g.index)
    days_sc = _pct(g["days_no_new_low"]) if has_days else nan

    has_vol_ma = {"vol_ma5_prev", "vol_ma20_prev"} <= set(g.columns)
    shrink_ok = ((_num(g["vol_ma5_prev"]) < _num(g["vol_ma20_prev"])) if has_vol_ma
                 else pd.Series(True, index=g.index))

    has_rsi = "rsi6" in g.columns
    rsi = _num(g["rsi6"]) if has_rsi else nan
    # 20~85:下限仍挡「还在超卖里掉」,上限对齐 lens_momentum 过热线 85(:119)——放量起爆日 RSI6
    # 常在 55~80,旧上限 50 与③起爆日硬门在定义上互斥(2026-08-21 修门)。
    rebound_ok = ((rsi >= 20) & (rsi <= 85)) if has_rsi else pd.Series(True, index=g.index)
    rebound_sc = ((rsi >= 20) & (rsi <= 85)).astype(float) if has_rsi else nan

    stabilize_gate = days_ok & shrink_ok & rebound_ok
    stabilize_sc = _blend((days_sc, 0.5), (rebound_sc, 0.5))

    # ③ 确认起爆硬门(40):vol_ratio_20 ≥1.5 ∧ 站回 MA20 ∧ MA5>MA10。三列任一缺列/缺值 →
    # 比较天然 NaN→False,硬门自动"不可跳"(与①②故意不同,不写 presence-gated 的 else 分支放行)。
    # 2026-08-21 修门:旧代理 ma_bull(MA5>MA10>MA20>**MA60** 全多头)对 60 日跌≥25% 的票在
    # 起爆日结构上不可满足(MA20 还在 MA60 下面),①③互斥 → 通道恒近空。三列由 turnup 的
    # 60 日 close 面板算(frame._harvest_vol_series),同序列同口径。
    vol20 = _num(g["vol_ratio_20"]) if "vol_ratio_20" in g.columns else nan
    above20 = _num(g["above_ma20"]) if "above_ma20" in g.columns else nan
    m5gt10 = _num(g["ma5_gt_ma10"]) if "ma5_gt_ma10" in g.columns else nan
    confirm_gate = (vol20 >= 1.5) & (above20 > 0) & (m5gt10 > 0)
    has_turn = {"above_ma20", "ma5_gt_ma10"} <= set(g.columns)
    turn_sc = ((above20 > 0) & (m5gt10 > 0)).astype(float) if has_turn else nan
    confirm_sc = _blend((_pct(vol20), 0.5), (turn_sc, 0.5))
```

同步改 docstring 的②③两条（把 `vol_ma5/vol_ma20` 写成 `vol_ma5_prev/vol_ma20_prev(D−1 截止)`、`RSI 20–50` → `20–85`、`ma_bull` → `above_ma20 ∧ ma5_gt_ma10`，并加一句「2026-08-21 修门理由见 design §2.3」）。

- [ ] **Step 4: 跑测试**

Run: `uv run --no-sync python -m pytest tests/common/test_scoring.py tests/scan/test_recall_channels.py tests/scan/test_parity.py -q`
Expected: 全绿（`reversal_confirm` 不在生产 `recall_channels`，parity 不受影响）

- [ ] **Step 5: Commit**

```bash
git add autoresearch/common/scoring.py tests/common/test_scoring.py tests/scan/test_recall_channels.py
git commit -m "fix(common): lens_reversal_confirm 修门——③改站回MA20∧MA5>MA10(ma_bull 与①互斥),②缩量看 D−1、RSI 上限 85

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 批次 G0 · 前置证伪（零 LLM）+ 用户裁决门

### Task G0.1: `research/lowturn_precheck.py`（回测 + `--live`）

**Files:**
- Create: `autoresearch/research/lowturn_precheck.py`
- Test: `tests/research/test_lowturn_precheck.py`（新）

**Interfaces:**
- `daily_stats(frame, mask, ruler) -> dict | None`、`aggregate(days) -> dict`、`group_masks(frame, cfg) -> dict[str, pd.Series]`（键 `lowturn/healthy/reversal_old/reversal_confirm`）、`enrich_frames(frames, piv, P)`、`run_precheck(cap_floor=30.0, cfg=None) -> (table, verdict, regime_table)`、`stop_rule(table) -> dict`（`verdict ∈ {PROCEED, SPARSE, STOP_P3, NO_DATA}`）、`render(table, verdict, regime_table=None) -> str`、`run_live(scan_root=None) -> (pd.DataFrame, str)`。regime 分桶只读（`common.regime.classify_regime` 同生产判据），**不进停机规则**。
- 停机常量**先写后看**：`STOP_EXCESS_PP = -0.5`、`STOP_T = -2.0`、`STOP_MIN_DAYS = 40`、`SPARSE_MED = 3`；`RULERS = ("gap_c1_o2", "fwd_5_oc", "fwd_10_oc")`。

- [ ] **Step 1: 写失败测试**

```python
# tests/research/test_lowturn_precheck.py
"""Gate 0 前置证伪器:单日/跨日聚合、四组掩码、停机规则三态、渲染固定脚注。合成,无网络。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from autoresearch.research import lowturn_precheck as lp


def _frame(n=200, seed=0, planted=0.0):
    rng = np.random.default_rng(seed)
    df = pd.DataFrame({"code": [f"{600000 + i:06d}" for i in range(n)],
                       "gap_c1_o2": rng.normal(0, 0.02, n), "fwd_5_oc": rng.normal(0, 0.04, n),
                       "fwd_10_oc": rng.normal(0, 0.06, n), "buyable_c1": True, "buyable": True})
    df["flag"] = False
    df.loc[: n // 10, "flag"] = True
    df.loc[df["flag"], "gap_c1_o2"] += planted
    return df


def test_daily_stats_excess_vs_market_median_and_min_sample():
    df = _frame(planted=0.05)
    st = lp.daily_stats(df, df["flag"], "gap_c1_o2")
    assert st["n"] == 21 and st["excess"] > 0.03
    assert lp.daily_stats(_frame(n=30), _frame(n=30)["flag"], "gap_c1_o2") is None   # 截面 <50 不算


def test_aggregate_t_stat_sign_and_counts():
    days = [{"n": 5, "mean": 0.01, "median": 0.01, "hit": 0.6, "excess": 0.01, "market_median": 0.0}] * 10
    agg = lp.aggregate(days)
    assert agg["n_days"] == 10 and np.isclose(agg["excess_mean_pp"], 1.0) and agg["n_med_per_day"] == 5
    neg = [dict(d, excess=-0.01) for d in days]
    assert lp.aggregate(neg)["excess_mean_pp"] < 0


def test_stop_rule_three_states():
    def tbl(excess, t, n_days=60, n_med=5):
        return pd.DataFrame([{"group": "lowturn", "ruler": "gap_c1_o2", "n_days": n_days,
                              "n_med_per_day": n_med, "excess_mean_pp": excess, "t": t,
                              "hit_mean": 0.5, "mean_pp": excess}])
    assert lp.stop_rule(tbl(-0.8, -2.5))["verdict"] == "STOP_P3"
    assert lp.stop_rule(tbl(-0.8, -2.5, n_days=20))["verdict"] == "PROCEED"       # 天数不够不判死
    assert lp.stop_rule(tbl(0.2, 0.5, n_med=2))["verdict"] == "SPARSE"
    assert lp.stop_rule(tbl(0.2, 0.5))["verdict"] == "PROCEED"
    assert lp.stop_rule(pd.DataFrame())["verdict"] == "NO_DATA"


def test_group_masks_keys_and_dtype():
    from tests.scan._synth_universe import synth_universe
    df = synth_universe(n=120, seed=2)
    masks = lp.group_masks(df)
    assert set(masks) == {"lowturn", "healthy", "reversal_old", "reversal_confirm"}
    for m in masks.values():
        assert m.dtype == bool and len(m) == len(df)


def test_render_has_fixed_observation_footnote_and_optional_regime_section():
    table = pd.DataFrame([{"group": "lowturn", "ruler": "gap_c1_o2", "n_days": 60, "n_med_per_day": 4.0,
                           "excess_mean_pp": 0.1, "t": 0.4, "hit_mean": 0.5, "mean_pp": 0.0}])
    md = lp.render(table, lp.stop_rule(table))
    assert "fwd_5_oc" in md and "只观察" in md and "决策尺仍为 gap_c1_o2" in md
    assert "PROCEED" in md and "分 regime" not in md
    reg = pd.DataFrame([{"regime": "range", "n_days": 40, "n_med_per_day": 4.0, "excess_mean_pp": 0.2,
                         "t": 0.8, "hit_mean": 0.52, "mean_pp": 0.1}])
    md2 = lp.render(table, lp.stop_rule(table), reg)
    assert "分 regime" in md2 and "| range |" in md2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/research/test_lowturn_precheck.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现**

```python
# autoresearch/research/lowturn_precheck.py
#!/usr/bin/env python3
"""低位转强 · Gate 0 前置证伪(零 LLM)+ 活体双尺观察报告。

design: docs/specs/2026-08-21-lowturn-recall-l3-picture-display-design.md §7 / §9。

回测:读 factor_lab 成型日面板(`context/factor_lab`,≥60 日)→ 每日算四组掩码
(lowturn 旗 / healthy / 旧 reversal 门 / 修后 reversal_confirm 门)→ 组内主尺 `gap_c1_o2`
与参考尺 `fwd_5_oc`/`fwd_10_oc`(**只观察**)相对全市场截面中位的超额 → 跨日 t → 停机规则。
停机常量**先写后看**(本文件落盘早于任何读数)。

活体(`--live`):生产 scan 日的 `_l3_judged.json`(lane=lowturn)× `retro/attribution.csv`,
同一套 daily_stats/aggregate,只读不写账本。

用法:
  uv run --no-sync python -m autoresearch.research.lowturn_precheck [--cap-floor 30] [--out PATH]
  uv run --no-sync python -m autoresearch.research.lowturn_precheck --live [--out PATH]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from autoresearch.common import ruler as _ruler
from autoresearch.common import workspace as ws

RULERS = ("gap_c1_o2", "fwd_5_oc", "fwd_10_oc")
GROUPS = ("lowturn", "healthy", "reversal_old", "reversal_confirm")
STOP_EXCESS_PP = -0.5      # 相对超额均值 ≤ −0.5pp …
STOP_T = -2.0              # … 且 t ≤ −2.0 …
STOP_MIN_DAYS = 40         # … 且有旗亮票的成型日 ≥40 → STOP_P3
SPARSE_MED = 3             # 每日旗亮中位 <3 只 → SPARSE(定义过严,预登记放宽一档后重跑一次)
MIN_CROSS_SECTION = 50
FOOTNOTE = ("_参考尺 `fwd_5_oc`/`fwd_10_oc` **只观察**;决策尺仍为 gap_c1_o2(2026-07-10 / 08-05 裁定),"
            "本表不构成换尺依据。相对超额 = 组内均值 − 当日可交易全集截面中位(与 stage_eval.channel_edge 同口径)。_")


def daily_stats(frame: pd.DataFrame, mask: pd.Series, ruler: str) -> dict | None:
    ok = _ruler.entry_tradable(frame, ruler_name=ruler)
    fwd = pd.to_numeric(frame[ruler], errors="coerce")
    valid = ok & fwd.notna()
    base = fwd[valid]
    grp = fwd[valid & mask.reindex(frame.index).fillna(False).astype(bool)]
    if len(base) < MIN_CROSS_SECTION or len(grp) == 0:
        return None
    mkt_med = float(base.median())
    return {"n": int(len(grp)), "mean": float(grp.mean()), "median": float(grp.median()),
            "hit": float((grp > 0).mean()), "excess": float(grp.mean() - mkt_med), "market_median": mkt_med}


def aggregate(days: list[dict]) -> dict:
    if not days:
        return {"n_days": 0, "n_med_per_day": 0.0, "excess_mean_pp": float("nan"), "t": float("nan"),
                "hit_mean": float("nan"), "mean_pp": float("nan")}
    ex = np.array([d["excess"] for d in days], dtype=float)
    sd = ex.std(ddof=1) if len(ex) > 1 else 0.0
    t = float(ex.mean() / (sd / np.sqrt(len(ex)))) if sd > 0 else float("nan")
    return {"n_days": int(len(ex)), "n_med_per_day": float(np.median([d["n"] for d in days])),
            "excess_mean_pp": float(ex.mean() * 100), "t": t,
            "hit_mean": float(np.mean([d["hit"] for d in days])),
            "mean_pp": float(np.mean([d["mean"] for d in days]) * 100)}


def group_masks(frame: pd.DataFrame, cfg: dict | None = None) -> dict[str, pd.Series]:
    from autoresearch.common import scoring, turnup
    false = pd.Series(False, index=frame.index)
    masks = {"lowturn": turnup.lowturn_mask(frame, cfg).reindex(frame.index).fillna(False).astype(bool)}
    h = scoring.healthy_riser_mask(frame)
    masks["healthy"] = (h if h is not None else false).fillna(False).astype(bool)
    try:
        masks["reversal_old"] = scoring.lens_reversal(frame)["reversal_gate"].fillna(False).astype(bool)
    except KeyError:
        masks["reversal_old"] = false
    try:
        masks["reversal_confirm"] = scoring.lens_reversal_confirm(frame)["reversal_confirm_gate"].fillna(False).astype(bool)
    except KeyError:
        masks["reversal_confirm"] = false
    return masks


def enrich_frames(frames: list[pd.DataFrame], piv: dict, P: list[str]) -> list[pd.DataFrame]:
    """研究帧原只带三因子 → 并入 turnup 十列(含 dist_high_60/above_ma20 等旗所需列)。"""
    from autoresearch.common import turnup
    out = []
    sub = {k: piv[k] for k in ("high", "low", "close", "amount") if k in piv}
    for fr in frames:
        D = str(fr["date"].iloc[0])
        if D not in P:
            continue
        tp = turnup.panel_factors(sub, P[:P.index(D) + 1])
        fr2 = fr.drop(columns=[c for c in turnup.PANEL_COLS if c in fr.columns])
        out.append(fr2.merge(tp, left_on="code", right_index=True, how="left"))
    return out


def run_precheck(cap_floor: float = 30.0, cfg: dict | None = None) -> tuple[pd.DataFrame, dict, pd.DataFrame]:
    """→ (主表 四组×三尺, 停机裁决, lowturn×主尺 分 regime 表[只读,不进停机规则])。"""
    from autoresearch.common.regime import classify_regime
    from autoresearch.research import factor_lab as fl
    plan = pd.read_pickle(fl.OUT / "plan.pkl")
    P = plan["P"]
    piv = fl.load_price_pivots(P)
    frames = enrich_frames(fl._all_frames(cap_floor), piv, P)
    per_day: dict[tuple[str, str], list[dict]] = {(g, r): [] for g in GROUPS for r in RULERS}
    per_regime: dict[str, list[dict]] = {}
    for fr in frames:
        masks = group_masks(fr, cfg)
        for g in GROUPS:
            for r in RULERS:
                if r not in fr.columns:
                    continue
                st = daily_stats(fr, masks[g], r)
                if st:
                    per_day[(g, r)].append(st)
        try:
            regime = classify_regime(fr).label          # 研究帧带 pct_60d/above_ma60,同生产判据
        except Exception:  # noqa: BLE001 — 分桶只读,算不出记 NA
            regime = "NA"
        st_lt = daily_stats(fr, masks["lowturn"], "gap_c1_o2") if "gap_c1_o2" in fr.columns else None
        if st_lt:
            per_regime.setdefault(regime, []).append(st_lt)
    table = pd.DataFrame([{"group": g, "ruler": r, **aggregate(days)} for (g, r), days in per_day.items()])
    regime_table = pd.DataFrame([{"regime": k, **aggregate(v)} for k, v in sorted(per_regime.items())])
    return table, stop_rule(table), regime_table


def stop_rule(table: pd.DataFrame) -> dict:
    if table is None or table.empty or "group" not in table.columns:
        return {"verdict": "NO_DATA", "why": "无可用成型日/无旗亮票"}
    r = table[(table["group"] == "lowturn") & (table["ruler"] == "gap_c1_o2")]
    if r.empty:
        return {"verdict": "NO_DATA", "why": "lowturn×gap_c1_o2 无行"}
    r = r.iloc[0]
    base = {"n_days": int(r["n_days"]), "excess_mean_pp": float(r["excess_mean_pp"]), "t": float(r["t"]),
            "n_med_per_day": float(r["n_med_per_day"])}
    if (r["n_days"] >= STOP_MIN_DAYS and r["excess_mean_pp"] <= STOP_EXCESS_PP
            and pd.notna(r["t"]) and r["t"] <= STOP_T):
        return {"verdict": "STOP_P3", "why": f"相对超额 {r['excess_mean_pp']:+.2f}pp,t={r['t']:.2f},"
                f"n_days={int(r['n_days'])} ≥{STOP_MIN_DAYS} —— 1~2 日尺下为显著负 edge,P3 不上线", **base}
    if r["n_med_per_day"] < SPARSE_MED:
        return {"verdict": "SPARSE", "why": f"每日旗亮中位 {r['n_med_per_day']:.0f} <{SPARSE_MED}:定义过严,"
                "按预登记放宽一档(min_vol_ratio_20 1.2→1.0 / max_dist_high_60 −15→−10)重跑一次", **base}
    return {"verdict": "PROCEED", "why": "未触发停机(不显著 ≠ 有 alpha;进入 ≥10 扫描日活体裁决)", **base}


def render(table: pd.DataFrame, verdict: dict, regime_table: pd.DataFrame | None = None) -> str:
    lines = ["# 低位转强 · Gate 0 读数", "",
             f"**裁决:{verdict.get('verdict')}** —— {verdict.get('why', '')}", "",
             "| 组 | 尺 | n_days | 每日 n 中位 | 组内均值 pp | 相对超额 pp | t | 胜率 |",
             "|---|---|---:|---:|---:|---:|---:|---:|"]
    for row in table.itertuples(index=False):
        lines.append(f"| {row.group} | `{row.ruler}` | {row.n_days} | {row.n_med_per_day:.0f} | "
                     f"{row.mean_pp:+.2f} | {row.excess_mean_pp:+.2f} | {row.t:.2f} | {row.hit_mean:.0%} |")
    if regime_table is not None and len(regime_table):
        lines += ["", "## lowturn × gap_c1_o2 分 regime(只读;risk_off 样本薄,不据此调参)", "",
                  "| regime | n_days | 每日 n 中位 | 相对超额 pp | t | 胜率 |", "|---|---:|---:|---:|---:|---:|"]
        for row in regime_table.itertuples(index=False):
            lines.append(f"| {row.regime} | {row.n_days} | {row.n_med_per_day:.0f} | "
                         f"{row.excess_mean_pp:+.2f} | {row.t:.2f} | {row.hit_mean:.0%} |")
    lines += ["", FOOTNOTE, "",
              f"_停机规则(先写后看):相对超额 ≤{STOP_EXCESS_PP}pp ∧ t ≤{STOP_T} ∧ n_days ≥{STOP_MIN_DAYS} → STOP_P3;"
              f"每日旗亮中位 <{SPARSE_MED} → SPARSE;其余 PROCEED。_"]
    return "\n".join(lines) + "\n"


def run_live(scan_root: Path | str | None = None) -> tuple[pd.DataFrame, str]:
    """生产日 lane=lowturn finalist 的双尺读数(只读)。无成熟日 → 空表 + 说明。"""
    root = Path(scan_root or ws.scan_root())
    per_day: dict[str, list[dict]] = {r: [] for r in RULERS}
    n_days_seen = 0
    for day in sorted(p for p in root.iterdir() if p.is_dir()):
        jp, ap = day / "_l3_judged.json", day / "retro" / "attribution.csv"
        if not (jp.exists() and ap.exists()):
            continue
        try:
            judged = json.loads(jp.read_text(encoding="utf-8"))
            attr = pd.read_csv(ap, dtype={"code": str})
        except Exception:  # noqa: BLE001 — 坏日跳过,不编
            continue
        codes = {str(e.get("code", "")).zfill(6) for e in judged
                 if isinstance(e, dict) and e.get("lane") == "lowturn" and e.get("finalist")}
        if not codes:
            continue
        n_days_seen += 1
        attr["code"] = attr["code"].astype(str).str.zfill(6)
        mask = attr["code"].isin(codes)
        for r in RULERS:
            if r in attr.columns:
                st = daily_stats(attr, mask, r)
                if st:
                    per_day[r].append(st)
    table = pd.DataFrame([{"group": "lowturn(live finalist)", "ruler": r, **aggregate(days)}
                          for r, days in per_day.items()])
    note = f"_活体:{n_days_seen} 个有 lowturn finalist 的扫描日;成熟日按尺计入 n_days。_"
    return table, note


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cap-floor", type=float, default=30.0)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--out", type=str, default=None)
    ap.add_argument("--cfg", type=str, default=None, help="JSON 字符串,覆盖 LOWTURN_DEFAULTS 阈值(预登记放宽一档用)")
    a = ap.parse_args(argv)
    cfg = json.loads(a.cfg) if a.cfg else None
    if a.live:
        table, note = run_live()
        md = render(table, {"verdict": "LIVE", "why": note})
        out = Path(a.out) if a.out else ws.reports_root() / "research" / "lowturn_live.md"
    else:
        table, verdict, regime_table = run_precheck(a.cap_floor, cfg)
        md = render(table, verdict, regime_table)
        out = Path(a.out) if a.out else ws.reports_root() / "research" / "lowturn_precheck.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"[done] → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑测试**

Run: `uv run --no-sync python -m pytest tests/research/test_lowturn_precheck.py -v`
Expected: 全绿

- [ ] **Step 5: Commit（停机规则随代码一起落盘 = 先写后看的证据）**

```bash
git add autoresearch/research/lowturn_precheck.py tests/research/test_lowturn_precheck.py
git commit -m "feat(research): lowturn Gate 0 前置证伪器(四组×三尺,停机规则先写后看)+ --live 双尺观察

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task G0.2: 先写假设再跑读数，落报告，**用户裁决门**

**Files:**
- Create: `docs/research/<YYYY-MM-DD>-lowturn-precheck.md`（`YYYY-MM-DD` = 跑动当天）
- Modify: `docs/research/factor-backlog.md`（队列加一行）

- [ ] **Step 1: 先写 §0（跑之前落盘并 commit）**

```markdown
# 低位转强 · Gate 0 前置证伪读数

## 0. 假设与停机规则(先写后看;本节 commit 早于任何读数)

- H0:在主尺 `gap_c1_o2`(T+1 收买 → T+2 开卖)下,「低位转强」画像(design §6.1 默认阈值)相对全市场可交易等权的超额 ≤ 0。
- H1:超额 > 0(不要求显著);本门只证伪 **显著为负**。
- 停机:相对超额均值 ≤ −0.5pp ∧ t ≤ −2.0 ∧ n_days ≥ 40 → **STOP_P3**(L3 席位不上线,P1/P2 保留);每日旗亮中位 <3 → **SPARSE**(仅允许预登记的两处放宽:`min_vol_ratio_20` 1.2→1.0、`max_dist_high_60` −15→−10,重跑一次);其余 → **PROCEED**(进入 ≥10 扫描日活体裁决)。
- 对照组:healthy(现行主力画像)、旧 `reversal` 门、修后 `reversal_confirm` 门;参考尺 `fwd_5_oc`/`fwd_10_oc` **只观察**。
- 样本:factor_lab 成型日面板(`context_claude/factor_lab/plan.pkl`,2025-05-23→2026-08-05,132 日),cap_floor 30 亿,buyable_only。
```

```bash
git add docs/research/$(date +%F)-lowturn-precheck.md
git commit -m "docs(research): lowturn Gate 0 §0 假设与停机规则(先写后看)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

- [ ] **Step 2: 跑读数**

Run: `uv run --no-sync python -m autoresearch.research.lowturn_precheck --cap-floor 30`
Expected: 打印四组×三尺表 + 裁决行；产物 `reports_claude/research/lowturn_precheck.md`。若 `SPARSE` → 再跑一次 `--cfg '{"min_vol_ratio_20":1.0,"max_dist_high_60":-10.0}'`，两份都贴。

- [ ] **Step 3: 把表与裁决贴进报告 §1–§3（§1 主表、§2 放宽档（如有）、§3 一句话裁决），`factor-backlog.md` 队列加行：**

```markdown
| **低位转强(组合旗 lowturn)** | 跌过+站回MA20+放量+主力转正 在 1~2 日尺下有非负超额 | 现有 lake daily(60 日面板,turnup.py) | **<PROCEED/SPARSE/STOP_P3>(YYYY-MM-DD)** | 读数:`docs/research/YYYY-MM-DD-lowturn-precheck.md`;design 2026-08-21 §7;fwd_5/10 只观察 |
```

```bash
git add docs/research/ reports_claude/research/lowturn_precheck.md 2>/dev/null; git add docs/research/
git commit -m "docs(research): lowturn Gate 0 读数 + factor-backlog 记账

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

（`reports_claude/` 已 gitignore，只 add `docs/research/`。）

- [ ] **Step 4: 🛑 用户裁决门**

把 §3 一句话裁决 + 表贴给用户。**拿到明确回复前，批次 3 不动工**；批次 2b 可继续。

---

## 批次 2b · P2 召回层:重开通道、反转桶、恒空探针、配置与文档

### Task 2b.1: `STYLE_CHANNELS["反转"]` 纳入 `reversal_confirm`

**Files:**
- Modify: `autoresearch/scan/recall/l2_stratify.py:17-25`
- Test: `tests/scan/test_l2_stratify.py`（追加）

- [ ] **Step 1: 写失败测试（追加）**

```python
def test_reversal_confirm_feeds_reversal_bucket():
    """重开 reversal_confirm(2026-08-21)后它的独有召回必须入「反转」桶,否则零 floor 保护——
    侦察实测的真缺口(design §5.2)。"""
    from autoresearch.scan.recall.l2_stratify import STYLE_CHANNELS
    assert set(STYLE_CHANNELS["反转"]) == {"reversal", "reversal_confirm"}
```

- [ ] **Step 2: 跑测试确认失败** → `uv run --no-sync python -m pytest tests/scan/test_l2_stratify.py -v -k reversal_confirm_feeds` Expected: FAIL

- [ ] **Step 3: 实现** — `l2_stratify.py` 中 `"反转": ("reversal",),` 改为 `"反转": ("reversal", "reversal_confirm"),   # 2026-08-21 重开:两路共用 floor 12`

- [ ] **Step 4: 跑测试** → `uv run --no-sync python -m pytest tests/scan/test_l2_stratify.py tests/scan/test_parity.py -q` Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add autoresearch/scan/recall/l2_stratify.py tests/scan/test_l2_stratify.py
git commit -m "feat(recall): reversal_confirm 纳入 L2「反转」风格桶(重开后独有召回有 floor 保护)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 2b.2: 恒空探针 `channel_liveness_lint` + 接线

**Files:**
- Modify: `autoresearch/learning/self_review.py`（新函数，放在 `product_shape_lint` 之前）、`autoresearch/scan/report_sections.py:866-870`（并列接线）
- Test: `tests/learning/test_channel_liveness_lint.py`（新）

**Interfaces:**
- `channel_liveness_lint(scan_dir, date_str, *, recall_channels=None, history_days=3) -> list[dict]`；返回行 `{"check": "通道活性·名义启用实际空召回", "severity": "warn", "detail": "...", "code": <channel>}`；`recall_channels=None` → 读 config `funnel.recall_channels`，config 缺/空 → `[]`（不知道谁启用就不报）。连续 ≥`history_days` 个扫描日 0 行 → detail 前缀 `🔴`（仍 warn，见 Global Constraints 偏离③）。

- [ ] **Step 1: 写失败测试**

```python
# tests/learning/test_channel_liveness_lint.py
"""通道活性探针:启用通道在 L1_channels.csv 0 行 → warn;连续 ≥3 扫描日 → 🔴 前缀。合成,零网络。

病灶:reversal_confirm 名义启用实际恒空 4 周+无人发现(2026-08-19 才摘);event 桶 29 日天天
改生产输入。探针配方:自动腿必须有一个会变的量做断言,否则它死了也像活着。"""
from __future__ import annotations

import pandas as pd

from autoresearch.learning.self_review import channel_liveness_lint

CH = ["composite", "momentum", "reversal_confirm"]


def _day(root, date, counts: dict[str, int]):
    d = root / date
    d.mkdir(parents=True, exist_ok=True)
    rows = [{"channel": ch, "code": f"{i:06d}", "channel_rank": i + 1, "channel_score": 1.0}
            for ch, n in counts.items() for i in range(n)]
    pd.DataFrame(rows, columns=["channel", "code", "channel_rank", "channel_score"]).to_csv(
        d / "L1_channels.csv", index=False)
    return d


def test_enabled_channel_with_zero_rows_warns(tmp_path):
    d = _day(tmp_path, "2026-08-20", {"composite": 5, "momentum": 3, "reversal_confirm": 0})
    rows = channel_liveness_lint(d, "2026-08-20", recall_channels=CH)
    assert len(rows) == 1 and rows[0]["code"] == "reversal_confirm" and rows[0]["severity"] == "warn"
    assert "连续 1" in rows[0]["detail"] and "🔴" not in rows[0]["detail"]


def test_all_live_is_silent_and_missing_file_is_silent(tmp_path):
    d = _day(tmp_path, "2026-08-20", {"composite": 5, "momentum": 3, "reversal_confirm": 2})
    assert channel_liveness_lint(d, "2026-08-20", recall_channels=CH) == []
    (d / "L1_channels.csv").unlink()
    assert channel_liveness_lint(d, "2026-08-20", recall_channels=CH) == []


def test_three_consecutive_empty_days_escalate_prefix(tmp_path):
    for date in ("2026-08-17", "2026-08-18", "2026-08-19"):
        _day(tmp_path, date, {"composite": 5, "reversal_confirm": 0})
    d = _day(tmp_path, "2026-08-20", {"composite": 5, "reversal_confirm": 0})
    rows = channel_liveness_lint(d, "2026-08-20", recall_channels=CH, history_days=3)
    assert rows[0]["severity"] == "warn" and rows[0]["detail"].startswith("🔴") and "连续 4" in rows[0]["detail"]


def test_streak_breaks_on_a_live_day(tmp_path):
    _day(tmp_path, "2026-08-18", {"composite": 5, "reversal_confirm": 0})
    _day(tmp_path, "2026-08-19", {"composite": 5, "reversal_confirm": 4})     # 活过一天
    d = _day(tmp_path, "2026-08-20", {"composite": 5, "reversal_confirm": 0})
    rows = channel_liveness_lint(d, "2026-08-20", recall_channels=CH)
    assert "连续 1" in rows[0]["detail"]


def test_no_config_means_silent(tmp_path, monkeypatch):
    d = _day(tmp_path, "2026-08-20", {"composite": 5})
    monkeypatch.setattr("autoresearch.scan.user_config.load_user_config", lambda *a, **k: {})
    assert channel_liveness_lint(d, "2026-08-20") == []
```

- [ ] **Step 2: 跑测试确认失败** → `uv run --no-sync python -m pytest tests/learning/test_channel_liveness_lint.py -v` Expected: ImportError

- [ ] **Step 3: 实现（`self_review.py`，放在 `def product_shape_lint` 之前）**

```python
LIVENESS_CHECK = "通道活性·名义启用实际空召回"
LIVENESS_ESCALATE_STREAK = 3


def _channel_rows(path) -> dict[str, int] | None:
    """L1_channels.csv → {channel: 行数};缺/坏文件 → None。"""
    import contextlib
    import pandas as pd
    with contextlib.suppress(Exception):
        if path.exists():
            df = pd.read_csv(path, dtype={"code": str})
            if "channel" in df.columns:
                return df["channel"].astype(str).value_counts().to_dict()
    return None


def channel_liveness_lint(scan_dir, date_str: str, *, recall_channels=None,
                          history_days: int = LIVENESS_ESCALATE_STREAK) -> list[dict]:
    """启用通道 0 召回探针(2026-08-21 低位转强波 §5.3)。

    `reversal_confirm` 名义启用实际恒空 4 周+无人发现(起爆硬门列从未接入 L1 帧)——这类死法
    没有任何报错,只有 `L1_channels.csv` 里那一路恒 0 行。探针:config `funnel.recall_channels`
    里的每一路在当日 `L1_channels.csv` 计行,0 行 → **warn**(恒 warn:`fail` 会触发 GATE4 阻断
    发布,探针职责是可见性不是停机);连续 ≥`history_days` 个扫描日 0 行 → detail 加 🔴 前缀。
    `recall_channels=None` → 读 config;config 缺/空 → 不知道谁启用,返回 []。全部 presence-gated,
    绝不抛异常。
    """
    from pathlib import Path
    scan_dir = Path(scan_dir)
    if recall_channels is None:
        try:
            from autoresearch.scan.user_config import load_user_config
            recall_channels = (load_user_config().get("funnel") or {}).get("recall_channels")
        except Exception:  # noqa: BLE001 — 配置层故障不挡自检
            recall_channels = None
    if not recall_channels:
        return []
    today = _channel_rows(scan_dir / "L1_channels.csv")
    if today is None:
        return []
    prior_days = sorted((p for p in scan_dir.parent.iterdir()
                         if p.is_dir() and p.name < scan_dir.name), reverse=True)
    out: list[dict] = []
    for ch in recall_channels:
        if int(today.get(ch, 0)) > 0:
            continue
        streak = 1
        for day in prior_days:
            counts = _channel_rows(day / "L1_channels.csv")
            if counts is None or int(counts.get(ch, 0)) > 0:
                break
            streak += 1
        prefix = "🔴" if streak >= history_days else ""
        out.append({"check": LIVENESS_CHECK, "severity": "warn", "code": ch,
                    "detail": f"{prefix}{ch} 当日 L1_channels.csv 0 行(连续 {streak} 个扫描日)"
                              "—— 名义启用实际空召回:列没接上/门写死/取数坏,先查再谈 edge"})
    return out
```

`report_sections.py:866` 的 `product_shape_lint` 块之后并列加：

```python
    with contextlib.suppress(Exception):                            # 通道活性探针(2026-08-21 低位转强波 §5.3,恒 warn)
        live_extra = self_review.channel_liveness_lint(scan_dir, scan_dir.name)
        if live_extra:
            res["failures"].extend(live_extra)
            res["n_warn"] = res.get("n_warn", 0) + len(live_extra)
```

- [ ] **Step 4: 跑测试** → `uv run --no-sync python -m pytest tests/learning/test_channel_liveness_lint.py tests/learning/test_product_shape_lint.py tests/scan/test_report_sections.py -q`（最后一个文件若不存在则略）Expected: 全绿

- [ ] **Step 5: 变异探针（手工）** — 把 `if int(today.get(ch, 0)) > 0: continue` 改成 `>= 0` → 第一条测试必须红；恢复。

- [ ] **Step 6: Commit**

```bash
git add autoresearch/learning/self_review.py autoresearch/scan/report_sections.py tests/learning/test_channel_liveness_lint.py
git commit -m "feat(learning): 通道活性探针——启用通道 0 召回进 self_review warn(连续 3 日 🔴)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 2b.3: 配置重开 + STAGES/SKILL 文档

**Files:**
- Modify: `.claude/skills/scan-market/scan_config.jsonc:70-99`（`recall_channels`、`channel_quotas`、注释）、`.claude/skills/scan-market/STAGES.md:53-54,:62 表行,:298`、`.claude/skills/scan-market/SKILL.md:48`
- Test: `tests/scan/test_scan_config_live.py`（新；锁**真实** jsonc 的这两键）

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_scan_config_live.py
"""锁生产 scan_config.jsonc 的召回键(2026-08-21 重开 reversal_confirm):配置是单一事实源,
测试锁住「重开了」这件事本身,防 revert/手滑把它再摘掉而无人知。"""
from __future__ import annotations

from pathlib import Path

from autoresearch.scan.user_config import load_user_config

CFG = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "scan-market" / "scan_config.jsonc"


def test_reversal_confirm_reopened_with_quota():
    cfg = load_user_config(CFG)
    funnel = cfg["funnel"]
    assert "reversal_confirm" in funnel["recall_channels"]
    assert "reversal" in funnel["recall_channels"]                 # A/B 两路同时活体
    assert funnel["channel_quotas"]["reversal_confirm"] == 150
    # 36 日版六键仍全写(注册表默认回落陷阱,见 jsonc 注释)
    for k, v in {"value": 312, "momentum": 188, "heat": 112, "healthy": 112, "growth": 112, "main_fund": 150}.items():
        assert funnel["channel_quotas"][k] == v
```

- [ ] **Step 2: 跑测试确认失败** → `uv run --no-sync python -m pytest tests/scan/test_scan_config_live.py -v` Expected: FAIL

- [ ] **Step 3: 改 jsonc**

`"recall_channels"` 行改为：

```jsonc
    // 2026-08-21 低位转强波(design docs/specs/2026-08-21-lowturn-…-design.md §5):reversal_confirm
    // **重开**(第 8→9 路)——reopen 条件「vol_ratio_20 接入生产 L1 帧」已满足(frame._harvest_vol_series
    // 60 日面板 + turnup 十列),且门已修(③ above_ma20∧ma5_gt_ma10 取代与①互斥的 ma_bull)。
    // 与旧 reversal 两路同时活体 A/B:channel_ledger 按 lane 分行,≥10 扫描日后人批裁决去留。
    // self_review「通道活性」探针盯它 0 行(名义启用实际恒空的同族前科)。
    "recall_channels": ["composite", "momentum", "reversal", "reversal_confirm", "value", "main_fund", "heat", "growth", "healthy"],
```

`"channel_quotas"` 行改为（七键全写）：

```jsonc
    "channel_quotas": { "value": 312, "momentum": 188, "heat": 112, "healthy": 112, "growth": 112, "main_fund": 150,
                        "reversal_confirm": 150 }   // 2026-08-21 重开:中档起步(design R6),10 日后按账本调
```

并把上面「2026-08-19 摘 reversal_confirm(第 9→8 路)」那段注释改写为历史沿革（保留一句「08-19 摘 / 08-21 重开」）。

- [ ] **Step 4: 改 STAGES.md / SKILL.md**

`STAGES.md:54` `reversal_confirm` 行改为：
`| **reversal_confirm** | 150/50 | 反转确认四段:低位 + 企稳(D−1 缩量、RSI 20~85)+ **放量起爆硬门(vol_ratio_20≥1.5 ∧ 站回 MA20 ∧ MA5>MA10)** + 可交易。**2026-08-21 重开**(数据腿:frame 60 日面板 + common/turnup.py;门修法与理由见 design 2026-08-21 §2.3/§5.1)。与旧 reversal 同时活体 A/B,裁决见「开放线头」 |`
`STAGES.md:298` 开放线头第 2 条末尾追加：「**2026-08-21 重开 reversal_confirm**(配额 150),A/B 重新计时:≥10 扫描日后按 `channel_ledger` 的 `unique_excess_t2` 分 lane 裁决(新路 ≥ 旧路且 ≥0 → 旧路提退役;新路 <0 → 回影子;皆负 → 皆退役)。」
`SKILL.md:48` 把 `recall_channels(8路,2026-08-19 摘 reversal_confirm)` 改为 `recall_channels(9路,2026-08-21 重开 reversal_confirm)`，`channel_quotas` 现值加 `reversal_confirm150`。

- [ ] **Step 5: 跑测试** → `uv run --no-sync python -m pytest tests/scan/test_scan_config_live.py tests/scan/test_user_config.py tests/scan/test_config_knobs.py tests/test_skill_docs_refs.py -q` Expected: 全绿

- [ ] **Step 6: Commit**

```bash
git add .claude/skills/scan-market/scan_config.jsonc .claude/skills/scan-market/STAGES.md .claude/skills/scan-market/SKILL.md tests/scan/test_scan_config_live.py
git commit -m "feat(scan): 重开 reversal_confirm 召回路(quota 150,与旧 reversal 活体 A/B)+ 文档同步

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 批次 3 · P3 L3 第三画像（**G0 = PROCEED/SPARSE-放宽后 PROCEED 且用户确认后**才动）

### Task 3.1: `l3.lowturn` 配置三件套

**Files:**
- Modify: `autoresearch/scan/user_config.py:112`（`_SUB_WHITELIST["l3"]`）、`:136-150`（`_KNOB_TYPES`）；`.claude/skills/scan-market/scan_config.jsonc` `l3` 块；`.claude/skills/scan-market/SKILL.md` L3 行
- Test: `tests/scan/test_user_config.py`（追加）

- [ ] **Step 1: 写失败测试（追加）**

```python
def test_l3_lowturn_dict_whitelisted(tmp_path):
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"l3": {"lowturn": {"enabled": True, "min_vol_ratio_20": 1.2}}}), encoding="utf-8")
    assert load_user_config(p)["l3"]["lowturn"] == {"enabled": True, "min_vol_ratio_20": 1.2}


def test_l3_lowturn_wrong_type_raises(tmp_path):
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"l3": {"lowturn": True}}), encoding="utf-8")   # 应为 object
    with pytest.raises(ValueError, match="lowturn"):
        load_user_config(p)
```

- [ ] **Step 2: 跑测试确认失败** → `uv run --no-sync python -m pytest tests/scan/test_user_config.py -v -k lowturn` Expected: 第一条 FAIL（unknown subkey raise）

- [ ] **Step 3: 实现**

`user_config.py:112` → `"l3": {"two_pass", "pass1_target", "finalist_max", "lowturn"},`
`_KNOB_TYPES` 追加一行：`("l3", "lowturn"): (_t_dict, "object"),`

jsonc `l3` 块追加（在 `"finalist_max": 10` 后，注意前一行补逗号）：

```jsonc
    "finalist_max": 10,        // finalist tier 上限
    // 低位转强第三画像(2026-08-21 design §6.1;Gate 0 裁决 <PROCEED,YYYY-MM-DD> 后启用)。
    // 【生效点】scan/l3/prompt.py prepare_l3_table → l3_table_md(lowturn_flag, lowturn_cfg)(表内 lowturn 旗列+图例)
    //           + scan/l3/triage.py triage_l2_for_l3(lowturn_cap, lowturn_cfg)(pass1 强留 ≤pass1_cap)
    //           谓词真身 common/turnup.lowturn_flag;enabled=false 或删块 = 逐字 parity(旗列与强留同关)。
    "lowturn": {
      "enabled": true,
      "max_dist_high_60": -15.0,  // 低位:距 60 日高 ≥15%(仍在水下)
      "max_pct_60d": 10.0,        //   且 60 日涨幅 <10(没涨回去)
      "min_vol_ratio_20": 1.2,    // 放量(通道硬门 1.5 的放宽档;L3 还有 agent 复核)
      "min_pct_5d": 0.0,          // 近 5 日为正(转强)
      "require_above_ma20": true, // 站回 MA20
      "require_ma5_gt_ma10": true,// 短均线拐头
      "fund": "main_or_cmf",      // 主力净额>0 或 cmf_20>0;可选 main | cmf
      "knife_pct_60d": -35.0,     // 落刀:60 日跌超此值且主力不为正 → 不接
      "pass1_cap": 8              // pass1 强留上限(保护 40 席预算)
    }
```

（若 G0 裁决为 SPARSE 后放宽档 PROCEED，`min_vol_ratio_20`/`max_dist_high_60` 写放宽值并在注释标明。）

`SKILL.md` L3 行：`two_pass·pass1_target·finalist_max·lowturn{enabled,阈值×8,pass1_cap}`，生效点加 `l3/triage.py`。

- [ ] **Step 4: 跑测试** → `uv run --no-sync python -m pytest tests/scan/test_user_config.py tests/scan/test_config_knobs.py tests/scan/test_scan_config_live.py -q` Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add autoresearch/scan/user_config.py .claude/skills/scan-market/scan_config.jsonc .claude/skills/scan-market/SKILL.md tests/scan/test_user_config.py
git commit -m "feat(config): l3.lowturn 低位转强阈值块入白名单(三件套;enabled 即生效点开关)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 3.2: L3 表 `lowturn` 旗列 + `prepare_l3_table` 接线

**Files:**
- Modify: `autoresearch/scan/l3/prompt.py:200-205`（签名）、`:326-334`（misread 块后加 lowturn 块）、`:383-388`（读 cfg）、`:409-412`（调用）
- Test: `tests/scan/test_l3_lowturn_flag.py`（新）；`tests/scan/test_l3_pass1.py:389-404` 与 `tests/scan/test_l3_prepare.py:95` 的 parity 测试**不改、必须仍绿**

**Interfaces:**
- `l3_table_md(..., lowturn_flag: bool = False, lowturn_cfg: dict | None = None)`：开 → 加列 `lowturn`（`转强`/空）+ 图例一行含「今日旗亮 N 只」；默认关 = 逐字 parity。
- `prepare_l3_table` 返回 dict 多一键 `lowturn_n`（旗亮只数；关时不出现）。

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_l3_lowturn_flag.py
"""L3 表低位转强旗列(lowturn_flag,默认关=parity)+ prepare 接线跟随 l3.lowturn.enabled。合成,无网络。"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.scan.agents.l3_select import l3_table_md, prepare_l3_table

_DATE = "2026-08-20"


def _row(code, *, turn: bool, name="甲"):
    base = {"code": code, "name": name, "industry": "电子", "composite": 80.0, "gbdt_score": 80.0,
            "n_channels": 1, "recall_channels": "reversal", "pe": 20.0, "main_net_ratio": -0.01,
            "pct_60d": -12.0, "dist_high_60": -22.0, "pct_5d": 4.0, "above_ma20": 1.0, "ma5_gt_ma10": 1.0,
            "vol_ratio_20": 1.6, "main_inflow_yi": 0.8, "cmf_20": 0.05}
    if not turn:
        base.update(above_ma20=0.0)
    return base


def _mk(root, rows):
    d = root / _DATE
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(d / "L2_gbdt_top200.csv", index=False)
    return d


def test_lowturn_flag_adds_column_and_legend(tmp_path):
    _mk(tmp_path, [_row("000001", turn=True), _row("000002", turn=False, name="乙")])
    md = l3_table_md(_DATE, root=tmp_path, lowturn_flag=True)
    assert "lowturn" in md and "转强" in md and "硬约束 B 不适用" in md and "今日旗亮 1 只" in md


def test_lowturn_flag_default_off_is_byte_parity(tmp_path):
    _mk(tmp_path, [_row("000001", turn=True)])
    assert l3_table_md(_DATE, root=tmp_path) == l3_table_md(_DATE, root=tmp_path, lowturn_flag=False)
    assert "lowturn" not in l3_table_md(_DATE, root=tmp_path)


def test_lowturn_cfg_threshold_respected(tmp_path):
    _mk(tmp_path, [_row("000001", turn=True)])
    md = l3_table_md(_DATE, root=tmp_path, lowturn_flag=True, lowturn_cfg={"min_vol_ratio_20": 2.0})
    assert "今日旗亮 0 只" in md


def test_prepare_follows_config_enabled(tmp_path, monkeypatch):
    base = tmp_path / "context" / "scan"
    _mk(base, [_row("000001", turn=True), _row("000002", turn=False, name="乙")])
    cfgp = tmp_path / "scan_config.jsonc"
    cfgp.write_text(json.dumps({"l3": {"two_pass": True, "pass1_target": 40,
                                        "lowturn": {"enabled": True}}}), encoding="utf-8")
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", cfgp)
    res = prepare_l3_table(_DATE, root=base, do_harvest=False)
    md = (base / _DATE / "_l3_table.md").read_text(encoding="utf-8")
    assert "lowturn" in md and res.get("lowturn_n") == 1


def test_prepare_config_disabled_has_no_column(tmp_path, monkeypatch):
    base = tmp_path / "context" / "scan"
    _mk(base, [_row("000001", turn=True)])
    cfgp = tmp_path / "scan_config.jsonc"
    cfgp.write_text(json.dumps({"l3": {"two_pass": True, "pass1_target": 40}}), encoding="utf-8")
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", cfgp)
    res = prepare_l3_table(_DATE, root=base, do_harvest=False)
    assert "lowturn" not in (base / _DATE / "_l3_table.md").read_text(encoding="utf-8")
    assert "lowturn_n" not in res
```

- [ ] **Step 2: 跑测试确认失败** → `uv run --no-sync python -m pytest tests/scan/test_l3_lowturn_flag.py -v` Expected: `TypeError: unexpected keyword 'lowturn_flag'`

- [ ] **Step 3: 实现**

签名（`prompt.py:200-205`）末尾加 `lowturn_flag: bool = False, lowturn_cfg: dict | None = None`；docstring 加一段：
`lowturn_flag=True:加 lowturn 低位转强旗列(谓词=common/turnup.lowturn_flag 单一事实源,阈值 lowturn_cfg 覆盖 LOWTURN_DEFAULTS)+ 图例(含今日旗亮只数;硬约束 B 对旗亮票不适用);默认 False = 逐字 parity。design 2026-08-21 §6.2。`

misread 块（`:326-333`）之后、`shuffle_seed` 之前加：

```python
    if lowturn_flag:
        from autoresearch.common.turnup import LOWTURN_DEFAULTS, LOWTURN_LABEL, lowturn_label
        lt_cfg = {**LOWTURN_DEFAULTS, **(lowturn_cfg or {})}
        df["lowturn"] = df.apply(lambda r: lowturn_label(r, lt_cfg), axis=1)
        cols = [*cols, "lowturn"]
        n_lt = int((df["lowturn"] == LOWTURN_LABEL).sum())
        header.append(
            f"lowturn 低位转强(确定性旗):距 60 日高 ≥{abs(lt_cfg['max_dist_high_60']):g}% 且 60 日涨幅 "
            f"<{lt_cfg['max_pct_60d']:g} ∧ 站回 MA20 且 MA5>MA10 ∧ 近 5 日为正 ∧ vol_ratio_20≥"
            f"{lt_cfg['min_vol_ratio_20']:g} ∧ 主力或 CMF 转正,且非健康上涨。**旗亮票不算「下跌趋势票」,"
            f"硬约束 B 不适用**;仍须过②资金真与⑥兑现机制,thesis 写明『低位转强』并答 D+1 买家。"
            f"今日旗亮 {n_lt} 只。")
```

`prepare_l3_table`：在 `l3_cfg = load_user_config().get("l3") or {}` 之后（仍在 `if two_pass is not False:` 块内）加：

```python
        lt_cfg = dict(l3_cfg.get("lowturn") or {})
        lowturn_on = bool(lt_cfg.get("enabled", False))
```

并在函数开头（`l3_cfg: dict = {}` 旁）初始化 `lt_cfg: dict = {}` 与 `lowturn_on = False`。`l3_table_md(...)` 调用（`:409-412`）加 `lowturn_flag=lowturn_on, lowturn_cfg=lt_cfg`。pass1 调用 `triage_l2_for_l3(df_full, target=pass1_target)` 暂不改（Task 3.3 改）。

旗亮只数不从 markdown 里数（字符串计数会被图例/表头误伤），在 `l3_table_md(...)` 调用之后、`if pass1_header:` 之前重算一次：

```python
    lowturn_counts: dict = {}
    if lowturn_on:
        from autoresearch.common.turnup import LOWTURN_DEFAULTS, lowturn_mask
        base_df = kept if two_pass else load_l3_input(date, root=base)     # two_pass 分支里 kept 已在
        lowturn_counts = {"lowturn_n": int(lowturn_mask(base_df, {**LOWTURN_DEFAULTS, **lt_cfg}).sum())}
```

函数末尾 `return {"codes": len(codes), "table_bytes": len(md), **pass1_counts}` 改为 `return {"codes": len(codes), "table_bytes": len(md), **pass1_counts, **lowturn_counts}`（关时 `lowturn_counts={}` → 返回形状与现在逐键相同）。

- [ ] **Step 4: 跑测试** → `uv run --no-sync python -m pytest tests/scan/test_l3_lowturn_flag.py tests/scan/test_l3_pass1.py tests/scan/test_l3_prepare.py tests/scan/test_l3_profile_lint.py tests/scan/test_l3_misread_flag.py -q` Expected: 全绿（两处 parity 测试未改仍绿 = lowturn 默认关确为逐字 parity）

- [ ] **Step 5: Commit**

```bash
git add autoresearch/scan/l3/prompt.py tests/scan/test_l3_lowturn_flag.py
git commit -m "feat(l3): L3 表 lowturn 低位转强旗列+图例(默认关=parity;prepare 跟随 l3.lowturn.enabled)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 3.3: pass1 规则③b —— lowturn 旗亮行强留（≤`pass1_cap`）

**Files:**
- Modify: `autoresearch/scan/l3/triage.py:22`（签名）、`:116-123`（③ 之后加 ③b）、`prompt.py` pass1 调用
- Test: `tests/scan/test_l3_pass1.py`（追加）

**Interfaces:**
- `triage_l2_for_l3(df, target=60, *, lowturn_cap: int = 0, lowturn_cfg: dict | None = None)`；`lowturn_cap<=0` = 现行为（parity）。③b：`turnup.lowturn_mask(d, cfg)` 为真且尚未 mandatory 的行，按 `order` 降序取前 `lowturn_cap` 进 mandatory，`_mark(i, "lane", "lowturn")`。

- [ ] **Step 1: 写失败测试（追加）**

```python
# ───────────────────────── ③b lowturn 强留(2026-08-21) ─────────────────────────


def _lt_row(code, composite, *, turn=True, **kw):
    # recall_channels 与其它行同为 composite:否则规则④的通道轮询会把它当「reversal 队列唯一成员」
    # 第一轮就捞进来,测不出③b 的作用;这里要测的是**分数最低也被强留**。
    r = _row(code, composite=composite, gbdt_score=composite, n_channels=1, recall_channels="composite")
    r.update({"pct_60d": -12.0, "dist_high_60": -22.0, "pct_5d": 4.0, "above_ma20": 1.0 if turn else 0.0,
              "ma5_gt_ma10": 1.0, "vol_ratio_20": 1.6, "main_inflow_yi": 0.8, "cmf_20": 0.05,
              "main_net_ratio": -0.01, "name": "甲"})
    r.update(kw)
    return r


def test_triage_lowturn_rows_forced_in_up_to_cap():
    rows = [_lt_row(f"{i:06d}", 1.0 + i) for i in range(5)]                      # 5 只旗亮、分数极低
    rows += [_row(f"{100 + i:06d}", composite=99.0 - i, gbdt_score=99.0 - i) for i in range(40)]
    kept, cut = triage_l2_for_l3(pd.DataFrame(rows), target=20, lowturn_cap=3)
    forced = kept[kept["selection_detail"] == "lowturn"]
    assert len(forced) == 3 and set(forced["code"]) == {"000004", "000003", "000002"}   # 按分数取前 3
    assert (forced["selection_reason"] == "lane").all()


def test_triage_lowturn_cap_zero_is_parity():
    rows = [_lt_row("000001", 1.0)] + [_row(f"{100 + i:06d}", composite=99.0 - i, gbdt_score=99.0 - i) for i in range(30)]
    a, _ = triage_l2_for_l3(pd.DataFrame(rows), target=10)
    b, _ = triage_l2_for_l3(pd.DataFrame(rows), target=10, lowturn_cap=0)
    pd.testing.assert_frame_equal(a, b)
    assert "000001" not in set(a["code"])


def test_triage_lowturn_respects_cfg_and_missing_cols():
    rows = [_lt_row("000001", 1.0, vol_ratio_20=1.1)] + [_row(f"{100 + i:06d}", composite=99.0 - i, gbdt_score=99.0 - i) for i in range(30)]
    kept, _ = triage_l2_for_l3(pd.DataFrame(rows), target=10, lowturn_cap=3)
    assert "000001" not in set(kept["code"])                                          # 1.1 < 1.2 不亮
    kept2, _ = triage_l2_for_l3(pd.DataFrame(rows), target=10, lowturn_cap=3, lowturn_cfg={"min_vol_ratio_20": 1.0})
    assert "000001" in set(kept2["code"])
    bare = pd.DataFrame([_row(f"{i:06d}", composite=99.0 - i, gbdt_score=99.0 - i) for i in range(30)])
    kept3, _ = triage_l2_for_l3(bare, target=10, lowturn_cap=3)                        # 缺旗列:不炸,不强留
    assert len(kept3) == 10
```

- [ ] **Step 2: 跑测试确认失败** → `uv run --no-sync python -m pytest tests/scan/test_l3_pass1.py -v -k lowturn` Expected: `TypeError: unexpected keyword 'lowturn_cap'`

- [ ] **Step 3: 实现**

签名改为 `def triage_l2_for_l3(df: pd.DataFrame, target: int = 60, *, lowturn_cap: int = 0, lowturn_cfg: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:`，docstring 加「③b lowturn 强留（2026-08-21）：`lowturn_cap>0` 时，`turnup.lowturn_mask` 为真且未入 mandatory 的行按 `order` 取前 cap 入 mandatory（`selection_reason=lane`、`detail=lowturn`）；`cap<=0` = parity」。在 ③ healthy 块之后、`mandatory_idx = ...` 之前加：

```python
    if lowturn_cap > 0:                                                  # ③b lowturn 强留(≤cap)
        from autoresearch.common.turnup import LOWTURN_DEFAULTS, lowturn_mask
        try:
            lt = lowturn_mask(d, {**LOWTURN_DEFAULTS, **(lowturn_cfg or {})})
        except Exception:  # noqa: BLE001 — 旗算不出(列缺/坏值)就不强留,不挡 pass1
            lt = pd.Series(False, index=d.index)
        cand = [i for i in d.index[lt.reindex(d.index).fillna(False).astype(bool)] if not mandatory.loc[i]]
        cand.sort(key=lambda i: order.loc[i], reverse=True)
        for i in cand[:int(lowturn_cap)]:
            mandatory.loc[i] = True
            _mark(i, "lane", "lowturn")
```

`prompt.py` pass1 调用改为 `triage_l2_for_l3(df_full, target=pass1_target, lowturn_cap=int(lt_cfg.get("pass1_cap", 8)) if lowturn_on else 0, lowturn_cfg=lt_cfg)`。

- [ ] **Step 4: 跑测试** → `uv run --no-sync python -m pytest tests/scan/test_l3_pass1.py tests/scan/test_l3_lowturn_flag.py -q` Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add autoresearch/scan/l3/triage.py autoresearch/scan/l3/prompt.py tests/scan/test_l3_pass1.py
git commit -m "feat(l3): pass1 ③b lowturn 旗亮行强留(≤pass1_cap;cap=0 parity)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 3.4: merge 守卫⑥ —— lowturn soft 1 席

**Files:**
- Modify: `autoresearch/scan/l3/merge.py:166-169`（守卫⑤之后）+ docstring 守卫序
- Test: `tests/scan/test_l3_merge_v3.py`（追加）

- [ ] **Step 1: 写失败测试（追加）**

```python
# ═══════════════════════ ⑥ lowturn soft 1 席(2026-08-21 低位转强波) ═══════════════════════


def test_lowturn_soft_quota_swaps_in_qualified_bench_candidate():
    """finalists 无 lowturn、bench 有 conviction≥55 的 lowturn → 换入,guard='lowturn_quota';
    不得吃掉 healthy/trend 行(protect_lanes)。"""
    judged = pd.DataFrame([
        _pick("HHHHHH", 60, lane="healthy", finalist=True),
        _pick("TTTTTT", 58, lane="trend", finalist=True),
        _pick("VVVVVV", 57, lane="value", finalist=True),
        _pick("LLLLLL", 56, lane="lowturn", finalist=False),
    ])
    fin, bench = merge_l3_finalists_v3(judged, budget=30, finalist_max=10)
    assert "LLLLLL" in set(fin["code"]) and fin[fin["code"] == "LLLLLL"].iloc[0]["guard"] == "lowturn_quota"
    assert set(bench["code"]) == {"VVVVVV"}                      # 被换出的是 value 尾票,不是 healthy/trend


def test_lowturn_quota_not_forced_below_55():
    judged = pd.DataFrame([
        _pick("AAAAAA", 70, lane="value", finalist=True),
        _pick("LLLLLL", 50, lane="lowturn", finalist=False),     # <55 不够格(守卫②同阈)
    ])
    fin, _ = merge_l3_finalists_v3(judged, budget=30, finalist_max=10)
    assert set(fin["code"]) == {"AAAAAA"}


def test_lowturn_already_present_no_swap():
    judged = pd.DataFrame([
        _pick("LLLLLL", 66, lane="lowturn", finalist=True),
        _pick("AAAAAA", 60, lane="value", finalist=True),
        _pick("MMMMMM", 58, lane="lowturn", finalist=False),
    ])
    fin, _ = merge_l3_finalists_v3(judged, budget=30, finalist_max=10)
    assert set(fin["code"]) == {"LLLLLL", "AAAAAA"} and (fin["guard"] == "").all()
```

- [ ] **Step 2: 跑测试确认失败** → `uv run --no-sync python -m pytest tests/scan/test_l3_merge_v3.py -v -k lowturn` Expected: 第一条 FAIL

- [ ] **Step 3: 实现** — `merge.py:168-169` 之后加：

```python
    fin_idx = _swap_lane_quota(m, conv, fin_idx, "lowturn", 1, "lowturn_quota",   # 守卫⑥(2026-08-21)
                               qualify_conv=55.0, protect_lanes={"healthy", "trend"})
```

docstring 守卫序加「⑥ **lowturn soft 1 席**：同④⑤机制，`lane=="lowturn"`、`qualify_conv=55`（与守卫②同阈，低位转强票 conviction 天然偏低，65 会让守卫恒空转）、`protect_lanes={"healthy","trend"}`；有够格候选才凑，无则 0。prompt 允许至多 2 席，确定性只兜底 1 席。design 2026-08-21 §6.4」。

- [ ] **Step 4: 跑测试** → `uv run --no-sync python -m pytest tests/scan/test_l3_merge_v3.py -q` Expected: 全绿（既有 ④⑤ 测试不受影响：它们没有 lowturn 行 → ⑥ 无操作）

- [ ] **Step 5: Commit**

```bash
git add autoresearch/scan/l3/merge.py tests/scan/test_l3_merge_v3.py
git commit -m "feat(l3): 守卫⑥ lowturn soft 1 席(qualify 55,保护 healthy/trend,无则不凑)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 3.5: `l3-rank.md` B 条例外 + G 条 + lane 枚举 + 锚

**Files:**
- Modify: `.claude/agents/l3-rank.md:30`（B 条末尾）、`:31` 之后新增 G 条、`:38`（lane 枚举）
- Test: `tests/test_agent_defs.py:110-119`（锚追加）

- [ ] **Step 1: 写失败测试** — `test_l3_rank_anchors_present` 的锚元组加 `"lowturn", "低位转强"`。

Run: `uv run --no-sync python -m pytest tests/test_agent_defs.py -v -k l3_rank` Expected: FAIL（缺锚）

- [ ] **Step 2: 改 agent def**

B 条行末追加（同一行内）：
`**例外（2026-08-21）**：表内 `lowturn` 旗亮的票不算下跌趋势票（已确定性核过：站回 MA20、短均线拐头、放量、主力/CMF 转正，且不属健康上涨），B 条对其不适用；仍须过②资金真与⑥兑现机制，thesis 必须写明『低位转强』并答 D+1 买家是谁。`

C 条之后新增一行：
`- **G. 低位转强席位（2026-08-21）**：finalist 中 `lowturn` 旗亮且 conviction≥55 的票 **1–2 席**（有够格候选才给，无则 0，不硬凑；确定性守卫只兜底 1 席）；这类票 `lane` 必须写 `lowturn`（与健康上涨分账追踪）。旗亮但②资金失真（main_dist 反号/微量）或写不出 D+1 买家的，照 E/F 与⑥处理，不因旗亮放水。`

`:38` 的 `lane`(trend|growth|reversion|accumulation|main|value|healthy) 改为 `lane`(trend|growth|reversion|accumulation|main|value|healthy|lowturn)。

- [ ] **Step 3: 跑测试** → `uv run --no-sync python -m pytest tests/test_agent_defs.py tests/test_skill_docs_refs.py tests/learning/test_product_shape_lint.py -q` Expected: 全绿（`retired_symbol_lint`/`workflow_literal_lint` 对本改动无新命中）

- [ ] **Step 4: Commit + 提醒**

```bash
git add .claude/agents/l3-rank.md tests/test_agent_defs.py
git commit -m "feat(agents): l3-rank B 条开低位转强例外 + G 条 1–2 席 + lane 枚举 lowturn(下个 session 生效)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

回复用户一句：**agent def 会话启动装载，本 session 派发的 l3-rank 仍是旧人设，下次扫描前需开新 session。**

---

## 批次 4 · P4 双尺分账

### Task 4.1: `relative_buy.md` 逐日表加 `lane` 列

**Files:**
- Modify: `autoresearch/learning/relative_ledger.py:291-340`（`row_from_decision`）、`:622-649`（render 表头/行）
- Test: `tests/learning/test_relative_ledger.py`（追加）

**Interfaces:**
- 行新增键 `"lane": str | None`（读当日 `_l3_judged.json` 该码的 `lane`，缺 → `None`）；**不**进 `_DECISION_IDENTITY_FIELDS`（身份不变 → 旧行不会被 `_freeze`，`roll()` 同身份整替后自然带上 lane）。

- [ ] **Step 1: 写失败测试（追加；沿用该文件现有的决策文档 fixture 工具，若无则按 `tests/scan/test_brief.py::_decision` 形状最小化构造）**

```python
def test_row_carries_l3_lane_when_judged_present(tmp_path):
    from autoresearch.learning import relative_ledger as rl
    scan = tmp_path / "scan"; day = scan / "2026-08-20"; day.mkdir(parents=True)
    (day / "_l3_judged.json").write_text(json.dumps(
        [{"code": "002081", "lane": "lowturn", "finalist": True, "conviction": 60}]), encoding="utf-8")
    doc = {"date": "2026-08-20", "mode": "active", "rule_version": "e6.v2.0", "ruler": "gap_c1_o2",
           "buys": [{"code": "002081", "basis": "relative", "rank": 1}],
           "candidates": [{"code": "002081", "name": "金螳螂", "eligible": True, "rank": 1,
                           "relative_decision_score": 0.7, "research_rating": "Underweight"}],
           "counts": {"candidates": 9, "eligible": 6}, "benchmark": {"market": {}, "sector": {}}}
    row = rl.row_from_decision(doc, scan)
    assert row["lane"] == "lowturn"
    assert "lane" not in rl._DECISION_IDENTITY_FIELDS          # 身份不变 → 不触发冻结
    md = rl.render([row])
    assert "| lane |" in md and "| lowturn |" in md


def test_row_lane_none_without_judged(tmp_path):
    from autoresearch.learning import relative_ledger as rl
    scan = tmp_path / "scan"; (scan / "2026-08-20").mkdir(parents=True)
    doc = {"date": "2026-08-20", "mode": "active", "buys": [], "candidates": [], "counts": {}, "benchmark": {}}
    assert rl.row_from_decision(doc, scan)["lane"] is None
```

- [ ] **Step 2: 跑测试确认失败** → `uv run --no-sync python -m pytest tests/learning/test_relative_ledger.py -v -k lane` Expected: KeyError `lane`

- [ ] **Step 3: 实现**

`relative_ledger.py` 加辅助函数（`row_from_decision` 之前）：

```python
def _l3_lane(scan: Path, date: str, code: str | None) -> str | None:
    """当日 `_l3_judged.json` 里该码的 L3 lane(分账用;缺文件/缺码 → None)。只读,不进决策身份。"""
    if not code:
        return None
    doc = _json_doc(scan / date / "_l3_judged.json")
    if not isinstance(doc, list):
        return None
    for entry in doc:
        if isinstance(entry, dict) and _code(entry.get("code")) == code:
            return str(entry.get("lane") or "") or None
    return None
```

`row_from_decision` 返回 dict 在 `"research_rating": ...` 之后加 `"lane": _l3_lane(scan, date, code),`。render 表头改为 `"| 日期 | 状态 | 影子 BUY | 评级 | lane | 📌 | score | ..."`，分隔行多一个 `|---|`，行里 `{row.get('research_rating') or '—'} | {row.get('lane') or '—'} | {'📌' ...`。

- [ ] **Step 4: 跑测试** → `uv run --no-sync python -m pytest tests/learning/test_relative_ledger.py -q` Expected: 全绿（43 条旧测试 + 2 新；若有测试逐字锁表头，按新表头更新并在 commit 写明）

- [ ] **Step 5: Commit**

```bash
git add autoresearch/learning/relative_ledger.py tests/learning/test_relative_ledger.py
git commit -m "feat(learning): relative_buy 账本逐日表加 L3 lane 列(分账;不进决策身份,不触发冻结)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 4.2: `--live` 观察报告进运维清单

**Files:**
- Modify: `.claude/skills/scan-market/STAGES.md`「运维细节」节（`:306` 起）

- [ ] **Step 1: 加一行** — 「低位转强活体双尺观察（只读，手动）：`uv run --no-sync python -m autoresearch.research.lowturn_precheck --live` → `reports_claude/research/lowturn_live.md`；≥10 个有 lowturn finalist 的成熟日后与 `channel_ledger`（reversal_confirm lane）一起提裁决提案。参考尺只观察，决策尺不变。」

- [ ] **Step 2: 跑文档测试** → `uv run --no-sync python -m pytest tests/test_skill_docs_refs.py tests/learning/test_product_shape_lint.py -q` Expected: 全绿

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/scan-market/STAGES.md
git commit -m "docs(scan): 运维清单加 lowturn --live 双尺观察报告(只读)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 批次 5 · 活体验收（首个真实扫描日，零代码）

- [ ] **5.1 全量测试**：`uv run --no-sync python -m pytest tests/ -q` → 0 failed（记下数字写进收尾汇报）。
- [ ] **5.2 prelude 前奏**：`uv run --no-sync python -m autoresearch.scan.prelude <date>`（≥20 分钟，**禁杀、别用 haiku 壳**）。验：`L1_scored_full.csv` 出现 `turnup.PANEL_COLS` 十列且 `vol_ratio_20` NaN 率 <5%（`run_health.json` `nan_rates`）；`L1_channels.csv` 里 `reversal_confirm` 行数 >0；`degraded.json` 无 `turnup_panel`。
- [ ] **5.3 L3**：`_l3_table.md` 含 `lowturn` 列与图例「今日旗亮 N 只」；`_l3_pass1_kept.csv` 的 `selection_detail` 出现 `lowturn`（若当日 N>0）；`_l3_judged.json` 出现 `lane=lowturn`（**新 session** 派发的 l3-rank 才认识 G 条）；`finalists.csv` 若有 `guard=lowturn_quota` 记录之。
- [ ] **5.4 L5**：`summary.md`「📉 今日漏斗读数」首行为「研究评级 ≥OW N 只 · 相对 BUY 1 只:…」（或 BLOCKED），**无**「空仓观望」；与 `brief.md` ③ 的票名一致；self_review 无「通道活性」warn（有则按 detail 先查列/门/取数）。
- [ ] **5.5 E6 不变**：`_relative_buy_decision.json` 的 `rule_version` 仍 `e6.v2.0`；`relative_buy.md` 逐日表新行带 `lane`。
- [ ] **5.6 记账**：把 5.2–5.5 的读数写进 memory（`l3-inverts-funnel-healthy-only-20260821` 更新「已实施」段），并在 spec §11 表打勾。≥10 扫描日后按 spec §8 走 `feedback` 裁决提案。

---

## 回滚速查

| 批次 | 一步回滚 |
|---|---|
| 0 | revert 两个 commit（无状态） |
| 1 | `frame._PANEL_LOOKBACK=20`（十列变 NaN + 降级记账，20 日组不变）；或 revert |
| 2a | revert（通道未上生产前无外显） |
| 2b | jsonc 摘回 `"reversal_confirm"` + 删 quota 键（一处）；探针 revert |
| 3 | jsonc `l3.lowturn.enabled=false`（旗列/强留同关，守卫⑥在无 lowturn lane 时天然无操作）+ revert `l3-rank.md` commit |
| 4 | revert（纯追加列） |
