# tests/scan/test_price_claims.py
from autoresearch.scan.price_claims import (
    audit_card_text,
    extract_price_claims,
    reconcile_claims,
)

NAME, CODE = "协创数据", "300857"


def test_extract_pct_claim_with_own_name():
    text = "协创数据 7-21 放量上涨 11.4%,量比 1.9。"
    claims = extract_price_claims(text, name=NAME, code6=CODE, year_hint=2026)
    assert len(claims) == 1
    c = claims[0]
    assert c["date"] == "20260721" and c["kind"] == "pct" and abs(c["value"] - 11.4) < 1e-9


def test_extract_limit_claim():
    text = "本股 07-15 涨停,随后三日回落。"
    claims = extract_price_claims(text, name=NAME, code6=CODE, year_hint=2026)
    assert len(claims) == 1 and claims[0]["kind"] == "limit" and claims[0]["date"] == "20260715"


def test_extract_skips_unattributed_index_sentence():
    # 句内没有本票名称/代码/本股指代 → 不认领(科创50 的涨幅不是本票断言)
    text = "7-21 工信部算力标准催化,科创50 单日 +10%。"
    assert extract_price_claims(text, name=NAME, code6=CODE, year_hint=2026) == []


def test_extract_skips_dateless():
    assert extract_price_claims("协创数据近期上涨 30%。", name=NAME, code6=CODE, year_hint=2026) == []


def test_reconcile_within_tolerance_passes():
    claims = [{"date": "20260721", "kind": "pct", "value": 11.4, "snippet": "s"}]
    assert reconcile_claims(claims, {"20260721": 11.42}, code6=CODE) == []


def test_reconcile_mismatch_caught():
    claims = [{"date": "20260721", "kind": "pct", "value": 5.3, "snippet": "s"}]
    out = reconcile_claims(claims, {"20260721": 1.2}, code6=CODE)
    assert len(out) == 1 and out[0]["claimed"] == 5.3 and out[0]["actual"] == 1.2


def test_reconcile_limit_board_aware():
    # 300 开头 20cm 板:实涨 19.99% 算涨停成立;9.98% 不算
    ok = [{"date": "20260715", "kind": "limit", "value": None, "snippet": "s"}]
    assert reconcile_claims(ok, {"20260715": 19.99}, code6="300857") == []
    assert len(reconcile_claims(ok, {"20260715": 9.98}, code6="300857")) == 1
    # 600 开头 10cm 板:9.98% 即成立
    assert reconcile_claims(ok, {"20260715": 9.98}, code6="600350") == []


def test_reconcile_nodata_skipped():
    claims = [{"date": "20260719", "kind": "pct", "value": 5.0, "snippet": "s"}]  # 周六,无 bar
    assert reconcile_claims(claims, {}, code6=CODE) == []


def test_audit_card_text_injectable_bars():
    text = "协创数据 07-21 大涨 11.4%;本股 07-15 涨停。"
    res = audit_card_text(text, name=NAME, code6=CODE, date="2026-07-21",
                          bars_fn=lambda c, ds, today: {"20260721": 1.0, "20260715": 19.99})
    assert res["n_claims"] == 2
    assert len(res["mismatches"]) == 1 and res["mismatches"][0]["date"] == "20260721"


def test_extract_direction_from_verb():
    claims = extract_price_claims("协创数据 07-16 下跌 11.4%。", name=NAME, code6=CODE, year_hint=2026)
    assert len(claims) == 1 and claims[0]["value"] == -11.4


def test_reconcile_direction_mismatch():
    claims = [{"date": "20260721", "kind": "pct", "value": 11.4, "snippet": "s"}]
    assert len(reconcile_claims(claims, {"20260721": -11.4}, code6=CODE)) == 1


def test_limit_claims_carry_direction():
    up = extract_price_claims("本股 07-15 涨停。", name=NAME, code6=CODE, year_hint=2026)[0]
    down = extract_price_claims("本股 07-15 跌停。", name=NAME, code6=CODE, year_hint=2026)[0]
    assert up["dir"] == 1 and down["dir"] == -1
    # 涨停断言撞上真实跌停日 → 必须抓出
    assert len(reconcile_claims([up], {"20260715": -19.99}, code6="300857")) == 1
    assert reconcile_claims([up], {"20260715": 19.99}, code6="300857") == []
    assert reconcile_claims([down], {"20260715": -19.99}, code6="300857") == []


