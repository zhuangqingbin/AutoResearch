# Phase 3 — L3 Opus-high holistic + 情感特征（FinGPT 借鉴）设计

> 续 `docs/specs/2026-06-22-autoresearch-arch-redesign-design.md`（Phase 1 骨架）的 **Phase 3**。
> L3 精排：holistic 选股 agent 升 **Opus-high**；加 **公告情感特征**（FinGPT 的「情感即特征」思路，Claude 自任情感引擎，零付费 API）；细化 prompt；与 Phase 2 的 channel provenance 合流。

## 目标 / 非目标

**目标**
- L3 holistic 选股 agent：**Sonnet → Opus, high reasoning**（1 次通看 ~200 表，非逐只 → 成本仍小）。
- **情感即特征**（FinGPT 可借鉴点）：确定性 harvest tushare **`anns_d`（信息披露公告）** → 每股 **紧凑 digest**（公告数 + 事件标签 + 最新标题）→ 并进 L3 表，Opus 把情感纳入 conviction。harvest 入 **lake**（`anns_d/<ann_date>` 不可变）→ **L4/analyze 复用**。
- **与 Phase 2 合流**：`recall_channels` provenance 成为 holistic prompt 的信号（「几路共振」）。
- **细化 prompt**：显式比较 rubric（channel 共振 · 资金确认 · 基本面支撑 · **情感** · 脆弱度）+ 反羊群；输出加 `sentiment` 字段，经 `merge_l3_finalists_v2` 透传。
- **记录 FinGPT 映射**：哪些借鉴、哪些不用、哪些留 learning loop。

**非目标（本 spec 不做，留后续）**
- 不做 L1 情感 channel / L2 情感特征（Q3 的 broader 选项；待本 digest 验证数据后再说）。
- 不做新闻**全文** NLP / 不跑 FinGPT 模型（**Claude 即情感引擎**，更强且零 API）。只用公告**标题**（已足够判材料事件方向）。
- 不改 L1/L2/L4 的算法主体（L4 仅「复用同一 harvest」为可选低优先备注）。
- 情感不做数值因子回测（属 learning/retro 后续）。

## 决策摘要

| 项 | 决策 |
|---|---|
| 模型 | holistic agent = **Opus, high effort**，1 次调用通看全表 |
| 情感深度 | **L3 digest**（Q3「你来推荐」→ 取 MVP：harvest+digest 喂 Opus，无额外模型/stage） |
| 数据源 | tushare **`anns_d`**（per-ts_code 可靠）；非泛新闻快讯（难归属个股） |
| 情感引擎 | **Claude 自任**（读 digest 在 holistic 内打分）；FinGPT 只借「情感即特征」范式 |
| 缓存 | harvest 入 lake，L4/analyze 复用 |

## 现状（本 spec 改什么）

**L3 today**（`scan/agents/l3_select.py` + `screening-playbook.md`）：
- 输入 ~200 行紧凑表（`l3_table_md`）：`composite/gbdt_score` + 9 子分 + 关键原始因子 + 证据摘要（`lhb_n/has_forecast/has_express`）。
- holistic subagent（**Sonnet**）通看全表，比较着选 ~30，逐只出 `conviction/fragility/thesis/risk/catalyst/lane`。
- **无新闻/情感输入**；模型为 Sonnet。

## 架构

### 1) 公告 harvest（确定性，入 lake）`scan/agents/l3_news.py`（新）
```python
def harvest_l3_news(date: str, codes: list[str], lookback_days: int = 10) -> dict[str, list]:
    """tushare anns_d 按 ann_date bulk 拉最近 ~10 交易日 → 按 code 分桶 → 入 lake(复用)。
       -> {code: [ {ann_date, title, ...}, ... ]}。无权限/空 → 各 code 空列表(降级不破)。"""
```
- 走 `data/sources` + `data/cache.get_or_fetch`：每个 `ann_date` 一个 lake key（`anns_d/<ann_date>.parquet`，桶①不可变·永不重取）→ 跨 run/跨 skill 命中。
- 按 `ts_code` 本地过滤到 L2-200 survivors；bulk by date 一次拉、本地分桶（与 `harvest_l3_evidence` 同模式）。
- 降级：端点无权限/空 → 记 `_errors`，各 code 空列表；L3 表照常渲染（情感列空）。

### 2) digest（确定性压缩）`scan/agents/l3_news.py`
```python
_EVENT_TAGS = {   # 标题关键词 → 事件方向标签(粗,Claude 在 holistic 内细化)
  "利多": ["回购","增持","中标","股权激励","业绩预增","预盈","扭亏","定增过会","重组","收购"],
  "利空": ["减持","质押","问询函","关注函","立案","商誉减值","业绩预减","预亏","退市风险","违规"],
}
def news_digest(anns: list[dict]) -> dict:
    """-> {news_n:int, news_tags:str("利多×2|利空×1"), news_head:str(最新标题≤24字)}。空→{0,"","—"}。"""
```
- **紧凑**：~200 行 × 每行 ≤ ~40 字 digest（公告数 + 方向标签计数 + 1 条最新标题截断）→ Opus 一次通看不爆 context。
- 标签是确定性关键词匹配（小词典）；最终方向判断交 Opus（标题可反讽/中性，Claude 细化）。

