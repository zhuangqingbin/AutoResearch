# session_v1 全扫第 3、4 场读数（2026-10-03，Claude 宿主）

分析日 2026-09-30（国庆休市，D1=10-08 收盘入场、D2=10-09 开盘退出），生产配置（`sentinel.auto_below=0.03`、`l4.max_cards=10`、持仓 688981/300750），mailbox 执行器，`--max-parallel 8`，主会话 Opus 5.5·max，研究角色钉版 `claude-opus-5-5` / 情报员 `claude-sonnet-5-5`（10-03 钉版后首次真跑）。用户 10-02 裁定跳过 S2 演练，两场都直接在生产根跑。**两场都未发布**；失败 capsule 在 `reports_claude/scan/_failed/`。

## 结论先行

- 编排机制本身已能跑通到 L4 全部出卡：两场共派发 60+ 次推理，全部 `spawn_to_bound ≤0.5 s`、`prompt_verbatim=True`（autobind 监视器做法稳定）。
- 10-02 的两处修复在真跑生效：行业 brief「买卖」误判未复发（18/18 brief 接受）；**R2-1 断言复核视图修复后 a1 决策卡 6/6 机检通过，含两只持仓满卡**。
- 新的阻断缺陷 **R4-1**：单阶段计划下 L4 票级重试（a2）的派发 prompt 不含卡面契约，a2 卡必然 `CONTRACT_ERROR`，而 a2 是最后一次机会 → 任何一只票走到重试，整场必 BLOCKED。
- 触发 a2 的直接原因是宿主侧容量：订阅 5 小时额度两次耗尽（429）、情报 15 分钟超时被宿主派发延迟吃掉、会话 WebSearch 预算 200 次用光。

## Run 3 · `20261003T051923360261Z` · FAILED（last_reliable_checkpoint=gate2）

13:19 begin → frame/prelude → GATE1 FULL（健康上涨 6.5%）→ 市场研判 → 9 行业 brief 全接受 → L3 入围 7 → GATE2 → L4 派 12 只。L4 情报 8 个在跑时订阅额度耗尽（另一会话的单股研究同时在耗同一额度），宿主无法领取/回交 → 各票 a1 情报 TIMEOUT、a2 情报 never-taken 3600 s → `MAX_ATTEMPTS=2` 用尽 → runner 09:26Z BLOCKED。显式 `capsule finalize --business-status FAILED` 并写明原因。无任何决策卡。

## Run 4 · `20261003T101330575299Z` · FAILED（BLOCKED，last_reliable_checkpoint=gate2）

| 阶段 | 读数 |
|---|---|
| frame/prelude | 前奏汇总与 Run 3 逐行相同（新闻目录最新 1053.5h 前；宏观 macro_state as_of 07-27 过期） |
| 市场研判 | 震荡、温度 52.4 退潮；半导体/元件资金净流出居前，银行/化学制药净流入；北向 5 日 +115 亿 vs 融资 5 日 −430 亿 |
| 行业 brief | 9/9 接受（含冶钢原料模板标签被 agent 自改「主动单」——agent 定义仍禁「买卖」，与模板冲突，待改定义） |
| L3 | 入围 7：恒瑞医药 72 / 艾力斯 70 / 成都先导 64 / 百联股份 57 / 毕得医药 56 / 苏美达 55 / 天新药业 55；备选 23；**另 10 只未写入**（契约要求全部给判断，第二次复现） |
| L4 人口 | 7 入围 + composite 3（大连热电、北化股份、美迪西）+ 📌2（中芯国际、宁德时代） |
| 中断 | 第一次 5h 额度在 L4 中途耗尽 → 主会话 `kill -9` runner 冻结状态；额度恢复后 4 张 a1 卡以 RATE_LIMIT 回交、换 key 重启 runner；2 个 a1 情报已因 15 m 超时被 SUPERSEDED |
| 结局 | 6 只票 a2 卡 `CONTRACT_ERROR` → PARENT_TICKET_FAILED；688981 review2 `REVIEW_UNAVAILABLE`（deep DD evidence unavailable）→ runner BLOCKED |

### L4 卡（未发布）

