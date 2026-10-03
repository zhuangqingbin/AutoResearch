// LEGACY_ORCHESTRATION_FALLBACK: keep for host rollback until real session_v1 acceptance.
export const meta = {
  name: 'l4-stock',
  description: '单只 finalist 的 L4 全链:活体情报盲搜 → 决策卡 → (≥OW)双复核折回;每股一个 workflow、N 股并行(fb_20260714_003)',
  phases: [
    { title: 'Intel', detail: 'l4-intel 六面盲搜(config 可关;失败不阻断,卡回退卡内网查)' },
    { title: 'Card', detail: 'l4-card 渐进深度 DD + 早停,写 details/<code>.md' },
    { title: 'Verify', detail: '≥OW → 2 独立复核 run 取中位,只向下折回 → _ensemble_<code>.json' },
  ],
}

// args: {date, run_id, code, attempt, name, sector, cfg} —— attempt 由 scan handoff/重试调度权威下发;
// cfg 透传 scan_config 的 agents/l4_intel 块(缺省 = 现硬编码值,parity)。
// 为什么每股一个 workflow(而非 scan-market.js 内批量派发):①每个 workflow 有独立并发帽,N 股真并行;
// ②intel→card 在股内链式衔接,股间零 barrier(旧批量版全体 intel 完才派卡);③单股失败只废单股,
// 主会话对该股单独重跑即可 —— 2026-07-14 GATE3 差 16 字节毙掉 60min/1.6M token 全流水线的教训。
// BEGIN GENERATED RESEARCH TEMPLATES
// Owner: autoresearch/common/research_prompts.py; regenerate with python -m scripts.sync_research_prompts
const RESEARCH_TEMPLATES = {"scan.l4.card": "执行 {prompt}:先读整个任务包,再按其指令做渐进深度 DD + 早停,写决策卡到 {output}。最后返回该卡最终五档评级与 FINAL 行(code / rating / conviction / proposal=FINAL TRANSACTION PROPOSAL 的值,如 \"SELL\")。", "scan.l4.review": "独立复核 run{run_index}(不知道其它 run 结论):执行 {prompt} 的任务包,按人设走渐进深度 DD,决策卡写到 {output}(先自行创建 ensemble/ 目录),返回 code/rating/conviction/proposal。"}
const researchPrompt = (role, values) => {
  const template = RESEARCH_TEMPLATES[role]
  if (!template) throw new Error('unknown research template')
  const keys = [...new Set([...template.matchAll(/\{(\w+)\}/g)].map(m => m[1]))].sort()
  if (JSON.stringify(keys) !== JSON.stringify(Object.keys(values).sort())) throw new Error('research template arguments mismatch')
  return template.replace(/\{(\w+)\}/g, (_, key) => String(values[key]))
}
// END GENERATED RESEARCH TEMPLATES
const A = (typeof args === 'string' && args ? JSON.parse(args) : args) || {}
const { date, code } = A
const validDate = (value) => {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const parsed = new Date(`${value}T00:00:00.000Z`)
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value
}
if (!validDate(date)) throw new Error(`args.date 非法:${String(date)}`)
const RUN_ID = A.run_id
if (!RUN_ID) throw new Error('args.run_id 必填；沿用 scan-market 的 run_id')
if (typeof RUN_ID !== 'string' || !/^\d{8}T\d{12}Z$/.test(RUN_ID)) throw new Error(`args.run_id 非法:${String(RUN_ID)}`)
if (typeof code !== 'string' || !/^\d{6}$/.test(code)) throw new Error(`args.code 非法:${String(code)}`)
if (!Number.isInteger(A.attempt) || A.attempt <= 0) {
  throw new Error(`args.attempt 必填且必须为正整数:${String(A.attempt)}`)
}
const name = A.name || ''
const sector = A.sector || '行业未知'
const cfg = A.cfg || {}
// Wave11-B5(07-21 事故根治,同 scan-market.js):空 cfg = 静默关 intel + 全体掉回缺省 effort,
// 且当时无人知晓。结构性拒绝替代文档叮嘱;确需空跑(离线试装)显式传 args.allow_empty_config=true。
if (!Object.keys(cfg).length && !A.allow_empty_config) {
  throw new Error('args.cfg 为空 —— 会静默关 intel/降 effort(07-21 事故)。传 allow_empty_config:true 才可空跑。')
}
// Wave11-B2:model/effort 单一事实源=scan_config.agents(闭集见 user_config._AGENT_ROLES);
// 本表=缺键回退值。调用点禁止内联字面量(product_shape_lint 会查)。回退链:
// config > 本表(AGENT_DEFAULTS) > agent .md frontmatter —— 非壳 role 本表不写 model 键,
// 缺省即落那一层(l4_intel=l4-intel frontmatter sonnet;l4_card/ens_review=l4-card frontmatter opus)。
// 08-06 review 修正:本表的 effort 只是"scan_config.jsonc 漏写该 role 键时"才会被吃到的
// 代码兜底值,不代表生产实际值——ens_review 的生产配置显式给 "max"(与 l4_card 同档,见
// scan_config.jsonc 的 ens_review 注),这里的 'xhigh' 只在配置文件缺这个键时才生效,平时
// 不会。此前误把"代码兜底值 xhigh"当成"与生产一致的 parity"写进了 task-8-report.md,
// 已在该报告追加更正(task-8-review.md Important,CONFIRMED)。
const AGENT_DEFAULTS = {
  gp_shell:      { model: 'sonnet', effort: 'low' },
  gp_shell_json: { model: 'sonnet', effort: 'low' },
  l4_intel:      { effort: 'max' },
  l4_card:       { effort: 'xhigh' },
  ens_review:    { effort: 'xhigh' },   // ≥OW/SELL 双复核 run2/3(此前借 l4_card 档;独立收口;
                                         // 代码兜底,生产实际吃 scan_config.jsonc 的 "max")
}
// Wave12-T33:**resolved 优先**。`cfg.resolved_agents` 是 Python 侧
// (`autoresearch.scan.user_config.resolve_agent_config`,落 `_resolved_agent_config.json`)
// 解释好的逐 role 生效值 —— model/effort 的解释从此只有一处,本文件不再参与解释。
// 本表(AGENT_DEFAULTS)降为**兜底**:只在 resolved 没随 args 传到时才吃(离线试装、
// 老编排、单 workflow 手动重跑)。两条腿的值必须一致,`tests/test_agent_defs.py` 用
// AST 相等断言机器锁住,不靠人记得同步。
const RESOLVED = (cfg.resolved_agents) || {}
const AG = (role) => (RESOLVED[role]
  ? { ...RESOLVED[role] }
  : { ...(AGENT_DEFAULTS[role] || {}), ...((cfg.agents || {})[role] || {}) })
