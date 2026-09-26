# 10 日尺预注册普查 · 首跑读数(FAM_SWING_RULER_20260926)

- 日期:2026-09-26(中秋,非交易日;湖最新 2026-09-24)
- 登记:`docs/research/2026-09-26-swing-ruler-family.spec.json`(提交 `791cbb2`,冻结 code_sha `e51e3de`),**先于普查代码提交**;看完本读数后未改任何阈值。
- 仪器:`python -m autoresearch.research.swing_ruler_census`(提交 `05326bb`;设计 `docs/superpowers/specs/2026-09-26-daily-engine-consolidation-design.md` §5 B2)
- 性质:**observational、只记不学**;不回注任何评级 / BUY / E6 / 主尺。本读数是 2026-09-26 冻结后的**基线读数**,不是 B4 的裁决输入(见 §4)。

## 1. 数据来源声明(必读)

首跑**不在生产账本上**,而在它的**回填副本**上:

1. 生产 `reports_claude/scan/_ledger/`(`recommendations.csv` 971 行 + 66 份逐 run outcome JSON)原样复制进会话 scratch;run 目录、`context_claude/`、`lake/` 只读软链。生产账本**一个字节未写**。
2. 用批 5 Task 1 的 `outcome.fill`(两枚成熟章;主尺已核验的 run 只补 10 日尺列,主尺冻结)回填副本。交易日历取 tushare `trade_cal` 一次(2026 全年 242 个 session),按请求区间过滤喂给 fill —— 与 `exec_anchor.trading_sessions` 第一级同源。
3. 回填结果:67 个 run 写入、0 个跳过;971 条既有行里**主尺数值变化 0 条、主尺状态变化 0 条、消失 0 条**(冻结在真数据上成立);新增 11 行(09-17 那一场,见 §1.1);10 日尺成熟章 MATURE_10 954 行 / PENDING_10 28 行。
4. 对齐自检:人口 606 行里,普查按账本 `t1`/`t10` 从湖重建的 fwd_10_oc 与账本自己的 `fwd_10_oc` 逐行相等(最大 |Δ| = 5e-7,即 6 位小数取整),市场基准窗口与账本同一交易日历。

### 1.1 回填时撞出的生产缺陷(已修,`4d27143`)

`exec_anchor._resolve_approved_at` 把 manifest 存储块里带 `+08:00` 的时刻与 `gate4.json` 换算出的 naive 本地时刻直接比较 → `TypeError`。2026-09-17 起的新 manifest 都带时区,而 `outcome.fill` 按目录序逐个 run 处理,于是**每晚的 fill 在 `20260917-0917_2152` 处中断**:生产账本停在分析日 2026-09-15,之后的 run(以及 B1 的 swing 回访)永远进不了账本。修法只改比较口径,返回值不变;测试 `tests/scan/test_exec_anchor.py::test_tz_aware_*`。

## 2. 读数(预注册判读)

窗口 `[2026-06-18, 2026-12-31)` · 样本门 n_days ≥ 40 · 决策区间 = 块长 10 的块 bootstrap 95% · H1–H3 一族 BY(arbitrary)q ≤ 0.05 · 行值 = fwd_10_oc − 同日全湖 D+1 开盘可买票 fwd_10_oc 中位(pp)。

| 假设 | 预期 | n_days | n_rows | 均值 pp | 块10 CI pp | 块1 / 块5 CI pp | q_BY | 判读 |
|---|---|---|---|---|---|---|---|---|
| H1 `swing_h1_hold_plus_finalists`(非 📌 finalist ∩ ≥Hold) | positive | 34 | 171 | −2.55 | [−5.93, −1.03] | [−4.47, −0.64] / [−5.63, −0.32] | 0.140 | **INSUFFICIENT** |
| H2 `swing_h2_lowturn_lane`(lane = lowturn) | positive | 6 | 9 | −3.11 | —(块不足) | [−5.73, −0.48] / [−3.34, −1.69] | — | **INSUFFICIENT** |
| H3 `swing_h3_rejection_negative`(UW / Sell) | negative | 28 | 78 | −3.72 | [−6.12, −0.50] | [−6.28, −1.75] / [−6.35, −0.71] | 0.085 | **INSUFFICIENT** |