| 代码 | 名称 | 卡 | 评级 / FINAL | 停 | 机检 |
|---|---|---|---|---|---|
| 600827 | 百联股份 | a1 | Underweight / SELL | P3·其他（兑现机制不成立；净利增长来自地块收储） | ✓ |
| 600719 | 大连热电 | a1 | Underweight / SELL | P3·估值透支（PB 9.82） | ✓ |
| 002246 | 北化股份 | a1 | Hold / HOLD | P3·其他（主力真在 FAIL） | ✓ |
| 688202 | 美迪西 | a1 | Underweight / SELL | P3·估值透支（前瞻 PE 128x） | ✓ |
| 688981 | 中芯国际📌 | a1 满卡 | Underweight / SELL | 满卡；9-30 收 111.99 触发两条盯梢线 → 10-08 默认清仓 | ✓（review2 同为 UW/SELL，但被拒，见 R4-3） |
| 300750 | 宁德时代📌 | a1 满卡 | Hold / HOLD | 满卡；主力真在 FAIL（CMF −0.22）；毛利率 28.2→24.8→23.2% | ✓ |
| 600276 | 恒瑞医药 | a2 | Hold / HOLD | 满卡；业绩门 FAIL（Q2 扣非 −39.6%），授权利好 9/29–30 已消化 | ✗ R4-1（a1 残卡同为 Hold） |
| 688578 | 艾力斯 | a2 | Overweight / BUY | 满卡；三门 PASS，但 EV +0.15%、R:R 1.1、兑现机制不成立 | ✗ R4-1 |
| 688222 | 成都先导 | a2 | Hold / HOLD | P3·估值透支（PE 127.7x） | ✗ R4-1 |
| 688073 | 毕得医药 | a2 | Hold / HOLD | P3·其他（10-09 起大股东减持窗口） | ✗ R4-1（a1 残卡同为 Hold） |
| 600710 | 苏美达 | a2 | Hold / HOLD | P3·其他（L3 两前提均不成立） | ✗ R4-1（a1 残卡同为 Hold） |
| 603235 | 天新药业 | a2 | Hold / HOLD | P3·其他（净利 −10.3%，业绩门 FAIL） | ✗ R4-1（a1 残卡同为 Hold） |

a1 残卡 = 被 429 中断的 a1 attempt 已写完整的卡文件（含决策块）；任务以 RATE_LIMIT 回交、未进接受链，只作一致性旁证：4/4 与 a2 评级相同。E6 相对层未运行（停在 L4），本场**无系统 BUY**。

## 缺陷（按阻断程度）

| # | 缺陷 | 根因 / 证据 | 建议修法 |
|---|---|---|---|
| R4-1 🟥 | 单阶段计划下 L4 票级重试 a2 卡恒 `CONTRACT_ERROR: exactly one research-decision-v2 Markdown block required` | `session_agent/service.py::retry_l4` 只在 `two_stage_plan(frozen_plan) and frame_in_plan(...)` 时 `attach_expansion(..., research.frame)`；本场 `card_research_profile=single-stage-v1` → a2 任务无 `research.frame` 输入 → `dispatch.py` 不拼冻结决策窗、`decision_instruction`、断言人口、评分档位（a2 卡 prompt 3675 字 / 8 行 vs a1 9.2K / 28 行）。任务包 `_l4_prompt_*.md`、指令 `0-l4-card.md`、agent 定义均无 `research-decision-v2` 字样 | 条件放宽为 `frame_in_plan(frozen_plan)`；加回归：单阶段计划下 retry_l4 的 a2 card 请求 prompt 必含 `research-decision-v2` 与断言人口段；a2 intel 同时会带回冻结决策窗头 |
| R4-2 🟧 | L4 情报 15 分钟超时从 `.taken` 起算，被宿主「批量领取 → 逐个生成长 prompt」延迟吃掉 | Run 4 首批 8 情报：688578 实耗 1019 s、688222 回交晚于 900 s → SUPERSEDED；Run 3 同类 | 宿主侧：领一个派一个、先回交再派新（本场后半段已改，零超时）；起 runner 时可直接加 `session_agent run --timeout-multiplier 2`（runner 已有此参数，同日另一会话的单股 run 用了 3）；系统侧再考虑 `session.timeouts.mailbox.l4_intel` 放宽到 ≥1200，或改成从 bind-access 起算 |
| R4-3 🟧 | 持仓复核 review2 被拒 `deep DD evidence unavailable; 未核: scan.l4.688981.a1.deep` | 复核 agent 自称读了 deep（报告 deep 无经营现金流行），机器未认到「成功完整 Read」观测 | 查 review2 transcript 的 deep 读法（Grep/分段 Read？）与证据判定口径 |
| R4-4 🟨 | 会话级 WebSearch 预算 `CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION`（默认 200）在同一会话两场全扫后耗尽 | 工具原文：`this session has used its web search budget (200 of 200 WebSearch calls)`；之后 7 个情报员只能 WebFetch，覆盖降级（已在各情报稿声明行标注） | 项目 settings `env` 调高（如 500）；或一场全扫一个会话 |
| R4-5 🟨 | 订阅 5 小时额度：单场全扫（Opus 5.5·max）+ 并行的单股研究会话即可耗尽 | 两次 429；子 agent 全部 `session limit · resets …` | 全扫在额度刚重置时开跑、期间不并行跑其他研究；额度将尽时主会话 `kill -9` runner 冻结（本场验证可恢复） |
| R4-6 🟨 | L3 只给 30/40 候选写判断（Run 3 为 28/40） | 两场复现；agent 以「契约要求 ~20–28 行」为由 | 契约口径统一（全部给判断或明确允许截断并登记） |
| R4-7 ⬜ | sector-brief 定义仍禁「买卖」二字，与模板标签「主动买卖单」冲突 | 冶钢原料 brief 自改标签；runner 已接受 | 改 agent 定义的禁词表述为 `买卖(?!单)` 口径 |

## 修复状态（2026-10-04，未提交）

