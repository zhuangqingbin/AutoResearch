"""确定性预取(spec ②):mainbz/fwd-EPS/估值带三腿,各自独立降级,写 `_prefetch/<code>.json`。

design: docs/specs/2026-07-22-research-depth-dossier-design.md ②。夜间 prewarm 只承担
**确定性预取**(不占扫描窗口);首覆 LLM workflow 消费 `_prefetch/<code6>.json` 省墙钟。

三腿:
  - mainbz:  Task 2 `mainbz_latest`(fina_mainbz 分业务,取数已自带 per-period suppress)。
  - fwd_eps: 同花顺一致预期 EPS —— 复用 `autoresearch.data.keyless` 的真取数/解析入口
             (`fetch_consensus_eps` 取原始 df + `fwd_eps` 逐年抽取),不重写抓取/解析逻辑。
             `consensus_eps_block` 耦合 markdown 渲染,不用它。
  - val_band:`daily_basic` 单票 3 年历史 pe_ttm/pb 分位。**直连 `sources.fetch`,不走
             `cache.get_or_fetch`/不入湖**——lake key 按 policy 是"日期"粒度(整市场当日快照),
             把单票多年历史塞进同一 key 会造成窄表污染(lake-narrow-fields-poisoning 教训);
             这是一次性单票拉取,湖零接触。

每腿失败/空 → 该键 `None`/`[]`,并在 `notes` 留一行降级说明(互不传染:一腿挂其余照跑)。
原子写:`<path>.tmp` → `os.replace`(同 cache.py 写湖的惯例)。
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
from datetime import datetime

import pandas as pd
from dateutil.relativedelta import relativedelta

from autoresearch.dossier import schema
from autoresearch.dossier.mainbz import mainbz_latest

PREFETCH_DIR = schema.DOSSIER_DIR / "_prefetch"


def _default_ths_fn(code6: str, today: str) -> dict:
    """同花顺一致预期 EPS 真身:`fetch_consensus_eps` 取原始 df +`fwd_eps` 逐年抽取。

    df 里出现的每个预测年份都尝试抽一次(kind=='YC');一个都抽不到 → `{"note": ...}` 降级
    (不是 None——留一句人可读的降级原因,供档案渲染/CLI 直接展示)。
    """
    from autoresearch.data.keyless import fetch_consensus_eps, fwd_eps

    df = fetch_consensus_eps(code6)
    if df is None or df.empty:
        return {"note": "同花顺一致预期不可用(空/降级)"}
    out: dict = {"asof": today}
    for year in sorted({str(y) for y in df["year"].astype(str).unique()}):
        v = fwd_eps(df, year)
        if v is not None:
            out[f"fwd_eps_{year}"] = v
    if len(out) <= 1:                      # 只有 asof,没抽到任何 fwd_eps_<year>
        return {"note": "同花顺仅有实际值、无前瞻一致预期"}
    return out


def _default_band_fn(code6: str, today: str) -> dict | None:
    """`daily_basic` 单票 3 年历史(直连 sources.fetch,不入湖)→ pe_ttm/pb 估值带分位+现值。"""
    from autoresearch.data.sources import fetch as _fetch
    from autoresearch.dataflows.symbol_utils import to_ts_code

    end = datetime.strptime(today, "%Y-%m-%d")
    start = end - relativedelta(years=3)
    df = _fetch("daily_basic", {"ts_code": to_ts_code(code6),
                                "start_date": start.strftime("%Y%m%d"),
                                "end_date": end.strftime("%Y%m%d")})
    if df is None or df.empty or not {"pe_ttm", "pb"}.issubset(df.columns):
        return None
    if "trade_date" in df.columns:
        df = df.sort_values("trade_date")
    out: dict = {}
    for col, prefix in (("pe_ttm", "pe"), ("pb", "pb")):
        s = pd.to_numeric(df[col], errors="coerce")
        if not s.notna().any():
            continue
        out[f"{prefix}_p25"] = float(s.quantile(0.25))
        out[f"{prefix}_p50"] = float(s.quantile(0.5))
        out[f"{prefix}_p75"] = float(s.quantile(0.75))
        last = s.iloc[-1]
        out[f"{prefix}_now"] = float(last) if pd.notna(last) else None
    return out or None


def prefetch_one(code6: str, today: str, *, fetch=None, ths_fn=None, band_fn=None) -> dict:
    """三腿各自降级取数 → 写 `_prefetch/<code6>.json` → 返回同一份 dict。"""
    code6 = str(code6).split(".")[0].zfill(6)
    notes: list[str] = []

    mainbz: list = []
    with contextlib.suppress(Exception):
        mainbz = mainbz_latest(code6, today, fetch=fetch)
    if not mainbz:
        notes.append("mainbz: 空(降级——fina_mainbz 该腿取数失败或确无分业务披露)")

    fwd_eps_out: dict | None = None
    with contextlib.suppress(Exception):
        fwd_eps_out = (ths_fn or _default_ths_fn)(code6, today)
    has_fwd = isinstance(fwd_eps_out, dict) and any(
        str(k).startswith("fwd_eps_") for k in fwd_eps_out)
    if not has_fwd:
        why = fwd_eps_out.get("note") if isinstance(fwd_eps_out, dict) else "该腿失败"
        notes.append(f"fwd_eps: 降级({why})")

    val_band: dict | None = None
    with contextlib.suppress(Exception):
        val_band = (band_fn or _default_band_fn)(code6, today)
    if not val_band:
        val_band = None
        notes.append("val_band: 空(降级——daily_basic 单票历史取数失败或缺 pe_ttm/pb)")

    out = {"code": code6, "asof": today, "mainbz": mainbz, "fwd_eps": fwd_eps_out,
           "val_band": val_band, "notes": notes}

    PREFETCH_DIR.mkdir(parents=True, exist_ok=True)
    p = PREFETCH_DIR / f"{code6}.json"
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    return out


def prefetch_pool(today: str, *, codes: list[str] | None = None) -> dict:
    """池内 active 票逐个 `prefetch_one`;单码失败不断链。返回 `{code6: ok_bool}`。"""
    if codes is None:
        from autoresearch.dossier.pool import load_pool
        pool = load_pool()
        codes = sorted(c for c, s in pool.get("stocks", {}).items()
                       if s.get("status") == "active")
    out: dict[str, bool] = {}
    for c in codes:
        try:
            prefetch_one(c, today)
            out[c] = True
        except Exception:
            out[c] = False
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="dossier 确定性预取(mainbz/fwd-EPS/估值带;零 LLM,prewarm 挂钩)")
    ap.add_argument("code", nargs="?", help="6 位代码(单码模式);--pool 模式下省略")
    ap.add_argument("today", nargs="?", default=None, help="YYYY-MM-DD;缺省=今天")
    ap.add_argument("--pool", action="store_true", help="预取整个覆盖池(coverage_pool active)")
    args = ap.parse_args(argv)
    today = args.today or datetime.now().strftime("%Y-%m-%d")

    if args.pool:
        if args.code and not args.today:      # 只给了一个位置参 → 视作 today,code 位留空
            today = args.code
        res = prefetch_pool(today)
        ok = sum(1 for v in res.values() if v)
        print(f"[prefetch] 池预取 {ok}/{len(res)}")
        return 0 if ok == len(res) else 1

    if not args.code:
        ap.error("需要 code 位置参(单码模式),或加 --pool 全池")
    prefetch_one(args.code, today)
    print(f"[prefetch] {args.code} @ {today}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
