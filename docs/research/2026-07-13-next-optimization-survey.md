# scan-market 下一波优化调研(定稿)

- 日期:2026-07-13(调研员:优化调研 subagent,只读不改代码)
- 基线读数:run `reports/scan/20260712_1857`(数据日 2026-07-10;墙钟 52m36s、1 买、L3 25m35s、~133k token 落盘下界)
- 上游:`docs/specs/2026-07-12-scan-speed-perimeter-design.md`(周边提速包 P1-P7,已落 main,`.superpowers/sdd/final-review.md` 1419 绿终审 With-fixes)
- 纪律锚:超短 T+2 主尺(**不推 swing**)· 0买≠门过严(**不松门**)· L2 不用模型 · 反思可能有害(注入克制)· 不建常驻回测 harness(`replay.py` 是钦定回放器)

---

## 执行摘要(5 条线 × 优先级)

| 线 | 头号建议 | 优先级 | 预计收益 | 风险 | 可逆 |
|---|---|---|---:|---|---|
| A · L3 26m | `pass1_target` 60→40(砍 rule-④ 落刀 fill) | **P1** | L3 输入 −8~12k token + 墙钟(待测) | 顶通道名误切→漏 finalist | 一行 config / two_pass=false 全回滚 |
| B · 报告残债 | pinned §2 空「风险/催化」修复 + 关 2 条挂账反馈 | **P1**(P0 探针随 C) | 报告面瑕疵清零 | 低 | 高 |
| C · 仪器/验收 | gate4 加 `product_shape_lint`(5 条 warn) | **P0** | 5 个停车场产物病每跑可见 · ~0 token | 误报噪声(全 warn 起步) | 删函数调用 |
| D · 数据面 | anns_d 正式退役 + 停 `anns_empty_rate=1.0` 告警 | **P1** | 去伪告警 · perimeter 已省 ~3m | 低(覆盖已在) | 中 |
| E · token | 认线 A 为头号杠杆(L3 输入 = 落盘 40%) | **P1**(并入 A) | 见线 A(唯一同降落盘+真实的刀) | 同 A | 同 A |

> 一句话:**下一波的最大标的是线 A,而线 A 恰好也是线 E 的最大 token 杠杆;线 C 是最便宜的 P0(把已知的产物形状病装上探针)。** 其余都是小头或已优化。

---

## 线 A · L3 26m 降载(全程 44%,上波明确没动)

### 现状数据锚(全部实读自 07-10 产物)

- **墙钟**:`_stage_timing.json` L3精排 `wall_s=1535`(=25m35s,全程 3156s 的 48.6%)。workflow 日志自述"历史 ~14m"(840s)——**本跑比自述基线慢 83%**。⚠先查是 60 行表变大 / run-to-run variance,再断言"26m 是结构性"。
- **pass1 分诊账**(`triage_l2_for_l3`,target=60):L2-204 → kept 60 / cut 144。
  - **mandatory 必保(规则①②③)= 22 只**:pinned 4 ∪ n_channels≥3 共 6 ∪ healthy lane 15(去重后 22)。
  - **rule ④ 填充 = 38 只**:从最高 gbdt 的非 mandatory 里补满到 60。
  - **落刀税**:L2-204 里落刀票(pct60<−20 ∧ main_net<0 ∧ cmf<0)= **97 只**,其中仅 1 只落在 mandatory;**96 只非 mandatory** → rule ④ 的 38 个填充位几乎全被落刀票占据(composite lane 60-70 分段,`_l3_table.md` 亲证:001301 尚太 −31.7、301538 骏鼎达 −44.5、000659 珠海中富 −36…)。
