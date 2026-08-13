# 复盘自改 skill 能力下线设计稿（B 档：毕业提名制 + prompt_patch 退役 + 禁令探针）

- **日期**：2026-08-13
- **状态**：设计定稿，**零实施**（用户裁定：不开发，只落详细开发文档）
- **用户裁定**：「下掉每次自动复盘修改 skill 的能力」，范围取 **B 档** = 断掉复盘/反馈流程通往 `.claude/**` 的全部文件写路径 + **退役 prompt_patch 整条管线**；数据侧学习（权重重标定、lessons 注入等）全保留。毕业提名复用 `proposals.jsonl` 看板（不另建 store）。
- **调研方法**：两路全仓实证扫描（learning 包 44 模块逐个读写盘点 + `.claude`/代码/git 历史写路径穷举），关键文件全读（retro-playbook 169 行、feedback-playbook 87 行、`feedback_store.py:505-696`）。

---

## 1. 背景与动机

用户体感：「每次自动复盘都在改 skill」。复盘（scan-retro / t1-review / feedback）驱动的 skill 文本修改近 3 个月真实发生 **11 例**（git 实证见 §2.3），带来两类成本：

1. **漂移风险**：skill/playbook 是研究流程的契约文件，复盘会话顺手改文本 = 契约在无专门评审的场合漂移（本仓已有教训：skill 文档被外部改，编辑前必须重读；lint 报警 3/3 是指令自身没写清）。
2. **审计负担**：git diff 噪声大，「这次复盘改了什么」与「这次开发改了什么」混在一起。

本设计把「改 skill 文本」从复盘流程中**整体摘除**：复盘的产出止于**账本与提案**；skill 文本改动只发生在用户显式发起的开发会话。

## 2. 现状实证（2026-08-13 快照）

### 2.1 自我学习机制四层地图

| 层 | 内容 | 是否碰 `.claude/` |
|---|---|---|
| **自动数据腿**（无人批） | ① 权重重标定：`retro.recalibrate_and_log`（`retro.py:1158`）→ `factor_lab.calibrate` 多日面板+申万收缩 → 重写 `$CTX/factor_lab/weights.json` + `changelog.jsonl` 审计。全仓仅两个调用点：retro-playbook 第 3 步（Claude 复盘会话内显式跑）与 `prewarm.py:169`（默认关）。② nightly_close 7 步（归因备料/t1 回补/gap 终判/盯梢/22 账本/exp 观测/快讯）。③ MTM 机判：`retro.mtm_check_guards`（`retro.py:303-338`）→ `fs.mtm_update`（support +0.03 / refute −0.08，refute≥3∧>support 自动提名退休）；`decay_lessons` 30 天防腐。 | 否（weights.json 在 `$CTX`，非 `.claude`） |
| **半自动提案腿**（自动起草、人批生效） | t1 快环同 key ≥2 日自动立案 `prompt_rule`（`t1_review.py:451-479`）；MTM 反驳达阈自动立案 `lesson` 退休提名（`feedback_store.py:276-282`）；quota/floor/门槛提议（`channel_ledger.propose_quota_adjustments` 等）。全部落 `proposals.jsonl` 等人批。 | 否（起草不动文件） |
| **写经验腿**（复盘会话内 Claude 判断后写库） | retro 步 5 `upsert_lesson`/M2 `adjudicate`；步 5.5 行业 memo；feedback skill 5 步。注回：`render_calibration_block`（cap=8、regime 过滤、收缩值）进 L3 表；`render_t1_calibration_block` 进 L3/L4；lessons guard 经 `self_review.review` 成发布硬门。 | 否（写的是 `$CTX/knowledge/*.jsonl`） |
| **skill 文件写路径**（本设计的靶子） | 见 §2.2 | **是** |

### 2.2 通往 `.claude/**` 的全部写路径（穷举）

