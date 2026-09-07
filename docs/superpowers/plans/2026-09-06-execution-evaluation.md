# Execution Evidence and Net Return Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking. Use inline execution unless delegation is explicitly authorized.

**Goal:** 建立决策时点证据、成交状态和成本后损益的独立离线评价，不将收盘后条件误写成盘中已知信息。

**Architecture:** 导入器只接受显式数据文件，时间/金额契约位于 contracts，纯计算位于 common，研究 CLI 位于 research。生产 outcome、BUY、执行动作不改；历史价格代理、快照模拟、实盘成交三路分别汇总。

**Tech Stack:** Python、Decimal、datetime、JSONL、现有 workspace/atomic、pytest；不新增券商连接或行情订阅。

**Status（2026-09-07）：** 工程链已实施并完成完整性整改：稳定成交身份、FIFO 多轮/部分退出、
快照 D+2 模拟退出和显式成本口径均已有测试。真实券商成交与真实快照样本仍是外部数据验收项，
因此当前只能声明“仪器可用”，不能声明已验证真实净收益。

**前置裁决（历史，索引 §9 Q-C）：** `feature/broker-ingest` 当时是进入条件；现已合并，
`C3/C5` 正式消费 `context_<engine>/broker/trades.csv`，不另造 fills/orders 格式。

**接口（Consumes / Produces）：**

| 方向 | 内容 | 精确形状 |
|---|---|---|
| Consumes | 决策时点 | `scan/exec_anchor.read_execution(run_dir)`：`first_available_session`（YYYY-MM-DD）+ `exec_decision_cutoff`（"14:45"）+ `timezone_assumed` → `decision_at`；`actionability_status != "ACTIONABLE"`（LATE_REVALIDATION_REQUIRED / EXPIRED）的 run 只进覆盖表 |
| Consumes | 可交易性旗 | `common/ruler.ENTRY_FLAG`（`buyable_c1`：T+1 收盘封涨停=买不进→剔样本）、`EXIT_FLAG`（`unsellable_o2`：T+2 一字跌停开=卖不出→标旗不剔）、`entry_tradable(frame)` |
| Consumes | 真实成交 | `broker/trades.csv`（列以 `broker/schema.py` 为准：ts_code / side / price / qty / amount / 费用四项 / 同价分笔 seq / row_hash / account 归户） |
| Consumes | 执行线阈值 | `contracts/agent_output.EXEC_LINE_MAX_PCT_1D`、`EXEC_LINE_MAX_POS_IN_RANGE` |
| Produces（给 F） | 每样本 | `evidence_mode`、`fill_rule_version`、`cost_model_version`、`entry_state`/`exit_state`、`realized_pnl`/`unrealized_pnl`/`net_return_realized`（Decimal 字符串或 null）、`holding_window_breached`、`corporate_action_status`、`ready_quality` |

