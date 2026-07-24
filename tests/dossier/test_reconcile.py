"""季度对账契约:express 优先/forecast 兜底/未披露 skip/幂等(Wave3 Task 6)。"""
import pandas as pd

from autoresearch.dossier import delta, reconcile, schema
from tests.dossier.test_delta import _mk_dossier


def _fake_fetch(express_df=None, forecast_df=None):
    def fetch(endpoint, params):
        assert params["ts_code"].endswith((".SZ", ".SH", ".BJ"))   # to_ts_code 路由过
        if endpoint == "express":
            return express_df if express_df is not None else pd.DataFrame()
        if endpoint == "forecast":
            return forecast_df if forecast_df is not None else pd.DataFrame()
        raise AssertionError(endpoint)
    return fetch


def test_reconcile_express_writes_s5_s8_and_frontmatter():
    p = _mk_dossier()
    # yoy_net_profit=去年同期净利润金额(2.0亿),非增长率;n_income=2.5亿 → 自算 +25.0%
    df = pd.DataFrame([{"ann_date": "20260828", "n_income": 2.5e8,
                        "yoy_net_profit": 2.0e8, "diluted_eps": 0.85}])
    res = reconcile.reconcile_one("300857", "20260630", "2026-08-29",
                                  fetch=_fake_fetch(express_df=df))
    assert res["updated"] and res["kind"] == "express" and res["issues"] == []
    text = p.read_text(encoding="utf-8")
    s5 = delta.section_body(text, 4)
    assert "季度对账 20260630" in s5 and "净利 2.5亿" in s5 and "yoy +25.0%" in s5
    assert "季度对账 20260630" in delta.section_body(text, 7)      # §8 也留痕
    assert schema.parse_frontmatter(text)["last_delta"] == "2026-08-29"
    # 幂等:重跑不重复
    reconcile.reconcile_one("300857", "20260630", "2026-08-29",
                            fetch=_fake_fetch(express_df=df))
    assert p.read_text(encoding="utf-8").count("季度对账 20260630") == 2   # §5 一次 + §8 一次


def test_reconcile_forecast_fallback_and_undisclosed():
    _mk_dossier(code="002371")
    fdf = pd.DataFrame([{"ann_date": "20260815", "type": "预增",
                         "p_change_min": 30.0, "p_change_max": 50.0}])
    res = reconcile.reconcile_one("002371", "20260630", "2026-08-16",
                                  fetch=_fake_fetch(forecast_df=fdf))
    assert res["kind"] == "forecast"
    assert "+30%~+50%" in delta.section_body(
        schema.dossier_path("002371").read_text(encoding="utf-8"), 4)
    res2 = reconcile.reconcile_one("002371", "20261231", "2027-01-05",
                                   fetch=_fake_fetch())
    assert res2["skipped"] == "undisclosed"
    # R2-I-1:未披露也落痕(此前只 skip、档案零字节改动,nag 永远无法清除)
    assert res2["recorded"] is True
    text = schema.dossier_path("002371").read_text(encoding="utf-8")
    s5 = delta.section_body(text, 4)
    assert "季度对账 20261231" in s5 and "未披露" in s5
    assert "季度对账 20261231" in delta.section_body(text, 7)      # §8 同款留痕
    assert schema.parse_frontmatter(text)["last_delta"] == "2027-01-05"
    # 20260630(forecast 真数据)那行不受影响
    assert "+30%~+50%" in s5


# ───────── R2-I-1:未披露留痕的升级/降级/幂等三条规则 ─────────


