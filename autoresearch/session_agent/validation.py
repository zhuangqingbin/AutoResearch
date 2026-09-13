"""Validate submitted outputs against the run-scoped artifact registry."""

from __future__ import annotations

from collections.abc import Callable

from autoresearch.session_agent import artifacts


class DomainValidationError(RuntimeError):
    """A produced artifact failed its declared domain output contract."""


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


__all__ = ["DomainValidationError", "validate_submission_outputs"]
