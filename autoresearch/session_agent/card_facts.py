"""Deterministic facts projection for the opt-in two-stage research card.

Never clean the legacy prompt: select known data owners and retain their payloads
and lineage. The inference allowlist contains only this projection and its frame.
"""
from __future__ import annotations

import base64
import csv
import io
import json
import os

from autoresearch.common.atomic import atomic_write_json, sha256_bytes
from autoresearch.session_agent import artifacts

INITIAL_CONTRACT = "research.card.initial.v1"
DECISION_CONTRACT = "research.card.decision.v1"
_RAW_TABLES = frozenset({"pledge.csv", "seats.csv", "consensus.csv", "fund_hold.csv",
                         "calendar.csv", "index_events.csv", "L3_catalyst.csv"})


def project_facts(subject: str, inputs: dict[str, bytes]) -> dict:
    """Pure projection shared by live execution and offline replay."""
    sources = []

    def add(source_id: str, content: str, *, member: str | None = None,
            projection: str = "raw") -> None:
        sources.append({"evidence_id": f"fact.{len(sources) + 1}",
                        "source_artifact_id": source_id,
                        "source_sha256": sha256_bytes(inputs[source_id]),
                        "member": member, "projection": projection,
                        "sha256": sha256_bytes(content.encode("utf-8")), "content": content})

    holding = False
    bundle_id = "scan.l4.source.bundle"
    members = {}
    if bundle_id in inputs:
        bundle = json.loads(inputs[bundle_id])
        if bundle.get("schema_version") != 1 or bundle.get("phase") != "l4_source":
            raise ValueError("facts require frozen L4 source bundle")
        members = {name: base64.b64decode(payload, validate=True).decode("utf-8")
                   for name, payload in bundle["files"].items()
                   if name in _RAW_TABLES or name in {"finalists.csv", "market_pack.json"}
                   or name.startswith("sector_briefs/") and name.endswith(".md")}
        rows = list(csv.DictReader(io.StringIO(members.get("finalists.csv", ""))))
        matching = [row for row in rows if str(row.get("code", "")).split(".")[0].zfill(6) == subject]
        if len(matching) != 1:
            raise ValueError("facts require one frozen finalist identity")
        holding = matching[0].get("lane") == "pinned"

    slim = [key for key in inputs if key.endswith(".slim")]
    deep = [key for key in inputs if key.endswith(".deep")]
    if len(slim) != 1 or len(deep) != 1:
        raise ValueError("facts require exactly one raw slim and deep pair")
    add(slim[0], inputs[slim[0]].decode("utf-8"))
    if holding:
        add(deep[0], inputs[deep[0]].decode("utf-8"))
    for name in sorted(_RAW_TABLES & members.keys()):
        # Whole raw tables retain risk columns, dates and source URLs; no prior
        # judgments are stored in these deterministic producer tables.
        add(bundle_id, members[name], member=name)
    if "market_pack.json" in members:
        from autoresearch.scan.market import market_context_block
        add(bundle_id, market_context_block(json.loads(members["market_pack.json"])),
            member="market_pack.json", projection="market_context_block.v1")
    from autoresearch.sector.brief import extract_terrain
    for name in sorted(key for key in members if key.startswith("sector_briefs/")):
        terrain = extract_terrain(members[name])
        if terrain:
            add(bundle_id, terrain, member=name, projection="extract_terrain.v1")
    return {"schema_version": 1, "subject": subject, "holding": holding,
            "frame_hash": sha256_bytes(inputs["research.frame"]), "sources": sources}


def prepare_projection(handle, task: dict, attempt: int):
    """Owner freezes exact registered inputs; the child needs no mutable run state."""
    from autoresearch.session_agent.evidence import _freeze_attempt_record

    inputs = {key: artifacts.read_bytes(handle, key) for key in task["input_artifact_ids"]}
    return _freeze_attempt_record(handle, task["task_id"], attempt, "facts_request", {
        "schema_version": 1, "task_id": task["task_id"], "attempt": attempt,
        "subject": task["subject"],
        "inputs": {key: {"sha256": sha256_bytes(data),
                         "base64": base64.b64encode(data).decode("ascii")}
                   for key, data in inputs.items()},
    })


