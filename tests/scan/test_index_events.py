# tests/scan/test_index_events.py
"""指数调样事件表(design 2026-09-25 §2.2):相位、生效日推导、白名单、缺席≠否。全部离线(注入假取数)。"""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.data import cache, contracts
from autoresearch.scan import index_events as ie

TDS = ["20261127", "20261130", "20261201", "20261202", "20261203", "20261204", "20261207", "20261208",
       "20261209", "20261210", "20261211", "20261214", "20261215", "20261216", "20261217", "20261218"]
A, E = "20261127", "20261211"


@pytest.fixture(autouse=True)
def _clean():
    contracts.clear_degradations()
    yield
    contracts.clear_degradations()


@pytest.fixture
def lake(tmp_path, monkeypatch):
    root = tmp_path / "lake"
    monkeypatch.setattr(cache, "LAKE", root)
    monkeypatch.setattr(cache, "_real_today", lambda: "20261210")
    return root


def test_second_friday_and_rule_dates():
    assert ie.second_friday(2026, 12) == "20261211" and ie.second_friday(2026, 6) == "20260612"
    assert ie.rule_eff_close_date("20261127") == "20261211"
    assert ie.rule_eff_close_date("20260529") == "20260612"
    assert ie.rule_eff_close_date("20260909") is None          # 临时调整无规则日


def test_rule_eff_close_date_covers_kechuang50_quarterly_legs():
    """review 2026-09-25 #6:半年腿只覆盖 5/11 月公告,科创50 的季度腿(2/8 月公告)此前无规则。"""
    assert ie.rule_eff_close_date("20260212") == ie.second_friday(2026, 3) == "20260313"
    assert ie.rule_eff_close_date("20260810") == ie.second_friday(2026, 9) == "20260911"
    assert ie.rule_eff_close_date("20260909") is None           # 9 月不在 {2,5,8,11} 里,仍无规则


def test_eff_close_from_three_sources():
    assert ie.eff_close_from("20261211", "after_close", A, TDS) == ("20261211", "csindex")
    assert ie.eff_close_from("20261214", "from_date", A, TDS) == ("20261211", "csindex")   # 起生效 → 前一交易日
    assert ie.eff_close_from(None, None, A, TDS) == ("20261211", "rule")                  # 11 月公告 → 规则兜底
    assert ie.eff_close_from(None, None, "20260909", TDS) == (None, "none")


def test_eff_close_from_never_fabricates_csindex_source_without_a_resolved_date():
    """review 2026-09-25 #3:「起生效」推到窗口下界之外(prev_trading_day → None)时,绝不能仍报
    source="csindex"——那是把「解析不出」编码成了「有一个 csindex 日期」。"""
    assert ie.eff_close_from(TDS[0], "from_date", A, TDS) == (None, "none")
    assert any(r["endpoint"] == "trade_cal" for r in contracts.degradations())


@pytest.mark.parametrize("scan_date,expect", [
    ("2026-11-26", None),                        # 公告前 → 不落表
    ("2026-11-27", "announced_runup"),
    ("2026-12-09", "announced_runup"),           # E−2
    ("2026-12-10", "passive_close_eve"),         # E−1:守卫相位
    ("2026-12-11", "effective"),
    ("2026-12-16", "post"),                      # E+3
    ("2026-12-17", None),                        # E+4 → 出窗
])
def test_phase_for(scan_date, expect):
    assert ie.phase_for(scan_date, A, E, TDS) == expect


def test_phase_unknown_eff_when_no_effective_date():
    assert ie.phase_for("2026-12-10", A, None, TDS) == "unknown_eff"


def test_phase_for_effective_date_not_a_trading_day_is_unknown_eff_not_snapped():
    """review 2026-09-25 #2(b):E 落在窗口内但不是交易日(这里用周六 20261212)——只标
    unknown_eff,绝不悄悄挪到最近的交易日(那会把真正的 E−1 错判成 announced_runup)。"""
    assert ie.phase_for("2026-12-10", A, "20261212", TDS) == "unknown_eff"
    assert any(r["endpoint"] == "trade_cal" for r in contracts.degradations())


