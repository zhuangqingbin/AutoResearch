# Agent Skills 第四批开发记录

## 授权与基线

用户在第三批交付后要求继续开发。本批执行 [完整开发计划](../superpowers/plans/2026-09-30-agent-skills-reliability-development-plan.md) 的 C2；前置输出隔离已在 [第三批](2026-09-30-agent-skills-batch3-readout.md) 实施。

- 目标：单票失败局部恢复，必需研究缺失继续阻止完整报告发布。
- 工作树：`.worktrees/agent-skills-reliability`。
- 原主工作区与工作树逐文件字节一致后，保全 1,287 个文件。
- 基线：`context_codex/development/20260930-agent-skills-batch4/baseline.tar`。
- SHA256：`8fa450fcde41a5a3b50b97401d00a10be7b2d100b5435a208ccf050bbfc4e99c`。
- 保留用户已有修改；按本批基线核对后同步，不提交 Git。

## 实施范围

| 内容 | 预期行为 |
|---|---|
| 局部故障 | 只阻断该票依赖链，其他就绪工作继续 |
| 复核暂时失败 | 只重试对应复核，复用成功主卡、其他票和成功复核 |
| 业务结果 | 有效偏空、早停、UNKNOWN 按现有业务语义处理 |
| 任务失败 | 非法结构、伪造来源或边界违规保留失败事实 |
| 完整性 | 任务终态、成功主卡、必需深核、复核和报告分别计量 |
| 全局输入失效 | 阻断依赖该输入的任务，禁止正式发布 |
| 0 BUY | 研究完整时允许报告，无须放宽入场门 |

复用既有任务状态、retry 分类、冻结人口与任务簿。C3 的私有输出、整组原子接受和历史布局兼容继续生效。诊断性部分结果不等于正式完整报告；`session_v1` 保持 PILOT。

## 进度与验证

本批实现、独立规格与质量复核、扩大回归均已完成。实际结果在执行后登记；不将测试夹具视为真实宿主验收。

### 实施前审计

- `_state()` 已优先返回其他 READY 任务；保留这一调度原则。
- 现有复核失败会连带将父 ticket 置为 FAILED，runner 不会仅重试复核。
- `scan_progress()` 原有任务簿状态计数不足以表示成功主卡、必需深核和复核覆盖。
- 冻结的 finalists、L4 plan、review plan 与 review decision 可提供业务分母；尚未接受的卡可能仍有未知深核触发条件。
- `l4_tasks.reconcile()` 只验证主卡相关文件，不能用它证明待复核票已经完整。
- 现有全部主卡汇合、全部第二轮复核汇合保留。C2 保证其他已就绪工作继续；更细粒度就绪调度属于 C5。

### 兼容性取舍

新复核失败采用复核自己的 SESSION attempt；父 ticket 与成功主卡不变。历史运行若已经留下 FAILED 父 ticket，不能仅靠更改状态恢复成功事实：应输出明确恢复阻断原因，保留失败记录和原接受字节，不自动重跑成功主卡。

### 已执行检查

- 实施前 C2 定向基线：**31 passed，1.58s**。
- 复核重试四项反例先失败，修改后 **4 passed，17 deselected，10.49s**；后续覆盖率断言与最终回归另记。
- 全局输入与局部依赖三项反例先失败，相关回归 **13 passed**。
- 新增多根阻断与行业 subject 分类反例，确认原先会遗漏全局根或将行业误标为单票。
- 开发文档预算 **6 passed，0.56s**。

### 实现与定向结果

生产变更集中于 `session_agent/runner.py`、`service.py`、`progress.py`。复核重试复用原 SESSION 状态和预算，状态诊断只读派生；共享损坏输入在单次状态查询中去重校验。冻结人口缺失或名单不可读时，明确保留 `POPULATION_NOT_FROZEN` / `FINALISTS_UNAVAILABLE`，不回退可变任务簿来确认研究完整。

- 覆盖率组：**9 passed，21 deselected，11.47s**。
- 单股、宏观等跨工作流小组：**44 passed，1.63s**。
- 首次扫描组合回归：**32 passed、1 failed，88.67s**。故意污染 accepted 快照的测试先遇到只读权限，尚未进入诊断断言；测试显式修改临时夹具权限后再破坏内容，生产只读保护保留。
- 修复后的三票恢复、冻结名单损坏、历史父票失败、必需深核缺失和非法结构定向组：**4 passed**；持仓业务阻断组：**2 passed，5.27s**。
- 最后轻量诊断组：**21 passed**。

三票用例保留 A 的成功结果，B 走既有主卡恢复，C 只重试对应复核；同时断言缺口未解决前覆盖不完整。review3 重试保留 review2，迟到 a1 输出不改变 a2 接受结果。新字段与运维规则见 [局部恢复说明](../session-agent/local-recovery.md)。

### 扩大回归与独立复核

- 独立规格聚焦组：**14 passed，22 deselected，24.25s**。发现终态覆盖分母在任务簿或 SESSION 记录缺失时会缩小，要求改为全部冻结 task ID 集合，缺失状态不计终态或成功。
- 首次扩大组（全部 session、forensics、contracts，加复核覆盖和文档预算）：**1,075 passed、1 failed，197.26s**。失败为历史 L3 降级测试的手工夹具没有注册 GATE2 输入，新就绪输入校验将其阻断；随后补齐真实 judged、validation、context、GATE1 和 run_mode 输入，保留降级、门释放和原 judged 字节一致断言；生产输入门未放宽。

- 分母缺失反例先红后绿；合并 L3 夹具与局部故障定向 **19 passed，1.12s**。独立规格最终复验 **13 passed**，规格审查 **PASS**。
- 变更 Python 文件 Ruff I/F401 无新增诊断；唯一 unused `shutil` 已在本批基线存在。scan_config 标准 **0 违规**，`git diff --check` 通过。

- 独立质量审查 **PASS**，无未解决 must-fix；聚焦复验 **13 passed，26 deselected，9.83s**，覆盖复核重试、历史父票失败、共享输入损坏、多根依赖和缺失 owner。
- 最终扩大组复跑 **1,079 passed，178.61s**，退出码 0；日志 `final-integration-rerun.log`。保留首次失败日志，不将修复前结果改写为全绿。

## 后续范围

C4 实际访问控制、C5 调度优化、C6 REAL_SESSION 和 B2 业务 claim 硬门等继续按总计划推进。本批不改变投资主尺、评级门或默认研究图。

## 复现与交付清单

扩大回归使用 Python 3.13.11：

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest -q tests/session_agent tests/forensics tests/contracts tests/scan/test_review_coverage.py tests/test_doc_budgets.py
```

本批变更共 **11 个文件**（3 个生产文件、4 个测试文件、4 个文档文件）。本批审计目录为 `context_codex/development/20260930-agent-skills-batch4/`，保存基线、各轮测试日志、变更补丁与逐文件 SHA256。实际同步状态以 `implementation-manifest.json` 为准；同步后文件核对和关键路径复验分别记录于 `post-sync-hashes.json` 与 `post-sync-tests.log`。
