# UZI-Skill 增量集成设计 — 2026-06-20

> 把 UZI-Skill(单票深研插件)的**增量数据 + 分析思维**吸收进本项目的三层:L1 召回因子、
> L4 决策卡、L3 判断机制。核心原则:**UZI 扩张假设空间,本项目的闭环(factor_lab rank-IC +
> retro + 反馈记忆)负责证伪**——加什么因子/透镜不拍脑袋,让 T+1 IC 判生死。

## 1. 定位与边界
- UZI = 单票机构级深研(≈超级 analyze-ticker);**无** scan / 无 IC 校准 / 无闭环。与本项目**互补**。
- ⚠️ **环境硬约束**:UZI 大量走东财 push2 / 雪球 cookie / 股吧·淘股吧爬虫,而**本机封 push2**。
  故**借其"要什么数据 + 怎么想",不搬其 fetcher**;能 tushare 化的优先,爬虫类降级为弱信号。
- **零付费 LLM 不变**:新增数据走 tushare(主)/akshare(可用子集);persona/辩论/估值推理由 Claude 在 session 内做。

## 2. 数据可达性实测(2026-06-18,本机 tushare token)
| UZI 数据 | 端点 | 实测 | 覆盖 | 归属 |
|---|---|---|---|---|
| 融资融券(个股) | tushare `margin_detail` | ✅ 1980 行 | 两融标的(~1980) | **L1 旗舰因子** |
| 大宗交易 | tushare `block_trade` | ✅ 165 行/日 | 稀疏(当日有大宗的) | L1 事件因子 / L3 |
| 龙虎榜席位名 | tushare `top_inst`+`top_list` | ✅ exalter 席位 | 稀疏(当日上榜~100) | L3/L4 席位识别 + L1 事件 |
| A股原生财报 | tushare `fina_indicator`/`dividend` | ✅ | 全市场 | **L4 财报块** |
| 机构季度持仓 | akshare `fund_hold_detail` | ❌ 报错(env) | — | **砍掉**(取不到) |
| 舆情热度 | akshare `stock_hot_rank_detail_em` | ⚠️ 能跑但脆弱 | 个股 | L4 弱信号(降级) |

## 3. Track A — 新增 L1 候选因子(推荐先做,纯量化可证伪)
**机制**:把市场级可算的新数据接成 `factor_lab` 的候选因子 → 跑 T+1 rank-IC → **只有 IC 显著的进 `screen_market` 复合分**(经收缩 + retro 持续校准)。新增即假设,IC 即裁决。

**新候选因子**(方向先验留空,由 IC 符号定):
- `rz_ratio` = 融资余额 `rzye` / 流通市值(融资杠杆水平)。
- `rz_buy_intensity` = 当日融资买入 `rzmre` / 成交额(融资买入强度)。
- `rz_momentum` = 融资余额 5 日变化率(需 D 与 D-5;harvest 已逐日缓存可算)。
- `block_net_ratio` = 大宗净买额 /(当日成交额)(稀疏,NaN 多)。
- `block_premium` = 大宗均价 / 当日收盘 − 1(折溢价;稀疏)。
- `lhb_inst_net` = 龙虎榜"机构专用"净买 / 成交额(稀疏事件)。
- `youzi_active` = 龙虎榜知名游资席位净买(需 seat_db;稀疏事件)。

**接入点**(全在 `factor_lab.py` 现成骨架上扩展):
1. `_FIELDS` 加 `margin`(margin_detail 字段)、`block`(block_trade)、`top_inst`。
2. `harvest` 对每个成型日 F 多缓存这 3 个端点(daily/daily_basic 已缓存,增量仅新端点)。
3. `factor_frame` 计算上述因子(融资类高覆盖;大宗/席位 merge 后 NaN-heavy 用 fillna 策略)。
4. `CANDIDATES` 加新因子;`eval` 出 IC 表 → 人看哪些有 T+1 信号。
5. 有信号者纳入 `screen_market._factor_groups`(新组或并入资金组)+ `calibrate` 重标定。
**产物**:`ic_table.csv` 新因子行 + 一句结论(进/不进复合分)。**稀疏事件因子**(大宗/席位)若 IC 噪声 → 退到 L3 证据,不进 L1。

## 4. Track B — 增强 L4 决策卡(单票深度)
给 `harvest_context.py`(及 analyze-ticker 卡)加 UZI 透镜,A股优先 tushare:
- **A股原生财报块**:`fina_indicator`(5y ROE/毛利/负债率/ROIC)+ `dividend`(分红史/股息率)——补 yfinance 对 A股的稀疏。
- **游资席位识别**:`top_inst`/`top_list` + `seat_db`(从 exalter 累积知名游资/机构名)→ "谁在买/卖、机构 vs 游资"。
- **融资融券块**:`margin_detail` 近 20 日融资余额趋势(杠杆资金进出)。
- **DCF·comps 机构估值**(高确信票):简版 DCF(WACC 敏感性 5×4)+ 行业 PE/PB 分位——给 analyze-ticker(全量),不进 lite。
- **供应链/客户集中**:`zygc`(主营拆分)+ websearch(客户/供应商集中度)——降级标注。
- **杀猪盘 trap 检测**:UZI 8 信号的轻量版(放量滞涨/股东户数激增/质押高/龙虎榜游资对倒)——L3/L4 风险透镜。
- **舆情热度**(弱信号):akshare 雪球热度,降级标注、不进硬打分。
**复用闭环**:卡片仍走 `load_ohlcv`(tushare 价格真值)+ 复用 L1 因子行(已做的去冗余);新块只补 L1 没有的。

