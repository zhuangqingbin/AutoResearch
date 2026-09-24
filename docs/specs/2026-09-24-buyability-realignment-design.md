# 可买性对齐设计（2026-09-24）：把「为什么不可买」从漏斗里解掉

> **性质**：设计稿，用户 2026-09-24 逐条批准的四条裁定的落地方案；零实施。实施计划另立（writing-plans）。
> **上游**：`docs/specs/2026-09-24-recommendation-quality-review-brainstorm.md`（复盘读数）。本稿只写「改什么、改在哪、怎么验、怎么回滚」，读数不再重复。
> **用户裁定（2026-09-24）**：① 保留「成功交易日 ≥1 只 BUY」授权；② 停止召回层按隔夜尺的 IC 校准；③ 上涨板块侧是否接进召回由 Claude 决定 → **决定：接，做成 L2 行业席位**（§2.3）；④ 零可买日不跳过 L3/L4，把「为什么不可买」当问题解掉。
> **不动的裁定**：主尺 `gap_c1_o2`；不提 swing；learning 层不重开（无自动重标定）；L4 复用不恢复；「不要跌势票」是产品偏好；E6 是唯一 BUY owner；`scan_config.jsonc` 是唯一参数事实源（新旋钮三件套 = 白名单 + 消费点 + `tests/scan/test_config_knobs.py`）；双引擎隔离、修改两边同修。

---

## 0. 一句话

**三堵墙按序拆：召回权重换成偏好档 → L2 加落刀帽、健康 floor、行业席位 → 卡片写机读入场行、E6 池回到全部派发卡并分 A/R 两级 → brief 印不可买归因。** 成功尺只有一把：改造后 10 个成功扫描日里，A 级 BUY（卡面允许入场）天数 ≥ 5。

---

## 1. 现场事实（实施时按这些 `file:line` 下手；写稿时逐条核过）

