# scan-market — A股全市场漏斗扫描(挖掘值得买入的股票 / 板块)

> 设计文档 · 2026-06-20 · 状态:待评审
> 关联:复用 `analyze-ticker` / `analyze-ticker-lite` skill 及其数据层(`harvest_context.py` / `assemble_report.py`)。

## 1. 动机与核心原理

**需求**:一次扫描全 A股(~5,400 只),挖掘值得买入的**个股**与**板块**。

**核心矛盾**:对 5,400 只逐个跑深度报告 ≈ 每只十万级 token × 5,400 ≈ 几亿 token,不可行(成本/时间/限流)。

**核心原理 —— 分层漏斗**:用**免费的确定性筛选**把 5,400 砍到 ~100,再用**低成本 LLM 分诊**收到 ~30,只对这 ~30 只 finalists 跑 **analyze-ticker-lite 决策卡(每只 ~20% token)**。
**token 只跟"最后深挖几只"成正比,与全市场规模无关。** 深挖数量 = 用户的预算旋钮。

**与项目 philosophy 契合**:筛选层就是"更多的免费确定性数据层"(与 `harvest_context.py` 同构),喂给 analyze-ticker 家族。akshare 已是(venv-only)依赖,bulk 端点现成。

## 2. 架构:新 skill 作为编排器

`scan-market` 是一个新的、独立的 skill,**编排** L0–L4;它在 L3b **委托** analyze-ticker-lite 出每只 finalist 的决策卡。**打分(L1/L2)与分诊(L3a)都不依赖 analyze-ticker——那是独立代码;依赖只发生在 L3b。**

```
scan-market (new skill, orchestrator)
  L0 universe        ─┐
  L1 四透镜打分        ├─ scripts/screen_market.py   (确定性, 零 LLM)
  L2 板块聚合         ─┘
  L3a 轻量 triage     ── 批量分诊 ~100→~30 (只吃已拉 bulk, 不重 harvest)
  L3b 决策卡深挖      ── ~30 逐只委托 analyze-ticker-lite (slim harvest → 决策卡, subagent 扇出)
  L4 综合            ── scripts/assemble_scan.py   (汇总 sector + 个股 → scan_summary.md)
```

analyze-ticker(全量)/ analyze-ticker-lite(决策卡)各保持单一职责,一行不改。skill 间通过接口解耦:
**接口 = finalist 清单(ticker + 标签)→ analyze-ticker-lite → `reports/<date>/<ticker>/complete_report.md`。**

## 3. 漏斗分层(L0–L4;L3 分 a/b 两档)

| 层 | 做什么 | 工具 / 端点 | 成本 |
|---|---|---|---|
| **L0 universe** | 全 A股快照 + 业绩/资金流/行业富化;剔 ST/停牌、市值地板 | akshare `stock_zh_a_spot_em` · `stock_yjbb_em` · `stock_individual_fund_flow_rank` | API 调用(非 token);几次 bulk 调用覆盖全市场 |
| **L1 四透镜并行** | 动量/成长/价值/反转各自"门+打分",各出 top ~50 | 纯 pandas | ~0 |
| **L2 板块聚合** | survivors 映射申万行业;板块按广度+跨透镜+资金流+板块动量排名 → top 3–5,板块内 survivors ~100 | pandas + `stock_board_industry_*` | ~0 |
| **L3a 轻量 triage** | top 板块内 ~100 survivors 批量分诊(只吃已拉 bulk,不重 harvest)→ ~30 | Claude 批量(in-session) | **低**(紧凑数据) |
| **L3b 决策卡深挖** | 最终 ~30 逐只跑 **analyze-ticker-lite**(`--slim` 取数 + 单张决策卡,subagent 扇出) | `harvest_context.py --slim` + Claude(lite-playbook) | **~20%/只(实测 slim=全量 20.4%)** |
| **L4 综合** | 板块结论 + 个股五档 + R:R buy-list → 一页 summary | `scripts/assemble_scan.py` | 小 |

