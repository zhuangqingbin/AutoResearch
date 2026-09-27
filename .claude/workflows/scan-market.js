// LEGACY_ORCHESTRATION_FALLBACK: keep for host rollback until real session_v1 acceptance.
export const meta = {
  name: 'scan-market',
  description: '全 A股漏斗前段(prelude→市场/行业→L3→L4-prep,GATE1/2/3)→ 返回 dispatch 交接;决策卡=每股独立 l4-stock workflow 由主会话并行拉起,assemble+GATE4 由主会话收尾(fb_20260714_003)',
  phases: [
    { title: 'Prelude', detail: 'frame → [universe/L0-L2 ∥ market_view] → GATE1' },
    { title: 'L3', detail: '[sector-briefs ∥ 证据harvest] → L3-rank → finalists+GATE2(合并壳)' },
    { title: 'L4-prep', detail: 'l4-prep(生产者并行)→ dispatch-plan → GATE3 slim(失败只剔单股)→ 交接每股 l4-stock' },
  ],
}

// ── 输入 & 常量 ──────────────────────────────────────────────────
// args 可能以对象或(harness 序列化后的)JSON 字符串到达 —— 两种都容错解析。
const A = (typeof args === 'string' && args ? JSON.parse(args) : args) || {}
const date = A.date
const validDate = (value) => {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const parsed = new Date(`${value}T00:00:00.000Z`)
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value
}
if (!validDate(date)) throw new Error(`args.date 非法:${String(date)}`)
const RUN_ID = A.run_id
if (!RUN_ID) throw new Error('args.run_id 必填；先运行 autoresearch.trace.capsule begin')
if (typeof RUN_ID !== 'string' || !/^\d{8}T\d{12}Z$/.test(RUN_ID)) throw new Error(`args.run_id 非法:${String(RUN_ID)}`)
// scan_config.json 白名单校验后的 user_config(autoresearch/scan/user_config.py)经 frame --json
// 回显、由调用方随 Workflow args.config 传入(本脚本无文件系统访问,不能自己读文件)。缺省 = {}。
const cfg = A.config || {}
// Wave11-B5(07-21 事故根治):空 cfg = 静默关 intel + 全体掉回缺省 effort,且当时无人知晓。
// 结构性拒绝替代文档叮嘱;确需空跑(离线试装)显式传 args.allow_empty_config=true。
const _allowEmpty = !!A.allow_empty_config
if (!Object.keys(cfg).length && !_allowEmpty) {
  throw new Error('args.config 为空 —— 会静默关 intel/降 effort(07-21 事故)。传 allow_empty_config:true 才可空跑。')
}
// Wave11-B2:model/effort 单一事实源=scan_config.agents(闭集见 user_config._AGENT_ROLES);
// 本表=缺键回退值。调用点禁止内联字面量(product_shape_lint 会查)。回退链:
// config > 本表(AGENT_DEFAULTS) > agent .md frontmatter —— opus 类 role 本表不写 model 键,
// 缺省即落那一层(strategist/sector_brief/l3_rank/l3_repair 皆是)。
const AGENT_DEFAULTS = {
  gp_shell:      { model: 'sonnet', effort: 'low' },
  gp_shell_json: { model: 'sonnet', effort: 'low' },
  l3_repair:     { effort: 'medium' },     // model 缺省=l3-rank frontmatter(opus)
  strategist:    { effort: 'high' },       // model 缺省=macro-brief frontmatter(opus)
  sector_brief:  { effort: 'high' },       // model 缺省=sector-brief frontmatter(opus)
  l3_rank:       { effort: 'max' },        // model 缺省=l3-rank frontmatter(opus)
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
// Wave 3 性能开关只改变调度/上下文布局，不拥有 finalist、rubric 或评级语义。
// streaming 默认开；另外两项默认当前生产行为，均有显式回滚杆。
const streamingL4 = cfg.performance?.streaming_l4 ?? true
// Wave10 B4:`stable_context_blocks` 已退役(离线 benchmark 收益 4.0% < 10% 门)。
// 这里连读都不再读 —— 留着已退役的 `cfg.performance?.stable_context_blocks` 键就等于留了个陷阱:
// Python 侧的 `--stable-context` 已随 context_blocks.py 一并删除,谁把这个键加回 config,
// 这条流水线就会给一个不认识它的 CLI 传 flag。
// 哨兵档人工 override(SKILL 步骤 2.2:哨兵是「确定性建议,**人拍板**」,而本脚本原先硬编码直接跳 L3/L4
// —— 判据只问"今天有没有值得买的",不知道用户还有"保送持仓该不该走"的问题挂着)。缺省 false = 现行为(parity)。
const forceFull = !!A.force_full
// 引擎隔离根(2026-08-11):context_<engine>,engine 随 args.config.engine 下发(frame 注入)
const ENGINE = (A.engine || cfg.engine || 'claude')
if (!['claude', 'codex'].includes(ENGINE)) throw new Error(`args.engine 非法:${ENGINE}`)

// ── 失败也要留下现场 ────────────────────────────────────────────────
// 主体包在 __main 里:业务异常必须**先冻结 capsule 再上抛**。不冻结的话,失败的 run
// 只剩一个 ACTIVE spool 和没人读的 stderr —— 而失败恰恰是最需要现场的那一种结局。
// (SIGKILL 走不到这里,那条路归 `capsule recover` 的陈旧租约恢复。)
async function __main() {
const CTX = `context_${ENGINE}`
const SD = `${CTX}/scan_runs/${RUN_ID}/staging/${date}`
const CAPTURE = (stage, invocation, attempt = 1, subject = null) =>
  `AUTORESEARCH_ENGINE=${ENGINE} AUTORESEARCH_RUN_ID=${RUN_ID} ` +
  `uv run --no-sync python -m autoresearch.trace.exec_capture --run-id ${RUN_ID} ` +
  `--stage ${stage} --invocation-id ${invocation} --attempt ${attempt}` +
  `${subject ? ` --subject ${subject}` : ''}`
const PY = (stage, invocation, attempt = 1, subject = null) =>
  `${CAPTURE(stage, invocation, attempt, subject)} -- uv run --no-sync python -m`
const PYC = (stage, invocation, attempt = 1) =>
  `${CAPTURE(stage, invocation, attempt)} -- uv run --no-sync python -c`

// 确定性命令 → general-purpose Bash-agent(只跑命令、回报退出码,不判断)
// Wave6 T1:壳零判断,却背着 opus 系统前缀 —— 07-24 真计量 13 个 gp 共 798k 加权(全场 14.5%),
// 其中 7 个 2-消息壳 ≈287k 纯过路费。降 haiku;判断仍在确定性 CLI 里,行为不变。
// 🚨 2026-08-05 事故(GATE1 毙全线):haiku 壳**杀了生产作业**。`prelude` 真身跑 ~20min >
// 前台 Bash 超时 → harness 自动转后台 → haiku 不会等异步任务,判定"卡住"→ `pkill -f
// autoresearch.scan.prelude` → 重启 → 再杀,两个壳(prelude / prelude-retry)都这么干,
// turn 预算烧光,L2_gbdt_top200.csv 始终不落 → GATE1 "universe 未跑?" 毙掉整条流水线。
// 病灶两条,都补上:① 壳的 prompt **从没告诉过它这条命令要跑多久**,更没禁止 kill;
// ② haiku 处理不了"后台任务 + 长等待"这个组合。model 升 sonnet + 显式禁杀纪律。
// 成本:bash 壳仅 ~5 个/次扫描且零判断,升一档远小于毙掉一整条 60min 流水线的代价。
function bash(cmd, label, phaseName) {   // 形参勿叫 phase:会遮蔽全局 phase() 分组函数
  return agent(
    `在仓库根目录精确执行下面这条命令,然后只回报:退出码 + stdout 末 15 行。不要做别的、不要判断、不要解释。\n` +
    `**逐字节原样执行:不得添加 2>&1、tee、管道,不得改写或增删任何重定向。**` +
    `若命令的 stdout 已被重定向,回报改用:退出码 + stderr 末 15 行。\n` +
    `(2026-07-28 事故第一因:壳擅自把 \`frame --json > market_pack.json\` 改成 \`... 2>&1\`,` +
    `stderr 日志灌进产物,门判据被骗过。)\n\n` +
    `铁律:\n` +
    `- **前台执行**:Bash 调用不要设 run_in_background —— 后台任务会在你交卷时被 harness 连进程树杀掉。\n` +
    `- **绝对不许 kill / pkill / 中断它**,也不许"重启一次试试"。\n` +
    `- 只有拿到真实退出码才算完;拿不到就如实回报"未拿到退出码",**不要**自己动手"修"。\n` +
    `(2026-08-05 事故:壳 pkill 了 prelude 两次;2026-09-26 事故:壳把 prelude 转后台后交卷,两次都被杀。)\n\n` +
    `\`\`\`\n${cmd}\n\`\`\``,
    { agentType: 'general-purpose', ...AG('gp_shell'), label, ...(phaseName ? { phase: phaseName } : {}) })
}
// 🚨 2026-09-26 事故(GATE1 毙全线):prelude 壳用 run_in_background 启动 prelude、挂 Monitor 就交卷;
// harness 在壳交卷时按**进程树**杀掉它的后台任务,两次 prelude 都在 ~7s 吃 SIGKILL,L2 不落。
// 壳选不选后台是模型行为(历史 135 次后台启动 23 次没等就交卷),叮嘱挡不住 —— 长命令(09-17 实测
// frame 150s / prelude 295s / l4-prep 287s / l3-prepare 29s)一律走 detach:命令双 fork 脱离壳的
// 进程树,壳只跑 ≤100s 的有界前台等待,这里循环到终态。壳后台化、提前交卷、被杀都碰不到命令本体;
// 同 key 再调只等不重跑(`autoresearch/trace/detach.py`)。
const shq = (s) => `'${String(s).replace(/'/g, `'\\''`)}'`
// state 用枚举、禁多余字段:09-26 真壳冒烟里壳自编 state="done" 并把整行包进同名字段。
const DETACHED = { type: 'object', required: ['state', 'key'], additionalProperties: false,
  properties: { state: { type: 'string', enum: ['RUNNING', 'COMPLETED', 'FAILED', 'LOST'] },
    key: { type: 'string' }, exit_code: { type: ['integer', 'null'] }, tail: { type: 'string' },
    stderr_tail: { type: 'string' }, result: {},
    expect_file: { type: ['boolean', 'null'] }, reason: { type: 'string' } } }
const DETACH_TERMINAL = ['COMPLETED', 'FAILED', 'LOST']
async function detached(key, cmd, label, phaseName, { expectFile = null, maxRounds = 40 } = {}) {
  const call = `AUTORESEARCH_ENGINE=${ENGINE} uv run --no-sync python -m autoresearch.trace.detach ` +
    `--run-id ${RUN_ID} --key ${key} --wait-seconds 100` +
    `${expectFile ? ` --expect-file ${expectFile}` : ''} --shell ${shq(cmd)}`
  let misses = 0
  // Workflow 运行时禁用 Date.now()(破坏 resume;09-26 探针实测)→ 用轮数封顶,每轮 ≤~100s 有界等待。
  for (let i = 1; i <= maxRounds; i++) {
    const res = await agent(
      `执行下面这条命令。它只打印一行 JSON 对象:把**这个对象本身**逐字段照抄作为结构化返回 —— 不要包一层、不要改写 state 的值、不要添加字段。不要判断、不要解释、不要重试或改写命令。\n` +
      `**前台执行**:Bash 调用不要设 run_in_background(它最多约 100 秒就返回;真正的长任务已在后台独立运行)。\n` +
      `**逐字节原样执行:不得添加 2>&1、tee、管道,不得改写或增删任何重定向。**\n\n\`\`\`\n${call}\n\`\`\``,
      { agentType: 'general-purpose', ...AG('gp_shell_json'), label: i > 1 ? `${label}#${i}` : label,
        schema: DETACHED, ...(phaseName ? { phase: phaseName } : {}) })
      .catch(() => null)
    if (res && DETACH_TERMINAL.includes(res.state)) return res
    misses = (res && typeof res.state === 'string') ? 0 : misses + 1   // 形状不对 = 没回报
    if (misses >= 3) return { state: 'LOST', key, exit_code: null, tail: '', reason: '中继壳连续 3 次无有效回报' }
  }
  return { state: 'TIMEOUT', key, exit_code: null, tail: '', reason: `${maxRounds} 轮有界等待内未到终态` }
}
// run_mode 四态白名单 = `autoresearch.scan.run_mode.MODES` 的镜像。GATE1 回来的模式不在其中,
// 就不是"取个默认值继续"的事(见下方 GATE1 处的守卫)。
const RUN_MODES = ['FULL', 'FORCED_FULL', 'SENTINEL_EMPTY', 'SENTINEL_PINNED']
const OK = { type: 'object', required: ['ok'],
  properties: { ok: { type: 'boolean' }, reason: { type: 'string' } } }
