# Wave10 Gate 0 · 证据冻结审计

> 对应 `docs/specs/2026-08-01-wave10-report-ops-slimdown-zerobuy-design.md` §1.3(Gate 0)与 §A0 / §A11。
> 冻结快照:`docs/research/2026-08-01-wave10-gate0-evidence.json`(62 指标 · 25 分母 · 0 冲突 · as_of `20260731`)。
> 本 commit **只做证据冻结,不改报告与生产行为**。

## 1. Gate 0 完成条件核对

| # | 条件 | 状态 | 证据 |
|---|---|---|---|
| 1 | 生成跨日 evidence manifest | ✅ | `reports/learning/wave10_evidence.{json,md}`;审计快照进 `docs/research/`(`reports/`、`context/` 均 gitignore) |
| 2 | 每指标含 value/numerator/denominator/cohort/as_of/source_paths/source_hashes/query_version | ✅ | `Metric` dataclass;另加 `source_field` 用于逮住"改标签不改来源"的语义注入 |
| 3 | 复算 §1.1,手抄数字 diff = 0 | ✅ | `tests/learning/test_wave10_gate0_freeze.py` **直接从设计稿正文抠数字**与快照对账 |
| 4 | 正式落 CORRECT/NEUTRAL/FALSE_KILL/UNMEASURED 门归因 | ✅ | `autoresearch/learning/gate_attribution.py` |
| 5 | registry inventory 入 manifest | ✅ | 快照 `registry_inventory`:`exp_20260729_l3_hard_constraint_f`(PREREGISTERED · prompt family) |

单日 manifest(`context/scan/<date>/evidence_manifest.json`)由 `--day` 生成,随扫描日增量落盘。

## 2. §1.1 复算结果:全部对上,零 diff

| §1.1 原文 | 快照再生 | 结论 |
|---|---|---|
| raw journal rows 30 日 | `journal_scan_days` = 30(cohort `raw_run`) | ✅ |
| T+2 mature 26 日 | `zero_buy_mature_days` = 26(cohort `t2_mature`) | ✅ |
| 20 个 0买日 + 6 个买日 | 20 / 6 | ✅ |
| 0买日市场 fwd_2 均值 −0.96% | `-0.0096` | ✅ |
| abstention v2 mature 8 日 · CORRECT 0 / FALSE 3 / NEUTRAL 5 · 8/8 DEGRADED | 同 | ✅ |
| 真实 −0.24%(9 笔) / 影子 −3.16%(81 笔) / sized −5.86% / 市场等权 −9.85% | 同 | ✅ |
| 主力真在门 15 日 / 63 次 · mean excess2 −1.33% | 同(cohort `legacy_migration`) | ✅ |
| 36/63 · 7/63 · 20/63 | `correct_rate` 0.5714 · `false_kill_rate` 0.3175 | ✅ |

`--check-legacy` 逐门对账 `gate_ledger`:主力真在 63/36 · 业绩真兑现 40/18 · 估值不透支 34/24 · UNDECIDABLE 8/3 · MULTI_GATE 7/3,全部一致。

## 3. 复核中发现的实质问题(立案诊断之外)

### 3.1 legacy 口径系统性放大单门分母

`gate_fires.csv` 路(63 次里的 61 次)**不去重**:65 个 (日,码) 里 **47 个同时踩 ≥2 道门**。
即"主力门拦了 63 次"里绝大多数是多门共同拦下的。v3 去重后:

| 门 | legacy 拦次 | v3 归因拦次 | v3 被拦 ex2 均值 |
|---|---:|---:|---|
| 主力真在 | 63 | **17** | **+0.63%** |
| 业绩真兑现 | 40 | **4** | +3.01% |
| 估值不透支 | 34 | **0** | — |
| MULTI_GATE | 7 | **54** | −2.07% |

**读法**:legacy 那句"主力门拦对 57%、平均 −1.33%"几乎全部由 `MULTI_GATE`(54 次 / −2.07%)扛。
当主力门是**唯一**否决者时,被拦票平均**跑赢** +0.63%,17 次里 5 次是 `FALSE_KILL`。

