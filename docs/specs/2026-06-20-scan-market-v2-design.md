# scan-market v2 — 选集→召回→粗排→精排→研究→整合 六段漏斗

> 设计日:2026-06-20。**取代** `2026-06-20-scan-market-design.md` 的漏斗部分(L0–L4),数据层/铁律沿用。
> 一句话:把全 A 股用「搜索/推荐系统」的管线打法,经 **富 tushare 因子 + T+1 IC 校准的召回** → **两轮 AI 资深投资师判断** → 收成 ~30 张决策卡 + 一份带漏斗溯源的整合报告。

---

## 0. 为什么 v2

v1 的中段是确定性的「四透镜并集 + 板块聚合」,有两个短板:
1. **因子太薄**:只用了 60日/YTD 动量、yjbb 基本面、单一主力净流入;tushare 的 **散户/大单资金结构、筹码集中度、北向、扩展技术指标** 全没进打分。
2. **权重是先验**:L1/L2 权重手设,只有 v1 末期对个别因子做过 IC 验证(砍 vol_ratio/winner_rate),没有系统化、按行业、按 IC 定权重。

v2 针对这两点:**召回阶段**用全因子复合分 + **按申万一级、用 T+1 远期收益 IC 校准且层级收缩**的权重;**粗排/精排**两段把 Claude 当资深投资师做判断(单一确定性分给不了的定性裁剪);慢因子(筹码/北向/基本面/龙虎榜/预告)在精排阶段兑现价值。

## 1. 漏斗总览

| 段 | 名称 | 作用 | 引擎 | 进 → 出 | token |
|---|---|---|---|---|---|
| **L0** | 选集阶段 | 全市场候选池 + 硬门 | 确定性 | 全A → ~5,500 | 0 |
| **L1** | 召回阶段 | 富因子复合分,高召回 | 确定性(权重 IC 校准) | ~5,500 → **1,000** | 0 |
| **L2** | 粗排阶段 | AI 资深投资师 keep/cut | Claude subagent 扇出 | 1,000 → **200** | 中 |
| **L3** | 精排阶段 | 增量取数 + 论点/红队 | Claude subagent 扇出 | 200 → **~30** | 中 |
| **L4** | 研究阶段 | analyze-ticker-lite 决策卡 | Claude subagent 扇出 | ~30 → 30 卡 | 大头 |
| **L5** | 整合阶段 | summary + buy-list + 漏斗溯源 | 确定性(parse_rating) | → 1 份 | 0 |

**搜索系统类比**:L0 候选生成 · L1 召回(宽、快、便宜)· L2 粗排(轻量打分裁剪)· L3 精排(重特征、深判断)· L4 研究(逐只尽调)· L5 整合(结果汇总)。每段只对上段的输出负责,接口 = CSV/卡片,层间解耦。

---

## 2. L0 选集阶段(~5,500)

`screen_market.fetch_universe*` 不变,**唯一改动**:`include_bj` 默认 **True**(纳入北交所)。
- 硬门:剔 ST/退、市值地板(默认 30 亿)、停牌代理(无成交额/无价)、剔次新(上市<60 交易日)。
- 北交所纳入后,30% 涨跌停(8/4/920)的可交易性/止损口径在 L4 决策卡的执行段标注(harvest/lite 已处理)。
- 取数源:`--source tushare`(本机 push2 被封,默认走 tushare 链路)。

## 3. L1 召回阶段(~5,500 → 1,000,零 token)

**两步,Step A 轻门、主力靠 Step B 复合分。**

### 3.1 Step A — 轻门(可交易 + 有数据,**不做选股判断**)
只去掉真正没法参与的尾部,**尽量不误杀**(召回优先):
- 成交额 > 极小阈值(默认 > 0,确保非停牌/有流动性);
- 有最新价、有打分所需的**核心因子**(动量价、资金、至少一组基本面或技术);
- 缺**非核心**因子不剔(打分时 `_wsum` 按"有值子因子"重归一)。

预期 ~5,500 → ~4,500–5,000(轻)。

