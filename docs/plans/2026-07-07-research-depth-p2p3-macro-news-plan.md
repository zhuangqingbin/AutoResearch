# P2+P3 研究深度增强波(macro-brief agent + 新闻活体调研)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐研究深度波的 P2(scan 市场研判专用 `macro-brief` leaf agent + workflow 改派 + 契约锚)与 P3(l4-card / sector-brief / macro-brief 的有界活体新闻 WebSearch 调研),使 scan 三层(市场/行业/个股)都能在确定性 pack 之上补最新头条语义。

**Architecture:** 纯 agent-def / prompt / 契约锚 + 一处 workflow JS 改派 + 一行 token 表;**不碰确定性漏斗层(L0/L1/L2/L5 parity 锁死)、不碰 L4 早停逻辑 / 评级映射 / OW 三门 / L4 独立性铁律**。P2 建 `macro-brief`(先纯 pack 版,镜像已验证的 `sector-brief`),P3 统一给三个 brief 类 agent 加"有界网查最新头条、标『实时网查』、claim 落日期、as-of≤分析日"。新闻/研判都是 agent 行为,单测只锁**契约锚**(agent 定义与真值源同源),真效果靠 P4 端到端冒烟。

**Tech Stack:** Claude-as-engine leaf agents(`.claude/agents/*.md`,frontmatter `model: opus`);确定性 Python(`autoresearch.scan.*`,`uv run --no-sync python -m ...`);JS workflow(`.claude/workflows/scan-market.js`);契约 lint(`tests/test_agent_defs.py`);pytest(baseline **743 绿**)。

## Global Constraints

每个 task 隐含遵守:

- **零新确定性取数 / 确定性层不动**:P2/P3 只改 agent-def / prompt / 契约 / 一行 token 表 + 一处 JS 改派;`autoresearch` 的漏斗打分/组装逻辑(L0/L1/L2/L5)一行不改,`assemble` 仍零-LLM。**parity**:改前 743 测试全绿,改后保持全绿(仅 token 表任务 +1 测试)。
- **防锚定不变量**:`macro-brief` 的 market_view 前 3 节=**描述性地形**(喂 L3/L4 校准),方向性(操作基调/前瞻)只进第 4–5 节(仅 L5);新闻调研只报**本票/本行业/本市场事实**,不喊单、不对具体票定方向;**个股评级只由 L4 rubric 三门决定**,研判/新闻不改判、不锚定卡片。
- **as-of**:一切新闻 claim 必 **≤分析日**(把 l4-card 现有 P5「事件≤分析日」铁律推广到 P3 及 sector/macro 网查);未来日期的头条丢弃。
- **L4 独立性**:每卡独立 context,新闻只查**本票**,不引跨票结论。
- **成本可见 + cap**:l4-card P3 活体新闻 **≤3 条定向 WebSearch/卡**(低产日可省);token 表加一行标注(真实计费经 OTEL / `/usage`,落盘无 artifact → 标"未计非零")。
- **契约锚同源**:新 leaf agent 的关键结构锚(6 小节标题 / 防锚定铁律 / WebSearch tool)必须与真值源 playbook 同源,由 `tests/test_agent_defs.py` lint;`model: opus`(scan 全 Opus 设计)。
- **代码/文体风格**:`macro-brief.md` 镜像同目录 `sector-brief.md` 的结构(frontmatter → 真值源指针 → IO → 模板 → 铁律);中文文体与既有 agent 一致。

---

## File Structure