def test_phase_for_effective_date_past_window_end_still_announced_runup():
    """review 2026-09-25 #2(b):E 越过窗口右端(不是节假日,只是窗口不够长)必须仍是
    announced_runup——出窗和节假日是两件不同的事,不能被同一条判据混在一起。"""
    assert ie.phase_for("2026-12-10", A, "20270101", TDS) == "announced_runup"


def test_phase_for_no_earlier_trading_day_in_window_records_degradation():
    """review 2026-09-25 #2(c):E 等于窗口最早一天,窗口内找不到它的前一交易日——必须显式记账,
    不能靠 `day == None` 的哑比较悄悄不吭声;返回值本身(announced_runup)不受影响。"""
    assert ie.phase_for("2026-11-25", "20261120", TDS[0], TDS) == "announced_runup"
    assert any(r["endpoint"] == "trade_cal" for r in contracts.degradations())


def test_phase_for_unknown_eff_does_not_survive_past_the_window_start():
    """review 2026-09-25 #7:unknown_eff 不能无限期挂着——公告比整段窗口都老 → 出窗(None),
    不再是 unknown_eff。"""
    assert ie.phase_for("2026-12-10", "20260909", None, TDS) is None


def test_phases_constant_is_the_closed_set_phase_for_actually_produces():
    """minor-3(final whole-branch review):`PHASES` 是一份声明的契约(模块常量),但没有任何
    代码检查 `phase_for` 的返回值真的落在这个集合里,也没有任何代码检查这个集合里的每一项
    `phase_for` 都真的会吐出来——声明和产出可以悄悄漂移,这正是本仓库反复撞见的「有灯没人看」
    (一份契约摆在那,没有测试盯着它,后人改一个不改另一个,两边都不会红)。双向锁死:五次调用
    分别照抄本文件上面五个已验证通过的用例(不发明新参数),把返回值收进一个集合,应当恰好
    等于 `PHASES`——目前五个值都被至少一条已有 fixture 命中过,不缺席。"""
    produced = {
        ie.phase_for("2026-11-27", A, E, TDS),         # announced_runup(同 test_phase_for 参数化)
        ie.phase_for("2026-12-10", A, E, TDS),          # passive_close_eve
        ie.phase_for("2026-12-11", A, E, TDS),          # effective
        ie.phase_for("2026-12-16", A, E, TDS),          # post
        ie.phase_for("2026-12-10", A, None, TDS),       # unknown_eff(同 test_phase_unknown_eff_...)
    }
    assert produced == set(ie.PHASES)


def _list(items):
    return lambda endpoint, params: pd.DataFrame(items, columns=ie_list_cols())


def ie_list_cols():
    from autoresearch.data.sources.csindex import LIST_COLS
    return LIST_COLS


def _detail_rows(ann_id, publish_date, content, rows):
    from autoresearch.data.sources.csindex import DETAIL_COLS
    base = {"ann_id": ann_id, "publish_date": publish_date, "title": "关于调整指数样本的公告",
            "content_text": content, "attachment_url": "u"}
    return pd.DataFrame([{**base, **r} for r in rows], columns=DETAIL_COLS)


ROSTER = [
    {"index_code": "000300", "index_name": "沪深300", "side": "add", "code": "600221", "name": "海航控股"},
    {"index_code": "000300", "index_name": "沪深300", "side": "drop", "code": "600188", "name": "兖矿能源"},
    {"index_code": "000905", "index_name": "中证500", "side": "add", "code": "600188", "name": "兖矿能源"},   # 跨指数迁移:同票两行
    {"index_code": "000009", "index_name": "上证380", "side": "add", "code": "603092", "name": "德力佳"},      # 非白名单
]


def _fetch_detail(ann_id_to_frame):
    return lambda endpoint, params: ann_id_to_frame[str(params["ann_id"])]


