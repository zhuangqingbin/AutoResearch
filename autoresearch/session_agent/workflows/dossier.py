"""Session plan and conflict-safe publication for dossier initialization."""
from __future__ import annotations

import contextlib
import fcntl
import json
from collections.abc import Iterator
from pathlib import Path

from autoresearch.common.atomic import (
    atomic_write_bytes,
    atomic_write_json,
    canonical_json,
    sha256_bytes,
)
from autoresearch.contracts.session_plan import plan_hash
from autoresearch.dossier import pool, schema
from autoresearch.session_agent import artifacts
from autoresearch.session_agent.roles import roles_hash


def _task(
    task_id: str,
    kind: str,
    code: str,
    *,
    dependencies: list[str],
    inputs: list[str],
    outputs: list[str],
    contract: str,
    role: str | None = None,
    operation: str | None = None,
) -> dict:
    return {
        "task_id": task_id,
        "kind": kind,
        "role": role,
        "operation": operation,
        "dependencies": dependencies,
        "input_artifact_ids": inputs,
        "output_artifact_ids": outputs,
        "expected_output_contract": contract,
        "owner": "SESSION",
        "subject": code,
        "independent_context": False,
        "parent_task": None,
    }


def build_dossier_plan(request: dict, handle) -> dict:
    if request["kind"] != "dossier-init":
        raise ValueError("dossier plan requires dossier-init request")
    code = request["subject"]
    prefix = f"dossier.{code}"
    tasks = [
        _task(
            f"{prefix}.prefetch",
            "DETERMINISTIC",
            code,
            dependencies=[],
            inputs=[],
            outputs=["dossier.prefetch"],
            contract="dossier.prefetch.v1",
            operation="dossier.prefetch",
        ),
        _task(
            f"{prefix}.skeleton",
            "DETERMINISTIC",
            code,
            dependencies=[f"{prefix}.prefetch"],
            inputs=["dossier.prefetch"],
            outputs=["dossier.skeleton", "dossier.permissions"],
            contract="dossier.skeleton.v1",
            operation="dossier.skeleton",
        ),
        _task(
            f"{prefix}.research",
            "INFERENCE",
            code,
            dependencies=[f"{prefix}.skeleton"],
            inputs=["dossier.prefetch", "dossier.skeleton", "dossier.permissions"],
            outputs=["dossier.candidate"],
            contract="dossier.v1",
            role="dossier.init",
        ),
        _task(
            f"{prefix}.lint",
            "DETERMINISTIC",
            code,
            dependencies=[f"{prefix}.research"],
            inputs=["dossier.skeleton", "dossier.permissions", "dossier.candidate"],
            outputs=["dossier.validation"],
            contract="dossier.validation.v1",
            operation="dossier.validate",
        ),
        _task(
            f"{prefix}.publish",
            "DETERMINISTIC",
            code,
            dependencies=[f"{prefix}.lint"],
            inputs=["dossier.candidate", "dossier.validation"],
            outputs=["dossier.publication.bundle"],
            contract="dossier.publication.v1",
            operation="dossier.publish",
        ),
    ]
    config_hash = getattr(handle.contract, "config_hash", None) or sha256_bytes(
        canonical_json(getattr(handle.contract, "user_config", {})).encode("utf-8")
    )
    value = {
        "schema_version": 1,
        "engine": handle.engine,
        "run_id": handle.run_id,
        "run_kind": request["kind"],
        "requested_mode": request["requested_mode"],
        "analysis_date": request["analysis_date"],
        "orchestration_version": "session_v1",
        "input_contract_hash": handle.contract.contract_hash,
        "config_hash": config_hash,
        "host_profile_hash": sha256_bytes(
            canonical_json(request["host_profile"]).encode("utf-8")
        ),
        "roles_hash": roles_hash(),
        "tasks": tasks,
        "task_templates": [],
        "plan_hash": "0" * 64,
    }
    value["plan_hash"] = plan_hash(value)
    return value


def register_dossier_artifacts(request: dict, handle, plan: dict) -> None:
    del request, plan
    output = Path(handle.staging) / "session_outputs"
    for artifact_id, name in {
        "dossier.prefetch": "dossier.prefetch.json",
        "dossier.skeleton": "dossier.skeleton.md",
        "dossier.permissions": "dossier.permissions.json",
        "dossier.candidate": "dossier.candidate.md",
        "dossier.validation": "dossier.validation.json",
        "dossier.publication.bundle": "dossier.publication.json",
    }.items():
        artifacts.register_artifact(handle, artifact_id, output / name, "WRITE")


def validate_dossier_operation_params(request: dict, task: dict, params: dict) -> None:
    del request, task
    if params != {}:
        raise ValueError("dossier deterministic operations accept no parameters")


@contextlib.contextmanager
def _locked(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(f"{path.suffix}.lock").open("a+", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _update_pool(code: str, name: str | None, pool_path: Path | str | None) -> None:
    target = Path(pool_path) if pool_path is not None else pool.POOL_PATH
    with _locked(target):
        value = pool.load_pool(target)
        stocks = value.setdefault("stocks", {})
        entry = stocks.setdefault(
            code,
            {
                "name": name or "",
                "status": "active",
                "entered": None,
                "entry_reason": "dossier-init",
                "last_selected": None,
                "note": "",
            },
        )
        entry["status"] = "active"
        if name:
            entry["name"] = name
        pending = value.get("pending_init") or []
        value["pending_init"] = [
            item
            for item in pending
            if str(item.get("code") if isinstance(item, dict) else item).zfill(6) != code
        ]
        atomic_write_json(target, value)


def publish_dossier(
    handle,
    *,
    target_path: Path | str | None = None,
    pool_path: Path | str | None = None,
) -> Path:
    request = json.loads(
        (Path(handle.workspace) / "session/request.json").read_text(encoding="utf-8")
    )
    output = Path(handle.staging) / "session_outputs"
    bundle = json.loads((output / "dossier.publication.json").read_text(encoding="utf-8"))
    permissions = json.loads((output / "dossier.permissions.json").read_text(encoding="utf-8"))
    candidate = output / "dossier.candidate.md"
    candidate_bytes = candidate.read_bytes()
    if sha256_bytes(candidate_bytes) != bundle["candidate_sha256"]:
        raise RuntimeError("dossier candidate changed after validation")
    target = Path(target_path) if target_path is not None else schema.dossier_path(request["subject"])
    with _locked(target):
        current_hash = sha256_bytes(target.read_bytes()) if target.is_file() else None
        opening_hash = permissions.get("opening_target_sha256")
        if current_hash not in {opening_hash, bundle["candidate_sha256"]}:
            raise RuntimeError("CONFLICT: live dossier changed after session began")
        if current_hash != bundle["candidate_sha256"]:
            atomic_write_bytes(target, candidate_bytes)
    _update_pool(request["subject"], request.get("name"), pool_path)
    return target


__all__ = [
    "build_dossier_plan",
    "publish_dossier",
    "register_dossier_artifacts",
    "validate_dossier_operation_params",
]
