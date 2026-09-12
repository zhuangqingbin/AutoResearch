# Scene Reconstruction via Transcript Binding — Implementation Plan

> 修订 v2 · 2026-09-12。用户已要求按本轮评审意见修改文档；本次未实施代码、未回填数据。
> 执行本计划时使用相关 superpowers 执行技能，逐任务核对输入、输出和验收；本文件的待办不是已完成记录。
> 本版用需求契约、接口和反例替代旧版大段预写实现。旧版的路径子串推断、顺序配 attempt、整文件来源优先与固定哨兵时间代码均已撤下，不作为实现参考。

**Goal:** 让每个 run/股票的研究证据、实际 E6 选择、卡面冲突与证据缺口可核验，并如实区分当时事实、历史补录与未知。

**Architecture:** 复用现有 capsule、事件链、角色词表、适配器与来源索引，以同一不可变 transcript 快照构建归一化、绑定和计量。scan 绑定器只负责期望与归属编排，E6 在原决策路径内输出解释，chain_view 按证据有效性合并与渲染，历史修复落独立 ledger。

**Tech Stack:** Python 3.12、uv、pytest，现有 trace/scan/contracts；不增加 LLM 调用、服务、数据库或通用事件框架。

**Spec:** [v2 设计契约](../specs/2026-09-12-scene-reconstruction-transcript-binding-design.md)。
**独立 P0:** [结果账本交易日历完整性计划](2026-09-12-outcome-trading-calendar-integrity.md)，优先交付，不与 transcript PR 捆绑。

## 1. 交付顺序与范围

| 交付 | 任务 | 可验收结果 | 依赖 |
|---|---|---|---|
| P0 | 独立日历计划 C1–C3 | T+1/T+2 不因湖缺日漂移，错误旧账可被明确失效和重算 | 无 |
| A | Task 1–5 | 新 run 有可信来源、绑定账和摘要/详细视图 | 无；不要求 P0 完成才能开发 |
| B | Task 6–7 | 卡面保守解析、E6 真实排序解释、冲突可见且选择不变 | A 的视图接口；解析可独立开发 |
| C | Task 8–9 | 冻结 run 可补录，抢救件有归属判定与持久消费路径 | A；不阻挡 A/B 先交付 |

历史收益评价必须等 P0 核对完成。0 BUY、sentinel、失败或部分证据 run 都是正常验收人口；不承诺历史调用全部 PRESENT。

不在范围：实时 exec_check、broker 接入、收益策略优化、自动归因/学习、修改 E6 候选池/硬门/排序、brief 改版、agent 决策日志模板、隐藏推理捕获。

## 2. 全局约束

- Codex 命令以 `export AUTORESEARCH_ENGINE=codex` 开始。研究产物只能在当前引擎根；禁止读写另一引擎 context/reports。真实验收由各引擎分别完成，合成测试在 tmp_path 模拟两引擎。
- 保留用户已有工作树修改，不提交无关文件；不自动提交、推送或执行扫描/回填。各交付实现时独立提交，联署按真实执行者处理，不预填另一模型身份。
- 所有 stage/role/mode 消费 `autoresearch/contracts/stages.py` 与 `scan/run_profile.py`，不手写映射。尤其 strategist/sector-brief 的阶段应读现有 ROLE_STAGES。
- 不变更已冻结 run 的任何文件内容、集合或 MANIFEST。验证需比较相对路径与内容 SHA256，不能只比较文件名。
- 业务发布可成功而证据降级；所有缺失/冲突均有原因。不能用全局 try/except 吞掉中间一项错误和最终报告。
- 每个已知期望恰一行：`accounted == expected`；状态计数之和等于 expected。产品派生的分母标 lower_bound；unexpected 不计入 expected 的分子/分母。
- E6 旧字段、评级与 `RULE_VERSION=e6.v3.0` 不变。新观察字段解析失败不能改变选择或让决策产物缺失。
- 新产物先登记 ARTIFACTS，再实现生产者；已有 capsule 布局由 capsule 契约扩展，不建立平行注册表。不新增 prelude STEP_NAMES。
- 原始 transcript 只读；同一源快照经既有脱敏纪律归档。tool_response hash、artifact hash、source_prefix hash、archive hash 分开。
- 每任务先写对应反例并确认失败，再实现；只运行相关测试。每个交付完成后跑当前引擎全量和 ruff，不在每个小步骤重复全量。
- 引用的 CLI/函数新增接口属于本计划实施内容；实现前不得把它们当成已可调用功能。

