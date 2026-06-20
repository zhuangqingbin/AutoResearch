# 闭环学习层(反馈记忆 + 复盘自迭代)设计 — 2026-06-20

> Claude 当引擎、零付费 LLM API。在现有 `scan-market / analyze-ticker / macro-research`
> skill 之上加一层**闭环学习**:记住用户反馈并注回研报(诉求一),用 T+1 实际涨跌
> 自我复盘、诊断漏判、自迭代打分权重与经验(诉求二)。不引入第二套运行时。

## 1. 目标 / 非目标

**目标**
- **反馈记忆**:用户对任何研报的批注被结构化记住,蒸馏成"经验规则",在下次出研报时**自动注回**(L2/L3 prompt + 报告骨架)。
- **复盘自迭代**(仅 scan-market):每个交易日后,用当日**已实现** `fwd_1_oo`(T+1 开到开)检验前一日报告 → 归因"抓到/漏掉/误判" → ① 量化重标定自动落地 ② 结构性改动出建议待批 ③ 写经验。
- **半自动闭环**:IC 权重重标定**自动落地**(确定性+收缩,低风险);新因子/门槛/prompt 规则改动**只出建议**,用户批准才落地。
- **会话触发、自动补跑**:不挂 cron;用户下次开 session / 跑 scan 时,自动把"未复盘的交易日"一次性补上。零无人值守额度、零 cron 运维。

**非目标**
- 不替换 Claude Code 运行时;不引入 hermes-agent / 外部模型 provider / FTS5 / 消息网关。
- 闭环自迭代**不挂** analyze-ticker / macro(单票/无标的,结果信号稀薄、重标定无统计意义);这两个 skill 只接**反馈记忆**。
- 不做盘中实时;不做 T+5/T+10 为主口径(T+1 为主,T+5 仅作辅助诊断标注)。

## 2. 关键发现:闭环零件已有 80%

| 能力 | 现成零件 | 缺口 |
|---|---|---|
| 归因原料(每只×每日 因子×实际收益) | `factor_lab` 的 `forward_returns()` + 多日 `cache/` + `calibrate()` 的 panel | 一段"查漏"编排 |
| 量化自迭代 | `calibrate()→weights.json`(rank-IC + 申万行业层级收缩)**已是自调参闭环** | 自动用实际标签触发重跑 |
| 历史报告/面板 | `reports/scan/<date>/` + `context/scan/<date>/L1_scored_full.csv` 等全持久化 | 读它做复盘 |
| 反馈记忆原语 | Claude memory 系统 | 反馈**采集** + 按范围**召回注入** |

**真正缺的只有**:① 反馈采集口 ② 复盘归因编排 ③ 经验/反馈注回 prompt 与骨架。

## 3. 架构(三件)

### 3.1 知识库(记忆底座)— `context/knowledge/`(随 `context/` 一并 gitignore)
- `feedback.jsonl` — 情节记忆,append-only。每条:
  ```json
  {"id":"fb_20260620_001","ts":"2026-06-20T13:10:00","skill":"scan-market",
   "scope":{"kind":"global|sector|industry|ticker","value":"申万-电子|600519|*"},
   "report":"reports/scan/20260619/1553_summary.md","note":"<用户原话>",
   "verdict":"wrong_rating|missed|false_positive|good_call|process",
   "root_cause":"<Claude 蒸馏>","corrective_rule":"<Claude 蒸馏,可执行>",
   "lesson_id":"ls_xxx|null","status":"open|distilled"}
  ```
- `lessons.jsonl` — 语义记忆(策展),**经验的真值源**。每条:
  ```json
  {"id":"ls_winner_rate_topping","scope":{"kind":"global","value":"*"},
   "rule":"winner_rate>90=抛压/见顶,非筹码健康;低 winner_rate=套牢盘多=上行空间。",
   "evidence":["factor_lab T+1 IC -42bps","retro 2026-06-19 漏赢家 winner_rate 中位 50","fb_20260619_003"],
   "confidence":0.8,"created":"2026-06-19","last_reinforced":"2026-06-20",
   "reinforce_count":2,"status":"active|retired"}
  ```
- `proposals.jsonl` — 结构性改动建议(待批)。`{id,ts,kind:factor|gate|prompt_rule,summary,rationale,diff_sketch,status:open|approved|rejected|applied}`。
- `changelog.jsonl` — 自动落地的权重重标定审计。`{ts,retro_date,weights_before_sha,weights_after_sha,top_changes:[{group,industry,before,after}],panel_dates_n}`。
- `lessons.md` — 由 `lessons.jsonl` **渲染**出的人读视图(便于浏览/手工策展)。真值在 jsonl。

