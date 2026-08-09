# Wave12-T31 · 嵌套 workflow 十项探针裁决(2026-08-09)

> **一句话**:十项**全部 UNTESTED**(PASS 0 · FAIL 0)→ **T32(L4 派发下沉)标 SKIPPED,维持主会话派发**。
> 这不是失败,是证伪省钱(Wave4 家训);`ready_for_ab()` 返回 `capability=BLOCKED / verdict=NOT_READY`。

任务书:`.superpowers/sdd/2026-08-08-wave12-implementation-plan/task-31-brief.md`
探针模块:`autoresearch/research/nested_probe.py`(2026-08-04 批5 建好未跑)
账本:`context/research/nested_probe.json`(本次首次写入 `observed_at`;`context/` 已 gitignore,故本文件是可提交的存证)

---

## 1. 为什么跑不了 —— 这一条才是本次的真结论

**执行上下文(subagent)没有 `Workflow` 工具**,而这是本仓唯一能拉起 workflow 的入口
(`.claude/skills/scan-market/SKILL.md:104` 的 `Workflow({scriptPath: '.claude/workflows/l4-stock.js', args})`)。

实测两条独立证据:

| 检查 | 结果 |
|---|---|
| 本 agent `ToolSearch("select:Workflow")` | `No matching deferred tools found` |
| 另派一个 agent 独立复验(要求它真调一次) | `WORKFLOW_TOOL: DEFERRED-NOT-FOUND` / `RUN_ATTEMPTED: NO` |
| `claude --help` 子命令表 | 无 workflow 运行子命令(只有 agents/auth/mcp/plugin/project/…) |

也就是说:**十项探针不是"跑了没过",是"这个位置根本按不动那个按钮"**。
按模块自己的纪律,这只能记 `UNTESTED` —— 记 FAIL 会污染"嵌套能力有问题"这个结论
(它没有被证伪,只是没被证实);记 PASS 更是模块 docstring 明令禁止的那件事。

## 2. 顺带挖到的:harness 自述契约(**是文档,不是实测**)

从 `~/.local/share/claude/versions/2.1.226` 二进制静态串里取到 harness 对 `workflow()` 的**自述**:

```
workflow(nameOrRef: string | {scriptPath: string}, args?: any): Promise<any>
  — run another workflow inline as a sub-step and return whatever it returns.
    Pass a name to invoke a saved workflow (same registry as {name: "..."}),
    or {scriptPath} to run a script file you Wrote earlier.
    The child shares this run's concurrency cap, agent counter, abort signal,
    and token budget — its agents appear under a "<name>" group in /workflows
    and its tokens count toward budget.spent(). The args param becomes the
    child's `args` global. Nesting is one level only: workflow() inside a
    child throws. Throws on unknown name / unreadable scriptPath / child
    syntax error; catch to handle gracefully.
```

以及**一级上限**的实现串(子 workflow 拿到的是一个恒 reject 的 `workflow`):

```
workflow: () => Promise.reject(Error(
  "workflow() cannot be called from within a child workflow — nesting is
   limited to one level. Inline the inner script or call its agents directly."))
```

**这段材料改变了什么**:设计稿 §4.3 原话「嵌套 workflow 的基本调用、恢复和故障语义均未在本仓得到验证」
里的"存在性"部分现在有了来源可查的依据 —— 一级嵌套确实是 harness 的**声明支持面**,
设计稿 B2 写的「一级嵌套,harness 契约允许」不是臆测。

**这段材料没有改变什么**(必须写清,否则就成了下一个"绿灯不等于有灯"):

- 它是**产品文档**,不是本仓跑出来的行为。`concurrency cap 共享`、`abort signal 共享`
  这两句尤其危险 —— 「共享」是架构声明,**实际并行度**和**父取消后子是否真的停**
  是两个必须眼见为实的量,恰恰又是 §4.3 最担心的两格(「变孤儿继续烧钱」)。
- 四项在 harness 文档层**完全空白**:子超时语义、父被 kill 后子的归宿、
  `resumeFromRunId` 对嵌套是否有效、以及本仓自有的 task-book lease / 幂等。
  空白 ≠ 不支持,但更 ≠ 支持。

## 3. 十项逐项裁决表