- **L3 实际产出**(`_l3_judged.json`):只写了 **22 条判断**(7 finalist + 15 bench),恰好≈mandatory 数。**38 只 rule-④ 落刀票:0 只成 finalist、0 条独立判断**——25m35s max-effort 里约 **38/60 行是"读完即弃"的落刀税**。
- **7 只非保送 finalist 全来自 floor/healthy**:5 只严格 healthy(③:药明康德/杭叉/天士力/浙江鼎力/海德);吉比特、睿创微纳主力微负但 cmf/obv 正(L3 以"背离旗自证"入选)。**唯一非 mandatory 来路是吉比特**(momentum 通道 top,rule ④ 首轮即入,gbdt 53.5)。
- **pass1 影子已证 recall 干净**:cut 的 144 只里 **healthy 数 = 0**(唯一 loose-sniff = 士兰微 半导体 pct60 +67.5 已涨透 / main=0.0,非温和上行健康名)。→ **pass1 没把赢家切掉**,病不在 recall 而在 rule-④ 把落刀票灌进 L3。
- effort 阶梯确认:L3=`max`、L4-intel=`max`、L4-card=`xhigh`(scan-market.js:116/143/182)→ **xhigh 是 max 下一档**,max→xhigh 是真降档杠杆。

### 选项对比

| 选项 | 机制 | 预计省 | 风险 | 可逆性 |
|---|---|---:|---|---|
| **pass1_target 60→40** | 砍 rule-④ 落刀 fill 20 行 | 落盘 L3 输入 −8~12k token;墙钟按行近线性(待测,25m→?) | 吉比特类顶通道名若被误切→漏 finalist | scan_config 一行;`two_pass=false` 逐字节全回滚 |
| effort max→xhigh 影子 | 判断核少想 | 墙钟(未知量级) | 判断质量赌注;用户明护"L3 结构不动" | 一行 config |
| 表列再瘦身(31→~24) | 删冗余列 | 落盘 −5~8k token | 删错列丢判据(上波已 42→22,边际递减) | 中 |
| finalist_max 10→8 | 收 finalist 上限 | **0**(本跑 7<10,未 binding) | — | — |
| 拒绝者化重构 | L3=批准 healthy+抓例外,落刀只给计数+抽样 | 大(结构级) | 大改;失"holistic 60 比较"审计价值 | 低 |

### 推荐 + 收益/风险/验证

**证据最强的一条 = `pass1_target` 60→40**(而非 effort 降档)。理由:
1. **parity 可确定性论证**:finalist 全来自 mandatory(①②③,target 无关恒保)+ 顶通道 rule-④ 首轮(吉比特)——两者在 target=40 都保。
2. **落刀 fill 是纯读税**:38/60 行 0 picks / 0 判断,直接可测。
3. **一行 config**(`l3.pass1_target`),已有回滚杆(`two_pass=false` 逐字节 parity)。
4. **同时是线 E 头号 token 杠杆**(L3 输入占落盘 40%)。

effort max→xhigh 作**并行 P1**,但**必须在 input-shrink 证明 finalist 不变之后单独做**——否则两变量混淆,读不出哪个动了质量。

**预计收益**:L3 落盘 148208 bytes → 砍 20 candidate 行 ≈ −8~12k token;墙钟若近线性则 25m→~17-19m(⚠未验证:max-effort 思考未必随行数线性)。
**风险与回滚**:①顶通道名误切(低,rule ④ 首轮即取通道 top)→影子先验;②失"L3 看落刀→救稀有真反转"能力(小,`ls_reversal_regime` 已证 0/21 反转候选过 OW 门,即便 L3 提名 L4 门①也拦)→过渡方案:pass1 把落刀票降为**计数+top3 抽样**(拒绝者化雏形),保审计不保 38 行读入。
**验证方法**(证据攒法明确):
1. **确定性影子(零 LLM,今天即得)**:对最近 ~10 已结算日的 `L2_gbdt_top200.csv` 跑 pass1 target∈{60,45,40,35},断言 mandatory + 历史真 finalist code 在 target=40 时 **0 漏**。10 日 0 漏 → parity 立。
2. **`retro.pass1_cut_winners`(retro.py:653,现成仪器)**:把 target=40 的更大 cut 集喂现成 T+2 赢家检测,数"新切进 cut 的票里赢家数"。攒 5-10 已结算日,≈0 → 落刀 fill 确无赢家。
3. **上线真跑 1 次**:对比 `_stage_timing` L3精排 `wall_s`(本跑 1535)与 L3 输入落盘字节(本跑 148208)。

---

## 线 B · 报告质量残债(summary.md 可见)

### 现状数据锚

