# 单股、宏观、行业与档案 Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax. 按依赖顺序执行，不调用付费 LLM API，不替其他引擎执行验收。

**Goal:** 将扫描以外的全部研究入口迁入 session 任务协议，并让它们可被扫描工作流按原契约复用。

**Architecture:** 工作流放在 session_agent/workflows；领域取数、评级和组装继续由原模块负责。每个独立入口创建对应 run；嵌入扫描的 lite 任务复用父 run。

**Tech Stack:** 现有 Python 数据/研究模块、A01–A08 执行桥、Markdown/JSON 契约、capsule 和 pytest。

前置：[计划一](2026-09-13-session-agent-migration-01-foundation.md)完成。[总计划](2026-09-13-session-agent-migration.md)与[设计](../specs/2026-09-13-session-agent-migration-design.md)共同约束本文件。

---

## B01. 单股 LITE：第一个可用研究 Agent

**Files**

- Create：autoresearch/session_agent/workflows/__init__.py、workflows/stock.py。
- Modify：session_agent/roles.py、operations.py、validation.py、publication.py。
- Reuse：autoresearch/analyze/harvest.py、slim_io.py、runctl.py、run_profile.py；agents/utils/rating.py；scan/l4 的现有纯解析与 rubric。
- Read：.claude/skills/stock-research/lite-playbook.md、.claude/agents/l4-card.md。
- Test：tests/session_agent/test_stock_lite.py、test_stock_lite_context.py、test_stock_lite_resume.py。

工作流公开构造函数目标为 build_stock_plan(request, run_context)。request 含 ticker、date、mode、peers、asset_type、name；由 CLI 请求文件验证，不修改原 harvest 位置参数语义。

本节 request 指设计 §7.4.1 归一化后的领域请求。输出契约注册名 stock.lite.v1，绑定现有卡格式、评级和早停 validator；该名字只标识验证组合，不新增一套评分规则。

### 固定链

| task_id | 执行方式 | 输入 | 输出/验证 |
|---|---|---|---|
| stock.harvest | DETERMINISTIC | ticker/date、冻结配置 | 原 slim、deep、原有数据契约 |
| stock.card | INFERENCE | 当次 slim 与允许的领域上下文 | 原格式 lite 卡、评级与早停状态 |
| stock.validate | DETERMINISTIC | 卡与已验证输入引用 | 原 parse_rating、适用 rubric/字段检查 |
| stock.publish | DETERMINISTIC | 已接受卡 | 原 standalone lite 报告布局、run 留存 |

P0–P5 首版保留在同一个 stock.card 研究角色内部，避免拆成多个模型调用改变研究上下文与早停语义。task 内的证据读取仍可逐步进行并留痕。只有通过对照验证后，才另立任务拆分研究步骤。

- [ ] 构造三组离线 fixture：正常早停卡、完成 P4/P5 的满卡、取数失败。写固定输入和预期输出契约，不让测试调用模型。
- [ ] begin 委托 analyze.runctl.begin(mode=LITE)；正常路径使用现有 run_id 与 checkpoint；stock.harvest 使用 A04 argv builder。
- [ ] stock.card 的输入包不包含 deep 正文；survivor 进入 P4 时才允许读取登记 deep artifact。早停卡不能读取或捏造未核内容。
- [ ] 在顶层 validation 中适配现有卡解析器。若某解析器只接受 scan_dir，明确构造受限适配数据；不得让 analyze 反向 import scan 或复制 rubric。
- [ ] 发布前验证 FINAL TRANSACTION PROPOSAL 与五档 Rating 均可解析；结构失败不通过“补一个默认 Hold”修正。
- [ ] 独立 LITE 采用其既有复核义务；扫描 L4 的 OW/SELL ensemble 在 C02 处理，不能因为共用角色而无条件加到所有 standalone 路径。
- [ ] 通过任务提交与实际 host receipt 记录研究完成。LITE profile 的逻辑 card 阶段已有，新增实际角色证据应按本次是否派发决定，兼容旧 lite 无 agent_roles 的 profile。
- [ ] 原无 run 的 harvest/lite 用法继续工作，防止只为新入口破坏旧入口。

验收测试矩阵：

