# Claude Code 真实宿主验收记录（2026-10-01）

本记录只覆盖 Claude 宿主在本会话内实际产生的证据。Codex 半边须由 Codex 会话自行采集，本会话没有读取 `context_codex/`、`reports_codex/`。`session_v1` 仍是显式 PILOT。

## 结论

| 项目 | 机器结果 |
|---|---|
| 宿主加载 | Claude Code 2.1.285，会话 `3e9c98ad-4f3b-4911-a6c4-f0b21f1ef568`。进程启动时间晚于全部 hook 文件改动时间 |
| 研究访问边界 | Claude 11/11 已签发并导入。`boundary_proof status` 为 11/22，Codex 11 项缺失，仍是 `PENDING_REAL_HOST_VALIDATION` |
| stock-research LITE | `stock-research:claude:lite-early-stop` 已被 `acceptance-status` 接受，固定分母 1/28，invalid 为空 |
| 默认入口 | PILOT，未切换 |

## 证据位置

- 签发者公钥指纹：`19b9fd870a7adcf8c3881d4dbb64c193db81947922cdbbc50ebb6565ff0a9899`，由 `shasum -a 256` 独立核对后登记信任。私钥位于 `context_claude/_acceptance/boundary/issuer.pem`，权限 0600。
- 边界证明：`reports_claude/_acceptance/proofs/boundary/proofs/`，共 11 份。challenge 和 hook 事件位于 `context_claude/_acceptance/boundary/{challenges,events}/`。
- 演练任务：`context_claude/_acceptance/boundary/drills/boundary-drill-20261001-claude-a/`，使用专用 boundary-canary 文件，没有用任何真实研究文件做越界探针。
  - 读写类 9 项由 l4-card 研究身份执行。
  - shell 类 2 项（arbitrary_shell、identity_spoof）由已绑定的 general-purpose 身份执行，因为 Claude 研究角色的工具表里没有 Bash。
- 验收 run：`20261001T081807747507Z`，603893.SS 瑞芯微，分析日 2026-09-30。
  - D1 为 2026-10-08 收盘，D2 为 2026-10-09 开盘，日历来自 trade_cal，国庆休市已识别。
  - 卡片结论：Hold，P3 早停（资金流出），入场为条件。
- canonical 报告：`reports_claude/analyze/runs/20261001T081807747507Z/p1/report/瑞芯微_lite.md`。
- `verify-report --level full` 结果：
  - `report_covered`、`publication_ok`、`orchestration_verified`、`integrity_ok`、`completeness_ok` 均为 true。
  - `compute_status=FULL`，`missing=[]`，`diffs=[]`。
- 隔离重放：`compute_status=FULL`，`isolation_status=ENFORCED`（macOS sandbox-exec），`diffs=[]`。
- 验收证明：`reports_claude/_acceptance/proofs/claude/stock-research/20261001T081807747507Z/lite-early-stop.json`，记录写在 `reports_claude/_acceptance/records.json`。
- shakedown run：`20261001T070429018207Z` 和 `20261001T075150140030Z`。两次都已发布，但都不作为验收证据。缺陷修复发生在 begin 之后，或存档 completeness 与重算结果不一致。

## 真实宿主暴露并已修复的缺陷

1. 边界校验器无法验证 Claude 的 Read。Claude Read 返回给模型的是带行号的渲染文本，`allowed_read`/`deep_after` 因此永远无法通过。
   - 修复：Claude 适配器从宿主写入的 `toolUseResult.file` 生成 `host_read` 摘要；`complete_host_read` 要求整文件读取、路径和字节都一致。
2. 业务深读证据有两处问题。`read_observation.ordered_tool_pairs` 不认 Claude 的 `tool_use_id`，Read 分支也只接受原文输出，导致 LITE 满卡和 scan L4 的 P4 深读在 Claude 上无法被证实。
   - 修复：同 1，并改用通用的 `tool_call_id`。
3. Claude 主 transcript 行没有原生 ordinal，finish 时主宿主证据被判为 MISSING，completeness 必然为 false。
   - 修复：`host_evidence._last_ordinal` 改用行位置，与 runner 的口径一致。
4. 卡片契约提示没有写出 `early_stop` 的非空形状，真实模型只能猜，早停卡被领域校验拒收。
   - 修复：`decision_instruction` 写明 `{"phase","reason"}` 及两张词表。
5. 取证契约把 argv 当作唯一集合校验，没有同业时 harvest 的空串参数会让 finish 失败。
   - 修复：argv 按有序命令行校验。
6. Claude 适配器忽略绑定区间。
   - 修复：区间改按行位置切片，主 transcript 证据只包含本 run 区间。
7. `stock.publish`/`stock.full.assemble` 重放时拿不到冻结的卡规则档案和 owner 任务表，回落到旧规则，replay 必然为 PARTIAL。
   - 修复：新增 `session_agent/card_semantics_context.py`，live 时冻结上下文，重放时恢复到独立的 claim handle。
8. session_v1 的 LITE 不写 `stages/card` checkpoint，存档 completeness 为 false，与重算结果冲突，核验报 `STORED_COMPLETENESS_DIFFERS`，验收证明无法写入。
   - 修复：由 `stock.validate` 以自身身份记录 `card` 阶段。只在 live run 中执行，重放跳过。

回归（codex 引擎环境，4 个分片）：7360 passed，12 skipped，0 failed。新增和改动的用例在 Claude 环境中通过；`tests/forensics/test_host_evidence.py` 原有的 4 条环境红未变。`boundary_proof.py` 属于边界策略文件，修改发生在演练之前，现有 11 份证明绑定的是修改后的策略指纹。此后再改任何策略文件，这些证明都会变为 STALE。

## 未修复、待裁定或待验证

- FULL 路径（静态发现，未经真实 run 证实）：`stock.full.assemble` 内部调用 `analyze.assemble.main()` 时，以 `stock.assemble` 身份执行 `record_stage`。这个 operation 不在 session 计划内，写守卫会抛出 `RUN_OPERATION_NOT_OWNED` 并重新抛出。FULL 的 intel、write、publish 阶段也没有 checkpoint 生产者。
- 绑定稳定性：已绑定的 transcript 在绑定后若继续增长（例如再次唤醒子 agent），finish 重新归档会覆盖归一化文件，绑定随之失效。这与文档“后续追加不改变证据字节”的承诺不符，跨引擎都受影响。因此 DOMAIN_VALIDATION_FAILED 后的同 attempt 修正在证据上不安全；非瞬时错误又不可重试，run 只能停在 BLOCKED。
- `stock.card` 的 `tool_policy=READ_WRITE`：standalone LITE 没有情报任务，卡片完全没有网查能力，与 l4-card 契约中“缺情报时可做 ≤3 条 WebSearch”不一致。这属于研究语义，需要裁定。
- analyze run 的契约 `user_config` 被当作 scan_config 校验，产生告警，agent 档位为 UNRESOLVED（回落到 frontmatter）。
- 子 agent 身份只能在创建后取得，绑定前的 1–2 次读取会被拒绝（符合设计）。验收 run 中子 agent 随后重试成功。
