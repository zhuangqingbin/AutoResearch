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
const date = (typeof args === 'string' && args ? JSON.parse(args).date : (args && args.date))
if (!date) throw new Error('args.date 必填,如 {date:"2026-07-07"}')
// scan_config.json 白名单校验后的 user_config(autoresearch/scan/user_config.py)经 frame --json
// 回显、由调用方随 Workflow args.config 传入(本脚本无文件系统访问,不能自己读文件)。缺省 = {} →
// 下游 `cfg.agents?.<stage>?.effort ?? '<现值>'` 全部落回硬编码现值(parity)。顶部取一次。
const cfg = (typeof args === 'string' && args ? JSON.parse(args).config : (args && args.config)) || {}
// 哨兵档人工 override(SKILL 步骤 2.2:哨兵是「确定性建议,**人拍板**」,而本脚本原先硬编码直接跳 L3/L4
// —— 判据只问"今天有没有值得买的",不知道用户还有"保送持仓该不该走"的问题挂着)。缺省 false = 现行为(parity)。
const forceFull = !!(typeof args === 'string' && args ? JSON.parse(args).force_full : (args && args.force_full))
const R = 'uv run --no-sync python -m'
const SD = `context/scan/${date}`

// 确定性命令 → general-purpose Bash-agent(只跑命令、回报退出码,不判断)
// Wave6 T1:壳零判断,却背着 opus 系统前缀 —— 07-24 真计量 13 个 gp 共 798k 加权(全场 14.5%),
// 其中 7 个 2-消息壳 ≈287k 纯过路费。降 haiku;判断仍在确定性 CLI 里,行为不变。
function bash(cmd, label, phaseName) {   // 形参勿叫 phase:会遮蔽全局 phase() 分组函数
  return agent(
    `在仓库根目录精确执行下面这条命令,然后只回报:退出码 + stdout 末 15 行。不要做别的、不要判断、不要解释。\n\n\`\`\`\n${cmd}\n\`\`\``,
    { agentType: 'general-purpose', model: 'haiku', effort: 'low', label, ...(phaseName ? { phase: phaseName } : {}) })
}
const OK = { type: 'object', required: ['ok'],
  properties: { ok: { type: 'boolean' }, reason: { type: 'string' } } }
const STAGE_RESULT = { type: 'object', required: ['stage', 'status', 'metrics'],
  properties: { stage: { type: 'string' }, status: { type: 'string' },
    metrics: { type: 'object' }, error: {} } }
// Wave6 T1:门的判据 100% 在确定性 CLI 里,agent 只把它打印的 JSON 原样带回 —— 转述不需要
// 思考,effort high→low 且降 haiku。schema 校验仍在(格式错会被 harness 拒),门行为不变。
function gate(label, cmd, schema, phaseName) {   // 同上:避免遮蔽全局 phase()
  return agent(
    `执行:\`${cmd}\`\n它会向 stdout 打印 JSON。把它打印的最后一行 JSON 原样作为你的结构化返回(字段不改、不增删)。`,
    { agentType: 'general-purpose', model: 'haiku', effort: 'low', label, schema, ...(phaseName ? { phase: phaseName } : {}) })
}
// 业务门先保留原 stdout 供诊断，Workflow 只消费随后读取并验 hash/contract 的 StageResult。
function stageGate(label, cmd, stage, phaseName) {
  return agent(
    `依次执行:\`${cmd}; ${R} autoresearch.scan.stage_result show ${SD} ${stage}\`\n` +
    '前一条命令的 stdout 保留作诊断；把最后一行 StageResult JSON 原样作为结构化返回。',
    { agentType: 'general-purpose', model: 'haiku', effort: 'low', label,
      schema: STAGE_RESULT, ...(phaseName ? { phase: phaseName } : {}) })
}

