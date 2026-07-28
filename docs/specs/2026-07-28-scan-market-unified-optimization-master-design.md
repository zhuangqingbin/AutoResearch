# scan-market 统一优化总纲：决策质量 × BUY 发现 × Token × 速度 × 架构

> **状态**：设计已确认；只落开发文档，本轮不实施\
> **基线日期**：2026-07-28\
> **代码基线**：`16463c0`\
> **适用范围**：`scan-market` 主链 + 共享数据、研究闭环与编排层\
> **取代关系**：本文取代 `docs/specs/2026-07-28-wave7-unified-roadmap-design.md` 的当前调度地位；Wave7 及更早设计稿仅保留为证据与机制沿革\
> **用户裁定**：
> 1. 五项目标平权：研究质量、BUY 发现能力、token 效率、运行速度、架构健康；
> 2. 以 `scan-market` 为主，`stock-research` / `macro-research` 只纳入共性能力；
> 3. 允许长期 0 BUY，不设 BUY 数量或频率指标；
> 4. 采用“度量控制面 + 渐进式收敛”，不做大爆炸重写。

---

## 0. 一页结论

### 0.1 项目是否还有优化空间

有，而且下一阶段的主要矛盾已经从“缺功能”转为“缺统一裁决”：

- **研究方向**：L1 各召回通道显著分化，L3 没有稳定选股 alpha，L4 的总体价值在拒绝侧，但单门中“主力真在”存在局部错杀嫌疑；频繁权重重标定已无增益。
- **BUY 发现**：系统不是从不出 BUY，而是多数日期主动弃权；大部分 0 BUY 可能正确，但当前无法稳定拆分“正确空仓、上游漏检、早停错杀、单门错杀、数据不可判”。
- **Token**：真实成本集中在 Opus L4、sector brief 和 L3，不是原始 token 最大的 Haiku general-purpose 壳；当前 cache 命中 89.7%，应保护而非破坏。
- **速度**：89m38s 中 L3、L4、ensemble 合计约 52 分钟，是关键路径；L0–L2 仅 2m16s，不值得优先微优化。
- **架构**：功能已丰富，但终评级、账本、报告和 Workflow 之间仍靠隐式文件与 Markdown 解析通信；三个生产核心文件均超过 1,200 行，lazy import 防环广泛存在。

### 0.2 为什么最近报告总没有 BUY

不是单一原因：

1. **市场环境确实差**：已成熟的 17 个 0-BUY 日，市场 T+2 平均 `-1.48%`；最新扫描是 risk-off，宽度约 15%，60 日中位约 `-17.7%`。
2. **拒绝机制总体有效**：真实组合 `-0.24%`，无门影子组合 `-3.69%`，市场约 `-11%`。
3. **上游菜单仍有失明**：L1 通道 21 日读数中 value 为 `+1.6%`，heat/healthy 为 `-1.6%`，momentum 为 `-1.0%`；历史复盘也曾显示 0 买连败的漏检主要发生在 L1。
4. **最新一日主要卡在资金确认**：10 张卡中 4 张早停；10 张可解析卡的三门失败分布为“主力真在 7、业绩真兑现 3、估值不透支 3”。
5. **三门不能整体调松**：多门失败候选 T+2 超额 `-1.32%`，总体拦对；但“主力真在”唯一绑定样本 T+2 超额 `+1.27%`，应查口径和窗口，而不是把三门一锅端。
6. **BUY 本身也未证明强**：Overweight 已实现仅 4 笔，T+2 均值 `-0.32%`。增加 BUY 数量不会自动提升系统质量。

### 0.3 推荐路线

先建立一层很薄的“度量控制面”，让每个生产变更都回答五个问题：

1. 研究结果是否更准；
2. 是否减少错误买入或错误空仓；
3. 模型价差折算成本是否下降；
4. 关键路径是否缩短；
5. 架构边界是否更清楚、失败是否更响亮。

只有“一项明确改善、其余四项不越过退化阈值”的改动才能进入生产。

---

## 1. 证据基线

### 1.1 扫描与 BUY

数据源：

- `reports/learning/journal.md`
- `reports/learning/zero_buy_ledger.md`
- `reports/learning/buy_ledger.md`
- `reports/learning/paper_nav.md`
- `reports/scan/20260727_2140/summary.md`

| 指标 | 当前读数 | 含义 |
|---|---:|---|
| 扫描日 | 26 | 含早期和非完整日 |
| 0 BUY 日 | 19 | 不是“每次都没有” |
| 已成熟 0-BUY 日 | 17 | 可用于市场方向对照 |
| 0-BUY 日市场 T+2 | -1.48% | 多数空仓方向正确 |
| 真实组合 | -0.24% | 9 笔；样本很薄 |
| 无门影子组合 | -3.69% | 72 笔 |
| 市场 | 约 -11% | 同期弱市 |
| Overweight 已实现 | 4 | 未达到稳定基率门槛 |
| Overweight T+2 均值 | -0.32% | BUY 质量仍需提升 |

早期 `2026-06-19` 是非交易日键，历史买单不能直接与正常交易日等价。本文的当前判断以可归因交易日和终评级为准。

### 1.2 最新一日漏斗

基线 run：`reports/scan/20260727_2140`

```text
L0 4039 → L1 1000 → L2 203 → pass1 40
→ L3 真 finalist 7 + pinned 3 → L4 10 卡 → 0 BUY
```

评级与停止机制：

- Hold 6；
- Underweight 4；
- 早停 4：基本面恶化 2、题材透支 1、其他 1；
- 满卡未达 OW 6；
- 主力门失败 7；
- 业绩门失败 3；
- 估值门失败 3。

### 1.3 召回与校准

数据源：

- `reports/learning/channel_ledger.md`
- `reports/learning/changelog_ledger.md`
- `reports/learning/cross_calib.md`

