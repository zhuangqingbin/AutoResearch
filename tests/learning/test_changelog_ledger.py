"""重标定效果 ledger:前后日度 IC 对比 + 薄样本旗。合成,无网络。

spec: docs/specs/2026-07-02-scan-observability-design.md §3
"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.learning.changelog_ledger import day_ics, render, roll


def _attr_day(root, date, ic_pos: bool):
    d = root / date / "retro"
    d.mkdir(parents=True)
    comp = list(range(20))
    fwd = [c * 0.001 for c in comp] if ic_pos else [-c * 0.001 for c in comp]
    pd.DataFrame({"code": [f"{i:06d}" for i in comp], "composite": comp,
                  "fwd_1_oo": fwd}).to_csv(d / "attribution.csv", index=False)


def _changelog(root, retro_date):
    p = root / "changelog.jsonl"
    rec = {"id": "cl_x", "ts": "t", "kind": "recalibrate", "retro_date": retro_date,
           "before_sha": "a", "after_sha": "b", "top_changes": [], "panel_dates_n": 9}
    p.write_text(json.dumps(rec) + "\n", encoding="utf-8")


def test_delta_and_render(tmp_path):
    scan = tmp_path / "scan"
    for d in ("2026-06-24", "2026-06-25", "2026-06-26"):
        _attr_day(scan, d, ic_pos=False)                    # 采纳前 IC = −1
    for d in ("2026-06-29", "2026-06-30", "2026-07-01"):
        _attr_day(scan, d, ic_pos=True)                     # 采纳后 IC = +1
    kn = tmp_path / "kn"
    kn.mkdir()
    _changelog(kn, "2026-06-29")
    ics = day_ics(scan)
    assert len(ics) == 6 and ics["2026-06-24"] < 0 < ics["2026-07-01"]
    df = roll(kn, scan)
    r = df.iloc[0]
    assert r["n_before"] == 3 and r["n_after"] == 3 and not r["thin"]
    assert r["delta"] == 2.0                                # −1 → +1
    assert "汇总" in "\n".join(render(df))


def test_thin_flag_and_empty(tmp_path):
    scan = tmp_path / "scan"
    _attr_day(scan, "2026-06-26", ic_pos=False)
    _attr_day(scan, "2026-06-29", ic_pos=True)
    kn = tmp_path / "kn"
    kn.mkdir()
    _changelog(kn, "2026-06-29")
    df = roll(kn, scan)
    assert df.iloc[0]["thin"]                               # 前后各 1 日 <3
    assert "⚠样本少" in "\n".join(render(df))
    assert not len(roll(tmp_path / "nope", scan))           # 无 changelog → 空


def test_trial_count_and_dsr_lines(tmp_path):
    """P0-6:trial 按 retro_date 升序 1-based;渲染含多重检验行;最近 Δ≤0 亮 C18 红灯。"""
    import pandas as pd

    from autoresearch.learning.changelog_ledger import render, roll  # noqa: F401
    df = pd.DataFrame([
        {"id": "a", "retro_date": "2026-07-01", "trial": 1, "n_before": 3, "n_after": 3,
         "ic_before": 0.01, "ic_after": 0.02, "delta": 0.01, "thin": False},
        {"id": "b", "retro_date": "2026-07-05", "trial": 2, "n_before": 3, "n_after": 3,
         "ic_before": 0.02, "ic_after": 0.01, "delta": -0.01, "thin": False},
    ])
    text = "\n".join(render(df))
    assert "已试 **2** 版" in text and "多重检验" in text
    assert "C18 红灯" in text and "停止调参信号" in text
