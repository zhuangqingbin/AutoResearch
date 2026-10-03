# S01 来源资格只读调查

日期：2026-10-01。开发树：real-host-acceptance。未修改源码、未执行研究、未生成 proof、未改旧 run。未读写其他引擎产物或冻结 research-quality-evidence-efficiency 树。

结论：**S01 的真实来源资格尚未满足**。现有 root field adapter 唯一支持 tushare.repurchase.v1；真实 run 没有 repurchase receipt。存在真实原始收盘价候选，但既缺 typed adapter，也缺截止时间前可用性的可靠证据。旧卡不能只补 sidecar 就升级语义 PASS。

## 核查对象

原根 `/Users/qingbin.zhuang/Personal/TradingAgents` 的 run `20261001T092950456043Z`，canonical 卡 `reports_codex/analyze/runs/20261001T092950456043Z/p1/report/贵州茅台_lite.md`。发布 capsule 及该 run 的 context 工作区均只读。

- 卡有 c01–c11 共 11 条 declaration，全部 BACKGROUND。
- card_claim_uses 附件 `859880ca99244519603dd13ebe179d7ae925afcc982a2b6b5fbd923bc1634292.json`：known_claims=11、mapped_claims=11、fraction=1.0；11 条均 SOURCE_NOT_BOUND。
- `evidence/material_claims` 无 sidecar；coverage=1 是声明映射完整，不能代表事实支持完整。
- 77 条 receipt：64 条 stock.harvest 原始/汇总取数；13 条 host_tool（含宿主命令/研究写稿，不能作为独立原始来源）。
- 64 条 harvest 构成：moneyflow×20、margin_detail×36；stk_factor_pro、cyq_perf、hk_hold、stock_news_em、stk_holdernumber、pledge_stat、stock_restricted_release_queue_em 各一；stock.harvest.snapshot.v1×1。
- 没有 daily、repurchase、fina_indicator、yfinance 原始 receipt。

## 截止时间共同约束

冻结 research.frame 的 knowledge_cutoff=`2026-10-01T09:29:50.456043Z`。64 条 harvest receipt 全是 v1，无 source_timing，available_at=本次读取结束时间；最早 `09:30:34.959461Z`，最晚 snapshot `09:30:52.614098Z`，全部晚于 cutoff。即使补正确字段匹配，现有 require_timing/evaluate_material_claim 仍须拒绝可用性资格。

SourceReceipt v2 **已经**区分 published_at、first_available_at、received_at，且用 latest_possible 尊重 day/minute 精度。只要可信 first_available_at 和（存在时）published_at 的最晚可能时间不晚于 cutoff，received_at 晚于 cutoff 不阻止 semantic PASS，但 received_by_cutoff=UNKNOWN。仅 published_at 早于 cutoff 而 first_available_at=null 不足。不得把 trade_date、ann_date、市场收盘钟点或文件 mtime 当成 first availability。

## 卡事实覆盖

所有下面“有字段”的项目均仅表示原始字节可定位；本 run 均无 quote_refs/field-review 绑定，且全受上述时间缺口约束。现有 predicate 契约只允许回购/增持/减持/中标；实际注册 adapter 仅回购。以下 c01–c11 没有一个能直接通过现有 adapter。