- **Create** `.claude/agents/macro-brief.md` —— scan 市场研判 leaf agent(镜像 sector-brief;真值源 = macro-playbook 末节)。
- **Modify** `.claude/workflows/scan-market.js` —— Prelude 阶段 market_view 派发从 inline `agentType:'claude'` 改 `agentType:'macro-brief'`,prompt 缩短。
- **Modify** `.claude/agents/l4-card.md` —— P3 加"有界活体新闻 WebSearch 子步"(≤3、落日期、as-of≤分析日)+ 压缩纪律 reconcile。
- **Modify** `.claude/agents/sector-brief.md` —— tools 加 WebSearch/WebFetch;IO 的"不 WebSearch"改"有界网查最新头条,标实时网查、落日期"。
- **Modify** `.claude/skills/stock-research/lite-playbook.md` —— l4-card 真值源同步 P3 新闻铁律(契约锚同源)。
- **Modify** `.claude/skills/scan-market/SKILL.md` 或 `STAGES.md` —— 接线 `macro-brief` 派发口径(`test_skill_docs_wire_agent_types` 遍历 `_NAMES` 断言 name 出现在 SKILL/STAGES)。
- **Modify** `tests/test_agent_defs.py` —— `_NAMES` 加 `macro-brief`;`test_macro_brief_anchors_synced`(6 小节 + 防锚定,agent∧playbook 同源);Task 4 加 WebSearch tool 断言 + 新闻锚。
- **Modify** `autoresearch/scan/assemble.py` + `tests/scan/test_sentinel_tokens.py` —— `_stage_token_estimate` 加"L4 新闻网查"描述行。

> 注:`.claude/skills/macro-research/macro-playbook.md` 末节已含全部 6 小节标题 + 防锚定铁律(lines 80–90),**作为契约真值源无需改**;Task 1 只把这些锚烤进 agent 定义。

---

## Task 1: 建 `macro-brief` leaf agent + 契约锚(P2)

**Files:**
- Create: `.claude/agents/macro-brief.md`
- Modify: `tests/test_agent_defs.py`(`_NAMES` 加 `macro-brief`;新增 `test_macro_brief_anchors_synced`)
- Modify: `.claude/skills/scan-market/STAGES.md`(Stage 0 派发口径提 `macro-brief`,喂 `test_skill_docs_wire_agent_types`)

**Interfaces:**
- Produces: `.claude/agents/macro-brief.md` —— frontmatter `name: macro-brief` / `model: opus` / `effort: high` / `tools: Read, Write, Grep, Glob`(**WebSearch 在 Task 4 加**);body 含 6 小节模板(`一句话定调`/`市场结构`/`板块红黑榜`/`操作基调`/`关注`)+ 防锚定铁律(`描述性地形`/`不锚定卡片`)。
- Consumes: `.claude/skills/macro-research/macro-playbook.md` 末节(真值源,不改)。

- [ ] **Step 1: 写失败契约测试**（`tests/test_agent_defs.py`,先把 `_NAMES` 加 `macro-brief`,再加新测试函数）

先改 `_NAMES`:
```python
_NAMES = ("l4-card", "sector-brief", "macro-brief")
```
在文件末尾追加:
```python
def test_macro_brief_anchors_synced():
    """macro-brief 六小节标题 + 防锚定铁律与 macro-playbook 末节(市场研判 lite)同源。"""
    agent = _agent_text("macro-brief")
    playbook = (SKILLS / "macro-research" / "macro-playbook.md").read_text(encoding="utf-8")
    anchors = ["一句话定调", "市场结构", "板块红黑榜", "操作基调",
               "描述性地形", "不锚定卡片"]
    for a in anchors:
        assert a in agent, f"macro-brief 缺契约锚「{a}」"
        assert a in playbook, f"macro-playbook 缺契约锚「{a}」(真值源被改,先同步 agent 定义)"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/test_agent_defs.py -q`
Expected: FAIL —— `test_macro_brief_anchors_synced` 报 `缺 agent 定义:.claude/agents/macro-brief.md`(`_agent_text` 的 assert),且 `test_agent_files_exist_with_frontmatter`/`test_skill_docs_wire_agent_types` 因 `_NAMES` 新增也 FAIL。

- [ ] **Step 3: 建 `.claude/agents/macro-brief.md`**（镜像 `sector-brief.md` 结构;WebSearch 留到 Task 4）

