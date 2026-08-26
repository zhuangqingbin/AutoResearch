# 现场留存 × BUY 所有权 —— 设计稿(2026-08-26,已实施)

> 状态：**已全部实施**（2026-08-26；§5 的 P0/P1/P2/P2′/P3 五批全落地，实施记录与八条偏离见 §9）。§6 的 Q1–Q5 按本稿建议裁定。两件用户点名的事：① 每个阶段的可复现现场都要留下来（为以后人肉复盘「推荐股票为什么效果不好」）；② 一直没有 BUY，集中解决。
> 本稿先把病根量出来（§1–§2），再给三条路和推荐（§3），现场留存的缺口表与机制（§4），实施顺序/验收/回滚（§5），最后是必须由用户裁的问题（§6）。所有数字可复现（§7）。

---

## 0. 一句话

**现场**：今天的 run 目录只留了漏斗的「结论」，没留「它看到的东西」——slim/市场研判/证据原件/E6 候选表/门判/复核稿/agent 规则/湖版本都不在 `reports_claude/scan/<run_id>/` 里，同一数据日重跑还会把 staging 原件盖掉；而且 08-21 闭环退役后，**没有任何东西再记录一只推荐票事后涨了跌了**。修法是「run 目录 = 完整现场（staging 可弃）+ 只记不学的结果账本 + 一条命令拉出整条推荐链路」。

**BUY**：在现行隔夜决策尺 `gap_c1_o2` 上，本系统的数据里**不存在期望显著高于交易成本的 BUY 家族**——判断层（L3/L4）显著为负、唯一正证据是确定性 `composite`（+0.14pp ≈ 一次往返成本），T+1 尾盘「追强」在四年全湖上稳定为负。L3 近两日最高 conviction 65/68（<70 = 自己都不愿真金买），L4 早停 100%。0 BUY 是这台机器的诚实答案；E6 用「相对 BUY」把答案盖住了——08-20 与 08-25 两次把 L4 提议 **SELL** 的 Underweight 卡当 BUY 出（E6 文件头「已知问题 #6」原样兑现）。要么把 BUY 的所有权交给唯一有证据的证据层、判断层只否决、再加一条 T+1 收盘执行条件（§3 路 A，隔夜尺内可做，期望 ≈ 成本，**不承诺赚钱**）；要么换到 5–10 日兑现窗口（路 B，唯一有 +1~2.6pp 证据的地方，但已三次裁定不换，本稿只列不推）；要么诚实 0 BUY（路 C）。

---

## 1. 立案证据（08-24 / 08-25 两次真跑 · 08-26 普查复跑 · 两个只读 spike）

### 1.1 E6 转正后的「BUY」长什么样

全史（`docs/research/2026-08-26-run-survey.md`：64 run / 43 数据日 / 1025 张卡）：**Buy 档从未出现过**；≥Overweight 最后一次是 2026-07-14（格力电器 000651），此后 **31 run / 26 个数据日连续 0 张 ≥OW**；早停占卡 59%（停因 资金流出 33 · 题材透支 28 · 其他 23 · 基本面恶化 22 · 涨停追高 7 · 估值透支 4）；满卡三门里「主力真在」FAIL 101 / PASS 50 最常失守；非📌 finalist 的 60 日涨幅在 L1 全集的中位分位 P87–P90（07-24 起一直如此）。E6 转正（08-19）后 5 个决策日 5 只 pick：卡面 **Hold 3 / Underweight 2**：

| 日 | run | ≥OW 卡 | 评级分布 | E6 relative BUY | 卡面 | 该卡早停/提案 |
|---|---|---|---|---|---|---|
| 08-19 | `20260819_2257` | 0 | Hold 5 / UW 3 / Sell 2 | 中国石油 601857 | Hold | —（当日硬否决 4 只） |
| 08-20 | `20260820_2200` | 0 | Hold 4 / UW 5 | 金螳螂 002081 | **Underweight** | 满卡，`FINAL TRANSACTION PROPOSAL: SELL` |
| 08-21 | `20260821_2255` | 0 | Hold 7 / UW 2 | 兴业银锡 000426 | Hold | 早停 |
| 08-24 | `20260824_2102` | 0 | Hold 6 / UW 2 | 瑞丰银行 601528 | Hold | 早停 P3「其他」 |
| 08-25 | `20260825_2149` | 0 | Hold 2 / UW 3 | 天味食品 603317 | **Underweight** | 早停 P3「资金流出」，`FINAL TRANSACTION PROPOSAL: SELL` |

08-25 的机制（`context_claude/scan/2026-08-25/_relative_buy_decision.json`）：4 只合格候选四面分全是 0.475/0.475/0.325，天味食品靠 `target_align`=0.9（composite 当日分位）夺冠；它的卡（`details/天味食品.md`）写的是「催化已落地作废 + 破位无承接 → UW，SELL」。E6 v1 硬门只挡 `research_rating == "Sell"`，UW 与「资金流出」早停都放行——`relative_buy.py` 文件尾「v1 已知问题 #6」原文：「提议卖出的票可以当相对 BUY 出这条通路是敞开的」。08-20 金螳螂同型。**这不是「没有 BUY」，是 BUY 与研究结论自相矛盾**——比 0 BUY 更误导。

影子期（08-10→08-18）另一种形状：连续 6 个决策日 `BLOCKED`，原因全是 `hard_gate.data_a×N`（全体候选被同一道日级门否决），08-19 E1a 修后才出单。

### 1.2 判断层自己也不信

| 日 | L3 judged | finalist | conviction 最高 | ≥70（=愿真金买） | L4 非📌卡 | 早停 | 停因 |
|---|---|---|---|---|---|---|---|
| 08-24 | 28 | 6 | 65 | 0 | 6 | **6/6** | 其他×4、资金流出、题材透支 |
| 08-25 | 27 | 5（含📌1） | 68 | 0 | 4 | **4/4** | 其他、资金流出×2、基本面恶化 |

`l3-rank.md` 对 conviction 的行为化定义：≥70 = 「我能说出 D+1 谁来买、且愿意明天开盘真金买入」；50–69 = 「值得 L4 深核但我不背书」。**两天没有一只 L3 背书的票**。L4 的 rubric（`l4-card.md`）：≥OW 需六维净分 ≥+2 且 OW 三门（主力真在·业绩真兑现·估值不透支）全过，且早停只向下——面对 L3 不背书的候选，P3 后 100% 早停是纪律，不是失灵。

