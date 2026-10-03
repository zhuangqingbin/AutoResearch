# 全项目优化审计与建议(2026-09-27 五路)

> **目的**:以 `docs/flow-handbook.md`(快照 main@4f20cc1,2026-09-27)为基线,对项目做一次全量体检,给出「增 / 删 / 改」建议清单。只给建议,不做开发。
>
> **方法**:五路并行、全只读 —— ① 手册 ↔ 代码漂移抽查 ② 死代码消费点普查(全仓库 grep + 两路 Explore 审计)③ 成本大头(命令壳、token 实测)④ 研究空白(对照历史裁定与负结果)⑤ 运维缺口(launchd、账本、git)。
>
> **一句话结论**:项目软件质量与自检体系处于历史最好状态(手册与代码零漂移、账本已修复、守卫活跃);**最大风险不在代码,而在 git 未同步(739 提交只存本机)**;最大的钱漏在 legacy 命令壳(每场 $13.5 量级);死代码存量约 1,750 行 + 8 个测试文件可删;下一场真实扫描是 session_v1 转正与壳税归零的最后一公里。

---

## 0. 结论摘要

| 优先级 | 项 | 类型 | 成本 | 收益 |
|---|---|---|---|---|
| **P0** | **push main(739 commits,origin 停在 07-28)** | 改·运维 | 免费 | 消除单点全灭风险(两个月工作只存一台 MacBook) |
| P0 | launchd 重装(nightly-close 旧无后缀版 / prewarm 旧版) | 改·运维 | 10 分钟 | 新版 23:30 账本步、21:00 prewarm 重试、防双跑 |
| P0 | 清理 3 个已合并 worktree + 3 条已合并分支 | 删·运维 | 5 分钟 | 省磁盘、减混淆 |
| P1 | 死代码删除包(≈1,750 行 + 8 测试文件) | 删·代码 | 半天 | 减维护面,防「死代码里长出新活代码」 |
| P1 | legacy 命令壳合并 4 组(每场省 $2–4) | 改·代码 | 半天 + 回归 | 每月 ≈$15–20;壳少一个=事故面小一分 |
| P1 | README 上游遗留三节删成指针 | 删·文档 | 半小时 | 新读者不再被 07 月的 fork 遗产误导 |
| P2 | 下一场真实扫描走 session_v1(机器门要 REAL_SESSION proof) | 改·验收 | 一场扫描 | 壳税 $13.5/场归零的前置;legacy 退役的唯一闸门 |
| P2 | 安装 headless scan-run + 送达渠道 | 增·运维 | 半天 | 无人值守 + 手机推送(09-26 C 线的执行遗留) |
| P2 | 影子件处置:死票门退役、SWING_RULER 等普查完 | 删·裁定 | — | 影子积压清零 |

---

## 1. 改(修复 · 补装 · 质量)

### P0 — 纯运维,零代码,建议立即做

**1.1 🚨 推送 main(全项目最高风险项)**

- 现状:`main` 领先 `origin/main`(**739 个提交**);origin 最后同步于 **2026-07-28**(19d0cfe)。Wave5–12、法证 capsule、E6、指数调样、三线设计稿批 0–6 等两个月高强度工作只存在于本机。
- remote 是个人仓库 `git@github.com:zhuangqingbin/AutoResearch.git`,推送无权限风险。
- 建议:`git push origin main`(可顺带把已合并分支标签推上去留档)。此条独立于其余一切建议,不需要裁定。

**1.2 launchd 重装(手册 §11 runbook 已写好,只是没执行)**

- 现状(2026-09-27 `launchctl list` 实测):
  - `com.tradingagents.nightly-close` —— **旧的无引擎后缀 label**(runbook 明示「先退役旧的,否则一晚会跑多次」的风险版本),最近一次退出码 1(09-25 exec_anchor 时区 bug,代码已修 4d27143,但自那次失败后还没机会跑成功过);
  - `com.tradingagents.scan-prewarm` —— 旧版,只有 19:30,缺 21:00 重试;
  - `com.tradingagents.macro-harvest` —— 正常(退出 0);
  - `com.tradingagents.scan-run` —— 未安装(与手册一致,属 P2 项)。
- 建议:按手册 §11 的安装块重装 nightly-close(引擎后缀版、23:30)与 prewarm(新版含 21:00 重试);下个交易日(09-28 周一)验证退出码归 0。

**1.3 清理 worktree 与已合并分支**

- 现状:`.worktrees/` 下 3 个 worktree(feature/broker-ingest、feature/scan-forensic-capsule、feature/session-agent-migration),**分支均已并入 main**(已逐一验证),可安全 `git worktree remove`。
- 另有 3 条已合并本地分支(stock-research-p0-p1、fix/wave12-t16-t22-review-followup-20260809、wave6-batch-b)可 `git branch -d`。

