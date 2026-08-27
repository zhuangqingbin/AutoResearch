"""Append-only execution events expose tampering instead of hiding it."""
from __future__ import annotations

import json
import os
import re
import stat
from concurrent.futures import ThreadPoolExecutor

import pytest

from autoresearch.trace.atomic import canonical_json
from autoresearch.trace.events import (
    EVENT_SCHEMA_VERSION,
    GENESIS_HASH,
    _event_hash,
    append_event,
    append_guarded_event,
    verify_event_chain,
)

RUN_ID = "20260827T010203456789Z"
OTHER_RUN_ID = "20260827T010203456790Z"
EXPECTED_KEYS = {
    "schema_version",
    "seq",
    "run_id",
    "ts",
    "engine",
    "stage",
    "invocation_id",
    "attempt",
    "subject",
    "event_type",
    "payload",
    "prev_hash",
    "event_hash",
}


def _fields(n: int = 1) -> dict:
    return {
        "run_id": RUN_ID,
        "engine": "codex",
        "stage": "frame",
        "invocation_id": f"inv-{n}",
        "attempt": 1,
        "subject": None,
        "event_type": "COMMAND_COMPLETED",
        "payload": {"exit_code": 0, "ordinal": n},
    }


def _append_test_event(path, n: int):
    return append_event(path, **_fields(n))


def _append_three(path):
    append_event(
        path,
        run_id=RUN_ID,
        engine="codex",
        stage="prelude",
        invocation_id="inv-1",
        attempt=1,
        subject=None,
        event_type="RUN_STARTED",
        payload={},
    )
    append_event(
        path,
        run_id=RUN_ID,
        engine="codex",
        stage="frame",
        invocation_id="inv-2",
        attempt=1,
        subject=None,
        event_type="COMMAND_STARTED",
        payload={"argv": ["python", "-m", "x"]},
    )
    append_event(
        path,
        run_id=RUN_ID,
        engine="codex",
        stage="frame",
        invocation_id="inv-2",
        attempt=1,
        subject=None,
        event_type="COMMAND_COMPLETED",
        payload={"exit_code": 0},
    )


def _rewrite(path, rows):
    path.write_text(
        "\n".join(canonical_json(row) if isinstance(row, dict) else row for row in rows)
        + "\n",
        encoding="utf-8",
    )


def _deep_json(depth: int) -> str:
    value = "0"
    for _ in range(depth):
        value = '{"x":' + value + "}"
    return value


def _replace_payload_json(row: str, replacement: str) -> str:
    parsed = json.loads(row)
    original = '"payload":' + canonical_json(parsed["payload"])
    assert original in row
    return row.replace(original, '"payload":' + replacement, 1)


def _deep_payload(depth: int) -> dict:
    root = {}
    cursor = root
    for _ in range(depth):
        child = {}
        cursor["x"] = child
        cursor = child
    cursor["value"] = 1
    return root


def test_append_writes_canonical_durable_event_and_returns_it(tmp_path):
    path = tmp_path / "nested" / "events.jsonl"

    event = append_event(path, **_fields())

    assert set(event) == EXPECTED_KEYS
    assert event["schema_version"] == EVENT_SCHEMA_VERSION == 1
    assert event["seq"] == 1
    assert event["prev_hash"] == GENESIS_HASH == "0" * 64
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z", event["ts"])
    assert event["event_hash"] == _event_hash(event)
    assert re.fullmatch(r"[0-9a-f]{64}", event["event_hash"])
    assert path.read_text(encoding="utf-8") == canonical_json(event) + "\n"
    assert verify_event_chain(path) == {
        "ok": True,
        "n": 1,
        "error": None,
        "last_hash": event["event_hash"],
    }


def test_append_links_to_the_full_previous_hash(tmp_path):
    path = tmp_path / "events.jsonl"
    first = _append_test_event(path, 1)
    second = _append_test_event(path, 2)

    assert second["seq"] == 2
    assert second["prev_hash"] == first["event_hash"]
    assert len(second["prev_hash"]) == 64


def test_guarded_append_checks_and_appends_under_one_lock(tmp_path):
    path = tmp_path / "events.jsonl"
    fields = _fields()

    def idempotent_guard(existing, proposed):
        for event in existing:
            if event["invocation_id"] == proposed["invocation_id"]:
                if all(event[key] == proposed[key] for key in proposed):
                    return event
                raise ValueError("conflicting invocation")
        return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda _: append_guarded_event(
                    path, guard=idempotent_guard, **fields
                ),
                range(8),
            )
        )

    assert len({event["event_hash"] for event in results}) == 1
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_event_chain_detects_delete_insert_reorder_and_edit(tmp_path, subtests):
    original = tmp_path / "events.jsonl"
    _append_three(original)
    rows = original.read_text(encoding="utf-8").splitlines()
    mutations = {
        "delete": [rows[0], rows[2]],
        "insert": [rows[0], json.dumps({"seq": 2}), rows[1], rows[2]],
        "reorder": [rows[1], rows[0], rows[2]],
        "edit": [rows[0], rows[1].replace("frame", "l4"), rows[2]],
    }
    for name, changed in mutations.items():
        with subtests.test(name=name):
            path = tmp_path / f"{name}.jsonl"
            path.write_text("\n".join(changed) + "\n", encoding="utf-8")
            assert verify_event_chain(path)["ok"] is False


