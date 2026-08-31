#!/usr/bin/env python3
"""外源实时信息扩面 · 数据层(design 2026-08-28-external-evidence-expansion §5.2/§5.3/§9)。

覆盖三个源模块(`yf_tape` / `yf_options` / `cboe_vix`)与它们的 9 个端点登记。**全程 monkeypatch,
零真网络**:三个模块各自的注入点(`history=` / `client=` / `fetch=`)是唯一入口,另加
`AUTORESEARCH_OFFLINE=1` 兜底 —— 万一某条路径漏了注入,它会**抛**而不是偷偷去打网络。

本文件锁死的命题(每条都对应 §9 湖纪律或一次真事故形态):

1. **「源成功但真空」≠「请求/解析失败」**。两者都落空数组 = 把「今天没有数据」和「我们没拿到
   数据」混进同一个下水道,而后者重跑能救、前者不能。
2. **UNMEASURED 不是 0**,且**禁止退回 `lastPrice`**(v1 原病:三周前一笔 1 张的成交冒充报价)。
3. **分位不足样本不给数** —— 12 个观测的「分位」和 250 个观测的长得一模一样,给了比不给更坏。
4. **口径版本混装即拒**:不同 `method_version` / tenor / 半截 session 串起来的不是历史。
5. **PCR 只描述对冲/持仓压力,不写方向**(08-24 普查裁定,别重开旧案)。
6. **写湖一律剥 `fields`**(窄表毒化前科)+ 快照端点 as_of 必须等于真实今天(PIT 错标)。
7. **时效三态**:fresh / stale-allowed / max-stale —— 超过 `max_stale` **整块省略**,不沿用旧值。
"""
from __future__ import annotations

import inspect
import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from autoresearch.data import cache, contracts, endpoints
from autoresearch.data.sources import cboe_vix, yf_options, yf_tape

# ───────────────────────── 公共夹具 ─────────────────────────

NINE_ENDPOINTS: tuple[str, ...] = (
    "global_tape", "us_options", "us_ticker", "cboe_vix", "fred_calendar",
    "fomc_calendar", "official_event_calendar", "edgar", "us_earnings_dates",
)
SNAPSHOT_ENDPOINTS: tuple[str, ...] = ("us_options", "fomc_calendar", "official_event_calendar")

_TODAY = "20260828"
# 2026-08-28 20:00 UTC = 16:00 EDT(美股收盘落定)= 08-29 04:00 上海。夏令时用 IANA 现算,
# 不写死偏移(§2 明令)。
NOW = datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc)
AS_OF = "2026-08-28"
_FRESH_TS = "2026-08-28 19:00:00+00:00"                      # age 1h < MAX_QUOTE_AGE_SEC
_STALE_TS = "2026-08-25 19:00:00+00:00"                      # age 3 天 > MAX_QUOTE_AGE_SEC


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """兜底:任何漏掉注入的默认路径都会**抛**,不会偷偷去打网络。"""
    monkeypatch.setenv("AUTORESEARCH_OFFLINE", "1")


@pytest.fixture(autouse=True)
def _clean_degradations():
    contracts.clear_degradations()
    yield
    contracts.clear_degradations()


@pytest.fixture
def lake(tmp_path, monkeypatch):
    root = tmp_path / "lake"
    monkeypatch.setattr(cache, "LAKE", root)
    return root


@pytest.fixture
def wall_clock(monkeypatch):
    """把「真实今天」钉死 —— 快照守门拿它比对 as-of 键,不能靠机器日历跑测试。"""
    monkeypatch.setattr(cache, "_real_today", lambda: _TODAY)


def _hist(last: str = "2026-08-28", n: int = 30, start: float = 100.0,
          step: float = 1.0) -> pd.DataFrame:
    """yfinance 风格日线(Date/Close),末根 = `last`,升序。"""
    end = pd.Timestamp(last)
    dates = [str((end - pd.Timedelta(days=n - 1 - i)).date()) for i in range(n)]
    return pd.DataFrame({"Date": dates, "Close": [start + step * i for i in range(n)]})


def _degraded_endpoints() -> list[str]:
    return [r["endpoint"] for r in contracts.degradations()]


# ═════════════════════ 任务 1:9 个端点的登记(名字必须与源模块对齐) ═════════════════════


def test_nine_endpoints_registered_with_expected_policy():
    """登记名 = 源模块真正用的字符串;policy 的 key/settle/source/snapshot 逐个对齐 §9 表。"""
    expected = {
        "global_tape": ("date", "yfinance", False),
        "us_options": ("as_of", "yfinance", True),
        "us_ticker": ("as_of", "yfinance", False),
        "cboe_vix": ("date", "cboe", False),
        "fred_calendar": ("date", "fred", False),
        "fomc_calendar": ("as_of", "official", True),
        "official_event_calendar": ("as_of", "official", True),
        "edgar": ("as_of", "sec", False),
        "us_earnings_dates": ("as_of", "yfinance", False),
    }
    for name, (key, source, snap) in expected.items():
        pol = endpoints.policy(name)
        assert (pol["key"], pol["source"], pol.get("snapshot", False)) == (key, source, snap), name
        assert pol["settle"] == "eod", name


@pytest.mark.parametrize("ep", NINE_ENDPOINTS)
def test_nine_endpoints_are_tier_b_with_presence_gated_note(ep):
    """全部 B 级:缺失只降级不阻断(I 类基建;消费者 presence-gated)。"""
    con = contracts.CONTRACTS[ep]
    assert con.tier == contracts.TIER_DEGRADE, ep


@pytest.mark.parametrize("ep", NINE_ENDPOINTS)
def test_nine_endpoints_declare_freshness_contract(ep):
    """§9 逐字:每个 endpoint 都要声明 `freshness_slo` / `max_stale` / `stale_on_error`。"""
    f = endpoints.freshness(ep)
    assert set(f) == set(endpoints.FRESHNESS_KEYS)
    assert endpoints.declares_freshness(ep), f"{ep}: 时效契约缺键 {f}"


