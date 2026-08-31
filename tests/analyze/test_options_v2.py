"""options v2 渲染块的验收(设计稿 2026-08-28 §5.2 / §11 的 D-3 行)。

覆盖四条硬纪律各一条探针 + 两个档位门:

1. **禁止退回 `lastPrice`**:crossed / stale 一腿 → `UNMEASURED`(链里那条 `lastPrice=99`
   若被当成跨式腿,隐含波动会变成 ±99% —— 探针就是拿它做诱饵)。
2. **AMC / BMO 锚必须对齐**:AMC 用 D close→D+1 open/close;BMO 用 D−1 close→D open/close;
   时段未知**不纳入**。
3. **IV 分位只比同 `method_version`**:口径不同的观测一条都不进这条历史。
4. **不含方向措辞**:期权块只描述量级与仓位结构,不得出现看涨 / 看跌 / bullish / bearish。

外加:无挂牌期权 → 保留 v1 降级文案;**slim / LITE 档整块不出现**。

全部离线:链 / 日线 / 财报 / 分位观测全用注入桩,**零真网络**。
"""
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from autoresearch.analyze import harvest
from autoresearch.data.sources import yf_options as yfo

AS_OF = "2026-08-28"
NOW = datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc)
SPOT = 200.0
NEAR_EXP = "2026-09-11"      # 14 DTE
FAR_EXP = "2026-10-16"       # 49 DTE —— 包围 30D 的另一条腿,也是财报后首个到期
EARN_DATE = "2026-09-15"


def _legs(strikes, *, iv=0.30, bid=5.0, ask=5.4, age_s=60, oi=500, special=None):
    """一条腿的链帧;`special = {strike: {字段覆盖}}` 用来单独把某个执行价做坏。"""
    rows = []
    for k in strikes:
        r = {"strike": float(k), "impliedVolatility": iv, "bid": bid, "ask": ask,
             "lastPrice": 99.0,           # ← 诱饵:退回 lastPrice 就会算出 ±99%
             "openInterest": oi,
             "lastTradeDate": NOW - timedelta(seconds=age_s)}
        r.update((special or {}).get(k, {}))
        rows.append(r)
    return pd.DataFrame(rows)


class _Client:
    """`yf_options` 的注入 client(duck-type,见该模块 docstring「注入点」)。"""

    def __init__(self, chains, *, spot=SPOT):
        self._chains = chains
        self._spot = spot

    def expirations(self):
        return sorted(self._chains)

    def option_chain(self, expiry):
        return self._chains[expiry]

    def spot(self):
        return self._spot, AS_OF

    def daily_closes(self, lookback: int = 25):
        return pd.Series([100.0 + i * 0.5 for i in range(lookback + 2)])

    def market_state(self):
        return "CLOSED"


STRIKES = [180, 190, 200, 210, 220]


def _chains(*, call_special=None, put_special=None):
    good = (_legs(STRIKES), _legs(STRIKES))
    far = (_legs(STRIKES, special=call_special), _legs(STRIKES, special=put_special))
    return {NEAR_EXP: good, FAR_EXP: far}


def _full(chains):
    return yfo.full_chain("NVDA", AS_OF, earnings_dt={"date": EARN_DATE, "session": "AMC"},
                          client=_Client(chains), now=NOW, record=False)


EARN = {"date": EARN_DATE, "session": "AMC", "time_quality": "TIMED"}
REALIZED_STUB = {"rows": [], "n": 0, "median_abs_cc_pct": None, "median_abs_co_pct": None,
                 "skipped_unknown_session": 0, "skipped_no_bars": 0,
                 "status": "UNMEASURED", "reason": "桩:不测历史"}


# ───────────────────────── 纪律 2:crossed / stale 一腿 → UNMEASURED ─────────────────────────


@pytest.mark.unit
def test_crossed_call_leg_is_unmeasured_and_never_falls_back_to_last_price():
    chain = _full(_chains(call_special={200: {"bid": 6.0, "ask": 5.0}}))   # crossed
    eim = chain["earnings_implied_move"]
    assert eim["status"] == "UNMEASURED"
    assert eim["implied_move_pct"] is None            # **不是 0,也不是 lastPrice 折出来的数**
    assert "crossed" in eim["reason"]
    assert "lastPrice" in eim["reason"]

    md = harvest.options_block("NVDA", AS_OF, chain=chain, earn=EARN, realized=REALIZED_STUB)
    assert "市场定价 UNMEASURED" in md
    assert "±99" not in md                            # 诱饵没被吃掉
    assert "禁用 lastPrice" in md


