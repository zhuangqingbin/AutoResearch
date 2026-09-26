# 日报引擎收敛 · 批 0–1(探针记录 + 文档瘦身 + 第二把尺常量)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `.claude/` 下的 skill/agent 文档从「changelog 冒充手册」收敛成「只回答现在怎么跑」的操作手册(契约行一字不动、由测试守),记录 headless 探针结论,并把 10 日尺立为代码里的第二把尺常量(不改主尺、不改行为)。

**Architecture:** 纯文档 + 一个常量 + 测试。每个 agent 文件成为其契约的唯一真身(playbook 变指针);历史/沿革/否决方向/运维细节搬到 `docs/`;新增字节预算测试让文档再次膨胀时会变红。零行为改动:任何 `autoresearch/` 逻辑不变,`capsule replay` 不受影响。

**Tech Stack:** Markdown、Python 3 + pytest(`uv run --no-sync pytest`)、git。

**Spec:** `docs/superpowers/specs/2026-09-26-daily-engine-consolidation-design.md` §4 A2 / §5 B0–B1 / §6 C0 / §4 A5。

## Global Constraints

- 机读契约字样逐字保留:`tests/test_agent_defs.py` 列出的全部锚(含 `_OW_GATES`、`_CARD_V4_MARKER`、`_RESEARCH_BODY_HDR`、`_MICRO_REPORT_HDR`、`_NO_DOSSIER_DECL`、`TERRAIN_HDR`)、`**入场**:`、`**早停**: 停于`、`[执行线]` 两行、七词停因词表、`FINAL TRANSACTION PROPOSAL`。
- 不改 `autoresearch/` 任何行为;本批唯一代码改动是 `common/ruler.py` 新增常量 + `session_agent/roles.py` 的引用列表。
- `.claude/` 全树不得出现活指令引用退役符号(`self_review` 的 retired-symbol lint);历史注可保留但须带「沿革/退役/历史」标记(`tests/test_skill_docs_refs.py`)。
- skill 文档里 `python -m autoresearch.<mod>` 引用必须可 import(`test_skill_doc_modules_importable`)。
- 两引擎共用同一份 agent 文件(`.codex/agents/*.toml` 是指针),不另写 Codex 副本。
- 冻结窗(buyability 批 4)内:本批零行为改动,允许。
- 每个任务单独提交;提交信息中文/英文皆可,末尾带 `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`。

## Review Focus

1. 早停卡模板里的 v4 标记行在 l4-card.md 内两处必须逐字节相同(`test_l4_card_v4_marker_full_line_byte_identical` 只剩单文件版后仍要守两处一致)。
2. `lite-playbook.md` 变指针后,`test_agent_input_boundary_hook.py` 仍引用该路径作为「研究 agent 可读的契约文档」样例:文件必须继续存在。
3. STAGES.md 搬走「运维细节」后,`coverage_pool` 与 `dossier.reconcile` 两个锚必须仍在 STAGES.md(`test_dossier_chain_wired_in_stages_doc`)。
4. SKILL.md 删配置大表后,`l4-intel`/`l4-card`/`sector-brief`/`macro-brief`/`l3-rank` 五个 agent 名至少在 SKILL 或 STAGES 出现一次(`test_skill_docs_wire_agent_types`/`test_l4_intel_wired_in_docs`)。
5. 新增字节预算测试必须对**当前提交前**的文件为红(变异验证:把旧文件 checkout 回来跑一次,必须 FAIL)。

---

### Task 0: 探针记录(C0 / A1-0 ①–④)

**Files:**
- Create: `docs/research/2026-09-26-headless-driver-probes.md`

**Interfaces:**
- Produces: 探针结论文档,供批 2 驱动器计划引用(§3.3 决策的 ⑤ 留待批 2 首任务)。

- [ ] **Step 1: 写探针记录**(内容 = 本会话实测,原样落盘)

