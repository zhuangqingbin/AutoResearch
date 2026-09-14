"""Read-only verification anchored by the exact report path and bytes."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.common.atomic import canonical_json, sha256_bytes, sha256_file
from autoresearch.common.commit_chain import read_publication_chain
from autoresearch.contracts.forensic import (
    validate_execution_origin,
    validate_verification_result,
)
from autoresearch.contracts.publication import (
    validate_publication_bundle,
    validate_publication_receipt,
)
from autoresearch.contracts.session_plan import validate_plan
from autoresearch.contracts.session_task import validate_host_profile
from autoresearch.contracts.stages import RUN_KINDS


@dataclass(frozen=True)
class _Binding:
    root: Path
    report_relative: str
    run_id: str
    publication_id: str
    run_kind: str
    bundle: dict | None
    publication_ok: bool


def _path_hash(path: Path) -> str:
    if path.is_file():
        return sha256_file(path)
    rows = {
        item.relative_to(path).as_posix(): sha256_file(item)
        for item in sorted(path.rglob("*"))
        if item.is_file() and not item.is_symlink()
    }
    return sha256_bytes(canonical_json(rows).encode("utf-8"))


def _within(path: Path, root: Path) -> str | None:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return None


def _committed_receipt(report_base: Path, bundle: dict, root: Path) -> dict | None:
    path = report_base / "_publications" / bundle["run_id"] / f"{bundle['publication_id']}.json"
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
        validate_publication_receipt(receipt)
        chain = read_publication_chain(report_base / "_publications/receipts.jsonl")
        capsule_root = json.loads(
            (root / "capsule/verification/ROOT.json").read_text(encoding="utf-8")
        )["root_hash"]
    except Exception:  # noqa: BLE001 - invalid publication is a false verdict
        return None
    if not (
        receipt in chain
        and receipt["engine"] == bundle["engine"]
        and receipt["run_id"] == bundle["run_id"]
        and receipt["publication_id"] == bundle["publication_id"]
        and receipt["bundle_hash"] == bundle["bundle_hash"]
        and receipt["capsule_root_hash"] == capsule_root
        and (report_base / receipt["canonical_path"]).resolve() == root.resolve()
    ):
        return None
    return receipt


def _valid_receipt(report_base: Path, bundle: dict, root: Path) -> bool:
    return _committed_receipt(report_base, bundle, root) is not None


def _business_match(bundle: dict, relative: str, digest: str, size: int) -> bool:
    return any(
        row["relative_path"] == relative
        and row["sha256"] == digest
        and row["bytes"] == size
        for row in bundle["business_files"]
    )


def _transaction_binding(path: Path) -> _Binding | None:
    for root in (path, *path.parents):
        bundle_path = root / "_publication/publication_bundle.json"
        if not bundle_path.is_file():
            continue
        try:
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
            validate_publication_bundle(bundle)
            report_base = ws.run_reports_root(bundle["run_kind"])
            if bundle["engine"] != ws.ENGINE:
                return None
            if root.resolve() != (
                report_base / "runs" / bundle["run_id"] / bundle["publication_id"]
            ).resolve():
                return None
            relative = _within(path, root)
            if relative is None:
                return None
            if path.is_file() and not _business_match(
                bundle, relative, sha256_file(path), path.stat().st_size
            ):
                return None
            report_relative = relative if relative not in {"", "."} else "publication"
            return _Binding(
                root=root,
                report_relative=report_relative,
                run_id=bundle["run_id"],
                publication_id=bundle["publication_id"],
                run_kind=bundle["run_kind"],
                bundle=bundle,
                publication_ok=_valid_receipt(report_base, bundle, root),
            )
        except Exception:  # noqa: BLE001 - malformed self-identity must not bind
            return None
    return None


def _delivery_sidecar(path: Path) -> Path:
    return (
        path / "delivery.json"
        if path.is_dir()
        else path.with_name(f"{path.name}.delivery.json")
    )


def _delivery_binding(path: Path) -> _Binding | None:
    sidecar = _delivery_sidecar(path)
    if not sidecar.is_file() or not path.is_file():
        return None
    try:
        identity = json.loads(sidecar.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if set(identity) != {
        "schema_version",
        "engine",
        "run_id",
        "publication_id",
        "bundle_hash",
        "capsule_root_hash",
        "receipt_hash",
        "canonical_path",
        "committed_at",
    }:
        return None
    if identity.get("engine") != ws.ENGINE:
        return None
    if identity.get("canonical_path") != (
        f"runs/{identity.get('run_id')}/{identity.get('publication_id')}"
    ):
        return None
    candidates = []
    for kind in RUN_KINDS:
        report_base = ws.run_reports_root(kind)
        canonical = report_base / str(identity.get("canonical_path") or "")
        bundle_path = canonical / "_publication/publication_bundle.json"
        if not bundle_path.is_file():
            continue
        try:
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
            validate_publication_bundle(bundle)
        except Exception:  # noqa: BLE001
            continue
        if any(
            identity.get(field) != bundle[field]
            for field in ("engine", "run_id", "publication_id", "bundle_hash")
        ):
            continue
        receipt = _committed_receipt(report_base, bundle, canonical)
        if receipt is None or any(
            identity.get(field) != receipt[field]
            for field in (
                "schema_version",
                "engine",
                "run_id",
                "publication_id",
                "bundle_hash",
                "capsule_root_hash",
                "receipt_hash",
                "canonical_path",
                "committed_at",
            )
        ):
            continue
        digest = sha256_file(path)
        matches = [
            row
            for row in bundle["business_files"]
            if row["sha256"] == digest and row["bytes"] == path.stat().st_size
        ]
        if len(matches) == 1:
            candidates.append((kind, report_base, canonical, bundle, matches[0]))
    if len(candidates) != 1:
        return None
    kind, report_base, canonical, bundle, row = candidates[0]
    return _Binding(
        root=canonical,
        report_relative=row["relative_path"],
        run_id=bundle["run_id"],
        publication_id=bundle["publication_id"],
        run_kind=kind,
        bundle=bundle,
        publication_ok=True,
    )


def _legacy_binding(path: Path) -> _Binding | None:
    from autoresearch.trace.capsule import read_manifest, read_valid_ledger

    candidates: dict[tuple[str, str, str, str], tuple[str, dict, Path, str]] = {}
    for kind in RUN_KINDS:
        for row in read_valid_ledger(kind=kind):
            root = Path(str(row.get("final_path") or ""))
            try:
                relative = path.resolve().relative_to(root.resolve()).as_posix()
            except (OSError, ValueError):
                continue
            manifest = read_manifest(root)
            if path.is_file() and manifest.get(relative) != sha256_file(path):
                continue
            key = (kind, str(row["run_id"]), str(root.resolve()), relative)
            candidates.setdefault(
                key,
                (kind, row, root, relative or "publication"),
            )
    if len(candidates) != 1:
        return None
    kind, row, root, relative = next(iter(candidates.values()))
    return _Binding(
        root=root,
        report_relative=relative,
        run_id=row["run_id"],
        publication_id=f"legacy-r{row['revision']}",
        run_kind=kind,
        bundle=None,
        publication_ok=True,
    )


def _find_binding(path: Path) -> _Binding | None:
    return (
        _transaction_binding(path)
        or _delivery_binding(path)
        or _legacy_binding(path)
    )


def _origin(binding: _Binding) -> tuple[str, bool, list[str]]:
    path = binding.root / "capsule/identity/execution_origin.json"
    try:
        value = validate_execution_origin(json.loads(path.read_text(encoding="utf-8")))
    except Exception as exc:  # noqa: BLE001
        return "UNKNOWN", False, [f"EXECUTION_ORIGIN_INVALID:{type(exc).__name__}"]
    missing = []
    if (
        value["engine"] != ws.ENGINE
        or value["run_id"] != binding.run_id
        or value["run_kind"] != binding.run_kind
    ):
        missing.append("EXECUTION_ORIGIN_IDENTITY_MISMATCH")
    if binding.bundle is not None:
        digest = sha256_bytes(canonical_json(value).encode("utf-8"))
        if digest != binding.bundle["origin_hash"]:
            missing.append("EXECUTION_ORIGIN_BUNDLE_HASH_MISMATCH")
    if value["orchestration"] == "session_v1":
        try:
            session_root = binding.root / "capsule/identity/session"
            plan = validate_plan(
                json.loads((session_root / "plan.json").read_text(encoding="utf-8"))
            )
            profile = validate_host_profile(
                json.loads(
                    (session_root / "host_profile.json").read_text(encoding="utf-8")
                )
            )
        except Exception as exc:  # noqa: BLE001
            missing.append(f"SESSION_ORIGIN_REFERENCE_INVALID:{type(exc).__name__}")
        else:
            host_hash = sha256_bytes(canonical_json(profile).encode("utf-8"))
            if value["entrypoint"] != "autoresearch.session_agent.begin":
                missing.append("SESSION_ENTRYPOINT_MISMATCH")
            if (
                plan["engine"] != ws.ENGINE
                or plan["run_id"] != binding.run_id
                or plan["run_kind"] != binding.run_kind
            ):
                missing.append("SESSION_PLAN_IDENTITY_MISMATCH")
            if value["plan_hash"] != plan["plan_hash"]:
                missing.append("SESSION_PLAN_HASH_MISMATCH")
            if binding.bundle is not None and binding.bundle["plan_hash"] != plan["plan_hash"]:
                missing.append("PUBLICATION_PLAN_HASH_MISMATCH")
            if value["host_profile_hash"] != host_hash:
                missing.append("SESSION_HOST_PROFILE_HASH_MISMATCH")
            if plan["host_profile_hash"] != host_hash:
                missing.append("PLAN_HOST_PROFILE_HASH_MISMATCH")
            if profile["engine"] != ws.ENGINE:
                missing.append("SESSION_HOST_ENGINE_MISMATCH")
            if any(
                profile[field] is True
                for field in (
                    "deterministic_exec",
                    "capture_binding",
                    "inference_handoff",
                    "safe_resume",
                    "independent_context",
                    "native_dispatch",
                    "web_search",
                    "web_fetch",
                )
            ) and not profile["evidence_refs"]:
                missing.append("SESSION_HOST_EVIDENCE_REFS_MISSING")
    return value["orchestration"], not missing, missing


def _unbound(path: Path, digest: str, reasons: list[str]) -> dict:
    value = {
        "schema_version": 1,
        "engine": ws.ENGINE,
        "run_id": None,
        "report_path": path.name or "report",
        "report_sha256": digest,
        "publication_id": None,
        "orchestration": "UNKNOWN",
        "orchestration_verified": False,
        "report_covered": False,
        "integrity_ok": False,
        "publication_ok": False,
        "completeness_ok": False,
        "compute_status": "NONE",
        "model_status": "UNKNOWN",
        "scope": ["report_identity"],
        "missing": sorted({"UNBOUND_REPORT", *reasons}),
        "diffs": [],
    }
    return validate_verification_result(value)


def verify_report(
    path: Path | str,
    expected_run_id: str | None = None,
    level: str = "full",
) -> dict:
    """Verify the exact report bytes without writing or borrowing a run identity."""
    if level not in {"integrity", "full"}:
        raise ValueError("verification level must be integrity or full")
    report = Path(path)
    if not report.exists() or report.is_symlink():
        raise FileNotFoundError(report)
    digest = _path_hash(report)
    binding = _find_binding(report)
    if binding is None:
        return _unbound(report, digest, [])
    if expected_run_id is not None and binding.run_id != expected_run_id:
        return _unbound(report, digest, ["EXPECTED_RUN_ID_MISMATCH"])

    from autoresearch.trace import capsule as capsule_mod

    integrity = capsule_mod.verify(
        binding.run_id,
        final_path=binding.root,
        kind=binding.run_kind,
    )
    orchestration, orchestration_ok, origin_missing = _origin(binding)
    missing = list(origin_missing)
    diffs = []
    completeness_ok = False
    compute_status = "NONE"
    scope = ["report_identity", "integrity"]
    if level == "full":
        scope.append("evidence_closure")
        try:
            from autoresearch.trace.evidence_closure import (
                evaluate_task_evidence_closure,
            )

            recomputed = evaluate_task_evidence_closure(binding.root / "capsule")
            completeness_ok = bool(recomputed["completeness_ok"])
            missing.extend(recomputed["missing"])
            compute_status = "FULL"
            stored_path = binding.root / "capsule/verification/evidence_closure.json"
            if stored_path.is_file():
                stored = json.loads(stored_path.read_text(encoding="utf-8"))
                if stored != recomputed:
                    diffs.append("STORED_EVIDENCE_CLOSURE_DIFFERS")
            stored_completeness = binding.root / "capsule/verification/completeness.json"
            if stored_completeness.is_file():
                stored = json.loads(stored_completeness.read_text(encoding="utf-8"))
                if bool(stored.get("completeness_ok")) != completeness_ok:
                    diffs.append("STORED_COMPLETENESS_DIFFERS")
        except Exception as exc:  # noqa: BLE001 - explicit partial recomputation
            compute_status = "PARTIAL"
            missing.append(f"EVIDENCE_CLOSURE_UNAVAILABLE:{type(exc).__name__}")
    if not integrity.get("integrity_ok"):
        missing.append("CAPSULE_INTEGRITY_FAILED")
    if not binding.publication_ok or not integrity.get("root_ledger_ok"):
        missing.append("PUBLICATION_RECEIPT_FAILED")
    value = {
        "schema_version": 1,
        "engine": ws.ENGINE,
        "run_id": binding.run_id,
        "report_path": binding.report_relative,
        "report_sha256": digest,
        "publication_id": binding.publication_id,
        "orchestration": orchestration,
        "orchestration_verified": orchestration_ok,
        "report_covered": True,
        "integrity_ok": bool(integrity.get("integrity_ok")),
        "publication_ok": bool(
            binding.publication_ok and integrity.get("root_ledger_ok")
        ),
        "completeness_ok": completeness_ok,
        "compute_status": compute_status,
        "model_status": "EVIDENCE_ONLY",
        "scope": scope,
        "missing": sorted(set(missing)),
        "diffs": sorted(set(diffs)),
    }
    return validate_verification_result(value)


__all__ = ["verify_report"]
