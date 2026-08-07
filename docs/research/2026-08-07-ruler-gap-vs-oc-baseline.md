# 两尺对照报告(gap_c1_o2 vs fwd_2_oc)—— 换尺认知底片

> 2026-08-05 用户裁定:评判主尺从 `fwd_2_oc`(D+1开买→D+2收卖)改为隔夜尺 `gap_c1_o2`(D+1收买→D+2开卖)。**本报告不是论证新尺更优**,只回答:换尺之后,哪些基于旧尺的历史结论会翻?

窗口:2026-06-18 ~ 2026-08-03（29 日）(有 `retro/attribution.csv` 的 scan 日;更晚的日期 T+2 尚未成熟)。`gap_c1_o2` 由 `context/lake/daily` 现算(attribution.csv 现无此列)——已用 2026-08-03/000001 逐位比特验证湖重算的 `fwd_2_oc` 与 attribution.csv 原值一致,确认湖的交易日序列与 retro 同源、D+1/D+2 定位可信(细节见 task-15-report.md)。

## ① 门的价值(真实 vs 影子 vs 市场,简单日频均值)

| 线 | fwd_2_oc 均值 | (n日) | gap_c1_o2 均值 | (n日) |
|---|---:|---:|---:|---:|
| 真实(bought) | -3.52% | 6 | -1.25% | 6 |
| 影子(shadow_buys) | -0.43% | 28 | -0.39% | 28 |
| 市场(eligible) | -0.37% | 29 | -0.15% | 29 |

- **门的价值(真实−影子)**:fwd_2_oc -3.09% vs gap_c1_o2 -0.86%。
- ⚠薄样本:「真实(bought)」只有 6 个有买单的 scan 日(0买日占绝大多数)——门的价值这两个数字统计功效弱,不建议直接当结论引用。

## ② 九路召回 unique 超额排序(两尺对照)

| 路 | 天数 | unique超额(oc) | 排名(oc) | unique超额(gap) | 排名(gap) | Δ排名 | 符号翻转 |
|---|---:|---:|---:|---:|---:|---:|---|
| value | 28 | +1.04% | 1 | -0.07% | 5 | +4 | ⚠是 |
| reversal | 28 | +0.54% | 2 | +0.05% | 3 | +1 | 否 |
| composite | 28 | +0.31% | 3 | +0.13% | 1 | -2 | 否 |
| main_fund | 28 | +0.04% | 4 | -0.18% | 8 | +4 | ⚠是 |
| northbound ⚠薄样本 | 1 | +0.01% | 5 | -0.01% | 4 | -1 | ⚠是 |
| accumulation | 14 | -0.11% | 6 | -0.12% | 6 | +0 | 否 |
| growth | 28 | -0.22% | 7 | +0.06% | 2 | -5 | ⚠是 |
| momentum | 28 | -1.09% | 8 | -0.26% | 9 | +1 | 否 |
| heat | 28 | -1.85% | 9 | -0.14% | 7 | -2 | 否 |
| healthy | 18 | -1.90% | 10 | -0.41% | 10 | +0 | 否 |

## ③ L3 真选 edge(finalist vs bench,两尺对照)

| 尺 | 天数 | finalist均值 | bench均值 | edge(finalist−bench) |
|---|---:|---:|---:|---:|
| fwd_2_oc | 14 | +0.46% | -1.14% | +1.59% |
| gap_c1_o2 | 14 | -0.26% | -0.25% | -0.01% |

## ④ 弃权日裁决翻转(abstention v2,shadow 口径重算)

- 自检:11/11 日,本模块 `_verdict()` 套用存量 fwd_2_oc 读数精确复现存量 `status_v2`。

| 日期 | 裁决(oc,存量) | 裁决(gap,重算) | 翻转 |
|---|---|---|---|
| 2026-07-15 | NEUTRAL | NEUTRAL | 否 |
| 2026-07-16 | FALSE | NEUTRAL | ⚠是 |
| 2026-07-17 | FALSE | NEUTRAL | ⚠是 |
| 2026-07-21 | FALSE | NEUTRAL | ⚠是 |
| 2026-07-24 | NEUTRAL | NEUTRAL | 否 |
| 2026-07-27 | NEUTRAL | NEUTRAL | 否 |
| 2026-07-28 | NEUTRAL | NEUTRAL | 否 |
| 2026-07-29 | NEUTRAL | FALSE | ⚠是 |
| 2026-07-30 | NEUTRAL | NEUTRAL | 否 |
| 2026-07-31 | NEUTRAL | NEUTRAL | 否 |
| 2026-08-03 | NEUTRAL | NEUTRAL | 否 |

- **4/11 日翻转**:2026-07-16, 2026-07-17, 2026-07-21, 2026-07-29

## ⑤ 作废/待重验清单

- **value 路「unique 超额 T2 全路第一」—— 作废/待重验**:fwd_2_oc 下 unique_excess=+1.04%(第1名),gap_c1_o2 下 unique_excess=-0.07%(第5名)—— **符号相反**,旧尺下的名次不能再直接引用。
- **momentum 相位条件性(`docs/research/2026-08-04-momentum-phase-conditional-ic.md`)—— 待重验,本任务未直接复算**:该结论建立在 fwd_2_oc 的分 regime rank IC 上,本报告四节都没有重跑分相位 IC,不构成对该结论的验证或推翻。 本报告②节的 momentum 路读数供参考:fwd_2_oc unique_excess=-1.09% vs gap_c1_o2 -0.26%(符号一致)——这是渠道层面的粗读数,不等于分 regime IC 的直接复算。
- **0买日「空仓正确性」—— 部分作废**:11 个可比对弃权日中 4 日两尺裁决翻转:2026-07-16, 2026-07-17, 2026-07-21, 2026-07-29——这些日子引用旧尺 v1/v2 裁决前需要用 gap 尺重判。
- **L3 真选 edge(finalist vs bench)—— 待重验**:fwd_2_oc 下 +1.59%,gap_c1_o2 下坍缩到近乎 0(-0.01%)——不是干净的符号翻转,是「有意义的正 edge」在隔夜尺下消失,同样不能直接沿用旧尺读数。

## 方法论 / 局限

- 市场基准口径全篇不统一命名同一套:①按 brief 原话用**均值**;②③④用 **eligible 子集中位数**(与 `channel_audit`/`rejection_attribution`/`l3_marginal` 各自的既有产物最接近的口径),两尺各自独立定义 eligible,不混用。
- `gap_c1_o2` 的 D+1/D+2 由湖(`context/lake/daily`)文件名排序推断,不查交易日历——与 `paper_nav.trade_days()` 同一口径;已做真实数据逐位比特验证(见上)。
- 本报告①节是 `paper_nav`(真实−影子=门的价值)的**简化**对照,不是 NAV 复利模拟——理由与限制见模块 docstring。
- ③节是 finalist-vs-bench 直接对照,不是 `l3_marginal.py` 的分层匹配反事实估计(窗口内历史日缺 `_l3_pass1_kept.csv` provenance,做不了那一套)。
- 样本量:见各节 n_days;不足的地方本报告如实标注,不外推。