H4(描述性,不入 BY 族):E6 相对 BUY 7 笔 / 7 天,fwd_10_oc 与 gap_c1_o2 符号一致 5/7(71%)。

人口:账本 982 行 → 同日多 run 只留最后一个(剔 252 行)→ MATURE_10 702 行(PENDING_10 28 行计数剔除,不当 0)→ 剔 📌 96 行 → 606 行 / 48 个分析日;市场基准 48 天全部可得;三格均无「D+1 开盘不可买 / fwd_10 缺值 / 基准不可得」剔除。📌 判据:账本 lane/role 旗 ∪ 冻结 staging 的 `edge_census.pinned_codes`;另查 65 个有冻结 finalists 文件的 run,「只有 pinned_note、lane 不是 pinned」的行 0 条,账本旗没有漏网。

## 3. 怎么读

- **三格全是 INSUFFICIENT**(H1 34 天 / H2 6 天 / H3 28 天,均 < 40):按登记规则**不判读**;停机规则(「H1 与 H2 同时不成立 → 观察席只展示不推」)要求判读,本次**未触发**。
- **方向(不是判读)**:三格点估计全为负。H1 ≥Hold 的 finalist 比同日市场中位低 2.55pp,块 10 区间整段在 0 以下,但 BY 后 q = 0.14 且样本门未过 —— 如果这个方向在 ≥40 天后仍在,H1 将是**反向证伪**(≥Hold 不是 10 日尺上的正信号),而不是「未证」。H3 的负号与否决假设同向(q = 0.085)。
- H1 与 H3 同为负、H3 更负约 1.2pp:这与 `stage_rulers.csv` 的 `l4_reject_value_fwd10`(+1.66pp,14 天;被否 = 早停 ∨ UW/Sell,可比 = 未早停满卡 Hold/OW/Buy)**口径不同**(人口、基准、去重规则都不一样),不可直接对表,也不据此补假设(不追加第五个假设)。
- `oc_judge` 列三格皆「样本不足」:overnight_census 的门(≥60 日且 ≥300 事件、2022–2025 逐年同号)在 2026 单年账本上按构造过不了,只作对照。
- 冻结前作者看过的东西(披露):账本的行数/评级/lane 构成、`stage_rulers.csv` 的 ALL 行(含上面那条 +1.66pp);**没有**看过任何按假设分格的 fwd_10 读数。

## 4. B4 用哪一次读数

- 登记的停机规则:B4 **只**采用冻结窗结束后第一次 H1 n_days ≥ 40 的全量读数,之后不再挑时点。读数目录是一次性的(`$RPT/research/swing_ruler/FAM_SWING_RULER_20260926/` 已存在即拒),所以**生产读数只跑一次**,且要等到门到了再跑。
- 判断「到没到」用不泄露读数、不建目录的探针(本次副本上:`h1_ready=false`,H1 34 天):

```bash
uv run --no-sync python -m autoresearch.research.swing_ruler_census \
    --spec docs/research/2026-09-26-swing-ruler-family.spec.json --sizes-only
```

- 生产账本先得有 10 日尺成熟章:合并后第一次 `outcome fill`(nightly_close 第 1 步,或手跑 `uv run --no-sync python -m autoresearch.scan.outcome fill`)会补上 09-17 那一场并给全部历史 run 盖 swing 章(主尺冻结)。
- 门到了(且冻结窗已过)再跑一次正式读数:

```bash
uv run --no-sync python -m autoresearch.research.swing_ruler_census \
    --spec docs/research/2026-09-26-swing-ruler-family.spec.json
```

## 5. 复现本次基线读数

副本位于会话 scratch(`trackB/census_world/`,`_ledger/` 为真拷贝,其余软链);回填脚本 `trackB/backfill_copy.py`,普查命令:

```bash
cd <scratch>/trackB/census_world
PYTHONPATH=<worktree> <worktree>/.venv/bin/python -m autoresearch.research.swing_ruler_census \
    --spec <worktree>/docs/research/2026-09-26-swing-ruler-family.spec.json \
    --reports-root ../census_out --scan-root reports_claude/scan --lake lake/daily
```

产物:`census_out/research/swing_ruler/FAM_SWING_RULER_20260926/{spec.json,cells.csv,manifest.json,readout.md}`(manifest 记账本与 70 个湖分区的 sha256、HEAD `4d27143`)。
