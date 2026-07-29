# Wave8:首航修缮波 —— 编排瘦身 × 存在≠有效根治 × L3 旗消费 × 学习环尺子(design)

> 2026-07-29 定稿(07-28 夜 brainstorm,**只落文档,不实施**)。调度权威仍是
> `docs/specs/2026-07-28-scan-market-unified-optimization-master-design.md`(07-28 总纲)——
> 本文 = 四主题的实施蓝本,配套逐 task 拆解见
> `docs/plans/2026-07-29-wave8-implementation-plan.md`。
>
> 证据基线:run `20260728_2319`(数据日 2026-07-28)——**Wave1-5 统一优化(38 commits,当日
> 17:32–20:54 落地)之后的首个真实全量 run(21:32 起跑)**。所有数字实测;估算处显式标注。

## 用户已拍板(2026-07-28/29 brainstorm)

1. **只落文档**:本波不写代码;文中批次是下次开工的实施蓝本。
2. **四主题全收**(A 编排瘦身 / B 存在≠有效根治 / C L3 旗消费失效 / D 学习环尺子+到期裁决)。
3. **fb_20260714_003 维持**:「每股一个 l4-stock workflow」不动;l4-fleet 单 workflow 方案
   记附录 A「延迟决策」(含完整取舍与解锁条件),不作废该裁定。
4. **双文档形态**:design(本文)+ implementation plan(docs/plans/,subagent 可直接领任务粒度)。

## 0. 定位与红线

### 0.1 一句话诊断

首航证明 Wave1-5 的机器基本成立(B′-a/B′-d/B′-f/批N/批P1-P2/SELL 双复核全部活体验证 ✓),
同时打响了三类新警报:**①主会话成本 48.7% 双倍击穿 25% 挂账线**(编排循环留在主会话的结构性
代价);**②「存在≠有效」家族第三代变体**(非空垃圾 pack 骗过 `test -s` 门 + argparse 断裂 CLI
零测试覆盖 + suppress 静默吞写盘失败);**③L3 对已注入旗的消费不一致**(601918 反号旗在表仍被
引用为多头论点,6 OW-lean 被 L4 全数压回)。另有弃权账本尺子与 paper_nav 同屏互扇、三个预挂账
裁决点到期。

### 0.2 红线(沿 07-28 总纲 + Wave6/7 全部 + 本波新增)

Wave6/7 红线原文不重抄,全部继续有效:不放松买入门 / 不动早停机制 / 不建当日大涨召回 /
超短 T+2 主尺 / L2 不用模型 / 52 周高不复活 / 不建 wire 快讯层 / 改生产行为 = 读数触发 +
用户点头 / lint 假阳修法排序:补指令 > 标记 > 加严检查。

本波新增:

| 红线 | 出处 |
|---|---|
| **每股一个 l4-stock workflow 不动**(滑窗只改派发节奏,不改 workflow 粒度) | fb_20260714_003,本波用户再确认 |
| **L3 prompt 改动必须同时走 prompt_patch 人批 + experiment_registry 登记**(§4.3) | 治理清单含 L3;prompt 无法影子跑的折中见 §4.3 |
| **弃权 v2 换尺过渡期双打印 ≥10 个成熟日**,旧 headline 才可退役 | 换尺不换史,防口径断层 |
| **D3 硬顶只拒稿不拒票**(intel 超硬顶 → 稿件降级,卡回退卡内网查,评级流程不受影响) | 情报是輔助面,不得反噬决策主链 |

## 1. 现状账本(run 20260728_2319 读数)

### 1.1 漏斗与战绩

- L0 4,050 → L1 1,000 → L2 203 → pass1 40 → L3 judged 25(finalist 8 + bench 17)+ 保送 3
  → 11 卡 → **0 买**(Hold 6 / UW 4 / Sell 1)。连续 0 买第 7 日,`risk_off`·温度 48(退潮)。
- **L3 给 6 个 OW-lean,L4 确认 0 个**(4 降一档、2 降两档);盘后核实 601918 的「反号」旗
  在 `_l3_table.md:115` 已渲染仍被 thesis 引用为多头论点(§4 证据)。
- OW 三门失守(7 卡可解析):主力真在 ✗4 · 业绩真兑现 ✗3 · 估值不透支 ✗2;停因分桶:早停 7
  (资金流出 3/基本面恶化 2/题材透支 1/其他 1)+ 满卡未达 OW 4。
