"""Configuration-only bootstrap for one immutable ``scan-market`` identity.

This module deliberately stops before every market, macro, lake, or network read.  It
is safe to call while allocating the forensic spool, before the business pipeline is
allowed to fetch data.
"""
from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.scan import user_config as config_module
from autoresearch.scan.artifacts import artifact_schema_versions
from autoresearch.scan.budget import normalize_budgets
from autoresearch.scan.run_contract import RunContract, sha256_json
from autoresearch.scan.user_config import (
    knob,
    load_pinned,
    load_user_config,
    resolve_agent_config,
)
from autoresearch.trace.atomic import canonical_json


def _load_config(config: Mapping | Path | str | None) -> dict:
    if config is None or isinstance(config, (str, Path)):
        return load_user_config(config)
    if not isinstance(config, Mapping):
        raise TypeError("scan config must be a mapping, path, or None")
    # Mapping callers (notably tests and orchestration code that already decoded
    # JSONC) have crossed the loader boundary.  Normalize into plain JSON so no
    # mutable/custom mapping can leak into the immutable contract.
    value = json.loads(canonical_json(dict(config)))
    if not isinstance(value, dict):
        raise TypeError("scan config root must be an object")
    unknown_top = sorted(set(value) - config_module._TOP_WHITELIST)
    if unknown_top:
        raise ValueError(f"scan_config.json 含未知顶层键: {unknown_top}")
    for block_name, allowed in config_module._SUB_WHITELIST.items():
        block = value.get(block_name)
        if isinstance(block, dict):
            unknown = sorted(set(block) - allowed)
            if unknown:
                raise ValueError(
                    f"scan_config.json 的 {block_name} 含未知子键: {unknown}"
                )
    performance = value.get("performance")
    if performance is not None:
        if not isinstance(performance, dict):
            raise ValueError("scan_config.json 的 performance 必须是 object")
        if "streaming_l4" in performance and not isinstance(
            performance["streaming_l4"], bool
        ):
            raise ValueError("scan_config.json performance.streaming_l4 必须是 boolean")
    for (block_name, key), (predicate, wanted) in config_module._KNOB_TYPES.items():
        block = value.get(block_name)
        if isinstance(block, dict) and key in block and not predicate(block[key]):
            raise ValueError(
                f"scan_config.json {block_name}.{key}={block[key]!r} 非法(须为 {wanted})"
            )
    agents = value.get("agents")
    if agents is not None:
        if not isinstance(agents, dict):
            raise ValueError("scan_config.json 的 agents 必须是 object")
        unknown_roles = sorted(set(agents) - config_module._AGENT_ROLES)
        if unknown_roles:
            raise ValueError(f"scan_config.json agents 含未知 role: {unknown_roles}")
        for role, spec in agents.items():
            if spec is not None and not isinstance(spec, dict):
                raise ValueError(f"agents.{role} 必须是 object")
            spec = spec or {}
            unknown = sorted(set(spec) - {"model", "effort"})
            if unknown:
                raise ValueError(f"agents.{role} 含未知子键: {unknown}")
            if "effort" in spec and spec["effort"] not in config_module._EFFORTS:
                raise ValueError(f"agents.{role}.effort={spec['effort']!r} 非法")
            if "model" in spec and spec["model"] not in config_module._MODELS:
                raise ValueError(f"agents.{role}.model={spec['model']!r} 非法")
    return value


def _resolved_user_config(
    config: Mapping | Path | str | None,
    *,
    engine: str,
) -> dict:
    user_config = _load_config(config)
    if user_config:
        user_config = {**user_config, "engine": engine}
    if user_config.get("agents"):
        user_config = {
            **user_config,
            "resolved_agents": resolve_agent_config(user_config),
        }
    return user_config


def _effective_data_policy(
    user_config: dict,
    *,
    cap_floor_yi: float | None,
    include_bj: bool | None,
    source: str | None,
) -> dict:
    return {
        "source": str(knob("l0", "source", source, "tushare", cfg=user_config)),
        "cap_floor_yi": float(
            knob("l0", "cap_floor_yi", cap_floor_yi, 30.0, cfg=user_config)
        ),
        "include_bj": bool(
            knob("l0", "include_bj", include_bj, True, cfg=user_config)
        ),
    }