| 测试名 | 输入/动作 | 必须观察到 |
|---|---|---|
| test_lite_early_stop_does_not_read_deep | P3 原规则可早停 | deep 无读取记录，陷阱未核，评级不高于 Hold |
| test_lite_full_card_requires_deep_evidence | 满卡且 ≥OW | P4/P5 必需证据存在，不能只校标题 |
| test_no_data_never_publishes_card | A级取数错误/空稿 | 任务失败，不发布卡 |
| test_lite_resume_preserves_input_identity | harvest 完成后中断 | 复用本次已验输入，旧日期卡拒绝 |
| test_lite_card_authority_is_unchanged | run 冻结 legacy_md | 不把新 JSON 候选提升为权威 |
| test_inline_lite_has_no_child_capsule | 父 scan run 调用 | 无第二个 run 和重复 usage |

~~~bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest -q tests/session_agent/test_stock_lite.py tests/session_agent/test_stock_lite_context.py tests/session_agent/test_stock_lite_resume.py tests/analyze/test_runctl.py tests/contracts/test_research_card.py
~~~

**完成判据：** 在当前官方会话完成一只股票的真实 LITE 演练，能从任务包走到可验证报告。该演练只证明当前引擎；另一个引擎单独验收。

## B02. 单股 FULL：分节角色与最终决策

**Files**

- Modify：session_agent/workflows/stock.py、roles.py、operations.py、validation.py、publication.py。
- Reuse：autoresearch/analyze/assemble.py 的 DECISION_REL/SPINE/APPENDIX、runctl.py、harvest.py。
- Read：.claude/skills/stock-research/engine-playbook.md、company-intel.md、us-intel.md。
- Test：tests/session_agent/test_stock_full.py、test_stock_full_roles.py、test_stock_full_products.py。

### 分节与责任映射

| 逻辑角色 | 产物相对路径 | 必需性 | 前置 |
|---|---|---|---|
| stock.market | 1_analysts/market.md | 必需 | harvest |
| stock.news | 1_analysts/news.md | 必需 | harvest、适用情报 |
| stock.fundamentals | 1_analysts/fundamentals.md | 必需 | harvest |
| stock.quality | 1_analysts/quality.md | 可选 | 财报材料 |
| stock.valuation | 1_analysts/valuation.md | 可选 | 估值材料 |
| stock.positioning | 1_analysts/positioning.md | 可选 | 资金/持股材料 |
| stock.peer | 1_analysts/peer.md | 可选 | 同业材料 |
| stock.solvency | 1_analysts/solvency.md | 可选 | 偿付材料 |
| stock.reality_check | 2_research/reality_check.md | 可选 | 已完成分析师章节 |
| stock.bull | 2_research/bull.md | 必需 | 分析师章节 |
| stock.bear | 2_research/bear.md | 必需 | 按原一轮对撞顺序 |
| stock.manager | 2_research/manager.md | 必需 | bull、bear |
| stock.risk | 3_risk/debate.md | 可选 | manager |
| stock.premortem | 3_risk/premortem.md | 必需 | 研究与风险材料 |
| stock.pm | 4_portfolio/decision.md、calendar.md；2_research/variant.md、faceoff.md | 四个均必需 | manager、premortem、已选择风险章节 |

该表描述逻辑责任，不承诺每行都独立派发；先保持 playbook 顺序。若把某行放进同一会话执行，实际 usage 不能按“独立 agent”重复归因。

- [ ] 将 assemble 的必需文件集合通过读取声明生成测试预期，避免手工另建权威清单；本文的映射用于开发理解。
- [ ] 实现 FULL plan，默认保持现有角色顺序；可选 lens 的启用决定在任务展开前冻结并说明原因。
- [ ] A 股绑定 company-intel，美股绑定 us-intel；实际可用情报条件沿原 playbook，失败降级与查询额度不改。
- [ ] 引用已声明同业与 verified snapshot，不用角色自己的搜索结果覆盖价格真值。
- [ ] stock.pm 一次产出四个必需文件；submit 把它们作为一个原子接受集合验证，少一份就不完成该任务。
- [ ] 最终组装委托 analyze.assemble；required 章节缺失不能因为进程返回码为零就视为完整，先对照原声明检查产物。
- [ ] RunProfile 继续使用现有阶段 harvest/intel/write/assemble/publish；新逻辑角色到 write 的映射显式登记，不把角色名直接当阶段名。
- [ ] 记录输出正文继承 AGENTS.md 的交易尺度；保留深层基本面事实与长期催化事实，不把旧示例中的长期仓位当作当前策略。

用于测试必需集合的一段完整代码：

~~~python
from autoresearch.analyze import assemble