### 1.3 08-22 漏斗形状波的 7 条活体验收（本次真跑逐条核；设计稿 §8.3）

| # | 项 | 08-24 | 08-25 | 判 |
|---|---|---|---|---|
| 1 | prelude 三段行 `lowturn 全帧→L1→L2`，L2 ≥1 | 79→52→**8** | 61→47→**8** | ✅ 生产者接通 |
| 2 | `_l3_table.md` 旗亮 ≥1；pass1 kept `lane=lowturn` ≥1 | 旗 13 行；judged lowturn 5 | 旗 14 行；judged lowturn 3 | ✅ |
| 3 | 当日 ≥9.5% 票 → `guard=chase_1d` | 无此类票，guard 空 | 同 | ✅（不硬造） |
| 4 | 任一 sector ≤3 席 | 银行 3（北京/瑞丰/江阴）| 各 1 | ✅ 恰好压线 |
| 5 | `gate_fires.csv` 无新 fail | fail 0 / warn 7 | fail 0 / warn 9 | ✅ |
| 6 | 汇总屏不再打「0买连败」 | 未打 | 未打 | ✅ |
| 7 | L4 对 lowturn finalist 的评级（只记录） | 中煤能源 Hold（早停「其他」）、招商南油 Hold（早停「题材透支」） | 无 lowturn finalist（3 只全 bench） | 记录 |

结论：低位转强路**机械上通了**（每日 8 只到 L2、1–2 只到 finalist），但它到 L4 的命运与 healthy 画像一样——Hold 早停。候选池形状打开了，BUY 没有跟着来，因为病不在形状。

### 1.4 普查复跑（`edge_census`，08-26 跑，可算日 41，扫描日 2026-06-18→08-20）

与 08-22 首读一致（`docs/research/2026-08-22-edge-census.md`），本节只摘主尺 `gap_c1_o2` 相对全湖截面中位的超额：

| 家族 | n_days | 超额 pp | t | 判读 |
|---|---:|---:|---:|---|
| L1·composite | 40 | **+0.14** | **3.05** | 正证据（唯一） |
| L1·全体 / L2·全体 | 41 / 40 | −0.00 / +0.05 | −0.00 / 1.47 | 未证 |
| L1·healthy | 30 | −0.36 | −5.47 | 显著负 |
| L3·finalist（非📌） | 40 | **−0.27** | **−3.87** | 显著负 |
| L3·conviction 55–74 | 40 | −0.40 | −3.94 | 显著负 |
| L4·评级·Hold / ·Underweight | 40 / 35 | −0.20 / −0.35 | −2.57 / −2.74 | 显著负 |
| L4·≥OW | **4** | −0.26 | −0.65 | 样本不足（40 日只出过 4 日） |
| E6·rank1 | **3** | −0.19 | −0.31 | 样本不足 |
| 📌·保送（用户自己的持仓） | 26 | **+0.65** | 0.86 | 未证（全表最好） |
| L4 评级 rank-IC（📌 剔除） | 29 | +0.14 | 1.74 | 弱正不显著 |

三门 PASS−FAIL（`_l4_rejection_readout.json`，滚动 40 日）：主力真在 **−0.22pp**（过门的更差）、业绩真兑现 +0.03、估值不透支 +0.38。三门里只有「估值」在隔夜尺上有区分力；「主力真在」反向。

### 1.5 Spike A（只读，42 个扫描日）：composite top-K 与 T+1 日内强度

`docs/research/2026-08-26-buy-owner-spikes/spike_buy_owner.py`（口径同普查：超额 = 家族均值 − 当日全湖可交易截面中位，逐日配对 t）：

| 人口 | 无条件 | T+1 收在区间上 30% | T+1 收在区间下 30% | T+1 收≤开 |
|---|---:|---:|---:|---:|
| 市场·全体 | −0.00 | **−0.22（t −3.49）** | −0.03 | −0.00 |
| L2·全体（200） | +0.06（t 1.41） | **−0.22（t −2.21）** | +0.07 | +0.06 |
| L2·composite·top20 | +0.14（t 1.96） | −0.11 | +0.17 | **+0.22（t 2.09）** |
| L0·composite·top50 | **+0.17（t 2.78）** | **−0.21（t −2.41）** | +0.21（t 2.28） | +0.21（t 2.82） |
| L0·composite·top5 | +0.10 | −0.21 | +0.44（t 2.31，n_days 28，每日 ~3 只） | +0.28 |
| L3·finalist（非📌） | −0.27（t −3.87） | **−0.50（t −4.51）** | −0.11 | −0.19 |

读法：① composite 前 20–50 名无条件 +0.11~+0.17pp，是隔夜尺上唯一稳定为正的选票口径；② **T+1 尾盘收得强的票，隔夜全体更差**（每个人口都同号），finalist 里收强的最差（−0.50）；③ 收弱子集 ≈ 市场或略正。这与卡片现在写的「入场否决 = 失守 X 放弃」方向相反——现行卡片在隔夜尺上否决的是**对的那一半**。

### 1.6 Spike B（只读，全湖四年 1086 日）：上一条在长史上稳不稳

`docs/research/2026-08-26-buy-owner-spikes/spike_overnight_4y.py`：对每个交易日 D 全湖（成交额 ≥5000 万、|涨跌|<9.5、非一字），gap = open[D+1]/close[D] − 1，条件 = D 日收盘位置：

| 条件 | 全期 超额 pp（vs 全体 −0.03） | t | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 收在区间上 30% | **−0.16（=比全体差 0.13）** | −16.0 | −0.19 | −0.16 | −0.22 | −0.07 | −0.18 |
| 收在区间上 30% ∧ 当日涨 >3% | **−0.30（差 0.27）** | −20.3 | −0.26 | −0.32 | −0.40 | −0.20 | −0.30 |
| 收>开 | −0.12（差 0.09） | −17.8 | 逐年同号 | | | | |
| 收在区间下 30% | −0.04（≈全体） | −5.0 | −0.02 | −0.01 | −0.13 | −0.05 | +0.01 |

