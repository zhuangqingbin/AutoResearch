# Wave11 设计稿:评判尺切换 × 配置统一 × L4 全并发 × Skill 整备(2026-08-05)

> **状态**:设计定稿,待实施(本稿只落文档,不写实现——用户 2026-08-05 裁定)。
> **调度定位**:Wave10 稿(2026-08-01)仍是报告优化/激进退役/0买归因线的调度权威;
> 「下一波」批1-6(2026-08-03/04 稿)已实施完毕。本稿另立四个工作面,编号 **Wave11 批A-D**。
> **现状证据底片**:`docs/research/2026-08-05-selflearning-loop-audit.md`(下称「审计稿」)。
> 冲突以源码为准;实施时逐 task 先跑「立案前提自查」(premise-check,2026-07-28 家训:
> 立案时写的诊断,动工一查可能全错)。

## 0. 用户裁定记录(2026-08-05 brainstorm 问答,盖过先前相关裁定)

| # | 问题 | 裁定 |
|---|---|---|
| R1 | 评判尺:字面隔夜尺 vs 现行 fwd_2_oc vs open-to-open | **字面隔夜尺 `gap_c1_o2 = open[T+2]/close[T+1] − 1`(T+1 收盘买 → T+2 开盘卖)**。取代 2026-07-10 的 fwd_2_oc 主尺裁定;超短 1~2 日风格与「勿推 swing」不变 |
| R2 | 废弃存量处置 | **文档+顺手删文件**(删定义/文档文件算清理不算开发)。实施结论见批D:skill/agent/workflow 层无整文件死尸,可删的死码全在 learning 包且被 test 引用 → 按协议归实施批 |
| R3 | agent model/effort | 统一收口 `.claude/skills/scan-market/scan_config.jsonc`,并且**必须可验证生效** |
| R4 | L4 并发 | **一次性全部并发**(恢复 fb_20260714_003「别分 wave」原意),省墙钟 |

**推荐实施序:C → B → A → D。** C 最小且立收(墙钟减半);B 堵住 08-05 壳杀进程这类
配置/执行层事故的复发面;A 是大活(换尺全链);D 是文案收尾(D5 依赖 A5 的卡契约 v4)。
四批彼此独立可单独回滚,唯 D5←A5 一条依赖。

**非目标**(本稿不碰):L3/L4 评级语义与 rubric 三门;召回 quota(momentum 相位条件 quota
另案走 registry,见 2026-08-04 研究稿);新增任何 LLM 调用点;dossier 学习子环。

---

## 批A 评判尺切换 `gap_c1_o2` + 闭环补强

### A0 语义定义(全批的单一事实源)

```
gap_c1_o2 = open[D+2] / close[D+1] − 1        # D = 报告日(盘后出报告)
买腿:D+1 收盘价成交。不可执行(unbuyable_c1):D+1 收盘封涨停(close≈涨停价 且 close==high)
     ——涨停封板收盘买不进;跌停收盘可买(愿意接就有货),不剔。
卖腿:D+2 开盘价成交。受限(unsellable_o2):D+2 一字跌停开(open≈跌停价 且 open==low)
     ——标旗不剔样本,账本以旗列呈现(卖不出=真实亏损延续,剔了反而美化)。
成熟:D+2 EOD 数据发布后(tushare 当晚)——与 fwd_2_oc 同晚,复盘节奏不变。
```

设计取舍(实施时不再讨论):
- **买腿剔除、卖腿标旗**:买不进=这笔交易不存在(与现行 `buyable` 剔 D+1 一字板同理);
  卖不出=交易存在但延续风险,必须留在样本里,另以 `unsellable_o2` 旗供分析。
- 涨跌停价判定复用 `factor_lab._board_limit`(10/20/30cm 按板)乘 0.98 容差,与现行
  `sealed` 判定同款。
- 旧列 `fwd_2_oc`/`hi_2_oc` **降参考不删**(与 07-10 处理 fwd_5/fwd_10 同手法),历史行
  不改写。

### A1 中央尺常量 + 新列生产

