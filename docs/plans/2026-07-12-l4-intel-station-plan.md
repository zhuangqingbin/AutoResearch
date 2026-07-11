# L4 情报站(l4-intel)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每只新派 finalist 先由一个盲搜 sonnet·max 情报员把六面活体情报写成 `_l4_intel_<code>.md`（与 slim 预取同窗口并行），l4-card 的 P3/P4 改为 intel-first + 缺文件回退现行网查（presence-gated，parity 不破）。

**Architecture:** 确定性层不动；`dispatch_plan` 提前到 GATE3 之前并附 `meta`（code→name/sector），workflow 在 `parallel([GATE3, ...intel agents])` 里并行派 `l4-intel` 叶子 agent；任务包尾部加 intel 路径指针行（共享前缀之后，cache 契约不破）；config 顶层新键 `l4_intel:{enabled}` 默认关。

**Tech Stack:** Python (pandas/pytest, `uv run --no-sync`)、Claude Code workflow JS（`.claude/workflows/scan-market.js`）、叶子 agent 定义（`.claude/agents/*.md` + `tests/test_agent_defs.py` 契约锚）。

**设计真值源:** `docs/specs/2026-07-12-l4-intel-station-brainstorm.md`（用户改点：情报员 **sonnet·max**、搜索**六面全覆盖上界 ≤15**、v1 不做"已知事件标题清单"输入——接受与 slim 新闻少量重叠，卡侧以数字为准）。

## Global Constraints

- config 默认关（`l4_intel.enabled` 缺省 false = parity）；卡侧缺 intel 文件必须回退现行网查（presence-gated）。
- 情报员**结构性盲**：tools 只给 `Write, WebSearch, WebFetch`（无 Read/Grep/Glob → 物理上读不了 L3 论点）；prompt 只给 代码/名称/行业/分析日/输出路径。
- 任务包 cache 前缀契约（`tests/scan/test_l4_prompt_cache_prefix.py`）不得破：intel 指针行只能加在**逐卡尾部指针区**（共享块之后）。
- 测试命令统一 `uv run --no-sync python -m pytest <path> -q`；全量绿（当前基线 1146 通过）才算完。
- 每个 Task 结束 commit（信息含 feat/fix(scope) 前缀 + 一句中文说明）。
- 判断骨架（评分卡/OW 三门/早停/ensemble/防误杀铁律）与全卡网查硬上界 5 条的**文字锚**不动。

---

### Task 1: config 白名单 `l4_intel` + scan_config 样例

**Files:**
- Modify: `autoresearch/scan/user_config.py:74-79`（`_TOP_WHITELIST`/`_SUB_WHITELIST`）、`:121`（apply 透传 tuple）
- Modify: `.claude/skills/scan-market/scan_config.jsonc`（agents 块 + 顶层新块）
- Test: `tests/scan/test_user_config.py`

**Interfaces:**
- Produces: `load_user_config()` 接受顶层 `l4_intel: {"enabled": bool}`；`apply_to_scan_config` 把 `l4_intel` 整块挂到 `ScanConfig.l4_intel`。workflow（Task 5）经 `cfg.l4_intel?.enabled` 消费。

- [ ] **Step 1: 失败测试**（追加到 `tests/scan/test_user_config.py`，先读该文件模仿既有 fixture 风格——通常是写临时 jsonc 再 load）

```python
def test_l4_intel_whitelisted(tmp_path):
    p = tmp_path / "scan_config.jsonc"
    p.write_text('{"l4_intel": {"enabled": true}}', encoding="utf-8")
    cfg = load_user_config(p)
    assert cfg["l4_intel"]["enabled"] is True

def test_l4_intel_unknown_subkey_raises(tmp_path):
    p = tmp_path / "scan_config.jsonc"
    p.write_text('{"l4_intel": {"enable": true}}', encoding="utf-8")   # 拼写错
    with pytest.raises(ValueError, match="l4_intel"):
        load_user_config(p)

def test_l4_intel_applies_to_scan_config(tmp_path):
    from autoresearch.scan.config import ScanConfig
    sc = ScanConfig()
    apply_to_scan_config({"l4_intel": {"enabled": True}}, sc)
    assert sc.l4_intel == {"enabled": True}
```