## 3. 文件责任与接口

| 文件 | 改动责任 | 任务 |
|---|---|---|
| autoresearch/contracts/artifacts.py | 注册绑定报告、ledger 重建目录和抢救来源 | 1 |
| autoresearch/trace/transcripts/base.py | 快照/观察字段共用类型，call_id 关联与响应摘要口径 | 1–2 |
| autoresearch/trace/transcripts/snapshot.py（新） | 读取稳定前缀、脱敏快照、摘要与诊断；不含 scan 逻辑 | 2 |
| autoresearch/trace/transcripts/claude.py、codex.py | rows 级解析、工具成功/失败/部分状态、可见文本、usage | 2–3 |
| autoresearch/trace/capsule.py | stage 参数、期望、快照消费、绑定/索引；保留旧版兼容 | 2–4 |
| autoresearch/trace/usage_harvest.py | 按唯一源/计数区间去重；未知调用计量不填零 | 2、4 |
| autoresearch/trace/completeness.py | 若需适配新增质量字段，保持完好性/完整性分离 | 4 |
| autoresearch/scan/l4_tasks.py | 复用 preflight/mark_success/mark_failure 的现有边界及可得关联信息 | 3 |
| autoresearch/scan/transcript_binder.py（新） | scan 期望合并、归属分配、绑定报告与 active/offline CLI | 3–4、8 |
| autoresearch/scan/post_run.py、user_config.py | observe 接线和开关白名单 | 4 |
| .claude/skills/scan-market/scan_config.jsonc、SKILL.md、STAGES.md | 后续实现时同步开关/流程说明；不改研究模板 | 4 |
| autoresearch/scan/chain_view.py | 证据来源合并、摘要/详细模式、E6/交易语义展示 | 5、7–9 |
| autoresearch/scan/l4/parsers.py | card_context 保守解析与错误隔离 | 6 |
| autoresearch/scan/relative_buy.py | 从现有选择变量产出 schema 2、field_usage、why、conflicts | 7 |
| autoresearch/scan/salvage.py（新） | 逐文件抢救与 provenance；复用快照和定位器 | 9 |

所有新定义均以 spec 的字段含义为准，实施接口如下：

- `capture_snapshot(path, *, engine) -> TranscriptSnapshot`：snapshot 模块新增；返回固定完整行前缀、最后 ordinal、字节截止、源摘要、脱敏归档摘要、坏行/尾半行/源变化诊断。
- `ClaudeTranscriptAdapter.stats_from_rows(rows, ref) -> TranscriptStats`、`CodexTranscriptAdapter.stats_from_rows(rows, ref) -> TranscriptStats`：文件入口均委托 rows 入口；normalize/usage 使用同一 rows 和区段。
- `bind_transcript(..., stage=None)`：向后兼容；snapshot 引用属于索引扩展，已有调用者不需要提供它。
- `agent_expectations(handle) -> dict[str, dict]`：以 invocation_id 为键，值含 role、subject、subject_key、attempt、来源、terminal、boundary_quality；分母质量由报告汇总。
- `assign(candidates, expectations, run_identity) -> dict`：返回 rows、unexpected、unmatched、coverage；所有期望行包含 binding_status、segment_quality、reason 和来源引用。
- `bind_run(run_id, scan_dir) -> dict`、`safe_bind_run(scan_dir) -> dict | None`：前者绑定并写报告，后者逐项隔离/记录全局故障。
- `parse_card_context(text, *, contract=None) -> dict`、`read_card_text(scan_dir, ticker) -> str | None`：新字段见 spec §7；没有 contract 时不能推断历史阈值是否合规。
- `offline_index(run_dir, *, ledger_root=None, sessions_root=None) -> dict`、`salvage_run(run_dir, *, ledger_root=None, sessions_root=None) -> dict`：必须验证当前 engine 与目标 run；写 ledger，不写 run。