- 新文件 `autoresearch/common/ruler.py`:
  ```python
  MAIN_RULER = "gap_c1_o2"          # 唯一主尺出处;换尺=改这一行
  RULER_LEGS = {"gap_c1_o2": ("close[D+1]", "open[D+2]"), "fwd_2_oc": ("open[D+1]", "close[D+2]")}
  ENTRY_FLAG = "buyable_c1"         # 主尺配套可执行旗
  EXIT_FLAG  = "unsellable_o2"
  TOUCH_COL  = "touch_o2"           # 触价尺:见 A5
  SCHEMA_SWITCH_V4 = "<实施日>"     # 卡契约 v4 日期分界(唯一出处,buy_ledger/l4_reuse 同源引用)
  ```
- `factor_lab.forward_returns` 增三列:`gap_c1_o2`、`buyable_c1`、`unsellable_o2`;
  `FWDS` 注册新列;IC 侧 `gap_c1_o2.clip(-0.31, 0.31)`(单日板极值:主板 10cm/创业科创
  20cm/北交所 30cm,取最宽板+容差;比 fwd_2_oc 的 ±0.30 两日复利口径窄一档语义,
  clip 参数写进 ruler.py 常量,勿散写)。
- **验收探针(变异测试,wave35 纪律)**:单测里把 gap 计算改成 `close[D+2]/close[D+1]`
  (错腿变异)与符号翻转两个变异体,断言现有断言能逮住——先证明测试有鉴别力再信绿灯。

### A2 消费点常量化 sweep(治「换尺=摸 30 个文件」的结构病)

- 审计稿 §1 列出的 30+ 文件,凡 `"fwd_2_oc"` 字符串用作**主尺**处 → 改 `ruler.MAIN_RULER`;
  用作**历史列名/参考尺**处(如 refresh 补列、hold=10 对照)→ 保留字面量并加行注
  `# 参考尺,固定列名,勿随主尺漂移`。逐文件二分类清单在实施 plan 里落表,不凭印象。
- **byte-parity 守卫**:sweep 提交前对一个历史日(建议 2026-07-31)重放
  `retro.attribute` + `stage_eval` + 各 ledger 渲染,断言输出与 sweep 前 **byte 一致**
  (此步只做常量化不换值,MAIN_RULER 暂仍= "fwd_2_oc")。常量化与换值**分两个 commit**,
  各自可回滚。
- 换值 commit(`MAIN_RULER = "gap_c1_o2"`)单独提,配 A8 对照报告。

### A3 账本历史回填(n 不清零,07-10 先例)

- `retro.refresh_attributions` 家族与 `t1_review backfill` 增补 `gap_c1_o2/buyable_c1/
  unsellable_o2` 列:湖 OHLC 全量可算,历史 attribution.csv/账本 jsonl **追加列不改旧值**。
- 账本行增 `ruler` tag(写入时取 `MAIN_RULER`);渲染层按 tag 分段汇总,禁止跨尺混合均值
  (07-16 「先问这把尺子量的是不是他做的事」家训的结构化落法)。
- 验收:回填后随机抽 3 日 5 票,手工用湖 OHLC 复算 gap 值对上;`buyable_c1` 在涨停票上
  的剔除率与 `sealed` 口径对照并出统计行。

### A4 权重腿切换 + regime 块修缮

- `recalibrate_and_log` / `factor_lab.calibrate` / `_build_calib_panel` 的 `label_col`
  改引 `MAIN_RULER`;IC/eval 同步。
- **顺手治审计稿病灶②**:切尺后立即跑 `calibrate_regimes`(两半符号一致门),把
  `weights.json.regimes` 从空块补成真分桶;若不过门 → **显式裁定**:要么保持 flat 并把
  `--regime-aware` 从 SKILL/prelude 默认参数里摘掉(旗子与数据一致),要么留旗留记账。
  不允许继续「旗开块空天天降级」的第三态。
- 回滚:`fs.snapshot_weights` 快照链已现成;换尺前快照 sha 记入本稿实施 plan。
- 验收探针:changelog_ledger 心跳行必须出现「label_col=gap_c1_o2」字样(会变的量)。

### A5 卡契约 v4(隔夜口径)+ 触价语义重锚

**直面语义坍缩**:隔夜窗内唯一实现价 = T+2 开盘。盘中目标价/止损在这把尺下**不会被系统
执行**,它们从「预测窗内触达」降格为「入场论据」。

