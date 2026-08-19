# Wave10 设计稿 —— 报告运营优化 × 退役瘦身 × 0买归因

> **状态:二次评审修订稿,零实施,待用户复核**(2026-08-01 首稿 + 同日 implementation-readiness / 0买实验复审)。
> **调度权威仍由本稿承接**;Wave9 批 C/D/E/F(期权双层/自学习章节/trace 重组/单工作流全链)维持原稿调度、不收编,见 §7。
> 沿革:Wave9 批A/B 已上线(`fcadbd1`,2255 绿);本稿基于 2026-07-31 实跑(`reports/scan/20260731_2132`)与闭环账本立案。
> 实施纪律:**Gate 0 先冻结证据口径,再动代码**。立案诊断≠事实;premise-check 未过不得把假设写成修复。0 BUY 不是失败,不得为凑单放松生产门。

---

## 0. 裁定记录

| # | 裁定 | 内容 |
|---|---|---|
| R1 | 文档形态 | 单一 Wave10 统一稿接棒调度权威;Wave9 C/D/E/F 不收编,只交叉引用 |
| R2 | 清理档位 | 激进档:零调用残件 + 活着但无人消费的功能 + 回滚杆评估;回滚杆必须过稳定判据门,证据不够只标记 `RETIRE_ELIGIBLE` 条件,不删 |
| R3 | 0买立场 | 诊断 + 注册制影子实验 + 报告出口;不改生产门。EXP-1 研究「主力 5 日持续性」;FALSE 弃权允许上浮,但必须同时展示分母与 as-of |
| R4 | 继承裁定 | fwd_2_oc 超短主尺·不碰防御/跌势侧轮动·L4 TTL 复用退役·intel cap 数值不重开·实验晋升前零生产副作用 |
| R5 | 证据分母 | 严分 `raw_run / valid_completed / t2_mature / experiment_eligible`;任何比例同时写 numerator、denominator、as-of,不得跨 cohort 拼句 |
| R6 | 报告显著性 | 漏肉要可见,但不得只展示 FALSE 个案而隐藏基率;主报告先给聚合分母,个股 near-miss 默认进附录 |
| R7 | 退役动作 | 运行时程序只产 `RETIRE_ELIGIBLE`,**不得自删源码**;删除由独立可审核 commit 完成。历史 specs 是审计记录,不为通过 grep 而改写 |
| R8 | 实验统计 | 固定成熟门,禁止「10–20 日」可选停止;同日多票按日聚类;同一批候选的 challenger/control 用 paired 口径;缺相位只保持 IMMATURE |
| R9 | 状态事实 | 运行模式、intel 降级、实验 as-of 均须结构化落盘;报告不得从文件是否存在或自然语言卡头反推状态 |

---

## 1. 证据快照与口径冻结

### 1.1 Cohort 账(截至 2026-07-31)

| Cohort | 数量 | 用途 | 说明 |
|---|---:|---|---|
| raw journal rows | 30 日 | 运营历史 | 含非交易日/缺卡/未成熟行,**不得直接作收益或 0买比例分母** |
| T+2 mature (`zero_buy_ledger`) | 26 日 | 市场层弃权背景 | 20 个 0买日 + 6 个买日;0买日市场 fwd_2 均值 −0.96% |
| abstention v2 mature | 8 日 | 逐票因果弃权 | CORRECT 0 · FALSE 3 · NEUTRAL 5;8/8 均 DEGRADED |
| paper NAV | 真实 9 笔 / 影子 81 笔 | 组合政策对照 | 真实 −0.24% vs 影子 −3.16% vs sized −5.86% vs 市场等权 −9.85% |
| 主力真在门 | 15 日 / 63 次 | 门级审计 | mean excess2 −1.33%;`excess2<0` 36/63=57.1%;`0≤excess2<+2pp` 7/63=11.1%;`excess2≥+2pp` 20/63=31.7% |

