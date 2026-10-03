# 研究任务文件访问边界

状态：C4/C6 边界软件与证明验证链已实现，真实双宿主生效仍待验收。历史实现见 [第五批记录](../research/2026-09-30-agent-skills-batch5-readout.md)，当前证明操作见下文。

## 授权来源

文件访问依据确定性编排器冻结的 DispatchRequest 及任务清单。研究 agent 不能通过正文里的路径、环境变量或自选 manifest 扩大权限。

| 对象 | 权限 |
|---|---|
| 已冻结角色指令和阶段片段 | 只读 |
| 本阶段显式登记的输入 | 只读，并核对冻结内容 |
| 本 attempt 声明的输出 | 写入及必要的回读 |
| 其他票、其他运行、另一引擎、源码和未授权 deep | 拒绝 |
| 清单、绑定登记与编排状态 | 研究角色不得改写 |

条件深核必须来自当前阶段已登记的授权输入。显式 DispatchRequest 登记的 deep 属于根编排授权；两阶段卡的普通初评未登记 deep，持仓初评和最终核验按各自任务图登记。不能通过额外路径扩权，也不要求普通单阶段任务新增人工握手。文档中提到某个路径，不会自动授予读取权限。

## 宿主绑定

hook 根据宿主提供的 session/agent 身份查找编排器登记的任务身份，再验证 run、engine、task、SESSION attempt 和当前任务状态。已经绑定的研究身份不能通过缺失或更改角色字段退出边界。

如宿主只能在创建子 agent 后返回其身份，绑定完成前的调用会被拒绝。根会话必须完成绑定后再让研究继续；不能把第一次拒绝改成目录级默认放行。

### Mailbox / 手工 claim

根会话在创建独立研究子 agent 并取得真实身份后，执行以下命令。`attempt` 是 SESSION attempt，不是另一套业务任务簿计数。

```bash
uv run --no-sync python -m autoresearch.session_agent mailbox bind-access \
  --run-id <RUN_ID> --task-id <TASK_ID> --attempt <SESSION_ATTEMPT> \
  --context-ref <HOST_AGENT_ID> --session-ref <HOST_SESSION_ID>
```

命令按 canonical dispatch 与当前任务 owner 绑定，返回 `ACCESS_BOUND`、登记路径和可直接派发的 `read_commands` / `write_commands`。写模板中的 `REPLACE_WITH_MARKDOWN` 必须替换为按下述规则转义的实际正文。根会话将这些命令交给对应子 agent；子 agent 不自行拼出其他路径。绑定前如已发起工具调用，应等待根会话完成绑定后重试。

Claude headless 执行器在启动进程前，按预分配 UUID 做 session 级绑定；该专用会话即便没有角色字段也仍受保护。旧 frozen request 缺少 C4 manifest 时明确拒绝，不现场升级。两条传输都仍需真实宿主验证身份字段是否按预期进入 hook。

## 文件工具

结构化文件工具检查真实路径字段。通用 shell、heredoc、命令替换和动态解释器读取不承担授权逻辑。

对于通过命令工具读写的宿主，受限 broker 仅接受冻结入口和规范编码的协议请求，按 artifact ID 访问声明文件。正文作为数据处理；不接受任意路径或命令。只有该固定协议属于文件工具，其他 shell 请求继续拒绝。

broker 命令由固定绝对解释器、`-I -S`、固定绝对脚本和一个单引号 JSON 参数构成。JSON 使用规范 UTF-8 编码，换行写作 `\n`，正文中的单引号必须写作 `\u0027`；不得在外层单引号中直接插入单引号。hook 将完整命令与规范重建结果比较，而不是宽松解析 shell。正文中的 `$()`、反引号和 heredoc 字样都只是数据。请求仅包含宿主身份、引擎、操作、artifact ID，以及写入所需正文；不能另加文件路径。

成功读取的业务证据仍须由绑定的宿主 transcript 验证。缺结果、执行失败、输出截断、重复 call ID 或内容不匹配，都不能被计为完整深读。

## 配置与真实生效

以下结论分别记录：

1. 角色与受限文件工具已登记。
2. hook 配置和本地反例测试通过。
3. 真实宿主已加载配置，并留下合法读写与越界拒绝证据。
4. 操作系统隔离是否成立。

前两项不能代替后两项。离线 replay sandbox 的 `ENFORCED` 也不能代替研究工具边界证明。缺少可信生效证据时，不满足默认入口切换的对应验收门，`session_v1` 保持 PILOT。

更新 hook 后，需要在新宿主会话中完成配置重载；若 Codex 启动要求审查项目 hook，应按宿主界面完成一次性审查。只有加载后产生的实际允许/拒绝记录可用于真实验收。本次代码开发不会自动重启正在进行的会话，也不会把当前会话推定为已经加载新 hook。

## 边界证明与默认入口门

