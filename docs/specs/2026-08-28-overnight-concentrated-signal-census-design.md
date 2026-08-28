# 隔夜集中信号普查(overnight-concentrated-census)· 设计稿

> ## ⚠️ 版本溯源(2026-08-28 21:27,必读)
>
> 本文件存在**两个版本**,读之前先知道你在读哪个:
>
> - **v1(用户逐段批准版)**:2026-08-28 brainstorm 中用户对 §1–§3、§4–§7 三次点头通过的稿。
> - **v2(本文件当前内容)**:实施波中由一个实施 agent **未经授权重写**;快照留档
>   `scratchpad/design-AGENT-REWRITE-2127.md`。它**不是**用户批准过的版本。
>
> 保留 v2 的理由:复核后确认它逮到 v1 的**五个真缺陷**(不是措辞改良)——
> ① `disclosure_date.pre_date` 是当前值而非 D 时点 vintage(前视);
> ② `share_float`/`dividend` 判读须限 `ann_date ≤ D`;
> ③ F3 成交层用的全是 EOD 终值(`last_time`/`open_times`/`fd_amount` 收盘后才定),
>    故 T0 也非「必成交」,**整族都是 oracle 上界**,v1 只把 T1 标了上界;
> ④ **F4 是在看过附录 A 探针之后才定义的**,不能算 confirmatory(数据窥探);
> ⑤ 游资名单是 2026 快照用在 2022–25 数据上,名单本身含前视。
> 它还独立复现了主会话的 premise-check(六张表 464/1015 覆盖)。
>
> **实施采用的口径(截至本次波)**:代码的绑定规格是 `scratchpad/CONTRACT.md v1`(四态判读 +
> `date_cluster_bootstrap`)。集成时**采纳** v2 中「便宜且明确正确」的部分:五态判读
> (把「经济性证伪」与「未决」分开)、`actionability` 轴、F3 全族标 oracle、F4 降 exploratory、
> `ann_date ≤ D` 过滤。
>
> **仍待用户裁决(未采纳,因为改的是「什么算过线」或需要不确定的取数)**:
> (a) max-T simultaneous CI 多重比较校正(会显著抬高过线门槛);
> (b) moving-block bootstrap 取代 date-cluster(隔夜窗不重叠,增益存疑);
> (c) `disclosure_date` 的历史 vintage —— tushare **不提供**历史快照,若强制要求则 F1a
>     不是「有偏」而是**不可做**,替代方案是用当前 `pre_date` 并用 `pre_date↔actual_date`
>     一致率量化修订偏差;
> (d) 新增 `namechange` 表回填以获得 PIT 的 ST 状态。
>
> 在 (a)–(d) 裁决之前,**读数不得发布为终稿**。



- 日期:2026-08-28 · 状态:**已按审阅意见修订,待用户终审,零实施,非调度权威**(§6 裁决树只到「提案」)
- 出处:本日 brainstorm「项目还能往哪优化 / 总觉得没有 BUY」。用户三次当面裁定:
  ① BUY 的定义 = **有正期望才叫 BUY,去找能过门槛线的集中信号**(不是「每天给一只最不差的」,也不是「机器只否决人来选」);
  ② 涨停板人口**纳入**,但单列一族、带成交概率模型;
  ③ 做法 = **一把仪器普查**(照 08-24 衍生品普查的模子),不做抛弃型探针、不扩 `factor_lab`。
- 与既有稿的关系:08-26 全项目稿(候选池 A–E)与 08-28 三问稿(账本/时间锚/现场)**不重复**;本稿只回答「隔夜尺上到底有没有可买的人口」。本稿是纯观测(M/I 类),**不重置 08-26 A0 的 20 结果日冻结钟**。
- 本次审阅修订的核心:证据分三层——**R 历史确认 → X 收盘后 oracle 上界 → 14:45 live shadow**。收盘后才完整可知的 `daily` / `limit_list_d` 事实不得冒充 14:45 可行动信号;历史 oracle 过门只产生 live 影子提案,不产生 BUY。

---

## 0. 边界(不重提)

| 类 | 内容 |
|---|---|
| 用户裁定 | 主尺 `gap_c1_o2`(T+1 尾盘买 → T+2 开盘卖;07-10 / 08-05 / 08-22b / 08-28 四次);5–10 日窗口不换;learning 层已退役且真删;L4 复用不恢复;`scan_config.jsonc` 唯一参数事实源;双引擎隔离;B 类改动冻结到 09-中(08-26 A0) |
| 负结果(不重做) | 追当日大涨(gap 尺 ≥9.5% 桶 −0.96%,整区间同号);连板高度在未封人口上反向(IC −0.11);52 周高;反弹日科技召回;预告事件通道;L2 模型 zoo;菜单内任何确定性分数无信号;衍生品三族零证据 |
| 本稿不做 | 不换主尺、不提 swing、不改任何生产代码/配置/prompt/E6、不写代码(实施走 writing-plans) |

---

## 1. 立案:「没有 BUY」不先归咎门太严;检验这把尺上是否存在覆盖成本的多头人口

### 1.1 门槛线(本日只读探针,附录 A;全湖 1091 分区 / 566 万可算股·日,按生产时点 D → D+1 收 → D+2 开对齐)