- [ ] **Step 2: 跑测试确认失败**  `uv run --no-sync python -m pytest tests/scan/test_user_config.py -q` → 3 FAIL（未知顶层键 ValueError / 无 l4_intel 属性）
- [ ] **Step 3: 实现**——`_TOP_WHITELIST` 加 `"l4_intel"`；`_SUB_WHITELIST` 加 `"l4_intel": {"enabled"}`；`apply_to_scan_config` 的 tuple `("agents", "l4_gate", "pinned", "redteam_prob", "reuse")` 加 `"l4_intel"`。
- [ ] **Step 4: scan_config.jsonc**——agents 块 `"l4_card"` 行后加 `"l4_intel":  { "effort": "max" }`（带注释 `// 活体情报员(盲搜六面,sonnet 由 agent 定义默认)`）；文件顶层（agents 块后）加：

```jsonc
  // ── L4 活体情报站 ──(sonnet·max 盲搜情报员 ∥ slim 预取;卡侧缺文件自动回退卡内网查)
  // P1 波真跑验收后再置 true(设计稿 2026-07-12-l4-intel-station §6:单变量 A/B,账本 ≥10-20 日裁)。
  "l4_intel": { "enabled": false },
```

- [ ] **Step 5: 跑测试通过 + frame 冒烟**  `uv run --no-sync python -m pytest tests/scan/test_user_config.py -q` 全绿；`uv run --no-sync python -c "from autoresearch.scan.user_config import load_user_config; print(load_user_config())"` 不 raise 且回显含 l4_intel。
- [ ] **Step 6: Commit**  `git add -A && git commit -m "feat(config): l4_intel 白名单键+scan_config 样例(默认关=parity)"`

---

### Task 2: `dispatch_plan` 附 meta + 任务包 intel 指针行

**Files:**
- Modify: `autoresearch/scan/agents/l4_card.py`（`dispatch_plan` :729-763；`write_dispatch_pack` 尾部指针区 :718-720）
- Test: `tests/scan/test_dispatch_plan.py`、`tests/scan/test_l4_dispatch_pack.py`

**Interfaces:**
- Produces: `dispatch_plan(date, root)` 返回新增 `"meta": {code6: {"name": str, "sector": str}}`（仅 dispatch 码；finalists.csv 列 `name`/`sector` 直取，缺列容错为 `""`）。任务包尾部新指针行（Task 5 workflow 与 Task 4 卡契约消费）。
- 不变式: `dispatch`/`reused` 语义不变；旧消费方（不读 meta）零感知。

- [ ] **Step 1: 失败测试**（各追加到两个测试文件，先读文件复用其现成 scan_dir fixture/构造方式）

```python
# test_dispatch_plan.py
def test_dispatch_plan_meta_names(tmp_scan_dir):   # 用该文件既有构造 finalists.csv 的 fixture 改造
    # finalists.csv 至少含 code,name,sector 三列,一只 dispatch 票(有 _l4_prompt)一只 reused(有 details 卡)
    plan = dispatch_plan(DATE, root=tmp_scan_dir.parent)
    code = plan["dispatch"][0]
    assert plan["meta"][code]["name"] and "sector" in plan["meta"][code]
    assert all(c not in plan["meta"] for c in [r["code"] for r in plan["reused"]])

# test_l4_dispatch_pack.py
def test_prompt_has_intel_pointer(tmp_scan_dir_with_finalists):
    write_dispatch_pack(tmp_scan_dir_with_finalists)
    p = next(tmp_scan_dir_with_finalists.glob("_l4_prompt_*.md")).read_text(encoding="utf-8")
    assert "_l4_intel_" in p and "回退" in p
    # cache 契约:指针行必须在共享块之后(简单锚:出现在 "## L4 派发 —" 之后)
    assert p.index("_l4_intel_") > p.index("## L4 派发 —")
```

- [ ] **Step 2: 确认失败**  `uv run --no-sync python -m pytest tests/scan/test_dispatch_plan.py tests/scan/test_l4_dispatch_pack.py -q`
- [ ] **Step 3: 实现**——`dispatch_plan`: 初始化 `meta: dict[str, dict] = {}`；两处 `dispatch.append(code6)` 后各加 `meta[code6] = {"name": str(r.get("name", "") or ""), "sector": str(r.get("sector", "") or "")}`；返回 dict 加 `"meta": meta`（docstring 补一句）。`write_dispatch_pack`: 在 `f"- deep 深核:..."`（:719）之后插入：

