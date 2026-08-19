"""O3 半特性接线探针 —— design 2026-08-03 §3.3。

`stratified_l2/select_l2` 的 `regime`/`regime_caps` **已建未接线**:函数体真的按 regime
调 sector cap,但 `scan/universe.py` 的全部生产调用点都不传它 → 生产路径上恒为 None。

这个探针守的不是「参数存在」,而是**「有人开始喂它却没走治理」**:
接了线 = L2 构成会变 = B 类行为变更 = 必须有显式治理留痕。两个条件不同时满足 → 变红。

D1(2026-08-19,用户裁决 A3):原判据「registry 里 ACTIVE 的 `l2_regime_caps` 实验」
随 experiment_registry 家族整删而失效(registry 不复存在)。证据源换成本项目现行的
「配置单一事实源」纪律(2026-08-11 裁定):`scan_config.jsonc` 是全流程参数事实源,
新键必须显式提交(不是悄悄改默认值)。新判据 = `funnel.regime_aware` 键在场
(config 侧确有一处显式登记的 regime 相关治理键)**且** `scan_config.jsonc` 本身有
真实 git 提交痕(不是未纳入版本控制的本地改动)——两条都满足才算「有治理留痕」;
`regime_caps` 一旦真被接线却仍缺这层留痕,探针必须变红,道理与retired前完全一致。

为什么用 AST 而不是 grep:`regime_caps=` 出现在注释、docstring 或字符串里都不算接线,
只有**真实关键字实参**才算。grep 会把这份文档自己的引用也数进去。
"""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

from autoresearch.scan.recall.l2_stratify import select_l2, stratified_l2
from autoresearch.scan.user_config import DEFAULT_PATH as SCAN_CONFIG_PATH, _read_jsonc

PRODUCER = Path("autoresearch/scan/universe.py")
L2_CALLEES = {"select_l2", "stratified_l2"}
REGIME_KWARGS = {"regime", "regime_caps"}


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


def _regime_config_has_governance_trace() -> bool:
    """新证据源(D1,2026-08-19 用户裁决 A3):config 单一事实源里 `funnel.regime_aware`
    键在场,且该文件本身有真实 git 提交痕 —— 两条都满足才算「有治理留痕」。

    不可读 / 无提交历史一律按**没有留痕**处理(不臆断放行,镜像被替换掉的
    `_has_active_experiment` 遇 registry 不可读时返回 False 的保守精神)。
    """
    try:
        cfg = _read_jsonc(SCAN_CONFIG_PATH)
    except (OSError, ValueError):
        return False
    if "regime_aware" not in (cfg.get("funnel") or {}):
        return False
    try:
        out = subprocess.run(
            ["git", "log", "--oneline", "-1", "--", str(SCAN_CONFIG_PATH)],
            capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(out.stdout.strip())


def test_regime_caps_is_not_wired_into_production_without_governance():
    """接线了但没有治理留痕 → 红。没接线 → 绿(当前状态)。"""
    if not PRODUCER.exists():
        pytest.skip(f"{PRODUCER} 不在(非仓库根目录运行)")
    wired = _wired_call_lines(PRODUCER.read_text(encoding="utf-8"))
    if wired and not _regime_config_has_governance_trace():
        pytest.fail(
            f"{PRODUCER} 第 {wired} 行开始给 L2 传 regime/regime_caps,但 "
            f"{SCAN_CONFIG_PATH} 缺 `funnel.regime_aware` 键或缺显式 git 提交痕 —— "
            "这会改变 L2 名单构成(B 类行为变更),必须先补 variant contract 并在"
            "config 单一事实源里显式登记(候选 O3_regime_caps)")


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
