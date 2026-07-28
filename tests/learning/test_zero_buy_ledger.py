"""0买日市场对照 ledger:roll 字段/render 对照/空目录优雅。合成 fixture。

spec: docs/specs/2026-07-02-scan-watchlist-and-health-metrics-design.md §2.3
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from autoresearch.learning.zero_buy_ledger import bought_mask, render, roll


def _mk_day(root, date, bought_flags, fwd1, fwd5, fwd2=None):
    d = root / date / "retro"
    d.mkdir(parents=True)
    data = {"code": [f"{i:06d}" for i in range(len(bought_flags))],
            "bought": bought_flags, "fwd_1_oo": fwd1, "fwd_5_oc": fwd5}
    if fwd2 is not None:
        data["fwd_2_oc"] = fwd2
    pd.DataFrame(data).to_csv(d / "attribution.csv", index=False)


def test_roll_and_render(tmp_path):
    _mk_day(tmp_path, "2026-06-24", [False, False, False], [0.01, -0.03, -0.01], [0.02, -0.06, np.nan],
            fwd2=[0.02, -0.06, -0.02])
    _mk_day(tmp_path, "2026-06-18", [True, False], [0.02, 0.04], [0.05, 0.07], fwd2=[0.03, 0.05])
    df = roll(tmp_path)
    assert list(df["date"]) == ["2026-06-18", "2026-06-24"]
    r24 = df.set_index("date").loc["2026-06-24"]
    assert r24["n_bought"] == 0 and abs(r24["mkt_fwd1"] - (-0.01)) < 1e-9
    assert abs(r24["mkt_fwd5"] - (-0.02)) < 1e-9          # NaN 容忍
    assert abs(r24["mkt_fwd2"] - (-0.02)) < 1e-9
    md = "\n".join(render(df))
    assert "0买日" in md and "2026-06-24" in md and "有买日" in md


def test_verdict_uses_fwd2(tmp_path):
    # fwd_2(主尺)全为负、fwd_5(参考)为正 的 0 买日 → verdict 仍应判「空仓方向正确」
    _mk_day(tmp_path, "2026-06-24", [False, False], [0.01, -0.02], [0.03, 0.05], fwd2=[-0.01, -0.02])
    led = roll(tmp_path)
    lines = render(led)
    assert any("mkt_fwd2" in c for c in led.columns) or "fwd_2" in "\n".join(lines)
    assert "空仓方向正确" in "\n".join(lines)


def test_verdict_falls_back_without_fwd2_column(tmp_path):
    """旧 attribution(无 fwd_2_oc 列,当前 100% 真实生产形态)→ roll/render 不炸,
    verdict 回退按 fwd_1 正常出(fwd_2 全列缺,非单行 NaN)。"""
    _mk_day(tmp_path, "2026-06-24", [False, False, False],
            [-0.01, -0.02, 0.01], [0.03, -0.02, 0.01])   # 不传 fwd2 → 无 fwd_2_oc 列
    led = roll(tmp_path)
    assert "mkt_fwd2" in led.columns and pd.isna(led.iloc[0]["mkt_fwd2"])
    md = "\n".join(render(led))
    assert "空仓方向正确" in md          # v1 均值 −0.00667 < 0 → 回退判定生效,不炸


def test_empty_root_graceful(tmp_path):
    df = roll(tmp_path)
    assert len(df) == 0
    assert any("无" in ln for ln in render(df))


def test_render_can_include_causal_verdict_summary():
    legacy = pd.DataFrame(
        [
            {
                "date": "2026-07-28",
                "n_bought": 0,
                "n_stocks": 2,
                "mkt_fwd1": -0.01,
                "mkt_fwd2": -0.02,
                "mkt_fwd5": None,
            }
        ]
    )
    causal = pd.DataFrame(
        [
            {
                "date": "2026-07-28",
                "status": "FALSE",
                "n_opportunities": 1,
            }
        ]
    )
    text = "\n".join(render(legacy, causal=causal))
    assert "因果裁决" in text
    assert "FALSE" in text


def test_bought_mask_is_public_and_reused_by_journal(tmp_path):
    """D5 单一事实源:`bought_mask` 抽成可复用原语(journal.py 会同口径导入,见 test_journal.py)。

    兼容字符串 True/False、1/0;缺列 → 全 False(不炸,现行为不变)。
    """
    df = pd.DataFrame({"bought": [True, False, "True", "false", 1, 0]})
    m = bought_mask(df)
    assert list(m) == [True, False, True, False, True, False]
    assert list(bought_mask(pd.DataFrame({"code": ["000001"]}))) == [False]