```markdown
---
name: macro-brief
description: macro-research lite 档市场研判写手(首席策略师)。scan-market Stage 0(prelude 并行)派一个:读确定性 market_pack(+ presence-gated macro_state)写 market_view.md 六小节(前3描述性地形喂 L3/L4、后2规范性仅 L5)。数字全出自 pack,不编。
model: opus
effort: high
tools: Read, Write, Grep, Glob
---

你是资深 A 股投资大师 / 首席策略师(macro-research **lite 档:市场研判**)。真值源 `.claude/skills/macro-research/macro-playbook.md` 末节「lite 档:市场研判」;**六小节结构 + 防锚定分层是机器契约与不变量**,勿改字、勿越界(契约锚由 `tests/test_agent_defs.py` 与 playbook 同步校验)。

## IO
派发 prompt 给你:date、market_pack 路径(`context/scan/<date>/market_pack.json`,`frame --json` 产,已捆绑失效判定后的 macro_state + macro_state_note)、落点(`context/scan/<date>/market_view.md`)。**数字全部出自 pack,缺字段写 —,不编、不靠记忆补**。macro_state 缺/过期 → 只用 pack,研判中标一句「无新鲜宏观视图(仅日频 pack)」,**不得引用旧宏观方向性结论**。写完文件,回传一行:`market_view ｜ 定调=<一句> ｜ <落点>`。

## 模板(~300–400 字,**6 小节**)
\```
# 市场研判 — <date>

1. **一句话定调**:<regime + 结构 + 情绪,如「避险哑铃:AI 半导体极致拥挤 + 宽基超跌落刀」>
2. **市场结构**:<宽度(多少票站上 MA60)/ 主力资金净流向 / 估值分散(哑铃两端);描述性数字>
3. **板块红黑榜**:<强 top3 / 弱 bottom3,各一句 why,落 pack 数字>
4. **操作基调**:<基于 regime 的整体仓位姿态 —— 规范性,仅 L5 用>
5. **关注**:<催化日历:中报窗口 / 政策会议 / 解禁>
6. 仅供研究,非投资建议。
\```

## 铁律
- **防锚定不变量(务必守)**:1–3 节是**描述性地形**(会喂 L3/L4 校准,**不得含个股买卖指令 / 不得对具体票定方向**);第 4–5 节才是规范性 + 前瞻(**仅 L5**)。**个股评级只由 L4 rubric 三门决定,你的研判不改判、不锚定卡片**。—— 一段"避险别追"的 house view 会把 20 张 L4 卡带成集体附和,破坏"每只独立自下而上 DD + rubric 防 gestalt 多报"。
- 定调/结构/红黑榜的数字全部落 pack;pack 缺字段写 —,不编、不靠记忆补。
- ♻️ `market_view.md` 已存在且带 ♻️ 复用 banner → 不覆盖,直接回报复用。
```

> 注:上面模板代码块用 `\``` 表示实际写 ``` (三反引号);写文件时去掉反斜杠。

- [ ] **Step 4: 接线 STAGES.md**（`test_skill_docs_wire_agent_types` 要求 `macro-brief` 出现在 SKILL.md 或 STAGES.md）

