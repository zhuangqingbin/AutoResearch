# scan-market 首席策略师市场研判 — 设计

- 状态:设计定稿(待实现)
- 日期:2026-07-01
- 关联:`docs/specs/2026-06-20-scan-market-v2-design.md`(六段漏斗母文档)、`docs/specs/2026-06-27-scan-l0-l5-optimizations-design.md`(regime 地基)
- 触及:`autoresearch/scan/market.py`(新)、`autoresearch/scan/assemble.py`、`autoresearch/scan/agents/l4_card.py`、`.claude/skills/scan-market/{SKILL.md,screening-playbook.md}`

## 1. 背景与目标

当前 L5 `summary.md` 只有一行确定性 `regime` 定性(`regime_and_drift`),没有"今日 A 股市场"的专业研判。用户要在 scan 流程里引入一个**资深 A 股投资大师视角的市场研判**,并且——经过迭代——不只是末尾报告的一段,而是一个**一次产出、多处复用**的 house view:

- **一次产出**:L0→L1→L2 确定性块跑完后(市场截面首次就绪),派一个专职 **Opus 首席策略师 subagent** 读确定性"市场数据包" → 写 `market_view.md`。
- **三处复用**:
  1. **L3 精排** 读它做 top-down 校准;
  2. **L4 每张决策卡** 简报头注入"市场地形"段,按 regime 校准估值/资金门;
  3. **L5 报告** 嵌到 `summary.md` 顶部 + 追加确定性"漏斗读数"尾注。

### 核心约束(为什么这样切)

1. **assemble.py 保持零-LLM**(铁律)。它只**读** `market_view.md` 文件并嵌入,和现在读 `verify.csv` / `details/*.md` 一模一样。LLM 那步是策略师 subagent 写 staging 文件,不在 assemble 里。
2. **不破 L4 独立性(防锚定)**。喂给 L3/L4 的必须是**描述性地形**(regime + 板块拥挤/超跌分位 + 宽度 + 估值分散),**不是方向指令**("今天别买半导体"会把 20 张卡带成集体附和,破坏"每只独立自下而上 DD + rubric 防 gestalt 多报"的纪律)。个股评级只由本股 rubric 三门决定——大盘看空不压个股、看多不松门(prompt 铁律)。
3. **不制造循环**。策略师在 L3/L4 判决**之前**成文,只读市场结构,不读 buy-list;所以 L5 嵌它时是"独立的市场地形读数",不是"描述它自己促成的结果"。
4. **产出分层**:**描述性**内容(地形/红黑榜)L3/L4 可读;**规范性 + 结果依赖**内容(操作建议、今日 N 买/0 买、观察单)只进 L5(卡片判决时这些还不存在)。
5. **缺 staging 不破老路**。`market_view.md` 缺失(如单独重跑 assemble)→ 回退确定性市场脉搏,summary 永远有市场上下文。

## 2. 架构与数据流

```
L0 → L1 → L2            (autoresearch.scan.universe 一次产出, 确定性, 零 LLM, 秒级)
      │   产出 L1_scored_full.csv + sectors.csv + meta.json
      │
      └─★ 首席策略师 subagent   (L2 完 → L3 前; 编排层派发)
            读 market_pack(scan_dir)  → 写 context/scan/<date>/market_view.md   【staging】
                 │
                 ├─① L3 精排:prompt 前置 market_view 地形段 (+ 现有 calibration block + l3_table_md)
                 ├─② L4 每卡:compose_funnel_brief 头部注入 market_context_block(地形 + 本股板块分位)
                 └─③ L5 报告:assemble 嵌 market_view.md 置顶 + 追加 render_funnel_readout 尾注
```

时机说明:策略师原料(宽度/板块红黑榜/估值分散)全来自 **L1/L2 打分**产出的 `L1_scored_full.csv` + `sectors.csv`。L0 刚启动时市场截面还不存在 → 最早有意义的启动点是 **"L2 一跑完"**(仍在昂贵的 L3/L4 之前)。

## 3. 组件设计

### 3.1 `autoresearch/scan/market.py`(新,确定性,零 LLM)