```python
            f"- 活体情报:`context/scan/{date}/_l4_intel_{code6}.md`(若存在:P3 先读它作催化/题材/机构主料、"
            f"自发网查降 ≤1 条验证;缺文件=回退卡内网查,cap 原规则)",
```

- [ ] **Step 4: 测试通过 + 真数据冒烟**  两测试文件全绿；`uv run --no-sync python -m autoresearch.scan.agents.l4_card dispatch-plan 2026-07-09` 输出 JSON 含 `meta` 且 name 非空。**再跑 `uv run --no-sync python -m pytest tests/scan/test_l4_prompt_cache_prefix.py -q` 确认 cache 契约未破**。
- [ ] **Step 5: Commit**  `git commit -am "feat(l4): dispatch_plan 附 meta(name/sector)+任务包活体情报指针行(共享前缀后,cache 契约不破)"`

---

### Task 3: `l4-intel` 叶子 agent 定义 + 契约测试

**Files:**
- Create: `.claude/agents/l4-intel.md`
- Test: `tests/test_agent_defs.py`

**Interfaces:**
- Produces: agentType `l4-intel`（frontmatter `model: sonnet` / `effort: max` / `tools: Write, WebSearch, WebFetch`），输出契约段标题（事件段/题材段/机构段/互动段/负面增量段/声明行）被 Task 4 卡契约与 Task 6 lint 引用。**注意：`.claude/agents/` 新叶子下个 session 才装载（07-05 已知坑），本 session 不冒烟真派发。**

- [ ] **Step 1: 失败测试**（追加到 `tests/test_agent_defs.py`；**不要**把 l4-intel 加进 `_NAMES`——那个循环断言 `model: opus`）

```python
def test_l4_intel_def():
    """l4-intel:sonnet·max 盲搜情报员;结构性盲(无 Read 工具);六面契约锚在位。"""
    text = _agent_text("l4-intel")
    head = text.split("---", 2)[1]
    assert "model: sonnet" in head and "effort: max" in head
    assert "WebSearch" in head and "WebFetch" in head and "Write" in head
    assert "Read" not in head.replace("WebSearch", "").replace("WebFetch", ""), "结构性盲:不得有 Read/Grep/Glob"
    for a in ("事件段", "题材段", "机构段", "互动段", "负面增量段", "声明行",
              "as-of", "六面全查", "≤15", "净分", "只报本票事实", "只攒料不判断", "不编", "盲"):
        assert a in text, f"l4-intel 缺契约锚「{a}」"

def test_l4_intel_wired_in_docs():
    skill = (SKILLS / "scan-market" / "SKILL.md").read_text(encoding="utf-8")
    stages = (SKILLS / "scan-market" / "STAGES.md").read_text(encoding="utf-8")
    assert "l4-intel" in skill or "l4-intel" in stages, "scan 文档未接线 l4-intel(Task 7 落)"
```

- [ ] **Step 2: 确认失败**  `uv run --no-sync python -m pytest tests/test_agent_defs.py -q`（新 2 测试 FAIL，旧全 PASS）
- [ ] **Step 3: 写 agent 定义**  `.claude/agents/l4-intel.md` 全文如下（原样落盘）：

````markdown
---
name: l4-intel
description: scan-market L4 前置活体情报员(sonnet·max)。一只 finalist 的六面实时情报盲搜(公告正文/突发新闻/题材梯队/机构动向/互动易/负面增量),写 _l4_intel_<code>.md 机器契约供 l4-card P3/P4 读。盲于 L3 论点(输入只有代码/名称/行业/日期),防确认偏误查询。由 scan-market 步骤 4 与 slim 预取并行派发。
model: sonnet
effort: max
tools: Write, WebSearch, WebFetch
---

你是 A 股**活体情报员**:一只票 = 你一个独立 context 的六面实时情报采集。你**只攒料不判断**——不给评级、不喊多空、不写操作建议;判断属于下游分析员(l4-card)。

## 输入与盲性
派发 prompt 只给你:代码/名称/行业/分析日/输出路径。你**没有也不该有** L3 论点、conviction、漏斗评分——盲搜是防污染设计(查询不被上游假设带偏),你的工具也没有 Read(结构性盲)。

