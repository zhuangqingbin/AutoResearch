# Ledger spike readout — recommendations.csv × lake market baseline (2026-08-26, read-only)

**Definitions used (verified in `autoresearch/research/factor_lab.py::forward_returns`, `autoresearch/scan/outcome.py`, `autoresearch/research/edge_census.py`):**
- `gap_c1_o2` = open[D+2]/close[D+1] − 1 (T+1 close buy → T+2 open sell; MAIN ruler). `excess_med_market` (ledger col) = gap − cross-sectional **median** of gap over all lake stocks with `buyable_c1` (T+1 close not sealed limit-up). Group stat restricted to `buyable_c1==True` (edge_census `daily_stats` convention).
- `fwd_5_oc` = close[D+5]/open[D+1] − 1; `fwd_10_oc` = close[D+10]/open[D+1] − 1, D+k = k-th **lake** trading day after analysis_date D (entry = D+1 open, **not** T+1 close). Ledger values are RAW. I recomputed them from `lake/daily` (max |diff| vs ledger = 5e-7 → identical) and built the baseline: per-date cross-sectional **median** over all lake stocks with `buyable` (D+1 not one-word limit-up: open==close==high ∧ pct≥board×0.98), n≈5,490–5,530/day. `fwd_5_rel`/`fwd_10_rel` = raw − that median; group stats restricted to rows with `buyable==True`.
- For Q4 an additionally **aligned** pair `fwd_5_c1c`/`fwd_10_c1c` = close[D+5]/close[D+1] − 1 and close[D+10]/close[D+1] − 1 (entry at the T+1 close that `t1_pos_in_range` describes), relative to lake median over `buyable_c1`.
- Stats: `pooled mean`/`t_pool` over rows; `day-mean`/`t_day` = mean and one-sample t of per-date group means (edge_census `aggregate` convention; primary); `t_NW` = Newey-West (Bartlett, lag 4 for fwd_5, lag 9 for fwd_10, in date-steps) on the per-date series — the honest t for overlapping windows. `hit>0` share of rows >0; `share days<0` share of per-date means <0.
- Dedupe: **LAST run_id per analysis_date** (9 dates had reruns). Sensitivity with FIRST run in Q7.
- Lake last day = 20260824 → `fwd_5` exists for analysis_date ≤ 2026-08-17 (37 dates), `fwd_10` for ≤ 2026-08-10 (33 dates). **P3 (E6 active, 08-19/08-20) has NO fwd_5/fwd_10 yet**, only gap.
- Rows: 879 total → 627 after dedupe. mode=shadow BUY rows (08-13, 08-17×2, 08-18) are **excluded** from every BUY stat (shown once, separately, for the record).
- Population break: ≤07-09 each run logged 30 'finalist' rows (pre-L3.5-merge era: finalist = L3 top-30, no L4 rating), from 07-10 on 6–10 finalists + 2–4 pinned per run with L4 ratings. 'finalist' in P1/early-P2 is therefore a wider, shallower population than in late P2.

## Q1 — by role × period (dedupe LAST run; BUY = mode=active only)

| role | period | measure | n_rows | n_days | pooled mean | t_pool | day-mean | t_day | t_NW | hit>0 | share days<0 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| finalist | P1 ≤07-02 reversal | gap_c1_o2 − mkt median (pp) | 273 | 10 | -0.23 | -2.74 | -0.22 | -1.24 | n/a | 0.46 | 0.70 |
| finalist | P1 ≤07-02 reversal | fwd_5_oc − mkt median (pp) | 279 | 10 | +1.11 | +2.23 | +0.94 | +1.30 | +1.13 | 0.56 | 0.30 |
| finalist | P1 ≤07-02 reversal | fwd_10_oc − mkt median (pp) | 279 | 10 | +0.48 | +0.71 | +0.42 | +0.38 | +0.84 | 0.53 | 0.30 |
| finalist | P2 07-03..08-18 | gap_c1_o2 − mkt median (pp) | 245 | 28 | -0.27 | -4.40 | -0.32 | -4.61 | n/a | 0.33 | 0.82 |
| finalist | P2 07-03..08-18 | fwd_5_oc − mkt median (pp) | 242 | 27 | -1.89 | -3.83 | -1.39 | -1.48 | -1.17 | 0.38 | 0.59 |
| finalist | P2 07-03..08-18 | fwd_10_oc − mkt median (pp) | 217 | 23 | -4.46 | -5.80 | -3.60 | -2.82 | -3.10 | 0.37 | 0.74 |
| finalist | P3 ≥08-19 E6-active | gap_c1_o2 − mkt median (pp) | 12 | 2 | +0.31 | +1.31 | +0.31 | +4.88 | n/a | 0.58 | 0.00 |
| finalist | ALL | gap_c1_o2 − mkt median (pp) | 530 | 40 | -0.24 | -4.54 | -0.26 | -3.85 | n/a | 0.40 | 0.75 |
| finalist | ALL | fwd_5_oc − mkt median (pp) | 521 | 37 | -0.28 | -0.79 | -0.76 | -1.04 | -0.78 | 0.47 | 0.51 |
| finalist | ALL | fwd_10_oc − mkt median (pp) | 496 | 33 | -1.68 | -3.24 | -2.39 | -2.40 | -2.03 | 0.46 | 0.61 |
| pinned | P2 07-03..08-18 | gap_c1_o2 − mkt median (pp) | 77 | 24 | +0.66 | +1.38 | +0.67 | +0.82 | n/a | 0.53 | 0.50 |
| pinned | P2 07-03..08-18 | fwd_5_oc − mkt median (pp) | 75 | 23 | +1.10 | +0.78 | +1.51 | +0.73 | +0.50 | 0.60 | 0.35 |
| pinned | P2 07-03..08-18 | fwd_10_oc − mkt median (pp) | 64 | 19 | -0.84 | -0.37 | +0.61 | +0.17 | +0.11 | 0.55 | 0.53 |
| pinned | P3 ≥08-19 E6-active | gap_c1_o2 − mkt median (pp) | 5 | 2 | -0.13 | -0.26 | +0.05 | +0.05 | n/a | 0.40 | 0.50 |
| pinned | ALL | gap_c1_o2 − mkt median (pp) | 82 | 26 | +0.61 | +1.36 | +0.62 | +0.82 | n/a | 0.52 | 0.50 |
| pinned | ALL | fwd_5_oc − mkt median (pp) | 75 | 23 | +1.10 | +0.78 | +1.51 | +0.73 | +0.50 | 0.60 | 0.35 |
| pinned | ALL | fwd_10_oc − mkt median (pp) | 64 | 19 | -0.84 | -0.37 | +0.61 | +0.17 | +0.11 | 0.55 | 0.53 |
| BUY-active | P3 ≥08-19 E6-active | gap_c1_o2 − mkt median (pp) | 2 | 2 | +0.13 | +0.14 | +0.13 | +0.14 | n/a | 0.50 | 0.50 |
| BUY-active | ALL | gap_c1_o2 − mkt median (pp) | 2 | 2 | +0.13 | +0.14 | +0.13 | +0.14 | n/a | 0.50 | 0.50 |

Shadow-BUY rows (excluded above, for the record): 2026-08-13 688766 普冉股份 lane=pinned rating=Hold gap_rel=+2.19pp fwd5_rel=+21.56pp; 2026-08-17 000779 甘咨询 lane=healthy rating=Underweight gap_rel=-0.82pp fwd5_rel=-12.66pp; 2026-08-17 000779 甘咨询 lane=healthy rating=Underweight gap_rel=-0.82pp fwd5_rel=-12.66pp; 2026-08-18 688766 普冉股份 lane=pinned rating=Hold gap_rel=-0.11pp fwd5_rel=—pp

Active-BUY rows (n=2): 2026-08-19 601857 中国石油 lane=healthy rating=Hold gap=+0.55pp gap_rel=+1.04pp fwd5/10=not mature; 2026-08-20 002081 金螳螂 lane=healthy rating=Underweight gap=-0.78pp gap_rel=-0.78pp fwd5/10=not mature

## Q1b — finalist, P2 split at the 07-10 population break (30/run unrated → 6–10/run rated)

