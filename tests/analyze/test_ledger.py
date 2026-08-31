"""stock-research 结果账本(D5.1)—— 独立产物(full 报告 / lite 决策卡)事后读数。

只记不学:本模块只做两件事——`ingest`(发现新产物,记下当时的评级/proposal)与
`fill`(D+2 成熟后补主尺 `gap_c1_o2`;full 报告另补 fwd_5/10/20)。不回注任何
prompt/权重/评级,不产生任何 proposal。

判据围绕三件真实风险写:
  ① 幂等 —— `ingest` 重跑不得复制行,也不得覆盖 `fill` 已经写出的事后列
     (insert-if-absent,不是整行覆盖;覆盖=把已经算好的战绩清空)。
  ② 成熟度门 —— D+2 未落湖是状态不是故障:列必须留空、`matured` 必须是 false,
     不得伪造成 0 或猜一个数。
  ③ 评级契约 —— 缺 `**Rating**:` 行不得兜底猜成 Hold,必须留空 + `contract_ok=false`
     (`parse_rating(strict=True)` 的既定行为,账本不得比它更宽松)。

不铺生产夹具:两个 tier 各自最小手搭 `reports_<engine>/analyze/<run_dir>/…`
(full = `analyze.assemble` 真实产出形状;lite = lite-playbook 满卡模板头三行)。
真实历史产物是 schema v1(无 schema_version/engine/rating/proposal 键)——2026-08-31
之前跑的 `reports_claude/analyze/*/manifest.json` 都是这个形状,ingest 必须扛住它。
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
                  name="中际旭创", date="2026-08-30", rating="Hold", proposal="HOLD"):
    """镜像 `analyze.assemble` 真实产出形状:`<run_dir>/{manifest.json,<name>.md}`。"""
    out = tmp_path / "analyze" / run_dir
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps({
        "ticker": ticker, "name": name, "market": "A股", "analysis_date": date,
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
    assert row["matured"] == "false"
    assert row["gap_c1_o2"] == ""
    assert row["ev_pct"] == "" and row["scenario_p_bull"] == ""      # D4.9 才有,先全空

    fake = _fake_frame("300308", gap=0.0152, fwd5=0.031, fwd10=0.052, fwd20=0.081)
    monkeypatch.setattr(ledger, "_market_returns", lambda date, **k: fake)

    result = ledger.fill()
    assert result["filled"] == 1
    row = _rows(tmp_path)[0]
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
    assert row["matured"] == "true"
    assert row["gap_c1_o2"] != ""


def test_fill_leaves_immature_rows_untouched(tmp_path, monkeypatch):
    """D+2 还没落湖 = 状态不是故障:不伪造数字,`matured` 保持 false、列留空。"""
    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path)
    _full_report(tmp_path)
    ledger.ingest()
    monkeypatch.setattr(ledger, "_market_returns", lambda date, **k: None)

    result = ledger.fill()
    assert result["filled"] == 0
    assert result["skipped"] == 1
    row = _rows(tmp_path)[0]
    assert row["matured"] == "false"
    assert row["gap_c1_o2"] == ""
    assert row["fwd_5"] == ""


def test_fill_skips_lite_tier_forward_horizon_columns(tmp_path, monkeypatch):
    """lite 卡只补 gap_c1_o2(隔夜);fwd_5/10/20 是 full 报告才有意义的长窗,永远留空。"""
    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path)
    _lite_card(tmp_path)
    ledger.ingest()

    fake = _fake_frame("300476", gap=-0.004, fwd5=0.02, fwd10=0.04, fwd20=0.06)
    monkeypatch.setattr(ledger, "_market_returns", lambda date, **k: fake)

    filled = ledger.fill()
    assert filled["filled"] == 1
    row = _rows(tmp_path)[0]
    assert row["tier"] == "lite"
    assert row["matured"] == "true"
    assert float(row["gap_c1_o2"]) == pytest.approx(-0.004)
    assert row["fwd_5"] == "" and row["fwd_10"] == "" and row["fwd_20"] == ""


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
    monkeypatch.setattr(ledger, "_market_returns", lambda date, **k: None)

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

    monkeypatch.setattr(ledger, "_market_returns", lambda date, **k: None)
    rc = ledger.main(["fill"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "ingest" not in out and out["fill"]["filled"] == 0
