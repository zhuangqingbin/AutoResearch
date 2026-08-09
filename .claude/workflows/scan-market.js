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
// 回显、由调用方随 Workflow args.config 传入(本脚本无文件系统访问,不能自己读文件)。缺省 = {}。
const cfg = (typeof args === 'string' && args ? JSON.parse(args).config : (args && args.config)) || {}
// Wave11-B5(07-21 事故根治):空 cfg = 静默关 intel + 全体掉回缺省 effort,且当时无人知晓。
// 结构性拒绝替代文档叮嘱;确需空跑(离线试装)显式传 args.allow_empty_config=true。
const _allowEmpty = !!(typeof args === 'string' && args ? JSON.parse(args).allow_empty_config
                       : (args && args.allow_empty_config))
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
// 这里连读都不再读 —— 留着 `cfg.performance?.stable_context_blocks` 就等于留了个陷阱:
// Python 侧的 `--stable-context` 已随 context_blocks.py 一并删除,谁把这个键加回 config,
// 这条流水线就会给一个不认识它的 CLI 传 flag。
// 哨兵档人工 override(SKILL 步骤 2.2:哨兵是「确定性建议,**人拍板**」,而本脚本原先硬编码直接跳 L3/L4
// —— 判据只问"今天有没有值得买的",不知道用户还有"保送持仓该不该走"的问题挂着)。缺省 false = 现行为(parity)。
const forceFull = !!(typeof args === 'string' && args ? JSON.parse(args).force_full : (args && args.force_full))
const R = 'uv run --no-sync python -m'
const SD = `context/scan/${date}`

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
    `⏳ **这条命令可能跑 5–30 分钟**(全市场取数)。铁律:\n` +
    `- **绝对不许 kill / pkill / 中断它**,也不许"重启一次试试"。它没卡住,它在取数。\n` +
    `- 若 harness 把它转成后台任务:安静等待完成通知即可。不要反复轮询、不要另起副本。\n` +
    `- 只有拿到真实退出码才算完;拿不到就如实回报"未拿到退出码",**不要**自己动手"修"。\n` +
    `(2026-08-05 事故:壳 pkill 了 prelude 两次,GATE1 因此毙掉整条流水线。)\n\n` +
    `\`\`\`\n${cmd}\n\`\`\``,
    { agentType: 'general-purpose', ...AG('gp_shell'), label, ...(phaseName ? { phase: phaseName } : {}) })
}
const RUN_MODE = { type: 'object', required: ['mode'],
  properties: { mode: { type: 'string' }, pinned_codes: { type: 'array', items: { type: 'string' } } } }
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
    `(混入 stderr 会污染这行 JSON)。`,
    { agentType: 'general-purpose', ...AG('gp_shell_json'), label, schema, ...(phaseName ? { phase: phaseName } : {}) })
}
// 通用确定性 CLI 壳:跑一条命令、把它打印的最后一行 JSON 原样带回(零判断)。
// 2026-08-03 事故:Wave10 A2(99efe7d)把 run_mode 的**调用点**抄进本文件,却把这份定义
// 落在了 l4-stock.js 里没一起搬过来 —— 每次全扫都在 GATE1 之后立刻 `gpJson is not defined`。
// 调用点的 `.catch(() => null)` 兜不住:ReferenceError 是同步抛的,promise 压根没生成。
// 与 l4-stock.js:42 保持同签名 (cmd, label, schema),phaseName 可选(缺省 = 沿用当前 phase())。
const gpJson = (cmd, label, schema, phaseName) => agent(
  `执行:\`${cmd}\`\n它会向 stdout 打印一行 JSON。把最后一行 JSON 原样作为结构化返回,` +
  '不改、不增删字段。**逐字节原样执行:不得添加 2>&1、tee、管道,不得改写或增删任何重定向。**',
  { agentType: 'general-purpose', ...AG('gp_shell_json'), label, schema,
    ...(phaseName ? { phase: phaseName } : {}) })
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
    `依次执行:\`${cmd}; ${R} autoresearch.scan.stage_result show ${SD} ${stage}\`\n` +
    '前一条命令的 stdout 保留作诊断；把最后一行 StageResult JSON 原样作为结构化返回。\n' +
    '**逐字节原样执行:不得添加 2>&1、tee、管道,不得改写或增删任何重定向**' +
    '(混入 stderr 会污染这行 JSON)。',
    { agentType: 'general-purpose', ...AG('gp_shell'), label,
      schema: STAGE_RESULT, ...(phaseName ? { phase: phaseName } : {}) })
}

