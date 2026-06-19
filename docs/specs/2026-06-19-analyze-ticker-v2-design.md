# analyze-ticker v2 — 设计稿

- 日期：2026-06-19
- 状态：已批准（用户）
- 关联：`.claude/skills/analyze-ticker/`、`scripts/harvest_context.py`、`scripts/assemble_report.py`

## 目标
把"Claude 当引擎、零付费 API"的 in-session 流程从 **12 棒升级到 17 棒** + 更严的产出标准：分析更**全面**（估值/事件/同业），更**诚实**（证伪/红队/全员置信度）。美股满血；A股/港股因 yfinance 缺期权/分析师覆盖而**自动降级**并注明。

## 新流水线顺序（新增**加粗**）
```
分析师: 市场→情绪→新闻→基本面→**估值**→**催化剂&定位**→**同业/相对**
  → **证伪校验(claims audit)**
  → 多空辩论 → 研究经理 → 交易员
  → 风控三方(激进→保守→中立) → **预审红队(pre-mortem)**
  → 投资组合经理(**三档情景+概率+EV+触发位**)
```

## 新增 agent（5）
1. **估值分析师** — bull/base/bear 三档目标价（前瞻EPS×倍数 / 简化DCF / 对标分析师一致预期），含假设+敏感性+对现价上/下行。
2. **催化剂&定位分析师** — 下次财报日+EPS预期、期权隐含波动幅度、IV水平/skew、put/call 持仓倾向、分析师目标价&评级变动。
3. **同业/相对分析师** — 相对估值(fwd PE vs 同业)、相对强度(1/3/6月 vs 同业+基准)、板块内偏贵/便宜、领先/落后。
4. **证伪校验** — 把分析师+多空的每条事实主张回对 context 数字，列出无支撑/高估主张 + grounding 评分 → 喂研究经理。
5. **预审红队(pre-mortem)** — "假设 12 个月后亏 30%，复盘 3-4 个最可能原因" + 具体早期预警触发位 → 喂 PM。

## 改动
- **全员置信度**：每份报告结尾加 `置信度: 高/中/低 ｜ 最大不确定项: …`。
- **PM 升级**：保留 Rating/执行摘要/论点/持有期，新增 **三档情景(目标价+概率)**、**期望值(概率加权)**、**触发/失效位**。`parse_rating` 仍读 `**Rating**`，不破坏现有解析。

## harvester 新增 4 块（yfinance 直取，各自 try/except 降级）
- 期权链/IV 摘要（最近到期 ATM IV、隐含波动幅度、put/call OI 比；非美无期权→注明）。
- 分析师一致预期（目标价 mean/high/low、评级、覆盖数、近期升降级）。
- 财报日历（下次财报日 + EPS 预期、除息日）。
- 同业相对（peers 可选 4th 参数；缺省用内置小映射或仅基准 SPY/板块ETF；算 1/3/6月相对收益 + 同业前瞻 PE）。

## 文件结构 & 校验
- `1_analysts/` +valuation/catalyst/peer；`2_research/` +verification(在 bull 前)；`4_risk/` +premortem(在 neutral 后)。
- 更新 `assemble_report.py` SECTIONS → 17 段；保留缺文件守卫并冒烟测试。
- 更新 `engine-playbook.md`(v2 角色/顺序/格式) + `SKILL.md`(流程)；走 writing-skills gap 测试。
- **在 NVDA 上跑完整 v2** 验证：17 段产出 + `parse_rating` 正常 + PM 三档情景成形。

## 风险 / YAGNI
- 同业**选择**最易出错 → 可选参数化、不硬猜；缺省仅基准。
- 期权/分析师/财报数据**美股为主**，非美**自动降级**并注明。
- 每块数据独立 try/except，单源失败不拖垮整轮（沿用现有 Polymarket 降级模式）。