```markdown
# headless 驱动器探针(2026-09-26,claude 2.1.283)

| # | 问题 | 结论 | 证据 |
|---|---|---|---|
| ① | 交互会话的 Bash 里能否起 `claude -p` | **能**(嵌套无限制) | session 06d99f98…,`--max-turns 1` 回 OK,$0.70(35k 1h cache 写 = 完整系统提示+工具+CLAUDE.md+记忆) |
| ② | `--agent l4-card` 是否装载项目 agent 定义 | **是**:model 走 frontmatter(`claude-opus-5-5`),PreToolUse hook `AGENT_INPUT_BOUNDARY` 在 headless 下拦截了对 `autoresearch/__init__.py` 的 Read | session 8c8e103f…,$0.26,cache 写 30.5k |
| ③ | transcript 落盘 | `~/.claude/projects/<slug>/<session-id>.jsonl`(`--session-id` 指定即得);结果 JSON 自带 `usage`/`total_cost_usd`/`modelUsage` | 同上 |
| ④ | `--agent l4-intel` 能否 WebSearch | **能**(sonnet-5,回了带 URL 的一行,$0.12) | session 60f9b52d… |
| ⑤ | session_agent host 模式真跑一场 | **未做**(需干净会话 + 一场真扫;批 2 首任务) | — |

**含义**:R1/R2/R3 解除。每次 headless 调用的固定成本 ≈ 一次 subagent 的 cache 写(23–35k),与今天相同;研究角色的 model/effort/tools/hook 全部由 `.claude/agents/*.md` 决定,驱动器不需要再解释一遍。

**待办**:⑤;`--max-budget-usd` 在订阅额度下是否生效未测(批 2 用 `--max-turns` + 墙钟兜底)。
```

- [ ] **Step 2: 提交**

```bash
git add docs/research/2026-09-26-headless-driver-probes.md
git commit -m "docs(research): headless driver probes — nested claude -p, --agent loads project agents + hooks, transcript path, WebSearch"
```

---

### Task 1: 四个 SKILL.md 的 session_v1 段去重(A2-1)

**Files:**
- Modify: `.claude/skills/scan-market/SKILL.md`、`.claude/skills/stock-research/SKILL.md`、`.claude/skills/macro-research/SKILL.md`、`.claude/skills/sector-research/SKILL.md`
- Test: `tests/test_skill_docs_refs.py`(新增用例)

- [ ] **Step 1: 写失败测试**

```python
def test_session_v1_boilerplate_not_duplicated_in_skills():
    """session_v1 编排入口只在 docs/session-agent/README.md 讲一遍;SKILL.md 只留一行指针。"""
    skills_root = ROOT / ".claude" / "skills"
    offenders = []
    for p in sorted(skills_root.glob("*/SKILL.md")):
        text = p.read_text(encoding="utf-8")
        if "## session_v1 编排入口" in text:
            offenders.append(str(p.relative_to(ROOT)))
        assert "docs/session-agent/README.md" in text, f"{p.name} 缺 session_v1 指针行"
    assert not offenders, "session_v1 整段仍重复于:\n" + "\n".join(offenders)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest tests/test_skill_docs_refs.py::test_session_v1_boilerplate_not_duplicated_in_skills -q`
Expected: FAIL(四个文件都含 `## session_v1 编排入口`)

- [ ] **Step 3: 四个文件各删掉整段 `## session_v1 编排入口`(从该标题到下一个 `>` 引用行或 `## 核心原理` 前),换成一行**

```markdown
> session_v1 编排入口(PILOT,默认仍 legacy):见 `docs/session-agent/README.md`;`finish` 后用 `session_agent verify-report --level full` 的机器结果交付。
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run --no-sync pytest tests/test_skill_docs_refs.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add .claude/skills/*/SKILL.md tests/test_skill_docs_refs.py
git commit -m "docs(skills): dedupe session_v1 entry block — one pointer line per skill"
```

---

### Task 2: 双源合并 —— agent 文件为唯一真身(A2-6)

**Files:**
- Modify: `.claude/skills/stock-research/lite-playbook.md`(整体改写为指针 + standalone 专属内容)
- Modify: `.claude/skills/macro-research/macro-playbook.md:70-101`(lite 节 → 指针)
- Modify: `.claude/skills/sector-research/sector-playbook.md:8-33`(lite 模板 → 指针)
- Modify: `autoresearch/session_agent/roles.py:38`(`stock.card` 只引 `.claude/agents/l4-card.md`)
- Modify: `tests/test_agent_defs.py`(`test_l4_card_contract_anchors_synced`、`test_l4_card_v4_marker_full_line_byte_identical`、`test_sector_brief_anchors_synced`、`test_macro_brief_anchors_synced` 去掉 playbook 半边,改为反向断言)
- Modify: `tests/session_agent/test_roles.py`