- 三保送持仓(崩盘日 −12.26%/−16.62%/−10% 跌停):协创 [Sell,UW,Sell]→**Sell**、普冉
  [UW,Hold,Hold]→**Hold**(向上折回)、长飞 [UW,UW] 同档早停→**UW**;昨卡三条止损位当日全部
  触发 = 判断层跨日一致性首个活体正例(pinned_ledger 首笔好素材)。
- 影子组合(起 20260618):真实 −0.24%(9 笔)vs 无门影子 −3.45%(72 笔)vs sized −6.57% vs
  市场等权 −11.66% → 门价值 ≈+3.2pp。
- **弃权账本同屏矛盾**:headline「CORRECT 0 · FALSE 5」——FALSE 判据"全市场任一票 +2pp"
  在 07-15 数出 1,503 只合格票(还标 DEGRADED),量的是召回上限不是弃权决策(§5.1)。

### 1.2 token/成本真计量(CP7 第二读,主会话首次入表)

1 主会话 + 100 subagent:原始 54.06M → 加权 **11.61M** · 输出 978.9k · cache 命中 91.7% ·
估算 **$62.68**(失败 0/重试 0/废弃 0)。

| 桶 | $ | 占比 | 备注 |
|---|---:|---:|---|
| **主会话(opus·max)** | **30.50** | **48.7%** | 143 消息 · 27.75M cache读 · 1.38M 1h写——**>25% 挂账线双倍击穿,SKILL 精简二期正式触发** |
| l4-card ×11 | 16.00 | 25.5% | 含 3 保送满卡+复核 |
| l4-intel ×11 | 5.54 | 8.8% | |
| l3-rank | 3.67 | 5.9% | |
| gp 壳(haiku) | 2.88 | 4.6% | $ 口径已证 Wave6 T1 降 haiku 正确 |
| sector/macro-brief | 4.10 | 6.5% | |

主会话结构拆解:14 个 workflow 完成通知 + 2 个 Monitor 约 20 次事件 + 3 批×4 次人工派发
+ CP 播报文件读——**每次唤醒都以全上下文计 cache 读**。

### 1.3 耗时(冷启动日,TTL 复用 0)

总 ~107m:L0L1L2 6m36s(预热湖命中)/ 策略师 2m10s / 行业 brief 1m29s / L3 精排 13m53s /
**L4slim 37m05s + L4 研究 57m40s + ensemble 16m19s**(三批串行,批间屏障各等最慢者;批 3
保送满卡 25-40min 独自拖尾)/ assemble 2m51s。对照 07-27(暖日,复用多):89m38s。

### 1.4 事故与探针失效(本波修缮证据源)

1. **非空垃圾 pack 骗过门**(pr_20260728_001 已立案):haiku bash 壳把 `frame --json > pack`
   擅自改写加 `2>&1`,tushare 断流(4/24 端点)后 stderr 日志+tqdm 进度条落入 pack(1,776B 无
   JSON);`scan-market.js:71` pack-check 判据 `test -s`(为 07-27 **0 字节**事故所加)对
   "非空但垃圾"零鉴别力 → `{'ok':true}` 放行,重试分支未触发。macro-brief 拒写自救(准确诊断
   "只重定向 stdout,不要 2>&1"),人工补跑 frame 后手工派发恢复。**同族第三代**:空 pickle →
   0 字节 pack → 非空垃圾 pack。
2. **assemble.py CLI 断裂**:Wave4 重构 `48b2e0d` 把实现抽走后适配器漏 `import argparse`,
   `python -m autoresearch.scan.assemble` 直接 NameError——**1952 绿逮不到**(CLI 入口零测试
   覆盖,「绿灯不等于有灯」的 CLI 层版本)。当晚一行修复(`import argparse`)已落。
3. **prelude 汇总屏静默吞**:`prelude.py:388` 区 `contextlib.suppress(Exception)` 吞掉
   write_summary 失败(根因被吞无从考证),而 `scan-market.js:90` 的
   `prelude ... && echo "SUMMARY_FILE=..."` 在 prelude 退出码 0 时无条件回显 → agent 回报
   "Summary file generated" 但文件不存在,CP1 全量转播落空。日志替不存在的文件背书 = 说谎的日志。
4. **CP5 读到写盘中途草稿**:卡片级 Monitor 用 grep 轮询 `details/*.md`,601319 先读到草稿
   评级 UW、终稿 Hold——文件级轮询无法区分"写完/写一半"。task_book 的 card hash 才是完成信号。
