export const meta = {
  name: 'scan-market',
  description: '全 A股六段漏斗:一个确定性 workflow 编排全流程 + 四校验门(prelude→市场/行业→L3→L4→assemble)',
  phases: [
    { title: 'Prelude', detail: 'frame → [universe/L0-L2 ∥ market_view] → GATE1' },
    { title: 'L3', detail: '[sector-briefs ∥ 证据harvest] → L3-rank(pass1→深比较 7-10) → finalists → GATE2' },
    { title: 'L4', detail: 'slim-harvest ∥ 情报站(GATE3) → 决策卡并发' },
    { title: 'Assemble', detail: 'assemble → GATE4' },
  ],
}

// ── 输入 & 常量 ──────────────────────────────────────────────────
// args 可能以对象或(harness 序列化后的)JSON 字符串到达 —— 两种都容错解析。
const date = (typeof args === 'string' && args ? JSON.parse(args).date : (args && args.date))
if (!date) throw new Error('args.date 必填,如 {date:"2026-07-07"}')
// scan_config.json 白名单校验后的 user_config(autoresearch/scan/user_config.py)经 frame --json
// 回显、由调用方随 Workflow args.config 传入(本脚本无文件系统访问,不能自己读文件)。缺省 = {} →
// 下游 `cfg.agents?.<stage>?.effort ?? '<现值>'` 全部落回硬编码现值(parity)。顶部取一次。
const cfg = (typeof args === 'string' && args ? JSON.parse(args).config : (args && args.config)) || {}
const R = 'uv run --no-sync python -m'
const SD = `context/scan/${date}`

// 确定性命令 → general-purpose Bash-agent(只跑命令、回报退出码,不判断)
function bash(cmd, label, phaseName) {   // 形参勿叫 phase:会遮蔽全局 phase() 分组函数
  return agent(
    `在仓库根目录精确执行下面这条命令,然后只回报:退出码 + stdout 末 15 行。不要做别的、不要判断、不要解释。\n\n\`\`\`\n${cmd}\n\`\`\``,
    { agentType: 'general-purpose', effort: 'low', label, ...(phaseName ? { phase: phaseName } : {}) })
}
// 门 CLI → Bash-agent + schema(把 CLI 打印的 JSON 原样带回)。
// required 只列 'ok':失败 JSON 只含 {ok:false, reason}(无 sentinel_level/finalists 等成功字段);
// 若把成功字段列进 required,失败 JSON 校验不过 → agent 返回 null → 丢失 reason(报错退化为"无返回")。
const GATE1 = { type: 'object', required: ['ok'],
  properties: { ok: { type: 'boolean' }, reason: { type: 'string' },
    sentinel_level: { type: 'string' }, l4_budget: { type: 'integer' } } }
const GATE2 = { type: 'object', required: ['ok'],
  properties: { ok: { type: 'boolean' }, reason: { type: 'string' },
    finalists: { type: 'array', items: { type: 'string' } }, n: { type: 'integer' },
    // L3.5 可插拔闸回显(design 2026-07-11 §3;plan Task 4):l4_gate=闸名(缺配置→'passthrough'
    // =parity),l35_cut_n=闸砍掉几只(passthrough 恒 0)。finalists/n 已是闸后(收窄后)数。
    l4_gate: { type: 'string' }, l35_cut_n: { type: 'integer' } } }
const OK = { type: 'object', required: ['ok'],
  properties: { ok: { type: 'boolean' }, reason: { type: 'string' } } }
function gate(label, cmd, schema, phaseName) {   // 同上:避免遮蔽全局 phase()
  return agent(
    `执行:\`${cmd}\`\n它会向 stdout 打印一行 JSON。把那行 JSON 原样作为你的结构化返回(字段不改、不增删)。`,
    { agentType: 'general-purpose', effort: 'high', label, schema, ...(phaseName ? { phase: phaseName } : {}) })
}

