# Wave10 设计稿 —— 报告运营优化 × 退役瘦身 × 0买归因(诊断+影子实验+报告出口)

> **状态:已批设计,零实施**(2026-08-01 brainstorm 三问三答定向;用户明确"不做开发,落详细开发文档")。
> **调度权威自本稿接棒**;Wave9 批 C/D/E/F(期权双层/自学习章节/trace 重组/单工作流全链)**维持原稿调度、不收编**,见 §7。
> 沿革:Wave9 批A/B 已上线(`fcadbd1`,2255 绿);本稿基于 **2026-07-31 实跑**(`reports/scan/20260731_2132`)与闭环账本立案。
> 实施纪律:动工前先读 §8 开放问题做一次冒烟裁决;每项验收见各节;**立案诊断≠事实,标了 premise-check 的必须先查再修**(07-28 教训:立案诊断动工一查 4/4 全错)。

---

## 0. 裁定记录(2026-08-01,均用户拍板,实施时以此为准勿重问)

| # | 裁定 | 内容 |
|---|---|---|
| R1 | 文档形态 | **单一 Wave10 统一稿**接棒调度权威;Wave9 C/D/E/F 不收编只交叉引用 |
| R2 | 清理档位 | **激进档**:零调用残件 + 活着但无人消费的功能 + **回滚杆一并纳入评估**(回滚杆必须先过稳定判据门,证据不够只标注不删) |
| R3 | 0买章立场 | **诊断 + 注册制影子实验 + 报告出口**;不改生产门。EXP-1 用「主力 5 日持续性」作影子口径、C3-2 FALSE 弃权上浮 summary,均已确认 |
| R4 | 继承裁定(不重议) | fwd_2_oc 超短主尺(07-10)· 不碰防御/跌势侧轮动(07-17,上涨侧未被否)· L4 TTL 复用退役(07-29)· intel cap 数值不重开(Wave9 §0)· 0 BUY 不是失败也不是放松门的理由(实验治理铁律) |

---

## 1. 证据快照(全部实测,来源标注;复核时对本节取 diff)

### 1.1 大账(截至 2026-07-31)

- **30 个扫描日,23 个 0 买日,6 个买日各 1 只**(06-18/06-22/06-30/07-08/07-10/07-14);当前连败 10 日(07-15 起)。〔journal.md〕
- 0买日市场 fwd_2 均值 **−0.96%**(主尺)→ 市场层面弃权方向正确。〔zero_buy_ledger.md〕
- **因果账 v2(shadow_buys 口径,主)**:已裁 8/8 日 **CORRECT 0 · FALSE 3 · NEUTRAL 5**,全部 DEGRADED。FALSE 三日:07-16(000651/002563/300628)、07-17(600285/688578)、07-21(600188)。〔abstention_ledger.md〕
- **paper NAV**(至 07-31):真实 **−0.24%**(9 笔)vs 影子(门不拦最想买3只)**−3.16%**(81 笔)vs 影子 sized **−5.86%** vs 市场等权 **−9.85%** → 门的总量价值 ≈ +2.9pp。〔paper_nav.md〕
- **「主力真在」门**:15 日拦 63 次,被拦票 fwd_2 均值 **−1.33%**,**拦对率 57% / 错杀率 39%**。〔gate_ledger.md〕
- L3 无选股 alpha:finalists −0.39pp/2日,t≈−1.2;菜单内任何确定性分数无信号(composite 价值在 5500→200 收口,不在 200 内排序)。〔memory 2026-07-12,12 日面板〕
- 板块动量前向 IC **+0.1155**,12/17 日正,t≈+1.46 不显著且单相位;板块超额量级 +4~9pp,大过个股 alpha 一个量级。〔memory 2026-07-17,17 日面板〕
- 追当日大涨已证伪:07-21 当日 ≥9.5% 的 350 只 fwd_2 超额 **−3.67pp(t=−11.9)**,科技 170 只 **−4.85pp(t=−13.6)**。〔Wave4 实证〕
- 目标价过乐观:近 30 scan 日全卡触达率 **39%**(n=36),中位目标 +6% vs 中位 MFE +3%。〔buy_ledger 📐 行〕