**Interfaces:**
- Produces: `lite-playbook.md` 仍存在(hook 测试引用路径),但不再含卡模板正文。

- [ ] **Step 1: 改测试(先红)** —— `test_l4_card_contract_anchors_synced` 只查 agent;新增反向断言:

```python
def test_playbooks_are_pointers_not_second_copies():
    """agent 文件是契约唯一真身;playbook 的 lite 段只许是指针(2026-09-26 A2-6)。"""
    from autoresearch.scan.self_review import _CARD_V4_MARKER
    lite = (SKILLS / "stock-research" / "lite-playbook.md").read_text(encoding="utf-8")
    assert ".claude/agents/l4-card.md" in lite, "lite-playbook 缺指向 l4-card.md 的指针"
    assert _CARD_V4_MARKER not in lite, "lite-playbook 仍含卡模板正文(第二份真身)"
    assert "FINAL TRANSACTION PROPOSAL" not in lite
    macro = (SKILLS / "macro-research" / "macro-playbook.md").read_text(encoding="utf-8")
    assert ".claude/agents/macro-brief.md" in macro
    assert "首席策略师 prompt(模板)" not in macro, "macro-playbook 仍含 lite prompt 模板"
    sector = (SKILLS / "sector-research" / "sector-playbook.md").read_text(encoding="utf-8")
    assert ".claude/agents/sector-brief.md" in sector
    assert "## 地形段(喂 L3/L4 · 描述性)\n- **链定位一句**" not in sector, "sector-playbook 仍含 lite 模板正文"
```

并把 `test_l4_card_contract_anchors_synced` 中 `assert a in playbook` 一行删除、`test_l4_card_v4_marker_full_line_byte_identical` 删除 playbook 三行断言、`test_sector_brief_anchors_synced`/`test_macro_brief_anchors_synced` 删除 playbook 断言(保留 agent 断言)。`tests/session_agent/test_roles.py` 改为 `assert card["instruction_refs"] == [".claude/agents/l4-card.md"]`。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest tests/test_agent_defs.py::test_playbooks_are_pointers_not_second_copies tests/session_agent/test_roles.py -q`
Expected: FAIL

- [ ] **Step 3: 改写 `lite-playbook.md`** 为下列全文(standalone 专属内容保留:落点、UZI 块说明、入场行 lint 边界、与 scan 衔接):

```markdown
# lite-playbook — stock-research lite 档(决策卡)

> **路径约定**:`$CTX`/`$RPT` = 本引擎工作区根(Claude→`context_claude`/`reports_claude`,Codex→`context_codex`/`reports_codex`)。数据湖 `lake/` 两引擎共享。

**卡片契约的唯一真身是 `.claude/agents/l4-card.md`**(流程 P0–P5、早停点、评分卡与五档映射、入场行、执行线、两张卡模板、机读口径、压缩纪律)。写卡时读它,不读本文;本文只记 lite 档在 **standalone**(单票直查 / 持仓复核)路径上与 scan L4 不同的几件事。

