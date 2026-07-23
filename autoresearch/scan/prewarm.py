#!/usr/bin/env python3
"""scan-market · 夜间预热(确定性,零 LLM)——把「点火→出报告」最贵的取数段挪到 19:30 后台。

design: docs/specs/2026-07-12-scan-speed-perimeter-design.md §P1。
- 解析最近**已结算**交易日(交易日历;今天是交易日且本地时间 ≥19:15 → 今天,否则上一交易日);
- 目标日 == 今天时设 LAKE_ASSUME_SETTLED=1(cache 层仅对 d==today 放行入湖,未来日恒拒;
  完整性守卫 = 既有契约层:get_or_fetch「拉取→check→原子写」,A 级空/残缺抛且拒写,湖零污染);
- build_market_frame 全市场取数入湖(daily×20 + 快照端点)→ L3 evidence 三端点预拉(P2a 已走湖)
  → temperature rollup → 写 _prewarm.json(stage_timing「预热」行消费);
- calibrate **默认不跑**:夜跑自动 recalibrate 会在不扫描的日子也改 weights + 记 changelog,
  污染 DSR-lite trial 计数(P0-6)——`--with-calibrate` 手动旋钮。
幂等:湖已有该日数据 → 全程命中秒退。失败退出码非零、不阻断(晚间扫描回落现路径)。
  uv run --no-sync python -m autoresearch.scan.prewarm            # 自动选日
  uv run --no-sync python -m autoresearch.scan.prewarm 2026-07-10 --with-calibrate
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

_SETTLE_HHMM = 19 * 60 + 15    # 当日 EOD 视为已结算的最早本地时刻(19:15;spec §P1 依据)


def latest_settled_trade_date(now: datetime | None = None) -> str:
    """最近已结算交易日(YYYY-MM-DD):今天是交易日且 now≥19:15 → 今天;否则上一交易日。"""
    from autoresearch.data.tushare_source import _pro, _trade_days
    now = now or datetime.now()
    days = _trade_days(_pro(), (now - timedelta(days=30)).strftime("%Y%m%d"), now.strftime("%Y%m%d"))
    if not days:
        raise RuntimeError("trade_cal 取不到交易日(token/网络?)")
    if days[-1] == now.strftime("%Y%m%d") and now.hour * 60 + now.minute < _SETTLE_HHMM:
        days = days[:-1]
    if not days:
        raise RuntimeError("近 30 天无已结算交易日")
    d = days[-1]
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


def _frame_lake(date: str) -> str:
    from autoresearch.scan.frame import build_market_frame
    _, counts = build_market_frame(date)
    return f"帧 {counts['after_gate_a']} 只(L0 {counts['universe']})已入湖"


def _prewarm_evidence(date: str) -> str:
    """L3 evidence 三端点按日预拉(B 级:空=真实空,单端点失败不挡预热)。"""
    from autoresearch.data import cache as _cache
    from autoresearch.data.tushare_source import _pro, _trade_days, resolve_momentum_dates
    pro = _pro()
    last = resolve_momentum_dates(pro, date)[0]
    start = (datetime.strptime(last, "%Y%m%d") - timedelta(days=30)).strftime("%Y%m%d")
    n = 0
    for ep, params in ([("top_list", {"trade_date": last})]
                       + [(e, {"ann_date": dd}) for dd in _trade_days(pro, start, last)[-10:]
                          for e in ("forecast", "express")]):
        try:
            _cache.get_or_fetch(ep, params, today=date)
            n += 1
        except Exception:  # noqa: BLE001 — B 级增强,单端点失败不挡
            pass
    return f"{n} 次端点预拉"


def _temperature(date: str) -> str:
    from autoresearch.scan.temperature import rollup
    out = rollup(date, date)
    return f"{len(out)} 行" if len(out) else "无新增"


def _dossier_prefetch(date: str) -> str:
    """覆盖池确定性预取(mainbz/fwd-EPS/估值带;presence-gated——空池即秒退,零网络)。"""
    from autoresearch.dossier.prefetch import prefetch_pool
    r = prefetch_pool(date)
    return f"池预取 {sum(1 for v in r.values() if v)}/{len(r)}"


def run_prewarm(date: str | None = None, *, with_calibrate: bool = False,
                now: datetime | None = None) -> dict:
    now = now or datetime.now()
    date = date or latest_settled_trade_date(now)
    scan_dir = Path("context/scan") / date
    scan_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    steps: list[dict] = []
    set_env = date == now.strftime("%Y-%m-%d")
    if set_env:
        os.environ["LAKE_ASSUME_SETTLED"] = "1"

    def _step(name: str, fn) -> None:
        try:
            steps.append({"step": name, "ok": True, "note": str(fn(date) or "")})
        except Exception as e:  # noqa: BLE001 — 单步失败记录继续,末尾以 ok 汇总定退出码
            steps.append({"step": name, "ok": False, "note": f"{type(e).__name__}: {e}"})
            print(f"[prewarm] ✗ {name}: {e}", file=sys.stderr)

    try:
        _step("frame_lake", _frame_lake)
        _step("evidence_lake", _prewarm_evidence)
        _step("temperature", _temperature)
        _step("dossier_prefetch", _dossier_prefetch)
        if with_calibrate:
            def _calib(d):
                from autoresearch.learning.retro import recalibrate_and_log
                return f"weights 重标定:{str(recalibrate_and_log(d))[:80]}"
            _step("calibrate", _calib)
    finally:
        if set_env:
            os.environ.pop("LAKE_ASSUME_SETTLED", None)
    (scan_dir / "_prewarm.json").write_text(json.dumps(
        {"date": date, "started_at": started, "ended_at": time.time(), "steps": steps},
        ensure_ascii=False, indent=1), encoding="utf-8")
    ok = all(s["ok"] for s in steps)
    print(f"[prewarm] {date} {'✓' if ok else '✗'} · "
          + " · ".join(f"{s['step']}{'✓' if s['ok'] else '✗'} {s['note']}" for s in steps))
    return {"date": date, "ok": ok, "steps": steps}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="scan 夜间预热(确定性,零 LLM;launchd 19:30 或手动)")
    ap.add_argument("date", nargs="?", default=None, help="缺省=最近已结算交易日")
    ap.add_argument("--with-calibrate", action="store_true",
                    help="附带 recalibrate_and_log(默认关:防污染 changelog/DSR 计数)")
    args = ap.parse_args(argv)
    return 0 if run_prewarm(args.date, with_calibrate=args.with_calibrate)["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
