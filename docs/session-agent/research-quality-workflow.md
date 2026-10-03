# 研究质量、现场与效率的开发闭环

本页是维护者的操作入口。研究角色仍只读冻结角色片段与本次授权输入。所有命令先固定
`AUTORESEARCH_ENGINE=codex` 或本宿主实际引擎；禁止从另一引擎借档案、账本或 proof。

## 1. 各层结论分别交付

| 结论 | 证据 | 不代表什么 |
|---|---|---|
| 工程回归通过 | 实际测试命令、退出码、代码版本、独立复核 | 不证明模型研究准确率提高 |
| 事实有支持 | 原始 source receipt、登记字段适配器、时间和更正链 | 不代表隔夜上涨概率 |
| 声明范围已审计 | 绑定正文及完整结论列表的独立审查 | 不证明全文无遗漏 |
| 真实宿主可用 | REAL_SESSION、访问边界、报告核验矩阵 | 不由合成测试补齐 |
| 方法质量或效率改善 | 冻结协议、留出事件、同组真实用量、全失败分母 | 不由角色数或文本长度推断 |
| 投资效果 | 成熟标签、执行模式、费用和完整分析日面板 | 不把事后毛标签当真实成交 |

## 2. 当前工程修正

- FRED metadata 与 observations 固定历史 vintage；观测日期不能代替可得日期。日精度不足以证明同日盘中可得。取消原样向上传播。
- 人口和 outcome 共用可信交易日历；缺整日行情不顺延到下一份价格文件。各期限独立成熟，保留实际价格字节和日期。
- 行业基准使用分析日有来源的全上市市场名单与分类，包含停牌后复牌同业；当前名单不能回填历史。缺资格、价格或分类保持 UNKNOWN。
- 主动买卖单净流入、大单及特大单净额、小单净额为统计代理，不能确认机构身份。数值单位定义见 `autoresearch/data/metric_semantics.py`。
- 共享湖同 key 冷 miss 使用进程锁、锁内复查、独立临时文件和原子发布；不同 key 可并发。NFS 锁行为需部署地另证。
- `source_fields` 只对登记来源结构生成可信字段复核。首个为 Tushare 回购；未支持的来源/谓语保持 UNKNOWN。自填 reviewer/provider 不构成人工授权。80 条真实人工 claim 样本仍是独立成熟门。

旧错误标签只通过已有迁移计划生成独立 `evaluation_only` 修正结果，保留历史报告。派生评估禁止调用历史账本 restore。修正不会重新裁定旧报告评级。

## 3. 重大结论审计：显式离线候选

首版 `material_conclusion_audit_v1` 是实验 `experiment_family`，没有新增 begin 参数，也没有增加生产必需章节。
适用 `stock-lite/stock-full/macro-full/sector-full`。同一原文可以产生新的审计版本，原文不能改写。

1. 使用既有 `experiment_io.freeze_validated_spec` 冻结完整实验 spec；family 写 `material_conclusion_audit_v1`。
2. 对实际报告和 spec 建立 `{path, sha256}` 引用。只接受本引擎 context/reports 内文件，校验实际字节及解析后路径。
3. 准备 request JSON，字段为 `spec_ref`、`document_ref`、`workflow`。执行：

   ```bash
   uv run --no-sync python -m autoresearch.research.material_conclusions \
     --prepare --request <request.json> --output <本引擎context内的新task.json>
   ```

4. 当前宿主读取任务及原权限内材料，产出独立 declaration sidecar：`schema_version=1`、`document_sha256`、`conclusions`。
   每条结论字段由生成任务列出，区分 FACT/INFERENCE/HYPOTHESIS；包括 claim、计算引用、推断前提和反证。
5. 独立审查者通读原文与声明，记录遗漏候选、未解决冲突和审查范围。review 同时绑定 `document_sha256`
   与 `declarations_sha256=sha256(canonical_json(完整conclusions列表))`。相同 conclusion_id 换内容必须重新审查。
6. request 增加 `declaration_ref`、`claim_usage_refs`、`review_refs`，去掉 `--prepare` 执行审计。

输出保留 `DECLARED_ONLY/AUDITED_WITH_GAPS/AUDITED`、声明/映射数量、反证和来源引用。
已有 claim usage 必须带 producer/document 身份；本离线工具不重放 accepted-source 授信，故标
`REFERENCED_NOT_REVERIFIED`。reviewer_kind 是外部身份声明，不是身份认证。不得据此重授来源 PASS 或变更生产三门。

## 4. 案例库与质量评价

```bash
uv run --no-sync python -m autoresearch.research.casebook starter \
  --output <本引擎context内的新proposals.json>
uv run --no-sync python -m autoresearch.research.casebook freeze \
  --request <cases-and-split.json> --output <本引擎context内的新目录>
uv run --no-sync python -m autoresearch.research.quality_eval \
  --request <evaluation-request.json> --output <本引擎context内的新readout.json>
```