| # | 事实 | 出处 |
|---|---|---|
| F1 | `factor_lab.calibrate` 在 `autoresearch/scan/` 无生产调用者；`context_claude/factor_lab/weights.json` mtime 2026-08-19，`meta.horizon=gap_c1_o2`，`regimes` 只有 `range` | `grep calibrate(` 空；`weights.json` |
| F2 | 生产每天读该文件的 range 块（`funnel.regime_aware=true`）。十组权重中位数：momentum −0.022、tech −0.036、volprice −0.029、fund_main −0.005、chip −0.006、value +0.014、north +0.002、fund_retail +0.002、growth 0、rz 0 | `weights.json regimes.range.weights`（111 行业） |
| F3 | composite 因此是「超卖分」：09-17 L1 池内与 pct_20d 秩相关 −0.80、rsi6 −0.86、cmf_20 −0.71、main_net_ratio −0.52；composite 前 50 名落刀 44%、后 50 名 2% | 本稿附录 A 探针 |
| F4 | 同一个 composite 贯穿：L1 `composite` 通道（quota 400 / floor 100）、L2 主排序（`stratified_l2(score_col="composite")`）、pass1 分诊排序、守卫⑨席位（`merge.pick_composite_seats` 按 `gbdt_score=composite`）、E6 `target_align` 面 | `recall/channels.py:29`、`recall/l2_stratify.py`、`l3/merge.py:68`、`relative_buy.py` |
| F5 | 落刀逐级叠加（7 日）：L0 15–27% → L1 20–40% → L2 27–56%（merit 核 31–65%、floor 22–47%）→ composite 前 20 名 45–85% → finalists 10–50%。floor 桶落刀率：健康 0%、成长 33%、趋势 44%、低位转强 54%、反转 67% | 附录 A |
| F6 | `composite_score` 支持 `weights["weights"]["__global__"]` 兜底 + 行业覆盖；内置 `_PRIOR_WEIGHTS` 也是 `__global__` 形态；已有过热惩罚（pct_60d 分位 >0.90 ∧ rsi6>80 或 winner>85 → −8）与吸筹加成 +5 | `common/scoring.py composite_score` |
| F7 | `pick_weights(frame, regime_aware, path=..., load=_load_weights)` 被 `universe.run` 与 L1Recall stage 共用；`weights_used.json` 快照实际权重；`session_agent/domain_ops.py:1318` 把 `weights.json` 当身份输入快照 | `common/scoring.py`、`scan/universe.py:349`、`session_agent/domain_ops.py` |
| F8 | `stratified_l2` 四步：② merit 核 = `l2_n − Σfloor` 按 sn 取（过 sector cap）③ floor 补（大 floor 先）④ 回填（过 cap；卡死松 cap）；`DEFAULT_FLOORS` 趋势 20/健康 15/反转 12/价值 12/成长 12/吸筹 12/主力 10/低位转强 8/事件 0；`l2.floors` 已是可配置旋钮（`universe.py:342`） | `recall/l2_stratify.py:41,202+` |
| F9 | `select_l2` 对 `pinned` 行：先抽出、不进竞争、选完追加、`l2_lane_reserved=True`、`selection_reason="pinned"` —— 行业席位照此注入 | `recall/l2_stratify.py select_l2` |
| F10 | `market.sector_healthy_top3(df)` 已有：资格门 n≥8 ∧ 资金门（主力净比中位>0 或 为正占比≥50%）∧ 60 日中位 > −20 ∧ 四组件 rank-sum；只喂 L5 | `scan/market.py:190-256` |
| F11 | `healthy_riser_mask` = 0<pct_60d<40 ∧ main_net_ratio>0 ∧ cmf_20>0，单一事实源，L1 healthy 通道与菜单体检共用 | `common/scoring.py` |
| F12 | `pick_composite_seats` 只剔 📌/ST/当日涨幅≥9.5%，不剔落刀；席位在 `write_finalists` 于 v3 守卫之后、pinned 之前注入；pass1 `triage` ①b 强留席位 | `l3/merge.py:68,611`、`l3/triage.py:140` |
| F13 | 卡面入场姿态靠散文推断（`parsers._entry_stance`：仓位 0% 或「不建仓/不新开仓/不新建仓」→ PROHIBITED；三词→ CONDITIONAL；「允许/建议/可以 新开仓」→ ALLOWED；其余 UNKNOWN）。近 10 场 106 张卡：空 89、UNKNOWN 11、PROHIBITED 6、ALLOWED 0 | `scan/l4/parsers.py:400-470`、附录 B |
| F14 | E6 `_selection_conflicts` 对 PROHIBITED 只**展示**冲突不设门；硬门四类 tradable/data_a/contract/no_redflag；`data_a` 读 `run_health.stage_results.failed_data`，而 `health.stage_results_health` 把 `l4_<code>` 阶段的 FAILED 原样计入 → 一只票 slim 失败连坐全天（09-15 ×6、09-17 ×11） | `relative_buy.py:426-460,582-663,984-1010`、`scan/health.py:440-460` |
| F15 | `pool="composite"` 时 BUY 只在席位里选且只按 `target_align` 排；`pool="finalists"` = 全部派发卡按四面 Borda 排。`RULE_VERSION="e6.v3.0"`；规则改动 = 升版 + golden 回放 | `relative_buy.py:208-217,1136-1180` |
| F16 | brief 输入白名单 `_WHITELIST_SPEC`（新输入文件须先登记 `contracts/artifacts.py` 再进白名单）；`_why_no_buy` 已算早停停因 + 三门 FAIL；`menu_health` 已算 L2 vs L1 的落刀/健康读数 | `scan/brief.py:98-125,177-200`、`scan/menu.py` |
| F17 | 账本 `LEDGER_COLUMNS` 有 `e6_buy` 无分级；`ledger_views.RUNS_COLUMNS` 有 `n_buy` | `scan/outcome.py:120`、`scan/ledger_views.py:105` |
| F18 | 09-17 中国船舶 `l4_600150` 在 `failed_data` 里，却仍发了 Hold 卡并入账 → 盲卡需要确定性层过滤，不能指望 agent | 09-17 `run_health.json` + `_final_ratings.json` |
| F19 | 离线重算可行：`L1_scored_full.csv`（L0 全帧，52 列）含十组 `score_<group>`（0–100 分位）与过热/吸筹所需列（pct_60d/rsi6/winner_rate/vol_ratio）；`L1_channels.csv` 留各路召回名单 | 09-17 staging |

