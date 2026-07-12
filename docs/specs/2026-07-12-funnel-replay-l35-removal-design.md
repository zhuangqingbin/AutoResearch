# 漏斗历史回放器(funnel replay)+ L3.5 完全移除 · 设计稿

> 日期:2026-07-12。来源:用户两条裁定——①"写设计稿吧"(承接同日研究方向评估,五方向中回放器为最大杠杆件);②"L3.5 要完全移除,直接 L3 输出"。
> 状态:Part A(L3.5 移除)本波内实施;Part B(回放器)设计定稿待拍板点批复后开工。
> 前情:`docs/specs/2026-07-11-funnel-six-questions-brainstorm.md`(三病灶+拍板记录)、`docs/specs/2026-07-12-l3-merge-plan.md`(L3.5 收窄职能并入 L3 finalist tier)。

---

## 0. 背景:为什么是这两件

**账本现状**(07-12 读数):避坑已证(真实 −0.30% vs 影子 −4.65% vs 市场 −5.83%,门 +4.35pp+选股 +1.2pp)、抓肉未证(OW 已实现 n=2 全亏、attribution 14 日 caught=0、missed_l1 5004)。所有买侧待裁项(reversal_confirm A/B、雷分级、l4_intel、新配额、题材类新路)都在**等前向账本攒 10-20 日**,而一个月只产 ~20 个样本日,且 06-18 以来前向窗全是弱市——温度计五相位里"修复/发酵/高潮"从未在前向窗出现,方案 B(相位切菜单)的核心假设处于零证据状态。**回放器把裁决样本从"每月 20 日"换成"一次 250-500 日",且覆盖前向窗看不到的相位。**

L3.5 侧:07-12 L3 两遍法已让 L3 直接产 finalist tier(7-10 只,merge v3 cap=min(`finalist_max`, `l4_budget`)),收窄职能与 L3.5 闸完全重复;闸自身 13 日回测已裁"conviction 闸更差,保 passthrough"=生产路径上恒 no-op。用户裁定:**完全移除**(推翻同日早前 STAGES"回测 harness 保留勿删"的注记),将来若要复验"收窄闸"类假设,用 Part B 回放器跑,不复活 L3.5。

---

## 1. Part A · L3.5 完全移除(直接 L3 输出)

### 1.1 语义

L3(两遍法 pass2,`l3-rank`)产出的 finalist tier **就是** L4 的入选集:`l3_select finalists` 注入 pinned → GATE2 校验(6 位码+预算记账)→ L4 派发。中间不再存在任何可插拔收窄层。

### 1.2 移除清单(触点图,已 grep 全库核实)

| # | 文件 | 动作 |
|---|---|---|
| 1 | `autoresearch/scan/l35_gate.py` | **整文件删**(registry:passthrough/topk_simple/conviction_floor_quota) |
| 2 | `autoresearch/research/gate_backtest.py` | **整文件删**(L3.5 闸回测 CLI;13 日结论已内化,见 1.3) |
| 3 | `autoresearch/scan/gates.py` | 删 `_l35_regime`/`_l35_gate_choice`/`_l35_exempt`/`apply_l35_gate` + gate2 内调用;gate2 返回键 `l4_gate`/`l35_cut_n` 移除;`user_config_path` 形参移除;**保留** exempt 预算记账(1.3) |
| 4 | `autoresearch/scan/assemble.py` | 删 `_l35_gate_shadow_line` + build_summary 调用点(presence-gated 节) |
| 5 | `autoresearch/learning/retro.py` | 删 `l35_gate_shadow` + `write_retro_input` 调用点(「L3.5 闸影子」节) |
| 6 | `autoresearch/scan/config.py` | 删 `ScanConfig.l4_gate` 字段 |
| 7 | `autoresearch/scan/user_config.py` | `_TOP_WHITELIST` 与 `apply_to_scan_config` 循环去掉 `"l4_gate"`(白名单拒收=写了就 raise,fail-fast 防僵尸配置) |
| 8 | `.claude/workflows/scan-market.js` | GATE2 schema 去 `l4_gate`/`l35_cut_n` 两键;132 行日志改 `GATE2 ✓ finalists=${g2.n}` |
| 9 | `.claude/skills/scan-market/scan_config.jsonc` | 删 `"l4_gate"` 块(28-31 行) |
| 10 | `tests/scan/test_l35_gate.py`、`tests/research/test_gate_backtest.py` | **整文件删** |
| 11 | `tests/scan/test_gates.py` | 删 L3.5 闸接线测试块(~103-215);exempt 记账测试保留 |
| 12 | `tests/learning/test_retro.py`、`tests/scan/test_assemble.py`、`tests/scan/test_user_config.py` | 删各自 l35/l4_gate 测试与 fixture 键 |
| 13 | `.claude/skills/scan-market/STAGES.md` | 194 行"残留现状三行"块改写为"已完全移除" |

