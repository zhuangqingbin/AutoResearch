---
name: scan-market
description: Use when the user wants to scan the WHOLE A-share market (not one named ticker) to discover buy-worthy stocks AND strong sectors without paying for an LLM API — e.g. "扫描全A股挖掘值得买的票", "全市场选股", "现在哪些板块值得买", "帮我筛一遍A股龙头", "find the best A-share buys / strongest sectors". For a single known ticker use analyze-ticker instead. Project-local skill.
---

# scan-market — 全 A股六段漏斗扫描(挖掘个股 + 板块,零付费 API)

## 核心原理
对 ~5,500 只逐个跑深度报告 = 几亿 token,不可行。本 skill 用**搜索/推荐系统式六段漏斗**:**确定性层**(零 token)把全市场富因子排序 → GBDT/线性**学习重排**收到 ~200;再让 Claude 当资深投资师 **holistic 一次通看、比较着精排**到 ~30;只对这 ~30 跑 **analyze-ticker-lite 决策卡**,最后整合。**token 只跟最终深挖的几十只成正比,与全市场规模无关。**

**渐进深度 + 早停**:L0/L1/**L2 全确定性(零 LLM)**;**L3 精排 = 1 次 Opus-high holistic**(全表一次通看);**L4 = 一只 finalist = 一个 Opus subagent 渐进深度 DD + 早停**(读够真数据才判、判断不好就早停、看着像买点的才深核 P4+P5);买点(≥OW)再过**独立 Opus skeptic** 证伪。**全程 Opus,省 token 靠早停**。

| 段 | 名称 | 引擎 / 模型 | 作用 | 进→出 | token |
|---|---|---|---|---|---|
| **L0** | 选集 | 确定性 | 全市场候选池 + 硬门(剔 ST/退/停牌/次新 + 市值地板) | 全A→~5,500 | 0 |
| **L1** | 召回 | 确定性 · 多路策略召回 | 9 路 channel(动量/反转/成长/价值/主力/北向/吸筹/**高热成交额** + IC 校准复合分)各取 top-Kᶜ → quota union(floor 保底多样性)+ provenance | →1,000 | 0 |
| **L2** | 粗排 | **确定性 · champion 学习重排** | zoo champion 重排(全 20 模型 × 3 horizon 训练、胜线性才晋升;默认 swing `l2_fwd5`,缺/未胜线性→回落 GBDT/复合分) | →200 | **0** |
| **L3** | 精排 | **Opus-high · holistic 单 agent** | 通看 ~200 比较选 + 增量真证据 + **公告/媒体情感(anns_d+akshare)** + **channel 共振** + 论点/红队/sentiment | →~30 | 中 |
| **L4** | 研究 | **一只=一个 Opus subagent 渐进深度 + 早停** | 决策卡(P0 简报→P1–P3 表面→主早停②→P4 陷阱核→③→P5;`rubric_rating` 派生评级) | ~29 卡 | 大头 |
| **买单 skeptic** | 对抗验证 | ≥OW 每只一个独立 Opus 证伪 | 发布前红队 + PM 3透镜裁判 → verify.csv | ~0–4 | 小 |
| **L5** | 整合 | 确定性 | summary(逐阶段表 + token 估算) + buy-list + 漏斗溯源 | 1 份 | 0 |

> **L2 从 AI keep/cut 改成确定性 champion 学习重排**(`autoresearch.models.zoo`):全 zoo(core/seq/graph 共 20 模型)× 3 horizon(`fwd_1_oo/fwd_5_oc/fwd_10_oc`)统一训练,每 horizon 选**胜线性基线**的最优 **core** 模型晋升 champion(默认 `gate=beats_linear`:胜线性即部署=最不伤的 1000→200 切;`gate=positive` 再加 ic>0、只部署真正向);L2 默认加载 swing 的 `l2_fwd5`(与 L3/L4 持有期对齐)。**自保门**:无模型胜线性 → 不晋升 + 清旧 champion,L2 回落 composite(绝不部署比 composite 回落更差的)。AI 判断从此**只在 L3/L4**。

本 skill 是**编排器**,三类角色分工清楚:
- **确定性层(零 LLM)** = L0/L1/L2(`autoresearch.scan.universe` 一次产出,L2 调 `champion_scores` → champion→GBDT→composite 级联回落)+ L5(`autoresearch.scan.assemble`)。纯 pandas/树/torch,不编数。
- **AI 判断层** = L3(holistic 单 agent 精排)+ L4(逐只决策卡),`autoresearch.scan.agents.l3_select` / `autoresearch.scan.agents.l4_card` 供紧凑表/取数/合并/级联名单;subagent 只回传紧凑结果。
- **L4 委托 analyze-ticker-lite**:**一只 finalist = 一个 Opus subagent 渐进深度 DD + 早停**(P0 简报定向 → P1–P3 表面 → 主早停② → P4 陷阱核 → ③击杀 → P5 满卡;早停只向下、≥OW 必走 P4+P5)→ 买单(≥OW)独立 Opus skeptic 证伪。

## 何时用 / 不用
- ✅ 用户想**一次扫全市场**、挖"值得买的票 / 强势板块"(A股)。
- ❌ 已知**单个** ticker → **analyze-ticker**(全量)或 **analyze-ticker-lite**(快速卡)。
- ❌ 港股/美股全市场:本期不支持。

## 前置
- 在**项目根目录**运行;akshare/tushare/lightgbm 已装(venv-only,**务必 `uv run --no-sync`**);`.env` 有 `TUSHARE_TOKEN`(默认源)+ `FRED_API_KEY`(L4 取数)。默认中文。
- **召回权重 + L2 champion**:`weights.json`(`factor_lab calibrate` 产,L1 复合分 + L2 回落基线)+ **lake 历史**(`python -m autoresearch.data.harvest <start> <end>` 落 `context/lake/`)→ **zoo 训练**(`python -m autoresearch.models.zoo train --dates-from … --dates-to …` → `context/factor_lab/zoo_leaderboard.csv` + champion 落 `models/store/l2_<horizon>/`;缺/未胜线性→自动回落)。**方法 + IC 实证基线见 `screening-playbook.md` 附录**。
- **闭环(开跑前补跑复盘)**:先 `uv run --no-sync python -m autoresearch.learning.retro pending`;若列出未复盘日 → 先用 **scan-retro** 把它们补上(权重/经验更到最新)再开始今天的扫描。
  - retro 的 `retro_input.md` 自带 **各阶段 agent edge**(`stage_eval`:L2 重排/L3/L4/买单 skeptic 各段对已实现收益的 lift/IC)+ **经验升门候选**(`feedback_store.promotion_candidates()`)。
  - L3 的『因子方向经验校准』运行时由 `feedback_store.render_calibration_block(本批申万行业, with_feedback=True)` 注入(近期反馈 + 自学习经验 + IC 基线,三层叠加);用户对报告的反馈用 **feedback** skill 记。

## 流程(6 段)
> 操作细节(L3 holistic prompt 模板 / 评分卡 / 多空辩论)全在 `screening-playbook.md`,按段对照。

1. **L0 选集 + L1 召回 + L2 粗排(全确定性,零 token)**:
   ```bash
   uv run --no-sync python -m autoresearch.scan.universe [YYYY-MM-DD] [--source tushare] [--recall-n 1000] [--l2-n 200] [--cap-floor 30] [--exclude-bj] [--recall-mode multi|composite] [--recall-channels a,b,c]
   ```
   → `L1_recall_top1000.csv`(复合分 + 9 子分〔含 volprice〕+ 原始因子 + **多路 provenance `recall_channels`/`n_channels`**)+ **`L1_channels.csv`**(各路召回名单,复盘/学习用)+ **`L2_gbdt_top200.csv`**(champion 重排 top200;`meta.l2_engine` 记 `champion:l2_fwd5` / `gbdt` / 回落 `composite-linear`)+ `sectors.csv` + `meta.json`。默认 `--recall-mode multi`(9 路策略召回,含 `heat` 高热);`composite` 为对拍/回退口径。默认源 tushare、含北交所、日期=今天。
2. **过目(建议)**:读 `L2_gbdt_top200.csv` 头部 + `sectors.csv`,把粗排概览给用户看一眼。
3. **L3 精排(holistic 单 agent,200→~30)**:`harvest_l3_evidence`(龙虎榜/预告/快报)+ **`harvest_l3_news`(近 ~10 日 anns_d 公告情感,入湖复用)** 补真证据 → `l3_table_md(date)` 把 ~200 只压成**一张紧凑表**(因子 + 证据 + **公告情感 + 召回 provenance**)→ **一个 `Agent(model='opus')` + high reasoning 通看全表、比较着选 ~30**(5 维 rubric:channel 共振/资金/基本面/情感/脆弱;每只出 `论点 + 红队 + 催化 + 确信/脆弱 + lane + sentiment`)→ 落 `L3_judged_full.csv` → `merge_l3_finalists_v2(judged, target=30)`(趋势配额安全网)→ `finalists.csv`。函数在 `autoresearch.scan.agents.l3_select` / `l3_news`。**比较式 > 孤立逐只打分**(后者各看各的、易虚高)。
4. **L4 研究(token 大头,一只=一个 Opus subagent)**——helper 在 `autoresearch.scan.agents.l4_card`:
   - **L4 · 渐进深度 + 早停**:对 finalists 每只 `l4_card.compose_funnel_brief(code, scan_dir)` 拼简报前置 slim 顶 → 一个 `Agent(model='opus')` 跑 **analyze-ticker-lite**(`harvest <ticker> <date> --slim` → staging `details/<ticker>.md`)。**P0 简报定向 → P1–P3 表面填 4 维 → 主早停②(非买点 → 早停卡)→ survivor P4 陷阱核 → ③击杀 → P5 满卡;评级由 `l4_card.rubric_rating` 派生(防 gestalt 过度多报)、早停只向下、≥OW 必走 P4+P5**。~29 个 subagent 一条消息并发派发。
   - **买单 skeptic · ≥OW · 独立 Opus**:`l4_card.pick_buy_candidates(ratings)`(最终 Buy/OW)每只派一个**独立** `Agent(model='opus')` 证伪(subagent 满卡多头 = bull 方,skeptic 只演空头),主线当 **PM 用 3 透镜投票裁判** → `verify.csv`(`code,verdict,bull,bear,trigger,consensus`)。
5. **L5 整合**:
   ```bash
   uv run --no-sync python -m autoresearch.scan.assemble <date>
   ```
   → **`reports/scan/<YYYYMMDD_HHMM>/`**(目录名 = **实际运行时刻**;数据日 analysis_date 记 `manifest.json`,解耦,retro 据此定位):`summary.md`(三段:漏斗数量 / 各阶段概览 / **buy-list〔逐阶段结论表 L1→L2→L3→L4 + 买单 skeptic 徽标〕** + **各阶段 token 估算**)+ `details/〈股票名称〉.md`(决策卡按名称命名)+ `trace/`(每阶段全量数据 + `reasoning/` 留痕 + funnel)。**汇报**:漏斗 + buy-list(评级/目标 + 多空 verdict)+ 诚实局限。

## 铁律
- **确定性层零 LLM**:L0/L1/**L2**/L5 全 pandas/GBDT,不在筛选里编数。
- **召回宽、判断深**:L1 高召回(快因子排序)→ L2 GBDT 学习重排收口;真正的多空取舍在 L3 holistic 精排 + L4 决策卡;慢因子(筹码/北向/基本面)在 L3/L4 兑现。
- **L3/L4 必须 subagent**:L3 一个 holistic agent(独立 context)+ L4 每只独立 context,只回传紧凑结果(L3 论点分 / L4 评级目标),否则撑爆主线。量大可选 **workflow** 并行(需用户显式开启)。
- **每只 finalist 走 analyze-ticker-lite**——继承其铁律(数字出自 slim context、五档评级、EV/R:R、`FINAL TRANSACTION PROPOSAL`、诚实局限)。
- **中间名单全 staging**(L2_gbdt / L3_evidence / finalists),L5 发布到 `trace/` 留溯源;re-run 友好。
- **诚实收尾**:召回/粗排是启发式 + T+1 单 horizon IC 校准/训练(随 regime 漂移);L3/L4 是 Claude 推理产出;"仅供研究,非投资建议"。

