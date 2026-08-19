export const meta = {
  name: 'l4-stock',
  description: '单只 finalist 的 L4 全链:活体情报盲搜 → 决策卡 → (≥OW)双复核折回;每股一个 workflow、N 股并行(fb_20260714_003)',
  phases: [
    { title: 'Intel', detail: 'l4-intel 六面盲搜(config 可关;失败不阻断,卡回退卡内网查)' },
    { title: 'Card', detail: 'l4-card 渐进深度 DD + 早停,写 details/<code>.md' },
    { title: 'Verify', detail: '≥OW → 2 独立复核 run 取中位,只向下折回 → _ensemble_<code>.json' },
  ],
}

// args: {date, code, name, sector, cfg} —— cfg 透传 scan_config 的 agents/l4_intel 块(缺省 = 现硬编码值,parity)。
// 为什么每股一个 workflow(而非 scan-market.js 内批量派发):①每个 workflow 有独立并发帽,N 股真并行;
// ②intel→card 在股内链式衔接,股间零 barrier(旧批量版全体 intel 完才派卡);③单股失败只废单股,
// 主会话对该股单独重跑即可 —— 2026-07-14 GATE3 差 16 字节毙掉 60min/1.6M token 全流水线的教训。
const A = (typeof args === 'string' && args ? JSON.parse(args) : args) || {}
const { date, code } = A
if (!date || !code) throw new Error('args.date/args.code 必填,如 {date:"2026-07-14", code:"000651"}')
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
const SD = `context_${ENGINE}/scan/${date}`
const R = 'uv run --no-sync python -m'
const TASK_BOOK = `${SD}/_l4_tasks.json`
const CARD = { type: 'object', required: ['code', 'rating'],
  properties: { code: { type: 'string' }, rating: { type: 'string' },
    conviction: { type: 'number' }, proposal: { type: 'string' } } }
const recordL4 = (errorCode = null) => agent(
  `在仓库根目录执行:\`${R} autoresearch.scan.stock_stage l4 ${date} ${code}` +
  `${errorCode ? ` --error ${errorCode}` : ''}\`。只回报退出码,不要判断或解释。` +
  `**逐字节原样执行:不得添加 2>&1、tee、管道,不得改写或增删任何重定向。**`,
  { agentType: 'general-purpose', ...AG('gp_shell'), label: `stage:${code}` })
  .catch((e) => { log(`⚠️ L4 StageResult 写入失败:${e && e.message ? e.message : e}`); return null })
// 🚨 2026-08-05 事故(同族,见 scan-market.js:35 注释):`prepare` 子命令内含单票 slim 取数,
// 可能跑数分钟 → harness 转后台 → haiku 壳判定"卡住"并 pkill 生产作业。同样两条药:
// 显式告知耗时 + 禁杀纪律,model 升 sonnet(每票仅 1 次调用,代价可忽略)。
const taskGate = (subcommand, schema, label) => agent(
  `执行:\`if test -s ${TASK_BOOK}; then ${R} autoresearch.scan.l4_tasks ${subcommand}; ` +
  `else echo '{"ok":true,"action":"LEGACY"}'; fi\`\n` +
  '把 stdout 最后一行 JSON 原样作为结构化返回；不要判断或增删字段。' +
  '**逐字节原样执行:不得添加 2>&1、tee、管道,不得改写或增删任何重定向。**\n' +
  '⏳ 这条命令可能跑数分钟(单票取数)。**绝对不许 kill / pkill / 中断 / 重启**它 —— ' +
  '它没卡住,它在取数;被 harness 转后台就安静等完成通知。拿不到退出码就如实回报,不要自己"修"。',
  { agentType: 'general-purpose', ...AG('gp_shell_json'), label, schema })
// 通用确定性 CLI 壳:跑一条命令、把它打印的最后一行 JSON 原样带回(零判断)。
const gpJson = (cmd, label, schema) => agent(
  `执行:\`${cmd}\`\n它会向 stdout 打印一行 JSON。把最后一行 JSON 原样作为结构化返回,` +
  '不改、不增删字段。**逐字节原样执行:不得添加 2>&1、tee、管道,不得改写或增删任何重定向。**',
  { agentType: 'general-purpose', ...AG('gp_shell_json'), label, schema })
// 确定性命令壳:跑一条命令、只回报退出码 + stdout 末 15 行(零判断)。
// 2026-08-03 事故(与 scan-market.js 的 gpJson 同族、同一个提交 99efe7d):Wave10 A5 把
// intel_status 的**调用点**写进本文件(L160),却没带上这份定义 —— 每只票都会在 Intel 相位
// 之后同步抛 `bash is not defined`,`.catch(() => null)` 接不住(ReferenceError 在 promise
// 生成前就抛了),结果是一张决策卡都出不来。与 scan-market.js:35 的 bash() 同语义、同签名。
const bash = (cmd, label, phaseName) => agent(
  '在仓库根目录精确执行下面这条命令,然后只回报:退出码 + stdout 末 15 行。' +
  '不要做别的、不要判断、不要解释。\n' +
  '**逐字节原样执行:不得添加 2>&1、tee、管道,不得改写或增删任何重定向。**\n' +
  '⏳ 命令可能跑数分钟。**绝对不许 kill / pkill / 中断 / 重启**它(2026-08-05 事故:' +
  '壳 pkill 了生产作业两次,整条流水线被毙)。被转后台就安静等完成通知。\n\n' +
  `\`\`\`\n${cmd}\n\`\`\``,
  { agentType: 'general-purpose', ...AG('gp_shell'), label,
    ...(phaseName ? { phase: phaseName } : {}) })
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
  `failure ${code} ${date} --error-class ${errorClass}`,
  TASK_RESULT,
  `task-failure:${code}`,
).catch(() => null)

