"""Validate submitted outputs against the run-scoped artifact registry."""

from __future__ import annotations

import re
from collections.abc import Callable

from autoresearch.agents.utils.rating import RATINGS_5_TIER, parse_rating
from autoresearch.contracts.agent_output import L4_CARD
from autoresearch.session_agent import artifacts


class DomainValidationError(RuntimeError):
    """A produced artifact failed its declared domain output contract."""


_PROPOSAL_RE = re.compile(L4_CARD.field("proposal").pattern, re.IGNORECASE)
_P4_RE = re.compile(L4_CARD.field("p4_intent").pattern)
_EARLY_RE = re.compile(L4_CARD.field("early_stop").pattern)


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


_CONTRACT_VALIDATORS = {"stock.lite.v1": _stock_lite}


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
    "validate_registered_contract",
    "validate_submission_outputs",
]
