"""Validate submitted outputs against the run-scoped artifact registry."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

from autoresearch.agents.utils.rating import RATINGS_5_TIER, validate_rating_and_proposal
from autoresearch.contracts.agent_output import L4_CARD
from autoresearch.macro.assemble import parse_allocation
from autoresearch.session_agent import artifacts


class DomainValidationError(RuntimeError):
    """A produced artifact failed its declared domain output contract."""


_P4_RE = re.compile(L4_CARD.field("p4_intent").pattern)
_EARLY_RE = re.compile(L4_CARD.field("early_stop").pattern)

_NEWS_EVIDENCE_FIELDS = frozenset({
    "schema_version", "analysis_date", "title", "claim", "source_url",
    "source_tier", "published_at", "available_at", "canonical_status",
    "canonical_url",
})
_NEWS_EVIDENCE_V2_FIELDS = _NEWS_EVIDENCE_FIELDS | {"received_at", "event_effective_at", "timestamp_precision"}
_SOURCE_TIERS = frozenset({"T1", "T2", "T3", "T4"})
_CANONICAL_STATES = frozenset({"FOLLOWED", "NOT_REQUIRED", "MISSING"})


def _http_url(value: object, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if type(value) is not str or not value:
        raise ValueError(f"{field} required")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"invalid {field}")
    return value


def _evidence_time(value: object, field: str, cutoff: date | datetime | None) -> str | None:
    if value is None:
        return None
    if type(value) is not str or not value:
        raise ValueError(f"invalid {field}")
    try:
        observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid {field}") from exc
    if observed.tzinfo is None:
        raise ValueError(f"{field} requires timezone")
    if cutoff is not None and (observed > cutoff if isinstance(cutoff, datetime) else observed.date() > cutoff):
        raise ValueError(f"future {field} is not admissible evidence")
    return value


def validate_news_evidence(value: dict, *, analysis_date: str, decision_frame: dict | None = None) -> dict:
    """Validate one web/news claim without inventing unavailable time metadata."""
    from autoresearch.contracts.session_task import require_exact_fields

    version = value.get("schema_version")
    if type(version) is not int or version not in {1, 2}:
        raise ValueError("unsupported news evidence schema")
    require_exact_fields(value, _NEWS_EVIDENCE_FIELDS if version == 1 else _NEWS_EVIDENCE_V2_FIELDS)
    try:
        cutoff = date.fromisoformat(analysis_date)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid analysis_date") from exc
    if decision_frame is not None:
        from autoresearch.contracts.execution import parse_aware, validate_decision_frame
        frame = validate_decision_frame(decision_frame)
        if frame["analysis_session"] != analysis_date:
            raise ValueError("decision frame analysis_session mismatch")
        cutoff = parse_aware(frame["knowledge_cutoff"])
    elif version == 2:
        raise ValueError("news evidence v2 requires frozen DecisionFrame")
    if value["analysis_date"] != analysis_date:
        raise ValueError("evidence analysis_date mismatch")
    for field in ("title", "claim"):
        if type(value[field]) is not str or not value[field].strip():
            raise ValueError(f"{field} required")
    if value["source_tier"] not in _SOURCE_TIERS:
        raise ValueError("invalid source_tier")
    if value["canonical_status"] not in _CANONICAL_STATES:
        raise ValueError("invalid canonical_status")
    _http_url(value["source_url"], "source_url")
    _http_url(value["canonical_url"], "canonical_url", optional=True)
    _evidence_time(value["published_at"], "published_at", cutoff)
    _evidence_time(value["available_at"], "available_at", cutoff)
    if version == 2:
        from autoresearch.contracts.claim_evidence import validate_effective_time
        from autoresearch.contracts.source_time import latest_possible, validate_source_times
        times = {"published_at": value["published_at"], "first_available_at": value["available_at"],
                 "received_at": value["received_at"], "timestamp_precision": {
                     "published_at": value["timestamp_precision"].get("published_at"),
                     "first_available_at": value["timestamp_precision"].get("available_at"),
                     "received_at": value["timestamp_precision"].get("received_at")}}
        require_exact_fields(value["timestamp_precision"], {"published_at", "available_at", "received_at"})
        validate_source_times(times)
        for field in ("published_at", "first_available_at"):
            latest = latest_possible(times[field], times["timestamp_precision"][field])
            if latest is not None and latest > cutoff:
                raise ValueError(f"future or unresolved {field} precision at cutoff")
        validate_effective_time(value["event_effective_at"])
    if value["source_tier"] == "T4" and (
        value["canonical_status"] != "FOLLOWED" or value["canonical_url"] is None
    ):
        raise ValueError("T4 aggregator evidence requires a canonical follow-up")
    if value["canonical_status"] == "FOLLOWED" and value["canonical_url"] is None:
        raise ValueError("canonical FOLLOWED status requires canonical_url")
    if value["canonical_status"] != "FOLLOWED" and value["canonical_url"] is not None:
        raise ValueError("canonical_url requires FOLLOWED status")
    return dict(value)


def _research_card_semantics(handle, text: str, task: dict, *, scan: bool = False) -> bool:
    from autoresearch.contracts.profiles import CURRENT_CARD_RULES, STRICT_CARD_RULES
    from autoresearch.scan.l4.card_io import card_from_text, card_rules_version
    from autoresearch.scan.l4.rubric import validate_card_decision

    version = card_rules_version(handle=handle)
    if version not in STRICT_CARD_RULES:
        return False
    subject = str(task.get("subject") or "")
    if not subject:
        request_path = Path(handle.workspace) / "session/request.json"
        if request_path.is_file():
            subject = str(json.loads(request_path.read_text(encoding="utf-8")).get("subject") or "")
    code = subject.split(".")[0]
    if version != CURRENT_CARD_RULES and not re.fullmatch(r"[0-9]{6}", code):
        # ResearchCard v1 identifies A shares. Other markets keep the strict
        # rating/action gate until their structured identity contract is versioned.
        if subject and not scan:
            return False
        raise DomainValidationError("ResearchCard requires an A-share subject")
    holding = False
    if scan:
        import csv
        import io

        try:
            with artifacts.open_artifact(handle, "scan.finalists") as stream:
                rows = list(csv.DictReader(io.StringIO(stream.read().decode("utf-8"))))
            matching = [row for row in rows if str(row.get("code", "")).zfill(6) == code]
            if len(matching) != 1:
                raise ValueError("card subject lacks unique frozen finalist")
            holding = matching[0].get("lane") == "pinned"
        except (KeyError, ValueError, RuntimeError) as exc:
            raise DomainValidationError(f"card finalist identity: {exc}") from exc
    try:
        if version == CURRENT_CARD_RULES:
            from autoresearch.common.card_decision import card_from_decision_text
            from autoresearch.contracts.execution import validate_decision_frame

            with artifacts.open_artifact(handle, "research.frame") as stream:
                frame = validate_decision_frame(json.loads(stream.read()))
            if not scan:
                holding = frame["usage"] == "holding_review"
            if scan:
                card = card_from_text(text, code=code, analysis_date=handle.analysis_date,
                                      holding=holding, rules_version=version)
            else:
                card = card_from_decision_text(
                    text, subject=subject, venue=frame["venue"], analysis_date=handle.analysis_date,
                    holding=holding,
                )
        else:
            card = card_from_text(text, code=code, analysis_date=handle.analysis_date,
                                  holding=holding, rules_version=version)
        if version == CURRENT_CARD_RULES:
            from autoresearch.news.card_claims import registered_card_semantics
            from autoresearch.trace.completeness import card_rating_bands_from_capsule
            output_ids = task.get("output_artifact_ids", [])
            artifact_id = output_ids[0] if len(output_ids) == 1 else (
                "stock.full.4_decision.decision" if task.get("task_id") == "stock.pm" else "stock.card.output")
            registered_card_semantics(handle, text, subject=card["subject"], frame=frame,
                frame_hash=artifacts.snapshot_artifact(handle, "research.frame")["sha256"],
                task=task, artifact_id=artifact_id, holding=holding,
                bands=card_rating_bands_from_capsule(handle.capsule))
        else:
            validate_card_decision(card)
    except (ValueError, KeyError, RuntimeError, OSError) as exc:
        raise DomainValidationError(f"ResearchCard decision: {exc}") from exc

    return holding

def _declared_deep_input(handle, task: dict, artifact_id: str) -> None:
    if "task_id" not in task:
        from autoresearch.session_agent import service
        task = service._task(handle, "stock.card")
    if artifact_id not in task.get("input_artifact_ids", []):
        raise ValueError(f"{artifact_id} must be a declared task input; deep DD 未核")
    with artifacts.open_artifact(handle, artifact_id) as stream:
        if stream.read().startswith(b"DEEP_EVIDENCE_UNAVAILABLE"):
            raise ValueError(f"{artifact_id} unavailable; deep DD 未核")


def _stock_lite_domain(handle, submission: dict, task: dict) -> list[str]:
    with artifacts.open_artifact(handle, "stock.card.output") as stream:
        text = stream.read().decode("utf-8")
    try:
        rating, _ = validate_rating_and_proposal(text)
    except ValueError as exc:
        raise DomainValidationError(f"stock card: {exc}") from exc
    _research_card_semantics(handle, text, task)
    early = _EARLY_RE.search(text)
    rank = {value: index for index, value in enumerate(RATINGS_5_TIER)}
    if early is not None:
        if rank[rating] < rank["Hold"]:
            raise DomainValidationError("early-stop card cannot recommend a buy")
        return []
    if rank[rating] < rank["Hold"] and _P4_RE.search(text) is None:
        raise DomainValidationError("buy-rated lite card lacks P4 intent evidence")
    if rating in {"Buy", "Overweight"} or _P4_RE.search(text) is not None:
        try:
            with artifacts.open_artifact(handle, "stock.deep"):
                pass
            from autoresearch.contracts.profiles import STRICT_CARD_RULES
            from autoresearch.scan.l4.card_io import card_rules_version

            if card_rules_version(handle=handle) in STRICT_CARD_RULES:
                _declared_deep_input(handle, task, "stock.deep")
                return ["stock.deep"]
        except (KeyError, ValueError, RuntimeError, OSError) as exc:
            raise DomainValidationError(f"lite deep DD requires stock.deep evidence: {exc}") from exc
    return []


def validate_deep_read_evidence(handle, submission: dict, task: dict, deep_ids: list[str]) -> None:
    from autoresearch.session_agent.evidence_bundle import require_read_evidence

    for artifact_id in deep_ids:
        try:
            require_read_evidence(handle, task, submission, artifact_id)
        except (KeyError, ValueError, RuntimeError, OSError) as exc:
            raise DomainValidationError(f"deep DD evidence unavailable; 未核: {artifact_id}: {exc}") from exc


def _stock_lite(handle, submission: dict, task: dict) -> None:
    validate_deep_read_evidence(handle, submission, task, _stock_lite_domain(handle, submission, task))


def _validate_rating_and_proposal(text: str) -> str:
    try:
        rating, _ = validate_rating_and_proposal(text)
    except ValueError as exc:
        raise DomainValidationError(f"stock card: {exc}") from exc
    early = _EARLY_RE.search(text)
    rank = {value: index for index, value in enumerate(RATINGS_5_TIER)}
    if early is not None and rank[rating] < rank["Hold"]:
        raise DomainValidationError("early-stop card cannot recommend a buy")
    if early is None and rank[rating] < rank["Hold"] and _P4_RE.search(text) is None:
        raise DomainValidationError("buy-rated lite card lacks P4 intent evidence")
    return rating


def _scan_l4_card_domain(handle, submission: dict, task: dict) -> list[str]:
    values = _open_outputs(handle, submission, task)
    if len(values) != 1:
        raise DomainValidationError("scan L4 card requires one output")
    text = next(iter(values.values()))
    rating = _validate_rating_and_proposal(text)
    holding = _research_card_semantics(handle, text, task, scan=True)
    from autoresearch.contracts.profiles import STRICT_CARD_RULES
    from autoresearch.scan.l4.card_io import card_rules_version

    required = []
    if (card_rules_version(handle=handle) in STRICT_CARD_RULES
            and (holding or rating in {"Buy", "Overweight"} or _P4_RE.search(text))):
        deep_ids = [item for item in task.get("input_artifact_ids", []) if item.endswith(".deep")]
        if len(deep_ids) != 1:
            raise DomainValidationError("scan deep DD needs one declared deep input; 未核")
        try:
            _declared_deep_input(handle, task, deep_ids[0])
            required = deep_ids
        except (KeyError, ValueError, RuntimeError, OSError) as exc:
            raise DomainValidationError(f"scan deep DD evidence unavailable; 未核: {exc}") from exc
    subject = str(task.get("subject") or "")
    if subject and subject not in text:
        raise DomainValidationError("scan L4 card identity is missing")
    return required


def _scan_l4_card(handle, submission: dict, task: dict) -> None:
    validate_deep_read_evidence(handle, submission, task, _scan_l4_card_domain(handle, submission, task))


def _scan_l4_intel(handle, submission: dict, task: dict) -> None:
    text = next(iter(_open_outputs(handle, submission, task).values()))
    if "## 事件段" not in text:
        raise DomainValidationError("scan L4 intel lacks 事件段")
    if "## 声明行" not in text:
        raise DomainValidationError("scan L4 intel lacks 声明行")


def _open_outputs(handle, submission: dict, task: dict) -> dict[str, str]:
    expected = set(task["output_artifact_ids"])
    actual = {item["artifact_id"] for item in submission["outputs"]}
    if actual != expected:
        raise DomainValidationError("inference output set is incomplete")
    values = {}
    for artifact_id in task["output_artifact_ids"]:
        try:
            with artifacts.open_artifact(handle, artifact_id) as stream:
                text = stream.read().decode("utf-8")
        except (KeyError, ValueError, RuntimeError) as exc:
            raise DomainValidationError(f"missing output artifact: {artifact_id}") from exc
        if not text.strip():
            raise DomainValidationError(f"empty output artifact: {artifact_id}")
        values[artifact_id] = text
    return values


def _stock_section(handle, submission: dict, task: dict) -> None:
    for artifact_id, text in _open_outputs(handle, submission, task).items():
        if "置信度:" not in text and "置信度：" not in text:
            raise DomainValidationError(f"stock section lacks confidence line: {artifact_id}")


def _stock_intel(handle, submission: dict, task: dict) -> None:
    _open_outputs(handle, submission, task)


def _stock_pm(handle, submission: dict, task: dict) -> None:
    values = _open_outputs(handle, submission, task)
    decisions = [text for key, text in values.items() if key.endswith(".decision")]
    if len(decisions) != 1:
        raise DomainValidationError("stock PM output lacks unique decision")
    decision = decisions[0]
    try:
        validate_rating_and_proposal(decision)
    except ValueError as exc:
        raise DomainValidationError(f"stock PM decision: {exc}") from exc
    from autoresearch.contracts.profiles import CURRENT_CARD_RULES
    from autoresearch.scan.l4.card_io import card_rules_version

    if card_rules_version(handle=handle) == CURRENT_CARD_RULES:
        _research_card_semantics(handle, decision, task)


def _macro_section(handle, submission: dict, task: dict) -> None:
    for artifact_id, text in _open_outputs(handle, submission, task).items():
        if "置信度:" not in text and "置信度：" not in text:
            raise DomainValidationError(f"macro section lacks confidence line: {artifact_id}")


def _macro_allocation(handle, submission: dict, task: dict) -> None:
    from autoresearch.macro.assemble import allocation_scope
    from autoresearch.session_agent.workflows.macro import macro_product_artifacts

    try:
        with artifacts.open_artifact(handle, "macro.data") as stream:
            scope = allocation_scope(stream.read().decode("utf-8"))
    except (KeyError, ValueError, RuntimeError) as exc:
        raise DomainValidationError(f"macro allocation scope: {exc}") from exc
    relative_by_id = {artifact_id: relative for relative, artifact_id in macro_product_artifacts().items()}
    for artifact_id, text in _open_outputs(handle, submission, task).items():
        try:
            relative = relative_by_id.get(artifact_id)
            if relative not in scope:
                raise ValueError(f"unknown allocation output: {artifact_id}")
            allocation = parse_allocation(text, expected_keys=scope[relative])
        except ValueError as exc:
            raise DomainValidationError(f"macro allocation {artifact_id}: {exc}") from exc
        if not allocation or any(value is None for value in allocation.values()):
            raise DomainValidationError(f"macro allocation is not parseable: {artifact_id}")
        if "置信度:" not in text and "置信度：" not in text:
            raise DomainValidationError(f"macro allocation lacks confidence: {artifact_id}")


def market_view_complete(text: str) -> bool:
    """Six sections as the macro-brief agent template writes them.

    ``.claude/agents/macro-brief.md``: sections 1–5 carry a bold title, section 6 is the
    plain disclaimer line ``6. 仅供研究,非投资建议。``.
    """
    titled = set(re.findall(r"(?m)^\s*([1-6])[.、]\s*\*\*", text))
    # Section 6 starts its own unindented line and carries text on that line: an indented
    # sub-item, a sentence opening with ``6.5%`` or a bare ``6.`` is not the disclaimer.
    disclaimer = re.search(r"(?m)^6[.、][ \t]*[^\s\d]", text) is not None
    return {"1", "2", "3", "4", "5"} <= titled and disclaimer


def _macro_brief(handle, submission: dict, task: dict) -> None:
    text = next(iter(_open_outputs(handle, submission, task).values()))
    if not market_view_complete(text):
        raise DomainValidationError("macro brief requires all six sections")


# 「买卖单」是 sector-brief 模板强制的资金流事实标签(「主动买卖单净流入合计」),不是方向措辞;
# 2026-10-02 首场真扫 8/8 份照模板写的 brief 栽在裸「买卖」上。与 domain_ops 同名正则保持一致。
_SECTOR_DIRECTIONS = re.compile(r"超配|低配|回避|买入|卖出|买卖(?!单)|看多|看空")
_SECTOR_SECTIONS = re.compile(r"(?m)^\s*#{1,6}\s*([1-6])[.、]\s*")


def _sector_terrain(handle, submission: dict, task: dict) -> None:
    from autoresearch.sector.brief import extract_terrain

    text = next(iter(_open_outputs(handle, submission, task).values()))
    terrain = extract_terrain(text)
    if not terrain:
        raise DomainValidationError("sector lite report lacks terrain section")
    if _SECTOR_DIRECTIONS.search(terrain):
        raise DomainValidationError("sector lite terrain contains directional language")


def _sector_intel(handle, submission: dict, task: dict) -> None:
    _open_outputs(handle, submission, task)


def _global_intel(handle, submission: dict, task: dict) -> None:
    text = next(iter(_open_outputs(handle, submission, task).values()))
    required = ("intel v2_macro", "## 事件段", "## 央行段", "## 数据发布段",
                "## 地缘/关税/制裁段", "## 美股龙头财报与指引段", "## 中国政策段",
                "## 资金与仓位段", "## 声明行")
    if any(marker not in text for marker in required):
        raise DomainValidationError("global intel lacks required machine sections")
    with artifacts.open_artifact(handle, "macro.intel.request") as stream:
        request = json.loads(stream.read().decode("utf-8"))
    count = re.search(r"网查\s*(\d+)\s*条", text)
    if count is None or int(count.group(1)) > request["source_budget"]["max_queries"]:
        raise DomainValidationError("global intel missing or exceeded declared source budget")


def _sector_full(handle, submission: dict, task: dict) -> None:
    import json

    text = next(iter(_open_outputs(handle, submission, task).values()))
    sections = {int(match.group(1)) for match in _SECTOR_SECTIONS.finditer(text)}
    if sections != {1, 2, 3, 4, 5, 6}:
        raise DomainValidationError("sector full report requires six sections")
    with artifacts.open_artifact(handle, "sector.pack") as stream:
        pack = json.loads(stream.read().decode("utf-8"))
    for item in pack.get("readthrough") or []:
        if item.get("kind") == "company":
            continue
        symbol = re.escape(str(item.get("symbol") or ""))
        if symbol and re.search(rf"(?im)^.*{symbol}.*(?:公司|财报|业绩指引).*$", text):
            raise DomainValidationError("non-company readthrough described as a company")


def _dossier(handle, submission: dict, task: dict) -> None:
    del submission, task
    from autoresearch.session_agent.domain_ops import _validate_dossier_candidate

    _validate_dossier_candidate(handle)


def _scan_l3(handle, submission: dict, task: dict) -> None:
    import json

    value = next(iter(_open_outputs(handle, submission, task).values()))
    try:
        rows = json.loads(value)
    except json.JSONDecodeError as exc:
        raise DomainValidationError("scan L3 output is malformed JSON") from exc
    from autoresearch.scan.l3.validation import validate_rank_rows

    version = 2 if task["expected_output_contract"] == "scan.l3.v2" else 1
    try:
        validate_rank_rows(rows, expected_version=version)
    except ValueError as exc:
        raise DomainValidationError(str(exc)) from exc


def _scan_l3_repair(handle, submission: dict, task: dict) -> None:
    import json

    value = next(iter(_open_outputs(handle, submission, task).values()))
    try:
        rows = json.loads(value)
    except json.JSONDecodeError as exc:
        raise DomainValidationError("scan L3 repair output is malformed JSON") from exc
    if not isinstance(rows, list) or not rows:
        raise DomainValidationError("scan L3 repair output must be a non-empty list")
    with artifacts.open_artifact(handle, "scan.l3.repair.pack") as stream:
        pack = json.loads(stream.read().decode("utf-8"))
    expected = {str(code).zfill(6) for code in pack.get("codes") or []}
    actual = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"code", "thesis"}:
            raise DomainValidationError("scan L3 repair rows require only code and thesis")
        code = str(row["code"]).zfill(6)
        if (
            code in actual
            or not isinstance(row["thesis"], str)
            or not row["thesis"].strip()
        ):
            raise DomainValidationError("scan L3 repair contains duplicate or empty rows")
        actual.add(code)
    if actual != expected:
        raise DomainValidationError("scan L3 repair codes do not match the repair pack")


def _sector_events(handle, submission: dict, task: dict) -> None:
    from autoresearch.sector.terrain import validate_event_supplement
    from autoresearch.session_agent.sector_terrain import source_binding
    request_ids = [key for key in task["input_artifact_ids"] if key.endswith(".events.request")]
    if len(request_ids) != 1:
        raise DomainValidationError("sector events require one frozen request")
    with artifacts.open_artifact(handle, request_ids[0]) as stream:
        request = json.load(stream)
    values = _open_outputs(handle, submission, task)
    if len(values) != 1:
        raise DomainValidationError("sector events require one JSON output")
    try:
        payload = json.loads(next(iter(values.values())))
        binding = source_binding(handle, task_id=task["task_id"], attempt=submission["envelope"]["attempt"]) if payload.get("events") else None
        validate_event_supplement(request, payload, bind_claim=binding)
    except (ValueError, TypeError, KeyError) as exc:
        raise DomainValidationError(f"sector event evidence: {exc}") from exc


_CONTRACT_VALIDATORS = {
    "stock.lite.v1": _stock_lite,
    "stock.section.v1": _stock_section,
    "company.intel.v1": _stock_intel,
    "global.intel.v1": _global_intel,
    "stock.pm.v1": _stock_pm,
    "macro.section.v1": _macro_section,
    "macro.allocation.v1": _macro_allocation,
    "macro.brief.v1": _macro_brief,
    "sector.terrain.v1": _sector_terrain,
    "sector.events.v1": _sector_events,
    "sector.intel.v1": _sector_intel,
    "sector.full.v1": _sector_full,
    "dossier.v1": _dossier,
    "scan.l3.v1": _scan_l3,
    "scan.l3.v2": _scan_l3,
    "scan.l3.repair.v1": _scan_l3_repair,
    "scan.l4.intel.v1": _scan_l4_intel,
}


def validate_registered_domain_contract(handle, submission: dict, task: dict) -> list[str]:
    from autoresearch.session_agent.card_facts import (
        DECISION_CONTRACT,
        INITIAL_CONTRACT,
        validate_changes_output,
        validate_initial_output,
    )
    contract = task.get("expected_output_contract")
    if contract in {INITIAL_CONTRACT, DECISION_CONTRACT}:
        try:
            values = _open_outputs(handle, submission, task)
            if contract == INITIAL_CONTRACT:
                validate_initial_output(handle, submission, task)
            else:
                card_ids = [key for key in values if not key.endswith(".changes")]
                if len(card_ids) != 1:
                    raise ValueError("decision requires one final card and one changes sidecar")
                validate_changes_output(handle, task, values[card_ids[0]])
                card_task = dict(task, output_artifact_ids=card_ids)
                card_submission = dict(submission, outputs=[row for row in submission["outputs"]
                                                           if row["artifact_id"] in card_ids])
                if task["role"] == "scan.l4.card":
                    return _scan_l4_card_domain(handle, card_submission, card_task)
                else:
                    return _stock_lite_domain(handle, card_submission, card_task)
        except (ValueError, KeyError, RuntimeError, OSError) as exc:
            raise DomainValidationError(f"two-stage card: {exc}") from exc
        return []
    if task.get("role") in {"scan.l4.card", "scan.l4.review"}:
        return _scan_l4_card_domain(handle, submission, task)
    if contract == "stock.lite.v1":
        return _stock_lite_domain(handle, submission, task)
    try:
        validator = _CONTRACT_VALIDATORS[task["expected_output_contract"]]
    except KeyError as exc:
        raise DomainValidationError(
            f"no validator registered for {task['expected_output_contract']}"
        ) from exc
    validator(handle, submission, task)
    return []


def validate_registered_contract(handle, submission: dict, task: dict) -> None:
    """Formal acceptance always checks both domain semantics and required reads."""
    deep_ids = validate_registered_domain_contract(handle, submission, task)
    validate_deep_read_evidence(handle, submission, task, deep_ids)


def validate_submission_outputs(
    handle,
    submission: dict,
    task: dict,
    *,
    domain_validator: Callable[[dict, dict], object] | None = None,
) -> None:
    """Re-open every declared output, verify its hash, then run domain checks."""
    declared = {item["artifact_id"]: item["sha256"] for item in submission["outputs"]}
    if set(declared) != set(task["output_artifact_ids"]):
        raise DomainValidationError("submitted outputs do not match task outputs")
    for artifact_id, expected_hash in declared.items():
        try:
            descriptor = artifacts.bind_artifact_hash(handle, artifact_id)
            if descriptor["sha256"] != expected_hash:
                raise DomainValidationError(f"artifact hash mismatch: {artifact_id}")
            with artifacts.open_artifact(handle, artifact_id):
                pass
        except DomainValidationError:
            raise
        except (KeyError, ValueError, RuntimeError) as exc:
            raise DomainValidationError(str(exc)) from exc
    if domain_validator is not None:
        try:
            domain_validator(submission, task)
        except DomainValidationError:
            raise
        except Exception as exc:
            raise DomainValidationError(str(exc)) from exc


__all__ = [
    "DomainValidationError",
    "market_view_complete",
    "validate_news_evidence",
    "validate_registered_contract",
    "validate_registered_domain_contract",
    "validate_deep_read_evidence",
    "validate_submission_outputs",
]