def test_build_filters_whitelist_and_labels_phase(lake):
    fl = _list([["3007001", "关于调整沪深300、中证500等指数样本的公告", "20261127", "index_rebalance"]])
    fd = _fetch_detail({"3007001": _detail_rows("3007001", "20261127",
                                                "上述调整将于2026年12月11日收市后生效。", ROSTER)})
    df = ie.build_index_events("2026-12-10", today="20261210", fetch_list=fl, fetch_detail=fd, trading_days=TDS)
    assert list(df.columns) == ie.EVENT_COLS
    assert set(df.index_code) == {"000300", "000905"}                       # 上证380 行不进表
    assert len(df) == 3 and (df.phase == "passive_close_eve").all()
    assert (df.eff_close_date == "20261211").all() and (df.source == "csindex").all()
    migrate = df[df.code == "600188"]
    assert set(migrate.side) == {"drop", "add"}                              # 迁移不合并,两行都在
    assert df.flow_adv_days.isna().all()                                     # B3 之前恒空 = 未计算


def test_build_keeps_both_rows_when_a_correction_announcement_covers_the_same_code_index_side(lake):
    """final whole-branch review 的一次限定范围复核(2026-09-26)推翻了 minor-4:那次修复给
    `sort_values("publish_date", ascending=False)` 配上了一句「newest-wins 去重」
    (`drop_duplicates(subset=[...], keep="first")`),但复核用本项目自己的临时公告措辞真实
    复现出它的不对称——新公告解析不出生效日(`phase="unknown_eff"`)时会无条件驱逐一条老
    公告本该在扫描日判成 `passive_close_eve` 的行,门因此在它存在的理由(生效前夜)那一晚
    悄悄放行(见 `test_build_keeps_a_resolved_row_when_a_newer_unresolvable_temporary_notice_
    arrives`);发布日相同时,稳定排序还会让谁留下变成任意的。撤回排序与去重两处改动,回到
    保守行为:两份公告(原公告 + 更正公告)覆盖同一个 (code, index_code, side) 时两行都保留。
    门是任一行命中即否决的 any-match(见 `relative_buy._hard_gate`),多一行的代价只是一次
    可能多余的否决,少一行的代价是漏放真正的生效前夜——两者不对称,故意保守不去重。"""
    fl = _list([
        ["3007011", "关于调整沪深300指数样本的公告", "20261127", "index_rebalance"],
        ["3007012", "关于更正沪深300指数样本调整的公告", "20261128", "index_rebalance"],
    ])
    fd = _fetch_detail({
        "3007011": _detail_rows("3007011", "20261127", "上述调整将于2026年12月11日收市后生效。", ROSTER[:1]),
        "3007012": _detail_rows("3007012", "20261128", "上述调整将于2026年12月14日收市后生效。", ROSTER[:1]),
    })
    df = ie.build_index_events("2026-11-30", today="20261210", fetch_list=fl, fetch_detail=fd, trading_days=TDS)
    hs300_add = df[(df.index_code == "000300") & (df.code == "600221") & (df.side == "add")]
    assert len(hs300_add) == 2                                        # 两行都留着,不再互相驱逐
    assert set(hs300_add["ann_date"]) == {"20261127", "20261128"}
    assert set(hs300_add["eff_close_date"]) == {"20261211", "20261214"}


