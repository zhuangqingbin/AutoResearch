#!/usr/bin/env python3
"""时间锚 —— 这份报告**什么时候才能真的下单**(设计稿 2026-08-28 §2.4 G1)。

## 病灶

主尺 `gap_c1_o2` 假设「T+1 尾盘买 → T+2 开盘卖」,而结果账本把 T+1 定义成
`analysis_date` 之后第一个交易日(`outcome.market_frame`:`t1 = P[idx+1]`)。
**报告是晚上跑的,数据日不一定是当天**:61 个已发布 run 里 19 个数据日 ≠ 跑动日,
其中 **8 个(13%)在 T+1 收盘之后才就绪** —— 账本给它们记的"T+1 尾盘买",
买的是那一刻**已经过去**的价格。名单里就有 `20260826_2000`(数据日 08-25,
08-26 20:00 才写完,T+1=08-26 早收盘了)。

这不是把 `t1` 加一天就完的 bug:**"批准时点 / 数据新鲜度 / 是否仍有效"三个维度
从来没有进过任何一把尺子**。所以本模块产出的不是一个日期,是一个状态块。

## 两条时点,别混

- `EXCHANGE_CUTOFF` = 14:57,交易所集合竞价开始 —— **只记录,不做判据**;
- `EXEC_DECISION_CUTOFF` = 14:45,**运营截止**(人读完 brief 还要下单,得留缓冲)。
  14:56:59 才批准的报告在物理上确实还能挂单,但把它算成"当天可执行"
  等于假设人零延迟 —— 那正是本模块要拆穿的那类假设。

## 三个状态(`actionability_status`)

- `ACTIONABLE` —— 在数据日的下一个交易日尾盘之前就批准了,主尺可以照常量;
- `LATE_REVALIDATION_REQUIRED` —— 迟 1~2 个 session,卡片里的 T+1/T+2 已不成立,
  **不能把日期平移一下当新策略用**(信息老了,不只是日历错位);
- `EXPIRED` —— 迟 ≥3 个 session;
- `FAILED` / `UNKNOWN` —— 业务未成功 / 证据不足以判断,**不猜**。

`executable_policy` 视图只纳入 `ACTIONABLE`;`signal_quality` 视图保留全部并如实标注。
迟到 run 要算反事实收益就单列 `late_execution_counterfactual`,
**绝不混进主 BUY 均值**——那正是"13% 假单"的来源。
"""
from __future__ import annotations

import contextlib
import json
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path

EXECUTION_SCHEMA_VERSION = 1

MARKET_TZ = "Asia/Shanghai"
#: 运营截止:报告要在这之前获批,才算"当天尾盘还来得及下单"。
EXEC_DECISION_CUTOFF = time(14, 45)
#: 交易所集合竞价开始时刻,**只留档不作判据**(见模块 docstring)。
EXCHANGE_CUTOFF = time(14, 57)

#: 迟到多少个 session 之后从"需重新核验"降为"过期"。
_EXPIRY_LAG_SESSIONS = 3

ACTIONABLE = "ACTIONABLE"
LATE_REVALIDATION_REQUIRED = "LATE_REVALIDATION_REQUIRED"
EXPIRED = "EXPIRED"
FAILED = "FAILED"
UNKNOWN = "UNKNOWN"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _compact(day: str) -> str:
    return str(day).replace("-", "")


def _dashed(day: str) -> str:
    text = str(day).replace("-", "")
    return f"{text[:4]}-{text[4:6]}-{text[6:]}"


# ───────────────────────── 交易日历(三级回退,质量如实标) ─────────────────────────

#: 进程内日历记忆。`outcome fill` 一次要给 61 个 run 各判一次时间锚,
#: 不缓存就是 61 次 trade_cal 往返 —— 而交易日历在一次进程生命周期内不会变。
_SESSION_CACHE: dict[tuple[str, str], tuple[list[str], str]] = {}


