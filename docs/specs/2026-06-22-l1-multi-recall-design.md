# Phase 2 — L1 多路策略召回设计（recsys 式 channels + quota union merge）

> 续 `docs/specs/2026-06-22-autoresearch-arch-redesign-design.md`（Phase 1 骨架）的 **Phase 2**。
> 把 L1 从「单一全表复合分排序」改成「**多路策略召回 → merge**」，对齐推荐系统的 recall/match 段。

## 目标 / 非目标

**目标**
- L1 召回从 **单条 `composite_score` 排序** → **N 路 channel 并行召回 + quota union 合并**。每路一种策略（趋势/反转/成长/价值/主力/北向/吸筹 + 校准复合分），各取 top-Kᶜ，**并集去重**到 `recall_n`。
- **channel 可插拔**（镜像 `models/` 的 registry）：加一路 = 写函数 + `@channel` 注册，不动 stage/merge。
- **零新因子数学**：8 路全部复用 `common/scoring.py` 现成的 lens / 因子组 / 列；只是换「怎么取候选」，不新增打分逻辑。
- **现场全留**：每路召回名单进 trace（`L1_channels`），合并集带 **provenance**（`recall_channels` / `n_channels` / 各路 rank）→ retro 可学「哪一路召回出了赢家」。
- **行为可回退**：`recall_mode=composite` 逐值复现今天的单复合分召回 → golden 对拍仍绿；`recall_mode=multi` 为新默认。

**非目标（本 spec 不做）**
- 不改 L2（champion 重排）、L3+ 逻辑。L2 仍 **自由重排**（见下「已知后果」）。
- 不引入 RRF / 学习式融合（用户定：**pure quota union**）。`n_channels` 仅作 trim 的 tiebreak + provenance，非加权融合。
- 不做情感 channel（属 Phase 3 的后续；本 spec 8 路均为量价/资金/基本面/筹码）。
- 不做 channel 权重的在线学习（trace 已留信号，retro 扩展属后续）。

## 决策摘要（brainstorming 定）

| 项 | 决策 |
|---|---|
| 合并方式 | **pure quota union**（各路保底配额 → 并集去重；非 RRF、非 score-blend） |
| L2 多样性 | **L2 自由重排**（不加 per-channel floor；多样性靠晋升的非线性 champion + provenance 流到 L3） |
| channel 集 | **full 8**：composite + momentum + reversal + growth + value + main_fund + northbound + accumulation |
| 默认 mode | `multi`（`composite` 保留作 A/B + 对拍） |

### ⚠️ 已知后果（诚实记录）
`pure quota union` 召回 + `L2 自由重排` + **过渡期线性 champion（=composite）** 三者叠加 → L2 把 1000 按 composite 重排回 top200，**额外的多样性在 L2 被重新坍塌**，要等 **训练出赢过线性的 champion（GBDT/zoo）晋升后** 才完整流到 L3。
本 spec 仍交付的即时价值：①更优的 **多样化 1000 候选池**（严格超集改进）；②完整 **provenance**（L3 holistic 可读「几路共振」）；③**per-channel 学习信号**（trace 留每路名单，retro 可算每路前瞻命中率）。
若日后想让多样性在线性 champion 下也到 L3，加 **L2 per-channel floor** 即可（本 spec 留接口、不实现）。

## 架构

### 新子包 `autoresearch/scan/recall/`（镜像 `models/`）
```
recall/
  __init__.py    # 导出 build · registered_channels · CHANNEL_DEFAULTS · quota_union
  base.py        # ChannelResult 类型 + 工具(_gate_rank)
  registry.py    # @channel(name, quota, floor, desc) 注册 + 默认元数据；build(name)；registered_channels()
  channels.py    # 8 路内置 channel(全复用 scoring.py)
  merge.py       # quota_union(channel_frames, defaults, recall_n) -> (merged_df, per_channel_long)
```

### Channel 契约（函数式 + registry，比 Model ABC 更轻、同样可插拔）
```python
# 每路 channel 是一个被 @channel 注册的纯函数:吃物化好的全市场帧,吐自己的 ranked 候选。
@channel(name="momentum", quota=250, floor=50, desc="趋势龙头(lens_momentum, 过门)")
def momentum(frame: pd.DataFrame, date: str, k: int) -> pd.DataFrame:
    """-> DataFrame[code, channel_rank(1..k), channel_score];已 gate、已排序、已截 top-k。"""
    ...
```
- `frame`：L1 物化好的全市场富因子帧（L0 universe + `_harvest_vol_series` 多日量价 + 全因子列；**与今天 L1 同一帧**，零重取）。
- `k`：该路配额（来自 `CHANNEL_DEFAULTS[name].quota`，config 可覆盖）。
- 返回：`code / channel_rank / channel_score` 三列，已 gate + 排序 + 截断。`base._gate_rank(frame, mask, score_col, k)` 是共用 helper（过 mask → 按 score 降序 → 取 top-k → 编 rank）。
- 未来「学习式 channel」可注册一个 callable 类实例，签名相同 → 自动进流水线。

