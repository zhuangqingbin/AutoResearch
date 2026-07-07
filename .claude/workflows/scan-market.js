export const meta = {
  name: 'scan-market',
  description: '全 A股六段漏斗:一个确定性 workflow 编排全流程 + 四校验门(prelude→市场/行业→L3→L4→assemble)',
  phases: [
    { title: 'Prelude', detail: 'frame → [universe/L0-L2 ∥ market_view] → GATE1' },
    { title: 'L3', detail: '[sector-briefs ∥ 证据harvest] → L3-rank → finalists → GATE2' },
    { title: 'L4', detail: 'slim-harvest(GATE3) → 决策卡并发' },
    { title: 'Assemble', detail: '0买红队 → assemble → GATE4' },
  ],
}

// ── 输入 & 常量 ──────────────────────────────────────────────────
const date = args && args.date
if (!date) throw new Error('args.date 必填,如 {date:"2026-07-07"}')
const R = 'uv run --no-sync python -m'
const SD = `context/scan/${date}`

// 确定性命令 → general-purpose Bash-agent(只跑命令、回报退出码,不判断)
function bash(cmd, label) {
  return agent(
    `在仓库根目录精确执行下面这条命令,然后只回报:退出码 + stdout 末 15 行。不要做别的、不要判断、不要解释。\n\n\`\`\`\n${cmd}\n\`\`\``,
    { agentType: 'general-purpose', effort: 'low', label })
}
// 门 CLI → Bash-agent + schema(把 CLI 打印的 JSON 原样带回)。
// required 只列 'ok':失败 JSON 只含 {ok:false, reason}(无 sentinel_level/finalists 等成功字段);
// 若把成功字段列进 required,失败 JSON 校验不过 → agent 返回 null → 丢失 reason(报错退化为"无返回")。
const GATE1 = { type: 'object', required: ['ok'],
  properties: { ok: { type: 'boolean' }, reason: { type: 'string' },
    sentinel_level: { type: 'string' }, l4_budget: { type: 'integer' } } }
const GATE2 = { type: 'object', required: ['ok'],
  properties: { ok: { type: 'boolean' }, reason: { type: 'string' },
    finalists: { type: 'array', items: { type: 'string' } }, n: { type: 'integer' } } }
const OK = { type: 'object', required: ['ok'],
  properties: { ok: { type: 'boolean' }, reason: { type: 'string' } } }
const RT = { type: 'object', required: ['run'],
  properties: { run: { type: 'boolean' }, reason: { type: 'string' } } }
function gate(label, cmd, schema) {
  return agent(
    `执行:\`${cmd}\`\n它会向 stdout 打印一行 JSON。把那行 JSON 原样作为你的结构化返回(字段不改、不增删)。`,
    { agentType: 'general-purpose', effort: 'low', label, schema })
}

// ── Phase Prelude ───────────────────────────────────────────────
phase('Prelude')
// frame 先行:pack 存盘 + 取数入湖(prelude/universe 随后湖命中不重拉)
await bash(`mkdir -p ${SD} && ${R} autoresearch.scan.frame ${date} --json > ${SD}/market_pack.json`, 'frame')
// universe(确定性)∥ market_view(macro-lite 判断)—— barrier
await parallel([
  () => bash(`${R} autoresearch.scan.prelude ${date}`, 'prelude/universe'),
  () => agent(
    `你是首席策略师。按 macro-research lite 档(模板见 .claude/skills/macro-research/macro-playbook.md 末节「lite 档:市场研判」)读 ${SD}/market_pack.json,写 ${SD}/market_view.md(定调/结构/红黑榜/操作基调)。数字只出自 pack,不编数;个股不评级。`,
    { agentType: 'claude', model: 'opus', effort: 'medium', label: 'market_view', phase: 'Prelude' }),
])
const g1 = await gate('GATE1', `${R} autoresearch.scan.gates gate1 ${date}`, GATE1)
if (!g1 || !g1.ok) throw new Error(`GATE1 失败:${g1 ? g1.reason : 'agent 无返回'}`)
log(`GATE1 ✓ sentinel=${g1.sentinel_level} · L4预算=${g1.l4_budget}`)

