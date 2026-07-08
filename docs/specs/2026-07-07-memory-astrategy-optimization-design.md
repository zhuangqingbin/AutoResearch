# Memory × A股策略优化波设计（三轴：记忆闭环 M / 策略 S / 交叉 X）

> 来源：2026-07-07 brainstorm（三路开源调研：agent 记忆系统 / LLM 交易 agent / A股量化工具链 + 本地摸底）。
> 状态：设计稿，未实现。每个工作流独立可落地，按「分期与依赖」节排期。

## 目标 / 非目标

**目标**
1. 打开记忆库的**写侧产能**（现状：lessons 仅 4 条 / feedback 5 条——瓶颈不在检索在写入），并在库长大前补齐写入卫生。
2. 补策略侧三个已量化的空白：range 市品相供给缺口（连续 10 日 0 买）、召回错配根因（赢家 91% 在池仅 4.8% 过线）、组合层空白（只有买单列表无仓位）。
3. 把已有 ledger/校准数据变现为**判断层加权**（开源全域尚无 persona/透镜级 track record 加权——先发优势）。
4. 用已落盘的 typed trace 建**确定性层回放**能力，让 proposals 裁决与 guard 晋升不再只等前向数据。

**非目标**
- 不上向量库/embedding 检索（regime/scope/tag 结构化匹配够用，零依赖）。
- 不做打板/隔日溢价**交易信号**（公开证据：2017 后持续衰减+监管趋严；只当温度计成分）。
- 不搬 Qlib Alpha158/360 因子集（公开 IC 已衰减近零；只抄回测保真度规则）。
- 不做 prompt 自动优化（R9 已拒）、不做风险人格翻转（与 0 买纪律冲突）、不做 K 线视觉多模态。
- 不改「宁缺勿滥」哲学：所有新机制**切品相/给地形/改记账，不放宽质量门**。

## 实现进度（2026-07-08）

已落地（TDD·全绿 768 测试）：**M2 M3 M1 X3**。
- **M3** 失效记账：`upsert` 记 `valid_from`、退休/衰减记 `invalid_at`、`lessons_as_of(day)` 时点信念集（feedback_store.py）。
- **M2** 写入四操作裁决：`similar_lessons`（结构化召回，零 embedding）+ `adjudicate(ADD/UPDATE/DELETE/NOOP)` + changelog 审计；已接线 feedback / scan-retro 蒸馏段。
- **M1** 同日配对蒸馏：`build_retro_pairs`（retro.py），`attribute()` 成熟日落 `retro/_retro_pairs.csv`；retro-playbook 步骤2 消费。**真数据驱动逮到并修复「未评级 universe 票误纳 fail 侧」坑。**
- **X3** 评测卫生：`risk_metrics` + `risk_block`（MDD/Sortino vs buy&hold 基线）入 paper NAV render。

未开始：S1 温度计 · X1 校准加权 · S2 因子外环 · S3 sizer · S4 保真度 · S5 数据扩容 · X2 回放 · M4/M5/M6（触发式后排）。

## 决策摘要（brainstorm 定）

