#!/usr/bin/env python3
"""Wave12 T2 · 热度快照入湖(东财人气/雪球关注)—— endpoints 注册 + B 级契约 + as-of lake。

design: docs/research/2026-08-09-hot-rank-probe.md(T1 探针裁定,**2026-08-09 复核后已改写**:
东财人气榜的**榜单本体可用**、akshare 封装 `stock_hot_rank_em` 不可用——它第二跳走
`push2.eastmoney.com`(项目记忆判例「A股数据走 tushare,东财 push2 被封」点名的主机),
实测 502 → 整个函数 JSONDecodeError。本仓改为**自采第一跳** `eastmoney_hot_rank`)。

本文件锁死四件事(每件配反向变异探针):
1. **半截/空返回不得钉进湖**(C2):`min_rows`/`required_cols` + `persist_violations=False`
   —— 「cache 空 pickle 永不重拉」家训的 parquet 同族,一旦落盘 `path.exists()` 恒命中,
   这一天永远残缺、重跑也自愈不了。
2. **快照分区必须是观测日**(I1):`snapshot=True` 的端点,as-of 键 ≠ 真实今天 → 拒绝取数
   (补跑/节假日触发会把今天的快照写成过去某天的历史,且事后不可甄别)。
3. **观测出处必须落列**(I3):`observed_at` + `first_seen_basis="observed"`(任务书 Interfaces
   逐字要求)——没有它,「这份分片到底什么时候抓的」永久不可回答。
4. **写湖一律剥 fields**(既有家训)。
"""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.data import cache, contracts, endpoints

XQ = "stock_hot_follow_xq"
EM = "eastmoney_hot_rank"
ENDPOINTS_UNDER_TEST = (EM, XQ)

_TODAY = "20260809"


@pytest.fixture(autouse=True)
def _clean_degradations():
    contracts.clear_degradations()
    yield
    contracts.clear_degradations()


@pytest.fixture(autouse=True)
def _wall_clock(monkeypatch):
    """把「真实今天」钉死 —— 快照守门(I1)拿它比对 as-of 键,不能靠机器日历跑测试。"""
    monkeypatch.setattr(cache, "_real_today", lambda: _TODAY)


@pytest.fixture
def lake(tmp_path, monkeypatch):
    root = tmp_path / "lake"
    monkeypatch.setattr(cache, "LAKE", root)
    return root


def _em_frame(rows: int = 100) -> pd.DataFrame:
    """东财人气榜第一跳(`emappdata/getAllCurrentList`)的**原始**列(2026-08-09 实测)。"""
    return pd.DataFrame({
        "sc": [f"SH{600000 + i}" for i in range(rows)],
        "rk": list(range(1, rows + 1)),
        "rc": [0] * rows,
        "hisRc": [1] * rows,
    })


def _xq_frame(rows: int = 5619) -> pd.DataFrame:
    """雪球关注度实测列;没有 `follow_delta`(「关注」是累计快照,7 日新增走 symbol=本周新增)。"""
    return pd.DataFrame({
        "股票代码": [f"SH{600000 + i}" for i in range(rows)],
        "股票简称": [f"股{i}" for i in range(rows)],
        "关注": [float(rows - i) for i in range(rows)],
        "最新价": [10.0] * rows,
    })


_FRAMES = {EM: _em_frame, XQ: _xq_frame}


@pytest.fixture
def rows_checked(monkeypatch):
    """打开**规模性**检查(全局 conftest 默认关掉;行数腰斩逻辑本身必须显式开着测)。"""
    monkeypatch.setattr(contracts, "CHECK_ROWS", True)


# ───────────────────────── 注册表:endpoints policy + contracts tier ─────────────────────────


def test_registered_as_asof_snapshot_endpoints():
    """两端点 = 按取数日快照 + `snapshot=True`(守门与观测戳都挂这面旗)。

    东财走**自采**第一跳(source=eastmoney),不是 akshare 封装——封装的第二跳 push2 被封。
    """
    assert endpoints.policy(EM) == {"key": "as_of", "settle": "eod", "source": "eastmoney",
                                    "snapshot": True}
    assert endpoints.policy(XQ) == {"key": "as_of", "settle": "eod", "source": "akshare",
                                    "snapshot": True}


