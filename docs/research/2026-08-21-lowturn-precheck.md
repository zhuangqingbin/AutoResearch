# 低位转强 · Gate 0 前置证伪读数

## 0. 假设与停机规则（先写后看；本节 commit 早于任何读数）

- **H0**：在主尺 `gap_c1_o2`（T+1 收买 → T+2 开卖）下，「低位转强」画像（design 2026-08-21 §6.1 默认阈值：距 60 日高 ≥15% ∧ 60 日涨幅 <10 ∧ 站回 MA20 ∧ MA5>MA10 ∧ 近 5 日为正 ∧ vol_ratio_20≥1.2 ∧ 主力或 CMF 转正 ∧ 非健康上涨）相对全市场可交易等权的超额 ≤ 0。
- **H1**：超额 > 0（不要求显著）。本门只证伪 **显著为负**——「不显著」不等于「有 alpha」，只等于「没被证伪」。
- **停机规则**：
  - 相对超额均值 ≤ −0.5pp **∧** t ≤ −2.0 **∧** n_days ≥ 40 → **STOP_P3**（L3 席位不上线；P1 数据腿 / P2 通道保留，负结果归档进 `factor-backlog.md`）；
  - 每日旗亮中位 <3 只 → **SPARSE**（定义过严）。仅允许**预登记的两处放宽**：`min_vol_ratio_20` 1.2→1.0、`max_dist_high_60` −15→−10，重跑一次；仍 <3 → 记「样本稀疏」，P3 仍可上但守卫⑥ target 保持 1；
  - 其余 → **PROCEED**（进入 ≥10 扫描日活体裁决，见 design §8）。
- **对照组**：`healthy`（现行主力画像）、旧 `reversal` 门、**修后**的 `reversal_confirm` 门（③ 已由 `ma_bull` 改为 `above_ma20 ∧ ma5_gt_ma10`，② 缩量看 D−1、RSI 上限 85；见 commit e054fc6）。
- **参考尺**：`fwd_5_oc` / `fwd_10_oc` **只观察**，不参与任何裁决；决策尺仍为 `gap_c1_o2`（2026-07-10 / 08-05 用户裁定）。
- **分 regime 表**：lowturn × 主尺按 trend/range/risk_off 分桶，**只读**，不进停机规则（risk_off 样本薄，历史教训：不得靠全样本调参后声称分 regime 稳定）。
- **样本**：factor_lab 成型日面板（`context_claude/factor_lab/plan.pkl`，2025-05-23→2026-08-05，132 个成型日），cap_floor 30 亿，`buyable_only`（逐尺过 `ruler.entry_tradable`）。
- **口径**：相对超额 = 组内均值 − 当日可交易全集截面**中位**（与 `stage_eval.channel_edge` 同口径）；跨日 t = 逐日超额的 one-sample t。截面 <50 只或组内空 → 该日不计入。

> 代码与常量：`autoresearch/research/lowturn_precheck.py`（`STOP_EXCESS_PP=-0.5`、`STOP_T=-2.0`、`STOP_MIN_DAYS=40`、`SPARSE_MED=3`），commit adde3f6 —— **早于本文件的任何读数**。

## 1. 主表（四组 × 三尺）

_（跑完贴表）_

## 2. 放宽档（仅当 §1 判 SPARSE）

_（如未触发，本节写「未触发」）_

## 3. 裁决

_（一句话 + 下一步）_
