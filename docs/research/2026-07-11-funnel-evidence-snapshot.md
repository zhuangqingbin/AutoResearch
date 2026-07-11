# scan-market 漏斗实跑证据报告（2026-07-11 收集·全只读）

收集方式：只读现有文件与既有报表产物；未跑 scan run，未执行任何写状态的 retro/ledger 命令。

## 1. 0买连败（zero_buy_ledger）

出处：`/Users/qingbin.zhuang/Personal/TradingAgents/reports/learning/zero_buy_ledger.md`（mtime 07-10 21:44；生成器 `autoresearch/learning/zero_buy_ledger.py`，聚合 `context/scan/*/retro/attribution.csv`）。

- 台账列出 14 个"0买日"（06-18→07-08），汇总：**0买日市场 fwd_1 −0.43%、fwd_2 −0.66%（主尺）、fwd_5 −1.91% → 空仓方向正确**。
- 逐日（全市场等权）：06-18 fwd_2 +0.54% / 06-22 −0.98% / 06-23 −2.21% / 06-24 −3.73% / 06-25 −2.16% / 06-26 +1.23% / 06-29 +3.52% / 06-30 +1.30% / 07-01 +1.14% / 07-02 −0.41% / 07-03 −4.00% / 07-06 −3.82% / 07-07 −1.02% / 07-08 +1.38%。
- **数据缺陷（重要）**：`n_bought` 读 attribution.csv 的 `bought` 列，但该列没落盘（retro.py:64 只在内存算 `bought=rating.isin(_BUY)`）→ 所有日被记 0买。真实买单日（run_health.json `counts.buys` + `reports/learning/buy_ledger.md` + paper_nav 交易数交叉验证）：**06-19（5买）、06-22（东方财富 1买）、07-08（思特威 1买）**。即 14 日聚合混入 06-22、07-08 两个有买日。
- 真实连败序列：06-23→07-07 连续 11 个交易日 0 买（其中 10 个有 scan run；07-07 无 run 仅 retro 归因）；**07-08 买思特威打断连败；07-09（最后一次实跑）0 买 → 截至 2026-07-11 连续 0 买 = 1 天**（07-10/07-11 无扫描）。

## 2. 评级基率 / 漏斗数量（最近 3 个 run）

出处：`reports/scan/<run>/summary.md`、`context/scan/<date>/run_health.json`、`context/scan/<date>/retro/attribution.csv` rating 列、`context/scan/<date>/details/*.md`。

| run | L0选集 | L1召回 | L2 | L3 finalist | L4卡 | 评级分布 | 买单 |
|---|---|---|---|---|---|---|---|
| 20260706_2314 (07-06) | 4244 | 1000 | 200 | 20 | 20 | 17 Hold / 3 UW | 0 |
| 20260708_2331 (07-08) | 4152 | 1000 | 200 | 15 | 15 | 12 Hold / 2 UW / **1 OW（思特威 688213 conv71 目标 110.4=+5.6%）** | **1** |
| 20260709_2218 (07-09) | 4149 | 1000 | 200 | 11 | 11 | 9 Hold / 2 UW | 0 |

- **累计五档分布**（attribution 14 个 retro 日 + 07-09 卡片）：**Hold 218 / Underweight 92 / Sell 11 / Overweight 4，n=325 → OW 基率 ≈ 1.2%，无 Buy 档**。
- attribution rating 两处噪声：07-01 只 join 到 2 张卡（缺 23）；06-30 胜宏记 OW 但发布前被 skeptic 降级 Hold（attribution 存卡面评级非最终发布评级）。
- 买单基率（`reports/learning/buy_ledger.md`）：OW n=7（5 张 06-19 未成熟），已实现 2 笔 **T+2 胜率 0% / 均值 −3.39%（东财 −2.32%、思特威 −4.46%），目标命中 0%**（样本少警示）。
- 全卡目标校准：近 30 scan 日有目标价卡 63、成熟 37，**10日触达率 43%，中位目标 +8% vs 中位 MFE +4%**（目标价过乐观坐实）。

