"""衍生品 F1–F3 单测 —— §2 的分页/口径/边界/能力门逐条钉死。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from autoresearch.derivatives import cb_gate, options_lake as ol, qvix, style_spread as ss

# ══════════════════ F1 · 强制分页(首页 12,000 是上限不是全量)══════════════════


def _pager(total: int, page_limit: int = ol.PAGE_LIMIT, *, overlap: int = 0):
    rows = [{"ts_code": f"C{i:07d}", "x": i} for i in range(total)]

    def fetch(offset, limit):
        start = max(0, offset - overlap if offset else 0)
        return pd.DataFrame(rows[start:offset + limit])
    return fetch


def test_full_first_page_triggers_another_fetch():
    """首页恰好等于上限 = 分页存在的信号,不是「数据正好这么多」。"""
    result = ol.paginate(_pager(ol.PAGE_LIMIT + 5))
    assert result["pages"] == 2
    assert result["rows_dedup"] == ol.PAGE_LIMIT + 5
    assert result["hit_page_limit"] is True


def test_short_first_page_stops_immediately():
    result = ol.paginate(_pager(100))
    assert result["pages"] == 1 and result["hit_page_limit"] is False


def test_pagination_dedupes_overlapping_boundaries():
    """分页边界重叠不去重 → PCR 分母虚高。"""
    result = ol.paginate(_pager(ol.PAGE_LIMIT + 50, overlap=10))
    assert result["rows_raw"] > result["rows_dedup"]
    assert result["rows_dedup"] == ol.PAGE_LIMIT + 50


def test_exhausting_max_pages_is_reported_as_truncated():
    """跑满 max_pages 仍未见短页 = 还有数据没拉完,必须说出来。"""
    result = ol.paginate(_pager(10_000), page_limit=10, max_pages=3)
    assert result["truncated"] is True and "没拉完" in result["note"]


def test_empty_source_yields_empty_frame():
    result = ol.paginate(lambda o, limit: pd.DataFrame())
    assert result["rows_dedup"] == 0 and result["pages"] == 0


# ══════════════════ F1 · coverage 对账 ══════════════════


def test_missing_metadata_is_listed_not_silently_dropped():
    daily = pd.DataFrame({"ts_code": ["A", "B", "C"]})
    basic = pd.DataFrame({"ts_code": ["A", "B"]})
    report = ol.coverage_report(daily, basic)
    assert report["reconciled"] is False
    assert report["n_missing_metadata"] == 1 and "C" in report["missing_sample"]
    assert "静默消失" in report["why_it_matters"]


def test_full_coverage_reconciles():
    frame = pd.DataFrame({"ts_code": ["A", "B"]})
    assert ol.coverage_report(frame, frame)["reconciled"] is True


def test_coverage_on_empty_daily():
    assert ol.coverage_report(pd.DataFrame({"ts_code": []}),
                              pd.DataFrame({"ts_code": ["A"]}))["coverage_rate"] is None


# ══════════════════ F1 · 联结 + 分桶 ══════════════════


def _daily(rows):
    return pd.DataFrame(rows)


def _basic(rows):
    return pd.DataFrame(rows)


def test_join_derives_expiry_bucket_and_side():
    daily = _daily([{"ts_code": "A", "vol": 10, "oi": 100, "amount": 1000}])
    basic = _basic([{"ts_code": "A", "exchange": "SSE", "call_put": "C",
                     "opt_code": "510050", "maturity_date": "20260810"}])
    joined = ol.join_asof(daily, basic, trade_date="20260801")
    assert joined.iloc[0]["expiry_bucket"] == "8-30d"
    assert joined.iloc[0]["side"] == "call"
    assert joined.iloc[0]["underlying"] == "510050"


def test_join_is_inner_so_orphan_contracts_do_not_pollute():
    daily = _daily([{"ts_code": "A", "vol": 1, "oi": 1, "amount": 1},
                    {"ts_code": "ORPHAN", "vol": 999, "oi": 999, "amount": 999}])
    basic = _basic([{"ts_code": "A", "exchange": "SSE", "call_put": "C",
                     "opt_code": "510050", "maturity_date": "20260810"}])
    assert len(ol.join_asof(daily, basic, trade_date="20260801")) == 1


def test_expiry_bucket_boundaries():
    assert ol.expiry_bucket(3) == "0-7d"
    assert ol.expiry_bucket(30) == "8-30d"
    assert ol.expiry_bucket(365) == "90d+"
    assert ol.expiry_bucket(None) == "unknown"
    assert ol.expiry_bucket(float("nan")) == "unknown"
    assert ol.expiry_bucket(-5) == "unknown"


def test_pcr_is_computed_per_underlying_and_bucket_not_summed_across():
    joined = pd.DataFrame([
        {"underlying": "510050", "expiry_bucket": "8-30d", "side": "call",
         "vol": 100, "oi": 200, "amount": 10},
        {"underlying": "510050", "expiry_bucket": "8-30d", "side": "put",
         "vol": 50, "oi": 400, "amount": 5},
        {"underlying": "510300", "expiry_bucket": "8-30d", "side": "call",
         "vol": 10, "oi": 10, "amount": 1},
    ])
    metrics = ol.bucket_metrics(joined)
    assert len(metrics) == 2                       # 分品种,不合并
    row = metrics[metrics["underlying"] == "510050"].iloc[0]
    assert row["vol_pcr"] == pytest.approx(0.5)
    assert row["oi_pcr"] == pytest.approx(2.0)


def test_zero_call_volume_yields_none_not_zero():
    joined = pd.DataFrame([{"underlying": "X", "expiry_bucket": "8-30d",
                            "side": "put", "vol": 5, "oi": 5, "amount": 1}])
    row = ol.bucket_metrics(joined).iloc[0]
    assert row["vol_pcr"] is None and row["oi_pcr"] is None


def test_bucket_metrics_empty():
    assert not len(ol.bucket_metrics(pd.DataFrame()))


def test_roll_adjustment_only_compares_buckets_present_both_days():
    """换月那天旧合约归零、新合约起步 —— 裸算 ΔOI 每月准时造一个假信号。"""
    today = pd.DataFrame([
        {"underlying": "X", "expiry_bucket": "8-30d", "oi_call": 100, "oi_put": 90},
        {"underlying": "X", "expiry_bucket": "0-7d", "oi_call": 5, "oi_put": 5},
    ])
    prev = pd.DataFrame([
        {"underlying": "X", "expiry_bucket": "8-30d", "oi_call": 80, "oi_put": 100},
    ])
    out = ol.roll_adjusted_oi(today, prev)
    assert len(out) == 1                            # 新出现的桶不参与
    assert out.iloc[0]["d_oi_call"] == 20
    assert out.iloc[0]["d_oi_put"] == -10


def test_roll_adjustment_with_no_overlap():
    today = pd.DataFrame([{"underlying": "X", "expiry_bucket": "0-7d",
                           "oi_call": 1, "oi_put": 1}])
    prev = pd.DataFrame([{"underlying": "Y", "expiry_bucket": "8-30d",
                          "oi_call": 1, "oi_put": 1}])
    assert not len(ol.roll_adjusted_oi(today, prev))


# ══════════════════ F1 · 口径与消费边界 ══════════════════


def test_block_gives_panels_not_a_single_total_pcr():
    """给不出总数是有意的 —— 那个数需要名义/delta 归一才成立。"""
    metrics = ol.bucket_metrics(pd.DataFrame([
        {"underlying": "A", "expiry_bucket": "8-30d", "side": "call",
         "vol": 1, "oi": 1, "amount": 1}]))
    block = ol.derivatives_block(metrics, trade_date="20260731")
    assert "total_pcr" not in block
    assert block["n_panels"] == 1
    assert "不得裸加总" in block["aggregation_rule"]


def test_pcr_semantics_travel_with_the_reading():
    block = ol.derivatives_block(pd.DataFrame())
    assert "无法区分买 put" in block["pcr_semantics"]
    assert "看空" in block["pcr_semantics"]


def test_derivatives_is_not_in_the_strategist_allowlist():
    """§2.2 消费边界:进 allowlist 即 B 类。当前必须在 I 类边界内。"""
    ol.assert_not_in_strategist_allowlist()
    from autoresearch.contracts.strategist_view import ALLOWED_KEYS
    from autoresearch.scan.strategist_pack import ALLOWED_KEYS as SCAN_ALLOWED

    assert "derivatives" not in ALLOWED_KEYS
    assert SCAN_ALLOWED is ALLOWED_KEYS, "scan 侧必须是同对象转发,不是第二份名单"


def test_guard_would_fire_if_it_leaked(monkeypatch):
    """2026-09-07:名单下沉 contracts 后,探针跟着搬 —— 单一事实源换了地方,打它才有鉴别力。"""
    import autoresearch.contracts.strategist_view as sv

    monkeypatch.setattr(sv, "ALLOWED_KEYS", (*sv.ALLOWED_KEYS, "derivatives"))
    with pytest.raises(ol.OptionsError, match="B 类"):
        ol.assert_not_in_strategist_allowlist()


def test_cli_guard_passes():
    assert ol.main(["guard"]) == 0


# ══════════════════ F1 · 最小证伪步:领先性 ══════════════════


def test_lead_lag_separates_lagged_from_same_day():
    dates = [f"2026-06-{i:02d}" for i in range(1, 21)]
    panel = pd.DataFrame({"date": dates, "oi_pcr": np.linspace(1, 2, 20)})
    breadth = pd.DataFrame({"date": dates, "breadth": np.linspace(2, 1, 20)})
    result = ol.lead_lag_check(panel, breadth)
    assert result["status"] == "MEASURED"
    assert result["same_day_corr"] is not None
    assert "同期相关不算领先证据" in result["discipline"]


def test_lead_lag_thin_sample_is_immature_not_no_signal():
    panel = pd.DataFrame({"date": ["2026-06-01"], "oi_pcr": [1.0]})
    breadth = pd.DataFrame({"date": ["2026-06-02"], "breadth": [1.0]})
    result = ol.lead_lag_check(panel, breadth)
    assert result["status"] == "IMMATURE" and "不是" in result["note"]


def test_lead_lag_no_data():
    assert ol.lead_lag_check(pd.DataFrame(), pd.DataFrame())["status"] == "NO_DATA"


def test_maturity_note_rejects_the_naive_60_day_rule():
    dates = [f"2026-06-{i:02d}" for i in range(1, 21)]
    result = ol.lead_lag_check(
        pd.DataFrame({"date": dates, "oi_pcr": np.linspace(1, 2, 20)}),
        pd.DataFrame({"date": dates, "breadth": np.linspace(2, 1, 20)}))
    assert "60 日不是自动充分条件" in result["maturity_note"]


def test_render_flags_unreconciled_coverage():
    block = ol.derivatives_block(pd.DataFrame(),
                                 coverage={"reconciled": False, "coverage_rate": 0.8,
                                           "n_missing_metadata": 12})
    assert "🚨" in ol.render(block)


# ══════════════════ F1 二期 · QVIX 契约 ══════════════════


def test_all_nan_last_row_is_unusable_even_with_many_rows():
    """1000 指数序列的真实症状:接口返回了行,但末行是空壳。"""
    frame = pd.DataFrame({"date": [f"2026-06-{i:02d}" for i in range(1, 11)],
                          "close": [18.0] * 9 + [np.nan]})
    verdict = qvix.check_series("1000index", frame)
    assert verdict.status == "UNUSABLE"
    assert verdict.last_row_all_nan is True
    assert any("末行" in p for p in verdict.problems)


def test_healthy_series_is_ok():
    frame = pd.DataFrame({"date": [f"2026-06-{i:02d}" for i in range(1, 11)],
                          "close": [18.0 + i for i in range(10)]})
    assert qvix.check_series("50etf", frame).status == "OK"


def test_empty_series():
    assert qvix.check_series("x", pd.DataFrame()).status == "EMPTY"
    assert qvix.check_series("x", None).status == "EMPTY"


def test_missing_value_columns_is_unusable():
    frame = pd.DataFrame({"date": ["2026-06-01"], "irrelevant": [1]})
    assert qvix.check_series("x", frame).status == "UNUSABLE"


def test_non_monotonic_dates_are_degraded():
    frame = pd.DataFrame({"date": ["2026-06-01", "2026-06-05", "2026-06-02"],
                          "close": [18.0, 19.0, 20.0]})
    verdict = qvix.check_series("x", frame)
    assert verdict.date_monotonic is False
    assert any("非单调" in p for p in verdict.problems)


def test_out_of_range_values_look_like_misalignment():
    frame = pd.DataFrame({"date": [f"2026-06-{i:02d}" for i in range(1, 4)],
                          "close": [18.0, 19.0, 99_999.0]})
    assert any("数据错位" in p for p in qvix.check_series("x", frame).problems)


def test_percentage_scale_is_tolerated():
    """QVIX 常以百分点计(18.5 = 18.5%)—— 两种量纲都不该报错。"""
    frame = pd.DataFrame({"date": [f"2026-06-{i:02d}" for i in range(1, 4)],
                          "close": [18.5, 20.1, 22.0]})
    assert qvix.check_series("x", frame).status == "OK"


def test_check_all_judges_each_series_independently():
    good = pd.DataFrame({"date": ["2026-06-01", "2026-06-02"], "close": [18.0, 19.0]})
    bad = pd.DataFrame({"date": ["2026-06-01"], "close": [np.nan]})
    report = qvix.check_all({"50etf": good, "1000index": bad})
    assert report["usable"] == ["50etf"]
    assert report["series"]["1000index"]["status"] == "UNUSABLE"


def test_capability_boundary_rejects_skew_substitution():
    assert "不能替代 25Δ skew" in qvix.CAPABILITY_BOUNDARY
    assert "不得固定 q=0" in qvix.CAPABILITY_BOUNDARY


def test_percentile_needs_enough_history():
    short = pd.DataFrame({"close": [1.0] * 10})
    assert qvix.percentile_of_latest(short)["percentile"] is None
    long = pd.DataFrame({"close": list(range(100))})
    assert qvix.percentile_of_latest(long)["percentile"] == pytest.approx(1.0)


def test_qvix_cli(capsys):
    assert qvix.main(["check"]) == 0
    assert "QVIX 序列契约" in capsys.readouterr().out


# ══════════════════ F2 · 风格温差 ══════════════════


def _panel(bucket, value, metric="oi_pcr"):
    return pd.DataFrame([{"underlying": "X", "expiry_bucket": bucket, metric: value}])


def test_unaligned_terms_refuse_to_produce_a_number():
    """换月期两边最近月不同 —— 硬比得到的是期限错配,不是风险偏好。"""
    result = ss.spread(_panel("0-7d", 1.2), _panel("8-30d", 1.0))
    assert result["status"] == ss.UNALIGNED
    assert result["z_spread"] is None and result["raw_spread"] is None
    assert "期限错配" in result["reason"]


def test_aligned_terms_produce_a_raw_spread_marked_exploratory():
    result = ss.spread(_panel("8-30d", 1.4), _panel("8-30d", 1.0))
    assert result["status"] == ss.ALIGNED
    assert result["raw_spread"] == pytest.approx(0.4)
    assert result["raw_spread_exploratory"] is True
    assert "只能是探索特征" in result["exploratory_only"]


def test_z_spread_needs_history_and_is_none_not_zero():
    result = ss.spread(_panel("8-30d", 1.4), _panel("8-30d", 1.0))
    assert result["z_spread"] is None
    assert "不给 z,不是给 0" in result["z_unavailable_reason"]


def test_z_spread_is_computed_from_each_series_own_history():
    history = {ss.MO: pd.Series(list(np.linspace(1.0, 1.4, 80))),
               ss.IO: pd.Series(list(np.linspace(1.0, 1.0, 80)) + [1.0])}
    result = ss.spread(_panel("8-30d", 1.4), _panel("8-30d", 1.0), history=history)
    assert result["mo_z"] is not None
    assert result["z_spread_available"] is (result["io_z"] is not None)


def test_zscore_returns_none_on_flat_history():
    assert ss.zscore(pd.Series([1.0] * 100)) is None


def test_style_spread_is_not_a_regime_signal():
    ss.assert_not_a_regime_signal(("breadth", "momentum", "volatility"))
    with pytest.raises(ss.StyleSpreadError, match="regime"):
        ss.assert_not_a_regime_signal(("breadth", "style_spread_z"))


def test_promotion_gate_requires_incremental_value_and_no_harm():
    assert "no-harm" in ss.PROMOTION_GATE and "仅展示" in ss.PROMOTION_GATE


def test_render_unaligned_says_it_refused():
    md = ss.render(ss.spread(_panel("0-7d", 1.2), _panel("8-30d", 1.0)))
    assert "拒绝出数" in md


def test_render_aligned():
    md = ss.render(ss.spread(_panel("8-30d", 1.4), _panel("8-30d", 1.0)))
    assert "探索特征" in md and "晋升门" in md


def test_style_spread_cli():
    assert ss.main(["guard"]) == 0


# ══════════════════ F3 · capability gate ══════════════════


def test_f3_is_blocked_by_data_today():
    report = cb_gate.evaluate()
    assert report.status == cb_gate.BLOCKED
    assert "historical_conv_price" in report.blocking


def test_the_original_conclusion_is_marked_withdrawn():
    assert "已撤销" in cb_gate.evaluate().withdrawn_conclusion


def test_conv_price_leak_is_named_in_the_evidence():
    gate = next(g for g in cb_gate.evaluate().as_dict()["gates"]
                if g["key"] == "historical_conv_price")
    assert "当前截面" in gate["evidence"] and "泄漏未来" in gate["evidence"]


def test_all_four_gates_are_present():
    assert len(cb_gate.evaluate().gates) == 4


def test_any_open_gate_blocks_the_whole_line():
    """不给部分解锁:三条信号全依赖历史转股价,第一道门不过就是伪 PIT 因子。"""
    gates = cb_gate.default_gates()
    for gate in gates:
        gate.status = cb_gate.PASS
    gates[-1].status = cb_gate.UNKNOWN
    assert cb_gate.evaluate(gates).status == cb_gate.BLOCKED


def test_all_gates_passing_opens_the_line():
    gates = cb_gate.default_gates()
    for gate in gates:
        gate.status = cb_gate.PASS
    report = cb_gate.evaluate(gates)
    assert report.status == cb_gate.OPEN and report.blocking == []
    cb_gate.assert_not_scheduled(report)


def test_scheduling_while_blocked_raises():
    with pytest.raises(RuntimeError, match="BLOCKED_BY_DATA"):
        cb_gate.assert_not_scheduled()


def test_premium_delta_semantics_are_disclaimed():
    discipline = cb_gate.evaluate().signal_discipline
    assert "不天然代表" in discipline and "residual" in discipline


def test_overlapping_findings_cannot_be_double_counted():
    cb_gate.assert_not_double_counted(("premium_change",))
    with pytest.raises(RuntimeError, match="两个独立发现"):
        cb_gate.assert_not_double_counted(("cb_minus_stock_mom", "premium_change"))


def test_routing_forbids_backfilling_fake_pit_factors():
    assert "不回填伪 PIT 因子" in cb_gate.evaluate().routing


def test_cb_gate_cli_exit_zero_because_blocked_is_correct(capsys):
    assert cb_gate.main(["status"]) == 0
    assert "BLOCKED_BY_DATA" in capsys.readouterr().out


# ══════════════════ 端点 / 契约登记 ══════════════════


@pytest.mark.parametrize("endpoint", ["opt_daily", "opt_basic", "cb_daily",
                                      "cb_basic", "cb_call", "cb_price_chg"])
def test_derivative_endpoints_are_registered(endpoint):
    from autoresearch.data.contracts import CONTRACTS, TIER_DEGRADE
    from autoresearch.data.endpoints import policy

    assert policy(endpoint)["source"] == "tushare"
    # 全部 B 级:期权/转债缺失时漏斗照常成立
    assert CONTRACTS[endpoint].tier == TIER_DEGRADE


def test_opt_basic_contract_warns_about_the_page_limit():
    from autoresearch.data.contracts import CONTRACTS

    assert "12,000" in CONTRACTS["opt_basic"].note


def test_cb_basic_contract_warns_about_the_conv_price_leak():
    from autoresearch.data.contracts import CONTRACTS

    assert "泄漏未来" in CONTRACTS["cb_basic"].note
