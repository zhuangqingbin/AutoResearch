# Wave5:过程直播 / 主尺对齐+上涨侧 / 宏观接线 / 计量先行(design)

> 2026-07-25 · brainstorm 定稿。起因:用户四点诉求——①扫描各环节过程展示要优雅完整;②天天 0 买且次日不准,问所有环节还有无优化空间;③宏观中观研究要更专业(它们是下游输入);④速度与 token 消耗优化。
> 本设计基于当日四路侦察(过程展示 / 漏斗与战绩 / 宏观中观 / 速度token)+ 账本硬数字,全部 path:line 见各章「现状证据」与附录索引。
> 四章相互独立可分批实施;共同纪律:**回测/影子先行,过账本才接线;不放松任何已证有价值的门**。

## 用户已拍板(2026-07-25)

1. **过程展示**:终端 GATE 检查点直播 + L4 每股进度聚合表 + **每个 L 阶段也直播**(未选 live.md 落盘看板与 HTML 全景页)。
2. **0 买正解**:对齐主尺 fwd_2_oc + 修上涨侧(不放松门,回测先行);未选「每天必出 top1-3」产品形态与「超短情绪/梯队新 alpha 线」。
3. **宏观档位**:接线已有端点 + 跑通 macro full(未选全量分位/delta 框架层与中观行业原生数据建设——后者列二期)。
4. **速度基调**:先仪表化再精准砍;本波只动已坐实的免费两修(prewarm 安装、共享块生产者),大刀全部挂触发条件进「第二刀清单」。

## 目标 / 非目标

**目标**:①主会话从 L0 到 L5 不再静默——8 个检查点把现成确定性产物转播给用户;②把「为什么 0 买」变成可审计账本,并沿「L2 排序负 IC → 上涨侧召回缺链 → 早停误杀」三个已坐实病灶做回测先行的修复;③market_pack 获得真宏观/资金面变量,macro full 一个月零产出的状态终结;④token 消耗从「bytes÷2.8 猜测」变成真计量,并白捡 prewarm 8–10min。

**非目标(硬约束,引用既有裁定)**:
- **不放松买入门**:`assemble.py:558` 的 ≥OW 唯一门槛一字不动。影子组合已证门价值 ≈+4.2pp(真实 −0.24% vs 无门影子 −4.44%,`reports/learning/paper_nav_summary.txt`);07-09 裁定「别再怀疑买入门」。
- **不建当日大涨/event 类召回**:追当日大涨 −4.85pp(t=−13.6)已证伪;event 路首读边际 −1.01pp 已立 pr 取证(≥10 日裁决),裁决前不动(Wave4,2026-07-25)。
- **不动超短 T+2 交易尺**:07-10 用户裁定,全尺对齐 fwd_2_oc,T+5/swing 作废。
- **不做**:live.md 看板、HTML 全景页、每天必出 top1-3 强制排名、中观行业原生数据接入(sw_daily 权限核实列二期前置)、L3 拆分/intel 降默认/双复核降档(进第二刀清单等读数)。

## 总览

```
①过程直播(纯路由,零新计算)          ④计量先行(真 usage + 免费两修)
  8检查点 SKILL.md 契约                  usage_harvest ←─ 裁决②③④后续砍向
  render CLI(渲染器前移)               prewarm 安装 / 共享块修复
        │ 共用 prelude 汇总屏            │
        ▼                                ▼
②主尺对齐+上涨侧(回测先行)          ③宏观接线(market_pack 扩容 + full 跑通)
  A. ic_by_regime 裁决 L2 权重           A. 北向/两融/指数估值分位/行业资金流 → pack
  B. 板块动量路(replay→影子→接线)     B. cron harvest + Stage0 自动补齐 assemble
  C. 早停记账→(取证后)playbook 修订      → macro_state 非空,presence-gate 激活
  D. 守护:双影子账 + pinned 派发硬化
```

依赖关系:①与④B 共享 prelude 汇总屏改动;③A 新块自动进①的 Stage0/GATE1 播报;④A 的真 token 分布是④D 第二刀与②各项成本核算的裁决基础(非阻塞依赖)。四章可并行开工,批次见「实施顺序」。

---

## ① 过程直播:8 检查点 + L4 滚动表

### 现状证据(2026-07-25 侦察)

