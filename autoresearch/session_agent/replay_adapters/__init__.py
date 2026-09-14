"""Offline adapters for session_v1 deterministic operations."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

from autoresearch.trace.offline import OfflineLayout, run_isolated


class DomainReplayRunner:
    """Launch each domain adapter inside the same system sandbox as replay core."""

    def __init__(
        self,
        capsule: Path | str,
        *,
        denied_write_roots: tuple[Path | str, ...] = (),
        timeout: float = 300.0,
    ) -> None:
        self.capsule = Path(capsule).resolve()
        self.denied_write_roots = denied_write_roots
        self.timeout = timeout

    def __call__(self, unit: dict, context) -> object:
        layout_root = context.code.parent
        layout = OfflineLayout(
            root=layout_root,
            code=context.code,
            inputs=context.inputs.parents[1],
            expected=layout_root / "expected",
            runtime=context.runtime,
            work=layout_root / "work",
            outputs=layout_root / "outputs",
            effects=layout_root / "effects",
            audit=layout_root / "audit",
        )
        request = context.work / "domain_replay_request.json"
        result_path = context.work / "domain_replay_result.json"
        request.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "run_id": json.loads(
                        (self.capsule / "identity/session/plan.json").read_text(encoding="utf-8")
                    )["run_id"],
                    "unit": unit,
                    "source_receipts": list(context.source_receipts),
                    "inputs": str(context.inputs),
                    "work": str(context.work),
                    "outputs": str(context.outputs),
                    "effects": str(context.effects),
                    "result": str(result_path),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        outcome = run_isolated(
            [
                sys.executable,
                "-m",
                "autoresearch.session_agent.replay_adapters",
                "--request",
                str(request),
            ],
            layout,
            env={**context.env, "PYTHONPATH": str(context.code)},
            denied_read_roots=(self.capsule,),
            denied_write_roots=(self.capsule, *self.denied_write_roots),
            timeout=self.timeout,
        )
        details = {}
        if result_path.is_file():
            try:
                details = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                details = {}
        return replace(
            outcome,
            effects=list(details.get("effects") or []),
            error=details.get("error"),
        )


__all__ = ["DomainReplayRunner"]