### 1.2 2026-07-31 实跑切面

- 哨兵判「材料枯竭」(全市场健康上涨 1.9% < 3%),被 `force_full` 覆盖拉满:10 只 finalist,**Hold 5 / UW 5,0 买**;7 只非持仓全落 Hold/UW = 机器预判成立。成本 **$34.48 / 113m44s**。
- 停因:早停 4(资金流出 2/题材透支 2)· 满卡未达 OW 6;OW 三门失守(7 卡可解析):**主力真在 ✗4** · 业绩 ✗1 · 估值 ✗2。
- 菜单体检:L2 落刀面 **74%** vs 全市场 47%;健康上涨 15/203(全市场 80/4103)。
- **920179 复核事件**:ensemble `[Underweight, Sell, Sell]` 中位 Sell、trigger=sell_review、spread=1 —— 单向阀按设计不折回、spread<2 不出人裁行,**报告零痕迹**(`_ensemble_920179.json` 已核;折回逻辑 `decision_finalize.py::_apply_ensemble_fold` 已读源确认非 bug)。
- 探针假阳 2/2:price_claim 把「主力占比 3.7%」「板块存储指数 +6.6%」当股价断言对账(07-30/07-31 各一条)。
- market_view 防锚定泄漏连续两日复发(07-30 白色家电 → 07-31 证券Ⅱ);**根因已验**:喂策略师的 market_pack 本身含 `sector_healthy_top3`(`market.py:156,300`)——指令级防线(playbook 说别写)挡不住数据在场。
- intel:6/10 稿自报 21–39 条 > cap 20;**自报 39 > hard_cap 30 却零拒稿/裁稿痕迹,且当日零 `.pretrim.md`**(与 Wave9B「超帽改按时效裁剪+pretrim 留档」的记载不符)→ 见 A5 premise-check。
- 网络瞬时错 ENOTFOUND ×3(000538 一 stage、601818/603170 的 intel);task 按设计 SUCCEEDED,但两张卡情报面变薄且报告不可见。
- Wave9 两项 parked 仍在:公告兜底串行无缓存(203×1.56s≈L3 +5min)、插队建档回执打入队数非真实新增数。

---

## 2. A 节 · 报告与流程优化(10 项)

> 通用验收:改动带变异探针(把修复撤掉对应守卫必须变红);涉及 workflow/呈现层的用真实产物冒烟,不许只看单测绿(wave35 教训:`node --check` 对 workflow js 零鉴别力)。

### A1 · 复核分歧透明化(sell_review 单向阀曝光)

- **证据**:§1.2 920179 事件。折回逻辑正确(救误卖持仓,Wave1 ⑤-3),**不可见**是缺陷。
- **设计**:不改 `_apply_ensemble_fold`。新增展示规则:`pinned=true ∧ trigger=sell_review ∧ median≠卡面评级` 即出行(注:ratings 含卡面自身,median≠卡面时 spread 必然 ≥1,故无需再设 spread 门槛;现行 `_ensemble_flag` 的 spread≥2 人裁语义不动):
  - 持仓表该票行追加:`⚠️ 复核 3run=[UW,Sell,Sell] 中位 Sell,单向阀未折回`;
  - 对应 detail 卡头同款一行。
- **落点**:`decision_finalize.py`(新增 pinned 专用 dissent 判定,不动 `_ensemble_flag` 原语义)+ `report_sections`/`publisher` 呈现。
- **验收**:用 07-31 真数据重跑 assemble,920179 行出现;非 pinned spread=1 不出行(不扩大噪声);变异探针=注释新判定后守卫变红。

### A2 · 哨兵中间档 `sentinel_pinned`