- 主会话 L0→L5 纯静默:`SKILL.md` 全文只有 Monitor 一行要求(:43-49)与收尾一行(:103),各 stage 无任何播报指令;Monitor 靠文件存在推断 stage(`autoresearch/scan/progress.py:118`),SKILL.md:49 自记两次误报。
- 素材全在但路由错了:prelude 12 步 ✓/✗ 汇总屏(`autoresearch/scan/prelude.py:294-316`)被 `scan-market.js:28`「只回报 stdout 末 15 行」截断;GATE2 处 `g2.meta` 已带逐 finalist name/sector(`autoresearch/scan/gates.py:105-107`)却只 log 了 `finalists=12`(`scan-market.js:136`);`menu.menu_health`(`autoresearch/scan/menu.py:36`)、`_gate_histogram`(`autoresearch/scan/assemble.py:355-373`)、funnel 行、耗时表全部只在收尾 summary.md 渲染;`universe.py:427,458,496`、`frame.py:176-183` 的计数进了 stderr。
- L4 逐股密集日志(`l4-stock.js:43,56,90`)散在 N 棵 workflow 树里,主会话从不聚合。

### 设计

**原则**:只转播已有确定性产物,零新计算、零新 LLM;不建 progress.py 式存在性推断(有误报前科)——「完成」的定义 = 该 stage 的产物本身落盘(卡=L4 单股完成、`_l3_judged.json`=L3 完成)。

**1.1 SKILL.md 新增「过程直播契约」节**,8 个检查点,每个定死素材来源与格式:

| # | 时机 | 必播内容 | 素材 |
|---|---|---|---|
| CP0 | Stage0 完成 | regime + 温度 + 策略师定调句(market_view §1 首句) | market_pack / market_view.md |
| CP1 | GATE1(prelude 完) | **prelude 12 步汇总屏全量** + L1 十路计数 + L2 200 分层构成 + 菜单体检 + prewarm/降级状态行 | `_prelude_summary.md`(新,见 1.2)+ render CLI |
| CP2 | 行业 brief 齐 | K 个行业各一句地形定调(brief 首句) | `sector_briefs/*.md` |
| CP3 | GATE2(L3 完) | **入围名单逐只表**(代码/名称/行业/conv/tier/一句论点)+ bench 计数 + pass1 被切影子名单 | `g2.meta` + `_l3_judged.json` + `_l3_pass1_cut.csv` |
| CP4 | L4 派发 | 派发 N 股清单 + 预算旗(五旗命中情况)+ intel 开关状态 | menu.py 预算五旗 + config echo |
| CP5 | L4 进行中 | 滚动表:每出一张卡播一行(k/N 代码 名称 评级 conv binding一句;intel 完成同理) | `_l4_card_*.md` / `_l4_intel_*.md` 落盘轮询(见 1.5) |
| CP6 | GATE3→assemble 前 | 评级分布直方图 + 早停/满卡计数(接②C 的停因桶) | render CLI `gate_hist` |
| CP7 | GATE4(L5 完) | 买单/0 买判词 + 产物路径 + 分段耗时表 + token 表(④A 就绪后) | summary.md 摘录 + render CLI `timing` |

**1.2 prelude 汇总屏落盘**:`prelude.py` 在打印汇总屏的同时写 `context/scan/<date>/_prelude_summary.md`(逐字同内容);`scan-market.js` 对应步骤改为「log 指路该文件」,主会话 Read 后全量转播。「末 15 行」限制保留给其余 shell 步骤。

**1.3 GATE2 名单 log**:`scan-market.js:136` 改为逐 finalist `log("L3入围 3/10 601899 紫金矿业(有色) conv=78 …")`,一行一只;pass1 被切名单同播(来源 `_l3_pass1_cut.csv`,标「影子」)。

**1.4 render CLI(本章唯一新代码件)**:新增 `autoresearch/scan/render.py` → `python -m autoresearch.scan.render <date> --view menu_health|gate_hist|timing|funnel`,零 LLM,把现有渲染器变成随时可调:`menu_health` 复用 `menu.py:36`;`gate_hist` 把 `assemble.py:355-373` 的 `_gate_histogram` 提为公共函数后复用(assemble 内部改 import,行为不变);`timing` 读 `_stage_timing.json`;`funnel` 复用 summary 的 funnel 行渲染。主会话在 CP1/CP6/CP7 Bash 调用后转播 stdout。
**1.5 L4 滚动表**:主会话在 fan-out 后进入轮询循环(Bash `ls -t context/scan/<date>/_l4_card_*.md` + grep 卡结构化头的 rating/conv 行;间隔 60–90s 或用 Monitor until-loop),对**新增**卡播 CP5 行;全部 N 卡齐或 workflow 通知完成即出循环。卡文件出现 = 该股完成,语义可靠(卡就是产物),与 progress.py 的 stage 推断不同类。
**1.6 stderr 归位**:`universe.py:427,458,496`、`frame.py:176-183` 的计数行并入汇总屏数据(或双写 stdout),消灭「白打进 stderr」。

