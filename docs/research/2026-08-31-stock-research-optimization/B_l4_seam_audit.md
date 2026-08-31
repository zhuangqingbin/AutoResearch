# B · scan-market ↔ stock-research(lite / l4-card)接缝审计(只读)

> 审计日 2026-08-31;基线 = 工作树(main,未提交改动在场)。所有行号为当时快照。
> 范围:L4 决策卡的「任务包组装 → 卡片输出 → 下游消费」全链 + pinned 路径 + 独立 lite 调用分叉 + 耦合清单。

---

## 1. 任务包组装:`_l4_prompt_<code>.md` 由谁、用什么拼出来

**生产者链**(全确定性,零 LLM):
- 编排:`.claude/workflows/scan-market.js:515`(`l4_card shared`)→ `:516-519` 四生产者并行(pledge/seats/consensus〔+fund_hold〕)→ `:524` `dispatch-plan` → `:536`(legacy 批量)或 `:562`(流式)`l4_card prompts` + `l4_tasks init`。哨兵持仓路单独在 `:386` 只跑 `prompts`。
- CLI 适配器:`autoresearch/scan/agents/l4_card.py:119`(`prompts` 子命令)→ 真身 `autoresearch/scan/l4/prompts.py:write_dispatch_pack`(:133-286)。
- 共享块:`write_shared_instructions`(prompts.py:18-33)→ `_l4_shared_instructions.md`。**2026-08-21 起只剩一行标头**(校准锚 📐/🔁/🚪、T+1 快环块随 learning 层退役删除,prompts.py:21-25);空骨架故意保留=cache 前缀稳定。

