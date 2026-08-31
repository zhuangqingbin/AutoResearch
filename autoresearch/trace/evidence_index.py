#!/usr/bin/env python3
"""统一外源审计索引 —— 三类异构证据「各归各账、审计时统一索引」(确定性,零 LLM)。

design: docs/specs/2026-08-28-external-evidence-expansion-design.md §3 / §6.1 / §6.3 / §9

## 为什么是「索引」而不是「第四本账」

§3.2 已排除「数值 / 新闻 / 网查全塞 news_catalog」:stage / revision 语义不相容,
会制造 `standalone` 假 stage 与 raw 重复。也排除了「各留各处、不建统一索引」:
复盘无法从一条 claim 一跳定位 source / blob / cutoff。

所以本模块**只存引用**:

    确定层数值 / 结构化源  →  capsule/lineage/reads.jsonl        (source_lineage)
    新闻 / 事件            →  news_catalog.source_observation    (PIT: first_seen ≤ cutoff)
    稿件断言              →  _claim_ledger.csv                  (VERIFIED/UNVERIFIED/…)
    LLM 工具行            →  capsule/lineage/external_tools.jsonl
                                        │
                                        ▼
                    capsule/lineage/external_evidence_index.json

索引项一律带 `evidence_kind / row_id_or_hash / role / stage / subject / requested_at /
canonical_url / decision_cutoff / verification_status`,**不复制 raw 内容**(正文、
snippet、blob 字节都不进索引;只留 blob hash 这类内容寻址引用)。

## 冻结纪律(§6.1)

`materialize_evidence_index` 是 finalize 内部的**幂等子步骤**,跑在 MANIFEST 之前:

    1. 归档 / 绑定 transcript,物化 external_tools.jsonl   (capsule.materialize_agent_index)
    2. 解析 batch 请求 → web_budget.json                   (trace.web_budget)
    3. 汇合三类引用 → external_evidence_index.json         (本模块)
    4. 再跑 expected / completeness / replay,最后 MANIFEST → freeze

同输入重跑必须**字节级同产物**;内容没变就一个字节都不写(冻结后的 capsule 只读,
`_write_if_changed` 会在「内容变了但文件不可写」时显式抛错,而不是偷偷改历史)。

## 独立技能 adapter(§6.1 第二段)

macro / sector / stock full 不走 scan capsule,由 `materialize_report_trace` 在**报告
发布前**写 `$CTX/<skill>/<run>/trace/`,记 `context=macro_full|sector_full|stock_full`,
**不新增 news stage**。harness 拿不到 transcript → 预算 `UNMEASURED`(不是 0),
但确定层 source lineage 与稿件 claim 仍可索引。
"""
from __future__ import annotations

import ipaddress
import json
import os
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from autoresearch.trace.atomic import atomic_write_bytes, canonical_json

SCHEMA_VERSION = 1
INDEX_NAME = "external_evidence_index.json"
TOOLS_NAME = "external_tools.jsonl"

#: 索引里承认的四类证据。**互不替代**:确定层数值有 lineage,新闻有 PIT 目录,
#: 稿件断言有 claim 状态,LLM 工具行有 transcript —— 索引只是把它们对齐,不合并。
KIND_SOURCE_READ = "source_read"
KIND_NEWS_OBSERVATION = "news_observation"
KIND_CLAIM = "claim"
KIND_EXTERNAL_TOOL = "external_tool"
EVIDENCE_KINDS = (KIND_SOURCE_READ, KIND_NEWS_OBSERVATION, KIND_CLAIM, KIND_EXTERNAL_TOOL)

#: 每类各自的受控词表 —— 故意**不统一成一个** VERIFIED/FAILED:
#: 「接口取数成功」和「稿件断言被原文支撑」不是同一个结论,合并就是把可达当成已核。
STATUS_DETERMINISTIC_OK = "DETERMINISTIC_OK"
STATUS_DETERMINISTIC_FAILED = "DETERMINISTIC_FAILED"
STATUS_OBSERVED = "OBSERVED"
STATUS_TOOL_COMPLETED = "TOOL_COMPLETED"
STATUS_TOOL_FAILED = "TOOL_FAILED"
STATUS_TOOL_INCOMPLETE = "TOOL_INCOMPLETE"
CLAIM_STATUSES = ("VERIFIED", "UNVERIFIED", "REFUTED", "UNPARSED")

