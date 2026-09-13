# scan-market 漏斗证据、双引擎与门调度设计

> 日期：2026-09-13；状态：已批准实施。
> 来源：结合原 brainstorm、代码核查与投资方法评审后的替代设计。
> 实施计划：[漏斗对照实验](../plans/2026-09-13-funnel-variant-lab.md)、[双引擎角色配置](../plans/2026-09-13-dual-engine-agent-config.md)、[GATE1 调度合并](../plans/2026-09-13-gate1-workflow-consolidation.md)。

## 1. 决策摘要

本轮不直接删召回通道、不删除 pass1、不调整 25 的截断、不把最终票数强改成 3–5，也不删除 GATE1/GATE2。先交付可重复的同日、同预算、同收益口径比较能力，再由配对样本决定生产改动。

立即实施三项基础设施：

1. 漏斗变体实验：逐日比较 current、composite_only、composite_plus_diversifiers，保持 L1/L2/pass1 名额一致，输出成员变化、原因、隔夜毛收益与相对当日市场中位数的超额。
2. 双引擎角色配置：角色意图与 Claude/Codex 执行参数分离；产物同时记录 declared expectation、runtime capability 与 actual execution，避免把配置当作已执行事实。
3. 门调度瘦身：保留 GATE1/GATE2 校验语义，只合并 GATE1 后的运行模式判断；L2 缺失重试仍保持独立，L4 preflight/prepare 不在本轮合并。

生产默认值保持：多通道召回、pass1=25、knife 硬门、普通 finalist 上限 5、composite BUY seats 规则不变。研究结果达到预注册门槛后，才单独提生产变更。

## 2. 对原 brainstorm 的关键修正

### 2.1 召回与 composite

低 Jaccard、较多 unique 只能证明候选成员不同，不能证明通道提供了独立经济信号；Recall@1000 的置信区间跨 1 只能说明样本没有检出增益，不能证明增益为零。因此“通道不冗余但无效”改为“成员非重复，增量收益尚未证实”。

composite 的 +0.14pp 是相对逐日市场中位数的超额，不是策略毛收益；原统计中的 family 原始均值约 +0.02pp、胜率约 39%。30/39 个交易日参与校准，只有 9 日严格样本外，故 composite 是有希望的确定性基准排序器，不是已证明可变现的独立策略。

期权信息不进入全 A 个股召回。国内可得期权主要映射指数/ETF，其信息粒度适合市场或风格地形；现有 QVIX、PCR、到期结构尚无稳定的 1–2 日个股增量证据。本期只保留研究接口，不把缺失的个股期权覆盖伪造成股票级召回通道。

### 2.2 L2、pass1 与最终列表

`l2_rank` 是选入顺序，不是统一打分名次。旧稿所谓“91 只 floor saved”混合了 lane、backfill 和 pinned，必须分原因报告。现有行业映射约为 7 个超级行业，不是完整申万一级；研究结果不能以“SW1 分散”命名。

现有 pass1 的各通道队列内部已经按 `gbdt_score`（即 composite）排序，因此它并非与 composite 无关；但通道轮转会显著改变成员，不能据此断言“几乎不敏感”。pass1 kept/cut 必须在相同日期交集上配对，不能拿 24 日和 11 日两组均值直接比较。

最终建议“3–5 只”必须定义为去重后的新研究列表总数，包含 composite seats；pinned 持仓另列。当前普通 L3 finalist 与 composite BUY pool 是不同人口，不能只改普通 finalist 上限便宣称最终只有 3–5 只。本期先输出总人口诊断，不改变 E6 所有权。

### 2.3 knife、门与 shell

现有 menu 统计的 knife 指标为 `pct_60d < -20%`，拟议硬门为 `pct_60d < -35% 且 fund <= 0`，二者不能直接互推效果。硬门继续保留，后续只能用同日反事实评估。

GATE1/GATE2 是产物契约和失败隔离，不是可随意删除的模型步骤。合法 0 BUY 与产物缺失、格式错误、超预算必须保持不同状态。可以删除的是重复调度壳，而非验证本身。

## 3. 漏斗变体实验契约

### 3.1 共同人口与变体

每个分析日只使用该日冻结的 `L1_scored_full.csv`、现有 L1/L2/pass1 产物与相同 forward-return 湖数据。所有变体使用相同 L1、L2、pass1 预算、相同可交易定义和相同主尺 `gap_c1_o2`。