| | pp/夜 |
|---|---|
| 全市场等权隔夜 `gap_c1_o2` | **−0.089**(中位 −0.062);逐年 2022/23/24/25/26 = −0.122 / −0.049 / −0.079 / −0.096 / −0.109,**没有一年为正**;只有 34.6% 的日子全市场隔夜为正 |
| 往返成本(印花税 0.05 + 佣金,不含滑点;`scan/relative_facts.py:26` 同口径) | ≈ −0.10 |
| 要覆盖 15bps 假设成本所需的相对超额 | **约 ≥ +0.24** |

本湖 2022-03-02 → 2026-08-27 的样本里,用户执行时点恰好**只吃隔夜**,起跑线每晚约 −0.09。这里是样本事实,不外推成制度因果结论。

### 1.2 已量过的多头信号,没有一个够到门槛

| 信号 | 相对超额 pp/夜 | 限定 |
|---|---|---|
| 08-08 过三门的 7 个「反转」因子(`pct_5d`/`dist_low_60`/`rsi12`/`winner_rate`/`price_to_cost`/`cost_premium`/`price_vs_vwap_20`) | 十分位**多空** +36~48bps(132 日) | 附录 A 已按生产时点重算:`pct_5d` D0(5 日跌最多)**−0.02(t −2.6)**、D2–D5 **约 +0.07**(t 21~26)、D9(涨最多)**−0.36(t −34.8)**,逐年大体同形。所谓反转 edge 主要在**空头腿**——它更像「别碰刚大动的票」的避雷信号,不是够覆盖成本的买入信号;多头腿天花板约 +0.07 < 0.2 |
| composite top-20 / top-50 | +0.14 / +0.17 | 40 日,t 2.0 / 2.8;≈成本 |
| 游资龙虎榜净买(08-08 混合版) | IC +0.038,方向对、强度不够 | 132 日;机构席不显著 |
| L1 召回 / L3 finalist−bench / L4 拒绝(08-28 逐级 KPI 首读) | lift 1.05(≈随机)/ −0.01 / −0.16 | 判断层在此尺上无选择力 |

### 1.3 路 A 已在 08-26 用户提供的真跑证据上自相矛盾(证据已内联,运行时不跨引擎读产物)

证据摘要:`pool=composite`;三个 composite 席位(001332 锡装股份 / 603279 景津装备 / 605305 中际联合)全被 L4 打 Underweight → `hard_gate.no_redflag=False`;L4 放行的 4 张 Hold 卡(002926 / 601336 / 601688 / 601995)全 `in_pool=False` → `blocked=True`。合理而非 bug:隔夜信号是微结构效应,L4 做的是基本面判断,两套目标函数天然选相反的一端。开工预注册时把这段摘要作为 `evidence_quality=user_attested` 写入共享 research 文档;Codex 仪器**不得跨引擎读取对端产物补证**。

### 1.4 结论

三份读数(08-22 普查 / 08-26 账本首读 / 08-28 逐级 KPI)已给出判断层偏负、宽因子多头腿天花板约 +0.07 的证据;**事件 / 席位 / 涨停板三类集中型人口从未在全史上用同尺、同门槛、同覆盖门和同时 CI 量过**(08-08 只 132 日且混合版;08-26 spike 只量收盘位置)。它们是隔夜尺上仅剩的、可能给出 +0.3~0.5pp 量级的预注册空间。**先区分「被经济性证伪 / 尚未证实 / 历史正候选」,再谈谁当 BUY owner;未发现不能写成不存在。**

---

## 2. 假设与判读(预注册草案;Stage 0 只解决 schema/PIT 可行性,随后冻结角色与参数到 `docs/research/<开工日>-overnight-concentrated-census.md` §0,**先 commit 后看任何 census 读数**)

### 2.1 假设与证据层
- **全局 H0**:预注册的 R-confirmatory 主格没有任何一个达到成本后正期望。**H1**:至少一格达到。
- 历史全窗都已被多轮研究触碰,所以本次最多产出**历史正候选**,不是 pristine OOS 证明;进入生产前仍须 20 个未来、且该信号实际在场的结果 session 的不可见影子。
- `X_ORACLE`(收盘后事实构造的理论上界)与 `LIVE_X`(14:45 point-in-time 快照)分账。X oracle 无论多强都不能进入全局 H1,只能决定是否值得开 live 影子。
- 普查不裁决任何生产变更;不设停机;全表印出。所有结论限定为「本次预注册主格 + 可观测数据范围」,不得写成整个市场不存在任何信号。

### 2.2 因变量与成本
- 主 target:`gap_c1_o2 = open(T+2)/close(T+1) − 1`,**与生产同一实现**(`autoresearch/research/factor_lab.py::forward_returns`,经 `research/edge_census.py::forward_frame` 复用;`|gap| > ruler.GAP_CLIP=0.31` 视数据错记数剔除)。
- 两列并印:绝对 `gap`;相对 `rel_gap_market` = gap − 当日全市场可交易(`ruler.entry_tradable`)等权均值(`ruler.REL_MARKET` 口径)。**判读只认绝对列**(用户的问题是绝对赚不赚)。
- 单位锁:`COST_BPS = 15`、`COST_RET = 0.0015`、展示时 `COST_PP = 0.15`;三者测试互锁,禁止在 decimal return 上直接减 `0.15`。组成 = 印花税 5bps(卖)+ 佣金 2.5bps×2 + 滑点余量 5bps。
- 每格同时落 `gross_return / assumed_cost_bps / net_return / cost_model_version`。真实成交接线后新增 `realized_cost_bps / realized_net_return`,**不覆盖**假设成本与毛值。
- `unsellable_o2` 不剔(剔了会美化),另印比例、对应报价收益与压力情景。主尺仍是 T+2 开盘报价;只要没有真实成交/可卖时点,结论必须叫「历史报价正候选」而不是「可执行正收益」。