**数量级**:5,400 →(L1)~150 →(L2 top板块)~100 →(L3a triage)~30 →(L3b lite,每只 ~20%)≈ **~30 万 token**,较"全跑 5,400 全量"约 **~700×** 削减。

## 4. L1 打分逻辑(核心)

### 4.1 定调
L1 的任务是**高召回 + 可解释**,不是"验证过的 alpha 模型"。真正的 alpha 判断在 L3b。**没有回测,不追求最优权重**——求稳健、透明、可调。

### 4.2 共享打分骨架(四透镜通用)
1. **"门 → 分"两段**:先过硬门(流动性地板、剔 ST/停牌、透镜专属 disqualifier),过门的才打分。
2. **横截面分位,不用绝对阈值**:每个子因子转百分位 [0,1]、方向对齐。**估值类按申万一级行业内分位**(PE/PB 只有同业可比),**动量/资金类按全市场分位**。
3. **缩尾 + 缺失不插补**:1/99 缩尾;缺核心因子 → 该透镜内剔除,不插补(插补=造信号)。
4. **复合分 = 子因子分位加权和 → 0–100**;**同时输出每个子因子分位 + 过了哪些门**(可解释,喂 L2/L3)。
5. **只用 bulk 端点,绝不对 5,400 逐个拉历史**(那是 wall-clock/限流杀手)。需历史的因子用 snapshot 自带 `60日涨跌幅`/`年初至今涨跌幅`/`量比` 当代理;真历史留给 L3b 的 ~30 只。

### 4.3 四透镜:门 + 默认权重(松门,高召回)

| 透镜 | 松门(高召回) | 子因子(默认权重,和=100) | 惩罚/约束 |
|---|---|---|---|
| **趋势动量** | 60日 或 YTD 涨幅>0、非 ST/停牌 | RS(60日.6+YTD.4)**35** · 主力净流入(5/10日)**30** · 趋势结构代理 **20** · 量能(量比/换手)**15** | 60日涨幅顶 5% / RSI 过热 → **−15 分**(防抛物线顶) |
| **成长加速** | 净利YoY>0 **或** 营收YoY>15%;CFO>0;营收≥3亿/季 | **加速度(最新YoY−上期YoY)30** · 净利YoY **25** · 营收YoY **20** · ROE **15** · 质量(CFO/NI·毛利)**10** | PE 行业分位过高 → 估值惩罚 |
| **价值低估** | PE>0、非 ST、营收YoY>−15%、ROE>0 | PE(行业内低分位)**30** · ROE **25** · PB(行业内)**20** · 股息率 **15** · 利润率 **10** | 全部行业内分位;崩塌门挡陷阱 |
| **困境反转** | **(边际改善 ∨ 资金确认)至少一项亮**;非退市;亏损未扩大 | **边际改善 35** · 超跌 **25** · 资金确认 **25** · 底部结构 **15** | 仍在 freefall 无拐点 → 出局 |

**有意为之的偏重**:动量重资金(A股趋势资金推动)、成长重加速度(二阶导=alpha)、价值重 ROE(防"便宜因为烂")、反转门+权重双重强调"必须有拐点"。

**高召回落地**:每透镜 top ~50 → 去重后 ~150 进 L2。权重为起步默认,首轮产出后微调。

### 4.4 数据可行性与坑
- 动量来自单次 `stock_zh_a_spot_em` 快照 + 资金流排名;成长/价值/ROE/毛利/CFO 来自 `stock_yjbb_em`(已实测:列名与本节一致)。
- **坑① 扣非净利** bulk 可能取不到(在财务摘要,逐个拉太贵)→ L1 用头条净利 + 毛利/CFO 质量门补偿,扣非确认留 L3b。
- **坑② 多头排列/精确 52周回撤**需历史 → L1 用 60日/YTD 涨跌幅代理,L3b 的 slim snapshot 有当前指标值。
- **坑③ akshare 端点/列名跨版本漂** → 防御性取列(`_col`)+ 缓存快照到 `context/scan/<date>/`;spot/资金流端点偶发断连,`_ak_call` 3 次重试 + 降级。
- **坑④ 业绩披露滞后** → 用最近可得报告期(脚本按分析日推算),输出标注 staleness。
- **坑⑤ 股息率不在 bulk 端点** → 价值透镜暂不含;`所处行业` 对北交所/部分票为空 → 归"未分类"(板块榜剔除)。