- 卡模板(`.claude/agents/l4-card.md` + stock-research `lite-playbook.md`)改:
  - EV/三情景 R:R 全部改写为**隔夜口径**(对 T+2 开盘价的三情景);
  - 目标价语义改「T+2 开盘预期带」;止损/tripwire 改「T+1 尾盘入场否决条件 + T+2 开盘
    应对预案」(涨停开/跌停开/平开三分支);
  - 加机器契约标记行 `〔卡契约 v4·隔夜 c1→o2〕`(锚测试锁死,承 v3 手法)。
- `buy_ledger._hi_col_for` 三段分界:`< 2026-07-10 → hi_10_oc`;`< SCHEMA_SWITCH_V4 →
  hi_2_oc`;`≥ V4 → touch_o2 = open[D+2]/close[D+1] − 1 对目标带的命中`(📐 触达率改口径,
  分界日常量引 `ruler.SCHEMA_SWITCH_V4`,旧卡不冤枉)。
- 📐 目标价校准当日件文案随之改:「目标带 vs T+2 开盘」;`hi_2_oc` 触价读数降参考,
  账本保留双列过渡 ≥20 交易日。
- ⚡ tripwire_watch:盯梢线仍以收盘价触发(它服务持仓人工裁,不在评判尺辖区),但卡内
  写线时须声明口径,`self_review` 增「v4 卡缺口径声明」warn。

### A6 t1_review:gap 终判(快环不失速)

- D+1 晚:现状不变(cc1 初判 + 诊断,速度优势保留);`scorecard` 列改名口径注明
  `cc1(初判尺)`。
- D+2 晚:`nightly_close` 增一步 `t1_gap_finalize`——对前一日 scorecard 回填
  `gap_c1_o2 / final_verdict(准|不准|中性,z 法同 v2,尺=gap)`;账本行整替(幂等)。
  **准不准的对外口径以 final_verdict 为准**(R1 裁定);cc1 verdict 降「初判」。
- t1-review workflow(LLM 诊断)输入两尺并示,prompt 注明「终评尺=gap;初判≠终判的票
  重点解释隔夜变化」。
- 验收:构造一个 cc1 判准、gap 判不准的历史票(07 月存量里找),断言 final_verdict 覆盖。

### A7 retro:pending 语义拆分 + 诊断批量补курс

- `retro pending` CLI 输出拆两段:`attribution_pending`(真欠归因)/`diagnosis_pending`
  (归因已备、诊断未跑,即现状 6 日那种)。prelude 建议行同步改文案。
- scan-retro skill 增「批量补诊断」模式:一次 workflow 吃 ≤N 日(建议 N=5)的
  retro_input.md 合诊(跨日模式比逐日更看得见系统性病因;超 N 分批)。诊断完成即
  `mark_done`(触发 decay_lessons),欠账清零判据=diagnosis_pending 为空。
- 验收:对现存 6 日欠账实跑一次批量补诊断作为首验。

### A8 两尺对照报告(上线前认知底片,非批准门)

- 离线 replay 近 30 个 scan 日:gap_c1_o2 vs fwd_2_oc 的 ①门价值(paper_nav 口径)
  ②召回路 unique 超额排序 ③L3 真选 edge ④弃权裁决翻转数。落
  `docs/research/<实施日>-ruler-gap-vs-oc-baseline.md`。
- 定位:**用户已裁定,不设批准门**(承 07-10 先例);对照报告是为了知道换尺后哪些历史
  结论会翻(如 value 路第一、momentum 相位条件性是否在 gap 尺下仍立),防止拿旧尺结论
  指挥新尺生产。翻转项逐条列入报告尾「作废/待重验清单」。

### A9 paper_nav 隔夜模式

- `simulate` 增 gap 模式:D+1 收盘建仓、D+2 开盘清仓、现金间隔;主表切 gap,
  `hold=2` 降对照副表(与 hold=10 并列)。真实/影子/sized/市场四线口径同步。
- 验收:影子 NAV 历史重放曲线与 fwd_2_oc 版并排出图一次,数量级合理性人查。

**批A 整体回滚杆**:`ruler.MAIN_RULER` 改回 `"fwd_2_oc"` + weights 快照恢复 + v4 分界日
后无新卡(分界日前行为逐字节不变,由 A2 的 parity commit 保证)。