## 六面全查(硬要求:每面至少 1 条定向查询;查不到该面明写「无」,不许静默跳面)
1. **公告增量+正文解读**:近 5 个交易日新公告——重组/中标/合同(带金额量级)/回购增减持/业绩预告修正/停复牌/问询函回复。标题不够,要正文级含义(WebFetch 抓原文)。
2. **突发新闻**:近 3–5 日本票新闻(分析日当天盘后优先);点名本票或其主业的产业链价格异动(涨价/断供/扩产)。
3. **题材归属+梯队位置**:该票当下被市场归入什么概念/题材;该题材是否当前主线;票在梯队的位置(龙头/跟风/蹭);同题材今日涨停家数(查得到就写)。
4. **卖方/机构动向**:近 1 月研报家数与方向、目标价区间变化、机构调研动向。
5. **互动易/e互动**:近 1 周公司在投资者互动平台对热点问题的官方回复(题材发酵火种)。
6. **负面增量**:立案/警示函/媒体质疑/大股东风险的**新闻级增量**。

搜索预算:全卡 **≤15** 条 WebSearch(上界非配额——低产票每面查完 1 轮无料即收工);WebFetch 抓原文计入预算。查询用中文,带公司名与时间限定词。

## 铁律
- **as-of ≤ 分析日**:事件段每行的日期列 = 信息披露/发生日,必须 ≤ 分析日;晚于分析日的信息(前视)一律丢弃。事件**内容**里的未来时点(如「8-15 披露中报」)合法,写进事件行正文。
- **来源必落**:每行带站点名;扒不到原始日期的转载标「日期不明」并降净分一档。
- **只报本票事实**:不引荐股文/喊单帖;股吧观点不是事实不入表(互动易官方回复是事实,可入)。
- **不编**:六面查不到就写「无」;宁可空面不编造。「近 14 天无重大事件」是合法且有价值的输出。
- **净分口径**:−2 重大利空/−1 偏空/0 中性/+1 偏多/+2 重大利好——只标事件本身方向,不是你对股价的预测。

## 输出契约(写往派发 prompt 给的路径;机器可读;行数上界硬性,超了砍次要行)
```
# 活体情报 — <代码> <名称> @ <分析日>
〔intel v1·盲搜·as-of ≤ 分析日〕

## 事件段(≤10 行)
| 日期 | 事件(一行,含量级) | 源 | 2日内可发酵? | 净分 |
|---|---|---|---|---|

## 题材段(≤3 行)
归属:<题材/概念 或 无> ｜ 主线?:<是/否/边缘> ｜ 梯队位置:<龙头/跟风/蹭/不适用> ｜ 同题材今日强度:<涨停家数 或 未查到>

## 机构段(≤3 行;无研报动向整段写「无」)
研报:<近1月 N 家,方向> ｜ 目标价区间:<低–高 或 无> ｜ 调研:<动向 或 无>

## 互动段(≤2 行)
## 负面增量段(≤2 行)

## 声明行
网查 <N> 条 ｜ 六面覆盖:<公告=有料/无 · 新闻=… · 题材=… · 机构=… · 互动=… · 负面=…> ｜ as-of ≤ <分析日> ｜ 本文仅事实采集,无判断
```
最终回传只给一行紧凑结果(code + 事件行数),正文写文件、不要贴回。
````

- [ ] **Step 4: 测试通过**  `uv run --no-sync python -m pytest tests/test_agent_defs.py -q`（`test_l4_intel_wired_in_docs` 若因文档未接线仍 FAIL → 在本 Task 先给 `STAGES.md` L4 节加一行占位真句：「活体情报:`l4-intel`(sonnet·max 盲搜六面)∥ slim 预取,config `l4_intel.enabled` 默认关」——Task 7 再写全）
- [ ] **Step 5: Commit**  `git commit -am "feat(agents): l4-intel 盲搜情报员叶子(sonnet·max·结构性盲)+契约测试"`

---

### Task 4: 卡侧 intel-first 契约（l4-card.md + lite-playbook.md 同步）

**Files:**
- Modify: `.claude/agents/l4-card.md:27-28`（P3 活体新闻/机构面网查两条铁律）、`:36`（P0–P5 表 P3 行）
- Modify: `.claude/skills/stock-research/lite-playbook.md`（真值源,同步同两处——先 grep「活体新闻」「机构面网查」定位）
- Test: `tests/test_agent_defs.py::test_l4_card_contract_anchors_synced`（anchors 列表加 `"活体情报"`）

**Interfaces:**
- Consumes: Task 2 任务包指针行、Task 3 输出契约段名。
- 不变式: 锚「活体新闻」「机构面网查」文字保留;全卡网查硬上界 5 条字样不动;防误杀(P3 后才许早停)语义不动。

