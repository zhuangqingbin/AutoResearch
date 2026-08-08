#!/usr/bin/env python3
"""Wave12 T2 · 热度快照入湖(东财人气/雪球关注)—— endpoints 注册 + B 级契约 + as-of lake。

design: docs/research/2026-08-09-hot-rank-probe.md(T1 探针裁定:两源均可用,列名以
实测为准)。快照语义:每晚一份全量榜单,不回填历史 —— 与 `stock_news_em` 同一族
(`as_of` 键,`{entity}@{as_of}` 分区),区别是这两个端点全市场无 entity 概念,
`entity` 落 `_cache_key` 的原生兜底 `"all"`(空 `params` 调用——T1 报告"移交 T2 的
设计决策"②:`stock_news_em` 先例把 `as_of` 塞进 params 会原样透传进真实 akshare
调用并 TypeError,本组端点不重蹈)。
"""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.data import cache, contracts, endpoints

ENDPOINTS_UNDER_TEST = ("stock_hot_rank_em", "stock_hot_follow_xq")


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


def _hot_rank_em_frame() -> pd.DataFrame:
    """T1 实测列(docs/research/2026-08-09-hot-rank-probe.md)。"""
    return pd.DataFrame({
        "当前排名": [1, 2, 3],
        "代码": ["SH603259", "SZ301308", "SH600664"],
        "股票名称": ["药明康德", "江波龙", "哈药股份"],
        "最新价": [154.82, 386.60, 6.84],
        "涨跌额": [13.14, 21.38, 0.68],
        "涨跌幅": [8.49, 5.53, 9.97],
    })


def _hot_follow_xq_frame() -> pd.DataFrame:
    """T1 实测列;注意没有 `follow_delta`(关注是累计快照,不是增量——见 T1 报告)。"""
    return pd.DataFrame({
        "股票代码": ["SH600519", "SZ000651"],
        "股票简称": ["贵州茅台", "格力电器"],
        "关注": [3696076.0, 2599548.0],
        "最新价": [1309.22, 40.10],
    })


_FRAMES = {"stock_hot_rank_em": _hot_rank_em_frame, "stock_hot_follow_xq": _hot_follow_xq_frame}


# ───────────────────────── 注册表:endpoints policy + contracts tier ─────────────────────────


@pytest.mark.parametrize("ep", ENDPOINTS_UNDER_TEST)
def test_registered_as_asof_akshare_endpoint(ep):
    assert endpoints.policy(ep) == {"key": "as_of", "settle": "eod", "source": "akshare"}


@pytest.mark.parametrize("ep", ENDPOINTS_UNDER_TEST)
def test_registered_as_tier_b_contract(ep):
    assert contracts.CONTRACTS[ep].tier == contracts.TIER_DEGRADE


# ─────────────────── test_hot_rank_registered_as_asof_lake(brief 指名的核心断言) ───────────────────


@pytest.mark.parametrize("ep", ENDPOINTS_UNDER_TEST)
def test_hot_rank_registered_as_asof_lake(ep, lake):
    """两端点在 endpoints 注册表内、契约级别为 B、lake 分区按日;mock fetch 返回 T1
    实测列的假帧,断言写湖后可按 as-of 读回且**剥 fields**(lake 窄表毒化家训:cache
    key 不含 fields → 写湖一律全列)。"""
    assert endpoints.policy(ep)["key"] == "as_of"
    assert contracts.CONTRACTS[ep].tier == contracts.TIER_DEGRADE

    frame = _FRAMES[ep]()
    calls: list[dict] = []

    def fetch(endpoint, params):
        calls.append(dict(params))
        return frame

    out = cache.get_or_fetch(ep, {"fields": "代码"}, today="20260809", fetch=fetch)
    assert len(out) == len(frame)
    assert list(out.columns) == list(frame.columns), "剥 fields:调用方只要窄列,湖里仍须落全列"
    assert "fields" not in calls[0], "_lake_params 必须先剥掉 fields 再喂给底层 fetch"

    path = cache.lake_path(ep, {}, today="20260809")
    assert path.exists()
    assert path.name == "all@20260809.parquet", "全市场快照,entity 落原生兜底 all;按日分区"

    # as-of 读回:同日再次调用命中湖,零取数,列不变窄。
    out2 = cache.get_or_fetch(ep, {}, today="20260809", fetch=fetch)
    assert len(calls) == 1
    assert list(out2.columns) == list(frame.columns)


@pytest.mark.parametrize("ep", ENDPOINTS_UNDER_TEST)
def test_different_days_partition_separately(ep, lake):
    """快照语义:每晚一份,不回填历史 —— 两天各自成独立分区,互不覆盖。"""
    frame = _FRAMES[ep]()
    cache.get_or_fetch(ep, {}, today="20260808", fetch=lambda e, p: frame)
    cache.get_or_fetch(ep, {}, today="20260809", fetch=lambda e, p: frame)
    p1 = cache.lake_path(ep, {}, today="20260808")
    p2 = cache.lake_path(ep, {}, today="20260809")
    assert p1.exists() and p2.exists() and p1 != p2


# ───────────────────────── B 级契约:空 → 降级记账,不阻断 ─────────────────────────


@pytest.mark.parametrize("ep", ENDPOINTS_UNDER_TEST)
def test_empty_return_degrades_and_records_not_raises(ep, lake):
    """B 级:断采只损失当日、不阻断 —— 空返回不 raise,照常入湖(空 parquet)+ 记账。"""
    out = cache.get_or_fetch(ep, {}, today="20260809", fetch=lambda e, p: pd.DataFrame())
    assert len(out) == 0
    assert cache.lake_path(ep, {}, today="20260809").exists()
    recs = contracts.degradations()
    assert len(recs) == 1
    assert recs[0]["endpoint"] == ep and recs[0]["kind"] == "degraded"


# ───────────────────────── 变异探针 ─────────────────────────


@pytest.mark.parametrize("ep", ENDPOINTS_UNDER_TEST)
def test_mutation_probe_tier_a_would_raise_on_same_empty_input(ep, lake, monkeypatch):
    """变异探针(把契约级别改 A):同一个"空返回"输入,B 级只记账不炸,A 级必须炸——
    证明 `test_empty_return_degrades_and_records_not_raises` 真的在验证"不阻断"这件
    事,不是碰巧总是绿的摆设。两条断言各自锁死(Step 4 要求)。"""
    monkeypatch.setitem(contracts.CONTRACTS, ep,
                        contracts.Contract(tier=contracts.TIER_BLOCKING, note="mutation probe"))
    with pytest.raises(contracts.DataContractError):
        cache.get_or_fetch(ep, {}, today="20260809", fetch=lambda e, p: pd.DataFrame())


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

    cache.get_or_fetch(ep, {"fields": narrow_col}, today="20260809", fetch=fetch)
    saved = pd.read_parquet(cache.lake_path(ep, {}, today="20260809"))
    assert list(saved.columns) == [narrow_col], \
        "反向验证:没有剥离时湖文件确实会被钉成窄表(证明正常路径的剥离断言不是摆设)"
