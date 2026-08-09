export const meta = {
  name: 't1-review',
  description: 'T+1 判断层复盘快环:T 报告真选票 vs T+1 收盘 —— 哪些准哪些不准、为什么、准的强化/不准的优化(用户裁定 2026-07-17,保送不算;2 agent:合诊+综合)',
  phases: [
    { title: 'Diagnose', detail: '合诊 agent:跑记分卡 CLI(零 LLM 确定性数字)+ 一个 context 通读全部卡对比诊断' },
    { title: 'Synthesize', detail: '综合官:候选(稳定 key)+ report.md + finalize 落账/自动立案' },
  ],
}

// args: {date, cfg?} —— date = T(报告日);cfg.agents.t1_diag/t1_synth 管 model/effort
// (scan-retro 编排时用 `python -m autoresearch.scan.user_config` 读 scan_config.jsonc 随 args 传入;
//  缺省 = 内建默认;综合官另有 pack.agents_cfg 兜底)。
//
// 为什么 2 个 agent 而非每票一个(2026-07-17 用户裁定「就几个股票,2 个 agent 跑就行」):
// 真选票 ≤13(L4 预算帽),全部卡片 ~40–100KB 一个 context 装得下;且诊断本质是**比较任务**——
// 07-16 首跑实证:「4/5 归因市场β」这个跨票模式,单票盲诊各自看不见、要靠综合官事后拼,
// 合诊一眼就是——同 L3 holistic 单 agent 哲学。每票 fan-out 是 L4 深研的形状
// (每票 ~172KB slim + 渐进 DD + 网查),不适用于浅读对比;若未来 n 真超一个 context,再拆不迟。
const A = (typeof args === 'string' && args ? JSON.parse(args) : args) || {}
const date = A.date
if (!date) throw new Error('args.date 必填(T 报告日),如 {date:"2026-07-16"}')
const cfg = A.cfg || {}
// Wave12-T33(补齐 07-21 事故的最后一个游离点,比照 scan-market.js / l4-stock.js):
// 此前本文件没有空 cfg 守卫 —— 编排忘传 `args.cfg`,两个 agent 会**静默**吃下 'high',
// 报告上一点看不出来。那正是 07-21 事故的形状(配置查无 → 全体掉回缺省 → 无人知晓)。
// 确需空跑(离线试装)显式传 args.allow_empty_config=true。
if (!Object.keys(cfg).length && !A.allow_empty_config) {
  throw new Error('args.cfg 为空 —— t1_diag/t1_synth 会静默掉回缺省 effort(07-21 事故同族)。传 allow_empty_config:true 才可空跑。')
}
// Wave12-T33:model/effort 的解释收在 Python 侧(`user_config.resolve_agent_config`),
// 本表只是 resolved 没传到时的兜底;值与 `_ROLE_FALLBACK` 由测试机器锁同步。
const AGENT_DEFAULTS = {
  t1_diag:  { effort: 'high' },
  t1_synth: { effort: 'high' },
}
const RESOLVED = cfg.resolved_agents || {}
// 综合官阶段的最后兜底(pack.agents_cfg):d1 回来之后才有,故用可变量而非常量。
let LATE = {}
const AG = (role) => (RESOLVED[role]
  ? { ...RESOLVED[role] }
  : { ...(AGENT_DEFAULTS[role] || {}),
      ...(((cfg.agents || cfg)[role]) || LATE[role] || {}) })
const R = 'uv run --no-sync python -m'
const TD = `context/scan/${date}/t1_review`
const MECHS = '市场β(随大盘)/行业β(随板块)/卡内论点兑现/卡内风险兑现/卡内论点未兑现/判断错误(卡内证据当时就该给出不同评级)/无法解释(疑消息/盘面,需人工)'

// ── Diagnose(合诊:CLI + 通读全部卡;诚实铁律:只依据给定材料,无法解释就说无法解释)──
phase('Diagnose')
// ⚠️ StructuredOutput 会剪掉 schema properties 里没声明的键(07-17 首跑实证)——透传键逐个显式声明。
const DIAG_ITEMS = { type: 'array', items: { type: 'object', required: ['code', 'mechanism', 'why'],
  properties: { code: { type: 'string' }, mechanism: { type: 'string' }, why: { type: 'string' },
    stage: { type: ['string', 'null'] },
    card_said: { type: 'string' }, keep: { type: ['string', 'null'] }, fix: { type: ['string', 'null'] } } } }
