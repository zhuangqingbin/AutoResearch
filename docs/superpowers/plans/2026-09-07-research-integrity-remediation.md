# Research Integrity Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair the twelve confirmed integrity defects in broker ingestion, execution accounting, evidence/card migration, and registered offline research without changing production trading policy.

**Architecture:** Preserve existing external artifacts and add conservative identity, coverage, and validation fields. Keep pure accounting/statistical logic below IO runners, isolate shadow failures at the B4 boundary, and make experiment specifications executable before any output directory is created.

**Tech Stack:** Python 3.12, pandas, NumPy, Decimal, pytest, JSON/CSV/Markdown artifacts, existing `autoresearch.common.stats` and workspace contracts.

**Execution mode:** Inline execution in this session; project rules do not authorize subagent implementation.

---

## File map

| Responsibility | Files |
|---|---|
| Stable broker identity/upsert conflicts | `autoresearch/broker/schema.py`, `autoresearch/broker/store.py`, `tests/broker/test_schema.py`, `tests/broker/test_store.py` |
| FIFO episodes and observed-fill accounting | `autoresearch/research/execution_import.py`, new `autoresearch/research/execution_ledger.py`, `autoresearch/research/execution_audit.py`, `tests/research/test_execution_audit.py`, new `tests/research/test_execution_ledger.py` |
| Snapshot exit and costed simulation | `autoresearch/research/execution_audit.py`, `autoresearch/common/execution_math.py`, `tests/research/test_execution_audit.py` |
| Conservative claim extraction/comparison | `autoresearch/news/claim_extract.py`, `autoresearch/news/claim_support.py`, `autoresearch/scan/l4/intel_guard.py`, `tests/news/test_claim_support.py`, `tests/scan/test_intel_guard.py` |
| Card tri-state/profile durability | `autoresearch/scan/l4/card_io.py`, `autoresearch/trace/completeness.py`, `tests/scan/test_research_card_migration.py`, `tests/trace/test_completeness.py` |
| Valid block p-values and W3 population | `autoresearch/common/stats.py`, `autoresearch/research/w3_grids.py`, `tests/common/test_block_bootstrap.py`, `tests/research/test_w3_grids.py` |
| Executable registration | new `autoresearch/research/registration.py`, `autoresearch/research/stage_value.py`, `autoresearch/research/w3_grids.py`, new `tests/research/test_registration.py`, existing runner tests |
| State/readout correction | implementation index, execution/research plans, W3 readout and a new registered W3 spec/readout |

## Task 1: Make broker upsert identity stable across export ranges

**Files:**
- Modify: `autoresearch/broker/schema.py`
- Modify: `autoresearch/broker/store.py`
- Test: `tests/broker/test_schema.py`
- Test: `tests/broker/test_store.py`

- [ ] **Step 1: Add failing identity and conflict tests.**

```python
def test_trade_id_hash_is_stable_across_export_ranges(raw):
    first = schema.normalize(raw.rows(
        {"trade_id": "T1", "trade_time": "09:31:05"},
        {"trade_id": "T2", "trade_time": "09:32:00"},
    ), source_kind="gtht", source_file="full.csv")
    second = schema.normalize(raw(trade_id="T2", trade_time="09:32:00"),
                              source_kind="gtht", source_file="partial.csv")
    assert first.loc[first.trade_id == "T2", "row_hash"].item() == second.row_hash.item()

def test_same_stable_identity_with_different_economics_is_rejected(tmp_path, raw):
    old = schema.normalize(raw(trade_id="T1", amount="1234"),
                           source_kind="gtht", source_file="a.csv")
    changed = schema.normalize(raw(trade_id="T1", amount="9999"),
                               source_kind="gtht", source_file="b.csv")
    store.upsert_raw(old, root=tmp_path)
    with pytest.raises(schema.DataContractError, match="identity conflict"):
        store.upsert_raw(changed, root=tmp_path)
```

- [ ] **Step 2: Run the two tests and verify RED.**