- [ ] **Step 1: 失败测试**——anchors 列表（test_agent_defs.py:41-45）加 `"活体情报"`；跑 `uv run --no-sync python -m pytest tests/test_agent_defs.py::test_l4_card_contract_anchors_synced -q` → FAIL（两文件都缺）。
- [ ] **Step 2: 改 l4-card.md 两条铁律**（lite-playbook.md 对应句做**相同**改写）：

P3 条（:27）改为：

```
- **P3 活体新闻(有界)**:任务包若列出**活体情报** `_l4_intel_<code>.md` 且文件存在 → P3 先读它(事件/题材/互动/负面段=催化维主料;它是盲搜事实采集,与 slim/简报数字矛盾时以数字侧为准),自发网查降为 **≤1 条**验证用;缺 intel 文件回退原规则:P3 除读 slim 新闻外,可发 **≤3 条**定向 WebSearch(`<名称> 最新 公告/业绩/催化/风险 近1月`);读到 claim **必落日期 + as-of≤分析日**(推广 P5 前视铁律,未来日期头条丢弃),只报**本票事实**不喊单。低产/明显奔早停的狗票可省网查(cap 是上界不是配额)。深度补日期仍在 P5。
```

机构面条（:28）改为：

```
- **机构面网查(有界)**:intel 文件在场 → 直接读其机构段作旁证,本条网查免发;缺文件回退:仅当简报带「机构面」行、或你已进入 P4 时,可发 **≤2 条**定向 WebSearch(`<名称> 研报 评级 近1月` / `<名称> 机构调研`)。结果必落来源+日期(as-of≤分析日),只作旁证——不得替代简报数据行、不单独改评级、不越过 rubric 三门。与 P3 活体新闻的条数**分开计数**(全卡网查硬上界 5 条)。
```

P0–P5 表 P3 行「读什么」列改为：`近14天新闻+预告/快报+日历+卖方目标+**活体情报 _l4_intel(若在场;缺则 ≤3条有界 WebSearch 活体新闻)**`

- [ ] **Step 3: 测试通过**  `uv run --no-sync python -m pytest tests/test_agent_defs.py -q` 全绿（含双文件同步断言）。
- [ ] **Step 4: Commit**  `git commit -am "feat(l4): 卡侧 intel-first 契约——P3/机构面先读活体情报缺则回退(l4-card+lite-playbook 同步,锚加'活体情报')"`

---

### Task 5: workflow 接线（dispatch-plan 前移 + 情报站 ∥ GATE3）

**Files:**
- Modify: `.claude/workflows/scan-market.js:7`（meta.phases L4 detail）、`:142-158`（L4 段重排）

**Interfaces:**
- Consumes: Task 1 `cfg.l4_intel.enabled`、Task 2 `plan.meta`、Task 3 agentType `l4-intel`。
- 不变式: GATE3 失败仍抛错中止；intel agent 失败/null 不阻断（卡侧回退）；卡派发段(:160-166)与 ensemble 段一字不动。

- [ ] **Step 1: 改 meta.phases**  `{ title: 'L4', detail: 'slim-harvest ∥ 情报站(GATE3) → 决策卡并发' }`
- [ ] **Step 2: 重排 L4 段**——把现 `:142-158`（GATE3 → dispatch-plan → PLAN/log）改为（PLAN schema 移到 GATE3 前、加 meta 字段；卡派发之后的代码不动）：

