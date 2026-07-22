"""L4 派发计划(dispatch_plan):按 `_l4_prompt_<code>.md` / `details/<code>.md` 是否存在,
把 finalists 分 dispatch(需新派 Opus)与 reused(TTL 复用卡已就位,解析评级并回,
不再派 subagent)。复审 task-4-review.md Important #1 修复 —— workflow 此前对全部
finalists 无条件派卡,复用码的 prompt 文件从未写过(write_dispatch_pack 早已 skip),
等于空派 Opus 且丢了复用卡评级。合成,无网络。
"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.scan.agents.l4_card import dispatch_plan

_DATE = "2026-07-07"


def _mk(root):
    d = root / "context" / "scan" / _DATE
    (d / "details").mkdir(parents=True)
    pd.DataFrame([
        {"code": "600584", "name": "长电科技"},
        {"code": "000062", "name": "深圳华强"},
        {"code": "000063", "name": "中兴通讯"},
    ]).to_csv(d / "finalists.csv", index=False)
    (d / "_l4_prompt_600584.md").write_text(
        "# L4 派发 prompt — 600584 长电科技\n", encoding="utf-8")
    (d / "details" / "000062.md").write_text(
        "♻️ **复用卡**(源 2026-07-03)\n**Rating**: Hold\n", encoding="utf-8")
    # 000063: _l4_prompt 与 details 都没有 → 异常兜底,仍归 dispatch
    return d


def test_dispatch_plan(tmp_path):
    _mk(tmp_path)
    res = dispatch_plan(_DATE, root=tmp_path / "context" / "scan")
    assert set(res["dispatch"]) == {"600584", "000063"}
    assert res["reused"] == [{"code": "000062", "rating": "Hold"}]


def test_dispatch_plan_no_finalists(tmp_path):
    d = tmp_path / "context" / "scan" / _DATE
    d.mkdir(parents=True)
    res = dispatch_plan(_DATE, root=d.parent)
    assert res == {"dispatch": [], "reused": [], "meta": {}}


def test_dispatch_plan_cli(tmp_path, monkeypatch, capsys):
    _mk(tmp_path)
    monkeypatch.chdir(tmp_path)
    from autoresearch.scan.agents.l4_card import main
    assert main(["dispatch-plan", _DATE]) == 0
    out = json.loads(capsys.readouterr().out)
    assert set(out["dispatch"]) == {"600584", "000063"}
    assert out["reused"] == [{"code": "000062", "rating": "Hold"}]


# ══════════════════════════ meta(name/sector,仅 dispatch 码;L4 情报站 plan Task 2) ══════════════════════════


def _mk_with_sector(root):
    """mirror `_mk` 但 finalists 多一列 `sector`,验 `meta` 落 name/sector(仅 dispatch 码)。"""
    d = root / "context" / "scan" / _DATE
    (d / "details").mkdir(parents=True)
    pd.DataFrame([
        {"code": "600584", "name": "长电科技", "sector": "半导体"},
        {"code": "000062", "name": "深圳华强", "sector": "汽车"},
        {"code": "000063", "name": "中兴通讯", "sector": "通信"},
    ]).to_csv(d / "finalists.csv", index=False)
    (d / "_l4_prompt_600584.md").write_text(
        "# L4 派发 prompt — 600584 长电科技\n", encoding="utf-8")
    (d / "details" / "000062.md").write_text(
        "♻️ **复用卡**(源 2026-07-03)\n**Rating**: Hold\n", encoding="utf-8")
    return d


def test_dispatch_plan_meta_names(tmp_path):
    _mk_with_sector(tmp_path)
    plan = dispatch_plan(_DATE, root=tmp_path / "context" / "scan")
    code = plan["dispatch"][0]
    assert plan["meta"][code]["name"] and "sector" in plan["meta"][code]
    assert all(c not in plan["meta"] for c in [r["code"] for r in plan["reused"]])


def test_dispatch_plan_meta_nan_cells(tmp_path):
    """终审 I-1:空单元格(NaN)不得以字面 "nan" 注入盲搜 prompt 的 meta。"""
    sd = tmp_path / "2026-07-09"
    sd.mkdir()
    (sd / "finalists.csv").write_text("code,name,sector\n600584,,\n", encoding="utf-8")
    (sd / "_l4_prompt_600584.md").write_text("x", encoding="utf-8")
    plan = dispatch_plan("2026-07-09", root=tmp_path)
    assert plan["meta"]["600584"] == {"name": "", "sector": "", "pinned": False}