---

## 批B agent model/effort 统一收口 + 生效对账

### B1 role 闭集注册表(治「自由形状=拼写错静默失效」)

- `user_config.py` 增 `_AGENT_ROLES` 闭集,`agents` 子键必须 ∈ 闭集,否则 raise(与顶层
  白名单同一存在理由)。role 清单(2026-08-05 盘点,共 12):

| role | 用途 | 现状出处(硬编码位置) | 现值 |
|---|---|---|---|
| `strategist` | 市场研判 | 已走 config | opus·max |
| `sector_brief` | 行业 brief | 已走 config | opus·xhigh |
| `l3_rank` | L3 精排 | 已走 config | opus·max |
| `l4_intel` | 活体情报 | 已走 config | sonnet·max |
| `l4_card` | 决策卡 | 已走 config | opus·max |
| `t1_diag` / `t1_synth` | T+1 复盘 | 已走 config | 继承·high |
| `ens_review` | ≥OW/SELL 双复核 | l4-stock.js:237 借用 l4_card 档 | 现 effective=opus·max(缺省回退 xhigh) |
| `l3_repair` | L3 数字自修 | scan-market.js:283 硬编码 | l3-rank·medium |
| `dossier_init` | 档案首覆 | dossier-init.js:39 硬编码 | opus·max |
| `gp_shell` | 命令壳(bash/stageGate/recordL4) | 3 个 workflow 散布 | sonnet/haiku·low 混杂 |
| `gp_shell_json` | JSON 壳(gate/gpJson/taskGate/ens-dump/lint) | 同上 | 同上 |

- `gp_shell` 与 `gp_shell_json` 的默认值裁定:**sonnet·low**(08-05 事故结论:haiku 处理
  不了「后台任务+长等待」,已实测两次 pkill 生产作业;省钱不省在这层——壳仅 ~15 调用/扫,
  差价 « 一次毙流水线)。config 可降回 haiku,但那是显式选择不是缺省。

### B2 workflow 端统一消费

- 三个 workflow(scan-market.js / l4-stock.js / dossier-init.js;t1-review.js 已合规)
  顶部立 `AGENT_DEFAULTS` 常量表(role → {model, effort}),**全部** `agent()` 调用点改
  `resolve(role)` 取值:`cfg.agents?.[role] ?? AGENT_DEFAULTS[role]`;禁止调用点内联
  字面量(lint 见 D4)。
- 回退链定案:**config > workflow AGENT_DEFAULTS > agent .md frontmatter**。frontmatter
  作为 agentType 自带缺省仍生效(不删),但 workflow 层显式传值时以传值为准——三层语义
  写进 scan_config.jsonc 头注释,唯一文档出处。

### B3 scan_config.jsonc 增 roles 文档

- `agents` 块补 5 个新 role 键(ens_review/l3_repair/dossier_init/gp_shell/gp_shell_json),
  每键行注:用途/默认/什么时候该动。现有 7 键注释順手校对(strategist 行的 07-12 沿革注
  保留)。

### B4 生效对账探针 `trace.usage_reconcile`(R3 的「确保生效」)

- 新 CLI `python -m autoresearch.trace.usage_reconcile <date>`:
  读 `_token_usage.json`(usage_harvest 已产出每 subagent 的 model/effort/agentType 实测)
  × 当日 `user_config_echo.json` 期望 → 逐 role 对账表(期望≠实测的行标 ❌,含「config 写了
  但没有任何 agent 以该 role 跑过」= 接线断裂检测)。
- 接入:CP7 四条命令后追加第五条;`self_review` 增 check「配置-实测不符」——首个扫描日
  warn,连续两日 fail(承 warn 升 binding 惯例)。
- **变异验收**:离线造一份把 `l4_card.effort` 改 `low` 的 echo + 真实 harvest json,断言
  reconcile 必须报差异;再造一份 role 拼错的 config,断言 B1 在装载时就 raise(两层各自
  逮各自的)。
- 边界如实声明:effort 是请求参数,harvest 记录的是**发出的请求**;它证明「配置到达了
  调用点」,不证明推理深度本身——这已是可对账的最深层,写进 reconcile 报表头。

