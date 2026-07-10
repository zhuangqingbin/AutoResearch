# 2026-07-10 · 超短口径对齐(T+1/T+2) + 机构面引入 + workflow 进度可视 —— 设计稿

> 状态:已经用户逐节批准(2026-07-10 会话),待实现。
> 来源:用户提出三问题 ①机构看好信息没引入 ②workflow 跑时进程不显示(要零 token 展示) ③验证数据是 T+1/T+2 的数据。
> 三路代码摸底(机构数据地图 / 进度展示现状 / 全链路 horizon 清单)结论内嵌于各节。

## 0. 用户拍板记录(设计的根)

| 决定 | 内容 |
|---|---|
| **持仓周期 = 超短 1~2 天** | 全套验证/选股/复盘尺子对齐 T+1/T+2;T+5/T+10 降为参考。此前系统自述"1–2 周 swing"是错误假设,一并改掉 |
| 机构看好 = 修尺后重审 + 接线 | 用新尺重测被拒机构因子;report_rc 探历史回填提速;主交付物 = L4 卡「机构面」块可见性;**允许 L4 卡有界 WebSearch 补机构动向**(用户确认,非零 token、有界) |
| L4 卡也超短化 | 目标价/三情景/tripwires 改 1~2 日语义(用户选了工作量更大的选项,非默认边界) |
| 进度展示 = 脚本+Python 双层 | 全零 LLM token;不做 _progress.log 心跳(用户未选三层) |
| T+5 两提案作废 | pr_20260702_001(regime 口径切 fwd_5)、pr_20260709_001(momentum quota 上调)记 **rejected**,理由="持仓意图 2026-07-10 裁定为超短 1~2 日,T+5 尺不适用" |

**诚实预期(已向用户申明)**:T+2 尺下 momentum(T+5 才有 +6.3% 独占超额)大概率继续被压权重——这从"错配"变成"正确";0 买日的解读随之反转:不是尺子错杀,是超短口径下真没机会。机构类慢信号大概率进不了排序门,主要价值落在 L4 卡可见性。

## 1. 波 1a · 尺子对齐超短(漏斗+账本)

### 1a.1 新窗口

`autoresearch/research/factor_lab.py forward_returns()`(定义源 :224-252)新增:

```
fwd_2_oc = close[D+2] / open[D+1] − 1            # D+1 开盘买、D+2 收盘卖的全程收益
hi_2_oc  = max(high[D+1..D+2]) / open[D+1] − 1   # 2 日触价(MFE),配波 1b 目标价校准
```

- 选 `_oc` 不选 `open[D+3]` 版:成熟日与现状 `fwd_1_oo` 相同(D+2 收盘),**retro 节奏不变**(跨周末 ≈4 自然日)。
- `fwd_1_oo` 保留为副口径(纯隔日纪律);`fwd_5_oc`/`fwd_10_oc`/`hi_10_oc` 保留为参考列,不删。
- 口径原则:**一把主尺(fwd_2_oc),两把备尺(fwd_1_oo 副、fwd_5 参考)**;所有"主判定/主排序/主归因"用主尺。

### 1a.2 切换点清单(逐文件)