**结论先行：没有任何生产代码自动写 `.claude/`**（`grep "write_text|open('w')|to_csv|shutil.copy" autoresearch/ | grep claude` 零命中）。全部是「指令路径」——playbook 文字或 CLI 打印的施工指引，让 Claude 在会话内用 Edit 工具改：

| # | 路径 | 人批门 | 处置 |
|---|---|---|---|
| P1 | **lesson「毕业」出口**（`feedback-playbook.md:67-71`）：MTM 达标 → 「把 rule 原文**固化写进对应 playbook 正文**，再 `retire_lesson`」 | **⚠️ 无**（全仓唯一无人批字样的直写指令） | **§4.1 改提名制** |
| P2 | **prompt_patch 起草**（retro-playbook 4.5 节；`fs.add_prompt_patch` 三重校验） | 只起草不动文件 | **§4.2 退役** |
| P3 | **prompt_patch 施工**（人批 → `feedback_store apply <pid>` 打印编辑处方 → Claude 照做改 target_file） | 有（但裁决常与复盘同 session，体感即「复盘完 skill 被改」） | **§4.2 退役** |
| P4 | self_review doc-lint fail → 修被点名文档 | 开发环节 | 不动（属开发会话） |
| P5 | 契约同步测试红 → 修另一侧（`test_agent_defs.py`） | 开发环节 | 不动 |
| P6 | codex 软链（`~/.codex/skills` ↔ `.claude/skills`）人工双向编辑 | 人 | 不动 |
| P7 | OMC 插件状态文件 `.claude/skills/.omc/state/`（untracked，非内容文件） | — | 不动 |

配置侧：`scan_config.jsonc`/`pinned.jsonc` **零自动写**（`user_config.py` 只有 load 路径；`rollback_watch`「只推荐不自动改」；`channel_audit`「本模块不写 scan_config」）。

### 2.3 「复盘改 skill」git 实证（近 3 个月 11 例，全部人在环）

`92d6ef5`（fb_20260717_001 驱动 +4 个 `.claude` 文件）、`e8a789a`（**P1/P2 诞生 commit**：prompt_patch + lesson 毕业出口）、`3837044`、`8e89391`、`758b2af`、`5936c8e`、`2bf81e6`、`58b418c`、`1e8d2f8`（旧尺 lint 驱动批量订正）、`2a54854`（提案过堂人手改 scan_config +10/−4）、`f79ad63`。——现象真实存在，但每例都有人 commit；本设计要下掉的是**指令层留给复盘流程的这个口子**，不是追责历史。

### 2.4 存量与依赖

- **存量提案**：`context_claude/knowledge/proposals.jsonl` 现有 open 共 20 条，其中 `kind=prompt_patch` 仅 **1 条**：`pr_20260729_001`（「选股硬约束(来自用户反馈,违反即失败)」→ `.claude/agents/l3-rank.md`，2026-07-29 立案，已挂 15 天）。`context_codex/` 无 proposals 文件，零迁移量。
- **治理依赖**：wave8 设计稿（`2026-07-29-wave8-maiden-run-optimization-design.md` §4.3）规定「L3 prompt 改动必须同时走 prompt_patch 人批 + experiment_registry 登记」——退役后须指定接棒载体（§4.4）。
- **测试依赖**：`tests/learning/test_prompt_patch.py`（整文件，含「apply 绝不写文件」不变量）、`test_proposals_kanban.py`（机器只整理不裁决——不受影响）、`test_assemble_court_nag.py`（催办只提醒不裁决——不受影响）。

## 3. 目标与不变量

**禁令锚句**（探针逐字断言，见 §6）：

> 复盘/反馈流程一律不得编辑 .claude/ 与 CLAUDE.md/AGENTS.md;skill/prompt/agent/workflow 文本只在用户显式发起的开发会话中修改。

约束对象 = scan-retro（含批量补诊断）、t1-review、feedback、scan-market 开跑前自动补复盘。这些会话的产出**止于**：账本（`$CTX`/`$RPT`）、lessons/memos（`$CTX/knowledge`）、weights（`$CTX/factor_lab`）、提案（`proposals.jsonl`）、复盘报告（`$RPT`）。

