# 漏斗形状修正设计稿：低位转强接生产者 · L3 防追高 · 共振降权 + 行业帽 · 0 买口径卫生

> 立案日 2026-08-22。证据全部来自 2026-08-21 扫描（run `20260821_2255`，`context_claude/scan/2026-08-21/`），零 LLM、可复现（§1 附命令）。
> 本稿是 2026-08-21 低位转强稿（`2026-08-21-lowturn-recall-l3-picture-display-design.md`）的**首跑收尾 + 纠偏**：那一波把 L3 侧（旗列 / pass1 强留 / 守卫⑥ / G 条）全部接好，但 R5 裁定「并入反转桶，先看 10 日」的前提在首跑当天就被证伪（§1.1）。
> 用户 2026-08-22 四项裁决见 §11；裁决权在用户，本稿只记录与实施。

## 0. 一句话

四批独立小改动，修的是**同一个病**——漏斗在 L3 把 L2 偏低位的菜单整个翻到高位（2026-08-21 记忆「为何都在高位 / 没低位向上挖掘」）：
**A** 给「低位转强」画像接上 L1 路 + L2 桶（它昨天上线时根本没有生产者）；**B** 让 L3 看见当日涨幅并用确定性守卫剔当日 ≥9.5% 的票；**C** 把「多路共振」从加分项降为描述项（rubric + pass1 两处）并加 L3 行业帽 3 席；**D** 让 L4 预算的「0 买连败」读 E6 决策（E6 已 active 三日，旗一直在说假话）。

## 1. 立案证据（2026-08-21，run `20260821_2255`）

### 1.1 低位转强旗：有消费者，没有生产者

| 段 | `turnup.lowturn_mask`（生产 cfg）亮旗 |
|---|---:|
| 全帧 `L1_scored_full.csv`（4284） | **120** |
| L1 `L1_recall_top1000.csv` | 17 |
| L2 `L2_gbdt_top200.csv` | **0** |
| `_l3_table.md` 图例 | 「今日旗亮 0 只」 |

- 原因：lowturn 票 composite 中位 **40.1** vs 全帧 50.5（分位 13.5%），merit 核不收；7 个风格桶按召回 provenance 分桶，没有一路召回它们。
- R5 的前提「`reversal_confirm` 重开后会把这类票送进反转桶」不成立：`reversal_confirm` 门（60 日跌 ≥25% ∧ `vol_ratio_20`≥1.5 ∧ …）全帧只 27 只过、通道召 6 只，与 lowturn 120 只**交集 2 只**——两个谓词是两群票。今日反转桶 floor 救回 5 只：pct_5d 中位 **−6.8%**、站上 MA20 的 **0%**，是还在跌的票，不是转强的票。
- R5 的观察机制「先看 10 日（l3_audit/lane 账）」已于同日随 learning 层退役删除。**前提没了，观察腿也没了**，继续等 = 永远 0。
- 这 120 只的形状：行业前八 半导体 11 / 通用设备 8 / 通信设备 8 / 消费电子 7 / 专用设备 6 / 光学光电子 6 / 电网设备 5 / 化学制品 4；市值中位 76.8 亿、成交额中位 4.12 亿、rsi6 中位 63.8、pct_1d 中位 +2.2%（其中 17 只当日 ≥9.5%，批 B 守卫会在 L3 兜住）。与当日 L2/L3 的资源股集中形成互补。

### 1.2 L3 看不见当日涨幅，却要替 L4 避开「涨停追高」

`prompt._L3_COLS` 没有 `pct_1d / pct_5d / dist_high_60`。9 只 finalist 在 L2 内 pct_60d 分位 **81~99**，7/9 距 60 日高 ≤5%：

| 代码 | pct_1d | pct_5d | dist_high_60 | rsi6 | L4 结局 |
|---|---:|---:|---:|---:|---|
| 002716 湖南白银 | **+10.0** | +17.1 | 0.0 | 75 | 早停·涨停追高 |
| 603209 兴通股份 | +2.5 | +7.4 | −0.9 | 79 | 早停·涨停追高 |

2/9 席位（22% 的 L4 Opus 预算）花在 L4 按规则必否的票上。「追当日大涨」在主尺 `gap_c1_o2` 上整区间为负（`docs/research/2026-08-08-overnight-evidence-gap.md` ①：最高桶 −0.96% [−1.06, −0.84]，定义 = 当日 ≥9.5%）。

### 1.3 「channel 共振」在数学上 = 「选已经涨的」

