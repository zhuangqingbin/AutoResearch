"""parquet lake cache — exists=hit, atomic write, settled-vs-today rule."""

import pandas as pd
import pytest

from autoresearch.data import cache


@pytest.fixture
def lake(tmp_path, monkeypatch):
    """Redirect the lake root to a tmp dir so tests never touch context/lake/."""
    root = tmp_path / "lake"
    monkeypatch.setattr(cache, "LAKE", root)
    return root


class _Counter:
    """A fake fetch that records call count and returns a fixed frame."""

    def __init__(self, frame):
        self.frame = frame
        self.calls = 0

    def __call__(self, endpoint, params):
        self.calls += 1
        return self.frame.copy()


def _daily_row(close: float = 10.5) -> pd.DataFrame:
    """一行**合契约**的 daily(真实列名)。

    数据契约(2026-07-12)会查 A 级端点的必需列 —— 这些 cache 机制测试(key/hit/write/settle)
    从前用的是假列名(`code`/`x`),契约上线后被正确地判为"缺列"。测试数据本就该像真数据:
    列名对齐 tushare 的 daily,内容仍是一行(行数腰斩这条规模检查已由 conftest 全局关掉)。
    """
    return pd.DataFrame({"ts_code": ["600000.SH"], "open": [10.0], "high": [11.0], "low": [9.0],
                         "close": [close], "amount": [1e5], "pct_chg": [1.2]})


def test_first_fetch_writes_then_second_call_hits(lake):
    fetch = _Counter(_daily_row())
    # settled past date → cacheable
    out1 = cache.get_or_fetch("daily", {"trade_date": "20240102"}, today="20260622", fetch=fetch)
    assert fetch.calls == 1
    assert out1["close"].iloc[0] == 10.5
    path = cache.lake_path("daily", {"trade_date": "20240102"})
    assert path.exists()
    assert path.suffix == ".parquet"

    out2 = cache.get_or_fetch("daily", {"trade_date": "20240102"}, today="20260622", fetch=fetch)
    assert fetch.calls == 1  # HIT, fetch NOT called again
    pd.testing.assert_frame_equal(out1.reset_index(drop=True), out2.reset_index(drop=True))


# ───────────────────── 窄表毒化回归(2026-07-12,回放器 M1 对拍逮到) ─────────────────────
#
# `_cache_key` 不含 `fields` → 湖里一个 key 只有一个 parquet。带窄 fields 的查询若成为该 key 的
# 首个写入者,就把窄表钉成这一天的湖快照,后来要别的列的调用方只能读到缺列的表(且多半被
# try/except 静默吞成"降级")。真实事故:temperature.rollup 用 fields='ts_code,pct_chg' 回填
# 07-09/07-10 的 daily(那两天扫描当天未结算故未入湖)→ 钉成两列窄表 → _harvest_vol_series
# 拿不到 high/low/amount → volprice 整组 NaN → 全市场 composite 失真 98.8%。


class _FieldsSpy:
    """记录 fetch 实际收到的 params(验证入湖那次是否剥掉了 fields)。"""

    def __init__(self, wide: pd.DataFrame, narrow: pd.DataFrame):
        self.wide, self.narrow = wide, narrow
        self.seen: list[dict] = []

    def __call__(self, endpoint, params):
        self.seen.append(dict(params))
        return (self.narrow if "fields" in params else self.wide).copy()


def test_narrow_fields_query_still_writes_full_snapshot_to_lake(lake):
    """入湖必须全字段:调用方传窄 fields,写湖那次 fetch 也不得带 fields(否则湖被钉成窄表)。"""
    wide = _daily_row()
    narrow = pd.DataFrame({"ts_code": ["600000.SH"], "pct_chg": [1.2]})
    fetch = _FieldsSpy(wide, narrow)

    out = cache.get_or_fetch("daily", {"trade_date": "20260709", "fields": "ts_code,pct_chg"},
                             today="20260712", fetch=fetch)
    assert "fields" not in fetch.seen[0], "写湖那次取数必须剥掉 fields"
    assert {"high", "low", "amount"} <= set(out.columns), "调用方也拿到全字段(多几列无害,少一列是灾难)"

    on_disk = pd.read_parquet(cache.lake_path("daily", {"trade_date": "20260709"}))
    assert {"high", "low", "close", "amount"} <= set(on_disk.columns), \
        "湖里必须是全量快照——否则下一个要 high/low/amount 的调用方静默拿到 NaN"


