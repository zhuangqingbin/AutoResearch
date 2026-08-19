"""L4 派发计划(dispatch_plan):全部 finalists 无条件进 dispatch。

Wave9 R5(2026-07-29 用户裁定「不要任何复用」):TTL 复用整体退役 —— 不再按
`_l4_prompt_<code>.md` / `details/<code>.md` 是否存在把 finalists 分
dispatch/reused 两路;返回不再有 `reused` 键。复审 task-4-review.md Important #1
的历史修复(workflow 此前对全部 finalists 无条件派卡、复用码的 prompt 文件从未写过)
已随复用退役一并作废——现在"全部 finalists 无条件派卡"本身就是正确行为。合成,无网络。
"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.common import workspace as ws
from autoresearch.scan.agents.l4_card import dispatch_plan

_DATE = "2026-07-07"


def _mk(root):
    d = root / ws.scan_root() / _DATE
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
    # 000063: _l4_prompt 与 details 都没有 → R5 后一律进 dispatch(无需异常兜底分支)
    return d


def test_dispatch_plan_has_no_reuse_key(tmp_path):
    """Wave9 R5:TTL 复用整体退役,每只 finalist 每日全量重研 —— 无论
    `_l4_prompt_<code>.md` / `details/<code>.md` 是否已存在(曾经的可复用信号),
    dispatch_plan 都不再读取它们做分流,全部 finalist 进 dispatch,返回不含
    `reused` 键。000062 是曾经会被判"可复用"的票(有 details 无 prompt),验证它
    现在也照样进 dispatch。
    """
    _mk(tmp_path)
    res = dispatch_plan(_DATE, root=tmp_path / ws.scan_root())
    assert "reused" not in res
    assert set(res["dispatch"]) == {"600584", "000062", "000063"}


def test_dispatch_plan_no_finalists(tmp_path):
    d = tmp_path / ws.scan_root() / _DATE
    d.mkdir(parents=True)
    res = dispatch_plan(_DATE, root=d.parent)
    assert res == {"dispatch": [], "meta": {}}


def test_dispatch_plan_cli(tmp_path, monkeypatch, capsys):
    _mk(tmp_path)
    monkeypatch.chdir(tmp_path)
    from autoresearch.scan.agents.l4_card import main
    assert main(["dispatch-plan", _DATE]) == 0
    out = json.loads(capsys.readouterr().out)
    assert set(out["dispatch"]) == {"600584", "000062", "000063"}
    assert "reused" not in out


# ══════════════════════════ meta(name/sector,全部 dispatch 码;L4 情报站 plan Task 2) ══════════════════════════


def _mk_with_sector(root):
    """mirror `_mk` 但 finalists 多一列 `sector`,验 `meta` 落 name/sector(仅 dispatch 码)。"""
    d = root / ws.scan_root() / _DATE
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
    plan = dispatch_plan(_DATE, root=tmp_path / ws.scan_root())
    assert set(plan["dispatch"]) == {"600584", "000062", "000063"}
    for code in plan["dispatch"]:
        assert plan["meta"][code]["name"] and "sector" in plan["meta"][code]


def test_dispatch_plan_meta_nan_cells(tmp_path):
    """终审 I-1:空单元格(NaN)不得以字面 "nan" 注入盲搜 prompt 的 meta。"""
    sd = tmp_path / "2026-07-09"
    sd.mkdir()
    (sd / "finalists.csv").write_text("code,name,sector\n600584,,\n", encoding="utf-8")
    (sd / "_l4_prompt_600584.md").write_text("x", encoding="utf-8")
    plan = dispatch_plan("2026-07-09", root=tmp_path)
    assert plan["meta"]["600584"] == {
        "name": "", "sector": "", "pinned": False, "dossier_summary": ""}
