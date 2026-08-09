export const meta = {
  name: '_probe-child',
  description: 'Wave12-T31 嵌套探针子 workflow(零 agent 调用 = 零 LLM 成本;不属生产调度)',
  phases: [{ title: 'Echo', detail: 'args 原样回传 + 可选抛错 + 可选二级嵌套' }],
}

// T31 探针子件:args-result 形状 / 子失败语义 / 二级嵌套上限。**不调 agent(),零 token**。
// 住在 probes/ 子目录的理由见 _probe-parent.js 顶部注。
const A = (typeof args === 'string' && args ? JSON.parse(args) : args) || {}
phase('Echo')
log(`[probe-child] got args=${JSON.stringify(A).slice(0, 200)}`)
if (A.boom) throw new Error(`probe-child 故意失败:${A.boom}`)
// 二级嵌套:子里再调 workflow() —— harness 静态串声称「nesting is limited to one level」,
// 这里是把那句话真跑一遍的地方(拿到的 message 就是探针 basic_invocation 的边界证据)。
let nestedError = null
if (A.tryNest) {
  try {
    await workflow({ scriptPath: '.claude/workflows/probes/_probe-child.js' }, { x: 'grandchild' })
    nestedError = 'NO_ERROR(二级嵌套竟然成功——与 harness 自述矛盾,按此改写裁决)'
  } catch (e) {
    nestedError = String((e && e.message) || e).slice(0, 300)
  }
}
return { ok: true, echoed: A.x ?? null, len: JSON.stringify(A).length, nestedError }