const pinned = !!A.pinned   // dispatch-plan meta 透传;缺省 false = 现行为(parity)
const dossierSummary = String(A.dossierSummary || '').trim()   // dispatch-plan meta 透传;缺省空 = parity(M-2:全函数防御,同款 !!A.pinned)
// 引擎隔离根:engine 随每股 args.engine 或 cfg 透传(缺省 claude;只有 Claude 会执行本 js)
const ENGINE = (A.engine || (A.cfg && A.cfg.engine) || 'claude')
// shells(scan_config.shells):中继壳的轮数 / 等待 / 失联判定 / 取证壳数 / 尾行数
const SHELLS = cfg.shells || {}
if (!['claude', 'codex'].includes(ENGINE)) throw new Error(`args.engine 非法:${ENGINE}`)
throw new Error('HOST_CAPABILITY_REQUIRED: legacy research has no C4 task-bound dispatch transport; explicit session_v1 remains PILOT') // C4_LEGACY_GUARD
const SD = `context_${ENGINE}/scan_runs/${RUN_ID}/staging/${date}`
const PY = (stage, invocation, attempt = 1, subject = null) =>
  `AUTORESEARCH_ENGINE=${ENGINE} AUTORESEARCH_RUN_ID=${RUN_ID} ` +
  `uv run --no-sync python -m autoresearch.trace.exec_capture --run-id ${RUN_ID} ` +
  `--stage ${stage} --invocation-id ${invocation} --attempt ${attempt}` +
  `${subject ? ` --subject ${subject}` : ''} -- uv run --no-sync python -m`
const TASK_BOOK = `${SD}/_l4_tasks.json`
let taskAttempt = A.attempt
const CARD = { type: 'object', required: ['code', 'rating'],
  properties: { code: { type: 'string' }, rating: { type: 'string' },
    conviction: { type: 'number', minimum: 0, maximum: 100 }, proposal: { type: 'string' } } }
const rawAgent = agent
const EVENT_HASH_SCHEMA = { type: 'string', pattern: '^[0-9a-f]{64}$' }
const AGENT_EVENT_ROW = {
  type: 'object', additionalProperties: false,
  required: ['schema_version', 'seq', 'run_id', 'ts', 'engine', 'stage',
    'invocation_id', 'attempt', 'subject', 'event_type', 'payload', 'prev_hash', 'event_hash'],
  properties: {
    schema_version: { type: 'integer', enum: [1] },
    seq: { type: 'integer', minimum: 1 }, run_id: { type: 'string' },
    ts: { type: 'string' }, engine: { type: 'string' }, stage: { type: 'string' },
    invocation_id: { type: 'string' }, attempt: { type: 'integer', minimum: 1 },
    subject: { type: 'string' },
    event_type: { type: 'string', enum: ['AGENT_DISPATCHED', 'AGENT_COMPLETED', 'AGENT_FAILED'] },
    payload: { type: 'object' }, prev_hash: EVENT_HASH_SCHEMA, event_hash: EVENT_HASH_SCHEMA,
  },
}
const CONTROL_BINDING_RESULT = {
  type: 'object', additionalProperties: false,
  required: ['target_event_type', 'target_invocation_id', 'target_role'],
  properties: {
    target_event_type: { type: 'string' }, target_invocation_id: { type: 'string' },
    target_role: { type: 'string' }, target_event_hash: EVENT_HASH_SCHEMA,
  },
}
const CONTROL_EVENT_ROW = { ...AGENT_EVENT_ROW, properties: {
  ...AGENT_EVENT_ROW.properties,
  payload: { type: 'object', additionalProperties: false,
    required: ['error', 'result', 'role'],
    properties: { error: {}, result: CONTROL_BINDING_RESULT,
      role: { type: 'string' } } },
} }
const AGENT_EVENT_ACK = { type: 'object', required: ['ok', 'event', 'control_events'],
  additionalProperties: false,
  properties: { ok: { type: 'boolean' }, event: AGENT_EVENT_ROW,
    control_events: { type: 'array', minItems: SHELLS.trace_calls_per_target ?? 2, maxItems: SHELLS.trace_calls_per_target ?? 2,
      items: CONTROL_EVENT_ROW } } }
