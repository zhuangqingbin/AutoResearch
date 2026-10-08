# scan-market 运维细节

> 2026-09-26 自 `.claude/skills/scan-market/STAGES.md`「运维细节」「计量与跨层校准」两节整段迁入(A2-3 文档瘦身:skill 文档只讲现行机制,运维与账本口径住这里)。内容原样,冲突以源码为准。

## 只读研究仪器(手动)

- **低位转强 Gate 0 回测**:`uv run --no-sync python -m autoresearch.research.lowturn_precheck [--cap-floor 30]` → `reports_claude/research/lowturn_precheck.md`。前向收益由 `factor_lab` 面板**现算**,不依赖任何账本。**参考尺只观察,决策尺不变**(2026-07-10 / 08-05 裁定)。(原 `--live` 活体双尺观察腿读 `retro/attribution.csv`,已随闭环删除。)
- **漏斗形状对照实验**(只读,手动,**预注册**,不接 prelude):`uv run --no-sync python -m autoresearch.research.funnel_variants --spec <已冻结方案 spec.json> --scan-dir <冻结日目录> [--scan-dir …] --outcomes <收益表>` → `$RPT/research/funnel_variants/<experiment_id>/`(`spec.json` / `membership.csv` / `daily_metrics.csv` / `paired_summary.json` / `manifest.json`;实验目录排他创建,已存在即拒 —— **没有 `--force`**,想改假设就换 `experiment_id` 开新实验)。
  逐日重建三个**同预算**漏斗(`current` 读冻结产物 / `composite_only` / `composite_plus_diversifiers`),同 L1/L2/pass1 名额、同主尺 `gap_c1_o2`、同可交易定义,按 date 等权配对。收益表需 `date`/`code`/`gap_c1_o2` 三列(有 `status_gap_c1_o2` 则按 MATURE 判成熟);**缺收益留空不填 0**。
  **旋钮全在 spec 里,命令行上一个都没有**:80/20 core、行业帽、style floor 写死在 `selection_rule`,bootstrap 的 `n_boot`/`seed` 与最小共同日(`maturity_policy`)同理。开跑前逐项验:引擎、evidence/cost 模式、`code_sha` 对 behavior roots 无漂移且工作区干净、输入清单 sha256 逐文件对得上、分析日全部落在冻结的 test 区间内;任一条不过就拒跑且**一个字节都不落盘**。家族登记见 `docs/research/2026-09-13-funnel-shape-family.spec.json`(过 F1 契约,`tests/research/test_family_registry.py` 守)。
  读法只有一条:`paired_summary.json` 的 `evidence_status` —— `PROMOTION_EVIDENCE`(≥20 个共同成熟日 ∧ bootstrap 90% CI 下界 > 0)才够资格**另开**设计与回滚计划;`INSUFFICIENT_EVIDENCE` / `NO_SWITCH_EVIDENCE` 都是「尚无证据切换」,**不是「证明两者相等」**。本命令不写任何 run 目录、不改任何生产参数。设计:`docs/superpowers/specs/2026-09-13-funnel-shape-gates-dual-engine-design.md` §3。

## 夜间预热(launchd)

交易日 19:30 自动 `scripts/prewarm.sh`(= `python -m autoresearch.scan.prewarm`,湖预拉+温度)。跑过预热的日子开扫全湖命中(L0-L2 ~6.5m);**当天有没有预热看汇总屏「预热(夜间):✓/✗」行**。安装:

```bash
sed "s|__REPO__|$PWD|" scripts/com.tradingagents.scan-prewarm.plist \
  > ~/Library/LaunchAgents/com.tradingagents.scan-prewarm.plist \
  && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tradingagents.scan-prewarm.plist
launchctl list | grep scan-prewarm          # 验证
launchctl kickstart -p gui/$(id -u)/com.tradingagents.scan-prewarm   # 手动触发
```

夜间补账 `scripts/nightly_close.sh`(交易日 23:30;2026-09-26 起,原 20:45,排在 21:20 无人值守扫描之后):outcome fill / ledger_views build / populations build / populations rulers / analyze ledger 五步,只记不学,每步带时刻行,`ledger_views` 写 `_health.json`。扫描锁被占(无人值守场还在跑)→ 每分钟再看,最多等 150 分钟(`NIGHTLY_SCAN_WAIT_MIN`),仍被占就**跳过**四个 scan 账本步(日志有「跳过」行),analyze ledger 照跑。