// ── Phase Prelude ───────────────────────────────────────────────
phase('Prelude')
// frame 先行:pack 存盘 + 取数入湖(prelude/universe 随后湖命中不重拉)
log('Prelude 开始:frame → [universe 全市场取数 ∥ market_view](取数历史 ~10m,完成即 GATE1)')
await bash(`mkdir -p ${SD} && ${R} autoresearch.scan.frame ${date} --json-out ${SD}/market_pack.json`, 'frame', 'Prelude')
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
  `${R.replace('python -m', 'python -c')} "import json,sys;json.load(open('${SD}/market_pack.json'))" 2>/dev/null && echo '{"ok":true}' || echo '{"ok":false,"reason":"market_pack 缺失或非合法 JSON(frame 崩 / 产物被污染)"}'`,
  OK, 'Prelude')
if (!packok || !packok.ok) {
  log('⚠️ market_pack 缺失或非合法 JSON(frame 半途失败)→ 重试一次')
  await bash(`${R} autoresearch.scan.frame ${date} --json-out ${SD}/market_pack.json`, 'frame-retry', 'Prelude')
  const packok2 = await gate('pack-recheck',
    `${R.replace('python -m', 'python -c')} "import json,sys;json.load(open('${SD}/market_pack.json'))" 2>/dev/null && echo '{"ok":true}' || echo '{"ok":false,"reason":"重试后仍缺失/非法"}'`,
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
  `${R.replace('python -m', 'python -c')} "import json,sys;d=json.load(open('${SD}/strategist_pack.json'));sys.exit(0 if d.get('pack') else 1)" 2>/dev/null && echo '{"ok":true}' || echo '{"ok":false,"reason":"strategist_pack 缺失/空投影"}'`,
  OK, 'Prelude')
if (!spok || !spok.ok) {
  await bash(`${R} autoresearch.scan.strategist_pack ${SD}/market_pack.json -o ${SD}/strategist_pack.json`,
    'strategist-pack-rebuild', 'Prelude')
  const spok2 = await gate('strategist-pack-recheck',
    `${R.replace('python -m', 'python -c')} "import json,sys;d=json.load(open('${SD}/strategist_pack.json'));sys.exit(0 if d.get('pack') else 1)" 2>/dev/null && echo '{"ok":true}' || echo '{"ok":false,"reason":"补投后仍缺"}'`,
    OK, 'Prelude')
  log(spok2 && spok2.ok ? 'strategist-pack ✓(补投后)'
    : '🚨 strategist_pack 补投后仍缺 → market_view 无输入(B级降级·已记账);L5 走确定性脉搏回退')
}
// universe(确定性)∥ market_view(macro-lite 判断)—— barrier
await parallel([
  // W8-5:回显必须以**文件真在**为条件。原先 `prelude && echo SUMMARY_FILE=...` 只看 prelude
  // 退出码,07-28 汇总屏写盘失败(被 suppress 吞)时照样回显路径 → agent 回报「Summary file
  // generated」但文件不存在,CP1 转播落空。日志不得替不存在的文件背书。
  () => bash(`${R} autoresearch.scan.prelude ${date}; test -s ${SD}/_prelude_summary.md ` +
    `&& echo "SUMMARY_FILE=${SD}/_prelude_summary.md" || echo "SUMMARY_MISSING(见 stderr 的落盘失败行)"`,
    'prelude/universe', 'Prelude'),
  // Wave10 A4:策略师只拿**投影**(`strategist_pack.json`),不给 full pack。
  // 此前防锚定写在这句 prompt 里(「pack 里的 sector_healthy_top3 …忽略它」)——
  // 一句叮嘱管着一份就摆在眼前的数据,07-30/31 连续两日复发。指令级约束的失败率不为零,
  // 数据级为零:看不见就写不出。投影由 `frame --json-out` 同步落盘,allowlist 之外的键
  // (含 sector_healthy_top3 / run_contract / user_config 与**将来任何新增键**)默认进不来。
  // full pack 仍是 L5 与 L3 数字 validator 的事实源,不受影响。
  () => agent(
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
  await bash(`${R} autoresearch.scan.prelude ${date} --skip retro_refresh,retro_pending,consensus`,
    'prelude-retry', 'Prelude')
}
const g1 = await stageGate('GATE1', `${R} autoresearch.scan.gates gate1 ${date}`, 'gate1', 'Prelude')
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
const rm = await gpJson(
  `${R} autoresearch.scan.run_mode ${date} --decide --sentinel-level ${g1m.sentinel_level || 'full'}` +
  `${forceFull ? ' --force-full' : ''}`,
  'run-mode', RUN_MODE).catch(() => null)
const runMode = (rm && rm.mode) || (g1m.sentinel_level === 'sentinel'
  ? (forceFull ? 'FORCED_FULL' : 'SENTINEL_EMPTY') : 'FULL')
log(`运行模式 = ${runMode}${rm && rm.pinned_codes ? `(持仓 ${rm.pinned_codes.length} 只)` : ''}`)

if (runMode === 'SENTINEL_EMPTY') {
  log('哨兵档且无持仓 → 跳过 L3/L4(日历已在 prelude 跑过);assemble+GATE4 由主会话收尾')
  return { date, mode: 'sentinel', run_mode: runMode, finalists: 0, dispatch: [], meta: {},
    l4_budget: g1m.l4_budget, published: false }
}
if (runMode === 'SENTINEL_PINNED') {
  // 跳行业 brief、跳 L3、跳非持仓 L4;**持仓走完整 L4 链**(与 FULL 同一 l4-stock workflow、
  // 同 rubric、同双复核)。prompt 因明确缺 L3/判断型行业 brief 而不会字节相同 ——
  // 这个差异由 run_mode 与 `l3_judged=false` 公开,不伪装成等价上下文。
  const codes = (rm && rm.pinned_codes) || []
  log(`哨兵档·仅持仓复核:${codes.length} 只(${codes.join(',')})→ 跳 sector/L3/非持仓 L4`)
  await bash(`${R} autoresearch.scan.gates gate2 ${date} --skip ${'sentinel_pinned_no_l3'}`,
    'GATE2-skip', 'L3').catch(() => null)
  await bash(`${R} autoresearch.scan.agents.l4_card prompts ${date}`, 'l4-prep-pinned', 'L4-prep')
  return { date, mode: 'l4-handoff', run_mode: runMode, finalists: codes.length,
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
const l3cap = Math.min(10, l4Budget)
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
// Wave10 B4:`sector_brief_mode=finalist_only` 已退役 —— 它让 L3 看不到判断型行业 brief、
// **可能改变 finalists**,按「性能开关不拥有评级」铁律它不是性能开关;而它从未有获批的
// research experiment 来证明评级等价(§B4)。现在只有 `all` 这一条路。
const preL3BriefSectors = sectors
log(`待写行业 brief:${sectors.length} 个${sectors.length ? ` (${sectors.join('、')})` : '(全部 TTL 复用)'}`)
await parallel([
  () => bash(`${R} autoresearch.scan.agents.l3_select prepare ${date}`, 'l3-prepare', 'L3'),
  ...preL3BriefSectors.map((sec) => () => agent(
    `你是行业分析师。读 context/sector/${date}/${sec}.json 写 ${SD}/sector_briefs/${sec}.md,两段机器契约(## 地形段 喂 L3/L4 · ## 研判段 仅 L5,含 **行业方向** 行)。零新取数。`,
    { agentType: 'sector-brief', ...AG('sector_brief'),
      label: `brief:${sec}`, phase: 'L3' })
    .then((r) => { log(`brief ✓ ${sec}`); return r })),
])
// L3 holistic 精排(唯一 max-effort 判断核心)
log(`L3 精排开始:pass1 已分诊 200→~40(影子 _l3_pass1_cut.csv),l3-rank 深比较出 finalist tier 7~${l3cap} 只+bench(effort max,历史 60行~14-25m,40行待测)`)
await agent(
  `L3 精排 · 日期 ${date} · finalist tier 按质 7~${l3cap} 只(judged 每元素带 finalist:true/false)+其余为 bench;宁缺毋滥。文件在 ${SD}/:_l3_table.md(~40 表,pass1 已分诊)、market_view.md(§1-3 地形)、sector_briefs/(地形段)。按你的人设(6 维 rubric + 硬约束 A-E)比较式精排,写 ${SD}/_l3_judged.json。`,
  { agentType: 'l3-rank', ...AG('l3_rank'),
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
  // Wave12-T34 归因契约(改这段前先读):`l3_repair` 复用 `agentType: 'l3-rank'` 派发,
  // harvest 分不清一行是主排还是自修。归因靠的是**产物**——`repair-pack` 在派发之前写下的
  // `_l3_repair_prompt.md` 在场即"本日派过一次 l3_repair"
  // (`autoresearch.trace.usage_reconcile.dispatch_census`)。
  // 故意不让这个 agent 自己写标记:2026-07-27 它死于 `Connection closed mid-response`,
  // 烧掉 56.9k 加权却没留下任何自报记录 —— 让"它自己承认跑过"当唯一事实源,恰好会在
  // 它死掉时丢掉那一行的归属,而那正是最需要看清成本的时刻。
  // ⚠️ 若以后改成"prompt 不在场也可能派发"或"n==0 也写 prompt",必须同步改 dispatch_census。
  const fix = repair && repair.n > 0 ? await agent(
    `Read ${SD}/_l3_repair_prompt.md，只处理其中列出的失败票；按文件内 schema 用 Write 写 ${SD}/_l3_repair_patch.json。不要读取任何全量 L3 输入或输出文件。`,
    { agentType: 'l3-rank', ...AG('l3_repair'), label: 'L3-lint-fix', phase: 'L3' })
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
await bash(
  // shared 必须先于 prompts:_l4_shared_instructions.md 此前全仓无生产者(只有读者),
  // 当日 📐/🔁/🚪 校准行从未到达任何一张决策卡(Wave5 ④B)。
  // TTL 复用(l4_reuse --apply)已于 2026-07-29 退役(用户裁定 R5「不要任何复用」)——
  // 复用票不跑 intel、新闻冻在源卡日,评级稳定性改由昨卡回声承接(l4/prompts.py)。
  `${R} autoresearch.scan.agents.l4_card shared ${date}; ` +
  `( ${R} autoresearch.scan.agents.l4_card pledge ${date} || true ) & ` +
  `( ${R} autoresearch.scan.agents.l4_card seats ${date} || true ) & ` +
  `( ${R} autoresearch.scan.calendar ${date} || true ) & ` +
  `( ${R} autoresearch.scan.agents.l4_card consensus ${date} || true ) & ` +
  `wait; ` +
  `${R} autoresearch.scan.agents.l4_card prompts ${date}`, 'l4-prep', 'L4-prep')
const PLAN = { type: 'object', required: ['dispatch'],
  properties: { dispatch: { type: 'array', items: { type: 'string' } },
    meta: { type: 'object' } } }
const plan = await gate('dispatch-plan', `${R} autoresearch.scan.agents.l4_card dispatch-plan ${date}`, PLAN, 'L4-prep')
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
  const g3 = await gate('GATE3', `${R} autoresearch.scan.agents.l4_card harvest-slim ${date}`, G3, 'L4-prep')
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
      dispatch_batches: { type: 'array', items: { type: 'array', items: { type: 'string' } } } } }
  const tasks = await gate('l4-tasks-init', `${R} autoresearch.scan.l4_tasks init ${date}`, TASKS, 'L4-prep')
  if (!tasks || !tasks.ok) throw new Error('L4 task book 初始化失败')
  taskBook = tasks.path
  dispatchBatches = tasks.dispatch_batches || []
  log(`L4 流式任务簿 ✓ ${taskBook} · 批次宽度 ${tasks.effective_cap || '?'} · ${dispatchBatches.length} 批`)
}
log(`L4 交接:新派 ${dispatch.length} 股(每股一个 l4-stock workflow,主会话并行拉起)`)
// CP4(Wave5 ①):随时可调的确定性看板,不用等一小时后的 summary.md
log(`🔎 随时可调:\`${R} autoresearch.scan.render ${date} --view menu_health\`(L2 成色)· \`--view gate_hist\`(L4 完成后看评级分布/停因分桶/门柱)· \`--view timing\`(分段耗时)`)
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
return { date, mode: 'l4-handoff', finalists: g2m.n, dispatch, dispatch_batches: dispatchBatches,
  task_book: taskBook, streaming_l4: streamingL4,
  meta: plan.meta || g2m.meta || {}, l4_budget: g1m.l4_budget, published: false }