#: claim 状态 → **消费口径**(设计稿 §9 逐字):`VERIFIED` 可进正文判断;`UNVERIFIED`
#: 仅进证据附录 / 待核,**不得影响评级**;`REFUTED` / `UNPARSED` 从正文排除并记诊断。
#: 索引里带上它,是为了让「这条证据当时被允许用来做什么」可复核 —— 只记状态不记口径,
#: 复盘时就只能靠记忆重建规则。
CLAIM_CONSUMPTION: dict[str, str] = {
    "VERIFIED": "body_judgment",
    "UNVERIFIED": "appendix_only",
    "REFUTED": "excluded_diagnostic",
    "UNPARSED": "excluded_diagnostic",
}

VERIFICATION_STATUSES = (
    STATUS_DETERMINISTIC_OK,
    STATUS_DETERMINISTIC_FAILED,
    STATUS_OBSERVED,
    STATUS_TOOL_COMPLETED,
    STATUS_TOOL_FAILED,
    STATUS_TOOL_INCOMPLETE,
    *CLAIM_STATUSES,
)

#: `$CTX/<skill>/<run>/trace/` adapter 认的 context(§6.1)。scan 侧是 `scan`。
CONTEXT_SCAN = "scan"
REPORT_CONTEXTS = ("macro_full", "sector_full", "stock_full")
CONTEXTS = (CONTEXT_SCAN, *REPORT_CONTEXTS)

#: 索引项**禁止**出现的键 —— 出现即说明有人把 raw 内容复制进了索引(§6.1「只存引用」)。
FORBIDDEN_ENTRY_KEYS = frozenset(
    {"text", "content", "body", "raw", "snippet", "excerpt", "html", "result"}
)


class EvidenceIndexError(ValueError):
    """索引契约违约 —— 建索引时就该失败。"""


class FrozenEvidenceError(RuntimeError):
    """冻结后的证据文件内容要变了 —— 绝不静默改写历史。"""


class UrlRejected(ValueError):
    """URL 不满足 §9 入账前提(非公网 http(s) / 私网 / 本机 / 非法 scheme)。"""


def consumption_for(status: object) -> str:
    """claim 状态 → 消费口径。未知状态按最保守的「排除 + 记诊断」处理。"""
    return CLAIM_CONSUMPTION.get(str(status), "excluded_diagnostic")


# ───────────────────────── URL canonicalize(§3.1 / §9)─────────────────────────

#: 追踪参数:去掉它们不改变资源身份,留着会让同一篇原文变成 N 条「不同」证据。
_TRACKING_PREFIXES = ("utm_", "spm_", "at_", "share_", "wt_", "pk_", "mtm_", "hsa_")
_TRACKING_PARAMS = frozenset(
    {
        "cmpid", "dclid", "fbclid", "from", "gclid", "gclsrc", "guccounter",
        "igshid", "mc_cid", "mc_eid", "msclkid", "ncid", "ref", "ref_src",
        "refsrc", "s_cid", "scm", "sourceid", "spm", "sr_share", "srcid",
        "twclid", "utm", "vero_conv", "vero_id", "wt_mc", "xtor", "yclid",
        "_hsenc", "_hsmi", "__twitter_impression",
    }
)
_ALLOWED_SCHEMES = ("http", "https")
_DEFAULT_PORTS = {"http": "80", "https": "443"}
#: 私网 / 本机域名后缀。IP 字面量另走 `ipaddress` 判定。
_PRIVATE_HOST_SUFFIXES = (".local", ".localdomain", ".internal", ".intranet", ".home.arpa")
_PRIVATE_HOSTS = frozenset({"localhost", "localhost.localdomain", "ip6-localhost", "[::1]"})


def _is_tracking(key: str) -> bool:
    low = key.lower()
    return low in _TRACKING_PARAMS or any(low.startswith(p) for p in _TRACKING_PREFIXES)


def _reject_private_host(host: str) -> None:
    bare = host.strip("[]")
    if not bare:
        raise UrlRejected("URL 没有主机名")
    low = bare.lower()
    if low in _PRIVATE_HOSTS or any(low.endswith(s) for s in _PRIVATE_HOST_SUFFIXES):
        raise UrlRejected(f"本机 / 内网主机不入账:{host!r}")
    try:
        address = ipaddress.ip_address(bare)
    except ValueError:
        return
    if not address.is_global:
        raise UrlRejected(f"非公网地址不入账:{host!r}")


