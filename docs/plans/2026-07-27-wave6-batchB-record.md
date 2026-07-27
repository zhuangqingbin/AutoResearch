# Wave6 批 B 实施记录(小刀 + intel 契约 + 修缮包 + 退役清理)

> 2026-07-27 实跑。设计依据 `docs/specs/2026-07-27-wave6-unified-roadmap-design.md` §3.1/§4/§5,
> 计划 `docs/plans/2026-07-27-wave6-batchB-plan.md`。分支 `wave6-batch-b`,起点 `2d99b74`。
> 全量 **1620 passed**(起点 1602;新增 23、删除 5 个 telemetry 测试)· ruff 净(仅一条**先于本波存在**的 B007,在 `tests/scan/test_universe_stdout_counts.py:73`,已用 stash 对照确认与本波无关)。

| commit | 内容 |
|---|---|
| `100286f` | intel 查询限频对账探针(probe 10) |
| `bfe647c` | 来源URL 硬契约 + 本票行情禁区 + 转引标题标注 + 独立初判升格 |
| `d4e7c7c` | 纯壳 agent 降 haiku(T1) |
| `9de9baa` | ensemble 同档早止(T2) |
| `94575a5` | token 估算列退役(T8) |
| `6d73b97` | usage_harvest 分模型汇总 + `--transcripts` 追溯模式 |
| `538f5a6` | OTEL telemetry 退役 + 文档改写 + 收尾三命令合并(E1/T3①) |
| `f21da5c` | slim 检查改用 GATE3 真判据(Q6-a) |
| `a8d12c2` | conviction 标度归一 + run_health 补刷(Q6-c) |
| `0f6c6f6` | market_pack 当日切面块(Q3) |

关账走 API 不进 git(`context/` 已 gitignore):open proposals **19 → 12**。

---

## 三处「指令与机检互相打架」——本波最值钱的发现

计划里我把 Q1 写成「情报员不守规矩,加探针罚它」。真读了 agent def 才发现**两条里有两条是我们自己的错**:

1. **零 URL 不是违规,是合规**。`l4-intel.md` 的铁律原文是「**来源必落**:每行带**站点名**」——从没要求过链接。07-24 那 11/11 稿全部老实写了「证券之星、新浪财经」,**完全照做**;天天 warn 它们的 `intel零URL` lint 才是那个没人给它配指令的孤儿。修法是补上教的一侧,不是加大罚。

2. **两条 price_claim_mismatch 的真身是转引媒体标题**。601869 那条来自卡里的「2026-07-15《业绩暴涨超7倍!601869,涨停!》(中国基金报)」——是**引用**不是自陈断言,机检按本票 OHLCV 对账当然不符。删 lint 是错解(它逮的是真问题:未标注的引用与自陈断言无法区分),正解是给引用一个标记 `〔转引标题〕`。

3. **`chk_blind_pass` 0/11 是契约缺位,不是检查坏**。「P1 写 3 行独立初判」这条指令在 agent def 和 playbook 里都有,但埋在散文「铁律」段;卡片模板从没要求把它作为**带标签的结构行**落下来。于是 agent 照做了、检查照样全 fail。放宽阈值是错解,升格为卡结构元素才是。

顺带在这里踩到一个连带坑:升格后**早停卡 ≤36 行的「保留」清单里没有它** —— 行数预算会把刚立的契约挤掉,又造一个静默失效。同批把它写进保留清单。

## 限频:声明行自报了三周,没有一个消费者

`l4-intel` 的声明行一直在写「网查 N 条」。07-24 真读:**18/18/17/15/20/26/23/16/17/21/25**(cap 15)——**10/11 超限**,最高 26 条 = cap 的 173%。`pr_20260714_007` 说的「限频形同虚设」不但是真的,而且**数据一直躺在产物里**,只是全仓零消费者(FN-1 家族的教科书样本)。

新探针接进 `product_shape_lint`(它已接线 assemble)后真数据跑出 **10/11**,与取证完全一致。未自报也上报(缺字段是弱证据,不得以缺推断合规)。

## 两处「已经学过的教训,副本没跟着改」

- **slim 阈值**:`process_score` 自持 `10*1024` 纯体积门槛,而 GATE3 侧早在 2026-07-14 就把体积从主判据降为地板了(`_slim_defect` docstring 写着药石科技「差 16 字节」被误杀、毙掉 60min/1.6M token 整条流水线,结论是「结构+内容决定能不能用,体积只兜真垃圾」)。留下的孤儿副本让 07-24 的 11 份正常 slim(8.7–10.1KB,表瘦身后新常态)被判 **11/11 假阳**。改成复用 `_slim_defect` 后 11/11 转 True、process_score 4→5;顺带逮住纯体积门槛**永远逮不到**的一类:结构齐、体积大、Close 是 NO_DATA 的降级稿。
- **报告 token 估算**:见下。

## token 真相与三把刀

`_stage_token_estimate` 的「字节÷2.8」对 07-24 那次跑动估 **~183.6k**,而对**同一次跑动**做 transcript 追溯真计量是 **加权 5.49M / billed 22.4M / 输出 716.6k** —— 低估 **30 倍**,且分布相反(L3 真占 7.8% 而非 37%)。整列退役,保留墙钟/调用数/落盘字节三列硬事实,token 指向 CP7 真表;**真表缺席时显式写「不等于用量小」**,不回落估算。

三把刀都做了成本以外的正确性论证:

- **T1 壳降 haiku**:7 个 2-消息壳(跑命令 / 转述门 JSON / heredoc 写文件)各背 ~60k opus 系统前缀 ≈287k 加权过路费。门的判据 100% 在确定性 CLI 里,schema 校验仍在。**但加权口径不含模型价差** → 同批给 `usage_harvest` 加了分模型汇总,否则这刀在表上完全看不出来(= 无法验收)。
- **T2 ensemble 同档早止**:`run1 == run2` 时三票排序中位**数学上已定**,第三票投什么都改不了 → 跳过 run3 结论逐字节相同。用参数化测试对全部五档第三票钉死这个前提,并用 node 直验两个真实 case:601869 [UW,UW] 早止(省一张满卡)、300857 [Sell,UW,UW] 照跑三票。
  - **最贵的写反方式**:把 `earlyStopped` 并进 `degraded`。后者语义是「复核 run 失败 → 禁折回」,早止必须照常折回,否则 SELL 复核会在最该救回误卖持仓时不折。锚钉在判据本行,变异实测会红。
- **T7 prewarm 未做**:诊断结论是**没有 bug**。`launchctl print` 显示 plist 已装载但 `runs = 0`、`last exit code = (never exited)`、日志文件不存在;plist 安装于 07-25(周六)12:55,排程是周一~周五 19:30 → 首个合法档期是 07-27(周一)19:30。07-25 那 959s 是 run 窗口内的手工 kickstart,不是排程失败。**没有改任何东西**,转为运维验证项。

## 与计划的偏差(四处)

1. **Task 1 接线点错了**:计划写「接进 `review()`」。实际 `review(ctx: dict)` 吃的是 ctx 字典,所有读盘探针都在 `product_shape_lint(scan_dir, date)` 里,而它**已经**接线 assemble —— 新探针进那个函数即自动上 `gate_fires.csv`,**不需要任何新接线**。
2. **Task 5 的 Python 侧改动取消**:计划要改 `_ensemble_flag` 让早止可区分。读源码后发现既有语义**天然正确**——早止时两票同档 ⇒ spread=0 ⇒ 不触发人裁(确实没有分歧可报),而 dissent 行打印的是 `len(ratings)` = 真实跑数,不会把 2 跑说成 3 跑。改成只留回归钉,不动判据。
3. **Task 11c 撤回(删 203 个 2 字节空 news 文件)**:`l4_reuse.py` 读这些文件,**「空列表 = 已查、无新公告」与「文件缺 = 未知」语义不同**。删了是拿信息换 406 字节,与本仓「文件不存在是弱证据」的纪律相反。不做。
4. **Task 11b 的根因不是「顺序写反」**:`write_run_health`(:1257)确实早于 `build_summary`(:1270),而 `gate_fires.csv` 是 build_summary **内部**才落的。也**不能**简单调换顺序——`product_shape_lint` 的 force_full 探针要读 run_health 且 presence-gated,挪后会让那个探针静默失效(又一个 FN-1)。修法是保留前一次 + 之后幂等补刷一次。

## 自己写的假绿灯(当场逮到一个)

Task 12 的投影锚第一版写成「全文 grep `pct_1d`」——而 `universe.py` 的 selftest 合成帧本来就有这串,**投影没改也照样通过**。改钉在投影那一行(要求与 `pct_60d`/`pct_ytd` 同行)后才真红。这正是本仓 `test_scan_market_workflow_pinned_roster_log` 注释里记过的同款病。

三个新守卫做了变异实测,全部会红:①删 `today_slice` 的 staging 挂载 → 两入口 parity 测试红;②去掉 gate 壳的 haiku → 壳降档测试红;③把早止并进 degraded → 早止锚测试红。

## 07-28 活体验收清单(交批 A)

下次真实扫描逐条 ✓/✗ 记录,✗ 即开修单:

| # | 期望 | 看哪里 |
|---|---|---|
| 1 | `intel零URL` 行 = 0 | `gate_fires.csv` |
| 2 | `intel限频` 行出现且条数合理(若仍 10/11 说明指令没被遵守,需再压) | `gate_fires.csv` |
| 3 | `price_claim_mismatch` = 0(转引已标注) | `gate_fires.csv` |
| 4 | `chk_slim_size` 11/11 True · `chk_blind_pass` 转 True | `process_scores.csv` |
| 5 | CP7 分模型列里 general-purpose 全落 haiku 行,且 GATE 行为不变 | `token_usage.md` |
| 6 | 若触发 ensemble:同档时见「同档早止」日志,`n_runs=2` | workflow 日志 + `_ensemble_*.json` |
| 7 | pack 出现 `today_slice`(旧产物无 pct_1d 故为 None,新 prelude 才有) | `market_pack.json` |
| 8 | `run_health.missing` 不再含 `gate_fires.csv` | `run_health.json` |
| 9 | 报告阶段表无 `~token` 列、有 `token_usage.md` 指路 | `summary.md` |
| 10 | prewarm 今晚 19:30 首次自动跑(**T7 的真验收**) | `/tmp/scan-prewarm.log` + `_prewarm.json` |

**注意**:第 1–4、6 项依赖 `.claude/agents/*.md` 与 playbook 的改动,**下 session 才装载生效** —— 验收扫描必须在新 session 跑。

## 未做(挂读数触发,见 spec §3.3)

L3 降档/拆分 · intel 降默认/限频压实(等 A/B 账本 ~08-08)· ≥OW 双复核降档(等买单 n≥10)·
SKILL.md 大瘦身(等 CP7 证实主会话占比仍 >25%)· sector-brief 降档(等复用命中率读数)·
`scan/progress.py` 退役(等 R3 直播首验收通过)· 三大指数当日涨跌(需新端点 `index_daily`,
`pr_20260721_002` 保持 open —— 它四个子项本波只做了两个)。