- journal 的“30 日”与 mature ledger 的“26 日”不是同一分母。首稿的“30 日、23 个 0买日、6 个买日”混用了 raw 与过滤后 cohort,本稿撤回该写法。
- gate ledger 现有 **39%** 是被拦票 `fwd_2_oc≤−5%` 的左尾保护率(收缩展示),**不是错杀率**。本稿统一把 `excess2≥+2pp` 定义为 `FALSE_KILL`;31.7% 只是本次复核重算的迁移基线,须由 A11 正式落账后才可进入实验注册表。
- paper NAV 的“真实−影子≈+2.9pp”只能称**当前组合政策的观察差**,不能归因到某一根门柱;真实/影子交易数与暴露不同。
- 当前连续 0买运营 streak 为 07-15 起 10 个扫描日;它是运营提示,不替代 mature cohort。
- L3 既有面板:finalists 相对菜单 −0.39pp/2日,t≈−1.2;板块动量 IC +0.1155,12/17 日正,t≈+1.46。二者均未显著,只支持开影子,不支持生产改权。
- 追当日大涨已证伪:07-21 当日 ≥9.5% 的 350 只 fwd_2 超额 −3.67pp(t=−11.9),科技 170 只 −4.85pp(t=−13.6)。
- 目标价全卡历史:成熟 n=36,触达率 39%,中位目标 +6% vs 中位 MFE +3%。但 `hi_2_oc × regime` p60 锚及“超锚需硬理由”**已经接入**;EXP-3 不再假装这是尚未实现的功能,见 C2。

### 1.2 2026-07-31 实跑切面

- 哨兵判「材料枯竭」(全市场健康上涨 1.9% <3%),被 `force_full` 拉满:10 finalist,Hold 5 / UW 5,0 BUY;成本 $34.48 / 113m44s。
- 停因:早停 4(资金流出 2/题材透支 2)·满卡未达 OW 6;7 张可解析卡的三门失守:主力真在 ✗4 ·业绩 ✗1 ·估值 ✗2。
- 菜单体检:L2 落刀面 74% vs 全市场 47%;健康上涨 15/203(全市场 80/4103)。
- 920179:ensemble `[Underweight,Sell,Sell]`,median Sell,trigger=sell_review,spread=1。单向阀按设计不把持仓评级变得更悲观,但报告零痕迹。
- price_claim 假阳:把「主力占比 3.7%」「板块存储指数 +6.6%」识别为本票股价断言。
- market_view 防锚定连续两日复发;根因已验:策略师可直接读取 full pack 的 `sector_healthy_top3`。
- intel cap 复核纠错:000651 自报 39 条**已触发 `TRIMMED`**,canonical 稿头有裁决戳;因可解析事件行本就 ≤10,`dropped_rows=0`,按设计不生成 `.pretrim`。真实缺陷是:
  1. 角色定义仍硬写 ≤15,workflow 又注入配置 cap(当日 20),存在双事实源;
  2. workflow 只显式播 `REJECTED/unreported`,不播 `TRIMMED`;
  3. report/T1 无结构化 intel 状态。
- 网络瞬时错 ENOTFOUND ×3;presence-gate 让任务完成,但两张卡情报面变薄且报告不可见。
- Wave9 两项运营债仍在:公告兜底已真接线,但串行无缓存约 +5min;插队建档已对消费者可见,但回执仍需区分“写入请求数/新增可见数”。

### 1.3 Gate 0 · 证据清单

实施第一个 commit 只做 A0/A11 证据冻结,不改报告与生产行为。Gate 0 完成条件:

1. 生成 `context/scan/<date>/evidence_manifest.json` 与跨日 `reports/learning/wave10_evidence.json`;
2. manifest 每个指标含 `value/numerator/denominator/cohort/as_of/source_paths/source_hashes/query_version`;
3. 复算 §1.1 并让 Markdown 由 manifest 渲染,手抄数字 diff 必须为零;
4. 正式落 `CORRECT/NEUTRAL/FALSE_KILL/UNMEASURED` 门归因;EXP-1 不得在此之前注册;
5. registry inventory 入 manifest:当前已有 `exp_20260729_l3_hard_constraint_f`(PREREGISTERED,l3_prompt),后续 L3 对齐研究不得绕开它另开冲突实验。

---

## 2. A 节 · 报告、运行契约与运营优化

> 通用验收:确定性函数配单测;workflow/呈现层必须用真实历史产物回放。测试要同时证明“能 catch”与“不会误 catch”;不能只看测试总数绿。

### A0 · Evidence Manifest(P0,新增)

- **目的**:消灭手抄分母、过期快照与指标误读。
- **设计**:新增纯确定性 evidence builder,只读 journal/zero_buy/abstention/paper_nav/gate/buy ledger 与 registry,输出 §1.3 契约。Markdown 证据表从 JSON 渲染。
- **边界**:不重算底层收益;只做既有账本的 cohort 对齐与来源指纹。源之间冲突→`status=CONFLICT`,拒绝生成结论句,但不阻断日常 scan。
- **验收**:注入“39%=错杀率”映射时 schema/semantic test 变红;raw 30 与 mature 26 不得落在同一 denominator 字段。

### A1 · sell_review 分歧透明化