---

## 2. 设计

### 2.1 召回权重：偏好档（裁定②）

**改什么**：`scan_config.jsonc` 新增两键（`funnel` 块）：

```jsonc
"weight_profile": "preference",   // "calibrated" = 读 weights.json(旧行为,回滚杆) | "preference" = 下面的固定档
"preference_weights": { "momentum": 0.20, "tech": 0.15, "volprice": 0.15, "fund_main": 0.15,
                        "chip": 0.05, "north": 0.05, "growth": 0.05, "value": 0.05,
                        "fund_retail": -0.05, "rz": 0.0 }
```

- 语义：符号 = 产品偏好「上涨趋势 + 有支撑 + 主力真在 + 散户不拥挤」；量级是裁定不是拟合，**没有自动重标定**（learning 层已退役）。`composite_score` 的归一化按 Σ|w| 做，量级只影响相对比例。
- 校验：`preference_weights` 必须恰好含 `scoring._GROUPS` 十个键、值为有限浮点，多键少键都在 `load_user_config` 抛 `ValueError`（三件套之一）。
- 生效点：新增 `common/scoring.resolve_weights(frame, cfg)` 作为**唯一**入口，内部：`profile=="preference"` → 返回 `{"meta": {"source": "profile:preference", "profile": "preference", "regime_applied": None, "config_sha256": <十键序列化哈希>}, "weights": {"__global__": preference_weights}}`；`profile=="calibrated"` → 原 `pick_weights` 逐字（parity）。`universe.run:349` 与 L1Recall stage 都改调它；`weights_used.json`/`meta.json.weights_source` 记 profile 名与哈希；`session_agent/domain_ops.py:1318` 的身份快照在 preference 档下改为快照配置块而非 `weights.json`。
- `funnel.regime_aware` 在 preference 档下无操作（meta 记 `regime_applied=None`），键保留。
- 过热惩罚与吸筹加成（F6）**不动**；追高由执行线与 L3 守卫⑦ 继续挡。
- 知识层同步：`STAGES.md` L1 节、`SKILL.md` 配置节、`scan_config.jsonc` 旁注写明「weights.json 自 2026-09-24 起仅研究用」。
- 内建默认：`weight_profile` 缺键 → `"calibrated"`（parity：不写配置就是旧行为）。生产配置文件本波改成 `"preference"`。

**离线验收**（§3.1 A1–A3）。

### 2.2 L2 形状：落刀帽 + 健康 floor（裁定④的第一堵墙）

**落刀谓词单一事实源**：新增 `common/scoring.falling_knife_mask(frame, thresh=-20.0)` = `pct_60d < thresh`；`menu._knife_share`、`l2_knife_audit._KNIFE`、本节落刀帽、§2.3/§2.4 席位剔刀全部改调它。L3 的 B 条散文（「pct_60d<−20 且无主力 直接弃」）不改。

**落刀帽**（`stratified_l2` 新增形参 `knife_cap_share: float | None`）：
- `universe.run` 计算 `l0_knife_share = falling_knife_mask(scored).mean()` 传入；`None` = 不动作（parity）。
- 生效在步骤 ②（merit 核）、④（回填）与 ③ 里**除反转、低位转强之外**的桶：每步的落刀行配额 = `round(knife_cap_share × 该步名额)`，超配额的落刀行跳过、由该步序列里下一个非落刀行顶上。反转与低位转强两桶**豁免**（它们的语义就是「跌过、在转」，L3 的 lowturn 例外条同源）。顶上的行记 `selection_detail="knife_cap"`（可数的「会变的量」），`selection_reason` 照旧。
- 预期：L2 落刀占比 ≤ L0 占比 + 豁免两桶带入的落刀（反转 6 + 低位转强 6 ≤ 12 行 = 6pp）。验收 §3.1 A4。
- 旋钮：`l2.knife_cap` bool，默认 `false`（parity），生产配置改 `true`。

