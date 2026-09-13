# Session Agent 协议、执行桥与双宿主 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax. 顺序执行；不自动启动额外 agent 或模型 API。

**Goal:** 在不改变研究业务的情况下，建立由官方会话调用的任务领取、工具执行、产物提交和恢复协议。

**Architecture:** session_agent 是最上层集成包，contracts 保存纯形状。非 L4 交接由新 store 管理，扫描 L4 委托现有任务簿。模型始终在宿主会话中运行。

**Tech Stack:** Python、argparse、现有 canonical_json/原子写/文件锁、exec_capture、capsule、pytest；不新增付费推理依赖。

上游：[总计划](2026-09-13-session-agent-migration.md)。规范：[设计 §5–§10](../specs/2026-09-13-session-agent-migration-design.md)。所有新增代码接口在本计划中是开发目标。

---

## A01. 建立可核对基线

**Files**

- Read：AGENTS.md、CLAUDE.md、四个项目 SKILL.md、三个 .claude/workflows 文件。
- Read：autoresearch/contracts/inference_task.py、scan/deterministic_runner.py、scan/user_config.py、research/efficiency_baseline.py。
- Create：docs/session-agent/current-surface.md。
- Test：tests/contracts/test_inference_task.py、tests/scan/test_deterministic_runner.py、tests/common/test_workspace.py、tests/research/test_efficiency_baseline.py。

- [ ] 记录 git SHA、脏文件、每种入口的生产调用者、输入输出目录、已有校验器。不要修改原 pinned.jsonc 或其他未提交文件。
- [ ] 核对双引擎 resolve_agent_bundle 与 capability_report 的真实字段；标明“已有”“仅校验”“未接入”的区别。
- [ ] 用本引擎已有计量构建基线；没有可用真实记录时写 null 和缺失原因，不读取另一个引擎产物。
- [ ] 在当前宿主用无网络临时文件演练一次读、执行、写、回执；记录能力 true/false/null 及来源。独立上下文与原生派发不能从模型配置缓存推断。
- [ ] 运行以下基线命令，记录真实结果；失败先分类为基线问题，不把它算作迁移回归。

~~~bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest -q tests/contracts/test_inference_task.py tests/scan/test_deterministic_runner.py tests/common/test_workspace.py tests/research/test_efficiency_baseline.py
~~~

**完成判据：** current-surface 有入口、owner、已知缺口和代码出处；测试结果已记录。尚不改生产入口。

## A02. 精确任务、计划和提交契约

**Files**

- Create：autoresearch/contracts/session_task.py、session_plan.py。
- Modify：tests/contracts/test_layering.py，将未来 session_agent 登记为顶层集成包。
- Test：tests/session_agent/test_contracts.py、test_plan_contract.py。

拟提供的纯函数：validate_task(dict)、validate_submission(dict)、validate_plan(dict)、validate_expansion(dict)、validate_tool_result(dict)、validate_begin_request(dict)。字段集合严格遵循设计 §7，不引入 IO 或宿主导入。

- [ ] 先用表驱动测试覆盖缺字段、多字段、bool 冒充整数、无效 enum、重复依赖、重复 artifact、非法 hash、错误 owner。
- [ ] 复用现有 validate_envelope；保持九字段信封 v1 一字不增。TaskSubmission 的外层携带 plan_hash 和回执。
- [ ] 将 schema 的版本检查实现为 type(value) is int，避免 True 等价于 1。
- [ ] 对 task_id 使用完整匹配，对数组逐项检查类型与唯一性；INFERENCE 与 DETERMINISTIC 的 role/operation 互斥。
- [ ] 验证 parent_task 精确字段、父 owner/代码/正整数 attempt；无父票必须显式 null。测试缺字段、父 attempt=True、串票与不合法 owner；运行时父状态检查留在扫描桥。
- [ ] 验证计划中静态任务和模板不重名、依赖存在、DAG 无环；展开的 expander 只能是注册名。
- [ ] 运行新测试与 contracts 分层测试，再提交本任务文件。

核心验证代码应采用以下语义；该例可作为新模块的公共基础函数：

