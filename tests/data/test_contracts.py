#!/usr/bin/env python3
"""数据契约:地基数据空/残缺 → 抛异常阻断;增强数据降级但记账。

用户裁定(2026-07-12):"取数以后要有一个全面校验,为空的时候要抛出异常阻断"。

核心命题(每条测试都在守它):**降级必须是显式的、可见的、可审计的**。
反面教材(真实事故):lake 的 daily 被窄表毒化 → `_harvest_vol_series` 静默返回空帧 →
volprice 组整列不落盘 → `composite_score` 把该组从分母剔除、放大其余组权重 → 打分照样输出
0–100、漏斗照样跑完、退出码 0 —— 全市场失真 98.8%,唯一信号是一行淹没的 warn。
"""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.data import cache, contracts
from autoresearch.data.contracts import DataContractError


@pytest.fixture(autouse=True)
def _clean_degradations():
    contracts.clear_degradations()
    yield
    contracts.clear_degradations()


@pytest.fixture
def row_checks(monkeypatch):
    """重新打开**规模性**检查(conftest 全局关掉了它——见那里的注释)。
    只有本文件里专门验证"行数腰斩"逻辑的测试需要它。"""
    monkeypatch.setattr(contracts, "CHECK_ROWS", True)


@pytest.fixture
def lake(tmp_path, monkeypatch):
    root = tmp_path / "lake"
    monkeypatch.setattr(cache, "LAKE", root)
    return root


def _daily(n=5000):
    return pd.DataFrame({"ts_code": [f"{i:06d}.SH" for i in range(n)],
                         "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0,
                         "amount": 1e5, "pct_chg": 0.5})


# ───────────────────────── 纯函数:违约判定 ─────────────────────────


def test_violations_empty_frame():
    assert "空帧(0 行)" in contracts.violations("daily", pd.DataFrame())[0]


def test_violations_none():
    assert contracts.violations("daily", None) == ["返回 None"]


def test_violations_row_count_halved(row_checks):
    """行数腰斩 = 取数半途而废(ChunkedEncodingError 是已知偶发),不是"市场变小了"。"""
    v = contracts.violations("daily", _daily(n=100))
    assert "行数腰斩" in v[0]


def test_row_check_is_scale_only_structural_checks_always_on():
    """开关的边界:关掉规模检查后,**结构性**违约(缺列)仍必须被逮到 —— 窄表毒化的签名正是
    "行数够、但缺列",若两者一起关掉,这个开关就把真正的病也放行了。"""
    narrow = pd.DataFrame({"ts_code": ["600000.SH"], "pct_chg": [1.0]})   # 1 行 + 缺列
    v = contracts.violations("daily", narrow)                             # CHECK_ROWS=False(conftest)
    assert not any("行数腰斩" in x for x in v)                             # 规模检查已关
    assert any("缺列" in x for x in v)                                     # 结构检查仍开


def test_violations_missing_columns_is_the_poisoning_signature(lake):
    """**窄表毒化的签名**:行数够、但缺 high/low/amount → volprice 组会整组 NaN。"""
    narrow = pd.DataFrame({"ts_code": [f"{i:06d}.SH" for i in range(5000)], "pct_chg": 0.5})
    v = contracts.violations("daily", narrow)
    assert any("缺列" in x and "high" in x for x in v)


def test_violations_healthy_frame_is_clean():
    assert contracts.violations("daily", _daily()) == []


def test_violations_unregistered_endpoint_not_checked():
    assert contracts.violations("nonexistent_endpoint", pd.DataFrame()) == []


# ───────────────────────── check:A 级阻断 / B 级记账 ─────────────────────────


def test_check_tier_a_empty_raises_with_actionable_message():
    """A 级空 → 抛,且错误信息必须说清**为什么阻断**(否则下次又有人加个 try/except 吞掉它)。"""
    with pytest.raises(DataContractError) as e:
        contracts.check("daily", pd.DataFrame(), key="20260709", source="lake")
    msg = str(e.value)
    assert "A级" in msg and "daily" in msg
    assert "残废得看不出来" in msg, "必须解释静默失真的机制"
    assert "doctor --purge" in msg, "必须给自愈路径"