### 改动清单

| 文件 | 改动 |
|---|---|
| `.claude/skills/scan-market/SKILL.md` | 新增「过程直播契约」节(8 检查点表);L4 段加滚动表指令;**编辑前重读**(skill 文档会被外部改的坑) |
| `.claude/workflows/scan-market.js` | :28 步骤改指路 `_prelude_summary.md`;:136 逐只 log;各相位末尾 log 检查点就绪提示 |
| `autoresearch/scan/prelude.py` | 汇总屏双写 `_prelude_summary.md`(含 prewarm 状态行、③A 降级行) |
| `autoresearch/scan/render.py`(新) | 四 view CLI;`_gate_histogram` 从 assemble 提为公共 |
| `autoresearch/scan/assemble.py` | `_gate_histogram` 迁出后改 import(行为不变) |
| `autoresearch/scan/universe.py` / `frame.py` | 计数行归位 |
| tests | render CLI 四 view 冒烟(真产物 fixture);prelude 双写断言;**变异探针**:把 `_prelude_summary.md` 写空应致 render/断言红(绿灯必须会变红) |

### 验收

一次真实扫描:8 检查点齐;prelude 汇总屏完整可见;GATE2 逐只名单 + pass1 影子名单可见;L4 期间每卡出炉 ≤2min 内播报;全程无 progress.py 推断误报。主会话新增输出估 3–6k token(纯转播,可接受)。

---

## ② 主尺对齐 + 修上涨侧(不放松门)

### 现状证据(账本硬数字,2026-07-25)

- **频率**:24 个 scan 日 17 日 0 买(71%),累计仅 9 笔买单(`reports/learning/journal.md`、`buy_ledger.md`)。
- **0 买方向对但买单零 edge**:13 个 0 买日市场 fwd_2 均值 −1.32%(空仓正确,`zero_buy_ledger.md`);买单 n=9 全 OW,已实现 4 笔 T+2 胜率 50%、均值 −0.32%(`buy_ledger.md:16`,自标 n≥10 才可注入先验)。
- **0 买真机制是早停,不是三门失守**:07-21 12 卡中仅 2 张可被 `gate_status` 解析(07-16:2/13),而 6/12(07-16:7/13)是早停卡——早停按定义压 ≤Hold 且从不写 OW三门段(`.claude/agents/l4-card.md:20`「早停只向下」、`l4_card.py:553-555`);summary 每天照打「N 只 finalist 无一过 ≥OW 三门」是**未被自己数据支持的叙事**;`gate_fires.csv` 当日 binding 行仅 3 只 pinned。早停无任何账本计量。
- **上涨侧盲区实锤(07-21)**:552 只赢家 405 进召回池(73.4%)、0 被买(`retro/retro_input.md:4-7`);分桶 missed_l1 295 / missed_l0 147 / recalled_cut 98 / pass1_cut 12;missed_l1 中 62% 主力并未净出、只是 composite 排到 2000–4000 名(:154);188 只科技大涨票 127 未召回、49 止步 L1(L1 序位中位 283 而 L2 只收 204);6 只 pass1 切掉票当日 +10~15%,其 fwd_2_oc 裁决挂在 07-23 retro(:156,**尚未跑**);唯一真选涨停票金禄电子被 L4 以「涨停日追高」压 Hold,次日 +4.0%(z=+3.0,t1 scorecard)。
- **排序尺与主尺反向**:07-21 `retro/stage_eval.csv`:L2 `ic_l2_score_t1` **−0.2225**;L4 拒绝侧 `ic_rating_t2` **+0.318**(全系统最好的判断信号);`context/factor_lab/weights.json` meta(117 日,fwd_2_oc):momentum **−0.0325**、fund_main −0.0113,仅 value +0.0245 为正——权重面板结构性压制上涨侧。
- **既有裁定边界**:追当日大涨 −4.85pp 已证伪;「上涨板块侧未被否」(07-17 裁定的被否范围仅防御/跌势侧);52周高 家族内 pct_60d 胜出(t+2.62)是 momentum 换代候选(07-18 优化波)。