| 项 | 改动 | 验证 |
|---|---|---|
| R4-1 | `service.retry_l4`：条件放宽为 `frame_in_plan(frozen_plan)`，与 `workflows.expansions_after_task` 同一规则 | 新测 `test_single_stage_scan_retry_keeps_the_frozen_frame_on_the_a2_subtree`（a1/a2 卡与 a2 情报都带 `research.frame`）先红后绿 |
| R4-2 | `scan_config.session.timeouts.mailbox` 加 `"scan.l4.intel": 1800`（原 900） | config lint 0 违规；宿主侧「领一个派一个」仍保留 |
| R4-3 | 根因：同一 assistant message 流式拆成多行时，`transcripts/claude.py` 只取最后一行的 tool_use，**先于兄弟调用的 Read 被丢**，于是 `observe_read` 看不到 deep 的完整读取。改为非末行里末行没有的 tool_use 先行发出、末行去重 | 两条新测先红后绿；用本场生产 transcript 复核：review2 对 deep 的读取现在能被观测到 |
| R4-4 | `.claude/settings.json` 加 `env.CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION="600"`（2.1.289 二进制核实：`?? 200`，计数覆盖全会话含子 agent） | JSON 校验通过、边界 hook 仍被识别；**需新开 Claude Code 会话才加载** |
| R4-5 | 不改代码（订阅额度） | 操作经验见下节第 4 条 |
| R4-6 | `l3-rank.md` 契约改为「表内每一行都要判断」，finalist 数按派发区间；`validation.lint_judged` 增 `coverage`（table_rows / judged / unjudged 码） | 新测先红后绿；Run 4 生产产物复算 40 / 30 / 10 未判 |
| R4-7 | `sector-brief.md`：模板数据标签照抄原字，不算方向词 | 新测从模板代码块取样，断言模板不含禁词 |
| 新·复核缺情报 | `workflows/scan.py`：intel 开启时 review/review3 输入加 `intel_doc`（与卡同源证据，仍不读卡结论）；开关读冻结 `user_config.l4_intel.enabled` | 新测先红后绿；注册表消费点同步 |
| 新·断言人口无事件 | `card_claims.population_prompt_rows`：派发 prompt 的断言人口每条带结构化事件标签（日期 · 谓词 · 生命周期 · 断言类型 · 极性 · 金额） | 新测；用生产 capsule 复算标签 |
| 新·新闻目录停更 | 根因：三源快讯 ingest 原在 `learning/nightly_close.py`，08-21 随 learning 层删除；`scripts/nightly_close.sh` 重建时没补回 → 目录 first_seen 停在 08-20（prelude「最新 1053.5h 前」）。补为第 6 步 `autoresearch.news.catalog ingest-flash`（B 级，exit 恒 0，扫描持锁照跑） | 四条脚本测试先红后绿；手动跑一次：三源 OK、新增 249 条、first_seen 缺失率 0。目录不是决策输入（只进 prelude 健康行与证据索引），本次停更未影响卡面 |
| 卡数 | 用户 10-04 裁定：非持仓 5 卡 = L3 4 + 证据席 1（`l4.max_cards` 10→5、`composite_seat.m` 3→1、`l3.finalist_min` 7→4）；L3 仍 40 行全判 | `effective_caps` = max_cards 5 / seat 1 / finalist_cap 4 |

回归（codex 引擎全量）：9322 过 / 12 跳过 / 1 红；唯一的红是运行中途改写的 l3-rank 措辞测试（旧版本已被收集），改动后的 5 个测试文件单独重跑 155 过，config lint 0 违规。

lane 保底（trend 2 / lowturn 1）未改：用 14 个历史分析日的 `_l3_judged.json` 在 4 席下回放，trend 配额从未触发，lowturn 配额 3/14 天换入 1 席；trend 2 与 trend 1 的结果逐日相同。

## 宿主操作经验（已验证）

1. 监视器先起、派发后看 `prompt_verbatim`；本场 60+ 次全部 0.1–0.5 s 绑定。
2. **领一个派一个**：`mailbox wait --timeout 5` 取单个请求后立即派发；不要 `take_all` 后再串行生成多份长 prompt。
3. **先回交再派新**：子 agent 返回立刻 `complete`，不要先派其它长 prompt。
4. 额度将尽：`kill -9` runner（不触发信号收尾），在飞 agent 的结果照常 `complete` 写入邮箱；恢复后被中断的 attempt 以 `--error-class RATE_LIMIT` 回交，换新 `--key` 重启 runner，已接受任务不重做。
5. a2 卡/情报的 prompt 与 a1 形状不同（无冻结决策窗）——见 R4-1；修复前任何重试都会把整场拖成 BLOCKED。

## 未核实

- 情报员称「WebSearch 被宿主拒绝」在 a1 阶段的说法，早期 transcript 中未找到拒绝原文；后期（a2 阶段）已拿到工具原文（R4-4）。
- 宁德时代、中芯国际港股假期表现未覆盖（情报 UNKNOWN），10-08 开盘缺口不可知。
