# Token 经济 × L3 质量波设计（同判断质量下压 30-40% 真实消耗 + 修 L3 三实锤）

> 来源：2026-07-08 token 专项 brainstorm + L3 早停诊断（确定性分析 345 张历史卡，脚本产物 `l3_earlystop_joined.csv`）。
> 状态：设计稿，未实现。用户已拍板范围：token brainstorm 全项**除 C2（effort A/B 裁决）**；早停诊断产出的 L3 修复项并入本波。

## 目标 / 非目标

**目标**
1. 同判断质量下，把单次 scan 真实 token（~1M 量级）压 30-40%——主刀 L4 输入侧（占落盘 46%）与 cache 命中率。
2. 修 L3 诊断出的三个实锤：误读自见数据（07-06 裁决表 31 条被打脸前提 22 条证据纯 L3 表内可见、0 条靠 slim 独有）、conviction 强预测早停（≤55 档 87%）却未用于派发、conviction 与 fwd 倒挂（≤55 档 fwd_5 中位 +2.5% 全场最好）。
3. 计量基建（OTEL/预算护栏/真实计费列）让省钱可验收。

**非目标（负结果记录）**
- **C2 effort=xhigh 实测裁决**：用户拍板不做。
- **D3 L2 出口 200→150**：数据裁决**不砍**——38/254（15%）finalist 出自 rank 151-200，虽 0 Buy/OW 但已结算 fwd_5 中位 +1.72% 不差于头部（L2 排序无 alpha 的又一证据）；砍了限制 L3 holistic 选择权，省的 token 用 T2/delta 就能拿到。
- L3 拆片并行（破坏 holistic 比较）、早停概率预判跳卡（漏斗铁律：能否决不能确认）、模型降级（"全 Opus"已裁决架构）。
- **早停率高本身不是病**：错杀率 10% 与满卡持平、被拒票 fwd_5 中位 −0.93% 为负、06-25 池子好时早停率仅 20%——高早停是弱市（risk_off/range）下纪律正确的表现，省钱靠结构化（T1/T9）而非压早停率。

## 诊断依据（2026-07-08 确定性分析，n=345 卡 / 13 日 / 255 张已结算）

| 读数 | 值 | 含义 |
|---|---|---|
| 早停率区间 | 20%（06-25）~100%（06-24），近三日 87-95% | regime 相关，非机制失灵 |
| 早停票错杀率（winner_5） | 10%，与满卡组持平；fwd_5 中位 −0.93% | 早停判断便宜且对 |
| L4 打脸 L3 前提率（07-06 卡 v2，n=51 前提） | ✗ 61%；证据 22/31 纯 L3 表内可见，0 slim 独有 | **L3 在误读自己的输入**（低基数/量价背离/位置误读三模式） |
| conviction→早停梯度 | ≤55:87% / 56-65:60% / 66-75:43% / >75:18% | conviction 已可用于派发分层 |
| conviction→fwd 倒挂 | ≤55 档 fwd_5 中位 +2.5% 全场最好（集中 06-26/29/30 反弹日） | 高确信≠alpha（与 cross_calib trend 33% 翻案同源）；低确信票不可粗暴砍派发 |
| 07-06 低确信档份额 | conviction≤55 = 8/20 卡（全早停） | 分层派发当日可省 ~40% L4 消耗 |

## 决策摘要