### 8 路 channel（全复用 `scoring.py`，零新因子数学）

| name | quota | floor | gate | 排序信号 | 复用 |
|---|---|---|---|---|---|
| `composite` | 500 | 100 | 无 | `composite`（IC 校准复合分，=今天） | `composite_score` |
| `momentum` | 250 | 50 | `momentum_gate` | `momentum_score` | `lens_momentum` |
| `reversal` | 200 | 50 | `reversal_gate` | `reversal_score` | `lens_reversal` |
| `growth` | 150 | 40 | `growth_gate` | `growth_score` | `lens_growth` |
| `value` | 200 | 50 | `value_gate` | `value_score` | `lens_value` |
| `main_fund` | 200 | 50 | `main_inflow_yi>0` | `main_net_ratio`（缺则 `main_inflow_yi`） | 现成列 |
| `northbound` | 120 | 30 | `hk_ratio>0` | `hk_ratio` | 现成列 |
| `accumulation` | 120 | 30 | froth-mirror（`vol_ratio≥1.5 & 低位 & 主力未撤`） | `vol_ratio` | `composite_score` 吸筹判据复用 |

- `composite` 配额最大（校准基线、对拍锚点）；`accumulation` 是**刻意高召回低精度**的投机路，交 L2/L3/L4 证伪（与 `composite_score` 里 +5 吸筹加成同判据，不重写）。
- 缺列/缺权限的 channel：gate/排序列缺失 → 该路返回空帧（不破合并；与现有「降级置 NaN」一致）。
- `CHANNEL_DEFAULTS`：上表的 `quota/floor/desc` 存在 `registry.py`；`scan/config.py` 可整体覆盖（`channel_quotas: dict[str,int]`、`channel_floors: dict[str,int]`、`recall_channels: list[str]` 启用子集）。

### merge：quota union（`merge.py`）
```python
def quota_union(channel_frames: dict[str, pd.DataFrame], defaults, recall_n: int
               ) -> tuple[pd.DataFrame, pd.DataFrame]:
```
算法（确定性、可复现）：
1. 各路已是 top-Kᶜ。**并集去重**成候选集；对每个 code 算 provenance：
   - `recall_channels`：命中它的路集合（排序字符串，如 `"composite|momentum|north"`）。
   - `n_channels`：命中路数（=共识计数，**union 的自然副产物，非 RRF 加权**）。
   - `best_rank`：各路 `channel_rank` 最小值。
   - `composite`：始终带（来自 composite 打分，供 tiebreak/回退）。
2. **保底 floor**：每路 top-`floorᶜ` 标 protected（无条件保留）→ 多样性保证（8 路保底合计 ≈ 380，远 < `recall_n`）。
3. **裁到 `recall_n`**：
   - 全部 protected 先入；
   - 剩余席位按 **(`n_channels` desc, `composite` desc)** 填（共识 + 高复合分优先；这是 trim 的 tiebreak，不是加权融合）；
   - 若并集 < `recall_n` → 从 `composite` 路的后续名次 backfill 至满。
4. 返回 `(merged_df, per_channel_long)`：
   - `merged_df`：`recall_n` 行，列 = 今天 L1_recall 全列 + provenance（`recall_channels` / `n_channels` / `best_rank`），默认按 `(n_channels desc, composite desc)` 排序展示。
   - `per_channel_long`：长表 `[channel, code, channel_rank, channel_score]`（进 trace `L1_channels`）。

> **L1_recall 仍留全因子列**（provenance 列追加在后）：L2 champion 要在同帧 re-predict 复合分才能逐值复现回退路径（Phase 1 既有约束，不变）。