## standalone 与 scan L4 的差异
- **输入**:`$CTX/<ticker>_<date>_slim.md`(`autoresearch.analyze.harvest --slim`);standalone 顶部**没有**漏斗简报,P0 改为「读 slim 快照建立假设」,「L3 论点裁决」表写「standalone:无 L3 前提」。
- **落点**:`$RPT/analyze/<YYYYMMDD>_<HHMM>/<名称|TICKER>_lite.md`(A股→中文名);scan L4 的落点由任务包指定。
- **档案节**:standalone 无「📚 覆盖档案摘要」注入时,「研报体 / 微研报」写一行「档案未建」。
- **入场行 lint 只在 scan 路生效**:`card_contract_lint` 的唯一调用点在 `scan/report_sections.py`;standalone 卡缺入场行不会被挡下——它是约定不是硬门,也不影响任何决策(lite 卡从不被 E6 读取)。
- **UZI 增量块(A股 slim 已含,可引用)**:`A股原生财报`(5y ROE/毛利/负债率/分红)、`融资余额趋势`、`龙虎榜席位`(presence-gated via seats.csv)、`杀猪盘/派发风险`、`量价形态/吸筹·多日资金流`。`bias=吸筹` 进多头(底部放量须基本面背书)、`bias=派发` 进风险压级;机构上榜净买默认反指、游资净买作接力信号,席位只作技术·资金维校准。更深的席位叙事 DD 与 DCF 只在 full 档(`engine-playbook.md`)。

## 与 scan-market 的衔接
scan-market L4 对每只 finalist 派 `l4-card` agent(model/effort 由 `scan_config.jsonc` 的 `agents.l4_card` 经 `user_config.resolve_agent_config` 解释,本文不写档位),产物落任务包指定的 staging 路径,由 `autoresearch.scan.assemble` 发布;≥OW 的卡在发布前由两次独立复核取中位、只向下折回。
```

- [ ] **Step 4: 改写 `macro-playbook.md` 的「## lite 档:市场研判」节**为:

```markdown
## lite 档:市场研判(首席策略师 · scan-market Stage 0 / 日频 brief)

**prompt 模板、六小节结构、防锚定分层(1–3 节描述性地形喂 L3/L4;4–5 节规范性仅 L5)、有界实时网查(≤2 条,标『实时网查』)的唯一真身是 `.claude/agents/macro-brief.md`**。本节只记输入与复用事实:
- 输入 = `strategist_pack.json` 的 `pack` 段(`market_pack` 的单向投影,Wave10 A4:`sector_healthy_top3`/`run_contract`/`user_config` 进不来 —— 防锚定是数据级的,看不见就写不出)+ presence-gated `macro_state.json`(full 档产物;`today − as_of ≤ 7 天` 且 regime 未变才注入,由 `autoresearch.macro.state.load_macro_state` 判)。`frame <date> --json` 一条命令给全两者。
- 产物 `$CTX/scan/<date>/market_view.md`,三处复用:L3 表地形段、每张 L4 卡的 `market_context_block`、L5 置顶;缺文件 → L5 回退确定性脉搏。
- 个股评级只由 L4 rubric 决定,研判不改判、不锚定卡片。
```

(保留原节后的「## 已知数据坑」。)

- [ ] **Step 5: 改写 `sector-playbook.md` 的「## lite brief 模板」节**为:

```markdown
## lite brief(scan-market Stage 1;每行业一个 `sector-brief` agent)

