# Dual-engine Agent Config — Implementation Plan

> 日期：2026-09-13；关联：[设计](../specs/2026-09-13-funnel-shape-gates-dual-engine-design.md)。

**Goal:** 用同一角色意图配置分别解析 Claude 与 Codex 参数，并把 declared、runtime、actual 三层事实分开校验和审计。

**Architecture:** `user_config` 保持唯一解析入口，新 schema 用 role→tier 与 engine tier profiles；兼容读取旧 Claude 形状一版。物化 bundle 带 engine 和 mismatch，usage reconcile 消费规范化期望而不假装配置就是实际执行。

**Tech Stack:** Python/JSONC、现有 user_config、usage_reconcile、Claude JS workflow、pytest。

## Task 1：schema 与解析

- [ ] 在 `tests/scan/test_user_config_roles.py` 写新 schema、旧 schema、未知 tier、Codex reasoning effort、model capability mismatch 的失败测试。
- [ ] 扩展 top-level whitelist；实现 engine-aware tier merge、role override 和显式 fallback。
- [ ] 保持 Claude 旧调用返回兼容；Codex 解析不依赖 Claude model 词表。

## Task 2：生产配置迁移与物化

- [ ] 把 `scan_config.jsonc` 迁为 role tier + `agent_engines`，证明 Claude 十角色解析与迁移前等价。
- [ ] schema 升级，物化 engine、resolved roles、runtime mismatch 和 digest。
- [ ] 更新 frame/workflow 合成 fixture；不得访问另一引擎目录。

## Task 3：actual reconciliation

- [ ] 在 `tests/trace/test_usage_reconcile.py` 写 Codex `reasoning_effort` 规范化、UNKNOWN actual、fallback mismatch 测试。
- [ ] 更新 reconcile，分别报告 declared/runtime/actual 与 fits；缺 usage 不能算 match。
- [ ] 增加 Codex project agent 定义或确定性生成器，并以解析结果校验漂移。

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/scan/test_user_config_roles.py tests/scan/test_user_config.py tests/trace/test_usage_reconcile.py tests/test_agent_defs.py -q
```

**验收：** Claude 行为等价；Codex 配置能表达当前 model/effort；runtime 不支持或 actual 不可见时报告真实状态，不静默降级。