### 设计(四条腿,沿漏斗因果顺序)

**2.A L2 排序对齐 fwd_2_oc(ic_by_regime 裁决)**

- 新增 factor_lab 报表 `ic_by_regime`:对 composite 及全部分项因子(momentum/pct_60d/52周高/fund_main/value/…),在 lake 全历史(非仅 117 日面板)上按当日 regime(journal 的 regime 列)分层,算逐日截面 IC(对 fwd_2_oc)+ t 值 + 汇总。输出 `context/factor_lab/ic_by_regime.csv` + md 报表。
- **裁决规则**:某分项在某 regime 桶 |t|≥2 才有资格进入该 regime 的权重条件化提案;momentum 全期负权重可能是 risk_off 均值掩埋了 trend/反弹日的正信号——**先分桶裁决再动权重,禁止直接全局调参**。
- 顺带正式裁决 **pct_60d 换代 52周高**(同表出数,先验 t+2.62)。
- 生产权重改动仍走现有 recalibrate 腿(含 07-16 后加的活性探针,防 NO-OP 空转复发);regime 条件化若过裁决,以 config 提案形式呈报用户点头后启用。
- 风险:regime 分桶后样本变薄(117 日 → 每桶 ~40)——用 lake 全历史回放缓解;仍薄的桶结论标「样本不足」不采纳。

**2.B 板块动量召回路(replay → 影子 → 接线,三阶段)**

- **病灶**:上涨板块内的票被全市场 composite 压在 L1 序位 200 开外(07-21 的 49 只、median 283)。
- **路定义**(与已证伪的追涨严格区隔):触发特征是**板块 5–10 日上涨结构**(申万一级板块聚合收益进入 red 榜 top3,或连续 2 日 top5),**非个股当日涨幅**;个股侧取该板块内 L1 综合序位前 30,且**排除当日涨幅 >7% 的票**(把「追当日大涨」特征显式挡在门外)。配额:每板块 ≤5、全路 ≤15,进入 L2 后仍受 `sector_cap_frac=0.20`(`l2_stratify.py:55`)约束。
- **实现**:L1 新增具名路 `sector_momentum`(默认 disabled,与 accumulation/northbound/event 停用位同层,`scan_config.jsonc:47`);**Wave4 教训写死:默认不启用 = 连副作用一起不启用**——阶段 1/2 期间不得改动任何生产 L2 构成、不得加 floor。
- **阶段 1(replay 历史裁决)**:用漏斗回放器(PIT 六条防线)在历史每日 `L1_scored_full.csv` 上模拟该路选票,记账:选入票 fwd_2_oc vs 若接线将被挤出的票 vs 全市场;**全样本边际为正且 t≥2** 才进阶段 2。
- **阶段 2(生产影子)**:每日 prelude 计算该路选票落 `_sector_momentum_shadow.csv`(floor=0,不进 L2),影子账本累计 ≥10 交易日。
- **阶段 3(接线)**:影子账本边际仍为正 → 提案改 `l2_stratify` floors,**须用户点头**。任一阶段负结果即关闭议题并记负结果档案(防重做)。

**2.C 早停记账 →(取证后)强势票规则修订**

- **先记账(本波实施)**:
  - l4-card 卡结构化头新增 `early_stop: {phase: P1|P2|P3, reason: <enum>}`,reason 枚举:`数据不足 | 涨停追高 | 题材透支 | 资金流出 | 估值透支 | 基本面恶化 | 其他`(agent def `.claude/agents/l4-card.md` + lite-playbook 模板同步;注意 agent def 会话装载、下 session 生效)。
  - `assemble.py` 卡解析器扩展该字段入 `_final_ratings.json`;新增账本 `reports/learning/earlystop_ledger.md`:按停因桶 × fwd_2_oc 分布(与 buy_ledger 同产线挂新表)。
  - t1_review 扩样:scorecard 对早停卡也算 cc1/oc1 落「早停桶」区(不判准/不准——Hold 无方向主张,只记分布);解决「最近 5 次 t1 里 3 次全 Hold 无从判准」的样本饥饿。