- **证据**:07-31 哨兵被 force_full 拉满,$34/113min,7 只非持仓全 Hold/UW。哨兵语义缺口:它只答「今天有没有值得买的」,不答「持仓要不要动」→ 现状二选一(全跳/全跑)都不对。
- **设计**:workflow 三态:
  - `sentinel`(现状):跳 L3/L4,assemble 收尾;
  - **`sentinel_pinned`(新)**:哨兵日 ∧ pinned 非空 ∧ 未显式 force_full → 跳行业 brief/L3/非持仓 L4,**只跑 pinned 全链**(l4-prep 走 pinned-only dispatch-plan)+ assemble;报告醒目标注「哨兵档:仅持仓复核,无选股结论」;
  - `force_full`(现状语义不变):显式覆盖仍全跑。
- **预估**:低产日省 ~$20 / ~70min(L3 $6.6 + 7 张非持仓卡 ~$13 + 行业 brief 份额)。
- **落点**:`scan-market.js`(哨兵分支)+ `l4_tasks`/dispatch-plan 的 pinned-only 入口 + SKILL 2.2 节文案同步。
- **验收**:桌演三态各一遍;`sentinel_pinned` 下 `_l4_tasks.json` 只含 pinned;GATE4 self_review 对「哨兵档无 buy-list」不误报;**性能开关不拥有评级**铁律不破(pinned 卡的 rubric/双复核逐字节同 full 路径)。

### A3 · price_claims 探针口径收紧

- **证据**:§1.2 假阳 2/2;「天天报警」磨探针公信力(同 intel cap 前科,狼来了效应)。
- **设计**:`price_claims.py` 断言抽取加上下文过滤——仅当 % 数字的语境为**该票股价涨跌**(收盘/涨幅/跌幅/盘中±邻近票名或"股价")才进对账;显式排除:占比/净比/指数/板块/行业资金/仓位/持股比例语境。保底:不确定语境**不对账不告警**(宁漏勿假阳,探针是 advisory)。
- **验收**:07-30/31 两条假阳在新口径下静默;**注入一条真错价(改卡内真实涨幅数字)仍被逮**(变异探针);回归近 10 日历史卡,新口径告警数下降且无已知真阳丢失。

### A4 · market_view 防锚定数据级切断

- **证据**:§1.2,根因已验(数据在场,指令防不住)。
- **设计**:`frame`/`market.py` 出**策略师专用 pack 变体**:剥 `sector_healthy_top3` 及其余 L5 专属字段(实施时盘点 pack 全字段,标注每个字段的消费层;L5-only 字段全剥)。L5/assemble 侧继续用全量 pack,top3 节不受影响。
- **验收**:重跑 macro-brief 冒烟,market_view 无确定性 top3 行业名;self_review 防锚定探针连续 5 个扫描日不触发;L5 top3 节 diff 为零。

### A5 · intel 限频/裁稿失联 premise-check(先查再修)

- **证据**:§1.2 intel 段。**本项不预设诊断**——两个待查:
  1. probe #10(`self_review.py:566`)的计数口径:自报「网查 N 条」数的是什么(查询次数 vs 证据条目);`intel_guard.py` hard_cap 数的又是什么;两者是否同一量纲(07-27 教训:先读指令原文,3/3 是我们自己没写清);
  2. Wave9B「超帽按时效裁剪 + `.pretrim.md` 留档」是否真接线(git log 找到实现 commit → 找调用链 → 07-31 为何零触发:是没接线、口径不合、还是根本没超它的口径)。
- **修法(按查明结果三选一)**:接线断了→补接线;口径错位→统一量纲后重定 warn 语义(warn 应报「裁稿后仍超」而非裸自报数);机制已废→删 warn 或降级为计量行。**不重开 cap 数值裁定**(R4)。
- **验收**:查证结论写回本节(diff 对账);修后跑一日真扫描,warn 行为与查明的机制一致。

### A6 · intel 瞬时错重试 + 降级可见