def test_build_keeps_a_resolved_row_when_a_newer_unresolvable_temporary_notice_arrives(lake):
    """final whole-branch review 限定范围复核(2026-09-26)逮到的回归,用本项目自己的规范
    临时公告措辞复现:一条**更新**的临时调整公告解析不出生效日(`phase="unknown_eff"`),
    与一条**更早**、已解析出生效日的公告覆盖同一个 (code, index_code, side)——按发布日期
    降序迭代 + `drop_duplicates(keep="first")` 的旧实现会让新公告那一行(未解析)驱逐旧公告
    那一行(已解析出 `passive_close_eve`),门在它唯一该响的那一夜(生效前夜)悄悄放行,
    per-stock 的 ⛔ 简报行也随 `calendar.py` 第三腿一起消失——唯一留下的痕迹是一个计数。
    这条测试就是那盏灯:在旧实现下必须失败(见修复报告的变异核验),修复后(delete the sort
    与 dedup,两行都保留)必须通过,且门要找的相位仍然在场。"""
    fl = _list([
        ["3007031", "关于调整沪深300指数样本的公告", "20261127", "index_rebalance"],
        ["3007032", "关于沪深300指数样本临时调整的公告", "20261128", "index_rebalance"],
    ])
    fd = _fetch_detail({
        "3007031": _detail_rows("3007031", "20261127", "上述调整将于2026年12月11日收市后生效。", ROSTER[:1]),
        "3007032": _detail_rows("3007032", "20261128", "自东兴证券、信达证券退市日起调整", ROSTER[:1]),
    })
    df = ie.build_index_events("2026-12-10", today="20261210", fetch_list=fl, fetch_detail=fd, trading_days=TDS)
    hs300_add = df[(df.index_code == "000300") & (df.code == "600221") & (df.side == "add")]
    assert len(hs300_add) == 2                                        # 两份公告都留着,不互相驱逐
    assert set(hs300_add["phase"]) == {"passive_close_eve", "unknown_eff"}
    resolved = hs300_add[hs300_add["phase"] == "passive_close_eve"]
    assert len(resolved) == 1 and resolved.iloc[0]["ann_date"] == "20261127"
    assert resolved.iloc[0]["eff_close_date"] == "20261211"           # 门要找的相位仍然在场


def test_build_processes_announcements_whose_title_lacks_the_word_sample(lake):
    """review 2026-09-25 #8:list 端点本身只返回调样类公告,标题关键词过滤只会制造静默丢失
    (换个措辞的真公告消失进空表,读起来像「源可达无事件」)。这条标题里没有「样本」二字,
    在旧的 `_looks_like_sample_adjustment` 下会被整条跳过;现在必须照常入表。"""
    fl = _list([["3007009", "关于调整沪深300成份股的公告", "20261127", "index_rebalance"]])
    fd = _fetch_detail({"3007009": _detail_rows("3007009", "20261127",
                                                 "上述调整将于2026年12月11日收市后生效。", ROSTER[:1])})
    df = ie.build_index_events("2026-12-10", today="20261210", fetch_list=fl, fetch_detail=fd, trading_days=TDS)
    assert len(df) == 1 and df.iloc[0]["phase"] == "passive_close_eve"


def test_build_uses_rule_date_when_text_has_no_date(lake):
    fl = _list([["3007002", "关于调整沪深300等指数样本的公告", "20261127", "index_rebalance"]])
    fd = _fetch_detail({"3007002": _detail_rows("3007002", "20261127", "调整名单见附件。", ROSTER[:1])})
    df = ie.build_index_events("2026-12-01", today="20261210", fetch_list=fl, fetch_detail=fd, trading_days=TDS)
    assert df.iloc[0].to_dict()["eff_close_date"] == "20261211" and df.iloc[0]["source"] == "rule"
    assert df.iloc[0]["phase"] == "announced_runup"


def test_build_withholds_rule_fallback_for_november_temporary_notice(lake):
    """review 2026-09-25 fix-round-2 item 1:标题带「临时」的公告即便落在规则月份(这里是半年腿
    的 11 月,规则本身在 #6 之前就已经存在),文字解析不出日期时也不能套用周期规则——旧代码会把
    这条临时公告在 2026-12-10(规则算出的 12 月11日的前一交易日)判成 passive_close_eve,而那
    一夜什么都不会发生。标题措辞取自 2026-09-25 对 csindex 的活体探针(见本轮修复报告)。"""
    fl = _list([["3006227", "关于沪深300等指数样本临时调整的公告", "20261127", "index_rebalance"]])
    fd = _fetch_detail({"3006227": _detail_rows("3006227", "20261127",
                                                "自东兴证券、信达证券退市日起调整", ROSTER[:1])})
    df = ie.build_index_events("2026-12-10", today="20261210", fetch_list=fl, fetch_detail=fd, trading_days=TDS)
    assert df.iloc[0]["phase"] == "unknown_eff" and df.iloc[0]["source"] == "none"
    assert pd.isna(df.iloc[0]["eff_close_date"])


