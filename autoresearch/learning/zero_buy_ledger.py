#!/usr/bin/env python3
"""0买日市场对照 ledger —— 回答"连续 0 买是纪律还是失明"(确定性,零 LLM)。

design: docs/specs/2026-07-02-scan-watchlist-and-health-metrics-design.md §2.3

逐 retro 日取全市场已实现 fwd 均值(attribution.csv,与 channel_eval 同源)+ 当日买单数
→ 跨日对照:0买日之后市场平均怎么走。0买日 mkt_gap(主尺)为负 = 空仓对了;持续显著为正 =
失明预警(该查召回/门,而不是庆祝纪律)。镜像 channel_ledger 的用法:

  uv run --no-sync python -m autoresearch.learning.zero_buy_ledger   # → reports/learning/zero_buy_ledger.md

Wave12-T6(A3):verdict 主判据 2026-08-08 由 fwd_2_oc 切到 gap_c1_o2(隔夜主尺,见
`autoresearch.common.ruler.MAIN_RULER`)——fwd_1/fwd_2/fwd_5 三档降参考列保留,不删
(旧行历史读数原样留存)。**定义断层预告**:E6(统一相对决策层)人批 activate 日起,
成功 run 不再产生 0买新行,本账本冻结为 legacy;新日级主账改记 `action_coverage`、
relative BUY 收益与 BLOCKED 原因(见 Wave12 T22 `relative_ledger`)——本次改动只覆盖
E6 activate 前的历史与 shadow 对照,不建冻结逻辑(activate 是 GATED task)。

review fix round 1(2026-08-08):任务书「表补 2026-08-06 起行」本轮**未完成**——
`context/scan/2026-08-06/` 无 `retro/` 子目录(2026-08-06 的 gap_c1_o2 需 T+2=
2026-08-10 周一开盘才成熟,BLOCKED_BY_MATURITY,与 Wave12 progress.md 的 T0.1 记账
一致);`2026-08-07` 当日未跑 scan(仅 `_prewarm.json`)。retro 归因待 08-10 后补跑,
`roll()` 逐次全量重算 `context/scan/*/retro/attribution.csv`,届时会自动纳入新行,
不需要手工回填。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

_COLS = ["date", "n_bought", "n_stocks", "mkt_gap", "mkt_fwd1", "mkt_fwd2", "mkt_fwd5"]


def bought_mask(df: pd.DataFrame) -> pd.Series:
    """attribution.csv 的 `bought` 列 → 规范布尔 Series(兼容字符串 True/False、1/0);缺列 → 全 False。

    D5 单一事实源(spec 2026-07-12 P0-1):journal.py 的买单计数经此与本 ledger 同口径读
    attribution `bought`(见 `journal._count_buys`),而非各自实现一套判定,防止两本账再分叉。
    """
    if "bought" not in df.columns:
        return pd.Series(False, index=df.index)
    return df["bought"].astype(str).str.lower().isin(("true", "1"))


def roll(scan_root: Path | None = None) -> pd.DataFrame:
    """聚合 context/scan/*/retro/attribution.csv → 每日 [date,n_bought,n_stocks,mkt_fwd1,mkt_fwd2,mkt_fwd5]。"""
    scan_root = scan_root or Path("context/scan")
    rows = []
    for p in sorted(Path(scan_root).glob("*/retro/attribution.csv")):
        try:
            df = pd.read_csv(p)
        except Exception:
            continue
        if "fwd_1_oo" not in df.columns or not len(df):
            continue
        bought = bought_mask(df)
        # Wave12-T6:mkt_gap(隔夜主尺 gap_c1_o2)为判据主档;fwd_1/fwd_2/fwd_5 三档并列
        # 取市场均值降为参考,不像 retro.attribute_frame 那样单挑主尺 —— 下面
        # gap_c1_o2/fwd_2_oc/fwd_5_oc 字面量固定,勿随主尺漂移(显式绑定 gap_c1_o2)。
        gap = pd.to_numeric(df.get("gap_c1_o2"), errors="coerce") if "gap_c1_o2" in df.columns else pd.Series(dtype=float)
        f1 = pd.to_numeric(df["fwd_1_oo"], errors="coerce")
        f2 = pd.to_numeric(df.get("fwd_2_oc"), errors="coerce") if "fwd_2_oc" in df.columns else pd.Series(dtype=float)
        f5 = pd.to_numeric(df.get("fwd_5_oc"), errors="coerce") if "fwd_5_oc" in df.columns else pd.Series(dtype=float)
        rows.append({"date": p.parent.parent.name, "n_bought": int(bought.sum()),
                     "n_stocks": int(f1.notna().sum()),
                     "mkt_gap": round(float(gap.mean()), 6) if len(gap) and gap.notna().any() else None,
                     "mkt_fwd1": round(float(f1.mean()), 6) if f1.notna().any() else None,
                     "mkt_fwd2": round(float(f2.mean()), 6) if len(f2) and f2.notna().any() else None,
                     "mkt_fwd5": round(float(f5.mean()), 6) if len(f5) and f5.notna().any() else None})
    return pd.DataFrame(rows, columns=_COLS).sort_values("date").reset_index(drop=True)


