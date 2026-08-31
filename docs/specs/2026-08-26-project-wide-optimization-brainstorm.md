# 全项目优化点 brainstorm(2026-08-26)

> **性质**:候选清单 + 待裁问题,**零实施**。不是调度权威(现行总调度仍是 08-26 现场留存 × BUY 所有权设计稿 §9.4 的活体验收 + 20 个结果日裁 A 去留)。
> **证据来源**:本 session 五路只读审计(工作流成本结构 / 数据与运维 / 代码热点 / 文档与 prompt 层 / 结果账本定量首读)+ 主会话逐条抽验。凡引用 `file:line` 的都是审计现场读到的;标 **UNVERIFIED** 的是估算或未复核的断言。
> **账本首读的仪器与全表**:`docs/research/2026-08-26-ledger-first-readout/`(`readout.md` 42KB 全表;`build_merged.py` → `analyze.py` 可复跑,抛弃型,整文件 `ruff: noqa`;需 scipy)。

---

## 0. 边界:不重提的东西

| 类 | 内容 | 出处 |
|---|---|---|
| 用户裁定 | 持仓=超短 1~2 日,主尺 `gap_c1_o2`;**5–10 日窗口三次裁不换**(07-10 / 08-05 / 08-22b);learning 层整体退役且真删(08-21);L4 TTL 复用不恢复(07-29);scan_config.jsonc 唯一参数事实源;双引擎隔离;sector-brief sonnet 试点已回滚(07-12) | 记忆索引「用户裁定」节 |
| 负结果 | 衍生品三族(QVIX/PCR/到期日历)零证据;追当日大涨 −4.85pp;52 周高因子;反弹日科技召回;预告事件通道;L2 模型 zoo;菜单内任何确定性分数无信号(L3 无选择 alpha) | `factor-backlog.md`、`STAGES.md` §「已被实证否决」 |
| 刚合入待验 | 08-26 五批(守卫⑨ composite 席 / E6 v3.0 / 执行线 / 结果账本 / chain_view / MANIFEST);7 条活体验收待下次真跑;**BUY 所有权设计本身本稿不动**,只列它的接线缺口(§2-A6/A7) | `2026-08-26-scene-retention-and-buy-owner-design.md` §9 |
| 本稿核掉的假想 | 「regime 43 日从未打 trend 是失准」——**不是**:全市场 60 日中位动量 07-10→08-25 一直在 −10%~−20%、宽度 0.11–0.48(`market_pack.json` 逐日),分类器按规则就该这么打 | `common/regime.py:41` 阈值 + 29 份 market_pack |

---

## 1. 现状一页

**体量**:`autoresearch/` 44.7k 行 Python(`scan/` 23.5k / 74 文件);测试 256 文件 / 40k 行 / **2974 绿**,全量 **4:17 墙钟但 CPU 只占 21%**(≈2.5 分钟在等子进程)。65 个 run / 43 个数据日(06-18→08-25)。**08-01 起 16 个提交日 238 次 commit**(≈15/日)。

**一天的扫描(08-25 真跑,`token_usage.md` + `_budget_observation.json`)**:$27.15 / 93 min。

| 花在哪 | $ | 占比 | 墙钟 |
|---|---:|---:|---|
| L4 卡 + 情报(5 只,含 1 次 📌 sell 复核) | 10.77 | 40% | L4研究 1419s + ensemble 615s |
| **47 个「壳」agent(sonnet·low,只跑确定性命令)** | 5.90 | 22% | — |
| 主会话(52 条消息,均 ~123k 上下文) | 5.35 | 20% | — |
| L3 精排(l3-rank ×1 + repair ×1) | 2.64 | 10% | 1153s |
| 行业 brief ×6(opus·xhigh) | 1.92 | 7% | 170s |
| 策略师 | 0.55 | 2% | 150s |
| **L0L1L2(零 LLM)** | 0 | — | **1916s(34%)= 1260 帧取数(串行网络)+ 150 策略师 + 507 prelude 尾** |