| 链路 | 位置 | 改动 |
|---|---|---|
| L1 权重校准 | factor_lab.py `calibrate()`(:655) | label_col 默认 `fwd_1_oo → fwd_2_oc`,weights.json 重算 |
| regime 块 | factor_lab.py `calibrate_regimes()`(:685) | 同上;用现成 107+ 成型日面板重算,零等待 |
| GBDT L2 label | factor_lab.py `GBDT_LABEL`(:738) | → `fwd_2_oc` |
| IC 表 | factor_lab.py FWDS(:451)/十分位(:521)/主排序(:543) | FWDS 加 fwd_2_oc;主排序/十分位切 fwd_2_oc |
| retro 主归因 | learning/retro.py 主 winner/bucket(:68-85) | 判定切 fwd_2_oc(≥90 分位 ∧ ≥3%);winner_5(:89-107) 降为参考节 |
| retro 错杀验尸/配对 | retro.py autopsy(:147-164)/pairs(:178-199) | T+5 → T+2 主,T+5 保留参考 |
| retro 机判/day_ic | retro.py mtm(:242)/day_ic(:325) | T+1 → T+2 |
| retro 门审计 | retro.py gate_audit(:279-284) | ex1/ex5 → ex1/**ex2** 主 + ex5 参考 |
| 各段 edge | learning/stage_eval.py(:29-30) | `_RET_T5` 主口径 → fwd_2_oc(常量更名 `_RET_T2`),自述"1-2 周 swing"注释改超短 |
| L2 champion 对照 | l2_eval(forward_compare :31,69) | label 默认 → fwd_2_oc |
| 召回路账本 | learning/channel_ledger.py(:13,35) | `unique_excess_t5` → 新列 `unique_excess_t2`(t5 列保留) |
| 0 买对照 | learning/zero_buy_ledger.py(:18,34-35,59-61) | 主裁决 mkt_fwd5 → mkt_fwd2(fwd5 保留) |
| 门 MTM | learning/gate_ledger.py(:18,39-46) | ex1/ex5 → ex1/ex2 主 + ex5 参考 |
| 跨层校准 | learning/cross_calib.py(:80-106) | ex5 → ex2;触价窗见波 1b |
| 行业嘴 MTM | learning/sector_ledger.py(:90,117) | fwd_5 → fwd_2 |
| 催化对照 | learning/catalyst_ledger.py(:29-43) | fwd_5_oc → fwd_2_oc |
| 买单账本 | learning/buy_ledger.py(:19,92-100) | 评级基率主看 T+5 → T+2(全列保留) |
| 观察单触发后市 | learning/watchlist_ledger.py(:16,43-45) | fwd_1+fwd_5 → fwd_1+fwd_2(fwd_5 保留) |
| paper NAV | learning/paper_nav.py(:54) | `hold=10 → hold=2` 主表;保留 hold=10 副表做连续性对照 |
| 先验/注释自述 | common/scoring.py(:104,:286)、learning/feedback_store.py(:438) | "swing/1-2 周"自述改超短;先验权重在重校后由数据接管 |
| 提案裁决 | context/knowledge/proposals.jsonl(:5,:7) | pr_20260702_001 / pr_20260709_001 → status=rejected + 理由 |

### 1a.3 账本迁移 = 新列 + 湖回填,n 不清零

所有账本改法一律**加新列不改旧列**;fwd_2_oc 对全部历史 scan 日可由湖(lake)价格回算 → 一次性回填脚本把已积累行的 ex2/unique_excess_t2/mkt_fwd2 补齐(channel 11 日、zero_buy 7+ 日、gate/sector/catalyst 全部),**样本量 n 不清零**。回填幂等(重跑不重复)。

### 1a.4 明确不动

- 成熟度门(retro.py:384,430,D+2 成熟)不变。
- watchlist `since_born` 无窗滚动(事件触发)不变。
- L0/L1/L2 漏斗结构、门(binding gates)、presence-gated parity 全不动——只换尺,不换秤。

## 2. 波 1b · L4 卡契约超短化(用户加选)

### 2.1 语义改动

- 卡片**目标价 / 三情景 R:R / tripwires 改 1~2 日语义**(现为 ~10 日 swing 语义);五档评级、渐进深度+早停、陷阱核、变化项节等结构全部不动。
- 与之配对的校准同步换:buy_ledger `target_calibration`(:108-149)触价窗 `hi_10_oc → hi_2_oc`(1a.1 已定义);cross_calib 门柱错杀判定同步。tr>0 看多口径过滤保留。
- stock-research **lite 档 = 超短决策卡**(scan L4 与单票"快速看一眼"同一档);**full 深研报告不动**(研究语义,非交易卡)。

### 2.2 波及面(契约同步清单)

- `autoresearch/scan/agents/l4_card.py` prompt 模板(目标价/情景/tripwire 措辞)。
- `.claude/agents/l4-card.md` 叶子 agent 人设 + `tests/scan/test_agent_defs.py` 同源契约 lint。
- `tests/scan/test_l4_prompt_cache_prefix.py`:L4 prompt 共享前缀 byte-identical 契约(07-08 波锁死)——模板改动后**重锚 golden,保住"共享块在前、逐票块在后"结构**,勿破 30 卡并发 cache 命中前提。
- rubric_rating 评分锚里含 horizon 措辞处同步;self_review 卡片契约 lint 期望同步。
- **卡片复用防混用**:l4_reuse(♻️TTL 复用)加 `card_schema_version` 进复用键——旧 swing 语义卡不得被当超短卡复用,版本升级当日全部新派。

## 3. 波 2 · 机构面引入(T+2 尺重审 + L4 可见性)

### 3.1 重审(排序门,数据说话)

factor_lab @ `fwd_1_oo + fwd_2_oc` 重测四类(当年 Phase A 以"T+1 无 alpha"判死,须在新主尺下重审一次):

- `lhb_inst_net`(龙虎榜机构,factor_lab.py:108,347-355)
- `rz_ratio` / `rz_buy_intensity`(两融,:106,333-339)
- `block_premium` / `block_intensity`(大宗,:107,340-346)
- `hk_ratio`(北向,确认其 ≈0 → 留给 calibrate 自动降权,不手工动)

过门标准沿用 consensus.py 既有约定:两半样本稳 + 符号一致。**过门者才进 L1 组/召回路**(新组入 `_GROUPS` + weights 校准自然接管);不过门 → 只留 L4 可见性(3.3)。

### 3.2 report_rc 提速(卖方一致预期)

- 现状:07-06 起每日拉,仅 4/60 日,`consensus_delta` 零消费(research/consensus.py;prelude.py:61-64 触发)。
- **探针先行**:对历史日期(如 2026-05-15)拉一次 report_rc;能返回 → 限频感知回填 60+ 交易日(cache/report_rc/),IC 门立刻可验,不必等到 10 月;不能返回 → 维持日拉,门期照旧。
- `consensus_delta` 接两个消费点(advisory,不进分):L3 表 `rc` 列(修正方向/家数)+ L4 机构面行(3.3)。IC 过门后才谈进 L1(另立提案)。

### 3.3 L4 卡「机构面」块(主交付物,presence-gated)

不论过不过排序门,研究员看卡时应看到机构信息。新增 `_inst_mark` 组块:

- 机构调研 N(现 `_cat_mark` 已有,归入)
- 卖方修正:近 30 日上调/下调家数 + 目标价中位 vs 现价(来自 report_rc/consensus_delta)
- 龙虎榜席位注记(现 `_seat_mark` 已有,反指口径保留)
- 基金重仓 Δ(见 3.4,若可用)
- **有界 WebSearch(用户确认)**:l4-card agent 本就带 WebSearch/WebFetch;prompt 加指令——仅当机构面块非空、或卡片进入 P4(未被主早停)时,≤2 次网查「<公司> 研报/评级/机构调研」近 30 日,结果须标来源+日期,只作旁证不得替代数据行。token 非零但有界(≤2 查询/卡,且触发有条件)。

全部 presence-gated:缺数据不加行,parity 不破。

### 3.4 fund_portfolio 探针

tushare `fund_portfolio`(基金季度重仓,2026-06-20 设计稿当年以"akshare 取不到"砍掉、tushare 侧未探)——一次探针;可用 → 季度文件入湖 + L4 行"基金重仓 N 家/环比 Δ"(季度滞后,恒 advisory);不可用 → 记档不再试。

## 4. 波 3 · 进度展示双层(全零 LLM token)

### 4.1 脚本层(.claude/workflows/scan-market.js,~30 行)

现状:log() ×8 全是"门后回执",三大算力段(universe ~9.5m / L3 精排 ~14m / L4 并发卡 ~7m)恰好全在两道门之间的 log 盲区(07-09 run 实测 41 分钟)。

- **前置 log**:三段开始前各加一条,格式"L3 精排开始:200 只,历史 ~14m"(只数取自脚本内已有变量,ETA 静态文案、以 4.2 的墙钟数据为更新依据)。
- **逐张计数 log**:L4 卡与行业 brief 的 pipeline 每项完成即 `log("L4 卡 3/7 ✓ <code>")`(纯 JS 闭包计数,零 token)。
- **phase 归组**:`bash()`/`gate()` helper(:20-24,:36-40)加 phase 参数,`sector-list`(:83)补 phase——所有确定性步骤进进度树分组。
- SKILL/STAGES 加一行:后台跑时可用 `/workflows` 看实时进度树(现有能力,用户不知道)。

### 4.2 Python 层(修 `_stage_timing.json` 有读无写)

根因:workflow 沙箱禁 `Date.now()`(2026-07-07 plan :693 已记),脚本产不出计时 → assemble 墙钟表(assemble.py:349-392)长期全 `—`。

- **assemble 从产物 mtime 链推导**各阶段墙钟并写 `_stage_timing.json`(策略师 = market_view − market_pack;L3 精排 = _l3_judged − _l3_table;L4 = max(details/*.md) − 派发锚;…),绕开沙箱限制,无需任何新写者。
- universe/prelude 入口落 1 行 `_t0.json` 标记,使取数段耗时可与 frame 分离。
- 墙钟表从此自动有数;4.1 的 ETA 文案有据可更;summary 的 effort+墙钟列(07-06 波遗留"下次跑要写")顺带闭环。

## 5. 测试与验收

- 全程 TDD;presence-gated parity 契约照旧(缺数据不加行/不加节)。
- 必改契约测试点名:`test_l4_prompt_cache_prefix.py`(重锚)、`test_agent_defs.py`(l4-card 人设同源)、l4_reuse 复用键版本测试、各账本新列 schema 测试、回填幂等测试。
- 验收 = 下一次真实扫描:①T+2 主尺全链路读数(weights/regime 重算生效)②L4 超短卡样张抽检 ③机构面块在有数据票上渲染 ④进度 log 全程可见+墙钟表有数 ⑤retro@T+2 首日跑通。

## 6. 落地顺序与依赖

```
波 3(独立小)  ────────────────┐
波 2 两探针(report_rc 历史 / fund_portfolio) ──┤ 可并行先行
波 1a(尺子+账本+回填+提案裁决) ──→ 波 1b(卡契约超短化) ──→ 波 2 主体(重审+机构面块)
```

波 2 重审依赖波 1a 的新尺;波 1b 依赖 1a 的 hi_2 列;探针无依赖。

## 7. 开放问题 / 后排(本波不做)

- 超短道的新因子挖掘(隔日反转/竞价/热度类,T+2 尺下真正对口的召回路)——另立 brainstorm。
- momentum/healthy 等 channel quota 在 T+2 重校后由 retro/channel_ledger 数据再裁,不预设。
- L5 报告叙事层的"超短语气"细调(先跑一次看样张再说)。
- OTEL 真计量仍未挂(与本波无关,遗留)。
