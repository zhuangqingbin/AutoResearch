# 自学习系统优化方向 brainstorm(2026-07-12)

> 状态:**P0-1..7 已实施**(2026-07-12 当日落 main,plan=`docs/plans/2026-07-12-selflearning-p0-plan.md`;P0-1a 补跑复盘=运营项由 nag 浮出;P1/P2 仍待拍板)。首批真跑读数:shrink 回放=翻案率 shrunk 优5.5%/触达率平/左尾 raw 微优→默认开续攒;changelog 复活=样本足3次 Δ+0.0614 且最新 Δ≤0→C18 红灯首亮;过程分回填 355 卡。调研原稿:`docs/research/2026-07-12-selflearning-external-research.md`。


> 输入:① 仓内闭环勘察(`learning-system-survey.md`,基于源码 grep+真实数据文件,截至 2026-07-12);② 外部调研 C1-C27(`selflearning-external-research.md`,24 次检索,证据强度已标注);③ 背景纪律 = `docs/specs/2026-07-11-funnel-six-questions-brainstorm.md` §0 证据底座、病灶三("反馈饥饿+仪器坏账——能学到别买什么,学不到该买什么")、§7 不做清单。
> 纪律申明:**不重启已否决方向**(L2 上模型/预告事件路/北向召回/打板·隔日溢价/放宽三门/T+5 尺/常设买单 skeptic/向量检索·prompt A/B harness=R9);**每条提案自带数据裁决法**;n≥10/20 门槛文化保留(shrinkage 是对"注入锚"的精化,不是对"裁决门槛"的松动,见 §4-P0-3)。本稿只列方向不拍板。

---

## §1 现状一页图:闭环四环,资产在长大,断点集中在"裁决环"

```
[写侧] ──自动──▶ [存储] ──需人触发──▶ [裁决] ──自动消费──▶ [消费侧] ──▶ 下次扫描行为
  ▲                                                                        │
  └────────────────────── 新一天 attribution/gate_fires 回流 ◀──────────────┘
```

| 环 | 状态 | 一行事实(量化 + 关键证据) |
|---|---|---|
| **写侧·自动** | **闭合** | assemble 每次真实发布自动写 gate_fires/sector_calls/shadow_buys/journal(`assemble.py:741-789,1126-1140`);prelude 每日自动刷 attribution+6 账本白名单(`prelude.py:59-72,144-156`:journal/buy_ledger/cross_calib 核心/catalyst/paper_nav/watchlist);L4 派发自动写 base_rates+target_calib(`l4_card.py:672-676`) |
| **写侧·手动** | **半闭合** | lessons/proposals/feedback 只能在 feedback/scan-retro 会话手写(仅 playbook 示例代码,无 CLI 自动触发);**precedents.build_index 写侧零生产调用点**(勘察 §1,读侧 `l4_card.py:311-326` 活);lessons 仅 5 条、07-09 后零新增=写侧瓶颈仍在 |
| **存储** | **闭合(在长大)** | attribution 17 真实日;gate_fires 三门账本 tail_rate 40%/37%/42%(但 n=2-3 天);precedents.db 406 判例;shadow_buys 45 笔;target_calib n=77156 逐票观测;temperature.csv 124 日;lessons 5/feedback 5(3 open)/proposals 11(3 open)/changelog 9 |
| **裁决** | **断(执行债最重的环)** | scan-retro 诊断必须人触发:**07-07/07-08 两日 attribution 已算但从未诊断**(无 done.json,`retro.py:410-436` pending 只报警);MTM 仅带 guard 自动(`retro.py:233-265`),无 guard 永远人判;recalibrate 无 cadence(`retro.py:732-749` 纯手调);**changelog_ledger.md 停在 07-02,漏评后 5 次校准,唯一有效样本 Δ=+0.0027≈校准空转** |
| **消费侧** | **闭合(仅 1 例外)** | L4 简报 6 类注入全 presence-gated(基率行 `l4_card.py:329-361`、目标锚 `:621-644`、判例/备忘/前科);L5 报告 5 类节(lessons cap8 `assemble.py:568`、温度/纸面法庭/提案 nag/banner);weights.json 被 L1 自动消费;**唯一例外=L3 硬约束 D 静态硬编码** "trend 翻案 33%(n=52)"(`.claude/agents/l3-rank.md:29`),不读 `cross_calib.flip_stats()`(`cross_calib.py:36-69`) |