- **后修规则(触发条件挂账)**:≥10 个交易日后,若「强势票停因桶」(reason ∈ {涨停追高, 题材透支})fwd_2_oc 均值 >0 且 t≥1.5 → 修订 playbook 该条(涨停日不因当日涨幅本身早停,须看结构位:梯队位/板块位/封单质量,由 intel 供给);否则维持现规则并记负结果。阈值低于 ②A 的 t≥2 是有意的:playbook 属可逆指令改动且自带 ②D 影子对照,权重属生产打分尺,门槛应更高。
- **保底红线**:L4 拒绝侧 IC +0.318 是全系统最好的信号——只动强势票子桶的早停判据,**不动整体早停机制、不动「早停只向下」原则**。

**2.D 守护与顺手修**

- **双影子账**:②B 影子(阶段 2)+ ②C 拟放行票影子(若修订后会放行的卡,记其 fwd_2_oc)——每一刀都有 counterfactual,延续 paper NAV 的裁决方法论。
- **pinned SELL 双复核补漏**:07-21 实锤 300857/601869 评级偏空但 `_ensemble_*.json` 缺失(⑤-3 漏传 `args.pinned`)。修法:SKILL.md L4 派发段硬化「派 l4-stock 必传 `args.pinned`」+ `assemble.py` 断言:pinned 票为 UW/Sell 而 ensemble 产物缺失 → GATE4 warn(不静默)。
- **0 买叙事纠偏**:summary 的 0 买判词从「无一过 ≥OW 三门」改为按真机制分桶陈述(早停 N 张〔按停因〕/ 满卡未达 OW M 张 / 三门拦截 K 张),数据源 = ②C 新字段。修掉「未被自己数据支持的叙事」。

### 验收(≥10 交易日观察窗)

1. 上涨日(市场 fwd_2>0)买单出现率与买单 fwd_2_oc 边际 vs 现状基线(基线:71% 0买、买单均值 −0.32%)。
2. `ic_l2_score_t1` 不再显著为负(或 regime 条件化提案已过裁决呈报)。
3. 拒绝侧 `ic_rating_t2` ≥ +0.25 不劣化(红线)。
4. earlystop_ledger 有 ≥10 日数据,强势票停因桶裁决出结论(正/负都算完成)。
5. ②B 阶段 1 replay 报告产出,结论明确(进阶段 2 或关闭议题)。

---

## ③ 宏观:market_pack 扩容 + macro full 跑通

### 现状证据

- market_pack 仅 6 块 24 标量,全部来自 tushare 全 A 个股快照横截面自聚合(`market.py:243` + `:57/:69/:80/:138`),**零真宏观变量**(无利率/汇率/商品/北向/两融/期权波动率/指数估值分位/成交额/涨跌家数/ERP/AH 溢价)。
- `macro_state` 恒 None:`context/macro/` 最新 2026-06-22 且只有 `data.md` 原料,**无 `1_spine/decision.md`** → macro assemble 从未跑通 → 一个月来 market_view 开篇均写「无新鲜宏观视图」;presence-gated 注入(`state.py:85`,TTL 7 天 + regime 双失效)从未激活。
- market_view 写作达标、洞见密度低:90% 是 24 个数字的中文化复述(07-16/07-24 样张)。**差距构成 ≈ 缺数据 70% + 缺框架 30%,不缺写法**。
- 端点已实现未接线:`index_dailybasic`(指数 PE/PB 含近 1 年分位)、`moneyflow_hsgt`、`margin`、`moneyflow_ind_ths` 四者只活在从未跑通的 `macro/tushare_macro.py`;另有 `stock_restricted_release_queue_em`(07-24 market_view 第 5 节解禁数字靠网查而端点已实现)等一批闲置(附录)。
- 下游消费链健全无需改:L3 只读 §1-3(`l3-rank.md:14`)、L4 7 行确定性块(`market.py:325-349`)、L5 整篇(`assemble.py:715`)、防锚定守卫(`self_review.py:379-391`)。

### 设计

**3.A market_pack 扩容(纯接线,零新数据源)**

- 新增两块:
  - `cross_money`:北向净流入 T-1 及 5 日累计(`moneyflow_hsgt`)、两融余额及 delta(`margin`)、行业资金流 top/bottom5(`moneyflow_ind_ths`)。
  - `index_val`:上证/沪深300/中证500/创业板 PE/PB + 近 1 年分位(`index_dailybasic`,分位算法沿 tushare_macro 已有实现)。
