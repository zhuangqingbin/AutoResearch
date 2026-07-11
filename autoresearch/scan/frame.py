#!/usr/bin/env python3
"""scan-market · 全市场因子帧(L0 取数 + 轻门 + 多日量价,零打分零召回)+ 盘前哨兵预告 CLI。

design: docs/specs/2026-07-03-research-skills-altitude-refactor-design.md §5.1(Phase 0)。

`build_market_frame` = `universe.run` 前半段的抽取(**单一代码路径**:run / L1Recall stage /
本 CLI 三处共用同一 `_harvest_vol_series`;测试 patch 锚点也统一在本模块)。产出的帧就是
`classify_regime` / `market_pack_from_frame` / `sentinel_advice_from_frame` / `healthy_riser_mask`
的输入 → 宏观 lite(Stage 0)与盘前 cron 不再依赖 universe 产物(L1_scored_full)。

用法(盘前预告,零 LLM):
  uv run --no-sync python -m autoresearch.scan.frame 2026-07-03            # regime + 哨兵预告
  uv run --no-sync python -m autoresearch.scan.frame 2026-07-03 --json     # 另打印 market_pack JSON
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd


def _recall_gate_a(df: pd.DataFrame, min_amount_yi: float = 0.0, min_list_days: int = 0) -> pd.Series:
    """L1 召回轻门:只去真正不可交易/无核心数据的尾部(召回优先,尽量不误杀)。

    `min_list_days`>0 且帧有 `list_days` 列 → 剔次新(上市<阈值日,量价/IC 因子无意义);缺列降级不剔。
    默认两门 =0 → 与改动前逐值一致(parity)。
    """
    keep = df["amount_yi"].fillna(0) > min_amount_yi       # 有流动性/非停牌
    keep &= df["close"].notna()                            # 有价
    keep &= df["pct_60d"].notna() | df["pct_ytd"].notna()  # 有动量价(打分核心)
    if min_list_days > 0 and "list_days" in df.columns:    # 次新过滤(有 list_days 才生效,缺则降级)
        keep &= pd.to_numeric(df["list_days"], errors="coerce").fillna(1e9) >= min_list_days
    return keep


def _harvest_vol_series(codes, analysis_date: str, lookback: int = 20) -> pd.DataFrame:
    """拉近 ~lookback 交易日 daily(high/low/close/amount)→ vol_series 算多日量价因子 per code。

    供 L1 召回的 volprice 组(快照层本来无序列)。tushare bulk by date(~lookback 次)→ pivot → 序列指标。
    无权限/失败 → 返回空帧(volprice 列缺失 → 组 NaN 重归一,recall 不破)。
    """
    try:
        from datetime import datetime, timedelta

        import autoresearch.common.vol_series as vol_series
        from autoresearch.data.tushare_source import (
            _code6,
            _pro,
            _trade_days,
            _ts_call,
            resolve_momentum_dates,
        )
        pro = _pro()
        last = resolve_momentum_dates(pro, analysis_date)[0]
        start = (datetime.strptime(last, "%Y%m%d") - timedelta(days=lookback * 2 + 15)).strftime("%Y%m%d")
        days = _trade_days(pro, start, last)[-lookback:]
        if len(days) < 10:
            return pd.DataFrame(columns=["code"])
        want = {str(c).zfill(6) for c in codes}
        recs = []
        from autoresearch.data.cache import get_or_fetch  # 已结算日湖命中零网络(policy: daily=eod)
        for d in days:
            try:
                df = get_or_fetch("daily", {"trade_date": d}, today=analysis_date)
            except Exception:  # noqa: BLE001 — 湖/policy 异常回退直拉
                df = _ts_call(lambda d=d: pro.daily(trade_date=d, fields="ts_code,high,low,close,amount"))
            if df is None or not len(df):
                continue
            df = df.assign(code=_code6(df["ts_code"]), date=d)
            recs.append(df[df["code"].isin(want)][["code", "date", "high", "low", "close", "amount"]])
        if not recs:
            return pd.DataFrame(columns=["code"])
        long = pd.concat(recs, ignore_index=True)
        piv = {f: long.pivot_table(index="code", columns="date", values=f)
               for f in ("high", "low", "close", "amount")}
        win = sorted(piv["close"].columns)
        H, L, C, A = (piv[f][win] for f in ("high", "low", "close", "amount"))
        out = pd.DataFrame({"code": list(C.index)})
        out["cmf_20"] = vol_series.cmf(H, L, C, A, win).to_numpy()
        out["obv_mom_20"] = vol_series.obv_momentum(C, A, win).to_numpy()
        out["price_vs_vwap_20"] = vol_series.price_vs_vwap(H, L, C, A, win).to_numpy()
        out["breakout_vol_20"] = vol_series.breakout_on_volume(C, A, win).to_numpy()
        return out
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 多日量价序列取数失败 → volprice 组置 NaN: {e}", file=sys.stderr)
        return pd.DataFrame(columns=["code"])


def build_market_frame(analysis_date: str, *, cap_floor_yi: float = 30.0, include_bj: bool = True,
                       source: str = "tushare", l0_min_amount_yi: float = 0.0,
                       l0_min_list_days: int = 0, vol_series: bool = True,
                       ) -> tuple[pd.DataFrame, dict]:
    """L0 取数 + L1 轻门 + 多日量价富化 → (全市场因子帧, 计数)。零打分零召回零 LLM。

    与 `universe.run` 前半段逐值一致(run 调本函数;golden parity 由 tests/scan/test_parity.py 锁)。
    counts:`universe_raw`(源头全量)/ `universe`(L0 硬门后)/ `after_gate_a`(轻门后=帧行数)。
    `vol_series=False` 跳过多日量价拉取(盘前只要 regime/哨兵、healthy 谓词缺 cmf 会降级时可省时)。
    """
    if source == "tushare":
        from autoresearch.data.tushare_source import (  # 默认源(东财 push2 常被封)
            _RAW_COUNT,
            fetch_universe_tushare,
        )
        uni = fetch_universe_tushare(analysis_date, cap_floor_yi=cap_floor_yi, include_bj=include_bj)
        n_raw = _RAW_COUNT.get("n", len(uni))
    else:
        from autoresearch.data.akshare_universe import _GATE_INFO, fetch_universe
        uni = fetch_universe(analysis_date, cap_floor_yi=cap_floor_yi, include_bj=include_bj)
        n_raw = _GATE_INFO.get("n_raw", len(uni))   # em 路径同模块,可靠
    n_l0 = len(uni)
    uni = uni[_recall_gate_a(uni, min_amount_yi=l0_min_amount_yi,
                             min_list_days=l0_min_list_days)].reset_index(drop=True)
    uni["code"] = uni["code"].astype(str).str.zfill(6)
    if vol_series:
        vps = _harvest_vol_series(uni["code"], analysis_date)      # 多日量价序列(CMF/OBV/...)→ volprice 组
        if len(vps):
            uni = uni.merge(vps, on="code", how="left")
    return uni, {"universe_raw": int(n_raw), "universe": n_l0, "after_gate_a": len(uni)}


# ───────────────────────── CLI:盘前哨兵预告(零 LLM) ─────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="盘前市场帧:regime + 哨兵预告(确定性,零 LLM,不依赖 scan staging)")
    ap.add_argument("date", nargs="?", help="分析日 YYYY-MM-DD(缺省=今天)")
    ap.add_argument("--cap-floor", type=float, default=30.0, help="市值地板(亿),默认 30(与 universe 同)")
    ap.add_argument("--exclude-bj", action="store_true", help="排除北交所(默认纳入)")
    ap.add_argument("--source", choices=["em", "tushare"], default="tushare")
    ap.add_argument("--json", action="store_true", help="另打印 market_pack JSON(宏观 lite / Stage 0 输入)")
    args = ap.parse_args(argv)
    analysis_date = args.date or date.today().isoformat()

    frame, counts = build_market_frame(analysis_date, cap_floor_yi=args.cap_floor,
                                       include_bj=not args.exclude_bj, source=args.source)
    from autoresearch.scan.market import market_pack_from_frame
    from autoresearch.scan.menu import sentinel_advice_from_frame
    pack = market_pack_from_frame(frame, date=analysis_date)
    level, reason = sentinel_advice_from_frame(frame)
    reg = pack.get("regime") or {}
    print(f"[frame] {analysis_date} 帧 {counts['after_gate_a']} 只(L0 {counts['universe']})｜"
          f"regime={reg.get('label', '—')} breadth={reg.get('breadth', '—')} "
          f"med_mom={reg.get('med_mom', '—')}", file=sys.stderr)
    print(f"[sentinel·盘前预告] {level} —— {reason}(正式判据以 scan 内 L1_scored_full 口径为准)",
          file=sys.stderr)
    from autoresearch.macro.state import load_macro_state  # Phase 2:宏观 lite 的输入捆绑
    mstate, mnote = load_macro_state(analysis_date, regime_today=reg.get("label"))
    print(f"[macro_state] {mnote}", file=sys.stderr)
    if args.json:
        from autoresearch.scan.user_config import load_user_config  # Plan A3 T1:用户配置层回显
        user_cfg = load_user_config()
        print(json.dumps({**pack, "macro_state": mstate, "macro_state_note": mnote,
                          "user_config": user_cfg},
                         ensure_ascii=False, indent=2))
        echo_dir = Path("context/scan") / analysis_date       # run meta:本次跑用的配置,可复现
        echo_dir.mkdir(parents=True, exist_ok=True)
        (echo_dir / "user_config_echo.json").write_text(
            json.dumps(user_cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
