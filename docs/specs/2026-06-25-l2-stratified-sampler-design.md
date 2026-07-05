# L2 粗排重设计 —— 确定性分层多样性采样器(ML-free)

> 2026-06-25。取代「champion ML 排序 + 单风格 lane 配额」。把 L2 从"预测涨跌的排序器"右-size 成
> "给 L3/L4 建均衡菜单的确定性采样器"。**设计经 4 年回测锚定(附录 B)。**

## 1. 问题(为什么改)

- 部署的 `l2_fwd5` champion 是 xgb,**OOS rank-IC = −0.023(负=反预测)**,只因 `gate=beats_linear` 当"最不伤切"上线;leaderboard 全模型全 horizon 皆负。
- 后果:在反转 regime 下它把**全部动量/heat 票压出 L2**(实测 quota=0 时 momentum 0/200、heat 0/200、健康图形仅 1%)→ L3/L4 只剩超卖落刀可选(用户实测"形态全很差")。
- 回测定论(附录 B):**确定性 L2 无稳健 alpha**(composite-top200 全样本 ≈ 0,t<1),且 **regime 依赖**(2022-2024 +14~28bps,2025-26 反转 −24bps)。→ 别指望 L2 产 alpha;它的价值在**多样性覆盖**,alpha 交 L3/L4。

## 2. 设计原则

1. **L2 不赌 regime**:固定 floor 保证每风格不为 0;regime 判断交 L3/L4(Claude)。
2. **L2 不预测**:配额是 policy(要多少多样性),桶内是同质选择——都不是预测问题,故**全程无 ML**。
3. **分层免费**:回测证 strat[composite] ≈ composite-top200 ≈ 0 → 多样性零 alpha 代价,白拿。

## 3. 组件

### 3.1 风格桶(复用 recall_channels provenance,零新增)
召回已给每只票打了 channel 标签。6 个风格桶 = 对应 channel:
`趋势`(momentum/heat)· `反转`(reversal)· `价值`(value)· `成长`(growth)· `吸筹`(accumulation)· `主力`(main_fund)。一只票可属多桶(recall_channels 含多个)。北向/composite 不单列桶。

### 3.2 桶内 + merit 核排序信号 = **sector-neutral composite**
`sn_composite = composite − mean(composite over 申万一级 industry)`。回测最优(附录 B):每桶 per-bucket IC 最高、去行业 beta 后当前 regime 最不伤(−15 vs 原 composite −20bps)。**不用** style-own 分(反预测,趋势 IC −0.076)、**不用** 多日资金流(全桶负 IC)、**不用** 低-winner 叠加(不 stack)、**不用** 模型(无 alpha)。

### 3.3 固定 floor(policy,可配置)
默认:`趋势 20 · 反转 12 · 价值 12 · 成长 12 · 吸筹 12 · 主力 10`(Σ=78,merit 核 122)。`channel_ledger` 的 `unique_excess_t5` 持续为负且 n_days≥3 → 人工下调该桶 floor(写 proposals,不自动改)。

### 3.4 sector cap
任一申万一级在最终 200 中 ≤ `sector_cap_frac × l2_n`(默认 0.20 = 40 只)。填充时超限则跳过、顺延下一只。

## 4. 算法(确定性,零 LLM)

```
输入:recall_df(含 composite, industry, recall_channels), l2_n=200, floors, sector_cap_frac
1. sn = composite − groupby(industry).composite.mean()              # sector-neutral 分
2. merit_need = l2_n − Σfloors
   merit = top(merit_need) by sn,过 sector_cap                      # merit 核
3. for 风格 in floors(按当前桶内已选数升序=最缺的先填):
     have = merit ∩ 该风格 的数量
     需补 = max(0, floor − have)
     从「该风格 ∩ 未选 ∩ 不破 sector_cap」按 sn 降序补 需补 只
4. 不足 l2_n(floor 桶成员不够)→ 用剩余 by sn 回填到 l2_n
5. 标 l2_lane_reserved = floor 补进来的(非 merit 核)
返回 200 行 + l2_lane_reserved
```