def prepared_projection(path, expected_hash: str) -> dict:
    from pathlib import Path

    data = Path(path).read_bytes()
    if sha256_bytes(data) != expected_hash:
        raise ValueError("prepared facts request hash mismatch")
    request = json.loads(data)
    if request["schema_version"] != 1:
        raise ValueError("unsupported prepared facts request")
    inputs = {}
    for key, row in request["inputs"].items():
        data = base64.b64decode(row["base64"], validate=True)
        if sha256_bytes(data) != row["sha256"]:
            raise ValueError("prepared facts input hash mismatch")
        inputs[key] = data
    return project_facts(request["subject"], inputs)


def build_registered_facts(handle, task: dict) -> dict:
    inputs = {}
    for artifact_id in task["input_artifact_ids"]:
        with artifacts.open_artifact(handle, artifact_id) as stream:
            inputs[artifact_id] = stream.read()
    value = project_facts(task["subject"], inputs)
    if len(task["output_artifact_ids"]) != 1:
        raise ValueError("facts operation requires one manifest output")
    atomic_write_json(artifacts.declared_path(handle, task["output_artifact_ids"][0]), value)
    return value


def execute_active(handle=None) -> dict:
    from autoresearch.session_agent.domain_ops import _active_handle
    from autoresearch.session_agent.service import _task
    current = handle or _active_handle()
    task = _task(current, os.environ["AUTORESEARCH_TASK_ID"])
    if task["operation"] != "research.card.facts":
        raise ValueError("facts operation differs from frozen task")
    return build_registered_facts(current, task)


def replay(unit: dict, context) -> list[dict]:
    from autoresearch.session_agent.replay_adapters.common import input_path
    inputs = {ref["artifact_id"]: input_path(context, ref["artifact_id"]).read_bytes()
              for ref in unit["input_refs"]
              if ref["artifact_id"] == "research.frame"
              or ref["artifact_id"].endswith((".slim", ".deep", ".source.bundle"))}
    value = project_facts(context.operation_request["subject"], inputs)
    atomic_write_json(context.output_path(unit["expected_outputs"][0]["artifact_id"]), value)
    return []


def _read(handle, artifact_id: str) -> dict:
    with artifacts.open_artifact(handle, artifact_id) as stream:
        return json.load(stream)


def _one(ids: list[str], suffix: str) -> str:
    found = [key for key in ids if key.endswith(suffix)]
    if len(found) != 1:
        raise ValueError(f"one declared {suffix} artifact required")
    return found[0]


def bound_manifest(handle, task: dict) -> tuple[str, dict]:
    fact_id = _one(task["input_artifact_ids"], ".facts")
    manifest = _read(handle, fact_id)
    if (manifest.get("schema_version") != 1 or manifest.get("subject") != task["subject"]
            or manifest.get("frame_hash") != artifacts.snapshot_artifact(handle, "research.frame")["sha256"]):
        raise ValueError("fact manifest subject/frame binding mismatch")
    seen = set()
    for source in manifest["sources"]:
        if (source["evidence_id"] in seen
                or source["sha256"] != sha256_bytes(source["content"].encode("utf-8"))):
            raise ValueError("invalid fact source identity/hash")
        seen.add(source["evidence_id"])
    return fact_id, manifest


def validate_bound_initial(handle, task: dict, initial: dict) -> dict:
    from autoresearch.contracts.research_card import validate_initial_assessment
    validate_initial_assessment(initial)
    fact_id, manifest = bound_manifest(handle, task)
    if initial["subject"] != task["subject"]:
        raise ValueError("initial subject binding mismatch")
    if initial["frame_hash"] != manifest["frame_hash"]:
        raise ValueError("initial frame_hash binding mismatch")
    if initial["fact_manifest_hash"] != artifacts.snapshot_artifact(handle, fact_id)["sha256"]:
        raise ValueError("initial fact_manifest_hash binding mismatch")
    if not set(initial["evidence_refs"]) <= {source["evidence_id"] for source in manifest["sources"]}:
        raise ValueError("initial evidence_refs are not declared facts")
    return manifest


def validate_initial_output(handle, submission: dict, task: dict) -> None:
    initial = _read(handle, _one(task["output_artifact_ids"], ".initial"))
    manifest = validate_bound_initial(handle, task, initial)
    if manifest["holding"]:
        from autoresearch.session_agent.evidence_bundle import require_read_evidence
        require_read_evidence(handle, task, submission, _one(task["input_artifact_ids"], ".deep"))