// ── 哨兵档:材料枯竭 → 跳过 sector/L3/L4 ─────────────────────────
if (g1.sentinel_level === 'sentinel') {
  log('哨兵档 → 跳过 L3/L4,直接 assemble(观察单/日历已在 prelude 跑过)')
  await bash(`${R} autoresearch.scan.assemble ${date}`, 'assemble')
  const g4s = await gate('GATE4', `${R} autoresearch.scan.gates gate4 ${date}`, OK)
  if (!g4s || !g4s.ok) throw new Error(`GATE4(哨兵)失败:${g4s ? g4s.reason : 'no return'}`)
  return { date, mode: 'sentinel', finalists: 0, cards: 0, buys: [], isZeroBuy: true, published: true }
}

// ── Phase L3 ────────────────────────────────────────────────────
phase('L3')
// 中观行业 pack(确定性)先行,再 [sector-briefs ∥ L3 表准备] barrier
await bash(`${R} autoresearch.sector.reuse ${date} --apply; ${R} autoresearch.sector.pack ${date}`, 'sector-pack')
const sectors = await agent(
  `列出目录 context/sector/${date}/ 下所有 *.json 文件的文件名去扩展名(= 行业名)。只返回 JSON 字符串数组;目录不存在或空则返回 []。`,
  { agentType: 'general-purpose', effort: 'low', label: 'sector-list',
    schema: { type: 'array', items: { type: 'string' } } }) || []
await parallel([
  () => bash(`${R} autoresearch.scan.agents.l3_select prepare ${date}`, 'l3-prepare'),
  ...sectors.map((sec) => () => agent(
    `你是行业分析师。读 context/sector/${date}/${sec}.json 写 ${SD}/sector_briefs/${sec}.md,两段机器契约(## 地形段 喂 L3/L4 · ## 研判段 仅 L5,含 **行业方向** 行)。零新取数。`,
    { agentType: 'sector-brief', effort: 'low', label: `brief:${sec}`, phase: 'L3' })),
])
// L3 holistic 精排(唯一 max-effort 判断核心)
await agent(
  `L3 精排 · 日期 ${date} · 目标约 ${g1.l4_budget} 只。文件在 ${SD}/:_l3_table.md(~200 表)、market_view.md(§1-3 地形)、sector_briefs/(地形段)。按你的人设(5 维 rubric + 硬约束 A/B/C/D)比较式精排,写 ${SD}/_l3_judged.json。`,
  { agentType: 'l3-rank', effort: 'max', label: 'L3-rank', phase: 'L3' })
// 确定性写 finalists(修前导零)+ GATE2
await bash(`${R} autoresearch.scan.agents.l3_select finalists ${date} --budget ${g1.l4_budget}`, 'finalists')
const g2 = await gate('GATE2', `${R} autoresearch.scan.gates gate2 ${date} --budget ${g1.l4_budget}`, GATE2)
if (!g2 || !g2.ok) throw new Error(`GATE2 失败:${g2 ? g2.reason : 'no return'}`)
log(`GATE2 ✓ finalists=${g2.n}`)

// ── Phase L4 ────────────────────────────────────────────────────
phase('L4')
// 派发包(确定性):TTL复用+carryover → prompts(.SH 归一)→ pledge → calendar
await bash(
  `${R} autoresearch.scan.l4_reuse ${date} --apply --carryover; ` +
  `${R} autoresearch.scan.agents.l4_card prompts ${date}; ` +
  `${R} autoresearch.scan.agents.l4_card pledge ${date} || true; ` +
  `${R} autoresearch.scan.calendar ${date} || true`, 'l4-prep')
