"""P2a:harvest_l3_evidence 走 get_or_fetch(湖),不再 _ts_call 裸调。"""
import json

import pandas as pd


def test_evidence_routes_through_lake(tmp_path, monkeypatch):
    calls: list[tuple[str, dict]] = []

    def fake_gof(endpoint, params, today=None, fetch=None):
        calls.append((endpoint, dict(params)))
        if endpoint == "top_list":
            return pd.DataFrame({"ts_code": ["000001.SZ"], "net_amount": [1.0]})
        return pd.DataFrame({"ts_code": ["000001.SZ"], "type": ["预增"]})

    import autoresearch.data.cache as cache
    monkeypatch.setattr(cache, "get_or_fetch", fake_gof)
    import autoresearch.data.tushare_source as ts
    monkeypatch.setattr(ts, "_pro", lambda: object())
    monkeypatch.setattr(ts, "resolve_momentum_dates", lambda pro, d: ("20260710", "", ""))
    monkeypatch.setattr(ts, "_trade_days", lambda pro, s, e: [f"202607{i:02d}" for i in range(1, 11)])

    from autoresearch.scan.agents.l3_select import harvest_l3_evidence
    ev = harvest_l3_evidence("2026-07-10", ["000001"], root=tmp_path)

    eps = {c[0] for c in calls}
    assert eps == {"top_list", "forecast", "express"}
    assert len([c for c in calls if c[0] == "forecast"]) == 10
    assert "_errors" not in ev
    saved = json.loads((tmp_path / "2026-07-10" / "L3_evidence" / "000001.json").read_text(encoding="utf-8"))
    assert saved["code"] == "000001" and "longhu" in saved and "forecast" in saved


def test_anns_permission_fast_fail(tmp_path, monkeypatch):
    n = {"calls": 0}

    def boom(endpoint, params, today=None):
        n["calls"] += 1
        raise RuntimeError("抱歉,您没有访问该接口的权限")

    import autoresearch.scan.agents.l3_news as ln
    monkeypatch.setattr(ln, "get_or_fetch", boom)
    monkeypatch.setattr(ln, "_trade_days_for", lambda date, lb: [f"202607{i:02d}" for i in range(1, 11)])
    buckets = ln.harvest_l3_news("2026-07-10", ["000001", "600000"], root=tmp_path)
    assert n["calls"] == 1                                  # 权限错 → 首日即 break
    assert buckets == {"000001": [], "600000": []}
    for c in ("000001", "600000"):                          # 空稿仍写(产物字节不变)
        assert (tmp_path / "2026-07-10" / "L3_news" / f"{c}.json").read_text(encoding="utf-8") == "[]"


def test_anns_transient_fail_capped_at_3(tmp_path, monkeypatch):
    n = {"calls": 0}

    def flaky(endpoint, params, today=None):
        n["calls"] += 1
        raise ConnectionError("timeout")

    import autoresearch.scan.agents.l3_news as ln
    monkeypatch.setattr(ln, "get_or_fetch", flaky)
    monkeypatch.setattr(ln, "_trade_days_for", lambda date, lb: [f"202607{i:02d}" for i in range(1, 11)])
    ln.harvest_l3_news("2026-07-10", ["000001"], root=tmp_path)
    assert n["calls"] == 3                                  # 任意异常有界:3 次封顶