> **不要 FTS5**:经验条目策展后保持精简(几十~上百条),按范围过滤后整体加载即可。借 hermes 的"agent 自策展"**纪律**,不借它的**基建**。

### 3.2 复盘 retro(归因闭环,仅 scan-market)
- **确定性层 `scripts/retro.py`**(零 LLM,自测):
  1. `pending_days()` — 扫 `context/scan/<date>/`:有报告、无 `retro/done.json`、且 D 的 `fwd_1_oo` 已可得(D+2 开盘价在 factor_lab 口径下到位、交易日历校验)→ 返回待复盘日列表。
  2. `realized_returns(date)` — 复用 `factor_lab.forward_returns` 口径,取**当日全市场** `fwd_1_oo`(及辅助 `fwd_5_oc`)。
  3. `attribute(date)` — join 当日 `L1_scored_full.csv`(全打分面板:rank/recalled/composite/8 子分/因子)× 报告买单(读 `<HHMM>_detail/<ticker>.md`,复用项目 `parse_rating`)× 实际收益 → 给每只分桶:

     | 桶 | 定义 |
     |---|---|
     | `caught` | 赢家 ∧ 在买单(Overweight/Buy) |
     | `recalled_cut` | 赢家 ∧ recalled ∧ L2/L3 被 cut(没进买单) |
     | `missed_l1` | 赢家 ∧ 在 universe ∧ 未召回(composite 太低) |
     | `missed_l0` | 赢家 ∧ 不在 L1_scored_full(门槛/ST/市值误杀) |
     | `false_positive` | 在买单 ∧ 实际下跌(底 decile) |

     **赢家** = 当日可交易 universe 内 `fwd_1_oo ≥ 九分位` ∧ `≥ abs_thresh`(默认 3%)。
  4. 产出 `context/scan/<date>/retro/attribution.csv` + 各段命中率/当日 IC 的 `retro_input.md`(喂诊断)。

- **Claude 诊断层 `scan-retro` skill**(就是"对比涨得好的为什么没筛出来"):
  5. 读 `retro_input.md` + 漏掉/误判赢家的因子行 → **成群**对比选中票 → 说清**系统性病因**(三段药:L0 门槛误杀 / L1 权重压低 / L2-L3 AI 误判),并把"可归因因子病因"与"不可预测消息脉冲"**分开**(后者不拿去重标定)。
  6. **量化(自动落地)**:把当日并入多日面板 → 重跑 `factor_lab.calibrate()` → 新 `weights.json`;写 `changelog.jsonl`。**绝不靠单日翻权重**(多日滚动 + 收缩 k=200)。
  7. **结构性(出建议)**:新因子 / 改 L0 门槛 / 改 L2-L3 prompt 规则 → 写 `proposals.jsonl`(待批)。
  8. **写经验**:把反复出现的诊断升成 `lessons.jsonl`(`reinforce_count`++、confidence 升);停止复现的退休(`status=retired`)。
  9. **retro 报告** → `reports/scan/<date>/retro_<HHMM>.md`:漏斗各段命中率 / 漏赢家 top + 系统性病因 / 已自动落地的权重变化 / 待批结构建议 / 新增·强化·退休经验。用户可对 retro 报告 `/feedback` → 闭环。

### 3.3 注回(让记忆改变下一份研报)— `scripts/feedback_store.py`
- `record_feedback(...)` / `upsert_lesson(...)` / `add_proposal(...)` / `log_change(...)` — 读写四个 store(原子 append)。
- `lessons_for(scopes) -> list[lesson]` — 按范围过滤 active 经验(global 永远included;sector/industry/ticker 命中本批集合)。
- `render_calibration_block(scopes) -> str` — 把命中经验渲染成『因子方向经验校准』markdown 块,**替换** `screening-playbook.md` 里手写的那段(向后兼容:store 空时回退内置基线)。
- `render_lessons_md()` — 渲染人读 `lessons.md`。
- 接线:
  - `screening-playbook.md` L2/L3 prompt:校准块改为 `render_calibration_block(本批行业)` 注入。
  - `assemble_scan.py`:报告骨架对覆盖标的浮出"相关经验 + 未决反馈"。
  - scan-market `SKILL.md`:开跑前先 `retro.pending_days()`,有则提示/自动补跑。