- **证据**:§1.2 ENOTFOUND ×3;卡侧 presence-gate 回退是设计内,但**降级不留痕**(数据契约裁定:降级必须记账)。
- **设计**:l4-stock.js 的 intel 腿对 `RATE_LIMIT/CONNECTION/TIMEOUT/ENOTFOUND` 给第 2 次尝试(与 card 腿同口径);终仍失败→卡头强制一行「🕳️ 情报面:缺稿回退卡内网查」,assemble 把该行带进 detail 与 summary 该票行注。
- **验收**:桌演 intel 断网路径,卡头行出现;t1_review 能按该旗分桶(薄情报卡 vs 全情报卡的准确率可对比)。

### A7 · intel 事件净分时效衰减

- **证据**:300857 的 2026-06-04 事件 +1 分挂 8 周未衰减(intel_stale_score warn)。
- **设计**:intel 稿机器契约里,事件净分条目 >7 自然日且未标「催化挂」(有明确未来兑现日)→ 建档时衰减为 0,原始分留档标 `stale`。
- **验收**:07-31 的 300857 稿重建后净分归 0;带「催化挂 8/28 中报」的条目不衰减。

### A8 · l4_watch 断点续播

- **证据**:07-31 Monitor 1h 超时重挂,重放已播 8 行。
- **设计**:`l4_watch` 落 checkpoint(已播 task 集合)于 scan 目录;`--watch` 启动时跳过已播终态,或加 `--since-checkpoint`。
- **验收**:桌演杀进程重启,只播增量。

### A9 · Wave9 parked 两项转正

- ①公告兜底 fetch 走 lake 缓存或并发化(目标:L3 段 +5min → <1min);②插队建档回执改打真实新增可见数。验收沿 Wave9 批B 原验收。

### A10 · 欠账托底(运营)

- retro「备料>48h 未收尾」从 warn 升 prelude **红行**(现状:三日烂尾靠人眼);dossier 债务(13 pending_init + 19 只 20251231 对账)排盘后节奏(≤3 只/晚照旧),不占扫描窗。
- **验收**:prelude 汇总屏红行出现条件可测;两周后 pending_init 清零、对账完成(运营指标,不阻塞本波)。

---

## 3. B 节 · 退役瘦身(激进档)

### B0 · 删除协议(每项强制,一项一 commit)

1. **五处调用链 grep**:生产 py / tests / `.claude/skills/*.md` / `.claude/workflows/*.js` / docs——引用清单写进 commit message;
2. **将删 test 先读 docstring 查双职**(07-19 教训:退役特性的 test 可能顺带锁着活契约——契约要迁移到活 test,不是陪葬);
3. **判身份**:死码(直接删)vs 回滚杆(过 B5 判据门)vs 「活着但没人消费」(过退役判定门);
4. **删后变异探针**:全测试绿 **且** 抽查 2 个相邻活守卫仍能变红(防「删出永绿」);
5. commit message 附 premise-check 记录(引用清单 + 判身份依据)。

### B1 · 零调用残件(已 grep 初核,动刀前仍过 B0)

| 残件 | 现状 | 处置 |
|---|---|---|
| `autoresearch/scan/l4_reuse.py` | 07-29 裁定退役,Wave9B 已断编排接线,模块留存 | 删模块+其 tests(先查双职);`scan-market.js` 内 l4_reuse 引用一并清(已 grep 到,实施时核性质) |
| `reuse` 白名单键 | `user_config.py:81,154` + `scan_config.jsonc` 注释块 | 白名单剔除;jsonc「预留未接线」注释块删 reuse 行 |
| `redteam_prob` | `config.py:38` 定义+白名单,**零消费点**(全仓 grep 仅定义处) | 字段+白名单+jsonc 注释一并删;若 grep 发现硬编码红队概率的注释引用它,注释同步改 |
| `pinned` cap/ttl 白名单键 | 白名单在、消费未接线(`load_pinned` 函数默认 5/10 已覆盖) | **删键**(要改 cap 改函数默认,一个事实源);jsonc 注释同步 |
| OTEL 残迹 | 仅 `usage_harvest.py` 注释级提及 | 顺手清措辞,不单独立项 |