def test_concurrent_append_produces_contiguous_sequence(tmp_path):
    path = tmp_path / "events.jsonl"
    with ThreadPoolExecutor(max_workers=8) as pool:
        written = list(pool.map(lambda n: _append_test_event(path, n), range(80)))

    result = verify_event_chain(path)
    assert result == {
        "ok": True,
        "n": 80,
        "error": None,
        "last_hash": result["last_hash"],
    }
    assert sorted(event["seq"] for event in written) == list(range(1, 81))
    assert len(path.read_text(encoding="utf-8").splitlines()) == 80


@pytest.mark.parametrize(
    ("content", "detail"),
    [
        ("{\n", "invalid JSON"),
        ("[]\n", "event must be an object"),
        ("\n", "blank event"),
        ('{"seq":1,"seq":2}\n', "duplicate key"),
    ],
)
def test_verifier_reports_malformed_json_and_shape(tmp_path, content, detail):
    path = tmp_path / "events.jsonl"
    path.write_text(content, encoding="utf-8")

    result = verify_event_chain(path)

    assert result["ok"] is False
    assert result["n"] == 0
    assert result["last_hash"] == GENESIS_HASH
    assert result["error"].startswith("line 1:")
    assert detail in result["error"]


def test_verifier_reports_first_failure_and_last_reliable_hash(tmp_path):
    path = tmp_path / "events.jsonl"
    _append_three(path)
    rows = path.read_text(encoding="utf-8").splitlines()
    rows[1] = rows[1].replace('"stage":"frame"', '"stage":"l4"')
    rows[2] = "{"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    result = verify_event_chain(path)

    first = json.loads(rows[0])
    assert result["ok"] is False
    assert result["n"] == 1
    assert result["last_hash"] == first["event_hash"]
    assert result["error"].startswith("line 2:")


@pytest.mark.parametrize(
    ("mutate", "detail"),
    [
        (lambda row: row.update(schema_version=2), "schema_version"),
        (lambda row: row.update(seq=2), "seq"),
        (lambda row: row.update(prev_hash="f" * 64), "prev_hash"),
        (lambda row: row.update(event_hash="f" * 64), "event_hash"),
        (lambda row: row.pop("payload"), "fields"),
        (lambda row: row.update(unexpected=True), "fields"),
    ],
)
def test_verifier_rejects_wrong_schema_sequence_hashes_or_fields(
    tmp_path, mutate, detail
):
    path = tmp_path / "events.jsonl"
    event = _append_test_event(path, 1)
    row = dict(event)
    mutate(row)
    if detail != "event_hash":
        row["event_hash"] = _event_hash(row)
    _rewrite(path, [row])

    result = verify_event_chain(path)

    assert result["ok"] is False
    assert result["n"] == 0
    assert result["error"].startswith("line 1:")
    assert detail in result["error"]


def test_verifier_rejects_a_hash_valid_event_from_another_run(tmp_path):
    path = tmp_path / "events.jsonl"
    first = _append_test_event(path, 1)
    second = _append_test_event(path, 2)
    second["run_id"] = OTHER_RUN_ID
    second["event_hash"] = _event_hash(second)
    _rewrite(path, [first, second])

    result = verify_event_chain(path)

    assert result["ok"] is False
    assert result["n"] == 1
    assert result["error"].startswith("line 2:")
    assert "run_id" in result["error"]


def test_append_rejects_a_different_run_without_extending_valid_log(tmp_path):
    path = tmp_path / "events.jsonl"
    _append_test_event(path, 1)
    before = path.read_bytes()
    fields = _fields(2)
    fields["run_id"] = OTHER_RUN_ID

    with pytest.raises(ValueError, match="invalid existing event chain.*run_id"):
        append_event(path, **fields)

    assert path.read_bytes() == before
    assert verify_event_chain(path)["ok"] is True


