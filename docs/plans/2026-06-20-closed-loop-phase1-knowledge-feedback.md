# Phase 1 — 知识库 + 反馈采集 + 注回(实现 plan)

**Goal:** 交付诉求一 —— 用户对研报的反馈被结构化记住、蒸馏成经验、**自动注回**下一份研报。
**Spec:** `docs/specs/2026-06-20-closed-loop-learning-design.md` §3.1 §3.3。
**前置:** 无(纯增量;`context/knowledge/` 随 `context/` gitignore)。

## 文件
- 新增 `scripts/feedback_store.py`(store 读写 + 召回 + 渲染 + 自测)
- 新增 `.claude/skills/feedback/SKILL.md` + `feedback-playbook.md`
- 改 `.claude/skills/scan-market/screening-playbook.md`(校准块→渲染注入,基线作 fallback)
- 改 `scripts/assemble_scan.py`(报告骨架浮出经验 + 未决反馈)

## Task 1 — `feedback_store.py` 骨架 + JSONL 原语
- `KNOW = Path("context/knowledge")`;四个文件常量 `FEEDBACK/LESSONS/PROPOSALS/CHANGELOG`。
- `_append_jsonl(path, rec)`(mkdir parents, utf-8, `ensure_ascii=False`)、`_read_jsonl(path)->list[dict]`(不存在→[])。
- `_next_id(prefix, day)` 用已有记录计数生成 `fb_<yyyymmdd>_NNN` / `ls_<slug>`。
- **不可用 `Date.now()` 之类**——`ts`/`day` 由调用方传入(脚本里用 `datetime.now()` 可,这是普通脚本非 workflow)。
- 测:append→read round-trip。

## Task 2 — 反馈 + 经验 + 建议 + 审计 的写接口
- `record_feedback(skill, scope, report, note, verdict, root_cause, corrective_rule, ts, lesson_id=None)`→ 组 id、append、return。
- `upsert_lesson(slug, scope, rule, evidence:list, confidence, day)`:存在→`reinforce_count`++、`last_reinforced=day`、evidence 并集、`confidence=min(0.95, 旧+0.05)`;不存在→create(`status="active"`,`reinforce_count=1`)。
- `retire_lesson(slug, day)` → `status="retired"`。
- `add_proposal(kind, summary, rationale, diff_sketch, ts)`、`set_proposal_status(id, status)`。
- `log_change(retro_date, before_sha, after_sha, top_changes, panel_dates_n, ts)`。
- 测:upsert 新建 vs 强化(count 2、confidence 升、evidence 合并);retire 后 `lessons_for` 不返回。

## Task 3 — 召回 + 校准块渲染(注回核心)
- `scope_match(lesson_scope, query_scopes)->bool`:lesson `kind=global` 永真;否则 `(kind,value)` ∈ query_scopes。
- `lessons_for(query_scopes)->list[dict]`:active ∧ scope_match,按 confidence desc。
- `_BASELINE_CALIBRATION`(常量)= 把 `screening-playbook.md` 现有手写的『因子方向经验校准』整段搬进来(fallback,保证 store 空时行为不变)。
- `render_calibration_block(query_scopes)->str`:
  ```python
  hits = lessons_for(query_scopes)
  if not hits:
      return _BASELINE_CALIBRATION
  lines = ["## ⚠️ 因子方向经验校准(自学习,务必写进每个 subagent prompt)"]
  for L in hits:
      tag = "" if L["scope"]["kind"]=="global" else f"[{L['scope']['value']}] "
      lines.append(f"- {tag}{L['rule']}  _(conf {L['confidence']:.2f}; {'/'.join(L['evidence'][:2])})_")
  return "\n".join(lines) + "\n\n" + _BASELINE_CALIBRATION  # 经验叠加在基线上,不丢基线
  ```
- `render_lessons_md()->str`:active 经验人读视图(Phase 3 落盘 `lessons.md`,本期先提供函数)。
- 测:`lessons_for` 范围过滤(global 命中、行业命中、不相关不命中);`render_calibration_block` 空→基线、有→含经验行 + 基线。

## Task 4 — `feedback` skill(跨 skill 采集)
- `SKILL.md`:description「Use when 用户对某份研报/扫描结果给出反馈或纠正、或显式 /feedback —— 把反馈结构化记住并蒸馏成经验」。
- `feedback-playbook.md` 流程:
  1. 定位被评报告(最近一份 or 用户指明)→ `report` 路径。
  2. Claude 判 `verdict`(wrong_rating/missed/false_positive/good_call/process)+ `scope`(global/sector/industry/ticker)。
  3. 蒸馏 `root_cause` + `corrective_rule`(可执行、≤40 字)。
  4. `feedback_store.record_feedback(...)`。
  5. **决定是否升语义**:若该纠正可泛化(非一次性)→ `upsert_lesson(slug, scope, rule=corrective_rule, evidence=[fb_id, ...], confidence=0.6, day)`,并回填 feedback 的 `lesson_id`、`status="distilled"`。
  6. 回执用户:记了什么 + 是否升成经验 + 下次哪个 skill/阶段会用上。
- **零 LLM 脚本边界**:store 是确定性的;判断/蒸馏由 Claude 在 session 内做(零付费)。

## Task 5 — 接线注回
- `screening-playbook.md`:L2/L3 prompt 段,把手写『因子方向经验校准』替换为指令:**构造 prompt 前调用 `feedback_store.render_calibration_block(本批申万行业 scopes)` 注入**;并注明 store 空时自动回退基线(基线文本已搬进 `_BASELINE_CALIBRATION`,文档保留一句指针即可)。
- `assemble_scan.py`:`build_summary` 末尾(或组合视角处)加"📌 经验 / 未决反馈"小节 —— `lessons_for(buy-list 行业+个股 scopes)` + open 状态 feedback 命中覆盖标的的,列出来。向后兼容:store 空→不输出该节。

## Task 6 — 测试 + 验收
- `uv run --no-sync python scripts/feedback_store.py --selftest`(覆盖 Task1-3 全部断言)。
- ruff clean(`feedback_store.py` + 改动文件)。
- 端到端手验:`record_feedback`+`upsert_lesson` 把本 session 手动发现的 winner_rate 经验入库 → `render_calibration_block([("global","*")])` 含该经验行 + 基线;清空 store → 回退纯基线。
- 不碰排除文件;不 commit。

## 验收
诉求一闭环可用:给一条反馈 → 落 `feedback.jsonl` → 升 `lessons.jsonl` → 下次 scan 的 L2/L3 校准块自动含它;store 空时与今天行为完全一致。