def trading_sessions(start: str, end: str) -> tuple[list[str], str]:
    """`[YYYY-MM-DD, …]` + 日历质量标签。**未来日只有 trade_cal 答得出**。

    三级回退,每级都如实报自己是谁:

    1. `trade_cal`(tushare)—— 唯一能回答"明天开不开市"的来源,`quality="trade_cal"`;
    2. `lake/daily` 分区 —— 只有过去,`quality="lake_partitions"`(历史回填够用);
    3. 工作日启发 —— `quality="weekday_heuristic"`,**不认识节假日**,调用方看到这个
       标签就该把结论降级(不是静默地当真)。

    第 2/3 级永远不会凭空造出"明天是交易日"这种断言:第 2 级给不出未来日,
    第 3 级给得出但带着最低的质量标签。
    """
    key = (_compact(start), _compact(end))
    if key in _SESSION_CACHE:
        return _SESSION_CACHE[key]
    with contextlib.suppress(Exception):
        from autoresearch.data.tushare_source import _pro, _trade_days
        days = _trade_days(_pro(), *key)
        if days:
            _SESSION_CACHE[key] = ([_dashed(d) for d in days], "trade_cal")
            return _SESSION_CACHE[key]
    with contextlib.suppress(Exception):
        from autoresearch.common import workspace as ws
        root = ws.lake_root() / "daily"
        days = sorted(p.stem for p in root.glob("*.parquet") if p.stem.isdigit())
        window = [_dashed(d) for d in days if key[0] <= d <= key[1]]
        if window and window[-1] >= _dashed(end):     # 湖答不出未来日,只在覆盖到 end 时才算数
            _SESSION_CACHE[key] = (window, "lake_partitions")
            return _SESSION_CACHE[key]
    first, last = date.fromisoformat(_dashed(start)), date.fromisoformat(_dashed(end))
    out, cur = [], first
    while cur <= last:
        if cur.weekday() < 5:
            out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out, "weekday_heuristic"


def first_available_session(approved_at: datetime, analysis_date: str,
                            sessions: list[str]) -> str | None:
    """报告获批之后,**第一个还来得及在尾盘下单**的交易日。

    判据两条同时成立:该 session 严格晚于数据日(T+1 起步),且批准时刻早于该日
    `EXEC_DECISION_CUTOFF`。批准时刻当天若已过截止,就顺延到下一个 session。
    """
    if approved_at is None:
        return None
    anchor = _dashed(analysis_date)
    for session in sorted(sessions):
        if session <= anchor:
            continue
        cutoff = datetime.combine(date.fromisoformat(session), EXEC_DECISION_CUTOFF)
        if approved_at.tzinfo is not None:
            cutoff = cutoff.replace(tzinfo=approved_at.tzinfo)
        if approved_at < cutoff:
            return session
    return None


def classify(exec_lag: int | None, *, business_status: str = "SUCCEEDED",
             approved_at: datetime | None = None) -> str:
    """`exec_lag` → `actionability_status`。证据不足一律 `UNKNOWN`,不猜。"""
    if str(business_status).upper() not in {"SUCCEEDED", "ACTIVE"}:
        return FAILED
    if approved_at is None or exec_lag is None:
        return UNKNOWN
    if exec_lag <= 0:
        return ACTIONABLE
    return LATE_REVALIDATION_REQUIRED if exec_lag < _EXPIRY_LAG_SESSIONS else EXPIRED


def build_execution_block(analysis_date: str, *, approved_at: datetime | None,
                          brief_written_at: datetime | None = None,
                          business_status: str = "SUCCEEDED",
                          sessions: list[str] | None = None,
                          calendar_quality: str = "",
                          ready_source: str = "gate4_approved",
                          ready_quality: str = "measured") -> dict:
    """一个 run 的时间锚块(写进 `manifest.json` 的 `execution`)。

    `sessions` 缺省时自己取日历:从数据日起往后 21 个自然日足够跨过任何长假边界的
    前几个交易日(春节最长 ~9 天),再长也没有意义——迟到 3 个 session 就已经 EXPIRED。
    """
    anchor = _dashed(analysis_date)
    quality = calendar_quality
    if sessions is None:
        end = (date.fromisoformat(anchor) + timedelta(days=21)).isoformat()
        sessions, quality = trading_sessions(anchor, end)
    first = first_available_session(approved_at, anchor, sessions) if approved_at else None
    index = {day: i for i, day in enumerate(sorted(sessions))}
    staleness = exec_lag = None
    if first is not None and anchor in index and first in index:
        staleness = index[first] - index[anchor]        # 1 = 正常 T+1
        exec_lag = max(0, staleness - 1)                # 0 = 正常
    return {
        "schema_version": EXECUTION_SCHEMA_VERSION,
        "analysis_date": anchor,
        "data_as_of": anchor,
        "brief_written_at": brief_written_at.isoformat() if brief_written_at else None,
        "decision_approved_at": approved_at.isoformat() if approved_at else None,
        "first_available_session": first,
        "exec_lag": exec_lag,
        "staleness_sessions": staleness,
        "actionability_status": classify(exec_lag, business_status=business_status,
                                         approved_at=approved_at),
        "ready_source": ready_source,
        "ready_quality": ready_quality,
        "calendar_quality": quality,
        "timezone_assumed": MARKET_TZ,
        "exec_decision_cutoff": EXEC_DECISION_CUTOFF.strftime("%H:%M"),
        "exchange_cutoff": EXCHANGE_CUTOFF.strftime("%H:%M"),
    }


