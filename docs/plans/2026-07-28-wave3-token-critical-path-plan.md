# Wave 3 Token and Critical-Path Controls Implementation Plan

> **Status:** Software complete on 2026-07-28. Acceptance:
> `1912 passed`, two pre-existing pandas `FutureWarning`s. Cost and latency
> effectiveness remains `IMMATURE` until ten real scans exist.

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete full-session cost accounting, observable budgets, stable L4
context contracts, narrow L3 repair, and resumable per-stock streaming without
changing rating gates or manufacturing BUY decisions.

**Architecture:** Usage facts are harvested into a versioned JSON ledger before
Markdown rendering. Run budgets only observe and degrade `StageResult`; they
never truncate research. L4 work is represented by an atomic per-stock task
book, so each stock can prepare slim data, run Intel, create a card, and finish
its adaptive ensemble independently. Every scheduling or context-layout
optimization has an explicit legacy switch, while promotion statistics remain
`IMMATURE` until ten real scans exist.

**Tech Stack:** Python 3.11, dataclasses, JSON/Markdown, pandas, pytest,
Claude Workflow JavaScript, Workflow `AsyncFunction` syntax probe.

---

## File map

- `autoresearch/trace/pricing.py`: dated official Claude price profiles and
  deterministic token-to-USD calculation.
- `autoresearch/trace/usage_harvest.py`: main-session plus subagent discovery,
  per-transcript lifecycle accounting, JSON ledger, and Markdown report.
- `autoresearch/scan/budget.py`: single-run observations and ten-run
  P50/P90/median promotion gates.
- `autoresearch/scan/context_blocks.py`: schema-bound atomic context blocks and
  content hashes.
- `autoresearch/scan/l4_tasks.py`: resumable per-stock task book, slim
  preparation, retry classification, and dispatch batches.
- `autoresearch/scan/agents/l3_select.py`: narrow repair pack and deterministic
  merge.
- `autoresearch/scan/agents/l4_card.py`: stable-context prompt producer and
  legacy/streaming slim compatibility paths.
- `.claude/workflows/scan-market.js`: configurable sector A/B and streaming
  handoff.
- `.claude/workflows/l4-stock.js`: presence gate, parallel slim/Intel, and task
  completion/failure facts.
- `.claude/skills/scan-market/SKILL.md` and `STAGES.md`: current runbook and
  artifact contracts.

## Task 1: Complete model-aware cost ledger

**Files:**

- Create: `autoresearch/trace/pricing.py`
- Modify: `autoresearch/trace/usage_harvest.py`
- Modify: `tests/trace/test_usage_harvest.py`
- Create: `tests/trace/test_pricing.py`

- [x] **Step 1: Write failing pricing and lifecycle tests**

  Add tests for:

  ```python
  def test_opus5_standard_price_includes_cache_and_output():
      usage = {
          "input": 1_000_000, "cache_read": 1_000_000,
          "cache_create_5m": 1_000_000, "cache_create_1h": 1_000_000,
          "output": 1_000_000,
      }
      got = estimate_usd("claude-opus-5", usage, as_of="2026-07-28")
      assert got["total_usd"] == 5 + 0.5 + 6.25 + 10 + 25
  ```

  Also prove that Sonnet 5 uses the introductory July 2026 price, unknown
  models remain explicitly unpriced, a failed transcript is marked discarded,
  and a failure followed by a terminal response is
  `RETRIED_SUCCEEDED`.

- [x] **Step 2: Run tests and verify RED**

  Run:

  ```bash
  uv run --no-sync python -m pytest \
    tests/trace/test_pricing.py tests/trace/test_usage_harvest.py -q
  ```

  Expected: collection fails because `autoresearch.trace.pricing` and the new
  lifecycle/cost fields do not exist.