// GATE3:批量 slim 失败响亮(harvest-slim 打印 JSON + 非零退出)
const g3 = await gate('GATE3', `${R} autoresearch.scan.agents.l4_card harvest-slim ${date}`, OK)
if (!g3 || !g3.ok) throw new Error(`GATE3 失败(slim<10KB 或 .SH):${g3 ? g3.reason : 'no return'}`)
log('GATE3 ✓ 全 slim >10KB')
// 派发计划(确定性):按 _l4_prompt_<code>.md 是否存在分 dispatch(需新派)/ reused(TTL复用
// 或 carryover 已写 details/<code>.md,不再派 subagent,直接解析该卡评级)。修复:此前对
// 全部 finalists 无条件派卡,复用码从未写过 prompt 文件,等于空派 Opus,抵消复用省下的成本。
const PLAN = { type: 'object', required: ['dispatch'],
  properties: { dispatch: { type: 'array', items: { type: 'string' } },
    reused: { type: 'array', items: { type: 'object',
      properties: { code: { type: 'string' }, rating: { type: 'string' } } } } } }
const plan = await gate('dispatch-plan', `${R} autoresearch.scan.agents.l4_card dispatch-plan ${date}`, PLAN)
if (!plan) throw new Error('dispatch-plan 无返回')
// 决策卡:只派 dispatch 码一次并发(barrier —— 红队需全部评级才知是否 0 买)
const CARD = { type: 'object', required: ['code', 'rating'],
  properties: { code: { type: 'string' }, rating: { type: 'string' }, conviction: { type: 'number' } } }
const fresh = (await parallel(plan.dispatch.map((code) => () => agent(
  `执行 ${SD}/_l4_prompt_${code}.md:先读整个任务包,再按其指令做渐进深度 DD + 早停,写决策卡到 ${SD}/details/${code}.md。最后返回该卡最终五档评级(code / rating / conviction)。`,
  { agentType: 'l4-card', effort: 'medium', label: `card:${code}`, phase: 'L4', schema: CARD }))))
  .filter(Boolean)
// 复用卡是 {code, rating}(无 conviction),下方 typeof c.conviction === 'number' 过滤天然排除它们
const cards = [...fresh, ...(plan.reused || [])]
const isOW = (r) => /(overweight|\bbuy\b|增持|买入)/i.test(r || '')
const buys = cards.filter((c) => isOW(c.rating)).map((c) => c.code)
const isZeroBuy = buys.length === 0
log(`L4 ✓ 新派 ${fresh.length} + 复用 ${(plan.reused || []).length} = ${cards.length} 卡 · ≥OW ${buys.length} · ${isZeroBuy ? '0买日' : '有买单'}`)

// ── Phase Assemble ──────────────────────────────────────────────
phase('Assemble')
// 0 买日:机会成本红队(抽检门 + conviction 最高的 2 个 Hold),产出只进观察单
if (isZeroBuy) {
  const rt = await gate('redteam-gate', `${R} autoresearch.scan.gates redteam ${date}`, RT)
  const holds = cards
    .filter((c) => !isOW(c.rating) && typeof c.conviction === 'number')
    .sort((a, b) => b.conviction - a.conviction).slice(0, 2)
  if (rt && rt.run && holds.length) {
    log(`机会成本红队 ×${holds.length}(${rt.reason})`)
    await parallel(holds.map((h) => () => agent(
      `机会成本红队(模式B=多方)。攻"压 ${h.code} 评级的那道 binding gate"是否太紧:读 ${SD}/details/${h.code}.md + slim,给翻转触发(观察单词表 close_above/ma_bull/money_pos/by_date),写 ${SD}/_v_${h.code}.md。不改评级、不喊单。`,
      { agentType: 'buy-skeptic', effort: 'high', label: `redteam:${h.code}`, phase: 'Assemble' })))
  } else {
    log(`机会成本红队跳过(${rt ? rt.reason : '无候选'})`)
  }
}
// L5 整合(内含 self_review 硬门 + dump gate_fires)+ GATE4
await bash(`${R} autoresearch.scan.assemble ${date}`, 'assemble')
const g4 = await gate('GATE4', `${R} autoresearch.scan.gates gate4 ${date}`, OK)
if (!g4 || !g4.ok) throw new Error(`GATE4 失败(self_review 未通过):${g4 ? g4.reason : 'no return'}`)
log('GATE4 ✓ self_review 通过')

return { date, mode: 'full', finalists: g2.n, cards: cards.length, buys, isZeroBuy, published: true }