| # | 项 | 一句话 | 档 | 成本 |
|---|---|---|---|---|
| T1 | slim 二段式 | `--slim` 拆 surface（P0-P3 用，~8-10KB）+ deep（P4 才 Read，财报质量/偿付/季度利润表段），早停卡永不读 deep | P0 | S |
| T2 | L3 表地形裁剪 | sector_terrain 只渲染 top200 实际覆盖行业（presence-gated 惯例） | P0 | XS |
| T3 | SKILL/STAGES 瘦身 | 逐日 changelog 移 git 历史，快照留当前态；51KB→~25KB | P0 | S |
| T4 | sector.reuse 白做修复 | `reuse --apply` 被 pack 重派覆盖（终审 M3 cost-only bug） | P0 | XS |
| T5 | bash 步合并 | workflow L4-prep 五步（l4_reuse→pledge→seats→calendar→prompts）合一个 agent 按序执行，砍 4 个 agent 会话固定开销 | P0 | XS |
| T6 | L3 误读三预警旗 | 确定性列：低基数旗（np_yoy>100∧roe<8）/量价背离旗（cmf_20>0∧main_net_ratio<0）/位置旗（winner_rate<25∧ma_bull=0）进 L2 csv+L3 表+L4 简报；L3 硬约束 E=引用相应论点必须先对旗表态 | P0 | S |
| T7 | 早停短模板+假阳 warn 根治 | 早停卡精简输出格式（评级/早停点/理由/重估触发四行核心）；核掉「P3 早停卡被当满卡查 P4 行」残留假阳 | P0 | S |
| T8 | cache 前缀完整性审计 | 断言测试：`_l4_prompt_*` 共享块严格前置、逐卡可变部分只在尾部；当日件不得插前缀中段 | P0 | XS |
| T9 | conviction 分层短卡 | conviction<55 的 finalist 派**短卡**（P0-P1+surface slim+禁 P4+短输出，约省该卡 60-70%），不砍派发（反弹日 fwd 倒挂教训）；阈值可配，影子记录反事实 | P1 | S-M |
| T10 | budget token 护栏 | workflow 在 L4 dispatch 前查 `budget.remaining()`，不足按 conviction 升序降级为短卡/削尾 | P1 | S |
| T11 | 预热派发 1+29 | 首卡 await 写热 prompt cache，其余 29 卡并发（5min TTL 内全命中）；代价 ~3-4min 墙钟 | P1 | XS |
| T12 | OTEL+真实计费列 | 挂真会话（运营，下次 scan）；token 表加真实计费列（presence-gated 读 token_telemetry） | P2 | S(运营) |
| T13 | conviction 档级校准 | cross_calib 加 conviction 档×早停/翻案/fwd 校准表，描述性注入 L3 校准块（X1 的 lite 前置） | P2 | S |
| T14 | 列引用面砍列 | 用 stage_eval/L3 输出引用面反推 22 列中的死列再砍一轮 | P2 | S |

## 分项设计

### T1 · slim 二段式（最大单刀，估省 L4 输入 30-40%）

- `analyze/harvest.py --slim` 拆两文件：`<tk>_<date>_slim.md`（surface：identity/快照/技术/市场上下文/tradeability/新闻/股东质押/UZI 四段/催化日历）+ `<tk>_<date>_slim_deep.md`（deep：Income statement quarterly / Earnings quality forensics / Solvency & refinancing / Fundamentals overview 长表）。段归属以「P4 陷阱核是否需要」为准，现 slim 段结构已核（20+ 段可干净切分）。
- l4-card agent 定义 + `_l4_prompt` 模板改：P0-P3 只给 surface 路径；**进入 P4 才 Read deep 路径**（subagent 本有 Read 工具）。
- GATE3/`harvest_slim_batch` 地板适配：surface 地板降至 ~7KB（原 10KB 含 deep 段），deep 存在性单独校验；`>10KB 才可信` 文案同步。
- **验收**：① 单测=段划分契约（deep 段名不出现在 surface）；② 真跑抽 2 张早停卡 transcript 确认未 Read deep；③ 满卡信息面不变（P4 段齐全）；④ token 表 slim 行分 surface/deep 两桶。
- **依赖**：无。与 T9 组合（短卡=surface only）。

### T6 · L3 误读三预警旗（打脸前置，零 LLM 成本）

- 落点：`scan/agents/l3_select.py` 组表处（或 `common/scoring` 旗函数，两侧复用）+ `l4_card.py` 简报 compose（presence-gated 一行）。
- 三旗定义（阈值先验，标注待校准）：`low_base_flag = np_yoy>100 ∧ roe<8`；`flow_div_flag = (cmf_20>0 ∨ obv_mom_20>0) ∧ main_net_ratio<0`；`trapped_flag = winner_rate<25 ∧ ma_bull=0`。
- L3 硬约束 E（l3-rank.md）：以成长/资金/空间为核心论点时，若对应旗亮必须一句话自证为何不是（低基数/派发/套牢）陷阱——与既有硬约束 D 同格式。
- **验收**：① 旗函数单测（含 NaN 容错）；② 07-06 回放：31 条被打脸前提中三模式命中的 ≥15 条应被旗标出；③ parity：旗列 presence-gated，L3 表不加宽超 +3 列。
- **依赖**：无。

