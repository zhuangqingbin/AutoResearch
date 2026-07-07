# sector-playbook — 行业 brief / 深研模板(sector-research)

> lite brief:scan-market Stage 1 每行业一个 subagent 产出;full 深研:standalone。
> **两段标题是机器契约**(`autoresearch/sector/brief.py` 的 `extract_terrain`/`extract_view` 按
> `## 地形段` / `## 研判段` 切分,`sector_ledger` 按 `**行业方向**` keyed 行记账),勿改字。

## lite brief 模板(~250–400 字/行业)

输入:`context/sector/<date>/<行业>.json`(确定性 pack,数字不可编造;字段含 n_market/n_l2/
median_pct_60d/median_pe/pe_p25/pe_p75/median_pb/median_np_yoy/median_roe/main_pos_frac/
main_net_sum_yi/healthy_n/median_winner/leaders/calendar)+ sector_memo 行(若有,历史事实)。
落点:`context/scan/<date>/sector_briefs/<行业>.md`。

```
# 行业 brief — <行业> @ <date>

## 地形段(喂 L3/L4 · 描述性)
- **链定位一句**:<这行业当下的需求驱动/处在什么产业链上;事实性,不带方向>
- **景气读数**:成分 <n_market> 只 · 中位60日 <median_pct_60d>% · 中位np_yoy <median_np_yoy>% · 中位roe <median_roe> · 健康上涨 <healthy_n> 只
- **估值地形**:中位PE <median_pe>(P25 <pe_p25> / P75 <pe_p75>)· 中位PB <median_pb> — <链内谁贵谁便宜,只报数字位置>
- **资金地形**:主力净流入为正占比 <main_pos_frac> · 合计 <main_net_sum_yi> 亿 · 中位获利盘 <median_winner>
- **龙头座次**(市值 top,事实):<leaders → 名称(市值亿/PE/60日%) ×3–5>
- **事件日历**:<calendar → n_events 条 · 最近 next_date · by_kind;无 → 近两周无行业级事件>

## 研判段(仅 L5)
**行业方向**: <看多|中性|看空> — <一句依据,落 pack 数字>
- 景气位置:<上行/顶部/磨底/下行 + 为什么(量价/盈利/资金哪个在说话)>
- 格局与表达:<链上哪个环节吃利润;只说"环节",不对具体票定方向>
- 最大证伪点:<什么数据出现时本研判作废>
_个股评级只由本股 rubric 三门决定;Claude 推理产出,仅供研究,非投资建议。_
```

**铁律**:地形段禁"超配/低配/回避/买卖"字样(它会喂 L3/L4——三层同律);研判段方向行必须
keyed 格式;数字全出 pack,缺字段写 —,不编、不靠记忆补;♻️复用 brief 顶部的 banner 保留勿删。

**实时网查(有界)**:pack 之外可发 **≤2 条** WebSearch 查本行业最新头条(政策/景气/龙头事件),入 brief 须标『实时网查』+ 落日期(as-of≤分析日),只报事实、不改方向定调。

## full 深研(standalone,6 节;报告落 `reports/sector/<date>/<行业>.md`)

1. **链结构**:上下游/需求驱动/环节利润分布——WebSearch 产业证据(价格/排产/订单),逐条标『实时网查』+日期;
2. **景气位置**:pack 量价读数 + 业绩预告方向(calendar)+ 一致预期变化(有数才写);
3. **竞争格局**:leaders 起步——集中度、份额趋势、新进入者;
4. **估值**:行业内分布(pe_p25/p75 + 中位)+ 与自身历史的相对位置(有数才写,别编分位);
5. **龙头映射**:环节 × 代表公司事实表(**不给个股评级**——要评级对该票跑 stock-research);
6. **研判段**(契约同 lite,更厚:情景 + 触发位)。

**收尾两件(闭环)**:
```bash
uv run --no-sync python - <<'PY'
from autoresearch.learning.sector_memo import upsert_memo
upsert_memo("<行业>", "<1–2 句研究结论(事实为主)>", "<date>")
PY
uv run --no-sync python -c "from autoresearch.learning.sector_ledger import record_calls; print(record_calls('reports/sector/<date>', '<date>'))"
```
(standalone 报告目录若无 `sector_briefs/` 结构,ledger 记账可改为把研判段落到当日 scan staging;
无 scan 日则只回写 memo——ledger 以 scan 日为主战场。)

## 与 scan-market 的衔接(编排事实)
Stage 1(L2 后)与 L3 证据取数**同一条消息并发**;L4 派发前对 ≥2 只同行业 finalist 的未覆盖链
补漏;消费(L3 地形行 / L4 简报注入 / L5 两节 / ledger 记账)全部自动、presence-gated——无 brief
的日子 = 现状行为,parity 不破。