def test_endpoint_names_match_what_the_source_modules_actually_use():
    """**名字对不上 = 白登记**:四个带 `ENDPOINT` 常量的模块直接比对。

    另三个(tape / options / vix)的字面量藏在函数体里,由下面的活体用例证明
    (湖路径落在 `lake/global_tape`、`lake/cboe_vix`,降级记在 `us_options` 名下)。
    """
    from autoresearch.data.sources import edgar, fomc_calendar, fred_calendar
    from autoresearch.data.sources import official_event_calendar as oec

    for mod in (edgar, fomc_calendar, fred_calendar, oec):
        assert mod.ENDPOINT in endpoints.ENDPOINTS, mod.__name__
        assert mod.ENDPOINT in contracts.CONTRACTS, mod.__name__


def test_new_sources_are_label_only_not_fetch_routes():
    """`cboe`/`official`/`sec` 没有 `sources.fetch` 分支 —— 真调必须**炸**而不是静默返回空帧。

    这是刻意的(它们的 load_* 总是注入 `fetch=`)。锁住它:哪天有人给这三个 source 加了半吊子
    路由,这条会红,逼他把 `load_*` 的注入契约一起想清楚。
    """
    from autoresearch.data import sources

    for ep in ("cboe_vix", "fomc_calendar", "edgar"):
        with pytest.raises(ValueError, match="unknown source"):
            sources.fetch(ep, {})


# ═════════════════════ 隔夜 tape:三态 / session / 分列 ═════════════════════


def test_tape_three_states_are_distinguishable():
    """**成功空 ≠ 请求失败**(§9 湖纪律):两者都落空数组 = 后者重跑能救的信息被抹掉。"""
    def history(sym):
        if sym == "^GSPC":
            return _hist()
        if sym == "^NDX":
            return pd.DataFrame(columns=["Date", "Close"])       # 源应答成功但零行
        raise yf_tape.TapeFetchError("HTTPError 429")            # 请求/解析失败

    df = yf_tape.fetch_global_tape(
        AS_OF, symbols=("^GSPC", "^NDX", "^SOX"), history=history, now=NOW)

    assert dict(zip(df["symbol"], df["status"])) == {
        "^GSPC": yf_tape.STATUS_OK, "^NDX": yf_tape.STATUS_EMPTY, "^SOX": yf_tape.STATUS_FAILED}
    assert df.attrs["tape_status"] == {"ok": 1, "empty": 1, "failed": 1, "requested": 3}
    reasons = dict(zip(df["symbol"], df["reason"]))
    assert "源应答成功但零行" in reasons["^NDX"]
    assert reasons["^SOX"].startswith("failed:") and "TapeFetchError" in reasons["^SOX"]
    assert reasons["^NDX"] != reasons["^SOX"], "两种空必须有不同的痕迹,否则重跑能不能救就没人知道"


def test_tape_keeps_one_row_per_requested_symbol_even_when_all_fail():
    """帧永远是「每个请求标的一行」——少一行 = 失败被静默吞掉。"""
    def boom(_sym):
        raise RuntimeError("network down")

    df = yf_tape.fetch_global_tape(AS_OF, symbols=("^GSPC", "^VIX"), history=boom, now=NOW)
    assert len(df) == 2 and set(df["status"]) == {yf_tape.STATUS_FAILED}
    assert list(df.columns) == list(yf_tape.TAPE_COLUMNS)


def test_tape_total_failure_records_summary_degradation_and_is_unusable():
    """整表失败 → 汇总一笔 + 逐标的一笔;pack `usable=False`(消费端整块省略,不沿用旧值)。"""
    def boom(_sym):
        raise RuntimeError("network down")

    pack = yf_tape.global_tape_pack(AS_OF, symbols=("^GSPC", "^VIX"), history=boom, now=NOW)
    assert pack["usable"] is False
    assert len(pack["degraded"]) == 2
    assert all(v is None for v in pack["macro_state"].values())
    recs = [r for r in contracts.degradations() if r["endpoint"] == "global_tape"]
    assert len(recs) == 3, "汇总 1 笔 + 逐标的 2 笔"
    assert any("整表取数失败" in r["reasons"][0] for r in recs)
    assert any("整块省略" in r["reasons"][0] for r in recs)


def test_tape_partial_loss_still_returns_the_usable_part():
    """部分缺失 = 可用部分照常返回(B 级:缺失永不阻断 run),但缺的那个必须记账。"""
    def history(sym):
        if sym == "^VIX":
            raise yf_tape.TapeFetchError("boom")
        return _hist()

    df = yf_tape.fetch_global_tape(AS_OF, symbols=("^GSPC", "^VIX"), history=history, now=NOW)
    assert df.attrs["tape_status"]["ok"] == 1 and df.attrs["tape_status"]["failed"] == 1
    assert _degraded_endpoints() == ["global_tape"], "只该记缺的那一个,不该记汇总"


def test_tape_pct_changes_and_close_are_computed_from_the_tail():
    df = yf_tape.fetch_global_tape(
        AS_OF, symbols=("^GSPC",), history=lambda s: _hist(n=10, start=100.0, step=1.0),
        now=NOW, record=False)
    row = df.iloc[0]
    assert row["close"] == 109.0
    assert row["pct_1d"] == round((109 / 108 - 1) * 100, 3)
    assert row["pct_5d"] == round((109 / 104 - 1) * 100, 3)


def test_tape_as_of_truncates_to_pit_bar():
    """`as_of` 是**日期截断**:各标的按自己的日历取该日期之前的最后一根。"""
    df = yf_tape.fetch_global_tape(
        "2026-08-20", symbols=("^GSPC",), history=lambda s: _hist(), now=NOW, record=False)
    assert df.iloc[0]["bar_date"] == "2026-08-20"