L2 200 按 `n_channels` 分组（2026-08-21）：

| n_channels | n | pct_60d 中位 | rsi6 中位 | dist_high_60 中位 | pct_5d 中位 |
|---:|---:|---:|---:|---:|---:|
| 1 | 129 | −20.2 | 31 | −25.8 | −4.4 |
| 2 | 63 | −11.8 | 32 | −16.4 | −2.7 |
| 3 | 6 | +8.5 | 76 | −1.8 | +11.7 |
| 4 | 1 | +8.0 | 75 | 0.0 | +17.1 |
| 5 | 3 | +14.7 | 72 | −5.3 | +12.0 |

spearman(n_channels, pct_60d) = 0.29，spearman(n_channels, dist_high_60) = **0.34**。机制：momentum / heat / healthy / main_fund / growth 五路在强势上涨时同时亮，低位票至多命中 value / reversal / composite 三路。这个偏置在**两处**生效：`triage.py` 规则②「n_channels≥3 全入」（今日 10/40 席）+ `l3-rank.md` rubric ①「多路共振加分」。今日 finalist 里 5 只出自那 10 只。与记忆「菜单内任何确定性分数都无信号」一致——`n_channels` 是确定性分数。

### 1.4 同一个 beta 占 5/9 席

贵金属（12 只成分）拿 4 席（600489/000426/002237 lane=healthy，002716 lane=main）+ 饰品 600916（金价下游）+ 小金属 600549 + 石油 601857 = 7/9 资源股。L2 有 `sector_cap` 20%，L3/merge **没有任何行业帽**。E6 在这个池里挑「相对最优」= 金价 beta 里最不差的（000426）。L3 自己在每只红队段都写了「同一动量被多路重复计数」，但没有机制阻止。

### 1.5 L4 预算压缩读的是 E6 之前的「0 买」

`menu.zero_buy_streak` 数 `final_ratings` 的 ≥OW 张数；E6 自 08-19 `mode=active`、每成功日必出 1 BUY，连败仍计到 10 日 → 汇总屏打「⚠️ 0买连败10日·重旗+连败≥7硬压→10」。**实际效果为零**：`scan-market.js:262 l3cap = min(10, l4_budget)`、`finalist_max=10`，预算 10 和 30 都落到 cap 10。病只在「旗说假话」。`health.count_buys_with_source` 已改读决策文件（E3b），menu 没跟。

### 1.6 复现命令（全部只读）

```bash
SD=context_claude/scan/2026-08-21
uv run --no-sync python - <<'PY'
import pandas as pd
from autoresearch.common import turnup
from autoresearch.scan.user_config import load_user_config
cfg=load_user_config()['l3']['lowturn']; SD='context_claude/scan/2026-08-21'
for f in ('L1_scored_full','L1_recall_top1000','L2_gbdt_top200'):
    d=pd.read_csv(f'{SD}/{f}.csv',low_memory=False); print(f, int(turnup.lowturn_mask(d,cfg).sum()))
l2=pd.read_csv(f'{SD}/L2_gbdt_top200.csv',low_memory=False); l2['nch']=l2['recall_channels'].astype(str).str.count(r'\|')+1
print(l2.groupby('nch')[['pct_60d','rsi6','dist_high_60','pct_5d']].median().round(1)); print(l2['nch'].corr(l2['dist_high_60'],method='spearman'))
PY
```

## 2. 继承约束（不重开）

- 2026-08-21 用户裁定 learning 层整体退役：本稿**不**设账本、不设影子、不设裁决提案；验收 = 测试锁 + 下一次真跑的活体清单（§8.3）。
- `scan_config.jsonc` = 唯一参数事实源；新参数三件套（白名单 + 消费点 + 测试）。**本稿不加任何新白名单键**：通道开关走既有 `funnel.recall_channels` / `channel_quotas`；L2 桶 floor、守卫阈值、pass1 cap 是行为归属，代码持有（同 75/55/`DEFAULT_FLOORS`）。
- 主尺 `gap_c1_o2` 不动；持仓超短 1~2 日；「不要跌势票」是产品偏好——lowturn 旗亮票按 2026-08-21 裁定不算跌势票（B 条例外已在）。
- lowturn 画像证据边界不变：决策尺 −0.24pp（t=−6.36，132 日）、与现任 healthy 同负、周级尺翻正。**批 A 是兑现 P3 裁定的前提（给它一个能被 L4 判的机会），不是 alpha 主张。**
- 📌 保送票在所有 L3 守卫**之后**注入（`_inject_pinned_finalists`），本稿守卫⑦/⑧对持仓零影响：持仓涨停也出卡、持仓同行业也不占帽。
- 「性能开关不拥有评级」：本稿没有性能开关。
- 前提条件：工作区里 learning 退役的 221 文件变更（131 个 staged 删除）**尚未提交**（HEAD `ec0935e`）。本稿实施前须先把它落成 commit（用户的事），否则四批 commit 会和退役混在一起、回滚杆失效。

