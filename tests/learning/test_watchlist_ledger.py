"""watchlist 触发→后市度量:roll join fwd / render / 空目录。合成,无网络。"""
from __future__ import annotations

import pandas as pd

from autoresearch.learning.watchlist_ledger import render, roll


def _mk_day(root, date, statuses, attr_rows=None):
    d = root / date
    d.mkdir(parents=True)
    pd.DataFrame(statuses).to_csv(d / "watchlist_status.csv", index=False)
    if attr_rows is not None:
        (d / "retro").mkdir()
        pd.DataFrame(attr_rows).to_csv(d / "retro" / "attribution.csv", index=False)


def test_roll_triggers_join_fwd(tmp_path):
    _mk_day(tmp_path, "2026-07-01",
            [{"code": "300476", "name": "胜宏科技", "status": "触发", "detail": "d",
              "narrative": "n", "born": "2026-06-30", "expiry": "2026-08-31"},
             {"code": "000001", "name": "甲", "status": "临近", "detail": "d",
              "narrative": "n", "born": "2026-06-30", "expiry": "2026-08-31"}],
            [{"code": "300476", "fwd_1_oo": 0.03, "fwd_2_oc": 0.05, "fwd_5_oc": 0.08},
             {"code": "000001", "fwd_1_oo": 0.0, "fwd_2_oc": 0.0, "fwd_5_oc": 0.0}])
    df = roll(tmp_path)
    assert len(df) == 1 and df.iloc[0]["code"] == "300476"        # 只统计触发行
    assert "fwd_2" in df.columns
    assert abs(df.iloc[0]["fwd_2"] - 0.05) < 1e-9
    assert abs(df.iloc[0]["fwd_5"] - 0.08) < 1e-9
    md = "\n".join(render(df))
    assert "胜宏科技" in md and "触发" in md and "fwd_2" in md


def test_render_summary_uses_fwd2_primary(tmp_path):
    """汇总行主尺切 fwd_2:均值/胜率以 fwd_2 dropna 为分母(主尺),fwd_5 保留降参考,不炸。"""
    _mk_day(tmp_path, "2026-07-01",
            [{"code": "300476", "name": "胜宏科技", "status": "触发", "detail": "d",
              "narrative": "n", "born": "2026-06-30", "expiry": "2026-08-31"}],
            [{"code": "300476", "fwd_1_oo": 0.03, "fwd_2_oc": -0.02, "fwd_5_oc": 0.08}])
    md = "\n".join(render(roll(tmp_path)))
    assert "fwd_2 均值 -2.00%(主尺)" in md
    assert "fwd_5 均值 +8.00%(参考)" in md


def test_render_summary_falls_back_when_fwd2_missing(tmp_path):
    """旧数据(attribution 无 fwd_2_oc 列)→ 汇总回退只出 fwd_5,不炸。"""
    _mk_day(tmp_path, "2026-07-02",
            [{"code": "300477", "name": "乙", "status": "触发", "detail": "d",
              "narrative": "n", "born": "2026-06-30", "expiry": "2026-08-31"}],
            [{"code": "300477", "fwd_1_oo": 0.02, "fwd_5_oc": -0.03}])
    md = "\n".join(render(roll(tmp_path)))
    assert "fwd_5 均值 -3.00%" in md
    assert "主尺" not in md


def test_empty_graceful(tmp_path):
    assert len(roll(tmp_path)) == 0
    assert any("无" in ln for ln in render(roll(tmp_path)))


def test_monitoring_section_born_to_date(tmp_path):
    import pandas as pd

    from autoresearch.learning.watchlist_ledger import monitoring_section
    d = tmp_path / "2026-07-03"
    d.mkdir(parents=True)
    pd.DataFrame([{"code": "300476", "name": "胜宏科技", "status": "待触发",
                   "since_born": 0.22, "fire": True}]).to_csv(d / "watchlist_status.csv", index=False)
    text = "\n".join(monitoring_section(tmp_path))
    assert "born→今" in text and "+22%" in text and "🔥" in text
    assert monitoring_section(tmp_path / "nope") == []
