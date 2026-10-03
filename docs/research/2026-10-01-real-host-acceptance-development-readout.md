# 真实宿主验收后续开发记录（2026-10-01）

本轮按 [开发计划](../superpowers/plans/2026-10-01-real-host-acceptance-remaining-development-plan.md) 实施。软件回归、真实工作流核验和真实边界验收分别记录；测试 fixture 不进入 production proof 目录。

## 隔离与基线

开发分支 `codex/real-host-acceptance`，工作树 `.worktrees/real-host-acceptance`。以当前主工作树的实际源码（包含已有未提交修复）建立快照 `ad2e1e5b1e59728fe82141018ba6b1667790d431`，基线定向测试 120 passed。未以旧 HEAD 替代当前代码。冻结前向观察工作树 `.worktrees/research-quality-evidence-efficiency` 不参与开发。

基线与诊断审计位于原工作树 `context_codex/development/real-host-acceptance/20261001/`；隔离分支的测试日志位于开发工作树同名目录。审计记录不属于真实宿主 proof。

## 宿主能力诊断

D01 已完成调查。已测 Codex 宿主版本 `0.159.3`，根 session `01a0f629-925e-7a91-8d8b-13e3042d8e63`。拒绝探针只有外层调用 `call_dvQ7zH61SmOrzCpOCMyuOxkb` 的 request/result；hook 观察中的 `exec-53144d54-72bc-4c82-82a9-602547ba064c` 在该子会话全部 93 条原生记录中不存在，拒绝 turn 没有 CommandExecution。现有验证器返回 INVALID，符合证据边界。

D02 为 `BLOCKED_EXTERNAL / HOST_DENIAL_CORRELATION_UNAVAILABLE`。需要宿主提供能唯一关联真实请求、hook 决策和原生拒绝结果的 ID/上下文链。相近时间和错误文本不能代替这个关联；不在项目中补造执行记录。

D03 为 `BLOCKED_EXTERNAL / STRUCTURED_OUTSIDE_PATH_PROBE_UNAVAILABLE`。当前暴露 `apply_patch` 和 `view_image`，但既有 hook/proof 尚未将其注册为所需路径读写路由。固定 artifact broker 不能表达清单外路径。通用 shell 拒绝不能计入 outside_read/write。诊断见 `native-denial-diagnostic.json` 与脱敏最小样本；后续适配需要真实工具 payload 和可关联拒绝链。

R01 因上述能力和最终政策的新宿主重载条件尚未满足，保持阻塞。新代码会改变 policy hash，旧 proof 保留作历史记录，不自动计入新政策下的集合。

## 开发进展

D04 已实现显式 proof 集合索引与原子 active 指针；独立规格和质量复核均通过。实现提交 `0a6046fc01ea96b2a936ab2671d1c5f1736e712d`，定向测试 158 passed，独立复核另跑 selection/proof 两组 121 passed。

复核发现并修复了非必需宿主错误污染单宿主 gate 的回归，修复提交 `ef76863b0a5c0d081e3299d25262969c12a889ae`；最终定向回归 161 passed。单宿主 gate 不受另一宿主 policy 漂移影响，双宿主 gate 仍拒绝。

D05 已实现当前可信 root 会话观察投影与原生 metadata 解析，`preflight_roles` 已接线。提交 `b1e416dd4153795c0a38f76877b8411e964335dc`，定向回归 199 passed；独立规格复核发现的畸形子会话 metadata 问题已在 `0d61121b48683c36cffcd512600979bbd50ca8ef` 修复，补充回归 54 passed。最终质量复核通过。另在 `1d3a37eb6a294b96350ba48f923fea78ed79e17d` 隔离快照解析异常，真实临时坏日志与 preflight 回归 55 passed；独立复核确认异常只产生诊断，不改变 runnable。

D07 已实现公开 `precheck` 服务与 CLI，提交 `0a751e7f96d6455a039607977469ecc8c1e786a3`。首轮合并定向回归 171 passed。规格复核发现 compact canonical task 的 deep 输入回退缺失，`7f2338ab8658463bb9d333715fba09b96b3e8ab7` 修复并通过 118 项回归。质量复核发现两阶段预检重复获取任务锁，`4a44b7c66b732be446943f9505fdfa4db96c2fae` 改用锁内记录的只读副本，75 项回归通过；独立规格与质量复核均通过。只支持 layout v2；缺匹配 receipt 保持 pending。

D06 已实现根侧专用 canary 演练工具（`eeef4e0`），全部 canary 异常变化检查（`59cb2a9`）及 helper 副作用前路径检查/观察分类修复（`553d6ec`）已通过规格复核，定向回归 146 passed。质量复核发现的 active 指针并发窗口与 bytes/mode 恢复窗口已在 `5a1f46f` 修复，POLICY_FILES 已纳入工具。最终相关回归 318 passed，独立质量复核含 6 项定向测试和并发/中断复现均通过；实际演练另记，不以软件结果替代。