def test_narrow_query_then_wide_reader_gets_all_columns(lake):
    """端到端复现事故场景:窄查询先来(温度计),宽读者后到(_harvest_vol_series)→ 必须拿到全列。"""
    wide = _daily_row()
    fetch = _FieldsSpy(wide, pd.DataFrame({"ts_code": ["600000.SH"], "pct_chg": [1.2]}))
    cache.get_or_fetch("daily", {"trade_date": "20260709", "fields": "ts_code,pct_chg"},
                       today="20260712", fetch=fetch)          # 温度计:只要 pct_chg
    later = cache.get_or_fetch("daily", {"trade_date": "20260709"}, today="20260712", fetch=fetch)
    assert len(fetch.seen) == 1, "第二次应命中湖(零取数)"
    assert {"high", "low", "amount"} <= set(later.columns)     # 事故前:这里会 KeyError → 静默降级


def test_unsettled_narrow_query_keeps_fields_and_does_not_write(lake):
    """不入湖的路径(date>=today)保留窄 fields 省流量,且不写湖(不会钉死窄表)。

    且**不按全字段契约校验**(`check(cols=False)`):这份数据不入湖、只服务当次调用,调用方要
    哪几列是它自己的事(温度计只要 `ts_code,pct_chg`)。列契约只管会被持久化/复用的数据。"""
    fetch = _FieldsSpy(_daily_row(), pd.DataFrame({"ts_code": ["600000.SH"], "pct_chg": [1.2]}))
    out = cache.get_or_fetch("daily", {"trade_date": "20260712", "fields": "ts_code,pct_chg"},
                             today="20260712", fetch=fetch)
    assert fetch.seen[0].get("fields") == "ts_code,pct_chg", "未结算日不入湖 → 窄查询原样透传"
    assert list(out.columns) == ["ts_code", "pct_chg"], "窄列原样返回,不被契约拦"
    assert not cache.lake_path("daily", {"trade_date": "20260712"}).exists()


def test_unsettled_empty_still_raises_data_not_published_yet(lake):
    """但**空仍然要抛**:A 级端点在盘中拉到空 = 数据还没发布,下游必然残废
    —— 与 `tushare_source.assert_tushare_ready` 同一立场(晚点再跑,或跑前一交易日)。"""
    from autoresearch.data.contracts import DataContractError

    with pytest.raises(DataContractError):
        cache.get_or_fetch("daily", {"trade_date": "20260712", "fields": "ts_code,pct_chg"},
                           today="20260712", fetch=lambda e, p: pd.DataFrame())


def test_empty_result_of_tier_b_endpoint_writes_and_still_hits(lake):
    """"存在==取过且为空"仍成立 —— 但**只对 B 级(增强)端点**。

    2026-07-12 数据契约上线后,A 级(地基)端点的空帧**不再入湖**(空 = 取数残缺,钉死它等于让
    每次命中都读到脏数据、重跑也自愈不了)。B 级的空多是真实的空(无北向额度的日子 hk_hold 就是
    空),照旧入湖以免每天重拉一次空。A 级空 → 抛 + 不写:见 tests/data/test_contracts.py。"""
    fetch = _Counter(pd.DataFrame())
    out = cache.get_or_fetch("hk_hold", {"trade_date": "20240103"}, today="20260622", fetch=fetch)
    assert fetch.calls == 1
    assert out.empty
    assert cache.lake_path("hk_hold", {"trade_date": "20240103"}).exists()
    # second call: existence == "fetched, empty" → hit, no refetch
    out2 = cache.get_or_fetch("hk_hold", {"trade_date": "20240103"}, today="20260622", fetch=fetch)
    assert fetch.calls == 1
    assert out2.empty


def test_live_endpoint_never_caches(lake):
    fetch = _Counter(pd.DataFrame({"code": ["600000"]}))
    cache.get_or_fetch("stock_zh_a_spot_em", {}, today="20260622", fetch=fetch)
    cache.get_or_fetch("stock_zh_a_spot_em", {}, today="20260622", fetch=fetch)
    assert fetch.calls == 2  # always fetch, never written
    # nothing persisted under the live endpoint
    assert not (lake / "stock_zh_a_spot_em").exists()


def test_date_equal_today_is_fetched_fresh_not_written(lake):
    fetch = _Counter(_daily_row(close=9.9))
    out = cache.get_or_fetch("daily", {"trade_date": "20260622"}, today="20260622", fetch=fetch)
    assert fetch.calls == 1
    assert out["close"].iloc[0] == 9.9
    # unsettled (date >= today) → NOT written
    assert not cache.lake_path("daily", {"trade_date": "20260622"}).exists()
    # next call refetches (still unsettled)
    cache.get_or_fetch("daily", {"trade_date": "20260622"}, today="20260622", fetch=fetch)
    assert fetch.calls == 2


