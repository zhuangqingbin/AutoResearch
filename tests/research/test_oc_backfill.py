"""隔夜集中信号普查 · 回填层(2026-08-28):合成湖 + 注入的假 `call`/`pro`,**零网络**。

覆盖设计稿 §4.6 第 12 条属于本模块的部分:
  幂等(完整分区跳过)/ 写盘不带 `fields` / 合法空 = 落盘且计 empty / 失败不落空 parquet 且
  不中断整批 / 分页 concat 后一次落盘 / 覆盖率分母来自 `lake/daily` 而非交易日历。
外加两条变异探针:把「文件存在即跳过」拆掉、把 `fields` 守卫拆掉 —— 对应断言必须变红,
否则那两个断言是死灯。
"""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.research.overnight_census import backfill as bf

# ───────────────────────── 合成湖 / 假取数端 ─────────────────────────


class FakePro:
    """记录每次调用的 (端点, kwargs);按端点返回预置帧,或抛预置异常。"""

    def __init__(self, frames=None, errors=None):
        self.frames = frames or {}          # endpoint -> DataFrame | callable(kwargs)->DataFrame
        self.errors = errors or {}          # (endpoint, 键值) -> Exception
        self.calls: list[tuple[str, dict]] = []

    def __getattr__(self, endpoint):
        if endpoint.startswith("_"):
            raise AttributeError(endpoint)

        def _endpoint(**kwargs):
            self.calls.append((endpoint, dict(kwargs)))
            for (ep, needle), exc in self.errors.items():
                if ep == endpoint and needle in str(sorted(kwargs.items())):
                    raise exc
            spec = self.frames.get(endpoint, pd.DataFrame())
            return spec(kwargs) if callable(spec) else spec

        return _endpoint


def _counting_call():
    """契约的 `call(fn)`:执行零参可调用。返回 (call, 调用次数列表)。"""
    hits: list[int] = []

    def call(fn):
        hits.append(1)
        return fn()

    return call, hits


def _make_lake(tmp_path, days=("20220302", "20220303", "20220304")):
    """合成湖:只放 `lake/daily/<day>.parquet`(交易日轴真值源),其余表全空。"""
    daily = tmp_path / "daily"
    daily.mkdir(parents=True)
    for d in days:
        pd.DataFrame({"ts_code": ["000001.SZ"], "close": [10.0]}).to_parquet(
            daily / f"{d}.parquet", index=False)
    return tmp_path


@pytest.fixture
def lake(tmp_path, monkeypatch):
    """把 `workspace.lake_root()` 指到 tmp —— 绝不碰真 `lake/`。"""
    root = _make_lake(tmp_path / "lake")
    monkeypatch.setattr(bf.ws, "lake_root", lambda: root)
    return root


ROWS = pd.DataFrame({"ts_code": ["000001.SZ", "600000.SH"], "trade_date": ["x", "x"],
                     "close": [1.0, 2.0]})


# ───────────────────────── 1 · 幂等:文件在即跳过,一次网络都不发 ─────────────────────────


def test_existing_partition_is_skipped_without_calling(lake):
    """完整分区跳过 —— 且 `call` **一次都不调**(不是「调了但丢弃」)。"""
    for d in ("20220302", "20220303", "20220304"):
        bf._save(ROWS, bf.partition_path("daily_basic", d, lake))
    call, hits = _counting_call()
    pro = FakePro({"daily_basic": ROWS})
    res = bf.backfill_all(tables=["daily_basic"], call=call, pro=pro, sleep=0.0,
                          progress=lambda e: None)
    assert res["daily_basic"]["skipped"] == 3
    assert res["daily_basic"]["fetched"] == 0
    assert hits == []
    assert pro.calls == []


def test_rerun_is_idempotent(lake):
    """跑两遍:第二遍全 skipped,行数/文件不变。"""
    pro = FakePro({"daily_basic": ROWS})
    call, _ = _counting_call()
    kw = {"tables": ["daily_basic"], "call": call, "pro": pro, "sleep": 0.0,
          "progress": lambda e: None}
    first = bf.backfill_all(**kw)
    n_calls_after_first = len(pro.calls)
    second = bf.backfill_all(**kw)
    assert first["daily_basic"]["fetched"] == 3
    assert second["daily_basic"]["fetched"] == 0
    assert second["daily_basic"]["skipped"] == 3
    assert len(pro.calls) == n_calls_after_first


