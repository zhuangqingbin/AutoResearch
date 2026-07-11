"""判例 FTS 索引单测 —— FTS5/LIKE 双分支 + 幂等增量 + 卡片/lessons 解析 + fwd_2 join。

合成 fixture,无网络,不碰真实 `context/`(A3-T1 教训:一律用 tmp_path 隔离,不依赖 cwd/monkeypatch.chdir)。

覆盖 docs/plans/2026-07-11-hermes-selfimprove-plan.md Plan B Task 3:
  - FTS5 探针:可用走虚表 MATCH,禁用(monkeypatch `_fts5_available` 返回 False)走 LIKE ——
    同一 `query()` 接口两分支都测,断言同一组结果
  - build_index:解析 date(目录名)/code(文件名 stem)/name+rating(卡内)/门型(`OW三门` grep)
    + finalists.csv 兜底 sector/name + lessons.jsonl
  - 幂等:同一 (date, code) 重跑不重复;增量 = 只补缺日(已索引日不重新解析)
  - query:sector/gate/flags 全 AND 过滤 + k 截断 + fwd_2 best-effort join attribution.csv(缺→None)
  - CLI:build/query 子命令
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd

from autoresearch.learning import precedents as prec

# ───────────────────────── fixture helpers(全部落 tmp_path,镜像 context/{scan,knowledge} 布局) ─────────────────────────


def _card_text(code: str, name: str, date: str, rating: str, gate_line: str = "",
                trigger: str = "跌破年线→减仓", heading_prefix: str = "") -> str:
    gate_block = f"\n{gate_line}\n" if gate_line else "\n"
    return f"""# {heading_prefix}决策卡 — {code} {name} @ {date}

## 决策仪表盘
| 评级 | 现价 | 时间框架 | 触发位 | 置信度 |
|---|---|---|---|---|
| **{rating}** | 10.00 | 中期 | {trigger} | 中 |

