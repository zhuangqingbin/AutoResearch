# session_v1 全扫首跑读数（2026-10-02，Claude 宿主）

分析日 2026-09-30（国庆休市，D1=10-08 收盘、D2=10-09 开盘），生产配置（`sentinel.auto_below=0.03`、`l4.max_cards=10`、持仓 688981/300750），mailbox 执行器，`--max-parallel 8`。按 [全扫首跑打通稿](../superpowers/plans/2026-10-02-scan-session-v1-first-real-run.md) Task 8 直接跑生产根（用户裁定跳过 S1–S3 演练）。**两场都未发布**；生产账本未受影响（失败 capsule 只在 `reports_claude/scan/_failed/`）。

## 开跑前处置

| 项 | 读数 | 处置 |
|---|---|---|
| `lake/stk_factor_pro/20260930.parquet` | 09-30 20:03 预热写入 4529 行；tushare 现值 5560 行（平日 ~5556） | `scan.readiness 2026-09-30 --interval 30` → 隔离为 `.partial`，扫描重取全量 |
| `margin_detail` / `hk_hold` 当日分区 | 0 行 | 09-24/09-29 同为 0 行，属常态，未动 |
| hook 加载探针 | 未绑定 `l3-repair` 读 README 被 `AGENT_INPUT_BOUNDARY` 拒 | 通过 |

## Run 1 · `20261002T113256809876Z` · FAILED（gate1 后）

墙钟 19:32:56 → ~19:51。frame 2 分钟、prelude ~6 分钟，GATE1 判 FULL。

| 缺陷 | 现象 | 根因 | 处置 |
|---|---|---|---|
| R1-1 行业 brief 方向词误判 | 交稿的 8/8 份 brief 全部 `DomainValidationError: sector lite terrain contains directional language` → `scan.l3.rank` 依赖全 BLOCKED | `.claude/agents/sector-brief.md:24` 模板**强制**写「主动买卖单净流入合计」，`validation._SECTOR_DIRECTIONS` 与 `domain_ops._SECTOR_DIRECTIONS` 含裸「买卖」（9c3e407 迁移时引入；legacy 从不对 agent 稿跑此正则） | 两处改 `买卖(?!单)`；`tests/session_agent/test_sector_products.py` +6 例（模板原文过两处校验 ×2；买卖建议/买卖点/回避/看空仍拒 ×4），先红后绿、两引擎；四片回归 1054/3256/1426/1990 passed、0 failed；8 份真实 brief 重放全放行。**未提交** |
| R1-2 宿主绑定竞态 | 一条消息并行派 7 个 agent，绑定在其后；agent 13–17 s 内两次读被拒即收工 | 工具调用随生成流式执行，每份 ~4K 字 prompt 生成 ~16 s，绑定排在最后 | 宿主侧改法（无代码改动）：run 级后台监视器在 agent transcript 出现后 0.1–0.3 s 内进程内调用 `mailbox_cli.mailbox_command(bind-access)`，并校验 prompt 逐字一致 |

## Run 2 · `20261002T115442312994Z` · FAILED（BLOCKED，last_reliable_checkpoint=gate2）

墙钟 19:54:42 → 21:41（1 h 47 m）。推理派发 33 次（市场研判 1、行业 brief 9、L3 1、情报 11、卡 11）；监视器接管后 23 次绑定全部 `spawn_to_bound ≤0.5 s`、`prompt_verbatim=True`。行业段前 8 个 agent 仍因 R1-2 晚绑定收工，补绑后 SendMessage 续跑（complete 之前，证据未破）。

阶段读数：GATE1 FULL（健康上涨 6.5%）；L0 4330 → 召回 1001 → L2 208；9 个行业 brief 全 SUCCEEDED（R1-1 修复在真跑生效）；L3 27 分钟，入围 6/7（宁缺毋滥）；GATE2 后 L4 11 只（入围 6 + composite 3 + 持仓 2）。

L4 卡（未发布、机检未通过的 3 张标 ✗）：

