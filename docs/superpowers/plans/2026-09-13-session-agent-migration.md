# 订阅会话研究 Agent 全项目迁移 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax. 默认在当前任务内顺序执行；本计划本身不授权额外子 agent、跨引擎代跑或付费模型调用。

**Goal:** 将全市场、单股、宏观、行业和首覆档案迁入统一研究任务协议，继续由 Codex/Claude Code 订阅会话完成推理。

**Architecture:** 官方会话负责模型与工具调用；Python 集成层负责固定流程、任务交接、产物校验和发布。复用已有 L4 taskbook、run capsule、数据湖、研究配置和评级规则，逐入口切换。

**Tech Stack:** Python ≥3.10、现有 uv 环境、pytest、JSON/Markdown、现有文件锁与原子写工具；官方 Codex/Claude Code 交互会话。基础版本不新增 LLM provider SDK、LangGraph、数据库或 MCP 服务。

**Status:** 文档完成待开发；下列任务均未因本次文档编写而实施。基线 48d1d4e，2026-09-13。

---

## 1. 阅读顺序与交付文件

1. [架构与协议设计](../specs/2026-09-13-session-agent-migration-design.md)：范围、不变量、接口、状态与迁移规则。
2. [计划一：协议、执行桥与双宿主](2026-09-13-session-agent-migration-01-foundation.md)：A01–A08。
3. [计划二：单股、宏观、行业与档案](2026-09-13-session-agent-migration-02-research.md)：B01–B06。
4. [计划三：扫描、验收与切换](2026-09-13-session-agent-migration-03-scan-cutover.md)：C01–C06。

文档中的新路径、CLI、类型和函数均为拟开发目标；标为“复用”的函数才是当前实现。不允许从文档中复制未来命令后声称今天已经可用。

## 2. 固定决策

- 只用订阅交互 session；不启动 API 模型调用、不提取订阅凭据。
- 确定性运行工具由宿主调用；代码返回待研究任务，模型在宿主内完成。
- 四个项目 skill 名称和软链发现方式保持；两边共用研究正文与契约。
- 只保留一个评分/校验/发布实现；session_agent 作为最上层集成包。
- 扫描 L4 的状态仍只有 l4_tasks 一份；新 store 只拥有非 L4 交接任务。
- 新生效参数仅 orchestration 选择与宿主能力记录；研究参数继续读取 scan_config.jsonc 及已有来源。
- 首期保持 research card 当前权威形式，不切换 card_source、不恢复学习闭环。
- 先单股、再其余研究入口、最后全市场；最终移除旧业务编排中的重复流程。

## 3. 工作包与依赖

| 任务 | 交付物 | 依赖 | 完成证据 |
|---|---|---|---|
| A01 | 当前入口/产物/宿主能力基线 | 无 | 清单、现有相关测试结果、能力有来源 |
| A02 | 任务、计划、展开、提交契约 | A01 | 精确字段、坏输入拒绝、身份测试 |
| A03 | 非 L4 store、DAG 和展开 | A02 | 并发认领、幂等、动态候选测试 |
| A04 | artifact 登记和受限 operation 执行 | A02–A03 | 路径攻击、实退出码、失联不重启测试 |
| A05 | 双宿主能力和角色映射 | A01–A04 | 显式 fallback、独立上下文证据、未知能力处理 |
| A06 | begin/next/claim/execute/submit/resume/finish CLI | A03–A05 | 合成工作流从命令行完整跑通 |
| A07 | capsule、回执和 usage 衔接 | A06 | 缺证据诚实、无重复计量、失败现场可验证 |
| A08 | 基础故障矩阵与开发者教程 | A07 | 冷启动、恢复、损坏记录、边界文档 |
| B01 | 单股 LITE 的独立入口 | A08 | 早停/满卡/无数据三路径 |
| B02 | 单股 FULL 工作流 | B01 | 必需章节、可选 lens、原组装器 |
| B03 | 宏观 FULL/LITE 与新 kind | A08、B01 | 两张表、macro_state、父子 run 分母 |
| B04 | 行业 FULL/LITE 与新 kind | B03 | 数据前置、仅地形、行业复用、报告 |
| B05 | 首覆档案与新 kind | A08、B02 | LLM 节边界、并发发布、schema lint |
| B06 | 数据/新闻/衍生品/券商/运维接缝清单 | B01–B05 | 工具 allowlist 与副作用边界 |
| C01 | 扫描前奏、模式、L3 | B03–B04、A08 | 四模式、GATE1/2、候选与输入投影 |
| C02 | 扫描 L4、情报与复核 | C01、B01、B05 | taskbook 单一权威、attempt、复核矩阵 |
| C03 | L5、观察、GATE4 与 CP0–CP7 | C02、A07 | brief/summary/DecisionRecord 与最终门 |
| C04 | 同引擎对拍与真实会话验收 | B06、C03 | 两宿主分别出具验收、真实计量 |
| C05 | 四个 skill 切换与旧编排收敛 | C04 | 无旧业务调用者、回退演练 |
| C06 | 文档、学习演练、最终验收 | C05 | 全部矩阵通过、已知限制、操作手册 |

