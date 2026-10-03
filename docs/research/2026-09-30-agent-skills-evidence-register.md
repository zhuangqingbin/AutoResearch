# Agent skills 经验声明证据登记（2026-09-30）

本登记校正活跃说明中的业绩声明。当前决策主尺是 `gap_c1_o2`（T+1 收买 → T+2 开卖）；旧 `fwd_2_oc`、周级持有收益、横截面画像和 token 统计不能代替这把尺。登记只供开发与人工审阅，**不进入 L3/L4 任务包，不是运行时数据库，不恢复 learning 层，不自动调门或配额**。

## 读取规则

- `CURRENT_SUPPORTED`：原样本支持写明的窄命题，且采用当前主尺；不代表当前整条策略已有可交易净收益。
- `HISTORICAL_ONLY`：仅适用于原窗口、原版本与原人口；不能直接迁移到现行策略。
- `INSUFFICIENT`：已有同尺读数，但有效事件数、样本外覆盖或识别条件不足。
- `REQUIRES_REVIEW`：窗口、口径或版本证据缺失，不能提升为当前结论。
- `REFUTED`：指定版本与样本中的窄命题被否定；不表示所有未来版本永远无效。

`sample_window` 只填原材料明确报告的起止日；只有月份、年份、报告日期或另一个总审计窗口时填 `null`。原材料没有代码 hash 时，`strategy_version` 明写能识别的历史实现，不补造版本号。每个 `evidence_path` 指向可共享的仓内原记录，本次没有读取任何引擎的历史产物，也没有重跑回测。引用保存的研究文档不等于已复现原始数据。

每日组均值、股票观测数、交易数、调样事件数不是同一个分母。相对市场中位数/对照组的毛超额不等于扣成本、受交易约束后的策略 NAV。prompt、选择规则、配置或执行时点变化后，应按现有版本/hash 重新界定可比样本；登记本身不创建运行状态。

## 核心分工与旧证据

<a id="historical_l2_model_value"></a>
### historical_l2_model_value

[L2 分层采样设计，附录 B](../specs/2026-06-25-l2-stratified-sampler-design.md)记录模型 OOS 负 IC，以及 83 个形成日、2022-06～2026-05 的 composite-top200 旧尺结果（`fwd_5_oc` 约 −1bps）。没有精确起止日。它支持当时退役模型的选择，不支持“所有确定性层永远没有 alpha”。

```json
{
  "claim_id": "historical_l2_model_value",
  "ruler": "fwd_5_oc",
  "strategy_version": "2026-06-25 L2 zoo 与 composite-top200 对照，原稿未绑定代码 hash",
  "sample_window": null,
  "sample_status": "REQUIRES_REVIEW",
  "evidence_path": "docs/specs/2026-06-25-l2-stratified-sampler-design.md",
  "applicability": "原模型和旧持有尺；月份范围不能补成精确日期，不能推成当前 gap_c1_o2 全策略结论。",
  "revalidation_trigger": {"ruler": "改用 gap_c1_o2", "selection_rule": "特征、模型、权重档或菜单改变", "execution_policy": "进出场、交易约束或成本改变"}
}
```

<a id="historical_l4_rating_ic"></a>
### historical_l4_rating_ic

[PANORAMA §8.1](../PANORAMA.md)的 07-14 摘录报告评级 rank-IC +0.55；没有完整样本窗口。[08-22 同尺普查 §0](2026-08-22-edge-census.md)明确其为旧 `fwd_2_oc`，并指出换尺后没有重建同等证据。07-14 是摘录日期，不能当成样本起止日。

```json
{
  "claim_id": "historical_l4_rating_ic",
  "ruler": "fwd_2_oc",
  "strategy_version": "PANORAMA 07-14 摘录对应旧 L4 评级，未绑定代码/prompt hash",
  "sample_window": null,
  "sample_status": "REQUIRES_REVIEW",
  "evidence_path": "docs/PANORAMA.md",
  "applicability": "历史旧尺 +0.55；不能作为当前 gap_c1_o2 评级或拒绝有效的证明。",
  "revalidation_trigger": {"ruler": "切换 gap_c1_o2", "selection_rule": "评级 rubric、prompt、候选池或 ensemble 改变", "execution_policy": "进出场或成本改变"}
}
```

