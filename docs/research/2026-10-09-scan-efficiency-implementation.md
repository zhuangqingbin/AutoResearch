# 全扫计量、情报复用与恢复实现读数

2026-10-09 用户确认方案后实施。生产模型、effort、评级三门、隔夜主尺、覆盖率和复核策略保持原值。

| 已实现项 | 实际行为 |
|---|---|
| 跨来源计量 | 整份 headless rollout 覆盖绑定的部分区间，只计一次；保留任务/attempt/区间归因。CLI exit=0 的 DOMAIN_VALIDATION 拒绝也记录，容量失败单列。Codex 美元成本未知。 |
| 情报复用 | 卡片修订只复用同 run、同分析日、UTC+8 当日、哈希/接受/访问/事件链证明完整的 FULL 情报。新 attempt 重跑完整守卫和来源授权；缺证明则新研究。冻结后的恢复不重新计算复用选择，避免午夜或任务状态变化造成图漂移。 |
| 容量暂停 | USAGE_LIMIT 停止主循环和事实预取回调中的所有新派发，收取在飞结果；不耗掉 L4 票据 attempt。MAX_ROUNDS、墙钟超时或中断也读取持久失败事实，保留可恢复 run。 |
| 单任务恢复 | recover-task 绑定精确失败 attempt、error、claim、输入哈希，记录一次授权与原失败；已接受工作不重跑。研究/身份/证据拒绝不允许借此绕过。 |
| 组装/发布恢复 | scan_run --resume-run-id 跳过 begin/readiness；ACTIVE 原图继续，已封存 SUCCEEDED 的发布事务仅 finish；仍有 runner 时不清扫、不封存。成功仍校验 canonical 报告。 |
| 组装 checkpoint | 候选报告只通过显式 staged_report_dir 捕获，限本 run 的 staging；发布报告根校验不变。 |
| 实验入口 | intel high/medium、sector medium、skills.max_context_tokens=1000 的隔离冻结清单及读数。卡片/用量/独立线程与输入内容均绑定，重复材料不能冒充独立样本。未进入生产。 |

原首轮 `20261008T143002429765Z` 的只读重算与此前审计完全一致，未改写原冻结账本：

| 指标 | 校正读数 |
|---|---:|
| 独立 transcript | 31（原账本 62 行重复记录） |
| 未缓存输入 | 1,496,240 |
| 缓存输入 | 8,381,184 |
| 总输入（含缓存） | 9,877,424 |
| 输出（含推理输出） | 219,308 |
| 推理输出（输出子集） | 112,115 |
| 加权输入代理 | 2,334,361 |
| 领域拒绝 / 重试 | 3 / 5 |

这修正了计量，不等于实际订阅消耗下降 50%。情报复用和避免整场重跑的真实节省比例须由下一场有效生产读数验证。

大范围测试：`tests/session_agent tests/trace tests/scan tests/research tests/contracts/test_forensic_contracts.py`，6460 passed、11 skipped、2 failed、4 subtests passed（24m59s）。失败仅为 `test_job_survives_when_caller_process_tree_is_killed` 和 `test_live_pid_with_a_different_start_time_is_stale`。同一沙箱下对 git HEAD 的独立源码副本运行这两项，仍是相同的两项失败：`ps` 被拒绝，以及因此无法获得进程启动时间。没有为消除这些环境失败而放宽进程身份逻辑。

最后一轮针对本次改动的测试结果记录在开发计划。配置标准 0 条违规；静态 F 规则与 diff 检查通过。所有推理测试使用假 CLI/冻结 fixture，没有启动新全扫或模型实验。

运行方法见 [恢复说明](../ops/scan-recovery.md)，实验方法见 [实验说明](../session-agent/scan-efficiency-experiments.md)。当前沙箱拒绝零推理目录探测命令，真实目录缩减效果与宿主 C4 验收仍为 UNVERIFIED；实验没有运行适配器自动改变生产 DispatchRequest，生产入口保持 PILOT。历史 FAILED run 未被重新激活。
