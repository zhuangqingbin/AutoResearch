# Wave 1 Control-Plane Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the factual control plane by making DecisionRecord the
primary rating read model, adding a stable local outbox with replayable
consumers, and making Workflow/L3/L4 expose and consume StageResult.

**Architecture:** Keep existing CSV/JSON/Markdown outputs as compatibility
views. Add narrow domain adapters (`decision_read_model`, `outbox`,
`post_run`, `stock_stage`) and route existing consumers through them. Workflow
continues orchestrating agents, but status and branch facts come from Python
StageResult snapshots.

**Tech Stack:** Python 3.13 standard library, pandas only where existing
consumers require it, JSON/JSONL-compatible local files, JavaScript Workflow
DSL, pytest, existing RunContract/ArtifactIndex/StageResult/DecisionRecord.

---

## Scope and invariants

- No changes to gate thresholds, rating rubric, finalist cap, or ensemble fold.
- `decision_records.json` is primary when valid.
- `_final_ratings.json` and card parsing remain explicit legacy fallbacks.
- Outbox and consumer failures never block report publication.
- Stable event IDs and successful receipts make replays idempotent.
- Failed consumers can run without rerunning assemble or other consumers.
- A missing per-stock StageResult is visible; it is never inferred as success.

## File map

- Create: `autoresearch/scan/decision_read_model.py`
- Create: `autoresearch/scan/outbox.py`
- Create: `autoresearch/scan/post_run.py`
- Create: `autoresearch/scan/stock_stage.py`
- Create: `tests/scan/test_decision_read_model.py`
- Create: `tests/scan/test_outbox.py`
- Create: `tests/scan/test_post_run.py`
- Create: `tests/scan/test_stock_stage.py`
- Modify: `autoresearch/scan/health.py`
- Modify: `autoresearch/scan/artifacts.py`
- Modify: `autoresearch/scan/assemble.py`
- Modify: `autoresearch/scan/stage_result.py`
- Modify: `autoresearch/learning/retro.py`
- Modify: `autoresearch/dossier/delta.py`
- Modify: `.claude/workflows/scan-market.js`
- Modify: `.claude/workflows/l4-stock.js`
- Modify focused consumer and Workflow tests.

### Task 1: DecisionRecord read model and compatibility ladder

- [x] **Step 1: Write failing read-model tests**

Create `tests/scan/test_decision_read_model.py` with cases that prove:

```python
def test_valid_decision_book_wins_over_legacy_file(tmp_path):
    scan = _scan_with_decision(tmp_path, final_rating="Hold")
    (scan / "_final_ratings.json").write_text(
        '{"000001":"Overweight"}', encoding="utf-8"
    )
    # decision record says Hold; legacy file says Overweight
    assert read_final_ratings(scan) == {"000001": "Hold"}


def test_missing_decision_book_falls_back_to_legacy_json(tmp_path):
    (tmp_path / "_final_ratings.json").write_text(
        '{"000001":"Overweight"}', encoding="utf-8"
    )
    # legacy compatibility for historical scan dates
    assert read_final_ratings(tmp_path) == {"000001": "Overweight"}


def test_invalid_decision_book_is_loud_by_default(tmp_path):
    scan = _scan_with_decision(tmp_path, final_rating="Hold")
    _tamper_decision_book(scan / "decision_records.json")
    with pytest.raises(ValueError, match="decision"):
        read_final_ratings(scan)


def test_optional_card_fallback_is_explicit(tmp_path):
    assert read_final_ratings(
        tmp_path, card_fallback=lambda _: {"000001": "Sell"}
    ) == {"000001": "Sell"}
```

- [x] **Step 2: Verify RED**

Run:

```bash
uv run --no-sync python -m pytest tests/scan/test_decision_read_model.py -q
```

Expected: collection error because `decision_read_model` does not exist.

- [x] **Step 3: Implement the read model**

Create `autoresearch/scan/decision_read_model.py` with:

```python
def read_decisions(scan_dir: Path | str) -> dict[str, DecisionRecord]:
    return load_decision_records(Path(scan_dir) / "decision_records.json")


def read_final_ratings(
    scan_dir: Path | str,
    *,
    card_fallback: Callable[[Path], dict[str, str]] | None = None,
) -> dict[str, str]:
    scan = Path(scan_dir)
    decision_path = scan / "decision_records.json"
    if decision_path.exists():
        return {
            code: record.final_rating
            for code, record in read_decisions(scan).items()
        }
    legacy = scan / "_final_ratings.json"
    if legacy.exists():
        try:
            raw = json.loads(legacy.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = None
        if isinstance(raw, dict) and raw:
            return {
                str(code).zfill(6): str(rating)
                for code, rating in raw.items()
            }
    return card_fallback(scan) if card_fallback is not None else {}
```

