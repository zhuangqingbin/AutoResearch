# scan-market 现场重建：证据归属、E6 解释与历史补录设计

> 日期：2026-09-12；修订：v2（按本轮评审意见重写）。
> 状态：用户已要求按评审意见修改文档；本次仅修订设计与计划，未实施代码、回填账本或启动扫描。
> 实施入口：[现场重建计划](../plans/2026-09-12-scene-reconstruction-transcript-binding.md)。
> 独立优先项：[P0 交易日历修复计划](../plans/2026-09-12-outcome-trading-calendar-integrity.md)。
> 原稿中的历史立案材料保留在附录 A；本版契约替代原稿的路径子串绑定、顺序配 attempt、整文件来源优先及宽松日历放行规则。

## 1. 目标与边界

对任一 run、任一只票，回答：有哪些可归属的研究证据、实际 E6 选择依据是什么、卡面与选择是否冲突、哪些环节无法证明。每个结论可追到同一份源快照，每个已知期望调用都有状态和原因。

服务对象是人读复盘。保留零 LLM、只记不学、冻结 run 不变、引擎隔离、现有评级与 E6 选择规则不变。不承诺从残缺 transcript 恢复完整判断过程；不把证据绑定成功等同于收益有效或研究完整。

本期分成三个可独立验收的交付：新 run 留证及视图、E6 解释、历史补录。交易日历是独立 P0，优先修复后才能把历史收益用于策略评价。

不在本期：实时 exec_check 采集、券商成交接入、自动归因/学习/调参、放宽门槛凑 BUY、修改 brief 预算或 agent 决策日志模板、捕获隐藏推理、全面逐字节重放系统。正常 0 BUY 是合法业务结果，不得为了验收制造买单。

## 2. 不变量

1. Codex 会话每条 shell 命令显式设置 `AUTORESEARCH_ENGINE=codex`。研究产物仅访问当前引擎根；Codex 禁止读写另一引擎的 context/reports。两引擎适配测试只使用合成 fixture 和临时目录。
2. transcript 链路不取行情、不改 `lake/`；P0 计划另行声明日历读取与行情只读边界。真实 transcript 只来自对应引擎的允许目录或已经校验的本引擎归档。
3. 已冻结 run 的文件集合、文件内容、MANIFEST 均不改变。补录只落当前引擎的 `scan/_ledger/`。
4. `contracts.stages.ROLE_STAGES` 是角色阶段唯一词表，`scan.run_profile` 决定当前模式是否需要该角色；不在绑定器手写第二张表。
5. 已知期望集合 E 的每个 invocation 恰有一行，候选缺失、重试失败、单行冲突均不能缩小 E。仅有产物推导的 E 是已知下界，必须标明分母不完整。
6. `PRESENT` 仅说明证据载体可读。归属强度、区段覆盖度、业务完成状态分别展示，均不能靠 PRESENT 推断。
7. 绑定失败不阻断业务发布，但必须降级证据状态、逐项记原因；不能吞掉异常后仍显示“证据完整”。
8. 保持 E6 `RULE_VERSION=e6.v3.0`、评级、候选池、硬门、排序、BUY/blocked 语义；原始决策与事后补充信息有明确版本和来源。
9. 标识使用 `contract_run_id`（capsule 身份）与 `report_run_id`（报告目录名）两个明确字段，不把二者互换；关联键始终带 engine。

## 3. 证据语义

### 3.1 观察到什么

| observation.kind | 定义 | 显示约束 |
|---|---|---|
| DISCOVERED | Glob/路径列表发现文件或命令中出现路径 | 只说发现，不说读到正文 |
| READ_REQUESTED | 存在读取请求，但没有可关联的返回 | 只说请求读取 |
| READ_SUCCEEDED | 同 call_id 返回成功、内容范围可识别 | 显示成功返回的范围；不暗示模型理解或采用 |
| READ_PARTIAL | grep/sed/分页/截断/多文件混合输出仅能证明部分内容 | 明示部分返回，不满足全文已读断言 |
| READ_FAILED | 返回错误或非成功退出 | 不计入成功读取 |
| WRITE_REQUESTED / WRITE_SUCCEEDED / WRITE_FAILED | 写请求、明确成功返回、失败返回 | 只有成功写入可作为强产物归属证据 |
| SEARCH_REQUESTED / SEARCH_SUCCEEDED / SEARCH_FAILED | 搜索请求与关联返回 | query、去重 URL 与返回范围分别记录 |