接口具体 dataclass/TypedDict 定义落对应模块，不从 trace 导入 scan，不通过宽泛字典省略缺失状态或身份校验。

## 4. Task 1：登记产物、统一证据词义与版本

**Files:** contracts/artifacts.py、trace/transcripts/base.py；测试 tests/contracts/、tests/trace/test_transcript_adapters.py。

- [ ] 阅读 spec §2–4、§9 和现有 ARTIFACTS/ROLE_STAGES；先登记 staging 绑定报告、ledger 索引/版本目录、salvage provenance/快照目录和 acceptance 验收记录。
- [ ] 将 spec §3 的观察类型与 §4.5 的绑定/区段质量分别建模。保留 call_id、操作结果、部分输出标志、路径来源、快照引用。
- [ ] transcript schema 升 2，E6 schema 的变更留 Task 7。旧版 normalized/index 可读，不把未知版本解释为空成功。
- [ ] 增加测试：Read 错误响应不得生成 READ_SUCCEEDED；Glob 只能 DISCOVERED；结构化响应摘要稳定且与文件摘要字段分开；stage 来自契约词表。
- [ ] 执行登记生成与契约检查，审阅所有生成差异，只包含本次新增产物。

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m autoresearch.contracts.emit --write
uv run --no-sync python -m pytest tests/contracts tests/trace/test_transcript_adapters.py -q
```

**验收：** 注册表和生产接口一致；没有新 prelude 步骤、重复 stage 映射或伪造 schema 1 字段语义。

## 5. Task 2：单源一致快照与 rows 级适配器

**Files:** 新 snapshot.py、base.py、claude.py、codex.py、capsule.py、usage_harvest.py。
**Tests:** 新 tests/trace/test_transcript_snapshot.py；现有 test_transcript_adapters.py、test_capsule.py。

- [ ] 写“读取中追加、源被替换、尾半行、两个 invocation 共享源”的合成测试，先证明重复活读会失败。
- [ ] capture_snapshot 固定读取时文件长度，只纳入完整 JSONL 行；记录 prefix hash 和 cutoff。无法保证同一前缀时 SOURCE_CHANGED，不混合重试内容。
- [ ] 按 spec §5.1 从同一内存前缀派生归档、normalized、usage；保留 existing secret redaction/atomic/fsync 纪律。
- [ ] 两适配器提供 stats_from_rows；文件入口只是读快照并调用 rows 入口。call/result 以 call_id 配对，未知工具保留往返并降级。
- [ ] 支持 Claude Read/Write/Edit、Codex apply_patch/exec_command 及现有 exec 包装中的可识别操作。只解析已知结构；不执行 transcript 中的 shell/JS，也不按任意文本路径猜操作。
- [ ] 返回带行号/分段/截断、多文件共用一份输出时标 READ_PARTIAL；文件 hash 只有来源/字节可证明时生成。
- [ ] 普通 assistant 文本与明文 reasoning summary 分开；加密体保持不可见。
- [ ] capsule materialize 接受选定快照，同源只写一份 raw，索引引用区段；旧版消费者仍能从 index 找到 raw。
- [ ] usage 从同一快照算，重叠计数区间去重；不可分摊到单 invocation 的记录写 UNMEASURED，不能复制 run 合计或均分。

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/trace/test_transcript_snapshot.py tests/trace/test_transcript_adapters.py tests/trace/test_capsule.py -q
```

**验收：** raw/normalized/usage/hash 共源；同一输入重复 materialize 内容不变；唯一 raw 数由唯一快照数决定。

## 6. Task 3：期望集合、session 定位与调用边界

**Files:** capsule.py、claude.py、codex.py、l4_tasks.py、新 transcript_binder.py。
**Tests:** 新 tests/scan/test_transcript_binder.py；现有 test_l4_tasks.py、test_run_profile.py。