def test_check_tier_b_missing_degrades_and_records_not_raises():
    """B 级(北向)空 = 合法(2026-07-08/09 就真的没有)→ 不阻断,但**记账**。"""
    out = contracts.check("hk_hold", pd.DataFrame(), key="20260709", source="fetch")
    assert out is not None and len(out) == 0
    recs = contracts.degradations()
    assert len(recs) == 1
    assert recs[0]["endpoint"] == "hk_hold" and recs[0]["key"] == "20260709"


def test_check_healthy_frame_passes_through_untouched():
    df = _daily()
    assert contracts.check("daily", df, key="20260709") is df


def test_render_degradations_summarises_by_endpoint():
    contracts.check("hk_hold", pd.DataFrame(), key="d1")
    contracts.check("hk_hold", pd.DataFrame(), key="d2")
    contracts.check("pledge_stat", pd.DataFrame(), key="d1")
    line = contracts.render()
    assert "hk_hold×2" in line and "pledge_stat×1" in line
    contracts.clear_degradations()
    assert contracts.render() == ""


# ───────────────────────── cache 接线:三条路径 ─────────────────────────


def test_tier_a_empty_fetch_raises_and_refuses_to_write_lake(lake):
    """**最关键的一条**:A 级取到空 → 抛,且**绝不入湖**。
    脏数据一旦落盘就被钉死(`path.exists()` → 命中),之后每次都是脏的,重跑也自愈不了
    —— 这正是 hk_hold 14 个空帧、daily 两个窄表的产生方式。"""
    with pytest.raises(DataContractError):
        cache.get_or_fetch("daily", {"trade_date": "20240102"}, today="20260712",
                           fetch=lambda e, p: pd.DataFrame())
    assert not cache.lake_path("daily", {"trade_date": "20240102"}).exists(), \
        "脏数据绝不能入湖 —— 否则它会被钉死,重跑也读到它"


def test_tier_a_poisoned_lake_hit_also_raises(lake):
    """**湖命中路径同样校验** —— 原设计的盲区:历史脏数据读出来照样毒化下游,
    而且它不会再经过取数路径的任何检查。"""
    path = cache.lake_path("daily", {"trade_date": "20260709"})
    path.parent.mkdir(parents=True, exist_ok=True)
    narrow = pd.DataFrame({"ts_code": [f"{i:06d}.SH" for i in range(5000)], "pct_chg": 0.5})
    narrow.to_parquet(path)                       # 模拟真实事故:窄表已躺在湖里

    with pytest.raises(DataContractError, match="湖命中"):
        cache.get_or_fetch("daily", {"trade_date": "20260709"}, today="20260712",
                           fetch=lambda e, p: _daily())


def test_tier_b_empty_writes_lake_and_records_degradation(lake):
    """B 级空是合法的(真实的空)→ 照常入湖(避免每天重拉一次空),但必须记账。"""
    out = cache.get_or_fetch("hk_hold", {"trade_date": "20240102"}, today="20260712",
                             fetch=lambda e, p: pd.DataFrame())
    assert len(out) == 0
    assert cache.lake_path("hk_hold", {"trade_date": "20240102"}).exists()
    assert contracts.degradations()[0]["endpoint"] == "hk_hold"


def test_healthy_tier_a_still_writes_and_hits(lake):
    """parity:合规数据一切照旧(写湖 → 二次命中零取数)。"""
    calls = []
    fetch = lambda e, p: calls.append(1) or _daily()          # noqa: E731
    cache.get_or_fetch("daily", {"trade_date": "20240102"}, today="20260712", fetch=fetch)
    cache.get_or_fetch("daily", {"trade_date": "20240102"}, today="20260712", fetch=fetch)
    assert len(calls) == 1


def test_live_endpoint_bypasses_contract(lake):
    """live 端点(盘中快照)不入湖、不校验 —— 完整性由调用方自负。"""
    out = cache.get_or_fetch("stock_zt_pool_em", {}, today="20260712",
                             fetch=lambda e, p: pd.DataFrame())
    assert len(out) == 0                                       # 不抛


