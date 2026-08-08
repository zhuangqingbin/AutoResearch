#!/usr/bin/env python3
"""winner-capture SLO —— 菜单质量的主 SLO(确定性,零 LLM,不承诺 alpha)。

design: docs/specs/2026-08-03-scan-next-wave-brainstorm-design.md §3.1 O1

**先说天花板**(§3.0 的坦白):L2 全 zoo 模型 OOS rank-IC 负、composite-top200 四年 ≈0
→ L2 不预测、只做菜单。所以本模块是**守菜单质量**的工程,不承诺 alpha;0 买根因的修复
主战场在 L1 权重/召回线。

## 三件必须先钉死的事

1. **winner 定义**(§3.1 第一句)。主尺 = `ruler.MAIN_RULER`(现 `gap_c1_o2`;2026-08-05 换尺
   前是 `fwd_2_oc`),同时满足:全市场 top-decile、绝对收益阈、**入场腿**可买/可交易
   (旗随尺走,`entry_flag_for()` 单点选:`gap_c1_o2` → D+1 收盘 `buyable_c1`,`fwd_2_oc` →
   D+1 开盘 `buyable`)。并列按 `>=` 一并计入;停牌/封板不可买 → 不算 winner。
   落盘的 `winner_definition` 串由 `MAIN_RULER`/`entry_flag_for()` **现算**(见下方常量),
   所以重跑报表即自动跟随换尺——旧报表里的旧串是当时的真值,不改写。
   `pinned` 分列不混算 —— **不得**把 retro 的复合 winner 和「纯 top-decile」混叫一个标签,
   所以两套口径在这里各有各的 `winner_definition` 字符串,随读数一起落盘。

2. **端到端 vs 条件召回**(§3.1 第二段)。只报 `WC_L2_all` 会把 L0/L1 的漏失全部归责给
   L2。故三层同时出:

     WC_L1_all       = |L1 召回 ∩ winners| / |winners|          端到端
     WC_L2_all       = |L2 ∩ winners| / |winners|               端到端
     WC_L1_given_L0  = |L1 ∩ winners| / |L0 池内 winners|       条件
     WC_L2_given_L1  = |L2 ∩ winners| / |L1 召回内 winners|     条件

   **K 用实际值**(含/不含 pinned 分列),不写死 L1-1001/L2-203 —— 那两个数是某一天的
   快照,写死等于把当天的偶然当成契约。

3. **报警线**(§3.1 末段)。用当日**之前**的 expanding P25,不是全期分位 ——
   全期分位含未来,回看时永远「没报警」。历史不足 `min_history` → 不报警(而不是拿
   两个点定分位)。

## 一句必须跟着读数走的话

winner capture 是**主** SLO,不是唯一指标:扩大 K 或集中追热点都能被动做高它。
所以 `guards` 里同时给行业集中度、lane 覆盖、selection_reason 分布与日间稳定性,
`render` 把这句话印在表下面。

  uv run --no-sync python -m autoresearch.scan.l2_slo
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from autoresearch.common import stats as st
from autoresearch.common.ruler import MAIN_RULER, entry_flag_for, entry_tradable

SCHEMA_VERSION = 1
DEFAULT_ROOT = Path("context/scan")
OUT_JSON = Path("reports/scan/l2_slo.json")
OUT_MD = Path("reports/scan/l2_slo.md")

TOP_DECILE = 0.90            # 全市场 top-decile(与 retro.attribute_frame 同源)
ABS_THRESHOLD = 0.02         # 绝对收益阈:光排进前 10% 但只涨 0.1% 不算赢
MIN_HISTORY = 10             # expanding P25 的最小历史日数

WINNER_DEFINITION = (
    f"{MAIN_RULER} ≥ 全市场可交易成熟票 P{int(TOP_DECILE * 100)} "
    f"∧ {MAIN_RULER} ≥ {ABS_THRESHOLD:+.2%} ∧ 入场腿可执行({entry_flag_for()})∧ 可交易(tradable);"
    "并列按 ≥ 一并计入。**不是** retro 的复合 winner,两者不得混叫一个标签")


def _read(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    try:
        return pd.read_csv(path, dtype={"code": str})
    except Exception:  # noqa: BLE001
        return None


def _codes(frame: pd.DataFrame | None) -> set[str]:
    if frame is None or not len(frame) or "code" not in frame.columns:
        return set()
    return set(frame["code"].astype(str).str.split(".").str[0].str.zfill(6))


def day_winners(attr: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    """当日 winner 集合(本模块自己的口径,不借用 attribution 的 `winner` 列)。

    为什么不复用那一列:`retro.attribute_frame` 的 winner 是**复合**定义(含 bucket 语义),
    §3.1 明令「不得把它和纯 top-decile 混叫一个标签」。这里重算,并把定义字符串带出去。
    """
    a = attr.copy()
    a["code"] = a["code"].astype(str).str.zfill(6)
    a[MAIN_RULER] = pd.to_numeric(a.get(MAIN_RULER), errors="coerce")
    # C1 修复(final-review 2026-08-08):入场腿旗跟随 MAIN_RULER 选(entry_tradable 单点),
    # 不是硬编码 "buyable"——换尺后 fwd_2_oc 的旧旗(D+1 开盘)与 gap_c1_o2 的入场腿(D+1
    # 收盘)不等价,旧旗会把「收盘封死买不进」的票误留在 winner 集合里。
    buyable = entry_tradable(a)
    tradable = a["tradable"].fillna(True).astype(bool) if "tradable" in a.columns \
        else pd.Series(True, index=a.index)
    eligible = buyable & tradable & a[MAIN_RULER].notna()
    if not eligible.any():
        return a.iloc[0:0], WINNER_DEFINITION
    cutoff = float(a.loc[eligible, MAIN_RULER].quantile(TOP_DECILE))
    is_winner = eligible & (a[MAIN_RULER] >= cutoff) & (a[MAIN_RULER] >= ABS_THRESHOLD)
    return a[is_winner], WINNER_DEFINITION


def _capture(numer: set[str], denom: set[str]) -> float | None:
    """分母为空 → `None`(**不是 0、也不是 1**)。那天没有赢家可捞,这个比率不存在。"""
    return round(len(numer & denom) / len(denom), 6) if denom else None


def day_slo(scan_dir: Path | str) -> dict | None:
    """单日三层 winner capture + 守卫。缺 attribution/L1 产物 → `None`(presence-gated)。"""
    day = Path(scan_dir)
    attr = _read(day / "retro" / "attribution.csv")
    if attr is None or not len(attr) or "code" not in attr.columns:
        return None
    winners_frame, definition = day_winners(attr)
    winners = _codes(winners_frame)

    l0 = _codes(_read(day / "L1_scored_full.csv"))
    l1 = _codes(_read(day / "L1_recall_top1000.csv"))
    l2_frame = _read(day / "L2_gbdt_top200.csv")
    l2 = _codes(l2_frame)
    if not l0 and not l1 and not l2:
        return None

    pinned: set[str] = set()
    if l2_frame is not None and "pinned" in l2_frame.columns:
        pinned = _codes(l2_frame[l2_frame["pinned"].fillna(False).astype(bool)])
    elif l2_frame is not None and "selection_reason" in l2_frame.columns:
        pinned = _codes(l2_frame[l2_frame["selection_reason"] == "pinned"])

    winners_in_l0 = winners & l0
    winners_in_l1 = winners & l1
    return {
        "date": day.name,
        "winner_definition": definition,
        "n_winners": len(winners),
        "n_winners_in_l0": len(winners_in_l0),
        "n_winners_in_l1": len(winners_in_l1),
        # K 用实际值,不写死 —— 含 pinned 与不含 pinned 分列
        "k_l0": len(l0), "k_l1": len(l1), "k_l2": len(l2),
        "k_l2_ex_pinned": len(l2 - pinned), "n_pinned": len(pinned),
        # 端到端(分母 = 全部 winner)
        "wc_l1_all": _capture(l1, winners),
        "wc_l2_all": _capture(l2, winners),
        "wc_l2_all_ex_pinned": _capture(l2 - pinned, winners),
        # 条件召回(分母 = 上一层池内的 winner)—— 避免把 L0/L1 的漏失归责给 L2
        "wc_l1_given_l0": _capture(l1, winners_in_l0),
        "wc_l2_given_l1": _capture(l2, winners_in_l1),
        "guards": _guards(l2_frame),
    }


def _guards(l2_frame: pd.DataFrame | None) -> dict:
    """守卫向量 —— winner capture 可以靠扩 K / 追热点被动做高,守卫是它的反面约束。"""
    if l2_frame is None or not len(l2_frame):
        return {"n": 0}
    out: dict = {"n": int(len(l2_frame))}
    if "industry" in l2_frame.columns:
        counts = l2_frame["industry"].astype(str).value_counts()
        out["top_industry"] = str(counts.index[0])
        out["top_industry_share"] = round(float(counts.iloc[0] / len(l2_frame)), 6)
        out["n_industries"] = int(counts.size)
    if "selection_reason" in l2_frame.columns:
        out["selection_reason"] = {
            str(k): int(v) for k, v in
            l2_frame["selection_reason"].value_counts().items()}
    if "recall_channels" in l2_frame.columns:
        lanes = set()
        for value in l2_frame["recall_channels"].fillna("").astype(str):
            lanes |= set(value.split("|")) - {"", "(backfill)", "pinned"}
        out["lane_coverage"] = sorted(lanes)
        out["n_lanes"] = len(lanes)
    return out


def collect(scan_root: Path | str | None = None) -> pd.DataFrame:
    root = Path(scan_root or DEFAULT_ROOT)
    if not root.exists():
        return pd.DataFrame()
    rows = [r for day in sorted(p for p in root.iterdir()
                                if p.is_dir() and p.name[:2] == "20")
            if (r := day_slo(day)) is not None]
    return pd.DataFrame(rows)


ALARM_METRICS = ("wc_l2_all", "wc_l2_given_l1", "wc_l1_given_l0")


def alarms(daily: pd.DataFrame) -> pd.DataFrame:
    """逐日报警线 —— 当日**之前**的 expanding P25(全期分位含未来,回看永不报警)。"""
    if not len(daily):
        return pd.DataFrame()
    out = daily[["date"]].copy()
    for metric in ALARM_METRICS:
        if metric not in daily.columns:
            continue
        series = daily[metric].tolist()
        line = st.expanding_p25(
            [0.0 if v is None or pd.isna(v) else float(v) for v in series],
            min_history=MIN_HISTORY)
        out[f"{metric}_p25"] = line
        out[f"{metric}_alarm"] = [
            (None if threshold is None or v is None or pd.isna(v)
             else bool(float(v) < threshold))
            for v, threshold in zip(series, line, strict=True)]
    return out


def build(scan_root: Path | str | None = None) -> dict:
    daily = collect(scan_root)
    alarm = alarms(daily)
    summary: dict = {
        "schema_version": SCHEMA_VERSION,
        "winner_definition": WINNER_DEFINITION,
        "n_days": int(len(daily)),
        "min_history_for_alarm": MIN_HISTORY,
        "not_the_only_metric": (
            "winner capture 是主 SLO,不是唯一指标 —— 扩大 K 或集中追热点都能被动做高它;"
            "必须与 guards(行业集中度 / lane 覆盖 / selection_reason 分布)一起读"),
    }
    for metric in ("wc_l1_all", "wc_l2_all", "wc_l2_all_ex_pinned",
                   "wc_l1_given_l0", "wc_l2_given_l1"):
        if metric not in daily.columns or not len(daily):
            summary[metric] = None
            continue
        interval = st.date_cluster_bootstrap(daily, metric, date_col="date")
        summary[metric] = interval.as_dict()
    summary["daily"] = daily.to_dict("records") if len(daily) else []
    summary["alarms"] = alarm.to_dict("records") if len(alarm) else []
    summary["maturity"] = st.maturity_verdict(
        scan_days=int(len(daily)), subgroup_n=None, unique_n=None,
        regimes=None).as_dict()
    return summary


def render(payload: dict) -> str:
    lines = [
        "# L2 winner-capture SLO(菜单质量,不承诺 alpha)",
        "",
        f"> **winner 定义**:{payload['winner_definition']}",
        "",
        f"- 成熟扫描日 **{payload['n_days']}** · 成熟度 "
        f"{payload['maturity']['status']}"
        + ("" if not payload["maturity"]["missing"]
           else "(缺:" + "、".join(payload["maturity"]["missing"]) + ")"),
        "",
        "## 三层曲线(端到端 vs 条件召回)",
        "",
        "| 指标 | 含义 | 均值 | 区间 | 聚簇 |",
        "|---|---|---:|---|---:|",
    ]
    labels = {
        "wc_l1_all": "端到端:L1 召回捞到的赢家占全部赢家",
        "wc_l2_all": "端到端:L2 菜单捞到的赢家占全部赢家",
        "wc_l2_all_ex_pinned": "同上,剔除 pinned(保送不算 L2 的功劳)",
        "wc_l1_given_l0": "条件:L0 池内赢家里 L1 召回捞到的",
        "wc_l2_given_l1": "条件:L1 召回内赢家里 L2 留下的",
    }
    for metric, label in labels.items():
        iv = payload.get(metric) or {}
        point = "—" if iv.get("point") is None else f"{iv['point']:.4f}"
        band = ("—" if iv.get("lo") is None
                else f"[{iv['lo']:.4f}, {iv['hi']:.4f}]")
        lines.append(f"| `{metric}` | {label} | {point} | {band} "
                     f"| {iv.get('n_clusters', 0)} |")
    lines += ["", f"> ⚠️ {payload['not_the_only_metric']}", "",
              "## 逐日(K 为实际值,不写死)", "",
              "| 日期 | 赢家 | K(L0/L1/L2) | L2 剔保送 | wc_l2_all | "
              "wc_l2_given_l1 | 报警线 | 报警 | 首行业占比 | lane 数 |",
              "|---|---:|---|---:|---:|---:|---:|---|---:|---:|"]
    alarm_by_date = {row["date"]: row for row in payload.get("alarms", [])}
    for row in payload.get("daily", []):
        alarm = alarm_by_date.get(row["date"], {})
        threshold = alarm.get("wc_l2_all_p25")
        fired = alarm.get("wc_l2_all_alarm")
        guards = row.get("guards") or {}
        share = guards.get("top_industry_share")
        lines.append(
            f"| {row['date']} | {row['n_winners']} | "
            f"{row['k_l0']}/{row['k_l1']}/{row['k_l2']} | {row['k_l2_ex_pinned']} | "
            + ("—" if row["wc_l2_all"] is None else f"{row['wc_l2_all']:.3f}") + " | "
            + ("—" if row["wc_l2_given_l1"] is None else f"{row['wc_l2_given_l1']:.3f}")
            + " | " + ("—" if threshold is None else f"{threshold:.3f}")
            + " | " + ("—" if fired is None else ("🚨" if fired else "·"))
            + " | " + ("—" if share is None else f"{share:.2f}")
            + f" | {guards.get('n_lanes', '—')} |")
    if not payload.get("daily"):
        lines.append("| — | — | — | — | — | — | — | — | — | — |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="L2 winner-capture SLO(§3.1 O1)")
    ap.add_argument("--scan-root", default=None)
    ap.add_argument("--json-out", default=str(OUT_JSON))
    ap.add_argument("--md-out", default=str(OUT_MD))
    a = ap.parse_args(argv)

    payload = build(a.scan_root)
    for path, text in ((Path(a.json_out),
                        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"),
                       (Path(a.md_out), render(payload))):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    fired = sum(1 for row in payload.get("alarms", []) if row.get("wc_l2_all_alarm"))
    print(f"[l2_slo] {payload['n_days']} 日 · 成熟度 {payload['maturity']['status']} "
          f"· 报警 {fired} 日 → {a.json_out} / {a.md_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
