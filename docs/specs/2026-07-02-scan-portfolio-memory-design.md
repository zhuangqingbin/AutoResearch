# scan 组合与记忆(买单ledger/仓位overlay/触发直通车/行业备忘录/编排lint)设计

日期:2026-07-02 ｜ 分支:feat/scan-portfolio-memory ｜ 状态:实施中

## 1. 触发直通车(watchlist.express_candidates / append_express)

观察单触发的票**当天可能已不在 L2 菜单** → 触发了没人研究 = 悲剧。触发* 行且不在
finalists → 追加进 finalists.csv(lane=watchlist_trigger,thesis=触发叙事,sector 从
L1_scored_full 补),直达 L4 **再判**(触发≠升级,评级仍由本卡 rubric 定)。幂等。
SKILL 步 4 派发前跑。

## 2. 买单 ledger(learning/buy_ledger.py)

买后无度量 = "推得好不好"永远靠感觉。逐 ≥OW 买单(verify 折回后,health.final_ratings
同口径)× attribution 已实现 fwd → `date/code/rating/gap_open/fwd_1/fwd_5/fwd_10/
target_ret/target_hit`。fwd_10_oc 本波已加进 realized_returns cols + retro._KEEP
(forward_returns 本就算、原先被丢);目标价从卡片仪表盘解析、除以当日 L1 收盘。
`rating_base_rates(min_n=10)`:**评级基率**(OW 历史 T+5 胜率)→ n≥10 后注入
skeptic/PM 当先验(playbook)。0 买期输出空表 = 机制就绪等首单。

## 3. 仓位 overlay(assemble._position_overlay)

regime 档位(risk_off 0–2 成 / range 3–5 成 / trend 5–8 成)+ 菜单病取下沿 +
0 买日"空仓与读数一致"。**只作用于总仓位,不改单票评级**(同策略师铁律)。
_portfolio_note 增:≥2 买单同板块 → "相关性上是 1 个 bet" 告警。缺 regime → 整行消失(parity)。

## 4. 行业备忘录(learning/sector_memo.py)

记忆三层的中层(全局 lessons ↔ 个股档案之间):`sector_memos.jsonl`(sector 一行,
upsert 覆盖)。**内容由 retro 月度蒸馏**(≥20 scan 日,Claude 从当月卡片提炼"估值区间/
门高频原因/坑位"),存取确定性。注入:L4 简报一行(render_memo_line)+ L3 prompt 块
(render_memo_block,编排层拼)。防锚定铁律同档案:历史事实非方向。

## 5. 编排完备性 lint(self_review 第 8 检)

LLM 段可能被静默跳过(全部未实跑过的已知风险):
- **买单未过 skeptic**(buys>0 ∧ verify 空)→ **fail**(最后防线不可跳);
- **策略师未跑**(finalists≥5 ∧ 无 market_view.md)→ warn。
阈值(finalists≥5 / buys>0)防合成小 fixture 误报。watchlist 未日检不进 lint
(需全局路径,已由 run_health.missing 覆盖)。

## 测试
test_watchlist.py 增 express 3 例、test_buy_ledger.py、test_sector_memo.py、
test_self_review.py 增流程 lint 2 例、test_portfolio_overlay.py。合成,无网络。