**单段模板(`## 地形段`)、六条读数行、铁律(地形段禁方向词、数字全出 pack、♻️ banner 保留)与有界实时网查(≤2 条,标『实时网查』)的唯一真身是 `.claude/agents/sector-brief.md`**。本节只记接口事实:输入 `$CTX/sector/<date>/<行业>.json`(字段 n_market/n_l2/median_pct_60d/median_pe/pe_p25/pe_p75/median_pb/median_np_yoy/median_roe/main_pos_frac/main_net_sum_yi/healthy_n/median_winner/leaders/calendar),落点 `$CTX/scan/<date>/sector_briefs/<行业>.md`;标题 `## 地形段` 是 `sector/brief.py` 的 `TERRAIN_HDR`,勿改字。
```

(保留「## full 深研」及其后全部内容。)

- [ ] **Step 6: 改 `roles.py`**:`stock.card` 的 `instruction_refs` 改为 `[".claude/agents/l4-card.md"]`(删除 lite-playbook 一项)。

- [ ] **Step 7: 跑测试确认通过**

Run: `uv run --no-sync pytest tests/test_agent_defs.py tests/session_agent/test_roles.py tests/test_skill_docs_refs.py tests/test_agent_input_boundary_hook.py -q`
Expected: PASS

- [ ] **Step 8: 提交**

```bash
git add .claude/skills/stock-research/lite-playbook.md .claude/skills/macro-research/macro-playbook.md .claude/skills/sector-research/sector-playbook.md autoresearch/session_agent/roles.py tests/test_agent_defs.py tests/session_agent/test_roles.py
git commit -m "docs(skills): playbooks become pointers — agent files are the single source of each contract"
```

---

### Task 3: 字节预算守卫 + l4-card.md 瘦身(A2-4)

**Files:**
- Create: `tests/test_doc_budgets.py`
- Modify: `.claude/agents/l4-card.md`

- [ ] **Step 1: 写预算测试(对当前文件为红)**

```python
"""文档字节预算 —— skill/agent 文档是 prompt,不是 changelog(2026-09-26 A2)。

预算是上限不是目标;超了先问「这段是现在怎么跑,还是历史」。
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

BUDGETS_BYTES = {
    ".claude/agents/l4-card.md": 14_000,
    ".claude/agents/l3-rank.md": 8_000,
    ".claude/skills/scan-market/SKILL.md": 16_000,
    ".claude/skills/scan-market/STAGES.md": 26_000,
    ".claude/skills/stock-research/lite-playbook.md": 4_000,
}


@pytest.mark.parametrize("rel,budget", sorted(BUDGETS_BYTES.items()))
def test_doc_within_byte_budget(rel: str, budget: int):
    size = (ROOT / rel).stat().st_size
    assert size <= budget, f"{rel} = {size}B > 预算 {budget}B —— 先搬历史到 docs/,别调预算"


def test_no_session_v1_block_in_skills():
    for p in (ROOT / ".claude" / "skills").glob("*/SKILL.md"):
        assert "## session_v1 编排入口" not in p.read_text(encoding="utf-8"), p
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest tests/test_doc_budgets.py -q`
Expected: FAIL(l4-card 23,437B;l3-rank 13,741B;SKILL 36,441B;STAGES 63,113B)

- [ ] **Step 3: 瘦身 `l4-card.md`**(frontmatter 不动)。保留节:任务包协议 / 输入边界 / 铁律(每条只留规则句) / 流程 P0–P5 表 / 早停点 / 评级=评分卡派生(含入场行规则与指数调样生效前夜) / 机读口径 7 条 / 卡片模板 A、B 逐字 / 压缩纪律。删除:所有 `pr_2026*`、`fb_2026*`、`(Wave1)`、`(07-24 …)` 之类编号与日期;「执行线」下 5 行证据叙述压成 1 行「证据:`docs/research/2026-08-26-buy-owner-spikes/`」;「读盘边界一毫米不动」保留 1 处;「同族记忆」类引述删除。所有锚字样(见 Global Constraints)逐字保留。

- [ ] **Step 4: 跑契约与预算测试**

Run: `uv run --no-sync pytest tests/test_agent_defs.py tests/test_doc_budgets.py::test_doc_within_byte_budget -q -k "l4-card or l4_card"`
Expected: `test_agent_defs.py` 全 PASS;预算用例 l4-card PASS(其余三个仍 FAIL,后续任务修)

- [ ] **Step 5: 提交**

```bash
git add tests/test_doc_budgets.py .claude/agents/l4-card.md
git commit -m "docs(agents): slim l4-card.md to its contract; add doc byte-budget guard"
```

---

### Task 4: l3-rank.md 瘦身(A2-5)

**Files:**
- Modify: `.claude/agents/l3-rank.md`

- [ ] **Step 1: 瘦身**:必读文件三条(保留数字纪律与地形数字引用规则)、输入边界、6 维 rubric(①保留「不加分/次级 tiebreak」结论,删 spearman 读数段;④保留「辅证/表头作废/med_sent 不存在」;其余原样)、硬约束 A–I 每条只留规则句(证据一律改为「证据:<docs 路径>」一处指针)、输出契约与 conviction 定义逐字。锚保留:`兑现机制`、`≥70`、`mechanism`、`finalist`、`bench`、`≥75`、`宁缺毋滥`、`lowturn`、`低位转强`。

- [ ] **Step 2: 跑测试**

Run: `uv run --no-sync pytest tests/test_agent_defs.py::test_l3_rank_anchors_present tests/test_doc_budgets.py -q -k "l3"`
Expected: PASS

- [ ] **Step 3: 提交**

```bash
git add .claude/agents/l3-rank.md
git commit -m "docs(agents): slim l3-rank.md — rules stay, rationale moves to pointers"
```

---

### Task 5: STAGES.md 拆分(A2-3)

**Files:**
- Create: `docs/ops/scan-ops.md`(收「运维细节」全部小节 + 「计量与跨层校准」的账本列名断层段)
- Create: `docs/research/scan-negative-results.md`(收「已被实证否决的方向」「开放线头」「历史产物」「行为变更的入口」的退役清单表)
- Modify: `.claude/skills/scan-market/STAGES.md`

- [ ] **Step 1: 搬运**:上述节整段剪切到新文件(保留原文,不改写);STAGES.md 对应位置各留一行指针(`> 运维细节见 docs/ops/scan-ops.md`)。
- [ ] **Step 2: 压缩留下的节**:L1 通道表只留启用的 10 路 + 一行「停用 4 路及理由见 docs/research/scan-negative-results.md」;L2/L3/L4/L5 各节删除日期与 §编号引述、删除「回滚 = …」句(回滚杆已在 `scan_config.jsonc` 注释);「E6 v3.0/v4.0/v4.1」三段压成一张 6 行表(版本 / 总闸键 / 硬门 / 候选池 / 分级 / 出处)。必须保留:`coverage_pool`、`dossier.reconcile`、五个 agent 名、`TERRAIN_HDR` 语义、capsule 三结论一句。
- [ ] **Step 3: 跑测试**

Run: `uv run --no-sync pytest tests/test_agent_defs.py tests/test_skill_docs_refs.py tests/test_doc_budgets.py -q -k "STAGES or stages or dossier or wire or dangling or importable"`
Expected: PASS(STAGES ≤ 26,000B)

- [ ] **Step 4: 提交**

```bash
git add docs/ops/scan-ops.md docs/research/scan-negative-results.md .claude/skills/scan-market/STAGES.md
git commit -m "docs(scan): split STAGES.md — current mechanism stays, ops/history/negative results move to docs/"
```

---

### Task 6: scan-market SKILL.md 改写(A2-2)

**Files:**
- Modify: `.claude/skills/scan-market/SKILL.md`
- Modify: `.claude/skills/scan-market/scan_config.jsonc`(仅当 SKILL 大表里某键的「生效点」在 jsonc 注释中缺失时补一句注释)

- [ ] **Step 1: 改写**为 ≤120 行,节序:frontmatter(不动)/ 指针行(session_v1)/ 核心原理表(六段)/ 前置(路径约定、uv、token)/ **配置**(3 行:唯一事实源 `scan_config.jsonc`,注释即文档;新参数三件套;白名单外键 load 即 raise)/ 流程(0 开场 capsule begin 与 `trade_date`;0.1 prelude;0.5 frame + macro-brief;1–2.7 一句一步;3 L3;4 L4:Workflow 一次性全派 + Monitor `l4_watch`;5 L5 五条 `&&` 链 + CP7 播报规则;6 档案维护两条命令)/ 过程直播 CP0–CP7 表 / 铁律 8 条 / 常见坑 5 条。删除:配置大表、每键回滚杆、GATE4 拦什么(→ 一句指针到 `self_review.BRIEF_LINT_SEVERITY`)、法证 capsule 长段(→ 3 行:三结论互不替代 + verify 命令 + 「回放禁写 run 目录」)、所有事故日期与「用户裁定」注。保留可 import 的 `python -m` 命令,删除不再存在的。
- [ ] **Step 2: 跑测试**

Run: `uv run --no-sync pytest tests/test_agent_defs.py tests/test_skill_docs_refs.py tests/test_doc_budgets.py tests/scan/test_product_shape_lint.py -q`
Expected: PASS

- [ ] **Step 3: 提交**

```bash
git add .claude/skills/scan-market/SKILL.md .claude/skills/scan-market/scan_config.jsonc
git commit -m "docs(scan): rewrite SKILL.md as a runbook — config table and history leave the prompt"
```

---

### Task 7: 第二把尺常量(B0 + B1 第一步)

**Files:**
- Modify: `autoresearch/common/ruler.py`
- Test: `tests/common/test_ruler.py`(若不存在则新建)

- [ ] **Step 1: 写失败测试**

```python
def test_swing_ruler_is_a_second_first_class_ruler_not_a_replacement():
    from autoresearch.common import ruler
    assert ruler.MAIN_RULER == "gap_c1_o2"          # 08-05 裁定不动
    assert ruler.SWING_RULER == "fwd_10_oc"          # 09-26 裁定:第二把尺
    assert ruler.SWING_RULER != ruler.MAIN_RULER
    assert ruler.swing_entry_flag() == "buyable"     # D+1 开盘买腿旗
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest tests/common/test_ruler.py::test_swing_ruler_is_a_second_first_class_ruler_not_a_replacement -q`
Expected: FAIL(`AttributeError: SWING_RULER`)

- [ ] **Step 3: 实现**(紧跟 `MAIN_RULER` 定义之后)

```python
# 2026-09-26 用户裁定(daily-engine-consolidation §5 B0):新增**第二把一等尺**,不替换主尺。
# 隔夜尺管 T+1 尾盘入场 / T+2 开盘退出与 E6 BUY;10 日尺管「观察席」与持仓周级判断。
# 冻结窗内只影子 + 预注册普查;是否换 MAIN_RULER 留 B4 由用户裁,这里不预设。
SWING_RULER = "fwd_10_oc"        # D+1 开盘买 → D+10 收盘卖(与 research.edge_census.RULERS 同实现)


def swing_entry_flag() -> str:
    """10 日尺的入场旗列(D+1 开盘可买):沿用 fwd_*_oc 家族的 `buyable`。"""
    return _LEGACY_ENTRY_FLAG
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run --no-sync pytest tests/common/test_ruler.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add autoresearch/common/ruler.py tests/common/test_ruler.py
git commit -m "feat(ruler): SWING_RULER=fwd_10_oc as a second first-class ruler (shadow; MAIN_RULER unchanged)"
```

---

### Task 8: 冻结声明与索引(A5)

**Files:**
- Modify: `docs/PANORAMA.md:1-9`(头部加一行「2026-09-26 起冻结:不新增法证层/普查族;设计稿 …」)
- Modify: `CLAUDE.md`(「研究入口」节末加一行指向设计稿与 `docs/ops/scan-ops.md`)

- [ ] **Step 1: 编辑两处;Step 2: `uv run --no-sync pytest tests/test_skill_docs_refs.py -q` PASS;Step 3: 提交**

```bash
git add docs/PANORAMA.md CLAUDE.md
git commit -m "docs: freeze statement for the consolidation window + pointers to spec and ops doc"
```

---

### Task 9: 全量回归(两引擎)

- [ ] **Step 1**: `uv run --no-sync pytest -q -x -p no:cacheprovider 2>&1 | tail -5` → 全绿。
- [ ] **Step 2**: `AUTORESEARCH_ENGINE=codex uv run --no-sync pytest -q -x tests/test_agent_defs.py tests/test_codex_agent_defs.py tests/test_agent_input_boundary_hook.py tests/test_skill_docs_refs.py 2>&1 | tail -3` → 全绿。
- [ ] **Step 3**: 变异验证:`git stash` 之外用 `git show HEAD~6:.claude/agents/l4-card.md > /tmp/x && cp /tmp/x .claude/agents/l4-card.md && uv run --no-sync pytest tests/test_doc_budgets.py -q; git checkout .claude/agents/l4-card.md` → 预算测试必须 FAIL 一次。
- [ ] **Step 4**: 记忆文件更新(`daily-engine-consolidation-brainstorm-20260926.md` 追加「批 0–1 已落 main」)。

---

## 后续计划(不在本文件)

- 批 2–4(驱动器 host/headless、调度、送达):`docs/superpowers/plans/2026-09-2x-daily-engine-driver.md`,首任务 = A1-0 ⑤ session_agent host 模式真跑一场(干净会话)。
- 批 5(B2 普查 + B3 影子席):`docs/superpowers/plans/2026-09-2x-swing-ruler-shadow.md`。
