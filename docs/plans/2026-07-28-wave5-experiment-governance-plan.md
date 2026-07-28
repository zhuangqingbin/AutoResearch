# Wave 5 Experiment Promotion and Rollback Implementation Plan

> **Status:** In progress on 2026-07-28.
>
> **Execution:** Directly on `main`, explicitly authorized by the user. Every
> behavior follows `superpowers:test-driven-development`; no current challenger
> is promoted merely because the governance software exists.

**Goal:** Preregister every research challenger, evaluate it against the five
independent guards, require explicit human approval before activation, and
retain a stable rollback baseline throughout the observation window.

**Architecture:** A separate local JSON registry owns experiment lifecycle and
immutable definition hashes. Promotion consumes explicit measurement facts and
can only recommend. Approval and activation are distinct, audited transitions.
Rollback watch consumes post-activation observations and can only recommend
rollback; applying rollback or accepting a new stable baseline remains an
explicit human action.

## Architecture decision

### Context

`feedback_store` currently owns lessons/proposals, but proposals are informal
work items rather than preregistered trials. Extending it would mix knowledge
curation with production lifecycle control. A database/service would add
operational complexity without a multi-writer requirement.

### Decision

Use three deterministic modules over atomic, schema-versioned local JSON:

- `experiment_registry.py`: identity, immutable definition, transitions,
  approval audit, active experiment, stable baseline/history.
- `promotion.py`: maturity and five-guard evaluation.
- `rollback_watch.py`: bounded post-activation observations and recommendation.

### Consequences

- Positive: replayable, diffable, no network/runtime dependency, explicit
  authority boundary, prior baseline always addressable.
- Negative: single-writer file semantics and manual candidate metric adapters.
  This is acceptable for the current local, single-operator project.
- Rejected: embedding lifecycle in `feedback_store` (mixed responsibilities);
  SQLite/service (unneeded coordination and operational overhead); automatic
  config mutation (violates human approval and rollback safety).

## State model

```text
PREREGISTERED
  └─ evaluation PASS → RECOMMENDED
       └─ explicit approve → APPROVED
            └─ explicit activate → ACTIVE
                 ├─ window PASS → STABLE_CANDIDATE
                 │    └─ explicit accept → stable baseline
                 └─ any guard breach → ROLLBACK_RECOMMENDED
                      └─ explicit rollback → ROLLED_BACK
```

`IMMATURE`, `UNKNOWN`, and `FAIL` evaluation results never activate anything.
The registry does not edit scan config, weights, gates, prompts, or ratings.

## Task 1: Registry identity and stable baseline

- [ ] Write failing schema/hash/idempotency tests.
- [ ] Add atomic canonical JSON read/write and corruption fail-loud behavior.
- [ ] Require a stable baseline before experiment registration.
- [ ] Preregister immutable definition hash, start/expiry, trial family,
  primary metric, five promotion guards, five rollback guards, challenger
  pointer, sample minima, rollback window, and stable rollback pointer.
- [ ] Reject duplicate IDs with a changed definition; accept exact replay.
- [ ] Add stable-baseline history so prior pointers remain addressable.
- [ ] Commit registry independently.

## Task 2: Five-guard promotion evaluation

- [ ] Write failing maturity/UNKNOWN/FAIL/PASS tests.
- [ ] Require minimum forward days, mature events, unique events, and regimes.
- [ ] Evaluate research, decision, token, speed, and architecture separately.
- [ ] Keep missing metrics `UNKNOWN`, never reinterpret them as `FAIL`.
- [ ] Require at least one explicit improvement and no guard breach.
- [ ] Disclose trial count and definition breakpoints.
- [ ] Persist canonical evaluation and update only recommendation state.
- [ ] Commit evaluator independently.

## Task 3: Human approval and activation boundary

- [ ] Write failing illegal-transition tests.
- [ ] `approve` requires `RECOMMENDED`, approver identity, timestamp, and note.
- [ ] `activate` requires an unexpired explicit approval and no other active
  experiment in the same trial family.
- [ ] Activation snapshots the rollback pointer and never overwrites the stable
  baseline.
- [ ] Reject any API/CLI attempt to activate directly from shadow states.
- [ ] Commit transition boundary independently.

## Task 4: Rollback observation

- [ ] Write failing observation-window and breach tests.
- [ ] Accept observations only for `ACTIVE` experiments.
- [ ] Evaluate the same five domains; missing observations stay `UNKNOWN`.
- [ ] Any observed breach emits `ROLLBACK_RECOMMENDED` with guard evidence and
  the stable rollback pointer; it does not mutate production automatically.
- [ ] A clean complete window emits `STABLE_CANDIDATE`.
- [ ] Explicit rollback and explicit stable-baseline acceptance require human
  identity and preserve audit history.
- [ ] Commit rollback watch independently.

## Task 5: Deterministic CLI and report

- [ ] Add registry CLI for baseline/register/show/list/approve/activate/
  rollback/accept/report.
- [ ] Add promotion and rollback-watch evaluate/observe CLIs with JSON input and
  optional canonical JSON output.
- [ ] Render `reports/learning/experiments.md` with stable baseline, lifecycle,
  latest five-guard verdicts, maturity, approval, rollback pointer, and audit.
- [ ] Add source-contract and end-to-end CLI tests.
- [ ] Commit integration independently.

## Task 6: Documentation and final acceptance

- [ ] Update completion program, master design, SKILL, and STAGES.
- [ ] Compile all Python modules and import-probe the package.
- [ ] Run focused experiment-governance tests.
- [ ] Prove rating/gate/recall/horizon and production Workflow parity.
- [ ] Run the full suite and record exact count/warnings.
- [ ] Mark software complete while leaving every unapproved/immature challenger
  shadow-only.

## Rollback

Each transition is append-audited and the stable baseline is never overwritten
by activation. Code rollback is per-task commit; production rollback is an
explicit audited transition to the stored rollback pointer.