## 4. 数据流(一圈)
```
D 收盘 → scan 出报告 D(reports + context/scan/D 面板)
                          │  你下次开 session / 跑 scan
                          ▼
        retro.py 补跑未复盘的 D(取 D 实际 fwd_1_oo)
                          ▼
        attribution.csv ──► scan-retro 诊断 ┬─量化自动落地→calibrate→weights.json(+changelog)
                                            ├─结构建议→proposals.jsonl(待批)
                                            └─写经验→lessons.jsonl
                          ┌──────────────────────────┘
        注回 L2/L3 prompt + 报告骨架 ◄── render_calibration_block / lessons_for
                          ▼
        retro 报告 → 你 →/feedback→ feedback.jsonl → 蒸馏 → lessons.jsonl(闭环)
```

## 5. 半自动边界(用户已定调)
- **自动落地**:仅 `factor_lab` IC 权重重标定(确定性、IC+收缩、多日滚动)。每次写 `changelog.jsonl`,可审计/回滚(保留前一版 `weights.json` 为 `weights.<sha>.json`)。
- **出建议待批**:新增候选因子、改 L0 门槛(市值地板/北交所/ST)、改 L2-L3 prompt 规则。批准后由人(或下一轮在用户确认下)落地;**prompt 规则改动须按 writing-skills 测过再上线**。

## 6. 防坑
- **零未来函数**:D 仅在 D 的 `fwd_1_oo` 已实现后复盘(交易日历 + 价格到位校验);沿用 factor_lab 的"D close 信号→D+1 open 买、剔 D+1 一字板"口径。
- **单日噪声**:自动落地=重跑多日滚动面板 + 收缩,绝非单日翻权重。
- **经验腐烂**:lessons 带 `confidence` + `last_reinforced` + `reinforce_count`;停止复现→退休(防 regime 翻转后死守旧规则)。
- **消息驱动假漏**:并购/停复牌/事件脉冲的赢家≠选股失败;诊断须标注并**排除出重标定样本**。
- **幂等**:retro 对同一 D 可重入(`retro/done.json` 标记 + 写操作幂等 upsert);反馈去重按 `(report, note hash)`。
- **向后兼容**:knowledge store 不存在/为空时,注回回退到当前内置的手写校准块——老路径不破。

## 7. 文件清单
**新增**:`scripts/retro.py`、`scripts/feedback_store.py`、skill `scan-retro/{SKILL.md,retro-playbook.md}`、skill `feedback/{SKILL.md,feedback-playbook.md}`、`context/knowledge/*`(运行期生成)。
**改**:`.claude/skills/scan-market/screening-playbook.md`(校准块→渲染注入)、`scripts/assemble_scan.py`(骨架浮出经验/反馈)、`.claude/skills/scan-market/SKILL.md`(开跑前补跑 retro)。

## 8. 分 Phase(每个独立可交付 + 自测)
1. **Phase 1 — 知识库 + 反馈采集 + 注回**:`feedback_store.py` + `feedback` skill + `render_calibration_block` 接进 screening-playbook + 骨架浮出。交付**诉求一**(记住反馈、注回研报)。
2. **Phase 2 — retro 归因 + 诊断 + 自动重标定 + 建议**:`retro.py` + `scan-retro` skill + 自动 calibrate 落地 + proposals。交付**诉求二**(自迭代)。
3. **Phase 3 — 经验生命周期 + 审计 + 打磨**:confidence/退休、`changelog`/`weights.<sha>` 回滚、`lessons.md` 渲染、scan-market 开跑补跑接线、消息脉冲过滤细化。

## 9. 验收标准
- `feedback_store.py --selftest` / `retro.py --selftest` 全过;ruff 全过。
- 端到端:对一个历史交易日(factor_lab cache 有 `fwd_1_oo`)跑 `retro.py attribute` → 产出 attribution.csv 分桶正确;`scan-retro` 诊断产出系统性病因 + 自动重标定写 `changelog` + 经验入 `lessons.jsonl`。
- 注回:`render_calibration_block` 命中经验时,生成的校准块包含该经验;store 空时回退基线。
- 反馈:`/feedback` 一条 → `feedback.jsonl` 落记录 → 蒸馏 → `lessons.jsonl` 出/强化经验 → 下次 scan 的 L2/L3 prompt 含该经验。
- 不碰 `fred.py`/`test_fred.py`/编辑器目录;不 commit;akshare/tushare 仍 venv-only;`uv run --no-sync`。

## 10. hermes-agent 取舍
**抄**:agent 自策展记忆(情节→语义的刻意提升)、skill 自改进(retro 提 prompt 改动建议,但经审批 + writing-skills 测试)、闭环 + 定时补跑。
**丢**:独立运行时、模型 provider、FTS5、消息网关——CC 已给底座,引入只会重新引入付费 LLM + 第二套运行时。