5. **`l4_tasks batches` RUNNING 不可见**:`l4_tasks.py:450` 只把 PENDING/FAILED 计入批次,
   601319 状态 RUNNING 时既不在 batches 也无提示 → 主会话误判"批 2 已完"提前派批 3(撞并发上限
   风险;当晚 4 并发内无实害)。
6. **intel 限频第 3 个超限 run**:11/11 稿自报 16-29 条 vs cap 15(中位 ~18)。超限 run 累计
   三例:07-14(首跑)、07-27(2 稿)、07-28(11 稿全体)——Wave7 §4.3 原文写「连续 3 跑」,
   严格连续不成立(07-21 空 config 事故致零情报稿、07-24 未见超限自报),**本波从宽读作
   「累计 3 跑」并以今晚 11/11 全面超限为主依据**,触发升格判定成立(§5.3)。
7. **指数断言不可对账**:price_claims 报「创业板指 −7.35%」不符 warn——湖里没有指数日线,
   审计器拿个股 OHLCV 硬对指数断言,真伪无法裁决(报告只能挂 hedge)。
8. **ensemble_ledger n=5 已到**(Wave7 日历「SELL 复核 3 跑→1 跑」裁决点),但折回对错需
   T+2 成熟(今晚 3 折 07-30 收盘才可判);买单 n=9(下一买单触发 OW 复核降档裁决)。

### 1.5 首航已验证项(本波**不再**立案,防重做)

B′-a 计量正门(主会话入表 ✓)/ B′-d earlystop_ledger(11 行与 gate_hist 同数 ✓)/
B′-f reuse 持仓豁免(崩盘日 0 复用、三持仓全重研 ✓)/ 批 N intel 三窗(11 稿全带时效窗列 ✓)/
批 P1-P2(pinned_ledger 37 行 + 结构化盯梢线 ✓)/ SELL 双复核(两向折回均正常 ✓)/
P5 nightly_close(launchd 已装 exit 0;⚠️ 07-24 归因当晚仍人工补——首周观察期核对其成熟判定,
观察期内不立案)。

## 2. 主题 A —— 编排瘦身(保每股 workflow)

**目标读数**:主会话 $30.50 → **≤$15(≤25%)**;L4 段墙钟(冷日)57m40s → **~40m**。

### A1 滑窗派发(消批间屏障,不动 workflow 粒度)

现状:主会话按 `dispatch_batches` 三批串行,每批 4 只并行、**等全批完成才派下一批**——批内
最慢者拖住整批(今晚批 3 普冉 40.6min 独自拖尾)。

改法(纯 SKILL 派发契约改写,零 python 代码):

- 初始并行派 `effective_cap`(=4)只;**每收到一股 workflow 完成通知,立即补派下一只 pending**,
  始终保持 cap 只在飞;
- 派发顺序**最长者先行**:📌 pinned(强制满卡+双复核,实测 25-40min)优先于非 pinned
  (12-20min)——今晚若 pinned 首批派出,总墙钟估省 15-25min;
- 每次唤醒只做一件事:领通知 → 补派一只 → 不产出分析文字;
- `l4_tasks batches` CLI 不动(重放/断点续跑语义保留;批完成判据改为「task_book 全
  SUCCEEDED」,见 B6)。

### A2 l4_watch 单一进度源 + progress.py 退役

双 Monitor 各有病:`progress.py` 按产物存在性猜阶段(累犯,pr_20260717_004,E1 已挂退役);
卡片 grep 监视读到 601319 写盘中途草稿(§1.4-4)。

改法:新建 `autoresearch/scan/l4_watch.py --watch`(确定性,零 LLM):

- **唯一权威 = `_l4_tasks.json`**:轮询 task_book,只在某股 status 翻 `SUCCEEDED` **且 card
  hash 已记**后,才读卡片 grep 评级行,打一行 `k/N 代码 名称 → 评级`(name 取 finalists.csv);
- 兼报 `FAILED`(含 error class)与 RUNNING 超龄(>30min 提示,阈值可参数);
- 全部 SUCCEEDED(或含 FAILED 的终态)即退出;
- SKILL 的进度可视化节改挂这一个 Monitor;CP5 滚动表节整段删除(l4_watch 就是 CP5);
- `progress.py` 按 E1 原案退役:删模块 + `tests/scan/test_progress.py` + SKILL.md:45 引用
  (删前 grep 消费者,遵循 deadcode-cleanup 双职检查)。

