# 局部失败恢复与报告完整性

状态：C2 本批实现与独立复核完成；实际验证结果以 [第四批开发记录](../research/2026-09-30-agent-skills-batch4-readout.md) 为准。

## 恢复原则

一项研究失败后，已接受的成功产物继续保留。runner 按既有错误分类和重试预算恢复失败任务，并继续执行其他已就绪任务。依赖未完成输入的任务继续等待；本批不拆除既有全局汇合点。

| 失败位置 | 恢复范围 | 成功产物 |
|---|---|---|
| 主卡或上游情报 | 既有 L4 ticket 重试流程 | 其他票保持原结果 |
| 第二次复核 | 对应 SESSION task 的新 attempt | 主卡保持原结果 |
| 第三次复核 | 对应 SESSION task 的新 attempt | 主卡与成功的第二次复核保持原结果 |
| 全局输入契约 | 阻断依赖该输入的任务 | 不以局部成功证明报告完整 |

任务重试采用 `TASK_ATTEMPT` 分类。合法偏空、早停和 UNKNOWN 输出是研究结果，不应当作瞬时故障反复重试；非法结构、虚假来源或边界违规必须保留失败事实。

## 两层 attempt

L4 ticket attempt 与 SESSION task attempt 分别记录。只重试复核时，票和主卡身份保持不变，复核的 SESSION attempt 增加。输出路径和接受规则沿用 [输出隔离](output-isolation.md)，迟到写入不能覆盖新 attempt 的已接受结果。

## 进度与发布

观察进度时分别检查任务终态、成功主卡、必需深核、复核与报告完整性。任务结束不代表成功，主卡成功不代表复核完成。冻结人口和任务计划定义分母，不能按当前成功文件数量缩小分母。任务簿或单条 SESSION 记录缺失时，冻结任务仍保留在分母中，缺失状态不计终态或成功。

`status` 的 `result.coverage` 提供以下诊断：

| 字段 | 含义 |
|---|---|
| `population` | 研究人口、冻结依据和任务簿差异 |
| `terminal_tasks` | 终态任务数与成功任务数分别统计 |
| `successful_cards` | 已接受成功主卡覆盖 |
| `deep_research` | 必需深核覆盖，保留尚未确定的触发条件 |
| `reviews` | 冻结复核计划要求的覆盖 |
| `report_completeness` | 图与研究覆盖诊断，不替代交付验证 |

覆盖项分别列出 `required`、`completed`、`missing` 与 `unresolved_conditions`。未知条件不计为完成；`report_completeness.authority` 明确要求交付仍以 `verify-report` 为准。

诊断性部分结果用于解释缺口和继续恢复。正式发布仍由原 `finish` 与完整性门决定：必需研究缺失不得发布完整报告，研究完整但 0 BUY 则允许正常发布。

## 运维边界

重试意图必须来自持久状态，runner 重启不重做已经接受的成功任务。历史记录如果无法安全恢复，应明确报告阻断原因，不能修改旧接受事实来凑成完成。

本机制不改变评级、主尺或 `session_v1` 的 PILOT 状态。真实报告交付仍需对 canonical 路径执行 `verify-report --level full`。