@pytest.mark.unit
def test_stale_put_leg_is_unmeasured():
    stale_age = yfo.MAX_QUOTE_AGE_SEC + 3600
    chain = _full(_chains(put_special={200: {"lastTradeDate": NOW - timedelta(seconds=stale_age)}}))
    eim = chain["earnings_implied_move"]
    assert eim["status"] == "UNMEASURED"
    assert eim["implied_move_pct"] is None
    assert "stale" in eim["reason"]


@pytest.mark.unit
def test_clean_chain_measures_implied_move_from_bid_ask_midpoint():
    """反面对照(没有它,上面两条测试对「永远 UNMEASURED」的实现也会绿)。"""
    chain = _full(_chains())
    eim = chain["earnings_implied_move"]
    assert eim["status"] == "ok"
    # 两腿 mid 各 (5.0+5.4)/2 = 5.2 → 跨式 10.4 / 200 = 5.2%(**不是** lastPrice 的 99)
    assert eim["straddle"] == pytest.approx(10.4)
    assert eim["implied_move_pct"] == pytest.approx(5.2)
    md = harvest.options_block("NVDA", AS_OF, chain=chain, earn=EARN, realized=REALIZED_STUB)
    assert "市场定价 ±5.20%" in md


# ───────────────────────── 纪律 4:AMC / BMO 两种锚 ─────────────────────────


def _bars(rows):
    idx = [r[0] for r in rows]
    return pd.DataFrame({"Open": [r[1] for r in rows], "Close": [r[2] for r in rows]}, index=idx)


@pytest.mark.unit
def test_amc_anchor_uses_d_close_to_next_day():
    bars = _bars([("2026-08-25", 100.0, 100.0),
                  ("2026-08-26", 100.0, 100.0),      # ← AMC 财报日 D
                  ("2026-08-27", 110.0, 105.0)])     # ← D+1
    out = harvest.earnings_realized_moves(
        [{"date": "2026-08-26", "session": "AMC"}], bars)
    assert out["n"] == 1
    row = out["rows"][0]
    assert row["anchor"] == "D close→D+1 open/close"
    assert row["base_date"] == "2026-08-26" and row["move_date"] == "2026-08-27"
    assert row["close_to_open_pct"] == pytest.approx(10.0)
    assert row["close_to_close_pct"] == pytest.approx(5.0)
    assert out["median_abs_cc_pct"] == pytest.approx(5.0)


@pytest.mark.unit
def test_bmo_anchor_uses_prev_close_to_same_day():
    bars = _bars([("2026-05-19", 205.0, 200.0),      # ← D−1
                  ("2026-05-20", 190.0, 180.0),      # ← BMO 财报日 D
                  ("2026-05-21", 181.0, 182.0)])
    out = harvest.earnings_realized_moves(
        [{"date": "2026-05-20", "session": "BMO"}], bars)
    assert out["n"] == 1
    row = out["rows"][0]
    assert row["anchor"] == "D−1 close→D open/close"
    assert row["base_date"] == "2026-05-19" and row["move_date"] == "2026-05-20"
    assert row["close_to_open_pct"] == pytest.approx(-5.0)
    assert row["close_to_close_pct"] == pytest.approx(-10.0)


@pytest.mark.unit
def test_unknown_session_is_excluded_not_guessed():
    bars = _bars([("2026-08-25", 100.0, 100.0), ("2026-08-26", 100.0, 100.0),
                  ("2026-08-27", 110.0, 105.0)])
    out = harvest.earnings_realized_moves([{"date": "2026-08-26", "session": None}], bars)
    assert out["n"] == 0 and out["skipped_unknown_session"] == 1
    assert out["status"] == "UNMEASURED"
    assert "UNMEASURED 不是 0" in out["reason"]


@pytest.mark.unit
def test_et_session_never_guesses_from_a_midnight_placeholder():
    ny = "America/New_York"
    assert harvest._et_session(pd.Timestamp("2026-08-26 16:00", tz=ny)) == "AMC"
    assert harvest._et_session(pd.Timestamp("2026-08-26 07:00", tz=ny)) == "BMO"
    assert harvest._et_session(pd.Timestamp("2026-08-26 00:00", tz=ny)) is None   # 只有日期
    assert harvest._et_session(pd.Timestamp("2026-08-26 12:00", tz=ny)) is None   # 盘中


@pytest.mark.unit
def test_render_puts_both_sides_of_the_comparison_in_one_sentence():
    bars = _bars([("2026-08-25", 100.0, 100.0), ("2026-08-26", 100.0, 100.0),
                  ("2026-08-27", 110.0, 107.0)])
    realized = harvest.earnings_realized_moves([{"date": "2026-08-26", "session": "AMC"}], bars)
    md = harvest.options_block("NVDA", AS_OF, chain=_full(_chains()), earn=EARN,
                               realized=realized)
    assert "**市场定价 ±5.20% vs 过去 1 次中位 ±7.00%**" in md


