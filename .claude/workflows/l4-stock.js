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
const pinned = !!A.pinned   // dispatch-plan meta 透传;缺省 false = 现行为(parity)
const dossierSummary = String(A.dossierSummary || '').trim()   // dispatch-plan meta 透传;缺省空 = parity(M-2:全函数防御,同款 !!A.pinned)
const SD = `context/scan/${date}`
const R = 'uv run --no-sync python -m'
const TASK_BOOK = `${SD}/_l4_tasks.json`
const CARD = { type: 'object', required: ['code', 'rating'],
  properties: { code: { type: 'string' }, rating: { type: 'string' },
    conviction: { type: 'number' }, proposal: { type: 'string' } } }
const recordL4 = (errorCode = null) => agent(
  `在仓库根目录执行:\`${R} autoresearch.scan.stock_stage l4 ${date} ${code}` +
  `${errorCode ? ` --error ${errorCode}` : ''}\`。只回报退出码,不要判断或解释。`,
  { agentType: 'general-purpose', model: 'haiku', effort: 'low', label: `stage:${code}` })
  .catch((e) => { log(`⚠️ L4 StageResult 写入失败:${e && e.message ? e.message : e}`); return null })
const taskGate = (subcommand, schema, label) => agent(
  `执行:\`if test -s ${TASK_BOOK}; then ${R} autoresearch.scan.l4_tasks ${subcommand}; ` +
  `else echo '{"ok":true,"action":"LEGACY"}'; fi\`\n` +
  '把 stdout 最后一行 JSON 原样作为结构化返回；不要判断或增删字段。',
  { agentType: 'general-purpose', model: 'haiku', effort: 'low', label, schema })
const TASK_ACTION = { type: 'object', required: ['ok', 'action'],
  properties: { ok: { type: 'boolean' }, action: { type: 'string' },
    attempt: { type: 'integer' }, reason: { type: 'string' } } }
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

// 任务簿存在时先领取本票；直接单独调用旧 workflow 时走 LEGACY，不强迫历史调用者补状态文件。
const taskPreflight = await taskGate(
  `preflight ${code} ${date}`,
  TASK_ACTION,
  `task-preflight:${code}`,
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
await parallel([
  () => taskGate(`prepare ${code} ${date}`, TASK_RESULT, `slim:${code}`)
    .then((r) => { slimResult = r; return r }),
  ...(intelOn ? [() => agent(
    `活体情报采集:${code} ${name}(${sector})· 分析日 ${date}。按你的人设六面全查(≤${maxQ} 条),写 ${SD}/_l4_intel_${code}.md;返回 code 与事件行数 events。${knownBase}`,
    { agentType: 'l4-intel', effort: cfg.agents?.l4_intel?.effort ?? 'max',
      ...(cfg.agents?.l4_intel?.model ? { model: cfg.agents.l4_intel.model } : {}),
      label: `intel:${code}`, phase: 'Intel', schema: INTEL })
    .then((r) => { intelResult = r; return r })
    .catch((e) => { log(`🕵️ intel ✗ ${code}:${e && e.message ? e.message : e}(卡自动回退卡内网查)`); return null })] : []),
])
if (!intelOn) {
  log(`intel 关(config l4_intel.enabled=false)→ 直接出卡`)
} else {
  log(intelResult ? `🕵️ intel ✓ ${code}(events=${intelResult.events ?? '?'})` : `🕵️ intel ✗ ${code}(缺稿,卡自动回退卡内网查)`)
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
    { agentType: 'l4-card', effort: cfg.agents?.l4_card?.effort ?? 'xhigh',
      ...(cfg.agents?.l4_card?.model ? { model: cfg.agents.l4_card.model } : {}),
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
    { agentType: 'l4-card', effort: cfg.agents?.l4_card?.effort ?? 'xhigh',
      label: `ens${i}:${code}`, phase: 'Verify', schema: CARD })
  // Wave6 T2 同档早止:run1==run2 时三票中位**数学上已定**(两票同档 → 排序中位恒为该档,
  // 第三票投什么都改不了),run3 是确定的冗余 → 跳过省一张满卡(07-24 的 601869 三票全 UW)。
  // 分歧则照常跑 run3 当裁决票。代价:串行化后分歧场景墙钟略长,同档场景反而更短。
  const r2 = await rerun(2)
  const sameTier = !!r2 && tier(r2.rating) === tier(card.rating)
  const r3 = sameTier ? null : await rerun(3)
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
  const rec = { code, ratings, median: names[medianTier],
    spread: sorted[sorted.length - 1] - sorted[0], degraded, trigger,
    n_runs: ratings.length, early_stopped: earlyStopped }
  await agent(
    `在仓库根目录精确执行下面这条命令,然后只回报退出码。不要做别的、不要判断。\n\n\`\`\`\ncat > ${SD}/_ensemble_${code}.json << 'EOF'\n${JSON.stringify(rec)}\nEOF\n\`\`\``,
    // Wave6 T1:heredoc 写文件,零判断
    { agentType: 'general-purpose', model: 'haiku', effort: 'low', label: `ens-dump:${code}`, phase: 'Verify' })
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