| group | measure | n_rows | n_days | pooled mean | t_pool | day-mean | t_day | t_NW | hit>0 | share days<0 |
|---|---|---|---|---|---|---|---|---|---|---|
| ≤07-09 (30-finalist era) | gap_c1_o2 − mkt median (pp) | 73 | 4 | -0.24 | -2.14 | -0.32 | -2.57 | n/a | 0.37 | 1.00 |
| ≤07-09 (30-finalist era) | fwd_5_oc − mkt median (pp) | 76 | 4 | -3.26 | -2.86 | -4.66 | -1.65 | -2.49 | 0.36 | 0.75 |
| ≤07-09 (30-finalist era) | fwd_10_oc − mkt median (pp) | 76 | 4 | -6.61 | -4.22 | -7.40 | -4.17 | -9.49 | 0.38 | 1.00 |
| 07-10..08-18 (rated era) | gap_c1_o2 − mkt median (pp) | 172 | 24 | -0.29 | -3.84 | -0.32 | -4.04 | n/a | 0.31 | 0.79 |
| 07-10..08-18 (rated era) | fwd_5_oc − mkt median (pp) | 166 | 23 | -1.26 | -2.57 | -0.82 | -0.84 | -0.56 | 0.39 | 0.57 |
| 07-10..08-18 (rated era) | fwd_10_oc − mkt median (pp) | 141 | 19 | -3.29 | -4.04 | -2.80 | -1.94 | -1.49 | 0.36 | 0.68 |

## Q1c — sign stability by month (finalist)

| group | measure | n_rows | n_days | pooled mean | t_pool | day-mean | t_day | t_NW | hit>0 | share days<0 |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-06 | gap_c1_o2 − mkt median (pp) | 233 | 8 | -0.33 | -3.66 | -0.34 | -1.76 | n/a | 0.45 | 0.75 |
| 2026-06 | fwd_5_oc − mkt median (pp) | 239 | 8 | +1.30 | +2.33 | +1.30 | +1.63 | +1.41 | 0.57 | 0.25 |
| 2026-06 | fwd_10_oc − mkt median (pp) | 239 | 8 | +0.31 | +0.45 | +0.32 | +0.24 | +0.54 | 0.54 | 0.25 |
| 2026-07 | gap_c1_o2 − mkt median (pp) | 209 | 19 | -0.15 | -1.94 | -0.27 | -2.75 | n/a | 0.37 | 0.79 |
| 2026-07 | fwd_5_oc − mkt median (pp) | 212 | 19 | -2.00 | -3.62 | -1.85 | -1.51 | -1.26 | 0.39 | 0.63 |
| 2026-07 | fwd_10_oc − mkt median (pp) | 212 | 19 | -3.90 | -4.45 | -3.67 | -2.42 | -2.45 | 0.38 | 0.74 |
| 2026-08 | gap_c1_o2 − mkt median (pp) | 88 | 13 | -0.20 | -2.18 | -0.21 | -1.92 | n/a | 0.35 | 0.69 |
| 2026-08 | fwd_5_oc − mkt median (pp) | 70 | 10 | -0.51 | -0.78 | -0.32 | -0.31 | -0.32 | 0.39 | 0.50 |
| 2026-08 | fwd_10_oc − mkt median (pp) | 45 | 6 | -1.81 | -1.87 | -1.91 | -1.23 | -2.19 | 0.42 | 0.67 |

Per-date finalist means (pp):

| date | gap_rel | fwd5_rel | fwd10_rel | n |
|---|---|---|---|---|
| 2026-06-18 | -0.92 | +4.74 | -7.63 | 30 |
| 2026-06-22 | -0.25 | +4.80 | +2.48 | 30 |
| 2026-06-23 | -0.47 | +0.28 | +3.74 | 30 |
| 2026-06-24 | +0.24 | +0.82 | +0.24 | 30 |
| 2026-06-25 | +0.31 | +0.38 | +3.95 | 30 |
| 2026-06-26 | -0.26 | -1.34 | -1.23 | 30 |
| 2026-06-29 | -0.08 | +0.80 | +0.37 | 30 |
| 2026-06-30 | -1.29 | -0.11 | +0.62 | 29 |
| 2026-07-01 | +0.63 | +1.29 | +3.43 | 25 |
| 2026-07-02 | -0.15 | -2.27 | -1.79 | 15 |
| 2026-07-03 | -0.05 | -1.31 | -5.64 | 30 |
| 2026-07-06 | -0.24 | +0.76 | -4.93 | 20 |
| 2026-07-08 | -0.35 | -6.22 | -6.39 | 15 |
| 2026-07-09 | -0.63 | -11.90 | -12.65 | 11 |
| 2026-07-10 | -0.52 | +2.78 | +11.59 | 7 |
| 2026-07-13 | -1.07 | +9.77 | +8.51 | 3 |
| 2026-07-14 | -0.72 | -2.74 | -8.05 | 10 |
| 2026-07-15 | -0.47 | +4.60 | -1.88 | 8 |
| 2026-07-16 | +0.42 | +2.42 | +2.44 | 9 |
| 2026-07-17 | -0.60 | +5.21 | +2.66 | 6 |
| 2026-07-21 | -0.41 | -4.22 | -9.23 | 8 |
| 2026-07-24 | +0.38 | -3.50 | -7.28 | 8 |
| 2026-07-27 | -0.20 | -3.70 | -6.38 | 7 |
| 2026-07-28 | +0.11 | -7.09 | -7.29 | 8 |
| 2026-07-29 | -0.68 | -4.05 | -7.04 | 7 |
| 2026-07-30 | -0.25 | -7.60 | -8.44 | 8 |
| 2026-07-31 | -0.35 | -7.40 | -11.44 | 7 |
| 2026-08-03 | -0.03 | -6.81 | -9.14 | 7 |
| 2026-08-04 | +0.27 | +0.37 | -1.49 | 6 |
| 2026-08-05 | -0.51 | -2.04 | -0.48 | 7 |
| 2026-08-06 | +0.00 | +0.85 | +1.69 | 8 |
| 2026-08-07 | -0.13 | -1.33 | -2.37 | 9 |
| 2026-08-10 | -0.31 | -1.31 | +0.35 | 8 |
| 2026-08-11 | -0.39 | -2.31 | — | 6 |
| 2026-08-12 | -0.55 | +5.38 | — | 6 |
| 2026-08-13 | -0.05 | +0.55 | — | 8 |
| 2026-08-17 | -0.80 | +3.41 | — | 5 |
| 2026-08-18 | -0.81 | — | — | 7 |
| 2026-08-19 | +0.37 | — | — | 6 |
| 2026-08-20 | +0.24 | — | — | 6 |

## Q2 — finalists by lane and by early_stop_reason (dedupe LAST; role=finalist only)

### by lane

