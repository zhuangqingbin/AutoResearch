#!/usr/bin/env python3
"""家族 × 尺 edge 普查 —— 漏斗每一层产出的每个家族,在现行决策尺上相对全市场有没有超额(零 LLM、只读、不回注)。

预注册(假设 / 判读规则 / 口径,**先写后看**):`docs/research/2026-08-22-edge-census.md` §0。
立案:08-07 两尺对照已证 value 路作废、L3 真选 edge 归零、门的价值配对 −0.68%(n=6);
08-21 Gate 0 证 lowturn −0.24pp、healthy −0.17pp。记忆里「门价值 +4.35pp / 评级 rank-IC +0.55」
全是旧尺 `fwd_2_oc` 读数,换尺后从未重建。本仪器把**所有家族**放进同一张表、同一把尺、同一个
基准,一次看完。

数据全在盘上,零网络:
  * 价格 = `lake/daily/*.parquet`(生产湖);前向收益复用 `factor_lab.forward_returns`
    (`gap_c1_o2` / `buyable_c1` / `fwd_5_oc` / `fwd_10_oc` 同一实现,不另造口径);
  * 家族成员 = 每个扫描日 `context_<engine>/scan/<date>/` 的 staging 产物(presence-gated:
    产物缺 → 该家族该日不计,**不伪造为空集**);
  * 📌 保送票从 L3/L4/E6 全部家族剔除,单列一族(2026-07-16 判例:保送不是排序的产物)。

口径(与幸存仪器 `lowturn_precheck` 对齐,差异显式):市场人口 = 当日**全湖**可交易票
(`ruler.entry_tradable` 折叠,**不设市值地板**,与 E6 `rel_gap_market` 同一人口;lowturn_precheck
用研究面板 cap_floor 30 亿)。相对超额主列 = 家族均值 − 截面中位;另给 − 截面等权均值(E6 口径)。

用法:
  uv run --no-sync python -m autoresearch.research.edge_census [--since 2026-07-01] [--out PATH]
→ `reports_<engine>/research/edge_census.md` + 同目录 `_edge_census.json`(机器可读)。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from autoresearch.common import ruler as _ruler, workspace as ws

RULERS = ("gap_c1_o2", "fwd_5_oc", "fwd_10_oc")
MAIN = _ruler.MAIN_RULER
MIN_CROSS_SECTION = 50          # 截面不足 → 该日不算(同 lowturn_precheck)
POS_T = 2.0                     # 判读规则(§0 预注册):正证据 = 超额>0 ∧ t≥POS_T ∧ n_days≥POS_MIN_DAYS
POS_MIN_DAYS = 20
RATING_ORD = {"Buy": 5, "Overweight": 4, "Hold": 3, "Underweight": 2, "Sell": 1}
RATING_GE_OW = ("Buy", "Overweight")
IC_MIN_CARDS = 5
FWD_LOOKAHEAD = 12              # 装价格 pivot 时往后多装的交易日数(fwd_10 + 余量)
FOOTNOTE = ("_参考尺 `fwd_5_oc`/`fwd_10_oc` **只观察**,不判读;决策尺 `gap_c1_o2`"
            "(2026-07-10 / 08-05 裁定),本表不构成换尺依据。相对超额(中位)= 家族内均值 − 当日"
            "全湖可交易截面中位;(均值)= − 截面等权均值(E6 `rel_gap_market` 口径)。"
            "📌 保送票已从 L3/L4/E6 家族剔除。**不显著 ≠ 有 alpha。**_")


# ───────────────────────── 价格:湖 → pivot → 前向收益 ─────────────────────────
# 三个函数 2026-09-06(E4)分别搬到 `data/market_panel.py`(湖读取)与
# `common/forward_returns.py`(纯计算);这里是同对象转发,口径与调用签名不变。
from autoresearch.common.forward_returns import forward_frame  # noqa: E402,F401
from autoresearch.data.market_panel import lake_trade_days, load_lake_pivots  # noqa: E402,F401

# ───────────────────────── 家族:staging 产物 → {家族名: codes} ─────────────────────────

def _codes(df: pd.DataFrame, col: str = "code") -> set[str]:
    if df is None or col not in df.columns:
        return set()
    return set(df[col].astype(str).str.zfill(6))


def _read_csv(p: Path) -> pd.DataFrame | None:
    try:
        return pd.read_csv(p, dtype={"code": str, "ticker": str}, low_memory=False) if p.exists() else None
    except Exception:  # noqa: BLE001 — 坏文件 = 该产物当日不在场
        return None


def pinned_codes(scan_dir: Path) -> set[str]:
    """📌 保送票 = finalists.csv 里 lane=="pinned" 或 pinned_note 非空(两代标记都认)。"""
    fin = _read_csv(scan_dir / "finalists.csv")
    if fin is None:
        return set()
    mask = pd.Series(False, index=fin.index)
    if "lane" in fin.columns:
        mask |= fin["lane"].astype(str).str.strip() == "pinned"
    if "pinned_note" in fin.columns:
        mask |= fin["pinned_note"].fillna("").astype(str).str.strip() != ""
    return _codes(fin[mask])


def families_for_day(scan_dir: Path | str) -> tuple[dict[str, set[str]], dict[str, int], set[str]]:
    """一个扫描日 → ({家族名: codes}, {code: 评级序数}(rank-IC 用), pinned)。
    全部 presence-gated:产物缺席的家族**不出现在 dict 里**(≠ 空集)。"""
    d = Path(scan_dir)
    fam: dict[str, set[str]] = {}
    pin = pinned_codes(d)
    if pin:
        fam["📌·保送"] = pin

    # ── L1 ──
    ch = _read_csv(d / "L1_channels.csv")
    if ch is not None and {"channel", "code"} <= set(ch.columns):
        for name, g in ch.groupby("channel"):
            fam[f"L1·{name}"] = _codes(g)
    l1 = _read_csv(d / "L1_recall_top1000.csv")
    if l1 is not None:
        fam["L1·全体"] = _codes(l1)

    # ── L2 ──
    l2 = _read_csv(d / "L2_gbdt_top200.csv")
    if l2 is not None:
        fam["L2·全体"] = _codes(l2) - pin
        if "selection_reason" in l2.columns:
            r = l2["selection_reason"].fillna("").astype(str)
            for reason in ("merit", "backfill", "sector"):
                sub = l2[r == reason]
                if len(sub):
                    fam[f"L2·{reason}"] = _codes(sub) - pin
            if "selection_detail" in l2.columns:
                det = l2["selection_detail"].fillna("").astype(str)
                for bucket, g in l2[(r == "lane") & (det != "")].groupby(det[(r == "lane") & (det != "")]):
                    fam[f"L2·桶·{bucket}"] = _codes(g) - pin

    # ── L3 ──
    fin = _read_csv(d / "finalists.csv")
    finalist = (_codes(fin) - pin) if fin is not None else None
    if finalist is not None:
        fam["L3·finalist"] = finalist
    jd = _read_csv(d / "L3_judged_full.csv")
    if jd is not None:
        judged = _codes(jd) - pin
        if finalist is not None:
            fam["L3·bench"] = judged - finalist
        if "lane" in jd.columns:
            lanes = jd["lane"].fillna("").astype(str).str.strip()
            for lane, g in jd[lanes != ""].groupby(lanes[lanes != ""]):
                if lane == "pinned":
                    continue
                fam[f"L3·lane·{lane}"] = _codes(g) - pin
        if "conviction" in jd.columns:
            conv = pd.to_numeric(jd["conviction"], errors="coerce")
            fam["L3·conviction≥75"] = _codes(jd[conv >= 75]) - pin
            fam["L3·conviction55-74"] = _codes(jd[(conv >= 55) & (conv < 75)]) - pin
            fam["L3·conviction<55"] = _codes(jd[conv < 55]) - pin
    kept, cut = _read_csv(d / "_l3_pass1_kept.csv"), _read_csv(d / "_l3_pass1_cut.csv")
    if kept is not None:
        fam["L3·pass1·kept"] = _codes(kept) - pin
    if cut is not None:
        fam["L3·pass1·cut"] = _codes(cut) - pin

    # ── L4 ──
    ratings: dict[str, str] = {}
    try:
        from autoresearch.scan.health import final_ratings
        ratings = {str(k).zfill(6): v for k, v in final_ratings(d).items()}
    except Exception:  # noqa: BLE001 — 读不出评级 = 该日 L4 家族不在场
        ratings = {}
    ratings = {k: v for k, v in ratings.items() if k not in pin and v in RATING_ORD}
    ord_map = {k: RATING_ORD[v] for k, v in ratings.items()}
    if ratings:
        for r in RATING_ORD:
            s = {k for k, v in ratings.items() if v == r}
            if s:
                fam[f"L4·评级·{r}"] = s
        fam["L4·≥OW"] = {k for k, v in ratings.items() if v in RATING_GE_OW}
        fam["L4·<OW"] = {k for k, v in ratings.items() if v not in RATING_GE_OW}
    try:
        from autoresearch.scan.decision_read_model import read_decisions
        recs = read_decisions(d) if (d / "decision_records.json").exists() else {}
    except Exception:  # noqa: BLE001 — 坏账本 = 该日三门/早停家族不在场(不猜)
        recs = {}
    if recs:
        early = {str(c).zfill(6) for c, r in recs.items() if r.early_stop} - pin
        full = {str(c).zfill(6) for c, r in recs.items() if not r.early_stop} - pin
        if early:
            fam["L4·早停"] = early
        if full:
            fam["L4·满卡"] = full
        gates: dict[tuple[str, str], set[str]] = {}
        for c, r in recs.items():
            c6 = str(c).zfill(6)
            if c6 in pin:
                continue
            for g, st in (r.gate_states or {}).items():
                if st in ("PASS", "FAIL"):
                    gates.setdefault((g, st), set()).add(c6)
        for (g, st), s in gates.items():
            fam[f"L4·门·{g}·{st}"] = s

    # ── E6 ──
    p = d / "_relative_buy_decision.json"
    if p.exists():
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            doc = None
        if isinstance(doc, dict) and str(doc.get("date") or "") == d.name:
            buys = {str(b.get("code")).zfill(6) for b in (doc.get("buys") or []) if b.get("code")}
            cands = [c for c in (doc.get("candidates") or []) if isinstance(c, dict) and c.get("code")]
            elig = {str(c["code"]).zfill(6) for c in cands if c.get("eligible")}
            veto = {str(c["code"]).zfill(6) for c in cands if not c.get("eligible")}
            r23 = {str(c["code"]).zfill(6) for c in cands if c.get("rank") in (2, 3)}
            if buys:
                fam["E6·rank1"] = buys - pin
            if elig:
                fam["E6·eligible"] = elig - pin
                fam["E6·eligible非rank1"] = (elig - buys) - pin
            if veto:
                fam["E6·硬否决"] = veto - pin
            if r23:
                fam["E6·rank2-3"] = r23 - pin
    return fam, ord_map, pin


# ───────────────────────── 统计 ─────────────────────────

def daily_stats(fr: pd.DataFrame, codes: set[str], ruler: str) -> dict | None:
    """一日 × 一族 × 一尺 → 读数;截面 <MIN_CROSS_SECTION 或族内无可算票 → None(不算)。"""
    if fr is None or ruler not in fr.columns:
        return None
    ok = _ruler.entry_tradable(fr, ruler_name=ruler)
    fwd = pd.to_numeric(fr[ruler], errors="coerce")
    base = fwd[ok & fwd.notna()]
    if len(base) < MIN_CROSS_SECTION:
        return None
    grp = base.reindex([c for c in codes if c in base.index]).dropna()
    if len(grp) == 0:
        return None
    return {"n": int(len(grp)), "mean": float(grp.mean()), "hit": float((grp > 0).mean()),
            "excess_med": float(grp.mean() - base.median()),
            "excess_mean": float(grp.mean() - base.mean())}


def aggregate(days: list[dict]) -> dict:
    """跨日聚合:超额均值(pp)+ 配对 t(逐日 excess_med 的 one-sample t)。"""
    if not days:
        return {"n_days": 0, "n_med_per_day": 0.0, "mean_pp": float("nan"),
                "excess_med_pp": float("nan"), "excess_mean_pp": float("nan"),
                "t": float("nan"), "hit_mean": float("nan")}
    ex = np.array([d["excess_med"] for d in days], dtype=float)
    sd = ex.std(ddof=1) if len(ex) > 1 else 0.0
    # 零方差(逐日超额完全相同)→ t 无定义,给 NaN 不给 1e16:浮点下 std 不会精确为 0,用 1e-12 阈
    t = float(ex.mean() / (sd / np.sqrt(len(ex)))) if sd > 1e-12 else float("nan")
    return {"n_days": int(len(ex)), "n_med_per_day": float(np.median([d["n"] for d in days])),
            "mean_pp": float(np.mean([d["mean"] for d in days]) * 100),
            "excess_med_pp": float(ex.mean() * 100),
            "excess_mean_pp": float(np.mean([d["excess_mean"] for d in days]) * 100),
            "t": t, "hit_mean": float(np.mean([d["hit"] for d in days]))}


def rank_ic_day(fr: pd.DataFrame, ord_map: dict[str, int], ruler: str = MAIN) -> float | None:
    """逐日 Spearman(评级序数, 尺);<IC_MIN_CARDS 张卡或评级只有一档 → None。"""
    if fr is None or not ord_map or ruler not in fr.columns:
        return None
    ok = _ruler.entry_tradable(fr, ruler_name=ruler)
    fwd = pd.to_numeric(fr[ruler], errors="coerce")
    rows = [(ord_map[c], fwd.loc[c]) for c in ord_map if c in fwd.index and ok.loc[c] and pd.notna(fwd.loc[c])]
    if len(rows) < IC_MIN_CARDS:
        return None
    a = pd.Series([r[0] for r in rows], dtype=float)
    b = pd.Series([r[1] for r in rows], dtype=float)
    if a.nunique() < 2 or b.nunique() < 2:
        return None
    return float(a.corr(b, method="spearman"))


def verdict(row: dict) -> str:
    """§0 判读规则(只对主尺):正证据 / 显著负 / 未证 / 样本不足。"""
    n, ex, t = row.get("n_days", 0), row.get("excess_med_pp", float("nan")), row.get("t", float("nan"))
    if n < POS_MIN_DAYS:
        return "样本不足"
    if pd.notna(t) and ex > 0 and t >= POS_T:
        return "正证据"
    if pd.notna(t) and ex < 0 and t <= -POS_T:
        return "显著负"
    return "未证"


# ───────────────────────── 主流程 ─────────────────────────

def scan_days(scan_root: Path | None = None, since: str | None = None) -> list[str]:
    root = Path(scan_root) if scan_root else ws.scan_root()
    if not root.exists():
        return []
    days = sorted(p.name for p in root.iterdir() if p.is_dir() and p.name[:2] == "20" and len(p.name) == 10)
    return [d for d in days if not since or d >= since]


def run_census(since: str | None = None, scan_root: Path | None = None,
               lake_daily: Path | None = None) -> tuple[pd.DataFrame, dict, dict]:
    """→ (主表 家族×尺, 评级 rank-IC 行, meta)。"""
    root = Path(scan_root) if scan_root else ws.scan_root()
    days = scan_days(root, since)
    meta: dict = {"since": since, "scan_days": len(days), "computable_days": 0, "clipped": 0,
                  "unmeasured_days": [], "main_ruler": MAIN}
    if not days:
        return pd.DataFrame(), {}, meta
    P = lake_trade_days(lake_daily)
    first = days[0].replace("-", "")
    lo = max(0, max((i for i, d in enumerate(P) if d <= first), default=0))
    hi_anchor = days[-1].replace("-", "")
    hi = min(len(P), max((i for i, d in enumerate(P) if d <= hi_anchor), default=0) + FWD_LOOKAHEAD + 1)
    window = P[lo:hi]
    piv = load_lake_pivots(window, lake_daily)
    per: dict[tuple[str, str], list[dict]] = {}
    ics: list[float] = []
    for day in days:
        D = day.replace("-", "")
        fr = forward_frame(piv, window, D)
        if fr is None:
            meta["unmeasured_days"].append(day)
            continue
        fam, ord_map, _pin = families_for_day(root / day)
        any_stat = False
        for name, codes in fam.items():
            for r in RULERS:
                st = daily_stats(fr, codes, r)
                if st:
                    per.setdefault((name, r), []).append(st)
                    any_stat = True
        if any_stat:
            meta["computable_days"] += 1
            meta["clipped"] += int(fr.attrs.get("n_clipped", 0))
        else:
            meta["unmeasured_days"].append(day)
        ic = rank_ic_day(fr, ord_map)
        if ic is not None:
            ics.append(ic)
    rows = []
    for (name, r), lst in per.items():
        agg = aggregate(lst)
        rows.append({"family": name, "layer": name.split("·")[0], "ruler": r, **agg,
                     "verdict": verdict(agg) if r == MAIN else ""})
    table = pd.DataFrame(rows)
    ic_row = {}
    if ics:
        arr = np.array(ics, dtype=float)
        sd = arr.std(ddof=1) if len(arr) > 1 else 0.0
        ic_row = {"n_days": int(len(arr)), "ic_mean": float(arr.mean()),
                  "t": float(arr.mean() / (sd / np.sqrt(len(arr)))) if sd > 1e-12 else float("nan"),
                  "hit": float((arr > 0).mean())}
    return table, ic_row, meta


# ───────────────────────── 拒绝价值日读(prelude 汇总屏一行;2026-08-22 批 (c)) ─────────────────────────
# 立案:L4 ≥OW 卡 40 天只出 4 天,「门的价值(真实−影子)」在现尺上不可测;旧尺那张「+4.35pp /
# rank-IC +0.55」没有替代品。改用每天都量得到的读数:评级 rank-IC(评级序数 vs gap)+ 三门
# PASS−FAIL 超额 + finalist 超额。只给人看(prelude 汇总屏 + JSON),不进 brief、不喂 agent、不回注。
READOUT_LOOKBACK = 40
READOUT_MIN_DAYS = 5
_GATES = ("主力真在", "业绩真兑现", "估值不透支")


def rejection_readout(scan_root: Path | None = None, lake_daily: Path | None = None,
                      lookback: int = READOUT_LOOKBACK, today: str | None = None) -> dict:
    """最近 `lookback` 个扫描日(不含 `today`)的 L4 拒绝价值读数。可算日 < READOUT_MIN_DAYS → `{"status":"INSUFFICIENT"}`。"""
    root = Path(scan_root) if scan_root else ws.scan_root()
    days = [d for d in scan_days(root) if not today or d < today]
    if not days:
        return {"status": "NO_DATA", "lookback": lookback}
    since = days[-lookback] if len(days) >= lookback else days[0]
    table, ic, meta = run_census(since=since, scan_root=root, lake_daily=lake_daily)
    out: dict = {"status": "OK", "lookback": lookback, "since": since,
                 "computable_days": int(meta.get("computable_days", 0)), "main_ruler": MAIN}
    if out["computable_days"] < READOUT_MIN_DAYS:
        out["status"] = "INSUFFICIENT"
        return out
    out["rank_ic"] = ic or None
    main = table[table["ruler"] == MAIN].set_index("family") if len(table) else pd.DataFrame()

    def _row(f):
        if f in main.index:
            r = main.loc[f]
            return {"n_days": int(r["n_days"]), "excess_pp": float(r["excess_med_pp"]), "t": float(r["t"])}
        return None
    out["ge_ow_days"] = int(main.loc["L4·≥OW", "n_days"]) if "L4·≥OW" in main.index else 0
    out["finalist"] = _row("L3·finalist")
    out["gates"] = {}
    for g in _GATES:
        p_, f_ = _row(f"L4·门·{g}·PASS"), _row(f"L4·门·{g}·FAIL")
        out["gates"][g] = {"pass": p_, "fail": f_,
                           "pass_minus_fail_pp": (p_["excess_pp"] - f_["excess_pp"]) if (p_ and f_) else None}
    return out


def rejection_line(d: dict) -> str:
    """汇总屏一行(纯函数)。"""
    st = d.get("status")
    if st == "NO_DATA":
        return "无历史扫描日"
    if st == "INSUFFICIENT":
        return f"滚动{d.get('lookback')}日可算 {d.get('computable_days', 0)} <{READOUT_MIN_DAYS} → 样本不足"
    parts = [f"滚动{d['lookback']}日(可算 {d['computable_days']})"]
    ic = d.get("rank_ic")
    if ic:
        t = ic.get("t")
        parts.append(f"评级 rank-IC {ic['ic_mean']:+.2f}(t {t:.1f}·IC>0 {ic['hit']:.0%}·n {ic['n_days']})"
                     if t is not None and t == t else f"评级 rank-IC {ic['ic_mean']:+.2f}(n {ic['n_days']})")
    else:
        parts.append("评级 rank-IC 不可算(<5 卡/日或单一档)")
    parts.append(f"≥OW 出现 {d.get('ge_ow_days', 0)} 日")
    gl = []
    for g, v in (d.get("gates") or {}).items():
        pm = v.get("pass_minus_fail_pp")
        if pm is None:
            gl.append(f"{g[:2]} —")
        else:
            gl.append(f"{g[:2]} {pm:+.2f}({v['pass']['n_days']}/{v['fail']['n_days']})")
    if gl:
        parts.append("三门 PASS−FAIL:" + "/".join(gl) + "pp")
    f = d.get("finalist")
    if f:
        parts.append(f"finalist {f['excess_pp']:+.2f}pp(t {f['t']:.1f}·n {f['n_days']})")
    return " · ".join(parts) + " —— 只给人看,不喂任何 agent"


_LAYER_ORDER = ("L1", "L2", "L3", "L4", "E6", "📌")


def render(table: pd.DataFrame, ic_row: dict, meta: dict) -> str:
    lines = ["# 家族 × 尺 · edge 普查读数", "",
             f"- 扫描日 {meta.get('scan_days', 0)} · 可算日 {meta.get('computable_days', 0)}"
             f"(D+2 有收盘数据)· 剔数据错 {meta.get('clipped', 0)} 行 · 主尺 `{meta.get('main_ruler')}`"
             + (f" · since {meta['since']}" if meta.get("since") else ""), ""]
    if table is None or table.empty:
        lines += ["_无可算家族。_", "", FOOTNOTE]
        return "\n".join(lines) + "\n"
    main = table[table["ruler"] == MAIN]
    pos = main[main["verdict"] == "正证据"]
    neg = main[main["verdict"] == "显著负"]
    lines += [f"**H0(无家族有正证据):{'被推翻' if len(pos) else '未被推翻'}** —— "
              f"正证据 {len(pos)} 族 · 显著负 {len(neg)} 族 · 未证 {int((main['verdict'] == '未证').sum())} 族 · "
              f"样本不足 {int((main['verdict'] == '样本不足').sum())} 族", ""]
    if len(pos):
        lines += ["正证据:" + "、".join(f"`{f}`({e:+.2f}pp, t={t:.2f}, n={n})"
                                       for f, e, t, n in pos[["family", "excess_med_pp", "t", "n_days"]].itertuples(index=False)), ""]
    if len(neg):
        lines += ["显著负:" + "、".join(f"`{f}`({e:+.2f}pp, t={t:.2f}, n={n})"
                                       for f, e, t, n in neg[["family", "excess_med_pp", "t", "n_days"]].itertuples(index=False)), ""]
    for layer in _LAYER_ORDER:
        sub = table[table["layer"] == layer]
        if sub.empty:
            continue
        lines += [f"## {layer}", "",
                  "| 家族 | 尺 | n_days | 每日 n 中位 | 组内均值 pp | 超额(中位) pp | 超额(均值) pp | t | 胜率 | 判读 |",
                  "|---|---|---:|---:|---:|---:|---:|---:|---:|---|"]
        fams = sorted(sub["family"].unique())
        for f in fams:
            for r in RULERS:
                row = sub[(sub["family"] == f) & (sub["ruler"] == r)]
                if row.empty:
                    continue
                x = row.iloc[0]
                lines.append(f"| {f} | `{r}` | {int(x['n_days'])} | {x['n_med_per_day']:.0f} | "
                             f"{x['mean_pp']:+.2f} | {x['excess_med_pp']:+.2f} | {x['excess_mean_pp']:+.2f} | "
                             f"{x['t']:.2f} | {x['hit_mean']:.0%} | {x['verdict']} |")
        lines.append("")
    if ic_row:
        lines += ["## L4 评级 rank-IC(逐日 Spearman(评级序数, gap_c1_o2);📌 剔除)", "",
                  f"- n_days {ic_row['n_days']} · IC 均值 **{ic_row['ic_mean']:+.3f}** · t {ic_row['t']:.2f} · "
                  f"IC>0 的日占比 {ic_row['hit']:.0%}", ""]
    if meta.get("unmeasured_days"):
        lines += ["## 未算日(D+2 无收盘数据 / 无任何家族产物)", "",
                  "- " + "、".join(meta["unmeasured_days"]), ""]
    lines += [FOOTNOTE, "",
              f"_判读规则(§0 预注册):正证据 = 超额(中位)>0 ∧ t≥{POS_T} ∧ n_days≥{POS_MIN_DAYS};"
              f"显著负 = 超额<0 ∧ t≤−{POS_T} ∧ n_days≥{POS_MIN_DAYS};其余未证;n_days<{POS_MIN_DAYS} 样本不足。_"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", type=str, default=None, help="只算 ≥ 此日的扫描日(YYYY-MM-DD)")
    ap.add_argument("--out", type=str, default=None)
    a = ap.parse_args(argv)
    table, ic_row, meta = run_census(since=a.since)
    out = Path(a.out) if a.out else ws.reports_root() / "research" / "edge_census.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(table, ic_row, meta), encoding="utf-8")
    (out.parent / "_edge_census.json").write_text(json.dumps(
        {"meta": meta, "rank_ic": ic_row,
         "rows": json.loads(table.to_json(orient="records", force_ascii=False)) if len(table) else []},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[done] → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