- **设计**:不改 `_apply_ensemble_fold`。当 `lane=pinned ∧ trigger=sell_review ∧ median≠card_rating`,持仓表与 detail 卡头显示:
  `⚠️ 持仓保护规则:复核中位 Sell 比卡面 UW 更悲观;单向阀未加重终评`。
- **措辞**:不写“未折回”这种像 bug 的表达;明确这是持仓保护规则的有意行为。
- **落点**:`decision_finalize` 产结构化 dissent record;`report_sections/publisher` 只渲染。非 pinned spread=1 仍不出人裁行。
- **验收**:07-31 回放 920179 出行;普通票 spread=1 静默;卡面/median/终评三字段均取结构化事实。

### A2 · 哨兵中间档 `SENTINEL_PINNED`

- **四态事实源**:`run_mode.json(schema_version=1)`:
  - `FULL`;
  - `FORCED_FULL`;
  - `SENTINEL_EMPTY`;
  - `SENTINEL_PINNED`。
- 每条记录含 `sentinel_reason/pinned_snapshot_hash/has_selection_conclusion/has_l3_judgment/created_at/contract_hash`。assemble/GATE4/report **只读此文件**,不得从 finalists 是否为空反推。
- `SENTINEL_PINNED` 判据:sentinel ∧ frozen run_contract 的 pinned.kept 非空 ∧ 未 force_full。跳行业判断 brief、L3 和非持仓 L4;运行**pinned L4 全链**(不是 L1→L5 全链)。
- 新 helper 从 frozen pinned snapshot 取码,用当日 L2 行补 name/sector/确定性字段;缺行则占位并 `data_missing=true`;写 pinned-only finalists,每行 `lane=pinned,selection_source=sentinel_pinned,l3_judged=false`。不得读取跑后可能变化的 `pinned.jsonc`。
- GATE2 写 `SKIPPED_NOT_APPLICABLE(reason=sentinel_pinned_no_l3)`;GATE3/streaming task-book 只验 pinned L4 负责的 prompt/slim/card。GATE4 在 `has_selection_conclusion=false` 时不要求 buy-list,但仍要求持仓卡完备。
- 报告醒目标注「哨兵档:仅持仓复核,无全市场选股结论」。pinned 卡使用与 FULL 相同的 l4-stock workflow、rubric 与双复核;prompt 因明确缺少 L3/判断型行业 brief 而不会字节相同,此差异须由 `run_mode` 与 `l3_judged=false` 公开,不得伪装等价上下文。
- **验收**:四态桌演;中间档 `_l4_tasks.json` 只含 snapshot pinned;跑后修改 pinned 配置不改变历史报告;缺 L2 行仍诚实降级。

### A3 · price_claims 高精度且可量 recall

- **设计**:先做 subject 分类,再抽数。仅 `subject=own_stock_price` 进对账;占比/净比/指数/板块/行业资金/仓位/持股比例明确排除;转述否定保留现有豁免。
- 不确定语境记 `UNKNOWN_SUBJECT` 计量,不告警,但不得静默丢弃。每跑输出 `n_candidate/n_own/n_excluded/n_unknown`。
- **验收 corpus**:Gate 0 先用近 10 日回放冻结 unknown-rate 基线;07-30/31 假阳静默;所有已知真阳仍 catch;注入真错价必 catch;live unknown rate 连续 5 日不得高于冻结基线 +5pp。

### A4 · strategist pack 数据级防锚定

- full `market_pack.json` 保持 L5/validator 事实源不变;从它单向投影 `strategist_pack.json`。
- allowlist 仅含:`breadth/money/valuation/regime/temperature/cross_money/index_val/macro_state/macro_state_note/today_slice/sectors`。`sector_healthy_top3/run_contract/user_config` 及未来新增字段默认拒绝进入。
- projection 带 `source_hash/schema_version/allowed_keys`;macro-brief workflow 只获 strategist path,不再获 full path。
- **验收**:forbidden key 注入测试;market_view 连续 5 个扫描日无 top3 泄漏;L5 top3 节与改前 diff=0;L3 数字 validator 继续读 full pack。

### A5 · intel cap 单一事实源 + 裁决可见

- 保留用户裁定的 cap 数值,不重开阈值。删 agent definition 中固定 `≤15` 数字,角色只保留“以 runtime max_queries 为唯一上限”;冻结的 `user_config_echo/run_contract` 是 cap 事实源,workflow prompt 只运输该值,不得另设默认真值。
- `intel_guard` 返回统一结构化 `IntelStatus`。状态拆成三条正交字段,避免把“取到了稿”“稿被守卫裁过”“卡最终读了什么”混成一个枚举:
  - `acquisition=FULL/RETRIED_FULL/DEGRADED/DISABLED`;
  - `guard=NOT_RUN/KEPT/TRIMMED/REJECTED/ABSENT`;
  - `availability_for_card=INTEL/CARD_FALLBACK/NONE`。
  附带 `claimed_queries/runtime_cap/hard_cap/dropped_rows/pretrim_ref/attempts/error_class`。