## 单股来源资格盘点

S01 调查发现旧成功 run `20261001T092950456043Z` 的 77 条收据中没有现有回购 adapter 所需的 `tushare.repurchase`；64 条非 host_tool harvest 收据均为 v1，可得时间晚于本 run 冻结截止。旧卡 11 项声明均 SOURCE_NOT_BOUND，无 material sidecar。旧报告不作事后资格升级。

原始 `tushare.stk_factor_pro` payload 确有 `600519.SH / 20260930 / close=1258.62` 的唯一行，但 trade_date 不能证明 first_available_at。新增未复权收盘价 adapter 还需独立登记 predicate、金额语义和可得时间来源；仅数字匹配不能授予事实资格。

完整逐项盘点及最小后续改动见 [S01 来源资格调查](2026-10-01-stock-lite-source-qualification-inventory.md)。

D04/D05/D07 的最终集成复核已通过，独立定向测试 131 passed。完整仓库测试已完成：8883 passed、12 skipped、5 warnings、4 subtests passed（1019.73s，退出码 0）。该次收集在 D06 新测试落盘前；D06 新增项和其后 owner 锁改动另有定向回归，不把两批数字相加冒充一次全量。

## 真实工具路由回归

首个实际 D06 batch `1481d1f4df87cc36214ccc59e068ffdd` 使用真实子身份 `01a0f7af-5b8d-7043-a953-15b9781273fd`。allowed_read 的 broker 读取成功；宿主 hook 实际记录 Bash/command，初版 challenge 为 exec_command/cmd，且包装输出形式不是原 native join 支持的形式，因此该 case 保持 OBSERVED_UNPROVABLE。allowed_write 尚未执行即结束该挑战，保留 NOT_OBSERVED；没有补造 proof。

`52cc9c3` 修复固定路由表示，并返回原解析器已支持的精确 wrapper；未改变 validator/native join。333 项相关回归通过，独立复核另外 18 项通过。原批次与诊断保留在本引擎开发/边界目录。完整最终政策矩阵仍需新宿主重采。

## 本轮真实边界演练结果

保留原批次后，使用修复后的精确 wrapper 只执行此前未执行的 case，以验证工具路由。这个跨修复批次不计作最终政策的完整 R01。各项真实结果如下：

| case | 实际观察与归档结果 |
|---|---|
| allowed_read | 真实读成功；初版工具路由不匹配，OBSERVED_UNPROVABLE |
| allowed_write | challenge 已建立但未执行，NOT_OBSERVED |
| deep_before | 真实 DENY；原生关联 ID 缺失，OBSERVED_UNPROVABLE |
| deep_after | 真实 ALLOW；签发、导入后 VALIDATED |
| arbitrary_shell、identity_spoof | 真实 DENY；原生关联 ID 缺失，OBSERVED_UNPROVABLE |
| stale_attempt、tampered_binding、missing_binding | 真实 DENY；原生关联 ID 缺失，OBSERVED_UNPROVABLE；故障已恢复 |
| outside_read、outside_write | UNSUPPORTED，未用其他路由替代 |

所有受检 canary 均无意外变更，批次 aborted=false、pending=null。完整诊断见原工作区 `context_codex/development/real-host-acceptance/20261001/probe-summary.json`，其 gate 是选择集合前的快照；选择后的当前结论见同目录 `after-boundary-status.json`。

本次唯一新导入 proof 为 `deep_after`，hash `d3e08f1051caaa791b685d1d7e894ca08c5971c6e4d7a2e476e81ffcffd874cc`。D04 通过真实 `boundary_proof select` 将它明确选为 Codex 当前集合；旧 read/write proof 保留在历史诊断，未删除或改写。当前政策 `6905a2b1a4eb0663a64f50a6b0eba7c0385d762642b23842dfc31fbda292a3e4` 下 Codex 为 **1/11**，双宿主门为 `PENDING_REAL_HOST_VALIDATION`、invalid=[]、missing=21；本引擎目录尚未导入另一宿主 proof。当前可信 root 会话投影也仅有 deep_after，`entrypoint_observed=false`、`acceptance_satisfied=false`，不能由一条 ALLOW 推断完整入口已验收。

## R02 新真实 LITE 工作流