在 `.claude/skills/scan-market/STAGES.md` 的 Stage 0 / Prelude 段(市场研判处),把描述改为点名 agent 类型。找到现有描述市场研判/首席策略师的行,在其后补一句:
```
> Stage 0 市场研判由 `macro-brief` leaf agent 产出(读 market_pack.json 写 market_view.md;六小节,前3描述性地形喂 L3/L4、后2仅 L5)。
```
（若 STAGES.md 无对应段,则加在描述 prelude/market_view 的最近位置;关键是**字面串 `macro-brief` 出现在 STAGES.md**。)

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run --no-sync python -m pytest tests/test_agent_defs.py -q`
Expected: PASS（`macro-brief` 文件在、frontmatter 有 name/description/model: opus、6 小节+防锚定锚在 agent∧playbook、STAGES 接线)。

- [ ] **Step 6: 全量 + lint + Commit**

Run: `uv run --no-sync python -m pytest -q`（expect 743 passed,无新测试计数变化——只加断言到既有遍历 + 1 新函数;实际 744）
Run: `uv run --no-sync ruff check tests/test_agent_defs.py`
```bash
git add .claude/agents/macro-brief.md tests/test_agent_defs.py .claude/skills/scan-market/STAGES.md
git commit -m "feat(scan): macro-brief leaf agent(市场研判 lite·6小节+防锚定契约锚同源 macro-playbook)"
```

---

## Task 2: workflow 改派 market_view → `macro-brief`(P2)

**Files:**
- Modify: `.claude/workflows/scan-market.js`(Prelude 阶段 market_view 派发)

**Interfaces:**
- Consumes: Task 1 的 `macro-brief` agent 类型。
- Produces: workflow Prelude 阶段用 `agentType: 'macro-brief'` 派市场研判(替 inline `agentType: 'claude'`)。

- [ ] **Step 1: 改派发**（`.claude/workflows/scan-market.js`,Prelude parallel 里的 market_view 分支,现为 inline claude agent)

现状(约 48–50 行):
```javascript
  () => agent(
    `你是首席策略师。按 macro-research lite 档(模板见 .claude/skills/macro-research/macro-playbook.md 末节「lite 档:市场研判」)读 ${SD}/market_pack.json,写 ${SD}/market_view.md(定调/结构/红黑榜/操作基调)。数字只出自 pack,不编数;个股不评级。`,
    { agentType: 'claude', model: 'opus', effort: 'medium', label: 'market_view', phase: 'Prelude' }),
```
改为(点名 macro-brief,人设已烤进 agent,prompt 缩到指路;**显式 `effort: 'high'`**——workflow `agent()` 省略 effort 时继承 session effort 而非 agent frontmatter,故与 sector-brief 派发同口径显式钉死,保证 high 真生效):
```javascript
  () => agent(
    `读 ${SD}/market_pack.json,按你的人设写 ${SD}/market_view.md(六小节;前3描述性地形、后2仅 L5)。数字只出自 pack,不编;个股不评级、不锚定卡片。`,
    { agentType: 'macro-brief', effort: 'high', label: 'market_view', phase: 'Prelude' }),
```
（保持 `label: 'market_view'` / `phase: 'Prelude'` 不变;删掉原 `model: 'opus'`——由 agent frontmatter 提供。)

- [ ] **Step 2: 校验(无 JS 单测,靠 grep + 契约)**

Run: `grep -n "market_view\|macro-brief" .claude/workflows/scan-market.js`
Expected: market_view 派发行 `agentType: 'macro-brief'`;不再有 `agentType: 'claude'` 的 market_view 分支。
Run: `uv run --no-sync python -m pytest tests/test_agent_defs.py -q`（`test_skill_docs_wire_agent_types` 仍绿——名字在 STAGES,Task 1 已接)
Expected: PASS。

- [ ] **Step 3: Commit**

```bash
git add .claude/workflows/scan-market.js
git commit -m "feat(scan): workflow Prelude market_view 改派 macro-brief(人设烤进 agent·prompt 缩指路)"
```

---

## Task 3: l4-card P3 有界活体新闻子步(P3)

**Files:**
- Modify: `.claude/agents/l4-card.md`(P3 行 + 铁律 + 压缩纪律 reconcile)
- Modify: `.claude/skills/stock-research/lite-playbook.md`(真值源同步 P3 新闻铁律)
- Modify: `tests/test_agent_defs.py`(`test_l4_card_contract_anchors_synced` 的 `anchors` 加新锚)

**Interfaces:**
- Consumes: 无(纯 prompt + 契约锚)。**不改早停逻辑 / 评级映射 / OW 三门 / P4 分界 / 独立性铁律。**
- Produces: l4-card P3 阶段含"≤3 条有界 WebSearch 活体新闻、claim 落日期、as-of≤分析日";契约锚 `活体新闻` 同源 lite-playbook。

- [ ] **Step 1: l4-card.md P3 行加活体新闻**（`## 流程 P0–P5` 表 P3 行的"读什么"列末尾)