单一职责:把市场级事实聚合成数据包 + 派生 L3/L4 注入块 + L5 尾注/回退。纯 pandas + stdlib,可用合成 fixture 独立测。

- **`market_pack(scan_dir) -> dict`** — 从落盘 CSV 聚合市场事实(不编数)。字段:
  - `regime`:`classify_regime(L1_full)` → `{label, breadth, med_mom, n}`(复用 `autoresearch.common.regime`)。
  - `breadth`:`above_ma60` 占比、`ma_bull` 占比、中位 `pct_60d` / `pct_ytd`、`pct_60d<-20` 占比(落刀面)、`pct_60d>0` 占比。
  - `valuation`:中位 PE(仅正)、中位 PB、PE 上十分位(贵端)、`pe>60` 占比(哑铃贵端粗标)。
  - `money`:`main_net_ratio>0` 占比、中位 `main_net_ratio`、`cmf_20>0` 占比。
  - `sectors`:读 `sectors.csv` → `red`(强势 top5)/ `black`(弱势 bottom5),每项 `{industry, n_recall, median_composite, median_pct_60d, median_main_net_ratio}`。排序键 = `median_pct_60d`(动量)为主。
  - `funnel`(结果就绪才填,否则 None):`universe/recall_n/l2_n`(meta)、`l2_落刀`/`l2_健康` 计数(L2_gbdt_top200 的因子列)、L4 评级分布(`parse_ratings_from_details`)、verify 计数(维持/降级/否决)。
  - `buylist` / `watchlist`(结果就绪才填):买单摘要 + 待触发观察单(降级 skeptic 的 + 带触发位的 Hold)。
  - 缺列/缺文件 → 该字段降级为 None 或空,不抛。
- **`market_context_block(pack, industry=None) -> str`** — **L3/L4 注入用的描述性地形块**(markdown,几行)。含:regime 一句、宽度、估值分散、板块红黑榜 top3/bottom3;`industry` 给定则附"本股所在板块分位"(该行业 median_composite/pct_60d 在全市场位置)。**只描述、无操作指令、无个股方向**。只用 `market_pack` 的 regime/breadth/valuation/money/sectors 段(不含 funnel 结果段)。
- **`render_funnel_readout(scan_dir) -> str`** — **L5 确定性尾注**(漏斗读数):今日实际 N 买 / 0 买(从 `parse_ratings_from_details` + verify 折回后计数)+ 观察单列表。0 买 → "regime 决定的纪律空仓";N 买 → 点名。永远和 buy-list 一致(同源计数)。
- **`render_fallback_pulse(pack) -> str`** — **market_view.md 缺失时的回退**:2–3 行确定性市场脉搏(regime + 红黑榜 top3/bottom3 + 宽度一句)。

### 3.2 首席策略师 subagent(编排层,skill 内)

- **时机**:`universe.py` 跑完(L2 完)、L3 之前。
- **输入**:`market_pack(scan_dir)`(可 dump 成 `market_pack.json` 供 subagent 读)。
- **人设/prompt**:资深 A 股投资大师 / 首席策略师;数字必须出自 pack(不编数);产出 `market_view.md`,结构(6 段,~300–400 字):
  1. 一句话定调(regime + 结构 + 情绪);
  2. 市场结构(宽度 / 主力资金 / 估值分散哑铃);
  3. 板块红黑榜(强 top3 / 弱 bottom3,各一句 why);
  4. 操作基调(基于 regime 的整体仓位姿态;**规范性,L5-only**);
  5. 关注(催化日历:中报窗口 / 政策会议 / 解禁);
  6. 收尾(仅供研究非投资建议)。
- **产出分节**:1–3 是**描述性地形**(L3/L4 读);4–5 是**规范性 + 前瞻**(仅 L5 嵌)。文件内用清晰小标题分节,便于 L3/L4 注入只取地形段。
- **回传**:只回一行确认(紧凑结果),正文落 `market_view.md`。

### 3.3 L3 注入

- L3 Opus prompt = `market_view.md` 地形段 + 现有因子方向校准块 + `l3_table_md(date)`。
- 编排层前置(playbook 指令);`market.py` 提供便捷取地形段的入口(复用 `market_context_block`,industry=None)。

