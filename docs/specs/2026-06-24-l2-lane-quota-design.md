# L2 lane 配额(保留召回多样性到下游)设计

> 状态:已验证(84 天反事实回测),待实现。分支 `l2-lane-quota`。
> 关联:`2026-06-22-l1-multi-recall-design.md`(9 路召回)、`2026-06-20-l2-dual-lane-design.md`(L2 双赛道·未落地)、`2026-06-24-l4-progressive-depth-design.md`(下游 L4)。

## 1. 目标

让 L1 多路召回注入的**多样性 lane**(momentum/heat/growth/accumulation)能**穿过 L2 粗排**到达 L3/L4 的 Claude 判断,而不是被单一 `l2_fwd5` champion 的反转倾向同质化抹平。一句话:**给催化剂/动量龙头(药明/普冉这类)一个被 Claude 看见并尽调的机会**——大多数会被 rubric 三门正确否掉,换取偶尔浮出纯量化栈结构上永远捞不到的尾部赢家。

**非目标**:不追求 L2 平均 IC 提升(个股动量 IC 为负);不改 champion 模型;不默认开启(保 parity)。

## 2. 诊断(为什么需要)

现行 `L2Rank` / `universe.run` 的 L2 是**纯单分排序**:`champion.predict(recall) → 按分降序 → head(l2_n)`,**无任何 lane/配额逻辑**。champion `l2_fwd5` 特征含 composite、反转 regime 下学到的就是反转,于是:

- **逐通道 floor→L2 存活表(06-23)**:9 路里只有 `composite`(独家→L2=31)、`reversal`(17)在贡献存活;`growth` 勉强(2);`value/heat/momentum/accumulation/main_fund` 的独家保命名额 → L2 存活**全是 0**。**L1 注入的多样性,L2 一关按 composite 同款口径全杀光。**
- **普冉股份**(688766):composite 22.1 / 全市场 4325 名,靠 `growth#2`(floor 保护)进了召回,**却在 L2 GBDT 被砍**(`L1✓→L2✗`)。**药明康德**(603259):heat#109 越过 floor、单通道 + 低 composite → **召回都没进**。
- L3 的 `merge_l3_finalists_v2` 有 `trend_quota`(给 lane=="trend" 保底),但它在 **200→30**;momentum/heat 在 **1000→200 就死了,trend_quota 见不到**。**缺口精确在 L2。**

## 3. 验证(84 天反事实,已跑)

在 factor_lab 84 天面板(有实现 fwd5)上原样重建 L1→L2,模拟"L2 留 Q=30 个 lane 席"(lanes=momentum/heat/accumulation,**growth 因面板无基本面未测 → 结果是下界**):

| 池子 | n | 均值fwd5 | 命中 | 中位 | p90 | max |
|---|---|---|---|---|---|---|
| displaced(被挤掉的 core 尾 Q) | 2520 | +0.572% | 51% | +0.17% | +6.08% | +56% |
| **reserved(配额救回的 Q)** | 2513 | **+0.795%** | 48% | **−0.16%** | **+13.1%** | +71% |
| killed_all(全部被杀 lane) | 26354 | +0.833% | 49% | −0.12% | +11.0% | +106% |

**解读**:reserved 均值 ≥ displaced 且 ≥ core 平均(配额不亏平均收益);但**中位负、命中<50%、日胜率 41/84=49%** → 典型 reserved 是哑弹,正均值**全靠肥尾**(p90 是 displaced 的 2.16×,max +71%)。**定性:尾部可选性,非稳定 edge;赚钱依赖下游 Claude 把尾从哑弹里挑出来。** 故**默认关闭、opt-in**。

## 4. 设计

### 4.1 共享 helper(确定性,单测核心)

新增 `autoresearch/scan/recall/l2_quota.py`:

```python
def apply_l2_lane_quota(ranked, l2_n, quota, lane_channels):
    """ranked: 已按 l2 分降序的召回帧(含 recall_channels, pct_60d)。
    返回恰 l2_n 行:core = top(l2_n−quota);reserve = core 线下、recall_channels∩lane_channels
    的票按 hybrid(半 l2 分 + 半 pct_60d)取 quota;reserve 不足由 score 回填到 l2_n。
    新增列 l2_lane_reserved(bool)。quota<=0 → 逐值复现 head(l2_n)(parity 锚)。"""
```