const STAGE_RESULT = { type: 'object', required: ['stage', 'status', 'metrics'],
  properties: { stage: { type: 'string' }, status: { type: 'string' },
    metrics: { type: 'object' }, error: {} } }
// Wave6 T1:门的判据 100% 在确定性 CLI 里,agent 只把它打印的 JSON 原样带回 —— 转述不需要
// 思考,effort high→low。schema 校验仍在(格式错会被 harness 拒),门行为不变。
// model 走 AGENT_DEFAULTS.gp_shell_json(Wave11-B2;08-05 事故后壳类缺省统一 sonnet,见 bash() 注)。
function gate(label, cmd, schema, phaseName) {   // 同上:避免遮蔽全局 phase()
  return agent(
    `执行:\`${cmd}\`\n它会向 stdout 打印 JSON。把它打印的最后一行 JSON 原样作为你的结构化返回(字段不改、不增删)。\n` +
    `**逐字节原样执行:不得添加 2>&1、tee、管道,不得改写或增删任何重定向**` +
    `(混入 stderr 会污染这行 JSON)。**前台执行**:Bash 调用不要设 run_in_background(秒级命令;后台任务会在你交卷时被杀)。`,
    { agentType: 'general-purpose', ...AG('gp_shell_json'), label, schema, ...(phaseName ? { phase: phaseName } : {}) })
}
// 2026-09-13:本文件的通用 JSON 壳 `gpJson` 已随它唯一的调用点(GATE1 后的独立 run-mode 壳)
// 一起退役 —— 模式判定并入 `gates gate1 --decide-run-mode`,少派一个 agent。
// 2026-08-03 事故的教训不随代码走:Wave10 A2(99efe7d)曾把 run_mode 的**调用点**抄进本文件、
// 定义却留在 l4-stock.js —— 每次全扫都在 GATE1 之后立刻 `gpJson is not defined`,而调用点的
// `.catch(() => null)` 兜不住(ReferenceError 同步抛,promise 压根没生成)。守卫仍在:
// tests/test_workflow_js_syntax.py 的未定义调用探针逐文件扫,本文件的反证锚点已改用 `PY`。
// ── agent 边界取证(Task 11)──────────────────────────────────────────────
// 每个**业务** agent(策略师/行业 brief/L3 精排/L3 自修)在派发前后各追加一条边界事件,
// 失败也追加 —— CP7 的 agents/index.json 按事件链点名,少一条就是 GONE,不是「目录里没有」。
// gp-shell / trace-control 是确定性中继,它们的现场是 logs/ 里的命令捕获,不欠 transcript。
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
    subject: { type: ['string', 'null'] },
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
    subject_display: { type: 'string' },
  },
}
const CONTROL_EVENT_ROW = { ...AGENT_EVENT_ROW, properties: {
  ...AGENT_EVENT_ROW.properties,
  payload: { type: 'object', additionalProperties: false,
    required: ['error', 'result', 'role'],
    properties: { error: {}, result: CONTROL_BINDING_RESULT, role: { type: 'string' } } },
} }
const AGENT_EVENT_ACK = { type: 'object', required: ['ok', 'event', 'control_events'],
  additionalProperties: false,
  properties: { ok: { type: 'boolean' }, event: AGENT_EVENT_ROW,
    control_events: { type: 'array', minItems: 2, maxItems: 2, items: CONTROL_EVENT_ROW } } }