| 通道 | 21 日边际 T+2 | 当前判断 |
|---|---:|---|
| value | +1.6% | 值得扩大影子验证 |
| reversal | +0.5% | 暂保留 |
| main_fund | +0.2% | 弱正 |
| composite | +0.1% | 近似中性 |
| growth | -0.3% | 需降权影子 |
| momentum | -1.0% | 与历史 T+5 结论发生口径变化，必须按 T+2 重审 |
| heat | -1.6% | 明显弱 |
| healthy | -1.6% | 当前定义未证明有效 |

权重重标定：

- 10 次样本足的重标定，平均 `ΔIC=-0.0011`；
- 已触发 C18 红灯；
- 结论：停止“看到一次结果就继续调权”，改为读数触发、影子验证、人工批准。

### 1.4 门与早停

唯一绑定门：

| 门 | 已实现样本 | 被拦 T+2 超额 | 当前判断 |
|---|---:|---:|---|
| 主力真在 | 12 | +1.27% | 疑似局部错杀；先查定义和窗口 |
| 业绩真兑现 | 4 | +0.35% | 样本不足 |
| 估值不透支 | 3 | -3.23% | 样本不足但方向有效 |
| 多门失败 | 87 | -1.32% | 总体拒绝有效 |

早停账本当前只有 4 张、成熟 0 张，任何改动都必须等待前向样本或使用不改变生产评级的影子深核。

### 1.5 Token 与速度

数据源：

- `reports/scan/20260727_2140/token_usage.md`
- `context/scan/2026-07-27/_stage_timing.json`

Token：

- 45 个 subagent；
- 原始输入 19.52M；
- cache-aware 加权输入 4.26M；
- 输出 613.7k；
- cache 命中 89.7%；
- 当前统计不覆盖主会话。

按模型价差折算后的优化顺序：

1. L4 card；
2. sector brief；
3. L3；
4. intel；
5. macro；
6. general-purpose Haiku 壳。

墙钟：

| 阶段 | 时间 |
|---|---:|
| L0–L2 | 2m16s |
| 策略师 | 4m08s |
| 行业 brief | 9m44s |
| L3 | 14m50s |
| L4 | 22m35s |
| ensemble | 14m20s |
| assemble | 1m35s |
| 合计 | 89m38s |

### 1.6 架构体量

| 文件 | 行数 | 主要风险 |
|---|---:|---|
| `autoresearch/scan/assemble.py` | 1,352 | 终评级、报告、发布、副作用混在一起 |
| `autoresearch/scan/agents/l4_card.py` | 1,264 | 上下文、rubric、生产者、dispatch、CLI 混合 |
| `autoresearch/scan/agents/l3_select.py` | 1,220 | 证据、分诊、prompt、lint、merge 混合 |
| `autoresearch/learning/retro.py` | 1,084 | 归因、刷新、重标定、编排耦合 |
| `.claude/workflows/scan-market.js` | 约 19KB | 调度与状态解释较重 |
| `scan-market/SKILL.md + STAGES.md` | 约 63KB | 当前态、沿革、runbook 交叠 |

现 `autoresearch/scan/artifacts.py` 只统一了 `finalists.csv` 的代码列读取；它证明了“契约收口”方向有效，但覆盖面仍太小。

---

## 2. 目标函数、红线与非目标

### 2.1 五项平权守卫

不把五项压成一个加权总分，防止通过优化一项掩盖另一项退化。

| 守卫 | 核心问题 | 主指标 |
|---|---|---|
| 研究有效性 | 找到的证据和判断是否有前向价值 | T+2 超额、rank-IC、左尾、跨 regime |
| 决策校准 | 是否少错买、少错空 | false buy、false abstention、correct abstention |
| Token | 每个成熟决策烧多少成本 | 模型价差折算成本/成熟决策 |
| 速度 | 用户多久拿到可用结果 | P50/P90 关键路径墙钟 |
| 架构 | 是否可验证、可回滚、少漂移 | 契约覆盖、单一事实源、故障注入 |

晋升规则：

- 至少一项有明确改善；
- 其余四项不得越过退化阈值；
- 无足够样本时只允许影子运行；
- 生产行为变更必须人工批准；
- 不允许以“BUY 更多”作为单独的成功证据。

### 2.2 继续有效的不变量

- 持仓尺度是超短 1–2 日，主尺 `fwd_2_oc`；
- 0 BUY 不等于门过严；
- L3/L4 只能读描述性市场地形，不能读 L5 方向指令；
- 个股评级只由本股 rubric 决定；
- L0/L1/L2/L5 保持确定性；
- A 级数据空帧或契约损坏必须阻断；
- B 级增强数据可以降级，但必须记账；
- lake 命中优先，窄字段不得毒化共享缓存；
- 中间产物可重跑、可追溯；
- 仅供研究，不是自动交易系统。

### 2.3 明确不做

- 不设每日或每周 BUY 配额；
- 不要求提高“有买单日比例”；
- 不整体放松三门；
- 不为提高 BUY 数量关闭早停；
- 不恢复 L2 模型；
- 不做大爆炸架构重写；
- 不把港美股全市场扩展纳入本轮；
- 不增加 7×24 全市场快讯层；
- 不用单次最快 run 或单次收益作晋升证据；
- 不继续无门槛地频繁重标定权重。

### 2.4 已比较但未选择的路线

#### 继续按 Wave 逐点调优

优点是见效快，可以直接调 quota、prompt、并发和缓存。未选择它作为主路线，是因为当前已经存在多本账、多个解析口径和跨层时序问题；继续局部调优会增加“指标变好但事实源不一致”的风险。

逐点修复仍然保留，但必须挂在控制面下，作为独立实验而不是新的总架构。

#### 架构优先大重写

优点是长期边界整洁。未选择它，是因为：

