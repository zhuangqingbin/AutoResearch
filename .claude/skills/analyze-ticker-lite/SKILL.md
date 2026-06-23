---
name: analyze-ticker-lite
description: Use when you need a FAST, low-token decision card for a single ticker (5-tier rating + 3-scenario target/R:R + variant view + tripwires) instead of a full deep-dive report — chiefly as scan-market's L4 研究阶段 workhorse over the ~30 finalists, or when the user wants a quick read on one name. ~20–30% of analyze-ticker's tokens. For the full evidence-appendix report use analyze-ticker. Project-local.
---

# analyze-ticker-lite — 单只决策卡(轻量,~20–30% token)

## 核心原理
和 analyze-ticker 同源(同一免费数据层 + Claude 当引擎),但**只产出一张决策卡**,且 harvest 走 `--slim`(只取决策驱动块)。省 token 的两个杠杆:**harvest 更少(输入)+ 只写卡(输出)**。用于 scan-market 的 L4 研究阶段(~30 只 finalists 逐只跑),或用户想快速判一只票。

**与 analyze-ticker 的分工**:lite 出"买不买"的卡;若某只值得下重注,再对它单独跑**全量 analyze-ticker** 看完整证据附录。

## 何时用 / 不用
- ✅ scan-market L4 研究阶段:对 finalists 批量出决策卡。
- ✅ 用户要某只票**快速**的评级/目标/R:R,不需要 8 段深挖。
- ❌ 要完整证据链(8 分析师段 + 多空散文 + 红队/风险辩论)→ 用 **analyze-ticker**(全量)。

## 前置
- 项目根目录;`.env` 有 `FRED_API_KEY`;A股需 akshare。默认中文。

## 流程(3 步)
1. **slim 取数(零 LLM)**:
   ```bash
   uv run --no-sync python -m autoresearch.analyze.harvest <ticker> <date> --slim
   ```
   → `context/<ticker>_<date>_slim.md`(技术快照/指标、市场资金、可交易性、个股新闻、(A股)股东户数、估值概况、利润表、盈利质量、偿付、卖方目标、财报/解禁日历)。**slim 已重排「表面块在前 / 深核块(利润表/盈利质量/偿付)在后 + `<!-- P4 深核分界 -->` 标记」支持渐进读盘;被 scan L4 调用时顶部前置漏斗简报(L1/L2/L3 评价,定向用)。**
2. **渐进 DD + 早停**(按 `lite-playbook.md`):**P0** 读漏斗简报定向 → **P1–P3** 读表面块填 4 表面维(技术资金/基本面/估值/催化)→【**主早停②**:加不起买点 → 出**早停卡**止,跳深核】→ survivor 读 `<!-- P4 深核分界 -->` 后做 **P4 陷阱核**(CFO/质押/商誉/周期顶)【**③** 命中击杀】→ **P5** 满卡(三档 EV/R:R + 多空自压)。**早停只向下,任何 ≥OW 必走 P4+P5**。落点:**独立跑** `reports/analyze/<YYYYMMDD>_<HHMM>/<名称|TICKER>_lite.md`(A股→中文名、其他→TICKER);**被 scan L4 调用** staging `context/scan/<date>/details/<ticker>.md`(`autoresearch.scan.assemble` 发布)。沿用 analyze-ticker 数据坑/铁律(`engine-playbook.md`)。
3. **(可选)校验**:`autoresearch.scan.assemble` / `parse_rating` 直接读这张卡(含 `**Rating**` + 决策仪表盘 + `FINAL TRANSACTION PROPOSAL`)。

## 铁律(继承 analyze-ticker)
- 每个价格/财务数字**出自 slim context**,不编。
- `get_verified_market_snapshot` 为价格唯一真值。
- 卡必须含 `**Rating**`(五档)+ `FINAL TRANSACTION PROPOSAL`(发布层依赖);**满卡**另含三档情景+EV+R:R+认错位+诚实局限,**早停卡**只到评分卡+Rating+一行(陷阱维标「未核」)。
- **早停建立在已读真数据,不据漏斗简报判**(防误杀);**早停只向下,≥OW 必走 P4+P5**。
- 收尾写明"Claude 推理产出、仅供研究,非投资建议"。
- **不水化也不补全**:lite 就是卡;要附录就对本票跑全量。

## 常见坑
- `--slim` 砍掉了:OHLCV 原始 / 全球宏观 / 内部交易 / 持仓做空 / 8×FRED / 资产负债+现金流全表 / 期权 / 同业全表。**卡里别引用这些**(没取);要它们 → 全量 analyze-ticker。
- 其余同 analyze-ticker:`uv run --no-sync`、仓库根目录、A股 降级、`context/`/`reports/` 已 gitignore。
