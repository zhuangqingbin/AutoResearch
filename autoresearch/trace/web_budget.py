#!/usr/bin/env python3
"""网查预算的**计量单位** —— 一行工具调用 ≠ 一次查询(确定性,零 LLM)。

design: docs/specs/2026-08-28-external-evidence-expansion-design.md §6.2 / §6.5

## 病

`external_tools.jsonl` 一行是**一次工具调用**;一个调用可能含多条 batch query。
拿行数当查询数,cap 8 的角色发一次 5 query 的 batch 就被记成「1 条」——
既低估成本,也让「超没超 cap」这个问题从此答不对。

## 修

按 `(role, stage, subject)` 聚合,同时输出 §6.2 的 11 键:

    measurement / tool_calls / search_queries / fetched_urls / failed_units /
    duplicate_urls / wall_s / cap_unit / cap / cap_enforcement / self_report_delta

四条硬纪律:

1. **cap 默认约束 `search_queries`**;角色另有 fetch cap 就单列(`fetch_cap`),
   **不得用 tool row 数冒充 query 数**。
2. transcript 未绑定 / payload 不可解析 / batch 计数不完整 → `measurement="UNMEASURED"`,
   对应计数写 `null`,**不是 0**;`cap_exceeded` 写 `null`,**不得判「未超限」**。
3. 查询去重按规范化 query hash;URL 去重按 canonical URL;**重复调用仍计预算**
   (`duplicate_*` 只是诊断,不从 `search_queries` / `fetched_urls` 里减)。
4. 自报「网查 N 条」只进 `self_report_delta` 做诊断;真值取解析后的 query / fetch unit。

## 限频的引擎边界(§6.5)

本波**不做**工具级硬 cap:Claude 侧 `cap_enforcement="OBSERVED_ONLY"`,Codex 同样
`OBSERVED_ONLY`。**不宣称双引擎硬限频等价** —— 靠解析 transcript 冒充强制是假的,
真要 `HARD_SHARED` 得把预算放到共同 dispatcher 层,另立设计。

姊妹稿的 `ReportModel` 运行事实**只读** `web_budget.json`;`UNMEASURED` 原样展示,
不得回退到稿件自报,也不得显示 0。
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path

from autoresearch.trace.atomic import canonical_json
from autoresearch.trace.evidence_index import safe_canonical_url, write_if_changed

SCHEMA_VERSION = 1
BUDGET_NAME = "web_budget.json"

MEASURED = "MEASURED"
UNMEASURED = "UNMEASURED"

#: §6.5:本波两个引擎都只是**观测**,不是强制。`UNMEASURED` 用于「连观测都没成立」。
OBSERVED_ONLY = "OBSERVED_ONLY"
HARD_CLAUDE = "HARD_CLAUDE"
ENFORCEMENTS = (OBSERVED_ONLY, HARD_CLAUDE, UNMEASURED)

CAP_UNIT_SEARCH = "search_queries"

KIND_SEARCH = "search"
KIND_FETCH = "fetch"
KIND_OTHER = "other"

#: 已知工具名(小写、去 namespace 前缀后比对)。未知名走关键词兜底,
#: **fail open 进对应族**:一个改了名的搜索工具不该从预算里凭空消失。
_SEARCH_NAMES = frozenset(
    {"websearch", "web_search", "web.search", "web.search_query", "search", "brave_search",
     "google_search", "exa_search", "tavily_search"}
)
_FETCH_NAMES = frozenset(
    {"webfetch", "web_fetch", "web.fetch", "web.open", "web.open_url", "fetch",
     "fetch_url", "open_url", "url_fetch", "read_url", "browse"}
)
_SEARCH_HINTS = ("search", "query")
_FETCH_HINTS = ("fetch", "open", "browse", "crawl", "scrape", "read_page")

#: 请求 payload 里可能承载 batch 的键。顺序即优先级。
_QUERY_KEYS = ("queries", "query", "search_queries", "q", "searches", "terms")
_URL_KEYS = ("urls", "url", "links", "link", "targets", "href")
_BATCH_KEYS = ("requests", "calls", "items", "batch", "searches", "fetches")

#: §7 给独立技能定的 cap(本设计稿自己的事实源)。**scan 侧的 cap 不在这里** ——
#: 那是当日冻结的 `user_config_echo.max_queries`,唯一事实源在 run contract 里。
SPEC_ROLE_CAPS = {
    "global-intel": 8,
    "us-intel": 12,
    "company-intel": 12,
    "sector-intel": 6,
}

_WHITESPACE = re.compile(r"\s+")


class WebBudgetError(ValueError):
    """预算计量契约违约。"""


# ───────────────────────── 工具分类与 unit 解析 ─────────────────────────


def classify_tool(name: object) -> str:
    """工具名 → `search` / `fetch` / `other`。other 只计 `tool_calls`,不吃 cap。"""
    text = str(name or "").strip().lower()
    if not text:
        return KIND_OTHER
    tail = text.rsplit("__", 1)[-1]
    for candidate in (text, tail):
        if candidate in _SEARCH_NAMES:
            return KIND_SEARCH
        if candidate in _FETCH_NAMES:
            return KIND_FETCH
    for candidate in (text, tail):
        if any(hint in candidate for hint in _FETCH_HINTS):
            return KIND_FETCH
        if any(hint in candidate for hint in _SEARCH_HINTS):
            return KIND_SEARCH
    return KIND_OTHER


def normalize_query(query: object) -> str:
    """查询规范化 —— 去首尾空白、折叠内部空白、小写。去重按它的 hash。"""
    return _WHITESPACE.sub(" ", str(query or "").strip()).lower()


def query_hash(query: object) -> str:
    return hashlib.sha256(normalize_query(query).encode("utf-8")).hexdigest()[:16]


def _as_payload(request: object) -> object | None:
    """请求体 → JSON 对象。解析不出就是 `None`(→ UNMEASURED,不猜 1 条)。"""
    if isinstance(request, (dict, list)):
        return request
    text = str(request or "").strip()
    if not text:
        return None
    try:
        value = json.loads(text)
    except Exception:  # noqa: BLE001 - 不可解析是事实,不是崩溃理由
        return None
    return value if isinstance(value, (dict, list)) else None


def _collect(payload: object, keys: Sequence[str]) -> tuple[list[str], bool]:
    """从 payload 抽出一族 unit。返回 (units, complete)。

    `complete=False` 表示「batch 计数不完整」—— 结构里明摆着有 batch,但数不清里面
    有几条(空列表、元素类型不认识)。这种情况必须走 UNMEASURED,不能记 0 或 1。
    """
    units: list[str] = []
    complete = True
    if isinstance(payload, list):
        if not payload:
            return [], False
        for item in payload:
            sub, ok = _collect(item, keys)
            units.extend(sub)
            complete = complete and ok
        return units, complete
    if isinstance(payload, str):
        return ([payload] if payload.strip() else []), bool(payload.strip())
    if not isinstance(payload, Mapping):
        return [], False
    # `searches` 同时出现在 `_BATCH_KEYS` 与 `_QUERY_KEYS` 里。展开过的键必须记下来,
    # 否则 `{"searches": ["a", "b"]}` 会被数两遍 —— 预算翻倍,同时凭空造出等量的
    # 「重复查询」诊断:两个数一起错、方向一致,自洽得看不出问题。
    consumed: set[str] = set()
    for key in _BATCH_KEYS:
        if key in payload:
            consumed.add(key)
            value = payload[key]
            if not isinstance(value, list) or not value:
                complete = False
                continue
            for item in value:
                sub, ok = _collect(item, keys)
                units.extend(sub)
                complete = complete and ok
    for key in keys:
        if key not in payload or key in consumed:
            continue
        value = payload[key]
        if isinstance(value, str):
            if value.strip():
                units.append(value)
            else:
                complete = False
        elif isinstance(value, list):
            if not value:
                complete = False
            for item in value:
                if isinstance(item, str) and item.strip():
                    units.append(item)
                elif isinstance(item, Mapping):
                    sub, ok = _collect(item, keys)
                    units.extend(sub)
                    complete = complete and ok
                else:
                    complete = False
        elif isinstance(value, Mapping):
            sub, ok = _collect(value, keys)
            units.extend(sub)
            complete = complete and ok
        else:
            complete = False
    return units, complete


class ToolUnits:
    """一次工具调用拆出来的预算单位。`parsed=False` ⇒ 这一格只能 UNMEASURED。"""

    __slots__ = ("kind", "queries", "urls", "parsed", "reason")

    def __init__(self, kind: str, queries: tuple, urls: tuple, parsed: bool, reason: str | None):
        self.kind = kind
        self.queries = queries
        self.urls = urls
        self.parsed = parsed
        self.reason = reason

    def __repr__(self) -> str:  # pragma: no cover - 诊断用
        return (
            f"ToolUnits(kind={self.kind!r}, queries={len(self.queries)}, "
            f"urls={len(self.urls)}, parsed={self.parsed}, reason={self.reason!r})"
        )


def parse_units(row: Mapping) -> ToolUnits:
    """一行 `external_tools.jsonl` → 预算单位。**行数不冒充查询数**。"""
    kind = classify_tool(row.get("tool_name"))
    if kind == KIND_OTHER:
        # 非搜索 / 非抓取的外部工具:计入 tool_calls,但不消耗网查预算。
        return ToolUnits(kind, (), (), True, None)
    payload = _as_payload(row.get("request"))
    if payload is None:
        return ToolUnits(kind, (), (), False, "请求 payload 不可解析")
    if kind == KIND_SEARCH:
        queries, complete = _collect(payload, _QUERY_KEYS)
        if not queries:
            return ToolUnits(kind, (), (), False, "搜索调用里认不出 query 字段")
        return ToolUnits(
            kind,
            tuple(queries),
            (),
            complete,
            None if complete else "batch 计数不完整",
        )
    urls, complete = _collect(payload, _URL_KEYS)
    if not urls:
        return ToolUnits(kind, (), (), False, "抓取调用里认不出 url 字段")
    return ToolUnits(
        kind, (), tuple(urls), complete, None if complete else "batch 计数不完整"
    )


# ───────────────────────── 聚合 ─────────────────────────


def _parse_ts(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _bucket_key(row: Mapping) -> tuple[str, str, str]:
    return (
        str(row.get("role") or ""),
        str(row.get("stage") or ""),
        str(row.get("subject") or ""),
    )


def _cap_for(caps: Mapping | None, role: str) -> tuple[int | None, int | None]:
    """`caps` 支持两种写法:`{role: 8}` 或 `{role: {"search": 8, "fetch": 20}}`。"""
    if not caps:
        return None, None
    entry = caps.get(role)
    if entry is None:
        return None, None
    if isinstance(entry, Mapping):
        search = entry.get("search", entry.get(CAP_UNIT_SEARCH))
        fetch = entry.get("fetch", entry.get("fetched_urls"))
        return (
            int(search) if search is not None else None,
            int(fetch) if fetch is not None else None,
        )
    return int(entry), None


def _self_report(self_reports: Mapping | None, key: tuple[str, str, str]) -> int | None:
    if not self_reports:
        return None
    role, stage, subject = key
    for candidate in (f"{role}|{stage}|{subject}", f"{role}|{subject}", role, subject):
        if candidate in self_reports:
            value = self_reports[candidate]
            return int(value) if value is not None else None
    return None


def _empty_bucket(
    key: tuple[str, str, str],
    *,
    caps: Mapping | None,
    self_reports: Mapping | None,
    reasons: list[str],
) -> dict:
    role, stage, subject = key
    cap, fetch_cap = _cap_for(caps, role)
    return {
        "role": role or None,
        "stage": stage or None,
        "subject": subject or None,
        "measurement": UNMEASURED,
        "tool_calls": None,
        "search_queries": None,
        "fetched_urls": None,
        "failed_units": None,
        "duplicate_urls": None,
        "duplicate_queries": None,
        "wall_s": None,
        "cap_unit": CAP_UNIT_SEARCH,
        "cap": cap,
        "fetch_cap": fetch_cap,
        "cap_enforcement": UNMEASURED,
        "self_report_delta": None,
        "self_reported_queries": _self_report(self_reports, key),
        "cap_exceeded": None,
        "unmeasured_reasons": sorted(set(reasons)),
    }


def _build_bucket(
    key: tuple[str, str, str],
    rows: Sequence[Mapping],
    *,
    caps: Mapping | None,
    self_reports: Mapping | None,
    enforcement: str,
    extra_reasons: Iterable[str] = (),
) -> dict:
    role, stage, subject = key
    reasons: list[str] = list(extra_reasons)
    queries: list[str] = []
    urls: list[str] = []
    failed_units = 0
    search_complete = True
    fetch_complete = True
    seen_queries: set[str] = set()
    seen_urls: set[str] = set()
    duplicate_queries = 0
    duplicate_urls = 0
    starts: list[datetime] = []
    ends: list[datetime] = []

    for row in rows:
        units = parse_units(row)
        status = str(row.get("status") or "")
        failed = status in {"FAILED", "INCOMPLETE"}
        if not units.parsed:
            reasons.append(f"{row.get('tool_name')}: {units.reason}")
            if units.kind == KIND_SEARCH:
                search_complete = False
            elif units.kind == KIND_FETCH:
                fetch_complete = False
        for query in units.queries:
            normalized = query_hash(query)
            if normalized in seen_queries:
                duplicate_queries += 1
            seen_queries.add(normalized)
            queries.append(query)
        for url in units.urls:
            canonical = safe_canonical_url(url) or f"(rejected){normalize_query(url)}"
            if canonical in seen_urls:
                duplicate_urls += 1
            seen_urls.add(canonical)
            urls.append(canonical)
        if failed:
            failed_units += len(units.queries) + len(units.urls)
        started = _parse_ts(row.get("requested_at"))
        ended = _parse_ts(row.get("completed_at")) or started
        if started is not None:
            starts.append(started)
        if ended is not None:
            ends.append(ended)

    cap, fetch_cap = _cap_for(caps, role)
    measured = search_complete and fetch_complete and not reasons
    wall = None
    if starts and ends:
        span = max(ends) - min(starts)
        wall = round(max(span.total_seconds(), 0.0), 3)
    search_queries = len(queries) if search_complete else None
    fetched_urls = len(urls) if fetch_complete else None
    self_reported = _self_report(self_reports, key)
    delta = (
        int(self_reported) - search_queries
        if self_reported is not None and search_queries is not None
        else None
    )
    cap_exceeded = None
    if measured and cap is not None and search_queries is not None:
        cap_exceeded = search_queries > cap
    if measured and fetch_cap is not None and fetched_urls is not None:
        cap_exceeded = bool(cap_exceeded) or fetched_urls > fetch_cap
    return {
        "role": role or None,
        "stage": stage or None,
        "subject": subject or None,
        "measurement": MEASURED if measured else UNMEASURED,
        "tool_calls": len(rows),
        "search_queries": search_queries,
        "fetched_urls": fetched_urls,
        # 数不清 unit 时失败 unit 也不可信。
        "failed_units": failed_units if measured else None,
        "duplicate_urls": duplicate_urls if fetch_complete else None,
        "duplicate_queries": duplicate_queries if search_complete else None,
        "wall_s": wall,
        "cap_unit": CAP_UNIT_SEARCH,
        "cap": cap,
        "fetch_cap": fetch_cap,
        # 连观测都不成立时不许说「已观测」—— 否则下游会把它读成一次合规结论。
        "cap_enforcement": enforcement if measured else UNMEASURED,
        "self_report_delta": delta,
        "self_reported_queries": self_reported,
        "cap_exceeded": cap_exceeded,
        "unmeasured_reasons": sorted(set(reasons)),
    }


def build_budget(
    rows: Iterable[Mapping],
    *,
    run_id: object = None,
    engine: object = None,
    context: str = "scan",
    caps: Mapping | None = None,
    self_reports: Mapping | None = None,
    unbound: Iterable[Mapping] = (),
    transcript_bound: bool = True,
    enforcement: str = OBSERVED_ONLY,
) -> dict:
    """按 `(role, stage, subject)` 聚合 §6.2 的预算。

    `unbound`:transcript 没绑上 / 不可读的 invocation(`{role, stage, subject, reason}`)。
    它们**必须**出现在结果里且恒 `UNMEASURED` —— 「没有行」不等于「零查询」。

    `transcript_bound=False`(独立技能 harness 拿不到 transcript)→ 整份预算
    `UNMEASURED`,不是 0。
    """
    if enforcement not in ENFORCEMENTS:
        raise WebBudgetError(f"未知 cap_enforcement={enforcement!r};合法:{list(ENFORCEMENTS)}")
    grouped: dict[tuple[str, str, str], list[Mapping]] = {}
    for row in rows:
        grouped.setdefault(_bucket_key(row), []).append(row)

    unbound_reasons: dict[tuple[str, str, str], list[str]] = {}
    for item in unbound:
        key = _bucket_key(item)
        unbound_reasons.setdefault(key, []).append(
            f"transcript 未绑定 / 不可读:{item.get('reason') or item.get('status') or 'GONE'}"
        )

    buckets = []
    for key in sorted(set(grouped) | set(unbound_reasons)):
        rows_for_key = grouped.get(key, [])
        extra = list(unbound_reasons.get(key, []))
        if not transcript_bound:
            extra.append("harness 未提供 transcript —— 预算不可计量")
        if rows_for_key:
            buckets.append(
                _build_bucket(
                    key,
                    rows_for_key,
                    caps=caps,
                    self_reports=self_reports,
                    enforcement=enforcement,
                    extra_reasons=extra,
                )
            )
        else:
            buckets.append(
                _empty_bucket(key, caps=caps, self_reports=self_reports, reasons=extra)
            )

    totals = _totals(buckets, transcript_bound=transcript_bound, enforcement=enforcement)
    return {
        **_spec_rollup(buckets, totals),
        "schema_version": SCHEMA_VERSION,
        "run_id": str(run_id) if run_id is not None else None,
        "engine": str(engine) if engine is not None else None,
        "context": context,
        "generated_from": "external_tools.jsonl",
        "policy": (
            "一行工具调用 ≠ 一次查询;UNMEASURED 不是 0 也不代表未超限;"
            "自报只作诊断;两个引擎都只是 OBSERVED_ONLY,不宣称硬限频等价(§6.2/§6.5)"
        ),
        "totals": totals,
        "buckets": buckets,
    }


#: §6.2 逐字的 11 键。`scan/self_review.WEB_BUDGET_KEYS` 按**顶层**读它们:缺任一键
#: 消费者就判「不可用」→ 真值路退化成自报路,而两侧单测各用各的合成夹具、双双全绿
#: (FN-1 家训:消费者读没人生产的键)。所以 run 级 rollup 必须摊在顶层,与 `totals` 同源。
SPEC_KEYS: tuple[str, ...] = (
    "measurement", "tool_calls", "search_queries", "fetched_urls", "failed_units",
    "duplicate_urls", "wall_s", "cap_unit", "cap", "cap_enforcement", "self_report_delta",
)


def _spec_rollup(buckets: Sequence[Mapping], totals: Mapping) -> dict:
    """`totals` + 三个只在格级存在的量 → §6.2 的 11 键(run 级)。

    `cap`:各格 cap 一致才给数,不一致 → `None`(一个跨角色的「总 cap」是编的)。
    `wall_s` / `self_report_delta`:只在整份预算 MEASURED 时才求和 —— 量不到的格
    参与求和,等于用 0 冒充「这格没花时间 / 没背离」。
    """
    measured = totals.get("measurement") == MEASURED
    caps = {b.get("cap") for b in buckets}
    wall = [b.get("wall_s") for b in buckets if b.get("wall_s") is not None]
    deltas = [b.get("self_report_delta") for b in buckets
              if b.get("self_report_delta") is not None]
    return {
        "measurement": totals.get("measurement"),
        "tool_calls": totals.get("tool_calls"),
        "search_queries": totals.get("search_queries"),
        "fetched_urls": totals.get("fetched_urls"),
        "failed_units": totals.get("failed_units"),
        "duplicate_urls": totals.get("duplicate_urls"),
        "wall_s": (round(sum(wall), 3) if measured and wall else None),
        "cap_unit": CAP_UNIT_SEARCH,
        "cap": (caps.pop() if len(caps) == 1 else None),
        "cap_enforcement": totals.get("cap_enforcement"),
        "self_report_delta": (sum(deltas) if measured and deltas else None),
    }


def _totals(buckets: Sequence[Mapping], *, transcript_bound: bool, enforcement: str) -> dict:
    measured = bool(buckets) and transcript_bound and all(
        b["measurement"] == MEASURED for b in buckets
    )
    if not transcript_bound or not buckets:
        measured = False

    def total(field: str) -> int | None:
        if not measured:
            return None
        values = [b[field] for b in buckets]
        return sum(int(v) for v in values if v is not None)

    reasons = sorted({r for b in buckets for r in (b.get("unmeasured_reasons") or [])})
    if not buckets:
        reasons = sorted(set(reasons) | {"没有任何外部工具行 —— 无法证明「零查询」"})
    if not transcript_bound:
        reasons = sorted(set(reasons) | {"harness 未提供 transcript —— 预算不可计量"})
    exceeded = [
        f"{b['role']}|{b['stage']}|{b['subject']}" for b in buckets if b.get("cap_exceeded")
    ]
    return {
        "measurement": MEASURED if measured else UNMEASURED,
        "buckets": len(buckets),
        "tool_calls": total("tool_calls"),
        "search_queries": total("search_queries"),
        "fetched_urls": total("fetched_urls"),
        "failed_units": total("failed_units"),
        "duplicate_urls": total("duplicate_urls"),
        "duplicate_queries": total("duplicate_queries"),
        "cap_unit": CAP_UNIT_SEARCH,
        "cap_enforcement": enforcement if measured else UNMEASURED,
        "cap_exceeded_buckets": exceeded if measured else None,
        "unmeasured_reasons": reasons,
    }


def unbound_from_agent_index(index: Mapping | None) -> list[dict]:
    """`agents/index.json` → 未绑定 / 不可读 transcript 的名单。

    只取 `expected=True` 且 `status != PRESENT` 的行:结构上没有 transcript 的角色
    (`NOT_EXPECTED`)不该把整份预算拖成 UNMEASURED。
    """
    rows = []
    for item in (index or {}).get("invocations") or []:
        if not item.get("expected"):
            continue
        if str(item.get("status")) == "PRESENT":
            continue
        rows.append(
            {
                "role": item.get("role"),
                "stage": item.get("stage"),
                "subject": item.get("subject"),
                "status": item.get("status"),
                "reason": item.get("reason"),
            }
        )
    return rows


def write_budget(path: Path | str, budget: Mapping) -> bool:
    """幂等落盘:内容没变一个字节都不写(冻结后零写入)。"""
    return write_if_changed(Path(path), (canonical_json(dict(budget)) + "\n").encode("utf-8"))


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def materialize_web_budget(
    capsule: Path | str,
    *,
    run_id: object = None,
    engine: object = None,
    caps: Mapping | None = None,
    self_reports: Mapping | None = None,
    context: str = "scan",
    enforcement: str = OBSERVED_ONLY,
) -> dict:
    """finalize 子步骤 2:解析 batch 请求 → `capsule/lineage/web_budget.json`。"""
    root = Path(capsule)
    rows = _read_jsonl(root / "lineage/external_tools.jsonl")
    index_path = root / "agents/index.json"
    index = None
    if index_path.is_file():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - 索引损坏 → 名单空,但不拖垮 finalize
            index = None
    budget = build_budget(
        rows,
        run_id=run_id,
        engine=engine,
        context=context,
        caps=caps,
        self_reports=self_reports,
        unbound=unbound_from_agent_index(index),
        enforcement=enforcement,
    )
    write_budget(root / "lineage" / BUDGET_NAME, budget)
    return budget


def budget_telemetry(bucket: Mapping) -> dict:
    """预算格 → `claim_ledger.check_budget` 认的 telemetry。

    `UNMEASURED` 映射成 `basis="unmeasured"`:**缺计量既不等于超预算,也不等于合规**。
    """
    if str(bucket.get("measurement")) != MEASURED:
        return {
            "basis": "unmeasured",
            "self_reported_queries": bucket.get("self_reported_queries"),
            "reasons": list(bucket.get("unmeasured_reasons") or []),
        }
    return {
        "tool_search_calls": bucket.get("search_queries"),
        "tool_fetch_calls": bucket.get("fetched_urls"),
        "wall_seconds": bucket.get("wall_s"),
        "self_reported_queries": bucket.get("self_reported_queries"),
    }


__all__ = [
    "BUDGET_NAME",
    "CAP_UNIT_SEARCH",
    "ENFORCEMENTS",
    "HARD_CLAUDE",
    "KIND_FETCH",
    "KIND_OTHER",
    "KIND_SEARCH",
    "MEASURED",
    "OBSERVED_ONLY",
    "SPEC_KEYS",
    "SPEC_ROLE_CAPS",
    "UNMEASURED",
    "ToolUnits",
    "WebBudgetError",
    "budget_telemetry",
    "build_budget",
    "classify_tool",
    "materialize_web_budget",
    "normalize_query",
    "parse_units",
    "query_hash",
    "unbound_from_agent_index",
    "write_budget",
]
