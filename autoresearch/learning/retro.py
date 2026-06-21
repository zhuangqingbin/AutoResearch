#!/usr/bin/env python3
"""闭环复盘 retro · 归因前一日 scan 报告 vs T+1 已实现涨跌(确定性,零 LLM)。

仅挂 scan-market。用当日已实现 `fwd_1_oo`(T+1 开到开,复用 factor_lab 口径:D 收盘信号→
D+1 开盘买、剔 D+1 一字板)检验 D 的报告,把每只股票分桶:抓到 / L2-L3 误判 / 漏在 L1 /
漏在 L0 / 误买。产出 attribution.csv + retro_input.md,喂给 scan-retro skill 做 Claude 诊断
(系统性病因 + 自动重标定 + 经验/建议)。归因数学纯函数、可离线自测;取数复用 factor_lab。

用法:
  uv run --no-sync python scripts/retro.py --selftest
  uv run --no-sync python scripts/retro.py attribute 2026-06-19      # 单日(需 fwd 已实现)
  uv run --no-sync python scripts/retro.py pending                   # 列未复盘日
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from autoresearch.agents.utils.rating import parse_rating

_BUY = ("Overweight", "Buy")


# ───────────────────────── 纯函数:分桶 + 阶段统计(可离线自测) ─────────────────────────


def _as_bool(s: pd.Series) -> pd.Series:
    def one(v):
        if isinstance(v, str):
            return v.strip().lower() in ("true", "1", "yes", "是")
        try:
            return bool(v) and v == v  # 非 NaN
        except (TypeError, ValueError):
            return False
    return s.map(one)


def attribute_frame(l1: pd.DataFrame, realized: pd.DataFrame, buylist: dict,
                    abs_thresh: float = 0.03, top_q: float = 0.9, bot_q: float = 0.1) -> pd.DataFrame:
    """全市场已实现收益 × L1 全打分面板 × 报告买单 → 每只一个 bucket。纯函数(无 IO)。

    赢家 = 可交易 universe 内 fwd_1_oo ≥ 九分位 ∧ ≥ abs_thresh。
    """
    l1 = l1.copy()
    l1["code"] = l1["code"].astype(str).str.zfill(6)
    realized = realized.copy()
    realized["code"] = realized["code"].astype(str).str.zfill(6)
    bl = {str(k).zfill(6): v for k, v in buylist.items()}

    m = realized.merge(l1, on="code", how="left")               # base = 全市场(含漏在 L0 的)
    m["in_l1"] = m["composite"].notna() if "composite" in m.columns else m.get("rank").notna()
    m["recalled_flag"] = _as_bool(m["recalled"]) if "recalled" in m.columns else False
    m["rating"] = m["code"].map(bl)
    m["bought"] = m["rating"].isin(_BUY)
    m["tradable"] = m["buyable"].fillna(True) & m["fwd_1_oo"].notna()

    trad = m[m["tradable"]]
    hi = trad["fwd_1_oo"].quantile(top_q) if len(trad) else float("nan")
    lo = trad["fwd_1_oo"].quantile(bot_q) if len(trad) else float("nan")
    m["winner"] = m["tradable"] & (m["fwd_1_oo"] >= hi) & (m["fwd_1_oo"] >= abs_thresh)

    def bucket(r) -> str:
        if r["winner"] and r["bought"]:
            return "caught"
        if r["winner"] and r["recalled_flag"] and not r["bought"]:
            return "recalled_cut"
        if r["winner"] and r["in_l1"] and not r["recalled_flag"]:
            return "missed_l1"
        if r["winner"] and not r["in_l1"]:
            return "missed_l0"
        if r["bought"] and r["tradable"] and r["fwd_1_oo"] <= lo:
            return "false_positive"
        return ""

    m["bucket"] = m.apply(bucket, axis=1)
    return m


def flag_news_pop(attr: pd.DataFrame, gap_thresh: float = 0.07) -> pd.DataFrame:
    """赢家里"隔夜大跳空"(gap_d1 ≥ 阈值)= 多为消息/事件脉冲,不可预测 →

    标 news_pop,诊断与重标定**排除**之(别拿不可预测脉冲惩罚打分)。纯函数。
    """
    attr = attr.copy()
    if "gap_d1" in attr.columns:
        attr["news_pop"] = attr["winner"] & (pd.to_numeric(attr["gap_d1"], errors="coerce") >= gap_thresh)
    else:
        attr["news_pop"] = False
    return attr


def stage_stats(attr: pd.DataFrame) -> dict:
    """漏斗各段对赢家的存活率 + 买单命中率 + 当日 composite IC。纯函数。"""
    winners = attr[attr["winner"]]
    nW = len(winners)
    bought = attr[attr["bought"]]
    nB = len(bought)
    res = {
        "n_universe_realized": int(attr["tradable"].sum()),
        "n_winners": int(nW),
        "winners_in_l1": int(winners["in_l1"].sum()),
        "winners_recalled": int(winners["recalled_flag"].sum()),
        "winners_bought": int(winners["bought"].sum()),
        "buylist_n": int(nB),
        "buylist_hit": int(bought["winner"].sum()),
        "buylist_fp": int((attr["bucket"] == "false_positive").sum()),
        "buckets": {k: int(v) for k, v in attr["bucket"].value_counts().items() if k},
        "n_news_pop": int(attr.get("news_pop", pd.Series([], dtype=bool)).fillna(False).sum()),
    }
    res["n_winners_systematic"] = res["n_winners"] - res["n_news_pop"]   # 剔消息脉冲后的"可归因漏判"基数
    res["winner_to_l1"] = round(res["winners_in_l1"] / nW, 3) if nW else None
    res["winner_to_buylist"] = round(res["winners_bought"] / nW, 3) if nW else None
    res["buylist_hitrate"] = round(res["buylist_hit"] / nB, 3) if nB else None
    sub = attr[attr["tradable"] & attr.get("composite", pd.Series(dtype=float)).notna()]
    if len(sub) >= 30:
        res["day_ic_composite"] = round(sub["composite"].rank().corr(sub["fwd_1_oo"].rank()), 4)
    else:
        res["day_ic_composite"] = None
    return res


# ───────────────────────── IO:买单 / 已实现收益 / 待复盘日 ─────────────────────────


def _report_dir_for(date: str, report_root: Path) -> Path | None:
    """定位数据日 analysis_date=date 的已发布报告目录(最新一轮)。

    新布局目录名 = **运行时刻**(与数据日解耦),数据日记在 `manifest.json` → 按 `analysis_date` 匹配;
    老布局目录名 = 数据日(无 manifest)→ glob `<date>_*` 兜底。都取目录名最大(= 最近运行)。
    """
    compact = date.replace("-", "")
    cands: set[Path] = set()
    for mf in report_root.glob("*/manifest.json"):
        try:
            if json.loads(mf.read_text(encoding="utf-8")).get("analysis_date") == date:
                cands.add(mf.parent)
        except (json.JSONDecodeError, OSError):
            continue
    cands |= set(report_root.glob(f"{compact}_*"))                 # 老布局:目录名即数据日
    dirs = sorted((p for p in cands if (p / "details").is_dir()), key=lambda p: p.name)
    return dirs[-1] if dirs else None


def _buylist(date: str, report_root: Path | None = None) -> dict[str, str]:
    """读数据日=date 的已发布报告 details/<名称>.md → {code: 五档评级}。

    目录名现在是运行时刻(数据日在 manifest),由 `_report_dir_for` 解析定位;发布层卡名是**名称**,
    code 从卡内标题 `# 决策卡 — <code> <名称>` 取(复用 parse_rating 提评级)。
    """
    rdir = _report_dir_for(date, report_root or Path("reports/scan"))
    if rdir is None:
        return {}
    out: dict[str, str] = {}
    for md in (rdir / "details").glob("*.md"):
        text = md.read_text(encoding="utf-8")
        m = re.search(r"决策卡\s*[—\-]\s*(\d{6})", text)
        if m:
            out[m.group(1).zfill(6)] = parse_rating(text)
    return out


def realized_returns(date: str, fwd: int = 10) -> pd.DataFrame:
    """全市场 D 的已实现 fwd_1_oo/fwd_5_oc + buyable(复用 factor_lab;按需拉 D..D+fwd 的 daily)。

    fwd 未实现(D+2 交易日还没到)→ 返回空(供 pending 判定)。
    """
    import factor_lab as fl

    from autoresearch.data.tushare_source import _trade_days

    cols = ["code", "fwd_1_oo", "fwd_5_oc", "buyable", "gap_d1"]
    pro = fl._pro()
    d0 = date.replace("-", "")
    today = datetime.now().strftime("%Y%m%d")
    fdays = _trade_days(pro, d0, today)
    if not fdays or fdays[0] != d0 or len(fdays) < 3:     # D 非交易日 / fwd 未实现
        return pd.DataFrame(columns=cols)
    P = fdays[:fwd + 2]
    for d in P:
        fl._cache("daily", d, fl._fetch(pro, "daily", d))
    piv = fl.load_price_pivots(P)
    fr = fl.forward_returns(piv, P, d0, fwd).reset_index()
    fr = fr.rename(columns={fr.columns[0]: "code"})
    op, cl = piv["open"], piv["close"]
    gap = (op[P[1]] / cl[P[0]] - 1.0).reset_index()       # D+1 开盘相对 D 收盘的隔夜跳空
    gap.columns = ["code", "gap_d1"]
    fr = fr.merge(gap, on="code", how="left")
    fr["code"] = fr["code"].astype(str).str.zfill(6)
    return fr[[c for c in cols if c in fr.columns]]


def pending_days(today: str | None = None, scan_root: Path | None = None,
                 report_root: Path | None = None) -> list[str]:
    """未复盘 scan 日:有 L1 面板 + 有报告 + 无 retro/done.json + D 的 fwd 已实现。"""
    import factor_lab as fl

    from autoresearch.data.tushare_source import _trade_days

    today = today or datetime.now().strftime("%Y-%m-%d")
    scan_root = scan_root or Path("context/scan")
    report_root = report_root or Path("reports/scan")
    if not scan_root.exists():
        return []
    pro = fl._pro()
    cal = _trade_days(pro, "20240101", today.replace("-", ""))   # 日历已截到 today
    pos = {d: i for i, d in enumerate(cal)}
    out = []
    for dd in sorted(p for p in scan_root.iterdir() if p.is_dir()):
        date = dd.name
        if not (dd / "L1_scored_full.csv").exists():
            continue
        if (dd / "retro" / "done.json").exists():
            continue
        if _report_dir_for(date, report_root) is None:           # 无已发布报告(目录名=运行日,按 manifest 定位)
            continue
        i = pos.get(date.replace("-", ""))
        if i is not None and i + 2 < len(cal):                   # D+2 交易日 ≤ today → fwd 已实现
            out.append(date)
    return out


# ───────────────────────── 编排:attribute / retro_input / done ─────────────────────────

_KEEP = ["code", "name", "industry", "bucket", "winner", "news_pop", "fwd_1_oo", "fwd_5_oc",
         "gap_d1", "rank", "recalled_flag", "composite", "score_momentum", "score_fund_main",
         "score_chip", "pct_60d", "main_net_ratio", "winner_rate", "price_to_cost", "rsi6", "rating"]


def attribute(date: str, scan_root: Path | None = None, report_root: Path | None = None,
              abs_thresh: float = 0.03) -> pd.DataFrame:
    """单日归因 → 写 context/scan/<date>/retro/attribution.csv,返回全帧。"""
    scan_root = scan_root or Path("context/scan")
    sdir = scan_root / date
    l1 = pd.read_csv(sdir / "L1_scored_full.csv", dtype={"code": str})
    realized = realized_returns(date)
    if realized.empty:
        raise RuntimeError(f"{date} 的 fwd 未实现 / 无价格,暂不能复盘")
    attr = attribute_frame(l1, realized, _buylist(date, report_root), abs_thresh=abs_thresh)
    attr = flag_news_pop(attr)                       # 标隔夜跳空脉冲(诊断/重标定排除)
    outdir = sdir / "retro"
    outdir.mkdir(parents=True, exist_ok=True)
    attr[[c for c in _KEEP if c in attr.columns]].to_csv(outdir / "attribution.csv", index=False)
    return attr


def write_retro_input(date: str, attr: pd.DataFrame, scan_root: Path | None = None) -> Path:
    """把 stage_stats + 漏判赢家 top(带因子行)+ 选中对照写成 retro_input.md(喂诊断)。"""
    scan_root = scan_root or Path("context/scan")
    st = stage_stats(attr)
    lines = [f"# retro 输入 — {date}\n", "## 漏斗命中(对赢家)",
             f"- 当日可交易 universe:{st['n_universe_realized']};**赢家(前10%∧≥3%):{st['n_winners']}**",
             f"- 赢家进入 L1 召回池:{st['winners_in_l1']}/{st['n_winners']} "
             f"(到召回 {st['winner_to_l1']});被买单抓到:{st['winners_bought']}/{st['n_winners']} "
             f"(到买单 {st['winner_to_buylist']})",
             f"- 买单 {st['buylist_n']} 只,命中赢家 {st['buylist_hit']}(命中率 {st['buylist_hitrate']}),"
             f"误买(跌入底10%){st['buylist_fp']}",
             f"- 分桶:{st['buckets']};当日 composite IC(vs fwd_1_oo):{st['day_ic_composite']}\n"]

    def _tbl(df: pd.DataFrame, cols: list[str]) -> list[str]:
        cols = [c for c in cols if c in df.columns]
        head = "| " + " | ".join(cols) + " |"
        sep = "|" + "|".join(["---"] * len(cols)) + "|"
        rows = ["| " + " | ".join(str(r[c]) for c in cols) + " |" for _, r in df.iterrows()]
        return [head, sep, *rows]

    fcols = ["code", "name", "industry", "fwd_1_oo", "rank", "composite", "score_momentum",
             "main_net_ratio", "winner_rate", "price_to_cost", "rsi6", "pct_60d"]
    for label, bk in [("漏在 L0(门槛误杀)", "missed_l0"), ("漏在 L1(权重压低)", "missed_l1"),
                      ("L2-L3 误判(召回了却 cut)", "recalled_cut")]:
        sub = attr[attr["bucket"] == bk].sort_values("fwd_1_oo", ascending=False).head(15)
        lines += [f"\n## {label} — {len(attr[attr['bucket'] == bk])} 只(top 15)"]
        lines += _tbl(sub, fcols) if len(sub) else ["_无_"]
    caught = attr[attr["bucket"] == "caught"].sort_values("fwd_1_oo", ascending=False).head(10)
    lines += ["\n## 对照:抓到的赢家(caught, top 10)"]
    lines += _tbl(caught, fcols) if len(caught) else ["_无_"]

    try:                                   # F · 逐阶段 agent edge(staging 缺 / fwd 未实现则跳过)
        import autoresearch.learning.stage_eval as stage_eval
        lines += stage_eval.render_stage_eval(stage_eval.evaluate(date, scan_root=scan_root))
    except Exception as e:  # noqa: BLE001
        lines += [f"\n## 各阶段 agent edge\n_stage_eval 跳过:{e}_"]

    try:                                   # E2 · 够格升『程序性硬门』的经验(给它写 guard → self_review 拦)
        import autoresearch.learning.feedback_store as fs
        cands = fs.promotion_candidates()
        if cands:
            lines += ["\n## 够格升硬门的经验(E2:反复强化、还没 guard → 给它写 {field,op,value} 升 self_review 硬门)"]
            lines += [f"- `{c['id']}` ×{c.get('reinforce_count')} conf {c.get('confidence')}:{c.get('rule')}"
                      for c in cands]
    except Exception:  # noqa: BLE001
        pass

    p = scan_root / date / "retro" / "retro_input.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def _sha8(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()[:8]


def top_weight_changes(before: dict, after: dict, n: int = 8) -> list[dict]:
    """__global__ 组权重的最大绝对变化(before/after 为 {group: weight});纯函数。"""
    rows = [{"group": k, "before": round(float(before.get(k, 0.0)), 5),
             "after": round(float(after.get(k, 0.0)), 5),
             "delta": round(float(after.get(k, 0.0)) - float(before.get(k, 0.0)), 5)}
            for k in (set(before) | set(after))]
    return sorted(rows, key=lambda r: abs(r["delta"]), reverse=True)[:n]


def recalibrate_and_log(retro_date: str, cap_floor: float = 30.0, k: float = 200.0) -> dict:
    """半自动闭环的"自动落地":factor_lab.calibrate(多日滚动+收缩)重写 weights.json + 审计 changelog。

    快照旧权重(weights.<sha>.json,供 Phase 3 回滚)→ calibrate → log_change(前后 sha + top 变化)。
    """
    import factor_lab as fl

    import autoresearch.learning.feedback_store as fs
    wp = Path("context/factor_lab/weights.json")
    before_raw = wp.read_bytes() if wp.exists() else b"{}"
    before_sha = fs.snapshot_weights() or _sha8(before_raw)   # 快照留底(Phase 3 回滚)
    fl.calibrate(cap_floor=cap_floor, k=k)                    # 重写 weights.json(多日面板,绝非单日)
    after_raw = wp.read_bytes()
    before, after = json.loads(before_raw), json.loads(after_raw)
    tc = top_weight_changes(before.get("weights", {}).get("__global__", {}),
                            after.get("weights", {}).get("__global__", {}))
    after_sha, n_dates = _sha8(after_raw), int(after.get("meta", {}).get("n_dates", 0))
    fs.log_change(retro_date, before_sha, after_sha, tc, n_dates)
    return {"before_sha": before_sha, "after_sha": after_sha, "top_changes": tc, "n_dates": n_dates}


def mark_done(date: str, summary: dict | None = None, scan_root: Path | None = None) -> None:
    scan_root = scan_root or Path("context/scan")
    p = scan_root / date / "retro" / "done.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"date": date, "ts": datetime.now().isoformat(timespec="seconds"),
                             "summary": summary or {}}, ensure_ascii=False), encoding="utf-8")


# ───────────────────────── 离线自测(分桶 + 阶段统计) ─────────────────────────


def _selftest() -> int:
    fails: list[str] = []
    # 构造全市场已实现:4 赢家(0.10)、1 误买(-0.09)、20 噪声(~0)
    rows = []
    rows += [{"code": c, "fwd_1_oo": 0.10, "fwd_5_oc": 0.12, "buyable": True}
             for c in ("000001", "000002", "000003", "000004")]
    rows += [{"code": "000005", "fwd_1_oo": -0.09, "fwd_5_oc": -0.10, "buyable": True}]
    rows += [{"code": f"0001{i:02d}", "fwd_1_oo": (i - 10) * 0.002, "fwd_5_oc": 0.0, "buyable": True}
             for i in range(20)]
    realized = pd.DataFrame(rows)
    # L1 面板:000001-3 在 universe(2 recalled),000005 在 universe 且被买,000004 不在(漏 L0)
    l1 = pd.DataFrame([
        {"code": "000001", "name": "抓到", "industry": "电子", "rank": 5, "recalled": True, "composite": 80},
        {"code": "000002", "name": "误判", "industry": "电子", "rank": 8, "recalled": True, "composite": 75},
        {"code": "000003", "name": "漏L1", "industry": "医药", "rank": 1500, "recalled": False, "composite": 40},
        {"code": "000005", "name": "误买", "industry": "电子", "rank": 12, "recalled": True, "composite": 70},
    ])
    buylist = {"000001": "Overweight", "000005": "Overweight", "000002": "Hold"}
    attr = attribute_frame(l1, realized, buylist)

    def bk(code):
        return attr.loc[attr["code"] == code, "bucket"].iloc[0]

    expect = {"000001": "caught", "000002": "recalled_cut", "000003": "missed_l1",
              "000004": "missed_l0", "000005": "false_positive"}
    for code, want in expect.items():
        got = bk(code)
        if got != want:
            fails.append(f"{code} 桶错: 期望 {want} 得 {got}")

    st = stage_stats(attr)
    checks = {"n_winners": 4, "winners_in_l1": 3, "winners_recalled": 2, "winners_bought": 1,
              "buylist_n": 2, "buylist_hit": 1, "buylist_fp": 1}
    for k, v in checks.items():
        if st[k] != v:
            fails.append(f"stage_stats[{k}] 期望 {v} 得 {st[k]}")
    if st["buckets"].get("caught") != 1 or st["buckets"].get("missed_l0") != 1:
        fails.append(f"buckets 计数错: {st['buckets']}")

    # 边界:realized 为空 → attribute_frame 不崩(无赢家)
    empty = attribute_frame(l1, pd.DataFrame(columns=["code", "fwd_1_oo", "fwd_5_oc", "buyable"]), {})
    if len(empty[empty["winner"]]) != 0:
        fails.append("空 realized 不应有赢家")

    # 重标定簿记:权重变化排序 + sha
    tc = top_weight_changes({"momentum": 0.026, "value": -0.010, "tech": 0.026},
                            {"momentum": 0.031, "value": -0.010, "tech": 0.020})
    if tc[0]["group"] not in ("momentum", "tech") or abs(tc[0]["delta"]) < 0.005:
        fails.append(f"top_weight_changes 排序错: {tc[:2]}")
    if next(r for r in tc if r["group"] == "value")["delta"] != 0.0:
        fails.append("未变的组 delta 应为 0")
    if _sha8(b"abc") != hashlib.sha1(b"abc").hexdigest()[:8]:
        fails.append("_sha8 错")

    # 消息脉冲:隔夜大跳空赢家被标 news_pop,普通赢家 / 非赢家不标
    npf = flag_news_pop(pd.DataFrame({"winner": [True, True, False], "gap_d1": [0.09, 0.01, 0.09]}))
    if list(npf["news_pop"]) != [True, False, False]:
        fails.append(f"flag_news_pop 错: {list(npf['news_pop'])}")

    if fails:
        print("SELFTEST ❌")
        for f in fails:
            print("  -", f)
        return 1
    print("SELFTEST ✅  分桶(caught/recalled_cut/missed_l1/missed_l0/false_positive)+ 阶段统计 全过")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--selftest" in sys.argv:
        return _selftest()
    if args and args[0] == "pending":
        print("\n".join(pending_days()) or "(无待复盘日)")
        return 0
    if len(args) >= 2 and args[0] == "attribute":
        attr = attribute(args[1])
        write_retro_input(args[1], attr)
        st = stage_stats(attr)
        print(f"[retro] {args[1]} 赢家 {st['n_winners']},买单命中 {st['buylist_hit']}/{st['buylist_n']},"
              f"漏 {st['buckets'].get('missed_l1', 0)+st['buckets'].get('missed_l0', 0)},"
              f"误判 {st['buckets'].get('recalled_cut', 0)} → context/scan/{args[1]}/retro/")
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
