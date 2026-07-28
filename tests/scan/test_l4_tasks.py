"""L4 单票任务簿：可恢复、失败隔离、限次重试和稳定批次。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from autoresearch.scan.l4_tasks import (
    dispatch_batches,
    initialize,
    mark_failure,
    mark_success,
    preflight,
    prepare_slim,
)

DATE = "2026-07-28"
NOW = datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc)


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
    return initialize(
        DATE,
        list(codes),
        root=tmp_path,
        context_root=tmp_path / "context",
        meta=meta,
        now=NOW,
    )


def test_initialize_is_atomic_and_preserves_order(tmp_path):
    result = _book(tmp_path)
    path = tmp_path / DATE / "_l4_tasks.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert result["path"] == str(path)
    assert list(payload["tasks"]) == ["000001", "000002", "000003"]
    assert payload["tasks"]["000002"]["pinned"] is True
    assert payload["tasks"]["000001"]["status"] == "PENDING"
    assert not path.with_name("_l4_tasks.json.tmp").exists()


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


def test_dispatch_batches_use_explicit_minimum_and_back_off_after_rate_limit(tmp_path):
    book = _book(tmp_path)
    caps = {"tushare": 6, "web_search": 4, "web_fetch": 5, "l4_stock": 8}

    first = dispatch_batches(book["path"], caps=caps)
    assert first["caps"] == caps
    assert first["effective_cap"] == 4
    assert first["batches"] == [["000001", "000002", "000003"]]

    preflight(book["path"], "000001", now=NOW)
    mark_failure(book["path"], "000001", "RATE_LIMIT", now=NOW)
    second = dispatch_batches(book["path"])
    assert second["effective_cap"] == 3
    assert second["batches"] == [["000001", "000002", "000003"]]