// ── Phase Prelude ───────────────────────────────────────────────
phase('Prelude')
// frame 先行:pack 存盘 + 取数入湖(prelude/universe 随后湖命中不重拉)
log('Prelude 开始:frame → [universe 全市场取数 ∥ market_view](取数历史 ~10m,完成即 GATE1)')
await bash(`mkdir -p ${SD} && ${R} autoresearch.scan.frame ${date} --json > ${SD}/market_pack.json`, 'frame', 'Prelude')
// frame 与 universe 同样走 tushare 全市场取数,同样会 ChunkedEncodingError 半途而废 —— 但此前只有
// universe 有重试守卫(见下方 l2-check),frame 这条裸奔。2026-07-27 实跑逮到:frame 在 11/12 端点
// 处断线退出码 1,`>` 重定向只留下 **0 字节** market_pack.json;bash() 不看退出码 → 空 pack 一路
// 流到 market_view。macro-brief 正确拒写(空壳比缺文件更坏:文件一旦存在就压掉 L5 的
// render_fallback_pulse 回退,还经 l4_card.py 的 market_context_block 把无信息简报注入每张 L4 卡),
// 于是 market_view.md 缺席、L3 在没有地形段的情况下精排 —— 静默降级,25 分钟后才被人眼发现。
// 同族前科:空 pickle 永不重拉 / 空 slim 默认 Hold。判据用 `test -s`(非零字节),与 l2-check 同形。
const packok = await gate('pack-check',
  `test -s ${SD}/market_pack.json && echo '{"ok":true}' || echo '{"ok":false,"reason":"market_pack 0 字节(frame 崩)"}'`,
  OK, 'Prelude')