# ── session_complete:IANA 时区现算,禁写死偏移 ──


@pytest.mark.parametrize("hour_utc,expected", [(19, False), (20, True), (23, True)])
def test_session_complete_uses_market_close_in_iana_timezone(hour_utc, expected):
    """美股 16:00 ET 落定:19:00 UTC = 15:00 EDT(未收)、20:00 UTC = 16:00 EDT(已收)。"""
    now = datetime(2026, 8, 28, hour_utc, 0, tzinfo=timezone.utc)
    assert yf_tape.session_complete("us", "2026-08-28", now=now) is expected


def test_session_complete_same_instant_differs_by_market():
    """同一时刻、同一 `bar_date`,美股未收而 A 股已收 —— 两个市场不能共用一个"今天收盘了"。"""
    now = datetime(2026, 8, 28, 7, 30, tzinfo=timezone.utc)     # 03:30 EDT / 15:30 上海
    assert yf_tape.session_complete("us", "2026-08-28", now=now) is False
    assert yf_tape.session_complete("cn", "2026-08-28", now=now) is True


def test_session_complete_past_and_future_bars():
    assert yf_tape.session_complete("us", "2026-08-27", now=NOW) is True
    assert yf_tape.session_complete("us", "2026-08-29", now=NOW) is False
    assert yf_tape.session_complete("us", None, now=NOW) is False
    assert yf_tape.session_complete("us", "not-a-date", now=NOW) is False


def test_us_and_cn_dates_are_separate_columns_never_mixed():
    """§9:美股日与 A 股日**分列不混**(08-28 账本层已逮到过同族的时间锚错位事故)。"""
    df = yf_tape.fetch_global_tape(
        AS_OF, symbols=("^GSPC", "000001.SS"), history=lambda s: _hist(), now=NOW, record=False)
    us, cn = df.set_index("symbol").loc["^GSPC"], df.set_index("symbol").loc["000001.SS"]
    assert us["us_date"] == "2026-08-28" and us["cn_date"] is None
    assert cn["cn_date"] == "2026-08-28" and cn["us_date"] is None
    assert df.attrs["derived"]["us_session_date"] == "2026-08-28"


# ── VIX 分位:不足样本不给数 ──


def test_percentile_rank_refuses_to_guess_on_thin_samples():
    """12 个观测的「分位」和 250 个观测的长得一模一样 —— 给了比不给更坏。"""
    thin = yf_tape.percentile_rank(list(range(12)), 5.0)
    assert thin["pctile"] is None and thin["note"] == "样本不足(12)" and thin["obs"] == 12
    fat = yf_tape.percentile_rank(list(range(200)), 100.0)
    assert fat["pctile"] is not None and fat["note"] == ""
    assert yf_tape.percentile_rank(list(range(200)), None)["pctile"] is None


def test_tape_derived_vix_percentile_reports_thin_sample_instead_of_a_number():
    def history(sym):
        return _hist(n=12 if sym == "^VIX" else 30)

    d = yf_tape.fetch_global_tape(
        AS_OF, symbols=("^VIX", "^VIX3M"), history=history, now=NOW, record=False,
    ).attrs["derived"]
    assert d["vix_1y_pctile"] is None
    assert d["vix_1y_pctile_obs"] == 12 and "样本不足" in d["vix_1y_pctile_note"]
    assert d["vix_term_ratio"] is not None, "分位不可测不该连累期限比"


def test_cboe_percentile_uses_the_same_ruler_as_the_primary_source():
    """备源必须与主源同一把尺,否则「备源」会给出一个不可比的数。

    ⚠️ **开窗责任不在同一层**:`cboe_vix.vix_percentile` 自己 `.tail(window)`,而
    `yf_tape.percentile_rank` 不开窗(开窗在 `tape_derived` 里)。所以"同一把尺"只在
    **同样开过窗**之后成立 —— 拿整段历史直接喂两边会得到两个不同的分位。
    """
    hist = pd.DataFrame({"date": [f"d{i}" for i in range(300)],
                         "close": [float(i) for i in range(300)]})
    got = cboe_vix.vix_percentile(hist, 150.0)
    want = yf_tape.percentile_rank(hist["close"].tail(yf_tape.PCTILE_WINDOW), 150.0)
    assert got["pctile"] == want["pctile"] and got["source"] == "cboe"
    assert got["pctile"] != yf_tape.percentile_rank(hist["close"], 150.0)["pctile"], \
        "开窗与不开窗本就不该相等 —— 这条锁住「窗口责任在调用方」这件事别被悄悄改掉"
    thin = cboe_vix.vix_percentile(hist.head(10), 5.0)
    assert thin["pctile"] is None and "样本不足" in thin["note"]
    empty = cboe_vix.vix_percentile(pd.DataFrame(), 5.0)
    assert empty["pctile"] is None and empty["note"] == "样本不足(0)"


# ── ZQ=F:命名 + 禁令 ──


def test_zq_front_month_avg_rate_named_and_carries_the_ban():
    """字段名在场 + 禁令逐字在 docstring 与 disclaimer 里 —— 渲染层必须原样带出去。"""
    ban = "不得声称「下次会议隐含变动」"
    assert yf_tape.ZQ_RATE_FIELD == "zq_front_month_avg_rate"
    assert yf_tape.ZQ_RATE_FIELD in yf_tape.MACRO_STATE_KEYS
    assert ban in yf_tape.ZQ_RATE_DISCLAIMER
    assert ban in (yf_tape.tape_derived.__doc__ or "")


