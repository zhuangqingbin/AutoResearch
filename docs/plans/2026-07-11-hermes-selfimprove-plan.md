# Plan B:hermes 借鉴 —— prompt_patch 自改进提案 + FTS 判例检索

spec: docs/specs/2026-07-11-recall-gate-pinned-config-design.md §5。
门/纪律同 A1。执行顺序:Wave A 之后;T1/T2 与 T3/T4 两条线内部有序、线间独立可并行。
不做边界(用户裁定):cron 化运维不做;锚字符串是 patch 禁区。

### Task 1: prompt_patch 提案约定 + 产出端

**Files:** Modify `autoresearch/learning/feedback_store.py`(便捷函数 `add_prompt_patch(target_file, anchor_text, current_text, proposed_text, evidence)` → 组 payload 调 `add_proposal(kind="prompt_patch", diff_sketch=...)`,:391 已有载体;**校验**:target_file 必须存在、proposed_text 不得删除/改写契约锚字符串集[l4-card 三锚等,常量表列出]、open prompt_patch 计数 ≤5 否则 raise)、`.claude/skills/scan-retro/*`(playbook 加「起草 prompt_patch」节:同型失误 ≥2 次+账本读数支撑才起草;grep 定位 playbook 文件名)、M2 裁决文档(lesson 四操作加第五出口「毕业→固化为 playbook 正文,lesson 退役」;grep `四操作\|M2` 定位所在 skill 文档);Test `tests/learning/test_prompt_patch.py`(合法起草/锚禁区 raise/超 5 raise)。

- commit `feat(learning): prompt_patch 提案(经验→提示词补丁·锚禁区·open≤5)+ retro 起草节 + lesson 毕业出口`。

### Task 2: 提案 show/apply 辅助流

**Files:** Modify proposals CLI(grep `open_proposals` 的 CLI 入口定位):加 `show <pid>`(打印 target+current→proposed diff 与证据)与 `apply <pid>`(**不自动改文件**——打印施工指引;若 target 在契约文件集[l4-card.md/lite-playbook.md/SKILL/STAGES]则指引末尾强制列出必跑命令:test_agent_defs + doc-lint;施工由会话中人批后进行,`set_proposal_status` 收尾);Test 追加 show/apply 输出断言。

- commit `feat(learning): proposals show/apply 辅助(prompt_patch 施工指引+契约文件强制验门)`。

### Task 3: FTS 判例索引 CLI

**Files:** Create `autoresearch/learning/precedents.py`;Test `tests/learning/test_precedents.py`。

- 探针先行(实现内建降级):`sqlite3` FTS5 可用 → FTS5 虚表;不可用 → 普通表+LIKE(同一查询接口,测试两分支)。
- `build_index(days=90)`:walk `context/scan/*/details/*.md`(解析 date/code/name/rating/门型[grep 卡内 `OW三门` 段]/正文)+ lessons → upsert `context/knowledge/precedents.db`(gitignored;按 (date,code) 幂等,增量=只补缺日)。
- `query(sector, gate, flags, k=3, days=90) -> list[dict]`:每条 {date, code, name, 一句结局[评级+触发位摘要], fwd_2[join attribution,缺=None]}。
- CLI:`python -m autoresearch.learning.precedents build|query ...`。
- commit `feat(learning): 判例 FTS 索引(cards+lessons·增量幂等·FTS5缺则LIKE降级)`。

### Task 4: L4 判例注入块

**Files:** Modify `autoresearch/scan/agents/l4_card.py`(`compose_funnel_brief` 逐卡块加 `_precedent_mark(base, code6, sector, gate_hint)`:查 top-k≤3 判例,渲染「📚 判例(跨票同型,advisory)」块,每条一行;**presence-gated**:无 db=返 "";异常降级空串,风格同 `_inst_mark` 家族;token 预算 ≤400/卡);Test `tests/scan/test_l4_precedent_mark.py`(有库有果/无库空串/异常降级)。

- **铁律**:只进逐卡块,决不触 `write_dispatch_pack` 共享前缀——`tests/scan/test_l4_prompt_cache_prefix.py` 必绿。
- commit `feat(scan): L4 简报注入跨票判例块(📚·k≤3·presence-gated·不触共享前缀)`。

### Task 5: 真库冒烟(跑动型,controller)

- 真跑 `precedents build`(现有 ≥14 日卡片)→ 记索引行数;单票 query 演示(半导体×估值门)贴 progress.md;一张 scratch 卡验 📚 块渲染与 token 增量 ≤400。**下次真实扫描观察判例块的实际引用质量**(L4 卡是否引用判例作旁证),据此调 k 与查询键。
