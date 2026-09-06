"""C1:执行评价的字段契约 —— 字段、类型、身份、时区与非法数量。

纪律:**每个字段都必须出现**,合法未知值是 `null`;身份/schema/code/engine/payload_hash
不允许 null。「字段缺席」与「字段为未知」是两件事——前者是导入器漏了,后者是来源没有,
把前者当后者会让缺失静默消失(data-contracts-fail-fast 同族)。
"""
import pytest

from autoresearch.contracts import execution as ex


def snapshot(**changes):
    row = {
        "schema_version": ex.SNAPSHOT_SCHEMA_VERSION, "snapshot_id": "snap-1",
        "engine": "claude", "run_id": "20260901T000000000001Z", "code": "600000",
        "venue": "SSE", "session_date": "2026-09-01",
        "decision_at": "2026-09-01T14:45:00+08:00",
        "market_event_at": "2026-09-01T14:44:58+08:00",
        "provider_published_at": "2026-09-01T14:44:59+08:00",
        "received_at": "2026-09-01T14:45:00+08:00",
        "persisted_at": "2026-09-01T14:45:01+08:00",
        "timezone": "Asia/Shanghai", "last": "10.10", "previous_close": "10.00",
        "high_so_far": "10.40", "low_so_far": "10.00", "volume_so_far": "1000",
        "amount_so_far": "10100", "suspended": False,
        "limit_up_price": "11.00", "limit_down_price": "9.00",
        "price_adjustment_basis": "none", "source_observation_id": "obs-1",
        "payload_hash": "a" * 64, "timestamp_precision": "second", "quality_flags": [],
    }
    return dict(row, **changes)


def test_fixture_covers_every_declared_field():
    """夹具漏字段 = 后面所有「缺字段被拒」的用例都在测一个不完整的基线。"""
    assert set(snapshot()) == set(ex.SNAPSHOT_FIELDS)


def test_valid_snapshot_passes():
    assert ex.validate_snapshot(snapshot()) == snapshot()


@pytest.mark.parametrize("field", sorted(ex.SNAPSHOT_FIELDS))
def test_every_missing_field_is_rejected(field):
    row = snapshot()
    del row[field]
    with pytest.raises(ValueError):
        ex.validate_snapshot(row)


def test_unknown_extra_field_is_rejected():
    with pytest.raises(ValueError):
        ex.validate_snapshot(snapshot(entry_vwap="10.2"))


@pytest.mark.parametrize("field", sorted(ex.SNAPSHOT_IDENTITY_FIELDS))
def test_identity_fields_may_not_be_null(field):
    with pytest.raises(ValueError):
        ex.validate_snapshot(snapshot(**{field: None}))


@pytest.mark.parametrize("field", ["market_event_at", "provider_published_at",
                                   "high_so_far", "limit_up_price", "quality_flags"])
def test_unknown_is_spelled_null_and_accepted(field):
    ex.validate_snapshot(snapshot(**{field: None}))


@pytest.mark.parametrize("bad", ["10.1", 10.1, "60000", "600000.SH", ""])
def test_invalid_code_is_rejected(bad):
    with pytest.raises(ValueError):
        ex.validate_snapshot(snapshot(code=bad))


@pytest.mark.parametrize("bad", [10.1, 10, "abc", "NaN", "Infinity", "-1"])
def test_prices_must_be_nonnegative_decimal_strings(bad):
    """float 会把 10.1 存成 10.099999…;金额口径上这是不可接受的静默失真。"""
    with pytest.raises(ValueError):
        ex.validate_snapshot(snapshot(last=bad))


def test_naive_timestamp_is_rejected():
    with pytest.raises(ValueError):
        ex.validate_snapshot(snapshot(decision_at="2026-09-01T14:45:00"))


@pytest.mark.parametrize("value", [True, False, None])
def test_suspended_accepts_boolean_or_unknown(value):
    ex.validate_snapshot(snapshot(suspended=value))


@pytest.mark.parametrize("bad", ["yes", 1, 0, "true"])
def test_suspended_rejects_truthy_stand_ins(bad):
    """`1` 在 Python 里 == True —— 用 `type(...) is bool` 把它挡住,否则来源的 0/1 会被
    当成布尔悄悄流进「停牌与否」。"""
    with pytest.raises(ValueError):
        ex.validate_snapshot(snapshot(suspended=bad))


def test_quality_flags_must_be_a_list_of_reason_codes():
    ex.validate_snapshot(snapshot(quality_flags=["STALE_FEED"]))
    with pytest.raises(ValueError):
        ex.validate_snapshot(snapshot(quality_flags="STALE_FEED"))
    with pytest.raises(ValueError):
        ex.validate_snapshot(snapshot(quality_flags=[1]))


def test_payload_hash_must_be_sha256_hex():
    with pytest.raises(ValueError):
        ex.validate_snapshot(snapshot(payload_hash="deadbeef"))


def test_state_vocabularies_are_closed_and_do_not_overlap_in_meaning():
    assert set(ex.EVIDENCE_MODES) == {"EOD_PROXY", "SNAPSHOT_SIMULATED", "OBSERVED_FILL"}
    assert "NOT_DUE" in ex.EXIT_STATES and "NOT_DUE" not in ex.ENTRY_STATES
    assert "NOT_SUBMITTED" in ex.ENTRY_STATES and "NOT_SUBMITTED" not in ex.EXIT_STATES
    assert "UNKNOWN" in ex.ENTRY_STATES and "UNKNOWN" in ex.EXIT_STATES


def test_actionability_literal_does_not_drift_from_exec_anchor():
    """contracts 不能 import scan(向上),所以字面量靠这条测试锁死,不靠 import。"""
    from autoresearch.scan import exec_anchor

    assert exec_anchor.ACTIONABLE == ex.ACTIONABLE
    assert exec_anchor.EXEC_DECISION_CUTOFF.strftime("%H:%M") == ex.EXEC_DECISION_CUTOFF_HHMM