结论：**「T+1 尾盘追强」在隔夜尺上是四年逐年同号的稳定负价值**（−0.1~−0.3pp）；「弱收盘买」不是正 alpha，只是不挨罚。这条可以直接做成执行条件（§3 路 A 的 A4）。

### 1.7 综合判定

1. 隔夜尺下，本系统数据里最好的可执行口径是「composite 前 20–50 ∧ 不追强」，期望 +0.1~+0.2pp，**与一次 A 股往返成本（印花税 0.05% + 佣金 + 滑点 ≈ 0.1–0.15%）同量级**。
2. 判断层（L3 选、L4 评级）在隔夜尺上是负贡献；它被证明有用的只有「拒绝」（估值透支门 +0.38、rank-IC 弱正）。`STAGES.md` §二早就写着「判断层已证的 edge 在拒绝不在挑选」，但 BUY 的所有权一直在判断层手里。
3. 所以「一直没有 BUY」不是某个门调紧了，而是：**用一个没有信息优势的尺（T 晚预测 T+2 开盘 vs T+1 收盘，中间隔整个 T+1 交易日）去要求判断层给出它给不出的确信**。E6 的补丁把这个结构问题盖成了「每天一只 UW 卡 BUY」。

---

## 2. 病根（三层，改哪一层都不够）

| 层 | 现状 | 证据 |
|---|---|---|
| **尺** | `gap_c1_o2`：T+1 收买 → T+2 开卖。T 晚已知的信息在 T+1 开盘就定价完毕；隔夜 gap 由 T+1 盘后信息与市场整体隔夜漂移决定 | §1.4 判断层全负、§1.6 四年全湖没有任何日内条件子集显著为正（最好的 ≈ 全体） |
| **判断层目标函数** | L3「两日内兑现机制 + 明日谁来买」、L4「六维净分 + 三门」——三门就是 healthy 画像（主力在·业绩在·估值不贵），而该画像三把尺全负 | §1.2、§1.4 三门 PASS−FAIL |
| **E6 选择函数** | 候选池 = L3 finalist（−0.27 家族）；四面里只有 `target_align`（composite 分位）有证据，其余三面零或反向；硬门只挡 Sell | §1.1、`relative_buy.py` 已知问题 #2/#6 |

08-22 波修的是候选池**形状**（低位转强/防追高/共振降权/行业帽），四批全部生效（§1.3），但形状不是病根——形状打开后进来的票照样在 L4 早停。

---

## 3. 三条路（裁决权在用户）

### 路 A ·「证据层持有 BUY，判断层只否决，T+1 收盘执行条件」（**推荐**；隔夜尺内可做）

**A1 · BUY 池换成 composite 席位。** L3 merge 加确定性守卫 **⑨ `composite_seat`**（在 ⑧ 之后）：当日 L2 菜单按 `gbdt_score`（= sector-neutral composite）取前 M（M=3，旋钮 `l3.composite_seat.m`；实施时归到 `l3` 块，因为它是 L3 的生效点），剔 📌/ST/`pct_1d≥9.5`（监管旗/质押/流动性不在这一层重做——流动性由 E6 硬门④ 的 P10 分位承接，监管/质押由 L4 的否决检查承接），**强制进 finalists**，`lane="composite"`，`guard="composite_seat"`，不受 ②`lt55` 与 ③`cap` 约束（与 📌 同级）。pass1 同步「composite 席强留」（同 lowturn 强留写法），让 l3-rank 照常判它（可 bench，判断不改席位）。证据：§1.5 composite top20/50 +0.14/+0.17，t 1.96/2.78。

**A2 · L4 对 composite 席的角色 = 否决检查。** 不改 `l4-card.md` 的 rubric、不改早停规则，卡照常出；E6 只消费三样：`research_rating ∈ {Sell, Underweight}`、`FINAL TRANSACTION PROPOSAL == SELL`、早停停因 ∈ {基本面恶化, 估值透支, 涨停追高, 数据不足} → **否决**。Hold（含早停「其他/资金流出/题材透支」）**不否决**——它们在隔夜尺上无区分力（§1.4 Hold −0.20 vs UW −0.35 差异不显著；「主力真在」反向），而 UW/SELL 否决是**产品一致性**要求（BUY 不能与卡面打架），不是统计要求。

**A3 · E6 v3.0 选择函数。** `rule_version = "e6.v3.0"`：候选池 = `lane=="composite"` 的 finalist；排序 = composite 分（`target_align`）；四面里 `recall_strength / evidence / risk_safety` 降为**记录列**不进排序（它们无证据）；硬门 = v2 四类 + A2 三条；全否决 → `BLOCKED`（原因分桶）。L3 finalist 照旧出卡（研究覆盖），但**不再是 BUY 候选**。第 2 只沿 v1 规则恒不出。`exclude_pinned` 照旧。回滚杆：`scan_config.jsonc` `relative_buy.pool = "finalists"`（v2 逐字行为）/ `"composite"`（v3）。

**A4 · T+1 收盘执行条件（机器可检）。** brief ③ 与卡片「入场否决」行改为两条结构化执行线：
```
- [执行线] T+1 pct_chg <= 3.0          → 当日涨超 3% 放弃本次尾盘入场
- [执行线] T+1 pos_in_range < 0.7       → 收在当日区间上 30% 放弃(追强四年稳定 −0.13~−0.27pp)
```
加 `buyable_c1`（未封涨停）与卡片自身价格线。系统在 T+1 晚 prelude 用日线回填 `exec_ok`（§4 结果账本），使「BUY 无条件 gap」与「BUY ∧ 执行条件满足 gap」两条都能量。证据：§1.5 + §1.6。

**A5 · 诚实呈现。** brief ③ 改为：
`✅ BUY：<名> <码> · 证据：L2 composite 第 k 名（分位 p）· L4 否决检查 通过（卡面 Hold·早停 其他）· 执行线 2 条 · 期望：相对市场 +0.1~0.2pp（≈往返成本，不承诺绝对上涨）`。`BANNED_RELATIVE_PHRASES` 照旧生效；summary 组合视角同源。

**成本**：composite 席 3 只若不在 L3 finalist 内则 +3 张卡（08-25 计量：l4-card $0.9–2.1 + l4-intel $0.45–0.86 ≈ **+$4.5/日**），日成本从 ~$27 到 ~$32。

