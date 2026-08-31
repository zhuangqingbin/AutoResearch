"""stock-research 结果账本(D5.1)—— 独立产物(full 报告 / lite 决策卡)事后读数。

只记不学:本模块只做两件事——`ingest`(发现新产物,记下当时的评级/proposal)与
`fill`(D+2 成熟后补主尺 `gap_c1_o2`;full 报告另补 fwd_5/10/20)。不回注任何
prompt/权重/评级,不产生任何 proposal。

判据围绕四件真实风险写:
  ① 幂等 —— `ingest` 重跑不得复制行,也不得覆盖 `fill` 已经写出的事后列
     (insert-if-absent,不是整行覆盖;覆盖=把已经算好的战绩清空)。
  ② 成熟度门 —— D+2 未落湖是状态不是故障:列必须留空、`matured` 必须是 false,
     不得伪造成 0 或猜一个数。
  ③ 评级契约 —— 缺 `**Rating**:` 行不得兜底猜成 Hold,必须留空 + `contract_ok=false`
     (`parse_rating(strict=True)` 的既定行为,账本不得比它更宽松)。
  ④ 成熟度诚实性(修复轮 1;reviewer Important-1)—— 「永久不可测」(非交易日/非 A 股/
     湖里没这只票)与「暂时未到期」(还没到 D+2)是两件语义完全不同的事,不得都压成
     同一个 `matured=false`;`maturity_status` 必须落在 `MATURITY_STATUSES` 闭集里,
     `fill()` 的 `skipped_by_reason` 必须按原因分桶。

不铺生产夹具:两个 tier 各自最小手搭 `reports_<engine>/analyze/<run_dir>/…`
(full = `analyze.assemble` 真实产出形状;lite = lite-playbook 满卡模板头三行)。
真实历史产物是 schema v1(无 schema_version/engine/rating/proposal 键)——2026-08-31
之前跑的 `reports_claude/analyze/*/manifest.json` 都是这个形状,ingest 必须扛住它。

`_market_returns` 的公式本身有独立的、**不 monkeypatch** 的黄金值测试(修复轮 1;
reviewer Important-2)——样本点 000001 @ 2026-08-20 / 2026-07-28,交易日序列与
open/close 抄自真实 `lake/daily/*.parquet`(2026-08-31 核对,与代码当时的真实输出
逐位比对过),落成本文件内自建的最小夹具 parquet,不依赖开发机 lake 是否存在。
"""
from __future__ import annotations

import csv
import json

import pandas as pd
import pytest

from autoresearch.analyze import ledger
from autoresearch.common import workspace as ws


# ───────────────────────── 夹具:两种 tier 各手搭一份 ─────────────────────────

def _full_report(tmp_path, run_dir="20260830_1738", *, ticker="300308.SZ",
                  name="中际旭创", market="A股", date="2026-08-30",
                  rating="Hold", proposal="HOLD"):
    """镜像 `analyze.assemble` 真实产出形状:`<run_dir>/{manifest.json,<name>.md}`。"""
    out = tmp_path / "analyze" / run_dir
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps({
        "ticker": ticker, "name": name, "market": market, "analysis_date": date,
        "generated_at": f"{date}T17:38:05.549287", "hhmm": "1738",
        "schema_version": 2, "engine": "claude", "run_id": None,
        "context_file": None, "degradations": 0,
        "rating": rating, "proposal": proposal,
    }, ensure_ascii=False), encoding="utf-8")
    (out / f"{name}.md").write_text(
        f"# Trading Analysis Report: {name}（{ticker}）\n\n"
        f"**Rating**: {rating}\n\nFINAL TRANSACTION PROPOSAL: **{proposal}**\n",
        encoding="utf-8")
    return out