def render(
    ledger: pd.DataFrame,
    *,
    causal: pd.DataFrame | None = None,
) -> list[str]:
    """ledger → markdown(逐日表 + 0买日 vs 有买日市场后市对照)。"""
    out = ["# 0买日市场对照(纪律 vs 失明)", ""]
    if ledger is None or not len(ledger):
        return out + ["_无 retro attribution 数据(先跑 scan-retro)_"]
    if "mkt_gap" not in ledger.columns:
        # 历史产物不改写、展示层现算(gate-status 家训):调用方仍可能传入 T6 之前落盘/构造的
        # legacy 形态(无 mkt_gap 列)——补一列全 NaN,不炸,自动走下方 fwd_2/fwd_1 回退链。
        ledger = ledger.assign(mkt_gap=pd.NA)

    def f(x):
        return "—" if x is None or pd.isna(x) else f"{x * 100:+.2f}%"

    # M-3 修复(review 2026-08-08):逐日表头曾没有任何标尺(既没写 gap_c1_o2 也没写
    # "(主尺)/(参考)"),与 Global Constraint「文案与产物一律标尺」不齐——补上。
    out += ["| 日期 | 买单 | 全市场gap(主尺) | 全市场fwd_1(参考) | 全市场fwd_2(参考) | 全市场fwd_5(参考) |",
            "|---|---|---|---|---|---|"]
    for r in ledger.itertuples(index=False):
        out.append(f"| {r.date} | {int(r.n_bought)} | {f(r.mkt_gap)} | {f(r.mkt_fwd1)} | {f(r.mkt_fwd2)} | {f(r.mkt_fwd5)} |")
    zero, some = ledger[ledger["n_bought"] == 0], ledger[ledger["n_bought"] > 0]
    out.append("")
    if len(zero):
        vg, v1, v2, v5 = (zero["mkt_gap"].mean(), zero["mkt_fwd1"].mean(),
                          zero["mkt_fwd2"].mean(), zero["mkt_fwd5"].mean())
        # I-4 修复(review 2026-08-08):gap 与 fwd_1/2/5 的非空天数可能不同(gap_c1_o2 有
        # 历史空洞,如实测 2026-07-07 无该列)——四个均值此前共用一个 "(len(zero) 日)"
        # 标签,把 N-1 日的 gap 均值和 N 日的 fwd 均值混进同一个分母标注,是本模块
        # evidence_manifest docstring 点名的第 1 号病(手抄分母)的原样复刻。改为逐列
        # 各自标注真实 notna 天数(本仓库铁律:比率同时写分子/分母/as-of)。
        n_gap = int(zero["mkt_gap"].notna().sum())
        n_fwd1 = int(zero["mkt_fwd1"].notna().sum())
        n_fwd2 = int(zero["mkt_fwd2"].notna().sum())
        n_fwd5 = int(zero["mkt_fwd5"].notna().sum())
        # Wave12-T6:verdict 主判据 gap(主尺)→ fwd_2(旧主尺,参考)→ fwd_1(参考)三级回退,
        # 只在更高优先级的列**整列缺失**(不是单行 NaN)时才降级,保持既有回退行为不变。
        verdict = "空仓方向正确" if (
            (pd.notna(vg) and vg < 0)
            or (pd.isna(vg) and pd.notna(v2) and v2 < 0)
            or (pd.isna(vg) and pd.isna(v2) and pd.notna(v1) and v1 < 0)
        ) else "⚠️ 0买日后市为正——查召回/门(失明预警),别只归因纪律"
        out.append(f"- **0买日**({len(zero)} 日):市场 **gap {f(vg)}(主尺,n={n_gap})**、"
                   f"fwd_1 {f(v1)}(参考,n={n_fwd1})、fwd_2 {f(v2)}(参考,n={n_fwd2})、"
                   f"fwd_5 {f(v5)}(参考,n={n_fwd5})→ {verdict}")
    if len(some):
        n_gap_b = int(some["mkt_gap"].notna().sum())
        n_fwd1_b = int(some["mkt_fwd1"].notna().sum())
        n_fwd2_b = int(some["mkt_fwd2"].notna().sum())
        n_fwd5_b = int(some["mkt_fwd5"].notna().sum())
        out.append(f"- **有买日**({len(some)} 日):市场 gap 均值 {f(some['mkt_gap'].mean())}(n={n_gap_b})、"
                   f"fwd_1 均值 {f(some['mkt_fwd1'].mean())}(n={n_fwd1_b})、"
                   f"fwd_2 均值 {f(some['mkt_fwd2'].mean())}(n={n_fwd2_b})、"
                   f"fwd_5 均值 {f(some['mkt_fwd5'].mean())}(n={n_fwd5_b})")
    if causal is not None and len(causal):
        counts = causal["status"].value_counts().to_dict()
        out += [
            "",
            "## 因果裁决（详见 `abstention_ledger.md`）",
            "- " + " · ".join(
                f"{status} {counts.get(status, 0)}"
                for status in ("CORRECT", "FALSE", "NEUTRAL", "IMMATURE")
            ),
        ]
    out.append("")
    out.append("_市场均值只作背景；正确/错误弃权以逐票、可交易、相对市场 +2pp 的因果账为准。_")
    return out


def main() -> int:
    from autoresearch.learning.abstention_ledger import roll as causal_roll

    ledger = roll()
    out = Path("reports/learning/zero_buy_ledger.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "\n".join(render(ledger, causal=causal_roll())) + "\n",
        encoding="utf-8",
    )
    print(f"[zero_buy_ledger] {len(ledger)} 日 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