def test_mutation_probe_skip_guard_is_alive(lake, monkeypatch):
    """变异探针:拆掉「文件存在即跳过」→ 上面那条幂等断言必须变红,否则它是死灯。"""
    for d in ("20220302", "20220303", "20220304"):
        bf._save(ROWS, bf.partition_path("daily_basic", d, lake))
    real_path = bf.partition_path
    monkeypatch.setattr(bf, "partition_path",
                        lambda *a, **k: _MissingPath(real_path(*a, **k)))
    call, hits = _counting_call()
    pro = FakePro({"daily_basic": ROWS})
    res = bf.backfill_all(tables=["daily_basic"], call=call, pro=pro, sleep=0.0,
                          progress=lambda e: None)
    assert res["daily_basic"]["skipped"] == 0      # 变异体下断言 skipped==3 会红
    assert hits == [1, 1, 1]


class _MissingPath:
    """变异体用:一个永远 `exists() is False` 的 path 代理(其余行为原样透传)。"""

    def __init__(self, path):
        self._p = path

    def exists(self):
        return False

    def __fspath__(self):
        return str(self._p)

    def __getattr__(self, name):
        return getattr(self._p, name)


# ───────────────────────── 2 · 写盘不带 fields(窄表毒化判例)─────────────────────────


def test_requests_never_carry_fields(lake):
    """每一次真实调用的 kwargs 里都**没有** `fields` 键 —— 少一列是灾难,多几列无害。"""
    pro = FakePro({"daily_basic": ROWS, "top_inst": ROWS, "moneyflow": ROWS,
                   "block_trade": ROWS, "hk_hold": ROWS, "limit_list_d": ROWS,
                   "disclosure_date": ROWS, "share_float": ROWS, "dividend": ROWS})
    call, _ = _counting_call()
    bf.backfill_all(call=call, pro=pro, sleep=0.0, progress=lambda e: None)
    assert pro.calls, "没有发生任何调用,这条断言就没在检查什么"
    for endpoint, kwargs in pro.calls:
        assert "fields" not in kwargs, f"{endpoint} 请求带了 fields"


def test_fields_guard_rejects_narrow_request(lake):
    """变异探针的另一半:守卫本身有牙 —— 显式塞 `fields` 必须抛。"""
    pro = FakePro({"daily_basic": ROWS})
    call, _ = _counting_call()
    with pytest.raises(bf.BackfillError):
        bf._fetch(lambda: pro, call, "daily_basic",
                  {"trade_date": "20220302", "fields": "ts_code"})


# ───────────────────────── 3 · 合法空:落盘 + 计 empty ─────────────────────────


def test_empty_result_is_persisted_and_counted(lake):
    """空帧也落盘(否则每次重跑都把空日重拉一遍),计 `empty`,且 `empty ⊆ fetched`。"""
    pro = FakePro({"top_inst": pd.DataFrame()})
    call, _ = _counting_call()
    res = bf.backfill_all(tables=["top_inst"], call=call, pro=pro, sleep=0.0,
                          progress=lambda e: None)
    assert res["top_inst"]["empty"] == 3
    assert res["top_inst"]["fetched"] == 3
    assert res["top_inst"]["rows"] == 0
    for d in ("20220302", "20220303", "20220304"):
        assert bf.partition_path("top_inst", d, lake).exists()
    # 空日再跑不重拉
    before = len(pro.calls)
    bf.backfill_all(tables=["top_inst"], call=call, pro=pro, sleep=0.0, progress=lambda e: None)
    assert len(pro.calls) == before


def test_none_result_is_treated_as_empty(lake):
    """端点返回 `None`(tushare 偶发)不得炸,按合法空处理。"""
    pro = FakePro({"hk_hold": lambda kw: None})
    call, _ = _counting_call()
    res = bf.backfill_all(tables=["hk_hold"], call=call, pro=pro, sleep=0.0,
                          progress=lambda e: None)
    assert res["hk_hold"]["empty"] == 3
    assert res["hk_hold"]["failed"] == 0