未知工具形状仍保留原始工具往返，不能静默消失或猜成成功读写。仅出现产物名、读取产物、讨论如何写文件、失败的 Write，都不是成功写入。

`NOT_OBSERVED` 是视图对“没有找到某项操作记录”的派生判断，不伪造为 transcript 事件。区段缺失、交错、坏行、工具未支持时显示“证据不足，未观察到”；只有确定覆盖完整且操作可识别时，才可说“在该调用记录中未观察到成功读取”。不输出绝对的“没读”。

deep 的预期来自卡种、早停状态和当时契约；早停不要求 deep 时显示“该路径不要求”。无法判断要求时显示未知，不额外给评级或处罚。

### 3.2 哈希与写入版本

- `tool_response_sha256/tool_response_bytes`：对工具原始响应的规范化表示计算摘要。响应为字符串按 UTF-8；结构化值按固定 canonical JSON；必须记录表示口径。
- `artifact_sha256/artifact_bytes`：对明确归属的文件内容字节计算。行号包装、工具提示、截断结果不能冒充源文件字节。
- `source_prefix_sha256`：本次读取的原始 transcript 截止前缀摘要；`archive_sha256`：脱敏归档字节摘要。两种摘要允许不同，标签不能混用。
- 发布卡一致性只对相同股票、相同产物、同一完整写入版本与发布卡比较。intel 比较对应 intel 归档，不拿 intel 与卡比较。
- apply_patch/Edit 只有 diff 时，不声称得到了完整文件 hash；只有可核对的前镜像与补丁结果，或确定性写入回执/快照，才能计算完整后镜像。
- 缺少关联内容、后续又有 Edit、存在多个版本、脱敏改变内容时，分别标记未知/版本变化/不可直接比较，不能用请求文本 hash 宣称最终发布一致。

### 3.3 可见文本

普通 assistant 文本标为“可见分析文本”；harness 明文摘要单列为“可见摘要”。加密内容不解密、不猜测，未落盘就记录缺失。文本只证明记录中说过什么，不证明所有影响决策的因素均被记录。

## 4. 调用身份、期望与绑定

### 4.1 身份

归属键为 `(engine, contract_run_id, session/thread, role, subject_key, attempt)`，引用现有 invocation_id。sector 的展示名与 subject_key 分开；已有事件给出的 sector 序号不重排。

角色产物仍为：l4-card → `details/<code>.md`；l4-intel → `_l4_intel_<code>.md`；sector-brief → `sector_briefs/<行业>.md`；l3-rank → `_l3_judged.json`；l3-repair → `_l3_repair_patch.json`；strategist → `market_view.md`。这些标记用于确认目标，不单独决定操作类型或调用边界。

### 4.2 期望集合

按角色、subject、attempt 合并已有 AGENT 事件与 TASK 事件，同一调用的强事实优先。不能因为全场存在一个 AGENT 事件，就关闭所有其他角色的回退。

- l4-card 可由 `TASK_CLAIMED/TASK_SUCCEEDED/TASK_FAILED` 派生，对应关系显式保存。
- 无事件的历史角色可以由 run 自有产物推导 `expectation_source=products`；保留原来的单例与按行业名排序规则，但 `terminal=null`、`boundary_quality=unknown`，不伪造完成事件。
- 产物只能证明某项结果存在，不能证明没有失败的前置 attempt；这时 `denominator_quality=lower_bound`。
- 候选中有强证据而期望中没有的调用列入 `unexpected`，不能借此提高 expected 覆盖率。
- sentinel/未到达阶段依现有 run_profile 标记，不凭最终没有 BUY 推断没有研究任务。