<a id="historical_l4_rejection_value"></a>
### historical_l4_rejection_value

[07-11 证据快照 §3–4](2026-07-11-funnel-evidence-snapshot.md)：hold=2、10% 固定槽、次日开盘进出；真实 NAV −0.30%、影子 −4.65%，差 +4.35pp。NAV 表明确窗口 2026-06-18→07-08、14 个节点，真实 7 笔、影子 45 笔；这些分母不可互换。[08-22 普查](2026-08-22-edge-census.md)确认这是旧 `fwd_2_oc` 证据。

```json
{
  "claim_id": "historical_l4_rejection_value",
  "ruler": "fwd_2_oc",
  "strategy_version": "2026-07-11 快照中的旧 L4 真/影子门，hold=2、10% 固定槽",
  "sample_window": {"start": "2026-06-18", "end": "2026-07-08"},
  "sample_status": "HISTORICAL_ONLY",
  "evidence_path": "docs/research/2026-07-11-funnel-evidence-snapshot.md",
  "applicability": "仅原版本的 NAV 差；不能把 +4.35pp 转为当前 gap_c1_o2 的拒绝收益或因果效果。",
  "revalidation_trigger": {"ruler": "切换 gap_c1_o2", "selection_rule": "门、候选或卡片判断改变", "execution_policy": "持仓槽、进出场、成交约束或成本改变"}
}
```

<a id="historical_recall_capture"></a>
### historical_recall_capture

[07-07 设计](../specs/2026-07-07-memory-astrategy-optimization-design.md)引用 06-24 retro：赢家 91% 落池、4.8% 越过召回线。[08-03 汇总](../specs/2026-08-03-scan-next-wave-brainstorm-design.md)再出现 413 只描述，但未在同一原记录绑定赢家期限、413 分母和完整窗口。因此不把 06-24 单日引用扩写成全时期的“0 买根因”。

```json
{
  "claim_id": "historical_recall_capture",
  "ruler": "UNKNOWN_WINNER_HORIZON",
  "strategy_version": "06-24 retro 的旧打分池/top1000；当前召回实现已多次改变",
  "sample_window": null,
  "sample_status": "REQUIRES_REVIEW",
  "evidence_path": "docs/specs/2026-07-07-memory-astrategy-optimization-design.md",
  "applicability": "历史召回诊断线索；413、91%、4.8% 不足以识别当前零买的原因，也不能当成当前漏召回率。",
  "revalidation_trigger": {"ruler": "明确赢家标签并改为 gap_c1_o2", "selection_rule": "召回通道、quota、floor、权重或 L0 过滤改变", "execution_policy": "加入可交易性、时点或成本约束"}
}
```

<a id="historical_value_channel"></a>
### historical_value_channel

[08-18 设计 §2.2、B1](../specs/2026-08-18-e6-activation-learning-slimdown-design.md)记载 36 日通道账本中 value +0.9%、胜率 57.6%。没有给出这 36 日子样本的准确窗口和所用收益尺；不能拿文中另一个“40 日扫描审计”的 06-18→08-17 替代。也不能与更早 13 日 value +1.04% / 64% 的样本拼接。

```json
{
  "claim_id": "historical_value_channel",
  "ruler": "UNKNOWN_IN_ORIGINAL_CHANNEL_SUMMARY",
  "strategy_version": "2026-08-18 设计引用的 36 日通道账本；原记录未绑定权重/召回版本",
  "sample_window": null,
  "sample_status": "REQUIRES_REVIEW",
  "evidence_path": "docs/specs/2026-08-18-e6-activation-learning-slimdown-design.md",
  "applicability": "配额调整的历史背景；57.6%/+0.9% 不能称为当前 gap_c1_o2 下最优通道。现行配额是配置事实，不是此数字的复现。",
  "revalidation_trigger": {"ruler": "确认原尺并统一 gap_c1_o2", "selection_rule": "value 谓词、配额、基准或其它通道改变", "execution_policy": "加入当前买卖时点、可成交性和成本"}
}
```

