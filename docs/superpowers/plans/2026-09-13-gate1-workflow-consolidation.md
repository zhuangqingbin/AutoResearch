# GATE1 Workflow Consolidation — Implementation Plan

> 日期：2026-09-13；关联：[设计](../specs/2026-09-13-funnel-shape-gates-dual-engine-design.md)。

**Goal:** 保留所有 GATE1/GATE2 验证语义，把 GATE1 成功后的 run-mode 判断并入一次确定性调用，减少一个通用代理 shell。

**Architecture:** Python 新增可测试的 `gate1_decide` 组合函数，先运行 gate1，再决定 run mode；只有成功才返回模式。JS workflow 继续单独做 l2-check/retry，随后只派发一次 stageGate，并从其 JSON 读取 run_mode。

**Tech Stack:** Python、现有 gates/run_mode/stage_result、Claude workflow JS、pytest/node syntax check。

## Task 1：组合函数

- [ ] 在 `tests/scan/test_gates.py` 写成功、gate 失败不调用 mode、force-full 和预算传播的失败测试。
- [ ] 实现 `gate1_decide` 与 CLI JSON 契约，不改变原 gate1/gate2 命令。
- [ ] stage result 保留原 status/metrics，并在 metrics 中保存 run_mode 决策依据。

## Task 2：workflow 替换

- [ ] 在 workflow 合同测试中先断言 GATE1 与 run-mode 只剩一次 stageGate 调用，l2-check/retry 仍存在。
- [ ] 修改 `.claude/workflows/scan-market.js`，删除独立 `gpJson` shell，消费组合结果。
- [ ] 验证 sentinel/full/forced-full 分支与现有行为一致。

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/scan/test_gates.py tests/scan/test_run_mode.py tests/test_workflow_syntax.py -q
node --check .claude/workflows/scan-market.js
```

**验收：** gate 失败仍硬停；合法 deterministic retry 不受影响；GATE2 未删除；L4 preflight/prepare 时序未改。