| 变体 | L1 | L2/pass1 语义 |
|---|---|---|
| current | 直接读取冻结产物，作为事实基线 | 直接读取冻结 L2/pass1，保留真实原因 |
| composite_only | 按 composite 排序取相同预算 | 不虚构通道 provenance；style floor 有效值自然为 0 |
| composite_plus_diversifiers | 80% composite core；余下最多 20% 从当前 L1 的 core 外成员按通道共振、composite 排序补入 | 复用真实 provenance；预算不变 |

`composite_only` 不是把现有 floor 数值照搬，因为 floor style 来自 recall provenance；不存在 provenance 时，强行应用会制造伪语义。混合变体的 80/20 是预注册的研究起点，不是生产最优参数。

### 3.2 输出

实验目录仅写当前引擎的 `reports_${AUTORESEARCH_ENGINE}/research/funnel_variants/`：

- `membership.csv`：date、code、variant、in_l1、in_l2、pass1_kept、selection_reason；
- `daily_metrics.csv`：逐日逐变体各阶段的 n_selected、n_scored、raw_mean、excess_vs_market_median、win_rate、entry/exit flags；
- `paired_summary.json`：current 与 challenger 的共同日期配对差、bootstrap CI、样本数和缺失原因；
- `manifest.json`：输入路径与摘要、预算、主尺、变体版本、代码版本和生成时间。

缺失收益保持 null，不填 0；逐日先聚合，再做 day-equal 配对。未成熟日、空选择、缺行情、无法重建是不同状态。报告同时给成员重叠、独有成员和选择原因，避免只看收益后反推叙事。

### 3.3 生产晋级门槛

默认使用 paired day-equal 的 challenger-current `excess_vs_market_median` 差值作为主比较量，raw return、win rate 和覆盖率为辅。至少 20 个共同成熟交易日，且必须保留至少 10 个滚动向前的严格样本外日；主指标 bootstrap 90% CI 下界大于 0，且 raw return、entry/exit 可交易覆盖不恶化超过预注册容忍值，才允许提生产切换。

样本不足、CI 跨 0 或数据完整性不合格的结论都是“尚无证据切换”，不是“证明相等”。任何 production 改动另开设计与回滚计划。

## 4. 双引擎角色配置契约

### 4.1 三层事实

1. declared：项目配置期望某角色用什么 tier、model、effort 及工具策略；
2. runtime：当前 harness 报告的可用模型、effort 和能力；
3. actual：trace/usage 中实际执行的 model、effort、角色与 invocation。

三层分别落盘。runtime 不支持 declared 时给出 mismatch 并按明确 fallback 解析，不能静默冒充已按配置执行；无 actual 证据时状态为 UNKNOWN，不从文件配置推断执行成功。

### 4.2 配置形状

`agents` 只保存角色到 tier 的映射及必要的角色级覆盖；`agent_engines` 保存 Claude/Codex 的 tier 定义与 fallback。解析结果按 engine 规范化：Claude 使用 `effort`，Codex 使用 `reasoning_effort`；共同审计层将二者映射为统一的 actual effort 字段。

迁移期读取旧 Claude-only `agents` 形状一个版本，但配置文件只保留新形状，避免两套活跃事实源。Codex effort 不写死在旧的 `{low..xhigh}` 集合；语法层接受当前支持的低到 ultra 词表，能力层按 runtime model capability 校验。

物化文件 schema 升级并包含 engine、resolved roles、fallback/mismatch 和配置摘要。Claude workflow 继续得到原有等价 role map；Codex 主会话和子代理配置从相同解析结果读取。

## 5. GATE1 调度设计

L2 完成后的顺序保持：

1. `l2-check` 只负责判断 L2 是否存在，以便决定是否重跑 deterministic prelude；
2. 重试结束后，一次 `gate1_decide` 完成原 GATE1 契约校验、预算计算、run-mode 判断与 stage result 记录；
3. gate 失败仍立即停止，不能因 run-mode 有默认值而放行。

这把原来的 GATE1 shell 与 run-mode shell 合成一个确定性调用，避免重复读 L2，同时保留可恢复的 prelude retry。GATE2 完全保留。L4 的 preflight 决定分支，而 prepare 当前可与 intel 并行，两者时序不同，故本轮不合并。

## 6. 验收与非目标

验收必须覆盖：合成日的同预算成员重建、无 provenance 时 floor 归零、共同日期配对、不成熟收益不填零；新旧配置兼容、Claude 等价解析、Codex max/ultra 能力验证与 mismatch；GATE1 成功/失败/forced-full/重试后的单次 run-mode 判断；全量测试通过。

本轮不跑真实全市场扫描、不改冻结 run、不回填另一引擎账本、不取期权行情、不自动学习权重、不改变 production 参数、不删除任何 gate。
