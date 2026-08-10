"""B5 判据的计量:结构失败落盘、不可测不冒充干净、开发期重建不算失败。

design: docs/specs/2026-08-01-wave10-report-ops-slimdown-zerobuy-design.md §B5
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from autoresearch.scan import structural_audit as sa
from autoresearch.scan.l4_tasks import initialize, mark_success, preflight

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


def _book(tmp_path, code="000001"):
    scan = tmp_path / DATE
    scan.mkdir(parents=True, exist_ok=True)
    (scan / f"_l4_prompt_{code}.md").write_text("# 任务包\n", encoding="utf-8")
    return initialize(DATE, [code], root=tmp_path, context_root=tmp_path / "context",
                      meta={code: {"ticker": f"{code}.SZ", "pinned": False}}, now=NOW)


def _payload(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _events(path, kind=None) -> list:
    log = _payload(path).get(sa.EVENTS_KEY) or []
    return [e for e in log if kind is None or e["kind"] == kind]


# ── 计量本身 ────────────────────────────────────────────────────────────────

def test_fresh_book_carries_empty_log_so_clean_is_distinguishable_from_unmeasured(tmp_path):
    """空表必须显式写:否则「干净的已计量日」和「计量前的日子」在盘上无法区分。"""
    book = _book(tmp_path)
    payload = _payload(book["path"])
    assert payload[sa.EVENTS_KEY] == []

    audit = sa.audit_day(tmp_path / DATE)
    assert audit.measured is True
    assert audit.failure_n == 0
    assert audit.clean is True


def test_legacy_book_is_unmeasured_not_clean(tmp_path):
    """计量上线前的账本:failure_n 必须是 None,**不是 0** —— 「没看」不能冒充「没有」。"""
    book = _book(tmp_path)
    path = tmp_path / DATE / "_l4_tasks.json"
    payload = _payload(path)
    del payload[sa.EVENTS_KEY]                      # 退回计量上线前的形状
    path.write_text(json.dumps(payload), encoding="utf-8")

    audit = sa.audit_day(tmp_path / DATE)
    assert audit.measured is False
    assert audit.failure_n is None
    assert audit.clean is None
    assert book["ok"]


def test_record_refuses_to_retrofit_the_key_onto_a_legacy_book(tmp_path):
    """补建键 = 给那天伪造「已计量」身份,而它的历史事件早已丢失。"""
    payload = {"tasks": {}}
    assert sa.record(payload, "000001", sa.TERMINAL_RERUN) is None
    assert sa.EVENTS_KEY not in payload


def test_record_rejects_unknown_kind(tmp_path):
    payload = {"tasks": {}, sa.EVENTS_KEY: []}
    with pytest.raises(ValueError, match="unknown structural event kind"):
        sa.record(payload, "000001", "SOMETHING_ELSE")


# ── streak:B5 那根杆的判据 ──────────────────────────────────────────────────

def _audit(date, measured=True, failure_n=0):
    return sa.DayAudit(date, measured, failure_n, 0, {}, "")


def test_streak_counts_consecutive_clean_runs():
    audits = [_audit("2026-07-27"), _audit("2026-07-28"), _audit("2026-07-29")]
    assert sa.streak(audits)["streak"] == 3


def test_unmeasured_day_breaks_the_streak_exactly_like_a_failure():
    """本模块存在的全部理由:不可测 ≠ 干净。"""
    audits = [_audit("2026-07-27"), _audit("2026-07-28", measured=False, failure_n=None),
              _audit("2026-07-29")]
    stats = sa.streak(audits)
    assert stats["streak"] == 1                      # 只数到不可测日为止
    assert "2026-07-28" in stats["streak_broken_by"]
    assert stats["runs_unmeasured"] == 1


def test_failure_day_breaks_the_streak_and_totals_stay_on_measured_subset():
    audits = [_audit("2026-07-27", failure_n=2), _audit("2026-07-28")]
    stats = sa.streak(audits)
    assert stats["streak"] == 1
    assert stats["failure_n_total"] == 2
    assert stats["as_of"] == "2026-07-28"


def test_missing_task_book_is_a_failure_only_when_the_day_should_have_had_one(tmp_path):
    (tmp_path / DATE).mkdir(parents=True)
    assert sa.audit_day(tmp_path / DATE) is None                       # 非流式日,不入账
    audit = sa.audit_day(tmp_path / DATE, expect_book=True)
    assert audit.failure_n == 1
    assert audit.kinds == {sa.TASK_BOOK_MISSING: 1}


# ── 事后重验:prompt 与 card 的地位不对称 ──────────────────────────────────

def test_post_hoc_prompt_rewrite_is_an_observation_not_a_failure(tmp_path):
    """实测每天都发生(开发期重跑 write_dispatch_pack 原地覆写)。

    prompt 是可重建的**输入**;它事后变了不代表那次运行结构不成立。
    """
    book = _book(tmp_path)
    _files(tmp_path, "000001", "000001.SZ")
    mark_success(book["path"], "000001", now=NOW)
    (tmp_path / DATE / "_l4_prompt_000001.md").write_text("# 重建过", encoding="utf-8")

    audit = sa.audit_day(tmp_path / DATE)
    assert audit.failure_n == 0
    assert audit.kinds.get(sa.PROMPT_REBUILT) == 1


def test_post_hoc_card_rewrite_is_a_real_failure(tmp_path):
    """card 是**结果**;结果在完成后变了才是真的产物 hash 失配。"""
    book = _book(tmp_path)
    _files(tmp_path, "000001", "000001.SZ")
    mark_success(book["path"], "000001", now=NOW)
    (tmp_path / DATE / "details" / "000001.md").write_text("# 被改了", encoding="utf-8")

    audit = sa.audit_day(tmp_path / DATE)
    assert audit.failure_n == 1
    assert audit.kinds.get(sa.ARTIFACT_HASH_MISMATCH) == 1


def test_non_terminal_tasks_are_not_rehashed(tmp_path):
    """未终态票的产物本来就允许在变,不该被当失配。"""
    _book(tmp_path)
    _files(tmp_path, "000001", "000001.SZ")
    audit = sa.audit_day(tmp_path / DATE)             # 从未 mark_success
    assert audit.failure_n == 0


# ── 活体接线:四个检测点真的会落盘 ────────────────────────────────────────

def test_artifact_change_at_preflight_is_recorded(tmp_path):
    book = _book(tmp_path)
    _files(tmp_path, "000001", "000001.SZ")
    mark_success(book["path"], "000001", now=NOW)
    (tmp_path / "context" / "000001.SZ_2026-07-28_slim.md").write_text(
        "变了", encoding="utf-8")

    preflight(book["path"], "000001", now=NOW)
    assert len(_events(book["path"], sa.ARTIFACT_HASH_MISMATCH)) == 1


def test_redispatch_of_verified_success_is_an_observation_not_a_failure(tmp_path):
    """断点续跑本来就会重派;守卫拦住 ≠ 失败,否则 streak 永远绿不了 = 判据废掉。"""
    book = _book(tmp_path)
    _files(tmp_path, "000001", "000001.SZ")
    mark_success(book["path"], "000001", now=NOW)

    assert preflight(book["path"], "000001", now=NOW)["action"] == "SKIP"
    assert len(_events(book["path"], sa.TERMINAL_REDISPATCH_BLOCKED)) == 1
    assert sa.audit_day(tmp_path / DATE).failure_n == 0


def test_stale_running_task_is_recorded_as_completion_misjudged(tmp_path):
    book = _book(tmp_path)
    _files(tmp_path, "000001", "000001.SZ")
    preflight(book["path"], "000001", now=NOW)                 # → RUNNING
    later = NOW + timedelta(seconds=7200)

    preflight(book["path"], "000001", now=later)
    assert len(_events(book["path"], sa.COMPLETION_MISJUDGED)) == 1


def test_mark_success_rejection_survives_the_raise(tmp_path):
    """原先只抛异常:上层一吞,这件事再无痕迹,B5 的判据永远查不到它。"""
    book = _book(tmp_path)
    _files(tmp_path, "000001", "000001.SZ")
    (tmp_path / DATE / "details" / "000001.md").unlink()

    with pytest.raises(ValueError, match="missing artifacts"):
        mark_success(book["path"], "000001", now=NOW)

    assert len(_events(book["path"], sa.COMPLETION_MISJUDGED)) == 1
    assert sa.audit_day(tmp_path / DATE).failure_n == 1


# ── 渲染:分母与不可测必须同屏 ────────────────────────────────────────────

def test_render_shows_denominator_and_refuses_to_hide_unmeasured():
    audits = [_audit("2026-07-27", measured=False, failure_n=None), _audit("2026-07-28")]
    text = "\n".join(sa.render(audits, required=10))
    assert "1/10" in text and "不得退役兜底" in text
    assert sa.UNMEASURED in text
    assert "不可测 1" in text
