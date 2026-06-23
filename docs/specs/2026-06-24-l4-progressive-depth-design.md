# scan-market L4 渐进深度 + 单 Opus subagent — 设计

> 2026-06-24。承接 `2026-06-21-cost-cascade-design.md`(三层成本级联)的迭代。
> 母文档:CLAUDE.md L4 节、scan-market `SKILL.md`、`analyze-ticker-lite` skill。

**Goal**:把 L4 从「Tier-1 Sonnet 全判 → Tier-2 Opus 平反 → Tier-3 Opus 辩论」三层两模型,改为 **一只 finalist = 一个 Opus subagent**,在 `analyze-ticker-lite` 内部做 **渐进深度 DD + 早停**——读够真数据才判,判断不好就停、不再深挖。**省 token 靠早停跳过深核 + 精雕,质量靠全程 Opus + 读真数据(非漏斗简报)判定。**

**Architecture**:删掉三层选择器(`batch_finalists` / `pick_downgrade_reviews` / 单遍 `pick_buy_candidates` 辩论编排)。L4 = 主线对 `finalists.csv` 每只发一个 `Agent(model='opus')`,跑改造后的 lite skill:**P0 漏斗简报定向 → P1–P3 表面 DD(读真数据)→【主早停②】→ P4 陷阱核 →【击杀③】→ P5 满卡终判 →(可选)买单独立 skeptic**。早停点建立在**已读的真数据**上,漏斗简报只负责把 DD 导向「验证 L3 的理由」。

**Tech Stack**:Claude Opus(本 session,零付费 LLM API)做 subagent;`autoresearch/scan/agents/l4_card.py`(确定性 helper:漏斗简报组装 + `rubric_rating` + 评级解析 + 买单名单);`autoresearch.scan.assemble`(发布);pytest。

## Global Constraints

- **零付费 LLM API**:subagent 由本 session 的 Claude 扮演,数据层走免费工具(tushare/yfinance/akshare/keyless)。
- **确定性层零 LLM**:漏斗简报组装、评级解析、买单名单、发布全是 pandas/确定性。
- **数字 grounded**:卡里每个数字可回溯到——① 漏斗简报里 L1/L2/L3 算出的真值,或 ② subagent 读过的 slim 块。**未读的块(尤其陷阱维)不许引用数字、不许编**;早停卡把未核维明写「未核·需深挖」。
- **早停只向下**:早停只能把票停在 ≤Hold;**任何 Rating ≥ Overweight 必须走完 P4 陷阱核 + P5**,绝不在早停点发买单(安全地板,结构性)。
- **防误杀铁律**:**永远不在读到「翻盘牌」之前早停**。翻盘牌 = 催化(新闻/事件)、forward 估值(fwd PE 远低于 TTM)、资金回流(吸筹)。⇒ 最早的安全主早停点 = **P3 之后**(催化 + forward 都读过)。
- **单测零网络**:确定性 helper 喂合成 fixture;不在单测里起 subagent / 联网。
- **发布可解析**:每张卡(早停卡 / 满卡)都含 `**Rating**`(五档)+ `FINAL TRANSACTION PROPOSAL`,否则 `parse_rating` / `assemble` 读不到。

---

## 1 · 背景:三层级联诊断错了成本

实测两份报告(06-22、06-23)的 token 表 + 落盘时间戳:

- **三个 Tier 不是成本**:06-23 Tier-2 + Tier-3 合计 = **4 次 Opus 调用、~9K 输出、~5 分钟**。砍 Tier 省的是噪声。
- **L4 真成本 = Tier-1 那「一遍非分层」全判**:29 张满卡,每张读一份 ~13KB slim。29 × 13KB ≈ **~150–180K 输入**(summary 自标「输入侧才是大头」)。而 06-23 这 29 张满卡 → **0 个买入**——花八成钱确认「今天没东西买」。
- **Tier-2 是 Sonnet 的脚手架**:`pick_downgrade_reviews` 存在的唯一理由是 base 用 Sonnet 会误杀高 conviction 票。base 换 Opus → Tier-2 蒸发。
- **Tier-3 的对抗不是脚手架**:它是真尽调闸(06-23 抓住 000933 周期顶陷阱),保留——但作为 survivor 的深核 + 买单红队,不再是独立一层。

**结论**:把模型曲线弯成跟「判断深度」一致——**每只一个 Opus,判断不好就早停、不深挖;只有看着像买点的才深核**。复杂度从「三层两模型」降到「一个 subagent + 渐进深度」。

