---
name: scan-market
description: "Use when the user wants to scan the WHOLE A-share market to discover buy-worthy stocks AND strong sectors — 「扫描全A股」「全市场选股」「哪些板块值得买」「find the best A-share buys」. Deterministic L0-L2 funnel + Claude L3/L4/L5; artifacts → engine-specific scan reports. NOT for: one named ticker (→ stock-research; 持仓单票复核走其 lite 档), cross-asset macro (→ macro-research). Project-local."
---

# 全 A 股扫描

## 输入与流程

输入已结算分析日、冻结配置和可选保送票。日期由交易日历确定；A 股用 tushare，需 `TUSHARE_TOKEN`。L0–L2 确定性收窄，L3 比较精排，L4 逐票生成决策卡，L5 确定性组装。

角色契约分别在 `.claude/agents/l3-rank.md`、`l4-intel.md`、`l4-card.md`、`macro-brief.md`、`sector-brief.md`。L3/L4 使用独立研究上下文；主会话不代写研究判断。运行图以冻结 session plan 为准，机制与维护资料按需读 [STAGES.md](STAGES.md)；运维见 `docs/ops/scan-ops.md`。

GATE1 同时决定 run_mode；单步维护命令必须带 `--decide-run-mode`。下游只读冻结 `run_mode.json`，不从 finalist 是否为空猜模式。哨兵日和保送持仓仍执行其必需研究与复核。learning 层已退役；历史收益不回注 L3/L4 提示。

## 配置


- **唯一参数事实源 = `scan_config.jsonc`**(值 + 每键一行作用)。键的块 / 类型 / 缺省 / 分区 / 宿主 / 生效点登记在注册表 `autoresearch/contracts/scan_config.py`,白名单由它派生。例外两个:保送票清单 `pinned.jsonc`;L1 校准权重 `$CTX/factor_lab/weights.json`(仅 calibrated 档读)。凭证只在 `.env`。
- **装载链**:`frame --json` 白名单校验后回显 → Workflow `args.config`(L4 每股 `args.cfg` 透传);确定性 CLI 经 `user_config.knob()` 读同一文件。优先级:CLI 显式 flag > 文件 > 代码内建默认。**传 `{}` = 静默关 intel + 降 effort,三个 workflow 直接 throw**。
- **配置标准**：编辑 skill、配置或消费者前必须读同目录 `config-standard.md` 的九条规则；改后运行 `uv run --no-sync python -m autoresearch.scan.config_standard`，零违规才可交付。
- **性能开关不拥有评级**：`performance.streaming_l4` 仅供 legacy workflow；不得改 finalist cap、三门、主尺或 BUY 数量。
- **新增键三件套** = 注册表登记 + 真实消费点(两宿主都接)+ 测试锁(`tests/scan/test_config_knobs.py` 或同族)。历史、证据、回滚讨论进 git log 或 `docs/research`,不进配置文件。

## 过程直播契约

只转播实际任务与产物，不根据已有卡片文件推断完成：

| # | 时机 | 播什么 | 怎么拿 |
|---|---|---|---|
| CP0 | Stage0 完 | regime + 温度 + 策略师定调句 | `market_pack.json` + `market_view.md` §1 首句 |
| CP1 | GATE1 过 | **前奏汇总屏全文** | Read `_prelude_summary.md` 全量转播 |
| CP2 | 行业 brief 齐 | 每行业一句地形定调 | 各 `sector_briefs/*.md` 首句(可与 CP3 合并) |
| CP3 | GATE2 过 | 入围名单逐只 + 被切影子 | workflow `L3入围` 日志 + `_l3_pass1_cut.csv` |
| CP4 | L4 派发 | 派发 N 股 + 预算旗 + intel 开关 + 📌保送名单 | workflow 日志 |
| CP5 | L4 进行中 | 每出一张卡播一行 k/N 代码 名称 评级 | `l4_watch` Monitor 自动播 |
| CP6 | L4 全完 | 评级分布 + 停因分桶 + OW三门直方图 | `autoresearch.scan.render <date> --view gate_hist` |
| CP7 | GATE4 过 | **`brief.md` 原文全量转播** + 产物路径 + 分段耗时 + token 真计量 | Read `$RPT/scan/<run_id>/brief.md` + `render --view timing` + `usage_harvest` |

