"""scan 进度探测(确定性读盘,零 LLM):workflow 后台跑时主对话一片空白 —— 本模块把
"跑到哪一步" 从产物文件反推出来,供 Monitor 轮询播报。合成,无网络。

铁律:只读盘、不猜。产物不在 = 该阶段未完成(不臆测"大概快好了")。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.scan.progress import render_line, snapshot


def _mk(root, date="2026-07-10"):
    d = root / "context" / "scan" / date
    d.mkdir(parents=True)
    return d


def test_snapshot_empty_dir_is_pending(tmp_path):
    d = _mk(tmp_path)
    s = snapshot(d, report_root=tmp_path / "reports" / "scan")
    assert s["stage"] == "Prelude"
    assert s["done"] == []
    assert s["cards"] == 0


def test_snapshot_prelude_complete(tmp_path):
    d = _mk(tmp_path)
    (d / "market_pack.json").write_text("{}", encoding="utf-8")
    pd.DataFrame({"code": ["000001"] * 900}).to_csv(d / "L1_recall_top1000.csv", index=False)
    pd.DataFrame({"code": ["000001"] * 204}).to_csv(d / "L2_gbdt_top200.csv", index=False)
    (d / "market_view.md").write_text("# 市场研判", encoding="utf-8")

    s = snapshot(d, report_root=tmp_path / "reports" / "scan")
    assert s["stage"] == "L3"                    # Prelude 产物齐 → 已进 L3
    assert s["l1"] == 900 and s["l2"] == 204
    assert "market_view" in s["done"]


def test_snapshot_l4_card_progress(tmp_path):
    d = _mk(tmp_path)
    pd.DataFrame({"code": ["000001"] * 204}).to_csv(d / "L2_gbdt_top200.csv", index=False)
    (d / "_l3_judged.json").write_text("[]", encoding="utf-8")
    pd.DataFrame({"code": ["000001", "000002", "000003"]}).to_csv(d / "finalists.csv", index=False)
    for c in ("000001", "000002", "000003"):
        (d / f"_l4_prompt_{c}.md").write_text("x", encoding="utf-8")
    (d / "_l4_intel_000001.md").write_text("x", encoding="utf-8")
    (d / "details").mkdir()
    (d / "details" / "000001.md").write_text("**Rating**: Overweight\n", encoding="utf-8")
    (d / "details" / "000002.md").write_text("**Rating**: Hold\n", encoding="utf-8")

    s = snapshot(d, report_root=tmp_path / "reports" / "scan")
    assert s["stage"] == "L4"
    assert s["finalists"] == 3
    assert s["dispatch"] == 3
    assert s["intel"] == 1
    assert s["cards"] == 2                        # 2/3 卡回来了
    assert s["ratings"] == {"Overweight": 1, "Hold": 1}


def test_snapshot_done_when_summary_published(tmp_path):
    d = _mk(tmp_path)
    pd.DataFrame({"code": ["000001"] * 204}).to_csv(d / "L2_gbdt_top200.csv", index=False)
    rr = tmp_path / "reports" / "scan" / "20260710_1857"
    rr.mkdir(parents=True)
    (rr / "manifest.json").write_text('{"analysis_date": "2026-07-10"}', encoding="utf-8")
    (rr / "summary.md").write_text("# 报告", encoding="utf-8")

    s = snapshot(d, report_root=tmp_path / "reports" / "scan")
    assert s["stage"] == "Done"
    assert s["report"].endswith("20260710_1857")


def test_snapshot_ignores_other_days_report(tmp_path):
    """别的日期的 report 目录不能把今天误判成 Done(manifest.analysis_date 定位)。"""
    d = _mk(tmp_path)
    pd.DataFrame({"code": ["000001"] * 204}).to_csv(d / "L2_gbdt_top200.csv", index=False)
    rr = tmp_path / "reports" / "scan" / "20260709_2218"
    rr.mkdir(parents=True)
    (rr / "manifest.json").write_text('{"analysis_date": "2026-07-09"}', encoding="utf-8")
    (rr / "summary.md").write_text("# 昨天的报告", encoding="utf-8")

    s = snapshot(d, report_root=tmp_path / "reports" / "scan")
    assert s["stage"] != "Done"
    assert s["report"] is None


def test_render_line_is_single_line_and_has_counts(tmp_path):
    d = _mk(tmp_path)
    pd.DataFrame({"code": ["000001"] * 204}).to_csv(d / "L2_gbdt_top200.csv", index=False)
    (d / "_l3_judged.json").write_text("[]", encoding="utf-8")
    pd.DataFrame({"code": ["000001", "000002"]}).to_csv(d / "finalists.csv", index=False)
    (d / "details").mkdir()
    (d / "details" / "000001.md").write_text("**Rating**: Hold\n", encoding="utf-8")

    line = render_line(snapshot(d, report_root=tmp_path / "reports" / "scan"))
    assert "\n" not in line                        # Monitor 一行 = 一条通知
    assert "L4" in line and "1/2" in line          # 卡进度可读
