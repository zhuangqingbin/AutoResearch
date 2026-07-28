# Wave 2 Abstention and Research Attribution Implementation Plan

> **Design authority:** `docs/specs/2026-07-28-scan-market-unified-optimization-master-design.md`
>
> **Depends on:** Wave 1 control plane through commit `e53198e`
>
> **Execution mode:** Directly on `main`, explicitly authorized by the user.

## Goal

Turn “0 BUY” and every rejected candidate into forward-testable facts without
changing production recall, selection, early-stop, gate, ensemble, or rating
behavior.

Wave 2 answers five separate questions:

1. where each candidate first left the funnel;
2. whether a zero-BUY day was a correct, false, neutral, or immature
   abstention;
3. whether a single gate, multiple gates, or unknown evidence caused a
   rejection;
4. whether L3 bench and early-stop shadow cohorts hide missed opportunities;
5. whether ensemble folds improved or worsened the T+2 decision.

## Progress

- [ ] Task 1: candidate first-death attribution
- [ ] Task 2: day abstention verdict and causal ledger
- [ ] Task 3: tri-state unique-gate accountability
- [ ] Task 4: L3 shadow audit basket
- [ ] Task 5: early-stop shadow deep-review queue
- [ ] Task 6: ensemble fold outcome ledger
- [ ] Task 7: retro events, health, parity, and documentation

## Non-negotiable invariants

- The primary realized horizon is `fwd_2_oc`.
- False abstention means next-open tradable and
  `fwd_2_oc - same-day market median >= +0.02`.
- `UNKNOWN` never counts as `FAIL`.
- Multiple failed gates form one `L4_MULTI_GATE` sample; they never credit
  three single-gate buckets.
- A zero-BUY date is valid. No minimum BUY count/frequency is introduced.
- L3 audit and early-stop deep research remain shadow-only and cannot write
  `details/`, `_final_ratings.json`, or `decision_records.json`.
- No result in this wave automatically changes a threshold, weight, quota,
  rating, or production workflow.

## Fact contracts

### Candidate rejection row

`retro/rejection_attribution.csv` contains one row per attribution-universe
stock:

```text
date, code, first_rejection_stage, final_rating, final_action,
gate_state_quality, buyable, mature, fwd_2_oc, market_fwd_2,
excess_2, opportunity, evidence_ref
```

Legal first-death values:

```text
L0_EXCLUDED
L1_NOT_RECALLED
L2_NOT_SELECTED
PASS1_CUT
L3_BENCH
L4_EARLY_STOP
L4_GATE_MAIN
L4_GATE_EARNINGS
L4_GATE_VALUATION
L4_MULTI_GATE
ENSEMBLE_FOLDED
BOUGHT
DATA_UNDECIDABLE
```

`DATA_UNDECIDABLE` is intentionally explicit. A missing card, corrupt fact,
unknown gate, or failed control artifact must not be disguised as a gate
failure.

### Day abstention verdict

`retro/abstention_verdict.json` is hash-verified and has:

```text
date, status, n_bought, n_rejected, n_opportunities,
opportunity_codes, data_quality, reasons, generated_at, verdict_hash
```

Legal statuses:

- `IMMATURE`: no usable T+2 cross-section;
- `FALSE`: at least one rejected, next-open-tradable `+2pp` opportunity;
- `CORRECT`: mature zero-BUY day, no such opportunity, complete control facts;
- `NEUTRAL`: mature but inside the economic band or facts are degraded;
- `NOT_ABSTAINED`: the day had at least one final Buy/Overweight.

Only the first four enter the zero-BUY causal ledger.

## Task 1: Candidate first-death attribution

**Files**

- Create: `autoresearch/learning/rejection_attribution.py`
- Create: `tests/learning/test_rejection_attribution.py`
- Modify: `autoresearch/learning/retro.py`
- Modify: `tests/learning/test_retro.py`

### Step 1: Write failing pure-classifier tests

Cover:

