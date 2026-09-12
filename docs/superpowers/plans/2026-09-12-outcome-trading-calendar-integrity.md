# Outcome Trading Calendar Integrity — Implementation Plan

> 日期：2026-09-12；独立 P0，来自现场重建评审。
> 状态：本次仅修订计划，未实施代码、取行情或回填账本。
> 关联：[现场重建设计 §1/§8](../specs/2026-09-12-scene-reconstruction-transcript-binding-design.md)、[主实施计划](2026-09-12-scene-reconstruction-transcript-binding.md)。
> 本计划可独立提交与验收；不依赖 transcript 绑定，不修改选股行为。

**Goal:** 按可信交易日历固定 T+1 收盘买、T+2 开盘卖的日期；湖缺日、日历不可核验、未到期分别记状态，并让受旧日期口径影响的账本可识别、可重算、可恢复。

**Architecture:** 由 outcome 的纯日期解析函数先确定目标 session，再按精确日期读取既有行情；主收益与迟到报告反事实共用该函数。状态和日历来源传播到逐 run JSON、CSV 与展示，批量重算在 ledger 内保留前镜像。

**Tech Stack:** 现有 Python、pandas/pyarrow、exec_anchor、market_panel、forward_returns、trace.atomic、pytest；不新建行情源、数据库或回测框架。

## 1. 立案与边界

旧 `outcome.market_frame` 用湖分区排序的后两项作为 T+1/T+2。原稿记载湖缺日导致 09-01、09-07 两次 run 共 21 行日期错位，这些数量是原调查记录，本次未读取跨引擎产物复核。

旧计划又允许 `calendar_quality=lake_partitions` 绕过日期验证，相当于让同一份可能缺日的数据验证自己。本计划删除该放行，并同步修正 `exec_anchor_frame` 按湖找前一日的路径。

主尺始终 `gap_c1_o2`，推荐毛收益与实际成交分开。保持已有价格调整、异常值过滤、可交易判断与 benchmark 人口定义；不换池、不新增 swing 策略，不用修复前战绩调参。

不写冻结 run、不补造行情、不改 `lake/`。日历可使用现有 Tushare trade_cal 路径及已验证的原有缓存；其取数行为单独记录，不把这一步宣传成绝对零网络。无可信日历就给出状态，不静默降级为工作日推断。