**A 给不了什么**：隔夜赚钱。它给的是「每天一个证据链自洽、与卡面不打架、可事后计量的 BUY」，期望 ≈ 成本；20 个结果日后按 §4 账本读数决定去留。

### 路 B ·「兑现窗口 5–10 日」（已三次裁定不选：07-10 / 08-05 / 08-22(b)；**只列不推**）

唯一有正证据的地方：`fwd_5_oc/fwd_10_oc` 上 composite +1.38/+1.93pp（t 5.3/5.5）、reversal +1.19/+2.18（t 4.0/5.4）、value +1.35/+2.63、lowturn +0.63/+0.99（08-21 Gate 0）；healthy/momentum/heat 显著负。换尺意味着：`common/ruler.MAIN_RULER` 与 10 个消费点、卡模板「隔夜」措辞、L3「两日内兑现机制」改「一周内」、E6 相对事实、结果账本 horizon、用户持仓习惯——全部重锚。**若用户把「要 BUY」的优先级放在「超短」之上，这是唯一有肉的路**；本稿不替答。

### 路 C ·「诚实 0 BUY」（最小改动）

只做 A2 的硬门扩展（UW/SELL 提案/负面早停不得为 BUY），候选池不换。效果（用 08-24/08-25 决策文件手推）：08-25 合格 4 只里 UW 2 只被否，剩中国中车 / 涛涛车业两张 Hold（早停「其他」/「资金流出」，后者不在 A2 否决词表内）→ 出中国中车（分 0.475 > 0.325）；08-24 全 Hold → 出北京银行。即 C 只堵「UW/SELL 提案当 BUY」的丑，不改「Hold 早停卡当 BUY」的虚。用户的抱怨会继续。

### 推荐

**A（含 C 的硬门）**。理由：它把 BUY 交给系统里唯一有隔夜证据的口径，让判断层做它被证明会做的事（拒绝），并把执行条件写成可计量的机器行——而且**它是 §4 结果账本能立刻开始计量的对象**。若 20 日后账本说 A 的 BUY 净值 ≤0，那时再裁 B，手里已经有本系统自己的数据。

---

## 4. 现场留存（用户第 ① 件事）

### 4.1 目标

任一 `run_id` + 代码，一条命令拉出该票的完整推荐链路视图，且每个环节的**输入**与**输出**都出自 run 目录内的不可变副本；另有跨 run 的结果账本能回答「哪些推荐事后跌了」。不做自动学习、不回注 prompt、不自动改参数（08-21 裁定原样）。

### 4.2 缺口（审计口径：staging `context_claude/scan/2026-08-25/` 83 项 vs 发布 `reports_claude/scan/20260825_2149/`；复制点只有 13 处：`publisher.py:111/194/217/221/228/410–442`、`post_run.py:635–636`）

| # | 缺的东西 | 住哪 | 复盘时缺它意味着 | 机制 |
|---|---|---|---|---|
| 1 | **逐票 slim / slim_deep** | `context_claude/<TICKER>_<date>_slim*.md`（引擎根目录，不在 scan/ 下；`l4/producers.py:293`） | 卡片每个数字的来源；只剩 sha256 在 `_l4_tasks.json` | 复制到 `trace/inputs/slim/` |
| 2 | `market_view.md` / `market_pack.json` / `strategist_pack.json` | staging | L3/L4 被锚定的市场地形；只在 summary 里有片段 | 复制 |
| 3 | `L3_evidence/`、`L3_news/`（201+201 JSON）、`L3_catalyst.csv` | staging 子目录 | L3 表各列的原件；`publisher.py:190–191` 只复制 `is_file()` → 子目录全被静默跳过 | 复制 |
| 4 | `_candidate_passport.json`（299 KB） | staging | **它就是复盘产物本身**（`passport.py`：「这只票在哪一段被降级了」）| 复制 |
| 5 | `L1_channels.csv` | staging | 逐路名次唯一来源 | 复制 |
| 6 | `_relative_buy_decision.json` / `_final_ratings.json` / `_early_stop.json` / `gate_fires.csv` / `dissent_records.json` / `run_mode.json` / `degraded.json` / `_brief_sources.json` | staging | E6 候选表与决策、终评级、早停、GATE4 判据真身、降级账、brief 每格出处 | 复制 |
| 7 | `ensemble/*.run2.md` + `_ensemble_*.json` | staging | ≥OW/SELL 双复核的推理稿 | 复制（前缀不匹配 `_l3/_l4/_v_`，目录被跳过） |
| 8 | 行业 pack | `context_claude/sector/<date>/*.json`（scan/ 之外） | 行业 brief 的唯一输入 | 复制到 `trace/inputs/sector_packs/` |
| 9 | 档案（as-read） | `context_claude/knowledge/dossiers/<code>.md`，assemble 尾 `delta.record_scan_deltas` **原地改写**（300857 mtime = 该 run 的 21:49） | L4 读的是 δ 前文本，盘上已是 δ 后 | prompt 构建时记 hash + 复制到 `trace/inputs/dossiers/` |
| 10 | **agent 规则与 playbook** | `.claude/agents/*.md`、`stock-research/lite-playbook.md`、`scan-market/{SKILL,STAGES}.md`、`scan_config.jsonc`、`pinned.jsonc` | 它们就是 prompt 本体；`_l4_shared_instructions.md` 是 65 字节空骨架；run 只记 `git_sha` | 复制到 `trace/inputs/prompts/` + hash 进 run_contract |
| 11 | 脏树 | `run_contract.resolve_git_sha` 只跑 `git rev-parse HEAD`（`run_contract.py:25–37`），无 `--porcelain` | 本地改过 agent 提示词跑的 run 记成干净 sha（此刻树就有 2 个 M） | `git_dirty` + `dirty_paths` + 上条 hash |
| 12 | subagent transcript（LLM 真正的推理链） | `~/.claude/projects/<slug>/**/agent-*.jsonl`，harness 管理、可被清理 | 卡片是结论不是链；`usage_harvest` 只读它算 token | gzip 复制到 `trace/transcripts/`（仅 l3-rank/l4-card/ens_review/macro-brief/sector-brief 角色）|
| 13 | 湖版本 | `lake/`：`key=date/period` 首写冻结（PIT 安全）；**`key=static`（stock_basic/trade_cal）刷新即覆盖**；`contracts doctor --purge` 删 parquet 后重拉得到的是今天的口径 | 行业/上市日/ST 状态在过去的 run 底下悄悄变 | `run_contract.lake_manifest`（读过的 parquet 的 endpoint/key/sha256）——§5 P3 |
| 14 | 温度计行 | `context_claude/learning/temperature.csv`（共享追加，可被 calib 重算） | brief 🌡 的出处 | 复制当日行 |
| 15 | **staging 按数据日而非 run_id 键** | `workspace.scan_dir(date)`（`workspace.py:76–77`）vs run 目录 `<date>_<hhmm>` | 同一数据日重跑原地覆盖；实测 64 个已发布 run 只剩 49 个 staging；07-29 三次跑同一日，run1 的 7 个 prompt hash 已对不上盘上文件 | 靠 #1–#14 让 staging 可弃；不改键（改键要动全部生产者） |
| 16 | 发布目录可写 | `reports_claude/scan/<run_id>/` 普通权限 | 本会话 `cd` 进去时 omc hook 就往 `trace/.omc/state/` 写了 4 个文件（已清；07-12、07-31 两个 run 也有同款遗留，一并清了）；更早的实例：`20260725_1316/trace/run_health.json` 被 07-28 的一次回放**覆盖**成 cards=0（该 run 实有 11 张卡）；staging 侧 `_relative_buy_decision.json` 也被 08-13/08-18 的影子回放改写成 `buys=[688766]`（与当日 brief 的 BLOCKED 不一致）| 发布末尾写 `trace/MANIFEST.sha256` + `verify` 子命令；回放/研究仪器一律写 scratch 或 `reports_*/research/`，禁写 run 目录与 staging |
| 17 | 旧任务簿路径腐烂 | 08-11 引擎隔离前的 `_l4_tasks.json` 记裸 `context/…`（113 处） | 复盘工具按路径找会 MISSING（文件其实在 `context_claude/`） | 读侧做前缀重映射 |
| 18 | **结果** | 无。08-21 闭环退役删了 `relative_ledger/buy_ledger/retro`；E6 设计稿 U6「转正后的记分册 = relative_buy 账本本身」已无实体 | **「推荐股票效果不好」本身没人记录** | §4.4 结果账本 |