### 3) L3 表扩列（`scan/agents/l3_select.py`）
- `load_l3_input`：合并 `news_digest` → 追加 `news_n / news_tags / news_head` 列。
- `_L3_COLS` / `l3_table_md`：在证据摘要后加这三列 + **Phase 2 的 `recall_channels / n_channels`**（若存在）→ holistic 一眼见「几路共振 + 近期公告情感」。

### 4) holistic 选股 prompt 升级（`screening-playbook.md`，编排层）
- 模型：`Agent(model='opus')` + **high reasoning effort**（1 次，全表）。
- **比较 rubric**（显式维度，反「只挑 composite 顶」）：
  1. **channel 共振**（`n_channels`/`recall_channels`：多路召回 = 多信号确认）
  2. **资金确认**（main_net_ratio / lhb_n）
  3. **基本面支撑**（growth/value 子分 + np_yoy/roe）
  4. **情感**（`news_tags`/`news_head`：材料事件方向）
  5. **脆弱度**（过热/见顶/利空公告）
- **反羊群**：要求跨 lane 选、给出为何 *此刻* 选它（催化 + 共振），而非复述高分。
- 输出 schema：现有 `conviction/fragility/thesis/risk/catalyst/lane` **+ `sentiment`**（利多/中性/利空 + 一句依据）。

### 5) finalists 透传（`scan/agents/l3_select.py`）
- `merge_l3_finalists_v2`：输出列加 `sentiment`（与 thesis/risk 并列），L4/L5 可见。

## trace / lake
- **lake**：`anns_d/<ann_date>.parquet`（桶①不可变）→ 跨 run/skill 复用；**L4 analyze-ticker-lite 的 slim context 可复用同一 harvest**（可选低优先：在 analyze 取数处加一行读 lake）。
- **trace**：L3 输入表（含 digest）staging 到 `context/scan/<date>/`；holistic 的 **prompt+response 逐字** 入 `trace/<run>/agents/L3_select/`（Phase 1 既有现场存储）。

## FinGPT 映射（记录:借什么/不借什么）
| FinGPT/FinNLP 元素 | 我们怎么处理 |
|---|---|
| 情感即特征（sentiment → 选股信号） | **采纳**：公告 digest 喂 L3 holistic |
| 指令微调的情感模型 | **不用**：Claude 更强且零 API；Claude 自任情感引擎 |
| FinNLP 数据连接器（新闻源） | **等价替换**：tushare `anns_d`（免费、per-ts_code 可靠） |
| market-feedback RLHF（情感 vs 价格验证） | **留 learning/retro**：用前瞻收益验证 L3 情感判断（后续 phase） |
| L4 情感 | **复用同一 harvest**（analyze slim context 加读 lake，可选低优先） |

## 测试
- `harvest_l3_news`：降级（无权限/空 → 各 code 空列表、记 `_errors`、不抛）；lake 命中（同 ann_date 二次调用不重取）；按 ts_code 分桶正确。
- `news_digest`：关键词标签计数正确（利多/利空/混合/无）；`news_head` 截断；空输入 → `{0,"","—"}`；确定性。
- `load_l3_input` / `l3_table_md`：含 digest 列、缺 harvest 时三列缺省渲染不破、列顺序含 recall provenance（若有）。
- `merge_l3_finalists_v2`：`sentiment` 列透传到 finalists。
- 合成夹具：免网络（造 anns 列表 + L2_top200 csv）。

## 文件清单
- 新建：`scan/agents/l3_news.py`（harvest + digest + 词典）；`tests/scan/test_l3_news.py`。
- 改：`scan/agents/l3_select.py`（`load_l3_input` 合 digest、`_L3_COLS` 扩列、`merge_l3_finalists_v2` 透传 sentiment）；`tests/scan/test_agents.py`。
- 改：`.claude/skills/scan-market/screening-playbook.md`（L3 段:Opus-high + rubric + sentiment 输出）、`SKILL.md`（L3 行表述）。
- 可选低优先：`analyze/harvest.py` 复用 `anns_d` lake。

## 成功标准（Phase 3 Done）
- L3 表含 `news_n/news_tags/news_head`（+ 可见 recall provenance）；harvest 入 lake 且二次命中不重取。
- `screening-playbook.md` 的 L3 升 Opus-high + rubric + `sentiment` 输出；`merge_l3_finalists_v2` 透传 sentiment。
- 无权限端点下整链降级跑通（情感列空、表照常）。
- 全 pytest 绿；ruff 净。
- FinGPT 映射表入文档。

## 风险 / 开放项
- **`anns_d` 权限/覆盖**：高权限 token 应可；不可得则降级（情感列空，不阻断）。manifest/`_errors` 记可见。
- **标题级情感偏粗**：只用标题（紧凑）；Claude 在 holistic 内细化方向（标题可中性/反讽）。需要更细 → 后续接全文/news 端点。
- **~200 digest 的 context 量**：靠 digest 紧凑（≤~40 字/行)；若仍偏大 → 后续可选 Sonnet 情感预pass 出单列标签（Q3 的中间档,本 spec 不做）。
- **Opus-high 成本**：仅 1 次（全表 holistic），可接受；非逐只。