const safeAgentPart = (value) => String(value).replace(/[^A-Za-z0-9_.-]+/g, '-').replace(/^-+|-+$/g, '')
// Workflow runtime 没有非 agent 的 shell primitive。trace-control 只能在获调度后的第一条
// 精确命令里自登记；该命令原子追加 control dispatch → 目标边界 → control terminal，
// 不递归套 tracedAgent。若它连命令都未执行，外层只可 best-effort 报警，后续完整性门报缺。
// 每个目标 agent 固定承担两次 trace-control 调用开销(dispatch 前一次、terminal 后一次)。
const TRACE_CONTROL_CALLS_PER_TARGET = SHELLS.trace_calls_per_target ?? 2
const EVENT_HASH_RE = /^[0-9a-f]{64}$/
const EVENT_TS_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$/
const validateAgentEventAck = (ack, eventType, invocationId, role, controlInvocationId) => {
  if (!ack || ack.ok !== true || !ack.event || !Array.isArray(ack.control_events)) {
    throw new Error('trace-control ACK 缺 ok=true/event/control_events')
  }
  const completeEvent = (event) => !!event && event.schema_version === 1 &&
    Number.isInteger(event.seq) && event.seq > 0 && EVENT_TS_RE.test(event.ts) &&
    event.engine === ENGINE && EVENT_HASH_RE.test(event.prev_hash) &&
    EVENT_HASH_RE.test(event.event_hash) && event.payload &&
    typeof event.payload === 'object' && !Array.isArray(event.payload)
  const matches = (event, expectedType, expectedInvocation, expectedRole) =>
    completeEvent(event) && event.run_id === RUN_ID && event.stage === 'l4' &&
    event.invocation_id === expectedInvocation && event.event_type === expectedType &&
    event.subject === code && event.attempt === taskAttempt &&
    event.payload && event.payload.role === expectedRole
  if (!matches(ack.event, eventType, invocationId, role)) {
    throw new Error('trace-control ACK 目标边界绑定不匹配')
  }
  const bindingMatches = (event) => event.payload.result &&
    event.payload.result.target_event_type === eventType &&
    event.payload.result.target_invocation_id === invocationId &&
    event.payload.result.target_role === role
  if (ack.control_events.length !== TRACE_CONTROL_CALLS_PER_TARGET ||
      !matches(ack.control_events[0], 'AGENT_DISPATCHED', controlInvocationId, 'trace-control') ||
      !matches(ack.control_events[1], 'AGENT_COMPLETED', controlInvocationId, 'trace-control') ||
      !bindingMatches(ack.control_events[0]) || !bindingMatches(ack.control_events[1]) ||
      !ack.control_events[1].payload.result ||
      ack.control_events[1].payload.result.target_event_hash !== ack.event.event_hash) {
    throw new Error('trace-control ACK 自身生命周期绑定不匹配')
  }
  // ACK 只证明 relay 返回结构与目标 hash 的绑定；磁盘上的 hash chain 仍是权威现场。
  return ack
}
const emitAgentEvent = (eventType, invocationId, role) => {
  const terminal = eventType === 'AGENT_FAILED'
    ? ` --error-json '{"status":"threw"}'`
    : ` --result-json '{"status":"${eventType === 'AGENT_DISPATCHED' ? 'queued' : 'returned'}"}'`
  const evidenceInvocation = `agent-event-${invocationId}-${eventType.toLowerCase()}`
  const controlInvocationId = `trace-control-${invocationId}-${eventType.toLowerCase()}`
  return rawAgent(
    `执行:\`${PY('l4', evidenceInvocation, taskAttempt, code)} autoresearch.trace.capsule agent-event ${RUN_ID} ${eventType} ` +
      `--role ${role} --subject ${code} --invocation-id ${invocationId} --attempt ${taskAttempt} ` +
      `--control-invocation-id ${controlInvocationId}${terminal}\`。` +
      '把 stdout 最后一行 JSON 原样作为结构化返回；不要判断或增删字段。' +
      '**逐字节原样执行:不得添加 2>&1、tee、管道,不得改写或增删任何重定向。**',
    { agentType: 'general-purpose', ...AG('gp_shell_json'),
      label: `trace-control:${eventType}:${invocationId}`, schema: AGENT_EVENT_ACK })
    .then((ack) => validateAgentEventAck(
      ack, eventType, invocationId, role, controlInvocationId))
}
// 边界事件只发给**业务** agent —— 与 scan-market.js:178 同策,与 contracts 的角色表同源:
// `run_profile.SCAN_AGENT_ROLES` 从 `vocab.ROLE_STAGES` 派生,里面只有 strategist /
// sector-brief / l3-* / l4-*,`gp-shell` 与 `trace-control` 是确定性中继,完整性门从不点
// 它们的名,它们的现场是 logs/ 里的命令捕获(exec_capture),不欠 transcript。
// 🚨 2026-09-03:此前本文件给**每个壳**也发一对边界事件,于是一条确定性命令要 3 个
// subagent(dispatch 壳 + 命令壳 + completed 壳)。node 探针实测:一只票 18 次 agent
// 调用里 10 次是壳的边界取证。09-01 真跑 200 个 trace-control 壳吃掉该 run subagent
// 加权输入的 73%(13.6M/18.7M),而它们产出的事件**没有任何消费者**——当日
// `agents/index.json` 0 行。改为业务角色专属后,一只票 18 → 8 次 agent 调用。
const RELAY_ROLES = new Set(['gp-shell', 'trace-control'])
async function tracedAgent(invocationId, role, prompt, options) {
  // 中继不发边界事件(见 RELAY_ROLES 注)。仍走本包装器,调用点因此不需要 `agent(` 旁路。
  if (RELAY_ROLES.has(role)) return rawAgent(prompt, options)
  try {
    await emitAgentEvent('AGENT_DISPATCHED', invocationId, role)
  } catch (error) {
    log(`⚠️ agent dispatch 取证失败:${invocationId}:${error && error.message ? error.message : error}`)
  }
  try {
    const result = await rawAgent(prompt, options)
    try {
      await emitAgentEvent('AGENT_COMPLETED', invocationId, role)
    } catch (error) {
      log(`⚠️ agent completed 取证失败:${invocationId}:${error && error.message ? error.message : error}`)
    }
    return result
  } catch (error) {
    try {
      await emitAgentEvent('AGENT_FAILED', invocationId, role)
    } catch (traceError) {
      log(`⚠️ agent failed 取证失败:${invocationId}:${traceError && traceError.message ? traceError.message : traceError}`)
    }
    throw error
  }
}
const recordL4 = (errorCode = null) => tracedAgent(
  `gp-shell-${code}-${taskAttempt}-stage-result-${safeAgentPart(errorCode || 'success')}`, 'gp-shell',
  `在仓库根目录执行:\`${PY('l4', `l4-stage-${code}-attempt-${taskAttempt}`, taskAttempt, code)} autoresearch.scan.stock_stage l4 ${date} ${code}` +
  `${errorCode ? ` --error ${errorCode}` : ''}\`。只回报退出码,不要判断或解释。` +
  `**逐字节原样执行:不得添加 2>&1、tee、管道,不得改写或增删任何重定向。**`,
  { agentType: 'general-purpose', ...AG('gp_shell'), label: `stage:${code}` })
  .catch((e) => { log(`⚠️ L4 StageResult 写入失败:${e && e.message ? e.message : e}`); return null })