**1.4 两个文件级小清理**

- `.claude/skills/scan-market/Untitled`(39 字节,内容「Sonnet · 情报员 ×5(盲搜,只写)」,14:36 手滑产物)→ 删除。
- `docs/flow-handbook.md` 尚未纳入 git(`??`)→ 它已是最新快照、质量高,应随 1.1 的推送一起提交进库。

### P1 — 轻量开发或裁定,建议走一轮裁决

**1.5 legacy 命令壳合并(壳数 95–224/场 → 压 15–30%)**

- 现状:每场全扫 ≈ **95–224 次命令壳调用**(scan-market.js ≈27–37 + 每股 l4-stock ≈13–17 × 6–11 只),与手册 §5.9「141 壳、$13.5、62% 加权输入」互证;09-17 最近一场实测总成本 ≈$40.5(opus 19 个 $21 + sonnet 152 个 $19.4)。
- 已探明的可合并对(连续、无 JS 层数据依赖):
  - scan-market.js:`pack-check → strategist-pack-check`(同一 frame 产物的两次校验);`dispatch-plan → l4-tasks-init`;trace-control `completed(N)`/`dispatched(N+1)` 成对;
  - l4-stock.js:`ens-dump → taskDone → recordL4` 三连;`intel-guard → intel-status`;错误路径 `taskFailure + recordL4` 对。
- 代码里已有合并先例(「壳合并①②」、GATE1 合并、l4-prep 5 生产者一壳),风险可控;但 legacy 是现行默认,动 workflow 要重跑验收。
- 预计收益:每场省 $2–4,按每月 4–5 场 ≈ $15–20/月;附带收益是壳有事故前科(后台化/禁杀/改写命令),少一个壳就少一处事故面。彻底归零仍靠 session_v1/headless(见 1.9/1.10)。

**1.6 「learning」目录名残留(功能合法,名字是尸体)**

- 现状:learning 层已于 08-21 整体退役,但 5 个活代码仍在往 `context_<engine>/learning/`、`reports_<engine>/learning/` 写东西:`trace/usage_reconcile.py:101`、`scan/report_sections.py:826,1016`(读写 usage_reconcile.jsonl)、`scan/temperature.py:47`(temperature.csv)、`scan/l2_knife_audit.py:129`、`scan/structural_audit.py:355`(审计输出)、`contracts/artifacts.py:634-635`(白名单)。这些是计量/审计功能,本身合法,只是借用了退役层的目录名。
- 建议二选一:改名(如 `metrics/` 或 `audit/`,动 5 写点 + 2 读点 + 白名单,一次性半小时,但要全量 grep 消费者)或接受为历史名(在 workspace.py 注释里已有说明)。倾向改名——名字是接口,「learning」会持续误导未来的自己以为闭环层还活着。

**1.7 README 上游遗留三节删成指针**

- 现状:README 351 行,143–235 行是上游 fork 遗产:Installation(`pip install .`)、Required APIs(OPENAI_API_KEY / GOOGLE_API_KEY)、CLI Usage(`tradingagents` / `python -m cli.main` —— **仓库里根本没有 `cli/` 目录**)。README 自己的 fork 注(~30 行)已承认它们是遗留参考。
- 建议:三节整删,替换为「项目真身是 Claude-as-engine 的免费研究系统,全流程见 `docs/flow-handbook.md`」+ 四条研究入口一行表。手册 §13「别被这些误导」就是在给这段背书。

**1.8 文档小修三件**

- `docs/PANORAMA.md`(1063 行,07-16 基线 + 09-26 冻结注):速查第 1045–1049 行仍列着已删的 `autoresearch.learning.retro` 等 5 条命令 → 只加一行墓碑「learning 层已退役,以下命令已删除」,别大动(冻结文档)。
- `autoresearch/research/__init__.py` docstring 声称 factor_lab 被 `scan.universe` 调用 —— 实际无此 import,改一句。
- `tests/scan/test_brief.py` ~750–777 行的 `_count_rolls` 死 helper 引用了已删的 `autoresearch.learning.buy_ledger`,定义但从未被调用 → 删(顺带清掉测试对退役层的最后一条 import)。

### P2 — 需要计划或验收窗口

**1.9 下一场真实扫描安排走 session_v1(转正的唯一闸门)**