- non-L1 row -> `L0_EXCLUDED`;
- L1 non-recalled -> `L1_NOT_RECALLED`;
- recalled but not L2 -> `L2_NOT_SELECTED`;
- pass1 cut -> `PASS1_CUT`;
- judged bench -> `L3_BENCH`;
- early stop -> `L4_EARLY_STOP`;
- exactly one failed gate -> the corresponding unique gate;
- two failed gates -> `L4_MULTI_GATE`;
- `UNKNOWN` only -> `DATA_UNDECIDABLE`;
- final Buy/Overweight -> `BOUGHT`;
- verify/ensemble fold -> `ENSEMBLE_FOLDED`.

### Step 2: Verify RED

```bash
uv run --no-sync python -m pytest \
  tests/learning/test_rejection_attribution.py -q
```

### Step 3: Implement deterministic join

Use:

- `retro/attribution.csv` for realized returns and next-open tradability;
- L1/L2/pass1/judged/finalists membership files for upstream first death;
- DecisionRecord as the authoritative finalist decision;
- historical card/gate parsing only when DecisionRecord is absent.

Compute the same-day market **median** from mature, buyable `fwd_2_oc` rows.
`opportunity=True` only when buyable, mature, rejected, and excess is at least
`+0.02`.

### Step 4: Wire retro output

After `attribution.csv` is written, atomically write
`rejection_attribution.csv`. A writer failure is loud for explicit retro CLI
execution; it is not silently treated as “no missed opportunities.”

### Step 5: Verify and commit

```bash
uv run --no-sync python -m pytest \
  tests/learning/test_rejection_attribution.py \
  tests/learning/test_retro.py tests/learning/test_retro_bought.py \
  tests/learning/test_retro_final_ratings.py -q
git add autoresearch/learning/rejection_attribution.py \
  autoresearch/learning/retro.py tests/learning/test_rejection_attribution.py \
  tests/learning/test_retro.py
git commit -m "feat(learning): attribute each candidate first death"
```

## Task 2: Day abstention verdict and causal ledger

**Files**

- Create: `autoresearch/learning/abstention_ledger.py`
- Create: `tests/learning/test_abstention_ledger.py`
- Modify: `autoresearch/learning/zero_buy_ledger.py`
- Modify: `autoresearch/scan/market.py`
- Modify: `tests/scan/test_zero_buy_narrative.py`

### Step 1: Write failing verdict tests

Cover:

- absent forward returns -> `IMMATURE`;
- one buyable rejected `+2pp` row -> `FALSE`;
- no `+2pp` opportunity with complete facts -> `CORRECT`;
- mature row inside `(-2pp,+2pp)` -> `NEUTRAL`;
- control/data degradation prevents `CORRECT` and yields `NEUTRAL`;
- a real BUY day -> `NOT_ABSTAINED`;
- corrupt verdict hash is rejected.

### Step 2: Implement verdict fact and report

Build the verdict from candidate rows, not from a market-average shortcut.
Atomically write the per-day fact, then roll it into:

```text
reports/learning/abstention_ledger.md
```

Keep `zero_buy_ledger.md` for compatibility, but make it link to and summarize
the causal verdict ledger rather than claim that a negative market average
alone proves discipline.

### Step 3: Add honest current-day narrative

The report-day funnel readout must say:

```text
日级弃权裁决: IMMATURE（T+2 尚未成熟）
```

until a verified verdict fact exists. It must never claim “非漏斗故障” merely
because the current market is risk-off.

### Step 4: Verify and commit

```bash
uv run --no-sync python -m pytest \
  tests/learning/test_abstention_ledger.py \
  tests/learning/test_zero_buy_ledger.py \
  tests/scan/test_zero_buy_narrative.py -q
git commit -m "feat(learning): classify zero-buy abstention outcomes"
```

## Task 3: Tri-state unique-gate accountability

**Files**

- Modify: `autoresearch/learning/cross_calib.py`
- Modify: `autoresearch/learning/gate_ledger.py`
- Create: `tests/learning/test_unique_gate_ledger.py`
- Modify: `tests/learning/test_gate_ledger.py`

### Step 1: Write failing tests

Require DecisionRecord-first gate reads:

- one `FAIL` and two `PASS` -> unique gate bucket;
- two `FAIL` -> `多门`;
- any `UNKNOWN` without explicit failure -> `不可判`;
- `UNKNOWN` never increments a unique fail bucket;
- a valid DecisionRecord book overrides contradictory Markdown/gate_fires.

### Step 2: Replace duplicate card parsing