- `guard=TRIMMED,dropped_rows=0` 文案为「触发超硬顶审计,事件表已在上限内,未删事件行」,不得伪装成“真裁了内容”。
- status 写进 task-book 的 intel 子记录,直播、报告、T1 共用;不再解析稿头猜状态。
- **验收**:07-31 000651 回放得到 `TRIMMED/0`;agent prompt 全链只出现一个 runtime cap 数值;报告字节经济行能看到裁决。

### A6 · intel 瞬时错重试 + 结构化降级

- intel 腿对 `RATE_LIMIT/CONNECTION/TIMEOUT/ENOTFOUND` 最多 2 次;指数退避只在 harness 可表达范围内,不阻塞其它票。
- 终失败→`acquisition=DEGRADED,availability_for_card=CARD_FALLBACK`,记录 `attempts/error_class/fallback=card_websearch`;卡头与 summary 由状态渲染「🕳️ 情报面降级」,不是让 LLM 自己写。
- `DISABLED` 与 `DEGRADED` 分开;主动关功能不是事故。
- T1 以 status 分桶比较 FULL/DEGRADED 卡的错判率;样本不足只展示 n。
- **验收**:ENOTFOUND→第 2 次成功=`acquisition=RETRIED_FULL`;两次失败=`DEGRADED/CARD_FALLBACK`;非瞬时 SCHEMA_ERROR 不重试。

### A7 · intel 旧事件净分确定性归一化

- 角色契约已经要求衰减;本项不是新增判断规则,而是把 lint 变成 card 消费前的 deterministic normalizer。
- `gap>7 自然日 ∧ window≠催化挂 ∧ score≠0`→canonical 稿净分改 0;原始行写 `shadow/intel_normalization_<code>.json`,含 before/after/reason/source_hash。
- `催化挂` 只认结构化时效窗字段,不从正文猜“中报/投产”等词。日期坏/窗未知→不改分,标 `UNMEASURED`。
- **验收**:07-31 300857 旧事件归 0;明确 `催化挂` 不衰减;撤 normalizer 后 stale guard 变红。

### A8 · l4_watch consumer cursor

- task-book 仍是任务状态唯一事实源;新增 watcher 自己的原子 cursor:`outbox/l4_watch_cursor.json`,记录 `consumer_id` 与已播 terminal event id。
- 重启只播未确认事件;`--replay-all` 才显式重播。cursor 损坏→报错并要求人工选择 replay,不得静默当空。
- **验收**:杀进程重启只播增量;两个 consumer_id 互不干扰;task-book 字节不因播报改变。

### A9 · Wave9 运营债转正

1. 公告兜底 fetch 走 lake 正缓存 + 有时限的负缓存,或 bounded concurrency;分析日/source/version 构成 cache key。目标 L3 附加耗时 <1min;失败继续 B级降级留痕。
2. 建档回执分三数:`requested/inserted/newly_visible`;报告只把 `newly_visible` 称“新增可见”。

### A10 · 欠账 SLO

- retro 备料 >48h 未收尾升 prelude 红行。
- dossier 不以“某日绝对清零”为成功条件;两周运营目标改为:`oldest_pending_age≤48h ∧ overdue_count=0 ∧ 7日消化数≥7日新增数`,帽 ≤3/晚不变。
- 20251231 对账债单列 `reconcile_overdue`,不与 pending_init 混数。

### A11 · Gate Attribution v3(P0,新增)

- 对每个被绑定门的可交易候选统一落:
  - `CORRECT`:excess2<0;
  - `NEUTRAL`:0≤excess2<+2pp;
  - `FALSE_KILL`:excess2≥+2pp;
  - `UNMEASURED`:缺 T+2/不可交易/事实坏。
- 记录 `gate/candidate/date/fwd2/market_fwd2/excess2/tradable/cohort_version`;多门票单列 `MULTI_GATE`,不得重复算进三个单门分母。
- gate ledger 保留 mean/left-tail,新增 outcome 分布。EXP-1 与 C3 只读本契约。
- **验收**:migration report 先复现 legacy 主力门 36/7/20(总63),再单列 v3 去重后的 single-gate/MULTI_GATE 新口径;两者不得冒充同一序列。EXP-1 只使用 v3 成熟样本;legacy 对不上时 Gate 0 阻断注册并输出 diff。

