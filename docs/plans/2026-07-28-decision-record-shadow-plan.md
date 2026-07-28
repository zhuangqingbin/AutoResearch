# DecisionRecord Shadow Facts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将每只 finalist 的卡面评级、rubric、三门、早停、verify/ensemble 折回和最终交易结论固化为可校验的结构化事实，并与现有终评级文件保持影子 parity。

**Architecture:** 新增独立 `decision_record.py`，以单个原子 `decision_records.json` 保存排序后的记录簿，避免逐票文件在同日重跑时残留僵尸记录。`assemble` 在现有 `_final_ratings.json` 和 `_early_stop.json` 写出后双写 DecisionRecord；旧消费者不切换，health 负责证明评级/早停 parity，ArtifactIndex 和 trace 负责发布。

**Tech Stack:** Python 3.12、标准库 `dataclasses/hashlib/json/re/pathlib`、现有 RunContract、ArtifactIndex、StageResult、pytest。

---

## Scope and invariants

本批只做 DecisionRecord 影子事实层，不迁移 learning/dossier/retro 消费者，不实现 outbox。

必须保留：

- `_final_ratings.json` 继续作为旧消费者单一入口。
- `_early_stop.json` 继续写出。
- summary 文本、买单数量、排序、verify/ensemble 折回顺序不变。
- DecisionRecord 失败只写 stderr，不阻断报告发布。
- 缺卡也必须有记录，`final_rating="—"`，不能因缺卡从事实表消失。
- 三门使用 `PASS / FAIL / UNKNOWN`，不得把无门柱早停卡误记为 PASS。
- 首次拒绝阶段只解释当前既有路径，不引入新的拒绝规则。

记录字段：

```text
schema_version
analysis_date
contract_hash
code
source_rating
rubric_rating
gate_states
early_stop
ensemble_ratings
final_rating
proposal
reason
evidence_refs
first_rejection_stage
record_hash
```

## File map

- Create: `autoresearch/scan/decision_record.py`
  - DecisionRecord 类型、记录簿 hash、原子读写和兼容读取。
- Create: `tests/scan/test_decision_record.py`
  - schema、hash、验证、排序、契约绑定和篡改测试。
- Modify: `autoresearch/scan/assemble.py`
  - 保留折回前中间状态并双写 DecisionRecord。
- Modify: `tests/scan/test_assemble.py`
  - 锁定四只 fixture 的完整决策事实与旧终评级 parity。
- Modify: `autoresearch/scan/artifacts.py`
  - 注册 `decision_records.json`。
- Modify: `autoresearch/scan/health.py`
  - 加入 contract/final rating/early stop parity。
- Modify: `tests/scan/test_artifacts.py`
  - 锁定决策记录产物指纹。
- Modify: `tests/scan/test_health.py`
  - 锁定 `ABSENT/OK/MISMATCH/INVALID`。
- Modify: `docs/specs/2026-07-28-scan-market-unified-optimization-master-design.md`
  - 记录 Wave 1 第三批影子态。

### Task 1: DecisionRecord domain and atomic book

**Files:**

- Create: `tests/scan/test_decision_record.py`
- Create: `autoresearch/scan/decision_record.py`

- [x] **Step 1: Write failing domain tests**

