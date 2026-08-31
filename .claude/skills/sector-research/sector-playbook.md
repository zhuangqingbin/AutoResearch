# sector-playbook — 行业 brief / 深研模板(sector-research)

> **路径约定**:`$CTX`/`$RPT` = 本引擎工作区根(Claude→`context_claude`/`reports_claude`,Codex→`context_codex`/`reports_codex`;shell 里 `CTX=context_${AUTORESEARCH_ENGINE:-claude}`,`RPT=reports_${AUTORESEARCH_ENGINE:-claude}`)。数据湖 `lake/` 两引擎共享。Read/Write 工具调用时把 `$CTX`/`$RPT` 代入具体目录名。

> lite brief:scan-market Stage 1 每行业一个 subagent 产出;full 深研:standalone。
> **单段标题是机器契约**(`autoresearch/sector/brief.py` 的 `extract_terrain` 按
> `## 地形段` 抽取),勿改字。2026-08-19 D6(⚖A6):研判段已整段砍除——brief 只产出
> 地形段(纯事实),行业方向叙事改由确定性 top3(`market.py` 的 `sector_healthy_top3`)独扛。

## lite brief 模板(~150–250 字/行业)

输入:`$CTX/sector/<date>/<行业>.json`(确定性 pack,数字不可编造;字段含 n_market/n_l2/
median_pct_60d/median_pe/pe_p25/pe_p75/median_pb/median_np_yoy/median_roe/main_pos_frac/
main_net_sum_yi/healthy_n/median_winner/leaders/calendar)。(原 sector_memo 历史事实行随 2026-08-21 learning 层退役删除。)
落点:`$CTX/scan/<date>/sector_briefs/<行业>.md`。

```
# 行业 brief — <行业> @ <date>

## 地形段(喂 L3/L4 · 描述性)
- **链定位一句**:<这行业当下的需求驱动/处在什么产业链上;事实性,不带方向>
- **景气读数**:成分 <n_market> 只 · 中位60日 <median_pct_60d>% · 中位np_yoy <median_np_yoy>% · 中位roe <median_roe> · 健康上涨 <healthy_n> 只
- **估值地形**:中位PE <median_pe>(P25 <pe_p25> / P75 <pe_p75>)· 中位PB <median_pb> — <链内谁贵谁便宜,只报数字位置>
- **资金地形**:主力净流入为正占比 <main_pos_frac> · 合计 <main_net_sum_yi> 亿 · 中位获利盘 <median_winner>
- **龙头座次**(市值 top,事实):<leaders → 名称(市值亿/PE/60日%) ×3–5>
- **事件日历**:<calendar → n_events 条 · 最近 next_date · by_kind;无 → 近两周无行业级事件>
```

**铁律**:地形段禁"超配/低配/回避/买卖/看多/看空"字样(它会喂 L3/L4——防锚定;行业方向
不由本 brief 判断);数字全出 pack,缺字段写 —,不编、不靠记忆补;♻️复用 brief 顶部的
banner 保留勿删。

**实时网查(有界)**:pack 之外可发 **≤2 条** WebSearch 查本行业最新头条(政策/景气/龙头事件),入地形段须标『实时网查』+ 落日期(as-of≤分析日),只报事实、不下方向判断。

## full 深研(standalone,6 节;报告落 `$RPT/sector/<date>/<行业>.md`)

1. **链结构**:上下游/需求驱动/环节利润分布——WebSearch 产业证据(价格/排产/订单),逐条标『实时网查』+日期;**pack 的 `readthrough` 键(若在)= 海外读透映射**,其中的海外同业/客户/供应商可作**产业证据**用(他们的财报口径、库存、指引是本行业需求侧的一手事实),写法见下「海外读透映射(full 专属)」;
2. **景气位置**:pack 量价读数 + 业绩预告方向(calendar)+ 一致预期变化(有数才写);
3. **竞争格局**:leaders 起步——集中度、份额趋势、新进入者;
4. **估值**:行业内分布(pe_p25/p75 + 中位)+ 与自身历史的相对位置(有数才写,别编分位);
5. **龙头映射**:环节 × 代表公司事实表(**不给个股评级**——要评级对该票跑 stock-research);
6. **研判结论**(standalone 报告专属,**不进机器契约**、不喂 L3/L4/ledger——仅供人读:情景 + 触发位。lite brief 已无此节,两档不再同构)。