**效果(结果账本首读,879 行 → 去同日重跑 627 行;相对全市场中位;`t_NW` = Newey-West 修正重叠窗)**:

| 家族 | 隔夜 `gap_c1_o2` | `fwd_5_oc` | `fwd_10_oc` | 一句话 |
|---|---|---|---|---|
| L3 finalist(全史 40 日) | **−0.26pp(t −3.85)** | −0.76(t_NW −0.78) | **−2.39pp(t_NW −2.03)** | 判断层选出来的票三尺全负 |
| finalist·rated era(07-10 起) | −0.32(t −4.04) | −0.82(n.s.) | −2.80(t_NW −1.49) | 同上,量级更大、显著性略弱 |
| L3 conviction ↔ fwd_10(rated era) | — | rho **−0.24(p 0.002)** | 逐日 IC **−0.25(t −2.75)** | **L3 越自信,10 日越差** |
| 早停「基本面恶化」 | −0.09 | −3.76(t −3.5) | **−6.85pp(t_NW −5.8,n=10)** | L4 的拒绝在 5–10 日有价值 |
| 早停「涨停追高」 | **−1.54(5/5 负)** | −3.51 | −4.31 | 同上 |
| lane=reversion(06 月旧反转路) | −0.01 | +2.29(t_NW +2.7) | +2.57(t_NW +4.1) | 唯一三尺非负的 lane(**5–10 日尺,只记不动**) |
| lane=healthy | −0.28(t −3.2) | −1.09 | −2.77 | 与 08-22 普查一致 |
| 📌 持仓(11 只/26 日) | **+0.62**(t 0.8) | +1.51 | +0.61 | 全表最好家族;**08 月** fwd_5 **+6.2(t_NW 3.9)**/fwd_10 **+11.0(t 2.5)** |
| E6 active BUY | n=2(中国石油 +1.04 / 金螳螂 −0.78) | 未成熟 | 未成熟 | 没法读 |

> 一个方法论旁证:T+1 强收盘(区间上 30%)在 `fwd_5/10_oc` 上看似 +3~+4pp,但**全市场对照组同样 +3.4/+3.0(t 6.9/4.6)**,换成从 T+1 收盘起算的 `c1→c5/c10` 后全市场 ≈0——那是 `fwd_*_oc` 从 T+1 开盘起算把 T+1 当天涨幅算进去的定义效应,**不是**新信号。而隔夜尺上强收盘 −0.22(t −3.35)也是全市场现象 → 08-26 执行线「弱收盘才入」量的是市场微结构,不是本系统选票。

**运维**:三个 launchd 任务 **两个死**——`scan-prewarm` 08-22 起 exit 1(19:30 DNS 瞬断,`prewarm.py:145` 的 `latest_settled_trade_date()` 在 try 外、plist 无 KeepAlive/重试 → 一次瞬断=整晚白过;死前耗时已从 335s 中位涨到 1670s/3784s 无人告警);`nightly-close` exit 1(`scripts/nightly_close.sh:6` 仍 exec 已删除的 `autoresearch.learning.nightly_close`);`macro-harvest` 绿,但 LLM 腿无人跑 → `macro_state.json` 停在 07-27(29d 过期警告的真因)。

**欠账**:档案 94 只待建、**0 自动化**(≤3/晚的帽只存在于 SKILL.md 散文里),10 个 run 里只有 4/28 份档案被 L4 注入过;新闻层 2167 行、8253 条观测**无写者**(`ingest_flash` 的唯一调用方随 nightly_close 死)、唯一消费者是 prelude 健康行;`lake/` 3.9G 里 **3.5G 是冻结的研究语料**(`stk_factor_pro` 2.9G 生产零读、261 列存 9 列用);`PANORAMA.md` 第 7 章仍活体描述已删的闭环层 + 教 4 个死 CLI;README 340 行里 240 行仍是上游 LangGraph 框架;CHANGELOG 停在 06-19。

---

