"""Static deterministic operation registry; no shell or dynamic imports."""
from __future__ import annotations

import re
import sys
from collections.abc import Callable
from copy import deepcopy
from datetime import date

from autoresearch.contracts.operation_replay import (
    OPERATION_REPLAY_CLASSIFICATION,
    operation_replay_classification,
)
from autoresearch.contracts.session_task import require_exact_fields

_TICKER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,31}", re.ASCII)
_ARTIFACT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", re.ASCII)


def _noop(params: dict) -> list[str]:
    require_exact_fields(params, frozenset({"message"}))
    message = params["message"]
    if type(message) is not str or len(message) > 200:
        raise ValueError("invalid noop message")
    return [sys.executable, "-c", "print('ok')", message]


def _stock_harvest(params: dict) -> list[str]:
    require_exact_fields(
        params, frozenset({"ticker", "analysis_date", "asset_type", "peers", "slim"})
    )
    ticker = params["ticker"]
    if type(ticker) is not str or not _TICKER_RE.fullmatch(ticker):
        raise ValueError("invalid ticker")
    try:
        date.fromisoformat(params["analysis_date"])
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid analysis_date") from exc
    if params["asset_type"] not in {"stock", "crypto"}:
        raise ValueError("invalid asset_type")
    peers = params["peers"]
    if type(peers) is not list or any(
        type(peer) is not str or not _TICKER_RE.fullmatch(peer) for peer in peers
    ):
        raise ValueError("invalid peers")
    if type(params["slim"]) is not bool:
        raise ValueError("slim must be boolean")
    argv = [
        "uv", "run", "--no-sync", "python", "-m", "autoresearch.analyze.harvest",
        ticker, params["analysis_date"], params["asset_type"], ",".join(peers),
    ]
    if params["slim"]:
        argv.append("--slim")
    return argv


def _no_params(command: str, params: dict) -> list[str]:
    require_exact_fields(params, frozenset())
    return [
        sys.executable,
        "-m",
        "autoresearch.session_agent.domain_ops",
        command,
    ]


def _research_calculate(params: dict) -> list[str]:
    require_exact_fields(
        params,
        frozenset({"calculator_id", "input_artifact_ids", "parameters"}),
    )
    from autoresearch.common.atomic import canonical_json
    from autoresearch.research.calculations import calculator_ids

    calculator_id = params["calculator_id"]
    if calculator_id not in calculator_ids():
        raise KeyError(f"unregistered calculator: {calculator_id}")
    inputs = params["input_artifact_ids"]
    if (
        not isinstance(inputs, list)
        or not inputs
        or any(not isinstance(item, str) or not _ARTIFACT_RE.fullmatch(item) for item in inputs)
        or len(inputs) != len(set(inputs))
    ):
        raise ValueError("input_artifact_ids must be a non-empty unique artifact list")
    if not isinstance(params["parameters"], dict):
        raise ValueError("calculation parameters must be an object")
    return [
        sys.executable,
        "-m",
        "autoresearch.session_agent.domain_ops",
        "research-calculate",
        "--calculator-id",
        calculator_id,
        "--input-artifact-ids-json",
        canonical_json(inputs),
        "--parameters-json",
        canonical_json(params["parameters"]),
    ]