def test_future_date_is_unsettled_not_written(lake):
    fetch = _Counter(_daily_row())
    cache.get_or_fetch("daily", {"trade_date": "20260630"}, today="20260622", fetch=fetch)
    assert not cache.lake_path("daily", {"trade_date": "20260630"}).exists()


def test_as_of_key_includes_entity_and_fetch_day(lake):
    fetch = _Counter(pd.DataFrame({"holder_num": [12345]}))
    params = {"symbol": "600519", "as_of": "20260101"}
    cache.get_or_fetch("stock_zh_a_gdhs_detail_em", params, today="20260622", fetch=fetch)
    p = cache.lake_path("stock_zh_a_gdhs_detail_em", params)
    assert "600519@20260101" in p.stem
    assert p.exists()


def test_static_endpoint_keyed_static(lake):
    # 契约:stock_basic 是 A 级(名称+上市日 = 剔次新硬门的地基)→ fixture 须带 list_date
    fetch = _Counter(pd.DataFrame({"ts_code": ["600000.SH"], "name": ["x"], "list_date": ["19991110"]}))
    cache.get_or_fetch("stock_basic", {"list_status": "L"}, today="20260622", fetch=fetch)
    p = cache.lake_path("stock_basic", {"list_status": "L"})
    assert p.stem == "static"
    assert p.exists()
    # second call hits regardless of params (static key is param-independent)
    cache.get_or_fetch("stock_basic", {"list_status": "L"}, today="20260622", fetch=fetch)
    assert fetch.calls == 1


def test_default_today_uses_compact_date(lake, monkeypatch):
    # when today is omitted it derives from date.today() in compact form; a clearly
    # past date must therefore be treated as settled and written.
    fetch = _Counter(_daily_row())
    cache.get_or_fetch("daily", {"trade_date": "20200101"}, fetch=fetch)
    assert cache.lake_path("daily", {"trade_date": "20200101"}).exists()


# ───────────────────── LAKE_ASSUME_SETTLED 结算豁免(2026-07-12,P1a) ─────────────────────
#
# `date` 键端点默认对 `d >= today`(盘中未结算)一律"拉新但不写",预热当天数据永远进不了湖。
# `LAKE_ASSUME_SETTLED=1` 开一条窄缝:仅当 `d == today` 才视为已结算放行入湖(19:15 后 EOD 已
# 发布,契约 min_rows 仍兜底);`d > today`(未来日)任何情况恒拒写;env 未设 = 现行为逐字节不变。


def test_lake_assume_settled_writes_same_day(tmp_path, monkeypatch):
    import autoresearch.data.cache as cache
    monkeypatch.setattr(cache, "LAKE", tmp_path)
    monkeypatch.setenv("LAKE_ASSUME_SETTLED", "1")
    df = pd.DataFrame({"ts_code": [f"{i:06d}.SZ" for i in range(3100)],
                       "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
                       "amount": 1.0, "pct_chg": 0.0})
    out = cache.get_or_fetch("daily", {"trade_date": "20260710"}, today="2026-07-10",
                             fetch=lambda ep, p: df)
    assert len(out) == 3100
    assert (tmp_path / "daily" / "20260710.parquet").exists()      # 同日入湖(豁免生效)


def test_lake_assume_settled_never_writes_future(tmp_path, monkeypatch):
    import autoresearch.data.cache as cache
    monkeypatch.setattr(cache, "LAKE", tmp_path)
    monkeypatch.setenv("LAKE_ASSUME_SETTLED", "1")
    df = pd.DataFrame({"ts_code": ["000001.SZ"], "open": [1.0], "high": [1.0],
                       "low": [1.0], "close": [1.0], "amount": [1.0], "pct_chg": [0.0]})
    cache.get_or_fetch("top_list", {"trade_date": "20260711"}, today="2026-07-10",
                       fetch=lambda ep, p: df)
    assert not (tmp_path / "top_list" / "20260711.parquet").exists()   # 未来日恒不写


def test_no_env_same_day_not_written(tmp_path, monkeypatch):
    import autoresearch.data.cache as cache
    monkeypatch.setattr(cache, "LAKE", tmp_path)
    monkeypatch.delenv("LAKE_ASSUME_SETTLED", raising=False)
    df = pd.DataFrame({"ts_code": ["000001.SZ"], "open": [1.0], "high": [1.0],
                       "low": [1.0], "close": [1.0], "amount": [1.0], "pct_chg": [0.0]})
    cache.get_or_fetch("top_list", {"trade_date": "20260710"}, today="2026-07-10",
                       fetch=lambda ep, p: df)
    assert not (tmp_path / "top_list" / "20260710.parquet").exists()   # parity