starter 生成 10 类各 4 条合成回归候选，人工 gold 为零，不能充当真实失败现场。应绑定真实输入、attempt、结论和 review refs 后再标注。
`research_case.validate_case` 是字段权威。严重案例需第二位真实复核者；单人标注如实保留。工具校验外部 review 证据引用，不能认证真人身份。
生成的 starter 文件同时是完整字段模板。`label_state` 为 `PROPOSED/HUMAN_SINGLE/HUMAN_REVIEWED`；
后两者分别记录一位/两位真实 reviewer、带时区 reviewed_at、review_refs、label_version 与 disagreements。
`gold_label` 仅为 `SUPPORTED/CONTRADICTED/INSUFFICIENT`；严重样本仅单人复核、有未解决分歧或缺输入均不能标 ELIGIBLE。
这些是研究案例的外部标注声明，不是 `source_fields` 的人工授信入口；不能通过填写这些字段给交易事实签 PASS。

freeze 的 request 为 `{cases:[...], split_spec:{train:[start,end],validation:[start,end],test:[start,end]}}`，时间区间右开。
同发行人/事件族及 group_id 的连通关联不跨 split；跨窗整组排除并留原因。目录排他创建，原引用变更即拒绝。
留出样本不进入 prompt 示例或调参；被查看、复用时登记污染并另建未来留出窗口。

evaluate 的 request 为 `casebook_ref/candidate_ref/grader_spec`。grader 固定 kind、version、prompt_hash、model、split、calibration_ref；
candidate 绑定 casebook hash、grader spec hash 和逐条 case hash，保存原始 label/rationale。缺人工 gold 和缺评价都保留分母。
确定性字段比较用 `quality_eval.grade_exact_fields`，严格区分类型、数值、单位、日期和缺字段；语义问题由独立模型或真人评审。
模型 grader 必须在人工集校准，校准材料不可来自测试窗。缺校准保持 UNKNOWN，不宣布质量提高。

## 5. 现场、用量和成本

诊断入口为 `research.run_diagnostics.build_run_diagnostics(run_ref, outcome_refs=...)`；以其模块 `--help` 获取落盘接口。
读取冻结 task/attempt、失败和增量宿主现场，只写新的派生 JSON/Markdown，不补写已封存 capsule。
活动可见性、工具响应缺口、取消和重试分别记录。NOT_REACHED 不是已尝试；缺原始现场不从报告存在反推完成。

根会话与子会话用量只按原生事件 ID 或可核对的 ordinal 去重；累计计量不能与逐事件重复相加。
失败/取消/重试均计入；不完整时展示 observed_total，total 保留 null。cached input/reasoning output 是子集。
requested 与 observed model 分开；缺实际模型名为 UNKNOWN。墙钟区间取并集，阶段时长相加只作诊断。

诊断 CLI 为 `python -m autoresearch.research.run_diagnostics --run-ref <run或capsule> --output <新目录>`。
封存后补证使用显式 `--supplemental-refs-json`，绑定原有已派发 attempt、宿主 session/ordinal 与原始字节；
原现场缺口保持，补证单列，不自动抬高完整成本。没有原合法绑定的外部材料不能冒充本 run 现场。

### 概率声明与执行日面板

沿用 `forward_study begin --sources`，执行观察额外冻结 `configs.execution_portfolio` 与 `configs.execution_cost`，可选 `configs.probability_training`。
`execution_cost` 是 `execution_import.load_policy` 可读取的完整费用 JSON，冻结原始字节哈希；消费时核对引用及全部费用参数，版本名相同不能允许参数事后变化。
组合配置登记初始现金/头寸、固定数量和权重、最多持仓、重复计划规则、费用、现金基准和快照模拟规则；不能看收益后改仓位。
旧 `EOD_PROXY/none` 模板继续可用。使用真实成交文件是调用者明确授权导入；工具不连接账户或下单。

每日已有 `forward_study day --inputs` 列表可增加 `artifact_id=execution.candidates`，内容为
`{schema_version:1,status,candidates}`。每项包含 run_id、plan、declaration、qty、weight，可含 sector。
概率声明包含 event_id、p、declared_at、plan_hash、sizing_hash；后者由 `probability_eval.execution_sizing_hash(plan, qty=qty, weight=weight)` 生成，绑定事前数量与权重。
实际成交数量不匹配，或实际买入支出超过冻结权重上限时，`y=null` 并保留理由。拒绝声明 p 时说明 abstention_reason，不从 conviction 或评级换算。
捕获的实际本地时间也必须早于入场，输入字节归档；仅自填更早 declared_at 不通过。

```bash
uv run --no-sync python -m autoresearch.research.execution_audit \
  --experiment-id <新ID> --policy <冻结费用JSON> --forward-study <STUDY> \
  --trades <明确授权的成交CSV>
```

