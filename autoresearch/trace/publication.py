"""Recoverable seal → promote → state-view → commit publication transaction."""

from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from autoresearch.common.atomic import (
    atomic_write_bytes,
    atomic_write_json,
    canonical_json,
    sha256_bytes,
)
from autoresearch.common.commit_chain import append_publication_receipt, read_publication_chain
from autoresearch.common.published_state import stage_state_mutation, target_locks
from autoresearch.contracts.publication import (
    validate_publication_bundle,
    validate_publication_receipt,
)

JOURNAL_RELATIVE = Path("publication/journal.json")
PUBLICATION_BUNDLE_NAME = "publication_bundle.json"
SEALED_MANIFEST_NAME = "sealed_manifest.json"
PHASES = (
    "PREPARING",
    "EVIDENCE_CLOSED",
    "BUNDLE_SEALED",
    "PROMOTED",
    "VIEWS_APPLIED",
    "COMMITTED",
)


class InjectedPublicationFault(RuntimeError):
    """Test-only deterministic crash after a persisted publication phase."""


def _phase_index(phase: str) -> int:
    try:
        return PHASES.index(phase)
    except ValueError as exc:
        raise ValueError(f"invalid publication phase: {phase}") from exc


def _journal_path(workspace: Path | str) -> Path:
    return Path(workspace) / JOURNAL_RELATIVE


def load_publication_journal(workspace: Path | str) -> dict[str, Any]:
    path = _journal_path(workspace)
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or value.get("state") not in PHASES
        or type(value.get("revision")) is not int
        or value["revision"] < 1
    ):
        raise ValueError("invalid publication journal")
    recorded_hash = value.get("journal_hash")
    body = {key: item for key, item in value.items() if key != "journal_hash"}
    if recorded_hash != sha256_bytes(canonical_json(body).encode("utf-8")):
        raise ValueError("publication journal hash mismatch")
    return value


def _write_journal(workspace: Path, value: dict[str, Any]) -> dict[str, Any]:
    value["revision"] = int(value.get("revision") or 0) + 1
    body = {key: item for key, item in value.items() if key != "journal_hash"}
    value["journal_hash"] = sha256_bytes(canonical_json(body).encode("utf-8"))
    atomic_write_json(_journal_path(workspace), value)
    return value


def _advance(
    workspace: Path,
    journal: dict[str, Any],
    phase: str,
    *,
    fault_after: str | None,
    **updates: Any,
) -> None:
    if _phase_index(phase) < _phase_index(str(journal["state"])):
        raise RuntimeError("publication journal cannot move backward")
    journal.update(updates)
    journal["state"] = phase
    _write_journal(workspace, journal)
    if fault_after == phase:
        raise InjectedPublicationFault(f"injected crash after {phase}")


def _safe_relative(relative: str) -> PurePosixPath:
    logical = PurePosixPath(relative)
    if (
        logical.is_absolute()
        or not logical.parts
        or logical.as_posix() != relative
        or any(part in {"", ".", ".."} for part in logical.parts)
    ):
        raise ValueError(f"unsafe publication relative path: {relative}")
    return logical


def _tree_manifest(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256_bytes(path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and not path.is_symlink()
        and path.name != SEALED_MANIFEST_NAME
        and path.relative_to(root).parts[0] != "capsule"
    }


def _seal_directory(
    handle,
    bundle: dict[str, Any],
    artifact_reader: Callable[[str], bytes],
) -> Path:
    workspace = Path(handle.workspace)
    sealed = workspace / "publication/sealed" / bundle["publication_id"]
    if sealed.is_dir():
        _verify_sealed(sealed, bundle)
        return sealed
    sealed.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".sealing-", dir=sealed.parent))
    cache: dict[str, bytes] = {}

    def payload(artifact_id: str) -> bytes:
        if artifact_id not in cache:
            value = artifact_reader(artifact_id)
            if not isinstance(value, bytes):
                raise TypeError("artifact_reader must return bytes")
            cache[artifact_id] = value
        return cache[artifact_id]

    try:
        for row in bundle["business_files"]:
            content = payload(row["artifact_id"])
            if len(content) != row["bytes"] or sha256_bytes(content) != row["sha256"]:
                raise RuntimeError(f"publication artifact changed: {row['artifact_id']}")
            destination = temporary.joinpath(*_safe_relative(row["relative_path"]).parts)
            atomic_write_bytes(destination, content)
        state_index = {}
        for mutation in bundle["state_mutations"]:
            content = payload(mutation["after_artifact_id"])
            if sha256_bytes(content) != mutation["after_hash"]:
                raise RuntimeError(
                    f"publication state artifact changed: {mutation['after_artifact_id']}"
                )
            relative = Path("_publication/state") / f"{mutation['after_hash']}.bin"
            atomic_write_bytes(temporary / relative, content)
            state_index[mutation["after_artifact_id"]] = relative.as_posix()
        atomic_write_json(temporary / "_publication/state_index.json", state_index)
        atomic_write_json(temporary / f"_publication/{PUBLICATION_BUNDLE_NAME}", bundle)
        manifest = _tree_manifest(temporary)
        atomic_write_json(
            temporary / f"_publication/{SEALED_MANIFEST_NAME}",
            {"schema_version": 1, "files": manifest},
        )
        os.replace(temporary, sealed)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    _verify_sealed(sealed, bundle)
    return sealed


