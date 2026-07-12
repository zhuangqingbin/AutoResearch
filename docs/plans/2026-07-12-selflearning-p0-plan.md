# 自学习 P0 波实施计划（P0-1..7）

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development。**需求真值源 = `docs/specs/2026-07-12-selflearning-optimization-brainstorm.md` §4 的 P0 各条**（含裁决法），本计划只做任务切分/文件边界/约束，不复述规格。批次 A=T1∥T3∥T5∥T7，批次 B=T2∥T6，T8 收口；executor 不 commit。

**Goal:** 修裁决环执行债+给"学习本身"装仪器：断环清欠接线、attribution 终评级、shrinkage 基率（带留一日回放裁决器）、过程分机检、lesson_yield 证伪器、DSR-lite 记账、裁决纪律条款。

## Global Constraints

- **双轨语义**（§4 排序原则原文）：裁决门槛（改不改机制）仍用硬 n；注入锚（给 LLM 读的数字）才用收缩值——两套语义不混；n<3 仍绝对禁注。
- 全部确定性零 token；新账本/新列一律 **presence-gated**（缺文件/缺列=现行为不变）；shrinkage 带配置开关可回滚（scan_config 白名单新 top 键 `learning`，sub `{shrink, shrink_k}`，默认 shrink=true, k=15——注入锚语义变化是本波目的，config 是回滚杆）。
- 每条 P0 的**裁决法产物**（run_health 新鲜度行/回放 CLI/秩相关读数/累计 Δ 曲线/两行固定文案）是交付的一部分，不是可选项。
- 测试 `uv run --no-sync python -m pytest <path> -q`；全量绿（基线 1252）；ruff 净。
- P0-1(a) 补跑 07-07/08 复盘 = **运营项不在本波**（scan-retro LLM 会话），由 nag 机制浮出。

## 任务切分（文件边界防冲突；assemble.py 单写者=T2）

### T1 · P0-1 断环清欠接线（不含 assemble 侧）
Files: `autoresearch/scan/prelude.py`（_ledgers 白名单加 channel/gate/zero_buy/changelog_ledger 四账本；retro_input 未读 nag 行,仿既有 proposals nag）、`autoresearch/scan/health.py`（run_health 加「账本新鲜度」行：复盘欠账日数/各账本 mtime 滞后/两本买单计数一致性）、`autoresearch/learning/journal.py` 或 `zero_buy_ledger.py`（D5 买单口径统一到 attribution `bought` 单一事实源——先勘察两处现实现再定改哪侧,报告写清）+ tests。先勘察 prelude._ledgers/_proposals_nag/run_health 现实现。

### T2 · P0-2 + P0-4 + P0-1(c)（assemble 单写者task）
Files: `autoresearch/scan/assemble.py`（①发布时落 `_final_ratings.json`=ensemble/verify 折回后的终评级,STAGES 开放线头 #6 有修法;②is_real 后处理挂 `precedents.build_index`,失败不挡发布;③过程分机检:逐卡确定性 checklist 布尔汇总——§4 P0-4 列的 6 项,落 `process_scores.csv`）、`autoresearch/learning/retro.py` 只动 attribution join 处（优先 join `_final_ratings.json`;attribution 加 process_score 列,presence-gated）、新 CLI 回填器（历史 reports/scan/*/details 全量卡回填过程分初读,§5 局限注明模板代际差）+ tests。先勘察 assemble 发布路径(:1005-1025,:741-789)/STAGES 线头 #6/attribution 列结构。

### T3 · P0-3 shrinkage 基率 + 留一日回放
Files: 新 `autoresearch/learning/shrink.py`（`shrink(p_bucket, n_bucket, p_global, k=15)` 单一原语+docstring 公式）、四消费点改造（`l4_card.write_base_rates`、`cross_calib.flip_stats`、`buy_ledger.write_target_calib` regime×lane 细分格、`gate_ledger` tail_rate——各点注入格式改「收缩值(n=X⚠)」,n<3 禁注不变）、`user_config.py` 白名单 `learning:{shrink,shrink_k}`、新回放 CLI `python -m autoresearch.learning.shrink_replay`（17 真实日留一日回放 raw vs shrunk MAE,§4 P0-3 裁决法）+ tests。先勘察四消费点现格式;**跑一次真实回放并把读数写进报告**。

### T5 · P0-5 lesson_yield 证伪器
Files: 新 `autoresearch/learning/lesson_yield.py`（带 guard 的 lesson:guard 命中集 join attribution fwd → 逐条累计反事实 Δpp 曲线+MTM 计数;n≥20 且累计 Δ≤0 自动**提名** retire,只提名不动作）+ CLI 报表 + tests。先勘察 retro.py:233-265 的 MTM guard 评估机制复用其谓词执行,勿重写。

### T6 · P0-6 DSR-lite 记账
Files: `autoresearch/learning/retro.py`（recalibrate_and_log/changelog 补 trial 计数=同参数族第 N 次）、changelog_ledger 复活模块（若独立文件则改那里;固定打印 §4 P0-6 的两行文案①多重检验②C18 红灯）+ tests;**对既有 5 次真实改权重跑一次出读数写进报告**。与 T1 并行时注意:T1 只动 prelude/health/journal,不碰 retro.py——你是 retro.py 在批次 B 的唯一写者（T2 也动 retro.py 的 attribution join 段——先 git status 确认 T2 已提交再开工;若未提交,NEEDS_CONTEXT）。
（注:T6 排批次 B 与 T2 串行同文件,控制端会先提交 T2。）

### T7 · P0-7 裁决纪律两条款（文字级）
Files: scan-retro skill 的 playbook（先 ls .claude/skills/scan-retro/ 找到真值文件再改）：①proposals 裁决 checklist 加「三门/买侧提案除 n≥20 外须覆盖 ≥2 温度相位」;②「注入锚用收缩值/裁决门槛用硬 n」双轨语义节。若有 proposals 模板文件同步。doc-lint 若锚定这些文件,跑对应测试。

### T8 · 收口
全量 pytest+ruff；真链冒烟（prelude dry-run 看新账本行/新鲜度行;shrink_replay 真跑;changelog 读数真跑）；review-package 终审；ledger/记忆/方向稿状态行更新（P0 已实施,P1/P2 待裁）。