def test_verifier_rejects_a_hash_valid_engine_change_on_later_event(tmp_path):
    path = tmp_path / "events.jsonl"
    first = _append_test_event(path, 1)
    second = _append_test_event(path, 2)
    second["engine"] = "claude"
    second["event_hash"] = _event_hash(second)
    _rewrite(path, [first, second])

    result = verify_event_chain(path)

    assert result["ok"] is False
    assert result["n"] == 1
    assert result["last_hash"] == first["event_hash"]
    assert result["error"].startswith("line 2:")
    assert "engine" in result["error"]


def test_append_rejects_a_different_engine_without_extending_valid_log(tmp_path):
    path = tmp_path / "events.jsonl"
    _append_test_event(path, 1)
    before = path.read_bytes()
    fields = _fields(2)
    fields["engine"] = "claude"

    with pytest.raises(ValueError, match="invalid existing event chain.*engine"):
        append_event(path, **fields)

    assert path.read_bytes() == before
    assert verify_event_chain(path)["ok"] is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_id", "not-a-run"),
        ("engine", ""),
        ("engine", "other"),
        ("stage", ""),
        ("invocation_id", 3),
        ("attempt", 0),
        ("attempt", True),
        ("subject", 603259),
        ("event_type", ""),
        ("payload", []),
        ("payload", {"bad": {1, 2}}),
        ("payload", {"nan": float("nan")}),
        ("payload", {"bad_unicode": "\ud800"}),
    ],
)
def test_append_rejects_fields_that_cannot_be_reliably_replayed(
    tmp_path, field, value
):
    path = tmp_path / "events.jsonl"
    fields = _fields()
    fields[field] = value

    with pytest.raises((TypeError, ValueError), match=field):
        append_event(path, **fields)

    assert not path.exists()


@pytest.mark.parametrize(
    "reserved",
    ("schema_version", "seq", "ts", "prev_hash", "event_hash"),
)
def test_callers_cannot_override_chain_owned_fields(tmp_path, reserved):
    path = tmp_path / "events.jsonl"
    fields = _fields()
    fields[reserved] = "caller-controlled"

    with pytest.raises(ValueError, match=f"reserved.*{reserved}"):
        append_event(path, **fields)

    assert not path.exists()


def test_append_rejects_unknown_semantic_fields(tmp_path):
    path = tmp_path / "events.jsonl"
    fields = _fields()
    fields["surprise"] = "not in schema"

    with pytest.raises(ValueError, match="unexpected.*surprise"):
        append_event(path, **fields)

    assert not path.exists()


@pytest.mark.parametrize(
    "corrupt",
    (
        "{\n",
        "\n",
        canonical_json({"schema_version": 1}) + "\n",
    ),
)
def test_append_never_silently_extends_a_corrupt_log(tmp_path, corrupt):
    path = tmp_path / "events.jsonl"
    path.write_text(corrupt, encoding="utf-8")
    before = path.read_bytes()

    with pytest.raises(ValueError, match="invalid existing event chain"):
        _append_test_event(path, 2)

    assert path.read_bytes() == before


@pytest.mark.parametrize("bad_line", (1, 2))
def test_verifier_rejects_excessive_json_nesting_at_first_or_later_line(
    tmp_path, bad_line
):
    path = tmp_path / "events.jsonl"
    _append_test_event(path, 1)
    if bad_line == 2:
        _append_test_event(path, 2)
    rows = path.read_text(encoding="utf-8").splitlines()
    rows[bad_line - 1] = _replace_payload_json(rows[bad_line - 1], _deep_json(80))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    result = verify_event_chain(path)

    expected_last_hash = GENESIS_HASH if bad_line == 1 else json.loads(rows[0])["event_hash"]
    assert result["ok"] is False
    assert result["n"] == bad_line - 1
    assert result["last_hash"] == expected_last_hash
    assert result["error"].startswith(f"line {bad_line}:")
    assert "nesting" in result["error"]


def test_verifier_converts_json_decoder_recursion_into_structured_failure(tmp_path):
    path = tmp_path / "events.jsonl"
    event = _append_test_event(path, 1)
    row = _replace_payload_json(canonical_json(event), _deep_json(2000))
    path.write_text(row + "\n", encoding="utf-8")

    result = verify_event_chain(path)

    assert result["ok"] is False
    assert result["n"] == 0
    assert result["last_hash"] == GENESIS_HASH
    assert result["error"].startswith("line 1:")
    assert "nesting" in result["error"]


def test_append_rejects_deep_payload_before_creating_target(tmp_path):
    path = tmp_path / "events.jsonl"
    fields = _fields()
    fields["payload"] = _deep_payload(80)

    with pytest.raises(ValueError, match="payload.*nesting"):
        append_event(path, **fields)

    assert not path.exists()


