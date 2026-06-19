# scan-market screening-playbook — 漏斗操作参考

> 读这一份就能跑 scan-market 全流程。打分逻辑的完整规格在
> `docs/specs/2026-06-20-scan-market-design.md` §4–§7;本文是操作向蒸馏 +
> **L3a 分诊**(唯一需要你在 session 内判断的步骤)的具体规则。

## 漏斗一图
```
5,400 →(L0 硬门)→(L1 四透镜松门, 各 top50)~150 →(L2 板块聚合, top5板块内)~100
      →(L3a LLM 分诊)~30 →(L3b analyze-ticker-lite 决策卡, ~20%/只)~30 张卡 →(L4)scan_summary.md
```
token 花在 L3b 的 ~30 张卡(每只 ~20%);L0–L2 零 token,L3a 低 token(只读紧凑 CSV)。

## L0–L2(`screen_market.py`,确定性,你不用手算)
- **L0**:`stock_zh_a_spot_em`(全市场快照)+ `stock_yjbb_em`(业绩,当期&上期)+ `stock_individual_fund_flow_rank`(主力资金)富化成 canonical 列;硬门 = 剔 ST/退市/停牌/次新 + 市值地板(默认 30 亿)+ 北交所开关。
- **L1**:四透镜(动量/成长/价值/反转),各"门 → 横截面分位打分 → top50"。权重见 spec §4.3(动量重资金、成长重加速度、价值重 ROE、反转门+权重双强调拐点)。产出 `survivors.csv`(命中≥1 透镜,带 `n_lens`/`conviction`/`hit_*`)。
- **L2**:survivors 映射 `所处行业`,板块按 **广度 + 跨透镜 + 资金 + 动量** 排名,取 top5;`survivors_top_sectors.csv` = top 板块内 ~100,**喂 L3a**。
- 关键产物:`sectors.csv`(板块榜)、`survivors_top_sectors.csv`、`meta.json`(漏斗计数)。

## L3a — 轻量分诊(你来做,低 token)
**目标**:把 `survivors_top_sectors.csv` 的 ~100 只,用确定性分给不了的**定性判断**收到 ~30 finalists。

**步骤**:
1. 读 `survivors_top_sectors.csv`(列:`code name industry mktcap_yi close pct_60d pct_ytd main_inflow_yi rev_yoy np_yoy roe pe pb n_lens conviction hit_*`)。
2. **分批**(每批 ~20–30 只,控 token),逐只判 `倾向(看多/中性/回避) · 一句理由 · triage 分(0–100)`。判的维度:
   - **成长**像不像**账面/一次性**(np_yoy 高但 roe 低、cfo 弱 → 存疑);
   - **价值**是不是**陷阱**(低 PE 但行业衰、营收下滑);
   - **动量**是否**已透支**(pct_60d 极高 + 命中过热);
   - **反转**拐点是否实;**跨透镜**命中(`n_lens`≥2)加分。
3. 少数高潜力但信息不足的,可 **WebSearch** 近期催化/利空(标注『实时网查』,不当确定性数据)。
4. **配额选 ~30,保板块分布**:top ~5 板块各取 triage 头部 ~5–6 + ~2–3 个板块外单透镜超星"外卡"。别让一个板块独吞。
5. 写 `context/scan/<date>/finalists.csv`,列固定为:
   ```
   ticker,code,name,sector,lenses,conviction,triage_lean,triage_reason
   ```
   - `ticker` = 传给 harvest 的代码(裸 6 位即可,harvester 自动补 .SS/.SZ/.BJ);
   - `lenses` = 命中透镜(如 `动量,成长`);`triage_lean` ∈ {看多,中性,回避};`triage_reason` ≤ 20 字。
   - **这张表是 L3b 的输入,也是 L4 buy-list 的标签源**——列名别改。

## L3b — 决策卡深挖(委托 analyze-ticker-lite,~20% token)
对 `finalists.csv` 每只,走 **analyze-ticker-lite**(slim 取数 + 单张决策卡,读其 `lite-playbook.md`,不在这里重复):
```bash
uv run --no-sync python scripts/harvest_context.py <ticker> <date> --slim   # slim 取数,每只 ~13KB(实测 = 全量的 20.4%)
# → 按 lite-playbook 产出单文件决策卡 reports/<date>/<ticker>/complete_report.md
```
- **为什么 lite**:~30 只全量 ≈ ~1.5M token;lite(slim 输入 + 只写卡)≈ **~20%**,~30 只 ≈ **~300k**。
- **必须 subagent 扇出**(不是主线循环):每只一个 subagent,在独立 context 里跑 lite,**只回传 评级/目标/R:R**;主线只收 ~30 条小结果——否则 30×context 撑爆主线窗口。量大可选 **workflow** 并行(需用户显式开启)。
- **某只想下重注**:再单独对它跑**全量 analyze-ticker**(完整证据附录)。**lite 出卡、full 确认。**
- 模型:建议 **Opus**(判断/校准优势)。

## L4 — 综合(`assemble_scan.py`,确定性)
```bash
uv run --no-sync python scripts/assemble_scan.py <date>
```
读 `finalists.csv` + `sectors.csv` + `meta.json` + 各 finalist 的 `complete_report.md`(lite 决策卡;用项目 `parse_rating` 提五档 + 仪表盘解析目标/R:R/置信度 + `FINAL TRANSACTION PROPOSAL`),按 **评级 → 确信度** 排成 buy-list → `reports/scan/<date>/scan_summary.md`(含漏斗计数 + 板块结论 + 局限)。报告缺失的 finalist 会被标 `⚠️报告缺失`(提示 L3b 漏跑)。

## 数据坑(沿用 spec §4.4)
- **扣非净利** bulk 不可得 → L1 用头条净利 + 质量门补偿;真扣非在 L3b 核。
- **MA结构/52周回撤**需历史 → L1 用 `60日/年初至今涨跌幅` 代理;L3b 的 slim snapshot 有当前指标值。
- **spot/资金流端点偶发限流/断连** → `_ak_call` 已 3 次重试 + 该因子降级;若 `stock_zh_a_spot_em` 持续失败,可切 `stock_zh_a_spot`(新浪源)做 universe(改 `fetch_universe`)。
- **`所处行业` 空**(北交所/部分票)→ 归"未分类",板块榜已剔除;**股息率不在这些 bulk 端点** → 价值透镜不含股息。
- **业绩披露滞后** → 用最近可得报告期(脚本按分析日自动推算,`meta.json` 可查),不当实时。
- **L3b slim 砍掉的块**(OHLCV原始/全球宏观/做空/8×FRED/资产负债+现金流全表/期权/同业全表)**决策卡不得引用**——要它们就对该票跑全量。
