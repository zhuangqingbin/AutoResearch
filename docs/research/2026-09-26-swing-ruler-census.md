# 10 日尺预注册普查 · 基线读数(FAM_SWING_RULER_V2_20260926)

- 日期:2026-09-26(中秋,非交易日;湖最新 2026-09-24)
- 登记:`docs/research/2026-09-26-swing-ruler-family-v2.spec.json`(提交 `5291bf4`,冻结 code_sha `a2066e5`),**先于普查代码改动提交**;看完本读数后未改任何阈值。
- 仪器:`python -m autoresearch.research.swing_ruler_census`(判读检验 `research/swing_ruler_decision.py`,`a2066e5`;普查接线 `5bebc83`;设计 `docs/superpowers/specs/2026-09-26-daily-engine-consolidation-design.md` §5 B2)
- 性质:**observational、只记不学**;不回注任何评级 / BUY / E6 / 主尺。本读数是**基线读数**,不是 B4 的裁决输入(见 §4)。

## 0. 取代声明:FAM_SWING_RULER_20260926 → FAM_SWING_RULER_V2_20260926

2026-09-26 批 5 复审(I1)发现旧登记的判读检验在它自己的判读点上**没校准**:样本门 n = 40 天、块长 10 的块 bootstrap 每次重采样只有 4 个块,方差被严重低估。复审用本仓原语做的蒙特卡洛(真均值为 0):

- MA(9) 零假设(相邻分析日的 10 日窗口重叠 9 个 session)下,块 10 的 95% 区间只覆盖 0 约 65–70%;`block_mean_test` 的 p ≤ 0.05 实际发生 27–29%(名义 5%)。
- 整条旧判读规则(q_BY ≤ 0.05 ∧ 区间不含 0):单格约 20% 被判成 POSITIVE/NEGATIVE,三格至少一格约 47%。
- 旧规则的区间(非循环 moving-block 分位)与 p 值(中心化循环块)是两套方法:旧基线 H1 的块 10 区间整段在 0 以下,同一格 p = 0.051。

旧 id **从未在生产上读过**(唯一一次读数是下面 §2.2 那次在账本副本上的基线),所以修法是换 id 重新登记:四条假设、人口、窗口、样本门(40)、BY 族与停机规则全部照旧,只换判读检验 —— HAC t(Newey–West,Bartlett 核,滞后 9)配同一 n 上纯 MA(9) 重叠零假设模拟出的临界值,决策区间与 p 值同源(区间不含 0 ⇔ p ≤ 0.05)。尺寸校准(`tests/research/test_swing_ruler_decision.py`,固定种子):MA(9) 零假设逐格假判率 n = 40 / 60 / 100 为 4.8% / 5.4% / 5.0%;整条规则三格任一格 4.3%。为什么是这个检验、为什么样本门不抬,写在登记 spec 的 `purge_rule` 里。

旧 spec 文件 `docs/research/2026-09-26-swing-ruler-family.spec.json` **原样保留**作沿革;普查代码拿它跑一律拒(`superseded by FAM_SWING_RULER_V2_20260926`),一个字节都不落盘。同一次复审还把代码出处的行为根去掉了 `scan/outcome.py`(M5:普查读的是账本数据,输入 sha256 已落 manifest)。

## 1. 数据来源声明(必读)

基线**不在生产账本上**,而在它的**回填副本**上:

1. 生产 `reports_claude/scan/_ledger/`(`recommendations.csv` 971 行 + 66 份逐 run outcome JSON)原样复制进会话 scratch;run 目录、`context_claude/`、`lake/` 只读软链。生产账本**一个字节未写**(跑前在 scratch 落时间戳标记,跑后 `find <生产三处> -newer 标记` 为空)。
2. 用本修补波的 `outcome.fill`(两枚成熟章;主尺已核验的 run 只补 10 日尺列,主尺冻结;swing 回访崩了只隔离不中断 —— 复审 M1;T+10 分区算得出的行不够多数不盖 MATURE_10 —— 复审 M6)回填副本。交易日历取 tushare `trade_cal` 一次(2026 全年 242 个 session),按请求区间过滤喂给 fill —— 与 `exec_anchor.trading_sessions` 第一级同源。
3. 回填结果:67 个 run 写入、0 个跳过;971 条既有行做**全列 diff**:10 日尺四列(`fwd_5_oc` 49 格、`fwd_10_oc` 43 格空 → 有值;`outcome_status_swing` / `t10` 971 格新盖章)之外**变化 0 格、消失 0 行**(主尺冻结在真数据上成立);新增 11 行(09-17 那一场,见 §1.1);10 日尺成熟章 MATURE_10 954 行 / PENDING_10 28 行。同一 `today` 再跑一夜:写入 0 个 run,CSV 逐字节不变(64 个 `already_verified_complete`、3 个 `swing_not_mature:PENDING_10`)。
4. 对齐自检(沿用首跑的核对,本次人口逐行相同):人口 606 行里,普查按账本 `t1`/`t10` 从湖重建的 fwd_10_oc 与账本自己的 `fwd_10_oc` 逐行相等(最大 |Δ| = 5e-7,即 6 位小数取整),市场基准窗口与账本同一交易日历。