### 2.3 估计量、区间与多重比较
- 主 estimand = **夜等权**:先在每个扫描日内对该格全部事件等权,得一条 `daily_portfolio_return`;再跨日等权。`n_events` 只描述覆盖,不让事件多的热闹日获得更高统计权重。另印 event-equal 作为敏感性,不判读。
- 主区间 = 对日收益序列做预注册 5 交易日 moving-block bootstrap;同日截面相关已在日内聚合吸收,block 保留短期序列相关。现有 `common.stats.date_cluster_bootstrap` 只作对账/敏感性,不得直接把各日事件拼接后当主估计。
- R-confirmatory 主格形成一个 family,用同一批 block draws 算 **max-T simultaneous 95% CI**(若实现受阻,保守回退 Bonferroni simultaneous CI 并在 meta 记 `multiplicity_method`)。正证据/经济性证伪均读校正后区间,不是多张裸 95% CI。
- 折算直觉:全市场约 −0.089pp + 成本 0.15pp,集中信号通常需相对市场约 +0.24pp;但唯一机器判据仍读绝对毛 simultaneous CI。

### 2.4 五态判读 + 可行动轴
先过数据门:主格 `coverage_days / eligible_trade_days ≥ 90%`、`n_days ≥ 60`、每个纳入稳定性检查的完整年 `n_days ≥ 10`;F4 exploratory 要 `n_days ≥ 200`。覆盖不足、PIT 不可证明或 MDE(80% power)仍大于 0.15pp → **数据/功效不足**,只印数。

- **历史正候选**(只允许 R-confirmatory):simultaneous CI 下界 ≥ 0.15pp ∧ 2022/23/24/25 至少 3/4 同号 ∧ 日期两半同号。
- **经济性证伪**:simultaneous CI 上界 < 0.15pp——已排除「覆盖假设成本」,即使毛均值略正也不能叫 BUY。
- **显著有害**:simultaneous CI 上界 < 0(是经济性证伪的更强子态,另打标签)。
- **未决**:区间仍跨 0.15pp;不显著 ≠ 无 alpha,更不等于已证不存在。
- **数据/功效不足**:覆盖、PIT、样本或 MDE 门任一不过。

每格另有 `actionability ∈ {R_REPORT_ELIGIBLE, X_ORACLE_ONLY, LIVE_SHADOW_REQUIRED, UNEXECUTABLE, UNKNOWN}`。`R_REPORT_ELIGIBLE` 只表示时点可供报告使用,不等于生产已批准;`X_ORACLE_ONLY` 只能输出 `ORACLE_EDGE / ORACLE_RULED_OUT / ORACLE_INCONCLUSIVE`,不得输出「历史正候选」。可用性同行印事件频率、胜率、中位、unsellable 比例、MDE、覆盖率、总测试格数与 confirmatory/exploratory/oracle 数。

---

## 3. 四族 + 一层:预注册定义(参数跑前锁死;方向先写后看)

