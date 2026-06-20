---
name: scan-market
description: Use when the user wants to scan the WHOLE A-share market (not one named ticker) to discover buy-worthy stocks AND strong sectors without paying for an LLM API — e.g. "扫描全A股挖掘值得买的票", "全市场选股", "现在哪些板块值得买", "帮我筛一遍A股龙头", "find the best A-share buys / strongest sectors". For a single known ticker use analyze-ticker instead. Project-local skill.
---

# scan-market — 全 A股六段漏斗扫描(挖掘个股 + 板块,零付费 API)

## 核心原理
对 ~5,500 只逐个跑深度报告 = 几亿 token,不可行。本 skill 用**搜索/推荐系统式六段漏斗**:确定性召回(零 token)把全市场富因子排序到 top1000,再用 Claude 当资深投资师做**两轮 AI 判断**(粗排/精排)收到 ~30,只对这 ~30 跑 **analyze-ticker-lite 决策卡**(研究阶段,每只 ~20% token),最后整合。**token 只跟最终深挖几只成正比,与全市场规模无关。**

| 段 | 名称 | 作用 | 进→出 | token |
|---|---|---|---|---|
| L0 | 选集 | 全市场 + 硬门 | 全A→~5,500 | 0 |
| L1 | 召回 | 富因子复合分(T+1 IC 校准) | →1,000 | 0 |
| L2 | 粗排 | AI 资深投资师 keep/cut | →200 | 中 |
| L3 | 精排 | 增量真证据 + 论点/红队 | →~30 | 中 |
| L4 | 研究 | analyze-ticker-lite 决策卡 | ~30 卡 | 大头 |
| L5 | 整合 | summary + buy-list + 漏斗溯源 | 1 份 | 0 |

本 skill 是**编排器**:L0/L1/L5 确定性(`screen_market.py` / `assemble_scan.py`);L2/L3 由你扮演资深投资师、subagent 扇出判断(`scan_pipeline.py` 供切片/合并/取数);L4 委托 **analyze-ticker-lite**。设计文档:`docs/specs/2026-06-20-scan-market-v2-design.md`。

## 何时用 / 不用
- ✅ 用户想**一次扫全市场**、挖"值得买的票 / 强势板块"(A股)。
- ❌ 已知**单个** ticker → **analyze-ticker**(全量)或 **analyze-ticker-lite**(快速卡)。
- ❌ 港股/美股全市场:本期不支持。

## 前置
- 在**项目根目录**运行;akshare/tushare 已装(venv-only);`.env` 有 `TUSHARE_TOKEN`(默认源)+ `FRED_API_KEY`(L4 取数)。默认中文。
- **召回权重**来自 `context/factor_lab/weights.json`(`factor_lab.py calibrate` 产;缺失则用内置先验,建议先校准)。
- **闭环(开跑前补跑复盘)**:先 `uv run --no-sync python scripts/retro.py pending`;若列出未复盘日 → 先用 **scan-retro** 把它们补上(权重/经验更新到最新)再开始今天的扫描。L2/L3 的『因子方向经验校准』运行时由 `feedback_store.render_calibration_block(本批申万行业)` 注入(含你的反馈 + retro 学到的经验,叠加在 IC 基线之上);用户对报告的反馈用 **feedback** skill 记。

## 流程(6 段)
1. **L0 选集 + L1 召回(零 token)**:
   ```bash
   uv run --no-sync python scripts/screen_market.py [YYYY-MM-DD] [--source tushare] [--recall-n 1000] [--cap-floor 30] [--exclude-bj]
   ```
   → `context/scan/<date>/L1_recall_top1000.csv`(复合分 + 8 子分 + 原始因子)+ `sectors.csv`(板块概览)+ `meta.json`(漏斗计数)。默认源 tushare、含北交所、日期=今天。
2. **过目(建议)**:读 `L1_recall_top1000.csv` 头部 + `sectors.csv`,把召回概览给用户看一眼。
3. **L2 粗排(subagent 扇出,1000→200)**:按 `screening-playbook.md` 把召回集 `slice_recall` 切 ~10 片×100,每片一个 subagent 当资深投资师 keep/cut(信号共振 / 排陷阱),`merge_l2_keeps` → `context/scan/<date>/L2_coarse_keep200.csv`。
4. **L3 精排(subagent 扇出,200→~30)**:`harvest_l3_evidence` 补真证据(龙虎榜/预告/快报)→ subagent 逐只出 `论点 + 红队风险 + 催化 + 确信度/脆弱度` → `merge_l3_finalists` → `context/scan/<date>/finalists.csv`(带 thesis/risk/catalyst)。
5. **L4 研究(token 大头,~20%/只)**:对 finalists **逐只 subagent** 跑 **analyze-ticker-lite**(`harvest_context.py <ticker> <date> --slim` → 决策卡 → staging `context/scan/<date>/details/<ticker>.md`),**只回传 评级/目标/R:R**;想下重注的票再单独跑全量 analyze-ticker。
6. **L5 整合**:
   ```bash
   uv run --no-sync python scripts/assemble_scan.py <date>
   ```
   → `reports/scan/<YYYYMMDD>/<HHMM>_summary.md`(三段:漏斗数量 / 各阶段卡点+概览 / L4 投资建议)+ `<HHMM>_detail/`(决策卡 + `A_pipeline/` 漏斗溯源)。**汇报**:漏斗 + buy-list(评级/目标/R:R)+ 诚实局限。

## 铁律
- **确定性层零 LLM**:L0/L1/L5 全 pandas/确定性,不在筛选里编数。
- **召回宽、判断深**:L1 高召回(快因子排序);真正的取舍在 L2/L3 的资深投资师判断;慢因子(筹码/北向/基本面)在 L2/L3/L4 兑现。
- **L2/L3/L4 必须 subagent 扇出**:每片/每只独立 context,只回传紧凑结果(L2 保留名单 / L3 论点分 / L4 评级目标),否则撑爆主线。量大可选 **workflow** 并行(需用户显式开启)。
- **每只 finalist 走 analyze-ticker-lite**——继承其铁律(数字出自 slim context、五档评级、EV/R:R、`FINAL TRANSACTION PROPOSAL`、诚实局限)。
- **中间名单全 staging**(L2_keep / L3_evidence / finalists),L5 发布到 `A_pipeline/` 留溯源;re-run 友好。
- **诚实收尾**:召回是启发式 + T+1 单 horizon IC 校准(随 regime 漂移);L2/L3 是 Claude 推理产出;"仅供研究,非投资建议"。

## 常见坑
- 必须 `uv run --no-sync`(不误删 venv-only 的 akshare/tushare)、仓库根目录。
- **默认 `--source tushare`**(东财 push2 常被网络封锁);需 `TUSHARE_TOKEN`。富因子(资金结构/筹码集中度/北向/RSI)缺端点权限则自动降级置 NaN、打分重归一。
- **召回权重**:`weights.json` 缺失 → 内置先验(能跑但弱)。改因子/组后跑 `factor_lab.py harvest`(一次)→ `calibrate` 重拟合 → `eval` 复核 IC 再上线(见 spec §9)。
- `context/`、`reports/` 已 gitignore;别误提交大文件。
