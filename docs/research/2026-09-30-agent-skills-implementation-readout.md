# Agent Skills 开发进度与验证记录

## 基线

- 用户于 2026-09-30 授权开始执行 [开发计划](../superpowers/plans/2026-09-30-agent-skills-reliability-development-plan.md)。
- 源仓库 HEAD：`431d5dc01c539c1a5d68bdd2a7f8d80379eda110`，原分支 `main`；包含本轮之前的大量未提交修改。
- 实施 worktree：`.worktrees/agent-skills-reliability`，分支 `codex/agent-skills-reliability`。
- 从源工作区复制完整已跟踪/未忽略源码（1,242 文件），未复制任何另一引擎产物、数据湖或凭证；虚拟环境复用源仓库 `.venv`。
- 源工作区快照：`context_codex/development/20260930-agent-skills/baseline.tar`，SHA256 `d9ea12464ed2577379cf76297027c4b86ee1d4b975becbc6c291498fb904bed2`；同目录有逐文件 `baseline.json`、原始 diff/status。
- 该快照可以复现开发起点，但不是已经收敛成干净 commit 的实验基线；正式研究实验前仍须完成代码 provenance 门。
- 所有命令使用 `AUTORESEARCH_ENGINE=codex`；不读取或改写另一引擎产物。

## 状态

| 任务 | 状态 | 说明 |
|---|---|---|
| A0 | 完成源码保全与局部基线 | 未提交用户已有修改；独立工作区开发 |
| A1 | 核心实现，范围边界待后续接线 | 严格 DecisionFrame、A 股收盘边界、入场价收益分母、新 session 任务与展开统一冻结时钟；legacy/跨市场执行门尚未闭合 |
| A2 | 完成 | 20 项实证登记，历史样本/旧尺/缺信息显式区分；无证据不补数字 |
| A3 | 已实现并完成独立问题修复 | 严格评级/动作；宏观集合来自确定性范围，状态完整校验与原子写入 |
| A4 | 完成核心实现 | 生产 rubric 语义门；run 开始冻结版本；历史缺版本按 legacy-v1；非 A 股结构化投影另列限制 |
| A5 | 已实现并接入 session | 统一全部补位资格、v2 B/E veto、历史 UNKNOWN、新旧任务契约不能降级 |
| B1 | 已实现并完成独立复核 | FULL evidence bundle 与显式输入图；按 full_role 装载；LITE deep 宿主读取证据及字节对账 |
| B2–B3 | 待实施 | 来源断言绑定与两段独立初判 |
| B4 | 已实现并完成独立复核 | 复核覆盖元数据、DecisionRecord v2/历史兼容、pinned SELL 语义修正 |
| C1 | 已实现并完成独立复核 | 集中角色能力登记、FULL/intel 宿主包装、行业命名输入、启动前检查；实际工具缺失或执行器明确禁用 Web 均拒绝 |
| C2–C6 | 待实施 | 局部恢复、首次 attempt 隔离、输入能力边界、调度与真实宿主验收 |
| D1–D4 | 待实施 | 评价协议及受控结构优化 |

## 已执行验证

1. 原工作区 A 批基线（完整命令输出在快照目录 `baseline-tests.log`）：

   `uv run --no-sync python -m pytest -q tests/contracts/test_execution_contract.py tests/common/test_execution_math.py tests/test_rating.py tests/analyze/test_assemble.py tests/macro/test_assemble.py tests/macro/test_state.py tests/scan/test_research_card_migration.py tests/contracts/test_research_card.py tests/scan/test_self_review.py tests/scan/test_l3_merge_v3.py tests/session_agent/test_stock_lite_context.py tests/session_agent/test_stock_full_products.py tests/session_agent/test_scan_l3.py tests/session_agent/test_scan_l3_merge_caps.py tests/test_agent_defs.py`

   **342 passed, 1 skipped，5.30s**。skip 为缺少 2026-07-09 本地历史现场的冒烟检查。

2. A1 新增纯函数/契约测试：**29 failed**，原因是尚未提供 DecisionFrame 与 conditional_gap；实现后连同既有执行/层级回归 **151 passed，2.81s**。

   `uv run --no-sync python -m pytest -q tests/contracts/test_decision_frame.py tests/common/test_decision_frame_math.py tests/contracts/test_execution_contract.py tests/common/test_execution_math.py tests/contracts/test_layering.py`

3. A3 开发者记录：22 项新回归先红后绿；相关测试 **130 passed，1.64s**。集成复核后再补最终命令与结论。

## 尚未发生的验收

本轮没有运行真实股票研究、双宿主 REAL_SESSION 矩阵或前向收益实验；不据局部测试声明默认入口切换或投资效果改善。当前 `session_v1` 继续 PILOT。

## 独立复核修复记录

- A1：拒绝首次注册前已存在但与请求市场、深度、用途或 run 创建时刻不一致的时间窗；合法重试不刷新交易日历。
- A1：已知 A 股市场在分析日 15:00 前不得生成“该日已经收盘”的可执行时间窗；未知日历保留空 session，不推算节假日。非法枚举类型统一拒绝。
- A3：补上宏观 submit → validate → assemble → state 的 expected_keys 消费；KEY 集合来自静态跨资产清单及本 run 行业资金表，不信任模型自造清单。
- A5：新生产任务使用 scan.l3.v2；冻结的 v1 任务显式保留旧输出规则。追高参数注册到真实资格函数。新增 v2 代码唯一性校验，拒绝同码 CLEAR 行掩盖 VETO 行。
- 文档：未扩大 prompt 字节预算；将配置开发九条移到 scan-market/config-standard.md，主 skill 保留编辑前必读指针。

## 交付范围限制