`research_boundary_gate` 现在消费可信签发者签名的实际宿主观察；缺证据仍返回
`PENDING_REAL_HOST_VALIDATION`。每个宿主固定检查 11 项，共 22 项：合法读写、越界读写、
任意 shell、身份伪装、deep 授权前后、过期 attempt、篡改绑定、缺失绑定。

证明绑定本轮代码/hook/broker/角色配置指纹、真实宿主版本与 session/context、冻结任务清单、
原生工具调用及结果、hook 的同一 call ID 观察，以及专用 canary 的前后字节。
缺结果、伪造 hook 自报、不同会话拼接、签名错误、代码漂移均不能通过。

### 操作顺序

以下命令由根会话/可信本地操作者执行。真实研究子角色无权签发证明。
先在新宿主会话重载配置、建立真实任务与绑定，再使用专门的 `boundary-canary` 文件演练。
不得用真实研究文件、凭证或另一引擎目录作越界探针。

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m autoresearch.session_agent.boundary_proof status
uv run --no-sync python -m autoresearch.session_agent.boundary_proof init-issuer
uv run --no-sync python -m autoresearch.session_agent.boundary_proof trust-issuer \
  --public-key <本引擎内暂存的公钥> --engine <codex或claude> \
  --expected-fingerprint <通过独立可信渠道核对的公钥SHA256>
uv run --no-sync python -m autoresearch.session_agent.boundary_proof challenge --spec <探针参数.json>
# 对应真实宿主执行返回的工具调用，根会话随后签发：
uv run --no-sync python -m autoresearch.session_agent.boundary_proof issue --challenge <challenge_path>
uv run --no-sync python -m autoresearch.session_agent.boundary_proof import --proof <proof_path>
uv run --no-sync python -m autoresearch.session_agent.boundary_proof status
```

探针参数 JSON 对应 `create_challenge`：`request_path`、`case`、`session_id`、`agent_id`、
`tool_name`、结构化 `tool_input`、`target`，允许操作还需 `expected_sha256`。
每次 challenge 先冻结，再进行一次实际调用；故障演练只改专用测试任务。
私钥在当前引擎的 `_acceptance/boundary/issuer.pem`，权限 0600；初始化是显式、排他的本地操作。
签名使用 `/usr/bin/openssl` 的 RSA-2048/SHA-256。导出的公钥不自动获得信任。

Claude 证明必须由 Claude 自己采集。跨宿主交换仅将 portable proof 和公钥交接到接收引擎目录，
再独立核对指纹并导入；Codex 不读取 Claude 的产物目录。证明仅携带身份元数据和相关工具片段。

签名证明“可信操作者签发了这些观察”，不证明操作系统隔离。历史矩阵通过也不代表当前新会话
已经加载 hook：`entrypoint_observed` 与当前能力状态仍需本次实际证据。
默认切换还要求既有双宿主 REAL_SESSION 工作流矩阵全部通过；边界测试和合成回归不能替代它。

### 选择当前 proof 集合

根操作者在导入后显式选择本批次；不会自动采用最新文件。选择文件只允许 `engine` 和 `proof_hashes` 两个字段，后者为 case 到已导入 proof SHA256 的映射。例如：

```json
{"engine":"codex","proof_hashes":{"allowed_read":"<64位已导入proof哈希>","allowed_write":"<64位已导入proof哈希>"}}
```

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m autoresearch.session_agent.boundary_proof select --spec <本引擎内的选择文件.json>
uv run --no-sync python -m autoresearch.session_agent.boundary_proof status
```

内容寻址索引保存在 `boundary/selections/<selection_hash>.json`，锁内原子更新 `boundary/active.json`，前序索引保持可追溯。每个集合的成员必须属于同一个根 session、host version 与当前 policy；选择不会重新授予失效 proof 资格。

有 active 时，门只以明确选定的集合计算当前状态，旧 proof 进入 `history_diagnostics`；无 active 时兼容旧目录检查。有 active 却缺某宿主时，该宿主保持缺项，不从历史补齐。部分集合可以选择，但每宿主分母始终为 11：缺项 PENDING，选中非法成员 INVALID。单宿主检查不受非必需宿主失效证据影响，双宿主检查仍核验两方。历史 proof、失败观察与不可变 selection 均应保留。

### 当前会话观察与历史验收

`capability` 的 `historical_boundary_acceptance` 继续报告选定证据集合是否满足完整边界门；`current_host_observation` 则按当前可信 root session、原生 host version 和现行 policy 精确投影。当前入口至少具备有效的 `allowed_read` 与 `allowed_write` 才显示 `entrypoint_observed=true`。2/11 的读写观察可以成立，同时完整 `acceptance_satisfied` 仍为 false。

工作流 `preflight_roles` 从冻结 host profile 的 session 引用，经官方原生日志索引定位并核对 root metadata、稳定前缀和宿主版本。不以 profile 自报 version、任意 JSONL 路径、环境变量、最近 proof 或用户名推断身份。无可核实上下文、子会话混淆、过期版本/政策或坏签名均保留未观察及诊断。