Run: `AUTORESEARCH_ENGINE=codex uv run --no-sync python -m pytest -q tests/broker/test_schema.py::test_trade_id_hash_is_stable_across_export_ranges tests/broker/test_store.py::test_same_stable_identity_with_different_economics_is_rejected`

Expected: the first hash differs and the second import is accepted.

- [ ] **Step 3: Implement stable identity and payload conflict checking.**

Add `stable_row_identity(record)` in `schema.py`. Hash `source_kind/account/trade_date/trade_id` when a real ID exists; otherwise hash all normalized economic/source fields except `source_file`, `ingested_at`, `seq`, and `row_hash`. Keep `seq` only as display/reconciliation metadata. In `store.upsert_raw`, compare every incoming hash already present with immutable economic fields and raise `DataContractError` on mismatch.

- [ ] **Step 4: Run all broker tests and verify GREEN.**

Run: `AUTORESEARCH_ENGINE=codex uv run --no-sync python -m pytest -q tests/broker`

- [ ] **Step 5: Commit only Task 1 files.**

```bash
git add autoresearch/broker/schema.py autoresearch/broker/store.py tests/broker/test_schema.py tests/broker/test_store.py
git commit -m "fix(broker): stabilize trade identity across exports"
```

## Task 2: Split observed fills into FIFO position episodes

**Files:**
- Create: `autoresearch/research/execution_ledger.py`
- Modify: `autoresearch/research/execution_import.py`
- Modify: `autoresearch/research/execution_audit.py`
- Create: `tests/research/test_execution_ledger.py`
- Test: `tests/research/test_execution_audit.py`

- [ ] **Step 1: Add failing episode tests.**

```python
def fill(side, qty, amount, trade_date, fill_id=None):
    return {
        "account_hash": "acct", "code": "600000", "fill_id": fill_id or f"{side}-{trade_date}",
        "side": side, "trade_date": trade_date, "trade_time": "09:30:00",
        "qty": qty, "amount": amount, "commission": "0", "stamp_tax": "0",
        "transfer_fee": "0", "other_fee": "0",
    }

def test_two_round_trips_are_two_episodes():
    rows, coverage = build_episodes([
        fill("BUY", "100", "1000", "20260901"), fill("SELL", "100", "1100", "20260902"),
        fill("BUY", "100", "2000", "20260903"), fill("SELL", "100", "1800", "20260904"),
    ])
    assert [r["net_return_realized"] for r in rows] == [Decimal("0.1"), Decimal("-0.1")]
    assert len({r["position_id"] for r in rows}) == 2 and coverage == []

def test_oversell_is_coverage_not_a_return():
    rows, coverage = build_episodes([
        fill("BUY", "100", "1000", "20260901"), fill("SELL", "200", "2200", "20260902"),
    ])
    assert rows == []
    assert coverage[0]["reason"] == "UNMATCHED_OPENING_INVENTORY"

def test_partial_exit_retains_remaining_risk():
    rows, _ = build_episodes([
        fill("BUY", "100", "1000", "20260901"), fill("SELL", "40", "440", "20260902"),
    ])
    assert rows[0]["exit_state"] == "PARTIAL_FILL"
    assert rows[0]["remaining_qty"] == Decimal("60")
```

- [ ] **Step 2: Run the new file and verify RED because `execution_ledger` does not exist.**

Run: `AUTORESEARCH_ENGINE=codex uv run --no-sync python -m pytest -q tests/research/test_execution_ledger.py`

- [ ] **Step 3: Implement the pure FIFO ledger.**

Create `build_episodes(fills) -> (assessments, coverage)`. Group by account hash/code, sort by date/time/fill ID, allocate buy notional and fees by FIFO quantity, allocate each sell's proceeds and fees to the quantity consumed, close an episode at zero, and use a digest of account/code/first-buy/fill IDs as `position_id`. Reject returns for an oversell. Expose `buy_qty`, `sell_qty`, `remaining_qty`, `realized_pnl`, `unrealized_pnl`, `net_pnl_cash`, and `net_return_realized`.

- [ ] **Step 4: Preserve `OTHER` rows as unresolved cash coverage.**

Change `load_trades` so `OTHER` rows produce a coverage record with amount/date/source instead of disappearing. Do not attach them to an episode without an explicit link.

