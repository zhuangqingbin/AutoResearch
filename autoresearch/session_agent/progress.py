"""Compact, deterministic progress projection for session scan runs."""
from __future__ import annotations

import json
from pathlib import Path


def scan_progress(handle, *, graph_state: str | None = None) -> dict:
    scan_dir = Path(handle.staging)
    book_path = scan_dir / "_l4_tasks.json"
    if not book_path.is_file():
        return {"schema_version": 1, "checkpoint": "CP0-CP4", "l4": None, "events": [],
                "coverage": research_coverage(handle, {}, graph_state=graph_state)}
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
    return {"schema_version": 1, "checkpoint": "CP5", "l4": counts, "events": events,
            "coverage": research_coverage(handle, tasks, graph_state=graph_state)}


__all__ = ["scan_progress"]


def research_coverage(handle, tickets: dict, *, graph_state: str | None = None) -> dict:
    """Diagnostic counts from frozen obligations and accepted results, never admission."""
    import csv
    import io
    import re

    from autoresearch.agents.utils.rating import validate_rating_and_proposal
    from autoresearch.contracts.agent_output import L4_CARD
    from autoresearch.contracts.profiles import STRICT_CARD_RULES
    from autoresearch.scan.l4.card_io import card_rules_version
    from autoresearch.session_agent import artifacts, legacy_scan, service, store

    workspace = getattr(handle, "workspace", None)
    frozen = workspace is not None and (Path(workspace) / "session/plan.json").is_file()
    tasks = service._all_tasks(handle) if frozen else []
    entries = store.read_entries(service._store_path(handle)) if frozen else {}
    owners = [task for task in tasks if task["owner"] == "L4_TASKBOOK"]
    codes = sorted({str(task["subject"]) for task in owners}) if owners else sorted(tickets)
    states = {
        task["task_id"]: (entries.get(task["task_id"], {}).get("state", "PENDING")
                          if task["owner"] == "SESSION" else "PENDING")
        for task in tasks
    }
    if owners and tickets:
        states.update(legacy_scan.ticket_states(handle, owners))
    elif not tasks:
        states = {code: ticket.get("status", "PENDING") for code, ticket in tickets.items()}

    def accepted_json(artifact_id):
        if not frozen:
            return None
        try:
            with artifacts.open_artifact(handle, artifact_id) as stream:
                return json.load(stream)
        except (KeyError, OSError, ValueError, RuntimeError):
            return None

    population_errors = []
    mode = accepted_json("scan.run_mode")
    empty_sentinel = (not owners and (mode or {}).get("mode") == "SENTINEL_EMPTY"
                      and states.get("scan.l4.skip") == "SUCCEEDED")
    population_frozen = bool(owners) or empty_sentinel
    if not population_frozen:
        population_errors.append("POPULATION_NOT_FROZEN")
    pinned = set()
    if frozen and owners:
        try:
            with artifacts.open_artifact(handle, "scan.finalists") as stream:
                rows = list(csv.DictReader(io.StringIO(stream.read().decode("utf-8"))))
                finalist_codes = [str(row["code"]).zfill(6) for row in rows]
                if len(finalist_codes) != len(set(finalist_codes)) or set(finalist_codes) != set(codes):
                    raise ValueError("frozen finalist population does not match the graph")
                pinned = {str(row["code"]).zfill(6) for row in rows if row.get("lane") == "pinned"}
        except (KeyError, OSError, ValueError, RuntimeError):
            population_errors.append("FINALISTS_UNAVAILABLE")
    cards, deep_required, deep_done = {}, set(pinned), set()
    deep_unresolved = set(codes) - pinned
    current_rules = frozen and card_rules_version(handle=handle) in STRICT_CARD_RULES
    for code in codes:
        candidates = [task for task in tasks if task["task_id"].endswith(".card")
                      and task.get("subject") == code]
        if not candidates:
            continue
        task = max(candidates, key=lambda item: (item.get("parent_task") or {}).get("attempt", 0))
        entry = entries.get(task["task_id"], {})
        if entry.get("state") != "SUCCEEDED":
            continue
        try:
            with artifacts.open_artifact(handle, task["output_artifact_ids"][0]) as stream:
                text = stream.read().decode("utf-8")
            rating, _ = validate_rating_and_proposal(text)
        except (KeyError, OSError, ValueError, RuntimeError):
            continue
        cards[code] = task
        deep_unresolved.discard(code)
        if rating in {"Buy", "Overweight"} or re.search(L4_CARD.field("p4_intent").pattern, text):
            deep_required.add(code)
        # Under the current contract successful acceptance requires a bound full
        # deep read for these cards. Legacy receipts do not establish that fact.
        if code in deep_required and current_rules:
            deep_done.add(code)

    if population_errors:
        deep_unresolved.update(codes)
    review_plan = accepted_json("scan.review.plan")
    review_decision = accepted_json("scan.review.decision")
    if frozen:
        from autoresearch.session_agent.workflows.scan import per_stock_reviews
        if per_stock_reviews(service._load_plan(handle)) and owners:
            review_plan = {"reviews": [row for code in codes
                for row in (accepted_json(f"scan.review.plan.{code}") or {}).get("reviews", [])]}
            review_decision = {"decisions": [row for code in codes
                for row in (accepted_json(f"scan.review.decision.{code}") or {}).get("decisions", [])]}
    review_required, review_done = set(), set()
    review_unresolved = set(codes)
    decisions = {row["code"]: row for row in (review_decision or {}).get("decisions", [])}
    for row in (review_plan or {}).get("reviews", []):
        code = row["code"]
        if code not in codes:
            continue
        review_unresolved.discard(code)
        if row.get("trigger") is None:
            continue
        review_required.add(code)
        decision = decisions.get(code)
        if decision is None:
            review_unresolved.add(code)
            continue
        attempt = int(row.get("attempt") or 1)
        required_tasks = [f"l4.{code}.a{attempt}.review2"]
        if decision.get("review3_required"):
            required_tasks.append(f"l4.{code}.a{attempt}.review3")
        if all(states.get(task_id) == "SUCCEEDED" for task_id in required_tasks):
            try:
                for task_id in required_tasks:
                    for artifact_id in entries[task_id]["spec"]["output_artifact_ids"]:
                        artifacts.snapshot_artifact(handle, artifact_id)
            except (KeyError, OSError, ValueError, RuntimeError):
                continue
            review_done.add(code)

    def metric(required, completed, unresolved=()):
        return {"required": len(required), "completed": len(completed & required),
                "missing": sorted(required - completed), "unresolved_conditions": sorted(unresolved)}

    mismatch = sorted(set(codes) ^ set(tickets)) if owners else []
    card_metric = metric(set(codes), set(cards))
    deep_metric = metric(deep_required, deep_done, deep_unresolved)
    review_metric = metric(review_required, review_done, review_unresolved)
    research_complete = (frozen and population_frozen and not population_errors and not mismatch and all(
        not value["missing"] and not value["unresolved_conditions"]
        for value in (card_metric, deep_metric, review_metric)))
    graph_complete = graph_state == "DONE"
    return {
        "population": {"codes": codes, "frozen": population_frozen, "taskbook_mismatch": mismatch,
                       "errors": population_errors},
        "terminal_tasks": {"total": len(states),
                           "terminal": sum(value in {"SUCCEEDED", "SUPERSEDED", "FAILED", "BLOCKED"}
                                           for value in states.values()),
                           "succeeded": sum(value == "SUCCEEDED" for value in states.values())},
        "successful_cards": card_metric, "deep_research": deep_metric, "reviews": review_metric,
        "report_completeness": {"graph_complete": graph_complete,
                                "research_complete": research_complete,
                                "complete": graph_complete and research_complete,
                                "authority": "DIAGNOSTIC_ONLY_USE_VERIFY_REPORT_FOR_DELIVERY"},
    }
