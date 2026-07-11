"""L4 简报跨票判例块 —— `_precedent_mark` presence-gated + 异常降级 + token 预算。

覆盖 docs/plans/2026-07-11-hermes-selfimprove-plan.md Plan B Task 4
(design: docs/specs/2026-07-11-recall-gate-pinned-config-design.md §5.2):
  - 有库有果 → 块含「📚 判例(跨票同型,advisory)」、条数 ≤3
  - 无库 → 空串(presence-gated,风格同 `_inst_mark`/`_seat_mark` 家族)
  - 异常(query 报错)→ 降级空串,不抛
  - k 缓冲后仍封顶 3 条;gate_hint 正确转发做 AND 过滤
  - 同票历史剔除(与 `dossier` 前科卡分工不重复:档案=同票前科,判例=跨票同型)
  - token 预算 ≤400/卡(粗估口径同源 `autoresearch.scan.assemble._BYTES_PER_TOK`)
  - `compose_funnel_brief` 逐卡块正确接线(落点验证;共享前缀契约见 test_l4_prompt_cache_prefix.py)

db 路径按 `_precedent_mark(base, ...)` 的 `base.parent.parent/knowledge/precedents.db` 反推
(镜像 `learning.precedents` 模块自身 `context/{scan,knowledge}` 兄弟约定,零硬编码路径)——
fixture 只需把 `scan_root=tmp_path/"scan"`、`db_path=tmp_path/"knowledge"/"precedents.db"`
摆成兄弟布局,不碰真实 `context/`,不需要 monkeypatch。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from autoresearch.learning import precedents as prec
from autoresearch.scan.agents.l4_card import _precedent_mark, compose_funnel_brief

_BYTES_PER_TOK = 2.8   # mirror autoresearch.scan.assemble._BYTES_PER_TOK(粗估口径同源,不重算)


def _card_text(code: str, name: str, date: str, rating: str, gate_line: str, trigger: str) -> str:
    return f"""# 决策卡 — {code} {name} @ {date}

## 决策仪表盘
| 评级 | 现价 | 时间框架 | 触发位 | 置信度 |
|---|---|---|---|---|
| **{rating}** | 10.00 | 中期 | {trigger} | 中 |

