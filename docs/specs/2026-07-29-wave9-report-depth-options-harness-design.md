# Wave9 设计稿 —— 研报纵深 × 新闻完备 × 期权地形 × 学习可见 × 现场完备 × 编排收束

> 日期:2026-07-29(晚,07-29 扫描 `reports/scan/20260729_2105` 复盘后 brainstorm 产出)
> 地位:**Wave9 调度总纲**,接棒 07-28 统一优化总纲(Wave1-5 软件完成)与 Wave8 首航修缮波。
> 性质:**详细开发文档,本波只落文档不动代码**(用户裁定);实施计划另起。
> 冲突裁决:与 SKILL.md / STAGES.md / 源码冲突时,以本文档的**裁定记录**为准、机制描述以源码为准。

---

## 0. 裁定记录(2026-07-29 brainstorm,均为用户拍板)

| # | 裁定 | 影响 |
|---|---|---|
| R1 | detail 形态 = **档案驱动增强卡**(决策卡 + 研报体附录),不做全员现场研报、不做分层升格 | 批B |
| R2 | 期权纳入 = **市场 + 风格双层**(QVIX + PCR 进 market_pack;创业板/科创 QVIX 差 = 风偏读数),不做个股映射、不做商品期权 | 批C |
| R3 | 编排收束 = **单工作流全链**(scan-market.js 嵌套拉起每股 l4-stock + assemble 收尾;主会话只转播) | 批F |
| R4 | 自我学习章节 = **确定性 diff + LLM 叙事**(diff 表保真,叙事回写锚块) | 批D |
| R5 | **🚨 不要任何复用**:L4 卡 TTL 复用整个退役,每只 finalist 每日全量重研(今天 601211 复用卡新闻冻在 07-27 = 直接诱因) | 批B |
| R6 | 建档节奏 = **finalist 插队必建**(当日无档案 finalist 盘后优先入队,总帽 ≤3/晚不变) | 批B |
| R7 | P0 = **公告流无权限盲区 / tripwire vs LLM 合并规则 / L2 菜单落刀偏斜**;intel 超帽治理**不是** P0(降 P1) | 批A/B |

批序:**A → B → D → E → C → F**(F 动编排主干、风险最高,压轴;前五批在现行滑窗编排下跑稳)。

---

## 1. 背景:07-29 首航后的七问

用户七问 → 本文六批的映射:

| 用户问题 | 病根探查结论(2026-07-29 现场) | 批次 |
|---|---|---|
| ① 最新 report 有什么优化方向 | 0买8连败结构、主力真在门主导(✗4/7)、菜单落刀 75% vs 全市场 43%、anns 无权限盲区、tripwire vs UW 分歧无契约、intel 7/9 超帽 2 拒稿 | A(+B) |
| ② 期权信息能否纳入 | 可,但 A股无个股期权 → 只有市场/风格层。akshare 65 接口实测可用(QVIX/两所 PCR/中金所);07-29 实测 300ETF QVIX 三日 20.23→22.74→23.34,反抽日隐波走高的信号今天的研判里没有 | C |
| ③ detail 没看到完备新闻(如国泰海通);新闻应先挖「收盘后→现在」 | intel 契约**已有** T0 必查面(≥60% 额度在 T0+24h),新卡新闻是完备的(格力卡有 07-29 19:46 盘后条);漏的是**复用路径**(601211 TTL 复用 → 当日 intel 整段没跑)与**拒稿路径**(002546/603893 超硬顶整稿被拒);且复用门的「无新公告」判据在 anns 无权限日是盲的 | B |
| ④ 每期 report 写「自我学习了什么」 | 素材全落盘(lessons/proposals/changelog/权重快照/t1_review/experiments),但 report 只有存量快照,无本次 diff | D |
| ⑤ trace 现场是否完备、目录怎么组织 | 96 件,平铺 + 3 子目录;**缺** L4 prompt/slim/ensemble/task book/market_pack/config 回显(全在可被重跑覆盖的 `context/scan/<date>/`);subagent transcript 在会话目录、会话删即失 | E |
| ⑥ detail 没有研报的充实和水平 | 卡 ~80 行决策卡形态(超短 T+2 主尺的产物);档案八节有研报素材但今天只覆盖 4/9 只 | B |
| ⑦ 流程 harness + 中途可视化 | 主会话滑窗人肉泵(今天 ~10 次唤醒);Workflow 引擎支持一层嵌套未用;CP 直播靠人肉纪律 | F |