## 5. Track C — 增强 L3 判断机制(多 persona 对抗 + 硬门)
把 L2/L3 单一"资深投资师" → UZI 式**多流派对抗**:
- **persona 面板**:每只 finalist 用 N 个 subagent 扮不同流派(价值/成长/游资/quant/风险官),各自引因子下判断。
- **「矛盾必须呈现」**:多 persona 分歧大 → 报告**强调分歧本身是信息**,不和稀泥(写进 `assemble_scan` 骨架)。
- **self-review 硬门**(UZI 13 检的本地版):发布前机械检查——行业冲突/覆盖率不足/因子方向违背 `lessons`(如又把 winner_rate 满当利好)/数据缺口过大 → **不达标不发布**,先修。
- **证据分级**:硬披露(财报/龙虎榜)> tushare 衍生 > websearch 弱;卡片与 `lessons` 标注置信层级。
**与现有闭环耦合**:self-review 硬门复用 `feedback_store.lessons_for`(违背已学经验即拦截);persona 分歧度可作为 retro 的一个观测量。

## 6. 防坑
- **覆盖率**:融资类只 ~1980 标的、大宗/席位稀疏 → 因子对未覆盖股 NaN,打分按现有"缺列重归一"处理(同 hk_ratio)。
- **稀疏事件因子**别硬塞 L1:大宗/席位 IC 多半噪声;先 eval,噪声就退 L3 证据。
- **爬虫降级**:雪球/股吧/淘股吧/机构季度持仓在本机不稳 → 弱信号/降级标注,**绝不进 L1 硬打分**。
- **seat_db 冷启动**:游资名库需积累;先用"机构专用"vs"非机构"二分,知名游资名逐步沉淀。
- **不破零付费/不碰排除文件/akshare·tushare venv-only/uv run --no-sync**。

## 7. 分 Phase
1. **Phase A**(先做):factor_lab 接 margin/block/top_inst 新因子 → harvest(复用现有日期计划)→ eval IC → 结论(谁进复合分)。**可证伪的经验闭环**。
2. **Phase B**:harvest_context 加 A股财报/游资席位/融资/(DCF 给全量)/trap 块。
3. **Phase C**:L3 多 persona 对抗面板 + self-review 硬门 + 证据分级。

## 8. 验收
- Phase A:新因子进 `ic_table.csv`,有/无 T+1 信号有明确数据结论;有信号者并入复合分并 `calibrate` 重标定、retro 可持续校准。
- Phase B:A股票的决策卡含原生财报 + 席位识别 + 融资块,数据带 source/降级标注。
- Phase C:L3 多 persona 产出分歧呈现;self-review 硬门能拦下"违背 lessons / 覆盖不足"的报告。
- 全程:selftest + ruff 绿;不 commit;不碰排除文件。

## 9. Phase A 实证结果(2026-06-20,23 个成型日,T+1 开到开)
把 5 个 UZI 市场级因子接进 `factor_lab` CANDIDATES 跑 rank-IC,**结论:无一进 L1 复合分**。

| 新因子 | 覆盖 | IC(T+1 oo) | IC(T+5) | IC(T+10) | 裁决 |
|---|---|---|---|---|---|
| `rz_ratio` 融资余额/流通市值 | 3233/4370(高) | −0.0095 | −0.0076 | −0.0141 | **噪声**(各 horizon ≈0,融资是慢变量,不驱动次日) |
| `rz_buy_intensity` 融资买入强度 | 3233/4370(高) | −0.0072 | +0.0092 | +0.0141 | **噪声**(半样本翻号,不稳) |
| `block_premium` 大宗折溢价 | 37/4370(稀疏) | −0.0066 | +0.0362 | −0.0066 | **太稀疏 + 翻号** → 退 L3 证据 |
| `block_intensity` 大宗活跃 | 37/4370(稀疏) | +0.0255 | +0.0273 | −0.0172 | **稀疏 + 半样本翻号** → 退 L3 证据 |
| `lhb_inst_net` 龙虎榜机构净买 | 44/4370(稀疏) | −0.0236 | −0.0543 | −0.0687 | 稀疏不进 L1,但**方向一致为负**=机构上榜买入后续偏弱 → **L4 反指提示** |

对照:`above_ma60` IC +0.037/+0.028/+0.019、`pct_60d` +0.027/+0.026/+0.015(真信号,量级高一档)。

**意义**:这是闭环的**成功证伪**——UZI 看似高级的市场级数据,经 IC 机器检验在 T+1 纯次日下**无 alpha**,被挡在复合分外(避免重蹈 winner_rate 误读式的拍脑袋)。`_GROUPS` 线上复合分**未改动**;5 因子保留为 `factor_lab` 的**长期监控候选**(retro 每轮重测,信号若现即捕获)。

**再路由**:大宗/龙虎榜机构 → Phase B 的 L4 席位识别证据(数据没浪费,换层用);`lhb_inst_net` 的负向一致性 → Phase B/C 的一条候选经验(机构龙虎榜买入≠看多,慎当利好)。