def test_extract_skips_range_phrase():
    assert extract_price_claims("本股 预计有 5-10% 的下行空间,继续观察。",
                                name=NAME, code6=CODE, year_hint=2026) == []


def test_audit_bad_date_noop():
    assert audit_card_text("协创数据 07-21 大涨 11.4%。", name=NAME, code6=CODE,
                           date="", bars_fn=lambda c, d, t: {}) == {"n_claims": 0, "mismatches": []}


# ── C-1 修(2026-07-23 终审):三条真卡假阳向量,逐字用原文,各自不产错误断言 ──

def test_extract_skips_cumulative_range_move():
    """向量1(华海 600521 卡第17行,逐字):`6/09 14.64→7/16 17.63(+20%)` 是 A日→B日 累计涨幅,
    抽取器旧行为把区间起点 6/09 + 累计 +20% 当单日 → 假不符。区间标记 → 在日期与%间 ⇒ 弃。"""
    line = ("| 前提2(兑现机制):医药超卖修复扩散买盘接力 | ✗ | 本股 6/09 14.64→7/16 17.63"
            "(+20%)非超卖;7/20 医药生物净流出 41.32 亿,扩散资金反向 |")
    assert extract_price_claims(line, name="华海药业", code6="600521", year_hint=2026) == []


def test_extract_forward_scenario_dropped_realized_kept():
    """向量2(普冉 688766 卡第43行,逐字):同句里 `延续至 510(+8.5%)` 是 Bull 情景目标(弃),
    `7/21 单日已实测 +17.5%` 是已实现单日移动(留)。绝不允许把前向 +8.5% 挂到 7/21。"""
    line = ("- Bull(25%):回购正式董事会决议落地 + 科创板资金续流入(7/21 净流入 91.95 亿),"
            "超跌反弹延续至 510(**+8.5%**)——超 📐p60(+3.7%)锚,**硬理由**:该股 ATR 86.72 = "
            "现价 18.5%,2 日波幅结构性数倍于全市场基率,7/21 单日已实测 +17.5%,"
            "用全市场 p60 度量本票会系统性低估振幅。")
    claims = extract_price_claims(line, name="普冉股份", code6="688766", year_hint=2026)
    assert len(claims) == 1, claims                       # 只留已实现那条,情景/目标 % 全弃
    c = claims[0]
    assert c["date"] == "20260721" and c["kind"] == "pct"
    assert abs(c["value"] - 17.5) < 1e-9                  # 是已实测 +17.5%,不是情景 +8.5%
    assert all(abs(x["value"] - 8.5) > 1e-9 for x in claims)  # +8.5% 绝不被认领


def test_extract_skips_fundamental_pct():
    """向量3(协创 300857,C-1 给的合成示例句):`营收同比上涨12%` 是基本面%非股价%——
    % 前后 8 字内有基本面名词(营收/同比)⇒ 弃(否则对账股价 +1.2% → 假不符)。"""
    assert extract_price_claims("协创数据 2026-07-15 营收同比上涨12%",
                                name=NAME, code6=CODE, year_hint=2026) == []


def test_extract_still_keeps_genuine_single_day_moves():
    """收紧后真断言仍被抽取(防过度收窄):放量上涨%、涨停都在场。"""
    up = extract_price_claims("协创数据 7-21 放量上涨 11.4%,量比 1.9。",
                              name=NAME, code6=CODE, year_hint=2026)
    assert len(up) == 1 and up[0]["date"] == "20260721" and abs(up[0]["value"] - 11.4) < 1e-9
    lim = extract_price_claims("本股 07-15 涨停,随后三日回落。",
                               name=NAME, code6=CODE, year_hint=2026)
    assert len(lim) == 1 and lim[0]["kind"] == "limit" and lim[0]["date"] == "20260715"


# ── R-1 修(round2 复核):同 C-1 类的「预告%/累计%」旁路,黑名单未关住 ──

def test_extract_skips_forecast_pct():
    assert extract_price_claims("本股 7-14 中报预告 +66%,兑现在望。",
                                name=NAME, code6=CODE, year_hint=2026) == []
    assert extract_price_claims("本股 7-14 中报预增 +105%。",
                                name=NAME, code6=CODE, year_hint=2026) == []


def test_extract_skips_cumulative_pct():
    assert extract_price_claims("本股 7-16 累计涨幅达 20%。",
                                name=NAME, code6=CODE, year_hint=2026) == []


