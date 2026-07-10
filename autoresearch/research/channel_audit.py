#!/usr/bin/env python3
"""通道整编报告 —— 各路累计 T+2 账本 + 两两召回集 Jaccard 重叠矩阵。零 LLM,证据件。

design: docs/specs/2026-07-11-recall-gate-pinned-config-design.md §2.3。

动机:10 路召回(`autoresearch/scan/recall/channels.py`)注册以来从未有『整编』证据——哪些路
重叠度高(冗余,同一批票被两路重复照顾)、哪些路边际超额持续为负(该退役/降额)全凭直觉。本 CLI
直接从**最原始**的两个 staging 文件独立重算(不复用 `stage_eval.channel_edge` 派生的
`L1_recall_top1000.csv` provenance)——两条独立证据链互相印证,且 `L1_channels.csv` 是唯一
带『某路当日实际召回了哪些代码』长表的文件,`channel_eval.csv` 只有聚合计数、算不出 Jaccard:
  - `context/scan/<date>/L1_channels.csv`(长表:channel, code, channel_rank, channel_score)
  - `context/scan/<date>/retro/attribution.csv`(已实现前向收益,主尺 `fwd_2_oc`)

三节输出(仅陈述数据,不自动改配置;人批见 spec §2.3 默认整编案表):
  ① 各路累计 T+2 账本 —— mean/unique_excess_t2、hit_rate_t2、n_days(简单日频均值,
     口径同 `autoresearch.learning.channel_ledger.roll`);n_days < 10 标 ⚠薄样本(spec 门槛 n≥10 日)。
  ② 两两召回集 Jaccard 重叠矩阵 —— 每路窗口内**跨日 union 去重**后的代码集合,
     共同召回码(交集)/ 并集 / Jaccard。
  ③ unique_excess_t2 排序 + 整编观察行 —— 高重叠对 + unique_excess_t2 为负的路点名,
     供人工参照 spec §2.3 默认整编案表逐条裁决(本模块不写 scan_config)。

用法:uv run --no-sync python -m autoresearch.research.channel_audit [--days 30]
"""
from __future__ import annotations

import argparse
import sys
from datetime import date as _date
from itertools import combinations
from pathlib import Path

import pandas as pd

_RET_MAIN = "fwd_2_oc"       # 超短主尺:D+1开→D+2收(2026-07-10 用户裁定)
_THIN_DAYS = 10               # spec §2.3 门槛:n_days < 10 → ⚠薄样本
_LEDGER_COLS = ["channel", "n_days", "mean_excess_t2", "unique_excess_t2", "hit_rate_t2", "thin"]
_JACCARD_COLS = ["channel_a", "channel_b", "common", "union", "jaccard"]


def _code6(s: pd.Series) -> pd.Series:
    return s.astype(str).str.zfill(6)


def _fmt_pct(x) -> str:
    return "—" if x is None or pd.isna(x) else f"{x * 100:+.2f}%"


# ───────────────────────── 纯统计核(无 IO,可离线自测) ─────────────────────────


def day_channel_stats(channels: pd.DataFrame, attribution: pd.DataFrame) -> pd.DataFrame:
    """单日 L1_channels(长表 channel,code) × attribution(fwd_2_oc) → 每路一行,纯函数。

    excess_t2 = 个股 fwd_2_oc − 当日全市场(attribution 全表)中位;unique = 当日仅被这一路召回
    (从 `L1_channels.csv` 自身的 channel×code 计数直接推导,不依赖 `L1_recall_top1000.csv` 的
    `recall_channels` provenance——两条独立证据链)。attribution 无 `buyable` 列 → 全视为可买
    (与 `stage_eval.channel_edge` 同一降级口径,真实 attribution.csv 现无此列)。
    返回列:channel / codes(该路当日代码 set,供 Jaccard 用) / n_recalled / n_unique /
    mean_excess_t2 / unique_excess_t2 / hit_rate_t2。channels 无 channel/code 列或空 → 空表。
    """
    cols = ["channel", "codes", "n_recalled", "n_unique", "mean_excess_t2", "unique_excess_t2", "hit_rate_t2"]
    if channels is None or not len(channels) or not {"channel", "code"}.issubset(channels.columns):
        return pd.DataFrame(columns=cols)

    ch = channels.copy()
    ch["code"] = _code6(ch["code"])
    attr = attribution.copy()
    attr["code"] = _code6(attr["code"])
    attr[_RET_MAIN] = pd.to_numeric(attr.get(_RET_MAIN), errors="coerce")
    attr["buyable"] = (attr["buyable"].fillna(True).astype(bool) if "buyable" in attr.columns
                       else pd.Series(True, index=attr.index))
    mkt = attr[_RET_MAIN].median()
    attr["excess_t2"] = attr[_RET_MAIN] - mkt

    n_channels_by_code = ch.groupby("code")["channel"].nunique()
    m = ch.merge(attr[["code", "excess_t2", "buyable"]], on="code", how="left")
    m["n_channels"] = m["code"].map(n_channels_by_code)

    def _mean(s: pd.Series):
        s = s.dropna()
        return round(float(s.mean()), 5) if len(s) else None

    rows = []
    for c, sub in m.groupby("channel"):
        uniq = sub[sub["n_channels"] == 1]
        buy_all, buy_uniq = sub[sub["buyable"]], uniq[uniq["buyable"]]
        ex_all = buy_all["excess_t2"].dropna()
        rows.append({
            "channel": c,
            "codes": frozenset(sub["code"]),
            "n_recalled": int(len(sub)),
            "n_unique": int(len(uniq)),
            "mean_excess_t2": _mean(buy_all["excess_t2"]),
            "unique_excess_t2": _mean(buy_uniq["excess_t2"]),
            "hit_rate_t2": round(float((ex_all > 0).mean()), 4) if len(ex_all) else None,
        })
    return pd.DataFrame(rows, columns=cols)


