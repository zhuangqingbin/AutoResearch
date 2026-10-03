# Agent Skills 第五批开发记录

## 授权与基线

用户要求继续剩余开发。本批执行 [总计划](../superpowers/plans/2026-09-30-agent-skills-reliability-development-plan.md) 的 C4，前置 C3 输出隔离与 C2 局部恢复分别见第三、第四批记录。

- 工作树：`.worktrees/agent-skills-reliability`。
- 主工作区与工作树逐文件一致后，保全 1,290 个文件。
- 基线：`context_codex/development/20260930-agent-skills-batch5/baseline.tar`。
- SHA256：`4519c61eea93b306b79d8034234683b19af2a74738180f4120d52e105ef55ceb`。
- 保留用户与前四批修改；按本批基线核对后同步，不提交 Git。

## 本批目标

当前 hook 按引擎目录放行、忽略部分 shell 内容且异常放行。C4 将研究任务的实际文件访问限制到确定性层冻结的任务清单，绑定运行、任务、attempt 和宿主身份；根会话与确定性命令壳保留明确职责例外。

| 范围 | 目标 |
|---|---|
| 输入 | 只读已登记且冻结的角色指令与任务文件 |
| 输出 | 只写 C3 本次 attempt 的声明路径 |
| 身份 | 根据宿主身份查找编排器绑定，不接受研究 agent 自选清单 |
| 工具 | 检查结构化路径；拒绝无法可靠约束的 shell/interpreter |
| 故障 | 受保护角色缺失绑定、清单损坏或解析失败时拒绝 |
| 能力 | 区分角色映射、可执行文件工具、hook 配置与真实生效证据 |

清单与 hook 测试通过不等于操作系统隔离，更不等于真实宿主已加载新 hook。没有真实生效证据时不宣称 ENFORCED，`session_v1` 继续 PILOT。

## 实现范围

- `task_access.py`：从本次 DispatchRequest 冻结精确输入与指令片段；run/engine/task/SESSION attempt/role/宿主身份绑定；当前任务必须仍 RUNNING，abandoned 或旧 attempt 拒绝。
- hook：结构化路径检查；未绑定研究角色和损坏清单拒绝；根会话与确定性命令壳保留职责例外。通用 shell/interpreter 不再承担文件授权。
- broker：固定绝对 Python `-I -S` 与脚本，只接受规范单引号 UTF-8 JSON；按 artifact ID 读写，核对代码依赖身份；长篇中文正文不要求模型先手工编码 base64。
- handoff：mailbox 由根会话在 spawn 后运行 `bind-access`，headless 在进程启动前绑定专用 UUID；可用命令与实际身份一起交付。
- 深读证据：绑定 transcript 中唯一请求与后续结果、明确成功退出、完整实际正文、摘要与长度均须匹配；普通 shell、声明已读、截断输出不能通过。
- 验收：新增 `research_boundary_gate` 实际参与 `default_enabled`。本批尚无可信真实宿主证明验证器，门为 `PENDING_REAL_HOST_VALIDATION`，持续阻止默认切换。
- 操作说明：四个技能先做编排 preflight，补齐绑定、broker 和重载步骤；修正 scan-market 技能 description 中旧尖括号导致的结构校验问题。

## 兼容变化

旧 Workflow 没有 C4 所需任务清单和身份传输。三个 JS 入口、scan capsule 旧 begin、stock runctl 旧 begin 提前拒绝；手工 skill 研究流程先运行同一 preflight。通用取数、数据湖与预热接口保留确定性用途。

**旧研究入口当前不可运行，新入口仍需显式选择 PILOT。** 没有自动迁移或默认切换；旧 frozen request 不会被静默补清单。此变化是收紧边界的实际代价，不应描述成无兼容影响。

hook 配置要求仓库根启动；当前会话未做新配置重载或真实加载观察。配置检查、本地反例、模拟宿主测试都不能证明 OS 隔离或 REAL_SESSION 验收。

## 验证进度

四份技能结构检查通过，`scan.config_standard` 返回 0 条违规。首次规格审查发现两项阻断：runner 预冻发生在 claim 前导致 deep 授权过早；原生 Read 证据分支未限制重复与逆序 call。两项均已修复并经独立规格复审 PASS（27 passed / 5.01 秒）。claim 与 RUNNING orphan 恢复只激活原冻结集合，未放宽当前 owner 校验。

首轮诊断回归主动中断于 327.11 秒，结果为 27 failed / 1,486 passed / 4 subtests passed，不作为交付通过证明。失败包括 runner 深读授权、旧入口原因契约夹具和两处新增向上依赖；最终回归将使用修复后的源码重跑。本批 Python lint 首轮 48 条，其中 9 条为基线已有、39 条为新增；新增项已清零，保留基线 9 项。legacy 早拒绝契约下移 `contracts/research_access.py`，未扩增层级例外。

修复后扩展定向回归 304 passed / 121.14 秒，headless 组 45 passed / 20.12 秒，文档预算 6 passed。独立质量复核 PASS，无 must-fix；生产文件哈希与审查清单一致。非阻断观察：broker 写失败可能留下空或部分 attempt 文件，命令返回失败且 C3 发布验收仍适用，可后续改成原子替换。

第二轮集成回归为 1 failed / 1,509 passed / 222.45 秒，唯一失败是精简 scan-market 技能时遗漏 runner PILOT 指针；已补回并通过文档定向 10 项测试，技能大小 16,990 B，小于 17,000 B 预算。最终整组重跑 **1,510 passed / 221.87 秒**，无失败。生产代码在最终审查与集成回归期间冻结；入口文档另由定向及同步后检查覆盖。同步及原工作区验证的实际结果记录在审计目录 `verification-summary.json`。

## 后续范围

后续仍包括 C5 调度优化、C6 真实双宿主验收、B2 业务证据硬门和总计划中的评价工作。本批不修改交易主尺或评级门。

## 回归命令与记录

最终集成覆盖本批实际改动与既有 C2/C3 依赖：

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest -q --maxfail=1 \
  tests/session_agent tests/forensics tests/contracts \
  tests/trace/test_capsule.py tests/trace/test_transcript_adapters.py \
  tests/trace/test_transcript_snapshot.py tests/scan/test_review_coverage.py \
  tests/scan/test_forensic_workflow.py tests/analyze/test_runctl.py \
  tests/test_agent_input_boundary_hook.py tests/test_agent_defs.py \
  tests/test_codex_agent_defs.py tests/test_doc_budgets.py
```

审计目录为 `context_codex/development/20260930-agent-skills-batch5/`：

- `baseline.tar` / `baseline.json`：保全本批开始前的已有修改。
- `implementation.patch` / `implementation-manifest.json`：只包含相对本批基线的差异及同步哈希。
- `integration.log`：首次已中断诊断，不能作为完成证明。
- `final-integration.log`：第二轮含文档指针失败的结果。
- `final-integration-rerun.log`：最终整组重跑。
- `doc-final.log`：入口指针及文档预算定向回归。
- `verification-summary.json`：最终实际统计、独立审查与同步状态。

未运行真实市场研究、模型 API 或双宿主 REAL_SESSION，也未把本批测试解释成投资效果提升。决策卡、`gap_c1_o2` 与评级三门保持既有业务口径。
