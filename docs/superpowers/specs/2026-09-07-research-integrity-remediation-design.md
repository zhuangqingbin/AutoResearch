# Research Integrity Remediation Design

**Date:** 2026-09-07
**Status:** Approved for planning
**Scope:** Repair the twelve review findings without changing trading policy, BUY ownership, or frozen prompt behavior.

## 1. Goal and invariants

This remediation makes broker ingestion, execution evaluation, evidence extraction, structured-card migration, and offline research fail conservatively on ambiguity. It preserves these project invariants:

- `gap_c1_o2` remains the decision ruler: D+1 close entry and D+2 open exit.
- `relative_buy` remains the only final BUY owner; zero BUY days remain valid.
- B4 remains shadow-only. Its output cannot change the existing intel verdict, action, or gate.
- D5 remains frozen. Candidate JSON is not promoted to production authority in this work.
- Sensitivity rulers remain report-only and cannot justify BUY.
- Existing frozen experiment directories are immutable. A corrected readout uses a new experiment ID.
- Codex reads and writes only `context_codex/` and `reports_codex/`; `lake/` remains the sole shared data root.

The work is split into four independently testable packages. Each package may ship after its own regression suite passes; no package depends on changing prompts or connecting to a broker API.

## 2. Package A: broker identity and execution accounting

### 2.1 Stable broker-row identity

`row_hash` must identify the economic source row, not its ordinal position inside the current export file.

For a row with a broker `trade_id`, the within-source identity is the canonical hash of:

```text
source_kind | normalized account | trade_date | trade_id
```

Changing `source_file`, import time, row order, or export range must not change that identity. The raw payload fields are still retained for audit. Reusing the same identity with materially different code, side, date, price, quantity, or amount is an A-grade conflict and rejects that import instead of silently replacing the prior row.

For rows without `trade_id`, use a stable content fingerprint containing account, date/time, code, side, price, quantity, amount, fees, cash balance, and business type. Exact duplicate fingerprints are idempotent. Because two economically distinct fills can be byte-identical when the source supplies no identifier, the importer records a degraded-identity warning rather than claiming perfect multiplicity recovery.

Cross-source reconciliation continues to use the existing natural-key and source-priority rules; the new identity only fixes within-source upsert semantics.

### 2.2 Position episodes and lot allocation

`load_trades` keeps account/code as a grouping key but no longer claims it is one position. Execution evaluation sorts fills by trade date, trade time, and stable fill identity, then applies FIFO lots:

- A buy opens or adds to the current episode.
- A sell consumes open lots proportionally by consumed quantity and carries only the corresponding buy basis and sell proceeds/fees.
- Returning to zero closes the episode; a later buy starts a new episode and receives a new deterministic `position_id`.
- A sell exceeding known open quantity is `UNMATCHED_OPENING_INVENTORY`. It remains in coverage and cannot produce realized-return statistics.
- A partial exit is `PARTIAL_FILL`; remaining quantity, allocated basis, optional mark value, realized P&L, unrealized P&L, and cash P&L remain separate fields.

Fees are allocated by quantity within each partially consumed fill. Missing fees, opening inventory, or required corporate-action data yields an incomplete assessment, never a zero-filled estimate.

`OTHER` rows are not trade legs. Dividend and tax rows are retained as candidate cash distributions and must either be explicitly linked to an episode or reported as unresolved coverage. They are not silently discarded and are not automatically treated as investment return.

### 2.3 Snapshot simulation completion

The snapshot path simulates both legs using an explicit `simulation_qty` supplied by the evaluation request. The runner does not infer position size from a research card and does not invent a default quantity:

- Entry: existing `close_auction_limit_v1` at D+1 close.
- Exit: `open_auction_v1` at D+2 open.
- Costs: when `simulation_qty` is present, both legs pass through the frozen cost policy and include minimum commission. Without it, gross return may be reported but costed return is unknown with `MISSING_SIMULATION_QTY` coverage.
- If D+2 is not yet available, exit is `NOT_DUE`; missing data after it is due is `UNKNOWN`, not `NOT_DUE`.
- A D+2 unsellable open is retained with the exit flag and window-breach state; it is not removed from the denominator.

The assessment schema is extended with quantities and P&L fields already promised by the C design. Report grouping exposes incomplete fees, partial exits, remaining risk, unmatched inventory, and unresolved corporate actions.

## 3. Package B: evidence extraction and structured-card safety

### 3.1 Shadow failure isolation

Each B4 line is isolated. Invalid dates, malformed values, contract failures, and sidecar-write failures become structured shadow diagnostics. `guard_intel` still returns the original `KEPT` or `TRIMMED` result and existing claims-lint result. Shadow failures cannot suppress status generation or mutate the source draft.

### 3.2 Conservative event parsing

Parsing follows phrase semantics rather than the first keyword found:

- Explicit planning markers such as `拟`, `计划`, and `预案` dominate embedded completion nouns such as `完成` unless an affirmative completed construction such as `已完成` or `实施完毕` is present.
- Amount parsing accepts comma-separated Chinese financial numbers only when grouping is valid. `1,000 万元` becomes `10000000`; malformed grouping is unknown with a diagnostic.
- Calendar-invalid dates become unknown with a diagnostic instead of raising.
- Event comparison checks comparability before values: identity, amount unit, amount basis, and effective time must refer to the same observation basis before an amount difference can be `FAIL`.
- Cumulative values at different effective times are `UNKNOWN` unless a source explicitly states a contradiction or reversal.