const TRACE_CONTROL_CALLS_PER_TARGET = 2
const EVENT_HASH_RE = /^[0-9a-f]{64}$/
const EVENT_TS_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$/
const validateBoundaryAck = (ack, spec, eventType, controlInvocationId) => {
  if (!ack || ack.ok !== true || !ack.event || !Array.isArray(ack.control_events)) {
    throw new Error('trace-control ACK 缺 ok=true/event/control_events')
  }
  const completeEvent = (event) => !!event && event.schema_version === 1 &&
    Number.isInteger(event.seq) && event.seq > 0 && EVENT_TS_RE.test(event.ts) &&
    event.engine === ENGINE && EVENT_HASH_RE.test(event.prev_hash) &&
    EVENT_HASH_RE.test(event.event_hash) && event.payload &&
    typeof event.payload === 'object' && !Array.isArray(event.payload)
  // subject_display 的 ASCII key 由 python 侧 sha256 派生,JS 无法预知 —— 只校验它是字符串。
  const subjectOk = (event) => spec.subjectDisplay
    ? typeof event.subject === 'string' && event.subject.length > 0
    : event.subject === (spec.subject === undefined ? null : spec.subject)
  const matches = (event, expectedType, expectedInvocation, expectedRole) =>
    completeEvent(event) && event.run_id === RUN_ID && event.stage === spec.stage &&
    event.invocation_id === expectedInvocation && event.event_type === expectedType &&
    subjectOk(event) && event.attempt === (spec.attempt || 1) &&
    event.payload && event.payload.role === expectedRole
  if (!matches(ack.event, eventType, spec.invocationId, spec.role)) {
    throw new Error('trace-control ACK 目标边界绑定不匹配')
  }
  const bindingMatches = (event) => event.payload.result &&
    event.payload.result.target_event_type === eventType &&
    event.payload.result.target_invocation_id === spec.invocationId &&
    event.payload.result.target_role === spec.role
  if (ack.control_events.length !== TRACE_CONTROL_CALLS_PER_TARGET ||
      !matches(ack.control_events[0], 'AGENT_DISPATCHED', controlInvocationId, 'trace-control') ||
      !matches(ack.control_events[1], 'AGENT_COMPLETED', controlInvocationId, 'trace-control') ||
      !bindingMatches(ack.control_events[0]) || !bindingMatches(ack.control_events[1]) ||
      !ack.control_events[1].payload.result ||
      ack.control_events[1].payload.result.target_event_hash !== ack.event.event_hash) {
    throw new Error('trace-control ACK 自身生命周期绑定不匹配')
  }
  return ack
}
const emitBoundary = (spec, eventType) => {
  const attempt = spec.attempt || 1
  const terminal = eventType === 'AGENT_FAILED'
    ? ` --error-json '{"status":"threw"}'`
    : ` --result-json '{"status":"${eventType === 'AGENT_DISPATCHED' ? 'queued' : 'returned'}"}'`
  const subjectArg = spec.subjectDisplay
    ? ` --subject-display '${spec.subjectDisplay}'`
    : (spec.subject ? ` --subject ${spec.subject}` : '')
  const evidenceInvocation = `agent-event-${spec.invocationId}-${eventType.toLowerCase()}`
  const controlInvocationId = `trace-control-${spec.invocationId}-${eventType.toLowerCase()}`
  return rawAgent(
    `执行:\`${PY(spec.stage, evidenceInvocation, attempt)} autoresearch.trace.capsule agent-event ${RUN_ID} ${eventType} ` +
      `--role ${spec.role}${subjectArg} --invocation-id ${spec.invocationId} --attempt ${attempt} ` +
      `--control-invocation-id ${controlInvocationId}${terminal}\`。` +
      '把 stdout 最后一行 JSON 原样作为结构化返回；不要判断或增删字段。' +
      '**逐字节原样执行:不得添加 2>&1、tee、管道,不得改写或增删任何重定向。**',
    { agentType: 'general-purpose', ...AG('gp_shell_json'),
      label: `trace-control:${eventType}:${spec.invocationId}`, schema: AGENT_EVENT_ACK })
    .then((ack) => validateBoundaryAck(ack, spec, eventType, controlInvocationId))
}
async function tracedAgent(spec, prompt, options) {
  try {
    await emitBoundary(spec, 'AGENT_DISPATCHED')
  } catch (error) {
    log(`⚠️ agent dispatch 取证失败:${spec.invocationId}:${error && error.message ? error.message : error}`)
  }
  try {
    const result = await rawAgent(prompt, options)
    try {
      await emitBoundary(spec, 'AGENT_COMPLETED')
    } catch (error) {
      log(`⚠️ agent completed 取证失败:${spec.invocationId}:${error && error.message ? error.message : error}`)
    }
    return result
  } catch (error) {
    try {
      await emitBoundary(spec, 'AGENT_FAILED')
    } catch (traceError) {
      log(`⚠️ agent failed 取证失败:${spec.invocationId}:${traceError && traceError.message ? traceError.message : traceError}`)
    }
    throw error
  }
}
// StageResult 的 metrics 解包。2026-07-30 实跑事故:haiku 壳把整条 StageResult 记录**再包一层**
// 塞进 metrics(`{stage,status,metrics:{...整条记录含自己的 metrics...}}`)—— 外层三字段仍匹配
// STAGE_RESULT schema,校验照常放行,于是当时的 `g1.metrics.l4_budget` 静默变 undefined:
//   Math.min(10, undefined) = NaN → L3 prompt 写成「7~NaN 只」→ GATE2 `--budget NaN` 被 argparse 毙。
// 病灶是「schema 只锁外层形状,锁不住嵌套深度」。两种形状都接住:内层 metrics 优先,回退外层。
function stageMetrics(g) {
  const m = (g && g.metrics) || {}
  return (m.metrics && typeof m.metrics === 'object') ? m.metrics : m
}
// 业务门先保留原 stdout 供诊断，Workflow 只消费随后读取并验 hash/contract 的 StageResult。
function stageGate(label, cmd, stage, phaseName) {
  return agent(
    `依次执行:\`${cmd}; ${PY(stage, `${stage}-result-${label.toLowerCase()}`)} autoresearch.scan.stage_result show ${SD} ${stage}\`\n` +
    '前一条命令的 stdout 保留作诊断；把最后一行 StageResult JSON 原样作为结构化返回。\n' +
    '**逐字节原样执行:不得添加 2>&1、tee、管道,不得改写或增删任何重定向**' +
    '(混入 stderr 会污染这行 JSON)。**前台执行**:Bash 调用不要设 run_in_background(后台任务会在你交卷时被杀)。',
    { agentType: 'general-purpose', ...AG('gp_shell'), label,
      schema: STAGE_RESULT, ...(phaseName ? { phase: phaseName } : {}) })
}