def test_extract_skips_pct_tilde_range():
    assert extract_price_claims("协创数据 7-19 中报预告 +247%~+340%。",
                                name=NAME, code6=CODE, year_hint=2026) == []


# ── round3 立项(2026-07-23):指数名黑名单——句级自指(个股/本股)夹带指数名时,
#    指数自己的涨跌% 不该被记到本票头上 ──

def test_extract_skips_index_pct_in_long_sentence():
    # 07-21 协创真卡长句(、/,连接一个句号):句尾「个股」不得给句首科创50 的 +10% 背书
    text = ("实读确认了成长侧:7-19 中报预告 +247%~+340% 已落地、7-13 定增 80 亿过股东会、"
            "7-21 工信部算力标准催化 + 科创50 单日 +10% 半导体涨停潮,个股放量 +11.4%(量比 1.9)"
            "——催化与档案里「无带日期催化/深跌落刀」两条证伪点均已翻转。")
    claims = extract_price_claims(text, name=NAME, code6=CODE, year_hint=2026)
    assert [c["value"] for c in claims if c["kind"] == "pct"] == [11.4]  # 只留个股 +11.4,指数 +10 弃


def test_extract_skips_index_pct_variants():
    for idx in ("沪深300", "上证指数", "创业板指", "科创50", "北证50"):
        t = f"本股 7-21 随{idx} 上涨 3.2%。"
        assert extract_price_claims(t, name=NAME, code6=CODE, year_hint=2026) == [], idx


# ── W2-T1 复审 Important 修(2026-07-23):黑名单短条目撞本票名字(恒生电子/600570) ──

def test_extract_keeps_claim_for_stock_named_like_index():
    # 恒生电子(600570):名字含「恒生」,不得被指数黑名单误吞(W2-T1 复审 Important)
    for t in ("7-21 提质增效催化兑现,恒生电子放量上涨11.4%,系统集成订单加速。",
              "7-21 恒生电子大涨11.4%,系统集成需求回暖。"):
        claims = extract_price_claims(t, name="恒生电子", code6="600570", year_hint=2026)
        assert [c["value"] for c in claims] == [11.4], t


def test_extract_still_skips_real_index_pct():
    t = "本股随恒生指数 7-21 上涨 2.1%。"
    assert extract_price_claims(t, name="协创数据", code6="300857", year_hint=2026) == []


# ── 复核上一测试时顺带发现的第二处遮蔽 bug(与 _INDEX_NAMES 碰撞同族不同因,独立隔离验证):
#    _FUND「数字之后 8 字」窗跨逗号吃进下一个不相关小句的基本面词(如"订单"),把该小句
#    误判成给价格 % 定性,静默吞真实价格断言——与上面 test_extract_keeps_claim_for_stock_
#    named_like_index 的第一条向量(系统集成订单加速)是同一句,但此处证明诱因与指数名黑名单
#    完全无关(句中不含任何 _INDEX_NAMES 词) ──

def test_extract_fund_word_in_next_clause_not_excluded():
    t = "恒生电子 7-21 放量上涨11.4%,系统集成订单加速。"  # 不含任何指数名,隔离验证第二因
    claims = extract_price_claims(t, name="恒生电子", code6="600570", year_hint=2026)
    assert [c["value"] for c in claims] == [11.4]


def test_extract_fund_word_same_clause_still_excluded():
    # 防过度收窄:同一小句内(逗号前)紧跟基本面词仍须排除
    assert extract_price_claims("协创数据 7-21 上涨12%订单强劲。",
                                name=NAME, code6=CODE, year_hint=2026) == []


def test_extract_keeps_claim_after_short_index_lead_in():
    # 「隔夜美指弱,A股个股自身涨」的标准引句形;window=14 会误吞,13 不会(W2-T1 round2)
    for t in ("纳指弱,个股 7-21 涨 3.2%。", "标普弱,个股 7-21 涨 3.2%。"):
        claims = extract_price_claims(t, name="协创数据", code6="300857", year_hint=2026)
        assert [c["value"] for c in claims] == [3.2], t


def test_index_window_still_catches_hengsheng_distance():
    assert extract_price_claims("本股随恒生指数 7-21 上涨 2.1%。",
                                name="协创数据", code6="300857", year_hint=2026) == []


