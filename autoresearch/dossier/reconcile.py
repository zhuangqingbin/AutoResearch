"""dossier 季度对账 CLI(spec ⑤-4;确定性,零 LLM)。

中报/年报披露后:实际业绩(express 业绩快报优先——披露最早字段全;forecast 业绩预告
兜底——只有区间)与档案 §2 一致预期快照对照,对账行写 §5 风险矩阵 + §8 变化项日志。
三情景归属/证伪点核对是 LLM 判断,留给下次 δ 卡内「档案对账」节;本 CLI 只落事实数。
取数直连 sources.fetch 不入湖(prefetch 估值带腿同款);两端点皆空 = 未披露 → skip 留痕。
短尺对账仍归 t1/retro,此处不重复(spec 非目标)。
"""
from __future__ import annotations

import argparse

from autoresearch.data.express_fields import express_yoy_pct  # 快报字段语义单一事实源
from autoresearch.dossier import delta, pool, schema


def _num(v) -> float | None:
    """NaN/None/非数 → None(NaN 穿 `or 默认值` 防线,Wave2 教训)。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _txt(v, default: str = "—") -> str:
    """字符串腿 NaN/None 守卫(数值腿归 _num;Wave2 NaN 教训的字符串版)。"""
    if v is None or (isinstance(v, float) and v != v):
        return default
    s = str(v).strip()
    return s if s and s.lower() != "nan" else default


def _fetch_actual(code6: str, period: str, *, fetch=None) -> dict | None:
    """express 优先,forecast 兜底;皆空 → None。返回 {"kind","ann_date","line"}。"""
    from autoresearch.data import sources
    from autoresearch.dataflows.symbol_utils import to_ts_code  # 单一事实源(92xxxx→.BJ)
    fetch = fetch or sources.fetch
    ts = to_ts_code(code6)
    try:
        df = fetch("express", {"ts_code": ts, "period": period})
    except Exception:  # noqa: BLE001 — 网络腿降级走 forecast
        df = None
    if df is not None and len(df):
        if "ann_date" in df.columns:
            df = df.sort_values("ann_date", ascending=False)
        r = df.iloc[0]
        parts = []
        np_v = _num(r.get("n_income"))
        if np_v is not None:
            parts.append(f"净利 {np_v / 1e8:.1f}亿")
        # yoy_net_profit = 去年同期净利润金额(元),非增长率——语义单一事实源在
        # data/express_fields(tushare_enrich 同源引用,2026-07-24 终审 C-1:同一误读
        # 曾有两份实现)。去年同期 <= 0(亏损或为零)时增速无意义,改报金额;
        # 去年同期字段本身缺失(None)则整段不渲染。
        base = _num(r.get("yoy_net_profit"))
        yoy = express_yoy_pct(np_v, base)
        if yoy is not None:
            parts.append(f"yoy {yoy:+.1f}%")
        elif base is not None and base <= 0:
            parts.append(f"去年同期 {base / 1e8:.1f}亿(增速不适用)")
        eps = _num(r.get("diluted_eps"))
        if eps is not None:
            parts.append(f"摊薄EPS {eps:.2f}")
        roe = _num(r.get("diluted_roe"))
        if roe is not None:
            parts.append(f"ROE {roe:.1f}%")
        return {"kind": "express", "ann_date": _txt(r.get("ann_date")),
                "line": "、".join(parts) if parts else "快报关键字段缺"}
    try:
        df = fetch("forecast", {"ts_code": ts, "period": period})
    except Exception:  # noqa: BLE001 — 两腿皆断按未披露处理(skip 留痕在调用方)
        df = None
    if df is not None and len(df):
        if "ann_date" in df.columns:
            df = df.sort_values("ann_date", ascending=False)
        r = df.iloc[0]
        lo, hi = _num(r.get("p_change_min")), _num(r.get("p_change_max"))
        line = (f"预告净利变动 {lo:+.0f}%~{hi:+.0f}%" if lo is not None and hi is not None
                else f"预告类型 {_txt(r.get('type'))}")
        return {"kind": "forecast", "ann_date": _txt(r.get("ann_date")), "line": line}
    return None


def reconcile_one(code6: str, period: str, today: str, *, fetch=None) -> dict:
    """单票对账;presence-gated(无档案/未首覆 skip),同 period 幂等。"""
    code6 = str(code6).split(".")[0].zfill(6)
    path = schema.dossier_path(code6)
    if not path.exists():
        return {"code": code6, "skipped": "no_dossier"}
    text = path.read_text(encoding="utf-8")
    if not schema.parse_frontmatter(text).get("initiated"):
        return {"code": code6, "skipped": "not_initiated"}
    actual = _fetch_actual(code6, period, fetch=fetch)
    if actual is None:
        return {"code": code6, "skipped": "undisclosed"}
    mark = f"季度对账 {period}"
    body5 = delta.section_body(text, 4)
    if mark not in body5:
        line5 = (f"- **{mark}**({today} 记,{actual['kind']} {actual['ann_date']}):"
                 f"{actual['line']};fwd-EPS 快照见 §2,三情景归属与证伪点核对由"
                 "下次 δ 卡内「档案对账」节裁决")
        text = delta.replace_section(text, 4, body5.rstrip("\n") + "\n" + line5 + "\n")
    text = delta.append_delta_line(text, today,
                                   f"{mark}:{actual['line']}({actual['kind']})", key=mark)
    text = delta.set_frontmatter_key(text, "last_delta", today)
    path.write_text(text, encoding="utf-8")
    return {"code": code6, "updated": True, "kind": actual["kind"],
            "issues": schema.lint_dossier(text)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="dossier 季度对账(express/forecast vs §2 快照;确定性)")
    ap.add_argument("period", help="报告期 YYYYMMDD,如 20260630")
    ap.add_argument("--code", default=None, help="单票;缺省 = 全池 active")
    ap.add_argument("--today", default=None, help="记账日 YYYY-MM-DD,缺省=今天")
    args = ap.parse_args(argv)
    from datetime import datetime
    today = args.today or datetime.now().strftime("%Y-%m-%d")
    codes = [args.code] if args.code else sorted(
        c for c, e in pool.load_pool()["stocks"].items() if e.get("status") == "active")
    n = 0
    for c in codes:
        res = reconcile_one(c, args.period, today)
        tag = "✓" if res.get("updated") else f"skip({res.get('skipped')})"
        issues = f" issues={res['issues']}" if res.get("issues") else ""
        print(f"[reconcile] {c} {args.period}: {tag}{issues}")
        n += bool(res.get("updated"))
    print(f"[reconcile] 更新 {n}/{len(codes)} 份档案")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
