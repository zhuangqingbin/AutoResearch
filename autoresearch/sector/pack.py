#!/usr/bin/env python3
"""sector-research · 确定性行业层(零 LLM):数据包 + 行业选择器 + L3 全行业地形。

design: docs/specs/2026-07-03-research-skills-altitude-refactor-design.md §5.3/§5.5(Phase 3)。

三件事全部只读 scan staging 既有产物(L1_scored_full / L2_gbdt_top200 / sectors / calendar +
全局 watchlist)——**零新增取数端点**(行业指数 sw_daily 待权限核实〔spec 开放问题 1〕,缺不影响
本包);缺文件/缺列逐字段降级(None/[]/''),presence-gated 不抛。

用法:
  uv run --no-sync python -m autoresearch.sector.pack <date>                     # 自动选行业
  uv run --no-sync python -m autoresearch.sector.pack <date> --industries 电子,煤炭
→ `context/sector/<date>/<行业>.json`(喂 sector-research lite brief);stdout 打印选择理由。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

PACK_ROOT = Path("context/sector")


def _read_csv(p: Path) -> pd.DataFrame | None:
    if not p.exists():
        return None
    try:
        df = pd.read_csv(p, dtype={"code": str})
    except Exception:  # noqa: BLE001 — 坏文件当缺(降级不抛)
        return None
    if not len(df):
        return None
    if "code" in df.columns:
        df["code"] = df["code"].astype(str).str.zfill(6)
    return df


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series([], dtype=float)
    return pd.to_numeric(df[col], errors="coerce")


def _med(s: pd.Series, nd: int = 2) -> float | None:
    s = s.dropna()
    return round(float(s.median()), nd) if len(s) else None


def _safe(industry) -> str:
    """行业名 → 文件名安全(同 assemble._safe_name 口径)。"""
    return re.sub(r'[/\\:*?"<>|\s]', "", str(industry)) or "未分类"


# ───────────────────────── 单行业数据包 ─────────────────────────


def sector_pack(industry: str, scan_dir: Path | str) -> dict:
    """单行业确定性数据包:成分截面聚合 + L2 入选数 + 日历事件计数。字段可缺(None/[])。"""
    scan_dir = Path(scan_dir)
    pack: dict = {"industry": str(industry), "as_of": scan_dir.name,
                  "n_market": 0, "n_l2": 0,
                  "median_pct_60d": None, "median_pe": None, "pe_p25": None, "pe_p75": None,
                  "median_pb": None, "median_np_yoy": None, "median_roe": None,
                  "main_pos_frac": None, "main_net_sum_yi": None,
                  "healthy_n": None, "median_winner": None,
                  "leaders": [], "calendar": None}
    l1 = _read_csv(scan_dir / "L1_scored_full.csv")
    if l1 is None or "industry" not in l1.columns:
        return pack
    g = l1[l1["industry"].astype(str) == str(industry)]
    if not len(g):
        return pack
    pack["n_market"] = int(len(g))
    pack["median_pct_60d"] = _med(_num(g, "pct_60d"))
    pe = _num(g, "pe")
    pe = pe[pe > 0]
    if len(pe):
        pack["median_pe"] = round(float(pe.median()), 2)
        pack["pe_p25"] = round(float(pe.quantile(0.25)), 2)
        pack["pe_p75"] = round(float(pe.quantile(0.75)), 2)
    pack["median_pb"] = _med(_num(g, "pb"))
    pack["median_np_yoy"] = _med(_num(g, "np_yoy"))
    pack["median_roe"] = _med(_num(g, "roe"))
    mnr = _num(g, "main_net_ratio").dropna()
    if len(mnr):
        pack["main_pos_frac"] = round(float((mnr > 0).mean()), 4)
    inflow = _num(g, "main_inflow_yi").dropna()
    if len(inflow):
        pack["main_net_sum_yi"] = round(float(inflow.sum()), 2)
    pack["median_winner"] = _med(_num(g, "winner_rate"))
    try:  # 健康上涨(与菜单体检/healthy 通道同一谓词——单一事实源)
        from autoresearch.common.scoring import healthy_riser_mask
        m = healthy_riser_mask(g)
        pack["healthy_n"] = int(m.sum()) if m is not None else None
    except Exception:  # noqa: BLE001
        pack["healthy_n"] = None
    if "mktcap_yi" in g.columns:  # 龙头映射素材(市值 top5,事实非方向)
        top = g.assign(_cap=_num(g, "mktcap_yi")).nlargest(5, "_cap")
        pack["leaders"] = [
            {"code": r["code"], "name": r.get("name"),
             "mktcap_yi": (round(float(r["_cap"]), 1) if pd.notna(r["_cap"]) else None),
             "pe": (round(float(r["pe"]), 1) if "pe" in top.columns and pd.notna(r.get("pe")) else None),
             "pct_60d": (round(float(r["pct_60d"]), 1)
                         if "pct_60d" in top.columns and pd.notna(r.get("pct_60d")) else None)}
            for _, r in top.iterrows()]
    l2 = _read_csv(scan_dir / "L2_gbdt_top200.csv")
    if l2 is not None and "industry" in l2.columns:
        pack["n_l2"] = int((l2["industry"].astype(str) == str(industry)).sum())
    cal = _read_csv(scan_dir / "calendar.csv")
    if cal is not None and {"code", "event_date"} <= set(cal.columns):
        c = cal[cal["code"].isin(set(g["code"]))]
        if len(c):
            pack["calendar"] = {
                "n_events": int(len(c)),
                "next_date": str(c["event_date"].astype(str).min()),
                "by_kind": (c["kind"].astype(str).value_counts().to_dict()
                            if "kind" in c.columns else {})}
    return pack


# ───────────────────────── 行业选择器(红榜∪集中度∪观察单) ─────────────────────────


def select_briefing_sectors(scan_dir: Path | str, k: int = 6,
                            wl_path: Path | str = Path("context/watchlist.csv"),
                            ) -> tuple[list[str], dict[str, str]]:
    """brief 该给哪几个行业:红榜 top3 ∪ L2 集中度 top3 ∪ 观察单行业,保序去重 cap=k。

    返回 (industries, provenance{行业: 来源});任一来源缺文件 → 该来源为空(降级不抛)。
    行业选择需要"当日菜单"做锚 —— 这是中观 lite 挂 L2 后而非更早的原因(design §4 D6)。
    P7:第四来源 = market_pack.json 的 sector_healthy_top3(确定性看多榜);**top3看多 追加不占 k**
    ——基础三来源仍 cap=k,top3 只在基础集之外补(不挤占红榜/集中度/观察单名额)。
    """
    scan_dir = Path(scan_dir)
    prov: dict[str, str] = {}

    def _add(ind, tag: str) -> None:
        ind = str(ind).strip()
        if ind and ind not in ("未分类", "nan") and ind not in prov:
            prov[ind] = tag

    sec = _read_csv(scan_dir / "sectors.csv")
    if sec is not None and {"industry", "median_pct_60d"} <= set(sec.columns):
        s = sec.assign(_m=pd.to_numeric(sec["median_pct_60d"], errors="coerce")).dropna(subset=["_m"])
        for ind in s.sort_values("_m", ascending=False)["industry"].head(3):
            _add(ind, "红榜top3")
    l2 = _read_csv(scan_dir / "L2_gbdt_top200.csv")
    if l2 is not None and "industry" in l2.columns:
        for ind in l2["industry"].value_counts().head(3).index:
            _add(ind, "L2集中度top3")
    wl = _read_csv(Path(wl_path))
    l1 = _read_csv(scan_dir / "L1_scored_full.csv")
    if wl is not None and "code" in wl.columns and l1 is not None and "industry" in l1.columns:
        imap = dict(zip(l1["code"], l1["industry"], strict=False))
        for c in wl["code"]:
            ind = imap.get(str(c).zfill(6))
            if ind:
                _add(ind, "观察单")
    mp = scan_dir / "market_pack.json"                       # P7:确定性看多 top3(追加,不占 k)
    if mp.exists():
        try:
            for r in (json.loads(mp.read_text(encoding="utf-8")).get("sector_healthy_top3") or [])[:3]:
                _add(r.get("industry"), "top3看多")
        except Exception:  # noqa: BLE001 — 新增来源,坏 pack 不挡行业选择
            pass
    base = [i for i, tag in prov.items() if tag != "top3看多"][:k]
    extra = [i for i in prov if prov[i] == "top3看多" and i not in base]
    inds = base + extra
    return inds, {i: prov[i] for i in inds}


# ───────────────────────── L3 全行业确定性地形(对称覆盖) ─────────────────────────


def sector_terrain_md(scan_dir: Path | str, max_rows: int = 40, top200_only: bool = False) -> str:
    """L3 紧凑表前置的**全行业地形段**:每申万一级一行,全行业对称——防"有 brief 的行业被系统性
    高看"(design §5.5-4)。数字全出 staging;缺 staging → ''(l3_table_md 默认关 = parity)。
    top200_only=True:只渲染 L2 top200 出现过的申万一级行业(~110→30-50 行,L3 表最大块瘦身);
    默认 False = 逐字 parity。"""
    scan_dir = Path(scan_dir)
    l1 = _read_csv(scan_dir / "L1_scored_full.csv")
    if l1 is None or "industry" not in l1.columns:
        return ""
    l2 = _read_csv(scan_dir / "L2_gbdt_top200.csv")
    l2n = (l2["industry"].astype(str).value_counts().to_dict()
           if l2 is not None and "industry" in l2.columns else {})
    if top200_only and l2n:
        l1 = l1[l1["industry"].astype(str).isin(l2n)]   # 只渲染 top200 覆盖行业(≈110→30-50 行)
    try:
        from autoresearch.common.scoring import healthy_riser_mask
        hm = healthy_riser_mask(l1)
    except Exception:  # noqa: BLE001
        hm = None
    rows = []
    for ind, g in l1.groupby("industry"):
        ind = str(ind)
        pe = _num(g, "pe")
        pe = pe[pe > 0]
        mnr = _num(g, "main_net_ratio").dropna()
        rows.append({
            "industry": ind, "n": int(len(g)), "l2": int(l2n.get(ind, 0)),
            "mom": _med(_num(g, "pct_60d"), 1),
            "pe": round(float(pe.median()), 1) if len(pe) else None,
            "mainpos": round(float((mnr > 0).mean()), 2) if len(mnr) else None,
            "healthy": int(hm.loc[g.index].sum()) if hm is not None else None})
    rows.sort(key=lambda r: (-r["l2"], -(r["mom"] if r["mom"] is not None else float("-inf"))))

    def _f(v):
        return "—" if v is None else v

    lines = ["## 全行业地形(确定性 · 申万一级对称覆盖 · 背景校准,非选股指令)",
             "_数字出自 L1_scored_full/L2 staging;健康 = 0<pct60<40∧主力+∧cmf+(与菜单体检同谓词);"
             "无行业 brief 的行业照常按 rubric 判断,不因信息薄降权。_",
             "",
             "| 行业 | 全市场n | 入L2 | 中位60日% | 中位PE | 主力+占比 | 健康数 |",
             "|---|---|---|---|---|---|---|"]
    for r in rows[:max_rows]:
        lines.append(f"| {r['industry']} | {r['n']} | {r['l2']} | {_f(r['mom'])} | "
                     f"{_f(r['pe'])} | {_f(r['mainpos'])} | {_f(r['healthy'])} |")
    return "\n".join(lines)


# ───────────────────────── CLI ─────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="行业确定性数据包(零 LLM;喂 sector-research lite brief)")
    ap.add_argument("date", help="scan 日 YYYY-MM-DD(staging 需已就绪 = L2 后)")
    ap.add_argument("--industries", default=None, help="逗号分隔;缺省 = 自动选(红榜∪集中度∪观察单)")
    ap.add_argument("--scan-dir", default=None, help="缺省 context/scan/<date>")
    ap.add_argument("--k", type=int, default=6, help="自动选行业数上限,默认 6")
    args = ap.parse_args(argv)
    scan_dir = Path(args.scan_dir) if args.scan_dir else Path("context/scan") / args.date
    if args.industries:
        inds = [s.strip() for s in args.industries.split(",") if s.strip()]
        prov: dict[str, str] = {}
    else:
        inds, prov = select_briefing_sectors(scan_dir, k=args.k)
    if not inds:
        print("[sector.pack] 无可选行业(staging 缺/空)—— presence-gated 跳过", file=sys.stderr)
        return 0
    outdir = PACK_ROOT / args.date
    outdir.mkdir(parents=True, exist_ok=True)
    for ind in inds:
        p = sector_pack(ind, scan_dir)
        out = outdir / f"{_safe(ind)}.json"
        out.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
        tag = f"({prov[ind]})" if ind in prov else ""
        print(f"[sector.pack] {ind}{tag} → {out}(全市场 {p['n_market']} 只·入L2 {p['n_l2']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