## 3. 批 A · 低位转强接生产者

### 3.1 L1 新路 `lowturn`（`autoresearch/scan/recall/channels.py`）

```python
@channel("lowturn", quota=120, floor=40,
         desc="低位转强(turnup.lowturn_mask 过门=与 L3 旗同一谓词同一 cfg;按 reversal_confirm_score 排)")
def lowturn(frame, date, k):
    from autoresearch.common.turnup import LOWTURN_DEFAULTS, lowturn_mask
    from autoresearch.scan.user_config import load_user_config
    cfg = {**LOWTURN_DEFAULTS, **((load_user_config().get("l3") or {}).get("lowturn") or {})}
    mask = lowturn_mask(frame, cfg)
    if not len(mask):
        return gate_rank(frame, None, "reversal_confirm_score", k)     # 空帧降级(与他路一致)
    g = lens_reversal_confirm(frame)
    return gate_rank(g, mask.reindex(g.index).fillna(False).astype(bool), "reversal_confirm_score", k)
```

- **门** = `turnup.lowturn_mask`，阈值读 `l3.lowturn` 块（单一事实源；两把开关分工：`recall_channels` 管召不召回，`l3.lowturn.enabled` 管 L3 旗列/pass1 强留；阈值共用）。缺列 → 谓词逐行 False → 空帧（与他路一致，不抛）。
- **排序** = `lens_reversal_confirm` 的 `reversal_confirm_score`（lowpos 30 / stabilize 30 / confirm 40）：「同模块两档」——通道门严（rc）、画像门宽（lowturn），分数公用，零新因子数学（用户 08-22 选 rc_score，备选 `vol_ratio_20` 不采）。2026-08-21 实测 120/120 行分数非 NaN。
- **quota 120 / floor 40**：`quota_union` 只对 `channel_rank < floor` 的行保底，其余按 (n_channels, composite) 竞争 1000 席——lowturn 票 composite 低、多为单路，floor 之外基本出局，所以 L1 实际到货 ≈ 40+。40/1000 = 4% 的位移，从 composite 尾部挤出。
- 配置：`funnel.recall_channels` 追加 `"lowturn"`（9→10 路）；`funnel.channel_quotas` 追加 `"lowturn": 120`（与注册默认相同，写明是为了「六键全写」同款防回落）。

### 3.2 L2 新桶「低位转强」（`recall/l2_stratify.py`）

- `STYLE_CHANNELS["低位转强"] = ("lowturn",)`；`DEFAULT_FLOORS["低位转强"] = 8`。Σfloor 93→101，merit 核 107→99。
- 翻转昨稿 R5（并入反转桶）。理由见 §1.1：反转桶装的是另一群票，共用 floor 12 永远轮不到 lowturn。
- 桶内排序沿用 sector-neutral composite（同其他桶）：取 40 只里 composite 最好的 8 只。

### 3.3 未启用通道的桶 floor 运行时归零（`l2_stratify.py` + `universe.run`）

```python
def effective_floors(floors: dict[str, int], enabled_channels) -> dict[str, int]:
    """桶的全部通道都不在 enabled_channels → floor 归 0(未启用通道不得改生产 L2 分布)。
    enabled_channels=None → 原样返回(parity:单测/离线不传)。"""
```

- `select_l2(..., enabled_channels=None)` 新增可选形参，内部 `floors = effective_floors(floors, enabled_channels)`；`universe.run` 传入解析后的 `recall_channels`（`_funnel_overlay` 之后那个；为 None 时传 `registered_channels()`，等价「全启用」= 现行为）。
- 效果：**批 A 的回滚杆只剩一根**——从 `recall_channels` 摘掉 `lowturn`，桶 floor 自动 0、pass1 强留无候选、守卫⑥无候选，逐字 parity。今天「event 桶 floor 必须手工写 0」那条约定升格为代码不变量；`DEFAULT_FLOORS["事件"]` 仍写 0、静态测试 `test_disabled_channel_buckets_have_zero_floor` 原样保留（双保险）。

