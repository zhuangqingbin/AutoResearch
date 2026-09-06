"""C1/C2:时点可见性、决策时点派生与快照入场条件。

三条被这组测试钉死的判断:

1. **晚收到 ≠ 当时可见**,**未来发生 ≠ 可用**。两者都不能算成 `AVAILABLE`,也都不是同一个
   原因码——混成一个,复盘时就分不清是「系统慢」还是「拿了未来信息」。
2. **`decision_at` 不由导入文件自己填**,从 run 的时间锚派生;非 ACTIONABLE 的 run
   (08-28 实测 61 个 run 里 8 个,13%,在 T+1 收盘后才批准)拿不到决策时点。
3. **快照条件不是成交证明**。封涨停报价推不出「买得到」——那是 08-28 普查「收益随买得到
   的可能性单调递减」的机制所在。
"""
import pytest

from autoresearch.common import execution_math as em


def snapshot_times(**changes):
    row = {
        "decision_at": "2026-09-01T14:45:00+08:00",
        "market_event_at": "2026-09-01T14:44:58+08:00",
        "provider_published_at": "2026-09-01T14:44:59+08:00",
        "received_at": "2026-09-01T14:45:00+08:00",
        "persisted_at": "2026-09-01T14:45:01+08:00",
    }
    return dict(row, **changes)


def market_row(**changes):
    row = dict(snapshot_times(), last="10.1", previous_close="10", high_so_far="10.4",
               low_so_far="10", suspended=False, limit_up_price=None)
    return dict(row, **changes)


# ───────────────────────── ① 可见性 ─────────────────────────

def test_received_before_cutoff_can_be_available():
    assert em.snapshot_visibility(snapshot_times()) == "AVAILABLE"


def test_late_import_does_not_prove_live_visibility():
    assert em.snapshot_visibility(snapshot_times(
        received_at="2026-09-02T09:00:00+08:00",
        persisted_at="2026-09-02T09:00:01+08:00")) == "NOT_OBSERVED_AT_DECISION"


def test_future_market_data_is_not_available():
    assert em.snapshot_visibility(snapshot_times(
        market_event_at="2026-09-01T15:00:00+08:00",
        provider_published_at="2026-09-01T15:00:01+08:00",
        received_at="2026-09-01T15:00:02+08:00",
        persisted_at="2026-09-01T15:00:03+08:00")) == "FUTURE_INFORMATION"


def test_event_before_cutoff_but_published_after_is_still_future_information():
    """行情发生在截止前、供应商却在截止后才发布 —— 当时看不到它,不能算「当时的判断」。

    这条用例让 `published > decision` 那一腿有鉴别力:只测「两个时点一起挪到未来」的话,
    删掉这一腿测试照样全绿(靠 `market > decision` 兜住了)。
    """
    assert em.snapshot_visibility(snapshot_times(
        provider_published_at="2026-09-01T14:46:00+08:00",
        received_at="2026-09-01T14:47:00+08:00",
        persisted_at="2026-09-01T14:47:01+08:00")) == "FUTURE_INFORMATION"


def test_unknown_source_time_remains_unknown():
    assert em.snapshot_visibility(snapshot_times(provider_published_at=None)) == "UNKNOWN"


def test_inverted_clock_is_its_own_reason_code():
    """收到早于发布 = 时钟/字段错位,不是「可用」也不是「未知」。"""
    assert em.snapshot_visibility(snapshot_times(
        received_at="2026-09-01T14:44:00+08:00")) == "INVALID_TIME_ORDER"


def test_same_instant_in_another_timezone_is_the_same_instant():
    """+08:00 的 14:45 与 UTC 的 06:45 是同一时刻;时区换算错会把可得判成不可得。"""
    assert em.snapshot_visibility(snapshot_times(
        decision_at="2026-09-01T06:45:00+00:00")) == "AVAILABLE"


def test_naive_timestamp_is_rejected_not_guessed():
    with pytest.raises(ValueError):
        em.snapshot_visibility(snapshot_times(decision_at="2026-09-01T14:45:00"))


def test_visibility_is_a_closed_vocabulary():
    from autoresearch.contracts import execution as ex
    assert em.snapshot_visibility(snapshot_times()) in ex.VISIBILITY_STATES


# ───────────────────────── ② 决策时点从 run 时间锚派生 ─────────────────────────

def execution_block(**changes):
    block = {"actionability_status": "ACTIONABLE", "first_available_session": "2026-09-02",
             "exec_decision_cutoff": "14:45", "timezone_assumed": "Asia/Shanghai"}
    return dict(block, **changes)


def test_decision_at_is_the_cutoff_of_the_first_available_session():
    moment, status = em.decision_at_from_execution_block(execution_block())
    assert status == "ACTIONABLE"
    assert moment.isoformat() == "2026-09-02T14:45:00+08:00"


@pytest.mark.parametrize("status", ["LATE_REVALIDATION_REQUIRED", "EXPIRED",
                                    "NOT_APPROVED", "UNKNOWN"])
def test_non_actionable_runs_get_no_decision_time(status):
    """08-28 的教训:账本给这些 run 记的是一笔已经过去、下不了的单。"""
    moment, got = em.decision_at_from_execution_block(execution_block(actionability_status=status))
    assert moment is None
    assert got == status