const PACK = { type: 'object', required: ['t', 't1', 'n'],
  properties: { t: { type: 'string' }, t1: { type: 'string' }, n: { type: 'integer' },
    market_cc_pct: { type: 'number' }, excluded: { type: 'object' },
    scorecard_md: { type: 'string' }, agents_cfg: { type: 'object' },
    open_candidates: { type: 'array' }, ledger_tail: { type: 'object' }, diagnoses: DIAG_ITEMS } }
const d1 = await agent(
  `T+1 判断层合诊(一个 context 对比全部真选票)。\n` +
  `1. Bash 执行 \`${R} autoresearch.learning.t1_review build ${date} --json\`,读 stdout 的 JSON` +
  `(rows = 逐票实现数字;命令失败如「T+1 未结算」→ 把错误原样报出,不要试别的命令)。` +
  `n=0 → 直接返回 {t, t1, n: 0},别的都不做。\n` +
  `2. 对 rows 每一只:Read context/scan/${date}/details/<code>.md(当日决策卡;若存在 ` +
  `context/scan/${date}/_l4_intel_<code>.md 一并读),对照该票实现数字诊断。**判定尺 = z ` +
  `(行业中性超额/截面稳健σ,已剥大盘与板块共振)**——市场超额只是背景,别再把「没跟跌」当 alpha。\n` +
  `**终评尺 = 隔夜 gap(T+1 收→T+2 开,2026-08-05 用户裁定)**:今晚看到的 cc1 判定只是 D+1 ` +
  `初判,D+2 晚 nightly_close 会用 gap 回填 final_verdict 覆盖它——**准不准的对外口径以那时的 ` +
  `final_verdict 为准**;若某票初判(cc1)与终判(gap)方向相反,诊断必须解释隔夜发生了什么` +
  `(消息/情绪衰减/隔夜跳空),不能假装没这回事。\n` +
  `**分诊纪律(needs_diag 列)**:needs_diag=false 的票 = β/噪声区间,why 一句话 + mechanism 即可,` +
  `不展开;深度花在 needs_diag=true(不准 / ⚡惊奇 / 方向票 |z|≥1)上——**失败与意外样本的教训` +
  `价值实证高于成功样本,别把 token 花在给「准」写赞美诗**。🔒一字板 = 开盘买不到,论「可实现」时如实标。\n` +
  `**铁律:只准引用卡片/情报原文与 build 给的数字;禁止网查;禁止编造盘面或消息叙事**——无法从` +
  `卡内论点/风险条目解释的,mechanism 填「无法解释(疑消息/盘面,需人工)」,不要编故事。\n` +
  `每票产出:code;mechanism(从 ${MECHS} 选一);**stage = 归责哪一环**(从 L3选票/L4评级/` +
  `L4论点/intel情报/gate门/无 选一;「无」= β或运气,不归责——归责要能指到那一环当时可用的证据);` +
  `why(≤2 句,引用卡内具体论点/风险);card_said(卡里最相关一句,≤40字);keep(准了该强化什么,一句话,` +
  `没有则 null);fix(不准该优化什么,同上)。**你能同时看到全部票——跨票共同模式(同随大盘/` +
  `同板块/同类风险)请在各票 why 里如实标出,这是合诊相对单票盲诊的全部意义。**\n` +
  `3. Write ${TD}/diagnoses.json —— 全部诊断的 JSON 数组(机器契约,finalize 要读)。\n` +
  `返回:build 包的 t/t1/n/market_cc_pct/excluded/scorecard_md/agents_cfg/open_candidates/` +
  `ledger_tail **原样透传** + diagnoses 数组。`,
  { ...AG('t1_diag'), label: `diagnose:${date}`, phase: 'Diagnose', schema: PACK })
if (!d1) throw new Error('合诊失败(常见:T+1 daily 未结算,17:00 后再跑;或 T 无 finalists)')
if (!d1.n) return { t: d1.t, t1: d1.t1, n: 0, note: '真选 0 只(哨兵/全保送日),无从复盘' }
const diags = d1.diagnoses || []
log(`🩺 合诊 ✓ ${d1.t} → ${d1.t1}:${diags.length}/${d1.n} 票,市场基准 ${d1.market_cc_pct}%${d1.excluded && Object.keys(d1.excluded).length ? ',剔除 ' + JSON.stringify(d1.excluded) : ''}`)