def test_akshare_hot_rank_em_wrapper_is_not_registered():
    """`stock_hot_rank_em`(akshare 封装)**不得**再被登记 —— 它每晚都失败(push2 502)。"""
    with pytest.raises(KeyError):
        endpoints.policy("stock_hot_rank_em")


@pytest.mark.parametrize("ep", ENDPOINTS_UNDER_TEST)
def test_registered_as_tier_b_with_shape_floor(ep):
    """B 级(断采不阻断扫描)**且**有行数下限/关键列 + 违约不入湖 —— C2 的三件套。"""
    con = contracts.CONTRACTS[ep]
    assert con.tier == contracts.TIER_DEGRADE
    assert con.min_rows > 0, "快照端点行数恒定,是全仓最该设行数下限的两个端点"
    assert con.required_cols, "关键列缺失同样是半截返回的形态"
    assert con.persist_violations is False, "半截/空一旦落盘,path.exists() 恒命中 → 永远残缺"


# ─────────────────── test_hot_rank_registered_as_asof_lake(brief 指名的核心断言) ───────────────────


@pytest.mark.parametrize("ep", ENDPOINTS_UNDER_TEST)
def test_hot_rank_registered_as_asof_lake(ep, lake):
    """两端点在 endpoints 注册表内、契约级别为 B、lake 分区按日;mock fetch 返回实测列的
    假帧,断言写湖后可按 as-of 读回且**剥 fields**(lake 窄表毒化家训:cache key 不含
    fields → 写湖一律全列)。"""
    assert endpoints.policy(ep)["key"] == "as_of"
    assert contracts.CONTRACTS[ep].tier == contracts.TIER_DEGRADE

    frame = _FRAMES[ep]()
    calls: list[dict] = []

    def fetch(endpoint, params):
        calls.append(dict(params))
        return frame

    out = cache.get_or_fetch(ep, {"fields": frame.columns[0]}, today=_TODAY, fetch=fetch)
    assert len(out) == len(frame)
    assert set(frame.columns) <= set(out.columns), "剥 fields:调用方只要窄列,湖里仍须落全列"
    assert "fields" not in calls[0], "_lake_params 必须先剥掉 fields 再喂给底层 fetch"

    path = cache.lake_path(ep, {}, today=_TODAY)
    assert path.exists()
    assert path.name == f"all@{_TODAY}.parquet", "全市场快照,entity 落原生兜底 all;按日分区"

    # as-of 读回:同日再次调用命中湖,零取数,列不变窄。
    out2 = cache.get_or_fetch(ep, {}, today=_TODAY, fetch=fetch)
    assert len(calls) == 1
    assert set(frame.columns) <= set(out2.columns)


def test_weekly_new_follow_gets_its_own_partition(lake):
    """M1:`symbol="本周新增"`(follow7d = 7 日新增关注 = 任务书要的 follow_delta)与「最热门」
    各占一个分区 —— `symbol` 在 `_ENTITY_PARAM_KEYS` 里,原生兜底就给独立键。"""
    cache.get_or_fetch(XQ, {}, today=_TODAY, fetch=lambda e, p: _xq_frame())
    cache.get_or_fetch(XQ, {"symbol": "本周新增"}, today=_TODAY, fetch=lambda e, p: _xq_frame())
    assert (lake / XQ / f"all@{_TODAY}.parquet").exists()
    assert (lake / XQ / f"本周新增@{_TODAY}.parquet").exists()


@pytest.mark.parametrize("ep", ENDPOINTS_UNDER_TEST)
def test_different_days_partition_separately(ep, lake, monkeypatch):
    """快照语义:每晚一份,不回填历史 —— 两天各自成独立分区,互不覆盖。"""
    frame = _FRAMES[ep]()
    monkeypatch.setattr(cache, "_real_today", lambda: "20260808")
    cache.get_or_fetch(ep, {}, today="20260808", fetch=lambda e, p: frame)
    monkeypatch.setattr(cache, "_real_today", lambda: "20260809")
    cache.get_or_fetch(ep, {}, today="20260809", fetch=lambda e, p: frame)
    p1 = cache.lake_path(ep, {}, today="20260808")
    p2 = cache.lake_path(ep, {}, today="20260809")
    assert p1.exists() and p2.exists() and p1 != p2


