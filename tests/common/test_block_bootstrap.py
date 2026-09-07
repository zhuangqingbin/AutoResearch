"""F5:块 bootstrap 下沉 `common/stats.py` 的搬迁 parity + 单序列版本。

同仓两套 block bootstrap 就是工作包 A 刚治过的病(同一个估计量两处实现,读数悄悄分叉)。
这里只留一份抽块逻辑 `block_index`,`moving_block_diff`(事件日 − 非事件日)与
`block_mean_ci`(单序列均值区间)共用它。

golden 在**搬迁前**从 `overseas_event_census` 原实现录制
(`tests/fixtures/block_bootstrap_golden.json`),9 个用例覆盖:均衡 / 短样本 /
block=1 / block=n−1 / 全是事件日 / 全不是 / 稀疏(有作废抽样)/ 默认 n_boot / n ≤ block。
"""
import json
from pathlib import Path

import numpy as np
import pytest

from autoresearch.common import stats

GOLDEN = json.loads((Path(__file__).resolve().parents[1] / "fixtures"
                     / "block_bootstrap_golden.json").read_text(encoding="utf-8"))


# ───────────────────────── ① 同对象 + 常量不漂移 ─────────────────────────

def test_census_forwards_to_the_shared_implementation():
    from autoresearch.research import overseas_event_census as oc
    assert oc.moving_block_diff is stats.moving_block_diff
    assert oc.BootResult is stats.BootResult


def test_preregistered_constants_do_not_drift_from_the_shared_defaults():
    """census 的 §0 预注册常量与共享默认必须同值 —— 否则「同对象转发」会悄悄换掉口径。"""
    from autoresearch.research import overseas_event_census as oc
    assert oc.BLOCK == stats.MOVING_BLOCK == GOLDEN["defaults"]["BLOCK"]
    assert oc.N_BOOT == stats.MOVING_BLOCK_BOOT == GOLDEN["defaults"]["N_BOOT"]
    assert oc.SEED == stats.MOVING_BLOCK_SEED == GOLDEN["defaults"]["SEED"]


# ───────────────────────── ② golden 逐位 ─────────────────────────

@pytest.mark.parametrize("case", GOLDEN["cases"], ids=[c["name"] for c in GOLDEN["cases"]])
def test_moving_block_diff_matches_pre_migration_golden(case):
    got = stats.moving_block_diff(np.asarray(case["values"], dtype=float),
                                  np.asarray(case["flags"], dtype=bool),
                                  block=case["block"], n_boot=case["n_boot"],
                                  seed=case["seed"]).as_dict()
    assert got == case["result"], case["name"]


def test_default_arguments_still_reproduce_the_golden():
    """census 的唯一调用点只传 n_boot/seed,靠 block/alpha 走默认 —— 默认换了它就静默变了。"""
    case = next(c for c in GOLDEN["cases"] if c["name"] == "default_n_boot")
    assert case["block"] == GOLDEN["defaults"]["BLOCK"]
    got = stats.moving_block_diff(np.asarray(case["values"], dtype=float),
                                  np.asarray(case["flags"], dtype=bool),
                                  n_boot=case["n_boot"], seed=case["seed"]).as_dict()
    assert got == case["result"]


# ───────────────────────── ③ 共享抽块 ─────────────────────────

def test_block_index_covers_the_whole_series_and_stays_in_range():
    rng = np.random.default_rng(1)
    for n, block in ((41, 5), (7, 1), (12, 12), (300, 10)):
        idx = stats.block_index(rng, n, block)
        assert len(idx) == n
        assert idx.min() >= 0 and idx.max() < n


def test_block_index_blocks_are_contiguous_and_in_range():
    """块内下标连续且不越界。

    诚实说明:防绕回的**真正**守卫是起点上界 `n−block`(含端点)——有了它,再套一个 `% n`
    是无差异变异(实测过,不变红)。上界本身由 golden 逐位钉死(把 `+1` 去掉 → 8 条红)。
    """
    rng = np.random.default_rng(0)
    n, block = 20, 5
    for _ in range(200):
        idx = stats.block_index(rng, n, block)
        for start in range(0, len(idx) - block + 1, block):
            chunk = idx[start:start + block]
            if len(chunk) == block:
                assert list(chunk) == list(range(chunk[0], chunk[0] + block))


