# Session Agent 迁移基线

记录日期：2026-09-14。代码基线：`198a669`，迁移分支首提交：`b3be3f4`。

## 入口与状态归属

| 入口 | 当前执行面 | 当前 run | 业务状态权威 | 迁移缺口 |
|---|---|---|---|---|
| scan-market | `.claude/workflows/scan-market.js`、`l4-stock.js` 与 Python stage CLI | scan-market capsule | `scan/stage_result.py`、`scan/l4_tasks.py`、原 gates | Python 只有 inference handoff 校验，没有可领取、提交和恢复的会话桥 |
| stock-research FULL/LITE | skill 内顺序调用 harvest、角色研究、assemble | stock-research capsule 可选 | `analyze/runctl.py` 与 `analyze/assemble.py` | 没有显式任务 DAG、宿主回执和统一 CLI |
| macro-research FULL/LITE | skill 内调用 harvest/assemble；扫描 LITE 写 market_view | 无独立 run kind | `macro/assemble.py`、`macro/state.py` | 缺 run profile、任务协议和独立入口生命周期 |
| sector-research FULL/LITE | skill 调 pack/reuse/brief | 无独立 run kind | `sector/pack.py`、`sector/reuse.py`、地形契约 | 缺独立数据前置、run profile 和发布器 |
| dossier-init | `.claude/workflows/dossier-init.js` | 无独立 run kind | builder/schema 与本引擎 dossier 正文 | 缺候选发布事务、编辑边界和统一恢复 |

## 当前可复用能力

- `contracts/inference_task.py`：九字段、严格字段集合的推理交接信封；不调用模型。
- `scan/deterministic_runner.py`：只验证 handoff 身份，不是完整 runner。
- `scan/l4_tasks.py`：扫描整票 attempt、锁、哈希、防重放和终态的唯一权威。
- `trace/capsule.py`：run 生命周期、冻结身份、事件、完整性和 finalize。
- `trace/exec_capture.py`：确定性子进程、退出事实、日志和进程身份。
- `scan/user_config.py`：Claude/Codex 配置、能力核对、显式 fallback；迁移不另建模型路由。
- `research/efficiency_baseline.py`：真实 usage/capability 读数，缺失值保持未知。
- `common/workspace.py`：进程级引擎隔离；当前只登记 scan-market 和 stock-research。

## 宿主基线

当前 Codex 会话可以读取和写入隔离 worktree、执行本地命令并保留工具结果。模型推理发生在当前官方订阅会话，Python 进程不能回调本会话模型。独立上下文、原生派发、web search/fetch 只接受本次宿主实际提供的证据，不能从模型配置缓存推断。

Claude 的真实能力与验收必须由 Claude Code 会话在 `context_claude/` 自己记录。Codex 不读取该目录，也不代填 Claude 结果。

## 基线测试

首次运行完整测试时，隔离环境缺少项目运行中实际使用的 `pyarrow`、`akshare`、`tushare` 和 `scipy`。补齐环境依赖后，原 9 个失败中的 8 个全部通过；剩余一项由 Codex harness 注入 `CODEX_*` 且当日 rollout 目录尚未出现，触发测试刻意检查的告警。移除这四个 harness 标识后该项通过。

验证结果：`6105` 个通过，`13` 个按既有现场条件跳过；其中完整首跑为 `6096 passed / 9 failed / 13 skipped`，对 9 项环境失败的定向复验为 `9 passed`。这些结果属于迁移前基线，不表示 session agent 已实现。

## 边界

- 本迁移只在 `context_codex/`、`reports_codex/` 和 run 内临时夹具写 Codex 产物。
- 唯一共享可变研究数据仍是 `lake/`；测试默认使用临时 lake。
- 不恢复学习闭环，不改变 card_source、评级、门、交易尺度或自动交易边界。
- 新的 `session_agent` 是最高集成层；领域包不得反向依赖它。