- [ ] 写矩阵 B01–B10 的失败用例；以真实事件格式的合成数据验证，不用真实私有 transcript。
- [ ] 按 invocation 合并 AGENT 与 TASK 期望，保留同一 code 的所有 attempt。产品回退只补已知结果，不伪造 dispatched/completed。
- [ ] 从 run_profile 判断未到达/不需要的角色；存在一个 AGENT 事件不能抹掉其他角色的 TASK/产品记录。
- [ ] session_ref 优先；其次验证 engine、仓库与本 run 的成功操作证据。支持跨日恢复、同仓并行会话，只对真正竞争同一归属的候选报告歧义。
- [ ] 在 l4_tasks 既有 transition 中保留可得 session/attempt 关联信息，关联 transcript 里的实际 preflight/success/failure 工具调用；无法取得时保留 partial/unknown。
- [ ] Claude 用已知 subagent 归属及成功产物操作确认，包含 Edit/失败返回诊断；usage 行只用于候选定位。
- [ ] Codex 按明确边界切各 attempt，并闭合 call/result。区段外的异步返回只有 call_id 能归属才补入；补入后保留边界扩展证据。
- [ ] 无边界的 intel 不从“写产物那一行”推断整次研究；可保存产物证据与 partial 状态，搜索数量为 unknown。
- [ ] 相对路径按调用 cwd 解析；拒绝跨 run、目录逃逸、未验证根映射。现有 input marker 只能辅助，不给别的 run 绑定。
- [ ] assign 完成后对全部 E 检查恰一行；剩余期望补 GONE；unmatched/unexpected 独立输出。禁止用首行时间排序把稀缺候选依次塞给 attempt。

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/scan/test_transcript_binder.py tests/scan/test_l4_tasks.py tests/scan/test_run_profile.py tests/trace/test_transcript_adapters.py -q
```

**验收：** 两期望一候选仍有两行；两个明确重试区段不混合；仅读产物无成功写入不产生强写证据。

## 7. Task 4：active run 接线、逐项失败与绑定账

**Files:** transcript_binder.py、capsule.py、post_run.py、user_config.py、相关配置/流程文档。
**Tests:** test_transcript_binder.py、test_post_run.py、test_user_config.py、tests/integration/test_scan_capsule_faults.py。

- [ ] 增加 `bind_transcript(stage=None)` 及 CLI 参数；显式值校验，旧调用仍按既有默认行为。
- [ ] 保持 observe 顺序：原有决策校验完成 → safe_bind_run（选定快照、归属、报告）→ retain → finalize。materialize 不能重新选择另一份活源。
- [ ] 开关新增 retention.bind_transcripts，白名单和类型检查完整接线。关闭/无 active run 时也有 enabled=false/reason。
- [ ] 每条绑定独立捕获冲突、源不可读、归一化不支持等；保留原绑定，继续下一条。最终报告按 spec §4.5 原子落盘。
- [ ] 对读期望集合失败、磁盘无法写报告等整场故障，调用既有 evidence degradation/event 通道并 stderr；现有发布不被证据采集毁掉。
- [ ] 将 coverage、binding_status 与 materialize 的载体状态分别留存。产品分母下界、partial/interleaved 不能让 completeness 误转绿。
- [ ] 增加 active CLI：`python -m autoresearch.scan.transcript_binder --run-id <contract_run_id>`，默认从 run handle 取 staging，不在日期目录里猜最后一份。
- [ ] 相同绑定重跑不重复事件；源增长但闭合区段相同复用快照，变更区段写冲突而不覆盖。
- [ ] 配置/流程文档明确“失败有账、绑定不等于研究完整”；不改 L3/L4 评级逻辑与输入内容。

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/scan/test_transcript_binder.py tests/scan/test_post_run.py tests/scan/test_user_config.py tests/integration/test_scan_capsule_faults.py tests/contracts -q
```

**验收：** 中间一项冲突，前后项仍有结果；删除生产接线会使集成测试失败；0 BUY 和失败 run 可正常记账。

## 8. Task 5：来源合并与 chain_view 摘要/详细模式

**Files:** chain_view.py。
**Tests:** 新 tests/scan/test_chain_view_agents.py；现有 test_chain_view.py。