- [ ] **Step 5: Route `_observed_rows` through `build_episodes` and verify GREEN.**

Run: `AUTORESEARCH_ENGINE=codex uv run --no-sync python -m pytest -q tests/research/test_execution_ledger.py tests/research/test_execution_audit.py tests/common/test_execution_math.py`

- [ ] **Step 6: Commit Task 2 files.**

```bash
git add autoresearch/research/execution_ledger.py autoresearch/research/execution_import.py autoresearch/research/execution_audit.py tests/research/test_execution_ledger.py tests/research/test_execution_audit.py
git commit -m "fix(research): account for FIFO execution episodes"
```

## Task 3: Complete snapshot-simulated exit and cost reporting

**Files:**
- Modify: `autoresearch/research/execution_audit.py`
- Modify: `autoresearch/common/execution_math.py`
- Test: `tests/research/test_execution_audit.py`
- Test: `tests/common/test_execution_math.py`

- [ ] **Step 1: Add failing due/not-due and cost tests.**

```python
def test_snapshot_simulation_exits_at_d2_open_with_costs(world):
    rows, coverage = _snapshot_rows(load_snapshots(world["snaps"], engine="codex")[0],
                                    blocks=_execution_blocks(world["runs"]), policy=POLICY,
                                    max_age_seconds=120, lake_daily=world["lake"],
                                    simulation_qty="100")
    row = next(r for r in rows if r["code"] == "600000")
    assert row["exit_state"] == "FILLED"
    assert row["gross_return"] is not None and row["net_return_realized"] is not None
    assert row["fill_rule_version"] == "close_auction_limit_v1+open_auction_v1"

def test_snapshot_without_quantity_has_gross_but_no_costed_return(world):
    rows, coverage = _snapshot_rows(load_snapshots(world["snaps"], engine="codex")[0],
                                    blocks=_execution_blocks(world["runs"]), policy=POLICY,
                                    max_age_seconds=120, lake_daily=world["lake"],
                                    simulation_qty=None)
    assert rows[0]["net_return_realized"] is None
    assert any(x["reason"] == "MISSING_SIMULATION_QTY" for x in coverage)
```

- [ ] **Step 2: Run the tests and verify RED on the missing parameter/constant exit.**

- [ ] **Step 3: Implement D+2 exit state and both simulated legs.**

Read entry close and D+2 open/exit flag from `forward_frame`. Compute gross return whenever both prices are known. When quantity is supplied, call `simulated_leg` for BUY and SELL and `position_pnl` for costed return. Distinguish `NOT_DUE`, `UNKNOWN`, `NO_FILL`, and `FILLED`; keep `unsellable_o2` and `holding_window_breached` in the assessment.

- [ ] **Step 4: Extend CLI/report fields additively and verify GREEN.**

Run: `AUTORESEARCH_ENGINE=codex uv run --no-sync python -m pytest -q tests/research/test_execution_audit.py tests/common/test_execution_math.py tests/contracts/test_execution_contract.py`

- [ ] **Step 5: Commit Task 3 files.**

```bash
git add autoresearch/research/execution_audit.py autoresearch/common/execution_math.py tests/research/test_execution_audit.py tests/common/test_execution_math.py
git commit -m "fix(research): complete snapshot execution simulation"
```

## Task 4: Isolate and correct shadow claim extraction

**Files:**
- Modify: `autoresearch/news/claim_extract.py`
- Modify: `autoresearch/news/claim_support.py`
- Modify: `autoresearch/scan/l4/intel_guard.py`
- Test: `tests/news/test_claim_support.py`
- Test: `tests/scan/test_intel_guard.py`

- [ ] **Step 1: Add failing parser/comparator tests.**