if (!packok || !packok.ok) {
  log('⚠️ market_pack 空(frame 半途失败)→ 重试一次')
  await bash(`${R} autoresearch.scan.frame ${date} --json > ${SD}/market_pack.json`, 'frame-retry', 'Prelude')
  const packok2 = await gate('pack-recheck',
    `test -s ${SD}/market_pack.json && echo '{"ok":true}' || echo '{"ok":false,"reason":"重试后仍空"}'`,
    OK, 'Prelude')
  // 不 throw:market_pack 是 B 级(缺了 L3 少地形段、L5 有确定性脉搏回退,持仓仍需当日卡)。
  // 但降级必须留痕 —— 这一行就是账,别让它再静默。
  if (!packok2 || !packok2.ok) {
    log('🚨 market_pack 重试后仍空 → 本次 L3/L4 无市场地形段(B级降级·已记账);market_view 会拒写,L5 走确定性脉搏回退')
  } else {
    log('pack-check ✓(重试后)')
  }
}
// universe(确定性)∥ market_view(macro-lite 判断)—— barrier
await parallel([
  () => bash(`${R} autoresearch.scan.prelude ${date} && echo "SUMMARY_FILE=${SD}/_prelude_summary.md"`,
    'prelude/universe', 'Prelude'),
  () => agent(
    `读 ${SD}/market_pack.json,按你的人设写 ${SD}/market_view.md(六小节;前3描述性地形、后2仅 L5)。数字只出自 pack,不编;个股不评级、不锚定卡片。pack 里的 sector_healthy_top3 键是 L5 专用的确定性产物,忽略它,不得把"看多行业"及其排名写进任何小节。`,
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
const g1 = await stageGate('GATE1', `${R} autoresearch.scan.gates gate1 ${date}`, 'gate1', 'Prelude')
if (!g1 || !(g1.status === 'SUCCEEDED')) throw new Error(`GATE1 失败:${g1 ? g1.error : 'agent 无返回'}`)
log(`GATE1 ✓ sentinel=${g1.metrics.sentinel_level} · L4预算=${g1.metrics.l4_budget}`)
// CP1(Wave5 ①):bash 回报只有 stdout 末 15 行,而汇总屏是 12 步 ✓/✗ + 预热状态 + 当日件
// 建议行 + 下一步 —— 结构性放不下。指路文件,由主会话 Read 后全量转播给用户。
log(`📋 前奏汇总屏全文:${SD}/_prelude_summary.md(主会话 Read 后全量转播 —— 回报的末 15 行装不下 12 步屏)`)

// ── 哨兵档:材料枯竭 → 跳过 sector/L3/L4;assemble+GATE4 由主会话收尾 ──────────
if (g1.metrics.sentinel_level === 'sentinel' && !forceFull) {
  log('哨兵档 → 跳过 L3/L4(日历已在 prelude 跑过);assemble+GATE4 由主会话收尾')
  return { date, mode: 'sentinel', finalists: 0, dispatch: [], reused: [], meta: {},
    l4_budget: g1.metrics.l4_budget, published: false }
}
if (g1.metrics.sentinel_level === 'sentinel' && forceFull) {
  log('⚠️ 哨兵档被人工 override(force_full)→ 照常跑 L3/L4。诚实标注:确定性判据判「材料枯竭」,买单侧期望低。')
}

// ── Phase L3 ────────────────────────────────────────────────────
phase('L3')
// finalist tier 上限(plan 2026-07-12-l3-merge-plan.md Task 4):L3.5 闸的收窄职能已并入 L3,
// L3 直接出 7–10 只 finalist(宁缺毋滥,不强制凑到此数)——cap 而非目标。
const l3cap = Math.min(10, g1.metrics.l4_budget)
// 中观行业 pack(确定性)先行,再 [sector-briefs ∥ L3 表准备] barrier。sector-pack + 待写清单
// 合并一个 gate(壳合并①,-1 spawn):schema 顶层必须是 object(API 拒 `type:'array'` → 400 →
// agent 返回 null → `|| []` 静默吞掉,结果是一份行业 brief 都不写、L3 在没有行业地形段的情况下
// 精排。2026-07-09 实跑逮到。
const SECTORS = { type: 'object', required: ['ok', 'sectors'],
  properties: { ok: { type: 'boolean' }, sectors: { type: 'array', items: { type: 'string' } } } }
const sectorsRes = await gate('sector-pack+list',
  `${R} autoresearch.sector.reuse ${date} --apply; ${R} autoresearch.sector.pack ${date}; ` +
  `uv run --no-sync python -c "import json,glob,os;d='context/sector/${date}';b='${SD}/sector_briefs';` +
  `print(json.dumps({'ok':True,'sectors':sorted(os.path.splitext(os.path.basename(p))[0] ` +
  `for p in glob.glob(d+'/*.json') if not os.path.exists(os.path.join(b,os.path.splitext(os.path.basename(p))[0]+'.md')))}))"`,
  SECTORS, 'L3')
if (!sectorsRes) throw new Error('sector-pack+list 无返回(schema/API 失败)—— 不静默降级为"无行业 brief"')
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
log(`L3 精排开始:pass1 已分诊 200→~40(影子 _l3_pass1_cut.csv),l3-rank 深比较出 finalist tier 7~${l3cap} 只+bench(effort max,历史 60行~14-25m,40行待测)`)
await agent(
  `L3 精排 · 日期 ${date} · finalist tier 按质 7~${l3cap} 只(judged 每元素带 finalist:true/false)+其余为 bench;宁缺毋滥。文件在 ${SD}/:_l3_table.md(~40 表,pass1 已分诊)、market_view.md(§1-3 地形)、sector_briefs/(地形段)。按你的人设(6 维 rubric + 硬约束 A-E)比较式精排,写 ${SD}/_l3_judged.json。`,
  { agentType: 'l3-rank', effort: cfg.agents?.l3_rank?.effort ?? 'max',
    ...(cfg.agents?.l3_rank?.model ? { model: cfg.agents.l3_rank.model } : {}),
    label: 'L3-rank', phase: 'L3' })
// thesis 数字机检(确定性 lint):打回一次自修,修复后不再二检(防循环)
const l3lint = await gate('l3-lint', `${R} autoresearch.scan.agents.l3_select lint ${date}`, OK, 'L3')
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
    `${R} autoresearch.scan.agents.l3_select repair-pack ${date}`, REPAIR, 'L3')
  const fix = repair && repair.n > 0 ? await agent(
    `Read ${SD}/_l3_repair_prompt.md，只处理其中列出的失败票；按文件内 schema 用 Write 写 ${SD}/_l3_repair_patch.json。不要读取任何全量 L3 输入或输出文件。`,
    { agentType: 'l3-rank', effort: 'medium', label: 'L3-lint-fix', phase: 'L3' })
    .catch((e) => { log(`⚠️ L3 自修 agent 异常:${e && e.message ? e.message : e}`); return null }) : null
  if (fix) {
    const APPLY = { type: 'object', required: ['ok', 'patched', 'preserved', 'codes'],
      properties: { ok: { type: 'boolean' }, patched: { type: 'integer' },
        preserved: { type: 'integer' }, codes: { type: 'array', items: { type: 'string' } } } }
    const applied = await gate('l3-repair-apply',
      `${R} autoresearch.scan.agents.l3_select apply-repair ${date}`, APPLY, 'L3')
    if (applied && applied.ok) log(`L3 局部修复 ✓ ${applied.patched} 票· 原样保留 ${applied.preserved} 票`)
    else log('⚠️ L3 局部修复 patch 校验/merge 未完成—— 带原 judged 继续')
  } else {
    log('⚠️ L3 数字自修未完成(agent 无返回/断连)—— 带未修 judged 继续,machine-lint 结论已记在上一行')
  }
}
// 确定性写 finalists(修前导零)+ GATE2,合并一个 gate(壳合并②,-1 spawn)
const g2 = await stageGate('GATE2',
  `${R} autoresearch.scan.agents.l3_select finalists ${date} --budget ${l3cap} && ` +
  `${R} autoresearch.scan.gates gate2 ${date} --budget ${l3cap}`, 'gate2', 'L3')
if (!g2 || !(g2.status === 'SUCCEEDED')) throw new Error(`GATE2 失败:${g2 ? g2.error : 'no return'}`)
// L3.5 闸已完全移除(2026-07-12 用户裁定"直接 L3 输出"):L3 finalist tier 即 L4 入选集。
// CP3(Wave5 ①):整条漏斗最高光的一刻是"选出了哪几只",而不是"选出了几只"。
// g2.meta 早就带着 name/sector(gates.py:95),此前被整段扔掉。
log(`GATE2 ✓ finalists=${g2.metrics.n}`)
const fmeta = g2.metrics.meta || {}
;(g2.metrics.finalists || []).forEach((c, i) => {
  const m = fmeta[c] || {}
  log(`  L3入围 ${i + 1}/${g2.metrics.n} ${c} ${m.name || ''}${m.sector ? `(${m.sector})` : ''}`)
})

// ── Phase L4-prep ───────────────────────────────────────────────
// (fb_20260714_003)决策卡与活体情报不再在本 workflow 内派发:每股一个独立 l4-stock workflow
// (.claude/workflows/l4-stock.js,intel→card→OW复核 股内链式衔接),由主会话在本 workflow 返回后
// 一条消息 N 个 Workflow 调用并行拉起——每股独立并发帽真并行、单股失败只废单股(2026-07-14
// GATE3 差 16 字节毙掉 60min/1.6M token 全流水线的教训)。本 workflow 到 dispatch 交接为止,
// assemble+GATE4 也随之上移主会话收尾。
phase('L4-prep')
log('L4-prep:reuse→[四生产者并行]→prompts→slim(决策卡交接给每股 l4-stock workflow)')
await bash(
  // shared 必须先于 prompts:_l4_shared_instructions.md 此前全仓无生产者(只有读者),
  // 当日 📐/🔁/🚪 校准行从未到达任何一张决策卡(Wave5 ④B)。
  `${R} autoresearch.scan.agents.l4_card shared ${date}; ` +
  `${R} autoresearch.scan.l4_reuse ${date} --apply; ` +
  `( ${R} autoresearch.scan.agents.l4_card pledge ${date} || true ) & ` +
  `( ${R} autoresearch.scan.agents.l4_card seats ${date} || true ) & ` +
  `( ${R} autoresearch.scan.calendar ${date} || true ) & ` +
  `( ${R} autoresearch.scan.agents.l4_card consensus ${date} || true ) & ` +
  `wait; ` +
  `${R} autoresearch.scan.agents.l4_card prompts ${date}`, 'l4-prep', 'L4-prep')
const PLAN = { type: 'object', required: ['dispatch'],
  properties: { dispatch: { type: 'array', items: { type: 'string' } },
    meta: { type: 'object' },
    reused: { type: 'array', items: { type: 'object',
      properties: { code: { type: 'string' }, rating: { type: 'string' } } } } } }
const plan = await gate('dispatch-plan', `${R} autoresearch.scan.agents.l4_card dispatch-plan ${date}`, PLAN, 'L4-prep')
if (!plan) throw new Error('dispatch-plan 无返回')
// GATE3:批量 slim 预取(合格判据 = 结构+内容,l4_card._slim_defect;体积只兜真垃圾)。
// 失败响亮但**只剔失败股**,全失败才毙——不再让单股数据病拖死整条流水线。
const G3 = { type: 'object', required: ['ok'],
  properties: { ok: { type: 'boolean' }, reason: { type: 'string' },
    failures: { type: 'array', items: { type: 'object',
      properties: { ticker: { type: 'string' }, bytes: { type: 'integer' }, why: { type: 'string' } } } } } }
const g3 = await gate('GATE3', `${R} autoresearch.scan.agents.l4_card harvest-slim ${date}`, G3, 'L4-prep')
if (!g3) throw new Error('GATE3 无返回')
let dispatch = plan.dispatch
if (!g3.ok) {
  const bad = new Set((g3.failures || []).map((f) => String(f.ticker || '').slice(0, 6)))
  dispatch = dispatch.filter((c) => !bad.has(c))
  log(`⚠️ GATE3:${bad.size} 股 slim 不合格被剔除(${[...bad].join('/') || '?'})—— ${(g3.failures || []).map((f) => `${f.ticker}:${f.why}`).join('; ') || g3.reason || ''}`)
  if (!dispatch.length) throw new Error(`GATE3 失败:全部 slim 不合格 —— ${g3.reason || ''}`)
} else {
  log('GATE3 ✓ 全 slim 结构+内容合格')
}
log(`L4 交接:新派 ${dispatch.length} 股(每股一个 l4-stock workflow,主会话并行拉起)· 复用 ${(plan.reused || []).length} 张跳派发`)
// CP4(Wave5 ①):随时可调的确定性看板,不用等一小时后的 summary.md
log(`🔎 随时可调:\`${R} autoresearch.scan.render ${date} --view menu_health\`(L2 成色)· \`--view gate_hist\`(L4 完成后看评级分布/停因分桶/门柱)· \`--view timing\`(分段耗时)`)
// 📌 保送票在派发那一秒必须可见:07-21 漏传 args.pinned → 300857/601869 的持仓 SELL 双复核
// 整段没跑(self_review 探针 9 sell_review_missing 只能事后 warn,拦不住)。
const metaAll = plan.meta || g2.metrics.meta || {}
const pinnedCodes = dispatch.filter((c) => metaAll[c] && metaAll[c].pinned)
if (pinnedCodes.length) {
  log(`📌 保送票 ${pinnedCodes.length} 只:${pinnedCodes.join('/')} —— 派发这些 l4-stock 必须传 args.pinned:true(漏传=持仓 SELL 双复核断链)`)
} else {
  log('📌 保送票 0 只(本次 dispatch 无 pinned)')
}

// meta(名称/行业)透传给 l4-stock 的 intel 盲搜 prompt;assemble+GATE4 由主会话在全部 l4-stock 完成后收尾。
return { date, mode: 'l4-handoff', finalists: g2.metrics.n, dispatch, reused: plan.reused || [],
  meta: plan.meta || g2.metrics.meta || {}, l4_budget: g1.metrics.l4_budget, published: false }