- [ ] 先测 capsule GONE + ledger PRESENT、有效 capsule + 冲突 ledger、损坏 normalized、shared 无归属四个场景。
- [ ] Sources 按 invocation 合并有效来源，保留原始状态/补录状态/冲突；不能见到 capsule 文件就直接返回整场索引。
- [ ] slim/deep 查找覆盖 inputs 与镜像的 _external_inputs。shared/salvage 无强归属只能列参考，不进入事实计数。
- [ ] 用规范化观察展示成功、失败、部分、未观察到；deep 预期由卡种/早停契约决定。删除“没读”和“>8KB 才可信”的绝对结论。
- [ ] 写入 hash 与发布版本按同产物核验，intel 对 intel；缺完整后镜像、后续 Edit、截断或脱敏变化分别给出比较状态。
- [ ] 默认摘要 ≤80 行；异常类型与缺口计数保留。新增 `--verbose` 输出完整链路，长文本规则见 spec §8。
- [ ] 复用已有 execution 时间锚，展示证据 observed_at 与决策时间关系；事后补录不能变成当时已知。
- [ ] 收益标签明确推荐毛收益、执行条件事后测算、迟到反事实、实际成交未知；不凭日线条件声称盘中核验过。
- [ ] 同一证据版本重复渲染字节一致，golden 覆盖摘要/详细模式与 schema 1 兼容。

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/scan/test_chain_view_agents.py tests/scan/test_chain_view.py -q
```

**验收：** 失败 Read 不出现在成功读取计数；partial 不输出“搜索 0 次”；补录可见且原始 GONE 仍可追溯。

## 9. Task 6：card_context 保守解析

**Files:** l4/parsers.py。
**Tests:** 新 tests/scan/test_parsers_card_context.py。

- [ ] 写纯 0%、0.0%、10%、不新开仓、待突破确认、不追高、明确允许、空表、损坏数值、相互矛盾卡面的参数化用例。
- [ ] 提供 read_card_text 公共读取入口；parse_card_context 复用现有 dashboard/proposal/early-stop 解析，返回 spec §7.1 的结构。
- [ ] 新开仓四态使用明确词义；未知和条件未满足不能渲染成“可建仓”。保留 position_raw/trigger_raw 与冲突来源。
- [ ] 分开执行线原始阈值、presence、contract_match。按传入的当时 contract 核验；未传 contract 为 UNKNOWN，不取当前常量冒充历史版本。
- [ ] 缺失/格式错误逐字段记录；无卡或未知卡种不伪装满卡，任何解析错误不向上抛出破坏 E6。
- [ ] EV/R:R 只保存卡面原文，不推导未声明概率的期望收益；不增加评级规则。

代表性验收（以下调用是本任务新增的公共接口）：

```python
import pytest
from autoresearch.scan.l4.parsers import parse_card_context

@pytest.mark.parametrize(
    ("position", "stance"),
    [
        ("0%", "PROHIBITED"),
        ("0.0%", "PROHIBITED"),
        ("不新开仓", "PROHIBITED"),
        ("待突破确认", "CONDITIONAL"),
        ("不追高", "CONDITIONAL"),
        ("明确允许新开仓，仓位 10%", "ALLOWED"),
        ("10%", "UNKNOWN"),
        ("—", "UNKNOWN"),
    ],
)
def test_entry_stance_requires_positive_evidence(position, stance):
    text = "\n".join([
        "# 决策卡",
        "| 评级 | 现价 | 仓位 |",
        "|---|---|---|",
        f"| Hold | 10 | {position} |",
        "FINAL TRANSACTION PROPOSAL: **HOLD**",
    ])
    got = parse_card_context(text)
    assert got["entry_stance"] == stance
    if stance in {"CONDITIONAL", "UNKNOWN"}:
        assert got["no_new_position"] is None
```

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/scan/test_parsers_card_context.py -q
```

**验收：** 0% 不可能生成“可建仓”；解析未知与明确禁止有区别；历史阈值未知不误报漂移。

## 10. Task 7：E6 schema 2 与实际选择解释

**Files:** relative_buy.py、chain_view.py。
**Tests:** test_relative_buy.py、test_chain_view_agents.py、test_post_run.py、test_report_sections.py。