An existing-but-invalid DecisionRecord must raise. It must not silently fall
through to a contradictory legacy fact. A missing, empty, or malformed legacy
file retains the historical card fallback.

- [x] **Step 4: Route final-rating consumers**

Refactor `health.final_ratings()` so its existing card/verify/ensemble
calculation becomes `_legacy_final_ratings()`, then call:

```python
return read_final_ratings(scan_dir, card_fallback=_legacy_final_ratings)
```

Change `retro._buylist()`, `dossier.delta.record_scan_deltas()`, and
`stage_eval` final-rating input to use the same read model. Preserve report-card
parsing only for dates where neither DecisionRecord nor a valid non-empty legacy
JSON object exists.

- [x] **Step 5: Verify focused consumer parity**

Run:

```bash
uv run --no-sync python -m pytest \
  tests/scan/test_decision_read_model.py \
  tests/scan/test_health.py \
  tests/learning/test_retro_final_ratings.py \
  tests/dossier/test_delta.py -q
```

Expected: all selected tests pass, including contradictory legacy fixtures
where DecisionRecord must win.

- [x] **Step 6: Commit**

```bash
git add autoresearch/scan/decision_read_model.py autoresearch/scan/health.py \
  autoresearch/learning/retro.py autoresearch/dossier/delta.py \
  tests/scan/test_decision_read_model.py tests/scan/test_health.py \
  tests/learning/test_retro_final_ratings.py tests/dossier/test_delta.py
git commit -m "feat(scan): read final ratings from decision facts"
```

### Task 2: Stable local outbox domain

- [x] **Step 1: Write failing event/book tests**

Create `tests/scan/test_outbox.py` covering:

```python
def test_event_id_is_semantic_and_stable():
    args = {
        "event_type": "RUN_FINALIZED",
        "analysis_date": "2026-07-28",
        "run_id": "run-1",
        "contract_hash": "a" * 64,
        "aggregate_id": "run-1",
        "payload": {"n_decisions": 2},
    }
    left = OutboxEvent.build(
        **args, created_at="2026-07-28T10:00:00Z"
    )
    right = OutboxEvent.build(
        **args, created_at="2026-07-28T11:00:00Z"
    )
    assert left.event_id == right.event_id


def test_emit_is_atomic_sorted_and_idempotent(tmp_path):
    path = emit_events(tmp_path, [event_b, event_a])
    before = path.read_bytes()
    emit_events(tmp_path, [event_a, event_b])
    assert path.read_bytes() == before


def test_load_rejects_tampered_payload(tmp_path):
    with pytest.raises(ValueError, match="hash"):
        load_events(tampered_path)
```

Legal types are exactly:

```python
RUN_FINALIZED
DECISION_FINALIZED
GATE_FAILED
EARLY_STOPPED
DOSSIER_DELTA_READY
```

- [x] **Step 2: Verify RED**

Run:

```bash
uv run --no-sync python -m pytest tests/scan/test_outbox.py -q
```

Expected: collection error because `outbox` does not exist.

- [x] **Step 3: Implement event integrity and atomic book**

Create `autoresearch/scan/outbox.py`:

```python
OUTBOX_SCHEMA_VERSION = 1
EVENT_SCHEMA_VERSION = 1
EVENT_TYPES = {
    "RUN_FINALIZED", "DECISION_FINALIZED", "GATE_FAILED",
    "EARLY_STOPPED", "DOSSIER_DELTA_READY",
}


@dataclass(frozen=True)
class OutboxEvent:
    schema_version: int
    event_id: str
    event_type: str
    analysis_date: str
    run_id: str | None
    contract_hash: str | None
    aggregate_id: str
    payload: dict
    created_at: str
    event_hash: str
```

`event_id` hashes type/date/run/aggregate/canonical payload, excluding
`created_at`; `event_hash` covers the full persisted event. Store the atomic
book at `outbox/events.json`, sorted by `(event_type, aggregate_id, event_id)`.
Merging the same semantic event preserves the first persisted bytes.

- [x] **Step 4: Build events from final facts**

Implement:

```python
def build_finalization_events(scan_dir: Path | str) -> list[OutboxEvent]:
    # one RUN_FINALIZED
    # one DECISION_FINALIZED per DecisionRecord
    # one EARLY_STOPPED per record with early_stop
    # one DOSSIER_DELTA_READY per non-dash final decision
    # one GATE_FAILED per FAILED gate StageResult
```

Read identity from RunContract, decisions from DecisionRecord, and failed gates
from StageResult. Missing optional inputs omit only the dependent event and
return explicit warnings to the caller; invalid present inputs raise.

- [x] **Step 5: Verify and commit**

Run:

```bash
uv run --no-sync python -m pytest tests/scan/test_outbox.py -q
```

Then:

```bash
git add autoresearch/scan/outbox.py tests/scan/test_outbox.py
git commit -m "feat(scan): add stable post-run outbox"
```

### Task 3: Idempotent consumer receipts and replay CLI

- [x] **Step 1: Write failing receipt/runner tests**

Create `tests/scan/test_post_run.py` proving:

```python
def test_successful_consumer_is_not_called_twice(tmp_path):
    assert run_consumers(tmp_path, registry={"journal": handler}).succeeded == 1
    assert run_consumers(tmp_path, registry={"journal": handler}).skipped == 1
    assert calls == ["event-1"]


def test_failed_consumer_can_be_retried_alone(tmp_path):
    first = run_consumers(tmp_path, registry={"journal": failing})
    second = run_consumers(
        tmp_path, registry={"journal": succeeding},
        only={"journal"}, retry_failed=True,
    )
    assert first.failed == 1 and second.succeeded == 1


def test_one_consumer_failure_does_not_block_another(tmp_path):
    result = run_consumers(
        tmp_path, registry={"journal": failing, "dossier": succeeding}
    )
    assert result.failed == 1 and result.succeeded == 1
```

- [x] **Step 2: Verify RED**

Run:

```bash
uv run --no-sync python -m pytest tests/scan/test_post_run.py -q
```

Expected: collection error because `post_run` does not exist.

- [x] **Step 3: Implement receipt store and runner**

Create `autoresearch/scan/post_run.py` with immutable receipt snapshots:

```python
@dataclass(frozen=True)
class ConsumerReceipt:
    event_id: str
    consumer: str
    status: Literal["SUCCEEDED", "FAILED", "SKIPPED"]
    attempts: int
    error: str | None
    updated_at: str
    receipt_hash: str
```

Persist atomic `outbox/consumer_state.json`. Define explicit subscriptions so
unsubscribed event/consumer pairs are not backlog:

```python
SUBSCRIPTIONS = {
    "RUN_FINALIZED": {
        "journal", "buy_ledger", "zero_buy_ledger", "paper_nav",
        "gate_ledger", "earlystop_ledger", "pinned_ledger", "precedents",
    },
    "DOSSIER_DELTA_READY": {"dossier_delta"},
}
```

`DECISION_FINALIZED`, `GATE_FAILED`, and `EARLY_STOPPED` remain factual events
without a batch-ledger subscription; this avoids rerunning a full-date ledger
once per stock. The default registry wraps existing idempotent functions.
Handler exceptions become FAILED receipts and do not escape
`safe_run_consumers()`.

- [x] **Step 4: Add replay/status CLI**

Support:

```bash
uv run --no-sync python -m autoresearch.scan.post_run 2026-07-28 status
uv run --no-sync python -m autoresearch.scan.post_run 2026-07-28 run
uv run --no-sync python -m autoresearch.scan.post_run 2026-07-28 run \
  --consumer dossier_delta --retry-failed
```

Status prints deterministic JSON and exits nonzero only for corrupt control
files, not for ordinary pending/failed consumer business state.

- [x] **Step 5: Verify and commit**

Run:

```bash
uv run --no-sync python -m pytest tests/scan/test_post_run.py -q
```

Then commit:

```bash
git add autoresearch/scan/post_run.py tests/scan/test_post_run.py
git commit -m "feat(scan): add replayable post-run consumers"
```

### Task 4: Assemble, ArtifactIndex, trace, and health integration

- [x] **Step 1: Write failing integration tests**

Extend assemble/artifact/health tests to assert:

```python
assert (scan / "outbox/events.json").exists()
assert (scan / "outbox/consumer_state.json").exists()
assert (trace / "outbox/events.json").read_bytes() == \
       (scan / "outbox/events.json").read_bytes()
assert artifact_rows["outbox_events"]["status"] == "PRESENT"
assert artifact_rows["consumer_state"]["status"] == "PRESENT"
assert health["post_run"]["pending"] >= 0
assert health["post_run"]["failed_consumers"] == []
```