def _lite_card(tmp_path, run_dir="20260830_1611", *, code="300476", name="胜宏科技",
               date="2026-08-30", rating="Overweight", proposal="BUY"):
    """镜像 lite-playbook 满卡模板头三行(独立跑落点 `<名称>_lite.md`,无 manifest)。"""
    out = tmp_path / "analyze" / run_dir
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{name}_lite.md").write_text(
        f"# 决策卡 — {code} {name} @ {date}\n\n"
        "## 决策仪表盘\n| 评级 | 现价 |\n|---|---|\n\n"
        f"**Rating**: {rating} ← 必须 = Rubric建议\n\n"
        f"FINAL TRANSACTION PROPOSAL: **{proposal}**\n",
        encoding="utf-8")
    return out


def _rows(tmp_path) -> list[dict]:
    path = tmp_path / "analyze" / "_ledger" / "cards.csv"
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _fake_frame(code: str, *, gap: float, fwd5: float, fwd10: float, fwd20: float) -> pd.DataFrame:
    fr = pd.DataFrame(index=pd.Index([code], name="code"))
    fr["gap_c1_o2"] = [gap]
    fr["fwd_5_oc"] = [fwd5]
    fr["fwd_10_oc"] = [fwd10]
    fr["fwd_20_oc"] = [fwd20]
    return fr


# ───────────────────────── ingest + fill 往返(幂等 · 成熟度) ─────────────────────────

def test_ingest_and_fill_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path)
    _full_report(tmp_path)

    first = ledger.ingest()
    second = ledger.ingest()                      # 幂等:第二次不得再新增
    assert first["added"] == 1
    assert second["added"] == 0
    assert second["scanned"] == first["scanned"]

    rows = _rows(tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["run_dir"] == "20260830_1738"
    assert row["file"] == "manifest.json"
    assert row["tier"] == "full"
    assert row["ticker"] == "300308.SZ"
    assert row["name"] == "中际旭创"
    assert row["market"] == "A股"
    assert row["analysis_date"] == "2026-08-30"
    assert row["engine"] == "claude"
    assert row["rating"] == "Hold"
    assert row["proposal"] == "HOLD"
    assert row["contract_ok"] == "true"
    assert row["maturity_status"] == ""            # fill 还没跑过,不是任何终态
    assert row["matured"] == "false"
    assert row["gap_c1_o2"] == ""
    assert row["ev_pct"] == "" and row["scenario_p_bull"] == ""      # D4.9 才有,先全空

    fake = _fake_frame("300308", gap=0.0152, fwd5=0.031, fwd10=0.052, fwd20=0.081)
    monkeypatch.setattr(ledger, "_market_returns", lambda date, **k: (fake, "MATURED"))

    result = ledger.fill()
    assert result["filled"] == 1
    assert result["skipped"] == 0
    assert result["skipped_by_reason"] == {}
    row = _rows(tmp_path)[0]
    assert row["maturity_status"] == "MATURED"
    assert row["matured"] == "true"
    assert float(row["gap_c1_o2"]) == pytest.approx(0.0152)
    assert float(row["fwd_5"]) == pytest.approx(0.031)
    assert float(row["fwd_10"]) == pytest.approx(0.052)
    assert float(row["fwd_20"]) == pytest.approx(0.081)

    # 已成熟的行第二次 fill 不得重算(增量:成本只与「还没成熟的」成正比)
    monkeypatch.setattr(ledger, "_market_returns",
                        lambda date, **k: (_ for _ in ()).throw(AssertionError("不该再取湖")))
    second_fill = ledger.fill()
    assert second_fill["filled"] == 0

    # 再跑一次 ingest:不得清空刚写好的事后列(insert-if-absent,不是整行覆盖)
    ledger.ingest()
    row = _rows(tmp_path)[0]
    assert row["maturity_status"] == "MATURED"
    assert row["matured"] == "true"
    assert row["gap_c1_o2"] != ""


def test_fill_leaves_pending_rows_untouched_and_labels_them_temporary(tmp_path, monkeypatch):
    """D+2 还没落湖 = 状态不是故障,且是**暂时**态(会自己解决)—— 不伪造数字,
    `maturity_status` 必须是 `PENDING_D2`(不是笼统的 false),`matured` 保持 false。"""
    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path)
    _full_report(tmp_path)
    ledger.ingest()
    monkeypatch.setattr(ledger, "_market_returns", lambda date, **k: (None, "PENDING_D2"))

    result = ledger.fill()
    assert result["filled"] == 0
    assert result["skipped"] == 1
    assert result["skipped_by_reason"] == {"PENDING_D2": 1}
    row = _rows(tmp_path)[0]
    assert row["maturity_status"] == "PENDING_D2"
    assert row["matured"] == "false"
    assert row["gap_c1_o2"] == ""
    assert row["fwd_5"] == ""


