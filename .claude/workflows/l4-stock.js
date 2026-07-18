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
const SD = `context/scan/${date}`
const CARD = { type: 'object', required: ['code', 'rating'],
  properties: { code: { type: 'string' }, rating: { type: 'string' }, conviction: { type: 'number' } } }

// ── Intel(结构性盲:prompt 只给码/名/行业/日期,防确认偏误)────────────────────
phase('Intel')
const intelOn = !!(cfg.l4_intel && cfg.l4_intel.enabled)
if (intelOn) {
  const maxQ = (cfg.l4_intel && cfg.l4_intel.max_queries) ?? 15
  const INTEL = { type: 'object', required: ['code'],
    properties: { code: { type: 'string' }, events: { type: 'integer' } } }
  const intel = await agent(
    `活体情报采集:${code} ${name}(${sector})· 分析日 ${date}。按你的人设六面全查(≤${maxQ} 条),写 ${SD}/_l4_intel_${code}.md;返回 code 与事件行数 events。`,
    { agentType: 'l4-intel', effort: cfg.agents?.l4_intel?.effort ?? 'max',
      ...(cfg.agents?.l4_intel?.model ? { model: cfg.agents.l4_intel.model } : {}),
      label: `intel:${code}`, phase: 'Intel', schema: INTEL })
  log(intel ? `🕵️ intel ✓ ${code}(events=${intel.events ?? '?'})` : `🕵️ intel ✗ ${code}(缺稿,卡自动回退卡内网查)`)
} else {
  log(`intel 关(config l4_intel.enabled=false)→ 直接出卡`)
}

// ── Card ────────────────────────────────────────────────────────
phase('Card')
const card = await agent(
  `执行 ${SD}/_l4_prompt_${code}.md:先读整个任务包,再按其指令做渐进深度 DD + 早停,写决策卡到 ${SD}/details/${code}.md。最后返回该卡最终五档评级(code / rating / conviction)。`,
  { agentType: 'l4-card', effort: cfg.agents?.l4_card?.effort ?? 'xhigh',
    ...(cfg.agents?.l4_card?.model ? { model: cfg.agents.l4_card.model } : {}),
    label: `card:${code}`, phase: 'Card', schema: CARD })
if (!card) return { code, name, rating: null, final: null, error: 'card 无返回 —— 单股失败只废单股,主会话单独重跑本 workflow 即可' }
log(`L4 卡 ✓ ${code} → ${card.rating}`)

// ── Verify:≥OW 双复核取中位,只向下折回(权威折回在 assemble 侧按 _ensemble_<code>.json 再算一遍)──
const isOW = (r) => /(overweight|\bbuy\b|增持|买入)/i.test(r || '')
let final = card.rating
if (isOW(card.rating)) {
  phase('Verify')
  log(`🎭 买单复核:${code} 追加 2 独立 run 取中位(只向下折回)`)
  const RANK = { 'sell': 0, 'underweight': 1, 'hold': 2, 'overweight': 3, 'buy': 4 }
  const tier = (r) => RANK[String(r || '').toLowerCase()] ?? 2
  const reruns = (await parallel([2, 3].map((i) => () => agent(
    `独立复核 run${i}(不知道其它 run 结论):执行 ${SD}/_l4_prompt_${code}.md 的任务包,按人设走渐进深度 DD,决策卡写到 ${SD}/ensemble/${code}.run${i}.md(先自行创建 ensemble/ 目录),返回 code/rating/conviction。`,
    { agentType: 'l4-card', effort: cfg.agents?.l4_card?.effort ?? 'xhigh',
      label: `ens${i}:${code}`, phase: 'Verify', schema: CARD })))).filter(Boolean)
  const ratings = [card.rating, ...reruns.map((r) => r.rating)]
  const sorted = ratings.map(tier).sort((a, b) => a - b)
  // N<3(复核 run 失败被 filter 掉)→ 退化取更偏空一侧(与"只向下"哲学对齐)+degraded 标记强制人裁展示
  const degraded = ratings.length < 3
  const medianTier = degraded ? sorted[0] : sorted[Math.floor(sorted.length / 2)]
  const names = ['Sell', 'Underweight', 'Hold', 'Overweight', 'Buy']
  const rec = { code, ratings, median: names[medianTier], spread: sorted[sorted.length - 1] - sorted[0], degraded }
  // 每股各写各的 _ensemble_<code>.json —— N 个 l4-stock 并行时无共享文件写竞态;assemble 合并读(_load_ensemble)。
  await agent(
    `在仓库根目录精确执行下面这条命令,然后只回报退出码。不要做别的、不要判断。\n\n\`\`\`\ncat > ${SD}/_ensemble_${code}.json << 'EOF'\n${JSON.stringify(rec)}\nEOF\n\`\`\``,
    { agentType: 'general-purpose', effort: 'low', label: `ens-dump:${code}`, phase: 'Verify' })
  if (tier(rec.median) < tier(card.rating)) final = rec.median
  log(`🎭 复核 ✓ ${code} runs=${JSON.stringify(ratings)} → 终评 ${final}${degraded ? '(degraded,报告强制人裁展示)' : ''}`)
}

return { code, name, rating: card.rating, final, conviction: card.conviction }
