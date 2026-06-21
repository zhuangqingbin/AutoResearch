# scan-market Agent 能力升级设计(2026-06-21)

> 姊妹篇:`2026-06-21-cost-cascade-design.md`(token/成本)。本篇管 **agent 能力**。
> 一句话:**agent 能力预算和 token 预算走同一漏斗逻辑——宽阶段(L2/L3)要便宜的鲁棒性,顶点(买点候选)才上深度多 agent 推理。**

## 0. 背景:参考"最新流行的 agent"

最该参考的是**本项目自己的上游** TradingAgents(UCLA Tauric,v0.2.4 / 2026-04-25):5 层 ~12 agent 模拟一家交易公司——分析师 ×4 → **多空辩论(Bull vs Bear)** → 交易员 → **风险三方辩论** → 组合经理(PM,5 档)→ + **反思/记忆**(把已实现收益反思注回 PM prompt)。本 fork 当初砍付费 LLM 路径时,把它最值钱的三个机制(多空辩论 / 风险辩论 / 反思记忆)一起扔了。本次**选择性零成本复活**,且只压在顶点。

通用 2026 范式对照(六件套 + 四可靠性模式):model core / memory / tools / planner / orchestration / **eval-observability**;debate / self-consistency / generator-verifier / reflexion。体检见 §3。

## 1. 现状判定:这是 workflow,不是 autonomous agent(而且是对的)

按 Anthropic「workflow(预定代码路径编排 LLM)vs agent(LLM 临场自决流程/工具)」:

| 能动性 | 现状 |
|---|---|
| 目标驱动 | ✅ 找买点 |
| 动态规划(LLM 自决控制流) | ❌ 六段漏斗写死在 `scan_pipeline`/skill |
| 工具使用 | 🟡 取数被编排,非 LLM 临场选 |
| 单次运行内 感知→行动→观察 回环 | 🟡 各阶段基本单发 |
| 多步自主 | 🟡 编排器(主线)有;子 agent 无 |
| 跨运行学习 | ✅ 闭环回路(反而先进) |
| 自我评估 | ❌ 缺(见 F) |

**结论**:结构化多步 LLM 工作流 + 编排器 + 学习回路;agentic 成分集中在「编排器动态升级 + 跨运行记忆」。对"已知漏斗的选股",偏 workflow 是对的(可审计/便宜/可复现)。**该加自主性的地方是顶点**——A(多空辩论)即在顶点注入有界自主,这是从 workflow 长成 agent 的正确切入点。

## 2. 本次落地(C → A → B,全部零付费 API = 只是多开 Claude subagent,全钉顶点)

### C · LLM-as-judge 评分卡(`rubric_rating`)——修过度多报的根因
- **病**:Sonnet 凭 gestalt 过度多报(实测 6-18:10 OW vs Opus 3 OW),撑大 Tier-2 复核量 → 级联只省 28% 的根因。
- **法**:`scan_pipeline.rubric_rating(dims, gates)`——6 维评分卡(强+1/中0/弱−1)**净分定档**(≥+4 Buy/≥+2 OW/−1~+1 Hold/≤−2 UW/≤−4 Sell)+ **3 道 OW 硬门**(主力真在/业绩真兑现/估值不透支);**任一门未过 → ≥OW 一律压 Hold**。评级**派生**自评分卡,不是拍脑袋。
- **强制**:卡片写 `**Rubric建议**` + `**Rating**` 必须等于它(否则 `**偏离**:<硬理由>`);发布层 `self_review` 新增 warn『评级超 rubric』(rating 比评分卡建议更激进且无偏离/override)。
- **双赢**:过度多报在 Tier-1 被掐 → Tier-2 候选变少(**负成本**)+ 校准更准。

### A · 买点候选多空辩论 + PM 裁判(Tier-3,复活 Bull-vs-Bear→PM)——单笔最大能力升级
- **病**:改前的对抗验证(旧 L4.5)是**单边 skeptic**——只一个角度挑刺,既可能漏真风险、也可能拿弱空头错杀好买点;且结构上只能往下压。
- **法**:每只发布买单跑一场辩论:**多头研究员**(steelman,Opus)⚔ **空头研究员**(证伪,Opus,与多头独立不互看稿)→ **主线当 PM 裁判**读两稿。PM 非另起 subagent(省 1 次 Opus/只 + 主线是天然 orchestrator)。
- **schema**:`verify.csv` 扩到 `code,verdict,bull,bear,trigger,consensus`(+bull +consensus,旧 4 列兼容)。
- **折回不变**:`降级`→降一档(踢出买单)、`否决`→至少 Hold;`assemble_scan` summary 出徽标 + 多空明细块。

