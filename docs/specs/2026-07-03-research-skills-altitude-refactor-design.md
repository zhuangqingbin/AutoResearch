# 研究技能海拔重构 —— 三能力×两档 + 编排前移(宏观 Stage 0 / 中观 Stage 1)

> 2026-07-03。三轮讨论定稿。把 `.claude/skills` 从"功能清单"重构成**研究部组织**:三个能力
> skill(宏观/中观/微观,各含 full/lite 两档,prompt 路由)+ scan-market 纯编排 + scan-retro/feedback
> 闭环。宏观 lite 前移到 Stage 0 与 universe 并行,中观(行业)lite 挂 L2 后与 L3 证据取数并行。
> **地基**:market_pack 改湖派生(`build_market_frame`),哨兵/宏观研判脱离对 universe 产物的依赖。

## 1. 问题(为什么改)

1. **行业层真空**:漏斗从市场地形(策略师)直接跳到逐只决策卡。同链 finalist(如 PCB 三只)各自
   的 L4 subagent 独立重建同一套行业论点(重复 token、结论可能互斥);L5 "同板块=1 bet" 只能事后
   **告警**,无法事前**选链上最佳表达**;lite 卡估值判断缺行业相对锚(slim 砍了同业表)。
2. **市场研判与宏观割裂**:首席策略师只看 A股内部结构(market_pack),macro-research 的跨资产/
   政策/regime 象限是孤岛——策略师定调时手里没有宏观背景;反向,macro 的 sector_map 也不消费
   scan 的日频地形。
3. **lite/full 双 skill 触发面重复**:analyze-ticker 与 analyze-ticker-lite 是两个 skill,描述互相
   排斥、lite-playbook 跨 skill 引用 engine-playbook 的数据坑,"卡想升级成全量报告"要换 skill。
4. **market_pack 钉死在 L2 后**(读 `L1_scored_full.csv`):策略师必须串行等 universe ~10 分钟;
   哨兵拍板(2.2)时只有确定性判据一行、没有叙事研判;盘前无法预判"今天该不该全扫"。

## 2. 目标 / 非目标

**目标**:① 三能力 skill 各一个、档位 prompt 路由;② 命名统一 `*-research`;③ 市场研判并入宏观
lite,`market_view.md` 下游契约不变;④ 新建 sector-research(行业 pack + brief + TTL 复用 + ledger);
⑤ 宏观 lite 前移 Stage 0、中观 lite 挂 Stage 1 + L4 前补漏;⑥ 全部注入 presence-gated,parity 不破。

**非目标**:❌ L1/L2 吃叙事(确定性层零 LLM 铁律;叙事下渗唯一合法通道=影子漏斗先行);❌ L2 复活
ML;❌ prompt A/B harness(R9);❌ 包(`autoresearch.*`)重命名——只动 skill 层命名(见 D8);
❌ 港美股行业体系(v1 只做申万一级)。

## 3. 总体架构

**2×3 矩阵**(每海拔 = full 存量研究 + lite 日频增量 + 确定性底座):

| 海拔 | full(存量,用户/低频触发) | lite(日频,scan 默认档) | 确定性底座(零 LLM) |
|---|---|---|---|
| **macro-research** | 全球宏观+跨资产/A股行业配置 → 落 `macro_state.json` | **市场研判**(原首席策略师)→ `market_view.md` | `build_market_frame` → `market_pack` |
| **sector-research**(新) | 单行业深研(链/格局/景气)→ 回写 sector_memo + ledger | **行业 brief**(地形段+研判段,TTL 3–5 日) | `sector.pack`(帧 groupby + 行业指数/资金) |
| **stock-research**(合并) | v4 全量报告(决策主线+证据附录) | 决策卡(P0–P5 渐进+早停) | `analyze.harvest`(全量/--slim) |

**流水线**(scan-market 编排;★=本次新增/移动):