不带可信 root 上下文的独立 CLI preflight 继续默认 `entrypoint_observed=false`。投影是诊断，不授予研究访问权限，不代表 OS 隔离，也不改变默认入口的真实矩阵门。

## 取数前检查与旧入口兼容

在仓库根、明确设置当前引擎后执行：

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m autoresearch.session_agent.task_access preflight --orchestration session_v1
```

`CONFIGURED_UNVERIFIED` 只表示配置及所需文件可用，不能作为宿主已加载证明。hook 当前通过仓库根的相对命令启动，因此运行目录要求仍是仓库根；`entrypoint_observed=false` 明示尚未观察真实加载。换目录执行 hook 的能力需真实宿主另行验收。

旧 Workflow 没有本批要求的任务绑定传输；其 preflight `--orchestration legacy` 返回 `HOST_CAPABILITY_REQUIRED`、`runnable=false` 和退出码 2。三个旧 JS 入口以及 scan capsule / stock runctl 的旧 begin 在取数前拒绝；四个 skill 的手工研究路径也先运行该检查。通用 collector、frame、pack、预热函数保留确定性用途，不受全局阻断。

这是本批明确的兼容变化：旧研究入口当前不可运行，新编排仍是需要显式选择的 PILOT，默认切换门未开启。未绑定的历史 DispatchRequest 可以读取，但不能静默扩权或补成新清单；需建立支持 C4 的新 run。不能通过主会话代演研究角色绕过任务隔离。

## 开发角色

未绑定的明确开发角色 `worker` / `explorer` 与根会话、确定性命令壳使用显式职责例外。若该宿主身份已有研究任务绑定，绑定优先；将角色字段改为开发角色仍拒绝，不能借此扩大研究输入。未知子角色不会默认视作根会话。

## 根侧专用 canary 演练

`boundary_probe` 首版供 Codex 根会话编排，复用既有 binding、challenge、签发、导入与 gate。它不发现或创建宿主身份，不代替研究 agent 调工具，也不初始化 issuer 或增加信任。研究角色仍由既有 hook 排除；这不是 OS 隔离声明。

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m autoresearch.session_agent.boundary_probe create
uv run --no-sync python -m autoresearch.session_agent.boundary_probe bind <BATCH> \
  --session-id <ACTUAL_ROOT_SESSION> --agent-id <ACTUAL_CHILD_ID>
uv run --no-sync python -m autoresearch.session_agent.boundary_probe prepare <BATCH> allowed_read
# 将返回的 wrapper_program 原样交给已绑定研究 agent，以 functions.exec 调用一次。
uv run --no-sync python -m autoresearch.session_agent.boundary_probe complete <BATCH>
uv run --no-sync python -m autoresearch.session_agent.boundary_probe summary <BATCH>
```

batch ID 由 `create` 生成。专用 canary、任务及恢复日志位于本引擎 `context_codex/_acceptance/boundary/probes/<BATCH>/`。只有该 batch 新身份的精确 registry binding 是故障注入的目录外例外；已有生产身份不能复用。原 challenge/event/active 继续由既有 proof owner 管理。不要传任意 run 或路径作为故障目标。

Codex 0.159.3 的实际调用为 `exec_command/{cmd}`，hook 记录为 `Bash/{command}`。工具分别返回实际调用字段与 `proof_tool_name/proof_tool_input`，并冻结同一命令字节；必须原样执行 `wrapper_program`，其中结果使用 `text(JSON.stringify(result))`。不要将 hook 字段直接当作宿主暴露工具调用，也不要自行改写 wrapper。其他宿主版本的路由仍需实测。

对固定 case 串行重复 `prepare → 真实 agent 调用 → complete`。`deep_before` 必须先于 `deep_after`；不撤销已有授权伪造“授权前”。当前 `outside_read` / `outside_write` 直接返回 UNSUPPORTED，不创建 challenge，不需执行或 complete。不能用 arbitrary_shell 抵扣两项。每个 case 的失败仍留在该 batch；需要重试时建立新批次和专用身份。

中断后可用 `restore <BATCH>`：先保留现有观察或签发失败诊断，再按原字节/hash/权限恢复工具自身的故障；如果文件已经被第三方改动，拒绝覆盖并保留现场。DENY 意外收到 ALLOW 或 canary 改变会中止 batch。没有 hook 为 NOT_OBSERVED，有观察但原生证据不充分为 OBSERVED_UNPROVABLE，签发成功但导入失败单独记录；这些都不是通过。

`summary` 始终列出 11 项、缺口与本批实际 proof hashes；其 gate 字段描述当前 active 集合，不替本批背书。需启用本批集合时，根核对后将 `select_spec` 写成独立 JSON，再执行前文 `boundary_proof select --spec`。工具不会自动选择最新批次或删除历史。
