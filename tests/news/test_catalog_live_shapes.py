#!/usr/bin/env python3
"""Wave12-T35:news_catalog「通电」—— inventory 修正 + 三源快讯 ingest(**离线 fixture**)。

`autoresearch/news/` 四模块 2026-08-04 就代码落地,却**零生产调用点**,连
`catalog inventory` 都没跑过一次 —— 「建好的仓库没通电」。本文件锁住通电三步里
能离线锁的部分。

**测试禁挂真网**:三个 akshare 端点全部走 `fetch` 注入的桩。下面的 fixture 列名与行
形状抄自 2026-08-09 的**真实**首跑读数(见各 fixture 注),不是凭空捏的 ——
「字段语义误读只有真数据能证伪」的家训:fake fetch 用作者的假设当输入,单测和 review
会一起瞎。所以这里额外有一条把 fixture 列名与 `FLASH_SOURCES` 声明对上的契约测试。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from autoresearch.news import catalog as C
from autoresearch.news.catalog import CST

# ── 2026-08-09 真实首跑读数(逐字抄列名)───────────────────────────────
#   stock_info_global_em   : 200 行 · ['标题', '摘要', '发布时间', '链接']
#   stock_info_global_sina :  20 行 · ['时间', '内容']            ← 无标题、无 url
#   stock_info_cjzc_em     : 400 行 · ['标题', '摘要', '发布时间', '链接']
_EM = pd.DataFrame([
    {"标题": "大幅拉升!霍尔木兹海峡新变数", "摘要": "【大幅拉升!霍尔木兹海峡新变数】原油暗盘集体异动",
     "发布时间": "2026-08-09 12:43:00", "链接": "https://finance.eastmoney.com/a/20260809"},
    {"标题": "两市成交额破万亿", "摘要": "【两市成交额破万亿】沪深两市今日成交额合计……",
     "发布时间": "2026-08-09 11:30:00", "链接": "https://finance.eastmoney.com/a/20260809b"},
])
_SINA = pd.DataFrame([
    {"时间": "2026-08-09 13:17:00", "内容": "【百家机构调研股曝光,两股上榜】近一周机构调研个股有6家……"},
    {"时间": "2026-08-09 13:05:00", "内容": "无括号头的一条纯文本快讯,用来验证标题回退到截断分支。"},
])
_CJZC = pd.DataFrame([
    {"标题": "东方财富财经早餐 8月7日周五", "摘要": "【东方财富财经早餐 8月7日周五】1、宇树科技……",
     "发布时间": "2026-08-07 06:00:35", "链接": "http://finance.eastmoney.com/a/202608063"},
])
_FRAMES = {"stock_info_global_em": _EM, "stock_info_global_sina": _SINA,
           "stock_info_cjzc_em": _CJZC}


def _fetch(endpoint):
    return _FRAMES[endpoint].copy()


def _cat(tmp_path):
    return C.NewsCatalog(tmp_path / "news_catalog")


# ───────────────────────── 源契约:fixture 与声明必须对得上 ─────────────────────────


def test_flash_source_specs_match_real_column_names():
    """`FLASH_SOURCES` 声明的列名必须真的存在于对应源的帧里。

    这是本文件唯一防"作者假设"的锁:fixture 抄自真实首跑,声明抄错列名会当场红,
    而不是等到某天夜里 ingest 静默出 0 行(wave3 家训:字段语义误读只有真数据能证伪)。
    """
    for source, spec in C.FLASH_SOURCES.items():
        frame = _FRAMES[spec["endpoint"]]
        for key in ("title", "body", "ts", "url"):
            col = spec[key]
            if col is None:
                continue
            assert col in frame.columns, f"{source}.{key}={col!r} 不在 {list(frame.columns)}"
    # 新浪那条"没有标题也没有 url"是**实测事实**,不是暂缺 —— 写死防止有人想当然补回来
    assert C.FLASH_SOURCES["global_sina"]["title"] is None
    assert C.FLASH_SOURCES["global_sina"]["url"] is None


def test_flash_title_prefers_bracket_headline():
    assert C.flash_title("【百家机构调研股曝光】近一周……") == "百家机构调研股曝光"
    long = "无括号头" * 30
    assert C.flash_title(long, fallback_len=12) == long[:12]
    assert C.flash_title("") == ""


# ───────────────────────── flash_observations:PIT 与 scope 纪律 ─────────────────────────


def test_first_seen_is_ingest_time_not_published_time():
    """`first_seen_ts` = 我们**抓到**的时刻,不是来源自称的发布时刻(§1.1 PIT 纪律)。

    cjzc 那条自称 08-07 发布,今晚(08-09)才被抓到 —— 它在 08-08 的决策里不存在。
    """
    now = "2026-08-09T20:00:00+00:00"
    obs = C.flash_observations("cjzc_em", _CJZC, now=now)
    assert len(obs) == 1
    assert obs[0].first_seen_ts == now
    assert obs[0].published_ts.startswith("2026-08-07")
    assert obs[0].first_seen_basis == C.BASIS_OBSERVED     # 新抓取一律 observed


def test_late_arrival_does_not_leak_into_earlier_cutoff(tmp_path):
    """晚到不泄漏:08-07 发布、08-09 抓到的那条,回放到 08-08 必须**看不见**。

    这条是整套 PIT 纪律的核心 —— `published_ts` 只是来源陈述,不能替代 first-seen。
    """
    cat = _cat(tmp_path)
    cat.ingest(C.flash_observations("cjzc_em", _CJZC, now="2026-08-09T20:00:00+00:00"))
    assert len(cat.replay(None, "2026-08-08T23:59:59+00:00", "L3")) == 0
    assert len(cat.replay(None, "2026-08-09T23:59:59+00:00", "L3")) == 1


def test_flash_scope_is_market_wide_and_heat_eligible(tmp_path):
    """三源是固定口径全局 feed → `market_wide`,是目前唯一有资格算市场热度的观测。"""
    cat = _cat(tmp_path)
    C.ingest_flash(catalog=cat, fetch=_fetch, now="2026-08-09T20:00:00+00:00")
    obs = cat.observations()
    assert set(obs["scope"]) == {C.SCOPE_MARKET_WIDE}
    assert len(cat.market_heat_eligible()) == len(obs)
    cat.assert_not_selective(obs, context="市场新闻量")     # 不该抛


def test_selective_observations_still_rejected_for_market_heat(tmp_path):
    """对照:逐票采集(selective)仍然被拒 —— 通电没有放松选择偏差那道闸。"""
    cat = _cat(tmp_path)
    cat.ingest([C.Observation(source="stock_news_em", title="某票公告",
                              first_seen_ts="2026-08-09T20:00:00+00:00",
                              scope=C.SCOPE_SELECTIVE, codes=("000651",))])
    with pytest.raises(C.CatalogError, match="selective"):
        cat.assert_not_selective(cat.observations(), context="市场新闻量")


def test_unknown_source_raises_in_pure_mapper():
    with pytest.raises(C.CatalogError, match="未知快讯源"):
        C.flash_observations("nope", _EM)


def test_empty_frame_yields_no_observations():
    assert C.flash_observations("global_em", pd.DataFrame()) == []
    assert C.flash_observations("global_em", None) == []


# ───────────────────────── ingest_flash:幂等 / B 级降级 ─────────────────────────


def test_ingest_flash_ingests_all_three_sources(tmp_path):
    cat = _cat(tmp_path)
    res = C.ingest_flash(catalog=cat, fetch=_fetch, now="2026-08-09T20:00:00+00:00")
    assert res["added"] == 5 and res["revised"] == 0 and res["unchanged"] == 0
    assert res["any_ok"] is True
    assert {s["source"]: s["status"] for s in res["per_source"]} == {
        "global_em": "OK", "global_sina": "OK", "cjzc_em": "OK"}
    assert res["health"]["first_seen_missing_rate"] == 0.0     # §1.1 契约:恒 0


def test_ingest_flash_is_idempotent(tmp_path):
    """重跑同一份数据 → 全部 `unchanged`,不产生新行。

    夜间腿会天天跑,而三个源的窗口是滚动的 —— 不幂等的话目录会以每晚 620 行的速度
    灌水,`n_observations` 这个读数也就再也不能当"我们到底看到了多少条"用。
    """
    cat = _cat(tmp_path)
    first = C.ingest_flash(catalog=cat, fetch=_fetch, now="2026-08-09T20:00:00+00:00")
    # 第二晚:同样的稿子又出现在窗口里(first_seen 更晚,但内容没变)
    second = C.ingest_flash(catalog=cat, fetch=_fetch, now="2026-08-10T20:00:00+00:00")
    assert second["added"] == 0 and second["revised"] == 0
    assert second["unchanged"] == first["added"]
    assert second["health"]["n_observations"] == first["health"]["n_observations"]


def test_ingest_flash_content_change_becomes_new_revision_not_overwrite(tmp_path):
    """同一 url 内容变了(更正)→ 新 revision,旧版本**保留**(§1.1 身份纪律)。"""
    cat = _cat(tmp_path)
    C.ingest_flash(["global_em"], catalog=cat, fetch=_fetch, now="2026-08-09T20:00:00+00:00")

    corrected = _EM.copy()
    corrected.loc[0, "摘要"] = "【更正】原油暗盘异动幅度修正为 3.1%"
    res = C.ingest_flash(["global_em"], catalog=cat,
                         fetch=lambda ep: corrected, now="2026-08-10T20:00:00+00:00")
    assert res["revised"] == 1 and res["added"] == 0
    obs = cat.observations()
    assert len(obs) == 3, "旧版本必须留着,更正是新增一行不是覆盖"
    assert sorted(obs["revision"].tolist()) == ["0", "0", "1"]


def test_ingest_flash_one_source_down_does_not_kill_the_others(tmp_path):
    """B 级:一个源挂了只降级记账,其余照常入库 —— 且**降级必须留痕**。"""
    def _flaky(endpoint):
        if endpoint == "stock_info_global_sina":
            raise TimeoutError("sina 超时")
        return _FRAMES[endpoint].copy()

    cat = _cat(tmp_path)
    res = C.ingest_flash(catalog=cat, fetch=_flaky, now="2026-08-09T20:00:00+00:00")
    by = {s["source"]: s for s in res["per_source"]}
    assert by["global_sina"]["status"] == "FETCH_FAILED"
    assert "TimeoutError" in by["global_sina"]["error"], "降级不留痕才是真病"
    assert by["global_em"]["status"] == "OK" and by["cjzc_em"]["status"] == "OK"
    assert res["added"] == 3 and res["any_ok"] is True


def test_ingest_flash_all_sources_down_reports_any_ok_false(tmp_path):
    """三源全挂 → `any_ok=False`(夜间腿据此记 ✗)。**不抛** —— B 级不阻断。"""
    def _dead(endpoint):
        raise ConnectionError("网络不可达")

    res = C.ingest_flash(catalog=_cat(tmp_path), fetch=_dead)
    assert res["any_ok"] is False and res["added"] == 0
    assert all(s["status"] == "FETCH_FAILED" for s in res["per_source"])


def test_ingest_flash_schema_drift_degrades_not_crashes(tmp_path):
    """列名被上游改掉(schema drift)→ 记 SHAPE_ERROR/EMPTY,不炸整晚。"""
    renamed = _EM.rename(columns={"摘要": "内容摘要"})
    res = C.ingest_flash(["global_em"], catalog=_cat(tmp_path),
                         fetch=lambda ep: renamed, now="2026-08-09T20:00:00+00:00")
    # 标题列还在 → 仍能出观测;真正致命的是标题+正文双缺,那时观测数为 0 且 status=EMPTY
    assert res["per_source"][0]["status"] in {"OK", "EMPTY", "SHAPE_ERROR"}
    assert res["any_ok"] in {True, False}          # 不抛就是本条的验收

    gone = _EM.rename(columns={"摘要": "x", "标题": "y"})
    res2 = C.ingest_flash(["global_em"], catalog=_cat(tmp_path / "b"),
                          fetch=lambda ep: gone, now="2026-08-09T20:00:00+00:00")
    assert res2["per_source"][0]["status"] == "EMPTY" and res2["added"] == 0


def test_ingest_flash_unknown_source_name_is_recorded(tmp_path):
    res = C.ingest_flash(["not_a_source"], catalog=_cat(tmp_path), fetch=_fetch)
    assert res["per_source"] == [{"source": "not_a_source", "status": "UNKNOWN_SOURCE",
                                  "rows": 0}]


# ───────────────────────── inventory:首跑修出的两处真问题 ─────────────────────────


def test_inventory_reports_true_date_span_not_code_order(tmp_path):
    """`first_key/last_key` 是**代码序**头尾,不是日期跨度 —— 2026-08-09 首跑实测:
    first=`000012@20260625`、last=`920982@20260624`,末尾那份日期反而更早。
    `date_min/date_max` 必须另算,否则读表的人会把代码序当成时间范围。
    """
    lake = tmp_path / "lake"
    folder = lake / "stock_news_em"
    folder.mkdir(parents=True)
    for key in ("000012@20260625", "920982@20260624", "600000@20260701"):
        (folder / f"{key}.parquet").write_bytes(b"x" * 10)

    inv = C.inventory(lake_root=lake, fallback_cache=tmp_path / "nope")
    src = inv["sources"]["stock_news_em"]
    assert src["first_key"] == "000012@20260625"      # 代码序头(**不是**最早)
    assert src["last_key"] == "920982@20260624"       # 代码序尾(日期反而更早)
    assert src["date_min"] == "20260624" and src["date_max"] == "20260701"
    assert src["n_shards"] == 3 and src["bytes"] == 30


def test_inventory_date_span_is_none_when_unparseable(tmp_path):
    """分片名不带 `@YYYYMMDD` → 跨度 None(**不猜**)。"""
    folder = tmp_path / "lake" / "stock_news_em"
    folder.mkdir(parents=True)
    (folder / "weird-name.parquet").write_bytes(b"x")
    src = C.inventory(lake_root=tmp_path / "lake")["sources"]["stock_news_em"]
    assert src["date_min"] is None and src["date_max"] is None


def test_inventory_counts_nested_anns_fallback_cache(tmp_path):
    """`anns_fallback` 真实布局是 `<date>/<code>.v1.json` —— 顶层 `glob("*.json")`
    恒为 0,而 0 长得和"没缓存"一模一样。这条锁住 rglob(首跑发现的第二处真问题)。
    """
    cache = tmp_path / "anns_fallback" / "2026-08-09"
    cache.mkdir(parents=True)
    (cache / "000651.v1.json").write_text(json.dumps({"source": "cninfo"}), encoding="utf-8")
    (cache / "600000.v1.json").write_text(json.dumps({"source": "cninfo"}), encoding="utf-8")

    inv = C.inventory(lake_root=tmp_path / "lake", fallback_cache=tmp_path / "anns_fallback")
    assert inv["sources"]["anns_fallback"]["n_shards"] == 2, "顶层 glob 会数成 0(旧 bug)"
    assert inv["total_shards"] >= 2


def test_inventory_labels_retired_source(tmp_path):
    """`anns_d` 的 0 分片是**预期**(2026-07-18 退役),必须标出来,否则会被当成丢数据。"""
    inv = C.inventory(lake_root=tmp_path / "lake", fallback_cache=tmp_path / "nope")
    assert "retired" in inv["sources"]["anns_d"]
    assert "retired" not in inv["sources"]["stock_news_em"]


# ───────────────────────── health:prelude 报表行读的那几个数 ─────────────────────────


def test_health_numbers_prelude_row_depends_on(tmp_path):
    """prelude 那行读的是 health 的 n_observations / by_source / first_seen_missing_rate
    与 `market_heat_eligible()` 的长度 —— 这几个键改名会让报表行静默变空,单独锁一遍。
    """
    cat = _cat(tmp_path)
    C.ingest_flash(catalog=cat, fetch=_fetch, now="2026-08-09T20:00:00+00:00")
    h = cat.health()
    assert h["n_observations"] == 5
    assert set(h["by_source"]) == {"global_em", "global_sina", "cjzc_em"}
    assert h["by_basis"] == {"observed": 5}
    assert h["first_seen_missing_rate"] == 0.0
    assert len(cat.market_heat_eligible()) == 5


def test_prelude_step_is_wired_and_reads_catalog():
    """接线锁(FN-1 家族):`prelude.run_prelude` 的步骤表里必须真的有 news_catalog。

    「消费者读没人生产的产物」的镜像 —— 通电三步的第三步要是没接上,目录会重新变回
    "跑过一次然后没人知道它死没死"。
    """
    import inspect

    from autoresearch.scan import prelude
    src = inspect.getsource(prelude.run_prelude)
    assert '("news_catalog", _news_catalog)' in src
    assert "market_heat_eligible" in src, "报表行必须区分市场口径与 selective"
    # I5:历史腿(snapshot_inferred)入没入目录必须在报表行上看得见 —— 首版整条缺席
    # 而报表行照样绿,正是因为它只报总数、不报 basis 构成。
    assert "by_basis" in src, "报表行必须显示 observed / snapshot_inferred 构成"
    # I6:时区可疑源要报出来(探针长在数据上,不只长在测试里)
    assert "tz_suspect_sources" in src


def test_flash_observations_visibility_gated_by_first_seen_not_published():
    """可见性由 first_seen 决定,不由 published 抢跑。

    🚨 **本条是改写版**(Wave12-T35 修复轮 1 · I6)。原版叫
    `test_flash_observations_recent_publish_still_gated_by_first_seen`,末行断言
    `published_ts > first_seen_ts` 并注释成「来源陈述可以更晚,不影响可见性」——
    那正是**给 bug 发合法身份证**的假绿灯:上线首日 `global_sina` **25/25** 条的
    `published − first_seen` 落在 +7.44~+7.99h,是 UTC+8 被当 UTC 的签名,而这条测试
    会把它一路放行。现在改成:仍然验"可见性看 first_seen",但**不再**给"published 超前"
    背书 —— 超前由下面的 `test_published_never_far_ahead_of_first_seen` 专门盯。
    """
    now = datetime(2026, 8, 9, 20, tzinfo=timezone.utc)
    # 来源自称 08-07 发布(过去),我们现在才抓到 → 可见性从 now 起算
    frame = pd.DataFrame([{"标题": "两天前的稿子", "摘要": "x",
                           "发布时间": "2026-08-07 09:00:00", "链接": "u"}])
    obs = C.flash_observations("global_em", frame, now=now.isoformat())
    assert obs[0].first_seen_ts == now.isoformat()
    assert obs[0].published_ts < obs[0].first_seen_ts


def test_published_ts_parsed_as_beijing_time_not_utc():
    """[I6] 来源自报墙钟是**北京时间**,必须按 CST 解析。

    `2026-08-09 12:43:00` 是北京时间 → UTC 04:43。当 UTC 解析会得到 12:43Z,整体偏 +8h。
    """
    obs = C.flash_observations("global_em", _EM.head(1), now="2026-08-09T20:00:00+00:00")
    pub = datetime.fromisoformat(obs[0].published_ts)
    assert pub.utcoffset() == timedelta(hours=8), "published 必须带 +08:00"
    assert pub.astimezone(timezone.utc).hour == 4, (
        f"12:43 北京时间应 = 04:43 UTC,实际 {pub.astimezone(timezone.utc)}")


def test_published_never_far_ahead_of_first_seen():
    """[I6 真正的判据] 抓到的那一刻,来源自称的发布时间**不该在未来**。

    这条才是能逮住时区错标的断言:sina 的稿子是"刚刚发布"的直播流,`published` 至多与
    `first_seen` 齐平;一旦整源系统性超前(尤其 ≈+8h),就是时区标错。
    原来的测试反而把这种超前断言成合法 —— 假绿灯的教科书形态。
    """
    now = datetime(2026, 8, 9, 13, 30, tzinfo=CST)          # 抓取时刻(北京时间 13:30)
    for source, frame in (("global_em", _EM), ("global_sina", _SINA), ("cjzc_em", _CJZC)):
        for o in C.flash_observations(source, frame, now=now.isoformat()):
            if o.published_ts is None:
                continue
            ahead = (datetime.fromisoformat(o.published_ts) - now).total_seconds() / 3600
            assert ahead <= 0.5, (
                f"{source} 的 published 比抓取时刻超前 {ahead:.2f}h —— 时区错标的签名"
                f"(published={o.published_ts} first_seen={o.first_seen_ts})")


def test_health_flags_timezone_mislabelled_source(tmp_path):
    """[I6 活体探针] `health()` 必须把整源系统性超前标成 `tz_suspect_sources`。

    造 fixture 的单测锁不住"下一个源又写错时区",这个读数能 —— 探针要长在**数据**上。
    """
    cat = _cat(tmp_path)
    seen = "2026-08-09T05:30:00+00:00"
    cat.ingest([C.Observation(source="badtz", title="错标时区的源",
                              published_ts="2026-08-09T13:20:00+00:00",   # 实为北京时间被当 UTC
                              first_seen_ts=seen, url="u1"),
                C.Observation(source="goodtz", title="正常的源",
                              published_ts="2026-08-09T05:10:00+00:00",
                              first_seen_ts=seen, url="u2")])
    h = cat.health()
    assert h["tz_suspect_sources"] == ["badtz"]
    assert h["published_ahead_hours_max"] > 7


# ═══ Wave12-T35 修复轮 1 · I5:历史湖分片入目录(snapshot_inferred)+ 逐源对账 ═══
#
# 首版把 Step 1 做成了「只数文件」:1,891 个 stock_news_em 分片一条都没进目录,
# 生产 source_observation.csv 626 行 100% observed、snapshot_inferred 0 行。
# 下面的 fixture 列名抄自 2026-08-09 实读的 `000012@20260625.parquet`。

_SHARD_COLS = ["关键词", "新闻标题", "新闻内容", "发布时间", "文章来源", "新闻链接"]


def _write_shard(root, code, date, rows):
    d = root / "stock_news_em"
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=_SHARD_COLS).to_parquet(d / f"{code}@{date}.parquet")
    return d / f"{code}@{date}.parquet"


def _row(code, title, url="", ts="2026-06-22 10:53:00", body="正文"):
    return {"关键词": code, "新闻标题": title, "新闻内容": body,
            "发布时间": ts, "文章来源": "证券时报网", "新闻链接": url}


def test_lake_shard_columns_match_real_parquet_shape():
    """声明的列名必须与真实分片对得上(同 flash 那条契约测试的用意)。"""
    spec = C._LAKE_SHARD_COLUMNS["stock_news_em"]
    for key in ("title", "body", "ts", "url", "code"):
        assert spec[key] in _SHARD_COLS, f"{key}={spec[key]!r} 不在真实列 {_SHARD_COLS}"


def test_lake_shards_ingest_as_snapshot_inferred(tmp_path):
    """[I5 核心] 历史分片必须标 `snapshot_inferred`,时间取分片 mtime。"""
    lake = tmp_path / "lake"
    _write_shard(lake, "000012", "20260625", [_row("000012", "今日149只个股突破五日均线", "u1")])
    cat = _cat(tmp_path)
    res = C.ingest_lake_shards(lake_root=lake, catalog=cat)

    assert res["added"] == 1 and res["reconciled"] is True
    obs = cat.observations()
    assert obs.iloc[0]["first_seen_basis"] == C.BASIS_SNAPSHOT
    assert obs.iloc[0]["scope"] == C.SCOPE_SELECTIVE, "逐票查回来的 → 不得算市场口径"
    assert cat.health()["by_basis"] == {C.BASIS_SNAPSHOT: 1}
    # code_link 用最强的关联方式(代码就是查询参数)
    assert cat.links().iloc[0]["method"] == C.METHOD_QUERY_CODE


def test_lake_snapshot_never_replays_before_its_snapshot_time(tmp_path):
    """「**不得回放到该快照之前**」—— 设计稿对既有资产的硬约束。"""
    lake = tmp_path / "lake"
    path = _write_shard(lake, "000012", "20260625",
                        [_row("000012", "标题A", "u1", ts="2026-06-22 10:53:00")])
    cat = _cat(tmp_path)
    C.ingest_lake_shards(lake_root=lake, catalog=cat)
    snap = C._shard_snapshot_ts(path)

    before = (datetime.fromisoformat(snap) - timedelta(seconds=1)).isoformat()
    assert len(cat.replay(None, before, "L3")) == 0, "回放到快照之前竟然看得见"
    assert len(cat.replay(None, snap, "L3")) == 1
    # 来源自称 06-22 发布,但快照时间才是可见性起点(published 不能抢跑)
    assert datetime.fromisoformat(snap) > datetime(2026, 6, 22, tzinfo=timezone.utc)


def test_lake_same_article_across_codes_merges_and_keeps_all_links(tmp_path):
    """同一篇稿子被多只票各抓一次 → **一条观测、多个 code_link**。

    这条是 `ingest_lake_shards` 必须先聚合再入库的理由:逐行 ingest 时第二次会命中
    `unchanged` 分支,而那个分支在 `code_link` 落库**之前**就 continue —— 第二只票的
    关联会永远丢掉(实测:20 个分片 200 行里有 90 行是这种跨票重复)。
    """
    lake = tmp_path / "lake"
    same = dict(title="今日149只个股突破五日均线", url="http://em/a/1", ts="2026-06-22 10:53:00")
    _write_shard(lake, "000012", "20260625", [_row("000012", same["title"], same["url"], same["ts"])])
    _write_shard(lake, "600000", "20260625", [_row("600000", same["title"], same["url"], same["ts"])])

    cat = _cat(tmp_path)
    res = C.ingest_lake_shards(lake_root=lake, catalog=cat)
    assert res["n_rows_in_shards"] == 2
    assert res["n_observations"] == 1 and res["merged_duplicate_rows"] == 1
    assert len(cat.observations()) == 1
    assert set(cat.links()["code"]) == {"000012", "600000"}, "跨票重复合并后两个关联都要在"
    assert res["reconciled"] is True


def test_lake_reconciliation_accounts_for_every_row(tmp_path):
    """[I5 逐源对账] 分片里每一行都要有去处:入库/已在库/被拒/合并/无标题跳过。"""
    lake = tmp_path / "lake"
    _write_shard(lake, "000012", "20260625", [
        _row("000012", "标题A", "u1"),
        _row("000012", "", "u2"),                       # 无标题 → skipped
        _row("000012", "标题B", "u3"),
    ])
    cat = _cat(tmp_path)
    res = C.ingest_lake_shards(lake_root=lake, catalog=cat)
    assert res["n_rows_in_shards"] == 3
    assert res["skipped_no_title"] == 1
    assert res["added"] == 2
    assert res["reconciled"] is True
    assert (res["n_rows_in_shards"]
            == res["added"] + res["revised"] + res["unchanged"] + len(res["rejected"])
            + res["merged_duplicate_rows"] + res["skipped_no_title"])


def test_lake_ingest_is_idempotent(tmp_path):
    """重跑 → 全部 unchanged(通电腿会被反复调用,不幂等就是灌水)。"""
    lake = tmp_path / "lake"
    _write_shard(lake, "000012", "20260625", [_row("000012", "标题A", "u1")])
    cat = _cat(tmp_path)
    first = C.ingest_lake_shards(lake_root=lake, catalog=cat)
    second = C.ingest_lake_shards(lake_root=lake, catalog=cat)
    assert first["added"] == 1
    assert second["added"] == 0 and second["unchanged"] == 1
    assert len(cat.observations()) == 1


def test_lake_published_uses_beijing_time(tmp_path):
    """[I6 同族] 分片里的 发布时间 同样是北京时间,不得当 UTC。"""
    lake = tmp_path / "lake"
    _write_shard(lake, "000012", "20260625",
                 [_row("000012", "标题A", "u1", ts="2026-06-22 10:53:00")])
    cat = _cat(tmp_path)
    C.ingest_lake_shards(lake_root=lake, catalog=cat)
    pub = datetime.fromisoformat(cat.observations().iloc[0]["published_ts"])
    assert pub.utcoffset() == timedelta(hours=8)
    assert pub.astimezone(timezone.utc).hour == 2      # 10:53 CST = 02:53 UTC


def test_lake_bad_shard_is_recorded_not_fatal(tmp_path):
    """坏分片记账跳过,不毁整次盘点(B 级)。"""
    lake = tmp_path / "lake"
    _write_shard(lake, "000012", "20260625", [_row("000012", "标题A", "u1")])
    (lake / "stock_news_em" / "999999@20260625.parquet").write_bytes(b"not parquet")
    res = C.ingest_lake_shards(lake_root=lake, catalog=_cat(tmp_path))
    assert res["n_bad_shards"] == 1 and res["added"] == 1
    assert res["bad_shards"][0]["shard"] == "999999@20260625.parquet"


def test_lake_selective_scope_still_barred_from_market_heat(tmp_path):
    """1,891 个分片全是逐票采集 → 一条都不能进市场热度口径。"""
    lake = tmp_path / "lake"
    _write_shard(lake, "000012", "20260625", [_row("000012", "标题A", "u1")])
    cat = _cat(tmp_path)
    C.ingest_lake_shards(lake_root=lake, catalog=cat)
    assert len(cat.market_heat_eligible()) == 0
    with pytest.raises(C.CatalogError, match="selective"):
        cat.assert_not_selective(cat.observations(), context="市场新闻量")


def test_lake_unknown_source_raises(tmp_path):
    with pytest.raises(C.CatalogError, match="未知湖源"):
        C.ingest_lake_shards("no_such_lake", lake_root=tmp_path, catalog=_cat(tmp_path))


def test_lake_missing_root_is_empty_not_crash(tmp_path):
    res = C.ingest_lake_shards(lake_root=tmp_path / "nope", catalog=_cat(tmp_path))
    assert res["n_shards"] == 0 and res["added"] == 0 and res["reconciled"] is True


def test_lake_same_url_different_code_window_is_not_a_revision(tmp_path):
    """[首跑实测修正] 同一篇稿子的**跨票副本**不得被判成 revision。

    `stock_news_em` 的 `新闻内容` 是以查询代码为中心截出的窗口:同一 url 被不同票查回来,
    标题相同(实测 338/339)、正文不同。首版把正文算进 content_hash,于是这些副本变成
    「同 lineage、内容变了」= 假"更正" —— 全量首跑真的造出了 **4,483 条**。
    revision 的语义是「来源改了稿」,不是「我们截取的窗口不同」。
    """
    lake = tmp_path / "lake"
    title, url, ts = "【盘中播报】70只个股突破年线", "http://em/a/1", "2026-06-22 10:53:00"
    _write_shard(lake, "000012", "20260625", [_row("000012", title, url, ts, body="…000012 窗口…")])
    _write_shard(lake, "000807", "20260630", [_row("000807", title, url, ts, body="…000807 窗口…")])

    cat = _cat(tmp_path)
    res = C.ingest_lake_shards(lake_root=lake, catalog=cat)
    assert res["revised"] == 0, "跨票窗口差异被误判成了更正"
    assert res["n_observations"] == 1 and res["added"] == 1
    assert set(cat.links()["code"]) == {"000012", "000807"}
    assert cat.health()["revisions"] == 0


def test_lake_genuine_title_change_still_becomes_a_revision(tmp_path):
    """对照:标题真的变了(来源改稿)→ 仍然记 revision,旧版本保留。

    没有这条,上一条就可能是「把 revision 检测整个关掉」的假绿灯。
    """
    lake = tmp_path / "lake"
    url, ts = "http://em/a/1", "2026-06-22 10:53:00"
    _write_shard(lake, "000012", "20260625", [_row("000012", "原标题", url, ts)])
    cat = _cat(tmp_path)
    C.ingest_lake_shards(lake_root=lake, catalog=cat)

    (lake / "stock_news_em" / "000012@20260625.parquet").unlink()
    _write_shard(lake, "000012", "20260625", [_row("000012", "原标题(更正)", url, ts)])
    res = C.ingest_lake_shards(lake_root=lake, catalog=cat)
    assert res["revised"] == 1
    assert len(cat.observations()) == 2, "旧版本必须留着"