重装(20:45 → 23:30)。**先退役本机早期装的不带引擎后缀的旧任务**(Label `com.tradingagents.nightly-close`,20:45;只 bootout `….<引擎>` 会让它留着,一晚跑三次),再按引擎装新模板(只用 Claude 就只装 claude):

```bash
launchctl bootout gui/$(id -u)/com.tradingagents.nightly-close 2>/dev/null
rm -f ~/Library/LaunchAgents/com.tradingagents.nightly-close.plist
for E in claude; do            # 两个引擎都要:for E in claude codex
  launchctl bootout gui/$(id -u)/com.tradingagents.nightly-close.$E 2>/dev/null
  sed -e "s|__REPO__|$PWD|" -e "s|__ENGINE__|$E|g" scripts/com.tradingagents.nightly-close.plist \
    > ~/Library/LaunchAgents/com.tradingagents.nightly-close.$E.plist
  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tradingagents.nightly-close.$E.plist
done
launchctl list | grep nightly-close        # 只应剩 …nightly-close.claude(和 .codex)
```

## 无人值守扫描(headless · launchd 交易日 21:20 · PILOT;交互会话同一条路)

`scripts/scan_run.sh [--engine claude|codex] …` → `python -m autoresearch.scan.scan_run`,流程全在 Python(macOS 无 `flock(1)`/`timeout(1)`)。
2026-10-08 起这也是交互会话「分析全市场」的日常路径:宿主后台起它、等完成、读摘要,自己不进研究回路
(`docs/session-agent/README.md`「日常全扫」)。`--engine` 缺省 claude(launchd 定时场);Codex 会话传 `--engine codex`,
runner 以 `codex exec` 起每个推理任务(`executors/headless_codex.py`,开线程 → 绑定 → resume 三步,见 README「headless 执行器」)。

1. **锁** `$CTX/.scan_run.lock`(fcntl,进程死锁即放):被占 → 立刻退出、打印持锁 pid、推「未开」。
2. **窗口**:没带 `--date` 且此刻不在 21:10–22:30 内(本机睡眠/关机后 launchd 补触发)→ 推一次「扫描 <日> 错过」(日 = 最近已结算交易日;该日已有摘要则静默;同日 21:10 前 = 手动早触发,静默)退出 0,**不**在早上轮询 tushare、占锁一整天。
3. **交易日**:今天不是 → 静默退出 0(launchd 只按星期触发,节假日在这里挡)。
4. **人工场**:同引擎有 ACTIVE 且心跳 90 分钟内的 scan run → 不开,推送;就绪等待之后、begin 之前再查一次。
5. **湖灌齐** `python -m autoresearch.scan.readiness <日>`:stk_factor_pro ≥5300 行且连续两次不变(5 分钟一次),等到 22:30 仍不齐 → 推「未开」(晚开场也先读两次、间隔 30 秒再判)。**就绪后对账湖分区**:21:00 预热若已写下半载 `lake/stk_factor_pro/<日>.parquet`(行数 < tushare 稳定行数),改名 `<日>.parquet.partial` 隔离,扫描重取全量。夜间预热本身不用这道探针,行为不变。
6. **begin** `session_agent begin --orchestration session_v1 --ignore-scan-lock`(本进程就是持锁者):请求 `$RPT/_ops/scan_run_<日>.request.json`,宿主 `session_ref=headless-<uuid>`(runner 进程 + 每个推理任务一个 `claude -p` 会话;`force_full=false`,📌 由 SENTINEL_PINNED 兜)。距夜间硬截止(`--hard-stop`,缺省次日 01:00;只管定时场)不足 10 分钟就不 begin,推「未开」。
7. **runner** `session_agent run --executor headless`:每个推理任务 `claude -p --agent <role> --output-format json --permission-mode bypassPermissions --session-id <uuid> --max-turns N [--effort] [--model]`(不传 `--dangerously-skip-permissions`;项目 hook 照常生效)。子进程环境显式构造:`ANTHROPIC_*`(API key / auth token / base URL / 模型覆盖)、`CLAUDE_CODE_{SUBAGENT_MODEL,EFFORT_LEVEL,USE_BEDROCK,USE_VERTEX}`、所有 `*_API_KEY`/`*_TOKEN`/`*_SECRET`(`CLAUDE_CODE_OAUTH_TOKEN` 除外)一律不传,调用记录的 `env_stripped` 只列名字 —— `.env` 里填了 API key 或从 `cc-ds` 壳里手动触发,都不会把整场挪出订阅。超时 intel 12m / card·复核 25m / L3 30m → 杀整个进程组,TIMEOUT 以新 attempt 重试一次(overloaded / `API Error: 5xx` 同样重试一次);结果 JSON 非法 / `is_error` / 退出 0 但产物没落盘 = 该 attempt 失败;重试前上一次的产物挪到 `_dispatch/headless/stale/`,同任务还在跑的旧会话先停掉。整场墙钟 = min(180 分钟 `--run-timeout-minutes`, 距硬截止),超时连在飞的 `claude -p` 一起杀。
8. **收尾**:完成 → `verify-report --level full` → 送达 brief(标题带 ✓ 或 `verify ✗`),摘要 `result: "FINISHED"` + 独立的 `delivery.status`(SENT / SKIPPED / FAILED —— 没发出去不叫送达);未完成 / 意外异常 / 收到 SIGTERM·SIGHUP·SIGINT(`launchctl bootout`、`kickstart -k`、关终端)→ 停 runner 进程组与在飞 `claude -p`(TERM→5 秒→KILL)→ `capsule finalize FAILED` + 推「FAILED · 阶段 · 一句原因 · run · 日志」(信号场摘要 `result: "INTERRUPTED"`)。**不自动改代码、不自动重跑第二场。**

