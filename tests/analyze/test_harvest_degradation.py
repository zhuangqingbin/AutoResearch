"""harvest 降级全记账(D1.3):`_section` 异常 → 先记账(B 级)再写降级文本。

Task 3 brief: 29 处 T 级(裸报错文案,只有人读)→ B 级(`contracts.record_degradation`,
可审计、`degradations()`/`main()` 尾账都能读到)。
"""
from autoresearch.analyze import harvest
from autoresearch.data import contracts as dc


def _boom():
    raise RuntimeError("vendor down")


def test_section_records_degradation():
    dc.clear_degradations()
    body = harvest._section("Ticker news", _boom, endpoint="stock_news_em")
    assert "_ERROR fetching this section" in body            # 原语义保留
    recs = dc.degradations()
    assert recs and recs[-1]["endpoint"] == "stock_news_em"  # 新:记了账


def test_section_without_endpoint_uses_slug():
    dc.clear_degradations()
    harvest._section("Global / macro news", _boom)
    assert dc.degradations()[-1]["endpoint"] == "analyze:global-macro-news"