// ── Phase Prelude ───────────────────────────────────────────────
phase('Prelude')
// frame 先行:pack 存盘 + 取数入湖(prelude/universe 随后湖命中不重拉)
log('Prelude 开始:frame → [universe 全市场取数 ∥ market_view](取数历史 ~10m,完成即 GATE1)')
await detached('frame-attempt-1', `mkdir -p ${SD} && ${PY('frame', 'frame-attempt-1')} autoresearch.scan.frame ${date} --json-out ${SD}/market_pack.json`, 'frame', 'Prelude')
// frame 与 universe 同样走 tushare 全市场取数,同样会 ChunkedEncodingError 半途而废 —— 但此前只有
// universe 有重试守卫(见下方 l2-check),frame 这条裸奔。事故两代:
//   2026-07-27:frame 在 11/12 端点断线退出码 1,`>` 重定向留下 **0 字节** pack;
//   2026-07-28:执行壳擅自把命令改写成 `... > pack 2>&1`,stderr 日志 + tqdm 进度条灌进 pack
//              (1,776B 无 JSON)—— **非空**,于是判据 `test -s` 放行、重试分支根本没触发。
// 两代同一病灶:产物由 shell 重定向 + 进程 stdout 决定。W8-1 收回 writer 侧(`--json-out`
// 先写 .tmp 再 os.replace),W8-2 把门判据从「非空」改成「JSON 可解析」——存在性 ≠ 有效性。
// 空/垃圾 pack 的下游代价照旧:macro-brief 会拒写(空壳比缺文件更坏 —— 文件一旦存在就压掉 L5 的
// render_fallback_pulse 回退,还经 l4_card.py 的 market_context_block 把无信息简报注入每张 L4 卡),
// 于是 market_view.md 缺席、L3 在没有地形段的情况下精排。同族前科:空 pickle 永不重拉 / 空 slim 默认 Hold。
const packok = await gate('pack-check',
  `${PYC('frame', 'pack-check-attempt-1')} "import json,sys;json.load(open('${SD}/market_pack.json'))" 2>/dev/null && echo '{"ok":true}' || echo '{"ok":false,"reason":"market_pack 缺失或非合法 JSON(frame 崩 / 产物被污染)"}'`,
  OK, 'Prelude')
if (!packok || !packok.ok) {
  log('⚠️ market_pack 缺失或非合法 JSON(frame 半途失败)→ 重试一次')
  await detached('frame-attempt-2', `${PY('frame', 'frame-attempt-2', 2)} autoresearch.scan.frame ${date} --json-out ${SD}/market_pack.json`, 'frame-retry', 'Prelude')
  const packok2 = await gate('pack-recheck',
    `${PYC('frame', 'pack-check-attempt-2', 2)} "import json,sys;json.load(open('${SD}/market_pack.json'))" 2>/dev/null && echo '{"ok":true}' || echo '{"ok":false,"reason":"重试后仍缺失/非法"}'`,
    OK, 'Prelude')
  // 不 throw:market_pack 是 B 级(缺了 L3 少地形段、L5 有确定性脉搏回退,持仓仍需当日卡)。
  // 但降级必须留痕 —— 这一行就是账,别让它再静默。
  if (!packok2 || !packok2.ok) {
    log('🚨 market_pack 重试后仍缺失/非法 → 本次 L3/L4 无市场地形段(B级降级·已记账);market_view 会拒写,L5 走确定性脉搏回退')
  } else {
    log('pack-check ✓(重试后)')
  }
}
// Wave10 A4:投影缺失必须被发现 —— 策略师现在只读它,缺了就是**静默无输入**(比缺 full pack
// 更隐蔽:pack-check 会绿,而 market_view 会写不出或凭空写)。缺则就地补投一次,仍缺则记账。
const spok = await gate('strategist-pack-check',
  `${PYC('frame', 'strategist-pack-check-attempt-1')} "import json,sys;d=json.load(open('${SD}/strategist_pack.json'));sys.exit(0 if d.get('pack') else 1)" 2>/dev/null && echo '{"ok":true}' || echo '{"ok":false,"reason":"strategist_pack 缺失/空投影"}'`,
  OK, 'Prelude')
if (!spok || !spok.ok) {
  await bash(`${PY('frame', 'strategist-pack-rebuild-attempt-1')} autoresearch.scan.strategist_pack ${SD}/market_pack.json -o ${SD}/strategist_pack.json`,
    'strategist-pack-rebuild', 'Prelude')
  const spok2 = await gate('strategist-pack-recheck',
    `${PYC('frame', 'strategist-pack-check-attempt-2', 2)} "import json,sys;d=json.load(open('${SD}/strategist_pack.json'));sys.exit(0 if d.get('pack') else 1)" 2>/dev/null && echo '{"ok":true}' || echo '{"ok":false,"reason":"补投后仍缺"}'`,
    OK, 'Prelude')
  log(spok2 && spok2.ok ? 'strategist-pack ✓(补投后)'
    : '🚨 strategist_pack 补投后仍缺 → market_view 无输入(B级降级·已记账);L5 走确定性脉搏回退')
}
// universe(确定性)∥ market_view(macro-lite 判断)—— barrier
await parallel([
  // W8-5:回显必须以**文件真在**为条件。原先 `prelude && echo SUMMARY_FILE=...` 只看 prelude
  // 退出码,07-28 汇总屏写盘失败(被 suppress 吞)时照样回显路径 → agent 回报「Summary file
  // generated」但文件不存在,CP1 转播落空。日志不得替不存在的文件背书。
  // 2026-09-26:回显改由 detach 的 expect_file(终态时现查文件)背书,仍以文件真在为条件。
  () => detached('prelude-attempt-1', `${PY('prelude', 'prelude-attempt-1')} autoresearch.scan.prelude ${date}`,
    'prelude/universe', 'Prelude', { expectFile: `${SD}/_prelude_summary.md` })
    .then((r) => {
      log(`prelude ${r.state}(exit=${r.exit_code})· ` +
        (r.expect_file ? `SUMMARY_FILE=${SD}/_prelude_summary.md` : 'SUMMARY_MISSING(见 stderr 的落盘失败行)'))
      return r
    }),
  // Wave10 A4:策略师只拿**投影**(`strategist_pack.json`),不给 full pack。
  // 此前防锚定写在这句 prompt 里(「pack 里的 sector_healthy_top3 …忽略它」)——
  // 一句叮嘱管着一份就摆在眼前的数据,07-30/31 连续两日复发。指令级约束的失败率不为零,
  // 数据级为零:看不见就写不出。投影由 `frame --json-out` 同步落盘,allowlist 之外的键
  // (含 sector_healthy_top3 / run_contract / user_config 与**将来任何新增键**)默认进不来。
  // full pack 仍是 L5 与 L3 数字 validator 的事实源,不受影响。
  () => tracedAgent(
    { stage: 'prelude', role: 'strategist', invocationId: 'strategist-market-1', attempt: 1 },
    `读 ${SD}/strategist_pack.json 的 pack 段,按你的人设写 ${SD}/market_view.md(六小节;前3描述性地形、后2仅 L5)。数字只出自该文件,不编;个股不评级、不锚定卡片。`,
    { agentType: 'macro-brief', ...AG('strategist'),
      label: 'market_view', phase: 'Prelude' }),
])
// universe 走 tushare 全市场取数,偶发 ChunkedEncodingError 半途而废(prelude 内 ✗ 但不阻断),
// 结果是 GATE1 在第 ~14 分钟毙掉整条流水线。进门前先探一次 L2,缺就重试一遍确定性前奏。
const l2ok = await gate('l2-check',
  `test -s ${SD}/L2_gbdt_top200.csv && echo '{"ok":true}' || echo '{"ok":false,"reason":"L2 缺失"}'`, OK, 'Prelude')
