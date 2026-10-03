# 2026-10-03 研究质量 × 防漂移 × token 复盘 —— 证据原件

主稿：[`../2026-10-03-research-drift-token-review-brainstorm.md`](../2026-10-03-research-drift-token-review-brainstorm.md)。本目录只放**外部调研原件**与**只读探针**；探针不进生产、不是新的普查族，输出一律落 gitignored 的
`context_claude/development/20261003-research-review/`（可用环境变量 `PROBE_OUT` 改）。

## 外部调研（2026-10-03 当日联网；每条断言带 `[FETCHED]` / `[SNIPPET]` / `[MEMORY]` 标签）

| 文件 | 内容 |
|---|---|
| `W1_finance_research.md` | LLM 交易 agent 的严格评测、前视/记忆偏差、LLM 在权益研究里被证实的能力边界、A 股隔夜/日内结构、LLM 判断稳定性、低频策略的统计功效 |
| `W2_agent_engineering.md` | agent 漂移的定义/检测/预防、eval 驱动开发、effort/缓存/级联/上下文工程的省 token 证据、「同决策更低成本」的等价检验方法 |
| `W3_claude_code_docs.md` | Claude Code / Claude API 官方文档逐条引文：别名解析与钉版、effort 语义、headless 参数、缓存 TTL 与并发、subagent 上下文、2026-10-03 当日牌价 |

`[SNIPPET]` 与 `[MEMORY]` 条目未打开原文核对，对外引用前须复核。

## 探针（全部从仓库根目录运行，零 LLM、零网络、只读 `reports_claude/` 与 `lake/`）

```bash
for p in docs/research/2026-10-03-research-drift-token-review/probes/p*.py; do
  uv run --no-sync python "$p"
done
```

| 脚本 | 回答的问题 | 主稿节 |
|---|---|---|
| `p01_ledger_readout.py` | 账本按 role / 评级 / 停因 / T+1 区间位的主尺读数；全部 E6 BUY 明细 | §2、§3.6 |
| `p02_stage_rulers.py` | `stage_rulers.csv` 的 ALL 行与逐日汇总 | §3.1 |
| `p03_e6_eligible_vs_vetoed.py` | L4 放行 vs 否决在主尺上的日配对差；市场各腿均值（账本窗） | §3.6 |
| `p04_pinned_rating_flips.py` | 持仓卡连续两场评级翻转率 | §3.8 |
| `p05_cards_parse.py` | 全部已发布卡的正文长度、评级、停因、入场三态（写 `cards.csv`） | §2、§3.6 |
| `p06_mechanism_verdict.py` | 「兑现机制」裁决行：✓ 的极性不固定 → 不可机读；08-05 以来逐行列出 | §3.6 |
| `p07_intel_yield.py` | 情报的 T0/24h 产出率（写 `intel.csv`，依赖 p05） | §3.6、§6 |
| `p08_intel_polarity_overnight.py` / `p09_intel_polarity_legs.py` | 情报正负面 × 隔夜 / T+1 开盘缺口 / T+1 日内 | §3.6 |
| `p10_tokens_by_role.py` | 32 场 `token_usage.md` 按 role × model × effort 聚合 | §5.1、§6 |
| `p11_reprice_official.py` | 用 10-03 官方牌价重算（仓内价表把 5.5 按子串当成 5 计价） | §6 |
| `p12_thinking_share.py` | 09-29 子 agent 可见输出字符 vs 计费输出 token（机器相关：读 `~/.claude` transcript） | §6 |
| `p13_population_ic.py` | 账本 `populations/` 的逐日截面秩相关：composite / l3_conviction / n_channels | §3.2、§3.5 |
| `p14_universe_composite_ic.py` | 全市场 composite 的主尺 IC、十分位、分时期 | §3.4 |
| `p15_factor_ic_pref_replay.py` | 十组子分与热度特征的主尺 IC；**按现行偏好权重回放 composite**；健康集内热度五分位 | §3.3、§3.4 |
| `p16_long_history_heat_execline.py` | 4.5 年（1112 日）EOD 代理：热度 × 隔夜、执行线内外、健康集内热度（写 `long_panel.parquet`，约 230MB） | §3.3 |
| `p17_long_history_legs.py` | 4.5 年五条 1–2 日持有腿的市场均值、因子 IC、分桶 | §3.3 |
| `p18_best_deterministic_set.py` | 隔夜腿上确定性组合能做到的最好水平；执行线内外的「日均 vs 合并」口径差（依赖 p16） | §3.3 |
| `p19_pref_menu_live_check.py` | 回放 composite 与生产值的一致性；偏好档上线后两个成熟日的实盘 IC；L2 池与 finalists 的热度分位 | §3.4 |
| `p20_stage_contrasts_ex_pinned.py` | 剔除 📌 后的阶段日配对差（finalist vs bench / vs L2 其余） | §3.5 |
| `p21_cc_versions.py` | 每场扫描会话的 Claude Code 版本与子 agent 实际模型（机器相关） | §5.1 |
| `p22_scenario_parse.py` | 满卡「三档情景」行能否机读计分（78 张里固定格式只解析出 10 张）；概率是否接近固定先验 | §3.6 |

`p11` 的 10-02 行需要先生成该会话的用量表：
`uv run --no-sync python -m autoresearch.trace.usage_harvest --session 05ed57b4-bb8a-42cb-8d8f-64b0265c23fc --out context_claude/development/20261003-research-review/usage_1002.md`（缺文件时该行自动跳过）。

## 读探针结果前必须知道的四件事

1. **p16–p18 是日线 EOD 代理**：T+1 收盘价买入、T+2 开盘价卖出都按日线成交价算，未扣成本、未建模集合竞价成交概率；T+1 收盘涨停的票已剔除（买不到），T+2 开盘跌停卖不出**未**处理。
2. **`lake/daily` 有 330 份窄表**（2025-02-18 → 2026-08-20，缺 `pre_close` / `vol` / `change`；325 份写于 2026-06-22，5 份写于 8 月）、`lake/daily_basic` 有 145 份窄表（缺 `trade_date`/`turnover_rate_f`/`circ_mv`）。探针按文件名取日期、用 `close/(1+pct_chg/100)` 回推前收、统一用 `turnover_rate` 与 `total_mv`。第一版加载器遇到窄表静默跳过，2025 年几乎整年丢失且 `shift` 错位——读数差别不大，但这个坑值得记。
3. **p15 的偏好 composite 是回放值**（Σ 权重 × 子分，不含过热惩罚 −8 / 吸筹加成 +5）；与偏好档上线后三场的生产 composite 秩相关 0.990–0.993（p19-a）。
4. **p06 前半段的收益差是伪信号**：把「✓」当成「机制成立」是语义误读，保留只为复现「该字段极性不固定」。