```python
def test_planned_completion_is_not_actual_completion():
    event = extract_event("公司拟完成回购 1 亿元。", subject_code="600000")["event"]
    assert (event["lifecycle"], event["assertion_kind"]) == ("plan", "forecast")

def test_comma_amount_is_not_truncated():
    event = extract_event("公司已完成回购 1,000 万元。", subject_code="600000")["event"]
    assert event["amount_value"] == "10000000"

def test_cumulative_totals_at_different_dates_are_unknown():
    first = {"start": "2026-09-01T00:00:00+08:00", "end": "2026-09-02T00:00:00+08:00",
             "precision": "day"}
    later = {"start": "2026-09-03T00:00:00+08:00", "end": "2026-09-04T00:00:00+08:00",
             "precision": "day"}
    result = compare_events(event(amount_value="100000000", effective_at=first),
                            event(amount_value="200000000", effective_at=later),
                            checked_fields=IDENTITY_FIELDS + SEMANTIC_FIELDS)
    assert result["verdict"] == "UNKNOWN"
```

- [ ] **Step 2: Add a failing guard isolation test.**

```python
def test_invalid_shadow_date_cannot_fail_intel_guard(scan_dir):
    intel_path(scan_dir, "600000").write_text("公司于 2026-09-31 完成回购 1 亿元。")
    result = guard_intel(scan_dir, "600000")
    assert result["ok"] is True and result["action"] == "KEPT"
    assert result["claim_events"]["errors"][0]["reason"] == "INVALID_DATE"
```

- [ ] **Step 3: Run the four tests and verify their current wrong outputs/exceptions.**

- [ ] **Step 4: Implement phrase precedence, strict comma parsing, and comparability.**

Recognize explicit completed phrases before general tokens, then let plan markers dominate bare completion nouns. Treat plan assertions as `forecast`. Strip only valid `\d{1,3}(,\d{3})+` grouping. Catch invalid `date(...)` construction and return a note with unknown effective time. In `compare_events`, evaluate amount only when unit, basis, and effective interval are all comparable; otherwise make amount `UNKNOWN`.

- [ ] **Step 5: Catch errors per shadow line and around sidecar persistence.**

Return `{count, path, errors}` from `_extract_claim_events`. Do not let its exceptions escape `guard_intel`; existing lint/action fields remain unchanged.

- [ ] **Step 6: Run claim and intel suites and verify GREEN.**

Run: `AUTORESEARCH_ENGINE=codex uv run --no-sync python -m pytest -q tests/news/test_claim_support.py tests/news/test_claim_binding.py tests/scan/test_intel_guard.py`

- [ ] **Step 7: Commit Task 4 files.**

```bash
git add autoresearch/news/claim_extract.py autoresearch/news/claim_support.py autoresearch/scan/l4/intel_guard.py tests/news/test_claim_support.py tests/scan/test_intel_guard.py
git commit -m "fix(news): make shadow claims conservative and isolated"
```

## Task 5: Preserve card tri-state and card-source identity

**Files:**
- Modify: `autoresearch/scan/l4/card_io.py`
- Modify: `autoresearch/trace/completeness.py`
- Test: `tests/scan/test_research_card_migration.py`
- Test: `tests/trace/test_completeness.py`

- [ ] **Step 1: Add failing migration tests.**

```python
def test_unreviewed_gate_is_unknown_not_pass():
    card = card_from_text("OW三门：主力真在 ✓｜业绩真兑现 未核｜估值不透支 未核\n评级：Hold",
                          code="600000", analysis_date="2026-09-07", holding=False)
    assert card["gates"] == {"主力真在": "PASS", "业绩真兑现": "UNKNOWN",
                             "估值不透支": "UNKNOWN"}

def test_card_source_survives_profile_round_trip(tmp_path):
    original = scan_profile(card_source="research_json_v1")
    write_expected(tmp_path, original)
    assert profile_from_capsule(tmp_path).card_source == "research_json_v1"
```

- [ ] **Step 2: Run both tests and verify RED.**

- [ ] **Step 3: Implement explicit three-state gate parsing and profile persistence.**

Parse the three gate clauses directly: recognized positive markers map to PASS, recognized veto markers to FAIL, and `未核`/missing text to UNKNOWN. Add `card_source` to `verification/profile.json`, pass it through `profile_from_capsule`, retain `legacy_md` only when the historical field is absent, and validate stored values through `scan_profile`.