def _verify_sealed(root: Path, bundle: dict[str, Any]) -> None:
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError("sealed publication is not a real directory")
    manifest_path = root / f"_publication/{SEALED_MANIFEST_NAME}"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest != {"schema_version": 1, "files": _tree_manifest(root)}:
        raise RuntimeError("sealed publication manifest mismatch")
    captured_bundle = json.loads(
        (root / f"_publication/{PUBLICATION_BUNDLE_NAME}").read_text(encoding="utf-8")
    )
    if captured_bundle != bundle:
        raise RuntimeError("sealed publication bundle conflict")


def _promote_directory(sealed: Path, canonical: Path, bundle: dict[str, Any]) -> Path:
    canonical.parent.mkdir(parents=True, exist_ok=True)
    if canonical.is_dir():
        _verify_sealed(canonical, bundle)
        return canonical
    if canonical.exists() or canonical.is_symlink():
        raise RuntimeError("canonical publication path conflict")
    temporary = Path(tempfile.mkdtemp(prefix=f".{canonical.name}.promote-", dir=canonical.parent))
    shutil.rmtree(temporary)
    try:
        shutil.copytree(sealed, temporary, symlinks=False)
        _verify_sealed(temporary, bundle)
        os.replace(temporary, canonical)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return canonical


def _state_payload(canonical: Path, artifact_id: str) -> bytes:
    index = json.loads((canonical / "_publication/state_index.json").read_text(encoding="utf-8"))
    relative = index.get(artifact_id)
    if not isinstance(relative, str):
        raise RuntimeError(f"sealed state artifact is missing: {artifact_id}")
    path = canonical.joinpath(*_safe_relative(relative).parts)
    resolved = path.resolve(strict=True)
    resolved.relative_to(canonical.resolve(strict=True))
    if stat.S_ISLNK(path.lstat().st_mode) or not stat.S_ISREG(path.lstat().st_mode):
        raise RuntimeError("sealed state artifact must be regular")
    return path.read_bytes()


def _root_hash(result: object, canonical: Path) -> str:
    if isinstance(result, dict):
        value = result.get("root_hash")
    else:
        value = getattr(result, "root_hash", None)
    if isinstance(value, str) and len(value) == 64:
        return value
    root = json.loads((canonical / "capsule/verification/ROOT.json").read_text(encoding="utf-8"))
    value = root.get("root_hash")
    if not isinstance(value, str) or len(value) != 64:
        raise RuntimeError("finalizer did not produce a capsule root hash")
    return value


def _receipt_path(reports_root: Path, bundle: dict[str, Any]) -> Path:
    return reports_root / "_publications" / bundle["run_id"] / f"{bundle['publication_id']}.json"