| # | 探针 | 裁决 | 一句话依据 |
|---|---|---|---|
| 1 | basic_invocation 基本调用 | ⏳ **UNTESTED** | 无 Workflow 工具跑不了;harness 声明支持 + 有专门的一级上限拒绝串 = 存在性有据、行为未跑 |
| 2 | args_and_result 形状与大小上限 | ⏳ **UNTESTED** | 同上;且**大小上限 harness 零声明**,只能实测 |
| 3 | concurrency_cap 并发上限共享 | ⏳ **UNTESTED** | harness 称"共享父的 cap",实际并行度/排队行为未测 |
| 4 | parent_cancel 父取消 | ⏳ **UNTESTED** | harness 称"共享 abort signal";孤儿场景未跑 —— 这格最不该按文档推定 |
| 5 | child_failure 子失败 | ⏳ **UNTESTED** | harness 只说了"未知名/不可读/语法错会抛";**运行期子抛错父看到什么没说** |
| 6 | timeout 子超时 | ⏳ **UNTESTED** | harness 文档层**空白** |
| 7 | parent_death 父被 kill | ⏳ **UNTESTED** | harness 文档层**空白**(abort signal 只覆盖优雅取消,不覆盖 SIGKILL) |
| 8 | resume_from_run_id | ⏳ **UNTESTED** | `resumeFromRunId` 字段存在,**对嵌套是否有效零声明** |
| 9 | task_book_lease | ⏳ **UNTESTED** | 本仓自有语义(`autoresearch/scan/l4_tasks.py` preflight/lease),零 LLM 玩具测不了 |
| 10 | idempotent_rerun 幂等 | ⏳ **UNTESTED** | 业务语义(同一票不得出两张卡),必须真扫描日验证 |

**合计:PASS 0 · FAIL 0 · UNTESTED 10 · `capability_verified=False`。**

## 4. 裁决

**T32(B2 · L4 派发下沉)→ SKIPPED。** 维持现行「主会话一次性全派 N 个 `l4-stock.js`」。

依据链:
1. 任务书 T31 Step 1:「任一 FAIL/UNTESTED → T32 整条标 SKIPPED」——现在是 10/10 UNTESTED。
2. `nested_probe.ready_for_ab()` 实跑返回 `capability: BLOCKED` / `verdict: NOT_READY`。
3. 设计稿 §4.3 的三门(capability / 恢复性不劣 / 实测成本)里,第一门就没过;
   后两门本来就只能靠 A/B 实测,**不能由 capability 推出**。
4. 附带提醒(设计稿原文):主会话份额降至 15%、每日省 $5–8 **仍是待测假设**,
   当前实测主会话份额 32.7%。别把假设写成收益。

## 5. 已就位的复跑装备(下一个有 `Workflow` 工具的会话可直接跑)

`.claude/workflows/probes/_probe-parent.js` + `_probe-child.js` —— **零 `agent()` 调用 = 零 token**,
可以随便重跑。一次调用覆盖探针 1/2/3/5 + 二级嵌套上限:

```
Workflow({scriptPath: '.claude/workflows/probes/_probe-parent.js', args: {n: 3, pad: 100}})
```

返回形如 `{basic, args_result, concurrency, child_failure, nesting}`,逐项落账:

```
uv run --no-sync python -m autoresearch.research.nested_probe record basic_invocation PASS --evidence "..."
uv run --no-sync python -m autoresearch.research.nested_probe status
```

**放在 `probes/` 子目录的原因**(不是随手放的):三处生产测试
`tests/test_agent_defs.py:279`、`tests/test_workflow_js_syntax.py:151`、
`tests/scan/test_workflow_syntax.py:24` 都用**非递归** `glob("*.js")` 枚举 workflows 根 ——
探针放根目录会被当生产 workflow 一起审,也会污染 `workflow('<name>')` 的可用名单。
代价:子目录文件不进名字注册表,只能用 `{scriptPath}` 形式互调(harness 两种形式都收)。

语法已用 **AsyncFunction 解析探针**验过(`node --check` 对 ESM export + 顶层 return 的 workflow js
是**永不变红的假绿灯**,本仓 Wave35 实测判例),并且对该探针做了变异验证:
把 `_probe-child.js` 的 return 打坏 → 探针 exit 1(`Unexpected token ','`),确认它有鉴别力。

**剩下 5 项即使玩具跑通也仍是 UNTESTED**:timeout / parent_death / resumeFromRunId 需要
真跑 + 外力干预;task_book_lease / idempotent_rerun 需要一次带 task book 的真实扫描日。
换句话说:**就算下次玩具全绿,T32 的门也还没开** —— 别把"5/10 过了"读成"可以换调度"。

## 6. 未决 / 疑虑

- **权威冲突未消**:`nested_probe.py` docstring 记着与 Wave9「L4 单工作流全链」直接重叠,
  继承矩阵标「需用户重裁」(候选 `G43_nested_l4_dispatch`)。本次裁决只处理 capability 这一门,
  **没有**触碰那条重裁事项。
- **本裁决的边界**:它只证明"这个 subagent 上下文跑不动",不证明"主会话也跑不动"。
  主会话有 `Workflow` 工具,大概率能跑玩具父子。所以这份 SKIPPED 的正确读法是
  **「本轮不做,复跑装备已备好」**,不是「嵌套方案已被否决」。