- [x] **Step 3: Implement dated price profiles**

  Define `PriceProfile`, `price_for_model(model, as_of, speed)` and
  `estimate_usd(model, usage, as_of, speed)` using Anthropic's standard global
  prices effective on 2026-07-28:

  ```python
  PriceProfile("fable5", 10.0, 12.5, 20.0, 1.0, 50.0)
  PriceProfile("opus5", 5.0, 6.25, 10.0, 0.5, 25.0)
  PriceProfile("sonnet5_intro", 2.0, 2.5, 4.0, 0.2, 10.0)
  PriceProfile("haiku45", 1.0, 1.25, 2.0, 0.1, 5.0)
  ```

  Keep legacy Opus/Sonnet/Haiku profiles for historical transcripts. Unknown
  model or unsupported speed returns `pricing_status="UNKNOWN"` rather than
  silently applying a family guess. Record the source URL and effective date
  in the returned facts.

- [x] **Step 4: Add transcript lifecycle and cost facts**

  Extend `usage_of()` with:

  ```python
  role: Literal["main", "subagent"]
  failure_count: int
  retry_count: int
  status: Literal[
      "SUCCEEDED", "RETRIED_SUCCEEDED", "FAILED", "INCOMPLETE"
  ]
  discarded: bool
  cache_create_5m: int
  input_usd/cache_read_usd/cache_write_5m_usd/cache_write_1h_usd/output_usd
  estimated_usd: float | None
  relative_opus_cost: float | None
  ```

  Lifecycle order comes from error rows and terminal `end_turn` /
  `stop_sequence` responses. Error rows keep their own zero or non-zero usage;
  `message.id` de-duplication remains mandatory.

- [x] **Step 5: Discover and include the main transcript**

  Add `find_session_files()` and `collect_session()` so `--session ID` reads
  both:

  ```text
  <project-slug>/<ID>.jsonl
  <project-slug>/<ID>/subagents/**/agent-*.jsonl
  ```

  `--dir` retains subagent-only semantics. `--transcripts` remains the
  historical escape hatch.

- [x] **Step 6: Persist canonical JSON before Markdown**

  Add `--json-out` and make the JSON ledger contain pricing metadata, rows,
  totals, role/model/agent rollups, cache hit rate, failure/retry/discarded
  cost, and coverage. Markdown must label unknown cost as `—` and must no
  longer claim the main session is absent when `collect_session()` found it.

- [x] **Step 7: Run focused tests and commit**

  Run:

  ```bash
  uv run --no-sync python -m pytest \
    tests/trace/test_pricing.py tests/trace/test_usage_harvest.py -q
  ```

  Expected: all pass.

  Commit:

  ```bash
  git add autoresearch/trace/pricing.py autoresearch/trace/usage_harvest.py \
    tests/trace/test_pricing.py tests/trace/test_usage_harvest.py
  git commit -m "feat(trace): account full-session model costs"
  ```

## Task 2: Observable run budgets and maturity gates

**Files:**

- Create: `autoresearch/scan/budget.py`
- Create: `tests/scan/test_budget.py`
- Modify: `autoresearch/scan/user_config.py`
- Modify: `autoresearch/scan/config.py`
- Modify: `autoresearch/scan/frame.py`
- Modify: `tests/scan/test_user_config.py`
- Modify: `tests/scan/test_frame.py`

- [x] **Step 1: Write failing budget tests**

  Cover:

  ```python
  assert evaluate_history(observations[:9])["status"] == "IMMATURE"
  assert evaluate_history(observations[:10])["p50_minutes"] == 70
  assert observe_run(over_budget)["status"] == "DEGRADED"
  assert observe_run(over_budget)["truncated"] is False
  ```

  Add a config test accepting only these `budgets` keys:
  `cache_hit_min`, `stage_cost_usd`, `stage_wall_seconds`, `concurrency`,
  `min_real_scans`, `baseline_run`.

- [x] **Step 2: Run tests and verify RED**

  Run:

  ```bash
  uv run --no-sync python -m pytest \
    tests/scan/test_budget.py tests/scan/test_user_config.py \
    tests/scan/test_frame.py -q
  ```

  Expected: `budget` module and whitelist fields are missing.

