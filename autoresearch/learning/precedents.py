#!/usr/bin/env python3
"""判例 FTS 检索索引 —— 跨票同型先例(决策卡 + lessons),零 LLM。

plan: docs/plans/2026-07-11-hermes-selfimprove-plan.md Plan B Task 3。

动机:L4 每次深核都从零判断"这类 setup 历史上怎么样"——但 `context/scan/*/details/*.md`
已经积累了几百张决策卡(评级 + OW三门判定 + 全文论证),`context/knowledge/lessons.jsonl`
也沉淀了同类蒸馏经验,两者都从未被检索复用。本模块建一个轻量 sqlite 索引:

  - **探针先行**:`_fts5_available()` 试建一次性 fts5 虚表,成功即可用;不可用(极少数
    sqlite 编译版本缺 fts5)→ 降级为对 `precedents` 主表做 LIKE 查询。`query()` 是**同一个
    接口**,调用方无感——分支只影响索引内部怎么查,不影响返回形状。
  - **幂等**:主表 `precedents` 以 `uid`(卡=`card:<date>:<code>`,lesson=`lesson:<id>`)为
    主键 upsert;`build_index` 额外按**日**跳过已索引的 scan 目录(增量=只补缺日,不逐文件
    diff)。
  - **path 约定**:`db_path` 与 `scan_root` 是 `context/{knowledge,precedents.db}` 与
    `context/scan` 的兄弟关系;`query()` 签名里没有 `scan_root` 参数,fwd_2 join 需要的
    scan 根目录由 `db_path.parent.parent / "scan"` 反推——测试只需把 db 放在
    `<root>/knowledge/precedents.db`、卡片放在 `<root>/scan/...`(镜像真实 `context/` 布局)
    即可保持隔离,不用额外传参也不会碰真实 `context/`。lessons.jsonl 同理从
    `scan_root.parent / "knowledge" / "lessons.jsonl"` 反推。

用法:
  uv run --no-sync python -m autoresearch.learning.precedents build [--days 90]
  uv run --no-sync python -m autoresearch.learning.precedents query --sector 半导体 \
      --gate 估值不透支 [--flags 质押 解禁] [--k 3] [--days 90]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

import pandas as pd

_SCAN_DEFAULT = Path("context/scan")
_DB_DEFAULT = Path("context/knowledge/precedents.db")

# "# 决策卡 — 002049 紫光国微 @ 2026-07-08" / "# L4 决策卡 — 600601 方正科技 @ 2026-06-25"
# (两种历史卡片 schema 都支持;heading 里的日期不采信——date 一律用目录名,见 spec)。
_HEADING_RE = re.compile(r"^#\s*(?:L4\s*)?决策卡\s*[—\-]\s*(\d{6})\s+(\S+?)\s*@", re.MULTILINE)
# 门型:grep 卡内含 "OW三门" 的整行(该行同时列出三道门的 ✓/✗,原样存作 gate_text,
# 供 query(gate=...) 做子串匹配——调用方若要精确到"某门未过",可把 ✗ 一起传进 gate)。
_GATE_RE = re.compile(r"^.*OW三门.*$", re.MULTILINE)

_META_SCHEMA = """
CREATE TABLE IF NOT EXISTS precedents (
    uid TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    date TEXT,
    code TEXT,
    name TEXT,
    sector TEXT,
    rating TEXT,
    gate_text TEXT,
    verdict_line TEXT,
    body TEXT
)
"""

_UPSERT_SQL = """
INSERT INTO precedents (uid, kind, date, code, name, sector, rating, gate_text, verdict_line, body)
VALUES (:uid, :kind, :date, :code, :name, :sector, :rating, :gate_text, :verdict_line, :body)
ON CONFLICT(uid) DO UPDATE SET
    kind=excluded.kind, date=excluded.date, code=excluded.code, name=excluded.name,
    sector=excluded.sector, rating=excluded.rating, gate_text=excluded.gate_text,
    verdict_line=excluded.verdict_line, body=excluded.body
