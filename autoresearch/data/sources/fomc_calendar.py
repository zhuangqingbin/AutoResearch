#!/usr/bin/env python3
"""FOMC 年度日程(物化 yaml)—— 读 `autoresearch/data/fomc_calendar.yaml`,产 `ExternalEvent`。

design: `docs/specs/2026-08-28-external-evidence-expansion-design.md` §9「FOMC 日程」行
(「官方年度页物化 yaml,带 source URL / fetched_at / content hash / revision」)。

## 为什么是 yaml 而不是抓取

Fed 的年度日程页一年只变几次(改期 / 跨年新表),抓取器的维护成本远高于「周检 + 跨年预热」的
人工物化。代价是**它可能是旧的**,所以文件里有 `revision` / `fetched_at` / `content_hash` /
`verified_by` 四个字段,而本模块把 `verified_by` 变成**有牙齿的**东西:

    verified_by: pending  ⇒  produce DATE_ONLY  ⇒  永远进不了两个窄窗,只能是「当日风险」

没核实过的日期**不许伪装成已核**。惯例上 FOMC 声明是 14:00 ET,但「惯例」不是「本源」:
一个没人比对过官方页的 14:00 会直接决定「这一夜有没有 FOMC 落在持仓窗内」,而 FOMC 换算到
CST 恰好是 T+2 凌晨 02:00 —— 正好在窗内,错一次就是整条风险提示的真假问题。

## 契约

`load_fomc(year)` → `list[ExternalEvent]`(`event_type="fomc"`,`event_id="fomc:<end_date>"`)。
`first_seen_ts` 取该年块的 `added_at`(= 这批行写进本仓的时刻),**不用 now() 冒充**。
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from autoresearch.data.sources.official_event_calendar import (
    STATUS_SCHEDULED,
    TIME_QUALITY_DATE_ONLY,
    TIME_QUALITY_TIMED,
    TZ_NEW_YORK,
    ExternalEvent,
    parse_date,
    parse_ts,
    to_utc,
)

ENDPOINT = "fomc_calendar"
YAML_PATH = Path(__file__).resolve().parents[1] / "fomc_calendar.yaml"

PENDING = "pending"


class FomcCalendarError(RuntimeError):
    """yaml 缺失 / 形态不对 / 该年份未登记。"""


def _load_yaml(path: Path) -> dict:
    try:
        import yaml
    except ImportError as exc:                                      # pragma: no cover
        raise FomcCalendarError(
            "缺 PyYAML(随 langchain-core 传递安装):`uv sync` 后重试") from exc
    if not path.exists():
        raise FomcCalendarError(f"FOMC 日程 yaml 不存在:{path}")
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise FomcCalendarError(f"{path} 顶层不是 mapping,而是 {type(doc).__name__}")
    return doc


def load_raw(path: Path | str | None = None) -> dict:
    """整份 yaml(原样)。给 lint / 巡检用。"""
    return _load_yaml(Path(path) if path else YAML_PATH)


def file_content_hash(path: Path | str | None = None) -> str:
    """**本文件**内容的 sha256 —— 与 yaml 里的 `content_hash`(**官方页**内容 hash)不是一回事。

    前者证明「我们读的是哪一版 yaml」(可入 run capsule 做证据钉);后者要等抓取实现才有值。
    两个都叫 hash,混用会让「已核实」变成一句谁也验不了的话。
    """
    p = Path(path) if path else YAML_PATH
    return hashlib.sha256(p.read_bytes()).hexdigest()


def is_verified(doc: dict, year: int) -> bool:
    """该年块是否**已人工核实**(年块 `verified_by` 优先,回落到文件级)。"""
    block = (doc.get("years") or {}).get(int(year)) or {}
    v = block.get("verified_by", doc.get("verified_by"))
    s = str(v or "").strip().lower()
    return bool(s) and s != PENDING


def load_fomc(
    year: int,
    *,
    path: Path | str | None = None,
    require_verified: bool = False,
) -> list[ExternalEvent]:
    """该年的 FOMC 会议 → `ExternalEvent` 列表。

    时刻规则(**这是本模块的全部意义**):

    - 年块 `verified_by` 仍是 `pending` → 恒 `DATE_ONLY`,`scheduled_at_utc=None`。哪怕
      `statement_local_time: "14:00"` 写在那里也**不使用** —— 未核实的惯例值不是事实。
    - 已核实(`verified_by` 是人名 / commit)且给了 `statement_local_time` → `TIMED`,
      按 IANA `America/New_York` 换算(DST 由 `ZoneInfo` 负责;3 月与 12 月的会议分别落在
      EDT / EST,写死偏移必错一小时)。
    - 已核实但没给时刻 → 仍是 `DATE_ONLY`(核实的是**日期**,不代表知道时刻)。

    `require_verified=True`:pending 年份直接抛,给「必须要真时刻」的调用点用。

    `local_date` 取会议**最后一天**(声明发布日),`subject` 里带完整会期,不丢信息。
    """
    doc = _load_yaml(Path(path) if path else YAML_PATH)
    years = doc.get("years") or {}
    block = years.get(int(year)) or years.get(str(year))
    if not block:
        raise FomcCalendarError(
            f"fomc_calendar.yaml 未登记 {year} 年 —— 跨年预热要人工物化官方页(§9 周检 + 跨年预热)")
    verified = is_verified(doc, int(year))
    if require_verified and not verified:
        raise FomcCalendarError(
            f"{year} 年 FOMC 日程 `verified_by: pending`(未经官方页核实),"
            f"调用方要求 require_verified=True → 拒绝返回。先人工比对 {doc.get('source_url')}。")

    src = str(doc.get("source_url") or "")
    tz = str(doc.get("timezone") or TZ_NEW_YORK)
    revision = str(block.get("revision") or doc.get("revision") or "initial")
    added = block.get("added_at") or doc.get("fetched_at")
    if added is None:
        raise FomcCalendarError(
            f"{year} 年块缺 `added_at`(这批行写进本仓的时刻)—— PIT 时刻不许靠 now() 冒充")
    first_seen = to_utc(added if isinstance(added, datetime) else parse_ts(added))

    out: list[ExternalEvent] = []
    for m in block.get("meetings") or []:
        end = parse_date(m.get("end_date") or m.get("start_date"))
        start = parse_date(m.get("start_date") or end)
        raw_time = str(m.get("statement_local_time") or "").strip()
        sched = None
        quality = TIME_QUALITY_DATE_ONLY
        if verified and raw_time:
            hh, _, mm = raw_time.partition(":")
            local = datetime.combine(end, time(int(hh), int(mm or 0)), tzinfo=ZoneInfo(tz))
            sched = to_utc(local)
            quality = TIME_QUALITY_TIMED
        span = end.isoformat() if start == end else f"{start.isoformat()}/{end.isoformat()}"
        tail = " · SEP" if m.get("projections") else ""
        note = "" if verified else "(日期未核实 · verified_by=pending)"
        out.append(ExternalEvent(
            event_id=f"fomc:{end.isoformat()}",
            event_type="fomc",
            subject=f"FOMC 议息声明 {span}{tail}{note}",
            scheduled_at_utc=sched,
            local_date=end,
            timezone=tz,
            time_quality=quality,
            source_url=src,
            first_seen_ts=first_seen,
            revision=revision if verified else f"{revision}(pending)",
            status=str(m.get("status") or STATUS_SCHEDULED),
            mapped_symbols=tuple(m.get("mapped_symbols") or ()),
        ))
    return out


def load_range(
    start: date, end: date, *, path: Path | str | None = None
) -> list[ExternalEvent]:
    """跨年区间的 FOMC 事件(缺登记的年份**跳过并记 B 级降级**,不抛 —— §0 全部 B 级)。"""
    from autoresearch.data.contracts import record_degradation

    out: list[ExternalEvent] = []
    for y in range(start.year, end.year + 1):
        try:
            out.extend(load_fomc(y, path=path))
        except FomcCalendarError as exc:
            record_degradation(ENDPOINT, f"{y} 年日程缺席:{exc}", key=str(y))
    return [e for e in out if start <= e.local_date <= end]


__all__ = [
    "ENDPOINT", "YAML_PATH", "PENDING", "FomcCalendarError",
    "load_raw", "load_fomc", "load_range", "is_verified", "file_content_hash",
]
