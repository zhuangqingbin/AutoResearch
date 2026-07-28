# Unified Optimization Completion Program

> **Design authority:** `docs/specs/2026-07-28-scan-market-unified-optimization-master-design.md`
>
> **Execution mode:** Directly on `main`, explicitly authorized by the user.
>
> **Completion meaning:** All software contracts, shadow measurements, replay
> paths, promotion gates, rollback mechanisms, module boundaries, and tests are
> implemented. Forward-looking challengers remain shadow-only until their real
> sample gates mature; software completion must not fabricate future evidence.

## Non-negotiable invariants

- Primary outcome horizon remains `fwd_2_oc`.
- Zero BUY remains a valid result; no BUY quota or “minimum BUY frequency” is
  introduced.
- Production gate thresholds and L3/L4 ratings do not change during
  observability, migration, or refactoring waves.
- `UNKNOWN` is not counted as `FAIL`.
- Multi-gate rejection is one multi-gate sample, not three single-gate samples.
- Existing artifacts remain readable until every consumer migration has parity
  tests and a rollback path.
- Every behavior change follows TDD and lands as an atomic commit.

## Dependency map

```text
Wave 1 facts/control completion
  ├─ DecisionRecord read model
  ├─ local outbox + consumer receipts/replay
  ├─ Workflow reads StageResult
  └─ per-stock L3/L4 StageResult
          ↓
Wave 2 abstention/research attribution
  ├─ candidate first-death facts
  ├─ daily abstention verdict
  ├─ unique gate / audit basket ledgers
  ├─ early-stop shadow ledger
  └─ ensemble fold ledger
          ↓
Wave 3 token and critical-path controls
  ├─ complete usage/cost accounting
  ├─ byte-stable shared context contracts
  ├─ bounded concurrency/presence gates
  └─ streaming L4 orchestration
          ↓
Wave 4 module-boundary extraction
  ├─ decision finalize/read model
  ├─ report sections/publisher/post-run
  ├─ L3 evidence/triage/validation/merge
  └─ L4 context/rubric/parsers/prompts/dispatch
          ↓
Wave 5 experiment governance
  ├─ ExperimentRegistry
  ├─ five-guard evaluation
  ├─ human approval boundary
  └─ rollback observation and stable baseline pointer
```

## Wave deliverables and exit evidence

### Wave 1 — facts/control completion

Detailed plan:
`docs/plans/2026-07-28-wave1-control-plane-completion-plan.md`.

Status: **completed 2026-07-28**. ArtifactIndex registers 19 critical
artifact classes; the full-suite acceptance run is recorded in the detailed
plan (`1798 passed`, with two pre-existing pandas FutureWarnings).

Exit evidence:

- DecisionRecord is the primary final-rating read path with explicit legacy
  fallback.
- Assemble emits stable local events and initializes consumer expectations.
- Consumer receipts are idempotent; failed consumers can be retried alone.
- Health reports invalid events, failed consumers, and pending backlog.
- Workflow branches consume StageResult status/metrics instead of duplicating
  gate interpretation.
- Each L3/L4 stock has an independently inspectable StageResult.

### Wave 2 — abstention and research attribution

Detailed plan:
`docs/plans/2026-07-28-wave2-abstention-research-attribution-plan.md`.

Status: **software completed 2026-07-28**. ArtifactIndex now registers 26
finite artifact classes; full-suite acceptance is `1854 passed` with the same
two pre-existing pandas `FutureWarning`s. All new outcome cohorts remain
shadow/measurement-only until their explicit forward-sample gates mature.

Implemented components:

- `autoresearch/learning/rejection_attribution.py`
- `autoresearch/learning/abstention_ledger.py`
- `autoresearch/learning/l3_audit_ledger.py`
- `autoresearch/learning/earlystop_shadow.py`
- `autoresearch/learning/ensemble_ledger.py`

Exit evidence:

- Every mature 0-BUY date is classified as `CORRECT`, `FALSE`, or `NEUTRAL`;
  immature dates are `IMMATURE`, not silently omitted.
- Every evaluated candidate has exactly one first-death stage.
- False-abstention threshold is market-relative `+2pp`, next-open tradable, and
  T+2 mature.