---

## 2. 批A · P0 修缮(裁定 R7)

### A-1 公告流无权限降级盲区

**病根**(07-29 实测):`anns_empty_rate=1.0` 被 self_review 判 `expected/no-permission` 放行——公告面整面缺席被当成正常。它同时污染两处:(i) L4 卡的公告证据面只剩 intel 单腿;(ii) `l4_reuse._has_new_anns` 返回 None →「公告数据缺,依价格门放行」= 复用门对新公告是盲的(R5 复用退役后此点消失,但公告确定性流本身仍喂 L3_news 与卡片证据,值得修)。

**设计**:
1. **B 级兜底源**:`autoresearch/data/sources/` 新增东财公告兜底(候选接口 `ak.stock_notice_report`,备选巨潮 `stock_zh_a_disclosure_report_cninfo`;首跑冒烟裁决用哪个)。契约:主源 tushare `anns_d`(需权限)→ 无权限/空 → 兜底源按 finalists+菜单票过滤;产物行带 `source` 列(`tushare|em|cninfo`)。B 级:兜底也失败 → 降级注记,不阻断。
2. **self_review 判据改口**:`anns去伪` probe 从「空=expected」改为「**双源都空**才 expected;主源空+兜底有料 = info(已兜底);兜底也空且当日有披露日历条目 = warn 公告面单腿」。
3. **权限探针**:主源连续 ≥5 交易日空 → prelude 汇总屏加一行「📡 anns 主源疑无权限,兜底承载中,考虑升 tushare 积分」。

**验收**:构造无权限日回放(mock 主源空),兜底行出现且带 source 列;probe 变异测试——删掉兜底调用,self_review 必须由 info 转 warn(绿灯≠有灯纪律)。

### A-2 tripwire vs LLM 评级冲突卡(呈现契约,不自动执行)

**病根**(07-29 实测):300857 确定性 tripwire(收盘 205.00 < 210.01 → 清仓)与 LLM 双复核终评(Underweight=减仓)同日分歧,报告里两头各说各话,无裁决材料。

**设计**:
1. **冲突判据**(确定性,`decision_finalize`):pinned 票当日 tripwire 触发 ∧ 终评 ≠ Sell → 判「冲突」。
2. **冲突卡渲染**(`report_sections` pinned 节 + detail 卡头 banner):

   | | tripwire(价格尺) | LLM 终评(基本面尺) |
   |---|---|---|
   | 结论 | 清仓(205.00 < 210.01) | Underweight(双复核中位,conv 76) |
   | 判据来源 | 用户预设价格线,不看基本面 | 满卡 DD + 双复核折回 |
   | 历史准确率 | tripwire_watch 账本读数(n<3 禁注,shrink 规则同现行) | sell_review/ensemble 账本读数 |
   | 失效条件 | 收盘收复线上 | 复核依据的驱动被证伪 |

   落款固定文案:「**两把尺子测的不是同一件事,系统不合并——人裁。**」
3. **账本回写**:`pinned_ledger` 加 `conflict` 列;T+2 结算谁对(fwd_2_oc 主尺),累积成上表「历史准确率」的数据源。

**非目标**:不做任何自动合并/自动执行;不改 tripwire 触发逻辑。

**验收**:用 07-29 现场重放 assemble,300857 出冲突卡;无冲突日(tripwire 未触发或终评=Sell)不渲染该框。

### A-3 L2 菜单落刀偏斜:先取证,后立案,不动生产

**病根**(07-29 实测):菜单落刀面(pct_60d<−20)75% vs 全市场 43%——分层采样把菜单配得比市场更"向下"。但**立案时写的诊断动工一查 4/4 全错**是本 repo 的一等坑:先取证。

**设计**:
1. **取证脚本** `autoresearch/scan/l2_knife_audit.py`(零 LLM):回放近 20 scan 日,逐日输出:菜单落刀率 vs 全市场落刀率;分解到 **主排序入选票 vs 风格桶 floor 救回票**(07-29 floor 救回 96 只)的各自落刀率;sector cap 的边际影响。落 `reports/learning/l2_knife_audit.md` + CSV。
2. **三必问前置**(premise-check 纪律):量错对象?(落刀率高是否只反映 risk_off 期 L1 召回池本身更落刀)/时序不对?(菜单 75% 是当日切面,与 L1 分布对照)/已经有了?(menu_health 已有落刀读数,audit 只做归因分解不重造)。
3. **立案不动刀**:若确证偏斜主因=floor 桶定义 → experiment registry `PREREGISTERED` 一个 challenger(floor 权重/桶定义变体),带稳定基线回滚指针,影子 ≥10 交易日再裁。**本波不改 `l2_stratify.select_l2` 生产行为。**