- 迁移面覆盖 Python、Workflow、skills、staging 和 learning；
- 无法快速回答 0 BUY 的正确与错误；
- 重构期间容易混入研究行为变化；
- 当前生产链已有大量有效能力，不需要推倒重来。

架构治理因此放在事实契约之后，采用兼容导出和 golden parity 渐进拆分。

#### 度量控制面 + 渐进式收敛

这是本文选择的路线。它先解决“什么是真的、怎么比较”，再分别优化研究、token、速度和模块边界，最符合五项目标平权与无 BUY 配额的用户裁定。

---

## 3. 总体设计：生产面 + 度量控制面

### 3.1 总图

```text
生产面
L0 → L1 → L2 → L3 → L4 → L5
 │    │    │    │    │    │
 └────┴────┴────┴────┴────┘
                  ↓
              统一事件/产物
                  ↓
┌─────────────────────────────────────────┐
│ 度量控制面                              │
│ 1. RunContract / ArtifactIndex          │
│ 2. DecisionRecord / StageAttribution    │
│ 3. ExperimentRegistry                   │
│ 4. CostTimingLedger                     │
│ 5. PromotionDecision                    │
└─────────────────────────────────────────┘
                  ↓
       保留 / 继续影子 / 回滚 / 人批上线
```

控制面不直接改门、评级、权重或名单。它只负责：

- 固化本次跑了什么；
- 收集阶段事实；
- 计算前向结果；
- 比较基线与实验；
- 形成带证据的晋升建议。

### 3.2 标准变更生命周期

```text
假设
→ 明确消费点与失败模式
→ 历史 PIT/生产回放
→ 影子运行
→ 前向成熟
→ 五项守卫
→ 人工裁决
→ 小流量上线
→ 自动回滚观察窗
→ 正式基线
```

任何跳过中间步骤的改动只能属于：

- 安全修复；
- 契约一致性修复；
- 纯观测；
- 已被真实事故证明、且不改变研究行为的修缮。

---

## 4. 0-BUY 根因模型

### 4.1 0-BUY 不是一个标签

每个 0-BUY 日必须拆成以下因果树：

```text
0 BUY
├─ A. 正确弃权
│  ├─ 市场不给钱
│  ├─ 影子候选也不给钱
│  └─ 被拒候选有明显左尾
├─ B. 上游失明
│  ├─ L0 硬门漏掉
│  ├─ L1 未召回
│  ├─ L2/pass1 截断
│  └─ L3 bench 隐藏机会
├─ C. 判断层错杀
│  ├─ L4 早停错杀
│  ├─ 单一硬门错杀
│  └─ ensemble 只向下折回错杀
├─ D. 数据/契约假阴性
│  ├─ 缺数被当作 FAIL
│  ├─ 资金窗口错配
│  ├─ 卡面评级与终评级分叉
│  ├─ 复用绕过新判断
│  └─ 账本时序或解析错误
└─ E. 未成熟
   └─ 不能下结论
```

### 4.2 日级裁决

#### 正确弃权

同时满足：

- 当日终评级无 Buy/Overweight；
- 可交易的被拒候选/影子 top3 在 T+2 没有形成经济有效的超额机会；
- 没有因数据不可判、产物损坏或阶段失败导致的假阴性。

#### 错误弃权

至少一只被拒候选满足：

- 次日开盘可交易；
- `fwd_2_oc - 市场中位 ≥ +2pp`；
- 不属于一字板、停牌或无法成交；
- 能明确追溯到第一次被拒层。

`+2pp` 是机会错失线，不是买入收益承诺。它与 pinned ledger 的卖飞线保持一致，避免多套阈值。

#### 中性

- 超额处于 `(-2pp,+2pp)`；
- 或前向数据不足；
- 或可交易性不确定。

### 4.3 票级首次死亡点

每只候选只记一个 `first_rejection_stage`：

```text
L0_EXCLUDED
L1_NOT_RECALLED
L2_NOT_SELECTED
PASS1_CUT
L3_BENCH
L4_EARLY_STOP
L4_GATE_MAIN
L4_GATE_EARNINGS
L4_GATE_VALUATION
L4_MULTI_GATE
ENSEMBLE_FOLDED
BOUGHT
```

多门失败单列 `L4_MULTI_GATE`，不得同时给三个单门记功或记过。

### 4.4 报告的 0-BUY 段

以后 L5 必须输出：

1. 当日即时机制：早停、满卡、唯一绑定门、多门失败、不可判；
2. 最近成熟样本的历史基率；
3. 当日尚未成熟声明；
4. 是否存在数据或契约降级；
5. “正确弃权 / 疑似漏检 / 未成熟”的最终状态。

当日不允许直接写“非漏斗故障”，除非：

- GATE1–4 全部完成；
- 关键产物齐全；
- 配置 hash 一致；
- 没有 `UNKNOWN` 被伪装成门失败。

---

## 5. 研究方向优化

### 5.1 各层目标重新定义

| 层 | 旧的隐含期待 | 新的明确职责 |
|---|---|---|
| L0 | 筛出好票 | 只处理可交易性和确定性硬风险 |
| L1 | 排名越准越好 | 高召回机会集；衡量唯一贡献与漏检 |
| L2 | 粗排预测 | 多样性菜单与预算控制，不预测 |
| L3 | 选出会涨的票 | 把有限 L4 预算分给最值得验证的票 |
| L4 | 深研并出评级 | 拒绝、定级、列明可证伪条件 |
| L5 | 汇总报告 | 决策事实、弃权解释、组合约束 |

### 5.2 L1：按机会召回治理

每条通道同时报告：

- membership 超额；
- unique 超额；
- unique 命中率；
- 首次死亡点；
- 对最终 BUY/错误弃权的增量贡献；
- 按 regime 的稳定性；
- 换手和与其他通道重叠。

建议的下一批影子实验：