- `breadth` 补涨跌家数与成交额(frame 现有截面直接可算,零端点)。
- **实现位置**:四个取数函数从 `macro/tushare_macro.py` 抽到公共层(`autoresearch/data` 或 `dataflows`),scan 与 macro 双方 import,消灭平行实现。
- **契约与缓存**:全走 lake 日频缓存(**写湖剥 fields**,窄表毒化坑);B 级契约——任一端点失败该块置 null + `_degraded` 记账行,prelude 汇总屏(CP1)显示降级状态,**降级必留痕**(A 级空即抛/B 级降级记账的既有裁定)。
- **模板小改**:macro-playbook 六小节的 §2 资金面,若 `cross_money` 非 null 则定调句必须引用 ≥1 个北向/两融变量(macro-brief agent def 同步;下 session 生效)。
- **权限 spike**:`moneyflow_hsgt` 等接口的 tushare 积分门槛在批 2 开工首日核实(高权限 token 已具备的先验:memory「高权限 token 可拉全市场历史」;若个别接口仍不可用 → 该块降级记账,不阻塞其余)。

**3.B macro full 修通 + 每周自动补齐**

- **Spike(半天)**:跑一次 `python -m autoresearch.macro.harvest` + `assemble`,复现 06-22 以来 decision.md 从未产出的断点,修通(骨架 S1-S5/M1-M4/A-D 与 `parse_allocation` 均已就位,预期是小修)。
- **排程**:launchd 每周日 20:00 跑确定性 harvest(cron 只做取数,LLM 节不无人跑)。
- **LLM 节自动补齐**:scan-market Stage 0 新增一步——检测「`context/macro/<latest>/data.md` 新鲜(≤7 天)且 `macro_state.json` 缺失/过期」→ **并行加派一个 macro full 组装 agent**(macro-research full 档的 LLM 节,产物落 `reports/macro/` + `macro_state.json`),不阻塞 GATE1 主漏斗。macro_state 从此每周自然刷新一次,market_view 的 presence-gated 注入零改动激活。

### 改动清单

| 文件 | 改动 |
|---|---|
| `autoresearch/data`(或 dataflows) | hsgt/margin/index_dailybasic/moneyflow_ind_ths 四函数公共化(自 tushare_macro 抽出) |
| `autoresearch/scan/market.py` | `market_pack_from_frame` 加 `cross_money`/`index_val` 两块;breadth 补两标量 |
| `autoresearch/macro/tushare_macro.py` | 改 import 公共层(行为不变) |
| `.claude/skills/macro-research/references/macro-playbook.md` + `.claude/agents/macro-brief.md` | §2 资金面引用要求(presence-gated) |
| `.claude/skills/scan-market/SKILL.md` + `scan-market.js` | Stage0 加 macro 补齐检测与并行派发 |
| launchd plist(新) | 周日 harvest |
| tests | pack 新块契约测试(null 降级路径 + `_degraded` 记账断言);分位计算对拍 tushare_macro 既有实现 |

### 验收

连续两周:market_view 开篇不再出现「无新鲜宏观视图」;pack 含 `cross_money`/`index_val` 且降级记账可审计;`reports/macro/` 出现本月首份 full 报告;L4 的 7 行确定性块与 L3 §1-3 消费不变(防锚定守卫测试保持绿)。

---

## ④ 速度/token:计量先行 + 免费两修

### 现状证据

- **wall-clock**(`_stage_timing.json` 七次跑,mtime 下界):全程 07-21 2367s(39min,intel 误关)→ 07-17 6662s(111min,intel 开);L3 精排纯串行 1129–1709s(19–28min,占总 30–49%);intel 使 L4 段 335s → 2551s(7.6×);L0L1L2 73–629s。
- **token 无真计量**:OTEL 从未实跑(`STAGES.md:263` 自述;`telemetry.py` 零生产调用点、全仓无 `token_telemetry.md` 实例);唯一读数 = 落盘字节 ÷2.8 下界(`assemble.py:500-536`):07-21 合计 ~154k(L3 57k/37%、L4 卡 48k/31%、slim 40k/26%),**未计** intel、全部 WebSearch、每 subagent ~15k 系统前缀、ensemble 复核;官方自估真实 ~1M/次(`STAGES.md:264`;`fb_20260704_001` open)。
- **两个「已做优化」疑似没在跑**:
  - `_l4_shared_instructions.md` **无生产者**(全仓只有 `l4_card.py:723-728` 读它 + 测试写它),07-17/07-21 均不存在 → 共享块实际只剩 t1 校准块(`l4_card.py:665-669`);
  - cache 契约疑似空转(**推断,未实测**):派发走「给路径让 agent 自己 Read」(`l4-stock.js:51`),共享块以 tool_result 身份出现在逐股发散的 user message 之后,byte-identical 前缀契约(`test_l4_prompt_cache_prefix.py:32`)对真实 API 前缀几乎不起作用;命中率无读数。