| group | measure | n_rows | n_days | pooled mean | t_pool | day-mean | t_day | t_NW | hit>0 | share days<0 |
|---|---|---|---|---|---|---|---|---|---|---|
| reversion | gap_c1_o2 − mkt median (pp) | 168 | 13 | +0.15 | +1.81 | -0.01 | -0.05 | n/a | 0.54 | 0.38 |
| reversion | fwd_5_oc − mkt median (pp) | 169 | 13 | +1.52 | +3.28 | +2.29 | +2.11 | +2.72 | 0.62 | 0.15 |
| reversion | fwd_10_oc − mkt median (pp) | 169 | 13 | +2.63 | +3.48 | +2.57 | +1.62 | +4.11 | 0.63 | 0.23 |
| healthy | gap_c1_o2 − mkt median (pp) | 158 | 29 | -0.29 | -4.05 | -0.28 | -3.18 | n/a | 0.32 | 0.79 |
| healthy | fwd_5_oc − mkt median (pp) | 148 | 26 | -1.53 | -2.71 | -1.09 | -1.06 | -0.78 | 0.38 | 0.62 |
| healthy | fwd_10_oc − mkt median (pp) | 127 | 22 | -3.18 | -3.42 | -2.77 | -1.71 | -1.50 | 0.36 | 0.68 |
| trend | gap_c1_o2 − mkt median (pp) | 130 | 13 | -0.70 | -5.22 | -0.53 | -1.88 | n/a | 0.34 | 0.77 |
| trend | fwd_5_oc − mkt median (pp) | 135 | 13 | +0.37 | +0.41 | -0.43 | -0.32 | -0.34 | 0.45 | 0.46 |
| trend | fwd_10_oc − mkt median (pp) | 135 | 13 | -3.36 | -3.16 | -3.59 | -2.31 | -2.18 | 0.38 | 0.77 |
| value | gap_c1_o2 − mkt median (pp) | 23 | 17 | -0.20 | -1.99 | -0.18 | -1.60 | n/a | 0.26 | 0.59 |
| value | fwd_5_oc − mkt median (pp) | 17 | 14 | -2.36 | -2.27 | -1.71 | -1.48 | -1.05 | 0.35 | 0.57 |
| value | fwd_10_oc − mkt median (pp) | 14 | 12 | -1.90 | -1.00 | -1.25 | -0.60 | -0.59 | 0.50 | 0.50 |
| growth | gap_c1_o2 − mkt median (pp) | 17 | 10 | -0.51 | -1.64 | -0.79 | -2.36 | n/a | 0.35 | 0.70 |
| growth | fwd_5_oc − mkt median (pp) | 18 | 10 | -0.16 | -0.06 | +0.04 | +0.01 | +0.02 | 0.44 | 0.50 |
| growth | fwd_10_oc − mkt median (pp) | 17 | 9 | -4.96 | -1.50 | -2.20 | -0.56 | -0.84 | 0.53 | 0.56 |
| carryover | gap_c1_o2 − mkt median (pp) | 16 | 6 | -0.18 | -0.46 | -0.49 | -1.20 | n/a | 0.31 | 0.83 |
| carryover | fwd_5_oc − mkt median (pp) | 18 | 6 | -5.13 | -3.42 | -3.66 | -2.02 | -2.51 | 0.28 | 0.67 |
| carryover | fwd_10_oc − mkt median (pp) | 18 | 6 | -11.56 | -4.04 | -10.04 | -2.24 | -6.68 | 0.28 | 0.83 |
| accumulation | gap_c1_o2 − mkt median (pp) | 10 | 9 | +0.24 | +0.72 | +0.29 | +0.81 | n/a | 0.50 | 0.44 |
| accumulation | fwd_5_oc − mkt median (pp) | 8 | 7 | -2.91 | -1.21 | -2.44 | -0.94 | -1.72 | 0.38 | 0.57 |
| accumulation | fwd_10_oc − mkt median (pp) | 8 | 7 | -4.94 | -1.21 | -4.61 | -1.02 | -1.95 | 0.38 | 0.57 |
| main | gap_c1_o2 − mkt median (pp) | 5 | 3 | +0.03 | +0.22 | +0.05 | +0.39 | n/a | 0.80 | 0.33 |
| main | fwd_5_oc − mkt median (pp) | 5 | 3 | -4.84 | -1.18 | -3.83 | -1.46 | -3.47 | 0.60 | 0.67 |
| main | fwd_10_oc − mkt median (pp) | 5 | 3 | -4.95 | -0.92 | -4.62 | -1.48 | -6.62 | 0.20 | 1.00 |

### by early_stop_reason (rows with a reason = L4 early-stopped the DD; 'none' = full card)

| group | measure | n_rows | n_days | pooled mean | t_pool | day-mean | t_day | t_NW | hit>0 | share days<0 |
|---|---|---|---|---|---|---|---|---|---|---|
| none | gap_c1_o2 − mkt median (pp) | 429 | 35 | -0.24 | -3.99 | -0.25 | -2.13 | n/a | 0.41 | 0.77 |
| none | fwd_5_oc − mkt median (pp) | 438 | 35 | +0.01 | +0.03 | -1.26 | -1.54 | -1.11 | 0.50 | 0.57 |
| none | fwd_10_oc − mkt median (pp) | 436 | 33 | -1.30 | -2.26 | -2.57 | -2.42 | -2.04 | 0.49 | 0.61 |
| 资金流出 | gap_c1_o2 − mkt median (pp) | 30 | 14 | -0.05 | -0.32 | -0.08 | -0.50 | n/a | 0.37 | 0.71 |
| 资金流出 | fwd_5_oc − mkt median (pp) | 29 | 13 | -1.77 | -1.58 | -2.01 | -1.53 | -1.01 | 0.38 | 0.77 |
| 资金流出 | fwd_10_oc − mkt median (pp) | 20 | 9 | -3.39 | -2.48 | -4.04 | -2.78 | -2.56 | 0.35 | 0.78 |
| 题材透支 | gap_c1_o2 − mkt median (pp) | 27 | 15 | -0.09 | -0.48 | -0.06 | -0.43 | n/a | 0.44 | 0.53 |
| 题材透支 | fwd_5_oc − mkt median (pp) | 22 | 12 | -1.25 | -0.89 | -0.49 | -0.27 | -0.26 | 0.36 | 0.58 |
| 题材透支 | fwd_10_oc − mkt median (pp) | 18 | 9 | -3.78 | -1.99 | -3.21 | -1.30 | -3.13 | 0.33 | 0.56 |
| 基本面恶化 | gap_c1_o2 − mkt median (pp) | 20 | 13 | -0.11 | -0.67 | -0.09 | -0.60 | n/a | 0.40 | 0.46 |
| 基本面恶化 | fwd_5_oc − mkt median (pp) | 13 | 10 | -3.65 | -3.30 | -3.76 | -3.51 | -3.52 | 0.23 | 0.90 |
| 基本面恶化 | fwd_10_oc − mkt median (pp) | 10 | 7 | -7.80 | -5.37 | -6.85 | -3.74 | -5.75 | 0.10 | 0.86 |
| 其他 | gap_c1_o2 − mkt median (pp) | 17 | 13 | -0.48 | -2.00 | -0.39 | -1.46 | n/a | 0.41 | 0.46 |
| 其他 | fwd_5_oc − mkt median (pp) | 13 | 10 | -1.35 | -0.99 | -1.47 | -0.84 | -0.86 | 0.31 | 0.80 |
| 其他 | fwd_10_oc − mkt median (pp) | 6 | 6 | -6.23 | -2.91 | -6.23 | -2.91 | -4.38 | 0.17 | 0.83 |
| 涨停追高 | gap_c1_o2 − mkt median (pp) | 5 | 5 | -1.54 | -3.50 | -1.54 | -3.50 | n/a | 0.00 | 1.00 |
| 涨停追高 | fwd_5_oc − mkt median (pp) | 4 | 4 | -3.51 | -3.01 | -3.51 | -3.01 | -10.12 | 0.00 | 1.00 |
| 涨停追高 | fwd_10_oc − mkt median (pp) | 4 | 4 | -4.31 | -1.69 | -4.31 | -1.69 | -5.15 | 0.25 | 0.75 |

Note: early_stop_reason only exists from 07-10 (rated era). 'none' therefore mixes the 30-finalist unrated era with full-card rows; below the same table restricted to the rated era (≥07-10):

