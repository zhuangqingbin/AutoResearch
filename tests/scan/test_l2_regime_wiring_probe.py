"""O3 半特性接线探针 —— design 2026-08-03 §3.3。

`stratified_l2/select_l2` 的 `regime`/`regime_caps` **已建未接线**:函数体真的按 regime
调 sector cap,但 `scan/universe.py` 的全部生产调用点都不传它 → 生产路径上恒为 None。

这个探针守的不是「参数存在」,而是**「有人开始喂它却没走治理」**:
接了线 = L2 构成会变 = B 类行为变更 = 必须先有 registry 里 ACTIVE 的 `l2_regime_caps`
实验。两个条件不同时满足 → 变红。

为什么用 AST 而不是 grep:`regime_caps=` 出现在注释、docstring 或字符串里都不算接线,
只有**真实关键字实参**才算。grep 会把这份文档自己的引用也数进去。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from autoresearch.scan.recall.l2_stratify import select_l2, stratified_l2

PRODUCER = Path("autoresearch/scan/universe.py")
L2_CALLEES = {"select_l2", "stratified_l2"}
REGIME_KWARGS = {"regime", "regime_caps"}
REGISTRY_FAMILY = "l2_regime_caps"


def _wired_call_lines(source: str) -> list[int]:
    """生产源码里**真的**给 L2 传了 regime/regime_caps 的调用行号。"""
    out = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name not in L2_CALLEES:
            continue
        if any(kw.arg in REGIME_KWARGS for kw in node.keywords if kw.arg):
            out.append(node.lineno)
    return out


def _has_active_experiment() -> bool:
    from autoresearch.learning.experiment_registry import (
        DEFAULT_REGISTRY,
        RegistryError,
        load_registry,
    )

    try:
        payload = load_registry(DEFAULT_REGISTRY)
    except RegistryError:
        return False
    return payload.get("active_by_family", {}).get(REGISTRY_FAMILY) is not None


def test_regime_caps_is_not_wired_into_production_without_governance():
    """接线了但没走 registry → 红。没接线 → 绿(当前状态)。"""
    if not PRODUCER.exists():
        pytest.skip(f"{PRODUCER} 不在(非仓库根目录运行)")
    wired = _wired_call_lines(PRODUCER.read_text(encoding="utf-8"))
    if wired and not _has_active_experiment():
        pytest.fail(
            f"{PRODUCER} 第 {wired} 行开始给 L2 传 regime/regime_caps,但 registry 里"
            f" `{REGISTRY_FAMILY}` 没有 ACTIVE 实验 —— 这会改变 L2 名单构成(B 类行为变更),"
            "必须先补 variant contract 并走 registry/replay(候选 O3_regime_caps)")


def test_probe_would_go_red_on_a_wired_call():
    """探针本身有鉴别力 —— 喂它一段「已接线」的源码,必须能认出来。

    (「绿灯不等于有灯」的判例:改完先问『把这段删掉测试会红吗』。)
    """
    wired_src = "select_l2(recall, l2_n, floors=f, regime_caps={'trend': 0.9})\n"
    assert _wired_call_lines(wired_src) == [1]


def test_probe_ignores_mentions_in_comments_and_strings():
    noise = (
        '"""这里说 regime_caps 只是文档"""\n'
        "# select_l2(recall, l2_n, regime_caps={'trend': 0.9})\n"
        "msg = \"select_l2(regime_caps=...)\"\n"
        "select_l2(recall, l2_n, floors=f, sector_cap_frac=0.2)\n"
    )
    assert _wired_call_lines(noise) == []


def test_the_parameter_still_works_when_explicitly_passed():
    """半特性不是坏特性 —— 显式传参时它必须真的生效,否则「补文档」补的是个谎。

    判据用 `selection_reason` 而不是行业集中度:sector-neutral 打分让两个等大行业天然
    对称,松紧 cap 后集中度可能一模一样(既有 `test_regime_cap_loosen_allows_more_
    concentration` 用 `>=` 正是因为这个)。而 `sector` 这个理由只在**cap 被卡死、
    不得不松开**时才产生 —— 它直接证明算法走了不同的分支。
    """
    import pandas as pd

    rows = [{"code": f"{i:06d}", "composite": float(100 - i),
             "industry": "半导体" if i % 2 == 0 else "白酒",
             "recall_channels": "composite", "pct_60d": float(i)} for i in range(100)]
    frame = pd.DataFrame(rows)
    tight = stratified_l2(frame, l2_n=20, floors={}, sector_cap_frac=0.20)
    loose = stratified_l2(frame, l2_n=20, floors={}, sector_cap_frac=0.20,
                          regime="trend", regime_caps={"trend": 0.9})
    assert (tight["selection_reason"] == "sector").any()      # cap=4 → 卡死后松 cap
    assert not (loose["selection_reason"] == "sector").any()  # cap=18 → 从不卡死


def test_select_l2_default_is_regime_blind():
    """不传 = 与传 None 逐值一致(生产当前走的正是这条路)。"""
    import pandas as pd

    rows = [{"code": f"{i:06d}", "composite": float(100 - i),
             "industry": "半导体" if i % 2 == 0 else "白酒",
             "recall_channels": "composite", "pct_60d": float(i)} for i in range(100)]
    frame = pd.DataFrame(rows)
    a, _ = select_l2(frame, 20)
    b, _ = select_l2(frame, 20, regime=None, regime_caps={"trend": 0.9})
    assert list(a["code"]) == list(b["code"])