### A12 · Sentinel 校准账本(新增)

- 每个 sentinel 日落 `sentinel_audit`:`reason/run_mode/cost_saved_estimate/pinned_n/L2_top_shadow_excess2/market_fwd2/next_regime/false_negative_status`。
- false-negative 只认预注册 shadow scope 内可交易候选 `excess2≥+2pp`,不看全市场任一赢家。
- 目标是形成 cost–miss frontier,验证 sentinel 是否既省钱又不漏掉系统本会买的机会;不自动改 sentinel 阈值。

---

## 3. B 节 · 退役瘦身

### B0 · 删除协议

每个 retirement family 一个 commit,按以下顺序:

1. 引用分层:`production py / tests / workflows / active skills+CLAUDE+README / historical docs`。历史 docs 允许保留命中,只需在审计清单标 `HISTORICAL_REFERENCE`。
2. 将删 test 先查是否兼锁活契约;活契约迁入存活 test 后再删旧 test。
3. 判身份:`DEAD / ABANDONED / ROLLBACK_LEVER / LIVE`。只有前两者能立即删;回滚杆先到 `RETIRE_ELIGIBLE`。
4. 删除审计写 `docs/research/wave10-retirement-audit.md`:引用清单、身份依据、历史 run 证据、相邻守卫清单。
5. 删后全测 + 两个相邻活守卫的定向 mutation test。commit message 链接审计节,不塞整份 grep 输出。

### B1 · 零调用残件

| 残件 | 复核结论 | 动作 |
|---|---|---|
| `autoresearch/scan/l4_reuse.py` | 生产调用已退役;workflow 只余历史注释 | 查 test 双职后删模块/tests;清 active docs/config 的 reuse 入口,历史 specs 保留 |
| `reuse` config 白名单 | 只服务已退役 L4 reuse | 删 `user_config` 白名单/ScanConfig 字段/jsonc 注释 |
| `redteam_prob` | 全仓仅定义/白名单/注释,零消费 | 删字段与 active config 注释 |
| `pinned.cap/ttl_days` | **LIVE**:`frame` 真读取并写 run contract | **保留,撤回首稿删除提议**;补消费链测试锁住 |
| OTEL 注释残迹 | 历史措辞 | 只清 active 注释;历史设计文档不动 |

### B2 · 观察单残件族

- 退役令 fb_20260714_002 不变。逐文件分类 `DEAD_READ / BACKWARD_COMPAT / LIVE_OTHER_PURPOSE` 是第一交付物。
- journal 恒 0 的“触发”列从新报告 schema 删除;历史 CSV/Markdown reader 继续兼容旧列,不回写历史。
- `sector/pack`、retro、self_review 等若只是兼容历史产物,把理由写进 retirement audit,不为追求零 grep 强删。

### B3 · earlystop_shadow 整族退役(首稿开放问题已关闭)

- 事实:当前 scan root 零 `shadow/earlystop_queue.json`;`write_shadow_queue` 只有 CLI/tests 调用,无生产 producer;账本 0 reviews。
- 判定:`ABANDONED`,不是“队列有货没人取”。删除 module、独立 workflow、artifacts/health/post_run 接点与专属 tests;若某 test 锁住通用原子写/采样契约,先迁移。
- 历史设计与 commit 保留;lessons 写明“消费者与账本接了,producer 从未接线”。不新增自动派发来拯救沉没成本。

### B4 · 未启用路径的身份重判

- `stable_context_blocks=true` 先跑零 LLM 离线 benchmark:比较 prompt 事实块等价、共享前缀字节与 manifest 完整性。预估节省 <10% 或事实不等价→`ABANDONED` 后删;≥10% 且等价→注册 performance experiment 并给到期日,不得永久默认 false 无人问。
- `sector_brief_mode=finalist_only` **不是纯性能开关**:它让 L3 看不到原本的判断型行业 brief,可能改变 finalists,不能靠 dispatch 数离线证明评级等价。按“性能开关不拥有评级”铁律把它移出 performance:
  - 无用户批准的 research experiment→标 `ABANDONED` 并删;
  - 若要保留→必须作为 research/decision experiment 注册,用真实双跑验证 finalist overlap/T+2/成本,不得以“省 brief 数”单独晋升。
- 本门不允许“继续维持默认 false”作为第三个选项。

### B5 · 回滚杆到期协议