**明确保留**（一件不动）：权重自动重标定 + changelog + heartbeat；lessons 全生命周期（adjudicate/MTM/decay/cap=8 注入/guard 硬门）；行业 memo；t1 自动立案 `prompt_rule`（纯建议）；quota/gate/floor 参数提案；22 账本 + nightly_close；experiment_registry；self_review 全部 lint。

**边界澄清**（防执行期扩大化）：
- 裁决会话里用户逐条批准参数提案后，Claude 当场改 `scan_config.jsonc` **仍允许**（显式人批的配置改动，非 skill 文本；先例 `2a54854`）。
- 用户显式发起的开发会话改 skill 文本不受限（P4/P5 属此类）。
- 禁令不引入 Claude Code permission 层硬拦（会误伤开发会话）；执行靠指令契约 + 探针（§6）。

## 4. 设计

### 4.1 毕业出口改造：直写 → 提名（P1）

**现状**：`feedback-playbook.md:67-71`（第五出口「毕业」）+ `:84`（「毕业退休」呼应行）指示达标即固化写 playbook + `retire_lesson`，无人批。

**改为**：毕业**提名**。条件不变（reinforce_count 持续走高、`mtm.support≥3` 且 support>refute），动作换成起草提案：

```
fs.add_graduation_nomination(slug) -> proposal record
```

规格（新增于 `feedback_store.py`，放 mtm/proposal 区段附近）：
- 校验：lesson 存在且 `status=active`，否则 raise（`ValueError`）；同 slug 已有 open 的 `kind=graduation` 提案 → 跳过并返回已有记录（幂等去重，防 retro 日复日重复提名——对齐 `mtm_update` 退休提名与 `t1_review.filed_pr` 的既有去重哲学）。
- 落库：`add_proposal(kind="graduation", summary="毕业提名: <slug>", rationale=<MTM 读数 + reinforce_count + confidence>, diff_sketch=<rule 原文 + 建议目标 playbook 相对路径>)`。**不带 proposed_text 全文补丁**——定稿措辞是开发会话的事，提案只携证据与原文。
- **时序不变量（本节核心）**：提名时**不** `retire_lesson`。lesson 保持 active、继续占 cap=8 注入名额，直到用户在开发会话把 rule 固化进 playbook 并**在同一会话里**显式 `fs.retire_lesson(slug)`（毕业退休语义不变：非失效，勿删档）。理由：现行流程固化与退役同刻发生、知识无空窗；改提名制后若提名即退役，等待人批期间该经验既不在 playbook 也不在注入名单——两头落空。
- kind 集合：`add_proposal` docstring（`feedback_store.py:515`）从 `{factor,gate,prompt_rule,prompt_patch}` 改为 `{factor, gate, prompt_rule, lesson, graduation}`（顺手修正：`lesson` kind 已被 `mtm_update:276-282` 实际使用但 docstring 漏列）。
- 看板露出：零新建——graduation 自动进入现有三处露出（`retro_input.md` 待裁决节 >14 天 ⚠、prelude `proposals_nag_lines`、L5 报告债务节）。

**playbook 文本改动**：
- `feedback-playbook.md:67-71` 重写为「毕业提名」：达标 → `add_graduation_nomination(slug)`；**固化施工与 `retire_lesson` 只在用户开发会话执行**；提名期间 lesson 继续注入。
- `:84`「毕业退休」行同步改：「已批准毕业并固化进 playbook 后，在该开发会话内 `retire_lesson`」。
- `retro-playbook.md` 第 5 步如引用毕业出口，同步改措辞（现文本经 M2 裁决节间接引用，实施时 grep「毕业」核对）。

### 4.2 prompt_patch 管线退役（P2+P3）

**删除清单**（`feedback_store.py`，行号为 2026-08-13 快照）：