def test_build_withholds_rule_fallback_for_february_temporary_notice(lake):
    """同上,针对 #6 新增的 2→3 月季度腿:标题带「临时」+ 解析不出日期 → unknown_eff/source=none,
    不是 2026 年 3 月第二个周五。"""
    feb_tds = ["20260210", "20260211", "20260212", "20260213", "20260216"]
    fl = _list([["3006227", "关于沪深300等指数样本临时调整的公告", "20260212", "index_rebalance"]])
    fd = _fetch_detail({"3006227": _detail_rows("3006227", "20260212",
                                                "自东兴证券、信达证券退市日起调整", ROSTER[:1])})
    df = ie.build_index_events("2026-02-13", today="20261210", fetch_list=fl, fetch_detail=fd,
                               trading_days=feb_tds)
    assert df.iloc[0]["phase"] == "unknown_eff" and df.iloc[0]["source"] == "none"
    assert pd.isna(df.iloc[0]["eff_close_date"])


def test_build_keeps_rule_fallback_for_february_periodic_notice(lake):
    """review 2026-09-25 fix-round-2 item 1:标题**没有**「临时」的周期公告(措辞取自活体探针的
    「关于调整…样本股的公告」)即便文字解析不出日期,仍然要吃到 #6 新增的 2→3 月规则兜底——这条
    修复只挡「临时」,不挡真正周期性的调样。"""
    feb_mar_tds = ["20260209", "20260210", "20260211", "20260212", "20260213",
                   "20260306", "20260309", "20260310", "20260311", "20260312", "20260313", "20260316"]
    fl = _list([["3007101", "关于调整科创50等指数样本股的公告", "20260212", "index_rebalance"]])
    fd = _fetch_detail({"3007101": _detail_rows("3007101", "20260212", "调整名单见附件。", ROSTER[:1])})
    df = ie.build_index_events("2026-03-10", today="20261210", fetch_list=fl, fetch_detail=fd,
                               trading_days=feb_mar_tds)
    assert df.iloc[0]["eff_close_date"] == "20260313" and df.iloc[0]["source"] == "rule"


def test_build_unknown_eff_row_is_kept_with_its_own_phase(lake):
    fl = _list([["3006227", "关于沪深300等指数样本临时调整的公告", "20260909", "index_rebalance"]])
    fd = _fetch_detail({"3006227": _detail_rows("3006227", "20260909", "自东兴证券、信达证券退市日起调整", ROSTER[:1])})
    # 本用例的公告/扫描日都在 2026-09,窗口必须真的覆盖那段时间(而不是像别处复用的 TDS 那样
    # 落在 11/12 月)——否则 review 2026-09-25 #7 的「公告老过窗口 → 出窗」新规则会正确地把它
    # 判成 None,而这条用例本意是测试「窗口内、真解析不出生效日」这个不同的场景。
    sept_tds = ["20260908", "20260909", "20260910", "20260911", "20260914"]
    df = ie.build_index_events("2026-09-10", today="20261210", fetch_list=fl, fetch_detail=fd,
                               trading_days=sept_tds)
    assert df.iloc[0]["phase"] == "unknown_eff" and pd.isna(df.iloc[0]["eff_close_date"])


def test_build_returns_empty_frame_when_no_whitelist_events(lake):
    # review 2026-09-25 fix-round-2 item 4:公告日必须落在 TDS 窗口内(12 月,不是 9 月),否则
    # review #7 的「公告老过窗口 → 出窗」新规则会在 phase_for 里就把这一行判成 None、提前
    # continue——这条用例本意是测「白名单过滤」这一步,不是测出窗,两者必须分开各自被覆盖。
    fl = _list([["3006244", "关于调整三板成指样本股的公告", "20261201", "index_rebalance"]])
    fd = _fetch_detail({"3006244": _detail_rows("3006244", "20261201", "x", ROSTER[3:])})
    df = ie.build_index_events("2026-12-05", today="20261210", fetch_list=fl, fetch_detail=fd, trading_days=TDS)
    assert df is not None and df.empty and list(df.columns) == ie.EVENT_COLS     # 源可达无事件 = 空表,不是 None


def test_build_returns_none_and_records_degradation_when_list_unreachable(lake):
    def boom(endpoint, params):
        raise RuntimeError("csindex down")
    df = ie.build_index_events("2026-12-10", today="20261210", fetch_list=boom, trading_days=TDS)
    assert df is None
    assert any(r["endpoint"] == "csindex_rebalance_list" for r in contracts.degradations())


