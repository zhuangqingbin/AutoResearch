"""全市场事件计数契约(Wave4 Task 2):湖优先、缺数据降级留痕、正催化口径。

Review Round 1(2026-07-25)修复后新增/改造的测试见各函数 docstring 里的 I-x/m-x 标记,
对应 `.superpowers/sdd/w4-task-2-report.md` Fix Round 1 节。全部测试用 `_patch_days`
monkeypatch `l3_news._trade_days_for`,保持 hermetic(`market_event_counts` 内部是**局部
import**——`from autoresearch.scan.agents.l3_news import _trade_days_for` 写在函数体内,
在调用前 patch `l3_news` 模块属性即会在下次调用时生效,不需要给 `market_event_counts`
额外加 `days=` 注入参)。
"""
import pandas as pd

from autoresearch.scan import events
from autoresearch.scan.agents import l3_news


def _patch_days(monkeypatch, days):
    monkeypatch.setattr(l3_news, "_trade_days_for", lambda date, n: list(days))


def _frames():
    """三端点的日帧(字段名与真身一致:holdertrade 用 in_de,repurchase 用 proc)。"""
    return {
        "stk_holdertrade": pd.DataFrame(
            [{"ts_code": "300857.SZ", "in_de": "IN"}, {"ts_code": "002371.SZ", "in_de": "DE"}]),
        "repurchase": pd.DataFrame(
            [{"ts_code": "300857.SZ", "proc": "实施"}, {"ts_code": "600000.SH", "proc": "预案"}]),
        "stk_surv": pd.DataFrame(
            [{"ts_code": "300857.SZ"}, {"ts_code": "300857.SZ"}, {"ts_code": "002371.SZ"}]),
    }


def test_market_event_counts_whole_market_no_code_filter(monkeypatch):
    """全市场:不传 want,湖里有谁就算谁(与 L3 阶段只算 L2-200 的口径相反)。"""
    fr = _frames()
    _patch_days(monkeypatch, ["20260724"])
    monkeypatch.setattr(events, "_fetch_day",
                        lambda ep, day, date: fr.get(ep, pd.DataFrame()))
    ev = events.market_event_counts("2026-07-24", lookback_days=1)
    assert set(ev["code"]) == {"300857", "002371", "600000"}
    r = ev.set_index("code").loc["300857"]
    assert r["ev_holder_in"] == 1 and r["ev_rep_impl"] == 1 and r["ev_surv_n"] == 2
    assert ev.set_index("code").loc["002371"]["ev_holder_de"] == 1


def test_ev_pos_excludes_reduction(monkeypatch):
    """正催化口径与 catalyst_ledger._POS 对齐:减持不算正;调研封顶只贡献 1(I-5)。"""
    fr = _frames()
    _patch_days(monkeypatch, ["20260724"])
    monkeypatch.setattr(events, "_fetch_day", lambda ep, day, date: fr.get(ep, pd.DataFrame()))
    ev = events.market_event_counts("2026-07-24", lookback_days=1).set_index("code")
    assert ev.loc["002371", "ev_pos"] == 1          # holder_de 不计;surv_n=1 封顶后仍是 1
    assert ev.loc["300857", "ev_pos"] == 3          # ev_hard(impl1+in1=2) + min(surv2,1)=1 → 3


def test_ev_pos_survey_capped_below_hard_events(monkeypatch):
    """I-5 回归锁:一次接待 82 家机构调研的票,ev_pos 不得高于一次回购实施+一次增持的票。

    2026-07-21 真湖裸求和口径下,按 ev_pos 降序 top10 曾 10/10 是纯调研(max=82,000729
    一次接待 82 家机构;而一次回购实施只算 1)——归一化后不得再复现这个形态。
    """
    fr = {
        "stk_holdertrade": pd.DataFrame([{"ts_code": "300857.SZ", "in_de": "IN"}]),
        "repurchase": pd.DataFrame([{"ts_code": "300857.SZ", "proc": "实施"}]),
        "stk_surv": pd.DataFrame([{"ts_code": "000729.SZ"}] * 82),
    }
    _patch_days(monkeypatch, ["20260724"])
    monkeypatch.setattr(events, "_fetch_day", lambda ep, day, date: fr.get(ep, pd.DataFrame()))
    ev = events.market_event_counts("2026-07-24", lookback_days=1).set_index("code")
    assert ev.loc["000729", "ev_surv_n"] == 82                 # 82 家机构调研属实,原始计数不折损
    assert ev.loc["000729", "ev_pos"] == 1.0                   # 调研封顶只贡献 1
    assert ev.loc["300857", "ev_pos"] == 2.0                   # 回购实施 1 + 增持 1,无调研贡献
    assert ev.loc["000729", "ev_pos"] < ev.loc["300857", "ev_pos"], \
        "82 家机构调研不得压过一次回购实施+一次增持——ev_pos 不是裸求和"