The heuristic `event_id` remains a correlation hint, not authoritative identity. A later source binding must not use equality of heuristic IDs alone to prove that two events are the same.

### 3.3 Card migration semantics

Markdown conversion treats a gate as `PASS` only when the text contains an explicit recognized positive state. Missing, unrecognized, and `未核` values become `UNKNOWN`; explicit failures remain `FAIL`.

`RunProfile.card_source` is included in the frozen profile payload and restored exactly. Missing historical fields retain the documented `legacy_md` default; unknown stored values fail validation. Candidate JSON remains compare-only until the separately approved D5 migration.

## 4. Package C: executable experiment registration and valid inference

### 4.1 Per-ruler research population

Each return label carries its own eligibility flag. `gap_c1_o2` uses the close-entry population; `fwd_5_oc` and `fwd_10_oc` use the eligibility corresponding to their D+1-open entry. G3 cannot be filtered by D+1-close information.

G1 keeps its registered close-entry rule. G2 continues to report buyable and unbuyable buckets separately, with the unbuyable bucket marked oracle-only. Population exclusions declared by a spec, including board/ST/new-listing rules, must be either applied or rejected as unsupported before calculation.

### 4.2 Real p-values before BY

The literal `0.02/0.5` interval proxy is removed. Each primary registered grid produces a two-sided p-value from its daily equal-weight series:

1. Center the observed daily series under the null mean of zero.
2. Use a deterministic circular moving-block bootstrap with the registered seed and label-aware block length.
3. Compute `(1 + count(abs(bootstrap_mean) >= abs(observed_mean))) / (n_boot + 1)`.
4. Apply BY once to the three preregistered primary hypotheses.

Raw p, adjusted q, block length, bootstrap count, and method are emitted. If sample maturity or a valid daily series is absent, p/q/rejection are null and the family result says `NOT_TESTED`; no synthetic p-value is substituted.

Confidence intervals remain descriptive and are not renamed as multiplicity-corrected significance.

### 4.3 Registration enforcement

Before creating an experiment output directory, each runner builds a canonical input manifest from the exact files and date slices it will consume. It then enforces:

- runtime engine equals `spec.engine`;
- observed manifest digest equals `spec.input_manifest_hash`;
- the selected dates equal the registered test interval and stop rule;
- split, purge, and embargo rules are executed when the experiment consumes overlapping labels;
- maturity is evaluated from the registered policy, not a hard-coded default; the first implementation accepts only the explicit forms `scan_days >= N` and `common.stats.maturity_verdict(scan_days >= N)` and rejects other prose;
- evidence mode and cost model are supported by that runner;
- code provenance matches `spec.code_sha`, or changes since that commit are proven documentation-only. Dirty behavior paths reject the registered run.

Validation occurs before output creation. The output manifest records both declared and observed identities. CLI date overrides can narrow an exploratory run only under a new explicitly exploratory contract; they cannot silently change a registered readout.

The existing W3 directory and Markdown remain immutable historical evidence but receive an explicit invalidation note in the human index. A corrected spec and new experiment ID are created only after the implementation is committed, then recomputed from the local lake without changing production decisions.

## 5. Package D: documentation and state truthfulness

The implementation index becomes the current source of status truth:

- C is described as partial until FIFO episodes, partial exits, snapshot exit, corporate-action coverage, and reporting are implemented.
- D1-D4 is partial until tri-state parsing and `card_source` round-trip pass.
- F6 is partial until per-ruler population and valid family inference are recomputed.
- Historical wave notes stay historical and are labeled as such instead of serving as current status.

Approved deferrals remain explicit and are not reopened: the 80 manually labeled B5 events, real broker/snapshot samples, D5 and native JSON prompt changes, F2/F3 prompt changes, E3, and the eleven registered upward dependency edges.

## 6. Error handling and compatibility

- Existing CSV/JSON columns are preserved where possible; new coverage fields are additive.
- Ambiguous financial data fails closed for statistics but remains visible in coverage output.
- No repair overwrites a frozen experiment or old report.
- No migration reads another engine's context or report roots.
- Existing production scan verdicts remain unchanged by B4 and candidate JSON work.
- A compatibility reader accepts historical raw broker rows, derives the new stable identity in memory, and rewrites them only during an explicit broker merge/upsert operation.

## 7. Verification and acceptance

Each behavior starts with a regression test that fails on the current implementation. Required acceptance cases are:

1. Full broker export followed by an overlapping partial export keeps one copy of a stable `trade_id`.
2. Conflicting payloads for the same stable identity reject the import.
3. Oversells do not produce a return; two round trips become two episodes; partial exits retain remaining risk.
4. Snapshot simulation produces a due D+2 exit and costed return when all inputs exist.
5. Invalid shadow dates cannot fail `guard_intel`; planned completion and comma amounts parse conservatively.
6. Different-as-of cumulative totals do not become automatic contradictions.
7. `未核` gates remain `UNKNOWN`; `card_source` survives a profile round trip.
8. A stock tradable at D+1 open but sealed at D+1 close remains in G3.
9. BY consumes computed p-values; insufficient samples produce `NOT_TESTED`.
10. Out-of-window data, wrong engine, unsupported evidence mode, wrong input hash, and unmet registered maturity policy are rejected or remain immature as declared.
11. Old frozen report hashes do not change.
12. Changed-module regression tests and the full project test suite pass with `AUTORESEARCH_ENGINE=codex`.

The corrected W3 readout is a separate research deliverable. Its sign and investment interpretation are not predetermined by this engineering design.