### 海外读透映射(full 专属;2026-08-28 外源扩面 D-6)

`readthrough` 是 **presence-gated** 的:pack JSON 里**有这个键才写这段,没有就整段省略**(空名单不写空表)。
名单来自人工维护的 `readthrough_map.yaml`(有效期内 + 证据齐 + 单层 ≤4 项);数字由 `yf_tape` 确定性供给。
每项字段:`symbol / kind / relation / direction / rationale / evidence_url / pct_1d / pct_5d /
next_earnings_date / implied_move_note / stale_reason`。

- **`kind` 决定它能被写成什么**:`company` 才可以当财报主体(讲财报日 / 指引 / 隐含波幅);
  `etf` / `index` **只能当板块地形**——它们没有 `next_earnings_date`(pack 里恒为 None),
  **把 ETF 写成"某公司"或给它安一个财报日 = 硬错**。
- **`stale_reason` 非空** = 该行涨跌数字缺或不完整(tape 取数失败 / 无该 symbol 行 / 美股时段未收):
  照写事实与关系,**数字位置写"—"并注明原因**,不许拿旧数、不许估。
- 每项必须带上 `evidence_url`(关系成立的证据)与 `rationale`;两者缺一不入正文。

**渲染禁忌(硬性,违反即作废)**:映射只表示「**值得观察的关系**」,**不表示因果方向、涨跌传导方向或评级方向**。

- ❌「NVDA 涨 3%,所以本行业应涨」「海外同业超预期 → A 股受益」「映射股走强印证景气向上」——**任何 A→B 的传导句一律禁止**。
- ✅「NVDA(customer/downstream)最新财季数据中心收入 +56% YoY、指引口径见〈源〉;上一完整交易日 +3.1%,下次财报 2026-11-19」——**只报事实、关系标签与数字,判断留给读者**。
- 映射股的走势**不构成**本行业的景气证据,也**不得**进入第 6 节研判结论的触发位;它进的是第 1 节的产业事实与第 5 节的对照表。

**活体情报(可选,full 专属)**:需要产业链最新一手料时派 `sector-intel` subagent(契约见
`.claude/agents/sector-intel.md`:sonnet·max、**cap 6**、四面盲搜〔产业价格排产订单 / 海外同业财报指引 /
政策监管 / 龙头事件〕、时效窗 `intel_v2_full`〔24h/本周/本月/背景 = 1/1/0.5/0,催化挂不衰减〕、
URL 必落 + 来源分级〔T4 聚合页只作发现,必须追到 canonical 原文〕),落
`$CTX/sector/<date>/_sector_intel_<行业>.md`。第②面的名单**只能是 `readthrough` 里的 symbol**,
无映射则该面写「不适用」。情报稿只采集不判断,**只进 full 报告**——不进 brief、不进 L3/L4、不进任何账本。

**收尾**:无。(2026-08-21 用户裁定「整个 learning 层退役」:`sector_memo.upsert_memo`
行业事实月度蒸馏与 `sector_ledger` 方向记账两条腿随闭环一并删除;full 深研跑完就是报告本身,
不再往任何账本里记东西。)

## 与 scan-market 的衔接(编排事实)
Stage 1(L2 后)与 L3 证据取数**同一条消息并发**;L4 派发前对 ≥2 只同行业 finalist 的未覆盖链
补漏;消费(L3 地形行 / L4 简报注入)全部自动、presence-gated——无 brief 的日子 = 现状行为,
parity 不破。行业方向叙事(L5 「🎯 看多行业 top3」)完全走确定性 top3,与 brief 是否存在无关。