# ───────────────────────── 4 · 单键失败不中断整批,且不落空 parquet ─────────────────────────


def test_single_day_failure_does_not_abort_batch(lake):
    """一天炸掉 → `failed` +1、进 `failed_keys`、**不落盘**,其余两天照常完成。"""
    pro = FakePro({"moneyflow": ROWS},
                  errors={("moneyflow", "20220303"): RuntimeError("抱歉,您每分钟最多访问该接口")})
    call, _ = _counting_call()
    res = bf.backfill_all(tables=["moneyflow"], call=call, pro=pro, sleep=0.0,
                          progress=lambda e: None)
    stat = res["moneyflow"]
    assert stat["failed"] == 1
    assert stat["fetched"] == 2
    assert [f["key"] for f in stat["failed_keys"]] == ["20220303"]
    assert "RuntimeError" in stat["failed_keys"][0]["error"]
    assert not bf.partition_path("moneyflow", "20220303", lake).exists()
    assert bf.partition_path("moneyflow", "20220304", lake).exists()


def test_failed_key_is_retried_next_run(lake):
    """失败不落盘 ⇒ 下次重跑会**重试它**(而不是被当成「取过且为空」永久跳过)。"""
    boom = FakePro({"moneyflow": ROWS},
                   errors={("moneyflow", "20220303"): RuntimeError("boom")})
    call, _ = _counting_call()
    bf.backfill_all(tables=["moneyflow"], call=call, pro=boom, sleep=0.0,
                    progress=lambda e: None)
    ok = FakePro({"moneyflow": ROWS})
    res = bf.backfill_all(tables=["moneyflow"], call=call, pro=ok, sleep=0.0,
                          progress=lambda e: None)
    assert res["moneyflow"]["fetched"] == 1
    assert res["moneyflow"]["skipped"] == 2
    assert [kw["trade_date"] for _, kw in ok.calls] == ["20220303"]


# ───────────────────────── 5 · disclosure_date 分页 ─────────────────────────


def _paged_frames(page: int, counts: list[int]):
    """按 offset 顺序返回给定行数的页。"""

    def _fn(kwargs):
        offset = int(kwargs["offset"])
        idx = offset // page
        n = counts[idx] if idx < len(counts) else 0
        return pd.DataFrame({"ts_code": [f"{offset + i:06d}.SZ" for i in range(n)],
                             "end_date": ["20211231"] * n})

    return _fn


def test_disclosure_pagination_concats_into_one_file(lake):
    """满页 / 满页 / 半页 → 落盘行数 = 三页之和,且**只写一个文件**。"""
    page = bf.DISCLOSURE_PAGE
    counts = [page, page, 137]
    pro = FakePro({"disclosure_date": _paged_frames(page, counts)})
    call, _ = _counting_call()
    res = bf.backfill_all(tables=["disclosure_date"], call=call, pro=pro, sleep=0.0,
                          until="20211231", progress=lambda e: None)
    stat = res["disclosure_date"]
    assert stat["keys"] == 1 and stat["fetched"] == 1
    assert stat["pages"] == 3
    assert stat["rows"] == sum(counts)
    base = lake / "events" / "disclosure_date"
    assert sorted(p.name for p in base.glob("*.parquet")) == ["20211231.parquet"]
    assert len(pd.read_parquet(base / "20211231.parquet")) == sum(counts)
    assert [kw["offset"] for _, kw in pro.calls] == [0, page, 2 * page]


def test_pagination_stops_on_exactly_empty_page(lake):
    """恰好整除时最后一页是 0 行 —— 必须停,并且这一页的 0 行不能被当成「本键为空」。"""
    page = bf.DISCLOSURE_PAGE
    pro = FakePro({"disclosure_date": _paged_frames(page, [page, 0])})
    call, _ = _counting_call()
    res = bf.backfill_all(tables=["disclosure_date"], call=call, pro=pro, sleep=0.0,
                          until="20211231", progress=lambda e: None)
    assert res["disclosure_date"]["pages"] == 2
    assert res["disclosure_date"]["rows"] == page
    assert res["disclosure_date"]["empty"] == 0


