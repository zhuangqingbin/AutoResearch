"""10 日尺普查的判读检验 —— 尺寸校准(2026-09-26 批 5 复审 I1)。

复审的蒙特卡洛:旧登记在判读样本量(40 天、块长 10 → 每次重采样 4 个块)上,块 bootstrap
的名义 5% 检验真实假阳 27–29%,整条判读规则单格约 20%、三格至少一格约 47%。本文件锁住
替代检验在**同一零假设、同一样本量**上的尺寸:

① MA(9) 零假设(相邻分析日 10 日窗口重叠 9 个 session)、n = 40:逐格假判率 ≤ 7%;
② 更轻的依赖 / 厚尾冲击 / 扫描日缺口:只会更保守(≤ 7%);
③ 整条登记规则(三格一族 BY + 同向拒绝):族内任一格假判率 ≤ 6%;
④ 测试有牙:把临界值换成正态 1.96,①必红;
⑤ 区间与 p 值同源:区间不含 0 ⇔ p ≤ α,逐条相等。

固定种子;全部向量化,整文件 < 5 s。
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from autoresearch.common.stats import family_adjustment
from autoresearch.research import swing_ruler_decision as dec

#: 登记的判读样本量(spec `maturity_policy: scan_days >= 40`;登记与此相等由
#: `tests/research/test_family_registry.py` 锁)。
DECISION_N = 40
NULL_REPS_TEST = 4000
TEST_SEED = 7_20260926          # 与登记的 NULL_SEED 不同:检验用的零假设样本与校准样本独立


def _reject_rate(x: np.ndarray, *, crit: float) -> float:
    mean, se = dec.hac_t_rows(x, lag=dec.HAC_LAG)
    return float((dec._abs_t(mean, se) > crit).mean())


def _mixture(rng, reps, n, *, share, heavy=False):
    """share·MA(9) + (1−share)·逐日独立;heavy = t(4) 冲击(单位方差)。"""
    def shocks(shape):
        return rng.standard_t(4, size=shape) / math.sqrt(2.0) if heavy else rng.standard_normal(shape)
    e = shocks((reps, n + dec.HAC_LAG))
    c = np.concatenate([np.zeros((reps, 1)), np.cumsum(e, axis=1)], axis=1)
    ma = (c[:, dec.HAC_LAG + 1:] - c[:, :-(dec.HAC_LAG + 1)]) / math.sqrt(dec.HAC_LAG + 1)
    return math.sqrt(share) * ma + math.sqrt(1.0 - share) * shocks((reps, n))


@pytest.mark.parametrize("n", [DECISION_N, 60, 100])
def test_size_under_the_overlap_null_at_the_decision_sample_size(n):
    """① MA(9) 零假设:逐格假判率 ≤ 7%(名义 5%;旧块 bootstrap 在 n=40 上是 27–29%)。"""
    rng = np.random.default_rng(TEST_SEED + n)
    x = dec.overlap_null_series(rng, NULL_REPS_TEST, n)
    assert _reject_rate(x, crit=dec.critical_value(n)) <= 0.07


@pytest.mark.parametrize("share, heavy", [(0.6, False), (0.6, True), (1.0, True), (0.0, False)])
def test_lighter_dependence_or_heavy_tails_only_make_it_more_conservative(share, heavy):
    """② 依赖更轻(个股噪声占比更高 / 逐日独立)或 t(4) 厚尾冲击:仍 ≤ 7%。"""
    rng = np.random.default_rng(TEST_SEED + int(share * 10) + (100 if heavy else 0))
    x = _mixture(rng, NULL_REPS_TEST, DECISION_N, share=share, heavy=heavy)
    assert _reject_rate(x, crit=dec.critical_value(DECISION_N)) <= 0.07


def test_scan_day_gaps_only_make_it_more_conservative():
    """② 扫描日有缺口(约 1/5 的交易日没扫)→ 观察步跨多个 session,重叠更少 → 更保守。"""
    rng = np.random.default_rng(TEST_SEED + 555)
    full = dec.overlap_null_series(rng, 2000, 60)
    keep = np.sort(rng.choice(60, size=DECISION_N, replace=False))
    assert _reject_rate(full[:, keep], crit=dec.critical_value(DECISION_N)) <= 0.07


def test_the_whole_registered_rule_keeps_the_family_false_verdict_rate_low():
    """③ 三格同时为零假设,按登记规则(校准检验拒绝 ∧ BY(arbitrary) q ≤ 0.05)判读:
    族内任一格判成 POSITIVE/NEGATIVE 的频率 ≤ 6%(本种子实测 4.3%;旧规则:单格 ~20%,
    任一格 ~47%)。"""
    rng = np.random.default_rng(TEST_SEED + 3)
    reps = 1500
    cells = [dec.overlap_null_series(rng, reps, DECISION_N) for _ in range(3)]
    any_false = 0
    for i in range(reps):
        tests = [dec.overlap_test(c[i]) for c in cells]
        adjusted = family_adjustment([t["p"] for t in tests], dependence="arbitrary", alpha=0.05)
        verdicts = [dec.decide(n_days=DECISION_N, min_days=DECISION_N, test=t, q=a["q"],
                               fdr_alpha=0.05, registered=True)
                    for t, a in zip(tests, adjusted, strict=True)]
        any_false += any(v in (dec.POSITIVE, dec.NEGATIVE) for v in verdicts)
    assert any_false / reps <= 0.06


def test_the_size_test_has_teeth_a_normal_critical_value_would_fail():
    """④ 同一零假设、同一 n,把临界值换成正态 1.96:假判率远超 7% —— ①不是摆设。"""
    rng = np.random.default_rng(TEST_SEED + DECISION_N)
    x = dec.overlap_null_series(rng, NULL_REPS_TEST, DECISION_N)
    assert _reject_rate(x, crit=1.959964) > 0.2


def test_interval_and_p_value_come_from_the_same_method():
    """⑤ 旧基线:H1 区间整段在 0 以下,同一格 p = 0.051(两套方法)。现在逐条相等。"""
    rng = np.random.default_rng(TEST_SEED + 9)
    x = dec.overlap_null_series(rng, 400, 50) + np.linspace(-1.5, 1.5, 400)[:, None]
    seen = set()
    for row in x:
        t = dec.overlap_test(row)
        excludes = t["lo"] > 0 or t["hi"] < 0
        assert excludes == (t["p"] <= 0.05) == t["reject"]
        seen.add(excludes)
    assert seen == {True, False}                      # 两种结果都真的出现过


def test_same_input_same_output_and_the_critical_value_shrinks_with_n():
    rng = np.random.default_rng(1)
    x = rng.standard_normal(45)
    assert dec.overlap_test(x) == dec.overlap_test(x.copy())
    assert dec.critical_value(40) > dec.critical_value(60) > dec.critical_value(100) > 1.96


def test_too_few_observations_are_not_tested():
    t = dec.overlap_test(np.ones(2 * (dec.HAC_LAG + 1) - 1))
    assert t["status"] == "INSUFFICIENT_OBSERVATIONS"
    assert t["p"] is None and t["lo"] is None and t["reject"] is False
    assert dec.decide(n_days=45, min_days=40, test=t, q=None, fdr_alpha=0.05,
                      registered=True) == dec.UNPROVEN


def test_a_constant_nonzero_series_rejects_and_a_constant_zero_series_does_not():
    up = dec.overlap_test(np.full(45, 1.0))
    assert up["reject"] and up["p"] == pytest.approx(1 / (dec.NULL_REPS + 1))
    assert up["lo"] == up["hi"] == 1.0
    flat = dec.overlap_test(np.zeros(45))
    assert not flat["reject"] and flat["p"] == 1.0


def test_decide_follows_the_registered_order():
    ok = {"status": "COMPUTED", "reject": True, "point": -0.5}
    assert dec.decide(n_days=45, min_days=40, test=ok, q=0.01, fdr_alpha=0.05,
                      registered=False) == dec.EXPLORATORY
    assert dec.decide(n_days=39, min_days=40, test=ok, q=0.01, fdr_alpha=0.05,
                      registered=True) == dec.INSUFFICIENT
    assert dec.decide(n_days=45, min_days=40, test=ok, q=0.2, fdr_alpha=0.05,
                      registered=True) == dec.UNPROVEN
    assert dec.decide(n_days=45, min_days=40, test=ok, q=0.01, fdr_alpha=0.05,
                      registered=True) == dec.NEGATIVE
    assert dec.decide(n_days=45, min_days=40, test={**ok, "point": 0.5}, q=0.01,
                      fdr_alpha=0.05, registered=True) == dec.POSITIVE
    assert dec.decide(n_days=45, min_days=40, test={**ok, "reject": False}, q=0.01,
                      fdr_alpha=0.05, registered=True) == dec.UNPROVEN