if (!l2ok || !l2ok.ok) {
  log('L2 缺失(universe 半途失败)→ 重试确定性前奏一次')
  const retry = await detached('prelude-attempt-2', `${PY('prelude', 'prelude-attempt-2', 2)} autoresearch.scan.prelude ${date} --skip consensus`,
    'prelude-retry', 'Prelude')
  log(`prelude 重试 ${retry.state}(exit=${retry.exit_code})${retry.reason ? ` · ${retry.reason}` : ''}`)
}
// 2026-09-13(GATE1 调度合并):契约校验 + 预算 + **运行模式判定**合成一次确定性调用。
// 这不只是省一个壳 —— 它把"判模式"收进 GATE1 这笔事务:gate 不过就根本不判模式,
// 模式判定/落盘失败则整道 GATE1 记 FAILED。旧形状里两者分家,run-mode 壳失败被
// `.catch(() => null)` 吞掉后由 JS 侧默认值补一个"看起来合理"的模式 —— 那正是拿默认值
// 替失败签字:哨兵日被补成 FULL = 白跑一趟全市场;全扫日被补成 SENTINEL_EMPTY = 当天啥也不跑。
const g1 = await stageGate('GATE1',
  `${PY('gate1', 'gate1-attempt-1')} autoresearch.scan.gates gate1 ${date} --decide-run-mode` +
  `${forceFull ? ' --force-full' : ''}`,
  'gate1', 'Prelude')
if (!g1 || !(g1.status === 'SUCCEEDED')) throw new Error(`GATE1 失败:${g1 ? g1.error : 'agent 无返回'}`)
const g1m = stageMetrics(g1)
log(`GATE1 ✓ sentinel=${g1m.sentinel_level} · L4预算=${g1m.l4_budget}`)
// CP1(Wave5 ①):bash 回报只有 stdout 末 15 行,而汇总屏是 12 步 ✓/✗ + 预热状态 + 当日件
// 建议行 + 下一步 —— 结构性放不下。指路文件,由主会话 Read 后全量转播给用户。
log(`📋 前奏汇总屏全文:${SD}/_prelude_summary.md(主会话 Read 后全量转播 —— 回报的末 15 行装不下 12 步屏)`)

// ── Wave10 A2:运行模式四态 —— 事实落 run_mode.json,下游**只读它**,不从 finalists 空否反推
// (「finalists 为空」可能是哨兵没选、L3 选空、或写盘失败,三者对读者意义完全不同)。
// 中间档 SENTINEL_PINNED 补的是此前的缺口:材料枯竭的日子里**持仓复核也一起没了**,
// 07-31 只能靠 force_full 手工拉满,代价是把全市场选股一并跑了($34.48/113min)。
const rm = g1m.run_mode || null
const runMode = rm && rm.mode
// 这里**没有** JS 侧兜底:run_mode 已是 GATE1 事务的一部分,SUCCEEDED 却拿不到四态之一,
// 只可能是 StageResult 被转述污染(2026-07-30 嵌套 metrics 前科)或代码版本不匹配。
// 与下方 l4_budget 的 NaN 守卫同款:宁可整条停,也不带着猜出来的模式往下跑。
if (!RUN_MODES.includes(runMode)) {
  throw new Error(`GATE1 通过却没给出可用的 run_mode(得到 ${JSON.stringify(runMode)})——` +
    `拒绝用默认值替它签字:这个模式决定跑不跑全市场、跑不跑持仓复核。` +
    `原始返回:${JSON.stringify(g1).slice(0, 400)}`)
}
log(`运行模式 = ${runMode}${rm.pinned_codes ? `(持仓 ${rm.pinned_codes.length} 只)` : ''}`)

if (runMode === 'SENTINEL_EMPTY') {
  log('哨兵档且无持仓 → 跳过 L3/L4(日历已在 prelude 跑过);assemble+GATE4 由主会话收尾')
  return { date, run_id: RUN_ID, engine: ENGINE, dispatch_attempt: 1, mode: 'sentinel', run_mode: runMode, finalists: 0, dispatch: [], meta: {},
    l4_budget: g1m.l4_budget, published: false }
}
if (runMode === 'SENTINEL_PINNED') {
  // 跳行业 brief、跳 L3、跳非持仓 L4;**持仓走完整 L4 链**(与 FULL 同一 l4-stock workflow、
  // 同 rubric、同双复核)。prompt 因明确缺 L3/判断型行业 brief 而不会字节相同 ——
  // 这个差异由 run_mode 与 `l3_judged=false` 公开,不伪装成等价上下文。
  const codes = (rm && rm.pinned_codes) || []
  log(`哨兵档·仅持仓复核:${codes.length} 只(${codes.join(',')})→ 跳 sector/L3/非持仓 L4`)
  await bash(`${PY('gate2', 'gate2-sentinel-attempt-1')} autoresearch.scan.gates gate2 ${date} --skip ${'sentinel_pinned_no_l3'}`,
    'GATE2-skip', 'L3').catch(() => null)
  await bash(`${PY('l4-prep', 'l4-prompts-pinned-attempt-1')} autoresearch.scan.agents.l4_card prompts ${date}`, 'l4-prep-pinned', 'L4-prep')
  return { date, run_id: RUN_ID, engine: ENGINE, dispatch_attempt: 1, mode: 'l4-handoff', run_mode: runMode, finalists: codes.length,
    dispatch: codes.map((c) => ({ code: c, lane: 'pinned' })), dispatch_batches: [codes],
    meta: {}, l4_budget: codes.length, published: false }
}
if (runMode === 'FORCED_FULL') {
  log('⚠️ 哨兵档被人工 override(force_full)→ 照常跑 L3/L4。诚实标注:确定性判据判「材料枯竭」,买单侧期望低。')
}

