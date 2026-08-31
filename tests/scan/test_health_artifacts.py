#!/usr/bin/env python3
"""`health._ARTIFACTS` / `_CORE` 从产物登记表派生 + `verify.csv` 幽灵已除。

plan: `docs/superpowers/plans/2026-08-29-full-coverage-p0-p1.md` Task 9(b)
spec: `docs/specs/2026-08-29-full-coverage-research-system-brainstorm.md` §1.3(消费者无
      生产者)· §2.2 K3(五份「该有什么」登记表互不派生)· §4.3(verify.csv 二选一)

两件事:
1. **parity** —— 派生结果必须逐字等于改动前手写的那份清单(下面的字面量就是 2026-08-29
   改动前 `health.py:33-36` 的原文)。派生 ≠ 今天 = 回归,不是「顺便调一下期望」。
2. **除幽灵** —— `verify.csv` 全仓零写者(Tier-3 买单 skeptic 2026-07-06 移除),
   却天天被 `run_health` 记进 `missing` → 永久假警报。它必须不在期望清单里。
"""
from __future__ import annotations

import pytest

from autoresearch.contracts import artifacts as contract_artifacts
from autoresearch.scan import health

#: 2026-08-29 改动前 `health.py:33-36` 的原文,**逐字抄下来**当 parity 基线。
_HARDCODED_BEFORE = ["L1_scored_full.csv", "L1_recall_top1000.csv", "L2_gbdt_top200.csv",
                     "finalists.csv", "market_view.md", "verify.csv",
                     "gate_fires.csv", "weights_used.json", "L3_judged_full.csv"]
_HARDCODED_CORE_BEFORE = {"L1_scored_full.csv", "L1_recall_top1000.csv",
                          "L2_gbdt_top200.csv", "finalists.csv"}

#: 唯一**故意**的差异:无产者的 `verify.csv`(见模块 docstring 第 2 条)。
_INTENDED_DROP = "verify.csv"


def test_artifacts_parity_with_pre_derivation_hardcode():
    """派生清单 == 昨天的手写清单 − verify.csv,顺序也一样(它决定 run_health 的键序)。"""
    expected = [a for a in _HARDCODED_BEFORE if a != _INTENDED_DROP]
    assert list(health._ARTIFACTS) == expected, (
        "派生结果与改动前的手写值不一致 —— 这是回归,先查登记表的 path 写错了没,"
        "不要改这条期望")


def test_core_parity_with_pre_derivation_hardcode():
    assert set(health._CORE) == _HARDCODED_CORE_BEFORE
    assert set(health._CORE) <= set(health._ARTIFACTS), "core 必须是体检清单的子集"


def test_paths_come_from_the_contract_registry_not_string_literals():
    """每一项都必须能在登记表里按 path 找回一条 Artifact —— 这才叫「派生」。

    鉴别力:把 health 改回写死字符串后,只要登记表的哪个 path 变了,这条就红;
    而 parity 那两条永远绿(它们比的是同一份写死值)。
    """
    registered = {a.path: a for a in contract_artifacts.ARTIFACTS}
    for path in health._ARTIFACTS:
        assert path in registered, f"{path} 不在 contracts 产物登记表里(K3:第五份登记表)"


@pytest.mark.parametrize("path", [*_HARDCODED_BEFORE[:5], *_HARDCODED_BEFORE[6:]])
def test_every_health_artifact_is_staging_rooted_and_not_a_glob(path):
    """`run_health` 用 `(scan_dir / name).exists()` 判在场 —— 所以只能是 staging 根的实名文件。

    登记表里若把这几件之一改成 glob(`details/*.md`)或搬去 report 根,`exists()` 会
    **恒 False**,整趟体检把在场的产物报成缺席。这条守的是那个静默失真。
    """
    if path == _INTENDED_DROP:
        pytest.skip("verify.csv 已除名")
    spec = next(a for a in contract_artifacts.ARTIFACTS if a.path == path)
    assert spec.root == "staging", f"{path} 不在 staging 根,run_health 的 exists() 判不了它"
    assert "*" not in spec.path, f"{path} 是 glob 族,exists() 恒 False"


def test_unknown_registry_name_raises_loudly():
    """策展名写错 → `by_name` 抛 KeyError(drift 守卫要的正是这声响,不是静默丢一项)。"""
    with pytest.raises(KeyError):
        contract_artifacts.by_name("l1_ful")          # 手滑少一个 l


# ══════════════ verify.csv:消费者无生产者(spec §1.3 / §4.3)══════════════

def test_verify_csv_not_in_expected_artifacts():
    assert _INTENDED_DROP not in health._ARTIFACTS
    assert _INTENDED_DROP not in health._CORE


def test_run_health_does_not_report_verify_csv_missing(tmp_path):
    """真跑现场:staging 里什么都没有时,`missing` 里也不该再出现 verify.csv。"""
    d = tmp_path / "2026-08-29"
    d.mkdir()
    h = health.run_health(d)
    assert _INTENDED_DROP not in h["missing"], "永久假警报还在"
    assert _INTENDED_DROP not in h["artifacts"], "在位表里也不该给它留一格"


def test_run_health_still_reports_genuinely_missing_artifacts(tmp_path):
    """上一条的鉴别力保险:missing 没瘫 —— 真缺的还得报,且 core 照样点名。

    没有这条,「verify.csv 不在 missing 里」可以靠把 missing 恒置空来作弊过关。
    """
    d = tmp_path / "2026-08-29"
    d.mkdir()
    h = health.run_health(d)
    assert "finalists.csv" in h["missing"] and "L2_gbdt_top200.csv" in h["missing"]
    assert "finalists.csv" in h["core_missing"]
    assert set(h["artifacts"]) == set(health._ARTIFACTS)