无真实成交不传 `--trades`；有授权快照或订单证据可分别使用 `--snapshots/--order-status`。
`execution_day_panel.json` 哈希进入原审计 manifest，包含所有分析日、研究状态、执行状态和存续持仓。
EOD_PROXY、SNAPSHOT_SIMULATED、OBSERVED_FILL 分开；日线不能制造可成交价格或平仓。
缺费用断现金可知性，缺估值断净值可知性；部分成交/超窗头寸持续保留。计划窗、实际现金流、实现/未实现盈亏分栏。
概率按真实计划事件去重并按日期聚类，缺标签保留分母；训练基线只读独立训练窗，缺训练数据保持未知。

## 6. 候选优化的比较入口

`research.profile_readouts` 支持 `macro/sector/stock-stage/reuse/schedule`，均使用 `--request/--output` 和原始引用。
`research.stage_effort` 使用相同参数形式，spec family 为 `stage_effort_v1`；`quality_constraints` 固定单一处理因素、
基线/候选值、min_events、非劣界、bootstrap_samples、seed、confidence。参数改变须新实验。

| 候选 | 固定输入与观察 | 当前采纳纪律 |
|---|---|---|
| 宏观 serial21 / six_groups_v1 | raw_pack/intel/frame/cutoff、模型设置、相同 optional；21 份产品逐份核对；六项质量引用 | 方法和成本证据齐备后另作采纳记录 |
| 行业 legacy / deterministic-v1 | 全部 L3 行业人口、当日地形、重大事件、方向边界 | 没触发事件与漏行业分别计数 |
| 稳定事实复用 | 真实 invalidations，原始 receipt、有效期、事实依赖 | 有证据证明过度失效后才开发事实级缓存 |
| 单股 FULL 综合链 | 七段新增事实、纠错、反证、判断变动及成本 | 缺基线不实现 grouped3，不向 begin 添加未支持字段 |
| 模型/effort/上下文/搜索 | 一次只变一个因素、事件聚类、难度与冷热缓存分层、全部失败 | 实际模型/effort不可观察则不当作已验证处理 |
| 调度 | 原始 segment、READY 等待、槽位空闲、确定性 lane、已知依赖链 | 缺真实瓶颈不改变默认 READY 顺序；单一状态写入者不变 |

配对 bootstrap 是声明口径下的描述性方法检验，不认证宿主或 grader。价格估算、文本字符数和真实 token 分栏。
Q13/Q14 每侧同组至少 10 次完整真实运行，双方次数与配对数分别披露；少于最低样本不宣布稳定节省。
宏观 `quality_refs` 的六个键为 `artifact_contracts/fact_references/risk_coverage/disagreement_retention/allocation_rationale/actual_measurement`。
每份 review 包含 verdict、两侧 run_id 和两侧 manifest 完整 `baseline_sha256/candidate_sha256`，原引用改变须重新审查。
模型处理的 `stage_effort_v1` 还须冻结 `model_resolution:{baseline,candidate}`，实际模型要匹配映射；所有非处理模型/effort控制项保持已观测一致。

## 7. 版本、漂移和回滚

每次工程修正/候选方法建立独立 readout，列出真实代码、输入/协议引用、完整结果与失败、采纳范围、owner、回滚版本。
漂移工具 `research.drift --request ... --output ...` 读取 previous_ref/current_ref；实际模型、宿主适配、来源 schema、prompt 或代码变化触发复查。
实际模型未知也触发补证，不因 requested 名称相同宣称稳定。每次上述变更及每个预注册观察窗口结束时执行固定 canary；频率属于协议。

canary 使用未污染的冻结事实、数值、权限、契约和成本样本，多次随机运行保留区间。PASS 只覆盖这个集合。
不根据短期盈亏自动改 prompt、三门或配置。工程修正可在相应回归/宿主门通过后发布，候选研究方法按自己的冻结条件采纳。
回滚保留失败实验与已发布历史，不能恢复已知泄漏的 FRED 版本或错误标签。

## 8. 外部验收与 60 日观察

先运行 `session_agent acceptance-status` 与 `session_agent.boundary_proof status`；缺 proof 按真实状态保持 PILOT。
本宿主不代签另一宿主；hook 修改后重载并完成真实允许/拒绝演练。每份报告仍必须 `verify-report --level full`。
60 日协议使用已有 `research.forward_study begin` 冻结干净代码、真实源交易日历、prompt/config及样本窗；
之后用 `forward_study day --directory <协议目录> --date <分析日> --inputs <输入清单>` 逐日冻结，成熟后 finalize。不得预填未来输入或收益，不因读数有利提前停止；故障按原协议中止后另开窗口。
80 条真实 claim gold、真实双宿主证明和未来 60 个分析日不能用本轮软件回归替代。

本轮固定窗口、canonical run 选择、L2→L5 主比较 API 和不完整处理见[交付记录](../research/2026-10-01-quality-evidence-efficiency-readout.md#首窗操作约束)。实际注册状态以协议 registration.json 为准。