def test_fill_skips_lite_tier_forward_horizon_columns(tmp_path, monkeypatch):
    """lite 卡只补 gap_c1_o2(隔夜);fwd_5/10/20 是 full 报告才有意义的长窗,永远留空。"""
    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path)
    _lite_card(tmp_path)
    ledger.ingest()

    fake = _fake_frame("300476", gap=-0.004, fwd5=0.02, fwd10=0.04, fwd20=0.06)
    monkeypatch.setattr(ledger, "_market_returns", lambda date, **k: (fake, "MATURED"))

    filled = ledger.fill()
    assert filled["filled"] == 1
    row = _rows(tmp_path)[0]
    assert row["tier"] == "lite"
    assert row["maturity_status"] == "MATURED"
    assert row["matured"] == "true"
    assert float(row["gap_c1_o2"]) == pytest.approx(-0.004)
    assert row["fwd_5"] == "" and row["fwd_10"] == "" and row["fwd_20"] == ""


# ───────────────────── 成熟度诚实性:永久不可测 vs 暂时未到期(修复轮 1) ─────────────────────

def test_fill_marks_non_ashare_ticker_as_permanently_na_market(tmp_path, monkeypatch):
    """美股票(如 NVDA)不是 6 位 A 股代码 —— 湖只装 A 股行情,这行永远不会成熟。
    这条走的是 `fill()` 自己的 `code` 校验,压根不该碰湖——把 `_market_returns` 换成
    一碰就炸,天然锁住"不该碰湖"这条路径,不只是断言结果碰巧对。"""
    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path)
    _full_report(tmp_path, ticker="NVDA", name="英伟达", market="其他")
    ledger.ingest()
    monkeypatch.setattr(ledger, "_market_returns",
                        lambda date, **k: (_ for _ in ()).throw(AssertionError("不该碰湖")))

    result = ledger.fill()
    assert result["filled"] == 0
    assert result["skipped_by_reason"] == {"NA_MARKET": 1}
    row = _rows(tmp_path)[0]
    assert row["maturity_status"] == "NA_MARKET"
    assert row["matured"] == "false"


def test_fill_marks_non_trading_day_as_permanently_na(tmp_path, monkeypatch):
    """`analysis_date` 恰好是周末(非交易日)—— 湖里永远不会有这天的行情,永久不可测。
    走真实 `_market_returns`(显式传一个空的 `lake_daily` 目录,不 monkeypatch 它本身),
    证明 `fill()` 正确把它返回的 `"NA_NON_TRADING_DAY"` 状态原样透传写进账本。"""
    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path)
    _full_report(tmp_path, date="2026-08-30")                # 真实周日,非交易日
    ledger.ingest()

    empty_lake = tmp_path / "empty_lake"
    empty_lake.mkdir()
    result = ledger.fill(lake_daily=empty_lake)
    assert result["filled"] == 0
    assert result["skipped_by_reason"] == {"NA_NON_TRADING_DAY": 1}
    row = _rows(tmp_path)[0]
    assert row["maturity_status"] == "NA_NON_TRADING_DAY"
    assert row["matured"] == "false"