## 2 · 目标 / 非目标

**目标**:① L4 简化为「一只 = 一个 Opus subagent」,删三层选择器。② subagent 内渐进深度 + 早停,跳过拒绝带的深核 + 精雕。③ 全程 Opus 质量(无 Sonnet 误杀/过度多报)。④ 早停建立在已读真数据上,**不误杀**。⑤ 保住「L4 反向打脸 L3」+ 买单对抗。

**非目标**:不改 L0/L1/L2(召回/GBDT)、不改因子、不引付费 LLM API。**不在本期 tier 化 harvest**(slim 仍一次性按 finalist 取;「surface/deep 懒加载取数」留给方向②`slim 瘦身` 单独 spec)——本期省的是 LLM 侧(读得少 + 写得少 + 早停),不是取数侧。

## 3 · 架构:单 Opus subagent + 渐进深度 DD

主线编排(`screening-playbook.md`):对 `finalists.csv` 每只,组装**漏斗简报**并发一个 `Agent(model='opus')` 跑改造后的 `analyze-ticker-lite`。**这 ~29 个 subagent 在一条消息里并发派发**(非顺序)。每个 subagent 内部分阶段:

| 阶段 | 读什么(真数据) | 回答 / 动作 | 填评分卡维 |
|---|---|---|---|
| **P0 定向** | 漏斗简报(见 §4) | 选它的理由是什么?要证伪的前提哪条 | 建立假设(不判) |
| **P1 现状核** | 快照 + 主力净/cmf_20/obv_mom_20 + 量价形态(吸筹/派发) + 筹码 winner | L3 的资金/技术前提真在吗?吸筹还是派发?超卖还是高位 | 技术·资金 |
| **P2 价值核** | np_yoy/rev_yoy/roe + pe/pb + **fwd PE(keyless)** + A股财报趋势 | 真便宜真成长?还是账面增长/只 TTM 便宜 | 基本面 + 估值 |
| **P3 催化核** | 近 14 天新闻 + 业绩预告/快报 + 日历 + 卖方目标 | 有没有带日期的前瞻催化?还是「只超跌无催化」 | 催化 |
| **— 主早停 ② —** | (此时翻盘牌全翻开) | **4 表面维加不起买点 → 出早停卡,跳 P4/P5** | — |
| **P4 陷阱核**(深) | 盈利质量 CFO/NI + 偿付 净债/质押/商誉 + 整张利润表 + **周期顶/正常化** | 是不是雷?CFO 负?高质押?周期顶盈利幻觉 | 盈利质量 + 偿付 |
| **— 击杀 ③ —** | (survivor) | **陷阱命中 → 降级/否决买点** | — |
| **P5 终判** | (上述已足)+ WebSearch 补催化日期 | 三档 EV/R:R + 预期差 + 多空自压 + 认错位 → 满卡 | 终评级 |

**漏斗简报把 DD 变成「验证 L3 的论点」**,而非冷启分析:subagent 带着假设去证伪 L3 选它的具体理由,DD 更快、早停更稳(不是冷判,是「选它的理由崩没崩」)。

**渐进读盘(input 省的前提)**:早停要省 input,subagent 就**不能一次把 13KB slim 读全**,得逐阶段读(`Read` 按块/offset)。为可靠,slim 重排成 **表面块在前(P1–P3:快照/资金/量价/财报/估值/fwd PE/新闻/日历/卖方)、深核块在后(P4:整张利润表/盈利质量/偿付)**,中间插一行分界标记 `<!-- P4 深核分界(早停在此之前 return) -->`。subagent **读到分界为止**做 P1–P3;主早停②触发就停笔(分界后一行未读);survivor 才继续读分界后。**这是块重排 + 标记,不是 harvest tier 化/懒加载**(§2 非目标仍成立:取数仍一次性全取)。

## 4 · 漏斗简报(P0 输入,确定性组装)

新增确定性 helper `compose_funnel_brief(code, scan_dir) -> str`:按 finalist 代码,从既有漏斗产物拼一段紧凑 markdown 简报,**前置到该票 slim context 顶部**(subagent 自顶向下读:先简报定向,再逐阶段读 slim 块)。内容:

- **L1 召回画像**:`recall_channels`(命中哪些队列)、`n_channels`(命中几路)、`best_rank`、`composite` + 9 子分(momentum/fund_main/fund_retail/chip/north/tech/growth/value/volprice)。
- **L1 关键原始因子**(给评分卡先验):`np_yoy`/`rev_yoy`/`roe`、`pe`/`pb`/`dv_ratio`、`main_net_ratio`/`main_inflow_yi`/`cmf_20`/`obv_mom_20`、`winner_rate`/`chip_concentration`/`price_to_cost`、`rsi`/`ma_bull`/`pct_60d`、`hk_ratio`。
- **L2**:GBDT/champion 分。
- **L3**:`conviction`、`thesis`、`risk`、`catalyst`、`lane`、`sentiment`、`lenses`。

**定位**:简报只**定向 + 给先验**,不作早停依据(信息太薄,据此判 = 误杀)。subagent 用它知道「该重点核哪条」,真正的判定来自 P1–P5 读到的 slim 真数据。

> 漏斗天然缺两维:**盈利质量(CFO/FCF)+ 偿付(净债/质押/商誉)** L1 不带 → 它们只在 P4 读 slim 时填。这正好定义早停边界:**漏斗 + 表面 DD 够「否决」(4 维),但「确认买点」必须深核 2 个陷阱维**。

## 5 · 早停点 + 防误杀 + 评分卡映射

**三个候选早停点**(越往后越省得少但越不误杀):

- **① P1 后(极端狗票快速通道)——默认 OFF,文档化逃生口**。仅当漏斗简报已显示「无催化」+ P1 实读确认「资金决绝派发(main_net_ratio<0 且 obv_mom_20<0)或高位(winner>85 且非超卖)」+ 简报 np_yoy 深负三者**同时**成立才停。**默认关闭**(尊重防误杀:未读催化/forward);需要再调开,可调阈值。
- **② P3 后(主早停)⭐ 默认开**。读完现状 + 价值 + 催化——**所有翻盘牌已翻开**。4 表面维(技术资金/基本面/估值/催化)加不起买点(资金中性/不便宜/无催化/基本面平)→ **否决安全**(没有未读项能翻盘)→ 出早停卡,跳 P4 整张利润表 forensics + P5 三档建模/深 WebSearch。06-23 的 ~23 只 Hold/UW/Sell 多停在这。
- **③ P4 后(击杀买点)——保质非省钱**。只有 P3 后看着像买点的进 P4。陷阱命中(CFO 负 / 高质押 / 商誉雷 / 周期顶)→ 降级/否决。**例:000933** P1–P3 全过(PE9.3+np223+主力净+3 路共振)→ P4 周期顶核 → ROE 59.6→17.3 正常化 PE 翻 19x → 击杀。

**防误杀铁律**(Global Constraints 已列):最早安全主早停 = P3 后。

**评分卡映射**(对齐现有 `rubric_rating`):
- **否决只需 4 表面维**(技术资金/基本面/估值/催化)= P1–P3 → 加不起买点就停;2 陷阱维标「未核」。
- **确认买点才需 6 维齐全**(+ 盈利质量/偿付)= P4。陷阱维是**买点否决项**,只对 survivor 核。
- 流程:**填 4 表面维 → 不够买点 → 早停(跳 P4/P5);够 → 读陷阱维(P4)+ 建模(P5)确认**。

## 6 · 卡模板(两种落点同 §10 现状不变)

**A. 早停卡(②/① 触发,~0.5–0.8K 输出,零深 WebSearch)**

```
# 决策卡 — <代码> <名称> @ <date>  ·  〔早停·表面 DD〕

## 决策仪表盘
| 评级 | 现价 | 时间框架 | 触发位 | 置信度 |
|---|---|---|---|---|
| **<五档>** | <价> | <月> | <减/清条件> | <高/中/低> |

## 维度评分卡(表面 4 维 + 陷阱 2 维标未核)
| 维度 | 评分 | 一句话依据(简报/已读 slim 数字) |
|---|---|---|
| 基本面 | 强/中/弱 | np_yoy/rev_yoy/roe |
| 估值 | 强/中/弱 | pe / fwd PE |
| 技术·资金 | 强/中/弱 | 主力净 + 量价(吸筹/派发) |
| 催化 | 强/中/弱 | 下一闸门 / 无 |
| 盈利质量 | **未核** | 需深挖(早停未读 CFO) |
| 偿付(爆雷) | **未核** | 需深挖(早停未读 质押/商誉) |

**Rubric建议**: 表面 4 维净分 <±n>/4 ｜ 早停因:<≤20字 为何此点否决·翻盘牌已翻开> → **建议 <Rating ≤ Hold>**
**Rating**: <Hold|Underweight|Sell> ← 必须 = Rubric建议
**一行多空**: 多 <…> ｜ 空 <…>
FINAL TRANSACTION PROPOSAL: **<HOLD|SELL>**
置信度: <高/中/低> ｜ _早停于 P<1|3>:表面 DD 判定非买点,未做深核;Claude 推理产出,仅供研究,非投资建议。_
```

