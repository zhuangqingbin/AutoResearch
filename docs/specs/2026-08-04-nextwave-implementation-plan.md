# 2026-08-04 下一波候选池 · 实施计划与落地记录

> **性质:implementation plan + 落地记录。** 需求源是
> `docs/specs/2026-08-03-scan-next-wave-brainstorm-design.md`(brainstorm 候选池)。
> 用户裁定(2026-08-04 会话):**按该设计稿全部开发完,在 main 分支**——
> 这条裁定取代设计稿里「只落文档、不做开发」的 08-03 裁定。
>
> **本稿不改变调度权威**:总调度权威仍是 Wave10 设计稿;Wave9 C/D/E/F 未清债仍归 Wave9。
> 本波交付的是**候选池的软件实现**,不是它们的研究结论。

---

## 0. 一句话总结

设计稿的 26 个候选全部转成了代码,但**没有一个 B 类候选被激活**。
交付形态是三类:

| 类 | 候选数 | 本波落地 | 说明 |
|---|---:|---:|---|
| **M 测量** | 7 | **7** | 只新增 ledger/报表/探针,已接夜间 |
| **I 数据基建** | 10 | **8** | 消费者默认关闭;未落地 2 件 = F1b(依赖 F1 实盘数据)、F3(能力门未过) |
| **B 生产行为** | 9 | **0 激活** | 8 件 `OPEN`、1 件 `REJECTED`;其中 2 件已备好 PREREGISTERED spec 生成器 |

L3/L4 的 prompt diff = 0;`strategist_pack.ALLOWED_KEYS` 未变;composite 因子组未变;
L2 名单构成未变(`selection_reason` 是新增列,不改选择逻辑)。

---

## 1. 批次与产出

### 批0 · 治理地基(commit `fdc0ae3`)

| 文件 | 对应 | 关键点 |
|---|---|---|
| `common/stats.py` | §5-2/§5-3 | date-cluster bootstrap(重采样单位是**扫描日**);`equivalence_verdict` 返回**三态**,区间跨 margin 只能得 `UNKNOWN`;Beta-Binomial(n=0 返回空不是 0.5);BH-FDR;统一成熟门;expanding P25。**零 scipy**(pyproject 未声明它)。 |
| `research/candidates.py` | §0.4/§6 | 26 条候选 × 继承矩阵 × M/I/B × 成本六栏。缺任一即抛错;B 类标 IMPLEMENTED 却在 registry 查无 family → 抛错。 |
| `learning/experiment_template.py` | §5-1/§5-2 | 十项必填 + 五态裁决(IMMATURE 优先于一切);`lower_is_better` 统一取负再判;**0 BUY 不是放松门的理由**做成 assert。 |

### 批1 · 门审计 + L3 边际(commit `0f8984f`)

- `evidence_manifest`:新增 `gate_participation` cohort(**不是单门因果分母**)、
  `GATE_DEFINITION` 口径指纹 + 反面清单、比率带 Beta-Binomial 区间与成熟度、
  `assert_not_shrink_derived`(收缩来源上裁门路径即抛错)。
- `learning/gate_recal.py`:两个**分开**的实验(证据增强 ≠ 门松紧);影子腿按 mtime 断言
  不写生产产物;`RECAL_INTERPRETATION` 翻译表(模板的 `FAIL` 在本实验里是阳性发现,
  `PASS` 数学上不可达)。
- `scan/l3/triage.py`:`selection_reason`/`selection_detail` + `_l3_pass1_kept.csv` + meta。
- `learning/l3_marginal.py`:tier-1 同 choice set 同 K 同层分布反事实;强制补入行两侧同剔;
  Jaccard 单列不进判据;tier-2 在 UNKNOWN/IMMATURE **且**有分歧时才触发。

### 批2 · L2 O1–O5(commit `69baf32`)