def test_build_on_past_date_reuses_latest_list_snapshot_instead_of_fetching(lake):
    calls = []

    def fl(endpoint, params):
        calls.append(1)
        return pd.DataFrame([["3007001", "关于调整沪深300等指数样本的公告", "20261127", "index_rebalance"]],
                            columns=ie_list_cols())
    fd = _fetch_detail({"3007001": _detail_rows("3007001", "20261127", "x", ROSTER[:1])})
    ie.build_index_events("2026-12-10", today="20261210", fetch_list=fl, fetch_detail=fd, trading_days=TDS)
    df = ie.build_index_events("2026-12-01", today="20261201", fetch_list=fl, fetch_detail=fd, trading_days=TDS)
    assert len(calls) == 1 and len(df) == 1                                  # 补跑:读湖里最新快照,不取网
    # review 2026-09-25 #4:这是把另一天的观测代入一个对时间敏感的相位计算,必须留痕,不能悄悄做。
    assert any(r["endpoint"] == "csindex_rebalance_list" and "快照" in r["reasons"][0]
               for r in contracts.degradations())


def test_detail_is_fetched_once_per_announcement(lake):
    calls = []
    fl = _list([["3007001", "关于调整沪深300等指数样本的公告", "20261127", "index_rebalance"]])

    def fd(endpoint, params):
        calls.append(params["ann_id"])
        return _detail_rows("3007001", "20261127", "x", ROSTER[:1])
    ie.build_index_events("2026-12-10", today="20261210", fetch_list=fl, fetch_detail=fd, trading_days=TDS)
    ie.build_index_events("2026-12-10", today="20261210", fetch_list=fl, fetch_detail=fd, trading_days=TDS)
    assert calls == ["3007001"]                                              # 第二次命中 <ann_id>@*.parquet


def test_load_detail_lake_hit_runs_the_contract_and_is_not_a_rubber_stamp(lake):
    """review 2026-09-25 fix-round-2 item 3:两个 csindex 端点都是 B 级,`check()` 从不对干净帧
    抛异常,所以此前每条测过湖命中分支的用例都只喂过干净数据——`check(...)` 调用本身从没被
    这些测试真正验证过存在的必要性。这里手写一份缺 `content_text` 列的 parquet 直接进湖,
    走 `_load_detail` 的湖命中分支,确认降级确实被记了账。"""
    folder = cache.LAKE / ie._DETAIL_EP
    folder.mkdir(parents=True, exist_ok=True)
    bad = pd.DataFrame([{"ann_id": "9999999", "publish_date": "20261127"}])   # 缺 content_text
    bad.to_parquet(folder / "9999999@20261210.parquet")
    detail = ie._load_detail("9999999", "20261210", fetch_detail=None)        # 缓存命中,fetch_detail 不会被调
    assert detail is not None and "content_text" not in detail.columns
    assert any(r["endpoint"] == ie._DETAIL_EP and "缺列" in r["reasons"][0] for r in contracts.degradations())


def test_latest_list_snapshot_lake_hit_runs_the_contract_and_is_not_a_rubber_stamp(lake):
    """同上,针对 `_latest_list_snapshot` 的湖命中分支:手写一份缺 `title` 列的 parquet,
    确认它也真的跑了契约,不是摆设。"""
    folder = cache.LAKE / ie._LIST_EP
    folder.mkdir(parents=True, exist_ok=True)
    bad = pd.DataFrame([{"ann_id": "8888888", "publish_date": "20261127"}])   # 缺 title
    bad.to_parquet(folder / "all@20261127.parquet")
    lst = ie._latest_list_snapshot("20261201")
    assert lst is not None and "title" not in lst.columns
    assert any(r["endpoint"] == ie._LIST_EP and "缺列" in r["reasons"][0] for r in contracts.degradations())