**一句话诊断**:这不是"设计了没接"的空转系统——确定性骨架全接线;病在**裁决环靠人力、且评估"学习本身有没有用"的仪器(changelog_ledger/lesson 边际收益)要么停摆要么根本不存在**。

---

## §2 断环与病理清单

### 2a. 勘察坐实的死码/零调用/欠账(执行债与接线债)

| # | 病 | 证据 | 性质 |
|---|---|---|---|
| D1 | 复盘欠账 2 日(07-07/07-08 attribution 已算未诊断);retro_input.md 写出后**无任何机制强制/提醒去读**(proposals 有 nag,retro_input 没有) | 勘察 §2.2;对照 `assemble.py:972-997` | 执行债+缺 nag |
| D2 | **坏账③未修**:attribution 记卡面评级非发布终评级——`_publish_details()` 原样 copy2,ensemble/verify 折回只改内存(`assemble.py:1005-1025,89-146`);而买单 ensemble 已同晚上线,**项目自标"首次真折回前必须完成"** | 勘察 §4;STAGES 开放线头 #6 | 高危配套缺口 |
| D3 | 4+1 个账本"存在但不会自己长大":channel/gate/zero_buy/changelog_ledger + sector_ledger 汇总,全不在 prelude 白名单,纯手动 CLI | 勘察 §1 小结 | 接线债 |
| D4 | precedents.build_index 写侧死链——db 会静默过期,406 条停在 07-09 | 勘察 §1/§3 | 接线债 |
| D5 | **同一天"是否有买单"两本账答案不同**(06-18):journal 用 `health.count_buys`,zero_buy 用 attribution `bought` 列(`journal.py:57-58` vs `zero_buy_ledger.py:32-33`) | 勘察 §3 发现 1 | 口径分叉(C24 的"实现风险"在自家账本上的实例) |
| D6 | changelog_ledger 停 07-02:**"重标定有没有用"这个元问题 9+ 天无人评估**,后 5 次校准(含 07-11 rz)从未被验;现有唯一结论="校准空转"(Δ+0.0027) | 勘察 §2.2/§3 | 学习环的学习环缺失 |
| D7 | L3 硬约束 D 静态文本 vs cross_calib 动态数据未打通(全链唯一) | 勘察 §5 | 接线债(六问 §4-6 已列未做) |
| D8 | feedback 3 条 open 自 06-25/07-04 挂起未关;lessons 5 条全 active 无一 retire、置信度 0.5-0.65 无分化 | 勘察 §2.2 | 裁决饥饿 |
| D9 | 07-11 波全部 LLM 段新功能(温度消费/L3 指纹+lint/L4 中性前提+基率行/ensemble)未经真实端到端 scan 验证 | STAGES 线头 #7 | 验收债(本稿一切 P1 的前置) |

### 2b. C21-C23 选择偏差视角下的结构性盲区(勘察看不见的病)

- **判断标签的选择偏差**:A 股的特殊幸运是 outcome 对全 universe 免费可观测(attribution 已记 missed_l1 5004 的 fwd)——recsys 那种"没推荐就没标签"的病**只部分适用**。真正的盲区在**判断标签**:L3/L4 评级只存在于每日 ~20-30 个漏斗幸存者上 → 所有评级基率(flip_stats、rating_base_rates、三门 tail_rate)都是"漏斗选择后条件分布",IPS 视角下拒绝池 propensity=0(C22),**无法回答"L4 若见到 missed_l1 的赢家会放行还是杀掉"**——而召回恰是账本坐实的第一瓶颈(missed_l1 5.7×)。
- 现有缓解全在 L2 以后:影子无门 NAV、pre_healthy、shadow_buys(finalist 内 top-3)、random_in_bucket(已提案)——**L1 前端无一随机臂**。
- **基率二值断流**:n<10 一律禁注(`write_base_rates` min_n=10、`buy_ledger.py:264-265`)=小样本信息直接弃用;C9-C12 说这正是收缩估计的教科书场景。三门 tail_rate 现在 n=2-3 天就更是如此。
- **结果分绑架复盘**(C15 "resulting"):0-3 笔/日的买侧结果是纯噪声,但复盘仪器只有结果尺(fwd_2)——过程质量(误读自见数据 22/31 这类病)没有任何结构化记分,只能靠人读卡撞见。
- **多重检验无记账**(C19):5 次真实改权重、若干 config 调整,changelog 只记事件不记 trial 计数——"最近一版看起来最好"有多大成分是运气,现在无法回答。
- **反思边际收益无监控**(C5-C8):lessons 注入(cap 8)默认有益,但 ATLAS 类证据说反思可能为负贡献;当前没有任何仪器测"某条 lesson 存在 vs 不存在的 Δ"。