- [ ] **Step 4: Run migration/trace suites and verify GREEN.**

Run: `AUTORESEARCH_ENGINE=codex uv run --no-sync python -m pytest -q tests/scan/test_research_card_migration.py tests/trace/test_completeness.py tests/trace/test_run_profiles.py`

- [ ] **Step 5: Commit Task 5 files.**

```bash
git add autoresearch/scan/l4/card_io.py autoresearch/trace/completeness.py tests/scan/test_research_card_migration.py tests/trace/test_completeness.py
git commit -m "fix(scan): preserve card tri-state and source profile"
```

## Task 6: Replace synthetic BY inputs with block-bootstrap p-values

**Files:**
- Modify: `autoresearch/common/stats.py`
- Modify: `autoresearch/research/w3_grids.py`
- Test: `tests/common/test_block_bootstrap.py`
- Test: `tests/research/test_w3_grids.py`

- [ ] **Step 1: Add failing p-value tests.**

```python
def test_block_mean_pvalue_is_deterministic_and_two_sided():
    result = block_mean_test([1.0, 1.1, .9, 1.2, .8], block=2, seed=7, n_boot=999)
    assert result.pvalue == pytest.approx(block_mean_test(
        [1.0, 1.1, .9, 1.2, .8], block=2, seed=7, n_boot=999).pvalue)
    assert 0 < result.pvalue <= 1

def test_family_correction_does_not_invent_p_for_immature_cell():
    family = family_correction(stats_with_one_immature_cell(), seed=7, n_boot=999)
    row = next(x for x in family if x["status"] == "NOT_TESTED")
    assert row["p_raw"] is None and row["q_by"] is None and row["rejected"] is None
```

- [ ] **Step 2: Run these tests and verify RED.**

- [ ] **Step 3: Implement `block_mean_test`.**

Convert finite observations to an array, center it, draw circular moving-block indices with existing `block_index`, compute the null bootstrap means, and return the finite-sample corrected two-sided p-value plus block/seed/n_boot metadata. Reject invalid block sizes; return an explicit insufficient result for fewer than two observations.

- [ ] **Step 4: Make W3 retain daily series for inference and call BY only on tested hypotheses.**

`judge_cells` emits the daily equal-weight primary series internally. `family_correction` computes raw p-values with label block lengths (`gap_c1_o2=1`, `fwd_5_oc=5`, `fwd_10_oc=10`), applies BY to the tested members, and merges null fields for `NOT_TESTED` members. Remove proxy wording and fields.

- [ ] **Step 5: Run stats/W3 tests and verify GREEN.**

Run: `AUTORESEARCH_ENGINE=codex uv run --no-sync python -m pytest -q tests/common/test_block_bootstrap.py tests/common/test_stats.py tests/research/test_w3_grids.py`

- [ ] **Step 6: Commit Task 6 files.**

```bash
git add autoresearch/common/stats.py autoresearch/research/w3_grids.py tests/common/test_block_bootstrap.py tests/research/test_w3_grids.py
git commit -m "fix(research): use block p-values for W3 BY correction"
```

## Task 7: Give every W3 ruler its correct entry population

**Files:**
- Modify: `autoresearch/research/w3_grids.py`
- Test: `tests/research/test_w3_grids.py`

- [ ] **Step 1: Add the failing open-versus-close eligibility test.**

```python
def test_g3_uses_fwd5_open_eligibility_not_close_buyability(lake, monkeypatch):
    panel = panel_where_open_is_tradable_but_close_is_sealed(lake)
    cells = build_cells(panel, lake_root=lake.root, signal_days=["20260901"])
    assert len(cells[G3]) == 1
    assert cells[G3].iloc[0]["fwd5_pp"] == pytest.approx(20.0)
```

- [ ] **Step 2: Run it and verify RED because `in_pop` uses `gap_c1_o2`.**

- [ ] **Step 3: Add label-specific population columns.**

Build `in_pop_gap`, `in_pop_fwd5`, and `in_pop_fwd10` with `entry_tradable(fr, ruler_name=...)`. Map each grid's primary label to its own population flag. Keep G2's explicit buyable/unbuyable split and G1's close-entry population.