def required_full_products() -> set[str]:
    result = {assemble.DECISION_REL}
    for _, items in assemble.SPINE + assemble.APPENDIX:
        result.update(rel for _, rel, optional in items if not optional)
    return result


def test_full_required_products_match_current_assembler():
    required = required_full_products()
    assert "4_portfolio/decision.md" in required
    assert "2_research/faceoff.md" in required
    assert "3_risk/premortem.md" in required
    assert "1_analysts/peer.md" not in required
~~~

另外必须测试：PM 少一个文件、可选 lens 缺失、不同 ticker 的财报混入、第二次提交覆盖同次决策、情报失败披露、FULL 恢复不重复已验章节。

~~~bash
uv run --no-sync python -m pytest -q tests/session_agent/test_stock_full.py tests/session_agent/test_stock_full_roles.py tests/session_agent/test_stock_full_products.py tests/analyze/test_assemble.py tests/analyze/test_runctl.py tests/contracts/test_layering.py
~~~

**完成判据：** 所有必需文件经过原 assemble 生成报告，原单股 CLI 与 profile 兼容。FULL 和 LITE 均可作为新入口显式调用，尚不强制切换旧 skill。

## B03. 宏观 FULL/LITE 与 run 生命周期

**Files**

- Create：session_agent/workflows/macro.py、autoresearch/macro/run_profile.py、run_bootstrap.py。
- Modify：contracts/stages.py、contracts/profiles.py、common/workspace.py、common/run_identity.py、macro/harvest.py、assemble.py、state.py。
- Modify：trace/capsule.py、completeness.py、identity.py 中已知 kind 判断接缝，以现有 registry/显式 bootstrap 注入扩展。
- Test：tests/session_agent/test_macro.py、test_macro_lite.py、test_macro_run_kind.py；tests/macro 原相关测试。

### B03a. 扩展 kind 与 profile

- [ ] 先写旧 scan/stock profile 回归，再将 macro-research 同时登记到 RUN_KINDS、RUN_SPOOLS、RUN_REPORT_DIRS、PROFILE_FACTORIES。
- [ ] 使用 A07 增加的可选 per-profile role_stages 映射；宏观 factory 显式传自己的映射，默认 None 的旧 profile 行为不变。不得把新角色强塞成 scan 的 L4 角色。
- [ ] 为 FULL 定义阶段 harvest/intel/write/assemble/publish；LITE 定义 frame/write/publish。每个 profile 声明真实 captured_stages 与实际派发角色；旧 profile JSON 缺新字段时按原行为读取。
- [ ] 检查 common/run_identity 的 workspace_mode、旧 run kind 特判、capsule finalize/retain/find_run_root 都认识新 kind；仅修改一张表不能通过验收。
- [ ] 使用宏观 bootstrap 显式注入 capsule.begin_run，禁止默认落入 scan.run_bootstrap。

### B03b. 迁移内容与路径

FULL 固定分节来自 macro.assemble 的 DECISION_REL/SPINE/MESO/APPENDIX。当前必需产物为：

~~~text
1_spine/decision.md variant.md crossfire.md calendar.md premortem.md
2_meso/sector_map.md flows.md sentiment.md themes.md
3_regional/us.md china.md global.md
4_crossasset/rates.md fx.md equities.md commodities.md crypto.md
5_sinous/divergence.md desync.md geopolitics.md relative.md
~~~

可选：1_spine/debate.md、4_crossasset/credit.md、6_meso_evidence/industry_cycle.md。

- [ ] 修改 macro.harvest/assemble，新增可选的显式工作目录参数；没有新参数时保持旧路径与输出。新参数必须是 run handle 导出的受限路径，不接受模型任意位置。
- [ ] 按原区域 → 跨资产 → 中美专题 → 中观 → 综合 → 配置顺序构建任务；可选 global-intel 遵守现有 agent 的证据和查询契约。
- [ ] 两张配置表逐行 parse_rating；不能只验证全文第一个 Rating。宏观五档是配置语义，不套单股 rubric。
- [ ] macro_state 先生成 run 内候选，经原 state_readiness 验证后更新本引擎日期级最新视图；并发发布比较 as_of，旧结果不得覆盖较新视图。
- [ ] macro LITE 仅读取 strategist_pack.pack 及原 freshness 机制允许的 macro_state；独立入口有宏观 run，嵌入扫描时不建子 run。
- [ ] 如果 frame 目前只能按 scan 路径写，增加显式输出路径与 strategist_pack 同步投影适配；下层不得反向读取 session_agent。