参考：[Tushare trade_cal 字段](https://tushare.pro/document/2?doc_id=26)、[上交所 2026 年休市安排](https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml)。交易所休市包含工作日，不能用 weekday 代替交易日历。

## 2. 日期、数据与状态契约

### 2.1 纯日期解析

新增 `resolve_outcome_sessions(analysis_date, *, calendar, today) -> dict`，放在 outcome.py，calendar 为现有 `(start, end) -> (sessions, quality)` 形状；测试注入合成日历。

1. 先核验日历来源和请求范围。quality 必须为 `trade_cal`；缓存只有在能验证来源与请求范围时才保留该质量标签。
2. `lake_partitions`、`weekday_heuristic`、空日历、请求异常或不完整范围一律 UNVERIFIED_CALENDAR。不得因为日期“恰好对上”便提高可信度。
3. 对可信日历按日期去重排序，确认包含所需后续 session；`analysis_date` 不在可信日历的交易日集合才判 INVALID_ANALYSIS_DATE，不能据弱日历下此结论。
4. T+1/T+2 从日历选，不从文件列表选。若 T+2 晚于 today，状态 PENDING_SESSION；否则按精确日期检查行情。
5. 主目标列 `t1/t2` 使用 YYYYMMDD，与旧字段兼容；analysis_date/today 按 Asia/Shanghai 的交易日期解释。UTC 时间先转换，不直接删除时区。
6. 日历元数据保存来源质量、请求范围、所用 session 列表的规范化摘要；today 只是成熟判断，不改变 session 归属。

### 2.2 market_frame

`market_frame(date, *, lake_daily=None, calendar=None, today=None) -> (DataFrame | None, meta)` 先调用日期解析，只有目标日期明确后才读行情。

meta 总是包含 `outcome_status, reason, calendar_quality, calendar_digest, t1, t2, missing_sessions`（未知值为 null/空列表）。状态如下：

| outcome_status | 条件 | 可否计入成熟主收益 |
|---|---|---|
| MATURE | 可信日历、T+2 已到，所需日期行情在场 | 仅有有效本票价格的行可以 |
| PENDING_SESSION | 可信目标 T+2 尚未到 | 否 |
| MISSING_MARKET_DATA | 目标已到但 D/T+1/T+2 所需行情缺失或不满足原数据契约 | 否 |
| UNVERIFIED_CALENDAR | 日历不可核验、弱回退、范围不足 | 否 |
| INVALID_ANALYSIS_DATE | 分析日期不是可信交易日 | 否 |

可保留诊断信息，但非 MATURE 状态不得落可供汇总的旧主收益值。缺行情不能悄悄顺延到下一个分区，不能填零。

个股停牌/缺价与全市场分区缺失分开：市场日期成熟不代表每只股票有可交易价格。行级缺失仍为 null，保留 reason；原有 `n_rows/n_scored` 继续说明覆盖。完整性 `complete` 要先满足可信日历和目标日期行情，再应用原有行覆盖判据，不能用“多数有值”掩盖错误日期。

### 2.3 迟到报告与既有其他收益列

`exec_anchor_frame` 使用 execution 的 `first_available_session`，从同一可信日历确定其前一个 session 与后续卖出日；禁止 `P[i-1]` 从湖分区猜前一天。其状态单独记录为 `exec_outcome_status`，无法核验时 exec_gap_c1_o2=null，不污染正常主尺。

既有 `fwd_5_oc/fwd_10_oc` 也不能在有缺日的文件序列上冒充第五/第十交易日。继续使用原公式，但所需交易日窗口未核验/不完整时该列为 null，单独记录期限成熟度；不因这些旁列未成熟阻塞隔夜主尺。本项只防错位，不扩展持仓目标。

## 3. 文件与消费链

| 文件 | 责任 |
|---|---|
| autoresearch/scan/outcome.py | 日期解析、market_frame、exec_anchor_frame、compute_outcome、fill/CSV 状态传播 |
| autoresearch/scan/exec_anchor.py | 复用 trading_sessions；只有必要时扩展可信缓存元信息，不全局改变其他调用方的弱质量展示规则 |
| autoresearch/scan/chain_view.py | 显示日期/日历质量/未成熟原因，区分主尺与反事实 |
| autoresearch/scan/ledger_views.py | 非 MATURE/日历未验证的行不进入成熟统计 |
| autoresearch/contracts/artifacts.py | 登记迁移前镜像、差异与完成状态 |
| autoresearch/trace/atomic.py | 复用现有原子落盘，不另写文件事务框架 |
| tests/scan/test_outcome_calendar.py（新） | 日期与失败状态反例 |
| tests/scan/test_outcome.py、test_exec_anchor.py、test_ledger_views.py、test_chain_view.py | 兼容、状态消费、失效与重算 |

OUTCOME_SCHEMA_VERSION 升 2。逐 run JSON 保留旧字段，新增状态/日历来源；recommendations.csv 增加 t1、t2、outcome_status、calendar_quality、calendar_digest、exec_outcome_status。不同人口与原来的 mode/src/actionability 标签一起保留。

`compute_outcome` 对有 run/facts 的日期失败返回带状态的文档，不能用 None 丢掉原因；完全无法定位 run/facts 的情形可沿旧空返回，但 fill 必须记录跳过原因。新状态无需制造一份虚假收益帧。

## 4. Task C1：严格日期与行情窗口

- [ ] 写 D=20260901、湖有 0902/0907 但缺 0903 的反例，目标必须保持 0902/0903，状态为 MISSING_MARKET_DATA。
- [ ] 写弱日历恰与湖一致、日历异常/空范围、分析日非交易日、尚未到 T+2 的用例；分别验证状态，不能放行。
- [ ] 实现 resolve_outcome_sessions 与 market_frame 的严格路径，日历先定日期，行情按目标日期读；现有价格/收益公式不变。
- [ ] 显式 today 注入；有未来分区也不能让未到期收益提前成熟。
- [ ] 修复迟到执行锚使用可信日历的前驱/后继，保留主收益与 exec 收益两个状态。
- [ ] 既有 5/10 日期窗口不完整时对应列为 null，隔夜成熟不受拖累。

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/scan/test_outcome_calendar.py tests/scan/test_outcome.py tests/scan/test_exec_anchor.py -q
```

**验收：** T+1/T+2 只由可信交易日历定义；弱回退不生成成熟收益，缺日不漂移。

## 5. Task C2：状态传播与旧账失效

- [ ] 写旧 schema 1 complete=true 但日期错位的 fixture，证明不能继续被 fill 的 complete 快捷路径永久跳过。
- [ ] 升 schema、传播 meta 到逐 run JSON/CSV/chain_view/ledger_views，明确显示失败原因和日历来源。
- [ ] fill 仅跳过 schema 2 且日历已验证、所需口径完整的结果；schema 1 与无质量字段的旧结果都需要重新验证。
- [ ] 重算返回 UNVERIFIED_CALENDAR/MISSING_MARKET_DATA 时，将该 run 的旧成熟值从有效统计撤出；保留前镜像，不能让新失败状态旁边继续展示旧收益为有效。
- [ ] CSV 的该 run 行整体替换/失效，不能只 upsert 新非空行而残留旧行；在同一重建后从新 JSON 一次性生成 CSV，核对 run/code 唯一。
- [ ] `ledger_line` 与 ledger_views 只统计合格人口，旧版本在迁移前显示“日历未验证”，不与 v2 已验证结果混算。
- [ ] 测试校验 calendar_quality/outcome_status 真被消费者使用，不只检查字段存在。

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/scan/test_outcome_calendar.py tests/scan/test_outcome.py tests/scan/test_ledger_views.py tests/scan/test_chain_view.py -q
```

**验收：** 错误旧 complete 被重新检查；校验失败后旧值不再进入成熟统计；JSON/CSV/视图日期一致。

## 6. Task C3：可审阅回填与恢复

数据操作只在后续明确执行本计划时进行，本次文档编辑不跑回填。禁止跨引擎数据操作。

- [ ] 登记 `_ledger/outcome_migrations/<migration_id>/` 目录契约，包含 before/ 前镜像、拟替换结果、diff.json 与迁移状态；源文件摘要写入 diff。
- [ ] 为 fill 增加 `--dry-run`（可与 --rebuild 合用）和 `--run-id <report_run_id>` 精确范围过滤。dry-run 只计算/输出，不改 outcome/CSV/冻结 run；只读日历取数是否联网须如实说明。
- [ ] 差异列包含 run/code、旧/新 t1/t2、旧/新主收益、旧/新成熟状态、失效原因、受影响行数及统计人口。
- [ ] 应用前校验源账本 hash 未变化，并保存精确目标文件 before 快照；写完候选 JSON 校验通过后替换，最后一次性重建 CSV。
- [ ] 中途失败不能记迁移完成；保留迁移状态，可从 before 恢复或以相同输入继续。原始 frozen run 与 lake 不参与替换。
- [ ] 重跑相同输入不新增重复 run/code；恢复验证不能只比较行数，要对目标内容 hash。
- [ ] 回填后对当前引擎受影响样本核验日历、价格源、收益公式和消费者读数；不预填另一引擎票价作为验收真值。

实现后的命令形式：

```text
python -m autoresearch.scan.outcome fill --rebuild --dry-run --today <YYYY-MM-DD>
python -m autoresearch.scan.outcome fill --rebuild --dry-run --run-id <report_run_id> --today <YYYY-MM-DD>
python -m autoresearch.scan.outcome fill --rebuild --run-id <report_run_id> --today <YYYY-MM-DD>
python -m autoresearch.scan.outcome line
```

恢复使用本次 diff 指定的精确 before 文件，不通过删除整个 ledger 或回退代码恢复数据。新正确账本的恢复由迁移记录追踪，禁止无声覆盖。

## 7. 验收矩阵

| ID | 输入 | 必须满足 |
|---|---|---|
| C01 | 缺 T+2，后面更晚分区存在 | 目标不漂移，MISSING_MARKET_DATA |
| C02 | 缺 T+1，T+2 与后面分区存在 | 目标不漂移，无有效主收益 |
| C03 | lake_partitions / weekday_heuristic 恰好对齐 | UNVERIFIED_CALENDAR，不放行 |
| C04 | 周末、春节/国庆长假 | 使用合成可信交易日历，准确跳休市日 |
| C05 | 日历异常、空范围、分析日无效 | 原因可见，状态区分 |
| C06 | T+2 尚未到但存在测试未来分区 | PENDING_SESSION，不提前成熟 |
| C07 | 分区齐全但本票停牌/缺价 | 行级 null，不能记零收益或已成交 |
| C08 | 迟到锚前一交易日缺湖 | 用日历前驱，不滑到更早分区 |
| C09 | 主尺成熟、5/10 窗口缺日 | 主尺保留，旁列 null/未成熟 |
| C10 | schema 1 complete=true 旧账 | 重新验证；未知日历旧收益撤出统计 |
| C11 | 重算失败，CSV 原有旧行 | 该 run 旧有效值失效，JSON/CSV 一致 |
| C12 | dry-run、精确 run-id、迁移中断/恢复 | 范围准确，dry-run 无数据写，前镜像可恢复 |
| C13 | 相同输入两次重建 | run/code 不重复、关键内容一致 |
| C14 | 毛收益、执行反事实、真实成交 | 标签和人口不混；未接 broker 仍未知 |

收尾：

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest -q
uv run --no-sync ruff check autoresearch tests
git diff --check
```

记录测试结果、日历来源摘要、受影响目标清单、迁移差异、恢复验证。真实另一引擎验收由对应引擎完成；本计划不授权读取其产物。