- [x] **Step 3: Implement observations**

  `observe_run(scan_dir, usage_ledger, timing, budgets)` writes
  `_budget_observation.json` atomically and records a `budget` StageResult.
  Every exceeded threshold becomes a warning and `DEGRADED`; no candidate,
  card, query, or stage is cut short.

- [x] **Step 4: Implement ten-run evaluation**

  `evaluate_history(observations, phase)` returns `IMMATURE` until:

  - at least `min_real_scans` distinct production runs exist;
  - a priced baseline observation exists;
  - cost and wall values are non-null.

  Mature output includes median USD, P50/P90 minutes, cache median, cost
  reduction versus `20260727_2140`, and phase-1/phase-2 target booleans. Never
  choose the single best run.

- [x] **Step 5: Put budgets in RunContract**

  Whitelist `budgets`, map it to `ScanConfig.budgets`, and copy the normalized
  block into `RunContract.stage_budgets` in `frame --json`. Missing config
  retains current finalist/pinned budgets and default observation thresholds.

- [x] **Step 6: Run focused tests and commit**

  Run:

  ```bash
  uv run --no-sync python -m pytest \
    tests/scan/test_budget.py tests/scan/test_user_config.py \
    tests/scan/test_frame.py tests/scan/test_run_contract.py -q
  ```

  Commit:

  ```bash
  git add autoresearch/scan/budget.py autoresearch/scan/user_config.py \
    autoresearch/scan/config.py autoresearch/scan/frame.py \
    tests/scan/test_budget.py tests/scan/test_user_config.py \
    tests/scan/test_frame.py
  git commit -m "feat(scan): add observable run budgets"
  ```

## Task 3: Schema-bound stable L4 context blocks

**Files:**

- Create: `autoresearch/scan/context_blocks.py`
- Create: `tests/scan/test_context_blocks.py`
- Modify: `autoresearch/scan/market.py`
- Modify: `autoresearch/scan/agents/l4_card.py`
- Modify: `tests/scan/test_l4_prompt_cache_prefix.py`
- Modify: `tests/scan/test_l4_dispatch_pack.py`

- [x] **Step 1: Write failing block and parity tests**

  Prove:

  - the same content/source inputs produce byte-identical JSON and SHA-256;
  - changed source content changes the hash;
  - a corrupt block raises instead of being reused;
  - legacy prompt mode is byte-for-byte unchanged;
  - stable mode puts common market context before the first stock-specific
    byte, while sector/dossier/differential blocks retain all legacy evidence.

- [x] **Step 2: Run tests and verify RED**

  Run:

  ```bash
  uv run --no-sync python -m pytest \
    tests/scan/test_context_blocks.py \
    tests/scan/test_l4_prompt_cache_prefix.py \
    tests/scan/test_l4_dispatch_pack.py -q
  ```

  Expected: the context-block API and stable prompt mode are missing.

- [x] **Step 3: Implement atomic content-addressed blocks**

  `ContextBlock` has:

  ```python
  schema_version, kind, scope, content, content_sha256,
  source_hashes, created_for_date
  ```

  Store it below `_context_blocks/<kind>/<safe-scope>.json`, write with a temp
  file plus replace, and verify schema/content/source hashes on read.

- [x] **Step 4: Split market content without losing text**

  Extract common market lines and the industry-specific line from
  `market_context_block()`. The default combined function remains byte-for-byte
  compatible. Stable mode writes common market once, sector terrain once per
  sector, dossier once per stock, and stock differential once per stock.

- [x] **Step 5: Add stable prompt mode with legacy rollback**

  `write_dispatch_pack(scan_dir, stable_context=False)` preserves current
  bytes by default. With `stable_context=True`, ordering is:

  ```text
  fixed contract → shared instructions → common market block
  → sector block → dossier block → stock differential → data pointers
  ```

  Hash facts remain in `_context_blocks` and a prompt manifest, not as
  per-stock bytes before the shared prefix.