# ───────────────────────── 因子帧出口契约 ─────────────────────────


def _frame(n=4000, **over):
    df = pd.DataFrame({"code": [f"{i:06d}" for i in range(n)], "close": 10.0,
                       "mktcap_yi": 100.0, "pct_60d": 5.0, "main_net_ratio": 0.01,
                       "cmf_20": 0.1, "obv_mom_20": 0.2})
    for k, v in over.items():
        df[k] = v
    return df


def test_check_market_frame_healthy():
    df = _frame()
    assert contracts.check_market_frame(df) is df


def test_check_market_frame_missing_volprice_is_the_real_accident():
    """真实事故的最后一道门:cmf_20/obv_mom_20 整列消失 → 必须炸,不能让它进 composite_score。"""
    df = _frame().drop(columns=["cmf_20", "obv_mom_20"])
    with pytest.raises(DataContractError, match="cmf_20"):
        contracts.check_market_frame(df)


def test_check_market_frame_all_nan_column_also_caught():
    """列在场但整列全 NaN = 同样的死法(列检查会被骗过去)。"""
    with pytest.raises(DataContractError, match="整列全 NaN"):
        contracts.check_market_frame(_frame(cmf_20=float("nan")))


def test_check_market_frame_skips_volprice_when_explicitly_disabled():
    """`with_vol_series=False`(盘前省时路径)= **显式跳过**,与静默丢失是两回事。"""
    df = _frame().drop(columns=["cmf_20", "obv_mom_20"])
    assert contracts.check_market_frame(df, with_vol_series=False) is df


def test_check_market_frame_empty_and_halved(row_checks):
    with pytest.raises(DataContractError, match="因子帧为空"):
        contracts.check_market_frame(pd.DataFrame())
    with pytest.raises(DataContractError, match="行数腰斩"):
        contracts.check_market_frame(_frame(n=100))


# ───────────────────────── 湖体检 doctor ─────────────────────────


def test_doctor_finds_and_purges_tier_a_poison_but_keeps_tier_b_empties(lake):
    """A 级毒源(窄表/空帧)= 必须清;B 级空帧多为真实的空 → **不删**(删了只会每天重拉一次空)。"""
    (lake / "daily").mkdir(parents=True)
    (lake / "hk_hold").mkdir(parents=True)
    pd.DataFrame({"ts_code": ["600000.SH"], "pct_chg": [1.0]}).to_parquet(
        lake / "daily" / "20260709.parquet")                      # 窄表毒源
    _daily().to_parquet(lake / "daily" / "20260708.parquet")      # 健康
    pd.DataFrame().to_parquet(lake / "hk_hold" / "20260709.parquet")   # B 级真实空

    res = contracts.doctor(lake)
    assert len(res["blocking"]) == 1 and "20260709" in res["blocking"][0]["file"]
    assert len(res["degraded"]) == 1                              # hk_hold 空 = 记录但不算毒源

    res2 = contracts.doctor(lake, purge=True)
    assert len(res2["purged"]) == 1
    assert not (lake / "daily" / "20260709.parquet").exists()     # 毒源已清 → 下次取数重拉
    assert (lake / "daily" / "20260708.parquet").exists()         # 健康的不动
    assert (lake / "hk_hold" / "20260709.parquet").exists()       # B 级空帧保留


# ───────────────────────── 降级可见性:落盘 + 进报告 ─────────────────────────


