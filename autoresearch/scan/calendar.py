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

from autoresearch.common import workspace as ws
from autoresearch.scan.user_config import knob

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
                     horizon_days: int = 35, index_rebalance: bool | None = None) -> pd.DataFrame:
    """拉解禁(≤14 天分块防 6000 行分页截断)+ 预约披露 + (旋钮开)指数调样,过滤 codes → calendar.csv。网络。

    第三腿(2026-09-25 §2.3):`index_rebalance=None` → 读旋钮 `calendar.index_rebalance`(默认 False =
    parity)。开时先由 `index_events.harvest_index_events` 落全量 `index_events.csv`(源不可达 → 不落),
    再把 **want 内、phase 已解析(非 unknown_eff)且生效日非空** 的行写成 `kind="index_rebalance"`:
    `event_date=` 被动调仓收盘日,`detail=f"{指数} {调入|调出}|{phase}"`(phase 让 `calendar_flags`
    分两种文案),`ratio=flow_adv_days`。`unknown_eff` 行一律不进日历——即使它的 `eff_close_date`
    非空:fix-round-1 #1(2026-09-25)之前这里只判「日期非空」,但 Task 3 的 `phase_for` 在生效日
    撞上节假日时会**保留**解析/规则算出的日期字符串而不清空它(不猜该往哪边挪),于是「没有日期」
    不再是 `unknown_eff` 的可靠标记——必须直接判 phase,日期检查只是第二道防线。
    """
    from autoresearch.data.tushare_source import _code6, _pro, _ts_call
    index_rebalance = knob("calendar", "index_rebalance", index_rebalance, False)
    root = root or ws.scan_root()
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

    if index_rebalance:
        from autoresearch.scan import index_events as _ie

        ev = _ie.harvest_index_events(date, outdir)
        if ev is not None:
            for r in ev.itertuples(index=False):
                eff = r.eff_close_date
                if r.code not in want or r.phase == "unknown_eff" or not isinstance(eff, str) or not eff:
                    continue
                flow = None if r.flow_adv_days is None or pd.isna(r.flow_adv_days) else float(r.flow_adv_days)
                rows.append({"code": r.code, "kind": "index_rebalance", "event_date": str(eff)[:8],
                             "detail": f"{r.index_name} {'调入' if r.side == 'add' else '调出'}|{r.phase}",
                             "ratio": flow})

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
    """该票的日历行(L4 简报注入):解禁窗内且占比≥阈 → ⚠️;预约披露 → 📅。缺文件 → []。
    指数调样 → 生效前夜 ⛔(唯一带方向词的日历行,方向是「禁止」),其它相位 📅 事实行。"""
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
        elif r.kind == "index_rebalance":
            label, _, phase = str(r.detail).partition("|")
            if phase == "passive_close_eve":
                out.append(f"- ⛔ **指数调样生效前夜**:{label} 于 {ev} 收盘生效;今晚买入 = 与被动资金同价买入,"
                           f"隔夜尺历史为负(docs/specs/2026-09-25-index-inclusion-signal-design.md §1)→ 入场行写 禁止")
            else:
                flow = "" if r.ratio is None or pd.isna(r.ratio) else f";ETF 被动买入≈{float(r.ratio):.1f} 天 ADV"
                out.append(f"- 📅 **指数调样**:{ev} 收盘生效({label}{flow})——事实日期非方向")
    return out