- [ ] 保存同一组 fixture 的旧版决策投影：buys、blocked、候选资格、hard_gate、rank、relative_decision_score、faces、excluded、pool、second_buy；用它锁定行为不变。
- [ ] SCHEMA_VERSION 升 2；RULE_VERSION 不变。候选新增 card_context/source/parse_status，卡缺席和读取失败分别留痕。
- [ ] 从 build_decision 已有 eligible、buy_pool、排序值、excluded、second_buy 构造 selection；不能从旧 rank 反推池内名次。
- [ ] 保留 rank 语义，新增 observation_rank；selection 含实际池、实际顺序、排序依据、最终池内名次和选择数量。
- [ ] 否决股票数按 code 去重，每门次数另列；流水计数明确起始人口。
- [ ] field_usage 按真实规则标 hard_gate/ranking/display_only；记录 PROHIBITED、CONDITIONAL、UNKNOWN 与 E6 选中之间的 conflicts，但不增加硬门。
- [ ] why 从 selection 固定渲染；composite 与 finalists 的解释都与实际选中顺序一致。
- [ ] 在 write_decision 的 I/O 边界固定卡输入与 hash，active run 复用 trace.blobs.put_bytes 保存脱敏快照并留引用；build_decision 接收可选的固定卡输入，保持纯计算。无 active run 或归档失败标 unarchived，不影响选择。
- [ ] writer-2 使用该快照验证并核对当前卡摘要；观察时发现源卡变化沿现有 mismatch 通道报告，保留已发布答案。无归档时仍检验当前输入 parity，但不宣称源消失后可重建。
- [ ] schema 1、schema 2 无卡、schema 2 解析失败分别渲染；新增字段不回注 agent、不改 brief。仅字段解析/读取失败时保留原有 E6 选择产物。
- [ ] 变更 EV/仓位/执行线等 display_only 输入，决策投影不变；相同输入新 schema 两写者字节 parity；修改实际受硬门消费的 proposal 不属于该不变测试。

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/scan/test_relative_buy.py tests/scan/test_chain_view_agents.py tests/scan/test_post_run.py tests/scan/test_report_sections.py -q
```

**验收：** 全体观察 rank 与池内实际 rank 不同的样本解释正确；多门否决不重复计股票；display_only 故障不改变 BUY。

## 11. Task 8：冻结 run 的离线索引

**Files:** transcript_binder.py、chain_view.py、ARTIFACTS 必要生成件。
**Tests:** test_transcript_binder.py、test_chain_view_agents.py。

- [ ] 写“已有 capsule 全 GONE”“源 sessions 被移除后只剩归档”“同日不同 run”“索引引用越界”的失败用例。
- [ ] CLI 新增 `--offline <report_run_id|run_dir>`。校验 engine/contract_run_id/report_run_id/manifest hash；不自动猜另一引擎根。
- [ ] 输入复用 run raw、已验证 salvage raw、harness 源的快照读取；不另写解析/usage/脱敏路径。旧 gz 用同一 rows API。
- [ ] 离线根映射来自 contract/归档元信息，不能把 _under_root 改成任意子串命中；同日 shared 根不足以确认归属。
- [ ] `revision_id` 由目标身份、源快照摘要、解析器版本规范化计算；相同版本不重写证据。
- [ ] 写 ledger 版本目录，normalized/source 引用齐全后，最后原子发布顶层 index。不同版本保留旧目录；computed_at 为实际 UTC，不参与 revision 身份。
- [ ] 每个期望都有一行，归一化错误逐项记录；report counts 与 carrier status 不混用。shape 与 capsule 的公共字段一致，额外含来源/版本/补录时间。
- [ ] Sources 按 invocation 合并后，schema 1 的 GONE 可由有效 ledger 补充，冲突显式保留。
- [ ] 冻结目录前后所有路径/内容哈希一致，且 capsule verify 保持原结果；补录不宣称修复了原始 capsule 完整性。

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/scan/test_transcript_binder.py tests/scan/test_chain_view_agents.py -q
```

**验收：** 补录的证据可在视图出现；原始 run 零改写；归档足以脱离原 sessions 读取。

## 12. Task 9：逐文件抢救与归属约束

**Files:** 新 salvage.py、chain_view.py。
**Tests:** 新 tests/scan/test_salvage.py；test_chain_view_agents.py。