### A3 SKILL 精简二期(48.7% > 25% 触发线已过)

`scan-market/SKILL.md` 运维细节段下沉 `STAGES.md`(SKILL 留一行指针):CP 表格逐条取数命令、
Monitor 误报考古注、07-21 空 config 事故长注、prewarm 安装命令与实测注、CP5 滚动表做法
(A2 后已过时)。目标:SKILL 主文行数 **−40%**(以 `wc -l` 现值为基线)。**契约锚字符串不动**
(`_CONTRACT_ANCHORS` 六锚),改后跑 doc-lint + `tests/test_agent_defs.py`。

### A4 唤醒纪律(runbook)

派发/收通知回合零播报(CP4/CP6/CP7 保留;CP5 归 l4_watch);workflow 完成通知里 ~2KB args
回显是 harness 行为,主会话不复述;CP2/CP3 合并为一次播报。

## 3. 主题 B —— 「存在≠有效」根治四件套(+2)

### B1 writer 侧原子写(根治,比门更上游)

`frame.py` 增 `--json-out PATH`:JSON 先写 `PATH.tmp` 再 `os.replace`——**产物文件与
stdout/stderr 彻底解耦**,壳加不加 `2>&1` 都污染不了产物;取数半途崩 = 无文件(不是半文件/
垃圾文件)。`scan-market.js:63` 改 `frame ${date} --json-out ${SD}/market_pack.json`。
同族普查:grep 两个 workflow js 全部 `>` 重定向产物命令,逐一改 CLI 侧落盘(frame 现已把
构建段 stdout 圈进 stderr——`frame.py:166` 注——所以 07-28 的污染源纯粹是壳加的 `2>&1`;
原子写把这最后一条通路也堵死)。

### B2 门判据升级(pr_20260728_001 落地)

`scan-market.js:71/77` pack-check/recheck:`test -s` → `python -c "import json;
json.load(open(...))"` 退出码判据。B1 落地后此门理论上永不触发——保留作纵深防御。

### B3 bash() 壳硬约束

`scan-market.js:~30` bash() 与 `l4-stock.js:32/38/183` 三处 gp-haiku 壳的 prompt 统一加:
「命令**逐字节原样执行,不得添加 2>&1/tee/管道或改写重定向**;若 stdout 已被重定向,回报
退出码 + stderr 末 15 行」。今晚事故第一因。

### B4 全 CLI 入口冒烟测试

新 `tests/test_cli_entrypoints.py`:参数化 ~25 个 `python -m autoresearch.<mod> --help`
(subprocess,断言退出码 0)。变异探针天然成立:还原 assemble 的 argparse bug → 测试红。
1952 绿逮不到 CLI 断裂的口子从此关上。

### B5 prelude 汇总屏诚实失败

`prelude.py:388` 区:`contextlib.suppress` → 显式 except,
`print(f"[prelude] ✗ 汇总屏落盘失败: {e}", file=sys.stderr)`(仍不阻断);
`scan-market.js:90`:`&& echo "SUMMARY_FILE=..."` → `test -s <file> && echo SUMMARY_FILE=...
|| echo SUMMARY_MISSING`。日志不再替不存在的文件背书;写失败根因(今晚被吞)下次运行自证。

### B6 `l4_tasks batches` RUNNING 可见性

`l4_tasks.py` dispatch_batches(:431,选取逻辑 :450)输出增 `running` 数组(code + started_at
龄期分钟);SKILL 派发契约明写「批/全程完成判据 = task_book 全 SUCCEEDED,**非** batches 为空」。

## 4. 主题 C —— L3 对已注入旗的消费失效

### 4.1 证据

`_l3_table.md:115`(601918 行)已渲染 `-0.66 | 反号`(main_inflow −0.66 亿 vs ratio +0.108),
L3 thesis 仍写「main_net_ratio 0.108 全表最高」当核心多头论点 → L4 实读「主力绝对净出且占比
失真」压 UW。**同一 L3 会话**在 bench 侧拒兴业/华泰/中信时全部正确引用反号/main_dist 旗——
旗在、消费不一致。按 instruction-vs-check-mismatch 修法排序(补指令 > 标记 > 加严检查):
`l3-rank.md` 硬约束列表(现 A-E)没写这条要求,病在指令缺失。

