"""Deterministic, descriptive sector terrain candidate with bounded event facts.

No network, model calls, universe construction, or publication defaults live here.
All enrichment comes from the caller's frozen inputs. Legacy packs stay readable.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

from autoresearch.common.atomic import canonical_json, sha256_bytes

PROFILE = "deterministic-v1"
DIRECTION = re.compile(
    r"超配|低配|回避|买入|卖出|买卖|看多|看空|sector_healthy_top3|\b(?:Buy|Sell|Overweight|Underweight)\b",
    re.I,
)
# Units match existing pack aggregation. Fractions are not silently multiplied by 100.
FIELD_UNITS = {
    "n_market": "只",
    "n_l2": "只",
    "median_pct_60d": "%",
    "median_pe": "倍",
    "pe_p25": "倍",
    "pe_p75": "倍",
    "median_pb": "倍",
    "median_np_yoy": "%",
    "median_roe": "%",
    "main_pos_frac": "fraction",
    "main_net_sum_yi": "亿元",
    "healthy_n": "只",
    "median_winner": "%",
}
FINANCIAL_FIELDS = {"median_np_yoy", "median_roe"}
INPUT_FILES = (
    "L1_scored_full.csv",
    "L2_gbdt_top200.csv",
    "calendar.csv",
    "meta.json",
    "sector_evidence.json",
)
SIGNAL_KINDS = ("major_new_events", "source_conflicts", "required_gaps")


def digest(value) -> str:
    return sha256_bytes(canonical_json(value).encode())


def _date(value) -> str | None:
    text = str(value or "")
    if re.fullmatch(r"\d{8}", text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _plain(value) -> str:
    text = str(value)
    if DIRECTION.search(text):
        raise ValueError("directional language is forbidden in sector terrain")
    return text.replace("\n", " ").replace("\r", " ").replace("|", "／").replace("#", "＃")


def classification_metadata(industry: str, value: dict | None = None) -> dict:
    """Do not infer SWL1 membership from a vendor's bare industry name."""
    if value is None:
        return {
            "provider": "UNKNOWN",
            "system": "provider_industry",
            "version": "UNKNOWN",
            "original_code": None,
            "original_name": industry,
            "target_mapping": None,
            "coverage": 0.0,
            "ambiguity": "UNMAPPED",
        }
    fields = {
        "provider",
        "system",
        "version",
        "original_code",
        "original_name",
        "target_mapping",
        "coverage",
        "ambiguity",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("invalid sector classification metadata")
    if value["original_name"] != industry:
        raise ValueError("classification original name differs from pack industry")
    for key in ("provider", "system", "version", "original_name", "ambiguity"):
        if not isinstance(value[key], str) or not value[key]:
            raise ValueError(f"invalid classification {key}")
    if value["original_code"] is not None and not isinstance(value["original_code"], str):
        raise ValueError("classification original_code must be text or null")
    if type(value["coverage"]) not in (int, float) or not 0 <= value["coverage"] <= 1:
        raise ValueError("classification coverage must be a fraction")
    target = value["target_mapping"]
    if target is not None:
        required = {"system", "version", "level", "code", "name", "mapping_version", "source_ref"}
        if not isinstance(target, dict) or set(target) != required or target["level"] != 1:
            raise ValueError("target mapping requires explicit level-one identity and source")
        if any(not isinstance(target[key], str) or not target[key] for key in required - {"level"}):
            raise ValueError("invalid target mapping metadata")
        if value["coverage"] != 1 or value["ambiguity"] != "NONE":
            raise ValueError("partial or ambiguous mapping cannot relabel sector as level one")
    return dict(value)


def enrich_pack(pack: dict, scan_dir: Path | str, *, evidence: dict | None = None) -> dict:
    """Attach per-field provenance using exact files; never use filesystem mtime.

    Optional sector_evidence.json is a frozen owner-provided sidecar with sources,
    classifications, signals and corrections. Missing dates remain UNKNOWN even
    when the directory happens to be named after today's analysis session.
    """
    from autoresearch.sector.pack import _read_csv

    root = Path(scan_dir)
    if evidence is None:
        path = root / "sector_evidence.json"
        evidence = json.loads(path.read_text()) if path.is_file() else {}
    if not isinstance(evidence, dict) or set(evidence) - {
        "sources",
        "classifications",
        "signals",
        "corrections",
        "stable_facts",
    }:
        raise ValueError("invalid sector evidence sidecar")
    as_of = _date(pack.get("as_of"))
    if as_of is None:
        raise ValueError("terrain target date must be an ISO trading session")
    sources = {}
    for name in INPUT_FILES:
        path = root / name
        supplied = evidence.get("sources", {}).get(name, {})
        days = set()
        if path.is_file() and name.endswith(".csv"):
            frame = _read_csv(path)
            if frame is not None:
                for column in ("trade_date", "as_of", "date"):
                    if column in frame.columns:
                        days.update(
                            day for value in frame[column].dropna() if (day := _date(value))
                        )
        declared_date = _date(supplied.get("as_of"))
        if declared_date:
            days.add(declared_date)
        source_day = next(iter(days)) if len(days) == 1 else None
        sources[name] = {
            "artifact": name,
            "sha256": sha256_bytes(path.read_bytes()) if path.is_file() else None,
            "as_of": source_day,
            "provider": supplied.get("provider", "UNKNOWN"),
            "date_conflict": len(days) > 1,
        }
    fields = {}
    for key, unit in FIELD_UNITS.items():
        source = sources["L2_gbdt_top200.csv" if key == "n_l2" else "L1_scored_full.csv"]
        value = pack.get(key)
        gap = []
        if value is None or type(value) not in (int, float) or not math.isfinite(value):
            value = None
            gap.append("字段未核")
        if source["sha256"] is None:
            value = None
            gap.append("来源文件缺失")
        if source["as_of"] is None:
            gap.append("来源日期冲突" if source["date_conflict"] else "来源日期未核")
        elif source["as_of"] != as_of:
            gap.append(f"来源日期与目标日不符:{source['as_of']}")
        if key in FINANCIAL_FIELDS:
            gap.append("财务报告期未核")
        fields[key] = {
            "value": value,
            "unit": unit,
            "as_of": source["as_of"],
            "source_refs": [source],
            "gap": gap,
        }
    industry = str(pack["industry"])
    classification = classification_metadata(
        industry, evidence.get("classifications", {}).get(industry)
    )
    signals = evidence.get("signals", {}).get(industry, {})
    if not isinstance(signals, dict) or set(signals) - set(SIGNAL_KINDS):
        raise ValueError("invalid sector event signals")
    signals = {kind: list(signals.get(kind, [])) for kind in SIGNAL_KINDS}
    for kind, rows in signals.items():
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"id", "detail", "source_ref"}:
                raise ValueError(f"invalid {kind} event signal")
            if any(not isinstance(value, str) or not value.strip() for value in row.values()):
                raise ValueError("event signals require id, detail and frozen source_ref")
    for name, source in sources.items():
        if source["date_conflict"]:
            signals["source_conflicts"].append(
                {
                    "id": f"date-conflict:{name}",
                    "detail": f"{name} 数据日期冲突",
                    "source_ref": name,
                }
            )
    fingerprints = {
        "market": digest({key: fields[key] for key in FIELD_UNITS if key not in FINANCIAL_FIELDS}),
        "financial": digest({key: fields[key] for key in sorted(FINANCIAL_FIELDS)}),
        "event": digest({"signals": signals, "calendar": pack.get("calendar")}),
        "correction": digest(evidence.get("corrections", [])),
        "mapping": digest(classification),
    }
    return {
        **pack,
        "terrain": {
            "schema_version": 1,
            "profile": PROFILE,
            "target_date": as_of,
            "fields": fields,
            "sources": sources,
            "classification": classification,
            "signals": signals,
            "fingerprints": fingerprints,
            "stable_facts": list(evidence.get("stable_facts", {}).get(industry, [])),
        },
    }


