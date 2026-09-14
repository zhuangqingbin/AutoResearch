"""Publication adapters for completed subscription-session runs."""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.common.atomic import atomic_write_json, canonical_json, sha256_bytes
from autoresearch.common.commit_chain import read_publication_chain
from autoresearch.contracts.profiles import profile_factory
from autoresearch.contracts.publication import (
    publication_bundle_hash,
    validate_publication_bundle,
    validate_publication_receipt,
)
from autoresearch.session_agent import artifacts
from autoresearch.session_agent.roles import role_stage

_PUBLISHERS: dict[str, str | Callable[[object], object]] = {
    "scan-market": "autoresearch.session_agent.workflows.scan:publish_scan",
    "stock-research": "autoresearch.session_agent.workflows.stock:publish_stock",
    "macro-research": "autoresearch.session_agent.workflows.macro:publish_macro",
    "sector-research": "autoresearch.session_agent.workflows.sector:publish_sector",
    "dossier-init": "autoresearch.session_agent.workflows.dossier:publish_dossier",
}

_PREPARERS = {
    "scan-market": "autoresearch.session_agent.workflows.scan:prepare_scan_bundle",
    "stock-research": "autoresearch.session_agent.workflows.stock:prepare_stock_bundle",
    "macro-research": "autoresearch.session_agent.workflows.macro:prepare_macro_bundle",
    "sector-research": "autoresearch.session_agent.workflows.sector:prepare_sector_bundle",
    "dossier-init": "autoresearch.session_agent.workflows.dossier:prepare_dossier_bundle",
}


def register_publisher(run_kind: str, publisher: str | Callable[[object], object]) -> None:
    if not run_kind or not (callable(publisher) or isinstance(publisher, str)):
        raise ValueError("run kind and publisher required")
    if run_kind in _PUBLISHERS and _PUBLISHERS[run_kind] is not publisher:
        raise RuntimeError(f"publisher already registered: {run_kind}")
    _PUBLISHERS[run_kind] = publisher


def publish(handle):
    """Invoke the registered domain publisher for a finished task graph."""
    try:
        target = _PUBLISHERS[handle.contract.run_kind]
    except KeyError as exc:
        raise RuntimeError(
            f"no session publisher registered for {handle.contract.run_kind}"
        ) from exc
    if isinstance(target, str):
        module_name, separator, attribute = target.partition(":")
        if not separator:
            raise RuntimeError(f"invalid publisher target: {target}")
        publisher = getattr(importlib.import_module(module_name), attribute)
    else:
        publisher = target
    return publisher(handle)


def _load_target(target: str):
    module_name, separator, attribute = target.partition(":")
    if not separator:
        raise RuntimeError(f"invalid publication target: {target}")
    return getattr(importlib.import_module(module_name), attribute)


def _read_artifact(handle, artifact_id: str, inline: dict[str, Path]) -> bytes:
    source = inline.get(artifact_id)
    if source is not None:
        return source.read_bytes()
    with artifacts.open_artifact(handle, artifact_id) as stream:
        return stream.read()


def _publication_predecessor(handle) -> dict | None:
    request = json.loads(
        (Path(handle.workspace) / "session/request.json").read_text(encoding="utf-8")
    )
    predecessor_run_id = request.get("predecessor_run_id")
    if predecessor_run_id is None:
        return None
    matches = []
    for run_kind in ws.RUN_REPORT_DIRS:
        root = ws.run_reports_root(run_kind)
        candidate_root = root / "_publications" / predecessor_run_id
        if not candidate_root.is_dir():
            continue
        for path in sorted(candidate_root.glob("*.json")):
            value = json.loads(path.read_text(encoding="utf-8"))
            validate_publication_receipt(value)
            chain = read_publication_chain(root / "_publications/receipts.jsonl")
            if (
                value in chain
                and value["engine"] == handle.engine
                and value["run_id"] == predecessor_run_id
            ):
                matches.append(value)
    if len(matches) != 1:
        raise RuntimeError("publication predecessor must resolve to exactly one committed receipt")
    receipt = matches[0]
    return {
        "engine": receipt["engine"],
        "run_id": receipt["run_id"],
        "publication_id": receipt["publication_id"],
        "root_hash": receipt["capsule_root_hash"],
    }