// 🚨 2026-08-05 事故(同族,见 scan-market.js:35 注释):`prepare` 子命令内含单票 slim 取数,
// 可能跑数分钟 → harness 转后台 → haiku 壳判定"卡住"并 pkill 生产作业。同样两条药:
// 显式告知耗时 + 禁杀纪律,model 升 sonnet(每票仅 1 次调用,代价可忽略)。
const taskGate = (subcommand, schema, label) => tracedAgent(
  `gp-shell-${code}-${taskAttempt}-${safeAgentPart(label)}`, 'gp-shell',
  `执行:\`if test -s ${TASK_BOOK}; then ${subcommand}; ` +
  `else echo '{"ok":true,"action":"LEGACY"}'; fi\`\n` +
  '把 stdout 最后一行 JSON 原样作为结构化返回；不要判断或增删字段。' +
  '**逐字节原样执行:不得添加 2>&1、tee、管道,不得改写或增删任何重定向。**\n' +
  '秒级命令,**前台执行**:Bash 调用不要设 run_in_background —— 后台任务会在你交卷时被 harness ' +
  '连进程树杀掉。**绝对不许 kill / pkill / 中断 / 重启**它。拿不到退出码就如实回报,不要自己"修"。',
  { agentType: 'general-purpose', ...AG('gp_shell_json'), label, schema })
// 通用确定性 CLI 壳:跑一条命令、把它打印的最后一行 JSON 原样带回(零判断)。
const gpJson = (cmd, label, schema) => tracedAgent(
  `gp-shell-${code}-${taskAttempt}-${safeAgentPart(label)}`, 'gp-shell',
  `执行:\`${cmd}\`\n它会向 stdout 打印一行 JSON。把最后一行 JSON 原样作为结构化返回,` +
  '不改、不增删字段。**逐字节原样执行:不得添加 2>&1、tee、管道,不得改写或增删任何重定向。**',
  { agentType: 'general-purpose', ...AG('gp_shell_json'), label, schema })
// 确定性命令壳:跑一条命令、只回报退出码 + stdout 末 15 行(零判断)。
// 2026-08-03 事故(与 scan-market.js 的 gpJson 同族、同一个提交 99efe7d):Wave10 A5 把
// intel_status 的**调用点**写进本文件(L160),却没带上这份定义 —— 每只票都会在 Intel 相位
// 之后同步抛 `bash is not defined`,`.catch(() => null)` 接不住(ReferenceError 在 promise
// 生成前就抛了),结果是一张决策卡都出不来。与 scan-market.js:35 的 bash() 同语义、同签名。
const bash = (cmd, label, phaseName) => tracedAgent(
  `gp-shell-${code}-${taskAttempt}-${safeAgentPart(label)}`, 'gp-shell',
  `在仓库根目录精确执行下面这条命令,然后只回报:退出码 + stdout 末 ${SHELLS.tail_lines ?? 15} 行。` +
  '不要做别的、不要判断、不要解释。\n' +
  '**逐字节原样执行:不得添加 2>&1、tee、管道,不得改写或增删任何重定向。**\n' +
  '**前台执行**:Bash 调用不要设 run_in_background —— 后台任务会在你交卷时被 harness 连进程树杀掉' +
  '(2026-09-26 事故)。**绝对不许 kill / pkill / 中断 / 重启**它(2026-08-05 事故:' +
  '壳 pkill 了生产作业两次,整条流水线被毙)。\n\n' +
  `\`\`\`\n${cmd}\n\`\`\``,
  { agentType: 'general-purpose', ...AG('gp_shell'), label,
    ...(phaseName ? { phase: phaseName } : {}) })