- **prewarm 未安装**:`launchctl list` 无 scan-prewarm、`_prewarm.json` 全历史仅 07-10/07-13 两次 → 每跑白付 8–10min 取数。
- 相关 open 欠账:`pr_20260714_007`(intel 实测 24 查询 vs cap 15,限频形同虚设)、`pr_20260721_001`(config-echo 探针)、`fb_20260704_002`。

### 设计

**4.A 真计量(usage_harvest,含 spike)**

- **Spike(首日,半天)**:验证本机 workflow transcript(journal.jsonl / agent-*.jsonl)中 per-agent usage 字段(in/out/cache_read/cache_write)可稳定提取——memory 已有 jq 抽取配方家族可复用。
- **落地**:
  - 派发时记录:scan-market 主会话把本次各 workflow 的 runId/transcriptDir 记入 `context/scan/<date>/_workflow_runs.json`(SKILL.md 派发段加一行)。
  - 收尾:新增 `python -m autoresearch.trace.usage_harvest <date>`(确定性)→ 读 `_workflow_runs.json` 指向的 transcripts,产 `reports/scan/<run>/token_usage.md` + csv:agent × phase × in/out/cache 命中,含全场合计与「本表覆盖率」行(**产物能证明跑过什么、不能证明没跑过什么**——未覆盖的 agent 显式列缺)。
  - 接入 CP7 播报(①)。
- **OTEL 裁决**:按 `STAGES.md:263` env 实跑一次;与 usage_harvest 对拍,**谁能给出 cache 命中率谁留任**,另一个退役(避免双计量)。若 spike 失败(transcript 无 usage),OTEL 转正。

**4.B 免费两修**

- **prewarm 安装**:核实 plist(缺则补写)→ `launchctl load`;prelude 汇总屏加状态行「prewarm: ✓/✗ + 上次 mtime」(接 CP1,让「装了没跑」当天可见,不再靠事后考古)。预期 L0L1L2 段 −8~10min。
- **共享块修复**:l4-prep 相位(scan-market.js 4 生产者段)确定性生成 `_l4_shared_instructions.md`(内容 = lite-playbook 共享段拼装);`l4_card.py` prompt 组装改为**内联**该内容于逐股 prompt 最前(byte-identical 前缀:固定标头 ≤300B → 共享块 → 逐卡块,恢复契约本义),`l4-stock.js:51` 不再传路径让 agent Read;`test_l4_prompt_cache_prefix.py` 改锁「l4-prep 产物存在 + N 股 prompt 前缀逐字节相同」。修复效果由 4.A 的 cache 命中率读数证实(闭环)。

**4.C 第二刀清单(本波不动,挂触发条件)**

| 候选 | 触发条件(动刀前必须有的读数) | 预期收益 |
|---|---|---|
| L3 精排拆两段/降 effort(max→xhigh) | token_usage 证实 L3 占比 ≥30% 且卡质量样本(fb_20260704_002)有基线 | −8~14min,token −15~25% |
| intel 降默认/限频压实(pr_20260714_007) | intel A/B 账本 ≥10 日结算 + usage 证实 intel 真实成本 | L4 段最多 −36min |
| ≥OW 双复核降档(2 满价 Opus → 1 满价 + 1 低 effort) | 买单样本 n≥10 且双复核翻案率有读数 | 每买单 −1 张满价卡 |
| L3 紧凑表再瘦(24KB/86 行) | token_usage 证实 L3 输入占比 | 边际小,末位 |

### 验收

下次扫描:`token_usage.md` 产出且覆盖率行明确;cache 命中率首次有读数;prewarm 状态行 ✓ 且 L0L1L2 wall 下降可见;共享块前缀测试绿(锁生产者)。两周后:以真分布数据评审 4.C 触发条件,决定第二刀。

---

## 实施顺序

