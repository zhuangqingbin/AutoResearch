# GATE1 Workflow Consolidation — Implementation Plan

> 日期：2026-09-13；关联：[设计](../specs/2026-09-13-funnel-shape-gates-dual-engine-design.md)。

**Goal:** 保留所有 GATE1/GATE2 验证语义，把 GATE1 成功后的 run-mode 判断并入一次确定性调用，减少一个通用代理 shell。

**Architecture:** Python 新增可测试的 `gate1_decide` 组合函数，先运行 gate1，再决定 run mode；只有成功才返回模式。JS workflow 继续单独做 l2-check/retry，随后只派发一次 stageGate，并从其 JSON 读取 run_mode。

**Tech Stack:** Python、现有 gates/run_mode/stage_result、Claude workflow JS、pytest/node syntax check。

## Task 1：组合函数

- [x] 在 `tests/scan/test_gates.py` 写成功、gate 失败不调用 mode、force-full 和预算传播的失败测试。
- [x] 实现 `gate1_decide` 与 CLI JSON 契约，不改变原 gate1/gate2 命令。
- [x] stage result 保留原 status/metrics，并在 metrics 中保存 run_mode 决策依据。

## Task 2：workflow 替换

- [x] 在 workflow 合同测试中先断言 GATE1 与 run-mode 只剩一次 stageGate 调用，l2-check/retry 仍存在。
- [x] 修改 `.claude/workflows/scan-market.js`，删除独立 `gpJson` shell，消费组合结果。
- [x] 验证 sentinel/full/forced-full 分支与现有行为一致。

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/scan/test_gates.py tests/scan/test_run_mode.py tests/test_workflow_syntax.py -q
node --check .claude/workflows/scan-market.js
```

**验收：** gate 失败仍硬停；合法 deterministic retry 不受影响；GATE2 未删除；L4 preflight/prepare 时序未改。


## 执行与复核记录(2026-09-13)

- **Task 1(Codex)**:`gate1_decide` + `--decide-run-mode`/`--force-full` CLI + stage metrics 带 `run_mode`。原 `gate1`/`gate2` 命令未改。
- **Task 2(本次补完)**:Codex 留下两条红的合同测试(workflow 本体没改)。已改 `.claude/workflows/scan-market.js`:GATE1 一次调用带 `--decide-run-mode`(+ `forceFull` 时 `--force-full`),删掉独立 run-mode 壳;`gpJson` 与 `RUN_MODE` schema 随唯一调用点退役,`tests/test_workflow_js_syntax.py` 的 2026-08-03 反证锚点从 `const gpJson = ` 改为同文件的 `const PY = `(复现的仍是同一类缺口)。

**JS 侧不再兜底**:模式已是 GATE1 事务的一部分,拿不到四态之一即 `throw`——旧形状的 `.catch(() => null)` + `sentinel_level` 推断是「拿默认值替失败签字」,推错的代价是整天跑错方向(哨兵日补成 FULL = 白跑全市场;全扫日补成 SENTINEL_EMPTY = 当天啥也不跑)。

**补灯(变异探针实测)**:Codex 的实现有两处声称但无灯 —— 删掉「run_mode 落盘失败则整道门 FAILED」与「metrics 带 run_mode」,全量**都不会红**。已补 `test_gate1_decide_fails_the_whole_gate_when_run_mode_cannot_be_written`、`test_gate1_decide_cli_hands_run_mode_to_the_workflow_through_stage_metrics`、`test_gate1_cli_without_the_flag_does_not_decide_run_mode`、`test_run_mode_flags_are_refused_outside_gate1`,JS 侧补 `test_scan_refuses_to_run_on_an_unusable_run_mode`、`test_force_full_reaches_the_merged_gate1_call`。6 条变异探针逐条实测变红。

**活体验收(真产物)**:把 `context_codex/scan/2026-08-25` 复制两份(原目录只读),旧路径(gate1 + 独立 `run_mode --decide`)与新路径(一次 `gate1 --decide-run-mode`)各跑一次 → `run_mode.json` **逐字段一致**(均 `FULL`,`l2_n=201`、`l4_budget=30`),唯一差异是 `sentinel_reason` 从 `null` 变成真判据「全市场健康上涨 7.0% 材料充足 → 全扫」:旧 workflow 从不传 `--sentinel-reason`,所以哨兵档报告横幅一直印「判据:—」。

**未做**:GATE2 未动;L4 preflight/prepare 未合并(时序不同,设计 §5 明确留在本轮之外)。**Codex 会话内路径已一并接线**(原留作待裁,用户裁「全部开发」后落地):`AGENTS.md` 规则 3 与速查块改成 `gates gate1 <date> --decide-run-mode`,并写明代价 —— 不带这个 flag,codex 侧就没有 `run_mode.json`,`load()` 返回 None(=「不知道」,**不是** FULL),哨兵日会被 GATE4 按全扫口径拿「覆盖率不足」毙掉、报告也印不出哨兵横幅。散文会漂,所以加了 `test_codex_session_path_also_decides_the_run_mode` 钉住(变异探针实测变红)。