// ── Phase Prelude ───────────────────────────────────────────────
phase('Prelude')
// frame 先行:pack 存盘 + 取数入湖(prelude/universe 随后湖命中不重拉)
log('Prelude 开始:frame → [universe 全市场取数 ∥ market_view](取数历史 ~10m,完成即 GATE1)')
await bash(`mkdir -p ${SD} && ${R} autoresearch.scan.frame ${date} --json > ${SD}/market_pack.json`, 'frame', 'Prelude')
// universe(确定性)∥ market_view(macro-lite 判断)—— barrier
await parallel([
  () => bash(`${R} autoresearch.scan.prelude ${date}`, 'prelude/universe', 'Prelude'),
  () => agent(
    `读 ${SD}/market_pack.json,按你的人设写 ${SD}/market_view.md(六小节;前3描述性地形、后2仅 L5)。数字只出自 pack,不编;个股不评级、不锚定卡片。`,
    { agentType: 'macro-brief', effort: cfg.agents?.strategist?.effort ?? 'high',
      ...(cfg.agents?.strategist?.model ? { model: cfg.agents.strategist.model } : {}),
      label: 'market_view', phase: 'Prelude' }),
])
// universe 走 tushare 全市场取数,偶发 ChunkedEncodingError 半途而废(prelude 内 ✗ 但不阻断),
// 结果是 GATE1 在第 ~14 分钟毙掉整条流水线。进门前先探一次 L2,缺就重试一遍确定性前奏。
const l2ok = await gate('l2-check',
  `test -s ${SD}/L2_gbdt_top200.csv && echo '{"ok":true}' || echo '{"ok":false,"reason":"L2 缺失"}'`, OK, 'Prelude')
if (!l2ok || !l2ok.ok) {
  log('L2 缺失(universe 半途失败)→ 重试确定性前奏一次')
  await bash(`${R} autoresearch.scan.prelude ${date} --skip retro_refresh,retro_pending,consensus`,
    'prelude-retry', 'Prelude')
}
const g1 = await gate('GATE1', `${R} autoresearch.scan.gates gate1 ${date}`, GATE1, 'Prelude')
if (!g1 || !g1.ok) throw new Error(`GATE1 失败:${g1 ? g1.reason : 'agent 无返回'}`)
log(`GATE1 ✓ sentinel=${g1.sentinel_level} · L4预算=${g1.l4_budget}`)

// ── 哨兵档:材料枯竭 → 跳过 sector/L3/L4 ─────────────────────────
if (g1.sentinel_level === 'sentinel') {
  log('哨兵档 → 跳过 L3/L4,直接 assemble(观察单/日历已在 prelude 跑过)')
  await bash(`${R} autoresearch.scan.assemble ${date}`, 'assemble', 'Assemble')
  const g4s = await gate('GATE4', `${R} autoresearch.scan.gates gate4 ${date}`, OK, 'Assemble')
  if (!g4s || !g4s.ok) throw new Error(`GATE4(哨兵)失败:${g4s ? g4s.reason : 'no return'}`)
  return { date, mode: 'sentinel', finalists: 0, cards: 0, buys: [], isZeroBuy: true, published: true }
}

// ── Phase L3 ────────────────────────────────────────────────────
phase('L3')
// finalist tier 上限(plan 2026-07-12-l3-merge-plan.md Task 4):L3.5 闸的收窄职能已并入 L3,
// L3 直接出 7–10 只 finalist(宁缺毋滥,不强制凑到此数)——cap 而非目标。
const l3cap = Math.min(10, g1.l4_budget)
// 中观行业 pack(确定性)先行,再 [sector-briefs ∥ L3 表准备] barrier
await bash(`${R} autoresearch.sector.reuse ${date} --apply; ${R} autoresearch.sector.pack ${date}`, 'sector-pack', 'L3')
// schema 顶层必须是 object(API 拒 `type:'array'` → 400 → agent 返回 null → `|| []` 静默吞掉,
// 结果是一份行业 brief 都不写、L3 在没有行业地形段的情况下精排。2026-07-09 实跑逮到。
const sectorsRes = await agent(
  `执行:\`uv run --no-sync python -c "import json,glob,os;d='context/sector/${date}';b='${SD}/sector_briefs';print(json.dumps(sorted(os.path.splitext(os.path.basename(p))[0] for p in glob.glob(d+'/*.json') if not os.path.exists(os.path.join(b,os.path.splitext(os.path.basename(p))[0]+'.md')))))"\`\n它打印一行 JSON 数组 = 待写 brief 的行业(有 pack 且尚无 brief;♻️复用行业已被 reuse 拷贝,故被排除,勿再派发覆盖)。把该数组放进 \`sectors\` 字段作为结构化返回;目录不存在则 \`sectors: []\`。`,
  { agentType: 'general-purpose', effort: 'low', label: 'sector-list', phase: 'L3',
    schema: { type: 'object', required: ['sectors'],
      properties: { sectors: { type: 'array', items: { type: 'string' } } } } })