// 🚨 2026-09-26 事故(scan-market prelude;09-15 slim 同族):壳用 run_in_background 跑长命令后交卷,
// harness 按**进程树**杀掉后台任务 —— 09-15 两只假 BLOCKED + 一张盲卡,09-17 002444 同样没等就交卷。
// slim prepare(09-17 实测均 82s / 峰 149s)走 detach:命令双 fork 脱离壳的进程树,壳只跑 ≤100s 的
// 有界前台等待,这里循环到终态;同 key 再调只等不重跑(`autoresearch/trace/detach.py`)。
const shq = (s) => `'${String(s).replace(/'/g, `'\\''`)}'`
// state 用枚举、禁多余字段:09-26 真壳冒烟里壳自编 state="done" 并把整行包进同名字段。
const DETACHED = { type: 'object', required: ['state', 'key'], additionalProperties: false,
  properties: { state: { type: 'string', enum: ['RUNNING', 'COMPLETED', 'FAILED', 'LOST'] },
    key: { type: 'string' }, exit_code: { type: ['integer', 'null'] }, tail: { type: 'string' },
    stderr_tail: { type: 'string' }, result: {},
    expect_file: { type: ['boolean', 'null'] }, reason: { type: 'string' } } }
const DETACH_TERMINAL = ['COMPLETED', 'FAILED', 'LOST']
async function detached(key, cmd, label, phaseName, maxRounds = SHELLS.detached_max_rounds_l4 ?? 20) {
  const call = `AUTORESEARCH_ENGINE=${ENGINE} uv run --no-sync python -m autoresearch.trace.detach ` +
    `--run-id ${RUN_ID} --key ${key} --wait-seconds ${SHELLS.wait_seconds ?? 100} --shell ${shq(cmd)}`
  let misses = 0
  // Workflow 运行时禁用 Date.now()(破坏 resume;09-26 探针实测)→ 用轮数封顶,每轮 ≤~100s 有界等待。
  for (let i = 1; i <= maxRounds; i++) {
    const res = await tracedAgent(
      `gp-shell-${code}-${taskAttempt}-${safeAgentPart(label)}${i > 1 ? `-w${i}` : ''}`, 'gp-shell',
      '执行下面这条命令。它只打印一行 JSON 对象:把**这个对象本身**逐字段照抄作为结构化返回 —— 不要包一层、不要改写 state 的值、不要添加字段。不要判断、不要解释、不要重试或改写命令。\n' +
      '**前台执行**:Bash 调用不要设 run_in_background(它最多约 100 秒就返回;真正的长任务已在后台独立运行)。\n' +
      `**逐字节原样执行:不得添加 2>&1、tee、管道,不得改写或增删任何重定向。**\n\n\`\`\`\n${call}\n\`\`\``,
      { agentType: 'general-purpose', ...AG('gp_shell_json'), label: i > 1 ? `${label}#${i}` : label,
        schema: DETACHED, ...(phaseName ? { phase: phaseName } : {}) })
      .catch(() => null)
    if (res && DETACH_TERMINAL.includes(res.state)) return res
    misses = (res && typeof res.state === 'string') ? 0 : misses + 1   // 形状不对 = 没回报
    if (misses >= (SHELLS.misses_lost ?? 3)) return { state: 'LOST', key, exit_code: null, tail: '', reason: `中继壳连续 ${SHELLS.misses_lost ?? 3} 次无有效回报` }
  }
  return { state: 'TIMEOUT', key, exit_code: null, tail: '', reason: `${maxRounds} 轮有界等待内未到终态` }
}
const INTEL_GUARD = { type: 'object', required: ['ok', 'code', 'action'],
  properties: { ok: { type: 'boolean' }, code: { type: 'string' }, action: { type: 'string' },
    claimed: {}, hard_cap: { type: 'integer' }, kept_as: { type: 'string' },
    warn: { type: 'string' }, note: { type: 'string' } } }
const TASK_ACTION = { type: 'object', required: ['ok', 'action'],
  properties: { ok: { type: 'boolean' }, action: { type: 'string' },
    attempt: { type: 'integer' }, reason: { type: 'string' },
    intel_resume: { type: 'boolean' } } }
const TASK_RESULT = { type: 'object', required: ['ok'],
  properties: { ok: { type: 'boolean' }, action: { type: 'string' },
    status: { type: 'string' }, reason: { type: 'string' }, attempts: { type: 'integer' } } }
const classifyFailure = (error) => {
  const msg = String((error && error.message) || error || '').toLowerCase()
  if (/rate.?limit|429/.test(msg)) return 'RATE_LIMIT'
  if (/timeout|timed out/.test(msg)) return 'TIMEOUT'
  if (/connect|socket|network|closed mid-response/.test(msg)) return 'CONNECTION'
  if (/schema|contract|json/.test(msg)) return 'SCHEMA_ERROR'
  return 'AGENT_ERROR'
}
const taskFailure = (errorClass) => taskGate(
  `${PY('l4', `l4-failure-${code}-attempt-${taskAttempt}`, taskAttempt, code)} ` +
    `autoresearch.scan.l4_tasks failure ${code} ${date} --error-class ${errorClass} --expected-attempt ${taskAttempt}`,
  TASK_RESULT,
  `task-failure:${code}`,
).catch(() => null)

// C1b(2026-08-10):bookless 的 LEGACY 分支移入 python(壳零判断)——preflight 现在
// 无论有无任务簿都能回答,且缺 prompt 一律 BLOCKED(盲卡在这里绝育)。
const taskPreflight = await gpJson(
  `${PY('l4', `l4-preflight-${code}-attempt-${taskAttempt}`, taskAttempt, code)} autoresearch.scan.l4_tasks preflight ${code} ${date} --expected-attempt ${taskAttempt}`,
  `task-preflight:${code}`,
  TASK_ACTION,
)
if (!taskPreflight) {
  await recordL4('task_preflight_no_return')
  return { code, name, rating: null, final: null, error: 'L4 task preflight 无返回' }
}
if (taskPreflight && taskPreflight.action === 'SKIP') {
  log(`♻️ L4 task ${code} 三件产物 hash 验证通过 → 跳过`)
  return { code, name, rating: null, final: null, reused: true, task_status: 'SUCCEEDED' }
}
if (taskPreflight && ['BLOCKED', 'WAIT'].includes(taskPreflight.action)) {
  return { code, name, rating: null, final: null,
    error: `task ${taskPreflight.action}:${taskPreflight.reason || ''}` }
}
const trackedTask = !!taskPreflight && taskPreflight.action === 'RUN'
if (trackedTask && (!Number.isInteger(taskPreflight.attempt) || taskPreflight.attempt <= 0 || taskPreflight.attempt !== taskAttempt)) {
  throw new Error(`L4 task attempt 与 args.attempt 不一致(task=${String(taskPreflight.attempt)}, args=${taskAttempt})；拒绝复用 invocation_id`)
}
const intelResume = !!(taskPreflight && taskPreflight.intel_resume)

// ── Slim ∥ Intel(结构性盲:prompt 只给码/名/行业/日期,防确认偏误)────────────
phase('Intel')
const intelOn = !!(cfg.l4_intel && cfg.l4_intel.enabled)
const maxQ = (cfg.l4_intel && cfg.l4_intel.max_queries) ?? 20   // 缺省与注册表 DEFAULT_INTEL_MAX_QUERIES 同值
const INTEL = { type: 'object', required: ['code'],
  properties: { code: { type: 'string' }, events: { type: 'integer' } } }