## 2. 候选池

变更分类沿用 08-03 brainstorm §0.4:**M**=计量/勘误(零行为变更)· **I**=基建/接线 · **B**=生产行为变更(须走裁决 + 活体验收)。

### A · 效果层(「分析有没有用」)

**A0 · 节奏:行为类改动排队,先把 20 个结果日攒出来(B 类治理,建议)**
08-01 起 238 commits,而账本里 active BUY 只有 2 个可读日;每一波行为变更都把「20 个结果日」的钟重置一次(08-19 E6 转正 → 08-22 形状波 → 08-26 所有权波,三次重置)。建议:到 **2026-09-中**(20 个交易日)之前,只做 M/I 类(本稿 §B/§C/§D 大部分),B 类只排队不上线;A 去留的裁决才有干净的样本。**不选的后果**:再来一波,裁 A 时又是 n<10。

**A1 · 把「拒绝」做成正式产物:避雷单(B 类,需裁)**
证据:finalist 10 日 −2.4pp(t_NW −2.0)、conviction 与 10 日逐日 IC −0.25(t −2.75)、早停「基本面恶化」−6.9pp、「涨停追高」5/5 负;`STAGES.md` §二早写着「判断层已证的 edge 在拒绝不在挑选」,但拒绝从来不是产物,只是 BUY 的副产品。候选形态:brief 加「⑦ 避雷」一行 = 当日早停名单(带停因)+ 持仓里命中同类停因的票;先**影子记账 20 日**再定要不要给人看。**边界**:这不是换尺——BUY 主尺不动;避雷单用在「不买/减」侧,评价尺用 `fwd_10` 只因为它量的是「别碰多久」,不是持有多久。**不选的后果**:系统唯一有统计证据的能力继续不落纸。

**A2 · 非席位 finalist 的 L4 卡还要不要做满(B 类,需裁)**
路 A 之后 BUY 只从 composite 席位选;非席位 finalist 的 5 张卡/日(≈$16 + 30 个壳 agent)只剩两个用途:brief「研究评级分布」一行、账本的判断层对照臂。而 Q3 读数:L4 评级对 5–10 日**没有排序力**(pooled rho +0.06 p 0.46;逐日 IC 方向互相打架),有信息的是早停停因。选项:(a)**保留到 20 结果日**当对照臂(建议);(b)之后降为「P1–P3 否决卡」(只出停因,不出评级、不做 P4/P5、不派 intel);(c)全砍。(b) 的日成本估 −$8~10(UNVERIFIED)。

**A3 · 持仓复核做厚(I→B 类)**
📌 是全表最好家族(隔夜 +0.62;08 月 5/10 日 +6.2/+11.0),用户自己的选择 > 系统的选择,系统的价值是**盯梢**。现状:持仓一行 + tripwire 日检 + 中报日历(08-28 协创数据披露,prelude 已提醒)。候选:①持仓卡的早停停因映射为减仓触发(与 A1 同源);②`tripwire_watch` 现只扫 `stock_news_em` 标题、`anns_d` 已退役(`tripwire_watch.py:15` 自陈)——公告面靠 cninfo fallback,需要把 fallback 接进盯梢;③pinned 的 sell_review 复核(08-25 那次 615s 空转,复核结果与原卡同档)改为「只在复核会改变持仓动作时才派」。

**A4 · lane 读数只记账**:reversion 是唯一三尺非负的 lane,但那是 5–10 日尺的证据(第三份,前两份见 08-21 Gate 0、08-22 普查)。按裁定**不据此动作**;若某天重开路 B,证据在 `readout.md` Q2。

**A5 · 「没有生产者的消费者」清单(I 类,决定接线或删分支)**
`relative_buy.py:1054-1077` 已知问题 ①「监管/审计红灯」token 永远匹配不到七词表 = 死条件;②逐票 `_price_claim_status.json` 无生产者 → 证据面「+0.2 clean」零区分、硬门③价格分支永不触发;`dossier/pool.py:95` pending 条目对消费者永不可见;`research/factor_lab.py:198` `plan.pkl` 只在 harvest 写、`calibrate` 从不重排 → 07-02 之后的扫描日永远进不了面板(**权重面板 F 止 08-05 的根因候选**,UNVERIFIED)。