<a id="historical_zero_buy_market"></a>
### historical_zero_buy_market

[PANORAMA §8](../PANORAMA.md)曾以零买日市场 fwd_1 −0.48% / fwd_5 −0.60% 支持拒绝判断，未给完整窗口。[07-11 快照 §1](2026-07-11-funnel-evidence-snapshot.md)对相近的旧 zero-buy 账本另指出 `bought` 缺失、真实买入日混入，且数值不同；不能把两组摘要视作同一已核验样本。单日市场上涨也不能单独证明“失明”。

```json
{
  "claim_id": "historical_zero_buy_market",
  "ruler": "LEGACY_FWD_1_AND_FWD_5",
  "strategy_version": "PANORAMA 中旧 zero-buy 汇总，买入日分组身份待核",
  "sample_window": null,
  "sample_status": "REQUIRES_REVIEW",
  "evidence_path": "docs/PANORAMA.md",
  "applicability": "不能据此证明当前零买有效；应按当日菜单、卡片、门和执行条件分别归因。",
  "revalidation_trigger": {"ruler": "统一 gap_c1_o2 与对照", "selection_rule": "校正买入日分组并绑定实际策略版本", "execution_policy": "按真实可交易时点和成本比较"}
}
```

## 同尺证据与适用边界

<a id="overnight_l4_rejection_unproven"></a>
### overnight_l4_rejection_unproven

[08-22 普查 §0–2](2026-08-22-edge-census.md)覆盖 06-22→08-19，40 个可算扫描日：L4 每日评级 IC 只有 34 日，+0.118、t=1.68；≥OW 只有 4 日。该记录没有重建旧尺的拒绝收益。低显著性和小样本既不证明有效，也不证明无效。

```json
{
  "claim_id": "overnight_l4_rejection_unproven",
  "ruler": "gap_c1_o2",
  "strategy_version": "2026-08-22 edge census 对当时 L4 卡和门的普查，非现行 e6.v4.1",
  "sample_window": {"start": "2026-06-22", "end": "2026-08-19"},
  "sample_status": "INSUFFICIENT",
  "evidence_path": "docs/research/2026-08-22-edge-census.md",
  "applicability": "同尺拒绝优势尚未建立；评级 IC 和≥OW组均值也不是完整可执行 NAV。不得用旧 +0.55/+4.35pp 补足证据。",
  "revalidation_trigger": {"ruler": "标签、异常值处理或市场基准改变", "selection_rule": "卡片、rubric、候选、评级门或策略版本改变", "execution_policy": "需前向可交易样本与实际成本，尤其补足稀少买入事件"}
}
```

<a id="overnight_channel_census"></a>
### overnight_channel_census

[08-22 普查主表及 §1](2026-08-22-edge-census.md)：composite 39 日 +0.14pp（t=3.05），L3 finalist 39 日 −0.27pp（t=−3.94），value 39 日 −0.04pp（t=−1.09），healthy 29 日 −0.37pp（t=−5.58）。composite 的 39 日中 30 日落在校准样本内，样本外只有 9 日；原权重是 calibrated，当前生产为 preference。原文的 40 个可算日不是每一族的 n，不能与 08-26 的 n=40 重跑结果混接。

