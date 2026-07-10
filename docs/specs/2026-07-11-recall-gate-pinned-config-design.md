# 召回整编 × L3.5 可插拔闸 × 保送直通 × 配置收口 —— 设计稿

日期:2026-07-11。来源:07-11 brainstorm 五方向 + 用户逐条裁决。前置:07-10 超短波已完结(fwd_2_oc 主尺贯通、卡契约 v3、机构面进卡,终审 Approved,834 绿)。

## §0 用户裁定(最高锚,盖过本稿其余论证)

1. **hermes 借鉴**:cron 化运维 **不做**(用户明确否);其余可做——配置文件塑形(并入 §4)、playbook-diff 自改进提案(§5.1)、FTS 判例检索(§5.2,Wave B)。
2. **底部反转**:四段确认定义可做;**并要求召回路整编**——"目前看召回路太多了,是否可以优化合并"。
3. **L4 出量 6~10**:认可;闸门做成**可插拔**,且要有**可回测**方式,迭代出好闸。
4. **保送**:文件放 `.claude/skills/scan-market/` 下(json/yaml 由我定→**裁决 JSON**);保送票**从 L1 一直走到 L5**(全程直通,非空降 L4)。
5. **配置**:`.claude/agents/*` frontmatter = 各 agent 的**默认** model/effort;`.claude/skills/scan-market/` 下配置文件定义 **scan-market 全程**用到的 agent 的 model/effort——统一管控。

格式裁决理由(JSON):stdlib 零依赖、与 weights.json/macro_state.json/proposals.jsonl 同族、jq 可查、机读校验简单;注释需求由 `note` 字段承载。

## §1 摸底事实(设计立足点,均已现场核实)

- **通道注册表现有 10 路**(`autoresearch/scan/recall/channels.py`,@channel 装饰器注册,"加一路=写函数+注册,不动 stage/merge"):composite(400/100)、momentum(250/50)、reversal(200/50)、value(200/50)、main_fund(200/50)、heat(200/50)、growth(150/40)、healthy(150/40)、northbound(120/30)、accumulation(120/30)。
- **ScanConfig 已有配置旋钮**(config.py:16):`recall_channels`(启用子集)/`channel_quotas`/`channel_floors`/`l2_floors`/`regime_aware` 等——代码面配置已半存在,缺的是**用户文件层**。
- **lens_reversal 现行定义**(common/scoring.py:167):边际改善40 + 超跌30 + 资金确认30,门=(改善∨资金)。**无企稳段、无量价确认段**——可召回仍在下跌途中的票(接刀缝隙);winner_rate 底部结构用法已因 regime 翻转实证被剔。
- **T+2 读数**:累计 13 日(channel_ledger):value +1.1%/命中 64.5% 全路第一,momentum 仅 +0.4%/48.1%(其 +6.3% 只在 T+5 窗)。07-08 单日(channel_eval):**reversal +1.56%/73.7% 全场第一**(n=175/96 unique)、main_fund +0.97%/63.8%、momentum −1.35%、heat −1.53%(t5 +4.2%=T+5 品种)、northbound 因子 hk_ratio T+2 IC −0.108 两半同负(07-10 T7 重审)。
- workflow 已把 `l4_budget` 告知 L3(l3-rank 产 ~28 judged);预算旗连败时已会压额。
- 影子反事实机制已有先例(pre_healthy 影子、影子漏斗 A/B,2026-07-02 第三波)。
- 07-10 波后排跟进项(final-review-prep.md)与本稿无冲突,照旧另行处理。

## §2 方向 2:reversal 升级四段确认 + 召回整编

### 2.1 reversal_confirm 四段定义(EOD 全可算)

**超短前提**:T+2 尺下"底部构筑期"不可交易,可交易的只有**确认日/起爆日**(D 日 EOD 识别 → D+1 开买 → D+2 收卖)。故本通道 = 反转**确认**通道,非左侧摸底。