### T9 · conviction 分层短卡（方案 B；方案 A=直接砍派发，因 fwd 倒挂弃）

- dispatch-plan 扩展：`dispatch` 拆 `full`（conviction≥55 或缺值或 carryover）/ `lite`（<55）。lite 卡 prompt=surface slim+P0-P1 指令+禁 P4+短输出模板；评级封顶 Hold（低确信+浅研不给买单资格——与"漏斗能否决不能确认"一致）。
- 反事实记账：lite 卡票自动进影子记录（沿用 shadow 基建），retro 可裁决"若给满卡会怎样"；阈值 55 进 config 可调。
- **验收**：① dispatch-plan 单测（分组/边界/缺 conviction 回退 full）；② 影子对照 ≥10 scan 日后 retro 出「lite 卡错杀率 vs 省耗」报告；③ 0 买日预算压缩（menu.l4_budget）与本机制叠加时 lite 优先被砍。
- **依赖**：T1（surface slim）。

### T3/T5/T10/T11/T12（编排与计量，合并要点）

- T3：STAGES.md 保留架构快照+当前参数表，逐日沿革整段删（git 有）；SKILL.md 手工步骤段压缩为"调参入口"注解（workflow 为准）。验收=字节数减半+`test_skill_docs_refs` 绿。
- T5：workflow.js L4-prep 五个 `bash()` 合一个（一 agent 顺序执行五命令、按序报告退出码，任一非零即失败上抛）；保持生产者先于 prompts 的顺序语义。
- T10：L4 派发前 `budget.remaining()` 检查（workflow `budget` 原语）；阈值=预估单卡成本×dispatch 数×1.2，不足先把 lite 组削尾、再满卡组按 conviction 升序降 lite。
- T11：`await agent(首卡)` 后 `parallel(其余)`；首卡选 dispatch 组第一只（无特殊选择逻辑）。
- T12：OTEL 五件 env 已备（07-05 波），下次真跑挂上；`_stage_token_estimate` 加「真实计费」列（presence-gated：无 telemetry 落稿则显示 —）。

### T2/T4/T7/T8/T13/T14（小件要点）

- T2：`l3_table_md(sector_terrain=...)` 改按 top200 行业集合过滤；全行业模式留 flag。
- T4：修 `sector.reuse --apply` 与 pack 重派的覆盖时序（复用行业跳过 pack 重建）。
- T7：早停卡模板（四行核心+论点裁决表保留）；`card_contract_lint` 对早停卡的 P4 行豁免已有，根治残留假阳路径并补回归测试。
- T8：`test_l4_prompt_cache_prefix`：共享块 sha 在 N 张 prompt 中一致且位于文件头部；当日件（📐🔁🚪）只允许出现在共享块内固定位置或逐卡尾部。
- T13：`cross_calib.py` 按 conviction 档聚合（早停率/翻案率/成熟 fwd），n<10 档 ⚠ 不注入；输出并入 `_l3_calibration.md` 描述性段。
- T14：排 P2 尾——等 OTEL+stage_eval 引用面数据，避免拍脑袋砍列。

## 分期与依赖

```
P0 无悔手术(并行)                     P1 派发经济                  P2 计量/校准
T1 slim二段式 ──────────────┐        T9 conviction短卡 ←T1        T12 OTEL+计费列(运营)
T2 地形裁剪  T3 SKILL瘦身   ├──→     T10 budget护栏 ←T9           T13 conviction校准表
T4 reuse修复 T5 bash合并    │        T11 预热派发(独立)            T14 引用面砍列 ←T12
T6 三预警旗  T7 早停短模板  │
T8 前缀审计契约测试 ────────┘
```

- 全项 parity 铁律：presence-gated / 默认行为可回退；`pytest` 全绿 + `ruff` clean 每 task 门。
- 验收总账：下一次真跑（同时是运营周四合一验收日）对比 token 表 + OTEL 真实读数；T1+T9 合计目标=真实口径省 30-40%。
- 预警旗阈值（T6）与 conviction 阈值（T9）首月只当 advisory/短卡分层，不动质量门。