| group | measure | n_rows | n_days | pooled mean | t_pool | day-mean | t_day | t_NW | hit>0 | share days<0 |
|---|---|---|---|---|---|---|---|---|---|---|
| none | gap_c1_o2 − mkt median (pp) | 83 | 21 | -0.29 | -2.53 | -0.25 | -1.42 | n/a | 0.27 | 0.76 |
| none | fwd_5_oc − mkt median (pp) | 83 | 21 | -0.67 | -0.89 | -1.66 | -1.45 | -0.94 | 0.46 | 0.67 |
| none | fwd_10_oc − mkt median (pp) | 81 | 19 | -2.46 | -1.96 | -3.12 | -1.97 | -1.59 | 0.42 | 0.68 |
| 资金流出 | gap_c1_o2 − mkt median (pp) | 30 | 14 | -0.05 | -0.32 | -0.08 | -0.50 | n/a | 0.37 | 0.71 |
| 资金流出 | fwd_5_oc − mkt median (pp) | 29 | 13 | -1.77 | -1.58 | -2.01 | -1.53 | -1.01 | 0.38 | 0.77 |
| 资金流出 | fwd_10_oc − mkt median (pp) | 20 | 9 | -3.39 | -2.48 | -4.04 | -2.78 | -2.56 | 0.35 | 0.78 |
| 题材透支 | gap_c1_o2 − mkt median (pp) | 27 | 15 | -0.09 | -0.48 | -0.06 | -0.43 | n/a | 0.44 | 0.53 |
| 题材透支 | fwd_5_oc − mkt median (pp) | 22 | 12 | -1.25 | -0.89 | -0.49 | -0.27 | -0.26 | 0.36 | 0.58 |
| 题材透支 | fwd_10_oc − mkt median (pp) | 18 | 9 | -3.78 | -1.99 | -3.21 | -1.30 | -3.13 | 0.33 | 0.56 |
| 基本面恶化 | gap_c1_o2 − mkt median (pp) | 20 | 13 | -0.11 | -0.67 | -0.09 | -0.60 | n/a | 0.40 | 0.46 |
| 基本面恶化 | fwd_5_oc − mkt median (pp) | 13 | 10 | -3.65 | -3.30 | -3.76 | -3.51 | -3.52 | 0.23 | 0.90 |
| 基本面恶化 | fwd_10_oc − mkt median (pp) | 10 | 7 | -7.80 | -5.37 | -6.85 | -3.74 | -5.75 | 0.10 | 0.86 |
| 其他 | gap_c1_o2 − mkt median (pp) | 17 | 13 | -0.48 | -2.00 | -0.39 | -1.46 | n/a | 0.41 | 0.46 |
| 其他 | fwd_5_oc − mkt median (pp) | 13 | 10 | -1.35 | -0.99 | -1.47 | -0.84 | -0.86 | 0.31 | 0.80 |
| 其他 | fwd_10_oc − mkt median (pp) | 6 | 6 | -6.23 | -2.91 | -6.23 | -2.91 | -4.38 | 0.17 | 0.83 |
| 涨停追高 | gap_c1_o2 − mkt median (pp) | 5 | 5 | -1.54 | -3.50 | -1.54 | -3.50 | n/a | 0.00 | 1.00 |
| 涨停追高 | fwd_5_oc − mkt median (pp) | 4 | 4 | -3.51 | -3.01 | -3.51 | -3.01 | -10.12 | 0.00 | 1.00 |
| 涨停追高 | fwd_10_oc − mkt median (pp) | 4 | 4 | -4.31 | -1.69 | -4.31 | -1.69 | -5.15 | 0.25 | 0.75 |

## Q3 — L4 rating face vs outcome (all rated rows: finalist+pinned+active BUY; shadow BUY excluded)

| group | measure | n_rows | n_days | pooled mean | t_pool | day-mean | t_day | t_NW | hit>0 | share days<0 |
|---|---|---|---|---|---|---|---|---|---|---|
| Overweight | gap_c1_o2 − mkt median (pp) | 2 | 2 | -0.28 | -0.33 | -0.28 | -0.33 | n/a | 0.50 | 0.50 |
| Overweight | fwd_5_oc − mkt median (pp) | 2 | 2 | +4.74 | +1.21 | +4.74 | +1.21 | — | 1.00 | 0.00 |
| Overweight | fwd_10_oc − mkt median (pp) | 2 | 2 | +8.62 | +8.04 | +8.62 | +8.04 | — | 1.00 | 0.00 |
| Hold | gap_c1_o2 − mkt median (pp) | 171 | 26 | -0.21 | -1.24 | -0.19 | -1.19 | n/a | 0.38 | 0.62 |
| Hold | fwd_5_oc − mkt median (pp) | 156 | 23 | -0.67 | -1.05 | -0.55 | -0.60 | -0.44 | 0.43 | 0.57 |
| Hold | fwd_10_oc − mkt median (pp) | 132 | 19 | -2.27 | -2.06 | -2.05 | -1.27 | -1.34 | 0.40 | 0.63 |
| Underweight | gap_c1_o2 − mkt median (pp) | 84 | 26 | +0.18 | +0.72 | +0.46 | +0.78 | n/a | 0.39 | 0.50 |
| Underweight | fwd_5_oc − mkt median (pp) | 74 | 23 | -0.67 | -0.57 | +0.79 | +0.50 | +0.41 | 0.46 | 0.35 |
| Underweight | fwd_10_oc − mkt median (pp) | 63 | 19 | -2.96 | -1.75 | -1.78 | -0.74 | -0.46 | 0.43 | 0.37 |
| Sell | gap_c1_o2 − mkt median (pp) | 10 | 8 | +2.68 | +1.83 | +2.94 | +1.65 | n/a | 0.60 | 0.38 |
| Sell | fwd_5_oc − mkt median (pp) | 8 | 7 | +2.19 | +0.82 | +2.20 | +0.77 | +0.90 | 0.75 | 0.14 |
| Sell | fwd_10_oc − mkt median (pp) | 7 | 6 | -6.96 | -1.21 | -4.33 | -0.74 | -1.03 | 0.43 | 0.50 |

Finalist-only rated rows:

| group | measure | n_rows | n_days | pooled mean | t_pool | day-mean | t_day | t_NW | hit>0 | share days<0 |
|---|---|---|---|---|---|---|---|---|---|---|
| Overweight | gap_c1_o2 − mkt median (pp) | 2 | 2 | -0.28 | -0.33 | -0.28 | -0.33 | n/a | 0.50 | 0.50 |
| Overweight | fwd_5_oc − mkt median (pp) | 2 | 2 | +4.74 | +1.21 | +4.74 | +1.21 | — | 1.00 | 0.00 |
| Overweight | fwd_10_oc − mkt median (pp) | 2 | 2 | +8.62 | +8.04 | +8.62 | +8.04 | — | 1.00 | 0.00 |
| Hold | gap_c1_o2 − mkt median (pp) | 136 | 26 | -0.23 | -2.55 | -0.23 | -2.30 | n/a | 0.35 | 0.69 |
| Hold | fwd_5_oc − mkt median (pp) | 125 | 23 | -0.97 | -1.71 | -0.75 | -0.74 | -0.48 | 0.39 | 0.61 |
| Hold | fwd_10_oc − mkt median (pp) | 107 | 19 | -3.07 | -3.38 | -2.83 | -1.74 | -1.20 | 0.36 | 0.74 |
| Underweight | gap_c1_o2 − mkt median (pp) | 44 | 21 | -0.28 | -2.34 | -0.37 | -2.39 | n/a | 0.30 | 0.76 |
| Underweight | fwd_5_oc − mkt median (pp) | 37 | 18 | -2.28 | -2.25 | -2.87 | -2.13 | -1.49 | 0.35 | 0.67 |
| Underweight | fwd_10_oc − mkt median (pp) | 30 | 14 | -4.46 | -2.34 | -6.40 | -2.37 | -2.20 | 0.33 | 0.71 |
| Sell | gap_c1_o2 − mkt median (pp) | 1 | 1 | -0.98 | — | -0.98 | — | n/a | 0.00 | 1.00 |
| Sell | fwd_5_oc − mkt median (pp) | 1 | 1 | -14.28 | — | -14.28 | — | — | 0.00 | 1.00 |
| Sell | fwd_10_oc − mkt median (pp) | 1 | 1 | -19.53 | — | -19.53 | — | — | 0.00 | 1.00 |

Spearman(rating ordinal Sell=1..OW=4, outcome): pooled rho/p, and per-date rank-IC (dates with ≥5 rated rows) mean & t:

| scope | outcome | n | pooled rho | p | n_days IC | mean IC | t(IC) |
|---|---|---|---|---|---|---|---|
| all rated | gap_c1_o2 − mkt median (pp) | 267 | -0.041 | +0.507 | 26 | +0.025 | +0.36 |
| all rated | fwd_5_oc − mkt median (pp) | 240 | -0.029 | +0.655 | 23 | -0.124 | -1.69 |
| all rated | fwd_10_oc − mkt median (pp) | 204 | +0.013 | +0.853 | 19 | -0.136 | -1.23 |
| finalist rated | gap_c1_o2 − mkt median (pp) | 183 | +0.093 | +0.212 | 21 | +0.176 | +1.63 |
| finalist rated | fwd_5_oc − mkt median (pp) | 165 | +0.101 | +0.196 | 18 | -0.051 | -0.54 |
| finalist rated | fwd_10_oc − mkt median (pp) | 140 | +0.063 | +0.460 | 14 | -0.211 | -1.92 |