Make `cross_calib.gate_stats()` and the rubric portion of `gate_ledger` read
DecisionRecord gate states first. Preserve historical fallback when the book is
absent; a present invalid book must be loud.

Single-gate rows, multi-gate rows, and undecidable rows remain physically
separate in rendered summaries.

### Step 3: Verify and commit

```bash
uv run --no-sync python -m pytest \
  tests/learning/test_unique_gate_ledger.py \
  tests/learning/test_gate_ledger.py \
  tests/learning/test_cross_calib.py -q
git commit -m "feat(learning): separate unique multi and unknown gates"
```

## Task 4: L3 shadow audit basket

**Files**

- Create: `autoresearch/learning/l3_audit_ledger.py`
- Create: `tests/learning/test_l3_audit_ledger.py`
- Modify: `autoresearch/scan/agents/l3_select.py`
- Modify: `tests/scan/test_finalists_writer.py`
- Modify: `autoresearch/learning/retro.py`

### Step 1: Write failing selection tests

The deterministic shadow selector:

- reads `_l3_bench.csv`;
- selects at most `ceil(l3_finalist_max * 0.20)` rows;
- orders by conviction descending, then fragility descending, then code;
- writes `shadow/l3_audit_candidates.csv`;
- never changes `finalists.csv`, dispatch, ratings, or card count;
- is byte-stable for identical inputs.

### Step 2: Implement the audit ledger

Join selected audit candidates to T+2 attribution and report:

- candidate count and mature count;
- false-abstention opportunities captured;
- mean market-relative T+2;
- main-finalist comparison;
- sample warning until 20 forward scan days.

The initial cohort is a measurement lane, not an alternative selector and not
a production BUY source.

### Step 3: Verify and commit

```bash
uv run --no-sync python -m pytest \
  tests/learning/test_l3_audit_ledger.py \
  tests/scan/test_finalists_writer.py \
  tests/learning/test_retro.py -q
git commit -m "feat(learning): add l3 shadow audit basket"
```

## Task 5: Early-stop shadow deep-review queue

**Files**

- Create: `autoresearch/learning/earlystop_shadow.py`
- Create: `tests/learning/test_earlystop_shadow.py`
- Create: `.claude/workflows/earlystop-shadow.js`
- Modify: `tests/scan/test_workflow_syntax.py`
- Modify: `tests/test_agent_defs.py`

### Step 1: Write failing queue tests

Require:

- stable SHA-256 sampling by `(date, code)` at a configurable 10%–20% rate;
- selection only from recorded early-stop cards;
- atomic `shadow/earlystop_queue.json`;
- no writes to production `details/` or decision facts;
- supplied shadow cards stored only under
  `shadow/earlystop_details/<code>.md`;
- ledger rows compare production rating, shadow full-card rating, T+2 excess,
  and stop reason;
- every stop-reason bucket remains `IMMATURE` until mature `n>=10`.

### Step 2: Add a separate shadow-only Workflow

The Workflow consumes an existing queue item, runs the deep/rubric path, and
writes only the shadow card path. It must state explicitly that it cannot alter
the production rating or transaction proposal. It is not called from the
latency-critical `scan-market` Workflow.

### Step 3: Verify and commit

```bash
uv run --no-sync python -m pytest \
  tests/learning/test_earlystop_shadow.py \
  tests/scan/test_workflow_syntax.py \
  tests/test_workflow_js_syntax.py tests/test_agent_defs.py -q
git commit -m "feat(learning): queue shadow review for early stops"
```

## Task 6: Ensemble fold outcome ledger

**Files**

- Create: `autoresearch/learning/ensemble_ledger.py`
- Create: `tests/learning/test_ensemble_ledger.py`
- Modify: `autoresearch/learning/pinned_ledger.py`

### Step 1: Write failing fold tests

Cover:

- OW/Buy folded to Hold on a buyable `+2pp` opportunity -> `FOLD_WRONG`;
- OW/Buy folded down on a `-2pp` underperformer -> `FOLD_RIGHT`;
- result inside the economic band -> `FOLD_NEUTRAL`;
- degraded ensemble -> `UNDECIDABLE`, not a fold;
- same-tier early stop records `early_stopped=True`;
- `ow_review` and `sell_review` remain separate;
- missing T+2 -> `IMMATURE`;
- source/final ratings come from DecisionRecord/ensemble facts, not card-name
  inference.

