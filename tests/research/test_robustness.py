"""F5 步 3/4:块长敏感性(全报,不挑)+ walk-forward 的标签重叠清除与交易日 embargo。

三条被钉死的判断:

1. **块长要预登记且全部报告**。事后挑最显著的那个长度,等于又做了一次没登记的检验。
2. **标签重叠必须按每行的 label 区间清除**,不是按「训练集结束日」一刀切:`fwd_10_oc` 的
   标签跨 10 个 session,训练集最后 10 行的标签早就伸进测试期了。
3. **embargo 数交易日,不数自然日**。两个自然日碰上周末就是零个交易日,那道闸等于没关。
"""
from datetime import datetime, timezone

import pytest

from autoresearch.research import robustness as rb

SESSIONS = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04",
            "2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10"]   # 09-05/06 是周末


def dt(day, hour=15):
    return datetime.fromisoformat(f"{day}T{hour:02d}:00:00+08:00")


def row(start, end, **extra):
    return dict({"label_start": dt(start), "label_end": dt(end)}, **extra)


# ───────────────────────── ① 块长敏感性 ─────────────────────────

def test_preregistered_blocks_match_the_production_ruler_horizons():
    """块长集合与 `scan.populations.RULER_BLOCK` 同源 —— 主尺 1 日、fwd_5 5 日、fwd_10 10 日。"""
    from autoresearch.scan import populations
    assert set(rb.BLOCK_SENSITIVITY) == set(populations.RULER_BLOCK.values())


def test_block_sensitivity_reports_every_registered_length():
    got = rb.block_sensitivity([0.01, -0.02, 0.03] * 20, seed=7)
    assert [row["block"] for row in got] == list(rb.BLOCK_SENSITIVITY)
    assert all(row["status"] in {"COMPUTED", "INSUFFICIENT_BLOCKS"} for row in got)


def test_block_sensitivity_does_not_pick_a_winner():
    """返回的是全部长度的读数,没有「best」/「selected」这类字段可供事后择优。"""
    got = rb.block_sensitivity([0.01] * 40, seed=7)
    assert all(set(row) == {"block", "point", "lo", "hi", "status"} for row in got)


def test_block_sensitivity_marks_short_samples_instead_of_dropping_them():
    got = rb.block_sensitivity([0.01] * 6, seed=7)
    by_block = {row["block"]: row for row in got}
    assert by_block[1]["status"] == "COMPUTED"
    assert by_block[10]["status"] == "INSUFFICIENT_BLOCKS"     # 6 个观测装不下 10 日块
    assert by_block[10]["lo"] is None


# ───────────────────────── ② 标签重叠清除 ─────────────────────────

def test_overlap_is_half_open():
    """左闭右开:测试期起点恰等于训练标签终点 → **不**算重叠,否则会白扔一行。"""
    assert not rb.overlaps(dt("2026-09-01"), dt("2026-09-03"),
                           dt("2026-09-03"), dt("2026-09-04"))
    assert rb.overlaps(dt("2026-09-01"), dt("2026-09-04"),
                       dt("2026-09-03"), dt("2026-09-05"))


def test_purge_drops_only_rows_whose_label_reaches_into_the_test_period():
    train = [row("2026-09-01", "2026-09-02", tag="clean"),
             row("2026-09-02", "2026-09-08", tag="reaches_in"),
             row("2026-09-09", "2026-09-10", tag="after")]
    kept = rb.purge_overlap(train, [(dt("2026-09-07"), dt("2026-09-09"))])
    assert [r["tag"] for r in kept] == ["clean", "after"]


def test_purge_on_empty_test_intervals_keeps_everything():
    train = [row("2026-09-01", "2026-09-02")]
    assert rb.purge_overlap(train, []) == train


def test_purge_requires_timezone_aware_intervals():
    naive = {"label_start": datetime(2026, 9, 1), "label_end": datetime(2026, 9, 2)}
    with pytest.raises(ValueError):
        rb.purge_overlap([naive], [(dt("2026-09-01"), dt("2026-09-02"))])


def test_purge_rejects_inverted_label_windows():
    with pytest.raises(ValueError):
        rb.purge_overlap([row("2026-09-03", "2026-09-01")],
                         [(dt("2026-09-07"), dt("2026-09-09"))])


# ───────────────────────── ③ embargo 数交易日 ─────────────────────────

def test_embargo_counts_trading_sessions_not_calendar_days():
    """测试期到 09-04 收盘;embargo 2 个 session 应推到 09-08,跨过周末的 09-05/06。"""
    (start, end), = rb.embargo_intervals([(dt("2026-09-03"), dt("2026-09-04"))],
                                         sessions=SESSIONS, embargo_sessions=2)
    assert start == dt("2026-09-03")
    assert end.date().isoformat() == "2026-09-08"


def test_zero_embargo_leaves_the_interval_untouched():
    intervals = [(dt("2026-09-03"), dt("2026-09-04"))]
    assert rb.embargo_intervals(intervals, sessions=SESSIONS, embargo_sessions=0) == intervals


def test_embargo_past_the_calendar_end_is_refused_not_clamped():
    """日历只到 09-10,还要往后 5 个 session —— 那是「不知道」,不是「就到 09-10」。"""
    with pytest.raises(ValueError):
        rb.embargo_intervals([(dt("2026-09-09"), dt("2026-09-10"))],
                             sessions=SESSIONS, embargo_sessions=5)


def test_embargo_needs_the_interval_end_to_be_a_session():
    with pytest.raises(ValueError):
        rb.embargo_intervals([(dt("2026-09-05"), dt("2026-09-06"))],
                             sessions=SESSIONS, embargo_sessions=1)


@pytest.mark.parametrize("bad", [-1, 1.5, True])
def test_invalid_embargo_length_is_rejected(bad):
    with pytest.raises(ValueError):
        rb.embargo_intervals([(dt("2026-09-03"), dt("2026-09-04"))],
                             sessions=SESSIONS, embargo_sessions=bad)


def test_split_train_purges_after_applying_embargo():
    """先扩 embargo 再清重叠 —— 反过来的话,禁运期里的行会被留下。"""
    train = [row("2026-09-01", "2026-09-02", tag="clean"),
             row("2026-09-07", "2026-09-08", tag="inside_embargo"),
             row("2026-09-09", "2026-09-10", tag="after_embargo")]
    kept = rb.split_train(train, [(dt("2026-09-03"), dt("2026-09-04"))],
                          sessions=SESSIONS, embargo_sessions=2)
    assert [r["tag"] for r in kept] == ["clean", "after_embargo"]


def test_split_train_without_embargo_keeps_the_row_right_after_the_test_period():
    train = [row("2026-09-07", "2026-09-08", tag="inside_embargo")]
    assert rb.split_train(train, [(dt("2026-09-03"), dt("2026-09-04"))],
                          sessions=SESSIONS, embargo_sessions=0) == train


def test_utc_and_shanghai_spellings_of_the_same_instant_agree():
    utc_row = {"label_start": datetime(2026, 9, 1, 7, tzinfo=timezone.utc),
               "label_end": datetime(2026, 9, 8, 7, tzinfo=timezone.utc)}
    assert rb.purge_overlap([utc_row], [(dt("2026-09-07"), dt("2026-09-09"))]) == []