def prepare_bundle(handle) -> tuple[dict, Callable[[str], bytes]]:
    """Convert one domain's frozen publication description to PublicationBundle v1."""
    try:
        prepare = _load_target(_PREPARERS[handle.contract.run_kind])
    except KeyError as exc:
        raise RuntimeError(f"no publication preparer for {handle.contract.run_kind}") from exc
    contents = prepare(handle)
    inline = {key: Path(value) for key, value in contents["inline_artifacts"].items()}

    def reader(artifact_id: str) -> bytes:
        return _read_artifact(handle, artifact_id, inline)

    business_files = []
    for row in contents["business_files"]:
        payload = reader(row["artifact_id"])
        business_files.append(
            {
                **row,
                "sha256": sha256_bytes(payload),
                "bytes": len(payload),
            }
        )
    state_mutations = []
    for row in contents["state_mutations"]:
        payload = reader(row["after_artifact_id"])
        state_mutations.append({**row, "after_hash": sha256_bytes(payload)})
    workspace = Path(handle.workspace)
    capsule = Path(handle.capsule)
    origin = json.loads((capsule / "identity/execution_origin.json").read_text(encoding="utf-8"))
    plan = json.loads((workspace / "session/plan.json").read_text(encoding="utf-8"))
    evidence = json.loads((capsule / "evidence/evidence_plan.json").read_text(encoding="utf-8"))
    value = {
        "schema_version": 1,
        "engine": handle.engine,
        "run_id": handle.run_id,
        "run_kind": handle.contract.run_kind,
        "publication_id": "p1",
        "predecessor": _publication_predecessor(handle),
        "origin_hash": sha256_bytes(canonical_json(origin).encode("utf-8")),
        "plan_hash": plan["plan_hash"],
        "evidence_plan_hash": evidence["evidence_plan_hash"],
        "business_files": business_files,
        "state_mutations": state_mutations,
        "generated_at": evidence["closure_cutoff"],
        "bundle_hash": "0" * 64,
    }
    value["bundle_hash"] = publication_bundle_hash(value)
    return validate_publication_bundle(value), reader


def _compatibility_view(handle, canonical: Path, receipt: dict) -> None:
    """Refresh legacy mirrors only after the commit receipt is externally visible."""
    del canonical
    target = _PUBLISHERS[handle.contract.run_kind]
    publisher = _load_target(target) if isinstance(target, str) else target
    module = importlib.import_module(publisher.__module__)
    active = getattr(module, f"_{publisher.__name__}_active")
    delivered = Path(active(handle))
    _write_delivery_sidecar(handle, delivered, receipt)


def _write_delivery_sidecar(handle, delivered: Path, receipt: dict) -> Path:
    """Bind a mutable legacy delivery path back to its committed canonical root."""
    allowed = (
        ws.context_root()
        if handle.contract.run_kind == "dossier-init"
        else ws.run_reports_root(handle.contract.run_kind)
    ).resolve(strict=True)
    delivered.resolve(strict=True).relative_to(allowed)
    sidecar = (
        delivered / "delivery.json"
        if delivered.is_dir()
        else delivered.with_name(f"{delivered.name}.delivery.json")
    )
    value = {
        "schema_version": 1,
        "engine": receipt["engine"],
        "run_id": receipt["run_id"],
        "publication_id": receipt["publication_id"],
        "bundle_hash": receipt["bundle_hash"],
        "capsule_root_hash": receipt["capsule_root_hash"],
        "receipt_hash": receipt["receipt_hash"],
        "canonical_path": receipt["canonical_path"],
        "committed_at": receipt["committed_at"],
    }
    if sidecar.is_file():
        current = json.loads(sidecar.read_text(encoding="utf-8"))
        if current != value:
            raise RuntimeError("legacy delivery sidecar conflict")
        return sidecar
    atomic_write_json(sidecar, value)
    return sidecar


def transactional_finish(handle) -> dict:
    """Run the common publication state machine for a completed session graph."""
    from autoresearch.trace import capsule as capsule_mod
    from autoresearch.trace.publication import execute_publication
    from autoresearch.trace.write_guard import run_write_lock

    bundle, reader = prepare_bundle(handle)
    report_root = ws.run_reports_root(handle.contract.run_kind)
    state_root = ws.context_root() / "_published_state"
    with run_write_lock(handle.run_id):
        receipt = execute_publication(
            handle,
            bundle,
            artifact_reader=reader,
            reports_root=report_root,
            state_root=state_root,
            finalizer=lambda canonical: capsule_mod._finalize_unlocked(
                handle.run_id,
                capsule_mod.BusinessStatus.SUCCEEDED,
                report_dir=canonical,
                profile=session_profile(handle),
            ),
            compatibility=lambda canonical, committed: _compatibility_view(
                handle, canonical, committed
            ),
        )
    return {
        "canonical_path": str(report_root / receipt["canonical_path"]),
        "receipt": receipt,
    }


def session_profile(handle, *, business_status: str = "SUCCEEDED"):
    """Build the legacy-compatible evidence profile from the frozen task plan."""
    plan_path = Path(handle.workspace) / "session/plan.json"
    frozen_plan = json.loads(plan_path.read_text(encoding="utf-8"))
    tasks = list(frozen_plan["tasks"])
    expansion_root = Path(handle.workspace) / "session/expansions"
    if expansion_root.is_dir():
        for path in sorted(expansion_root.glob("*.json")):
            expansion = json.loads(path.read_text(encoding="utf-8"))
            tasks.extend(expansion["tasks"])
    roles = tuple(dict.fromkeys(task["role"] for task in tasks if task["kind"] == "INFERENCE"))
    mapping = {role: role_stage(role) for role in roles}
    from autoresearch.trace.capsule import _last_reliable_checkpoint, resolve_run_mode

    return profile_factory(handle.contract.run_kind)(
        mode=resolve_run_mode(handle),
        business_status=business_status,
        last_stage=_last_reliable_checkpoint(handle.capsule),
        agent_roles=roles,
        role_stages=mapping,
    )


__all__ = [
    "prepare_bundle",
    "publish",
    "register_publisher",
    "session_profile",
    "transactional_finish",
]