## 一段话研判
合成测试正文,不涉真实公司,便于判例块单测。
{gate_line}
**Rating**: {rating}
FINAL TRANSACTION PROPOSAL: **{rating.upper()}**
"""


def _seed(tmp_path: Path, rows: list[dict]) -> tuple[Path, Path]:
    """rows: [{code,name,date,rating,sector,gate_line?,fwd_2?,trigger?}] → 建卡+finalists(+attribution)
    再 build_index。返回 (scan_root, db_path),镜像 `context/{scan,knowledge}` 兄弟布局。
    """
    scan_root = tmp_path / "scan"
    db_path = tmp_path / "knowledge" / "precedents.db"
    by_date: dict[str, list[dict]] = {}
    for r in rows:
        by_date.setdefault(r["date"], []).append(r)
    for date, rs in by_date.items():
        d = scan_root / date / "details"
        d.mkdir(parents=True, exist_ok=True)
        fin_rows, attr_rows = [], []
        for r in rs:
            text = _card_text(r["code"], r["name"], date, r["rating"], r.get("gate_line", ""),
                              r.get("trigger", "跌破年线→减仓"))
            (d / f"{r['code']}.md").write_text(text, encoding="utf-8")
            fin_rows.append({"code": r["code"], "name": r["name"], "sector": r["sector"]})
            if r.get("fwd_2") is not None:
                attr_rows.append({"code": r["code"], "fwd_2_oc": r["fwd_2"]})
        pd.DataFrame(fin_rows).to_csv(scan_root / date / "finalists.csv", index=False)
        if attr_rows:
            rd = scan_root / date / "retro"
            rd.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(attr_rows).to_csv(rd / "attribution.csv", index=False)
    prec.build_index(scan_root=scan_root, db_path=db_path)
    return scan_root, db_path


def _base_for(scan_root: Path, date: str) -> Path:
    """`_precedent_mark`/`compose_funnel_brief` 的 `base`(= scan_dir 本身)。"""
    d = scan_root / date
    d.mkdir(parents=True, exist_ok=True)
    return d


# ───────────────────────── presence-gated:无库 ─────────────────────────


def test_precedent_mark_no_db_returns_empty(tmp_path):
    base = tmp_path / "scan" / "2026-07-10"
    base.mkdir(parents=True)
    assert _precedent_mark(base, "000001", "半导体", None) == ""


def test_precedent_mark_empty_sector_no_crash(tmp_path):
    scan_root, _db = _seed(tmp_path, [
        {"code": "002049", "name": "紫光国微", "date": "2026-07-01", "rating": "Hold", "sector": "半导体"},
    ])
    base = _base_for(scan_root, "2026-07-10")
    assert _precedent_mark(base, "000001", None, None) != ""   # 无 sector 过滤 → 仍在窗口内查到


# ───────────────────────── 有库有果:渲染 + k 封顶 + gate_hint 转发 ─────────────────────────


def test_precedent_mark_renders_block_with_results(tmp_path):
    scan_root, _db = _seed(tmp_path, [
        {"code": "002049", "name": "紫光国微", "date": "2026-07-01", "rating": "Hold", "sector": "半导体",
         "gate_line": "OW三门 主力真在 ✓·业绩真兑现 ✗·估值不透支 ✗", "fwd_2": -0.031},
    ])
    base = _base_for(scan_root, "2026-07-10")   # 今天 compose 的 base,与判例日期不同
    out = _precedent_mark(base, "000001", "半导体", None)
    assert "📚 判例(跨票同型,advisory)" in out
    assert "002049" in out and "紫光国微" in out
    assert "-3.10%" in out                       # fwd_2 join 到 attribution.csv,渲染成百分比
    n_items = out.count("\n  - ")
    assert 1 <= n_items <= 3


def test_precedent_mark_no_match_returns_empty(tmp_path):
    scan_root, _db = _seed(tmp_path, [
        {"code": "002049", "name": "紫光国微", "date": "2026-07-01", "rating": "Hold", "sector": "半导体"},
    ])
    base = _base_for(scan_root, "2026-07-10")
    assert _precedent_mark(base, "000001", "不存在的板块XYZ", None) == ""


def test_precedent_mark_k_capped_at_3(tmp_path):
    rows = [{"code": f"10000{i}", "name": f"票{i}", "date": f"2026-07-0{i + 1}", "rating": "Hold",
             "sector": "电子", "gate_line": "OW三门 主力真在 ✓·业绩真兑现 ✓·估值不透支 ✗"}
            for i in range(5)]
    scan_root, _db = _seed(tmp_path, rows)
    base = _base_for(scan_root, "2026-07-10")
    out = _precedent_mark(base, "999999", "电子", None)
    assert out.count("\n  - ") == 3


def test_precedent_mark_forwards_gate_hint_as_and_filter(tmp_path):
    # 真实 `OW三门` 行永远三门连写(固定 rubric 格式),gate 过滤只能分辨"提没提这道门"
    # 而非"过没过"(✓/✗ 不参与 token 匹配,精细区分见 precedents.py 既有测试)——
    # 000111 无 gate_line(不提任何门名) vs 000222 有 → 用"提没提"这层粒度验证转发生效。
    rows = [
        {"code": "000111", "name": "甲票", "date": "2026-07-01", "rating": "Hold", "sector": "电子"},
        {"code": "000222", "name": "乙票", "date": "2026-07-02", "rating": "Hold", "sector": "电子",
         "gate_line": "OW三门 主力真在 ✗·业绩真兑现 ✓·估值不透支 ✓"},
    ]
    scan_root, _db = _seed(tmp_path, rows)
    base = _base_for(scan_root, "2026-07-10")
    out_all = _precedent_mark(base, "999999", "电子", None)
    assert "000111" in out_all and "000222" in out_all
    out_gated = _precedent_mark(base, "999999", "电子", "估值不透支")
    assert "000222" in out_gated and "000111" not in out_gated


# ───────────────────────── 同票历史剔除(与 dossier 前科卡分工不重复) ─────────────────────────


def test_precedent_mark_excludes_same_ticker_history(tmp_path):
    rows = [
        {"code": "000001", "name": "本票历史", "date": "2026-07-01", "rating": "Hold", "sector": "电子"},
        {"code": "000002", "name": "他票", "date": "2026-07-02", "rating": "Hold", "sector": "电子"},
    ]
    scan_root, _db = _seed(tmp_path, rows)
    base = _base_for(scan_root, "2026-07-10")
    out = _precedent_mark(base, "000001", "电子", None)   # code6="000001" 与本票历史同码
    assert "000002" in out
    assert "000001" not in out


# ───────────────────────── 异常降级 ─────────────────────────


def test_precedent_mark_degrades_on_exception(tmp_path, monkeypatch):
    scan_root, _db = _seed(tmp_path, [
        {"code": "002049", "name": "紫光国微", "date": "2026-07-01", "rating": "Hold", "sector": "半导体"},
    ])
    base = _base_for(scan_root, "2026-07-10")

    def _boom(**kwargs):
        raise RuntimeError("db locked")

    monkeypatch.setattr(prec, "query", _boom)
    assert _precedent_mark(base, "000001", "半导体", None) == ""


def test_precedent_mark_bad_sector_type_degrades_not_raises(tmp_path):
    scan_root, _db = _seed(tmp_path, [
        {"code": "002049", "name": "紫光国微", "date": "2026-07-01", "rating": "Hold", "sector": "半导体"},
    ])
    base = _base_for(scan_root, "2026-07-10")
    # NaN sector 不炸、不当字面量 "nan" 去脏查询 —— 与显式 sector=None(无过滤)结果一致
    out_nan = _precedent_mark(base, "000001", float("nan"), None)
    out_none = _precedent_mark(base, "000001", None, None)
    assert out_nan == out_none
    assert "002049" in out_nan


# ───────────────────────── token 预算 ≤400/卡 ─────────────────────────


def test_precedent_mark_token_budget_within_400(tmp_path):
    long_name = "跨票判例测试长名称股份有限公司"           # 刻意较长,逼近真实最坏情形
    long_trigger = "跌破半年线且成交量持续萎缩后反弹乏力需结合大盘节奏与板块轮动综合评估风险敞口"  # >48 字,触发 _clip 截断上限
    rows = [
        {"code": f"20000{i}", "name": long_name, "date": f"2026-07-0{i + 1}", "rating": "Underweight",
         "sector": "电子", "gate_line": "OW三门 主力真在 ✗·业绩真兑现 ✗·估值不透支 ✗",
         "fwd_2": -0.1234, "trigger": long_trigger}
        for i in range(3)
    ]
    scan_root, _db = _seed(tmp_path, rows)
    base = _base_for(scan_root, "2026-07-10")
    out = _precedent_mark(base, "999999", "电子", None)
    assert out.count("\n  - ") == 3
    n_tokens = len(out.encode("utf-8")) / _BYTES_PER_TOK
    assert n_tokens <= 400, f"判例块 token 粗估 {n_tokens:.0f} 超预算 400"


# ───────────────────────── compose_funnel_brief 接线:落在逐卡块 ─────────────────────────


def test_compose_funnel_brief_includes_precedent_block(tmp_path):
    scan_root, _db = _seed(tmp_path, [
        {"code": "002049", "name": "紫光国微", "date": "2026-07-01", "rating": "Hold", "sector": "半导体"},
    ])
    today = scan_root / "2026-07-10"
    today.mkdir(parents=True)
    pd.DataFrame([{"code": "000001", "name": "测试股", "industry": "半导体"}]).to_csv(
        today / "L1_recall_top1000.csv", index=False)
    brief = compose_funnel_brief("000001", today)
    assert "📚 判例(跨票同型,advisory)" in brief
    assert "002049" in brief


def test_compose_funnel_brief_unchanged_without_precedents_db(tmp_path):
    """无 precedents.db(老仓库/未 build)→ brief 不含判例块,老 brief 行为不破。"""
    d = tmp_path / "s"
    d.mkdir()
    pd.DataFrame([{"code": "000001", "name": "测试股", "industry": "半导体"}]).to_csv(
        d / "L1_recall_top1000.csv", index=False)
    brief = compose_funnel_brief("000001", d)
    assert "📚 判例" not in brief
    assert "漏斗简报" in brief