```
盘前 cron(零LLM):    lake日更 → build_market_frame → regime → ★哨兵预告 → consensus/calendar/watchlist
Stage 0(同一条消息并发):
  ├─ universe L0-L2(确定性 Bash,后台)
  └─ ★宏观 lite(market_pack(frame) + macro_state.json) → market_view.md
哨兵拍板(确定性判据 + market_view 双在手)
Stage 1(L2 后同一条消息并发):
  ├─ harvest_l3_evidence / l3_news(确定性)
  └─ ★中观 lite × 热点行业(TTL 缓存) → sector_briefs/<industry>.md
L3 精排(紧凑表 = 因子+证据+★全行业地形行+★热点brief地形块+market_view地形段)
L4 派发前: 直通车 → ♻️卡片复用 → ★未覆盖 finalist 聚类补 brief → slim 批量 harvest(并行)
L4 卡(market_view地形 + ★行业brief地形 + 档案) → skeptic/红队 → L5(方向性内容唯一落点 + ★同链对比)
```

## 4. 设计决策

- **D1 一能力一 skill,档位 prompt 路由**。路由规则写死在各 SKILL.md 头部:被 scan 调用 → 恒 lite;
  用户单独触发 → 默认 full,出现"快速/看一眼/出张卡/brief"→ lite;lite 结论想下重注 → 同 skill 升
  full。弃"full/lite 双 skill"(触发面重复、跨 skill 引用、升级要换 skill)。
- **D2 命名 `*-research` 后缀**:`macro-research`(不动)/ `sector-research` / `stock-research`。
  弃 `research-*` 前缀(分组更好看但三个全要 rename,churn 大)。"sector" 对齐代码词汇
  (`sectors.csv`/`sector_memo`/`l2_sector_cap`)。分类学:`*-research`=能力,`scan-*`=编排+考核,
  `feedback`=记忆。
- **D3 市场层并入宏观 lite**:策略师人格移进 macro playbook 当 lite 模式;`market_view.md` 文件名、
  L3/L4/L5 三处注入、缺文件回退 `render_fallback_pulse` 全部不变(下游零改动)。
- **D4 pack 湖派生**:从 `universe.run` 抽出"lake→因子帧"前半段成 `build_market_frame(date)`
  (不打分、不召回),universe 改为调用它——**单一代码路径**,帧口径天然与 L1_scored_full 一致。
  `market_pack`/`sentinel_advice`/`classify_regime` 增加帧入口。
- **D5 宏观 lite = Stage 0 并行**:输入只有 frame+macro_state,无漏斗上游依赖;哨兵拍板前叙事就绪;
  哨兵日本来就跑策略师 → 两条路径共用此步。
- **D6 中观 lite = Stage 1 并行 + L4 前补漏**:选行业 = `红榜top3 ∪ L2集中度top3 ∪ 观察单/直通车
  涉及行业`(去重 cap K=6),L4 派发前对"≥2 只同行业 finalist 且未覆盖"补跑——保证**每条链一份
  brief** 的契约。不更早(L1 前无菜单集中度,行业选择失锚)、不只在 L3 后(L3 是漏斗咽喉,缺链
  认知可能砍掉最佳表达且 L4 不可恢复;与证据取数并行=零墙钟)。
- **D7 确定性层只吃确定性衍生物**:L1 regime-aware 权重块(已有)是唯一 宏观→L1 通道;行业/主题
  想进召回,必须走影子漏斗变体 ≥10 日 retro 裁决后转正(healthy 通道先例)。
- **D8 skill 层命名动、包命名不动**:`autoresearch.analyze`/`autoresearch.macro` 保持(CLI/测试/
  肌肉记忆 churn 不值);新包 `autoresearch/sector/` 与新 skill 同名。

## 5. 组件详设

### 5.1 `build_market_frame` + market_pack 双入口(Phase 0,零行为变化)