### 3.2 Step B — 行业条件化复合分(→ top 1,000)
每只算 0–100 复合分,全市场排序取 **top 1,000 = 召回集**。

**因子菜单(8 组,均来自 tushare bulk 端点,§8)**
| 组 | 因子(原始列) | 端点 |
|---|---|---|
| ① 动量/趋势 | pct_60d、pct_ytd、ma_bull(多头排列)、above_ma60 | daily / stk_factor_pro |
| ② 资金·主力 | main_inflow_yi、**大单+特大单净占比**(buy_lg+buy_elg − sell_lg−sell_elg)/amount | moneyflow |
| ③ 资金·散户 | **小单净额/占比**(buy_sm − sell_sm)、散户买卖比 | moneyflow |
| ④ 筹码 | winner_rate(获利比例)、**集中度=(cost_85−cost_15)/cost_50**、现价/cost_50(相对主力成本) | cyq_perf |
| ⑤ 北向 | hk_hold ratio、近 N 日 ratio 变化 | hk_hold |
| ⑥ 技术 | rsi6/rsi12(超买超卖)、macd、vol_ratio、turnover | stk_factor_pro / daily_basic |
| ⑦ 成长 | np_yoy、rev_yoy、加速度、roe、cfo/毛利 质量 | yjbb |
| ⑧ 价值 | 行业内 PE/PB 低分位、dv_ratio 股息 | daily_basic / yjbb |

> 注:T+1 校准下,慢因子(④⑤⑦⑧大部)IC 小、权重自然低——它们的价值在 L2/L3 兑现。**全部仍计算并随 top1000 带下去**(子分 + 原始列)喂粗排/精排。

**复合分** = Σ_组 (组内因子的 IC 加权分 × 组权重),按申万一级条件化(§9 的权重表)。
输出 `L1_recall_top1000.csv`:`code,name,industry,composite,score_momentum,...,score_value` + 全部原始因子列 + `rank`。

## 4. L2 粗排阶段(1,000 → 200,subagent 扇出)

**目标**:用资深投资师的定性判断,把召回集里"分高但有雷"的快速裁掉。粗、快,不补新数据。

- **切片**:1,000 按 L1 rank 切 ~10 片 × 100(或按 budget 调)。
- **每片一个 subagent**:读该片紧凑因子表(~18 列:复合分 + 8 子分 + 关键原始因子如 60日%、主力净占比、散户净、winner_rate、集中度、北向变化、RSI、PE/PB/股息、np_yoy/roe),独立 context。
- **rubric(keep/cut)**:
  - **信号共振**:多组子分一致看多 → 保;只单组突出且与其它矛盾 → 疑。
  - **排陷阱**:放量滞涨/派发(量价背离 + 主力净流出)、价值陷阱(低 PE + 营收下滑 + 行业衰)、过热透支(60日顶 + RSI 超买)、筹码松散 + 高获利盘抛压、北向持续流出。
  - **流动性/题材**合理性。
  - 不确定但高潜 → 保(召回优先,交精排核)。
- **输出**:每只 `keep(bool) + L2分(0–100) + 一句理由`,**只回传保留名单**(不回传全表,省主线 context)。
- **主线配额合并 → 200**:按 L1 rank × L2 分;留少量行业/题材多样性外卡,别让单板块独吞。
- staging `context/scan/<date>/L2_coarse_keep200.csv`。

## 5. L3 精排阶段(200 → ~30,subagent 扇出)

**目标**:对 200 补 L1 没有的**真证据**,逐只形成观点并红队压测,精排出 ~30 finalists。慢因子在此兑现。

- **增量取数**(多为 bulk 端点,200 只可控;无权限/失败则降级标注):
  - 龙虎榜席位(`top_list`/`top_inst`:游资 vs 机构 vs 北向席位)
  - 业绩预告/快报(`forecast`/`express`:前瞻 EPS 方向/超预期)
  - 北向多日趋势(`hk_hold`:加仓/减仓斜率)
  - 股东户数趋势(`stk_holdernumber`:户数降=筹码集中、户数升=散户进场)
  - 质押(`pledge_stat`:>40% 红旗)
  - staging `context/scan/<date>/L3_evidence/<ticker>.json`
