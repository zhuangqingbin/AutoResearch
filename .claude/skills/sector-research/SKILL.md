---
name: sector-research
description: "Single A-share INDUSTRY (申万一级) research — 景气度/产业链/竞争格局/资金地形/龙头映射 (「研究半导体行业」「创新药板块怎么样」). Also owns the LITE sector brief scan-market invokes at Stage 1: a one-段 machine contract (`## 地形段`, terrain-only, feeds L3/L4). NOT for one ticker (→ stock-research), whole-market (→ scan-market), cross-asset (→ macro-research). Project-local."
---

# 单行业研究

## 输入与模式

输入行业、分析日和 FULL/LITE。默认 FULL；快速 brief 或 scan 行业地形使用 LITE。

- FULL：产业链、竞争格局、景气位置和龙头映射；规范角色为 `.claude/agents/sector-full.md`，按需参考 [sector-playbook.md](sector-playbook.md)。判断性结论只留在 standalone 报告。
- LITE：唯一机器段以 `## 地形段` 开头，数字、单位、日期、来源和缺口均来自冻结 pack。显式 `deterministic-v1` 候选由确定性 renderer 写字段，仅必要事件、冲突或关键缺口触发有界补充；补充事实须绑定来源，不能改写机算数字。
- 分类明确供应方、分类体系/版本、原始代码及映射；子行业不能冒充申万一级，无法映射时披露歧义。

## 数据与输出

standalone 使用本次行业快照；缺当日全市场扫描时由登记 prepare 取当前数据，不借旧日价格冒充当日。scan 覆盖按冻结行业列表执行，不按最终个股名单反向缩小行业覆盖。

复用只限仍有效的稳定事实，当日行情字段重新计算；市场/财务/事件/更正/分类输入变化使相关复用失效。旧整段 MD 缓存只能用于历史协议回放。行业弱不构成跳过该行业个股研究的门。

地形不含行业方向、个股评级或 `sector_healthy_top3`；该字段是 L5 专用。输出路径、实际复用来源与缺口随发布回执交付。

## 入口与交付

在项目根固定本宿主的 `AUTORESEARCH_ENGINE`，使用 `uv run --no-sync`。只读写本引擎的 `context_<engine>/`、`reports_<engine>/`，只有 `lake/` 共享。

显式 `session_v1` 的控制循环、请求样例与 CLI 以 [统一入口](../../../docs/session-agent/README.md) 为准。取数前运行 `uv run --no-sync python -m autoresearch.session_agent.task_access preflight --orchestration session_v1`。`CONFIGURED_UNVERIFIED` 只表示配置可用；真实双宿主验收未齐时仍为 **PILOT**，不自行切换默认入口。旧 Workflow 当前返回 `HOST_CAPABILITY_REQUIRED`，保留为维护参考，不能绕过能力门启动。

研究角色只读冻结任务包与角色片段；主会话负责认领、真实身份绑定、提交、恢复和发布。`finish` 后对机器返回的 canonical 报告路径及 run_id 执行 `verify-report --level full`；交付只引用该 VerificationResult，缺项原样披露。

## 决策约束

FULL/LITE 仅表示研究深度。交易结论统一使用冻结 DecisionFrame 的 `gap_c1_o2`：D1 收盘入场、D2 开盘退出。所属交易所、日历与截止时间必须有来源；UNKNOWN 限制执行可用性，不自动改写研究评级。情景收益需要明确假设入场价或区间；缺分母时不发布精确 EV/R:R。

个股评级与动作由共同的六维、三门和冻结阈值校验。保留模型原判断、机器建议及偏离理由；宏观和行业只向个股传递描述性地形。0 买日可以成立，不能放宽门凑单。规则实现以 `autoresearch/common/card_decision.py`、`autoresearch/contracts/execution.py` 为准。

## 复盘与候选实验

主会话按 [研究质量闭环](../../../docs/session-agent/research-quality-workflow.md) 保存诊断、冻结案例并比较候选；缺真实计量或人工标签保持未知，不自动改默认 profile。研究角色仍只读本次冻结任务包。
