# 漏斗形状 × 门 × 双引擎档位 brainstorm(2026-09-13):召回 / 粗排 / pass1 / 截断与 GATE / effort·模型契约

> **性质**:设计讨论稿,**零实施**(用户原话「brainstorm 先出开发文档不做开发」)。不是调度权威;与 08-29 全覆盖 brainstorm(R-A/R-B/R-C 三路、Q5 未裁)、08-26 现场留存 × BUY 所有权设计稿(E6 v3.0)、09-04 token 两线稿、09-06/09-07 研究可信度稿**互补不重复**,只在本稿改变其优先级处引用。
> **用户五问原文**:① 「召回是不是冗余了 composite 的变现如何 可以合并一些 以及 期权市场的召回 怎么没有」② 「粗排有什么更好的方案吗」③ 「精排 pass1 可以去掉吗 有什么影响」④ 「精排截断最终选出 3–5 只吧 GATE1 和 2 有用吗 可以删除吗」⑤ 「对于每个阶段的 effort 还有模型的选择 目前只有 claude 的 我也会用 codex 跑 这块得兼容」。
> **证据来源**(全部本 session 只读取证,零 LLM):① 09-11 run(`context_claude/scan_runs/20260912T035541989512Z/staging/2026-09-11/`)的 `L1_recall_top1000.csv` / `L2_gbdt_top200.csv` / `_l3_pass1_kept.csv` / `_l3_pass1_meta.json` / `_l3_table.md` / `finalists.csv` / `_stage_timing.json` 逐路计算;② 结果账本 `reports_claude/scan/_ledger/recommendations.csv`(944 行 · 63 run · 06-18→09-09,主尺 `gap_c1_o2` 已回填行)按 lane / guard / rating / e6_buy 聚合;③ 09-11 run 的 `token_usage.md` 按 agent 聚合;④ 前稿读数:08-22 edge 普查、08-24 衍生品普查、08-29 阶段尺首读、08-26 §1.5 composite 席位证据、08-31 双引擎审计、09-12 E7 Codex run 现场。抛弃型脚本 `chan_overlap.py` / `ledger_agg.py` 在本次 session scratchpad,不入库。
> **与 08-29 稿的关系**:用户问①②③ 恰是 08-29 稿 Q5(R-A / R-C)一直没裁的那件事;本稿不重造三路,只把**新量到的数**补上、把推荐收紧到一条,并把「先造尺再裁」的顺序写成分期。

---

## 0. 边界(既有裁定,本稿不重提、全部遵守)

| 裁定 | 对本稿的约束 |
|---|---|
| 主尺 `gap_c1_o2`(T+1 尾盘买 → T+2 开盘卖),5–10 日窗三次裁不换(07-10 / 08-05 / 08-22) | 五问的所有读数只在主尺上;周级尺读数只作「形状」旁证,不作换尺依据 |
| learning 层整体退役且真删(08-21);L4 复用不恢复(07-29);不设收编官 agent | 本稿不重开任何「从历史学 → 回注 prompt」;新仪器**只读、只记不学** |
| `scan_config.jsonc` 唯一参数事实源;新参数三件套(白名单 + 消费点 + 测试锁);引擎隔离只共享 `lake/` | 本稿所有旋钮改动都落 scan_config;Codex 侧现状只引 09-12 稿 E7,**本 session 未读 `reports_codex/`** |
| 期权只进研究/描述阶段,**永不进筛选 / 评级 / regime**(08-28 用户裁定);08-24 衍生品普查三族零正证据 | 问①「期权召回」的答案是结构性的,不立项 |
| **B 类改动冻结到攒够 20 个结果日**(08-26 A0;`tests/scan/test_frozen_b_class_boundary.py` 六条守卫) | 账本自 E6 转正(08-19)只有 **9 个 run / 84 行**,未到 20 → 本稿把每条建议标 **M / I / B**;B 类要么等,要么用户显式豁免(待裁 Q1) |
| **Q-E(2026-09-07)**:确定性 runner **不立项**,「扫描留 workflow.js 做减法」(09-04) | 问④「壳太贵」的解法只能在 workflow.js 内合并壳,不建进程内 runner(重开须用户裁) |
| 「宁缺毋滥、不凑数」(l3-rank 硬约束)/ 性能开关不拥有评级 / 评级只由 rubric 三门定 | finalist cap 是上限不是目标;任何缩减不得反向变成「凑到 N 只」 |
| 「立案时写的诊断,动工一查 4/4 全错」/「绿灯不等于有灯」 | 本稿每个「现状」都附本次量到的数与出处;没量到的写 **[U]** |

---

## 1. 一页现状(全部是本次量到的数)

### 1.1 09-11 run 的漏斗切面(单日,regime=range,落刀 54% vs 全市场 27%,L4 预算 22)

**L1 top1000 的召回来源**(`recall_channels` 按 `|` 拆分;一票可多路):`n_channels` 分布 1 路 **713** / 2 路 231 / 3 路 42 / 4 路 12 / 5 路 1 / 6 路 1 —— **71% 的召回票只有一路把它召进来**。两两 Jaccard 最高 0.20(lowturn∩main_fund),其余 ≤0.16 —— 各路**成员几乎不重叠**。

| 通道 | 在 top1000 | 只此一路 | 独占率 | 活到 L2(200) | 活到 pass1(40) | 进 finalists(11) |
|---|---:|---:|---:|---:|---:|---:|
| composite | 400 | 316 | 79% | 133 | 19 | 4 |
| value | 237 | 149 | 63% | 36 | 7 | 2 |
| reversal | 169 | 80 | 47% | 23 | 8 | 2 |
| main_fund | 121 | 20 | 17% | 13 | 7 | 4 |
| momentum | 101 | 30 | 30% | 7 | 4 | 0 |
| growth | 90 | 40 | 44% | 12 | 3 | 0 |
| heat | 89 | 37 | 42% | 20 | 8 | 4 |
| lowturn | 73 | 22 | 30% | 8 | 8 | 2 |
| healthy | 68 | 19 | 28% | 15 | 8 | 4 |
| reversal_confirm | **12** | **0** | 0% | 1 | 1 | 1 |

