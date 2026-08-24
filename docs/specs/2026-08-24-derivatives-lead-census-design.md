# 衍生品领先性普查(derivatives-lead-census)· 设计稿

> **状态:仅设计,未实施**(2026-08-24 用户裁定:只产出开发文档,不进入开发)。
> 实施(预注册 commit / 数据回补 / 任何接线)均须用户另批;本稿不改变现行调度权威。
> 前情:`docs/specs/2026-08-03-scan-next-wave-brainstorm-design.md` §2(F1/F2/F3)与
> `autoresearch/derivatives/` 四模块(2026-08-04 建成,commit 22b8750)。

## 0. 立案:为什么是「证伪普查」而不是「接线」

**现状三事实**(2026-08-24 盘点):

1. 期权线的设计(08-03 稿 §2)与基建(`autoresearch/derivatives/`:options_lake /
   qvix / style_spread / cb_gate)都已存在,含强制分页、as-of 联结、I/B 类消费边界
   护栏、甚至 `lead_lag_check` 骨架;
2. 但四模块**零生产调用方**(仅 tests 引用)、磁盘**零数据**、08-03 稿写明的「最小
   证伪步」**从未执行**——FN-1 家族(建好即搁置的基建);
3. tushare `opt_daily` 与 akshare QVIX 都有**可回补的历史**→ 证伪不需要等日积月累,
   一次历史回补即可出全读数。

**方法论**(项目判例):先问「哪把尺子真的预测」(l3-no-selection-alpha)、开工前
证伪省下整条管道(wave4-event-recall)、预注册先写后看(edge_census 2026-08-22)。
**读数全负 → 这条线至多留日历提醒候选,省掉整条接线;有正证据 → 才谈接线波。**

## 1. 探针实测(2026-08-24;写方案前先证伪的数据面)

| 探针 | 结果 | 裁定 |
|---|---|---|
| tushare `opt_daily(SSE)` 20160105/20200106/20240102 | 104 / 240 / 528 行 | SSE 2016 起全史可回补 ✅ |
| tushare `opt_daily(CFFEX)` 20200106/20240102 | 204 / 656 行 | CFFEX 2020 起 ✅(IO 2019-12 上市) |
| tushare `fut_daily(CFFEX)` 20260821 / 20230103 | 64 / 57 行,含 `settle/oi` 列 | **权限有**(稿外新发现)→ 股指基差腿可行 ✅ |
| tushare `index_daily` 000852/000905 | 各 15 行(20260801–21) | 现货腿 ✅ |
| tushare `opt_basic(SSE)` | 12000 行,`maturity_date` 12000/12000 非空 | 到期日历腿 ✅(12000 = 分页上限,须 `paginate`) |
| akshare QVIX 8 序列 | **全部**返回 2796 行、称"2015-02-09 起" | **谎报起点**:接口统一回填日期轴(300ETF 期权 2019-12 才上市);有效起点须逐序列按非空段验定 |
| 其中 300index / 1000index QVIX | 末行 OHLC 全 NaN | **两条指数 QVIX 序列死**;活的是 6 条 ETF QVIX(50/300/500/创业板/科创/深100) |
| `lake/daily/` | 1087 个交易日,**2022-03-02 起** | breadth/全市场量只能从 2022-03 起算 → 决定主判读窗(§3.4) |

## 2. 候选全景与裁定(2026-08-24 用户选定)

**入证伪轮(4/4 全选)**:

- **族A · QVIX 恐慌/波动率地形**:A1 水平 250 日分位、A2 单日急升、A3 VRP(稿外新角度);
- **族B · PCR 持仓/对冲压力**:08-03 F1 的「最小证伪步」一次还清;
- **族C2 · 股指期货基差**(稿外新角度):IM/IF 年化贴水差替代 F2 已断的 IV 腿(1000index QVIX 死);
- **族D · 到期日/交割日日历**(稿外新角度):确定性日历旗 × 隔夜主尺分布。

**待读数再议(不入本轮)**:族E = QVIX 急升哨兵(A2 的 `tripwire_watch` 消费侧变体,
A2 尾部读数过线才立项)。