| # | 工作流 | 一句话 | 灵感来源 | 成本 |
|---|---|---|---|---|
| M1 | 同日配对蒸馏 | retro 补「同日反例」构造 fail/success pair，每个扫描日产 lesson | ExpeL | S |
| M2 | 写入四操作裁决 | 落 lesson 前判 ADD/UPDATE/DELETE/NOOP，UPDATE 可改写旧条文本 | Mem0 + ExpeL.EDIT | S |
| M3 | 失效记账 | lesson 加 valid_from/invalid_at/superseded_by，退休不删 | Zep/Graphiti | XS |
| M4 | 注入打分制+引用晋升 | cap 选择改 importance×recency×relevance；被引用即加权 | FinMem + Generative Agents | S（触发式后排） |
| M5 | 记忆园丁 | 败绩累计触发的合并/升维整理 session | Letta sleep-time + GA 反思树 | M（触发式后排） |
| M6 | 记忆探针 | 自家历史造「知识更新/弃答」探针接 stage_eval | LongMemEval 能力分型 | S（后排） |
| S1 | 市场温度计 | tushare `limit_list_d` 出 5 序列→情绪温度→regime 第四维切品相菜单 | 民间情绪周期口径 + QuantsPlaybook | S |
| S2 | 因子挖掘外环 | Claude 提议→factor_lab IC 验证→mutual-IC 去重→champion | RD-Agent(Q) + AlphaGen 去冗余 | M |
| S3 | 仓位 sizer | fractional Kelly × vol target × 流动性上限，胜率用触价校准修正值 | Kelly 实践共识 | S |
| S4 | 回测/触价保真度 | 涨停不可成交 + point-in-time 财务 | Qlib Exchange 层 | S |
| S5 | 数据扩容 | 股东户数因子（RankIC −0.062）+ 融资/ETF 份额行业轮动地形 | tushare 端点 + 公开研究 | S |
| X1 | 透镜/辩论校准加权 | 买单 ledger 基率 + cross_calib → L4.5 辩论/PM 三透镜按历史命中率加权 | MARGIN + FinCon | S |
| X2 | 策略回放 harness | 对历史 trace 重放门/权重/quota 变体，服务 proposals 裁决 + guard 自验证 | Voyager 自验证 + 自有 trace | M |
| X3 | 评测卫生 | paper NAV 加 Sortino/MDD/buy&hold 基线；harvest lookahead 自查清单 | StockBench + 上游 v0.3.x | XS |

## 全局设计原则（沿用本 repo 既有铁律）

1. **Parity 不破**：一切新块 presence-gated / 默认关 / store 空时逐字回退；老命令输出与改前一致。
2. **零付费 LLM**：判断/蒸馏由 Claude session 内做；存取/回放/打分全确定性脚本，可 `--selftest`。
3. **描述性地形 vs 方向指令**：温度计/校准表/轮动地形喂 L3/L4 时只描述不指令；规范性读法只进 L5（护 L4 独立性）。
4. **人批不自动动门**：guard 晋升、champion 切换、lesson 退休走 proposals 人批；机器只提名+提供回放证据。
5. **先 IC 验证再进卡**：任何新因子（股东户数/温度序列/轮动列）先过 `factor_lab` 再进 L4 卡或 sector pack。

---

## 轴一 M：记忆/闭环学习

### M1 · 同日配对蒸馏（写侧产能，最优先的记忆项）

**动机**：写入源只有用户反馈 + retro missed-winner 两条路 → 库 4 条。ExpeL 精髓 = 控制变量的 fail/success 轨迹对；选股场景里「同一天」才是「同一任务」（regime/地形/注入 lessons/漏斗参数全恒定，diff 只剩标的特征与判断）。

**机制**
- retro 成熟日（T+5）构造 pair：**「L4 高评级但 fwd 跌的票」×「同日被门拦/被 L2-L3 砍/漏召回但 fwd 涨的票」**，按同行业/同风格最近邻配对（无最近邻时放宽到同 lane）。
- 0 买日用评级构造（OW/Hold 当 success 侧代理），保证日日可产。
- pair 表（确定性产出：两票的因子行 diff + 门触发 diff + 卡片结论摘要）交 Claude 蒸馏候选 lesson → 走 M2 裁决落库。
- lesson 标注 `regimes=[当日 regime]`（既有 R1 惯例），evidence 记 pair id。

**落地点**：`autoresearch/learning/retro.py`（pair 构造 + 落 `context/scan/<date>/_retro_pairs.csv`）；`scan-retro` skill 增蒸馏段。
**验收**：① pair 构造零 LLM、有契约测试（同日无反例→空表不报错）；② 连续 5 个 retro 日累计新增 lesson ≥3 条且经 M2 无重复入库；③ parity：不跑 retro 的路径零变化。
**成本**：S。**依赖**：无（M2 先行更佳，见分期）。

### M2 · 写入四操作裁决（Mem0 式，库长大前的卫生地基）

**动机**：`upsert_lesson` 只按 slug 撞 id——不同 slug 的重复/矛盾条无人裁决；M1 开闸后必然涌入相近条目。

