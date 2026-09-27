"""Bind exported host transcript segments to exact session task attempts."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from autoresearch.common.atomic import atomic_write_json, sha256_file
from autoresearch.contracts.forensic import (
    host_evidence_binding_hash,
    validate_host_evidence_binding,
)

_REF_PREFIX = "host-binding:"


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _binding_root(handle) -> Path:
    return Path(handle.capsule) / "agents/session/task_bindings"


def register_main_context(handle, host_profile: dict) -> dict:
    """Freeze the explicitly exported main transcript source, if available."""
    candidates = [
        item.removeprefix("transcript-file:")
        for item in host_profile["evidence_refs"]
        if item.startswith("transcript-file:")
    ]
    if len(candidates) > 1:
        raise ValueError("host profile declares multiple main transcript sources")
    if candidates:
        source = Path(candidates[0])
        if source.is_symlink() or not source.is_file():
            raise ValueError("declared main transcript is not a regular file")
        from autoresearch.trace.transcripts.snapshot import capture_snapshot

        snapshot = capture_snapshot(source, engine=handle.engine)
        main = {
            "status": "REGISTERED",
            "source_path": str(source.resolve()),
            "start_ordinal": (
                0 if snapshot.last_ordinal is None else snapshot.last_ordinal + 1
            ),
        }
    else:
        main = {
            "status": "UNAVAILABLE",
            "source_path": None,
            "start_ordinal": None,
            "reason": "host profile has no transcript-file evidence ref",
        }
    value = {
        "schema_version": 1,
        "engine": handle.engine,
        "run_id": handle.run_id,
        "session_ref": host_profile["session_ref"],
        "main_transcript": main,
    }
    path = Path(handle.capsule) / "identity/session/host_evidence.json"
    if path.is_file() and _read_json(path) != value:
        raise RuntimeError("frozen main host evidence registration changed")
    atomic_write_json(path, value)
    return value


def _task_and_state(handle, task_id: str, attempt: int) -> dict:
    from autoresearch.session_agent import service, store

    task = service._task(handle, task_id)
    if task["owner"] == "SESSION":
        entry = store.read_entry(Path(handle.workspace) / "session/tasks.json", task_id)
        if entry["state"] == "RUNNING" and entry["attempt"] == attempt:
            return task
        from autoresearch.session_agent.evidence import read_abandonment

        # A late transcript of an attempt the runner abandoned (timed out, never
        # accepted) is evidence of that attempt only — never of an accepted result.
        accepted = entry["state"] == "SUCCEEDED" and entry["attempt"] == attempt
        if (
            not accepted
            and 1 <= attempt <= int(entry["attempt"])
            and read_abandonment(handle, task_id, attempt) is not None
        ):
            return task
        raise ValueError("host evidence requires the running task attempt")
    return task


def _existing_binding(handle, task_id: str, attempt: int) -> dict | None:
    root = _binding_root(handle)
    if not root.is_dir():
        return None
    matches = []
    for path in sorted(root.glob("*.json")):
        value = validate_host_evidence_binding(_read_json(path))
        if value["task_id"] == task_id and value["attempt"] == attempt:
            matches.append(value)
    if len(matches) > 1:
        raise RuntimeError("multiple host evidence bindings for one task attempt")
    return matches[0] if matches else None


def _tool_call_ids(handle, invocation_id: str) -> list[str]:
    result = []
    lineage_path = Path(handle.capsule) / "lineage/external_tools.jsonl"
    if lineage_path.is_file():
        for line in lineage_path.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            call_id = item.get("tool_call_id")
            if item.get("invocation_id") == invocation_id and call_id:
                result.append(str(call_id))
    return sorted(set(result))


def capture_main_context(handle) -> dict:
    """Capture the main transcript interval opened by ``begin``."""
    registration_path = Path(handle.capsule) / "identity/session/host_evidence.json"
    registration = _read_json(registration_path)
    main = registration["main_transcript"]
    output_path = Path(handle.capsule) / "evidence/main_host.json"
    if main["status"] != "REGISTERED":
        result = {
            "schema_version": 1,
            "engine": handle.engine,
            "run_id": handle.run_id,
            "status": "MISSING",
            "reason": str(main.get("reason") or "main transcript unavailable"),
            "start_ordinal": None,
            "end_ordinal": None,
            "raw_path": None,
            "archive_sha256": None,
            "normalized_path": None,
            "normalized_sha256": None,
            "tool_call_ids": [],
        }
        atomic_write_json(output_path, result)
        return result
    source = Path(main["source_path"])
    try:
        from autoresearch.trace.capsule import (
            _archive_bound_transcripts,
            bind_transcript_handle,
        )
        from autoresearch.trace.transcripts.snapshot import capture_snapshot

        snapshot = capture_snapshot(source, engine=handle.engine)
        start = int(main["start_ordinal"])
        end = snapshot.last_ordinal
        if end is None or end < start:
            raise RuntimeError("main transcript has no exported rows after begin")
        invocation_id = f"session-main-{handle.run_id}"
        bind_transcript_handle(
            handle,
            source,
            role="main",
            invocation_id=invocation_id,
            subject=None,
            engine=handle.engine,
            start_ordinal=start,
            end_ordinal=end,
            stage="finalize",
            session_ref=registration["session_ref"],
            attempt=1,
        )
        row = _archive_bound_transcripts(handle)
        captured = row.get(invocation_id)
        if not isinstance(captured, dict) or captured.get("status") != "PRESENT":
            raise RuntimeError("main transcript interval could not be archived")
        result = {
            "schema_version": 1,
            "engine": handle.engine,
            "run_id": handle.run_id,
            "status": "PRESENT",
            "reason": None,
            "start_ordinal": start,
            "end_ordinal": end,
            "raw_path": captured["raw"],
            "archive_sha256": captured["archive_sha256"],
            "normalized_path": captured["normalized"],
            "normalized_sha256": sha256_file(
                Path(handle.capsule) / captured["normalized"]
            ),
            "tool_call_ids": _tool_call_ids(handle, invocation_id),
        }
    except Exception as exc:
        result = {
            "schema_version": 1,
            "engine": handle.engine,
            "run_id": handle.run_id,
            "status": "MISSING",
            "reason": f"{type(exc).__name__}:{exc}",
            "start_ordinal": main.get("start_ordinal"),
            "end_ordinal": None,
            "raw_path": None,
            "archive_sha256": None,
            "normalized_path": None,
            "normalized_sha256": None,
            "tool_call_ids": [],
        }
    atomic_write_json(output_path, result)
    return result


def _capsule_subject(task: dict) -> str | None:
    """The ASCII subject the capsule records for this task's agent (N1).

    A display-name subject (a sector brief's 申万一级 industry) maps to the same
    derived key its AGENT_DISPATCHED/COMPLETED events carry (``service._subject_kwargs``);
    the task's own subject stays verbatim in the session-level binding record.
    """
    from autoresearch.session_agent.service import _subject_kwargs
    from autoresearch.trace.capsule import subject_key

    kwargs = _subject_kwargs(task)
    if "subject_display" in kwargs:
        return subject_key(kwargs["subject_display"])
    return kwargs["subject"]


def bind_task_transcript(
    run_id: str,
    task_id: str,
    attempt: int,
    source_path: Path | str,
    *,
    context_ref: str,
    parent_context_ref: str | None,
    session_ref: str,
    start_ordinal: int,
    end_ordinal: int,
    context_source: str,
    handle_loader=None,
) -> dict:
    """Capture one exact exported segment and return its receipt reference."""
    from autoresearch.trace.capsule import (
        _archive_bound_transcripts,
        bind_transcript_handle,
        require_active_run,
    )

    handle = (handle_loader or require_active_run)(run_id)
    if handle.run_id != run_id:
        raise ValueError("host evidence run identity mismatch")
    task = _task_and_state(handle, task_id, attempt)
    if context_source not in {"MAIN", "SUBAGENT", "SHARED"}:
        raise ValueError("host evidence context_source is not attributable")
    if not context_ref or not session_ref:
        raise ValueError("host evidence session/context identity required")
    if context_source == "SUBAGENT" and (
        not parent_context_ref or parent_context_ref == context_ref
    ):
        raise ValueError("subagent evidence requires a distinct parent context")
    source = Path(source_path).resolve(strict=True)
    current = _existing_binding(handle, task_id, attempt)
    if current is not None:
        proposed = (
            str(source), context_ref, parent_context_ref, session_ref,
            start_ordinal, end_ordinal, context_source,
        )
        frozen = (
            current["source_path"], current["context_ref"],
            current["parent_context_ref"], current["session_ref"],
            current["start_ordinal"], current["end_ordinal"],
            current["context_source"],
        )
        if proposed != frozen:
            raise ValueError("conflicting host evidence binding")
        return {
            **current,
            "evidence_ref": f"{_REF_PREFIX}{current['binding_id']}",
        }
    invocation_id = f"session-{task_id}-a{attempt}"
    bind_transcript_handle(
        handle,
        source,
        role=task["role"],
        invocation_id=invocation_id,
        subject=_capsule_subject(task),
        engine=handle.engine,
        start_ordinal=start_ordinal,
        end_ordinal=end_ordinal,
        stage=None,
        session_ref=session_ref,
        attempt=attempt,
    )
    archived = _archive_bound_transcripts(handle)
    row = archived.get(invocation_id)
    if not isinstance(row, dict) or row.get("status") != "PRESENT":
        raise RuntimeError("host transcript segment could not be captured")
    value = {
        "schema_version": 1,
        "engine": handle.engine,
        "run_id": handle.run_id,
        "task_id": task_id,
        "attempt": attempt,
        "role": task["role"],
        "subject": task["subject"],
        "session_ref": session_ref,
        "context_ref": context_ref,
        "parent_context_ref": parent_context_ref,
        "context_source": context_source,
        "invocation_id": invocation_id,
        "start_ordinal": start_ordinal,
        "end_ordinal": end_ordinal,
        "source_path": str(source),
        "source_sha256": row["source_sha256"],
        "archive_sha256": row["archive_sha256"],
        "raw_path": row["raw"],
        "normalized_path": row["normalized"],
        "normalized_sha256": sha256_file(Path(handle.capsule) / row["normalized"]),
        "tool_call_ids": _tool_call_ids(handle, invocation_id),
        "status": "PRESENT",
        "created_at": _now(),
        "binding_id": "0" * 64,
    }
    value["binding_id"] = host_evidence_binding_hash(value)
    validate_host_evidence_binding(value)
    atomic_write_json(_binding_root(handle) / f"{value['binding_id']}.json", value)
    return {**value, "evidence_ref": f"{_REF_PREFIX}{value['binding_id']}"}


def _load_binding_ref(handle, reference: str) -> dict:
    if not reference.startswith(_REF_PREFIX):
        raise ValueError("host receipt contains an unsupported evidence reference")
    binding_id = reference.removeprefix(_REF_PREFIX)
    path = _binding_root(handle) / f"{binding_id}.json"
    if not path.is_file():
        raise ValueError("host receipt evidence binding does not exist")
    value = validate_host_evidence_binding(_read_json(path))
    if value["binding_id"] != binding_id:
        raise ValueError("host receipt evidence binding identity mismatch")
    for field, expected in (
        ("raw_path", value["archive_sha256"]),
        ("normalized_path", value["normalized_sha256"]),
    ):
        target = Path(handle.capsule) / value[field]
        if not target.is_file() or sha256_file(target) != expected:
            raise ValueError("host receipt evidence binding content mismatch")
    return value


def resolve_receipt_evidence(handle, task: dict, receipt: dict) -> list[dict]:
    bindings = [
        _load_binding_ref(handle, reference)
        for reference in receipt["evidence_refs"]
    ]
    matching = [
        item
        for item in bindings
        if item["engine"] == handle.engine
        and item["run_id"] == handle.run_id
        and item["task_id"] == task["task_id"]
        and item["attempt"] == receipt["attempt"]
        and item["role"] == task["role"]
        and item["subject"] == task["subject"]
        and item["session_ref"] == receipt["session_ref"]
        and item["context_ref"] == receipt["context_ref"]
        and item["parent_context_ref"] == receipt["parent_context_ref"]
    ]
    if len(matching) != 1:
        raise ValueError("host receipt has no exact task evidence binding")
    if task["independent_context"] and matching[0]["context_source"] != "SUBAGENT":
        raise ValueError("independent task requires subagent transcript evidence")
    return matching


def transcript_refs_for_task(handle, task_id: str, attempt: int) -> list[dict]:
    binding = _existing_binding(handle, task_id, attempt)
    if binding is None:
        return []
    try:
        _load_binding_ref(handle, f"{_REF_PREFIX}{binding['binding_id']}")
        status = "PRESENT"
        captured_path = binding["raw_path"]
        digest = binding["archive_sha256"]
    except ValueError:
        status, captured_path, digest = "MISSING", None, None
    return [{
        "engine": binding["engine"],
        "status": status,
        "role": binding["role"],
        "subject": binding["subject"],
        "invocation_id": binding["invocation_id"],
        "session_ref": binding["session_ref"],
        "start_ordinal": binding["start_ordinal"],
        "end_ordinal": binding["end_ordinal"],
        "captured_path": captured_path,
        "sha256": digest,
        "context_source": binding["context_source"],
    }]


__all__ = [
    "bind_task_transcript",
    "capture_main_context",
    "register_main_context",
    "resolve_receipt_evidence",
    "transcript_refs_for_task",
]
