export const meta = {
  name: 'dossier-init',
  description: '单票首覆建档:确定性骨架(builder)→ Opus 首覆 agent 填四 LLM 节 → lint 校验;池内 pending_init 逐票拉起(spec 2026-07-22 ②)',
  phases: [
    { title: 'Skeleton', detail: 'prefetch(若缺)+ builder 骨架(幂等)' },
    { title: 'Initiate', detail: 'dossier-init agent 填 LLM 节(不改确定性节)' },
    { title: 'Lint', detail: 'schema.lint_dossier 校验 + frontmatter initiated 核' },
  ],
}

// args: {date, code, name, sector}
const A = (typeof args === 'string' && args ? JSON.parse(args) : args) || {}
const { date, code } = A
if (!date || !code) throw new Error('args.date/args.code 必填')
const name = A.name || ''
const sector = A.sector || ''
const R = 'uv run --no-sync python -m'
const DP = `context/knowledge/dossiers/${code}.md`

function bash(cmd, label, ph) {
  return agent(
    `在仓库根目录精确执行下面这条命令,然后只回报:退出码 + stdout 末 10 行。不要做别的。\n\n\`\`\`\n${cmd}\n\`\`\``,
    { agentType: 'general-purpose', effort: 'low', label, phase: ph })
}

phase('Skeleton')
await bash(`${R} autoresearch.dossier.prefetch ${code} ${date} || true; ` +
           `${R} autoresearch.dossier.builder ${code} ${date} --name "${name}" --sector "${sector}"`,
           `skeleton:${code}`, 'Skeleton')

phase('Initiate')
const INIT = { type: 'object', required: ['code'],
  properties: { code: { type: 'string' }, initiated: { type: 'boolean' },
    summary_tokens: { type: 'number' }, uncertainty: { type: 'string' } } }
const r = await agent(
  `首覆建档:${code} ${name}(${sector})· 分析日 ${date}。骨架:${DP};prefetch:context/knowledge/dossiers/_prefetch/${code}.json;slim 若在:context/${code}.*_${date}_slim.md(Glob 找,含 _slim_deep)。按你的人设只填四个 LLM 节(<!-- LLM:待首覆 --> 处)与摘要叙事锚,不改确定性节。返回 code/initiated/summary_tokens/uncertainty。`,
  { agentType: 'dossier-init', effort: 'max', label: `init:${code}`, phase: 'Initiate', schema: INIT })
if (!r || !r.initiated) return { code, initiated: false, issues: ['agent 未完成或未回传'] }

phase('Lint')
const LINT = { type: 'object', required: ['ok'],
  properties: { ok: { type: 'boolean' }, reason: { type: 'string' } } }
const lint = await agent(
  `在仓库根目录执行:\n\n\`\`\`\nuv run --no-sync python -c "from autoresearch.dossier import schema; import json, pathlib; t=pathlib.Path('${DP}').read_text(encoding='utf-8'); iss=schema.lint_dossier(t); print(json.dumps({'ok': not iss, 'reason': ';'.join(iss)[:200]}, ensure_ascii=False))"\n\`\`\`\n\n它打印一行 JSON,把最后一行 JSON 原样作为结构化返回。`,
  { agentType: 'general-purpose', effort: 'low', label: `lint:${code}`, phase: 'Lint', schema: LINT })
return { code, initiated: true, issues: lint && !lint.ok ? [lint.reason] : [] }
