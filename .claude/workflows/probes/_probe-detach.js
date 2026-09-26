export const meta = {
  name: '_probe-detach',
  description: '2026-09-26 detach 冒烟:真 sonnet 中继壳跑 >100s 命令,detached() 须循环到 COMPLETED 并带回命令自己的 JSON',
  phases: [{ title: 'Probe', detail: '130s 命令 → ≥2 轮有界等待 → COMPLETED + result' }],
}

// 住 probes/ 的理由见 _probe-parent.js 顶部注(非递归 glob 的生产测试不审它)。
// detached() 与 scan-market.js 的同名函数逐字相同;这里只换掉 ENGINE/RUN_ID/AG 的来源。
const A = (typeof args === 'string' && args ? JSON.parse(args) : args) || {}
const ENGINE = 'claude'
const RUN_ID = A.run_id || '20260926T000000000000Z'
const AG = () => ({ model: 'sonnet', effort: 'low' })

const shq = (s) => `'${String(s).replace(/'/g, `'\\''`)}'`
const DETACHED = { type: 'object', required: ['state', 'key'], additionalProperties: false,
  properties: { state: { type: 'string', enum: ['RUNNING', 'COMPLETED', 'FAILED', 'LOST'] },
    key: { type: 'string' }, exit_code: { type: ['integer', 'null'] }, tail: { type: 'string' },
    stderr_tail: { type: 'string' }, result: {},
    expect_file: { type: ['boolean', 'null'] }, reason: { type: 'string' } } }
const DETACH_TERMINAL = ['COMPLETED', 'FAILED', 'LOST']
async function detached(key, cmd, label, phaseName, { expectFile = null, maxRounds = 40 } = {}) {
  const call = `AUTORESEARCH_ENGINE=${ENGINE} uv run --no-sync python -m autoresearch.trace.detach ` +
    `--run-id ${RUN_ID} --key ${key} --wait-seconds 100` +
    `${expectFile ? ` --expect-file ${expectFile}` : ''} --shell ${shq(cmd)}`
  let misses = 0
  // Workflow 运行时禁用 Date.now()(破坏 resume;09-26 探针实测)→ 用轮数封顶,每轮 ≤~100s 有界等待。
  for (let i = 1; i <= maxRounds; i++) {
    const res = await agent(
      `执行下面这条命令。它只打印一行 JSON 对象:把**这个对象本身**逐字段照抄作为结构化返回 —— 不要包一层、不要改写 state 的值、不要添加字段。不要判断、不要解释、不要重试或改写命令。\n` +
      `**前台执行**:Bash 调用不要设 run_in_background(它最多约 100 秒就返回;真正的长任务已在后台独立运行)。\n` +
      `**逐字节原样执行:不得添加 2>&1、tee、管道,不得改写或增删任何重定向。**\n\n\`\`\`\n${call}\n\`\`\``,
      { agentType: 'general-purpose', ...AG('gp_shell_json'), label: i > 1 ? `${label}#${i}` : label,
        schema: DETACHED, ...(phaseName ? { phase: phaseName } : {}) })
      .catch(() => null)
    log(`[probe] ${label} 第 ${i} 轮 → ${res ? res.state : 'null'}`)
    if (res && DETACH_TERMINAL.includes(res.state)) return res
    misses = (res && typeof res.state === 'string') ? 0 : misses + 1   // 形状不对 = 没回报
    if (misses >= 3) return { state: 'LOST', key, exit_code: null, tail: '', reason: '中继壳连续 3 次无有效回报' }
  }
  return { state: 'TIMEOUT', key, exit_code: null, tail: '', reason: `${maxRounds} 轮有界等待内未到终态` }
}

phase('Probe')
const key = A.key || 'probe-130s'
const res = await detached(key, `echo start; sleep 130; echo '{"ok":true,"it":"works"}'`, 'probe-130s', 'Probe',
  { maxRounds: 6 })
log(`[probe] 终态 ${JSON.stringify(res).slice(0, 400)}`)
return res