- [ ] 写同日重跑覆盖、仅 mtime 接近、run 已有 E6 但缺市场研判、UTC/本地同一时刻、Codex --all 的失败用例。
- [ ] 对 E6 决策、market_view、transcript 分别判断是否缺失；不能由“已有决策”跳过整场抢救。
- [ ] provenance 使用 spec §6.3 的归属状态。只有 VERIFIED_RUN 可以成为事实源；mtime 近似只写 TIME_WINDOW_ONLY。
- [ ] 复用快照/脱敏归档，源变动保存不同摘要版本；保留已验证快照，不因 shared 后续变化覆盖旧件。
- [ ] 对有明确身份的后跑覆盖，标 OVERWRITTEN_BY_LATER_RUN；源时区不明标 UNKNOWN。标准化已知时区至 UTC 再比，不能直接删除 tzinfo。
- [ ] `salvage <run>|--all` 的取证窗口来自每个 run 的 start/end；当前时间仅记 captured_at，不拿固定 2000 年窗口追求输出幂等。
- [ ] Sources 将有效持久快照放在可变 shared 前；UNKNOWN/覆盖件只列参考，不能让“当日 BUY”或收益发生变化。
- [ ] --all 输出逐文件成功/缺失/歧义/错误计数，重复输入不重复归档；错误码与正常 ABSENT 分开。
- [ ] 删除合成原 sessions 后跑 offline_index，仍可从 salvage raw 得到同一观察结果；验证完整消费闭环。

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/scan/test_salvage.py tests/scan/test_chain_view_agents.py tests/scan/test_transcript_binder.py -q
```

**验收：** 已覆盖文件不参与 BUY 事实；Codex --all 确有合成归档；已有决策不妨碍抢救其他文件。

## 13. 验收矩阵

以下场景均使用可审阅的合成 fixture；每行必须有独立断言。误绑定为零指这些反例集的结果，不是对任意未知 harness 格式的无条件保证。

| ID | 场景 | 必须满足 |
|---|---|---|
| B01 | 成功写本 run 产物且身份唯一 | 正确绑定，stage 同词表 |
| B02 | 仅讨论/读取产物，或 Write 返回失败 | 不生成成功写入或产品强验证 |
| B03 | 其他 run、同日重跑、相对路径逃逸 | 不误绑定；原因可见 |
| B04 | 两期望 attempt，仅一候选 | 两行；未匹配项 GONE，不按时间猜归属 |
| B05 | 两次重试有明确分段 | 各自区段，不重复认领整段 |
| B06 | intel 搜索早于写入且边界不明 | partial/unknown，不声称搜索零次 |
| B07 | 一条 binding conflict | 原绑定保留，其余项继续，报告完整 |
| B08 | AGENT/TASK/产品混合、条件角色 | 不漏期望、不重复计数、下界标识 |
| B09 | 同仓两会话仅一份有 run 证据 | 不因候选主线程数量直接全场歧义 |
| B10 | 昨日创建、今日恢复的 session | 能找到且按当前 run 边界处理 |
| S01 | 活源追加/尾半行/替换 | 同一稳定前缀；错误不混合 |
| S02 | 多调用共享源且区段交错 | raw 去重、usage 不重复累计 |
| O01 | Read 失败、Glob、Grep、截断/多文件输出 | 分别标失败/发现/部分，摘要口径不混 |
| O02 | Write 后 Edit、仅 patch diff、intel 写入 | 按同产物版本比较，未知不冒充一致 |
| V01 | capsule GONE + ledger PRESENT | 补录可见，原始状态保留 |
| V02 | 两个有效来源冲突/未知 schema | 显式冲突或不支持，不静默成功 |
| V03 | 默认/verbose、无卡/schema 1 | 标签准确，≤80 行摘要，golden 稳定 |
| E01 | 0%、0.0%、10%、条件、明确允许、空卡 | 四态准确，未知不变允许 |
| E02 | 历史执行线版本缺失或漂移 | presence/contract_match 分开 |
| E03 | composite 实际顺序与观察 rank 不同 | why 使用真实选择顺序 |
| E04 | 一票触发多硬门 | 股票数去重，门次数另列 |
| E05 | display_only 改动/损坏 | 决策投影不变，错误有账 |
| E06 | 两写者同输入/卡中途变化 | parity；变化进入 mismatch，不覆盖 |
| H01 | 原源消失、冻结 run 外部补录 | 归档可消费；run 内容/集合 hash 不变 |
| H02 | 仅时间吻合/明确后跑覆盖 | 不充当本 run BUY 或收益事实 |
| H03 | Codex --all/时区/逐文件缺失 | 真窗口、无伪造时间、不整场跳过 |
| R01 | 成功 0 BUY、失败 run、两类 sentinel | 合法计账，不要求制造 BUY 或虚构调用 |
| T01 | 事后证据、迟到报告、日线执行测算 | 时点和收益人口明确，实际成交未知 |

P0 的节假日/缺日/旧账失效矩阵见独立计划，两个计划使用同一隔夜主尺。

## 14. 交付检查、真实验收与回滚

每个交付完成后执行当前引擎检查。两适配器 fixture 必须隔离在临时目录，不通过切换真实产物根来代替测试。

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest -q
uv run --no-sync ruff check autoresearch tests
git diff --check
```

