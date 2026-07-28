#!/usr/bin/env python3
"""保送持仓判断账本 —— 回答"持仓的卖/持判断做对了没有"(确定性,零 LLM)。

design: docs/specs/2026-07-28-wave7-unified-roadmap-design.md §6 P1

**与选股复盘物理分表(防火墙)**:2026-07-17 用户裁定「保送不算」——那是给 `retro` /
`t1_review` / L3 edge 定的口径,防止手工保送票污染"漏斗自己选得准不准"的度量。本表是**另一
个问题**:你手上这几只持仓,当天给的卖/持判断事后看对不对。两者共用主尺(`fwd_2_oc`)但
永不互读:选股复盘一行都不从这里取数,本表也不进权重重标定。

**为什么必须有它**:保送票是全流程唯一"判断错了会真赔钱"的一档(选股判错只是没买),
而此前它**完全没有度量** —— 07-27 的 300857 从 Sell 被双复核折回 Underweight,那次折回
救对了还是救错了,没有任何账本回答得了。

  uv run --no-sync python -m autoresearch.learning.pinned_ledger  # → reports/learning/pinned_ledger.md
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

_COLS = ["date", "code", "name", "rating", "fwd_2", "market_fwd_2", "excess", "verdict",
         "fold_from", "fold_to", "fold_verdict", "trigger", "spread", "l3_conviction"]

# 卖飞线:看空判断(Sell/UW)之后该票**相对市场**还涨了这么多 → 判错。
# 首版定值 +2pp。取值理由:比 0 宽一点,免得把"和大盘差不多"记成卖飞;又远小于一个
# 涨停,不至于漏掉真卖飞。改它会改写全部历史结论,所以写在这里而不是散在函数里。
_FLY_PP = 0.02
_MIN_N = 10          # n<10 一律自标"样本不足",禁止据此改任何规则(与 earlystop_ledger 同口径)

_BEARISH = {"sell", "underweight"}


def _read_attr(d: Path) -> pd.DataFrame | None:
    ap = d / "retro" / "attribution.csv"
    if not ap.exists():
        return None
    try:
        attr = pd.read_csv(ap, dtype={"code": str})
        attr["code"] = attr["code"].astype(str).str.zfill(6)
        return attr.set_index("code")
    except Exception:  # noqa: BLE001
        return None


def market_fwd2(attr: pd.DataFrame | None) -> float | None:
    """当日全市场 `fwd_2_oc` 中位 —— 判对错的基准。

    用中位不用均值(A 股单日涨停尾巴会把均值拽偏),用 attribution 全量(≈5.5k 行 = L0
    全市场)而不是 finalist 子集:后者是被漏斗挑过的偏置样本,拿它当"市场"会systematically
    低估超额。
    """
    if attr is None or "fwd_2_oc" not in attr.columns:
        return None
    s = pd.to_numeric(attr["fwd_2_oc"], errors="coerce").dropna()
    return None if not len(s) else float(s.median())


def verdict_of(rating: str, excess: float | None, fly_pp: float = _FLY_PP) -> str | None:
    """(评级, 相对市场超额) → 判对 / 判错 / 中性;数据不全 → None。

    看空侧(Sell/UW)与看多侧(Hold/OW/Buy)镜像 —— 对**持仓**而言 Hold 不是弃权,
    它是"继续持有"这个主张,同样可以被证伪(该走没走)。
    """
    if excess is None or not rating:
        return None
    bearish = str(rating).strip().lower() in _BEARISH
    if bearish:
        if excess < 0:
            return "判对"
        return "判错(卖飞)" if excess >= fly_pp else "中性"
    if excess > 0:
        return "判对"
    return "判错(该走没走)" if excess <= -fly_pp else "中性"


def fold_verdict_of(fold_from: str | None, fold_to: str | None,
                    excess: float | None) -> str | None:
    """双复核折回是救对了还是救错了(无折回 → None)。

    把"原判"与"终评"各自过一遍 `verdict_of`,比较谁更接近事实 —— 这是整条 sell_review
    腿唯一的效果度量;没有它,"只向温和折回"到底救了多少误卖、又放走多少该卖,永远是
    一句无法证伪的设计主张。
    """
    if not fold_from or not fold_to or fold_from == fold_to or excess is None:
        return None
    a, b = verdict_of(fold_from, excess), verdict_of(fold_to, excess)
    if a == b:
        return "折平"
    if b == "判对":
        return "折对"
    if a == "判对":
        return "折错"
    return "折平"


def roll(scan_root: Path | str | None = None) -> pd.DataFrame:
    """逐 scan 日抽 lane=pinned 的持仓卡 × attribution 已实现 fwd_2 → 账本帧。"""
    from autoresearch.scan.health import final_ratings  # lazy 防环
    scan_root = Path(scan_root or "context/scan")
    rows: list[dict] = []
    if not scan_root.exists():
        return pd.DataFrame(columns=_COLS)
    for d in sorted(p for p in scan_root.iterdir() if p.is_dir() and p.name[:2] == "20"):
        fp = d / "finalists.csv"
        if not fp.exists():
            continue
        try:
            fin = pd.read_csv(fp, dtype={"code": str})
        except Exception:  # noqa: BLE001
            continue
        if "lane" not in fin.columns:
            continue
        pinned = fin[fin["lane"].astype(str).str.strip() == "pinned"]
        if not len(pinned):
            continue
        attr = _read_attr(d)
        mkt = market_fwd2(attr)
        ratings = final_ratings(d)
        from autoresearch.learning.ensemble_ledger import load_fold_facts

        fold_facts = load_fold_facts(d)
        for _, r in pinned.iterrows():
            code = str(r.get("code", "")).split(".")[0].zfill(6)
            rating = ratings.get(code) or ""
            fwd = None
            if attr is not None and code in attr.index and "fwd_2_oc" in attr.columns:
                v = pd.to_numeric(pd.Series([attr.at[code, "fwd_2_oc"]]), errors="coerce").iloc[0]
                fwd = None if pd.isna(v) else float(v)
            excess = None if (fwd is None or mkt is None) else fwd - mkt
            fold = fold_facts.get(code) or {}
            fold_from = fold.get("source_rating")
            fold_to = fold.get("final_rating")
            conv = pd.to_numeric(pd.Series([r.get("conviction")]), errors="coerce").iloc[0]
            rows.append({
                "date": d.name, "code": code,
                "name": "" if pd.isna(r.get("name")) else str(r.get("name")),
                "rating": rating, "fwd_2": fwd, "market_fwd_2": mkt,
                "excess": None if excess is None else round(excess, 6),
                "verdict": verdict_of(rating, excess),
                "fold_from": fold_from, "fold_to": fold_to,
                "fold_verdict": fold_verdict_of(fold_from, fold_to, excess),
                "trigger": fold.get("trigger"), "spread": fold.get("spread"),
                "l3_conviction": None if pd.isna(conv) else float(conv),
            })
    return pd.DataFrame(rows, columns=_COLS).sort_values(["date", "code"]).reset_index(drop=True)


def render(ledger: pd.DataFrame) -> list[str]:
    out = ["# 保送持仓判断账本(卖/持判对了没有 · 主尺 fwd_2_oc 相对全市场中位)", "",
           "_与选股复盘物理分表:2026-07-17「保送不算」裁定管的是漏斗选股口径,"
           "本表只问「持仓当天的判断事后看对不对」,两表永不互读。_", ""]
    if ledger is None or not len(ledger):
        return out + ["_无保送持仓记录(pinned.jsonc 为空,或尚无成熟样本)_"]
    mature = ledger[ledger["verdict"].notna()]
    out += [f"- 累计持仓卡 {len(ledger)} 张(其中 fwd_2 已成熟 {len(mature)} 张)"]
    if len(mature):
        right = int((mature["verdict"] == "判对").sum())
        out += [f"- 判对 {right}/{len(mature)}"
                + ("" if len(mature) >= _MIN_N else f" ⚠️ 样本不足(需 ≥{_MIN_N},不作结论)")]
    out += ["", "| 日期 | 代码 | 名称 | 终评 | fwd_2 | 市场 | 超额 | 判定 | 折回 |",
            "|---|---|---|---|---:|---:|---:|---|---|"]
    def _p(v):
        return "—" if v is None or (isinstance(v, float) and v != v) else f"{v * 100:+.2f}%"
    for _, r in ledger.iterrows():
        fold = "—" if not r["fold_verdict"] else f"{r['fold_from']}→{r['fold_to']} {r['fold_verdict']}"
        out.append(f"| {r['date']} | {r['code']} | {r['name']} | {r['rating'] or '—'} "
                   f"| {_p(r['fwd_2'])} | {_p(r['market_fwd_2'])} | {_p(r['excess'])} "
                   f"| {r['verdict'] or '未成熟'} | {fold} |")
    folds = ledger[ledger["fold_verdict"].notna()]
    if len(folds):
        n_right = int((folds["fold_verdict"] == "折对").sum())
        n_wrong = int((folds["fold_verdict"] == "折错").sum())
        out += ["", f"**双复核折回效果**:{len(folds)} 次折回 → 折对 {n_right} · 折错 {n_wrong} "
                    f"· 折平 {len(folds) - n_right - n_wrong}"
                    + ("" if len(folds) >= _MIN_N else f"(n<{_MIN_N},仅记录不裁决)")]
    out += ["", f"_看空侧(Sell/UW):超额<0=判对,超额≥+{_FLY_PP:.0%}=卖飞;看多侧(Hold/OW/Buy)镜像。"
                f"n<{_MIN_N} 的任何分桶都不作结论 —— 本表只攒数据,不改任何规则。_"]
    return out


def main() -> int:
    df = roll()
    p = Path("reports/learning/pinned_ledger.md")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(render(df)) + "\n", encoding="utf-8")
    print(f"[pinned_ledger] {len(df)} 行 → {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
