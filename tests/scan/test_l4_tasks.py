"""L4 单票任务簿：可恢复、失败隔离、限次重试和稳定批次。"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from autoresearch.common import workspace as ws
from autoresearch.scan.l4_tasks import (
    dispatch_batches,
    initialize,
    mark_failure,
    mark_success,
    preflight,
    prepare_slim,
    reconcile,
)
from autoresearch.trace.capsule import begin_run

DATE = "2026-07-28"
NOW = datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc)
TRACE_NOW = datetime(2026, 7, 28, 8, 0, 0, 123456, tzinfo=timezone.utc)


def _files(tmp_path, code: str, ticker: str) -> None:
    scan = tmp_path / DATE
    (scan / "details").mkdir(parents=True, exist_ok=True)
    (scan / f"_l4_prompt_{code}.md").write_text("# prompt", encoding="utf-8")
    ctx = tmp_path / "context"
    ctx.mkdir(exist_ok=True)
    (ctx / f"{ticker}_{DATE}_slim.md").write_text(
        "\n".join([
            "## Verified market snapshot",
            "### Latest verified OHLCV row",
            "| Close | 12.34 |",
            "## Market context",
            "## Fundamentals overview",
            "x" * 5000,
        ]),
        encoding="utf-8",
    )
    (scan / "details" / f"{code}.md").write_text("# card", encoding="utf-8")


def _book(tmp_path, codes=("000001", "000002", "000003")):
    meta = {
        "000001": {"ticker": "000001.SZ", "pinned": False},
        "000002": {"ticker": "000002.SZ", "pinned": True},
        "000003": {"ticker": "000003.SZ", "pinned": False},
    }
    scan = tmp_path / DATE
    scan.mkdir(parents=True, exist_ok=True)
    for c in codes:
        (scan / f"_l4_prompt_{c}.md").write_text("# 任务包\n", encoding="utf-8")
    return initialize(
        DATE,
        list(codes),
        root=tmp_path,
        context_root=tmp_path / "context",
        meta=meta,
        now=NOW,
    )


def _traced_book(tmp_path, monkeypatch, code="000001"):
    monkeypatch.setattr(ws, "ENGINE", "codex")
    monkeypatch.setattr(ws, "context_root", lambda: tmp_path / "context_codex")
    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path / "reports_codex")
    monkeypatch.setattr(
        "autoresearch.scan.user_config.DEFAULT_PINNED_PATH",
        tmp_path / "missing-pinned.jsonc",
    )
    handle = begin_run("scan-market", DATE, "codex", {}, now=TRACE_NOW)
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", handle.run_id)
    scan = handle.staging
    inputs = ws.scan_input_dir(DATE, scan_dir=scan)
    (scan / "details").mkdir(parents=True, exist_ok=True)
    inputs.mkdir(parents=True, exist_ok=True)
    (scan / f"_l4_prompt_{code}.md").write_text("# prompt\n", encoding="utf-8")
    ticker = f"{code}.SZ"
    (inputs / f"{ticker}_{DATE}_slim.md").write_text(
        "\n".join([
            "## Verified market snapshot",
            "### Latest verified OHLCV row",
            "| Close | 12.34 |",
            "## Market context",
            "## Fundamentals overview",
            "x" * 5000,
        ]),
        encoding="utf-8",
    )
    (scan / "details" / f"{code}.md").write_text("# card\n", encoding="utf-8")
    book = initialize(
        DATE,
        [code],
        root=handle.workspace / "staging",
        context_root=inputs,
        meta={code: {"ticker": ticker}},
        now=NOW,
    )
    return handle, Path(book["path"])


def _trace_events(handle, code="000001"):
    path = handle.capsule / "events/events.jsonl"
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("subject") == code
    ]


def _persisted_book_bytes(payload: dict) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def test_initialize_is_atomic_and_preserves_order(tmp_path):
    result = _book(tmp_path)
    path = tmp_path / DATE / "_l4_tasks.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert result["path"] == str(path)
    assert list(payload["tasks"]) == ["000001", "000002", "000003"]
    assert payload["tasks"]["000002"]["pinned"] is True
    assert payload["tasks"]["000001"]["status"] == "PENDING"
    assert not path.with_name("_l4_tasks.json.tmp").exists()


def test_initialize_defaults_to_run_scoped_slim_and_hashes_it(tmp_path, monkeypatch):
    from autoresearch.common import workspace as ws

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ws, "ENGINE", "codex")
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", "20260827T010203456789Z")
    scan = ws.scan_dir(DATE)
    inputs = ws.scan_input_dir(DATE)
    scan.joinpath("details").mkdir(parents=True)
    inputs.mkdir(parents=True)
    scan.joinpath("_l4_prompt_000001.md").write_text("# prompt", encoding="utf-8")
    slim = inputs / f"000001.SZ_{DATE}_slim.md"
    slim.write_text(
        "\n".join([
            "## Verified market snapshot",
            "### Latest verified OHLCV row",
            "| Close | 12.34 |",
            "## Market context",
            "## Fundamentals overview",
            "x" * 5000,
        ]),
        encoding="utf-8",
    )
    scan.joinpath("details/000001.md").write_text("# card", encoding="utf-8")

    book = initialize(DATE, ["000001"], now=NOW)
    # This path-layout test uses a synthetic run id, not a valid forensic capsule.
    monkeypatch.delenv("AUTORESEARCH_RUN_ID")
    preflight(book["path"], "000001", now=NOW)
    mark_success(book["path"], "000001", expected_attempt=1, now=NOW)
    payload = json.loads((scan / "_l4_tasks.json").read_text(encoding="utf-8"))
    slim_ref = payload["tasks"]["000001"]["artifacts"]["slim"]

    assert slim_ref["path"] == str(slim)
    assert slim_ref["content_hash"] == hashlib.sha256(slim.read_bytes()).hexdigest()


def test_initialize_rejects_traversal_date_before_explicit_context_write(tmp_path):
    declared_root = tmp_path / "declared"
    outside = tmp_path / "outside"
    outside.mkdir()
    outside.joinpath("_l4_prompt_000001.md").write_text("# prompt", encoding="utf-8")

    with pytest.raises(ValueError, match="scan date"):
        initialize(
            "../outside",
            ["000001"],
            root=declared_root,
            context_root=tmp_path / "explicit_context",
            now=NOW,
        )

    assert not outside.joinpath("_l4_tasks.json").exists()


def test_l4_task_cli_book_path_rejects_traversal_date(tmp_path):
    from autoresearch.scan import l4_tasks

    with pytest.raises(ValueError, match="scan date"):
        l4_tasks._book_path("../outside", str(tmp_path / "declared"))


def test_l4_tasks_cli_init_validates_date_before_dispatch_read(tmp_path):
    from autoresearch.scan import l4_tasks

    declared = tmp_path / "declared"
    declared.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    outside.joinpath("finalists.csv").write_bytes(b"\xff")
    caps = json.dumps({"tushare": 1, "web_search": 1, "web_fetch": 1, "l4_stock": 1})

    with pytest.raises(ValueError, match="scan date"):
        l4_tasks.main([
            "init",
            "../outside",
            "--root",
            str(declared),
            "--caps-json",
            caps,
        ])


def test_success_is_skipped_only_while_all_artifact_hashes_match(tmp_path):
    book = _book(tmp_path, ("000001",))
    _files(tmp_path, "000001", "000001.SZ")

    assert preflight(book["path"], "000001", now=NOW)["action"] == "RUN"
    mark_success(book["path"], "000001", now=NOW)
    assert preflight(book["path"], "000001", now=NOW)["action"] == "SKIP"

    (tmp_path / DATE / "details" / "000001.md").write_text(
        "# changed card", encoding="utf-8"
    )
    changed = preflight(book["path"], "000001", now=NOW)
    assert changed["action"] == "BLOCKED"
    assert changed["attempt"] == 1
    assert changed["reason"] == "ARTIFACT_CHANGED"


def test_transient_failure_retries_once_without_touching_other_stock(tmp_path):
    book = _book(tmp_path)
    assert preflight(book["path"], "000001", now=NOW)["attempt"] == 1
    mark_failure(book["path"], "000001", "RATE_LIMIT", now=NOW)

    retry = preflight(book["path"], "000001", now=NOW)
    payload = json.loads(
        (tmp_path / DATE / "_l4_tasks.json").read_text(encoding="utf-8")
    )
    assert retry["action"] == "RUN"
    assert retry["attempt"] == 2
    assert payload["tasks"]["000002"]["status"] == "PENDING"

    mark_failure(book["path"], "000001", "TIMEOUT", now=NOW)
    assert preflight(book["path"], "000001", now=NOW)["action"] == "BLOCKED"


def test_l4_trace_preserves_retry_failure_then_success_history(tmp_path, monkeypatch):
    handle, book = _traced_book(tmp_path, monkeypatch)

    assert preflight(book, "000001", now=NOW)["attempt"] == 1
    mark_failure(book, "000001", "TIMEOUT", expected_attempt=1, now=NOW)
    assert preflight(book, "000001", now=NOW)["attempt"] == 2
    mark_success(book, "000001", expected_attempt=2, now=NOW)

    events = _trace_events(handle)
    assert [event["event_type"] for event in events] == [
        "TASK_CLAIMED",
        "TASK_RETRY_SCHEDULED",
        "TASK_CLAIMED",
        "TASK_SUCCEEDED",
    ]
    assert events[1]["payload"]["error_class"] == "TIMEOUT"
    assert events[1]["payload"]["old_status"] == "RUNNING"
    assert events[1]["payload"]["new_status"] == "FAILED"
    assert events[-1]["attempt"] == 2


@pytest.mark.parametrize(
    "error_class,expected_event,expected_status",
    [
        ("SCHEMA_ERROR", "TASK_BLOCKED", "BLOCKED"),
        ("TIMEOUT", "TASK_RETRY_SCHEDULED", "FAILED"),
    ],
)
def test_l4_failure_transition_event_mapping(
    tmp_path, monkeypatch, error_class, expected_event, expected_status
):
    handle, book = _traced_book(tmp_path, monkeypatch)
    preflight(book, "000001", now=NOW)

    result = mark_failure(
        book, "000001", error_class, expected_attempt=1, now=NOW
    )

    event = _trace_events(handle)[-1]
    assert result["status"] == expected_status
    assert event["event_type"] == expected_event
    assert event["payload"]["error_class"] == error_class


def test_l4_exhausted_transient_failure_is_task_failed(tmp_path, monkeypatch):
    handle, book = _traced_book(tmp_path, monkeypatch)
    preflight(book, "000001", now=NOW)
    mark_failure(book, "000001", "TIMEOUT", expected_attempt=1, now=NOW)
    preflight(book, "000001", now=NOW)

    result = mark_failure(
        book, "000001", "CONNECTION", expected_attempt=2, now=NOW
    )

    event = _trace_events(handle)[-1]
    assert event["event_type"] == "TASK_FAILED"
    assert event["attempt"] == 2
    assert event["payload"]["new_status"] == result["status"] == "BLOCKED"
    assert event["payload"]["task_book_hash"] == hashlib.sha256(
        book.read_bytes()
    ).hexdigest()


def test_l4_transition_hashes_authoritative_book_and_visible_artifacts(
    tmp_path, monkeypatch
):
    handle, book = _traced_book(tmp_path, monkeypatch)

    preflight(book, "000001", now=NOW)

    payload = json.loads(book.read_text(encoding="utf-8"))
    task = payload["tasks"]["000001"]
    event = _trace_events(handle)[-1]
    evidence = event["payload"]
    expected_book_hash = hashlib.sha256(book.read_bytes()).hexdigest()
    assert evidence["task_book_hash"] == expected_book_hash
    assert evidence["attempt"] == task["attempt"] == event["attempt"]
    for name in ("prompt", "slim", "card"):
        path = Path(task["artifacts"][name]["path"])
        assert evidence[f"{name}_status"] == "PRESENT"
        assert evidence[f"{name}_hash"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_l4_event_failure_is_best_effort_after_authoritative_mutation(
    tmp_path, monkeypatch, capsys
):
    from autoresearch.scan import l4_tasks

    handle, book = _traced_book(tmp_path, monkeypatch)
    before_events = (handle.capsule / "events/events.jsonl").read_bytes()

    def fail_event(*args, **kwargs):
        raise OSError("events full")

    monkeypatch.setattr(l4_tasks, "append_event", fail_event)
    result = preflight(book, "000001", now=NOW)

    task = json.loads(book.read_text(encoding="utf-8"))["tasks"]["000001"]
    assert result["action"] == "RUN"
    assert task["status"] == "RUNNING"
    assert task["attempt"] == 1
    assert (handle.capsule / "events/events.jsonl").read_bytes() == before_events
    diagnostic = capsys.readouterr().err
    assert "TASK_CLAIMED" in diagnostic
    assert "events full" in diagnostic


@pytest.mark.parametrize("terminal", ["failure", "success"])
def test_late_attempt_one_terminal_cannot_mutate_claimed_attempt_two(
    tmp_path, monkeypatch, terminal
):
    handle, book = _traced_book(tmp_path, monkeypatch)
    preflight(book, "000001", expected_attempt=1, now=NOW)
    mark_failure(book, "000001", "TIMEOUT", expected_attempt=1, now=NOW)
    preflight(book, "000001", expected_attempt=2, now=NOW)
    book_before = book.read_bytes()
    events_before = (handle.capsule / "events/events.jsonl").read_bytes()

    with pytest.raises(ValueError, match="expected attempt 1.*authoritative attempt 2"):
        if terminal == "failure":
            mark_failure(
                book,
                "000001",
                "CONNECTION",
                expected_attempt=1,
                now=NOW,
            )
        else:
            mark_success(book, "000001", expected_attempt=1, now=NOW)

    assert book.read_bytes() == book_before
    assert (handle.capsule / "events/events.jsonl").read_bytes() == events_before
    task = json.loads(book.read_text(encoding="utf-8"))["tasks"]["000001"]
    assert (task["attempt"], task["status"]) == (2, "RUNNING")


def test_active_run_terminal_requires_expected_attempt_without_mutation(
    tmp_path, monkeypatch
):
    handle, book = _traced_book(tmp_path, monkeypatch)
    preflight(book, "000001", expected_attempt=1, now=NOW)
    book_before = book.read_bytes()
    events_before = (handle.capsule / "events/events.jsonl").read_bytes()

    with pytest.raises(ValueError, match="expected_attempt.*active run"):
        mark_failure(book, "000001", "TIMEOUT", now=NOW)

    assert book.read_bytes() == book_before
    assert (handle.capsule / "events/events.jsonl").read_bytes() == events_before


def test_duplicate_identical_failure_is_idempotent_but_conflict_rejects(
    tmp_path, monkeypatch
):
    handle, book = _traced_book(tmp_path, monkeypatch)
    preflight(book, "000001", expected_attempt=1, now=NOW)
    first = mark_failure(
        book,
        "000001",
        "TIMEOUT",
        error="same",
        expected_attempt=1,
        now=NOW,
    )
    book_after_first = book.read_bytes()
    events_after_first = (handle.capsule / "events/events.jsonl").read_bytes()

    duplicate = mark_failure(
        book,
        "000001",
        "TIMEOUT",
        error="same",
        expected_attempt=1,
        now=NOW + timedelta(minutes=1),
    )
    assert duplicate == {**first, "idempotent": True}
    assert book.read_bytes() == book_after_first
    assert (handle.capsule / "events/events.jsonl").read_bytes() == events_after_first

    with pytest.raises(ValueError, match="contradictory terminal"):
        mark_failure(
            book,
            "000001",
            "CONNECTION",
            expected_attempt=1,
            now=NOW,
        )
    assert book.read_bytes() == book_after_first
    assert (handle.capsule / "events/events.jsonl").read_bytes() == events_after_first


def test_duplicate_identical_success_is_idempotent_and_failure_conflicts(
    tmp_path, monkeypatch
):
    handle, book = _traced_book(tmp_path, monkeypatch)
    preflight(book, "000001", expected_attempt=1, now=NOW)
    first = mark_success(book, "000001", expected_attempt=1, now=NOW)
    book_after_first = book.read_bytes()
    events_after_first = (handle.capsule / "events/events.jsonl").read_bytes()

    duplicate = mark_success(
        book, "000001", expected_attempt=1, now=NOW + timedelta(minutes=1)
    )
    assert duplicate == {**first, "idempotent": True}
    assert book.read_bytes() == book_after_first
    assert (handle.capsule / "events/events.jsonl").read_bytes() == events_after_first

    with pytest.raises(ValueError, match="contradictory terminal"):
        mark_failure(
            book, "000001", "TIMEOUT", expected_attempt=1, now=NOW
        )


def test_success_rejects_symlinked_artifact_without_terminal_mutation(
    tmp_path, monkeypatch
):
    handle, book = _traced_book(tmp_path, monkeypatch)
    preflight(book, "000001", expected_attempt=1, now=NOW)
    task = json.loads(book.read_text(encoding="utf-8"))["tasks"]["000001"]
    card = Path(task["artifacts"]["card"]["path"])
    target = card.with_name("real-card.md")
    card.rename(target)
    card.symlink_to(target)
    events_before = (handle.capsule / "events/events.jsonl").read_bytes()

    with pytest.raises(ValueError, match="missing artifacts:card"):
        mark_success(book, "000001", expected_attempt=1, now=NOW)

    task = json.loads(book.read_text(encoding="utf-8"))["tasks"]["000001"]
    assert task["status"] == "RUNNING"
    assert (handle.capsule / "events/events.jsonl").read_bytes() == events_before


@pytest.mark.parametrize(
    "artifact,parent_name",
    [("card", "details"), ("slim", "_external_inputs")],
)
def test_active_trace_rejects_symlinked_artifact_parent_without_hashing_outside(
    tmp_path, monkeypatch, artifact, parent_name
):
    handle, book = _traced_book(tmp_path, monkeypatch)
    task = json.loads(book.read_text(encoding="utf-8"))["tasks"]["000001"]
    artifact_path = Path(task["artifacts"][artifact]["path"])
    parent = artifact_path.parent
    outside = tmp_path / f"outside-{parent_name}"
    parent.rename(outside)
    parent.symlink_to(outside, target_is_directory=True)
    outside_digest = hashlib.sha256(
        (outside / artifact_path.name).read_bytes()
    ).hexdigest()

    preflight(book, "000001", expected_attempt=1, now=NOW)

    claimed = _trace_events(handle)[-1]
    assert claimed["payload"][f"{artifact}_status"] == "UNREADABLE"
    assert claimed["payload"][f"{artifact}_hash"] is None
    assert outside_digest not in json.dumps(claimed)
    events_before = (handle.capsule / "events/events.jsonl").read_bytes()
    with pytest.raises(ValueError, match=f"missing artifacts:{artifact}"):
        mark_success(book, "000001", expected_attempt=1, now=NOW)
    persisted = json.loads(book.read_text(encoding="utf-8"))["tasks"]["000001"]
    assert persisted["status"] == "RUNNING"
    assert persisted["artifacts"][artifact]["content_hash"] is None
    assert (handle.capsule / "events/events.jsonl").read_bytes() == events_before


def test_active_success_rejects_task_book_artifact_path_outside_declared_shape(
    tmp_path, monkeypatch
):
    handle, book = _traced_book(tmp_path, monkeypatch)
    preflight(book, "000001", expected_attempt=1, now=NOW)
    payload = json.loads(book.read_text(encoding="utf-8"))
    outside = tmp_path / "outside-card.md"
    outside.write_text("# outside card\n", encoding="utf-8")
    payload["tasks"]["000001"]["artifacts"]["card"]["path"] = str(outside)
    book.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    book_before = book.read_bytes()
    events_before = (handle.capsule / "events/events.jsonl").read_bytes()

    with pytest.raises(ValueError, match="artifact path.*card"):
        mark_success(book, "000001", expected_attempt=1, now=NOW)

    assert book.read_bytes() == book_before
    assert (handle.capsule / "events/events.jsonl").read_bytes() == events_before
    assert hashlib.sha256(outside.read_bytes()).hexdigest().encode() not in book_before


def test_active_reconcile_does_not_recover_through_symlinked_details(tmp_path, monkeypatch):
    handle, book = _traced_book(tmp_path, monkeypatch)
    preflight(book, "000001", expected_attempt=1, now=NOW)
    details = handle.staging / "details"
    outside = tmp_path / "outside-reconcile-details"
    details.rename(outside)
    details.symlink_to(outside, target_is_directory=True)
    outside_digest = hashlib.sha256((outside / "000001.md").read_bytes()).hexdigest()
    events_before = (handle.capsule / "events/events.jsonl").read_bytes()

    result = reconcile(book, now=NOW)

    assert result["recovered"] == []
    assert result["skipped"] == [{"code": "000001", "missing": ["card"]}]
    persisted = json.loads(book.read_text(encoding="utf-8"))["tasks"]["000001"]
    assert persisted["status"] == "RUNNING"
    assert persisted["artifacts"]["card"]["content_hash"] is None
    assert outside_digest.encode() not in book.read_bytes()
    assert (handle.capsule / "events/events.jsonl").read_bytes() == events_before


def test_active_artifact_hash_tolerates_unrelated_sibling_creation(
    tmp_path, monkeypatch
):
    from autoresearch.scan import l4_tasks

    handle, book = _traced_book(tmp_path, monkeypatch)
    card = handle.staging / "details/000001.md"
    card_info = card.stat()
    expected_hash = hashlib.sha256(card.read_bytes()).hexdigest()
    real_read = l4_tasks.os.read
    created = False

    def create_sibling_during_card_read(fd, size):
        nonlocal created
        chunk = real_read(fd, size)
        info = l4_tasks.os.fstat(fd)
        if not created and (info.st_dev, info.st_ino) == (
            card_info.st_dev,
            card_info.st_ino,
        ):
            (card.parent / "parallel-sibling.md").write_text(
                "parallel output\n", encoding="utf-8"
            )
            created = True
        return chunk

    monkeypatch.setattr(l4_tasks.os, "read", create_sibling_during_card_read)
    preflight(book, "000001", expected_attempt=1, now=NOW)

    claimed = _trace_events(handle)[-1]
    assert created is True
    assert claimed["payload"]["card_status"] == "PRESENT"
    assert claimed["payload"]["card_hash"] == expected_hash


def test_active_preflight_rejects_symlinked_task_book_without_outside_mutation(
    tmp_path, monkeypatch
):
    handle, book = _traced_book(tmp_path, monkeypatch)
    outside = tmp_path / "outside-task-book.json"
    book.rename(outside)
    book.symlink_to(outside)
    outside_before = outside.read_bytes()
    events_before = (handle.capsule / "events/events.jsonl").read_bytes()

    with pytest.raises(ValueError, match="task book.*symlink|regular"):
        preflight(book, "000001", expected_attempt=1, now=NOW)

    assert book.is_symlink()
    assert outside.read_bytes() == outside_before
    assert (handle.capsule / "events/events.jsonl").read_bytes() == events_before


def test_active_preflight_rejects_task_book_replacement_during_secure_read(
    tmp_path, monkeypatch
):
    from autoresearch.scan import l4_tasks

    handle, book = _traced_book(tmp_path, monkeypatch)
    original = book.read_bytes()
    original_info = book.stat()
    events_before = (handle.capsule / "events/events.jsonl").read_bytes()
    real_read = l4_tasks.os.read
    replaced = False

    def replace_during_book_read(fd, size):
        nonlocal replaced
        chunk = real_read(fd, size)
        info = l4_tasks.os.fstat(fd)
        if not replaced and (info.st_dev, info.st_ino) == (
            original_info.st_dev,
            original_info.st_ino,
        ):
            replacement = book.with_name("replacement-task-book.json")
            replacement.write_bytes(original)
            replacement.replace(book)
            replaced = True
        return chunk

    monkeypatch.setattr(l4_tasks.os, "read", replace_during_book_read)
    with pytest.raises(ValueError, match="task book changed while reading"):
        preflight(book, "000001", expected_attempt=1, now=NOW)

    assert replaced is True
    assert json.loads(book.read_text(encoding="utf-8"))["tasks"]["000001"][
        "status"
    ] == "PENDING"
    assert (handle.capsule / "events/events.jsonl").read_bytes() == events_before


def test_l4_without_run_id_never_attempts_event_capture(tmp_path, monkeypatch):
    from autoresearch.scan import l4_tasks

    book = _book(tmp_path, ("000001",))
    monkeypatch.delenv("AUTORESEARCH_RUN_ID", raising=False)

    def unexpected(*args, **kwargs):
        raise AssertionError("legacy task transition attempted forensic capture")

    monkeypatch.setattr(l4_tasks, "append_event", unexpected)
    result = preflight(book["path"], "000001", now=NOW)

    assert result["action"] == "RUN"


def test_expected_retry_attempt_mismatch_is_atomic_then_exact_next_attempt_runs(tmp_path):
    book = _book(tmp_path, ("000001",))
    assert preflight(book["path"], "000001", expected_attempt=1, now=NOW)["attempt"] == 1
    mark_failure(book["path"], "000001", "RATE_LIMIT", now=NOW)
    path = tmp_path / DATE / "_l4_tasks.json"
    before = path.read_bytes()

    with pytest.raises(ValueError, match="expected attempt 3.*next attempt 2"):
        preflight(book["path"], "000001", expected_attempt=3, now=NOW)

    assert path.read_bytes() == before
    retry = preflight(book["path"], "000001", expected_attempt=2, now=NOW)
    assert retry["action"] == "RUN"
    assert retry["attempt"] == 2
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["tasks"]["000001"]["status"] == "RUNNING"
    assert payload["tasks"]["000001"]["attempt"] == 2


def test_contract_failure_never_retries(tmp_path):
    book = _book(tmp_path, ("000003",))
    preflight(book["path"], "000003", now=NOW)
    mark_failure(book["path"], "000003", "SCHEMA_ERROR", now=NOW)

    blocked = preflight(book["path"], "000003", now=NOW)
    assert blocked["action"] == "BLOCKED"
    assert blocked["attempt"] == 1


def test_stale_running_task_is_recovered_as_one_transient_retry(tmp_path):
    book = _book(tmp_path, ("000001",))
    first = preflight(book["path"], "000001", now=NOW)
    assert first["attempt"] == 1

    recovered = preflight(
        book["path"],
        "000001",
        now=NOW + timedelta(hours=2),
        stale_after_seconds=3600,
    )
    assert recovered["action"] == "RUN"
    assert recovered["attempt"] == 2
    assert recovered["reason"] == "STALE_TASK"


def test_stale_running_trace_persists_failure_then_claim_with_exact_hashes(
    tmp_path, monkeypatch
):
    handle, book = _traced_book(tmp_path, monkeypatch)
    first = preflight(book, "000001", expected_attempt=1, now=NOW)
    before = json.loads(book.read_text(encoding="utf-8"))
    original_started_at = before["tasks"]["000001"]["started_at"]
    n_before = len(_trace_events(handle))

    result = preflight(
        book,
        "000001",
        expected_attempt=2,
        now=NOW + timedelta(hours=2),
        stale_after_seconds=3600,
    )

    assert first["attempt"] == 1 and result["attempt"] == 2
    events = _trace_events(handle)[n_before:]
    assert [event["event_type"] for event in events] == [
        "TASK_RETRY_SCHEDULED",
        "TASK_CLAIMED",
    ]
    failed, claimed = events
    assert (failed["payload"]["old_status"], failed["payload"]["new_status"]) == (
        "RUNNING",
        "FAILED",
    )
    assert failed["payload"]["error_class"] == "STALE_TASK"
    assert failed["attempt"] == 1
    assert (claimed["payload"]["old_status"], claimed["payload"]["new_status"]) == (
        "FAILED",
        "RUNNING",
    )
    assert claimed["attempt"] == 2
    final = json.loads(book.read_text(encoding="utf-8"))
    intermediate = deepcopy(final)
    intermediate_task = intermediate["tasks"]["000001"]
    intermediate_task["attempt"] = 1
    intermediate_task["status"] = "FAILED"
    intermediate_task["started_at"] = original_started_at
    expected_failed_hash = hashlib.sha256(
        _persisted_book_bytes(intermediate)
    ).hexdigest()
    expected_claimed_hash = hashlib.sha256(book.read_bytes()).hexdigest()
    assert failed["payload"]["task_book_hash"] == expected_failed_hash
    assert claimed["payload"]["task_book_hash"] == expected_claimed_hash
    assert failed["payload"]["task_book_hash"] != claimed["payload"]["task_book_hash"]


def test_stale_expected_attempt_mismatch_precedes_all_mutation_and_audit(
    tmp_path, monkeypatch
):
    from autoresearch.scan import l4_tasks

    handle, book = _traced_book(tmp_path, monkeypatch)
    preflight(book, "000001", expected_attempt=1, now=NOW)
    book_before = book.read_bytes()
    events_before = (handle.capsule / "events/events.jsonl").read_bytes()
    audit_calls = []
    original_record = l4_tasks.structural_audit.record

    def observed_record(*args, **kwargs):
        audit_calls.append((args, kwargs))
        return original_record(*args, **kwargs)

    monkeypatch.setattr(l4_tasks.structural_audit, "record", observed_record)
    with pytest.raises(ValueError, match="expected attempt 3.*next attempt 2"):
        preflight(
            book,
            "000001",
            expected_attempt=3,
            now=NOW + timedelta(hours=2),
            stale_after_seconds=3600,
        )

    assert audit_calls == []
    assert book.read_bytes() == book_before
    assert (handle.capsule / "events/events.jsonl").read_bytes() == events_before


def test_exhausted_failure_preflight_blocks_without_duplicate_task_failed_event(
    tmp_path, monkeypatch
):
    handle, book = _traced_book(tmp_path, monkeypatch)
    preflight(book, "000001", expected_attempt=1, now=NOW)
    mark_failure(book, "000001", "TIMEOUT", expected_attempt=1, now=NOW)
    preflight(book, "000001", expected_attempt=2, now=NOW)
    mark_failure(book, "000001", "CONNECTION", expected_attempt=2, now=NOW)
    before = _trace_events(handle)
    assert before[-1]["event_type"] == "TASK_FAILED"

    book_before = book.read_bytes()
    result = preflight(book, "000001", now=NOW)

    assert result["action"] == "BLOCKED"
    assert json.loads(book.read_text(encoding="utf-8"))["tasks"]["000001"][
        "status"
    ] == "BLOCKED"
    assert book.read_bytes() == book_before
    assert _trace_events(handle) == before


def test_prepare_slim_retries_only_target_stock_once(tmp_path):
    book = _book(tmp_path, ("000001", "000002"))
    calls: list[str] = []

    def harvest(ticker: str, date: str):
        calls.append(ticker)
        target = tmp_path / "context" / f"{ticker}_{date}_slim.md"
        if len(calls) == 1:
            target.write_text("too small", encoding="utf-8")
        else:
            target.write_text(
                "\n".join([
                    "## Verified market snapshot",
                    "### Latest verified OHLCV row",
                    "| Close | 12.34 |",
                    "## Market context",
                    "## Fundamentals overview",
                    "x" * 5000,
                ]),
                encoding="utf-8",
            )
        return target

    got = prepare_slim(
        book["path"],
        "000001",
        harvest_fn=harvest,
        retries=1,
        now=NOW,
    )
    payload = json.loads(
        (tmp_path / DATE / "_l4_tasks.json").read_text(encoding="utf-8")
    )
    assert got["ok"] is True
    assert calls == ["000001.SZ", "000001.SZ"]
    assert payload["tasks"]["000001"]["artifacts"]["slim"]["status"] == "PRESENT"
    assert payload["tasks"]["000001"]["slim_attempts"] == 2
    assert payload["tasks"]["000002"]["slim_attempts"] == 0


def _valid_slim_text() -> str:
    return "\n".join([
        "## Verified market snapshot",
        "### Latest verified OHLCV row",
        "| Close | 12.34 |",
        "## Market context",
        "## Fundamentals overview",
        "x" * 5000,
    ])


def test_active_prepare_slim_initial_unreadable_is_sticky_until_fresh_call(
    tmp_path, monkeypatch
):
    from autoresearch.scan import l4_tasks

    handle, book = _traced_book(tmp_path, monkeypatch)
    slim = handle.staging / "_external_inputs" / f"000001.SZ_{DATE}_slim.md"
    original_identity = (slim.stat().st_dev, slim.stat().st_ino)
    replacement_bytes = (_valid_slim_text() + "\nstable replacement\n").encode()
    replacement_hash = hashlib.sha256(replacement_bytes).hexdigest()
    real_read = l4_tasks.os.read
    replaced = False

    def replace_initial_slim_during_read(fd, size):
        nonlocal replaced
        chunk = real_read(fd, size)
        info = l4_tasks.os.fstat(fd)
        if not replaced and (info.st_dev, info.st_ino) == original_identity:
            replacement = slim.with_name("initial-replacement-slim.md")
            replacement.write_bytes(replacement_bytes)
            replacement.replace(slim)
            replaced = True
        return chunk

    monkeypatch.setattr(l4_tasks.os, "read", replace_initial_slim_during_read)
    first = prepare_slim(
        book,
        "000001",
        harvest_fn=lambda *_: pytest.fail("unstable cache must not harvest"),
        retries=0,
        now=NOW,
    )

    assert replaced is True
    assert first["ok"] is False
    assert "UNREADABLE" in first["reason"]
    first_task = json.loads(book.read_text(encoding="utf-8"))["tasks"]["000001"]
    assert first_task["artifacts"]["slim"]["status"] == "UNREADABLE"
    assert first_task["artifacts"]["slim"]["content_hash"] is None
    assert replacement_hash.encode() not in book.read_bytes()

    second = prepare_slim(book, "000001", retries=0, now=NOW)
    second_task = json.loads(book.read_text(encoding="utf-8"))["tasks"]["000001"]
    assert second["ok"] is True
    assert second_task["artifacts"]["slim"]["status"] == "PRESENT"
    assert second_task["artifacts"]["slim"]["content_hash"] == replacement_hash


def test_active_prepare_slim_post_harvest_unreadable_is_sticky_until_fresh_call(
    tmp_path, monkeypatch
):
    from autoresearch.scan import l4_tasks

    handle, book = _traced_book(tmp_path, monkeypatch)
    slim = handle.staging / "_external_inputs" / f"000001.SZ_{DATE}_slim.md"
    slim.rename(tmp_path / "saved-slim.md")
    replacement_bytes = (_valid_slim_text() + "\nstable post-harvest replacement\n").encode()
    replacement_hash = hashlib.sha256(replacement_bytes).hexdigest()
    real_read = l4_tasks.os.read
    harvested_identity = None
    replaced = False

    def replace_harvested_slim_during_read(fd, size):
        nonlocal replaced
        chunk = real_read(fd, size)
        info = l4_tasks.os.fstat(fd)
        if (
            not replaced
            and harvested_identity is not None
            and (info.st_dev, info.st_ino) == harvested_identity
        ):
            replacement = slim.with_name("post-harvest-replacement-slim.md")
            replacement.write_bytes(replacement_bytes)
            replacement.replace(slim)
            replaced = True
        return chunk

    def harvest(_ticker: str, _date: str) -> Path:
        nonlocal harvested_identity
        slim.write_text(_valid_slim_text(), encoding="utf-8")
        info = slim.stat()
        harvested_identity = (info.st_dev, info.st_ino)
        return slim

    monkeypatch.setattr(l4_tasks.os, "read", replace_harvested_slim_during_read)
    first = prepare_slim(
        book, "000001", harvest_fn=harvest, retries=0, now=NOW
    )

    assert replaced is True
    assert first["ok"] is False
    assert "UNREADABLE" in first["reason"]
    first_task = json.loads(book.read_text(encoding="utf-8"))["tasks"]["000001"]
    assert first_task["artifacts"]["slim"]["status"] == "UNREADABLE"
    assert first_task["artifacts"]["slim"]["content_hash"] is None
    assert replacement_hash.encode() not in book.read_bytes()

    second = prepare_slim(book, "000001", retries=0, now=NOW)
    second_task = json.loads(book.read_text(encoding="utf-8"))["tasks"]["000001"]
    assert second["ok"] is True
    assert second_task["artifacts"]["slim"]["status"] == "PRESENT"
    assert second_task["artifacts"]["slim"]["content_hash"] == replacement_hash


def test_active_prepare_slim_rejects_symlinked_external_inputs_cache(
    tmp_path, monkeypatch
):
    handle, book = _traced_book(tmp_path, monkeypatch)
    inputs = handle.staging / "_external_inputs"
    outside = tmp_path / "outside-inputs"
    inputs.rename(outside)
    inputs.symlink_to(outside, target_is_directory=True)
    outside_slim = outside / f"000001.SZ_{DATE}_slim.md"
    outside_before = outside_slim.read_bytes()
    outside_digest = hashlib.sha256(outside_before).hexdigest()
    calls = []

    result = prepare_slim(
        book,
        "000001",
        harvest_fn=lambda ticker, date: calls.append((ticker, date)),
        retries=0,
        now=NOW,
    )

    assert result["ok"] is False
    assert result["attempts"] == 0
    assert calls == []
    assert outside_slim.read_bytes() == outside_before
    persisted = json.loads(book.read_text(encoding="utf-8"))["tasks"]["000001"]
    assert persisted["artifacts"]["slim"]["status"] != "PRESENT"
    assert persisted["artifacts"]["slim"]["content_hash"] is None
    assert persisted["last_error_class"] == "DATA_INTEGRITY"
    assert outside_digest.encode() not in book.read_bytes()


def test_active_prepare_slim_does_not_harvest_through_symlinked_parent(
    tmp_path, monkeypatch
):
    handle, book = _traced_book(tmp_path, monkeypatch)
    inputs = handle.staging / "_external_inputs"
    outside = tmp_path / "outside-missing-inputs"
    inputs.rename(outside)
    outside_slim = outside / f"000001.SZ_{DATE}_slim.md"
    outside_slim.rename(tmp_path / "saved-slim.md")
    inputs.symlink_to(outside, target_is_directory=True)
    calls = []

    def harvest(ticker: str, date: str) -> Path:
        calls.append((ticker, date))
        outside_slim.write_text(_valid_slim_text(), encoding="utf-8")
        return inputs / outside_slim.name

    result = prepare_slim(
        book, "000001", harvest_fn=harvest, retries=0, now=NOW
    )

    assert result["ok"] is False
    assert result["attempts"] == 0
    assert calls == []
    assert not outside_slim.exists()
    persisted = json.loads(book.read_text(encoding="utf-8"))["tasks"]["000001"]
    assert persisted["artifacts"]["slim"]["status"] != "PRESENT"
    assert persisted["artifacts"]["slim"]["content_hash"] is None


def test_active_prepare_slim_does_not_harvest_through_final_symlink(
    tmp_path, monkeypatch
):
    handle, book = _traced_book(tmp_path, monkeypatch)
    declared = handle.staging / "_external_inputs" / f"000001.SZ_{DATE}_slim.md"
    outside = tmp_path / "outside-final-slim.md"
    declared.rename(outside)
    declared.symlink_to(outside)
    outside_before = outside.read_bytes()
    calls = []

    def harvest(ticker: str, date: str) -> Path:
        calls.append((ticker, date))
        declared.write_text(_valid_slim_text(), encoding="utf-8")
        return declared

    result = prepare_slim(
        book, "000001", harvest_fn=harvest, retries=0, now=NOW
    )

    assert result["ok"] is False
    assert result["attempts"] == 0
    assert calls == []
    assert outside.read_bytes() == outside_before
    persisted = json.loads(book.read_text(encoding="utf-8"))["tasks"]["000001"]
    assert persisted["artifacts"]["slim"] == {
        "path": str(declared),
        "status": "UNREADABLE",
        "content_hash": None,
    }


def test_active_prepare_slim_retries_ordinary_harvest_exception(
    tmp_path, monkeypatch
):
    handle, book = _traced_book(tmp_path, monkeypatch)
    declared = handle.staging / "_external_inputs" / f"000001.SZ_{DATE}_slim.md"
    declared.rename(tmp_path / "saved-slim.md")
    calls = []

    def harvest(ticker: str, date: str) -> Path:
        calls.append((ticker, date))
        if len(calls) == 1:
            raise ConnectionError("transient")
        declared.write_text(_valid_slim_text(), encoding="utf-8")
        return declared

    result = prepare_slim(
        book, "000001", harvest_fn=harvest, retries=1, now=NOW
    )

    assert result["ok"] is True
    assert result["attempts"] == 2
    assert calls == [("000001.SZ", DATE), ("000001.SZ", DATE)]


def test_active_prepare_slim_stops_retry_after_failed_harvest_replaces_parent(
    tmp_path, monkeypatch
):
    handle, book = _traced_book(tmp_path, monkeypatch)
    inputs = handle.staging / "_external_inputs"
    declared = inputs / f"000001.SZ_{DATE}_slim.md"
    declared.rename(tmp_path / "saved-slim.md")
    outside = tmp_path / "failed-harvest-inputs"
    calls = []

    def harvest(_ticker: str, _date: str) -> Path:
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            inputs.rename(outside)
            inputs.symlink_to(outside, target_is_directory=True)
            raise ConnectionError("failed after replacing parent")
        declared.write_text(_valid_slim_text(), encoding="utf-8")
        return declared

    result = prepare_slim(
        book, "000001", harvest_fn=harvest, retries=1, now=NOW
    )

    assert result["ok"] is False
    assert result["attempts"] == 1
    assert calls == [1]
    assert not (outside / declared.name).exists()
    persisted = json.loads(book.read_text(encoding="utf-8"))["tasks"]["000001"]
    assert persisted["artifacts"]["slim"]["status"] == "UNREADABLE"
    assert persisted["artifacts"]["slim"]["content_hash"] is None


def test_active_prepare_slim_stops_retry_after_failed_harvest_creates_symlink(
    tmp_path, monkeypatch
):
    handle, book = _traced_book(tmp_path, monkeypatch)
    declared = handle.staging / "_external_inputs" / f"000001.SZ_{DATE}_slim.md"
    declared.rename(tmp_path / "saved-slim.md")
    outside = tmp_path / "failed-harvest-outside.md"
    outside.write_text("outside must remain unchanged", encoding="utf-8")
    outside_before = outside.read_bytes()
    calls = []

    def harvest(_ticker: str, _date: str) -> Path:
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            declared.symlink_to(outside)
            raise ConnectionError("failed after creating final symlink")
        declared.write_text(_valid_slim_text(), encoding="utf-8")
        return declared

    result = prepare_slim(
        book, "000001", harvest_fn=harvest, retries=1, now=NOW
    )

    assert result["ok"] is False
    assert result["attempts"] == 1
    assert calls == [1]
    assert outside.read_bytes() == outside_before
    persisted = json.loads(book.read_text(encoding="utf-8"))["tasks"]["000001"]
    assert persisted["artifacts"]["slim"]["status"] == "UNREADABLE"
    assert persisted["artifacts"]["slim"]["content_hash"] is None


def test_active_prepare_slim_rejects_alternate_harvest_output(
    tmp_path, monkeypatch
):
    handle, book = _traced_book(tmp_path, monkeypatch)
    declared = handle.staging / "_external_inputs" / f"000001.SZ_{DATE}_slim.md"
    declared.rename(tmp_path / "saved-slim.md")
    alternate = tmp_path / "alternate-slim.md"

    def harvest(_ticker: str, _date: str) -> Path:
        alternate.write_text(_valid_slim_text(), encoding="utf-8")
        return alternate

    result = prepare_slim(
        book, "000001", harvest_fn=harvest, retries=0, now=NOW
    )

    assert result["ok"] is False
    assert result["attempts"] == 1
    assert "path" in result["reason"].lower()
    persisted = json.loads(book.read_text(encoding="utf-8"))["tasks"]["000001"]
    slim = persisted["artifacts"]["slim"]
    assert slim["path"] == str(declared)
    assert slim["status"] != "PRESENT"
    assert slim["content_hash"] is None
    assert (
        hashlib.sha256(alternate.read_bytes()).hexdigest().encode()
        not in book.read_bytes()
    )


def test_active_prepare_slim_rejects_parent_replacement_during_harvest(
    tmp_path, monkeypatch
):
    handle, book = _traced_book(tmp_path, monkeypatch)
    inputs = handle.staging / "_external_inputs"
    declared = inputs / f"000001.SZ_{DATE}_slim.md"
    declared.rename(tmp_path / "saved-slim.md")
    outside = tmp_path / "replaced-inputs"

    def harvest(_ticker: str, _date: str) -> Path:
        inputs.rename(outside)
        inputs.symlink_to(outside, target_is_directory=True)
        (outside / declared.name).write_text(_valid_slim_text(), encoding="utf-8")
        return declared

    result = prepare_slim(
        book, "000001", harvest_fn=harvest, retries=0, now=NOW
    )

    assert result["ok"] is False
    persisted = json.loads(book.read_text(encoding="utf-8"))["tasks"]["000001"]
    assert persisted["artifacts"]["slim"]["status"] != "PRESENT"
    assert persisted["artifacts"]["slim"]["content_hash"] is None


def test_active_prepare_slim_cache_and_trusted_default_harvest(
    tmp_path, monkeypatch
):
    from autoresearch.scan.l4 import producers

    handle, book = _traced_book(tmp_path, monkeypatch)
    inputs = handle.staging / "_external_inputs"
    cached = prepare_slim(book, "000001", retries=0, now=NOW)
    assert cached["ok"] is True
    assert cached["attempts"] == 0

    declared = inputs / f"000001.SZ_{DATE}_slim.md"
    inputs.rename(tmp_path / "saved-inputs")
    seen = []

    def default_harvest(ticker: str, date: str, ctx_root: Path) -> Path:
        seen.append((ticker, date, ctx_root))
        target = ctx_root / f"{ticker}_{date}_slim.md"
        target.write_text(_valid_slim_text(), encoding="utf-8")
        return target

    monkeypatch.setattr(producers, "_default_harvest_slim", default_harvest)
    harvested = prepare_slim(book, "000001", retries=0, now=NOW)

    assert harvested["ok"] is True
    assert harvested["attempts"] == 1
    assert seen == [("000001.SZ", DATE, inputs)]
    persisted = json.loads(book.read_text(encoding="utf-8"))["tasks"]["000001"]
    assert persisted["artifacts"]["slim"]["content_hash"] == hashlib.sha256(
        declared.read_bytes()
    ).hexdigest()


def test_dispatch_batches_effective_cap_is_l4_stock_and_ignores_rate_limit(tmp_path):
    """Wave11 C1:派发帽=caps.l4_stock,不再 min 四帽、不再被 rate_limit_failures 收窄。"""
    book = _book(tmp_path)
    caps = {"tushare": 6, "web_search": 4, "web_fetch": 5, "l4_stock": 8}

    first = dispatch_batches(book["path"], caps=caps)
    assert first["caps"] == caps
    assert first["effective_cap"] == 8
    assert first["batches"] == [["000001", "000002", "000003"]]

    preflight(book["path"], "000001", now=NOW)
    mark_failure(book["path"], "000001", "RATE_LIMIT", now=NOW)
    second = dispatch_batches(book["path"])
    assert second["effective_cap"] == 8
    assert second["batches"] == [["000001", "000002", "000003"]]


# ── E1b:reconcile 收尾自愈(2026-08-12 九票卡全在盘、book 全 RUNNING → contract 团灭)──
def test_reconcile_recovers_running_with_artifacts_on_disk(tmp_path):
    """一票 status=RUNNING,prompt/slim/card 三产物齐且 slim 合格 → 按盘上事实补记 SUCCEEDED。"""
    book = _book(tmp_path, ("000001",))
    _files(tmp_path, "000001", "000001.SZ")
    preflight(book["path"], "000001", now=NOW)  # PENDING → RUNNING(卡在盘、book 没收尾)

    from autoresearch.scan import l4_tasks

    got = l4_tasks.reconcile(book["path"], now=NOW)
    assert got["ok"] and got["recovered"] == ["000001"]
    _, payload = l4_tasks._read(book["path"])
    task = payload["tasks"]["000001"]
    assert task["status"] == "SUCCEEDED" and task["recovered"] is True
    assert task["artifacts"]["card"]["content_hash"]


def test_reconcile_skips_when_card_missing(tmp_path):
    """缺产物的票原样保留 —— contract 门拦它拦得对,不得被静默补记。"""
    book = _book(tmp_path, ("000001",))
    _files(tmp_path, "000001", "000001.SZ")
    (tmp_path / DATE / "details" / "000001.md").unlink()  # card 缺席
    preflight(book["path"], "000001", now=NOW)

    from autoresearch.scan import l4_tasks

    got = l4_tasks.reconcile(book["path"], now=NOW)
    assert got["recovered"] == [] and got["skipped"][0]["missing"] == ["card"]
    _, payload = l4_tasks._read(book["path"])
    assert payload["tasks"]["000001"]["status"] == "RUNNING"


def test_reconcile_idempotent_on_succeeded(tmp_path):
    """幂等:SUCCEEDED 行直接跳过,不重跑一次不必要的补记。"""
    book = _book(tmp_path, ("000001",))
    _files(tmp_path, "000001", "000001.SZ")
    preflight(book["path"], "000001", now=NOW)
    mark_success(book["path"], "000001", now=NOW)

    from autoresearch.scan import l4_tasks

    assert l4_tasks.reconcile(book["path"], now=NOW)["recovered"] == []


def test_reconcile_does_not_revive_failed_task(tmp_path):
    """复核修复轮 1(设计稿 §3 E1b:只自愈 RUNNING)—— 显式 mark_failure 过的票,
    即使三件产物齐全且 slim 合格,也不得被 reconcile 用盘上文件推翻显式失败判断。
    """
    book = _book(tmp_path, ("000001",))
    _files(tmp_path, "000001", "000001.SZ")
    preflight(book["path"], "000001", now=NOW)
    mark_failure(book["path"], "000001", "RATE_LIMIT", now=NOW)  # 瞬时错误 → FAILED

    from autoresearch.scan import l4_tasks

    got = l4_tasks.reconcile(book["path"], now=NOW)
    assert "000001" not in got["recovered"]
    _, payload = l4_tasks._read(book["path"])
    assert payload["tasks"]["000001"]["status"] == "FAILED"
