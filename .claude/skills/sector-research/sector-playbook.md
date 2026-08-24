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

1. **链结构**:上下游/需求驱动/环节利润分布——WebSearch 产业证据(价格/排产/订单),逐条标『实时网查』+日期;
2. **景气位置**:pack 量价读数 + 业绩预告方向(calendar)+ 一致预期变化(有数才写);
3. **竞争格局**:leaders 起步——集中度、份额趋势、新进入者;
4. **估值**:行业内分布(pe_p25/p75 + 中位)+ 与自身历史的相对位置(有数才写,别编分位);
5. **龙头映射**:环节 × 代表公司事实表(**不给个股评级**——要评级对该票跑 stock-research);
6. **研判结论**(standalone 报告专属,**不进机器契约**、不喂 L3/L4/ledger——仅供人读:情景 + 触发位。lite brief 已无此节,两档不再同构)。

**收尾**:无。(2026-08-21 用户裁定「整个 learning 层退役」:`sector_memo.upsert_memo`
行业事实月度蒸馏与 `sector_ledger` 方向记账两条腿随闭环一并删除;full 深研跑完就是报告本身,
不再往任何账本里记东西。)

## 与 scan-market 的衔接(编排事实)
Stage 1(L2 后)与 L3 证据取数**同一条消息并发**;L4 派发前对 ≥2 只同行业 finalist 的未覆盖链
补漏;消费(L3 地形行 / L4 简报注入)全部自动、presence-gated——无 brief 的日子 = 现状行为,
parity 不破。行业方向叙事(L5 「🎯 看多行业 top3」)完全走确定性 top3,与 brief 是否存在无关。