- `scan/l2_slo.py`:winner 定义自带标签;端到端与**条件召回**同时出;K 用实际值、
  pinned 分列;报警线用当日**之前**的 expanding P25。
- O3 半特性清算:`regime_caps` 已建未接线 → 补文档 + AST 探针(生产开始传它而 registry
  无 ACTIVE 实验即变红)。**不接线**。
- `research/replay.py`:`VariantSpec` + `definition_hash` + **独立输出根**。
- `research/l2_grid.py`:网格**只探索不裁决**;守卫破线即 GUARD_BREACH。
- `research/feature_gate.py`:capability → factor_lab → replay → registry 有序状态机。
- O2 守卫:52 周高距离族按**别名全集**查 composite/权重/floor/通道注册表。

### 批3 · 新闻 D1–D4(commit `501976a`)

- `news/catalog.py`:三张表 + PIT(`first_seen_ts ≤ cutoff ∧ available_stage ≤ stage`);
  转载 vs 更正分离;`published_ts` 不参与过滤;selective 范围禁止进市场热度。
- `news/typed_events.py`:多标签 + 生命周期 + PIT 覆盖矩阵;两条假设纪律做成 assert。
- `news/claim_ledger.py`:三段 verdict + 日期焊接;cap 改为**真实 tool telemetry** 预算;
  source precision 在薄样本时不发布比例;红线 `ticket_verdict` 恒 KEEP。
- `news/fulltext.py`:预算按**格式化后的 prompt 实测**;独立 `verify_excerpt`。

### 批4 · 衍生品 F1–F3(commit `22b8750`)

- endpoints/contracts 登记 6 个端点,全 B 级。
- `derivatives/options_lake.py`:强制分页 + coverage 对账 + 分品种面板(**不给 total_pcr**)
  + PCR 语义声明 + 换月调整 + strategist allowlist 守卫 + lead-lag 最小证伪步。
- `derivatives/qvix.py`:末行全 NaN 单列一个字段(1000 指数序列的真实症状)。
- `derivatives/style_spread.py`:期限对不上 → 拒绝出数;主口径先各自归一再比较。
- `derivatives/cb_gate.py`:**BLOCKED_BY_DATA**,四门任一不过即整线阻断。

### 批5 · 闭环债 + 嵌套 probe + §4.5(commit `74a5269`)

- `learning/nightly_runner.py`:锁(stale 回收)/原子心跳/幂等键(含债务快照)/
  交易日历 catch-up/超时/退避/env 校验/run ledger;launchd plist 显式设 WorkingDirectory。
- `scan/gate0.py`:硬闸挂载点,默认 ADVISORY;带 GATE1 勘误;override 过期即失效。
- `research/nested_probe.py`:10 项探针三态账本,`UNTESTED` 不得读成 `PASS`。
- `research/consensus.py`:触发条款 + **会变的量**断言(n 必须周周增长)。
- `dossier/debt_schedule.py` / `scan/temperature_v2_guard.py`。

### 批6 · 接线与文档(本 commit)

- `prelude` 新增 `preflight` 步(GATE0 advisory,跑在 frame/universe 之前);
- `nightly_close` 追加 `gate_recal` / `l3_marginal` / `l2_slo`;
- 本稿 + 设计稿状态回填。

---

## 2. 候选状态一览(以 `research/candidates.py` 为准)

    uv run --no-sync python -m autoresearch.research.candidates          # 渲染报告
    uv run --no-sync python -m autoresearch.research.candidates --check  # CI 门

| 状态 | 数 | 说明 |
|---|---:|---|
| `IMPLEMENTED` | 15 | 软件已落地。**软件完成 ≠ 研究成熟** |
| `OPEN` | 9 | 8 件 B 类 + F1b(依赖 F1 一期实盘数据) |
| `REJECTED` | 1 | O2 52 周高距离族(带四条重开条件) |
| `BLOCKED_BY_DATA` | 1 | F3 可转债(四门任一不过即整线阻断) |