**A6 · l3-rank agent def 漂移(I 类,08-26 波收尾债)**
`.claude/agents/l3-rank.md:16` 必读 `_l3_calibration.md`「硬约束,逐条遵守」——**无生产者**(08-25 staging 无此文件;`retention-audit.md:33` 已独立逮到)→ 每次 L3 静默少读一份被写成硬约束的输入;def 最后一次改动 08-22(`3cce07f`),**守卫⑨/席位/BUY 所有权变更一个字没进 def**,它仍被告知「宁缺毋滥禁止凑数」而输出会被席位填充;`SKILL.md:100` 写 prelude「9 步」,`prelude.STEP_NAMES` 是 10(`outcome_fill` 漏列);`STAGES.md:14-16` 漏斗图卡数少算 3 张席位卡。`tests/test_agent_defs.py` 的 l3-rank 锚是唯一**没有真值源对照**的锚集,所以这些漂移 CI 看不见。

**A7 · `lake_manifest` 没接线(I 类,P0)**
`scan/retention.py:321 write_lake_manifest` 有测试、无生产调用点:`retain()`(:450-:473)初始化了 `"lake_files": 0` 却从不调它。08-26 设计稿 §9.2 第 4 条记「P3 湖清单当波做完」、§9.4 活体验收第 1 条要求「四件齐含 lake_manifest」——**下次真跑这条必红**。FN-1 家族第 N 次(生产者没接线)。

**A8 · 新闻层裁决(I 类 → 二选一)**
2167 行(`catalog` 1027 / `claim_ledger` 457 / `fulltext` 304 / `typed_events` 379),其中 ~1140 行零调用;唯一生产消费者 `prelude.py:437` 健康行;三个 flash 源最后一次写入是 08-20 12:48Z 的一次手工 CLI(16 秒内三源同批),`stock_news_em` 停在 07-24。按「输入没人产就不留」:**要么**接成 l4-intel 的「已知底」(它现在六面盲搜 ≈$0.56/票,目录可替掉一部分网查)+ 给 `ingest_flash` 一个 cron;**要么**整包退役,健康行同删。

**A9 · 档案链裁决(I 类)**
池 28 active + 94 pending;10 个 run 只注入过 4 份(300857 ×10、601869/688766 ×5、603338 ×1);prewarm 每晚预取 29–30 份喂 1 份;SLO 三项 🚨 连红但队列只能靠人敲 `dossier-init`。选项:①池收缩到「近 20 日出现 ≥2 次的 finalist ∪ 持仓」并把首覆挂进夜间 cron(≤3/晚,≈$?/只 UNVERIFIED);②只保留持仓档案,SLO 行退役;③维持现状但把 SLO 改成诚实的「无消费者」标记。

### B · 成本 / 时长

**B1 · prewarm 修好(I 类,P0)**:`latest_settled_trade_date()` 进 `_step()` 保护;`_ts_call` 重试加抖动 + 上限;plist 加 `KeepAlive`/`StartInterval` 或 21:00 二次尝试;prelude 汇总屏已印「✗ 未跑」但没人看——见 C3。**量级诚实**:预热只把 ~81 次往返变湖命中(60 日 `daily` + 21 次证据端点),`stk_factor_pro/cyq_perf/moneyflow/hk_hold/margin_detail/daily_basic/daily×3/stock_basic` **10 个全市场快照端点绕过湖每次重拉**(`tushare_source.py:187-380` 全是裸 `pro.X()`)。所以 B1 单独救不了 32 分钟。

