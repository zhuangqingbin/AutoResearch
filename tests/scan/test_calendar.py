"""日历(解禁/预约披露):quarter 端点、flags、section、简报注入、assemble 嵌入。合成,无网络。

spec: docs/specs/2026-07-02-scan-calendar-shadow-design.md §1
"""
from __future__ import annotations

import pandas as pd

from autoresearch.scan.calendar import _last_quarter_end, calendar_flags, calendar_section

_ROWS = [
    {"code": "000001", "kind": "unlock", "event_date": "20260715", "detail": "定增股份·3方", "ratio": 8.2},
    {"code": "000001", "kind": "disclosure", "event_date": "20260716", "detail": "预约披露(期 20260630)", "ratio": None},
    {"code": "000002", "kind": "unlock", "event_date": "20260710", "detail": "定增股份·1方", "ratio": 0.5},
    {"code": "000003", "kind": "unlock", "event_date": "20261230", "detail": "首发原股东", "ratio": 30.0},
]


def _mk(tmp_path, date="2026-07-02", finalists=("000001",)):
    d = tmp_path / date
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(_ROWS).to_csv(d / "calendar.csv", index=False)
    pd.DataFrame([{"code": c, "name": f"N{c}", "sector": "半导体"} for c in finalists]).to_csv(
        d / "finalists.csv", index=False)
    return d


def test_last_quarter_end():
    assert _last_quarter_end("2026-07-02") == "20260630"
    assert _last_quarter_end("2026-02-01") == "20251231"
    assert _last_quarter_end("2026-04-01") == "20260331"


def test_calendar_flags(tmp_path):
    d = _mk(tmp_path)
    f1 = calendar_flags(d, "000001")
    assert any("解禁" in x and "8.2%" in x for x in f1)
    assert any("预约披露" in x and "20260716" in x for x in f1)
    assert calendar_flags(d, "000002") == []          # ratio 0.5 < 2.0 阈,不旗
    assert calendar_flags(d, "000003") == []          # 解禁在 30 日窗外
    assert calendar_flags(tmp_path / "nope", "000001") == []


def test_calendar_section(tmp_path):
    d = _mk(tmp_path)
    s = calendar_section(d)
    assert "📅" in s and "000001 20260716" in s        # finalists 披露
    assert "8%" in s or "8.2" in s or "(8%)" in s      # 大解禁 ≥5%
    assert "000003" not in s                           # 14 日窗外
    assert calendar_section(tmp_path / "nope") == ""


def test_calendar_section_survives_a_corrupt_index_events_file(tmp_path):
    """I2(final whole-branch review):`index_events.csv` 是非原子写,中断的一次会留下一个
    读不出来的半成品(如零字节文件)。`calendar_section` 是**唯一**把调样市场级计数送进已
    发布 summary 的路径(`report_sections.py:1178-1179`,无外层 catch)——旧代码读表这一步
    没有任何防护,读盘异常会穿透整个函数,把本该正常渲染的解禁/披露两段一起带走(不只是
    少一行调样计数,是整份 summary 的这一节都没有,再往上甚至没有报告)。"""
    d = _mk(tmp_path)
    (d / "index_events.csv").write_bytes(b"")          # 零字节:中断写留下的半成品
    s = calendar_section(d)
    assert "📅" in s and "000001 20260716" in s        # 解禁/披露两段没被调样那半段拖垒
    assert "指数调样" not in s                          # 调样计数这一段诚实地什么都没加


def test_brief_injects_calendar(tmp_path):
    from autoresearch.scan.agents.l4_card import compose_funnel_brief
    d = _mk(tmp_path)
    s = compose_funnel_brief("000001", d)
    assert "解禁" in s and "预约披露" in s
    s2 = compose_funnel_brief("000002", d)
    assert "解禁" not in s2                            # 小解禁不旗