def test_pagination_refuses_to_loop_forever(lake):
    """`offset` 被端点忽略(永远满页)→ 抛 `BackfillError`,计 failed,而不是抄同一页 400 遍。"""
    page = bf.SHARE_FLOAT_PAGE
    pro = FakePro({"share_float": lambda kw: pd.DataFrame({"ts_code": ["x"] * page})})
    call, _ = _counting_call()
    res = bf.backfill_all(tables=["share_float"], call=call, pro=pro, sleep=0.0,
                          since="20220302", until="20220331", progress=lambda e: None)
    assert res["share_float"]["failed"] == 1
    assert "BackfillError" in res["share_float"]["failed_keys"][0]["error"]
    assert not (lake / "events" / "share_float" / "202203.parquet").exists()


def test_share_float_uses_month_windows(lake):
    """`share_float` 走逐月区间键,参数是当月首末日(实测单月 15 页,故必须分页)。"""
    pro = FakePro({"share_float": _paged_frames(bf.SHARE_FLOAT_PAGE, [12])})
    call, _ = _counting_call()
    res = bf.backfill_all(tables=["share_float"], call=call, pro=pro, sleep=0.0,
                          since="20220302", until="20220430", progress=lambda e: None)
    assert res["share_float"]["keys"] == 2
    spans = [(kw["start_date"], kw["end_date"]) for _, kw in pro.calls]
    assert spans == [("20220301", "20220331"), ("20220401", "20220430")]
    assert sorted(p.stem for p in (lake / "events" / "share_float").glob("*.parquet")) == \
        ["202203", "202204"]


def test_empty_lake_produces_no_dated_work(tmp_path, monkeypatch):
    """湖里一个 daily 分区都没有 → **按日期取键的表**零工作量,不抛、不凭空造日子。

    唯一例外是 `disclosure_date`:报告期不需要价格锚(2021Q4..2026Q2 是日历事实),
    它照常跑 19 个键 —— 这条差异写在这里,免得以后被当成漏网。
    """
    root = tmp_path / "lake"
    (root / "daily").mkdir(parents=True)
    monkeypatch.setattr(bf.ws, "lake_root", lambda: root)
    pro = FakePro()
    res = bf.backfill_all(call=lambda fn: fn(), pro=pro, sleep=0.0, progress=lambda e: None)
    assert {ep for ep, _ in pro.calls} == {"disclosure_date"}
    assert res["disclosure_date"]["keys"] == 19
    dated = [t for t in bf.TABLES if t != "disclosure_date"]
    assert all(res[t]["keys"] == 0 for t in dated)
    assert all(res[t]["fetched"] == 0 and res[t]["failed"] == 0 for t in dated)
    # 但给了显式窗就有锚:share_float 仍能按月回填(daily 表依旧为 0,湖里没有那些天)
    res2 = bf.backfill_all(tables=["share_float", "daily_basic"], since="20220301",
                           until="20220430", call=lambda fn: fn(),
                           pro=FakePro({"share_float": _paged_frames(bf.SHARE_FLOAT_PAGE, [3])}),
                           sleep=0.0, progress=lambda e: None)
    assert res2["share_float"]["keys"] == 2
    assert res2["daily_basic"]["keys"] == 0


def test_dividend_keys_are_trade_days(lake):
    """`dividend` 逐日 `ex_date=`,键取自湖交易日(不分页:实测单日 ≤110 行)。"""
    pro = FakePro({"dividend": ROWS})
    call, _ = _counting_call()
    res = bf.backfill_all(tables=["dividend"], call=call, pro=pro, sleep=0.0,
                          progress=lambda e: None)
    assert res["dividend"]["fetched"] == 3
    assert [kw["ex_date"] for _, kw in pro.calls] == ["20220302", "20220303", "20220304"]
    assert all("offset" not in kw for _, kw in pro.calls)


def test_disclosure_periods_are_quarter_ends():
    """报告期清单 = 2021Q4..2026Q2 的季末日,19 个,一个不多一个不少。"""
    periods = bf.quarter_ends()
    assert periods[0] == "20211231" and periods[-1] == "20260630"
    assert len(periods) == 19
    assert all(p[4:] in ("0331", "0630", "0930", "1231") for p in periods)