算法:
1. `quota<=0 or len(ranked)<=l2_n` → `ranked.head(l2_n)` + `l2_lane_reserved=False`(**parity**)。
2. `core_cut = l2_n − quota`;`core = ranked.head(core_cut)`。
3. `below = ranked.iloc[core_cut:]`;`eligible = below[below.recall_channels 含任一 lane_channels]`。
4. hybrid:`n_score = quota//2` 取 `eligible` 按 l2 分 top;余 `quota−n_score` 在剩下 eligible 按 `pct_60d` top → `reserve`。
5. `reserve` 不足 quota → 从 `below` 非 reserve 的按 l2 分回填,凑够 `l2_n` 总数。
6. `result = concat(core, reserve, filler).head(l2_n)`;`reserve` 的 code 标 `l2_lane_reserved=True`。稳定排序、确定性。

### 4.2 接线(两条 L2 路径共用 helper)

- `universe.run`(staging,写 `L2_gbdt_top200.csv`,分列 `gbdt_score`):打分排序后改调 `apply_l2_lane_quota`。
- `stages/l2_rank.py::L2Rank`(typed trace,分列 `l2_score`):同。
- 两处都把 `l2_lane_reserved` 纳入输出列;manifest/meta 记 `l2_lane_quota`。

### 4.3 config + CLI

`ScanConfig` 加:
- `l2_lane_quota: int = 0`(**默认 0 = 关闭 = parity**;建议值 30)。
- `l2_lane_channels: tuple[str,...] = ("momentum","heat","growth","accumulation")`(prod 含 growth)。

CLI(`cli.py`)加 `--l2-lane-quota`(默认 0)、`--l2-lane-channels`(逗号分隔,默认上表)。

### 4.4 下游可见性 + L3 wiring(软)

- `l2_lane_reserved` 列随 L2 表流到 L3 `l3_table_md` 输入 + assemble 留痕(让 Claude 看见"这只是配额救回的动量/题材票")。
- `screening-playbook.md` 补一句:`l2_lane_reserved=True` / 命中 momentum/heat/growth/accumulation 的票,judge 应倾向打 `lane="trend"`,使 `trend_quota` 在 200→30 能接住(否则纯 net 排序仍会砍掉)。这是软指引,非硬代码。

## 5. parity 与安全

- `l2_lane_quota=0`(默认)→ helper 逐值复现 `head(l2_n)` → `scan check` 的 L2 集合/名次 golden parity **不破**。
- 新增 `l2_lane_reserved` 列(Q=0 时全 False):`parity.py` 只比 L2 集合 + 名次,不比列 → 安全。
- 全程确定性、稳定排序、无网络。

## 6. 文件结构

| 文件 | 改动 |
|---|---|
| `autoresearch/scan/recall/l2_quota.py` | **新增** `apply_l2_lane_quota` |
| `autoresearch/scan/universe.py` | L2 块调 helper(staging) |
| `autoresearch/scan/stages/l2_rank.py` | L2Rank 调 helper(trace) |
| `autoresearch/scan/config.py` | 加 `l2_lane_quota` / `l2_lane_channels` |
| `autoresearch/scan/cli.py` | 加 `--l2-lane-quota` / `--l2-lane-channels` |
| `.claude/skills/scan-market/screening-playbook.md` | L3 lane 软指引一句 |
| `tests/scan/test_l2_quota.py` | **新增** helper 单测(合成帧,无网络) |

## 7. 风险与诚实局限

- **尾部驱动、不稳**:日胜率 49%,均值优势靠少数肥尾日;非每日 edge。
- **依赖下游**:配额只把哑弹+尾部一起送进 L3;真正赚钱靠 Claude rubric 三门筛——本设计**不**保证这步。
- **growth 未在回测中验证**(面板无基本面),prod 含 growth → 期望比回测更肥的尾,但仍是假设。
- **fwd5 裸收益非风险调整**;肥尾票高波动,双向。
- **成本**:多 ≤Q 只进 L3 holistic(+少量 token,可控)。
- 故 **默认关闭**;建议下次实扫 `--l2-lane-quota 30` 试跑、观察 reserved 票的 L4 定级与实际表现,再议是否翻默认 + 重做 golden。

## 8. 自检

- [ ] Q=0 严格复现 head(l2_n)(parity 单测)。
- [ ] Q>0 输出恰 l2_n 行;reserve 来自 lane_channels∩below;hybrid 半分半动量;不足回填。
- [ ] `l2_lane_reserved` 标记正确;两条 L2 路径口径一致。
- [ ] eligible 为空 / below 不足 等边界不抛、退化合理。
- [ ] 全测试套件 + ruff 绿;`scan check`(若有 golden)parity 不破。
