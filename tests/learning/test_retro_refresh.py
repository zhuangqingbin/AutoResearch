"""attribution 刷新:该刷才刷/完整则跳/单日失败不阻。monkeypatch attribute,无网络。

spec: docs/specs/2026-07-03-scan-run-reliability-design.md §3
"""
from __future__ import annotations

import pandas as pd

import autoresearch.learning.retro as retro


def _mk_day(root, date, cols_full=False, fwd_nan=False):
    d = root / date / "retro"
    d.mkdir(parents=True)
    (d / "done.json").write_text("{}", encoding="utf-8")
    row = {"code": "000001", "fwd_1_oo": 0.01,
           "fwd_5_oc": (None if fwd_nan else 0.05)}
    if cols_full:
        row.update(fwd_10_oc=(None if fwd_nan else 0.10),
                   hi_10_oc=(None if fwd_nan else 0.12))
    pd.DataFrame([row]).to_csv(d / "attribution.csv", index=False)


def test_refresh_only_when_needed(tmp_path, monkeypatch):
    _mk_day(tmp_path, "2026-06-19")                       # 缺 fwd_10/hi_10 列 → 该刷
    _mk_day(tmp_path, "2026-06-22", cols_full=True)       # 列全且有值 → 跳
    _mk_day(tmp_path, "2026-06-24", cols_full=True, fwd_nan=True)   # 列全但全 NaN → 该刷
    called = []

    def fake_attribute(date, scan_root=None, report_root=None, abs_thresh=0.03):
        called.append(date)
        return pd.DataFrame()

    monkeypatch.setattr(retro, "attribute", fake_attribute)
    done = retro.refresh_attributions(scan_root=tmp_path)
    assert done == ["2026-06-19", "2026-06-24"] and called == done


def test_refresh_failure_does_not_block(tmp_path, monkeypatch):
    _mk_day(tmp_path, "2026-06-19")
    _mk_day(tmp_path, "2026-06-20")

    def fake_attribute(date, scan_root=None, report_root=None, abs_thresh=0.03):
        if date == "2026-06-19":
            raise RuntimeError("fwd 未实现")
        return pd.DataFrame()

    monkeypatch.setattr(retro, "attribute", fake_attribute)
    done = retro.refresh_attributions(scan_root=tmp_path)
    assert done == ["2026-06-20"]                          # 06-19 失败被跳过,不阻余下