### 3.4 三段计数 + 探针

- `universe.run`：在 `scored` / `recall` / `l2` 三帧上各算一次 `lowturn_mask`（cfg 同 3.1；`"lowturn" in recall_channels` 或 `l3.lowturn.enabled` 任一真才算，否则跳过 = parity），写 `meta.json` 三键 `lowturn_full / lowturn_l1 / lowturn_l2` + 返回 dict 同名三键。4284 行 `apply` ≈ 0.5s×3。
- `prelude._universe()` 行改为 `L0 … → L2 202(…) · lowturn 全帧 120 → L1 17 → L2 0`（三键缺 = 不印，parity）。
- `self_review.product_shape_lint` 新探针 **14**「低位转强·L2 零到货」：`meta.json` 有三键 ∧ `lowturn_l2 == 0` ∧ `lowturn_full > 0` → **warn**（不 fail；探针职责是可见性）。昨天那种「上线即恒 0」第一天就会响。`channel_liveness_lint` 对新路免费生效（L1 0 行也会响）。

### 3.5 测试锁

- `tests/scan/test_lowturn_channel.py`（镜像 `test_healthy_channel.py`）：① 门 = `turnup.lowturn_mask` 逐行相等（单一事实源）；② 排序 = `reversal_confirm_score` 降序；③ 缺核心列 → 空帧；④ 注册 quota/floor = 120/40；⑤ L2 桶映射 `STYLE_CHANNELS["低位转强"] == ("lowturn",)` 且 floor 8。
- `tests/scan/test_l2_stratify.py`：`test_effective_floors_zeroes_disabled_buckets`（禁 lowturn → 该桶 0、其他不变；`None` → 原样）；`test_floors_guaranteed_per_style` 自动覆盖新桶。
- `tests/scan/test_channel_quota_override.py`：新键 override 生效。
- `tests/scan/test_universe_lowturn_counts.py`：三段计数抽成纯函数 `universe._lowturn_counts(scored, recall, l2, cfg) -> dict`（不跑 `run`、不取数），单测合成三个小帧断言三键；prelude `_universe()` 行渲染另一条用例（三键缺 → 不印）。
- `tests/scan/test_product_shape_lint.py`：探针 14 的三态（零到货 warn / 有到货静默 / 键缺静默）。
- **变异检查**（每条测试写完先问「把实现删掉会红吗」）：把 `@channel("lowturn")` 注释掉 → ①②④ 红；把 `effective_floors` 改成原样返回 → 归零测试红；把探针 14 的 `== 0` 改 `< 0` → 三态测试红。

### 3.6 回滚 / 成本 / 反事实

- 回滚：`scan_config.jsonc` 摘 `"lowturn"`（一行）。代码留着无害（3.3 保证）。
- 成本：L1/L2 是定额位移不是增量；pass1 仍 40；L4 张数由 cap 10 定 → **≈ 0**。
- 2026-08-21 反事实：L1 +≥40 只（行业分布见 §1.1）、L2 +8、`_l3_table.md` 旗亮 ≤8、pass1 `selection_detail=lowturn` ≤8 行、守卫⑥有候选可用。L4 会怎么判它们——**今天之后才知道**，这正是 P3 要观察的东西。

## 4. 批 B · L3 防追高

### 4.1 表列 + 指纹词（`scan/l3/prompt.py`）

- `_L3_COLS`：在 `"pct_60d"` 后插 `"pct_1d", "dist_high_60"`（两列 L2 表已有；`compact_table` 按 `c in df.columns` 过滤，缺列不炸）。
- `row_profile`（`pf` 列）词表加两项：`今日大涨`（`pct_1d ≥ 9.5`）、`贴顶`（`dist_high_60 ≥ −2 ∧ pct_60d > 0`）。docstring 词表同步。
- 表头图例加一句：「pct_1d 当日涨幅 / dist_high_60 距 60 日高(%≤0)；当日 ≥9.5% 的票守卫⑦会剔除，勿选」。

### 4.2 merge 守卫⑦ `chase_1d`（`scan/l3/merge.py`）