1. value quota 上调；
2. heat quota 下调；
3. healthy 定义重审或下调；
4. momentum 以 T+2 为主重新裁决，历史 T+5 结论只作参考；
5. composite 保持中性地基，不因短期弱而关闭；
6. 每个变更单变量运行，不把多条 quota 一次改掉。

生产晋升门：

- PIT 回放无前视；
- ≥20 个前向扫描日；
- unique 成熟样本 ≥30；
- 至少两个 regime 不出现方向反转；
- false abstention 下降；
- L4 token 不因召回扩大而无上限增加。

### 5.3 L3：从“选股”改为“研究预算配置”

L3 的直接收益 edge 未被证明，不再用“finalist 自身涨没涨”作为唯一评价。

L3 评价指标：

- 最终 BUY 机会覆盖率；
- 错误弃权候选覆盖率；
- L4 研究预算利用率；
- bench 中隐藏机会率；
- 进入 L4 后被表面早停的比例；
- 同行业重复研究率。

L3 逻辑篮子：

#### 主研究篮

- 证据完整；
- T+2 兑现机制明确；
- 资金、基本面与估值至少两项形成可验证共振；
- 用于生产决策。

#### 审计篮

- 上游信号强但存在结构性不确定性；
- 用来检测 L1/L2/L3 的盲区；
- 数量从现有 finalist cap 内切出，不增加总卡数；
- 默认只形成影子结果，不自动产生 BUY。

初始分配：

- 主研究篮 80%；
- 审计篮 20%；
- 连续 20 个前向扫描日后，按错误弃权捕获率调整；
- 审计篮长期无增量则回滚为 100% 主研究篮。

### 5.4 L4：门级问责与三态

门状态从概念上的布尔值升级为：

```text
PASS
FAIL
UNKNOWN
```

规则：

- `UNKNOWN` 仍不允许给 ≥Overweight；
- 但必须写“数据不可判”，不能计入该门的拦对/错杀；
- 缺字段、源降级、窗口冲突、证据过期均可导致 `UNKNOWN`；
- 只有明确反证才记 `FAIL`。

#### 主力真在门

优先研究，不直接调松：

- 对齐主尺窗口；
- 区分当日大单、20 日 CMF、OBV 趋势；
- 记录绝对净额、相对成交额、相对市场资金变化；
- 反转票与趋势票可使用不同的确认解释，但不能使用不同的结果门槛而不记账；
- 只用唯一绑定样本裁决。

建议影子定义：

1. 现生产定义；
2. `main_net>0 ∧ OBV>0`，CMF 作为滞后确认；
3. 三者共振；
4. 相对行业/市场资金改善。

四种定义同时影子记账，不改变生产评级。达到样本门后择一提案。

#### 业绩与估值门

- 样本不足，不提前修改；
- 继续积累唯一绑定样本；
- 同时记录目标触达和左尾；
- 不以“后来上涨”单独证明门错，因为估值门首先承担尾部保护职责。

### 5.5 早停

生产早停保持不动。

建立每周影子深核：

- 从早停卡中确定性抽样 10%–20%；
- 不改变当日评级；
- 只补跑 deep 与 rubric；
- 比较早停评级、影子满卡评级与 T+2；
- 每个停因成熟样本 ≥10 后才提改动；
- 影子成本单列，不混入生产成本基线。

### 5.6 ensemble

必须记录：

- 原判；
- run2；
- run3；
- 是否同档早止；
- spread；
- 终评；
- 折回前后哪个更接近 T+2 结果；
- 额外 token 和尾部墙钟。

裁决门：

- SELL/UW 折回成熟 n≥10；
- BUY/OW 折回成熟 n≥10；
- 若同档早止覆盖率高且结果不退化，保留；
- 若折回持续制造错误弃权，再分别调整买单和卖单复核，不一起关闭。

### 5.7 权重重标定

立即把研究原则改为：

- retro 默认只刷新读数；
- 不顺带重标定；
- regime 变化、固定研究窗口到期或人工请求时才生成 challenger；
- challenger 先走 PIT 与影子；
- trial 数进入多重检验披露；
- 未显著优于 prior 不晋升。

### 5.8 通用研究晋升门

涉及召回、L3、门、早停、ensemble 或评级的行为变更，统一执行：

1. 能进行历史 PIT 回放时，先覆盖 250–500 个交易日；不能可靠重建 PIT 输入时，不用伪回测替代，直接进入预注册影子阶段；
2. 前向观察不少于 20 个真实扫描日；
3. 关键细分至少有 10 个成熟事件；召回通道的 unique 样本至少 30；
4. 至少覆盖两个 regime；只有单一 regime 时继续影子，不晋升；
5. 同时报告均值、胜率、左尾、目标触达、错误买入和错误弃权；
6. 报告 trial 数和定义变更断点；
7. 未过全部门只允许影子运行；
8. 最终生产切换必须人工批准并保留一键回滚。

---

## 6. Token 优化设计

### 6.1 正确成本口径

每次扫描记录：

- input；
- cache read；
- 5m/1h cache write；
- uncached input；
- output；
- model；
- effort；
- 相对 Opus 成本；
- 按当前官方价计算的估算成本；
- 主会话与 subagent 分列；
- 失败、重试、废弃调用。

主要 KPI：

```text
成本 / 成熟 DecisionRecord
成本 / 最终 BUY 候选
成本 / 被验证为正确的拒绝
```

不能只看：

- 原始 token；
- 落盘字节；
- agent 数；
- cache-aware 加权输入但忽略模型价差。

### 6.2 优化顺序

#### 第一刀：消灭无效调用

- 空输入；
- 重复 macro；
- lint 假阳修复；
- 连接中断后的全量重跑；
- 已有产物却未复用；
- 单票失败引发批次重跑。

#### 第二刀：L4 重复上下文

- 固定 rubric、格式契约和共享说明保持 byte-stable；
- 市场地形、行业地形、dossier 各生成一次带 hash 的共享块；
- 每票只追加差异化证据；
- dossier 只注入变化、相关风险和可证伪项；
- slim/deep 继续物理分离；
- 早停票不得读取 deep。