def test_degraded_json_written_by_universe_run_and_rendered_in_report(tmp_path):
    """**降级必须可见**(本模块存在的另一半理由):B 级降级 → `degraded.json` → 报告里一行。
    否则读报告的人无从分辨某面旗是"真没有"还是"没取到"。"""
    import json

    from autoresearch.scan import assemble

    scan = tmp_path / "2026-07-12"
    scan.mkdir()
    assert assemble._degraded_line(scan) == ""            # presence-gated:无文件 → 不加行

    (scan / "degraded.json").write_text(json.dumps([
        {"endpoint": "hk_hold", "key": "20260712", "source": "fetch", "reasons": ["空帧(0 行)"]},
        {"endpoint": "hk_hold", "key": "20260711", "source": "fetch", "reasons": ["空帧(0 行)"]},
        {"endpoint": "pledge_stat", "key": "x", "source": "fetch", "reasons": ["空帧(0 行)"]},
        {"endpoint": "forecast", "key": "20260712", "source": "fetch",
         "reasons": ["空帧(0 行)"], "kind": "legit_empty"},          # 合法空:留痕在 json
    ]), encoding="utf-8")
    line = assemble._degraded_line(scan)
    assert "数据降级" in line and "hk_hold×2" in line and "pledge_stat×1" in line
    assert "forecast" not in line                          # ……但不进报告告警行(Minor-1)


def test_record_degradation_covers_non_cache_paths():
    """不走 `cache.get_or_fetch` 的降级点(直接调 tushare 的 `_fetch_hk_hold` 就是)也必须记账
    —— 2026-07-09 的 hk_ratio 全表为空,当时唯一的痕迹是一行没人看见的 warn。"""
    contracts.record_degradation("hk_hold", "空返回(该日无北向数据)→ north 组置空", key="20260709")
    recs = contracts.degradations()
    assert recs[0]["endpoint"] == "hk_hold" and recs[0]["source"] == "direct"
    assert "hk_hold×1" in contracts.render()


# ───────────── anns_d 退役 + 合法空告警面(survey 2026-07-13 线 D · 2026-07-18)─────────────


def test_anns_d_retired_note_but_contract_and_degrade_path_kept():
    """anns_d 正式退役:note 说清由谁覆盖(news_em 头条 + intel 盲搜);**端点注册与 B 级降级
    路径保留** —— 残余调用(l3_news harvest 等)仍优雅降级,不抛、照记账。"""
    con = contracts.CONTRACTS["anns_d"]
    assert con.tier == contracts.TIER_DEGRADE
    assert "已退役" in con.note and "stock_news_em" in con.note and "expected" in con.note
    out = contracts.check("anns_d", pd.DataFrame(), key="20260718")     # 残余调用
    assert out is not None and len(out) == 0                            # 不阻断
    assert contracts.degradations()[0]["endpoint"] == "anns_d"          # 仍记账


def test_legit_empty_recorded_but_not_rendered():
    """forecast/express/stk_surv 某交易日 0 行 = 真实合法空 → **留痕**(degradations/degraded.json
    审计不丢)但**不进告警渲染** —— 告警面只留"今天坏了"类真降级(Minor-1)。"""
    for ep in ("forecast", "express", "stk_surv"):
        contracts.check(ep, pd.DataFrame(), key="20260718")
    recs = contracts.degradations()
    assert len(recs) == 3 and all(r["kind"] == "legit_empty" for r in recs)
    assert contracts.render() == ""


def test_no_permission_and_error_shapes_still_rendered():
    """无权限空(anns_d,无 empty_ok)与报错形态(forecast 返回 None)**不是**合法空 →
    照旧进告警渲染。empty_ok 只豁免"恰为空帧"这一种形态。"""
    contracts.check("anns_d", pd.DataFrame(), key="d")     # 无权限恒空:仍告警(呈现面另行标 expected)
    contracts.check("forecast", None, key="d")             # 返回 None = 报错降级,非合法空
    line = contracts.render()
    assert "anns_d×1" in line and "forecast×1" in line


def test_mixed_records_render_only_real_degradations():
    contracts.check("forecast", pd.DataFrame(), key="d")   # 合法空 → 滤掉
    contracts.check("hk_hold", pd.DataFrame(), key="d")    # 增强缺失 → 保留
    line = contracts.render()
    assert "hk_hold×1" in line and "forecast" not in line


def test_render_backward_compatible_with_kindless_records():
    """旧 degraded.json(无 `kind` 字段)→ 按降级渲染 —— 老现场不因新字段静默消失。"""
    line = contracts.render([{"endpoint": "hk_hold", "key": "x", "source": "fetch",
                              "reasons": ["空帧(0 行)"]}])
    assert "hk_hold×1" in line