# ══ Wave7 B′-b:元话语句(转述/否决/负判)不认领 ══════════════════════════════
#
# 2026-07-27 实跑:self_review 的 price_claim_mismatch 报了 4 条,逐条核完 **4/4 是假阳**,
# 且其中两条卡片正在做我们要它做的事(拿 OHLCV 对账并否决 intel 的断言)。真捏造在 intel
# 稿里,由 assemble 发布层的 🔎 块另行对账(那一层工作正常)。下面四个向量是四张真卡的原句。


def test_refutation_sentence_not_claimed_600988():
    """卡片自己拿 OHLCV 否决 intel 的跌停断言 —— 却被判成卡片捏造(最讽刺的一例)。"""
    t = ('〔转引标题〕intel 载"美银警告黄金跌势远未结束,赤峰黄金跌停"(新浪财经·2026-07-17)'
         "——slim OHLCV 显示 7/17 为 34.62→31.74 = -8.3%,非跌停,intel 该价格断言未对账。")
    assert extract_price_claims(t, name="赤峰黄金", code6="600988", year_hint=2026) == []


def test_refutation_sentence_not_claimed_601211():
    """「intel 称 X … 未对账,不采信」= 卡片在拒绝采信,不是在断言。"""
    t = ("intel 称「07-27 证券板块 +4.82%、港股中资券商普涨」与本股 verified -1.4% "
         "**未对账**,不采信。")
    assert extract_price_claims(t, name="国泰海通", code6="601211", year_hint=2026) == []


def test_quoted_headline_sentence_not_claimed_601869():
    """〔转引标题〕里的日期 + 同句 fwd-EPS「隐含 +637%」—— 契约本就要求标记转引,审计器要认这个标记。"""
    t = ("转引标题(非本票行情自陈):〔转引标题〕《7分钟封住涨停!601869,1年涨超15倍》"
         "(华夏时报 2026-06-22)｜ **一致预期差**:fwd-EPS 7.81 对 TTM 实际 EPS 1.06 "
         "= 隐含 +637% 跃升。")
    assert extract_price_claims(t, name="长飞光纤", code6="601869", year_hint=2026) == []


def test_other_stock_limit_with_negation_not_claimed_601918():
    """涨停属他票(兖矿/中煤),且句意恰恰是本股「未见于」龙头名单 —— 不该记到本股头上。"""
    t = ("07-20 板块午后发力、兖矿/中煤涨停时「新集能源未见于当日龙头名单」"
         "(证券时报网 2026-07-20)。")
    assert extract_price_claims(t, name="新集能源", code6="601918", year_hint=2026) == []


def test_implied_eps_growth_is_not_a_price_claim():
    """独立防线:即使没有〔转引标题〕标记,「隐含 +637%」也不是价格断言(EPS 离数字超 8 字窗)。"""
    t = "长飞光纤 2026-06-22 fwd-EPS 7.81 对 TTM 实际 EPS 1.06 = 隐含 +637% 跃升。"
    assert extract_price_claims(t, name="长飞光纤", code6="601869", year_hint=2026) == []


# ── 反向保护:去假阳不能把真捏造一起放走(否则这刀就是把探针关了)──


def test_real_own_claim_still_extracted_alongside_meta_talk():
    """同一张卡里,元话语句被跳过,但另一句真正的自陈断言必须照常认领。"""
    t = ("intel 称「证券板块 +4.82%」与本股 verified 不采信。"
         "2026-07-27 金价跳空高开带动本股 +4.52%。")
    claims = extract_price_claims(t, name="赤峰黄金", code6="600988", year_hint=2026)
    assert [c["value"] for c in claims] == [4.52], "把真自陈也一起豁免了 = 探针被关掉"


def test_plain_fabricated_limit_still_caught():
    """不带任何转述/否决/负判标记的裸断言 —— 该报还得报。"""
    t = "本股 2026-07-17 涨停,量能配合。"
    claims = extract_price_claims(t, name="赤峰黄金", code6="600988", year_hint=2026)
    assert [(c["date"], c["kind"], c["dir"]) for c in claims] == [("20260717", "limit", 1)]


def test_reconcile_dedups_repeated_identical_claim():
    """同一断言在卡里复述三遍 → 只报一次(计数是读者判断严重性的依据,不能虚高)。"""
    claims = [{"date": "20260717", "kind": "limit", "value": None, "dir": 1, "snippet": "x"}] * 3
    bad = reconcile_claims(claims, {"20260717": -8.32}, code6="600988")
    assert len(bad) == 1
