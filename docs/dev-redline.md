# 开发红线:token 性价比

2026-10-10 用户裁定,对之后所有开发生效(两个引擎同样适用)。设计与推演见
`docs/superpowers/specs/2026-10-10-token-growth-guard-design.md`,本页只放规则、机器件和命令。

**性价比 = 决策增量 / token。** 本仓每天只有两个决策:当日一笔相对 BUY,和 📌 持仓的红线盯梢。
一笔推理花费如果改变不了它们,就要先证明它值得。

**红线的红线:** 每条都必须同时有「事故 / 机器件 / 会红的量」三栏。只写进文档、没有机器件的规则不叫红线,
写进下面的「开发守则」。

## 八条红线

| # | 红线 | 起因 | 机器件(住哪) | 会红的量 |
|---|---|---|---|---|
| R1 | 推理之前先零推理回放 | 10-03 r4、10-07 r5:研究花完才死在校验 | `scan.replay_gate`:开场前用当前校验器重验上一场已接受产物 | 回归 > 0 → `REFUSED_REPLAY` |
| R2 | 宿主零研究 | 10-07 主会话占 62% / 84–90% | `scan.redline` 宿主行 | 宿主加权输入 > `budgets.envelope.host_weighted_max` → FAIL |
| R3 | 档位只能带着等价读数改 | 10-03 别名静默换代,同 effort 输出 1.9 万 → 5.1 万 | `contracts/tier_lock.json` + lint R12;redline 对实际模型 | 配置 ≠ 锁、变更缺 `equivalence_ref`、实际模型不在锁里 → 红 |
| R4 | 每笔推理花费要有决策消费者 | FN-1 家族;intel 死票门 0 命中 | `agents.<role>.consumer` + lint R14;redline 消费图 | 缺消费者 → 红;连续 5 场无人消费 → 待裁清单 |
| R5 | 预算是契约不是告警 | 10-08 第 2 场撞额度 | lint R11(静态成本清单)+ redline 场线 + 断路器 | 超 `budgets.declared` → 改动时 exit 2 / 场后 FAIL → 下一场 `REFUSED_BUDGET` |
| R6 | 失败隔离到最小单元,不回退整场重跑 | 10-02~10-07 一个契约错 = 整场重来 | 补丁 04、`recover-task`、`--resume-run-id`;redline 记同日重跑 | 同日第 2 场起 `SAME_DATE_RERUN` |
| R7 | 上下文只传指针与增量,前导与 agent 文件有预算 | 09-15 研究 agent 翻源码;AGENTS.md / MEMORY.md 注入每次调用 | `session.preamble`;`session.max_turns`;lint R10;redline 前导指纹 + 场中前导守卫 | agent 文件超 `budgets.agent_chars` → 红;前导 ×2 → 停派 `PREFIX_DRIFT` |
| R8 | 计量先于优化,估算不入台账,开发会话也算钱 | 账本 62 行重复;诊断量错对象;10-09 周额度 93% | `research.token_ledger` 周账本;复审规则 | 降本改动无前后真计量 → 复审拒 |

预算线只降不升(lint R13,对 git HEAD):agent 不能自己抬线,人改 `budgets.declared` 那一行并提交即可。
每场 redline 读数在本场低于 线 / `ratchet_margin` 时会给出 `ratchet_suggestion`,按它下调。

## 命令

```bash
# 场后读数(scan_run 已自动跑;手动重算 / 只看不写)
uv run --no-sync python -m autoresearch.scan.redline build --run-id <RUN_ID> --no-write
uv run --no-sync python -m autoresearch.scan.redline status          # 当前断路器
scripts/scan_run.sh --engine <claude|codex> --ack-redline <RUN_ID>   # 看过后放行下一场
scripts/scan_run.sh --engine <e> --resume-run-id <RUN_ID> --ack-redline <RUN_ID>   # 恢复被前导守卫停下的 run

# 改动时
uv run --no-sync python -m autoresearch.scan.config_standard --hook  # R2–R14 + 一行成本估算
uv run --no-sync python -m autoresearch.scan.token_bom               # 当前配置下一场的估算
uv run --no-sync python -m autoresearch.scan.token_rules lock --add-missing   # 新角色补档位锁第一条
uv run --no-sync python -m autoresearch.scan.replay_gate             # 手动跑回放门

# 周账本(两引擎 · 按项目 · 生产 / 开发)
uv run --no-sync python -m autoresearch.research.token_ledger --since <YYYY-MM-DD>
```

改档位的流程:先按 `docs/session-agent/research-quality-workflow.md` 的 `stage_effort_v1` 做等价读数,
读数落 `docs/research/` 后,在 `tier_lock.json` 对应键追加一条 `{model, effort, since, equivalence_ref}`,再改配置。

## 开发守则(没有机器件,靠周账本曝光)

1. 新增 LLM 角色、工具回合或 prompt 段落之前,先写清「线程 × 调用 × 上下文」和谁消费它;agent 文件先登记字符预算。
2. 量法脚本第一次用就放进 `autoresearch/research/`,量过的读数进 `docs/research/`,下次先查再量。
3. 读大文件用范围(grep、`sed -n`、Read 的 offset),日志只看尾部,全量测试只看摘要。
4. 子 agent 只派给能拆开、各自独立的大块任务;派之前写一句「为什么不在本会话做」,复审派一个不派一排。
5. 计划稿给实施者的部分控制在一屏(任务 + 验收),证据与推演放附录,子 agent 只读前者。