## 3. 门审计

- **gate_ledger**（`reports/learning/gate_ledger.md`；`autoresearch/learning/gate_ledger.py` = gate_fires.csv × attribution）目前只覆盖卡片契约门：`卡片契约·P4倾向缺失` 2日4拦、被拦 ex2 +2.94%、拦对率 25%；`卡片契约·变化项缺失` 1拦、ex2 +1.70%、拦对率 0%（ex>0=拦错；样本极少）。
- `gate_fires.csv` 全库仅 5 条（07-03×4、07-06×1，全 warn 级）→ **OW 三门（主力真在/业绩真兑现/估值不透支）的击杀没有结构化累计账**。
- run summary 的"OW三门失守分布"解析样本小：07-06 三 run 均"5卡可解析 全 0✗"；07-09"3卡可解析：业绩真兑现✗1 · 估值不透支✗1 · 主力真在✗0"。哪个门杀最多 OW 候选目前只有定性证据：紫光国微×3=CFO门、深圳华强=业绩兑现/CFO门（红队另挖出对外担保75亿=108%净资产）、东北证券=主力门、思瑞浦=估值门 PE177、胜宏=估值+盈利4连miss（skeptic 降级）。
- **门价值原始出处 = `reports/learning/paper_nav.md`**：主尺 hold=2 表（截至 20260708）真实 −0.30% vs 影子 −4.65% → **门价值 +4.35pp（= memory "+4.4pp"）**；副表 hold=10 真实 +0.17% vs 影子 −7.01% → **+7.18pp（= memory "+7.2pp"；07-09 run summary 20260709_2218 打印的正是 hold=10 口径）**。两个数字是同一台账两种持有期口径，不是两个独立门。

## 4. paper NAV（真实 vs 影子 vs 市场）

出处：`reports/learning/paper_nav.md` + `paper_nav_summary.txt`（mtime 07-10 21:50，数据截至 20260708）。

- **主尺 hold=2（10%固定槽，次日开盘进出）**：真实 **0.9970（−0.30%，7笔）** vs 影子(无门) **0.9535（−4.65%，45笔）** vs 市场等权 **0.9417（−5.83%）**。
- 风险调整（X3）：真实 最大回撤 −0.42% / Sortino −1.46；影子 −7.15% / −3.17；市场 buy&hold −6.15% / −4.32。
- hold=10 副表：真实 1.0017（+0.17%）/ 回撤 −0.50% / Sortino +0.62。
- 全序列 14 个日期节点在文件内（起 20260618=1.0000）。

## 5. channel_audit 累计账本

CLI `autoresearch/research/channel_audit.py` 会写 `reports/channel_audit_<date>.md`（有写盘，故直接读现成产物）：`reports/channel_audit_2026-07-08.md`（窗口 06-22→07-08，13日）+ `reports/learning/channel_ledger.md`。

各路累计 unique超额T2 / 命中率T2（channel_audit 表①）：
- **value +1.04% / 64.0%（全路第一）**；momentum +0.75% / 49.6%；growth +0.68% / 50.0%；composite +0.58% / 53.6%；reversal +0.50% / 51.6%；heat +0.31% / 48.0%（与 momentum Jaccard 0.27 全矩阵最高重叠）；main_fund +0.17%；**accumulation −0.21%（唯一实数据负路，已裁并入 reversal_confirm，pr_20260711_001 resolved）**；healthy −0.23%（仅 4 日薄样本）。
- channel_ledger.md 边际超额T2 口径：value +1.1%、growth +0.6%、composite +0.6%、reversal +0.5%、heat/momentum +0.4%、healthy +0.2%、main_fund +0.1%、accumulation −0.2%；附 quota advisory（value 200→250 升、heat 200→150 降、main_fund 200→150 降等，人工 gate）。

