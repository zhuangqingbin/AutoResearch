"""score_title + news_digest 情感升级单测 —— 否定消解 + 强度 + 数值净情感。无网络。

覆盖 spec §C:
  - 利多/利空方向;强词 intensity ×2 > 弱词
  - 否定/澄清词 → 中性化(保守不翻转)
  - digest 净情感 *_sent ∈[-1,1] 且符号正确;空→0.0
"""
from __future__ import annotations

from autoresearch.scan.agents.l3_news import news_digest, score_title


def test_positive_strong():
    d, i = score_title("公司公告回购股份并增持")        # 回购+增持 皆强
    assert d == "利多" and i >= 4


def test_negative_strong():
    d, i = score_title("收到证监会立案告知书")          # 立案 强
    assert d == "利空" and i >= 2


def test_negation_neutralizes():
    d, i = score_title("澄清公告:不存在重组事项")        # 澄清/不 → 中性(虽含"重组")
    assert d == "" and i == 0.0


def test_strong_intensity_gt_mild():
    _, strong = score_title("回购")                    # 强词 ×2
    _, mild = score_title("签约")                      # 弱词 ×1
    assert strong > mild


def test_digest_net_sent_positive():
    anns = [{"title": "回购", "ann_date": "20260601"}, {"title": "增持", "ann_date": "20260602"}]
    dg = news_digest(anns)
    assert dg["news_sent"] > 0 and dg["news_n"] == 2 and "利多" in dg["news_tags"]


def test_digest_mixed_sent_in_range():
    anns = [{"title": "回购", "ann_date": "1"}, {"title": "立案", "ann_date": "2"}]
    dg = news_digest(anns)
    assert -1.0 <= dg["news_sent"] <= 1.0


def test_digest_empty_sent_zero():
    dg = news_digest([])
    assert dg["news_sent"] == 0.0 and dg["news_n"] == 0


def test_med_prefix_sent_key():
    dg = news_digest([{"title": "中标重大订单", "ann_date": "1"}], prefix="med")
    assert "med_sent" in dg and dg["med_sent"] > 0