主依赖链：A01 → A02 → A03/A04 → A05 → A06 → A07 → A08 → B01 → B02 → B03/B04/B05 → B06 → C01 → C02 → C03 → C04 → C05 → C06。

B03/B05 在基础稳定后可独立开发；共享 contracts/workspace/profile 变更按顺序合并。不能让多个实现同时改 run kind 注册表。

## 4. 每个工作包的执行纪律

- [ ] 读取对应分计划、当前代码和现有测试；如果基线已前移，记录变化，不按旧签名盲改。
- [ ] 新契约或状态行为先写有区分力的测试，确认失败来自目标功能未实现。
- [ ] 按任务中的具体算法和接口实现；原领域计算保留原函数调用。
- [ ] 运行任务指定的测试；通过后做一次必要集成检查。
- [ ] 核对变更范围、文档、事件/产物登记与依赖方向。
- [ ] 以该任务列出的文件形成独立提交；禁止 git add . 混入其他工作。
- [ ] 更新本任务的验收证据，只有实际完成才勾选。

文档或低风险措辞修正不新增镜像测试。命令均在仓库根运行，并在每个新 shell 先执行：

~~~bash
export AUTORESEARCH_ENGINE=codex
~~~

Claude 实施者设置自己的 engine。Codex 的测试使用临时双根夹具验证隔离，不读取真实 Claude 产物。Claude 宿主真实验收由 Claude 会话本人执行并在自身目录保存记录。

## 5. 当前资源到目标的总映射

| 资源组 | 保留内容 | 新集成点 | 何时可收敛旧入口 |
|---|---|---|---|
| scan | L0–L2、L3守卫、L4 rubric、taskbook、L5、门 | workflows/scan、legacy_scan | C05 |
| analyze | harvest、slim_io、assemble、runctl、ledger | workflows/stock | B01/B02 验收后切该 skill 路由 |
| macro | harvest、assemble、state、tushare_macro | workflows/macro、新 run_profile/bootstrap | B03 |
| sector | pack、reuse、brief | workflows/sector、新 run_profile/bootstrap/publish | B04 |
| dossier | builder、prefetch、schema、pool、reconcile、delta | workflows/dossier、新 run_profile/bootstrap | B05 |
| data/dataflows/common | 数据与计算、路径与契约 | operations 调原模块 | 不迁成 LLM、不批量搬文件 |
| news | 原文、claim、acceptance、ledger | artifact 输入和来源验证 | 不另建新闻事实库 |
| derivatives | options_lake、qvix、cb_gate、style_spread | 只保留原允许消费面 | 不扩展成买卖判断输入 |
| broker | ingest、schema、store、reconcile | 显式用户任务的确定性服务 | 不增加 agent 自动交易 |
| research | 离线实验、概率/效率/执行评估 | evaluation 只读接口 | 不把审计结果自动喂回研究 |
| ops | prewarm、backup 等原运维职责 | 保留显式 CLI | 不触发研究 agent 无边界巡检 |
| trace/contracts | 原现场、字段、阶段、profile | 新计划/回执登记 | 最终仍是一套权威 |
| .claude skills/agents | 研究正文和用户入口 | roles、hosts 与 CLI | 正文不复制成两份 |

