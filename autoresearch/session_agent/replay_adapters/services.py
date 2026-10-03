"""Scratch-only replay adapters for standalone deterministic service evidence."""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pandas as pd

from autoresearch.common.atomic import (
    atomic_write_bytes,
    atomic_write_json,
    canonical_json,
    sha256_bytes,
)
from autoresearch.trace.operation_evidence import load_operation_evidence

_FORBIDDEN_OPERATIONS = frozenset(
    {"broker.execute", "broker.order", "ops.delete", "ops.migrate", "trade.execute"}
)


def _payload(value) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    return (canonical_json(value) + "\n").encode("utf-8")


def _safe_name(artifact_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", artifact_id)


def _input_map(root: Path, value: dict) -> dict[str, bytes]:
    return {
        ref["artifact_id"]: (root / ref["captured_path"]).read_bytes()
        for ref in value["input_refs"]
    }


def _render_prewarm(parameters: dict, inputs: dict[str, bytes], scratch: Path):
    del scratch
    before = json.loads(inputs["prewarm.lake.before"].decode("utf-8"))
    after = json.loads(inputs["prewarm.lake.after"].decode("utf-8"))
    writes = [
        {"kind": "VIRTUAL_LAKE_WRITE", "path": key, "sha256": after[key]}
        for key in sorted(after)
        if before.get(key) != after[key]
    ]
    manifest = json.loads(inputs["prewarm.manifest.source"].decode("utf-8"))
    if manifest["date"] != parameters["date"]:
        raise ValueError("prewarm evidence date mismatch")
    return {
        "prewarm.manifest": json.dumps(manifest, ensure_ascii=False, indent=1).encode(
            "utf-8"
        )
    }, writes


def _render_dossier(parameters: dict, inputs: dict[str, bytes], scratch: Path):
    from autoresearch.dossier.reconcile import render_reconcile_candidate

    opening = inputs["dossier.opening"].decode("utf-8")
    actual = json.loads(inputs["dossier.actual"].decode("utf-8"))
    text, result = render_reconcile_candidate(
        opening,
        parameters["code"],
        parameters["period"],
        parameters["today"],
        actual,
    )
    if "dossier.fact_context" in inputs:
        from autoresearch.dossier.facts import apply_fact_context
        from autoresearch.dossier.schema import lint_dossier

        text, result['fact_delta'] = apply_fact_context(
            text, json.loads(inputs['dossier.fact_context']), scratch_root=scratch)
        result['issues'] = lint_dossier(text)
    effects = [
        {
            "kind": "DOSSIER_PATCH",
            "code": parameters["code"],
            "before_sha256": sha256_bytes(opening.encode("utf-8")),
            "after_sha256": sha256_bytes(text.encode("utf-8")),
        }
    ]
    return {
        "dossier.candidate": text.encode("utf-8"),
        "dossier.result": _payload(result),
    }, effects


def _render_dossier_delta(parameters: dict, inputs: dict[str, bytes], scratch: Path):
    from autoresearch.dossier.delta import render_scan_delta_snapshot
    from autoresearch.dossier.facts import apply_fact_context
    from autoresearch.dossier.schema import lint_dossier

    opening = inputs['dossier.opening'].decode('utf-8')
    text, result = render_scan_delta_snapshot(
        opening, parameters, json.loads(inputs['dossier.scan_inputs']), scratch_root=scratch)
    if 'dossier.fact_context' in inputs:
        text, result['fact_delta'] = apply_fact_context(
            text, json.loads(inputs['dossier.fact_context']), scratch_root=scratch)
        result['issues'] = lint_dossier(text)
    return {'dossier.candidate': text.encode('utf-8'), 'dossier.result': _payload(result)}, [
        {'kind': 'DOSSIER_PATCH', 'code': parameters['code'],
         'before_sha256': sha256_bytes(inputs['dossier.opening']),
         'after_sha256': sha256_bytes(text.encode('utf-8'))}]


def _render_broker_ingest(parameters: dict, inputs: dict[str, bytes], scratch: Path):
    from autoresearch.broker.ingest import normalize_source_file

    source = scratch / parameters["source_file"]
    atomic_write_bytes(source, inputs["broker.source"])
    frame, report = normalize_source_file(
        source,
        source_kind=parameters["source_kind"],
        account=parameters.get("account"),
        ingested_at=parameters["ingested_at"],
        today=date.fromisoformat(parameters["today"]),
    )
    target = scratch / "service-normalized"
    frame.to_csv(target, index=False, encoding="utf-8")
    return {
        "broker.normalized": target.read_bytes(),
        "broker.validation": _payload(report),
    }, [{"kind": "BROKER_NORMALIZED_ROWS", "rows": int(len(frame))}]


def _render_broker_reconcile(parameters: dict, inputs: dict[str, bytes], scratch: Path):
    from autoresearch.broker import reconcile, store

    raw = scratch / store.RAW_DIRNAME
    for artifact_id, data in inputs.items():
        kind = artifact_id.removeprefix("broker.raw.")
        atomic_write_bytes(raw / f"{kind}.csv", data)
    text = reconcile.report(
        scratch,
        account=parameters.get("account"),
        since=parameters.get("since"),
    )
    return {"broker.reconcile.report": (text + "\n").encode("utf-8")}, []


def _render_research(parameters: dict, inputs: dict[str, bytes], scratch: Path):
    from autoresearch.research.stage_value import paired_daily_selection

    source = scratch / "service-candidate-input"
    atomic_write_bytes(source, inputs["research.candidates"])
    frame = pd.read_csv(source, dtype={"code": str})
    result = paired_daily_selection(frame)
    target = scratch / "service-evaluation-output"
    result.to_csv(target, index=False, encoding="utf-8")
    return {"research.evaluation": target.read_bytes()}, [
        {"kind": "CANDIDATE_EVALUATION", "rows": int(len(result)), **parameters}
    ]


_RENDERERS = {
    "prewarm": _render_prewarm,
    "dossier.reconcile": _render_dossier,
    "dossier.delta": _render_dossier_delta,
    "broker.ingest": _render_broker_ingest,
    "broker.reconcile": _render_broker_reconcile,
    "research.evaluate": _render_research,
}


def replay_operation_evidence(path: Path | str, output_root: Path | str) -> dict:
    """Recompute one supported service under *output_root* and never apply effects."""
    root, value = load_operation_evidence(path)
    if value["operation"] in _FORBIDDEN_OPERATIONS or value["operation"] not in _RENDERERS:
        raise ValueError(f"operation has no replay handler:{value['operation']}")
    output = Path(output_root)
    output.mkdir(parents=True, exist_ok=True)
    scratch = output / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    actual, effects = _RENDERERS[value["operation"]](
        value["parameters"], _input_map(root, value), scratch
    )
    expected = {
        ref["artifact_id"]: (root / ref["captured_path"]).read_bytes()
        for ref in value["output_refs"]
    }
    missing = sorted(set(expected) ^ set(actual))
    diffs = [
        artifact_id
        for artifact_id in sorted(set(expected) & set(actual))
        if expected[artifact_id] != actual[artifact_id]
    ]
    for artifact_id, data in actual.items():
        atomic_write_bytes(output / "outputs" / _safe_name(artifact_id), data)
    atomic_write_json(output / "service-effects", effects)
    result = {
        "schema_version": 1,
        "operation_id": value["operation_id"],
        "operation": value["operation"],
        "status": (
            "MATCH"
            if not missing and not diffs and effects == value["effects"]
            else "MISMATCH"
        ),
        "missing": missing,
        "diffs": diffs,
        "effects": effects,
    }
    atomic_write_json(output / "service-result", result)
    return result


__all__ = ["replay_operation_evidence"]