- 常量 `CHASE_1D_PCT = 9.5`（代码持有，同 75/55；引用 2026-08-08 研究稿定义）。
- `write_finalists`：从 L2 回填 `pct_1d` 进 judged（与现有 `pct_60d` 回填同一处，**无条件** merge，不只在缺列时）。
- `merge_l3_finalists_v3`，守卫序改为 **① ins75 → ② lt55 → ⑦ chase_1d（剔 + 补）→ ③ cap → ④ healthy → ⑤ trend → ⑥ lowturn → ⑧ sector_cap（剔 + 补，批 C）**：
  - 剔：候选集里 `pct_1d ≥ 9.5` 的行 → bench，`guard="chase_1d"`（无条件覆写；ins75 行也剔——用户裁定的 ins75 是「高确信误杀保险」，不是「追高豁免」，在 docstring 写明）。
  - 补：从 bench（非 dup、非 chase、`conviction ≥ 55`）按 conviction 降序补到**剔除前的候选数**，`guard="chase_backfill"`；够格不足 → 不硬凑。
  - `pct_1d` 列缺 → 守卫⑦ 整体 no-op（parity）。
- 📌 保送在 `_inject_pinned_finalists` 注入，不经守卫⑦。

### 4.3 agent def（`.claude/agents/l3-rank.md`）

硬约束 **H**：「当日涨幅 `pct_1d ≥ 9.5%`（pf 词『今日大涨』）**不得 finalist**——隔夜主尺上整区间为负（研究稿 2026-08-08 ①）；守卫⑦会剔除并从 bench 回填，选它 = 浪费一席。`贴顶` 词不是硬约束，是⑤脆弱维的输入。」

### 4.4 测试锁 / 回滚

- `tests/scan/test_l3_merge_v3.py`：`test_chase_1d_drops_and_backfills_from_bench`（剔 1 补 1，guard 两端正确）/ `test_chase_1d_no_backfill_below_55` / `test_chase_1d_noop_without_column` / `test_chase_1d_overrides_ins75`。
- `tests/scan/test_l3_prepare.py` 或新 `test_l3_profile_words.py`：`今日大涨` / `贴顶` 两词的出现与不出现（NaN 不出现）。
- 回滚：`CHASE_1D_PCT = float("inf")` 一行 = 守卫静默；列与词保留无害。
- 2026-08-21 反事实：002716 出（guard `chase_1d`），bench 里 conviction 最高的够格票补入（`_l3_bench.csv` 第一行 ≥55 者）。603209 不被⑦拦——它是判断问题，表里新增 `dist_high_60=−0.9`、rsi6 79 让 L3 自己判。

## 5. 批 C · 共振降权 + 行业帽

### 5.1 rubric ①（`.claude/agents/l3-rank.md`）

改为：「① **channel 共振（描述性，不加分）**：2026-08-21 L2 实测 `n_channels` 与 `dist_high_60` ρ=0.34，≥3 路的 10 只中位 rsi6 72–76、pct_5d +12%——多路同时命中在数学上≈『已经涨起来』（momentum/heat/healthy/main_fund 只在强势时同时亮）。只在**同一画像内**比较时作次级 tiebreak，**不得作入选首因**；单路召回不扣分。」

### 5.2 pass1 规则②（`scan/l3/triage.py`）

- 常量 `RESONANCE_CAP = 5`。规则②「`n_channels ≥ 3` 全入」→ 按 `order`（composite）降序取前 `RESONANCE_CAP` 只强留（detail 仍记 `n_channels=k`）；其余共振票走正常 lane round-robin（不是被切，是不再免检）。
- docstring 与 `PASS1_REASONS` 不变（`conviction_guard` 语义收窄为「共振 top-5」，写进 docstring）。
- 今日效果：释放 5/40 席给 lane 轮询。

### 5.3 merge 守卫⑧ `sector_cap`（`scan/l3/merge.py`）

- 常量 `L3_SECTOR_CAP = 3`，按 `sector` 列（= L2 `industry`，申万二级口径，与 L2 `sector_cap` 用的同一列）。
- 位置：⑥ 之后（见 §4.2 守卫序）。
- 剔：某 sector 席数 > 3 → 从该 sector 里挑**可剔**行按 conviction 升序剔到 3。可剔 = `conviction < 75`（ins75 行不可剔）∧ 剔掉后该行 lane 的配额仍满足（healthy ≥ ceil(n/3)、trend ≥ 2、lowturn ≥ 1 —— 保护的是**配额**，不是每一行：今日贵金属 4 席里 3 席 lane=healthy，若整 lane 免剔帽子永远不咬）。`guard="sector_cap"`。
- 补：从 bench（非 dup、非 chase、`conviction ≥ 55`、所在 sector 当前 < 3）按 conviction 降序补到剔除前候选数，`guard="sector_backfill"`；不硬凑。
- `sector` 列缺 → no-op。
- 2026-08-21 反事实：⑦ 已剔 002716 → 贵金属 3 席（600489/000426/002237）→ ⑧ 不再动作；若 ⑦ 未剔，⑧ 剔 002716（62，lane main）。