// ── Phase L3 ────────────────────────────────────────────────────
phase('L3')
// finalist tier 上限(plan 2026-07-12-l3-merge-plan.md Task 4):L3.5 闸的收窄职能已并入 L3,
// L3 直接出 7–10 只 finalist(宁缺毋滥,不强制凑到此数)——cap 而非目标。
// 守卫(2026-07-30 事故):NaN/undefined 曾一路无声流进 L3 prompt(「7~NaN 只」)与 GATE2
// `--budget NaN`。判断核心的指令被污染却没人喊 —— 这里 fail fast,宁可整条停也不带病判断。
const l4Budget = Number(g1m.l4_budget)
if (!Number.isInteger(l4Budget) || l4Budget <= 0) {
  throw new Error(`GATE1 未给出可用的 l4_budget(得到 ${JSON.stringify(g1m.l4_budget)})——` +
    `拒绝带 NaN 继续:它会污染 L3 prompt 与 GATE2 --budget。原始返回:${JSON.stringify(g1).slice(0, 400)}`)
}
// L4 卡数(2026-09-26 l4.max_cards):唯一算法在 Python(scan/l4/card_count.effective_caps),GATE1
// 回显;这里只读,不再 Math.min(10, …)。l3cap = L3 finalist tier 上限(进 L3 prompt 区间与
// l3_select --budget);maxCards = 非📌 卡总上限(含 composite 席位)—— GATE2 数的正是非豁免
// lane 的全部行(席位也算),所以 GATE2 预算是 maxCards 而不是 l3cap。
const l3cap = Number(g1m.l3cap)
const maxCards = Number(g1m.max_cards)
if (!Number.isInteger(l3cap) || l3cap <= 0 || !Number.isInteger(maxCards) || maxCards <= 0) {
  throw new Error(`GATE1 未给出可用的 l3cap/max_cards(得到 ${JSON.stringify({ l3cap: g1m.l3cap, max_cards: g1m.max_cards })})` +
    `——升级后的 gates.gate1 必回显它们;拒绝猜卡数继续。`)
}
const l3lo = Math.min(7, l3cap)
log(`GATE1 卡数 · L4预算=${l4Budget} · 卡上限 max_cards=${maxCards} → l3cap=${l3cap}${g1m.budget_flags === false ? '(五面旗不参与)' : ''}`)
// 中观行业 pack(确定性)先行,再 [sector-briefs ∥ L3 表准备] barrier。sector-pack + 待写清单
// 合并一个 gate(壳合并①,-1 spawn):schema 顶层必须是 object(API 拒 `type:'array'` → 400 →
// agent 返回 null → `|| []` 静默吞掉,结果是一份行业 brief 都不写、L3 在没有行业地形段的情况下
// 精排。2026-07-09 实跑逮到。
const SECTORS = { type: 'object', required: ['ok', 'sectors'],
  properties: { ok: { type: 'boolean' }, sectors: { type: 'array', items: { type: 'string' } } } }
const sectorsRes = await gate('sector-pack+list',
  `${PY('l3', 'sector-reuse-attempt-1')} autoresearch.sector.reuse ${date} --apply; ` +
  `${PY('l3', 'sector-pack-attempt-1')} autoresearch.sector.pack ${date}; ` +
  `${PYC('l3', 'sector-list-attempt-1')} "import json,glob,os;d='${CTX}/sector/${date}';b='${SD}/sector_briefs';` +
  `print(json.dumps({'ok':True,'sectors':sorted(os.path.splitext(os.path.basename(p))[0] ` +
  `for p in glob.glob(d+'/*.json') if not os.path.exists(os.path.join(b,os.path.splitext(os.path.basename(p))[0]+'.md')))}))"`,
  SECTORS, 'L3')
if (!sectorsRes) throw new Error('sector-pack+list 无返回(schema/API 失败)—— 不静默降级为"无行业 brief"')
const sectors = sectorsRes.sectors || []
// Wave10 B4:`sector_brief_mode=finalist_only` 已退役 —— 它让 L3 看不到判断型行业 brief、
// **可能改变 finalists**,按「性能开关不拥有评级」铁律它不是性能开关;而它从未有获批的
// research experiment 来证明评级等价(§B4)。现在只有 `all` 这一条路。
const preL3BriefSectors = sectors
log(`待写行业 brief:${sectors.length} 个${sectors.length ? ` (${sectors.join('、')})` : '(全部 TTL 复用)'}`)
await parallel([
  () => detached('l3-prepare-attempt-1', `${PY('l3', 'l3-prepare-attempt-1')} autoresearch.scan.agents.l3_select prepare ${date}`, 'l3-prepare', 'L3'),
  // invocation id 用**当日行业清单里的序号**:行业名是中文,JS 侧没有 sha256 可用,
  // 而事件 subject 的 ASCII key 由 python 从 --subject-display 派生。序号在一次 run 内
  // 唯一且确定;index 里显示的仍是行业原名(payload.subject_display)。
  ...preL3BriefSectors.map((sec, i) => () => tracedAgent(
    { stage: 'l3', role: 'sector-brief', subjectDisplay: sec,
      invocationId: `sector-brief-${i + 1}-1`, attempt: 1 },
    `你是行业分析师。读 ${CTX}/sector/${date}/${sec}.json 写 ${SD}/sector_briefs/${sec}.md,单段机器契约(## 地形段 喂 L3/L4;纯事实性,不含方向判断)。零新取数。`,
    { agentType: 'sector-brief', ...AG('sector_brief'),
      label: `brief:${sec}`, phase: 'L3' })
    .then((r) => { log(`brief ✓ ${sec}`); return r })),
])
// L3 holistic 精排(唯一 max-effort 判断核心)
log(`L3 精排开始:pass1 已分诊 200→~40(影子 _l3_pass1_cut.csv),l3-rank 深比较出 finalist tier ${l3lo}~${l3cap} 只+bench(effort max,历史 60行~14-25m,40行待测)`)
await tracedAgent(
  { stage: 'l3', role: 'l3-rank', invocationId: 'l3-rank-market-1', attempt: 1 },
  `L3 精排 · 日期 ${date} · finalist tier 按质 ${l3lo}~${l3cap} 只(judged 每元素带 finalist:true/false)+其余为 bench;宁缺毋滥。文件在 ${SD}/:_l3_table.md(~40 表,pass1 已分诊)、market_view.md(§1-3 地形)、sector_briefs/(地形段)。按你的人设(6 维 rubric + 硬约束 A-E)比较式精排,写 ${SD}/_l3_judged.json。`,
  { agentType: 'l3-rank', ...AG('l3_rank'),
    label: 'L3-rank', phase: 'L3' })
