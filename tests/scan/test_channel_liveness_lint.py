"""通道活性探针:启用通道在 L1_channels.csv 0 行 → warn;连续 ≥3 扫描日 → 🔴 前缀。合成,零网络。

病灶:reversal_confirm 名义启用实际恒空 4 周+无人发现(2026-08-19 才摘);event 桶 29 日天天
改生产输入。探针配方:自动腿必须有一个会变的量做断言,否则它死了也像活着。"""
from __future__ import annotations

import pandas as pd

from autoresearch.scan.self_review import channel_liveness_lint

CH = ["composite", "momentum", "reversal_confirm"]


def _day(root, date, counts: dict[str, int]):
    d = root / date
    d.mkdir(parents=True, exist_ok=True)
    rows = [{"channel": ch, "code": f"{i:06d}", "channel_rank": i + 1, "channel_score": 1.0}
            for ch, n in counts.items() for i in range(n)]
    pd.DataFrame(rows, columns=["channel", "code", "channel_rank", "channel_score"]).to_csv(
        d / "L1_channels.csv", index=False)
    return d


def test_enabled_channel_with_zero_rows_warns(tmp_path):
    d = _day(tmp_path, "2026-08-20", {"composite": 5, "momentum": 3, "reversal_confirm": 0})
    rows = channel_liveness_lint(d, "2026-08-20", recall_channels=CH)
    assert len(rows) == 1 and rows[0]["code"] == "reversal_confirm" and rows[0]["severity"] == "warn"
    assert "连续 1" in rows[0]["detail"] and "🔴" not in rows[0]["detail"]


def test_all_live_is_silent_and_missing_file_is_silent(tmp_path):
    d = _day(tmp_path, "2026-08-20", {"composite": 5, "momentum": 3, "reversal_confirm": 2})
    assert channel_liveness_lint(d, "2026-08-20", recall_channels=CH) == []
    (d / "L1_channels.csv").unlink()
    assert channel_liveness_lint(d, "2026-08-20", recall_channels=CH) == []


def test_three_consecutive_empty_days_escalate_prefix(tmp_path):
    for date in ("2026-08-17", "2026-08-18", "2026-08-19"):
        _day(tmp_path, date, {"composite": 5, "reversal_confirm": 0})
    d = _day(tmp_path, "2026-08-20", {"composite": 5, "reversal_confirm": 0})
    rows = channel_liveness_lint(d, "2026-08-20", recall_channels=CH, history_days=3)
    assert rows[0]["severity"] == "warn" and rows[0]["detail"].startswith("🔴")
    assert "连续 4" in rows[0]["detail"]


def test_streak_breaks_on_a_live_day(tmp_path):
    for date, n in (("2026-08-18", 0), ("2026-08-19", 4), ("2026-08-20", 0)):   # 中间活过一天
        d = _day(tmp_path, date, {"composite": 5, "momentum": 3, "reversal_confirm": n})
    rows = channel_liveness_lint(d, "2026-08-20", recall_channels=CH)
    rc = [r for r in rows if r["code"] == "reversal_confirm"]
    assert len(rc) == 1 and "连续 1" in rc[0]["detail"]        # streak 被 08-19 的活跃日打断


def test_every_empty_enabled_channel_gets_its_own_row(tmp_path):
    """探针逐通道报,不只报第一个 —— 一天里两路同时死,必须两行都看得见。"""
    d = _day(tmp_path, "2026-08-20", {"composite": 5, "momentum": 0, "reversal_confirm": 0})
    rows = channel_liveness_lint(d, "2026-08-20", recall_channels=CH)
    assert {r["code"] for r in rows} == {"momentum", "reversal_confirm"}


def test_no_config_means_silent(tmp_path, monkeypatch):
    d = _day(tmp_path, "2026-08-20", {"composite": 5})
    monkeypatch.setattr("autoresearch.scan.user_config.load_user_config", lambda *a, **k: {})
    assert channel_liveness_lint(d, "2026-08-20") == []
