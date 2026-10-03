# 研究质量、现场复盘与效率：实施交付记录

日期：2026-10-01。对应 [开发计划](../superpowers/plans/2026-10-01-research-quality-evidence-efficiency-development-plan.md)。维护入口见 [操作指南](../session-agent/research-quality-workflow.md)。

## 1. 交付范围与版本

本轮按用户“开始开发，一直开发到全部完成”授权实施软件部分。工程、真实宿主、人工语义质量、投资结果和效率结果分别记录。未成熟的外部证据保留待办，不用合成测试代替。

基线为原 HEAD `431d5dc01c539c1a5d68bdd2a7f8d80379eda110` 加当时工作区；原有 400 项变更/未跟踪状态已保全。1350 个源文件的字节、权限和状态记录在本引擎开发目录。隔离集成基线 commit 为 `7807cd0021b8e3bfb98d1299379058fee096b88c`，后续按任务提交；向原工作区交付仅复制本轮差量，逐文件对照基线拒绝覆盖并发修改。

机器证据根为 `context_codex/development/20261001-research-quality-evidence-efficiency/`；包括 `tasks.json`、`baseline/manifest.json`、`sync-result.json` 和最终验收摘要。精确交付 commit 与文件哈希以 `sync-result.json` 为准。分项评审与全量回归保存在隔离集成树 `context_codex/development/reviews/` 和 `context_codex/development/integration/`。

## 2. 任务交付矩阵

| 任务 | 实际实现 | 当前边界 |
|---|---|---|
| Q00 | 冻结脏工作区、分支隔离、任务台账、差量交付校验 | 未重置用户原有工作区 |
| Q01 | FRED metadata/observations 固定历史 vintage、时间精度、原始来源及取消传播 | 日精度不能证明同日盘中可得 |
| Q02 | 可信交易 session 统一人口和标签，各期限独立成熟，归档实际价格字节 | 缺失行情不顺延，暂缺可重试 |
| Q03 | 分析日全上市人口及历史行业基准，原始成员快照和原子发布 | 无可信历史名单/分类保持 UNKNOWN；历史更正仅 evaluation_only |
| Q04 | 集中注册资金流代理指标语义，更新 L3/L4/行业角色 | 净流入不能确认机构身份；数值口径不变 |
| Q05 | 登记 Tushare 回购字段适配器，来源字节及截止核验，根会话签发接通 intel 消费 | 其他来源/谓语 UNKNOWN；自填 reviewer 不能授信 |
| Q06 | 重大结论 FACT/INFERENCE/HYPOTHESIS 声明、映射和绑定全文及完整声明的审计 | 显式离线候选，AUDITED 仅覆盖声明范围，不重授生产 PASS |
| Q07 | 失败、取消、重试、拒稿及未派发现场诊断，增量现场和绑定补证 | 原始缺口保留，封存后不改原 capsule |
| Q08 | 根/子会话原生用量去重、累计/逐条辨别、模型身份与成本覆盖 | 存在未解释的 usage 缺口时不能宣布完整成本；补证用量不自动并入 |
| Q09 | 40 条合成启动案例、版本化案例库、事件隔离、逐条评价和遗漏分母 | 人工 gold 数仍为零；grader 需独立人工校准 |
| Q10 | 事前概率声明、完整执行日面板、现金/持仓/费用/净值、按日期聚类 | 按执行模式分组；缺证据不能变成零收益；不连接下单 |
| Q11 | 真实状态查询、真实交易日历及 60 日协议注册工具链 | 双宿主真实证明、未来输入及成熟标签待实际发生 |
| Q12 | 同 key 冷 miss 锁内复查、独立临时文件、原子发布 | NFS 部署语义未验；不推断真实延迟百分比 |
| Q13 | 宏观 21 产品与 six_groups 的成对质量/完整成本比较 | 缺同组真实配对运行，未采纳候选 |
| Q14 | 行业全人口、事件覆盖及复用失效的读数 | 市场变化不冒充纯价格变化；未新增无证据的事实缓存 |
| Q15 | 单股 FULL 七阶段事实、纠错、反证及成本增量读数 | grouped3 依计划待阶段价值证据后开发 |
| Q16 | 模型/effort/上下文单因素冻结比较，实际模型映射及事件 bootstrap | 未证明降配无损，默认研究设置不改 |
| Q17 | 调度 segment 去重、已观察依赖路径与等待读数 | 未证明瓶颈，不改变默认 READY 顺序 |
| Q18 | 实际模型/来源/prompt/代码漂移检测，四 skill 导航和维护纪律 | requested 模型相同不能证明实际版本稳定 |