# ───────────────────────── 读取(新 run 直读,老 run 估算并标记) ─────────────────────────

def _parse_dt(value: object) -> datetime | None:
    if not value:
        return None
    with contextlib.suppress(ValueError, TypeError):
        return datetime.fromisoformat(str(value))
    return None


def _gate4_approved_at(run_dir: Path) -> datetime | None:
    """GATE4 真正放行的时刻 —— **这才是"报告可用"的时点**。

    `trace/stage_results/gate4.json` 的 `recorded_at` 是 UTC(带 `Z`);这里换算成
    市场本地时(中国无夏令时,恒 +8,所以固定偏移与真时区库逐秒等价,还不依赖 tzdata)。
    GATE4 未过的 run 返回 `None` —— 没过门的报告谈不上"什么时候可以下单"。
    """
    path = run_dir / "trace" / "stage_results" / "gate4.json"
    if not path.is_file():
        return None
    with contextlib.suppress(OSError, json.JSONDecodeError, ValueError, TypeError):
        doc = json.loads(path.read_text(encoding="utf-8"))
        if str(doc.get("status") or "").upper() != "SUCCEEDED":
            return None
        stamp = str(doc.get("recorded_at") or "")
        if not stamp:
            return None
        utc = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        return (utc + timedelta(hours=8)).replace(tzinfo=None)
    return None


def _market_local_naive(value: datetime | None) -> datetime | None:
    """带时区 → 市场本地(恒 +8,同 `_gate4_approved_at`)naive;naive 视为已是市场本地时。"""
    if value is None or value.tzinfo is None:
        return value
    return (value - value.utcoffset() + timedelta(hours=8)).replace(tzinfo=None)


#: `stage_results/gate4.json` 与本 run 发布时刻的一致性窗。超窗 = 那份文件不是本 run 的。
_GATE4_MAX_BEFORE = timedelta(hours=12)
_GATE4_MAX_AFTER = timedelta(hours=6)


def _resolve_approved_at(run: Path, written: datetime | None
                         ) -> tuple[datetime | None, str, str]:
    """批准时刻 + 它的来源与质量。**实测优先,但实测要先自证是本 run 的**。

    🚨 共享 staging 污染(实测):`staging` 按**数据日**键,同数据日重跑原地覆盖,于是
    `20260730_0116` 与 `20260730_2132` 两个 run 的 `trace/stage_results/gate4.json` 带着
    **逐字节相同**的 `recorded_at`(2026-07-29T13:05:40.266192Z)。直接采信它,会把
    07-30 21:32 才发布的那个 run 判成"07-29 晚就批准了" → 假 `ACTIONABLE`。

    所以实测值必须落在发布时刻附近才采信:早于发布 12 小时以上 = 上一次跑的残留;
    晚于发布 6 小时以上 = 与本 run 无关(GATE4 略晚于 `generated_at` 是正常的,
    发布早段就写 manifest、CP7 才跑完门)。超窗一律退回 `generated_at` 估算并留痕。
    """
    approved = _gate4_approved_at(run)
    # 比较前把 `written` 换成与 `approved` 同口径的市场本地 naive 时刻:2026-09-17 起新
    # manifest 的存储块带 `+08:00`,naive/aware 直接比较会 TypeError,让 outcome.fill
    # 整晚中断。只改比较,返回值原样。
    local = _market_local_naive(written)
    if (approved is not None and local is not None
            and not (local - _GATE4_MAX_BEFORE <= approved <= local + _GATE4_MAX_AFTER)):
        return written, "manifest_generated_at(gate4 超一致性窗,疑共享 staging 残留)", "estimated"
    if approved is not None:
        return approved, "gate4_stage_result", "measured"
    return written, "manifest_generated_at", "estimated"