```python
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from autoresearch.scan.decision_record import (
    DecisionRecord,
    load_decision_records,
    write_decision_records,
)
from autoresearch.scan.run_contract import RunContract, write_run_contract

NOW = datetime(2026, 7, 28, 17, 0, tzinfo=timezone.utc)


def _record(code="000001", final="Hold", contract_hash=None):
    return DecisionRecord.build(
        analysis_date="2026-07-28",
        contract_hash=contract_hash,
        code=code,
        source_rating="Hold",
        rubric_rating="Hold",
        gate_states={
            "主力真在": "PASS",
            "业绩真兑现": "FAIL",
            "估值不透支": "PASS",
        },
        early_stop=None,
        ensemble_ratings=[],
        final_rating=final,
        proposal="HOLD",
        reason="rubric:业绩真兑现",
        evidence_refs=[f"finalists.csv#{code}", f"details/{code}.md"],
        first_rejection_stage="L4_RUBRIC",
    )


def _contract(scan):
    contract = RunContract.build(
        analysis_date=scan.name,
        user_config={},
        pinned={"kept": [], "expired": []},
        data_policy={"source": "tushare"},
        stage_budgets={},
        artifact_schema_versions={},
        git_sha="abc",
        now=NOW,
    )
    write_run_contract(scan / "run_contract.json", contract)
    return contract


def test_record_round_trip_and_hash(tmp_path):
    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    contract = _contract(scan)
    record = _record(contract_hash=contract.contract_hash)
    path = write_decision_records(scan, [record])
    loaded = load_decision_records(path)
    assert path == scan / "decision_records.json"
    assert loaded["000001"].to_dict() == record.to_dict()
    assert len(record.record_hash) == 64


def test_book_is_sorted_and_semantically_stable(tmp_path):
    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    left = write_decision_records(scan, [_record("600000"), _record("000001")])
    before = left.read_bytes()
    right = write_decision_records(scan, [_record("000001"), _record("600000")])
    assert right.read_bytes() == before
    raw = json.loads(right.read_text(encoding="utf-8"))
    assert [row["code"] for row in raw["records"]] == ["000001", "600000"]


def test_load_rejects_tampered_record(tmp_path):
    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    path = write_decision_records(scan, [_record()])
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["records"][0]["final_rating"] = "Buy"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        load_decision_records(path)


def test_write_rejects_contract_mismatch(tmp_path):
    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    contract = _contract(scan)
    assert contract.contract_hash != "a" * 64
    with pytest.raises(ValueError, match="contract_hash"):
        write_decision_records(scan, [_record(contract_hash="a" * 64)])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("code", "../1"),
        ("final_rating", "Strong Buy"),
        ("proposal", "WAIT"),
        ("gate_states", {"主力真在": "YES"}),
    ],
)
def test_record_rejects_invalid_domain_values(field, value):
    kwargs = {
        "analysis_date": "2026-07-28",
        "contract_hash": None,
        "code": "000001",
        "source_rating": "Hold",
        "rubric_rating": "Hold",
        "gate_states": {},
        "early_stop": None,
        "ensemble_ratings": [],
        "final_rating": "Hold",
        "proposal": "HOLD",
        "reason": "x",
        "evidence_refs": [],
        "first_rejection_stage": "L4_RUBRIC",
    }
    kwargs[field] = value
    with pytest.raises(ValueError):
        DecisionRecord.build(**kwargs)
```

- [x] **Step 2: Verify RED**

Run:

```bash
uv run --no-sync python -m pytest tests/scan/test_decision_record.py -q
```

Expected: collection fails with `ModuleNotFoundError`.

- [x] **Step 3: Implement DecisionRecord and book validation**

Create `autoresearch/scan/decision_record.py`:

```python
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
    ) -> "DecisionRecord":
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
            else {"phase": str(early_stop["phase"]), "reason": str(early_stop["reason"])}
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
    def from_dict(cls, raw: dict) -> "DecisionRecord":
        record = cls(**raw)
        rebuilt = cls.build(**{
            key: value
            for key, value in raw.items()
            if key not in {"schema_version", "record_hash"}
        })
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
    except Exception:
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
    if not isinstance(raw, dict) or raw.get("schema_version") != DECISION_BOOK_SCHEMA_VERSION:
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
    except Exception as exc:
        print(f"[decision_record] 写入失败: {exc}", file=sys.stderr)
        return None
```

- [x] **Step 4: Run domain tests**

Run:

```bash
uv run --no-sync python -m pytest tests/scan/test_decision_record.py -q
```

Expected: `8 passed`.

- [x] **Step 5: Commit**

```bash
git add autoresearch/scan/decision_record.py tests/scan/test_decision_record.py
git commit -m "feat(scan): add decision record facts"
```

### Task 2: Assemble shadow writer

**Files:**

- Modify: `tests/scan/test_assemble.py`
- Modify: `autoresearch/scan/assemble.py`

- [x] **Step 1: Add failing integration tests**

Append:

```python
def test_decision_records_capture_fold_chain(published):
    from autoresearch.scan.decision_record import load_decision_records

    records = load_decision_records(published["scan_dir"] / "decision_records.json")
    assert set(records) == {"300476", "600519", "002384", "301117"}

    downgraded = records["300476"]
    assert downgraded.source_rating == "Overweight"
    assert downgraded.rubric_rating == "Overweight"
    assert downgraded.final_rating == "Hold"
    assert downgraded.proposal == "HOLD"
    assert downgraded.first_rejection_stage == "VERIFY"
    assert downgraded.reason == "verify:降级"
    assert "verify.csv#300476" in downgraded.evidence_refs

    missing = records["002384"]
    assert missing.source_rating == "—" and missing.final_rating == "—"
    assert missing.first_rejection_stage == "L4_CARD_MISSING"

    maintained = records["301117"]
    assert maintained.source_rating == maintained.final_rating == "Overweight"
    assert maintained.first_rejection_stage is None
    assert maintained.reason == "qualified"


def test_decision_records_match_legacy_final_ratings(published):
    from autoresearch.scan.decision_record import load_decision_records

    records = load_decision_records(published["scan_dir"] / "decision_records.json")
    legacy = json.loads(
        (published["scan_dir"] / "_final_ratings.json").read_text(encoding="utf-8")
    )
    assert {code: record.final_rating for code, record in records.items()} == legacy
```

- [x] **Step 2: Verify RED**

Run:

```bash
uv run --no-sync python -m pytest \
  tests/scan/test_assemble.py::test_decision_records_capture_fold_chain \
  tests/scan/test_assemble.py::test_decision_records_match_legacy_final_ratings -q
```

Expected: FAIL because `decision_records.json` is absent.

- [x] **Step 3: Preserve fold checkpoints in rows**

Immediately after `rows = [...]` in `build_summary()`:

```python
    for r in rows:
        r["_source_rating"] = r.get("rating", "—")
```

After the verify loop:

```python
    for r in rows:
        r["_post_verify_rating"] = r.get("rating", "—")
```

Inside the ensemble loop, before `if not e`:

```python
        r["_ensemble_ratings"] = list((e or {}).get("ratings") or [])
```

- [x] **Step 4: Add deterministic construction and a non-blocking shadow boundary**

Import `sys`, then add after `_dump_final_ratings()`:

```python
def _build_decision_records(
    scan_dir: Path,
    rows: list[dict],
    vmap: dict[str, dict],
    emap: dict[str, dict],
) -> list:
    from autoresearch.scan.decision_record import DecisionRecord
    from autoresearch.scan.stage_result import contract_hash_for

    records = []
    qualified = {"Buy", "Overweight"}
    for row in rows:
        code = str(row.get("code", "")).zfill(6)
        text = _decision_text(scan_dir, code)
        gates = gate_status(text or "")
        gate_states = dict.fromkeys(_GATES3, "UNKNOWN")
        if gates is not None:
            gate_states.update({
                gate: "FAIL" if failed else "PASS"
                for gate, failed in gates.items()
            })
        early = parse_early_stop(text or "")
        source = row.get("_source_rating", "—")
        post_verify = row.get("_post_verify_rating", source)
        final = row.get("rating", "—")
        verify = vmap.get(code)
        ensemble = emap.get(code)

        if final in qualified:
            first_rejection = None
            reason = "qualified"
        elif text is None:
            first_rejection = "L4_CARD_MISSING"
            reason = "card_missing"
        elif early:
            first_rejection = f"L4_{early['phase']}_EARLY_STOP"
            reason = f"early_stop:{early['phase']}:{early['reason']}"
        elif source not in qualified:
            first_rejection = "L4_RUBRIC"
            failed_gates = [gate for gate, state in gate_states.items() if state == "FAIL"]
            reason = "rubric:" + ("|".join(failed_gates) if failed_gates else source)
        elif post_verify not in qualified:
            first_rejection = "VERIFY"
            reason = f"verify:{(verify or {}).get('verdict', 'unknown')}"
        else:
            first_rejection = "ENSEMBLE"
            reason = f"ensemble:{(ensemble or {}).get('median', 'unknown')}"

        refs = [f"finalists.csv#{code}"]
        if text is not None:
            refs.append(f"details/{code}.md")
        if verify:
            refs.append(f"verify.csv#{code}")
        if ensemble:
            per_code = scan_dir / f"_ensemble_{code}.json"
            refs.append(
                per_code.name if per_code.exists() else f"_ensemble.json#{code}"
            )
        records.append(DecisionRecord.build(
            analysis_date=scan_dir.name,
            contract_hash=contract_hash_for(scan_dir),
            code=code,
            source_rating=source,
            rubric_rating=row.get("rubric_suggest") or "—",
            gate_states=gate_states,
            early_stop=early,
            ensemble_ratings=list((ensemble or {}).get("ratings") or []),
            final_rating=final,
            proposal=_PROPOSAL_BY_RATING.get(final, "—"),
            reason=reason,
            evidence_refs=refs,
            first_rejection_stage=first_rejection,
        ))
    return records


def _dump_decision_records(
    scan_dir: Path,
    rows: list[dict],
    vmap: dict[str, dict],
    emap: dict[str, dict],
) -> None:
    from autoresearch.scan.decision_record import safe_write_decision_records

    try:
        records = _build_decision_records(scan_dir, rows, vmap, emap)
    except Exception as exc:
        print(f"[decision_record] 写入失败: {exc}", file=sys.stderr)
        return
    safe_write_decision_records(scan_dir, records)
```