### L1Recall stage 改动（`scan/stages/l1_recall.py`）
```python
def run(self, ctx):
    uni = ctx.trace.get_df(ctx.run_id, schema.L0_UNIVERSE)          # 不变
    vps = smu._harvest_vol_series(...); uni = uni.merge(vps, ...)    # 不变(同一物化帧)
    weights = _load_weights()
    scored = composite_score(uni, weights)                           # 全表打分一次(各 channel 共用其列)

    if ctx.config.recall_mode == "composite":                        # A/B + 对拍:逐值复现今天
        recall = scored.sort_values("composite", ascending=False).head(recall_n)
        per_channel = None
    else:                                                            # multi(新默认)
        names = ctx.config.recall_channels or registered_channels()
        frames = {n: build(n)(scored, ctx.analysis_date, quota_of(n)) for n in names}
        recall, per_channel = quota_union(frames, CHANNEL_DEFAULTS, recall_n)

    # 写 trace:L1_recall(带 provenance)+ L1_scored_full(不变)+ L1_channels(per_channel)
```
- 段间仍只经 trace 通信；manifest 记 `recall_mode / recall_channels / n_per_channel`。
- `scored` 全表算一次，8 路共享其列（momentum_score 等由 lens 在各 channel 内按需算；或预算进 scored —— 实现时择一，保证零重复网络取数）。

### trace schema 追加（`trace/schema.py`）
- `L1_CHANNELS = "L1_channels"`：长表 parquet（channel/code/channel_rank/channel_score）。
- `L1_RECALL` 产物列追加 `recall_channels / n_channels / best_rank`（typed schema 放宽这三列）。
- manifest 追加 `recall_mode / recall_channels / channel_counts`。

### config 追加（`scan/config.py`）
- `recall_mode: str = "multi"`（`multi` | `composite`）。
- `recall_channels: list[str] | None = None`（None = 全 8 路）。
- `channel_quotas: dict[str,int] | None`、`channel_floors: dict[str,int] | None`（覆盖默认）。
- CLI：`--recall-mode`、`--recall-channels a,b,c`（默认全开）。

## 测试 / 对拍

**golden 对拍**（`scan/parity.py` 既有）
- `recall_mode=composite`：新管道与重构前 **召回集/名次逐值一致**（diff=0）→ 证明改造未破旧路径。

**新行为单测**（`tests/scan/test_recall.py` + `tests/scan/test_merge.py`）
- 每路 channel：返回列契约（code/channel_rank/channel_score）、gate 生效、已截 top-k、缺列→空帧不抛。
- `quota_union`：并集去重正确；每路 top-floor 全在结果（多样性保证）；输出恰 `recall_n` 行；provenance（`recall_channels`/`n_channels`）正确；并集不足时 composite backfill；确定性（同输入同输出）。
- registry：`@channel` 注册副作用、`build(name)`、`registered_channels()` 全 8、未知名抛 KeyError。
- L1Recall stage：`multi` 端到端跑通、写 3 个 trace 产物、manifest 记 mode；`composite` 走旧路径。

**合成夹具**：`tests/scan/_synth_universe.py` 造小型全市场帧（含各 channel 所需列），免网络。

## 文件清单
- 新建：`recall/__init__.py · base.py · registry.py · channels.py · merge.py`。
- 改：`scan/stages/l1_recall.py`（分支 multi/composite）、`scan/config.py`（4 字段 + CLI）、`trace/schema.py`（L1_CHANNELS + provenance 列 + manifest）。
- 测试：`tests/scan/test_recall.py · test_merge.py · _synth_universe.py`；扩 `tests/scan/test_parity.py`（composite mode diff=0）。
- 文档：`.claude/skills/scan-market/SKILL.md` + `screening-playbook.md` 的 L1 段（多路召回 + provenance 给 L3）。

## 成功标准（Phase 2 Done）
- `python -m autoresearch.scan run <date>`（默认 `multi`）端到端跑通，L1_recall 带 provenance、L1_channels 入 trace。
- `recall_mode=composite` golden 对拍 diff=0（旧路径未破）。
- 8 路 channel 全注册、`quota_union` 保证每路 top-floor 入选、输出恰 `recall_n`。
- 全 pytest 绿；ruff 净。
- skill 文档/CLI 更新（多路召回 + provenance）。

## 风险 / 开放项
- **多样性在 L2 坍塌（过渡期）**：见「已知后果」。本 spec 接受（用户定 L2 自由重排）；留 L2 floor 接口。
- channel 配额调参：默认配额是先验；trace 的 per-channel 命中率（retro）将驱动后续调参/加权（后续 phase）。
- `accumulation` 路低精度：刻意高召回，交下游证伪；不放大其配额。
- 缺权限端点（北向/资金结构）→ 对应 channel 退化空帧，merge 不破，但该策略当日缺位（manifest 记 `channel_counts` 可见）。