- **看什么**:日志 `$RPT/_ops/scan_run_<日>.log`(runner 的 JSON 事件行也在里面;launchd 的 `/tmp/scan-run.log` 只兜启动前的错);摘要 `$RPT/_ops/scan_run_<交易日>.json`(`result` / `delivery.status` / `delivery_channel` / `warnings`);每次 `claude -p` 一份 `<staging>/_dispatch/headless/<task>.a<n>.json`(同一 attempt 被重派时文件名多一段 session 前缀,不覆盖;argv 脱敏、pid + 启动时刻、exit、usage、`total_cost_usd`、transcript 路径、`env_stripped`、`claude_bin_resolved`)+ `.stdout/.stderr`;`token_usage.md` 多一列 `dispatcher`,headless 行成本 = 结果 JSON 的 `total_cost_usd`(transcript 找不到 = UNMEASURED,不计 $0)。codex 场的记录同一目录、`engine: "codex"`,多 `thread_id` 与 `open`(开线程那一轮的 argv / exit / usage)、`.open.stdout`、`.last_message.md`;rollout 按线程 id 反查,行的成本按 rollout 估(`codex exec` 不自报美元),找不到 rollout 同样 UNMEASURED。
- **电源**(MacBook):`scripts/scan_run.sh` 用 `/usr/bin/caffeinate -i` 包住整场,挡住电池 1 分钟闲置睡眠;但**合盖(无外接显示器)照样睡** → claude -p 中途睡死、角色超时、整场 FAILED。交易日晚上:**插电 + 开盖**(或接外接显示器)。可选:让机器在 21:15 前醒来 `sudo pmset repeat wakeorpoweron MTWRF 21:15:00`(看 `pmset -g sched`;撤销 `sudo pmset repeat cancel`,注意它会覆盖已有的 repeat 计划)。睡过了窗口 → 推「错过」,第二天用 `scripts/scan_run.sh --date <交易日> --skip-readiness` 补跑。
- **安装前先选送达渠道**:缺省 `delivery.channel = "none"`,所有推送(含 FAILED / 未开 / 错过)都发不出去 —— scan_run 会在日志开头与结尾各打一行 `⚠ 送达渠道 = none`,摘要 `warnings` 也记着,但没人看日志就等于静默。见下方「送达」。
- **安装**(只装 Claude 引擎;模板 `__REPO__` 占位同 prewarm):

  ```bash
  sed "s|__REPO__|$PWD|" scripts/com.tradingagents.scan-run.plist \
    > ~/Library/LaunchAgents/com.tradingagents.scan-run.plist \
    && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tradingagents.scan-run.plist
  launchctl list | grep scan-run                                   # 验证
  launchctl kickstart gui/$(id -u)/com.tradingagents.scan-run      # 手动触发(= scripts/scan_run.sh;只在 21:10–22:30 内有意义,窗口外它按「错过」处理 —— 白天补跑用 scripts/scan_run.sh --date <日> --skip-readiness)
  launchctl bootout gui/$(id -u)/com.tradingagents.scan-run        # 卸载
  ```