**回落/parity**:`floors 全 0 + sector_cap=1.0` → 退化为 `sn_composite top-200`;`score_col="composite"` 且 floors=0 → 严格复现旧 composite top-200(parity 锚)。

## 5. ML 处置

删除 L2 的 champion 调用(`champion_scores`)。`l2_engine` 记 `stratified(sn_composite)`。zoo/champion 基建保留在 `models/`(measure-only,不接 L2);**不留自动顶上 hook**(用户定:不要模型)。

## 6. 度量改口径(retro / stage_eval)

L2 不再用 IC/keep-cut-lift 当 KPI(那是预测标尺,L2 不预测)。改看:
- **风格/行业均衡度**:200 的风格覆盖(每桶 ≥floor)+ 行业 Herfindahl ≤ 阈值;
- **赢家存活率**:retro `recalled_cut` 桶(赢家被 L2 切掉的)占比,越低越好。
stage_eval 保留 lift 作**诊断**(不是优化目标),并新增"各风格桶 floor 命中的赢家覆盖"。

## 7. 接线 + 测试

- 新模块 `autoresearch/scan/recall/l2_stratify.py::stratified_l2(...)`(纯函数)。
- `universe.run` 与 `L2Rank` stage **共用** 它(golden parity)。`config.ScanConfig` 增 `l2_floors`/`l2_sector_cap`,弃 `l2_lane_quota`(保留兼容映射:旧 quota>0 → 用默认 floors)。
- 测试:`test_l2_stratify.py`(floor 保底 / sector_cap / 多 channel 归桶 / 回落 parity);`test_parity.py` 两管道一致(都走 stratified);`test_cli.py` 默认值。

---
## 附录 A · 边界

- 多 channel 名:计入所属任一桶;填充按"最缺的桶先填"。
- backfill / 无 channel 名:只进 merit 核。
- 桶成员 < floor:floor 欠填,merit/回填吸收。
- `np_yoy`/`roe` 缺历史(lake 无 yjbb)→ 成长桶 floor 设计沿用 sn_composite 口径,实测见 B 的边界。

## 附录 B · 回测证据(83 成型日 × 2022-06~2026-05,fwd_5_oc,buyable;`scratchpad/bt_*.py`)

**(1) 桶内信号 per-bucket rank-IC**:composite 在每桶最高(趋势 +0.048 / 价值 +0.024 / 主力 +0.056 / 吸筹 +0.040 / 反转 +0.001);style-own 多为负(趋势动量分 **−0.076**);cmf/obv/volprice 全桶负;低-winner 在主力桶 +0.057。

**(2) 整池 L2-200 前瞻超额(vs 截面均值,skew-robust)**:composite-top200 **−1bps t−0.09**;random-200 +5bps(≈0);strat[composite] **−0bps**(≈ top200=**分层免费**);strat[own_style] **−13bps**(更差);加 flow/negwin 无改善。

**(3) horizon 稳健**:composite-top200 fwd_1 −5 / fwd_5 −1 / fwd_10 +1bps(t 全<1)。

**(4) regime 分段**:2022熊 +20 / 2023震荡 +14 / 2024含动量 **+28** / **2025-26反转 −24bps** → "负-IC"是当前 regime 现象。

**(5) sector-neutral 头对头(stratified,fwd_5)**:sn_composite 全样本 +1bps(t+0.11)/当前 regime −15bps —— 均优于原 composite(−0 / −20)与 +negwin 叠加(−18)。→ **桶内用 sn_composite**。

**诚实结论**:确定性 L2 无稳健 alpha、regime 依赖;最优确定性口径 = sector-neutral composite;分层免费。故 L2 = 免费的多样性采样器,alpha 在 L3/L4。