## 常见坑
- 必须 `uv run --no-sync`(不误删 venv-only 的 akshare/tushare/lightgbm)、仓库根目录。
- **默认 `--source tushare`**(东财 push2 常被网络封锁);需 `TUSHARE_TOKEN`。富因子(资金结构/筹码集中度/北向/RSI)缺端点权限则自动降级置 NaN、打分重归一。
- **召回权重 / L2 模型**:`weights.json` 缺失 → 内置先验(能跑但弱);`gbdt_model.pkl` 缺失或 oos 未胜线性 → **L2 自动回落线性 top200**(`meta.l2_engine` 会标),不报错。改因子/组后:`factor_lab harvest`(取数,一次)→ `calibrate`(线性权重)→ `train`(GBDT,看是否胜线性)→ `eval`(复核 IC)再上线(方法见 `screening-playbook.md` 附录 B)。
- **GBDT 胜不过线性是常态**(成型日少时):薄面板上 GBDT 多半只复刻线性(composite 锚定特征),加不出稳健非线性 → 门关、用线性。要它真启用:`harvest` 更多成型日(更广 regime)再 `train`。
- `context/`、`reports/` 已 gitignore;别误提交大文件。

---
## 设计沿革(可选背景,删除不影响运行)
本 skill 的**两份文档**(本文 + `screening-playbook.md`)是**自足**的:跑全流程、读懂每段、改因子、校准/训练所需的一切都收在这两份里。下列 `docs/specs/` 仅历史设计推演,**删掉不影响运行**,且部分已落后于现实现(以本 skill 为准):
- `2026-06-20-scan-market-v2-design.md` — 六段漏斗 + 召回校准方法母文档
- `2026-06-20-l2-dual-lane-design.md` — L2 旧双赛道 AI keep/cut(**已被 L2 确定性 GBDT 学习重排取代**)
- `2026-06-21-cost-cascade-design.md` — 模型成本级联(Sonnet 宽段 / Opus 顶点)
- `2026-06-21-agent-upgrade-design.md` — C 评分卡 rubric / A 多空辩论 / B 3透镜共识 / E 记忆闭环 / F 各阶段 eval
