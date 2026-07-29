"""L4 逐股出卡播报:唯一权威是 task_book(Wave8 W8-7)。

**两个前任各有各的瞎**:

1. `scan.progress` 按**产物文件存在性**反推阶段 —— 分不清「在跑 / 被跳过 / 挂了」,
   累犯三次误报(pr_20260717_004:把 l3-rank 的输入当"精排中"、把复用卡计进新卡数、
   9/8 溢出)。E1 早已挂它退役。
2. 07-28 首航我临时挂的卡片级 grep 轮询 —— 直接扫 `details/*.md` 抓 `**Rating**`,
   读到了 601319 **写盘中途的草稿**:先播 Underweight,终稿是 Hold。文件存在 ≠ 写完了。

本模块的判据:**只认 `_l4_tasks.json` 里 status 翻 SUCCEEDED 且 card.content_hash
已记的票**,此时卡片才是完稿。这也是 W8-6 的同一条纪律 —— 完成态由账本定义,不由
文件存在性定义。
"""

from __future__ import annotations

import json

import pytest

from autoresearch.scan.l4_watch import pending_fold_lines, render_events, snapshot


def _write_book(scan_dir, tasks: dict) -> None:
    (scan_dir / "_l4_tasks.json").write_text(json.dumps({
        "schema_version": 1,
        "analysis_date": "2026-07-28",
        "order": list(tasks),
        "tasks": tasks,
    }, ensure_ascii=False), encoding="utf-8")


def _task(status: str, *, card_hash: str | None = None, started_at: str | None = None,
          error: str | None = None) -> dict:
    return {
        "status": status,
        "started_at": started_at,
        "last_error_class": error,
        "artifacts": {"card": {"path": "x.md",
                               "status": "PRESENT" if card_hash else "MISSING",
                               "content_hash": card_hash}},
    }


def _write_card(scan_dir, code: str, rating: str) -> None:
    details = scan_dir / "details"
    details.mkdir(exist_ok=True)
    (details / f"{code}.md").write_text(
        f"# 决策卡 — {code}\n\n**Rating**: {rating}\n", encoding="utf-8")


@pytest.fixture
def scan_dir(tmp_path):
    (tmp_path / "details").mkdir()
    return tmp_path


def test_running_task_with_card_on_disk_is_not_reported(scan_dir):
    """**核心回归**:卡文件已在盘上但 task 仍 RUNNING → 一个字都不许播。

    这正是 07-28 的 601319:卡片 agent 写了草稿(Rating: Underweight),
    终稿改成 Hold。按文件存在性播报 = 播出一个从未成立的评级。
    """
    _write_card(scan_dir, "601319", "Underweight")      # 草稿已落盘
    _write_book(scan_dir, {"601319": _task("RUNNING", started_at="2026-07-28T14:23:29Z")})

    events = render_events(snapshot(scan_dir), seen=set())

    assert events == [], f"读到了写盘中途的草稿:{events}"


def test_succeeded_without_hash_is_not_reported(scan_dir):
    """SUCCEEDED 但 card hash 未记 → 仍不播(账本尚未确认卡是完稿)。"""
    _write_card(scan_dir, "601319", "Hold")
    _write_book(scan_dir, {"601319": _task("SUCCEEDED", card_hash=None)})

    assert render_events(snapshot(scan_dir), seen=set()) == []


def test_succeeded_with_hash_reports_rating(scan_dir):
    _write_card(scan_dir, "601319", "Hold")
    _write_book(scan_dir, {
        "601319": _task("SUCCEEDED", card_hash="ce4b1e08"),
        "300857": _task("RUNNING", started_at="2026-07-28T14:23:29Z"),
    })

    events = render_events(snapshot(scan_dir), seen=set())

    assert len(events) == 1
    assert "601319" in events[0] and "Hold" in events[0]
    assert "1/2" in events[0], "计数应为 已终态/总数"


def test_failed_task_is_reported_with_error_class(scan_dir):
    _write_book(scan_dir, {"601319": _task("FAILED", error="RATE_LIMIT")})

    events = render_events(snapshot(scan_dir), seen=set())

    assert len(events) == 1
    assert "601319" in events[0] and "RATE_LIMIT" in events[0]


def test_seen_set_suppresses_repeats(scan_dir):
    """已播过的票不重播(Monitor 每轮调用,只报增量)。"""
    _write_card(scan_dir, "601319", "Hold")
    _write_book(scan_dir, {"601319": _task("SUCCEEDED", card_hash="abc")})
    snap = snapshot(scan_dir)

    assert render_events(snap, seen=set())
    assert render_events(snap, seen={"601319"}) == []


def test_done_only_when_every_task_is_terminal(scan_dir):
    _write_book(scan_dir, {
        "601319": _task("SUCCEEDED", card_hash="abc"),
        "300857": _task("RUNNING", started_at="2026-07-28T14:23:29Z"),
    })
    assert snapshot(scan_dir)["done"] is False

    _write_book(scan_dir, {
        "601319": _task("SUCCEEDED", card_hash="abc"),
        "300857": _task("FAILED", error="CONTRACT"),
    })
    assert snapshot(scan_dir)["done"] is True, "FAILED 也是终态 —— 否则监视器永不退出"


def test_missing_book_is_reported_not_guessed(scan_dir):
    """账本还没生成:报"未就绪",不猜、不当成 0/0 完成。"""
    snap = snapshot(scan_dir)
    assert snap["ready"] is False
    assert snap["done"] is False


def test_fold_disagreement_is_flagged(scan_dir):
    """卡片评级 ≠ 复核中位 → 必须提示终评以 assemble 折回为准。

    07-28 实况回归:688766 卡片 Underweight,sell_review 三跑中位 Hold。
    我当时照卡片播了 UW 又更正 —— 播报层要么别播,要么把待结算摆出来。
    """
    _write_card(scan_dir, "688766", "Underweight")
    (scan_dir / "_ensemble_688766.json").write_text(json.dumps({
        "code": "688766", "ratings": ["Underweight", "Hold", "Hold"],
        "median": "Hold", "trigger": "sell_review", "n_runs": 3,
    }), encoding="utf-8")

    lines = pending_fold_lines(scan_dir)

    assert any("688766" in ln and "Hold" in ln for ln in lines)
    assert any("assemble" in ln for ln in lines)


def test_fold_agreement_is_silent(scan_dir):
    """复核中位与卡片一致 → 不打扰(601869 [UW,UW] 同档即此形)。"""
    _write_card(scan_dir, "601869", "Underweight")
    (scan_dir / "_ensemble_601869.json").write_text(json.dumps({
        "code": "601869", "ratings": ["Underweight", "Underweight"],
        "median": "Underweight", "trigger": "sell_review", "n_runs": 2,
    }), encoding="utf-8")

    assert pending_fold_lines(scan_dir) == []