### B2 · 观察单残件族

- 退役令:fb_20260714_002(观察单日检)。现状:8+ 文件仍引 `watchlist`(`sector/pack.py`、`learning/{self_review,journal,t1_review,stage_eval,zero_buy_ledger,feedback_store,retro}.py`…)+ journal「触发」列恒 0。
- **处置**:逐文件分类【死引用(退役腿的残骸)/ 活契约(如历史账本列的向后兼容读取)】→ 死引用删、活契约标注保留原因;journal「触发」列裁掉(30 日恒 0/—,零信息)。**分类结果是本项的第一交付物**,先出清单再动刀。

### B3 · earlystop_shadow 退役判定门

- 现状:队列机制齐全(`earlystop_shadow.py` sample/queue + health/artifacts/post_run 接点 + `earlystop-shadow` skill),**账本天天刷、0 reviews 从未产生** =「活着但死了」(同 recalibrate-noop 病:自动腿没有会变的量)。
- **判定门(二选一,不许维持现状)**:
  - 历史证据已足(账本自首行至今 0 reviews,无需再攒观察期):premise-check 队列内容后,若确认 0 派发 ∧ 无下游消费 shadow review 产物 → **整族退役**(module + 三接点 + skill + ledger 行);
  - 若要留 → 必须补**自动派发接线**(盘后 cadence 里逐晚消化队列,如 dossier-init 模式),且给账本加「会变的量」断言探针。
- premise-check:先查队列文件实际内容(是队列一直空,还是队列有货没人取——两者退役理由不同,后者还要查为什么生产者在填)。

### B4 · `stable_context_blocks` 未启用路径

- 现状:Wave3 落地至今默认 false,**从未在生产开启**、无实验注册。
- **处置**:确认无 experiment_registry 记录后,删 true 分支 + hash manifest 代码 + workflow 引用 + SKILL 铁律该条目(铁律行改为只余 streaming_l4 / sector_brief_mode)。
- 坑提示:该开关当年动机是 L4 prompt cache 前缀断裂(token-economy P0);删前确认现行 prompt 路径已有 byte-identical 契约测试锁死(有则安全删,无则先补锁再删)。

### B5 · 回滚杆(判据门前置,证据不够只标注不删)

| 回滚杆 | 稳定判据 | 本波动作 |
|---|---|---|
| `streaming_l4=false` 旧批量 GATE3 路径 | 流式路径满 **10 次真实扫描** 0 结构性失败(现 4 次) | **只挂「到期删」标注**(代码注释+本稿追踪表);第 10 次达标后另行一 commit 删 |
| `_ensemble.json` 旧批量格式双读(`decision_finalize._load_ensemble`) | 同上判据(与旧批量路径同生死) | 同上,联动删 |
| abstention **v1 口径** | 自宣「≥10 个成熟日后退役」,现 8/8 | 差 2 日,**写死自动到期**:第 10 个成熟日的 assemble 后删 v1 列与代码 |
| `sector_brief_mode=finalist_only` | 从未启用(待 grep 确认无任何 run 记录) | 确认后按 B4 同款处理 |

### B6 · event 召回路 —— 本波不动

- pr_20260725_001 取证中(判据写死:`channel_audit --variant plus_event` unique_excess_t2 累计 ≥10 日 >0 才提启用)。到期为负→按既定纪律退役+负结果写 lessons(与 accumulation 同口径)。**本稿只登记,不加速不拖延**。

---

## 4. C 节 · 为什么永远没有 buy

### C1 · 四层归因(结论性陈述,证据见 §1)

**第一性答案:0 买 = 设计如此 × 市场如此,不是 bug;但有三根可动杠杆(菜单上涨侧 / 门柱错杀 / 目标校准)。**