关键守卫做定点变异验证：取消路径/身份验证、漏补 GONE、移除 observe 接线、重复区段 usage 求和、允许 0% 解析为 ALLOWED、以 capsule 文件存在短路 ledger。对应测试必须变红，然后恢复代码；无需对每个展示字段做一轮机械变异。

真实验收由下一次获准扫描/离线补录完成，本次文档编辑不执行。命令中的 run/code 必须来自当前引擎已有目标；不得复制旧稿的跨引擎样本路径。实现后 CLI 示例：

```text
python -m autoresearch.scan.transcript_binder --offline <本引擎报告目录或 report_run_id>
python -m autoresearch.scan.chain_view <本引擎报告目录或 report_run_id> <code>
python -m autoresearch.scan.chain_view <本引擎报告目录或 report_run_id> <code> --verbose
python -m autoresearch.scan.salvage <本引擎报告目录或 report_run_id>
```

- [ ] 验收记录写在 `reports_<engine>/scan/_ledger/acceptance/scene-reconstruction-<date>.md`，开发时先注册该产物；记录代码版本、源快照版本、测试结果、实际覆盖/缺失与原因。
- [ ] 下一次本引擎真实扫描能展示 BUY 或合法 0 BUY 的现场；未测另一引擎真实 run 则明确标“未验收”，不能用合成测试冒充。
- [ ] 真实历史样本记录实际 expected/bound/partial/gone，不设 ≥34 PRESENT 的数量门，不调整判据凑数。
- [ ] 保存当前路径处理耗时/源字节数/唯一快照数；默认摘要预算与同源去重满足要求。
- [ ] 在 P0 完成前，旧统计只标待重算；不能据此给策略或执行线效果下结论。
- [ ] 确认只修改本交付文件，保留用户无关修改；按 A/B/C 独立提交，不自动更新外部记忆或发布。

回滚：retention.bind_transcripts=false 停止新增采集但保留证据；E6 schema 2 独立回退但保留旧读兼容；历史 CLI 停止运行即可，不删除已保存快照。回滚代码不应改变冻结 run 或清空 ledger。

## 附录：评审意见落点

| 本轮评审问题 | 契约/任务 |
|---|---|
| 0%/待突破确认被渲染可建仓 | spec §7.1；Task 6；E01 |
| why 使用全体 rank 解释不同选择顺序 | spec §7.2；Task 7；E03/E04 |
| 失败/部分读取、工具响应 hash 冒充文件 | spec §3；Task 1/2/5；O01/O02 |
| Codex 产物路径假写入、intel 搜索丢失、重试混段 | spec §4；Task 3；B02/B05/B06 |
| 缺失 attempt 消失、单项错误整场吞掉 | spec §4.5/§5.2；Task 3/4；B04/B07 |
| capsule GONE 遮住补录 | spec §6.2；Task 5/8；V01 |
| 活源多次读取导致摘要不一致、重复计量 | spec §5.1；Task 2；S01/S02 |
| salvage 覆盖件冒充事实、--all 时间错误 | spec §6.3；Task 9；H02/H03 |
| 日历弱回退仍放行、迟到锚沿湖取日 | 独立 P0 C1–C3 |
| 一屏过长、8KB 可信度、跨引擎验收 | spec §2/§8；Task 5；交付检查 |