1. **前置低位**:pct_60d ≤ −25%,或现价距 60/120 日低点 ≤15%。
2. **衰竭企稳**:近 10 日不创新低 + 缩量(5 日均量 < 20 日均量)+ RSI6 从超卖区回升。
3. **确认起爆**:当日量比 ≥1.5~2(vs 20 日均量)+ 阳线站上 MA20 或突破近 20 日高点。**无量突破=假突破,硬门不入**。
4. **可交易**:非一字板(现有涨跌停可交易性检查)。

新因子(量比、距低点距离%、不创新低天数)入 factor_lab,**过 T7 同款三门**(两半同号 ∧ ICIR 前半 ∧ decile spread_t≥2)再进权重;**确认信号禁用 CMF-20**(汇川/柳工 day1-2 滞后实证),用当日量比/当日 main_net。L4 侧配一条纪律:反转候选须查"下跌有因"(质押/业绩雷/问询=价值陷阱非反转)。

### 2.2 升级姿势(不动旧路,影子对照)

新注册 `reversal_confirm` 通道,与旧 `reversal` **并行跑 ≥10 日** → channel_eval 按 lane 自动对照(unique_excess_t2/hit_rate_t2)→ 新路胜则旧路经 `recall_channels` 配置退役(不删代码,可回滚)。

### 2.3 召回整编(数据驱动,非拍脑袋)

- **证据件先行**:新增「通道整编报告」CLI(零 LLM):各路**累计** T+2 账本(门槛 n≥10 日)+ 两两召回集 Jaccard 重叠矩阵 + unique_excess 排序,一页落盘。
- **默认整编案**(待报告数据确认后人批,逐条独立可否):
  | 动作 | 通道 | 依据 |
  |---|---|---|
  | 退役→L4 advisory | northbound | hk_ratio T+2 IC −0.108 两半同负;北向占比已在 L4 简报行,不损失可见性 |
  | 合并为 trend 一路 | momentum + heat | 同为 T+5 品种(T+2 负超额)、召回重叠预计高;quota 250+200→200 |
  | 并入 reversal_confirm | accumulation | 吸筹=四段定义的前置段①②;其可交易时刻就是确认日;CMF 滞后坑同源 |
  | 缩额观察 | growth 150→100 | T+2 弱;基本面轴保留待累计数据 |
  | 保留 | composite/value/main_fund/healthy | value 累计第一、main_fund 稳定正;healthy 按累计数据再裁 |
- 结果:10 路 → 6~7 路。全程走 `recall_channels`/`channel_quotas` 配置开关 + 影子 A/B,parity 不破,随时回滚。

## §3 方向 3:L3.5 可插拔闸 + 回测(L4 出量 6~10)

- **L3 判断面不缩**:l3-rank 照产 ~20-28 judged(rank-IC +0.36/+0.47 的评估连续性不断)。
- **闸=确定性可插拔层**,镜像 recall registry 模式:`@gate(name)` 注册,`scan_config.json` 选 `{name, params}`。v1 内置三策略:
  - `conviction_floor_quota`:conviction 地板 + lane 多样性配额(每主 lane ≥1)+ regime 分档上限(trend 10 / range 8 / risk_off 6)——推荐默认;
  - `topk_simple`:纯 L3 排名截断(对照基线);
  - `passthrough`:=现状全放行(parity 默认,配置缺失时行为不变)。
- **回测 harness**(`gate_backtest` CLI,零 LLM):重放历史各日 `L3_judged_full.csv × attribution.fwd_2_oc`,对每个 gate 变体输出:入选集 mean_fwd2/hit、**落选赢家清单**(错杀审计)、入选数分布。机制同 l2_eval forward_compare 先例;现有 ≥14 日历史即刻可跑,以后每次 retro 自动多一天样本——这就是"迭代出好闸"的路径。
- **上线双保险**:被闸掉的票记 fwd_2 影子反事实(零成本,两周内裁决闸的错杀率);保送/观察单直通车/carryover **不占 6~10 坑**。现有预算旗逻辑收编为闸的输入参数(一个机制,不留两套)。
- 附带收益预估:L4 30→8 卡 + slim 只拉入选票,65 分钟基线预计 →35~40 分钟,token 省半以上。已知代价:卡片类账本(触价校准/门审计)样本变薄——0 买纪律下可接受。