- 现状:五类入口自动化与合成回归全通过,但机器门 `evaluation.accept_workflow` 要求**双宿主真实会话 REAL_SESSION proof**,全部 INCOMPLETE;09-27 已修掉「四个真扫阻断」(信箱模式、a1 intel 不突变、owner-ticket 证据、BLOCKED 证据闭包),manual 手册 §10.4 的已知缺口还剩 6 条(单票失败停整场、研判无降级、复核无降级等)。
- 建议:下 1–2 场真实扫描用 `session_agent run --executor mailbox` 跑(宿主循环按手册 §10.2),失败即回退 legacy(legacy 照常可用,回退代价只是一晚);跑完用 `verify-report --level full` 的四项机器结果作为交付依据。这是壳税 $13.5/场归零、主会话上下文不再膨胀(61k→494k 事故)的最后一公里。

**1.10 安装 headless scan-run + 送达渠道(09-26 C 线执行遗留)**

- 代码已合 main(`scripts/scan_run.sh`、plist 模板、headless 执行器),本机未安装、未验收。
- 建议:按手册 §11 安装 `com.tradingagents.scan-run`(交易日 21:20),同时把 `delivery.channel` 从 `"none"` 改成 `"bark"`(仓库根 `.env` 加 `BARK_TOKEN`,`scripts/notify.sh` 试发)。注意合盖睡死风险(手册已写:插电开盖)。
- 这一项同时覆盖「无人值守」和「壳税归零」两条收益,但首次装后建议先影子观察 1–2 晚(人工场照跑时它会自动让位)。

---

## 2. 删(删除候选清单)

以下为消费点普查确认的**零生产调用方**文件(两路 Explore 全仓库 grep:live 路径 = scan/analyze/macro/sector/dossier/session_agent/common/trace/data + workflows + skills;调用方只有自身测试或离线普查 CLI):

| 文件 | 行数 | 唯一调用方 | 死因证据 |
|---|---|---|---|
| `autoresearch/ops/backup.py`(+`tests/ops/`) | 104 | 自身测试 | 无 scripts/plist/docs 任何引用;docs 指向的 `ops.purge` 模块已不存在 |
| `autoresearch/news/fulltext.py` | 304 | 自身测试 | 设计稿自注「建成未接线」 |
| `autoresearch/news/typed_events.py` | 379 | 自身测试 | 同上 |
| `autoresearch/news/claim_acceptance.py` | 149 | 自身测试 | 计划文档自认「从未接线的验收仪器」 |
| `autoresearch/derivatives/cb_gate.py` | 170 | 自身测试 | 普查记录 gate 为 BLOCKED_BY_DATA 且弃用 |
| `autoresearch/derivatives/style_spread.py` | 186 | 自身测试 | 普查确认零生产调用方、磁盘零数据 |
| `autoresearch/research/probability_eval.py` | 90 | 自身测试 | 仅一处历史计划片段引用 |
| `autoresearch/research/feature_gate.py` | 266 | 自身测试 | 唯一外部引用是 artifacts.py 的被动白名单字符串,非 import |
| `tests/scan/test_brief.py` 死 helper `_count_rolls` | ~27 | 无(定义未调用) | import 已删的 `learning.buy_ledger` |

**合计 ≈1,750 行代码 + 8 个测试文件**。删除时注意:

- **保留**:`derivatives/options_lake.py`、`qvix.py`(离线普查 `research/derivatives_census.py` 在用,是研究仪器不是死代码);`news/` 其余 6 个模块(catalog/claim_* 是活代码,evidence_index、intel_guard、completeness 在消费);`research/` 的 15 个普查 CLI(scan-ops.md / SKILL.md / `_probe-parent.js` 有登记,是研究仪器)。
- **历史教训**(memory):删退役特性的测试会静默孤立它顺带锁的 live 契约 → 删前对每个文件 grep 全部消费者;`tests/test_agent_defs.py:503-541` 已有「learning 包必须不存在」的反向断言,同类文件删除建议同样补反向测试。
- **冻结窗**:09-26 三线设计稿的冻结是「不新增法证层/普查族」——删死代码不违反,且与 A 线(编排壳税归零 + 文档瘦身)精神一致;但按项目惯例走一轮用户裁定再动手。

---

## 3. 增(机会)

**3.1 召回线 4.8% 系统化普查(研究仪器,解冻后立项)**

- 依据(手册 §7.1):413 只 T+1 赢家 91% 落在打分池、仅 **4.8%** 越过 top1000 召回线;「0 买的根因在召回线」。同时 value 路胜率 57.6% / +0.9% 是全部召回路最优,说明个别路有真信号,召回结构有可挖空间。
- 建议(解冻后):离线普查「打分池里但没过召回线的赢家」画像——什么路漏的、漏因是门太严还是信号不覆盖、能否新增一路低假阳性召回。产出要么是一条新召回路的候选规格,要么是「不加」的负结果(都值钱)。
- 前置:09-26 冻结期内**不做**;且新召回路必须走 `factor-backlog.md` 的 IC 门流程(先过门,再碰 composite)。