### 4.3 机制（publisher / run_contract / post_run）

- **R1 · staging 镜像**：`_publish_pipeline` 末尾整目录镜像 `context_claude/scan/<date>/` → `trace/staging/`（含子目录；跳 `_sem/` 锁与 `*.lock`），**加法**——现有 `trace/` 顶层文件与 `reasoning/` 原样保留（不动任何消费者）。规则从「白名单 9 项 + 3 个前缀」变成「staging 里有的都在」，以后新增产物不用再改复制表（同族坑：`prelude-step-two-skip-lists`）。体积 +6~7 MB/run。
- **R2 · run 外输入**：`trace/inputs/{slim,dossiers,sector_packs,prompts}/`。slim 按 `_harvest_list.txt`/任务簿路径取（含 deep）；档案在 `l4_card prompts` 落稿时复制（δ 改写之前）并把 sha256 写进 `_dossier_present.json`；prompts = 上表 #10 列出的 10 个文件原样复制（agent def 5 + lite-playbook/SKILL/STAGES 3 + scan_config/pinned 2）；温度计当日行落 `trace/inputs/temperature_row.json`。
- **R3 · run_contract 扩字段**（schema_version 2）：`git_dirty`、`dirty_paths[]`、`prompt_hashes{path: sha256}`；`contract_hash` 覆盖新字段。`run_mode` 已有「跑后可能被改所以读冻结快照」的先例（`run_mode.py:19,154`），这是把同一本能推到 prompt/slim/湖。
- **R4 · transcript**：CP7 `usage_harvest` 已定位 `agent-*.jsonl`（`usage_harvest.py:229–251`），同批 gzip 复制上表 #12 角色的文件到 `trace/transcripts/<role>-<code>.jsonl.gz`。体积待实测（估 10–20 MB/run 压缩后）——**§6 Q3 由用户裁是否保留**。
- **R5 · MANIFEST + verify**：`post_run observe` 末尾（它是最后一个写 run 目录的步骤）写 `trace/MANIFEST.sha256`；新增 `python -m autoresearch.scan.publisher verify <run_id>` 逐文件核 hash，缺/改/多都列出。
- **R6 · 结果账本（只记不学）**：见 §4.4。
- **R7 · 链路视图**：`python -m autoresearch.scan.chain_view <run_id> <code>` → markdown：护照行 → L1 逐路名次（`L1_channels`）→ L2 行（`selection_reason`）→ pass1 去留 → L3 表行 + judged 条目（thesis/mechanism/risk/conviction/finalist）→ L4 prompt/slim/intel/卡/复核稿路径与关键行（评级/早停/三门/执行线）→ E6 候选行（四面/硬门/rank）→ brief ③ 原句 → 结果（T+1 OHLC、T+2 开、gap、exec_ok、fwd_5/10）→ 当时 prompt/config 版本。零 LLM；老 run 缺文件的环节明写 `缺席`（不伪造）。
- **不做**：staging 改按 run_id 键（全部生产者都用 `ws.scan_dir(date)`；R1 让它可弃）；`chmod a-w`（post_run observe 在 publish 之后还要写）；自动学习/回注/proposal。

### 4.4 结果账本（只记不学）