## 6. 关键接口的现有签名

以下在基线核验过，实施时再次检查定义，不调用同名的旧设计伪接口：

~~~text
# autoresearch.trace.capsule
begin_run(kind, analysis_date, engine, config, *, now=None,
          session_ref=None, bootstrap=None)
finalize(run_id, business_status, report_dir=None, *, error=None,
         replay_stages=(), profile=None, now=None)

# autoresearch.trace.exec_capture
run_captured(handle, stage, argv, invocation_id, attempt=1, subject=None,
             *, drain_grace=1.0, termination_grace=5.0)

# autoresearch.scan.l4_tasks
initialize(date, codes, *, root=None, context_root=None, meta=None,
           caps=None, now=None)
preflight(book, code, *, expected_attempt=None, now=None,
          stale_after_seconds=3600)
mark_success(book, code, *, expected_attempt=None, now=None)
mark_failure(book, code, error_class, *, error=None,
             expected_attempt=None, now=None)
dispatch_batches(book, *, caps=None, now=None)

# autoresearch.analyze.runctl
begin(ticker, analysis_date, *, mode, session_ref=None,
      peers=None, asset_type="stock", name=None)
record_stage(stage, *, status="SUCCEEDED", inputs=(), outputs=(),
             metrics=None, error=None)
finalize(run_id, *, report_dir=None, status="SUCCEEDED", reason=None)
~~~

以上为接口签名索引，不是可直接执行的 Python 程序。exec_capture 默认值按当前内部常量展开，调用方继续使用默认值。新增接口的完整字段与错误定义在设计 §7，具体适配在分计划中。

## 7. 验收证据布局

实现时的证据写入本引擎 context_root()/migration/session_agent/<git_sha>/，该路径通过 workspace 生成，不写共享可变结果目录。

~~~text
inventory.json                基线文件/签名/生产消费者
host_capabilities.json        实际宿主能力与来源
test_results.json             命令、时间、退出码、测试计数
workflow_acceptance.json      各入口、模式、案例、run_id
comparison.json               同引擎旧/新输入身份与结果差异
efficiency.json               真实计量、覆盖情况、样本分层
rollback.json                 回退步骤、原run状态与新run身份
~~~

这些是迁移验收产物，不是新业务账本，也不喂 L3/L4。仅有文档、单测或合成 host 回执不能填充真实宿主演练字段。

## 8. 完成判据

- [ ] A01–C06 均有实际开发、测试或验收证据。
- [ ] 四类研究的 FULL/LITE、档案 INIT 和扫描四模式均覆盖。
- [ ] 两个官方会话宿主分别通过本引擎验收，任何缺项明确列出。
- [ ] 原研究规则、产物机器接口、引擎隔离和冻结项无漂移。
- [ ] 完成的 L4 任务不能被重放重置；失败与中断不产生伪成功报告。
- [ ] 旧 CLI 与历史 capsule 仍可读取；新入口回滚不改旧现场。
- [ ] 性能宣称有成熟样本支撑；没有提升就如实记录，不降低研究质量凑数字。
- [ ] 项目手册、四个技能和学习教程已对齐实际运行入口。

## 9. 已知实施成本

主要工作量在跨宿主任务交接、现有状态权威衔接、宏观/行业/档案生命周期补齐，以及扫描复核与法证证据对齐。不能把“包一层 CLI”视为全项目迁移完成。

学习优先级依次为：一个任务的工具循环 → 上下文与结构化产物 → 多角色与分支 → 恢复与幂等 → 证据与评估。先建立每一层的可解释行为，再优化调用次数。