---

## §3 C 弹药 → 现状逐条映射

| 弹药 | 映射到现状 | 判定 |
|---|---|---|
| C1 Reflexion(文字反思入情景记忆) | lessons.jsonl+retro 闭环即此形态 | ✅验证骨架方向 |
| C2 Voyager(经验存成可执行技能非自由文本) | misread 三谓词/lesson guard 字段/scan_config/硬门=已在走"教训→规则化"路线 | ✅验证 + ➕新动作:**guard 强制化**(§4-P1-2) |
| C3 FinMem 三层记忆分衰减 | decay_lessons+regime 域已是轻量版 | ✅验证轻量版;⛔否决建三层记忆库(自证收益撞 C8,且 R9 已禁向量检索) |
| C4 Devil's Advocate 三段反思 | L4 P4 陷阱核+ensemble 分歧人裁=事前/事中已有 | ✅验证现排布;⛔否决"自写 pre-mortem 字段"(C7:自评不可靠;skeptic 已被用户裁撤,不变相复活) |
| **C5-C8 反思的病理**(记忆自强化错误/弱模型反思有害/自评系统性失准/OOS Sharpe 衰减 51-62%) | **MTM 设计(refute −0.08>support +0.03、降级提名人批)方向完全正确**——用后续证据裁教训而非自我感觉;但"反思整体是否净正贡献"无仪器 | ✅强验证 MTM + ➕新动作:**lesson 边际收益记账**(§4-P0-5);⛔否决"多写教训=多学习"的直觉 |
| C9-C12 收缩估计/部分池化/EPP | 现状=n<10 二值禁注,信息弃用;EPP 同时警告 tail_rate n=12-20 拦次远不够精确 | ➕新动作:**shrinkage 基率**(§4-P0-3);✅验证 n≥10/20 门槛文化本身(作为最低限速保留) |
| C13 Brier 分+决策日志+颗粒更新 | journal/attribution/卡片=决策日志已全;无逐卡准确度记分 | ✅验证日志层 + ➕新动作:过程/结果分离记分(§4-P0-4) |
| C14 GJP 极端化聚合(有争议) | ensemble 现行"取中位+只向下折回"是其保守反面 | ⛔否决把 ensemble 改成向上极端化;现行设计被反衬为正确 |
| C15 Annie Duke "resulting" | 复盘现在只有结果尺;0 买日买侧无标签=病灶三原文 | ➕新动作:过程分(§4-P0-4)——**0 买日也有 10-30 张卡的过程标签,直接治反馈饥饿** |
| C16 Bridgewater Issue Log | feedback.jsonl 即此;但 3 条 open 挂 17+ 天=制度在、节奏无 | ✅验证设计 + ➕新动作:裁决 cadence 纳入 D8 清欠 |
| C17 Champion/Challenger | models/champion 框架+proposals 人批+影子漏斗=正确形状 | ✅验证 |
| C18 Walk-forward 过拟合红灯(重优化不如原版=停) | changelog_ledger 唯一结论"校准空转 Δ+0.0027"**正是这个红灯亮着没人看** | ➕新动作:C18 红灯规则写进 changelog_ledger(§4-P0-6) |
| C19 Deflated Sharpe(多重检验修正) | 5 次真实改权重无 trial 记账;proposals 裁决不考虑"试了几版" | ➕新动作:DSR-lite 记账(§4-P0-6) |
| C20 底线 30 笔+regime 多样性>笔数 | "等账本 n≥20"文化已有;但近 6 判定日全 risk_off——**单 regime 里攒够 n 也裁不动三门** | ✅验证门槛文化 + ➕新动作:裁决 checklist 加"跨 ≥2 相位"条款(§4-P0-7) |
| C21-C23 反馈循环选择偏差/common support/拒绝池跟踪 | 影子仪器全在 L2 后;判断标签只覆盖幸存者(§2b) | ➕新动作:L1 拒绝池随机臂(§4-P2-1,token 成本已估);✅同时验证既有影子无门 NAV/pre_healthy 是对的雏形 |
| C24 回测实现风险(换引擎结果分化) | journal vs zero_buy 的 06-18 矛盾=自家版本的活例 | ✅验证"单一事实源"直觉 + ➕新动作:口径统一(D5→§4-P0-1) |
| C25-C26 OPRO/TextGrad 成本高+小验证集不稳(n=5-20 量级即不稳) | 本系统日 n=10-30,天然处在文献已证不稳的量级 | ⛔**否决 prompt 自动改写回路**=R9 重申;add_prompt_patch 维持人批 |
| C27 自我修正幻觉(无外部信号纠不了自己) | thesis 数字机检回环(lint=确定性外部真值)恰好绕开此坑 | ✅验证机检回环设计;⛔否决任何"LLM 复核自己产出"式质检扩张;⛔否决把 scan-retro 诊断自动化成无人会话 |