**B2 · L0L1L2 1260s 串行网络 → 并发 + 缓存(I 类,先计量后动)**
`performance` 块只有 `streaming_l4`,**没有任何 tushare 并发旋钮**;仓里三个 ThreadPool 都不在 L0/L1/L2 路径。`trade_cal` 一次 run 拉 ~10 次(`_trade_days` 无 `lru_cache`,`endpoints.py:76` 明明登记为 `static`);`daily` 末日被两条路各拉一次(`frame.py:93` 走湖、`tushare_source.py:376-378` 裸拉)。**前置**:`prelude._run_steps`(:66-76)只记 `{step, ok, note}` 不记时长,本稿所有耗时全靠 mtime 反推 —— 先加每步 `duration_s`,再动并发。目标量级 1916s → ~600s(**UNVERIFIED**)。

**B3 · 壳 agent 47 → 31(I 类,≈ −$2.0/日)**
公式 `14 + 2·(l3 lint 失败) + 6N + ens`;N=5 恰 47。可合并(顺序已核):`frame`+`pack-check`、`pack-check`+`strategist-pack-check`、`GATE1`+`run-mode`、`l3-lint`+`repair-pack`、`repair-apply`+`GATE2`、`l4-prep`+`dispatch-plan`+`l4-tasks-init`(**`dispatch-plan` 是纯重复**,`l4_tasks.py:739` 在 init 里重算同一份)、`l3-prepare` 并进 `sector-pack+list`;每股 `intel-guard`+`intel-status`、`ens-dump`+`task-success`+`recordL4`。**不可合**:`preflight`+`prepare`(preflight 返回值决定是否派 intel,且 prepare 与 intel 并行)。每个壳 ≈$0.125 且成本几乎与命令无关(26–42k 5m cache 写 = 每次 spawn 重付一份前缀),所以「换更便宜的壳」在 sonnet·low 已到底,只剩「少 spawn」。

**B4 · 主会话瘦身(I 类,≈ −$1.5~2.5/日 UNVERIFIED)**
`args.config`(整份 user_config)被序列化进主会话 **6 次**(scan-market.js 1 + l4-stock.js ×5;`SKILL.md:106/109/140`),实际只需 `resolved_agents`(608B)+ `l4_intel` + `engine` → 传路径;CP2 读 9 份行业 brief 17.8KB 只用首句 → 出一个 `render --view sector_headlines`(~1KB);`STAGES.md` 43KB 被 SKILL 指了 22 次,进过一次主会话就跟 52 条消息;CP7 五条命令的 stdout(`token_usage.md` 10KB)全进上下文。

**B5 · sector-brief opus·xhigh 出 2.5k 事实段($1.92/日)**——07-12 用户拍板回滚 sonnet 试点,**只列不推**;若重试须带活体对照。

**B6 · l3_repair 第二个 opus 上下文($0.51/日)换 4.4k 措辞补丁**,`scan-market.js:300-304` 自述「可选增益,不是必经关节」「不影响正确性」→ 可关(config `l3_repair` 已是独立 role)。

**B7 · 计量误报(M 类,P0)**:`usage_harvest.py:93-97` 只认 `stop_reason ∈ {end_turn, stop_sequence}` 为终态 → 所有带 `schema:` 的 workflow agent(32 壳 + 6 l4-card + 5 l4-intel)全标 INCOMPLETE,且污染 `discarded/retry` 计数;`trace/stage_results/budget.json` 是 writer-1 的陈旧副本(DEGRADED/UNMEASURED)而 `_budget_observation.json` 是 writer-2 的 MEASURED $27.15——`post_run.py:639-641` 只回镜后者。两处都是一行量级。

**B8 · ensemble 615s 的形状**:Verify 串在每股 workflow 内 → 全 run 等最慢一股(08-25 其他 4 股 21:30 完,300857 卡 21:38 + 复核 21:48);sell_review 结果与原卡同档 = 空转但仍付一张 opus 满卡。见 A3③。