**3.2 headless 无人值守 + 送达(已有代码,只差安装验收)** —— 见 1.10,此不赘述。

**3.3 送达渠道 bark / mail** —— 一行 config + `.env` token,与 1.10 同批做。

**3.4 明确不建议现在做(防重提)**:

- **美股全市场扫描**:scan-market 只支持 A 股;美股全扫是全新子系统(数据源、可交易性、盘后时差全部不同),成本≈重做一遍 scan,收益未验证。要扩大市场覆盖,先在 stock-research full 单票层面做美股覆盖(已支持)。
- **盘中/开盘前信号**:追当日大涨已被 07-25 普查证伪(−4.85pp,t=−13.6),主尺是隔夜;任何「盘中信号」提议先读该负结果。
- **A 级(BUY)恒 0 修复**:09-25 已定案是结构性(早停卡不得写「允许」+ 约束在菜单不在 L4),7 笔已发布 BUY 全来自早停卡;09-24 四裁定已解「保留 ≥1 BUY / 停隔夜校准」,不要再从「让 A 级非零」方向提。
- **换主尺/双尺上生产**:SWING_RULER 10 日尺 V2 普查基线三格全负(09-26),B4 换尺前提可能塌;等普查完成再裁,不立项。

---

## 4. 需要用户裁定的问题

| # | 问题 | 我的倾向 |
|---|---|---|
| Q1 | 死代码删除包(§2)是否现在执行?冻结窗内删死代码是否可接受? | 可执行;删死代码≠新增法证层/普查族,且符合 A 线瘦身精神 |
| Q2 | 命令壳合并(1.5)是否值得在 session_v1 转正前投入? | 值得;4 组合并都是无数据依赖相邻对,半天工作量,每月 ~$15–20,且 legacy 至少还要活到 session_v1 验收完 |
| Q3 | 下一场真实扫描是否安排走 session_v1(mailbox)? | 是;机器门只认 REAL_SESSION proof,越早跑分母越早齐 |
| Q4 | `l4_intel.skip_when_dead` 影子去留?(09-26 普查:81 卡 0 命中,谓词前提不成立) | 退役影子(代码 + config 键),留普查结论存档 |
| Q5 | `learning/` 目录名残留:改名(`metrics/`/`audit/`)还是接受历史名? | 改名;名字是接口,尸体会误导 |
| Q6 | 推送时只推 main,还是把已合并分支标签一并推上去留档? | 一并推,留档成本为零 |

---

## 5. 已核对无恙(本次审计确认不需要动的)

- learning / retro / feedback 三层**真删干净**(无活代码 import;仅 1.6 的目录名残留)。
- L4 TTL 复用零残留;`reuse_ttl` 全部命中是 sector brief 的合法复用旋钮。
- `l3.finalist_max` 退役守卫活跃(写了即报 ValueError,迁移指针指向 `l4.max_cards`)。
- 结果账本已修复:`_health.json` filled=68、last_success=2026-09-27T03:12(09-17→09-27 的 982 行手动补齐已生效);绿灯被 3 个成熟欠账压着是诚实报账,非故障。
- 手册与 HEAD(4f20cc1)一致;`scan_config.jsonc` 现值与手册 §7.10 表逐项吻合(max_cards 13 / budget_flags / weight_profile preference / knife_cap / sector_seats / relative_buy e6.v4.1 / delivery none)。
- 负结果归档机制健全:`docs/research/`(普查族)+ `scan-negative-results.md` + `factor-backlog.md`(先过 IC 门),本次审计未发现负结果丢失。
- 手册 §13「别被这些误导」一节准确预告了 README / PANORAMA 两处遗留——本次审计只是把它落成了具体行号。

---

## 附录 A:本机实测状态(2026-09-27,供下轮审计对照)

- `launchctl list`:nightly-close(旧无后缀 label,退出 1)· scan-prewarm(旧版,退出 1)· macro-harvest(0)· scan-run(未装)。
- git:HEAD 4f20cc1;`origin/main` = 19d0cfe(2026-07-28);领先 739 提交;3 个已合并 worktree;3 条已合并本地分支。
- 成本:最近一场真实扫描 09-17 —— 1 主会话 + 170 subagent,加权输入 10.04M,估算 ≈$40.5(opus 19 个 $21.1 + sonnet 152 个 $19.4),cache 命中 82.3%。
- 壳:每场 ≈95–224 次(scan-market.js ≈27–37 + 每股 13–17 × 6–11),手册 §5.9「141/$13.5/62%」为中位口径。
- 账本:`_ledger/views/_health.json` green=false(3 个成熟欠账),filled 68,n_runs 73。