## Q4 — T+1 strong close (t1_pos_in_range ≥0.7) vs weak (<0.3)


### all roles (finalist+pinned+BUY-active)

| group | measure | n_rows | n_days | pooled mean | t_pool | day-mean | t_day | t_NW | hit>0 | share days<0 |
|---|---|---|---|---|---|---|---|---|---|---|
| ≥0.7 strong | gap_c1_o2 − mkt median (pp) | 162 | 38 | -0.34 | -2.16 | -0.31 | -2.01 | n/a | 0.33 | 0.74 |
| ≥0.7 strong | fwd_5_oc − mkt median (pp) | 165 | 35 | +1.42 | +2.05 | +3.53 | +2.78 | +2.54 | 0.56 | 0.29 |
| ≥0.7 strong | fwd_10_oc − mkt median (pp) | 159 | 31 | +0.43 | +0.46 | +3.58 | +2.05 | +2.14 | 0.53 | 0.35 |
| ≥0.7 strong | ALIGNED c1→c5 − mkt median (pp) | 155 | 35 | -1.61 | -2.49 | +0.21 | +0.21 | +0.17 | 0.44 | 0.46 |
| ≥0.7 strong | ALIGNED c1→c10 − mkt median (pp) | 149 | 31 | -1.95 | -2.18 | +0.78 | +0.52 | +0.51 | 0.44 | 0.52 |
| mid | gap_c1_o2 − mkt median (pp) | 195 | 39 | -0.20 | -1.95 | -0.15 | -1.17 | n/a | 0.44 | 0.51 |
| mid | fwd_5_oc − mkt median (pp) | 187 | 36 | +1.85 | +2.90 | +0.87 | +1.14 | +0.84 | 0.56 | 0.39 |
| mid | fwd_10_oc − mkt median (pp) | 173 | 32 | -0.09 | -0.09 | -0.58 | -0.54 | -0.46 | 0.49 | 0.56 |
| mid | ALIGNED c1→c5 − mkt median (pp) | 187 | 36 | +1.81 | +2.93 | +0.82 | +1.19 | +1.12 | 0.56 | 0.42 |
| mid | ALIGNED c1→c10 − mkt median (pp) | 173 | 32 | -0.11 | -0.12 | -0.47 | -0.46 | -0.43 | 0.49 | 0.53 |
| <0.3 weak | gap_c1_o2 − mkt median (pp) | 257 | 37 | +0.08 | +0.59 | +0.32 | +1.15 | n/a | 0.46 | 0.41 |
| <0.3 weak | fwd_5_oc − mkt median (pp) | 244 | 34 | -2.64 | -5.08 | -3.07 | -3.12 | -2.61 | 0.39 | 0.76 |
| <0.3 weak | fwd_10_oc − mkt median (pp) | 228 | 30 | -4.12 | -5.05 | -5.53 | -3.67 | -3.11 | 0.42 | 0.77 |
| <0.3 weak | ALIGNED c1→c5 − mkt median (pp) | 244 | 34 | +0.18 | +0.36 | +0.18 | +0.20 | +0.20 | 0.50 | 0.56 |
| <0.3 weak | ALIGNED c1→c10 − mkt median (pp) | 228 | 30 | -1.34 | -1.73 | -2.16 | -1.52 | -1.53 | 0.50 | 0.60 |

Strong − weak difference:

| measure | n_strong | n_weak | diff pooled (pp) | Welch t | p | n_days paired | diff day-mean | t_day |
|---|---|---|---|---|---|---|---|---|
| gap_c1_o2 − mkt median (pp) | 162 | 257 | -0.42 | -2.05 | +0.041 | 35 | -0.60 | -2.22 |
| fwd_5_oc − mkt median (pp) | 165 | 244 | +4.06 | +4.70 | +0.000 | 32 | +6.89 | +4.50 |
| fwd_10_oc − mkt median (pp) | 159 | 228 | +4.54 | +3.67 | +0.000 | 28 | +9.41 | +4.15 |
| ALIGNED c1→c5 − mkt median (pp) | 155 | 244 | -1.80 | -2.19 | +0.029 | 32 | +0.15 | +0.11 |
| ALIGNED c1→c10 − mkt median (pp) | 149 | 228 | -0.61 | -0.51 | +0.609 | 28 | +3.00 | +1.36 |

### finalist only

| group | measure | n_rows | n_days | pooled mean | t_pool | day-mean | t_day | t_NW | hit>0 | share days<0 |
|---|---|---|---|---|---|---|---|---|---|---|
| ≥0.7 strong | gap_c1_o2 − mkt median (pp) | 135 | 35 | -0.43 | -4.53 | -0.50 | -4.51 | n/a | 0.31 | 0.77 |
| ≥0.7 strong | fwd_5_oc − mkt median (pp) | 141 | 32 | +0.65 | +0.93 | +2.31 | +2.07 | +1.67 | 0.52 | 0.31 |
| ≥0.7 strong | fwd_10_oc − mkt median (pp) | 137 | 28 | -1.18 | -1.32 | +1.29 | +0.80 | +0.55 | 0.47 | 0.43 |
| ≥0.7 strong | ALIGNED c1→c5 − mkt median (pp) | 131 | 32 | -2.19 | -3.30 | -0.46 | -0.54 | -0.47 | 0.40 | 0.47 |
| ≥0.7 strong | ALIGNED c1→c10 − mkt median (pp) | 127 | 28 | -3.23 | -3.62 | -0.79 | -0.56 | -0.40 | 0.39 | 0.57 |
| mid | gap_c1_o2 − mkt median (pp) | 174 | 38 | -0.20 | -2.24 | -0.18 | -1.66 | n/a | 0.41 | 0.58 |
| mid | fwd_5_oc − mkt median (pp) | 168 | 35 | +1.73 | +2.56 | +0.21 | +0.26 | +0.18 | 0.55 | 0.46 |
| mid | fwd_10_oc − mkt median (pp) | 158 | 31 | -0.05 | -0.05 | -1.02 | -0.91 | -0.69 | 0.50 | 0.55 |
| mid | ALIGNED c1→c5 − mkt median (pp) | 168 | 35 | +1.63 | +2.50 | +0.19 | +0.27 | +0.22 | 0.54 | 0.54 |
| mid | ALIGNED c1→c10 − mkt median (pp) | 158 | 31 | -0.18 | -0.18 | -0.92 | -0.88 | -0.73 | 0.49 | 0.58 |
| <0.3 weak | gap_c1_o2 − mkt median (pp) | 221 | 35 | -0.15 | -1.75 | -0.11 | -0.99 | n/a | 0.45 | 0.51 |
| <0.3 weak | fwd_5_oc − mkt median (pp) | 212 | 32 | -2.50 | -5.20 | -2.64 | -3.15 | -2.75 | 0.38 | 0.78 |
| <0.3 weak | fwd_10_oc − mkt median (pp) | 201 | 28 | -3.30 | -4.29 | -4.53 | -3.50 | -4.31 | 0.42 | 0.71 |
| <0.3 weak | ALIGNED c1→c5 − mkt median (pp) | 212 | 32 | -0.21 | -0.47 | -0.49 | -0.61 | -0.53 | 0.50 | 0.59 |
| <0.3 weak | ALIGNED c1→c10 − mkt median (pp) | 201 | 28 | -1.06 | -1.48 | -2.31 | -2.00 | -2.15 | 0.49 | 0.64 |

Strong − weak difference:

| measure | n_strong | n_weak | diff pooled (pp) | Welch t | p | n_days paired | diff day-mean | t_day |
|---|---|---|---|---|---|---|---|---|
| gap_c1_o2 − mkt median (pp) | 135 | 221 | -0.28 | -2.17 | +0.031 | 30 | -0.40 | -2.22 |
| fwd_5_oc − mkt median (pp) | 141 | 212 | +3.15 | +3.71 | +0.000 | 27 | +4.81 | +3.84 |
| fwd_10_oc − mkt median (pp) | 137 | 201 | +2.13 | +1.81 | +0.072 | 23 | +6.18 | +3.26 |
| ALIGNED c1→c5 − mkt median (pp) | 131 | 212 | -1.98 | -2.47 | +0.014 | 27 | -0.12 | -0.10 |
| ALIGNED c1→c10 − mkt median (pp) | 127 | 201 | -2.17 | -1.89 | +0.060 | 23 | +1.90 | +1.05 |