- [x] **Step 6: Run focused tests and commit**

  Run the command from Step 2; expected all pass.

  Commit:

  ```bash
  git add autoresearch/scan/context_blocks.py autoresearch/scan/market.py \
    autoresearch/scan/agents/l4_card.py tests/scan/test_context_blocks.py \
    tests/scan/test_l4_prompt_cache_prefix.py tests/scan/test_l4_dispatch_pack.py
  git commit -m "feat(scan): add stable L4 context blocks"
  ```

## Task 4: Narrow L3 lint repair

**Files:**

- Modify: `autoresearch/scan/agents/l3_select.py`
- Create: `tests/scan/test_l3_repair.py`
- Modify: `.claude/workflows/scan-market.js`
- Modify: `tests/scan/test_workflow_contracts.py`

- [x] **Step 1: Write failing repair tests**

  Build three judged rows with one lint failure and assert:

  ```python
  pack = build_repair_pack(date, root)
  assert [row["code"] for row in pack["rows"]] == ["000002"]
  assert "000001" not in pack["prompt"]
  assert apply_repair_patch(date, root)["preserved"] == 2
  ```

  Reject patches containing an unrequested code, a duplicate code, or fields
  outside `code/thesis`.

- [x] **Step 2: Run tests and verify RED**

  Run:

  ```bash
  uv run --no-sync python -m pytest tests/scan/test_l3_repair.py -q
  ```

  Expected: repair-pack APIs and CLI commands are missing.

- [x] **Step 3: Implement repair pack and merge**

  Add CLI commands:

  ```text
  l3_select repair-pack DATE
  l3_select apply-repair DATE
  ```

  The pack contains only failing judged rows, their exact L2 evidence,
  admissible market values relevant to the failure, and a two-field patch
  schema. The agent writes `_l3_repair_patch.json`; deterministic merge
  preserves every unrequested row byte-semantically and rewrites
  `_l3_judged.json` atomically.

- [x] **Step 4: Replace full-context workflow repair**

  Workflow runs `repair-pack`, asks the repair agent to read only
  `_l3_repair_prompt.md`, then runs `apply-repair`. On connection failure it
  logs the failure and continues with the original judged file exactly as
  today.

- [x] **Step 5: Run tests, syntax probe, and commit**

  Run:

  ```bash
  uv run --no-sync python -m pytest \
    tests/scan/test_l3_repair.py tests/scan/test_workflow_contracts.py -q
  uv run --no-sync python scripts/check_workflow_js.py \
    .claude/workflows/scan-market.js
  ```

  Commit:

  ```bash
  git add autoresearch/scan/agents/l3_select.py \
    .claude/workflows/scan-market.js tests/scan/test_l3_repair.py \
    tests/scan/test_workflow_contracts.py
  git commit -m "perf(scan): repair only failing L3 rows"
  ```

## Task 5: Resumable per-stock L4 task book

**Files:**

- Create: `autoresearch/scan/l4_tasks.py`
- Create: `tests/scan/test_l4_tasks.py`
- Modify: `autoresearch/scan/artifacts.py`
- Modify: `tests/scan/test_artifacts.py`

- [x] **Step 1: Write failing task-book tests**

  Cover atomic initialization, successful-card skip, failed-stock isolation,
  one transient retry, no schema/contract retry, stale-task recovery, and
  stable dispatch batching:

  ```python
  assert preflight(book, "000001")["action"] == "RUN"
  mark_success(book, "000001")
  assert preflight(book, "000001")["action"] == "SKIP"
  mark_failure(book, "000002", "RATE_LIMIT")
  assert preflight(book, "000002")["attempt"] == 2
  mark_failure(book, "000003", "SCHEMA_ERROR")
  assert preflight(book, "000003")["action"] == "BLOCKED"
  ```