def test_reconcile_undisclosed_then_real_upgrades_s5_line():
    """未披露先落痕,真数据来后整行替换(升级)——§5 该 period 仍只一行且是真数据。"""
    _mk_dossier(code="002371")
    res1 = reconcile.reconcile_one("002371", "20251231", "2026-07-24",
                                   fetch=_fake_fetch())
    assert res1["skipped"] == "undisclosed" and res1["recorded"] is True
    s5_1 = delta.section_body(
        schema.dossier_path("002371").read_text(encoding="utf-8"), 4)
    assert "季度对账 20251231" in s5_1 and "未披露" in s5_1
    # I-2(2026-07-24 终审):「未披露不写 last_refresh」此前零护栏——M6 变异(未披露分支
    # 也写 last_refresh)在 668 测试下全绿存活,既有测试对 last_refresh 一个字都没断言过。
    # 首次对账即未披露 = last_refresh 从未被设过,须仍是 None(only last_delta 前进)。
    fm1 = schema.parse_frontmatter(
        schema.dossier_path("002371").read_text(encoding="utf-8"))
    assert fm1["last_refresh"] is None and fm1["last_delta"] == "2026-07-24"

    df = pd.DataFrame([{"ann_date": "20260227", "n_income": 2.08e8,
                        "yoy_net_profit": 2.92e8, "diluted_eps": 1.41}])
    res2 = reconcile.reconcile_one("002371", "20251231", "2026-08-29",
                                   fetch=_fake_fetch(express_df=df))
    assert res2["updated"] and res2["kind"] == "express"
    s5_2 = delta.section_body(
        schema.dossier_path("002371").read_text(encoding="utf-8"), 4)
    marked = [ln for ln in s5_2.splitlines() if ln.startswith("- **季度对账 20251231**")]
    assert len(marked) == 1                     # 同 period 仍只一行,未叠加
    assert "未披露" not in marked[0]
    assert "净利 2.1亿" in marked[0] and "yoy -28.8%" in marked[0]


def test_reconcile_real_then_undisclosed_does_not_downgrade():
    """已有真数据行,后续再查到未披露(如误触发/端点抖动)不得覆盖——真数据优先。

    T3-m-4(2026-07-24 终审同批建议):I-2 的第二半此前无钉子——真数据分支写
    `last_refresh`(D1)后,D2 未披露分支必须**不**把钟拨到 D2(否则「全量核对」的
    语义被未披露污染)。断言 D2 后 `last_refresh` 仍停在 D1,不前进到 D2。
    """
    _mk_dossier(code="601869")
    df = pd.DataFrame([{"ann_date": "20260828", "n_income": 2.5e8,
                        "yoy_net_profit": 2.0e8, "diluted_eps": 0.85}])
    res1 = reconcile.reconcile_one("601869", "20260630", "2026-08-29",
                                   fetch=_fake_fetch(express_df=df))
    assert res1["updated"]
    s5_1 = delta.section_body(
        schema.dossier_path("601869").read_text(encoding="utf-8"), 4)
    assert "净利 2.5亿" in s5_1 and "未披露" not in s5_1

    res2 = reconcile.reconcile_one("601869", "20260630", "2026-09-01",
                                   fetch=_fake_fetch())
    assert res2["skipped"] == "undisclosed" and res2["recorded"] is True
    s5_2 = delta.section_body(
        schema.dossier_path("601869").read_text(encoding="utf-8"), 4)
    assert s5_2 == s5_1                          # §5 原行完全不动(不降级)
    assert "未披露" not in s5_2
    # §8 仍按日志语义留一条未披露记账(不影响 §5 结论)
    assert "2026-09-01 季度对账 20260630:两端点均无数据,未披露" in delta.section_body(
        schema.dossier_path("601869").read_text(encoding="utf-8"), 7)
    fm = schema.parse_frontmatter(
        schema.dossier_path("601869").read_text(encoding="utf-8"))
    assert fm["last_refresh"] == "2026-08-29"     # D2 未披露不得把钟拨到 D2(T3-m-4)


def test_reconcile_undisclosed_rerun_is_idempotent():
    """同状态(未披露)重跑:§5 该 period 仍只 1 行,不重复堆积。"""
    _mk_dossier(code="002415")
    reconcile.reconcile_one("002415", "20251231", "2026-07-24", fetch=_fake_fetch())
    reconcile.reconcile_one("002415", "20251231", "2026-07-24", fetch=_fake_fetch())
    text = schema.dossier_path("002415").read_text(encoding="utf-8")
    s5 = delta.section_body(text, 4)
    assert s5.count("季度对账 20251231") == 1
    assert len([ln for ln in s5.splitlines()
               if ln.startswith("- **季度对账 20251231**")]) == 1


def test_reconcile_presence_gated():
    assert reconcile.reconcile_one("999999", "20260630", "2026-08-29",
                                   fetch=_fake_fetch())["skipped"] == "no_dossier"


