"""10 日尺预注册普查(2026-09-26 daily-engine §5 B2,批 5 Task 2)。

判据围绕 Review Focus 写,不围绕实现写:

① **窗口重叠** —— 相邻分析日的 fwd_10 窗口重叠 9 个 session,显著性只许用登记的校准检验
   (HAC t + 同一 n 上 MA(9) 重叠零假设的模拟临界值,`research/swing_ruler_decision`);逐日
   独立的区间会把 10 天的平台当 10 个独立样本,窄到假;区间与 p 值必须同源(复审 I1);
② **未成熟行** —— PENDING_10 的行从分母剔除并**计数**,不当 0、不静默参与均值;
③ **📌 污染** —— 保送票(账本 lane/role 或冻结 staging 的 pinned_note)进任何一格都是错;
④ **样本门** —— n_days < 40 一律 INSUFFICIENT,哪怕区间好看;
⑤ **预注册** —— 输出目录已存在即拒,代码出处漂移即拒,被拒的跑法一个字节都不落盘。

夹具全部是 `tmp_path` 里的真 parquet 湖 + 真 CSV 账本;只有 `git` 出处核验被打桩
(它读的是仓库状态,不是本模块的逻辑)。
"""
from __future__ import annotations

import csv
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from autoresearch.common import workspace as ws
from autoresearch.research import swing_ruler_census as sc
from autoresearch.scan import outcome

#: 2026-09-26 复审 I1:判读检验重新登记为 v2(旧 id 拒跑,见 `test_superseded_v1_spec_is_refused`)。
SPEC_V1 = Path(__file__).resolve().parents[2] / "docs" / "research" / "2026-09-26-swing-ruler-family.spec.json"
SPEC = SPEC_V1.with_name("2026-09-26-swing-ruler-family-v2.spec.json")
FLAT_CODES = tuple(f"{i:06d}" for i in range(11, 32))      # 21 只平推票 → 市场中位恒 0
LEDGER_CODES = ("000001", "000002", "000003", "000004", "000005")


@pytest.fixture(autouse=True)
def _no_git_provenance(monkeypatch):
    """出处核验读的是真仓库的 git 状态 —— 与本文件要验的逻辑无关,打桩成「已核验」。"""
    monkeypatch.setattr(sc, "verify_code_provenance",
                        lambda spec, roots, **kw: {"declared": spec["code_sha"],
                                                   "observed": "f" * 40,
                                                   "behavior_roots": sorted(roots)})


def _weekdays(start: str, n: int) -> list[str]:
    out, cur = [], date.fromisoformat(start)
    while len(out) < n:
        if cur.weekday() < 5:
            out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def _c(day: str) -> str:
    return day.replace("-", "")


def _spec(tmp_path: Path, experiment_id: str = "FAM_SWING_TEST") -> Path:
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    spec["experiment_id"] = experiment_id
    spec["engine"] = ws.ENGINE
    path = tmp_path / f"{experiment_id}.spec.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return path


def _lake(tmp_path: Path, sessions: list[str]) -> Path:
    """全湖平推(open=high=low=close=10,pct_chg=0):fwd_10_oc 恒 0、D+1 全部可买。"""
    root = tmp_path / "lake" / "daily"
    root.mkdir(parents=True, exist_ok=True)
    for day in sessions:
        pd.DataFrame([{"ts_code": f"{code}.SZ", "open": 10.0, "high": 10.0, "low": 10.0,
                       "close": 10.0, "pct_chg": 0.0, "amount": 1.0e5}
                      for code in (*FLAT_CODES, *LEDGER_CODES)]
                     ).to_parquet(root / f"{_c(day)}.parquet", index=False)
    return root


def _row(day: str, t1: str, t10: str, code: str, **kw) -> dict:
    base = {"run_id": f"{_c(day)}_2100", "analysis_date": day, "mode": "active", "src": "run",
            "outcome_status": outcome.MATURE, "calendar_quality": outcome.TRADE_CAL_QUALITY,
            "code": code, "role": "finalist", "lane": "trend", "rating": "Hold",
            "e6_buy": "False", "t1": _c(t1), "t10": _c(t10),
            "outcome_status_swing": outcome.MATURE_10}
    base.update(kw)
    return base