#### 第三刀：L4 输出

结构化最小核心：

- 六维；
- 三门；
- 证据引用；
- 关键反证；
- 目标/止损；
- `FINAL TRANSACTION PROPOSAL`；
- 诚实局限。

删除的是重复散文，不是证据链。

#### 第四刀：sector brief A/B

当前 75 次调用、成熟 0，不能证明价值。

对照：

- A：现状，L3 前生成完整 brief；
- B：L3 只读确定性行业地形，LLM brief 仅为 finalist 行业生成。

B 只有在：

- finalist 机会覆盖率不降；
- 行业错判不增；
- L4 结论质量不降；
- token 和墙钟下降；

时才晋升。

#### 第五刀：L3 局部修复

lint 修复调用只带：

- 出错行；
- 原判断；
- 合法证据；
- 修复 schema。

不得重新传整个 40 股上下文。

#### 第六刀：Intel

- 搜索覆盖面保持宽；
- T0/24h 预算优先；
- URL、事件、转载链去重；
- 输出最多保留影响门或 T+2 的证据；
- >1 周已兑现事件默认不进入净分；
- 未兑现催化保留。

### 6.3 Cache 红线

当前 89.7% 命中率是有效资产。

- 稳定前缀不得随日期或股票变化；
- 变化内容全部后置；
- prompt 缩短实验必须同时报告 cache write/read 变化；
- 若成本下降来自输入变短、但 cache 命中大幅下降，按真实折算成本裁决；
- cache key 与 schema version 绑定，避免旧契约误复用。

### 6.4 Token 目标

基线：`20260727_2140`

阶段一：

- 模型价差折算成本下降 ≥15%；
- cache 命中不低于 85%；
- false abstention 与 false buy 不恶化。

阶段二：

- 累计成本下降 ≥25%；
- 主会话进入计量；
- 失败/废弃 token 可单独归因；
- 每种 agent 有单位产物成本。

验收使用至少 10 次真实扫描的中位数，不使用单次最佳值。

---

## 7. 速度优化设计

### 7.1 关键路径

```text
当前关键路径
L3 14m50s
→ L4 22m35s
→ ensemble 14m20s
= 约 52 分钟
```

L0–L2 仅 2m16s，保留现状。

### 7.2 流式 DAG

```text
L2 完成
├─ market context
├─ sector terrain / brief
└─ L3 evidence
        ↓
L3 形成候选
        ↓
每票：intel ∥ slim/deep 准备
        ↓
单票证据齐备即启动 card
        ↓
触发者进入 adaptive ensemble
        ↓
全部终评完成
        ↓
assemble + post-run consumers
```

与现状相比：

- Intel 前置到 GATE2 后；
- slim、intel、确定性生产者并行；
- 单票证据就绪即开卡；
- 不等整个 batch；
- card 与其他票的 intel 可以重叠；
- assemble 只等待终评级，不等待非关键展示件。

### 7.3 并发策略

- 以外部服务失败率和限频决定并发上限；
- 不使用“越多越快”的固定逻辑；
- 对 tushare、WebSearch、WebFetch 分别设独立并发帽；
- 连续限频时自动降并发；
- 单票失败不拖累其他票；
- pinned 与普通 finalist 可并行，但 pinned 永不复用。

### 7.4 ensemble 尾部

继续：

```text
run1
→ run2
→ 同档则停止
→ 分歧才 run3
```

不直接把 run2/run3 全并行，因为它会稳定增加 token。

若未来 wall 优先级需要提高，只能在 ensemble ledger 证明：

- 分歧率高；
- run3 经常必跑；
- 并行增加的成本在预算内；

之后再提案。

### 7.5 预热

- 夜间预热不计入交互墙钟；
- 报告同时列“预热耗时”和“交互耗时”；
- 预热失败必须在 GATE1 前暴露；
- 已预热数据应通过 hash 证明被本次 run 消费；
- 预热成功但扫描仍重拉同端点，计为缓存失效。

### 7.6 速度目标

阶段一：

- P50 ≤75 分钟；
- P90 ≤100 分钟；
- 单次网络事故不触发全流程重跑。

阶段二：

- P50 ≤65 分钟；
- P90 ≤90 分钟；
- L4 单票可以独立完成、独立失败、独立重跑。

研究守卫优先于速度目标；未过研究守卫的提速不得上线。

---

## 8. 渐进式架构设计

### 8.1 目标依赖方向

```text
autoresearch.data / common
          ↓
scan.domain
          ↓
scan.stages
          ↓
scan.reporting
          ↓
learning consumers

workflow/orchestrator 只调度，不拥有业务规则
```

禁止：

- domain 反向导入 reporting；
- learning 从 Markdown 猜终评级；
- Workflow 复制 Python 的门或评级规则；
- 报告渲染改变终评级；
- 同一事实有多个互相独立的解析器。

### 8.2 RunContract

开跑前生成：

```text
RunContract
- analysis_date
- run_id
- git_sha
- user_config
- config_hash
- agent model/effort
- pinned list
- data policy
- stage budgets
- artifact schema versions
```

规则：

- Python 读取与白名单校验；
- Workflow 参数只传同一对象；
- 每个 stage 回显 contract hash；
- hash 不一致、配置意外为空或 pinned 不一致时 GATE1 失败；
- 报告 manifest 固化最终 contract。

### 8.3 ArtifactIndex

不恢复通用 typed pipeline，只覆盖关键生产产物：

```text
ArtifactRef
- name
- schema_version
- producer
- path
- input_hash
- content_hash
- status
- created_at
```

第一批：

- market_pack；
- L1/L2；
- L3_judged；
- finalists；
- L4 card；
- ensemble；
- final_ratings；
- gate_fires；
- early_stop；
- summary；
- manifest。