def test_reconcile_nan_fields_not_rendered():
    _mk_dossier(code="601869")
    df = pd.DataFrame([{"ann_date": "20260828", "n_income": float("nan"),
                        "yoy_net_profit": float("nan"), "diluted_eps": 0.5}])
    reconcile.reconcile_one("601869", "20260630", "2026-08-29",
                            fetch=_fake_fetch(express_df=df))
    s5 = delta.section_body(schema.dossier_path("601869").read_text(encoding="utf-8"), 4)
    assert "nan" not in s5 and "摊薄EPS 0.50" in s5      # NaN 穿 or-默认防线(Wave2 教训)


def test_reconcile_forecast_nan_type_not_rendered():
    """forecast 兜底路:type=NaN 且 p_change 双缺 → 字符串腿 NaN 不得渲染(Important-1 回归)。"""
    _mk_dossier(code="002415")
    fdf = pd.DataFrame([{"ann_date": float("nan"), "type": float("nan"),
                         "p_change_min": float("nan"), "p_change_max": float("nan")}])
    res = reconcile.reconcile_one("002415", "20260630", "2026-08-16",
                                  fetch=_fake_fetch(forecast_df=fdf))
    assert res["kind"] == "forecast"
    text = schema.dossier_path("002415").read_text(encoding="utf-8")
    s5 = delta.section_body(text, 4)
    s8 = delta.section_body(text, 7)
    assert "预告类型 —" in s5
    assert "nan" not in s5.lower() and "nan" not in s8.lower()


def test_reconcile_express_picks_latest_ann_date_not_first_row():
    """多行修正公告:旧行放 df 第 0 位,须按 ann_date 降序锁最新披露(Important-2 回归)。"""
    _mk_dossier(code="000725")
    df = pd.DataFrame([
        {"ann_date": "20260815", "n_income": 1e8, "yoy_net_profit": 0.8e8, "diluted_eps": 0.20},
        {"ann_date": "20260828", "n_income": 2.5e8, "yoy_net_profit": 2.0e8, "diluted_eps": 0.85},
    ])
    res = reconcile.reconcile_one("000725", "20260630", "2026-08-29",
                                  fetch=_fake_fetch(express_df=df))
    assert res["updated"]
    s5 = delta.section_body(schema.dossier_path("000725").read_text(encoding="utf-8"), 4)
    assert "净利 2.5亿" in s5 and "yoy +25.0%" in s5
    assert "20260828" in s5
    assert "净利 1.0亿" not in s5 and "20260815" not in s5


def test_reconcile_express_yoy_self_computed_from_live_values():
    """活体真值回归(688766 兰剑智能 20251231 快报,2026-07-24 逮出):yoy_net_profit
    是去年同期净利润金额(元)非增长率,须自算 (n_income/base-1)*100;真值 -28.8% 与该票
    forecast 腿独立口径的"略减 -29.89%"吻合,证明自算路正确而非巧合凑数。
    """
    _mk_dossier(code="688766")
    df = pd.DataFrame([{"ann_date": "20260129", "n_income": 208232900.0,
                        "yoy_net_profit": 292416600.0, "diluted_eps": 1.41,
                        "diluted_roe": 9.0}])
    res = reconcile.reconcile_one("688766", "20251231", "2026-01-30",
                                  fetch=_fake_fetch(express_df=df))
    assert res["updated"]
    s5 = delta.section_body(schema.dossier_path("688766").read_text(encoding="utf-8"), 4)
    assert "yoy -28.8%" in s5
    assert "净利 2.1亿" in s5
    assert "ROE 9.0%" in s5
    assert "292416600" not in s5


def test_reconcile_yoy_base_zero_or_negative_shows_not_applicable_no_div_zero():
    """去年同期净利润 <=0(为零或亏损)→ 增速无意义,只报金额,不出现除零/inf/nan。"""
    _mk_dossier(code="688767")
    df_zero = pd.DataFrame([{"ann_date": "20260828", "n_income": 1e8,
                             "yoy_net_profit": 0.0, "diluted_eps": 0.5}])
    reconcile.reconcile_one("688767", "20260630", "2026-08-29",
                            fetch=_fake_fetch(express_df=df_zero))
    s5_zero = delta.section_body(schema.dossier_path("688767").read_text(encoding="utf-8"), 4)
    assert "增速不适用" in s5_zero
    assert "去年同期 0.0亿" in s5_zero
    assert "inf" not in s5_zero.lower() and "nan" not in s5_zero.lower()

    _mk_dossier(code="688768")
    df_neg = pd.DataFrame([{"ann_date": "20260828", "n_income": 1e8,
                            "yoy_net_profit": -5.0e7, "diluted_eps": 0.5}])
    reconcile.reconcile_one("688768", "20260630", "2026-08-29",
                            fetch=_fake_fetch(express_df=df_neg))
    s5_neg = delta.section_body(schema.dossier_path("688768").read_text(encoding="utf-8"), 4)
    assert "增速不适用" in s5_neg
    assert "inf" not in s5_neg.lower() and "nan" not in s5_neg.lower()


