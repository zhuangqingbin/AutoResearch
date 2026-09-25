"""endpoint policy registry — settle/key/source classification."""

import pytest

from autoresearch.data import endpoints


def test_daily_is_settled_eod_tushare():
    pol = endpoints.policy("daily")
    assert pol["settle"] == "eod"
    assert pol["key"] == "date"
    assert pol["source"] == "tushare"


def test_spot_em_is_live_and_uncached():
    pol = endpoints.policy("stock_zh_a_spot_em")
    assert pol["settle"] == "live"
    assert pol["key"] is None
    assert pol["source"] == "akshare"


def test_as_of_snapshot_endpoint():
    # 股东户数 = per-fetch-day snapshot, keyed by entity@as_of
    pol = endpoints.policy("stock_zh_a_gdhs_detail_em")
    assert pol["key"] == "as_of"
    assert pol["settle"] == "eod"


def test_static_endpoint():
    pol = endpoints.policy("stock_basic")
    assert pol["key"] == "static"


def test_fred_source():
    # any FRED series id resolves to the fred source with eod settle
    pol = endpoints.policy("fred")
    assert pol["source"] == "fred"
    assert pol["settle"] == "eod"


def test_unknown_endpoint_raises_keyerror():
    with pytest.raises(KeyError):
        endpoints.policy("nope_not_a_real_endpoint")


def test_every_entry_is_wellformed():
    for name, pol in endpoints.ENDPOINTS.items():
        assert pol["key"] in {"date", "period", "as_of", "static", None}, name
        assert pol["settle"] in {"eod", "live"}, name
        assert pol["source"] in {"tushare", "akshare", "eastmoney", "fred", "yfinance",
                                 "cboe", "official", "sec", "csindex"}, name
        # live endpoints must not be keyed (they are never written to the lake)
        if pol["settle"] == "live":
            assert pol["key"] is None, name
        # snapshot(不可回填的观测型端点):只对入湖端点有意义,且必须是 as_of 键
        # ——按天留底才谈得上"今天的观测只能写今天"(cache 的 SnapshotDateError 守门)。
        if pol.get("snapshot"):
            assert pol["key"] == "as_of", name
            assert pol["settle"] == "eod", name
        assert set(pol) <= {"key", "settle", "source", "snapshot",
                            *endpoints.FRESHNESS_KEYS}, name


def test_freshness_contract_is_all_or_nothing():
    """时效三键要么全声明、要么全不声明(design 2026-08-28 §9)。

    只写一半 = 消费端读到 `max_stale=None` 就当"没有上限",而这正是 §9 要禁的
    「静默沿用旧值」。数值本身也要自洽:`freshness_slo ≤ max_stale`,否则"陈旧带"是空的,
    `stale_on_error` 永远轮不到被消费(登记了却永不生效的字段 = 没写)。
    """
    for name in endpoints.ENDPOINTS:
        f = endpoints.freshness(name)
        declared = [k for k, v in f.items() if v is not None]
        assert len(declared) in (0, 3), f"{name}: 时效契约只写了一半 {declared}"
        if not declared:
            continue
        assert isinstance(f["stale_on_error"], bool), name
        assert f["freshness_slo"] > 0 and f["max_stale"] > 0, name
        assert f["freshness_slo"] <= f["max_stale"], name
        if f["stale_on_error"] is False:
            assert f["freshness_slo"] < f["max_stale"], \
                f"{name}: stale_on_error=False 却没有陈旧带 → 该字段永远不会被消费"


def test_freshness_state_is_undeclared_for_legacy_endpoints():
    """既有端点没声明时效契约 → 不给它们凭空立规矩(恒 usable,不编造 stale_reason)。"""
    s = endpoints.freshness_state("daily", 10**9)
    assert s["state"] == endpoints.UNDECLARED
    assert s["usable"] is True and s["stale_reason"] == ""