def calendar_section(scan_dir: Path | str, horizon_days: int = 14,
                     big_ratio: float = 5.0) -> str:
    """summary 的未来两周日历块:finalists 披露日 + 大解禁(占比≥big_ratio)+ 指数调样市场级计数。

    三段各自独立,不共用一次"缺文件就交白卷"的早退(fix round 1,2026-09-25):disclosure/unlock
    两段读 `calendar.csv`,调样计数改读**全量** `index_events.csv`——这两个文件谁缺席、谁只有
    表头,都只影响它自己那一段,绝不连带拦掉另一段。这是修一个真实缺陷:`calendar.csv` 的调样行
    只在 `want`(L2∪finalists)命中时才由 `harvest_calendar` 写出,而一次真实调样常常谁都不在这
    份名单里——那正是市场级计数存在的理由,却曾被"`calendar.csv` 读出 `None` 就整函数
    `return ''`"这条早退抢先吞掉,`index_events.csv` 从未被看一眼。

    调样计数不过 `want`/finalists 过滤,理由同上;`unknown_eff` 相位的行即使 `eff_close_date`
    非空也不计入:那一列日期是 `phase_for` 撞上节假日时保留下来的解析产物,不是已核实的生效日
    (同 `harvest_calendar` 对第三腿的处理,2026-09-25 fix-round-1 #1)——这一行断言的是
    「这一天是真的」,把未判定的日期算进计数就是把它当事实发布。

    `scan_dir.name` 解析不出日期(如缺席探针用的假目录)→ 连窗口都算不出,交白卷;三段过滤后
    都真的没有内容 → 交白卷。缺 `finalists.csv` → `fin` 空集,"finalist 涉及 N 只" 照样算得出
    (N 可能就是 0——一次市场级调样很可能一个本轮 finalist 都不涉及)。
    """
    scan_dir = Path(scan_dir)
    try:
        day0 = datetime.strptime(scan_dir.name[:10], "%Y-%m-%d")
    except Exception:  # noqa: BLE001 — 目录名不是日期 → 窗口算不出,只能交白卷
        return ""
    cut = (day0 + timedelta(days=horizon_days)).strftime("%Y%m%d")
    df = _load(scan_dir)
    if df is None:                          # 缺失或只有表头:disclosure/unlock 两段视作空,
        df = pd.DataFrame(columns=_CAL_COLS)  # 不连带拦掉下面读 index_events.csv 的调样计数
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
    from autoresearch.scan.index_events import load_index_events
    ev = load_index_events(scan_dir)
    if ev is not None and len(ev):
        # amendment(task-6):unknown_eff 排除——见上面 docstring 与 harvest_calendar 同一处理。
        ev = ev[ev["eff_close_date"].notna() & (ev["eff_close_date"].astype(str).str[:8] <= cut)
                & (ev["phase"] != "unknown_eff")]
    has_ev = ev is not None and len(ev) > 0
    if not len(disc) and not len(unlk) and not has_ev:
        return ""
    lines = [f"### 📅 未来 {horizon_days} 天日历(披露=催化锚,解禁=风险窗,调样=被动调仓收盘日;事实日期非方向)"]
    if len(disc):
        lines.append("- **finalists 预约披露**:" + "、".join(
            f"{r.code} {str(r.event_date)[:8]}" for r in disc.itertuples(index=False)))
    if len(unlk):
        top = unlk.sort_values("ratio", ascending=False).head(8)
        lines.append(f"- **大解禁(占比≥{big_ratio:.0f}%)**:" + "、".join(
            f"{r.code} {str(r.event_date)[:8]}({r.ratio:.0f}%)" for r in top.itertuples(index=False)))
    if has_ev:
        for e_date, g in ev.groupby(ev["eff_close_date"].astype(str).str[:8]):
            counts = g.groupby("index_name").size()
            fin_n = int(g["code"].astype(str).str.zfill(6).isin(fin).sum())
            lines.append(f"- **指数调样 {e_date} 收盘生效**:"
                         + " / ".join(f"{k} ×{int(v)}" for k, v in counts.items())
                         + f"(finalist 涉及 {fin_n} 只)")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="解禁+预约披露日历 harvest(L2∪finalists;网络)")
    ap.add_argument("date", help="scan 日 YYYY-MM-DD")
    ap.add_argument("--horizon", type=int, default=35, help="解禁前瞻天数,默认 35")
    ap.add_argument("--index-rebalance", action="store_true", default=None, help="强制开第三腿(缺省读旋钮)")
    args = ap.parse_args(argv)
    d = ws.scan_root() / args.date
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
    df = harvest_calendar(args.date, codes, horizon_days=args.horizon, index_rebalance=args.index_rebalance)
    n_u = int((df["kind"] == "unlock").sum()) if len(df) else 0
    n_d = int((df["kind"] == "disclosure").sum()) if len(df) else 0
    n_i = int((df["kind"] == "index_rebalance").sum()) if len(df) else 0
    print(f"[calendar] {len(codes)} 票 → 解禁 {n_u} 条 + 披露 {n_d} 条 + 调样 {n_i} 条 → {d / 'calendar.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