def test_zq_rate_value_and_disclaimer_travel_together_in_the_pack():
    def history(sym):
        return _hist(n=30, start=95.75, step=0.0) if sym == "ZQ=F" else _hist()

    pack = yf_tape.global_tape_pack(AS_OF, symbols=("ZQ=F",), history=history, now=NOW)
    assert pack["macro_state"]["zq_front_month_avg_rate"] == 4.25       # 100 − 95.75
    assert pack["derived"]["zq_front_month_price"] == 95.75
    assert yf_tape.ZQ_RATE_DISCLAIMER in pack["notes"], "数字走到哪,口径注就得跟到哪"


# ═════════════════════ 期权 v2:插值 / 跨式 / 分位 / PCR ═════════════════════


def _chain_frame(strikes, *, iv: float, bid: float, ask: float, oi: int = 100,
                 ts: str = _FRESH_TS, override: dict | None = None) -> pd.DataFrame:
    rows = []
    for k in strikes:
        row = {"strike": float(k), "bid": bid, "ask": ask, "impliedVolatility": iv,
               "openInterest": oi, "lastTradeDate": ts,
               # v1 的原病:拿三周前一笔 1 张的成交当"市场定价"。放一个荒唐的大数在这里,
               # 任何"退回 lastPrice"的实现都会立刻露馅。
               "lastPrice": 999.0}
        if override and k in override:
            row.update(override[k])
        rows.append(row)
    return pd.DataFrame(rows)


_STRIKES = (95.0, 97.5, 100.0, 102.5, 105.0)


def _expiry(iv: float, *, call_override=None, put_override=None):
    return (_chain_frame(_STRIKES, iv=iv, bid=4.0, ask=5.0, override=call_override),
            _chain_frame(_STRIKES, iv=iv, bid=3.0, ask=4.0, override=put_override))


class FakeOptionClient:
    """`yf_options` 的 duck-type client(expirations/option_chain/spot/daily_closes/market_state)。"""

    def __init__(self, chains: dict, *, spot: float = 100.0, closes=None,
                 state: str = "CLOSED", raise_on: str | None = None):
        self.chains = chains
        self._spot = spot
        self._closes = closes
        self._state = state
        self._raise_on = raise_on
        self.chain_calls: list[str] = []

    def expirations(self):
        if self._raise_on == "expirations":
            raise yf_options.OptionsFetchError("到期列表 503")
        return sorted(self.chains)

    def option_chain(self, expiry):
        self.chain_calls.append(expiry)
        return self.chains[expiry]

    def spot(self):
        return self._spot, "2026-08-28T20:00:00+00:00"

    def daily_closes(self, lookback: int = 60):
        return pd.Series(dtype="float64") if self._closes is None else pd.Series(self._closes)

    def market_state(self):
        return self._state


def test_narrow_snapshot_interpolates_30d_between_bracketing_expiries():
    """包围 30D 的两个有效到期 → DTE 上线性插值;口径写死在 `method_version` 里。"""
    client = FakeOptionClient({"2026-09-18": _expiry(0.20),      # dte 21
                               "2026-10-16": _expiry(0.27)})     # dte 49
    out = yf_options.narrow_snapshot("NVDA", AS_OF, client=client, now=NOW)

    assert out["status"] == yf_options.STATUS_OK
    assert out["interpolation"]["method"] == "linear_dte"
    assert out["interpolation"]["legs"] == ["2026-09-18", "2026-10-16"]
    assert out["atm_iv_30d"] == round(20.0 + (30 - 21) / (49 - 21) * (27.0 - 20.0), 4) == 22.25
    assert out["method_version"] == yf_options.METHOD_VERSION
    assert out["session_complete"] is True and out["iv_unit"] == "annualized_pct"
    assert contracts.degradations() == []


def test_narrow_snapshot_unmeasured_when_30d_is_not_bracketed():
    """不外推、不拿别的 tenor 冒充 → `UNMEASURED` + 原因,**不是 0**(纪律 1)。"""
    client = FakeOptionClient({"2026-09-04": _expiry(0.20)})     # dte 7,单边
    out = yf_options.narrow_snapshot("NVDA", AS_OF, client=client, now=NOW)

    assert out["status"] == yf_options.UNMEASURED
    assert out["atm_iv_30d"] is None, "UNMEASURED 不是 0"
    assert "无包围 30D 的有效到期" in out["reason"] and "不外推" in out["reason"]
    assert _degraded_endpoints() == ["us_options"]


def test_narrow_snapshot_unmeasured_when_quality_gate_rejects_every_expiry():
    """链取数成功但报价质量不合格 → UNMEASURED 且**保留质量原因**(不是"没有数据")。"""
    dead = {k: {"bid": None, "ask": None} for k in _STRIKES}
    client = FakeOptionClient({"2026-09-18": _expiry(0.20, call_override=dead, put_override=dead),
                               "2026-10-16": _expiry(0.27, call_override=dead, put_override=dead)})
    out = yf_options.narrow_snapshot("NVDA", AS_OF, client=client, now=NOW)

    assert out["status"] == yf_options.UNMEASURED and out["atm_iv_30d"] is None
    assert "有效合约不足" in out["reason"]
    assert all(e["quality"]["ok"] is False for e in out["expiries"])


def test_narrow_snapshot_respects_the_fetch_budget():
    """不为一个日历量级把映射池每天全链抓爆:最多 `NARROW_FETCH_BUDGET` 个到期。"""
    chains = {f"2026-{m:02d}-18": _expiry(0.20 + 0.01 * m) for m in range(9, 9 + 6)
              if m <= 12}
    chains.update({"2027-01-15": _expiry(0.30), "2027-02-19": _expiry(0.31)})
    client = FakeOptionClient(chains)
    yf_options.narrow_snapshot("NVDA", AS_OF, client=client, now=NOW)
    assert len(client.chain_calls) <= yf_options.NARROW_FETCH_BUDGET