(finalists 11 = 6 只 L3 真选 + 2 只 📌 + 3 只 composite 席位;多路票在多行重复计数。)

**只按 composite 排**的反事实覆盖:L1 内 composite top-200 覆盖 L2 **91/202**、top-400 覆盖 **133/202**、pass1 19/40、finalists **4/11**。

**L2 的 202 只里 91 只(45%)是风格桶 floor 救回**(`selection_reason`:lane 46 / backfill 43 / pinned 2 / merit 0);**pass1 留下的 40 只里 27 只是 floor 救回**(L2 名次中位 126);**finalists 11 只里 9 只是 floor 救回,L2 名次中位 141/202**。也就是说:L3 今天挑的票主要来自 L2 的**兜底桶**,不是 L2 的**主排序**。

**pass1**(`pass1.v2`,202→40):lane 轮询 30 / 共振强留 8 / 📌 2;`forced_in` 10。L3 表 40 行候选 = **13.5KB(338 B/行)** + 全行业地形 ≈7KB。

**耗时**(`_stage_timing.json`):L0L1L2 527 s · 策略师 139 s · 行业 brief 114 s · **L3 精排 985 s** · L4 研究 1106 s(11 票并行)· ensemble 134 s · assemble 92 s · 总计 3055 s。

### 1.2 结果账本按家族(944 行 · 63 run · 主尺 `gap_c1_o2` · 已回填行)

| 人口(账本 `lane`) | n | 隔夜均值 | t | 相对市场 | 读法 |
|---|---:|---:|---:|---:|---|
| **pinned**(📌 持仓) | 111 | **+1.2pp** | **+2.28** | +1.2pp | 全表**唯一显著为正**(用户自己的票) |
| healthy | 250 | −0.5 | **−7.15** | −0.3 | 与 08-22 普查同号(L1·healthy −0.37 t −5.58) |
| trend | 171 | −0.5 | −3.10 | −0.2 | 显著负 |
| momentum | 14 | −1.7 | −5.03 | −1.2 | 显著负 |
| value | 30 | −0.3 | −2.03 | −0.1 | 弱负 |
| reversion | 236 | −0.0 | −0.65 | +0.2 | ≈0 |
| composite(席位) | 12 | −0.3 | −0.66 | **+0.3** | 不显著;相对市场略正 |
| lowturn | 8 | −1.3 | −1.65 | −0.8 | 样本薄 |
| **finalist 全体**(剔 📌/席位) | 821 | −0.3 | **−6.42** | −0.1 | 判断层整体显著负(与 08-22 L3·finalist −0.27 t −3.94 同向) |

按卡面评级:Hold 239 −0.2pp · Underweight 134 +0.1pp · Sell 13 **+4.1pp**(t 2.40,n=13,样本薄)· Overweight **2** · Buy **0**。**E6 转正(08-19)以来 84 行:UW 40 / Hold 40 / Sell 3 / OW 0 / Buy 0**;每 run 的 L3 真选 finalist 数 = 6 / 6 / 6 / 5 / 3 / 6 / 7 / 6 / 7。

**E6 相对 BUY 实盘 10 笔**(`e6_buy=true`,含 1 笔重复行):healthy lane 5 / 📌 2 / reversion 1 / composite 席位 1;隔夜:+2.19 / −1.64 / −1.64 / +0.63 / +0.55 / −0.78 / −1.87 / −0.40 / 0.00 / **−1.47(09-09 海博思创,pool=composite 以来唯一一笔)**。

### 1.3 09-11 run 的成本结构(`token_usage.md`,$46.22 · 加权 12.03M · 182 subagent)

| agent | 次数 | 合计 | 占比 | 均次 |
|---|---:|---:|---:|---:|
| **general-purpose 壳**(bash / gate / gpJson / taskGate / trace-control) | **149** | **$16.45** | **35.6%** | $0.11 |
| l4-card(含 1 次 ens_review) | 12 | $10.76 | 23.3% | $0.90 |
| 主会话 | 1 | $7.12 | 15.4% | — |
| l4-intel | 11 | $6.53 | 14.1% | $0.59 |
| l3-rank(主排 + 自修) | 2 | $3.21 | 6.9% | $1.61 |
| sector-brief | 7 | $1.71 | 3.7% | $0.24 |
| macro-brief | 1 | $0.44 | 1.0% | — |

壳的分布:scan-market.js 约 16 条命令壳 + 10 个业务 agent × 2 条 trace-control ≈ 36;其余 ≈113 在 11 个 l4-stock 里,**≈10 壳/票**(preflight / prepare / intel-guard / intel-status / success / stage-result + intel·card 各 2 条 trace-control)。**一张 L4 卡的全口径 ≈ $2.5**(card 0.90 + intel 0.59 + 壳 ≈1.0)。

### 1.4 前稿读数(只引结论,出处在各稿)

