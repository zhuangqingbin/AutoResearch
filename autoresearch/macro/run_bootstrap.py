"""Build the immutable identity for a standalone macro research run."""
from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.common.atomic import canonical_json, sha256_bytes
from autoresearch.common.run_identity import RunContract
from autoresearch.contracts.stages import MACRO_MODES

_CONFIG_KEYS = frozenset({"mode"})
_PROMPT_PATHS = (
    Path(".claude/skills/macro-research/SKILL.md"),
    Path(".claude/skills/macro-research/macro-playbook.md"),
    Path(".claude/agents/macro-brief.md"),
)


def prompt_hashes() -> dict[str, str]:
    return {
        path.as_posix(): sha256_bytes(path.read_bytes())
        for path in _PROMPT_PATHS
        if path.is_file()
    }


def prepare_macro_run(
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
        raise TypeError("macro-research config must be a mapping")
    unknown = sorted(set(config) - _CONFIG_KEYS)
    if unknown:
        raise ValueError(f"macro-research config contains unknown keys: {unknown}")
    mode = str(config.get("mode") or "")
    if mode not in MACRO_MODES:
        raise ValueError(f"macro-research mode={mode!r} invalid")
    if run_id is None or workspace_path is None:
        raise ValueError("macro-research requires run_id and workspace_path")
    resolved_engine = engine or ws.ENGINE
    user_config = json.loads(canonical_json({"mode": mode, "engine": resolved_engine}))
    return RunContract.build(
        analysis_date=ws.validate_scan_date(analysis_date),
        user_config=user_config,
        pinned={},
        data_policy={},
        stage_budgets={},
        artifact_schema_versions={},
        prompt_hashes=prompt_hashes(),
        run_kind="macro-research",
        engine=resolved_engine,
        workspace_path=workspace_path,
        session_ref=session_ref,
        run_id=run_id,
        now=now,
        repo_root=repo_root,
        git_sha=git_sha,
    )


__all__ = ["prepare_macro_run", "prompt_hashes"]