const knownBase = dossierSummary
  ? `\n\n## 已知底(覆盖档案摘要·仅用于去重,**不是**查询方向指令)\n${dossierSummary}\n\n已在上面出现的事实不必复查,查询额度全花在增量与新事件上。`
  : ''
let slimResult = null
let intelResult = null
let intelAttempts = 0
let intelError = null
// Wave10 A6:瞬时错(RATE_LIMIT/CONNECTION/TIMEOUT/ENOTFOUND)最多重试 2 次。
// 07-31 实跑 ENOTFOUND ×3 —— presence-gate 让任务"完成"了,但两张卡情报面变薄
// **而报告上看不出来**。非瞬时错(如 SCHEMA_ERROR)不重试:重试它只是把一次失败
// 变成三次失败 + 三倍延迟。终失败 → 结构化 DEGRADED,不是静默变薄。
// ── <contracts:begin> ── 由 `python -m autoresearch.contracts.emit --write` 生成,勿手编
// 真身:autoresearch/contracts/agent_output.py(评级序) · autoresearch/contracts/retry.py(瞬时错误)
// 评级名次:**JS 与 python 方向相反**(这里 sell=0…buy=4,而 python 的
// RATING_ORDER 是 Buy=0…Sell=4)。此前两边各写一份字面量、谁也没有测试锁,
// 方向只活在人的记忆里。
const RANK = { buy: 4, overweight: 3, hold: 2, underweight: 1, sell: 0 }
// 可重试的瞬时错误(**情报再搜**口径 = contracts/retry.INTEL_RESEARCH)。
// 注意 `retry.TASK_ATTEMPT` 是**另一套**(含 STALE_TASK 而非 ENOTFOUND):
// 任务簿重试与网查重试是两条不同策略,名字像但不是一回事 —— 别顺手合并。
const TRANSIENT = ['RATE_LIMIT', 'CONNECTION', 'TIMEOUT', 'ENOTFOUND']
// ── <contracts:end> ──
const errClass = (e) => {
  const m = String((e && e.message) || e || '').toUpperCase()
  return TRANSIENT.find((t) => m.includes(t)) || 'OTHER'
}
const intelMaxAttempts = (cfg.l4_intel && cfg.l4_intel.max_attempts) ?? 3   // l4_intel.max_attempts
async function intelLeg() {
  for (let i = 1; i <= intelMaxAttempts; i++) {
    intelAttempts = i
    try {
      return await tracedAgent(
        `l4-intel-${code}-${taskAttempt}${i > 1 ? `-retry-${i}` : ''}`, 'l4-intel',
        `活体情报采集:${code} ${name}(${sector})· 分析日 ${date}。按你的人设六面全查(≤${maxQ} 条),写 ${SD}/_l4_intel_${code}.md;返回 code 与事件行数 events。${knownBase}`,
        { agentType: 'l4-intel', ...AG('l4_intel'),
          label: i > 1 ? `intel:${code}#${i}` : `intel:${code}`, phase: 'Intel', schema: INTEL })
    } catch (e) {
      intelError = errClass(e)
      if (!TRANSIENT.includes(intelError) || i === 3) {
        log(`🕵️ intel ✗ ${code}:${intelError}(第 ${i} 次;${TRANSIENT.includes(intelError) ? '重试用尽' : '非瞬时错不重试'})→ 卡回退卡内网查`)
        return null
      }
      log(`🕵️ intel ↻ ${code}:${intelError}(第 ${i} 次,重试)`)
    }
  }
  return null
}
let slimRun = null
await parallel([
  // 结果取 prepare 自己打印的最后一行 JSON(detach 从 stdout 读,不经壳转述);语义同原 taskGate。
  () => detached(`slim-${code}-attempt-${taskAttempt}`,
    `if test -s ${TASK_BOOK}; then ${PY('l4', `l4-prepare-${code}-attempt-${taskAttempt}`, taskAttempt, code)} ` +
      `autoresearch.scan.l4_tasks prepare ${code} ${date}; else echo '{"ok":true,"action":"LEGACY"}'; fi`,
    `slim:${code}`, 'Intel')
    .then((r) => {
      slimRun = r
      slimResult = ['COMPLETED', 'FAILED'].includes(r.state) && r.result && typeof r.result === 'object'
        ? r.result : null
      return r
    }),
  ...(intelOn && !intelResume ? [() => intelLeg().then((r) => { intelResult = r; return r })] : []),
])
if (!intelOn) {
  log(`intel 关(config l4_intel.enabled=false)→ 直接出卡`)
  // 复核修复轮1(2026-08-10 Important):这条落盘不能只在"跑过 intel"的分支才做——DISABLED
  // 是三态之一(vs DEGRADED/缺状态),`--disabled` 这个 flag 存在的唯一理由就是让报告侧分清
  // "情报面被主动关掉"与"情报面出事了/根本没有"。值与迁移前逐字节一致:本分支从不派 intelLeg,
  // intelAttempts 恒 0、intelResult/intelError 恒 null → 只带 --normalize --disabled。
  await bash(
    `${PY('l4', `l4-intel-status-${code}-attempt-${taskAttempt}`, taskAttempt, code)} autoresearch.scan.l4.intel_status ${date} ${code} --normalize` +
    `${intelOn ? '' : ' --disabled'}${intelAttempts > 1 ? ` --attempts ${intelAttempts}` : ''}` +
    `${intelResult ? '' : (intelError ? ` --error-class ${intelError}` : '')}`,
    `intel-status:${code}`, 'Intel').catch(() => null)
} else if (intelResume) {
  // C3:同日 crash-resume —— 稿 + status 已在盘上且 ≤24h(preflight 验过并已 mark_resumed),
  // 重盲搜只是把同一晚的六面查询再付一遍(2026-08-09 实测 34 次里 22 次是重复 ≈$15)。
  // guard/intel-status 也跳过:它们的产物就是上一轮落的那两份,重写只会抹掉 attempts 痕迹。
  log(`🕵️ intel ♻ ${code} 同日续传(稿+status 验证通过,盲搜跳过;status.resumed=true 已披露)`)
} else {
  log(intelResult ? `🕵️ intel ✓ ${code}(events=${intelResult.events ?? '?'})` : `🕵️ intel ✗ ${code}(缺稿,卡自动回退卡内网查)`)
  // W8-13:硬顶守卫。cap(20)是**指令级**约束、agent 想超就超(07-28 十一稿 16–29,
  // 旧 cap 15 下 11/11 超限 = 天天报警天天无视,狼来了);硬顶 30 才有牙齿 ——
  // 超顶把稿改名 .rejected.md,下面 card 的 presence-gate 自动回退卡内网查。
  // 铁律:**只拒稿不拒票**,守卫失败一律不阻断本票。
  if (intelResult) {
    const g = await gpJson(
      `${PY('l4', `l4-intel-guard-${code}-attempt-${taskAttempt}`, taskAttempt, code)} autoresearch.scan.l4.intel_guard ${date} ${code}`,
      `intel-guard:${code}`, INTEL_GUARD)
      .catch((e) => { log(`⚠️ intel-guard ✗ ${code}:${e && e.message ? e.message : e}(放行)`); return null })
    if (g && g.action === 'REJECTED') {
      log(`🚫 intel 拒稿 ${code}:自报 ${g.claimed} 条 > 硬顶(留档 ${g.kept_as});卡回退卡内网查`)
    } else if (g && g.warn === 'unreported') {
      log(`⚠️ intel ${code} 未自报查询数(无法对账,不拒稿)`)
    }
  }
  // Wave10 A5:三正交字段落盘 + 直播 —— 此前只显式播 REJECTED/unreported,**TRIMMED 不播**,
  // 于是 07-31 的 000651「自报 39 条触发超硬顶审计」在报告里零痕迹。报告/直播/T1 从此读同一份
  // 结构化状态,谁也不许再解析稿头猜。A7 的旧事件净分归一化同批跑(--normalize)。
  await bash(
    `${PY('l4', `l4-intel-status-${code}-attempt-${taskAttempt}`, taskAttempt, code)} autoresearch.scan.l4.intel_status ${date} ${code} --normalize` +
    `${intelOn ? '' : ' --disabled'}${intelAttempts > 1 ? ` --attempts ${intelAttempts}` : ''}` +
    `${intelResult ? '' : (intelError ? ` --error-class ${intelError}` : '')}`,
    `intel-status:${code}`, 'Intel').catch(() => null)
}
// 命令本体失联/超时 = 基础设施事件,归可重试的 TIMEOUT,不归 DATA_INTEGRITY(09-15 假 BLOCKED 连坐 data_a 的教训)。
if (slimRun && ['LOST', 'TIMEOUT'].includes(slimRun.state)) {
  await taskFailure('TIMEOUT')
  await recordL4(`slim_${slimRun.state.toLowerCase()}`)
  return { code, name, rating: null, final: null,
    error: `slim 准备 ${slimRun.state}(可重试):${slimRun.reason || ''}` }
}
if (slimResult && slimResult.action !== 'LEGACY' && !slimResult.ok) {
  await taskFailure('DATA_INTEGRITY')
  await recordL4('slim_data_integrity')
  return { code, name, rating: null, final: null,
    error: `slim 不合格:${slimResult.reason || 'DATA_INTEGRITY'}` }
}
if (trackedTask && !slimResult) {
  await taskFailure('CONTRACT_ERROR')
  await recordL4('slim_preflight_no_return')
  return { code, name, rating: null, final: null, error: '单票 slim 准备无返回' }
}