- [x] **Step 2: Run tests and verify RED**

  Run:

  ```bash
  uv run --no-sync python -m pytest tests/scan/test_l4_tasks.py -q
  ```

  Expected: `l4_tasks` is missing.

- [x] **Step 3: Implement the task state machine**

  `_l4_tasks.json` stores `PENDING/RUNNING/SUCCEEDED/FAILED/BLOCKED`, attempt,
  pinned flag, required artifact paths/hashes, last error class, and timestamps.
  A success is reusable only when prompt/slim/card hashes verify. Transient
  classes are `RATE_LIMIT`, `CONNECTION`, and `TIMEOUT`; maximum attempt is
  two. Contract/schema/data-integrity failures never retry.

- [x] **Step 4: Implement per-stock slim preparation**

  `prepare CODE DATE` harvests only that stock when the verified slim file is
  absent, uses `_slim_defect`, performs at most one light retry, and records the
  attempt. It never touches another stock's successful state.

- [x] **Step 5: Implement bounded dispatch batches**

  Read independent configured caps for `tushare`, `web_search`, `web_fetch`,
  and `l4_stock`. The active batch width is their explicit minimum because one
  Intel workflow can consume both web services. Persist the individual caps
  and effective cap; after a rate-limit failure the next batch drops by one to
  a floor of one. This changes scheduling only, never rating or query caps.

- [x] **Step 6: Register the artifact and commit**

  Register `l4_task_book` in `ArtifactIndex`, run:

  ```bash
  uv run --no-sync python -m pytest \
    tests/scan/test_l4_tasks.py tests/scan/test_artifacts.py -q
  ```

  Commit:

  ```bash
  git add autoresearch/scan/l4_tasks.py autoresearch/scan/artifacts.py \
    tests/scan/test_l4_tasks.py tests/scan/test_artifacts.py
  git commit -m "feat(scan): add resumable L4 stock tasks"
  ```

## Task 6: Streaming Workflow and sector-brief A/B switch

**Files:**

- Modify: `.claude/workflows/scan-market.js`
- Modify: `.claude/workflows/l4-stock.js`
- Modify: `autoresearch/scan/user_config.py`
- Modify: `autoresearch/scan/config.py`
- Modify: `autoresearch/scan/agents/l4_card.py`
- Modify: `.claude/skills/scan-market/scan_config.jsonc`
- Create: `tests/scan/test_wave3_workflows.py`
- Modify: `tests/scan/test_user_config.py`

- [x] **Step 1: Write failing config/source-contract tests**

  Accept:

  ```json
  {
    "performance": {
      "streaming_l4": true,
      "stable_context_blocks": false,
      "sector_brief_mode": "all"
    }
  }
  ```

  Reject invalid modes. Source tests must prove the streaming branch has no
  batch `harvest-slim` barrier, l4-stock runs slim and Intel in `parallel`,
  task success is presence-gated, and legacy mode retains the old batch gate.

- [x] **Step 2: Run tests and verify RED**

  Run:

  ```bash
  uv run --no-sync python -m pytest \
    tests/scan/test_wave3_workflows.py tests/scan/test_user_config.py -q
  ```

  Expected: config keys and workflow branches are absent.

- [x] **Step 3: Add performance switches**

  Whitelist:

  ```text
  streaming_l4: bool
  stable_context_blocks: bool
  sector_brief_mode: "all" | "finalist_only"
  ```

  Defaults are current production behavior for context and sector research;
  streaming defaults on because its inputs and rating path are unchanged.
  `streaming_l4=false` is the byte-compatible rollback path.

- [x] **Step 4: Move slim into each stock workflow**

  In streaming mode scan-market initializes the task book and returns
  `dispatch_batches` immediately after prompt creation. Each l4-stock workflow:

  1. checks task presence/status;
  2. runs per-stock slim preparation and Intel concurrently;
  3. starts the card as soon as both are terminal;
  4. runs the existing adaptive ensemble unchanged;
  5. verifies the card/ensemble and marks only that stock successful;
  6. records a classified failure without modifying other stocks.