**机制**
- `feedback_store.adjudicate_candidates(candidate) -> {op, target_id, merged_rule}` 的**确定性预检**：按 scope kind/value + regimes 交集 + rule 关键词重合度拉 top-k 相似旧条（不用 embedding）。
- Claude 对照候选与旧条判 **ADD / UPDATE（改写旧条 rule 文本+并集 evidence，保 id 与 MTM 账）/ DELETE（矛盾且旧条 MTM 弱）/ NOOP**；DELETE 实际走 M3 失效（不物理删）。
- 裁决记录落 `changelog.jsonl`（kind="lesson_adjudicate"，before/after 摘要）可回滚。

**落地点**：`autoresearch/learning/feedback_store.py`（预检函数 + UPDATE 写路径）；`feedback` / `scan-retro` skill 蒸馏段改为「先 adjudicate 后落库」。
**验收**：① 同义 candidate 二次提交 → NOOP/UPDATE 而非新条；② UPDATE 保 id/mtm/reinforce 账；③ selftest 覆盖四操作。
**成本**：S。**依赖**：无；M1 前落地最佳。

### M3 · 失效记账（Graphiti 双时间轴，XS）

**机制**：lesson schema 加 `valid_from`（=created）`invalid_at` `superseded_by` 三字段；`retire_lesson`/`mtm_update` 退休路径写 `invalid_at`，M2 的 UPDATE-替代路径写 `superseded_by`。`lessons_for` 行为不变（仍只取 active）——字段先记账，消费方是 X2 回放（「任意历史时点信念集」）。
**落地点**：`feedback_store.py`。**验收**：老记录缺字段兼容；render 输出逐字不变（parity）。**依赖**：无；X2 的前置。

### M4 · 注入打分制 + 引用晋升（触发式后排：active lessons > cap 才启用）

**动机**：现排序 = confidence → last_reinforced → scope 精度；cap=8 未绑定时改它是空转，绑定后 confidence-first 会挤掉「低 conf 但正相关当日场景」的条目。

**机制**
- 复合分 = `importance`（写入时 Claude 打 1-10，新字段）× `recency`（沿用 decay 后 conf 的时间项）× `relevance`（当日 regime/lane/行业与 lesson scope+regimes 的重合度，纯结构化）。
- **引用晋升**（FinMem）：lesson 被注入且当日卡片/精排引用（触发 ledger 已有记录可当 access counter）→ importance +1、decay 半衰期加长一档；被 MTM support → 同。
- 开关：`render_calibration_block(..., scoring="composite")`，默认旧行为。

**落地点**：`feedback_store.py`（打分）+ 触发 ledger 读取。**验收**：hits≤cap 时输出与旧逐字一致；>cap 时 A/B 对比注入集合差异有日志。**依赖**：M1（库先长大）。

### M5 · 记忆园丁（触发式后排）

**机制**：败绩 importance 累计超阈值（GA 反思触发式，非固定日程）→ 独立「园丁」session：合并同族（交 M2 的 UPDATE）、把 ≥3 条同主题低层 lesson 升维成一条带证据指针的高层洞见（新条 evidence 指向子条 id，子条 `superseded_by` 指回）。产物全走 M2 裁决 + changelog 审计。
**落地点**：`scan-retro` skill 增园丁段（或独立 skill 命令）；无新模块。
**验收**：园丁跑后 active 条数不增反降或持平、探针（M6）得分不降。**依赖**：M1/M2/M3，且库 ≥15 条再启用。

### M6 · 记忆探针评测（后排）

**机制**：`context/knowledge/probes.jsonl` 固定探针集，两能力（LongMemEval 分型裁剪）：**知识更新**（"X 的 CFO 门前科？"——新证据落库后旧答案应被取代）与**弃答**（"无前科票问前科"——不得编造）。scan 后由 stage_eval 段抽查 3-5 条，Claude 自答自评，得分入 `stage_eval` 账。
**落地点**：`autoresearch/learning/stage_eval.py` 增 probe 段。**验收**：探针分随 M1-M5 落地可追踪；shadow NAV 测钱、探针测忆，互补成对出现在 summary。**依赖**：库 ≥15 条。

---

## 轴二 S：A股策略

### S1 · 市场温度计（直击 range 0 买供给缺口）