### 4.3 session 与路径定位

优先使用 run contract 的 `session_ref`，验证仓库、引擎及 run 关联。缺少 session_ref 时，日期和 cwd 仅用于缩小搜索，再用本 run 的有效操作证据筛选；两条普通并行会话不自动导致全场 AMBIGUOUS。

支持跨日恢复：按已知 session 找历史创建日期的 rollout；候选不在当天目录不等于缺失。可先扫有限日期范围，再使用对应引擎的完整 metadata 搜索；归一化只处理最终相关候选。显式记录搜索范围、候选数与耗时。

相对路径按该条调用实际 cwd 归一化。校验路径组件及归属根，拒绝跨 run、跨 engine、目录逃逸和不明 symlink。离线搬家使用经 run contract/归档证明的根映射；同日 `/scan/<date>/`、路径子串或 mtime 都不足以确认 run 身份。

### 4.4 区段

优先由现有 task/agent 边界以及 transcript 中可关联的工具调用建立 start/end，并闭合范围内的 tool request/result。新 run 在已有确定性入口传递可获得的 session/attempt 关联信息；不依赖模型额外手敲一份独立日志，不要求修改研究模板。

同票重试必须分别对应各自边界，禁止每个 attempt 都重用全文件的“最早输入—最晚产物”。没有显式边界时，只有证据唯一且不冲突的产物级关联可以成立，区段质量只能标 partial/unknown；intel 写产物之前的搜索无法归属时，不输出“搜索 0 次”。

交错区段允许保留，但其中工具往返只有可唯一归属的才进入本票确定统计；共享上下文单列。子线程也要核验 run/角色/subject，不能仅凭 parent id 把整个多任务线程当成一次调用。

### 4.5 状态与不变量

绑定报告采用两个正交字段：

- `binding_status`：BOUND / UNVERIFIED_BY_PRODUCT / AMBIGUOUS / GONE / ERROR。
- `segment_quality`：complete / partial / interleaved / unknown。

BOUND 要求唯一调用身份及可核验关联；UNVERIFIED_BY_PRODUCT 只用于调用身份已明确但产物写出未经证明的证据，不允许仅靠外部 run 的输入文件名绑定。是否有产物与是否有完整区段分开，BOUND 也可是 partial。

同一 invocation 有竞争性证据且无法消解才记 AMBIGUOUS；不要靠时间排序强配 attempt。一条候选不能未经边界证据同时认领两个 attempt。每条失败均留 reason 与候选引用。

报告 coverage 至少包含 `expected, accounted, bound, unverified, ambiguous, gone, errors, unexpected, denominator_quality`。`accounted == expected`，且五种期望行状态之和等于 expected。全部 accounted 不代表全部绑定或研究完整。

## 5. 一致快照、归一化与生命周期

### 5.1 单源一次快照

在 active run 的 observe 阶段确定截止前缀：固定读取时的字节长度，只解析其中完整 JSONL 行，记录截止字节、最后 ordinal、坏行/尾半行数量。后续追加留到另一个快照；截断/替换源文件需报 SOURCE_CHANGED，不重试拼出混合快照。

同一前缀先保存在内存，原始摘要、脱敏归档、归一化、usage 都从它派生。源内容不落未脱敏的额外副本；复用 `trace.identity` 与 `trace.atomic` 的脱敏、原子写和持久化规则。

按源快照去重归档，invocation 引用 snapshot_id 和区段。已有 raw 路径消费者通过 index 解析，保留旧版读取兼容。适配器提供同一份 rows 的 normalize/stats/usage 入口，不能 usage 再读活文件。

同源多个交错区段的 token 不直接相加。run 合计按可测的唯一 session 计数区间核算；不能可靠拆分到 invocation 就写 UNMEASURED，并保留 run 级计量。不通过均分或填零制造精度。

