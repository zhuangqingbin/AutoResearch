# 全扫 effort 与目录上下文实验

`autoresearch.research.scan_efficiency_experiment` 是离线的冻结、目录探测和比较入口。它不启动 `codex exec`，不修改生产 `scan_config.jsonc`、角色文件、hooks、既有 capsule 或全局用户配置。默认模型、评级三门、风险核对和必要复核保持生产口径。

| profile | 唯一处理因素 | 基线 → 候选 | 允许角色 |
|---|---|---|---|
| `intel_high` | effort | xhigh → high | `scan.l4.intel` |
| `intel_medium` | effort | xhigh → medium | `scan.l4.intel` |
| `sector_medium` | effort | high → medium | `sector.brief` |
| `catalog_1000` | 目录上下文预算 | 已观察的较大预算 → 1000 | `scan.l4.intel` / `sector.brief` |

模型固定 `gpt-5.6-sol`。L3、最终 card、ensemble review 不接受这些降档 profile。不同 profile 用不同实验身份；同时降 effort 和目录预算不属于单因素实验。

OpenAI 的 [配置参考](https://learn.chatgpt.com/docs/config-file/config-reference) 登记了 `skills.max_context_tokens`、`skills.config` 和 `tool_output_token_limit`。本入口选择目录预算，不禁用研究必需工具，不截断域数据，也不改角色开发者指令。目录预算是否在当前本地 CLI 实际生效，仍需探测；配置参数存在不能证明 C4 hook 已加载。

## 冻结

准备 JSON request，字段必须完整：

```json
{
  "experiment_id": "intel-high-20261009-001",
  "profile": "intel_high",
  "repetitions": 2,
  "min_tasks": 20,
  "margin": 0.0,
  "max_role_invocations": 80,
  "controls": {
    "role_instructions": {"path": "同引擎快照绝对路径", "sha256": "64位摘要"},
    "access_policy": {"path": "同引擎快照绝对路径", "sha256": "64位摘要"},
    "hooks": {"path": "同引擎快照绝对路径", "sha256": "64位摘要"},
    "downstream_contract": {"path": "同引擎快照绝对路径", "sha256": "64位摘要"}
  },
  "tasks": [
    {
      "task_id": "frozen-case-01",
      "role": "scan.l4.intel",
      "model": "gpt-5.6-sol",
      "effort": "xhigh",
      "skills_max_context_tokens": 10000,
      "inputs": {
        "task": {"path": "冻结任务包绝对路径", "sha256": "64位摘要"}
      }
    }
  ]
}
```

示例只展示一个 task 的结构，实际 request 需 20–100 个独立冻结输入案例；每臂每任务 2–5 次。输入原字节摘要集合重复会被拒绝；更换 task ID、artifact 标签或复制文件路径不能增加独立案例数。所有引用都是当前引擎 `context` / `reports` 下的不可变快照，并按原字节校验。基线目录预算应使用已观察值，不把当前默认值倒填历史配置。

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m autoresearch.research.scan_efficiency_experiment freeze \
  --request <request.json> --output context_codex/research/<experiment-id>/manifest.json
```

输出排他创建，返回 `{path,sha256}`。20 个任务、两臂、每臂两次会生成 80 个角色 invocation，以及 160 次 open/work 调用。这个上限是实验角色范围，后续保持生产档位的卡片与流程继续运行须另设预算；manifest 明确保留该限制，不把它伪装成全流程成本。

jobs 中给出两回合相同的 `codex_overrides` 与 `argv_overrides`。实际执行前，必须将这些设置冻结进独立实验运行的 dispatch contract，再完成 open → 宿主绑定 → work；不能把参数追加到已经绑定的生产 DispatchRequest。当前入口不提供推理执行适配器，不能据此声称 headless 生产运行已启用降档。完整研究登记与采纳仍依照 [研究质量闭环](research-quality-workflow.md) 的 `stage_effort_v1` 协议。

## 零推理目录探测

对 `catalog_1000` manifest，准备 `{ "manifest_ref": {"path": "...", "sha256": "..."} }`：

```bash
uv run --no-sync python -m autoresearch.research.scan_efficiency_experiment probe \
  --request <probe-request.json> --output context_codex/research/<experiment-id>/catalog-probe.json
```

入口调用本地 `codex debug prompt-input`，每个唯一设置渲染一次，记录退出码、prompt 原字节摘要和序列化字符数。结果不保存或打印完整 prompt，明确 `model_calls=0`，字符数不冒充 token。它不使用 `--ignore-user-config` 或 hook trust 绕过参数。

本次开发环境的实际探测在 10000 与 1000 两个设置下均被文件系统沙箱以 `Operation not permitted` 拒绝。结果应保持 `BLOCKED` / `UNVERIFIED`；没有目录缩减的真实验收证据，也没有 token 节省结论。换到允许本地 CLI 正常渲染的宿主后可重复上述命令，仍需单独检查角色指令、绑定和 allow/deny 证据。

## 决策比较

每个 job 的 observation 包含：

```json
{
  "job_id": "frozen-case-01.candidate.1",
  "host_evidence_ref": {"path": "...", "sha256": "..."},
  "decision_card_ref": {"path": "...", "sha256": "..."},
  "input_tokens": 100,
  "cached_input_tokens": 70,
  "output_tokens": 10,
  "wall_seconds": 1.0,
  "metering_complete": true
}
```

`decision_card_ref` 指向按原生产 card 设置完成的下游卡，用于观察 intel/行业地形改变是否影响最终决策。每臂使用相同输入与下游契约。用量字段来自完整、去重后的已观察记录；input 包含 cached input，output 包含 reasoning。必须预先保持一致的计量范围，且计入失败及开线程成本；字段未知不能补零。cached input 不得大于 input。

host evidence JSON 必须绑定 `job_id`、manifest 原字节 `manifest_sha256`、实际 `observed_overrides`、`real_session=true`、`access_verdict=PASS`、`domain_verdict=PASS`，以及整数零值 `critical_errors/access_faults/publication_faults`。它还必须含有与 observation 完全相同的 `decision_card_ref`，及 `usage` 对象：该对象包含 observation 中全部五项 `input_tokens/cached_input_tokens/output_tokens/wall_seconds/metering_complete`。换卡、调低正数用量或修改时长均须重新生成绑定证据，不能沿用原 proof。

每份 host evidence 必须包含 `session_id`、`thread_id` 和原字节校验的 `transcript_ref`。不同独立 job 不能复用 session/thread 身份，不能共享 transcript 路径或复制同一 transcript 摘要，也不能共用同一 downstream card 路径。相同决策文本可由不同真实运行产生，因此不按 card 内容摘要删除一致决策。

哈希绑定只防止引用漂移和观察替换，无法自行证明宿主声明真实；仍须独立复核原始 transcript、访问证据与 grader。入口在结果中明确保留 `HOST_AND_GRADER_PROVENANCE_REVIEW_REQUIRED`。

将全部 observations 包装成 `{ "observations": [...] }`，冻结引用；readout request 为 `{ "manifest_ref": {...}, "observations_ref": {...} }`：

```bash
uv run --no-sync python -m autoresearch.research.scan_efficiency_experiment readout \
  --request <readout-request.json> --output context_codex/research/<experiment-id>/readout.json
```

入口拒绝缺任务、缺重复、重复 job、配置未实际观察、访问/领域错误、未知用量及不可解析决策。决策比较复用 `noise_floor`，检查评级、硬否决、入场，并额外检查早停；先基线自一致，再候选交叉一致，按冻结 margin 判定。任务重复不增加独立任务数。token 变化、cached 输入和累积 job 时长单列；累积时长不是扫描墙钟时长，订阅额度节省保持未知。

所有输出的 `production_adoption` 恒为 `NOT_AUTHORIZED`。20 个任务各两次足以进行本入口的噪声地板读数，不能替代 Q13/Q14 的完整真实运行样本与后续正式采纳审查。