- **08-29 阶段尺首读**:L1 Recall@1000 = 0.254,**lift 1.047(CI 0.994–1.101,跨 1)**;L2 keep **lift 0.959(CI 0.893–1.022)**——召回赢家的比例 ≈ 随机 1000/4300,L2 保持赢家略**低于**随机 200/1000。
- **08-22 edge 普查**(隔夜主尺,39–40 日):L1·composite **+0.14pp t 3.05**(唯一正证据;30/39 日在权重样本内,OOS 9 日 +0.18 t 2.90 按规则「样本不足」);L1·全体 −0.00;L2·全体 +0.06(未证);healthy −0.37(t −5.58)、main_fund −0.22(t −4.07)、momentum −0.22(t −2.05)显著负;value −0.04 / reversal +0.02 / growth +0.03 / heat −0.13 未证;L3·finalist −0.27(t −3.94);**被 pass1 切掉的影子 +0.06 反而好于留下的 −0.16(n=11)**。
- **08-26 §1.5**:L2·composite·top20 +0.14(t 1.96)、L0·composite·top50 +0.17(t 2.78)—— composite 前 20–50 名是隔夜尺上唯一稳定为正的选票口径,量级 ≈ 一次往返成本。
- **08-24 衍生品普查**:QVIX 18 格 / PCR 36 格 / 到期日历 6 格 **0 正证据**;唯一正证据股指基差 IC/IM,但换 close 口径即不成立、近两年衰减。**08-03 稿 §2**:交易所产品集里**没有全 A 个股期权**,只有 ETF/指数期权;可转债 F3 是唯一个股级候选,capability gate `derivatives/cb_gate.py` 现状 **`BLOCKED_BY_DATA`**(`cb_basic.conv_price` 是当前截面,重算历史转股价值泄漏未来调整;强赎须 `cb_call`/公告)。
- **08-31 双引擎审计 §1.4 + 09-12 E7**:`.claude/agents/*.md` 10 份**零 Codex 等价物**;Codex 主会话自演全部角色(AGENTS.md 第 3 条);Codex 09-08 run:**零 `AGENT_*` 事件**、`token_usage.md` 写「无 transcript」、`run_contract.session_ref=None`;Codex 原生已有 subagent(`.codex/agents/*.toml`)与 `codex exec --output-schema`;Codex `web_search` 默认 `cached` 非实时。

---

## 2. 问①:召回是不是冗余了?composite 变现如何?能合并吗?期权召回为什么没有?

### 2.1 先把「冗余」拆成三个不同的问题

| 问的是 | 读数 | 结论 |
|---|---|---|
| **成员重叠**(几路召回同一批票) | 71% 单路召回;两两 Jaccard ≤0.20 | **不冗余**——各路召的是不同的票 |
| **增量赢家**(多一路多捞多少事后赢家) | Recall@1000 lift 1.047(CI 跨 1);composite 一路 top-400 已覆盖 L2 66%、pass1 48% | 十路一起 ≈ 随机;composite 之外的九路合起来**没有可测的增量赢家** |
| **决策尺上的 edge**(召回来的票隔夜是否更好) | 普查:只有 composite +0.14 正;healthy / main_fund / momentum 显著负;账本 lane 同号 | 九路里三路**主动召负票**,其余 ≈0 |

所以答案是:**通道不冗余,是无效**。「合并几路」解决的是重叠问题——而重叠不是病;真病是「除 composite 外没有一路在主尺上有信号」。把 value 与 growth 合并成一路,得到的仍是一路没有信号的通道。

### 2.2 composite 的「变现」:三层读数,越靠近钱越差

| 层 | 读数 | 出处 |
|---|---|---|
| 普查(召回口径,不可交易假设) | +0.14pp,t 3.05;OOS 9 日 +0.18(样本不足) | 08-22 |
| 席位实盘(守卫⑨ 强制进 finalists,08-26 起) | n=12,隔夜 −0.3pp(t −0.66),相对市场 **+0.3pp**(不显著) | 账本 §1.2 |
| 作为 BUY 池(E6 `pool=composite`) | **1 笔**:09-09 海博思创 −1.47pp | 账本 §1.2 |

读法:composite 是**唯一不为负的确定性选择器**,量级 ≈ 一次往返成本——它有资格做**菜单排序的主键**(挑出「不比市场差」的候选),没有资格做买单理由;这一点 08-26 稿写「期望 ≈ 成本、不承诺赚钱」时已说清,本次账本没有推翻也没有加强它(n=12)。**它今天的真实用途就是排序键**(L2 sn-composite、pass1 队列、席位),不必赋予更多。

### 2.3 期权召回为什么没有(三层原因,任一层都够)

1. **结构性**:A 股交易所产品集只有 ETF/指数期权(上交所 50/300/500/科创 50 ETF、深交所 ETF、中金所股指期权),**没有全 A 个股期权**——「用期权做个股召回」没有数据对象(08-03 稿 §2,官方规则页核验)。
2. **市场级也被证伪**:08-24 普查把能拿到的全部信号(QVIX 水平/急升/VRP、12 品种 PCR 三口径、到期日历三旗)放在同一把尺上,**60 格 0 正证据**;唯一的股指基差信号对期货价格口径不稳健、近两年衰减。
3. **裁定**:08-28 用户裁定「期权只进研究/描述阶段,永不进筛选/评级/regime」。

唯一个股级的衍生品邻近候选是**可转债**(F3):它不是期权替身,而是独立信号(CB return / underlying return / premium change 三分解),现状 `cb_gate.py` = `BLOCKED_BY_DATA`。解锁条件写在门里:历史转股价(非当前截面)+ `cb_call` 强赎/到期状态 + 新债/低流动性/妖债预注册过滤。**没有这三样,F3 不能进普查,更不能进召回**。本稿不立项,只把解锁条件抄进待裁 Q3 供用户决定要不要去要权限。

### 2.4 三条路(沿用 08-29 的编号,不重造)

| 路 | 做什么 | 类别 | 本稿读数后的判断 |
|---|---|---|---|
| **R-B · 只修死腿** | `reversal_confirm` 摘出启用集(09-11:12/1000、独占 0、A/B 无裁判);`heat` 补门或降 quota;`recall_channels` 缺省 = 当前启用集而非全部 14 路;`accumulation` 的 composite +5 bonus 明示或摘;`north`/`rz` 死腿修或摘 | M(摘通道属 B) | 无争议,现在就该做;但「摘 reversal_confirm」是通道变更,按冻结规则属 B,须 Q1/Q3 裁 |
| **R-C · composite-first** | L1 = composite top-1000(权重仍 regime-aware `weights.json`);其余九路**降为旗列**(`recall_channels` 只留 composite,各路 `score_*` 列照算照落盘,L3 表照旧能看到「value 低估 / lowturn 亮旗」);多样性只由 L2 的 floor 保(floor 值由 §3 的尺定) | **B** | **推荐为默认假设**:它与全部三条读数一致(唯一正家族、其余无增量赢家、三路显著负);代价是**放弃「多样性来自召回」这个假设**——而这个假设从未量到过正证据(lift 1.047) |
| **R-A · 五族 + 贡献配额** | 14 路 → 5 族,quota 由 `unique_winner_capture_per_seat` 定,L2 floors 从族表派生 | B(重) | 只有当 §2.5 的尺量到**某族的独占赢家显著 >0** 时才值得做;09-11 切面 composite 之外独占率最高的 value(63%)在普查上 −0.04,没有理由先做重的 |