**floor 调整**（`l2.floors` 已是旋钮，本波只改生产配置值，不改 `DEFAULT_FLOORS` 代码常量）：健康 15→**25**、反转 12→**6**、低位转强 8→**6**，其余不变。Σfloor 101→**103**，merit 核 99→97。依据 F5 各桶落刀率；健康桶供给足够（L1 健康占比 8–11% ≈ 80–115 行）。回滚 = 删掉配置里的 `floors` 键。

### 2.3 行业席位（裁定③，Claude 决定：接，上涨侧、席位制、不做排序驱动）

- 生效点：`universe.run` 在 `scored` 就绪后、`recall_select` 之前：`top = sector_healthy_top3(scored, k=cfg.max_sectors)`；每个入围行业内取 `healthy_riser_mask ∧ ¬falling_knife_mask` 的成员，按当日 composite（偏好档）降序取 `per_sector` 只；剔 📌/ST/当日涨幅 ≥ `CHASE_1D_PCT`。产出 `sector_seats: list[{code, industry, rank_in_sector}]`，落 `_sector_seats.json`（新登记产物）。
- 注入：席位行以 `sector_seat=True`、`recall_channels += "sector_seat"` 加进 `recall`（不占 `recall_n`，镜像 `pinned` 强注）；`select_l2` 把 `sector_seat` 行与 `pinned` 同一处理：不进竞争、选完追加、`l2_lane_reserved=True`、`selection_reason="sector_seat"`、`selection_detail=<industry>`。
- L3：`prompt.prepare_l3_table` 加 🏭 列（图例：「行业席位 = 当日 healthy top3 行业内的非落刀健康上涨成员，确定性直通到 L3；B 条照常适用；不因席位抬评级」）；`triage` 新增 ①c 强留 `sector_seat` 行（与 ①b 同属受保护集）。L3 守卫链不加新守卫；席位票在 L3 正常参与 finalist 竞争。
- 旋钮：`l2.sector_seats: {"enabled": false, "per_sector": 2, "max_sectors": 3}`，默认关（parity），生产配置开。回滚一行。
- 边界：只接上涨侧（`sector_healthy_top3` 已含 60 日中位 > −20 的资格门），与 07-17 裁定一致；席位 ≤6 行/日；不建新 L1 通道（`sector_momentum` 影子通道保持原状）。

### 2.4 守卫⑨ composite 席位（E6 池来源之一）

`pick_composite_seats` 加 `keep &= ~falling_knife_mask(d)`（席位不接刀）。排序键仍是 composite（此时已是偏好档）。`m=3` 不变。

### 2.5 卡片机读入场行（两引擎）

**契约**：`contracts/agent_output.L4_CARD` 新增 `Field("entry", r"\*\*入场\*\*[:：]\s*(允许|禁止|条件)", required=False, note="满卡必填、早停卡必填;缺席按散文推断并记 entry_source=prose")`。**不升** `CARD_SCHEMA_VERSION`（它是 ResearchCard JSON 的版本，与 md 入场行无关；`RESEARCH_CARD_FIELDS` 不动）。

**语义分离**（写进三份 agent 定义）：五档评级答「值不值得持有」；`入场` 答「T+1 尾盘按执行线能不能新开仓」。规则：
- 满卡：≥OW → `允许`（入场否决条件另写在触发位，不改本行）；Hold → `允许` 仅当 EV 目标带中枢 ≥ +0.3% ∧ R:R ≥ 1.0 ∧ OW 三门至少两门 ✓ ∧ 情报 T0/24h 无负面；否则 `条件(<一句>)` 或 `禁止`；UW/Sell → `禁止`。
- 早停卡：只能写 `禁止` 或 `条件(<一句>)`，**不得写允许**（早停 = 翻盘牌翻完仍加不起买点；A 级 BUY 只来自走完 P4/P5 的卡）。
- 📌 持仓卡照写（持仓管理节另答持有者怎么办）。

