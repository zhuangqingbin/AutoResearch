"""run_health / churn / L4 阶段效能 / 买单计数 / index.md + assemble 接线。合成,无网络。

spec: docs/specs/2026-07-02-scan-observability-design.md
"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.scan.health import (
    count_buys,
    finalist_churn,
    index_md,
    l4_phase_stats,
    run_health,
    write_run_health,
)

CARD_OW = "# 决策卡\n**Rating**: Overweight\n进入P4倾向: Buy\n**一行多空**:多:强 ｜ 空:贵\n"
CARD_STOP = "# 早停卡\n**Rating**: Hold\n早停因: 主力流出 → 停\n"
CARD_REUSE = "♻️ 复用@2026-06-30 卡\n**Rating**: Hold\n"


def _mk_day(root, date, codes=("000001",), cards=None, l1_rows=None, meta=None):
    d = root / date
    (d / "details").mkdir(parents=True)
    pd.DataFrame([{"code": c, "name": f"N{c}", "sector": "半导体"} for c in codes]).to_csv(
        d / "finalists.csv", index=False)
    for code, text in (cards or {}).items():
        (d / "details" / f"{code}.md").write_text(text, encoding="utf-8")
    if l1_rows is not None:
        pd.DataFrame(l1_rows).to_csv(d / "L1_recall_top1000.csv", index=False)
    if meta is not None:
        (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return d


def test_run_health_core(tmp_path):
    l1 = [{"code": f"{i:06d}", "composite": 50.0, "pe": (None if i % 2 else 30.0),
           "main_net_ratio": 0.01} for i in range(10)]                    # pe 50% NaN → 降级
    d = _mk_day(tmp_path, "2026-07-02", cards={"000001": CARD_OW}, l1_rows=l1,
                meta={"regime": "risk_off", "l2_engine": "stratified(sn_composite)"})
    h = run_health(d)
    assert h["date"] == "2026-07-02" and h["regime"] == "risk_off"
    assert "pe" in h["degraded_fields"] and h["nan_rates"]["pe"] == 0.5
    assert h["artifacts"]["finalists.csv"] and "L2_gbdt_top200.csv" in h["missing"]
    assert "L2_gbdt_top200.csv" in h["core_missing"] and "market_view.md" not in h["core_missing"]
    assert h["counts"]["cards"] == 1 and h["counts"]["buys"] == 1
    p = write_run_health(d)
    assert json.loads(p.read_text(encoding="utf-8"))["counts"]["buys"] == 1


def test_finalist_churn(tmp_path):
    _mk_day(tmp_path, "2026-07-01", codes=("000001", "000002"))
    d = _mk_day(tmp_path, "2026-07-02", codes=("000002", "000003"))
    ch = finalist_churn(d)
    assert ch["prev_date"] == "2026-07-01" and ch["n_repeat"] == 1 and ch["repeat_rate"] == 0.5
    assert finalist_churn(tmp_path / "2026-07-01") is None      # 无更早日


def test_l4_phase_stats_and_p4_flip(tmp_path):
    d = _mk_day(tmp_path, "2026-07-02",
                cards={"000001": CARD_OW, "000002": CARD_STOP, "000003": CARD_REUSE})
    ph = l4_phase_stats(d)
    assert ph["n_cards"] == 3 and ph["n_earlystop"] == 1 and ph["n_reused"] == 1
    assert ph["n_full"] == 1 and ph["p4_seen"] == 1 and ph["p4_flips"] == 1   # 进P4倾向Buy→终OW


def test_count_buys_verify_downgrade(tmp_path):
    d = _mk_day(tmp_path, "2026-07-02", cards={"000001": CARD_OW})
    assert count_buys(d) == 1
    pd.DataFrame([{"code": "000001", "verdict": "降级", "bull": "", "bear": "b",
                   "trigger": "", "consensus": "2/3"}]).to_csv(d / "verify.csv", index=False)
    assert count_buys(d) == 0                                    # OW 降级→Hold,踢出买单


def test_index_md_links_and_prev_run(tmp_path):
    d = _mk_day(tmp_path / "ctx", "2026-07-02", cards={"000001": CARD_OW})
    rep = tmp_path / "reports"
    (rep / "20260701_0900").mkdir(parents=True)
    (rep / "20260701_0900" / "summary.md").write_text("x", encoding="utf-8")
    rd = rep / "20260702_1200"
    (rd / "details").mkdir(parents=True)
    (rd / "details" / "N甲.md").write_text("x", encoding="utf-8")
    (rd / "trace").mkdir()
    (rd / "trace" / "funnel.md").write_text("x", encoding="utf-8")
    s = index_md(d, rd)
    assert "summary.md" in s and "N甲" in s and "funnel.md" in s
    assert "20260701_0900" in s and "健康一行" in s


def test_retro_health_section(tmp_path):
    """retro 的运行健康节:降级字段/核心缺产物才出声;无恙/缺文件 → []。"""
    from autoresearch.learning.retro import _health_section
    assert _health_section(tmp_path) == []
    (tmp_path / "run_health.json").write_text(json.dumps(
        {"degraded_fields": ["pe"], "core_missing": ["L2_gbdt_top200.csv"]}), encoding="utf-8")
    s = "\n".join(_health_section(tmp_path))
    assert "运行健康" in s and "pe" in s and "数据病" in s and "L2_gbdt_top200.csv" in s
    (tmp_path / "run_health.json").write_text(json.dumps(
        {"degraded_fields": [], "core_missing": []}), encoding="utf-8")
    assert _health_section(tmp_path) == []


def test_assemble_writes_health_and_index(tmp_path):
    """assemble.run 落 run_health.json(staging+trace)+ index.md(报告目录)。"""
    from autoresearch.scan.assemble import run as assemble_run
    d = tmp_path / "s"
    d.mkdir()
    (d / "meta.json").write_text("{}", encoding="utf-8")
    (d / "finalists.csv").write_text("code,name,sector\n", encoding="utf-8")
    out = assemble_run("2026-07-02", scan_dir=d, out_root=tmp_path / "rep",
                       hhmm="1200", run_date="2026-07-02")
    base = out.parent
    assert (d / "run_health.json").exists()
    assert (base / "trace" / "run_health.json").exists()
    assert (base / "index.md").exists() and "扫描现场索引" in (base / "index.md").read_text(encoding="utf-8")