**动机**：连续 10 日 0 买的第二层根因 = range 市 swing/趋势品相缺供给；现 regime 三块（risk_off/range/trend）是宽基/广度口径，缺**短线情绪维度**——冰点/修复段的超跌反弹品相从未被激活。

**机制**
- 数据：tushare `limit_list_d`（涨停/跌停/炸板明细，高权限 token 可回填历史）。**勿用 akshare 涨停池**（走 push2ex host，大概率同 push2 被封）。
- 5 个日频序列：涨停家数 / 连板高度（最高板）/ 晋级率（昨 N 板→今 N+1 板成功率）/ 炸板率 / 昨涨停今溢价（昨涨停池今日均涨幅）。
- 加权合成情绪温度 0-100 + 分段（参考民间口径校准后自定：冰点 <20 / 修复 / 发酵 / 高潮 / 退潮，滞回防抖动，沿用 carryover 滞回惯例）。
- 消费（三处，全 presence-gated）：
  ① `frame.py` market_pack 加 `temperature` 块（**描述性**：数值+分段+近 5 日走向），macro-lite/市场研判可读；
  ② `menu.py` 温度分段→品相菜单切换（冰点/修复 → 启用「超跌反弹」品相入池 quota；高潮/退潮 → 收紧追高品相）——**切菜单不放宽门**；
  ③ L5 研判段允许规范性读法（"温度 15=冰点，历史上修复段 …"）。
- 温度序列本身进 `factor_lab` 验证：温度分段与 fwd_1/fwd_5 市场收益的条件分布（它是择时/regime 变量，不是选股因子——按 regime 校准的口径来，非 IC）。

**落地点**：`autoresearch/data/tushare_source.py`（fetcher 入湖）→ 新 `autoresearch/scan/temperature.py`（序列+合成+分段，纯确定性）→ `frame.py` / `menu.py` 消费。
**验收**：① 历史回填 ≥120 日、与 regime 三块的交叉表（温度是否提供正交信息）；② 不开旗时 frame/menu 输出逐字一致；③ 冰点/修复日影子漏斗对照（沿用 pre_healthy 反事实基建）显示品相切换带来的入池差异。
**成本**：S（1-2 天）。**依赖**：无。

### S2 · Claude 因子挖掘外环（RD-Agent 式，治召回根因）

**动机**：召回错配（06-24 retro：赢家 91% 在池仅 4.8% 过线、composite 日 IC −0.11）是「**因子集固定、只重加权**」的天花板——regime 分块权重已尽力，缺的是新因子供给。RD-Agent(Q)（NeurIPS'25）证明 LLM 提议→回测→反馈回灌闭环每轮 <$10 可行；我们连这 $10 都不用付。

**机制**（外环四步，验证侧全确定性）
1. **提议**：Claude 读上轮反馈包（现有因子 regime 分块 IC + 未解释的 missed-winner 特征），提议受限表达式因子（白名单算子集：加减乘除/rank/ts_mean/ts_std/delay/…，防任意代码）。
2. **验证**：`factor_lab` 现有 IC harness 跑 T+1 + fwd_5 双 horizon、regime 分块、时间外样本（训练窗后留 60 日 holdout）。
3. **去重门**（AlphaGen 防过拟合核心）：与 factor_zoo 内已有因子 |mutual-IC|>0.99 或与现有 composite 相关 >0.95 → 拒。
4. **入库**：`context/factor_lab/factor_zoo.jsonl`（表达式/提议理由/IC 分块/holdout/状态 candidate→validated→champion_member/retired）；进 composite 权重走现有 calibrate_regimes + changelog + 人批。

**落地点**：`autoresearch/research/factor_lab.py`（表达式求值器 + 去重门 + zoo 存取）；提议段挂 `scan-retro` skill（retro 后顺跑）或独立命令。
**验收**：① 求值器白名单外算子拒绝 + selftest；② 每轮反馈包确定性可复现；③ 首月目标：≥1 个因子过 holdout 且对 missed-winner 池的召回率有量化改善（用 X2 回放验证「加入该因子后历史赢家过线率」）。
**成本**：M。**依赖**：X2（回放做召回改善验证，可先用静态 trace 手工验）。

### S3 · 仓位 sizer（组合层最小实现）