1. **pinned 4 只 §2 空「风险:;催化:」**(summary L141-144:同花顺/华友钴业/联影医疗/宁德时代)。但 `_l3_judged.json` bench 有它们 thesis(119-167 字)——**L3 判过,§2 render 却拉空的 risk/cat 子字段**(bench 条目 thesis 有、risk/catalyst 未单独填)。memory `[pinned-l3-discarded]` 记"pinned L3 判断在 merge 处被丢弃",修复"落待验"。
2. **fb_20260704 两条 process 反馈未决**:`_001`(token 太大)+ `_002`(报告质量/15-30 卡重复击杀·L4 结论列 48 字符)。
3. **效能表口径**:本跑 summary 表仍写"session/Opus·low/medium"(旧渲染);final-review 证 P6 已修列读 `user_config_echo.json`(strategist high / sector sonnet·high / l4_card xhigh)——**本跑在 P6 修复前,下跑起自动对**。
4. **观察单 7 条**:深圳华强触发叙事单行 ~40 中文句(极密),信息价值高(红队反挖 75 亿对外担保)但可读性差。

### 推荐 + 收益/风险/验证

- **P0(随线 C)**:把"pinned §2 风险/催化非空"作 product-shape **warn**——同时**验证"落待验"的修复**:修好则不亮,仍空则逮住。零成本探针。
- **P1**:让 L3 对 pinned/bench 也填 risk/catalyst(或 §2 对 pinned 回退取 judged thesis 首句),消空字段。收益:报告面清零;风险:低。
- **P1**:**关闭 fb_20260704 两条**——token(线 A/E 已答:L3 输入是头号,方向已定)、质量(重复击杀 = 主力微盘失真,已有 `main_dist` 反号/微量旗;结论列长度是渲染参数)。retro 时标 resolved,别继续挂 20 日提醒。
- **P2(停车)**:观察单密度——保留内容仅换行分段,低优先。
- **效能表口径本跑自愈**(P6 已落),无需动作,下跑验收即可。

---

## 线 C · 仪器/验收自动化 —— 最小 gate4 扩展(P0)

### 现状数据锚

- `gates.py:97` gate4 = 仅在 `gate_fires.csv` 有 `severity=fail` 时挡(读 self_review 落的表)。
- `self_review.review(ctx)` 已产 fail/warn,且已有 `card_contract_lint` / `intel_future_dates_lint` 两个**附加 lint 函数**追加进 failures(assemble.py:809/814 调用点)。
- `run_health.json` 已现成算好:`anns_empty_rate`、`l4_phases`(n_full/n_earlystop/p4_flips)、`degraded_fields`、`nan_rates`。
- 停车场 5 条产物形状断言全无仪器:pinned thesis 非空 / force_full_card 生效数 / intel 稿数=派发数 / anns_empty_rate<1 / market_view 防锚定 grep(**= final-review I-1 未闭合的策略师泄漏通道**)。
- `force_full_card`(l4_card.py:597)判据 = conv≥70 ∧ channels≥4;本跑 `l4_phases.n_full=1`(=药明康德的买单满卡,非 force 触发——它 n_channels=3 不满足 ch≥4)→ **force_full 本跑大概率 0 生效,静默无痕**(memory FN-1 第五修:该网自建成零调用点)。

### 设计:新增 `product_shape_lint(scan_dir, date)`(self_review.py,同 card_contract_lint pattern)

| # | assert | 读源 | severity 起步 | 逮什么 |
|---|---|---|---|---|
| 1 | 每 pinned finalist §2 风险/催化(或 judged thesis)非空 | staging + judged.json | **warn** | 本跑 4/4 空;验"落待验"修复(线 B) |
| 2 | force_full 判据命中票 ∈ n_full,否则显式"本日 0 命中" | run_health.l4_phases + judged conv/channels | **warn** | 探"零生效"病,防静默 |
| 3 | intel enabled 时 `_l4_intel_*.md` 文件数 == dispatch 数(减 reuse) | glob + gate2 名单 | **warn** | intel 首跑防线(final-review item-4) |
| 4 | anns_empty_rate==1.0 → 明置 "expected/no-permission" 非告警 | run_health.anns_empty_rate | **info** | 去伪告警(见线 D) |
| 5 | `market_view.md` 不含 top3 行业标签 grep | market_view.md | **warn** | **闭合 final-review I-1**(spec §4.5 grep 覆盖不到 market_view) |

