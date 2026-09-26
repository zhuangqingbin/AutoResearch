# CLAUDE.md

## 在 session 内做交易研究（零付费 LLM API）

本项目支持用 **Claude 当引擎**在 session 内跑完整的多 agent 交易分析，**不依赖付费 LLM API**：

- **数据层**走项目自己的免费工具（yfinance / FRED / akshare / tushare，keyless + `FRED_API_KEY` / `TUSHARE_TOKEN`）；**LLM 层由 Claude（本 session）替代**框架原本计费的多 agent 调用。
- 所有取数/组装是确定性脚本（零 LLM），统一收进 `autoresearch` 包，用 `uv run --no-sync python -m autoresearch.<...>` 调用。**产物按引擎分根**（2026-08-11 裁定）：Claude 会话落 `reports_claude/`、`context_claude/`，Codex 落 `reports_codex/`、`context_codex/`（python 侧按 `AUTORESEARCH_ENGINE`/`CLAUDECODE` 自动判定，Claude 下无需设置）；**唯一共享的是数据湖 `lake/`**。均已 gitignore。

### 统一 session_v1 编排

五类入口现在共用 `uv run --no-sync python -m autoresearch.session_agent`。显式设置
`AUTORESEARCH_ENGINE=claude` 后，宿主循环为
`begin → next → claim → execute/Claude 推理 → submit → finish`。冻结计划、artifact、attempt、
回执和发布由 Python 验证，推理仍发生在本 Claude Code 订阅会话。真实 Claude 验收未记录的
场景继续使用标为 `LEGACY_ORCHESTRATION_FALLBACK` 的旧 Workflow；切换状态见
`docs/session-agent/acceptance.md`，操作方法见 `docs/session-agent/README.md`。
`finish` 后以返回的 canonical 路径运行 `session_agent verify-report --level full`；只有
`report_covered/publication_ok/orchestration_verified/completeness_ok` 的机器结果可以用于交付说明。
未绑定的改写报告返回 `UNBOUND_REPORT`，不得借同一 run_id 或旧 ROOT 归因。合成 PASS 不改变
默认入口，双宿主真实 proof 未齐时 `session_v1` 仍是 PILOT。

### 研究入口（skill 自动触发）

- **单标的**：`stock-research` skill（原 analyze-ticker + analyze-ticker-lite 合并，**full/lite 两档 prompt 路由**）—— 说"研究 NVDA" / "分析 600519.SS"即触发 full 全量报告（可带同业，如 `AMD,AVGO`；6 步流程 + **决策主线 / 证据附录** 骨架 v4）；"快速看一眼 / 出张卡"或被 scan-market L4 调用 → lite 决策卡。
  - 取数：`python -m autoresearch.analyze.harvest <ticker> [date] [stock|crypto] [PEER1,PEER2]`（`--slim` = lite 档轻量取数）。
  - 组装：`python -m autoresearch.analyze.assemble context_claude/analyze/<TICKER>_<date>`（用项目 `parse_rating` 校验五档评级）。
- **全 A 扫描**：`scan-market` skill —— "扫描全 A 股 / 全市场选股 / 哪些板块值得买"。确定性漏斗 L0→L1→L2 + Claude 在 L3/L4/L5 做研究/辩论/整合。
  - 漏斗：`python -m autoresearch.scan.prelude <date>`（确定性前奏一键：L0→L2 + 日历/观察单/菜单/账本；staging `context_claude/scan/<date>/*.csv`；发布产物 `reports_claude/scan/<run_id>/` 由 assemble 生成）。
  - 整合：`python -m autoresearch.scan.assemble <date>`。**闭环复盘已整体退役**（2026-08-21 用户裁定：`autoresearch/learning/` 整包 + `scan-retro`/`feedback` 两个 skill 删除；细节见 scan-market 的 `STAGES.md`「行为变更的入口」节）。
  - 常备覆盖档案（`context_claude/knowledge/dossiers/`）：池日检在 prelude 内；首覆走 `dossier-init` skill；**中报/年报披露后**跑季度对账 `python -m autoresearch.dossier.reconcile <period>`（如 `20260630`；prelude 的 dossier_pool 行会在该期未对账时打 📐 提醒）。