# ───────────── C2:空/半截返回被记 ✓ 并永久钉进湖(cache 空 pickle 家训的 parquet 同族)─────────────


@pytest.mark.parametrize("ep", ENDPOINTS_UNDER_TEST)
def test_empty_return_degrades_records_and_is_not_nailed(ep, lake):
    """空返回:B 级不阻断(不 raise)、必须记账,**但绝不落盘** —— 落了这一天就永远是空。"""
    calls = []

    def fetch(endpoint, params):
        calls.append(endpoint)
        return pd.DataFrame()

    out = cache.get_or_fetch(ep, {}, today=_TODAY, fetch=fetch)
    assert len(out) == 0                                        # 不阻断
    recs = contracts.degradations()
    assert len(recs) == 1 and recs[0]["endpoint"] == ep and recs[0]["kind"] == "degraded"
    assert not cache.lake_path(ep, {}, today=_TODAY).exists(), "空 parquet 落盘 = 这一天永久为空"

    cache.get_or_fetch(ep, {}, today=_TODAY, fetch=fetch)       # 没钉死 → 同日重跑仍会真取数
    assert calls == [ep, ep]


def test_half_return_is_caught_and_not_nailed(lake, rows_checked):
    """C2 主症:雪球内部分 29 页,单页解析失败被 akshare 自己 `except TypeError` 吞掉 →
    限流时**静默返回 3000 行而不抛异常**。契约必须把它逮成违约,且不许落盘。"""
    calls = []

    def fetch(endpoint, params):
        calls.append(endpoint)
        return _xq_frame(3000)                                   # 29 页只拼上了一半

    out = cache.get_or_fetch(XQ, {}, today=_TODAY, fetch=fetch)
    assert len(out) == 3000
    recs = contracts.degradations()
    assert len(recs) == 1 and any("行数腰斩" in r for r in recs[0]["reasons"])
    assert not cache.lake_path(XQ, {}, today=_TODAY).exists()

    cache.get_or_fetch(XQ, {}, today=_TODAY, fetch=fetch)
    assert calls == [XQ, XQ], "半截没被钉死 → 同日重跑可以自愈"


def test_missing_key_column_is_caught(lake):
    """半截的另一种形态:行数够、关键列没了(窄表毒化的签名)。"""
    bad = _xq_frame().drop(columns=["关注"])
    cache.get_or_fetch(XQ, {}, today=_TODAY, fetch=lambda e, p: bad)
    recs = contracts.degradations()
    assert len(recs) == 1 and any("缺列" in r for r in recs[0]["reasons"])
    assert not cache.lake_path(XQ, {}, today=_TODAY).exists()


def test_mutation_probe_persisting_violations_would_nail_the_half_day(lake, rows_checked):
    """变异探针(还原「违约照样入湖」的旧行为):把 `persist_violations` 改回 True,同一个
    3000 行半截**会落盘**,而且同日重跑因 `path.exists()` 恒命中**再也拉不动** ——
    反向证明上面两条「不落盘 / 能自愈」断言不是碰巧总绿的摆设。"""
    con = contracts.CONTRACTS[XQ]
    monkey = contracts.Contract(tier=con.tier, required_cols=con.required_cols,
                                min_rows=con.min_rows, note=con.note, empty_ok=con.empty_ok,
                                persist_violations=True)
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(contracts.CONTRACTS, XQ, monkey)
        calls = []

        def fetch(endpoint, params):
            calls.append(endpoint)
            return _xq_frame(3000)

        cache.get_or_fetch(XQ, {}, today=_TODAY, fetch=fetch)
        assert cache.lake_path(XQ, {}, today=_TODAY).exists(), "旧行为:半截照样钉进湖"
        cache.get_or_fetch(XQ, {}, today=_TODAY, fetch=fetch)
        assert calls == [XQ], "旧行为:钉死后重跑也自愈不了(湖命中,零取数)"


def test_mutation_probe_without_min_rows_half_return_looks_compliant(lake, rows_checked):
    """变异探针(还原「没有 min_rows」的旧契约):同一个 3000 行半截被判**合规**——
    反向证明是 `min_rows` 在干活,不是别的什么顺带逮住的。"""
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(contracts.CONTRACTS, XQ,
                   contracts.Contract(tier=contracts.TIER_DEGRADE, note="no floor"))
        assert contracts.violations(XQ, _xq_frame(3000)) == []
    assert contracts.violations(XQ, _xq_frame(3000)) != [], "真契约必须逮住同一份输入"