Call it immediately after `_dump_final_ratings(scan_dir, rows)`:

```python
    _dump_decision_records(scan_dir, rows, vmap, emap)
```

Add a regression that monkeypatches `DecisionRecord.build()` to raise and asserts
`build_summary()` still returns the report while stderr records the shadow-write failure.

- [x] **Step 5: Run assemble tests**

Run:

```bash
uv run --no-sync python -m pytest tests/scan/test_assemble.py -q
```

Expected: `66 passed`; existing `_final_ratings.json` assertions remain unchanged.

- [x] **Step 6: Commit**

```bash
git add autoresearch/scan/assemble.py tests/scan/test_assemble.py
git commit -m "feat(scan): shadow-write decision records"
```

### Task 3: Artifact publication and health parity

**Files:**

- Modify: `tests/scan/test_artifacts.py`
- Modify: `tests/scan/test_health.py`
- Modify: `tests/scan/test_assemble.py`
- Modify: `autoresearch/scan/artifacts.py`
- Modify: `autoresearch/scan/health.py`
- Modify: `autoresearch/scan/assemble.py`

- [x] **Step 1: Add failing health parity tests**

Append to `tests/scan/test_health.py`:

```python
def test_decision_records_health_absent_is_advisory(tmp_path):
    d = _mk_day(tmp_path, "2026-07-28")
    result = run_health(d)["decision_records"]
    assert result == {
        "status": "ABSENT",
        "n_records": 0,
        "contract_hash_match": None,
        "final_ratings_match": None,
        "rating_mismatches": [],
        "early_stop_match": None,
        "early_stop_mismatches": [],
        "error": None,
    }


def test_decision_records_health_detects_legacy_rating_mismatch(tmp_path):
    from autoresearch.scan.decision_record import DecisionRecord, write_decision_records

    d = _mk_day(tmp_path, "2026-07-28")
    record = DecisionRecord.build(
        analysis_date=d.name,
        contract_hash=None,
        code="000001",
        source_rating="Hold",
        rubric_rating="Hold",
        gate_states={},
        early_stop=None,
        ensemble_ratings=[],
        final_rating="Hold",
        proposal="HOLD",
        reason="rubric:Hold",
        evidence_refs=[],
        first_rejection_stage="L4_RUBRIC",
    )
    write_decision_records(d, [record])
    (d / "_final_ratings.json").write_text(
        json.dumps({"000001": "Overweight"}),
        encoding="utf-8",
    )
    result = run_health(d)["decision_records"]
    assert result["status"] == "MISMATCH"
    assert result["final_ratings_match"] is False
    assert result["rating_mismatches"] == ["000001"]
```