**模板位置**：两张模板的 `[执行线]` 两行上方各加一行 `**入场**: <允许|禁止|条件(...)>`。改 `.claude/agents/l4-card.md`、`.claude/skills/stock-research/lite-playbook.md`、`.codex/agents/l4_card.toml` 三处（同一段文字，`tests/test_agent_defs.py` 家族的双文件锚测试跟着改）。

**解析**：`parsers._parse_card_context_impl` 先找入场行：命中 → `entry_stance ∈ {ALLOWED, PROHIBITED, CONDITIONAL}`、新增 `entry_source="line"`；未命中 → 现有散文推断、`entry_source="prose"`。`no_new_position` 映射不变。

**自检**：`self_review` 新增 warn `卡契约·缺入场行`（满卡/早停卡缺行）；两个成功扫描日后升 fail（与 intel 限频同一升格路径）。

### 2.6 E6 v4.0：池、门、分级（裁定①）

`RULE_VERSION = "e6.v4.0"`。改动四处，其余（四面算法、Borda、并列决胜、第 2 只的门、`expected_abs_gap` 常量）逐字不动，用 8 日 golden 回放锁：

1. **池**：生产配置 `relative_buy.pool` 改回 `"finalists"`（= 全部 L4 派发卡，席位含在内）。`"composite"` 保留为回滚值。
2. **票级 data_a**：`_data_contract_ok` 返回 `(day_ok, reason, per_ticker_failed: set[str])`：`failed_data` 中匹配 `^l4_(\d{6})$` 的项归入 `per_ticker_failed`，其余为日级。`_hard_gate` ②：日级不 OK → 全体 fail（原样）；否则仅 `code ∈ per_ticker_failed` 的票 fail（detail 写 `l4_<code> 失败`）。历史 `run_health` 无 `failed_data` 键 → 保持 v1.1 旧口径（不改写历史判定）。
3. **入场门**：`_hard_gate` ④ `no_redflag` 在 UW/Sell 判定之后加：`card_context.entry_stance == "PROHIBITED"` → `fail("no_redflag", "卡面入场=禁止")`（`entry_source` 为 line 或 prose 皆算）。**与第 4 条的分级同受 `relative_buy.tiering` 控制**（一根杆：关 = v3 逐字，开 = 入场门 + 分级）。`_selection_conflicts` 保留（tiering 开后 PROHIBITED 冲突应恒 0，作为守卫的「会变的量」）。
4. **分级选择**（`relative_buy.tiering` 旋钮，默认 `false` = v3 选择逻辑逐字；生产开 `true`）：
   - A 级候选 = `eligible ∧ ¬pinned ∧ entry_stance == "ALLOWED"`，排序沿用 v3 finalists 键 `(−relative_decision_score, −target_align, −amount, code)`；卡面 EV 只展示不排序（不给解析器新的承重）。
   - A 级空 → R 级候选 = `eligible ∧ ¬pinned ∧ entry_stance ≠ "ALLOWED"`（PROHIBITED 已在硬门被否，故实际 = CONDITIONAL/UNKNOWN），排序同上。
   - 两级皆空 → `blocked=true`（UW/Sell 硬门不为授权让路；这天的「不可买归因」由 §2.7 解释）。
   - 输出：`buys[0]` 加 `tier: "A"|"R"`、`basis: "card_backed"|"relative_forced"`；顶层加 `tier_counts: {"A": n, "R": n}`；`why` 渲染加一句「A 级 n 只 / R 级 n 只」。
5. **盲卡不入账**（确定性层，不依赖 agent）：`_final_ratings.json` 与 `decision_records.json` 的写点（实施时 `grep` 定位唯一生产者）按任务簿过滤：`status != "SUCCEEDED"` 或 `artifacts.slim.status != "PRESENT"` 的票**不写入**，记入 `_blind_cards.json`（新登记产物）；summary 候选表该行印「⚠️数据不完整，未评级」。账本 `outcome_fill` 只读 `_final_ratings.json`，自然不入账。