// ── Card ────────────────────────────────────────────────────────
phase('Card')
let card
try {
  card = await tracedAgent(
    `l4-card-${code}-${taskAttempt}`, 'l4-card',
    researchPrompt('scan.l4.card', {prompt: `${SD}/_l4_prompt_${code}.md`, output: `${SD}/details/${code}.md`}),
    { agentType: 'l4-card', ...AG('l4_card'),
      label: `card:${code}`, phase: 'Card', schema: CARD })
} catch (error) {
  await taskFailure(classifyFailure(error))
  await recordL4('card_agent_exception')
  throw error
}
if (!card) {
  await taskFailure('CONTRACT_ERROR')
  await recordL4('card_no_return')
  return { code, name, rating: null, final: null, error: 'card 无返回 —— 单股失败只废单股,主会话单独重跑本 workflow 即可' }
}
// pr_20260717_005:同一字段两种标度(07-14 实测一只回 0.62,其余 8 只是 60–78 整数)。
// 下游 force_full_card 判据是 conviction>=70 —— 0.62 会被当成极低确信而静默失效。
// <=1 视为比例口径,归一到 0-100;>1 原样(0-100 本身不会落进 (0,1])。
const normConviction = (v) => (typeof v === 'number' && v > 0 && v <= 1 ? v * 100 : v)
if (card.conviction != null) card.conviction = normConviction(card.conviction)
log(`L4 卡 ✓ ${code} → ${card.rating}`)