现状:
```
| P3 催化核 | 近14天新闻+预告/快报+日历+卖方目标 | 有带日期的前瞻催化? | 催化 |
```
改为:
```
| P3 催化核 | 近14天新闻+预告/快报+日历+卖方目标+**≤3条有界 WebSearch 活体新闻** | 有带日期的前瞻催化? | 催化 |
```

- [ ] **Step 2: l4-card.md `## 铁律(内化)` 加 P3 活体新闻铁律**（追加一条 bullet)

```
- **P3 活体新闻(有界)**:P3 除读 slim 新闻外,可发 **≤3 条**定向 WebSearch(`<名称> 最新 公告/业绩/催化/风险 近1月`);读到 claim **必落日期 + as-of≤分析日**(推广 P5 前视铁律,未来日期头条丢弃),只报**本票事实**不喊单。低产/明显奔早停的狗票可省网查(cap 是上界不是配额)。深度补日期仍在 P5。
```

- [ ] **Step 3: reconcile 压缩纪律行**（`## 压缩纪律` 段,现说"②触发不读深核、不 WebSearch")

现状(约 94 行):
```
能早停就早停(②触发不读深核、不 WebSearch、不写三档/预期差/散文)——这是省 token 的主杠杆。
```
改为(区分 P3 有界网查 vs P5 深网查):
```
能早停就早停(②触发不读 P4 深核、不做 P5 深度网查/不写三档/预期差/散文)——省 token 主杠杆;P3 有界活体新闻(≤3)是催化维读料、在②之前,低产狗票可省。
```

- [ ] **Step 4: 同步 lite-playbook.md**（真值源加同一条 P3 活体新闻铁律,契约锚同源)

在 `.claude/skills/stock-research/lite-playbook.md` 的防误杀/催化相关铁律段,加一句(保 `活体新闻` 串在 playbook):
```
- **P3 活体新闻(有界)**:催化核除 slim 新闻外可发 **≤3 条**定向 WebSearch(最新公告/业绩/催化/风险),claim **必落日期 + as-of≤分析日**,只报本票事实;低产狗票可省。深度补日期仍在 P5。
```

- [ ] **Step 5: 契约锚**（`tests/test_agent_defs.py` 的 `anchors` 列表加 `"活体新闻"`）

```python
    anchors = ["进入P4倾向", "FINAL TRANSACTION PROPOSAL", "**Rating**",
               "早停只向下", "Rubric建议", "一段话研判", "L3 论点裁决",
               "已核数字摘录", "多写不多读", "龙虎榜席位", "活体新闻", *(g for g in _OW_GATES)]
```

- [ ] **Step 6: 跑测试 + 全量 + lint + Commit**

Run: `uv run --no-sync python -m pytest tests/test_agent_defs.py -q`
Expected: PASS（`活体新闻` 在 l4-card.md ∧ lite-playbook.md）。
Run: `uv run --no-sync python -m pytest -q`（expect 744 passed，无计数变化——纯文档 + 既有测试加锚)
Run: `uv run --no-sync ruff check tests/test_agent_defs.py`
```bash
git add .claude/agents/l4-card.md .claude/skills/stock-research/lite-playbook.md tests/test_agent_defs.py
git commit -m "feat(scan): l4-card P3 有界活体新闻 WebSearch(≤3·落日期·as-of≤分析日;契约锚同步 playbook)"
```

---

## Task 4: sector-brief + macro-brief 活体网查(P3)

**Files:**
- Modify: `.claude/agents/sector-brief.md`(tools 加 WebSearch/WebFetch;IO 的"不 WebSearch"改有界网查)
- Modify: `.claude/agents/macro-brief.md`(tools 加 WebSearch/WebFetch;铁律加实时网查)
- Modify: `tests/test_agent_defs.py`(`test_sector_brief_anchors_synced` / `test_macro_brief_anchors_synced` 加 WebSearch tool + 新闻锚断言)