**brief ③ 文案**：A 级印 `✅ **relative BUY**:<名> · **A 级·卡面允许入场** · EV <x%> · …`；R 级印 `✅ **relative BUY**:<名> · **R 级·卡面无买点·强制相对（裁定①）** · …`（红字由 emoji 🟥 承担，brief 无样式）。「绝对 gap UNMEASURED(n=0)」那句改读账本：`e6_buy` 成熟行 n 与均值，n<20 印「n=<n>，不足 20 不给区间」。

### 2.7 不可买归因（裁定④的可见面）

新模块 `scan/buyability.py`（零 LLM，`post_run.observe` 末尾、`relative_buy.write_decision` 之后跑），产出 `_buyability.json`（登记 `contracts/artifacts.py`；brief 白名单加 `("artifact","buyability")`）：

```json
{"menu": {"l2_knife": 0.40, "l0_knife": 0.23, "l2_healthy": 0.08, "l0_healthy": 0.11, "sector_seats": 6, "composite_seats": 3},
 "cards": {"n": 11, "allowed": 2, "conditional": 3, "prohibited": 5, "unknown": 1, "blind": 1},
 "gates": {"data_a_day": 0, "data_a_ticker": 1, "contract": 2, "no_redflag": 5},
 "buy": {"tier": "R", "code": "...", "blocked": false},
 "wall": "menu|cards|gates|none"}
```

`wall` 判定顺序（第一堵撞上的墙）：`menu` = L2 落刀 > L0 落刀 + 6pp 或 L2 健康 < L0 健康（与 §3.1 A4/A5 同一门）；`cards` = 菜单过关但 `allowed == 0`；`gates` = 有 allowed 卡但全被硬门否决；`none` = 出了 A 级。

brief ③ 加一行固定格式：`不可买归因:<wall> ｜ 菜单 落刀 L2 40%/L0 23% · 健康 8%/11% ｜ 卡 允许 2/条件 3/禁止 5 ｜ 门 data_a 票级 1 · contract 2 · redflag 5`。每个数进 `_brief_sources.json`。summary 的 🧭 仪表盘镜像 brief ③，不单独渲染（`_buyability.json` 在 `post_run.observe` 才写，比 `build_summary` 晚一站）。

账本：`LEDGER_COLUMNS` 加 `buy_tier`（A/R/空）；`RUNS_COLUMNS` 加 `n_buy_a`、`wall`；`stage_rulers.csv` 加 `E6/a_tier_day_share`（ALL 行 = A 级天数 / 成功天数）。

### 2.8 双引擎

Python 侧全部共用；引擎差异只在根目录与 agent 定义。清单：`.claude/agents/l4-card.md` ↔ `.codex/agents/l4_card.toml`；`.claude/skills/scan-market/{SKILL.md,STAGES.md,scan_config.jsonc}` 是共享文件；Codex 的 L4 slim 中继壳缺陷（09-15 记忆）与 `harvest.py` 修法在工作树未提交，**批 0 之前先提交**。

---

## 3. 验收

### 3.1 离线（零 LLM，实施批 1/批 2 各自的完成判据）

新工具 `autoresearch/research/menu_replay.py`（只读 staging，不写生产目录）：读 `L1_scored_full.csv` + `L1_channels.csv`，用 `score_<group>` 列按新权重重算 composite（复刻 `composite_score` 含过热/吸筹调整），L1′ = composite′ top-400 ∪ 其余各路留底名单，L2′ = `select_l2` 带新 floors/帽/席位。对 09-01/07/09/10/11/15/17 七日输出：

| # | 指标 | 门 |
|---|---|---|
| A1 | L1 池内 spearman(composite′, pct_20d) | > +0.3（七日全部） |
| A2 | composite′ 前 20 名落刀占比 | < 20%（七日全部） |
| A3 | L1′ 落刀占比 | 比现状 L1 低 ≥5pp（七日中位；反转/低位转强通道的 520 个 quota 本就带刀，L1 不设绝对门，L2 的 A4 才是硬门） |
| A4 | L2′ 落刀占比 | ≤ L0 落刀占比 + 6pp |
| A5 | L2′ 健康上涨占比 | ≥ L0 健康占比 |
| A6 | 行业席位 | 每日 ≥1 个入围行业时席位 ≥2 且全部非落刀 |
| A7 | 落刀帽「会变的量」 | `selection_detail=="knife_cap"` 行数七日合计 > 0 |