**验收**:audit 报表能把 75% 分解到两个来源且两者之和对得上;registry 里出现该实验的 PREREGISTERED 记录。

---

## 3. 批B · 新闻完备 + 研报级增强卡(裁定 R1/R5/R6/R7)

### B-1 TTL 复用退役(裁定 R5:「不要任何复用」)

**现状**:`autoresearch/scan/l4_reuse.py` 以 Δ价 ±5%(超额口径)/新公告/regime 三门判定复用,复用票直接搬源卡(07-29:601211 复用 07-27 卡)。**复用票不跑 intel → T0/24h 新闻整段盲区**;且公告门在 anns 无权限日是盲的(A-1)。

**设计**:
1. **摘接线**:`scan-market.js` L4-prep 相位删除 `l4_reuse <date> --apply` 步;dispatch-plan 对全部 finalist 出全量任务(不再有 `reused` 数组;workflow 返回契约同步删该字段,消费方 grep 全链:`grep -rn "reused" .claude/workflows/ autoresearch/scan/`)。
2. **昨卡回声**(复用的"评级稳定性"价值由记忆承接,不由跳过研究承接):`scan/l4/prompts.py` 构建 `_l4_prompt_<code>.md` 时,查最近 ≤5 交易日内该 code 的已发布卡(`reports/scan/*/manifest.json` 数据日索引 → `details/`),抽 3 行注入任务包「昨卡回声」块:`日期 | 评级(终评) | 一行多空 | 触发位`。附防锚定文案:「历史判断非今日默认值;若今日翻覆,必须写明触发翻覆的**增量证据**。」
   - **prompt cache 注意**(token-economy-p0 前科):回声块放**逐票段**(本就 per-code),不得插入共享前缀之前;byte-identical 契约测试相应更新。
3. **模块处置**:`l4_reuse.py` 从生产链退役,进入死码候补(独立死码波再删;删 test 前查双职——它的测试是否顺带锁了价格超额口径等 live 契约)。`scan_config.jsonc` 注释区的 `"reuse"` 预留 key 标注「已退役(R5)」。
4. **文档同步**:SKILL.md 步骤 4 删 `l4_reuse --apply` 行;STAGES.md L4 节复用小节改为「已退役(2026-07-29 用户裁定),由昨卡回声承接」。

**成本影响**:每复用票转全量 ≈ +12~20min 墙钟(滑窗内并行,尾部影响小)、+$2~3;近 10 日复用频率 ~1 票/日 → 日增 ~$2~3。

**验收**:重放 07-29,601211 出**当日新卡**(含当日 intel T0 段)且任务包含 07-27 昨卡回声;workflow 返回无 `reused` 字段且 assemble 不再打 ♻️ 头。

### B-2 intel 拒稿改裁稿(P1,裁定 R7 之外的新闻完备补丁)

**现状**:自报查询数 > hard_cap 30 → 整稿改名 `.rejected.md`,card 回退卡内网查 ≤1 条(07-29:002546/603893 中招,新闻面变薄)。「只拒稿不拒票」铁律成立,但拒稿把 T0 增量一并扔了。

**设计**:`autoresearch/scan/l4/intel_guard.py` 超硬顶分支改为**确定性裁剪**:
- 解析事件段表(时效窗列是机器契约),按 `T0 → 24h → 催化挂 → 背景 → >1周` 优先级保留 ≤10 行,背景/>1周 全砍;题材/机构/互动/负面段保留;稿头加戳 `〔已裁剪·自报N超硬顶30·保留T0/24h/催化挂〕`。
- `.rejected.md` 只留给**不可解析稿**(无声明行/事件段表损坏)——不可信的稿仍然整拒。
- 硬顶的"牙"不变:超帽内容被强制丢弃,只是丢弃顺序按时效价值排。
- cap/硬顶数值**不动**(用户未选入 P0,校准另议)。

**验收**:用 07-29 的 002546 原稿(自报 36)回放 guard,产出裁剪稿保留全部 T0/24h 行;喂损坏稿仍走 .rejected。变异测试:把优先级排序删掉,验收必须变红。