### 1.3 保留物(删的时候别多手)

- **GATE2 exempt 预算记账**(`_L35_EXEMPT_LANES`→改名 `_EXEMPT_LANES`):pinned/carryover/watchlist_trigger 不占 finalist 名额——这是终审 C-1 修复(满员日+pinned 确定性触雷),契约属于 GATE2 而非 L3.5 闸,**必须保留**,连同其"追加顺序防呆"注释。
- **历史证据**:`reports/research/gate_backtest_2026-07-11.md`(13 日回测:唯 conviction≥70 有 T+2 edge/中间 band 是噪声)不删——结论已内化为 L3 conviction 行为化定义(≥70=能说出 D+1 谁买且愿真金买入)与 finalist tier 质量门。
- 历史 staging 里的 `context/scan/*/_l35_cut.csv` 数据文件:不清理,retro/assemble 删节后自然无人再读。

### 1.4 验收

① 全测试绿;② `python -m autoresearch.scan.gates gate2 <近期真日期>` CLI 冒烟(JSON 无 l4_gate/l35_cut_n 键、ok 正常);③ `grep -rn "l35\|l4_gate" autoresearch tests .claude`(排除本设计稿与历史 docs/reports)零残留;④ scan_config.jsonc 含 `l4_gate` 键时 loader 响亮 raise(白名单拒收自证)。回滚=单 commit revert。

---

## 2. Part B · 漏斗历史回放器(`autoresearch/research/replay.py`)

### 2.1 目标 / 非目标

**目标**:对历史交易日重放确定性漏斗(L0 门→L1 九路召回→L2 菜单)+ 温度相位标注,按 fwd_2_oc/hi_2_oc 主尺记账,一次性产出召回/菜单/相位层的大样本证据。**是研究仪器**:离线、零 LLM、不碰线上任何行为,不新增生产机制(与冻结窗兼容)。

**非目标**:不回放 L3/L4(LLM 层不可回放——回放结论止步于"谁进了 finalist 候选池",不主张端到端收益);不是交易回测(不产 NAV 曲线做策略宣传,只做条件分布/捕获率/IC 口径的裁决证据);不做参数寻优刷格子(每个变体须先有假设,DSR-lite 纪律照用)。

### 2.2 架构:复用生产真身,不写第二套漏斗

07-11 教训(FN-1 三连):**生产真身 = workflow→prelude→`universe.run` 直调**。回放器唯一正确姿势是逐日调同一个 `universe.run`,不重新实现召回:

```
replay.run(start, end, root="context/replay", variant=None)
  for d in trade_days(start, end):                    # 湖 parquet 日历
      universe.run(d, outdir=root/d, source="tushare",
                   regime_aware=True, shadow=False,
                   recall_channels=..., channel_quotas=...)   # variant 显式传参恒优先,
                                                              # 绕开 _funnel_overlay 的 scan_config 兜底
      temperature.show(d)                              # rollup 先 backfill,相位 join 进 meta
      retro.attribute(d, scan_root=root)               # 生产同款归因:realized_returns→attribute_frame
                                                       # (buylist 空、missed_l0/l1/recalled_cut 桶照算)
aggregate(root) → reports/research/replay/<run_id>/   # R1-R4 四产出
```