_OPERATIONS: dict[str, dict[str, object]] = {
    "test.noop": {"builder": _noop, "idempotent": True, "stage": "session"},
    "research.calculate": {
        "builder": _research_calculate,
        "idempotent": True,
        "stage": "calculate",
    },
    "stock.harvest": {"builder": _stock_harvest, "idempotent": True, "stage": "harvest"},
    "stock.validate": {
        "builder": lambda params: _no_params("stock-validate", params),
        "idempotent": True,
        "stage": "card",
    },
    "stock.publish": {
        "builder": lambda params: _no_params("stock-publish", params),
        "idempotent": True,
        "stage": "publish",
    },
    "stock.full.validate": {
        "builder": lambda params: _no_params("stock-full-validate", params),
        "idempotent": True,
        "stage": "assemble",
    },
    "stock.full.assemble": {
        "builder": lambda params: _no_params("stock-full-assemble", params),
        "idempotent": True,
        "stage": "assemble",
    },
    "macro.harvest": {
        "builder": lambda params: _no_params("macro-harvest", params),
        "idempotent": True,
        "stage": "harvest",
    },
    "macro.lite.frame": {
        "builder": lambda params: _no_params("macro-lite-frame", params),
        "idempotent": True,
        "stage": "frame",
    },
    "macro.lite.validate": {
        "builder": lambda params: _no_params("macro-lite-validate", params),
        "idempotent": True,
        "stage": "write",
    },
    "macro.publish": {
        "builder": lambda params: _no_params("macro-publish", params),
        "idempotent": True,
        "stage": "publish",
    },
    "macro.full.validate": {
        "builder": lambda params: _no_params("macro-full-validate", params),
        "idempotent": True,
        "stage": "assemble",
    },
    "macro.full.assemble": {
        "builder": lambda params: _no_params("macro-full-assemble", params),
        "idempotent": True,
        "stage": "assemble",
    },
    "sector.prepare": {
        "builder": lambda params: _no_params("sector-prepare", params),
        "idempotent": True,
        "stage": "prepare",
    },
    "sector.validate": {
        "builder": lambda params: _no_params("sector-validate", params),
        "idempotent": True,
        "stage": "validate",
    },
    "sector.publish": {
        "builder": lambda params: _no_params("sector-publish", params),
        "idempotent": True,
        "stage": "publish",
    },
    "dossier.prefetch": {
        "builder": lambda params: _no_params("dossier-prefetch", params),
        "idempotent": True,
        "stage": "prefetch",
    },
    "dossier.skeleton": {
        "builder": lambda params: _no_params("dossier-skeleton", params),
        "idempotent": True,
        "stage": "skeleton",
    },
    "dossier.validate": {
        "builder": lambda params: _no_params("dossier-validate", params),
        "idempotent": True,
        "stage": "lint",
    },
    "dossier.publish": {
        "builder": lambda params: _no_params("dossier-publish", params),
        "idempotent": True,
        "stage": "publish",
    },
    "scan.frame": {
        "builder": lambda params: _no_params("scan-frame", params),
        "idempotent": True,
        "stage": "frame",
    },
    "scan.prelude": {
        "builder": lambda params: _no_params("scan-prelude", params),
        "idempotent": True,
        "stage": "prelude",
    },
    "scan.gate1": {
        "builder": lambda params: _no_params("scan-gate1", params),
        "idempotent": True,
        "stage": "gate1",
    },
    "scan.sector.prepare": {
        "builder": lambda params: _no_params("scan-sector-prepare", params),
        "idempotent": True,
        "stage": "l3",
    },
    "scan.sector.skip": {
        "builder": lambda params: _no_params("scan-sector-skip", params),
        "idempotent": True,
        "stage": "l3",
    },
    "scan.l3.prepare": {
        "builder": lambda params: _no_params("scan-l3-prepare", params),
        "idempotent": True,
        "stage": "l3",
    },
    "scan.l3.lint": {
        "builder": lambda params: _no_params("scan-l3-lint", params),
        "idempotent": True,
        "stage": "l3",
    },
    "scan.l3.repair.skip": {
        "builder": lambda params: _no_params("scan-l3-repair-skip", params),
        "idempotent": True,
        "stage": "l3",
    },
    "scan.l3.repair.apply": {
        "builder": lambda params: _no_params("scan-l3-repair-apply", params),
        "idempotent": True,
        "stage": "l3",
    },
    "scan.l3.merge": {
        "builder": lambda params: _no_params("scan-l3-merge", params),
        "idempotent": True,
        "stage": "gate2",
    },
    "scan.gate2.skip": {
        "builder": lambda params: _no_params("scan-gate2-skip", params),
        "idempotent": True,
        "stage": "gate2",
    },
    "scan.l4.prepare": {
        "builder": lambda params: _no_params("scan-l4-prepare", params),
        "idempotent": True,
        "stage": "l4-prep",
    },
    "scan.l4.skip": {
        "builder": lambda params: _no_params("scan-l4-skip", params),
        "idempotent": True,
        "stage": "l4",
    },
    "scan.l4.ticket": {
        "builder": lambda params: _no_params("scan-l4-ticket", params),
        "idempotent": True,
        "stage": "l4",
        "subject": True,
    },
    "scan.l4.slim": {
        "builder": lambda params: _no_params("scan-l4-slim", params),
        "idempotent": True,
        "stage": "l4",
        "subject": True,
    },
    "scan.l4.intel.status": {
        "builder": lambda params: _no_params("scan-l4-intel-status", params),
        "idempotent": True,
        "stage": "l4",
        "subject": True,
    },
    "scan.l4.intel.disabled": {
        "builder": lambda params: _no_params("scan-l4-intel-disabled", params),
        "idempotent": True,
        "stage": "l4",
        "subject": True,
    },
    "scan.review.plan": {
        "builder": lambda params: _no_params("scan-review-plan", params),
        "idempotent": True,
        "stage": "l4",
    },
    "scan.review.none": {
        "builder": lambda params: _no_params("scan-review-none", params),
        "idempotent": True,
        "stage": "l4",
        "subject": True,
    },
    "scan.review.decide": {
        "builder": lambda params: _no_params("scan-review-decide", params),
        "idempotent": True,
        "stage": "l4",
    },
    "scan.review.skip": {
        "builder": lambda params: _no_params("scan-review-skip", params),
        "idempotent": True,
        "stage": "l4",
    },
    "scan.review3.skip": {
        "builder": lambda params: _no_params("scan-review3-skip", params),
        "idempotent": True,
        "stage": "l4",
    },
    "scan.l4.finalize": {
        "builder": lambda params: _no_params("scan-l4-finalize", params),
        "idempotent": True,
        "stage": "l4",
        "subject": True,
    },
    "scan.l4.complete": {
        "builder": lambda params: _no_params("scan-l4-complete", params),
        "idempotent": True,
        "stage": "l4",
    },
    "scan.assemble": {
        "builder": lambda params: _no_params("scan-assemble", params),
        "idempotent": True,
        "stage": "assemble",
    },
    "scan.gate4": {
        "builder": lambda params: _no_params("scan-gate4", params),
        "idempotent": True,
        "stage": "gate4",
    },
    "scan.usage": {
        "builder": lambda params: _no_params("scan-usage", params),
        "idempotent": True,
        "stage": "observe",
    },
    "scan.observe": {
        "builder": lambda params: _no_params("scan-observe", params),
        "idempotent": True,
        "stage": "observe",
    },
}