def canonicalize_url(url: object) -> str:
    """§9 入账前的统一 canonicalize。**拒绝**比「尽力修好」重要。

    - 只收公网 `http(s)`;`file:` / `data:` / `javascript:` / localhost / 私网一律抛
      `UrlRejected` —— 它们不是可复核的外部证据,是本机路径或注入面。
    - 去 fragment、去追踪参数、去 userinfo、去默认端口、query 排序 → 同一篇原文
      只有一个身份(URL 去重按它做)。
    """
    text = str(url or "").strip()
    if not text:
        raise UrlRejected("URL 为空")
    if any(ch in text for ch in ("\n", "\r", "\t")) or any(ord(ch) < 0x20 for ch in text):
        raise UrlRejected("URL 含控制字符")
    parts = urlsplit(text)
    scheme = parts.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise UrlRejected(f"只接受公网 http(s);拒绝 scheme={parts.scheme!r}")
    host = (parts.hostname or "").lower()
    _reject_private_host(host)
    port = parts.port
    netloc = host if parts.hostname is None or "[" not in parts.netloc else f"[{host}]"
    if ":" in host and not netloc.startswith("["):
        netloc = f"[{host}]"
    if port is not None and str(port) != _DEFAULT_PORTS.get(scheme):
        netloc = f"{netloc}:{port}"
    query = urlencode(
        sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking(k))
    )
    path = parts.path or "/"
    # fragment 恒去掉:`#section` 不是另一个资源。
    return urlunsplit((scheme, netloc, path, query, ""))


def safe_canonical_url(url: object) -> str | None:
    """canonicalize,不合格就是 `None` —— 给「拒了要记账但不能炸」的调用点。"""
    try:
        return canonicalize_url(url)
    except UrlRejected:
        return None


def follow_allowed_redirect(source: object, target: object, *, allowed_domains: Iterable[str]) -> str:
    """跟随重定向的**纯函数**判据(不联网):只允许落在允许域内。

    §9「跟随允许域重定向」在这里是可测的规则,不是抓取实现:调用方把已记录的
    重定向对交进来,由本函数裁定最终 canonical URL 能不能入账。
    """
    canonical_source = canonicalize_url(source)
    canonical_target = canonicalize_url(target)
    allow = {str(d).strip().lower().lstrip(".") for d in allowed_domains if str(d).strip()}
    host = urlsplit(canonical_target).hostname or ""
    source_host = urlsplit(canonical_source).hostname or ""
    allow.add(source_host)
    if not any(host == d or host.endswith(f".{d}") for d in allow if d):
        raise UrlRejected(f"重定向目标 {host!r} 不在允许域 {sorted(allow)!r}")
    return canonical_target


# ───────────────────────── 索引项 ─────────────────────────


def _entry(
    *,
    evidence_kind: str,
    row_id_or_hash: str,
    role: object = None,
    stage: object = None,
    subject: object = None,
    requested_at: object = None,
    canonical_url: str | None = None,
    decision_cutoff: object = None,
    verification_status: str,
    ref: str,
    blob_hash: object = None,
    consumption: object = None,
) -> dict:
    if evidence_kind not in EVIDENCE_KINDS:
        raise EvidenceIndexError(f"未知 evidence_kind={evidence_kind!r};合法:{list(EVIDENCE_KINDS)}")
    if verification_status not in VERIFICATION_STATUSES:
        raise EvidenceIndexError(f"未知 verification_status={verification_status!r}")
    return {
        "evidence_kind": evidence_kind,
        "row_id_or_hash": str(row_id_or_hash),
        "role": _text_or_none(role),
        "stage": _text_or_none(stage),
        "subject": _text_or_none(subject),
        "requested_at": _text_or_none(requested_at),
        "canonical_url": canonical_url,
        "decision_cutoff": _text_or_none(decision_cutoff),
        "verification_status": verification_status,
        "ref": str(ref),
        "blob_hash": _text_or_none(blob_hash),
        "consumption": _text_or_none(consumption),
    }