- **放哪**:`self_review.py` 新函数,`assemble._self_review_banner` 追加(与 card_contract_lint 同点)。
- **advisory vs fail**:**全部先 warn**。理由:产物形状是"报告瑕疵"非"正确性硬门",fail 会挡发布。攒 3-5 跑证无误报后,把 assert 1(pinned 空 thesis)与 assert 3(intel 稿数 mismatch)**升 fail**(指向真断线)——遵循项目"advisory→enforced 实测过再升"惯例(memory `[scan-agent-upgrade]` E2)。
- **收益**:近零 token(纯 assert)+ 几秒墙钟;5 个停车场产物病变成每跑可见。**风险**:误报噪声 → 全 warn 起步 + 逐条实测。**回滚**:删函数调用。
- **验证**:下次真跑看 `gate_fires.csv` 新 5 行 severity/detail;人工核对 pinned 空 thesis 是否被逮(若修复已生效则 assert 1 不亮 = 反证修复)。

---

## 线 D · 数据面(anns / hk_ratio / degraded_fields)

### 现状数据锚

- `contracts.py:115` `anns_d = TIER_DEGRADE(note="信息披露公告:已知无权限,anns_empty_rate=1.0")`;run_health `anns_empty_rate=1.0`。
- **但催化/公告覆盖已被多端点承担**(全 TIER_DEGRADE 但有数据):`stock_news_em`(个股新闻)+ `forecast`/`express`(预告/快报)+ `stk_holdertrade`(增减持)+ `repurchase`(回购)+ `stk_surv`(机构调研);**l4-intel 站(sonnet·max)P3/P4 还做 live 公告正文盲搜**。→ anns_d 边际唯一价值 = 结构化公告标题流,**已被 news_em 头条 + intel live 双重覆盖**。
- perimeter P2 已把 `harvest_l3_news` 对 anns_d 做权限性 fast-fail(省 ~3m 注定失败退避)——**功能缺口已在编排层堵上**。
- `hk_ratio` 0.92 NaN + degraded_fields=[hk_ratio]:northbound n=0(本日无北向额度数据)= **正确/良性**,非 bug;presence-gated 已对。
- Minor-1(终审):`forecast`/`express` 是"某日无=真实空"却记 B 级降级 → 未来某日无预告即入 degraded_fields。**本跑 degraded_fields=[hk_ratio] 仅 1 条,未灌噪**(风险未兑现)。

### 选项对比 + 推荐

| 选项 | 成本 | ROI |
|---|---|---|
| tushare anns_d 权限升级(积分) | 付费 | **低**(覆盖已在) |
| akshare 巨潮替代(`stock_zh_a_disclosure_report_cninfo` 等,keyless) | +1 源 +1 contract | 中(补结构化标题喂 cat 列) |
| **正式退役 anns_d + 停告警** | ~0 | **高**(去伪告警) |

- **推荐 P1 = 退役为主**:停止把 `anns_empty_rate=1.0` 当健康告警(它是"expected 无权限"非"今天坏了"),run_health 该字段改注 "anns: n/a(no-permission·covered by news_em+intel)"(= 线 C assert 4)。**akshare cninfo 作可选实验**:仅当 retro 显示 `cat` 催化列信号不足(前向 IC 门)再补——现无证据缺,别先加源。
- **Minor-1 修**(P1,低成本):run_health.degraded_fields 区分"无权限/报错降级"(hk_ratio/anns)vs"合法空"(forecast/express 0 行 on valid trade day),后者从告警面过滤(= final-review Minor T1)。下次真跑先 eyeball `degraded.json` 是否被合法空 B 日灌爆,再决定是否动手。
- **收益**:去伪告警,run_health 不再每跑亮一条"已知无权限";**风险**:退役后若某日真需公告正文,回落 intel live(已在)。**验证**:下跑 run_health `anns` 字段读数 + degraded_fields 是否只剩真降级。

---

## 线 E · token 经济

### 现状数据锚(效能表 + 落盘实测)