// thesis 数字机检(确定性 lint):打回一次自修,修复后不再二检(防循环)
const l3lint = await gate('l3-lint', `${PY('l3', 'l3-lint-attempt-1')} autoresearch.scan.agents.l3_select lint ${date}`, OK, 'L3')
if (l3lint && l3lint.ok === false) {
  log(`L3 数字机检未过 → 打回一次自修:${(l3lint.reason || '').slice(0, 200)}`)
  // Wave7 B′-e:自修是**可选增益**,不是流水线的必经关节 —— 它挂了不该让人以为它跑过了。
  // 2026-07-27 实跑该 agent 死于 `API Error: Connection closed mid-response`,journal 里
  // 只留一条 started 没有 result,workflow 若无其事地继续,56.9k 加权白烧且无人知晓;
  // 我是靠事后翻 <failures> 才发现的。这里显式接住:失败 → 说出来 → 带着未修的 judged 继续
  // (下游 GATE2/finalists 读的是 judged 本身,自修没跑只是数字措辞没优化,不影响正确性)。
  const REPAIR = { type: 'object', required: ['ok', 'codes', 'n', 'prompt'],
    properties: { ok: { type: 'boolean' }, codes: { type: 'array', items: { type: 'string' } },
      n: { type: 'integer' }, prompt: { type: 'string' } } }
  const repair = await gate('l3-repair-pack',
    `${PY('l3', 'l3-repair-pack-attempt-1')} autoresearch.scan.agents.l3_select repair-pack ${date}`, REPAIR, 'L3')
  // Wave12-T34 归因契约(改这段前先读):`l3_repair` 复用 `agentType: 'l3-rank'` 派发,
  // harvest 分不清一行是主排还是自修。归因靠的是**产物**——`repair-pack` 在派发之前写下的
  // `_l3_repair_prompt.md` 在场即"本日派过一次 l3_repair"
  // (`autoresearch.trace.usage_reconcile.dispatch_census`)。
  // 故意不让这个 agent 自己写标记:2026-07-27 它死于 `Connection closed mid-response`,
  // 烧掉 56.9k 加权却没留下任何自报记录 —— 让"它自己承认跑过"当唯一事实源,恰好会在
  // 它死掉时丢掉那一行的归属,而那正是最需要看清成本的时刻。
  // ⚠️ 若以后改成"prompt 不在场也可能派发"或"n==0 也写 prompt",必须同步改 dispatch_census。
  const fix = repair && repair.n > 0 ? await tracedAgent(
    { stage: 'l3', role: 'l3-repair', invocationId: 'l3-repair-market-1', attempt: 1 },
    `Read ${SD}/_l3_repair_prompt.md，只处理其中列出的失败票；按文件内 schema 用 Write 写 ${SD}/_l3_repair_patch.json。不要读取任何全量 L3 输入或输出文件。`,
    { agentType: 'l3-rank', ...AG('l3_repair'), label: 'L3-lint-fix', phase: 'L3' })
    .catch((e) => { log(`⚠️ L3 自修 agent 异常:${e && e.message ? e.message : e}`); return null }) : null
  if (fix) {
    const APPLY = { type: 'object', required: ['ok', 'patched', 'preserved', 'codes'],
      properties: { ok: { type: 'boolean' }, patched: { type: 'integer' },
        preserved: { type: 'integer' }, codes: { type: 'array', items: { type: 'string' } } } }
    const applied = await gate('l3-repair-apply',
      `${PY('l3', 'l3-repair-apply-attempt-1')} autoresearch.scan.agents.l3_select apply-repair ${date}`, APPLY, 'L3')
    if (applied && applied.ok) log(`L3 局部修复 ✓ ${applied.patched} 票· 原样保留 ${applied.preserved} 票`)
    else log('⚠️ L3 局部修复 patch 校验/merge 未完成—— 带原 judged 继续')
  } else {
    log('⚠️ L3 数字自修未完成(agent 无返回/断连)—— 带未修 judged 继续,machine-lint 结论已记在上一行')
  }
}
// 确定性写 finalists(修前导零)+ GATE2,合并一个 gate(壳合并②,-1 spawn)
const g2 = await stageGate('GATE2',
  `${PY('l3', 'l3-finalists-attempt-1')} autoresearch.scan.agents.l3_select finalists ${date} --budget ${l3cap} && ` +
  `${PY('gate2', 'gate2-attempt-1')} autoresearch.scan.gates gate2 ${date} --budget ${maxCards}`, 'gate2', 'L3')
if (!g2 || !(g2.status === 'SUCCEEDED')) throw new Error(`GATE2 失败:${g2 ? g2.error : 'no return'}`)
const g2m = stageMetrics(g2)   // 同 g1:haiku 壳可能多包一层,见 stageMetrics 注释
// L3.5 闸已完全移除(2026-07-12 用户裁定"直接 L3 输出"):L3 finalist tier 即 L4 入选集。
// CP3(Wave5 ①):整条漏斗最高光的一刻是"选出了哪几只",而不是"选出了几只"。
// g2.meta 早就带着 name/sector(gates.py:95),此前被整段扔掉。
log(`GATE2 ✓ finalists=${g2m.n}`)
const fmeta = g2m.meta || {}
;(g2m.finalists || []).forEach((c, i) => {
  const m = fmeta[c] || {}
  log(`  L3入围 ${i + 1}/${g2m.n} ${c} ${m.name || ''}${m.sector ? `(${m.sector})` : ''}`)
})

// ── Phase L4-prep ───────────────────────────────────────────────
// (fb_20260714_003)决策卡与活体情报不再在本 workflow 内派发:每股一个独立 l4-stock workflow
// (.claude/workflows/l4-stock.js,intel→card→OW复核 股内链式衔接),由主会话在本 workflow 返回后
// 一条消息 N 个 Workflow 调用并行拉起——每股独立并发帽真并行、单股失败只废单股(2026-07-14
// GATE3 差 16 字节毙掉 60min/1.6M token 全流水线的教训)。本 workflow 到 dispatch 交接为止,
// assemble+GATE4 也随之上移主会话收尾。
phase('L4-prep')
log(`L4-prep:[四生产者并行]→prompts→${streamingL4 ? '单票 slim∥intel 流式交接' : '批量 slim legacy 交接'}`)
await detached('l4-prep-attempt-1',
  // shared 必须先于 prompts:_l4_shared_instructions.md 此前全仓无生产者(只有读者),
  // 当日 📐/🔁/🚪 校准行从未到达任何一张决策卡(Wave5 ④B)。
  // TTL 复用(l4_reuse --apply)已于 2026-07-29 退役(用户裁定 R5「不要任何复用」)——
  // 复用票不跑 intel、新闻冻在源卡日,评级稳定性改由昨卡回声承接(l4/prompts.py)。
  `${PY('l4-prep', 'l4-shared-attempt-1')} autoresearch.scan.agents.l4_card shared ${date}; ` +
  `( ${PY('l4-prep', 'l4-pledge-attempt-1')} autoresearch.scan.agents.l4_card pledge ${date} || true ) & ` +
  `( ${PY('l4-prep', 'l4-seats-attempt-1')} autoresearch.scan.agents.l4_card seats ${date} || true ) & ` +
  `( ${PY('l4-prep', 'l4-calendar-attempt-1')} autoresearch.scan.calendar ${date} || true ) & ` +
  `( ${PY('l4-prep', 'l4-consensus-attempt-1')} autoresearch.scan.agents.l4_card consensus ${date} || true ) & ` +
  `wait`, 'l4-prep', 'L4-prep')
const PLAN = { type: 'object', required: ['dispatch'],
  properties: { dispatch: { type: 'array', items: { type: 'string' } },
    meta: { type: 'object' } } }