- [x] **Step 5: Add sector-brief A/B scheduling**

  `all` retains the current pre-L3 full sector brief path.
  `finalist_only` lets L3 consume deterministic sector terrain only, then
  generates briefs for unique finalist sectors after GATE2 and before those
  stocks' prompts. No rating or finalist cap changes.

- [x] **Step 6: Run workflow tests and commit**

  Run:

  ```bash
  uv run --no-sync python -m pytest \
    tests/scan/test_wave3_workflows.py tests/scan/test_user_config.py \
    tests/scan/test_l4_dispatch_pack.py tests/scan/test_l4_tasks.py -q
  uv run --no-sync python scripts/check_workflow_js.py \
    .claude/workflows/scan-market.js .claude/workflows/l4-stock.js
  ```

  Commit:

  ```bash
  git add .claude/workflows/scan-market.js .claude/workflows/l4-stock.js \
    .claude/skills/scan-market/scan_config.jsonc \
    autoresearch/scan/user_config.py autoresearch/scan/config.py \
    autoresearch/scan/agents/l4_card.py \
    tests/scan/test_wave3_workflows.py tests/scan/test_user_config.py
  git commit -m "perf(scan): stream independent L4 stock workflows"
  ```

## Task 7: Cost/timing observation integration

**Files:**

- Modify: `autoresearch/scan/assemble.py`
- Modify: `autoresearch/scan/artifacts.py`
- Modify: `autoresearch/scan/post_run.py`
- Create: `tests/scan/test_wave3_observation.py`
- Modify: `tests/scan/test_assemble.py`

- [x] **Step 1: Write failing integration tests**

  Assert that an available token JSON plus timing JSON produces a budget
  observation and artifact entry; absence produces an explicit unmeasured
  warning, not `$0`. Verify a degraded budget does not alter finalists,
  DecisionRecords, or report ratings.

- [x] **Step 2: Run tests and verify RED**

  Run:

  ```bash
  uv run --no-sync python -m pytest \
    tests/scan/test_wave3_observation.py tests/scan/test_assemble.py -q
  ```

  Expected: no observation integration exists.

- [x] **Step 3: Integrate without business coupling**

  Post-run reads only canonical cost/timing artifacts, writes the observation,
  registers it, and adds a report section with `IMMATURE`/mature statistics.
  It must not import rating logic or mutate any DecisionRecord.

- [x] **Step 4: Add cost effectiveness denominators**

  Report:

  - USD per mature DecisionRecord;
  - USD per final BUY candidate;
  - USD per verified correct rejection.

  A zero denominator renders `—`; it is never converted to zero cost or a
  forced BUY.

- [x] **Step 5: Run tests and commit**

  Run:

  ```bash
  uv run --no-sync python -m pytest \
    tests/scan/test_wave3_observation.py tests/scan/test_assemble.py \
    tests/scan/test_artifacts.py -q
  ```

  Commit:

  ```bash
  git add autoresearch/scan/assemble.py autoresearch/scan/artifacts.py \
    autoresearch/scan/post_run.py tests/scan/test_wave3_observation.py \
    tests/scan/test_assemble.py
  git commit -m "feat(scan): publish cost and latency observations"
  ```

## Task 8: Runbook, parity, and Wave 3 acceptance

**Files:**

- Modify: `.claude/skills/scan-market/SKILL.md`
- Modify: `.claude/skills/scan-market/STAGES.md`
- Modify: `docs/plans/2026-07-28-unified-optimization-completion-program.md`
- Modify: `docs/specs/2026-07-28-scan-market-unified-optimization-master-design.md`
- Modify: `docs/plans/2026-07-28-wave3-token-critical-path-plan.md`