def test_harvest_writes_no_file_when_source_unreachable(lake, tmp_path):
    d = tmp_path / "2026-12-10"
    d.mkdir()

    def boom(endpoint, params):
        raise RuntimeError("csindex down")
    df = ie.harvest_index_events("2026-12-10", d, today="20261210", fetch_list=boom, trading_days=TDS)
    assert df is None and not (d / ie.INDEX_EVENTS_FILENAME).exists()           # 世界①:不可达 = 不落文件


def test_harvest_writes_header_only_file_when_reachable_without_events(lake, tmp_path):
    # review 2026-09-25 fix-round-2 item 4:同上——公告日移进 TDS 窗口(12 月),让这条用例继续
    # 覆盖白名单过滤这一步,而不是被 #7 的出窗规则提前接管。
    d = tmp_path / "2026-12-05"
    d.mkdir()
    fl = _list([["3006244", "关于调整三板成指样本股的公告", "20261201", "index_rebalance"]])
    fd = _fetch_detail({"3006244": _detail_rows("3006244", "20261201", "x", ROSTER[3:])})
    df = ie.harvest_index_events("2026-12-05", d, today="20261210", fetch_list=fl, fetch_detail=fd,
                                 trading_days=TDS)
    assert df is not None and df.empty and (d / ie.INDEX_EVENTS_FILENAME).exists()   # 世界②:可达无事件 = 落表头
    reloaded = ie.load_index_events(d)
    assert reloaded is not None and reloaded.empty                              # 落盘后读回仍可分辨于「缺席」


def test_write_load_by_code_and_absence_semantics(tmp_path):
    d = tmp_path / "2026-12-10"
    d.mkdir()
    assert ie.load_index_events(d) is None and ie.events_by_code(None) is None      # 缺文件 = 缺席
    ie.write_index_events(d, pd.DataFrame(columns=ie.EVENT_COLS))
    empty = ie.load_index_events(d)
    assert empty is not None and empty.empty and ie.events_by_code(empty) == {}     # 空表 = 可达无事件
    rows = pd.DataFrame([{"code": "600221", "index_code": "000300", "index_name": "沪深300", "side": "add",
                          "ann_date": A, "eff_close_date": E, "phase": "passive_close_eve",
                          "source": "csindex", "flow_adv_days": None}], columns=ie.EVENT_COLS)
    ie.write_index_events(d, rows)
    by = ie.events_by_code(ie.load_index_events(d))
    assert list(by) == ["600221"] and by["600221"][0]["phase"] == "passive_close_eve"
    assert by["600221"][0]["eff_close_date"] == E                                  # 读回仍是字符串


def test_write_index_events_is_atomic_not_a_bare_to_csv(tmp_path, monkeypatch):
    """I2(final whole-branch review):`write_index_events` 曾是裸 `to_csv`——中断的一次
    (如进程被杀在写盘中途)会在目标路径上留下一个读不出来的半成品(如零字节文件),而
    `calendar_section`/`index_events_health`/`card_contract_lint` 三个读者当时都没有防这
    件事的准备。镜像 `relative_buy.write_decision` 自己用的临时文件 + `Path.replace`:
    一次写只有完整落盘才会替换掉目标路径,中断只留下一个从未被 rename 进目标名的孤儿文件,
    盘上那份完好的旧文件永远不会被半成品覆盖。"""
    d = tmp_path / "2026-12-10"
    d.mkdir()
    good = pd.DataFrame([{"code": "600221", "index_code": "000300", "index_name": "沪深300", "side": "add",
                          "ann_date": A, "eff_close_date": E, "phase": "passive_close_eve",
                          "source": "csindex", "flow_adv_days": None}], columns=ie.EVENT_COLS)
    ie.write_index_events(d, good)
    target = d / ie.INDEX_EVENTS_FILENAME
    before = target.read_bytes()
    assert not list(d.glob("*.tmp"))                       # 成功写不留孤儿临时文件

    def boom(self, path_or_buf=None, **kw):
        # 忠实重现"写到一半"——真 `to_csv` 也是边写边落盘,不是原子的一瞬间;直接照抄
        # `path_or_buf` 写几个字节再抛,让裸 `to_csv(target)` 的旧实现在这里就会把目标文件
        # 覆盖成半成品,而 tmp+replace 的新实现只会弄脏那个从未被 rename 进目标名的 `.tmp`。
        from pathlib import Path
        Path(path_or_buf).write_bytes(b"garbage-mid-write")
        raise OSError("disk full mid-write")
    monkeypatch.setattr(pd.DataFrame, "to_csv", boom)
    with pytest.raises(OSError):
        ie.write_index_events(d, pd.DataFrame(columns=ie.EVENT_COLS))
    assert target.read_bytes() == before                   # 中断没有替换掉盘上那份完好的旧文件