- A1 的 research.frame 目前接入新 session_v1 计划；legacy 编排的完整机读时钟、跨市场独立执行时钟，以及所有执行入口对 UNKNOWN 的确定性封锁仍需后续任务完成。
- 截止后新闻的来源级时间校验与内容绑定归 B2，不能据“提示词已包含 cutoff”宣称证据隔离已完成。
- 非 A 股卡片保持严格评级/动作校验；现有六位证券代码 ResearchCard 没有被伪造扩容，非 A 股完整 rubric 结构化投影待扩展身份契约。
- 未改为自动交易系统，未建立投资收益改善结论；没有把同模型多次调用表述为统计独立。

## 本批代码入口

| 能力 | 主入口 |
|---|---|
| 冻结交易时钟 | `contracts/execution.py`、`common/execution_math.py`、`session_agent/decision_frame.py` |
| 严格评级和宏观完整范围 | `agents/utils/rating.py`、`macro/assemble.py`、`macro/state.py` |
| 决策卡生产语义门 | `scan/l4/rubric.py`、`scan/l4/card_io.py`、`session_agent/validation.py` |
| 全路径 L3 资格 | `scan/l3/merge.py`、`scan/l3/validation.py` |
| FULL 原始证据持续可读 | `session_agent/evidence_bundle.py`、`session_agent/workflows/stock.py` |
| 复核范围及兼容 | `scan/decision_finalize.py`、`scan/decision_record.py` |
| 角色、启动检查 | `contracts/agent_roles.py`、`session_agent/preflight.py`、`session_agent/roles.py` |
| 实证依据登记 | `docs/research/2026-09-30-agent-skills-evidence-register.md` |

### 读取证明与真实宿主

B1 要求可绑定的宿主工具调用、成功回执及完整响应内容与冻结 deep 文件的 UTF-8 字节数、哈希一致。读取 17 字节文件但仅返回 6 字节的反例已被拒绝。仅 shell 读取、行号包装或分页返回目前不构成这项完整读取证明，须明确标记未核；不会用模型自报“已读”补齐。

C1 preflight 是 `SUPPORT_CHECK_ONLY`；读取证据能力明确标记 `UNVERIFIED`。共享物理角色的工具集合只是已声明能力，不代表文件系统强隔离。新增角色定义在宿主重新装载后才生效；本轮没有重启宿主或运行 REAL_SESSION 验收。

### 版本与后续顺序

- 新 `RunProfile.card_rules_version=skills-gap-v2`；缺字段的历史 profile 读为 `legacy-v1`。
- L3 输出 v2 对字段、B/E veto、代码唯一性严格校验；原 v1 续跑不由省字段自动降级。
- DecisionRecord v2 记录复核策略/触发/必需性/状态/理由；历史 v1 保留原字节哈希和 UNKNOWN。新规则残缺复核不折回，旧 median-only 报告保留原折回逻辑。
- scan slim snapshot v2 保存 deep；旧 v1 离线读取保持原契约。
- 下一批先做 B2 来源断言绑定与 B3 两段初判，再推进 C2–C5 故障、attempt 隔离、输入边界、调度。C6 的真实双宿主证明和 D1 的前向样本不得以本批合成测试替代。

## 最终集成回归（工作区冻结检查前）

- 2026-09-30 集成组：**1,333 passed，2 skipped，99.36s**。覆盖整个 `tests/session_agent`、`tests/contracts`，以及执行数学、评级、宏观状态、决策卡、L3 资格、复核、E6、角色文档、证据登记、stock/macro 离线重放与 trace 完整性。
- 两个 skip 均为本隔离工作树缺少真实历史 run，未补造历史现场：L3 2026-07-09 冒烟、E6 真实 run 检查。
- 该结果是上述集成组，不是全仓所有测试；本轮未运行全仓全量测试。
- `config_standard`：0 违规；`git diff --check` 通过。独立审查发现的 v2 重复代码、冻结时间窗身份、读取响应截断和复核完成状态问题均已补反例修复。

### C1 收口与重复验证

- 新增 8 个反例先失败后通过：Claude 角色缺少 Read、Write、WebSearch 或 WebFetch；执行器 Web 能力为 False、None、UNKNOWN。只有 `HOST` 将能力判断委托给宿主。相关开发回归 **60 passed**，独立复核组 **56 passed**；审查无剩余 mustfix。
- 最终代码集成重跑：**1,340 passed，2 skipped，1 failed，107.13s**。唯一失败为既有 `test_timeout_kills_the_whole_process_group` 的 0.8 秒超时夹具尚未写入 grandchild.pid；该次失败原样保留，不记作全绿。
- 原代码不变，失败用例单独重跑 **1 passed，1.42s**；整个 `tests/session_agent` 再跑 **434 passed，67.38s**。结果提示该用例存在时序敏感性，尚不能据重跑排除偶发问题。
- 日志保存在源工作区 `context_codex/development/20260930-agent-skills/`：`agent-skills-integration-final.log`、`agent-skills-integration-final-c1.log`、`agent-skills-first-failure.log`。最后一份名称来自定位命令，内容为完整 session 组通过记录。

## 工作区交付

实现按保全基线的逐文件 SHA256 差异同步到源工作区；每个目标须仍等于基线或本批最终内容，遇到并发修改即停止。同步结果与文件清单以 `context_codex/development/20260930-agent-skills/implementation-manifest.json` 为准，同目录 `implementation.patch` 仅包含本批相对基线的差异。未提交 Git，保留用户原有未提交修改和独立 worktree。

本批已同步 **127 个文件，0 个冲突**，全部目标文件 SHA256 对账通过。源工作区追加运行角色能力、DecisionFrame 与文档预算回归：**94 passed，3.90s**；配置标准 **0 违规**，`git diff --check` 通过。