1. **地形层(主因)**:系统 06-18 出生至今 regime 几乎全程 risk_off(市场等权 −9.85%),AND 三门在弱市**按设计**收敛到 0。0买日市场 fwd_2 −0.96% + 影子线整体输真实线 = 总量上弃权正确。**但「来了行情它会开火」是未经检验的信念**——系统没见过牛市,此为 EXP-0 的存在理由。
2. **菜单层**:L1 十路全个股因子、零板块路;L2 主动 sector-neutral → 板块轮动 +4~9pp(大个股 alpha 一个量级)结构性吃不到;risk_off 日菜单落刀 74%。上涨侧未被否(R4 边界),是唯一可开影子路的方向。
3. **判断层**:L3 无选股 alpha(−0.39pp)且防御倾斜——选的是 cmf/obv 20 日滞后量为正的"安静票",而「主力真在」门要**当日真金** → **L3 挑的票天生过不了 L4 的门**(层间目标函数不对齐,07-11 三病灶遗留)。07-31 实证 4/7 卡倒在此门;该门错杀率 39%。
4. **尺子层**:fwd_2_oc 主尺(不议)+ 追涨证伪(−4.85pp)+ 目标价过乐观(触达 39%)→ 过门形态 =「温和上行 ∧ 当日主力真金 ∧ 估值不透支 ∧ 1~2 日兑现」,弱市交集≈空集。
- **核心矛盾(两真并立)**:门总量救命(+2.9pp)∧ 门尾部漏肉(因果账已裁 8 日 CORRECT 0 / FALSE 3)。处方 = C2 影子实验(动杠杆)+ C3 报告出口(先看见),**不降门**。

### C2 · 影子实验(experiment_registry 注册制;判据预注册,写 spec JSON 时逐字带走)

> 通用:全影子零生产改动;IMMATURE/UNKNOWN/FAIL 不批;`approve`/`activate` 人工记名;每 family 至多一个 ACTIVE。

| ID | family | 假设 | 影子跑法 | 预注册判据 |
|---|---|---|---|---|
| **EXP-1** | ow_gate_regime | 弱市主力**单日**真金稀缺,「主力 5 日净额持续性」是更低噪声的同义信号(R3 已确认此口径) | L4 卡照常出;卡产物并记两口径判定(当日绝对值 vs 5 日持续性),分歧样本进 shadow 列 | ≥20 日:影子口径**错杀率 <39% 且拦对率 ≥57% 不降** → 提 RECOMMENDED;否则负结果记账 |
| **EXP-2** | recall_sector_momentum | 上涨板块侧有肉(+4~9pp 量级),漏斗零感知 | 注册影子 channel(**不占 quota、floor=0、只记 per_channel 长表**——Wave4 教训:未启用不得有任何副作用,含 L2 floor);`channel_audit --variant` 攒 unique_excess_t2 | ≥10–20 日 **∧ ≥2 温度相位**:>0 → 提启用;为负 → 退役+负结果(与 accumulation/event 同口径同命令) |
| **EXP-3** | target_calib_v4 | 目标锚从叙事压到 hi_2 基率 × regime 后触达率 ↑ | **先 premise-check**:07-11 P0 的 hi_2 锚现状(可能部分已做);增量 = 影子并记两套目标的触达率 | ≥30 张成熟卡:影子触达率显著 >39% 基线(两比例检验) |
| **EXP-0** | counterfactual_riskon | 「系统只是没见过牛市」不可回测(无 risk_on 历史)→ 改预注册未来裁决窗 | regime 首次翻转(risk_off→range/risk_on)后**前 5 个扫描日**自动成为裁决窗(prelude 侦测翻转即标记) | **主判据:窗内 BUY>0** → 地形层归因成立(门柱直方图只作诊断注脚,不进判据);仍 0 买 → **结构层问题升格,重开本章**(此时才有资格谈门) |

### C3 · 报告出口(不改门,改可见性;R3 已确认)