### 4.2 C1 硬约束 F(prompt_patch 人批通道)

经 `fs.add_prompt_patch` 起草(自带契约锚校验),target `.claude/agents/l3-rank.md`
「选股硬约束」节(:28)追加:

> **F**:带「反号」旗(主力净额与占比符号相反)或 main_dist 失真旗的票,**主力资金一律不得
> 作为入选/OW 论点**;若凭其它证据仍入选,thesis 必须显式声明「资金证据不可用」并给出替代论据。

evidence 三条:601918 今晚(旗在被无视 → L4 翻案)+ bench 三券商(同会话正确消费 = 指令
可执行性自证)+ cross_calib 账本。**施工走人批**,本文只锁补丁草案原文。

### 4.3 C3 治理接线(registry 第一个真实条目)

L3 prompt 属治理清单「L3 行为变更」,但 prompt 无法影子跑(不能双跑 L3)。折中:

- C1 补丁登记为 `PREREGISTERED` 实验:definition = 补丁全文,baseline = 当前 `l3-rank.md`
  git sha,rollback = revert 至该 sha;
- 人批 `approve` + `activate` 同日(影子不可行的显式记录写入 spec 字段);
- 事后守卫:`rollback_watch` 盯 healthy lane OW-lean 确认率与 finalist 质量 facts,
  **≥5 个前向扫描日**再 `accept`/`rollback`。

prompt_patch 流程与实验治理从两套平行账合流;registry 从零条目变为有第一个活案例。

### 4.4 C2 cross_calib 增「OW-lean 确认率」per lane

现状:`cross_calib.flip_stats` 已 per-lane 计算(groupby lane),但 flip 定义 =
conviction≥70 ∧ L4≤UW——**今晚 6 只 OW-lean 全部不进这个分母**(仅 601288 conviction 72
≥70,且其 L4=Hold 不算 flip)。L3 看不到自己 lean 层面的前科。

改法:`cross_calib.py` 新增 lean 口径统计——per lane 的
`(triage_lean=OW 数, L4 终评 ≥OW 确认数, 确认率)`(shrink 收缩 + n<3 禁注,复用现基建);
注入行从「只挑 flip_rate 最差 lane」扩为「flip_rate 与 lean 确认率两指标各挑最差一行」
(仍 ≤2 行,防注入膨胀)。今晚读数:healthy lane OW-lean 确认 **0/6**。零 prompt 改动,
纯账本派生数据。

## 5. 主题 D —— 学习环尺子重定 + 到期裁决

### 5.1 D1 弃权账本 v2(反事实换 shadow_buys)

病根(`abstention_ledger.py:~228-247`):`opportunity` = 全市场任一 eligible 票
`excess_2 ≥ +2pp` → FALSE(reason `market_relative_opportunity`)。5,000 只票的市场几乎
恒真(07-15 数出 1,503 只,DEGRADED 数据仍判 FALSE),量的是**召回上限**不是**弃权决策**;
与 paper_nav(真实 −0.24% vs 影子 −3.45% = 门在挣钱)同屏互扇。

改法:

- **FALSE 新判据**:`shadow_buys.csv`(系统当日最想买 3 只,20260618 起有史,列
  date/code/name/conviction/binding/close)中任一只 `fwd_2_oc − 市场中位 ≥ +2pp` 且次开可交易;
- 旧全市场口径降级为 **`recall_ceiling_n` 诊断字段**(保留信息行,撤出裁决 headline);
- **过渡期双打印 ≥10 个成熟日**(新旧 verdict 两列并排)后旧 headline 退役;
- 历史行按 shadow_buys 可得性回算,缺数据行不入 v2 统计(md 渲染 `—(no_shadow)`,
  不新增 status 枚举值——现枚举有迭代消费方);
- `report_sections` 的 0 买判词行同步改口径。

### 5.2 D2 到期裁决点的裁决规则落账(不是现在裁)

| 裁决点 | 状态 | 预定义规则(写入账本 docstring,届时按数据裁,人拍板) |
|---|---|---|
| SELL 复核 3 跑→1 跑 | ensemble n=5 已到,折回对错 07-30 起成熟 | ≥5 折有成熟结果且含 ≥1 分歧场景开裁:折回救对率 <50% → 降 1 跑;≥50% → 维持,再攒 5 折复裁 |
| OW 复核降档 | 买单 n=9,差 1 单 | 下一买单后开裁,同款救对率规则 |
| intel 限频强制力 | 3 超限 run 已命中 | 本波直接执行(→D3),不再等 |