**每张 prompt 的拼装顺序**(prompts.py:244-263;cache 前缀契约):
1. 固定标头一行(逐卡不变,≤300B;prompts.py:246-247)
2. **共享块原文**(:249;缺文件时回退一句"按 lite-playbook 执行")
3. `---`
4. **逐卡 body**(共享块之后才允许出现逐卡字节 —— `tests/scan/test_l4_prompt_cache_prefix.py:1-33` 冻结「共享块位置统一且 ≤300 offset、头部前缀 byte-identical」):
   - `## L4 派发 — <code6> <name>` 标题(:209)
   - 📌 保送两行(仅 `finalists.csv` 行 `lane=="pinned"`:保送声明 + **📌持仓管理要求**〔卡必须含『持仓管理』节〕,:210-218)
   - ⛔ 强制满卡块(`force_full_card(priors)` 命中才插;priors = finalists 行 conviction/lane + L2 行 n_channels/l2_lane_reserved,:219-230;判据真身 `l4/rubric.py:48-80`:📌 恒 True / conviction≥70 ∧(n_channels≥4 ∨ lane_reserved))
   - `compose_funnel_brief(code6, scan_dir)`(:233;真身 `l4/context.py:194-294`),**其内部顺序**(context.py:293 `parts=(ctx, dsum, doss, dsecs, brief)`):
     a. **市场地形块** `market_context_block(pack, industry)`(context.py:11-20 → `scan/market.py:393`;读 staging `market_pack`,common 段全卡相同但**排在逐卡标题之后**,吃不到 cache 前缀 —— stable_context 前置方案已于 Wave10 B4 退役,预估省 4%<10% 门)
     b. **📚 覆盖档案摘要**(`dossier.schema.injectable_summary`,context.py:170-192;硬帽 `SUMMARY_CAP=3000` est_tokens ≈8.4KB,**超帽即整段弃**,schema.py:21,:151-153;未首覆/缺档 → "")
     c. **📁 前科卡**(`scan/dossier.render_dossier`,context.py:279-283;近 ≤10 个 scan 日入围史,每日一行 + 已知证伪点 ≤4 条,scan/dossier.py:97-118;素材本身来自**历史卡的 parse_rating+_l4_brief**,dossier.py:44-48)
     d. **档案节选(研报体素材)** `dossier_sections(code6, ("§1","§2","§3","§5"))`(context.py:285-292;帽 `RESEARCH_BODY_CAP=12000` est_tokens ≈33.6KB,整节累加超帽截断+显式 ⚠️ 标记,schema.py:28,:82-134;实测 31 份档案四节 6.8–15.1KB —— **任务包里最大的一块**)
     e. **漏斗简报**(context.py:224-246):L1 召回行/8 子分/基本面·估值·资金·筹码先验(⚠主力失真旗 `_dist_mark` :22-33、龙虎榜席位行 `_seat_mark` :58-80)/L2 分/**L3 前提清单**(thesis + 前提2=mechanism,中性措辞防锚定 :240-242)/L3 元数据(conviction/lane/sentiment/risk/catalyst)/日历旗(`calendar_flags`:解禁⚠️+披露📅,`scan/calendar.py:102-119`)/质押旗/误读预警/催化行/机构面两行(consensus+fund_hold)/**行业 brief 地形段**(`sector/brief.render_terrain_block`,只抽 `## 地形段`,sector/brief.py:47-58)
   - **昨卡回声**(`yesterday_echo`,prompts.py:41-99,:237-239):读**已发布** `reports_<eng>/scan/<run>/manifest.json` + `details/<名称>.md`(≤5 自然日),抽 `**Rating**`/`**一行多空**`/前两条 `[价格线]`(:36-38 三个正则)→ 3-4 行注入
5. `---` + 四个路径指针(:258-262):slim(`<input_dir>/<ticker>_<date>_slim.md`,「>8KB 才可信,≈4.8KB=NO_DATA」字样直接写进 prompt)/ deep(`…_slim_deep.md`,survivor 进 P4 才 Read)/ intel(`<scan_dir>/_l4_intel_<code6>.md`)/ 卡片落点(`<scan_dir>/details/<code6>.md`)

**旁产物**:`_harvest_list.txt`(:267,yfinance 后缀归一,`.SH` 绝迹)、`_dossier_present.json`(:269-271,探针10 读)、`_dossier_snapshot/<code>.md`+索引(:106-130,:279-280 —— 档案会被 assemble 尾的 δ 原地改写,落稿这一刻抄的才是 agent 真读的那版)。

**输入的另一半走 args 不走文件**(接缝在 workflow 手上):
- `dispatch_plan`(`l4/dispatch.py:12-52`)产 `{dispatch, meta{name,sector,pinned,dossier_summary}}`;`dossier_summary` = `_dossier_summary_text`(context.py:162-168,与卡注入同源)。
- 主会话按 SKILL.md:154 一次性全派 `l4-stock.js`,**手工透传** `args.{date,run_id,code,attempt,name,sector,cfg,pinned,dossierSummary}`。`cfg` 必须是 `frame --json` 回显的 user_config(空 cfg 直接 throw,l4-stock.js:34-38);`pinned`/`dossierSummary` 各自缺省安全但**漏传即静默降级**(pinned 漏传 → SELL 双复核整段不跑,07-21 事故;test_agent_defs.py:346-357 只锁「派发前打印名单」,拦不住主会话真忘)。
- intel prompt(l4-stock.js:327)只给 代码/名称/行业/日期/落点 + `knownBase`(dossierSummary 内嵌,:295-297);cap 走 `cfg.l4_intel.max_queries`(:292,现值 20,scan_config.jsonc:182)。
- 卡 prompt(l4-stock.js:409)只有一句:「执行 `_l4_prompt_<code>.md` … 写决策卡到 `details/<code>.md`,返回 code/rating/conviction/proposal」;人设全部烤在 `.claude/agents/l4-card.md`。

**大小预算一览**:共享块≈40B;标头≤300B;📚 摘要 ≤8.4KB(超即弃);档案节选 ≤33.6KB(超即截);前科卡 ≤~15 行;漏斗简报 ~15-25 行;昨卡回声 3-4 行;slim 表面块>8KB 才可信(体积只兜真垃圾,合格判据=结构锚+Close 数值,`l4/producers.py:296-336`,地板 4KB=`l4_tasks.prepare_slim min_bytes=4096` :1419)。

---

## 2. 卡片输出契约(l4-card 必须写什么、机器读什么)

**产物落点**:
- staging 卡 `<SD>/details/<code6>.md`(l4-stock.js:409;`SD=context_<eng>/scan_runs/<run_id>/staging/<date>` :71)。
- 复核卡 `<SD>/ensemble/<code>.run2.md`/`run3.md` + `_ensemble_<code>.json`(l4-stock.js:443,:470-480;record 含 code/ratings/median/spread/degraded/trigger/n_runs/early_stopped/**role='ens_review'/n_dispatch**)。
- workflow 结构化回传 `{code, rating, conviction, proposal}`(CARD schema :79-81;conviction ≤1 归一 ×100,:422-426)。**conviction 只活在回传里,不落任何盘上产物** —— 见 §3「写了没人消费」。
- StageResult:`recordL4` → `stock_stage.record_l4_result`(stock_stage.py:89-149,parse_rating+parse_early_stop+ensemble fold → metrics)。
- 发布副本 `reports/<run>/details/<中文名>.md`(publisher.py:190-193,同名冲突挂 `_<code>`),发布层**原地改写**:插 📰 头行(:196-201)、复核分歧行(:202-210)、追加 intel 全文附录(:211-219)、追加价格断言对账脚注(:220-245)。

**模板双真身**:`.claude/agents/l4-card.md`(烤入版)⇄ `.claude/skills/stock-research/lite-playbook.md`(自称真值源);`tests/test_agent_defs.py:38-61` 只用**子串锚**同步(~20 个锚 + v4 标记整行 byte-identical :63-87)。**数字预算已经漂了**:早停卡 ≤44 行 / ~1.6-2.2K / 满卡 ~4.5K(l4-card.md:143-144)vs ≤36 行 / ~1.2-1.8K / 满卡 ~3K(lite-playbook.md:57,:177-178;STAGES.md:174 站 36 行)——锚测试不比数字,两边各说各话。

**机器解析的字段(逐条,24 个)**:

| # | 卡面字段/记号 | 解析器(file:line) | 下游 |
|---|---|---|---|
| 1 | `**Rating**: <五档>` | `agents/utils/rating.parse_rating`(rating.py:25 泛正则 `rating.*?[:\-]`;**:42-46 兜底取全文第一个评级词**)| `_finalist_row`(parsers.py:272)、health:267、stock_stage:116、market:563、dossier:47;严格版另有三份:l4_watch `_rating_of`(:155-163 行首 `**Rating**`)、yesterday_echo `_ECHO_RATING`(prompts.py:36,^锚+全角冒号容错**无**——只认半角)、self_review 探针9 `"**Rating**" in line`(:1271) |
| 2 | `FINAL TRANSACTION PROPOSAL: **BUY\|HOLD\|SELL**` | `_PROPOSAL_RE`(parsers.py:14-17) | `_finalist_row["proposal"]`;l4-stock.js:432 `isSellish`(pinned sell_review 触发的一半)。⚠️ **E6 读的"提案"不是这行**:DecisionRecord.proposal = `_PROPOSAL_BY_RATING[final_rating]`(decision_finalize.py:30-36,:352),护照 :411 再带给 relative_buy:565 —— 卡面 PROPOSAL 行在 python 决策链上**零消费**(A2 的"第二道防线" :572-575 实际防的是同一个派生值) |
| 3 | `置信度: 高/中/低` | `_CONF_RE`(:18)+ 仪表盘列(:266-269) | 进 `_finalist_row["conf"]` 后**无任何渲染点**(候选表列=评级/目标/一句依据/L1→L2,report_sections:936-949) |
| 4 | 决策仪表盘表·`EV目标/目标` 列 | `_parse_dashboard`(:88-98,第一张含「评级」的表,取表头+2 行,列数必须相等)+`_get`(:100-104) | 候选/保送表「目标(EV)」列、同链块(report_sections:948,:663);**数值从不被事后核验**(见 §3 outcome) |
| 5 | 仪表盘·`R:R` 列 | 同上(:275) | `_finalist_row["rr"]` **无渲染点、无消费者** |
| 6 | `**Rubric建议** … <Rating>` | `_RUBRIC_RE`(:19-22,行内第一个评级词) | self_review check6「评级超rubric」(self_review.py:248-255)、DecisionRecord.rubric_rating(decision_finalize.py:343)。⚠️ 评分卡映射真身 `rubric_rating`(l4/rubric.py:18-46)**全仓零生产调用点**(memory 已记):机器只比对卡自报的建议串,从不重算净分 |
| 7 | `**偏离**` | `_DEV_RE`(:23) | check6 豁免(:249) |
| 8 | `**一行多空**: 多…｜空…` | `_BULLBEAR_RE`(:24)、`_bullbear_side`(:171-180) | `_l4_brief`(96 字,:111-128;前科卡/买列)、`pick_rating_aligned_evidence`(80 字 `EVIDENCE_MAX_CHARS`,:204-238;候选表「一句依据」,report_sections:1155-1158)、昨卡回声 `_ECHO_LS`(prompts.py:37) |
| 9 | `**早停**: 停于 P<n> ｜ 停因:<七选一>` | `_EARLYSTOP_RE`(:29-31);词表外折「其他」(:344;闭集 :32-35 = contracts/agent_output.py:47-50) | `write_early_stop` → `_early_stop.json`(:346-359)→ market `_zero_buy_mechanism`、outcome.early_stop_reason、chain_view、护照;`parse_early_stop` → DecisionRecord.early_stop → brief `_why_no_buy`(brief.py:177-201)、summary 节6(report_sections:953-994)、E6 红灯停因(relative_buy.py:181-183,:578-579) |
| 10 | `早停因:` 自由文本 | `_STOPWHY_RE`(:25)、health 子串 `"早停因" in text`(health.py:227)、card_contract_lint 豁免(self_review.py:304) | `_l4_brief` 回退、l4_phase_stats 早停计数 |
| 11 | `OW三门 …主力真在✗…` 段 | `_GATESEG_RE`+`gate_status`(:27-331;取**最后一个可解析段**,容错 `**`/`门`字/空白 —— 17.4% 加粗误读疤的修法) | DecisionRecord.gate_states(decision_finalize.py:287-295)→ brief/节6/仪表盘③;`gate_histogram`(report_sections:129-152,自由文本口径,附 `GATE_HIST_BASIS_NOTE` :123)、`_gate_breach_text` 证据链(:192-201)、护照 risk_flags(passport.py:284-292) |
| 12 | `进入P4倾向: <Rating>` | health `_P4_RE`(:75)、card_contract_lint(self_review.py:295)、agent_output:102 | 仅 p4_seen/p4_flips 计量 + 缺失 warn |
| 13 | `[价格线] close <op> <数> → 动作` | `tripwire_watch._PRICE_RE`(:32) | 持仓日检 hits、两尺分歧框(decision_finalize.py:394-449)、昨卡回声 `_ECHO_WIRE`(prompts.py:38) |
| 14 | `[日期线] YYYY-MM-DD` | `_DATE_RE`(:33) | 日检提前 3 日预警(:181-190) |
| 15 | `[事件旗] a\|b\|c` | `_EVENT_RE`(:34) | 对 news_em 标题(:196-202;覆盖率诚实声明 :14-17) |
| 16 | `[执行线] pct_chg<=3.0 / pos_in_range<0.7` | `_EXEC_RE`(:42-43) | **解析后故意不判**(:191-195);事后 exec_ok 用常量 `EXEC_MAX_*`(outcome.py:71-72)——**卡里写的阈值本身零消费**(agent_output.py:16 自己记了这条:两处各写一份、没有测试对齐) |
| 17 | 带日期引用行 ≥6 | `_DATED`(self_review.py:1204-1206) | citation_density warn(满卡;早停/♻️ 豁免) |
| 18 | 价格断言(某日±X%/涨停;`〔转引标题〕`=豁免记号) | `price_claims.classify/audit_card_text`(price_claims.py:604-618;主语判定 A3;`〔转引标题〕` 在 `_QUOTE_OR_REFUTE` 词表 :131) | staging 对账 warn(self_review:1229-1247)+ 发布副本脚注(publisher:220-245)+ E6 contract 门(`_price_claim_status.json`,relative_buy.py:400-402,:532,:556——**该文件是可选产物,缺席=UNMEASURED 放行**) |
| 19 | `〔卡契约 v4·隔夜 c1→o2〕` 行 | `card_v4_marker_lint`(self_review.py:829-866;`_CARD_V4_MARKER` :39) | warn;test_agent_defs:63-87 锁两模板整行 byte-identical |
| 20 | 标题尾 `〔早停·表面 DD〕` | citation_density 豁免(self_review:1212 `"〔早停" in text`)、card_contract_lint 首行判(:302-304) | 早停/满卡分流 |
| 21 | `研报体(档案δ)`/`微研报`/`档案未建` | 探针10(self_review:1286-1301;锚常量 :32-34) | warn;test_agent_defs:90-107 |
| 22 | `档案对账`/`变化项` 节名 | card_contract_lint(self_review:315-324) | warn(**只查在场,内容从不被 δ 吸收**,见 §3) |
| 23 | `♻️`+`复用` 横幅 | health:224、self_review:1041/:1212、card_contract_lint:300 | 历史卡豁免 |
| 24 | `# ` 标题行(+第二行 `〔` 契约戳) | publisher `_inject_news_headline`(:148-167)、chain_view 按前 400 字含 code 认卡(:356-359) | 发布注入锚 / 链路视图定位 |

**契约层现状**:`autoresearch/contracts/agent_output.py:88-108` 已把 L4_CARD 十个字段declar成单一真身(2026-08-29 P0),**但执行侧未切换**——`parse_rating` 仍是泛正则+全文兜底(agent_output.py:10-11 自己点名这病);声明与执行靠"对拍测试"维系。

**解析脆弱点**(含 memory 前科):
- ① `parse_rating` 兜底:卡缺 `**Rating**` 行时取全文第一个评级词——L3 论点裁决表引用原文里的 "Overweight"、一段话研判里的 "Underweight" 都可能被认作评级(agent_output.py:10)。
- ② `gate_status` 方向性偏差仍在:`_mark_after` 只认 `✓/✗` 两个字形(parsers.py:305),写 ✔/✘/× → ""→`False`=**判 PASS**;整段无标记时回退首段全 False=三门全 PASS(:331)——同族"真失守读成通过"(17.4% 加粗疤,:285-291)只修了加粗一种。
- ③ 早停行格式一错(如漏 `｜`、停因带空格)→ `parse_early_stop`=None → DecisionRecord 把该卡按满卡分类(`first_rejection=L4_RUBRIC`,gate_states 全 UNKNOWN,decision_finalize.py:296-319),0 买归因整桶挪位。
- ④ finalist 前导零:`_decision_text` 已带 zfill+glob 兜底(parsers.py:241-257,memory 疤的修法);但 yesterday_echo 走**中文名**文件(prompts.py:76-78),publisher 改名/同名挂 code 后缀(publisher:192-193)会让回声按 `<名称>.md` 找不到挂了后缀的卡。
- ⑤ 空 slim 4.8KB 盲卡:三道防线——prompt 文本自带阈值提醒(prompts.py:258)、`_slim_defect` 结构判据(producers.py:309-336,体积只兜 4KB 地板;"差 16 字节毙全线"疤的修法)、preflight 缺 prompt 即 BLOCKED(l4_tasks.py:725-728「盲卡在这里绝育」)。
- ⑥ 停因闭集三处副本:parsers:32-35 = agent_output:47-50 = l4-card.md:89;但 **relative_buy 的红灯集里混进了闭集外的「监管/审计红灯」**(relative_buy.py:181-183)——解析器会把它折成「其他」,这一档**永远不可能命中**(死条目)。
- ⑦ `_ensemble_<code>.json` 由 gp_shell heredoc 写(l4-stock.js:474-480)——JSON 经壳 agent 转写,格式坏 → `_load_ensemble` 静默吞(decision_finalize.py:107)=复核折回静默失效。

---

## 3. 下游消费者:字段 → 消费者矩阵

**消费者(20 个读点)与各自读的字段**:

| 消费者 | 读什么 | 备注(file:line) |
|---|---|---|
| `l4-stock.js`(Verify) | 回传 rating/proposal 串(不读卡文) | `isOW`/`isSellish` :431-433;JS 先折一遍,python `_apply_ensemble_fold` 再折=权威(decision_finalize.py:120-132) |
| `l4_tasks` | 卡文件 **hash**(prompt/slim/card 三件指纹) | success/preflight/recover :1045-1074,:694-705;不解析字段 |
| `stock_stage.record_l4_result` | parse_rating+parse_early_stop+ensemble fold | StageResult metrics(:103-147) |
| `l4_watch`(CP5 直播) | `**Rating**` 行 | :155-163,:214,:279 |
| `decision_finalize`+`report_sections.prepare_report_model` | rating/target/rr/proposal/conf/l4/rubric_suggest/rubric_dev + gate_status + parse_early_stop | verify 折回(**verify.csv 恒空**:2026-07-06 起零写点,decision_finalize.py:45-57)→ ensemble 折回 → `_final_ratings.json`/`_early_stop.json`/`decision_records.json`/`dissent_records.json`(:252-374);「一句依据」(:1152-1161) |
| `brief.py` | **不读卡**(白名单禁 details/,brief.py:73,:98-122) | 只吃 `_final_ratings`/`decision_records`(停因+gate_states)/`_tripwire_conflicts` |
| `self_review` | 见 §2 表 #6/#12/#17/#18/#21/#22 + 探针9(`**Rating**` 行 Sell/UW ∧ 缺 sell_review ensemble,:1264-1283) | 卡内容类检查**全 warn**,不进 GATE4 fail |
| `health.l4_phase_stats`/`_legacy_final_ratings` | `早停因` 子串、`进入P4倾向`、parse_rating+双折回退 | :211-273;n_full 喂 force_full 探针 |
| `market.render_funnel_readout` | parse_ratings_from_details + `_early_stop.json` | :549-590(legacy ≥OW 行;E6 active 时另起一行) |
| `publisher._publish_details` | 整卡搬运+改名+四处注入 | :170-247 |
| `price_claims` | 全卡 %/涨停断言(staging 卡 + 发布卡含 intel 附录) | self_review:1229 / publisher:224 |
| `relative_buy`(E6)← `passport.build_passport` | **research_rating=DecisionRecord.final_rating**、proposal(派生)、earlystop_reason、gate_states、task-book 三件 PRESENT、intel `availability_for_card`、`_price_claim_status` | passport.py:268-292,:399-417;硬门 :540-586(rating∈{Sell,UW} / proposal==SELL / 停因∈红灯集 / 流动性分位);护照**现算**不读盘上那份(relative_buy.py:593) |
| `outcome.py`(结果账本) | **只吃两个卡面派生字段:rating(`_final_ratings`)+ early_stop_reason(`_early_stop`)** + finalists lane/guard/conviction + E6 decision | run_facts :156-206;主尺 gap_c1_o2 从湖现算 :211-240;`exec_ok` 用常量不用卡值 :318-325。**卡的入场价/目标价/止损/tripwire 一概不进账本、事后零核验**;`rel_gap`/`fwd_5/10` 与评级配对是账本仅有的"卡 vs 事后"对表 |
| `tripwire_watch` | 三型盯梢线 + 执行线(parse only) | 只对 **pinned** 码(check :150-155);⚠️ 疑点:`latest_card` 只扫 `scan_root()` 兄弟日目录(:94-103),run 分区下 `scan_root()=scan_runs/<run_id>/staging` 只有本 run 日期——**跨日找昨卡在 RUN_ID 在位时结构性落空**(menu.py:74-91 的 K4 修法没同步到这里;`_tripwire_hits` 传 `card_before=今日` 后更是恒空,decision_finalize.py:380-391)。prelude 无 RUN_ID 时不受影响 |
| `dossier/delta.record_scan_deltas` | read_final_ratings(终评级)+ finalists conviction + intel `档案缺口:` 行 | :309-355,:208-239;**不读卡的「档案对账」正文**——该节只被 lint 查在场,δ 从不吸收 |
| `scan/dossier`(前科卡→次日 prompt) | parse_rating + `_l4_brief` | :38-49 |
| `l4/prompts.yesterday_echo`(昨卡→今日 prompt) | `**Rating**`/`**一行多空**`/`[价格线]`×2(**发布副本**,含 publisher 注入后的文本) | prompts.py:41-99 |
| `chain_view` | `_early_stop`/`_final_ratings`/decision_records/`_ensemble_<code>`/发布卡定位 | :298-362 |
| `render --view gate_hist`(CP6) | final_ratings + `_zero_buy_mechanism` + gate_histogram | render.py:44-58 |
| `usage_reconcile.dispatch_census` | `_ensemble_<code>.json` 的 role/n_dispatch | usage_reconcile.py:146-185 |
| `report_appendix` | 按**中文名**给 `details/<名称>.md` 链接 | :157-173 |

**写了但没人消费(机器侧零读点)**:
- `置信度`(解析进 row 后不渲染)、仪表盘 `R:R`(同)、`上行/下行`/`建议仓位` 列(仪表盘解析只取 目标/R:R/置信度三键)。
- **conviction(卡回传)**:只在 workflow 返回值里,零落盘。
- `**独立初判**:` 行——模板自称「机读契约,`chk_blind_pass` 按此标签核在场」(l4-card.md:70,lite-playbook.md:68),**全仓已无 `chk_blind_pass`**(learning 层 08-21 退役;grep 零命中)——死契约,模板还在逼 agent 写。
- 「断言分级」三级标签(已核/网查/推断)——只有 `〔转引标题〕` 一个记号被 price_claims 消费,「网查」「推断」标签零读点。
- 『持仓管理』节(pinned 卡强制,prompts.py:216-217)——零 python 读点,纯人读。
- 三档情景/EV 数值/预期差/多空对撞/催化&认错位/T+2 开盘预案/已核数字摘录——人读;且给 price_claims 制造误报面(Bull/Base/Bear/EV/R:R 语境要专门排除,price_claims.py:14-15,:168)。
- `[执行线]` 的**阈值数字**(解析出 metric/op/level 后弃用)。
- `研报体`/`微研报`/`档案对账` 的**正文**(只查节名在场)。
- `rubric_rating()` 纯函数(l4/rubric.py:18-46)——评分卡映射的"机器真身"零调用,评级实际全靠卡自报。

**消费者读但卡片不保证产出**:
- OW三门段:早停卡按定义不写(→UNKNOWN,合法);**满卡漏写 ✓/✗ → 三门全 PASS**(见 §2 脆弱点②)。
- `**早停**` 机读行 vs 早停卡:格式错=整卡被归 L4_RUBRIC(脆弱点③)。
- `EV目标`/`R:R`:早停卡仪表盘没有这两列 → "—"(合法降级)。
- `进入P4倾向`(满卡)、≥6 带日期引用、v4 标记行、研报体/缺档声明、档案对账——全 warn 级,缺了照发。
- `[价格线]`:卡可以一条不写(tripwire 对该票就此失明,render_line 会说"全部未触发"——:237-246 有诚实注,但没有"这票根本没写线"的区分)。
- E6 侧:`_price_claim_status.json` 可选(缺=UNMEASURED 放行,relative_buy.py:69-71);`missing: l4.research_rating`(卡在盘读不出评级)才 fail contract(passport.py:404,relative_buy.py:547-555)。

---

## 4. pinned 持仓路径 vs 普通 finalist

**进场**:`pinned.jsonc`(现 1 条:300857;cap=5/ttl=10,scan_config.jsonc:34)→ `user_config.load_pinned`(:417-431,kept/expired 分类+cap 截断)→ frame 烤进 run_contract → L1 强注 → L3 pass1 全入 + `merge._inject_pinned_finalists`(l3/merge.py:469-536:已入选行改判 `lane="pinned"`+`pinned_note`;未入选行整段带 judged 判断;三层 pinned 标记互相独立 :481-483)。守卫②lt55/③cap/⑦chase/⑧sector_cap 全部**豁免**(注入在全部守卫之后,STAGES.md:130)。

**与普通票的 8 处差异**:
1. **prompt**:📌 两行 + 『持仓管理』节强制 + ⛔ 强制满卡恒触发(rubric.py:66-67「盈利质量/偿付不许未核」;普通票需 conviction≥70∧共振)——prompts.py:208-230。
2. **复核方向相反**:普通票 ≥OW 才触发 `ow_review`(中位只向下折);pinned 卡 rating/proposal 含 Sell 触发 `sell_review`(中位只向**温和**折,防误卖持仓)——l4-stock.js:429-486;python 权威折回 decision_finalize.py:120-132;单向阀吃掉的分歧记 `PINNED_SELL_PROTECTION`(:145,:190-192)并印上发布卡头(publisher:129-145)。
3. **触发依赖手传 `args.pinned`**:dispatch meta.pinned(dispatch.py:48)→ scan-market.js:581-586 只**打印名单**,真正传参靠主会话(SKILL.md:158「逐一核对」);漏传=sell_review 断链(探针9 事后 warn,self_review:1264-1283)。
4. **报告分列**:`_pinned_section` 单独表+保送理由列+⚖️ 两尺分歧框(report_sections:605-640,:567-602);brief ④ 持仓行+tripwire 计数(brief.py:571-588)。
5. **E6 排除**:`exclude_pinned: true`(scan_config:217-219)——pinned 永不当日 BUY;outcome role="pinned"(outcome.py:179)。
6. **哨兵日仍出卡**:`sentinel_pinned` 路跳 L3、只为 pinned 跑 prompts、bookless LEGACY preflight(scan-market.js:377-389;l4_tasks.py:745-750;STAGES.md:369-371 force_full 铁律)。
7. **盯梢独占**:tripwire_watch 日检只看 pinned(:150-155);两尺分歧框只对 pinned 且只用**严格早于今日**的卡(decision_finalize.py:394-449)。
8. **复用**:「pinned 永不复用」已被「所有票都不复用」吸收(Wave9 R5,dispatch.py:13-21)——今日无差异;唯一残留是 preflight 的 SUCCEEDED hash 复用(三件指纹全验才跳过,对两类票同规则)。

---

## 5. 独立调用 vs 扫描内调用:两条路在哪分叉

**分叉点(代码级)**:
- 档位路由是**纯文档路由**:stock-research SKILL.md:17-22 的表(被 scan L4 调用→恒 lite;用户说"快速看一眼"→lite)。没有任何代码枚举"当前是哪条路"。
- 取数分叉:`harvest.main` 的 `--slim`(harvest.py:1926)+ `_output_dir`(:1899-1906):slim 无 `--out-dir` → `ws.scan_input_dir(date)`(workspace.py:125-130)——**无 RUN_ID → `context_<engine>/` 根;有 RUN_ID → 该 run 的 `staging/<date>/_external_inputs/`**。即:用户在一个 export 过 AUTORESEARCH_RUN_ID 的 shell 里独立跑 lite,slim 会写进那个 run 的法证现场(污染面)。
- 执行分叉:扫描内=`l4-stock.js:409` 指向任务包;独立=主会话(或直接派 l4-card agent)按 lite-playbook 走,**没有任务包**。l4-card.md:12 写「任务包路径…或内联简报」,但 P0/L3 论点裁决表/漏斗简报锚全都假设任务包在场。
- 落点分叉:lite-playbook.md:55 唯一写明两条:独立→`$RPT/analyze/<YYYYMMDD>_<HHMM>/<名称|TICKER>_lite.md`;扫描→staging `details/<code>.md`。

**独立跑时输入包缺什么**:无漏斗简报(L1/L2 行、L3 前提清单、conviction——P0 无从定向,模板的「L3 论点裁决」节无源可填)、无 `_l4_intel_*`(l4-intel 只被 l4-stock.js 派;engine-playbook.md:199「lite 一律不派」两个 full 档情报员)、无 📚/📁/档案节选注入、无市场地形/行业地形/日历旗/质押旗/席位行/机构面行、无昨卡回声、无共享块、无 📌/强制满卡。slim 本身的 L1 复用块有回退:`_load_l1_row` 找不到当日 scan 产物 → **live tushare 现拉**(harvest.py:1678-1696 docstring 明写 fallback),所以数字块不缺,缺的全是"判断的语境"。

**独立跑时输出无人接**:全仓 grep `_lite` 零 python 读点(唯一命中是 SKILL.md 落点说明)。不进 capsule/exec_capture、无任务簿/StageResult、无 DecisionRecord、无 `_final_ratings`、不进 E6、不进 outcome 账本、无 self_review/price_claims lint、无 δ 回写、无前科卡(scan/dossier 只扫 scan staging)、无昨卡回声(只读 reports/scan)、tripwire_watch 不看 reports/analyze。SKILL.md:40 说"可选校验 `autoresearch.scan.assemble` / parse_rating 直接读卡"——scan.assemble 根本不认 `reports/analyze/*_lite.md` 这个位置,这句是文档幻觉级接线。**即:同一张"决策卡",在扫描内有 20 个消费者,独立跑时有 0 个。**

---

## 6. 耦合清单:把 stock-research(lite)与 scan-market 焊死的点

若把 lite 卡改成「结构化 JSON(机器)+ markdown(人)」双写,每一条都是要动的消费点:

1. **文件名/路径族**:`_l4_prompt_<code6>.md`(prompts.py:265;l4-stock.js:409/443;l4_tasks.py:295/352/603;scan-market.js:562;chain_view:300;contracts/artifacts.py:124)· `details/<code6>.md`(prompts.py:243;l4-stock.js:409;l4_tasks.py:354/605;stock_stage:99;parsers:243;tripwire_watch:97;publisher:187;self_review×5;artifacts.py:141)· 发布名 `details/<中文名>[_code].md`(publisher:190-193;yesterday_echo prompts.py:76-78;report_appendix:173)· `_l4_intel_<code6>.md`(+`.pretrim`/`.rejected.md`/`.orig` 侧车,intel_guard.py:33-43)· `_l4_intel_status_<code>.json`(intel_status:343-344;passport:274)· `ensemble/<code>.run<i>.md`+`_ensemble_<code>.json` · `<ticker>_<date>_slim[_deep].md`(harvest:1884-1897;prompts:240-241;l4_tasks:353/604;chain_view:302-305)。新增 JSON 卡要同步:contracts/artifacts 登记表、scan/artifacts.py:44 ArtifactSpec、l4_tasks 的 ("prompt","slim","card") 三件指纹集(:416/:694/:1045/:1070/:1142——**出现 5 处**)、capsule/retention expected 清单(artifacts.py:237-265)。
2. **代码/后缀格式**:6 位 zfill 散布全链(parsers `_decision_text` 的前导零 glob 兜底 :246-249 是疤的补丁);ticker 后缀单一口径 `.SS/.SZ/.BJ`(prompts:206;harvest:1927;producers `.SH` 守卫 :360)。
3. **散文正则族**(§2 表的 24 项;核心在 parsers.py:14-35、rating.py:25、tripwire_watch:32-43、health:75/:227、l4_watch:160、prompts:36-38、self_review:1204/:1271、price_claims 全模块)——JSON 双写后这些全部要改读 JSON 或保留为 markdown 对账器。
4. **节名/标记字符串常量**:`研报体(档案δ)`/`微研报`/`档案未建`(self_review:32-34 ⇄ l4-card.md ⇄ test_agent_defs:90-107)· `档案对账`/`变化项`(self_review:315-324 ⇄ context.py:188)· `〔卡契约 v4·隔夜 c1→o2〕`(self_review:39;test:63-87 整行 byte 锁)· `OW三门`+三门名(parsers:27 ⇄ rubric.py:8 ⇄ agent_output:53 ⇄ 模板 ⇄ test:57)· 停因七词表**三副本+relative_buy 死条目**(§2 脆弱点⑥)· `# `标题+`〔`次行(publisher:160-164)。
5. **行数/字节预算已漂**:44 vs 36 行、2.2K vs 1.8K、4.5K vs 3K(l4-card.md:143-144 vs lite-playbook.md:57/:177-178 vs STAGES.md:174)——同步机制(子串锚)结构性覆盖不到数字。
6. **agent/role 名字**:`l4-card`(l4-stock.js:410 + ens 复用 :444;usage_reconcile:68 把 "l4-card"→(l4_card,ens_review);artifacts.py:141 producer 字段;agent_output role)· `l4-intel` · role 闭集 `l4_card/ens_review/l4_intel`(scan_config:46-48 ⇄ AGENT_DEFAULTS l4-stock.js:48-55 ⇄ `_ROLE_FALLBACK`,test_agent_defs:632-652 node 求值锁)。
7. **workflow JS 烤入字符串**:卡派发句(:409 含落点+FINAL 行要求)、CARD schema(:79-81)、RANK 反向表(:311,contracts emit 生成)、`isOW`/`isSellish` 正则(:431-432)、heredoc 写 ensemble(:474-480)、`SD` 路径在 JS 里复刻 workspace 逻辑(:71)。test_agent_defs:213-247/:318-343/:553-563 把这些子串逐一钉死——改 JS 先改测试。
8. **模板双真身**:l4-card.md 自称 lite-playbook 的烤入版(l4-card.md:9),同步只靠 test_agent_defs:38-61 的锚;而 lite-playbook 末节(:185-187)整段内嵌 scan 内部件(l4-stock/finalists.csv/staging 路径/scan_config.agents.l4_card/assemble/已死的 verify.csv)——stock-research 文档反向依赖 scan-market 实现细节。
9. **slim 落点共用 `ws.scan_input_dir`**(workspace.py:125-130):独立 harvest 与扫描共享同一路径函数,RUN_ID 在位时独立跑会写进 run 现场(§5)。
10. **契约层声明未接线**:agent_output.L4_CARD 的严格 Rating 正则没有替换 parse_rating 的泛+兜底(rating.py:25/:42);`[执行线]` 阈值双份无对齐测试(agent_output:16)。
11. **发布副本被四次原地改写**(publisher:196-245),而昨卡回声/price_claims/chain_view 读的是改写后的文本——JSON 双写必须决定"发布注入"落在哪一份。
12. **`_l4_brief`/`pick_rating_aligned_evidence` 两套宽度**(96 vs 80,parsers:114/:148;report_model:59)+ 旧调用方并存(:130-137 注)。
13. **E6 链对 DecisionRecord 形状的 B 类冻结**:verify 三读者/`first_rejection` 档位/evidence_refs 都算 schema(decision_finalize.py:45-57「删掉会改 DecisionRecord 形状…B 类冻结禁区」)——卡→record 的投影不能顺手改。
14. **测试网**:tests/scan/{test_card_lint, test_card_v4_switch, test_early_stop_parse, test_gate_status_tolerant, test_tripwire_watch, test_l4_prompt_cache_prefix, test_l4_dispatch_pack, test_l4_dispatch_pinned, test_l4_dossier_inject, test_l4_watch, test_l4_tasks*} + tests/test_agent_defs.py——每一条格式改动都要同批扫这些锚(锚本身还有"历史注也能满足锚"的前科,test_agent_defs:435-452)。

**「JSON+MD 双写」最小可行切口**(供 brainstorm 参考,非建议书):卡 agent 在写 md 的同时写 `details/<code6>.json`(rating/proposal/conviction/gates/early_stop/target_band/rr/tripwires/exec_line/evidence 两侧),消费侧从 `_finalist_row`/`gate_status`/`parse_early_stop`/`parse_tripwires`/`_rating_of` 五个 owner 函数入手改为「JSON 优先、md 对账回退」——因为其余 15 个消费者几乎都经由这五个函数或它们落的 `_final_ratings/_early_stop/decision_records` 二级产物;真正绕不开的直接读卡者只有 publisher 注入、price_claims、citation_density、yesterday_echo、chain_view 定位五处。

---

## 7. 一句话结论:接缝最脏的三处

1. **一张 markdown 卡被 20 个消费者用 24 组各自维护的正则拆**,而评级这个最贵的字段用的还是"泛正则+全文第一个评级词兜底"(rating.py:42-46),三门段"无标记=全 PASS"(parsers.py:305-331)、早停行格式错=归因整桶挪位——contracts/agent_output.py 已经声明了唯一真身却没接线,声明与执行两张皮。
2. **"lite 档=stock-research"是名义上的**:模板双真身只靠子串锚同步(行数/字节预算已各说各话),模板结构(漏斗简报/L3 论点裁决/研报体/持仓管理/独立初判)全是 scan 专属且一半是死契约(chk_blind_pass 零消费者);独立调用时输入无源、输出零消费者、还可能把 slim 写进别人 run 的法证现场(workspace.scan_input_dir 分支)。
3. **卡写出的可执行信息几乎全被丢弃**:入场/目标/止损/R:R/置信度/conviction/执行线阈值/持仓管理无一进账本或被事后核验,outcome 只吃 rating+停因两个字段,tripwire 在 run 分区下还疑似读不到昨卡——「卡片→执行→复盘」的闭环里,卡片这头写得最厚、被读得最薄。

_(审计为只读;未修改任何仓库文件。)_