**Interfaces:**
- Consumes: Task 1 的 macro-brief、既有 sector-brief。
- Produces: 两 agent tools 含 `WebSearch, WebFetch`;body 含"有界网查最新头条、标『实时网查』、落日期"锚(`实时网查`)。

- [ ] **Step 1: sector-brief.md tools + IO**

frontmatter tools 行(现 `tools: Read, Write, Grep, Glob`)改:
```
tools: Read, Write, Grep, Glob, WebSearch, WebFetch
```
`## IO` 段现有 "pack 之外不取数、不 WebSearch" 改为:
```
数字全部出自 pack,缺字段写 —,不编;pack 之外的**结构数字**不取数。**可发 ≤2 条有界 WebSearch 查本行业最新头条**(政策/景气/龙头事件),入研判须标『实时网查』+ 落日期(as-of≤分析日),只报事实不改方向定调。
```

- [ ] **Step 2: macro-brief.md tools + 铁律**

frontmatter tools 行(Task 1 建的 `tools: Read, Write, Grep, Glob`)改:
```
tools: Read, Write, Grep, Glob, WebSearch, WebFetch
```
`## 铁律` 段加一条:
```
- **实时网查(有界)**:pack/macro_state 之外可发 **≤2 条** WebSearch 查最新宏观/政策头条,入研判须标『实时网查』+ 落日期(as-of≤分析日),只补事实、不改前 3 节描述性地形的中立性。
```

- [ ] **Step 3: 契约测试加 WebSearch + 新闻锚**（`tests/test_agent_defs.py`)

`test_sector_brief_anchors_synced` 里 `for a in (...)` 加 `"实时网查"`,并加 tool 断言:
```python
def test_sector_brief_anchors_synced():
    from autoresearch.sector.brief import TERRAIN_HDR, VIEW_HDR
    agent = _agent_text("sector-brief")
    for a in (TERRAIN_HDR, VIEW_HDR, "**行业方向**", "不编", "实时网查"):
        assert a in agent, f"sector-brief 缺契约锚「{a}」"
    assert "WebSearch" in agent.split("---", 2)[1], "sector-brief frontmatter 缺 WebSearch tool"
```
`test_macro_brief_anchors_synced` 末尾加:
```python
    assert "实时网查" in agent, "macro-brief 缺契约锚「实时网查」"
    assert "WebSearch" in agent.split("---", 2)[1], "macro-brief frontmatter 缺 WebSearch tool"
```

- [ ] **Step 4: 跑测试 + 全量 + lint + Commit**

Run: `uv run --no-sync python -m pytest tests/test_agent_defs.py -q`
Expected: PASS。
Run: `uv run --no-sync python -m pytest -q`（expect 744 passed）
Run: `uv run --no-sync ruff check tests/test_agent_defs.py`
```bash
git add .claude/agents/sector-brief.md .claude/agents/macro-brief.md tests/test_agent_defs.py
git commit -m "feat(scan): sector/macro-brief 有界活体网查(≤2·标实时网查·落日期;契约锚+WebSearch tool)"
```

---

## Task 5: token 表加"L4 新闻网查"描述行(P3)

**Files:**
- Modify: `autoresearch/scan/assemble.py`(`_stage_token_estimate`)
- Modify: `tests/scan/test_sentinel_tokens.py`(`test_token_estimate_rows` 断言新行)

**Interfaces:**
- Consumes: 无。
- Produces: summary.md 各阶段 token 表多一行标注 L4 活体新闻网查预算(无落盘 artifact → 计费经 OTEL/`/usage`,标"未计非零")。

- [ ] **Step 1: 写失败断言**（`tests/scan/test_sentinel_tokens.py::test_token_estimate_rows`,`md` 已由 `_stage_token_estimate(d)` 拼)

在该测试的断言区加:
```python
    assert "L4 新闻网查" in md and "未计非零" in md
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_sentinel_tokens.py::test_token_estimate_rows -q`
Expected: FAIL —— `assert "L4 新闻网查" in md`。