// C1b(2026-08-10):bookless 的 LEGACY 分支移入 python(壳零判断)——preflight 现在
// 无论有无任务簿都能回答,且缺 prompt 一律 BLOCKED(盲卡在这里绝育)。
const taskPreflight = await gpJson(
  `${R} autoresearch.scan.l4_tasks preflight ${code} ${date}`,
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
const intelResume = !!(taskPreflight && taskPreflight.intel_resume)

// ── Slim ∥ Intel(结构性盲:prompt 只给码/名/行业/日期,防确认偏误)────────────
phase('Intel')
const intelOn = !!(cfg.l4_intel && cfg.l4_intel.enabled)
const maxQ = (cfg.l4_intel && cfg.l4_intel.max_queries) ?? 15
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
const TRANSIENT = ['RATE_LIMIT', 'CONNECTION', 'TIMEOUT', 'ENOTFOUND']
const errClass = (e) => {
  const m = String((e && e.message) || e || '').toUpperCase()
  return TRANSIENT.find((t) => m.includes(t)) || 'OTHER'
}
async function intelLeg() {
  for (let i = 1; i <= 3; i++) {
    intelAttempts = i
    try {
      return await agent(
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
await parallel([
  () => taskGate(`prepare ${code} ${date}`, TASK_RESULT, `slim:${code}`)
    .then((r) => { slimResult = r; return r }),
  ...(intelOn && !intelResume ? [() => intelLeg().then((r) => { intelResult = r; return r })] : []),
])
if (!intelOn) {
  log(`intel 关(config l4_intel.enabled=false)→ 直接出卡`)
  // 复核修复轮1(2026-08-10 Important):这条落盘不能只在"跑过 intel"的分支才做——DISABLED
  // 是三态之一(vs DEGRADED/缺状态),`--disabled` 这个 flag 存在的唯一理由就是让报告侧分清
  // "情报面被主动关掉"与"情报面出事了/根本没有"。值与迁移前逐字节一致:本分支从不派 intelLeg,
  // intelAttempts 恒 0、intelResult/intelError 恒 null → 只带 --normalize --disabled。
  await bash(
    `${R} autoresearch.scan.l4.intel_status ${date} ${code} --normalize` +
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
      `${R} autoresearch.scan.l4.intel_guard ${date} ${code}`,
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
    `${R} autoresearch.scan.l4.intel_status ${date} ${code} --normalize` +
    `${intelOn ? '' : ' --disabled'}${intelAttempts > 1 ? ` --attempts ${intelAttempts}` : ''}` +
    `${intelResult ? '' : (intelError ? ` --error-class ${intelError}` : '')}`,
    `intel-status:${code}`, 'Intel').catch(() => null)
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
  card = await agent(
    `执行 ${SD}/_l4_prompt_${code}.md:先读整个任务包,再按其指令做渐进深度 DD + 早停,写决策卡到 ${SD}/details/${code}.md。最后返回该卡最终五档评级与 FINAL 行(code / rating / conviction / proposal=FINAL TRANSACTION PROPOSAL 的值,如 "SELL")。`,
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
const isOW = (r) => /(overweight|\bbuy\b|增持|买入)/i.test(r || '')
const isSellish = (card) => /sell/i.test(card.rating || '') || /sell/i.test(card.proposal || '')
const trigger = isOW(card.rating) ? 'ow_review' : (pinned && isSellish(card) ? 'sell_review' : null)
let final = card.rating
if (trigger) {
  phase('Verify')
  log(trigger === 'ow_review'
    ? `🎭 买单复核:${code} 追加 2 独立 run 取中位(只向下折回)`
    : `🎭 持仓卖出复核:${code} 追加 2 独立 run 取中位(只向温和折回,卖错持仓代价不对称)`)
  const RANK = { 'sell': 0, 'underweight': 1, 'hold': 2, 'overweight': 3, 'buy': 4 }
  const tier = (r) => RANK[String(r || '').toLowerCase()] ?? 2
  const rerun = (i) => agent(
    `独立复核 run${i}(不知道其它 run 结论):执行 ${SD}/_l4_prompt_${code}.md 的任务包,按人设走渐进深度 DD,决策卡写到 ${SD}/ensemble/${code}.run${i}.md(先自行创建 ensemble/ 目录),返回 code/rating/conviction/proposal。`,
    { agentType: 'l4-card', ...AG('ens_review'),
      label: `ens${i}:${code}`, phase: 'Verify', schema: CARD })
  // Wave6 T2 同档早止:run1==run2 时三票中位**数学上已定**(两票同档 → 排序中位恒为该档,
  // 第三票投什么都改不了),run3 是确定的冗余 → 跳过省一张满卡(07-24 的 601869 三票全 UW)。
  // 分歧则照常跑 run3 当裁决票。代价:串行化后分歧场景墙钟略长,同档场景反而更短。
  const r2 = await rerun(2)
  const sameTier = !!r2 && tier(r2.rating) === tier(card.rating)
  const r3 = sameTier ? null : await rerun(3)
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
  await agent(
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
  `success ${code} ${date}`,
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