**B9 · 测试套件 4:17 → <1:30(M 类)**:`tests/test_cli_entrypoints.py:62` 对 ≥60 个 `__main__` 模块各起子进程 `--help`;`test_frame_json_clean`/`test_user_config` 12 条各 10–15s(CLI 子进程 smoke);`test_factor_lab` 一条 33s。`-n auto` 或改 in-process `main([...])` + capsys,保留 1 条真子进程 smoke。CI 没开 `-n auto`,75 个 `@pytest.mark.unit` 标记从未用于选择。

### C · 可靠性 / 运维

**C1 · nightly-close 死任务(I 类,P0)**:删 plist+脚本,**或**改造成新的夜间任务干它本来该干的活——T+1 晚 `outcome_fill`(给昨日 BUY 落 `exec_ok`,08-26 §9.4 第 7 条正是这个),顺带 `ingest_flash`(若 A8 选接线)与 dossier 首覆(若 A9 选 cron)。

**C2 · macro LLM 腿**:要么 scan Stage 0 派 macro-brief 时顺手刷 `macro_state.json`,要么把 7d 过期阈值改成周频实情(30d)并停止空警告。

**C3 · 「报警静默失败」家族(M 类)**:`prelude.py:405-431` 三处 `contextlib.suppress` 让 SLO 🚨 直接消失而不是报错;`tushare_source.py:198-204` `stk_factor_pro` 失败只 `print` 然后用代理列(`ma_bull/rsi6` 无标记);`pe` NaN 率 14.8%→22.1% 十连涨从未进 `degraded_fields`;prewarm 耗时 11 倍暴涨无人告警。建议:prelude 汇总屏加一行 **「任务健康」= 三个 launchd 任务 last-exit + 最近产物时间 + prewarm 耗时**,并把 `degraded_fields` 的准入从「字段级 NaN 阈值」扩到「趋势」。

**C4 · 死源仍占位**:北向 `hk_hold` 十连空(`northbound.n=0`)但召回通道仍持 `quota=120/floor=30`(`recall/channels.py:131`,`health.py:132` 自陈「quota 白占」);`missing: ["verify.csv"]` 十连;`anns` 十连 fallback(cninfo 无 key 无契约档)。北向通道该显式关掉或改条件启用。

**C5 · 三条永远 skip 的真产物测试(M 类,一行修)**:`tests/scan/test_passport.py:688`、`test_relative_buy.py:817`、`test_strategist_pack.py:157` 硬编码 `context/scan/…`(08-11 分根前的旧根)→ 08-11 起 100% skip,本地和 CI 都是。

**C6 · ruff/CI(M 类)**:本地 `autoresearch+tests` 57 条、全仓 77 条(19 条来自 `docs/research/2026-08-26-buy-owner-spikes/*.py`);CI 是 `ruff check .` strict → **若 Actions 在跑必红**(gh 未登录,UNVERIFIED)。42 条 `--fix` 一键;spike 脚本决定「排除还是按生产标准」(本稿新增的两个 spike 用了整文件 `noqa`)。

### D · 代码 / 文档债

**D1 · 大模块的天然缝**:`self_review.py`(1548)里 `retired_symbol_lint/workflow_literal_lint/stale_ruler_lint`(:366/:457/:577)读 `.claude/**` 和 git,与 run 产物零耦合 → `scan/doclint.py`;`product_shape_lint` 368 行 = ~15 个探针共用一个 `add()`。`relative_buy.py`(1078,118 行 docstring 是 spec)= 纯门/面 | 决策装配 | 持久化校验 | config 门面;`report_sections.py` 从 `decision_finalize` 进口 13 个私有符号;`post_run.py` 把 outbox 消费框架、观察报告渲染、**相对 BUY 第二写者**、档案入队四件事装一起。