- [ ] **Step 4: Add a contract test for declared ST/new/BJ exclusions.**

Either apply the registered exclusions from available lake metadata or fail the runner with `UNSUPPORTED_POPULATION_RULE`; never claim them while using an unfiltered frame.

- [ ] **Step 5: Run W3 tests and verify GREEN.**

Run: `AUTORESEARCH_ENGINE=codex uv run --no-sync python -m pytest -q tests/research/test_w3_grids.py tests/common/test_ruler.py`

- [ ] **Step 6: Commit Task 7 files.**

```bash
git add autoresearch/research/w3_grids.py tests/research/test_w3_grids.py
git commit -m "fix(research): align W3 populations with entry rulers"
```

## Task 8: Enforce registered experiment identity before output

**Files:**
- Create: `autoresearch/research/registration.py`
- Modify: `autoresearch/research/stage_value.py`
- Modify: `autoresearch/research/w3_grids.py`
- Create: `tests/research/test_registration.py`
- Test: `tests/research/test_stage_value.py`
- Test: `tests/research/test_w3_grids.py`

- [ ] **Step 1: Add failing registration unit tests.**

```python
@pytest.mark.parametrize("policy, expected", [
    ("scan_days >= 20", 20),
    ("common.stats.maturity_verdict(scan_days >= 60)", 60),
])
def test_parse_supported_maturity_policy(policy, expected):
    assert parse_maturity_policy(policy) == expected

def test_wrong_manifest_is_rejected_before_output(tmp_path, spec, population):
    spec["input_manifest_hash"] = "0" * 64
    with pytest.raises(ValueError, match="input manifest"):
        stage_value.run(spec_path=write_spec(tmp_path, spec), populations=[population],
                        parent=tmp_path / "out")
    assert not (tmp_path / "out").exists()

def test_out_of_registered_test_window_is_rejected(tmp_path, spec, population):
    population.loc[len(population)] = outside_test_row()
    with pytest.raises(ValueError, match="registered test interval"):
        stage_value.run(spec_path=write_bound_spec(tmp_path, spec, [population]),
                        populations=[population], parent=tmp_path / "out")
```

- [ ] **Step 2: Run registration tests and verify RED.**

- [ ] **Step 3: Implement canonical manifest and strict policy parsing.**

`registration.py` provides `file_manifest(paths, date_slice)`, `manifest_digest(payload)`, `parse_maturity_policy(text)`, `registered_test_range(spec)`, `verify_engine(spec)`, and `verify_code_provenance(spec, behavior_roots)`. Canonical JSON uses sorted keys and paths. Verification runs before `create_experiment_dir`.

- [ ] **Step 4: Bind stage-value runner.**

Reject rows outside the test interval, unsupported evidence modes/cost modes, wrong manifest/engine/code provenance, and unsupported maturity prose. Pass the parsed threshold into `summarize_daily` instead of calling the default `maturity_verdict` policy. Apply registered purge/embargo to overlapping sensitivity labels before estimation.

- [ ] **Step 5: Bind W3 runner.**

Default `since/until` to the registered test range. Reject conflicting CLI overrides. Build the exact lake/source manifest for selected dates, validate its digest, and record declared plus observed identity in `input_manifest.json` and `manifest.json`.

- [ ] **Step 6: Run runner suites and verify GREEN.**

Run: `AUTORESEARCH_ENGINE=codex uv run --no-sync python -m pytest -q tests/research/test_registration.py tests/research/test_stage_value.py tests/research/test_w3_grids.py tests/research/test_experiment_io.py tests/research/test_robustness.py`

- [ ] **Step 7: Commit Task 8 files.**

```bash
git add autoresearch/research/registration.py autoresearch/research/stage_value.py autoresearch/research/w3_grids.py tests/research/test_registration.py tests/research/test_stage_value.py tests/research/test_w3_grids.py
git commit -m "fix(research): enforce registered experiment identity"
```

## Task 9: Correct documentation state and supersede the invalid W3 readout