| 声明 | 事实类型 | 原始 receipt/blob 与字段情况 | quote/计算/已有 predicate 覆盖 |
|---|---|---|---|
| c01 | 公司英文名、行业 | 只有 stock.harvest.snapshot.v1 的 instrument_context/渲染 blocks；无底层公司资料原始 receipt | 汇总文本能证明写过该值，不独立证明公司身份；无支持 |
| c02 | 入退场日期、尺 | root 冻结 research.frame 明确含 entry_session=10-08、exit_session=10-09、ruler=gap_c1_o2 | 属任务/框架事实；应走既有 frame 校验，不伪装市场 source review |
| c03 | 09-30 收盘价 1258.62 | tushare.stk_factor_pro 原 parquet 唯一 ts_code=600519.SH/trade_date=20260930 行，close=1258.62 | 最小 typed 候选；当前 predicate 不支持 |
| c04 | 10日资金与末日资金 | moneyflow×20；09-30 原 net_mf_amount=60958.84（万元）；10日需要明确交易日窗口与确定性单位换算/求和 | 无 calculation sidecar；不能把单字段扩大成“主力真在” |
| c05 | MA60、RSI6、MACD与趋势 | stk_factor_pro 同行：ma_qfq_60=1288.78767、macd_qfq=-4.544、rsi_hfq_6=52.768；有不同复权字段 | 均需固定复权口径与关系运算；close-only adapter 不涵盖 |
| c06 | 筹码获利与成本 | cyq_perf：winner_rate=21.21、cost_50pct=1339.8、weight_avg=1381.67 | 卡“平均成本中位数”措辞混合；字段不能互换；无支持 |
| c07 | 财务毛利/净利/负债率/同比 | 只有 snapshot 财务渲染 block；本 run 无相应底层财务 endpoint receipt | 不能从卡或 snapshot 数字反造原始财务来源 |
| c08 | TTM PE/forward PE/PB | stk_factor_pro 有 pe_ttm/pb 列，可另作 typed 候选；但卡高精度值来自 snapshot，未逐值确认；forward PE 只有汇总 block | 不可假定口径/时间一致；无支持 |
| c09 | 户数与质押 | stk_holdernumber：20260630 296404，20260331 243159，ann_date 分别 20260815/20260425；pledge_stat：20260924 pledge_ratio=0.06 | 卡显示0.1是舍入，需显式 rounding；复合声明须拆分；无支持 |
| c10 | 新闻报道及无硬催化 | akshare.stock_news_em 原表存在 | 标题列表不能证明不存在公司硬催化；无支持 |
| c11 | 输入缺档案/ENTRY_PRICE/未绑定 | frozen task/dispatch、frame 与空 material 目录可以核查 | 属任务状态，不能借 host_tool 命令正文当外部事实支持 |

## 最小真实候选与可信时间检查

建议只做 `tushare.stk_factor_pro.close.v1`，并严格只支持未复权收盘价。

- source receipt：`18359fe2858f281d6c99efe8eac32d0284b89514db98fe7a535b774d3e3efb17`
- payload_hash：`3ac45524fe312638f8ad3a8678dada7d624423bc076d0963082c522aeccb09ce`
- raw_hash：`1eb63df92476027d3d87cfc380a5e10d48a12f85c9dcf48184e892de68367df3`
- provider/endpoint/codec：tushare / stk_factor_pro / dataframe.parquet.v1。
- selector：`{"ts_code":"600519.SH","trade_date":"20260930"}`；唯一行 close=1258.62。
- 两个 blob 的实际 SHA256 均核对相等；parquet attrs 均为空。元数据只有 ARROW:schema、pandas；无原采集时戳。
- reads.jsonl 明确 CACHE_HIT、SOURCE_FILE_SNAPSHOT、lake/stk_factor_pro/20260930.parquet、4529行、原始8139154字节、标准化6721986字节；started_at/ended_at 都是本次晚于 cutoff 的读取时间。没有同 payload 首次观测时戳。
- data/cache.py `_stamp_observed` 仅 snapshot policy 数据添加 first_seen 两列；本候选是 date/eod policy，原始列中无这两列。cache 文件写入不另落签名采集时间元数据；不能拿 CACHE_HIT 或 mtime 推断早于 cutoff。

因此此 run 的 candidate 必须 UNKNOWN。新增 run 若仍从同一无时间 metadata 的湖读取，也不能自动变 PASS。需要真正 source-owned、绑定 exact payload 的历史观测/可用时间证据；或后续带真实 publication/availability provenance 的 supplier capture。该时间补齐是另一个有限开发项，当前不要扩成通用平台，也不要外网/新研究。

## 函数与接线