**汇报(CP7)**：按 VerificationResult 核验后原文转播 `brief.md`，附 canonical 路径、评级分布、停因分桶与缺项。机器消费者不读 `summary.md` 正文，结构化记录是判定来源。

进度使用 session `status` 与 `autoresearch.scan.render`；失败按错误分类局部恢复，RATE_LIMIT/CONNECTION/TIMEOUT 不等同 schema 或数据完整性错误。调度和恢复详见 `docs/session-agent/local-recovery.md`、`scheduling.md`。每个 attempt 独占输出，迟到写入不覆盖已接受产物。

计量来自 `autoresearch.trace.usage_harvest` 和冻结 metering；实际 token、cache、代理量、估算加权价格分栏。缺失为 UNMEASURED，同组不足 10 次完整真实运行时为 IMMATURE。机器消费者不解析 summary 正文来补计量。

## 会话与档案

改动 hook 或角色定义后须重载宿主；修改 `.codex/agents` 或 `.codex/hooks.json` 后重开 Codex，新 hook 在启动审查中批准一次才生效。配置存在不证明当前宿主已加载。

首覆档案使用登记的 `dossier-init / INIT` session 任务，角色只执行任务包。季度对账保留旧值和来源；稳定事实、动态快照、假设分别判断有效性。本次 slim、新闻、主尺与执行条件都重新生成。

## 入口与交付

在项目根固定本宿主的 `AUTORESEARCH_ENGINE`，使用 `uv run --no-sync`。只读写本引擎的 `context_<engine>/`、`reports_<engine>/`，只有 `lake/` 共享。

启动扫描前检查互斥锁，失败立即终止当前启动块：

```bash
uv run --no-sync python -m autoresearch.scan.run_lock check || { scan_lock_status=$?; echo "拒绝开扫：扫描锁检查未通过"; exit "$scan_lock_status"; }
```

显式 `session_v1` 的控制循环、请求样例与 CLI 以 [统一入口](../../../docs/session-agent/README.md) 为准。取数前运行 `uv run --no-sync python -m autoresearch.session_agent.task_access preflight --orchestration session_v1`。`CONFIGURED_UNVERIFIED` 只表示配置可用；真实双宿主验收未齐时仍为 **PILOT**，不自行切换默认入口。旧 Workflow 当前返回 `HOST_CAPABILITY_REQUIRED`，保留为维护参考，不能绕过能力门启动。

可选 runner 入口 `session_agent run --executor mailbox` 仍为 PILOT，宿主循环只见 [统一入口](../../../docs/session-agent/README.md)。

研究角色只读冻结任务包与角色片段；主会话负责认领、真实身份绑定、提交、恢复和发布。`finish` 后对机器返回的 canonical 报告路径及 run_id 执行 `verify-report --level full`；交付只引用该 VerificationResult，缺项原样披露。

## 决策约束

FULL/LITE 仅表示研究深度。交易结论统一使用冻结 DecisionFrame 的 `gap_c1_o2`：D1 收盘入场、D2 开盘退出。所属交易所、日历与截止时间必须有来源；UNKNOWN 限制执行可用性，不自动改写研究评级。情景收益需要明确假设入场价或区间；缺分母时不发布精确 EV/R:R。

个股评级与动作由共同的六维、三门和冻结阈值校验。保留模型原判断、机器建议及偏离理由；宏观和行业只向个股传递描述性地形。0 买日可以成立，不能放宽门凑单。规则实现以 `autoresearch/common/card_decision.py`、`autoresearch/contracts/execution.py` 为准。

## 复盘与候选实验

主会话按 [研究质量闭环](../../../docs/session-agent/research-quality-workflow.md) 保存诊断、冻结案例并比较候选；缺真实计量或人工标签保持未知，不自动改默认 profile。研究角色仍只读本次冻结任务包。