CSV/JSON/Markdown 保留；契约层统一路径、schema、代码列、存在性和版本。

### 8.4 StageResult

确定性 CLI 与 Workflow 交界统一为：

```json
{
  "status": "SUCCEEDED",
  "artifacts": [],
  "metrics": {},
  "warnings": [],
  "error": null
}
```

合法状态：

```text
PENDING
RUNNING
SUCCEEDED
DEGRADED
FAILED
SKIPPED
```

Workflow 只：

- 排依赖；
- 控并发；
- 做有限重试；
- 读结构化状态；
- 写 journal。

### 8.5 DecisionRecord

终评级成为领域事实：

```text
DecisionRecord
- code
- source_rating
- rubric_rating
- gate_states
- early_stop
- ensemble_ratings
- final_rating
- proposal
- reason
- evidence_refs
- first_rejection_stage
```

消费者：

- summary；
- journal；
- buy/zero-buy ledger；
- retro；
- pinned ledger；
- paper NAV；
- dossier delta；
- health。

Markdown 只是视图。

### 8.6 Post-run outbox

`assemble` 完成终评级后写本地事件：

```text
RUN_FINALIZED
DECISION_FINALIZED
GATE_FAILED
EARLY_STOPPED
DOSSIER_DELTA_READY
```

约束：

- JSONL/JSON 本地文件即可，不建消息队列；
- consumer 幂等；
- 每条事件有稳定 id；
- 失败 consumer 可单独补跑；
- 报告发布不被非关键 learning consumer 阻断；
- health 必须显示 consumer 欠账。

### 8.7 核心文件拆分

#### `assemble.py`

拆为：

- `decision_finalize.py`
- `decision_read_model.py`
- `report_sections.py`
- `publisher.py`
- `post_run.py`

#### `l4_card.py`

拆为：

- `l4/context.py`
- `l4/rubric.py`
- `l4/producers.py`
- `l4/prompts.py`
- `l4/dispatch.py`
- `l4/parsers.py`

#### `l3_select.py`

拆为：

- `l3/evidence.py`
- `l3/triage.py`
- `l3/prompt.py`
- `l3/validation.py`
- `l3/merge.py`

迁移策略：

- 先抽纯函数；
- 保留旧 import 兼容导出；
- 每次只移动一个边界；
- golden parity；
- 行为不变；
- 所有消费者切完后再删兼容层。

### 8.8 运行目录

第一阶段：

- 保持 `context/scan/<date>`；
- 增加 run manifest 与 artifact index；
- 同日重跑记录 run_id 和覆盖关系。

第二阶段：

- 对拍稳定后再引入按 run_id 隔离的 workspace；
- 报告目录与 staging 通过 manifest 关联；
- 不直接一次性修改所有 Workflow 的硬编码路径。

### 8.9 文档职责

| 文档 | 唯一职责 |
|---|---|
| `CLAUDE.md` | 项目入口和不变量 |
| `scan-market/SKILL.md` | 触发、编排、命令、人工检查点 |
| `STAGES.md` | 当前生产机制与参数 |
| 本文 | 方向、优先级、证据、波次、裁决门 |
| 历史 specs | 沿革与旧实验 |

能从代码/config 生成的参数表不再复制到多个文档。

---

## 9. 错误处理设计

### 9.1 错误分类

| 类型 | 行为 |
|---|---|
| A 级数据错误 | 整链停止，拒绝入湖 |
| B 级增强数据错误 | `DEGRADED`，显式记账 |
| 配置/hash 不一致 | GATE1 失败 |
| 单票 slim/card 失败 | 只废该票，可独立重跑 |
| Intel 失败 | 卡内受控回退并标记 |
| L3 失败 | 不生成伪 finalists |
| 终评级不可解析 | 失败，不得回退为 Hold |
| 报告节渲染失败 | 终评级保留，报告标缺节 |
| learning consumer 失败 | 发布继续，欠账响亮 |
| 重试耗尽 | 保留原错误与每次调用成本 |

### 9.2 重试

- 只重试瞬时网络错误；
- 默认 1 次轻重试；
- 已完成产物存在且 hash 正确时复用；
- 数据契约错误不重试绕过；
- schema 错误不通过换模型或重跑掩盖；
- 每次重试进入 token/耗时浪费账。

### 9.3 复用

- Buy/Overweight 永不复用；
- pinned 永不复用；
- 复用看相对市场位移，不看绝对涨跌；
- schema、contract、regime 或重大公告变化时不复用；
- 复用必须记录来源 run、旧评级、旧证据 hash；
- 复用卡不计为新完成卡。

---

## 10. 测试与验证

### 10.1 确定性层

- 单元测试；
- schema contract；
- golden parity；
- PIT 回放；
- 数据契约；
- 配置 hash；
- artifact hash；
- 幂等；
- 故障注入；
- 变异探针。

### 10.2 LLM 层

不做逐字 golden，验证：

- prompt 输入契约；
- 输出 schema；
- 引用合法性；
- 三门完整性；
- `UNKNOWN` 与 `FAIL` 区分；
- rubric 与终评级一致；
- 时效窗；
- 价格断言；
- early-stop 结构；
- ensemble 折回；
- 卡与 DecisionRecord 一致。

### 10.3 Workflow

- `AsyncFunction` 语法探针；
- 每个状态分支测试；
- 空返回；
- 连接中断；
- partial success；
- 单票失败；
- sentinel + pinned；
- config 丢失；
- intel 已存在；
- gate 失败；
- 重试后成功；
- 重试耗尽。

### 10.4 回放样本

至少固定以下真实事故为回归夹具：

- frame 0 字节；
- config 传 `{}`；
- ticker 前导零丢失；
- 卡面评级与终评不一致；
- earlystop/gate ledger 落后一个 run；
- intel 价格捏造与卡片否决转述假阳；
- L3 合法派生数字 lint 假阳；
- pinned 被 TTL 复用；
- 复用只看绝对涨跌；
- workflow transcript 未递归计量；
- retro 主尺无数据却写空归因。