### B5 空配置 fail fast(07-21 事故根治)

- scan-market.js / l4-stock.js 开头:`cfg` 为空对象且未传 `args.allow_empty_config=true`
  → `throw`(消息引用 07-21 事故:空 cfg = 静默关 intel + 全体掉回缺省 effort)。
  SKILL.md「⚠️ 配置必传」段同步改为「结构性强制,逃生旗 allow_empty_config」。

### B6 测试

- user_config:未知 role raise / 已知 role 通过 / agents 非 dict raise。
- workflow js:AsyncFunction 解析探针进 tests(`node --check` 假绿灯已两次实测,禁用);
  对 `AGENT_DEFAULTS` 表做「调用点无内联 model/effort 字面量」的 grep 断言(gp shells 的
  两个定义点除外)。

**批B 回滚杆**:roles 段从 scan_config 删除 → AGENT_DEFAULTS parity;B4 探针独立可摘。

---

## 批C L4 全并发(一次性全派)

### C1 caps 语义拆分(l4_tasks.py)

- 现状:`effective_cap = min(tushare, web_search, web_fetch, l4_stock) − rate_limit_failures
  = 4`,把**资源限速**和**派发并发**焊在一把闸里;`batches` 按 4 切片喂滑窗。
- 改:`dispatch_cap = caps.l4_stock`(默认改 **64** = 事实无上限);`batches` 返回
  `[全部 pending]` 单批。`tushare/web_*` caps 保留但**退出 min 运算**,只喂 C2 的操作级
  信号量。`rate_limit_failures` 扣减改作用于 tushare 信号量槽数(风暴时自动收缩取数并发,
  不再缩派发)。
- `budgets.concurrency` 配置通道现成(user_config → l4_tasks:549),值语义文档化。

### C2 tushare 操作级信号量

- `prepare_slim`(单票 slim 取数,tushare 大户)入口加 fcntl 多槽锁:
  `context/scan/<date>/_sem/tushare.<0..K-1>.lock`(K=caps.tushare=4),尝试轮询获槽,
  等待时心跳日志(每 60s 一行,防「卡住」误判——08-05 壳杀进程事故的另一半药)。
  stale 锁按 mtime 回收(承 nightly_runner 手法)。
- intel(纯网)/card(LLM+有界网查)/复核不排队,即刻起跑。
- 夜间预热已把湖预拉做在前(08-05 实测 30/30 池预取),slim 湖命中时秒过信号量——
  信号量只在真取数时起作用,不制造额外串行。

### C3 派发协议改写(SKILL.md 步骤 4)

- 主会话:**一条消息 N 个 Workflow 调用全派**(📌 pinned 排列表最前只为 watch 可读性,
  无先后语义);删滑窗段(「每完成一只补派一只」整段),保留为「回滚模式」附录引用。
- 完成判据不变(task_book 全 SUCCEEDED);单票失败重放不变(batches 现返回单批全量
  pending,只含未完成票);l4_watch/唤醒纪律不变——全派后主会话唤醒次数反而从
  2N(领通知+补派)降到 N(只领通知)。
- 预期收益(以 08-05 实测为基线):L4 段墙钟 76m51s → ≈ max(单票链) ≈ 40m(pinned
  满卡+双复核为长杆);slim 段 50m08s 因湖预热+信号量不变或略降。**总墙钟预估省
  ~35-40 分钟**;票数>10 时收益放大。

### C4 首跑观测(YAGNI 边界)

- task book 已记 attempts/error:首跑后出一行统计(RATE_LIMIT/CONNECTION/TIMEOUT 计数、
  slim 排队最长等待)。**429 率 >10% 或 intel 空稿率显著升高才考虑 stagger**,本批不做。

### C5 实验治理(速度类 challenger,按铁律登 registry)

spec 草稿(实施时落 `docs/research/<实施日>-wave11-experiment-specs.json`):