**动机**：paper NAV 现在等权记账；买单 0-3 只/日不需要优化器（PyPortfolioOpt/Riskfolio 过重），但「买多少」完全缺席。

**机制**：`size = min(fractional_Kelly, vol_target, liquidity_cap)`——
- fractional Kelly（1/4~1/2 档）：p 用**触价校准修正后的胜率**（cross_calib 已发现自报过乐观 39% 触达），b 用卡片三情景 R:R；
- vol target：单票波动贡献 ≤ 组合目标 vol/√N（N=当期持仓+新买单）；
- liquidity cap：≤ 当日成交额 x%（默认 1%）。
约 50 行纯函数；先只改 paper NAV 记账与卡片展示（"建议仓位"行），不动买单产生逻辑。

**落地点**：`autoresearch/learning/paper_nav.py`（sizer 函数 + 记账）；`assemble.py` 卡片仓位行（presence-gated）。
**验收**：① 纯函数 selftest（边界：p≤1/(1+b) → 0 仓）；② paper NAV 双轨记账（等权 vs sized）对照 ≥20 交易日；③ 无买单日零变化。
**成本**：S。**依赖**：S4（胜率/触价口径先修，否则 Kelly 输入偏乐观——可并行，S3 先用现值+标注）。

### S4 · 回测/触价保真度（Qlib Exchange 规则移植）

**动机**：触价校准已坐实过乐观（tr>0 口径 39% 触达 n=36）；paper NAV/retro forward returns 未建模**涨停不可成交**与**财务数据前视**。

**机制**
- **涨停不可成交**：买单 T+1 开盘一字板（open≈high≈涨停价）→ 记「未成交顺延」或放弃（可配置），paper NAV/影子买单同口径；触价判定同理（目标价当日涨停封死不算真触达）。阈值 ±9.5%（主板）/±19.5%（创业/科创）。
- **Point-in-Time**：财务字段一律按公告日生效（tushare 有 ann_date）；factor_lab 回测与 harvest 的财务因子统一走 PIT 视图。
- 顺手纳入上游 TradingAgents v0.3.x 的 lookahead 修复清单当自查项（窗口中段取数泄漏、prompt 日期锚定）→ 挂 `self_review` lint 或 docs 检查单。

**落地点**：`paper_nav.py` / `retro.py`（成交与触价判定）；`factor_lab.py` + `autoresearch/data/tushare_enrich.py`（PIT）。
**验收**：① 一字板样例的单测（买不进/触不达）；② PIT 前后因子 IC 对比留档（前视污染量化）；③ 老报表 parity（新口径列并存，不改旧列）。
**成本**：S-M。**依赖**：无。

### S5 · 数据扩容两件（先 IC 后进卡）

- **股东户数**（tushare `stk_holdernumber`，季频+部分月频）：公开证据最强的免费因子（源达 2026：RankIC≈−0.062，中小市值最优，医药/传媒/计算机最强；户数降=筹码集中=正 alpha）。流程：入湖 → factor_lab 验 IC（注意季频对齐 PIT）→ 过门才进 L4 卡「筹码集中度」行 + L1 候选因子。
- **行业轮动地形**（北向实时 2024-05 停发的替代）：`margin_detail` 按票聚合申万行业=融资净买入序列；`fund_share`=ETF 份额申赎当 smart money。进 sector pack 两列地形（描述性，喂 sector-brief 地形段）。
**落地点**：`tushare_source.py`/`endpoints.py` + `autoresearch/sector/` pack。**验收**：presence-gated（缺数据行业照常）；IC/条件分布留档。**成本**：S。**依赖**：S4 的 PIT（股东户数季频对齐）。

---

## 轴三 X：交叉（判断层 × 记忆层）

### X1 · 透镜/辩论方校准加权（MARGIN + FinCon；零新采数）

**动机**：L4.5 多空辩论与 PM 三透镜聚合现在听**自报置信度**；cross_calib 已坐实自报不可信（trend lane 高确信被翻案 33% n=52）。MARGIN（2026.05）证明：校准差时置信度加权可劣于随机——**先校准再加权**。调研确认这是开源全域空白（61k★ ai-hedge-fund 也没有）。