### 5.4 agent def 硬约束 I

「同 `industry` **≤3 席**（守卫⑧兜底：超出的换 bench 异行业最高 conviction）。跟随强板块可以，但 4/9 给一个 12 只成分的二级行业、再加 1 只它的下游，已不是『跟随』而是『单押』（2026-08-21）。」

### 5.5 测试锁 / 回滚

- `tests/scan/test_l3_pass1.py`：`test_triage_keeps_resonance_rows_n_channels_ge_3` 改为 7 只共振只 5 只记 `conviction_guard`，其余 2 只无该 reason（可仍被 round-robin 留下）。
- `tests/scan/test_l3_merge_v3.py`：`test_sector_cap_drops_lowest_and_backfills_other_sector` / `test_sector_cap_respects_ins75` / `test_sector_cap_keeps_healthy_quota`（剔后 healthy 仍 ≥ ceil(n/3)）/ `test_sector_cap_noop_without_sector_column` / `test_sector_backfill_never_exceeds_cap`。
- 变异检查：`L3_SECTOR_CAP = 99` → 帽测试红；`RESONANCE_CAP = 999` → pass1 测试红；rubric 文案改动无机械锁（指令级，靠 `test_agent_defs.py` 的存在性检查 + 活体）。
- 回滚：`L3_SECTOR_CAP = 99` / `RESONANCE_CAP = 10**6` 各一行；rubric 文案 git revert。

## 6. 批 D · 0 买口径卫生（`scan/menu.py`）

- `zero_buy_streak`：`ratings = final_ratings(d)` 仍用于判「这天出过卡」（`seen` 计数）；「有没有买」改为 `n, _src = health.count_buys_with_source(d)`，`n > 0` 即断链。shadow 期 `count_buys_with_source` 返回 ≥OW 数 = 逐字 parity；active 期读当日 `_relative_buy_decision.json`（`load_decision` 自带日期校验，历史目录各读各的）。
- docstring「源 = details 卡最终评级」改为「源 = `health.count_buys_with_source`（与 brief ③ / run_health 同一口径）」。
- 效果：E6 active 期连败归零 → 预算旗不再打「0买连败」假旗；**finalist 数不变**（cap 10）。`finalist_max` 放宽本波不做（用户 08-22 裁定：等 A–C 落地看两天再议）。
- 测试：`tests/scan/test_menu_health.py`（既有文件，追加用例）：合成三日目录，shadow 期 ≥OW 口径不变；active 期某日决策文件 `buys=[…]` → streak 在该日断链；决策文件日期不符 → 回退 ≥OW（与 `count_buys_with_source` 的回退语义一致）。`monkeypatch relative_buy.is_active`。
- 回滚：一行 revert。

## 7. 文档同步（同 commit 内，不单独成批）

- `STAGES.md`：L1 表加 `lowturn` 行（quota 120/floor 40、门/排序/两把开关）、「已注册 N 路，当前启用 10 路」；L2「8 风格桶 … 低位转强 8」+ 运行时归零一句；L3 守卫序 ①②⑦③④⑤⑥⑧ + pass1 规则② top-5；「行为变更的入口」不动。
- `SKILL.md` 配置表 L1 行：9 路 → 10 路、`channel_quotas` 加 lowturn。
- `scan_config.jsonc`：`recall_channels` / `channel_quotas` 两处注释记本稿与日期。
- 昨稿 `2026-08-21-lowturn-…-design.md` 追加 **§17 首跑纠偏（2026-08-22）**：R5 翻转 + 三段读数 + 指向本稿。
- `README.md` 架构节若写了路数，同步。

## 8. 实施顺序、commit 粒度、验收

### 8.1 顺序与粒度

0. **前提**：用户先把工作区 learning 退役的 221 文件变更提交（或 stash），本稿四批在其之上。
1. 批 A（一个 commit：channel + 桶 + 归零 + 三段计数/探针 + 配置 + 文档 + 测试）。
2. 批 B（一个 commit）。
3. 批 C（一个 commit；含 rubric/硬约束 H/I 的 agent def 改动——**agent def 会话启动装载，下个 session 生效**）。
4. 批 D（一个 commit）。

每批独立可回滚（§3.6/§4.4/§5.5/§6）。全量 `uv run --no-sync pytest -q` 基线 2750 绿（2026-08-21 记忆），每批结束必须全绿。