def test_trading_days_window_falls_back_to_weekdays_and_records_it(monkeypatch):
    import autoresearch.data.tushare_source as ts_src
    monkeypatch.setattr(ts_src, "_pro", lambda: (_ for _ in ()).throw(RuntimeError("no token")))
    days, source = ie.trading_days_window("2026-12-10", before=7, after=7)
    assert source == "weekday_approx" and "20261210" in days and "20261212" not in days   # 周六不在
    assert any(r["endpoint"] == "trade_cal" for r in contracts.degradations())


def test_trading_days_window_success_path_reports_trade_cal_basis(monkeypatch):
    """review 2026-09-25(#1/#2 缺的测试):目前只测过失败/近似路径——成功路径从没被测过,
    所以 `tushare_source._trade_days` 被改名/签名变化会被 `except Exception` 悄悄吞成
    weekday_approx,而不会有任何测试变红。这条直接注入一个成功的 `_trade_days`,钉住
    source == "trade_cal"。"""
    import autoresearch.data.tushare_source as ts_src
    monkeypatch.setattr(ts_src, "_pro", lambda: object())
    monkeypatch.setattr(ts_src, "_trade_days", lambda pro, start, end: ["20261127", "20261211"])
    days, source = ie.trading_days_window("2026-12-10", before=7, after=7)
    assert source == "trade_cal" and days == ["20261127", "20261211"]


def test_build_returns_none_when_trading_calendar_is_only_approximated(lake, monkeypatch):
    """review 2026-09-25 #1(a):没有显式传 `trading_days` 的生产路径,真实日历不可达、只拿到
    weekday_approx 近似时,`build_index_events` 必须不产表——一张按近似日历算出的相位表比没有
    表更危险(它会把 passive_close_eve 错标到真实日历上的另一天)。"""
    import autoresearch.data.tushare_source as ts_src
    monkeypatch.setattr(ts_src, "_pro", lambda: (_ for _ in ()).throw(RuntimeError("no token")))
    fl = _list([["3007001", "关于调整沪深300等指数样本的公告", "20261127", "index_rebalance"]])
    fd = _fetch_detail({"3007001": _detail_rows("3007001", "20261127", "x", ROSTER[:1])})
    df = ie.build_index_events("2026-12-10", today="20261210", fetch_list=fl, fetch_detail=fd)  # 无 trading_days=
    assert df is None
    assert any(r["endpoint"] == "trade_cal" for r in contracts.degradations())


def test_build_returns_none_not_empty_frame_when_calendar_degraded_and_list_is_also_empty(lake, monkeypatch):
    """review 2026-09-25 fix-round-2 item 2:世界①(日历只能近似,不可信)与世界②(源可达但列表
    真的没有事件)必须保持互斥,即便两个条件同时成立——日历基准检查排在「列表是否为空」之前,
    产物是 None,不是只有表头的空帧。(空列表帧本身是 B 级违约、`persist_violations=False`,
    按设计不会落湖——那是另一件事,这条用例只钉产物形状,不断言湖里有没有文件。)"""
    import autoresearch.data.tushare_source as ts_src
    monkeypatch.setattr(ts_src, "_pro", lambda: (_ for _ in ()).throw(RuntimeError("no token")))
    fl = _list([])                                            # 源可达,但真的一条公告都没有
    df = ie.build_index_events("2026-12-10", today="20261210", fetch_list=fl)   # 无 trading_days=
    assert df is None                                          # 不是 pd.DataFrame(columns=...) 的空帧
    assert any(r["endpoint"] == "trade_cal" for r in contracts.degradations())