| 代码 | 名称 | 评级 | FINAL | 停 | 三门（主力/业绩/估值） | task |
|---|---|---|---|---|---|---|
| 688578 | 艾力斯 | Hold | HOLD | P3·其他 | P/P/P | ✓ |
| 688222 | 成都先导 | Hold | HOLD | P3·估值透支 | P/P/F | ✓ |
| 600276 | 恒瑞医药 | Hold | HOLD | P3·题材透支 | P/F/P | ✓ |
| 000498 | 山东路桥 | Hold | HOLD | P3·基本面恶化 | P/F/P | ✓ |
| 688073 | 毕得医药 | Hold | HOLD | P3·其他 | F/P/P | ✓ |
| 603235 | 天新药业 | Hold | HOLD | P3·其他 | P/F/P | ✓ |
| 002246 | 北化股份 | Hold | HOLD | P3·其他 | F/P/P | ✓ |
| 600719 | 大连热电 | Underweight | SELL | P3·估值透支 | F/F/F | ✓ |
| 688202 | 美迪西 | Underweight | SELL | P3·估值透支 | P/P/F | ✗ |
| 688981 | 中芯国际（持仓） | Underweight | SELL | 满卡 P0–P5 | F/P/F | ✗ |
| 300750 | 宁德时代（持仓） | Hold | HOLD | 满卡 P0–P5 | F/P/P | ✗ |

### 阻断缺陷 R2-1：卡片断言有效投影 × 两条评级校验（S01 缺口在真跑的后果）

- `news/card_claims.evaluate_card_claims`：本卡新 `declarations` 一律 `verdict=UNKNOWN, reason=SOURCE_NOT_BOUND`；任何 `(target, support_group)` 的 REQUIRED 引用只要不是 PASS，有效卡把该维度改为 `未核`（0 分）/ 门改 `UNKNOWN`。
- `common/card_decision.validate_card_decision(effective_card)` 随后：①评级 ≠ 有效卡建议且 `rating_deviation_reason` 空 → 拒；②`holding` 且 盈利质量/偿付 为 `未核` → `PINNED_QUALITY_UNREVIEWED`。
- 后果：照卡面契约（「本卡新事实均须映射」）把维度映射到新声明的卡，机器视图恒为全 `未核` → 建议 Hold。**持仓卡恒挂**（688981 Underweight、300750 Hold 都挂，后者证明与评级无关）；非 Hold 卡恒挂（688202）。600719 能过只因它没有声明新事实；Hold 卡能过是因为建议恰好也是 Hold。
- 生产配置固定两只持仓 → **在此缺口关闭前，session_v1 全扫无法发布**。
- 既有立场：[剩余开发计划 §8](../superpowers/plans/2026-10-01-real-host-acceptance-remaining-development-plan.md) 认定 SOURCE_NOT_BOUND 判定正确、正解是 S01 来源资格接线（首版只打通一种事实类型），且「不能仅靠让 agent 填偏离理由维持原评级」。该计划未覆盖「复核过但未机证」与「未复核」在持仓完整性检查里被同一个 `未核` 编码的问题。
- 演练 S2（哨兵持仓，单张持仓卡）本可用一张卡的成本暴露此缺陷；跳过演练是本次的代价。

### 非阻断观察（记账，待裁）

1. 工具登记与 agent 定义不一致：macro-brief、l4-card 定义带 WebSearch，任务未登记 → 全部被拒（卡片侧即矩阵稿 E5）；sector-brief 的 Glob 未登记（查 ♻️ 复用文件被拒）。
2. 情报正文未进卡片输入：卡只拿到 `intel_status`（断言 id+哈希、全 UNKNOWN、无原文），11 份情报员产出实际未被决策卡消费。
3. l4-intel 无 Read 工具，读不到登记给它的 `prompt.md`（档案已知底摘要）。
4. L4 slim/deep 在 begin 之后（12:49–12:50Z）取数，晚于冻结 `knowledge_cutoff`（11:54Z）；数据内容均为 09-30 及以前。
5. 子 agent 用量（任务通知 `subagent_tokens`，非 usage_harvest 口径）：Run 2 合计约 3.2M（情报 11 份 ~1.34M、卡 11 张 ~1.31M、行业 ~0.33M、L3 0.12M、研判 0.07M）。

## R2-1 处置（2026-10-02 晚，用户批准，未提交）

重放 11 张卡的审计附件，修正上文一处表述：11 张卡的全部断言 verdict 都是 UNKNOWN；被降级的只有把事实映射到维度/门的 5 张（688222、000498 各降 7 项后建议仍是 Hold 才过；688202 降 7 项、688981/300750 各降 9 项被拒），其余 6 张没映射，机检直接采信自评。即旧规则的实际效果是「列证据的卡被拒、不列的放行」。