if (!sectorsRes) throw new Error('sector-list 无返回(schema/API 失败)—— 不静默降级为"无行业 brief"')
const sectors = sectorsRes.sectors || []
log(`待写行业 brief:${sectors.length} 个${sectors.length ? ` (${sectors.join('、')})` : '(全部 TTL 复用)'}`)
await parallel([
  () => bash(`${R} autoresearch.scan.agents.l3_select prepare ${date}`, 'l3-prepare', 'L3'),
  ...sectors.map((sec) => () => agent(
    `你是行业分析师。读 context/sector/${date}/${sec}.json 写 ${SD}/sector_briefs/${sec}.md,两段机器契约(## 地形段 喂 L3/L4 · ## 研判段 仅 L5,含 **行业方向** 行)。零新取数。`,
    { agentType: 'sector-brief', effort: cfg.agents?.sector_brief?.effort ?? 'high',
      ...(cfg.agents?.sector_brief?.model ? { model: cfg.agents.sector_brief.model } : {}),
      label: `brief:${sec}`, phase: 'L3' })
    .then((r) => { log(`brief ✓ ${sec}`); return r })),
])
// L3 holistic 精排(唯一 max-effort 判断核心)
log(`L3 精排开始:pass1 已分诊 200→~60(影子 _l3_pass1_cut.csv),l3-rank 深比较出 finalist tier 7~${l3cap} 只+bench(effort max,历史 ~14m)`)
await agent(
  `L3 精排 · 日期 ${date} · finalist tier 按质 7~${l3cap} 只(judged 每元素带 finalist:true/false)+其余为 bench;宁缺毋滥。文件在 ${SD}/:_l3_table.md(~60 表,pass1 已分诊)、market_view.md(§1-3 地形)、sector_briefs/(地形段)。按你的人设(6 维 rubric + 硬约束 A-E)比较式精排,写 ${SD}/_l3_judged.json。`,
  { agentType: 'l3-rank', effort: cfg.agents?.l3_rank?.effort ?? 'max',
    ...(cfg.agents?.l3_rank?.model ? { model: cfg.agents.l3_rank.model } : {}),
    label: 'L3-rank', phase: 'L3' })
// thesis 数字机检(确定性 lint):打回一次自修,修复后不再二检(防循环)
const l3lint = await gate('l3-lint', `${R} autoresearch.scan.agents.l3_select lint ${date}`, OK, 'L3')
if (l3lint && l3lint.ok === false) {
  log(`L3 数字机检未过 → 打回一次自修:${(l3lint.reason || '').slice(0, 200)}`)
  await agent(
    `你之前写的 ${SD}/_l3_judged.json 有 thesis 引用数字与 ${SD}/_l3_table.md 不符:\n${l3lint.reason}\n只修这些票的 thesis/数字(以表为准或删掉具体数字改定性措辞),其余票原样保留,用 Write 覆写同一文件。`,
    { agentType: 'l3-rank', effort: 'medium', label: 'L3-lint-fix', phase: 'L3' })
}
// 确定性写 finalists(修前导零)+ GATE2
await bash(`${R} autoresearch.scan.agents.l3_select finalists ${date} --budget ${l3cap}`, 'finalists', 'L3')
const g2 = await gate('GATE2', `${R} autoresearch.scan.gates gate2 ${date} --budget ${l3cap}`, GATE2, 'L3')
if (!g2 || !g2.ok) throw new Error(`GATE2 失败:${g2 ? g2.reason : 'no return'}`)
// finalists/n 已是 L3.5 闸后(收窄后)数——闸默认 passthrough(=parity,cut 恒 0),仅在
// scan_config.json 显式配置 l4_gate 时才会真收窄,此时才附 cut 只数。
log(`GATE2 ✓ finalists=${g2.n}(L3.5闸=${g2.l4_gate || 'passthrough'}${g2.l35_cut_n ? ` · cut${g2.l35_cut_n}` : ''})`)