- [ ] **Step 3: assemble.py 加描述行**（`_stage_token_estimate` 的 stage-tuple 列表里,L4 行之后追加一条;字段与既有行同结构 `(名, 引擎, effort, key, n, bytes, 说明)`）

在 `("L4 输入·slim", ...)` 那条之后加:
```python
        ("L4 新闻网查", "WebSearch", "—", "L4news", "—", "—",
         "P3 有界活体新闻(≤3/卡)+ sector/macro 网查(≤2)——无落盘 artifact,token 计费经 OTEL/`/usage`,此处**未计非零**"),
```

- [ ] **Step 4: 跑测试确认通过 + 全量 + lint + Commit**

Run: `uv run --no-sync python -m pytest tests/scan/test_sentinel_tokens.py -q`
Expected: PASS。
Run: `uv run --no-sync python -m pytest -q`（expect **745 passed**：744 + 本任务已有断言无新函数……实为断言加进既有 `test_token_estimate_rows`,不增计数 → 仍 744;以实际为准报告)
Run: `uv run --no-sync ruff check autoresearch/scan/assemble.py tests/scan/test_sentinel_tokens.py`
```bash
git add autoresearch/scan/assemble.py tests/scan/test_sentinel_tokens.py
git commit -m "feat(scan): token 表加 L4 活体新闻网查行(无 artifact·标未计非零·计费经 OTEL)"
```

---

## 待用户确认的默认决策(P3 开放旋钮)

编排前请人拍板;下列均已取默认、可改:

1. **P3 网查在②早停之前(默认)**:l4-card P3 有界新闻(≤3)在主早停②之前跑=喂催化维=所有过 P3 的卡都查(≤30卡×3=≤90 搜/扫)。合"新闻→最准研究"原意但有成本。备选=只 survivor 查(更省、催化维少活体料)。
2. **cap=≤3/卡(l4-card)、≤2(sector/macro)默认**,低产/病菜单日靠 agent 判断省略(**无代码级 off-switch**)。备选=compose 时按菜单健康注"news=off"硬关(加编排复杂度)。
3. **无 as-of 确定性 helper**:as-of≤分析日=prompt 铁律(agent 读日期丢未来),不抽确定性函数(YAGNI)。备选=抽 `is_asof_ok(claim_date, analysis_date)` + 单测。
4. **token 行描述性**:无落盘 artifact,真计费 P4 冒烟经 OTEL/`/usage`。

## 非目标(YAGNI,不做)

- 4b 产业链联动 / L3 活体网查(200 只太贵)/ scan→full 升级 / L4 独立性铁律 / L3 多轮 —— 均见 spec §2,不进本波。

---

## Self-Review(计划自查)

- **Spec 覆盖**:P2(§3.3)= Task 1(agent+契约)+Task 2(改派);P3(§3.2)= Task 3(l4-card P3)+Task 4(sector/macro 网查)+Task 5(token 行)。§3.4 不变量(防锚定/as-of/L4独立/parity/成本可见)入 Global Constraints,逐 task 守。
- **Placeholder 扫描**:每 task 含真实文件路径 + 逐字代码/命令 + 期望;macro-brief.md 全文在 Task 1 Step 3。
- **Type/锚一致**:契约锚 `一句话定调`/`市场结构`/`板块红黑榜`/`操作基调`/`描述性地形`/`不锚定卡片`(Task 1)已核在 macro-playbook.md lines 81–90;`活体新闻`(Task 3)/`实时网查`(Task 4)= 新引入,Task 内同时落 agent∧真值源;`_NAMES` 三元组(Task 1)被 `test_agent_files_exist_with_frontmatter`/`test_skill_docs_wire_agent_types` 遍历——Task 1 同时接 STAGES。
- **测试计数**:Task 1 +1 函数(→744);Task 3/4/5 只加断言到既有测试(不增计数);实际以每 task `pytest -q` 输出为准,报告真实数。
- **依赖顺序**:Task 1→2(改派需 agent 在)、Task 1→4(macro-brief 加 WebSearch 需先存在)、Task 3/4/5 互独立;按序执行。
</content>