**机制**
- `cross_calib.py` 扩：按（视角/辩论方 × regime × 置信度档）聚合历史命中率 → `calib_table.json`（n<10 档标 ⚠ 不加权，沿用 sector_ledger 惯例）。
- 消费：L4.5 PM 聚合段 prompt 注入**描述性校准表**（如"trend lane 高确信档历史被翻案 33%（n=52）"——只给基率，不给折扣指令）；Tier-3 verify.csv 折回评级时同表参考。**不做硬性数值改写**——表是证据，折扣裁量在 PM（保 LLM 判断层地位，机器只供基率）。
- FinCon 延伸（可选二期）：归因粒度从漏斗层细到**分析视角**（资金/估值/行业/席位），lesson 注入只进责任视角 prompt 段，省 cap 名额。

**落地点**：`autoresearch/learning/cross_calib.py` + `buy_ledger.py`（基率源）→ `assemble.py`/L4.5 prompt 组装处。
**验收**：① 表生成确定性 + n 门槛护栏；② 注入 parity（无表回退现 prompt）；③ 前向 20 日对照：加表后翻案率/评级基率变化留档。
**成本**：S。**依赖**：无（数据已备）。

### X2 · 策略回放 harness（确定性层离线重放）

**动机**：proposals 裁决（如 pr_20260702_001 horizon 之争）只能等前向数据；guard 晋升（promotion_candidates → self_review 硬门）无自验证——Voyager 的关键差异恰是「自验证通过才入库」。typed trace 已落全（`trace/L1_scored_full.csv` + `weights_used.json` + `L2_gbdt_top200.csv` + `L3_judged_full.csv`）。

**机制**
- 新 `autoresearch/research/replay.py`：读历史 run 的 trace → 对**确定性层**（L0 门/L1 权重/L2 quota/温度菜单/guard 谓词）施加变体 → 输出 Δ过线集合/Δfinalist 候选/赢家召回率对比（结合 retro 的 forward returns）。
- 三个消费方：① proposals 附回放证据再人批；② guard 晋升前跑「历史误杀率」自验证（误杀率>阈值→不晋升只 advisory）；③ M3 失效字段配合做「历史信念集」重放（若当日仍信旧 lesson 的反事实）。
- **诚实边界**：L3/L4 判断层不可回放（LLM 判断非确定性）——回放结论只对确定性层有效，报告里显式标注。

**落地点**：`autoresearch/research/replay.py`（新）；trace 兼容层（旧 run 缺列时降级跳过并报告覆盖率）。
**验收**：① 同参数回放 = 原 trace 结果（自洽性测试）；② 对既有 open proposal 出一份回放证据报告；③ guard 自验证接进 promotion 流程（advisory 起步）。
**成本**：M。**依赖**：M3（信念集重放那一路）；其余独立。

### X3 · 评测卫生三小件（XS，顺手落）

1. paper NAV 报表加 **Sortino / MDD / buy&hold（沪深300 或全 A 等权）基线行**——StockBench 核心发现：多数 LLM agent 跑不赢 buy&hold，我们要日日直面这一行。
2. 影子/真实/市场三列已有，补**风险调整口径**后 门价值(+3.8pp) 的表述升级为「风险调整后门价值」。
3. lookahead 自查清单（S4 提及）成文落 `docs/`：窗口中段取数、日期锚定、PIT、涨停可成交性——每次 harvest 改动过 self_review lint。

**落地点**：`paper_nav.py` + summary 模板 + docs。**依赖**：无。

---

## 负结果清单（调研结论：明确不做）

| 不做 | 依据 |
|---|---|
| 向量/embedding 检索、常驻记忆服务 | 8 个记忆项目 6 个以向量库为地基，但判断层全是 LLM 读文本；cap=8 + regime/tag 分域下结构化匹配零依赖等效 |
| 打板/隔日溢价/一进二交易信号 | BigQuant/打板实证：2017 后隔日溢价持续衰减、监管+内卷致盈亏比恶化；只进温度计 |
| Qlib Alpha158/360 因子集移植 | CSI500 IC 0.02-0.04 且近年衰减近零；抄的是 Exchange 保真度不是因子 |
| RL/遗传规划因子挖掘整套（AlphaGen/gplearn） | 公认易 p-hacking、样本外衰减快；只借 mutual-IC 去冗余思想，挖掘走 S2 Claude 外环 |
| FinMem 风险人格翻转（亏 3 日翻风险偏好） | 与 0 买纪律正面冲突 |
| ATLAS 式 prompt 自动优化 harness | = R9 已拒的 prompt A/B harness |
| K 线视觉多模态（FinAgent） | 已有结构化因子，边际价值低 |
| TradingAgents-CN 机制移植 | 30k★ 全是工程化/本地化；上游决策日志被 retro+ledger+影子 NAV 超集覆盖 |