```python
# autoresearch/scan/frame.py(从 universe.run 抽出)
def build_market_frame(date, root="context/lake") -> pd.DataFrame:
    """lake → 全市场因子帧(零打分零召回)。列含 market_pack/sentinel/regime 全部依赖:
    code/name/industry/pct_60d/pct_ytd/above_ma60/ma_bull/pe/pb/main_net_ratio/cmf_20/obv_mom_20…
    cmf/obv 沿用现湖上 rolling 计算路径;缺端点列 → NaN(pack 的 _frac_of 空→None 语义已支持)。"""
```

- `market_pack(scan_dir)` 保持;新增 `market_pack_from_frame(frame, sectors=None)`——红黑榜改由
  帧 `groupby(industry)` 生成(`n_recall`→`n`,`median_composite` 盘前无打分 → None,描述性可缺)。
- `sentinel_advice` 增帧入口(`healthy_riser_mask(frame)` × `classify_regime(frame)`),盘前 cron 可
  打**哨兵预告**;scan 内仍走 L1_scored_full 路径(同谓词同口径)。
- **parity 测试**:同日 `build_market_frame` vs `L1_scored_full` 关键列(regime 三元组/breadth/
  估值/资金占比)一致或容差 ≤1e-6;universe 重构后 golden 输出逐字节不变。

### 5.2 macro-research 改造

- **playbook 增 lite 段**:原 screening-playbook『首席策略师 prompt』整段移入,输入从
  `market_pack(scan_dir)` 换成 `market_pack_from_frame` JSON + `macro_state.json`(若新鲜);
  6 小节模板、"1–3 节描述性/4–5 节规范性"、防锚定铁律逐字保留。
- **`macro_state.json`**(full 档 assemble 时确定性落盘,复用 `parse_allocation` 已有解析):

```json
{"as_of": "2026-07-01", "run_report": "reports/macro/20260701/1030_summary.md",
 "regime_at_run": "range", "quadrant": "增长下/通胀下(衰退交易)",
 "risk_stance": "neutral", "cross_asset": {"美股": "Hold", "A股·港股": "Overweight"},
 "ashare_sectors": {"电子": "Overweight", "煤炭": "Underweight"},
 "key_risks": ["关税二次升级", "中国地产失速"], "ttl_days": 7}
```

- **失效规则(两条,注入方判)**:① `today − as_of > ttl_days`;② 当日 `classify_regime` 标签 ≠
  `regime_at_run`(regime 翻转日拿一周前宏观叙事校准比没有更坏)。失效 → 宏观 lite prompt 标
  "宏观视图过期,仅用日频 pack";缺文件 → 只用 pack(现状,parity)。

### 5.3 sector-research 新建

- **目录**:`.claude/skills/sector-research/{SKILL.md, sector-playbook.md}`;包 `autoresearch/sector/`。
- **确定性 pack**(`python -m autoresearch.sector.pack <申万一级> [date]`):成分截面聚合(当日帧或
  L1_scored_full:n、median pe/pb/pct_60d、主力净占比合计与占比、healthy 数、winner_rate 分布、
  np_yoy/roe 中位、fwd_pe 有值中位)+ 行业指数序列与估值分位(tushare `sw_daily`/`index_dailybasic`,
  端点权限待核,缺 → 降级)+ 行业资金净流入(`tushare_macro` 已有)+ 成分解禁/披露聚合(`calendar.csv`)
  + 预告/公告计数(`L3_evidence`/`L3_news` 若在)。产物 `context/sector/<date>/<industry>.json`。
- **lite brief**(一个 subagent,读 pack JSON + sector_memo 行):落
  `context/scan/<date>/sector_briefs/<industry>.md`,**两段结构**(复用策略师切法):
  - 【地形段】(喂 L3/L4):链定位一句 + 景气读数(数字)+ 行业内估值/资金分布(谁贵谁便宜,只
    数字)+ 事件日历。**禁超低配语言、禁个股方向**。
  - 【研判段】(只进 standalone 报告与 L5):景气位置判断/格局观点/风险。
  - 铁律尾注:个股评级只由本股 rubric 三门定。