Add corruption and failed-receipt tests expecting `INVALID` and `BACKLOG`.

- [x] **Step 2: Verify RED**

Run the named new tests and confirm they fail because integration is absent.

- [x] **Step 3: Wire non-blocking finalization**

Move the existing `is_real` calculation before post-run dispatch. After gate4
StageResult is written, call:

```python
if safe_emit_finalization_events(scan_dir) is not None:
    initialize_consumer_state(scan_dir)
    if is_real:
        safe_run_consumers(scan_dir)
```

Register:

```python
ArtifactSpec("outbox_events", 1, "post_run", "outbox/events.json")
ArtifactSpec("consumer_state", 1, "post_run", "outbox/consumer_state.json")
```

Copy the outbox directory to trace before the final ArtifactIndex snapshot.
Add `post_run_health()` with `ABSENT / OK / BACKLOG / INVALID`.

- [x] **Step 4: Verify integration**

Run:

```bash
uv run --no-sync python -m pytest \
  tests/scan/test_outbox.py tests/scan/test_post_run.py \
  tests/scan/test_artifacts.py tests/scan/test_health.py \
  tests/scan/test_assemble.py -q
```

- [x] **Step 5: Commit**

```bash
git add autoresearch/scan/assemble.py autoresearch/scan/artifacts.py \
  autoresearch/scan/health.py tests/scan/test_artifacts.py \
  tests/scan/test_health.py tests/scan/test_assemble.py
git commit -m "feat(scan): publish post-run control state"
```

### Task 5: StageResult read CLI and Workflow branching

- [x] **Step 1: Write failing CLI tests**

Extend `tests/scan/test_stage_result.py`:

```python
def test_show_cli_returns_verified_snapshot(tmp_path, capsys):
    rc = main(["show", str(tmp_path), "gate1"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["status"] == "SUCCEEDED"


def test_show_cli_fails_for_missing_or_corrupt_snapshot(tmp_path, capsys):
    assert main(["show", str(tmp_path), "gate1"]) == 2
```

Add Workflow anchor tests requiring `stage-result`/`status`/`metrics` and
forbidding direct branching on duplicated gate business fields.

- [x] **Step 2: Verify RED**

Run the new StageResult CLI and Workflow anchor tests.

- [x] **Step 3: Implement StageResult CLI**

Add an argv-testable `main(argv=None)` to `stage_result.py`:

```bash
python -m autoresearch.scan.stage_result show <scan-dir> <stage>
```

It emits the verified `StageResult.to_dict()` as the final stdout line. Missing,
invalid, or contract-mismatched snapshots emit structured error JSON and exit
2.

- [x] **Step 4: Change Workflow gate adapter**

In `scan-market.js`, execute the existing deterministic gate command, then read
the StageResult snapshot and branch on:

```javascript
stage.status === 'SUCCEEDED'
stage.metrics.sentinel_level
stage.metrics.finalists
stage.error
```

The Python gate remains the only owner of gate rules. Keep legacy gate stdout
for compatibility/debugging, but do not use it as the Workflow branch source.

- [x] **Step 5: Verify Workflow**

Run:

```bash
uv run --no-sync python -m pytest \
  tests/scan/test_stage_result.py tests/scan/test_gates.py \
  tests/scan/test_workflow_syntax.py tests/test_workflow_js_syntax.py \
  tests/test_agent_defs.py -q
```

- [x] **Step 6: Commit**

```bash
git add autoresearch/scan/stage_result.py .claude/workflows/scan-market.js \
  tests/scan/test_stage_result.py tests/scan/test_workflow_syntax.py \
  tests/test_agent_defs.py
git commit -m "feat(scan): branch workflow on stage results"
```

### Task 6: Per-stock L3/L4 StageResult

- [ ] **Step 1: Write failing stock-stage tests**

Create `tests/scan/test_stock_stage.py`:

```python
def test_record_l3_writes_one_result_per_judged_stock(tmp_path):
    scan = _write_judged_fixture(tmp_path)
    paths = record_l3_results(scan)
    assert load_stage_result(scan / "stage_results/l3_000001.json").metrics[
        "selected"
    ] is True


def test_record_l4_success_captures_card_and_ensemble(tmp_path):
    scan = _write_l4_fixture(tmp_path, with_card=True, with_ensemble=True)
    result = record_l4_result(scan, "000001")
    assert result.status == "SUCCEEDED"
    assert result.metrics["provisional_rating"] == "Overweight"


def test_record_l4_missing_card_is_failed_not_inferred_success(tmp_path):
    scan = _write_l4_fixture(tmp_path, with_card=False, with_ensemble=False)
    result = record_l4_result(scan, "000001")
    assert result.status == "FAILED"
```

