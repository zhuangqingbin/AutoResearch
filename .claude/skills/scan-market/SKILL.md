---
name: scan-market
description: Use when the user wants to scan the WHOLE A-share market (not one named ticker) to discover buy-worthy stocks AND strong sectors without paying for an LLM API — e.g. "扫描全A股挖掘值得买的票", "全市场选股", "现在哪些板块值得买", "帮我筛一遍A股龙头", "find the best A-share buys / strongest sectors". For a single known ticker use analyze-ticker instead. Project-local skill.
---

# scan-market — 全 A股漏斗扫描(挖掘个股 + 板块,零付费 API)

## 核心原理
对 ~5,400 只逐个跑深度报告 = 几亿 token,不可行。本 skill 用**分层漏斗**:免费确定性筛选(L0–L2)把 5,400 砍到 top 板块内 ~100,低成本 LLM 分诊(L3a)收到 ~30,只对这 ~30 跑 **analyze-ticker-lite 决策卡**(L3b,每只 ~20% token),再综合(L4)。**token 只跟最终深挖几只成正比,与全市场规模无关。**

本 skill 是**编排器**:确定性筛选(L0–L2)与分诊(L3a)都**不依赖** analyze-ticker;L3b 委托 **analyze-ticker-lite** 出决策卡。设计文档:`docs/specs/2026-06-20-scan-market-design.md`。

## 何时用 / 不用
- ✅ 用户想**一次扫全市场**、挖"值得买的票 / 强势板块"(A股)。
- ❌ 已知**单个** ticker 要深挖 → 用 **analyze-ticker**(全量)或 **analyze-ticker-lite**(快速卡)。
- ❌ 港股/美股全市场:本期不支持(依赖 akshare 中国数据)。

## 前置
- 在**项目根目录**运行;akshare 已装(venv-only);`.env` 有 `FRED_API_KEY`(L3b 取数用)。
- 默认中文报告。

## 流程(6 步)

1. **L0–L2 确定性筛选(零 token)**:
   ```bash
   uv run --no-sync python scripts/screen_market.py [YYYY-MM-DD] [--cap-floor 30] [--include-bj] [--top-per-lens 50]
   ```
   → `context/scan/<date>/`:`sectors.csv`(板块榜)、`survivors_top_sectors.csv`(top 板块内 ~100,**喂 L3a**)、`survivors.csv`、`lens_*.csv`、`meta.json`。日期默认今天。
2. **过目(建议)**:读 `sectors.csv` + `survivors_top_sectors.csv`,把板块榜 + 候选给用户看一眼,确认方向再花 token。
3. **L3a 轻量分诊(低 token)**:读 `survivors_top_sectors.csv`,按 `screening-playbook.md` **分批**(每批 ~20–30)对 ~100 只做定性分诊 → 收到 ~30 finalists,写 `context/scan/<date>/finalists.csv`。**只吃 CSV 紧凑数据,不 harvest。**
4. **L3b 决策卡深挖(token 大头,但已压到 ~20%)**:对 finalists **逐只 subagent** 跑 **analyze-ticker-lite**(`harvest_context.py <ticker> <date> --slim` → 按 `lite-playbook.md` 产出单张决策卡 → `reports/<date>/<ticker>/complete_report.md`)。subagent 独立 context、**只回传 评级/目标/R:R**(避免 30×context 撑爆主线);想下重注的票再单独跑全量 analyze-ticker。建议 Opus。
5. **L4 综合**:
   ```bash
   uv run --no-sync python scripts/assemble_scan.py <date>
   ```
   → `reports/scan/<date>/scan_summary.md`(漏斗计数 + 板块结论 + 评级排序 buy-list;用项目 `parse_rating` 提五档)。
6. **汇报**:给用户板块榜 + buy-list(评级/目标/R:R)+ 诚实局限。

## 铁律
- **确定性层零 LLM**:L0–L2 全 pandas/akshare,不在筛选里编数。
- **分诊不 harvest**:L3a 只读已拉 bulk 的紧凑 CSV;**harvest 只发生在 L3b 的 ~30 只(且是 `--slim`,~20%)**。
- **每只 finalist 走 analyze-ticker-lite(决策卡)**——继承其铁律(数字出自 slim context、五档评级、三档情景/EV/R:R、`FINAL TRANSACTION PROPOSAL`、诚实局限);想下重注的票再单独跑全量 analyze-ticker 看证据附录。
- **L3b 必须 subagent 扇出**:30 只全量 context 不能同时在主线;每只一个 subagent、只回传紧凑决策。
- **保板块结构**:finalists 跨 top 板块分布(配额),别让一个板块独吞。
- **诚实收尾**:筛选是启发式(无回测);写明"Claude 推理产出、非自动引擎、仅供研究,非投资建议"。

## 常见坑
- 必须 `uv run --no-sync`(用锁定环境且**不误删 venv-only 的 akshare**)、且在仓库根目录。
- 东财 spot/资金流端点偶发限流 → 脚本已 3 次重试 + 优雅降级;若 `stock_zh_a_spot_em` 持续失败,换 universe 源(见 playbook)。
- `所处行业` 对北交所/部分票为空 → 归"未分类"(板块榜已剔除);**股息率不在 bulk → 价值透镜暂不含**。
- `context/`、`reports/` 已 gitignore;别误提交大文件。