def _prompt_hashes() -> dict[str, str]:
    try:
        from autoresearch.scan.retention import prompt_hashes

        return prompt_hashes()
    except Exception as exc:  # noqa: BLE001 - missing evidence is recorded honestly
        print(
            f"[bootstrap] prompt_hashes skipped: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return {}


def prepare_scan_run(
    analysis_date: str,
    *,
    config: Mapping | Path | str | None = None,
    run_id: str | None = None,
    engine: str | None = None,
    workspace_path: Path | str | None = None,
    session_ref: str | None = None,
    cap_floor_yi: float | None = None,
    include_bj: bool | None = None,
    source: str | None = None,
    now: datetime | None = None,
    repo_root: Path | str = ".",
    git_sha: str | None = None,
) -> RunContract:
    """Resolve all configuration evidence and build one complete v3 contract.

    ``run_id`` and ``workspace_path`` are supplied together by the capsule allocator.
    Omitting both retains the historical one-shot workspace and generated identity.
    """
    resolved_date = ws.validate_scan_date(analysis_date)
    capsule_mode = run_id is not None
    resolved_engine = ws.ENGINE if engine is None and capsule_mode else (engine or "")
    user_config = _resolved_user_config(config, engine=resolved_engine or ws.ENGINE)
    data_policy = _effective_data_policy(
        user_config,
        cap_floor_yi=cap_floor_yi,
        include_bj=include_bj,
        source=source,
    )
    pinned_config = user_config.get("pinned") or {}
    pinned_cap = int(pinned_config.get("cap", 5))
    pinned_ttl = int(pinned_config.get("ttl_days", 10))
    l3_config = user_config.get("l3") or {}
    pinned = load_pinned(
        resolved_date,
        cap=pinned_cap,
        ttl_days=pinned_ttl,
    )
    budgets = {
        **normalize_budgets(user_config.get("budgets")),
        "l3_finalist_max": int(l3_config.get("finalist_max", 10)),
        "pinned_cap": pinned_cap,
        "pinned_ttl_days": pinned_ttl,
    }
    return RunContract.build(
        analysis_date=resolved_date,
        user_config=user_config,
        pinned=pinned,
        data_policy=data_policy,
        stage_budgets=budgets,
        artifact_schema_versions=artifact_schema_versions(),
        prompt_hashes=_prompt_hashes(),
        run_kind="scan-market",
        engine=resolved_engine,
        workspace_path=workspace_path,
        session_ref=session_ref,
        run_id=run_id,
        now=now,
        repo_root=repo_root,
        git_sha=git_sha,
    )


def resolve_active_scan_contract(
    analysis_date: str,
    *,
    config: Mapping | Path | str | None = None,
    cap_floor_yi: float | None = None,
    include_bj: bool | None = None,
    source: str | None = None,
) -> RunContract:
    """Load and validate the already-allocated active v3 identity, without writes."""
    run_id = ws.active_run_id()
    if run_id is None:
        raise RuntimeError("RunContract v3 requires AUTORESEARCH_RUN_ID")
    try:
        # Lazy import avoids a module cycle: capsule.begin_run calls prepare_scan_run.
        from autoresearch.trace.capsule import require_active_run

        handle = require_active_run(run_id)
    except Exception as exc:
        raise RuntimeError(
            f"RunContract v3 for active run {run_id} is missing or invalid: {exc}"
        ) from exc
    contract = handle.contract
    resolved_date = ws.validate_scan_date(analysis_date)
    if contract.schema_version != 3:
        raise RuntimeError(
            f"RunContract v3 required, got schema_version={contract.schema_version}"
        )
    if contract.analysis_date != resolved_date:
        raise RuntimeError(
            "RunContract v3 analysis_date mismatch: "
            f"expected={resolved_date!r}, actual={contract.analysis_date!r}"
        )
    if contract.engine != ws.ENGINE:
        raise RuntimeError(
            "RunContract v3 engine mismatch: "
            f"expected={ws.ENGINE!r}, actual={contract.engine!r}"
        )
    # ``begin --config-file custom.jsonc`` freezes the effective config into the
    # contract.  A later frame invocation normally supplies no config source, so it
    # must consume that frozen value instead of silently consulting DEFAULT_PATH.
    # Callers that explicitly supply ``config=`` retain the mismatch assertion hook.
    current_config = contract.user_config
    if config is not None:
        current_config = _resolved_user_config(config, engine=contract.engine)
        if sha256_json(current_config) != contract.config_hash:
            raise RuntimeError("RunContract v3 config mismatch with expected scan_config")
    requested_policy = _effective_data_policy(
        current_config,
        cap_floor_yi=cap_floor_yi,
        include_bj=include_bj,
        source=source,
    )
    if requested_policy != contract.data_policy:
        raise RuntimeError(
            "RunContract v3 data policy mismatch: "
            f"expected={contract.data_policy!r}, requested={requested_policy!r}"
        )
    return contract


__all__ = ["prepare_scan_run", "resolve_active_scan_contract"]