~~~python
def require_exact_fields(value: dict, fields: frozenset[str]) -> None:
    if not isinstance(value, dict):
        raise ValueError("object required")
    missing = fields - value.keys()
    extra = value.keys() - fields
    if missing or extra:
        raise ValueError(f"fields mismatch: missing={sorted(missing)}, extra={sorted(extra)}")


def require_version(value: object) -> None:
    if type(value) is not int or value != 1:
        raise ValueError("unsupported schema version")
~~~

首组完整测试例：

~~~python
import pytest
from autoresearch.contracts.session_task import require_exact_fields, require_version


@pytest.mark.parametrize("value", [True, False, "1", 0, 2, None])
def test_schema_version_is_exact_integer(value):
    with pytest.raises(ValueError):
        require_version(value)


def test_unknown_and_missing_fields_are_rejected():
    fields = frozenset({"engine", "run_id"})
    with pytest.raises(ValueError):
        require_exact_fields({"engine": "codex", "status": "done"}, fields)
    require_exact_fields({"engine": "codex", "run_id": "r"}, fields)
~~~

运行：

~~~bash
uv run --no-sync python -m pytest -q tests/session_agent/test_contracts.py tests/session_agent/test_plan_contract.py tests/contracts/test_inference_task.py tests/contracts/test_layering.py
~~~

**完成判据：** 所有有效形状可往返规范 JSON，所有未登记字段拒绝；旧信封测试不变。

## A03. 冻结计划、动态展开和非 L4 状态

**Files**

- Create：autoresearch/session_agent/__init__.py、plan.py、store.py。
- Reuse：autoresearch/common/atomic.py、trace/atomic.py 中适用的已核验原子写工具。
- Test：tests/session_agent/test_plan.py、test_store.py、test_expansion.py、test_submission_recovery.py。

公开函数目标：plan.ready_tasks(tasks, states)、plan.freeze_plan(path, payload)、plan.apply_expansion(plan, expansion)、store.initialize(path, plan)、store.claim(path, task_id, expected_attempt, session_ref)、store.accept(path, submission, validator)、store.recover_receipt(path, task_id)。store 只接受 owner=SESSION。

- [ ] 先实现纯 DAG 选择及 hash 固定性测试。输入字典遍历次序变化不能改变 plan_hash；冻结后字段变化必须报错。
- [ ] 写 tests/session_agent/conftest.py，提供临时本引擎根、两个有依赖的 SESSION task、一份合法信封与登记 artifact。夹具用合成内容，不使用真实报告。
- [ ] 实现同目录锁文件、临时文件、fsync、原子替换；锁路径与状态路径均在安全解析后的本 run 内。
- [ ] 用两个独立进程竞争同一个 task，证明恰好一个 claim 获得该 attempt；next 不增加 attempt。
- [ ] 按设计 §7.4.1 测试首次 expected_attempt=1、同会话重复领取、其他会话抢占拒绝及经 resume 后的新尝试；不得让重复 claim 增加次数。
- [ ] 对模板展开保存冻结输入 artifact 哈希和完整 TaskSpec；同模板同输入重入无重复任务。
- [ ] 按接收意图 → owner 提交 → ACCEPTED 回执的顺序，实现两个崩溃窗口的恢复；收到意图但未提交时不能展示成功。
- [ ] 拒绝向 store 写 owner=L4_TASKBOOK；后续扫描桥仅投影已有任务状态。

ready_tasks 的完整核心算法如下；输入合法性由 A02 先行验证：

~~~python
def ready_tasks(tasks: list[dict], states: dict[str, str]) -> list[dict]:
    return [
        task for task in tasks
        if states.get(task["task_id"], "PENDING") == "PENDING"
        and all(states.get(dep) == "SUCCEEDED" for dep in task["dependencies"])
    ]
~~~

必须加入的状态案例：