def _text_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _read_jsonl(path: Path) -> list[dict]:
    if not Path(path).is_file():
        return []
    rows: list[dict] = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:  # noqa: BLE001 - 半行是事实,不是崩溃理由
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _source_read_entries(rows: Iterable[Mapping], *, decision_cutoff) -> list[dict]:
    out = []
    for row in rows:
        status = str(row.get("status") or "")
        complete = bool(row.get("evidence_complete", True))
        verification = (
            STATUS_DETERMINISTIC_OK if status == "OK" and complete else STATUS_DETERMINISTIC_FAILED
        )
        # 确定层没有 URL 语义(endpoint + params 才是它的身份),所以 canonical_url 恒 None。
        identity = row.get("blob_hash") or row.get("correlation_id") or row.get("endpoint")
        out.append(
            _entry(
                evidence_kind=KIND_SOURCE_READ,
                row_id_or_hash=str(identity),
                role=row.get("endpoint"),
                stage=row.get("stage"),
                subject=row.get("subject"),
                requested_at=row.get("started_at"),
                canonical_url=None,
                decision_cutoff=decision_cutoff,
                verification_status=verification,
                ref=f"lineage/reads.jsonl#{identity}",
                blob_hash=row.get("blob_hash"),
            )
        )
    return out


def _news_entries(rows: Iterable[Mapping], *, decision_cutoff) -> list[dict]:
    out = []
    for row in rows:
        out.append(
            _entry(
                evidence_kind=KIND_NEWS_OBSERVATION,
                row_id_or_hash=str(row.get("source_observation_id")),
                role=row.get("source"),
                stage=row.get("available_stage"),
                subject=row.get("scan_run_id"),
                requested_at=row.get("first_seen_ts"),
                canonical_url=safe_canonical_url(row.get("url")),
                decision_cutoff=decision_cutoff,
                # 观测只证明「我们看见过」;是否支撑某条断言由 claim_ledger 单独判。
                verification_status=STATUS_OBSERVED,
                ref=f"news_catalog/source_observation#{row.get('source_observation_id')}",
                blob_hash=row.get("content_hash"),
            )
        )
    return out


def _claim_entries(rows: Iterable[Mapping], *, decision_cutoff, ledger_ref: str) -> list[dict]:
    out = []
    for row in rows:
        status = str(row.get("status") or "UNVERIFIED")
        if status not in CLAIM_STATUSES:
            raise EvidenceIndexError(f"未知 claim status={status!r}")
        raw_citation = str(row.get("raw_citation") or "")
        url = raw_citation.strip("[]").split("|")[-1] if "|" in raw_citation else ""
        out.append(
            _entry(
                evidence_kind=KIND_CLAIM,
                row_id_or_hash=str(row.get("claim_id")),
                role=row.get("role") or "claim_ledger",
                stage=row.get("stage"),
                subject=row.get("subject"),
                requested_at=row.get("effective_date"),
                canonical_url=safe_canonical_url(url),
                decision_cutoff=decision_cutoff,
                verification_status=status,
                ref=f"{ledger_ref}#{row.get('claim_id')}",
                consumption=consumption_for(status),
            )
        )
    return out


def _tool_entries(rows: Iterable[Mapping], *, decision_cutoff) -> list[dict]:
    statuses = {
        "COMPLETED": STATUS_TOOL_COMPLETED,
        "FAILED": STATUS_TOOL_FAILED,
        "INCOMPLETE": STATUS_TOOL_INCOMPLETE,
    }
    out = []
    for row in rows:
        identity = f"{row.get('invocation_id')}/{row.get('tool_call_id')}"
        out.append(
            _entry(
                evidence_kind=KIND_EXTERNAL_TOOL,
                row_id_or_hash=identity,
                role=row.get("role"),
                stage=row.get("stage"),
                subject=row.get("subject"),
                requested_at=row.get("requested_at"),
                canonical_url=safe_canonical_url(row.get("url")),
                decision_cutoff=decision_cutoff,
                verification_status=statuses.get(str(row.get("status")), STATUS_TOOL_INCOMPLETE),
                ref=f"lineage/{TOOLS_NAME}#{identity}",
                blob_hash=row.get("result_hash"),
            )
        )
    return out


