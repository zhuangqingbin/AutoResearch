"""dossier 确定性建档骨架(spec ①②;确定性,零 LLM)。

design: docs/specs/2026-07-22-research-depth-dossier-design.md ①②;
plan: docs/plans/2026-07-23-wave2-dossier-foundation-plan.md Task 7。

只搭骨架,不写叙事:frontmatter(`initiated=None`)+ 摘要六锚(机算两行「带位/判例」+
叙事四行占位「(待首覆)」)+ 八节(§1/§2/§5 留 `<!-- LLM:待首覆 -->` 锚 + prefetch 素材;
§3 估值带确定性成表;§4/§6 从最近一个 scan 日的 staging(seats/pledge/calendar.csv)
presence-gated 抄该码行;§7 复用 `scan.dossier` 前科机制;§8 空日志 + 首行建档记录)。
叙事锚由 Task 8 的 dossier-init agent 首覆填入,本模块只管确定性节。

幂等:档案已存在且非 `force` → 原文一字不动。降级:prefetch json 或某一腿缺 /
staging 缺该码行 → 该素材位写 `[数据缺,<today>]`(数据契约 B 级:降级必须留痕,不空写)。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from autoresearch.dossier import prefetch, schema

_LLM_ANCHOR = "<!-- LLM:待首覆 -->"
_NARRATIVE_PENDING = "(待首覆)"
_NO_PRECEDENT = "(无入围史)"
_PRECEDENT_WINDOW = 10          # 与 scan.dossier 的 stock_dossier/render_dossier 同窗,显式对齐防读数漂移
_STAGING_FILES = ("seats.csv", "pledge.csv", "calendar.csv")
_COMPUTED_ANCHORS = {"带位:": "带位", "判例:": "判例"}   # SUMMARY_ANCHORS 里两条机算锚 → calc dict 键


def _missing(today: str) -> str:
    """统一降级留痕串(数据契约:降级必须留痕,不空写)。"""
    return f"[数据缺,{today}]"


# ---------------------------------------------------------------------------
# 机算:估值带位(§3 表尾一句 与 摘要「带位:」行 同源,同一份计算)
# ---------------------------------------------------------------------------

def _band_bracket(now: float, p25: float, p50: float, p75: float) -> str:
    if now < p25:
        return "<P25"
    if now < p50:
        return "P25~P50"
    if now < p75:
        return "P50~P75"
    return ">P75"


def _band_position_text(band: dict | None) -> str:
    if not band:
        return "数据缺(待预取)"
    now, p25, p50, p75 = (band.get(k) for k in ("pe_now", "pe_p25", "pe_p50", "pe_p75"))
    if None in (now, p25, p50, p75):
        return "数据缺(待预取)"
    now, p25, p50, p75 = float(now), float(p25), float(p50), float(p75)
    bracket = _band_bracket(now, p25, p50, p75)
    return f"当前 PE={now:.1f} 落带位 {bracket}(P25={p25:.1f}/P50={p50:.1f}/P75={p75:.1f})"


def _val_band_table(band: dict) -> str:
    rows = [("P25", band.get("pe_p25"), band.get("pb_p25")),
            ("P50", band.get("pe_p50"), band.get("pb_p50")),
            ("P75", band.get("pe_p75"), band.get("pb_p75")),
            ("现值", band.get("pe_now"), band.get("pb_now"))]
    has_pb = any(pb is not None for _, _, pb in rows)
    header = "| 分位 | PE | PB |" if has_pb else "| 分位 | PE |"
    sep = "|---|---|---|" if has_pb else "|---|---|"
    lines = [header, sep]
    for label, pe, pb in rows:
        pe_txt = f"{float(pe):.1f}" if pe is not None else "—"
        if has_pb:
            pb_txt = f"{float(pb):.1f}" if pb is not None else "—"
            lines.append(f"| {label} | {pe_txt} | {pb_txt} |")
        else:
            lines.append(f"| {label} | {pe_txt} |")
    return "\n".join(lines)


def render_summary_calc(prefetch_data: dict | None, precedent_n: int) -> dict[str, str]:
    """摘要六锚里两条机算行(带位/判例)的实值文本;纯函数,δ 阶段重算摘要时同样复用。"""
    band = (prefetch_data or {}).get("val_band")
    prec_txt = f"近 {_PRECEDENT_WINDOW} 扫描日入围 {precedent_n} 次" if precedent_n > 0 else _NO_PRECEDENT
    return {"带位": _band_position_text(band), "判例": prec_txt}


# ---------------------------------------------------------------------------
# §1/§2 prefetch 素材(mainbz 表 / fwd-EPS 行)
# ---------------------------------------------------------------------------

def _mainbz_table(rows: list[dict]) -> str:
    """业务拆分表;金额原样落亿元、保留一位小数(不四舍五入丢精度)。"""
    lines = ["| 期间 | 业务 | 收入(亿元) | 利润(亿元) |", "|---|---|---|---|"]
    for r in rows:
        sales, profit = r.get("bz_sales"), r.get("bz_profit")
        sales_txt = f"{sales / 1e8:.1f}" if sales is not None else "—"
        profit_txt = f"{profit / 1e8:.1f}" if profit is not None else "—"
        lines.append(f"| {r.get('period', '—')} | {r.get('bz_item', '—')} | "
                     f"{sales_txt} | {profit_txt} |")
    return "\n".join(lines)


def _fwd_eps_line(fwd: dict) -> str:
    years = sorted(k for k in fwd if str(k).startswith("fwd_eps_"))
    parts = [f"{k.replace('fwd_eps_', '')}={float(fwd[k]):.2f}" for k in years]
    return "一致预期 fwd-EPS:" + "、".join(parts)


# ---------------------------------------------------------------------------
# §4/§6 staging(seats/pledge/calendar.csv)presence-gated 抄该码行
# ---------------------------------------------------------------------------

def _latest_staging_dir(scan_root: Path) -> Path | None:
    """scan_root 下最近一个含 §4/§6 素材(seats/pledge/calendar 任一)的日期目录;缺 → None。"""
    if not scan_root.exists():
        return None
    days = sorted((p for p in scan_root.iterdir() if p.is_dir() and p.name[:2] == "20"),
                  key=lambda p: p.name, reverse=True)
    for d in days:
        if any((d / f).exists() for f in _STAGING_FILES):
            return d
    return None


def _pledge_row(staging_dir: Path | None, code6: str) -> str:
    if staging_dir is None:
        return ""
    p = staging_dir / "pledge.csv"
    if not p.exists():
        return ""
    try:
        df = pd.read_csv(p, dtype={"code": str})
        df["code"] = df["code"].astype(str).str.zfill(6)
        sub = df[df["code"] == code6]
        if not len(sub):
            return ""
        r = sub.iloc[0]
        ratio = float(pd.to_numeric(pd.Series([r.get("pledge_ratio")]), errors="coerce").iloc[0])
        if pd.isna(ratio):
            return ""
        end = r.get("end_date", "—")
    except Exception:  # noqa: BLE001 — 行可选,缺了不挡建档
        return ""
    return f"- **质押**(扫描日 {staging_dir.name}):比例 {ratio:.1f}%(截至 {end})"


def _seats_row(staging_dir: Path | None, code6: str) -> str:
    if staging_dir is None:
        return ""
    p = staging_dir / "seats.csv"
    if not p.exists():
        return ""
    try:
        df = pd.read_csv(p, dtype={"code": str})
        df["code"] = df["code"].astype(str).str.zfill(6)
        sub = df[df["code"] == code6]
        if not len(sub):
            return ""
        r = sub.iloc[0]
        n = float(pd.to_numeric(pd.Series([r.get("n_appear")]), errors="coerce").iloc[0] or 0)
        inst = float(pd.to_numeric(pd.Series([r.get("inst_net_wan")]), errors="coerce").iloc[0] or 0)
        retail = float(pd.to_numeric(pd.Series([r.get("retail_net_wan")]), errors="coerce").iloc[0] or 0)
        if n <= 0:
            return ""
    except Exception:  # noqa: BLE001 — 行可选,缺了不挡建档
        return ""
    return (f"- **龙虎榜席位**(扫描日 {staging_dir.name}):近窗口上榜 {int(n)} 次,"
            f"机构净买 {inst:+.0f}万,游资/营业部净买 {retail:+.0f}万")


def _section4_body(staging_dir: Path | None, code6: str, today: str) -> str:
    rows = [r for r in (_pledge_row(staging_dir, code6), _seats_row(staging_dir, code6)) if r]
    return "\n".join(rows) if rows else _missing(today)


def _section6_body(staging_dir: Path | None, code6: str, today: str) -> str:
    if staging_dir is None:
        return _missing(today)
    from autoresearch.scan.calendar import calendar_flags  # lazy import 防环(scan↔dossier)
    flags = calendar_flags(staging_dir, code6)
    return "\n".join(flags) if flags else _missing(today)


# ---------------------------------------------------------------------------
# prefetch 落盘 json 读取(module-attr 调用,兼容测试 monkeypatch PREFETCH_DIR)
# ---------------------------------------------------------------------------

def _load_prefetch(code6: str) -> dict | None:
    p = prefetch.PREFETCH_DIR / f"{code6}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 坏 json 当缺处理,不阻断建档
        return None


# ---------------------------------------------------------------------------
# 渲染装配(节头一律用 schema.SECTIONS,不重复字面量)
# ---------------------------------------------------------------------------

def _section(idx: int, body: str) -> str:
    return f"{schema.SECTIONS[idx]}\n{body}\n"


def _summary_lines(calc: dict[str, str]) -> list[str]:
    lines = [schema.SUMMARY_HEAD]
    for anchor in schema.SUMMARY_ANCHORS:
        key = _COMPUTED_ANCHORS.get(anchor)
        val = calc[key] if key else _NARRATIVE_PENDING
        lines.append(f"- {anchor} {val}")
    return lines


def build_skeleton(code6: str, today: str, *, name: str = "", sector: str = "",
                   scan_root: str | Path = "context/scan", force: bool = False) -> dict:
    """确定性建档骨架。档案已存在且非 force → 原文不动(`created=False`)。"""
    code6 = str(code6).split(".")[0].zfill(6)
    path = schema.dossier_path(code6)
    if path.exists() and not force:
        return {"path": path, "created": False, "issues": []}

    prefetch_data = _load_prefetch(code6)
    mainbz = (prefetch_data or {}).get("mainbz") or []
    fwd_eps = (prefetch_data or {}).get("fwd_eps") or {}
    has_fwd = isinstance(fwd_eps, dict) and any(str(k).startswith("fwd_eps_") for k in fwd_eps)
    val_band = (prefetch_data or {}).get("val_band") or None

    scan_root = Path(scan_root)
    staging_dir = _latest_staging_dir(scan_root)

    from autoresearch.scan import dossier as scan_dossier  # lazy import 防环(scan↔dossier)
    entries = scan_dossier.stock_dossier(code6, scan_root=scan_root, exclude=today,
                                         max_days=_PRECEDENT_WINDOW)
    precedent_text = scan_dossier.render_dossier(code6, scan_root=scan_root, exclude=today,
                                                 max_days=_PRECEDENT_WINDOW)
    calc = render_summary_calc(prefetch_data, len(entries))

    meta = {"code": code6, "name": name, "sector": sector, "pool_status": "active",
            "entered": today, "entry_reason": None, "initiated": None,
            "last_refresh": None, "last_delta": None}

    parts = [
        schema.render_frontmatter(meta),
        "\n".join(_summary_lines(calc)) + "\n",
        _section(0, (_mainbz_table(mainbz) if mainbz else _missing(today)) + "\n\n" + _LLM_ANCHOR),
        _section(1, (_fwd_eps_line(fwd_eps) if has_fwd else _missing(today)) + "\n\n" + _LLM_ANCHOR),
        _section(2, (_val_band_table(val_band) + "\n\n" + _band_position_text(val_band))
                if val_band else _missing(today)),
        _section(3, _section4_body(staging_dir, code6, today)),
        _section(4, _LLM_ANCHOR),
        _section(5, _section6_body(staging_dir, code6, today)),
        _section(6, precedent_text if precedent_text else _NO_PRECEDENT),
        _section(7, f"- {today} 建档\n"),
    ]
    text = "\n".join(parts) + "\n"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return {"path": path, "created": True, "issues": schema.lint_dossier(text)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="dossier 确定性建档骨架(八节;零 LLM,幂等不覆盖)")
    ap.add_argument("code", help="6 位代码")
    ap.add_argument("today", help="YYYY-MM-DD")
    ap.add_argument("--name", default="", help="股票名称")
    ap.add_argument("--sector", default="", help="申万一级行业")
    ap.add_argument("--force", action="store_true", help="强制重建(覆盖已有档案)")
    args = ap.parse_args(argv)

    out = build_skeleton(args.code, args.today, name=args.name, sector=args.sector,
                         force=args.force)
    status = "created" if out["created"] else "skip(existing,非 --force 不覆盖)"
    tail = f"; issues={out['issues']}" if out["issues"] else ""
    print(f"[builder] {args.code} @ {args.today} -> {out['path']} ({status}){tail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