### 5.2 生产接线

`post_run.publish_run_observation` 内顺序：原有 E6 校验 → 绑定/快照与报告 → retain → finalize。finalize/materialize 消费此前选定快照，不能重新读活源改变身份。保留 `bind_transcript(stage=None)` 的旧调用语义，新调用显式传契约词表阶段。

`retention.bind_transcripts` 默认为 true；false、无 active run 都写带原因的禁用报告，不清除已有证据。逐 invocation 捕获冲突后继续，最终原子写完整报告。若报告本身无法落盘，通过既有 evidence degradation/event 通道留失败状态与 stderr；不能报成功。

重跑相同绑定、相同快照不重复追加。源文件增长但已闭合区段未变时复用原快照；需要修正已绑定区段则追加修订证据或报告冲突，不覆盖旧绑定。冻结后统一进入离线补录。

## 6. 历史补录与来源选择

### 6.1 离线索引

输入顺序：run 自有已归档证据 → 本引擎 ledger 中已核验的抢救快照 → 对应 harness 尚在的源文件。harness 消失后，已归档快照仍可独立生成视图。

以 `engine + contract_run_id + report_run_id + run manifest hash` 确认目标；所有输出在 ledger。索引记录源快照摘要、解析器版本、计算时间、重建身份和证据质量。坏文件按项报告，不使同场有效项消失；未知 schema 不按成功空列表处理。

相同源快照与解析器版本重跑不改证据；内容变化创建新的重建版本，顶层当前索引原子切换，保留上一版。归一化目录属于相应版本，避免新旧文件混读；`computed_at` 记录真实 UTC 时间，不参与内容身份、不固定成哨兵时间。

### 6.2 按 invocation 合并

有效 capsule PRESENT 优先；capsule 缺失/GONE/无法归一化时，可用同一 run 的有效 ledger 项补充。行上同时保留原始状态、补录状态、实际来源。两边有效但内容冲突时明确列出冲突，不静默挑选。

这解决“capsule 索引存在、全 GONE，ledger 已补齐却不可见”的场景。原始 capsule 的完好性结论不被补录改变；补录覆盖率另报。

### 6.3 抢救件

每件记录 source、source hash、source mtime、captured_at、target run identity、run 时间窗口及归属结论：

| attribution | 含义 | 可否充当本 run 事实 |
|---|---|---|
| VERIFIED_RUN | 有 run 身份或原有内容哈希等强证据 | 可以 |
| TIME_WINDOW_ONLY | 仅 mtime 与运行窗口接近 | 不可以，仅作参考 |
| OVERWRITTEN_BY_LATER_RUN | 有后续 run 身份/产物证据证明覆盖 | 不可以 |
| UNKNOWN / ABSENT | 无法归属 / 不存在 | 不可以 |

mtime 只作线索，不能单独证明归属或覆盖。同日多个 run 必须检测；UTC 与本地时间先按已知时区转换，不能直接去 tzinfo。不知道源时区则标 UNKNOWN。

逐文件抢救，不能因为 run 已有 E6 决策就跳过缺失的市场研判或 transcript。已确认归属的持久快照优先于可变 shared staging；其他抢救件只进“参考资料”，在片段旁标识，不参与 BUY、收益或覆盖率结论。

`salvage --all` 使用各 run 的真实取证窗口；幂等指证据不重复、不覆盖，stdout 稳定不能以伪造时间实现。批量结果包含每项成功/缺失/冲突；退出码非零表示存在未处理错误，不把正常 ABSENT 当异常。

## 7. E6 解释契约

### 7.1 卡面结构

`_relative_buy_decision.json` 升 schema 2，旧键与选择语义不变。每候选 `card_context` 包含：

