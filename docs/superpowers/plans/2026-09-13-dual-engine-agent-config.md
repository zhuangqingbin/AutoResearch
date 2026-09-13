# Dual-engine Agent Config — Implementation Plan

> 日期：2026-09-13；关联：[设计](../specs/2026-09-13-funnel-shape-gates-dual-engine-design.md)。

**Goal:** 用同一角色意图配置分别解析 Claude 与 Codex 参数，并把 declared、runtime、actual 三层事实分开校验和审计。

**Architecture:** `user_config` 保持唯一解析入口，新 schema 用 role→tier 与 engine tier profiles；兼容读取旧 Claude 形状一版。物化 bundle 带 engine 和 mismatch，usage reconcile 消费规范化期望而不假装配置就是实际执行。

**Tech Stack:** Python/JSONC、现有 user_config、usage_reconcile、Claude JS workflow、pytest。

## Task 1：schema 与解析

- [x] 在 `tests/scan/test_user_config_roles.py` 写新 schema、旧 schema、未知 tier、Codex reasoning effort、model capability mismatch 的失败测试。
- [x] 扩展 top-level whitelist；实现 engine-aware tier merge、role override 和显式 fallback。
- [x] 保持 Claude 旧调用返回兼容；Codex 解析不依赖 Claude model 词表。

## Task 2：生产配置迁移与物化

- [x] 把 `scan_config.jsonc` 迁为 role tier + `agent_engines`，证明 Claude 十角色解析与迁移前等价。
- [x] schema 升级，物化 engine、resolved roles、runtime mismatch 和 digest。
- [x] 更新 frame/workflow 合成 fixture；不得访问另一引擎目录。

## Task 3：actual reconciliation

- [x] 在 `tests/trace/test_usage_reconcile.py` 写 Codex `reasoning_effort` 规范化、UNKNOWN actual、fallback mismatch 测试。
- [x] 更新 reconcile，分别报告 declared/runtime/actual 与 fits；缺 usage 不能算 match。
- [x] 增加 Codex project agent 定义或确定性生成器，并以解析结果校验漂移。

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/scan/test_user_config_roles.py tests/scan/test_user_config.py tests/trace/test_usage_reconcile.py tests/test_agent_defs.py -q
```

**验收：** Claude 行为等价；Codex 配置能表达当前 model/effort；runtime 不支持或 actual 不可见时报告真实状态，不静默降级。


## 执行与复核记录(2026-09-13)

Task 1–3 已实施(commit `2d49376`)。复核:

- Task 1/3 有对应测试在场并通过(`tests/scan/test_user_config_roles.py` 双 schema/未知 tier/Codex capability mismatch/declared-runtime-resolved 三层分离;`tests/trace/test_usage_reconcile.py` 的 `reasoning_effort` 归一与 `actual_status`;`tests/test_codex_agent_defs.py` 逐 toml 对解析结果验漂移)。
- **补完**:Task 2 的「证明 Claude 十角色解析与迁移前等价」原无证明。已用两条腿验:**同对象**(两边都是 `resolve_agent_config` 的 resolved dict)+ **搬迁前 golden**(在 main 上用迁移前代码 + 迁移前配置实跑)。结果**逐 role 逐字段完全相同**,已钉成 `test_claude_roles_resolve_exactly_as_before_the_tier_migration`,并附带断言判断类 role 不得出现 `model` 键。
- `run_bootstrap._load_config` 的 −53 行是把内联校验收进 `user_config.validate_user_config` 单一事实源。等价性已实测:21 个 `_KNOB_TYPES` 注册项 + 顶层/子键白名单 + performance/agents 形状,新旧**逐条同拦**,合法配置阳性对照通过。
