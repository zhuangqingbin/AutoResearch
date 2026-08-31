"""Task 10(D1.1):harvest.py 拆五模块 + `(market,tier)` 派发表单一事实源。

`HARVEST_PLAN`/`BLOCKS` 是 SKILL.md:38 手抄清单的机器真身 —— 这两个测试锁住它的
形状契约(两档都在、slim 不拉 400 天 OHLCV、每个引用的块名都在 `BLOCKS` 里注册)。
"""
from autoresearch.analyze.harvest import BLOCKS, HARVEST_PLAN


def test_dispatch_table_covers_both_tiers():
    assert ("ashare", "slim") in HARVEST_PLAN and ("us", "full") in HARVEST_PLAN
    slim = HARVEST_PLAN[("ashare", "slim")]
    assert "price_history_400d" not in slim         # slim 不拉 400 天(既有语义)
    assert "verified_snapshot" in slim


def test_every_plan_block_is_registered():
    for blocks in HARVEST_PLAN.values():
        for b in blocks:
            assert b in BLOCKS, f"派发表引用未注册块 {b}"


def test_dispatch_table_covers_all_four_declared_markets():
    """market ∈ {"ashare","us","crypto","other"} × tier ∈ {"full","slim"} 全在场
    (type domain 不许撒谎)。"""
    for market in ("ashare", "us", "crypto", "other"):
        for tier in ("full", "slim"):
            assert (market, tier) in HARVEST_PLAN, f"缺 {(market, tier)}"


def test_non_ashare_markets_share_identical_content():
    """crypto/other 与 us 内容相同(现状即如此:main() 从不区分三者,见 harvest.py
    HARVEST_PLAN 注释)——声明齐全不是新增行为。"""
    assert HARVEST_PLAN[("crypto", "full")] == HARVEST_PLAN[("us", "full")]
    assert HARVEST_PLAN[("crypto", "slim")] == HARVEST_PLAN[("us", "slim")]
    assert HARVEST_PLAN[("other", "full")] == HARVEST_PLAN[("us", "full")]
    assert HARVEST_PLAN[("other", "slim")] == HARVEST_PLAN[("us", "slim")]


def test_slim_plans_never_include_not_slim_only_blocks():
    """既有语义:slim 不拉 400 天 OHLCV / 指标序列 / 外源扩面 / 资产负债表&现金流全表 /
    宏观8序列 / 预测市场 / 中国底色 / 同业相对估值 / 龙虎榜席位(uzi_seats 是"仅全量"块,
    不是"仅 slim"块——命名易混,单独断言排除)。"""
    not_slim_only = {
        "price_history_400d", "technical_indicators", "global_macro_news",
        "insider_transactions", "ownership_short", "macro_series", "china_backdrop",
        "prediction_markets", "balance_sheet", "cash_flow", "external_evidence",
        "peer_relative", "uzi_seats",
    }
    for tier, market in (("slim", "ashare"), ("slim", "us")):
        plan = set(HARVEST_PLAN[(market, tier)])
        leaked = plan & not_slim_only
        assert not leaked, f"{(market, tier)} 不该含 not_slim 专属块:{leaked}"


def test_ashare_only_blocks_absent_from_us_plans():
    ashare_only = {"ashare_market_context", "ashare_shareholder", "ashare_calendar",
                  "fwd_pe", "uzi_fundamentals", "uzi_margin", "uzi_trap", "uzi_volprice",
                  "uzi_seats", "china_backdrop"}
    for tier in ("full", "slim"):
        plan = set(HARVEST_PLAN[("us", tier)])
        leaked = plan & ashare_only
        assert not leaked, f"us/{tier} 不该含 A股专属块:{leaked}"


def test_verified_snapshot_precedes_fwd_pe_in_ashare_plans():
    """`fwd_pe` 复用 `verified_snapshot` 渲染出的 `ctx['snapshot_section']`(D1.6 #2,
    零二次网络往返)——派发顺序必须让前者先执行,否则读到的是初始空字符串。"""
    for tier in ("full", "slim"):
        plan = HARVEST_PLAN[("ashare", tier)]
        assert plan.index("verified_snapshot") < plan.index("fwd_pe")