| 符号 | 位置 | 处置 |
|---|---|---|
| `_CONTRACT_ANCHORS`（6 锚集） | `:530-537` | 删。锚保护不失守：真正的契约锚守卫是 `tests/test_agent_defs.py`（锚同步测试，保留），`_CONTRACT_ANCHORS` 只挡 prompt_patch 草稿这一个入口，管线退役后无对象 |
| `_MAX_OPEN_PROMPT_PATCH = 5` | `:538` | 删 |
| `add_prompt_patch()` | `:541-580` | 删 |
| `_prompt_patch_payload()` | `:621-630` | 删 |
| `show_proposal()` prompt_patch 分支 | `:632-657` | 瘦身：保留通用打印（summary/rationale/diff_sketch/evidence/status），删 target_file+diff 专用渲染 |
| `apply_proposal()` 施工处方分支 | `:659-694` | 瘦身：保留「打印 summary + `set_proposal_status(pid,"applied")` 收尾」；删「打开 target_file / 替换 current→proposed / 契约文件强制验门」处方段。**docstring 的硬约束原句保留**：「本函数从不 Edit/Write target_file——调用前后磁盘内容逐字不变」 |
| `_CONTRACT_FILE_BASENAMES` / `_is_contract_file()` | `:601` 附近 | 删（唯一消费者是 apply 处方段与其测试）。契约文件「施工后必跑 test_agent_defs + doc-lint」的要求不消失，移进禁令段文档（§4.3）作为开发会话守则 |

**周边文案**：
- `t1_review.py:471`：diff_sketch 文案「人批后走 lesson 裁决(ADD)或 prompt_patch」→「人批后走 lesson 裁决(ADD)」。
- `retro-playbook.md`：整删 4.5 节「起草 prompt_patch」（22 行）；`:89-90`「施工永远走人批」句随节删除，其精神并入边界节禁令（§4.3）。
- 历史档案**不改写**：`docs/plans/2026-07-11-hermes-selfimprove-plan.md`、`docs/specs/2026-07-11-recall-gate-pinned-config-design.md` §5.1、wave8 plan `:293-298` 保持原样（产物能证明跑过什么；沿革注记只加在 wave8 设计稿一处，见 §4.4）。

**测试重组**：
- 删 `tests/learning/test_prompt_patch.py`（诞生于 `e8a789a` 波）。**删前双职审计**（本仓教训：删退役特性的 test 会静默孤立它顺带锁的 live 契约）——该文件顺带锁的存活契约必须移植：
  - `apply 绝不写文件`（磁盘逐字不变）→ 移植到新 `tests/learning/test_proposals_apply.py`，对瘦身后 `apply_proposal` 原样断言（此不变量在退役后**更加载重**：它是「python 永不写 .claude」设计不变量的唯一运行时测试）。
  - `show` 不存在 id raise、通用退化打印不崩 → 同上移植。
  - `apply` 收尾置 `applied`、不存在 id raise → 同上移植。
  - 三重校验/锚禁区/open≤5/`_is_contract_file` 各测试随符号一起删（契约本体退役）。
- `test_proposals_kanban.py`：`annotate_open_proposals` 若对 kind 有穷举断言，补 `graduation`（实施时核对）。

**存量迁移**（一次性，实施波内完成）：
- `pr_20260729_001`（唯一 open prompt_patch，target `.claude/agents/l3-rank.md`）：代码退役**前**由用户过堂一次——① 拒绝（`set_proposal_status(pid,"rejected")`）；或 ② 用户认可其内容 → 当场作为**普通开发改动**在开发会话落地（跑 `test_agent_defs` + doc-lint），提案置 `applied`。不留 open 的孤儿 kind。
- 已终态（approved/rejected/applied）的历史 prompt_patch 行**不迁移不改写**（jsonl 是审计日志）；瘦身后 `show_proposal` 对旧行退化为通用打印，不崩（移植测试覆盖）。
- `context_codex/` 无 store，零动作。

### 4.3 禁令契约落点（4+1 文件）

锚句逐字（见 §3）写入：

