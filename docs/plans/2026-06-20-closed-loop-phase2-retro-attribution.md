# Phase 2 — retro 归因 + 诊断 + 自动重标定 + 建议(实现 plan)

**Goal:** 交付诉求二 —— 用 T+1 实际涨跌复盘前一日报告,归因漏判,自迭代权重 + 出建议 + 写经验。
**Spec:** `docs/specs/2026-06-20-closed-loop-learning-design.md` §3.2 §5。
**前置:** Phase 1(`feedback_store` 的 `upsert_lesson/add_proposal/log_change`)。

## 文件
- 新增 `scripts/retro.py`(确定性归因 + 自测)
- 新增 `.claude/skills/scan-retro/{SKILL.md, retro-playbook.md}`
- 复用 `scripts/factor_lab.py`(`forward_returns` / `calibrate`)、项目 `parse_rating`

## Task 1 — 实际收益 + 待复盘日
- 定位项目 `parse_rating`(`grep -rn "def parse_rating"`),retro 复用它读评级。
- `realized_returns(date)->DataFrame[code, fwd_1_oo, fwd_5_oc]`:复用 `factor_lab` 取数口径(`harvest`/`forward_returns`/`cache`),全市场;沿用"剔 D+1 一字板"。
- `pending_days(today)->list[str]`:遍历 `context/scan/*/`,选 有报告(`reports/scan/<YYYYMMDD>/*_summary.md`)∧ 无 `context/scan/<date>/retro/done.json` ∧ `realized_returns(date)` 非空(D 的 fwd 已实现)。交易日历用 factor_lab 的交易日。
- 测:`pending_days` 在合成目录上识别正确;`done.json` 存在→不返回。

## Task 2 — 买单解析 + 归因分桶
- `_buylist(date)->dict[code,rating]`:找 `reports/scan/<YYYYMMDD>/` 最新 `<HHMM>_detail/*.md`,逐只 `parse_rating` → {code: 五档}。买单 = Overweight/Buy。
- `attribute(date)->DataFrame`:读 `context/scan/<date>/L1_scored_full.csv`(rank/recalled/composite/子分/因子)→ merge realized(on code,全市场 outer 以保 missed_l0)→ merge buylist。计算:
  - `tradable` = 有 fwd_1_oo（可交易）。
  - `winner` = `fwd_1_oo >= tradable.fwd_1_oo.quantile(0.9)` ∧ `fwd_1_oo >= abs_thresh`(默认 0.03)。
  - `bucket`:
    ```
    in_l1 = code ∈ L1_scored_full ; recalled = L1.recalled ; bought = rating∈{OW,Buy}
    caught          = winner & bought
    recalled_cut    = winner & recalled & ~bought
    missed_l1       = winner & in_l1 & ~recalled
    missed_l0       = winner & ~in_l1
    false_positive  = bought & (fwd_1_oo <= tradable.quantile(0.1))
    ```
  - 写 `context/scan/<date>/retro/attribution.csv`(code,name,bucket,winner,fwd_1_oo,fwd_5_oc,rank,recalled,composite,关键子分/因子,rating)。
- 测(合成 panel + returns):每桶计数符合构造预期(caught/recalled_cut/missed_l1/missed_l0/false_positive 各命中应命中的合成股)。

## Task 3 — 阶段统计 + 诊断输入
- `stage_stats(attr)->dict`:赢家总数;各段"漏斗存活率"(L0→L1→买单 各保留多少赢家);买单命中率(buy 中 winner 占比 / false_positive 率);当日 IC(composite vs fwd_1_oo,Spearman)。
- `write_retro_input(date)`:`context/scan/<date>/retro/retro_input.md` —— stage_stats 表 + 漏掉/误判赢家 top（带因子行,供诊断成群对比）+ 选中票对照样本。
- `mark_done(date)`:写 `retro/done.json`(幂等)。
- 测:stage_stats 数值对合成集正确。

## Task 4 — `scan-retro` skill(Claude 诊断 + 落地)
- `SKILL.md` description:「Use when 复盘前一交易日的 scan 报告 / 跑 /retro / scan 开跑前发现未复盘日 —— 用实际涨跌归因漏判、自迭代权重与经验」。
- `retro-playbook.md` 流程:
  1. `retro.pending_days(today)` → 对每个 D:`retro.attribute(D)` + `write_retro_input(D)`。
  2. 读 `retro_input.md` + 漏判赢家因子行 → **成群诊断系统性病因**(L0门槛/L1权重/L2-L3误判 三段药);**分离消息脉冲**(涨停/停复牌/巨量异常 → 标 `news_pop`,排除出重标定样本与权重诊断)。
  3. **量化自动落地**:确认 D 已并入 factor_lab 面板(必要时 `factor_lab.py harvest` 该日)→ `factor_lab.py calibrate` 重跑 → 取新旧 `weights.json` 的 sha + top 变化 → `feedback_store.log_change(...)`。
  4. **结构建议**:门槛/新因子/prompt 规则改动 → `feedback_store.add_proposal(...)`(待批,不落地)。
  5. **写经验**:反复出现的诊断 → `feedback_store.upsert_lesson(...)`(强化);停止复现的留待 Phase 3 退休。
  6. **retro 报告** `reports/scan/<YYYYMMDD>/retro_<HHMM>.md`:漏斗各段命中率 / 漏赢家 top+病因 / 已落地权重变化(引 changelog)/ 待批建议 / 经验增改。`retro.mark_done(D)`。
  7. 模型建议 Sonnet(归因是结构化对比,便宜);量大可选 workflow(需用户显式开启)。
- **半自动边界**:仅权重重标定自动落地;其余 `add_proposal` 待批。

## Task 5 — 测试 + 验收
- `uv run --no-sync python scripts/retro.py --selftest`(Task1-3 合成断言全过)。
- ruff clean。
- 真数据校验(诚实标注可达范围):`realized_returns` 对 factor_lab cache 内某历史日返回非空且口径与 `forward_returns` 一致;若某 scan 日 fwd 已实现,跑 `attribute` 产出 attribution.csv 并人核分桶;否则用合成 + cache 校验、报告说明哪些是单测/哪些是真跑。
- 不碰排除文件;不 commit;`uv run --no-sync`;akshare/tushare 仍 venv-only。

## 验收
对一个 fwd 已实现的 scan 日:`retro.attribute` 分桶正确 → `scan-retro` 诊断出系统性病因 → 自动重标定写 `changelog.jsonl` + 经验入 `lessons.jsonl` + 结构建议入 `proposals.jsonl` → retro 报告落地。
