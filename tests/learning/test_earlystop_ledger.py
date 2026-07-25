"""早停账本:按停因桶累计 fwd_2_oc,为「强势票早停是不是误杀」攒裁决样本(Wave5 ②C)。"""
from __future__ import annotations

import json

from autoresearch.learning import earlystop_ledger as el


def _day(root, date, stops: dict, attribution: str):
    d = root / date
    (d / "retro").mkdir(parents=True)
    (d / "_early_stop.json").write_text(json.dumps(stops, ensure_ascii=False), encoding="utf-8")
    (d / "retro" / "attribution.csv").write_text(attribution, encoding="utf-8")


def test_roll_joins_stops_to_forward_returns(tmp_path):
    _day(tmp_path, "2026-07-21",
         {"000651": {"phase": "P3", "reason": "涨停追高"},
          "300857": {"phase": "P3", "reason": "资金流出"}},
         "code,fwd_2_oc\n000651,0.05\n300857,-0.02\n")
    df = el.roll(scan_root=tmp_path)
    assert len(df) == 2
    row = df[df["code"] == "000651"].iloc[0]
    assert row["reason"] == "涨停追高"
    assert abs(float(row["fwd_2_oc"]) - 0.05) < 1e-9


def test_render_buckets_by_reason(tmp_path):
    _day(tmp_path, "2026-07-21",
         {"000651": {"phase": "P3", "reason": "涨停追高"},
          "000002": {"phase": "P3", "reason": "涨停追高"}},
         "code,fwd_2_oc\n000651,0.05\n000002,0.03\n")
    md = "\n".join(el.render(el.roll(scan_root=tmp_path)))
    assert "涨停追高" in md
    assert "n=2" in md
    assert "样本不足" in md          # n<10 必须自标禁裁决


def test_empty_root_renders_placeholder(tmp_path):
    md = "\n".join(el.render(el.roll(scan_root=tmp_path)))
    assert "无早停记录" in md