## 6. retro 归因（赢家在漏斗哪层丢）

出处：`context/scan/<date>/retro/attribution.csv` `bucket` 列（retro.py 分桶）+ `retro/stage_eval.csv`。

- **14 个 retro 日 T+2 主尺累计**：**missed_l1 5004 / missed_l0 1214 / recalled_cut 882 / false_positive 3 / caught 0** → missed_l1 : recalled_cut ≈ **5.7×**（逐日 3.1×~19×，与 memory "4–9×" 同向）→ 病在召回线（L1 权重压低），L3/L4 误判是小头；**caught=0 = 至今没有一个 T+2 赢家走到 finalist**。
- 典型日：07-08 = missed_l1 340 / missed_l0 101 / recalled_cut 111；06-23 = 494 / 31 / 26。
- stage_eval 逐日：L2 lift 多在 ±0.01 内微弱（07-08 +0.003）；L4 ic_rating_t2 波动大不稳（06-22 −0.42、07-02 **+0.54**、07-08 −0.27）。memory 引用的 "L4 rank-IC T+1 +0.36 / T+5 +0.47" 即 stage_eval 2026-07-02 单日行（ic_rating_t1 0.3581 / t5 0.4655）。

## 7. L3.5 闸回测（07-11 波）

出处：`reports/gate_backtest_2026-07-11.md`（CLI `autoresearch/research/gate_backtest.py`，重放 13 日 `L3_judged_full.csv × attribution.fwd_2_oc`）+ memory `recall-gate-pinned-config-wave-20260711.md` + spec `docs/specs/2026-07-11-recall-gate-pinned-config-design.md` §3。

- **passthrough（现状全放行）：mean_fwd2 −1.48% / 命中 34%**；conviction floor **55/60/65 全部比 passthrough 更差（−1.8%~−2.3%）**；**唯 floor=70 跑赢：−1.19% / 命中 37.5% / n=40（≈3只/日）**。分 regime：trend +0.19%/55%（n20）、range −2.51%/33%（n9）、risk_off −2.62%/9%（n11）。
- 每个配置砍掉 **80–91 个 T+2 赢家**（floor=70 错杀 91 只，报告列清单：06-30 000536 conv50 fwd2 +23.0%、06-29 603078 conv46 +20.8% 等）。
- 裁决：**保 passthrough 不切闸**——只有 conviction≥70 极高确信在 T+2 有正 edge，中间 band 是噪声/反预测（确信度为 swing 校准残留）；"回测机器救了一次错误切换"，真切换=人批门。

## 8. regime 序列（最近交易日）

出处：`context/scan/<date>/meta.json`（07-02 起才有 regime 字段）+ `reports/scan/<run>/summary.md`"市场 regime"行（06-29 起才有）。市场涨跌用 `paper_nav.md` 市场等权 NAV 日差（非指数，全市场等权口径）；07-09 用 zero_buy 表 07-08 行 fwd_1_oo。

| 交易日 | regime | 市场等权日变动 |
|---|---|---|
| 06-19~06-26 | 无正式判定（regime 机制 06-27 合入；07-02 calibrate 回溯归 risk_off 11日块） | 06-22 +0.64% / 06-23 −0.11% / 06-24 −0.93% / 06-25 −1.39% / 06-26 −2.50% |
| 06-29 | risk_off（避险 breadth 27%·中位动量 −13.0%） | +0.02% |
| 06-30 | risk_off（breadth 29%·−11.7%） | +1.28% |
| 07-01 | range（震荡 breadth 32%·−11.5%） | +2.13% |
| 07-02 | range（breadth 30%·−10.5%） | −0.58% |
| 07-03 | range（breadth 32%·−7.5%） | +1.14% |
| 07-06 | risk_off（breadth 30%·−8.6%） | −1.22% |
| 07-07 | risk_off（meta.json；当日无 scan run） | −2.63% |
| 07-08 | risk_off（breadth 22-23%·−13.7%） | −1.42% |
| 07-09 | risk_off（breadth 23%·−14.6%） | ≈+0.40%（fwd_1_oo） |