### All-lake control on the same analysis dates (every tradable stock, same definitions)

| measure | group | n_days | median n/day | day-mean rel (pp) | t_day | share days<0 |
|---|---|---|---|---|---|---|
| gap_c1_o2 | ≥0.7 strong | 40 | 1450 | -0.22 | -3.35 | 0.62 |
| gap_c1_o2 | <0.3 weak | 40 | 1495 | -0.03 | -0.40 | 0.47 |
| gap_c1_o2 | strong−weak | 40 |  | -0.19 | -1.51 | 0.55 |
| fwd_5_oc | ≥0.7 strong | 37 | 1480 | +3.44 | +6.85 | 0.11 |
| fwd_5_oc | <0.3 weak | 37 | 1535 | -2.56 | -4.66 | 0.89 |
| fwd_5_oc | strong−weak | 37 |  | +6.00 | +7.76 | 0.11 |
| fwd_10_oc | ≥0.7 strong | 33 | 1764 | +2.95 | +4.62 | 0.12 |
| fwd_10_oc | <0.3 weak | 33 | 1535 | -3.36 | -3.97 | 0.79 |
| fwd_10_oc | strong−weak | 33 |  | +6.31 | +5.05 | 0.21 |
| c1c5 | ≥0.7 strong | 37 | 1408 | +0.10 | +0.29 | 0.38 |
| c1c5 | <0.3 weak | 37 | 1535 | +0.28 | +0.55 | 0.41 |
| c1c5 | strong−weak | 37 |  | -0.17 | -0.23 | 0.41 |
| c1c10 | ≥0.7 strong | 33 | 1692 | -0.19 | -0.30 | 0.45 |
| c1c10 | <0.3 weak | 33 | 1535 | -0.36 | -0.45 | 0.61 |
| c1c10 | strong−weak | 33 |  | +0.17 | +0.14 | 0.48 |

## Q5 — pinned rows (role=pinned; dedupe LAST)

n_rows=82, n_days=26, distinct codes=11: 300857(24), 688766(19), 601869(15), 920179(6), 002371(5), 300750(4), 300033(2), 603799(2), 688271(2), 601688(2), 600549(1)
(before dedupe: n_rows=99, distinct codes=11; plus 2 shadow-BUY rows on 688766 that were pinned-lane)
| group | measure | n_rows | n_days | pooled mean | t_pool | day-mean | t_day | t_NW | hit>0 | share days<0 |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-07 | gap_c1_o2 − mkt median (pp) | 46 | 13 | +0.92 | +1.25 | +1.02 | +0.72 | n/a | 0.50 | 0.54 |
| 2026-07 | fwd_5_oc − mkt median (pp) | 46 | 13 | -2.21 | -1.15 | -2.10 | -0.72 | -0.64 | 0.48 | 0.54 |
| 2026-07 | fwd_10_oc − mkt median (pp) | 46 | 13 | -5.48 | -2.12 | -4.19 | -1.01 | -0.93 | 0.48 | 0.62 |
| 2026-08 | gap_c1_o2 − mkt median (pp) | 36 | 13 | +0.22 | +0.53 | +0.23 | +0.37 | n/a | 0.56 | 0.46 |
| 2026-08 | fwd_5_oc − mkt median (pp) | 29 | 10 | +6.36 | +4.04 | +6.19 | +2.95 | +3.88 | 0.79 | 0.10 |
| 2026-08 | fwd_10_oc − mkt median (pp) | 18 | 6 | +11.02 | +3.40 | +11.02 | +2.46 | +3.73 | 0.72 | 0.33 |

ALL months:

| group | measure | n_rows | n_days | pooled mean | t_pool | day-mean | t_day | t_NW | hit>0 | share days<0 |
|---|---|---|---|---|---|---|---|---|---|---|
| ALL | gap_c1_o2 − mkt median (pp) | 82 | 26 | +0.61 | +1.36 | +0.62 | +0.82 | n/a | 0.52 | 0.50 |
| ALL | fwd_5_oc − mkt median (pp) | 75 | 23 | +1.10 | +0.78 | +1.51 | +0.73 | +0.50 | 0.60 | 0.35 |
| ALL | fwd_10_oc − mkt median (pp) | 64 | 19 | -0.84 | -0.37 | +0.61 | +0.17 | +0.11 | 0.55 | 0.53 |

Per code:

| group | measure | n_rows | n_days | pooled mean | t_pool | day-mean | t_day | t_NW | hit>0 | share days<0 |
|---|---|---|---|---|---|---|---|---|---|---|
| 300857 | gap_c1_o2 − mkt median (pp) | 24 | 24 | +1.10 | +1.37 | +1.10 | +1.37 | n/a | 0.54 | 0.46 |
| 300857 | fwd_5_oc − mkt median (pp) | 21 | 21 | +2.07 | +1.05 | +2.07 | +1.05 | +0.94 | 0.62 | 0.38 |
| 300857 | fwd_10_oc − mkt median (pp) | 17 | 17 | +1.05 | +0.38 | +1.05 | +0.38 | +0.23 | 0.59 | 0.41 |
| 688766 | gap_c1_o2 − mkt median (pp) | 19 | 19 | +0.87 | +0.63 | +0.87 | +0.63 | n/a | 0.53 | 0.47 |
| 688766 | fwd_5_oc − mkt median (pp) | 18 | 18 | -2.15 | -0.53 | -2.15 | -0.53 | -0.33 | 0.50 | 0.50 |
| 688766 | fwd_10_oc − mkt median (pp) | 15 | 15 | -2.24 | -0.30 | -2.24 | -0.30 | -0.17 | 0.53 | 0.47 |
| 601869 | gap_c1_o2 − mkt median (pp) | 15 | 15 | -0.73 | -1.05 | -0.73 | -1.05 | n/a | 0.53 | 0.47 |
| 601869 | fwd_5_oc − mkt median (pp) | 13 | 13 | -4.37 | -1.26 | -4.37 | -1.26 | -0.91 | 0.38 | 0.62 |
| 601869 | fwd_10_oc − mkt median (pp) | 9 | 9 | -14.89 | -2.50 | -14.89 | -2.50 | -2.28 | 0.22 | 0.78 |
| 920179 | gap_c1_o2 − mkt median (pp) | 6 | 6 | +2.32 | +1.00 | +2.32 | +1.00 | n/a | 0.50 | 0.50 |
| 920179 | fwd_5_oc − mkt median (pp) | 6 | 6 | +12.08 | +3.46 | +12.08 | +3.46 | +6.07 | 1.00 | 0.00 |
| 920179 | fwd_10_oc − mkt median (pp) | 6 | 6 | +11.60 | +3.90 | +11.60 | +3.90 | +11.45 | 1.00 | 0.00 |
| 002371 | gap_c1_o2 − mkt median (pp) | 5 | 5 | +1.10 | +1.10 | +1.10 | +1.10 | n/a | 0.60 | 0.40 |
| 002371 | fwd_5_oc − mkt median (pp) | 5 | 5 | +4.29 | +0.98 | +4.29 | +0.98 | +1.93 | 0.60 | 0.40 |
| 002371 | fwd_10_oc − mkt median (pp) | 5 | 5 | -6.41 | -1.92 | -6.41 | -1.92 | -4.16 | 0.00 | 1.00 |
| 300750 | gap_c1_o2 − mkt median (pp) | 4 | 4 | -0.15 | -0.22 | -0.15 | -0.22 | n/a | 0.50 | 0.50 |
| 300750 | fwd_5_oc − mkt median (pp) | 4 | 4 | +5.44 | +2.50 | +5.44 | +2.50 | +3.98 | 1.00 | 0.00 |
| 300750 | fwd_10_oc − mkt median (pp) | 4 | 4 | +8.88 | +1.94 | +8.88 | +1.94 | +4.15 | 0.75 | 0.25 |
| 300033 | gap_c1_o2 − mkt median (pp) | 2 | 2 | +0.06 | +0.26 | +0.06 | +0.26 | n/a | 0.50 | 0.50 |
| 300033 | fwd_5_oc − mkt median (pp) | 2 | 2 | +4.36 | +1.44 | +4.36 | +1.44 | — | 1.00 | 0.00 |
| 300033 | fwd_10_oc − mkt median (pp) | 2 | 2 | +3.58 | +7.94 | +3.58 | +7.94 | — | 1.00 | 0.00 |
| 603799 | gap_c1_o2 − mkt median (pp) | 2 | 2 | -0.69 | -11.39 | -0.69 | -11.39 | n/a | 0.00 | 1.00 |
| 603799 | fwd_5_oc − mkt median (pp) | 2 | 2 | -4.50 | -35.30 | -4.50 | -35.30 | — | 0.00 | 1.00 |
| 603799 | fwd_10_oc − mkt median (pp) | 2 | 2 | +4.14 | +163.23 | +4.14 | +163.23 | — | 1.00 | 0.00 |
| 688271 | gap_c1_o2 − mkt median (pp) | 2 | 2 | -0.41 | -1.41 | -0.41 | -1.41 | n/a | 0.00 | 1.00 |
| 688271 | fwd_5_oc − mkt median (pp) | 2 | 2 | +9.64 | +2.96 | +9.64 | +2.96 | — | 1.00 | 0.00 |
| 688271 | fwd_10_oc − mkt median (pp) | 2 | 2 | +6.76 | +3.09 | +6.76 | +3.09 | — | 1.00 | 0.00 |
| 601688 | gap_c1_o2 − mkt median (pp) | 2 | 2 | +0.22 | +1.87 | +0.22 | +1.87 | n/a | 1.00 | 0.00 |
| 601688 | fwd_5_oc − mkt median (pp) | 2 | 2 | +0.04 | +0.10 | +0.04 | +0.10 | — | 0.50 | 0.50 |
| 601688 | fwd_10_oc − mkt median (pp) | 2 | 2 | -2.96 | -5.77 | -2.96 | -5.77 | — | 0.00 | 1.00 |
| 600549 | gap_c1_o2 − mkt median (pp) | 1 | 1 | +1.22 | — | +1.22 | — | n/a | 1.00 | 0.00 |

