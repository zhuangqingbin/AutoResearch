"""Compact, deterministic progress projection for session scan runs."""
from __future__ import annotations

import json
from pathlib import Path


def scan_progress(handle) -> dict:
    scan_dir = Path(handle.staging)
    book_path = scan_dir / "_l4_tasks.json"
    if not book_path.is_file():
        return {"schema_version": 1, "checkpoint": "CP0-CP4", "l4": None, "events": []}
    payload = json.loads(book_path.read_text(encoding="utf-8"))
    tasks = payload.get("tasks") or {}
    counts = {"total": len(tasks), "succeeded": 0, "blocked": 0, "running": 0}
    from autoresearch.scan.l4_watch import load_cursor, snapshot

    for _code, task in sorted(tasks.items()):
        status = str(task.get("status") or "PENDING")
        if status == "SUCCEEDED":
            counts["succeeded"] += 1
        elif status in {"FAILED", "BLOCKED"}:
            counts["blocked"] += 1
        elif status == "RUNNING":
            counts["running"] += 1
    seen = load_cursor(scan_dir)
    snap = snapshot(scan_dir)
    events = [
        {
            "code": item["code"],
            "status": item["status"],
            "reason": item.get("error"),
        }
        for item in snap.get("terminal", [])
        if item.get("event_id", item["code"]) not in seen
    ]
    return {"schema_version": 1, "checkpoint": "CP5", "l4": counts, "events": events}


__all__ = ["scan_progress"]