def test_narrow_snapshot_degrades_when_symbol_has_no_listed_options():
    """A 股/港股常见:无挂牌期权 = 合法空(`legit_empty`),不是失败。"""
    out = yf_options.narrow_snapshot("600519.SS", AS_OF, client=FakeOptionClient({}), now=NOW)
    assert out["status"] == yf_options.UNMEASURED and "无挂牌期权" in out["reason"]
    assert [r["kind"] for r in contracts.degradations()] == ["legit_empty"]


def test_narrow_snapshot_expirations_failure_is_a_degradation_not_an_exception():
    out = yf_options.narrow_snapshot(
        "NVDA", AS_OF, client=FakeOptionClient({}, raise_on="expirations"), now=NOW)
    assert out["status"] == yf_options.UNMEASURED and "到期列表失败" in out["reason"]
    assert [r["kind"] for r in contracts.degradations()] == ["degraded"]


# ── 财报隐含波动:一腿坏 → UNMEASURED,且绝不退回 lastPrice ──


_EARN = {"date": "2026-09-10", "session": "AMC"}


def _full_chains(*, call_override=None, put_override=None):
    return {"2026-09-04": _expiry(0.18),                                        # 财报前
            "2026-09-18": _expiry(0.20, call_override=call_override,            # 财报后首个
                                  put_override=put_override),
            "2026-10-16": _expiry(0.27)}


def test_full_chain_earnings_move_uses_bid_ask_midpoint_straddle():
    """正对照:两腿都合格 → 跨式 = (call_mid + put_mid) / spot。"""
    out = yf_options.full_chain("NVDA", AS_OF, _EARN, client=FakeOptionClient(_full_chains()),
                                now=NOW)
    move = out["earnings_implied_move"]
    assert move["status"] == yf_options.STATUS_OK
    assert move["expiry"] == "2026-09-18" and move["time_quality"] == "TIMED"
    assert move["straddle"] == 8.0                      # call mid 4.5 + put mid 3.5
    assert move["implied_move_pct"] == 8.0              # / spot 100 × 100
    assert move["legs"]["call"]["mid"] == 4.5 and move["legs"]["put"]["mid"] == 3.5


@pytest.mark.parametrize("bad,where", [
    ({"bid": 5.0, "ask": 4.0}, "call"),                 # crossed
    ({"lastTradeDate": _STALE_TS}, "put"),              # stale
])
def test_full_chain_earnings_move_unmeasured_on_one_bad_leg(bad, where):
    """任一腿 crossed / stale → `UNMEASURED`,**不退回 lastPrice**(纪律 2)。"""
    override = {100.0: bad}
    chains = _full_chains(**{f"{where}_override": override})
    out = yf_options.full_chain("NVDA", AS_OF, _EARN, client=FakeOptionClient(chains), now=NOW)
    move = out["earnings_implied_move"]

    assert move["status"] == yf_options.UNMEASURED
    assert move["implied_move_pct"] is None and move["straddle"] is None
    assert "不退回 lastPrice" in move["reason"]
    assert ("crossed" if where == "call" else "stale") in move["reason"]
    assert move["legs"][where]["mid"] is None
    # lastPrice=999 若被当作跨式腿,跨式 = 1998 / move = 1998% —— 断言它从未出现在结果里。
    assert "1998" not in json.dumps(out, ensure_ascii=False, default=str)


def _code_without_docstring(fn) -> str:
    """函数源码剥掉 docstring —— 只留**会执行的那部分**。

    按 `\"\"\"` 切,不拿 `fn.__doc__` 去 replace:3.13 起 `__doc__` 会被自动去缩进,
    与源码文本对不上(那样 replace 静默失效,探针就成了永不变红的绿灯)。
    """
    parts = inspect.getsource(fn).split('"""')
    return parts[0] + "".join(parts[2:]) if len(parts) >= 3 else parts[0]


def _reads_column(src: str, col: str) -> bool:
    """列名被**当作键**读(`df["col"]` / `.get("col")` / `_num_col(df, "col")`)。

    只认"两侧带引号"的形态 —— 提示语里提一嘴列名(「不退回 lastPrice」)不算读它。
    """
    import re

    return re.search(r"""['"]%s['"]""" % re.escape(col), src) is not None


def test_earnings_implied_move_never_reads_last_price_at_all():
    """静态探针:纪律 2 说"本函数从头到尾不读该列" —— 那就让**可执行代码**里没有那条退路。

    行为断言只能证明"这次没退回";这条证明"退路根本不存在"。
    """
    for fn in (yf_options._earnings_implied_move, yf_options.leg_quality):
        assert not _reads_column(_code_without_docstring(fn), "lastPrice"), fn.__name__
    # 反向控制:同一形状的探针必须能逮住**真正被读**的列名,否则它是永不变红的绿灯。
    assert _reads_column(inspect.getsource(yf_options._valid_frame), "impliedVolatility")
    assert _reads_column(inspect.getsource(yf_options.leg_quality), "bid")


def test_full_chain_earnings_move_requires_expiry_strictly_after_earnings():
    """硬条件恒为「到期日严格晚于财报日」—— 对 AMC / BMO 都安全,故时段未确认时不猜。"""
    out = yf_options.full_chain(
        "NVDA", AS_OF, {"date": "2026-12-01"}, client=FakeOptionClient(_full_chains()), now=NOW)
    move = out["earnings_implied_move"]
    assert move["status"] == yf_options.UNMEASURED
    assert move["time_quality"] == "DATE_ONLY", "只有日期就别猜时段"
    assert "无严格晚于财报日的可用到期" in move["reason"]

    no_date = yf_options.full_chain(
        "NVDA", AS_OF, None, client=FakeOptionClient(_full_chains()), now=NOW)
    assert no_date["earnings_implied_move"]["time_quality"] == "UNKNOWN"
    assert "不猜时段" in no_date["earnings_implied_move"]["reason"]