**不做/不重启**:个股期权召回(当前产品集不可行,按季度重检——08-03 裁定);F3 转债
(`BLOCKED_BY_DATA` 未解);max-pain 磁吸位(消费点窄、与隔夜尺关系薄);HK 个股期权
代理/场外期权(明确不做)。

## 3. 预注册草案(开工时原样落 `docs/research/2026-08-24-derivatives-lead-census.md` §0,**先 commit 后看任何读数**)

### 3.0 假设

**问题**:T 日收盘后可知的衍生品市场信号(QVIX / PCR / 基差 / 到期日历),对 **T+1 之后
的市场级结果**有没有超出 breadth+动量基线的领先性?

- **H0**:四族没有任何一个信号过「正证据」线(08-03 稿的谨慎默认——期权只配当地形
  展示,不配进判断)。
- **H1**:至少一族过线。**本普查不裁决任何接线/哨兵/日历变更,只给裁决者读数。**

### 3.1 信号定义(全部 T 收盘后可知;参数在此锁死,跑前不改)

| 族 | 信号 | 定义 | 样本起点 |
|---|---|---|---|
| A1 | `qvix_pctile` | QVIX close 在滚动 250 日窗内分位(min 120 日才出数) | 逐序列有效起点 |
| A2 | `qvix_spike_z` | ΔQVIX = close_T − close_{T−1} 的 250 日滚动 z(min 120) | 同上 |
| A3 | `vrp` | (QVIX_T/100)² − 标的指数对数收益 20 日滚动年化方差 | 同上 |
| B | `pcr_vol` / `pcr_oi`(分品种) | `options_lake.bucket_metrics` 的成交量/持仓 PCR;另报 ΔPCR 与 250 日 z | SSE 2016 / SZSE·CFFEX 2020 |
| C2 | `basis_ann`(分品种)、`style_gap` | 主力(当日最大 OI)年化基差 = (settle/现货close − 1) × 365/剩余自然日(剩余 <7 日弃,避换月毛刺);`style_gap` = IM − IF 年化基差 | IF/IH/IC 2016、IM 2022-07 |
| D | `expiry_day` / `expiry_week` / `post_expiry` | ETF 期权到期日(`opt_basic.maturity_date` 去重)∪ 股指期权/期货第三周五;到期周 = 周一至到期日;次日旗 | 2016 起 |

QVIX 标的映射:50ETF→000016.SH、300ETF→000300.SH、500ETF→000905.SH、创业板→399006.SZ、
科创→000688.SH、深100→399330.SZ。PCR 语义:只解释为对冲/持仓压力,**不得标看空**
(`PCR_SEMANTICS` 随读数走——卖 put 是看多,PCR 分不出买卖方向)。

### 3.2 因变量(把证伪尺对齐到产品真做的事)

信号在 T 盘后可知 → 若被 E6/scan 消费,影响的是 T 晚决策、T+1 收盘买、T+2 开盘卖的
持仓。故:

- **主判读 target**:`gap` = 信号自己标的指数的 close(T+1) → open(T+2) 收益
  (与产品主尺 `gap_c1_o2` 完全同窗);
- 观察 target(只印不判):`oc(T+1)` = open(T+1)→close(T+1)(若日内可行动)、
  `Δbreadth(T+1)`;
- 公共观察列:000300.SH 与 000852.SH 两个基准的 `gap`(族间可比);
- **A2 哨兵专测尾部**:H 档(急升)之后 P(指数 close-to-close(T+1) ≤ −1.5%) 相对
  无条件基率的提升倍数——哨兵要的是尾部预警,不是均值。

### 3.3 判读规则

- 档位:signal_T 在**滚动 250 日窗**内分位 ≤20% = L 档、≥80% = H 档、其余 M
  (walk-forward,无全样本前视);
- **正证据**(逐信号 × 主 target):H−L 条件均值差方向明确 ∧ Newey-West t(lag=5)
  |t| ≥ **2.0** ∧ 两档各 n ≥ **100**(D 族:旗日 vs 非旗日 two-sample,旗日 n ≥ 20)
  ∧ **基线增量成立**:OLS `target ~ signal_z + breadth_T + idx_mom20_T` 中 signal 的
  NW-t |t| ≥ 2.0 且与 H−L 同号(同期相关、被 breadth/动量解释掉的都不算);
