from autoresearch.common.failclass import fail_class


def test_hygiene_prefix():
    assert fail_class("产物形状·退役符号指令性引用") == "hygiene"
    assert fail_class("产物形状·旧尺裸写") == "hygiene"


def test_metering_prefix():
    assert fail_class("usage_reconcile·配置-实测不符") == "metering"


def test_unknown_defaults_to_data():
    # fail-safe:未登记的检查一律按 data 连坐;想豁免必须显式进 EXEMPT_PREFIXES
    assert fail_class("价格断言与OHLCV不符") == "data"
    assert fail_class("brief③生产BUY行与决策文件不符") == "data"
    assert fail_class("") == "data"
    assert fail_class(None) == "data"
