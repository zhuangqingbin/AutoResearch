# 因子待验清单(制度化:先过 IC 门,再碰 composite)

> 规则(2026-07-02 定):任何新因子/新通道**先排队进本清单**,走统一验收,不合格不上线。
> 负结果**必须归档**(写进本表状态 + playbook 附录),防止半年后"好主意"复活重做。
> 已有负结果:预告事件通道(附录 E,两季事件研究)、L2 模型 zoo(OOS rank-IC 全负)。

## 统一验收标准

1. **样本**:factor_lab 成型日面板 ≥60 日(事件类先跑事件研究,n≥100 事件);
2. **强度**:|rank-IC 均值| ≥ 0.02 且 IC-IR 方向稳定(两半样本同号);
3. **regime 分桶复核**(trend/range/risk_off 至少不反噬主 regime);
4. **增量**:对现有 composite 的边际贡献(与近邻因子相关性 <0.8);
5. 通过 → `proposals.jsonl` 提名(人批)→ calibrate 接权重;不过 → 状态记"否决+原因"。

## 队列

| 因子/通道 | 假设 | 数据源(端点) | 状态 | 备注 |
|---|---|---|---|---|
| 一致预期 EPS 修正 | 修正动量领先价格 | report_rc(1次/时限频) | **前向积累中**(consensus.py,2026-07-02 起) | ≥60 日才进 factor_lab |
| 两融余额变化 | 杠杆资金流向 = 情绪/动量确认 | margin_detail / margin | 待验 | 高权限 token 可拉;先 harvest 30 日冒烟 |
| 股东户数变化 | 户数降 = 筹码集中(吸筹佐证) | holder_number | 待验 | 季频+不定期披露,注意前视 |
| 52 周高距离 | 距新高近 = 动量延续(经典) | 现有 lake close 可算 | 待验 | 零新数据成本,优先验 |
| 开盘 gap 侵蚀 | 买单 T+1 开盘跳空吃掉 edge → 入场纪律 | attribution.gap_d1(已有) | **buy_ledger 已接**(度量非因子) | 读数:买单 gap 分布 |
| 回购/增持公告 | 公司行为正信号 | repurchase / 股东增减持 | 待验 | 事件类 → 事件研究先行 |
| 解禁临近(负) | 大解禁前抛压 | share_float(**已接日历**) | **已作风险旗上线**(非因子,L4 简报) | 若要进 composite 需负因子验收 |

## 流程

`python -m autoresearch.data.harvest`(如需新 lake 字段)→ factor_lab 面板加列 →
`factor_lab eval`(IC/IR/分桶)→ 本表更状态 → 过线才提 proposal。
**别跳队**:直觉再好也先过门——vol_ratio/winner_rate 就是被 IC 门剔掉的"直觉好因子"。