| 回滚杆 | `RETIRE_ELIGIBLE` 判据 | 到期动作 |
|---|---|---|
| `streaming_l4=false` 旧批量 GATE3 | 流式路径累计 10 次真实扫描,`structural_failure_n=0`;结构失败定义固定为 task-book 丢失/错误重跑成功票/完成态误判/产物 hash 失配 | health 标 eligible;独立 commit 删除旧路 |
| `_ensemble.json` 旧批量双读 | 与旧批量路径同生死 | 同 commit 联动删;历史文件仍可由离线 migration reader 读取,生产 reader 不背永久兼容 |
| abstention v1 | v2 达 10 个 mature day 且两日重跑一致 | ledger 标 eligible;独立 commit 删除 v1 生产列/代码,保留历史报告 —— **已拔（2026-08-19，v2 19/19 成熟 + 两日重跑一致）**:2026-07-16/2026-08-06 两天各重跑两遍 `abstention_ledger`,`reports_claude/learning/abstention_ledger.md` 对应行与全文逐字一致(diff 为空);19/19 全部成熟裁决(FALSE×5、NEUTRAL×14)。`render()` 的 v1 headline 与 `recall_ceiling_n` 右侧诊断列已从 `abstention_ledger.md` 退役;`status`/`recall_ceiling_n` 字段与计算本身**未删**(`scan/market.py`/`scan/health.py`/`learning/zero_buy_ledger.py` 三个下游消费点仍直接读取),历史报告未改写 |

代码不得在第 10 日 assemble 后自行修改/删除源码。

### B6 · event 召回路本波不动

- pr_20260725_001 继续按既定 `plus_event` 证据窗;到期为负→退役+负结果写 lessons。它与 EXP-2 使用相同 channel_audit 仪器,但 family/variant 分开,不得混样本。

---

## 4. C 节 · 0 BUY 归因、实验与报告出口

### C1 · 当前能说什么、不能说什么

**可说**:在当前以 risk_off 为主、且 mature 数据有限的 cohort 中,整体弃权政策优于“门不拦的 shadow 组合”;系统的已证能力是避坑,抓肉能力未证。

**不可说**:“0买已被证明是市场导致”或“某一根门贡献 +2.9pp”。现有数据缺少足够 risk_on 相位,且真实/影子暴露不同,无法把地形与门的因果贡献完全识别。

四层诊断保留,但全部按证据强度标注:

1. **地形层(强相关,未识别因果)**:mature 0买日市场 fwd_2 −0.96%;risk_on 缺样本。
2. **菜单层(待实验)**:L1 无正式板块动量路、L2 sector-neutral;板块动量量级大但统计未显著→EXP-2。
3. **判断层(已见目标错位,仍需结构化账)**:L3 使用 20日 cmf/obv 与当日 main_net,L4 要当日真金;07-31 4/7 倒在主力门。先建 L3→L4 alignment ledger,不直接改 L3。
4. **尺子层(部分已验)**:fwd_2_oc 主尺固定;追涨证伪;目标价 p60 锚已接线,需要新 cohort 验证而非重复实现。

### C2 · 实验治理与预注册

#### C2.0 共用纪律

- control/challenger 绑定同一 `run_id/candidate/date`,用 paired delta;统计按 date cluster bootstrap。
- 全部特征必须 PIT/as-of;报告日 D 不得读 D 之后成熟结果。
- 凡进入 registry 的 challenger 必须填满现行五 guard domains(`research/decision/token/speed/architecture`)与 `minimums(forward_days/mature_events/unique_events/regimes)`;本节表格是业务契约,实现时生成的 JSON hash 写回 evidence manifest。EXP-0/3 是 monitor,使用版本化 monitor schema,不伪装成可 activate 的 registry experiment。
- `10–20日` 这种浮动停止删除;到固定 minimum 仍缺 regime/unique events→IMMATURE。
- 当前 registry 已有 `exp_20260729_l3_hard_constraint_f`(PREREGISTERED,l3_prompt)。它关闭/激活前,不得新开会改变 L3 prompt 的 family。