1. **0 买日 summary 增「🎯 差一点(未过门)」节**:shadow_buys top3 逐只——倒在哪根门柱(带具体读数)、EV/R:R、该票影子账本历史战绩;**节头固定标注**「未过门,非建议;影子线整体仍输真实线」。落点:`report_sections` + shadow_buys 数据已在(`shadow_buys.csv`)。
2. **因果裁决上浮**:abstention v2 出 FALSE 的次日,summary 醒目一行(「昨日弃权被因果账判 FALSE:600188 相对市场 +2pp 可交易」)——漏肉要像错买一样刺眼。NEUTRAL/CORRECT 不上浮(防噪声)。
3. 连败→预算压缩联动保留现状(menu 行已有,不加不减)。
- **验收**:用 07-16/17/21 三个历史 FALSE 日回放 assemble,三节内容正确;非 0 买日「差一点」节不出现;变异探针=撤 shadow_buys 输入后该节显式写 UNMEASURED 而非静默消失。

---

## 5. 批次 / 依赖 / 量级

| 批 | 内容 | 量级 | 依赖 |
|---|---|---|---|
| **①** | A3 · A5(premise-check)· A1 · A4 | 半天 | 无 |
| **②** | A2 哨兵中间档 + C3 报告出口(同动呈现层) | 1 天 | A1 先行(同文件避免冲突) |
| **③** | B0 协议 + B1–B4(B5 挂标注,B6 登记) | 1 天 | 无(与①②并行安全,但按序做防 rebase 噪声) |
| **④** | C2 四实验注册 + A6/A7/A8 | 1–2 天 | EXP-2 依赖 channel_audit 仪器(已修,Wave4);EXP-3 先过 premise-check |
| **⑤** | A9/A10 | 运营节奏 | 不占开发窗 |

## 6. 验收纪律(全批通用,沿 Wave9 §9)

- 每项修复配**变异探针**(撤修复→守卫必须变红);workflow/呈现层改动用**真实历史产物回放**冒烟;
- 涉及删除:B0 协议逐条走,一项一 commit;
- 影子实验:registry 先 `register --spec` 再动代码;判据 JSON 与本稿 §C2 逐字一致,防"边跑边改判据";
- 操作建议(命令行)先跑通再写进文档(Wave3.5 教训);
- 冒烟"真 catch"须对手方复核(Wave1 教训:2/2 假阳)。

## 7. 非目标(本波明确不做)

- 不改 OW 三门生产口径、不改 fwd_2_oc 主尺、不碰防御/跌势侧轮动、不重开 intel cap 数值;
- 不动 Wave9 批 C/D/E/F 的内容与调度(期权双层/自学习章节/trace 重组/单工作流全链,各自另出计划);
- 不做港美股、不做盘中、不引入任何付费 API;
- EXP 系列在晋升门通过前**不改任何生产行为**(含"顺手"的 floor/quota/prompt 措辞)。

## 8. 开放问题(实施前一次冒烟裁决,答案回写本节)

1. **A5 双查**:probe #10 计数量纲?Wave9B 裁稿接线真伪?(答案决定 A5 走三岔中哪条)
2. **EXP-3 前提**:hi_2 基率锚现状——07-11 P0 是否已部分实现?(决定 EXP-3 是新建还是增量)
3. **B2 清单**:观察单 8+ 文件的死引用/活契约分类结果(第一交付物,先清单后动刀)
4. **B3 队列**:earlystop_shadow 队列是"一直空"还是"有货没人取"?(退役理由不同,后者需查生产者)
5. **EXP-1 数据面**:「主力 5 日净额持续性」在 slim/L1 现有列里是否够算(main_net 5 日序列可得性)?不够则先补湖列再开影子
6. **A2 边界**:`sentinel_pinned` 档下 GATE2/GATE3 的语义(无 finalists 时门怎么判)——桌演时定,原则=门只验它管的东西

---

_沿革:承接 Wave9(`2026-07-29-wave9-report-depth-options-harness-design.md`,批A/B 已上线)与 07-28 统一总纲;0 买归因承接 07-11 漏斗六问、07-12 L3 无 alpha 裁决、07-17 轮动盲区裁定、07-25 Wave4 事件路实证。仅供研究,非投资建议。_
