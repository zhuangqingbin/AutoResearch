---
name: stock-research
description: Two-tier single-ticker research. FULL deep-dive report by default (「研究 NVDA」「分析 600519.SS」, peers ok); LITE decision card (5-tier rating + 隔夜口径 R:R + tripwires) when speed is asked (「快速看一眼」「出张决策卡」) — lite is also the workhorse scan-market L4 invokes per finalist and the pinned-holdings review path on sentinel days. NOT for whole-market scans (→ scan-market) or macro (→ macro-research). Project-local.
---

# 单标的研究

## 输入与模式

输入标的、分析日及可选同业；A 股可以六位代码输入，跨市场身份保留真实标的和 venue。默认 FULL；“快速看一眼 / 出张卡 / 持仓复核”为 LITE。全市场 finalist 恒走 LITE；`holding_review` 在新 run 中关联前一 frame，保持原分析锚并冻结新截止与输入。

- FULL：决策卡在前，完整证据附录在后；规范角色为 `.claude/agents/stock-full.md`，按需参考 [engine-playbook.md](engine-playbook.md)。
- LITE：渐进 DD 与早停，规范角色为 `.claude/agents/l4-card.md`；standalone 差异见 [lite-playbook.md](lite-playbook.md)。早停不豁免业务门，要求深核时必须有实际已读证据。
- 两段独立初判是冻结的可选 profile，不能在运行中临时补读后改称独立研究。

## 数据与输出

A 股数据走 tushare，需 `TUSHARE_TOKEN`；其他标的使用登记的数据适配器。价格与指标以本次 verified market snapshot 为真值；缺失、冲突、未核来源分别披露。报告首屏给出标的、市场、数据截止、D1/D2、研究评级、入场状态、假设 entry、三门、风险和退出安排。

档案 INIT 只通过登记的 `dossier-init / INIT` session 能力启动，研究角色见 `.claude/agents/dossier-init.md`。档案按稳定事实、动态快照和研究假设逐项复核；本次 slim、新闻与执行条件重新生成，不复用整卡。

## 入口与交付

在项目根固定本宿主的 `AUTORESEARCH_ENGINE`，使用 `uv run --no-sync`。只读写本引擎的 `context_<engine>/`、`reports_<engine>/`，只有 `lake/` 共享。

显式 `session_v1` 的控制循环、请求样例与 CLI 以 [统一入口](../../../docs/session-agent/README.md) 为准。取数前运行 `uv run --no-sync python -m autoresearch.session_agent.task_access preflight --orchestration session_v1`。`CONFIGURED_UNVERIFIED` 只表示配置可用；真实双宿主验收未齐时仍为 **PILOT**，不自行切换默认入口。旧 Workflow 当前返回 `HOST_CAPABILITY_REQUIRED`，保留为维护参考，不能绕过能力门启动，`--legacy-reason` 也不构成能力豁免。

研究角色只读冻结任务包与角色片段；主会话负责认领、真实身份绑定、提交、恢复和发布。`finish` 后对机器返回的 canonical 报告路径及 run_id 执行 `verify-report --level full`；交付只引用该 VerificationResult，缺项原样披露。

## 决策约束

FULL/LITE 仅表示研究深度。交易结论统一使用冻结 DecisionFrame 的 `gap_c1_o2`：D1 收盘入场、D2 开盘退出。所属交易所、日历与截止时间必须有来源；UNKNOWN 限制执行可用性，不自动改写研究评级。情景收益需要明确假设入场价或区间；缺分母时不发布精确 EV/R:R。

个股评级与动作由共同的六维、三门和冻结阈值校验。保留模型原判断、机器建议及偏离理由；宏观和行业只向个股传递描述性地形。0 买日可以成立，不能放宽门凑单。规则实现以 `autoresearch/common/card_decision.py`、`autoresearch/contracts/execution.py` 为准。

## 复盘与候选实验

主会话按 [研究质量闭环](../../../docs/session-agent/research-quality-workflow.md) 保存诊断、冻结案例并比较候选；缺真实计量或人工标签保持未知，不自动改默认 profile。研究角色仍只读本次冻结任务包。