### 1.1 回填时撞出的生产缺陷(已修,`4d27143`)

`exec_anchor._resolve_approved_at` 把 manifest 存储块里带 `+08:00` 的时刻与 `gate4.json` 换算出的 naive 本地时刻直接比较 → `TypeError`。2026-09-17 起的新 manifest 都带时区,而 `outcome.fill` 按目录序逐个 run 处理,于是**每晚的 fill 在 `20260917-0917_2152` 处中断**:生产账本停在分析日 2026-09-15,之后的 run(以及 B1 的 swing 回访)永远进不了账本。修法只改比较口径,返回值不变;测试 `tests/scan/test_exec_anchor.py::test_tz_aware_*`。

现场证据(只读):生产 `_ledger/views/_health.json` 的 `last_success_at` 停在 2026-09-17T12:59:43Z,`last_attempt_at` 一直走到 2026-09-26T05:28:40Z;`recommendations.csv` 最后修改于 09-17 20:59(本地),早于 09-17 那一场 21:52 的发布 —— 夜间任务每晚都在跑、每晚都没成功。

## 2. 读数

### 2.1 本登记(v2,校准检验)

窗口 `[2026-06-18, 2026-12-31)` · 样本门 n_days ≥ 40 · 决策检验 `hac_bartlett_overlap_null`(滞后 9,R = 20000,种子 20260926)的 95% 区间与 p 值同源 · H1–H3 一族 BY(arbitrary)q ≤ 0.05 · 行值 = fwd_10_oc − 同日全湖 D+1 开盘可买票 fwd_10_oc 中位(pp)。

| 假设 | 预期 | n_days | n_rows | 均值 pp | 校准 CI pp | p | q_BY | 判读 |
|---|---|---|---|---|---|---|---|---|
| H1 `swing_h1_hold_plus_finalists`(非 📌 finalist ∩ ≥Hold) | positive | 34 | 171 | −2.55 | [−9.15, +4.05] | 0.331 | 0.909 | **INSUFFICIENT** |
| H2 `swing_h2_lowturn_lane`(lane = lowturn) | positive | 6 | 9 | −3.11 | —(观察数不足 20) | — | — | **INSUFFICIENT** |
| H3 `swing_h3_rejection_negative`(UW / Sell) | negative | 28 | 78 | −3.72 | [−13.39, +5.95] | 0.310 | 0.909 | **INSUFFICIENT** |

H4(描述性,不入 BY 族):E6 相对 BUY 7 笔 / 7 天,fwd_10_oc 与 gap_c1_o2 符号一致 5/7(71%)。

块 bootstrap 敏感性(全报不挑;**未校准,不判读**):H1 块 1 / 5 / 10 = [−4.47, −0.64] / [−5.63, −0.32] / [−5.93, −1.03];H2 [−5.73, −0.48] / [−3.34, −1.69] / —;H3 [−6.28, −1.75] / [−6.35, −0.71] / [−6.12, −0.50]。

人口:账本 982 行 → 同日多 run 只留最后一个(剔 252 行)→ MATURE_10 702 行(PENDING_10 28 行计数剔除,不当 0)→ 剔 📌 96 行 → 606 行 / 48 个分析日;市场基准 48 天全部可得;三格均无「D+1 开盘不可买 / fwd_10 缺值 / 基准不可得」剔除。📌 判据:账本 lane/role 旗 ∪ 冻结 staging 的 `edge_census.pinned_codes`;另查 65 个有冻结 finalists 文件的 run,「只有 pinned_note、lane 不是 pinned」的行 0 条,账本旗没有漏网。

### 2.2 旧登记(v1,已取代 —— 未校准,只作沿革)

同一份人口的 v1 首跑(块 10 的块 bootstrap 作决策区间,`block_mean_test` 作 p):H1 34d −2.55 [−5.93, −1.03] q 0.140;H2 6d −3.11 —;H3 28d −3.72 [−6.12, −0.50] q 0.085;三格 INSUFFICIENT。这些区间就是 §0 说的「4 块重采样」区间:它们排除 0 **不是证据**,只是方差被低估。

## 3. 怎么读