### 10.5 五项验收矩阵

| 变更 | 研究 | 决策 | Token | 速度 | 架构 |
|---|---|---|---|---|---|
| L1 quota | PIT+前向 | false abstention | L4卡数成本 | 总墙钟 | 单变量可回滚 |
| L3 审计篮 | 覆盖率 | 错误弃权 | 固定卡预算 | 不增关键路径 | 产物分 lane |
| 门三态 | 唯一绑定 | UNKNOWN 不混 FAIL | 基本不变 | 基本不变 | 单一状态源 |
| 早停影子深核 | 错杀率 | 不改生产评级 | 影子单列 | 周期外运行 | 可关闭 |
| sector brief B | 下游质量 | BUY/弃权不退化 | 下降 | 下降 | 契约不变 |
| Intel 前置 | 证据不降 | 评级不变 | 不显著上升 | 下降 | presence gate |
| DecisionRecord | parity | 终评级一致 | 基本不变 | 基本不变 | 单一事实源 |
| 模块拆分 | 完全 parity | 完全 parity | 不退化 | 不退化 | 依赖改善 |

---

## 11. 分阶段实施蓝图

本节是未来开发顺序，不代表本轮开始实施。

### Wave 0：冻结口径与基线

目标：停止边调边量。

交付：

1. 将本文标为唯一当前 roadmap；
2. 固化 `16463c0` 基线；
3. 停止自动重标定生产权重；
4. 固化五项基线报告；
5. 清理未成熟与非交易日口径；
6. 给现有 proposals 标注“有效、已完成、被否决、等待样本”。

验收：

- 同一天重复生成基线读数一致；
- 0 BUY、买单、终评级三本账一致；
- 无业务行为变化。

回滚：

- 只回滚配置和文档指针，不删除历史账本。

### Wave 1：事实控制面

目标：统一“本次跑了什么”和“最终决定是什么”。

交付顺序：

1. `RunContract`；
2. `ArtifactIndex`；
3. `StageResult`；
4. `DecisionRecord`；
5. post-run outbox；
6. health 欠账与 hash 检查。

验收：

- 关键产物契约覆盖率 100%；
- config/pinned/model effort 不可能静默丢失；
- 终评级不再从 Markdown 反推；
- 现有报告与账本 golden parity；
- learning consumer 可单独补跑。

回滚：

- 保留旧文件写出；
- 新控制面先影子双写；
- 任一 parity 失败即继续读旧路径。

#### 2026-07-28 第一批实现状态

已进入影子双写：

- `frame --json` 写 `run_contract.json`，market pack 仅携带短引用；
- `run_health.json` 报告契约 `ABSENT / OK / INVALID`，暂不阻断；
- `assemble` 把 contract 身份固化进 manifest；
- 15 类关键产物进入 `artifact_index.json`，最终快照随 trace 发布；
- 历史 staging 缺少 contract 时保持兼容。

尚未升级为生产硬门：

- Workflow 参数与 contract hash 的逐段回显；
- GATE1 的 config/pinned/hash fail-fast；
- 生产者级 `input_hash`；
- `StageResult / DecisionRecord / outbox`。

### Wave 2：0-BUY 可解释与研究闭环

目标：回答每个 0 BUY 是正确弃权还是漏判。

交付顺序：

1. 首次死亡点；
2. 日级 `correct/false/neutral/immature abstention`；
3. 门三态；
4. 唯一绑定门 ledger；
5. L3 主研究篮/审计篮影子；
6. 早停周频影子深核；
7. ensemble 折回对错账。

验收：

- 100% 的 0-BUY 日有因果分桶；
- 多门失败不污染单门统计；
- UNKNOWN 不计入 FAIL；
- 生产 BUY 数量不作为验收项；
- 所有行为改动仍处于影子态。

回滚：

- 删除影子消费，不改变生产卡与报告主表。

### Wave 3：Token 与速度

目标：在研究守卫不退化的前提下压成本和关键路径。

交付顺序：

1. 主会话计量；
2. 输入/输出真实价格折算；
3. 失败与废弃调用成本；
4. L3 局部修复；
5. Intel 前置；
6. 单票流式 L4；
7. sector brief A/B；
8. L4 共享块与差异证据。

阶段一验收：

- 成本下降 ≥15%；
- P50 ≤75 分钟；
- cache ≥85%；
- false abstention、false buy 不退化。

阶段二验收：

- 成本累计下降 ≥25%；
- P50 ≤65 分钟；
- P90 ≤90 分钟；
- 单票失败不触发批次重跑。

回滚：

- 每个优化独立开关；
- 保留旧调度路径；
- 研究守卫先于成本守卫触发回滚。

### Wave 4：模块边界治理

目标：降低修改风险，不改变研究行为。

顺序：

1. 抽 `l4/rubric.py` 与 parsers；
2. 抽 `decision_finalize.py`；
3. 抽 L3 triage/merge；
4. 抽 report sections；
5. 抽 post-run consumers；
6. 清理反向 import；
7. 收缩 Workflow 业务解释；
8. 收缩 SKILL/STAGES 重复内容。

验收：

- 行为与产物 parity；
- 关键领域模块不反向导入 reporting；
- 终评级只有一个生产者；
- Workflow 只调度；
- 历史事故夹具全绿；
- 无新兼容层长期悬空。

回滚：

- 逐模块兼容导出；
- 每个拆分独立 commit；
- parity 失败只回滚当前边界。

### Wave 5：生产实验晋升

目标：只把已积累足够样本的研究 challenger 上线。

候选：

- value quota；
- heat/healthy quota；
- momentum T+2 新裁决；
- 主力门新定义；
- L3 审计篮正式化；
- early-stop 停因规则；
- sector brief 新路线；
- ensemble 买单/卖单策略。