## Q6 — L3 conviction (0–100) vs outcome, Spearman (finalists only; pinned have conviction too — shown separately)

| scope | outcome | n | pooled rho | p | n_days IC | mean IC | t(IC) |
|---|---|---|---|---|---|---|---|
| finalist ALL | gap_c1_o2 − mkt median (pp) | 519 | -0.091 | +0.038 | 39 | -0.046 | -0.81 |
| finalist ALL | fwd_5_oc − mkt median (pp) | 508 | -0.042 | +0.345 | 36 | -0.095 | -1.44 |
| finalist ALL | fwd_10_oc − mkt median (pp) | 483 | -0.090 | +0.047 | 32 | -0.135 | -2.21 |
| finalist P1 ≤07-02 reversal | gap_c1_o2 − mkt median (pp) | 273 | -0.106 | +0.081 | 10 | -0.060 | -1.41 |
| finalist P1 ≤07-02 reversal | fwd_5_oc − mkt median (pp) | 279 | +0.030 | +0.624 | 10 | -0.037 | -0.64 |
| finalist P1 ≤07-02 reversal | fwd_10_oc − mkt median (pp) | 279 | -0.141 | +0.019 | 10 | -0.032 | -0.56 |
| finalist P2 07-03..08-18 | gap_c1_o2 − mkt median (pp) | 234 | -0.028 | +0.668 | 27 | -0.047 | -0.59 |
| finalist P2 07-03..08-18 | fwd_5_oc − mkt median (pp) | 229 | -0.091 | +0.170 | 26 | -0.118 | -1.32 |
| finalist P2 07-03..08-18 | fwd_10_oc − mkt median (pp) | 204 | +0.052 | +0.463 | 22 | -0.183 | -2.17 |
| finalist P3 ≥08-19 E6-active | gap_c1_o2 − mkt median (pp) | 12 | +0.109 | +0.736 | 2 | +0.029 | — |
| finalist P3 ≥08-19 E6-active | fwd_5_oc − mkt median (pp) | 0 | — | — | 0 | — | — |
| finalist P3 ≥08-19 E6-active | fwd_10_oc − mkt median (pp) | 0 | — | — | 0 | — | — |
| finalist rated era ≥07-10 | gap_c1_o2 − mkt median (pp) | 181 | -0.069 | +0.357 | 25 | -0.062 | -0.75 |
| finalist rated era ≥07-10 | fwd_5_oc − mkt median (pp) | 163 | -0.242 | +0.002 | 22 | -0.150 | -1.49 |
| finalist rated era ≥07-10 | fwd_10_oc − mkt median (pp) | 138 | -0.164 | +0.055 | 18 | -0.252 | -2.75 |
| pinned ALL | gap_c1_o2 − mkt median (pp) | 82 | -0.122 | +0.274 | 0 | — | — |
| pinned ALL | fwd_5_oc − mkt median (pp) | 75 | +0.056 | +0.630 | 0 | — | — |
| pinned ALL | fwd_10_oc − mkt median (pp) | 64 | +0.150 | +0.238 | 0 | — | — |

conviction distribution (finalist): n=527, mean=60.8, sd=9.7, p10/50/90=46/61/73

## Q7 — src=shared sanity

analysis_dates: 40 total; **shared-only = 26** (2026-07-10..2026-08-20); mixed = 2 (['2026-07-08', '2026-07-09']); run-only = 12 (2026-06-18..2026-07-06).
Every date from 07-10 onward is shared-only → excluding shared rows removes the entire rated/E6 era; what is left is the ≤07-09 30-finalist era. Q1 finalist stats on src=run rows only (dedupe LAST):

| group | measure | n_rows | n_days | pooled mean | t_pool | day-mean | t_day | t_NW | hit>0 | share days<0 |
|---|---|---|---|---|---|---|---|---|---|---|
| all finalist | gap_c1_o2 − mkt median (pp) | 530 | 40 | -0.24 | -4.54 | -0.26 | -3.85 | n/a | 0.40 | 0.75 |
| all finalist | fwd_5_oc − mkt median (pp) | 521 | 37 | -0.28 | -0.79 | -0.76 | -1.04 | -0.78 | 0.47 | 0.51 |
| all finalist | fwd_10_oc − mkt median (pp) | 496 | 33 | -1.68 | -3.24 | -2.39 | -2.40 | -2.03 | 0.46 | 0.61 |
| src=run only | gap_c1_o2 − mkt median (pp) | 346 | 14 | -0.23 | -3.30 | -0.25 | -1.90 | n/a | 0.44 | 0.79 |
| src=run only | fwd_5_oc − mkt median (pp) | 355 | 14 | +0.17 | +0.37 | -0.66 | -0.59 | -0.46 | 0.51 | 0.43 |
| src=run only | fwd_10_oc − mkt median (pp) | 355 | 14 | -1.04 | -1.61 | -1.82 | -1.37 | -1.10 | 0.50 | 0.50 |
| src=shared only | gap_c1_o2 − mkt median (pp) | 184 | 26 | -0.25 | -3.44 | -0.27 | -3.39 | n/a | 0.33 | 0.73 |
| src=shared only | fwd_5_oc − mkt median (pp) | 166 | 23 | -1.26 | -2.57 | -0.82 | -0.84 | -0.56 | 0.39 | 0.57 |
| src=shared only | fwd_10_oc − mkt median (pp) | 141 | 19 | -3.29 | -4.04 | -2.80 | -1.94 | -1.49 | 0.36 | 0.68 |

Sensitivity — dedupe by FIRST run per date instead of LAST (finalist):