E6 v4：`tests/scan/test_relative_buy.py` 新增 golden：09-11 场 `tiering=true` 时 R 级应选出（合格 4 只里 Hold 3 只）；09-15/09-17 场票级 data_a 后 `data_a` 否决数 6→2、11→1；`tiering=false` 8 日回放与 v3.0 逐字 parity。

### 3.2 真实扫描（10 个成功扫描日，两引擎各自计）

| # | 指标 | 门 |
|---|---|---|
| L1 | A 级 BUY 天数 | ≥ 5/10 |
| L2 | `wall=="menu"` 天数 | ≤ 2/10 |
| L3 | finalist 隔夜 \|gap\| 90 分位 | > 1.0pp |
| L4 | L2 落刀 ≤ L0 落刀 + 6pp | 10/10 |
| L5 | 单场成本 | ≤ 当前中位 $46（不因席位多卡而涨；席位 ≤6 张卡替代等量 finalist 名额由 `l4_budget` 兜住） |
| L6 | `_selection_conflicts` 的 `card_says_prohibited` | 0 次 |

不设收益门：主尺读数照旧进账本，但本波的成功定义是「可买性」不是「隔夜赚钱」；20 个成熟结果日后另读 A 级 BUY 的 `gap_c1_o2` 与 `rel_gap_market`。

---

## 4. 批次、顺序、回滚杆

| 批 | 内容 | 前置 | 回滚杆 |
|---|---|---|---|
| 0 | 提交工作树里 09-15 harness 修法；票级 data_a；盲卡不入账；「昨日 delta」修跨 run 读者（prev = 本引擎 `reports_<engine>/scan/` 里数据日早于本场的最新已发布 run，用 `outcome.published_runs` 判定，不依赖账本视图）；`expected_abs_gap` 句改读账本；intel 超 cap 确定性截断 | 无 | 均为缺陷修复，无杆；delta/stub 文案有 golden |
| 1 | `resolve_weights` + 偏好档配置 + `weights_used` 记档 + `menu_replay` 工具 + A1–A3 | 批 0 | `funnel.weight_profile="calibrated"` |
| 2 | `falling_knife_mask` 单源 + 落刀帽 + floors 配置值 + 行业席位 + 席位剔刀 + L3 🏭 列/①c 强留 + A4–A7 | 批 1 | `l2.knife_cap=false`、删 `l2.floors`、`l2.sector_seats.enabled=false` |
| 3 | 卡入场行（三份定义 + 契约 + 解析 + 自检）+ E6 v4（池/入场门/分级）+ `buyability` 归因 + brief/summary/账本列 | 批 2 | `relative_buy.tiering=false` + `pool="composite"`；卡行多写不影响旧解析 |
| 4 | 真实扫描 10 日验收（§3.2），期间不改规则 | 批 3 | — |

每批独立 commit、全量测试绿、ruff 干净；批 1/2 有离线 A 表读数才算完成（「跑通一次 CLI」不算接线；每个新生产者 `grep` 调用链）。

---

## 5. 测试计划（按批）

- 批 0：`test_relative_buy.py`（票级 data_a 三例：日级坏/票级坏/历史无键）；`test_assemble_*`（盲卡过滤）；`test_brief.py`（delta 跨 run、abs_gap 文案）；`test_l4_intel_*`（截断）。
- 批 1：`tests/scan/test_config_knobs.py`（两新键三件套）；`tests/common/test_scoring.py`（`resolve_weights` 两档；preference 档 meta 字段；`__global__` 落到 `composite_score`）；`test_universe_*`（`weights_used.json` 记 profile）；`research/test_menu_replay.py`（复刻 composite 与生产 `composite_score` 逐值一致 = 变异探针：改一个权重必须变红）。
- 批 2：`test_l2_stratify.py`（帽配额 ②④ 生效、③ 豁免、`knife_cap`=None parity）；`test_menu_health.py`（改调单源谓词读数不变）；`test_universe_l2_cols.py`（`sector_seat` 行注入、reserved、reason）；`test_l3_merge_v3.py`（席位剔刀）；`l3/test_triage*`（①c 强留）；`test_market_sector_top3`（席位成员非落刀 ∧ healthy）。
- 批 3：`test_parsers_card_context.py`（入场行优先、`entry_source`、缺行回退）；`tests/test_agent_defs.py`（三份定义同段锚）；`test_relative_buy.py`（v4 golden ×3、parity ×1、`tier_counts`、入场门）；`test_buyability.py`（四种 wall 各一例 + 白名单不变量）；`test_brief.py`（归因行 + sources）；`test_ledger_views`（新列）。
- 每批做一次「删掉这段测试会红吗」抽查（变异探针家训）。

