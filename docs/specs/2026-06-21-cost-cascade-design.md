# scan-market 成本级联 + 买单对抗验证 — 设计

> 2026-06-21。对症两实测问题:**① token/$ 大**(L3/L4 静默吃 Opus)**② 买点质量没有终局红队闸**。
> 上游与外部佐证:hierarchical「frontier orchestrator + budget workers」≈ 97.7% 全 frontier 准确率 @ ~61% 成本;多模型路由常态省 30-60%(高扇出链更高)。

## 1 · 背景:漏斗窄了数据,没窄模型

六段漏斗把**数据**从 ~5500 收敛到 ~30,但 **L3(判 200)与 L4(判 30)都静默继承 Opus**——最宽的 LLM 阶段跑最贵的模型,正好反了。

实测单轮 token(占比):

| 阶段 | LLM | 现模型 | 扇出 | token 占比 | $ 占比* |
|---|---|---|---|---|---|
| L0/L1/L2a | ✗ | — | — | 0 | 0 |
| L2b 双赛道 | ✓ | Sonnet ✓ | 4 | ~13% | ~3% |
| **L3 精排** | ✓ | **Opus ⚠** | ~10 | ~22% | ~30% |
| **L4 研究** | ✓ | **Opus ⚠** | ~30 | ~62% | ~62% |
| L5 整合 | ✗ | — | — | 0 | 0 |

\*Opus 输出 ≈ Sonnet 5×,故 **L3+L4-on-Opus 吃 ~90% 的钱**,而 L4 判的票大多注定 Sell。

被数据证伪的两个直觉:lite-playbook 仅 ~700 token(re-read 30× 可忽略)、卡片输出仅 250-350 字(已紧)。**唯一的漏点是 30 张 Opus 深研 + 10 个 Opus L3 批判**,不是上下文膨胀。

## 2 · 目标 / 非目标

**目标**:① Opus 仅用在漏斗顶尖(可能进买单的票 + 终局买单红队);宽阶段降到 Sonnet。② 终局买单加 LLM 对抗验证闸。③ 整轮 $ 降 ~60-70%,**不丢** L4「反向打脸 L3」的尽调能力。
**非目标**:不改 L0/L1/L2(L2 v2 已优化)、不改因子/召回、不引付费 LLM API(硬约束)、不做日频增量缓存(留后续 spec)。

## 3 · 设计 A:成本级联(把 Opus 收敛到刀刃)

把模型曲线弯成跟漏斗一致——**越宽越便宜,只在顶尖上 Opus**:

| 阶段 | 旧 | 新 | 理由 |
|---|---|---|---|
| L2b | Sonnet | Sonnet(不动) | 已优化 |
| **L3 精排(200→30)** | Opus | **Sonnet** | 规则化判断 + 校准注入 + `trap_signals` 机械底;trend_quota/hybrid 合并是确定性代码,与模型无关 |
| **L4 Tier-1(全 30)** | Opus×30 | **Sonnet,3 只/子代理(30→~10)** | 宽筛;「抛物线顶 PE160+CFO负+解禁→Sell」是 rubric 套用非深推理,Sonnet 胜任;批处理省重复子代理前导 |
| **L4 Tier-2(买点候选 ~6-10)** | — | **Opus,逐只** | 只对 Tier-1 评级 ∈ {Buy, Overweight} 的票上 frontier 模型复核(同 slim 证据,确认/降级) |
| L5 | — | 不动 | 确定性 |

**关键不变量**:Tier-1 Sonnet 仍带完整尽调 rubric(trap 信号 / 估值纪律 / 抛物线顶→压级),保住「L4 反向打脸 L3」。只有**评级已 ≥ Overweight 的买点候选**才值得 Opus 二次确认。

**旋钮(默认已选,可调)**:
- **K1 Tier-2 深度**:默认 = **Opus 复跑 lite 卡(同 slim 证据)**——有界、可复现。想要 Tier-2 拉 live 证据(新闻/DCF/席位/10日资金)→ 切到**全量 analyze-ticker**(更贵更有 edge);"下重注单跑全量"那条铁律保留(playbook L95)。
- **K2 Tier-2 门槛**:默认 = 评级 ∈ {Buy, Overweight};可放宽到 + R:R≥2 的 Hold。

