"""Root-only collector of accepted production artifacts for retrospective research."""

from __future__ import annotations

import json
from pathlib import Path

from autoresearch.common.atomic import canonical_json, sha256_bytes, sha256_file
from autoresearch.scan.research_provenance import (
    FILENAME,
    freeze_bytes,
    freeze_research_provenance,
    safe_path,
)
from autoresearch.session_agent import artifacts, host_evidence


def freeze_read_proof(handle, task, attempt, artifact_id, target):
    """Capture the original binding plus complete transcript/result bytes, not a PASS flag."""
    from autoresearch.session_agent import task_access as access
    from autoresearch.session_agent.evidence_bundle import require_read_evidence
    from autoresearch.trace.read_observation import verify_read_bundle

    require_read_evidence(handle, task, {"envelope": {"attempt": attempt}}, artifact_id)
    binding = host_evidence._existing_binding(handle, task["task_id"], attempt)
    binding = host_evidence._load_binding_ref(handle, "host-binding:" + binding["binding_id"])
    target = safe_path(target)
    target.mkdir(parents=True, exist_ok=True)

    def freeze(name, data):
        path = target / name
        freeze_bytes(path, data, allow_existing=True)
        return {"path": name, "sha256": sha256_bytes(data)}

    with artifacts.open_artifact(handle, artifact_id) as stream:
        data = stream.read()
    value = {
        "schema_version": 1,
        "identity": {
            key: binding[key]
            for key in ("engine", "run_id", "task_id", "attempt", "role", "subject")
        },
        "artifact_id": artifact_id,
        "artifact_path": str(artifacts.artifact_path(handle, artifact_id).resolve()),
        "binding": binding,
        "artifact": freeze("artifact.bin", data),
        "raw": freeze(
            "raw.bin", safe_path(Path(handle.capsule) / binding["raw_path"]).read_bytes()
        ),
        "normalized": freeze(
            "normalized.json",
            safe_path(Path(handle.capsule) / binding["normalized_path"]).read_bytes(),
        ),
        "dispatch_manifest": None,
        "dispatch_request": None,
    }
    request = Path(handle.workspace) / "session/dispatch" / f"{task['task_id']}-a{attempt}.json"
    try:
        access._load_manifest(request)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    else:
        value["dispatch_request"] = freeze("dispatch.json", request.read_bytes())
        value["dispatch_manifest"] = freeze(
            "dispatch_manifest.json", access.manifest_path(request).read_bytes()
        )
    verify_read_bundle(value, read_bytes=lambda relative: (target / relative).read_bytes())
    content = (canonical_json(value) + "\n").encode()
    freeze("proof.json", content)
    return {"path": str(target / "proof.json"), "sha256": sha256_bytes(content)}


def _accepted_json(handle, artifact_id):
    with artifacts.open_artifact(handle, artifact_id) as stream:
        return json.loads(stream.read())


def _capture_artifact(handle, artifact_id):
    try:
        with artifacts.open_artifact(handle, artifact_id) as stream:
            data = stream.read()
        return {
            "artifact_id": artifact_id,
            "path": str(artifacts.artifact_path(handle, artifact_id)),
            "sha256": sha256_bytes(data),
        }
    except KeyError:
        return None
    except artifacts.ArtifactConflict as exc:
        if "no accepted generation" in str(exc) or "has not been bound" in str(exc):
            return None
        raise


def _collection_binding(handle, owner, request):
    rows = []
    sources = {}
    for task_id, entry in sorted(owner["tasks"].items()):
        task = entry["spec"]
        if task.get("role") != "scan.l4.card":
            continue
        rows.append(
            {
                key: entry.get(key)
                for key in ("spec", "state", "attempt", "submission_hash", "accepted_artifacts")
            }
        )
        for artifact_id in task.get("input_artifact_ids", []) + task.get("output_artifact_ids", []):
            sources[artifact_id] = _capture_artifact(handle, artifact_id)
        frozen = Path(handle.workspace) / "session/dispatch" / f"{task_id}-a{entry['attempt']}.json"
        for path in (frozen, frozen.with_suffix(".access.json")):
            sources[str(path)] = sha256_file(path) if path.is_file() else None
        binding = host_evidence._existing_binding(handle, task_id, entry["attempt"])
        if binding is not None:
            binding = host_evidence._load_binding_ref(
                handle, "host-binding:" + binding["binding_id"]
            )
        sources["host-binding:" + task_id] = binding
    stage = Path(handle.staging)
    paths = [stage / "_relative_buy_decision.json", stage / "_dossier_present.json"]
    paths += sorted((stage / "_l4_force_full").glob("*.json"))
    sources.update(
        {str(path): sha256_file(safe_path(path)) if path.is_file() else None for path in paths}
    )
    return sha256_bytes(
        canonical_json(
            {
                "run_id": handle.run_id,
                "engine": handle.engine,
                "contract_hash": handle.contract.contract_hash,
                "profile": request.get("card_research_profile"),
                "tasks": rows,
                "sources": sources,
            }
        ).encode()
    )