def test_fill_marks_ticker_missing_from_lake_window_as_permanently_na(tmp_path, monkeypatch):
    """交易日有效、代码形状也对,但整段前瞻窗口湖里都没有这只票的一行——
    停牌全程/代码有误/尚未上市,同样是永久态,不是"再等等"。"""
    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path)
    _full_report(tmp_path, ticker="000005", name="缺数票", date="2026-08-20")
    ledger.ingest()

    lake = tmp_path / "lake_daily"
    lake.mkdir()
    # D=2026-08-20、D+1=08-21、D+2=08-24 三个交易日文件都在场(D+2 结构上够得到),
    # 但只含 000002,不含本行要测的 000005 —— "窗口本身没问题,是这只票没数据"。
    for day in ("20260820", "20260821", "20260824"):
        pd.DataFrame({"ts_code": ["000002.SZ"], "open": [10.0], "close": [10.1]}).to_parquet(
            lake / f"{day}.parquet")

    result = ledger.fill(lake_daily=lake)
    assert result["filled"] == 0
    assert result["skipped_by_reason"] == {"NA_NO_LAKE_ROW": 1}
    row = _rows(tmp_path)[0]
    assert row["maturity_status"] == "NA_NO_LAKE_ROW"
    assert row["matured"] == "false"


def test_fill_buckets_skipped_by_reason_across_mixed_rows(tmp_path, monkeypatch):
    """一次 `fill` 里三种永久态 + 一种暂时态同时出现——`skipped_by_reason` 必须分桶,
    不能只给一个笼统总数。"""
    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path)
    _full_report(tmp_path, run_dir="a_market", ticker="NVDA", name="英伟达",
                 market="其他", date="2026-08-20")
    _full_report(tmp_path, run_dir="b_non_trading", ticker="300308.SZ", name="非交易日票",
                 date="2026-08-30")            # 周日,湖的交易日历里压根没有
    _full_report(tmp_path, run_dir="c_no_row", ticker="000005", name="缺数票",
                 date="2026-08-20")            # 交易日有效、D+2 够得到,但票不在湖里
    _full_report(tmp_path, run_dir="d_pending", ticker="000002", name="待成熟票",
                 date="2026-08-21")            # 交易日有效,但 D+2 结构上还够不到
    ledger.ingest()

    lake = tmp_path / "lake_daily"
    lake.mkdir()
    for day in ("20260820", "20260821", "20260824"):
        pd.DataFrame({"ts_code": ["000002.SZ"], "open": [10.0], "close": [10.1]}).to_parquet(
            lake / f"{day}.parquet")

    result = ledger.fill(lake_daily=lake)
    assert result["filled"] == 0
    assert result["skipped"] == 4
    assert result["skipped_by_reason"] == {
        "NA_MARKET": 1, "NA_NON_TRADING_DAY": 1, "NA_NO_LAKE_ROW": 1, "PENDING_D2": 1,
    }


# ───────────────────── `_market_returns` 真实公式(黄金值,不 monkeypatch) ─────────────────────
#
# 交易日序列 + 000001 的 open/close 抄自真实 `lake/daily/*.parquet`(2026-08-31 核对,
# 与本函数当时的真实输出逐位比对过)——落成本文件自建的最小夹具,不依赖开发机
# lake 是否存在。000002 是干扰票(全部数值 +50),证明透视表按代码正确隔离
# (用 fwd_5_oc 而非 gap_c1_o2 做隔离断言:2026-07-28 那天 000001 恰好 open[D+2]==
# close[D+1],+50 的纯加法偏移会*保持*这个巧合等式,gap_c1_o2 两票都算出 0.0,
# 不是隔离失败,是这一列在这个日期上本来就没有区分度)。