```json
{
  "claim_id": "overnight_channel_census",
  "ruler": "gap_c1_o2",
  "strategy_version": "2026-08-22 census，历史 calibrated 权重、当时通道/选择规则",
  "sample_window": {"start": "2026-06-22", "end": "2026-08-19"},
  "sample_status": "INSUFFICIENT",
  "evidence_path": "docs/research/2026-08-22-edge-census.md",
  "applicability": "原家族毛超额相对当日全市场中位数，存在家族重叠与样本内校准；支持历史诊断，不足以推出当前通道优劣或全策略净 alpha。",
  "revalidation_trigger": {"ruler": "基准、标签或有效日口径改变", "selection_rule": "calibrated→preference、通道谓词、配额或 L3 改变", "execution_policy": "使用真正样本外窗口并计入成交约束与成本"}
}
```

<a id="overnight_chase_executable"></a>
### overnight_chase_executable

[08-08 证据补口 ①](2026-08-08-overnight-evidence-gap.md)：2022-03-02→2026-08-05，1075 个形成日；D 日涨幅≥9.5%最高桶在可执行样本中隔夜超额 −0.96%，95% CI [−1.06%, −0.84%]，83,047 个股票观测。对照是同日可执行全市场等权；大涨原人口仅保留 83.3%，多数剔除来自 D+1 涨停不可买。这里量 D+1 收→D+2 开，不是 D 收→D+1 开的打板收益。

```json
{
  "claim_id": "overnight_chase_executable",
  "ruler": "gap_c1_o2",
  "strategy_version": "2026-08-08 overnight_evidence 执行过滤和 D 日涨幅分桶，非当前完整策略",
  "sample_window": {"start": "2022-03-02", "end": "2026-08-05"},
  "sample_status": "CURRENT_SUPPORTED",
  "evidence_path": "docs/research/2026-08-08-overnight-evidence-gap.md",
  "applicability": "原可执行人口中追 D 日大涨的负向证据；仅支持此窄命题，不涵盖所有大涨股、当夜打板、当前净收益或任意交易规则。",
  "revalidation_trigger": {"ruler": "换持有夜或对照基准", "selection_rule": "涨幅阈值、市场、股票池或过滤改变", "execution_policy": "可买过滤、下单时点、退出方式或成本改变"}
}
```

<a id="overnight_lowturn_positive_edge"></a>
### overnight_lowturn_positive_edge

[08-21 lowturn 预检](2026-08-21-lowturn-precheck.md)：2025-05-23→2026-08-05，共 132 个形成日；隔夜相对市场中位数 −0.24pp、t=−6.36。5 日 +0.63pp（129 日）与 10 日 +0.99pp（124 日）不能迁为隔夜优势。允许继续是因为没有越过当时的 −0.5pp 幅度线，不是检出了正 alpha。

```json
{
  "claim_id": "overnight_lowturn_positive_edge",
  "ruler": "gap_c1_o2",
  "strategy_version": "2026-08-21 lowturn_precheck 原谓词，原文 e054fc6/adde3f6 所述版本",
  "sample_window": {"start": "2025-05-23", "end": "2026-08-05"},
  "sample_status": "REFUTED",
  "evidence_path": "docs/research/2026-08-21-lowturn-precheck.md",
  "applicability": "原谓词具有正隔夜超额的命题不成立；不限制把它作为菜单形状，也不证明后来修改的谓词表现。",
  "revalidation_trigger": {"ruler": "尺或市场对照改变", "selection_rule": "lowturn 谓词、排序或候选配额改变", "execution_policy": "执行时点、过滤或成本改变"}
}
```

## 其它仍影响生产说明的历史记录

<a id="historical_multichannel_overlap"></a>
### historical_multichannel_overlap

[08-22 漏斗形状设计 §1](../specs/2026-08-22-funnel-shape-after-lowturn-first-run-design.md)引用 2026-08-21 单日 run：L2 的 `n_channels` 与 `dist_high_60` 相关 0.34、与 `pct_60d` 相关 0.29；当日 9 席中贵金属及下游 5 席。这是重叠与集中度诊断，不是收益检验，也不证明“多路必然等于追高”。