def test_full_chain_reports_term_structure_skew_and_vol_premium():
    """全快照其余派生块:期限结构 / 偏度代理 / 波动率溢价,各自带自己的 UNMEASURED 口径。"""
    closes = [100.0 + (i % 3) for i in range(40)]
    client = FakeOptionClient(_full_chains(), closes=closes)
    out = yf_options.full_chain("NVDA", AS_OF, _EARN, client=client, now=NOW)

    assert out["term_structure"]["status"] == yf_options.STATUS_OK
    assert out["term_structure"]["slope"] == round(18.0 - 27.0, 4)     # IV_near − IV_far
    assert out["skew_proxy"]["label"] == yf_options.SKEW_LABEL
    assert out["vol_premium"]["status"] == yf_options.STATUS_OK
    assert out["vol_premium"]["realized_vol_20d"] is not None
    assert out["not_computed"] == ["max_pain", "oi_magnet", "unusual_activity"]


def test_realized_vol_returns_none_not_zero_on_thin_history():
    assert yf_options.realized_vol([100.0, 101.0, 102.0]) is None
    assert yf_options.realized_vol(list(range(100, 140))) is not None


# ── IV 分位:口径混装即拒 ──


def _obs(n: int, *, mv: str | None = None, tenor: int = 30, complete: bool = True,
         start: float = 20.0):
    return [{"atm_iv_30d": start + i * 0.1,
             "method_version": mv or yf_options.METHOD_VERSION,
             "tenor_days": tenor, "session_complete": complete} for i in range(n)]


def test_iv_percentile_rejects_mixed_method_version():
    """不同口径串成的"历史"不是历史 —— 混进来的那条必须被剔,且不得影响分位。"""
    clean = _obs(45)
    mixed = clean + _obs(1, mv="options_v1.0", start=999.0)
    got = yf_options.iv_percentile(mixed, 22.0)
    base = yf_options.iv_percentile(clean, 22.0)

    assert got["obs"] == 45 and got["dropped"]["method_version"] == 1
    assert got["pctile"] == base["pctile"], "被剔的离群值不得挪动分位"
    assert got["method_version"] == yf_options.METHOD_VERSION


def test_iv_percentile_rejects_other_tenors_and_incomplete_sessions():
    rows = _obs(45) + _obs(3, tenor=60) + _obs(4, complete=False) + [
        {"atm_iv_30d": None, "method_version": yf_options.METHOD_VERSION,
         "tenor_days": 30, "session_complete": True}]
    got = yf_options.iv_percentile(rows, 22.0)
    assert got["obs"] == 45
    assert got["dropped"] == {"method_version": 0, "tenor": 3,
                              "incomplete_session": 4, "no_value": 1}


def test_iv_percentile_needs_forty_complete_sessions():
    """§5.2:≥40 个**完整交易时段**的有效观测才印分位,否则「样本不足(n)」。"""
    thin = yf_options.iv_percentile(_obs(yf_options.IV_PCTILE_MIN_OBS - 1), 22.0)
    assert thin["status"] == yf_options.UNMEASURED and thin["pctile"] is None
    assert thin["note"] == f"样本不足({yf_options.IV_PCTILE_MIN_OBS - 1})"
    ok = yf_options.iv_percentile(_obs(yf_options.IV_PCTILE_MIN_OBS), 22.0)
    assert ok["status"] == yf_options.STATUS_OK and ok["pctile"] is not None


# ── PCR:只描述对冲/持仓压力,不写方向 ──


def test_pcr_semantics_and_payload_contain_no_direction_words():
    """08-24 普查裁定:PCR 不进方向。渲染层禁词一个都不许出现在返回体里。"""
    client = FakeOptionClient(_full_chains(), closes=[100.0 + (i % 3) for i in range(40)])
    out = yf_options.full_chain("NVDA", AS_OF, _EARN, client=client, now=NOW)
    pcr = out["pcr"]

    assert pcr["semantics"] == yf_options.PCR_SEMANTICS
    assert "对冲" in pcr["semantics"] and "持仓压力" in pcr["semantics"]
    assert pcr["total"]["pcr_oi"] == 1.0 and len(pcr["by_expiry"]) == 3
    blob = json.dumps(out, ensure_ascii=False, default=str).lower()
    for word in yf_options.FORBIDDEN_DIRECTION_WORDS:
        assert word.lower() not in blob, f"期权返回体出现方向词 {word!r}"


def test_pcr_is_unmeasured_when_call_oi_is_zero_not_a_fabricated_ratio():
    zero_oi = {k: {"openInterest": 0} for k in _STRIKES}
    chains = {"2026-09-18": _expiry(0.20, call_override=zero_oi),
              "2026-10-16": _expiry(0.27, call_override=zero_oi)}
    out = yf_options.full_chain("NVDA", AS_OF, None, client=FakeOptionClient(chains), now=NOW)
    assert out["pcr"]["status"] == yf_options.UNMEASURED
    assert out["pcr"]["total"]["pcr_oi"] is None and "无定义" in out["pcr"]["reason"]


# ═════════════════════ 湖纪律:窄表毒化 / 快照 PIT / 名字对齐 ═════════════════════


def _tape_frame() -> pd.DataFrame:
    return yf_tape.fetch_global_tape(
        "2026-08-27", symbols=("^GSPC", "000001.SS", "^VIX"),
        history=lambda s: _hist(last="2026-08-27"), now=NOW, record=False)


def test_narrow_fields_query_still_writes_the_full_table_to_the_lake(lake):
    """**窄表毒化探针**:带 `fields` 的查询不得把窄表钉成这一天的湖快照(§9 逐字)。"""
    wide = _tape_frame()
    seen: list[dict] = []

    def fetch(_ep, params):
        seen.append(dict(params))
        return wide[["symbol", "close"]].copy() if "fields" in params else wide.copy()

    cache.get_or_fetch("global_tape", {"trade_date": "20260827", "fields": "symbol,close"},
                       today=_TODAY, fetch=fetch)
    assert "fields" not in seen[0], "写湖那次取数必须剥掉 fields"
    saved = pd.read_parquet(lake / "global_tape" / "20260827.parquet")
    assert set(yf_tape.TAPE_COLUMNS) <= set(saved.columns)


