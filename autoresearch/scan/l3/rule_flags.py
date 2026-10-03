#!/usr/bin/env python3
"""L3 规则旗的确定性预计算(2026-10-03 复盘稿 B4;影子:只记账,不进 L3 输入、不改选择)。

L3 契约(`.claude/agents/l3-rank.md`)有一半是数字规则,今天全靠模型逐只判断:

- `funds_aligned` —— ② 资金共振:`main_net_ratio`、`cmf_20`、`obv_mom_20` 三者同向为正;
- `downtrend_b`   —— B 下跌趋势:价在 MA20 与 MA60 之下 ∧ 主力净流出(`lowturn` 旗亮 = 契约例外;
                     例外按该场当时的 `l3.lowturn` 配置用 `common.turnup` 同一谓词现算,答不出 → <NA>);
- `knife_b`       —— B 深跌落刀:`common.scoring.falling_knife_mask` ∧ 主力未流入(无例外);
- `chase_h`       —— H 当日大涨:`pct_1d ≥ l3.guards.chase_1d_pct`;
- `fragile_winner`—— ⑤ 脆弱:`winner_rate > 90`。

输入缺失 → 该旗 `<NA>`(答不出 ≠ 没亮)。`agreement` 量模型的 finalist / bench 与这些旗的关系;
`retest_jaccard` 是噪声地板协议(同配置重跑 L3)要的那把尺。B 的「真吸筹 / 带日期催化」例外是判断,
不在这里收口 —— 所以亮了 B 旗的 finalist 只是计数,不是违规判定。

  uv run --no-sync python -m autoresearch.scan.l3.rule_flags --out <dir>
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

FLAG_NAMES = ("funds_aligned", "downtrend_b", "knife_b", "chase_h", "fragile_winner")
#: 硬拒绝类旗(契约 B / H);`agreement` 为它们单独计「亮旗仍入选」的次数。
HARD_FLAGS = ("downtrend_b", "knife_b", "chase_h")
#: ⑤ 的字面阈值(契约原文「高 winner_rate(>90)」)。改它 = 改 L3 契约,不是调参。
WINNER_FRAGILE = 90


def _num(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(pd.NA, index=frame.index, dtype="Float64")
    return pd.to_numeric(frame[column], errors="coerce").astype("Float64")


def _flag(mask: pd.Series) -> pd.Series:
    return mask.astype("boolean")


def _truthy(value):
    from autoresearch.common.turnup import LOWTURN_LABEL

    if value is None or (isinstance(value, float) and pd.isna(value)) or value is pd.NA:
        return pd.NA
    return value is True or str(value).strip() in {"True", "true", "1", LOWTURN_LABEL}


def compute_flags(frame: pd.DataFrame, lowturn: pd.Series | None = None) -> pd.DataFrame:
    """L1/L2 帧 → 每只票一行规则旗(pandas 可空布尔;输入缺失 = <NA>)。

    `lowturn`:契约 B 的例外(低位转强旗),与帧同索引的布尔列;帧自带 `lowturn` 列(L3 表的
    「转强」标签或布尔)时用帧的。两者都没有 = 例外是否成立未知 → 亮的 B 旗记 <NA>,不当成没有例外
    (复审 M-2:L1 帧没有这一列,此前一律当 False,B 旗多数)。
    """
    from autoresearch.common.scoring import falling_knife_mask
    from autoresearch.scan.l3.merge import guards_cfg

    main, cmf, obv = _num(frame, "main_net_ratio"), _num(frame, "cmf_20"), _num(frame, "obv_mom_20")
    above20, above60 = _num(frame, "above_ma20"), _num(frame, "above_ma60")
    knife = falling_knife_mask(frame)
    knife = (pd.Series(pd.NA, index=frame.index, dtype="boolean") if knife is None
             else knife.astype("boolean").where(_num(frame, "pct_60d").notna()))
    if "lowturn" in frame.columns:
        lowturn = frame["lowturn"].map(_truthy)
    elif lowturn is None:
        lowturn = pd.Series(pd.NA, index=frame.index)
    lowturn = lowturn.astype("boolean")
    out = pd.DataFrame({"code": frame["code"].astype(str).str.zfill(6)}, index=frame.index)
    out["funds_aligned"] = _flag((main > 0) & (cmf > 0) & (obv > 0))
    raw_b = _flag((above20 == 0) & (above60 == 0) & (main < 0))
    out["downtrend_b"] = (raw_b.mask(lowturn.fillna(False), False)
                          .mask(raw_b.fillna(False) & lowturn.isna(), pd.NA))
    out["knife_b"] = _flag(knife & (main <= 0))
    out["chase_h"] = _flag(_num(frame, "pct_1d") >= float(guards_cfg()["chase_1d_pct"]))
    out["fragile_winner"] = _flag(_num(frame, "winner_rate") > WINNER_FRAGILE)
    return out.reset_index(drop=True)


def _is_finalist(row: dict) -> bool:
    return str(row.get("finalist")).strip().lower() == "true"


def agreement(flags: pd.DataFrame, judged: list[dict]) -> dict:
    """模型的 finalist / bench 与规则旗:硬旗亮了仍入选几只;资金三同向在两侧的占比。"""
    by_code = flags.drop_duplicates("code").set_index("code")    # 同一 code 多一行 → 留第一行
    rows = [r for r in judged if str(r.get("code", "")).zfill(6) in by_code.index]
    finalists = [str(r["code"]).zfill(6) for r in rows if _is_finalist(r)]
    bench = [str(r["code"]).zfill(6) for r in rows if not _is_finalist(r)]
    out: dict = {}
    for name in HARD_FLAGS:
        lit = by_code[name].fillna(False).astype(bool)
        out[name] = {"flagged": int(lit.reindex(finalists + bench).sum()),
                     "flagged_finalist": int(lit.reindex(finalists).sum())}

    def share(codes: list[str]) -> float | None:
        known = by_code["funds_aligned"].reindex(codes).dropna()
        return round(float(known.astype(bool).mean()), 4) if len(known) else None

    out["funds_aligned_share"] = {"finalist": share(finalists), "bench": share(bench)}
    return out


def retest_jaccard(judged_a: list[dict], judged_b: list[dict]) -> float | None:
    """同配置两次 L3 的 finalist 集合 Jaccard;两次都没有 finalist → None。"""
    a = {str(r["code"]).zfill(6) for r in judged_a if _is_finalist(r)}
    b = {str(r["code"]).zfill(6) for r in judged_b if _is_finalist(r)}
    if not a and not b:
        return None
    return len(a & b) / len(a | b)


def _judged_rows(path: Path) -> list[dict]:
    value = json.loads(path.read_text(encoding="utf-8"))
    rows = value if isinstance(value, list) else value.get("judged") or []
    return [r for r in rows if isinstance(r, dict) and r.get("code")]


def _lowturn_for(staging: Path, frame: pd.DataFrame) -> pd.Series | None:
    """该场当时的 `l3.lowturn` 配置(发布物里的 `trace/run_contract.json`)现算 B 例外;
    配置没记 → None(例外未知);关着 → 全 False(当天 L3 表没有这列,例外不生效)。"""
    try:
        contract = json.loads((staging.parent / "run_contract.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    cfg = ((contract.get("user_config") or {}).get("l3") or {}).get("lowturn")
    if not isinstance(cfg, dict):
        return None
    if not cfg.get("enabled", False):
        return pd.Series(False, index=frame.index, dtype="boolean")
    from autoresearch.common.turnup import lowturn_mask

    return lowturn_mask(frame, cfg).astype("boolean")


def readout(reports_scan_root: Path) -> list[dict]:
    """逐场(已发布 run 的 `trace/staging`):L1 帧算旗,对 `_l3_judged.json` 量一致性。"""
    rows: list[dict] = []
    for judged_path in sorted(Path(reports_scan_root).glob("*/trace/staging/_l3_judged.json")):
        staging = judged_path.parent
        frame_path = staging / "L1_scored_full.csv"
        if not frame_path.is_file():
            continue
        frame = pd.read_csv(frame_path, dtype={"code": str})
        got = agreement(compute_flags(frame, _lowturn_for(staging, frame)), _judged_rows(judged_path))
        rows.append({"run": staging.parents[1].name,
                     **{f"{name}_flagged": got[name]["flagged"] for name in HARD_FLAGS},
                     **{f"{name}_finalist": got[name]["flagged_finalist"] for name in HARD_FLAGS},
                     "funds_aligned_finalist": got["funds_aligned_share"]["finalist"],
                     "funds_aligned_bench": got["funds_aligned_share"]["bench"]})
    return rows


def main(argv: list[str] | None = None) -> int:
    from autoresearch.common import workspace as ws

    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--reports-root", default=None, help="已发布 scan 报告根(缺省 = 当前引擎)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    root = Path(args.reports_root) if args.reports_root else ws.reports_root() / "scan"
    rows = readout(root)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows)
    frame.to_csv(out / "l3_rule_flags.csv", index=False)
    (out / "l3_rule_flags.md").write_text(
        "# L3 规则旗 vs 模型选择(影子,只记账)\n\n"
        + (frame.to_markdown(index=False) if rows else "(没有可读的 L3 产物)") + "\n",
        encoding="utf-8")
    print(frame.to_string(index=False) if rows else "no rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