- **送达** = `scan_config.jsonc` 的 `delivery.channel`(默认 `none` = 什么都不发)。Bark:在仓库根 `.env`(已 gitignore)加一行 `BARK_TOKEN=<Bark App 里的 key>`,把 channel 改成 `"bark"`,试发 `scripts/notify.sh "测试"`;正文 = brief 原文(超 3000 字节截断)+ 报告路径。mail:`.env` 加 `DELIVERY_MAIL_TO=<地址>`。file:填 `delivery.file_dir`。每次送达落兼容报告目录的 `_delivery.json`(canonical `runs/<run_id>/p1` 是封存的目录哈希,绝不往里写)。送达失败不改 run 状态。
- **人工会话**开扫:SKILL 步骤 0 的 `run_lock check || { echo …; exit 3; }` 被占即停;不止是建议 —— `capsule begin scan-market` 与 `session_agent begin`(scan 请求)在锁被占时**代码里拒绝**(退出 3,打印持锁 pid),确需并跑才显式加 `--ignore-scan-lock`。反方向:scan_run 开跑前与 begin 前(就绪等待之后)各查一次人工场(ACTIVE 且持有者活着或心跳 90 分钟内)。
- **失败后**:按推送里的阶段查日志;修代码在另一个会话;补跑 `scripts/scan_run.sh --date <交易日> --skip-readiness`(新 run_id)。

## user_config 传参铁律

`frame --json` 回显的 `user_config` 必须随 Workflow `args.config` 传入,L4 逐股 `args.cfg` 原样透传。**传 `{}` = 静默关 l4_intel + 全体 agent 掉回内建缺省 effort**(配置真身是 `scan_config.jsonc`,**.jsonc 非 .json**;现 workflow 对空 config 直接 throw)。新 run 优先消费 `resolved_agents`;Claude 老 workflow 的内建表只服务离线/历史兜底,Codex project agent TOML 与 production profile 由测试锁定同值。

## 哨兵 vs 持仓

哨兵判据只问「今天有没有值得买的」,**不含「持仓要不要动」**。有 pinned 持仓时哨兵档跳 L3/L4 会让持仓拿不到当日决策卡 → 必传 `force_full: true` 覆盖(实测:哨兵开火日 4 只持仓身处崩盘日,靠 force_full 才拿到 Sell/UW)。

## L4 派发节奏(现行)

**一次性全派**:`l4_tasks batches` 返回单批全量 pending,`effective_cap`=`caps.l4_stock`(默认 64);tushare 并发由 `prepare_slim` 内 K 槽信号量控制,不靠派发节奏限流。回滚杆 `budgets.concurrency.l4_stock=4`。

**卡数**由 `scan_config.jsonc` 的 `l4.max_cards` 决定(非 📌 含 composite 席位;唯一算法 `scan/l4/card_count`,GATE1 回显,`write_finalists` 守卫⑩截尾);任务簿票数 = finalists.csv 行数,发布前 `l4_card_count_lint` 对账。离线验证:`python -m autoresearch.scan.l4.card_count replay <staging> --max-cards N --out <scratch>`。

## 活体情报站

铁律见 STAGES.md L4 节(价格断言须与 verified OHLCV 对账)。已知线头:限频自报仍会超 cap(warn 信号按 `l4_intel.max_queries` 对账)。

## 覆盖档案:重做首覆的正确姿势

📐 = 该报告期未对账、🕰️ = 档案 >90 日未全量刷新。解药是该票跑一次**成功的季度对账**(`dossier.reconcile <period>`,唯一写 `last_refresh` 的路径)。要重做首覆须先 `builder --force`(**不是** `dossier-init --force`,该 flag 不存在;对已建档票重派 `dossier-init` 是 no-op,清不掉 🕰️);注意 `builder --force` 清空 `initiated`/`last_refresh`,该票期间同时退出 🕰️ 与 `pending_init` 视野。未披露也落痕,所以 📐 计数应随对账动作**下降**;天天恒定 = 探针坏了。

## l4_tasks 子命令语义

- **`preflight` 是认领不是只读探针**:调用即可能把该票置 RUNNING 抢锁(误用作"验证"会抢走别人的票,靠 `failure --error-class TIMEOUT` 释放)。人工看状态用 `l4_tasks stats <date>`(纯读)或直接读 `_l4_tasks.json`。
- **prompt 硬门**:`init` 见任一 `_l4_prompt_*.md` 缺失/空 → `ok:false` 拒建任务簿(修复=重跑 `l4_card prompts` 再 init,幂等 ~3.5s);`preflight` 缺 prompt → `BLOCKED/PROMPT_MISSING` 不认领。**没有 `--allow-missing-prompts` 这种口子——修产物,别修门。**
- **intel 同日续传**:事故重派时 preflight 对「稿+status 完好且 ≤24h」的票返回 `intel_resume:true`,l4-stock 跳过重盲搜并在 status 落 `resumed:true` 披露。跨日/重放历史日不满足 → 照常盲搜;强制重搜 = 删该票 `_l4_intel_*` 两个文件。
- **单票恢复入口**:`python -m autoresearch.scan.l4_tasks batches <date>` 只返回未完成批次,按原 args 重派对应 `l4-stock`。**不要删 `_l4_tasks.json`**,否则丢失已验证成功的跳过事实。

