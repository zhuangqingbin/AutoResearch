"""assemble 的 L2 菜单体检嵌入(presence-gated)+ 观察单日检退役回归。

观察单日检节已退役(fb_20260714_002,2026-07-14 用户裁定):即便 watchlist_status.csv 在也不渲染。
菜单体检不受影响。独立于 tests/scan/test_assemble.py,fixture 模式同 test_market_view_embed。

2026-08-28 summary 精简重构(design 2026-08-28-summary-slimdown-design.md §5 行 19):🍱 菜单体检
下沉 `appendix.md` B 节,断言跟着搬。退役类断言(观察单)则扩到**两份文件**——退役的东西
不该在发布包的任何一页里冒出来,只查 summary 会让「搬去附录」冒充「已退役」。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.scan.assemble import build_summary
from autoresearch.scan.report_appendix import render_appendix
from autoresearch.scan.report_model import ABSENT
from autoresearch.scan.report_sections import (
    attach_review,
    prepare_report_model,
    render_summary,
)

_D = "2026-07-02"
_F = "20260702_1200"


def _render(d):
    """一次整形 → 两处纯渲染,返回 (summary, appendix)。顺序照 publisher 冻结时序。"""
    model = prepare_report_model(d, _D, "1200", _F)
    summary = render_summary(model)
    return summary, render_appendix(attach_review(model, summary))


def _min_scan(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    (d / "meta.json").write_text("{}", encoding="utf-8")
    (d / "finalists.csv").write_text("code,name,sector\n", encoding="utf-8")
    return d


def test_watchlist_block_retired_even_when_status_present(tmp_path):
    """退役回归(fb_20260714_002):watchlist_status.csv 就算在盘上,观察单节也不得再渲染。

    发布包两份都查:退役 ≠ 挪到附录。
    """
    d = _min_scan(tmp_path)
    pd.DataFrame([{"code": "300476", "name": "胜宏科技", "status": "临近",
                   "detail": "ma_bull=no;close_above:314=yes", "narrative": "中报beat",
                   "born": "2026-06-30", "expiry": "2026-08-14"}]).to_csv(
        d / "watchlist_status.csv", index=False)
    md, ap = _render(d)
    assert "观察单日检" not in md and "胜宏科技" not in md
    assert "观察单日检" not in ap and "胜宏科技" not in ap


def test_no_watchlist_block_when_absent(tmp_path):
    md, ap = _render(_min_scan(tmp_path))
    assert "观察单日检" not in md
    assert "观察单日检" not in ap


def test_menu_block_when_l2_present(tmp_path):
    """🍱 L2 菜单体检 → appendix B(§5 行 19);决策层不再展开漏斗现场。"""
    d = _min_scan(tmp_path)
    rows = [{"code": "000001", "industry": "半导体", "pct_60d": -30.0,
             "main_net_ratio": -0.01, "cmf_20": -0.02, "pe": 30.0}]
    pd.DataFrame(rows).to_csv(d / "L2_gbdt_top200.csv", index=False)
    pd.DataFrame(rows * 3).to_csv(d / "L1_scored_full.csv", index=False)
    md, ap = _render(d)
    assert "🍱 L2 菜单体检" in ap
    assert "行业集中度 top3" in ap, "菜单体检正文要跟着标题一起搬,不是只搬个标题"
    assert "菜单体检" not in md, "菜单体检属漏斗现场,不进决策层"


def test_no_menu_block_when_absent(tmp_path):
    """无 L2 staging → summary 一个字都不印;appendix B 的**标题恒在**、内容印 ABSENT。

    §4.2:条件素材缺席时明确印 `无 / NOT_EXPECTED`,不靠删整节表达 absence ——
    否则「appendix 缺任一节」检查会把合法缺席误报成丢件。
    """
    md, ap = _render(_min_scan(tmp_path))
    assert "菜单体检" not in md
    assert f"**L2 菜单体检**:{ABSENT}" in ap


def test_gate_fires_staging_written_by_assemble(tmp_path):
    """assemble 每次跑完幂等落 gate_fires.csv(即使 0 failures,写表头区分没拦/没跑)。

    走 `build_summary` 兼容壳:那条路里 `finalize_review_artifacts` 的落盘副作用是被测
    行为本身(prepare/render 拆开后它必须被显式调用,漏掉 = GATE4 没有输入)。
    """
    d = _min_scan(tmp_path)
    build_summary(d, _D, "1200", _F)
    assert (d / "gate_fires.csv").exists()