```json
{
  "claim_id": "historical_multichannel_overlap",
  "ruler": "CROSS_SECTIONAL_SHAPE_NOT_RETURN",
  "strategy_version": "20260821_2255 的 L2/初次 lowturn 菜单与 L3",
  "sample_window": {"start": "2026-08-21", "end": "2026-08-21"},
  "sample_status": "HISTORICAL_ONLY",
  "evidence_path": "docs/specs/2026-08-22-funnel-shape-after-lowturn-first-run-design.md",
  "applicability": "单日画像与集中度；可解释当时去共振加分/设行业帽，不能转成隔夜表现。",
  "revalidation_trigger": {"ruler": "若主张收益必须另用 gap_c1_o2", "selection_rule": "通道定义、配额、行业映射或选择器改变", "execution_policy": "若用于交易结论，需增加执行约束"}
}
```

<a id="historical_trend_confidence"></a>
### historical_trend_confidence

[07-12 learning 调查](2026-07-12-learning-system-survey.md)核到 trend 高确信被 L4 改判 33%（n=52）的旧摘要，但没有精确样本窗口。L3/L4 分歧率不是未来收益，更不能独立证明拒绝正确。

```json
{
  "claim_id": "historical_trend_confidence",
  "ruler": "L3_L4_DISAGREEMENT_NOT_RETURN",
  "strategy_version": "2026-07-12 调查所见旧 L3/L4 calibration 摘要",
  "sample_window": null,
  "sample_status": "REQUIRES_REVIEW",
  "evidence_path": "docs/research/2026-07-12-learning-system-survey.md",
  "applicability": "历史判断分歧提示；不能作为当前翻案率、正确率或 gap_c1_o2 胜率。",
  "revalidation_trigger": {"ruler": "从分歧率改为交易结果时需另建同尺证据", "selection_rule": "L3 conviction、L4 rubric、模型或 prompt 改变", "execution_policy": "若解释投资效果，需绑定执行和成本"}
}
```

<a id="historical_accumulation"></a>
### historical_accumulation

[07-11 快照 §5](2026-07-11-funnel-evidence-snapshot.md)给出 06-22→07-08 的 13 日通道审计，accumulation unique T+2 超额 −0.21%。此处是旧持有尺、原通道去重人口。

```json
{
  "claim_id": "historical_accumulation",
  "ruler": "fwd_2_oc",
  "strategy_version": "2026-07-11 旧通道 unique 审计，停用前 accumulation",
  "sample_window": {"start": "2026-06-22", "end": "2026-07-08"},
  "sample_status": "HISTORICAL_ONLY",
  "evidence_path": "docs/research/2026-07-11-funnel-evidence-snapshot.md",
  "applicability": "历史停用依据的一部分；13 日旧尺负读数不能证明所有版本在 gap_c1_o2 下无效。",
  "revalidation_trigger": {"ruler": "换为 gap_c1_o2", "selection_rule": "通道谓词、去重集合或排序改变", "execution_policy": "时点、可交易性或成本改变"}
}
```

<a id="historical_northbound"></a>
### historical_northbound

[07-11 召回设计](../specs/2026-07-11-recall-gate-pinned-config-design.md)记录 `hk_ratio` T+2 IC −0.108，未报告准确样本起止日。仅保留为当时停用通道的历史依据。

```json
{
  "claim_id": "historical_northbound",
  "ruler": "LEGACY_T_PLUS_2",
  "strategy_version": "2026-07-10 T7 复核在 07-11 设计中的摘要，原 panel 未绑定",
  "sample_window": null,
  "sample_status": "REQUIRES_REVIEW",
  "evidence_path": "docs/specs/2026-07-11-recall-gate-pinned-config-design.md",
  "applicability": "旧 hk_ratio 因子摘要；非当前 gap_c1_o2 或全部北向信息的效果结论。",
  "revalidation_trigger": {"ruler": "明确原标签并统一 gap_c1_o2", "selection_rule": "持股字段、滞后、覆盖或因子定义改变", "execution_policy": "考虑信息可得时点和成交条件"}
}
```

<a id="historical_momentum"></a>
### historical_momentum

