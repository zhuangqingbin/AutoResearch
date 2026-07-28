#!/usr/bin/env python3
"""终评级领域事实：结构化记录、完整性 hash 和原子记录簿。"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from autoresearch.scan.run_contract import load_run_contract, sha256_json

DECISION_RECORD_SCHEMA_VERSION = 1
DECISION_BOOK_SCHEMA_VERSION = 1
_CODE_RE = re.compile(r"^\d{6}$")
_RATINGS = {"Buy", "Overweight", "Hold", "Underweight", "Sell", "—"}
_PROPOSALS = {"BUY", "HOLD", "SELL", "—"}
_GATE_STATES = {"PASS", "FAIL", "UNKNOWN"}


@dataclass(frozen=True)
class DecisionRecord:
    schema_version: int
    analysis_date: str
    contract_hash: str | None
    code: str
    source_rating: str
    rubric_rating: str
    gate_states: dict[str, str]
    early_stop: dict | None
    ensemble_ratings: list[str]
    final_rating: str
    proposal: str
    reason: str
    evidence_refs: list[str]
    first_rejection_stage: str | None
    record_hash: str

    def _hash_payload(self) -> dict:
        payload = asdict(self)
        payload.pop("record_hash")
        return payload

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def build(
        cls,
        *,
        analysis_date: str,
        contract_hash: str | None,
        code: str,
        source_rating: str,
        rubric_rating: str,
        gate_states: dict[str, str],
        early_stop: dict | None,
        ensemble_ratings: list[str],
        final_rating: str,
        proposal: str,
        reason: str,
        evidence_refs: list[str],
        first_rejection_stage: str | None,
    ) -> DecisionRecord:
        code = str(code).zfill(6)
        if not _CODE_RE.fullmatch(code):
            raise ValueError(f"invalid decision code: {code!r}")
        ratings = (source_rating, rubric_rating, final_rating, *ensemble_ratings)
        if any(rating not in _RATINGS for rating in ratings):
            raise ValueError(f"invalid decision rating: {ratings}")
        if proposal not in _PROPOSALS:
            raise ValueError(f"invalid decision proposal: {proposal}")
        if any(state not in _GATE_STATES for state in gate_states.values()):
            raise ValueError(f"invalid gate state: {gate_states}")
        normalized_early = (
            None
            if early_stop is None
            else {
                "phase": str(early_stop["phase"]),
                "reason": str(early_stop["reason"]),
            }
        )
        base = cls(
            schema_version=DECISION_RECORD_SCHEMA_VERSION,
            analysis_date=analysis_date,
            contract_hash=contract_hash,
            code=code,
            source_rating=source_rating,
            rubric_rating=rubric_rating,
            gate_states=dict(sorted(gate_states.items())),
            early_stop=normalized_early,
            ensemble_ratings=[str(value) for value in ensemble_ratings],
            final_rating=final_rating,
            proposal=proposal,
            reason=str(reason),
            evidence_refs=list(dict.fromkeys(str(value) for value in evidence_refs)),
            first_rejection_stage=(
                None if first_rejection_stage is None else str(first_rejection_stage)
            ),
            record_hash="",
        )
        return replace(base, record_hash=sha256_json(base._hash_payload()))

    @classmethod
    def from_dict(cls, raw: dict) -> DecisionRecord:
        record = cls(**raw)
        rebuilt = cls.build(
            **{
                key: value
                for key, value in raw.items()
                if key not in {"schema_version", "record_hash"}
            }
        )
        if record.schema_version != DECISION_RECORD_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported decision schema_version={record.schema_version}"
            )
        if record.record_hash != rebuilt.record_hash:
            raise ValueError("decision record hash mismatch")
        return record


def _contract_hash(scan: Path) -> str | None:
    path = scan / "run_contract.json"
    if not path.exists():
        return None
    try:
        return load_run_contract(path).contract_hash
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def write_decision_records(
    scan_dir: Path | str,
    records: list[DecisionRecord],
) -> Path:
    scan = Path(scan_dir)
    ordered = sorted(records, key=lambda record: record.code)
    contract_hash = _contract_hash(scan)
    if any(record.analysis_date != scan.name for record in ordered):
        raise ValueError("decision record analysis_date mismatch")
    if any(record.contract_hash != contract_hash for record in ordered):
        raise ValueError("decision record contract_hash mismatch")
    if len({record.code for record in ordered}) != len(ordered):
        raise ValueError("decision record duplicate code")
    payloads = [record.to_dict() for record in ordered]
    book = {
        "schema_version": DECISION_BOOK_SCHEMA_VERSION,
        "analysis_date": scan.name,
        "contract_hash": contract_hash,
        "records": payloads,
        "records_hash": sha256_json(payloads),
    }
    target = scan / "decision_records.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.name}.tmp")
    temp.write_text(
        json.dumps(book, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(target)
    return target


def load_decision_records(path: Path | str) -> dict[str, DecisionRecord]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if (
        not isinstance(raw, dict)
        or raw.get("schema_version") != DECISION_BOOK_SCHEMA_VERSION
    ):
        raise ValueError("unsupported decision book")
    rows = raw.get("records")
    if not isinstance(rows, list) or raw.get("records_hash") != sha256_json(rows):
        raise ValueError("decision book records_hash mismatch")
    records = [DecisionRecord.from_dict(row) for row in rows]
    if any(record.analysis_date != raw.get("analysis_date") for record in records):
        raise ValueError("decision book analysis_date mismatch")
    if any(record.contract_hash != raw.get("contract_hash") for record in records):
        raise ValueError("decision book contract_hash mismatch")
    if len({record.code for record in records}) != len(records):
        raise ValueError("decision book duplicate code")
    return {record.code: record for record in records}


def safe_write_decision_records(
    scan_dir: Path | str,
    records: list[DecisionRecord],
) -> Path | None:
    try:
        return write_decision_records(scan_dir, records)
    except Exception as exc:  # noqa: BLE001 — shadow fact failure must not block L5
        print(f"[decision_record] 写入失败: {exc}", file=sys.stderr)
        return None