---

## §4 优化方向候选(成本 × 证据强度 × 直击断环)

> 排序原则:先修"裁决环"的执行债与仪器(零 token、已有数据当天出读数),再动 LLM 段小改,最后是等数据的中成本件。**裁决门槛(改不改机制)仍用硬 n;注入锚(给 LLM 读的数字)才用收缩值**——两套语义不混。

### P0 · 确定性/账本级,立即可做

**P0-1 断环清欠包(纯接线+执行,一次会话可完)**
- 补跑 scan-retro 07-07/07-08;channel/gate/zero_buy/changelog_ledger 纳入 `prelude._ledgers()` 白名单(D3);precedents.build_index 挂到 assemble `is_real` 后处理(D4);journal/zero_buy 买单口径统一到 attribution `bought` 单一事实源(D5);retro_input 未读 → prelude/GATE 加 nag 行(D1,仿 `_proposals_nag`)。
- **裁决法**:run_health 新增"账本新鲜度"行——目标:复盘欠账稳态 ≤1 日、全账本 mtime 滞后 ≤1 个 scan 日、两本买单计数逐日一致。当天可验。

**P0-2 attribution 终评级(坏账③,项目自标"ensemble 首折前必须")**
- 落 `_final_ratings.json`,retro 优先 join(STAGES 线头 #6 已给修法)。
- **裁决法**:契约测试(折回卡的 retro 评级==终评级)+ 下次真实折回日人工核对一次。

**P0-3 shrinkage 基率(C9-C12,直击"基率二值断流")**
- 四个落点:`write_base_rates`(lane 翻案率/评级基率)、`flip_stats`、target_calib 的 regime×lane 细分格、gate_ledger tail_rate(现 n=2-3 天最急需)。公式:p̂=(n·p_桶+k·p_全局)/(n+k),k=10-20 起步;注入格式改"收缩值(n=X⚠)";n<3 仍绝对禁注。
- **裁决法(零 token,当天可跑)**:留一日回放——用 t-1 之前的数据分别出 raw/shrunk 估计,预测第 t 日实际(触达率/翻案率/左尾率),比 MAE;17 个真实日全回放,shrunk 不优则整体回滚(机制可逆,配置开关)。

**P0-4 过程分/结果分分离·机检版(C13-C15,直击病灶三"反馈饥饿")**
- 结果分=fwd_2(已有)。过程分=纯确定性 checklist 逐卡打分落 attribution 新列:数字机检回环通过?盲读微 pass 存在?基率/目标锚行已渲染?卡片契约 lint 通过?评级=rubric 自洽派生?slim>10KB?——全部现成仪器的布尔汇总,零 token。
- **卖点:0 买日也有全量卡的过程标签(日 n=10-30),买侧结果饥饿不再绑架复盘节奏**;retro 诊断改为优先读过程分最低的卡。
- **裁决法**:攒 n≥30 卡(2-3 个 scan 日)后算 process_score 与评级误差(评级 vs fwd_2 分位)的秩相关;历史 325 张卡可回填初读(见 §5 局限)。若与结果完全无关→重设计清单项,不加码。

**P0-5 教训边际收益记账 lesson_yield(C5-C8,给"反思有益"装证伪器)**
- 每条带 guard 的 lesson:guard 谓词对每日票池的命中集 join attribution fwd → 逐条累计"遵循该教训的反事实 Δpp"曲线 + MTM support/refute 计数,纯确定性报表(MTM 雏形已在,补的是逐条累计视图与注入席位成本视角)。
- **裁决法**:命中样本 n≥20 后,累计 Δ≤0 的 lesson 自动提名 retire(人批,沿用 MTM 降级只提名的既定纪律);若全体 lessons 合计边际 ≈0,触发 P2-2(cap 收缩实验)。

**P0-6 DSR-lite 多重检验记账(C18+C19)**
- changelog 补 trial 计数(同参数族第 N 次校准);changelog_ledger 复活(纳入 P0-1 白名单)并固定打印两行:①"该参数已试 N 版,按多重检验直觉,第 N 版需 Δ 显著大于噪声才可信";② C18 红灯行:"重标定后不如未标定版=停止调参信号,而非继续调"。不做完整 DSR 方差估计,先记账。
- **裁决法**:复活当天即可对既有 5 次真实改权重出读数;若结论仍"校准空转"→ 产出一条正式提案:recalibrate 从"诊断顺带"改为"仅 regime 切换时触发"(该提案本身按 20 交易日 cadence 人批)。

**P0-7 裁决纪律两条款(文字级)**
- proposals 裁决 checklist 加:凡三门/买侧提案,除 n≥20 外须覆盖 ≥2 温度相位(温度计 124 日回填在手,相位标签免费,C20);
- 明确"注入锚用收缩值/裁决门槛用硬 n"的双轨语义写进 retro-playbook。
- **裁决法**:纪律条款,验证=提案模板字段齐全即过。

### P1 · LLM 段小改(前置:D9——先用一次真实 scan 验收 07-11 波)

**P1-1 L3 硬约束 D 动态化**(全链唯一静态注入点,六问 §4-6 已列):cross_calib 动态生成 lane×conviction 翻案率表(用 P0-3 收缩值)替换 l3-rank.md:29 手写数字。**裁决法**:注入值与 cross_calib.md 一致性 lint;前向观察 trend 高确信翻案率是否从 33% 收敛。
**P1-2 lesson guard 强制化**(C2 Voyager 路线):playbook 改为——新教训必须带机判 guard 才能注入,无 guard 只入 note 不占 cap 席位。**裁决法**:guard 覆盖率→100%;MTM 自动判决率上升、人判积压归零。
**P1-3 retro 诊断模板微改**(C13):补"过程分最低 3 卡必读"+逐教训 Brier 式颗粒记分行。**裁决法**:retro_input.md 模板 diff+下两次复盘会话实际使用抽检。

### P2 · 等数据/中成本

**P2-1 L1 拒绝池随机对照臂·纸面卡(C21-C23)**
- 变体一(推荐先跑):**L3 表尾盲注 5 行随机票**(从过 L0 未进 L2 的池均匀抽,不标注),L3 照常精排——边际成本≈0(表已 200 行),直接测 L3 判断对拒绝池的区分度。
- 变体二(贵档):每日抽 K=2 跑 slim-only lite 卡(免 intel/免网查/不入发布,纯纸面账)。**token 成本估算**:按 07-04 实测口径(L4 段全量 ~1M 真实 tokens/15-30 卡),单卡摊 ~30-70k;K=2 ≈ +60-140k/日 ≈ L4 段 +7-15%,20 日攒 n=40。
- **裁决法**:对照臂评级分布 vs finalist 评级分布(若 OW 率同样≈0,说明门在拒绝池上同样挑剔,评级基率可无偏化);对照臂 fwd_2 vs finalist fwd_2 = 漏斗判断层增益的首个无偏估计(现只有 finalist 内 rank-IC 条件估计)。n=40 仍小(C11),只做方向读数不做改门依据。
- 诚实前提:outcome 侧 attribution 已全覆盖 universe,随机臂的**独特增量只在判断标签**——故排 P2 非 P0。

**P2-2 lessons cap 收缩实验**(C6):若 P0-5 显示合计边际 ≈0,试 cap 8→3(只留 yield 最高者)对照 20 日。**裁决法**:两窗口 L4 翻案率/误读率对比;依赖 P0-5 数据 ≥20 日。
**P2-3 shrinkage k 的经验贝叶斯估计**:桶数攒多后由数据自estimate k,替换拍脑袋 10-20。**裁决法**:同 P0-3 留一日回放,k_EB 须优于 k 固定。
**P2-4 过程分 LLM 评审版**:大概率不做(撞 C27 自评幻觉);仅当机检版与结果零相关且仍想深挖时再议,且评审信号必须来自独立外部真值。

### 不做(重申+本次新增)

- **prompt 自动改写回路(OPRO/TextGrad 式)**——C25-C27 三连否决,R9 重申;add_prompt_patch 维持人批,任何重写产物按"完整校准/retro 门槛的候选提案"走。
- **三层记忆库/向量检索**——C3 收益自证存疑(C8),R9 已裁。
- **ensemble 极端化(向上推置信)**——C14 有争议;现行"取中位只向下折回"保守方向被反衬为正确。
- **常设 skeptic 复活或自写 pre-mortem 字段**——C7 自评不可靠,用户已裁撤,ensemble 是既定替代形态。
- **scan-retro 诊断全自动化(无人会话)**——只自动化报警与备料(P0-1),判决留人:诊断的信号源本就是同一套 agent 的事后自评,C27 正中此盲区。
- **"等 n 够才看基率"的旧习惯**——被 P0-3 取代(注入侧),但硬 n 作为裁决门槛与最低限速保留,不是废除。

---

## §5 诚实局限

1. **P1 全部悬于 D9**:07-11 波的 LLM 段新功能(温度消费/L3 指纹+lint/L4 中性前提+基率行/ensemble)尚未经一次真实端到端 scan 验收(STAGES 线头 #7)——在验收前叠加新 prompt 改动会让归因不可能。P1 的真实前置是"下次真扫描"。
2. **token 成本估算粗糙**:随机臂变体二基于 07-04"真实 ~1M vs 落盘 75k"口径的均摊推算,±2× 不确定;落地前应用 telemetry/token 落稿契约实测一张 slim-only 卡再定 K。
3. **shrinkage 回放自身是小样本**:只有 17 个真实 scan 日可留一日回放,且近 6 判定日全 risk_off——k 初值 10-20 是文献惯例非本仓拟合,回放结论对 regime 迁移不稳健(C20 的警告同样适用于验证器本身)。
4. **过程分历史回填会偏乐观/缺项**:325 张历史卡契约版本混杂(v2/v3),盲读微 pass 等新机检项旧卡不存在;回填只能出"可比子集"读数,正式相关性检验以新卡 n≥30 为准。
5. **C 弹药强度不均**:C23(PRFS)单一来源、C16 单一机构案例、C14 明确有争议——映射表已随条标注,不单独作为任何 P0 的依据;所有 P0 的依据都同时有仓内账本证据。
6. **本稿未复核每个 file:line**:§1/§2 的行号继承自勘察报告(其自述基于源码直读),落地设计时需回源码确认;lessons 是否逐条带 guard 未逐条核验(勘察自述同一局限)。
7. **不下拍板**:P0 内部除 P0-2 有硬时序(ensemble 首折前)外可并行;P0-1 与 P0-2 属"已承诺欠账",其余 P0-3~7 是本稿新增议题,采纳与否留用户。