- **TTL 复用**(镜像 `l4_reuse`):近 5 日已有该行业 brief ∧ 行业指数 |Δ|≤3% ∧ regime 未翻 ∧ 无新
  行业级事件 → 拷贝 + ♻️banner;`python -m autoresearch.sector.reuse <date> --apply`。
- **full 档**:单行业深研(链上下游、WebSearch 产业证据标『实时网查』、成分龙头映射)→
  `reports/sector/<date>/<industry>.md`;收尾 `upsert_memo`(sector_memo 从"卡片共性蒸馏"升级为
  "研究结论回写")+ ledger 记方向判断。
- **`sector_ledger`**(`autoresearch/learning/sector_ledger.py`,与 channel_ledger 同构):brief/full
  研判段的方向性结论 × 行业指数 fwd_5/20 → 行业嘴的 MTM;n<10 标 ⚠样本少。

### 5.4 stock-research 合一

- 目录合并:`.claude/skills/stock-research/{SKILL.md, engine-playbook.md, lite-playbook.md}`——
  lite-playbook 对数据坑的跨 skill 引用变成目录内引用。
- SKILL.md 重写为**路由器**(~3KB):档位判定(D1)+ 共同铁律(数字出 context/五档/
  `FINAL TRANSACTION PROPOSAL`/诚实收尾)+ 两 playbook 指针。frontmatter description 覆盖双档
  关键词("研究/分析 X"、"快速看一眼/出张卡"、"scan L4 workhorse")。

### 5.5 scan-market 编排改写点

