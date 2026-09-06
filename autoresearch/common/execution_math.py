#!/usr/bin/env python3
"""执行评价的**无 IO 纯计算**(工作包 C1/C2)—— 时点可见性、决策时点派生、快照入场条件。

实施计划:`docs/superpowers/plans/2026-09-06-execution-evaluation.md` Task C1/C2。

**这里算的是「当时看得见什么」,不是「成交了什么」。** 三种证据模式(EOD_PROXY /
SNAPSHOT_SIMULATED / OBSERVED_FILL)分别汇总,本模块只服务前两种的时点判定;真实成交
状态与损益归 C3/C4。

与计划的一处偏离(记在这里,不藏):计划 C1 Step 5 的示例 `decision_at_for_run(run_dir)`
直接 `from autoresearch.scan.exec_anchor import read_execution`,那会造出一条
`common → scan` 的**向上**边(`tests/contracts/test_layering.py`)。这里改成纯函数
`decision_at_from_execution_block(block)`:由上层(research 的导入器/CLI)自己
`read_execution(run_dir)` 之后把块传进来。语义一字不差,方向对了。
"""
from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from autoresearch.contracts.agent_output import (
    EXEC_LINE_MAX_PCT_1D,
    EXEC_LINE_MAX_POS_IN_RANGE,
)
from autoresearch.contracts.execution import ACTIONABLE, parse_aware

_VISIBILITY_TIMES = ("decision_at", "market_event_at", "provider_published_at",
                     "received_at", "persisted_at")
_MARKET_FIELDS = ("last", "previous_close", "high_so_far", "low_so_far")


def snapshot_visibility(row: dict) -> str:
    """五个时点 → 可见性状态(闭集见 `contracts.execution.VISIBILITY_STATES`)。

    - 任一时点缺失 → `UNKNOWN`(**没给全**,不是「不可得」);
    - 顺序不成立(行情发生 ≤ 供应商发布 ≤ 我方收到 ≤ 落盘)→ `INVALID_TIME_ORDER`;
    - 行情发生或发布**晚于**决策时点 → `FUTURE_INFORMATION`;
    - 收到晚于决策 → `NOT_OBSERVED_AT_DECISION`(当时没看见,不能算进「当时的判断」);
    - 其余 → `AVAILABLE`。

    `persisted_at` 允许略晚于决策:先收到、后落盘是正常的,不据此否定已经收到的数据。

    这证明的只是**导入字段符合可得性顺序**,仍须 observation/blob 留证。历史供应商能证明
    当时已发布、但本系统晚收到的,只能标历史模拟,不升级 `OBSERVED_FILL`。
    """
    times = {key: parse_aware(row[key]) for key in _VISIBILITY_TIMES}
    if any(value is None for value in times.values()):
        return "UNKNOWN"
    decision, market, published, received, persisted = (times[k] for k in _VISIBILITY_TIMES)
    if not market <= published <= received <= persisted:
        return "INVALID_TIME_ORDER"
    if market > decision or published > decision:
        return "FUTURE_INFORMATION"
    return "AVAILABLE" if received <= decision else "NOT_OBSERVED_AT_DECISION"


def decision_at_from_execution_block(block: dict) -> tuple[datetime | None, str]:
    """run 的时间锚块 → `(decision_at, actionability_status)`。

    `block` 就是 `scan.exec_anchor.read_execution(run_dir)` 的返回(或 `manifest.execution`)。
    只有 `ACTIONABLE` 的 run 才有决策时点:其余(迟到需复核 / 过期 / 未批准)返回
    `(None, status)`,由调用方记进覆盖表——**不进任何模式的分母**。

    08-28 实测:61 个已发布 run 里 8 个(13%)在 T+1 收盘之后才批准,账本给它们记的是一笔
    已经过去、下不了的单。让导入文件自己填 `decision_at` 就是把这个病搬进新仪器。

    缺 `exec_decision_cutoff` / `timezone_assumed` 时**抛错**,不取缺省:猜一次就够把 14:45
    这条运营纪律悄悄改掉。
    """
    status = str(block.get("actionability_status") or "UNKNOWN")
    session = block.get("first_available_session")
    if status != ACTIONABLE or not session:
        return None, (status if session or status != ACTIONABLE else "UNKNOWN")
    cutoff, zone = block.get("exec_decision_cutoff"), block.get("timezone_assumed")
    if not cutoff or not zone:
        raise ValueError("execution block must carry exec_decision_cutoff and timezone_assumed")
    hh, mm = (int(part) for part in str(cutoff).split(":"))
    return datetime.combine(date.fromisoformat(str(session)), time(hh, mm),
                            tzinfo=ZoneInfo(str(zone))), status


def entry_condition(row: dict, *, max_age_seconds: float) -> dict:
    """截至快照的入场条件 —— 与卡面执行线**同形式**,不是它的等价替换,更不是成交证明。

    阈值单源在 `contracts.agent_output`(`EXEC_LINE_MAX_PCT_1D` / `EXEC_LINE_MAX_POS_IN_RANGE`),
    等号方向照抄卡面:当日涨幅 `<=` 上限、区间位置 `<` 上限。

    `max_age_seconds` 是**显式研究参数**,不冒充项目现有缺省:快照有多新才算「当时」是本次
    实验的声明,得跟着 spec 走。

    封涨停单列 `LIMIT_UP_QUEUE`:没有队列/盘口证据,就不能从 `last == limit_up` 推出买得到
    ——08-28 普查「收益随买得到的可能性单调递减」正是在这里发生的。
    """
    if max_age_seconds < 0:
        raise ValueError("negative freshness policy")
    if snapshot_visibility(row) != "AVAILABLE":
        return {"verdict": "UNKNOWN", "reason": "TIME_NOT_VERIFIED"}
    age = (parse_aware(row["decision_at"]) - parse_aware(row["market_event_at"])).total_seconds()
    if age > max_age_seconds:
        return {"verdict": "UNKNOWN", "reason": "STALE_SNAPSHOT"}
    if row["suspended"] is True:
        return {"verdict": "FAIL", "reason": "SUSPENDED"}
    if row["suspended"] is None or any(row[k] is None for k in _MARKET_FIELDS):
        return {"verdict": "UNKNOWN", "reason": "MISSING_MARKET_FIELDS"}
    last, prev, high, low = (Decimal(row[k]) for k in _MARKET_FIELDS)
    if not all(x.is_finite() for x in (last, prev, high, low)):
        raise ValueError("nonfinite price")
    if not (prev > 0 and low > 0 and high >= last >= low):
        raise ValueError("invalid price range")
    if high == low:
        return {"verdict": "UNKNOWN", "reason": "ZERO_RANGE"}
    limit_up = row.get("limit_up_price")
    if limit_up is not None and last >= Decimal(limit_up):
        return {"verdict": "UNKNOWN", "reason": "LIMIT_UP_QUEUE"}
    pct = (last / prev - 1) * 100
    pos = (last - low) / (high - low)
    passed = (pct <= Decimal(str(EXEC_LINE_MAX_PCT_1D))
              and pos < Decimal(str(EXEC_LINE_MAX_POS_IN_RANGE)))
    return {"verdict": "PASS" if passed else "FAIL", "reason": "SNAPSHOT_PROXY"}
