"""P1b:夜间预热——结算日解析(19:15 门)+ 步骤编排 + _prewarm.json + env 生命周期。"""
import json
import os
from datetime import datetime


def _patch_tradedays(monkeypatch):
    import autoresearch.data.tushare_source as ts
    monkeypatch.setattr(ts, "_pro", lambda: object())
    monkeypatch.setattr(ts, "_trade_days",
                        lambda pro, s, e: [d for d in ("20260709", "20260710") if d <= e.replace("-", "")])


def test_latest_settled_before_1915_falls_back(monkeypatch):
    _patch_tradedays(monkeypatch)
    from autoresearch.scan.prewarm import latest_settled_trade_date
    assert latest_settled_trade_date(datetime(2026, 7, 10, 18, 0)) == "2026-07-09"
    assert latest_settled_trade_date(datetime(2026, 7, 10, 19, 30)) == "2026-07-10"


def test_run_prewarm_writes_manifest_and_env_lifecycle(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                      # context/scan/<date> 落 tmp
    _patch_tradedays(monkeypatch)
    import autoresearch.scan.prewarm as pw
    monkeypatch.setattr(pw, "_frame_lake", lambda date: "帧 4000 只已入湖")
    monkeypatch.setattr(pw, "_prewarm_evidence", lambda date: "21 次端点预拉")
    monkeypatch.setattr(pw, "_temperature", lambda date: "1 行")
    monkeypatch.setattr(pw, "_dossier_prefetch", lambda date: "池预取 3/3")
    res = pw.run_prewarm(now=datetime(2026, 7, 10, 19, 30))
    assert res["date"] == "2026-07-10" and res["ok"]
    assert os.environ.get("LAKE_ASSUME_SETTLED") is None            # 收尾必清
    j = json.loads((tmp_path / "context/scan/2026-07-10/_prewarm.json").read_text(encoding="utf-8"))
    assert j["ended_at"] >= j["started_at"]
    assert [s["step"] for s in j["steps"]] == \
        ["frame_lake", "evidence_lake", "temperature", "dossier_prefetch"]


def test_run_prewarm_past_date_no_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _patch_tradedays(monkeypatch)
    import autoresearch.scan.prewarm as pw
    seen = {}
    monkeypatch.setattr(pw, "_frame_lake",
                        lambda date: seen.setdefault("env", os.environ.get("LAKE_ASSUME_SETTLED")))
    monkeypatch.setattr(pw, "_prewarm_evidence", lambda date: "")
    monkeypatch.setattr(pw, "_temperature", lambda date: "")
    monkeypatch.setattr(pw, "_dossier_prefetch", lambda date: "")
    pw.run_prewarm(date="2026-07-09", now=datetime(2026, 7, 10, 19, 30))
    assert seen["env"] is None                       # 目标日≠今天 → 不设豁免