| 文件 | 位置 | 改动 |
|---|---|---|
| `.claude/skills/scan-retro/retro-playbook.md` | 「边界」节（现 `:155-158`） | 首行加锚句；「仅权重自动落地」句保留；净行数变化 ≈ −21（删 4.5 节 22 行 + 加 1 行，playbook 行数预算受益） |
| `.claude/skills/scan-retro/SKILL.md` | 「自我迭代腿」段（`:31`） | 「半自动边界不变」句后加锚句引用（该文件经 grep 证实无 prompt_patch 字样，无删改项） |
| `.claude/skills/feedback/feedback-playbook.md` | 毕业节重写处（§4.1） | 节尾加锚句 |
| `.claude/skills/feedback/SKILL.md` | 「流程」段 | 加一句：裁决提案只改状态与配置（人批逐条），不改 skill 文本 |
| `.claude/skills/scan-market/SKILL.md` | 「闭环(开跑前补跑复盘)」前置段（`:31`） | 加半句引用：「补复盘会话同受『复盘不动刀』禁令约束(见 scan-retro)」 |

另：开发会话守则一句并入 retro-playbook 边界节——「skill 契约文件在开发会话改动后必跑 `tests/test_agent_defs.py` + `tests/test_skill_docs_refs.py`」（承接原 apply 处方里的强制验门要求）。

### 4.4 治理接棒（wave8 依赖）

wave8 §4.3「L3 prompt 改动必须同时走 prompt_patch 人批 + experiment_registry 登记」改由以下承接（写入本稿即生效为新权威）：

> L3 prompt 改动只在用户显式发起的开发会话中进行；**experiment_registry 登记（`approved_by` 必填）与契约锚测试/doc-lint 要求不变**；改动动机若源于复盘诊断，引用对应 `retro_input.md`/账本读数作为 evidence 即可（原 prompt_patch 的证据门槛「同型失误 ≥2 次 + 账本读数支撑」作为开发会话立项参考保留）。

wave8 设计稿 §4.3 处加一行时点注记：「2026-08-13 起 prompt_patch 载体退役，本条治理由 `2026-08-13-retro-skill-selfmodify-removal-design.md` §4.4 承接」（本仓有更正旧稿先例：08-04 §6）。

## 5. 实施计划（3 批，单 commit/批，均含探针）

> 本稿只规划不实施。每批「验收」列的是可机检命令；mutation 概念见 §6。

**批 A：毕业提名制 + 禁令契约**
- Files：`feedback_store.py`（+`add_graduation_nomination`，改 `add_proposal` docstring kind 集）；`feedback-playbook.md`（毕业节重写 + 锚句）；`retro-playbook.md`（边界节锚句）；`scan-retro/SKILL.md`、`feedback/SKILL.md`、`scan-market/SKILL.md`（锚句/引用句）。
- Tests：新 `tests/learning/test_graduation.py`（①提名落 kind=graduation ②同 slug open 去重幂等 ③**提名不 retire lesson、`render_calibration_block` 仍含该条** ④lesson 不存在/非 active raise）；`test_skill_docs_refs.py` 正锚（§6）。
- 验收：`uv run --no-sync python -m pytest tests/learning/test_graduation.py tests/test_skill_docs_refs.py`。

**批 B：prompt_patch 退役 + 存量过堂**
- 前置：用户过堂 `pr_20260729_001`（§4.2 存量迁移）。
- Files：`feedback_store.py`（§4.2 删除清单）；`t1_review.py:471` 文案；`retro-playbook.md` 删 4.5 节。
- Tests：删 `test_prompt_patch.py` → 新 `tests/learning/test_proposals_apply.py`（移植 4 项存活不变量，§4.2）；`test_skill_docs_refs.py` 负锚（§6）。
- 验收：全量 `pytest tests/learning/ tests/test_skill_docs_refs.py tests/test_agent_defs.py`；`grep -rn "add_prompt_patch" .claude/ autoresearch/ tests/` 仅允许出现在本设计稿与历史 docs。