| 字段 | 契约 |
|---|---|
| card_kind | earlystop / full / unknown；空卡或解析失败不伪装 full |
| proposal, ev_target, rr | 原机读提案与仪表盘原文；缺失为 null，不据区间中枢重算 EV |
| position_raw, trigger_raw | 保留仪表盘原文 |
| entry_stance | PROHIBITED / CONDITIONAL / ALLOWED / UNKNOWN |
| no_new_position | PROHIBITED=true；ALLOWED=false；CONDITIONAL/UNKNOWN=null，仅兼容字段 |
| exec_lines | 原文、操作符、阈值、presence、contract_match、contract_version |
| source | 相对路径、卡内容 hash、来源版本/是否事后补充 |
| parse_status, parse_errors | OK / PARTIAL / ERROR；逐字段记录失败原因 |

仓位“0%/0.0%”（作为完整仓位数值）、不建仓、不新开仓、不新建仓 → PROHIBITED；“待突破确认/满足条件才考虑/不追高” → CONDITIONAL；明确肯定的新开仓建议且无否定/前置条件 → ALLOWED；其余 UNKNOWN。不使用“没有否定词”推导允许。否定或零仓位与允许同时出现时展示冲突，保守记 PROHIBITED；数量匹配不得把“10%”误读成“0%”。

执行线 `presence` 与 `contract_match` 分开；历史卡按当时留存的契约版本验证，版本不可得就 UNKNOWN，不拿今天常量判历史漂移。`exec_lines_present` 若保留仅代表两行在场。

### 7.2 实际选择依据

解释在 `build_decision` 原有决策路径内取已有变量，不能另算一套排序：

- `observation_rank` 对应现有全体 eligible 排名；保留旧 `rank`。
- `selection` 记录实际 pool、最终 buy_pool 代码顺序、排序键名及值、入池/持仓排除、硬门结果、选中代码、池内名次和第二只未触发原因。
- composite 使用当前 `target_align → amount → code`；finalists 使用原有顺序。解释与实际选中的列表同源。
- 否决股票数按 code 去重；各门命中数单列，并注明可能重复。候选数、过硬门数、排持仓后数、最终池人数分别命名，不能都叫“合格”。
- `field_usage` 记录 hard_gate / ranking / display_only：EV/执行线/仓位在当前规则中为 display_only；评级、提案和现有门按真实用途声明。
- `conflicts` 至少记录选中票卡面 PROHIBITED、条件尚未证明满足、卡面缺失/解析未知。这是展示事实，不新增门。
- `why` 由以上结构化字段固定渲染，使用实际池内名次。不得把全体观察第 3 名写成“实际池内第 3 名”。

卡面读取/解析错误按单票隔离，card_context 留错误与原始片段，不能使 BUY 文档缺失。writer-1 与 writer-2 使用同一份已固定卡输入；相同输入字节级 parity。中途卡内容改变必须保留原决策并沿现有 mismatch 通道报差异，不能继续声称上下文一致。

卡输入快照在 write_decision 的 I/O 边界生成，active run 复用现有 capsule blobs 保存脱敏后的卡内容，source 同时记录原始内容摘要与归档引用，再将固定输入传给纯 build_decision。writer-2 核对当前卡摘要并用已记录快照校验；来源变更即走 mismatch。无 active run 或归档失败时保留卡面解析和 source.snapshot_quality=unarchived，仍可做相同输入的 parity，但不能承诺脱离源文件重建。不得在 build_decision 内新增落盘副作用，也不建立第二份选择文档。

schema 1、schema 2 卡面缺失、schema 2 解析失败分别显示；历史解释只补 ledger，不原地升级冻结的决策文件。新字段不回注 L3/L4，不改变 brief 或执行。

## 8. 视图与交易时间语义

默认 `chain_view <run> <code>` 输出摘要，目标上限 80 行；超出部分明确截断并指向 `--verbose`。固定排序与截断，同一冻结证据版本重复渲染字节相同；不把查看时刻写进正文。

摘要优先展示身份/来源、E6 实际选择、卡面冲突、成功/失败/部分证据计数、缺口、已有执行时间锚与收益口径。80 行内必须保留所有冲突类型、缺口计数及详细入口，不能用截断掩盖异常。