| 初始状态/动作 | 预期 |
|---|---|
| A RUNNING、B 依赖 A；调用 next | B 不就绪、run WAITING |
| A SUCCEEDED、B PENDING | 仅 B 就绪 |
| 同 submission 重复接收 | 返回第一次 receipt，不增加 attempt |
| 同 task/attempt 提交不同 output hash | 身份冲突，不覆盖产物 |
| 旧 attempt 回到已重新认领任务 | 拒绝，现任务不变 |
| 模板尚未展开且静态任务全成功 | run 仍不 DONE |
| 改输入后试图覆盖既有 expansion | 拒绝，保留旧快照 |
| 接收意图已落、owner 未提交即崩溃 | 恢复后仍未成功 |
| owner 已成功、receipt 尚未落即崩溃 | 核验原产物后补同一 receipt |

~~~bash
uv run --no-sync python -m pytest -q tests/session_agent/test_plan.py tests/session_agent/test_store.py tests/session_agent/test_expansion.py tests/session_agent/test_submission_recovery.py
~~~

**完成判据：** 非 L4 的重复领取、重复接收、计划展开与两个崩溃窗口均有验证；未增加全局业务账本。

## A04. Artifact 注册和确定性工具执行

**Files**

- Create：autoresearch/session_agent/artifacts.py、operations.py、executor.py。
- Reuse：common/workspace.py、trace/exec_capture.py、trace/process_probe.py、现有安全路径/原子写实现。
- Test：tests/session_agent/test_artifacts.py、test_operations.py、test_executor.py。

公开目标：register_artifact(handle, artifact_id, path, access)、open_artifact(handle, artifact_id)、build_argv(operation, params)、execute_operation(handle, task, attempt)、probe_execution(handle, task_id, attempt)。不接收 LLM 自定义命令字符串。

- [ ] 建立注册表字段 artifact_id、engine、run_id、relative_path、sha256、access、source_ref；只登记验证后的相对路径。
- [ ] 本引擎既有 context 证据先冻结为 run 内快照；lake 读取使用现有 lineage 与内容 hash。不得把其他引擎路径登记成 source_ref 绕过隔离。输出预登记允许 sha256=null，接受后绑定实际值。
- [ ] claim 冻结每个输入 hash，submit 重新核验实际快照；测试 run 合同 hash 未变但某个输入文件被改动时仍拒绝提交。
- [ ] 拒绝绝对路径、..、跨引擎根、符号链接逃逸、被替换的祖先目录；实际打开时再次验证文件描述符身份，不能只调用 resolve 后就信任路径。
- [ ] 对大输入登记文件引用与字节/段落范围；按需读取仍记录实际 lineage，未读的 artifact 不能记为已读。
- [ ] operation 注册为静态 op_id → 参数 validator → argv builder → 领域结果 validator。最初只开放合成 noop 与 stock.harvest，后续逐任务登记。
- [ ] 所有 argv 作为列表传给现有 run_captured，禁止 shell=True，禁止从模型结果透传 env、模块名或工作目录。
- [ ] 绑定真实 invocation_id、attempt、run handle；scan 使用 exec_capture，已有 analyze 进程内 checkpoint 不重复计入必需日志。
- [ ] 用运行中的短命令夹具模拟宿主中断；probe 返回仍活着时 execute 不启动第二份进程。

argv builder 示例为拟新增函数；ticker 等业务输入必须在上游按既有 symbol/date 规则验证：

~~~python
def stock_harvest_argv(ticker: str, analysis_date: str, *, slim: bool) -> list[str]:
    argv = ["uv", "run", "--no-sync", "python", "-m",
            "autoresearch.analyze.harvest", ticker, analysis_date]
    if slim:
        argv.append("--slim")
    return argv
~~~

攻击/故障测试清单：路径替换、引擎串根、同名不同 hash、恶意 ticker 中含 shell 元字符、stdout 混入 stderr、退出非零但有文件、进程仍活着、没有退出证据、重复 execute。

~~~bash
uv run --no-sync python -m pytest -q tests/session_agent/test_artifacts.py tests/session_agent/test_operations.py tests/session_agent/test_executor.py tests/common/test_workspace.py
~~~

**完成判据：** 返回真实退出事实；产物存在不能替代成功退出与领域验证；不新增任意 shell 工具。

## A05. 角色注册与双宿主能力适配

**Files**