1. `news/material_claims.py:bind_material_claim` 只是 sidecar writer；`review_receipt_ids=[]` 可保存 v2 UNKNOWN。引用 receipt/quote/calculation 的完整性验证不代表语义 PASS。`evaluate_material_claim` 只有重算 root review + compare_events 能给 semantic PASS。
2. `news/card_claims.py:_mapping/evaluate_card_claims` 解析声明，但对新 declaration 只建内存 UNKNOWN population，并不调用 bind_material_claim。`claim_population` 只接受 root task 当前 attempt/成功祖先，`bound_claim_context` 固定卡生产者。BACKGROUND UNKNOWN 不升级维度；PASS 本身也不把模型 UNKNOWN 门自动改 PASS。
3. `trace/source_receipts.py:record_response` 已有 exact payload/raw hash 和 v2 timing；`record_active_response` 已接受 source_timing。现有 `trace/source_lineage.py:SourceReadTrace._persist` 固定 available_at=ended_at，未接 v2。优先把可信原始 timing 传入已有 owner，不能在 binder 猜日期。
4. `news/source_fields.py:_REGISTRY/_table/derive_fields/matching_requests` 当前硬编码 repurchase 注册/selector/列；close adapter 需在这里增加一个明确分支。selector 用 subject+trade_date，身份不含价格；重复行/不同候选锚点/非法数值/非有限值/负值/复权 close 字段均拒。predicate 建议“收盘价”，amount_unit=CNY、amount_basis=unadjusted_close，effective_at 为 trade_date 的日区间；不要塞入 executed_total。contracts/claim_evidence.py ENUMS 需对应有限扩展。其余 lifecycle/assertion/polarity 采用明确受控 close observation 约定，并在 checked_fields 中由 adapter 明确给出，不能依赖模型。
5. `session_agent/service.py:source_fields` 已提供 root-only request API：校验 RUNNING current attempt、冻结输入、祖先归属、来源时间、subject；签发 deterministic claim_fields.v1 receipt 与 SOURCE_FIELDS_VERIFIED event；无需新增研究 broker 权限。
6. `session_agent/source_fields.py:freeze_intel_context` 当前仅 `_REGISTRY[ADAPTER_ID]` 收集回购候选，扩 close 时要遍历注册候选，保证离线 replay 不漏。`replay_review` 已重算投影、校验 owner event 与 frozen input snapshots。
7. `session_agent/workflows/stock.py:_lite_tasks` harvest→card→validate→publish 无 source binding stage；`_two_stage_lite_tasks` 添加 card.facts/initial，但同样没有 material binder。避免为一个事实新增工作流平台：root 在 RUNNING stock.card、card bytes 与 declaration 固定后、submit 验证前调用既有 API，再写同 statement hash 的 sidecar。可由 `service.submit` 中 domain validation 前的窄 stock-card root helper 完成，必须保持生产者/attempt 与 accepted ancestors 确定，不允许研究子角色自行指定权限。既有 `scan/l4/intel_guard.py:_bind_frozen_event` 可参考，但它只处理事件、URL与回购；不能直接宣称 standalone stock 已接通。
8. `session_agent/validation.py:_stock_lite`、`domain_ops.py` stock validation/publication，以及 `workflows/stock.py` publication 已重复调用 registered_card_semantics；应保留这些拒绝/降级门。

可复用调用（仅示意，未执行）：

```python
review = service.source_fields(run_id, "stock.card", attempt, {
    "source_receipt_id": original_receipt_id,
    "adapter_id": "tushare.stk_factor_pro.close.v1",  # 尚未实现
    "selector": {"ts_code": "600519.SH", "trade_date": "20260930"},
})["result"]
sidecar = bind_material_claim(capsule, engine="codex", run_id=run_id,
    task_id="stock.card", attempt=attempt, claim_id="c03", statement=exact_declaration,
    source_receipt_ids=[original_receipt_id], quote_refs=[], calculation_ids=[],
    claim_event=closed_typed_close_claim, review_receipt_ids=[review["receipt_id"]])
```

时间不合格时 service.source_fields 必须拒绝；root 可以绑定已有原始 receipt 并留空 review_receipt_ids，evaluate 返回 NOT_AVAILABLE_AT_DECISION/UNKNOWN，不能冒充字段已验证。声明解析也必须闭合到受控单个 close fact，不能给任意 prose 安上从来源构造的 event 以绕过 statement 语义检查。

## 建议回归范围

- `tests/news/test_source_fields.py` 现有 real_producer/adversarial/current-attempt/ancestor/replay/root-event/date-precision/ambiguous-row 测试可复用模式，为 close 增加真实 schema fixture 与唯一 row、错误价格 FAIL、不同日期 UNKNOWN、复权字段拒绝、未知 timing UNKNOWN、after-cutoff UNKNOWN。
- 保留 `test_unsupported_predicates_have_no_structured_adapter` 对增持/减持/中标的否定覆盖。
- `tests/news/test_card_claims.py`：声明只有 mapping 仍 UNKNOWN；root typed binding 可支持 c03；未知时间不能改变 effective dimensions/gates；PASS 不自动升级门。
- `tests/session_agent/test_card_claim_uses.py`：真实 submit owner 绑定 current stock.card，晚/失败/另一 ticker attempt 拒绝；publish 重算仍一致；非 supported declaration 留 UNKNOWN；离线 replay 不读原工作区或湖。
- `tests/forensics` source receipt/lineage：若以后接 timing，测试 exact payload 与 metadata 同一快照，unknown/不可信时间不回填，日精度保守上界，received after cutoff 的独立字段保留 UNKNOWN。

本调查未跑测试或 verify-report，未重新宣称旧报告 proof；这里只报告文件读取与 blob hash 检查证据。