- **显著负 / 未证 / 样本不足**:edge_census 同款(**不显著 ≠ 有 alpha**;样本不足
  只印数不判);
- A2 尾部:提升倍数 + Fisher 精确 p(p < 0.05 才算过);
- **不设停机;全表印出**:判读只认预注册主列(信号自标的 `gap`),其余为观察列;
  正证据数必须对照「总共测了多少格」一起报,防多重比较错觉。

### 3.4 口径

- **主判读窗 = 2022-03-02 起**(`lake/daily` 起点;breadth 基线可得、IM 在窗内)。
  2016 起的长史只出 raw H−L 观察列,**不判读**——缺 breadth 基线,窗混着判会造成
  族间不可比;
- `breadth_T` = 当日全湖可交易票中 pct_chg>0 占比(`lake/daily` 直算);
  `idx_mom20_T` = 对应指数 20 日收益;
- QVIX 有效起点 = 首个连续 ≥60 交易日全非空段的起点,之前全弃;死序列
  (300index/1000index)整条弃并记账;
- 指数 target 极值:|gap| > 8% 视数据错,剔除并计数(个股的 31% 阈不适用于指数);
- 缓存缺日:该信号该日不计,**不伪造为空**;coverage 报每族有效日数;
- 不分 regime(样本薄,加了只会诱导调参);不做任何个股级断言。

### 3.5 不做

不接 `market_pack` / `strategist_pack` / regime、不建 prelude 步骤、不动任何生产代码、
不动主尺;哨兵与日历提醒的立项在读数之后另行请批。

复现(实施后):`uv run --no-sync python -m autoresearch.research.derivatives_census
[--backfill] [--since] [--out PATH]` → `reports_<engine>/research/derivatives_census.md`
+ 同目录 `_derivatives_census.json`(机器可读)。

## 4. 仪器蓝图

### 4.1 数据缓存(一次回补、长期复用;若 F1 日后转正,此缓存即其湖底)

```
lake/derivatives/
  opt_basic/<EX>_<snapshot_date>.parquet   # paginate 全量快照(全列)
  opt_daily/<EX>/<YYYYMMDD>.parquet        # 逐日全列(≈30KB/日;SSE 2016 起、SZSE/CFFEX 2020 起)
  fut_daily/CFFEX/<YYYYMMDD>.parquet       # 逐日全列(2016 起)
  index_daily/<ts_code>.parquet            # 6 条现货指数整段(增量追加)
  qvix/<series>.parquet                    # 6 条活序列整段(每次全量重拉,幂等覆盖)
```

- **全列落盘**(不传 fields)——lake 窄表毒化判例(cache key 不含 fields → 整组 NaN
  静默失真);
- 断点续传:文件存在即跳过;`--force-refresh-tail N` 重拉最近 N 日;
- 限速:`sleep_ms` 可调(默认 150ms ≈ 400 call/min),复用 `factor_lab._ts_call` 的
  retry/backoff 模式;
- **成本实测(2026-08-24 冒烟,不是估算)**:`opt_daily` **0.82 s/call**、`fut_daily`
  0.65 s/call(含 0.12s 礼貌 sleep;瓶颈在 tushare 往返而非本地)。据此定回补窗
  **2021-01-01 起**(判读窗 2022-03-02 − 250 日暖机),≈1,370 交易日:
  opt_daily 1,370×3 ≈ 4,110 call ≈ **56 分钟**、fut_daily 1,370 call ≈ 15 分钟
  (两条腿可并行,互不写同一文件)+ 元数据 ≈20 call → **合计 ≈70~90 分钟**,磁盘 ≈150MB。
  原稿「8,400 call ≈ 25–35 分钟」按 2016 起全史 + 0.4s/call 估算,**两项都被实测推翻**;
  2016–2020 段只影响长史观察列(判读窗内不用),故按需再补(`--backfill-since`)。

### 4.2 模块与复用

`autoresearch/research/derivatives_census.py`(零 LLM,只读湖 + 自建缓存;house 风格
同 `edge_census`):