def test_assemble_embeds_calendar(tmp_path):
    from autoresearch.scan.assemble import build_summary
    d = _mk(tmp_path)
    (d / "meta.json").write_text("{}", encoding="utf-8")
    md = build_summary(d, "2026-07-02", "1200", "20260702_1200")
    # 2026-08-29 B+ 重构:节标题由 summary 自己出(生产者自带的 `### 📅 …日历` 内标题被剥,
    # 避免与本节标题重复);断言改锚新标题 **并加验真日历内容**——只对标题断言是弱验收。
    assert "## 📅 未来 14 天" in md
    assert "000001 20260716" in md                     # 披露锚真进了 summary,不是只剩个标题
    assert "000003" not in md                          # 14 日窗外的仍被挡在外面
    assert "### 📅" not in md                          # 内标题已剥,不与节标题重复


# ───────────────────────── 第三腿:指数调样(2026-09-25 §2.3) ─────────────────────────
from autoresearch.scan import index_events as ie  # noqa: E402


class _DeadPro:
    """解禁/披露两腿离线:方法一律抛 → harvest_calendar 那两段按既有 try/except 静默跳过。"""

    def share_float(self, **kw):
        raise RuntimeError("offline")

    def disclosure_date(self, **kw):
        raise RuntimeError("offline")


_EV = pd.DataFrame([
    {"code": "000001", "index_code": "000300", "index_name": "沪深300", "side": "add", "ann_date": "20260529",
     "eff_close_date": "20260612", "phase": "passive_close_eve", "source": "csindex", "flow_adv_days": None},
    {"code": "999999", "index_code": "000905", "index_name": "中证500", "side": "drop", "ann_date": "20260529",
     "eff_close_date": "20260612", "phase": "passive_close_eve", "source": "csindex", "flow_adv_days": 0.4},
    {"code": "000002", "index_code": "000300", "index_name": "沪深300", "side": "add", "ann_date": "20260909",
     "eff_close_date": None, "phase": "unknown_eff", "source": "none", "flow_adv_days": None},
], columns=ie.EVENT_COLS)


def _offline(monkeypatch):
    import autoresearch.data.tushare_source as ts_src
    from autoresearch.scan import calendar as cal
    monkeypatch.setattr(ts_src, "_pro", lambda: _DeadPro())
    monkeypatch.setattr(cal, "knob", lambda block, key, cli, default, cfg=None: default if cli is None else cli)
    # fix-round-1 #2(2026-09-25):_ts_call 的重试退避(1.5+3+4.5+6=15s)× 4 次调用/测试 = 60s——
    # 这三个测试不测重试,直接让被包装的 fn 立即抛,既有两腿的 try/except 照样吞。
    monkeypatch.setattr(ts_src, "_ts_call", lambda fn, *a, **k: fn())


def test_harvest_calendar_third_leg_is_off_by_default(tmp_path, monkeypatch):
    from autoresearch.scan import calendar as cal
    _offline(monkeypatch)

    def must_not_run(*a, **k):
        raise AssertionError("index_events must not be harvested when the knob is off")
    monkeypatch.setattr(ie, "harvest_index_events", must_not_run)
    df = cal.harvest_calendar("2026-06-11", {"000001"}, root=tmp_path)
    assert df.empty and (tmp_path / "2026-06-11" / "calendar.csv").exists()
    assert not (tmp_path / "2026-06-11" / "index_events.csv").exists()


def test_harvest_calendar_third_leg_filters_to_wanted_codes_and_keeps_phase(tmp_path, monkeypatch):
    from autoresearch.scan import calendar as cal
    _offline(monkeypatch)

    def fake_harvest(date, outdir, **k):
        ie.write_index_events(outdir, _EV)
        return _EV
    monkeypatch.setattr(ie, "harvest_index_events", fake_harvest)
    df = cal.harvest_calendar("2026-06-11", {"000001", "000002"}, root=tmp_path, index_rebalance=True)
    rows = df[df["kind"] == "index_rebalance"]
    assert rows["code"].tolist() == ["000001"]                    # 999999 不在 want;000002 无生效日不进日历
    assert rows.iloc[0]["event_date"] == "20260612"
    assert rows.iloc[0]["detail"] == "沪深300 调入|passive_close_eve"
    assert pd.isna(rows.iloc[0]["ratio"])
    assert (tmp_path / "2026-06-11" / "index_events.csv").exists()   # 全量表照落(999999 也在里面)
    assert len(ie.load_index_events(tmp_path / "2026-06-11")) == 3