def build_index(
    *,
    run_id: object = None,
    context: str = CONTEXT_SCAN,
    decision_cutoff: object = None,
    source_reads: Iterable[Mapping] = (),
    observations: Iterable[Mapping] = (),
    claims: Iterable[Mapping] = (),
    external_tools: Iterable[Mapping] = (),
    ledger_ref: str = "_claim_ledger.csv",
) -> dict:
    """把四类记录的**引用**汇成一个确定性索引。raw 一个字节都不复制。"""
    if context not in CONTEXTS:
        raise EvidenceIndexError(f"未知 context={context!r};合法:{list(CONTEXTS)}")
    entries = [
        *_source_read_entries(source_reads, decision_cutoff=decision_cutoff),
        *_news_entries(observations, decision_cutoff=decision_cutoff),
        *_claim_entries(claims, decision_cutoff=decision_cutoff, ledger_ref=ledger_ref),
        *_tool_entries(external_tools, decision_cutoff=decision_cutoff),
    ]
    entries.sort(key=lambda item: (item["evidence_kind"], item["row_id_or_hash"], item["ref"]))
    counts = {kind: sum(1 for e in entries if e["evidence_kind"] == kind) for kind in EVIDENCE_KINDS}
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": _text_or_none(run_id),
        "context": context,
        "decision_cutoff": _text_or_none(decision_cutoff),
        "policy": (
            "只存引用不复制 raw;证据不完整是法证诊断,不是业务门 —— "
            "不得据此追加 gate_fires 或淘汰股票(§6.1)"
        ),
        "counts": {**counts, "total": len(entries)},
        "entries": entries,
    }


def assert_reference_only(index: Mapping) -> None:
    """索引里出现任何 raw 内容键就是把证据复制了一遍 —— 直接抛。"""
    for entry in index.get("entries") or []:
        bad = sorted(FORBIDDEN_ENTRY_KEYS & set(entry))
        if bad:
            raise EvidenceIndexError(f"索引项复制了 raw 内容:{bad} —— §6.1 只存引用")


# ───────────────────────── 写盘(幂等 + 冻结零写入)─────────────────────────


def write_if_changed(path: Path | str, payload: bytes) -> bool:
    """内容一样就**一个字节都不写**;要变但文件不可写 → 抛,不偷改冻结历史。

    返回 True 表示真的写了。这是「二次 materialize 字节不变」与「MANIFEST / publish
    后零写入」两条验收共用的实现。
    """
    target = Path(path)
    if target.is_file() and target.read_bytes() == payload:
        return False
    if target.is_file() and not os.access(target, os.W_OK):
        raise FrozenEvidenceError(
            f"{target} 已冻结(只读)但新内容与冻结版本不同 —— 冻结后禁止追加 / 回填(§6.1)"
        )
    atomic_write_bytes(target, payload)
    return True


def write_json_if_changed(path: Path | str, value: object) -> bool:
    return write_if_changed(path, (canonical_json(value) + "\n").encode("utf-8"))


def _claim_rows(ledger_path: Path) -> list[dict]:
    if not ledger_path.is_file():
        return []
    import pandas as pd

    frame = pd.read_csv(ledger_path, dtype=str, keep_default_na=False)
    return [{str(k): v for k, v in row.items()} for row in frame.to_dict(orient="records")]


def _find_claim_ledger(root: Path | None) -> Path | None:
    if root is None or not Path(root).is_dir():
        return None
    from autoresearch.news.claim_ledger import LEDGER_NAME

    matches = sorted(Path(root).rglob(LEDGER_NAME))
    return matches[0] if matches else None


def _run_observations(run_id: object, catalog=None) -> list[dict]:
    """只索引**本 run** 产生的观测 —— 全局目录整表进索引既不确定也没意义。"""
    if run_id is None:
        return []
    try:
        if catalog is None:
            from autoresearch.news.catalog import NewsCatalog

            catalog = NewsCatalog()
        frame = catalog.observations()
    except Exception:  # noqa: BLE001 - 目录缺失 / 损坏不该拖垮 finalize
        return []
    if not len(frame):
        return []
    rows = frame[frame["scan_run_id"].astype(str) == str(run_id)]
    return [{str(k): v for k, v in row.items()} for row in rows.to_dict(orient="records")]


def _pit_filter(rows: Sequence[Mapping], decision_cutoff: object) -> list[dict]:
    """PIT:`first_seen_ts ≤ cutoff` 才进索引(§6.3)。cutoff 缺失 → 不过滤,原样带出。"""
    if not decision_cutoff:
        return [dict(row) for row in rows]
    from autoresearch.news.catalog import _parse  # noqa: PLC0415 - 共用同一个时间解析

    cutoff = _parse(decision_cutoff)
    if cutoff is None:
        return [dict(row) for row in rows]
    out = []
    for row in rows:
        seen = _parse(row.get("first_seen_ts"))
        if seen is not None and seen <= cutoff:
            out.append(dict(row))
    return out