def test_append_rejects_cyclic_payload_without_mutating_existing_log(tmp_path):
    path = tmp_path / "events.jsonl"
    _append_test_event(path, 1)
    before = path.read_bytes()
    cycle = {}
    cycle["self"] = cycle
    fields = _fields(2)
    fields["payload"] = cycle

    with pytest.raises(ValueError, match="payload.*cyclic"):
        append_event(path, **fields)

    assert path.read_bytes() == before


def test_append_rejects_a_torn_final_line_without_extending_it(tmp_path):
    path = tmp_path / "events.jsonl"
    event = _append_test_event(path, 1)
    path.write_text(canonical_json(event), encoding="utf-8")
    before = path.read_bytes()

    with pytest.raises(ValueError, match="invalid existing event chain"):
        _append_test_event(path, 2)

    assert path.read_bytes() == before
    assert "newline" in verify_event_chain(path)["error"]


def test_empty_and_missing_logs_have_a_stable_genesis_result(tmp_path):
    expected = {"ok": True, "n": 0, "error": None, "last_hash": GENESIS_HASH}
    missing = tmp_path / "missing.jsonl"
    empty = tmp_path / "empty.jsonl"
    empty.touch()

    assert verify_event_chain(missing) == expected
    assert verify_event_chain(empty) == expected


def test_fsync_failure_releases_lock_and_closes_handle(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"

    def fail_fsync(_fd):
        raise OSError("disk sync failed")

    monkeypatch.setattr(os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="disk sync failed"):
        _append_test_event(path, 1)
    monkeypatch.undo()

    second = _append_test_event(path, 2)
    assert second["seq"] == 2
    assert verify_event_chain(path)["ok"] is True


def test_first_append_fsyncs_file_then_parent_directory(tmp_path, monkeypatch):
    path = tmp_path / "new" / "events.jsonl"
    fsync_targets = []
    real_fsync = os.fsync

    def recording_fsync(fd):
        fsync_targets.append("directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", recording_fsync)
    _append_test_event(path, 1)

    assert fsync_targets == ["file", "directory"]
    fsync_targets.clear()
    _append_test_event(path, 2)
    assert fsync_targets == ["file", "directory"]


def test_parent_directory_fsync_failure_is_observable_after_event_write(
    tmp_path, monkeypatch
):
    path = tmp_path / "new" / "events.jsonl"
    real_fsync = os.fsync

    def fail_directory_fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("event directory sync failed")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_directory_fsync)
    with pytest.raises(OSError, match="event directory sync failed"):
        _append_test_event(path, 1)

    assert path.exists()
    assert list(path.parent.glob("*.tmp")) == []
    assert verify_event_chain(path)["ok"] is True


def test_verifier_rejects_non_utf8_bytes_without_raising(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_bytes(b"\xff\n")

    result = verify_event_chain(path)

    assert result["ok"] is False
    assert result["n"] == 0
    assert "UTF-8" in result["error"]


def test_verifier_reports_non_utf8_after_the_last_reliable_event(tmp_path):
    path = tmp_path / "events.jsonl"
    first = _append_test_event(path, 1)
    with path.open("ab") as handle:
        handle.write(b"\xff\n")

    result = verify_event_chain(path)

    assert result["ok"] is False
    assert result["n"] == 1
    assert result["last_hash"] == first["event_hash"]
    assert result["error"].startswith("line 2: invalid UTF-8")


def test_append_fails_closed_on_non_utf8_existing_log(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_bytes(b"\xff\n")
    before = path.read_bytes()

    with pytest.raises(ValueError, match="invalid existing event chain"):
        _append_test_event(path, 1)

    assert path.read_bytes() == before


def test_verifier_rejects_invalid_semantic_types_even_with_valid_hash(tmp_path):
    path = tmp_path / "events.jsonl"
    row = _append_test_event(path, 1)
    row["attempt"] = True
    row["event_hash"] = _event_hash(row)
    _rewrite(path, [row])

    result = verify_event_chain(path)

    assert result["ok"] is False
    assert result["n"] == 0
    assert "attempt" in result["error"]


def test_append_preserves_unicode_payload_exactly(tmp_path):
    path = tmp_path / "events.jsonl"
    fields = _fields()
    fields["payload"] = {"message": "最完整的现场"}

    event = append_event(path, **fields)

    assert "最完整的现场" in path.read_text(encoding="utf-8")
    assert verify_event_chain(path)["last_hash"] == event["event_hash"]


def test_unicode_line_separators_remain_inside_one_json_event(tmp_path):
    path = tmp_path / "events.jsonl"
    fields = _fields()
    fields["payload"] = {"message": "one\u2028two\u0085three"}

    event = append_event(path, **fields)

    assert verify_event_chain(path) == {
        "ok": True,
        "n": 1,
        "error": None,
        "last_hash": event["event_hash"],
    }