def _write_ledger(scan_root: Path, rows: list[dict]) -> Path:
    path = outcome.ledger_root(scan_root) / outcome.LEDGER_CSV
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(outcome.LEDGER_COLUMNS), extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in outcome.LEDGER_COLUMNS})
    return path


def _world(tmp_path: Path, *, n_days: int = 45, n_pending: int = 10,
           h1_values=None, h3: float = -0.02) -> tuple[Path, Path, Path]:
    """`n_days` 个成熟分析日:H1 票(Hold)、H3 票(UW)、📌 票(Hold,+30%,不许进格)、
    隔天一只 E6 BUY(Hold,与 H1 同值);再加 `n_pending` 个 PENDING_10 的日子。"""
    sessions = _weekdays("2026-06-18", n_days + n_pending + 12)
    lake = _lake(tmp_path, sessions[: n_days + 11])
    rows = []
    for i in range(n_days):
        day, t1, t10 = sessions[i], sessions[i + 1], sessions[i + 10]
        h1 = 0.01 if h1_values is None else h1_values[i]
        rows.append(_row(day, t1, t10, "000001", fwd_10_oc=h1))
        rows.append(_row(day, t1, t10, "000002", rating="Underweight", lane="reversion",
                         fwd_10_oc=h3))
        rows.append(_row(day, t1, t10, "000003", role="pinned", lane="pinned", fwd_10_oc=0.30))
        if i % 2 == 0:
            rows.append(_row(day, t1, t10, "000004", role="BUY", e6_buy="True",
                             gap_c1_o2=0.004, fwd_10_oc=h1))
    for j in range(n_pending):
        day = sessions[n_days + j]
        rows.append(_row(day, sessions[n_days + j + 1], sessions[n_days + j + 10], "000001",
                         outcome_status_swing=outcome.PENDING_10, fwd_10_oc=""))
    scan_root = tmp_path / "rpt" / "scan"
    _write_ledger(scan_root, rows)
    return lake, scan_root, tmp_path / "rpt"


def _cells(out_dir: Path) -> dict[str, dict]:
    with (out_dir / "cells.csv").open(encoding="utf-8") as fh:
        return {r["hypothesis_id"]: r for r in csv.DictReader(fh)}


def _run(tmp_path, lake, scan_root, reports_root, **kw) -> Path:
    return sc.run_census(spec_path=_spec(tmp_path), scan_root=scan_root,
                         reports_root=reports_root, lake_daily=lake, **kw)


# ───────────────────────── 主路径:判读 + 未成熟计数 ─────────────────────────

def test_census_reads_only_mature_rows_counts_pending_and_judges_h1_h3(tmp_path):
    lake, scan_root, rpt = _world(tmp_path)
    out = _run(tmp_path, lake, scan_root, rpt)

    assert out == rpt / "research" / "swing_ruler" / "FAM_SWING_TEST"
    cells = _cells(out)
    for col in ("n_days", "mean_pp", "ci_lo", "verdict"):
        assert col in next(iter(cells.values()))
    h1, h3 = cells["swing_h1_hold_plus_finalists"], cells["swing_h3_rejection_negative"]
    assert int(h1["n_days"]) == 45 and float(h1["mean_pp"]) == pytest.approx(1.0)
    assert h1["verdict"] == "POSITIVE" and h1["supports"] == "True"
    assert int(h3["n_days"]) == 45 and float(h3["mean_pp"]) == pytest.approx(-2.0)
    assert h3["verdict"] == "NEGATIVE" and h3["supports"] == "True"

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["counts"]["swing_status"]["PENDING_10"] == 10       # 剔除但计数
    assert manifest["counts"]["swing_status"]["MATURE_10"] > 0
    assert manifest["n_days"] == 45
    assert manifest["experiment_id"] == "FAM_SWING_TEST"
    assert {"path", "sha256"} <= set(manifest["inputs"][0])
    assert manifest["code_sha"] == "f" * 40
    assert (out / "readout.md").is_file() and (out / "spec.json").is_file()