// ── Phase L4 ────────────────────────────────────────────────────
phase('L4')
// 派发包(确定性):TTL复用+carryover → pledge/seats/calendar(旗源 csv)→ prompts(.SH 归一;compose 时读旗源)
log('L4 派发包+slim 预取开始(reuse→旗源→prompts→slim,历史 ~10m)')
await bash(
  `${R} autoresearch.scan.l4_reuse ${date} --apply --carryover; ` +
  `${R} autoresearch.scan.agents.l4_card pledge ${date} || true; ` +
  `${R} autoresearch.scan.agents.l4_card seats ${date} || true; ` +
  `${R} autoresearch.scan.calendar ${date} || true; ` +
  `${R} autoresearch.scan.agents.l4_card consensus ${date} || true; ` +
  `${R} autoresearch.scan.agents.l4_card prompts ${date}`, 'l4-prep', 'L4')
// 派发计划(确定性)提前到 GATE3 之前:情报站要与 slim 预取同窗口并行(只读 finalists/_l4_prompt 存在性,与 slim 无依赖)
const PLAN = { type: 'object', required: ['dispatch'],
  properties: { dispatch: { type: 'array', items: { type: 'string' } },
    meta: { type: 'object' },
    reused: { type: 'array', items: { type: 'object',
      properties: { code: { type: 'string' }, rating: { type: 'string' } } } } } }
const plan = await gate('dispatch-plan', `${R} autoresearch.scan.agents.l4_card dispatch-plan ${date}`, PLAN, 'L4')
if (!plan) throw new Error('dispatch-plan 无返回')
// 活体情报站(design 2026-07-12 §3;config 默认关):盲搜 sonnet·max,每票一个,∥ GATE3 slim 预取。
// agent 失败→null 不阻断——卡侧 presence-gated 缺文件自动回退卡内网查。
const intelOn = !!(cfg.l4_intel && cfg.l4_intel.enabled)
const INTEL = { type: 'object', required: ['code'],
  properties: { code: { type: 'string' }, events: { type: 'integer' } } }
const intelThunks = intelOn ? plan.dispatch.map((code) => () => agent(
  `活体情报采集:${code} ${(plan.meta?.[code]?.name) || ''}(${(plan.meta?.[code]?.sector) || '行业未知'})· 分析日 ${date}。按你的人设六面全查(≤15 条),写 ${SD}/_l4_intel_${code}.md;返回 code 与事件行数 events。`,
  { agentType: 'l4-intel', effort: cfg.agents?.l4_intel?.effort ?? 'max',
    ...(cfg.agents?.l4_intel?.model ? { model: cfg.agents.l4_intel.model } : {}),
    label: `intel:${code}`, phase: 'L4', schema: INTEL })) : []
if (intelOn) log(`🕵️ 情报站并行:${plan.dispatch.length} 票盲搜(sonnet·max,与 slim 预取同窗口)`)
// GATE3:批量 slim 失败响亮(harvest-slim 打印 JSON + 非零退出)—— intel 与之并行,barrier 后再派卡
const [g3, ...intelRes] = await parallel([
  () => gate('GATE3', `${R} autoresearch.scan.agents.l4_card harvest-slim ${date}`, OK, 'L4'),
  ...intelThunks,
])
if (!g3 || !g3.ok) throw new Error(`GATE3 失败(slim<8KB 或 .SH):${g3 ? g3.reason : 'no return'}`)
if (intelOn) log(`🕵️ 情报站 ✓ ${intelRes.filter(Boolean).length}/${plan.dispatch.length}(缺稿卡自动回退网查)`)
log('GATE3 ✓ 全 slim >8KB(surface)')
// 决策卡:只派 dispatch 码一次并发(barrier —— assemble 与 isZeroBuy 需全部卡评级才能判)
const CARD = { type: 'object', required: ['code', 'rating'],
  properties: { code: { type: 'string' }, rating: { type: 'string' }, conviction: { type: 'number' } } }
