# 隔夜集中信号普查 · 预注册(§0)

> **本文件 §0 在看到任何 census 读数之前写定并 commit,事后一字不改。**
> 读数在 §1 起追加,不得回改 §0。设计稿:`docs/specs/2026-08-28-overnight-concentrated-signal-census-design.md`
> (含 21:27 版本溯源头:v1 = 用户批准版,v2 = 实施 agent 重写版;本预注册采用下述**明示**口径)。

## 0.1 问题与假设

在主尺 `gap_c1_o2`(T+1 收盘买 → T+2 开盘卖;2026-07-10 / 08-05 / 08-28 用户三次裁定不换)下,
有没有任何**集中型**人口(日历事件 / 席位资金 / 涨停板 / 微结构组合)能稳定给出
**绝对毛收益 ≥ +0.15pp/夜**(= 覆盖往返成本后期望为正)?

- **H0**:没有任何格满足 §0.4 的「历史正候选」判据。
- **H1**:至少一格满足。
- 本普查**不裁决任何生产变更**,只到「提案」(设计稿 §6 裁决树);不设停机;全表印出。

## 0.2 因变量、单位与成本(跑前锁死)

- 主 target = `gap_c1_o2 = open(D+2)/close(D+1) − 1`,**必须**来自生产实现
  `autoresearch/research/factor_lab.py::forward_returns`(经 `edge_census.forward_frame` 复用),
  不另写公式;`|gap| > ruler.GAP_CLIP = 0.31` 视数据错置 NaN 并计数。
- 单位一律 **pp(百分点)**。`lake/daily.amount` = 千元;`top_inst.net_buy`、
  `limit_list_d.{amount,fd_amount}` = 元;跨表比值前换算。
- `COST_PP = 0.15` = 印花税 0.05(卖)+ 佣金 0.025×2 + 滑点余量 0.05
  (与 `scan/relative_facts.py:26` 同量级)。`net = 绝对毛 − COST_PP`。
- 并印 `rel_gap_pp` = gap − 当日**全湖可交易**(`buyable_c1`)等权均值(`ruler.REL_MARKET` 口径);
  **判读只认绝对列**(用户的问题是绝对赚不赚)。

## 0.3 估计量与区间

- **夜等权**:先在每个扫描日内对该格全部事件等权得一条日收益,再跨日等权。
  `n_events` 只描述覆盖,不让事件多的热闹日拿到更高权重。
- 区间 = `autoresearch/common/stats.py::date_cluster_bootstrap`(按日聚簇,单日不产区间)。
  > 待裁项(设计稿溯源头 (b)):是否改用 5 日 moving-block bootstrap。隔夜窗不重叠,
  > 增益存疑;若采用则**在看读数之前**改本节并重跑,不得看完读数再换区间口径。
- 多重比较:**每格独立 95% CI**;正证据数必须与「总共测了多少格」同行印出。
  > 待裁项 (a):是否对 6 个 confirmatory 主格加 max-T simultaneous CI。会显著抬高门槛。
  > 同样必须在看读数之前决定。

## 0.4 判读(五态,**样本门先判**)

样本门:事件族 `n_events ≥ 300 ∧ n_days ≥ 60`;宽族(F4)`n_days ≥ 200`。不过 → **数据不足**,只印数。

| 态 | 判据 |
|---|---|
| **历史正候选** | CI 下界 ≥ `+0.15pp` ∧ 2022/23/24/25 中有读数年份 ≥3/4 与全期均值同号 ∧ 日期两半同号。**仅 R-confirmatory 格可得此态** |
| **经济性证伪** | CI 上界 < `+0.15pp` —— 已排除「覆盖假设成本」,即使毛均值略正也不叫 BUY |
| **显著有害** | CI 上界 < 0(经济性证伪的更强子态,另打标签;**避雷单材料**) |
| **未决** | 区间仍跨 `+0.15pp`。不显著 ≠ 无 alpha |
| **数据不足** | 样本门任一不过 |

每格另有 `actionability ∈ {R_REPORT_ELIGIBLE, X_ORACLE_ONLY, UNEXECUTABLE}`:
- `R_REPORT_ELIGIBLE` 仅表示**时点上**报告可用(T 盘后可知),≠ 生产已批准;
- `X_ORACLE_ONLY`(F3 全族 + F5):输入含 EOD 终值,只能输出理论上界,**不得**判「历史正候选」。

## 0.5 人口(跑前锁死)

非 ST ∧ 非北交所(`.BJ`)∧ `total_mv ≥ 30 亿` ∧ `buyable_c1`(F3 除外,见下)。
- `total_mv` 来自 `daily_basic`,允许 **≤10 交易日前向填充**并以 `mv_stale_days` 留痕(0 = 当日真值)。
- `is_st` 来自 `stock_basic` **当前**名称快照,**不是 PIT** —— 已知偏差(方向:轻微放宽人口),
  meta 标 `is_st_source = "static snapshot, not PIT"`。待裁项 (d):是否回填 `namechange` 取 PIT。