## 分期与依赖

```
P0 快赢（数据全备/确定性/直击痛点）      P1 结构件                 P2 触发式后排（条件到才启用）
┌─ S1 温度计 ──────────┐                ┌─ S2 因子外环 ←(验证)── X2 │  M4 注入打分制  ← active>cap
├─ X1 校准加权         │                ├─ S3 sizer ←(胜率口径) S4 │  M5 记忆园丁    ← 库≥15条
├─ M2+M3 写入裁决+失效 ─┼→ M1 同日配对   ├─ S4 保真度               │  M6 记忆探针    ← 库≥15条
└─ X3 评测卫生(半天)    │                ├─ X2 回放 harness ←── M3  │
                       │                └─ S5 数据扩容 ←(PIT) S4   │
```

- **P0 顺序注意**：M2（裁决）先于 M1（配对开闸）落地，防相近条目涌入还债；M3 挂 M2 顺手。
- **S3/S4 关系**：S3 可先行（胜率用现值+"未修正"标注），S4 落地后自动改善输入。
- **X2 是 S2 的验证放大器**：因子召回改善用回放量化，但 S2 首轮可用静态 trace 手工验，不硬阻塞。

## 风险与开放问题

1. **温度计有效性未验证**：五序列合成权重先拍脑袋后校准（≥120 日回填做条件分布）；上线首月只当地形+菜单开关，不进因子权重。
2. **校准加权样本量**：买单 ledger 基率 n 小（0 买常态）→ 按评级档聚合而非按票；n<10 档 ⚠ 不加权。
3. **S2 表达式求值器安全与算力**：白名单算子 + tushare 限频（factor_lab CACHE 惯例沿用；防空 pickle 坑已有前科）。
4. **回放的 staging drift**：旧 trace 列结构随 wave 演进（如 _L3_COLS 42→22）→ 兼容层必须报告覆盖率而非硬失败。
5. **M4/M5 的启用时机**依赖 M1 实际产能——若配对蒸馏月产 <5 条，后排项继续冻结，避免为小库建重机制。

## 调研来源（关键引用）

- 记忆：[Mem0](https://arxiv.org/html/2504.19413v1) · [Zep/Graphiti](https://arxiv.org/html/2501.13956v1) · [Letta sleep-time](https://www.letta.com/blog/sleep-time-compute/) · [ExpeL](https://arxiv.org/html/2308.10144v3) · [Generative Agents](https://ar5iv.labs.arxiv.org/html/2304.03442) · [Voyager](https://voyager.minedojo.org/) · [LongMemEval](https://arxiv.org/abs/2410.10813)
- 交易 agent：[FinMem](https://arxiv.org/abs/2311.13743) · [FinCon](https://arxiv.org/abs/2407.06567) · [MARGIN](https://arxiv.org/pdf/2605.22949) · [StockBench](https://arxiv.org/abs/2510.02209) · [TradingAgents CHANGELOG](https://github.com/TauricResearch/TradingAgents/blob/main/CHANGELOG.md) · [ai-hedge-fund](https://github.com/virattt/ai-hedge-fund)
- A股：[Qlib](https://github.com/microsoft/qlib) · [RD-Agent(Q)](https://arxiv.org/html/2505.15155v2) · [alphagen](https://github.com/RL-MLDM/alphagen) · [QuantsPlaybook](https://github.com/hugo2046/QuantsPlaybook) · [股东户数因子(源达)](https://finance.sina.com.cn/roll/2026-06-08/doc-iniaswmn5452256.shtml) · [北向停发](https://www.stcn.com/article/detail/1203876.html)