## 计量与跨层校准(usage_harvest = 唯一正典)

- **token/成本真计量**(Codex 引擎走 `--engine codex --run-id "$RUN_ID"`,读 capsule 里的显式 transcript 绑定;量不到写 `UNMEASURED`,**绝不渲染成 `$0.0000`**):`python -m autoresearch.trace.usage_harvest --session <sessionId> --out $RPT/scan/<run>/token_usage.md --json-out $CTX/scan/<date>/_token_usage.json`。覆盖可定位的主会话+subagent;按 `message.id` 去重,分 input/output/cache read/write/model/effort/失败/重试,按公开计价倍率加权估算。补账 `--transcripts <glob>`。
  - **回填**:`python -m autoresearch.scan.post_run <date> observe --report-dir $RPT/scan/<run>`;缺成本/墙钟写未计量 warning,**绝不写 `$0`**。
  - **预算只告警**:超 cache 红线/阶段成本/墙钟只产 `DEGRADED` StageResult,`truncated=false`;不能截候选、查询、卡或阶段。
  - **成熟门**:至少 **10 次真实扫描**且基线已定价、成本/墙钟/cache 齐全,才报中位成本与 P50/P90 并判 PASS/FAIL;此前恒 `IMMATURE`。效率分母(USD/成熟 DecisionRecord、USD/最终 BUY、USD/已验证正确拒绝)分母 0 显示 `—`,不制造 BUY。
  - ⚠️ 旧「落盘字节÷2.8」估算曾低估 30 倍且分布相反——估算列已退役;信 usage_harvest。
- **配置生效对账**(`usage_reconcile`,scan 步骤 5 第四条命令):当日 `user_config_echo.json`(期望)× `_token_usage.json`(实测)逐 role 对账,`ok=false` 直接进 CP7 播报 stdout——**这就是当日结论**,不经 `self_review` 转手。三条精度边界(时序滞后 / `general-purpose` 壳只做集合断言 / effort 是请求参数非实测)写在模块 docstring 与报表头。exit 恒 0。
- **OTEL 遥测已删除**:勿再配那五件 env。
- **账本列名断层(读历史产物必知)**:落盘列名 `*_t2`/`ex2`/`fwd_2` 等沿自旧主尺 `fwd_2_oc` 年代,**值随 `common.ruler.MAIN_RULER`(现 `gap_c1_o2`)现算**。逐日文件可能在扫描日之后被重算刷新——**分界不是扫描日期,是该文件最近一次被(重)算的时刻**;读侧认每份文件自带的 `ruler` 列,缺列按 `res.get("ruler","fwd_2_oc")` 兜底。**历史产物不回改,展示层现算**。
- **注入分层铁律**:python 只产读数;prelude 打三条当日件建议行(📐/🔁/🚪),n<10 的 thin 行标「禁注」勿贴。**校准不改门/权重/评级。**

## 法证 run capsule(`autoresearch/trace/`,2026-08-28)

run 从 `capsule begin` 起就拥有独立工作区 `$CTX/scan_runs/<run_id>/`(`state.json` 带租约:hostname/pid/进程启动时刻/心跳);每条确定性命令经 `trace.exec_capture` 捕获 argv/stdout/stderr/信号,每个 agent 边界经 `capsule agent-event` 进 hash 链 `events/events.jsonl`,每次 lake 读取经 `trace.source_lineage` 落 `lineage/reads.jsonl` + 内容寻址 blob。CP7 的 `post_run observe` 末尾调 `capsule finalize`,固定次序:transcript 物化 → 真计量 → 产物快照 → expected/replay/completeness → `capsule.json` → MANIFEST → root → 脱钩 `ROOT.json` → `.tar.zst` 归档 → `_ledger/run_capsules.jsonl` 追加 revision → 全树只读 → 复验。