# ───────────────────────── 纪律 4:IV 分位只比同 method_version ─────────────────────────


def _obs(n, *, mv, iv=30.0):
    return [{"atm_iv_30d": iv + i * 0.1, "method_version": mv, "tenor_days": yfo.TARGET_DTE,
             "session_complete": True} for i in range(n)]


@pytest.mark.unit
def test_iv_percentile_rejects_mixed_method_version():
    chain = _full(_chains())
    mixed = _obs(60, mv="options_v1.9")           # 口径不同 → 一条都不算数
    cell = harvest._iv_percentile_row(chain, mixed)
    assert "UNMEASURED" in cell and "样本不足(0)" in cell

    same = _obs(yfo.IV_PCTILE_MIN_OBS, mv=chain["method_version"])
    ok_cell = harvest._iv_percentile_row(chain, same)
    assert "UNMEASURED" not in ok_cell and f"n={yfo.IV_PCTILE_MIN_OBS}" in ok_cell


@pytest.mark.unit
def test_iv_percentile_row_renders_unmeasured_not_zero_when_history_empty():
    cell = harvest._iv_percentile_row(_full(_chains()), [])
    assert "UNMEASURED" in cell and "样本不足(0)" in cell
    assert not cell.startswith("0")


# ───────────────────────── 纪律 3:渲染块零方向措辞 ─────────────────────────


@pytest.mark.unit
def test_rendered_block_has_no_direction_words():
    md = harvest.options_block("NVDA", AS_OF, chain=_full(_chains()), earn=EARN,
                               realized=REALIZED_STUB)
    for bad in ("看涨", "看跌", "bullish", "bearish"):
        assert bad not in md.lower() and bad not in md
    for bad in yfo.FORBIDDEN_DIRECTION_WORDS:       # 源模块的禁词表 = 同一把尺
        assert bad.lower() not in md.lower(), f"期权块出现方向措辞:{bad}"
    assert "不得由期权推出评级" in md
    assert "不是方向" in md


@pytest.mark.unit
def test_pcr_and_skew_carry_structure_only_semantics():
    md = harvest.options_block("NVDA", AS_OF, chain=_full(_chains()), earn=EARN,
                               realized=REALIZED_STUB)
    assert "对冲" in md and "持仓压力" in md          # PCR 语义原样带出
    assert "仓位结构" in md                           # 偏度只描述结构
    assert "25Δ" in md                                # 无 Greeks 的自曝


# ───────────────────────── 降级 / 档位门 ─────────────────────────


@pytest.mark.unit
def test_no_listed_options_keeps_the_v1_degrade_sentence():
    chain = yfo.full_chain("600519.SS", AS_OF, client=_Client({}), now=NOW, record=False)
    md = harvest.options_block("600519.SS", AS_OF, chain=chain, earn=EARN, realized=REALIZED_STUB)
    assert md == harvest.NO_OPTIONS_DEGRADE
    assert "无挂牌期权" in md and "催化剂&定位分析师需注明降级" in md


@pytest.mark.unit
def test_unmeasured_30d_iv_is_shown_with_reason_not_zero():
    """只有一个到期(不包围 30D)→ 插值 UNMEASURED,渲染必须带原因、不得写 0。"""
    only_near = {NEAR_EXP: (_legs(STRIKES), _legs(STRIKES))}
    chain = yfo.full_chain("NVDA", AS_OF, client=_Client(only_near), now=NOW, record=False)
    md = harvest.options_block("NVDA", AS_OF, chain=chain, earn=EARN, realized=REALIZED_STUB)
    assert "30D ATM IV: UNMEASURED" in md
    assert "无包围 30D 的有效到期" in md
    assert "30D ATM IV: 0" not in md


@pytest.mark.unit
def test_slim_lite_never_gets_the_options_block():
    titles_slim = [t[0] for t in harvest.external_sections("NVDA", AS_OF, slim=True)]
    assert titles_slim == []                                   # slim 档整档关闭
    titles_full = [t[0] for t in harvest.external_sections("NVDA", AS_OF, slim=False)]
    assert harvest._TITLE_OPTIONS in titles_full               # 反面对照:full 档才有
    assert harvest._TITLE_OPTIONS not in titles_slim


@pytest.mark.unit
def test_external_section_plan_splits_us_and_ashare():
    us = [t[0] for t in harvest.external_sections("NVDA", AS_OF, slim=False)]
    cn = [t[0] for t in harvest.external_sections("600519.SS", AS_OF, slim=False)]
    assert harvest._TITLE_EDGAR in us and harvest._TITLE_GNEWS_EN in us
    assert harvest._TITLE_ANALYST_ACTIONS in us
    assert harvest._TITLE_READTHROUGH not in us
    assert harvest._TITLE_READTHROUGH in cn and harvest._TITLE_GNEWS_ZH in cn
    assert harvest._TITLE_EDGAR not in cn                      # A 股没有 EDGAR