```js
// 派发计划(确定性)提前到 GATE3 之前:情报站要与 slim 预取同窗口并行(只读 finalists/_l4_prompt 存在性,与 slim 无依赖)
const PLAN = { type: 'object', required: ['dispatch'],
  properties: { dispatch: { type: 'array', items: { type: 'string' } },
    meta: { type: 'object' },
    reused: { type: 'array', items: { type: 'object',
      properties: { code: { type: 'string' }, rating: { type: 'string' } } } } } }
const plan = await gate('dispatch-plan', `${R} autoresearch.scan.agents.l4_card dispatch-plan ${date}`, PLAN, 'L4')
if (!plan) throw new Error('dispatch-plan 无返回')
// 活体情报站(design 2026-07-12 §3;config 默认关):盲搜 sonnet·max,每票一个,∥ GATE3 slim 预取。
// agent 失败→null 不阻断——卡侧 presence-gated 缺文件自动回退卡内网查。
const intelOn = !!(cfg.l4_intel && cfg.l4_intel.enabled)
const INTEL = { type: 'object', required: ['code'],
  properties: { code: { type: 'string' }, events: { type: 'integer' } } }
const intelThunks = intelOn ? plan.dispatch.map((code) => () => agent(
  `活体情报采集:${code} ${(plan.meta?.[code]?.name) || ''}(${(plan.meta?.[code]?.sector) || '行业未知'})· 分析日 ${date}。按你的人设六面全查(≤15 条),写 ${SD}/_l4_intel_${code}.md;返回 code 与事件行数 events。`,
  { agentType: 'l4-intel', effort: cfg.agents?.l4_intel?.effort ?? 'max',
    ...(cfg.agents?.l4_intel?.model ? { model: cfg.agents.l4_intel.model } : {}),
    label: `intel:${code}`, phase: 'L4', schema: INTEL })) : []
if (intelOn) log(`🕵️ 情报站并行:${plan.dispatch.length} 票盲搜(sonnet·max,与 slim 预取同窗口)`)
// GATE3:批量 slim 失败响亮(harvest-slim 打印 JSON + 非零退出)—— intel 与之并行,barrier 后再派卡
const [g3, ...intelRes] = await parallel([
  () => gate('GATE3', `${R} autoresearch.scan.agents.l4_card harvest-slim ${date}`, OK, 'L4'),
  ...intelThunks,
])
if (!g3 || !g3.ok) throw new Error(`GATE3 失败(slim<8KB 或 .SH):${g3 ? g3.reason : 'no return'}`)
if (intelOn) log(`🕵️ 情报站 ✓ ${intelRes.filter(Boolean).length}/${plan.dispatch.length}(缺稿卡自动回退网查)`)
log('GATE3 ✓ 全 slim >8KB(surface)')
```

（原 `:146-154` 的 dispatch-plan 注释与调用、原 `:149-152` PLAN 定义随本块整合删除；原 `:155-158` 的 CARD/log 起卡派发段保持原位不动。）

- [ ] **Step 3: 语法冒烟**  `node --check .claude/workflows/scan-market.js` → 无输出（exit 0）。
- [ ] **Step 4: 回归**  `uv run --no-sync python -m pytest tests/scan/test_l4_prompt_cache_prefix.py tests/scan/test_ensemble_fold.py -q` 全绿（引用 workflow 文本的契约测试若断言行号/片段,按新文本修断言,语义不变）。
- [ ] **Step 5: Commit**  `git commit -am "feat(workflow): L4 情报站接线——dispatch-plan 前移+intel ∥ GATE3(默认关;agent 失败不阻断)"`

---

### Task 6: 计量（token 表 intel 行）+ as-of 前视机检（self_review advisory）

**Files:**
- Modify: `autoresearch/scan/assemble.py:432,448-478`（token 估算表加 `_l4_intel_*` 行——先读 :420-480 照 slim 行(:455 附近)的元组形状镜像一行,说明文案 `l4-intel 盲搜落稿(每票活体情报;未启用=0)`；:432 的 l4t1 glob 列表**不动**——那是 L4 输出侧计时锚）
- Modify: `autoresearch/learning/self_review.py`（advisory 检查 `intel_future_dates`——先读该文件 :1-80 找 `add(check, sev, detail, code)` 的调用惯例与 scan_dir/date 变量名）
- Test: `tests/scan/test_assemble.py`（token 表含 intel 行断言）、`tests/learning/test_self_review.py`

**Interfaces:**
- Consumes: Task 3 输出契约（事件段表格行首格式 `| YYYY-MM-DD |`）。
- 语义: **只查事件段日期列**（事件内容里的未来催化时点合法）；命中 → `sev="warn"`（advisory 不挡发布）。

- [ ] **Step 1: 失败测试**

```python
# test_self_review.py 追加(用该文件既有 tmp scan_dir fixture)
def test_intel_future_dates_warn(tmp_scan_dir):
    (tmp_scan_dir / "_l4_intel_000001.md").write_text(
        "# 活体情报 — 000001 @ 2026-07-09\n## 事件段(≤10 行)\n"
        "| 2026-07-20 | 未来事件 | x | 是 | +1 |\n## 题材段\n无\n", encoding="utf-8")
    res = self_review(tmp_scan_dir)   # 按该文件既有入口函数名/返回形状适配
    assert any(c["check"] == "intel_future_dates" and c["sev"] == "warn" for c in res["checks"])

# test_assemble.py 追加
def test_token_table_intel_row(tmp_scan_dir_ready):
    (tmp_scan_dir_ready / "_l4_intel_000001.md").write_text("# 活体情报\n", encoding="utf-8")
    md = build_report(tmp_scan_dir_ready)   # 按该文件既有组装入口适配
    assert "L4 输入·情报" in md
```