- 落盘下界 **~133494 token**。单项拆解(bytes/2.8):
  - **L3 输入表 148208 B = 52931 token(占 40%,最大单项)**
  - L4 slim 109522 B = 39115 · L4 卡 99786 B = 35637 · L4 prompts 51100 B(11×4645)· 行业brief 13723 · 策略师 2548
- 真实量级 ~1M(memory:落盘 75k vs 真实 1M)——落盘是下界,真实计费只有 `/usage` 准。
- **prompt cache 已修**:l4_card.py:772 实现"固定标头共享前缀 ≤300B + 逐卡标题移到共享块之后"(byte-identical 契约测试锁)——旧 memory `[token-economy-p0-wave]` 的"L4 dispatch cache 全 miss"病已闭环。**L4 派卡 cache 现应命中,不再是大头,别重复优化**。

### 推荐 + 收益/风险/验证

- **认线 A 为 token 头号杠杆**:L3 输入 = 落盘 40%,pass1 60→40 砍 20 candidate 行 ≈ **−8~12k 落盘 token + 按比例 off 真实读入**——**唯一同时降落盘与真实 token 且零判断质量代价的刀**(落刀行本被弃)。
- **其余不单独立项**:cache 已优化;slim 109522/11≈10KB avg(非 NO_DATA-heavy 本跑,--slim 已轻取);卡模板 4645 B/卡已精。
- **intel 增量诚实标注**:11×sonnet·max 盲搜 = 新增未计 token 沉,首跑 OTEL/`/usage` 实测前**别预估**;`max_queries=15` 是护栏,>8m 再拧(perimeter P4③)。
- **收益/风险/回滚**:同线 A。**诚实**:落盘 133k 优化对真实 ~1M 计费是次要;真压 token 只能从"L3 读多少行"下手 = 回到线 A。
- **验证**:线 A 上线后 `/usage` 对比(真实)+ 效能表 L3 落盘字节(下界)。

---

## 优先级汇总 + 不确定性诚实标注

**P0(下次真跑前值得做)**
- 线 C:`product_shape_lint` 5 条 warn 装上 gate4(含 final-review I-1 的 market_view grep + pinned 空 thesis 探针)。最便宜、闭合最多停车场。

**P1(攒证据后做)**
- 线 A / 线 E:`pass1_target` 60→40——先确定性影子(10 日 0 漏)+ `pass1_cut_winners`(5-10 日 ≈0)再上;effort max→xhigh 作 A/B 但排在 input-shrink 之后。
- 线 B:pinned §2 空字段修复 + 关 fb_20260704 两条。
- 线 D:anns_d 退役 + 停告警;degraded_fields 合法空过滤(Minor-1);akshare cninfo 仅作条件实验。

**P2(停车)**
- 线 A:拒绝者化全重构(等 input-shrink 读数)。
- 线 B:观察单密度。
- 线 E:prompt-cache 深度审计(需先有 `/usage` 真实读数)。

**诚实标注的不确定性**
1. **L3 墙钟 vs 行数线性未验证**:max-effort 思考未必随行数线性;25m→~17-19m 是乐观外推,以真跑为准。
2. **本跑 L3 1535s vs 自述 ~14m 基线差 83%**:先分清 run-to-run variance / 表变大,再谈"26m 是否结构性"——别把一次可能偏高的读数当基线。
3. **吉比特 target=40 存活**:高信心(顶通道 rule-④ 首轮)但未真跑,靠确定性影子先验。
4. **effort max→xhigh 质量影响未知**:是赌注;用户明护 L3 结构,须 finalist-不变证明在先。
5. **pass1 shrink 失"稀有反转救援"**:coverage 小损,靠 0/21 OW-门史兜底;过渡方案(计数+抽样)可补审计。
6. **落盘 token 是真实 ~1M 的部分代理**:优化落盘≠等比优化计费。

**尊重的既有裁定**:全程未推 swing(T+2 主尺)· 未提松门(0买≠门过严,线 A 只动 pass1 输入不碰 OW 门)· L2 保持确定性分层(未引模型)· 注入克制(线 C 只加产物形状 assert,不加方向注入)· 无常驻 harness(验证走 `replay.py` + 确定性影子 + retro 现成仪器)。