- **步骤**：prelude 新步 `outcome_fill`（放 `l4_rejection` 之后；`--skip outcome_fill` 可跳；**两份 skip 清单同改**）。对每个已发布 run：若 `trace/outcome.json` 缺或不完整，且湖里已有 D+2 收盘，则按 `factor_lab.forward_returns` 同一实现算并落盘；再追加/更新跨 run 索引 `reports_claude/scan/_ledger/recommendations.csv`（按 `run_id,code` 幂等 upsert）。
- **一行一票一 run**：`run_id, analysis_date, code, name, role(BUY|finalist|bench|pinned|composite_seat), lane, conviction, rating, early_stop_reason, e6_rank, e6_eligible, e6_buy, buyable_c1, t1_open/high/low/close/pct_chg, t1_pos_in_range, exec_ok, t2_open, gap_c1_o2, rel_gap_market, rel_gap_sector, fwd_5_oc, fwd_10_oc, ruler, computed_at`。
- **口径**：与 `edge_census` 逐字同源（同一 `forward_returns`、同一 `entry_tradable`、同一 `GAP_CLIP`），两者读数可直接对表。
- **消费**：只有两个——`chain_view` 与 prelude 汇总屏一行「结果账本：BUY n 笔 · 均 gap ±x · exec_ok 内 ±y（≥20 笔才印）」。**不进 brief、不喂任何 agent、不改任何参数。**
- **与 08-21 裁定的关系**：删掉的是「从账本里学、回注 prompt、自动提案」；这里只剩「记」。判据仍是那一条「输入没人产就不留」——这次输入（推荐本身）天天在产，缺的是记录者。

---

## 5. 实施顺序 · 验收 · 回滚

| 批 | 内容 | 主要文件 | 机械验收 | 回滚 |
|---|---|---|---|---|
| **P0** 现场 | R1 镜像 + R2 run 外输入 + R3 run_contract v2 + R5 MANIFEST/verify + #17 路径重映射 | `scan/publisher.py`、`scan/run_contract.py`、`scan/l4/prompts.py`（档案 hash/复制）、`scan/post_run.py`、新 `scan/chain_view.py`（R7 一并） | 用 08-25 staging 重发布到 scratch，`trace/staging/` 文件数 = staging 文件数（减锁）；`verify` 全绿；随手改一个文件 `verify` 必红（变异）；`chain_view 20260825_2149 603317` 十段全在场 | 各函数一根旗（`publish.mirror_staging=false` 等），关掉逐字 parity |
| **P1** 账本 | R6 `outcome_fill` + 汇总屏行 | `scan/outcome.py`（新）、`scan/prelude.py`、`tests/scan/test_prelude*.py` 两份 skip 清单 | 对 06-18→08-20 回填 41 日；BUY 行数 = E6 决策文件 buys 数；与 `edge_census` E6·rank1 行逐日对表相等；`--skip outcome_fill` 时不写文件 | `--skip`；删步骤 |
| **P2** BUY（裁 A 后） | A1 守卫⑨ + pass1 强留；A2/A3 E6 v3.0；A4 执行线（卡模板 + brief + tripwire 解析器加 `[执行线]` 型）；A5 brief 文案 | `scan/l3/merge.py`、`scan/l3/triage.py`、`scan/relative_buy.py`、`scan/relative_facts.py`、`scan/brief.py`、`scan/tripwire_watch.py`、`.claude/agents/l4-card.md` + `lite-playbook.md`（执行线两行，agent def 下个 session 生效）、`scan_config.jsonc` 白名单 `relative_buy.pool/pool_m` | 08-24/08-25 真数据回放：finalists 多出 ≤3 只 `lane=composite`；E6 v3 在 08-25 **不再**选天味食品（UW 否决）；变异：去掉 UW 否决 → 08-25 回到天味食品（必红）；brief 禁词 lint 绿；`test_agent_defs` 契约同步 | `relative_buy.pool="finalists"` 一行 = v2 逐字 |
| **P2′** transcript | R4 | `trace/usage_harvest.py`、`scan/post_run.py` | 一次真跑后 `trace/transcripts/`：每个 task book 成功票至少 1 份 l4-card transcript（有复核的再 +2 份 ens_review），l3-rank / macro-brief 各 1 份，sector-brief = 当日行业数；压缩后体积记入 `_budget_observation` | 旗关 |
| **P3** 湖清单 | #13 `lake_manifest` | `data/cache.py get_or_fetch` 单点 | 一次真跑记录 ≥ 全部 A 级端点当日文件 | 旗关 |

活体验收（下一次真跑，逐条核，只记录）：① `trace/staging/` 与 `trace/inputs/` 在场且 `verify` 绿；② prelude 汇总屏「结果账本」行出现（≥20 笔前印「攒样本 n/20」）；③（裁 A 后）brief ③ 的 BUY 是 `lane=composite` 票、卡面 ≥Hold、带两条执行线；④ T+1 晚 `outcome.json` 有该票 `exec_ok`。

---

## 6. 待裁（用户）

| # | 问题 | 本稿建议 | 不选的后果 |
|---|---|---|---|
| Q1 | BUY 走 A / B / C？ | **A**（含 C 硬门）| 选 C：UW 不再当 BUY，但多数日仍是 Hold 卡当 BUY；选 B：换尺整条重锚，但那是唯一有 +1~2.6pp 证据的地方 |
| Q2 | A 的两个旋钮：composite 席 M（建议 3）；L4 否决口径是否含 Underweight（建议含）| M=3，含 UW | M 越大成本越高（每席 ≈$1.5）；不含 UW 则 08-25 那种「SELL 提案当 BUY」还会发生 |
| Q3 | transcript 是否留（R4，估 10–20 MB/run 压缩后）| 留 5 个判断角色的，gzip | 不留：复盘只有结论没有推理链 |
| Q4 | 结果账本（R6）是否建 | 建，只记不学 | 不建：第 ① 件事的「分析效果不好」没有数据源，A 的去留也无法裁 |
| Q5 | 湖清单（P3）现在做还是攒着 | 攒着（P3），P0/P1 先上 | 过去 run 的行业/ST 口径继续悄悄漂 |

---

## 7. 复现

- 普查：`uv run --no-sync python -m autoresearch.research.edge_census --out <path>`（08-26 读数已誊 `docs/research/2026-08-26-buy-owner-spikes/edge_census_20260826.md`；口径见 `docs/research/2026-08-22-edge-census.md` §0）。
- Spike A/B：`docs/research/2026-08-26-buy-owner-spikes/spike_buy_owner.py`、`spike_overnight_4y.py`（抛弃型脚本，`uv run --no-sync python <path>` 即可复跑；读数 `spike_buy_owner_readout.csv` / `spike_overnight_4y_readout.txt`；**若裁 A，A4 执行线阈值须先预注册再做成生产仪器**，勿直接抄 spike）。
- 全史 run 普查：`docs/research/2026-08-26-run-survey.md`（§1.1 的全史数字出处）。
- 08-24/08-25 活体读数：`context_claude/scan/2026-08-2{4,5}/{_prelude_summary.md,_l3_table.md,_l3_pass1_kept.csv,finalists.csv,L3_judged_full.csv,_early_stop.json,_final_ratings.json,_relative_buy_decision.json}`。
- 留存审计：`docs/research/2026-08-26-retention-audit.md`（file:line 全表 + 546 个任务簿 hash 重算的核验）。