// ── Verify:≥OW 双复核(防追高误买)∥ pinned 卖出双复核(防误卖持仓,Wave1 ⑤-3)──
// 取中位;ow_review 只向下折、sell_review 只向温和折(assemble 侧 _apply_ensemble_fold 按 trigger 再折一遍=权威)。
// l4.review(scan_config;Python 侧同一份规则在 decision_finalize.review_trigger)
const REVIEW = (cfg.l4 && cfg.l4.review) || {}
const owRatings = (REVIEW.ow_ratings || ['Buy', 'Overweight', '增持', '买入']).map((x) => String(x).toLowerCase())
const isOW = (r) => owRatings.includes(String(r || '').toLowerCase())
const sellReviewPinnedOnly = REVIEW.sell_review_pinned_only ?? true
const maxRuns = REVIEW.max_runs ?? 3
const isSellish = (card) => /sell/i.test(card.rating || '') || /sell/i.test(card.proposal || '')
const trigger = isOW(card.rating) ? 'ow_review' : ((pinned || !sellReviewPinnedOnly) && isSellish(card) ? 'sell_review' : null)
let final = card.rating
if (trigger) {
  phase('Verify')
  log(trigger === 'ow_review'
    ? `🎭 买单复核:${code} 追加 2 独立 run 取中位(只向下折回)`
    : `🎭 持仓卖出复核:${code} 追加 2 独立 run 取中位(只向温和折回,卖错持仓代价不对称)`)
  const tier = (r) => RANK[String(r || '').toLowerCase()] ?? 2
  const rerun = (i) => tracedAgent(
    `ens-review-${code}-${taskAttempt}-reviewer-${i}`, 'ens-review',
    researchPrompt('scan.l4.review', {prompt: `${SD}/_l4_prompt_${code}.md`, output: `${SD}/ensemble/${code}.run${i}.md`, run_index: i}),
    { agentType: 'l4-card', ...AG('ens_review'),
      label: `ens${i}:${code}`, phase: 'Verify', schema: CARD })
  // Wave6 T2 同档早止:run1==run2 时三票中位**数学上已定**(两票同档 → 排序中位恒为该档,
  // 第三票投什么都改不了),run3 是确定的冗余 → 跳过省一张满卡(07-24 的 601869 三票全 UW)。
  // 分歧则照常跑 run3 当裁决票。代价:串行化后分歧场景墙钟略长,同档场景反而更短。
  const r2 = await rerun(2)
  const sameTier = !!r2 && tier(r2.rating) === tier(card.rating)
  const r3 = (sameTier || maxRuns < 3) ? null : await rerun(3)
  // Wave12-T34:**派发次数**(不是成功次数)——失败的 run 一样烧了 token、一样在
  // harvest 里留一行,所以归因普查要数"派了几次"。r2 恒派;r3 仅在分歧时派。
  const ensDispatched = sameTier ? 1 : 2
  const earlyStopped = sameTier
  const reruns = [r2, r3].filter(Boolean)
  if (earlyStopped) log(`🎭 同档早止:${code} run2 与 run1 同为 ${card.rating} —— 中位已定,跳过 run3`)
  const ratings = [card.rating, ...reruns.map((r) => r.rating)]
  const sorted = ratings.map(tier).sort((a, b) => a - b)
  // degraded 只表示「复核 run 失败」(→ 不折回原判 + 强制人裁展示,sell_review 不因缺 run 软化卖出)。
  // 早止是**主动省跑**,必须照常折回 —— 写反会让 SELL 复核在最该救回误卖持仓时不折。
  const degraded = earlyStopped ? false : ratings.length < 3
  const medianTier = sorted[Math.floor(sorted.length / 2)]
  const names = ['Sell', 'Underweight', 'Hold', 'Overweight', 'Buy']
  // Wave12-T34:`role`/`n_dispatch` = 本票 ens_review 的**派发子记录**。
  // 复核 run 复用 `agentType: 'l4-card'` 派发,harvest 的 attributionAgent 只记 agentType,
  // 于是 usage_reconcile 此前分不清一行是主卡还是复核,靠「两者 effort 恰好同为 max」蒙混。
  // 这两个键让它有据可归行(`autoresearch.trace.usage_reconcile.dispatch_census`)。
  // 零新增派发:搭既有 ens-dump 的便车,只多两个 JSON 字段。
  const rec = { code, ratings, median: names[medianTier],
    spread: sorted[sorted.length - 1] - sorted[0], degraded, trigger,
    n_runs: ratings.length, early_stopped: earlyStopped,
    role: 'ens_review', n_dispatch: ensDispatched }
  await tracedAgent(
    `gp-shell-${code}-${taskAttempt}-ens-dump`, 'gp-shell',
    `在仓库根目录精确执行下面这条命令,然后只回报退出码。不要做别的、不要判断。\n` +
    `**逐字节原样执行:不得添加 2>&1、tee、管道,不得改写或增删任何重定向(heredoc 原样保留)。**` +
    `\n\n\`\`\`\ncat > ${SD}/_ensemble_${code}.json << 'EOF'\n${JSON.stringify(rec)}\nEOF\n\`\`\``,
    // Wave6 T1:heredoc 写文件,零判断
    { agentType: 'general-purpose', ...AG('gp_shell_json'), label: `ens-dump:${code}`, phase: 'Verify' })
  if (!degraded) {
    if (trigger === 'ow_review' && tier(rec.median) < tier(card.rating)) final = rec.median
    if (trigger === 'sell_review' && tier(rec.median) > tier(card.rating)) final = rec.median
  }
  log(`🎭 复核 ✓ ${code} [${trigger}] runs=${JSON.stringify(ratings)} → 终评 ${final}${degraded ? '(degraded,报告强制人裁展示)' : ''}`)
}
const taskDone = await taskGate(
  `${PY('l4', `l4-success-${code}-attempt-${taskAttempt}`, taskAttempt, code)} ` +
    `autoresearch.scan.l4_tasks success ${code} ${date} --expected-attempt ${taskAttempt}`,
  TASK_RESULT,
  `task-success:${code}`,
).catch(() => null)
if (trackedTask && (!taskDone || !taskDone.ok)) {
  await taskFailure('SCHEMA_ERROR')
  await recordL4('task_book_success_failed')
  return { code, name, rating: card.rating, final, conviction: card.conviction,
    error: '卡已生成，但任务簿产物校验失败；本票未标成功' }
}
await recordL4()
return { code, name, rating: card.rating, final, conviction: card.conviction,
  task_status: taskDone && taskDone.status ? taskDone.status : 'LEGACY' }