- **每只一个(或小批)subagent** 输出:`一句多头论点 + 最大风险(红队) + 催化时点 + 确信度(0–100) + 脆弱度(0–100)`。
- **主线排序**:按 **确信度 − 脆弱度** 取 ~30 → `finalists.csv`。
  - 列:`ticker,code,name,sector,lenses,conviction,triage_lean,triage_reason`(沿用 v1,L4/L5 接口不变)**+ 新增** `thesis,risk,catalyst`。

## 6. L4 研究阶段(~30,= 旧 L3b,不变)

逐只 subagent 跑 **analyze-ticker-lite**(`harvest_context --slim` → 决策卡),staging `context/scan/<date>/details/<ticker>.md`,只回传 评级/目标/R:R。想下重注的票再单独跑全量 analyze-ticker。(落点沿用 2026-06-20 路径改造。)

## 7. L5 整合阶段(`assemble_scan.py`)

发布到 `reports/scan/<YYYYMMDD>/`:`<HHMM>_summary.md` + `<HHMM>_detail/`(同一 HHMM)。

### 7.1 summary 三段(按用户要求)
1. **漏斗数量**:`5,500 →(召回)1,000 →(粗排)200 →(精排)30 →(研究)30 卡` 漏斗表 + 扫描日/universe/规模。
2. **各卡点原因 + 股票概览**:逐段(L0/L1/L2/L3)写「砍了什么 · 标准是什么 · 活下来大致哪类票(板块/风格特征 + 2–3 代表股)」。
3. **L4 汇总投资建议**:buy-list(评级/目标/R:R/提案/置信度,`parse_rating` 五档校验)+ 板块/题材分布 + 组合层面建议(集中度/对冲/节奏)+ 诚实局限。

### 7.2 漏斗溯源 `A_pipeline/`(新)
L5 把各段产物从 context staging 复制发布到 `<HHMM>_detail/A_pipeline/`(`A_` 前缀排最前):
```
reports/scan/<YYYYMMDD>/<HHMM>_detail/
  A_pipeline/
    L0_universe_meta.json        # 选集计数 + 门参数
    L1_recall_top1000.csv        # 召回集 + 复合分 + 子分 + 原始因子
    L1_weights.json              # 本次用的 IC 校准权重(provenance)
    L2_coarse_keep200.csv        # 粗排保留 + L2分 + 理由
    L3_fine_finalists.csv        # 精排 ~30 + thesis/risk/catalyst
    funnel.md                    # 漏斗数量 + 各段标准(人读版)
  <代码>.md                       # L4 决策卡(每只 finalist)
  ...
  ../<HHMM>_summary.md            # 整合报告(父级)
```

---

## 8. 数据与端点(tushare bulk,全市场 per 交易日)

| 端点 | 用途 | 关键字段 | 单位/坑 |
|---|---|---|---|
| `daily` | 价/涨跌/成交额 + 历史算动量 | close,pct_chg,amount | amount 千元 /1e5=亿 |
| `daily_basic` | 估值/活跃/股息 | pe_ttm,pb,dv_ratio,turnover_rate,volume_ratio,total_mv | total_mv 万元 /1e4=亿 |
| `moneyflow` | **资金结构(主力/散户)** | net_mf_amount,buy_sm/md/lg/elg_amount,sell_… | 万元 /1e4=亿;小单≈散户、大+特大≈主力 |
| `stk_factor_pro` | **扩展技术** | ma_5/10/20/60,rsi_6/12,macd,(kdj/boll/cci 可选) | 全市场 ~5,500 行,历史可拉 |
| `cyq_perf` | **筹码** | winner_rate,cost_5/15/50/85/95pct,weight_avg | 集中度=(85−15)/50 |
| `hk_hold` | 北向持股 | ratio,vol | 个股口径;多日算趋势 |
| `top_list`/`top_inst` | 龙虎榜(L3) | 席位/游资/机构 | 事件型,可能需权限 |
| `forecast`/`express` | 业绩预告/快报(L3) | 预告类型/净利区间 | 前瞻信号 |
| `stk_holdernumber` | 股东户数(L3) | holder_num | 趋势=散户进出 |
| `pledge_stat` | 质押(L3) | pledge_ratio | >40% 红旗 |