- [ ] **Step 2: Verify RED**

Run:

```bash
uv run --no-sync python -m pytest tests/scan/test_stock_stage.py -q
```

- [ ] **Step 3: Implement stock result writers**

Create `autoresearch/scan/stock_stage.py`:

```python
def record_l3_results(scan_dir: Path | str) -> list[Path]:
    # L3_judged_full.csv -> l3_<code>
    # metrics: selected, lane, conviction, fragility


def record_l4_result(
    scan_dir: Path | str, code: str, *, error: str | None = None
) -> StageResult:
    # details/<code>.md plus optional _ensemble_<code>.json
    # metrics: card_present, early_stop, source/provisional rating,
    # ensemble_present; DecisionRecord remains the only final-rating source
```

Use existing parsers/read models; do not reimplement rating or early-stop rules.

- [ ] **Step 4: Wire producers and Workflow**

- After deterministic finalists writing, call `record_l3_results()`.
- Record already-reused L4 cards during dispatch-plan generation with
  `metrics.reused=true`.
- Add CLI `stock_stage l4 <date> <code>`.
- In `l4-stock.js`, wrap the card path so the recorder runs on both success and
  failure; rethrow the original failure after recording FAILED.
- A completed stock may be retried independently; semantic StageResult
  idempotency preserves identical snapshots.

- [ ] **Step 5: Verify**

Run:

```bash
uv run --no-sync python -m pytest \
  tests/scan/test_stock_stage.py tests/scan/test_finalists_writer.py \
  tests/scan/test_workflow_syntax.py tests/test_workflow_js_syntax.py \
  tests/test_agent_defs.py -q
```

- [ ] **Step 6: Commit**

```bash
git add autoresearch/scan/stock_stage.py \
  autoresearch/scan/agents/l3_select.py .claude/workflows/l4-stock.js \
  tests/scan/test_stock_stage.py tests/scan/test_finalists_writer.py \
  tests/scan/test_workflow_syntax.py tests/test_agent_defs.py
git commit -m "feat(scan): record per-stock stage outcomes"
```

### Task 7: Wave 1 final parity and documentation

- [ ] **Step 1: Compile touched Python modules**

```bash
uv run --no-sync python -m compileall -q \
  autoresearch/scan/decision_read_model.py autoresearch/scan/outbox.py \
  autoresearch/scan/post_run.py autoresearch/scan/stock_stage.py \
  autoresearch/scan/stage_result.py autoresearch/scan/assemble.py \
  autoresearch/scan/health.py
```

- [ ] **Step 2: Run Wave 1 focused regression**

Run all newly added tests plus existing assemble, health, artifact, retro,
dossier, ledger, StageResult, gate, and Workflow tests.

- [ ] **Step 3: Run the full suite**

```bash
uv run --no-sync python -m pytest -q
```

- [ ] **Step 4: Verify invariant-sensitive diffs**

Confirm no changes to production thresholds/rubrics:

```bash
git diff 5452769..HEAD -- \
  autoresearch/scan/config.py autoresearch/scan/recall \
  autoresearch/agents/utils/rating.py
```

Workflow changes may alter status plumbing only; finalists and final-rating
golden tests must remain green.

- [ ] **Step 5: Update implementation status and commit docs**

Update the master design with Wave 1 fourth-batch status, ArtifactIndex count,
consumer fallback policy, and remaining Wave 2 work. Mark every completed
checkbox in this plan.

```bash
git add docs/specs/2026-07-28-scan-market-unified-optimization-master-design.md \
  docs/plans/2026-07-28-wave1-control-plane-completion-plan.md \
  docs/plans/2026-07-28-unified-optimization-completion-program.md
git commit -m "docs(scan): complete wave1 control plane"
```

## Acceptance criteria

- DecisionRecord is primary for final-rating consumers.
- Invalid present DecisionRecord is loud; historical absence has a tested
  fallback.
- Stable outbox events and receipt hashes reject tampering.
- Successful consumer/event pairs run once; failed pairs can replay alone.
- Report publication survives any learning consumer failure.
- Health exposes pending and failed consumer debt.
- Workflow branches on verified StageResult.
- L3/L4 stock outcomes are independently recorded and retryable.
- No production selection, rating, or BUY-frequency behavior changes.
- Full test suite passes.