**Design:** [主设计 §7](../specs/2026-09-06-research-reliability-and-system-evolution-design.md#7-工作包-c执行时点与净收益评价) · [全阶段索引](2026-09-06-research-system-implementation-index.md)

## 0. 实施边界

主尺始终是 D+1 收盘入场、D+2 开盘退出的 gap_c1_o2。D+1 14:45 快照用于评价当时信息，不自动成为收盘成交价。沿用旧 exec_ok 作 EOD_PROXY 的事后诊断，新条件称 entry_condition_at_snapshot。

三条 A 股微观结构约束贯穿全包：

- **买腿的真实实现是 14:57–15:00 收盘集合竞价的限价单**：成交价 = 集合竞价价（= 收盘价），成交条件 = 收盘价 ≤ 限价且收盘未封涨停；封涨停时排板成交概率 ≈ 0（08-28 普查「收益随买得到的可能性单调递减」的机制）。科创板/创业板另有 **15:05–15:30 盘后固定价格交易**（按收盘价、以盘后成交量为上限）作第二次成交机会。卖腿是 T+2 开盘集合竞价，一字跌停开 = 卖不出。每种模拟都要带 `fill_rule_version`（C4 Step 0）。
- **三种证据模式共用主尺的可交易性语义**：`ruler.ENTRY_FLAG`/`EXIT_FLAG`/`entry_tradable()` 决定样本进不进分母；`ENTRY_STATES`/`EXIT_STATES` 只描述委托成交状态，不替代这两面旗。
- **`decision_at` 由 run 的时间锚派生，不由导入文件自由填**（08-28 逮到 13% 的 run 在 T+1 收盘后才批准，账本记了下不了的单）；非 ACTIONABLE 的 run 进覆盖表，不进任何模式的分母（C1 Step 5）。

| 文件 | 职责 |
|---|---|
| autoresearch/contracts/execution.py | Snapshot、Fill、Assessment 必填字段与枚举 |
| autoresearch/common/execution_math.py | 无 IO 的时点、数量、费用与损益计算 |
| autoresearch/research/execution_import.py | 读 `broker/trades.csv`（真实成交，格式归 broker 包）与明确授权的快照文件；验证、脱敏、单位标准化；**不定义第二套成交格式** |
| autoresearch/research/execution_audit.py | 离线 CLI、版本与输出清单、分组读数 |
| tests/contracts/test_execution_contract.py | 字段、类型、身份、时区与非法数量 |
| tests/common/test_execution_math.py | 部分成交、费用、未实现损益的固定案例 |
| tests/research/test_execution_audit.py | 导入→评价→报告、失败清单与覆盖拒绝 |

## Task C1：建立时间与证据模式契约

- [ ] **Step 1：新增契约声明，字段与主设计 §7.3 对齐，不在导入时任意删除时点。**

~~~python
SNAPSHOT_FIELDS = (
    "schema_version", "snapshot_id", "engine", "run_id", "code", "venue",
    "session_date", "decision_at", "market_event_at", "provider_published_at",
    "received_at", "persisted_at", "timezone", "last", "previous_close",
    "high_so_far", "low_so_far", "volume_so_far", "amount_so_far", "suspended",
    "limit_up_price", "limit_down_price", "price_adjustment_basis",
    "source_observation_id", "payload_hash", "timestamp_precision", "quality_flags",
)
EVIDENCE_MODES = {"EOD_PROXY", "SNAPSHOT_SIMULATED", "OBSERVED_FILL"}
ENTRY_STATES = {"UNKNOWN", "NOT_SUBMITTED", "NO_FILL", "PARTIAL_FILL", "FILLED", "CANCELLED"}
EXIT_STATES = {"NOT_DUE", "UNKNOWN", "NO_FILL", "PARTIAL_FILL", "FILLED"}
TIME_FIELDS = (
    "decision_at", "market_event_at", "provider_published_at", "received_at", "persisted_at",
)
~~~

每个字段必须出现，但合法未知值为 null；身份、schema、code、engine、payload_hash 不允许 null。价格/数量用 decimal string，时间是带偏移 ISO8601；quality_flags 是原因码数组。

- [ ] **Step 2：在 execution_math.py 添加时间函数前，写如下测试并确认 RED。**

~~~python
from autoresearch.common.execution_math import snapshot_visibility

def snapshot_times(**changes):
    result = {
        "decision_at": "2026-09-01T14:45:00+08:00",
        "market_event_at": "2026-09-01T14:44:58+08:00",
        "provider_published_at": "2026-09-01T14:44:59+08:00",
        "received_at": "2026-09-01T14:45:00+08:00",
        "persisted_at": "2026-09-01T14:45:01+08:00",
    }
    return dict(result, **changes)

def test_received_before_cutoff_can_be_available():
    assert snapshot_visibility(snapshot_times()) == "AVAILABLE"

def test_late_import_does_not_prove_live_visibility():
    assert snapshot_visibility(snapshot_times(
        received_at="2026-09-02T09:00:00+08:00",
        persisted_at="2026-09-02T09:00:01+08:00",
    )) == "NOT_OBSERVED_AT_DECISION"

def test_future_market_data_is_not_available():
    assert snapshot_visibility(snapshot_times(
        market_event_at="2026-09-01T15:00:00+08:00",
        provider_published_at="2026-09-01T15:00:01+08:00",
        received_at="2026-09-01T15:00:02+08:00",
        persisted_at="2026-09-01T15:00:03+08:00",
    )) == "FUTURE_INFORMATION"

def test_unknown_source_time_remains_unknown():
    assert snapshot_visibility(snapshot_times(provider_published_at=None)) == "UNKNOWN"
~~~

- [ ] **Step 3：实现以下时间判定。**

~~~python
from datetime import datetime

def parse_aware(value):
    if value is None:
        return None
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("timezone required")
    return result

def snapshot_visibility(row):
    names = ("decision_at", "market_event_at", "provider_published_at",
             "received_at", "persisted_at")
    times = {key: parse_aware(row[key]) for key in names}
    if any(value is None for value in times.values()):
        return "UNKNOWN"
    decision, market, published, received, persisted = (times[key] for key in names)
    if not market <= published <= received <= persisted:
        return "INVALID_TIME_ORDER"
    if market > decision or published > decision:
        return "FUTURE_INFORMATION"
    return "AVAILABLE" if received <= decision else "NOT_OBSERVED_AT_DECISION"
~~~

这证明的是导入字段符合可得性顺序，仍须 observation/blob 留证。persisted 可以略晚于决策，不据此否定先前收到的数据。历史供应商证明当时已发布、但本系统晚收到的，只可标为历史模拟，不升级 OBSERVED_FILL。

- [ ] **Step 4：增加时区等价、naive 时间、倒置时钟测试；运行 tests/common/test_execution_math.py 与新契约测试。**

- [ ] **Step 5：decision_at 从 run 时间锚派生；写测试确认非 ACTIONABLE 的 run 不能进入评价。**

~~~python
from datetime import date, datetime, time
from zoneinfo import ZoneInfo
from autoresearch.scan.exec_anchor import MARKET_TZ, read_execution

def decision_at_for_run(run_dir):
    block = read_execution(run_dir)
    status = block.get("actionability_status") or "UNKNOWN"
    session = block.get("first_available_session")
    if status != "ACTIONABLE" or not session:
        return None, status
    hh, mm = (int(x) for x in block["exec_decision_cutoff"].split(":"))
    tz = ZoneInfo(block.get("timezone_assumed") or MARKET_TZ)
    return datetime.combine(date.fromisoformat(session), time(hh, mm), tzinfo=tz), status
~~~

返回 None 的 run 在覆盖表记其 `actionability_status`（LATE_REVALIDATION_REQUIRED / EXPIRED / …），快照评价与成交评价都不计入分母；不允许用导入文件里的 `decision_at` 覆盖它。老 run 的锚是估算（`ready_quality="estimated"`），读数分组必须带 `ready_quality`。

## Task C2：实现快照条件，禁止日线信息穿越

- [ ] **Step 1：在新模块中从 contracts.agent_output 引用现有两个阈值；不重新定义 3% 和 0.7。**

~~~python
from decimal import Decimal
from autoresearch.contracts.agent_output import (
    EXEC_LINE_MAX_PCT_1D, EXEC_LINE_MAX_POS_IN_RANGE,
)

def entry_condition(row, *, max_age_seconds):
    if snapshot_visibility(row) != "AVAILABLE":
        return {"verdict": "UNKNOWN", "reason": "TIME_NOT_VERIFIED"}
    age = (parse_aware(row["decision_at"]) - parse_aware(row["market_event_at"])).total_seconds()
    if max_age_seconds < 0:
        raise ValueError("negative freshness policy")
    if age > max_age_seconds:
        return {"verdict": "UNKNOWN", "reason": "STALE_SNAPSHOT"}
    if row["suspended"] is True:
        return {"verdict": "FAIL", "reason": "SUSPENDED"}
    keys = ("last", "previous_close", "high_so_far", "low_so_far")
    if row["suspended"] is None or any(row[k] is None for k in keys):
        return {"verdict": "UNKNOWN", "reason": "MISSING_MARKET_FIELDS"}
    last, prev, high, low = (Decimal(row[k]) for k in keys)
    if not all(x.is_finite() for x in (last, prev, high, low)):
        raise ValueError("nonfinite price")
    if not (prev > 0 and low > 0 and high >= last >= low):
        raise ValueError("invalid price range")
    if high == low:
        return {"verdict": "UNKNOWN", "reason": "ZERO_RANGE"}
    limit_up = row.get("limit_up_price")
    if limit_up is not None and last >= Decimal(limit_up):
        return {"verdict": "UNKNOWN", "reason": "LIMIT_UP_QUEUE"}   # 排板成交概率≈0,无盘口证据不判可买
    pct = (last / prev - 1) * 100
    pos = (last - low) / (high - low)
    passed = (pct <= Decimal(str(EXEC_LINE_MAX_PCT_1D))
              and pos < Decimal(str(EXEC_LINE_MAX_POS_IN_RANGE)))
    return {"verdict": "PASS" if passed else "FAIL", "reason": "SNAPSHOT_PROXY"}
~~~

这是“截至快照的同形式条件”，不是已有收盘条件的等价替换，也不是必然成交证明。涨停/跌停报价单列；没有队列/盘口证据不能从 last=limit 推出能买到或能卖出。新 max_age_seconds 作为显式研究参数记录，不假冒项目现有默认值。

- [ ] **Step 2：添加边界测试：15:00 日线不能补 14:45 high_so_far；过期/零振幅/字段缺失→UNKNOWN；超过阈值→FAIL；阈值等号按现有定义。**

~~~python
from autoresearch.common.execution_math import entry_condition

def test_snapshot_condition_is_not_close_fill():
    row = dict(snapshot_times(), last="10.1", previous_close="10",
               high_so_far="10.4", low_so_far="10", suspended=False)
    assert entry_condition(row, max_age_seconds=60)["verdict"] == "PASS"
    assert "entry_vwap" not in entry_condition(row, max_age_seconds=60)
    assert entry_condition(row, max_age_seconds=1)["verdict"] == "UNKNOWN"

def test_limit_up_quote_is_not_buyable():
    row = dict(snapshot_times(), last="11", previous_close="10", high_so_far="11",
               low_so_far="10.2", suspended=False, limit_up_price="11")
    assert entry_condition(row, max_age_seconds=60) == {"verdict": "UNKNOWN", "reason": "LIMIT_UP_QUEUE"}
~~~

## Task C3：实际成交与部分实现损益

- [ ] **Step 1：成交来源是 `broker/trades.csv`（`broker/schema.py` 的 normalize/validate 已定义 ts_code、side、price、qty、amount、费用四项、同价分笔 seq、row_hash、account 归户）；C 只做投影：`fill_id = row_hash`、`position_id` 由 (account, code, anchor_session) 派生、`source_observation_id` 指向 broker `ingest_log.jsonl` 行。券商导出没有委托信息时 `submitted`/`requested_qty` 为 None → `entry_status` 返回 UNKNOWN；不能从没有成交推断未下单，也不能从有成交倒推委托量。**
- [ ] **Step 2：添加固定金额测试后实现平均成本分配；全部 Decimal 运算。**

~~~python
from decimal import Decimal as D
from autoresearch.common.execution_math import position_pnl

def test_partial_sale_allocates_entry_cost_and_leaves_exposure():
    result = position_pnl(
        buy_qty="100", buy_notional="1000", buy_fees="2",
        sell_qty="40", sell_notional="440", sell_fees="1",
        mark_price="10.5", cash_distribution="0", receivable="0",
    )
    assert result["realized_pnl"] == D("38.2")
    assert result["remaining_qty"] == D("60")
    assert result["unrealized_pnl"] == D("28.8")
    assert result["net_pnl_cash"] == D("-563")

def test_no_sale_has_no_realized_return():
    result = position_pnl(
        buy_qty="100", buy_notional="1000", buy_fees="2",
        sell_qty="0", sell_notional="0", sell_fees="0",
        mark_price=None, cash_distribution="0", receivable="0",
    )
    assert result["net_return_realized"] is None
    assert result["unrealized_pnl"] is None
~~~

~~~python
def position_pnl(*, buy_qty, buy_notional, buy_fees, sell_qty,
                 sell_notional, sell_fees, mark_price,
                 cash_distribution, receivable):
    raw = (buy_qty, buy_notional, buy_fees, sell_qty, sell_notional,
           sell_fees, cash_distribution, receivable)
    if any(x is None for x in raw):
        raise ValueError("missing amounts; do not replace unknown fees with zero")
    bq, bn, bf, sq, sn, sf, cash, due = map(Decimal, raw)
    if any(not x.is_finite() or x < 0 for x in (bq, bn, bf, sq, sn, sf, cash, due)):
        raise ValueError("invalid amount")
    if bq <= 0 or bn <= 0 or sq > bq:
        raise ValueError("invalid long position")
    if sq == 0 and (sn != 0 or sf != 0):
        raise ValueError("sell cash without sell quantity")
    basis = bn + bf
    allocated = basis * sq / bq
    left = bq - sq
    realized = sn - sf - allocated
    mark = None if mark_price is None else Decimal(mark_price)
    if mark is not None and (not mark.is_finite() or mark <= 0):
        raise ValueError("invalid mark")
    unrealized = None if mark is None else left * mark - (basis - allocated)
    return {
        "remaining_qty": left, "realized_pnl": realized,
        "net_return_realized": realized / allocated if allocated > 0 else None,
        "unrealized_pnl": unrealized,
        "net_pnl_cash": sn + cash - bn - bf - sf,
        "cash_distribution": cash, "corporate_action_receivable": due,
    }
~~~

buy_fees/sell_fees 为归属该腿的费用与税合计；Assessment 仍分别保留 fees_paid 与 tax_paid。此函数处理一个已对账 position_id，不把不同币种、不同标的、借券空头混成一仓。

net_pnl_cash 是完整仓位累计现金流，不是部分平仓的已实现利润；realized_pnl 与剩余市值必须并列。分红现金与应收单列，不并入上述价格腿收益；总经济损益另作现金+市值+应收对账，禁止与复权价重复计入。

- [ ] **Step 3：实现逐订单状态归并，并测试撤单前部分成交。**

~~~python
def entry_status(*, submitted, requested_qty, filled_qty, cancelled):
    if submitted is None or filled_qty is None:
        return "UNKNOWN"
    qty = Decimal(filled_qty)
    if not submitted:
        if qty != 0:
            raise ValueError("fill without submitted order")
        return "NOT_SUBMITTED"
    requested = Decimal(requested_qty)
    if not requested.is_finite() or not qty.is_finite() or not 0 <= qty <= requested or requested <= 0:
        raise ValueError("invalid order quantity")
    if cancelled:
        return "CANCELLED"  # 已成交数量仍保留在独立字段
    return "FILLED" if qty == requested else "PARTIAL_FILL" if qty > 0 else "NO_FILL"
~~~

exit_status 根据计划退出时点是否已到、已卖/应卖量计算；未到 NOT_DUE、无委托证据 UNKNOWN、有委托零成交 NO_FILL；T+2 一字跌停开的样本按 `ruler.EXIT_FLAG`（unsellable_o2）标旗不剔——剔了会美化。D+2 开盘未完成退出则标记 holding_window_breached，继续追踪，不删样本、不把晚卖价格塞回 gap_c1_o2。

## Task C4：版本化模拟成本与公司行动

- [ ] **Step 0：先登记成交规则版本，再写成本。** `fill_rule_version` 首版三个值：`close_auction_limit_v1`（买腿：以 14:45 快照 last × (1 + limit_bps) 挂收盘集合竞价限价单；成交价 = 收盘价；成交条件 = 收盘价 ≤ 限价 且 `ENTRY_FLAG` 为可买，否则 NO_FILL）、`after_hours_fixed_v1`（仅 688/300 代码：15:05–15:30 按收盘价成交，量以盘后成交量为上限，缺盘后量数据则 UNKNOWN）、`open_auction_v1`（卖腿：T+2 开盘集合竞价，`EXIT_FLAG` 一字跌停开 = 标旗不剔）。规则版本与 `cost_model_version` 同进 manifest；没有规则版本的模拟结果不得进入读数。

- [ ] **Step 1：模拟必须提供 cost_model_version、适用市场/日期、佣金率、最低佣金、税率、收费腿、过户费率、滑点和订单归并方法。实际费率由执行者按数据来源及生效规则核验，不在开发示例中声明现行税率。**

~~~python
def simulated_leg(*, price, qty, side, slippage_bps,
                  commission_rate, minimum_commission, tax_rate,
                  tax_sides=frozenset({"SELL"}), transfer_fee_rate="0"):
    p, q, slip, rate, minimum, tax, transfer = map(
        Decimal, (price, qty, slippage_bps, commission_rate, minimum_commission,
                  tax_rate, transfer_fee_rate))
    if any(not x.is_finite() or x < 0 for x in (p, q, slip, rate, minimum, tax, transfer)) or slip >= 10000:
        raise ValueError("invalid cost parameters")
    if side not in {"BUY", "SELL"} or p <= 0 or q <= 0:
        raise ValueError("invalid simulated order")
    if not set(tax_sides) <= {"BUY", "SELL"}:
        raise ValueError("tax sides must be BUY/SELL")
    adjusted = p * (1 + slip / 10000 if side == "BUY" else 1 - slip / 10000)
    notional = adjusted * q
    return {"price": adjusted, "notional": notional,
            "commission": max(notional * rate, minimum),
            "tax": notional * tax if side in tax_sides else Decimal("0"),   # A 股印花税只收卖出腿
            "transfer_fee": notional * transfer}                            # 过户费双边,费率由 policy 声明
~~~

- [ ] **Step 2：测试零费用显式配置、最低佣金、买卖方向滑点、同订单多笔成交只收一次最低佣金、多个订单分别计费、买腿 tax 恒为 0 而 transfer_fee 双边、封涨停买腿 NO_FILL（`close_auction_limit_v1`）、688 代码盘后固定价格成交（`after_hours_fixed_v1`）。实际成交分支不得调用 simulated_leg 再次扣滑点。**
- [ ] **Step 3：公司行动输入为 action_id、effective_at、quantity_delta、cash_paid、cash_receivable、sellable_at、source_observation_id。进入计算前核对原始价与数量变更；未知公司行动状态标 CORPORATE_ACTION_UNRESOLVED，不给“已完整核算”的净收益。**

首版可导入已核对的行动记录；自动拆股/分红推断不属首版。送转未到账数量仍是不可卖权益。对账不完的样本保留在 coverage 表，不放进 complete 净收益分母。

## Task C5：导入、排他输出和端到端报告

- [ ] **Step 1：execution_import.py 只保留白名单字段，拒绝重复 fill_id、跨引擎、同 ID 不同 hash、非法代码/时间/数量、未声明币种。保留逐行 error_code，不因一行坏数据悄悄跳过。**

~~~python
def minimize_record(row, allowed):
    return {key: row[key] for key in allowed if key in row}

def unique_records(rows, key):
    seen = set()
    for row in rows:
        identity = row[key]
        if identity in seen:
            raise ValueError(f"duplicate {key}: {identity}")
        seen.add(identity)
    return rows
~~~

禁止把原始账号、持有人姓名、券商 token 带入研究目录。保存脱敏记录 hash 与原来源登记 ID，不复制账户凭据。

- [ ] **Step 2：实现 CLI 契约：**

~~~text
python -m autoresearch.research.execution_audit
  --experiment-id EXEC_20260906_01
  --snapshots <明确授权的脱敏快照文件>
  --orders <明确授权的脱敏订单文件>
  --fills <明确授权的脱敏成交文件>
  --policy <版本化执行与成本配置>
~~~

snapshots/orders/fills 可独立缺席，必须在覆盖表中记录；不传文件不自动访问券商。experiment_id 限字母数字下划线横线，必须新建目录。源码从 workspace 取根，不硬编码 codex。

~~~python
import re
from autoresearch.common import workspace as ws

def create_output_dir(experiment_id):
    if not re.fullmatch("[A-Za-z0-9_-]{1,80}", experiment_id):
        raise ValueError("invalid experiment id")
    parent = ws.reports_root() / "research" / "execution"
    parent.mkdir(parents=True, exist_ok=True)
    output = parent / experiment_id
    output.mkdir(exist_ok=False)
    return output
~~~

导入根同样排他创建在 ws.context_root()/execution/<id>。先验证输入、再创建输出；中断目录保留失败 manifest，不自动覆盖重跑。已有冻结扫描目录禁止作为 --out。

- [ ] **Step 3：产出 assessments.jsonl、daily_metrics.csv、readout.md、manifest.json。manifest 记录版本、代码 SHA、输入 hash、交易日历来源、覆盖数、失败数、as_of。**
- [ ] **Step 4：按 evidence_mode/venue/cost_version/ruler 分组，报告总样本、缺快照、缺成交、缺费用、未成交、部分成交、窗口违约、净收益、尾部损失、剩余风险及资金占用。缺失不填零。**
- [ ] **Step 5：用 tmp_path 做三模式混合端到端测试，断言净收益不跨模式合并；在测试开始和结束比较旧报告 hash。**

~~~bash
uv run --no-sync python -m pytest -q tests/contracts/test_execution_contract.py tests/common/test_execution_math.py tests/research/test_execution_audit.py tests/contracts/test_layering.py
~~~

预期：全部新增测试及分层门通过，离线报告生成；不执行真实扫描、不产生委托。

## 验收与发布

| 验收编号 | 对应任务 | 必须展示的证据 |
|---|---|---|
| C01/C02 | C1/C2 | 未来信息、过期/未知时间不能 PASS |
| C03 | C3/C5 | 缺快照/成交/费用分别计数且收益缺失 |
| C04 | C3/C4 | 零成交、部分撤单、停牌/封板和公司行动测试 |
| C05 | C5 | 三模式三组分母，价格代理与实盘损益分列 |
| C06 | C5 | 输出排他创建、旧 run hash 不变 |
| C07 | 全部 | diff 不改评级、relative_buy、生产 outcome 语义 |
| C08 | C1/C5 | 非 ACTIONABLE 的 run 与 `ready_quality=estimated` 分组可见，不进任何模式分母 |
| C09 | C4 | 买腿印花税为 0；封涨停买腿 NO_FILL；`fill_rule_version` 与 `cost_model_version` 进 manifest |

**提交边界：** 契约与时间；损益计算；导入器；报告及端到端测试分别提交。每批只 stage 本表文件，运行相关测试后 git diff --check。

**回滚：** 停用离线 CLI 即可，保留已脱敏的原始证据与版本化结果；没有任何需要“撤回”的自动交易动作。只有合成数据时结论写“功能通过，实盘验证未完成”。