def _upgrade_with_gate4(run: Path, block: dict) -> dict | None:
    """存储块是估算、而盘上已有可信实测 → 用实测重算一份;否则 `None`(保持原样)。"""
    written = _parse_dt(block.get("brief_written_at")) or _parse_dt(
        block.get("decision_approved_at"))
    approved, source, quality = _resolve_approved_at(run, written)
    if quality != "measured" or approved is None:
        return None
    return build_execution_block(
        str(block.get("analysis_date")), approved_at=approved, brief_written_at=written,
        business_status="SUCCEEDED", ready_source=source, ready_quality=quality)


def read_execution(run_dir: Path | str) -> dict:
    """一个已发布 run 的时间锚块。

    新 run 的 `manifest.execution` 直接返回;**老 run 只能估算** —— `generated_at` 是
    发布早段(assemble 开始时)写的 naive 时刻,严格早于 GATE4 真正批准的时刻,所以它
    只是一个 lower bound,一律标 `ready_quality="estimated"`。**历史 manifest 一个字
    不改**:run 目录发布后不再变是 MANIFEST/ROOT 的不变量,回写会让每个历史 run 的
    `verify` 当场变红。要精确值就去 capsule 的 `events.jsonl` 里找 gate4 事件。
    """
    run = Path(run_dir)
    manifest = {}
    path = run / "manifest.json"
    if path.is_file():
        with contextlib.suppress(OSError, json.JSONDecodeError):
            manifest = json.loads(path.read_text(encoding="utf-8"))
    block = manifest.get("execution")
    if isinstance(block, dict) and block.get("analysis_date"):
        if str(block.get("ready_quality")) == "measured":
            return block
        # 发布时 GATE4 还没跑,存的必然是发布时刻的**估算**(`publish_time`/`estimated`)。
        # 门过了以后 `stage_results/gate4.json` 才落盘 —— 读到实测就地升级,
        # 否则新 run 反而不如老 run(老 run 走下面那条路,本来就读得到实测)。
        # 不回写 manifest:run 目录发布后不再变是 MANIFEST/ROOT 的不变量。
        upgraded = _upgrade_with_gate4(run, block)
        if upgraded is not None:
            return upgraded
        return block

    analysis_date = str(manifest.get("analysis_date") or "")
    if not _DATE_RE.fullmatch(analysis_date):
        # 目录名的首段在**新格式**下才是数据日;legacy 目录名首段是跑动日,不能当数据日用。
        from autoresearch.scan.run_naming import parse_run_dir
        parsed = parse_run_dir(run.name)
        analysis_date = (parsed.analysis_date if parsed else None) or ""
    if not analysis_date:
        return {"schema_version": EXECUTION_SCHEMA_VERSION, "analysis_date": None,
                "actionability_status": UNKNOWN, "ready_source": "absent",
                "ready_quality": "unknown"}
    # 优先级:GATE4 实测 > manifest.generated_at 估算。后者是发布**早段**写的,
    # 严格早于放行时刻,只是一个 lower bound —— 所以它必须标 estimated。
    written = _parse_dt(manifest.get("generated_at"))
    approved, source, quality = _resolve_approved_at(run, written)
    return build_execution_block(
        analysis_date, approved_at=approved, brief_written_at=written,
        business_status=str(manifest.get("business_status") or "SUCCEEDED"),
        ready_source=source, ready_quality=quality)


__all__ = [
    "ACTIONABLE",
    "EXCHANGE_CUTOFF",
    "EXECUTION_SCHEMA_VERSION",
    "EXEC_DECISION_CUTOFF",
    "EXPIRED",
    "FAILED",
    "LATE_REVALIDATION_REQUIRED",
    "MARKET_TZ",
    "UNKNOWN",
    "build_execution_block",
    "classify",
    "first_available_session",
    "read_execution",
    "trading_sessions",
]
