#!/usr/bin/env python3
"""A股宏观/资金面结构化取数(tushare)—— scan 与 macro 共用的单一事实源。

design: docs/specs/2026-07-25-scan-wave5-live-mainruler-macro-metering-design.md §③A

**为什么单独一层**:这四个端点(北向汇总 / 两融 / 行业资金 / 指数估值分位)此前只活在
`autoresearch/macro/tushare_macro.py` 里,而且那边的函数**直接返回 markdown 字符串** ——
scan 的 `market_pack` 想用其中的数字就只能重新实现一遍(平行实现 = 下一个口径分叉)。
这里抽出**返回 dict 的取数函数**,markdown 渲染留在 macro 侧作薄壳。

**为什么落 JSON 而不是在 pack 里直接调**:`market_pack_from_frame` 是帧入口、被测试与
盘前 cron 频繁调用,往里塞网络请求会让它变慢且不可测。照 `_temperature_block` 的既有
模式:本模块负责取数落 `context/scan/<date>/_macro_cn.json`,pack 只读文件(presence-gated)。

**降级契约(B 级)**:任一端点失败 → 该块 None **且** 在 `_degraded` 里记一条(含端点名与
错误摘要)。降级可以发生,降级不留痕不行。

  uv run --no-sync python -m autoresearch.data.macro_cn 2026-07-25
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

_INDEXES = {"000300.SH": "沪深300", "399006.SZ": "创业板指", "000905.SH": "中证500",
            "000001.SH": "上证指数"}


def _num(s):
    return pd.to_numeric(s, errors="coerce")


def _back(yyyymmdd: str, days: int) -> str:
    return (datetime.strptime(yyyymmdd, "%Y%m%d") - timedelta(days=days)).strftime("%Y%m%d")


def _pctile(hist: pd.Series, cur: float) -> float:
    h = _num(hist).dropna()
    return float((h < cur).mean() * 100) if len(h) else float("nan")


def _f(v) -> float | None:
    """NaN/None → None(JSON 里 NaN 是非法字面量,写进去整份文件就废了)。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else round(f, 4)


# ───────────────────────── 结构化取数(单一事实源) ─────────────────────────


def northbound_data(pro, last: str) -> dict:
    """北向/南向官方汇总(moneyflow_hsgt);north_money 万元 → /1e4 = 亿。"""
    from autoresearch.data.tushare_source import _ts_call
    df = _ts_call(lambda: pro.moneyflow_hsgt(start_date=_back(last, 16), end_date=last))
    df = df.sort_values("trade_date").tail(6)
    nm = _num(df["north_money"]) / 1e4
    sm = _num(df["south_money"]) / 1e4
    return {"latest_yi": _f(nm.iloc[-1]), "cum5_yi": _f(nm.tail(5).sum()),
            "south_latest_yi": _f(sm.iloc[-1]),
            "rows": [{"date": str(d), "north_yi": _f(n), "south_yi": _f(s)}
                     for d, n, s in zip(df["trade_date"], nm, sm, strict=True)]}


def margin_data(pro, last: str) -> dict:
    """两融融资余额(margin,沪深合计 rzye);元 → /1e8 = 亿。融资余额↑=加杠杆/risk-on。"""
    from autoresearch.data.tushare_source import _ts_call
    frames = []
    for ex in ("SSE", "SZSE"):
        try:
            d = _ts_call(lambda ex=ex: pro.margin(
                start_date=_back(last, 16), end_date=last, exchange_id=ex))
            frames.append(d[["trade_date", "rzye", "rqye"]])
        except Exception:  # noqa: BLE001 — 单所失败由下方"两所齐全"判据兜住
            pass
    if not frames:
        raise RuntimeError("margin 两交易所均空")
    # 只保留两所都已披露的日期(最新日常只有 SSE 先出 → 否则合计虚降一半)
    g = (pd.concat(frames).groupby("trade_date")
         .agg(rzye=("rzye", "sum"), n=("rzye", "size")).reset_index())
    m = g[g["n"] >= 2].sort_values("trade_date").tail(6)
    if m.empty:
        raise RuntimeError("margin 无两所齐全的交易日")
    rzye = _num(m["rzye"]) / 1e8
    latest = rzye.iloc[-1]
    prev = rzye.iloc[-2] if len(rzye) > 1 else latest
    return {"rzye_yi": _f(latest), "d1_yi": _f(latest - prev),
            "d5_yi": _f(latest - rzye.iloc[0]),
            "as_of": str(m["trade_date"].iloc[-1])}


