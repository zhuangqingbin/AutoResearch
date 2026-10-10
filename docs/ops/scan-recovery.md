# 全扫额度暂停与单任务恢复

2026-10-09 起，headless 的 `USAGE_LIMIT` 单独表示订阅容量耗尽。runner 停止新派发，收取在飞结果，返回 `recoverable=true`；scan_run 摘要为 `RECOVERABLE`，run 保持 ACTIVE。短时 HTTP 429 仍走原 RATE_LIMIT 自动重试。自动重试次数和研究质量门不变。

先读取 `$RPT/_ops/scan_run_<分析日>.json` 的 `run_id`、`recovery_tasks`，确认额度恢复或组装代码已修复。对每个失败任务的精确 attempt 追加一次授权：

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m autoresearch.session_agent recover-task \
  --run-id <RUN_ID> --task-id <TASK_ID> --failed-attempt <ATTEMPT> \
  --reason '额度窗口已恢复 / 组装问题已修复'
scripts/scan_run.sh --engine codex --resume-run-id <RUN_ID>
```

`recover-task` 只接受 USAGE_LIMIT 推理失败或幂等确定性操作失败，验证原 claim 的输入哈希后授权下一 attempt。授权及原失败保留在证据中。已接受任务不重跑；L4 容量暂停保持原票据 attempt，只重发未完成子任务。DOMAIN_VALIDATION、契约/身份/证据拒绝不能借此绕过原修订策略。再次失败需要新的精确授权，不能无限自动重试。

恢复入口跳过 readiness/begin，拒绝跨引擎、非 headless、仍有活 runner 和永久 FAILED run。原发布事务已经封存 SUCCEEDED 而尚未完成交付时，只继续现有事务与验证。所有成功恢复仍执行 canonical 报告的 `verify-report --level full`。历史 2026-10-08 的两个 FAILED run 不被本次代码重新激活。

墙钟超时或中断如果已存在额度/组装可恢复事实，也保留 run；其他被停止的 RUNNING attempt 仍按现有 orphan/STALE_TASK 流程处理，不能认领旧 attempt 或假装其已成功。

卡片的 DOMAIN_VALIDATION 修订可复用原已接受情报，条件为同 run、同分析日、UTC+8 当日，原输入及原始/守卫产物哈希一致，访问证据和事件链完整。新 attempt 重新运行情报守卫、来源授权与卡片校验；条件不齐则重新研究。复核员的失败仅重发该复核任务。

计量按实际 transcript 来源与覆盖区间合并；CLI 成功但领域拒绝也计为失败，容量暂停单列。原始输入包含缓存，不能把重复计量修正称作订阅消耗减少。Codex 订阅美元成本仍未知。模型降档与技能目录压缩见 [实验入口](../session-agent/scan-efficiency-experiments.md)，真实宿主验证与等价判据通过之前不改变生产配置。