def test_h4_is_descriptive_sign_agreement_not_a_verdict(tmp_path):
    lake, scan_root, rpt = _world(tmp_path)
    h4 = _cells(_run(tmp_path, lake, scan_root, rpt))["swing_h4_e6_r_tier_sign"]
    assert h4["verdict"] == "DESCRIPTIVE"
    assert int(h4["n_rows"]) == 23 and float(h4["sign_agree"]) == pytest.approx(1.0)
    assert h4["q_by"] == ""                                     # 不入 BY 族


def test_below_forty_days_every_directional_cell_is_insufficient(tmp_path):
    """样本门先判:30 天哪怕每天都 +1pp、区间漂亮,也只报 INSUFFICIENT。"""
    lake, scan_root, rpt = _world(tmp_path, n_days=30, n_pending=0)
    cells = _cells(_run(tmp_path, lake, scan_root, rpt))
    assert cells["swing_h1_hold_plus_finalists"]["verdict"] == "INSUFFICIENT"
    assert cells["swing_h3_rejection_negative"]["verdict"] == "INSUFFICIENT"
    assert cells["swing_h2_lowturn_lane"]["verdict"] == "INSUFFICIENT"      # 0 天也是不足


# ───────────────────────── ① 窗口重叠:校准检验 ─────────────────────────