def event_request(pack: dict, *, max_queries: int, knowledge_cutoff: str) -> dict:
    if type(max_queries) is not int or max_queries < 0:
        raise ValueError("event source budget must be a nonnegative integer")
    cutoff = datetime.fromisoformat(knowledge_cutoff.replace("Z", "+00:00"))
    if cutoff.tzinfo is None:
        raise ValueError("event knowledge_cutoff requires timezone")
    terrain = pack.get("terrain") or {}
    if terrain.get("profile") != PROFILE:
        raise ValueError("event request requires candidate terrain pack")
    reasons = [{**row, "kind": kind} for kind in SIGNAL_KINDS for row in terrain["signals"][kind]]
    if len({row["id"] for row in reasons}) != len(reasons):
        raise ValueError("duplicate sector event signal identity")
    value = {
        "schema_version": 1,
        "profile": PROFILE,
        "industry": pack["industry"],
        "analysis_date": terrain["target_date"],
        "pack_sha256": digest(pack),
        "knowledge_cutoff": knowledge_cutoff,
        "max_queries": max_queries,
        "max_events": max_queries,
        "reasons": reasons,
        "needed": bool(reasons),
        "dispatch": bool(reasons) and max_queries > 0,
    }
    return value


def validate_event_supplement(
    request: dict, supplement: dict, *, bind_claim: Callable | None = None
) -> dict:
    """Owner callback binds every admitted claim to B2 observations/raw source bytes.

    Models cannot self-authorize bindings. With no owner binding callback only an
    explicit unresolved result is acceptable; plausible URLs are not sufficient.
    """
    fields = {"schema_version", "pack_sha256", "events", "unresolved_reason_ids"}
    if (
        not isinstance(supplement, dict)
        or set(supplement) != fields
        or supplement["schema_version"] != 1
    ):
        raise ValueError("invalid sector event supplement")
    if supplement["pack_sha256"] != request["pack_sha256"]:
        raise ValueError("event supplement pack hash mismatch")
    events, unresolved = supplement["events"], supplement["unresolved_reason_ids"]
    if not isinstance(events, list) or len(events) > request["max_events"]:
        raise ValueError("event source budget exceeded")
    reasons = {row["id"] for row in request["reasons"]}
    if (
        not isinstance(unresolved, list)
        or any(item not in reasons for item in unresolved)
        or len(set(unresolved)) != len(unresolved)
    ):
        raise ValueError("invalid unresolved event reasons")
    covered = set(unresolved)
    cutoff = datetime.fromisoformat(request["knowledge_cutoff"].replace("Z", "+00:00"))
    event_fields = {
        "reason_id",
        "claim",
        "source_url",
        "published_at",
        "available_at",
        "source_observation_id",
        "source_text_sha256",
        "quote",
    }
    for event in events:
        if (
            not isinstance(event, dict)
            or set(event) != event_fields
            or event["reason_id"] not in reasons
        ):
            raise ValueError(
                "event output may only supplement declared reasons; numeric overrides forbidden"
            )
        if not all(isinstance(value, str) and value.strip() for value in event.values()):
            raise ValueError("event evidence fields required")
        _plain(event["claim"])
        if not event["source_url"].startswith(("https://", "http://")) or not re.fullmatch(
            "[0-9a-f]{64}", event["source_text_sha256"]
        ):
            raise ValueError("invalid event source URL/hash")
        for key in ("published_at", "available_at"):
            observed = datetime.fromisoformat(event[key].replace("Z", "+00:00"))
            if observed.tzinfo is None or observed > cutoff:
                raise ValueError("event evidence unavailable at frozen cutoff")
        if bind_claim is None or bind_claim(event, request).get("verdict") != "PASS":
            raise ValueError("sector event claim lacks verified source binding")
        covered.add(event["reason_id"])
    if covered != reasons:
        raise ValueError("event request reasons missing factual or unresolved coverage")
    return supplement