1. **Stage 0**:第 1 步改为同一条消息并发 ① `universe` Bash(后台)② 宏观 lite subagent。
2. **哨兵**:2.2 拍板时呈现 确定性判据 + market_view 摘要;盘前 cron 已出预告则引用。
3. **Stage 1**:L3 证据取数与 中观 lite × K 行业(TTL 命中跳过)同一条消息并发。
4. **L3**:`l3_table_md(..., sector_terrain=True)` 前置**全行业确定性地形段**(每申万一级一行:
   L2 只数/median pct_60d/median pe/主力占比/healthy 数——全 31 行业对称,防"有 brief 的行业被
   系统性高看")+ 热点行业【地形段】块。参数默认 False = 逐字 parity。
5. **L4**:`compose_funnel_brief` 在 sector_memo 行处升级——有 `sector_briefs/<industry>.md` →
   注入其【地形段】;无 → 现状 `render_memo_line`(presence-gated)。
6. **L5**:assemble 读 briefs 研判段入报告(presence-gated 新节);同链 ≥2 卡时出**确定性并排表**
   (评级/estval/主力/R:R 同列对比),"同板块=1 bet"从告警升级为可比较。
7. **哨兵日**:宏观 lite 照跑(Stage 0 天然),中观 lite 跳过(无 L3/L4;红队对象行业可选跑 1 张)。

## 6. 护栏(防锚定 + parity)

- **三层同律**:宏观→行业→个股逐层注入的只能是描述性地形(数字/事实/日历);方向性判断只出现在
  各层 standalone 报告与 L5。个股评级只由本股 rubric 三门决定——此句在三个注入模板中逐字保留。
- **presence-gated 清单**:缺 `market_view.md` → fallback_pulse;缺/过期 `macro_state.json` → 只用
  pack;缺 `sector_briefs/` → L4 回退 memo 行、L5 不加节;`sector_terrain` 默认关。全部现状即回退。
- **确定性层零 LLM**:L0/L1/L2/L5 与全部 pack/frame/ledger 纯 pandas;brief 是 LLM 产物但其**进入
  确定性层的路径不存在**(只进 L3/L4 prompt 与 L5 报告)。

## 7. 迁移计划(每步独立可合,parity 检查点随行)

| Phase | 内容 | 检查点 |
|---|---|---|
| **0** | `build_market_frame` 抽取;pack/sentinel 帧入口;盘前哨兵预告 | universe golden 逐字节不变;帧 vs L1_scored_full parity 测试 |
| **1** | skill 目录重组:stock-research 合一、macro playbook 收编策略师、全部旧名引用清扫 | `rg 'analyze-ticker|首席策略师市场研判' .claude CLAUDE.md README.md` 归零(设计沿革注保留);现有测试全绿 |
| **2** | `macro_state.json` 落盘 + 失效规则;scan Stage 0 接线 | 缺 state 文件 → 策略师行为=现状;market_view 下游三处零改动 |
| **3** | `autoresearch/sector/` pack+brief+TTL;scan Stage 1/L3 terrain/L4 注入/L5 对比 | 全部 presence-gated;`sector_terrain=False` 逐字 parity;无 brief 日 = 现状输出 |
| **4** | `sector_ledger` + retro 接线(行业嘴 MTM) | n<10 ⚠;retro_input 增节 presence-gated |

**引用清扫面**(Phase 1):scan-market SKILL/playbook/STAGES 中 `analyze-ticker-lite` ×多处、
CLAUDE.md 研究入口节、README 架构节;memory 索引下次会话顺带更新。**测试新增**:
`test_market_frame_parity` / `test_pack_frame_entry` / `test_sentinel_frame` / `test_sector_pack` /
`test_sector_reuse` / `test_l3_sector_terrain`(默认关 parity)/ `test_brief_presence_gate` /
`test_macro_state_schema`;可选 doc-lint 雏形(`test_skill_docs_refs`:skill md 里的
`python -m autoresearch.*` 模块可 import、旧 skill 名零命中)。

## 8. 度量(怎么知道改对了)

- **同链摊销**:同行业 ≥2 卡日的 L4 输出字节/卡 环比(brief 上线前后);
- **L3 链内错杀**:错杀验尸增"同链最佳表达被砍"标签,brief 上线后应趋零;
- **行业嘴 hit-rate**:sector_ledger 方向判断 vs 行业指数 fwd_20(≥10 样本再下结论);
- **墙钟**:Stage 0 并行省 ~8–10 分钟(策略师不再串行);brief TTL 命中率(预期稳态 ≥50%);
- **哨兵预告命中**:盘前预告 vs scan 内正式判据一致率(帧口径漂移监控)。

## 9. 开放问题

1. tushare 申万指数端点(`sw_daily`/`index_dailybasic`)权限与字段待实核;缺 → 行业指数块降级,
   pack 其余字段不受影响。
2. `cmf_20` 全市场湖上 rolling 的盘前耗时若 >1–2 分钟 → 归 cron 摊掉(交互路径不付)。
3. 行业粒度 v1 = 申万一级(31);二级(如"半导体设备")留给 full 档内自行下钻,不进 pack 契约。
4. brief【地形段】在 L3 表的呈现:表前块 vs 行业段头,待首次真实跑动看 L3 引用质量再定。
5. 三个 LLM 段(宏观 lite 新输入、行业 brief、L5 同链对比)照例**未实跑验证**——Phase 3 合入后
   第一个真实 scan 日出读数。

---
## 附录 A · 讨论沿革(三轮,2026-07-03)

R1:海拔分层提出(宏观/行业/微观能力 + scan 编排),行业层判为真空白;R2:市场层并入宏观、
"前移"拆成"数据早(盘前)/叙事判断层(Stage 0/1)/方向只进 L5",L1/L2 只吃确定性衍生物,叙事
下渗唯一通道=影子;R3:一能力一 skill(prompt 档位路由)、`*-research` 命名、scan 恒 lite、
宏观 Stage 0/中观 Stage 1+补漏定稿。相关既有 spec:`2026-07-01-scan-market-strategist-view-design.md`
(策略师,本设计收编其人格归属)、`2026-06-25-l2-stratified-sampler-design.md`(L2 不动)、
`2026-07-03-scan-sentinel-economy-design.md`(哨兵,本设计给其盘前预告入口)。
