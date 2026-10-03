"""Export a single frozen production run to D1 adapters and forward-study inputs.

A capsule manifest proves file integrity, not host enforcement or independent
experimental assignment. Missing historical metadata remains explicit UNKNOWN.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from autoresearch.common.atomic import canonical_json, sha256_bytes
from autoresearch.common.run_identity import RunContract
from autoresearch.scan.decision_record import load_decision_records
from autoresearch.scan.populations import FrozenSources, build_population
from autoresearch.scan.research_provenance import FILENAME, safe_path, validate_provenance

EXPORT_VERSION = "production_export.v1"
PROFILE_MAP = {"single-stage-v1": "one_stage", "two-stage-v1": "two_stage"}


def _read_identity(src):
    path = src.find("run_contract.json") or src.run / "capsule/identity/run_contract.json"
    contract = RunContract.from_dict(json.loads(src.read_bytes(path)))
    if contract.run_id != src.capsule_run_id or contract.analysis_date != src.analysis_date:
        raise ValueError("production run identity mismatch")
    provenance_path = src.find(FILENAME)
    doc = (
        validate_provenance(json.loads(src.read_bytes(provenance_path)))
        if provenance_path
        else None
    )
    missing = []
    if doc is not None and (doc["run_id"], doc["analysis_date"], doc["contract_hash"]) != (
        contract.run_id,
        contract.analysis_date,
        contract.contract_hash,
    ):
        raise ValueError("research provenance identity mismatch")
    inputs = {}
    if doc:
        for row in doc["base_inputs"]:
            content = src.read_bytes(provenance_path.parent / row["path"])
            if sha256_bytes(content) != row["sha256"]:
                raise ValueError("accepted base input hash mismatch")
            inputs[row["artifact_id"]] = row["sha256"]
    profile = doc["profile"] if doc else None
    identity = {
        "run_id": contract.run_id,
        "profile": PROFILE_MAP.get(profile),
        "original_profile": profile,
        "engine": contract.engine or None,
        "analysis_date": contract.analysis_date,
        "run_key": src.report_dir_id,
        "report_dir_id": src.report_dir_id,
        "capsule_run_id": src.capsule_run_id,
        "input_hash": sha256_bytes(canonical_json(inputs).encode()) if inputs else None,
        "code_sha": contract.git_sha
        if not contract.git_dirty and contract.git_sha != "unknown"
        else None,
        "prompt_hashes": dict(contract.prompt_hashes) or None,
        "config_hashes": {"run_config": contract.config_hash},
        "contract_hash": contract.contract_hash,
        "git_dirty": contract.git_dirty,
    }
    missing += [
        "identity." + key
        for key in ("profile", "engine", "input_hash", "code_sha", "prompt_hashes")
        if identity[key] is None
    ]
    return identity, doc, missing


def _write(directory, name, value):
    path = directory / name
    content = (canonical_json(value) + "\n").encode()
    with path.open("xb") as stream:
        stream.write(content)
    return {"path": str(path), "sha256": sha256_bytes(content)}


def export_run(run_dir, output_dir):
    """Preserve all attempts, including unknown metadata, in an exclusive export."""
    src = FrozenSources(run_dir, verify_hashes=True)
    identity, provenance, missing = _read_identity(src)
    table, coverage = build_population(src.run, include_outcomes=False, sources=src)
    if table is None:
        raise ValueError("frozen candidate population missing")
    if coverage["counts"]["duplicate_source_rows"]:
        raise ValueError("duplicate frozen candidate population")
    records_path = src.find("decision_records.json")
    records = {}
    if records_path is not None:
        src.read_bytes(records_path)
        records = load_decision_records(records_path)
        if any(
            record.analysis_date != identity["analysis_date"]
            or record.contract_hash != identity["contract_hash"]
            for record in records.values()
        ):
            raise ValueError("decision book run identity mismatch")
    observed = provenance["candidates"] if provenance else {}
    task_doc = src.json("_l4_tasks.json") or {}
    tasks = task_doc.get("tasks") or {}
    candidates = set(records) | set(observed) | set(tasks)
    for row in table.to_dict("records"):
        if row["is_finalist"] is True or row["l4_dispatched"] is True:
            candidates.add(row["code"])
    rows = []
    indexed = table.set_index("code")
    for code in sorted(candidates):
        record, facts = records.get(code), observed.get(code, {})
        if code not in indexed.index:
            raise ValueError("dispatched candidate missing frozen population")
        sector = indexed.loc[code, "sector"]
        rows.append(
            {
                "date": identity["analysis_date"],
                "code": code,
                "sector": sector,
                "post_verify_rating": getattr(record, "post_verify_rating", None),
                "final_rating": getattr(record, "final_rating", None),
                "initial_rating": facts.get("initial_rating"),
                "review_required": getattr(record, "review_required", None),
                "review_status": getattr(record, "review_status", "UNKNOWN"),
                "status": "COMPLETE" if record and record.final_rating != "—" else "MISSING",
                "value": None,
                "outcome_status": "PENDING",
                "observed_research": {
                    **facts,
                    "deep_read_verified": indexed.loc[code, "deep_read_verified"],
                },
                "record_hash": getattr(record, "record_hash", None),
            }
        )
    document = {
        "export_version": EXPORT_VERSION,
        "identity": identity,
        "rows": rows,
        "force_full": {code: facts.get("force_full") for code, facts in observed.items()} or None,
        "evidence_depth": {
            "deep_declared": {code: facts.get("deep_declared") for code, facts in observed.items()},
            "deep_read_verified": {
                code: indexed.loc[code, "deep_read_verified"] for code in candidates
            },
        },
        "metering_ref": None,
        "source_run": str(src.run),
        "source_manifest_sha256": sha256_bytes(
            (src.run / "capsule/verification/MANIFEST.sha256").read_bytes()
        ),
    }
    metering_path = src.run / "capsule/agents/session/metering.json"
    if metering_path.is_file():
        from autoresearch.contracts.session_metering import validate_metering

        content = src.read_bytes(metering_path)
        metering = validate_metering(json.loads(content))
        if metering["run_id"] != identity["run_id"] or metering["engine"] != identity["engine"]:
            raise ValueError("metering identity mismatch")
        document["metering_ref"] = {"path": str(metering_path), "sha256": sha256_bytes(content)}
    else:
        missing.append("metering_ref")
    e6 = src.json("_relative_buy_decision.json")
    if e6 is not None and e6.get("date") != identity["analysis_date"]:
        raise ValueError("E6 decision analysis identity mismatch")
    target = safe_path(output_dir)
    if target.is_relative_to(src.run):
        raise ValueError("export must not mutate frozen run")
    target.mkdir(parents=True, exist_ok=False)
    refs = {name: _write(target, name + ".json", document) for name in ("ensemble", "b3")}
    if e6 is not None:
        refs["e6"] = _write(
            target,
            "e6.json",
            {
                **e6,
                "identity": identity,
                "metering_ref": document["metering_ref"],
                "force_full": document["force_full"],
                "evidence_depth": document["evidence_depth"],
            },
        )
    else:
        missing.append("_relative_buy_decision.json")
    # This file retains the entire frozen population, including rejected/unknown rows.
    for name, reference in refs.items():
        _write(target, name + ".ref.json", reference)
    table.to_csv(target / "population.csv", index=False)
    available = datetime.now(timezone.utc).isoformat()
    _write(
        target,
        "forward-inputs.json",
        [
            {
                "artifact_id": "population",
                "path": str(target / "population.csv"),
                "available_at": available,
            }
        ],
    )
    _write(
        target,
        "export.json",
        {
            "export_version": EXPORT_VERSION,
            "status": "UNKNOWN" if missing else "COMPLETE",
            "identity": identity,
            "references": refs,
            "missing": sorted(set(missing + coverage["missing"])),
            "coverage": coverage,
            "created_at": available,
            "observational_only": True,
        },
    )
    return target


def export_labels(run_dir, output_dir, *, study_dir, lake_daily=None, today=None):
    """Generate labels against the verified study's exact frozen calendar identity."""
    from pathlib import Path

    from autoresearch.common import workspace as ws
    from autoresearch.research.forward_study import read_protocol
    protocol = read_protocol(study_dir)
    sessions = [row['date'] for row in protocol['calendar']['sessions']]
    src = FrozenSources(run_dir)
    if src.analysis_date not in {day['date'] for day in protocol['days']}:
        raise ValueError('label run is outside frozen study calendar window')
    table, coverage = build_population(src.run, sources=src, lake_daily=lake_daily,
        calendar=lambda *_: (sessions, 'trade_cal'), today=today)
    if table is None:
        raise ValueError('frozen candidate population missing')
    target = safe_path(output_dir)
    if target.is_relative_to(src.run):
        raise ValueError('label export must not mutate frozen run')
    price_root = Path(lake_daily) if lake_daily else ws.lake_root() / 'daily'
    hashes = json.loads(table.iloc[0]['price_input_hashes_gap_c1_o2']) if len(table) else {}
    prices = {day: str(safe_path(price_root / f'{day}.parquet')) for day, digest in hashes.items() if digest}
    target.mkdir(parents=True, exist_ok=False)
    table.to_csv(target / 'labels.csv', index=False)
    _write(target, 'labels.ref.json', {'date':src.analysis_date, 'path':str(target / 'labels.csv'),
        'price_inputs':prices, 'calendar_source':protocol['calendar']['source'],
        'calendar_digest':table.iloc[0]['calendar_digest'] if len(table) else None,
        'coverage':coverage})
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    print(export_run(args.run_dir, args.output_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