关键复用点(全部已存在、已参数化):`universe.run(analysis_date, outdir=...)`(universe.py:324)、`retro.attribute(date, scan_root=...)`(retro.py:528,attr 帧自带 fwd_2_oc/hi_2_oc 与 missed 桶)、`temperature.rollup/backfill`(temperature.py,limit_list_d 历史全可拉)、`temperature_calib.forward_returns`(市场基线)。**新代码只有编排循环+聚合报告,预计 ≤400 行。**

### 2.3 PIT(point-in-time)纪律(六条,M1 对拍验收)

> **第 1 条是本设计稿初稿漏掉的**,M1 实施时才发现——它是回放器最容易犯的前视偏差。

1. **权重 PIT(最隐蔽)**:`context/factor_lab/weights.json` 是 retro 用**含未来前向收益**校准出来的——拿它回放历史 = 用未来的权重预测过去。故回放默认 `weights='prior'`(内置 `_PRIOR_WEIGHTS`,零校准=零泄漏);`'current'` **有泄漏**,只允许用于 M1 对拍/对照,且 `weights_leak` 标记随每份报告落盘。落点:给 `universe.run` 加 `weights_path` 参数(透传 `pick_weights(path=...)`,`None`=现行为=逐字节 parity)。
2. 行情/资金类端点(daily/daily_basic/moneyflow/margin/lhb/limit_list_d)按 `trade_date` 取数=天然 PIT;`fetch_universe_tushare` 只用这些 EOD 端点(已核实)。
3. **live-only 端点禁用**:spot 快照/涨停池(zt_pool)等 `settle=live` 端点不得进回放路径——`_assert_pit_source` 硬断言 `source=='tushare'`(akshare/em 路径含 live spot,回放历史日会读到**今日**盘口)。
4. 财务:`scoring.latest_reported_quarter(analysis_date)` 按当日已披露报告期取。
5. **幸存者偏差**:`stock_basic(list_status='L')` 只含**当前存续**股 → 回放期内已退市的票可能掉队。`survivorship_probe` 逐日报 L0 缺口,如实标注而非"修复"(缺口放大的窗口,捕获率读数须打折看)。
6. **可执行性**:D+1 一字板/停牌不可买 —— `attribution.csv` 的 `buyable` 列已承担此职,`winner_autopsy`/`channel_audit` 均按可执行口径过滤。

### 2.4 M1 实跑结论(2026-07-12,已执行)

**回放器可信,门通过。** 用 as-of 配置 + as-of 权重重放 2026-07-09 与生产 staging 对拍:

- **一致**:L0 层逐值一致(universe_raw 5559 / universe 4149 / after_gate_a 4149 全等);L1 打分帧 code 集合 **jaccard 1.0**(4149/4149);原始数据列(pe/pb/np_yoy/rev_yoy/roe/close/pct_60d/main_net_ratio/turnover/mktcap)**零漂移**;momentum/value/growth/fund_main/tech/volprice **六组因子逐值一致**。
- **残余差异全部可归因,无一是回放器缺陷**:①**代码漂移**——`score_rz` 列生产侧不存在(rz 组是 07-10 之后加进 scoring 的)、`score_chip` 随组定义调整而变、`healthy` 通道回放召回 150 只而当日 0 只(谓词在 07-11 整编波改过);②**数据漂移**——`score_north` 274 只 NaN 翻转(tushare 事后补了当日全空的 hk_ratio)。这正是回放器该有的语义:**"今天这套代码在历史数据上会怎么跑",不是逐字节复现历史**。
- **交叉验证(独立于对拍)**:5 日回放的 R2 读数里 `value` unique 超额 **+1.30% 全路第一**,与前向 13 日 channel_audit 账本(`value +1.04%` 第一)方向一致;R3 的 `missed_l1` 占比 51–58%,复现"召回线是瓶颈"的既有结论。两条独立证据链指向同一结论 = 口径正确。