最近 6 个判定日全 risk_off（07-06→07-09），之前 07-01~07-03 是 range 窗口。无独立逐日 regime 历史文件（`context/factor_lab/weights.json` 只有分 regime 权重块，无日期序列）。

## 9. pr_20260710_001 与 proposals 看板

出处：`context/knowledge/proposals.jsonl`。

- **pr_20260710_001（open，kind=factor）**：rz_buy_intensity（融资买入强度）T+2 尺重审三条全过门——**ICIR 0.134（全因子前半）、IC 两半同号（IC_h1 +0.0036 / IC_h2 +0.0149）、decile spread_t 2.11≥2 = 唯一三条全过的机构因子**。提议保守接入 L1 候选因子（进 CANDIDATES→_GROUPS 或并入 fund_main，权重由 calibrate@fwd_2_oc 自动定）；diff_sketch=tushare margin_detail 取数→scoring 归组→先影子/staging 验一轮。同批 block_intensity 两条过继续积累；hk_ratio/rz_ratio/block_premium/lhb_inst_net 未过门维持 L4 advisory。
- 其余 **open**：pr_20260624_001（小盘/北交所 cap_floor 30亿系统性漏判）、pr_20260625_001（L1 vs L4 资金口径核对）、pr_20260702_002（OW门① CMF 滞后 → main+∧OBV+ 算半共振）、pr_20260711_003（heat 缩额 200→100，等 channel_quotas 管道）。
- **resolved**：pr_20260623_001、pr_20260625_002、pr_20260711_001（accumulation 并入 reversal_confirm）、pr_20260711_002（northbound 退役→L4 advisory）。**rejected**：pr_20260702_001（horizon 之争，被 07-10 T+2 裁定取代）、pr_20260709_001（momentum quota 调整）。

## 10. L3 conviction 分布与 L4 早停

出处：`context/scan/<date>/_l3_judged.json` + `context/scan/<date>/details/*.md` 卡头。

| 日期 | n | conviction≥70 | 60–69 | 56–59 | ≤55 | min/max |
|---|---|---|---|---|---|---|
| 07-03 | 30 | 6 | 8 | 6 | 10 | 42/82 |
| 07-06 | 22 | 2 | 4 | 3 | 13 | 40/72 |
| 07-08 | 10 | 1 | 3 | 1 | 5 | 43/71 |
| 07-09 | 9 | **0** | 1 | 1 | 7 | 42/63 |

- **L4 早停分布**（卡头"〔早停·表面 DD〕"标记）：07-06 = 19 早停 + 1 满卡（深圳华强，当日最高 conv72）+ 0 复用；07-08 = 12 早停 + 1 满卡（思特威=当日买单）+ 2 复用卡；07-09 = 6 早停 + 1 满卡（思特威复核，OW→Hold）+ 4 复用卡。
- 规律：**只有 OW 倾向票走完 P4/P5 满深度，其余全在 P3 主早停闸截停**（07-08/07-09 满卡率 1/15、1/11）。

## 交叉验证到的坑（顺带发现）

1. attribution.csv 未落盘 `bought` 列（retro.py 只在内存算）→ zero_buy_ledger n_bought 全 0，0买聚合混入 06-22/07-08 两个有买日。
2. attribution 的 rating 是卡面评级非最终发布评级（06-30 胜宏 OW→skeptic 降 Hold 后仍存 OW）→ 用它数"买单"会高估（4 OW ≠ 7 真实买单）。
3. `context/scan/2026-07-09/market_pack.json` 文件头混入取数日志文本（"[L0·tushare] as-of..."），非纯 JSON，json.load 直接失败。