def test_harvest_calendar_third_leg_skips_unknown_eff_even_with_a_populated_date(tmp_path, monkeypatch):
    """fix-round-1 #1(2026-09-25):`unknown_eff` 行不能只靠「日期是否非空」来挡——Task 3 的
    `phase_for` 在生效日撞节假日时会保留解析/规则算出的日期字符串而不清空它(不猜该往哪边挪),
    所以一条 `phase="unknown_eff"` 但 `eff_close_date` 非空的行,此前会漏过滤混进日历,把一个
    未判定的日期当事实发布给全部四个日历消费者。"""
    from autoresearch.scan import calendar as cal
    _offline(monkeypatch)
    ev = pd.DataFrame([*_EV.to_dict("records"), {
        "code": "000003", "index_code": "000300", "index_name": "沪深300", "side": "add",
        "ann_date": "20260529", "eff_close_date": "20260612", "phase": "unknown_eff",
        "source": "csindex", "flow_adv_days": None,
    }], columns=ie.EVENT_COLS)

    def fake_harvest(date, outdir, **k):
        ie.write_index_events(outdir, ev)
        return ev
    monkeypatch.setattr(ie, "harvest_index_events", fake_harvest)
    df = cal.harvest_calendar("2026-06-11", {"000001", "000002", "000003"}, root=tmp_path, index_rebalance=True)
    rows = df[df["kind"] == "index_rebalance"]
    assert "000003" not in rows["code"].tolist()          # unknown_eff 即使带日期也不进日历
    assert rows["code"].tolist() == ["000001"]             # 既有行为不受影响


def test_harvest_calendar_source_absent_leaves_other_legs_intact(tmp_path, monkeypatch):
    from autoresearch.scan import calendar as cal
    _offline(monkeypatch)
    monkeypatch.setattr(ie, "harvest_index_events", lambda date, outdir, **k: None)
    df = cal.harvest_calendar("2026-06-11", {"000001"}, root=tmp_path, index_rebalance=True)
    assert df.empty and not (tmp_path / "2026-06-11" / "index_events.csv").exists()


class _PartialPro:
    """解禁腿真出一行数据,披露腿离线;第三腿由各测试自己 monkeypatch(I1)。"""

    def share_float(self, **kw):
        return pd.DataFrame([{"ts_code": "000001.SZ", "float_date": "20260615",
                              "float_ratio": 5.0, "holder_name": "A", "share_type": "定增股份"}])

    def disclosure_date(self, **kw):
        raise RuntimeError("offline")


def test_harvest_calendar_third_leg_exception_does_not_lose_the_other_two_legs(tmp_path, monkeypatch):
    """I1(final whole-branch review):解禁/披露两腿各自有 try/except,恰恰是为了不被彼此的失败
    拖累;第三腿此前一个没有——它跑在两腿之后、`to_csv` 之前,一次未捕获异常(如湖分区缺一列
    契约本不要求的列,导致 `r.index_code`/`r.side` 之类的属性访问抛 AttributeError/KeyError)
    会在写盘前直接终止整个函数,连同上面两腿已经收集好的行一起带走——`calendar.csv` 是
    L4 简报/summary/档案/行业包四处消费者共读的**恒在**产物,不该因为第三腿一个人的问题而
    整份消失。"""
    import autoresearch.data.tushare_source as ts_src
    from autoresearch.scan import calendar as cal
    monkeypatch.setattr(ts_src, "_pro", lambda: _PartialPro())
    monkeypatch.setattr(ts_src, "_ts_call", lambda fn, *a, **k: fn())

    def boom(date, outdir, **k):
        raise KeyError("index_code")   # 模拟湖分区缺一列契约不要求的列 → 深处属性访问真的会炸
    monkeypatch.setattr(ie, "harvest_index_events", boom)
    df = cal.harvest_calendar("2026-06-11", {"000001"}, root=tmp_path, index_rebalance=True)
    assert (tmp_path / "2026-06-11" / "calendar.csv").exists()
    assert (df["kind"] == "unlock").any()                       # 解禁腿的行没被第三腿的异常带走
    assert not (df["kind"] == "index_rebalance").any()          # 第三腿本身诚实地什么都没贡献