## 风险与开放问题

1. **T1 段归属边界**：个别段（如 Analyst consensus）P3 翻盘牌可能要用——切分清单落地时逐段过一遍 lite-playbook 的 P1-P3 引用面，宁可 surface 多留不误伤。
2. **T9 评级封顶 Hold 的错失面**：低确信票若真有大机会，lite 卡看不出——靠影子反事实+观察单兜底；≥10 日后 retro 裁决阈值。
3. **T11 收益不确定**：若 cache 本来就命中（竞态假设不成立），预热只多花 3-4min 墙钟——OTEL（T12）读数出来后可一行回退。
4. **诊断样本薄**：打脸率分析仅 07-06 一日 20 卡（卡 v2 才有裁决表）；T6 验收②用该日回放，后续每真跑日自动累积。

## 实现进度（2026-07-08 更新）

**P0 全部落地**（分支 `feat/token-economy-p0`，main `47141d9`→`f3e4f79`，12 commit，788 tests green + ruff clean，subagent-driven 逐 task 审 + whole-branch 终审 Ready-to-merge）：

| Task | 状态 | commit / 备注 |
|---|---|---|
| T1 slim 二段式 | ✅ | `acc30ce` `_slim.md`(表面 P0–P3)+`_slim_deep.md`(深核 P4)；GATE3 地板 10K→8K；早停卡永不读 deep |
| T6 误读三预警旗 | ✅ | `eb1db74`+`3a0b2d9` `scoring.l3_misread_flags` 单一事实源→L3 列+L4 简报+l3-rank 硬约束 E |
| T3 早停假阳+短格式 | ✅ | `2b269f2`+`01c7c5d` 豁免收紧到首行(fail-open)；早停卡短格式 ≤35 行 |
| T4 地形 top200 裁剪 | ✅ | `8356a59` `sector_terrain_md(top200_only=True)` ≈110→30-50 行 |
| T5 bash 步合并 | ✅ **已满足**（无需新改） | 前一波 research-depth（`a61192c`）已把 L4-prep 五步重排为 workflow.js 内 `l4_reuse→pledge→seats→calendar→prompts` 按序执行；本波核实其已满足 T5 意图（生产者先于 prompts 顺序语义在位），未重复改。 |
| T7 SKILL/STAGES 瘦身 | ✅ | `803afe4`+`5fcb52d` 51KB→25.7KB，契约锚保留，交叉引用修 |
| — cache 前缀契约（T8 关联） | ✅ | `7495bf4`+`7d12805` **首跑逮到真 bug**：逐票标题排共享块前→30 卡 cache 前缀从第 1 字节断裂（历史实跑大概率全 miss）；修=固定标头前置，byte-identical 契约锁死 |
| — sector 复用白做修复 | ✅ | `36084df` fan-out 排除已复用 brief（cost-only） |

**终审 I-1 已修**（`f3e4f79`）：套牢旗 `winner_rate<25∧ma_bull=0` 在 risk_off 深跌市亮 ~90%=wallpaper → 加 `∧pct_60d>0`（反弹撞套牢盘才成立），真数据 07-07 **90%→12%**、三旗均衡。此即兑现 §决策摘要 T6「阈值先验，标注待校准」的校准承诺。

**未做（转后续）**：
- **P1**：T9 conviction 分层短卡（方案 B，依赖本波 T1 surface/deep 形态）/ T10 / T11 预热派发 / T12 OTEL 真计量 — 另立 plan。
- **P2**：T13 / T14 引用面砍列 / T8 剩余 — P1 后排。
- **defer Minor**（终审三诊 OK-to-defer）：`低基` 概念在 `uzi_lenses.trap_signals`(np_yoy≥150) 与 `scoring.l3_misread_flags`(np_yoy>100) 双阈值（建议加交叉引用注）；T4-M1 L2 缺失回退分支无专项回归测试；其余见 `.superpowers/sdd/final-review-p0.md`。
- **P0 验收日**（下次真扫描）：slim surface/deep 两桶 token 对比 / misread 列上表（套牢校准后 ~30% 覆盖）/ 早停卡短格式 / GATE3 8K 地板 / sector 复用不再被覆盖 / cache 命中率（OTEL）实测修复后改善。