- [x] **Step 1: Update the runbook**

  Document `dispatch_batches`, task replay, transient-only retry, legacy
  rollback switches, cost JSON/Markdown, budget observation, cache redline,
  and the ten-real-scan maturity rule. State that the main session must execute
  batches in order while calls within one batch remain parallel.

- [x] **Step 2: Run compile and focused regression**

  Run:

  ```bash
  uv run --no-sync python -m compileall -q autoresearch
  uv run --no-sync python -m pytest \
    tests/trace tests/scan/test_budget.py tests/scan/test_context_blocks.py \
    tests/scan/test_l3_repair.py tests/scan/test_l4_tasks.py \
    tests/scan/test_wave3_workflows.py tests/scan/test_wave3_observation.py -q
  uv run --no-sync python scripts/check_workflow_js.py \
    .claude/workflows/scan-market.js .claude/workflows/l4-stock.js
  ```

- [x] **Step 3: Prove rating/gate parity**

  Diff production rating parser, gate thresholds, recall settings, and
  `fwd_2_oc` definitions against the Wave 2 completion commit. Expected: no
  semantic change. Run the prompt golden tests in both legacy and stable modes.

- [x] **Step 4: Run full suite**

  Run:

  ```bash
  uv run --no-sync python -m pytest -q
  ```

  Expected: all tests pass; only the two pre-existing pandas FutureWarnings may
  remain.

- [x] **Step 5: Record honest completion**

  Mark Wave 3 software complete, record exact test counts and commits, and keep
  cost/latency promotion `IMMATURE` until ten real scans exist. Do not claim
  the ≥15%/≥25% cost or P50/P90 targets from unit tests.

- [x] **Step 6: Commit documentation**

  ```bash
  git add .claude/skills/scan-market/SKILL.md \
    .claude/skills/scan-market/STAGES.md \
    docs/plans/2026-07-28-unified-optimization-completion-program.md \
    docs/specs/2026-07-28-scan-market-unified-optimization-master-design.md \
    docs/plans/2026-07-28-wave3-token-critical-path-plan.md
  git commit -m "docs(scan): complete wave3 performance controls"
  ```

### Completion record

- Implementation commits:
  `3c6c6f8`, `2dfce79`, `7a774d4`, `ac58161`, `7a85754`, `f885177`,
  `4a1ac56`.
- Focused acceptance: 62 performance-control tests plus 21 prompt/card parity
  tests; both production Workflow files passed the `AsyncFunction` syntax
  probe.
- Full-suite acceptance: `1912 passed`, with two pre-existing pandas
  `FutureWarning`s.
- Production rating, gate, recall scoring, and `fwd_2_oc` files have no diff
  against Wave 2 completion commit `257f826`.
- Real-scan cost/latency promotion remains `IMMATURE`; no cost or speed target
  is claimed from synthetic tests.

## Self-review

- **Spec coverage:** Task 1 covers main/subagent, cache/input/output/model,
  official pricing, failure/retry/discarded facts and cost denominators. Tasks
  2/7 cover budgets, StageResult degradation, ten-run medians and P50/P90.
  Tasks 3/4 cover stable shared context and narrow repair. Tasks 5/6 cover
  bounded batches, Intel/slim overlap, per-stock presence gates, independent
  retry, adaptive ensemble preservation, and sector brief A/B.
- **No hidden production research change:** stable context and finalist-only
  sector briefs are opt-in. Streaming changes scheduling only and retains a
  legacy switch. No gate, rating, horizon, finalist cap, or BUY quota changes.
- **Failure semantics:** only transient failures get one retry; schema and data
  contract failures remain loud. Missing cost/timing is unmeasured, never zero.
- **Type consistency:** `usage_ledger → observe_run → budget observation`;
  `l4 task book → dispatch_batches → preflight/prepare → success/failure`;
  `repair pack → patch → deterministic merge` use one named contract each.
- **Maturity honesty:** software completion and production promotion are
  separate. Unit tests cannot satisfy real-scan thresholds.