**B. 满卡(survivor,§10 不变)**:沿用现 `lite-playbook.md` 全卡(仪表盘 + 6 维评分卡 + Rubric + Rating + 三档 EV/R:R + 预期差 + 多空对撞〔P5 强制空头压测〕+ 催化&认错位 + A股富化行 + proposal)。

**Grounded 纪律**:早停卡数字只引简报 L1/L2/L3 真值 + 已读 slim 块;陷阱维写「未核」不写数字。满卡数字出自读过的 slim 块。

## 7 · 买点对抗 + 安全地板

- **P5 自压**(每个 survivor,subagent 内):多空对撞强制先写最强 bear case + 「什么情况下我就错了」,评级须扛住才维持 ≥OW。
- **买单独立 skeptic(默认开,保留旧 L4.5)**:对最终 Rating ≥ Overweight 的发布买单(~0–4 只),主线**另起一个 Opus** 专职证伪 → `verify.csv`(`维持/降级/否决` + 最强空头 + 触发位)。独立挑战者比自压更抗自我合理化;只在真买单触发,近乎免费。**注**:这不是「tier」(不对全 29 铺一层),是发布前红队。
- **安全地板**:Rating ≥ OW 一律走完 P4 + P5;早停只能 ≤Hold。漏买点结构上堵死。

## 8 · 编排改动(`l4_card.py` + playbook)

- **删**:`batch_finalists`(不再批 3,改 1 只/subagent)、`pick_downgrade_reviews`(无 Tier-2 平反)。
- **改**:`pick_buy_candidates` 语义 → 「最终 ≥OW → 买单 skeptic 名单」(原是 Tier-3 辩论名单,合并到买单红队)。
- **留**:`rubric_rating`(P1–P3 表面 4 维 + P4 补齐 6 维的确定性锚)、`parse_ratings_from_details`、`pick_buylist`。
- **加**:`compose_funnel_brief(code, scan_dir) -> str`(§4)。
- **harvest slim**:`autoresearch/analyze/harvest.py` 的 slim 渲染**块重排**(表面块 P1–P3 在前、深核块 P4 在后)+ 插 `<!-- P4 深核分界 -->` 标记(支持渐进读盘;**非 tier 化**,取数仍一次全取)。
- **playbook**:`analyze-ticker-lite/lite-playbook.md` 重写为 §3 阶段流 + §5 早停点 + §6 两卡模板 + 防误杀铁律;`SKILL.md` 流程同步(渐进深度 + 早停)。`scan-market/SKILL.md` + `screening-playbook.md` L4 节重写(1 Opus/finalist + 简报组装 + 删三层 + 留买单 skeptic)。
- **assemble**:`_archive_reasoning` 适配(早停卡 vs 满卡都在 `details/`;`_v_*` 仍归 `reasoning/verify/`);buy-list 渲染带 skeptic verdict 徽标(已有)。

## 9 · 成本(诚实 before/after)

**不是相对 Sonnet 级联的 token 净省**:宽筛从 Sonnet 抬到 Opus,即使早停,**大致与今天打平、略贵(~1.3–1.5×)**。早停的作用是把「29 只全 Opus 满卡」(~3–5× 灾难)压回打平区间。

| | 旧(Sonnet 级联) | 新(Opus 单 subagent + 早停) |
|---|---|---|
| 宽筛(~29) | Tier-1 Sonnet 满卡 ×29(读 13KB + 写满卡) | 每只 Opus;~23 只 P3 早停(读表面块 ~60% slim + 写 ~0.5K 早停卡) |
| 平反 | Tier-2 Opus ~2 | 蒸发 |
| 买点对抗 | Tier-3 Opus ~2 | survivor P4/P5 自压 + ≥OW 独立 skeptic ~0–4 |
| 模型质量 | Sonnet 宽判(过度多报 3×,靠 Opus 补) | 全程 Opus |

**换到的是**:① 架构极简(无 tier/选择器/批);② 全程 Opus 质量;③ 大概率更快(29 并发,多数 P3 早停)。**早停卡省的两笔**:跳 P4 整张利润表 forensics 读入 + 跳 P5 三档建模/深 WebSearch 输出。