def sector_flow_data(pro, last: str, topn: int = 8) -> dict:
    """行业资金净流入(moneyflow_ind_ths,net_amount 已=亿)。"""
    from autoresearch.data.tushare_source import _ts_call
    df = _ts_call(lambda: pro.moneyflow_ind_ths(trade_date=last))
    df = (df.assign(net=_num(df["net_amount"])).dropna(subset=["net"])
          .sort_values("net", ascending=False))

    def _rows(sub):
        return [{"industry": str(r["industry"]), "net_yi": _f(r["net"]),
                 "lead": str(r.get("lead_stock", ""))} for _, r in sub.iterrows()]

    return {"top": _rows(df.head(topn)), "bottom": _rows(df.tail(5).iloc[::-1]),
            "n": int(len(df)), "n_pos": int((df["net"] > 0).sum())}


def index_val_data(pro, last: str) -> dict:
    """指数估值(index_dailybasic:PE_ttm/PB + 近1年分位)。逐指数独立失败,不连坐。"""
    from autoresearch.data.tushare_source import _ts_call
    out: dict[str, dict] = {}
    for code, name in _INDEXES.items():
        try:
            d = _ts_call(lambda code=code: pro.index_dailybasic(
                ts_code=code, start_date=_back(last, 400), end_date=last,
                fields="trade_date,pe_ttm,pb"))
            d = d.sort_values("trade_date")
            pe = _num(d["pe_ttm"]).iloc[-1]
            out[code] = {"name": name, "pe_ttm": _f(pe), "pb": _f(_num(d["pb"]).iloc[-1]),
                         "pe_pct_1y": _f(_pctile(_num(d["pe_ttm"]), pe))}
        except Exception as e:  # noqa: BLE001 — 单指数失败只缺该行
            out[code] = {"name": name, "pe_ttm": None, "pb": None, "pe_pct_1y": None,
                         "error": f"{type(e).__name__}: {e}"[:120]}
    return out


# ───────────────────────── 编排 + 落盘 ─────────────────────────

_BLOCKS = (("northbound", northbound_data), ("margin", margin_data),
           ("sector_flow", sector_flow_data), ("index_val", index_val_data))


def fetch_macro_cn(date: str, pro=None) -> dict:
    """四块结构化取数 → dict;逐块失败 → 该块 None + `_degraded` 记一条(B 级降级留痕)。"""
    from autoresearch.data.tushare_source import _pro, resolve_momentum_dates
    out: dict = {"date": date, "as_of": None, "_degraded": []}
    try:
        pro = pro or _pro()
    except Exception as e:  # noqa: BLE001 — 无 token = 整块缺,照样留痕
        out["_degraded"].append({"block": "*", "why": f"tushare 不可用:{type(e).__name__}: {e}"[:160]})
        out.update(dict.fromkeys([n for n, _ in _BLOCKS]))
        return out
    last = resolve_momentum_dates(pro, date)[0]
    out["as_of"] = last
    for name, fn in _BLOCKS:
        try:
            out[name] = fn(pro, last)
        except Exception as e:  # noqa: BLE001
            out[name] = None
            out["_degraded"].append({"block": name, "why": f"{type(e).__name__}: {e}"[:160]})
    return out


def write_macro_cn(date: str, root: Path | str | None = None, pro=None) -> Path:
    """取数落 `<root>/<date>/_macro_cn.json`(pack 的输入);返回路径。"""
    det = Path(root or "context/scan") / date
    det.mkdir(parents=True, exist_ok=True)
    p = det / "_macro_cn.json"
    p.write_text(json.dumps(fetch_macro_cn(date, pro=pro), ensure_ascii=False, indent=1),
                 encoding="utf-8")
    return p


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="A股宏观/资金面结构化取数(tushare,零 LLM)")
    ap.add_argument("date", help="scan 日 YYYY-MM-DD")
    ap.add_argument("--root", default=None, help="scan staging 根(默认 context/scan)")
    a = ap.parse_args(argv)
    p = write_macro_cn(a.date, root=a.root)
    data = json.loads(p.read_text(encoding="utf-8"))
    deg = data.get("_degraded") or []
    print(f"[macro_cn] {p}(as_of {data.get('as_of')} · 降级 {len(deg)} 块"
          + (":" + "、".join(d["block"] for d in deg) if deg else "") + ")")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
