---
name: macro-research
description: "Top-down GLOBAL + 中美 macro → cross-asset tilts AND A股行业配置 read (「研究全球宏观」「现在该超配什么资产」). Also owns the LITE 市场研判 daily brief: invoked by scan-market Stage 0 or 「今天大盘怎么看」, writes market_view.md from the deterministic market_pack. NOT for one ticker (→ stock-research), a full A-share screen (→ scan-market), or single-industry depth (→ sector-research). Project-local."
---

# 全球宏观与市场研判

## 输入与模式

输入分析日及 FULL/LITE。默认 FULL；“今天大盘怎么看”或 scan Stage 0 使用 LITE。

- FULL：冻结宏观原始数据、全球情报和决策时间，生成跨资产配置表与 A 股行业配置表。规范角色为 `.claude/agents/macro-full.md`；按需参考 [macro-playbook.md](macro-playbook.md)。
- LITE：从确定性 market_pack 写 `market_view.md`；规范角色为 `.claude/agents/macro-brief.md`。只有描述性地形进入 L3/L4，配置方向留给 L5/独立报告。
- FULL 的 `six_groups_v1` 是显式冻结候选；默认仍为 `serial21`。组内全部产物通过才接受，失败只重试对应组。两种布局保留相同必需产物；质量、证据与真实成本对拍完成前不宣称六组更优。

## 数据与输出

宏观数据使用登记的 FRED、tushare 等来源；`FRED_API_KEY`、`TUSHARE_TOKEN` 由环境提供。数字来自冻结 context，网查必须有来源、日期和可用时间；情景概率、政策路径与相关性假设标为判断。

交付 regime、两张配置表、触发条件、来源缺口和报告路径。宏观配置背景不替代个股隔夜三门；LITE 不输出个股评级。

## 入口与交付

在项目根固定本宿主的 `AUTORESEARCH_ENGINE`，使用 `uv run --no-sync`。只读写本引擎的 `context_<engine>/`、`reports_<engine>/`，只有 `lake/` 共享。

显式 `session_v1` 的控制循环、请求样例与 CLI 以 [统一入口](../../../docs/session-agent/README.md) 为准。取数前运行 `uv run --no-sync python -m autoresearch.session_agent.task_access preflight --orchestration session_v1`。`CONFIGURED_UNVERIFIED` 只表示配置可用；真实双宿主验收未齐时仍为 **PILOT**，不自行切换默认入口。旧 Workflow 当前返回 `HOST_CAPABILITY_REQUIRED`，保留为维护参考，不能绕过能力门启动。

研究角色只读冻结任务包与角色片段；主会话负责认领、真实身份绑定、提交、恢复和发布。`finish` 后对机器返回的 canonical 报告路径及 run_id 执行 `verify-report --level full`；交付只引用该 VerificationResult，缺项原样披露。

## 决策约束

FULL/LITE 仅表示研究深度。交易结论统一使用冻结 DecisionFrame 的 `gap_c1_o2`：D1 收盘入场、D2 开盘退出。所属交易所、日历与截止时间必须有来源；UNKNOWN 限制执行可用性，不自动改写研究评级。情景收益需要明确假设入场价或区间；缺分母时不发布精确 EV/R:R。

个股评级与动作由共同的六维、三门和冻结阈值校验。保留模型原判断、机器建议及偏离理由；宏观和行业只向个股传递描述性地形。0 买日可以成立，不能放宽门凑单。规则实现以 `autoresearch/common/card_decision.py`、`autoresearch/contracts/execution.py` 为准。

## 复盘与候选实验

主会话按 [研究质量闭环](../../../docs/session-agent/research-quality-workflow.md) 保存诊断、冻结案例并比较候选；缺真实计量或人工标签保持未知，不自动改默认 profile。研究角色仍只读本次冻结任务包。