[08-04 momentum 研究](2026-08-04-momentum-phase-conditional-ic.md)报告 06-17→08-03 的 26 个成熟扫描日，旧 `fwd_2_oc` 的 unique 均值从早期 +0.75% 更新为 −1.07%。该文顶部 08-09 补注明确这不等于隔夜尺结果。

```json
{
  "claim_id": "historical_momentum",
  "ruler": "fwd_2_oc",
  "strategy_version": "2026-08-04 momentum unique 旧通道/相位研究",
  "sample_window": {"start": "2026-06-17", "end": "2026-08-03"},
  "sample_status": "HISTORICAL_ONLY",
  "evidence_path": "docs/research/2026-08-04-momentum-phase-conditional-ic.md",
  "applicability": "旧尺与原人口的样本扩充反转，不能直接宣告当前 momentum 隔夜失效。",
  "revalidation_trigger": {"ruler": "切换 gap_c1_o2", "selection_rule": "相位、去重、通道或配额改变", "execution_policy": "可交易时点、成交约束或成本改变"}
}
```

<a id="historical_forecast_channel"></a>
### historical_forecast_channel

[08-03 汇总](../specs/2026-08-03-scan-next-wave-brainstorm-design.md)引用预告通道 T+5 超额 −0.27% / 胜率 35%、追缺口 −2.92%，完整事件窗口与分母未随摘要保留。不能据此断言所有公告事件没有 alpha 或收益必在披露前。

```json
{
  "claim_id": "historical_forecast_channel",
  "ruler": "LEGACY_T_PLUS_5",
  "strategy_version": "2026-08-03 汇总引用的旧强制披露季预告试验",
  "sample_window": null,
  "sample_status": "REQUIRES_REVIEW",
  "evidence_path": "docs/specs/2026-08-03-scan-next-wave-brainstorm-design.md",
  "applicability": "原事件通道的历史负结果摘要；当前不启用是产品决定，非隔夜通用定理。",
  "revalidation_trigger": {"ruler": "改为 gap_c1_o2", "selection_rule": "公告事件定义、季节、预期差或样本过滤改变", "execution_policy": "公告可得时点、买卖规则或成本改变"}
}
```

<a id="historical_p3_stop_distribution"></a>
### historical_p3_stop_distribution

[09-26 整编设计 §1.2、A3](../superpowers/specs/2026-09-26-daily-engine-consolidation-design.md)记载早停 238/239 在 P3，但未绑定这 239 张卡的完整窗口。旁边的“9 月 7 次成功扫描 / 70% 早停”不是足够的子样本身份说明。停在哪一阶段也不能单独证明判断正确或没有数据失败。

```json
{
  "claim_id": "historical_p3_stop_distribution",
  "ruler": "CARD_STAGE_DISTRIBUTION_NOT_RETURN",
  "strategy_version": "2026-09-26 整编设计引用的既有早停卡摘要",
  "sample_window": null,
  "sample_status": "REQUIRES_REVIEW",
  "evidence_path": "docs/superpowers/specs/2026-09-26-daily-engine-consolidation-design.md",
  "applicability": "历史成本/阶段诊断线索，不能把早停比例或 P3 位置解释为当前拒绝准确率。",
  "revalidation_trigger": {"ruler": "若解释收益必须另量 gap_c1_o2", "selection_rule": "卡片早停规则、数据生产者、模型或 prompt 改变", "execution_policy": "统计须绑定同一 run/卡集合及实际失败状态"}
}
```

<a id="historical_close_position"></a>
### historical_close_position

[08-26 E6 设计 §1.6](../specs/2026-08-26-scene-retention-and-buy-owner-design.md)记录 2022-03～2026-08、1086 日盘后代理：收在当日区间上 30% 的票隔夜较全体低 0.13～0.27pp。未给精确起止日；人口含成交额/涨幅/一字过滤，对照依 spike 实现为每日中位数。这不是 14:45 能得到相同字段、能以同样价格成交的证明。