- [x] **Step 2: Add failing publication assertions**

In `test_artifact_index_is_written_and_published`, add `decision_records` to the names expected
`PRESENT`.

Append:

```python
def test_decision_records_are_published(published):
    staging = published["scan_dir"] / "decision_records.json"
    traced = published["trace"] / "decision_records.json"
    assert traced.read_bytes() == staging.read_bytes()
    manifest = json.loads(
        (published["out_base"] / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["decision_record_schema_version"] == 1
```

- [x] **Step 3: Verify RED**

Run:

```bash
uv run --no-sync python -m pytest \
  tests/scan/test_health.py::test_decision_records_health_absent_is_advisory \
  tests/scan/test_health.py::test_decision_records_health_detects_legacy_rating_mismatch \
  tests/scan/test_assemble.py::test_decision_records_are_published -q
```

Expected: FAIL because health/trace/manifest wiring is absent.

- [x] **Step 4: Register the artifact**

Add to `CRITICAL_ARTIFACTS`:

```python
    ArtifactSpec("decision_records", 1, "assemble", "decision_records.json"),
```

- [x] **Step 5: Implement health parity**

Add before `run_health()`:

```python
def decision_records_health(scan_dir: Path) -> dict:
    from autoresearch.scan.decision_record import load_decision_records

    scan = Path(scan_dir)
    path = scan / "decision_records.json"
    empty = {
        "status": "ABSENT",
        "n_records": 0,
        "contract_hash_match": None,
        "final_ratings_match": None,
        "rating_mismatches": [],
        "early_stop_match": None,
        "early_stop_mismatches": [],
        "error": None,
    }
    if not path.exists():
        return empty
    try:
        records = load_decision_records(path)
    except Exception as exc:
        return {**empty, "status": "INVALID", "error": str(exc)}

    contract = run_contract_health(scan)
    expected_hash = contract.get("contract_hash")
    contract_match = (
        None
        if expected_hash is None
        else all(record.contract_hash == expected_hash for record in records.values())
    )
    legacy_ratings = _json_object(scan / "_final_ratings.json")
    ratings = {code: record.final_rating for code, record in records.items()}
    rating_mismatches = []
    ratings_match = None
    if legacy_ratings is not None:
        rating_mismatches = sorted(
            code for code in set(ratings) | set(legacy_ratings)
            if ratings.get(code) != legacy_ratings.get(code)
        )
        ratings_match = not rating_mismatches

    legacy_early = _json_object(scan / "_early_stop.json")
    early = {
        code: record.early_stop
        for code, record in records.items()
        if record.early_stop is not None
    }
    early_mismatches = []
    early_match = None
    if legacy_early is not None:
        early_mismatches = sorted(
            code for code in set(early) | set(legacy_early)
            if early.get(code) != legacy_early.get(code)
        )
        early_match = not early_mismatches

    mismatch = (
        contract_match is False
        or ratings_match is False
        or early_match is False
    )
    return {
        "status": "MISMATCH" if mismatch else "OK",
        "n_records": len(records),
        "contract_hash_match": contract_match,
        "final_ratings_match": ratings_match,
        "rating_mismatches": rating_mismatches,
        "early_stop_match": early_match,
        "early_stop_mismatches": early_mismatches,
        "error": None,
    }
```

Add to `run_health()`:

```python
            "decision_records": decision_records_health(scan_dir),
```

- [x] **Step 6: Wire manifest, StageResult artifacts and trace**

Import `DECISION_RECORD_SCHEMA_VERSION` next to the ArtifactIndex manifest import and add:

```python
        "decision_record_schema_version": DECISION_RECORD_SCHEMA_VERSION,
```

Add `"decision_records"` to the assemble StageResult artifact list.

Inside the final publication block before ArtifactIndex generation:

```python
        decision_source = scan_dir / "decision_records.json"
        if decision_source.exists():
            shutil.copy2(decision_source, out_base / "trace" / "decision_records.json")
            n_pipe += 1
```

- [x] **Step 7: Run integration regressions**

Run:

```bash
uv run --no-sync python -m pytest \
  tests/scan/test_decision_record.py \
  tests/scan/test_artifacts.py \
  tests/scan/test_health.py \
  tests/scan/test_assemble.py -q
```

Expected: `106 passed`.

- [x] **Step 8: Commit**

```bash
git add \
  autoresearch/scan/artifacts.py \
  autoresearch/scan/health.py \
  autoresearch/scan/assemble.py \
  tests/scan/test_artifacts.py \
  tests/scan/test_health.py \
  tests/scan/test_assemble.py
git commit -m "feat(scan): verify and publish decision records"
```

### Task 4: Documentation and full verification

**Files:**

- Modify: `docs/specs/2026-07-28-scan-market-unified-optimization-master-design.md`
- Modify: `docs/plans/2026-07-28-decision-record-shadow-plan.md`

- [x] **Step 1: Record Wave 1 third-batch status**

Add:

```markdown
#### 2026-07-28 第三批实现状态

已进入影子双写：

- 每只 finalist 固化 DecisionRecord，缺卡票也保留；
- source/rubric/三门/早停/verify/ensemble/final/proposal 可独立查询；
- 首次拒绝阶段进入结构化事实；
- health 对 `_final_ratings.json`、`_early_stop.json` 做 parity；
- DecisionRecord 进入 ArtifactIndex 和 trace。

仍待下一批：

- 将 summary/learning/dossier/retro 消费者逐个切到 DecisionRecord；
- post-run outbox 与 consumer 幂等补跑。
```

将注册表数量更新为 17 类。

- [x] **Step 2: Compile touched modules**

Run:

```bash
uv run --no-sync python -m compileall -q \
  autoresearch/scan/decision_record.py \
  autoresearch/scan/assemble.py \
  autoresearch/scan/artifacts.py \
  autoresearch/scan/health.py
```

Expected: exit 0, no output.

- [x] **Step 3: Run focused regression**

Run:

```bash
uv run --no-sync python -m pytest \
  tests/scan/test_decision_record.py \
  tests/scan/test_assemble.py \
  tests/scan/test_artifacts.py \
  tests/scan/test_health.py \
  tests/learning/test_retro_final_ratings.py \
  tests/dossier/test_delta.py -q
```

Expected: `136 passed`.

- [x] **Step 4: Run full regression**

Run:

```bash
uv run --no-sync python -m pytest -q
```

Expected: `1759 passed, 2 warnings`.

- [x] **Step 5: Verify no consumer migration or selection changes slipped in**

Run:

```bash
git diff 1920564..HEAD -- \
  autoresearch/learning \
  autoresearch/dossier \
  autoresearch/scan/agents/l3_select.py \
  autoresearch/scan/l4_card.py \
  autoresearch/agents/utils/rating.py
```

Expected: no output.

- [x] **Step 6: Verify plan and diff**

Run:

```bash
rg -n "T""BD|T""ODO|implement la""ter|fill in det""ails|appropriate er""ror|Similar t""o" \
  docs/plans/2026-07-28-decision-record-shadow-plan.md
git diff --check
git status --short
```

Expected: placeholder scan has no output and diff check exits 0.

- [x] **Step 7: Mark completed checkboxes and commit docs**

```bash
git add \
  docs/specs/2026-07-28-scan-market-unified-optimization-master-design.md \
  docs/plans/2026-07-28-decision-record-shadow-plan.md
git commit -m "docs(scan): record decision facts shadow layer"
```

## Acceptance criteria

- Every finalist, including missing-card rows, has one DecisionRecord.
- Record and book hashes reject tampering.
- Records are sorted and atomic, with no stale per-code files.
- `source_rating` is captured before verify/ensemble folds.
- `final_rating` exactly matches `_final_ratings.json`.
- Gate states distinguish PASS/FAIL/UNKNOWN.
- Early-stop facts exactly match `_early_stop.json`.
- Verify and ensemble evidence are traceable without parsing summary Markdown.
- `first_rejection_stage` is deterministic and does not alter production decisions.
- Existing consumers remain unchanged.
- ArtifactIndex registers 17 critical artifact types.
- Full test suite passes.
