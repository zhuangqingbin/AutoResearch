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

from autoresearch.data.contracts import check_market_frame


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


_VOL_MIN_DAYS = 10          # 少于 10 个交易日算不出 20 日 CMF/OBV —— 与原 `len(days) < 10` 同阈值


def _harvest_vol_series(codes, analysis_date: str, lookback: int = 20) -> pd.DataFrame:
    """拉近 ~lookback 交易日 daily(high/low/close/amount)→ vol_series 算多日量价因子 per code。

    供 L1 召回的 **volprice 组**(快照层本来无序列)。tushare bulk by date(~lookback 次)→ pivot。

    **失败即抛 `DataContractError`,不再静默返回空帧**(2026-07-12 用户裁定 + 事故复盘)。
    原实现三层吞异常 → 任何失败都退化成空帧 → cmf_20/obv_mom_20 **整列不落盘** →
    `composite_score` 把 volprice 组从分母剔除、放大其余组权重 → 打分照样输出 0–100、漏斗照样
    跑完、退出码 0。实测代价:全市场打分失真 **98.8%**、L2 名单 jaccard **0.36**,而唯一的信号
    是一行淹没在日志里的 warn(根因是 lake 的 daily 被窄表毒化,见 `cache._lake_params`)。

    volprice 是 A 级地基(10 组因子之一,且是唯一的多日序列组)——**它死了这次扫描就不该发布**。
    """
    from datetime import datetime, timedelta

    import autoresearch.common.vol_series as vol_series
    from autoresearch.data.cache import get_or_fetch  # 已结算日湖命中零网络(policy: daily=eod)
    from autoresearch.data.contracts import DataContractError
    from autoresearch.data.tushare_source import (
        _code6,
        _pro,
        _trade_days,
        _ts_call,
        resolve_momentum_dates,
    )

    try:
        pro = _pro()
        last = resolve_momentum_dates(pro, analysis_date)[0]
        start = (datetime.strptime(last, "%Y%m%d") - timedelta(days=lookback * 2 + 15)).strftime("%Y%m%d")
        days = _trade_days(pro, start, last)[-lookback:]
        want = {str(c).zfill(6) for c in codes}
        recs = []
        for d in days:
            try:
                df = get_or_fetch("daily", {"trade_date": d}, today=analysis_date)
            except DataContractError:
                raise                       # 契约违约必须炸穿:回退直拉只会掩盖湖里的毒源
            except Exception:  # noqa: BLE001 — 湖/policy 其它异常(如未登记端点)→ 回退直拉
                df = _ts_call(lambda d=d: pro.daily(trade_date=d, fields="ts_code,high,low,close,amount"))
            if df is None or not len(df):
                continue
            df = df.assign(code=_code6(df["ts_code"]), date=d)
            recs.append(df[df["code"].isin(want)][["code", "date", "high", "low", "close", "amount"]])
    except DataContractError:
        raise
    except Exception as e:  # noqa: BLE001 — 其余任何失败都升级为契约违约(不再静默降级)
        raise DataContractError(
            f"[数据契约·A级] volprice 组(多日量价序列)取数失败:{e!r}\n"
            f"  为什么阻断:cmf_20/obv_mom_20 整列缺失 → composite_score 会把 volprice 组从分母\n"
            f"           剔除、放大其余组权重 → 打分照样输出 0–100,**残废得看不出来**。\n"
            f"  怎么办:若是湖里的 daily 损坏 → `python -m autoresearch.data.contracts doctor --purge`") from e

    if len(recs) < _VOL_MIN_DAYS:
        raise DataContractError(
            f"[数据契约·A级] volprice 组只取到 {len(recs)} 个交易日(需 ≥{_VOL_MIN_DAYS})"
            f" —— 20 日 CMF/OBV 算不出来,拒绝用残缺序列打分。\n"
            f"  多半是 lake 里这段日期的 daily 缺失/损坏 → "
            f"`python -m autoresearch.data.contracts doctor --purge` 后重跑。")

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
        uni = uni.merge(vps, on="code", how="left")                # 失败已在上面抛 DataContractError
    # 出口契约(漏斗地基的最后一道门):喂给 composite_score 的帧到底全不全,与它从哪来无关。
    check_market_frame(uni, with_vol_series=vol_series)
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

    import contextlib

    # --json 时把整个构建段的 stdout 圈进 stderr:湖冷时取数层(tushare_source 等)会 print 进度行,
    # 只改本函数三行 info 挡不住(2026-07-09 market_pack 污染的完整根因)。JSON 是 stdout 唯一产出。
    with contextlib.redirect_stdout(sys.stderr) if args.json else contextlib.nullcontext():
        frame, counts = build_market_frame(analysis_date, cap_floor_yi=args.cap_floor,
                                           include_bj=not args.exclude_bj, source=args.source)
        # Wave5 ③A:资金面/指数估值取数落 `_macro_cn.json` —— 必须在 market_pack 之前跑,
        # pack 读的就是这份文件(prewarm 跑过则本次多半是湖/缓存命中)。失败只降级不阻断。
        try:
            from autoresearch.data.macro_cn import write_macro_cn
            mp = write_macro_cn(analysis_date)
            print(f"[macro_cn] {mp}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001 — 取数全挂也只让 pack 少两块,不毁整帧
            print(f"[warn] macro_cn 取数失败(pack 缺 cross_money/index_val): {e}", file=sys.stderr)
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
        from autoresearch.scan.artifacts import artifact_schema_versions
        from autoresearch.scan.run_contract import RunContract, write_run_contract
        from autoresearch.scan.user_config import load_pinned, load_user_config

        user_cfg = load_user_config()
        pinned_cfg = user_cfg.get("pinned") or {}
        pinned_cap = int(pinned_cfg.get("cap", 5))
        pinned_ttl = int(pinned_cfg.get("ttl_days", 10))
        l3_cfg = user_cfg.get("l3") or {}
        pinned = load_pinned(analysis_date, cap=pinned_cap, ttl_days=pinned_ttl)
        contract = RunContract.build(
            analysis_date=analysis_date,
            user_config=user_cfg,
            pinned=pinned,
            data_policy={
                "source": args.source,
                "cap_floor_yi": args.cap_floor,
                "include_bj": not args.exclude_bj,
            },
            stage_budgets={
                "l3_finalist_max": int(l3_cfg.get("finalist_max", 10)),
                "pinned_cap": pinned_cap,
                "pinned_ttl_days": pinned_ttl,
            },
            artifact_schema_versions=artifact_schema_versions(),
        )
        echo_dir = Path("context/scan") / analysis_date
        echo_dir.mkdir(parents=True, exist_ok=True)
        write_run_contract(echo_dir / "run_contract.json", contract)
        (echo_dir / "user_config_echo.json").write_text(
            json.dumps(user_cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        from autoresearch.scan.stage_result import safe_record_stage_result
        safe_record_stage_result(
            echo_dir,
            stage="frame",
            status="SUCCEEDED",
            artifacts=["run_contract"],
            metrics={
                "frame_rows": int(counts["after_gate_a"]),
                "l0_rows": int(counts["universe"]),
                "regime": reg.get("label"),
                "sentinel_level": level,
            },
            warnings=[],
            error=None,
        )
        print(json.dumps({
            **pack,
            "macro_state": mstate,
            "macro_state_note": mnote,
            "user_config": user_cfg,
            "run_contract": contract.short_ref(),
        }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
