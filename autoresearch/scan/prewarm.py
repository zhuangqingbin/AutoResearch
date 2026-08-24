#!/usr/bin/env python3
"""scan-market · 夜间预热(确定性,零 LLM)——把「点火→出报告」最贵的取数段挪到 19:30 后台。

design: docs/specs/2026-07-12-scan-speed-perimeter-design.md §P1。
- 解析最近**已结算**交易日(交易日历;今天是交易日且本地时间 ≥19:15 → 今天,否则上一交易日);
- 目标日 == 今天时设 LAKE_ASSUME_SETTLED=1(cache 层仅对 d==today 放行入湖,未来日恒拒;
  完整性守卫 = 既有契约层:get_or_fetch「拉取→check→原子写」,A 级空/残缺抛且拒写,湖零污染);
- build_market_frame 全市场取数入湖(daily×20 + 快照端点)→ L3 evidence 三端点预拉(P2a 已走湖)
  → temperature rollup → 热度快照(东财人气/雪球关注,Wave12 T3;B 级断采不挡预热,见
  `_hot_rank_snapshot`)→ 写 _prewarm.json(stage_timing「预热」行消费);
(2026-08-21 learning 层退役:原 `--with-calibrate` 旋钮 —— 夜跑自动重标定 `weights.json`
 —— 随 `learning.retro.recalibrate_and_log` 一并删除。权重现在只由显式的
 `python -m autoresearch.research.factor_lab calibrate` 改。)
幂等:湖已有该日数据 → 全程命中秒退。失败退出码非零、不阻断(晚间扫描回落现路径)。
  uv run --no-sync python -m autoresearch.scan.prewarm            # 自动选日
  uv run --no-sync python -m autoresearch.scan.prewarm 2026-07-10
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta

from autoresearch.common import workspace as ws
from pathlib import Path  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)

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


# 热度快照的三个分片(endpoint, params)。雪球两个 symbol 各占一个分区(`symbol` 在
# `cache._ENTITY_PARAM_KEYS` 里,原生兜底就给独立键):
#   最热门   → 「关注」= **累计**关注数(做环比要自己按日做差)
#   本周新增 → 同名「关注」列其实是 follow7d(7 日新增)= 任务书 Interfaces 想要的 follow_delta
# 两者都不可回填,今天不采同样永远没有。
_HOT_RANK_SOURCES = (
    ("eastmoney_hot_rank", {}),
    ("stock_hot_follow_xq", {}),
    ("stock_hot_follow_xq", {"symbol": "本周新增"}),
)


def _snapshot_date(now: datetime | None = None) -> str:
    """快照分区日 = **观测日(墙上时钟今天)**,不是预热的目标交易日。

    Wave12 复核 I1:两者在正常交易日 19:30 相等,但**节假日 launchd 触发 / 补跑 / 手工传日期**
    时不等 —— 那时 `date` 是上一个交易日,而快照接口给的永远是"此刻"的内容。按 `date` 分区
    就会把今天的观测写成过去某天的假历史(工作树里那个 08-09 03:03 写成 `all@20260807` 的
    分区正是这么来的),且事后不可甄别。cache 层的 `SnapshotDateError` 是同一件事的兜底。
    """
    return (now or datetime.now()).strftime("%Y-%m-%d")


def _hot_rank_snapshot(date: str) -> str:
    """热度快照(东财人气榜 + 雪球关注度 ×2;Wave12 T2/T3,design
    docs/research/2026-08-09-hot-rank-probe.md)——**B 级,断采只损失当日、不阻断预热**。

    快照型数据:今天不采,今天的历史就永远没有了(不像行情可以事后用 trade_date 回补)——
    这是它在计划里排最优先的唯一理由,所以夜间必须每晚真的采一次。

    逐源 try/except(不是 `_prewarm_evidence` 那种整段 `except: pass` 静默吞掉的旧模式):
    单源失败必须显式 `record_degradation`(降级记账,而不是只打一行没人看的 warn)——且不能
    让第一个源的异常拖累后面的源完全不被尝试("连续断采仅损失当日"这句话依赖的正是这里的
    逐源隔离,不是外层 `_step()` 的整步兜底)。

    **✓ 不等于拿到了完整的一份**(Wave12 复核 C2):雪球内部分 29 页,单页解析失败被 akshare
    自己 `except TypeError` 吞掉 → 限流时静默返回 3000 行而**不抛异常**。所以这里在取数之后
    还要再问一次契约(`violations`):半截/空一律记成 ✗,不然 note 会写着 `✓(3000行)`,
    prelude 也就永远不告警。契约层已在 `check()` 里记过账,这里只负责让它在 note 里可见。
    """
    from autoresearch.data import cache as _cache
    from autoresearch.data.contracts import record_degradation, violations
    snap = _snapshot_date()
    parts: list[str] = []
    for ep, params in _HOT_RANK_SOURCES:
        label = ep + (f"[{params['symbol']}]" if params.get("symbol") else "")
        try:
            df = _cache.get_or_fetch(ep, dict(params), today=snap)
            v = violations(ep, df)
            parts.append(f"{label}{'✗' if v else '✓'}({len(df)}行"
                         + (f":{'; '.join(v)[:60]}" if v else "") + ")")
        except Exception as e:  # noqa: BLE001 — B 级:单源断采不挡其余源、不挡预热
            record_degradation(ep, f"{type(e).__name__}: {e}", key=snap)
            parts.append(f"{label}✗({type(e).__name__})")
    return " · ".join(parts) + f" · 观测日 {snap.replace('-', '')}"


def run_prewarm(date: str | None = None, *, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    date = date or latest_settled_trade_date(now)
    scan_dir = ws.scan_root() / date
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
        _step("hot_rank_snapshot", _hot_rank_snapshot)
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
    args = ap.parse_args(argv)
    return 0 if run_prewarm(args.date)["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
