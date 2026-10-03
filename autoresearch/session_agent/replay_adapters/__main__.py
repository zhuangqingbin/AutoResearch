"""Subprocess entrypoint for isolated domain replay."""

from __future__ import annotations

import argparse
import json
import traceback
from pathlib import Path


class _Context:
    def __init__(self, request: dict) -> None:
        self.unit = request["unit"]
        self.replay_run_id = request["run_id"]
        self.inputs = Path(request["inputs"])
        self.work = Path(request["work"])
        self.outputs = Path(request["outputs"])
        self.effects = Path(request["effects"])
        self.env = dict(__import__("os").environ)
        self.operation_request = {}
        operation_id = (
            f"operation.request:{self.unit['task_id']}:a{self.unit['attempt']}"
        )
        from autoresearch.session_agent.replay_adapters.common import safe_name

        operation_path = self.inputs / "artifacts" / safe_name(operation_id)
        if operation_path.is_file():
            self.operation_request = json.loads(operation_path.read_text(encoding="utf-8"))
        self.source_receipts = tuple(request.get("source_receipts") or [])

    def output_path(self, artifact_id: str) -> Path:
        from autoresearch.session_agent.replay_adapters.common import safe_name

        target = self.outputs / safe_name(artifact_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    args = parser.parse_args(argv)
    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    result_path = Path(request["result"])
    context = _Context(request)
    try:
        operation = context.unit["operation"]
        if operation == "research.card.facts":
            from autoresearch.session_agent.card_facts import replay as execute
        elif operation.startswith("stock."):
            from autoresearch.session_agent.replay_adapters.stock import execute
        elif operation.startswith("macro."):
            from autoresearch.session_agent.replay_adapters.macro import execute
        elif operation.startswith("sector."):
            from autoresearch.session_agent.replay_adapters.sector import execute
        elif operation.startswith("dossier."):
            from autoresearch.session_agent.replay_adapters.dossier import execute
        elif operation.startswith("scan."):
            from autoresearch.session_agent.replay_adapters.scan import execute
        else:
            raise KeyError(f"no domain replay adapter: {operation}")
        effects = execute(context.unit, context)
        value = {"schema_version": 1, "effects": effects, "error": None}
        exit_code = 0
    except Exception as exc:  # noqa: BLE001 - serialized for expected-failure comparison
        value = {
            "schema_version": 1,
            "effects": [],
            "error": {
                "category": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            },
        }
        exit_code = 1
    result_path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