def _mk_index(tmp_path, date="2026-06-11"):
    d = tmp_path / date
    d.mkdir(parents=True, exist_ok=True)
    rows = _ROWS + [
        {"code": "000004", "kind": "index_rebalance", "event_date": "20260612",
         "detail": "沪深300 调入|passive_close_eve", "ratio": 0.3},
        {"code": "000005", "kind": "index_rebalance", "event_date": "20260612",
         "detail": "中证500 调出|announced_runup", "ratio": None},
        {"code": "000006", "kind": "index_rebalance", "event_date": "20260612",
         "detail": "沪深300 调入|announced_runup", "ratio": 0.3},
    ]
    pd.DataFrame(rows).to_csv(d / "calendar.csv", index=False)
    pd.DataFrame([{"code": c, "name": f"N{c}", "sector": "半导体"} for c in ("000001", "000004")]).to_csv(
        d / "finalists.csv", index=False)
    ie.write_index_events(d, pd.DataFrame([
        {"code": "000004", "index_code": "000300", "index_name": "沪深300", "side": "add", "ann_date": "20260529",
         "eff_close_date": "20260612", "phase": "passive_close_eve", "source": "csindex", "flow_adv_days": 0.3},
        {"code": "000006", "index_code": "000300", "index_name": "沪深300", "side": "add", "ann_date": "20260529",
         "eff_close_date": "20260612", "phase": "passive_close_eve", "source": "csindex", "flow_adv_days": 0.3},
        {"code": "000005", "index_code": "000905", "index_name": "中证500", "side": "drop", "ann_date": "20260529",
         "eff_close_date": "20260612", "phase": "passive_close_eve", "source": "csindex", "flow_adv_days": None},
        # amendment(task-6,非 brief 原文):Task 3 的 phase_for 撞节假日时保留日期字符串不清空——
        # unknown_eff 行可以带一个"看起来"合法的 eff_close_date。它不是已核实的生效日,
        # calendar_section 的市场级计数必须把它挡在外面(同 harvest_calendar 对第三腿的处理)。
        # 若过滤漏了,下面这行会把同日沪深300计数从 ×2 顶成 ×3,冲掉
        # test_calendar_section_prints_market_level_rebalance_counts 的断言——这就是「pin」。
        {"code": "000007", "index_code": "000300", "index_name": "沪深300", "side": "add", "ann_date": "20260529",
         "eff_close_date": "20260612", "phase": "unknown_eff", "source": "csindex", "flow_adv_days": None},
    ], columns=ie.EVENT_COLS))
    return d


def test_calendar_flags_rebalance_eve_is_the_only_directional_line(tmp_path):
    d = _mk_index(tmp_path)
    eve = calendar_flags(d, "000004")
    assert len(eve) == 1 and eve[0].startswith("- ⛔ **指数调样生效前夜**")
    assert "沪深300 调入" in eve[0] and "20260612" in eve[0] and eve[0].endswith("入场行写 禁止")
    fact = calendar_flags(d, "000005")
    assert len(fact) == 1 and fact[0].startswith("- 📅 **指数调样**")
    assert "中证500 调出" in fact[0] and "事实日期非方向" in fact[0]
    assert "禁止" not in fact[0] and "买入" not in fact[0]           # 其它相位零方向词


def test_calendar_flags_fact_line_carries_flow_only_when_present(tmp_path):
    d = _mk_index(tmp_path)
    assert "ETF 被动买入≈0.3 天 ADV" in calendar_flags(d, "000006")[0]
    assert "ADV" not in calendar_flags(d, "000005")[0]


