"""L4 单票任务簿：可恢复、失败隔离、限次重试和稳定批次。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

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
    preflight(book["path"], "000001", now=NOW)
    mark_success(book["path"], "000001", now=NOW)
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