### 8.2 机械验收（每批）

- 新测试按 §3.5/§4.4/§5.5/§6 落地且做过变异检查（删实现 → 红）。
- `uv run --no-sync python -m autoresearch.scan.universe 2026-08-21`（湖命中，零网络）重跑当日：`meta.json` 三键在场、`L1_channels.csv` 有 `lowturn` 行、`L2_gbdt_top200.csv` 里 `selection_detail=低位转强` ≥1 行。
- `uv run --no-sync python -m autoresearch.scan.agents.l3_select prepare 2026-08-21`：`_l3_table.md` 新列在场、图例「今日旗亮 N 只」N≥1、pf 出现「今日大涨」。
- 对 2026-08-21 的 `_l3_judged.json` 重跑 `l3_select finalists 2026-08-21 --budget 10`：`finalists.csv` 的 `guard` 列出现 `chase_1d` / `chase_backfill`；002716 落 `_l3_bench.csv`。（只在 scratch 副本上跑，不覆盖已发布产物。）

### 8.3 活体验收（下一次真实扫描，逐条核）

1. prelude 汇总屏 universe 行带 `lowturn 全帧 N → L1 n₁ → L2 n₂`，n₂ ≥ 1。
2. `_l3_table.md` 旗亮 ≥1；pass1 kept 里 `lane=lowturn` ≥1。
3. `finalists.csv` 若当日有 ≥9.5% 票，`guard` 出现 `chase_1d`；无 → 不出现（不要为验收硬造）。
4. 任一 sector ≤3 席。
5. `gate_fires.csv` 无新 fail；探针 14 若 warn，说明 A 没接通，按 §1.1 的三段计数查。
6. 汇总屏不再打「0买连败」（E6 active 期且前一日有 BUY）。
7. L4 对 lowturn finalist 的评级——**只记录，不下结论**（样本 1 日）。

## 9. 不做清单

- 不改主尺、不加周级尺决策（Gate 0 的周级翻正是独立设计稿的事）。
- 不动 `finalist_max`、不动 L4 预算阶梯本身。
- 不做板别感知涨停阈（主板 10 / 创业 20 / 北交 30 / ST 5）：守卫⑦沿用研究稿「当日 ≥9.5%」定义；创业板 +9.5% 不是涨停但同样是追高。
- 不改 `reversal_confirm` 门与配额（A/B 继续）。
- 不加任何白名单键。
- 不做 intel 价格断言前置对账、防锚定 lint 误报修正、档案队列策略（brainstorm 列出但用户未选，另案）。

## 10. 风险与开放问题

1. **lowturn 票进 L4 后大概率 Hold/UW**（决策尺为负）——那是 P3 要观察的结果，不是本稿失败；失败的定义是「到不了 L3」。
2. 守卫⑦/⑧ 的回填从 bench 取 `conviction ≥ 55` 者：bench 是 L3 判为「不够 finalist」的票，回填等于确定性层推翻判断层的排序。已有 ④⑤⑥ 先例；回填只到原席位数、不硬凑。
3. `RESONANCE_CAP = 5` 后 pass1 强留集 = pinned ∪ 共振 top5 ∪ healthy ∪ lowturn ≤8；今日 healthy 14 + 5 + 8 + 2 = 29 ≤ 40，仍有 11 席轮询。healthy 路 floor 40 时可能挤到 40 上限——`triage` 已有 mandatory > target 的截尾逻辑（非 pinned 按 order 截），不新增处理。
4. 通道函数内读 `load_user_config()`（3.1）是 L1 首次跨块读 `l3.lowturn`；测试环境无配置文件 → 默认阈值。若将来阈值块搬家，`test_lowturn_channel.py::门=谓词` 会红。
5. `effective_floors` 让「桶是否生效」取决于 `recall_channels` 解析结果；`recall_mode != "multi"` 时 `recall_channels` 语义不同（单路 composite）——`universe.run` 只在 multi 路传 enabled，其他模式传 None = 原样（parity）。

## 11. 裁决记录（用户 2026-08-22）

| # | 问题 | 裁定 |
|---|---|---|
| A | 翻转 R5 → 独立 L1 路 + L2 桶 floor 8 + 未启用桶 floor 运行时归零；通道排序 | **同意；按 `reversal_confirm_score` 排** |
| B | 守卫⑦ 剔 `pct_1d ≥ 9.5` 后是否回填 | **剔除并回填（conviction ≥55，不硬凑）** |
| C | 共振降权力度 | **rubric ① 降描述性 + pass1 规则② 全入→top5 + 行业帽 3** |
| D | #5 处理 | **只做卫生修正（streak 读 E6 决策）；`finalist_max` 等 A–C 落地观察后再议** |