无权限/失败一律**降级标注**(列置 NaN,打分重归一;L3 证据缺则 thesis 标"未取到")。memory 记:`ths_hot`/`kpl_concept` 无权限。

## 9. 实证校准方法(`factor_lab.py` 扩展)

**这是 v2 的"更准确"内核。校准目标 = T+1 远期收益(用户选定)。**

1. **点对点面板**(已有):D 收盘出信号 → D+1 **开盘**买入(无前视),剔 D+1 一字板,缓存离线迭代。
2. **逐因子 IC**:每因子对 T+1 远期收益的横截面 rank-IC,跨 formation dates 聚合 → **IC 均值 / IC-IR / t 值 / 十分位多空价差**;两半样本稳定性分割。
3. **逐申万一级 IC**:同上但按行业分组(pooled across dates)。
4. **层级收缩**(关键,解决申万一级样本少的噪声):
   - `w(行业, 因子) = λ₁·IC(行业) + (1−λ₁)·[ λ₂·IC(大类板块) + (1−λ₂)·IC(全市场) ]`
   - `λ ∝ f(n_样本, IC 稳定性)`,如 `λ = n/(n+k)`(k 经验调,如 200);样本足/稳的行业更个性化,小行业回落基准。
   - 大类板块 = 申万一级 → ~6–8 组的映射表(周期/制造/消费/医药/TMT成长/金融地产/公用),作为 config。
5. **组权重与组内权重**:组内因子按各自 IC-IR 配比;组权重亦由"组复合分 vs T+1"的 IC 定。
6. **自我迭代**:重拟合 → 比较 IC-IR/价差 → 只留两半样本都稳、符号一致的因子(沿用砍 vol_ratio/winner_rate 的纪律)。
7. **产物**:`weights.json`(`{行业: {因子: 权重}}` + 元信息:as-of、样本期、horizon、shrinkage k)。**L1 读它打分**,权重与代码解耦。
8. **交付承诺**:实现时**实际跑**校准,在本 spec 配套报告 + spec §实证 里给出各组 IC/IC-IR + 价差 + 收缩前后对比,据此定初版 `weights.json`;不拍脑袋。

> 诚实边界:T+1 单 horizon、A股某段 regime;动量/资金类 regime 依赖。weights.json 带 as-of,建议定期重拟合;样本扩到多年/跨牛熊是 future work。

## 9.1 实证结果(2026-06-19 全市场实跑 + 校准)

**校准**(`factor_lab calibrate`,23 成型日 / 102,878 行 / 110 行业,T+1 开到开):
- 组 IC(全市场):**动量 +0.026、技术 +0.026 领先**;北向 +0.014、散户 +0.012;主力净占比 −0.008、价值 −0.010 轻微负;成长 0(factor_lab 无季度基本面)。
- 逐因子十分位多空(T+1,买得到):pct_60d **+68bps(t=2.6)**、above_ma60 +46bps(**t=3.7**)、ma_bull +39bps、rsi6 +49bps 为正;**winner_rate −42bps、vol_ratio −15bps、price_to_cost −37bps、低 PE/PB/股息 ≈ −50bps 为负**。
- 结论:T+1 **动量+技术主导**,筹码/价值弱或反向 → 复合分由快因子排序、符号 IC 驱动。
- 申万一级层级收缩(k=200)产生真实行业差异(如 IT设备 动量/技术 T+1 负=均值回归;专用机械 正)。

**全市场实跑漏斗**(2026-06-19):全A **5,463** → 硬门 **4,318** → 召回 **1,000** → 粗排 **195** → 精排 **30** → 研究(测试取 top6 卡;余 24 同机制)。