def _load_receipt(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(value, dict):
        raise ValueError("publication receipt must be an object")
    validate_publication_receipt(value)
    return value


def execute_publication(
    handle,
    bundle: dict[str, Any],
    *,
    artifact_reader: Callable[[str], bytes],
    reports_root: Path | str,
    state_root: Path | str,
    finalizer: Callable[[Path], object],
    compatibility: Callable[[Path, dict[str, Any]], None] | None = None,
    now: datetime | None = None,
    fault_after: str | None = None,
) -> dict[str, Any]:
    """Advance one publication journal; retry resumes strictly after its last phase."""
    validate_publication_bundle(bundle)
    if (
        bundle["run_id"] != handle.run_id
        or bundle["engine"] != handle.engine
        or bundle["run_kind"] != handle.contract.run_kind
    ):
        raise RuntimeError("publication bundle does not belong to this run")
    workspace = Path(handle.workspace)
    report_base = Path(reports_root)
    state_base = Path(state_root)
    receipt_path = _receipt_path(report_base, bundle)
    committed = _load_receipt(receipt_path)
    if committed is not None:
        if committed not in read_publication_chain(report_base / "_publications/receipts.jsonl"):
            raise RuntimeError("publication receipt is missing from commit chain")
        journal = load_publication_journal(workspace)
        expected_identity = (
            bundle["engine"],
            bundle["run_id"],
            bundle["publication_id"],
            bundle["bundle_hash"],
        )
        if (
            journal["bundle_hash"] != bundle["bundle_hash"]
            or (
                committed["engine"],
                committed["run_id"],
                committed["publication_id"],
                committed["bundle_hash"],
            )
            != expected_identity
        ):
            raise RuntimeError("publication bundle conflict")
        canonical = report_base / committed["canonical_path"]
        _verify_sealed(canonical, bundle)
        if _root_hash({}, canonical) != committed["capsule_root_hash"]:
            raise RuntimeError("committed capsule root hash mismatch")
        if journal["state"] != "COMMITTED":
            if (
                journal["state"] != "VIEWS_APPLIED"
                or journal.get("state_effects") != committed["state_effects"]
            ):
                raise RuntimeError("receipt conflicts with incomplete publication journal")
            _advance(
                workspace,
                journal,
                "COMMITTED",
                fault_after=None,
                capsule_root_hash=committed["capsule_root_hash"],
                receipt_hash=committed["receipt_hash"],
            )
        if compatibility is not None and journal.get("compatibility_status") != "APPLIED":
            compatibility(canonical, committed)
            journal["compatibility_status"] = "APPLIED"
            _write_journal(workspace, journal)
        return {**committed, "state": "COMMITTED"}
    try:
        journal = load_publication_journal(workspace)
    except FileNotFoundError:
        journal = {
            "schema_version": 1,
            "engine": handle.engine,
            "run_id": handle.run_id,
            "publication_id": bundle["publication_id"],
            "bundle_hash": bundle["bundle_hash"],
            "state": "PREPARING",
            "canonical_path": None,
            "state_effects": [],
            "capsule_root_hash": None,
            "commit_intent": None,
            "compatibility_status": "PENDING",
            "revision": 0,
            "journal_hash": "0" * 64,
        }
        _write_journal(workspace, journal)
    if journal["bundle_hash"] != bundle["bundle_hash"]:
        raise RuntimeError("publication bundle conflict")
    if fault_after is not None and fault_after not in PHASES:
        raise ValueError("fault_after is not a publication phase")
    if _phase_index(journal["state"]) < _phase_index("EVIDENCE_CLOSED"):
        _advance(
            workspace,
            journal,
            "EVIDENCE_CLOSED",
            fault_after=fault_after,
        )
    sealed = workspace / "publication/sealed" / bundle["publication_id"]
    if _phase_index(journal["state"]) < _phase_index("BUNDLE_SEALED"):
        sealed = _seal_directory(handle, bundle, artifact_reader)
        _advance(
            workspace,
            journal,
            "BUNDLE_SEALED",
            fault_after=fault_after,
        )
    else:
        _verify_sealed(sealed, bundle)
    canonical_relative = Path("runs") / bundle["run_id"] / bundle["publication_id"]
    canonical = report_base / canonical_relative
    if _phase_index(journal["state"]) < _phase_index("PROMOTED"):
        _promote_directory(sealed, canonical, bundle)
        _advance(
            workspace,
            journal,
            "PROMOTED",
            fault_after=fault_after,
            canonical_path=canonical_relative.as_posix(),
        )
    else:
        _verify_sealed(canonical, bundle)
    target_keys = [item["target_key"] for item in bundle["state_mutations"]]
    with target_locks(state_base, target_keys):
        if _phase_index(journal["state"]) < _phase_index("VIEWS_APPLIED"):
            publication_identity = {
                "engine": bundle["engine"],
                "run_id": bundle["run_id"],
                "publication_id": bundle["publication_id"],
                "bundle_hash": bundle["bundle_hash"],
                "receipt_scope": report_base.name,
            }
            effects = [
                stage_state_mutation(
                    mutation,
                    _state_payload(canonical, mutation["after_artifact_id"]),
                    publication=publication_identity,
                    state_root=state_base,
                    reports_root=report_base,
                )
                for mutation in bundle["state_mutations"]
            ]
            _advance(
                workspace,
                journal,
                "VIEWS_APPLIED",
                fault_after=fault_after,
                state_effects=effects,
            )
        commit_intent = journal.get("commit_intent")
        if commit_intent is None:
            finalization = finalizer(canonical)
            capsule_root_hash = _root_hash(finalization, canonical)
            timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
            commit_intent = {
                "capsule_root_hash": capsule_root_hash,
                "committed_at": timestamp.isoformat().replace("+00:00", "Z"),
            }
            journal["commit_intent"] = commit_intent
            journal["capsule_root_hash"] = capsule_root_hash
            _write_journal(workspace, journal)
        else:
            capsule_root_hash = _root_hash({}, canonical)
            if capsule_root_hash != commit_intent.get("capsule_root_hash"):
                raise RuntimeError("finalized capsule changed after commit intent")
        receipt_stub = {
            "schema_version": 1,
            "engine": bundle["engine"],
            "run_id": bundle["run_id"],
            "publication_id": bundle["publication_id"],
            "bundle_hash": bundle["bundle_hash"],
            "capsule_root_hash": capsule_root_hash,
            "canonical_path": canonical_relative.as_posix(),
            "committed_at": commit_intent["committed_at"],
            "state_effects": journal["state_effects"],
            "previous_receipt_hash": None,
            "receipt_hash": "0" * 64,
        }
        receipt = append_publication_receipt(
            report_base / "_publications/receipts.jsonl",
            receipt_stub,
        )
        atomic_write_json(receipt_path, receipt)
        _advance(
            workspace,
            journal,
            "COMMITTED",
            fault_after=fault_after,
            capsule_root_hash=capsule_root_hash,
            receipt_hash=receipt["receipt_hash"],
        )
    if compatibility is not None:
        compatibility(canonical, receipt)
        journal["compatibility_status"] = "APPLIED"
        _write_journal(workspace, journal)
    return {**receipt, "state": "COMMITTED"}


__all__ = [
    "InjectedPublicationFault",
    "execute_publication",
    "load_publication_journal",
]
