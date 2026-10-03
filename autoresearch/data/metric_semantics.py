"""Source field meanings; canonical numeric aliases retain their historical names."""
from types import MappingProxyType


def _definition(display_name, source_fields, canonical_fields):
    return MappingProxyType({
        "display_name": display_name,
        "unit": "万元",
        "source_fields": source_fields,
        "canonical_fields": canonical_fields,
        "canonical_unit": "亿元",
        "identity_evidence": False,
        "supports": ("该统计口径下的净额和时间变化",),
        "does_not_establish": ("机构身份", "持续吸筹", "隔夜正收益"),
        "source_ref": "https://tushare.pro/document/2?doc_id=170",
    })


METRICS = MappingProxyType({
    "tushare.moneyflow.net_mf_amount": _definition(
        "主动买卖单净流入", ("net_mf_amount",), ("main_inflow_yi",),
    ),
    "tushare.moneyflow.large_order_net": _definition(
        "大单及特大单净额",
        ("buy_lg_amount", "buy_elg_amount", "sell_lg_amount", "sell_elg_amount"),
        ("main_net_yi",),
    ),
    "tushare.moneyflow.small_order_net": _definition(
        "小单净额", ("buy_sm_amount", "sell_sm_amount"), ("retail_net_yi",),
    ),
})
