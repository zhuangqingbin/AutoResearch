from autoresearch.scan import decision_finalize as df
from autoresearch.scan import report_sections as rs


def test_conflict_when_tripwire_fires_and_rating_not_sell(monkeypatch):
    monkeypatch.setattr(df, "_tripwire_hits", lambda *a, **k: [
        {"code": "300857", "kind": "price", "raw": "[价格线] close < 210.01 → 清仓",
         "detail": "收盘 205.00 < 210.01 → 清仓", "card_date": "2026-07-28"}])
    out = df.tripwire_conflicts("ignored", "2026-07-29",
                                [{"code": "300857", "rating": "Underweight"}])
    assert "300857" in out
    assert out["300857"]["rating"] == "Underweight"
    assert "205.00" in out["300857"]["tripwire_detail"]


def test_no_conflict_when_rating_is_sell(monkeypatch):
    """终评已是 Sell = 两把尺子同向,不构成冲突。"""
    monkeypatch.setattr(df, "_tripwire_hits", lambda *a, **k: [
        {"code": "920179", "kind": "price", "raw": "r", "detail": "d", "card_date": "x"}])
    out = df.tripwire_conflicts("ignored", "2026-07-29",
                                [{"code": "920179", "rating": "Sell"}])
    assert out == {}


def test_no_conflict_when_no_tripwire(monkeypatch):
    monkeypatch.setattr(df, "_tripwire_hits", lambda *a, **k: [])
    out = df.tripwire_conflicts("ignored", "2026-07-29",
                                [{"code": "688766", "rating": "Hold"}])
    assert out == {}


def test_date_kind_hits_are_not_conflicts(monkeypatch):
    """日期线/事件旗是提醒,不是与评级对立的价格判据。"""
    monkeypatch.setattr(df, "_tripwire_hits", lambda *a, **k: [
        {"code": "300857", "kind": "date", "raw": "r", "detail": "d", "card_date": "x"}])
    out = df.tripwire_conflicts("ignored", "2026-07-29",
                                [{"code": "300857", "rating": "Hold"}])
    assert out == {}


def test_conflict_block_renders_two_rulers():
    block = rs._conflict_block({"300857": {
        "tripwire_detail": "收盘 205.00 < 210.01 → 清仓",
        "tripwire_raw": "[价格线] close < 210.01 → 清仓",
        "rating": "Underweight"}})
    assert "300857" in block
    assert "收盘 205.00 < 210.01" in block
    assert "Underweight" in block
    assert "系统不合并" in block


def test_conflict_block_empty_when_no_conflict():
    assert rs._conflict_block({}) == ""
