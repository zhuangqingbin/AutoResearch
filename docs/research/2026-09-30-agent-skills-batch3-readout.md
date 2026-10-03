# Agent Skills 第三批开发记录

## 授权与基线

用户在第二批交付后要求继续开发。沿用 [完整开发计划](../superpowers/plans/2026-09-30-agent-skills-reliability-development-plan.md)；前次交付见 [第二批记录](2026-09-30-agent-skills-batch2-readout.md)。

- 本批优先 C3：首次与重试输出隔离、已接受快照、多产物原子提交、恢复和历史兼容。
- 开发工作树：`.worktrees/agent-skills-reliability`。
- 原主工作区与工作树源码哈希逐项一致后，保全 1,283 个文件。
- 基线归档：`context_codex/development/20260930-agent-skills-batch3/baseline.tar`。
- SHA256：`fa2ac2d99b0312e33966df9ddbdbe9bc3b3011bcfa4bbefd4155c8f3857f3302`。
- 不提交 Git，不清理或重置用户已有修改。代码同步仍以本批基线逐文件核对，主工作区并发改动不得覆盖。

## 范围与顺序

| 任务 | 状态 | 验收重点 |
|---|---|---|
| C3 输出隔离与接受 | 核心与消费者已实现；规格与质量复核通过 | 首次即私有输出；下游只读已提交快照；多输出一次接受 |
| C2 局部恢复 | 后续 | 先完成 C3，再扩大复核重试；必需缺口仍阻断完整发布 |
| C4 实际输入边界 | 后续 | 任务清单与宿主真实能力分别验证 |
| C5 调度 | 后续 | 逐票研究就绪与完整报告汇合分别处理 |
| B2 业务 claim 硬门 | 后续 | 先补 claim 到判断字段的用途/必要性映射 |

## C3 设计约束

复用现有任务状态 owner：多输出快照描述与任务成功在同一次 owner 文件原子提交中生效。产物注册表保存稳定声明；receipt 和固定路径工作副本由已提交状态重建。接受时真实复制并校验快照，不将可能继续改写的 agent 文件直接当作已接受结果。

新运行冻结存储布局版本。历史缺版本按既有布局解释，不原地迁移旧请求、计划或回放材料。L4 ticket attempt 与 SESSION attempt 分别保留；B3 已成功初评保持原输入和原字节。输出存储的改造不改变评级、入场门、主尺和默认研究图。

文件隔离防止迟到任务通过原输出路径污染结果，不等于宿主已有操作系统级访问隔离。后者归 C4 实际能力验收。`session_v1` 继续 PILOT。

## 验证与交付

此处只登记实际完成的检查，不以计划或合成测试代替真实宿主验收。最终集成结果及同步记录见本节末尾。

- 改动前 C3 基线：mailbox、submission recovery、scan L4 recovery、transactional publication、headless Claude 共 **93 passed，25.77s**。
- 发布消费者：7 个反例先失败后通过，连同宏观、行业、档案、scan finish 与事务发布共 **22 passed，1.38s**。metadata、正文、状态候选、权限与 pool 均通过 artifact resolver 读取，保留原 hash/CAS 语义。独立规格与质量复核通过。
- 取消确认：新增 6 个反例先失败后通过；执行器与发布消费者共 **93 passed，26.77s**。headless 仅观察到整个进程组不存在才确认取消；leader 退出不能代替此证据，权限/观察错误保持未知。mailbox 标记取消不受支持、未确认。独立复核通过。
- 原有真实子进程测试曾因子进程尚未写出 PID 就超时而波动；测试增加有界启动握手，再测原来的超时与全组退出。生产 timeout 未放宽。定向 **7 passed，2.07s**。
- 合约登记、模块分层、文档预算、配置标准与发布读取组 **82 passed，5.89s**；scan_config CLI **0 违规**，`git diff --check` 通过。
- 实现者扩大组：全部 session 与多领域离线 ReplayUnit **529 passed、1 failed，134.19s**；唯一失败为旧测试仍期待固定输出路径，已更新为私有 attempt 路径断言。该组后续最终复跑另记，不将初次结果改写为全绿。
- 独立 C3 规格复核通过：隔离测试 **17 passed，0.41s**；另验证校验期间改写私有文件不改变已复制快照、污染工作副本不改变 evidence bundle 字节、重复 dispatch 保留已冻结参数。