---

## 3. 本波**没有**做的事(显式列出)

设计稿要求的、本波刻意留在 `OPEN` 的:

1. **intel 先读目录**(D1C2)、**L3 公告第二源**(D1C3)、**typed-event 进 prompt**——
   都是 B 类,须 registry 影子 + 真实 tool telemetry 对照;
2. **IV 分位 / 25Δ skew / 期限结构**(F1b)—— 依赖 F1 一期的实盘数据积累;
3. **regime cap/floor 接线**(O3)、**PCR/F2 进策略师或 regime**、**温度 v2**——
   全是会改名单/判断的 B 类;
4. **门重标定的两个实验**(§4.1)—— spec 生成器已就绪,但**未注册、未批准**;
5. **债务硬闸**(GATE0 BLOCKING)—— 需 availability SLO + 故障演练 + registry;
6. **嵌套 L4 调度**(§4.3)—— 10 项 probe 全 `UNTESTED`,且与 Wave9 冲突需用户重裁;
7. **F3 三条转债信号** —— capability gate 第一门(历史转股价 PIT)当前 FAIL。

---

## 4. 下一步该做什么(按信息增益排序)

1. **跑够样本**:`gate_recal` / `l3_marginal` / `l2_slo` 三个账本现在都是 `IMMATURE`,
   它们需要 ≥20 个成熟扫描日才会给出第一个非 IMMATURE 的读数。夜间已接线,只需时间。
2. **D1 真实 inventory**:`python -m autoresearch.news.catalog inventory` 跑一次真湖,
   拿到实测字节/分片数,再决定要不要迁物理文件。
3. **F1 一期取数接线**:`opt_basic` 分页 + `opt_daily` 联结的**生产取数**尚未接线
   (本波只交付了算法与契约)。接线后 `market_pack.derivatives` 才有内容。
4. **§4.3 玩具 probe**:10 项里先跑 `basic_invocation` / `child_failure` / `parent_death`
   三项 —— 前者不通则后面都不必测。
5. **用户重裁**:§4.3 与 Wave9 的 L4 调度冲突需要一次显式裁定。

---

## 5. 验收(实测,非声称)

| 项 | 读数 |
|---|---|
| 全量 `pytest` | 基线 **2534** → 本波后 **3116**(+582),0 失败 |
| `ruff check` 新增文件 | **0 errors**(15 个新模块逐个查) |
| `ruff check autoresearch/` | 165 → **163**。⚠️ **不是 0** —— 这 163 条是本波之前就在的历史遗留(集中在未触及的老文件),本波净减 2 条。诚实起见不写成「全绿」。 |
| `candidates --check` | 26 条全合规 |

**行为不变量**(逐条对 `fdc0ae3~1` 取 diff 验证,不是凭印象):

- `strategist_pack.py` / `common/scoring.py` diff = **空** → allowlist 与 composite 因子组未变;
- `l2_stratify.py` 的 `DEFAULT_FLOORS` diff = **空**;
- `.claude/agents/` 与 `.claude/skills/` diff = **空** → agent 指令与 skill 流程未动;
- `scan/l3/prompt.py` 唯一改动是**新增两个 artifact 写盘**(`_l3_pass1_kept.csv` /
  `_l3_pass1_meta.json`),`md`(喂 l3-rank 的正文)与 `pass1_header` 逐字节未变;
- `registry.active_by_family` = `{}` → **无任何 ACTIVE 实验**,§5-1 状态机未被绕过。

**已知的两处生产可见变化**(都是 advisory,不改名单/评级):

1. `prelude` 多一步 `preflight`(GATE0 advisory 行);
2. `nightly_close` 的 ledger 序多三项(`gate_recal` / `l3_marginal` / `l2_slo`),
   各自独立 suppress,失败不连坐。

---

_仅供研究,非投资建议。本波交付软件实现;任何生产行为变更仍须经实验治理流程与人工批准。_
