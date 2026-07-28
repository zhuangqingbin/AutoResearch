# Wave 4 Module-Boundary Extraction Implementation Plan

> **Status:** In progress on 2026-07-28.
>
> **Execution:** Directly on `main`, explicitly authorized by the user. Apply
> `superpowers:test-driven-development` to every extraction and preserve the old
> import surface as a compatibility adapter.

**Goal:** Turn `assemble.py`, `agents/l3_select.py`, and
`agents/l4_card.py` into thin adapters while moving their implementations into
single-responsibility domain modules, without changing prompts, artifacts,
ratings, gates, `fwd_2_oc`, or BUY behavior.

**Architecture:** Dependencies point inward: L3/L4 domain modules do not import
reporting; decision finalization reads L4 parsers; report sections read decision
facts and parsers; publisher reads report sections and post-run services. Old
module paths re-export the same callables and retain the CLI so existing code,
tests, skills, and historical scripts keep working.

## Target ownership map

### L4

- `scan/l4/parsers.py`: card rating/dashboard/gate/early-stop parsing.
- `scan/l4/rubric.py`: rubric dimensions, OW three-gate rating, force-full rule.
- `scan/l4/context.py`: per-stock descriptive context and base-rate rendering.
- `scan/l4/prompts.py`: shared instructions and dispatch-pack rendering.
- `scan/l4/dispatch.py`: per-stock dispatch-plan assembly.
- `scan/l4/producers.py`: pledge/seats/consensus/fund/slim deterministic producers.
- `agents/l4_card.py`: compatibility exports and CLI dispatch only.

### L3

- `scan/l3/evidence.py`: input/evidence loading and harvesting.
- `scan/l3/triage.py`: deterministic pass-1 triage.
- `scan/l3/prompt.py`: compact table, delta/terrain rendering, preparation.
- `scan/l3/merge.py`: finalist merge, pinned injection, artifact writes.
- `scan/l3/validation.py`: numeric citation lint and narrow repair merge.
- `agents/l3_select.py`: compatibility exports and CLI dispatch only.

### L5

- `scan/l4/parsers.py`: reusable card parser formerly embedded in assemble.
- `scan/decision_finalize.py`: verify/ensemble folds and DecisionRecord write.
- `scan/report_sections.py`: pure summary sections and summary composition.
- `scan/publisher.py`: detail/trace publication and run orchestration.
- `scan/post_run.py`: existing outbox/consumer/observation service remains the
  post-run owner.
- `scan/assemble.py`: compatibility exports and CLI dispatch only.

## Task 1: Boundary contracts and cycle guards

- [ ] Add failing import/parity tests for every target module.
- [ ] Assert old imports resolve to the exact new implementation objects.
- [ ] Assert adapters stay below 250 lines and contain no implementation
  function body longer than the CLI dispatch.
- [ ] Assert L3/L4 domain modules do not import `report_sections`, `publisher`,
  or `assemble`.
- [ ] Assert Workflow sources contain scheduling only and no rating/gate
  function definitions.

## Task 2: Extract L4

- [ ] Move parser and rubric pure functions first.
- [ ] Move context, prompt, dispatch, and producer functions.
- [ ] Replace `agents/l4_card.py` with compatibility exports plus unchanged CLI.
- [ ] Run all L4/card/dispatch/context/producer tests and prompt goldens.
- [ ] Commit the L4 boundary independently.

## Task 3: Extract L3

- [ ] Move evidence and triage.
- [ ] Move prompt/table preparation.
- [ ] Move merge/finalist writing.
- [ ] Move validation/repair.
- [ ] Replace `agents/l3_select.py` with compatibility exports plus unchanged CLI.
- [ ] Run all L3 tests, including narrow repair and historical lint fixtures.
- [ ] Commit the L3 boundary independently.

## Task 4: Extract decision, report, and publisher

- [ ] Move reusable L4 card parsing out of assemble.
- [ ] Move verify/ensemble/final-rating/DecisionRecord logic to
  `decision_finalize.py`.
- [ ] Move summary helpers/composition to `report_sections.py`.
- [ ] Move detail/trace publication and run orchestration to `publisher.py`.
- [ ] Replace `assemble.py` with compatibility exports plus unchanged CLI.
- [ ] Run assemble, health, learning, report-golden, and post-run tests.
- [ ] Commit the L5 boundary independently.

## Task 5: Migrate internal consumers

- [ ] Replace learning/health/render/dossier/market/stock-stage imports from
  adapters with their owning domain modules.
- [ ] Preserve third-party and test compatibility through adapter exports.
- [ ] Run an import-cycle probe over all `autoresearch` modules.
- [ ] Commit consumer migration independently.

## Task 6: Documentation and acceptance

- [ ] Update the unified completion program, master design, SKILL, and STAGES.
- [ ] Compile all Python modules.
- [ ] Run focused parity suites and Workflow syntax probes.
- [ ] Diff production rating/gate/recall/horizon definitions against Wave 3.
- [ ] Run the full test suite and record the exact count/warnings.
- [ ] Mark software complete only after all evidence is green.

## Rollback

Each extraction is one commit. A failed parity check reverts only the current
boundary; old import paths and artifact schemas remain stable throughout.
