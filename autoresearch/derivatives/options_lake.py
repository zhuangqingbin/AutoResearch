#!/usr/bin/env python3
"""F1 期权市场地形 —— PCR / 持仓结构(确定性,零 LLM;**I 类,不进 prompt**)。

design: docs/specs/2026-08-03-scan-next-wave-brainstorm-design.md §2.2 F1

## 产品集裁定(不是永久结构断言)

截至 2026-08-04 的公开产品集,本项目能稳定获取的是 **ETF/指数期权**,未找到可用于全 A
**个股召回**的交易所个股期权数据。因此本波**不做个股期权召回**;期权只作市场地形/风格
风险候选。上交所规则允许股票或 ETF 成为期权标的 → 该结论**按季度重检**,不是钉死。

## 三件必须做对的事

1. **强制分页**。`opt_basic` 首页恰 12,000 行 —— 那是**分页上限**,不是全量
   (offset=12,000 仍有行)。不分页 = 元数据缺一大块,而缺的那部分合约在联结时会
   静默消失,PCR 却照样算得出一个数。`paginate()` + `coverage_report()` 一起用。
2. **不裸加总**。不同 ETF/指数、合约乘数与期限**不得直接相加**。跨产品展示需名义金额/
   delta 归一;做不到就**保持分品种面板**(本模块的默认)。
3. **PCR 不等于看空**。PCR 只能解释为「对冲/持仓压力」——它**无法区分买 put 与卖 put**
   (卖 put 是看多)。任何把 PCR 直接标成「看空」的渲染都是错的,`PCR_SEMANTICS`
   随每个读数一起走。

## 消费边界

进入 `market_pack.derivatives` 只算 **I**;加入 `strategist_pack` 的 allowlist、
或进温度/regime 即为 **B**,必须走 registry。`assert_not_in_strategist_allowlist()`
把这条做成会抛错的检查 —— L3/L4 的 prompt diff 必须保持 0,直到行为实验批准。

  uv run --no-sync python -m autoresearch.derivatives.options_lake report --date 2026-07-31
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

SCHEMA_VERSION = 1
RULE_VERSION = "options_lake.v1"

# 2026-08-04 实测:`opt_basic` SSE 首页恰 12,000 行,offset=12,000 仍有行 → 这是分页上限。
PAGE_LIMIT = 12_000
MAX_PAGES = 50                     # 防失控:50 页 × 12,000 = 60 万行,远超真实规模

# 2026-08-04 实测 opt_daily(20260731):SSE 752 / SZSE 492 / CFFEX 720 行 —— CFFEX
# 权限已证(OPEN-Q-3 关闭)。行数只作规模先验,不作断言。
KNOWN_EXCHANGES = ("SSE", "SZSE", "CFFEX")

PCR_SEMANTICS = (
    "PCR = 对冲/持仓压力,**不是方向观点**。它无法区分买 put(看空)与卖 put(看多),"
    "任何把它直接标成「看空」的渲染都是错的。")

AGGREGATION_RULE = (
    "不同 ETF/指数、合约乘数与期限**不得裸加总**;跨产品比较需名义金额/delta 归一,"
    "否则保持分品种面板")

CONSUMPTION_BOUNDARY = (
    "进 market_pack.derivatives = I 类;进 strategist_pack allowlist 或温度/regime "
    "= B 类,必须 registry。L3/L4 prompt diff 必须保持 0 直到行为实验批准")


class OptionsError(RuntimeError):
    """分页/联结/口径违约 —— 宁可无读数,不要一个算错的 PCR。"""


# ───────────────────────── 强制分页 ─────────────────────────


def paginate(fetch, *, page_limit: int = PAGE_LIMIT, max_pages: int = MAX_PAGES,
             key: str = "ts_code") -> dict:
    """强制分页拉取 → `{frame, pages, rows_raw, rows_dedup, hit_page_limit, truncated}`。

    `fetch(offset, limit)` 返回一页 DataFrame。**首页满页即继续翻**——「首页恰好等于
    上限」是分页存在的信号,不是数据正好这么多。翻到不足一页为止。

    去重按 `key`:分页边界重叠是常见实现细节,不去重会让 PCR 的分母虚高。
    """
    frames: list[pd.DataFrame] = []
    pages = 0
    hit_limit = False
    for page in range(max_pages):
        chunk = fetch(page * page_limit, page_limit)
        if chunk is None or not len(chunk):
            break
        frames.append(chunk)
        pages += 1
        if len(chunk) < page_limit:
            break
        hit_limit = True
    else:
        # for-else:跑满 max_pages 仍未见短页 = 还有数据没拉完,必须说出来
        return _paginated(frames, pages, key, hit_limit, truncated=True)
    return _paginated(frames, pages, key, hit_limit, truncated=False)


def _paginated(frames, pages, key, hit_limit, *, truncated) -> dict:
    raw = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    dedup = (raw.drop_duplicates(subset=[key]).reset_index(drop=True)
             if len(raw) and key in raw.columns else raw)
    return {"frame": dedup, "pages": pages, "rows_raw": int(len(raw)),
            "rows_dedup": int(len(dedup)), "hit_page_limit": hit_limit,
            "truncated": truncated,
            "note": ("翻满 max_pages 仍未见短页 —— **还有数据没拉完**,"
                     "本次结果不完整" if truncated else "")}


def coverage_report(daily: pd.DataFrame, basic: pd.DataFrame) -> dict:
    """联结前的 coverage 对账 —— 行情里有、元数据里没有的合约必须**显式列出**。

    这是分页缺页最典型的症状:PCR 照样算得出一个数,只是少了一批合约。
    """
    daily_codes = set(daily["ts_code"].astype(str)) if len(daily) else set()
    basic_codes = set(basic["ts_code"].astype(str)) if len(basic) else set()
    missing = sorted(daily_codes - basic_codes)
    return {
        "n_daily_contracts": len(daily_codes),
        "n_basic_contracts": len(basic_codes),
        "n_missing_metadata": len(missing),
        "missing_sample": missing[:10],
        "coverage_rate": (round(len(daily_codes & basic_codes) / len(daily_codes), 6)
                          if daily_codes else None),
        "reconciled": not missing,
        "why_it_matters": ("元数据缺失的合约在联结时静默消失,而 PCR 照样算得出一个数 —— "
                           "这正是 opt_basic 不分页的症状"),
    }


# ───────────────────────── 联结 + 分桶 ─────────────────────────

_EXPIRY_BUCKETS = ((0, 7, "0-7d"), (8, 30, "8-30d"), (31, 90, "31-90d"),
                   (91, 10_000, "90d+"))


def expiry_bucket(days: float | None) -> str:
    if days is None or (isinstance(days, float) and np.isnan(days)):
        return "unknown"
    for lo, hi, label in _EXPIRY_BUCKETS:
        if lo <= days <= hi:
            return label
    return "unknown"


def join_asof(daily: pd.DataFrame, basic: pd.DataFrame, *,
              trade_date: str) -> pd.DataFrame:
    """`opt_daily` × `opt_basic` 按 as-of 联结,并派生到期天数/期限桶。

    联结键 `ts_code`。**内联结**:元数据缺失的合约不参与计算(它们已在 coverage 报告
    里被点名),硬凑一个 `unknown` 品种只会让面板更脏。
    """
    if not len(daily) or not len(basic):
        return pd.DataFrame()
    meta_cols = [c for c in ("ts_code", "exchange", "call_put", "opt_code", "name",
                             "delist_date", "maturity_date", "per_unit")
                 if c in basic.columns]
    merged = daily.merge(basic[meta_cols], on="ts_code", how="inner")
    if not len(merged):
        return merged
    expiry_col = next((c for c in ("maturity_date", "delist_date")
                       if c in merged.columns), None)
    if expiry_col:
        as_of = pd.to_datetime(str(trade_date).replace("-", ""), format="%Y%m%d",
                               errors="coerce")
        exp = pd.to_datetime(merged[expiry_col].astype(str).str.replace("-", ""),
                             format="%Y%m%d", errors="coerce")
        merged["days_to_expiry"] = (exp - as_of).dt.days
    else:
        merged["days_to_expiry"] = np.nan
    merged["expiry_bucket"] = merged["days_to_expiry"].map(expiry_bucket)
    merged["side"] = merged["call_put"].astype(str).str.upper().str[0].map(
        {"C": "call", "P": "put"}).fillna("unknown")
    merged["underlying"] = (merged["opt_code"].astype(str)
                            if "opt_code" in merged.columns
                            else merged["exchange"].astype(str))
    return merged


def bucket_metrics(joined: pd.DataFrame) -> pd.DataFrame:
    """分 (underlying, expiry_bucket) 的成交量 PCR / 持仓 PCR / 成交额 / OI。

    **按品种分桶,不跨品种加总** —— 合约乘数不同,裸加总没有意义(`AGGREGATION_RULE`)。
    """
    if not len(joined):
        return pd.DataFrame(columns=["underlying", "expiry_bucket", "vol_call",
                                     "vol_put", "vol_pcr", "oi_call", "oi_put",
                                     "oi_pcr", "amount", "n_contracts"])
    work = joined.copy()
    for col in ("vol", "oi", "amount"):
        work[col] = pd.to_numeric(work.get(col), errors="coerce").fillna(0.0)
    rows = []
    for (under, bucket), group in work.groupby(["underlying", "expiry_bucket"]):
        calls = group[group["side"] == "call"]
        puts = group[group["side"] == "put"]
        vol_c, vol_p = float(calls["vol"].sum()), float(puts["vol"].sum())
        oi_c, oi_p = float(calls["oi"].sum()), float(puts["oi"].sum())
        rows.append({
            "underlying": str(under), "expiry_bucket": str(bucket),
            "vol_call": vol_c, "vol_put": vol_p,
            # 分母为 0 → None,**不是** 0(没有 call 成交时 PCR 不存在)
            "vol_pcr": round(vol_p / vol_c, 6) if vol_c > 0 else None,
            "oi_call": oi_c, "oi_put": oi_p,
            "oi_pcr": round(oi_p / oi_c, 6) if oi_c > 0 else None,
            "amount": float(group["amount"].sum()),
            "n_contracts": int(len(group)),
        })
    return pd.DataFrame(rows).sort_values(["underlying", "expiry_bucket"]).reset_index(
        drop=True)


def roll_adjusted_oi(today: pd.DataFrame, prev: pd.DataFrame) -> pd.DataFrame:
    """换月调整的 OI 变化 —— **只比两日都在场的合约**。

    为什么必须调整:换月那天旧合约到期归零、新合约从零起步,裸算 ΔOI 会出现一个巨大的
    假信号,而它每个月都准时出现一次。只比交集 = 把换月的机械项消掉。
    """
    cols = ["underlying", "expiry_bucket", "oi_call", "oi_put"]
    if not len(today) or not len(prev):
        return pd.DataFrame(columns=[*cols, "d_oi_call", "d_oi_put", "roll_adjusted"])
    merged = today[cols].merge(prev[cols], on=["underlying", "expiry_bucket"],
                               how="inner", suffixes=("", "_prev"))
    if not len(merged):
        return pd.DataFrame(columns=[*cols, "d_oi_call", "d_oi_put", "roll_adjusted"])
    merged["d_oi_call"] = merged["oi_call"] - merged["oi_call_prev"]
    merged["d_oi_put"] = merged["oi_put"] - merged["oi_put_prev"]
    merged["roll_adjusted"] = True
    dropped = len(today) - len(merged)
    if dropped:
        merged.attrs["dropped_buckets"] = dropped
    return merged[[*cols, "d_oi_call", "d_oi_put", "roll_adjusted"]]


# ───────────────────────── market_pack 块(I 类)─────────────────────────


def derivatives_block(metrics: pd.DataFrame, *, coverage: dict | None = None,
                      trade_date: str | None = None) -> dict:
    """`market_pack.derivatives` 的内容。**分品种面板**,不给一个"全市场 PCR"总数。

    给不出总数是有意的:那个数需要名义/delta 归一才成立,而归一还没做
    (`AGGREGATION_RULE`)。宁可让读的人看到三行面板,也不给一个算错的单一数字。
    """
    panels = (metrics.to_dict("records") if len(metrics) else [])
    return {
        "schema_version": SCHEMA_VERSION,
        "rule_version": RULE_VERSION,
        "trade_date": trade_date,
        "panels": panels,
        "n_panels": len(panels),
        # 没有 total_pcr —— 这是刻意的,见 aggregation_rule
        "aggregation_rule": AGGREGATION_RULE,
        "pcr_semantics": PCR_SEMANTICS,
        "consumption_boundary": CONSUMPTION_BOUNDARY,
        "coverage": coverage or {},
    }


def assert_not_in_strategist_allowlist() -> None:
    """守卫:`derivatives` 进了 strategist allowlist = 已经是 B 类,必须先过 registry。"""
    from autoresearch.contracts.strategist_view import ALLOWED_KEYS

    if "derivatives" in ALLOWED_KEYS:
        raise OptionsError(
            "`derivatives` 出现在 strategist_pack.ALLOWED_KEYS —— 那已经是 B 类"
            "(进入策略师判断),必须先有 registry ACTIVE 实验;"
            f"当前边界:{CONSUMPTION_BOUNDARY}")


# ───────────────────────── 最小证伪步:领先性检验 ─────────────────────────


def lead_lag_check(panel: pd.DataFrame, breadth: pd.DataFrame, *,
                   metric: str = "oi_pcr", horizon: int = 1) -> dict:
    """**lagged** PCR/ΔPCR 对**次日** breadth 的增量 —— 同期相关不算领先证据(§2.2)。

    `panel`:`[date, <metric>]`;`breadth`:`[date, breadth]`。
    按扫描日 walk-forward 的完整实现留给 factor_lab;这里只做最小证伪步:
    **先确认它到底领不领先**,不领先就不必往下修了。
    """
    from autoresearch.common import stats as st

    if not len(panel) or not len(breadth):
        return {"status": "NO_DATA", "n": 0, "interval": None}
    left = panel[["date", metric]].dropna().copy()
    right = breadth[["date", "breadth"]].dropna().copy()
    left["date"] = left["date"].astype(str)
    right["date"] = right["date"].astype(str)
    left = left.sort_values("date").reset_index(drop=True)
    right = right.sort_values("date").reset_index(drop=True)
    # 用**滞后**的 PCR 对齐**未来**的 breadth:panel[t] ↔ breadth[t+horizon]
    right["_target_date"] = right["date"].shift(-horizon)
    pairs = left.merge(right[["date", "breadth"]].rename(
        columns={"date": "_bdate"}), left_on="date", right_on="_bdate", how="inner")
    lagged = left.copy()
    lagged["date_next"] = lagged["date"].shift(-horizon)
    joined = lagged.dropna(subset=["date_next"]).merge(
        right[["date", "breadth"]], left_on="date_next", right_on="date",
        how="inner", suffixes=("", "_b"))
    if len(joined) < 3:
        return {"status": "IMMATURE", "n": int(len(joined)), "interval": None,
                "note": "样本不足 —— 不是「无领先性」,是还没法判"}
    corr = float(np.corrcoef(joined[metric], joined["breadth"])[0, 1])
    same_day = (float(np.corrcoef(pairs[metric], pairs["breadth"])[0, 1])
                if len(pairs) >= 3 else None)
    interval = st.date_cluster_bootstrap(
        joined.assign(_x=joined[metric] * joined["breadth"]), "_x", date_col="date")
    return {
        "status": "MEASURED", "n": int(len(joined)),
        "metric": metric, "horizon": horizon,
        "lagged_corr": round(corr, 6),
        "same_day_corr": None if same_day is None else round(same_day, 6),
        "interval": interval.as_dict(),
        "discipline": "同期相关不算领先证据 —— same_day_corr 单列,仅供对照",
        "maturity_note": ("60 日不是自动充分条件:按转折事件数、到期周期与 regime 覆盖"
                          "判成熟(§2.2)"),
    }


def render(block: dict) -> str:
    lines = ["# 期权市场地形(F1;I 类 —— 不进 prompt)", "",
             f"> {block['pcr_semantics']}", "",
             f"> {block['aggregation_rule']}", "",
             f"- 交易日 `{block.get('trade_date') or '—'}` · 面板 {block['n_panels']} 个 · "
             f"rule `{block['rule_version']}`"]
    coverage = block.get("coverage") or {}
    if coverage:
        lines.append(
            f"- 元数据覆盖 {coverage.get('coverage_rate')} · 缺元数据合约 "
            f"{coverage.get('n_missing_metadata')} "
            + ("✅ 已对平" if coverage.get("reconciled") else "🚨 **未对平**"))
    lines += ["", "| 标的 | 期限桶 | 成交量 PCR | 持仓 PCR | 成交额 | 合约数 |",
              "|---|---|---:|---:|---:|---:|"]
    for row in block["panels"]:
        lines.append(
            f"| {row['underlying']} | {row['expiry_bucket']} | "
            + ("—" if row["vol_pcr"] is None else f"{row['vol_pcr']:.3f}") + " | "
            + ("—" if row["oi_pcr"] is None else f"{row['oi_pcr']:.3f}")
            + f" | {row['amount']:.0f} | {row['n_contracts']} |")
    if not block["panels"]:
        lines.append("| — | — | — | — | — | — |")
    lines += ["", f"> 消费边界:{block['consumption_boundary']}", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="F1 期权市场地形(§2.2;I 类)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("guard", help="守卫:derivatives 不得进 strategist allowlist")
    g.add_argument("--json-out", default=None)
    a = ap.parse_args(argv)
    if a.cmd == "guard":
        try:
            assert_not_in_strategist_allowlist()
        except OptionsError as exc:
            print(f"  ✗ {exc}")
            return 1
        print("[options_lake] ✅ derivatives 仍在 I 类边界内(未进 strategist allowlist)")
        if a.json_out:
            Path(a.json_out).write_text(
                json.dumps({"boundary": CONSUMPTION_BOUNDARY, "ok": True},
                           ensure_ascii=False, indent=2), encoding="utf-8")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