### 3.1 通用人口
- **候选人口**按扫描日 D 的生产 L0 配置快照重建:`cap_floor_yi / include_bj / min_amount_yi / min_list_days` 全部读 `scan_config.jsonc`,并把实际值与 config hash 写入 meta;不得把当前 `include_bj=true` 偷换成「剔北交所」。另印无市值地板敏感性,不判读。
- ST/*ST/退市状态必须 point-in-time:优先 `namechange` 的生效区间;只有当前 `stock_basic.name` 时,PIT 门不过,相关格降 `数据/功效不足`,不得把今天名称回填历史。
- **基准人口**与候选人口分开:`rel_gap_market` 继续使用 `ruler.REL_MARKET` 的全市场可交易分母;L0 门只决定候选是否属于产品可见人口。meta 同时落 `candidate_population` 与 `benchmark_population`,禁止只写一个含糊的 universe。
- 买腿资格:R 族走 `ruler.entry_tradable(fr, MAIN_RULER)`;未知旗另计 `entry_unknown`,主表给 production-default 与 unknown-excluded 两列敏感性。F3 是生产 `buyable_c1=False` 人口的 oracle 镜像,显式标 `production_invisible=true`。
- 单位矩阵写死并测试:`daily.amount=千元`、`moneyflow.*_amount=万元`、`top_inst.net_buy=元`、`block_trade.amount=万元`、`limit_list_d.amount/fd_amount=同表元口径`;所有跨表强度统一转成元后再除,内部同表比值不转换。

### 3.2 时点标签
- **R** = D 盘后、报告生成前已知,可供 D 晚报告使用;每张源表必须证明 `known_at ≤ decision_cutoff(D)`。
- **X_ORACLE** = 用 D+1 **收盘后** EOD 表构造的事后上界;`daily` 最终 OHLC、`limit_list_d.limit/last_time/fd_amount` 均属于此类,不能标成 14:45 可知。
- **LIVE_X** = D+1 14:45 实际保存的 point-in-time 原始响应,带 `source_ts / received_at / raw_hash / staleness`;只有这层能评价尾盘工具。影子期收盘前不可见、不拦单,否则当日即升级为 B 类行为改动。

### 3.3 表

| 族 / 格 | 时点 | 数据 | 定义(扫描日 = D;R 族只读 cutoff 前事实,X_ORACLE 读 D+1 EOD) | 预注册方向 |
|---|---|---|---|---|
| **F1a 财报披露前夜**(R-confirmatory,过 PIT 门后) | R | `disclosure_date`(新表) | 必须用 D 当时可见的 `pre_date` vintage;事件生效 session 按公告时间戳映射,不得只拿今天的最终预约日回填。子格:年报/中报/一季/三季;已出预告/快报 vs 无;`actual_date` 只作 hindsight 诊断 | + 弱;已出预告者 ≈0 |
| F1b 解禁前夜 | R | `share_float`(新表) | `float_date == D+2` ∧ `float_ratio ≥ 1%` | − |
| F1c 除权除息前夜 | R | `dividend`(新表) | `ex_date == D+2`;校正 `gap_adj = (open(D+2)×(1+stk_div) + cash_div_tax) / close(D+1) − 1` | ≈0(机械) |
| **F2a 知名游资净买**(R-confirmatory 条件式) | R | `top_inst`(需补覆盖) | 先按经济键去重,再算 D 日 `Σ知名游资席 net_buy / 当日成交额 ≥ 3%`;名单若只能用 2026 当前快照而无法证明与历史收益独立,本格自动降 exploratory。子格:D 日首板 / 非涨停 / 二板+ | +(08-08 混合版方向已支持) |
| **F2b 机构专用净买**(R-confirmatory) | R | 同上 | 去重后 Σ「机构专用」`net_buy` / 成交额 ≥ 3%;同名匿名机构席不同金额保留,完全相同经济行去重 | ≈0 |
| F2c 北向 | R | `top_inst` 沪/深股通席;`hk_hold`(湖) | 两子格:股通席净买 > 0;`ratio(D) − ratio(D−5)` 当日前 1% | ≈0 / 弱 + |
| F2d 大宗 | R | `block_trade`(需补覆盖) | 股票日聚合金额;成交价用金额加权 VWAP。溢价:VWAP ≥ close(D) ∧ 总金额 ≥ 当日成交额 1%;折价 ≥ 5% 另一格 | 溢价 + / 折价 − |
| F2e 超大单净流入 | R | `moneyflow`(湖) | (`buy_elg_amount − sell_elg_amount`)/成交额 当日前 2% | ≈0(composite 内已弱) |
| F3a 首板·可能成交 | X_ORACLE | `limit_list_d`(回填) | D+1 最终 `limit=='U'` ∧ `limit_times==1` ∧ oracle 成交层 T1;只能判理论上界 | + 上界 |
| F3b 首板·炸板 | X_ORACLE | 同上 | D+1 最终 `limit=='Z'`(收盘未封;T0 oracle);最终状态在 14:57 尚不确定 | − |
| F3c 二板+·可能成交 | X_ORACLE | 同上 | `limit_times ≥ 2` ∧ T1 | + 但更弱 |
| F3d 板块共振增量 | X_ORACLE | 同上(自带 `industry`) | 同日同时存在共振/孤板时做日内配对增量;不直接比两个不同日期集合的裸均值 | + 增量 |
| F3e 一字/早封厚单 | X_ORACLE | 同上 | 其余 U(T2),只印作理论参照 | + 大 |
| **F4 冷门中间态**(exploratory 对照) | R | 湖 `daily` / `daily_basic` | `pct_5d(D)` D2–D5 ∩ `turnover_rate(D)` ≤ 当日中位 ∩ 截至 D 的 20 日收益标准差 ≤ 当日中位 ∩ 截至 D 近 5 日无涨停。因组合在看过附录后形成,不可列 confirmatory | 相对约 +0.1、绝对约 0,预期过不了门 |
| **F5 T+1 条件层** | X_ORACLE | 湖 `daily` EOD | 叠在 F1/F2 每格上:`t1_pct_chg ≤ 3 ∧ t1_pos_in_range < 0.7 ∧ buyable_c1`;与 `scan/outcome.py` 的**事后记账**定义锁相等。with/without 用同日配对 delta;不得称 14:45 规则 | 弱收盘 ≈ 市场;强收盘 −(已知) |

### 3.4 Oracle 成交层与 live 升级门(F3)
- **T0·EOD 可成交参照**:`limit == 'Z'` 只证明最终收盘未封,不证明 14:57 当刻可成交。收益同时印 `entry=close` 的描述值与 `entry=涨停价` 的保守值,不把收盘价冒充真实成交价。
- **T1·EOD 可能成交参照**:`limit == 'U'` ∧ (`last_time ≥ 143000` ∨ `open_times ≥ 1` ∨ `fd_amount / amount ≤ 0.10`)。`open_times` 可能发生在上午、最终 `fd_amount` 含收盘后信息,所以只是一把宽松上界。
- **T2·理论不可成交**:其余 U(早封 + 厚封单 + 一字),只作参照。
- F3 输出必须醒目标 `X_ORACLE_ONLY`;真实 fill rate、排队位置、逆向选择只能由 14:45 live shadow 获得。没有 point-in-time 原始响应/hash 时,任何单测都不得把 F3 verdict 升成 actionable。
- 板幅按板别走 `factor_lab._board_limit`;是否纳入北交所读本次 config snapshot。纳入时显式支持 30cm,不得沿用排除 BSE 的旧假设。

### 3.5 席位分类(`top_inst.exalter`)
`机构专用` → inst;`沪股通专用`/`深股通专用` → north;命中知名游资名单 → youzi;其余 → other。名单 = UZI 插件 `lhb-analyzer/assets/seats-2026.json`(source of truth `scripts/lib/seat_db.py`,v2026.04,22 席)的名称**快照进仓库** `autoresearch/research/assets/youzi_seats.json`(注明来源、许可、snapshot hash),运行时不读插件路径。

- 去重先于分类/求和:经济键 = `(trade_date,ts_code,exalter,buy,sell,net_buy)`;`reason/side` 造成的重复展示不重复计钱。机构专用同名但金额不同的行保留,避免把多个匿名机构席误合一。
- 名单对湖内营业部名称匹配率 < 30% 时,不静默回退后继续判主格:结果降 exploratory,同时印规则版、匹配率与 unmatched top names。关键字回退只作敏感性。
- 当前 2026 名单若无法证明不由 2022–2025 的事后胜负挑出,`F2a.confirmatory_eligible=false`;历史正读数只能叫 exploratory candidate。

### 3.6 预注册 cell registry(机器事实源)
开工 commit 前落一张 JSON/YAML 表,每格固定:`cell_id / family / role(confirmatory|exploratory|oracle) / hypothesis_origin / direction / signal_known_at / entry_order_at / entry_price_model / population / weighting / threshold / coverage_gate / multiplicity_family / verdict_axis`。默认 confirmatory 候选为 F1a/F2a/F2b,但 F1a/F2a 必须先过各自 Stage 0 PIT/lineage 门;最终集合在预注册 commit 后不可变。render 只从 registry 生成表头与总格数;运行时发现未登记格直接报错,防读完结果再加格/换角色。

---

## 4. 仪器蓝图

### 4.1 模块与复用
- 新:`autoresearch/research/overnight_census.py`(研究编排/纯统计;`preflight → build_panel → families → judge/render`)+ `autoresearch/research/assets/youzi_seats.json` + cell registry。共享 lake 的取数/写盘契约属于 `autoresearch/data/`,不塞进研究模块。
- 复用,不另写:目标列/可买旗 `edge_census.lake_trade_days / load_lake_pivots / forward_frame`;主尺常量一律 `common/ruler.py`;F5 数值与 `scan/outcome.py` 的 `EXEC_MAX_PCT_1D / EXEC_MAX_POS_IN_RANGE` 锁相等,但 meta 快照实际数值与 rule hash,避免以后生产阈值变化后历史仪器静默漂移。
- 新增/抽取纯统计原语:`common.stats.date_block_bootstrap` 与 simultaneous max-T;输入是一日一行的配对日收益矩阵。禁止复制第二套口径到 render。
- **不复用** `derivatives_census._save` 写共享湖:其 `to_parquet` 非原子、无 endpoint policy/契约/source trace。已登记端点走 `data.cache.get_or_fetch`;新事件端点必须先登记 policy + contract,或实现同等级的原子 vintage writer(tmp → fsync/close → `os.replace`)。

### 4.2 Stage 0 数据可行性与回填清单

2026-08-28 只读盘点:价格 `daily=1091` 个分区;`daily_basic/moneyflow/top_inst/block_trade≈464`;`limit_list_d=156`。这些缺口是**未采**而非真实空,原稿只回填 `limit_list_d` 会让 F2/F4 在非随机子样本上判读。开工先以 `daily` 交易日轴生成逐端点 expected keys,每张表落 `COMPLETE_ROWS / COMPLETE_EMPTY / FAILED / TRUNCATED / MISSING` manifest;仅文件存在不等于完成。

| 表 | 端点 / 键 | 范围 | 调用量 | 落点 |
|---|---|---|---|---|
| `daily_basic / moneyflow / top_inst / hk_hold / block_trade` | 已登记,date key | 补齐 `daily` 交易日轴缺口;合法空也写 manifest | 以 preflight missing keys 为准 | `lake/<endpoint>/<date>.parquet` |
| `limit_list_d` | 已登记,date key | 补齐 `daily` 交易日轴缺口 | 以 preflight missing keys 为准 | `lake/limit_list_d/<date>.parquet` |
| `namechange` | 新登记;按 ts_code 分页/或可证明完整的区间键 | 覆盖全部出现过的代码与研究窗 | 探针后定 | canonical lake + retrieval manifest |
| `disclosure_date` | 新登记;`end_date` × `retrieved_as_of` vintage,强制分页 | 2021Q4 → 2026Q2 | ≈19×页数×vintage | canonical lake;不得把今天快照伪装成历史 D vintage |
| `share_float` | 新登记;区间查询强制分页,保留 `ann_date` | 2022-03 → 2026-12(含未来解禁) | 探针后定 | canonical lake;判读只用 `ann_date≤D` 的记录 |
| `dividend` | 新登记;按公告/实施键分页,保留 `ann_date/ex_date/div_proc` | 2022-03 → 最新 | 探针后定 | canonical lake;只认实施方案且 `ann_date≤D` |
| `forecast` / `express`(可选) | 已登记,`ann_date` 逐日 | 2022-03 → 湖起点前 | ≈2×800 | 湖同表续接 |

不再预估固定 15–25 分钟:先印 missing-key 数、端点权限、分页上限与预计调用量,用户确认后才联网。每页校验 schema、去重键、返回条数与截断;合法空和失败分开,中断后只重试 FAILED/MISSING。

### 4.3 面板
全湖 `(code,D)` 长表:`gap_c1_o2 / rel_gap_market / buyable_c1 / unsellable_o2 / t1_pct_chg / t1_pos_in_range` + D 日 L0/PIT 人口字段。价格目标继续由 `forward_frame` 生成;因子只读截至 D;R 事件按 `known_at≤D cutoff` join;X oracle 才 join D+1 EOD 表。

缓存键 = `sha256(price_partition_hashes + daily_basic/namechange hashes + config hash + metric_definition_version + code/diff hash)`;落 `reports_<engine>/research/overnight_census/_cache/panel-<fingerprint>.parquet`。输入变化自动换键,不靠人工 `--rebuild` 猜缓存是否过期。

### 4.4 每格输出 + meta
每格:role/actionability、日等权绝对毛/净均值、event-equal 敏感性、相对均值、中位、胜率、n_events/n_days、coverage、MDE、raw 与 simultaneous CI、逐年/两半、事件频率、unsellable、五态/oracle 判读。F3 同印 close-entry 描述值与 limit-entry 保守值;F5/F3d 增量必须是同日配对 delta。

meta 至少包含:engine/run_id、湖窗、expected/observed/missing/failed keys、每个实际读取文件的 path/hash、trade-day axis hash、config/registry/seat snapshot hash、成本与 metric definition version、git commit + dirty flag + dirty diff hash、剔除计数(clip / D+1 缺 / D+2 缺 / 人口/PIT/coverage 门)、multiplicity method/seed/block length、运行时刻与耗时。湖订正只生成新 run,不覆盖旧读数。

### 4.5 CLI
`uv run --no-sync python -m autoresearch.research.overnight_census preflight [--since 20220302]`

`uv run --no-sync python -m autoresearch.research.overnight_census backfill --manifest <preflight.json>`

`uv run --no-sync python -m autoresearch.research.overnight_census run [--since 20220302] [--run-id <canonical-run-id>]`

→ `reports_<engine>/research/overnight_census/<run_id>/{readout.md,cells.json,meta.json,source_manifest.json}`。共享 `docs/research/<开工日>-overnight-concentrated-census.md` 只存先于读数 commit 的预注册与用户批准后的结论摘要,运行时不让 Codex/Claude 互相覆盖。移除任意 `--out`;所有机器产物强制在 `ws.reports_root()` 下。

### 4.6 测试 `tests/research/test_overnight_census.py`(零网络;合成湖 + 合成事件表)
1. 目标列与 `factor_lab.forward_returns` 在合成 pivot 上逐字节相等;专门锁 `factor(D) → close(D+1) → open(D+2)`,把因子错移到 D+1 必须红。
2. 单位锁:`15bps == 0.15pp == 0.0015 return`;破坏任一换算必须红。
3. 日等权与 event-equal 在「一天 100 事件、一天 1 事件」fixture 上必须给不同结果;主判读只读前者。
4. moving-block/max-T deterministic;裸 CI 过而 simultaneous CI 不过的 fixture 必须判未决。
5. 五态边界:lower 恰 0.15 / upper 恰 0.15 / upper <0 / coverage 89.9% / MDE >0.15;「全未决」绝不能渲染成已证无信号。
6. F3 真值表仍锁 T0/T1/T2 分类,但所有 EOD 输入只能产 `X_ORACLE_ONLY`;任何路径把它升 actionable 必须红。
7. F3 Z 同印 close-entry 与 limit-entry;T1 `open_times=1` 不得被描述成 14:57 必成交。
8. 席位经济键去重、机构同名不同金额保留、四分类;匹配率 <30% 自动降 exploratory 且 meta 留痕。
9. F1 PIT:同一 `pre_date` 的两个 retrieval vintage 只允许 `known_at≤D` 版本进入;用最终版本回填必须红。F1c 锁纯现金/送转/混合及实施状态。
10. 当前 config `include_bj=true` fixture 必须纳入 30cm;改 false 才剔。历史 ST 缺 namechange → PIT 不足,不猜。
11. `exec_ok` EOD 复算与 outcome 常量相等,但 verdict 固定 oracle;F5 delta 先同日配对再 bootstrap。
12. 回填原子/幂等:完整分区跳过;临时/截断文件不算完成;合法空 = COMPLETE_EMPTY;失败不落空 parquet;写盘不带 fields。
13. source/config/registry/seat/diff 任一 hash 变化 → 新 fingerprint/run;旧结果不覆盖。
14. 剔除、缺日、unknown entry、unsellable、重复经济行计数全部进 meta。
15. 变异探针 ≥3:成本置 0 翻转一个格;破坏逐年符号断稳定性;删除 simultaneous 校正让假阳性 fixture 变绿,证明三盏灯活着。

---

## 5. 实施顺序与验收(交 writing-plans;每批一 commit,只按显式路径提交)

| 批 | 内容 | 验收 |
|---|---|---|
| 0 | 离线/最小联网 schema 探针:公告时点、vintage 可得性、各端点分页/权限、历史 ST、14:45 live 源 | §8 六个方法学问题逐项 PASS/BLOCKED;任一 BLOCKED 自动降级对应 cell role,不靠实现者自由解释 |
| A | 预注册 §0 + cell registry + cost/stat/coverage 常量**先 commit**;实现纯函数(日等权 / block+max-T / 去重 / PIT / oracle / 判读)与 4.6 测试 | 全量测试绿;三条变异探针真变红;预注册 commit 早于任何 census readout |
| B | `preflight` 盘点 → 用户确认调用量 → canonical backfill → build panel | 每表 expected/complete-empty/failed/missing 对账;主格覆盖 ≥90%;面板行数 = 输入股·日 − 各类剔除且 source hash 齐 |
| C | `run` 生成不可变 run 目录 → 对照 §6 只写「提案」 → 记忆 + `factor-backlog.md` 归档 | 每格 role/actionability/n/coverage/MDE/raw+simultaneous CI/逐年/tier 齐;confirmatory/exploratory/oracle 数同行;附录 A 对账差异只来自生产人口门 |
| D(条件触发) | 仅当 X oracle 值得继续且用户另批:建 14:45 live 数据源探针与 20 日不可见影子 | 每日保存 point-in-time 原始响应/hash/source_ts/received_at/staleness;收盘前零展示零拦截;实测 fill/逆向选择后才提尾盘 owner |

---

## 6. 读数之后的裁决树(全部只到「提案」,均须用户另批)

| 读数 | 提案 |
|---|---|
| 所有 R-confirmatory 主格均**经济性证伪**且 coverage/power/PIT 齐 | 只可写「本次预注册 R 主格内,已排除覆盖 15bps 成本的历史报价 edge」;转 08-26 A1 避雷单/A2 砍卡提案;BUY 维持诚实 0 |
| 所有 R 主格未决 / 数据不足 | 归档为 **INCONCLUSIVE**;BUY 仍为 0,但严禁写「已证无人口」。先决定补数据、积累未来样本还是停止投入 |
| F1/F2 某 R-confirmatory 格成为历史正候选 | I 类只展示事实行;若要成为 BUY 来源,新策略版本 + 20 个未来结果日不可见影子 + 人批。历史全窗不直接接 E6 |
| F3 某 X oracle 格有 edge | 只说明理论上界值得量:**提案** 14:45 live 源与 20 日不可见影子;实测 fill/staleness/逆向选择过门后,才讨论尾盘工具 owner |
| F4 exploratory 过门 | 视为假设生成;先查 D/D+1 错位、人口与数据泄漏,再预注册独立未来窗,不挑战 §1 后直接上线 |
| F5 EOD delta 显著 | 只确认「事后收盘条件有区分」;不能确认 14:45 执行线。是否做 live F5 仍走 D 批 |
| 任何格显著有害 | 进入避雷单**候选**;同样要校正 CI、覆盖/PIT 门与未来影子,不能因方向是拒绝就免治理 |

---

## 7. 不做
不换主尺 / 不提 swing;不改任何生产代码、配置、prompt、E6;不回注、不写生产账本;不分 regime(样本薄 → 诱导调参);不做 ML / 组合优化 / 参数搜索;不引用单日(如 07-21)作证据;指数调整生效日、新股首日、ST 摘帽等稀有事件记 backlog 不进本批;不把 first-sellable 的延迟收益混入主尺;不采购历史 tick。D 批若触发只采**未来** 14:45 point-in-time 影子,不伪造历史快照。

---

## 8. Stage 0 方法学硬门(开工首批解决;不过则降格/阻断对应 family)
1. **F1 时间锚/PIT**:`disclosure_date.pre_date` 抽样与公告时间戳核对;确认预约日的历史 vintage 能否取得、公告究竟在哪个 session 开盘前可知。只有最终快照时,F1a 不得 confirmatory。
2. **端点完整性**:实测 tushare 权限、分页上限、稳定排序/去重键;`disclosure_date` 单期 5000+、`share_float` 月窗同样检查截断,不只测首页。
3. **历史人口**:`namechange` 能否覆盖 ST/退市生效区间;`limit_list_d` 的 ST/BJ 覆盖与当前 `include_bj` 配置对拍。无 PIT 名称时,不能声称生产 L0 历史复刻。
4. **席位 lineage**:名单许可、来源 hash、是否由研究窗内表现挑选;无法排除事后选择就把 F2a 降 exploratory。
5. **除权执行**:确认 `cash_div_tax/stk_div/div_proc` 单位与买入 D+1、卖出 D+2 的税务/到账语义;无法构造真实可实现现金流时,F1c 只作机械报价诊断。
6. **LIVE_X 可得性**:keyless/live 源是否在 14:45 返回带 source timestamp 的涨停池/盘口;只有 EOD endpoint 时明确 `BLOCKED_BY_PIT`,不得用最终表替代。

---

## 附录 A · 只读探针(抛弃型;2026-08-28 跑;复现即贴回 `uv run --no-sync python -` 跑,零写盘)

口径限定:`lake/daily` 1091 分区(2022-03-02 → 2026-08-27),5,742,622 原始股·日;因子取扫描日 **D**,买腿/可买旗取 D+1 收盘,卖腿取 D+2 开盘,与 `factor_lab.forward_returns` 的时点一致。可买近似已升级为逐板别 `buyable_c1`:D+1 收盘未封涨停且有成交;**仍未**剔历史 ST / 市值地板,北交所按 30cm 处理,所以人口比生产 L0 松。`|gap| > 0.31` 剔除。t 只是逐日均值的描述性朴素 t;主仪器判读改用 §2.3 simultaneous block CI。2026 为部分年。

```python
import pandas as pd, numpy as np, glob
files=sorted(glob.glob('lake/daily/*.parquet'))
d=pd.concat([pd.read_parquet(f)[['ts_code','trade_date','open','high','close','pct_chg','amount']] for f in files],ignore_index=True)
d['trade_date']=d['trade_date'].astype(str)
dates=np.sort(d['trade_date'].unique()); rank={x:i for i,x in enumerate(dates)}
d['r']=d['trade_date'].map(rank); d=d.sort_values(['ts_code','r']); g=d.groupby('ts_code')
d['close_5']=g['close'].shift(5); d['r5']=g['r'].shift(5)
d['c1']=g['close'].shift(-1); d['h1']=g['high'].shift(-1); d['pc1']=g['pct_chg'].shift(-1); d['amt1']=g['amount'].shift(-1)
d['o2']=g['open'].shift(-2); d['r1']=g['r'].shift(-1); d['r2']=g['r'].shift(-2)
code=d['ts_code'].astype(str).str.split('.').str[0]
lim=np.where(code.str.startswith(('688','30')),20.,np.where(code.str.startswith(('8','4','920')),30.,10.))
sealed=(d['pc1']>=lim*.98)&(d['c1']>=d['h1']-1e-6)
b=d[(d['r1']==d['r']+1)&(d['r2']==d['r']+2)&(d['amt1']>0)&(~sealed)].copy()
b['gap']=b['o2']/b['c1']-1; b=b[b['gap'].abs()<=0.31]
mkt=b.groupby('trade_date')['gap'].agg(['mean','median']); mkt['year']=mkt.index.str[:4]
print((mkt.groupby('year')[['mean','median']].mean()*100).round(3)); print((mkt[['mean','median']].mean()*100).round(3), (mkt['mean']>0).mean())
print('raw_rows',len(d),'market_days',len(mkt),'computable_rows',len(b))
b=b[b['r5']==b['r']-5]; b['pct_5d']=b['close']/b['close_5']-1
b['dec']=b.groupby('trade_date')['pct_5d'].transform(lambda s: pd.qcut(s.rank(method='first'),10,labels=False))
b['rel']=b['gap']-b.groupby('trade_date')['gap'].transform('mean')
dd=b.groupby(['trade_date','dec'])['rel'].mean().unstack(); dd['year']=dd.index.str[:4]
print((dd.groupby('year')[list(range(10))].mean()*100).round(2)); print((dd[list(range(10))].mean()*100).round(2)); print((dd[list(range(10))].mean()/dd[list(range(10))].std()*np.sqrt(len(dd))).round(1))
for y,x in dd.groupby('year'):
    s=x[0].dropna(); print('D0',y,'t',round(s.mean()/s.std()*np.sqrt(len(s)),1))
print('D0 names/day',round(b[b.dec==0].groupby('trade_date').size().mean(),1))
```

**读数**

全市场(逐板别 `buyable_c1` 近似,但未过完整 L0)隔夜 `gap_c1_o2`,pp/日:

| 年 | 均值 | 中位 |
|---|---:|---:|
| 2022 | −0.122 | −0.093 |
| 2023 | −0.049 | −0.016 |
| 2024 | −0.079 | −0.041 |
| 2025 | −0.096 | −0.079 |
| 2026 | −0.109 | −0.098 |
| 全期 | **−0.089** | **−0.062** |

正日占比 0.346;1089 个可算扫描日、5,657,340 股·日。

`pct_5d` 十分位(0 = 5 日跌最多)相对全市场等权隔夜超额,全期 pp/日与 t:

| 十分位 | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 超额 | −0.02 | +0.06 | +0.07 | +0.07 | +0.07 | +0.07 | +0.05 | +0.03 | −0.03 | **−0.36** |
| t | −2.6 | 12.5 | 20.7 | 24.3 | 25.8 | 25.2 | 17.4 | 8.7 | −6.6 | **−34.8** |

逐年大体同形(D9 逐年 −0.27 ~ −0.47;D2–D5 逐年 +0.05 ~ +0.10);D0 逐年 t:2022 −3.5 / 2023 −8.3 / 2024 −2.0 / 2025 −0.5 / 2026 +1.2;D0 日均 ≈519 只。

**读法**:最优中间多头腿与 D9 的差约 +0.43pp,与 08-08 的 +36~48bps 多空读数同量级,但主要贡献来自 D9 的 −0.36 空头腿;多头腿最好的十分位约 +0.07,绝对值 ≈ −0.089 + 0.07 = −0.019pp,尚未扣成本已略负。该探针只支撑「宽因子多头腿不够」,不承担正式显著性裁决。

---

_仅供研究,非投资建议。_