### 3.4 L4 注入(`compose_funnel_brief` 改)

- `compose_funnel_brief(code, scan_dir)` 头部**自动前置** `market_context_block(pack, industry=本股行业)`。
- 本股行业从 finalists/L1 行的 `industry`/`sector` 取。
- 加 prompt 铁律(playbook + 简报文案):**市场地形是背景校准,非选股指令;本股评级只由 rubric 三门决定**。
- 缺 pack/market_view → 不前置(老 brief 不变,老路不破)。

### 3.5 L5 嵌入(`assemble.py` 改)

- **`_load_market_view(scan_dir) -> str`**:读 `market_view.md`,缺则 ""。
- `build_summary`:在 `regime_line` 之后、`## 1. 漏斗` 之前插:
  - market_view 在 → `## 📈 今日 A 股市场(首席策略师视角)` + 正文 + `render_funnel_readout` 尾注;
  - market_view 缺 → `render_fallback_pulse(market_pack(scan_dir))` 的 2–3 行脉搏。
- assemble 仍零-LLM(只读文件 + 确定性聚合)。

## 4. 数据契约:`market_pack` schema(摘要)

```python
{
  "regime": {"label": "risk_off", "breadth": 0.27, "med_mom": -13.0, "n": 4159},
  "breadth": {"above_ma60": 0.27, "ma_bull": 0.11, "med_pct_60d": -13.0,
              "med_pct_ytd": ..., "falling_knife": 0.42, "up_60d": 0.19},
  "valuation": {"med_pe": 34.0, "med_pb": 2.1, "pe_top_decile": 137.0, "pe_gt_60": 0.18},
  "money": {"main_pos": 0.28, "med_main_ratio": -0.01, "cmf_pos": 0.31},
  "sectors": {"red": [{"industry": "半导体", "median_pct_60d": 114.1, ...}, ...],
              "black": [{"industry": "软件开发", "median_pct_60d": -24.6, ...}, ...]},
  "funnel": {"universe": 5500, "recall_n": 1000, "l2_n": 200,
             "l2_falling_knife": 178, "l4_ratings": {"Overweight": 1, "Hold": 21, ...},
             "verify": {"维持": 0, "降级": 1, "否决": 0}} | None,
  "buylist": [...] | None, "watchlist": [...] | None,
}
```

## 5. 测试(TDD,合成 fixture,无网络)

- **`tests/scan/test_market_pack.py`**:合成 L1_scored_full / sectors / (finalists/verify/details)→ 断言 regime/breadth%/估值分位/红黑榜排序/漏斗计数/观察单;缺列降级不抛。
- **`tests/scan/test_market_context_block.py`**:断言地形块含 regime/红黑榜、**不含** funnel 结果/操作指令;industry 给定附板块分位。
- **`tests/scan/test_market_view_embed.py`**:
  - 有 `market_view.md` → summary 顶部含 `## 📈 今日 A 股市场` + 正文 + 漏斗读数尾注;
  - 无 `market_view.md` → 含回退脉搏,且**其余 summary 与旧行为逐字节 parity**(老路不破)。
- **`tests/scan/test_l4_brief_market_ctx.py`**:`compose_funnel_brief` 有 pack → 前置地形块含本股板块分位;缺 pack → 与旧 brief 逐字节一致。

## 6. 落点与非目标

**文件**:`autoresearch/scan/market.py`(新)· `assemble.py`(读+嵌+尾注)· `agents/l4_card.py`(brief 前置)· `SKILL.md` + `screening-playbook.md`(策略师步骤 + prompt 模板 + 注入指令)· 4 个测试文件。

**非目标**:
- 不改任何评级/召回/打分逻辑(策略师**只叙述不改判**;评级仍由 L4 rubric + verify 折回定死)。
- 不引入付费 LLM(策略师是本 session 的 Claude subagent)。
- 不给 L4 卡片方向指令(只给描述性地形校准)。
- 不做前瞻预测的量化(操作基调是 regime 条件化的定性姿态)。