**推荐顺序:先造尺(I 类,冻结窗内可做)→ 20 日反事实读数 → 在 R-C 与 R-A 之间裁。** 不建议在没有尺的情况下直接切 R-C——08-29 稿已指出通道级取证渠道(`channel_ledger` / `channel_audit`)随闭环退役全部消失,今天**无尺可裁**;09-11 的切面只有一天,不够裁。

### 2.5 尺:`research/recall_lab.py`(08-29 §3.5 提过、从没建;本稿把它定成 P0)

- **输入**:冻结的 `L1_scored_full.csv`(各路 `score_*` 列都持久化了,可按备选 `recall_channels` / quota 重算 top-1000 与 L2 top-200,不重取数)+ 湖前向收益(`factor_lab` 面板现算,与 `lowturn_precheck` 同一条腿,**不依赖任何已退役账本**)。
- **三把尺,同一人口**:① `Recall@1000 lift`(主尺 top-decile 赢家 ∧ `buyable_c1`,分母扩到 L0 硬门外的湖全体——修 08-29 R-D7 的盲区);② `unique_winner_capture_per_seat`(滚动 40 日「只被该路召回的赢家数 / 该路席位」);③ `L2 keep lift`。同时报 `winner_econ`(赢家 ∧ 净隔夜 >0,带 `assumed_cost_bps`)防止深熊日把「跌得少」叫赢家。
- **变体**(预注册,读完不许加):`current`(10 路)/ `R-C`(composite only + 现 floors)/ `R-C-nofloor` / `R-B`(现 10 路减 reversal_confirm)/ `R-A-5族`(用 08-29 §3.5 的族表)。
- **只读只写 scratch / `$RPT/research/`**,不碰 staging;每日一行影子读数,20 个数据日后出读数稿。
- **验收**:变异探针——把某一路的 `score_*` 列置零,该路的 `unique_winner_capture` 必须变 0(「自动学习的腿必须有一个会变的量」同款配方)。

### 2.6 影响 / 回滚 / 不做

- R-C 上线后 L1 的**名单**会大变(09-11 切面:composite top-1000 与现 top-1000 的交集只有 400),L2 桶、pass1、L3 表、席位的**代码零改**(它们读的是列不是通道);行业 brief 选择器(L2 集中度 top3)会随之变。
- 回滚杆:`funnel.recall_channels` 改回 10 路(一行,逐字 parity)。
- **不做**:不合并通道(解决的不是病);不做期权/ETF 期权召回;不重开 `event` 通道(入场纪律 pr_20260725_001);不改 `weights.json` 的标定方式。

---

## 3. 问②:粗排有更好的方案吗?

### 3.1 现状的病,按读数

| 症状 | 读数 | 含义 |
|---|---|---|
| L2 保持赢家**低于随机** | keep lift 0.959(CI 0.893–1.022) | 分层采样在「不排除赢家」这件事上比随机差一点 |
| 菜单一半是兜底桶 | 45% floor 救回;finalists **9/11** 来自 floor 救回、L2 名次中位 141/202 | L3 看的实际是**桶**,不是 sn-composite 主排序;而桶里最大的两桶(健康 15 / 趋势 20)在主尺上显著负 |
| 菜单比市场更落刀 | 09-11:54% vs 27%(L4 预算因此 30→22) | 「落刀」既不是赢家特征也不是 L3 想要的(硬约束 B 直接弃) |
| 行业口径错 | `industry` 是东财细标签(全帧 129 个),`sector_cap` 20% 从不触发,sn-composite 在几只票的小组内去均值 | 08-26 稿已把口径说对但没改判据 |

**「L2 上模型」不重开**:全 zoo OOS rank-IC 为负、回测 ≈0(STAGES「已被实证否决」),本稿没有新证据。

### 3.2 三条路

| 路 | 做什么 | 类别 | 判断 |
|---|---|---|---|
| **A · 修输入,不动采样器** | ① sn-composite 去均值与 `sector_cap` 改用**申万一级**(`common/sw_sector_map.py` 的粗类现成);② 加 L2 硬门「落刀不接」= `pct_60d < −35 ∧ 主力 ≤0`(谓词已在 `l3.lowturn.knife_pct_60d`,只是没人在 L2 用);③ floors 按普查改:健康 15→0、趋势 20→8(主尺显著负的桶不再兜底),价值/反转/吸筹保留(≈0 不负) | ①② M/I(口径与门)、③ B | **推荐先做 ①②**:直接治 54% 落刀与「20% 帽从不动」,不改「菜单」的世界观;③ 与 R-C 一起由 recall_lab 裁 |
| **B · composite-first 菜单**(R-C 的 L2 版) | L2 = sn-composite top-200,只留申万一级 cap 20% + recall_lab 证明有独占赢家的桶;文件名/列一个不动(`menu` / `sector.pack` / 席位 / replay 四个消费者零改) | B | 若 R-C 在 L1 上线,L2 自然退化成这条;单独先做没意义 |
| **C · 合并 L2 与 pass1** | 1000→40 一段采样直出,200 只作观测产物 | B + 结构 | **不推**:省的是一段 CSV,代价是 `menu.py` / `sector/pack.py` / `pick_composite_seats` / replay 单元 `l2` 四处消费者改口径,而 pass1 的规则问题(§4)并不因合并而消失 |

### 3.3 验收尺(与 §2.5 同一把仪器,加两个读数)

- `L2 keep lift` 的 CI 下界 ≥1(现 0.893);
- 菜单落刀率 / 全市场落刀率 ≤1.3(现 2.0);
- finalists 中 floor 救回占比从 82% 下降(它是「L3 到底在选谁」的直接读数);
- 行业口径改申万一级后 `sector_cap` 的年触发日数 >0(现 0)。