def render_terrain(
    pack: dict,
    *,
    request: dict | None = None,
    supplement: dict | None = None,
    bind_claim: Callable | None = None,
    stable_facts=(),
) -> str:
    """Render only known descriptive fields; preserve values, units and source dates."""
    terrain = pack.get("terrain") or {}
    if terrain.get("profile") != PROFILE or set(terrain.get("fields", {})) != set(FIELD_UNITS):
        raise ValueError("candidate terrain metadata is missing")
    lines = [
        f"# 行业 brief — {_plain(pack['industry'])} @ {terrain['target_date']}",
        "",
        "## 地形段",
        f"- 目标交易日: {terrain['target_date']}；profile: {PROFILE}",
    ]
    classification = classification_metadata(pack["industry"], terrain["classification"])
    lines.append("- 分类: " + _plain(canonical_json(classification)))
    for key, item in terrain["fields"].items():
        value = "未核" if item["value"] is None else canonical_json(item["value"])
        sources = ",".join(
            f"{source['artifact']}@{source['sha256'] or '未核'}" for source in item["source_refs"]
        )
        gap = "、".join(item["gap"]) or "无"
        lines.append(
            f"- {key}: {value} {item['unit']}；数据日期: {item['as_of'] or '未核'}；来源: {sources}；缺口: {gap}"
        )
    l1_source = terrain["sources"]["L1_scored_full.csv"]
    calendar_source = terrain["sources"]["calendar.csv"]
    for leader in pack.get("leaders") or []:
        # Explicit allowlist prevents extra model/direction fields entering L3/L4.
        row = {key: leader.get(key) for key in ("code", "name", "mktcap_yi", "pe", "pct_60d")}
        lines.append(
            "- 市值排序成分: "
            + _plain(canonical_json(row))
            + f"；单位: mktcap_yi=亿元,pe=倍,pct_60d=%；数据日期: {l1_source['as_of'] or '未核'}；来源: L1_scored_full.csv@{l1_source['sha256'] or '未核'}"
        )
    calendar = pack.get("calendar")
    lines.append(
        "- 已记录事件日历: "
        + (_plain(canonical_json(calendar)) if calendar else "未核；缺失不等于没有事件")
        + f"；数据日期: {calendar_source['as_of'] or '未核'}；来源: calendar.csv@{calendar_source['sha256'] or '未核'}"
    )
    if request is not None:
        if request["pack_sha256"] != digest(pack):
            raise ValueError("render event request does not match frozen pack")
        if request["dispatch"] and supplement is None:
            raise ValueError("required event supplement is missing")
        if supplement is not None:
            validate_event_supplement(request, supplement, bind_claim=bind_claim)
            for event in supplement["events"]:
                proof = bind_claim(event, request)
                timing = proof.get("source_timing")
                published, available = event["published_at"], event["available_at"]
                if timing is not None:
                    precision = timing["timestamp_precision"]
                    published = f"{timing['published_at']}（精度 {precision['published_at']}）"
                    available = f"{timing['first_available_at']}（精度 {precision['first_available_at']}）"
                lines.append(
                    f"- 事件事实: {_plain(event['claim'])}；来源: {_plain(event['source_url'])}；"
                    f"公开: {published}；可得: {available}；"
                    f"observation: {event['source_observation_id']}；原文hash: {event['source_text_sha256']}"
                )
            unresolved = supplement["unresolved_reason_ids"]
        else:
            unresolved = [row["id"] for row in request["reasons"]]
        for reason in unresolved:
            lines.append(f"- 事件缺口: {_plain(reason)} 未核")
    elif any(terrain["signals"].values()):
        raise ValueError("event signals require an explicit frozen event request")
    for fact in stable_facts:
        lines.append(
            f"- 稳定事实: {_plain(fact['claim'])}；原始日期: {fact['as_of']}；"
            f"来源: {_plain(fact['source_url'])}；原文hash: {fact['source_text_sha256']}"
        )
    text = "\n".join(lines) + "\n"
    if DIRECTION.search(text):
        raise ValueError("directional language is forbidden in sector terrain")
    return text