log(`L4 并发:新派 ${plan.dispatch.length} 张(历史 ~7–15m)· 复用 ${(plan.reused || []).length} 张跳派发`)
let _done = 0
const fresh = (await parallel(plan.dispatch.map((code) => () => agent(
  `执行 ${SD}/_l4_prompt_${code}.md:先读整个任务包,再按其指令做渐进深度 DD + 早停,写决策卡到 ${SD}/details/${code}.md。最后返回该卡最终五档评级(code / rating / conviction)。`,
  { agentType: 'l4-card', effort: cfg.agents?.l4_card?.effort ?? 'xhigh',
    ...(cfg.agents?.l4_card?.model ? { model: cfg.agents.l4_card.model } : {}),
    label: `card:${code}`, phase: 'L4', schema: CARD })
  .then((r) => { _done += 1; log(`L4 卡 ${_done}/${plan.dispatch.length} ✓ ${code}${r ? ` → ${r.rating}` : '(无返回)'}`); return r }))))
  .filter(Boolean)
const cards = [...fresh, ...(plan.reused || [])]
const isOW = (r) => /(overweight|\bbuy\b|增持|买入)/i.test(r || '')

// 买单复核 ensemble(拍板 2):≥OW 的新派卡各追加 2 个独立 run,取中位;只向下折回。
const RANK = { 'sell': 0, 'underweight': 1, 'hold': 2, 'overweight': 3, 'buy': 4 }
const tier = (r) => RANK[String(r || '').toLowerCase()] ?? 2
const owFresh = fresh.filter((c) => isOW(c.rating))
if (owFresh.length) {
  log(`🎭 买单复核:${owFresh.length} 张 ≥OW 卡各追加 2 独立 run 取中位`)
  const ens = await parallel(owFresh.map((c) => () => (async () => {
    const reruns = (await parallel([2, 3].map((i) => () => agent(
      `独立复核 run${i}(不知道其它 run 结论):执行 ${SD}/_l4_prompt_${c.code}.md 的任务包,按人设走渐进深度 DD,决策卡写到 ${SD}/ensemble/${c.code}.run${i}.md(先自行创建 ensemble/ 目录),返回 code/rating/conviction。`,
      { agentType: 'l4-card', effort: cfg.agents?.l4_card?.effort ?? 'xhigh', label: `ens${i}:${c.code}`, phase: 'L4', schema: CARD })))).filter(Boolean)
    const ratings = [c.rating, ...reruns.map((r) => r.rating)]
    const sorted = ratings.map(tier).sort((a, b) => a - b)
    // N<3(复核 run 失败被 filter 掉)→ 退化取更偏空一侧(与"只向下"哲学对齐)+degraded 标记强制人裁展示
    const degraded = ratings.length < 3
    const medianTier = degraded ? sorted[0] : sorted[Math.floor(sorted.length / 2)]
    const names = ['Sell', 'Underweight', 'Hold', 'Overweight', 'Buy']
    return { code: c.code, ratings, median: names[medianTier], spread: sorted[sorted.length - 1] - sorted[0], degraded }
  })()))
  const rows = ens.filter(Boolean)
  await bash(`cat > ${SD}/_ensemble.json << 'EOF'\n${JSON.stringify(rows)}\nEOF`, 'ensemble-dump', 'L4')
  for (const e of rows) {                       // buys 判定用折回后评级
    const card = cards.find((c) => c.code === e.code)
    if (card && tier(e.median) < tier(card.rating)) card.rating = e.median
  }
}

const buys = cards.filter((c) => isOW(c.rating)).map((c) => c.code)
const isZeroBuy = buys.length === 0
log(`L4 ✓ 新派 ${fresh.length} + 复用 ${(plan.reused || []).length} = ${cards.length} 卡 · ≥OW ${buys.length} · ${isZeroBuy ? '0买日' : '有买单'}`)

// ── Phase Assemble ──────────────────────────────────────────────
phase('Assemble')
// L5 整合(内含 self_review 硬门 + dump gate_fires)+ GATE4
await bash(`${R} autoresearch.scan.assemble ${date}`, 'assemble', 'Assemble')
const g4 = await gate('GATE4', `${R} autoresearch.scan.gates gate4 ${date}`, OK, 'Assemble')
if (!g4 || !g4.ok) throw new Error(`GATE4 失败(self_review 未通过):${g4 ? g4.reason : 'no return'}`)
log('GATE4 ✓ self_review 通过')

return { date, mode: 'full', finalists: g2.n, cards: cards.length, buys, isZeroBuy, published: true }