def validate_changes_output(handle, task: dict, text: str) -> None:
    from autoresearch.agents.utils.rating import validate_rating_and_proposal
    from autoresearch.contracts.research_card import validate_decision_changes
    from autoresearch.scan.l4.card_io import _dimensions, _gates, card_from_text
    initial_id = _one(task["input_artifact_ids"], ".initial")
    initial = _read(handle, initial_id)
    manifest = validate_bound_initial(handle, task, initial)
    changes = validate_decision_changes(_read(handle, _one(task["output_artifact_ids"], ".changes")))
    if changes["subject"] != task["subject"]:
        raise ValueError("decision subject binding mismatch")
    if changes["initial_hash"] != artifacts.snapshot_artifact(handle, initial_id)["sha256"]:
        raise ValueError("decision initial_hash binding mismatch")
    code = task["subject"].split(".")[0]
    from autoresearch.contracts.profiles import CURRENT_CARD_RULES
    from autoresearch.scan.l4.card_io import card_rules_version
    if card_rules_version(handle=handle) == CURRENT_CARD_RULES:
        from autoresearch.common.card_decision import card_from_decision_text
        frame = _read(handle, "research.frame")
        if task["role"] == "scan.l4.card":
            card = card_from_text(text, code=code, analysis_date=handle.analysis_date,
                                  holding=manifest["holding"], rules_version=CURRENT_CARD_RULES)
        else:
            card = card_from_decision_text(text, subject=task["subject"], venue=frame["venue"],
                                           analysis_date=handle.analysis_date, holding=manifest["holding"])
    elif len(code) == 6 and code.isdigit():
        card = card_from_text(text, code=code, analysis_date=handle.analysis_date, holding=manifest["holding"])
    else:
        # The existing ResearchCard identity schema is A-share-only. Use its
        # exact field parsers for standalone symbols without inventing a code.
        card = {"initial_rating": validate_rating_and_proposal(text)[0],
                "dimensions": _dimensions(text), "gates": _gates(text)}
    changed = {"rating"} if initial["initial_rating"] != card["initial_rating"] else set()
    for group in ("dimensions", "gates"):
        changed.update(f"{group}.{key}" for key, value in card[group].items()
                       if initial[f"initial_{group}"][key] != value)
    if changed != set(changes["changed_fields"]):
        raise ValueError("changed_fields differs from the actual initial/final delta")
    references = {source["evidence_id"] for source in manifest["sources"]}
    for artifact_id in task["input_artifact_ids"]:
        if artifact_id.endswith((".slim", ".deep", ".intel", ".intel_status", ".intel_bundle",
                                 ".intel_doc")):
            artifacts.snapshot_artifact(handle, artifact_id)
            references.add(artifact_id)
    if not set(changes["new_evidence_refs"]) <= references:
        raise ValueError("new_evidence_refs are not declared evidence")


def validate_distinct_context(handle, task: dict, host_receipt: dict, *, _entry_reader=None) -> None:
    """A second context must differ from both main and accepted initial context."""
    from pathlib import Path

    from autoresearch.session_agent import service, store
    initial_id = _one(task["input_artifact_ids"], ".initial")
    producers = [row for row in service._all_tasks(handle)
                 if initial_id in row["output_artifact_ids"]]
    if len(producers) != 1:
        raise ValueError("decision requires one frozen initial producer")
    producer = producers[0]
    entry = (_entry_reader(producer["task_id"]) if _entry_reader is not None else
             store.read_entry(Path(handle.workspace) / "session/tasks.json", producer["task_id"]))
    if entry["state"] != "SUCCEEDED":
        raise ValueError("decision requires a successful immutable initial")
    completion = Path(handle.capsule) / "agents/session/completions" / f"{producer['task_id']}-a{entry['attempt']}.json"
    previous_id = json.loads(completion.read_text())["host_receipt_id"]
    previous = json.loads((Path(handle.workspace) / "session/receipts" / f"{previous_id}.json").read_text())
    if previous["context_ref"] == host_receipt["context_ref"]:
        raise ValueError("decision requires an independent context distinct from initial")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-request", required=True)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args()
    print(json.dumps(prepared_projection(args.prepared_request, args.sha256), ensure_ascii=False))