- Create：autoresearch/session_agent/roles.py、hosts/__init__.py、hosts/base.py、hosts/codex.py、hosts/claude.py。
- Reuse：scan/user_config.py 的 resolve_agent_bundle、load_codex_capabilities；research/efficiency_baseline.py 的 capability_report。
- Read：.claude/agents/*.md、当前四个 playbook、AGENTS.md。
- Test：tests/session_agent/test_roles.py、test_hosts.py、test_independent_context.py。

接口目标：roles.get_role(role_id)、hosts.observe_host(evidence)、hosts.render_request(task, host_profile)、hosts.validate_receipt(task, receipt)。render_request 只生成任务交接内容，不调用任何模型。

- [ ] 将现有角色正文以 instruction_refs 注册，不复制两套 prompt；按 playbook 的实际分节需求登记新逻辑角色。
- [ ] HostProfile 的 true/false/null 能力来自当前会话工具清单和实际演练。静态缓存只证明可用模型信息，不能证明本次独立子 agent 能力。
- [ ] 复用已实现的双引擎模型词汇及显式 fallback；声明值、resolved 值、实际 observed 值分别保存。
- [ ] Codex 默认按 AGENTS.md 顺序执行；Claude 按其当前原生能力派发。相同任务协议不要求相同派发工具名。
- [ ] 需要独立上下文的任务先验证可用能力；不支持时返回 BLOCKED/NEEDS_HOST_CAPABILITY，保留可由同引擎新会话领取的任务包。
- [ ] 收到独立回执时，验证实际 context/session 身份与父任务不同，并确认上下文装载范围；模型自报“独立”不算证据。
- [ ] 记录宿主不能强制控制的部分，例如主会话已经看过其他候选结论；不要将提示词白名单描述为操作系统沙箱。

必需测试：未知能力不变 true；未声明 fallback 不自动降档；Claude effort 不套用 Codex reasoning_effort 枚举；同 context 的第二个角色不算独立复核；旧引擎 receipt 被拒绝；无 receipt 的普通角色可保留业务产物但证据覆盖明确缺失。

~~~bash
uv run --no-sync python -m pytest -q tests/session_agent/test_roles.py tests/session_agent/test_hosts.py tests/session_agent/test_independent_context.py tests/scan/test_user_config.py
~~~

**完成判据：** 两边任务输入输出同构，能力差异显式可见；没有任何模拟 session API。

## A06. 应用服务和 CLI

**Files**

- Create：autoresearch/session_agent/service.py、__main__.py、validation.py、publication.py。
- Test：tests/session_agent/test_service.py、test_cli.py、test_engine_bootstrap.py。

service 提供 begin、status、next、claim、execute、submit、resume、finish；对应设计 §7.4。所有路径、操作和 owner 均由登记表解析。

- [ ] CLI 先检查显式 AUTORESEARCH_ENGINE，再装载依赖 workspace 的模块；--run-id 在导入前与环境一致性校验，不能导入后切 engine。
- [ ] begin 校验请求、host profile、kind、mode、subject、配置，在任何数据取数前调用领域 bootstrap 创建 capsule。
- [ ] 按设计 §7.4.1 实现 BeginRequest 和领域字段映射；--request-file 必需，重复标量参数只能断言一致。测试 CLI 冲突、错误 kind/mode 组合、其他引擎 host profile 和冻结请求被改动。
- [ ] next 读取已有状态与已验证的展开记录；空任务时返回 WAITING/BLOCKED/DONE 的真实原因。
- [ ] claim 后返回同一次 attempt 的信封；禁止一条无状态的“请分析”请求绕过认领。
- [ ] execute 与 submit 分别接收确定性任务和推理任务，错误类型用固定退出码映射。
- [ ] finish 检查依赖、展开模板和领域发布条件；必需任务未成功时不执行 publish。
- [ ] 实现 --help；所有 JSON 写 stdout，诊断写 stderr。输出中不暴露 .env 或凭据。

CLI 对照测试必须逐进程运行，避免 workspace 导入缓存造成伪隔离。完整命令示例在模块开发后可使用：

~~~bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m autoresearch.session_agent --help
uv run --no-sync python -m autoresearch.session_agent status --run-id 20260913T010203000000Z
~~~

示例 run_id 仅用于夹具，真实运行使用 begin 返回值。不存在的 run 必须明确报错，不创建空目录补齐。

运行：

~~~bash
uv run --no-sync python -m pytest -q tests/session_agent/test_service.py tests/session_agent/test_cli.py tests/session_agent/test_engine_bootstrap.py
~~~

**完成判据：** 合成两步工作流通过真实 CLI 领取、执行、提交、finish；缺 engine、错 run、未知 operation 和未完成 finish 都拒绝。

## A07. 与 capsule、事件及计量衔接

**Files**

- Modify：autoresearch/session_agent/service.py、executor.py、publication.py。
- Modify：autoresearch/scan/run_profile.py、autoresearch/analyze/run_profile.py，仅对 session_v1 增量登记证据。
- Modify：autoresearch/contracts/profiles.py，新增可选 per-profile role_stages 映射，默认 None 时保持现有角色阶段解析行为；旧冻结 profile 缺此字段必须兼容。
- Modify：autoresearch/trace/identity.py、completeness.py、usage_reconcile.py 中确有消费需要的接缝；沿用现有参数注入方式。
- Test：tests/session_agent/test_trace.py、test_usage.py、test_finalize.py。

- [ ] 在现有 capsule 中冻结 plan、host profile、角色来源 hash，并登记动态 expansions、requests、receipts 的证据义务。
- [ ] session_v1 profile 注入实际逻辑角色到现有阶段的映射；未注入时 role_expected 与旧版一致，角色名不冒充阶段名。
- [ ] 用现有事件链记录真实任务交接与确定性执行；未产生实际调用不得造 AGENT_DISPATCHED/COMPLETED。
- [ ] 非 L4 事务接收意图与 owner 提交的恢复事实必须可追查，事件写失败时按原 trace 故障语义处理，不报完好。
- [ ] 通过原 transcript adapter 定位实际会话和调用；没有 transcript 则完整性缺失，不能把 request.md 当作执行记录。
- [ ] usage 只消费实际观测；同一主会话多角色无法切分时保存 run 总量，角色数值为 null。对父 scan 的内嵌 lite 任务只计一次。
- [ ] finish 输出业务状态、完好性、完整性、可重放性四项独立结果；模拟缺 prompt/缺输出/缺 transcript 的差别。

完整性测试不能只断言文件存在：至少验证 manifest hash、expected denominator、未执行角色不算派发、已派发失败角色仍计入义务。

~~~bash
uv run --no-sync python -m pytest -q tests/session_agent/test_trace.py tests/session_agent/test_usage.py tests/session_agent/test_finalize.py tests/trace/test_capsule.py tests/analyze/test_runctl.py
~~~

**完成判据：** 没有重复 UsageLedger；新旧 profile 可共存；失败 run 可以验证且不误报证据完整。

## A08. 基础故障演练与开发教程

**Files**

- Create：docs/session-agent/developer-guide.md、tests/session_agent/test_failure_matrix.py。
- Modify：docs/session-agent/current-surface.md。

- [ ] 用合成任务分别演练冷启动、宿主中断、run 已冻结、旧回执到达、输出被替换、能力不足、证据缺失。
- [ ] 冻结 run 不重新激活；创建后继 run 时把 predecessor 信息放 request 附件并冻结为输入，避免修改 SessionPlan v1 字段。
- [ ] 教程逐步解释 task、operation、artifact、attempt、receipt、owner 的关系，并给出 CLI 中每一步输入输出。
- [ ] 标记真实宿主实验与合成夹具的区别；教学图不能被当作实际执行 trace。
- [ ] 运行基础包与原契约回归，核对没有新增 provider SDK 或跨引擎数据读取。

~~~bash
uv run --no-sync python -m pytest -q tests/session_agent tests/contracts tests/common/test_workspace.py
~~~

**本分计划验收：** 基础软件可在无网络夹具上运行；模型任务由人或宿主提交，不伪造模型调用。随后进入 B01，不能跳过单股垂直验证直接迁扫描。