## 4 · 设计 B:买单对抗验证(L4.5 红队闸)

L4 后,**发布买单**(评级 ≥ Overweight,~4 只)每只派一个 **Opus skeptic 子代理**,目标是**证伪买点**(deep-research adversarial-verify 模式,scoped 到最贵的决策点):

- 攻击面:估值证伪(PE/PEG 分位)、解禁/质押压顶、主力背离(承接消失)、业绩雷(预告/快报/应收存货)、**前视偏差**(证据是否严格 point-in-time)、筹码派发。
- 输出:`verdict ∈ {维持, 降级, 否决}` + 一条最强空头论点 + 触发位。命中实锤 → 卡片降级或附 bear-case 后再发布;喂 `feedback_store`。
- 与既有 `self_review` 机械硬门(winner>88 无 override / 覆盖 / 评级-因子矛盾 / 行业集中 / 空泛)**叠加**:一个是确定性红线,一个是 LLM 红队。

**为何高 ROI**:名字少(~4)→ 近乎免费;错一个买点 = 真金白银。这是把级联省下的预算**重投到买点准确度**。

## 5 · Token 预算 before/after

| | Opus 子代理数 | Sonnet 子代理数 | 说明 |
|---|---|---|---|
| 旧 | ~40(L3 10 + L4 30) | 4(L2b) | Opus 判全程 |
| 新 | **~12**(Tier-2 ~8 + verify ~4) | **~24**(L2b 4 + L3 10 + L4t1 10) | Opus 仅顶尖 |

Opus 子代理 40→~12(−70%),且新 Opus 调用是小 lite 卡/单票红队(非批判)。叠加 L3+L4t1 降 Sonnet,整轮 **$ 降 ~60-70%**,与外部 hierarchical 基准一致。

## 6 · 改动清单

- `scan_pipeline.py`:`parse_ratings_from_details(details_dir)`(复用项目 `parse_rating`)→ {code: rating};`pick_buy_candidates(ratings, thresh)`(Tier-2 名单);`pick_buylist(ratings)`(verify 名单);`batch_finalists(df, size=3)`(Tier-1 批)。+ selftest。
- `screening-playbook.md`:漏斗一图 + token 注 + L3(→Sonnet)+ L4(→tiered)重写 + 新增「L4.5 买单对抗验证」节。
- `SKILL.md`:阶段表 / token 注同步。
- `assemble_scan.py`:`_archive_reasoning` 扩 `_l4_tier2_*` / `_v_*` 到 `reasoning/l4/`、`reasoning/verify/`;summary ③ buy-list 带 verify verdict 徽标(✅维持 / ⚠️降级 / 🛑否决)。
- `docs/plans/2026-06-21-cost-cascade-plan.md`:实施计划。

## 7 · 风险与缓解

| 风险 | 缓解 |
|---|---|
| Sonnet L4 漏掉 Opus 才抓得到的尽调反转 | Tier-1 带完整 rubric;**所有 ≥Overweight 买点候选必过 Tier-2 Opus 复核**——漏的只可能是被低估的 Sell→Buy,概率极低且 Tier-2 会捞 |
| 虚假节约(Tier-2/verify 把省下的又花回去) | Tier-2 同 slim 证据(不 live 重取)、verify 单票;Opus 调用上限 ~12,实测仍 −70% |
| 批处理 3 只/子代理串味(卡片互相污染) | 卡片本独立;prompt 明确「逐只独立判,不交叉引用」;有疑虑可降到 1-2 只/批 |
| verify 与 self_review 重复 | 二者正交:self_review=确定性红线机械门,verify=LLM 找新 bear 证据;summary 同时呈现 |

## 8 · 验收

- 7 个 selftest 全绿 + ruff 通过;新 `scan_pipeline` 选择器有 selftest。
- 重跑 6-18:买单名单与 v2 基线**评级方向一致**(级联不改结论,只改成本),且每个买单带 verify verdict。
- A_pipeline 出 `reasoning/l4/`(tier2)+ `reasoning/verify/` 归档。
- 主线报告标注:各 Opus 调用 ≤ ~12;无付费 LLM API。