def test_missing_session_is_unknown_not_today():
    moment, status = em.decision_at_from_execution_block(
        execution_block(first_available_session=None))
    assert moment is None and status == "UNKNOWN"


def test_missing_cutoff_or_timezone_is_refused_not_defaulted():
    """缺截止时刻/时区就猜一个,等于把 14:45 这条运营纪律悄悄改掉。"""
    with pytest.raises(ValueError):
        em.decision_at_from_execution_block(execution_block(exec_decision_cutoff=None))
    with pytest.raises(ValueError):
        em.decision_at_from_execution_block(execution_block(timezone_assumed=None))


# ───────────────────────── ③ 快照入场条件 ─────────────────────────

def test_snapshot_condition_is_not_close_fill():
    assert em.entry_condition(market_row(), max_age_seconds=60)["verdict"] == "PASS"
    assert "entry_vwap" not in em.entry_condition(market_row(), max_age_seconds=60)
    assert em.entry_condition(market_row(), max_age_seconds=1)["verdict"] == "UNKNOWN"


def test_stale_snapshot_says_so():
    assert em.entry_condition(market_row(), max_age_seconds=1)["reason"] == "STALE_SNAPSHOT"


def test_limit_up_quote_is_not_buyable():
    """封涨停报价 ⇒ 排板成交概率≈0。没有队列/盘口证据就不能从 last==limit 推出买得到。"""
    row = market_row(last="11", previous_close="10", high_so_far="11", low_so_far="10.2",
                     limit_up_price="11")
    assert em.entry_condition(row, max_age_seconds=60) == {"verdict": "UNKNOWN",
                                                           "reason": "LIMIT_UP_QUEUE"}


def test_suspended_is_a_hard_fail_but_unknown_suspension_is_not():
    assert em.entry_condition(market_row(suspended=True), max_age_seconds=60) == {
        "verdict": "FAIL", "reason": "SUSPENDED"}
    assert em.entry_condition(market_row(suspended=None),
                              max_age_seconds=60)["reason"] == "MISSING_MARKET_FIELDS"


def test_missing_market_fields_are_unknown_not_pass():
    assert em.entry_condition(market_row(high_so_far=None),
                              max_age_seconds=60)["reason"] == "MISSING_MARKET_FIELDS"


def test_zero_range_has_no_position_in_range():
    row = market_row(last="10", previous_close="10", high_so_far="10", low_so_far="10")
    assert em.entry_condition(row, max_age_seconds=60)["reason"] == "ZERO_RANGE"


def test_thresholds_come_from_the_contract_not_from_a_local_literal():
    """阈值单源在 `contracts.agent_output`;这里只验等号方向与卡面两行同义。"""
    from autoresearch.contracts.agent_output import (
        EXEC_LINE_MAX_PCT_1D,
        EXEC_LINE_MAX_POS_IN_RANGE,
    )
    at_limit = market_row(previous_close="10",
                          last=str(10 * (1 + EXEC_LINE_MAX_PCT_1D / 100)),
                          low_so_far="10", high_so_far="100")
    assert em.entry_condition(at_limit, max_age_seconds=60)["verdict"] == "PASS"   # `<=`
    over = market_row(previous_close="10", last="10.31", low_so_far="10", high_so_far="100")
    assert em.entry_condition(over, max_age_seconds=60)["verdict"] == "FAIL"
    # pos_in_range 是**严格小于**:恰好落在阈值上算不过。数字取能被 Decimal 精确表示的
    # (8−1)/(11−1)=0.7,别用 1/0.7 反算 —— 浮点让它落不到边界上,这条断言就永远是 PASS。
    assert EXEC_LINE_MAX_POS_IN_RANGE == 0.7
    on_pos = market_row(previous_close="8", last="8", low_so_far="1", high_so_far="11")
    assert em.entry_condition(on_pos, max_age_seconds=60)["verdict"] == "FAIL"
    just_under = market_row(previous_close="8", last="7.9", low_so_far="1", high_so_far="11")
    assert em.entry_condition(just_under, max_age_seconds=60)["verdict"] == "PASS"


def test_time_not_verified_short_circuits_before_market_fields():
    """时点没验过就先返回,不去读价格 —— 否则「未来信息」会以 PASS/FAIL 的形式出现。"""
    row = market_row(received_at="2026-09-02T09:00:00+08:00",
                     persisted_at="2026-09-02T09:00:01+08:00")
    assert em.entry_condition(row, max_age_seconds=60) == {"verdict": "UNKNOWN",
                                                           "reason": "TIME_NOT_VERIFIED"}


def test_negative_freshness_policy_is_a_programming_error():
    with pytest.raises(ValueError):
        em.entry_condition(market_row(), max_age_seconds=-1)


@pytest.mark.parametrize("changes", [{"previous_close": "0"}, {"low_so_far": "0"},
                                     {"last": "99", "high_so_far": "10.4"}])
def test_impossible_price_shapes_raise(changes):
    with pytest.raises(ValueError):
        em.entry_condition(market_row(**changes), max_age_seconds=60)