- **宏观**：`macro-research` skill（**full/lite 两档**）—— full："研究全球宏观 / 现在该超配什么资产 / A股哪些行业值得配";lite = **市场研判**(原首席策略师,scan-market Stage 0 调用或"今天大盘怎么看",读 `python -m autoresearch.scan.frame <date> --json` 的湖派生 market_pack 写 market_view.md)。
  - 取数：`python -m autoresearch.macro.harvest [date]`；组装：`python -m autoresearch.macro.assemble context_claude/macro/<date>`。

### 包结构（`autoresearch/`）

- `autoresearch/data`、`autoresearch/dataflows`、`autoresearch/agents/utils` —— 免费数据层（lake + contracts + sources；yfinance/FRED/akshare/tushare）+ `rating.py` 等工具。
- `autoresearch/common`、`autoresearch/trace` —— 打分原语 / **法证 run capsule**(2026-08-28:`capsule`(生命周期+finalize+verify+recover+repair)、`events`(hash 链)、`exec_capture`(命令/日志/信号)、`identity`(代码/环境/prompt 快照+脱敏)、`source_lineage`+`blobs`(精确读点与内容寻址)、`transcripts/`(Claude/Codex adapter)、`completeness`、`replay`;配 `scan/run_profile.py` 声明每种模式该有的证据。**三个结论互不替代:完好性 / 完整性 / 可重放性**,`MANIFEST` 通过≠现场完整。设计稿 `docs/superpowers/specs/2026-08-27-scan-forensic-run-capsule-design.md`) / token 真计量（usage_harvest，读 subagent transcript；OTEL telemetry 因零生产调用点已于 2026-07-27 退役）。（原 `autoresearch/learning` 闭环学习包已于 2026-08-21 用户裁定整体退役；发布前自检硬门 `self_review` 与持仓盯梢尺 `tripwire_watch` 搬去 `autoresearch/scan/`——它们检的是「报告对不对/持仓破没破线」，从来不是「从历史里学到什么」。原 `models` 模型园区与 typed-trace 平行实现已于 2026-07-13 移除。）
- `autoresearch/scan`、`autoresearch/analyze`、`autoresearch/macro` —— 三个 skill 的 stage 管道 + agents + CLI。
- `autoresearch/broker` —— 券商成交取数层(2026-08-27 设计稿):手机 App 导出的交割单/对账单/截图表 → 标准化成交表 `context_<engine>/broker/trades.csv`(零 LLM;A/B 两级契约;幂等;`ingest`/`reconcile` 两个 CLI)。只记不学,不进 lake/。券商格式 adapter(chinaclear/gtht/tpy)等真样本探针后再建。

> **2026-09-26 三线设计稿**(`docs/superpowers/specs/2026-09-26-daily-engine-consolidation-design.md`):A 编排壳税归零 + skill 文档瘦身(agent 文件是契约唯一真身,playbook 只剩指针;运维见 `docs/ops/scan-ops.md`,负结果见 `docs/research/scan-negative-results.md`)· B `common.ruler.SWING_RULER="fwd_10_oc"` 第二把尺(影子,不替换 `MAIN_RULER`)· C launchd headless 自治。实施期冻结:不新增法证层/普查族。
>
> 注：原框架的**付费 LLM 多 agent 路径**（LangGraph 编排、provider clients、CLI、批量 runner）已移除——本项目现在**只**保留 Claude-as-engine 的 scan / analyze / macro 路线 + 其依赖的免费数据层（`autoresearch/data`、`autoresearch/dataflows`、`autoresearch/agents/utils`）。架构详见 `docs/specs/2026-06-22-autoresearch-arch-redesign-design.md` 与 README 的 **架构** 节。