### B-3 档案研报体(裁定 R1:档案驱动增强卡)

**现状**:满卡 ~3K/早停卡 ~1.5K 决策卡;档案八节(业务/驱动三情景/风险矩阵/带位…)只以 ~600B 摘要注入任务包;07-29 finalist 档案覆盖 4/9。

**设计**:
1. **输入侧**:任务包对有档案票**内联注入档案四节全文**(§1 业务模型/§2 盈利驱动三情景/§5 风险矩阵/§7 带位;~4-8KB/票,相对 170KB slim 可忽略)。选内联而非让卡 agent 自己 Read:读盘边界不动、无工具调用方差。
2. **输出侧**(`.claude/agents/l4-card.md` 模板改):
   - **满卡 B** 增段 `## 研报体(档案δ)`,四小块:业务叙事一段(档案§1 + 当日增量)/驱动三情景表(标「**哪个驱动今天动了**」)/估值带位对照(档案带位 vs 今日 PE/PB 落点)/风险矩阵(标「哪条今天变了/解除了」)。
   - **早停卡 A** 增微研报块 ≤8 行:业务一句 + 带位一行 + 风险 top2(全誊自档案,零现场成本)。
   - 无档案票:固定一行「研报体:档案未建(已插队今晚建档)」。
   - 「多写不多读」铁律不破:研报体素材=已注入档案,不新增读盘/网查。
3. **行数/格式预算同步改**(「升格新契约必查行数预算,否则预算会把它挤掉」前科):早停卡正文 ≤36 行 → **≤44 行**;满卡 ~3K → ~4.5K;`product_shape_lint` 预算常量同批调。
4. **lint 检查与指令同波落**(instruction-vs-check 纪律,先指令后检查):agent def 先写要求,`product_shape_lint` 再加「有档案票必有研报体段;无档案票必有缺档声明行」。
5. **建档插队**(裁定 R6):assemble/post_run 把当日无档案 finalist 写入 `coverage_pool.json` 的 pending_init,带 `priority=finalist` + `last_seen`;dossier-init 晚间消化按 priority 降序再 FIFO,总帽 ≤3/晚不变。07-29 例:920179/002546/002568 当晚建,003013/000333 顺延。

**验收**:重放一只有档案票(如 000651),满卡带研报体四小块且带位数字与档案一致;lint 对无档案票的缺档声明行通过;变异测试——删模板里研报体段,lint 必须报。

### B-4 📰 新闻头行导航

**现状**:intel 全文附在 detail 卡尾,用户看不到(「detail 为什么没看到新闻」的直接观感来源之一)。

**设计**:`publisher` 拼 detail 时在卡头(标题行后)注入一行:
`📰 T0 增量:N 条 / 盘后无增量 · 24h M 条 · 详见文末情报附录`(N/M 确定性解析自 intel 事件段时效窗列;intel 缺席则写 `📰 情报缺席(<原因:裁剪失败/未启用>)`)。

**验收**:07-29 十卡重放,格力头行 `T0 1 条`(19:46 R290);复用退役后无 ♻️ 卡,每卡必有 📰 行。

---

## 4. 批C · 期权双层(裁定 R2)

### C-1 数据层(`autoresearch/data` 三件套:sources + contracts + lake)

| 数据 | 接口(akshare,keyless) | 说明 |
|---|---|---|
| QVIX 日线 ×5 | `index_option_50etf_qvix` / `300etf` / `500etf` / `cyb` / `kcb` | 07-29 实测可用,自带全历史(300ETF 2778 行);OHLC 结构 |
| 两所每日期权统计 | `option_daily_stats_sse` / `option_daily_stats_szse` | 按标的 ETF 的 PCR(成交&持仓)。**⚠️ 冒烟必查**:实测无参调用返回 2024-06-26 旧切面——date 参数语义/翻页行为要在实施首日验证;若确认是"最新快照"型接口,则按 consensus 模式**每日 1 拉、湖内积累**(prelude/夜间预热接线) |
| 备源 | tushare `opt_daily`(需权限) | 列备源不首发 |

契约:**B 级**(缺=降级注记 `_degraded.options`,不阻断);lake cache key **剥 fields**(窄表毒化前科);中金所 IO/MO/HO、商品期权(GFEX/SHFE)**不做**(裁定 R2 排除)。