**D2 · 重复实现**:`trade_days` 4 份(`edge_census:54`/`temperature_calib:35`/`derivatives_census:434`/`tushare_source:90`);六位代码归一 4 份字节相同 + 裸 `zfill(6)` 209 处/40 文件;私有 JSON 读 8 份;原子写 ≥12 份;评级序表 4 份;**无共享 markdown 表渲染器**(33 文件手写 `|---|`);`ws.scan_dir()` 存在但 ≥15 处绕过;`common/ruler.py:77` 自陈「仓里已有 6 处同构 `_bool_series`」然后加了第 7 处;`temperature_calib.py:79` 有一个**同名不同义**的 `forward_returns`。(前向收益本体是共享的:`factor_lab.forward_returns` 单一生产者,`outcome/edge_census/lowturn_precheck` 都走它——记忆里那句「逐字同源」已核实为真共享,不是复制。)

**D3 · 类型**:全包 0 个 `TypedDict`;`scan/` 139 个 `-> dict`、87 处 `json.loads(path.read_text())`;16 个 dataclass 全是信封(StageResult/RunContract/…),**载荷**(finalists 行/passport/决策文件/任务簿/run_health)全是裸 dict——这是 D2 里 8 份 JSON 读的根因。候选:一个 `scan/artifact_models.py` 只覆盖 5 个最常读产物。

**D4 · 文档**:`PANORAMA.md`(基线 07-16)第 7 章 130 行活体描述已删的闭环层、:1044-1047 教 4 个死 CLI(`learning.retro/zero_buy_ledger/gate_ledger/channel_ledger` 全 import 失败)、:108 流程图仍路由到 `scan-retro`/`feedback`;`AGENTS.md:3`「六个项目技能」实际 4 个;`README.md:94-330` 仍是上游;CHANGELOG 06-19 后无记录;110 份 pre-07-28 spec/plan 里 **62 份零引用**(42 份连别的文档都不引)→ `docs/archive/pre-2026-07-28/`;`docs/` 无索引。doc-lint `tests/test_skill_docs_refs.py:20-23` 的 `_docs()` 只扫 `.claude/skills/** + CLAUDE.md + README.md` → 扩到 PANORAMA/AGENTS 就能逮住上面大半。`test_agent_defs.py` 53 次 commit 是全仓最高频测试(锚字面串;08-08 commit 标题「第三次复发」)。

**D5 · 残留**:`tests/learning/` 空目录;`scan/l4/intel_guard.py:28/:272/:284`、`tests/scan/test_intel_trim.py:74`、`test_anns_probe.py:11` 注释仍指 `autoresearch/learning/`;`context_claude/learning/` 4 份无生产者账本;`reports_claude/scan/l2_slo.{json,md}` 退役产物;6 个零引用函数候选(`common/stats.py:57`、`scan/brief.py:640 safe_write`、`outbox.py:184`、`retention.py:321`←**A7 说明它不是死码是没接线**、`l4/intel_status.py:247`、`temperature_v2_guard.py:95`)——删前逐个查 git 何时落地。

**D6 · 存储(不可逆,需确认)**:`lake/` 3.9G 中 8 个端点各 464 文件(2022-06→2026-08-05)是 factor_lab 回填语料,factor_lab 自己读的是 `.pkl` 缓存(`factor_lab.py:45,476`)不读这些 parquet;`stk_factor_pro` 345 个旧文件 261 列 vs 生产 9 列;`context_claude/factor_lab` 396M、`replay` 19M(已退役)。全仓 ≈4.9G **无任何 retention 策略**(`scan/retention.py` 是「多留」不是「清理」)。选项:归档到外部盘 / 只留 2026 / 全删。

**D7 · 依赖与环境**:`langchain-core` 只为 `@tool` 装饰器;`.venv` 是 py3.13、`pyproject` target py3.10、CI 用 3.12;`uv.lock` 07-13。

### E · 产品 / 路由