新 run `20261001T134913840836Z` 对 600519.SS / 2026-09-30 完成真实 harvest → stock.card → validate → publish，四个任务均为成功。真实 L4 子身份为 `01a0f7bd-7eff-7850-a450-babdb17b5b09`，根身份与本轮诊断相同；输入与角色片段在 claim 时冻结，使用固定 broker 读取与写稿。harvest 的 15 项 B 级增强数据缺口保留在原始日志，没有绕过 A 级数据契约。

公开 precheck 首轮拒绝可选情景块中的 null 数值；修订后领域检查通过，但 source claim-use 审计仍显示 8 项 SOURCE_NOT_BOUND。根将实际审计反馈给同一活跃研究 attempt；研究角色将四维改为未核、三门 UNKNOWN，并独立修订为 Hold/HOLD、P3 数据不足早停，禁止入场。结构 PASS 不代表来源资格 PASS，未用“无偏离”文案掩盖缺口，也未补造 sidecar。最终候选 hash 为 `c450345272d03d603395e63966548f0a80917e01feb7bd4267fac8ea724ac778`。

先完成全部修订，再绑定原生 transcript ordinal 0–103；binding 为 `c2cafbe31bc311406b2a5bbe4bbca95a797386878ca2d197966062668b99797c`。携实际 receipt 的最终公开预检为 domain PASS、host VERIFIED、can_submit=true；正式 submit 重新捕获同一 hash 并 ACCEPTED。deterministic CLI 的业务输出与末行机器 JSON 都保留在 raw log；归档提取末行时严格核对 command、errors 和退出码，没有重新执行已成功的任务。

finish 返回的 canonical 目录及 bundle 中的报告 artifact 确定了 [新报告](../../reports_codex/analyze/runs/20261001T134913840836Z/p1/report/贵州茅台_lite.md)。其 full VerificationResult 五项布尔值全 true、missing/diffs=[]、compute FULL、model EVIDENCE_ONLY。ROOT 为 `1420b729327cc8440cd41618bdbbeda27b919a2b15db332d372a177174070abf`；机器原件归档在本轮开发审计目录 `r02-verification.json`。这证明流程和报告字节绑定，不证明市场摘录已取得来源资格。

离线 replay 真实执行 3/3 确定性单元，全部 MATCH；scene COMPLETE、compute FULL、isolation ENFORCED、missing/diffs=[]。模型单元为冻结输出重注入，明确标 EVIDENCE_ONLY。REAL_SESSION [工作流 proof](../../reports_codex/_acceptance/proofs/codex/stock-research/20261001T134913840836Z/lite-early-stop.json) 已签发，hash `af099fe7d06c0621f6547e7536dc3e12b0530f6d0ba577df659ff0d609ff01b4`；[核验与 replay 审计](../../reports_codex/_acceptance/host-01a0f629-925e-7a91-8d8b-13e3042d8e63/20261001T134913840836Z/verification.json) 已归档。旧 records 原字节归档到 `_acceptance/record-history/`，旧 canonical 和旧 proof 保留；当前索引只替换同一场景，未重复累计。

运行 `acceptance-status --records-file reports_codex/_acceptance/records.json` 重验：accepted_count=1、required_count=28、invalid_records=[]，接受 `codex:lite-early-stop`，默认仍 PILOT。省略 records-file 的调用按空输入返回 0/28，该原始输出单独保留为 `after-acceptance-status-without-records.json`，不作为本地索引状态。R02 至此为 REAL_VERIFIED；R01 未闭合。

## 交付与仍需工作

本轮 A 层软件工作已经完成；D02/D03 采用经真实样本确认的外部不支持结论。B 层的新真实 LITE 已通过，完整边界仍为 BLOCKED_EXTERNAL。S01 仅完成盘点，typed close adapter、可信 payload 可得时间接线及 root-owned 声明绑定尚未实施；R03 双宿主固定矩阵仍未齐，不能启用默认入口。

下一步可执行项已经写入开发计划：宿主需要暴露可唯一连接请求、hook 决策、拒绝结果的原生字段，以及已登记的结构化清单外路径工具路由；取得这些能力后在新宿主加载最终 policy，按 D06 重采 11 项。来源资格按 S01 最小事实范围另行接线，新真实 run 验证，旧报告不回填资格。

本轮源码与操作文档以逐文件 hash/mode 冲突检查同步回原工作区，仅覆盖本开发分支相对实际基线的差量；保留原工作区既有未提交改动，保留开发分支及工作树。全量测试 8883 passed / 12 skipped 与后续 D06 定向 333 passed 属不同批次。最终旧成功 canonical full 核验仍五项 true，hash `75ae982a21d87851c3f32550137812424c229844c12a8d1b58b2a8f45949bda3` 未变；旧失败发布未改写。冻结工作树收尾只读核对仍为 `3b4c9549ee042bc621bb38b354ba2f0aa9574ad0` 且 status 为空。
