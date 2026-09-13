# Session Agent 迁移现状

记录日期：2026-09-14。代码基线：`198a669`，迁移分支首提交：`b3be3f4`。

基础层 A01–A08、研究接线 B01–B06 和扫描实现 C01–C03 已落地；C04–C06 的对拍、入口标记和操作文档也已实现。真实双宿主运行验收按设计保持独立：代码与合成回归完成不等于 Codex/Claude 的真实模型矩阵已经通过。

## 入口与状态归属

| 入口 | session_v1 执行面 | run | 业务状态权威 | 验收状态 |
|---|---|---|---|---|
| scan-market | 固定前奏 + sector/L3/repair/L4/review 动态展开 | scan-market capsule | 原 gates、stage_result、l4_tasks、publisher | 自动化通过；双宿主真实矩阵 INCOMPLETE |
| stock-research FULL/LITE | harvest、角色 DAG、validate/assemble/publish | stock-research capsule | analyze/runctl、assemble、rating validator | 自动化通过；双宿主真实矩阵 INCOMPLETE |
| macro-research FULL/LITE | 独立 run、harvest/frame、sections、state/publish | macro-research capsule | macro assemble/state | 自动化通过；双宿主真实矩阵 INCOMPLETE |
| sector-research FULL/LITE | 独立 run、prepare、terrain/full、publish | sector-research capsule | sector pack/reuse/brief | 自动化通过；双宿主真实矩阵 INCOMPLETE |
| dossier-init | prefetch、skeleton、受限分节推理、lint/publish | dossier-init capsule | dossier builder/schema/pool | 自动化通过；双宿主真实矩阵 INCOMPLETE |

## 迁移后基础能力

- `contracts/inference_task.py`：九字段、严格字段集合的推理交接信封；不调用模型。
- `scan/deterministic_runner.py`：只验证 handoff 身份，不是完整 runner。
- `scan/l4_tasks.py`：扫描整票 attempt、锁、哈希、防重放和终态的唯一权威。
- `trace/capsule.py`：run 生命周期、冻结身份、事件、完整性和 finalize。
- `trace/exec_capture.py`：确定性子进程、退出事实、日志和进程身份。
- `scan/user_config.py`：Claude/Codex 配置、能力核对、显式 fallback；迁移不另建模型路由。
- `research/efficiency_baseline.py`：真实 usage/capability 读数，缺失值保持未知。
- `common/workspace.py`：进程级引擎隔离，并登记五类 session run kind。
- `session_agent/service.py`：`begin/status/next/claim/execute/submit/fail/retry-l4/resume/finish` 应用边界。
- `session_agent/store.py`：SESSION owner 的 attempt、锁、接收意图、幂等回执与恢复。
- `session_agent/artifacts.py`：run 内路径约束、symlink/inode/hash 校验。
- `session_agent/hosts/*`：基于本次证据的宿主能力与独立上下文回执校验。
- `session_agent/roles.py`：复用现有 skill/agent 正文的逻辑角色登记，不复制 prompt。

## 宿主基线

当前 Codex 会话可以读取和写入隔离 worktree、执行本地命令并保留工具结果。模型推理发生在当前官方订阅会话，Python 进程不能回调本会话模型。独立上下文、原生派发、web search/fetch 只接受本次宿主实际提供的证据，不能从模型配置缓存推断。

Claude 的真实能力与验收必须由 Claude Code 会话在 `context_claude/` 自己记录。Codex 不读取该目录，也不代填 Claude 结果。

## 基线测试

首次运行完整测试时，隔离环境缺少项目运行中实际使用的 `pyarrow`、`akshare`、`tushare` 和 `scipy`。补齐环境依赖后，原 9 个失败中的 8 个全部通过；剩余一项由 Codex harness 注入 `CODEX_*` 且当日 rollout 目录尚未出现，触发测试刻意检查的告警。移除这四个 harness 标识后该项通过。

验证结果：`6105` 个通过，`13` 个按既有现场条件跳过；其中完整首跑为 `6096 passed / 9 failed / 13 skipped`，对 9 项环境失败的定向复验为 `9 passed`。这些数字是迁移前对照；每个迁移阶段另运行新增测试和受影响的旧回归。

## 边界

- 本迁移只在 `context_codex/`、`reports_codex/` 和 run 内临时夹具写 Codex 产物。
- 唯一共享可变研究数据仍是 `lake/`；测试默认使用临时 lake。
- 不恢复学习闭环，不改变 card_source、评级、门、交易尺度或自动交易边界。
- 新的 `session_agent` 是最高集成层；领域包不得反向依赖它。
