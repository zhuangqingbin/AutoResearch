"""F6:检验族的多重比较校正 —— BH(默认)与 BY(任意依赖下更保守)。

两条不许含糊的边界:

1. **依赖假设必须显式声明**。BH 只在独立或正相关(PRDS)下控 FDR;因子之间通常既非独立也
   不保证正相关,那时得用 BY(乘 Σ1/i)。让调用方在 `dependence` 里说清楚,不给默认——
   「一律称任意相关下受控」是错的,不写又会被当成写了。
2. **原始顺序不变**。校正结果按入参顺序返回;重排会让「第 3 个因子」在报告里对错人。

BH 本体不重写,直接用 `common.stats.bh_fdr`(它已被海外/衍生品普查用着)。
"""
import math

import pytest

from autoresearch.common import stats
from autoresearch.common.stats import bh_fdr, family_adjustment

PS = [0.001, 0.008, 0.03, 0.2, 0.9]


def test_bh_branch_reproduces_the_existing_primitive():
    """`independent_or_positive` 分支必须与既有 BH 逐字段相同 —— 否则就是又造了一份。"""
    got = family_adjustment(PS, dependence="independent_or_positive")
    base = bh_fdr(PS)
    assert [row["q"] for row in got] == [row["q"] for row in base]
    assert [row["rejected"] for row in got] == [row["rejected"] for row in base]
    assert all(row["method"] == "BH" for row in got)


def test_by_is_never_less_conservative_than_bh():
    by = family_adjustment(PS, dependence="arbitrary")
    bh = family_adjustment(PS, dependence="independent_or_positive")
    assert all(b["q"] >= h["q"] for b, h in zip(by, bh, strict=True))
    assert all(row["method"] == "BY" for row in by)


def test_by_multiplier_is_the_harmonic_sum():
    """BY 的乘数是 Σ_{i=1..m} 1/i;写错成 ln(m) 之类会让它悄悄变松。"""
    by = family_adjustment(PS, dependence="arbitrary")
    bh = family_adjustment(PS, dependence="independent_or_positive")
    factor = sum(1 / i for i in range(1, len(PS) + 1))
    for b, h in zip(by, bh, strict=True):
        assert b["q"] == pytest.approx(min(1.0, h["q"] * factor))


def test_q_is_capped_at_one():
    by = family_adjustment([0.9, 0.95, 0.99], dependence="arbitrary")
    assert all(row["q"] <= 1.0 for row in by)


def test_rejection_follows_the_adjusted_q_not_the_raw_p():
    """乘完 BY 因子还按旧 q 判 rejected,就等于报了 BY 的 q、用了 BH 的门。"""
    rows = family_adjustment([0.02, 0.9], dependence="arbitrary", alpha=0.05)
    assert all(row["rejected"] == (row["q"] <= 0.05) for row in rows)
    assert not rows[0]["rejected"]          # 0.02 在 BY 下过不了 0.05


def test_input_order_is_preserved():
    rows = family_adjustment([0.9, 0.001, 0.03], dependence="independent_or_positive")
    assert [row["i"] for row in rows] == [0, 1, 2]
    assert [row["p"] for row in rows] == [0.9, 0.001, 0.03]


def test_empty_family_is_empty_not_an_error():
    assert family_adjustment([], dependence="arbitrary") == []


def test_dependence_assumption_is_mandatory():
    with pytest.raises(TypeError):
        family_adjustment(PS)
    with pytest.raises(ValueError):
        family_adjustment(PS, dependence="whatever")


@pytest.mark.parametrize("bad", [[float("nan")], [float("inf")], [-0.01], [1.01]])
def test_invalid_pvalues_are_rejected(bad):
    with pytest.raises(ValueError):
        family_adjustment(bad, dependence="arbitrary")


@pytest.mark.parametrize("alpha", [0.0, 1.0, -0.1, 1.5])
def test_invalid_alpha_is_rejected(alpha):
    with pytest.raises(ValueError):
        family_adjustment(PS, dependence="arbitrary", alpha=alpha)


def test_single_hypothesis_family_leaves_bh_alone_and_by_equals_bh():
    """m=1 时 Σ1/i = 1,两分支必须给同一个 q —— 不然「只有一个假设」也会被平白罚一次。"""
    bh = family_adjustment([0.04], dependence="independent_or_positive")
    by = family_adjustment([0.04], dependence="arbitrary")
    assert bh[0]["q"] == by[0]["q"] == pytest.approx(0.04)
    assert math.isclose(sum(1 / i for i in range(1, 2)), 1.0)


def test_dependence_vocabulary_is_closed_and_exported():
    assert set(stats.DEPENDENCE_ASSUMPTIONS) == {"independent_or_positive", "arbitrary"}