if set(OPERATION_REPLAY_CLASSIFICATION) != set(_OPERATIONS):
    raise RuntimeError("operation replay classifications must partition the registry")

_NO_PARAMS = {"type": "object", "required": [], "additionalProperties": False}
_CATALOG_META: dict[str, dict[str, object]] = {
    "test.noop": {
        "params": {"message": "string<=200"},
        "side_effects": "none; test-only captured process",
        "outputs": ["test fixture output"],
        "callers": ["session-agent tests"],
        "errors": ["INVALID_PARAMS", "OPERATION_FAILED"],
        "limits": "test-only",
        "retained_cli": False,
    },
    "research.calculate": {
        "params": {
            "calculator_id": "registered calculator ID",
            "input_artifact_ids": "non-empty registered artifact ID[]",
            "parameters": "calculator-specific exact object",
        },
        "side_effects": "writes one content-addressed calculation evidence artifact",
        "outputs": ["capsule/evidence/calculations/<calculation_id>.json"],
        "callers": ["bounded calculation child of any workflow task"],
        "errors": ["INVALID_PARAMS", "UNSUPPORTED_CALCULATION", "CALCULATION_FAILED"],
        "limits": "four registered pure calculators; no network, source text, shell, or dynamic import",
        "retained_cli": False,
    },
    "stock.harvest": {
        "params": {
            "ticker": "validated symbol",
            "analysis_date": "YYYY-MM-DD",
            "asset_type": "stock|crypto",
            "peers": "validated symbol[]",
            "slim": "boolean",
        },
        "side_effects": "reads vendors/lake; freezes a typed snapshot; writes active stock staging",
        "outputs": ["stock.slim", "stock.deep", "stock.context", "stock.indicators"],
        "callers": ["stock.harvest"],
        "errors": ["INVALID_PARAMS", "DATA_CONTRACT", "OPERATION_FAILED"],
        "limits": "existing harvester vendor and retry caps",
        "retained_cli": True,
    },
    "stock.validate": {
        "params": _NO_PARAMS,
        "side_effects": "writes card validation in active run",
        "outputs": ["stock.card.validation"],
        "callers": ["stock.validate"],
        "errors": ["DOMAIN_VALIDATION", "ARTIFACT_CONFLICT"],
        "limits": "no network",
        "retained_cli": False,
    },
    "stock.publish": {
        "params": _NO_PARAMS,
        "side_effects": "prepares a run-scoped publication bundle",
        "outputs": ["stock.publication.bundle"],
        "callers": ["stock.publish"],
        "errors": ["DOMAIN_VALIDATION", "ARTIFACT_CONFLICT"],
        "limits": "no network",
        "retained_cli": False,
    },
    "stock.full.validate": {
        "params": _NO_PARAMS,
        "side_effects": "writes full-product validation in active run",
        "outputs": ["stock.full.validation"],
        "callers": ["stock.full.validate"],
        "errors": ["MISSING_PRODUCT", "DOMAIN_VALIDATION"],
        "limits": "no network",
        "retained_cli": False,
    },
    "stock.full.assemble": {
        "params": _NO_PARAMS,
        "side_effects": "runs the legacy assembler into run staging",
        "outputs": ["stock.full.report", "stock.full.manifest", "stock.publication.bundle"],
        "callers": ["stock.assemble"],
        "errors": ["MISSING_PRODUCT", "ASSEMBLY_FAILED"],
        "limits": "no network",
        "retained_cli": True,
    },
    "macro.harvest": {
        "params": _NO_PARAMS,
        "side_effects": "reads macro vendors; freezes a typed snapshot and scan-meta version",
        "outputs": ["macro.data", "macro.global_tape", "macro.scan_meta"],
        "callers": ["macro.harvest"],
        "errors": ["DATA_CONTRACT", "OPERATION_FAILED"],
        "limits": "existing FRED/yfinance/akshare/tushare retry caps",
        "retained_cli": True,
    },
    "macro.lite.frame": {
        "params": _NO_PARAMS,
        "side_effects": "freezes market/state inputs then builds freshness-gated projections",
        "outputs": ["macro.market_pack", "macro.strategist_pack"],
        "callers": ["macro.frame"],
        "errors": ["DATA_CONTRACT", "PROJECTION_FAILED"],
        "limits": "one market-frame build; no L3/L4",
        "retained_cli": True,
    },
    "macro.lite.validate": {
        "params": _NO_PARAMS,
        "side_effects": "writes six-section brief validation",
        "outputs": ["macro.lite.validation"],
        "callers": ["macro.lite.validate"],
        "errors": ["DOMAIN_VALIDATION"],
        "limits": "no network",
        "retained_cli": False,
    },
    "macro.publish": {
        "params": _NO_PARAMS,
        "side_effects": "prepares a run-scoped macro publication bundle",
        "outputs": ["macro.publication.bundle"],
        "callers": ["macro.publish"],
        "errors": ["ARTIFACT_CONFLICT"],
        "limits": "no network",
        "retained_cli": False,
    },
    "macro.full.validate": {
        "params": _NO_PARAMS,
        "side_effects": "validates required sections and every allocation row",
        "outputs": ["macro.full.validation"],
        "callers": ["macro.full.validate"],
        "errors": ["MISSING_PRODUCT", "DOMAIN_VALIDATION"],
        "limits": "no network",
        "retained_cli": False,
    },
    "macro.full.assemble": {
        "params": _NO_PARAMS,
        "side_effects": "assembles report and candidate state inside active run",
        "outputs": ["macro.full.report", "macro.state.candidate", "macro.publication.bundle"],
        "callers": ["macro.assemble"],
        "errors": ["MISSING_PRODUCT", "ASSEMBLY_FAILED"],
        "limits": "no network",
        "retained_cli": True,
    },
    "sector.prepare": {
        "params": _NO_PARAMS,
        "side_effects": "freezes exact scan/brief/readthrough sources then renders one sector pack",
        "outputs": ["sector.input.manifest", "sector.pack", "sector.reuse"],
        "callers": ["sector.*.prepare"],
        "errors": ["DATA_CONTRACT", "UNKNOWN_INDUSTRY"],
        "limits": "no market ranking, L3, or L4",
        "retained_cli": True,
    },
    "sector.validate": {
        "params": _NO_PARAMS,
        "side_effects": "writes terrain/full-report validation",
        "outputs": ["sector.validation"],
        "callers": ["sector.*.validate"],
        "errors": ["DOMAIN_VALIDATION"],
        "limits": "no network",
        "retained_cli": False,
    },
    "sector.publish": {
        "params": _NO_PARAMS,
        "side_effects": "prepares a run-scoped sector publication bundle",
        "outputs": ["sector.publication.bundle"],
        "callers": ["sector.*.publish"],
        "errors": ["ARTIFACT_CONFLICT"],
        "limits": "no network",
        "retained_cli": False,
    },
    "dossier.prefetch": {
        "params": _NO_PARAMS,
        "side_effects": "runs three data legs, freezes their typed result, then renders active staging",
        "outputs": ["dossier.prefetch"],
        "callers": ["dossier.*.prefetch"],
        "errors": ["OPERATION_FAILED"],
        "limits": "each prefetch leg degrades independently",
        "retained_cli": True,
    },
    "dossier.skeleton": {
        "params": _NO_PARAMS,
        "side_effects": "freezes opening target and bounded scan sources; builds skeleton and permissions",
        "outputs": ["dossier.skeleton", "dossier.permissions"],
        "callers": ["dossier.*.skeleton"],
        "errors": ["ALREADY_INITIALIZED", "INVALID_SKELETON"],
        "limits": "no network",
        "retained_cli": True,
    },
    "dossier.validate": {
        "params": _NO_PARAMS,
        "side_effects": "writes candidate lint and byte-protection result",
        "outputs": ["dossier.validation"],
        "callers": ["dossier.*.lint"],
        "errors": ["DOMAIN_VALIDATION", "DETERMINISTIC_SECTION_CHANGED"],
        "limits": "no network",
        "retained_cli": False,
    },
    "dossier.publish": {
        "params": _NO_PARAMS,
        "side_effects": "freezes opening pool version; emits candidate pool, bundle, and CAS effect plan",
        "outputs": ["dossier.pool.candidate", "dossier.publication.bundle"],
        "callers": ["dossier.*.publish"],
        "errors": ["ARTIFACT_CONFLICT"],
        "limits": "no network",
        "retained_cli": False,
    },
    "scan.frame": {
        "params": _NO_PARAMS,
        "side_effects": "builds the original market frame and strategist projection in active scan staging",
        "outputs": ["scan.market.pack", "scan.strategist.pack"],
        "callers": ["scan.frame"],
        "errors": ["DATA_CONTRACT", "PROJECTION_FAILED"],
        "limits": "one frame build; original vendor and lake policies",
        "retained_cli": True,
    },
    "scan.prelude": {
        "params": _NO_PARAMS,
        "side_effects": "runs the original prelude STEP_NAMES pipeline and freezes its portable staging state",
        "outputs": ["scan.prelude.summary", "scan.l2", "scan.prelude.bundle"],
        "callers": ["scan.prelude"],
        "errors": ["DATA_CONTRACT", "PRELUDE_FAILED"],
        "limits": "steps and retries remain owned by scan.prelude",
        "retained_cli": True,
    },
    "scan.gate1": {
        "params": _NO_PARAMS,
        "side_effects": "runs original GATE1 and freezes four-state run_mode",
        "outputs": ["scan.gate1.result", "scan.run_mode"],
        "callers": ["scan.gate1"],
        "errors": ["GATE1_FAILED", "INVALID_BUDGET", "RUN_MODE_FAILED"],
        "limits": "no network",
        "retained_cli": True,
    },
    "scan.sector.prepare": {
        "params": _NO_PARAMS,
        "side_effects": "applies original sector reuse and builds run-scoped sector packs",
        "outputs": ["scan.sector.list", "scan.sector.source.bundle", "scan.sector.*.pack"],
        "callers": ["scan.sector.prepare"],
        "errors": ["SECTOR_PACK_FAILED"],
        "limits": "scan_config sector.max_briefs",
        "retained_cli": True,
    },
    "scan.sector.skip": {
        "params": _NO_PARAMS,
        "side_effects": "records that sector research is not applicable for a sentinel branch",
        "outputs": ["scan.sector.list", "scan.sector.source.bundle"],
        "callers": ["scan.sector.skip"],
        "errors": ["RUN_MODE_FAILED"],
        "limits": "no network",
        "retained_cli": False,
    },
    "scan.l3.prepare": {
        "params": _NO_PARAMS,
        "side_effects": "runs original L3 evidence harvest, triage and compact table preparation",
        "outputs": ["scan.l3.table", "scan.l3.source.bundle"],
        "callers": ["scan.l3.prepare"],
        "errors": ["L3_INPUT_FAILED"],
        "limits": "original pass1 target and candidate contract",
        "retained_cli": True,
    },
    "scan.l3.lint": {
        "params": _NO_PARAMS,
        "side_effects": "runs original judged JSON lint and writes its exact result",
        "outputs": ["scan.l3.validation", "scan.l3.repair.pack", "scan.l3.repair.prompt", "scan.l3.context.bundle"],
        "callers": ["scan.l3.lint"],
        "errors": ["L3_SCHEMA", "L3_EVIDENCE"],
        "limits": "no network; one bounded task attempt",
        "retained_cli": True,
    },
    "scan.l3.repair.skip": {
        "params": _NO_PARAMS,
        "side_effects": "records that the bounded L3 repair was not required",
        "outputs": ["scan.l3.repair.result", "scan.l3.effective.judged"],
        "callers": ["scan.l3.repair.skip"],
        "errors": [],
        "limits": "no network",
        "retained_cli": False,
    },
    "scan.l3.repair.apply": {
        "params": _NO_PARAMS,
        "side_effects": "validates and atomically applies the original narrow L3 repair patch",
        "outputs": ["scan.l3.repair.result", "scan.l3.effective.judged"],
        "callers": ["scan.l3.repair.apply"],
        "errors": ["L3_REPAIR_INVALID"],
        "limits": "one bounded patch; no network",
        "retained_cli": True,
    },
    "scan.l3.merge": {
        "params": _NO_PARAMS,
        "side_effects": "runs original finalist merge and GATE2",
        "outputs": ["scan.finalists", "scan.l3.bench", "scan.gate2.result", "scan.l3.final.bundle"],
        "callers": ["scan.gate2"],
        "errors": ["L3_MERGE_FAILED", "GATE2_FAILED"],
        "limits": "frozen GATE1 l3cap/max_cards (pre-2026-09-26 runs: l4_budget)",
        "retained_cli": True,
    },
    "scan.gate2.skip": {
        "params": _NO_PARAMS,
        "side_effects": "writes frozen pinned/empty finalists and original not-applicable GATE2 record",
        "outputs": ["scan.finalists", "scan.gate2.result", "scan.l3.final.bundle"],
        "callers": ["scan.gate2"],
        "errors": ["RUN_MODE_FAILED"],
        "limits": "sentinel branches only; no network",
        "retained_cli": True,
    },
    "scan.l4.prepare": {
        "params": _NO_PARAMS,
        "side_effects": "runs original L4 producers, writes prompts, initializes the original taskbook",
        "outputs": ["scan.l4.plan", "scan.l4.taskbook", "scan.l4.source.bundle", "scan.l4.*.prompt"],
        "callers": ["scan.l4.prepare"],
        "errors": ["DISPATCH_MISMATCH", "TASKBOOK_INIT_FAILED"],
        "limits": "frozen finalists and original producer limits",
        "retained_cli": True,
    },
    "scan.l4.skip": {
        "params": _NO_PARAMS,
        "side_effects": "records an empty-sentinel L4 and review plan",
        "outputs": ["scan.l4.plan", "scan.review.plan", "scan.l4.source.bundle"],
        "callers": ["scan.l4.skip"],
        "errors": ["RUN_MODE_FAILED"],
        "limits": "SENTINEL_EMPTY only",
        "retained_cli": False,
    },
    "scan.l4.ticket": {
        "params": _NO_PARAMS,
        "side_effects": "external-owner marker; claim delegates to the original L4 taskbook",
        "outputs": ["scan.l4.*.ticket"],
        "callers": ["L4_TASKBOOK owner"],
        "errors": ["STALE_ATTEMPT", "TASK_BLOCKED"],
        "limits": "one whole-stock owner",
        "retained_cli": False,
    },
    "scan.l4.slim": {
        "params": _NO_PARAMS,
        "side_effects": "prepares one verified slim through the original taskbook",
        "outputs": ["scan.l4.*.slim"],
        "callers": ["l4.*.slim"],
        "errors": ["DATA_INTEGRITY", "TASK_BLOCKED"],
        "limits": "original tushare semaphore and retry cap",
        "retained_cli": True,
    },
    "scan.l4.intel.status": {
        "params": _NO_PARAMS,
        "side_effects": "guards, normalizes, and records one web-intel result",
        "outputs": ["scan.l4.*.intel_status", "scan.l4.*.intel_bundle"],
        "callers": ["l4.*.intel_status"],
        "errors": ["INTEL_CONTRACT"],
        "limits": "frozen runtime cap and original hard cap",
        "retained_cli": True,
    },
    "scan.l4.intel.disabled": {
        "params": _NO_PARAMS,
        "side_effects": "records the distinct DISABLED intel state",
        "outputs": ["scan.l4.*.intel_status", "scan.l4.*.intel_bundle"],
        "callers": ["l4.*.intel_status"],
        "errors": ["STATUS_CONTRACT"],
        "limits": "no network",
        "retained_cli": True,
    },
    "scan.review.plan": {
        "params": _NO_PARAMS,
        "side_effects": "derives review triggers from original cards and frozen pinned lanes",
        "outputs": ["scan.review.plan"],
        "callers": ["scan.review.plan"],
        "errors": ["CARD_CONTRACT"],
        "limits": "no network",
        "retained_cli": False,
    },
    "scan.review.none": {
        "params": _NO_PARAMS,
        "side_effects": "records why a ticker needs no ensemble review",
        "outputs": ["scan.l4.*.review_none"],
        "callers": ["scan.review.none.*"],
        "errors": ["IDENTITY_CONFLICT"],
        "limits": "no network",
        "retained_cli": False,
    },
    "scan.review.decide": {
        "params": _NO_PARAMS,
        "side_effects": "compares original and second-review tiers to decide third dispatch",
        "outputs": ["scan.review.decision"],
        "callers": ["scan.review.decide"],
        "errors": ["REVIEW_CONTRACT"],
        "limits": "no network",
        "retained_cli": False,
    },
    "scan.review.skip": {
        "params": _NO_PARAMS,
        "side_effects": "records no review decisions for empty sentinel mode",
        "outputs": ["scan.review.decision"],
        "callers": ["scan.reviews.skip"],
        "errors": ["RUN_MODE_FAILED"],
        "limits": "SENTINEL_EMPTY only",
        "retained_cli": False,
    },
    "scan.review3.skip": {
        "params": _NO_PARAMS,
        "side_effects": "records complete L4 in empty sentinel mode",
        "outputs": ["scan.l4.complete", "scan.l4.final.bundle"],
        "callers": ["scan.review3.skip"],
        "errors": ["RUN_MODE_FAILED"],
        "limits": "SENTINEL_EMPTY only",
        "retained_cli": False,
    },
    "scan.l4.finalize": {
        "params": _NO_PARAMS,
        "side_effects": "writes ensemble/stage evidence and commits original taskbook success",
        "outputs": ["scan.l4.*.ticket"],
        "callers": ["l4.*.finalize"],
        "errors": ["REVIEW_CONTRACT", "TASKBOOK_SUCCESS_FAILED"],
        "limits": "one terminal per whole-stock attempt",
        "retained_cli": True,
    },
    "scan.l4.complete": {
        "params": _NO_PARAMS,
        "side_effects": "verifies every original L4 taskbook ticket succeeded",
        "outputs": ["scan.l4.complete", "scan.l4.final.bundle"],
        "callers": ["scan.l4.complete"],
        "errors": ["TASKBOOK_INCOMPLETE"],
        "limits": "no network",
        "retained_cli": False,
    },
    "scan.assemble": {
        "params": _NO_PARAMS,
        "side_effects": "runs the original scan publisher into a run-scoped candidate directory",
        "outputs": ["scan.report.plan", "scan.report.build.bundle"],
        "callers": ["scan.assemble"],
        "errors": ["ASSEMBLY_FAILED", "REPORT_INCOMPLETE"],
        "limits": "no model calls",
        "retained_cli": True,
    },
    "scan.gate4": {
        "params": _NO_PARAMS,
        "side_effects": "runs and records the original final hard gate",
        "outputs": ["scan.gate4.result"],
        "callers": ["scan.gate4"],
        "errors": ["GATE4_FAILED"],
        "limits": "no network",
        "retained_cli": True,
    },
    "scan.usage": {
        "params": _NO_PARAMS,
        "side_effects": "harvests bound subscription-session usage and reconciles frozen role config",
        "outputs": ["scan.token.usage", "scan.usage.reconcile", "scan.report.used.bundle"],
        "callers": ["scan.usage"],
        "errors": ["UNMEASURED"],
        "limits": "read-only host transcript binding",
        "retained_cli": True,
    },
    "scan.observe": {
        "params": _NO_PARAMS,
        "side_effects": "refreshes original observation/report facts and freezes a publication bundle",
        "outputs": ["scan.publication.bundle", "scan.report.*", "scan.progress.final"],
        "callers": ["scan.observe"],
        "errors": ["OBSERVATION_FAILED", "REPORT_CHANGED"],
        "limits": "no model calls",
        "retained_cli": True,
    },
}

