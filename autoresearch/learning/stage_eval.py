#!/usr/bin/env python3
"""F · 各阶段 agent edge 评估 —— 把每个 LLM 阶段的 keep/score/rating/verdict 对齐已实现 fwd 收益,
量化『这一段 agent 到底有没有赚到它的 token』(确定性,零 LLM)。

retro 只评最终 buy-list 的命中;本模块补**逐阶段归因**(2026 agent-eval 的核心:measure a sequence
of actions,不是单点输出)。每段一个 edge:
- **L2 粗排**:召回池内 keep(200)vs cut(800)的 fwd 均值 lift;l2_score 的 rank-IC。
- **L3 精排**:L2-keep 内 finalist(30)vs 非 finalist 的 lift;`conviction−fragility` 的 rank-IC。
- **L4 研究**:finalist 五档评级是否**单调**(越多头 fwd 越高)→ 评级-score 的 rank-IC + 分档均值。
- **Tier-3 多空辩论**:`维持` vs `降级/否决` 的 fwd 均值差(>0 = 辩论正确压低了后来的差票)。

纯统计函数无 IO、可离线自测;取已实现收益复用 `retro.realized_returns`(tushare,与 factor_lab 同口径)。
喂 retro_input.md → scan-retro skill 据此判断哪段该调(松/紧/重标定),即闭环的『可观测』层。

用法:
  uv run --no-sync python scripts/stage_eval.py --selftest
  uv run --no-sync python scripts/stage_eval.py 2026-06-18          # 需 fwd 已实现
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from autoresearch.agents.utils.rating import RATINGS_5_TIER  # Buy>OW>Hold>UW>Sell

_RET_T5 = "fwd_5_oc"   # T+5 收盘口径:LLM 阶段(L3/L4)推的是 1–2 周 swing,T+5 比 T+1 更贴论点
_RET_T1 = "fwd_1_oo"   # T+1 开到开:更快、噪声大,主要给 L2


# ───────────────────────── 纯统计核(无 IO,可离线自测) ─────────────────────────


def _as_bool(s: pd.Series) -> pd.Series:
    def one(v):
        if isinstance(v, str):
            return v.strip().lower() in ("true", "1", "yes", "是")
        try:
            return bool(v) and v == v
        except (TypeError, ValueError):
            return False
    return s.map(one)


def _code6(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["code"] = df["code"].astype(str).str.zfill(6)
    return df


def binary_lift(df: pd.DataFrame, flag_col: str, ret_col: str = _RET_T5) -> dict:
    """成员 flag(bool)分两组的 fwd 均值 + lift(in−out)。某组空 → 对应均值 None。纯函数。"""
    d = df.copy()
    d[ret_col] = pd.to_numeric(d.get(ret_col), errors="coerce")
    d = d[d[ret_col].notna()]
    flag = _as_bool(d[flag_col]) if flag_col in d.columns else pd.Series(False, index=d.index)
    a, b = d[flag], d[~flag]
    mi = round(float(a[ret_col].mean()), 4) if len(a) else None
    mo = round(float(b[ret_col].mean()), 4) if len(b) else None
    lift = round(mi - mo, 4) if (mi is not None and mo is not None) else None
    return {"n_in": int(len(a)), "n_out": int(len(b)), "mean_in": mi, "mean_out": mo, "lift": lift}


def rank_ic(df: pd.DataFrame, score_col: str, ret_col: str = _RET_T5, min_n: int = 20) -> float | None:
    """score 与 fwd 的 Spearman rank-IC;有效样本 < min_n → None(样本太小不算)。纯函数。"""
    d = df.copy()
    for c in (score_col, ret_col):
        d[c] = pd.to_numeric(d.get(c), errors="coerce")
    d = d[d[score_col].notna() & d[ret_col].notna()]
    if len(d) < min_n:
        return None
    return round(float(d[score_col].rank().corr(d[ret_col].rank())), 4)


def group_means(df: pd.DataFrame, group_col: str, ret_col: str = _RET_T5) -> dict:
    """按 group_col 分组的 fwd 均值 + 计数 → {group: {n, mean}}。纯函数。"""
    d = df.copy()
    d[ret_col] = pd.to_numeric(d.get(ret_col), errors="coerce")
    d = d[d[ret_col].notna() & d[group_col].notna()]
    return {str(g): {"n": int(len(sub)), "mean": round(float(sub[ret_col].mean()), 4)}
            for g, sub in d.groupby(group_col)}


def verdict_edge(df: pd.DataFrame, verdict_col: str = "verdict", ret_col: str = _RET_T5) -> dict:
    """Tier-3 多空辩论 edge:`维持` 组 fwd 均值 − (`降级`∪`否决`)组 fwd 均值。>0 = 辩论压对了。纯函数。"""
    gm = group_means(df, verdict_col, ret_col)
    keep = gm.get("维持", {}).get("mean")
    downs = [v["mean"] for k, v in gm.items() if k in ("降级", "否决") and v.get("mean") is not None]
    down = round(sum(downs) / len(downs), 4) if downs else None
    edge = round(keep - down, 4) if (keep is not None and down is not None) else None
    return {"by_verdict": gm, "edge_keep_minus_down": edge}


def rating_score(rating: str) -> float | None:
    """五档 → 多头分(Buy=4 … Sell=0;越多头越大),供评级单调性 rank-IC。"""
    order = {r: i for i, r in enumerate(RATINGS_5_TIER)}
    i = order.get(rating)
    return None if i is None else float(len(RATINGS_5_TIER) - 1 - i)


# ───────────────────────── IO:读阶段产物 × 已实现收益 → 各段 edge ─────────────────────────


def _read(p: Path) -> pd.DataFrame | None:
    return pd.read_csv(p, dtype={"code": str}) if p.exists() else None


def evaluate(date: str, scan_root: Path | None = None, report_root: Path | None = None,
             realized: pd.DataFrame | None = None) -> dict:
    """读 context/scan/<date>/ 各阶段 staging × 已实现收益 → 逐段 edge dict。

    realized 显式传入(测试用 stub 注入)则不取数;否则 `retro.realized_returns(date)`(网络)。
    fwd 未实现 → RuntimeError(供上游 try/except 跳过)。
    """
    scan_root = scan_root or Path("context/scan")
    sdir = scan_root / date
    if realized is None:
        import autoresearch.learning.retro as retro  # 延迟导入,避免与 retro 的循环 import
        realized = retro.realized_returns(date)
    if realized is None or realized.empty:
        raise RuntimeError(f"{date} 的 fwd 未实现 / 无价格,暂不能逐段评估")
    realized = _code6(realized)
    res: dict = {"date": date, "n_realized": int(realized["fwd_5_oc"].notna().sum()), "stages": {}}

    # L2:召回池内 是否进 GBDT 学习重排 top200(l2_kept)+ gbdt_score 的 IC(确定性 L2 的 edge 评估)
    recall, keep = _read(sdir / "L1_recall_top1000.csv"), _read(sdir / "L2_gbdt_top200.csv")
    if recall is not None and keep is not None:
        recall, keep = _code6(recall), _code6(keep)
        recall["l2_kept"] = recall["code"].isin(set(keep["code"]))
        if "gbdt_score" in keep.columns:
            recall = recall.merge(keep[["code", "gbdt_score"]], on="code", how="left")
        m = recall.merge(realized, on="code", how="left")
        res["stages"]["L2"] = {**binary_lift(m, "l2_kept", _RET_T1),
                               "ic_gbdt_score_t1": rank_ic(m, "gbdt_score", _RET_T1) if "gbdt_score" in m else None}

    # L3:L2-keep 内 finalist vs 非 finalist + (conviction−fragility) IC
    l3 = _read(sdir / "L3_judged_full.csv")
    fin = _read(sdir / "finalists.csv")
    if l3 is not None and {"conviction", "fragility"}.issubset(l3.columns):
        l3 = _code6(l3)
        l3["net"] = pd.to_numeric(l3["conviction"], errors="coerce").fillna(0) - \
            pd.to_numeric(l3["fragility"], errors="coerce").fillna(0)
        fin_codes = set(_code6(fin)["code"]) if fin is not None else set()
        l3["is_finalist"] = l3["code"].isin(fin_codes)
        m = l3.merge(realized, on="code", how="left")
        res["stages"]["L3"] = {**binary_lift(m, "is_finalist", _RET_T5),
                               "ic_net_t5": rank_ic(m, "net", _RET_T5)}

    # L4:finalist 五档评级单调性(从已发布卡取 {code: rating})
    import autoresearch.learning.retro as retro  # 复用卡片评级解析(发布层卡名是名称,code 从卡内标题取)
    ratings = retro._buylist(date, report_root)
    if ratings:
        rdf = pd.DataFrame([{"code": c, "rating": r, "rating_score": rating_score(r)}
                            for c, r in ratings.items()])
        rdf = _code6(rdf).merge(realized, on="code", how="left")
        res["stages"]["L4"] = {"by_rating": group_means(rdf, "rating", _RET_T5),
                               "ic_rating_t5": rank_ic(rdf, "rating_score", _RET_T5, min_n=10),
                               "ic_rating_t1": rank_ic(rdf, "rating_score", _RET_T1, min_n=10)}

    # Tier-3:多空辩论 verdict edge
    v = _read(sdir / "verify.csv")
    if v is not None and "verdict" in v.columns and "code" in v.columns:
        vdf = _code6(v[["code", "verdict"]]).merge(realized, on="code", how="left")
        res["stages"]["Tier-3"] = verdict_edge(vdf, "verdict", _RET_T5)

    outdir = sdir / "retro"
    outdir.mkdir(parents=True, exist_ok=True)
    _flat_csv(res).to_csv(outdir / "stage_eval.csv", index=False)
    return res


def _flat_csv(res: dict) -> pd.DataFrame:
    """各段 edge 摊平成一表(每段一行 metric),便于跨日累积/回看。"""
    rows = []
    for stage, d in res.get("stages", {}).items():
        for k, val in d.items():
            if isinstance(val, dict):
                continue
            rows.append({"date": res["date"], "stage": stage, "metric": k, "value": val})
    return pd.DataFrame(rows, columns=["date", "stage", "metric", "value"])


def _pct(x) -> str:
    return "—" if x is None else f"{x * 100:+.1f}%"


def render_stage_eval(res: dict) -> list[str]:
    """逐段 edge → retro_input.md 区块(给 scan-retro skill 判断哪段该松/紧/重标定)。"""
    s = res.get("stages", {})
    out = [f"\n## 各阶段 agent edge(已实现 {res.get('n_realized', '?')} 只;T+5 收口径,L2 用 T+1)"]
    if not s:
        return out + ["_无可评估阶段(staging 缺失)_"]
    if "L2" in s:
        d = s["L2"]
        out.append(f"- **L2 粗排**:keep {d['n_in']} vs cut {d['n_out']},fwd lift **{_pct(d['lift'])}**"
                   f"(keep {_pct(d['mean_in'])} / cut {_pct(d['mean_out'])});l2_score IC {d.get('ic_l2_score_t1')}")
    if "L3" in s:
        d = s["L3"]
        out.append(f"- **L3 精排**:finalist {d['n_in']} vs 落选 {d['n_out']},lift **{_pct(d['lift'])}**"
                   f";(确信−脆弱)net IC(T+5){d.get('ic_net_t5')}")
    if "L4" in s:
        d = s["L4"]
        br = "、".join(f"{k} {_pct(v['mean'])}×{v['n']}" for k, v in d.get("by_rating", {}).items())
        out.append(f"- **L4 评级**:单调性 rank-IC(T+5){d.get('ic_rating_t5')} / T+1 {d.get('ic_rating_t1')}"
                   f";分档:{br or '—'}  _(IC>0 = 越多头越涨,评级有效)_")
    if "Tier-3" in s:
        d = s["Tier-3"]
        bv = "、".join(f"{k} {_pct(v['mean'])}×{v['n']}" for k, v in d.get("by_verdict", {}).items())
        out.append(f"- **Tier-3 多空辩论**:维持−(降级∪否决)edge **{_pct(d.get('edge_keep_minus_down'))}**"
                   f"({bv or '—'})  _(edge>0 = 辩论压对了差票,值回 Opus)_")
    return out


# ───────────────────────── 离线自测(纯统计核) ─────────────────────────


def _selftest() -> int:
    fails: list[str] = []

    # binary_lift:in 组明显更高 → lift 正;计数对
    df = pd.DataFrame({"keep": [True, True, False, False, False],
                       "fwd_5_oc": [0.10, 0.08, -0.02, 0.00, -0.04]})
    bl = binary_lift(df, "keep", "fwd_5_oc")
    if bl["n_in"] != 2 or bl["n_out"] != 3 or bl["lift"] is None or bl["lift"] <= 0:
        fails.append(f"binary_lift 错: {bl}")
    if abs(bl["mean_in"] - 0.09) > 1e-9:
        fails.append(f"binary_lift mean_in 错: {bl}")
    # flag 缺列 → 全 False,in 组空
    bl2 = binary_lift(df, "missing", "fwd_5_oc")
    if bl2["n_in"] != 0 or bl2["mean_in"] is not None or bl2["lift"] is not None:
        fails.append(f"binary_lift 缺列应 in 空: {bl2}")

    # rank_ic:完全单调正相关 ≈ +1;样本不足 → None
    n = 25
    mono = pd.DataFrame({"score": list(range(n)), "fwd_5_oc": [i * 0.01 for i in range(n)]})
    ic = rank_ic(mono, "score", "fwd_5_oc")
    if ic is None or ic < 0.99:
        fails.append(f"rank_ic 单调应 ≈1: {ic}")
    if rank_ic(mono.head(5), "score", "fwd_5_oc") is not None:
        fails.append("rank_ic 样本<min_n 应 None")

    # group_means:分组均值 + 计数
    gm = group_means(pd.DataFrame({"g": ["A", "A", "B"], "fwd_5_oc": [0.1, 0.3, -0.1]}), "g")
    if gm["A"]["n"] != 2 or abs(gm["A"]["mean"] - 0.2) > 1e-9 or gm["B"]["mean"] != -0.1:
        fails.append(f"group_means 错: {gm}")

    # verdict_edge:维持涨、降级跌 → edge 正;否决并入 down
    vdf = pd.DataFrame({"verdict": ["维持", "维持", "降级", "否决"],
                        "fwd_5_oc": [0.08, 0.06, -0.03, -0.05]})
    ve = verdict_edge(vdf)
    if ve["edge_keep_minus_down"] is None or ve["edge_keep_minus_down"] <= 0:
        fails.append(f"verdict_edge 应正(辩论压对): {ve}")
    if ve["by_verdict"]["维持"]["n"] != 2:
        fails.append(f"verdict_edge 计数错: {ve}")

    # rating_score:Buy 最大、Sell 最小、未知 None
    if rating_score("Buy") != 4.0 or rating_score("Sell") != 0.0 or rating_score("?") is not None:
        fails.append("rating_score 映射错")

    # evaluate:注入 realized stub(不取数),跑通 L3/L4/Tier-3 的 join(用 tmp staging)
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        sdir = Path(td) / "2026-06-18"
        sdir.mkdir(parents=True)
        pd.DataFrame({"code": ["000001", "000002", "000003"], "conviction": [90, 80, 60],
                      "fragility": [10, 20, 55]}).to_csv(sdir / "L3_judged_full.csv", index=False)
        pd.DataFrame({"code": ["000001", "000002"]}).to_csv(sdir / "finalists.csv", index=False)
        pd.DataFrame({"code": ["000001", "000002"], "verdict": ["维持", "降级"],
                      "bear": ["x", "y"]}).to_csv(sdir / "verify.csv", index=False)
        realized = pd.DataFrame({"code": ["000001", "000002", "000003"],
                                 "fwd_1_oo": [0.05, -0.02, -0.01], "fwd_5_oc": [0.09, -0.03, -0.02]})
        res = evaluate("2026-06-18", scan_root=Path(td), realized=realized)
        if "L3" not in res["stages"] or res["stages"]["L3"]["lift"] is None:
            fails.append(f"evaluate L3 缺/无 lift: {res['stages'].get('L3')}")
        if res["stages"]["L3"]["lift"] <= 0:        # finalist(000001/2)应 > 落选(000003)
            fails.append(f"evaluate L3 finalist 应跑赢落选: {res['stages']['L3']}")
        if "Tier-3" not in res["stages"] or res["stages"]["Tier-3"]["edge_keep_minus_down"] is None:
            fails.append(f"evaluate Tier-3 缺 edge: {res['stages'].get('Tier-3')}")
        if not (sdir / "retro" / "stage_eval.csv").exists():
            fails.append("evaluate 未落 stage_eval.csv")
        rendered = "\n".join(render_stage_eval(res))
        if "L3 精排" not in rendered or "Tier-3 多空辩论" not in rendered:
            fails.append(f"render_stage_eval 缺段: {rendered}")

    if fails:
        print("SELFTEST ❌")
        for f in fails:
            print("  -", f)
        return 1
    print("SELFTEST ✅  binary_lift / rank_ic / group_means / verdict_edge / rating_score "
          "+ evaluate(stub realized → L3/Tier-3 join + csv + render) 全过")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--selftest" in sys.argv:
        return _selftest()
    if args:
        res = evaluate(args[0])
        print("\n".join(render_stage_eval(res)))
        print(f"\n→ context/scan/{args[0]}/retro/stage_eval.csv")
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
