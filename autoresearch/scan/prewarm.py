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
日期解析是**第一步**而不是入口前的裸调用(2026-08-29 T6,见 `run_prewarm` docstring):
交易日历瞬断 → 记一行失败账 + `_prewarm_failed.json`,不再抛栈把整晚带走;
`scripts/com.tradingagents.scan-prewarm.plist` 另配 21:00 二次尝试(退出码非零即有第二次机会)。
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
from pathlib import Path

from autoresearch.common import workspace as ws

_SETTLE_HHMM = 19 * 60 + 15    # 当日 EOD 视为已结算的最早本地时刻(19:15;spec §P1 依据)


def settle_minutes() -> int:
    """`scan_config.readiness.settle_hhmm`(HH:MM → 自当日 0 点起的分钟数;缺省 19:15)。"""
    from autoresearch.scan.user_config import knob
    raw = str(knob("readiness", "settle_hhmm", None, "19:15"))
    hh, mm = raw.split(":")
    return int(hh) * 60 + int(mm)
#: `_step()` 的失败哨兵。用独立对象而非 `None`:步骤函数返回 `None`(空 note)是合法成功。
_STEP_FAILED = object()


def latest_settled_trade_date(now: datetime | None = None) -> str:
    """最近已结算交易日(YYYY-MM-DD):今天是交易日且 now≥19:15 → 今天;否则上一交易日。"""
    from autoresearch.data.tushare_source import _pro, _trade_days
    now = now or datetime.now()
    days = _trade_days(_pro(), (now - timedelta(days=30)).strftime("%Y%m%d"), now.strftime("%Y%m%d"))
    if not days:
        raise RuntimeError("trade_cal 取不到交易日(token/网络?)")
    if days[-1] == now.strftime("%Y%m%d") and now.hour * 60 + now.minute < settle_minutes():
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
    """夜间预热主流程 —— **日期解析也是一步**,日历炸了不再把整晚带走。

    2026-08-29(计划 T6):`date = date or latest_settled_trade_date(now)` 原来写在 `_step()`
    **之外**,于是 `trade_cal` 一次 DNS 瞬断就让整个函数抛栈退出 —— 一步取数没跑、磁盘上一点
    证据没有。08-28 20:13 真死过(`NameResolutionError api.waditu.com` ×3),`lake/daily/20260828`
    因此干脆缺席;plist 那边当时也没有任何重试(本波一并补了 21:00 二次尝试)。现在:

      · 显式传 `date` → **完全不碰日历**(离线补跑可用);
      · 自动选日失败 → 记一行 `{"step": "resolve_date", "ok": False}`、返回 `{"ok": False}`
        而**不抛**,且**不再往下跑取数步**(没有日期,后面每一步都会拿 `None` 去取数);
      · 自动选日成功**不占账面一行** —— 日期就是记录里的 `date` 字段,而 `steps` 是取数账
        (`prelude._hot_rank_snapshot_warning` / `stage_timing` / 既有测试都按那五步读它)。

    落盘位置:有日期照旧 `scan_dir/_prewarm.json`;**无日期时 `scan_dir` 无处可建**,记录改落
    `ws.scan_root()/"_prewarm_failed.json"`(同一个 staging 根,不带日期分区)。汇总屏的
    `prewarm_line()` 读的仍是 `<date>/_prewarm.json`,那行照旧显示「预热(夜间):✗ 未跑」——
    这份 `_prewarm_failed.json` 是它旁边那句「为什么没跑」的素材,不必靠 /tmp 日志考古。
    """
    # 与 prelude 同理:新一轮取数之前先冻结上一次被打断的 run(只警告,不阻断)。
    from autoresearch.trace.capsule import recover_stale_runs_quietly

    recover_stale_runs_quietly()
    now = now or datetime.now()
    started = time.time()
    steps: list[dict] = []

    def _step(name: str, fn, *args, record_success: bool = True):
        """跑一步、记一行账;成功返回 `fn` 的返回值,失败返回 `_STEP_FAILED` 且**不抛**。

        `record_success=False` 只给 `resolve_date` 用:见上方 docstring(成功的解析不占账面)。
        """
        try:
            out = fn(*args)
        except Exception as e:  # noqa: BLE001 — 单步失败记录继续,末尾以 ok 汇总定退出码
            steps.append({"step": name, "ok": False, "note": f"{type(e).__name__}: {e}"})
            print(f"[prewarm] ✗ {name}: {e}", file=sys.stderr)
            return _STEP_FAILED
        if record_success:
            steps.append({"step": name, "ok": True, "note": str(out or "")})
        return out

    def _finish(target: str | None, record: Path) -> dict:
        record.parent.mkdir(parents=True, exist_ok=True)
        manifest = {
            "date": target,
            "started_at": started,
            "ended_at": time.time(),
            "steps": steps,
        }
        record.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        ok = bool(steps) and all(s["ok"] for s in steps)
        print(f"[prewarm] {target or '(日期未解析)'} {'✓' if ok else '✗'} · "
              + " · ".join(f"{s['step']}{'✓' if s['ok'] else '✗'} {s['note']}" for s in steps))
        from autoresearch.common.atomic import sha256_bytes
        from autoresearch.trace.operation_evidence import record_operation_evidence

        virtual_after = {
            f"prewarm/{target or 'unresolved'}/{step['step']}": sha256_bytes(
                json.dumps(step, ensure_ascii=False, sort_keys=True).encode("utf-8")
            )
            for step in steps
            if step["ok"]
        }
        effects = [
            {"kind": "VIRTUAL_LAKE_WRITE", "path": path, "sha256": digest}
            for path, digest in sorted(virtual_after.items())
        ]
        evidence = record_operation_evidence(
            "prewarm",
            parameters=manifest,
            inputs={
                "prewarm.lake.before": {},
                "prewarm.lake.after": virtual_after,
                "prewarm.manifest.source": record,
            },
            outputs={"prewarm.manifest": record},
            effects=effects,
            code_paths=[Path(__file__)],
            evidence_root=ws.scan_root() / "_operation_evidence",
            status="SUCCEEDED" if ok else "UNMEASURED",
        )
        return {
            "date": target,
            "ok": ok,
            "steps": steps,
            "operation_id": evidence["operation_id"],
        }

    if date is None:
        resolved = _step("resolve_date", latest_settled_trade_date, now, record_success=False)
        if resolved is _STEP_FAILED:
            return _finish(None, ws.scan_root() / "_prewarm_failed.json")
        date = str(resolved)

    scan_dir = ws.scan_root() / date
    scan_dir.mkdir(parents=True, exist_ok=True)
    set_env = date == now.strftime("%Y-%m-%d")
    if set_env:
        os.environ["LAKE_ASSUME_SETTLED"] = "1"

    try:
        _step("frame_lake", _frame_lake, date)
        _step("evidence_lake", _prewarm_evidence, date)
        _step("temperature", _temperature, date)
        _step("dossier_prefetch", _dossier_prefetch, date)
        _step("hot_rank_snapshot", _hot_rank_snapshot, date)
    finally:
        if set_env:
            os.environ.pop("LAKE_ASSUME_SETTLED", None)
    return _finish(date, scan_dir / "_prewarm.json")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="scan 夜间预热(确定性,零 LLM;launchd 19:30 或手动)")
    ap.add_argument("date", nargs="?", default=None, help="缺省=最近已结算交易日")
    args = ap.parse_args(argv)
    return 0 if run_prewarm(args.date)["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
