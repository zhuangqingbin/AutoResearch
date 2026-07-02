#!/usr/bin/env python3
"""scan-market · 解禁 + 预约披露日历(确定性;harvest 走 tushare)。

design: docs/specs/2026-07-02-scan-calendar-shadow-design.md §1

两个事实日期源:`share_float`(限售解禁 → 风险窗)+ `disclosure_date`(财报预约披露
`pre_date` → 催化日期锚,观察单"中报 beat"类触发从此有确切日子)。产物
`<scan_dir>/calendar.csv`;L4 简报注入风险/催化行,summary 嵌未来两周日历。
铁律:日历是**事实日期**非方向;解禁旗只提示 P4 必核,不自动降级。

  uv run --no-sync python -m autoresearch.scan.calendar 2026-07-02   # L2∪finalists 取数落 staging
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

_CAL_COLS = ["code", "kind", "event_date", "detail", "ratio"]


def _last_quarter_end(date: str) -> str:
    """上一**日历**季末(报告待披露的期次;≠ latest_reported_quarter 的"已披露期"语义)。"""
    dt = datetime.strptime(date[:10], "%Y-%m-%d")
    for m, d in ((12, 31), (9, 30), (6, 30), (3, 31)):
        q = datetime(dt.year, m, d)
        if q <= dt:
            return q.strftime("%Y%m%d")
    return f"{dt.year - 1}1231"


def harvest_calendar(date: str, codes, root: Path | None = None,
                     horizon_days: int = 35) -> pd.DataFrame:
    """拉解禁(≤14 天分块防 6000 行分页截断)+ 预约披露,过滤 codes → calendar.csv。网络。"""
    from autoresearch.data.tushare_source import _code6, _pro, _ts_call
    root = root or Path("context/scan")
    outdir = root / date
    outdir.mkdir(parents=True, exist_ok=True)
    pro = _pro()
    want = {str(c).split(".")[0].zfill(6) for c in codes}
    rows: list[dict] = []

    d0 = datetime.strptime(date[:10], "%Y-%m-%d")
    step = 14
    for off in range(0, horizon_days, step):
        s = (d0 + timedelta(days=off)).strftime("%Y%m%d")
        e = (d0 + timedelta(days=min(off + step - 1, horizon_days))).strftime("%Y%m%d")
        try:
            sf = _ts_call(lambda s=s, e=e: pro.share_float(start_date=s, end_date=e))
        except Exception:  # noqa: BLE001 — 无权限/失败 → 该块跳过(降级)
            continue
        if sf is None or not len(sf):
            continue
        sf = sf.assign(code=_code6(sf["ts_code"]))
        sf = sf[sf["code"].isin(want)]
        if not len(sf):
            continue
        g = sf.groupby(["code", "float_date"], as_index=False).agg(
            ratio=("float_ratio", "sum"), n=("holder_name", "count"),
            share_type=("share_type", "first"))
        for r in g.itertuples(index=False):
            rows.append({"code": r.code, "kind": "unlock", "event_date": str(r.float_date),
                         "detail": f"{r.share_type}·{r.n}方", "ratio": round(float(r.ratio or 0), 3)})

    period = _last_quarter_end(date)
    try:
        dd = _ts_call(lambda: pro.disclosure_date(end_date=period))
    except Exception:  # noqa: BLE001
        dd = None
    if dd is not None and len(dd):
        dd = dd.assign(code=_code6(dd["ts_code"]))
        dd = dd[dd["code"].isin(want)]
        cut = date.replace("-", "")
        for r in dd.itertuples(index=False):
            ev = str(getattr(r, "pre_date", "") or "")
            if getattr(r, "actual_date", None):        # 已实际披露 → 不再是"未来催化"
                continue
            if ev and ev >= cut:
                rows.append({"code": r.code, "kind": "disclosure", "event_date": ev,
                             "detail": f"预约披露(期 {period})", "ratio": None})

    df = pd.DataFrame(rows, columns=_CAL_COLS).sort_values(["event_date", "code"]).reset_index(drop=True)
    df.to_csv(outdir / "calendar.csv", index=False)
    return df


def _load(scan_dir: Path | str) -> pd.DataFrame | None:
    p = Path(scan_dir) / "calendar.csv"
    if not p.exists():
        return None
    try:
        df = pd.read_csv(p, dtype={"code": str, "event_date": str})
        return df if len(df) else None
    except Exception:  # noqa: BLE001
        return None


def calendar_flags(scan_dir: Path | str, code: str, within_days: int = 30,
                   min_ratio: float = 2.0) -> list[str]:
    """该票的日历行(L4 简报注入):解禁窗内且占比≥阈 → ⚠️;预约披露 → 📅。缺文件 → []。"""
    df = _load(scan_dir)
    if df is None:
        return []
    code6 = str(code).split(".")[0].zfill(6)
    day0 = datetime.strptime(Path(scan_dir).name[:10], "%Y-%m-%d")
    cut = (day0 + timedelta(days=within_days)).strftime("%Y%m%d")
    sub = df[df["code"].astype(str).str.zfill(6) == code6]
    out = []
    for r in sub.itertuples(index=False):
        ev = str(r.event_date)[:8]
        if r.kind == "unlock" and ev <= cut and (r.ratio or 0) >= min_ratio:
            out.append(f"- ⚠️ **解禁**:{ev} 占流通 {r.ratio:.1f}%({r.detail})——P4 必核抛压,事实日期非方向")
        elif r.kind == "disclosure":
            out.append(f"- 📅 **预约披露**:{ev}({r.detail})——业绩验证日,触发条件可锚定此日")
    return out


def calendar_section(scan_dir: Path | str, horizon_days: int = 14,
                     big_ratio: float = 5.0) -> str:
    """summary 的未来两周日历块:finalists 披露日 + 大解禁(占比≥big_ratio)。缺文件 → ""。"""
    df = _load(scan_dir)
    if df is None:
        return ""
    scan_dir = Path(scan_dir)
    day0 = datetime.strptime(scan_dir.name[:10], "%Y-%m-%d")
    cut = (day0 + timedelta(days=horizon_days)).strftime("%Y%m%d")
    fin: set[str] = set()
    fp = scan_dir / "finalists.csv"
    if fp.exists():
        try:
            fd = pd.read_csv(fp, dtype={"code": str})
            if "code" in fd.columns:
                fin = set(fd["code"].astype(str).str.zfill(6))
        except Exception:  # noqa: BLE001
            pass
    df = df[df["event_date"].astype(str).str[:8] <= cut]
    disc = df[(df["kind"] == "disclosure") & df["code"].isin(fin)]
    unlk = df[(df["kind"] == "unlock") & (pd.to_numeric(df["ratio"], errors="coerce") >= big_ratio)]
    if not len(disc) and not len(unlk):
        return ""
    lines = [f"### 📅 未来 {horizon_days} 天日历(披露=催化锚,解禁=风险窗;事实日期非方向)"]
    if len(disc):
        lines.append("- **finalists 预约披露**:" + "、".join(
            f"{r.code} {str(r.event_date)[:8]}" for r in disc.itertuples(index=False)))
    if len(unlk):
        top = unlk.sort_values("ratio", ascending=False).head(8)
        lines.append(f"- **大解禁(占比≥{big_ratio:.0f}%)**:" + "、".join(
            f"{r.code} {str(r.event_date)[:8]}({r.ratio:.0f}%)" for r in top.itertuples(index=False)))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="解禁+预约披露日历 harvest(L2∪finalists;网络)")
    ap.add_argument("date", help="scan 日 YYYY-MM-DD")
    ap.add_argument("--horizon", type=int, default=35, help="解禁前瞻天数,默认 35")
    args = ap.parse_args(argv)
    d = Path("context/scan") / args.date
    codes: set[str] = set()
    for fname in ("L2_gbdt_top200.csv", "finalists.csv"):
        p = d / fname
        if p.exists():
            df = pd.read_csv(p, dtype={"code": str})
            if "code" in df.columns:
                codes |= set(df["code"].astype(str).str.zfill(6))
    if not codes:
        print("[calendar] 无 L2/finalists staging,先跑 universe")
        return 1
    df = harvest_calendar(args.date, codes, horizon_days=args.horizon)
    n_u = int((df["kind"] == "unlock").sum()) if len(df) else 0
    n_d = int((df["kind"] == "disclosure").sum()) if len(df) else 0
    print(f"[calendar] {len(codes)} 票 → 解禁 {n_u} 条 + 披露 {n_d} 条 → {d / 'calendar.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