## 5. L2 板块聚合("先板块"的核心)

- **行业口径**:申万一级(或 akshare 现成行业字段),全程一致。
- 每个行业计算:
  - **广度 breadth** = 该板块 L1 survivors 数 / 板块成分数(板块整体走强,而非一只独苗)。
  - **跨透镜 cross-lens** = 该板块被几条不同透镜命中(被 3 条命中 > 被 1 条;这是确信度信号)。
  - **资金流 aggregate** = 板块成分主力净流入中位/合计(或板块级资金流端点)。
  - **板块动量** = 板块指数涨跌幅。
  - **质量** = survivors 的 ROE/成长中位(挡"全是低质便宜货"的板块)。
- **板块分** = 上述加权(广度 + 跨透镜 权重最高)→ 排名取 **top 3–5 板块**。
- 输出:板块强弱排名表 + 每个板块下挂的 survivors(top 板块内合计 ~100,喂 L3a)。

## 6. L3:轻量 triage(L3a)→ 决策卡深挖(L3b)

L3 分两档深度,**只有最终 ~30 才 harvest(且是 `--slim`)+ 写卡**。

### 6.1 L3a — 轻量 LLM triage(~100 → ~30,低 token)
- **输入**:L2 top 板块内的 ~100 survivors,每只带 L1/L2 **已拉好的紧凑 bulk 行**(价/PE/PB/营收·净利 YoY+加速度/ROE/主力净流入/板块/命中透镜/L1 复合分)。**不重新 harvest。**
- **过程**:Claude **批量**读(每批 ~20–30 只),对每只给 `倾向(看多/中性/回避) · 一句理由 · triage 分`;对少数高潜力但边界的名字可触发**快速 WebSearch**(近期催化/利空),标注『实时网查』。这一步加的是确定性分给不了的**定性判断**(增长像不像账面、价值是不是陷阱、动量是否已透支)。
- **保板块结构**:分诊在板块桶内进行,最终 ~30 仍跨 top 板块分布——配额:top ~5 板块各取 triage 头部 ~5–6 + ~2–3 板块外单透镜超星外卡,不让某板块独吞。
- **输出**:`context/scan/<date>/finalists.csv`,~30 只,每只带 `板块 · 命中透镜 · L1 分 · triage 倾向/理由`。
- **可选加速**:并行 workflow 把 ~100 拆给多个 triage agent(需用户显式开启,非默认)。

### 6.2 L3b — analyze-ticker-lite 决策卡(最终 ~30,~20% token)
- 逐只走 **analyze-ticker-lite**:`harvest_context.py <ticker> <date> --slim`(只取决策块,实测 = 全量的 **20.4%**)→ Claude 按 `lite-playbook.md` 产出**单张决策卡** → `reports/<date>/<ticker>/complete_report.md`。
- **必须 subagent 扇出**:每只一个 subagent(独立 context),只回传评级/目标/R:R;否则 30×context 撑爆主线窗口。可选 workflow 并行。
- **lite vs full**:lite 出"买不买"的卡;某只想下重注,再单独对它跑**全量 analyze-ticker** 看证据附录。
- **模型**:建议 Opus。

## 7. L4 综合(`scan_summary.md`)

`scripts/assemble_scan.py` 读 ~30 份 finalist 决策卡(五档评级 / 目标 / R:R)+ L2 板块排名,产出一页:
- **漏斗计数**:5,400 → L1 ~150 → L2 ~100 → L3a ~30 → 报告数,扫描日期、universe 规模。
- **板块结论**:每个 top 板块为何强(广度/跨透镜/资金/动量)、所处周期、龙头是谁。
- **个股 buy-list**:~30 只按确信度排序,每只:五档评级、目标价/R:R、透镜标签、一句话 thesis,链接到决策卡。
- **诚实局限**:筛选为启发式(无回测)、数据滞后、A股涨跌停可交易性等。

