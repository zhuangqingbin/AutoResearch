# 前向评估闭环 — per-channel 归因 + 跨日 rollup(度量+呈现)设计

> **状态**:已 brainstorm 通过,待写 TDD plan。母文档:`docs/specs/2026-06-22-l1-multi-recall-design.md`(多路召回)、`autoresearch/learning/stage_eval.py`(各段 edge 评估)。

**Goal**:给闭环学习层补上**唯一缺的器官**——recall channel 的前向归因。让"`heat`(及每一路)到底有没有找到别人没找到的赢家"从拍脑袋变成**可累积的量化数字**,渲染进 retro 报告供 scan-retro / 人决策。**measure-only,不自动改 quota**。

**Architecture**:在现有 `stage_eval.evaluate() → render → retro` 流上加一个 **L1/channel 归因块**(纯函数 `channel_edge`,复用 `binary_lift/rank_ic` 同款原语 + `retro.realized_returns` 的全市场 fwd),产物落 `context/scan/<date>/retro/channel_eval.csv`;再加一个独立小模块 `channel_ledger.py` 做**跨日聚合**(单日是噪声,跨日滚动才是信号)。零新取数管道、零 LLM。

**Tech Stack**:Python / pandas / pytest。复用 `autoresearch.learning.stage_eval`(primitives)、`autoresearch.learning.retro.realized_returns`(全市场 fwd_1_oo/fwd_5_oc + buyable + gap_d1)。

---

## 背景:缺口在哪、为什么这么设计

- **per-rating 已存在**:`stage_eval.evaluate()` 已有 L2(keep-lift + gbdt IC)、L3(finalist-lift + 确信−脆弱 IC)、**L4 per-rating**(`by_rating` 均值 + 评级单调性 rank-IC)、Tier-3 verdict edge,写 `retro/stage_eval.csv`。
- **缺 per-channel**:`evaluate()` 无 L1 段。`L1_recall_top1000.csv` 的 `recall_channels`/`n_channels` provenance 被取了却从不对齐 fwd 收益。**这是闭环唯一缺的器官**。
- **为什么头条用 excess-vs-market(截面超额)**:当前 falling-knife regime 下绝对收益近乎全负(L2 champion OOS rank-IC 负、候选普遍超卖)。绝对收益会把"全市场都跌、这路跌得少"误判成"这路差"。**截面超额 = 个股 fwd − 当日全市场中位**,regime-中性,才能说清一路有没有真 edge。
- **为什么 unique/边际**:每只票常被多路同时召回(composite 几乎覆盖所有)。membership 均值被 composite 稀释、看不出某路的独立价值。**unique(`recall_channels` 仅此一路)的超额 = 边际 alpha** = "没这路就进不来的票,到底赚不赚" = "该不该留这路"的最诚实判据。`heat` 在 2026-06-22 有 29 只 unique-recall 票,正是这指标要回答的。
- **为什么 buyable-aware**:`realized_returns` 带 `buyable`(D+1 开盘能否买入)。一路若净召回 D+1 涨停/停牌锁死的票,"找到了却买不进"=没给可执行信号,均值须剔除这些、单列 `n_unbuyable`。
- **为什么跨日 rollup**:单日 per-channel 超额在负-IC regime 下纯噪声。只有跨 N 日滚动均值 + hit_rate 才下结论。这是 `channel_ledger` 的存在理由。

---

## 组件与接口

### 1. `channel_edge`(纯函数,加在 `stage_eval.py`)

```python
def channel_edge(recall: pd.DataFrame, realized: pd.DataFrame) -> pd.DataFrame:
    """L1 多路召回 provenance × 已实现 fwd → 每路一行的前向归因表(纯函数,零网络)。

    recall:    L1_recall_top1000(需列 code, recall_channels, n_channels);code 已 6 位。
    realized:  retro.realized_returns(date) 全市场(code, fwd_1_oo, fwd_5_oc, buyable);code 已 6 位。
    返回 DataFrame,每路一行,按 unique_excess_t5 降序:
      channel, n_recalled, n_unique, n_unbuyable,
      mean_excess_t5, unique_excess_t5, mean_excess_t1, hit_rate_t5
    """
```

