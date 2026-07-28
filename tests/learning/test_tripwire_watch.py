"""持仓盯梢线日检(Wave7 P2):两次扫描之间唯一盯着持仓的腿。

L4 卡的「失效」一直是自由文本(真卡实例:「收盘破 303.28 → 隔日清仓」)——信息在,
但没有任何机器读得了。卡片写完那一刻起到下一次扫描为止,持仓处于无人盯梢状态。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.learning import tripwire_watch as T

_CARD = """〔卡契约 v3〕
# 决策卡 — 601869 长飞光纤
**Rating**: Underweight
**盯梢线**
- [价格线] close < 303.28 → 隔日清仓
- [价格线] close >= 373.29 → 本 UW 失效,改持
- [日期线] 2026-08-25 中报披露 → 兑现验证日
- [事件旗] 减持|质押|问询 → 触发即重研
失效:收盘站回 373.29 或主力净占比转正 → 本 UW 失效。
"""


def _day(root, date, code, close, card=None):
    d = root / date
    (d / "details").mkdir(parents=True)
    if card is not None:
        (d / "details" / f"{code}.md").write_text(card, encoding="utf-8")
    pd.DataFrame([{"code": code, "close": close}]).to_csv(d / "L1_scored_full.csv", index=False)
    return d


# ── 解析 ──────────────────────────────────────────────────────────────────


def test_parses_three_kinds():
    got = T.parse_tripwires(_CARD)
    assert [w["kind"] for w in got] == ["price", "price", "date", "event"]
    assert got[0]["op"] == "<" and got[0]["level"] == 303.28
    assert got[1]["op"] == ">=" and got[1]["level"] == 373.29
    assert got[2]["date"] == "2026-08-25"
    assert got[3]["keywords"] == ["减持", "质押", "问询"]


def test_free_text_lines_are_left_to_human_eyes():
    """认不出的行不进表,也不报错 —— 不要为了凑格式把条件改简单。"""
    assert T.parse_tripwires("失效:收盘站回 373.29 或主力净占比转正 → 本 UW 失效。") == []
    assert T.parse_tripwires("") == []


def test_parse_tolerates_missing_action():
    assert T.parse_tripwires("- [价格线] close <= 10")[0]["action"] == ""


# ── 日检 ──────────────────────────────────────────────────────────────────


def test_price_line_fires_when_crossed(tmp_path):
    _day(tmp_path, "2026-07-28", "601869", close=300.0, card=_CARD)
    hits = T.check("2026-07-28", codes=["601869"], scan_root=tmp_path)
    price = [h for h in hits if h["kind"] == "price"]
    assert len(price) == 1 and "隔日清仓" in price[0]["detail"]


def test_price_line_silent_when_inside_band(tmp_path):
    _day(tmp_path, "2026-07-28", "601869", close=350.0, card=_CARD)
    assert [h for h in T.check("2026-07-28", codes=["601869"], scan_root=tmp_path)
            if h["kind"] == "price"] == []


def test_upper_price_line_also_fires(tmp_path):
    """双向都要盯:破下轨要减仓,站回上轨说明看空判断失效了 —— 后者同样是要人裁的事。"""
    _day(tmp_path, "2026-07-28", "601869", close=380.0, card=_CARD)
    hits = [h for h in T.check("2026-07-28", codes=["601869"], scan_root=tmp_path)
            if h["kind"] == "price"]
    assert len(hits) == 1 and "改持" in hits[0]["detail"]


def test_date_line_warns_only_when_near(tmp_path):
    _day(tmp_path, "2026-08-23", "601869", close=350.0, card=_CARD)
    near = [h for h in T.check("2026-08-23", codes=["601869"], scan_root=tmp_path)
            if h["kind"] == "date"]
    assert len(near) == 1 and "还有 2 天" in near[0]["detail"]

    _day(tmp_path, "2026-08-01", "601869", close=350.0, card=_CARD)
    assert [h for h in T.check("2026-08-01", codes=["601869"], scan_root=tmp_path)
            if h["kind"] == "date"] == []


def test_date_line_not_fired_after_it_passed(tmp_path):
    _day(tmp_path, "2026-08-27", "601869", close=350.0, card=_CARD)
    assert [h for h in T.check("2026-08-27", codes=["601869"], scan_root=tmp_path)
            if h["kind"] == "date"] == []


def test_event_flag_fires_on_news_title(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "_news_titles", lambda code, date: ["公司公告:股东拟减持不超过2%"])
    _day(tmp_path, "2026-07-28", "601869", close=350.0, card=_CARD)
    hits = [h for h in T.check("2026-07-28", codes=["601869"], scan_root=tmp_path)
            if h["kind"] == "event"]
    assert len(hits) == 1 and "减持" in hits[0]["detail"]


def test_event_flag_silent_when_news_unavailable(tmp_path, monkeypatch):
    """取不到新闻 → 不报(而不是报「无触发」)—— 分不清「没新闻」与「没查到」时闭嘴。"""
    monkeypatch.setattr(T, "_news_titles", lambda code, date: None)
    _day(tmp_path, "2026-07-28", "601869", close=350.0, card=_CARD)
    assert [h for h in T.check("2026-07-28", codes=["601869"], scan_root=tmp_path)
            if h["kind"] == "event"] == []


def test_uses_latest_card_not_history(tmp_path):
    """盯的是最新判断:旧卡的盯梢线已被新卡取代。"""
    old = _CARD.replace("close < 303.28", "close < 999.0")
    _day(tmp_path, "2026-07-24", "601869", close=350.0, card=old)
    _day(tmp_path, "2026-07-28", "601869", close=350.0, card=_CARD)
    assert T.latest_card("601869", tmp_path)[0] == "2026-07-28"
    assert [h for h in T.check("2026-07-28", codes=["601869"], scan_root=tmp_path)
            if h["kind"] == "price"] == []


def test_no_card_is_presence_gated(tmp_path):
    _day(tmp_path, "2026-07-28", "601869", close=350.0, card=None)
    assert T.check("2026-07-28", codes=["601869"], scan_root=tmp_path) == []


# ── 渲染 ──────────────────────────────────────────────────────────────────


def test_render_line_states_coverage_when_quiet():
    """没触发也要出行,并说清覆盖边界 —— 「未命中≠未发生」。"""
    ln = T.render_line([], n_watched=3)
    assert "3 只持仓" in ln and "未命中≠未发生" in ln


def test_render_line_none_when_nothing_watched():
    assert T.render_line([], n_watched=0) is None


def test_render_line_lists_hits():
    ln = T.render_line([{"code": "601869", "kind": "price", "detail": "收盘 300.00 < 303.28"}], 3)
    assert "601869" in ln and "人裁" in ln