def materialize_evidence_index(
    capsule: Path | str,
    *,
    run_id: object = None,
    staging: Path | str | None = None,
    decision_cutoff: object = None,
    catalog=None,
    context: str = CONTEXT_SCAN,
) -> dict:
    """finalize 子步骤 3:汇合三类记录的引用 → `capsule/lineage/external_evidence_index.json`。

    幂等:同输入重跑字节级同产物;内容没变就不写(冻结后零写入)。
    """
    root = Path(capsule)
    ledger = _find_claim_ledger(Path(staging) if staging else None)
    index = build_index(
        run_id=run_id,
        context=context,
        decision_cutoff=decision_cutoff,
        source_reads=_read_jsonl(root / "lineage/reads.jsonl"),
        observations=_pit_filter(_run_observations(run_id, catalog), decision_cutoff),
        claims=_claim_rows(ledger) if ledger else (),
        external_tools=_read_jsonl(root / f"lineage/{TOOLS_NAME}"),
        ledger_ref=(
            ledger.name if ledger else "_claim_ledger.csv"
        ),
    )
    assert_reference_only(index)
    write_json_if_changed(root / "lineage" / INDEX_NAME, index)
    return index


# ───────────────────────── 独立技能 adapter(§6.1 第二段)─────────────────────────


def materialize_report_trace(
    run_dir: Path | str,
    *,
    context: str,
    external_tool_rows: Sequence[Mapping] = (),
    transcript_bound: bool | None = None,
    source_reads: Iterable[Mapping] = (),
    observations: Iterable[Mapping] = (),
    claims: Iterable[Mapping] | None = None,
    decision_cutoff: object = None,
    caps: Mapping | None = None,
    self_reports: Mapping | None = None,
    engine: object = None,
    run_id: object = None,
) -> dict:
    """macro / sector / stock full 的 trace adapter —— **报告发布前**写三件套。

    落 `$CTX/<skill>/<run>/trace/{external_tools.jsonl,web_budget.json,
    external_evidence_index.json}`,记 `context`,**不新增 news stage**(独立技能的
    时点信息记在这里的 `decision_cutoff` / `context`,不去污染 scan 的 L0…L5)。

    harness 拿不到 transcript(`transcript_bound=False` 或压根没有工具行)→ 预算
    `UNMEASURED`,**不是 0**;确定层 source lineage 与稿件 claim 照常索引。
    """
    from autoresearch.trace import web_budget as web_budget_mod

    if context not in REPORT_CONTEXTS:
        raise EvidenceIndexError(
            f"独立技能 context 只能是 {list(REPORT_CONTEXTS)};收到 {context!r}"
        )
    trace_dir = Path(run_dir) / "trace"
    rows = [dict(row) for row in external_tool_rows]
    bound = bool(rows) if transcript_bound is None else bool(transcript_bound)

    tools_path = trace_dir / TOOLS_NAME
    write_if_changed(
        tools_path,
        "".join(canonical_json(row) + "\n" for row in rows).encode("utf-8"),
    )

    budget = web_budget_mod.build_budget(
        rows,
        run_id=run_id,
        engine=engine,
        context=context,
        caps=caps,
        self_reports=self_reports,
        transcript_bound=bound,
    )
    web_budget_mod.write_budget(trace_dir / web_budget_mod.BUDGET_NAME, budget)

    ledger_rows = (
        list(claims)
        if claims is not None
        else _claim_rows(Path(run_dir) / "_claim_ledger.csv")
    )
    index = build_index(
        run_id=run_id,
        context=context,
        decision_cutoff=decision_cutoff,
        source_reads=source_reads,
        observations=_pit_filter(list(observations), decision_cutoff),
        claims=ledger_rows,
        external_tools=rows,
    )
    assert_reference_only(index)
    write_json_if_changed(trace_dir / INDEX_NAME, index)
    return {"trace_dir": trace_dir, "web_budget": budget, "index": index}


__all__ = [
    "CLAIM_CONSUMPTION",
    "CLAIM_STATUSES",
    "CONTEXTS",
    "EVIDENCE_KINDS",
    "EvidenceIndexError",
    "FrozenEvidenceError",
    "INDEX_NAME",
    "REPORT_CONTEXTS",
    "UrlRejected",
    "assert_reference_only",
    "build_index",
    "canonicalize_url",
    "consumption_for",
    "follow_allowed_redirect",
    "materialize_evidence_index",
    "materialize_report_trace",
    "safe_canonical_url",
    "write_if_changed",
    "write_json_if_changed",
]