**度量公式**(`mkt5 = realized["fwd_5_oc"].median()`、`mkt1 = realized["fwd_1_oo"].median()`,全市场截面中位):
- 先 `m = recall.merge(realized, on="code", how="left")`;`excess_t5 = fwd_5_oc − mkt5`、`excess_t1 = fwd_1_oo − mkt1`。
- 每路 `c`:
  - `members` = `m` 中 `c in recall_channels`(`recall_channels` 是 `|` 分隔串,按集合判含)。
  - `unique` = `m` 中 `recall_channels == c`(只此一路)。
  - **均值统计只在 buyable 行**上算(`buyable` 缺列时视作全 True);`n_unbuyable` = members 中 `~buyable` 计数。
  - `mean_excess_t5` = members(buyable)的 `excess_t5` 均值;`unique_excess_t5` = unique(buyable)的 `excess_t5` 均值;`mean_excess_t1` 同理。
  - `hit_rate_t5` = members(buyable)中 `excess_t5 > 0` 的比例。
  - 任一组为空 → 对应值 `None`(不编 0)。
- 渠道集合 = `recall_channels` 里出现过的全部名(含 `(backfill)` 也照单计,便于看 backfill 质量)。

### 2. `evaluate()` 加 "L1" 段(`stage_eval.py`)

在现有各段后插入:读 `L1_recall_top1000.csv`,若有 `recall_channels` 列 → 调 `channel_edge` → 落 `outdir/channel_eval.csv`;`res["stages"]["L1"]` 存 `{"by_channel": ce.to_dict("records"), "ic_n_channels_t5": rank_ic(m, "n_channels", _RET_T5)}`(`n_channels` 共振是否预测 fwd,验证 merge 的 `n_channels desc` tiebreak)。`_flat_csv` 已跳过 dict 值 → `by_channel` 不污染 stage_eval.csv,只 `ic_n_channels_t5` 标量入表;明细在 channel_eval.csv。

### 3. `render_stage_eval` 加 L1 段(`stage_eval.py`)

在 L2 段前加 L1 段:列出每路 `unique_excess_t5`(边际)+ `mean_excess_t5` + `n_unique` + `hit_rate_t5`,按边际超额降序,标 `n_channels` 共振 IC。一句话注解:_"unique 超额 >0 = 这路找到别人没找到的赢家,值得留"_。

### 4. `channel_ledger.py`(新模块 + CLI)

```python
def roll(scan_root: Path | None = None) -> pd.DataFrame:
    """聚合所有 context/scan/*/retro/channel_eval.csv 跨日 → 每路滚动汇总。
    返回:channel, n_days, sum_unique, mean_unique_excess_t5, mean_excess_t5,
          mean_hit_rate_t5,按 mean_unique_excess_t5 降序。单日缺/空跳过。"""

def render(ledger: pd.DataFrame) -> list[str]:
    """ledger → markdown 段(每路近 N 日边际超额 + hit_rate)。"""

def main() -> int:
    """CLI:roll → 写 reports/learning/channel_ledger.md + 打印。"""
```

跨日均值对 `n_days` 加权前先看 `n_days`:`n_days < 3` 的路在渲染时标 `⚠样本少`(不下结论)。

### 5. 鲁棒性补丁:ratings 兜底(`stage_eval.py`)

L4 per-rating 块现用 `retro._buylist(date, report_root)`(读已发布报告);未 assemble 时返回 `{}` → L4 段不触发(2026-06-22 即此情况:`reports/scan/20260622/` 空)。加纯函数:

