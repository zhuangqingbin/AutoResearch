"""Validate submitted outputs against the run-scoped artifact registry."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, datetime
from urllib.parse import urlparse

from autoresearch.agents.utils.rating import RATINGS_5_TIER, parse_rating
from autoresearch.contracts.agent_output import L4_CARD
from autoresearch.macro.assemble import parse_allocation
from autoresearch.session_agent import artifacts


class DomainValidationError(RuntimeError):
    """A produced artifact failed its declared domain output contract."""


_PROPOSAL_RE = re.compile(L4_CARD.field("proposal").pattern, re.IGNORECASE)
_P4_RE = re.compile(L4_CARD.field("p4_intent").pattern)
_EARLY_RE = re.compile(L4_CARD.field("early_stop").pattern)

_NEWS_EVIDENCE_FIELDS = frozenset({
    "schema_version", "analysis_date", "title", "claim", "source_url",
    "source_tier", "published_at", "available_at", "canonical_status",
    "canonical_url",
})
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


def _evidence_time(value: object, field: str, analysis_date: date) -> str | None:
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
    if observed.date() > analysis_date:
        raise ValueError(f"future {field} is not admissible evidence")
    return value


def validate_news_evidence(value: dict, *, analysis_date: str) -> dict:
    """Validate one web/news claim without inventing unavailable time metadata."""
    from autoresearch.contracts.session_task import require_exact_fields, require_version

    require_exact_fields(value, _NEWS_EVIDENCE_FIELDS)
    require_version(value["schema_version"])
    try:
        cutoff = date.fromisoformat(analysis_date)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid analysis_date") from exc
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
    if value["source_tier"] == "T4" and (
        value["canonical_status"] != "FOLLOWED" or value["canonical_url"] is None
    ):
        raise ValueError("T4 aggregator evidence requires a canonical follow-up")
    if value["canonical_status"] == "FOLLOWED" and value["canonical_url"] is None:
        raise ValueError("canonical FOLLOWED status requires canonical_url")
    if value["canonical_status"] != "FOLLOWED" and value["canonical_url"] is not None:
        raise ValueError("canonical_url requires FOLLOWED status")
    return dict(value)


def _stock_lite(handle, submission: dict, task: dict) -> None:
    del submission, task
    with artifacts.open_artifact(handle, "stock.card.output") as stream:
        text = stream.read().decode("utf-8")
    rating = parse_rating(text, strict=True)
    proposal_match = _PROPOSAL_RE.search(text)
    if rating is None or proposal_match is None:
        raise DomainValidationError("stock card requires strict Rating and proposal lines")
    proposal = proposal_match.group(1).upper()
    expected = (
        "BUY"
        if rating in {"Buy", "Overweight"}
        else ("HOLD" if rating == "Hold" else "SELL")
    )
    if proposal != expected:
        raise DomainValidationError("stock card rating and proposal disagree")
    early = _EARLY_RE.search(text)
    rank = {value: index for index, value in enumerate(RATINGS_5_TIER)}
    if early is not None:
        if rank[rating] < rank["Hold"]:
            raise DomainValidationError("early-stop card cannot recommend a buy")
        return
    if rank[rating] < rank["Hold"]:
        if _P4_RE.search(text) is None:
            raise DomainValidationError("buy-rated lite card lacks P4 intent evidence")
        try:
            with artifacts.open_artifact(handle, "stock.deep"):
                pass
        except (KeyError, ValueError, RuntimeError) as exc:
            raise DomainValidationError("buy-rated lite card requires stock.deep") from exc


def _open_outputs(handle, submission: dict, task: dict) -> dict[str, str]:
    expected = set(task["output_artifact_ids"])
    actual = {item["artifact_id"] for item in submission["outputs"]}
    if actual != expected:
        raise DomainValidationError("inference output set is incomplete")
    values = {}
    for artifact_id in task["output_artifact_ids"]:
        try:
            with artifacts.open_artifact(handle, artifact_id) as stream:
                text = stream.read().decode("utf-8").strip()
        except (KeyError, ValueError, RuntimeError) as exc:
            raise DomainValidationError(f"missing output artifact: {artifact_id}") from exc
        if not text:
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
    if parse_rating(decision, strict=True) is None or _PROPOSAL_RE.search(decision) is None:
        raise DomainValidationError("stock PM decision lacks strict Rating or proposal")


def _macro_section(handle, submission: dict, task: dict) -> None:
    for artifact_id, text in _open_outputs(handle, submission, task).items():
        if "置信度:" not in text and "置信度：" not in text:
            raise DomainValidationError(f"macro section lacks confidence line: {artifact_id}")


def _macro_allocation(handle, submission: dict, task: dict) -> None:
    for artifact_id, text in _open_outputs(handle, submission, task).items():
        allocation = parse_allocation(text)
        if not allocation or any(value is None for value in allocation.values()):
            raise DomainValidationError(f"macro allocation is not parseable: {artifact_id}")
        if "置信度:" not in text and "置信度：" not in text:
            raise DomainValidationError(f"macro allocation lacks confidence: {artifact_id}")


def _macro_brief(handle, submission: dict, task: dict) -> None:
    text = next(iter(_open_outputs(handle, submission, task).values()))
    sections = set(re.findall(r"(?m)^\s*([1-6])[.、]\s*\*\*", text))
    if sections != {"1", "2", "3", "4", "5", "6"}:
        raise DomainValidationError("macro brief requires all six sections")


_SECTOR_DIRECTIONS = re.compile(r"超配|低配|回避|买入|卖出|买卖|看多|看空")
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


_CONTRACT_VALIDATORS = {
    "stock.lite.v1": _stock_lite,
    "stock.section.v1": _stock_section,
    "company.intel.v1": _stock_intel,
    "stock.pm.v1": _stock_pm,
    "macro.section.v1": _macro_section,
    "macro.allocation.v1": _macro_allocation,
    "macro.brief.v1": _macro_brief,
    "sector.terrain.v1": _sector_terrain,
    "sector.intel.v1": _sector_intel,
    "sector.full.v1": _sector_full,
    "dossier.v1": _dossier,
}


def validate_registered_contract(handle, submission: dict, task: dict) -> None:
    try:
        validator = _CONTRACT_VALIDATORS[task["expected_output_contract"]]
    except KeyError as exc:
        raise DomainValidationError(
            f"no validator registered for {task['expected_output_contract']}"
        ) from exc
    validator(handle, submission, task)


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
    "validate_news_evidence",
    "validate_registered_contract",
    "validate_submission_outputs",
]