def test_calendar_section_prints_market_level_rebalance_counts(tmp_path):
    d = _mk_index(tmp_path)
    s = calendar_section(d)
    assert "- **指数调样 20260612 收盘生效**:" in s
    assert "沪深300 ×2" in s and "中证500 ×1" in s and "finalist 涉及 1 只" in s
    # amendment(task-6):000007 是 unknown_eff,即使带 eff_close_date 也不得计入——若漏过滤,
    # 上面的 "沪深300 ×2" 断言会失败(实际会是 ×3),这里再加一条直接否定式断言便于定位。
    assert "沪深300 ×3" not in s


def test_calendar_section_excludes_past_dated_post_phase_rows_from_the_future_window(tmp_path):
    """minor-5(final whole-branch review):第三腿打破了 calendar.csv 的旧不变量——它的日期
    恒在未来。`effective`/`post` 相位的行 `eff_close_date` 可以等于或早于扫描日,而
    `calendar_section` 的上界过滤(`<= cut`)对一个已经过去的日期毫无意义地恒真,若没有下界,
    一个 3 天前已生效的调样会被算进「未来 14 天」标题下的市场级计数。"""
    d = tmp_path / "2026-06-11"
    d.mkdir(parents=True)
    pd.DataFrame(columns=["code", "kind", "event_date", "detail", "ratio"]).to_csv(
        d / "calendar.csv", index=False)
    from autoresearch.scan import index_events as ie
    ie.write_index_events(d, pd.DataFrame([
        {"code": "000004", "index_code": "000300", "index_name": "沪深300", "side": "add",
         "ann_date": "20260529", "eff_close_date": "20260608", "phase": "post",
         "source": "csindex", "flow_adv_days": None},          # 3 天前已生效——过去,不是未来
        {"code": "000005", "index_code": "000905", "index_name": "中证500", "side": "drop",
         "ann_date": "20260529", "eff_close_date": "20260613", "phase": "announced_runup",
         "source": "csindex", "flow_adv_days": None},          # 未来窗内——应保留
    ], columns=ie.EVENT_COLS))
    s = calendar_section(d)
    assert "中证500" in s and "20260613" in s
    assert "沪深300" not in s and "20260608" not in s


def test_calendar_section_shows_rebalance_even_without_unlock_or_disclosure_rows(tmp_path):
    d = _mk_index(tmp_path)
    df = pd.read_csv(d / "calendar.csv", dtype={"code": str})
    df[df["kind"] == "index_rebalance"].to_csv(d / "calendar.csv", index=False)
    s = calendar_section(d)
    assert "指数调样 20260612" in s and "预约披露" not in s


def test_brief_injects_rebalance_eve_line(tmp_path):
    from autoresearch.scan.agents.l4_card import compose_funnel_brief
    d = _mk_index(tmp_path)
    assert "⛔ **指数调样生效前夜**" in compose_funnel_brief("000004", d)
    assert "指数调样" not in compose_funnel_brief("000002", d)