- **F3 不要求 `buyable_c1`**:它正是生产剔掉的镜像人口,`notes` 标「生产不可见」。
- R 族逐格报 `n_sealed_dropped`(因 D+1 封板被剔的观测数;08-08 判例:剔了会美化账本)。

## 0.6 格清单与预注册方向(16 格;一格不多一格不少)

| 格 | 时点/角色 | 定义摘要 | 预注册方向 |
|---|---|---|---|
| F1a 财报披露前夜 | R · confirmatory | `pre_date == D+2`;子格年报/中报/一季/三季、已出预告 vs 无 | + 弱;已出预告 ≈0 |
| F1b 解禁前夜 | R · exploratory | `float_date == D+2` ∧ `float_ratio ≥ 1%` ∧ `ann_date ≤ D` | − |
| F1c 除权除息前夜 | R · exploratory | `ex_date == D+2` ∧ `ann_date ≤ D`;gap 走机械校正 | ≈0 |
| F2a 知名游资净买 | R · confirmatory | Σ游资席 `net_buy` / 成交额 ≥ 3% | + |
| F2b 机构专用净买 | R · confirmatory | Σ「机构专用」/ 成交额 ≥ 3% | ≈0 |
| F2c 北向(两子格) | R · exploratory | 股通席净买 > 0;`hk_hold.ratio` 5 日增前 1% | ≈0 / 弱 + |
| F2d 大宗(溢价/折价) | R · exploratory | 溢价 ∧ 金额 ≥ 成交额 1%;折价 ≥ 5% | 溢价 + / 折价 − |
| F2e 超大单净流入 | R · exploratory | `(elg_buy − elg_sell)`/成交额 前 2% | ≈0 |
| F3a 首板·T1 | **X_ORACLE** | D+1 `limit=='U'` ∧ `limit_times==1` ∧ T1 | + 上界 |
| F3b 首板·炸板 | **X_ORACLE** | D+1 `limit=='Z'`(T0) | − |
| F3c 二板+·T1 | **X_ORACLE** | `limit_times ≥ 2` ∧ T1 | + 更弱 |
| F3d 板块共振增量 | **X_ORACLE** | 同行业当日 ≥3 板 vs 孤板,**同日配对** | + 增量 |
| F3e 一字/早封厚单 | **X_ORACLE** | 其余 U(T2),只印参照 | + 大 |
| F4 冷门中间态 | R · **exploratory** | `pct_5d` D2–D5 ∩ 低换手 ∩ 低波 ∩ 近 5 日无板 | 相对 ≈+0.1、**绝对 ≈0,预期不过门** |
| F5 T+1 条件层 | **X_ORACLE** | 叠在 F1/F2 上,`exec_ok` 与 `scan/outcome.py` 锁相等,**同日配对** delta | 弱收盘 ≈ 市场 |

**F4 只能是 exploratory**:它的组合形态是在看过本波附录 A 的 `pct_5d` 十分位探针之后定义的,
列为 confirmatory 即数据窥探。
**F2a 的名单前视**:游资名单是 2026-04 快照用于 2022–25 数据,名单构成本身可能含前视;
该格结论须连带此限定,若因此不可采信则降 exploratory。

## 0.7 成交层(F3;全族 oracle)

挂单时点 14:57、涨停价。三层**全部由 EOD 终值判定**(`last_time` / `open_times` / `fd_amount`
都在收盘后才定),故**没有一层等于「当刻必成交」**:
- **T0** `limit == 'Z'`:收盘未封 → 当日曾可成交(仍非 14:57 当刻确定)
- **T1** `limit == 'U'` ∧ (`last_time ≥ 143000` ∨ `open_times ≥ 1` ∨ `fd_amount/amount ≤ 0.10`)
- **T2** 其余 U(早封 + 厚封单 + 一字)——理论不可成交,只印参照
真实成交率、排队位置、逆向选择只能由 14:45 live shadow 获得;无 point-in-time 原始响应时,
F3 任何格**不得**升为 actionable。板幅按板别走 `factor_lab._board_limit`。

## 0.8 不做
不换主尺 / 不提 swing;不改任何生产代码、配置、prompt、E6;不回注、不写生产账本;
不分 regime;不做 ML / 组合优化 / 参数搜索;不引用单日作证据;
指数调整生效日、新股首日、ST 摘帽等稀有事件记 backlog 不进本批;不评估 T+2 日内;不抓 tick。

## 0.9 复现
```
uv run --no-sync python -m autoresearch.research.overnight_census --backfill
uv run --no-sync python -m autoresearch.research.overnight_census --run
```

---

_(§1 起为读数,预注册 commit 之后追加。)_
_仅供研究,非投资建议。_