**E1 · 技能触发冲突(需决定)**:第三方插件 `stock-deep-analyzer` 的 `deep-analysis` 声明「深度分析 / 全面分析 / 帮我看看 / 值不值得买 / 个股…」,与 `CLAUDE.md:12`「研究 NVDA / 分析 600519.SS → stock-research」正面撞车;`scan-market` vs 插件 `screen/quick-scan`、`sector-research` vs 插件行业综述、`l4-card.md:29`「机构净买=反指」vs 插件 `lhb-analyzer` 相反口径。项目文档零处提及插件,`.claude/` 无 settings.json,**没有任何优先级规则**。建议一句话进 CLAUDE.md:「本仓个股/行业/全市场研究一律走项目技能;插件只在显式 `/stock-deep-analyzer:*` 时用」。

**E2 · Codex 引擎路径**:08-11 起零使用(`context_codex/`、`reports_codex/` 空);保留隔离机制,但 `AGENTS.md` 的技能计数与 prelude 描述要改。

**E3 · brief 的两行**:20 日后的「结果账本读数」已在计划;建议同时加「昨日推荐票 T+1 实际(涨跌/exec_ok)」一行——`outcome_fill` 已经有数,只是没人印。

---

## 3. 建议优先序(仅建议)

| 档 | 内容 | 类 | 工量 |
|---|---|---|---|
| **P0(本周,一天内)** | A7 `lake_manifest` 接线 + 存在性测试 · A6 l3-rank 死引用/守卫⑨/9→10 步 · C1 nightly-close · B1 prewarm 三处 · B7 两处计量误报 · C5 三条假 skip · C6 `ruff --fix` | I/M | ≤1 天 |
| **P1(计量先行,零行为变更)** | B2 前置每步计时 → 并发化 · B3 壳合并 47→31 · B4 主会话瘦身 · B9 测试提速 · C3 任务健康行 · C4 北向通道显式关 · D4 doc-lint 扩域 + PANORAMA 第 7 章标「已退役」 | I/M | 2–4 天 |
| **P2(需裁决)** | A1 避雷单影子 · A2 非席位卡去留 · A8 新闻层二选一 · A9 档案链 · D6 存储 · E1 路由 · A0 冻结窗 | B/I | 各半天~2 天 |
| **P3(攒着)** | D1–D3 重构 · D4 归档 · D5 残留 · D7 依赖 | — | — |

---

## 4. 待裁(用户)

| # | 问题 | 本稿建议 | 不选的后果 |
|---|---|---|---|
| Q1 | A0:到 09-中之前 B 类改动冻结、只做 M/I? | 冻结 | 裁 A 去留时样本再次被重置 |
| Q2 | A1:「拒绝」要不要做成正式产物(避雷单,先影子 20 日)? | 做影子 | 唯一有证据的能力继续只是副产品 |
| Q3 | A2:非席位 finalist 的满卡,20 日后是保留 / 降为否决卡 / 砍? | 20 日后降为否决卡 | 每天 ≈$16 买一列没有排序力的评级 |
| Q4 | A8:新闻层接线(cron + 喂 l4-intel)还是整包退役? | **退役**,除非愿意给它一个 cron 主人 | 2167 行无主代码 + 一条撒谎的健康行 |
| Q5 | A9:档案池收缩 + 夜间 cron,还是只留持仓档案? | 收缩 + cron(≤3/晚) | SLO 永远三红 |
| Q6 | D6:3.5G 冻结语料归档/删? | 归档到外部盘后删 | 无害但无限增长 |
| Q7 | E1:插件与项目技能的优先级写不写进 CLAUDE.md? | 写 | 「分析 XXX」哪天走错技能没人知道 |
| Q8 | B6:关掉 l3_repair? | 关 | 每天 $0.5 换措辞补丁 |

---

## 5. 局限

- 账本首读 40 个数据日、单一深熊 regime(全市场 60 日中位 −10%~−20%);`fwd_5/10` 窗口重叠已用 NW 修正但 n_days 仍小;08-19 后无 `fwd_5/10`;active BUY n=2 不可读。
- 成本/耗时读数来自**一次**真跑(08-25);壳 agent 单价 $0.125 是当日均值。
- 所有「省多少」的量级除 B3 外均 UNVERIFIED,须先加计量(B2 前置、B7)再动。
- 仅供研究,非投资建议。
