"""Frozen FULL evidence index. References never grant undeclared file access."""
from __future__ import annotations

import json
import re
from pathlib import Path

from autoresearch.common.atomic import sha256_bytes
from autoresearch.session_agent import artifacts


def build_bundle(handle, input_ids: list[str]) -> dict:
    if len(input_ids) != len(set(input_ids)) or not input_ids:
        raise ValueError("evidence bundle requires unique declared inputs")
    sources, source_index, gaps = [], [], []
    # declared_path() is resolved; production handles are cwd-relative (context_<engine>/…).
    staging = Path(handle.staging).resolve()
    for artifact_id in sorted(input_ids):
        with artifacts.open_artifact(handle, artifact_id) as stream:
            data = stream.read()
        path = artifacts.declared_path(handle, artifact_id)
        sources.append({"artifact_id": artifact_id,
                        "relative_path": path.relative_to(staging).as_posix(),
                        "sha256": sha256_bytes(data), "size_bytes": len(data)})
        # Retain exact source/gap lines, including their originating section; do
        # not turn analyst prose into machine-asserted facts or invent sources.
        for number, line in enumerate(data.decode("utf-8").splitlines(), 1):
            record = {"artifact_id": artifact_id, "line": number, "text": line}
            if re.search(r'https?://|来源[:：]|source[:：]', line, re.I):
                source_index.append(record)
            if re.search(r'缺口|缺失|未核|unverified|missing|unavailable', line, re.I):
                gaps.append(record)
    return {"schema_version": 1, "run_id": handle.run_id,
            "analysis_date": handle.analysis_date, "usage": "standalone", "depth": "FULL",
            "sources": sources, "source_index": source_index, "data_gaps": gaps}


def validate_bundle(handle, declared_input_ids: list[str]) -> dict:
    with artifacts.open_artifact(handle, "stock.evidence_bundle") as stream:
        bundle = json.loads(stream.read())
    if type(bundle.get("schema_version")) is not int or bundle["schema_version"] != 1 or bundle.get("run_id") != handle.run_id:
        raise ValueError("stock evidence bundle identity mismatch")
    source_ids = [item["artifact_id"] for item in bundle["sources"]]
    if not set(source_ids) <= set(declared_input_ids):
        raise ValueError("bundle sources must all be declared task inputs")
    if build_bundle(handle, source_ids) != bundle:
        raise artifacts.ArtifactConflict("stock evidence bundle source hashes changed")
    return bundle


def require_read_evidence(handle, task: dict, submission: dict, artifact_id: str) -> dict:
    """Require a bound successful structured read; never infer reads from shell text."""
    from autoresearch.session_agent import host_evidence, service, store

    # Deterministic revalidation uses the same original inference task/attempt.
    if "task_id" not in task:
        task = service._task(handle, "stock.card")
    if artifact_id not in task.get("input_artifact_ids", []):
        raise ValueError(f"{artifact_id} must be a declared task input; deep DD 未核")
    with artifacts.open_artifact(handle, artifact_id) as stream:
        data = stream.read()
    if data.startswith(b"DEEP_EVIDENCE_UNAVAILABLE"):
        raise ValueError(f"{artifact_id} unavailable; deep DD 未核")
    attempt = submission.get("envelope", {}).get("attempt")
    if attempt is None:
        entry = store.read_entry(Path(handle.workspace) / "session/tasks.json", task["task_id"])
        attempt = entry["attempt"]
    binding = host_evidence._existing_binding(handle, task["task_id"], attempt)
    if binding is None:
        raise ValueError(f"{artifact_id} read evidence unavailable; deep DD 未核")
    binding = host_evidence._load_binding_ref(handle, f"host-binding:{binding['binding_id']}")
    identity = {"engine": handle.engine, "run_id": handle.run_id,
                "task_id": task["task_id"], "attempt": attempt,
                "role": task["role"], "subject": task["subject"]}
    if any(binding.get(key) != value for key, value in identity.items()):
        raise ValueError("deep DD transcript identity mismatch; 未核")
    normalized = json.loads((Path(handle.capsule) / binding["normalized_path"]).read_text(encoding="utf-8"))
    from autoresearch.common.atomic import sha256_file
    from autoresearch.session_agent import task_access as access
    from autoresearch.trace.read_observation import observe_read

    manifest, manifest_hash = None, None
    request = Path(handle.workspace).resolve()/'session/dispatch'/f"{task['task_id']}-a{attempt}.json"
    try:
        manifest = access._load_manifest(request)
        manifest_hash = sha256_file(access.manifest_path(request))
    except (OSError, ValueError, TypeError, KeyError):
        pass
    observed = observe_read(normalized=normalized, binding=binding, identity=identity,
        artifact_id=artifact_id, artifact_path=str(artifacts.artifact_path(handle, artifact_id).resolve()),
        data=data, manifest=manifest, manifest_sha256=manifest_hash)
    if observed is not None:
        return observed
    raise ValueError(f"{artifact_id} successful full read not observed; deep DD 未核")
