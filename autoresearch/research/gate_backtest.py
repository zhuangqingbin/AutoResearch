#!/usr/bin/env python3
"""gate_backtest —— L3.5 可插拔闸历史重放回测 CLI。零 LLM。

design: docs/specs/2026-07-11-recall-gate-pinned-config-design.md §3。plan: docs/plans/2026-07-11-l35-gate-backtest-plan.md Task 2。

动机:闸(L3.5,`autoresearch.scan.l35_gate` registry)决定 L3 判断 → L4 出卡的入选集,但"闸对不对"
不能拍脑袋定案。本 harness 重放历史各日 `L3_judged_full.csv`(L3 精排全量判断)× `retro/attribution.csv`
的 `fwd_2_oc`(超短主尺,2026-07-10 用户裁定,已实现收益),对每个候选 gate(+ params 网格)输出:
入选集 mean_fwd2/hit/n、**落选赢家清单**(cut 且 fwd_2_oc > win_thr,默认 +3% = 错杀审计)、分 regime
小节。机制镜像 `autoresearch.research.l2_eval.forward_compare`(同一 panel 上比较路径的前向收益)。
现有 ≥14 日历史即刻可跑;以后每次 retro 自动多一天样本 = "迭代出好闸"的数据路径。

registry 隔离(与 Plan A2 Task 1 并行开发,零文件冲突):本模块的核心重放函数(`replay_day`/
`run_backtest`)只吃**注入的** `gate_fn`(签名同 l35_gate 契约:
`gate_fn(judged, *, regime, exempt, params) -> GateResult`,GateResult 鸭子类型 —— dict
`{picked, cut}` 或带 `.picked`/`.cut` 属性的对象均可),不 import l35_gate。只有 CLI 路径
(`main`)需要"按名字取 gate"时才 `from autoresearch.scan.l35_gate import build, registered_gates`;
该 import 若失败(registry 未就位/并行开发中)优雅降级为 `_build_gate=None` + 空 registry,`main`
提示后 `return 1`,不崩、不 import 副作用。registry 落地后本 CLI 自动可用,无需改动。

用法:
  uv run --no-sync python -m autoresearch.research.gate_backtest \\
      --gate conviction_floor_quota --params-json '{"floor": 60}'
  uv run --no-sync python -m autoresearch.research.gate_backtest --dates 2026-07-02,2026-07-08
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import json
import sys
from pathlib import Path

import pandas as pd

try:
    from autoresearch.scan.l35_gate import build as _build_gate, registered_gates
except ImportError:                           # registry 未就位(并行开发中)—— CLI 优雅降级,不崩
    _build_gate = None

    def registered_gates() -> list[str]:
        return []

_RET_MAIN = "fwd_2_oc"        # 超短主尺:D+1开→D+2收(2026-07-10 用户裁定持仓 1~2 日)
_DEFAULT_WIN_THR = 0.03       # 落选赢家审计阈值默认 +3%(spec §3)


# ───────────────────────── 纯函数核(无 IO,可离线自测) ─────────────────────────


def _code6(df: pd.DataFrame, col: str = "code") -> pd.DataFrame:
    df = df.copy()
    df[col] = df[col].astype(str).str.zfill(6)
    return df


def _topn_metrics(fwd: pd.Series) -> dict:
    """已选子集的前向收益 → {mean_fwd2, hit(>0 占比), n}。空 → NaN/0。纯函数,镜像 l2_eval._topn_metrics。"""
    fwd = pd.to_numeric(fwd, errors="coerce").dropna()
    if not len(fwd):
        return {"mean_fwd2": float("nan"), "hit": float("nan"), "n": 0}
    return {"mean_fwd2": round(float(fwd.mean()), 5), "hit": round(float((fwd > 0).mean()), 4), "n": int(len(fwd))}


def _unpack_gate_result(result) -> tuple[list[str], list[dict]]:
    """GateResult 鸭子类型解包:dict({picked,cut}) 或带 .picked/.cut 属性的对象均可。

    与 Plan A2 Task 1 的具体落地(dataclass/dict)解耦——两个 agent 零文件冲突的关键。
    """
    if isinstance(result, dict):
        picked, cut = result.get("picked", []), result.get("cut", [])
    else:
        picked, cut = getattr(result, "picked", []), getattr(result, "cut", [])
    return list(picked), list(cut)


def resolve_regime(scan_dir: Path | str | None, judged: pd.DataFrame) -> str:
    """单日 regime 标签:`meta.json.regime` 优先(与线上 scan 同一标签,可复现);缺失 → `classify_regime`
    现算(镜像 `autoresearch.scan.menu.sentinel_advice` 的 meta.json-优先/现算-兜底两段式)。
    """
    if scan_dir is not None:
        mp = Path(scan_dir) / "meta.json"
        if mp.exists():
            with contextlib.suppress(Exception):
                r = json.loads(mp.read_text(encoding="utf-8")).get("regime")
                if r:
                    return str(r)
    try:
        from autoresearch.common.regime import classify_regime
        return classify_regime(judged).label
    except Exception:  # noqa: BLE001
        return "range"


def replay_day(judged: pd.DataFrame, attribution: pd.DataFrame, gate_fn, *, regime: str,
               exempt: set[str] | None = None, params: dict | None = None,
               win_thr: float = _DEFAULT_WIN_THR, label_col: str = _RET_MAIN) -> dict:
    """单日重放:judged × attribution[label_col] 过一个 gate → 入选集指标 + 落选赢家清单。

    `gate_fn(judged, *, regime, exempt, params) -> GateResult`——与 l35_gate 契约一致,可注入桩函数
    (测试)或真 registry 取出的函数(CLI)。落选赢家 = cut 里 fwd > win_thr 的票(错杀审计)。
    """
    judged = _code6(judged)
    attribution = _code6(attribution).drop_duplicates(subset="code", keep="first")
    result = gate_fn(judged, regime=regime, exempt=exempt or set(), params=params or {})
    picked, cut = _unpack_gate_result(result)
    picked = [str(c).zfill(6) for c in picked]

    fwd_by_code = (pd.to_numeric(attribution.set_index("code")[label_col], errors="coerce")
                   if label_col in attribution.columns else pd.Series(dtype=float))

    picked_fwd = fwd_by_code.reindex(picked).dropna()
    picked_metrics = _topn_metrics(picked_fwd)

    cut_winners = []
    for row in cut:
        code = str(row.get("code", "")).zfill(6)
        fwd = fwd_by_code.get(code)
        if fwd is not None and pd.notna(fwd) and fwd > win_thr:
            cut_winners.append({**row, "code": code, label_col: round(float(fwd), 5)})
    cut_winners.sort(key=lambda r: r[label_col], reverse=True)

    return {"regime": regime, "n_judged": int(len(judged)), "n_picked": len(picked), "n_cut": len(cut),
            "picked_fwd": [round(float(x), 5) for x in picked_fwd.tolist()],
            "picked_metrics": picked_metrics, "cut_winners": cut_winners,
            "win_thr": win_thr, "label_col": label_col}


# ───────────────────────── IO:发现回测日 × 跨日池化 ─────────────────────────


def discover_dates(scan_root: Path | str) -> list[str]:
    """`scan_root` 下同时具备 `L3_judged_full.csv` + `retro/attribution.csv` 的日子(可回测日)。"""
    scan_root = Path(scan_root)
    if not scan_root.exists():
        return []
    out = []
    for d in sorted(p.name for p in scan_root.iterdir() if p.is_dir()):
        sdir = scan_root / d
        if (sdir / "L3_judged_full.csv").exists() and (sdir / "retro" / "attribution.csv").exists():
            out.append(d)
    return out


def _load_day(scan_root: Path, date: str) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    sdir = scan_root / date
    jp, ap = sdir / "L3_judged_full.csv", sdir / "retro" / "attribution.csv"
    if not (jp.exists() and ap.exists()):
        return None
    return pd.read_csv(jp, dtype={"code": str}), pd.read_csv(ap, dtype={"code": str})


def run_backtest(dates: list[str], *, gate_fn, gate_name: str = "gate", params: dict | None = None,
                 exempt: set[str] | None = None, win_thr: float = _DEFAULT_WIN_THR,
                 label_col: str = _RET_MAIN, scan_root: Path | str | None = None) -> dict:
    """多日重放同一 gate(+ 固定 params)→ 池化 overall + 分 regime 小节 + 跨日 cut_winners 汇总。

    缺 staging 的日子跳过(记 `skipped_dates`,不报错——历史样本天然有洞,不该整跑失败)。
    """
    scan_root = Path(scan_root) if scan_root else Path("context/scan")
    params = params or {}
    days: list[dict] = []
    skipped: list[str] = []
    pooled_fwd: list[float] = []
    by_regime_fwd: dict[str, list[float]] = {}
    cut_winners: list[dict] = []

    for date in dates:
        loaded = _load_day(scan_root, date)
        if loaded is None:
            skipped.append(date)
            continue
        judged, attribution = loaded
        regime = resolve_regime(scan_root / date, judged)
        day_res = replay_day(judged, attribution, gate_fn, regime=regime, exempt=exempt,
                             params=params, win_thr=win_thr, label_col=label_col)
        days.append({"date": date, **{k: v for k, v in day_res.items() if k != "picked_fwd"}})
        pooled_fwd.extend(day_res["picked_fwd"])
        by_regime_fwd.setdefault(regime, []).extend(day_res["picked_fwd"])
        for row in day_res["cut_winners"]:
            cut_winners.append({"date": date, **row})

    cut_winners.sort(key=lambda r: r.get(label_col, 0.0), reverse=True)
    overall = _topn_metrics(pd.Series(pooled_fwd, dtype=float))
    by_regime = {r: _topn_metrics(pd.Series(vals, dtype=float)) for r, vals in sorted(by_regime_fwd.items())}

    return {"gate": gate_name, "params": params, "n_days": len(days), "days": days,
            "overall": overall, "by_regime": by_regime, "cut_winners": cut_winners,
            "skipped_dates": skipped, "win_thr": win_thr, "label_col": label_col}


# ───────────────────────── 渲染 + 落盘 ─────────────────────────


def _pct(x) -> str:
    return "—" if x is None or x != x else f"{x * 100:+.2f}%"


def render_report(results: list[dict], *, out_date: str) -> str:
    """多个 gate×params 回测结果 → `reports/gate_backtest_<date>.md` 正文。presence-gated(空结果不崩)。"""
    lines = [f"# gate_backtest {out_date}", "",
             "零 LLM 历史重放:`L3_judged_full.csv` × `retro/attribution.csv` 的 `fwd_2_oc`"
             "(超短主尺),机制镜像 `l2_eval.forward_compare`。", ""]
    if not results:
        lines.append("_无可回测的 gate/日期组合(registry 未就位或无历史 staging)_")
        return "\n".join(lines) + "\n"

    for res in results:
        gate, params = res["gate"], res.get("params") or {}
        title = f"## {gate}"
        if params:
            title += f"  `params={json.dumps(params, ensure_ascii=False, sort_keys=True)}`"
        lines.append(title)
        skipped = res.get("skipped_dates") or []
        cov = f"- 覆盖 {res['n_days']} 日"
        if skipped:
            cov += f"(跳过 {len(skipped)} 日缺 staging:{', '.join(skipped)})"
        lines.append(cov)

        ov = res["overall"]
        lines.append(f"- **入选集**:mean_fwd2 {_pct(ov['mean_fwd2'])} / hit {_pct(ov['hit'])} / n {ov['n']}")

        lines.append("- 分 regime:")
        by_regime = res.get("by_regime") or {}
        if by_regime:
            for regime, m in by_regime.items():
                lines.append(f"  - {regime}:mean_fwd2 {_pct(m['mean_fwd2'])} / hit {_pct(m['hit'])} / n {m['n']}")
        else:
            lines.append("  - (无)")

        win_thr = res.get("win_thr", _DEFAULT_WIN_THR)
        label_col = res.get("label_col", _RET_MAIN)
        winners = res.get("cut_winners") or []
        lines.append(f"- **落选赢家清单**(cut 且 {label_col}>{win_thr:.0%},错杀审计,共 {len(winners)} 只):")
        if winners:
            for w in winners[:20]:                        # 报告瘦身:超长清单只列前 20(全量在返回 dict 里)
                lines.append(f"  - {w.get('date', '?')} {w.get('code', '?')}"
                             f" rank={w.get('rank', '—')} conviction={w.get('conviction', '—')}"
                             f" lane={w.get('lane', '—')} {label_col}={_pct(w.get(label_col))}"
                             f" reason={w.get('reason', '—')}")
            if len(winners) > 20:
                lines.append(f"  - ...(其余 {len(winners) - 20} 条从略,全量见返回值)")
        else:
            lines.append("  - 无(本样本无错杀)")
        lines.append("")
    return "\n".join(lines) + "\n"


def write_report(results: list[dict], *, out_date: str, out_root: Path | str | None = None) -> Path:
    out_root = Path(out_root) if out_root else Path("reports")
    out_root.mkdir(parents=True, exist_ok=True)
    outp = out_root / f"gate_backtest_{out_date}.md"
    outp.write_text(render_report(results, out_date=out_date), encoding="utf-8")
    return outp


# ───────────────────────── CLI ─────────────────────────


def _parse_params_grid(raw: str | None) -> list[dict]:
    """`--params-json` → params 网格(list[dict])。缺省=单一空 params;object=单点;array=网格。"""
    if not raw:
        return [{}]
    parsed = json.loads(raw)
    if isinstance(parsed, list):
        return [dict(p) for p in parsed]
    if isinstance(parsed, dict):
        return [parsed]
    raise ValueError(f"--params-json 须是 JSON object 或 array,got {type(parsed).__name__}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m autoresearch.research.gate_backtest",
                                 description="L3.5 闸门历史重放回测(入选收益 + 落选赢家错杀审计)")
    ap.add_argument("--dates", default=None, help="逗号分隔回测日;缺省=--scan-root 下自动发现全部可回测日")
    ap.add_argument("--scan-root", default="context/scan")
    ap.add_argument("--out-root", default="reports")
    ap.add_argument("--gate", default=None, help="逗号分隔 gate 名;缺省=registry 已注册全部 gate")
    ap.add_argument("--params-json", default=None, help="gate params:单 JSON object 或 array(网格)")
    ap.add_argument("--win-thr", type=float, default=_DEFAULT_WIN_THR, help="落选赢家审计阈值(默认 +3%%)")
    ap.add_argument("--label", default=_RET_MAIN, help="前向收益列(默认超短主尺 fwd_2_oc)")
    ap.add_argument("--date", default=None, help="报告文件名日期戳(缺省=今天)")
    args = ap.parse_args(argv)

    scan_root = Path(args.scan_root)
    dates = [d for d in args.dates.split(",") if d] if args.dates else discover_dates(scan_root)
    if not dates:
        print(f"[gate_backtest] {scan_root} 下无可回测日"
              f"(需同时有 L3_judged_full.csv + retro/attribution.csv)", file=sys.stderr)
        return 1

    if _build_gate is None:
        print("[gate_backtest] gate registry 未就位(autoresearch.scan.l35_gate 待落地)"
              " → 无法按名字解析 gate,优雅退出", file=sys.stderr)
        return 1

    gate_names = [g for g in args.gate.split(",") if g] if args.gate else registered_gates()
    if not gate_names:
        print("[gate_backtest] registry 无已注册 gate(需先 import 三策略模块完成注册)", file=sys.stderr)
        return 1

    grid = _parse_params_grid(args.params_json)
    results = []
    for name in gate_names:
        try:
            gate_fn = _build_gate(name)
        except Exception as e:  # noqa: BLE001
            print(f"[gate_backtest] 跳过 gate={name}:{e}", file=sys.stderr)
            continue
        for params in grid:
            res = run_backtest(dates, gate_fn=gate_fn, gate_name=name, params=params,
                               win_thr=args.win_thr, label_col=args.label, scan_root=scan_root)
            results.append(res)
            ov = res["overall"]
            print(f"  {name} params={params}: mean_fwd2={ov['mean_fwd2']} hit={ov['hit']} n={ov['n']}"
                  f" cut_winners={len(res['cut_winners'])}")

    out_date = args.date or datetime.date.today().isoformat()
    outp = write_report(results, out_date=out_date, out_root=Path(args.out_root))
    print(f"[gate_backtest] {len(results)} 个 gate×params 组合 × {len(dates)} 日 → {outp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