必需测试：过期 macro_state 不注入、regime 不匹配不注入、sector_healthy_top3 等字段拒绝进入策略师包、独立 LITE 不触发全 L3/L4、FULL 缺任一必需章节不发布、旧 scan/stock run 仍可 finalize。

~~~bash
uv run --no-sync python -m pytest -q tests/session_agent/test_macro.py tests/session_agent/test_macro_lite.py tests/session_agent/test_macro_run_kind.py tests/macro tests/trace/test_capsule_kinds.py tests/contracts/test_registry_parity.py tests/common/test_workspace.py
~~~

**完成判据：** 独立宏观两档可在各自 run 内完成，扫描可调用同一 LITE 任务定义；macro_state 的旧消费方不需要知道 session_agent。

## B04. 行业 FULL/LITE 与数据前置

**Files**

- Create：session_agent/workflows/sector.py、autoresearch/sector/run_profile.py、run_bootstrap.py、publish.py。
- Modify：contracts/stages.py、contracts/profiles.py、common/workspace.py、common/run_identity.py、sector/pack.py、reuse.py、brief.py。
- Reuse：scan/frame.py、universe.py 的确定性数据准备；不调用全扫描研究链。
- Test：tests/session_agent/test_sector.py、test_sector_prerequisites.py、test_sector_products.py、test_sector_run_kind.py。

当前不存在 sector.assemble。本任务新增 publish 只负责验证/发布行业原格式产物，不发明新的研究结论模板。

- [ ] 原子扩展 sector-research 的 kind/spool/profile，复用 B03 已建立的通用 kind 支持；sector profile 的角色阶段来自自身声明。
- [ ] 行业名按现有申万映射验证，输出文件名采用 sector.pack 现有安全规则。Task ID 使用注册的行业编码，不能把中文名当 shell/path 片段。
- [ ] 前置任务检查同引擎、同分析日、已验 hash 的 L1/L2/行业所需输入。数据缺失时明确调 frame/universe 生成基础数据；不自动运行市场筛选建议、L3 或 L4。
- [ ] pack 增加 run-scoped 输出适配，旧 --scan-dir/--industries 语义不变。
- [ ] LITE 调原 sector.reuse；可复用时登记来源 run/日期/hash 和原复用 banner，不伪造当日重新研究。
- [ ] LITE 发布只接受地形段契约；“看多/回避/买卖”等原禁词与方向性结论不进 L3/L4。
- [ ] FULL 保留六节结构、可选 sector-intel、readthrough evidence_url 与 kind 限制；不把 ETF 当公司、不根据海外涨跌推断 A 股涨跌。
- [ ] publish 在 run 内完成完整性检查后输出到原 reports/sector 约定位置；同日同业文件有锁与输入身份检查，旧记录不静默覆盖新记录。

固定测试案例：

| 案例 | 预期 |
|---|---|
| 独立行业没有基础扫描数据 | 仅生成必要确定性准备任务 |
| 输入来自另一个 engine | 拒绝，不自动跨根复制 |
| 原 sector.reuse TTL/regime/momentum 条件满足 | 使用原复用结果并记录来源 |
| FULL 报告被提交为 LITE | 输出契约拒绝 |
| LITE 中夹带行业方向 | 不通过地形校验 |
| readthrough.kind=etf 且正文当公司财报主体 | 原事实约束测试失败 |
| 缺真实历史估值分位 | 输出明确缺失，不填模型估计 |

~~~bash
uv run --no-sync python -m pytest -q tests/session_agent/test_sector.py tests/session_agent/test_sector_prerequisites.py tests/session_agent/test_sector_products.py tests/session_agent/test_sector_run_kind.py tests/sector tests/common/test_workspace.py
~~~

**完成判据：** 独立行业不再隐式依赖“用户已经跑过全扫”的假设；扫描内嵌 brief 仍由父 run 留存，FULL 内容不泄漏给 L3/L4。

## B05. 首覆档案与安全发布

**Files**

- Create：session_agent/workflows/dossier.py、autoresearch/dossier/run_profile.py、run_bootstrap.py。
- Modify：contracts/stages.py、contracts/profiles.py、common/workspace.py、common/run_identity.py、dossier/builder.py。
- Reuse：dossier/prefetch.py、schema.py、pool.py、reconcile.py、delta.py、debt_schedule.py、debt_slo.py。
- Read：.claude/workflows/dossier-init.js、.claude/agents/dossier-init.md。
- Test：tests/session_agent/test_dossier.py、test_dossier_publish.py、test_dossier_run_kind.py。

