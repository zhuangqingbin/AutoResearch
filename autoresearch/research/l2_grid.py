#!/usr/bin/env python3
"""L2 cap/floor 参数网格 —— **只做探索,不做裁决**(确定性,零 LLM)。

design: docs/specs/2026-08-03-scan-next-wave-brainstorm-design.md §3.4 O4

## 这个模块最重要的一句话

**网格只可用于探索;正式裁决用 nested walk-forward / 锁定 holdout,并对多重比较修正。**

一次扫 20 个格点、每个格点看一眼 SLO,必然有几个"看起来更好" —— 那是分位数的算术性质,
不是参数的性质。所以本模块:

- 每个格点跑完都过 BH-FDR(`n_hypotheses = 格点数`),`q>alpha` 的一律标 `NOT_SIGNIFICANT`;
- 排名第一的格点**不会**被标成"建议采用",只标 `EXPLORATORY_BEST`;
- 输出里带一句 `decision_policy`,写明正式裁决要走哪条路。

## 前置(P0-3 已补)

每个格点一个 `VariantSpec` + `definition_hash` + **独立输出根**。少了这一步,replay 的
`_STAGING` 幂等会让第二个格点直接复用第一个的产物,网格表上出现一排相同读数 ——
看起来像"参数不敏感",实际是根本没跑。

## 目标函数与守卫

目标 = §3.1 的 SLO 向量(`wc_l2_all` / `wc_l2_given_l1`);
守卫 = 行业集中度、健康占比、落刀面(selection_reason 分布)、日间稳定性。
守卫破线的格点即便 SLO 更高也标 `GUARD_BREACH` —— winner capture 可以靠集中追热点做高。

`capfloor20` 测的是 **L0 市值 floor**,只可复用对照框架与产物契约,**不是** L2 sector cap /
style floor 的近亲实证,不得当参数先验(§3.4 末段)。

  uv run --no-sync python -m autoresearch.research.l2_grid plan --caps 0.15,0.20,0.25
  uv run --no-sync python -m autoresearch.research.l2_grid score --root context/replay
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from autoresearch.common import stats as st
from autoresearch.research.replay import VariantSpec, bind_variant, read_variant

SCHEMA_VERSION = 1
OUT_JSON = Path("reports/research/l2_grid.json")
OUT_MD = Path("reports/research/l2_grid.md")

DECISION_POLICY = (
    "网格只可用于**探索**。正式裁决必须走 nested walk-forward 或锁定 holdout,"
    "并对多重比较修正;本报告的排名第一者只是 EXPLORATORY_BEST,不是「建议采用」。")

# 守卫阈(预注册)—— 破线即 GUARD_BREACH,不看它的 SLO 有多好看
MAX_TOP_INDUSTRY_SHARE = 0.35     # 单行业占比上限
MIN_LANES = 4                     # lane 覆盖下限
ALPHA = 0.05


def grid_specs(*, caps: list[float] | None = None,
               floor_scales: list[float] | None = None,
               l2_n: int | None = None,
               weights: str = "prior") -> list[VariantSpec]:
    """参数网格 → VariantSpec 列表(含 baseline)。每个格点自带 definition_hash 与独立根。"""
    from autoresearch.scan.recall.l2_stratify import DEFAULT_FLOORS

    caps = caps or [0.20]
    floor_scales = floor_scales or [1.0]
    out = [VariantSpec(name="baseline", weights=weights,
                       note="现行参数;输出根即 replay 主根")]
    for cap in caps:
        for scale in floor_scales:
            if cap == 0.20 and scale == 1.0:
                continue                      # 与 baseline 同定义,不重复占一个根
            floors = ({k: int(round(v * scale)) for k, v in DEFAULT_FLOORS.items()}
                      if scale != 1.0 else None)
            name = f"cap{int(round(cap * 100)):03d}_floor{int(round(scale * 100)):03d}"
            out.append(VariantSpec(name=name, sector_cap_frac=cap, floors=floors,
                                   l2_n=l2_n, weights=weights,
                                   note="探索格点 —— 不得据此直接改生产参数"))
    return out


def plan(specs: list[VariantSpec], base: Path | str | None = None) -> list[dict]:
    """建各格点的独立输出根并落 spec。返回 `[{name, hash, root}]`(可直接喂 replay)。"""
    return [{"name": s.name, "definition_hash": s.definition_hash,
             "output_root": str(bind_variant(s, base))} for s in specs]


# ───────────────────────── 打分 ─────────────────────────


def _slo_for(root: Path) -> dict | None:
    from autoresearch.scan.l2_slo import build as slo_build

    payload = slo_build(root)
    return payload if payload.get("n_days") else None


def _guard_verdict(daily: list[dict]) -> tuple[str, dict]:
    """守卫:行业集中度 / lane 覆盖。破线 → `GUARD_BREACH`(SLO 再好也不算)。"""
    shares, lanes = [], []
    for row in daily:
        guards = row.get("guards") or {}
        if guards.get("top_industry_share") is not None:
            shares.append(float(guards["top_industry_share"]))
        if guards.get("n_lanes") is not None:
            lanes.append(int(guards["n_lanes"]))
    detail = {
        "mean_top_industry_share": round(sum(shares) / len(shares), 4) if shares else None,
        "min_lanes": min(lanes) if lanes else None,
        "max_top_industry_share": MAX_TOP_INDUSTRY_SHARE,
        "min_lanes_required": MIN_LANES,
    }
    breaches = []
    if shares and detail["mean_top_industry_share"] > MAX_TOP_INDUSTRY_SHARE:
        breaches.append("行业集中度超线")
    if lanes and detail["min_lanes"] < MIN_LANES:
        breaches.append("lane 覆盖不足")
    detail["breaches"] = breaches
    return ("GUARD_BREACH" if breaches else "OK"), detail


def score(base: Path | str, *, metric: str = "wc_l2_all") -> dict:
    """扫 `base` 下所有 variant 根 + baseline,算 SLO、配对差、BH-FDR。"""
    root = Path(base)
    baseline = _slo_for(root)
    points: list[dict] = []
    for child in sorted(root.glob("_v_*")):
        if not child.is_dir():
            continue
        spec = read_variant(child)
        payload = _slo_for(child)
        if spec is None or payload is None:
            points.append({"name": child.name, "status": "NO_DATA",
                           "definition_hash": (spec or {}).get("definition_hash")})
            continue
        guard_status, guard_detail = _guard_verdict(payload["daily"])
        points.append({
            "name": spec["spec"]["name"],
            "definition_hash": spec["definition_hash"],
            "output_root": str(child),
            "n_days": payload["n_days"],
            "slo": payload.get(metric),
            "guard_status": guard_status,
            "guards": guard_detail,
            "status": guard_status if guard_status != "OK" else "SCORED",
            "daily": payload["daily"],
        })

    scored = [p for p in points if p["status"] == "SCORED"]
    deltas = _paired_deltas(baseline, scored, metric)
    return {
        "schema_version": SCHEMA_VERSION,
        "metric": metric,
        "decision_policy": DECISION_POLICY,
        "alpha": ALPHA,
        "n_hypotheses": len(scored),
        "baseline": None if baseline is None else {
            "n_days": baseline["n_days"], "slo": baseline.get(metric)},
        "points": points,
        "deltas": deltas,
        "exploratory_best": _best(deltas),
        "capfloor20_note": ("`capfloor20` 测的是 L0 市值 floor,"
                            "只可复用对照框架与产物契约,不是 L2 cap/floor 的近亲实证"),
    }


def _paired_deltas(baseline: dict | None, scored: list[dict], metric: str) -> list[dict]:
    """逐格点与 baseline 的**按日配对**差 + BH-FDR。baseline 缺 → 全部 NO_BASELINE。"""
    if baseline is None or not scored:
        return [{"name": p["name"], "status": "NO_BASELINE"} for p in scored]
    base_by_date = {row["date"]: row.get(metric) for row in baseline["daily"]}
    rows: list[dict] = []
    pvalues: list[float] = []
    for point in scored:
        pairs = [{"date": row["date"], "variant": row.get(metric),
                  "baseline": base_by_date.get(row["date"])}
                 for row in point["daily"]
                 if row.get(metric) is not None and base_by_date.get(row["date"]) is not None]
        frame = pd.DataFrame(pairs)
        interval = (st.paired_delta_interval(frame, "variant", "baseline", date_col="date")
                    if len(frame) else st.Interval(None, None, None, 0, 0, "no_overlap"))
        # 粗略双侧 p:用区间宽度反推 z(仅供 FDR 排序;裁决不看它,看 walk-forward)
        pvalue = _interval_pvalue(interval)
        pvalues.append(pvalue)
        rows.append({"name": point["name"], "definition_hash": point["definition_hash"],
                     "n_paired_days": int(len(frame)), "delta": interval.as_dict(),
                     "p_approx": pvalue})
    for row, verdict in zip(rows, st.bh_fdr(pvalues, ALPHA), strict=True):
        row["q"] = verdict["q"]
        row["status"] = "SIGNIFICANT_EXPLORATORY" if verdict["rejected"] \
            else "NOT_SIGNIFICANT"
    return rows


def _interval_pvalue(interval: st.Interval) -> float:
    """区间 → 近似双侧 p(仅供 FDR 排序)。区间缺失 → 1.0(最保守)。"""
    if interval.lo is None or interval.hi is None or interval.point is None:
        return 1.0
    se = (interval.hi - interval.lo) / (2 * st.norm_ppf(1 - interval.alpha / 2))
    if se <= 0:
        return 0.0 if interval.point != 0 else 1.0
    return float(2 * (1 - st.norm_cdf(abs(interval.point) / se)))


def _best(deltas: list[dict]) -> dict | None:
    """排名第一者 —— 标签是 `EXPLORATORY_BEST`,**不是**「建议采用」。"""
    usable = [d for d in deltas if d.get("delta", {}).get("point") is not None]
    if not usable:
        return None
    top = max(usable, key=lambda d: d["delta"]["point"])
    return {"name": top["name"], "delta": top["delta"]["point"], "q": top.get("q"),
            "label": "EXPLORATORY_BEST",
            "caveat": "探索排名,不是建议采用 —— " + DECISION_POLICY}


def render(payload: dict) -> str:
    lines = [
        "# L2 cap/floor 参数网格(探索,非裁决)",
        "",
        f"> ⚠️ {payload['decision_policy']}",
        "",
        f"- 目标 `{payload['metric']}` · 格点 {payload['n_hypotheses']} 个 · "
        f"多重检验 BH-FDR(alpha={payload['alpha']})",
        f"- {payload['capfloor20_note']}",
        "",
        "## 格点",
        "",
        "| variant | hash | 日数 | 配对差 | 区间 | q | 状态 | 守卫 |",
        "|---|---|---:|---:|---|---:|---|---|",
    ]
    guard_by_name = {p["name"]: p for p in payload["points"] if "guard_status" in p}
    for row in payload["deltas"]:
        delta = row.get("delta") or {}
        point = "—" if delta.get("point") is None else f"{delta['point']:+.4f}"
        band = ("—" if delta.get("lo") is None
                else f"[{delta['lo']:+.4f}, {delta['hi']:+.4f}]")
        q = "—" if row.get("q") is None else f"{row['q']:.3f}"
        guard = guard_by_name.get(row["name"], {}).get("guard_status", "—")
        lines.append(f"| `{row['name']}` | `{(row.get('definition_hash') or '')[:8]}` "
                     f"| {row.get('n_paired_days', 0)} | {point} | {band} | {q} "
                     f"| {row.get('status')} | {guard} |")
    for point in payload["points"]:
        if point.get("guard_status") == "GUARD_BREACH":
            lines.append(f"| `{point['name']}` | — | — | — | — | — | GUARD_BREACH "
                         f"| {'、'.join(point['guards']['breaches'])} |")
    if not payload["deltas"] and not payload["points"]:
        lines.append("| — | — | — | — | — | — | — | — |")

    best = payload.get("exploratory_best")
    lines += ["", "## 排名第一(探索标签)", ""]
    lines.append(f"- `{best['name']}` Δ={best['delta']:+.4f} · **{best['label']}** · "
                 f"{best['caveat']}" if best else "- _(无可比格点)_")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="L2 cap/floor 网格(§3.4 O4,仅探索)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan", help="建各格点的独立输出根并落 spec")
    p.add_argument("--root", default="context/replay")
    p.add_argument("--caps", default="0.15,0.20,0.25")
    p.add_argument("--floor-scales", default="1.0")
    p.add_argument("--l2-n", type=int, default=None)

    s = sub.add_parser("score", help="扫各格点根 → SLO + 配对差 + BH-FDR")
    s.add_argument("--root", default="context/replay")
    s.add_argument("--metric", default="wc_l2_all")
    s.add_argument("--json-out", default=str(OUT_JSON))
    s.add_argument("--md-out", default=str(OUT_MD))

    a = ap.parse_args(argv)
    if a.cmd == "plan":
        specs = grid_specs(caps=[float(x) for x in a.caps.split(",") if x],
                           floor_scales=[float(x) for x in a.floor_scales.split(",") if x],
                           l2_n=a.l2_n)
        rows = plan(specs, a.root)
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    payload = score(a.root, metric=a.metric)
    for path, text in ((Path(a.json_out),
                        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"),
                       (Path(a.md_out), render(payload))):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    print(f"[l2_grid] {payload['n_hypotheses']} 格点 → {a.json_out} / {a.md_out}"
          f";⚠️ 探索用,裁决走 walk-forward/holdout")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
