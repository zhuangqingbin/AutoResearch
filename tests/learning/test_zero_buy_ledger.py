"""0买日市场对照 ledger:roll 字段/render 对照/空目录优雅。合成 fixture。

spec: docs/specs/2026-07-02-scan-watchlist-and-health-metrics-design.md §2.3
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from autoresearch.learning.zero_buy_ledger import render, roll


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


def test_empty_root_graceful(tmp_path):
    df = roll(tmp_path)
    assert len(df) == 0
    assert any("无" in ln for ln in render(df))