## 8. 局限

- Spike 样本：A 是 42 个扫描日、单 regime 主导；B 是四年全湖但**无 composite**（因子帧只有扫描日才有）。两者都只支持「不追强」这一条方向，不支持任何更细的阈值调参。
- 相对超额是「相对截面中位」的读数，不是策略收益；成本估算按 A 股散户口径。
- 本稿不改主尺（三次裁定）；路 B 的全部数字来自观察尺。
- 仅供研究，非投资建议。

---

## 9. 实施记录与偏离（2026-08-26）

状态:**§5 的 P0 / P1 / P2 / P2′ / P3 全部实施完毕**(用户「开始开发吧 开发到完」;§6 Q1–Q5 按本稿建议裁定 —— Q1=A 含 C 硬门、Q2=M3 且含 UW、Q3=留、Q4=建、Q5 一并做完不再攒)。

### 9.1 落地清单

| 批 | 新增/改动 | 产物 |
|---|---|---|
| P0 | 新 `scan/retention.py`(镜像/快照/清单/transcript/湖清单)、新 `scan/chain_view.py`、`run_contract` v2、`l4/prompts._snapshot_dossiers`、`publisher` 与 `post_run` 各挂一次 `retain()` | `trace/staging/`、`trace/inputs/`、`trace/transcripts/`、`trace/lake_manifest.json`、`trace/MANIFEST.sha256` |
| P1 | 新 `scan/outcome.py` + prelude 步 `outcome_fill` + `STEP_NAMES` 单一事实源 | `$RPT/scan/_ledger/{outcome/<run_id>.json, recommendations.csv}` |
| P2 | `l3/merge` 守卫⑨ + `l3/triage` ①b + E6 `v3.0`(A2 硬门 + `pool`)+ brief ③ + `tripwire_watch` `[执行线]` + agent def/playbook + `scan_config` 两键 | `finalists.csv` 的 `guard=composite_seat`、决策文件的 `pool`/`pool_members`/`in_pool` |
| P2′ | `retention.archive_transcripts`(读 `_token_usage.json` 定位,不另写一套) | `trace/transcripts/<agent>-<file>.jsonl.gz` + `_index.json` |
| P3 | `retention.lake_manifest` / `diff_lake_manifest` | `trace/lake_manifest.json` |

新增测试:`test_retention.py`(28)、`test_chain_view.py`(8)、`test_outcome.py`(17)、`test_composite_seat.py`(23)、E6 v3 段(14)、brief v3 段(3)、prelude 三条、run_contract v2 六条。

### 9.2 与本稿的偏离（八条，全部当场决定并记录）

1. **结果账本落 `_ledger/` 而不是 `trace/outcome.json`**(§4.4 原文)。同一波刚给 run 目录立了「发布后不再变」的 MANIFEST 不变量,事后往 run 里写文件会让每个 run 的 `verify` 永远报一条 `extra` —— 等于自己把刚立的哨兵弄哑。
2. **`relative_buy.pool="finalists"` 不是 v2 逐字 parity**(§5 P2 回滚列原文说是)。它只回滚**候选池与排序**;A2 的 UW/SELL 硬门对两个池都生效 —— 那是产品一致性(BUY 不能与卡面打架),与「从哪个池选」是两件事。要连硬门一起回滚得改 `relative_buy.py` 的两个 frozenset(显式代码改动)。常量旁注与 `STAGES.md` 已按此写。
3. **transcript 体积估错一个量级**:§4.3 R4 估 10–20 MB/run,实测(2026-08-25 真跑)五个 role 原始 2.65 MB、gzip ≈49% → **约 1.3 MB/run**。既然这么便宜,把 `l4-intel` 也收了(原计划 4 个判断 role)。
4. **P3 湖清单当波做完,不攒**(§6 Q5 建议攒着)。因为实现方式换了:不动 `cache.get_or_fetch` 热路径(跨进程、风险高),改成发布收尾对**窗口内湖文件**做一次只读指纹(2781 文件 / 126 MB / **1.0 秒**)。代价是它证明「文件是不是同一批」而**不证明「当天读过」**——这句话逐字写进产物的 `note` 与测试断言里(过度声称的留痕比没有留痕更危险)。
5. **composite 席位加入 pass1 的受保护集**(§3 A1 未提)。实施时被测试逮到:`mandatory > target` 时截尾会把席位切掉,而守卫⑨ 仍会强制它进 finalists → 出现「L2 展示字段 + 空 thesis」行,正是 pinned 当年那次事故的形状。现在 `protected = pinned ∪ seat`。生产 target=40、mandatory≈18,这条永不咬人,但已用两条用例钉住(含「合法超 target」那条,免得后人当故障修)。
6. **prelude 步骤表提成 `STEP_NAMES` 模块常量**(§5 P1 只说「两份 skip 清单同改」)。改成两份测试从生产常量**派生**,加步骤不再需要改它们;同时新增 `test_step_names_inventory` 显式锁清单 —— 派生消灭的是「忘了同步」的红,不是「悄悄加了一步」的哑。这条顺手把记忆里 `prelude-step-two-skip-lists` 那个反复踩的坑关掉了。
7. **`run_contract` v2 必须保持 v1 可读**(§4.3 R3 未提)。`from_dict` 认 `{1,2}`,`_hash_payload` 按 `schema_version` 排除 v2 三键 —— 否则全部历史 run 的契约一起报 hash mismatch,`publisher` 的 manifest 与 `run_mode` 的冻结快照会同时丢掉 run 身份。
8. **席位注入放在 `write_finalists`(守卫全跑完之后、pinned 之前)而不是 `merge_l3_finalists_v3` 内部**。席位要的是 L2 的 `gbdt_score`,而 v3 只吃 judged 帧;放外面还能复用 pinned 那套「已在场只打标 / 在 judged 就整段带过来 / 都没有才建占位行」的既有形状,不动守卫链。