def test_market_event_counts_all_legs_fail_is_loud(monkeypatch, capsys):
    """三腿全失败 → 空帧 + 显式告警(降级留痕),不静默返回空。

    I-3 hermetic 修复:此前未 patch `_trade_days_for` 时,离线/无 token 环境下它会因网络
    异常被自身 try/except 吞掉返回 `[]`,循环体一次都不执行、`ok` 恒 0——测试碰巧也通过,
    但通过的原因是"日历读不到"而不是"三腿都取数失败",对真实失败模式零鉴别力(实测
    `TUSHARE_TOKEN=""` 时该测试此前直接 FAIL,因为函数体内还会真打一次网络请求)。这里
    显式 patch 日历返回非空日期列表,再让 `_fetch_day` 对每条腿每天都真的抛异常,才是
    名副其实的"三腿全失败"测试。
    """
    _patch_days(monkeypatch, ["20260724"])

    def _boom(ep, day, date):
        raise RuntimeError("no permission")

    monkeypatch.setattr(events, "_fetch_day", _boom)
    ev = events.market_event_counts("2026-07-24", lookback_days=1)
    assert list(ev.columns)[0] == "code" and len(ev) == 0
    assert "事件取数三腿全失败" in (capsys.readouterr().err)


def test_market_event_counts_single_leg_failure_is_loud(monkeypatch, capsys):
    """I-4 回归锁:三腿里 1 条出数、2 条失败时,`ok>0` 不再落进"三腿全失败"分支,但挂掉的
    两条腿必须逐条单独告警——不许因为整体 ok>0 就对腿粒度失明(与紧邻的上一个 commit
    《Wave4 Task 1》根治的"anns_d 静默产零值"同族病灶)。
    """
    _patch_days(monkeypatch, ["20260724"])
    good = pd.DataFrame([{"ts_code": "300857.SZ", "in_de": "IN"}])

    def _fetch(ep, day, date):
        if ep == "stk_holdertrade":
            return good
        raise RuntimeError("no permission")            # repurchase / stk_surv 两腿挂

    monkeypatch.setattr(events, "_fetch_day", _fetch)
    ev = events.market_event_counts("2026-07-24", lookback_days=1)
    assert len(ev) == 1 and ev.set_index("code").loc["300857", "ev_holder_in"] == 1

    err = capsys.readouterr().err
    assert "三腿全失败" not in err, "只有 2/3 腿失败,不该报成三腿全失败"
    assert "repurchase" in err and "stk_surv" in err, "两条挂掉的腿必须逐条被点名告警"
    assert err.count("⚠️") == 2, "每条失败腿各一行告警,不多不少"


def test_market_event_counts_covers_more_than_200_codes(monkeypatch):
    """I-2 回归锁:本 task 头号交付主张是"L2-200 → 全市场",此前无护栏(fixture 只有 3 个
    code,永远碰不到 200,变异"want 截断成前 200 个"能带绿全套件)。构造 500 个合成 code
    的场景,断言全市场覆盖不被截断。
    """
    n = 500
    codes = [f"{i:06d}" for i in range(n)]
    holder = pd.DataFrame([{"ts_code": f"{c}.SZ", "in_de": "IN"} for c in codes])
    fr = {"stk_holdertrade": holder,
          "repurchase": pd.DataFrame(columns=["ts_code", "proc"]),
          "stk_surv": pd.DataFrame(columns=["ts_code"])}
    _patch_days(monkeypatch, ["20260724"])
    monkeypatch.setattr(events, "_fetch_day", lambda ep, day, date: fr.get(ep, pd.DataFrame()))
    ev = events.market_event_counts("2026-07-24", lookback_days=1)
    assert len(ev) == n, "want 截断成前 200 会在此处丢票(变异 C 的靶子)"
    assert set(ev["code"]) == set(codes)


