# L2 粗排 v2 设计 — 双赛道 + 确定性分桶 + 推理留痕

> 日期 2026-06-20 · 状态:已批准实现
> 解决两个实测问题:① L2 **过分打压强势股**(T+1 校准的"过热=一刀切"误杀健康强势);② **token 大**(Opus 对全 1000 只逐一推理,387k/轮)。

## 1. 背景与问题

2026-06-18 全市场扫描复盘:科技(电子/半导体/算力)占召回 25%(250/1000),却 **0 进 buy-list**。逐段追踪发现最猛的科技票(pct_60d 180–346%)几乎全在 **L2 被砍**,因为现有 L2 rubric 把 `pct>150 + rsi>80 + winner>85` 当**回避红线一刀切**,**分不清**:
- 健康强势:生益科技 +205%、np **+105%**、主力还在 → 本该留;
- 衰竭垃圾:沃格光电 +346%、np **−112%**、主力流出 → 该砍。

现有 L2:召回 1000 → `slice_recall` 切 10×100 → 每批 1 个 **Opus** subagent 读 25 列表 + 单一(回归友好)rubric → keep top20% → `merge_l2_keeps` 按 `归一composite×归一l2_score` 取 200。成本 387k token/轮。

## 2. 目标

- **不再一刀切强势股**:给"趋势延续"一条带保底席位的独立赛道;用"主力还在 + 业绩跟得上"区分健康强势 vs 衰竭顶。
- **降本**:确定性分桶把 LLM 工作量从 1000 砍到模糊带 ~400;中间带判别换 **Sonnet**。目标 token −60%、成本 −85%。
- **推理留痕**:所有有中间结果的阶段(L2/L3/L4)的 prompt/批表/keep-judged/calib 归档进 `A_pipeline/reasoning/{l2,l3,l4}/`,发布报告自带可追溯的 LLM 输入。
- **向后兼容**:旧 `slice_recall/compact_table/merge_l2_keeps` 保留;新逻辑加在旁边;`render_calibration_block` 不带 lane 参数时逐字回退原基线。

## 3. 架构:L2a 确定性分桶 → L2b 双赛道 LLM → 配额合并

### 3.1 L2a 确定性分桶(零 LLM,`scan_pipeline.l2_pre_bucket`)
对召回 1000 逐只用 pandas + `uzi_lenses.classify_regime(row)` 打标签,新增列:
- `resonance`:看多因子组 `[score_momentum, score_fund_main, score_chip, score_north, score_tech, score_growth, score_value]` 中**非 NaN 且 ≥60** 的个数(0–7)。
- `healthy_strong` / `exhausted`(布尔,见 §3.4)。
- `regime` ∈ {`趋势`, `回归`, `过热衰竭`, `平庸`}。
- `l2a_action` ∈ {`auto_keep`, `llm`, `auto_cut`};`l2_lane` ∈ {`trend`, `reversion`, `—`}。

分桶规则(实测 6-18 召回 1000 切分 ≈ **auto_keep 95 / auto_cut 535 / llm 370**;llm 带 trend 309 / reversion 61):
- **auto_keep**(免 LLM 直接留):`regime ∈ {趋势, 回归}` ∧ `resonance ≥ min_reso_keep`(默认 **5**)∧ `not exhausted`。
- **auto_cut**(免 LLM 直接砍):`regime == 平庸`(无共振无边际、与强势股无关)**或** `regime == 过热衰竭` ∧ `np_yoy < 0`(真破)。
- **llm**(进中间带,争议带):其余;`l2_lane` = `trend`(regime ∈ {趋势, 过热衰竭})/ `reversion`(regime == 回归)。
- 设计:`min_reso_keep=4` 会令 auto_keep 278 > target 淹没 LLM;`=5` 留 95 高确信、把 ~370 争议交 LLM,token 仍 −63%。

### 3.2 L2b 双赛道 LLM(只判 `llm` 桶,Sonnet)
- `slice_l2_llm(bucketed, lane, batch_size=100)`:筛 `l2a_action=='llm' ∧ l2_lane==lane` 后按 composite 降序切片。
- `compact_table(df, lean=True)`:精简 12 列(`code,name,industry,composite,score_momentum,score_fund_main,score_chip,score_growth,pct_60d,main_net_ratio,winner_rate,np_yoy`)。
- **趋势 lane prompt**:不砍强势,只辨"健康 vs 衰竭";配 `render_calibration_block(scopes, lane='trend')` 的趋势版校准(动量为正、主力还在=健康、**只砍**放量滞涨/业绩证伪)。
- **回归 lane prompt**:现有 rubric(低 winner 有空间 + 主力进场 + 排陷阱)。
- 子 agent 模型:**Sonnet**(编排层 `Agent(model='sonnet')` 设定;playbook 注明)。