- 第二次扩大组 **535 passed、4 failed，128.83s**：4 个纯 prompt 单测使用不含 workspace 的最小对象；渲染层增加对象能力判断，实际运行的存储版本与身份检查保持严格。修复后相关用例纳入下列回归。
- 质量复核发现并关闭两个 P1：取消标记已经落盘而任务状态尚未更新时的迟到接受竞态；L3 修复子进程被杀后，降级误用半修复工作副本。接受操作现按 mailbox → taskbook → SESSION 锁序覆盖标记检查和 owner 提交；L3 降级直接读取原 accepted artifact。
- 同时补齐历史布局重复 submit 的 completion/promotion 恢复。新布局已接受回执即使私有文件已删除或随后出现 abandoned 标记，仍按原接受事实幂等返回。
- 修复后定向组 **61 passed，1.45s**；独立质量复核 **26 passed，1.19s**，并以完整 service.execute 模拟修复进程 `exit_code=-9`，确认降级 effective 与原 accepted 字节一致。质量复核最终通过，无未解决 must-fix。最终规格复核再跑取消、L3 降级和旧布局恢复四项反例，**4 passed，20 deselected，0.79s**。

- 扩大至全部 session、forensics 与 contracts 后，首次 **1,024 passed、13 failed，145.93s**。失败集中于两个取证测试文件：夹具在新布局将尚未接受的 WRITE 输出直接绑定为可读 artifact，被新门禁拒绝。保留本轮日志，修正夹具以走真实接受流程后复跑；不放宽生产接受规则。夹具改为真实 `service.execute` / `service.submit` 接受，并验证快照路径不同且字节一致，定向 **15 passed，0.51s**。独立复核确认原缺失证据、失败 attempt 分母、transcript/source receipt 与伪造 binding 负例均保留，未删弱原断言。
- 变更文件 Ruff I/F401 检查有 13 条历史诊断；逐文件读取本批基线归档，确认 13 条全部已存在（一个旧 import 排序、一个旧 unused import、两阶段卡测试的 11 个旧 import 排序）。本批新增实现未引入该组诊断，未将全文件检查描述为零违规。

- 最终文档预算、配置标准、主尺文档和技能证据引用组 **64 passed，7.18s**。

- 最终扩大组复跑：全部 `tests/session_agent tests/forensics tests/contracts` **1,037 passed，146.96s**，退出码 0。日志：`final-integration-rerun.log`。包含真实子进程取消测试、隔离/恢复反例、发布、跨领域 ReplayUnit、证据闭包和契约检查。

### 明确的实现边界

扫描报告 candidate 是确定性生成的目录，继续使用原 directory manifest 拒绝改写；本批没有把整个目录迁移成文件级 accepted artifact。历史布局仍按旧语义读取，隔离保证适用于新布局运行。没有新增 retention 删除动作，未接受的 generation 不进入下游可读集合。

本批审计材料存于 `context_codex/development/20260930-agent-skills-batch3/`。其中保留基线、逐轮测试日志、变更补丁及逐文件 SHA256 清单。

## 实现入口与维护规则

| 入口 | 本批职责 |
|---|---|
| `session_agent/artifacts.py` | 冻结布局、私有输出、整组捕获、accepted 读取与历史版本、工作副本重建 |
| `session_agent/store.py`、`service.py` | 单一 owner 原子接受、锁序、重复回执、completion 恢复 |
| `session_agent/dispatch.py`、`runner.py` | 冻结请求、私有输出映射、orphan 原请求恢复 |
| `session_agent/evidence.py`、`evidence_bundle.py`、`replay_adapters/` | 取证读取接受字节，逻辑路径保持稳定，离线精确回放 |
| `session_agent/workflows/{macro,sector,dossier,scan}.py` | 发布 bundle、报告、权限、状态与 pool 候选统一读取 artifact |
| `session_agent/executors/{headless_claude,mailbox}.py` | 区分取消能力、请求与已观察确认，保留未知状态 |

布局与恢复矩阵见 [输出隔离说明](../session-agent/output-isolation.md)。新消费者应使用 artifact resolver，不能根据熟悉的固定文件名直接读取 agent 输出。历史 run 不做原地迁移；owner 的布局版本不可由旁置文件升级。

复现本批扩大回归（Python 3.13.11）：

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest -q tests/session_agent tests/forensics tests/contracts
```

## 主工作区交付审计

本批变更共 34 个文件。同步使用基线 SHA256 防覆盖检查；逐文件清单与实际应用状态见 `context_codex/development/20260930-agent-skills-batch3/implementation-manifest.json`，完整补丁见同目录 `implementation.patch`。同步后的关键路径复验单独记录于 `post-sync-tests.log`；文件一致性结果见 `post-sync-hashes.json`。

未完成项仍按上表及总计划推进；本批测试证明实现行为与离线契约，不构成真实宿主验收或投资收益证据。