- **三格全是 INSUFFICIENT**(H1 34 天 / H2 6 天 / H3 28 天,均 < 40):按登记规则**不判读**;停机规则(「H1 与 H2 同时不成立 → 观察席只展示不推」)要求判读,本次**未触发**。
- **方向不是判读**:三格点估计都为负(H1 −2.55pp、H3 −3.72pp),但校准区间很宽、跨 0(p ≈ 0.33 / 0.31)。旧版本文件曾写「块 10 区间整段在 0 以下…如果这个方向在 ≥40 天后仍在,H1 将是反向证伪」—— **撤回**:那个区间是未校准的 4 块重采样,在校准检验下离显著还很远。在 v2 的正式读数出来之前,不对 H1 的方向下任何结论(既不说「反向证伪」,也不说「≥Hold 在 10 日尺上无效」)。
- 功效要心里有数:日超额标准差 σ 在 H1 约 5.7pp,真效应 0.5σ(≈ 2.8pp)在 40 天时的检出率约 16%、60 天约 27%(登记 `purge_rule`)。所以 40 天的正式读数若是 UNPROVEN,它的意思是「这点样本量不出来」,**不等于**已证无效。
- H1 与 H3 同为负、H3 更负约 1.2pp:这与 `stage_rulers.csv` 的 `l4_reject_value_fwd10`(+1.66pp,14 天;被否 = 早停 ∨ UW/Sell,可比 = 未早停满卡 Hold/OW/Buy)**口径不同**(人口、基准、去重规则都不一样),不可直接对表,也不据此补假设(不追加第五个假设)。
- `oc_judge` 列三格皆「样本不足」:overnight_census 的门(≥60 日且 ≥300 事件、2022–2025 逐年同号)在 2026 单年账本上按构造过不了,只作对照。
- 冻结前作者看过的东西(披露):v1 登记冻结前看过账本的行数/评级/lane 构成、`stage_rulers.csv` 的 ALL 行(含上面那条 +1.66pp);v2 登记冻结前**已经看过** §2.2 那张 v1 基线表(本次重新登记只换检验、不动假设与人口,判读样本也要到 ≥40 天才读;看过的是基线的点估计与未校准区间)。

## 4. B4 用哪一次读数

- 登记的停机规则:B4 **只**采用冻结窗结束后第一次 H1 n_days ≥ 40 的 **v2** 全量读数,之后不再挑时点。读数目录是一次性的(`$RPT/research/swing_ruler/FAM_SWING_RULER_V2_20260926/` 已存在即拒),所以**生产读数只跑一次**,且要等到门到了再跑。
- 判断「到没到」用不泄露读数、不建目录的探针(本次副本上:`h1_ready=false`,H1 34 天):

```bash
uv run --no-sync python -m autoresearch.research.swing_ruler_census \
    --spec docs/research/2026-09-26-swing-ruler-family-v2.spec.json --sizes-only
```

- 生产账本先得有 10 日尺成熟章:合并后第一次 `outcome fill`(nightly_close 第 1 步,或手跑 `uv run --no-sync python -m autoresearch.scan.outcome fill`)会补上 09-17 那一场并给全部历史 run 盖 swing 章(主尺冻结;首夜在本次副本上约 45 秒、复审实测约 155 秒,之后约 2 秒 —— 复审 M7 建议在下一场扫描之前手跑一次)。
- 门到了(且冻结窗已过)再跑一次正式读数:

```bash
uv run --no-sync python -m autoresearch.research.swing_ruler_census \
    --spec docs/research/2026-09-26-swing-ruler-family-v2.spec.json
```

- 代码出处:普查按 spec 的 `code_sha`(`a2066e5`)核验行为根,合并时**保留该提交**(merge,不要 squash / rebase 改写它),否则核验找不到那个提交就拒跑。

## 5. 复现本次基线读数

副本位于会话 scratch(`trackF/census_world/`,`_ledger/` 为真拷贝,run 目录 / lake / context 软链);回填脚本 `trackF/backfill_copy.py`(含全列 diff 与二夜重跑),普查命令:

```bash
cd <scratch>/trackF/census_world
PYTHONPATH=<worktree> <worktree>/.venv/bin/python -m autoresearch.research.swing_ruler_census \
    --spec <worktree>/docs/research/2026-09-26-swing-ruler-family-v2.spec.json \
    --reports-root ../census_out --scan-root reports_claude/scan --lake lake/daily
```

产物:`census_out/research/swing_ruler/FAM_SWING_RULER_V2_20260926/{spec.json,cells.csv,manifest.json,readout.md}`(manifest 记账本与 70 个湖分区的 sha256、声明 code_sha `a2066e5`、本次 HEAD `5bebc83`)。
