#!/usr/bin/env python3
"""终评级领域事实：结构化记录、完整性 hash 和原子记录簿。"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

from autoresearch.scan.run_contract import load_run_contract, sha256_json

DECISION_RECORD_SCHEMA_VERSION = 3
DECISION_BOOK_SCHEMA_VERSION = 1
_CODE_RE = re.compile(r"^\d{6}$")
_RATINGS = {"Buy", "Overweight", "Hold", "Underweight", "Sell", "—"}
#: ⚠️ `proposal` 的语义自 E6 转正起**降格**(2026-08-19 E3b 裁定 1):它是
#: **研究评级派生的提案**(`final_rating` 的五档→三档投影),**不是** BUY 决策。
#: 正式 BUY 只存在于 `_relative_buy_decision.json` 的 `buys[]` 里(Wave12 `:466`
#: 「research_rating 仅作证据字段,不得独立渲染成买入建议」的同一精神)。
#:
#: **为什么这里不改成读决策文件**:`decision_records.json` 是 `relative_buy.build_decision`
#: 的**输入**(它现算护照要读评级),让它反过来依赖决策文件 = 循环依赖,无解 —— 所以
#: 保持读评级,只降语义 + 改文档。谁想拿 `proposal == "BUY"` 当买入建议渲染,先读这段。
_PROPOSALS = {"BUY", "HOLD", "SELL", "—"}
_GATE_STATES = {"PASS", "FAIL", "UNKNOWN"}


def complete_review_results(source_rating: str, ratings: object) -> bool:
    """Completion needs observed valid ratings, not a model's completion flag."""
    return (
        isinstance(ratings, list) and 2 <= len(ratings) <= 3
        and all(isinstance(item, str) and item in _RATINGS - {"—"} for item in ratings)
        and ratings[0] == source_rating
        and (len(ratings) == 3 or ratings[0] == ratings[1])
    )


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
    review_policy_version: str | None = None
    review_trigger: str | None = None
    review_required: bool | None = None
    review_status: str = "UNKNOWN"
    review_coverage_reason: str = "historical_coverage_not_recorded"

    post_verify_rating: str | None = None

    def _hash_payload(self) -> dict:
        payload = self.to_dict()
        payload.pop("record_hash")
        return payload

    def to_dict(self) -> dict:
        value = asdict(self)
        if self.schema_version == 1:
            value = {key: item for key, item in value.items() if not key.startswith("review_")}
        if self.schema_version < 3:
            value.pop("post_verify_rating")
        return value

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
        review_policy_version: str | None = None,
        review_trigger: str | None = None,
        review_required: bool | None = None,
        review_status: str = "UNKNOWN",
        review_coverage_reason: str = "historical_coverage_not_recorded",
        post_verify_rating: str | None = None,
    ) -> DecisionRecord:
        if post_verify_rating is not None and post_verify_rating not in _RATINGS:
            raise ValueError("invalid post verify rating")
        if review_status not in {"UNKNOWN", "NOT_REQUIRED", "MISSING", "INCOMPLETE", "COMPLETE"}:
            raise ValueError("invalid review status")
        if review_required is not None and type(review_required) is not bool:
            raise ValueError("invalid review required flag")
        if review_trigger not in {None, "ow_review", "sell_review"}:
            raise ValueError("invalid review trigger")
        if review_status != "UNKNOWN":
            if not review_policy_version or not review_coverage_reason or review_required != (review_trigger is not None):
                raise ValueError("inconsistent review coverage")
            if (review_status == "NOT_REQUIRED") != (review_required is False):
                raise ValueError("inconsistent review status")
        if review_status == "COMPLETE" and not complete_review_results(post_verify_rating if post_verify_rating is not None else source_rating, ensemble_ratings):
            raise ValueError("complete review requires actual review results")
        code = str(code).zfill(6)
        if not _CODE_RE.fullmatch(code):
            raise ValueError(f"invalid decision code: {code!r}")
        ratings = (source_rating, rubric_rating, final_rating, *ensemble_ratings)
        if any(not isinstance(rating, str) or rating not in _RATINGS for rating in ratings):
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
            review_policy_version=review_policy_version,
            review_trigger=review_trigger,
            review_required=review_required,
            review_status=review_status,
            review_coverage_reason=review_coverage_reason,
            post_verify_rating=post_verify_rating,
        )
        return replace(base, record_hash=sha256_json(base._hash_payload()))

    @classmethod
    def from_dict(cls, raw: dict) -> DecisionRecord:
        if type(raw.get("schema_version")) is not int:
            raise ValueError("invalid decision schema version")
        required = {field.name for field in fields(cls)}
        if raw["schema_version"] == 1:
            required = {key for key in required if not key.startswith("review_")}
        if raw["schema_version"] < 3:
            required.discard("post_verify_rating")
        if set(raw) != required:
            raise ValueError("invalid decision record fields")
        record = cls(**raw)
        rebuilt = cls.build(
            **{
                key: value
                for key, value in raw.items()
                if key not in {"schema_version", "record_hash"}
            }
        )
        if record.schema_version not in {1, 2, DECISION_RECORD_SCHEMA_VERSION}:
            raise ValueError(
                f"unsupported decision schema_version={record.schema_version}"
            )
        if record.schema_version == 1 and any(key.startswith("review_") for key in raw):
            raise ValueError("legacy decision cannot declare review coverage")
        if record.schema_version < 3:
            rebuilt = replace(rebuilt, schema_version=record.schema_version)
            rebuilt = replace(rebuilt, record_hash=sha256_json(rebuilt._hash_payload()))
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
