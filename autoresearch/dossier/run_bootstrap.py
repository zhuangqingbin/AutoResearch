"""Build the immutable identity for one dossier initialization run."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.common.atomic import canonical_json, sha256_bytes
from autoresearch.common.run_identity import RunContract

_CONFIG_KEYS = frozenset({"mode", "code", "name"})
_CODE_RE = re.compile(r"[0-9]{6}")
_PROMPT_PATHS = (
    Path(".claude/agents/dossier-init.md"),
    Path(".claude/workflows/dossier-init.js"),
)


def prompt_hashes() -> dict[str, str]:
    return {
        path.as_posix(): sha256_bytes(path.read_bytes())
        for path in _PROMPT_PATHS
        if path.is_file()
    }


def prepare_dossier_run(
    analysis_date: str,
    *,
    config: Mapping | None = None,
    run_id: str | None = None,
    engine: str | None = None,
    workspace_path: Path | str | None = None,
    session_ref: str | None = None,
    now: datetime | None = None,
    repo_root: Path | str = ".",
    git_sha: str | None = None,
) -> RunContract:
    if not isinstance(config, Mapping):
        raise TypeError("dossier-init config must be a mapping")
    unknown = sorted(set(config) - _CONFIG_KEYS)
    if unknown:
        raise ValueError(f"dossier-init config contains unknown keys: {unknown}")
    code = str(config.get("code") or "")
    if config.get("mode") != "INIT" or not _CODE_RE.fullmatch(code):
        raise ValueError("dossier-init requires INIT mode and a six-digit code")
    if run_id is None or workspace_path is None:
        raise ValueError("dossier-init requires run_id and workspace_path")
    resolved_engine = engine or ws.ENGINE
    user_config = {
        "mode": "INIT",
        "code": code,
        "engine": resolved_engine,
        **({"name": str(config["name"])} if config.get("name") else {}),
    }
    user_config = json.loads(canonical_json(user_config))
    return RunContract.build(
        analysis_date=ws.validate_scan_date(analysis_date),
        user_config=user_config,
        pinned={},
        data_policy={},
        stage_budgets={},
        artifact_schema_versions={},
        prompt_hashes=prompt_hashes(),
        run_kind="dossier-init",
        engine=resolved_engine,
        workspace_path=workspace_path,
        session_ref=session_ref,
        run_id=run_id,
        now=now,
        repo_root=repo_root,
        git_sha=git_sha,
    )


__all__ = ["prepare_dossier_run", "prompt_hashes"]
