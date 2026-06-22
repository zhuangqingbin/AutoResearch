"""公告情感 digest(确定性)+ harvest 降级(无网络)。"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.scan.agents import l3_news
from autoresearch.scan.agents.l3_news import harvest_l3_news, news_digest


def test_digest_empty_defaults():
    assert news_digest([]) == {"news_n": 0, "news_tags": "", "news_head": "—"}


def test_digest_counts_tags_and_latest_head():
    anns = [
        {"ann_date": "20260618", "title": "关于回购公司股份的进展公告"},      # 利多
        {"ann_date": "20260620", "title": "第一大股东减持计划"},            # 利空(最新)
        {"ann_date": "20260619", "title": "关于增持公司股份的公告"},        # 利多
        {"ann_date": "20260617", "title": "关于召开股东大会的通知"},        # 中性
    ]
    d = news_digest(anns)
    assert d["news_n"] == 4
    assert "利多×2" in d["news_tags"] and "利空×1" in d["news_tags"]
    assert d["news_head"].startswith("第一大股东减持")          # ann_date 最大者
    assert len(d["news_head"]) <= 24


def test_harvest_degrades_when_fetch_empty(monkeypatch, tmp_path):
    """get_or_fetch 抛错/空 → 各 code 空列表、写 staging、不抛。"""
    monkeypatch.setattr(l3_news, "_trade_days_for", lambda date, n: ["20260620", "20260619"])
    monkeypatch.setattr(l3_news, "get_or_fetch",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no permission")))
    out = harvest_l3_news("2026-06-20", ["000001", "600000"], root=tmp_path / "scan")
    assert out == {"000001": [], "600000": []}
    saved = json.loads((tmp_path / "scan" / "2026-06-20" / "L3_news" / "000001.json").read_text())
    assert saved == []


def test_harvest_buckets_by_code(monkeypatch, tmp_path):
    monkeypatch.setattr(l3_news, "_trade_days_for", lambda date, n: ["20260620"])
    fake = pd.DataFrame({"ts_code": ["000001.SZ", "600000.SH"], "ann_date": ["20260620", "20260620"],
                         "title": ["回购公告", "减持公告"]})
    monkeypatch.setattr(l3_news, "get_or_fetch", lambda *a, **k: fake.copy())
    out = harvest_l3_news("2026-06-20", ["000001", "600000"], root=tmp_path / "scan")
    assert len(out["000001"]) == 1 and out["000001"][0]["title"] == "回购公告"
    assert len(out["600000"]) == 1


# ───────────────────────── 媒体新闻(akshare stock_news_em)─────────────────────────


def test_news_digest_prefix_med():
    d = news_digest([{"title": "某公司中标大单", "ann_date": "20260601"}], prefix="med")
    assert set(d) == {"med_n", "med_tags", "med_head"}
    assert d["med_n"] == 1 and "利多×1" in d["med_tags"]


def test_news_digest_default_prefix_unchanged():
    assert set(news_digest([])) == {"news_n", "news_tags", "news_head"}
    assert set(news_digest([{"title": "x", "ann_date": "1"}])) == {"news_n", "news_tags", "news_head"}


def test_harvest_web_news_normalizes_and_buckets(tmp_path, monkeypatch):
    """注入 get_or_fetch 桩:归一 akshare 中文列(新闻标题/发布时间)→ title/ann_date,按 code 分桶 + 落 json。"""
    def fake_gof(endpoint, params, today=None, fetch=None):
        assert endpoint == "stock_news_em"
        return pd.DataFrame({"新闻标题": [f"{params['symbol']} 中标大单"],
                             "发布时间": ["2026-06-20 09:00:00"]})
    monkeypatch.setattr(l3_news, "get_or_fetch", fake_gof, raising=True)
    out = l3_news.harvest_l3_web_news("2026-06-20", ["600519", "000001"], root=tmp_path / "scan")
    assert set(out) == {"600519", "000001"}
    assert out["600519"][0]["title"] == "600519 中标大单" and out["600519"][0]["ann_date"].startswith("2026")
    saved = json.loads((tmp_path / "scan" / "2026-06-20" / "L3_webnews" / "600519.json").read_text())
    assert saved[0]["title"] == "600519 中标大单"


def test_harvest_web_news_degrades_on_error(tmp_path, monkeypatch):
    """单股 get_or_fetch 抛错 → 该 code 空列表、写空 json、不抛(降级隔离)。"""
    def boom(endpoint, params, today=None, fetch=None):
        raise RuntimeError("no net")
    monkeypatch.setattr(l3_news, "get_or_fetch", boom, raising=True)
    out = l3_news.harvest_l3_web_news("2026-06-20", ["600519"], root=tmp_path / "scan")
    assert out["600519"] == []
    assert json.loads((tmp_path / "scan" / "2026-06-20" / "L3_webnews" / "600519.json").read_text()) == []