if set(_CATALOG_META) != set(_OPERATIONS):
    raise RuntimeError("operation catalog and executable registry differ")


def operation_spec(operation: str) -> dict:
    try:
        return _OPERATIONS[operation]
    except KeyError as exc:
        raise KeyError(f"unknown operation: {operation}") from exc


def build_argv(operation: str, params: dict, *, subject: str | None = None) -> list[str]:
    spec = operation_spec(operation)
    builder = spec["builder"]
    if not isinstance(params, dict) or not isinstance(builder, Callable):
        raise ValueError("operation params must be an object")
    argv = builder(params)
    if spec.get("subject"):
        if type(subject) is not str or not re.fullmatch(r"[0-9]{6}", subject):
            raise ValueError("six-digit operation subject required")
        argv.extend(["--subject", subject])
    return argv


def operation_catalog() -> dict[str, dict[str, object]]:
    result = {}
    for operation, metadata in _CATALOG_META.items():
        spec = _OPERATIONS[operation]
        result[operation] = {
            **deepcopy(metadata),
            "idempotent": bool(spec["idempotent"]),
            "stage": str(spec["stage"]),
            "replay_classification": replay_classification(operation),
        }
    return result


def replay_classification(operation: str) -> str:
    operation_spec(operation)
    return operation_replay_classification(operation)


__all__ = [
    "build_argv",
    "operation_catalog",
    "operation_spec",
    "replay_classification",
]