def _existing(handle, target, binding):
    from autoresearch.scan.research_provenance import validate_provenance
    from autoresearch.trace.read_observation import verify_read_bundle

    value = validate_provenance(json.loads(target.read_text()))
    if (
        value.get("collection_binding") != binding
        or value["run_id"] != handle.run_id
        or value["contract_hash"] != handle.contract.contract_hash
    ):
        raise ValueError("research collector accepted inputs or identity changed")
    for row in value["base_inputs"]:
        if sha256_file(safe_path(target.parent / row["path"])) != row["sha256"]:
            raise ValueError("research input archive changed")
    for row in value["candidates"].values():
        proof = row.get("deep_read_proof")
        if proof is None:
            continue
        path = safe_path(target.parent / proof["path"])
        if sha256_file(path) != proof["sha256"]:
            raise ValueError("research read proof changed")
        verify_read_bundle(
            json.loads(path.read_text()),
            read_bytes=lambda name, path=path: safe_path(path.parent / name).read_bytes(),
        )
    return target


def collect_and_freeze(handle):
    """Collect after publisher from this run's accepted owner state; root mirrors the result."""
    stage = safe_path(handle.staging)
    target = stage / FILENAME
    owner = json.loads((Path(handle.workspace) / "session/tasks.json").read_text())
    if owner["run_id"] != handle.run_id or owner["engine"] != handle.engine:
        raise ValueError("research collector task owner identity mismatch")
    request = json.loads((Path(handle.workspace) / "session/request.json").read_text())
    collection_binding = _collection_binding(handle, owner, request)
    if target.exists():
        return _existing(handle, target, collection_binding)
    profile = request.get("card_research_profile")
    cards = {}
    initial = {}
    for entry in owner["tasks"].values():
        task = entry["spec"]
        if task.get("role") != "scan.l4.card":
            continue
        code = str(task["subject"]).split(".")[0]
        key = (task.get("parent_task") or {}).get("attempt", 1), entry["attempt"]
        if task.get("expected_output_contract") == "research.card.initial.v1":
            if entry["state"] in {"SUCCEEDED", "SUPERSEDED"} and (
                code not in initial or key > initial[code][0]
            ):
                initial[code] = (key, entry)
        elif code not in cards or key > cards[code][0]:
            cards[code] = (key, entry)
    e6path = stage / "_relative_buy_decision.json"
    e6 = json.loads(e6path.read_text()) if e6path.is_file() else {}
    if e6 and e6.get("date") != handle.analysis_date:
        raise ValueError("collector E6 analysis date mismatch")
    e6rows = {row["code"]: row for row in e6.get("candidates", [])}
    dossier_path = stage / "_dossier_present.json"
    dossiers = json.loads(dossier_path.read_text()) if dossier_path.is_file() else None
    if dossiers is not None and not isinstance(dossiers, list):
        raise ValueError("invalid dispatch dossier snapshot")
    rows, base = {}, {}
    for code, (_, entry) in sorted(cards.items()):
        task = entry["spec"]
        row = {}
        if dossiers is not None:
            row["dossier_status"] = (
                "PRESENT_AT_DISPATCH" if code in dossiers else "ABSENT_AT_DISPATCH"
            )
        force = stage / "_l4_force_full" / f"{code}.json"
        if force.is_file():
            observed = json.loads(force.read_text())
            if observed["code"] != code or observed["rule_hash"] != sha256_bytes(
                canonical_json(observed["rule"]).encode()
            ):
                raise ValueError("force_full dispatch observation binding changed")
            row.update(
                force_full=observed["force_full"],
                force_full_reason=observed["reason"],
                force_full_rule_hash=observed["rule_hash"],
                **observed["priors"],
            )
        inputs = task.get("input_artifact_ids", [])
        from autoresearch.session_agent import task_access as access

        dispatch = (
            Path(handle.workspace)
            / "session/dispatch"
            / f"{task['task_id']}-a{entry['attempt']}.json"
        )
        declared = {}
        if dispatch.is_file():
            manifest = access._load_manifest(dispatch)
            declared = {
                r["artifact_id"]: r for r in manifest["reads"] + manifest["conditional_reads"]
            }
        row["deep_declared"] = any(key.endswith(".deep") for key in inputs)
        # Only shared raw fact inputs define the B3 comparison population. Initial,
        # final, and intel opinions are excluded from this common-base hash.
        for artifact_id in inputs:
            suffix = artifact_id.rsplit(".", 1)[-1]
            if suffix not in {"slim", "deep"} and artifact_id != "research.frame":
                continue
            captured = _capture_artifact(handle, artifact_id)
            if captured is None or artifact_id not in declared:
                continue
            if captured["sha256"] != declared[artifact_id]["sha256"]:
                raise ValueError("original dispatched input hash changed")
            key = "research.frame" if artifact_id == "research.frame" else f"card.{code}.{suffix}"
            base[key] = {**captured, "artifact_id": key}
        if code in initial:
            prior = initial[code][1]["spec"]
            ids = [key for key in prior["output_artifact_ids"] if key.endswith(".initial")]
            if len(ids) != 1:
                raise ValueError("accepted initial identity missing")
            value = _accepted_json(handle, ids[0])
            if value["subject"].split(".")[0] != code:
                raise ValueError("initial candidate identity mismatch")
            row["initial_rating"] = value["initial_rating"]
            row["initial_faces"] = value.get("initial_faces")
            row["initial_raw_metrics"] = {
                key: value[key] for key in ("initial_dimensions", "initial_gates") if key in value
            }
        if entry["state"] == "SUCCEEDED":
            for artifact_id in task["output_artifact_ids"]:
                if not artifact_id.endswith(".card"):
                    continue
                with artifacts.open_artifact(handle, artifact_id) as stream:
                    text = stream.read().decode("utf-8")
                from autoresearch.scan.decision_finalize import parse_early_stop
                from autoresearch.scan.l4.card_io import _dimensions, _gates

                dimensions = _dimensions(text)
                stop = parse_early_stop(text)
                row.update(
                    card_kind="EARLY_STOP" if stop else "FULL",
                    early_stop_phase=stop.get("phase") if stop else None,
                    quality_status=dimensions.get("盈利质量"),
                    solvency_status=dimensions.get("偿付"),
                    final_raw_metrics={"card_dimensions": dimensions, "card_gates": _gates(text)},
                )
            for artifact_id in inputs:
                if not artifact_id.endswith(".deep"):
                    continue
                try:
                    proof = freeze_read_proof(
                        handle, task, entry["attempt"], artifact_id, stage / "research_reads" / code
                    )
                except (OSError, ValueError, KeyError, RuntimeError):
                    # Unavailable is not false: no transcript means we cannot observe the read.
                    row["deep_read_proof"] = None
                else:
                    proof["path"] = str(Path(proof["path"]).relative_to(stage))
                    row["deep_read_proof"] = proof
        for artifact_id in inputs:
            if artifact_id.endswith(".intel_status"):
                status = _accepted_json(handle, artifact_id)
                row["intel_status"] = status.get("status")
        candidate = e6rows.get(code, {})
        row["final_faces"] = candidate.get("faces")
        if candidate:
            metrics = row.setdefault("final_raw_metrics", {})
            metrics.update(
                {
                    "e6_faces_missing": candidate.get("faces_missing"),
                    "e6_sort_values": (
                        e6.get("selection", {}).get("sort_keys", {}).get("values", {})
                    ).get(code),
                    "e6_raw_face_metrics": candidate.get("raw_face_metrics"),
                }
            )
        rows[code] = row
    return freeze_research_provenance(
        target,
        run_id=handle.run_id,
        analysis_date=handle.analysis_date,
        contract_hash=handle.contract.contract_hash,
        profile=profile,
        candidates=rows,
        base_inputs=list(base.values()),
        collection_binding=collection_binding,
    )
