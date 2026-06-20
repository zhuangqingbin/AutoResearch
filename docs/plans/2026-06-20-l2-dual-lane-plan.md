# L2 粗排 v2 实现计划

> 配套 spec:`docs/specs/2026-06-20-l2-dual-lane-design.md`。每个 Task 改一处 + 写 `_selftest` 跑绿。零 LLM、零网络可测。

**执行子技能**:inline 执行(本 session 一个 part 一个 part 做完,中途不打扰),每 Task 末跑该脚本 `--selftest`。

---

### Task 1: `uzi_lenses.classify_regime`(精修判别基座)
**Files**:Modify `scripts/uzi_lenses.py`(新增函数 + selftest 段,不动 `trap_signals`)
- [ ] 新增 `classify_regime(row: dict) -> dict`:按 spec §3.4 算 `resonance / healthy_strong / exhausted / regime / reasons`。
- [ ] `_selftest` 加 4 断言:生益型(pct205+np105+主力还在)→ `趋势`∧¬exhausted;沃格型(pct346+np−112+主力流出)→ `过热衰竭`∧exhausted;低 winner(35)+主力进(0.03)→ `回归`;平庸(共振 0)→ `平庸`。
- [ ] 跑 `uv run --no-sync python scripts/uzi_lenses.py --selftest` 绿。

### Task 2: `feedback_store` 趋势版校准块
**Files**:Modify `scripts/feedback_store.py`
- [ ] 加 `_BASELINE_CALIBRATION_TREND`(趋势友好:动量为正、主力还在=健康、只砍放量滞涨/业绩证伪;低位回归经验仍提示但不主导)。
- [ ] `render_calibration_block(query_scopes, lane="reversion")` 加 `lane` 参数;`lane=='trend'` → 趋势块(叠加学到经验),否则**逐字**走原基线。
- [ ] `_selftest` 加:`lane='trend'` 非空且含关键词;**不带 lane 调用结果 == 改前基线**(`==` 比对,老路径不破)。
- [ ] 跑 `--selftest` 绿。

### Task 3: `scan_pipeline` L2a 分桶 + lane 切片 + 精简表 + v2 合并
**Files**:Modify `scripts/scan_pipeline.py`
- [ ] 加 `_L2_COLS_LEAN`(12 列);`compact_table(df, lean=False)` 加 `lean` 参数(True 用精简列)。
- [ ] 加 `l2_pre_bucket(recall_df) -> DataFrame`:逐行 `classify_regime` → 加 `resonance/healthy_strong/exhausted/regime/l2a_action/l2_lane` 列(spec §3.1)。
- [ ] 加 `slice_l2_llm(bucketed, lane, batch_size=100)`:筛 `l2a_action=='llm' ∧ l2_lane==lane`,composite 降序切片,yield `(idx, df)`。
- [ ] 加 `merge_l2_keeps_v2(auto_keep_df, trend_keeps, reversion_keeps, recall, target=200, trend_quota=50)`(spec §3.3:保底席位 + 去重 + 排序键)。
- [ ] `_selftest` 加:合成 ~24 行召回 → `l2_pre_bucket` 三桶非空、列齐;`slice_l2_llm('trend')` 只含 trend;`merge_l2_keeps_v2` trend 占满 quota、总数=target、去重。
- [ ] 跑 `--selftest` 绿。

### Task 4: `assemble_scan` 推理留痕归档
**Files**:Modify `scripts/assemble_scan.py`
- [ ] 加 `_archive_reasoning(scan_dir, pipeline_dir)`:把 `_l2_*/_l3_*/_l4_*/_calib*/L2a_bucketed.csv` 按前缀拷入 `<detail>/A_pipeline/reasoning/{l2,l3,l4}/`;缺失跳过。
- [ ] 在发布 A_pipeline 处调用它。
- [ ] `_selftest`(或新增轻测)造 staging 假件 → 断言落到 `reasoning/{l2,l3,l4}/`。
- [ ] 跑 `assemble_scan --selftest` 绿(若无 selftest 入口则加最小 `--selftest`)。

### Task 5: `screening-playbook.md` 重写 L2 段为 v2
**Files**:Modify `.claude/skills/scan-market/screening-playbook.md`
- [ ] L2 段改为:L2a 确定性分桶(`l2_pre_bucket`)→ L2b 双赛道(trend/reversion)Sonnet 扇出(只判 llm 桶,趋势 lane 注 `render_calibration_block(lane='trend')`)→ `merge_l2_keeps_v2` 配额合并。
- [ ] 写两个 lane 的 subagent prompt 模板(趋势:不砍强势只辨健康/衰竭;回归:沿用)。
- [ ] 注明 L2b 子 agent 用 **Sonnet**;中间件落 `A_pipeline/reasoning/`。

### Task 6: 全量验收
- [ ] `uv run --no-sync python scripts/{uzi_lenses,feedback_store,scan_pipeline,assemble_scan,self_review,retro}.py --selftest` 全绿。
- [ ] `ruff check scripts/` 通过。
- [ ] 不 commit(待用户指令);不碰排除文件。