## 10 · 文件结构

- **Modify** `autoresearch/scan/agents/l4_card.py`:删 `batch_finalists`/`pick_downgrade_reviews`;改 `pick_buy_candidates`→买单 skeptic 名单;加 `compose_funnel_brief`。
- **Modify** `autoresearch/analyze/harvest.py`:slim 块重排(表面前/深核后)+ 插 `<!-- P4 深核分界 -->` 标记。
- **Modify** `.claude/skills/analyze-ticker-lite/lite-playbook.md`:重写为阶段流 + 早停 + 两卡模板。
- **Modify** `.claude/skills/analyze-ticker-lite/SKILL.md`:流程同步。
- **Modify** `.claude/skills/scan-market/SKILL.md` + `screening-playbook.md`:L4 节重写。
- **Modify** `autoresearch/scan/assemble.py`(或 assemble 包):归档/渲染适配(早停卡 + skeptic)。
- **Modify** `tests/scan/test_agents.py`:删函数的测试移除;加 `compose_funnel_brief` 测试 + `pick_buy_candidates` 新语义测试。

## 11 · 测试策略(TDD,零网络)

- `compose_funnel_brief`:喂合成 L1_recall / L2 / finalists fixture → 断言简报含 channels/n_channels/子分/关键因子/L3 thesis;缺列降级(用 NaN/缺省占位,不抛)。
- `pick_buy_candidates`(新语义):ratings dict → 只回 ≥OW 的码(买单 skeptic 名单)。
- `rubric_rating`:补「表面 4 维」调用路径(陷阱维缺省=中)→ 断言早停档位;6 维齐全路径不变。
- 删 `batch_finalists`/`pick_downgrade_reviews` → 移除其 selftest,确认无引用残留(grep)。
- 卡可解析:早停卡模板字符串过 `parse_rating` → 命中五档 + `FINAL TRANSACTION PROPOSAL`。

## 12 · 风险与缓解

| 风险 | 缓解 |
|---|---|
| 早停误杀(据薄信息停) | **铁律:P3 前不停**(翻盘牌全翻开才主早停);①默认 OFF;早停只向下 |
| 全 Opus 比 Sonnet 级联贵 | 早停把灾难性 5× 压回 ~1.3–1.5×;换简单 + 质量 + 速度,§9 已诚实标注 |
| 买点 survivor 漏陷阱 | P4 陷阱核 + ≥OW 必走 P5 + 独立 skeptic 三道;000933 回归测试方向一致 |
| 早停卡引用未读数字(编) | 模板把陷阱维写「未核」;Grounded 纪律;`self_review` 抓「评级超 rubric」 |
| 漏斗简报缺列/旧产物 | `compose_funnel_brief` 缺列降级占位;简报只定向不判,缺失不致命 |
| subagent 把简报当判据(误杀回潮) | playbook 显式:简报=定向+先验,判定必来自 P1–P5 读到的真数据 |
| input 省不到(subagent 一次读全 slim) | slim 表面块在前 + `<!-- P4 深核分界 -->` 标记;playbook 命令「读到分界为止做 P1–P3」,主早停②停笔则分界后不读,survivor 才读后半 |

## 13 · 非目标 / YAGNI

- 不 tier 化 harvest(surface/deep 懒加载取数)→ 方向②单独 spec。
- 不做日频增量缓存。
- ① 极端狗票快速通道默认 OFF,不调参不展开。
- 买单 skeptic 不重取 live 证据(同 slim + WebSearch 定性);要全量证据链 → 单独跑 analyze-ticker 全量。

## 14 · 自检

- **占位**:无 TBD。
- **一致**:`compose_funnel_brief` / `pick_buy_candidates`(新语义)/ `rubric_rating` 三处签名贯穿 §4/§8/§10/§11;早停点 ①②③ 与 §5/§6 模板一致(②=P3 主早停默认开,①默认 OFF,③=P4 击杀)。
- **范围**:单 plan 可实现(1 helper 新增 + 2 删 + 1 改 + 4 文档/playbook 重写 + 测试);harvest tier 化明确划走。
- **歧义**:早停只向下、P3 前不停、≥OW 必走 P4+P5——均写死为铁律;漏斗简报「定向不判」写死。
- **防误杀**:贯穿 Global Constraints / §5 / §12——最早安全主早停 = P3 后。