def test_mutation_probe_without_field_stripping_the_lake_gets_pinned_narrow(lake, monkeypatch):
    """变异探针(还原"不剥 fields"):同一次调用会把两列窄表钉进湖 —— 证明上面那条有鉴别力。"""
    monkeypatch.setattr(cache, "_lake_params", lambda p: p)
    wide = _tape_frame()

    def fetch(_ep, params):
        return wide[["symbol", "close"]].copy() if "fields" in params else wide.copy()

    cache.get_or_fetch("global_tape", {"trade_date": "20260827", "fields": "symbol,close"},
                       today=_TODAY, fetch=fetch)
    saved = pd.read_parquet(lake / "global_tape" / "20260827.parquet")
    assert list(saved.columns) == ["symbol", "close"], "旧行为:窄表真的会被钉死"
    assert any("缺列" in r["reasons"][0] for r in contracts.degradations())


def test_cboe_vix_narrow_frame_is_refused_by_the_lake(lake):
    """另一半:剥不掉的窄(源头就少列)→ 契约逮住 + **拒绝入湖**(不落 → 重跑还能救)。"""
    narrow = pd.DataFrame({"date": ["2026-08-27"], "open": [15.0]})     # 缺 close
    out = cache.get_or_fetch("cboe_vix", {"trade_date": "20260827"}, today=_TODAY,
                             fetch=lambda e, p: narrow)
    assert len(out) == 1, "B 级不阻断:数据照样返回给调用方"
    assert not (lake / "cboe_vix" / "20260827.parquet").exists(), "半截不许钉进湖"
    assert [r["endpoint"] for r in contracts.degradations()] == ["cboe_vix"]


def test_mutation_probe_without_persist_violations_flag_the_half_frame_gets_pinned(lake):
    """变异探针(还原「没有 `persist_violations=False`」):同一份缺列帧会被钉进湖 ——
    反向证明上面那条"不许钉进湖"是 `persist_violations=False` 在干活。"""
    narrow = pd.DataFrame({"date": ["2026-08-27"], "open": [15.0]})
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(contracts.CONTRACTS, "cboe_vix",
                   contracts.Contract(tier=contracts.TIER_DEGRADE))     # 旧契约:照落不误
        cache.get_or_fetch("cboe_vix", {"trade_date": "20260827"}, today=_TODAY,
                           fetch=lambda e, p: narrow)
    assert (lake / "cboe_vix" / "20260827.parquet").exists(), "旧行为:半截真的会被钉死"


def test_cboe_vix_healthy_history_lands_in_the_lake_under_the_registered_name(lake):
    """名字对齐的活体证明:`load_vix_history` 的湖路径必须落在登记名 `cboe_vix` 下。"""
    hist = pd.DataFrame({"date": [f"2026-08-{d:02d}" for d in range(1, 21)],
                         "open": 15.0, "high": 16.0, "low": 14.0,
                         "close": [15.0 + i for i in range(20)]})
    df = cboe_vix.load_vix_history("2026-08-27", today=_TODAY, fetch=lambda e, p: hist)
    assert df.attrs["status"] == "ok" and len(df) == 20
    assert (lake / "cboe_vix" / "20260827.parquet").exists()
    assert contracts.degradations() == []


def test_cboe_vix_failure_is_recorded_and_returns_an_empty_frame_marked_failed(lake):
    """B 级:拿不到就降级,`attrs['status']` 区分 failed / empty —— 不抛。"""
    def boom(_e, _p):
        raise cboe_vix.CboeFetchError("CBOE csv 行数腰斩(120 < 2000)")

    out = cboe_vix.load_vix_history("2026-08-27", today=_TODAY, fetch=boom)
    assert out.attrs["status"] == "failed" and "腰斩" in out.attrs["reason"]
    assert list(out.columns) == list(cboe_vix.COLUMNS) and len(out) == 0
    assert _degraded_endpoints() == ["cboe_vix"]

    contracts.clear_degradations()
    empty = cboe_vix.load_vix_history("2026-08-26", today=_TODAY,
                                      fetch=lambda e, p: pd.DataFrame(columns=["date", "close"]))
    assert empty.attrs["status"] == "empty", "「源真空」与「请求失败」不可混"


def test_global_tape_lands_in_the_lake_under_the_registered_name(lake):
    """名字对齐的活体证明:`load_global_tape` 的分区落在 `lake/global_tape/<us_date>`。"""
    df = yf_tape.load_global_tape("2026-08-27", today=_TODAY,
                                  history=lambda s: _hist(last="2026-08-27"), now=NOW)
    assert len(df) == len(yf_tape.ALL_SYMBOLS)
    assert (lake / "global_tape" / "20260827.parquet").exists()


def test_options_degradation_is_recorded_under_the_registered_endpoint_name():
    """名字对齐的活体证明:期权降级记在 `us_options` 名下(登记名与记账名同一个)。"""
    yf_options.narrow_snapshot("NVDA", AS_OF, client=FakeOptionClient({}), now=NOW)
    assert _degraded_endpoints() == ["us_options"]
    assert "us_options" in endpoints.ENDPOINTS and "us_options" in contracts.CONTRACTS


# ── 快照端点的 PIT 守门 ──


@pytest.mark.parametrize("ep", SNAPSHOT_ENDPOINTS)
def test_snapshot_endpoint_refuses_backdated_as_of(ep, lake, wall_clock):
    """`snapshot: True` 端点 as_of ≠ 今天 → `SnapshotDateError`,且守门在**取数之前**。"""
    calls = []
    with pytest.raises(cache.SnapshotDateError, match="快照 PIT"):
        cache.get_or_fetch(ep, {"symbol": "NVDA"}, today="20260826",
                           fetch=lambda e, p: calls.append(e) or pd.DataFrame({"a": [1]}))
    assert calls == [], "守门必须在取数之前,别拉完再扔"
    assert not cache.lake_path(ep, {"symbol": "NVDA"}, today="20260826").exists()


