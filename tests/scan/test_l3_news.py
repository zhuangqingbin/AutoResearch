"""公告情感 digest(确定性)+ harvest 降级(无网络)。"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.scan.agents import l3_news
from autoresearch.scan.agents.l3_news import harvest_l3_news, news_digest


def test_digest_empty_defaults():
    assert news_digest([]) == {"news_n": 0, "news_tags": "", "news_head": "—", "news_sent": 0.0}


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
    assert set(d) == {"med_n", "med_tags", "med_sent", "med_head"}
    assert d["med_n"] == 1 and "利多×1" in d["med_tags"]


def test_news_digest_default_prefix_unchanged():
    assert set(news_digest([])) == {"news_n", "news_tags", "news_sent", "news_head"}
    assert set(news_digest([{"title": "x", "ann_date": "1"}])) == {"news_n", "news_tags", "news_sent", "news_head"}


# ───────────────────────── anns_d 退役:一次性告警,不再逐日试探 ─────────────────────────
# monkeypatch module-attr `get_or_fetch` 用 pytest 的 monkeypatch fixture(自动 teardown 恢复),
# 不做裸赋值——裸赋值会永久改写 l3_news 模块命名空间,污染同进程后续测试(brief 原稿如此,已改)。


def test_harvest_l3_news_retired_endpoint_is_loud(capsys, tmp_path, monkeypatch):
    """anns_d 已退役(无权限):必须一次性识别 + 打印告警,不得静默写空。"""
    calls = {"n": 0}

    def _boom(endpoint, params, today=None):
        calls["n"] += 1
        raise Exception("抱歉，您没有接口(anns_d)访问权限")

    monkeypatch.setattr(l3_news, "get_or_fetch", _boom)
    out = l3_news.harvest_l3_news("2026-07-24", ["300857", "002371"], root=tmp_path)
    assert out == {"300857": [], "002371": []}
    assert calls["n"] <= 1, "权限错必然日日同错:不得逐日重试"
    cap = capsys.readouterr()
    assert "anns_d" in (cap.out + cap.err) and "退役" in (cap.out + cap.err), \
        "断链必须留痕(降级不留痕是本项目最忌的形态)"


def test_harvest_l3_news_writes_empty_buckets_still(tmp_path, monkeypatch):
    """契约不变:仍为每只票落 json(下游 news_digest 依赖文件存在)。"""
    monkeypatch.setattr(l3_news, "get_or_fetch",
                        lambda *a, **k: (_ for _ in ()).throw(Exception("权限")))
    l3_news.harvest_l3_news("2026-07-24", ["300857"], root=tmp_path)
    assert (tmp_path / "2026-07-24" / "L3_news" / "300857.json").exists()