| ID | 类型 | Challenger 定义 | 固定成熟门 | 晋升主判据与守卫 |
|---|---|---|---|---|
| **EXP-1** `ow_gate_mainflow5d` | 影子 gate | `sum(main_net_yi,T-4..T)>0 ∧ positive_days≥3 ∧ main_distortion=false`;control=当日口径,T 表示分析日且区间严格为五个交易日。数据从 moneyflow lake 分区取,缺任一日→UNMEASURED,assemble 不联网补 | forward_days≥20,mature_events≥50,unique_events≥50,regimes≥2 | primary:`false_kill_rate_delta≤−3pp`;guard:`correct_block_rate_delta≥−3pp`,`mean_excess2_delta≤0`;architecture diff=0;研究不通过即记负结果 |
| **EXP-2** `recall_sector_momentum` | 影子 channel | 上涨侧 sector momentum channel;floor=0、不占 quota、不写生产 finalists,只落 per_channel 长表 | forward_days≥20,mature_events≥50,unique_events≥50,regimes≥2,且每相位≥5日 | primary:date-cluster bootstrap 的 `unique_excess_t2_lcb>0`;guard:left-tail 不恶化、生产 artifact diff=0、token/speed 增量≤5% |
| **EXP-3** `target_calib_postcheck` | **既有功能验收,非新 challenger** | 现行 hi_2 × regime p60 锚 + 超锚硬理由;只收集接线后的新卡 cohort | mature_events≥60,forward_days≥20,regimes≥2 | 观察 calibration error/触达/中位目标幅。若 calibration error 未改善,再另立 challenger;本波不重复实现现有功能,不注册可激活实验 |
| **EXP-0** `regime_flip_monitor` | prospective monitor | regime 首次从 risk_off 转 range/risk_on 后,冻结前后对称窗口;记录 candidate_n、三门通过率、shadow excess、BUY n | 每侧≥10 个 scan day 且每侧完整卡≥50;不足自动延长,不选好看的第5日停 | 不产 RECOMMENDED。BUY>0 只作描述;结构问题升格需“通过率无提升 + shadow 有 FALSE_KILL”共同成立 |

#### C2.1 L3→L4 alignment 诊断(新增,先量后试)

- 新账本逐票记录:`L3 rank/finalist/conviction/main_net_1d/cmf20/obv20/main_dist/sector_state → L4 first_rejection/gate outcomes → excess2`。
- 20 个 mature day 后回答:高 L3 排名是否显著提高 L4 三门通过率与 T+2 excess;主力门错杀是否集中于某种 L3 画像。
- 只有当“misalignment 稳定存在 ∧ 某个可预注册 challenger 有独立 T+2 edge”时才设计 EXP-4。不得以“提高过门率”为目标函数;过门率上升但 excess/左尾不改善=失败。
- EXP-4 若涉及 L3 prompt,必须先处置现有 `exp_20260729_l3_hard_constraint_f`;若只做确定性 shadow rerank,也要独立 family 与全五域 guard。

### C3 · 报告出口(不改门,控制显著性与前视)

1. **0买日 summary 主体只放聚合**:
   `🎯 差一点:shadow top3 中 主力门2/业绩门0/估值门1;历史同口径截至 D-2:FALSE_KILL x/n`。
2. 个股 top3 放「影子观察附录」:逐只门柱读数、EV/R:R、历史战绩。固定标注「未过门,非建议;shadow 整体仍输真实线」。主报告不把 near-miss 与正式 buy-list 排成同视觉层级。
3. 所有“历史战绩”查询强制 `mature_date < report_date`;历史回放不得读取未来 ledger。撤输入→显式 `UNMEASURED`,不静默消失。
4. abstention v2 出 FALSE 后,在**首次可用的下一份扫描报告**上浮,不写死“次日/昨日”(T+2 成熟可能跨休市日):
   `⚠️ 最近成熟弃权 FALSE:信号日 07-21·600188·excess2 +2pp;滚动成熟窗 FALSE 3/8,NEUTRAL 5/8,CORRECT 0/8`。
5. CORRECT/NEUTRAL 不逐案刷屏,但分母始终同屏;避免只看漏肉形成绕门冲动。
- **验收**:07-16/17/21 信号日分别验证 near-miss 附录;FALSE banner 在其首次成熟后的报告出现;as-of mutation 注入未来记录时守卫变红;非0买日无 near-miss 节。

### C4 · Sentinel cost–miss 出口(新增)

- 周报展示 `sentinel_days/cost_saved/false_negative_n/UNMEASURED_n` 与最近四周趋势。
- 只有 A12 达固定成熟门后才允许提 sentinel 阈值实验;单日 07-31 的 $20/~70min 仅是容量估算,不作为长期收益承诺。

---

## 5. 实施轨道、依赖与完成态

| Track | 内容 | Build 量级 | Build DoD | Maturity DoD |
|---|---|---:|---|---|
| **0 证据地基** | A0+A11+registry inventory | 0.5–1天 | manifest/归因 schema + 历史回填 + 数字 diff | 不需等新扫描 |
| **A 报告/状态契约** | A1–A8+C3 | 2–3天 | 单测+07-31/历史 FALSE 回放+四态桌演 | A4 连续5个 live day;其余一次 live smoke |
| **B 退役** | B0–B5 | 1–2天 | retirement audit + 每族独立 commit + 全测 | 回滚杆未到期只标 eligible 条件,不阻塞本 track |
| **C 实验仪器** | EXP-1/2 数据面+EXP-0/3 monitor+alignment ledger | 2–3天 | EXP-1/2 registry specs/hash;EXP-0/3 monitor schema;零生产 diff;历史可回填部分 | 20+ forward days、50/60 events、2 regimes;可能数周至数月 |
| **D 运营** | A9/A10/A12/C4 | 1–2天+持续运营 | 性能/回执/SLO/账本接线 | 两周 SLO + sentinel 固定窗口 |