# ───────────────── 美股确定层新块:presence-gated + B 级降级(失败不抛)─────────────────


@pytest.mark.unit
def test_edgar_block_separates_legit_empty_from_failure():
    from autoresearch.data.sources.edgar import _FILING_COLS, FetchOutcome

    empty = pd.DataFrame(columns=list(_FILING_COLS))
    assert harvest.edgar_block("600519.SS", AS_OF,
                               outcome=FetchOutcome(empty, "NO_CIK", "非美股")) is None
    legit = harvest.edgar_block("NVDA", AS_OF, outcome=FetchOutcome(empty, "EMPTY"))
    failed = harvest.edgar_block("NVDA", AS_OF, outcome=FetchOutcome(empty, "FAILED", "UA 未设置"))
    assert "0 条申报" in legit and "合法空" in legit
    assert "EDGAR 取数失败" not in legit                       # 两种空写法必须分得开
    assert "EDGAR 取数失败" in failed and "UA 未设置" in failed
    rows = pd.DataFrame([{"form": "10-Q", "filing_date": "2026-08-26",
                          "report_date": "2026-07-31", "accession": "a",
                          "primary_document": "b",
                          "acceptance_datetime": "2026-08-26T16:31:00-04:00",
                          "items": "", "url": "https://www.sec.gov/a"}],
                        columns=list(_FILING_COLS))
    ok = harvest.edgar_block("NVDA", AS_OF, outcome=FetchOutcome(rows, "OK"))
    assert "10-Q" in ok and "https://www.sec.gov/a" in ok


@pytest.mark.unit
def test_analyst_actions_narrow_to_window_and_unmeasured_range_change():
    acts = pd.DataFrame(
        [{"Firm": "Firm A", "ToGrade": "Buy", "FromGrade": "Neutral", "Action": "up"},
         {"Firm": "Firm B", "ToGrade": "Hold", "FromGrade": "Buy", "Action": "down"}],
        index=pd.to_datetime(["2026-08-20", "2026-01-05"]))
    tg = {"targetLowPrice": 180, "targetMeanPrice": 305.8, "targetMedianPrice": 300,
          "targetHighPrice": 420, "numberOfAnalystOpinions": 58}
    md = harvest.analyst_actions_block("NVDA", AS_OF, actions=acts, targets=tg)
    assert "Firm A" in md and "Firm B" not in md          # 30 日窗外的被收窄掉
    assert "180.00 – 420.00" in md
    assert "区间变化: **UNMEASURED**" in md and "不是「没变」" in md
    md2 = harvest.analyst_actions_block("NVDA", AS_OF, actions=acts, targets=tg,
                                        prev_targets={"as_of": "2026-07-28",
                                                      "targetLowPrice": 170,
                                                      "targetHighPrice": 400,
                                                      "targetMeanPrice": 300.0})
    assert "UNMEASURED" not in md2 and "+10.00" in md2 and "+20.00" in md2


@pytest.mark.unit
def test_analyst_block_is_presence_gated_for_non_us():
    empty_targets = dict.fromkeys(("targetLowPrice", "targetMeanPrice", "targetMedianPrice",
                                   "targetHighPrice", "numberOfAnalystOpinions"))
    assert harvest.analyst_actions_block("600519.SS", AS_OF, actions=pd.DataFrame(),
                                         targets=empty_targets) is None


@pytest.mark.unit
def test_gnews_block_is_t4_unverified_windowed_and_presence_gated():
    items = [
        {"title": "in window", "url": "https://news.google.com/x",
         "published_ts": "2026-08-27T10:00:00Z", "source_name": "Reuters"},
        {"title": "too old", "url": "https://news.google.com/y",
         "published_ts": "2026-01-01T10:00:00Z", "source_name": "X"},
        {"title": "lookahead", "url": "https://news.google.com/z",
         "published_ts": "2026-09-30T10:00:00Z", "source_name": "Y"},
    ]
    md = harvest.gnews_block("NVIDIA", AS_OF, lang="en", items=items)
    assert "in window" in md and "too old" not in md and "lookahead" not in md
    assert "T4" in md and "UNVERIFIED" in md and "须跟到原文" in md
    assert harvest.gnews_block("NVIDIA", AS_OF, lang="en", items=[]) is None


@pytest.mark.unit
def test_external_block_failure_never_kills_the_harvest():
    def _boom(*_a, **_k):
        raise RuntimeError("源炸了")

    sec = harvest._opt_section(harvest._TITLE_EDGAR, _boom, "NVDA", AS_OF)
    assert harvest._TITLE_EDGAR in sec and "ERROR fetching this section" in sec