// ── Synthesize(综合官:第二双独立的眼睛——对照账本查重复、写候选与报告、finalize 落账)──
phase('Synthesize')
// pack 兜底(合诊 agent 透传的 agents_cfg):resolved / args.cfg 都没给 t1_synth 时才用到。
LATE = (d1.agents_cfg && (d1.agents_cfg.agents || d1.agents_cfg)) || {}
if (d1.agents_cfg && d1.agents_cfg.resolved_agents) Object.assign(RESOLVED, d1.agents_cfg.resolved_agents)
const SYNTH = { type: 'object', required: ['right', 'wrong', 'candidates'],
  properties: { right: { type: 'integer' }, wrong: { type: 'integer' }, neutral: { type: 'integer' },
    surprises: { type: 'integer' }, unexplained: { type: 'integer' },
    candidates: { type: 'array', items: { type: 'string' } } } }
const openCands = d1.open_candidates || []
const synth = await agent(
  `你是 T+1 复盘综合官。材料:\n` +
  `- 记分卡:Read ${d1.scorecard_md}\n` +
  `- 合诊 JSON(${diags.length} 条):\n${JSON.stringify(diags)}\n` +
  `- 账本近况(近 ${d1.ledger_tail?.days ?? 0} 个 T 日,查重复模式用):${JSON.stringify(d1.ledger_tail)}\n` +
  `- 既有候选账本(跨日收敛的观察,key 是稳定标识):${JSON.stringify(openCands)}\n\n` +
  `做四件事:\n` +
  `1. 独立复核合诊:诊断与记分卡数字矛盾的(如超额为正却写「论点未兑现」),在报告里如实点出,不改 diagnoses.json。\n` +
  `2. Write ${TD}/candidates.json —— 今日候选经验的机器稿:JSON 数组 [{"key": "...", "text": "...", "stage": "..."}]。` +
  `key = 短横线小写英文 slug;**若与既有候选账本里某 key 同义,必须复用那个 key**(跨日重复计数靠它;换词=计数清零)。` +
  `**text 必须是「触发条件 → 动作」格式,含显式触发条件与可执行动作**,拒绝空泛——` +
  `反例:「要更注意β」;正例:「当全市场 |cc1|>3%(系统性日)时,对 z∈(−0.5,0.5) 的票一律记『无法区分β』,` +
  `禁记『论点兑现』」。stage = 这条经验该注给谁(L3/L4/intel/gate/process 选一,决定注入路由)。没有候选 → 写 []。\n` +
  `3. Write ${TD}/report.md —— 复盘报告,小节:## 总分(方向票准/不准/中性、Hold ⚡惊奇数)/## 逐票一行表/` +
  `## 准的共性(为什么准→强化什么)/## 不准的共性(为什么不准→优化什么)/` +
  `## 重复模式(对照账本近况与既有候选:同 key/同 mechanism 累计第几次,如实注明)/` +
  `## 诚实局限(单日 n=${d1.n} 样本、cc1 单日尺、无法解释票数、合诊复核发现)。` +
  `所有断言必须能追溯到合诊 JSON 或记分卡数字。你只写候选,**不裁决**——candidates.json 落盘后,` +
  `finalize 会把它记入候选账本、同 key 累计 ≥2 个 T 日才自动起草提案(人批);L3 明日的表会自动看到这些观察。\n` +
  `4. Bash 执行 \`${R} autoresearch.learning.t1_review finalize ${date}\`(它会验 report.md → 诊断入账本 → ` +
  `候选入账本+重复自动立案 → 标 done),把它打印的 JSON(含 promoted)如实并入返回。\n` +
  `返回:{right, wrong, neutral, surprises, unexplained, candidates:[候选一句话,...]}`,
  { ...AG('t1_synth'), label: `synth:${date}`, phase: 'Synthesize', schema: SYNTH })
if (!synth) throw new Error('综合阶段失败:report.md/finalize 未完成,本日仍 pending,可单独重跑本 workflow')
log(`✅ 快环 ✓ ${d1.t}→${d1.t1}:准 ${synth.right}/不准 ${synth.wrong};候选经验 ${synth.candidates.length} 条(人批)`)
return { t: d1.t, t1: d1.t1, n: d1.n, summary: synth, report: `${TD}/report.md` }