_REAL_BARS = {                          # day -> (open, close),000001,真实抽自 lake
    "20260728": (11.10, 11.20), "20260729": (11.19, 11.28), "20260730": (11.28, 11.61),
    "20260731": (11.50, 11.63), "20260803": (11.54, 11.62), "20260804": (11.58, 11.44),
    "20260805": (11.41, 11.25), "20260806": (11.22, 11.27), "20260807": (11.23, 11.19),
    "20260810": (11.18, 11.29), "20260811": (11.31, 11.26), "20260812": (11.26, 11.25),
    "20260813": (11.23, 11.25), "20260814": (11.22, 11.11), "20260817": (11.20, 11.10),
    "20260818": (11.10, 11.05), "20260819": (11.08, 11.27), "20260820": (11.20, 11.40),
    "20260821": (11.36, 11.41), "20260824": (11.38, 11.56), "20260825": (11.57, 11.59),
    "20260826": (11.55, 11.73), "20260827": (11.70, 11.59), "20260828": (11.54, 11.65),
    "20260831": (11.64, 11.72),
}


def _real_lake_fixture(tmp_path):
    """把 `_REAL_BARS` 落成最小夹具 `<day>.parquet`(ts_code/open/close 两只票:
    000001=真实值,000002=干扰票全部 +50)。"""
    lake = tmp_path / "golden_lake_daily"
    lake.mkdir()
    for day, (o, c) in _REAL_BARS.items():
        pd.DataFrame({
            "ts_code": ["000001.SZ", "000002.SZ"],
            "open": [o, o + 50.0], "close": [c, c + 50.0],
        }).to_parquet(lake / f"{day}.parquet")
    return lake


def test_market_returns_real_formula_fully_matured_2026_07_28(tmp_path):
    """全部四列都在窗口内(D+20 已落湖)—— 黄金值:gap=0.0、fwd5≈0.022341、
    fwd10≈0.006256、fwd20≈0.035746。"""
    lake = _real_lake_fixture(tmp_path)
    fr, status = ledger._market_returns("2026-07-28", lake_daily=lake)
    assert status == "MATURED"
    assert fr is not None
    row = fr.loc["000001"]
    assert float(row["gap_c1_o2"]) == pytest.approx(0.0, abs=1e-9)
    assert float(row["fwd_5_oc"]) == pytest.approx(0.022341, abs=1e-6)
    assert float(row["fwd_10_oc"]) == pytest.approx(0.006256, abs=1e-6)
    assert float(row["fwd_20_oc"]) == pytest.approx(0.035746, abs=1e-6)
    # 干扰票不串号(见上方模块注释:此处用 fwd_5_oc,不用 gap_c1_o2,理由已写明)
    assert float(fr.loc["000002"]["fwd_5_oc"]) != pytest.approx(float(row["fwd_5_oc"]), abs=1e-6)


def test_market_returns_real_formula_partial_window_2026_08_20(tmp_path):
    """D+2/D+5 已落湖但窗口本身还没到 D+10/D+20 —— 该 NaN 就 NaN,不得瞎填一个数。
    黄金值:gap≈-0.002629、fwd5≈0.020246、fwd10/fwd20=NaN。"""
    lake = _real_lake_fixture(tmp_path)
    fr, status = ledger._market_returns("2026-08-20", lake_daily=lake)
    assert status == "MATURED"                 # 主尺够用就算成熟,不受 fwd_10/20 是否到期影响
    assert fr is not None
    row = fr.loc["000001"]
    assert float(row["gap_c1_o2"]) == pytest.approx(-0.002629, abs=1e-6)
    assert float(row["fwd_5_oc"]) == pytest.approx(0.020246, abs=1e-6)
    assert pd.isna(row["fwd_10_oc"])
    assert pd.isna(row["fwd_20_oc"])
    assert float(fr.loc["000002"]["fwd_5_oc"]) != pytest.approx(float(row["fwd_5_oc"]), abs=1e-6)


# ───────────────────────── lite 卡解析(header + 评级契约) ─────────────────────────

def test_ingest_lite_card_parses_header_and_rating(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path)
    _lite_card(tmp_path)

    res = ledger.ingest()
    assert res["added"] == 1
    row = _rows(tmp_path)[0]
    assert row["tier"] == "lite"
    assert row["run_dir"] == "20260830_1611"
    assert row["file"] == "胜宏科技_lite.md"
    assert row["ticker"] == "300476"
    assert row["name"] == "胜宏科技"
    assert row["market"] == "A股"
    assert row["analysis_date"] == "2026-08-30"
    assert row["engine"] == ws.ENGINE
    assert row["rating"] == "Overweight"
    assert row["proposal"] == "BUY"
    assert row["contract_ok"] == "true"


