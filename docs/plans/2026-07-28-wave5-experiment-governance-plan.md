# Wave 5 Experiment Promotion and Rollback Implementation Plan

> **Status:** Software completed on 2026-07-28. Real challenger evidence
> remains immature/shadow-only.
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

- [x] Write failing schema/hash/idempotency tests.
- [x] Add atomic canonical JSON read/write and corruption fail-loud behavior.
- [x] Require a stable baseline before experiment registration.
- [x] Preregister immutable definition hash, start/expiry, trial family,
  primary metric, five promotion guards, five rollback guards, challenger
  pointer, sample minima, rollback window, and stable rollback pointer.
- [x] Reject duplicate IDs with a changed definition; accept exact replay.
- [x] Add stable-baseline history so prior pointers remain addressable.
- [x] Commit registry independently.

## Task 2: Five-guard promotion evaluation

- [x] Write failing maturity/UNKNOWN/FAIL/PASS tests.
- [x] Require minimum forward days, mature events, unique events, and regimes.
- [x] Evaluate research, decision, token, speed, and architecture separately.
- [x] Keep missing metrics `UNKNOWN`, never reinterpret them as `FAIL`.
- [x] Require at least one explicit improvement and no guard breach.
- [x] Disclose trial count and definition breakpoints.
- [x] Persist canonical evaluation and update only recommendation state.
- [x] Commit evaluator independently.

## Task 3: Human approval and activation boundary

- [x] Write failing illegal-transition tests.
- [x] `approve` requires `RECOMMENDED`, approver identity, timestamp, and note.
- [x] `activate` requires an unexpired explicit approval and no other active
  experiment in the same trial family.
- [x] Activation snapshots the rollback pointer and never overwrites the stable
  baseline.
- [x] Reject any API/CLI attempt to activate directly from shadow states.
- [x] Commit transition boundary independently.

## Task 4: Rollback observation

- [x] Write failing observation-window and breach tests.
- [x] Accept observations only for `ACTIVE` experiments.
- [x] Evaluate the same five domains; missing observations stay `UNKNOWN`.
- [x] Any observed breach emits `ROLLBACK_RECOMMENDED` with guard evidence and
  the stable rollback pointer; it does not mutate production automatically.
- [x] A clean complete window emits `STABLE_CANDIDATE`.
- [x] Explicit rollback and explicit stable-baseline acceptance require human
  identity and preserve audit history.
- [x] Commit rollback watch independently.

## Task 5: Deterministic CLI and report

- [x] Add registry CLI for baseline/register/show/list/approve/activate/
  rollback/accept/report.
- [x] Add promotion and rollback-watch evaluate/observe CLIs with JSON input and
  optional canonical JSON output.
- [x] Render `reports/learning/experiments.md` with stable baseline, lifecycle,
  latest five-guard verdicts, maturity, approval, rollback pointer, and audit.
- [x] Add source-contract and end-to-end CLI tests.
- [x] Commit integration independently.

## Task 6: Documentation and final acceptance

- [x] Update completion program, master design, SKILL, and STAGES.
- [x] Compile all Python modules and import-probe the package.
- [x] Run focused experiment-governance tests.
- [x] Prove rating/gate/recall/horizon and production Workflow parity.
- [x] Run the full suite and record exact count/warnings.
- [x] Mark software complete while leaving every unapproved/immature challenger
  shadow-only.

## Acceptance record

- Focused governance and source-contract tests: `34 passed`.
- Full suite: `1952 passed, 2 warnings`; both warnings are the pre-existing
  pandas `FutureWarning`s in L3 merge and temperature upsert tests.
- Python compile succeeded; all 165 `autoresearch` modules import with zero
  failures.
- Both production Workflow files pass the repository's V8 `AsyncFunction`
  probe and its mutation tests (`18 passed`).
- Relative to the Wave 4 baseline `6a06bc8`, production rating, gates, recall
  scoring, `fwd_2_oc`, and both production Workflow files have no diff.
- No live registry, challenger approval, production activation, baseline
  acceptance, rollback, BUY quota, or threshold relaxation was created during
  software acceptance.

## Rollback

Each transition is append-audited and the stable baseline is never overwritten
by activation. Code rollback is per-task commit; production rollback is an
explicit audited transition to the stored rollback pointer.