## 8. 新增/复用组件

**新增**
- `.claude/skills/scan-market/SKILL.md` + `screening-playbook.md` — 编排 + L1 规格 + L3a 分诊规则 + L3b 委托 + L4。
- `.claude/skills/analyze-ticker-lite/SKILL.md` + `lite-playbook.md` — 单只决策卡(slim + 卡,~20% token)。
- `scripts/screen_market.py` — L0–L2 确定性筛选。零 LLM。
- `scripts/assemble_scan.py` — L4 汇总。
- `harvest_context.py --slim` — slim 取数模式(加性改动)。

**复用(不改)**
- `scripts/harvest_context.py`(全量路径)、`scripts/assemble_report.py`、`.claude/skills/analyze-ticker/engine-playbook.md`(用户想下重注时跑全量)。

**产物目录**
```
context/scan/<date>/   universe.csv  lens_*.csv  sectors.csv  finalists.csv  meta.json
context/<ticker>_<date>_slim.md      # L3b 每只 slim context
reports/<date>/<ticker>/complete_report.md   # lite 决策卡(每只 finalist)
reports/scan/<date>/scan_summary.md  # L4 综合
```
(`context/`、`reports/` 已 gitignore。)

## 9. 锁定的决策(来自 brainstorm)

| 维度 | 决策 |
|---|---|
| 选股风格 | **全部 4 种**(动量/成长/价值/反转)→ 4 条独立透镜,不取交集 |
| 产出形态 | **先板块后个股** |
| 深度分析量 | **中等 ~30 只** |
| 权重 | **作者给合理默认**(见 §4.3),首轮后用户微调 |
| 门松紧 | **高召回(松门)**,每透镜 top ~50 |
| 100→30 收口 | **轻量 LLM triage**(自动,低 token,只吃已拉 bulk,不重 harvest) |
| 最终深挖深度 | **analyze-ticker-lite 决策卡**(~20% token:slim 取数 + 只写卡);想下重注的票再单独全量 |
| 行业口径 | 申万一级(或 akshare 现成),全程一致 |
| universe 默认 | 剔 ST/*ST/退市/停牌/次新(<60交易日);市值地板 ~30亿;北交所默认排除 |

## 10. 分两期落地

- **Phase 1(零 token,已完成)**:`screen_market.py`(L0–L2)→ 板块榜 + ~100 排序 survivors。selftest + ruff 通过。
- **Phase 2(已完成)**:L3a 分诊(playbook 驱动)+ L3b `analyze-ticker-lite`(slim + 卡,~20%)+ `assemble_scan.py`(L4)。
- 待**用户环境**首次真跑验证实时取数(本地 akshare 网络可用)。

## 11. 风险 / 开放问题

- akshare bulk 限流/版本漂 → 防御取列 + 缓存 + 重试(`_ak_call`)。
- 4 透镜可能产出几乎不重叠的 4 张榜 → 这本身是信息(市场分化),最终 30 的合并规则需清晰(§6.1 配额+外卡即是)。
- 业绩滞后期 → 标注 staleness,不当实时。
- as-of 分析日、无未来数据(沿用 analyze-ticker 铁律)。
- **开放**:市值地板 30亿 vs 50亿;北交所纳入与否(均为 `screen_market.py` 开关);L2 板块分各因子权重(待 L2 实现细化)。

## 12. 不在本期范围(future)

- L3a triage 与 L3b lite 的并行 workflow 化(默认 in-session / subagent;workflow 需显式开启)。
- slim 进一步瘦身(如裁 news 窗口)以压到 <20%。
- 回测/因子有效性验证(当前为启发式粗筛)。
- 每日 cron 自动刷新 shortlist + 仅对新进者深挖。
- 港股/美股版(本期仅 A股,因依赖 akshare 中国数据)。