回滚杆:①② 各一行开关(`l2.sector_level: "em"|"sw1"`、`l2.knife_gate: false`);③ 改回 `DEFAULT_FLOORS` 常量。

---

## 4. 问③:精排 pass1 可以去掉吗?有什么影响?

### 4.1 pass1 今天是什么

`l3/triage.triage_l2_for_l3`(`pass1.v2`):**第三段确定性采样**——📌 全入 → composite 席位 ①b 强留 → 多路共振按 composite 取前 5 → lowturn 强留 ≤8 → **各通道轮询**填到 40。09-11:lane 轮询 30 / 共振 8 / 📌 2。它做了两件事:**省 L3 的输入**(40 行 13.5KB vs 202 行 ≈68KB)和**执行保护席**(让 l3-rank 真判到 📌 / 席位 / lowturn 票,否则 finalists 里只剩空 thesis 行——08-26 稿踩过)。

它**没有**被证明的事:选得比切掉的好。08-22 普查:被 pass1 切掉的影子 +0.06 反而好于留下的 −0.16(n=11);09-11 切面:留下的 40 只里 27 只是 L2 floor 救回——pass1 的轮询把 L2 兜底桶的形状**原样放大**进了 L3。

### 4.2 「去掉」= `l3.two_pass:false`(回滚杆现成),影响逐项

| 影响 | 估计 | 依据 |
|---|---|---|
| l3-rank 输入 | 13.5KB → ≈68KB 候选行(+7KB 地形),**≈5×** | 338 B/行 × 202 |
| l3-rank 成本 | $2.87 → **估 $8–12** | cache 写主导;输出侧 judged 仍只写 20–28 只,不随行数涨 |
| 墙钟 | 985 s → **估 30–50 min** | 60 行时代实测 14–25 min(workflow 日志);200 行未测 **[U]** |
| 判断质量 | **退化风险高**:比较式精排在 200 行上退化为逐只打分;07-18 把 60→40 的理由正是「60 行 mandatory 0 漏、delta 无赢家富集」 | agent def 自述「~60 只 holistic 比较是判断核心」 |
| 保护席 | ①b / ③b 失去执行点;merge 守卫仍把席位塞进 finalists,但 **l3-rank 可能没判它们**→ 空 thesis 行 | 08-26 A1 实施注 |
| 仪器 | `_l3_pass1_cut.csv` 影子与 08-29 §3.4 的 `pass1_keep_lift` 尺消失 | — |

结论:**去掉的收益是「不再被 pass1 的形状绑架」,代价是 3–4× 的 L3 成本 + 质量退化风险 + 保护席要另找执行点**。而「形状绑架」有更便宜的解法:换规则。

### 4.3 三条路

| 路 | 做什么 | 类别 | 判断 |
|---|---|---|---|
| **① 留 pass1,换规则** | 去掉「各通道轮询」,改为 **composite 序 top-N + 保护席**(📌 / 席位 / lowturn ≤8 照旧);L3 表按 composite 分块而非 lane 分块 | B | **推荐**:pass1 的存在理由(省输入 + 执行保护席)都保住,选票规则换成唯一有证据的键;与 R-C 一致,不依赖 R-C 先上 |
| **② 缩 target** | `pass1_target` 40 → **25**,与问④的 finalist 5 联动(5 席从 25 里挑,比例不变) | B(一行) | 推荐与 ① 同批;单独做也省 L3 ≈35% 输入 |
| **③ 去掉** | `two_pass:false` | B(一行) | 不推;若用户坚持,先做**一次同日对照**(同一天 40 行与 202 行各跑一次 l3-rank,比 finalists 重叠 / conviction 分布 / 成本 / 墙钟)再定,不盲切 |

验收:① 的变异探针 = 把 composite 列打乱,pass1 kept 集必须变(现在轮询下打乱 composite 几乎不变 kept——这正是「pass1 不吃 composite」的证据);②③ 看 L3 成本与 finalists 重叠率。回滚:`l3.pass1_rule: "round_robin"|"composite"` 一行,`pass1_target` 一行。

---

## 5. 问④:精排截断改 3–5 只?GATE1 / GATE2 有用吗、能删吗?

### 5.1 finalist 3–5 只:可以,而且几乎不丢东西

- **判断层的产出是拒绝,不是挑选**:E6 转正以来 84 行 **≥OW = 0、Buy = 0**;BUY 的所有权在 composite 席位(08-26 A1),不在 finalists。finalists 的信息价值 = 「L4 对它们说了不」,而拒绝的价值在 L4 而不在 L3 多选几只。
- **实际已经在 3–7**:E6 以来每 run L3 真选 finalist 数 6/6/6/5/3/6/7/6/7(cap 10 只在好日子咬到);`lt55` / 宁缺毋滥已经把数量压下来了。
- **省多少**:一张卡全口径 ≈ $2.5(§1.3)。cap 10→5 在 09-11 恰省 1 卡(6→5),典型日省 1–2 卡 ≈ **$2.5–5/run(5–10%)**。诚实地说:这不是主要成本杆(见 5.3)。
- **联动改动**(不改则 5 席被守卫互相挤空):`l3.finalist_max` 10→5;守卫⑤ `trend_quota` soft 2→1、⑥ lowturn 1 保留、⑧ `sector_cap` 3→2;`pass1_target` 40→25(§4.3 ②);`menu.l4_budget` 的 30/22/15 档对「5 + 3 席位 + 📌≤5」已无约束力,`base` 改 10 或干脆只留旗不留数(代码持有,顺手改)。**l3-rank 的「7–10 只」措辞与 agent def 锚一起改**(`tests/test_agent_defs.py` 锁着)。
- **不改的**:「宁缺毋滥」——cap 是上限;席位与 📌 不占名额的语义不变;E6 硬门不变。
- 类别:**B**(改 finalist 数量 = 行为变更)。回滚:三个数字各一行。