### 5.3 D3 intel 限频升格(advisory → 有牙齿)

数据:今晚 11/11 稿自报 16-29 条,中位 ~18。双腿:

- **cap 15→20**(`scan_config.jsonc` `l4_intel.max_queries`,对齐实测中位,消掉天天 warn 的
  狼来了效应);
- **>30 = 硬顶**:新 `autoresearch/scan/l4/intel_guard.py`(确定性 CLI:解析声明行自报条数
  → verdict;超硬顶把稿改名 `_l4_intel_<code>.rejected.md`),`l4-stock.js` 在 [slim∥intel]
  屏障后、card 前经 gp 壳调用;card 侧 presence-gate 找不到 intel 文件 → 自动回退卡内网查
  (现有机制零改动)。**只拒稿不拒票**(红线);
- 自报缺失照旧 warn(无法对账 ≠ 合规)。

### 5.4 D4 registry 实例化 + report `--out` 锐边

- `experiment_registry baseline` 注册稳定基线(当前 weights sha + scan_config hash +
  l3-rank.md sha)——治理从「枪造好」到「上膛」;
- 首条目 = C1 实验(§4.3);其余候选(OW 三门 risk_off 变体等)**不注册**——读数全 IMMATURE,
  规矩是攒够再谈;
- 修 Wave5 已知锐边:`experiment_registry.py:792` write_report 加 `--out` 参数,演练不再
  覆盖生产 `reports/learning/experiments.md`。

### 5.5 D5 index_daily 入湖(指数断言可对账)

- dataflows 增 tushare `index_daily`(上证 000001.SH / 深成 399001.SZ / 创业板指 399006.SZ /
  科创50 000688.SH / 沪深300 000300.SH / 中证500 000905.SH,B 级契约缺则降级不阻断),
  prewarm/frame 顺带预拉;
- `scan/price_claims.py` 加指数分支:断言含指数名/代码 → 对指数湖裁真伪(杀掉「创业板指
  −7.35%」这类只能挂着的 warn);
- `market_pack.today_slice` 补三大指数当日涨跌(pr_20260721_002 原案未竟部分,策略师小节 2
  盘面句从此可对账)。

## 6. 批次、验收、日历 v4

### 6.1 批次与依赖(实施计划按此拆 task)

| 批 | 内容 | 依赖 | 预估 |
|---|---|---|---|
| **批 Ⅰ 止血** | B1 原子写 · B2 门判据 · B3 壳约束 · B4 CLI 冒烟 · B5 诚实失败 · B6 RUNNING 可见 | 无 | ~1 天 |
| **批 Ⅱ 瘦身** | A2 l4_watch+progress 退役 · A1 滑窗派发 · A3 SKILL 精简 · A4 唤醒纪律 | A2 的 l4_watch 先行(A1/A3 的 SKILL 文字要引用它;SKILL 三处改动各自独立 commit) | ~1 天 |
| **批 Ⅲ 尺子** | D1 弃权 v2 · C2 lean 确认率 · D3 intel 升格 · D5 index 入湖 | 无互依 | ~1 天 |
| **批 Ⅳ 治理** | C1 补丁起草(人批) · C3+D4 registry 实例化 · D2 裁决规则落账 | C1 在 C3 前 | ~半天 |

推荐次序 Ⅰ→Ⅱ→Ⅲ→Ⅳ;Ⅲ/Ⅳ 可并行。

### 6.2 本波「完成」的定义

1. 下次真实扫描:主会话 $ 占比 ≤25%、L4 段墙钟(冷日)≤45m、唤醒序列中零批间空等;
2. 变异探针 2 例:故意注入垃圾 pack → 门红;删 assemble 的 argparse import → B4 测试红;
3. CP5 全程零草稿误读(l4_watch 只报 SUCCEEDED 后评级),progress.py 已删且无残留引用;
4. 下次 0 买日 summary 弃权 headline 基于 shadow_buys,与 paper_nav 同向不再互扇
   (双打印过渡照常);
5. intel 稿 warn 从 11/11 降到仅 >20 者;>30 拒稿路径有一次演练证据(含 card 回退);
6. registry 非空:baseline + C1 实验条目;`report --out` 演练不污染生产报告;
7. C1 生效后 ≥5 个扫描日:healthy lane OW-lean 确认率进入 🔁 注入且方向复核
   (观察指标,非门)。