```json
{
  "id": "exp_l4_full_parallel",
  "family": "speed",
  "hypothesis": "L4 全并发派发(dispatch_cap=∞, tushare 降操作级信号量)使 L4 段墙钟 P50 下降≥30%,且不改变任何评级产物",
  "baseline": "滑窗 cap=4(2026-07-28..08-05 实测:L4 段 76-113min)",
  "guardrails": {
    "research": "无(不触评级语义——性能开关不拥有评级铁律)",
    "decision": "完成率=N/N;schema/contract 错误 0;评级分布无系统位移(描述性对照,同日无法双跑)",
    "token": "加权成本不升(全并发不改调用数,仅改时序)",
    "speed": "L4 段墙钟 P50 改善≥30%(未达即回滚,改善<10% 视为失败)",
    "arch": "tushare RATE_LIMIT 次数 ≤ 滑窗基线;信号量 stale 回收 0 次误杀"
  },
  "rollback": "scan_config.jsonc 设 budgets.concurrency={tushare:4,web_fetch:4,web_search:4,l4_stock:4}(_normalize_caps 要四键全给,只写 l4_stock 会 ValueError)。只压 L4 派发帽,不恢复旧滑窗补派节奏——那是 SKILL.md 步骤 4 的主会话行为,config 管不到;真要回滑窗须同时 revert SKILL.md 步骤 4 与 STAGES.md L4 节。〔final-review 2026-08-08 C5 修正:原文「config 一行,回滑窗」与实施后 SKILL.md:103 的文案矛盾且拉不动〕"
}
```

---

## 批D Skill 描述优化 + 废弃清理

### D1 六个 skill 的 description 重写(新文案定稿,实施时原样落)

统一骨架:一句定位 → 触发短语(中英)→ **反触发**(该去哪个兄弟 skill)→ 被谁调用
(编排关系)。frontmatter description 是路由器,正文才是操作手册——描述里不再塞机制细节。

| skill | 新 description 要点(相对现状的 delta) |
|---|---|
| `scan-market` | 保留现有触发句;**删**「零付费 API」重复修饰(CLAUDE.md 已述);加反触发「持仓单票复核→stock-research lite;昨日报告复盘→scan-retro」;加「产物=reports/scan/<run_id>/」一句 |
| `stock-research` | full/lite 路由句提前到第一行;加「lite 档被 scan-market L4 与哨兵持仓复核调用」;删 analyze-ticker 合并史(留 git) |
| `macro-research` | lite=市场研判的被调关系已清楚;加反触发「单行业景气→sector-research」;删「原首席策略师」括号考古 |
| `sector-research` | 加「brief 两段契约(地形段喂 L3/L4,研判段仅 L5)」一句——这是它最常被误解的边界;触发词补「申万一级」别名 |
| `scan-retro` | 拆两句:快环 t1(D+1)/慢环 retro(D+2);**加批量补诊断模式触发词**(「补复盘欠账」,承批A A7);删已退役 L3.5/观察单余句 |
| `feedback` | 现状最清楚,微调:加「裁决 pr_ 提案也走这里」一句(治审计稿病灶⑥的入口可见性) |

### D2 SKILL.md 瘦身与退役标记处置

- `scan-market/SKILL.md` 24KB:操作主线(流程 6 段+直播契约+铁律)目标 ≤14KB;
  搬迁对象=沿革叙事/参数快照/实测读数 → STAGES.md(49KB,本就是机制档案位)。
- 全仓 22 处退役标记(`已退役/已移除/勿再跑`)三分法处置:
  ①**防复发墓碑**(观察单日检勿再跑/TTL 复用 R5/L3.5 已并——有人会手贱加回来的)留原位;
  ②**纯沿革**(OTEL 退役、models 园区移除等)删句留 git;
  ③**引用已死符号的活指令**(若有)= bug,逐条改。实施时逐处登记处置表,不凭手感。

### D3 死码清单(2026-08-06 勘误后;删除归实施批)

> **勘误(2026-08-06,出计划时协议第③步自逮)**:首版本表列 6 个"零引用"模块,但
> `nightly_close._ledgers` 用 `importlib.import_module(f"autoresearch.learning.{n}")`
> **字符串拼名动态调用**,`gate_recal`/`l3_marginal`/`sentinel_audit`(还有
> `l3_l4_alignment`)都在它的 names 表里 —— 每晚都在跑,是活体。按
> `autoresearch.learning.X` 模式做的 grep 逮不到这种调用面。这正是删除协议③存在的
> 理由,也再次坐实:**"零引用"结论必须过裸名 grep,不是过一种引用模式**。

