#!/usr/bin/env python3
"""scan-market · 结构化观察单 + 触发器日检(确定性,零 LLM)。

design: docs/specs/2026-07-02-scan-watchlist-and-health-metrics-design.md §2.1

哑铃/避险市里高价值产物 = 待触发观察单;本模块把触发条件从报告散文升为机判状态:
`context/watchlist.csv`(跨日活状态)→ 每日对 `L1_scored_full.csv` 逐条件判定 →
`watchlist_status.csv`(staging)→ L5 嵌入。机判词表 v2:close_above/close_below/
ma_bull/money_pos/by_date(日期锚⏰);manual 恒待人工。状态梯度:触发>提醒(k/n,k≥2)>
临近(k=1)>待触发;since_born≥+15% 未触发标🔥(错过审计);🆕=较前日新达成。结构化 conds
由编排层补——机器只搬运不理解。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

WATCHLIST_COLS = ("code", "name", "born", "expiry", "source", "narrative",
                  "conds", "invalidation", "note", "born_price")
_FIRE_TH = 0.15     # 未触发已 +15% → 🔥 错过审计旗
_EXPIRY_DAYS = 45   # born + 45 日历日默认到期


def _status_rank(s: str) -> int:
    s = str(s)
    if s.startswith("触发(待人工项)"):
        return 1
    if s.startswith("触发"):
        return 0
    if s.startswith("提醒"):
        return 2
    return {"临近": 3, "待触发": 4, "失效": 5}.get(s, 9)


def load_watchlist(path: Path | str) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame(columns=list(WATCHLIST_COLS))
    df = pd.read_csv(p, dtype=str).fillna("")
    df["code"] = df["code"].astype(str).str.zfill(6)
    return df[[c for c in WATCHLIST_COLS if c in df.columns]]


def ingest_verify(scan_dir: Path | str, path: Path | str) -> int:
    """从 verify.csv 的 `降级` 行草拟观察单条目(narrative=trigger 原文,conds 留空待编排层补)。

    按 (code, born) 去重幂等;born = scan_dir 目录名(数据日)。返回新增行数。
    """
    scan_dir, p = Path(scan_dir), Path(path)
    vf = scan_dir / "verify.csv"
    if not vf.exists():
        return 0
    v = pd.read_csv(vf, dtype={"code": str}).fillna("")
    v = v[v.get("verdict", "") == "降级"]
    if not len(v):
        return 0
    born = scan_dir.name
    expiry = (pd.Timestamp(born) + pd.Timedelta(days=_EXPIRY_DAYS)).strftime("%Y-%m-%d")
    wl = load_watchlist(p)
    seen = set(zip(wl["code"], wl["born"], strict=False)) if len(wl) else set()
    closes: dict[str, float] = {}
    lp = scan_dir / "L1_scored_full.csv"
    if lp.exists():
        try:
            l1 = pd.read_csv(lp, dtype={"code": str})
            if {"code", "close"} <= set(l1.columns):
                closes = dict(zip(l1["code"].astype(str).str.zfill(6),
                                  pd.to_numeric(l1["close"], errors="coerce"), strict=False))
        except Exception:  # noqa: BLE001
            closes = {}
    rows = [{"code": str(r["code"]).zfill(6), "name": r.get("name", ""), "born": born,
             "expiry": expiry, "source": "skeptic", "narrative": r.get("trigger", ""),
             "conds": "[]", "invalidation": "[]", "note": "",
             "born_price": ("" if pd.isna(closes.get(str(r["code"]).zfill(6), float("nan")))
                            else closes.get(str(r["code"]).zfill(6)))}
            for _, r in v.iterrows() if (str(r["code"]).zfill(6), born) not in seen]
    if not rows:
        return 0
    out = pd.concat([wl, pd.DataFrame(rows)], ignore_index=True)
    p.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(p, index=False)
    return len(rows)


def _eval_cond(cond: dict, row: pd.Series | None) -> str:
    """单条件 → yes / no / unknown / manual。row=None 表示该 code 不在 L1_full。"""
    kind = cond.get("kind")
    if kind == "manual":
        return "manual"
    if kind == "by_date":
        return "manual"          # 日期锚人工项:机器不判真伪,只在 _label 标 ⏰临期/到期
    if row is None:
        return "unknown"

    def num(col):
        try:
            v = float(row[col])
            return None if v != v else v
        except (KeyError, TypeError, ValueError):
            return None

    if kind == "close_above":
        c = num("close")
        return "unknown" if c is None else ("yes" if c >= float(cond.get("value", 0)) else "no")
    if kind == "close_below":
        c = num("close")
        return "unknown" if c is None else ("yes" if c <= float(cond.get("value", 0)) else "no")
    if kind == "ma_bull":
        m = num("ma_bull")
        return "unknown" if m is None else ("yes" if m > 0 else "no")
    if kind == "money_pos":
        a, b = num("main_net_ratio"), num("cmf_20")
        if a is None or b is None:
            return "unknown"
        return "yes" if (a > 0 and b > 0) else "no"
    return "unknown"


def _label(cond: dict, date: str | None = None) -> str:
    k = cond.get("kind", "?")
    if k in ("close_above", "close_below"):
        return f"{k}:{cond.get('value')}"
    if k == "manual":
        return f"manual:{cond.get('text', '')}"
    if k == "by_date":
        due, txt = str(cond.get("date", "")), cond.get("text", "")
        mark = ""
        if date and due:
            try:
                if date > due:
                    mark = "(⏰已到期待确认)"
                elif (pd.Timestamp(due) - pd.Timestamp(date)).days <= 3:
                    mark = "(⏰临期)"
            except Exception:  # noqa: BLE001 — 毒日期(如月份 20)不击杀整条 check/run_check
                mark = "(⚠日期无效)"
        return f"by_date:{due}{mark}:{txt}"
    return k


def check(wl: pd.DataFrame, l1_full: pd.DataFrame, date: str) -> pd.DataFrame:
    """逐条目判定 → [code,name,status,detail,narrative,born,expiry,k,n,since_born,fire]。纯函数。

    状态梯度(spec 2026-07-05 wave §C1):机判全 yes=触发(带人工项则待人工);k≥2=提醒(k/n);
    k=1=临近;k=0=待触发;invalidation/过期=失效。by_date/manual 不计入机判分母。
    since_born=close/born_price−1(错过审计);fire=未触发且 since_born≥+15%。
    """
    l1 = l1_full.copy()
    if "code" in l1.columns:
        l1["code"] = l1["code"].astype(str).str.zfill(6)
        l1 = l1.set_index("code")
    out = []
    for _, r in wl.iterrows():
        code = str(r["code"]).zfill(6)
        row = l1.loc[code] if code in l1.index else None
        conds = json.loads(r.get("conds") or "[]")
        inval = json.loads(r.get("invalidation") or "[]")
        verdicts = [(c, _eval_cond(c, row)) for c in conds]
        inval_hit = any(_eval_cond(c, row) == "yes" for c in inval)
        expired = bool(r.get("expiry")) and date > str(r.get("expiry"))
        machine = [v for c, v in verdicts if c.get("kind") not in ("manual", "by_date")]
        has_manual = any(c.get("kind") in ("manual", "by_date") for c, _ in verdicts)
        k, n = sum(1 for v in machine if v == "yes"), len(machine)
        if inval_hit or expired:
            status = "失效"
        elif machine and k == n:
            status = "触发(待人工项)" if has_manual else "触发"
        elif k >= 2:
            status = f"提醒({k}/{n})"
        elif k == 1:
            status = "临近"
        else:
            status = "待触发"
        bp = pd.to_numeric(pd.Series([r.get("born_price")]), errors="coerce").iloc[0]
        close = None
        if row is not None:
            close = pd.to_numeric(pd.Series([row.get("close")]), errors="coerce").iloc[0]
        since = (round(float(close) / float(bp) - 1.0, 4)
                 if bp and not pd.isna(bp) and close is not None and not pd.isna(close) else None)
        fire = bool(since is not None and since >= _FIRE_TH and not status.startswith("触发"))
        detail = ";".join(f"{_label(c, date)}={v}" for c, v in verdicts) or "(无机判条件)"
        out.append({"code": code, "name": r.get("name", ""), "status": status, "detail": detail,
                    "narrative": r.get("narrative", ""), "born": r.get("born", ""),
                    "expiry": r.get("expiry", ""), "k": k, "n": n,
                    "since_born": since, "fire": fire})
    return pd.DataFrame(out, columns=["code", "name", "status", "detail", "narrative",
                                      "born", "expiry", "k", "n", "since_born", "fire"])


def _prev_status(scan_dir: Path) -> pd.DataFrame | None:
    """上一 scan 日的 watchlist_status(比对 k 增量用);无 → None。"""
    scan_dir = Path(scan_dir)
    prevs = sorted((p for p in scan_dir.parent.iterdir()
                    if p.is_dir() and p.name[:2] == "20" and p.name < scan_dir.name
                    and (p / "watchlist_status.csv").exists()), reverse=True)
    if not prevs:
        return None
    try:
        return pd.read_csv(prevs[0] / "watchlist_status.csv", dtype={"code": str})
    except Exception:  # noqa: BLE001
        return None


def mark_new(st: pd.DataFrame, prev: pd.DataFrame | None) -> pd.DataFrame:
    """加 `new_k` 列:较前日**新达成**机判条件(k 增)→ True。无前日/前日无 k 列 → 全 False
    (防首日全🆕噪声)。持续满足的条目不再置顶播报,只常规行显示——spec §C1。"""
    s = st.copy()
    if prev is None or "k" not in getattr(prev, "columns", ()):
        s["new_k"] = False
        return s
    pk = dict(zip(prev["code"].astype(str).str.zfill(6),
                  pd.to_numeric(prev["k"], errors="coerce").fillna(0), strict=False))
    s["new_k"] = [float(r.get("k") or 0) > float(pk.get(str(r["code"]).zfill(6),
                                                        float(r.get("k") or 0)))
                  for r in s.to_dict("records")]
    return s


def run_check(date: str, scan_dir: Path | str,
              path: Path | str = "context/watchlist.csv") -> pd.DataFrame:
    """读 L1_scored_full + watchlist → check → 写 <scan_dir>/watchlist_status.csv。"""
    scan_dir = Path(scan_dir)
    wl = load_watchlist(path)
    src = scan_dir / "L1_scored_full.csv"
    if not len(wl) or not src.exists():
        return pd.DataFrame()
    st = check(wl, pd.read_csv(src, dtype={"code": str}), date)
    st = mark_new(st, _prev_status(scan_dir))
    st.to_csv(scan_dir / "watchlist_status.csv", index=False)
    return st


def express_candidates(scan_dir: Path | str) -> pd.DataFrame:
    """触发直通车名单:watchlist_status 触发* 且不在今日 finalists → 待追加行(直达 L4 再判)。

    防悲剧:触发了、但该票当天不在 L2 菜单 → 没人研究。行 lane=watchlist_trigger,
    thesis=触发叙事;sector 从 L1_scored_full.industry 补。缺 status/finalists → 空。
    """
    scan_dir = Path(scan_dir)
    sp, fp = scan_dir / "watchlist_status.csv", scan_dir / "finalists.csv"
    cols = ["ticker", "code", "name", "sector", "conviction", "thesis", "risk", "catalyst", "lane"]
    if not sp.exists() or not fp.exists():
        return pd.DataFrame(columns=cols)
    st = pd.read_csv(sp, dtype={"code": str}).fillna("")
    trig = st[st["status"].astype(str).str.startswith("触发")]
    if not len(trig):
        return pd.DataFrame(columns=cols)
    fin = pd.read_csv(fp, dtype={"code": str})
    have = set(fin["code"].astype(str).str.zfill(6)) if "code" in fin.columns else set()
    ind: dict[str, str] = {}
    lp = scan_dir / "L1_scored_full.csv"
    if lp.exists():
        l1 = pd.read_csv(lp, dtype={"code": str})
        if {"code", "industry"} <= set(l1.columns):
            ind = dict(zip(l1["code"].astype(str).str.zfill(6), l1["industry"], strict=False))
    rows = []
    for _, r in trig.iterrows():
        code = str(r["code"]).zfill(6)
        if code in have:
            continue
        rows.append({"ticker": code, "code": code, "name": r.get("name", ""),
                     "sector": ind.get(code, ""), "conviction": "",
                     "thesis": f"观察单触发:{r.get('narrative', '')}",
                     "risk": "", "catalyst": str(r.get("detail", "")), "lane": "watchlist_trigger"})
    return pd.DataFrame(rows, columns=cols)


def append_express(scan_dir: Path | str) -> int:
    """把直通车行追加进 finalists.csv(幂等:code 已在则跳)。返回追加数。"""
    scan_dir = Path(scan_dir)
    ex = express_candidates(scan_dir)
    if not len(ex):
        return 0
    fp = scan_dir / "finalists.csv"
    fin = pd.read_csv(fp, dtype={"code": str})
    out = pd.concat([fin, ex[[c for c in ex.columns if c in fin.columns or c in
                              ("ticker", "code", "name", "sector", "thesis", "lane", "catalyst")]]],
                    ignore_index=True)
    out.to_csv(fp, index=False)
    return len(ex)


def render_watchlist_block(status: pd.DataFrame) -> str:
    """L5 嵌入块:触发置顶,失效垫底;born→今 = 错过审计列;空 → ""。"""
    if status is None or not len(status):
        return ""
    s = status.copy()
    s["_o"] = s["status"].map(_status_rank)
    s = s.sort_values(["_o", "code"], kind="stable")
    lines = ["### 👀 观察单日检", "",
             "| 状态 | 股票 | born→今 | 条件明细 | 触发叙事 | 到期 |", "|---|---|---|---|---|---|"]
    for _, r in s.iterrows():
        st = str(r["status"])
        if st.startswith("触发(待人工项)"):
            mark = "🔔 **触发**(待人工项)"
        elif st.startswith("触发"):
            mark = "🔔 **触发**"
        elif st.startswith("提醒"):
            mark = f"🟠 {st}"
        else:
            mark = {"临近": "🟡 临近", "失效": "⚫ 失效"}.get(st, st)
        if bool(r.get("new_k")):
            mark = "🆕 " + mark            # Δ新达成:较前日新满足了机判条件(spec §C1 噪声控制)
        sb = r.get("since_born")
        sb_txt = "—" if sb is None or pd.isna(sb) else f"{float(sb):+.0%}"
        if bool(r.get("fire")):
            sb_txt += " 🔥"
        lines.append(f"| {mark} | {r['name']}({r['code']}) | {sb_txt} | {r['detail']} "
                     f"| {r['narrative']} | {r['expiry']} |")
    lines.append("")
    lines.append("_触发≠自动升级评级:按 stock-research lite 档复核(拟下重注可对该票升 full 档),"
                 "评级仍由 rubric 三门定;🔥=未触发已 +15%(条件可能太保守,candidate 进 proposals)。_")
    return "\n".join(lines) + "\n"


def backfill_born_price(path: Path | str = "context/watchlist.csv",
                        lake: Path | str = "context/lake/daily") -> int:
    """存量条目补 born_price(born 日 lake close);已有值/湖缺该日 → 跳过。返回补的行数。"""
    from autoresearch.data.tushare_source import _code6
    p, lake = Path(path), Path(lake)
    wl = load_watchlist(p)
    if not len(wl):
        return 0
    if "born_price" not in wl.columns:
        wl["born_price"] = ""
    n = 0
    for i, r in wl.iterrows():
        if str(r.get("born_price", "")).strip():
            continue
        day = str(r.get("born", "")).replace("-", "")
        fp = lake / f"{day}.parquet"
        if not fp.exists():
            continue
        try:
            df = pd.read_parquet(fp, columns=["ts_code", "close"])
            df = df.assign(_c=_code6(df["ts_code"]))
            sub = df[df["_c"] == str(r["code"]).zfill(6)]
            if len(sub) and not pd.isna(sub.iloc[0]["close"]):
                wl.at[i, "born_price"] = float(sub.iloc[0]["close"])
                n += 1
        except Exception:  # noqa: BLE001 — 单行降级
            continue
    if n:
        wl.to_csv(p, index=False)
    return n


def migrate_by_date(path: Path | str = "context/watchlist.csv") -> int:
    """conds 里带 MM-DD 日期的 manual → by_date(年份取 born 年;幂等)。返回改的条目数。"""
    import re
    p = Path(path)
    wl = load_watchlist(p)
    if not len(wl):
        return 0
    n = 0
    for i, r in wl.iterrows():
        try:
            conds = json.loads(r.get("conds") or "[]")
        except Exception:  # noqa: BLE001
            continue
        year = str(r.get("born", ""))[:4] or "2026"
        changed = False
        for c in conds:
            if c.get("kind") != "manual":
                continue
            m = re.search(r"(\d{2})-(\d{2})", str(c.get("text", "")))
            if not m:
                continue
            mm, dd = m.group(1), m.group(2)
            try:
                pd.Timestamp(f"{year}-{mm}-{dd}")
            except Exception:  # noqa: BLE001 — 非法日期(如 20-25)跳过不转
                continue
            c["kind"], c["date"] = "by_date", f"{year}-{mm}-{dd}"
            changed = True
        if changed:
            wl.at[i, "conds"] = json.dumps(conds, ensure_ascii=False)
            n += 1
    if n:
        wl.to_csv(p, index=False)
    return n


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="观察单维护 CLI")
    ap.add_argument("cmd", choices=["backfill", "migrate"], help="backfill=补born_price;migrate=manual带日期→by_date")
    args = ap.parse_args(argv)
    n = backfill_born_price() if args.cmd == "backfill" else migrate_by_date()
    print(f"[watchlist] {args.cmd}: {n} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
