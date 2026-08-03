"""统计裁决原语单测 —— 重点锁「不显著 ≠ 等价」这条三态纪律。"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from autoresearch.common import stats as st

# ── 分位数(无 scipy 的自带实现必须真的准)──────────────────────────────


@pytest.mark.parametrize("p,expected", [(0.025, -1.959963985), (0.5, 0.0),
                                        (0.975, 1.959963985), (0.9, 1.2815515655)])
def test_norm_ppf_matches_known_quantiles(p, expected):
    assert st.norm_ppf(p) == pytest.approx(expected, abs=1e-8)


def test_norm_ppf_round_trips_through_cdf():
    for p in (0.001, 0.01, 0.3, 0.7, 0.99, 0.999):
        assert st.norm_cdf(st.norm_ppf(p)) == pytest.approx(p, abs=1e-10)


def test_betainc_matches_closed_form_for_integer_params():
    # I_x(1,1) = x;I_x(2,1) = x^2;I_x(1,2) = 1-(1-x)^2
    for x in (0.1, 0.5, 0.9):
        assert st.betainc(1, 1, x) == pytest.approx(x, abs=1e-12)
        assert st.betainc(2, 1, x) == pytest.approx(x ** 2, abs=1e-12)
        assert st.betainc(1, 2, x) == pytest.approx(1 - (1 - x) ** 2, abs=1e-12)


def test_beta_ppf_inverts_betainc():
    for a, b in ((2.0, 5.0), (7.5, 1.5), (1.0, 1.0)):
        for p in (0.025, 0.5, 0.975):
            assert st.betainc(a, b, st.beta_ppf(p, a, b)) == pytest.approx(p, abs=1e-9)


# ── Beta-Binomial ────────────────────────────────────────────────────


def test_beta_binomial_interval_brackets_point():
    iv = st.beta_binomial_interval(2, 6)
    assert iv.point == pytest.approx(2 / 6)
    assert iv.lo < iv.point < iv.hi
    assert iv.n == 6


def test_beta_binomial_zero_n_is_empty_not_half():
    """n=0 不得渲染成 0.5 —— 「没有观测」与「一半一半」是两件事。"""
    iv = st.beta_binomial_interval(0, 0)
    assert iv.point is None and iv.lo is None and iv.hi is None


def test_beta_binomial_narrows_with_n():
    small = st.beta_binomial_interval(3, 6)
    large = st.beta_binomial_interval(300, 600)
    assert large.width < small.width


def test_beta_binomial_rejects_impossible_counts():
    with pytest.raises(ValueError):
        st.beta_binomial_interval(7, 6)


# ── date-cluster bootstrap ───────────────────────────────────────────


def _frame(per_day: dict[str, list[float]]) -> pd.DataFrame:
    rows = [{"date": d, "x": v} for d, vals in per_day.items() for v in vals]
    return pd.DataFrame(rows)


def test_cluster_bootstrap_is_deterministic():
    f = _frame({f"2026-07-{i:02d}": [0.1 * i, 0.2 * i] for i in range(1, 12)})
    a = st.date_cluster_bootstrap(f, "x", n_boot=200)
    b = st.date_cluster_bootstrap(f, "x", n_boot=200)
    assert (a.lo, a.hi, a.point) == (b.lo, b.hi, b.point)


def test_cluster_bootstrap_is_wider_than_row_level_illusion():
    """同一天 60 只票不是 60 个独立样本 —— 聚簇区间必须比「假装独立」宽。"""
    per_day = {f"2026-07-{i:02d}": list(np.linspace(-1, 1, 60) + i * 0.4)
               for i in range(1, 9)}
    clustered = st.date_cluster_bootstrap(_frame(per_day), "x", n_boot=400)
    flat = _frame(per_day).assign(date=lambda d: [str(i) for i in range(len(d))])
    naive = st.date_cluster_bootstrap(flat, "x", n_boot=400)
    assert clustered.width > naive.width


def test_cluster_bootstrap_single_day_reports_no_interval():
    iv = st.date_cluster_bootstrap(_frame({"2026-07-01": [1.0, 2.0, 3.0]}), "x")
    assert iv.point == pytest.approx(2.0)
    assert iv.lo is None and iv.hi is None and iv.n_clusters == 1


def test_cluster_bootstrap_empty_is_empty():
    iv = st.date_cluster_bootstrap(pd.DataFrame({"date": [], "x": []}), "x")
    assert iv.point is None and iv.n == 0


def test_paired_delta_pairs_before_aggregating():
    frame = pd.DataFrame({
        "date": ["2026-07-01"] * 3 + ["2026-07-02"] * 3,
        "actual": [1.0, 2.0, 3.0, 11.0, 12.0, 13.0],
        "baseline": [0.5, 1.5, 2.5, 10.5, 11.5, 12.5],
    })
    iv = st.paired_delta_interval(frame, "actual", "baseline", n_boot=200)
    assert iv.point == pytest.approx(0.5)      # 市场共同项(+10)被配对消掉
    assert iv.n == 6 and iv.n_clusters == 2


# ── 三态判决:这是本模块存在的理由 ───────────────────────────────────


def test_wide_interval_spanning_zero_is_unknown_not_equivalent():
    """未显著但很宽 → UNKNOWN。把它读成「无差异」正是 §4.4 点名要禁的动作。"""
    wide = st.Interval(0.0, -0.5, 0.5, 100, 20, "test")
    assert st.equivalence_verdict(wide, margin=0.05) == st.UNKNOWN


def test_tight_interval_inside_margin_is_equivalent():
    tight = st.Interval(0.001, -0.01, 0.012, 400, 40, "test")
    assert st.equivalence_verdict(tight, margin=0.05) == st.EQUIVALENT


def test_interval_beyond_margin_is_different():
    shifted = st.Interval(0.2, 0.12, 0.30, 400, 40, "test")
    assert st.equivalence_verdict(shifted, margin=0.05) == st.DIFFERENT


def test_interval_straddling_margin_edge_is_unknown():
    straddle = st.Interval(0.06, 0.04, 0.09, 100, 20, "test")
    assert st.equivalence_verdict(straddle, margin=0.05) == st.UNKNOWN


def test_missing_interval_is_unknown():
    assert st.equivalence_verdict(st.Interval(0.1, None, None, 3, 1, "x"), 0.05) == st.UNKNOWN


def test_asymmetric_margin_supported():
    iv = st.Interval(-0.02, -0.03, 0.001, 200, 20, "test")
    assert st.equivalence_verdict(iv, margin=0.05, margin_lo=-0.05) == st.EQUIVALENT
    assert st.equivalence_verdict(iv, margin=0.05, margin_lo=-0.01) == st.UNKNOWN


def test_no_harm_three_states():
    assert st.no_harm_verdict(st.Interval(0.0, -0.01, 0.03, 9, 9, "x"), 0.02) == st.EQUIVALENT
    assert st.no_harm_verdict(st.Interval(-0.2, -0.30, -0.10, 9, 9, "x"), 0.02) == st.DIFFERENT
    assert st.no_harm_verdict(st.Interval(-0.02, -0.30, 0.20, 9, 9, "x"), 0.02) == st.UNKNOWN


# ── FDR / 功效 ───────────────────────────────────────────────────────


def test_bh_fdr_preserves_input_order_and_is_monotone():
    out = st.bh_fdr([0.9, 0.001, 0.04, 0.5])
    assert [r["i"] for r in out] == [0, 1, 2, 3]
    assert out[1]["rejected"] and not out[0]["rejected"]
    assert out[1]["q"] <= out[2]["q"] <= out[3]["q"]


def test_bh_fdr_kills_the_lone_lucky_hit_in_a_wide_grid():
    """20 个格点里恰好一个 p=0.04:BH 之后不该算发现。"""
    out = st.bh_fdr([0.04] + [0.6] * 19)
    assert not out[0]["rejected"]


def test_bh_fdr_empty():
    assert st.bh_fdr([]) == []


def test_power_grows_with_n_and_min_n_round_trips():
    assert st.proportion_power(0.3, 0.5, 20) < st.proportion_power(0.3, 0.5, 400)
    n = st.min_n_for_proportion(0.3, 0.5, power=0.8)
    assert n is not None and st.proportion_power(0.3, 0.5, n) >= 0.8


def test_min_n_none_when_no_difference():
    assert st.min_n_for_proportion(0.4, 0.4) is None


# ── 成熟门 / expanding 分位 ──────────────────────────────────────────


def test_maturity_all_dimensions_pass():
    m = st.maturity_verdict(scan_days=25, subgroup_n=12, unique_n=40, regimes=3)
    assert m.status == st.MATURE and m.missing == ()


def test_maturity_reports_every_failing_dimension():
    m = st.maturity_verdict(scan_days=6, subgroup_n=2, unique_n=1, regimes=1)
    assert m.status == st.IMMATURE and len(m.missing) == 4


def test_maturity_none_dimension_is_skipped_not_zero():
    """不适用的维度传 None,不该被当 0 判死。"""
    m = st.maturity_verdict(scan_days=30, subgroup_n=None, unique_n=None, regimes=2)
    assert m.status == st.MATURE


def test_expanding_p25_uses_only_prior_history():
    values = [1.0] * 12 + [100.0]
    line = st.expanding_p25(values, min_history=10)
    assert line[:10] == [None] * 10                # 历史不足 → 不报警
    assert line[12] == pytest.approx(1.0)          # 末点的线只由它之前的点决定
    assert not any(v == 100.0 for v in line if v is not None)


def test_expanding_p25_short_series_all_none():
    assert st.expanding_p25([1.0, 2.0], min_history=10) == [None, None]


def test_no_scipy_import():
    """依赖纪律:判据代码不得建在 pyproject 未声明的 scipy 上(当前环境有它纯属传递依赖)。

    查的是 **import 语句**,不是文中提到的字样 —— 后者会把说明文字当违规。
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(st))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".")[0])
    assert "scipy" not in modules
    assert modules <= {"__future__", "math", "dataclasses", "numpy", "pandas"}
    assert math and np