def cumulative_ledger(daily: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """{date: day_channel_stats 输出} → 跨日累计账本(纯函数)。

    简单日频均值(每日等权,口径同 `channel_ledger.roll`——不按当日样本数加权,避免大盘日
    压过缩量日)。n_days < 10 → thin=True(spec §2.3 门槛 n≥10 日方视为稳健证据)。
    按 unique_excess_t2 降序(None/NaN 殿后)。
    """
    frames = [df.assign(date=d) for d, df in daily.items() if df is not None and len(df)]
    if not frames:
        return pd.DataFrame(columns=_LEDGER_COLS)
    alld = pd.concat(frames, ignore_index=True)

    def _avg(s: pd.Series):
        s = pd.to_numeric(s, errors="coerce").dropna()
        return round(float(s.mean()), 5) if len(s) else None

    rows = []
    for c, sub in alld.groupby("channel"):
        n_days = int(sub["date"].nunique())
        rows.append({
            "channel": c,
            "n_days": n_days,
            "mean_excess_t2": _avg(sub["mean_excess_t2"]),
            "unique_excess_t2": _avg(sub["unique_excess_t2"]),
            "hit_rate_t2": _avg(sub["hit_rate_t2"]),
            "thin": n_days < _THIN_DAYS,
        })
    out = pd.DataFrame(rows, columns=_LEDGER_COLS)
    return out.sort_values("unique_excess_t2", ascending=False, na_position="last").reset_index(drop=True)


def jaccard_matrix(daily: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """{date: day_channel_stats 输出} → 每路跨日 union 去重代码集合的两两 Jaccard(纯函数)。

    `common` = 交集大小(共同召回码数)、`union` = 并集大小;`jaccard = common/union`
    (并集为 0 → 0.0)。< 2 路有数据 → 空表。
    """
    code_sets: dict[str, set[str]] = {}
    for df in daily.values():
        if df is None or not len(df):
            continue
        for r in df.itertuples(index=False):
            code_sets.setdefault(r.channel, set()).update(r.codes)

    channels = sorted(code_sets)
    rows = []
    for a, b in combinations(channels, 2):
        sa, sb = code_sets[a], code_sets[b]
        union = len(sa | sb)
        common = len(sa & sb)
        rows.append({"channel_a": a, "channel_b": b, "common": common, "union": union,
                     "jaccard": round(common / union, 4) if union else 0.0})
    return pd.DataFrame(rows, columns=_JACCARD_COLS)


def consolidation_notes(ledger: pd.DataFrame, jaccard: pd.DataFrame, *,
                        jaccard_thresh: float = 0.3, weak_thresh: float = 0.0) -> list[str]:
    """仅陈述数据的整编观察行(纯函数,**不改配置**)——人批见 spec §2.3 默认整编案表。

    高重叠对(jaccard ≥ jaccard_thresh)逐对点名 + 两路各自 unique_excess_t2 读数;
    unique_excess_t2 为负(< weak_thresh)的路整行点名为整编/退役候选。都没有 → 一行『暂无信号』。
    """
    ue = {r.channel: r.unique_excess_t2 for r in ledger.itertuples(index=False)} if len(ledger) else {}
    notes: list[str] = []

    if jaccard is not None and len(jaccard):
        for r in jaccard.itertuples(index=False):
            if r.jaccard >= jaccard_thresh:
                notes.append(
                    f"{r.channel_a} × {r.channel_b}:Jaccard={r.jaccard:.2f}(共同{r.common}/并集{r.union})"
                    f" — unique_excess_t2 {r.channel_a}={_fmt_pct(ue.get(r.channel_a))} / "
                    f"{r.channel_b}={_fmt_pct(ue.get(r.channel_b))}")

    if ledger is not None and len(ledger):
        weak = [r.channel for r in ledger.itertuples(index=False)
               if pd.notna(r.unique_excess_t2) and r.unique_excess_t2 < weak_thresh]
        if weak:
            notes.append(f"unique_excess_t2 为负(整编/退役候选,人批 scan_config.funnel 才生效):{', '.join(weak)}")

    if not notes:
        notes.append("无高重叠对 / 无负 unique_excess_t2 路——暂无整编信号")
    return notes


# ───────────────────────── IO:扫历史 staging → 三节数据 → 落盘 ─────────────────────────


def _scan_dates(scan_root: Path, days: int) -> list[str]:
    """`context/scan/` 下 YYYY-MM-DD 目录名,升序取最近 `days` 个(目录存在即算,不查交易日历)。"""
    if not scan_root.exists():
        return []
    names = sorted(p.name for p in scan_root.iterdir()
                   if p.is_dir() and len(p.name) == 10 and p.name[4] == "-" and p.name[7] == "-")
    return names[-days:] if days > 0 else names


def _load_day(scan_root: Path, date: str) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    """读单日 L1_channels.csv + retro/attribution.csv;任一缺失/空/读取失败 → None(静默跳过)。"""
    cp, ap = scan_root / date / "L1_channels.csv", scan_root / date / "retro" / "attribution.csv"
    if not (cp.exists() and ap.exists()):
        return None
    try:
        ch = pd.read_csv(cp, dtype={"code": str})
        attr = pd.read_csv(ap, dtype={"code": str})
    except Exception:
        return None
    if ch.empty or attr.empty or "code" not in attr.columns:
        return None
    return ch, attr


def audit(scan_root: Path | None = None, days: int = 30) -> dict:
    """扫 `context/scan/<date>/` 窗口(最近 `days` 个目录,双文件齐全才纳入)→ 整编报告三节数据。

    返回 `{dates, ledger, jaccard, notes}`;窗口内无可用日 → dates=[]、ledger/jaccard 皆空表。
    """
    scan_root = scan_root or Path("context/scan")
    daily: dict[str, pd.DataFrame] = {}
    for d in _scan_dates(scan_root, days):
        loaded = _load_day(scan_root, d)
        if loaded is None:
            continue
        stats = day_channel_stats(*loaded)
        if len(stats):
            daily[d] = stats
    ledger, jac = cumulative_ledger(daily), jaccard_matrix(daily)
    return {
        "dates": sorted(daily),
        "ledger": ledger,
        "jaccard": jac,
        "notes": consolidation_notes(ledger, jac),
    }


def render(result: dict) -> list[str]:
    """整编报告三节 markdown(纯函数)。"""
    dates = result.get("dates") or []
    span = f"{dates[0]} ~ {dates[-1]}({len(dates)} 日)" if dates else "无数据"
    out = ["# 通道整编报告(证据件,零 LLM)", "", f"窗口:{span}", ""]

    out += [f"## ① 各路累计 T+2 账本(n_days < {_THIN_DAYS} 标 ⚠薄样本)", ""]
    ledger: pd.DataFrame = result.get("ledger", pd.DataFrame())
    if ledger is None or not len(ledger):
        out += ["_窗口内无 L1_channels.csv × attribution.csv 可配对数据_", ""]
    else:
        out += ["| 路 | 天数 | membership超额T2 | unique超额T2 | 命中率T2 |", "|---|---|---|---|---|"]
        for r in ledger.itertuples(index=False):
            thin = " ⚠薄样本" if r.thin else ""
            out.append(f"| {r.channel}{thin} | {r.n_days} | {_fmt_pct(r.mean_excess_t2)} | "
                       f"{_fmt_pct(r.unique_excess_t2)} | {_fmt_pct(r.hit_rate_t2)} |")
        out.append("")

    out += ["## ② 两两召回集 Jaccard 重叠矩阵(跨日 union 去重代码集合)", ""]
    jac: pd.DataFrame = result.get("jaccard", pd.DataFrame())
    if jac is None or not len(jac):
        out += ["_通道数 < 2 或窗口内无数据,无对可比_", ""]
    else:
        out += ["| 路A | 路B | 共同召回码 | 并集 | Jaccard |", "|---|---|---|---|---|"]
        for r in jac.itertuples(index=False):
            out.append(f"| {r.channel_a} | {r.channel_b} | {r.common} | {r.union} | {r.jaccard:.2f} |")
        out.append("")

    out += ["## ③ unique_excess_t2 排序 + 整编观察(仅陈述数据,不自动改配置)", ""]
    for n in result.get("notes") or []:
        out.append(f"- {n}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m autoresearch.research.channel_audit",
                                 description="通道整编报告(累计T+2账本 + 召回集Jaccard重叠矩阵,证据件)")
    ap.add_argument("--days", type=int, default=30, help="回看最近 N 个 scan 日目录(默认 30)")
    args = ap.parse_args(argv)

    result = audit(days=args.days)
    body = "\n".join(render(result))
    dates = result.get("dates") or []
    tag = dates[-1] if dates else _date.today().isoformat()
    outp = Path("reports") / f"channel_audit_{tag}.md"
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(body, encoding="utf-8")
    print(body)
    print(f"\n[channel_audit] → {outp}", file=sys.stderr)
    if not dates:
        print("[channel_audit] 窗口内无可用 L1_channels.csv/attribution.csv 配对数据", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