# ─────────────────── fix round 1(2026-09-25):两个源必须互相独立 ───────────────────
def test_calendar_section_reports_rebalance_when_calendar_csv_has_no_rows_for_the_code(tmp_path):
    """fix round 1:`calendar.csv` 的调样行只在 want(L2∪finalists)命中时才由 `harvest_calendar`
    写出;`index_events.csv` 故意不过滤 want。此前 `calendar_section` 先从 `_load(calendar.csv)`
    拿到 `None`(缺失或只剩表头都算)就整函数提前 `return ""`,`index_events.csv` 从未被看一眼——
    一次真实的市场级 passive_close_eve 事实,只因当天没有一只票撞进 L2/finalists 菜单,就被这条
    早退吞掉了。用一个 code 不在菜单里的事件行复现,并核对 finalist 计数确实是 0。同时验证
    「calendar.csv 存在但空」与「calendar.csv 整个不存在」现在必须等价(修复前两者都等价于同一条
    早退,恰好殊途同归到同一个错误答案;修复后不再走那条早退,必须靠新逻辑本身重新殊途同归到
    同一个正确答案)。"""
    ev_rows = pd.DataFrame([
        {"code": "000099", "index_code": "000300", "index_name": "沪深300", "side": "add",
         "ann_date": "20260529", "eff_close_date": "20260612", "phase": "passive_close_eve",
         "source": "csindex", "flow_adv_days": None},
    ], columns=ie.EVENT_COLS)

    d = tmp_path / "2026-06-11"
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(columns=["code", "kind", "event_date", "detail", "ratio"]).to_csv(
        d / "calendar.csv", index=False)                          # 存在,但空(非缺失)
    pd.DataFrame([{"code": "000001", "name": "N000001", "sector": "半导体"}]).to_csv(
        d / "finalists.csv", index=False)
    ie.write_index_events(d, ev_rows)
    s_empty = calendar_section(d)
    assert "- **指数调样 20260612 收盘生效**:" in s_empty
    assert "沪深300 ×1" in s_empty and "finalist 涉及 0 只" in s_empty     # 000099 不是 finalist
    assert "预约披露" not in s_empty and "大解禁" not in s_empty           # 没凭空造出另两段
    # (注:标题的图例文案本身含"披露"/"解禁"两个字,如"解禁=风险窗"——只断言各段自己的行标记
    # "预约披露"/"大解禁",不断言裸字,否则会对着图例文案假摔)

    d2 = tmp_path / "2026-06-12"
    d2.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"code": "000001", "name": "N000001", "sector": "半导体"}]).to_csv(
        d2 / "finalists.csv", index=False)
    ie.write_index_events(d2, ev_rows.assign(eff_close_date="20260613"))  # 日期换一下,验真读的是这张表
    assert not (d2 / "calendar.csv").exists()                     # 整个不存在(非只是空)
    s_missing = calendar_section(d2)
    assert "- **指数调样 20260613 收盘生效**:" in s_missing
    assert "沪深300 ×1" in s_missing and "finalist 涉及 0 只" in s_missing
    assert s_missing.replace("20260613", "20260612") == s_empty   # 缺失 vs 空,除日期外必须等价


def test_calendar_section_returns_empty_when_both_sources_are_genuinely_empty(tmp_path):
    """两个源都在(合法日期目录)却都没有内容 → 交白卷。这条测的是日期解析成功之后
    disc/unlk/has_ev 三者都空的收尾判断,与既有的 `calendar_section(tmp_path / "nope") == ""`
    不是同一条路径——那条测的是目录名解析不出日期时的提前交白卷。"""
    d = tmp_path / "2026-06-11"
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(columns=["code", "kind", "event_date", "detail", "ratio"]).to_csv(
        d / "calendar.csv", index=False)
    pd.DataFrame([{"code": "000001", "name": "N000001", "sector": "半导体"}]).to_csv(
        d / "finalists.csv", index=False)
    assert calendar_section(d) == ""
    d2 = tmp_path / "2026-06-12"                                  # calendar.csv 干脆不存在,同样交白卷
    d2.mkdir(parents=True, exist_ok=True)
    assert calendar_section(d2) == ""


def test_calendar_section_tolerates_a_missing_finalists_csv_for_rebalance_counts(tmp_path):
    """finalist 计数读 `finalists.csv`;这份文件本就一直允许缺失(`fp.exists()` 早已挡在那里,
    这个测试之前没有场景把它和「有真实调样行」撞在一起验过)。缺文件 → `fin` 空集 →
    "finalist 涉及 0 只",不抛异常——市场级调样很可能真的一个本轮 finalist 都不涉及。"""
    d = tmp_path / "2026-06-11"
    d.mkdir(parents=True, exist_ok=True)
    assert not (d / "finalists.csv").exists() and not (d / "calendar.csv").exists()
    ie.write_index_events(d, pd.DataFrame([
        {"code": "000099", "index_code": "000300", "index_name": "沪深300", "side": "add",
         "ann_date": "20260529", "eff_close_date": "20260612", "phase": "passive_close_eve",
         "source": "csindex", "flow_adv_days": None},
    ], columns=ie.EVENT_COLS))
    s = calendar_section(d)
    assert "- **指数调样 20260612 收盘生效**:" in s and "finalist 涉及 0 只" in s