| group | measure | n_rows | n_days | pooled mean | t_pool | day-mean | t_day | t_NW | hit>0 | share days<0 |
|---|---|---|---|---|---|---|---|---|---|---|
| LAST-run dedupe | gap_c1_o2 − mkt median (pp) | 530 | 40 | -0.24 | -4.54 | -0.26 | -3.85 | n/a | 0.40 | 0.75 |
| LAST-run dedupe | fwd_5_oc − mkt median (pp) | 521 | 37 | -0.28 | -0.79 | -0.76 | -1.04 | -0.78 | 0.47 | 0.51 |
| LAST-run dedupe | fwd_10_oc − mkt median (pp) | 496 | 33 | -1.68 | -3.24 | -2.39 | -2.40 | -2.03 | 0.46 | 0.61 |
| FIRST-run dedupe | gap_c1_o2 − mkt median (pp) | 525 | 40 | -0.18 | -3.47 | -0.24 | -3.49 | n/a | 0.42 | 0.72 |
| FIRST-run dedupe | fwd_5_oc − mkt median (pp) | 519 | 37 | -0.39 | -1.12 | -0.80 | -1.11 | -0.84 | 0.48 | 0.51 |
| FIRST-run dedupe | fwd_10_oc − mkt median (pp) | 494 | 33 | -1.15 | -2.22 | -2.11 | -2.14 | -1.62 | 0.48 | 0.58 |
| no dedupe (all runs) | gap_c1_o2 − mkt median (pp) | 754 | 40 | -0.28 | -6.56 | -0.25 | -3.70 | n/a | 0.40 | 0.72 |
| no dedupe (all runs) | fwd_5_oc − mkt median (pp) | 754 | 37 | -0.97 | -2.97 | -0.78 | -1.08 | -0.81 | 0.46 | 0.51 |
| no dedupe (all runs) | fwd_10_oc − mkt median (pp) | 724 | 33 | -2.55 | -5.59 | -2.25 | -2.29 | -1.82 | 0.45 | 0.61 |
## Q1d — is the finalist 5–10d underperformance stock-specific or a beta/rally-lag effect? (per-date finalist rel vs market median move)

| horizon | dates | n_days | corr(rel, mkt) | slope | intercept (rel when mkt=0, pp) | p | mean rel on mkt-up days | mean rel on mkt-down days |
|---|---|---|---|---|---|---|---|---|
| fwd_5 | all dates | 37 | -0.65 | -0.74 | -1.29 | 0.000 | -3.17 (n=16) | +1.08 (n=21) |
| fwd_5 | rated era ≥07-10 | 23 | -0.81 | -0.94 | -0.57 | 0.000 | -3.58 (n=14) | +3.49 (n=9) |
| fwd_10 | all dates | 33 | -0.46 | -0.48 | -2.72 | 0.007 | -6.03 (n=11) | -0.56 (n=22) |
| fwd_10 | rated era ≥07-10 | 19 | -0.75 | -0.90 | -0.88 | 0.000 | -6.51 (n=10) | +1.31 (n=9) |

Reading: a strongly negative slope means finalists lag when the market rallies and hold up when it falls (low-beta / 'not-extended' selection), i.e. the 10-day 'negative selection' is partly beta-lag in the July rally, not stock-specific drift. The intercept is the market-neutral residual.


## Q2b — lane availability by period (lanes are not comparable populations: reversion/trend = June era; healthy = all eras; carryover = rated era)

| lane | 2026-06 | 2026-07 | 2026-08 |
|---|---|---|---|
| accumulation | 0 | 7 | 3 |
| carryover | 0 | 18 | 0 |
| growth | 0 | 15 | 4 |
| healthy | 0 | 91 | 67 |
| main | 0 | 4 | 1 |
| momentum | 0 | 3 | 0 |
| reversion | 128 | 41 | 0 |
| trend | 111 | 24 | 0 |
| value | 0 | 9 | 14 |

early_stop rows × rating: {'Hold': 72, 'Underweight': 30}

Q3 extra — finalist Hold − Underweight on fwd_10_rel, per-date paired: n_days=14, diff=+2.48pp, t=+0.81, share days Hold>UW=0.43
Q3 extra — same on fwd_5_rel: n_days=14, diff=+1.55pp, t=+1.02

## Bottom line + caveats

1. **Overnight (main ruler)**: finalists −0.26pp vs lake median, t_day −3.85, negative in 30/40 days and in all three months (−0.34/−0.27/−0.21) → the one robust negative reading. Pinned +0.62pp (t 0.82, n.s.). Active BUY n=2 (+0.13pp) — not measurable.
2. **5–10 days**: finalists fwd_5_rel −0.76pp (t_day −1.04, NW −0.78: nothing); fwd_10_rel −2.39pp (t_day −2.40, NW −2.03) — but sign is **not** stable: June +0.32 (75% of days ≥0), July −3.67 (t −2.42), Aug −1.91 (t −1.23). Rated era (≥07-10) −2.80pp, t_day −1.94, NW −1.49. Q1d: corr(finalist rel, market move) = −0.75 in the rated era; residual at market=0 is only −0.9pp (fwd_10) / −0.6pp (fwd_5). Most of the 10-day "negative selection" is finalists lagging the July rally (low-beta / not-extended selection), not stock-specific decay. Verdict: **not a reliable negative selector at 5–10 days**; only the overnight penalty is robust.
3. Early-stop reasons all negative at 10d (基本面恶化 −6.9pp t −3.7 n=10; 资金流出 −4.0 t −2.8 n=20; 其他 −6.2 n=6; 涨停追高 −4.3 n=4; 题材透支 −3.2 n=18) vs full-card −3.1 (rated era) — consistent with "rejection works better than selection", but n is tiny and overlapping.
4. Rating: OW n=2 → cannot evaluate ≥OW. Finalist group means order correctly at 10d (Hold −2.8 > UW −6.4 > Sell −19.5 n=1) but per-date rank IC is −0.21 (t −1.9, 14 days) and pooled rho +0.06 (p 0.46); Hold−UW paired diff +2.5pp t 0.8. **No evidence the rating rank-orders 5–10d returns.** Pooled across finalist+pinned, UW/Sell rows beat Hold (pinned holdings get UW ratings and then rise).
5. Q4: strong T+1 close: overnight −0.31 vs weak +0.32 (diff −0.60pp, t −2.2; all-lake −0.19pp t −1.5) = the design-doc penalty, small but right-signed. The "+6.9pp at fwd_5" reversal is an artifact of the fwd_5_oc/fwd_10_oc entry leg (D+1 open — the T+1 intraday move is inside the window; all-lake shows the identical +6.0/+6.3pp). On the aligned c1→c5/c1→c10 measure the strong−weak diff is +0.15pp (t 0.1) and +3.0pp (t 1.4); all-lake ≈0. **No horizon flip: a strong T+1 close costs ~0.5pp overnight and predicts nothing at 5–10 days.**
6. Pinned (11 codes, 3 codes = 58/82 rows): gap +0.62 (t 0.8), fwd_5 +1.51 (t 0.7), fwd_10 +0.61 (t 0.2); July fwd_10 −4.2 vs Aug +11.0 (t 2.5, driven by 920179/300750/688271). Effective n ≈ 11 → the 08-22 "📌 +0.64 best" is noise.
7. Conviction: Spearman with fwd_10_rel is negative in every cut (pooled −0.09 p 0.047; per-date IC −0.135 t −2.2; rated era pooled −0.16 p 0.055, IC −0.25 t −2.75; fwd_5 rated era pooled −0.24 p 0.002). L3 conviction is weakly **anti**-predictive at 5–10d.
8. src: 26/40 dates are shared-only (all of 07-10..08-20), 2 mixed, 12 run-only. Dropping shared rows = dropping the rated/E6 era: finalist fwd_10 becomes −1.82pp (t −1.37), gap −0.25 (t −1.90). Dedupe FIRST vs LAST vs none: fwd_10 −2.11/−2.39/−2.25 (t −2.1/−2.4/−2.3) — stable.

Caveats: (a) fwd_10 windows overlap across consecutive dates → t_day overstated, use t_NW (and NW with <20 days is itself unstable, e.g. reversion lane NW +4.1 vs t_day +1.6); (b) rated era has 6–10 finalists/day → 19–23 usable days for fwd_10, none for P3; (c) population break at 07-10 (30 → 7–10 finalists) and lane mix changes by month (reversion/trend = June only, carryover = July only) — lane comparisons are era comparisons; (d) shared staging rows may reflect a later replay, not the run-time list (provenance, not return bias); (e) survivorship: only published runs with a manifest are in the ledger, and suspended/limit-locked names drop out of fwd via NaN (20 rows buyable_c1=False); (f) 4 shadow BUY rows (08-13/08-17×2/08-18, 688766 pinned + 000779 UW) excluded from all BUY stats; (g) rating ≥OW n=2 and active BUY n=2 — untestable.