改法：区分两种「未证实」，引入复核视图（`reviewed_card`）。
- `news/card_claims.evaluate_card_claims`：缺口照旧全部记入 `usage.gaps`，有效卡（机器核验视图）照旧降级；复核视图只在缺失前提**全部**是本卡自有声明时保留模型复核值。任何未证的上游前提、或没有 REQUIRED 前提的组，复核视图同样降级。
- `common/card_decision.validate_decision_text`：持仓质量完整性、评级与评分一致这两条复核检查读复核视图；`machine_suggestion` 仍由核验视图计算并记录。
- `scan/l4/parsers._finalist_row`：评级校验与 `rubric_suggest` 读复核视图；送 E6 的 `_card_facts.gate_states` 仍取核验视图，不会因此多出买入。
- 不变：Buy/Overweight 缺支持即拒（`validate_claimed_decision` 原有门）、三门、E6、主尺、usage 附件格式。

| 情况 | 旧 | 新 |
|---|---|---|
| Buy/OW 支撑事实机器核不了 | 拒 | 拒 |
| 持仓质量/偿付依赖未证实的上游断言 | 拒 | 拒 |
| 持仓卡自己未给质量/偿付打分 | 拒 | 拒 |
| 评级与卡面自评不符且无偏离理由 | 映射卡放行（机器视图恰为 Hold）/ 不映射卡拒 | 一律拒 |
| 持仓卡读了 deep 打了分，事实仅本卡声明（688981/300750） | 拒 | 过，缺口披露，入场 PENDING_EVIDENCE |
| 评级与自评一致，机器视图另有建议（688202） | 拒 | 过，记录机器视图建议 |

验证：
- 新增 8 例（`tests/news/test_card_claims.py` 4 例、`tests/session_agent/test_card_claim_uses.py` 4 例），先红后绿；运行时变异 5 项（复核检查读核验视图 / E6 门取复核视图 / 机器建议取复核视图 / 自有声明判定写反 / 去掉「无 REQUIRED 即降级」）均至少一例变红。
- 11 张真实卡离线重放（失败现场副本）：11/11 通过；688202、688981 记录机器视图建议 Hold；5 张映射卡 `entry_status=PENDING_EVIDENCE`。
- 回归：codex 全量 9038 passed / 0 failed；Claude 引擎 `tests/news tests/common tests/scan tests/session_agent` 失败集合与改动前快照逐条相同（138 条，均为测试夹具写死 codex 的引擎环境红），通过数 +14 = 新增测试数。
- 与 [剩余开发计划 §8](../superpowers/plans/2026-10-01-real-host-acceptance-remaining-development-plan.md)「SOURCE_NOT_BOUND 判定正确、等 S01」的立场部分相左：判定本身不变，变的是两条复核检查读哪个视图。需知会 Codex 侧。

### 顺带发现：测试隔离漏洞（已修）

`tests/session_agent/test_dossier_publish.py::test_dossier_publish_detects_concurrent_manual_creation_and_is_idempotent` 调 `publish_dossier` 不传 `pool_path`，会提交到**当前目录、当前引擎的生产覆盖池**（`pool.POOL_PATH`）。今晚 Claude 引擎跑测试时把 `context_claude/knowledge/coverage_pool.json` 重写成紧凑格式（数据未变：用 indent=1 重序列化可复现 21:42 的哈希），并新建了空的 `context_claude/_published_state/locks/*.lock`。处置：覆盖池按原字节恢复（sha256 `5b1a9e58…` 一致），空锁目录删除；测试改为 monkeypatch `pool.POOL_PATH` 到临时目录，演练根上先演示旧测试会造出真实覆盖池（红），修后两引擎运行生产覆盖池哈希与 mtime 不变（绿）。Codex 覆盖池 mtime 仍为 09-14，未被触及。

## 结论

Run 机制（begin/runner/mailbox/绑定/校验/finalize）已在真实宿主跑通到 L4 完；R1-1、R2-1 已修（未提交）。下一步：在演练根新开会话按 `context_claude/development/20261002-scan-first-run/S2-drill-runbook.md` 跑 S2（哨兵 + 单只持仓），验证收尾段（汇总/GATE4/发布/verify-report）后再重跑生产全扫。演练根已同步到快照 `ac4f750`。