- **批 1(纯确定性,~1–2 天)**:①全部 + ④B(prewarm、共享块)+ ②C 记账(卡头 schema/解析/账本/t1 扩样)+ ②D(pinned 派发硬化、0 买叙事纠偏)。
- **批 2(接线+计量,~2 天)**:③A pack 扩容(含权限 spike)+ ③B full 修通与排程 + ④A usage_harvest(含 spike)。
- **批 3(离线研究,不改生产)**:②A ic_by_regime + pct_60d 换代裁决;②B 阶段 1 replay。
- **触发式(账本成熟后)**:②B 阶段 2→3;②C playbook 修订;②A 权重条件化提案;④C 第二刀——每项动刀前须满足各自触发条件,且改生产行为的都需用户点头。

### 裁决点日历

| 日期(约) | 裁决 |
|---|---|
| 立即 | 07-23/24 retro 补跑 → 6 只 pass1-cut 票 fwd_2_oc(上涨侧盲区首个直接证据);pinned 三只 07-25 已到期需续期 |
| +10 交易日(~08-08) | ②C 强势票停因桶;②B 阶段 2 影子账本;event 路 pr 取证到期;intel A/B 结算 |
| +2 周 | ③ 验收(market_view 非空宏观);④C 第二刀评审(基于 token_usage 真分布) |
| 买单 n≥10 时 | 双复核降档评审;评级基率注入解禁 |

## 风险与坑位备忘(实施必读)

1. SKILL.md / playbook 会被外部改:**编辑前重读**(skills-altitude-refactor 坑)。
2. agent def(l4-card/macro-brief)改动**下 session 才生效**:批 1 改完后首次验收扫描须在新 session 跑。
3. 写湖一律剥 fields(窄表毒化);新端点接入走 lake 时同坑。
4. B 级降级必记账(DataContractError 不得被吞);③A 的 `_degraded` 行是验收项不是装饰。
5. `pytest|tail` 吞退出码;新测试跑法沿 repo 现约定。
6. 变异探针纪律(Wave3.5):每个新守卫问一句「删掉被守内容,测试会红吗」;①的 render/双写、④的前缀测试都要有会变红的断言。
7. ②B/②C 的一切「默认不启用」= 连副作用一起不启用(Wave4 floor 教训);影子文件不得影响任何生产输入。
8. 「文件不存在」是弱证据(slim-path 坑):④A 覆盖率行必须显式列缺,不得以缺推断零。

## 附录:侦察证据索引(2026-07-25 四路)

- **展示路**:SKILL.md:43-49,:103;progress.py:118;prelude.py:294-316;scan-market.js:28,:136;gates.py:105-107;menu.py:36;assemble.py:355-373;universe.py:427,458,496;frame.py:176-183;l4-stock.js:43,56,90;telemetry.py:143-158;STAGES.md:263。
- **漏斗路**:rating.py:18;l4_card.py:521,:530-556,:563,:723-728;l4-card.md:20;assemble.py:90,:142,:337-352,:558,:561-567,:879-896;frame.py:28-38,:118;scan_config.jsonc:26,:31,:47,:66;l2_stratify.py:36,:40,:55;l3_select.py:170-250,:708-726;menu.py:103-154;STAGES.md:62-75,:207,:210,:259;retro_input.md:4-7,:154,:156;stage_eval.csv;factor_lab/weights.json;buy_ledger.md:16;zero_buy_ledger.md;paper_nav_summary.txt;journal.md。
- **宏观路**:market.py:57,:69,:80,:138,:243,:325-349;frame.py:184-189;macro-brief.md:14-24,:30;macro-playbook.md:78-92;l3-rank.md:14-15;assemble.py:674,:715;self_review.py:379-391;macro/harvest.py:52-73;macro/assemble.py:27-72;macro/state.py:85;sector/pack.py:61-173,:179;sector/reuse.py:45-73;sector/brief.py:19-21,:58;data/endpoints.py 闲置清单(hk_hold/margin_detail/block_trade/restricted_release/pledge_stat/stk_holdernumber/cyq_perf/fred 30+/polymarket/同花顺一致预期);sector-research/SKILL.md:34。
- **速度路**:stage_timing.py:33;assemble.py:500-536;summary.md(20260721_2227):225-234;STAGES.md:215,:263-264;l4-stock.js:40,:51-52,:71-74;l4_card.py:665-669,:734;test_l4_prompt_cache_prefix.py:32;dossier/schema.py:19;scan/dossier.py:85;assemble.py:626;open 欠账 fb_20260704_001/002、pr_20260714_007、pr_20260716_003、pr_20260721_001。