详细模式保留原链路各段，新增研究现场：逐项操作及 call_id、来源、字节/摘要口径、版本匹配、可见分析文本。长文本每块至多 300 字、至多 6 块，明确省略量并引用归档；搜索 URL 按结构化结果去重，不能用字符串中“http”出现次数代替 URL 数。

输入定位可查 `trace/inputs/slim/`、`trace/staging/_external_inputs/` 与已核验补录；shared 候选只有证明同 run 归属才进入事实视图。删去“>8KB 才可信”，字节数仅为诊断。

复用 `exec_anchor.read_execution` 的批准时刻、first_available_session、迟到状态。关联证据的 source_published_at、observed_at、decision_at（现有数据可得才填）；事后获取的信息只作补充，不能计作当时可用。时间缺失显示未知，不能因文件被归档就推断当时已看过。

主尺固定 T+1 收盘买、T+2 开盘卖。推荐毛收益、事后执行条件测算、迟到报告反事实收益与实际成交分别命名。日线收盘条件不证明盘中某时刻核验过；未接 broker 就显示“实际成交未知”。未计费用/滑点不能标净收益；样本计数同时说明 run 数、交易日数及同票重跑，不把相关记录当独立样本。

## 9. 模块与产物边界

`trace/transcripts` 负责 harness 解析与源快照；`scan/transcript_binder.py` 负责 scan 期望和绑定编排；`scan/chain_view.py` 只渲染；E6 原有模块产出选择解释；历史 CLI 复用同一套快照、绑定与来源验证。禁止把 parsing、usage、归档在 active/offline/salvage 各写一遍。

| 产物 | 根/生产者 | presence |
|---|---|---|
| _transcript_bindings.json | staging / transcript_binder | observe 执行时必须有报告，开关关闭亦有原因 |
| agents/index.json、bindings.jsonl、raw/、normalized/ | capsule / 现有 materialize 扩展 | 沿用 capsule 契约，新增字段升级 transcript schema |
| agents_index/<report_run_id>.json | ledger / transcript_binder --offline | 跑过该 CLI 时存在，完整当前视图索引 |
| agents_index/<report_run_id>/<revision_id>/ | ledger / transcript_binder --offline | 保存该重建版本的 normalized/ 与源快照引用 |
| salvage/<report_run_id>/provenance.json | ledger / salvage | 逐文件 provenance；文件快照按摘要命名保存 |
| acceptance/scene-reconstruction-<date>.md | ledger / 本引擎验收执行者 | 真实验收发生后记录，不由文档修订生成 |

先登记 `contracts/artifacts.py`，再执行 `contracts.emit --write` 并过 contracts 测试。ledger 版本目录内的 raw/normalized 用目录契约登记，顶层引用须校验 containment 和 hash。不新增 prelude 步骤，不额外建设服务、数据库或通用框架。

## 10. 验收与交付

完整反例矩阵及命令见实施计划。发布标准：

1. 规定合成反例集中跨 run/attempt 误绑定为 0；每个已知期望恰有一行，计数守恒。
2. 错误读取、部分输出、纯路径提及均不能生成成功全文读取/写入断言；未知证据明确降级。
3. 快照过程中源追加、尾半行、重复 materialize 后，raw/normalized/usage/hash 有同一快照来源；交错 usage 无重复求和。
4. capsule GONE + ledger PRESENT 能显示补录；已覆盖或仅时间吻合的 shared/salvage 不能改变当日 BUY 事实。
5. 原始 schema 1 仍可读；观察字段变化不改变 E6 决策投影；writer parity 与中途输入变更检测都通过。
6. 冻结目录前后对所有相对文件名与内容 SHA256 比较一致，另跑 capsule verify；不能只比较文件名。
7. 当前引擎合成成功 run、失败 run、0 BUY、sentinel/pinned 路径均可解释；不把 PARTIAL 或 lower_bound 包装为完整。
8. 真实验收按各引擎权限分别进行：记录实际覆盖/缺失，不设“必须 ≥34 PRESENT”凑数。另一引擎真实验收未做时明确标注；Codex 不代读 Claude 产物。
9. 正式上线前保存基线与修订后处理耗时、源字节数、唯一快照数；同源 raw 不随 invocation 数重复增长。默认视图预算与详细模式均有 golden。

