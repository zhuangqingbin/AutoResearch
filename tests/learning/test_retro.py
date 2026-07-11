import numpy as np
import pandas as pd

from autoresearch.learning import retro


def test_selftest():
    assert retro._selftest() == 0


def test_winner_follows_fwd2_not_fwd1():
    """主归因主尺=fwd_2_oc:T+1 大涨但 T+2 回吐的票不是赢家;反之才是。"""
    n = 40
    realized = pd.DataFrame({
        "code": [f"{i:06d}" for i in range(n)],
        "fwd_1_oo": [0.08] + [0.0] * (n - 1),            # 000000 只赢在 T+1
        "fwd_2_oc": [0.0] + [0.06] + [0.001] * (n - 2),  # 000001 赢在 T+2(主尺)
        "fwd_5_oc": [np.nan] * n,
        "buyable": [True] * n,
    })
    l1 = pd.DataFrame({"code": realized["code"], "composite": 0.5, "recalled": False})
    attr = retro.attribute_frame(l1, realized, buylist={})
    w = attr[attr["winner"]]
    assert set(w["code"]) == {"000001"}


# ───────────────────────── L3.5 闸影子(l35_gate_shadow,plan A2 Task 3) ─────────────────────────
#
# design: docs/specs/2026-07-11-recall-gate-pinned-config-design.md §3
# plan:   docs/plans/2026-07-11-l35-gate-backtest-plan.md Task 3
# 姿势抄 shadow_compare(presence-gated,读 scan_dir 里的 staging 文件 × 已算好的 attr 帧)。


def test_l35_gate_shadow_absent_cut_file_returns_none(tmp_path):
    """presence-gated 核心契约:无 _l35_cut.csv(passthrough 默认/该闸当日全放行)→ None,
    不进 retro_input,与 assemble 的「无 cut 文件不加节」同一判据。"""
    attr = pd.DataFrame({"code": ["000001"], "fwd_2_oc": [0.05]})
    assert retro.l35_gate_shadow(attr, tmp_path) is None


def test_l35_gate_shadow_computes_cut_vs_picked_means(tmp_path):
    """cut 集(_l35_cut.csv)vs picked 集(finalists.csv,闸后名单)的 fwd_2_oc 均值对照。"""
    attr = pd.DataFrame({
        "code": ["000001", "000002", "000003", "000004"],
        "fwd_2_oc": [0.10, -0.02, 0.03, 0.01],
    })
    pd.DataFrame({"code": ["000002", "000003"], "rank": [2, 3], "conviction": [10, 5],
                 "lane": ["trend", "trend"],
                 "reason": ["conviction<floor(50)"] * 2}).to_csv(tmp_path / "_l35_cut.csv", index=False)
    pd.DataFrame({"code": ["000001", "000004"]}).to_csv(tmp_path / "finalists.csv", index=False)
    gs = retro.l35_gate_shadow(attr, tmp_path)
    assert gs["n_cut"] == 2 and gs["n_cut_realized"] == 2
    assert gs["cut_mean_fwd2"] == round((-0.02 + 0.03) / 2, 5)
    assert gs["n_picked_realized"] == 2
    assert gs["picked_mean_fwd2"] == round((0.10 + 0.01) / 2, 5)


def test_l35_gate_shadow_missing_finalists_reports_none_picked_mean():
    """无 finalists.csv(理论上不该发生,但纵深防御)→ picked 侧 None/0,不臆造数字;cut 侧仍算。"""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        sdir = Path(td)
        attr = pd.DataFrame({"code": ["000002"], "fwd_2_oc": [-0.02]})
        pd.DataFrame({"code": ["000002"], "rank": [2], "conviction": [10], "lane": ["trend"],
                     "reason": ["conviction<floor(50)"]}).to_csv(sdir / "_l35_cut.csv", index=False)
        gs = retro.l35_gate_shadow(attr, sdir)
        assert gs["n_cut"] == 1 and gs["cut_mean_fwd2"] == -0.02
        assert gs["picked_mean_fwd2"] is None and gs["n_picked_realized"] == 0


def test_l35_gate_shadow_unrealized_fwd_reports_none_not_zero(tmp_path):
    """fwd_2_oc 未成熟(缺列)→ mean=None(不是 0),n_realized=0(如实报告样本不足)。"""
    attr = pd.DataFrame({"code": ["000001", "000002"]})   # 无 fwd_2_oc 列
    pd.DataFrame({"code": ["000002"], "rank": [2], "conviction": [10], "lane": ["trend"],
                 "reason": ["x"]}).to_csv(tmp_path / "_l35_cut.csv", index=False)
    gs = retro.l35_gate_shadow(attr, tmp_path)
    assert gs["cut_mean_fwd2"] is None and gs["n_cut_realized"] == 0


def test_write_retro_input_includes_l35_shadow_section_when_cut_present(tmp_path):
    """presence-gated 端到端:_l35_cut.csv 在场 → retro_input.md 含「L3.5 闸影子」节。"""
    sdir = tmp_path / "context" / "scan" / "2026-07-11"
    sdir.mkdir(parents=True)
    pd.DataFrame({"code": ["000002"], "rank": [2], "conviction": [10], "lane": ["trend"],
                 "reason": ["conviction<floor(50)"]}).to_csv(sdir / "_l35_cut.csv", index=False)
    pd.DataFrame({"code": ["000001"]}).to_csv(sdir / "finalists.csv", index=False)
    n = 20
    attr = pd.DataFrame({
        "code": [f"{i:06d}" for i in range(n)],
        "fwd_1_oo": [0.0] * n, "fwd_2_oc": [0.0] * n, "fwd_5_oc": [np.nan] * n,
        "buyable": [True] * n, "winner": [False] * n, "bucket": [""] * n,
        "recalled_flag": [False] * n, "in_l1": [True] * n, "bought": [False] * n,
        "tradable": [True] * n,
    })
    p = retro.write_retro_input("2026-07-11", attr, scan_root=tmp_path / "context" / "scan")
    assert "## L3.5 闸影子" in p.read_text(encoding="utf-8")


def test_write_retro_input_omits_l35_shadow_section_when_cut_absent(tmp_path):
    """presence-gated 反向:无 _l35_cut.csv → retro_input.md 不含该节(老路不破)。"""
    sdir = tmp_path / "context" / "scan" / "2026-07-11"
    sdir.mkdir(parents=True)
    n = 20
    attr = pd.DataFrame({
        "code": [f"{i:06d}" for i in range(n)],
        "fwd_1_oo": [0.0] * n, "fwd_2_oc": [0.0] * n, "fwd_5_oc": [np.nan] * n,
        "buyable": [True] * n, "winner": [False] * n, "bucket": [""] * n,
        "recalled_flag": [False] * n, "in_l1": [True] * n, "bought": [False] * n,
        "tradable": [True] * n,
    })
    p = retro.write_retro_input("2026-07-11", attr, scan_root=tmp_path / "context" / "scan")
    assert "L3.5 闸影子" not in p.read_text(encoding="utf-8")