依赖:

1. Track 0 是 EXP-1 与 C3 gate history 的硬前置;
2. A2 依赖 `run_mode`/frozen pinned snapshot;A1 与 C3 共用 report sections,按 A1→C3 顺序降低冲突;
3. A5/A6/A7 共用 IntelStatus,同一批落地,不拆成三个互相解析自然语言的实现;
4. EXP-2 与 event variant 共用仪器但不共样本;EXP-3 只监控既有 p60;
5. B4 benchmark 先于删分支或注册性能实验。

---

## 6. 验收纪律

- **事实门**:设计里的基线数字必须能由 evidence manifest 再生;手抄差异=失败。
- **状态门**:run mode/intel status/experiment as-of 必须结构化;报告文本不是事实源。
- **历史回放门**:使用 source date 的冻结配置与 as-of,禁止读当前 pinned 或未来 mature ledger。
- **实验门**:registry 先 register spec 再收 challenger 数据;definition hash 改变必须新实验 id,不能边跑边改。
- **统计门**:固定 minimum;paired + date cluster;多相位缺失=IMMATURE,不是放宽门。
- **删除门**:按 B0 审计;历史 docs 不做零 grep KPI;运行时不自删源码。
- **真实冒烟门**:workflow/呈现至少一次真产物 smoke;probe 的 false-positive/false-negative corpus 同时通过。
- **工作树门**:每个 commit 只含本任务文件;用户已有未提交改动不纳入。

---

## 7. 非目标

- 不改 OW 三门生产口径、不改 fwd_2_oc、不碰防御/跌势侧轮动、不重开 intel cap 数值;
- 不动 Wave9 C/D/E/F 内容与调度;
- 不做港美股、盘中或付费 API;
- EXP 晋升前不改生产 floor/quota/ranking/prompt/评级;
- 不把提高 BUY 数、过门率、目标触达率单独当成功;必须同时满足 T+2 与风险守卫;
- 不为追求“仓库零引用”改写历史 specs/报告。

---

## 8. Gate 0 premise 裁决表

| 原开放项 | 二次复核结论 | 实施动作 |
|---|---|---|
| A5 计数量纲/裁稿接线 | 同为声明行网查数;warn cap 与 hard cap 阈值不同。07-31 已 TRIMMED/0,无 pretrim 符合现契约 | 按 A5 收单一 cap + 状态可见,不再查“是否接线” |
| EXP-3 hi_2 锚 | 已实现 regime 分组 p60、逐卡注入、超锚需理由 | 改为 postcheck monitor,不重复开发 |
| B2 观察单分类 | 尚需逐文件身份分类 | 作为 B2 第一交付物,不是开放设计选择 |
| B3 earlystop queue | 当前 scan root 零 queue;producer 仅 CLI/tests,未进生产 | 按 ABANDONED 整族退役 |
| EXP-1 5日数据 | L1/slim 只有 D 日截面;历史可从 moneyflow 日分区派生 | 建 PIT 5交易日 loader;缺任一分区→UNMEASURED,assemble 不临时联网 |
| A2 GATE2/GATE3 | sentinel_pinned 无 L3,不能伪造通过 | GATE2=`SKIPPED_NOT_APPLICABLE`;GATE3/task-book 只验 pinned L4 责任 |
| B1 pinned cap/ttl | frame 真消费并写 run contract | 保留并补测试,撤回删除 |
| B4 未启用路径 | stable_context 可离线验事实等价/前缀收益;finalist_only 会改变 L3 上下文,不是纯性能 | 前者按 10% 门二选一;后者无获批 research experiment 即 ABANDONED,不得用离线省 brief 数晋升 |
| 现有 L3 experiment | registry 有一个 PREREGISTERED `l3_prompt` 实验 | alignment 先记账;新 prompt 实验必须等其生命周期处置 |

---

_沿革:承接 Wave9(`2026-07-29-wave9-report-depth-options-harness-design.md`,批A/B 已上线)与 07-28 统一总纲;0买归因承接 07-11 漏斗六问、07-12 L3 无 alpha 裁决、07-17 轮动盲区裁定、07-25 Wave4 事件路实证。仅供研究,非投资建议。_