def test_market_event_counts_dtype_is_float_both_paths(monkeypatch):
    """m-1:全失败(`_empty()`)与正常两条返回路,EVENT_COLS 的 dtype 必须一致——此前
    `_empty()` 声明 float64,正常路的原始 5 列继承自 `catalyst_counts` 是 int64(今天靠
    `attach_event_cols` 的 `fillna(0.0)` 隐式兜住,不是显式契约)。
    """
    _patch_days(monkeypatch, ["20260724"])
    monkeypatch.setattr(events, "_fetch_day", lambda ep, day, date: pd.DataFrame())
    empty_ev = events.market_event_counts("2026-07-24", lookback_days=1)   # 全空不抛异常 → ok=0

    fr = _frames()
    monkeypatch.setattr(events, "_fetch_day", lambda ep, day, date: fr.get(ep, pd.DataFrame()))
    nonempty_ev = events.market_event_counts("2026-07-24", lookback_days=1)

    for c in events.EVENT_COLS:
        assert empty_ev[c].dtype == "float64", c
        assert nonempty_ev[c].dtype == "float64", c


def test_attach_event_cols_parity_and_fill():
    """不就地改入参(I-1 修复)。断言必须覆盖"空 ev"分支——那才是真正会改到调用方的分支
    (`out[c] = 0.0` 直接写入参;merge 分支会重新绑定 `out`,入参列本就不受影响)。改用
    `assert_frame_equal` 深比较而非只比列名,鉴别力更强(此前 `out = scored.copy()` →
    `out = scored` 的变异能让 4 个测试全绿)。
    """
    scored = pd.DataFrame({"code": ["300857", "999999"], "composite": [50.0, 60.0]})
    scored_snapshot = scored.copy(deep=True)
    ev = pd.DataFrame({"code": ["300857"], "ev_rep_impl": [1.0], "ev_rep_plan": [0.0],
                       "ev_holder_in": [1.0], "ev_holder_de": [0.0], "ev_surv_n": [2.0],
                       "ev_hard": [2.0], "ev_pos": [3.0]})
    ev_snapshot = ev.copy(deep=True)

    out = events.attach_event_cols(scored, ev)
    assert len(out) == 2 and out.set_index("code").loc["999999", "ev_pos"] == 0.0
    assert out.set_index("code").loc["300857", "ev_pos"] == 3.0
    pd.testing.assert_frame_equal(scored, scored_snapshot)     # 非空 ev 分支:入参深比较不变
    pd.testing.assert_frame_equal(ev, ev_snapshot)

    # 空事件帧 → 全 0 列(parity:下游 channel 拿到列但恒 0 = 空帧降级)
    out2 = events.attach_event_cols(scored, pd.DataFrame({"code": []}))
    assert all(c in out2.columns for c in events.EVENT_COLS)
    assert out2["ev_pos"].sum() == 0.0
    # I-1 关键验证点:空 ev 分支是真正会改到调用方的分支——断言必须排在这次调用之后,
    # 否则对 `out = scored.copy()` → `out = scored` 这类变异零鉴别力。
    pd.testing.assert_frame_equal(scored, scored_snapshot)


def test_attach_event_cols_zfills_short_and_suffixed_codes():
    """m-3:变异"ev 侧 code 不 zfill"此前存活,因 fixture 全是标准 6 位码。补短码(丢前导零,
    本仓 `finalists.csv` 有前科,见 memory `scan-finalists-ticker-zfill-bug`)与带交易所
    后缀的码,两种都必须归一化到 6 位裸码才能与 `scored` 对齐。
    """
    scored = pd.DataFrame({"code": ["000729", "300857"], "composite": [10.0, 20.0]})
    ev = pd.DataFrame({
        "code": ["729", "300857.SZ"],                          # 短码(丢前导零) / 带后缀
        "ev_rep_impl": [1.0, 0.0], "ev_rep_plan": [0.0, 0.0],
        "ev_holder_in": [0.0, 1.0], "ev_holder_de": [0.0, 0.0], "ev_surv_n": [0.0, 0.0],
        "ev_hard": [1.0, 1.0], "ev_pos": [1.0, 1.0],
    })
    out = events.attach_event_cols(scored, ev).set_index("code")
    assert out.loc["000729", "ev_rep_impl"] == 1.0, "短码 729 须 zfill 成 000729 才能对上 scored"
    assert out.loc["300857", "ev_holder_in"] == 1.0, "带 .SZ 后缀的码须剥离后缀才能对上 scored"