```python
def _ratings_from_details(date: str, scan_root: Path | None = None) -> dict[str, str]:
    """从 context/scan/<date>/details/<code>.md 解析 {code: 五档评级}(发布前兜底)。
    正则取 `**Rating**` 行;非五档 / 无文件 → 跳过该只。"""
```

L4 块改 `ratings = retro._buylist(...) or _ratings_from_details(date, scan_root)`。

---

## 数据流

```
scan D:  universe.run → L1_recall_top1000.csv(+recall_channels/n_channels)
         L3/L4 → finalists/details/verify
  ↓ (D+5 交易日后,fwd 已实现)
retro pending → stage_eval.evaluate(D):
   realized_returns(D)  ← 全市场 fwd_1_oo/fwd_5_oc/buyable(已有)
   channel_edge(recall, realized) → retro/channel_eval.csv   ← 新
   (+ 现有 L2/L3/L4/Tier-3 段)→ retro/stage_eval.csv
  ↓
channel_ledger.roll() → reports/learning/channel_ledger.md   ← 新(跨日)
  ↓
scan-retro skill 读 retro_input.md 的 L1 段 + ledger → 人/Claude 决定调不调 quota(不自动)
```

---

## 文件结构

- **Modify** `autoresearch/learning/stage_eval.py`:+`channel_edge`(纯函数)、+`_ratings_from_details`、`evaluate()` 加 L1 段、`render_stage_eval` 加 L1 段、L4 块加 ratings 兜底。
- **Create** `autoresearch/learning/channel_ledger.py`:`roll` / `render` / `main`。
- **Create** `tests/learning/test_channel_eval.py`:`channel_edge` 纯函数(合成 recall + stub realized)、`evaluate` L1 段集成(stub realized 注入,无网络)、`_ratings_from_details` 解析、buyable 剔除、unique vs membership。
- **Create** `tests/learning/test_channel_ledger.py`:`roll` 跨合成多日 channel_eval.csv、`n_days<3` 标注、空/缺日跳过。
- **Modify** `.claude/skills/scan-market/screening-playbook.md` + `scan-retro` skill:提 per-channel edge / ledger 的读法与"该不该留一路"的判据。

## 测试策略(TDD,合成 fixture,无网络)

- `channel_edge` 是纯函数:构造小 recall(含 `recall_channels` 如 `"composite|heat"`、`"heat"`、`"composite"`)+ stub realized(已知 fwd_5_oc/buyable)→ 断言:unique 只算独占票、membership 含共享票、buyable=False 被剔且计入 `n_unbuyable`、excess 用全市场中位、空组→None。
- `evaluate` 集成:`realized=` 显式注入 stub(现有 evaluate 已支持 `realized` 参数)→ 断言 `res["stages"]["L1"]["by_channel"]` 有行、`channel_eval.csv` 落地、`ic_n_channels_t5` 在表。
- `channel_ledger.roll`:写 2–3 个合成 `*/retro/channel_eval.csv` → 断言跨日均值、`n_days`、降序、`n_days<3` 标注。
- 仿现有 `stage_eval._selftest` 的合成统计核风格;不触网。

## 非目标(YAGNI)

- **不自动改 quota/floor**(measure-only;用户定)。ledger 只呈现,调参由 scan-retro / 人。
- **horizon 先 T+1/T+5**(`realized_returns` 现成列)。T+10 需给其 `cols` 加 `fwd_10_oc`,留后续。
- 无 per-channel ML、无 RRF/加权融合回写。

## 自检(spec self-review)

- 占位扫描:无 TBD/TODO。
- 一致性:头条度量(excess-vs-market)、判据(unique 边际)、跨日才下结论——三处贯穿 background/组件/测试。
- 歧义:excess 基准明确为"全市场截面中位"(非 recall 子集中位);buyable 缺列时视全 True;均值只在 buyable 行算——均已写死。
- 范围:单 plan 可实现(2 文件改/建 + 2 测试 + 文档);无需拆子项目。