### 6.3 裁决日历 v4(继承 v3 未到期项,新增)

| 时点 | 裁决/动作 | 输入 |
|---|---|---|
| 07-30 收盘 | 首批 3 折折回对错成熟 → ensemble 裁决材料开始积累(≥5 折开裁) | ensemble_ledger |
| C1 激活 +5 扫描日 | rollback_watch accept/rollback | cross_calib lean 确认率 |
| 弃权 v2 上线 +10 成熟日 | 旧 headline 退役 | abstention 双打印 |
| D5 上线首跑 | price_claims 指数分支对账演练 | index 湖 |
| 下一买单 | OW 复核降档开裁 | buy_ledger(n=9→10) |
| P5 装载 +1 周(~08-04) | 「备料未收尾」增量=0 验收(Wave7 项,继承) | retro pending |
| ~08-08 | event 路裁决 · intel A/B 结算(Wave7 项,继承) | 各账本 |
| 8 月中报季 | dossier.reconcile 20260630(继承) | 披露日历 |

## 附录 A:延迟决策(不丢上下文)

| 路径 | 内容 | 不选原因 | 解锁条件 |
|---|---|---|---|
| **l4-fleet 单 workflow** | l4-stock 逻辑内联为一个 workflow 的 pipeline(),11 股一次交接:主会话唤醒 14→~2、批间屏障消失(股1 写卡时股5 slim 在跑,L4 段 57m→~35-40m)、task_book 逐股恢复语义保留、resumeFromRunId+agent 缓存断点续 | 用户 2026-07-29 裁定保留 fb_20260714_003(每股一个 workflow) | 滑窗+瘦身(批 Ⅱ)落地后两次真实扫描,若主会话 $ 仍 >25% 或 L4 墙钟仍 >45m,可重提 |
| 唤醒批处理/通知合并 | harness 无此机制 | 平台能力,非本仓可改 | harness 支持后 |
| ensemble run2∥run3 并行 | wall −7min(分歧场景) | Wave7 附录 B 维持:token 优先 + 分歧样本 1 例 | ensemble_ledger 分歧率 >40% 且 wall 成瓶颈 |
| cfg 走文件传递 | 派发 args 瘦身 | workflow js 无文件系统访问,cfg 供 js 本身路由 effort,必须内联(~1KB,代价可忽略) | 无 |
| abstention 全市场口径彻底删除 | 简化 | 召回上限信息仍有诊断价值 | 双打印期后若无人读,随旧 headline 一并退役 |

## 附录 B:证据索引

- run 取证:`reports/scan/20260728_2319/`(summary/token_usage/details×11/trace 97 件);
  `context/scan/2026-07-28/`(`_l3_table.md:115` 反号行 / `_l4_tasks.json` 终态 /
  `_ensemble_{300857,688766,601869}.json` / `_budget_observation.json` / `market_pack.json`
  修复版 / `_token_usage.json`)。
- 事故一手记录:本 session workflow journal `wf_26b68702-717`(pack-check `{'ok':true}`
  放行行 / macro-brief 拒写全文 / prelude agent「Summary file generated」误报);
  垃圾 pack 原文(1,776B,已被修复版覆盖,内容形态记录于 §1.4-1)。
- 代码定位:`scan-market.js:30(bash)/63(frame)/71,77(pack-check)/90(SUMMARY_FILE)`;
  `l4-stock.js:32,38,183(gp 壳)/81-102(intel 相位)`;`frame.py:154-166(main/--json)`;
  `prelude.py:388(suppress)`;`l4_tasks.py:431,450(dispatch_batches)`;
  `abstention_ledger.py:228-247(FALSE 判据)`;`cross_calib.py:42-97(flip_stats)/191-203(注入)`;
  `price_claims.py`;`experiment_registry.py:291(baseline)/792(write_report)`;
  `l3-rank.md:28(硬约束节)`。
- 账本:`reports/learning/{paper_nav,zero_buy_ledger,abstention_ledger,ensemble_ledger,buy_ledger,pinned_ledger}.md`;`context/learning/shadow_buys.csv`。
- 上游:07-28 总纲(调度权威)/ Wave7 spec(B′/N/P 机制细节与已验证项)/
  `pr_20260728_001`(本波 B2 落地它)/ fb_20260714_003(A 节边界)/
  instruction-vs-check-mismatch 归档(C 节修法排序)。
