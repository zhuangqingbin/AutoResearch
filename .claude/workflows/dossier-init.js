export const meta = {
  name: 'dossier-init',
  description: '单票首覆建档:确定性骨架(builder)→ Opus 首覆 agent 填四 LLM 节 → lint 校验;池内 pending_init 逐票拉起(spec 2026-07-22 ②)',
  phases: [
    { title: 'Skeleton', detail: 'prefetch(若缺)+ builder 骨架(幂等)' },
    { title: 'Initiate', detail: 'dossier-init agent 填 LLM 节(不改确定性节)' },
    { title: 'Lint', detail: 'schema.lint_dossier 校验 + frontmatter initiated 核' },
  ],
}

// args: {date, code, name, sector, cfg?}(cfg = scan_config 的 agents 回显,透传 dossier_init/
// gp_shell/gp_shell_json 的 model/effort;省略 = 用本文件 AGENT_DEFAULTS 缺省)
const A = (typeof args === 'string' && args ? JSON.parse(args) : args) || {}
const { date, code } = A
if (!date || !code) throw new Error('args.date/args.code 必填')
const name = A.name || ''
const sector = A.sector || ''
const cfg = A.cfg || {}
// Wave11-B2:model/effort 单一事实源=scan_config.agents(闭集见 user_config._AGENT_ROLES);
// 本表=缺键回退值。调用点禁止内联字面量(product_shape_lint 会查)。回退链:
// config > 本表(AGENT_DEFAULTS) > agent .md frontmatter —— dossier_init 本表不写 model 键,
// 缺省即落 dossier-init frontmatter(opus)。
const AGENT_DEFAULTS = {
  gp_shell:      { model: 'sonnet', effort: 'low' },
  gp_shell_json: { model: 'sonnet', effort: 'low' },
  dossier_init:  { effort: 'max' },
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
const R = 'uv run --no-sync python -m'
// 引擎隔离根:engine 随 args.engine 下发(缺省 claude;只有 Claude 会执行本 js)
const ENGINE = (A.engine || 'claude')
const CTX = `context_${ENGINE}`
const DP = `${CTX}/knowledge/dossiers/${code}.md`

function bash(cmd, label, ph) {
  return agent(
    `在仓库根目录精确执行下面这条命令,然后只回报:退出码 + stdout 末 10 行。不要做别的。\n\n\`\`\`\n${cmd}\n\`\`\``,
    // Wave6 T1(扩到本 workflow):2026-07-27 实测一次建档 249.8k 加权,其中壳 agent 占 **27%**
    // (67.2k 换 1.0k 输出)—— 与 scan-market/l4-stock 的壳同一形态,零判断。
    // 2026-08-05 事故后壳类缺省统一升 sonnet(同族教训见 scan-market.js/l4-stock.js 顶部注)。
    { agentType: 'general-purpose', ...AG('gp_shell'), label, phase: ph })
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
  `首覆建档:${code} ${name}(${sector})· 分析日 ${date}。骨架:${DP};prefetch:${CTX}/knowledge/dossiers/_prefetch/${code}.json;slim 若在:${CTX}/${code}.*_${date}_slim.md(Glob 找,含 _slim_deep)。按你的人设只填四个 LLM 节(<!-- LLM:待首覆 --> 处)与摘要叙事锚,不改确定性节。返回 code/initiated/summary_tokens/uncertainty。`,
  { agentType: 'dossier-init', ...AG('dossier_init'), label: `init:${code}`, phase: 'Initiate', schema: INIT })
if (!r || !r.initiated) return { code, initiated: false, issues: ['agent 未完成或未回传'] }

phase('Lint')
const LINT = { type: 'object', required: ['ok'],
  properties: { ok: { type: 'boolean' }, reason: { type: 'string' } } }
const lint = await agent(
  `在仓库根目录执行:\n\n\`\`\`\nuv run --no-sync python -c "from autoresearch.dossier import schema; import json, pathlib; t=pathlib.Path('${DP}').read_text(encoding='utf-8'); iss=schema.lint_dossier(t); print(json.dumps({'ok': not iss, 'reason': ';'.join(iss)[:200]}, ensure_ascii=False))"\n\`\`\`\n\n它打印一行 JSON,把最后一行 JSON 原样作为结构化返回。`,
  // lint 判据全在确定性 CLI(schema.lint_dossier)里,agent 只转述 JSON(gp_shell_json 壳)
  { agentType: 'general-purpose', ...AG('gp_shell_json'), label: `lint:${code}`, phase: 'Lint', schema: LINT })
return { code, initiated: true, issues: lint && !lint.ok ? [lint.reason] : [] }
