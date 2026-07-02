# scan L4 token 经济(卡片 TTL 复用 / 菜单感知预算 / L3 增量表 / 稳定性抽检)设计

日期:2026-07-02 ｜ 分支:feat/scan-l4-economy ｜ 状态:实施中

## 动机(真数据)

token 大头 = L4 每日 ~25-30 张 Opus 卡。实测(run_health 07-01):早停率仅 20%(5/25)=
早停省得有限;finalist 逐日重叠 16%(4/25,紫光国微窗口更高)= 重复研究同一批票;
0 买六连的日子每天照烧全额卡。三个确定性杠杆:

## 1. 卡片 TTL 复用(`autoresearch/scan/l4_reuse.py`,新)

`reuse_decision(code, scan_dir, max_age_days=4, price_tol=0.05, conv_max=70)`:
**全部条件都过才复用**(条件缺数据 → 保守不复用,除公告一项):
- 有前卡(近日 details/<code>.md,取最近)且**前卡不是复用卡**(禁链式复用防陈旧);
- 前卡评级 ∈ {Hold, Underweight, Sell}(**≥OW 永不复用**——买点必须重研);
- 日历天龄 ≤ max_age_days(默认 4,覆盖周末);
- |Δclose| ≤ price_tol(两日 L1/L2 staging 都有价才可比;无价 → 不复用);
- 今日 L3 conviction < conv_max(强先验值得重研,对齐 force_full_card 精神);
- regime 未翻(两日 meta.regime 都非空且不同 → 不复用;任一空 → 跳过该检);
- 无新公告(今日 L3_news/<code>.json 里 ann_date > 前卡日 → 不复用;
  **文件缺 → 放行**,依价格门兜底——公告冲击通常带价,诚实注明)。

`write_reused_card` 拷前卡 + 顶部 ♻️ banner(源日期/Δ价/失效条件;不覆盖已存在的今日卡);
`reuse_pass(scan_dir, apply=False)` 对全部 finalists 出决策表;CLI:
`python -m autoresearch.scan.l4_reuse <date> [--apply]`(默认 dry-run)。
**编排**:SKILL L4 步派发前先跑 reuse pass,复用票不派 subagent。
`health.l4_phase_stats` 已识别 ♻️ 卡(n_reused)→ 省了多少可测。

## 2. 菜单感知 L4 预算(`menu.l4_budget`)

`l4_budget(scan_dir, base=30, floor=12) -> (n, rationale)`:三旗
(落刀>60% / 健康涨≤2 只 / regime==risk_off);0 旗 = base、1 旗 = 3/4、≥2 旗 = 1/2(不低于 floor)。
**在最不可能出买单的日子少烧一半 Opus**;机会成本红队 + 观察单兜底防错过。只降不升(不通胀)。
L2/meta 缺 → (base, parity 注)。`python -m autoresearch.scan.menu <date>` 打印体检 + 预算;
L3 合并时 `merge_l3_finalists_v2(judged, target=预算)`。

## 3. L3 增量表(`l3_table_md(date, delta=False, shuffle_seed=None)`)

默认逐字 parity。`delta=True`:找前一 L3 现场(L3_judged_full+L2),**略去
"昨判弃 ∧ 今无变化"** 的行(变化 = |Δcomposite|>2 ∨ |Δpct_60d|>2 ∨ 今日 lhb/预告/快报证据;
prev 缺值 = 视为变);保留行加 `prev_l3` 列(选/弃)。表头注明略去数 + 重新入场条件 +
**防锚定令**(今日仍须独立重新比较)+ 全量表每 ≤5 个 scan 日至少 1 次。无前日 → 回退全量。

## 4. 稳定性抽检(`stability_overlap` + shuffle_seed)

`shuffle_seed` 确定性乱序表行(同 seed 同输出);周频用乱序表跑第二个 L3 audit agent,
`stability_overlap(正选, 乱序选)` <70% → 记 proposal(rubric 太松/噪声大)。

## 5. 前缀对齐(playbook,文档)

L4 一条消息并发派发时:每个 subagent prompt = **[稳定共享块:playbook 摘录+market_view]
在前 + [个股块:简报] 在后**,最大化 prompt cache 命中。

## 不做
- 买单 skeptic 条件化(最后防线,不为省 token 动);
- 复用卡链式续期(TTL 从原研日起算,过期必重研);
- P4 条件化(等 l4_phase_stats 的 P4 翻盘率积累,先测量后动刀)。

## 测试
test_l4_reuse.py(复用通过/各否决路径/写卡/不覆盖)、menu 预算(test_menu_health.py 增)、
test_l3_delta.py(parity/过滤/标记/回退/乱序/overlap)。合成 fixture,无网络。