## 3. 开发者入口

### 正确性与来源

主要模块：`dataflows/fred.py`、`common/outcome_sessions.py`、`data/benchmark_members.py`、`scan/outcome.py`、`research/production_export.py`、`data/metric_semantics.py`、`news/source_fields.py`、`data/cache.py`。

修正的是可得时间、交易 session、比较人口、指标解释和并发发布。旧报告字节不改；既有迁移链路使用 evaluation_only 生成独立修正评价，禁止恢复覆盖现有账本。历史来源不足时保留 UNKNOWN。

### 复盘现场

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m autoresearch.research.run_diagnostics \
  --run-ref <本引擎run或capsule> --output <新目录>
```

补证可用 `--supplemental-refs-json`，必须有原已派发 attempt/session/ordinal 绑定及原始字节引用。诊断原始活动覆盖、补证和用量覆盖分栏展示。失败现场不能由最终报告反推，NOT_DISPATCHED 不算已尝试。

### 质量评估

`research.material_conclusions` 提供冻结报告的离线声明/审计；`research.casebook` 负责启动候选及冻结案例；`research.quality_eval` 生成逐条结果、覆盖和错误分母。严重案例只有合格双人外部复核才可进入可评价 gold；软件只核验引用与声明，不认证真人身份。

### 执行与概率

`research.forward_study` 沿用 begin → day → finalize；执行候选通过 `execution.candidates` 冻结，概率声明还绑定数量/权重 `sizing_hash`，完整费用由 `configs.execution_cost` 绑定原字节及全部参数。`research.execution_audit --forward-study` 生成 `execution_day_panel.json`，哈希进入已有 manifest。真实授权成交、授权快照模拟及日线代理分开；不从评级推导概率。

### 效率与采纳

`research.profile_readouts` 覆盖 macro/sector/stock-stage/reuse/schedule；`research.stage_effort` 接受单因素冻结协议；`research.drift` 生成版本复查缺口。请求和结果都引用实际文件哈希，不能读另一引擎材料。完整参数、字段和命令示例见操作指南，模块 `--help` 为 CLI 入口。

## 4. 真实验证记录

### 工程回归

基线：8390 passed、12 skipped、4 subtests passed。一个原有湖单测会触发无关实时 akshare 网络调用，已隔离该 fixture 并保留原中断日志。

第三轮全量回归：**8682 passed, 12 skipped, 5 warnings, 4 subtests passed in 931.29s (0:15:31)**，命令 `uv run --no-sync python -m pytest -q -o faulthandler_timeout=90`，测试版本 `9bf1f8daf3335877325ea3eff000c7781b944eb0`，运行前后工作树均干净。结果见隔离集成树的 `context_codex/development/integration/release-full-regression-result.json`。前两轮失败日志完整保留（第一轮 11 failed / 7 errors；第二轮 1 failed，均已修复）。最终收尾仅补文档并删除两个 Python 文件末尾的额外空行；两文件 AST 完全一致，交付版本另跑定向回归。最终摘要绑定各自实际执行版本，不将旧结果冒充新运行。

FRED 捕获通过包入口显式注入下层回调；macro、stock FULL 和直接工具调用均保留收据，线程调用、失败及取消另有反例。

集成还修复了依赖向上穿层、漏登记产物、只读进度对象无 capsule、源码敏感词误报及档案离线复核缺根签发事件的问题；旧自填 deterministic 来源信任用例改为拒绝，并新增真实登记生产者的成功回放与缺事件/输入篡改反例。

独立复核已覆盖来源截止与更正链、未评分 gold 的召回分母、嵌套 JSON 类型、完整声明哈希、缺 usage、补证边界、真实模型控制、行业成员及标签恢复限制。Q10 补充审查纳入部分订单 UNKNOWN、完整费用冻结、数量匹配、零成本及未知行业集中度反例。

保留经基线确认的 30 项旧 lint：`evidence.py` 的后置导入 E402，以及 `analyze/harvest.py` 的 29 项既有导入问题（代码和消息与基线一致，避免删除兼容导出）；本轮新增 lint 为零。prompt 同步与配置标准检查作为最终交付门执行。

### 实际数据与历史现场

- 本引擎历史源/报告目录只读普查 747 个 JSON/Markdown 文件，100 个包含 FRED/系列标识等线索；结果记录在 `fred-historical-impact.json`，仅为潜在影响引用清单，压缩 capsule 未展开，逐报告重算资格仍待原始来源和截止核对。
- 有界 FRED 请求确认观测月份与当时已公开月份可能不同；该演练不等于同一观测值历史修订验证。
- 有界 Tushare 回购请求取得 21 行、3 个唯一已完成事件投影，字段适配及归档可离线重算；它们不是人工 gold 或宿主验收证明。
- 真实本引擎历史 run `20260917T084504245633Z` 的诊断保留 1 个 FAILED、17 个 NOT_DISPATCHED；原始宿主活动与完整成本不足，保持 UNKNOWN。
- 真实 Tushare SSE 日历覆盖 2026-10-01 至 2027-03-31：182 行、119 个开放交易日；来源字节与读取时间已归档。开放交易日安排未来仍可能调整，协议变更必须另行留痕。

## 5. 60 日观察与外部验收

本轮协议的固定窗口从 2026-10-08 开始，第 60 个分析日为 2026-12-30，最后 D2 标签按当前冻结日历于 2027-01-04 到达。实际注册状态和协议路径以开发证据根 `forward-readiness/` 的机器结果为准。未发生的日输入和收益不能预填。

首窗为 EOD_PROXY/none，比较 L5 选择与固定确定性 L2 基线，保留拒绝、失败、UNKNOWN 和空选择；日期等权、固定 60 日、预先登记训练/验证/测试窗与 bootstrap。日历的开盘/信息截止/标签等待时点属于预注册政策，不冒充源公开时间。本窗不评价真实成交净收益或真实执行概率。

实际查询仍为 0/28 REAL_SESSION、0/22 边界证明，默认 PILOT。宿主加载证明必须来自真实新宿主执行；本会话不能代签另一宿主。仍待 80 条真实人工 claim gold、每侧同组至少 10 次完整真实运行（分别披露运行数、可配对数与失败分母），以及未来 60 个分析日及成熟标签。真实节省率与研究准确率改善均未获证明。

## 6. 采纳、回滚和后续工作

本轮采纳经回归和审查的数据/标签/现场正确性修正、确定性工具及开发文档。质量和效率方法仍沿用冻结实验、留出评价和实际采纳记录。真实报告必须继续执行 canonical `verify-report --level full`；软件测试不改变宿主默认门。

回退按任务 commit 处理，同时保留现场和失败证据。已知前视、错日、候选内基准及缺计量伪完整问题不能通过回退重新标为可信。原工作区有既存变更，禁止直接 hard reset 到旧 HEAD。

后续操作者按指南补齐真实宿主证明，逐日冻结研究输入，成熟后归档标签，并在同组实际成本齐全后运行候选比较。任何后续行为代码变化先另开协议或遵守原冻结的中止规则；不能为保持协议 hash 而偷偷忽略代码变化。

### 首窗操作约束

协议目录：`context_codex/research/forward_studies/QUALITY_FORWARD_20261008_V1/`；是否成功登记以其中 `registration.json` 和开发目录的 `forward-readiness/registration-result.json` 为准。研究及逐日输入必须由协议绑定的干净工作树 `.worktrees/research-quality-evidence-efficiency` 产生。后续改动主工作区不改变该冻结版本；不要删除或修改此工作树。没有创建自动调度。

主比较通过现有 `stage_value.evaluate_candidates` 与 `summarize_daily` API 明确运行：`in_l2` 对比同一冻结 run 的 `is_buy`，仅消费 MATURE 的 `gap_c1_o2`，保留未知选择；标准 stage_value CLI 不自动执行 L2→L5 主比较。先左连接全部 60 个协议日期，失败、未开始、缺人口/来源、空选择和标签不全保持命名状态与 null，不补零收益。少于 60 个 COMPLETE 配对日为 INSUFFICIENT，不延长窗口、不凑买入。

每天以注册工作树内、当天截止前最早持久化 begin 的 scan-market run 为 canonical；同一 run 可在原截止前重试，全部失败与成本保留，后续新 run 不替换原 run。整日人口未在截止前冻结，则原窗中止或保持不完整，不能事后补写。完整约束已写入冻结 template 与 operating-boundary，标签最晚等待时点按当前冻结日历为 2027-01-04 18:00（UTC+8）。