## §4 方向 4+5:两个 JSON 落 `.claude/skills/scan-market/`

### 4.1 `pinned.json`(保送,L1→L5 全程直通)

- 结构:`[{code, note, expires}]`;**cap ≤5**;缺 `expires` 默认 10 个交易日;过期条目自动失效并在报告备注(防僵尸条目永久吃 token)。
- 全程语义(用户裁定"从 L1 一直走到 L5"):
  - **L1**:强注召回(lane=`pinned`,额外注入**不占 recall_n**);湖缺数据→诚实降级注(不编数);
  - **L2**:强留(不占 l2_n,不挤他票);
  - **L3**:入 `_l3_table` 带 📌 标记——L3 **真判**(判断记录在案)但**不可淘汰**;
  - **L4**:必出卡(复用规则照常适用,♻️ 也在 📌 节可见),**不占 §3 的 6~10 配额**;
  - **L5**:报告独立「📌 保送」节,印 note。
- 记账红利:channel_eval 按 lane 天然把 `pinned` 独立成行 = **你的手工票 T+2 记分卡**,与漏斗读数互不污染。

### 4.2 `scan_config.json`(全程 agent 管控 + 漏斗旋钮)

- **v1 白名单键**(白名单外的键=报错,防拼写错静默失效):
  - `agents`:`{strategist|sector_brief|l3_rank|l4_card|redteam: {model, effort}}` —— 覆盖优先级:**scan_config > workflow/代码内建 > agent def frontmatter 默认**(与用户裁定语义一致:agents 目录=默认,此文件=scan 全程管控);
  - `l4_gate`:`{name, params}`(§3 闸选择);
  - `funnel`:`{recall_channels, channel_quotas, channel_floors}`(§2 整编开关,直喂 ScanConfig 既有字段);
  - `pinned`:`{cap, ttl_days}`;
  - `redteam_prob`、`reuse: {max_age_days, price_delta_pct}`。
- **装载链**(技术约束:workflow 脚本无文件系统访问,不能自己读文件):Stage 0 `frame --json` 读入 + 白名单校验 + **回显进 market_pack/run meta**(trace 记录本次跑用的配置=可复现)→ workflow 经 `args` 消费(脚本内硬编码值降级为 fallback)→ Python 侧同一 loader 喂 `ScanConfig`。
- **parity**:缺文件/缺键 = 现行为,一切默认关。
- **不进配置**:网查上界(≤2/≤3/全卡5)——那是提示词契约,锚测试锁死,配置化反破契约。

## §5 hermes 借鉴落地(Wave B)

- **5.1 playbook-diff 自改进提案**:retro/复盘除权重提案外,可产出「提示词补丁提案」(target file + 具体 diff + 证据读数),进 proposals.jsonl 同一审批流(人批才落)。解"lessons 仅 4 条=写侧瓶颈";M2 四操作裁决的自然延伸。
- **5.2 FTS 判例检索**:历史卡片+lessons 建 SQLite FTS5 索引,L4 派发前按 票/行业/门型 检索 top-k 判例注入简报(E1 检索式注入的跨票扩展;个股档案已覆盖单票前科)。presence-gated,索引缺=现行为。

## §6 分波与验收

- **Wave A**(本波):§2 reversal_confirm+整编报告 CLI+影子 A/B 接线;§3 gate registry+gate_backtest+接线;§4 两文件+装载链。
- **Wave B**(后排):§5.1 playbook-diff 提案、§5.2 FTS 判例检索。
- 验收件:①整编报告落盘且默认整编案有数据裁决;②gate_backtest 历史重放报告(含错杀审计);③保送票端到端一次真扫描可见(L1 lane 标记→L5 📌 节);④改 scan_config 一处 effort→run meta 回显验证;⑤全程 parity(配置文件全缺=07-10 波行为)。
- **明确不做**:cron 化运维(用户否);网查上界配置化(契约锚)。