### 5.2 GATE1 / GATE2:检查有用、几乎免费;删掉的是壳不是门

| 门 | 检查什么 | 抓过什么 | 今天的成本 |
|---|---|---|---|
| GATE1(`gates.gate1`) | `L2_gbdt_top200.csv` 在 · 非空 · 代码 6 位;顺带回 `sentinel_level` + `l4_budget`(NaN 直接 fail fast) | universe 半途失败(ChunkedEncodingError)→ 不带空 L2 进 L3;前导零家族(3 个修复提交 `b48bd18` / `6c27bc2` / `ba5b417`,同族三次复发);07-30 `l4_budget=NaN` 污染 L3 prompt | **3 个壳**:`l2-check`(gate)+ GATE1(stageGate)+ `run-mode`(gpJson)≈ $0.35 |
| GATE2(`gates.gate2`) | `finalists.csv` 在 · 非空 · 6 位 · count≤budget(exempt lane 不占名额);回显 name/sector 供 CP3 与 intel 盲搜 | 满员日 +📌 触发的 GATE2 假失败(修在 gate 自身,C-1) | **0 个额外壳**:与 `l3_select finalists` 合在同一个 stageGate 里(壳合并②) |

所以:**删检查 = 重开一个已复发三次的 bug 家族,收益 ≈ 0**;**删壳才有钱,而 GATE2 的壳本来就不存在**。推荐:GATE1 的三壳合一——`prelude` 壳尾追加 `stage_result show gate1` 与 `run_mode --decide`(判据不动,少 2 次 spawn ≈ $0.25/run);GATE2 不动。类别 M。

### 5.3 真正的钱在 149 个壳(35.6%,比全部 L4 卡还贵)

- 一票 ≈10 壳:preflight / prepare / intel-guard / intel-status / success / stage-result 6 条命令壳 + intel·card 各 2 条 trace-control 边界壳(09-03 已从「每壳一对」减到「业务 agent 才发」,一票 18→8 次 agent 调用是那次的读数;09-11 实测仍 ≈10)。
- Q-E(09-07)裁「runner 不立项、workflow.js 做减法」,本稿在这条裁定内给减法:
  - **M-1 命令壳合并**(纯搬运,判据不动):l4-stock 里 `preflight+prepare` 合一、`intel-guard+intel-status` 合一、`success+stage-result` 合一 → 6→3;scan-market.js 的 16 条按 phase 合并成 ≈6(`frame+pack-check+strategist-pack-check`、`l2-check+GATE1+run-mode`、`sector-reuse+pack+list` 已合、`l3-prepare+lint`、`l4-prep+dispatch-plan+tasks-init`)。**估 −60 壳 ≈ −$6.5/run**。
  - **I-1 边界事件改派生**(08-29 A5 最小版):`AGENT_DISPATCHED` 由 python 侧在写任务包/派发前落(`DISPATCH_INTENT`),`AGENT_COMPLETED/FAILED` 由 transcript adapter 从真实 transcript 派生——不再为每个业务 agent 派两条 trace-control 壳。**估 −40 壳 ≈ −$4.5/run**。前提:09-12 已上线的 `transcript_binder` 期望集是「AGENT 事件 / TASK 事件 / 产物回退三路合并」,改派生后 AGENT 事件不再是第一路,**分母质量标记与 `completeness` 判据要同步改**——这一条须先读 `agent_expectations` 的合并逻辑再定 **[U]**。
  - 合计目标 **149 → ≈50 壳,≈ −$11/run(−24%)**,不改任何评级/门/主尺。

### 5.4 影响 / 验收 / 回滚

- 验收:一次真跑的 `token_usage.md` 里 general-purpose 行数 ≤60;`usage_reconcile` 的壳集合断言同步改;capsule `completeness_ok` 在 I-1 后仍能对 11 票全绿(变异:删一份 transcript → 红)。
- 回滚:M-1 是 JS 结构改动,按 commit 回滚;I-1 留 `retention.agent_events: "relay"|"derived"` 一行。

---

## 6. 问⑤:每个阶段的 effort 与模型;Claude / Codex 双引擎兼容

### 6.1 现状(读码 + 前稿)

| 件 | 现状 | 问题 |
|---|---|---|
| `scan_config.jsonc` `agents` 块 | 10 role 闭集(`user_config._AGENT_ROLES`),值 = `{model?, effort}`;`_MODELS={haiku,sonnet,opus}`、`_EFFORTS={low,medium,high,xhigh,max}` | **词表是 Claude 的**;Codex 的 `gpt-5.6-sol` / `reasoning_effort` 写进去 load 即 raise |
| 判断类 role 的 model | 故意不写,落 `.claude/agents/*.md` frontmatter(opus / sonnet) | frontmatter 是 Claude harness 文件;Codex 没有等价物(09-08 run 零 AGENT 事件) |
| `resolve_agent_config` → `_resolved_agent_config.json` | 只解释一次、三个 workflow `AG()` 照它派发 | 不带 `engine`;Codex 会话没有派发点,resolved 无消费者 |
| Codex 执行形态 | AGENTS.md 第 3 条:主会话**自演**全部角色;全局 `model_reasoning_effort=xhigh`(`~/.codex/config.toml`) | **逐 role 的 effort 在 Codex 上根本没生效过**;情报员「结构性盲」退化为指令级自律(08-31 §1.4) |
| 计量 | `trace/pricing.py` 只有 Claude 价目;Codex adapter 能从 rollout `turn_context` 读 model/effort | Codex 成本恒「未定价」;`usage_reconcile` 逐 role 对账在 Codex 上无期望可比 |
| 契约 | 08-31 D9 已提「契约生成两侧(def 机器块 + Codex 角色文件)、任务包是唯一跨引擎接口」 | 零实施 |

### 6.2 设计:档位(tier)是引擎中立的,模型/effort 是引擎映射

**一份声明,两层解释**:

```jsonc
"agents": {                       // role → tier(引擎中立;闭集不变,只是值变了)
  "strategist":   { "tier": "writer" },
  "sector_brief": { "tier": "writer" },
  "l3_rank":      { "tier": "core" },
  "l3_repair":    { "tier": "judge_lite" },
  "l4_intel":     { "tier": "collector" },
  "l4_card":      { "tier": "core" },
  "ens_review":   { "tier": "core" },
  "dossier_init": { "tier": "core" },
  "gp_shell":     { "tier": "relay" },
  "gp_shell_json":{ "tier": "relay" }
},
"engines": {                      // tier → 引擎具体档位;每引擎各自的词表校验
  "claude": {
    "core":       { "model": "opus",   "effort": "max"   },
    "judge_lite": { "model": "opus",   "effort": "medium"},
    "writer":     { "model": "opus",   "effort": "high"  },   // 现 strategist max / sector_brief xhigh,见 6.4
    "collector":  { "model": "sonnet", "effort": "max"   },
    "relay":      { "model": "sonnet", "effort": "low"   }
  },
  "codex": {
    "core":       { "model": "gpt-5.6-sol", "reasoning_effort": "xhigh" },
    "judge_lite": { "model": "gpt-5.6-sol", "reasoning_effort": "low"   },
    "writer":     { "model": "gpt-5.6-sol", "reasoning_effort": "medium"},
    "collector":  { "model": "gpt-5.6-sol", "reasoning_effort": "medium", "web_search": "live" },
    "relay":      null                                              // Codex 无壳:确定性命令进程内执行
  }
}
```

- **role 级 override 仍允许**(`agents.l4_card.claude: {effort: "xhigh"}`)但必须落在引擎子键下,不再有裸 `model/effort`——裸键即旧格式,load 时给出一次性迁移提示后 raise(防「两份事实源」)。
- `user_config.resolve_agent_config(cfg, engine=…)` 输出 `_resolved_agent_config.json = {engine, roles: {role: {tier, model, effort|reasoning_effort, ...}}}`;`identity` 快照与 `run_contract` 记 `engine` + resolved 的 sha(**同一 run 的档位从此可回答「当时按什么档跑的」**)。
- 词表校验按引擎:`_ENGINE_VOCAB = {claude: {models, efforts}, codex: {models: [从 config 读的 allowlist], reasoning_efforts: [minimal, low, medium, high, xhigh]}}`——Codex 的合法值先按官方文档钉,**本机版本认哪些值须探针一次 [U]**。
- Claude 侧派发不变:三个 workflow 的 `AG(role)` 仍读 resolved(它只是从 `{model,effort}` 变成 `roles[role]` 的投影);`AGENT_DEFAULTS` 兜底表继续存在,`tests/test_agent_defs.py` 的 AST 相等断言改成对 `engines.claude` 的相等断言。
- **契约生成两侧**(D9 落地的最小面):`contracts.emit --write` 从同一份角色声明生成 `.claude/agents/<role>.md` 的机器块(现有)**和** `.codex/agents/<role>.toml`(name / description / developer_instructions = agent def 正文;model/effort 若 Codex 的 agent toml 支持则写,不支持则留在 resolved 里由叶子读 **[U]**)。这样「情报员没有 Read」这类结构性盲在 Codex 上也能成为工具集约束而不是叮嘱。

### 6.3 Codex 叶子的三种形态(逐步,不一步到位)

| 形态 | 现在能做什么 | 类别 |
|---|---|---|
| **(i) 单会话自演 + 对账**(现状 + 计量) | resolved 只作**期望**;`usage_reconcile --engine codex` 用 09-12 已上线的 rollout ordinal 区段绑定,把每个 role 区段的实测 model/effort(adapter 已能读)与期望对比,`ok=false` 进 CP7;`trace/pricing.py` 加 Codex 价目(来源与生效日随行,量不到仍写 UNMEASURED) | **I / M**,冻结窗内可做,先把「Codex 到底按什么档跑的」变成可测 |
| **(ii) 原生 subagent**(`.codex/agents/<role>.toml` 由 emit 生成) | Codex 主会话按 SKILL 顺序 spawn 各 role;结构性盲与并发帽在 Codex 上成立;AGENT 事件由 adapter 派生(与 §5.3 I-1 同一机制) | I → B(改变 Codex 侧执行结构) |
| **(iii) `codex exec --output-schema` 任务包叶子**(08-31 D9) | 每张 L4 卡 = 一次 `codex exec` 读 `_l4_prompt_<code>.md` 写卡 + JSON;编排回到确定性 Python——但这与 Q-E「runner 不立项」相冲,须重裁 | B + 裁定 |

推荐:**(i) 现在做;(ii) 在 Codex 真跑 scan 前做;(iii) 不在本稿立项**。

### 6.4 档位本身:现在不动,先结构后档位

- 成本里 writer(sector-brief $1.71 + macro $0.44 = 4.7%)与 collector(l4-intel 14.1%)都不是主要杆;core(l3-rank + l4-card ≈30%)是判断核心,降档没有证据支持。**壳(35.6%)的问题是数量不是档位**(§5.3)。
- 唯一值得开一个影子的:`l4_intel` sonnet·max → high(它是采集不是判断;max 的额外推理花在「查什么」上,能否降档只有网查覆盖率 / 事件行数对照能答)。B 类,20 日对照后裁,不在本稿立项。
- `writer` 现值(strategist max / sector_brief xhigh)是 07-12 用户拍板,本稿**不建议改**,6.2 示例里的 `high` 只是 tier 缺省值,迁移时把现值原样写进 role override 保 parity。

### 6.5 验收 / 回滚

- 验收:① 旧格式 `agents.l4_card.effort` 直接 load → 迁移提示 + raise(测试锁);② `resolve_agent_config(engine="codex")` 在 `engines.codex` 缺块时 raise 而不是静默退 Claude 值;③ Codex 一次真跑后 `token_usage.md` 有定价、`_usage_reconcile.json` 逐 role 有实测 model/effort;④ 变异:把 `engines.claude.core.effort` 改 low,`_resolved_agent_config.json` 与 workflow 派发参数同步变(现有 AST 锁的替代)。
- 回滚:`user_config` 保留旧格式读取器一个版本(带迁移提示),`engines` 块缺省 = 现值。