def compare_terrain_candidates(
    baseline_pack: dict,
    candidate_pack: dict,
    *,
    baseline_text: str,
    candidate_text: str,
    quality_checks: dict,
) -> dict:
    """Compare original field/evidence/unit/date coverage before considering cost.

    This is a review record only. Quality checks are owner review artifacts with
    verdict and evidence_refs; actual event verification and measurement are never
    inferred from the absence of an inference task.
    """
    checks = {"field_coverage", "event_evidence", "injection_scope", "actual_measurement"}
    if set(quality_checks) - checks:
        raise ValueError("unknown terrain comparison check")
    diffs, missing = [], []
    for key in ("industry", "as_of", "leaders", "calendar"):
        if baseline_pack.get(key) != candidate_pack.get(key):
            diffs.append(
                {
                    "path": key,
                    "baseline": baseline_pack.get(key),
                    "candidate": candidate_pack.get(key),
                }
            )
    for key in ("fields", "classification", "sources"):
        old, new = (
            baseline_pack.get("terrain", {}).get(key),
            candidate_pack.get("terrain", {}).get(key),
        )
        if old is None or new is None:
            missing.append("terrain." + key)
        elif old != new:
            diffs.append({"path": "terrain." + key, "baseline": old, "candidate": new})
    for key in checks:
        check = quality_checks.get(key)
        if check is None:
            missing.append(key)
            continue
        if not isinstance(check, dict) or set(check) != {"verdict", "evidence_refs"}:
            raise ValueError("invalid terrain comparison evidence")
        if check["verdict"] not in {"PASS", "FAIL", "INCOMPLETE"} or not isinstance(
            check["evidence_refs"], list
        ):
            raise ValueError("invalid terrain comparison evidence")
        if any(not isinstance(ref, str) or not ref for ref in check["evidence_refs"]):
            raise ValueError("invalid terrain comparison evidence reference")
        if not check["evidence_refs"] or check["verdict"] == "INCOMPLETE":
            missing.append(key)
        if check["verdict"] == "FAIL":
            diffs.append({"path": "quality." + key, "baseline": "PASS", "candidate": "FAIL"})
    return {
        "schema_version": 1,
        "profile": PROFILE,
        "deterministic_diffs": diffs,
        "research_diffs": []
        if baseline_text == candidate_text
        else [{"path": "terrain_text", "baseline": baseline_text, "candidate": candidate_text}],
        "missing_evidence": sorted(missing),
        "quality_checks": quality_checks,
        "verdict": "FAIL" if diffs else ("INCOMPLETE" if missing else "PASS"),
    }