def test_significance_uses_the_overlap_calibrated_test_not_independent_days(tmp_path):
    """10 天一个平台(+1.5pp / −1.0pp 交替):逐日独立的区间 [0.15, 0.85] 排除 0,
    登记的校准检验(HAC t + MA(9) 零假设临界值)区间跨 0 → 必须是 UNPROVEN。
    改成按块长 1 判读,这条测试必红。"""
    from autoresearch.research import swing_ruler_decision as dec

    values = [0.015 if (i // 10) % 2 == 0 else -0.010 for i in range(50)]
    lake, scan_root, rpt = _world(tmp_path, n_days=50, n_pending=0, h1_values=values)
    h1 = _cells(_run(tmp_path, lake, scan_root, rpt))["swing_h1_hold_plus_finalists"]
    assert float(h1["mean_pp"]) == pytest.approx(0.5)
    assert float(h1["ci_lo_b1"]) > 0                     # 逐日独立会误判「显著」
    assert float(h1["ci_lo"]) < 0 < float(h1["ci_hi"])   # 校准区间:跨 0
    assert h1["test"] == dec.DECISION_TEST
    assert float(h1["crit"]) == pytest.approx(dec.critical_value(50))
    assert h1["verdict"] == "UNPROVEN"


def test_decision_interval_and_p_value_come_from_the_same_method(tmp_path):
    """复审 I1:旧基线 H1 区间整段在 0 以下而 p = 0.051(区间与 p 值两套方法)。
    现在 cells.csv 里 CI 不含 0 ⇔ p ≤ 0.05,逐格成立;块 bootstrap 区间只作敏感性。"""
    rng = np.random.default_rng(3)
    values = list(0.004 + 0.01 * rng.standard_normal(50))
    lake, scan_root, rpt = _world(tmp_path, n_days=50, n_pending=0, h1_values=values)
    cells = _cells(_run(tmp_path, lake, scan_root, rpt))
    for h in ("swing_h1_hold_plus_finalists", "swing_h3_rejection_negative"):
        c = cells[h]
        excludes = float(c["ci_lo"]) > 0 or float(c["ci_hi"]) < 0
        assert excludes == (float(c["p_value"]) <= 0.05), h
        assert {"ci_lo_b10", "ci_hi_b10"} <= set(c)


def test_superseded_v1_spec_is_refused_before_anything_is_written(tmp_path):
    """复审 I1:FAM_SWING_RULER_20260926 的判读检验没校准,已被 v2 取代 —— 拿旧 spec 跑必拒,
    一个字节都不落盘(旧 id 从未在生产上读过,不许补一次)。"""
    lake, scan_root, rpt = _world(tmp_path, n_days=41, n_pending=0)
    v1 = json.loads(SPEC_V1.read_text(encoding="utf-8"))
    v1["engine"] = ws.ENGINE
    path = tmp_path / "v1.spec.json"
    path.write_text(json.dumps(v1, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="superseded"):
        sc.run_census(spec_path=path, scan_root=scan_root, reports_root=rpt, lake_daily=lake)
    with pytest.raises(ValueError, match="superseded"):
        sc.census_sizes(spec_path=path, scan_root=scan_root, reports_root=rpt, lake_daily=lake)
    assert not (rpt / "research").exists()


def test_provenance_roots_pin_the_decision_test_but_not_the_ledger_writer():
    """复审 M5:普查读账本**数据**,`scan/outcome.py` 不重算已写的 fwd_10_oc —— 钉它不保护
    任何东西,只会让无关的 outcome 修补逼出新 id。判读检验模块必须钉。"""
    assert "autoresearch/scan/outcome.py" not in sc.BEHAVIOR_ROOTS
    assert "autoresearch/research/swing_ruler_decision.py" in sc.BEHAVIOR_ROOTS
    assert "autoresearch/common/stats.py" in sc.BEHAVIOR_ROOTS


# ───────────────────────── ③ 📌 污染 ─────────────────────────

def test_pinned_rows_never_enter_any_cell(tmp_path):
    """账本 lane=pinned 的票(+30pp)进 H1 会把均值拉到 +15pp;剔干净恰好 +1pp。"""
    lake, scan_root, rpt = _world(tmp_path)
    out = _run(tmp_path, lake, scan_root, rpt)
    assert float(_cells(out)["swing_h1_hold_plus_finalists"]["mean_pp"]) == pytest.approx(1.0)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["counts"]["pinned_excluded"] == 45


def test_pinned_note_in_the_frozen_staging_is_also_excluded(tmp_path):
    """第二代 📌 标记(finalists.csv 的 pinned_note)只在 run 的冻结 staging 里 ——
    账本行本身看着像普通 finalist。`edge_census.pinned_codes` 认它,本普查也得认。"""
    lake, scan_root, rpt = _world(tmp_path, n_days=41, n_pending=0)
    first = scan_root / "20260618_2100" / "trace" / "staging"
    first.mkdir(parents=True)
    (first / "finalists.csv").write_text("code,lane,pinned_note\n000001,trend,持仓\n",
                                         encoding="utf-8")
    out = _run(tmp_path, lake, scan_root, rpt)
    h1 = _cells(out)["swing_h1_hold_plus_finalists"]
    assert int(h1["n_days"]) == 41                     # 那天还有 000004(BUY)撑着
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["counts"]["pinned_excluded"] == 41 + 1


# ───────────────────────── ⑤ 预注册纪律 ─────────────────────────

def test_existing_output_directory_is_refused(tmp_path):
    lake, scan_root, rpt = _world(tmp_path, n_days=41, n_pending=0)
    _run(tmp_path, lake, scan_root, rpt)
    with pytest.raises(FileExistsError):
        _run(tmp_path, lake, scan_root, rpt)


def test_code_provenance_drift_is_refused_before_anything_is_written(tmp_path, monkeypatch):
    lake, scan_root, rpt = _world(tmp_path, n_days=41, n_pending=0)

    def drift(spec, roots, **kw):
        raise ValueError("code provenance behavior drift: ['autoresearch/common/stats.py']")

    monkeypatch.setattr(sc, "verify_code_provenance", drift)
    with pytest.raises(ValueError, match="drift"):
        _run(tmp_path, lake, scan_root, rpt)
    assert not (rpt / "research").exists()


def test_since_that_narrows_the_registered_window_is_exploratory_only(tmp_path):
    """`--since` 收窄预注册窗口 = 探索性读数:照算,但不许出 POSITIVE/NEGATIVE。"""
    lake, scan_root, rpt = _world(tmp_path)
    out = _run(tmp_path, lake, scan_root, rpt, since="2026-06-25")
    assert {c["verdict"] for c in _cells(out).values()} == {"EXPLORATORY"}
    assert json.loads((out / "manifest.json").read_text(encoding="utf-8"))["registered_window"] is False


def test_one_run_per_analysis_day_keeps_the_last_published_run(tmp_path):
    """同一分析日多个 run:只取 run_id 字典序最大者(= populations.select_run),
    否则同一只票同一天被数两遍。"""
    lake, scan_root, rpt = _world(tmp_path, n_days=41, n_pending=0)
    path = outcome.ledger_root(scan_root) / outcome.LEDGER_CSV
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    extra = dict(rows[0], run_id="20260618_0900", fwd_10_oc="0.50")   # 更早的一次,+50pp
    _write_ledger(scan_root, [*rows, extra])
    h1 = _cells(_run(tmp_path, lake, scan_root, rpt))["swing_h1_hold_plus_finalists"]
    assert float(h1["mean_pp"]) == pytest.approx(1.0)


def test_a_day_whose_rows_lack_a_run_id_does_not_crash_day_selection(tmp_path):
    """坏行(手改账本 / 旧工具写出的 run_id 空单元格)不许让整次普查 KeyError 崩掉:
    那一天照样选得出(空串当作它自己的 run),其余天的读数不受影响。"""
    lake, scan_root, rpt = _world(tmp_path, n_days=41, n_pending=0)
    path = outcome.ledger_root(scan_root) / outcome.LEDGER_CSV
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    orphan = dict(rows[0], run_id="", analysis_date="2026-08-20", code="000009")
    _write_ledger(scan_root, [*rows, orphan])
    h1 = _cells(_run(tmp_path, lake, scan_root, rpt))["swing_h1_hold_plus_finalists"]
    assert int(h1["n_days"]) == 41 and float(h1["mean_pp"]) == pytest.approx(1.0)


def test_every_file_the_census_writes_is_a_registered_artifact(tmp_path):
    """新产物先进 `contracts.artifacts` 再写代码:落盘的每个文件都要对得上登记的
    `swing_ruler/*/<name>`(research_report 根),多写一个没登记的文件这条就红。"""
    from fnmatch import fnmatch

    from autoresearch.contracts import artifacts as ca

    lake, scan_root, rpt = _world(tmp_path, n_days=41, n_pending=0)
    out = _run(tmp_path, lake, scan_root, rpt)
    registered = [a.path for a in ca.for_root("research_report")
                  if a.path.startswith("swing_ruler/")]
    assert registered
    for written in sorted(p for p in out.iterdir() if p.is_file()):
        rel = f"swing_ruler/{out.name}/{written.name}"
        assert any(fnmatch(rel, pattern) for pattern in registered), rel


def test_sizes_probe_reports_sample_sizes_only_and_consumes_nothing(tmp_path, capsys):
    """stop_rule 说 B4 只用「第一次 H1 n_days ≥ 40」的读数,而读数目录一次性(已存在即拒)。
    `--sizes-only` 只报每格 n_days/n_rows 与是否到门,不报任何收益、不建目录。"""
    lake, scan_root, rpt = _world(tmp_path)
    argv = ["--spec", str(_spec(tmp_path)), "--scan-root", str(scan_root),
            "--reports-root", str(rpt), "--lake", str(lake), "--sizes-only"]
    assert sc.main(argv) == 0
    got = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert got["sizes"]["swing_h1_hold_plus_finalists"] == {"n_days": 45, "n_rows": 68}
    assert got["h1_ready"] is True and got["min_days"] == 40
    assert "mean_pp" not in json.dumps(got) and "excess" not in json.dumps(got)
    assert not (rpt / "research").exists()


def test_cli_refuses_an_existing_directory_with_exit_1(tmp_path, capsys):
    lake, scan_root, rpt = _world(tmp_path, n_days=41, n_pending=0)
    argv = ["--spec", str(_spec(tmp_path)), "--scan-root", str(scan_root),
            "--reports-root", str(rpt), "--lake", str(lake)]
    assert sc.main(argv) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["ok"] is True
    assert sc.main(argv) == 1