| 模块 | 生产引用(含 -m 与裸名) | test 引用 | 处置 |
|---|---|---|---|
| `wave10_experiments.py` | 0(裸名复核 ✓) | test_wave10_experiments.py | 待删(连 test) |
| `process_backfill.py` | 0(裸名复核 ✓) | test_process_backfill.py | 待删(连 test) |
| `shrink_replay.py` | 仅旧 plan 文档(裸名复核 ✓) | test_shrink_replay.py | 待删(连 test) |
| `gate_recal.py` | **活体**:nightly_close 动态 import | test_gate_recal.py | **勿删** |
| `l3_marginal.py` | **活体**:nightly_close 动态 import | test_l3_marginal.py | **勿删** |
| `sentinel_audit.py` | **活体**:nightly_close 动态 import + fwd 消费 | test_wave10_ops.py | **勿删** |

删除协议(每模块必过,承 2026-07-19 家训「删退役 test 会静默孤立它顺带锁的 live 契约」):
① 读 test docstring 查双职(test 是否顺带锁了别的活契约);② vulture 四类假阳口径复核;
③ **裸名** `git grep`(getattr/importlib 字符串拼名/launchd plist);④ 删后全测绿 +
一次真实 prelude/nightly 冒烟。附带动作:给 `nightly_close._ledgers` 的 names 表加一行注
「动态调用面——删 learning 模块前先看这张表」,防下一次同样的假阳。

### D4 skill/workflow 文档 lint(product_shape_lint 家族新成员)

- 规则:`.claude/{skills,agents,workflows}` 文本引用「已退役符号表」(observe_watchlist、
  scan.progress、l4_reuse --apply、stable_context_blocks、sector_brief_mode、redteam_prob …
  实施时从各 wave 退役清单汇总)→ lint fail;workflow js 调用点内联 model/effort 字面量
  (B2 之后)→ lint fail。挂进现有 lint 跑批位。

### D5 lite-playbook 卡契约 v4 文案(依赖 A5)

- stock-research `lite-playbook.md` + `.claude/agents/l4-card.md` 的模板段按 A5 改隔夜口径;
  锚测试(`test_l4_prompt_cache_prefix` 家族)重锚一次——**改前先重读两文件**
  (skills-altitude 家训:skill 文档会被外部改,编辑前重读)。

---

## 风险与对冲(跨批)

| 风险 | 批 | 对冲 |
|---|---|---|
| 换尺后历史结论符号翻转被旧记忆误引 | A | A8 对照报告「作废/待重验清单」;memory 主尺条目已改(08-05) |
| gap 尺样本噪声更大(隔夜窗短) | A | IC clip 收窄 + 两半符号一致门不放松;首月权重变化幅度观察行进 changelog |
| 卡契约 v4 与旧卡混读 | A5 | SCHEMA_SWITCH_V4 单点分界(承 v3 已验证手法) |
| role 闭集漏列未来新 agent | B1 | raise 消息附闭集清单,加 role=改一行;好过静默 |
| 全并发打爆 tushare/WebSearch | C | tushare 信号量 4 槽不变;429 属 TRANSIENT 可重试;C4 观测阈值 |
| 全并发 N 大时本机/harness 压力 | C | dispatch_cap=64 仍是数,不是 ∞;首跑 N=10 实测后再议 30+ |
| skill 文本被并行会话改写 | D | 编辑前重读;lint(D4)保底 |

## 验收总表(实施批照抄)

- A:变异探针(错腿/翻符号)红 → 绿;parity commit byte 一致;抽样复算 3 日 5 票;
  changelog 出现 label_col 新值;两尺对照报告落盘;全测绿。
- B:role 拼错 raise;变异 config 被 reconcile 逮住;空 cfg throw;js 解析探针绿;
  真实扫描一次 CP7 对账表全 ✓。
- C:registry PREREGISTERED 记录在案;首跑 N=10 墙钟/错误率/完成率三数对照基线;
  task book 全 SUCCEEDED;评级产物 schema 零错误。
- D:六 description 落稿;退役标记处置表 22/22 登记;死码六模块按协议删净后全测绿;
  lint 上线且对已知退役符号能红。