---

## 6. 文档同步

`STAGES.md`（L1 权重来源、L2 帽/floor/席位、L3 🏭、E6 v4 分级、归因行）；`SKILL.md`（配置节新键；步骤 5 的 `buyability`）；`scan_config.jsonc` 旁注（生效点/证据/回滚杆三段式）；`docs/session-agent/README.md` 若身份快照变更；`AGENTS.md` 若卡契约行影响 Codex 说明。

---

## 7. 明确不做

- 不换主尺、不提 swing、不重开自动重标定、不恢复 L4 复用、不重建 pipeline 对象。
- 不改 L3 的 B 条、不改五档评级映射、不改执行线阈值。
- 不做 L4 派发前确定性预判与编排壳合并（成本项，另立）。
- 不动 `expected_abs_gap` 的规则（只改 brief 文案读账本）。
- 不启用 `sector_momentum` 影子通道。

---

## 附录 A：落刀链探针（2026-09-24 只读，session scratchpad `knife_chain.py`）

| 日期 | L0 | L1 | L2 | L2 merit | L2 floor | composite 前 20 | finalists | L0 健康 | L1 健康 | L2 健康 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 09-01 | 15.1 | 20.2 | 27.4 | 31.5 | 22.2 | 55 | 18.2 | 14.5 | 14.4 | 10.4 |
| 09-07 | 15.3 | 25.3 | 32.8 | 34.2 | 31.1 | 45 | 10.0 | 9.1 | 10.4 | 8.0 |
| 09-09 | 22.0 | 33.4 | 44.6 | 51.4 | 36.3 | 85 | 25.0 | 8.7 | 10.6 | 8.4 |
| 09-10 | 24.3 | 37.9 | 51.5 | 57.7 | 44.0 | 75 | 20.0 | 9.1 | 11.0 | 7.9 |
| 09-11 | 26.7 | 40.0 | 54.0 | 59.5 | 47.3 | 75 | 27.3 | 7.2 | 9.4 | 8.4 |
| 09-15 | 26.0 | 40.5 | 55.9 | 64.9 | 45.1 | 80 | 50.0 | 5.0 | 8.0 | 7.4 |
| 09-17 | 22.8 | 28.8 | 39.6 | 36.0 | 44.0 | 45 | 27.3 | 11.2 | 11.5 | 7.9 |

单位 %。floor 桶落刀率（七日合并）：健康 0（n=102）、成长 32.6（43）、趋势 43.8（114）、低位转强 54.2（48）、反转 66.7（12）。09-17 L1 池 spearman(composite, ·)：pct_5d −0.62、pct_20d −0.80、dist_high_60 −0.34、main_net_ratio −0.52、cmf_20 −0.71、rsi6 −0.86、pe −0.33。

## 附录 B：卡面入场姿态普查（近 10 场 `_relative_buy_decision.json`，非持仓）

| 引擎 | 评级 | PROHIBITED | UNKNOWN | 空 |
|---|---|---:|---:|---:|
| claude | Hold | 3 | 4 | — |
| claude | UW | 2 | 1 | — |
| claude | — | 0 | 3 | — |
| 合计（两引擎 106 行） | | 6 | 11 | 89 |

ALLOWED 0 次。Codex 各场 Hold 卡 6–8 张、Claude 1–6 张。