@pytest.mark.parametrize("ep", ENDPOINTS_UNDER_TEST)
def test_full_snapshot_still_lands(ep, lake, rows_checked):
    """反面:完整快照(恰好的行数)照常入湖 —— 别把门修成谁都过不去。"""
    cache.get_or_fetch(ep, {}, today=_TODAY, fetch=lambda e, p: _FRAMES[ep]())
    assert cache.lake_path(ep, {}, today=_TODAY).exists()
    assert contracts.degradations() == []


# ───────────── I1:PIT 错标 —— 快照分区必须是**观测日**,不能是补跑/节假日的目标交易日 ─────────────


@pytest.mark.parametrize("ep", ENDPOINTS_UNDER_TEST)
def test_snapshot_refuses_partition_dated_in_the_past(ep, lake):
    """把今天的快照写成过去某天 = 事后不可甄别的假历史 → 取数前就拒绝。"""
    calls = []
    with pytest.raises(cache.SnapshotDateError):
        cache.get_or_fetch(ep, {}, today="20260807",
                           fetch=lambda e, p: calls.append(e) or _FRAMES[ep]())
    assert calls == [], "守门必须在取数**之前**,别拉完再扔"
    assert not cache.lake_path(ep, {}, today="20260807").exists()


@pytest.mark.parametrize("ep", ENDPOINTS_UNDER_TEST)
def test_snapshot_history_stays_readable(ep, lake):
    """守门只挡**写**新分区:已积累的历史分区照常读得回来(否则明天就没人能用这个湖)。"""
    frame = _FRAMES[ep]()
    path = cache.lake_path(ep, {}, today="20260807")
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path)
    out = cache.get_or_fetch(ep, {}, today="20260807", fetch=lambda e, p: pytest.fail("不该取数"))
    assert len(out) == len(frame)


def test_mutation_probe_without_snapshot_flag_pit_mislabel_happens(lake):
    """变异探针(还原「无 snapshot 旗」的旧行为):同一次调用会把**今天**取到的内容
    落成 `all@20260807.parquet` —— 这正是工作树里那个错标分区的成因。"""
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(endpoints.ENDPOINTS, XQ,
                   {"key": "as_of", "settle": "eod", "source": "akshare"})
        cache.get_or_fetch(XQ, {}, today="20260807", fetch=lambda e, p: _xq_frame())
    assert cache.lake_path(XQ, {}, today="20260807").exists(), "旧行为:错标真的会发生"


# ───────────── I3:观测出处 —— `first_seen_basis="observed"`(任务书 Interfaces 逐字要求)─────────────


@pytest.mark.parametrize("ep", ENDPOINTS_UNDER_TEST)
def test_snapshot_rows_carry_observed_provenance(ep, lake):
    """没有观测时刻列,「这份分片什么时候抓的 / 是不是半截」永久不可回答。"""
    import datetime as _dt

    from autoresearch.news.catalog import BASIS_OBSERVED

    cache.get_or_fetch(ep, {}, today=_TODAY, fetch=lambda e, p: _FRAMES[ep]())
    saved = pd.read_parquet(cache.lake_path(ep, {}, today=_TODAY))
    assert set(saved["first_seen_basis"]) == {BASIS_OBSERVED}, \
        "列名/取值沿用既有 first_seen 词汇表(news/catalog.py),别另起一套"
    stamped = _dt.datetime.fromisoformat(str(saved["first_seen_ts"].iloc[0]))
    assert stamped.tzinfo is not None, "观测时刻必须带时区(夜采跨零点靠它对齐)"


def test_mutation_probe_without_stamping_provenance_is_unanswerable(lake, monkeypatch):
    """变异探针(还原「不打观测戳」的旧行为):湖里没有 first_seen_ts/first_seen_basis 两列。"""
    monkeypatch.setattr(cache, "_stamp_observed", lambda df: df)
    cache.get_or_fetch(XQ, {}, today=_TODAY, fetch=lambda e, p: _xq_frame())
    saved = pd.read_parquet(cache.lake_path(XQ, {}, today=_TODAY))
    assert "first_seen_ts" not in saved.columns and "first_seen_basis" not in saved.columns


