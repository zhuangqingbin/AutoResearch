"""dossier 季度对账 CLI(spec ⑤-4;确定性,零 LLM)。

中报/年报披露后:实际业绩(express 业绩快报优先——披露最早字段全;forecast 业绩预告
兜底——只有区间)与档案 §2 一致预期快照对照,对账行写 §5 风险矩阵 + §8 变化项日志。
三情景归属/证伪点核对是 LLM 判断,留给下次 δ 卡内「档案对账」节;本 CLI 只落事实数。
取数直连 sources.fetch 不入湖(prefetch 估值带腿同款);两端点皆空 = 未披露 → **也落痕**
(R2-I-1,2026-07-24 终审复核:此前只 `skip` 不写档案,消费者 `dossier_reconcile_nag`
分不清"没跑过"与"跑了、真没数据",导致该票永久被催办——同一波刚立的 I-4「降级留痕」
原则在隔壁又破了一次)。§5 同 period 只保留一行:真数据优先(不被未披露降级),未披露
可被真数据升级替换,见 `_upsert_period_line`。
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


UNDISCLOSED_TAG = "未披露"    # §5 行内识别标记(真实业绩数据行不会自然出现这三个字)


def _upsert_period_line(body5: str, mark: str, new_line: str, *, real: bool) -> str:
    """§5 同 `mark`(如 "季度对账 20251231")只保留一行;按 `- **{mark}**` 行首前缀定位。

    三条规则(R2-I-1):
      - 无既有行 → 直接追加;
      - 既有行是「未披露」、新行是真数据 → **整行替换**(升级,未披露痕迹让位给真数据);
      - 既有行已是真数据、新行是「未披露」(`real=False`)→ **原样不动**(真数据优先,不降级);
      - 其余情形(状态相同的重跑)→ 整行替换为最新一次记账(today/措辞可能变,幂等不重复)。
    """
    prefix = f"- **{mark}**"
    lines = body5.splitlines()
    idx = next((i for i, ln in enumerate(lines) if ln.startswith(prefix)), None)
    if idx is None:
        tail = body5.rstrip("\n")
        return (tail + "\n" if tail else "") + new_line + "\n"
    if not real and UNDISCLOSED_TAG not in lines[idx]:
        return body5      # 已有真数据,未披露不得覆盖(真数据优先,不降级)
    lines[idx] = new_line
    return "\n".join(lines) + "\n"


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
    """单票对账;presence-gated(无档案/未首覆 skip)。

    两端点皆空(未披露)**也落痕**(R2-I-1):§5 写一行可识别的「未披露」记账、§8 同款
    留痕、`last_delta` 照更新 —— 让 `dossier_reconcile_nag` 分得清"没跑过"与
    "跑了、真没数据",不再对同一只票永久重复提醒。返回值仍带 `skipped="undisclosed"`
    语义,额外加 `recorded=True` 表明已落痕(供 CLI 区分打印)。

    真数据分支额外写 `last_refresh`(Task 3:季度对账 = 报告期全量核对);未披露分支
    **不写** `last_refresh` —— 没核到数不算全量刷新,`staleness_issues` 陈旧计龄据此判断。
    """
    code6 = str(code6).split(".")[0].zfill(6)
    path = schema.dossier_path(code6)
    if not path.exists():
        return {"code": code6, "skipped": "no_dossier"}
    text = path.read_text(encoding="utf-8")
    if not schema.parse_frontmatter(text).get("initiated"):
        return {"code": code6, "skipped": "not_initiated"}
    mark = f"季度对账 {period}"
    actual = _fetch_actual(code6, period, fetch=fetch)
    body5 = delta.section_body(text, 4)
    if actual is None:
        line5 = f"- **{mark}**({today} 查):两端点均无数据,{UNDISCLOSED_TAG}"
        new_body5 = _upsert_period_line(body5, mark, line5, real=False)
        if new_body5 != body5:
            text = delta.replace_section(text, 4, new_body5)
        text = delta.append_delta_line(text, today, f"{mark}:两端点均无数据,{UNDISCLOSED_TAG}",
                                       key=mark)
        text = delta.set_frontmatter_key(text, "last_delta", today)
        path.write_text(text, encoding="utf-8")
        return {"code": code6, "skipped": "undisclosed", "recorded": True,
                "issues": schema.lint_dossier(text)}
    line5 = (f"- **{mark}**({today} 记,{actual['kind']} {actual['ann_date']}):"
             f"{actual['line']};fwd-EPS 快照见 §2,三情景归属与证伪点核对由"
             "下次 δ 卡内「档案对账」节裁决")
    new_body5 = _upsert_period_line(body5, mark, line5, real=True)
    if new_body5 != body5:
        text = delta.replace_section(text, 4, new_body5)
    text = delta.append_delta_line(text, today,
                                   f"{mark}:{actual['line']}({actual['kind']})", key=mark)
    text = delta.set_frontmatter_key(text, "last_delta", today)
    text = delta.set_frontmatter_key(text, "last_refresh", today)   # 对账=报告期全量核对
    path.write_text(text, encoding="utf-8")
    return {"code": code6, "updated": True, "kind": actual["kind"],
            "issues": schema.lint_dossier(text)}


def _valid_date(s: str) -> bool:
    """YYYY-MM-DD 格式校验(`main` 的 `--today` 源头校验)。

    I-1(2026-07-24 终审):此前零校验,`--today` help 文案写着 YYYY-MM-DD 但从不检查
    ——一次手误(如漏横杠 `20260830`)就把畸形日期写进档案,`staleness_issues` 此后
    1.5 年都读不出陈旧、`lint_dossier` 对日期又零校验,两边都不报 = 降级不留痕。
    堵在写入前比事后探测更彻底。
    """
    from datetime import date as _date
    try:
        y, m, d = (int(x) for x in str(s).split("-"))
        _date(y, m, d)
        return True
    except Exception:  # noqa: BLE001 — 任何解析失败都算不合法
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="dossier 季度对账(express/forecast vs §2 快照;确定性)")
    ap.add_argument("period", help="报告期 YYYYMMDD,如 20260630")
    ap.add_argument("--code", default=None, help="单票;缺省 = 全池 active")
    ap.add_argument("--today", default=None, help="记账日 YYYY-MM-DD,缺省=今天")
    args = ap.parse_args(argv)
    from datetime import datetime
    today = args.today or datetime.now().strftime("%Y-%m-%d")
    if not _valid_date(today):
        ap.error(f"--today 格式非法(需 YYYY-MM-DD):{today!r}")
    codes = [args.code] if args.code else sorted(
        c for c, e in pool.load_pool()["stocks"].items() if e.get("status") == "active")
    n = 0
    for c in codes:
        res = reconcile_one(c, args.period, today)
        if res.get("updated"):
            tag = "✓"
        elif res.get("recorded"):
            tag = f"skip({res.get('skipped')}·已留痕)"
        else:
            tag = f"skip({res.get('skipped')})"
        issues = f" issues={res['issues']}" if res.get("issues") else ""
        print(f"[reconcile] {c} {args.period}: {tag}{issues}")
        n += bool(res.get("updated"))
    print(f"[reconcile] 更新 {n}/{len(codes)} 份档案")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
