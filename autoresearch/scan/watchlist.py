#!/usr/bin/env python3
"""scan-market · 结构化观察单 + 触发器日检(确定性,零 LLM)。

design: docs/specs/2026-07-02-scan-watchlist-and-health-metrics-design.md §2.1

哑铃/避险市里高价值产物 = 待触发观察单;本模块把触发条件从报告散文升为机判状态:
`context/watchlist.csv`(跨日活状态)→ 每日对 `L1_scored_full.csv` 逐条件判定 →
`watchlist_status.csv`(staging)→ L5 嵌入。机判词表 v1:close_above/close_below/
ma_bull/money_pos;`manual` 恒待人工。结构化 conds 由编排层补——机器只搬运不理解。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

WATCHLIST_COLS = ("code", "name", "born", "expiry", "source", "narrative",
                  "conds", "invalidation", "note")
_STATUS_ORDER = {"触发": 0, "触发(待人工项)": 1, "临近": 2, "待触发": 3, "失效": 4}
_EXPIRY_DAYS = 45   # born + 45 日历日默认到期


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
    rows = [{"code": str(r["code"]).zfill(6), "name": r.get("name", ""), "born": born,
             "expiry": expiry, "source": "skeptic", "narrative": r.get("trigger", ""),
             "conds": "[]", "invalidation": "[]", "note": ""}
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


def _label(cond: dict) -> str:
    k = cond.get("kind", "?")
    if k in ("close_above", "close_below"):
        return f"{k}:{cond.get('value')}"
    if k == "manual":
        return f"manual:{cond.get('text', '')}"
    return k


def check(wl: pd.DataFrame, l1_full: pd.DataFrame, date: str) -> pd.DataFrame:
    """逐条目判定 → [code,name,status,detail,narrative,born,expiry]。纯函数。"""
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
        machine = [v for c, v in verdicts if c.get("kind") != "manual"]
        has_manual = any(c.get("kind") == "manual" for c, _ in verdicts)
        if inval_hit or expired:
            status = "失效"
        elif machine and all(v == "yes" for v in machine):
            status = "触发(待人工项)" if has_manual else "触发"
        elif any(v == "yes" for v in machine):
            status = "临近"
        else:
            status = "待触发"
        detail = ";".join(f"{_label(c)}={v}" for c, v in verdicts) or "(无机判条件)"
        out.append({"code": code, "name": r.get("name", ""), "status": status, "detail": detail,
                    "narrative": r.get("narrative", ""), "born": r.get("born", ""),
                    "expiry": r.get("expiry", "")})
    return pd.DataFrame(out, columns=["code", "name", "status", "detail",
                                      "narrative", "born", "expiry"])


def run_check(date: str, scan_dir: Path | str,
              path: Path | str = "context/watchlist.csv") -> pd.DataFrame:
    """读 L1_scored_full + watchlist → check → 写 <scan_dir>/watchlist_status.csv。"""
    scan_dir = Path(scan_dir)
    wl = load_watchlist(path)
    src = scan_dir / "L1_scored_full.csv"
    if not len(wl) or not src.exists():
        return pd.DataFrame()
    st = check(wl, pd.read_csv(src, dtype={"code": str}), date)
    st.to_csv(scan_dir / "watchlist_status.csv", index=False)
    return st


def render_watchlist_block(status: pd.DataFrame) -> str:
    """L5 嵌入块:触发置顶,失效垫底;空 → ""。"""
    if status is None or not len(status):
        return ""
    s = status.copy()
    s["_o"] = s["status"].map(_STATUS_ORDER).fillna(9)
    s = s.sort_values(["_o", "code"], kind="stable")
    lines = ["### 👀 观察单日检", "", "| 状态 | 股票 | 条件明细 | 触发叙事 | 到期 |", "|---|---|---|---|---|"]
    for _, r in s.iterrows():
        mark = {"触发": "🔔 **触发**", "触发(待人工项)": "🔔 **触发**(待人工项)",
                "临近": "🟡 临近", "失效": "⚫ 失效"}.get(r["status"], r["status"])
        lines.append(f"| {mark} | {r['name']}({r['code']}) | {r['detail']} "
                     f"| {r['narrative']} | {r['expiry']} |")
    lines.append("")
    lines.append("_触发≠自动升级评级:只提示按 analyze-ticker-lite 复核,评级仍由 rubric 三门定。_")
    return "\n".join(lines) + "\n"