### C-2 派生指标(`scan/frame.py`,确定性)

`market_pack.options` 块:

```json
{
  "qvix": {"p300": {"close": 23.34, "pctile_1y": 0.87, "chg_5d": 3.1},
            "p50": {...}, "p500": {...}, "cyb": {...}, "kcb": {...}},
  "spreads": {"growth": "cyb − p300", "kcb50": "kcb − p50"},
  "pcr": {"510300": {"pcr_vol": ..., "pcr_oi": ..., "d5_vol": ..., "d5_oi": ...}, "510050": {...}, "588000": {...}},
  "as_of": "20260729"
}
```

- `pctile_1y` = 滚动 1 年分位;`spreads.growth` = 成长/价值风偏溢价(创业板恐慌相对沪深300 的溢价,涨=成长端在买保险)。
- PCR 湖内不足 1 年时分位列 `null` + 注记(诚实降级,不编)。

### C-3 消费层(防锚定纪律不变:描述性进地形,规范性只进 L5)

1. **market_view §2** 增「期权面」一句(macro-brief 模板改,macro-playbook 末节同步):数字全出自 pack,如「300ETF 隐波 23.3(1年 87 分位),3 日 +3.1;创业板−300 隐波差走阔 = 成长端避险溢价抬升;510300 持仓 PCR 0.84(5日 +0.06)」。**只描述,不给方向指令。**
2. **温度计影子因子**:`learning/` 温度计算里记 `qvix_shadow` 列(分位映射值,**不进权重**);任何进正式权重/regime 判定的变更必须走 experiment registry(`PREREGISTERED → … → ACTIVE`,稳定基线回滚指针)——描述性字段直接上,行为变更走治理,与「实验治理是行为变更唯一生产入口」对齐。
3. L3/L4 **不喂**个股期权字段(A股无个股期权,不造假映射)。

**验收**:连续 5 交易日 `market_pack.options` 非空且 `as_of` 当日;QVIX 收盘与东财页面人工核对 1 次;market_view 期权句里的每个数字能在 pack 找到(self_review 数字对账沿用现有机制);夜间预热清单含 options 拉取。

---

## 5. 批D · 自我学习章节(裁定 R4)

### D-1 learning_diff 模块(确定性,零 LLM)

新模块 `autoresearch/learning/learning_diff.py`,assemble 时运行:

1. **快照**:落 `trace/learning/learning_state.json`:

```json
{
  "as_of_run": "20260729_2105", "date": "2026-07-29",
  "lessons": {"active_ids": [...], "rev": {"id": "hash"}},
  "proposals": {"open_ids": [...], "status": {"pr_id": "state"}},
  "weights": {"snapshot": "543d67f8", "top_deltas_pending": null},
  "experiments": {"exp_id": "state"},
  "calib": {"target_hit_30d": 0.44, "l3_trend_overturn": 0.32, "gate_precision": 0.61, "gate_overkill": 0.23},
  "t1_last": {"t": "2026-07-28", "right": 1, "wrong": 0, "surprise": 1, "new_candidates": 6}
}
```

2. **diff**:与**上一个已发布 run** 的 learning_state 对比(定位:reports/scan 按 manifest 数据日倒序取前一份;首个 run = 全量基线,diff 标「首基线」),产出 `trace/learning/learning_diff.json`:新增/修订 lesson(带**来源链**:t1_review/retro/feedback/手工)、新立案与状态迁移、权重快照变更(引 changelog_ledger 的 from→to)、实验状态迁移、校准读数 Δ、昨日 t1 摘要。
3. **渲染**(`report_sections`):summary.md 新节:

```markdown
## 🧠 本次自我学习(进化日志)        ← 以下三行为格式示例,数字非实测
| 变化 | 内容 | 来源链 |
|---|---|---|
| 新经验 ×1 | pr_20260729_002 β剥离归因(|z|<1 禁写论点兑现) | t1_review 07-28(2日自动立案) |
| 权重 | f3440ac5 → 543d67f8(top-k 因子Δ列自 changelog) | retro.recalibrate 07-28 |
| 校准Δ | trend lane 翻案率 32% → 本次重算值 | cross_calib |
<!-- learning-narrative:start -->
(待主会话 CP7 回写进化叙事;缺=本次无叙事)
<!-- learning-narrative:end -->
```

### D-2 LLM 进化叙事(锚块回写,仿 run-observation 先例)