**M1 逮到的两个真 bug(这就是这道门存在的理由)**:

1. 🚨 **lake 窄表毒化(生产级,已修)**:`temperature.rollup` 用 `fields='ts_code,pct_chg'` 回填 07-09/07-10 的 daily(那两天在扫描**当天**尚未结算、按"date>=today 拉新但不写"的规则没入湖),成了这两天的**首个写入者** → 把两列窄表钉成该日湖快照。而 `_cache_key` **不含 fields**,一个 key 只有一个 parquet。后果:`_harvest_vol_series` 拿不到 high/low/amount → try/except **静默**吞成空帧 → volprice 组整组 NaN → 全市场 composite 失真 **98.8%**、L2 名单 jaccard 掉到 **0.36**。而这两天正落在下次扫描的近 20 日窗口里——**生产扫描本会静默中招**,唯一信号是一行淹没在日志里的 warn。修:`cache._lake_params` 入湖一律剥掉 `fields`(湖存全量快照,窄列由调用方自己选);毒表已删待重拉;回归测试锁死。
2. **as-of 反推的两个盲点**(回放器自身,已修):①从产物(`L1_channels.csv`)反推"当日跑了哪几路"会**漏掉零召回的通道**(07-09 的 healthy/reversal_confirm 就是)——**产物能证明"跑过什么",不能证明"没跑过什么"**;改用注册表全集。②`pinned.jsonc`(当下的持仓保送清单)会强注 L1 → `recall_n` 1000→1001,而历史生产日没有它 → M1 须禁用保送。

**新增守卫**:`frame_integrity`(因子帧完整性门)——整组失效的因子列当场炸出来并标 `degraded`,不让被污染的日子悄悄进 250-500 日的统计。这类"静默降级"是回放器最危险的失效模式(漏斗照常跑完,只是打分已经失真)。

### 2.4 产出四件(R1-R4,落 `reports/research/replay/`)

| # | 产出 | 回答的问题 | 机制 |
|---|---|---|---|
| R1 | 相位×fwd 条件分布 | 温度计 v1 阈值/权重校准;修复/发酵段是否真有超跌/接力品相的肉 | `temperature_calib` 扩样版(107→500 日),phase×regime 交叉表 |
| R2 | 通道×相位 unique 超额/捕获率 | 哪条路在哪个相位有肉;quota 该怎么随相位摆 | 逐日 L1_channels.csv 聚合,channel_audit 同口径(unique 超额 T+2/命中率),按 phase 分桶 |
| R3 | 赢家验尸(大样本) | "换什么原料"——历史 top-decile fwd_2_oc 赢家死在哪层、什么画像 | attr 帧 missed_l0/missed_l1/recalled_cut 桶聚合+赢家日频特征画像(涨停结构/突破/资金/位阶) |
| R4 | 候选新路同框 | 起爆/题材梯队/rz/block 若在场能多捕多少 | 先因子级(factor_lab IC,fwd_2_oc 主尺,按相位分桶);IC 过硬门者注册为影子通道跑通道级变体 |

### 2.5 试验清单(E1-E5,判决条款先写死再跑)

- **E1 温度计校准**:R1 出数后修 `temperature.py` 的 score 权重/相位阈值(先验→拟合),给"下一波菜单/预算联动"提供接线参数。判决:相位间 fwd_2 条件均值差 ≥ 全样本σ/2 且 n≥30/相位,否则相位机器只保留描述性地位。
- **E2 通道整编预裁**:R2 与前向 channel_ledger 打架时,**历史定方向、前向定生死**——回放给候选方向与幅度,quota 实改仍走"前向 ≥10 日+人批"既有节奏,不因回放跳过。
- **E3 题材梯队候选路**:概念成分(tushare `concept_detail`/`ths_member`,权限先探)×当日涨停结构→"强题材内未涨停、低位阶、放量"截面因子。**与否决清单划界**:打板/隔日溢价=追涨停本身,已否决不碰;本路是题材扩散截面因子。IC 不过 factor_lab 三门即弃,不特批。
- **E4 卖侧兑现规则**:回放赢家+shadow_buys(~45 笔)上测条件退出(D+1 高开>x% 兑现 vs 持到 D+2;MFE 分位;退潮相位提前兑现),产出给 L4 持仓管理卡的校准锚(目标+8% vs MFE+4% 的病最直接的药)。
- **E5 pool-vs-pick 判决**(前向为主、回放辅助):若 finalist 篮子(等权/sized)纸面 NAV 30-60 日持续跑赢市场而单票 OW 继续拉胯(现 n=2 全亏),主线切"相位定仓位×篮子×三门否决",OW 降级为篮内超配。回放贡献相位条件化的仓位先验,不替代前向判决。