- Audit-basket, early-stop-shadow, and ensemble ledgers are separate from
  production rating behavior and production token baselines.
- `RETRO_FINALIZED` makes all Wave 2 reports independently replayable and
  health-observable without rerunning price attribution.
- No L3/L4 selection, gate, rating, BUY quota, or production Workflow behavior
  changed. Sample maturity is not claimed by software completion.

### Wave 3 — token and speed controls

Detailed plan:
`docs/plans/2026-07-28-wave3-token-critical-path-plan.md`.

Status: **software completed 2026-07-28**. Full-suite acceptance is
`1912 passed`, with the same two pre-existing pandas `FutureWarning`s.
Cost/latency effectiveness remains `IMMATURE` until ten real scans exist; unit
tests prove the measurement and promotion contracts, not the ≥15%/≥25% cost or
P50/P90 targets.

Implemented components:

- Extend `autoresearch/trace/usage_harvest.py` with main-session, output-price,
  retry, failure, and discarded-call accounting.
- Add `autoresearch/scan/budget.py` for per-stage cost/wall/concurrency budgets.
- Add `autoresearch/scan/context_blocks.py` for schema-bound, byte-stable shared
  market/sector/dossier blocks.
- Make `.claude/workflows/scan-market.js` and `l4-stock.js` use presence-gated,
  independently retryable stock tasks.
- Add post-run cost/latency observations with honest `UNMEASURED` and
  `IMMATURE` states.

Exit evidence:

- Cost reports distinguish main session, workflow subagents, failed/retried
  calls, cache read/write, input, and output.
- Budget violations become StageResult warnings/degradation, never hidden
  truncation.
- A stock failure does not require rerunning completed stocks.
- Latency/cost promotion remains blocked until at least ten real scans establish
  P50/P90 and median cost; tests only verify the measurement and gate logic.
- Production rating, gate, recall scoring, `fwd_2_oc`, and BUY-count behavior
  are unchanged from the Wave 2 completion baseline.

### Wave 4 — module boundaries

Detailed plan:
`docs/plans/2026-07-28-wave4-module-boundaries-plan.md`.

Status: **software completed 2026-07-28**. Full-suite acceptance is
`1918 passed`, with the same two pre-existing pandas `FutureWarning`s.
All 162 `autoresearch` modules import without a cycle/failure.

Implemented extractions preserve compatibility imports:

- `scan/decision_finalize.py`
- `scan/decision_read_model.py`
- `scan/report_sections.py`
- `scan/publisher.py`
- `scan/post_run.py`
- `scan/l3/{evidence,triage,prompt,validation,merge}.py`
- `scan/l4/{context,rubric,producers,prompts,dispatch,parsers}.py`

Exit evidence:

- `assemble.py`, `agents/l3_select.py`, and `agents/l4_card.py` become adapters
  rather than mixed-responsibility owners.
- Golden summary, finalists, rating, and prompt contracts remain byte/semantic
  compatible.
- Domain modules do not import reporting; Workflow contains no rating/gate
  business rules.
- The three legacy entrypoints are 99, 101, and 142 lines respectively and
  contain only compatibility exports plus their CLI dispatch.
- Eight critical rubric/finalization functions are AST-equivalent to the
  pre-extraction baseline; production rating/gate/recall files have no diff.

### Wave 5 — experiment promotion and rollback

Planned components:

- `autoresearch/learning/experiment_registry.py`
- `autoresearch/learning/promotion.py`
- `autoresearch/learning/rollback_watch.py`
- deterministic CLI/report integration.

Exit evidence:

- Each challenger is preregistered with definition hash, start date, expiry,
  trial family, primary metric, guard thresholds, and rollback pointer.
- Insufficient samples/regimes return `IMMATURE`.
- Software may recommend promotion, but production activation requires explicit
  human approval.
- An activated experiment that breaches a guard during its observation window
  emits a rollback recommendation and keeps the prior baseline addressable.

## Final verification

For every wave:

1. compile touched Python modules;
2. run focused contract/parity tests;
3. run Workflow `AsyncFunction` syntax probes when JavaScript changes;
4. run the full test suite;
5. confirm no unintended gate, rating, horizon, or BUY-quota diff;
6. update the master design implementation status;
7. leave `main` clean and do not push without an explicit request.