- **CP7 契约**(SKILL.md 直播契约表加一行):主会话读 `learning_diff.json`,写 3~5 句叙事(学到什么/**怎么学到的**(证据链:哪次预测错→哪个账本量出→哪条机制蒸出)/对明天扫描的影响)回写锚块内。
- **防编数 probe**(self_review,warn 级):叙事段出现的 lesson id / pr id / 百分比数字必须能在 learning_diff.json 序列化值中找到;找不到 → warn「学习叙事含 diff 外数字」。
- diff 为空的日子:锚块写「本次无学习增量(diff 为空)」——**0 增量是合法输出**,不许编。

**验收**:连续两日重放,第二日 diff 只含增量;变异测试——伪造叙事塞一个 diff 外数字,probe 必须 warn。

---

## 6. 批E · trace 现场完备化(方向⑤)

### E-1 目标与判据

**现场完备 = 任一历史 run 可离线回答:每个决策当时看到了什么(输入)、谁产出的(producer)、判据为何(中间物)。** 07-29 缺口:L4 prompt 任务包/slim/ensemble 复核原稿/task book/market_pack/market_view/config 回显都只在 `context/scan/<date>/`(重跑即覆盖);subagent transcript 在会话目录(会话清理即失)。「产物能证明跑过什么、不能证明没跑过什么」——现场件是回放器与 retro 诊断的地基。

### E-2 目录重组(每阶段一夹 + L4 每股一夹)

```
reports/scan/<run_id>/trace/
  manifest.json  run_contract.json  artifact_index.json     # 根:身份与索引
  L0_universe/   meta.json, universe.csv
  L1_recall/     top1000.csv, channels.csv, scored_full.csv, weights_used.json
  L2_menu/       top200.csv, menu_health.json
  L3_rank/       evidence表, judged.json, pass1_cut.csv, bench.csv, repair_*.json
  L4_research/<code>/   prompt.md, slim.md, intel.md(含裁剪/拒稿态), card.md(原稿),
                        ensemble_*.json, task_record.json      # ← 现在全缺,本批补齐
  L5_assemble/   decision_records.json, stage_results/, gates, run_health.json
  market/        market_pack.json, market_view.md, sector_briefs/, calendar.csv,
                 user_config_echo.json, _prelude_summary.md
  learning/      learning_state.json, learning_diff.json, weights快照指针, 当日lesson注入清单
  agents/        journal.jsonl(各 workflow 副本), final_messages 索引       # ← 会话删即失,本批收割
```

### E-3 收割器与索引

1. **`autoresearch/trace/scene_harvest.py`**(assemble 末尾调用,幂等):按上表映射把 `context/scan/<date>/` 现场件**复制**进 report trace(report 目录不可变,context 会被重跑覆盖——这是复制而非移动的理由)。
2. **artifact_index v2**:逐文件 `{path, stage, producer, sha256, bytes}`;消费者**经 index 解析路径**,对旧 run(平铺布局)fallback 旧路径——新旧兼容靠 index,不靠猜。
3. **agents/ 收割**:`trace/usage_harvest` 扩参 `--scene-out`,顺路复制各 workflow 的 `journal.jsonl` + 抽每 agent 最终消息(subagent-report-recovery-jq 配方落成代码);在 CP7 由主会话执行(需 sessionId,只能主会话做)。
4. **消费者迁移**(「发现一处必 grep 全部消费者」):`grep -rn "trace/\|_l3_judged\|L2_gbdt\|stage_results" autoresearch/ .claude/` 全量清单进实施计划;回放器 PIT 六条测试锁住迁移不破位。
5. **体积与保鲜**:~3-4MB/run(gitignored);提供手动 `scene_harvest --prune-days 90`(slim 置换为 {sha256,bytes} 存根),**不做自动清理**(YAGNI,先观察)。

**验收**:对 07-29 run 重放收割,`L4_research/300857/` 齐 6 件且 sha256 与 context 侧一致;删 context/scan/2026-07-29 后,仅凭 trace 能重建该票「输入→中间→终评」链;旧 run(20260728_2319)经 index fallback 仍可被 render/retro 读通。

---

## 7. 批F · 单工作流全链(裁定 R3;风险最高,压轴)

### F-1 现状与目标

现状:scan-market.js(前段)→ **主会话滑窗人肉泵**(收完成通知→补派下一只,07-29 实测 ~10 次唤醒)→ 主会话 assemble 收尾。主会话是编排的**单点**:睡着/断线,流水线停。

目标:scan-market.js 一个 workflow 走完 Prelude→L3→L4(嵌套每股 l4-stock)→assemble/GATE4/scene_harvest;主会话降级为**观众 + CP 转播员 + CP7 收尾员**。唤醒 ~10 → ~3;主会话死掉流水线照跑。

### F-2 嵌套派发设计(scan-market.js 改造)

```js
// L4 相位(伪码):替换现返回 dispatch 交接
const caps = await gate('l4-caps', `${R} autoresearch.scan.l4_tasks batches ${date}`, CAPS, 'L4')
const queue = orderForDispatch(dispatch, meta)        // 📌 pinned 与预计最长者先行(现纪律不变)
const results = []
let inFlight = 0, i = 0
await new Promise((done) => {
  const pump = () => {
    while (inFlight < caps.effective_cap && i < queue.length) {
      const code = queue[i++]; inFlight++
      workflow({scriptPath: '.claude/workflows/l4-stock.js'},
               {date, code, name: meta[code].name, sector: meta[code].sector,
                pinned: meta[code].pinned, dossierSummary: meta[code].dossier_summary, cfg})
        .then(r => results.push(r))
        .catch(e => results.push({code, task_status: 'FAILED', error: String(e)}))  // 单票失败不连坐
        .finally(() => { inFlight--; pump() })
    }
    if (inFlight === 0 && i >= queue.length) done()
  }
  pump()
})
// L5 相位:assemble → GATE4 → scene_harvest(bash/stageGate 壳,判据仍全在确定性 CLI)
```

要点:
- **每股一 workflow 裁定(fb_20260714_003)不破**:嵌套子 = 原 l4-stock.js 原样,一层嵌套合法(引擎限一层,l4-stock 内部无 workflow() 调用,合规)。
- 滑窗帽仍读 `l4_tasks batches` 的 `effective_cap`(外部资源帽 tushare/web 的唯一事实源);子 workflow 共享父并发帽与 token budget,天然不超。
- 失败语义沿用 task book:瞬时错误(RATE_LIMIT/CONNECTION/TIMEOUT)子链内已有第 2 次尝试;子级 throw → 记 FAILED 继续,收尾 assemble 前若有 FAILED → GATE 判 degraded(现语义)。
- **恢复**:workflow 中途死 → `resumeFromRunId` 续跑,已完成子调用走缓存;或退回滑窗模式手动重放未完成批(task book 不变,两条路都在)。

### F-3 过程直播不降级(2026-07-12「主对话一片空白」反馈是硬约束)

**新 CLI `autoresearch/scan/progress_feed.py`**(确定性,替代人肉 CP 泵 + 吸收 l4_watch):

- **判据铁律**:只播**落盘终态事实**——`stage_results/*.json`(每段 started/result)、`_l4_tasks.json`(任务终态)、`_prelude_summary.md`(存在即全文可引)。**不做存在性反推**(前任 scan.progress 靠猜误报三次退役;l4_watch「只认 task book」的纪律是唯一幸存者,本 CLI 继承它)。
- 事件行(一行一事件,Monitor 友好):`CP0 regime=risk_off 温度=60.7` / `CP1 GATE1✓ L0=4082→L1=1001→L2=203(汇总屏见 _prelude_summary.md)` / `CP3 GATE2✓ 入围N只:...` / `CP4 派发N股 cap=4 📌3只` / `🃏 k/N 代码 名称 → 评级`(l4_watch 逻辑并入)/ `↩️ 折回待结算` / `CP6 全终态 评级分布...` / `❌ FAILED/GATE✗ 行`(失败签名必须覆盖——静默≠成功)。
- assemble stage_result 落盘 → 自动退出。
- **主会话新姿势**(SKILL.md 流程节重写):发起 workflow → 挂 1 个 Monitor(progress_feed --watch)→ 事件到就转播一行(纯转播,不派发、不产出分析)→ workflow 完成通知 → CP7(usage_harvest + 学习叙事回写 + 终报)。
- `l4_watch` CLI 退役并入 progress_feed(读盘函数复用,退役走死码纪律)。

### F-4 回滚杆与风险

- `scan_config.jsonc` → `performance.orchestration: "nested" | "sliding"`,默认 `nested`;`sliding` = workflow 返回 dispatch 交接的现行为(整段保留)。**性能开关不拥有评级**铁律沿用:两种模式 finalist/rubric/评级/BUY 数量必须逐字节等价(对照测试)。
- 实施首跑冒烟清单(设计即写死,防「操作建议未跑通就进文档」前科):① 嵌套子 workflow 的 journal.jsonl 是否归并父 transcript dir(scene_harvest 收割路径依赖它);② resumeFromRunId 对嵌套子的缓存语义;③ 子 workflow 内 bash 壳的 caps 读数与父共享并发的实际并行度;④ progress_feed 在 0 事件间隔 >20min 时的心跳行(Monitor 静默与挂掉不可区分——加 `⏳ 心跳` 行)。
- **js 探针纪律**:workflow js 改动用 AsyncFunction 探针冒烟(`node --check` 对 ESM+顶层 return 零鉴别力,永不变红)。

---

## 8. 批序 / 依赖 / 成本

| 批 | 内容 | 依赖 | 规模 |
|---|---|---|---|
| A | anns 兜底源+probe / tripwire 冲突卡 / L2 落刀取证+立案 | 无 | S/S/M |
| B | 复用退役+昨卡回声 / 拒稿改裁稿 / 档案研报体+lint / 插队建档 / 📰 头行 | A-1(公告兜底供卡证据) 弱依赖 | M |
| D | learning_state/diff / 🧠 节 / 叙事锚块+probe | 无 | M |
| E | trace 重组 + scene_harvest + index v2 + agents 收割 + 消费者迁移 | D(learning/ 子目录归它) | M/L |
| C | options source+湖 / frame 派生 / market_view 句 / 温度影子+registry | 无(独立) | M |
| F | progress_feed / 嵌套改造 / assemble 入流 / 回滚杆 | E(scene_harvest 已在收尾链)、B(dispatch 契约变了) | L |

成本影响汇总:复用退役 +$2~3/日;研报体 +30~50% 输出 token(输出侧,便宜);期权/学习/trace 零 LLM;批F 省主会话唤醒 ~$2/run。净 ≈ +$3~5/run。墙钟:复用退役 +~15min 尾巴(滑窗吸收),批F 不改单票时长。

---

## 9. 验收纪律(全批通用)

1. **变异测试**:每个新 probe/lint/gate 落地时做一次「删掉被测行为,检查必须变红」;js 改动用 AsyncFunction 探针。
2. **对照等价**:批F 两种 orchestration 模式对同日数据产物逐字节对照(评级/finalist/BUY 数量必须相同)。
3. **活体验收**:每批留「下次真扫描看什么」清单(如:B 批看 601211 类回头票出当日新卡;D 批看 🧠 节 diff 与叙事;F 批看唤醒次数 ≤3)。
4. **文档同步清单**:SKILL.md(步骤 4/5、直播契约表、CP7)、STAGES.md(L4 复用节改退役、期权节新增、trace 节)、`.claude/agents/l4-card.md`(研报体模板)、`.claude/agents/l4-intel.md`(裁稿语义)、macro-playbook 末节(期权句)、`.claude/agents/macro-brief.md`。**编辑 skill 文档前重读**(会被外部改)。
5. **计量**:每批合并后跑一次 usage_harvest 对比,研报体/复用退役的成本读数进 run-observation,10 次真实扫描前 IMMATURE 口径不变。

## 10. 非目标(本波明确不做)

- 个股期权因子/映射(A股无个股期权)、商品期权链(R2 排除)、中金所指数期权首发。
- intel cap/hard_cap 数值再校准(用户未选入 P0)。
- L2 采样生产行为变更(A-3 只取证+立案)。
- tripwire 与 LLM 评级的任何自动合并/自动执行。
- 全员现场研报、Top-N 升格 full(R1 已裁)。
- 实施与代码——**本文档是本波唯一交付物**。

## 11. 开放问题(实施前需一次冒烟裁决)

1. `option_daily_stats_sse/szse` 的 date 参数语义(快照型则走湖积累模式)。
2. 公告兜底源选型:东财 `stock_notice_report` vs 巨潮 disclosure(可达率/字段完整度实测定)。
3. 嵌套 workflow journal 归并路径(F-4 冒烟清单①)——影响 scene_harvest 的 agents/ 收割实现。
4. 昨卡回声的回看窗(≤5 交易日)是否够:pinned 票 gap >5 日的场景(节后)是否放宽到 ≤10。
