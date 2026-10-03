"""`l4_tasks batches` 必须让 RUNNING 可见(Wave8 W8-6)。

2026-07-28 首航:批 2 的 601319 处于 RUNNING(卡片 agent 还在写)时,`batches` 的输出
既不把它列进 `batches`(只收 PENDING/FAILED),也没有任何"它在飞"的提示 —— 主会话据此
读成「批 2 已完」,提前派了批 3。当晚 4 并发内没出事,但判据本身是错的:

    完成判据 ≠ batches 为空;完成判据 = task_book 全 SUCCEEDED。

`batches` 为空可能是「都跑完了」,也可能是「都还在飞」,这两种状态对调度者的含义完全相反。
本文件把 `running` 数组锁进契约,让两者可区分。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from autoresearch.scan.l4_tasks import dispatch_batches


def _book(tmp_path, tasks: dict, *, order: list[str] | None = None):
    path = tmp_path / "_l4_tasks.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "analysis_date": "2026-07-28",
        "caps": {"tushare": 4, "l4_stock": 4},
        "order": order or list(tasks),
        "tasks": tasks,
    }, ensure_ascii=False), encoding="utf-8")
    return path


def _task(status: str, *, started_at: str | None = None) -> dict:
    return {"code": "x", "status": status, "attempt": 1,
            **({"started_at": started_at} if started_at else {})}


def test_running_tasks_are_reported_with_age(tmp_path):
    """RUNNING 的票必须出现在 running 数组里,并带上已跑分钟数。"""
    now = datetime(2026, 7, 28, 14, 40, tzinfo=timezone.utc)
    started = (now - timedelta(minutes=17)).strftime("%Y-%m-%dT%H:%M:%SZ")
    book = _book(tmp_path, {
        "601319": _task("RUNNING", started_at=started),
        "300857": _task("PENDING"),
    })

    out = dispatch_batches(book, now=now)

    assert [r["code"] for r in out["running"]] == ["601319"]
    assert out["running"][0]["age_min"] == pytest.approx(17, abs=1)
    assert out["batches"] == [["300857"]], "RUNNING 不得被重复派发"


def test_empty_batches_with_running_is_not_completion(tmp_path):
    """**本 task 的核心断言**:batches 空 + running 非空 = 还在飞,不是跑完了。"""
    now = datetime(2026, 7, 28, 14, 40, tzinfo=timezone.utc)
    book = _book(tmp_path, {
        "601319": _task("RUNNING", started_at="2026-07-28T14:23:29Z"),
        "600018": _task("SUCCEEDED"),
    })

    out = dispatch_batches(book, now=now)

    assert out["batches"] == []
    assert out["pending"] == 0
    assert out["running"], (
        "batches 空却看不到 RUNNING —— 调度者会把「都在飞」误读成「都跑完」"
        "(07-28 批 2 的 601319 即此形)"
    )


def test_all_succeeded_reports_no_running(tmp_path):
    """真跑完:batches 空且 running 空 —— 这才是完成态。"""
    now = datetime(2026, 7, 28, 14, 40, tzinfo=timezone.utc)
    book = _book(tmp_path, {
        "601319": _task("SUCCEEDED"),
        "600018": _task("SUCCEEDED"),
    })

    out = dispatch_batches(book, now=now)

    assert out["batches"] == []
    assert out["running"] == []


def test_running_without_started_at_still_listed(tmp_path):
    """缺 started_at(旧账本)也要列出来,age_min 记 None —— 不能因缺字段就隐身。"""
    now = datetime(2026, 7, 28, 14, 40, tzinfo=timezone.utc)
    book = _book(tmp_path, {"601319": _task("RUNNING")})

    out = dispatch_batches(book, now=now)

    assert [r["code"] for r in out["running"]] == ["601319"]
    assert out["running"][0]["age_min"] is None