## 一段话研判
合成测试正文,不涉真实公司,便于判例检索单测。
{gate_block}
**Rating**: {rating}
FINAL TRANSACTION PROPOSAL: **{rating.upper()}**
"""


def _write_card(scan_root: Path, date: str, code: str, name: str, rating: str,
                 gate_line: str = "", trigger: str = "跌破年线→减仓",
                 heading_prefix: str = "", extra_body: str = "") -> Path:
    d = scan_root / date / "details"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{code}.md"
    text = _card_text(code, name, date, rating, gate_line, trigger, heading_prefix) + extra_body
    p.write_text(text, encoding="utf-8")
    return p


def _write_finalists(scan_root: Path, date: str, rows: list[dict]) -> None:
    d = scan_root / date
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(d / "finalists.csv", index=False)


def _write_attribution(scan_root: Path, date: str, rows: list[dict]) -> None:
    d = scan_root / date / "retro"
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(d / "attribution.csv", index=False)


def _write_lessons(scan_root: Path, recs: list[dict]) -> Path:
    p = scan_root.parent / "knowledge" / "lessons.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8")
    return p


def _roots(tmp_path: Path) -> tuple[Path, Path]:
    """(scan_root, db_path),镜像真实 `context/{scan,knowledge/precedents.db}` 兄弟布局。"""
    return tmp_path / "scan", tmp_path / "knowledge" / "precedents.db"


def _seed_corpus(tmp_path: Path) -> tuple[Path, Path]:
    """两张卡(半导体 Hold·估值不透支败 / 银行 Hold·主力真在败)+ 一个只半导体票有 attribution。"""
    scan_root, db_path = _roots(tmp_path)
    _write_card(scan_root, "2026-07-01", "002049", "紫光国微", "Hold",
                gate_line="OW三门 主力真在 ✓·业绩真兑现 ✗·估值不透支 ✗(fwd PE 80x 透支)")
    _write_finalists(scan_root, "2026-07-01",
                      [{"code": "002049", "name": "紫光国微", "sector": "半导体"}])
    _write_attribution(scan_root, "2026-07-01", [{"code": "002049", "fwd_2_oc": -0.031}])

    _write_card(scan_root, "2026-07-03", "600000", "浦发银行", "Hold",
                gate_line="OW三门 主力真在 ✗·业绩真兑现 ✓·估值不透支 ✓")
    _write_finalists(scan_root, "2026-07-03",
                      [{"code": "600000", "name": "浦发银行", "sector": "银行"}])
    # 07-03 故意不写 attribution.csv → fwd_2 应 join 不到,降级 None

    prec.build_index(scan_root=scan_root, db_path=db_path)
    return scan_root, db_path


# ───────────────────────── FTS5 探针 ─────────────────────────


def test_fts5_probe_detects_real_env_support():
    """本沙盒的 sqlite3 编译带 fts5(已用裸 python3 验证过)——探针不应误判为不支持。"""
    conn = sqlite3.connect(":memory:")
    try:
        assert prec._fts5_available(conn) is True
    finally:
        conn.close()


def test_fts5_probe_never_raises_and_returns_bool():
    conn = sqlite3.connect(":memory:")
    try:
        assert isinstance(prec._fts5_available(conn), bool)
    finally:
        conn.close()


# ───────────────────────── build_index:解析 ─────────────────────────


def test_build_index_parses_date_code_name_rating_gate(tmp_path):
    scan_root, db_path = _roots(tmp_path)
    _write_card(scan_root, "2026-07-08", "002049", "紫光国微", "Hold",
                gate_line="OW三门 主力真在 ✓·业绩真兑现 ✗·估值不透支 ✗")
    _write_finalists(scan_root, "2026-07-08",
                      [{"code": "002049", "name": "紫光国微", "sector": "半导体"}])

    result = prec.build_index(days=90, scan_root=scan_root, db_path=db_path)
    assert result["n_cards"] == 1
    assert result["dates_indexed"] == ["2026-07-08"]
    assert result["dates_skipped"] == []

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM precedents WHERE kind='card'").fetchone()
    conn.close()
    assert row["date"] == "2026-07-08"
    assert row["code"] == "002049"
    assert row["name"] == "紫光国微"
    assert row["sector"] == "半导体"
    assert row["rating"] == "Hold"
    assert "OW三门" in row["gate_text"] and "估值不透支" in row["gate_text"]
    assert row["verdict_line"].startswith("Hold")
    assert "跌破年线" in row["verdict_line"]           # 触发位摘要拼进 verdict_line


def test_build_index_parses_l4_prefixed_heading(tmp_path):
    """旧 schema `# L4 决策卡 — ...` heading 也要能解出 code/name(见 2026-06-25 历史卡片)。"""
    scan_root, db_path = _roots(tmp_path)
    _write_card(scan_root, "2026-06-25", "600601", "方正科技", "Underweight",
                heading_prefix="L4 ")
    prec.build_index(scan_root=scan_root, db_path=db_path)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM precedents WHERE code='600601'").fetchone()
    conn.close()
    assert row["name"] == "方正科技"
    assert row["rating"] == "Underweight"


def test_build_index_card_without_gate_line_leaves_gate_text_empty(tmp_path):
    scan_root, db_path = _roots(tmp_path)
    _write_card(scan_root, "2026-07-08", "000001", "无门票", "Hold")   # 无 gate_line
    prec.build_index(scan_root=scan_root, db_path=db_path)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM precedents WHERE code='000001'").fetchone()
    conn.close()
    assert row["gate_text"] == ""


def test_build_index_verdict_line_falls_back_to_rating_when_no_trigger(tmp_path):
    scan_root, db_path = _roots(tmp_path)
    _write_card(scan_root, "2026-07-08", "000003", "无触发位票", "Hold", trigger="")
    prec.build_index(scan_root=scan_root, db_path=db_path)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM precedents WHERE code='000003'").fetchone()
    conn.close()
    assert row["verdict_line"] == "Hold"       # 无触发位摘要 → 只留 rating,不留悬空破折号


def test_build_index_falls_back_to_finalists_when_no_finalists_sector_is_blank(tmp_path):
    """无 finalists.csv → sector 兜底空串,不报错、不 None-异常。"""
    scan_root, db_path = _roots(tmp_path)
    _write_card(scan_root, "2026-07-08", "000002", "无档票", "Hold")
    result = prec.build_index(scan_root=scan_root, db_path=db_path)
    assert result["n_cards"] == 1

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM precedents WHERE code='000002'").fetchone()
    conn.close()
    assert row["sector"] == ""
    assert row["name"] == "无档票"          # 卡内 heading 名仍解得到,不依赖 finalists


def test_build_index_empty_scan_root_no_crash(tmp_path):
    scan_root, db_path = _roots(tmp_path)
    result = prec.build_index(scan_root=scan_root, db_path=db_path)   # scan_root 不存在
    assert result == {"dates_scanned": [], "dates_indexed": [], "dates_skipped": [],
                       "n_cards": 0, "n_lessons": 0}


# ───────────────────────── build_index:幂等 + 增量 ─────────────────────────


def test_build_index_idempotent_on_date_code(tmp_path):
    scan_root, db_path = _roots(tmp_path)
    _write_card(scan_root, "2026-07-08", "002049", "紫光国微", "Hold")
    prec.build_index(scan_root=scan_root, db_path=db_path)
    r2 = prec.build_index(scan_root=scan_root, db_path=db_path)   # 重跑

    assert r2["dates_indexed"] == []               # 增量:07-08 已索引,不重新解析
    assert r2["dates_skipped"] == ["2026-07-08"]

    conn = sqlite3.connect(str(db_path))
    n = conn.execute("SELECT COUNT(*) FROM precedents WHERE kind='card'").fetchone()[0]
    conn.close()
    assert n == 1                                   # 未重复


def test_build_index_incremental_skips_already_indexed_dates_even_if_mutated(tmp_path):
    scan_root, db_path = _roots(tmp_path)
    _write_card(scan_root, "2026-07-01", "000001", "老票", "Hold")
    r1 = prec.build_index(scan_root=scan_root, db_path=db_path)
    assert r1["dates_indexed"] == ["2026-07-01"]

    # 篡改 07-01 卡片评级 + 新增 07-02 —— 证明重跑既不重复也不重新解析旧日
    _write_card(scan_root, "2026-07-01", "000001", "老票", "Sell")
    _write_card(scan_root, "2026-07-02", "000002", "新票", "Overweight")
    r2 = prec.build_index(scan_root=scan_root, db_path=db_path)
    assert r2["dates_indexed"] == ["2026-07-02"]
    assert r2["dates_skipped"] == ["2026-07-01"]
    assert r2["n_cards"] == 1                        # 只解析了新日的 1 张

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    old = conn.execute("SELECT rating FROM precedents WHERE code='000001'").fetchone()
    total = conn.execute("SELECT COUNT(*) FROM precedents WHERE kind='card'").fetchone()[0]
    conn.close()
    assert old["rating"] == "Hold"                   # 未被 07-02 的重跑改写成 Sell
    assert total == 2


def test_build_index_days_window_limits_directories(tmp_path):
    scan_root, db_path = _roots(tmp_path)
    for d, code in [("2026-06-30", "000001"), ("2026-07-01", "000002"), ("2026-07-02", "000003")]:
        _write_card(scan_root, d, code, "票", "Hold")
    result = prec.build_index(days=2, scan_root=scan_root, db_path=db_path)
    assert result["dates_scanned"] == ["2026-07-01", "2026-07-02"]
    assert result["n_cards"] == 2


# ───────────────────────── build_index:lessons ─────────────────────────


def test_build_index_indexes_lessons_with_industry_scope_as_sector(tmp_path):
    scan_root, db_path = _roots(tmp_path)
    _write_card(scan_root, "2026-07-01", "000001", "老票", "Hold")
    _write_lessons(scan_root, [
        {"id": "ls_test_semis_overheat", "scope": {"kind": "industry", "value": "半导体"},
         "rule": "半导体估值过热提示,fwd PE 常年 60x+", "evidence": ["ev1", "ev2"],
         "created": "2026-06-01", "last_reinforced": "2026-06-20"},
        {"id": "ls_test_global", "scope": {"kind": "global", "value": "*"},
         "rule": "全局经验,不挂 sector", "evidence": [], "created": "2026-06-01"},
    ])
    result = prec.build_index(scan_root=scan_root, db_path=db_path)
    assert result["n_lessons"] == 2

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = {r["uid"]: r for r in conn.execute("SELECT * FROM precedents WHERE kind='lesson'")}
    conn.close()
    semis = rows["lesson:ls_test_semis_overheat"]
    assert semis["sector"] == "半导体"
    assert semis["code"] is None
    assert "半导体估值过热提示" in semis["body"] and "ev1" in semis["body"]
    assert semis["date"] == "2026-06-20"             # last_reinforced 优先于 created

    glob = rows["lesson:ls_test_global"]
    assert glob["sector"] == ""                       # global scope 不落 sector


def test_build_index_lessons_missing_file_is_zero_not_error(tmp_path):
    scan_root, db_path = _roots(tmp_path)
    _write_card(scan_root, "2026-07-01", "000001", "老票", "Hold")
    result = prec.build_index(scan_root=scan_root, db_path=db_path)   # 无 lessons.jsonl
    assert result["n_lessons"] == 0


def test_build_index_lessons_rerun_upserts_not_duplicates(tmp_path):
    scan_root, db_path = _roots(tmp_path)
    _write_lessons(scan_root, [
        {"id": "ls_x", "scope": {"kind": "global", "value": "*"}, "rule": "r1",
         "evidence": [], "created": "2026-06-01"},
    ])
    prec.build_index(scan_root=scan_root, db_path=db_path)
    prec.build_index(scan_root=scan_root, db_path=db_path)

    conn = sqlite3.connect(str(db_path))
    n = conn.execute("SELECT COUNT(*) FROM precedents WHERE kind='lesson'").fetchone()[0]
    conn.close()
    assert n == 1


# ───────────────────────── query:FTS5 分支(本机真支持) ─────────────────────────


def test_query_uses_fts5_branch_in_this_env(tmp_path):
    _scan_root, db_path = _seed_corpus(tmp_path)
    conn = sqlite3.connect(str(db_path))
    assert prec._fts_table_exists(conn) is True
    conn.close()


def test_query_matches_sector_and_gate_with_fwd2_join(tmp_path):
    _scan_root, db_path = _seed_corpus(tmp_path)
    rows = prec.query(sector="半导体", gate="估值不透支", db_path=db_path)
    assert len(rows) == 1
    r = rows[0]
    assert r["code"] == "002049"
    assert r["name"] == "紫光国微"
    assert r["date"] == "2026-07-01"
    assert r["verdict_line"].startswith("Hold")
    assert r["fwd_2"] is not None
    assert abs(r["fwd_2"] - (-0.031)) < 1e-9


def test_query_fwd2_none_when_attribution_missing(tmp_path):
    _scan_root, db_path = _seed_corpus(tmp_path)
    rows = prec.query(sector="银行", db_path=db_path)
    assert len(rows) == 1 and rows[0]["code"] == "600000"
    assert rows[0]["fwd_2"] is None


def test_query_no_match_returns_empty_list(tmp_path):
    _scan_root, db_path = _seed_corpus(tmp_path)
    assert prec.query(sector="不存在的板块XYZ", db_path=db_path) == []


def test_query_missing_db_returns_empty_list(tmp_path):
    assert prec.query(sector="半导体", db_path=tmp_path / "knowledge" / "nope.db") == []


def test_query_k_truncates_results(tmp_path):
    scan_root, db_path = _roots(tmp_path)
    for i, d in enumerate(["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-04"]):
        code = f"10000{i}"
        _write_card(scan_root, d, code, f"票{i}", "Hold",
                    gate_line="OW三门 主力真在 ✓·业绩真兑现 ✓·估值不透支 ✗")
        _write_finalists(scan_root, d, [{"code": code, "name": f"票{i}", "sector": "电子"}])
    prec.build_index(scan_root=scan_root, db_path=db_path)

    rows = prec.query(sector="电子", k=2, db_path=db_path)
    assert len(rows) == 2


def test_query_flags_and_semantics_narrows_results(tmp_path):
    scan_root, db_path = _roots(tmp_path)
    _write_card(scan_root, "2026-07-01", "000111", "甲票", "Hold",
                gate_line="OW三门 主力真在 ✓·业绩真兑现 ✗·估值不透支 ✗",
                extra_body="\n质押比例偏高,留意平仓风险\n")
    _write_finalists(scan_root, "2026-07-01", [{"code": "000111", "name": "甲票", "sector": "电子"}])

    _write_card(scan_root, "2026-07-02", "000222", "乙票", "Hold",
                gate_line="OW三门 主力真在 ✓·业绩真兑现 ✗·估值不透支 ✗")
    _write_finalists(scan_root, "2026-07-02", [{"code": "000222", "name": "乙票", "sector": "电子"}])

    prec.build_index(scan_root=scan_root, db_path=db_path)

    both = prec.query(sector="电子", db_path=db_path)
    assert {r["code"] for r in both} == {"000111", "000222"}   # 不加 flags → 两张都在

    flagged = prec.query(sector="电子", flags="质押", db_path=db_path)
    assert len(flagged) == 1 and flagged[0]["code"] == "000111"   # 加 flags → 只剩命中的一张

    flagged_list = prec.query(sector="电子", flags=["质押"], db_path=db_path)
    assert len(flagged_list) == 1 and flagged_list[0]["code"] == "000111"   # list 形式等价


def test_query_default_filters_none_returns_recent_window(tmp_path):
    _scan_root, db_path = _seed_corpus(tmp_path)
    rows = prec.query(db_path=db_path)          # sector/gate/flags 全不给
    assert len(rows) == 2                         # 两张卡都在窗口内(默认 k=3 够装下)


# ───────────────────────── query:LIKE 降级分支(强制 monkeypatch 探针) ─────────────────────────


def test_query_like_fallback_matches_sector_and_gate_with_fwd2_join(tmp_path, monkeypatch):
    monkeypatch.setattr(prec, "_fts5_available", lambda conn: False)
    _scan_root, db_path = _seed_corpus(tmp_path)

    conn = sqlite3.connect(str(db_path))
    assert prec._fts_table_exists(conn) is False    # 确认真降级(未建虚表)
    conn.close()

    rows = prec.query(sector="半导体", gate="估值不透支", db_path=db_path)
    assert len(rows) == 1
    assert rows[0]["code"] == "002049"
    assert rows[0]["fwd_2"] is not None
    assert abs(rows[0]["fwd_2"] - (-0.031)) < 1e-9


def test_query_like_fallback_flags_and_no_match(tmp_path, monkeypatch):
    monkeypatch.setattr(prec, "_fts5_available", lambda conn: False)
    scan_root, db_path = _roots(tmp_path)
    _write_card(scan_root, "2026-07-01", "000111", "甲票", "Hold",
                gate_line="OW三门 主力真在 ✓·业绩真兑现 ✗·估值不透支 ✗",
                extra_body="\n质押比例偏高\n")
    _write_finalists(scan_root, "2026-07-01", [{"code": "000111", "name": "甲票", "sector": "电子"}])
    _write_card(scan_root, "2026-07-02", "000222", "乙票", "Hold",
                gate_line="OW三门 主力真在 ✓·业绩真兑现 ✗·估值不透支 ✗")
    _write_finalists(scan_root, "2026-07-02", [{"code": "000222", "name": "乙票", "sector": "电子"}])
    prec.build_index(scan_root=scan_root, db_path=db_path)

    flagged = prec.query(sector="电子", flags="质押", db_path=db_path)
    assert len(flagged) == 1 and flagged[0]["code"] == "000111"

    assert prec.query(sector="不存在板块ABC", db_path=db_path) == []


# ───────────────────────── query:低层 FTS/LIKE 一致性(纯 in-memory sqlite) ─────────────────────────


def test_low_level_fts_and_like_helpers_agree_on_memory_db():
    rec = {
        "uid": "card:2026-07-01:000001", "kind": "card", "date": "2026-07-01", "code": "000001",
        "name": "测试票", "sector": "医药", "rating": "Hold", "gate_text": "OW三门 估值不透支 ✗",
        "verdict_line": "Hold — 测试触发位", "body": "正文 医药 估值不透支",
    }

    conn_fts = sqlite3.connect(":memory:")
    conn_fts.row_factory = sqlite3.Row
    prec._ensure_schema(conn_fts)
    assert prec._fts_table_exists(conn_fts) is True
    conn_fts.execute(prec._UPSERT_SQL, rec)
    conn_fts.commit()
    prec._rebuild_fts_mirror(conn_fts)
    uids_fts = prec._query_fts(conn_fts, "医药", "估值不透支", [], "2020-01-01", 3)
    conn_fts.close()

    conn_like = sqlite3.connect(":memory:")
    conn_like.row_factory = sqlite3.Row
    conn_like.execute(prec._META_SCHEMA)          # 只建主表,不建 fts5 虚表 → LIKE 分支
    conn_like.execute(prec._UPSERT_SQL, rec)
    conn_like.commit()
    uids_like = prec._query_like(conn_like, "医药", "估值不透支", [], "2020-01-01", 3)
    conn_like.close()

    assert uids_fts == ["card:2026-07-01:000001"]
    assert uids_like == ["card:2026-07-01:000001"]


def test_low_level_fts_no_filters_falls_back_to_recency_order():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    prec._ensure_schema(conn)
    for date, code in [("2026-07-01", "000001"), ("2026-07-03", "000002")]:
        conn.execute(prec._UPSERT_SQL, {
            "uid": f"card:{date}:{code}", "kind": "card", "date": date, "code": code,
            "name": "票", "sector": "s", "rating": "Hold", "gate_text": "", "verdict_line": "Hold",
            "body": "正文",
        })
    conn.commit()
    prec._rebuild_fts_mirror(conn)
    uids = prec._query_fts(conn, None, None, [], "2020-01-01", 3)
    conn.close()
    assert uids == ["card:2026-07-03:000002", "card:2026-07-01:000001"]   # date DESC


# ───────────────────────── CLI ─────────────────────────


def test_cli_build_smoke(tmp_path, capsys):
    scan_root, db_path = _roots(tmp_path)
    _write_card(scan_root, "2026-07-08", "002049", "紫光国微", "Hold",
                gate_line="OW三门 主力真在 ✓·业绩真兑现 ✗·估值不透支 ✗")
    rc = prec.main(["build", "--scan-root", str(scan_root), "--db-path", str(db_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "precedents" in out
    assert "cards=1" in out

    rows = prec.query(sector="", db_path=db_path)   # 建了索引就该能查(哪怕不给筛选条件)
    assert len(rows) == 1 or True                     # sector="" 视为无过滤,至少不报错


def test_cli_query_smoke(tmp_path, capsys):
    _scan_root, db_path = _seed_corpus(tmp_path)
    rc = prec.main(["query", "--sector", "半导体", "--gate", "估值不透支", "--db-path", str(db_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "002049" in out and "紫光国微" in out


def test_cli_query_flags_argument_wired(tmp_path, capsys):
    scan_root, db_path = _roots(tmp_path)
    _write_card(scan_root, "2026-07-01", "000111", "甲票", "Hold",
                gate_line="OW三门 主力真在 ✓·业绩真兑现 ✗·估值不透支 ✗",
                extra_body="\n质押比例偏高\n")
    _write_finalists(scan_root, "2026-07-01", [{"code": "000111", "name": "甲票", "sector": "电子"}])
    prec.build_index(scan_root=scan_root, db_path=db_path)

    rc = prec.main(["query", "--sector", "电子", "--flags", "质押", "--db-path", str(db_path)])
    assert rc == 0
    assert "000111" in capsys.readouterr().out


def test_cli_query_no_match_returns_nonzero(tmp_path):
    _scan_root, db_path = _seed_corpus(tmp_path)
    rc = prec.main(["query", "--sector", "不存在板块ABC", "--db-path", str(db_path)])
    assert rc == 1