**批 C：治理注记 + 文档扫尾**
- Files：wave8 设计稿 §4.3 一行注记；扫尾 grep（`grep -rn "prompt_patch\|毕业" .claude/ docs/PANORAMA.md`——PANORAMA 本日快照零命中，扫尾防实施期间新增引用）；memory 索引条目由会话侧另记。
- 验收：doc-lint 绿；扫尾 grep 结果人工核对仅剩历史档案命中。

## 6. 探针规格（挂 `tests/test_skill_docs_refs.py`，doc-lint 之家）

每条探针都回答「把实现删掉/改回去，它会红吗」（本仓铁律：绿灯不等于有灯）：

**正锚**（断言存在，逐字）：
1. 禁令锚句在 §4.3 表列 4 个 `.claude` 文件中逐字存在（scan-market 为引用句变体）→ *有人删禁令段 → 红*。
2. `feedback-playbook.md` 毕业节含「毕业提名」与「add_graduation_nomination」→ *有人把毕业节改回直写 → 红*。

**负锚**（断言不存在）：
3. `grep .claude/ -r "add_prompt_patch"` 零命中 → *有人在 playbook 里教回这招 → 红*。
4. `not hasattr(feedback_store, "add_prompt_patch")` → *有人把函数加回来而不过设计评审 → 红*。
5. `feedback-playbook.md` 不含「固化写进」直写指令句式 → *毕业直写复辟 → 红*。（重写后的毕业节须用「在开发会话固化落地」等措辞避开该 token——§4.1 的示范文案已避开；这是给实施者的措辞边界，不是巧合）

**移植不变量**（`test_proposals_apply.py`）：
6. `apply_proposal` 调用前后 target 目录树磁盘逐字不变（继承原 `test_prompt_patch.py:222-226` 语义,对象换成任意 open 提案）→ *apply 长出写文件能力 → 红*。

**graduation 语义**（`test_graduation.py`）：见 §5 批 A。其中 ③（提名不退役、注入仍在）是时序不变量的直接探针 → *有人「顺手优化」成提名即退役 → 红*。

## 7. 回滚

- 三批各自单 commit、纯减法 + 提名制小增量：`git revert <批commit>` 即回滚，无数据迁移不可逆点（proposals.jsonl 只追加状态、不删行）。
- 整线恢复参照：诞生 commit `e8a789a`（prompt_patch + 毕业出口）与 `docs/plans/2026-07-11-hermes-selfimprove-plan.md`（原实施计划）。
- 若恢复：先撤 §6 负锚探针（否则测试红），再 revert——探针红即「有人在未过设计评审时恢复」的告警本身，属预期行为。

## 8. 不做清单

- ❌ 不动权重自动重标定（唯一自动腿及其 heartbeat 空转探针,pr_20260716_001 教训）。
- ❌ 不动 lessons/memo/t1 候选/账本任何数据侧机制（含 t1 自动立案 `prompt_rule`——它只落 JSONL）。
- ❌ 不加 Claude Code permission/hook 层对 `.claude/` 的硬写拦截（误伤开发会话；执行层 = 指令契约 + 探针）。
- ❌ 不改写历史 docs/plans、docs/specs、已终态提案行。
- ❌ 不迁移 `context_codex/`（无 store）。
- ❌ 不新建 graduations.jsonl（用户已裁定复用 proposals 看板）。

## 9. 开放问题

无阻塞项。一处实施期核对点：`test_proposals_kanban.py` 与 `annotate_open_proposals` 对 kind 是否穷举（若是，补 `graduation`；§4.2 已列）。

---
> 设计沿革：P1/P2 诞生于 `e8a789a`（设计 `2026-07-11-recall-gate-pinned-config-design.md` §5.1，plan `2026-07-11-hermes-selfimprove-plan.md`）；治理前置 `2026-07-29-wave8-maiden-run-optimization-design.md` §4.3；本稿由 2026-08-13 用户裁定（B 档）驱动，调研实证同日全仓快照。