def test_disclosure_periods_never_exceed_until(lake):
    """`until` 之后的报告期不落空盘 —— 尚未开始披露的季度会持续变,钉死就是钉死一个半成品。"""
    pro = FakePro({"disclosure_date": _paged_frames(bf.DISCLOSURE_PAGE, [5])})
    call, _ = _counting_call()
    bf.backfill_all(tables=["disclosure_date"], call=call, pro=pro, sleep=0.0,
                    until="20230101", progress=lambda e: None)
    assert [kw["end_date"] for _, kw in pro.calls] == ["20211231", "20220331", "20220630",
                                                       "20220930", "20221231"]


# ───────────────────────── 6 · coverage_report ─────────────────────────


def test_coverage_report_counts_against_lake_daily(lake):
    """逐日表覆盖率:分母 = `lake/daily` 在 [first,last] 窗内的交易日数。"""
    bf._save(ROWS, bf.partition_path("daily_basic", "20220302", lake))
    bf._save(ROWS, bf.partition_path("daily_basic", "20220304", lake))
    rep = bf.coverage_report(tables=["daily_basic"], lake_root=lake)["daily_basic"]
    assert rep["n_files"] == 2
    assert (rep["first"], rep["last"]) == ("20220302", "20220304")
    assert rep["expected_days"] == 3
    assert rep["coverage"] == pytest.approx(2 / 3)
    assert rep["missing_sample"] == ["20220303"]


def test_coverage_report_event_tables_only_report_files(lake):
    """事件表只报文件数与区间:`expected_days`/`coverage` 恒 `None`(**不是 0**)。"""
    bf._save(ROWS, bf.partition_path("share_float", "202203", lake))
    bf._save(ROWS, bf.partition_path("dividend", "20220302", lake))
    rep = bf.coverage_report(tables=["share_float", "dividend"], lake_root=lake)
    assert rep["share_float"]["n_files"] == 1
    assert rep["share_float"]["expected_days"] is None
    assert rep["share_float"]["coverage"] is None
    assert rep["dividend"]["first"] == "20220302"


def test_coverage_report_empty_table(lake):
    """一个分区都没有 → n_files 0 / first,last None / coverage 0.0(不抛)。"""
    rep = bf.coverage_report(tables=["hk_hold"], lake_root=lake)["hk_hold"]
    assert rep == {"kind": "daily", "n_files": 0, "first": None, "last": None,
                   "expected_days": 0, "coverage": 0.0, "missing_sample": []}


def test_coverage_report_full_after_backfill(lake):
    """回填完 → 覆盖率 1.0、缺日清单空(这是「跑完了」的验收判据)。"""
    pro = FakePro({"limit_list_d": ROWS})
    call, _ = _counting_call()
    bf.backfill_all(tables=["limit_list_d"], call=call, pro=pro, sleep=0.0,
                    progress=lambda e: None)
    rep = bf.coverage_report(tables=["limit_list_d"], lake_root=lake)["limit_list_d"]
    assert rep["coverage"] == 1.0
    assert rep["missing_sample"] == []


# ───────────────────────── 7 · 交易日轴来自湖,不是交易日历 ─────────────────────────


def test_trade_days_come_from_lake_daily_filenames(tmp_path, monkeypatch):
    """合成湖只放 3 个 `daily` 分区 → 只回填这 3 天(20220301 是交易日但湖里没有 → 不存在)。"""
    root = _make_lake(tmp_path / "lake", days=("20220302", "20220303", "20220304"))
    monkeypatch.setattr(bf.ws, "lake_root", lambda: root)

    def _explode(*a, **k):     # 交易日历一旦被碰,这条测试就该红
        raise AssertionError("回填不得查 trade_cal —— 交易日轴只认湖里的 daily 分区")

    monkeypatch.setattr("autoresearch.data.tushare_source._trade_days", _explode)
    pro = FakePro({"daily_basic": ROWS})
    call, _ = _counting_call()
    res = bf.backfill_all(tables=["daily_basic"], call=call, pro=pro, sleep=0.0,
                          progress=lambda e: None)
    assert res["daily_basic"]["fetched"] == 3
    assert [kw["trade_date"] for _, kw in pro.calls] == ["20220302", "20220303", "20220304"]