@pytest.mark.parametrize("ep", ENDPOINTS_UNDER_TEST)
def test_mutation_probe_without_field_stripping_would_poison_lake(ep, lake, monkeypatch):
    """变异探针(还原"剥 fields"前的行为):若 `_lake_params` 不剥 `fields`,窄 fields
    请求会把窄表钉死成湖快照 —— 反向证明上面"全列落盘"断言真的有鉴别力。"""
    monkeypatch.setattr(cache, "_lake_params", lambda p: p)     # 反向:不剥 fields
    frame = _FRAMES[ep]()
    narrow_col = frame.columns[0]

    def fetch(endpoint, params):
        cols = params.get("fields")
        return frame[[c.strip() for c in cols.split(",")]] if cols else frame

    with pytest.MonkeyPatch.context() as mp:      # 窄表必然缺关键列 → 借旧契约放它落盘
        mp.setitem(contracts.CONTRACTS, ep, contracts.Contract(tier=contracts.TIER_DEGRADE))
        cache.get_or_fetch(ep, {"fields": narrow_col}, today=_TODAY, fetch=fetch)
    saved = pd.read_parquet(cache.lake_path(ep, {}, today=_TODAY))
    assert [c for c in saved.columns if c in set(frame.columns)] == [narrow_col], \
        "反向验证:没有剥离时湖文件确实会被钉成窄表(证明正常路径的剥离断言不是摆设)"


# ───────────── C1:东财人气榜自采第一跳(绕开被封的 push2)─────────────


class _Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _FakeRequests:
    """记录**所有**出网 URL —— 用来证明第二跳(push2)确实没被碰。"""

    def __init__(self, payload):
        self.payload, self.calls = payload, []

    def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        return _Resp(self.payload)

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        return _Resp({"data": {"diff": []}})


def _fake_hop1(rows: int = 100) -> dict:
    return {"data": [{"sc": f"SH{600000 + i}", "rk": i + 1, "rc": 0, "hisRc": 1}
                     for i in range(rows)]}


def test_eastmoney_hot_rank_fetches_only_the_emappdata_hop(monkeypatch):
    """C1 核心:只走第一跳 `emappdata/getAllCurrentList`(200/100 行/keys sc,rk,rc,hisRc);
    **绝不**碰 `push2.eastmoney.com`(记忆判例点名的被封主机,实测 502 —— 它只负责补
    最新价/涨跌幅,而这两列本仓早有 tushare daily)。"""
    from autoresearch.data.sources import eastmoney_hot_rank as em

    fake = _FakeRequests(_fake_hop1())
    monkeypatch.setattr(em, "requests", fake)
    df = em.fetch_hot_rank()

    assert list(df.columns) == ["sc", "rk", "rc", "hisRc"], "原始列不译(T1 决策④)"
    assert len(df) == 100
    urls = [u for _, u, _ in fake.calls]
    assert urls == [em.HOT_RANK_URL]
    assert not any("push2" in u for u in urls), "第二跳被封 → 一碰就是每晚全损"
    assert all(kw.get("timeout") for _, _, kw in fake.calls), "夜跑必须有超时,不能挂死"


def test_eastmoney_hot_rank_raises_on_broken_payload(monkeypatch):
    """返回体形态不对(限频/改版)→ **抛**,不要静默返回空帧假装成功。"""
    from autoresearch.data.sources import eastmoney_hot_rank as em

    monkeypatch.setattr(em, "requests", _FakeRequests({"data": None}))
    with pytest.raises(RuntimeError):
        em.fetch_hot_rank()


def test_sources_fetch_routes_eastmoney_to_selfmade_fetcher(monkeypatch):
    """接线断言:`sources.fetch` 认得 source=eastmoney,不再 getattr 到 akshare 封装。"""
    from autoresearch.data import sources
    from autoresearch.data.sources import eastmoney_hot_rank as em

    monkeypatch.setattr(em, "requests", _FakeRequests(_fake_hop1(3)))
    df = sources.fetch(EM, {})
    assert list(df.columns) == ["sc", "rk", "rc", "hisRc"] and len(df) == 3