---

## 12. 实施记录与偏离（2026-08-22）

四批已全部实施并合入（`b09ca9c` 批A → `0635f9a` 批B → `c1c0bfb` 批C → `bc6e48d` 批D），全量 **2801 passed / 5 skipped**（基线 2750，新增 51 条）。

### 与设计稿的偏离（四条，全部当场决定并记录）

1. **守卫⑦ 的位置从「② 之后、③ cap 之前」改为「③ cap 之后」**。§4.2 原写在 cap 前，但那里操作的是布尔 `sel`，剔掉的行只是让 `order` 变短——`cap` 截尾并不会从 bench 回填，「剔除并回填」根本无从谈起（第一版实现按稿写完，测试立刻红：剔 1 补 0）。回填需要一个「已经截到 cap 的席位集」才有「席位数」可言，故移到 cap 之后、④ 之前。守卫序实际为 **①②③⑦④⑤⑥⑧**，`STAGES.md` 已按此记。
2. **守卫⑦ 的回填也认行业帽**（`sector_cap=L3_SECTOR_CAP`）。设计稿只给⑧ 设了帽。08-21 真数据实测：⑦ 剔 002716（贵金属）后补入 001337 四川黄金（**也是贵金属**），⑧ 随即再剔，净效果白丢一席。帽是全局不变量，越早认越省事。
3. **回填池的资格判据**：`_drop_and_backfill` 排除的是**失格** guard（`chase_1d` / `lt55` / `dup`），**不排除** `cap`——被 cap 截尾的行正是「conviction 次高、只因名额满才没进」的那批，是最该补位的人。第一版把「guard 非空」一律排除，等于把最佳候选一起挡在门外。
4. **文档同步单独成一个 commit**，而非 §7 说的「同 commit 内」。原因是 `STAGES.md` / `SKILL.md` 被四批共同修改，路径限定 commit 无法在文件内切分。

### 实施中发现的测试缺陷（一条，值得单记）

**变异检查 MC3（去掉守卫⑧ 的 lane 配额保护）第一轮没被逮住** —— 当时的 `test_sector_cap_keeps_healthy_quota` 里，超帽行业中 conviction 最低的那只本来就是非配额 lane，无论保护分支在不在，被剔的都是同一只：**对该分支零鉴别力**。已重写为「最弱者恰是 healthy 且剔了会跌破 `ceil(n/3)`」+ 一条无冲突对照，重测确认变红。这是「改完先问『把这段删掉测试会红吗』」那条配方又一次兑现。

### 活体验收读数（08-21 真数据重跑，产物落 scratch，未覆盖已发布 staging）

| 项 | 改动前 | 改动后 |
|---|---|---|
| lowturn 三段 | 120 → 17 → **0** | 120 → **64** → **8** |
| L1/L2 规模 | 1000 / 202 | 1000 / 202（23 进 23 出的位移） |
| L2『低位转强』桶 | 不存在 | 8 只（pct_60d −12~−34、dist_high_60 −17~−40、pct_5d 全正） |
| finalist 贵金属席数 | 4（+ 下游饰品 1 = 5/9） | **3** |
| 当日 ≥9.5% 入围 | 002716（L4 早停「涨停追高」） | 落 bench，`guard=chase_1d` |
| finalist 总数 | 9 | 8（bench 里 conviction≥55 的三只**全是贵金属**，无合法回填人选 → 按「不硬凑」诚实空着） |

最后一行是本波最该记住的读数：**08-21 那天整个候选池就是黄金**。批 C 只能把「单押」压回「集中」，真正打开形状的是批 A —— 它每日往 L2 送 8 只低位转强票，那批票要到下一次真跑才第一次被 L3/L4 判到。

### 未做（§8.3 活体验收清单原样保留）

7 条活体验收要一次**真实扫描**才能逐条核（prelude 汇总屏三段行、pass1 `lane=lowturn`、`guard=chase_1d` 是否出现、任一 sector ≤3 席、`gate_fires` 无新 fail、汇总屏不再打「0买连败」、L4 对 lowturn finalist 的评级**只记录不下结论**）。批 C 改了 `.claude/agents/l3-rank.md`，agent def 会话启动装载，**下个 session 生效**。

