# Codex 真实宿主验收记录 — 2026-10-01

本次完成一条真实 `stock-research LITE` 早停流程，canonical 报告 full 核验及 portable proof 均通过。研究访问边界仍有宿主证据缺项，`session_v1` 保持显式 PILOT；本记录不声明双宿主矩阵完成。

## 真实运行与交付

- 根宿主：Codex 0.159.3，session `01a0f629-925e-7a91-8d8b-13e3042d8e63`。
- run：`20261001T092950456043Z`，贵州茅台 `600519.SS`，分析日 `2026-09-30`。
- 独立研究身份：`01a0f6cc-a29b-77c1-a9d4-7e9b7e4e8a08`，`stock.card` attempt 1；真实绑定后通过 broker 读取冻结指令、frame 和 slim，写入卡片。
- 实际执行：begin → harvest claim/execute → card claim/宿主推理/submit → validate claim/execute → publish claim/execute → finish。
- 卡片结果：Hold / HOLD，P3 因数据不足早停，未读 deep。新事实缺少独立 material source 绑定，六维均为未核、三门均为 UNKNOWN，EV/R:R 未核；此 proof 不证明这些原始事实已取得评级证据资格。
- [canonical 报告](../../reports_codex/analyze/runs/20261001T092950456043Z/p1/report/贵州茅台_lite.md)。路径取自 finish 返回的 canonical 目录及 publication bundle 的 `stock.card.output`，未从日期目录猜测。
- [VerificationResult](../../reports_codex/_acceptance/host-01a0f629-925e-7a91-8d8b-13e3042d8e63/20261001T092950456043Z/verification.json)：`report_covered`、`publication_ok`、`orchestration_verified`、`completeness_ok`、`integrity_ok` 均为 true；`compute_status=FULL`，`missing=[]`、`diffs=[]`。
- [ReplayResult](../../reports_codex/_acceptance/host-01a0f629-925e-7a91-8d8b-13e3042d8e63/20261001T092950456043Z/replay-result.json)：3/3 确定性步骤 MATCH，FULL / ENFORCED，无缺项、无差异。模型步骤仅重注入已冻结的真实输出，标为 EVIDENCE_ONLY，没有把重放冒充新推理。
- [REAL_SESSION proof](../../reports_codex/_acceptance/proofs/codex/stock-research/20261001T092950456043Z/lite-early-stop.json)，hash `6a7715fe4643b33b0bf40c41a8f93805edabce6412337270f0bf150d74fbfbe8`。
- [验收状态](../../reports_codex/_acceptance/host-01a0f629-925e-7a91-8d8b-13e3042d8e63/20261001T092950456043Z/acceptance-status.json)：本引擎已导入的记录接受 `codex:lite-early-stop`，无非法记录；Codex 固定分母为 1/14，默认仍为 PILOT。此处未读取另一引擎目录，未导入的另一宿主结果不代表其没有发生。

## 当前会话 hook 与任务边界

依照 [access-boundary.md](../session-agent/access-boundary.md) 使用专用 canary 与真实 L4 身份 `01a0f62b-3feb-76e2-85da-8daf40d0fdf3` 演练；未以真实研究文件、凭证或另一引擎目录作为越界目标。

授权读、授权写均有 native CommandExecution 与同 ID hook 观察，并已签发、导入、验证 portable proof：

- allowed_read：`fcee0bb3485feac4e7ff219447a80b182cc86b4de3c5ce93c3d32b8a4002e464`。
- allowed_write：`6ca7b3df8d5612f9d5a9edeb3ab48f6eba045e6c276ef6ba82888d8cfafac8e4`。

通用 shell 实际遭到 `AGENT_INPUT_BOUNDARY` 拒绝，目标 canary 未变化。但拒绝的嵌套 exec 在宿主 rollout 中缺少可关联的 native CommandExecution ID，签发器返回 `unique native CommandExecution ID required`。未用错误文本、时间接近或合成 ID 替代关联证据。详细观察与拒绝结果见 [边界观察](../../reports_codex/_acceptance/host-01a0f629-925e-7a91-8d8b-13e3042d8e63/boundary-observations.json) 和 [拒绝 proof 结果](../../reports_codex/_acceptance/host-01a0f629-925e-7a91-8d8b-13e3042d8e63/denial-proof-result.json)。

[边界状态](../../reports_codex/_acceptance/host-01a0f629-925e-7a91-8d8b-13e3042d8e63/boundary-status.json) 为 `PENDING_REAL_HOST_VALIDATION`：Codex 2/11；其余拒绝、身份、deep 和 attempt 场景未取得合格 proof。配置 preflight 的 `entrypoint_observed=false` 未被人工改写。真实观察证明本会话发生了 hook 检查，不证明完整策略覆盖或操作系统隔离；replay 的 ENFORCED 也不替代此门。

## 修复与保留的失败证据

1. 修复 cwd 相对 artifact 路径被重复添加 workspace 前缀的问题，保留 containment/symlink 约束。
2. 修复 standalone 业务配置被错误用作研究角色编排配置的问题；角色配置单独冻结进 run contract。
3. 修复 Codex 子会话发现使用父 session_id 而非子 id，以及嵌套原生 CommandExecution 的归一化接线。拒绝事件缺 native ID 时仍拒绝签发。
4. 真实卡因来源资格不足与评级不一致被拒绝后，由研究 agent 独立修订，未降低领域门槛。
5. run `20261001T091735797809Z` 的 p1 full 核验发现 `STORED_COMPLETENESS_DIFFERS`。宿主日志追加后，归档覆盖同名 normalized 文件，仅 snapshot_id 变化便破坏先前冻结哈希。现以 snapshot_id 区分归档文件，保留旧引用。原 p1 不改写；修复后新建本次真实 run 完整重做。

主宿主与研究任务绑定两条新增回归均先失败后通过，相关回归 277 passed。另一次 task_access 与 transcript adapter 回归 122 passed。这些软件结果不抵扣真实验收场景。

失败 run、首次发布、原始回执与核验差异均保留在本引擎 acceptance/run 目录。`.worktrees/research-quality-evidence-efficiency` 未修改，收尾只读 `git status --porcelain` 仍为空。
