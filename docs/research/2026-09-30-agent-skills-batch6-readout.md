# Agent Skills 第六批开发记录

## 范围与基线

用户继续授权开发。本批执行总计划 C5：逐票流式复核、受控确定性计算与推理重叠、单写入者状态提交和调度观测。交易主尺仍为决策卡与 `gap_c1_o2`，不改变评级三门。

- 工作树：`.worktrees/agent-skills-reliability`。
- 本批开始前，原工作区和工作树 1,297 个源码/文档文件逐字节一致。
- 保全目录：`context_codex/development/20260930-agent-skills-batch6/`。
- 基线归档：`baseline.tar`。
- SHA256：`dbe0f7a65a04bbd758e75ef85afe23628ecca1c4a9fd12dd88d844824bcdd131`。
- 保留用户与前五批修改，不以 Git HEAD 代替本批基线。

## 前置修正：开发角色边界

实际开发子任务触发 C4 hook 后发现，明确的 `worker` / `explorer` 开发角色被误当作无绑定研究身份。新增两个未绑定开发角色的明确例外；已有研究绑定仍优先，不能通过将角色字段改成开发角色绕过边界。

先复现 2 failed / 9 passed，再通过完整 hook 定向回归 53 passed / 6.24 秒。为继续开发，已只同步 hook 修正到原工作区，并保存 `development-role-sync.json`；其余本批文件随后完成整体审查，统一纳入同步清单。此记录不能用作研究角色 loaded-host 验收证明。

## 审计发现

现有 runner 在检查确定性 lane 忙碌前先 harvest 推理 future，因此虽然阻止新派发，推理 submit 与后台 service.execute 的状态写入仍可能重叠。不能仅删除 busy 分支。

优先拆分 `research.card.facts` 的纯字节计算核：主循环准备冻结输入，后台只计算，主循环复验 attempt 并提交。未能证明安全的收集、共享 staging、taskbook、gate、assemble 等操作继续独占执行；计划展开、claim、submit、retry 和 artifact 接受必须串行。

逐票复核需要新的显式展开 scope，不能仅修改 expansion ID 就放宽旧计划的幂等约束。旧冻结计划保留原语义；新计划最后仍汇合全部必需结果后才可发布。

## 实现内容

| 部分 | 实现与约束 |
|---|---|
| 逐票图 | `scan.reviews.per-stock-v1` / `scan.review3.per-stock-v1`；每票主卡接受后即可开始本票复核 |
| 幂等展开 | scope 绑定唯一已接受 plan/decision 产物；跨票独立，同 scope 锚定哈希变化拒绝 |
| 最终汇合 | `scan.review.join` 等全部必需决定，complete 再等全部 finalize；恢复全局 review.plan/decision 供现有下游消费 |
| 旧计划 | 保留冻结全局模板语义；无复核、哨兵、重试与两阶段卡路径纳入回归 |
| 状态写入 | unsafe 确定性操作同步执行；只有冻结字节的 facts 纯 CLI 允许等待时 owner 派发/接受推理 |
| 证据 | 保留 command、capture、operation_request；facts_request 的冻结引用、哈希、原输入 artifact 身份支持离线重算 |
| 取消 | 捕获转到 owner 后对 forwarded signals 显式中断；回调不递归运行确定性任务、finish 或 recovery |
| 计量 | runner segment 单调时钟与墙钟、开始/接受 milestone、排队下界、槽位秒、det busy；历史 UNKNOWN |

实现入口、指标字段与路径见 [调度说明](../session-agent/scheduling.md)。复用原并发和预算配置；评级三门、隔夜主尺与最终完整性门保持原契约。

## 同输入模拟对比

实际运行 runner，使用受控 executor 与模拟时钟；业务版本 `c5-test-rubric-v1`。分类 **SIMULATED**，每次确定性操作附加 0.1 秒模拟成本。

| 读数 | 原全局图 | 逐票图 |
|---|---:|---:|
| 模拟总耗时（秒） | 47.0 | 44.9 |
| 首次复核启动（秒） | 44.2 | 5.5 |
| 模型调用 | 9 | 9 |
| 确定性调用 | 29 | 33 |

两图冻结输入摘要一致，模型调用/重试序列与所比较业务产物哈希一致。逐票 plan/decide 增加四次确定性调用，未计作模型节约。快票第二、第三份意见在慢票主卡提交前已启动；第三票重试不重跑已接受的快票研究。

完整 JSON：保全目录下 `c5-simulated-comparison.json`；输入摘要 `fb3f411b4f8a313a951b9d48289f515a7f6692d07579b26e4293612b515c3616`。不以此合成耗时推断真实扫描吞吐或投资效果。

## 验证与审查

规格审查与独立质量审查均 PASS，未留 must-fix；修复后受影响集成 **1,304 passed / 355.77 秒**。实现者最终定向 50 passed / 8 deselected；B3 与 stage timing 27 passed；owner/capture 收尾 10 passed。以上组可能重叠，不相加为总覆盖数。文档入口定向 10 passed。

首次受影响集成在 3 failed / 434 passed 后停止：一个旧模板列表断言未包含新 join，一个观测新增测试遇到运行进程已加载旧代码，另一个是 metrics 访问尚未同步 store 的任务导致原恢复路径抛 KeyError。审查另外发现正常纯核结束误记取消、回调已接受结果后外层旧 READY 列表重复启动两处竞态。两处竞态均先复现失败，再作最小修正；最终修复定向 13 passed / 24 deselected，覆盖正常退出、真实取消、回调成功接受、旧 READY 列表、缺失 store entry 与排队原因。最终受影响集成与独立审查结果见下文及保全目录 `verification-summary.json`。

静态检查相对本批保存基线：本批修改的 Python 文件原有 19 条诊断，现有 18 条，新增 0 条；仍存量的诊断不记作本批通过。详见保全目录 `lint-comparison.json`。

## 交付与审计

本批软件范围 C5 已完成；32 个文件纳入相对保存基线的同步清单，保留前五批与用户原有修改。发布前的两次审查记录为 `spec-review.json` 与 `quality-review.json`；最终受影响测试日志为 `integration-final.log`，首次失败日志 `integration.log` 保留。

集成覆盖 `tests/session_agent`、`tests/contracts`、`tests/forensics`、capture 与 owner callback、stage timing、review coverage、hook/agent 定义和文档预算。原工作区同步、冒烟与逐文件 SHA256 对账的实际结果统一记录在保全目录 `verification-summary.json` 与 `implementation-manifest.json`。未创建提交，不以当前 Git HEAD 代表本批完整源码。

## 限制

`session_v1` 继续显式 PILOT；C4 研究边界的真实宿主证明与 C6 双宿主验收尚未完成。旧 Workflow 的能力门不因本批调度开发自动解除。模拟时钟和合成任务只能证明调度行为，不能据此声称真实扫描或投资效果改善。