### Step 2: Implement general ledger

Write:

```text
reports/learning/ensemble_ledger.md
```

with original rating, run ratings, early-stop flag, spread, final rating,
market-relative T+2, and fold verdict. Keep the pinned ledger’s position-
management view, but reuse the same fold parser so the two reports cannot
disagree about source/final ratings.

No fold rule changes until each trigger family has at least ten mature folds.

### Step 3: Verify and commit

```bash
uv run --no-sync python -m pytest \
  tests/learning/test_ensemble_ledger.py \
  tests/learning/test_pinned_ledger.py \
  tests/scan/test_ensemble_fold.py -q
git commit -m "feat(learning): measure ensemble fold outcomes"
```

## Task 7: Retro events, health, integration, and Wave 2 parity

**Files**

- Modify: `autoresearch/scan/outbox.py`
- Modify: `autoresearch/scan/post_run.py`
- Modify: `autoresearch/scan/health.py`
- Modify: `autoresearch/learning/retro.py`
- Modify: `tests/scan/test_outbox.py`
- Modify: `tests/scan/test_post_run.py`
- Modify: `tests/scan/test_health.py`
- Modify: Wave 2/master documentation

### Step 1: Add a stable `RETRO_FINALIZED` event

Its payload contains attribution and rejection-attribution content hashes.
Subscribe the Wave 2 ledgers as independent consumers. A failed ledger can be
retried without rerunning price attribution or other ledgers.

Only the real `context/scan/<date>` path executes global report consumers.
Temporary tests may write local facts but must not touch `reports/learning`.

### Step 2: Add observability

Health reports:

- rejection fact presence/integrity;
- abstention verdict and data quality;
- pending/failed retro consumers;
- unique/multi/unknown gate counts;
- shadow queue counts.

Register new finite artifacts in ArtifactIndex and publish them with trace when
present. Historical absence remains advisory.

### Step 3: Verify invariants and full parity

```bash
uv run --no-sync python -m compileall -q \
  autoresearch/learning/rejection_attribution.py \
  autoresearch/learning/abstention_ledger.py \
  autoresearch/learning/l3_audit_ledger.py \
  autoresearch/learning/earlystop_shadow.py \
  autoresearch/learning/ensemble_ledger.py

uv run --no-sync python -m pytest \
  tests/learning/test_rejection_attribution.py \
  tests/learning/test_abstention_ledger.py \
  tests/learning/test_unique_gate_ledger.py \
  tests/learning/test_l3_audit_ledger.py \
  tests/learning/test_earlystop_shadow.py \
  tests/learning/test_ensemble_ledger.py \
  tests/scan/test_outbox.py tests/scan/test_post_run.py \
  tests/scan/test_health.py tests/learning/test_retro*.py -q

uv run --no-sync python -m pytest -q
```

Confirm no production-behavior diff:

```bash
git diff e53198e..HEAD -- \
  autoresearch/scan/config.py autoresearch/scan/recall \
  autoresearch/agents/utils/rating.py \
  .claude/workflows/scan-market.js .claude/workflows/l4-stock.js
```

The only Workflow addition allowed in this wave is the separate shadow-only
early-stop Workflow.

### Step 4: Update docs and commit

Record software completion separately from sample maturity. Historical or new
cohorts with `n<10`/`<20 days` remain `IMMATURE`; documentation must not claim
that any gate, audit lane, early-stop rule, or ensemble trigger is proven.

```bash
git commit -m "docs(learning): complete wave2 attribution controls"
```

## Acceptance criteria

- Every attribution-universe stock has exactly one first-death stage.
- Every zero-BUY day with a scan has a causal verdict or explicit `IMMATURE`.
- False abstention uses next-open tradability and market-relative `+2pp`.
- Unknown and multi-gate rows never contaminate single-gate statistics.
- L3 audit and early-stop shadow artifacts cannot modify production decisions.
- Ensemble fold outcomes are measurable separately for buy and sell reviews.
- Retro-derived consumers are replayable and independently observable.
- Existing reports, ratings, thresholds, and `fwd_2_oc` semantics remain
  unchanged.
- Full test suite passes.