"""


# ───────────────────────── FTS5 探针 + schema(降级内建于此) ─────────────────────────


def _fts5_available(conn: sqlite3.Connection) -> bool:
    """探针:试建一次性 fts5 虚表。成功→环境支持;`sqlite3.OperationalError`→ 不支持。"""
    try:
        conn.execute("CREATE VIRTUAL TABLE _fts5_probe USING fts5(x)")
        conn.execute("DROP TABLE _fts5_probe")
        return True
    except sqlite3.OperationalError:
        return False


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """主表恒建(plain table,LIKE 分支靠它兜底);fts5 虚表仅在探针通过时建(镜像表,搜索专用)。"""
    conn.execute(_META_SCHEMA)
    if _fts5_available(conn):
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS precedents_fts "
            "USING fts5(uid UNINDEXED, date UNINDEXED, sector, gate_text, body)"
        )
    conn.commit()


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name=?", (name,)
    ).fetchone()
    return row is not None


def _fts_table_exists(conn: sqlite3.Connection) -> bool:
    return _table_exists(conn, "precedents_fts")


def _rebuild_fts_mirror(conn: sqlite3.Connection) -> None:
    """把 `precedents` 全量镜像进 `precedents_fts`(搜索专用副本)。无镜像表(降级环境)→ no-op。

    简单粗暴地整表重建(而非增量同步)——语料量级(几百张卡+几十条 lesson)下秒级完成,
    换来"镜像绝不会跟主表逻辑漂移"的强保证;副作用:一旦环境从"无 fts5"升级到"有 fts5",
    下次 build_index 会自动补建镜像表并回填全部历史行(自愈,非刻意设计但不是坏事)。
    """
    if not _fts_table_exists(conn):
        return
    conn.execute("DELETE FROM precedents_fts")
    conn.execute(
        "INSERT INTO precedents_fts (uid, date, sector, gate_text, body) "
        "SELECT uid, date, sector, gate_text, body FROM precedents"
    )


# ───────────────────────── 纯解析(无 IO,可离线自测) ─────────────────────────


def _clean_str(v) -> str:
    """csv/序列取值 → 干净字符串;None/NaN → ""(pandas 空单元格即便 dtype=str 也会读成 NaN)。"""
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    return str(v)


def _parse_card(text: str, code: str) -> dict:
    """单张卡全文 → {name, rating, gate_text, verdict_line}(纯函数,无 IO)。

    name/rating 来自卡内(heading 里的 code 与文件名 stem 不一致时不采信 heading name,
    留给调用方用 finalists.csv 兜底);rating 用共享 `parse_rating`;门型 grep `OW三门`
    整行;verdict_line = rating + 决策仪表盘『触发位』一句话摘要(缺 → 只留 rating)。
    """
    from autoresearch.agents.utils.rating import parse_rating  # lazy 防环
    from autoresearch.scan.assemble import _clip, _get, _parse_dashboard  # lazy 防环

    name = ""
    m = _HEADING_RE.search(text)
    if m and m.group(1).zfill(6) == code:
        name = m.group(2)

    rating = parse_rating(text)

    gm = _GATE_RE.search(text)
    gate_text = gm.group(0).strip() if gm else ""

    dash = _parse_dashboard(text)
    trigger = _get(dash, "触发位")
    verdict_line = f"{rating} — {_clip(trigger)}" if trigger else rating

    return {"name": name, "rating": rating, "gate_text": gate_text, "verdict_line": verdict_line}


def _flag_terms(flags: str | list[str] | None) -> list[str]:
    if not flags:
        return []
    if isinstance(flags, str):
        return [flags]
    return [f for f in flags if f]


def _fts_quote(s: str) -> str:
    """FTS5 query 里把任意串当字面量短语(双引号包裹,内部引号转义)—— 规避标点被当操作符解析。"""
    return '"' + str(s).replace('"', '""') + '"'


def _build_match(sector: str | None, gate: str | None) -> str | None:
    """sector/gate → fts5 MATCH 表达式(列过滤 + AND);都空 → None(不加 MATCH 子句)。

    **只用于 sector/gate,不用于 flags**:两列的值可靠地被空格/`·`/括号分隔(finalists.csv
    的 sector 是整值入列、`OW三门` 行里各门名前后都有分隔符),fts5 默认 unicode61 分词器
    对这类"干净 token"做精确/前缀匹配没问题。但 `body` 是自由行文,中文长句常常没有任何
    分隔符——unicode61 会把一整段无标点的连续汉字揉成一个大 token,这时候拿一个 2-4 字的
    关键词做 quoted-phrase MATCH 大概率**匹配不到**(它是那个大 token 的子串,不是独立
    token;已用 fts5vocab 实测坐实:"质押比例偏高,留意平仓风险" 只切出两个 token
    "质押比例偏高"/"留意平仓风险","质押" 两字对不上任何一个)。所以 flags 一律走
    `body LIKE '%term%'`(见 `_query_fts`),不进这个 MATCH 表达式。
    """
    parts = []
    if sector:
        parts.append(f"sector:{_fts_quote(sector)}")
    if gate:
        parts.append(f"gate_text:{_fts_quote(gate)}")
    return " AND ".join(parts) if parts else None


# ───────────────────────── IO:目录/文件读取 ─────────────────────────


def _scan_dates(scan_root: Path, days: int) -> list[str]:
    """`scan_root` 下 YYYY-MM-DD 目录名,升序取最近 `days` 个(同 channel_audit._scan_dates 口径)。"""
    if not scan_root.exists():
        return []
    names = sorted(p.name for p in scan_root.iterdir()
                   if p.is_dir() and len(p.name) == 10 and p.name[4] == "-" and p.name[7] == "-")
    return names[-days:] if days > 0 else names


def _card_files(scan_dir: Path) -> list[Path]:
    d = scan_dir / "details"
    return sorted(d.glob("*.md")) if d.exists() else []


def _load_finalist_map(scan_dir: Path) -> pd.DataFrame | None:
    """`finalists.csv`(code 索引,补 sector/name)——缺文件/缺 code 列/读取失败 → None。"""
    fp = scan_dir / "finalists.csv"
    if not fp.exists():
        return None
    try:
        fin = pd.read_csv(fp, dtype=str)
    except Exception:
        return None
    if "code" not in fin.columns:
        return None
    fin["code"] = fin["code"].astype(str).str.zfill(6)
    return fin.set_index("code")


def _lessons_path_for(scan_root: Path) -> Path:
    """`context/scan` → `context/knowledge/lessons.jsonl`(兄弟目录反推,零额外入参)。"""
    return scan_root.parent / "knowledge" / "lessons.jsonl"


def _scan_root_for_db(db_path: Path) -> Path:
    """`context/knowledge/precedents.db` → `context/scan`(兄弟目录反推,给 fwd_2 join 用)。"""
    return db_path.parent.parent / "scan"


def _indexed_dates(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT DISTINCT date FROM precedents WHERE kind='card'").fetchall()
    return {r["date"] for r in rows}


def _index_date(conn: sqlite3.Connection, scan_root: Path, date: str) -> int:
    """单个 scan 日:walk `details/*.md` → 解析 + finalists.csv 兜底 sector/name → upsert。"""
    scan_dir = scan_root / date
    fin_map = _load_finalist_map(scan_dir)
    n = 0
    for fp in _card_files(scan_dir):
        code = fp.stem.zfill(6)
        try:
            text = fp.read_text(encoding="utf-8")
            parsed = _parse_card(text, code)
        except Exception:
            continue
        sector, fin_name = "", ""
        if fin_map is not None and code in fin_map.index:
            row = fin_map.loc[code]
            sector = _clean_str(row.get("sector"))
            fin_name = _clean_str(row.get("name"))
        name = parsed["name"] or fin_name
        conn.execute(_UPSERT_SQL, {
            "uid": f"card:{date}:{code}", "kind": "card", "date": date, "code": code,
            "name": name, "sector": sector, "rating": parsed["rating"],
            "gate_text": parsed["gate_text"], "verdict_line": parsed["verdict_line"],
            "body": text,
        })
        n += 1
    return n


def _index_lessons(conn: sqlite3.Connection, lessons_path: Path) -> int:
    """`lessons.jsonl` 逐行 upsert(kind='lesson');scope.kind=='industry' 才落 sector。"""
    from autoresearch.scan.assemble import _clip  # lazy 防环

    if not lessons_path.exists():
        return 0
    n = 0
    for ln in lessons_path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            rec = json.loads(ln)
        except json.JSONDecodeError:
            continue
        lid = rec.get("id")
        if not lid:
            continue
        scope = rec.get("scope") or {}
        sector = scope.get("value", "") if scope.get("kind") == "industry" else ""
        rule = rec.get("rule", "") or ""
        evidence = rec.get("evidence") or []
        body = rule + "\n" + "\n".join(str(e) for e in evidence)
        date = rec.get("last_reinforced") or rec.get("created") or ""
        conn.execute(_UPSERT_SQL, {
            "uid": f"lesson:{lid}", "kind": "lesson", "date": date, "code": None,
            "name": lid, "sector": sector, "rating": "", "gate_text": "",
            "verdict_line": _clip(rule, 80), "body": body,
        })
        n += 1
    return n


def build_index(days: int = 90, scan_root: Path | str | None = None,
                 db_path: Path | str | None = None) -> dict:
    """增量建判例索引:卡片(按 scan 日跳过已索引日)+ lessons(全量 upsert,量小不设窗)。

    返回 `{dates_scanned, dates_indexed, dates_skipped, n_cards, n_lessons}`。
    幂等:同一 (date, code) / lesson id 的 uid 重跑走 `ON CONFLICT DO UPDATE`,不产生重复行;
    "增量"体现在**日粒度**跳过(已索引的 scan 目录不重新 walk/解析),而非逐文件 diff。
    """
    scan_root = Path(scan_root) if scan_root is not None else _SCAN_DEFAULT
    db_path = Path(db_path) if db_path is not None else _DB_DEFAULT
    conn = _connect(db_path)
    try:
        existing = _indexed_dates(conn)
        dates = _scan_dates(scan_root, days)
        new_dates = [d for d in dates if d not in existing]
        n_cards = sum(_index_date(conn, scan_root, d) for d in new_dates)
        n_lessons = _index_lessons(conn, _lessons_path_for(scan_root))
        conn.commit()
        _rebuild_fts_mirror(conn)
        conn.commit()
        return {
            "dates_scanned": dates,
            "dates_indexed": new_dates,
            "dates_skipped": sorted(set(dates) & existing),
            "n_cards": n_cards,
            "n_lessons": n_lessons,
        }
    finally:
        conn.close()


# ───────────────────────── query(FTS5/LIKE 同一接口) ─────────────────────────


def _cutoff_from_recent(conn: sqlite3.Connection, days: int) -> str | None:
    """最近 `days` 个**库内实际出现过**的 date 值里最早那个(结构性窗口,非 wall-clock 减法——
    与 build_index/channel_audit 的 `_scan_dates` 同一口径,day 语义不依赖真实"今天")。
    库内无任何非空 date → None(调用方应把它当"空库,直接返回 []")。
    """
    rows = conn.execute(
        "SELECT DISTINCT date FROM precedents WHERE date IS NOT NULL AND date != '' ORDER BY date"
    ).fetchall()
    all_dates = [r["date"] for r in rows]
    if not all_dates:
        return None
    window = all_dates[-days:] if days > 0 else all_dates
    return window[0] if window else None


def _query_fts(conn: sqlite3.Connection, sector: str | None, gate: str | None,
               terms: list[str], cutoff: str, k: int) -> list[str]:
    """sector/gate 走 fts5 MATCH(列过滤,可用 bm25 rank 排相关性);flags 走 `body LIKE`
    (原因见 `_build_match` docstring)——同一条 SQL 里混 MATCH 谓词 + 普通 LIKE 谓词已实测
    可行(fts5 虚表允许对任意列附加普通 WHERE 条件,不限于 MATCH 表达式里的列)。
    """
    match = _build_match(sector, gate)
    clauses: list[str] = []
    params: list = []
    if match is not None:
        clauses.append("precedents_fts MATCH ?")
        params.append(match)
    clauses.append("date >= ?")
    params.append(cutoff)
    for t in terms:
        clauses.append("body LIKE ?")
        params.append(f"%{t}%")
    order = "rank" if match is not None else "date DESC"
    sql = f"SELECT uid FROM precedents_fts WHERE {' AND '.join(clauses)} ORDER BY {order} LIMIT ?"
    params.append(k)
    return [r["uid"] for r in conn.execute(sql, params).fetchall()]


def _query_like(conn: sqlite3.Connection, sector: str | None, gate: str | None,
                 terms: list[str], cutoff: str, k: int) -> list[str]:
    clauses = ["date >= ?"]
    params: list = [cutoff]
    if sector:
        clauses.append("sector LIKE ?")
        params.append(f"%{sector}%")
    if gate:
        clauses.append("gate_text LIKE ?")
        params.append(f"%{gate}%")
    for t in terms:
        clauses.append("body LIKE ?")
        params.append(f"%{t}%")
    sql = f"SELECT uid FROM precedents WHERE {' AND '.join(clauses)} ORDER BY date DESC LIMIT ?"
    params.append(k)
    return [r["uid"] for r in conn.execute(sql, params).fetchall()]


def _fetch_rows(conn: sqlite3.Connection, uids: list[str]) -> list[sqlite3.Row]:
    if not uids:
        return []
    placeholders = ",".join("?" for _ in uids)
    rows = conn.execute(
        f"SELECT * FROM precedents WHERE uid IN ({placeholders})", uids
    ).fetchall()
    by_uid = {r["uid"]: r for r in rows}
    return [by_uid[u] for u in uids if u in by_uid]   # 保序(搜索排名顺序,IN() 不保证)


def _fwd2_lookup(scan_root: Path, date: str, code: str | None, cache: dict) -> float | None:
    """best-effort join `retro/attribution.csv` 的 `fwd_2_oc`——缺文件/缺列/缺行 → None。"""
    if not date or not code:
        return None
    if date not in cache:
        ap = scan_root / date / "retro" / "attribution.csv"
        series = None
        if ap.exists():
            try:
                df = pd.read_csv(ap, dtype={"code": str})
                df["code"] = df["code"].astype(str).str.zfill(6)
                if "fwd_2_oc" in df.columns:
                    series = df.set_index("code")["fwd_2_oc"]
            except Exception:
                series = None
        cache[date] = series
    series = cache[date]
    if series is None or code not in series.index:
        return None
    v = series.loc[code]
    return None if pd.isna(v) else round(float(v), 6)


def query(sector: str | None = None, gate: str | None = None,
          flags: str | list[str] | None = None, k: int = 3, days: int = 90,
          db_path: Path | str | None = None) -> list[dict]:
    """跨票同型判例检索——sector/gate/flags 全部 AND('同型' = 各维度都吻合,非任一命中)。

    每条结果 `{date, code, name, verdict_line, fwd_2}`;fwd_2 best-effort join
    attribution.csv(缺 → None,不硬依赖)。db 不存在/主表不存在/窗口内无数据 → `[]`。
    FTS5 可用则用 fts5 镜像表 MATCH(bm25 rank 排序);不可用则对主表 LIKE(date DESC 排序)——
    两分支返回同一形状,调用方无感。
    """
    db_path = Path(db_path) if db_path is not None else _DB_DEFAULT
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        if not _table_exists(conn, "precedents"):
            return []
        cutoff = _cutoff_from_recent(conn, days)
        if cutoff is None:
            return []
        terms = _flag_terms(flags)
        if _fts_table_exists(conn):
            uids = _query_fts(conn, sector, gate, terms, cutoff, k)
        else:
            uids = _query_like(conn, sector, gate, terms, cutoff, k)
        rows = _fetch_rows(conn, uids)
    finally:
        conn.close()

    scan_root = _scan_root_for_db(db_path)
    fwd_cache: dict = {}
    return [
        {
            "date": r["date"],
            "code": r["code"],
            "name": r["name"],
            "verdict_line": r["verdict_line"],
            "fwd_2": _fwd2_lookup(scan_root, r["date"], r["code"], fwd_cache),
        }
        for r in rows
    ]


# ───────────────────────── CLI ─────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m autoresearch.learning.precedents",
                                 description="判例 FTS 索引:build 增量建索引 / query 查跨票同型先例")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="增量建索引(按 scan 日跳过已索引日 + 全量重扫 lessons)")
    b.add_argument("--days", type=int, default=90, help="回看最近 N 个 scan 日目录(默认 90)")
    b.add_argument("--scan-root", default=None)
    b.add_argument("--db-path", default=None)

    q = sub.add_parser("query", help="查跨票同型判例(sector/gate/flags 全 AND)")
    q.add_argument("--sector", default=None)
    q.add_argument("--gate", default=None)
    q.add_argument("--flags", nargs="*", default=None)
    q.add_argument("--k", type=int, default=3)
    q.add_argument("--days", type=int, default=90)
    q.add_argument("--db-path", default=None)

    args = ap.parse_args(argv)

    if args.cmd == "build":
        result = build_index(days=args.days, scan_root=args.scan_root, db_path=args.db_path)
        print(f"[precedents] 索引:{len(result['dates_indexed'])} 新日"
              f"(跳过 {len(result['dates_skipped'])} 已索引日)"
              f" ｜ cards={result['n_cards']} lessons={result['n_lessons']}")
        return 0

    rows = query(sector=args.sector, gate=args.gate, flags=args.flags,
                 k=args.k, days=args.days, db_path=args.db_path)
    if not rows:
        print("[precedents] 无匹配判例", file=sys.stderr)
        return 1
    for r in rows:
        fwd = f"{r['fwd_2']:+.4f}" if r["fwd_2"] is not None else "—"
        print(f"  {r['date']} {r['code'] or '—'} {r['name'] or '—'} | {r['verdict_line']} | fwd_2={fwd}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
