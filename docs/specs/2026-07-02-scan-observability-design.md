# scan 观察性主干(run_health / 索引 / 日记 / changelog_ledger / 重放快照 / 阶段效能)设计

日期:2026-07-02 ｜ 分支:feat/scan-observability ｜ 状态:实施中

## 动机

三环(现场/记忆/学习)已建,但缺**观察性主干**:
1. **脏数据静默毒化**:因子 NaN 率高的日子照常进归因/校准(intraday EOD 缺失曾触发过),没人喊。
2. **中间结果导航靠记路径**:trace 全在,但"第二天复盘看现场"需要人肉拼路径。
3. **纵向无叙事**:9 本 ledger 都是按仪器纵览,没有"按日一行"的驾驶舱。
4. **自学习无元评估**:changelog 记了每次重标定,但没人回头看"标定后 IC 真变好了吗"。
5. **重放缺输入**:run 只留输出,当日用的权重/regime 没固化进现场。
6. **L4 阶段效能无读数**:早停率、P4 翻盘率不可测 → 想砍阶段只能拍脑袋。

## 设计(全确定性,零 LLM;presence-gated,老路不破)

### 1. `autoresearch/scan/health.py`(新)
- `run_health(scan_dir) -> dict`:artifacts 在位表 + missing、counts(l1/recall/l2/finalists/cards/**buys**)、
  L1_recall 关键因子 NaN 率 + `degraded_fields`(>30%)、meta 回显(regime/l2_engine/weights_source)、
  `churn`(finalist 逐日重叠)、`l4_phases`(早停/满卡/复用分布 + P4 翻盘)。
- `count_buys(scan_dir)`:finalists → parse_rating(卡)→ verify 降级折回 → ≥OW 计数(lazy 复用 assemble helpers)。
- `finalist_churn(scan_dir)`:与上一 scan 日 finalists 的重叠(卡片 TTL 复用的前置测量)。
- `l4_phase_stats(scan_dir)`:解析 details/*.md 的 `早停因`/`♻️ 复用`/`进入P4倾向: <Rating>` 标记
  → n_earlystop / n_full / n_reused / p4_seen / p4_flips(**playbook 新契约**:survivor 进 P4 时记一行倾向评级)。
- `write_run_health(scan_dir)` → `<scan_dir>/run_health.json`(assemble 时写,发布层随 trace 带走)。
- `index_md(scan_dir, report_dir)`:报告目录 `index.md`——summary/决策卡/trace 链接 + staging 在位表 +
  上一 run 链接 + 健康一行。**"现场保留完备、方便看中间结果"的导航入口。**

### 2. `autoresearch/learning/journal.py`(新)
`roll(scan_root)` 每 scan 日一行:date/regime/菜单(落刀%·健康数)/finalists/卡/买/触发/市场 fwd(retro 成熟后回填)/retro 状态
→ `reports/learning/journal.md`。按日叙事主干,与各 ledger(按仪器)正交。

### 3. `autoresearch/learning/changelog_ledger.py`(新)
每条 changelog(recalibrate)→ 采纳日前后各 ≤k 个 retro 日的**日度 composite rank-IC** 均值对比 → delta。
`n<3` 标 ⚠样本少。→ `reports/learning/changelog_ledger.md`。**自学习的元评估:学了到底有没有用。**

### 4. universe.py(改)
- meta.json 增 `"regime": <label|None>`(pick_weights 已返回,原来丢掉了)。
- 落 `<scan_dir>/weights_used.json`(当日实际权重快照;失败仅 stderr 警告不阻断)。
- **重放**:`universe <date>` 本就确定性,现场自带输入(lake cache + weights_used + meta 参数)= 可复现。

### 5. assemble.py(改)
发布时 `write_run_health(scan_dir)`(在 `_publish_pipeline` 前,mapping 增一行随 trace 带走)+
`index.md` 写入报告目录。全部 suppress 包裹,失败不阻发布。

### 6. retro.py(改)
`_health_section(sdir)`(新小函数,单测友好):读 run_health.json,降级字段/缺产物 → retro_input
"运行健康"节 + "勿把数据病当因子病"提示。缺文件 → []。

## 不做
- 精确 token 计量(需编排层逐次记 usage,超出确定性层能力——沿用输出字节下界口径)。
- retro 自动排除脏数据日(只提示人判;自动排除待 degraded 语义积累样本后再议)。

## 测试
合成 fixture、无网络:test_health.py(core/churn/phases/buys/index/assemble 接线)、
test_journal.py、test_changelog_ledger.py、retro `_health_section` 单测。