---

## 7. 分期(P0 → P2,依赖在前)

| 期 | 内容 | 类别 | 依赖 / 门槛 |
|---|---|---|---|
| **P0 · 冻结窗内就能做(不改行为)** | ① `research/recall_lab.py`(§2.5,三尺五变体,每日影子行);② 双引擎档位契约(§6.2 声明 + `resolve_agent_config(engine)` + resolved 记 engine + Codex 价目 + `usage_reconcile --engine codex` 读实测);③ 壳减法 M-1(§5.3 命令壳合并)+ GATE1 三壳合一(§5.2);④ L2 口径:申万一级 + `sector_cap` 触发计数进 `menu_health`(先只报数,不改判据) | I / M | 无;③④ 各带回滚 |
| **P1 · 20 个数据日读数后(recall_lab)** | 裁 R-C / R-A / 维持(待裁 Q2);裁 L2 floors 派生(§3.2 A③);裁 `reversal_confirm` 去留(Q3) | B | recall_lab ≥20 日;冻结解除或豁免(Q1) |
| **P2 · 与 P1 同批或独立(不依赖读数,只依赖裁定)** | finalist_max 5 + 守卫联动(§5.1);pass1 换规则 + target 25(§4.3);L2 落刀硬门(§3.2 A②);边界事件改派生 I-1(§5.3,先核 binder 分母逻辑) | B(前三)/ I(I-1) | 裁定 Q5/Q6;I-1 须 [U] 核完 |

**每期一句回滚**:P0 全部一行开关或 commit 回滚;P1 = `recall_channels` / `DEFAULT_FLOORS` 改回;P2 = 三个数字 + `pass1_rule` + `knife_gate` 各一行。

---

## 8. 待裁(Q1–Q10;每条给推荐与「不裁的后果」)

| # | 问题 | 推荐 | 不裁的后果 |
|---|---|---|---|
| Q1 | B 类冻结(08-26 A0,攒 20 结果日)现只有 9 run / 84 行:等到 20 日、还是对本稿 P2 显式豁免? | **P0 不需要裁;P2 等 P1 读数同批裁**(finalist 5 / pass1 25 这类纯数量收缩若要先做,单独豁免) | P2 一直做不了,壳减法与仪器照做 |
| Q2 | 召回整编终选:R-C(composite-first)/ R-A(五族)/ 维持? | **R-C 为默认假设,recall_lab 20 日后裁**;R-A 只在某族独占赢家显著 >0 时 | 十路继续各干各的;09-11 切面式的「L3 在兜底桶里选」延续 |
| Q3 | `reversal_confirm` 是否立即摘出启用集(12/1000、独占 0、A/B 无裁判)?F3 转债是否去要 `cb_call`/历史转股价权限? | 摘出(一行);F3 **不去要**,除非用户想开衍生品线 | 通道名义活着、白占 quota 150 |
| Q4 | L2:先做申万一级 + 落刀硬门(§3.2 A①②)/ 直接 composite-first / 不动? | **先 ①②**(口径与门),floors 与 composite-first 随 Q2 | 54% 落刀与 20% 帽从不触发延续 |
| Q5 | pass1:换 composite 规则 + 缩 25 / 去掉 / 不动? | **换规则 + 缩 25**;去掉须先同日对照 | L3 输入形状继续 = L2 兜底桶形状 |
| Q6 | finalist_max 5 与守卫联动(trend 2→1、sector 3→2、pass1 25、`l4_budget` base)? | **是**,四个数字同批 | cap 10 只在好日子咬到,变化不大 |
| Q7 | GATE1 三壳合一(判据不动)?GATE2 不动? | **是 / 是**;不删任何检查 | 多花 ≈$0.25/run |
| Q8 | 壳减法范围:只做命令壳合并 M-1 / 连边界事件改派生 I-1? | **M-1 现在;I-1 核完 binder 分母逻辑再做** | 149 壳 $16/run 延续,是全场最大单项 |
| Q9 | Codex 叶子形态:单会话自演 + 对账(i)/ 原生 subagent(ii)/ exec 叶子(iii,撞 Q-E)? | **(i) 现在,(ii) Codex 真跑 scan 前,(iii) 不立项** | Codex 的 role 档位继续「不存在」,成本继续未定价 |
| Q10 | effort 档位是否现在调(如 l4_intel max→high)? | **不调**;先结构(壳)后档位;intel 降档只做 20 日影子 | — |

---

## 9. 不做什么 / 诚实局限

- **不做**:换主尺;L2 上模型;重开 learning 回注;期权 / ETF 期权召回;合并通道;进程内 runner(Q-E);`event` 通道重开;改 `weights.json` 标定方式;改 rubric 三门与早停。
- **读数的边界**:§1.1 是**一个 run(09-11,range regime)的切面**,只能说明形状不能裁决;§1.2 账本 63 run 跨 06-18→09-09 但 E6 转正后仅 9 run,且多个 regime 混在一起、未扣成本、`Sell` 行 n=13 不可读作结论;§1.3 成本只量了 Claude 引擎的一个 run(壳数 149 与 09-03 探针的「一票 8 次」不一致,本稿按实测写,差异来源未查 **[U]**)。
- **Codex 侧**:本 session 按引擎隔离**未读** `reports_codex/` / `context_codex/`;现状全部引 08-31 §1.4 与 09-12 E7,如已有变化以 Codex 会话自查为准。
- **[U] 清单**(动工前必核):Codex 本机版本的 `reasoning_effort` 合法值与 agent toml 是否支持逐 agent model/effort;200 行 L3 表的真实成本与墙钟;I-1 改派生后 `transcript_binder` 期望集的分母质量;壳数 149 vs「一票 8 次」的差异来源;`heat` 通道补门的具体谓词(08-29 只说「无门」)。

仅供研究,非投资建议。