def test_scarce_valid_draws_report_no_interval_instead_of_a_confident_one():
    """有效抽样 < n_boot/2 → `p=None` + 无区间,由调用方判「未证」。

    这条是变异探针逼出来的:golden 九个用例里,稀疏族的 n_valid 是 996/1000,压根走不到这道
    守卫;把 `n_valid < n_boot // 2` 改成 `< 0`,九条 golden 全绿 —— 那道守卫当时是**没有灯
    的绿灯**。块长接近样本长度 + 事件日极稀疏,才逼得出大量「一侧为空」的作废抽样。
    """
    values = np.arange(40, dtype=float)
    flags = np.zeros(40, dtype=bool)
    flags[0] = True
    got = stats.moving_block_diff(values, flags, block=20, n_boot=400, seed=5)
    assert 0 < got.n_valid < 400 // 2
    assert got.p is None and got.lo is None and got.hi is None
    assert got.point is not None          # 点估计照给 —— 不可信的是区间,不是差值本身


def test_block_mean_ci_constant_series_and_reproducibility():
    result = stats.block_mean_ci([.01] * 40, block=5, seed=7)
    assert result["status"] == "COMPUTED"
    assert result["lo"] == pytest.approx(.01) and result["hi"] == pytest.approx(.01)
    assert result == stats.block_mean_ci([.01] * 40, block=5, seed=7)


def test_block_mean_ci_short_sample_has_no_interval():
    short = stats.block_mean_ci([.01] * 4, block=5, seed=7)
    assert short["status"] == "INSUFFICIENT_BLOCKS"
    assert short["lo"] is None and short["hi"] is None
    assert short["point"] == pytest.approx(.01)


def test_block_mean_ci_empty_series_has_no_point():
    assert stats.block_mean_ci([], block=5, seed=7)["point"] is None


@pytest.mark.parametrize("bad", [[.01, float("inf")], [.01, float("nan")]])
def test_block_mean_ci_refuses_nonfinite(bad):
    with pytest.raises(ValueError):
        stats.block_mean_ci(bad, block=1, seed=1)


@pytest.mark.parametrize("kwargs", [{"block": 0}, {"block": True}, {"block": 5, "n_boot": 0},
                                    {"block": 5, "alpha": 0.0}, {"block": 5, "alpha": 1.0}])
def test_block_mean_ci_refuses_invalid_parameters(kwargs):
    with pytest.raises(ValueError):
        stats.block_mean_ci([.01] * 40, seed=1, **kwargs)


def test_block_mean_ci_refuses_two_dimensional_input():
    with pytest.raises(ValueError):
        stats.block_mean_ci([[.01, .02], [.03, .04]], block=1, seed=1)


def test_block_mean_ci_shares_the_sampler_with_moving_block_diff():
    """两者用同一个抽块函数:同 seed 同长度下,单序列版的抽样均值可由 block_index 复算。"""
    values = np.arange(40, dtype=float)
    expected = []
    rng = np.random.default_rng(99)
    for _ in range(50):
        expected.append(values[stats.block_index(rng, 40, 5)].mean())
    got = stats.block_mean_ci(values, block=5, seed=99, n_boot=50)
    assert got["point"] == pytest.approx(values.mean())
    lo, hi = np.quantile(np.asarray(expected), [0.025, 0.975])
    assert got["lo"] == pytest.approx(float(lo)) and got["hi"] == pytest.approx(float(hi))


def test_block_mean_pvalue_is_deterministic_and_two_sided():
    values = [1.0, 1.1, .9, 1.2, .8]

    result = stats.block_mean_test(values, block=2, seed=7, n_boot=999)

    assert result.pvalue == pytest.approx(
        stats.block_mean_test(values, block=2, seed=7, n_boot=999).pvalue
    )
    assert result.pvalue == pytest.approx(
        stats.block_mean_test([-v for v in values], block=2, seed=7, n_boot=999).pvalue
    )
    assert 0 < result.pvalue <= 1
    assert result.status == "COMPUTED"


def test_block_mean_test_uses_circular_blocks():
    rng = np.random.default_rng(3)
    saw_wrap = False
    for _ in range(30):
        index = stats.circular_block_index(rng, n=7, block=3)
        for start in range(0, len(index) - 2, 3):
            chunk = index[start:start + 3]
            assert list(chunk) == [chunk[0], (chunk[0] + 1) % 7, (chunk[0] + 2) % 7]
            saw_wrap |= chunk[0] >= 5
    assert saw_wrap


def test_block_mean_test_reports_insufficient_observations():
    result = stats.block_mean_test([1.0], block=1, seed=7, n_boot=99)

    assert result.status == "INSUFFICIENT_OBSERVATIONS"
    assert result.pvalue is None