这不改变任何生产口径(§7 非目标),但它是 §C1「判断层」诊断的直接证据升级:
门的总量价值成立,而"主力门单独否决"这一动作目前**没有已证价值**。

### 3.2 EXP-1 的人口口径必须是 participation,不是 attribution

按 §A11 的去重口径,主力门单门样本只有 17 例 / 10 日 —— 永远攒不到 EXP-1 要求的
`mature_events≥50`。但 EXP-1 要考核的人口本来就不是"只有主力门否决的票",而是
"主力门参与否决过的票"。故 A11 落两张表,**职责不同、不得互换**:

- `gate_attribution_v3.csv`(attribution):多门共拦 → `MULTI_GATE`。**错杀率只能用这张**。
- `gate_participation_v3.csv`(participation):三道门各记一次,带 `n_gates_failed` /
  `sole_killer`。**EXP-1 用这张**;因分母重复计数,该表在报告里**结构上不提供比率列**。

participation 口径下主力门 69 次 / 15 日,EXP-1 有可用人口。

### 3.3 两套市场基准长期并存,此前无人标注

`gate_ledger` 用**全表 `fwd_2_oc` 均值**;`rejection_attribution` / abstention v2 / C3 用
**可交易成熟票中位**。同一票在两套基准下可以得出相反结论(见单测
`test_legacy_and_v3_use_different_market_baselines`)。v3 统一采用后者(与 C3 同源),
legacy 保留前者仅供迁移复现,`market_baseline` 字段逐行标注。

## 4. 语义守卫(§A0 验收)

- **「39% = 错杀率」注入**:`left_tail_protection_rate` 与 `false_kill_rate` 绑定不同来源字段
  (`gate_ledger.tail_rate` vs `gate_attribution.false_kill_rate`),且允许 cohort 不重叠。
  三种注入手法各有专属守卫:换标签+换 cohort(分母规则)、只换标签(来源绑定)、
  改绑定表本身(`test_no_two_semantics_share_a_source_field`)。
- **raw 30 与 mature 26 不得同分母**:`denominator_id` 只登记一次且只属于一个 cohort;
  跨 cohort 借分母在 `Manifest.add` 写入时抛错。
- **冲突降级不阻断**:同一 `metric_id` 两个值 → `status=CONFLICT` + `quotable()` 为假;
  另有 journal × zero_buy 逐日买单数对账(历史 D5 病灶)。

## 5. 验收证据

- `tests/learning/test_gate_attribution.py` 20 例;`tests/learning/test_evidence_manifest.py` 21 例;
  `tests/learning/test_wave10_gate0_freeze.py` 12 例。
- **变异测试**(改完先问"把这段删掉测试会红吗"):
  - A11 5/5 变异被逮(FALSE_KILL 边界 `>=`→`>` / 去重失效 / 基准 median→mean /
    tradable 过滤失效 / participation 只记第一道门);
  - A0 6/6 被逮(分母 cohort 独占 / 跨 cohort 借分母 / 语义↔来源绑定 /
    validate 的 cohort 允许集 / paper_nav 取错成绩单 / 冲突不降级);
  - 事实门:篡改冻结快照数值 → 3 例变红。
- `gate_ledger.md` 原表逐字未变(新 v3 分布另起小节,parity 已核)。

## 6. 未做 / 后续

- 本 commit **不改**任何生产门、评级、prompt、floor、quota(§7)。
- EXP-1 **尚未注册** —— 按 §1.3-4 必须等门归因正式落账后才可注册,本 commit 完成该前置。
- `paper_nav` 的"真实 − 影子 ≈ +2.9pp"仍只能称**组合政策的观察差**,快照 `note` 已写死这一限制。
- 单日 manifest 尚未接进 `scan.assemble`(Track A 一并做);当前由 `nightly_close` 跑跨日清单。