def test_mutation_probe_without_snapshot_flag_the_pit_mislabel_happens(lake, wall_clock):
    """变异探针(还原「没有 `snapshot: True`」):**今天**取到的期权链会被落成 08-26 的
    "历史"且事后不可甄别 —— 反向证明守门是那面旗在干活,不是别的什么顺带拦下的。"""
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(endpoints.ENDPOINTS, "us_options",
                   {"key": "as_of", "settle": "eod", "source": "yfinance"})
        cache.get_or_fetch("us_options", {"symbol": "NVDA"}, today="20260826",
                           fetch=lambda e, p: pd.DataFrame({"a": [1]}))
    assert cache.lake_path("us_options", {"symbol": "NVDA"}, today="20260826").exists(), \
        "旧行为:错标真的会发生"


@pytest.mark.parametrize("ep", SNAPSHOT_ENDPOINTS)
def test_snapshot_today_writes_with_observed_provenance(ep, lake, wall_clock):
    """正对照:as_of == 今天照常落盘,并打 `first_seen_basis="observed"` 观测戳。"""
    cache.get_or_fetch(ep, {"symbol": "NVDA"}, today=_TODAY,
                       fetch=lambda e, p: pd.DataFrame({"a": [1]}))
    saved = pd.read_parquet(cache.lake_path(ep, {"symbol": "NVDA"}, today=_TODAY))
    assert set(saved["first_seen_basis"]) == {"observed"}
    assert datetime.fromisoformat(str(saved["first_seen_ts"].iloc[0])).tzinfo is not None


@pytest.mark.parametrize("ep", SNAPSHOT_ENDPOINTS)
def test_snapshot_empty_result_is_not_pinned_into_the_lake(ep, lake, wall_clock):
    """快照端点的空/半截**不入湖**:落了就 `path.exists()` 恒命中,这一天永远残缺。"""
    cache.get_or_fetch(ep, {"symbol": "NVDA"}, today=_TODAY, fetch=lambda e, p: pd.DataFrame())
    assert not cache.lake_path(ep, {"symbol": "NVDA"}, today=_TODAY).exists()


def test_non_snapshot_endpoints_accept_backdated_as_of(lake, wall_clock):
    """反向:没打快照旗的端点(edgar / us_ticker)按取数日留底,补跑历史键不受守门影响。"""
    for ep in ("edgar", "us_ticker", "us_earnings_dates"):
        cache.get_or_fetch(ep, {"symbol": "NVDA", "as_of": "20260826"}, today=_TODAY,
                           fetch=lambda e, p: pd.DataFrame({"a": [1]}))
        assert cache.lake_path(ep, {"symbol": "NVDA", "as_of": "20260826"},
                               today=_TODAY).exists(), ep


# ═════════════════════ 时效契约三态(§9) ═════════════════════


def test_freshness_three_states_fresh_stale_allowed_max_stale():
    """fresh / stale-allowed / max-stale:第三态**整块省略**,不是"标个陈旧继续用"。"""
    slo = endpoints.freshness("cboe_vix")["freshness_slo"]
    mx = endpoints.freshness("cboe_vix")["max_stale"]

    fresh = endpoints.freshness_state("cboe_vix", slo - 1)
    assert fresh["state"] == endpoints.FRESH and fresh["usable"] is True
    assert fresh["stale_reason"] == ""

    stale = endpoints.freshness_state("cboe_vix", slo + 1)
    assert stale["state"] == endpoints.STALE and stale["usable"] is True
    assert "freshness_slo" in stale["stale_reason"] and "打印 age" in stale["stale_reason"]

    dead = endpoints.freshness_state("cboe_vix", mx + 1)
    assert dead["state"] == endpoints.EXPIRED and dead["usable"] is False
    assert "整块省略" in dead["stale_reason"] and "不得静默沿用旧值" in dead["stale_reason"]


def test_freshness_boundaries_are_inclusive_at_the_slo_and_at_max_stale():
    slo = endpoints.freshness("global_tape")["freshness_slo"]
    mx = endpoints.freshness("global_tape")["max_stale"]
    assert endpoints.freshness_state("global_tape", slo)["state"] == endpoints.FRESH
    assert endpoints.freshness_state("global_tape", mx)["state"] == endpoints.STALE
    assert endpoints.freshness_state("global_tape", mx + 0.1)["state"] == endpoints.EXPIRED


def test_unknown_age_is_not_treated_as_fresh():
    """age 不可判(缺 `fetched_at`)≠ 很新 —— 与期权 quote age 同一条纪律。"""
    s = endpoints.freshness_state("global_tape", None)
    assert s["state"] == endpoints.EXPIRED and s["usable"] is False
    assert "不可判" in s["stale_reason"]


def test_stale_on_error_decides_whether_a_stale_copy_may_stand_in():
    """易腐数据(隔夜 tape / 期权报价)取数失败时宁可缺席,不拿上一份冒充。"""
    slo = endpoints.freshness("global_tape")["freshness_slo"]
    perishable = endpoints.freshness_state("global_tape", slo + 60, after_error=True)
    assert perishable["state"] == endpoints.STALE and perishable["usable"] is False
    assert "不拿上一份冒充" in perishable["stale_reason"]

    durable = endpoints.freshness_state("fomc_calendar", 8 * 86400, after_error=True)
    assert durable["state"] == endpoints.STALE and durable["usable"] is True

    quotes = endpoints.freshness_state("us_options", yf_options.MAX_QUOTE_AGE_SEC + 60,
                                       after_error=True)
    assert quotes["usable"] is False