每个候选独立晋升，不组成“大版本一起上线”。

---

## 12. 文件级未来改动地图

| 区域 | 未来职责 | 主要文件 |
|---|---|---|
| 运行契约 | config/hash/run identity | `scan/config.py`, `scan/user_config.py`, 新 contract 模块 |
| 产物契约 | artifact path/schema/hash | 扩展 `scan/artifacts.py` |
| 决策事实 | rubric/gates/final rating | 从 `l4_card.py`、`assemble.py` 抽出 |
| 阶段归因 | first rejection / abstention | `learning/stage_eval.py`、新 abstention ledger |
| 实验登记 | challenger 生命周期 | `learning/feedback_store.py` 或独立 registry |
| Token | 主会话、输出、废弃调用 | `trace/usage_harvest.py` |
| 速度 | 流式依赖与 presence gate | workflows + stage journal |
| 报告 | 纯视图 | 从 `assemble.py` 抽出 |
| learning | 事件消费 | journal、gate、earlystop、pinned 等 |
| 文档 | 当前态与 runbook 收敛 | `CLAUDE.md`、skills、STAGES |

---

## 13. Wave7 项目状态收编

截至 `16463c0`，Wave7 中以下事项已经完成，不应在本文重复列为待开发：

- usage_harvest 递归收 workflow transcript；
- price_claims 去转述/否决假阳；
- L3 合法引用 lint；
- gate/earlystop ledger 发布后补刷；
- frame pack-check；
- L3 修复调用断连兜底；
- pinned 不复用；
- 复用改量相对市场位移；
- Intel T0/24h/背景三窗；
- pinned ledger；
- 持仓 tripwire；
- L4 置信度校准；
- Intel 档案缺口回流；
- nightly deterministic close；
- retro 主尺无数时响亮失败。

部分完成：

- token 已有模型价差表，但主会话、输出价格和废弃调用仍未完整计量；
- Intel 时效已完成，但前置并行仍未完成；
- pinned 自进化账本已完成，但与统一 DecisionRecord 尚未收口；
- 夜间欠账补跑已完成，但控制面尚未统一 consumer 状态。

仍待本文接管：

- 0-BUY 因果归因；
- 门三态；
- L3 审计篮；
- 早停影子深核；
- ensemble 折回对错；
- RunContract / ArtifactIndex / DecisionRecord；
- 流式 L4；
- sector brief A/B；
- 核心模块拆分。

---

## 14. 风险与防护

### 14.1 样本薄

防护：

- 不用 n<10 细分改规则；
- PIT + 前向双门；
- 报告置信区间或样本警告；
- 跨 regime；
- 不因单次 run 翻案。

### 14.2 非平稳

防护：

- 主尺固定 T+2；
- regime 只分层，不任意改 horizon；
- challenger 有过期时间；
- 旧结论在定义变化处断点，不直接连趋势。

### 14.3 过度保守

防护：

- 同时审 BUY 和弃权；
- 错误弃权有独立 KPI；
- 审计篮；
- 早停影子深核；
- 单门 unique binding。

### 14.4 为省 Token 伤研究

防护：

- 成本变更必须过 false abstention/false buy；
- 稳定前缀与证据链不删；
- 影子 A/B；
- 单位有效决策成本，而非总量。

### 14.5 架构重构引入行为漂移

防护：

- 先契约、后搬代码；
- 双写；
- golden parity；
- 兼容导出；
- 每边界独立 commit；
- 禁止重构波顺带调参数。

### 14.6 文档再次漂移

防护：

- 本文是唯一 roadmap；
- STAGES 只写当前态；
- 参数表尽量生成；
- 历史 design 标 superseded；
- 每次生产行为变更必须同时更新一处当前态文档。

---

## 15. Definition of Done

这轮统一优化最终完成，不以“出了更多 BUY”定义，而以以下条件全部成立定义：

### 研究与决策

- 每个 0-BUY 日可归为正确弃权、错误弃权、中性或未成熟；
- 每只候选有首次死亡点；
- 单门与多门统计不混；
- UNKNOWN 与 FAIL 不混；
- BUY 与弃权都被前向问责；
- 召回、L3、早停、ensemble 均有独立边际价值账。

### Token 与速度

- 主会话和 subagent 全计量；
- 成本按模型和输入/输出价差折算；
- 成本较基线下降 ≥25%；
- cache 命中 ≥85%；
- P50 ≤65 分钟；
- P90 ≤90 分钟；
- 单票故障不触发批次重跑。

### 架构

- 关键产物 100% 进入 ArtifactIndex；
- RunContract hash 全链一致；
- DecisionRecord 是终评级单一事实源；
- learning 通过事件消费，不从 Markdown 猜事实；
- Workflow 只调度；
- 三个超大核心模块完成职责拆分；
- 历史事故全部有回归夹具；
- 所有迁移有 parity 与回滚路径。

### 治理

- 研究变更全部进入 ExperimentRegistry；
- 未成熟实验不进入生产；
- 自动调权停止；
- 本文成为唯一当前 roadmap；
- Wave7 退居历史参考；
- 没有 BUY 配额或凑单逻辑。

---

## 16. 最终判断

当前项目最大的机会不是再增加一个 agent、再堆一个因子或再调一次阈值，而是把已经拥有的大量能力变成一个可问责的系统：

- 上游负责不漏；
- L3 负责把研究预算花在值得验证的地方；
- L4 负责拒绝和定级；
- L5 负责诚实解释；
- learning 负责用前向结果批改；
- 控制面负责阻止错误结论、错误优化和文档漂移进入生产。

“经常没有 BUY”本身不是 bug。真正需要修复的是：系统必须能证明哪些 0 BUY 是纪律、哪些是失明，并且能在不放松纪律的前提下持续减少后者。