```json
{
  "claim_id": "historical_close_position",
  "ruler": "gap_c1_o2",
  "strategy_version": "2026-08-26 spike_overnight_4y 的盘后区间位置代理",
  "sample_window": null,
  "sample_status": "REQUIRES_REVIEW",
  "evidence_path": "docs/specs/2026-08-26-scene-retention-and-buy-owner-design.md",
  "applicability": "原 EOD 代理的负相关，未扣成本；不能把月度范围补成精确日，也不能当作现行尾盘规则净收益。",
  "revalidation_trigger": {"ruler": "对照或标签改变", "selection_rule": "位置阈值、过滤或候选池改变", "execution_policy": "以盘中可得字段、实际入场时点及成本重验"}
}
```

<a id="historical_index_rebalance"></a>
### historical_index_rebalance

[09-25 调样设计附录 A](../specs/2026-09-25-index-inclusion-signal-design.md)主表为 5 个半年调样指数（剔科创50）、1652 调入票次：公告夜 −0.07pp、跑道 −0.10pp、生效前夜 −0.31pp / 胜率 35%。原文只报 2022–2026、17 次调样，未给精确日期边界；月末快照可能混入临时调整，事件日按规则推断，未扣成本。六指数默认 CLI 的 n=1698 / −0.297pp 是另一人口，不能混作复现。

```json
{
  "claim_id": "historical_index_rebalance",
  "ruler": "gap_c1_o2",
  "strategy_version": "2026-09-25 附录 A 五指数半年调样人口，E6 v4.1 设计依据",
  "sample_window": null,
  "sample_status": "REQUIRES_REVIEW",
  "evidence_path": "docs/specs/2026-09-25-index-inclusion-signal-design.md",
  "applicability": "原事件人口及对照（同指数未变动成分股）的毛超额；窗口和真实公告日期补齐前，不宣称守卫已有前向净收益证明。",
  "revalidation_trigger": {"ruler": "相位、对照或标签改变", "selection_rule": "指数人口、调样名单、临时调整清洗改变", "execution_policy": "核实公告可得时点、实际生效日及成本"}
}
```

<a id="historical_index_prediction"></a>
### historical_index_prediction

[09-25 调样设计 §2.8、附录 B](../specs/2026-09-25-index-inclusion-signal-design.md)：预测名单 A−1 夜，沪深300 +0.21pp / 胜率49% / t_event=0.6，科创50 +0.55pp / 54% / t_event=2.7。不能写“两者皆不显著”；两者都未达预设胜率≥65%，沪深300还未达超额≥0.4pp。完美预见名单的结果是不可直接交易的上界，不是预测器实绩。原表无准确窗口。

```json
{
  "claim_id": "historical_index_prediction",
  "ruler": "gap_c1_o2",
  "strategy_version": "2026-09-25 predictor spike，沪深300 7 轮/科创50 14 轮规则复刻",
  "sample_window": null,
  "sample_status": "REQUIRES_REVIEW",
  "evidence_path": "docs/specs/2026-09-25-index-inclusion-signal-design.md",
  "applicability": "原预测器未达预设胜率门槛；不立项裁定保留。不能将科创50 t=2.7 写成不显著，也不能将 oracle 收益转为可实现 alpha。",
  "revalidation_trigger": {"ruler": "事件夜或对照改变", "selection_rule": "A 股市值口径、ESG 排除、预测器或指数覆盖改变", "execution_policy": "名单预测必须在公告前可得，计入误报、交易约束和成本"}
}
```

## 维护边界

改活跃说明时，通过 `[证据:claim_id]` 链到对应记录；原材料不足时保留未知，不从其它样本借分母或窗口。补齐证据或变更策略后更新状态与适用范围，并保留原来源。历史规格和 PANORAMA 不整篇重写；活跃入口不得继续把历史摘要当作当前效果保证。产品裁定、数据完整性门和评级契约继续由各自代码/配置拥有，本登记不授予新的交易或研究动作。
