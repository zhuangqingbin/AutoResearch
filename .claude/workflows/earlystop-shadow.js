export const meta = {
  name: 'earlystop-shadow',
  description: '对已入稳定抽样队列的早停票做完整深审；只写 shadow，不改变正式评级或交易建议',
  phases: [
    { title: 'Preflight', detail: '验证 code 已在 shadow/earlystop_queue.json' },
    { title: 'ShadowReview', detail: '强制走完深度 DD 与 Rubric，写 shadow-only 决策卡' },
  ],
}

// args: {date, code}; 本 workflow 与 scan-market 关键路径完全分离。
const A = (typeof args === 'string' && args ? JSON.parse(args) : args) || {}
const { date, code } = A
if (!date || !code) throw new Error('args.date/args.code 必填')
const SD = `context/scan/${date}`
const OUT = `${SD}/shadow/earlystop_details/${code}.md`
const CARD = {
  type: 'object',
  required: ['code', 'rating'],
  properties: {
    code: { type: 'string' },
    rating: { type: 'string' },
    conviction: { type: 'number' },
    proposal: { type: 'string' },
  },
}

phase('Preflight')
await agent(
  `在仓库根目录执行:\`uv run --no-sync python -m autoresearch.learning.earlystop_shadow show ${date} ${code}\`。只回报退出码；失败则抛错。队列事实位于 ${SD}/shadow/earlystop_queue.json。`,
  { agentType: 'general-purpose', model: 'haiku', effort: 'low',
    label: `earlystop-shadow-check:${code}`, phase: 'Preflight' })

phase('ShadowReview')
const result = await agent(
  `这是早停反事实影子深审，不是正式重跑。读取 ${SD}/_l4_prompt_${code}.md 仅获取本票数据与研究上下文；忽略其中正式输出路径与早停退出指令。对 ${code} 强制走完全部深度 DD、P4 与 Rubric，把完整卡只写到 ${OUT}。

硬边界：不得修改 production 的正式卡、decision_records、ensemble、final rating 或 transaction proposal；影子结论不能改变正式评级，也不能成为 BUY 来源。最后返回影子卡 code/rating/conviction/proposal。`,
  { agentType: 'l4-card', effort: 'xhigh', label: `earlystop-shadow:${code}`,
    phase: 'ShadowReview', schema: CARD })

log(`🪞 early-stop shadow ✓ ${code} → ${OUT};不能改变正式评级`)
return { code, shadowRating: result && result.rating, output: OUT, productionEffect: 'NONE' }