def test_reconcile_yoy_missing_base_no_yoy_segment_but_profit_still_renders():
    """yoy_net_profit 字段缺(未披露该字段)→ 不渲染 yoy/去年同期段,净利仍正常渲染。"""
    _mk_dossier(code="688769")
    df = pd.DataFrame([{"ann_date": "20260828", "n_income": 1e8, "diluted_eps": 0.5}])
    res = reconcile.reconcile_one("688769", "20260630", "2026-08-29",
                                  fetch=_fake_fetch(express_df=df))
    assert res["updated"]
    s5 = delta.section_body(schema.dossier_path("688769").read_text(encoding="utf-8"), 4)
    assert "净利 1.0亿" in s5
    assert "yoy" not in s5
    assert "增速不适用" not in s5


def test_reconcile_sets_last_refresh():
    """季度对账 = 报告期全量核对 → 写 last_refresh(spec:中报季强制全量刷新)。

    N-12(2026-07-24 终审记账):本条是 `last_refresh` 写入路径的**唯一**守卫
    (跨 task 变异 M6 实测:删掉 `reconcile_one` 里那行 `set_frontmatter_key(...,
    "last_refresh", ...)` 后,全量回归里只有本条测试变红)。删本文件前先确认
    有等价断言接手,否则该写入路径会静默失守。
    """
    import pandas as pd

    from autoresearch.dossier import reconcile, schema
    from tests.dossier.test_delta import _mk_dossier
    _mk_dossier(code="300858")
    df = pd.DataFrame([{"ann_date": "20260828", "n_income": 2.5e8,
                        "yoy_net_profit": 2.0e8, "diluted_eps": 0.85}])

    def fetch(endpoint, params):
        return df if endpoint == "express" else pd.DataFrame()

    reconcile.reconcile_one("300858", "20260630", "2026-08-29", fetch=fetch)
    fm = schema.parse_frontmatter(schema.dossier_path("300858").read_text(encoding="utf-8"))
    assert fm["last_refresh"] == "2026-08-29" and fm["last_delta"] == "2026-08-29"


def test_reconcile_main_rejects_malformed_today(capsys):
    """I-1(2026-07-24 终审):`--today` help 写着 YYYY-MM-DD 但此前零格式校验——一次手误
    (如漏横杠 `20260830`)就把畸形日期写进档案,陈旧探针此后 1.5 年都读不出来(终审活体
    复现)。堵在源头:格式非法 → `ap.error` 报错退出(非零 code),不落任何笔。
    """
    import pytest

    from autoresearch.dossier import reconcile as _reconcile
    with pytest.raises(SystemExit) as exc:
        _reconcile.main(["20260630", "--code", "300857", "--today", "20260830"])
    assert exc.value.code != 0
    assert "--today" in capsys.readouterr().err


def test_reconcile_main_rejects_malformed_period(capsys):
    """Wave3.5 终审 I-1(镜像上面的 `..._rejects_malformed_today`):`period` 位置参此前
    零格式校验——手误带横杠(如 `2026-06-30`)让 tushare 两端点必然皆空,必走 undisclosed
    分支,把畸形串永久写进(默认全池 active 时**每一份**)档案的 §5+§8(终审活体复现)。
    堵在源头:格式非法 → `ap.error` 报错退出(非零 code),不落任何笔、不碰任何一份档案。
    """
    import pytest

    from autoresearch.dossier import reconcile as _reconcile
    with pytest.raises(SystemExit) as exc:
        _reconcile.main(["2026-06-30", "--code", "300857"])
    assert exc.value.code != 0
    assert "period" in capsys.readouterr().err