**三个结论互不替代**:`integrity_ok`(已归档文件被改没)/ `completeness_ok`(该有的证据齐不齐)/ `replayability`(冻结输入能否重放出同样字节)。**`MANIFEST` 通过 ≠ 完整**(`20260826_2000` 带着 0 transcript / 557 未归档 staging / `$0.0000` 假成本显示「完整性 ✓」)。失败与中断也冻结:`$RPT/scan/_failed/<run_id>/`,带 `failure.json`。事后找回的证据走叠加层 `_repairs/<run_id>/revision-N/`,base root 永不变动。Codex transcript 只认显式绑定(`capsule bind-transcript`),候选 0 个写 `GONE`、多个写 `AMBIGUOUS`,禁止按最新 mtime 猜。

**transcript 绑定**(2026-09-12 起接进生产):`post_run publish_run_observation` 在决策校验之后、`retain` 镜像之前、capsule 冻结之前,调 `autoresearch.scan.transcript_binder.safe_bind_run`,按 `agent_expectations` 配真实候选,写五态之一(`BOUND`/`UNVERIFIED_BY_PRODUCT`/`AMBIGUOUS`/`GONE`/`ERROR`)进 `_transcript_bindings.json`(开关 `retention.bind_transcripts`,默认 true)。逐条绑定失败都有账,不阻断业务发布;**一条 `BOUND` 不是研究完整的证明**,完整性结论仍以 capsule 的 `completeness_ok` 为准。CLI:`python -m autoresearch.scan.transcript_binder --run-id <contract_run_id>`。

**现场留存**(`scan/retention.py`,已降为兼容路径):发布收尾 + `post_run observe` 各跑一次 `retain()` → `trace/staging/` + `trace/inputs/{slim,sector_packs,prompts,temperature_row}` + `trace/transcripts/*.jsonl.gz` + `trace/lake_manifest.json` + `trace/MANIFEST.sha256`。判据:run 目录自足到 staging 可弃。核验 `python -m autoresearch.scan.retention verify <run_dir>`;链路复盘 `python -m autoresearch.scan.chain_view <run_id> <code>`。

**核验与复盘命令**(任何时候都能跑,全部只读):
```bash
uv run --no-sync python -m autoresearch.trace.capsule verify "$RUN_ID"              # 三个结论分开报
uv run --no-sync python -m autoresearch.trace.capsule inspect "$RUN_ID"             # 活跃 spool 概览
uv run --no-sync python -m autoresearch.trace.capsule replay "$RUN_ID"              # 只用冻结输入重放 L0-L2/L5
uv run --no-sync python -m autoresearch.scan.chain_view <run_id> <6位码>             # 这只票是怎么被推上来的
uv run --no-sync python -m autoresearch.trace.capsule repair "$RUN_ID" --reason "…" --source <暂存目录>   # 叠加层
```
⚠️ **回放/研究仪器一律写 scratch 或 `$RPT/research/`,禁写 run 目录与 staging**(实测 `20260725_1316` 的 `run_health.json` 被一次回放覆盖成 `cards=0`;08-13/08-18 的 `_relative_buy_decision.json` 被影子回放改写)。`verify` 就是用来发现这类事的。

## run 目录命名与时间锚

发布落 `$RPT/scan/<数据日YYYYMMDD>-<发布MMDD_HHMM>/`(2026-08-28 裁定;真身 `scan/run_naming.py`;旧 `<跑动日>_<HHMM>` 只读兼容)。`scan/exec_anchor.py`:manifest `execution` 块(`decision_approved_at`/`first_available_session`/`exec_lag`/`actionability_status`),账本同名四列。**读 BUY 战绩前先看 `actionability`**:报告在 T+1 收盘之后才就绪的 run,主尺买腿是已经过去的价格,不进 `ledger_line` 均值,单列「迟到 n 笔不计」;反事实收益走独立列 `exec_gap_c1_o2`,两列绝不混算。运营截止 14:45。老 run 由 `read_execution` 按 `generated_at` 估算并标 `ready_quality=estimated`。

## 结果账本(只记不学)

`scan/outcome.py`:prelude 的 `outcome_fill` 步逐日回填已发布 run 的推荐票事后读数 → `$RPT/scan/_ledger/outcome/<run_id>.json` + `_ledger/recommendations.csv`。口径与 `research.edge_census` 逐字同源。**必读两列**:`mode`(shadow 期的 BUY 明写「不执行」)与 `src`(`shared` = 读自共享 staging)。消费者只有 `chain_view` ⑩ 段与汇总屏一行;不进 brief、不喂任何 agent、不改任何参数。