- [ ] 扩展 dossier-init kind、INIT 模式和 skeleton/research/lint/publish 阶段。
- [ ] builder 新增候选输出路径选项，默认不变。session 路径生成 run 内候选，保留现有档案初始 hash。
- [ ] prefetch 失败沿现有可降级语义记录真实原因，不把 shell 的 || true 翻译成取数成功。
- [ ] 研究角色只得到允许编辑的 LLM 节、摘要和参考证据；提交后比较确定性分节的字节/hash，改变即拒绝。
- [ ] 调 schema.lint_dossier 检查完整性、摘要上限和必需结构；未初始化档案不能标 initiated。
- [ ] 发布时锁定目标档案，比较开场 hash 与当前 hash；他人修改过则报 CONFLICT，不覆盖。
- [ ] 成功后按现有 pool/builder 接缝更新既有状态；季度 reconcile 和债务日检仍是显式确定性操作，不加入模型学习。

必需测试：模型改确定性节、摘要超限、骨架未完成、prefetch 降级、并发人工修改、重复发布、另一引擎档案引用。

~~~bash
uv run --no-sync python -m pytest -q tests/session_agent/test_dossier.py tests/session_agent/test_dossier_publish.py tests/session_agent/test_dossier_run_kind.py tests/dossier
~~~

**完成判据：** 原 dossier-init workflow 的业务行为由新计划承接；已有人工档案和季度流程不受破坏。

## B06. 全项目服务边界与证据工具清单

**Files**

- Modify：session_agent/operations.py、artifacts.py、roles.py、validation.py。
- Create：docs/session-agent/tool-catalog.md、tests/session_agent/test_tool_boundaries.py、test_news_evidence.py。
- Read：autoresearch/news、derivatives、broker、research、ops 的当前公开 CLI 和生产调用者。

工具目录必须逐项列 operation ID、输入形状、副作用、输出 artifact、调用者、错误分类、查询/并发额度、是否幂等。以下为必需分类：

| 服务 | 研究 agent 权限 | 禁止自动执行 |
|---|---|---|
| data/dataflows | 调原 harvester/已登记数据请求，保留 A 级契约 | 任意写 lake、改源绕过契约 |
| news/claim | 原文读取、来源绑定、既有证据校验 | 把摘要升级为原文事实、由模型覆盖价量真值 |
| derivatives | 仅原有消费面允许的确定性读数 | 将原只给人看的字段注入评级面 |
| broker | 用户明确导入任务下的确定性 ingest/reconcile | 自动下单、擅自修改成交记录 |
| research | 读取迁移效率与契约评估结果 | 自动校准、promote、权重回注 |
| ops | 明确命令下的原预热和备份 | 自动删除备份、无限周期运行 |

- [x] 对当前公开 CLI 做调用者核查，目录说明标“保留独立 CLI”或“进入 agent operation”，不能所有模块一键暴露。
- [x] 原生 WebSearch/WebFetch 的结果必须有分析日、来源层级、canonical 跟进状态；任务包中网页文本不能改变角色规则和工具权限。
- [x] 来源数据缺少发布时间精度时保持未知，不假定盘前已知。未来数据测试必须基于真实 available-at 字段或明确缺失。
- [x] 数据、工具参数和报告中不输出 .env、token、订阅凭据；身份快照继续使用原脱敏逻辑。
- [x] 为每个工作流确认所有 operation 都在工具目录中有唯一实现与 validator。
- [x] 运行研究工作流集成回归；未连接模块也需在 catalog 说明保留原因，避免“整个项目迁移”遗漏边缘入口。

~~~bash
uv run --no-sync python -m pytest -q tests/session_agent/test_tool_boundaries.py tests/session_agent/test_news_evidence.py tests/session_agent/test_stock_lite.py tests/session_agent/test_stock_full.py tests/session_agent/test_macro.py tests/session_agent/test_sector.py tests/session_agent/test_dossier.py tests/contracts/test_layering.py
~~~

**本分计划验收：** 股票、宏观、行业两档与档案均有可调用新入口；数据服务和周边模块全部完成边界登记。研究规则仍由原领域代码与当前 playbook 决定。