回滚：关闭绑定开关停止新增采集，保留既有证据及读取兼容；E6 记录变更独立提交，可单独回退；历史 CLI 停止执行即可，不自动删除账本证据。P0 的回填恢复按独立计划，不靠回退代码清除已算数据。

## 11. 修订取舍

- 保留现有 capsule/事件链/原子写/脱敏/评级与收益尺，局部扩展。
- 新 run 的明确边界优于历史路径推断；未取得边界时标部分证据，不夸大覆盖承诺。
- 不新增专门的“完整推理日志”，不将证据数量、字节数或摘要长度当作研究质量。
- P0 日历修复独立优先；实时执行采集、brief 改版、E6 行为变化分别另案。
- 原稿 Q1 已在本次文档修订中改为独立 P0；修改文档不等于实施代码或批准回填数据。

## 附录 A 历史立案材料（原稿记载，非本轮重新核验）

以下保留原稿的调查记录和出处，便于对应历史问题。涉及另一引擎的路径只是文档出处，不授权 Codex 读取。原文统计、排名与判断可能受日历错误、规则版本及共享 staging 覆盖影响，不能作为本版验收真值。

| # | 事实 | 出处 |
|---|---|---|
| E1 | 09-09 run `trace/transcripts/_index.json` 37 份 PRESENT(l4-card 12、l4-intel 13、sector-brief 9、l3-rank 2、macro-brief 1);`capsule/agents/index.json` coverage `expected 36 / present 0`,每行 `status=GONE, reason="reached dispatch has no bound transcript"` | `reports_claude/scan/20260909-0909_2209/` |
| E2 | 688411 的 l4-card transcript(`l4-card-agent-a5abc4ddcd799ac25.jsonl.gz`,解压 101185 B):Read 3 次(`_l4_prompt_688411.md` 2244 B、`688411.SS_2026-09-09_slim.md` 8332 B、`_l4_intel_688411.md` 2253 B)、Glob 2、Write 1(`staging/2026-09-09/details/688411.md`)、StructuredOutput 1;text 块 6(1303 字);**thinking 块 5 个、字符 0**;model `claude-opus-5` | 同上 |
| E3 | `chain_view.py:302-305` 只在 `trace/inputs/slim/` 找 slim;真身在 `trace/staging/_external_inputs/688411.SS_2026-09-09_slim.md`(及 `_slim_deep.md`)→ ⑦ 段印「缺席(2026-08-26 前未留存)」 | `autoresearch/scan/chain_view.py` |
| E4 | `capsule.bind_transcript`(capsule.py:1562)、CLI `bind-transcript` / `materialize-agents` 存在;生产调用者只有 `autoresearch/analyze/runctl.py:245`;scan 路零调用 | `grep -rn bind_transcript autoresearch/` |
| E5 | `retention.archive_transcripts` 从 `_token_usage.json` 行(`agent`,`path`)定位 transcript;行里无 subject / invocation_id;usage 行 `agent` 取值:general-purpose 160(trace-control 壳)、l4-intel 12、l4-card 12、sector-brief 9、l3-rank 2、macro-brief 1、主会话 1 | `autoresearch/scan/retention.py:227`、09-09 staging `_token_usage.json` |
| E6 | capsule 事件链已有 `AGENT_DISPATCHED/COMPLETED`,`invocation_id` 形如 `l4-card-688411-1`、`l4-intel-688411-1`、`sector-brief-3-1`(subject=行业名)、`l3-rank-market-1`、`l3-repair-market-1`、`strategist-market-1`;由 `.claude/workflows/{scan-market,l4-stock}.js` 经 `capsule agent-event` 写入 | `capsule/agents/index.json`、`capsule/events/events.jsonl` |
| E7 | Codex 09-08 run:events 里 `SOURCE_READ 704 / TASK_CLAIMED 11 / TASK_SUCCEEDED 11 / STAGE_COMPLETED 11`,**零 AGENT_\* 事件**,agents coverage `expected 0`;`token_usage.md` 写「无 transcript」;`run_contract.session_ref=None` | `reports_codex/scan/20260908-0908_2246/` |
| E8 | 09-08 本仓 Codex rollout 恰一份(主线程,980 行,13:43→14:48Z),13 处 response_item 引用 `_l4_prompt_`、18 处引用 `/details/`;每行带 `ordinal`;`session_meta.payload` 含 `cwd`、`session_id`、`timestamp`、`source.subagent.thread_spawn.parent_thread_id`;工具调用是 `custom_tool_call name=exec`,input 为 JS 片段 | `~/.codex/sessions/2026/09/08/` |
| E9 | AGENTS.md 第 3 条:Codex 不派 subagent,按同一顺序**在会话内自己完成**各角色 | `AGENTS.md:26` |
| E10 | 65 个已发布 run:E6 决策在 run 内 6、只在共享 staging 13(08-07~08-25;12 份 mtime 与发布时刻一致,`20260817_2150` 被同日 22:15 重跑覆盖)、两处都无 46 | 本次普查脚本 |
| E11 | `_relative_buy_decision.json` 每候选只记 faces 四面 + 四硬门 + 分数;`relative_buy.py` 全文只读卡的 `proposal`,不读 EV / R:R / `[执行线]` / 「不建仓」;docstring 第 15 行「每个成功完成的交易日至少给出一只」 | `autoresearch/scan/relative_buy.py` |
| E12 | 四笔亏损 BUY 的卡原文:金螳螂 UW·SELL(入场否决「收盘仍 < 5.60 弃买」T+1 已触发)、兴业银锡 Hold「不追」、瑞丰银行 早停 Hold「不建仓」、海博思创 早停 Hold「不新开仓」(L3 `finalist=False`,靠守卫⑨强塞;09-10 又选 #5/5) | 各 run `details/*.md`、`chain_view` |
| E13 | CP7 定序:gate4 → usage_harvest → usage_reconcile → `post_run observe`(护照 → E6 → … → `retain()` → `_finalize_forensic_run`);`finalize` 内部第 1 步 `materialize_agent_index` → `_write_usage` | `SKILL.md:185-195`、`post_run.py:827-918,1116-1118`、`capsule.py finalize` |

### 原稿统计的使用限制

- 08-19 E6 转正起 9 次成熟 run、84 行:relative BUY 6 笔 5 负,均 −0.66pp、相对市场 −0.31pp;全部非持仓卡等权 −0.11pp、相对 +0.11(t −0.82);Hold 卡 −0.39(t −2.07)反比 UW 卡 +0.10 差;📌持仓 −0.36 不显著;执行线内 +0.11 vs 线外 −0.74(t −3.77)。
- 四笔亏损 BUY 的现场:四张卡全写不建仓或 UW·SELL;BUY 由 E6 相对层制造;3/4 是📌排除后的第三名;E6 不读 EV/R:R/入场否决。
- 09-01、09-07 两 run 的账本 T+2 取错(湖缺日滑到下一份文件),21/84 行错;`market.csv` 因 09-12 用补齐的湖重建反而正确——同一账本两套日历。

这些读数仅作为待核对线索。完成独立 P0 后，以当时规则、可核验的 run 来源和统一交易日历重算；明确 6 笔、未扣成本、单一 range regime 等限制。同日重复 run/同票的相关性必须保留，不据这组小样本直接改选股规则或宣称执行线有效。

上游参考：[法证 capsule 设计](2026-08-27-scan-forensic-run-capsule-design.md)、[现场留存与 BUY owner 设计](../../specs/2026-08-26-scene-retention-and-buy-owner-design.md)。
