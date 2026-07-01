from autoresearch.scan.market import market_context_block

_PACK = {
    "regime": {"label": "risk_off", "breadth": 0.27, "med_mom": -13.0, "n": 4000},
    "breadth": {"above_ma60": 0.27, "med_pct_60d": -13.0, "falling_knife": 0.42, "up_60d": 0.19},
    "valuation": {"med_pe": 34.0, "med_pb": 2.1, "pe_top_decile": 137.0, "pe_gt_60": 0.18},
    "money": {"main_pos": 0.28, "med_main_ratio": -0.01, "cmf_pos": 0.31},
    "sectors": {"red": [{"industry": "半导体", "median_pct_60d": 114.1}],
                "black": [{"industry": "汽车零部件", "median_pct_60d": -25.3}]},
}


def test_block_describes_regime_and_sectors():
    b = market_context_block(_PACK)
    assert "避险" in b and "半导体" in b and "汽车零部件" in b


def test_block_has_no_directives():
    b = market_context_block(_PACK)
    for bad in ("买入", "卖出", "仓位", "操作建议", "0 买"):
        assert bad not in b
    assert "个股评级只由本股 rubric 三门决定" in b       # 反锚定护栏文案


def test_block_sector_rank_when_industry_given():
    b = market_context_block(_PACK, industry="半导体")
    assert "本股所在板块" in b and "强势" in b


def test_empty_pack_safe():
    assert isinstance(market_context_block({}), str)
