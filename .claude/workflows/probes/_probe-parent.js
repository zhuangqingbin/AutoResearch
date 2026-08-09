export const meta = {
  name: '_probe-parent',
  description: 'Wave12-T31 嵌套探针父 workflow(零 agent 调用 = 零 LLM 成本;不属生产调度)',
  phases: [{ title: 'Probe', detail: '调子 workflow:基本调用 / args-result / 并发 / 子失败 / 嵌套上限' }],
}

// T31 探针 1/2/3/5 + 嵌套上限:父能否调起子、args 与 result 形状、N 子并发、子失败父看到什么。
// **零 agent() 调用**——本对父子只跑 JS,不烧一个 token(所以可以随便重跑)。
//
// 住在 `.claude/workflows/probes/`(子目录)而不是 workflows 根:三处生产测试
// (`tests/test_agent_defs.py:279`、`tests/test_workflow_js_syntax.py:151`、
// `tests/scan/test_workflow_syntax.py:24`)都用**非递归** `glob("*.js")` 枚举 workflows 根,
// 探针放根目录会被当生产 workflow 一起审、也会污染 `workflow('<name>')` 的可用名单。
// 代价:子目录文件不进名字注册表,只能用 `{scriptPath}` 形式互调(harness 两种都收)。
//
// harness 自述契约(2.1.226 二进制静态串,docs/research/2026-08-09-nested-probe-verdict.md 存证):
//   workflow(nameOrRef: string | {scriptPath: string}, args?: any): Promise<any>
//   子共享父的 concurrency cap / agent counter / abort signal / token budget;
//   args 成为子的 `args` 全局;**嵌套只有一级,子里再调 workflow() 会抛**;
//   未知名/不可读 scriptPath/子语法错 → 抛,可 catch。
// ⚠️ 以上是**文档,不是实测**。本文件存在的意义就是把它跑成实测。
//
// 跑法(**只有能用 Workflow 工具的会话才跑得动**,如主会话;subagent 无此工具):
//   Workflow({scriptPath: '.claude/workflows/probes/_probe-parent.js', args: {n: 3, pad: 100}})
// 拿到返回后逐项落账:
//   uv run --no-sync python -m autoresearch.research.nested_probe record <key> <PASS|FAIL> --evidence "..."
const CHILD = { scriptPath: '.claude/workflows/probes/_probe-child.js' }
const A = (typeof args === 'string' && args ? JSON.parse(args) : args) || {}
phase('Probe')
const out = { basic: null, args_result: null, concurrency: null, child_failure: null, nesting: null }

// ① basic_invocation + ② args_and_result
try {
  const t0 = Date.now()
  const r = await workflow(CHILD, { x: 'hello', pad: 'p'.repeat(A.pad ?? 100) })
  out.basic = { ok: true, ms: Date.now() - t0, resultType: typeof r,
                keys: r && typeof r === 'object' ? Object.keys(r) : null }
  out.args_result = { echoed: r && r.echoed, len: r && r.len, roundtrip: !!r && r.echoed === 'hello' }
} catch (e) {
  out.basic = { ok: false, error: String((e && e.message) || e).slice(0, 300) }
}

// ③ concurrency_cap:N 个子同时挂,量墙钟(串行 vs 并行由耗时比看得出来)
try {
  const n = A.n ?? 3
  const t0 = Date.now()
  const rs = await Promise.all(Array.from({ length: n }, (_, i) => workflow(CHILD, { x: `c${i}` })))
  out.concurrency = { n, ms: Date.now() - t0, allOk: rs.every((r) => r && r.ok),
                      echoed: rs.map((r) => r && r.echoed) }
} catch (e) {
  out.concurrency = { ok: false, error: String((e && e.message) || e).slice(0, 300) }
}

// ⑤ child_failure:父看到异常?空结果?还是静默?(静默是最坏的那种)
try {
  const r = await workflow(CHILD, { boom: 'intentional' })
  out.child_failure = { sawException: false,
                        result: r === null ? 'null' : JSON.stringify(r).slice(0, 200) }
} catch (e) {
  out.child_failure = { sawException: true, error: String((e && e.message) || e).slice(0, 300) }
}

// 嵌套上限:子里再调 workflow()(harness 自述「一级」——这里真跑一遍)
try {
  const r = await workflow(CHILD, { x: 'nest', tryNest: true })
  out.nesting = (r && r.nestedError) || 'CHILD_RETURNED_NO_NESTED_ERROR'
} catch (e) {
  out.nesting = `PARENT_SAW:${String((e && e.message) || e).slice(0, 200)}`
}

log(`[probe-parent] ${JSON.stringify(out)}`)
return out