- 复用 `derivatives/options_lake.py`:`paginate` / `coverage_report` / `join_asof` /
  `bucket_metrics`(PCR);
- 复用 `derivatives/qvix.py`:`check_series`(有效起点验定的非空契约);
- 复用 `research/factor_lab.py`:`_pro` / `_ts_call`(retry)模式;
- 新增纯函数(全部可单测、零网络):
  - `annualized_basis(settle, spot, days_left) -> float | None`(<7 日返 None)
  - `vrp(qvix_close, idx_logret_win20) -> float`
  - `rolling_pctile(s, win=250, min_periods=120) -> Series`(**窗含当日、不含任何未来日**——与 §3.1 A1 定义一致;「无前视」指档位切点只用 ≤T 的数据)
  - `expiry_flags(maturity_dates, trade_days) -> DataFrame[expiry_day, expiry_week, post_expiry]`
  - `bucket_hl(signal, target, win=250) -> dict`(walk-forward 档位 + H−L + NW t + n)
  - `baseline_increment(target, signal_z, breadth, mom20) -> dict`(OLS + NW t)
  - `tail_lift(spike_flag, tail_flag) -> dict`(基率提升 + Fisher 精确 p)
- CLI:`--backfill`(只回补)/ 默认 report(读缓存出表)/ `--since`。

### 4.3 测试清单(`tests/research/test_derivatives_census.py`,零网络)

年化基差(含 <7 日弃)/ VRP 符号与量纲 / `rolling_pctile` 无前视(截断窗断言)/
`expiry_flags`(第四周三·第三周五·节假日顺延,固定 fixture)/ `bucket_hl` 的 H−L 与
n 计数 / NW t 对自相关序列不虚高(合成 AR 序列探针)/ 谎报起点与死序列的验定(合成
「2015 回填 NaN 头」fixture)/ coverage 缺日不伪造。

**变异探针**(wave35 判例:绿灯不等于有灯):把 `bucket_hl` 档位切点改成全样本分位
→ 无前视断言必须变红;把 NW lag 改 0 → AR 探针必须变红。

## 5. 实施顺序与验收(开工时执行;每批一 commit,只按显式路径提交)

- **批0**:预注册 doc(§3 原样落 `docs/research/2026-08-24-derivatives-lead-census.md`)
  **单独 commit,早于任何读数**;
- **批1**:仪器 + 测试(全绿)commit;
- **批2**:`--backfill`(实测限速后定 sleep)→ 跑普查 → 读数追加进 doc §1 → commit。

验收:① `opt_basic` coverage 对账零缺口;② 每族有效日数与预期窗吻合(50ETF ≥2300、
主判读窗 ≥800);③ QVIX 有效起点表印出且 300ETF ≈ 2019-12(谎报起点被验定逻辑纠正的
直接证据);④ 极值剔除计数印出;⑤ 两条变异探针红。

## 6. 读数之后的裁决树(全部只到「提案」,不预授权任何接线)

| 读数 | 下一步提案(均须用户另批) |
|---|---|
| 四族全负/未证 | 归档;至多留 D 日历提醒候选(纯确定性,`calendar` 一行);季度重检产品集时顺带复跑 |
| A2 尾部过线 | 族E 哨兵进 `tripwire_watch` 提案(持仓保护层;B 类,registry) |
| A1/B/C2 任一过线 | `market_pack.derivatives` I 类接线波提案(只展示);进 strategist/regime = B 类,行为实验治理(l3-no-selection-alpha 同款) |
| D 过线 | calendar 日历行 + L5 地形一句(I 类) |

## 7. 开放问题(开工首日解决,不阻塞本稿)

1. tushare 实际限速(首日实测定 sleep;高权限 token 或可更快);
2. SZSE `opt_daily` 真实起点(159919 期权 2019-12 挂牌,以回补数据为准);
3. `opt_basic` 三所分页全量后的到期日历完备性(coverage 对账);
4. breadth 口径与 `frame.py` regime breadth 的差异,读数表脚注显式(本稿定义 =
   全湖 pct_chg>0 占比);
5. CFFEX 股指期权(IO/MO/HO)PCR 与 ETF 期权 PCR 分列判读(锁:分品种,永不合并)。