### 2.6 运行与成本

- 首拉:~8 端点×500 日,lake 缓存后一次性;tushare 高权限但有限频→按日循环自带退避,**断点续跑**(outdir 已存在且 meta.json 完整=跳过,幂等)。预计小时级批任务,分段跑(先 M2 近窗再 M4 扩窗)。
- 磁盘:staging 每日 ~2-5MB×500 ≈ 1-2.5GB,`context/replay/` 已 gitignore 范围内。

### 2.7 里程碑

- ✅ **M1 单日对拍**(可信度门):**已通过**,结论见 §2.4(逮到 lake 毒化生产 bug + 两个 as-of 盲点,全部已修)。
- ✅ **M1.5 端到端冒烟**:07-02~07-08 五交易日回放 → 归因 → 温度 → R1/R2/R3 报告全链路跑通,零 `degraded`;首批读数与前向账本交叉验证一致(见 §2.4)。命令:`replay run 2026-07-02 2026-07-08` + `replay report 2026-07-02 2026-07-09`。
- **M2 近窗回放**(下一步,待批):2026-01→07(~120 交易日),出 R1-R3 首版 + E1 判决。预计 2–4 小时(每日 ~1–2 min,首拉入湖后重跑走缓存)。
- **M3 扩窗**:2024-01→2025-12(含 924 行情=修复/发酵/高潮真样本),R1-R4 全量,E2/E3 判决。
- **M4 常态化**:replay 变体接影子漏斗惯例,新候选路先回放后前向。

**运行手册**:

```bash
uv run --no-sync python -m autoresearch.research.replay m1 2026-07-09          # 可信度门(改了漏斗代码后重跑)
uv run --no-sync python -m autoresearch.research.replay run 2026-01-01 2026-06-30   # 断点续跑,单日失败不中断
uv run --no-sync python -m autoresearch.research.replay attr                    # 补跑归因(窗口末尾等 fwd 成熟)
uv run --no-sync python -m autoresearch.research.replay report 2026-01-01 2026-06-30
```

### 2.8 风险与诚实局限

回放止步 L2+相位,L3/L4 判断层的贡献仍只有前向账本能证;财务重述/复权口径漂移靠 M1 对拍兜(对不齐就降级该因子);相位/regime 标签用当日可得数据现算(不许读 T+1 后视);多重检验用 DSR-lite 固定文案+试验预注册(E1-E5 判决条款即预注册);回放期涵盖极端行情(924)——通道结论要报分相位读数,不报全期平均掩盖结构。

---

## 3. 推进节奏

1. **Part A 本波做完**(纯删除+行为零变化:passthrough 恒 no-op,删除不改任何生产输出)。
2. Part B 等本稿拍板点批复后开 plan(`docs/plans/`),放在"冻结窗"内合法——离线研究件,不新增线上机制;三波联合验收(下次真扫描)优先级不受影响。

## 4. 待拍板点

1. 回放窗口:M3 起点 2024-01(推荐,含 924 前后完整情绪周期)还是 2025-01(省一半配额/时长)?
2. E3 概念成分数据源:tushare concept(免费档旧)vs ths_member(积分档)——权限探测后定,探测本身零风险。
3. E2 打架判决规则("历史定方向/前向定生死")是否确认为默认纪律?