def test_ingest_missing_rating_line_leaves_row_blank_and_flags_contract(tmp_path, monkeypatch):
    """缺 `**Rating**:` 契约行 —— 不得兜底猜成 Hold,行留空 + contract_ok=false。"""
    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path)
    out = tmp_path / "analyze" / "20260830_1900"
    out.mkdir(parents=True)
    (out / "无评级_lite.md").write_text(
        "# 决策卡 — 000001 无评级 @ 2026-08-30\n\n(尚未写出评级行)\n", encoding="utf-8")

    res = ledger.ingest()
    assert res["added"] == 1
    row = _rows(tmp_path)[0]
    assert row["ticker"] == "000001"
    assert row["rating"] == ""
    assert row["proposal"] == ""
    assert row["contract_ok"] == "false"


def test_ingest_handles_legacy_v1_manifest_without_new_fields(tmp_path, monkeypatch):
    """真实历史产物是 v1(2026-08-31 前跑的):没有 schema_version/engine/rating/proposal
    键。不得炸;当作缺契约处理(contract_ok=false),engine 落回当前进程引擎(该目录只可能
    是本引擎自己写的——双引擎隔离下 reports_<engine>/ 从不跨引擎共享)。"""
    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path)
    out = tmp_path / "analyze" / "20260621_2029"
    out.mkdir(parents=True)
    (out / "manifest.json").write_text(json.dumps({
        "ticker": "300476", "name": "胜宏科技", "market": "A股",
        "analysis_date": "2026-06-21", "generated_at": "2026-06-21T20:29:29.568558",
        "hhmm": "2029",
    }, ensure_ascii=False), encoding="utf-8")

    res = ledger.ingest()
    assert res["added"] == 1
    row = _rows(tmp_path)[0]
    assert row["tier"] == "full"
    assert row["ticker"] == "300476"
    assert row["rating"] == ""
    assert row["proposal"] == ""
    assert row["contract_ok"] == "false"
    assert row["engine"] == ws.ENGINE


# ───────────────────────── 目录卫生(不能自己扫自己) ─────────────────────────

def test_ingest_never_scans_the_ledger_directory_itself(tmp_path, monkeypatch):
    """`_ledger/` 是账本自己的落点,不是产物目录 —— iterdir 必须跳过它,否则自己扫自己。"""
    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path)
    _full_report(tmp_path)
    ledger.ingest()

    bogus = tmp_path / "analyze" / "_ledger" / "manifest.json"
    bogus.write_text("{}", encoding="utf-8")
    ledger.ingest()
    assert len(_rows(tmp_path)) == 1


# ───────────────────────── CLI(nightly = ingest + fill 连跑) ─────────────────────────

def test_cli_nightly_runs_ingest_then_fill(tmp_path, monkeypatch, capsys):
    """`python -m autoresearch.analyze.ledger nightly` = ingest + fill 连跑(nightly_close 第5步)。"""
    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path)
    _full_report(tmp_path)
    monkeypatch.setattr(ledger, "_market_returns", lambda date, **k: (None, "PENDING_D2"))

    rc = ledger.main(["nightly"])
    assert rc == 0
    printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert printed["ok"] is True
    assert printed["ingest"]["added"] == 1
    assert printed["fill"]["filled"] == 0
    assert len(_rows(tmp_path)) == 1


def test_cli_ingest_and_fill_are_independently_selectable(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path)
    _full_report(tmp_path)

    rc = ledger.main(["ingest"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "fill" not in out and out["ingest"]["added"] == 1

    monkeypatch.setattr(ledger, "_market_returns", lambda date, **k: (None, "PENDING_D2"))
    rc = ledger.main(["fill"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "ingest" not in out and out["fill"]["filled"] == 0