def test_since_until_clip_the_axis(lake):
    """`since`/`until` 只做裁剪,不能凭空造出湖里没有的日子。"""
    days = bf.trade_days("20220303", "20991231", lake_root=lake)
    assert days == ["20220303", "20220304"]
    assert bf.trade_days("20200101", "20220302", lake_root=lake) == ["20220302"]
    assert bf.trade_days("20260101", None, lake_root=lake) == []


# ───────────────────────── 杂项:契约面 ─────────────────────────


def test_tables_registry_matches_contract():
    """9 张表、两类键法;`top_list` 不在其中(19 天且不可靠,普查不用它)。"""
    assert list(bf.TABLES) == ["limit_list_d", "daily_basic", "top_inst", "block_trade",
                               "moneyflow", "hk_hold", "disclosure_date", "share_float",
                               "dividend"]
    assert bf.DAILY_TABLES == ("limit_list_d", "daily_basic", "top_inst", "block_trade",
                               "moneyflow", "hk_hold")
    assert bf.EVENT_TABLES == ("disclosure_date", "share_float", "dividend")
    assert "top_list" not in bf.TABLES
    assert all(s["available"] for s in bf.TABLES.values())   # 2026-08-28 探针:四端点全通


def test_unknown_table_fails_fast(lake):
    """未登记的表名 → 抛,不静默跳过(静默跳过 = 一个永远不会红的绿灯)。"""
    with pytest.raises(bf.BackfillError):
        bf.backfill_all(tables=["cyq_perf"], call=lambda fn: fn(), pro=FakePro(), sleep=0.0)
    with pytest.raises(bf.BackfillError):
        bf.coverage_report(tables=["cyq_perf"], lake_root=lake)


def test_unavailable_table_is_skipped_with_reason(lake, monkeypatch):
    """探针若判某端点权限不足 → `available=False` 整表跳过并说明,**不伪造成功**。"""
    monkeypatch.setitem(bf.TABLES["dividend"], "available", False)
    monkeypatch.setitem(bf.TABLES["dividend"], "reason", "2000 积分不足")
    pro = FakePro({"dividend": ROWS})
    res = bf.backfill_all(tables=["dividend"], call=lambda fn: fn(), pro=pro, sleep=0.0,
                          progress=lambda e: None)
    assert res["dividend"]["unavailable"] is True
    assert res["dividend"]["reason"] == "2000 积分不足"
    assert res["dividend"]["fetched"] == 0
    assert pro.calls == []


def test_progress_callback_fires_and_defaults_to_stderr(lake, capsys):
    """进度可注入;缺省打到 stderr —— 6000 次调用的任务必须能看见活着。"""
    seen: list[dict] = []
    pro = FakePro({"daily_basic": ROWS})
    bf.backfill_all(tables=["daily_basic"], call=lambda fn: fn(), pro=pro, sleep=0.0,
                    progress=seen.append)
    assert seen and seen[-1]["done"] == seen[-1]["total"] == 3
    assert seen[-1]["table"] == "daily_basic" and seen[-1]["fetched"] == 3

    for d in ("20220302", "20220303", "20220304"):
        bf.partition_path("daily_basic", d, lake).unlink()
    bf.backfill_all(tables=["daily_basic"], call=lambda fn: fn(), sleep=0.0,
                    pro=FakePro({"daily_basic": ROWS}))
    assert "[oc-backfill] daily_basic 3/3" in capsys.readouterr().err


def test_save_is_atomic_and_leaves_no_tmp(lake):
    """原子写:落盘后目录里不留 `.tmp`(半截 parquet 在续传里长得跟完成品一样)。"""
    fp = bf.partition_path("block_trade", "20220302", lake)
    bf._save(ROWS, fp)
    assert fp.exists()
    assert list(fp.parent.glob("*.tmp")) == []
    assert len(pd.read_parquet(fp)) == len(ROWS)


def test_month_helpers():
    assert bf.month_keys("202211", "202302") == ["202211", "202212", "202301", "202302"]
    assert bf.month_keys("202302", "202211") == []
    assert bf.month_span("202402") == ("20240201", "20240229")   # 闰年
    assert bf.month_span("202302") == ("20230201", "20230228")