**关键发现 + 已修(测试暴露)**:
1. **horizon 错配**:L1 T+1 校准 → 召回被强动量(60日 +100~300%)占满;L4 决策卡判 1–2 周 swing,这些多为**见顶**(主力派发 + 获利盘满 + RSI 超买)→ 6 张卡 5 张 Underweight/Sell、buy-list 近 0 买入。
2. **因子方向被 L2/L3 误读**:子 agent 把高 `winner_rate` 当"筹码健康/顶配"(实证 **−42bps 抛压**),把抛物线顶堆到精排顶端,被 L4 反向打脸。
   - **已修**:① 复合分加**过热抑制**(60日顶5% + RSI>88 + winner>92 → −6,**不改 IC 权重**);② `screening-playbook` 新增**『因子方向经验校准』**并写进 L2/L3/L4 每个 subagent prompt(高获利盘=抛压、过热=回避、主力看 `main_net_ratio`、价值非 T+1 信号)。
3. **本质**:T+1 召回 = 追次日动量;要"买得稳"靠 L2/L3/L4 用经验校准剔过热、偏好"未过热 + 主力真实 + 筹码有空间"。若想 L1 就偏 buyable,改 horizon 到 T+5/10(当前用户选 T+1)。

## 10. 工程

- **重命名**:全 skill 文档 + 脚本 + spec 把 `L3a→L3(精排)`、`L3b→L4(研究)`、旧 `L4→L5(整合)`;并引入 选集/召回/粗排/精排/研究/整合 命名。
- **改 `screen_market.py`(就地演进,不新建文件)**:L1 召回 = 在原 L0 之后,把 v1 的「四透镜并集 + 板块聚合」替换为 **Step A 轻门 + Step B 复合分(读 `weights.json`)→ top1000**;`run()` 产出召回集 CSV(8 子分 + 原始因子)。`aggregate_sectors` 降级为"板块概览统计"(仅供 L5 描述,不再做漏斗截断)。`factor_lab.py` 扩展(§9)产 `weights.json`。L2/L3 由 skill 编排 subagent,产物 staging。
- **可复现**:L1/L2/L3 中间名单全部 staging 到 `context/scan/<date>/`,L5 发布到 `A_pipeline/`。AI 段 re-run 友好(读 staging)。
- **校验保留**:L4 卡仍带 `**Rating**` + 决策仪表盘 + `FINAL TRANSACTION PROPOSAL`,L5 `parse_rating` 五档机读不变。
- **selftest**:`screen_market`(打分/门/复合分)、`factor_lab`(IC 数学 + 收缩 + 板幅)、`assemble_scan`(漏斗解析 + A_pipeline 发布 + 缺卡降级)各自离线自测。

## 11. 诚实局限

- L1 召回是**启发式 + IC 校准**,非保证;T+1 单 horizon、单 regime 样本。
- L2/L3 是 **Claude 推理产出**,非自动引擎;每段留 staging + A_pipeline 溯源可核。
- 业绩/龙虎榜/预告有**披露滞后/事件稀疏**;无权限端点降级标注。
- A股涨跌停/停牌使名义止损未必可执行(L4 卡执行段标注)。
- token 中段上升(L2 跑 1,000 + L3 对 200 补数据+论点);换更宽召回 + 两轮真判断。

## 12. 复现命令(实现后)

```bash
# 校准(离线、缓存、可迭代)
uv run --no-sync python scripts/factor_lab.py harvest     # 拉+缓存全市场面板(一次)
uv run --no-sync python scripts/factor_lab.py calibrate    # T+1 IC + 申万一级收缩 → weights.json

# 跑漏斗
uv run --no-sync python scripts/screen_market.py <date> --source tushare   # L0+L1 → 召回 top1000
#   → L2 粗排 / L3 精排 / L4 研究:skill 编排 subagent(见 screening-playbook.md)
uv run --no-sync python scripts/assemble_scan.py <date>    # L5 整合 → <HHMM>_summary.md + A_pipeline/
```