### B · 3 透镜自洽共识(self-consistency)——治单样本评级方差
- **病**:同一票 Opus 复核两次结论会飘(实测菱电 Opus-vs-Opus 方差)。
- **法**:PM 裁判用 **3 透镜各投一票**——① 估值透镜 ② 资金面透镜 ③ 毁灭风险透镜;多数票定 verdict,票型记入 `consensus`(如 `降级2/3(估值/资金)`)。
- **为何和 A 绑**:单独自洽投票会固化共同偏见(三样本一起错);A 的独立多空辩论负责打破共识——A+B 合用才对。

**新顶点形态(三层级联)**:Tier-1 Sonnet 全判(C 评分卡定级)→ **Tier-2** Opus 只平反被压的高 conviction(瘦)→ **Tier-3** 买点候选(Buy/OW)多空辩论 + PM 3 透镜共识(B)→ 折回评级 + verify.csv。**买点候选只过一次 Opus(辩论)**——旧 Tier-2 单遍买点确认 + 旧 L4.5 已并入 Tier-3,去掉对最终买单的双重 Opus。

## 3. 记忆 + 可观测层(E/F,已落地)

### F · per-stage eval(`stage_eval.py`)——补『可观测』,量化每段 agent 的 edge
retro 原本只评最终 buy-list 的 T+1 命中;F 补**逐阶段归因**(2026 agent-eval 的核心:measure a
sequence of actions)。每段把 staging 的 keep/score/rating/verdict 对齐已实现 fwd 收益:
- **L2**:召回池内 keep(200)vs cut(800)fwd lift + l2_score rank-IC(T+1);
- **L3**:L2-keep 内 finalist(30)vs 落选 lift + `conviction−fragility` rank-IC(T+5);
- **L4**:五档评级**单调性** rank-IC + 分档均值(IC>0 = 越多头越涨,评级有效);
- **Tier-3**:`维持` vs `降级/否决` 的 fwd 均值差(>0 = 辩论压对了差票,**值回 Opus**)。
纯统计函数可离线自测;取数复用 `retro.realized_returns`。retro 的 `retro_input.md` 自动带这块 →
scan-retro skill 据此判断哪段该松/紧/重标定。**没有 F,A 辩论值不值无从验证**——这是 F 的意义。

### E · 记忆从情节升到检索 + 程序性(`feedback_store.py`)
- **E1·检索式**(`recent_feedback_for` + `render_calibration_block(with_feedback=True)`):把**近期同域、未蒸馏**(open)反馈注在校准块最前——补『用户 flag 到蒸馏成 lesson』之间的延迟,让刚被标错的坑**在判断当下**就避开。默认 `with_feedback=False` 输出与改前逐字一致(老路径不破)。
- **E2·程序性**(`upsert_lesson(guard=...)` + `promotion_candidates`):经验可带 `guard{field,op,value}` → 从『建议文本』升为 `self_review` 的**确定性硬门**(发布买单触发即 fail)。`self_review` 早已消费 lesson.guard、assemble 早已把 global 经验喂给它——E2 只补『让经验能带 guard』这一环,advisory→enforced 自此打通。`promotion_candidates`(反复强化 + 高 conf + 还没 guard)在 retro_input 浮出,交 skill 写 guard 落地。

**仍未做**:E1 的『按因子签名 KNN 取最相似旧错』(现是 scope 命中,非向量相似)、F 的跨日 edge 趋势累积(现单日)、Tier-2 改写前的 Tier-1 评级留存(测不到『Opus 比 Sonnet 改对没』)。

## 4. 反馈自迭代机制现状(回答"有没有")

**有半套闭环**:`feedback_store`(情节 jsonl + 语义 lesson,同 slug 反复命中自动强化)+ `retro`(复盘 T+1 实现收益 → 自动强化/退休经验 + 触发 `factor_lab` 重校准权重)+ `render_calibration_block`(注回 L2/L3)。
- **自动**:量化收益、权重重校准、经验强化/退休、注回;**+F 逐阶段 edge**(粒度补上)、**+E2 经验升确定性硬门**(advisory→enforced)、**+E1 未蒸馏反馈即时注入**。
- **还要人**:跑 retro / 给 feedback / 判断是否升经验 / 给 promotion_candidates 写 guard 的 {field,op,value}(语义,需 Claude)。
- **离全自主仅差**:触发靠人(可上 cron)、guard 的字段映射靠 skill 临场写(不可纯确定性)。粒度(F)与自改(E)已不再是缺口。

## 5. 验证

8 个 `--selftest` 全绿 + ruff clean:`scan_pipeline`(C·rubric)、`self_review`(评级超 rubric)、`assemble_scan`(多空辩论 schema/徽标 + rubric 解析)、`stage_eval`(F·binary_lift/rank_ic/verdict_edge + evaluate stub join)、`feedback_store`(E·guard 硬门 / 升门候选 / 检索式反馈)、`retro`/`uzi_lenses`/`factor_lab`(回归)。真实 6-18 staging 验证 F 四段 join 通(返回 Hold×15/OW×1/UW×13/Sell×1,印证 C 压住过度多报);真实 fwd 实现后 retro 自动出各段 edge。