### 3.3 配额合并(`merge_l2_keeps_v2`)
`merge_l2_keeps_v2(auto_keep_df, trend_keeps, reversion_keeps, recall, target=200, trend_quota=50)`:
1. 池 = auto_keep ∪ trend_lane keeps ∪ reversion_lane keeps(去重,按 code)。
2. 排序键 = `归一(composite) × 归一(l2_score)`;auto_keep 无 l2_score 则赋中性 l2_score=70。
3. **先给 trend lane 命中者保底 `trend_quota` 席**(按键取 trend 池 top-quota),再用全池按键填满到 target。
4. 输出含 `l2_lane`、`l2a_action`、`l2_score` 列,写 `L2_coarse_keep200.csv` + 全量 `L2_scored_full.csv`(召回 1000 + 全标签 + kept 布尔)。

### 3.4 精修 trap/regime 判别(`uzi_lenses.classify_regime`)
新增纯函数(不动现有 `trap_signals`,harvest_context 仍用旧的);入参 = L1 因子行 dict。

`exhausted` = 任一命中:
- 放量滞涨/派发:`pct_60d ≥ 40` ∧ `main_net_ratio < −0.04`;
- 业绩证伪:`pct_60d ≥ 50` ∧ `np_yoy < 0`;
- 满获利盘+主力流出:`winner_rate ≥ 85` ∧ `main_net_ratio < 0`;
- 抛物线顶:`pct_60d ≥ 80` ∧ `rsi6 ≥ 85` ∧ `(main_net_ratio is None or < 0)`。

`healthy_strong` = `(pct_60d ≥ 40 或 score_momentum ≥ 70)` ∧ `(main_net_ratio is not None ∧ ≥ −0.01)` ∧ `(np_yoy is None 或 > 0)`。

`regime` 优先级级联:① `exhausted ∧ ¬healthy_strong` → `过热衰竭`;② `healthy_strong` → `趋势`;③ `winner_rate<40 ∧ main_net_ratio>0` → `回归`;④ `resonance≥4` → `趋势`(若 score_momentum≥70)否则 `回归`;⑤ 其余 → `平庸`。

返回 `{regime, resonance, healthy_strong, exhausted, reasons:[...]}`(reasons 给 L2b/溯源用)。

## 4. 推理留痕归档(`assemble_scan.py`)
发布 `<HHMM>_detail/` 后,把 staging `context/scan/<date>/` 下的中间推理件按前缀拷入 `A_pipeline/reasoning/`:
- `_l2_*` → `reasoning/l2/`;`_l3_*` → `reasoning/l3/`;`_l4_*` → `reasoning/l4/`;`_calib*` → `reasoning/l2/`。
- 涵盖:prompt md、批表 md、keep/judged csv、calib md、L2a_bucketed.csv。
- 缺失静默跳过(re-run 友好)。reports/ 已 gitignore,仅本地留痕。

## 5. 旋钮(默认值)
| 旋钮 | 默认 | 位置 |
|---|---|---|
| auto_keep 共振阈 `min_reso_keep` | **5**(≥5 ∧ 趋势/回归 ∧ 无衰竭) | `l2_pre_bucket` |
| auto_cut | 平庸全砍,或 过热衰竭∧np<0 | `l2_pre_bucket` |
| 趋势 lane 保底席位 | `trend_quota=50` / 200 | `merge_l2_keeps_v2` |
| L2b 模型 | `sonnet` | 编排(playbook) |
| 精简表列 | 12 列 | `compact_table(lean=True)` |
| 归档目录 | `A_pipeline/reasoning/{l2,l3,l4}/` | `assemble_scan` |

## 6. 测试
全部纯函数 + `_selftest`(零 LLM、零网络):
- `classify_regime`:健康强势(生益型)→ `趋势`∧¬exhausted;衰竭(沃格型 np<0)→ `过热衰竭`∧exhausted;低 winner+主力进 → `回归`;平庸 → `平庸`。
- `l2_pre_bucket`:合成 20 行 → auto_keep/llm/auto_cut 三桶非空、lane 正确、列齐全。
- `merge_l2_keeps_v2`:trend 池即便键偏低也占满 `trend_quota` 席;总数 = target;去重。
- `render_calibration_block(lane='trend')` 非空且含"主力还在/只砍衰竭";不带 lane 时逐字回退原基线(`==` 比对)。
- `assemble_scan` reasoning 归档:selftest 造 staging 假件 → 断言落到 `reasoning/{l2,l3,l4}/`。

## 7. 非目标
- 不改 L1 召回 / L3 / L4 / L5 的算法(仅 L5 加归档)。
- 不做 T+20 重校准(单独议题);趋势 lane 已部分缓解 horizon 错配。
- 不强制 commit(按用户指令)。