**Files:**
- Modify: `docs/superpowers/plans/2026-09-06-research-system-implementation-index.md`
- Modify: `docs/superpowers/plans/2026-09-06-execution-evaluation.md`
- Modify: `docs/superpowers/plans/2026-09-06-research-methods-and-stage-value.md`
- Modify: `docs/research/2026-09-07-w3-three-grids-readout.md`
- Create: `docs/research/2026-09-07-w3-three-grids-family-v2.spec.json`
- Create: `docs/research/2026-09-07-w3-three-grids-readout-v2.md`

- [ ] **Step 1: Mark the old W3 result as superseded without changing its frozen machine artifacts.**

Add a top-level note naming the two invalid assumptions: G3 used close eligibility for an open-entry label, and BY used synthetic values rather than p-values. State that its numerical conclusions must not drive candidate selection.

- [ ] **Step 2: Replace stale completion claims with current package states.**

The index must distinguish implemented primitives, completed end-to-end behavior, external-data validation, and approved deferrals. Remove stale “broker not merged” language from current-status sections while retaining dated history as history.

- [ ] **Step 3: Freeze a corrected W3 spec only after Tasks 6-8 are committed.**

Use a new experiment ID, real code provenance, exact canonical input-manifest hash, registered test dates, the corrected population declarations, and the supported maturity grammar. Do not overwrite the 2026-09-07 experiment.

- [ ] **Step 4: Run the corrected local-lake experiment and validate its manifest.**

Run only against `lake/` with `AUTORESEARCH_ENGINE=codex`; do not fetch network data. If required local inputs are absent, publish coverage describing the missing input and do not invent a sign conclusion.

- [ ] **Step 5: Write the new readout from the validated output.**

Report raw p, BY q, population flags, registered window, sample maturity, and sensitivity/main-ruler boundaries. State only the signs and conclusions actually present in the fresh output.

- [ ] **Step 6: Run documentation contract tests and commit.**

Run: `AUTORESEARCH_ENGINE=codex uv run --no-sync python -m pytest -q tests/research/test_w3_grids.py tests/research/test_experiment_io.py tests/contracts/test_artifacts_contract.py tests/test_docs.py`

```bash
git add docs/superpowers/plans/2026-09-06-research-system-implementation-index.md docs/superpowers/plans/2026-09-06-execution-evaluation.md docs/superpowers/plans/2026-09-06-research-methods-and-stage-value.md docs/research
git commit -m "docs(research): correct package status and W3 evidence"
```

## Task 10: Final regression and scope audit

**Files:**
- Verify all files changed by Tasks 1-9

- [ ] **Step 1: Confirm the user-owned pinned file is the only unrelated worktree change.**

Run: `git status --short`

Expected unrelated entry: `M .claude/skills/scan-market/pinned.jsonc` only.

- [ ] **Step 2: Run focused regression suites.**

Run: `AUTORESEARCH_ENGINE=codex uv run --no-sync python -m pytest -q tests/broker tests/common/test_execution_math.py tests/common/test_block_bootstrap.py tests/news/test_claim_support.py tests/news/test_claim_binding.py tests/scan/test_intel_guard.py tests/scan/test_research_card_migration.py tests/trace/test_completeness.py tests/research/test_execution_audit.py tests/research/test_execution_ledger.py tests/research/test_registration.py tests/research/test_stage_value.py tests/research/test_w3_grids.py`

- [ ] **Step 3: Run the complete project suite.**

Run: `AUTORESEARCH_ENGINE=codex uv run --no-sync python -m pytest -q`

Expected: zero failures. Skips are acceptable only when they retain their documented missing-historical-artifact reasons.

- [ ] **Step 4: Audit the design requirements and frozen boundaries.**

Confirm no code reads/writes `context_claude/` or `reports_claude/`, B4 and candidate JSON do not feed verdict/action/BUY, no frozen output was overwritten, and no deferred prompt/runner/dependency work was smuggled into the changes.

- [ ] **Step 5: Confirm every remediation file is committed, then report exact verification evidence.**

Run: `git status --short`

Expected: only the pre-existing user change `M .claude/skills/scan-market/pinned.jsonc` remains.
