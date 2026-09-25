#!/usr/bin/env python3
"""六大指数白名单(六位代码 → 中文名)—— 调样事件覆盖范围的**跨包边界事实**(零 IO,不导入上层)。

2026-09-25(index-rebalance-events 波,Task 13/14 收尾)从 `scan/index_events.py` 下沉。搬的理由
同 `strategist_view.py`:这份名单不只是 `scan` 一家的私有细节——`analyze/index_membership.py`
(stock-research full 档的确定性「指数成分/调样事件」行,替代原来的 WebSearch 兜底)同样要读它,
而 `analyze` 在分层棘轮(`tests/contracts/test_layering.py`)里排在 `scan` **之下**,下层 import
上层是被 ratchet 挡死的一条新边(`test_no_new_upward_edges`)。让两个包各自 import 同一份
`contracts` 声明,而不是让下层为了一张白名单去 import 上层。

事件表构造/相位判定等**行为**仍在 `scan/index_events.py`(它 import 本模块,同一个对象,值不变);
`scan/index_flow.py`(ETF 规模 → flow_adv_days)沿用同包引用不受影响。
"""
from __future__ import annotations

INDEX_WHITELIST: dict[str, str] = {
    "000300": "沪深300", "000905": "中证500", "000852": "中证1000",
    "000510": "中证A500", "000688": "科创50", "399006": "创业板指",
}