### 9.3 实施中被真数据推翻/确认的

- **A2 在真数据上生效**(08-24/08-25 staging 副本回放):08-25 天味食品(UW + `FINAL PROPOSAL: SELL`)被 `hard_gate.no_redflag` 否决,BUY 改中国中车(Hold);08-24 瑞丰银行(Hold)不变。与 §3 路C 的预测逐字一致。
- **`pool=composite` 回放历史日必然 BLOCKED**(`in_pool=0`)—— 那几天没有守卫⑨,席位不存在。这是**正确行为**(诚实 blocked,不静默退回全体池),但也意味着 A1/A3 的活体证据只能等下一次真跑。
- **结果账本口径与 `edge_census` 逐日对得上**:5 个 BUY 日的 `excess_med_market` 与 `edge_census.daily_stats` 的 `excess_med` 六位小数全等。
- **账本第一读就逮到一件事**:全史 6 笔 `e6_buy` 里 **4 笔是 shadow 期**,其中 08-13/08-18 两笔的决策文件是被影子回放改写出来的(brief 当天印的是 BLOCKED),且标的还是 📌 持仓 688766。所以账本加了 `mode`/`src` 两列,`ledger_line` 只数 **active** 期 —— 不分列读就是把两条假 BUY 算进战绩。
- **P0 机械验收**:08-25 staging 副本重发布 → 镜像 531 件(= staging 535 减 4 个锁/`_sem`,逐文件 diff 一致)、inputs `{slim 10, sector_packs 9, prompts 11, temperature_row 1}`、MANIFEST 664 件 `verify` 全绿;改一个字节 → `changed` 精确点名 + exit 1。run 目录 4 MB → 9.5 MB。

### 9.4 仍待下一次**真跑**才能核的（活体验收）

1. `trace/staging` + `trace/inputs` + `trace/transcripts` + `lake_manifest` 四件齐,`retention verify` 绿;
2. prelude 汇总屏出现 `outcome_fill` 行与「结果账本」读数;
3. `finalists.csv` 出现 3 行 `guard=composite_seat`,且它们在 `L3_judged_full.csv` 里**有 thesis**(证明 pass1 ①b 真的让 l3-rank 判到了);
4. brief ③ 的 BUY 是席位票、卡面 ≥Hold、带「池=composite 证据席」与两条执行线;
5. L4 卡里出现 `[执行线]` 两行(**agent def 会话启动装载,下个 session 生效**);
6. 日成本 ~$27 → ~$32(+3 张卡);
7. T+1 晚 `outcome_fill` 给当日 BUY 落上 `exec_ok`。

**20 个结果日后**按 `_ledger/recommendations.csv` 读数裁 A 的去留(§3 推荐段的承诺)——若 active BUY 净值 ≤0,那时手里已经有本系统自己的数据,再裁是否走路 B。

### 9.5 变异探针(12 条,逐条实跑)

「改完先问『把这段删掉测试会红吗』」—— 12 条一次跑完,**3 条第一轮没红**,都是真的零鉴别力:

| # | 变异 | 首轮 | 根因 / 修法 |
|---|---|---|---|
| M1 | `rating in REDFLAG_RATINGS` → `rating == "Sell"`(撤销 UW 否决) | ❌ 仍绿 | 用例那只票**同时**是 UW 且提案 SELL,两条防线各自都拦得住 → 分不开。补 `test_underweight_alone_vetoes_without_a_sell_proposal`(UW 但提案 HOLD),并断言否决理由里点名评级 |
| M2 | 删掉 `buy_pool = [… if row["in_pool"]]`(撤销池过滤) | ❌ 仍绿 | 用例里非席位票的 composite **低于**席位,删了过滤后「按 composite 排」照样给同一答案。补 `test_composite_pool_excludes_a_higher_composite_non_seat`(非席位票 composite 全场最高) |
| M7 | `gzip.compress(..., mtime=0)` → 去掉 `mtime=0` | ❌ 仍绿 | 同一秒内两次 compress 的 mtime 本来就相同 → 幂等断言测不出。改断言 gzip 头第 4–8 字节(MTIME 字段)恒零 |
| M3 | 排序退回 Borda 平均 | ✅ | |
| M4 | 席位不受 pass1 保护 | ✅ | |
| M5 | 席位不剔追高/ST/📌 | ✅ | |
| M6 | 席位不带 L3 判断过来 | ✅ | |
| M8 | 镜像 `rglob`→`glob`(子目录又被跳过) | ✅ | |
| M9 | 账本 upsert→append | ✅ | |
| M10 | v1 契约 hash 不排除 v2 键 | ✅ | |
| M11 | `ledger_line` 不分 shadow/active | ✅ | |
| M12 | `exec_ok` 不看收盘区间位置 | ✅ | |

补完三条后 12/12 全红。这一节本身也是读数:**新写的用例里有 1/4 是「看着在测、其实什么都没测」** —— 与 Wave3.5 那次运行时变异测试的比例相当。

### 9.6 既有守卫在本波逮到的四件事(它们值这个钱)

全量首跑 4 红,**没有一条是巧合**:

1. `test_no_bare_root_literals_in_source` —— `retention.py` 里为做历史路径重映射写了裸 `'context'`。修法不是加豁免,是把这个**已作废的根名**归进 `workspace.LEGACY_CONTEXT_ROOT`(该模块本就是根名的唯一事实源,包括作废的那个)。
2. `test_prelude_step_is_wired_and_reads_catalog` —— 它按**源码文本**匹配 `("news_catalog", _news_catalog)`,被 `STEP_NAMES` 重构打破。改成两条**更强**的结构断言(名字在清单里 ∧ 清单里每个名字都有实现),不是把探针调软。
3. `test_manifest_records_contract_identity` —— 写死 `schema_version == 1`。改取常量:锁的是「manifest 记的 = 代码写的」,不是「版本永远是 1」。
4. `test_relative_buy_decision_has_buy_but_brief_prints_blocked_is_fail` —— brief ③ 插了 `· 池=…`,锚点失配。顺带证实了一件事:`(N 只)` 后缀是 presence-gated 的,**老决策文件逐字无害**。