- [ ] **Step 2: 确认失败** → **Step 3: 实现**。self_review 检查体（变量名按实际适配）：

```python
    # intel as-of 前视机检(advisory):只查事件段表格行的日期列;事件正文里的未来催化时点合法
    import re as _re
    for p in sorted(scan_dir.glob("_l4_intel_*.md")):
        in_events, future = False, []
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.startswith("## 事件段"):
                in_events = True
                continue
            if in_events and line.startswith("## "):
                break
            m = _re.match(r"\|\s*(\d{4}-\d{2}-\d{2})\s*\|", line) if in_events else None
            if m and m.group(1) > date_str:
                future.append(m.group(1))
        if future:
            add("intel_future_dates", "warn",
                f"{p.name} 事件段含晚于扫描日的日期:{','.join(future[:3])}",
                code=p.stem.replace("_l4_intel_", ""))
```

- [ ] **Step 4: 测试通过**  `uv run --no-sync python -m pytest tests/scan/test_assemble.py tests/learning/test_self_review.py -q`
- [ ] **Step 5: Commit**  `git commit -am "feat(scan): token 表 L4 输入·情报行+self_review intel 前视 advisory(只查事件段日期列)"`

---

### Task 7: 文档接线 + 全量回归 + 设计稿状态收口

**Files:**
- Modify: `.claude/skills/scan-market/SKILL.md`（步骤 4 加情报站段——**编辑前重读该文件**,外部会改）、`.claude/skills/scan-market/STAGES.md`（L4 节,替换 Task 3 占位句为完整两行）
- Modify: `docs/specs/2026-07-12-l4-intel-station-brainstorm.md`（状态行改「已批准开发·本计划实施中」;§3 情报员改 sonnet·max/≤15/无 Read 结构性盲/v1 无已知标题清单）

**Interfaces:** 满足 Task 3 的 `test_l4_intel_wired_in_docs`；SKILL 步骤 4 与 workflow 行为一字同源。

- [ ] **Step 1: SKILL.md 步骤 4** 加一段（放 slim 预取/派发说明旁）：

```
- **活体情报站**(config `l4_intel.enabled`,默认关):dispatch-plan 前移,每只新派票并发一个 `l4-intel`(sonnet·max,结构性盲——只给码/名/行业/日期)与 slim 预取同窗口盲搜六面,落 `_l4_intel_<code>.md`;卡 P3 先读 intel、自发网查降 ≤1 验证,缺文件自动回退卡内网查(presence-gated,parity 不破)。裁决:stage_eval+账本 ≥10–20 日,P1 波验收后才开。
```

- [ ] **Step 2: 全量回归**  `uv run --no-sync python -m pytest -q` → 全绿（基线 1146 + 新增 ~8）。
- [ ] **Step 3: 真链冒烟**（FN-1 纪律:不手搓中间态）——对已有数据日 `2026-07-09`：`uv run --no-sync python -m autoresearch.scan.agents.l4_card prompts 2026-07-09 && grep -l "_l4_intel_" context/scan/2026-07-09/_l4_prompt_*.md | head -3`（指针行真进任务包）；`node --check .claude/workflows/scan-market.js`。
- [ ] **Step 4: Commit**  `git commit -am "docs(scan): SKILL/STAGES 接线 l4-intel 情报站+设计稿状态收口"`

---

## 验收清单（wave 完成定义）

1. 全量 pytest 绿；`node --check` 过。
2. `dispatch-plan 2026-07-09` 真命令回 meta；任务包含 intel 指针行；cache prefix 测试绿。
3. `l4_intel.enabled` 默认 false（parity）：不开 = 现行为逐字节不变（例外两处观测面：任务包多一行指针——卡侧缺文件走回退分支；summary token 表恒渲染「L4 输入·情报」0 行）。
4. 已知坑复述：`.claude/agents/l4-intel.md` **下个 session 才装载**；首次真跑（P1 验收后置 true）按设计稿 §6 冒烟三查（WebSearch 并发限频/中文源可达率/intel 空稿率）。