const plan = await gate('dispatch-plan', `${PY('l4-prep', 'l4-dispatch-plan-attempt-1')} autoresearch.scan.agents.l4_card dispatch-plan ${date}`, PLAN, 'L4-prep')
if (!plan) throw new Error('dispatch-plan 无返回')
let dispatch = plan.dispatch
let dispatchBatches = dispatch.length ? [dispatch] : []
let taskBook = null
if (!streamingL4) {
  // 回滚路径保持旧批量 GATE3：先等所有 slim，再交接股票 workflow。
  const G3 = { type: 'object', required: ['ok'],
    properties: { ok: { type: 'boolean' }, reason: { type: 'string' },
      failures: { type: 'array', items: { type: 'object',
        properties: { ticker: { type: 'string' }, bytes: { type: 'integer' }, why: { type: 'string' } } } } } }
  const g3 = await gate('GATE3',
    `${PY('l4-prep', 'l4-prompts-batch-attempt-1')} autoresearch.scan.agents.l4_card prompts ${date} && ` +
    `${PY('l4-prep', 'l4-harvest-slim-attempt-1')} autoresearch.scan.agents.l4_card harvest-slim ${date}`,
    G3, 'L4-prep')
  if (!g3) throw new Error('GATE3 无返回')
  if (!g3.ok) {
    const bad = new Set((g3.failures || []).map((f) => String(f.ticker || '').slice(0, 6)))
    dispatch = dispatch.filter((c) => !bad.has(c))
    log(`⚠️ GATE3:${bad.size} 股 slim 不合格被剔除(${[...bad].join('/') || '?'})—— ${(g3.failures || []).map((f) => `${f.ticker}:${f.why}`).join('; ') || g3.reason || ''}`)
    if (!dispatch.length) throw new Error(`GATE3 失败:全部 slim 不合格 —— ${g3.reason || ''}`)
  } else {
    log('GATE3 ✓ 全 slim 结构+内容合格')
  }
  dispatchBatches = dispatch.length ? [dispatch] : []
} else {
  const TASKS = { type: 'object', required: ['ok', 'path', 'dispatch_batches'],
    properties: { ok: { type: 'boolean' }, path: { type: 'string' },
      effective_cap: { type: 'integer' },
      dispatch_batches: { type: 'array', items: { type: 'array', items: { type: 'string' } } },
      // I-2(2026-08-10 终审必修):C1a 拒绝路径(prompts 缺失)靠这两个键把 python 明确的
      // 拒绝理由带到人眼前。不在 properties 声明过的键,结构化输出按 schema 归一时有被
      // 丢掉的现实可能——一旦丢,下面 throw 读到的 tasks.reason 是 undefined,C1a 拦下
      // 缺 prompt 的那一刻,人看到的会是"agent 无返回",而不是"缺哪票"。不放进 required:
      // 成功路径(ok:true)不带这两个键。
      reason: { type: 'string' },
      missing_prompts: { type: 'array', items: { type: 'string' } } } }
  const tasks = await gate('l4-tasks-init',
    `${PY('l4-prep', 'l4-prompts-streaming-attempt-1')} autoresearch.scan.agents.l4_card prompts ${date} && ` +
    `${PY('l4-prep', 'l4-tasks-init-attempt-1')} autoresearch.scan.l4_tasks init ${date}`,
    TASKS, 'L4-prep')
  if (!tasks || !tasks.ok) throw new Error(
    `L4 task book 初始化失败:${tasks
      ? (tasks.reason || 'ok:false 但未带 reason(检查 prompts 是否非 0 退出)')
      : 'agent 无返回'}` +
    `${tasks && tasks.missing_prompts ? ` missing=${tasks.missing_prompts.join('/')}` : ''}`)
  taskBook = tasks.path
  dispatchBatches = tasks.dispatch_batches || []
  log(`L4 流式任务簿 ✓ ${taskBook} · 批次宽度 ${tasks.effective_cap || '?'} · ${dispatchBatches.length} 批`)
}
log(`L4 交接:新派 ${dispatch.length} 股(每股一个 l4-stock workflow,主会话并行拉起；必须传 args.attempt=1)`)
// CP4(Wave5 ①):随时可调的确定性看板,不用等一小时后的 summary.md
log(`🔎 随时可调:\`${PY('observe', 'render-menu-health-attempt-1')} autoresearch.scan.render ${date} --view menu_health\`(L2 成色)· ` +
  `\`${PY('observe', 'render-gate-hist-attempt-1')} autoresearch.scan.render ${date} --view gate_hist\`(L4 完成后看评级分布/停因分桶/门柱)· ` +
  `\`${PY('observe', 'render-timing-attempt-1')} autoresearch.scan.render ${date} --view timing\`(分段耗时)`)
// 📌 保送票在派发那一秒必须可见:07-21 漏传 args.pinned → 300857/601869 的持仓 SELL 双复核
// 整段没跑(self_review 探针 9 sell_review_missing 只能事后 warn,拦不住)。
const metaAll = plan.meta || g2m.meta || {}
const pinnedCodes = dispatch.filter((c) => metaAll[c] && metaAll[c].pinned)
if (pinnedCodes.length) {
  log(`📌 保送票 ${pinnedCodes.length} 只:${pinnedCodes.join('/')} —— 派发这些 l4-stock 必须传 args.pinned:true(漏传=持仓 SELL 双复核断链)`)
} else {
  log('📌 保送票 0 只(本次 dispatch 无 pinned)')
}

// meta(名称/行业)透传给 l4-stock 的 intel 盲搜 prompt;assemble+GATE4 由主会话在全部 l4-stock 完成后收尾。
return { date, run_id: RUN_ID, engine: ENGINE, dispatch_attempt: 1, mode: 'l4-handoff', finalists: g2m.n, dispatch, dispatch_batches: dispatchBatches,
  task_book: taskBook, streaming_l4: streamingL4,
  meta: plan.meta || g2m.meta || {}, l4_budget: g1m.l4_budget, published: false }

}

try {
  return await __main()
} catch (error) {
  const detail = JSON.stringify({
    error_type: (error && error.name) || 'Error',
    message: String((error && error.message) || error).slice(0, 500),
    stage: 'workflow',
  }).replace(/'/g, '')
  try {
    await agent(
      `执行:\`AUTORESEARCH_ENGINE=${ENGINE} AUTORESEARCH_RUN_ID=${RUN_ID} ` +
        `uv run --no-sync python -m autoresearch.trace.exec_capture --run-id ${RUN_ID} ` +
        `--stage finalize --invocation-id finalize-failed-${RUN_ID} --attempt 1 ` +
        `-- uv run --no-sync python -m ` +
        `autoresearch.trace.capsule finalize ${RUN_ID} --business-status FAILED ` +
        `--error-json '${detail}'\`。把 stdout 最后一行 JSON 原样作为结构化返回。` +
        '**逐字节原样执行:不得添加 2>&1、tee、管道,不得改写或增删任何重定向。**',
      { agentType: 'general-purpose', label: 'capsule:finalize-failed' })
  } catch (traceError) {
    // 冻结失败要说出来,但绝不能盖住原始异常 —— 那才是这次 run 死掉的真正原因。
    log(`⚠️ 失败冻结未完成:${traceError && traceError.message ? traceError.message : traceError}`)
  }
  throw error
}
