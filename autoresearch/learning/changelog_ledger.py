#!/usr/bin/env python3
"""重标定效果追踪 —— 自学习的元评估(确定性,零 LLM)。

design: docs/specs/2026-07-02-scan-observability-design.md §3

changelog.jsonl 记了每次权重重标定;本模块回答"标定之后 IC 真变好了吗":
每条 recalibrate → 采纳日前后各 ≤k 个 retro 日的日度 composite rank-IC 均值对比。
持续 delta≤0 = 校准在空转/过拟合,回头查 horizon/收缩参数。n<3 标 ⚠样本少。

  uv run --no-sync python -m autoresearch.learning.changelog_ledger  # → reports/learning/changelog_ledger.md
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

_COLS = ["id", "retro_date", "trial", "n_before", "n_after", "ic_before", "ic_after", "delta", "thin"]


def _read_jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln:
            try:
                out.append(json.loads(ln))
            except Exception:  # noqa: BLE001
                continue
    return out


def _day_ic(attr: pd.DataFrame) -> float | None:
    """单日 composite vs fwd_1_oo 的 rank-IC(spearman);列缺/样本<10 → None。"""
    if "composite" not in attr.columns or "fwd_1_oo" not in attr.columns:
        return None
    s = pd.to_numeric(attr["composite"], errors="coerce")
    f = pd.to_numeric(attr["fwd_1_oo"], errors="coerce")
    ok = s.notna() & f.notna()
    if ok.sum() < 10:
        return None
    return float(s[ok].rank().corr(f[ok].rank()))


def day_ics(scan_root: Path | str | None = None) -> dict[str, float]:
    """{scan日: 日度IC},来源 retro/attribution.csv(已归因日才有)。"""
    scan_root = Path(scan_root or "context/scan")
    out: dict[str, float] = {}
    if not scan_root.exists():
        return out
    for p in sorted(scan_root.glob("*/retro/attribution.csv")):
        try:
            ic = _day_ic(pd.read_csv(p))
        except Exception:  # noqa: BLE001
            continue
        if ic is not None:
            out[p.parent.parent.name] = ic
    return out


def roll(knowledge_dir: Path | str | None = None, scan_root: Path | str | None = None,
         k: int = 5) -> pd.DataFrame:
    recs = [r for r in _read_jsonl(Path(knowledge_dir or "context/knowledge") / "changelog.jsonl")
            if r.get("kind") == "recalibrate" and r.get("retro_date")]
    ics = day_ics(scan_root)
    dates = sorted(ics)
    rows = []
    # P0-6 DSR-lite:trial = 同参数族第 N 次校准(现全部 recalibrate 都动 composite 权重族,
    # 按 retro_date 升序 1-based 计数;将来引入别的参数族时按 kind+族键分组再计)。
    for trial, r in enumerate(sorted(recs, key=lambda x: str(x["retro_date"])), start=1):
        d = str(r["retro_date"])
        before = [ics[x] for x in dates if x < d][-k:]
        after = [ics[x] for x in dates if x >= d][:k]
        ib = round(sum(before) / len(before), 4) if before else None
        ia = round(sum(after) / len(after), 4) if after else None
        rows.append({"id": r.get("id", ""), "retro_date": d, "trial": trial,
                     "n_before": len(before), "n_after": len(after),
                     "ic_before": ib, "ic_after": ia,
                     "delta": round(ia - ib, 4) if ib is not None and ia is not None else None,
                     "thin": min(len(before), len(after)) < 3})
    return pd.DataFrame(rows, columns=_COLS)


def render(df: pd.DataFrame) -> list[str]:
    out = ["# 重标定效果 ledger(采纳日前后日度 composite IC 对比)", ""]
    if df is None or not len(df):
        return out + ["_无 recalibrate 记录或无已归因日_"]

    def f(x):
        return "—" if x is None or pd.isna(x) else f"{x:+.4f}"

    out += ["| 重标定 | retro日 | 试次 | 前n | 后n | IC前 | IC后 | Δ | |", "|---|---|---|---|---|---|---|---|---|"]
    for r in df.itertuples(index=False):
        out.append(f"| {r.id} | {r.retro_date} | 第{r.trial}版 | {r.n_before} | {r.n_after} "
                   f"| {f(r.ic_before)} | {f(r.ic_after)} | {f(r.delta)} "
                   f"| {'⚠样本少' if r.thin else ''} |")
    solid = df[~df["thin"]]["delta"].dropna()
    if len(solid):
        out += ["", f"- **汇总**:{len(solid)} 次样本足的重标定,Δ 均值 {solid.mean():+.4f}"
                " —— 持续 ≤0 = 校准空转,查 horizon/收缩/样本窗。"]
    # P0-6 DSR-lite 两行固定文案(design: 2026-07-12-selflearning brainstorm §4 P0-6;C19/C18):
    n_trials = int(df["trial"].max()) if "trial" in df.columns and len(df) else 0
    out += ["", f"- **多重检验(DSR-lite)**:composite 权重族已试 **{n_trials}** 版——试得越多,"
            "纯凭运气也会有一版好看(C19);第 N 版须 Δ 显著大于噪声才可信,别就着最近一版顺眼就批。"]
    latest = df.sort_values("retro_date").iloc[-1] if len(df) else None
    if latest is not None and latest["delta"] is not None and not pd.isna(latest["delta"]) \
            and float(latest["delta"]) <= 0:
        out += ["- 🔴 **C18 红灯**:最近一次重标定后不如未标定版(Δ≤0)= **停止调参信号,而非继续调**"
                "——考虑出提案:recalibrate 从『诊断顺带』改『仅 regime 切换时触发』(20 交易日 cadence 人批)。"]
    return out


def main() -> int:
    df = roll()
    out = Path("reports/learning/changelog_ledger.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(render(df)) + "\n", encoding="utf-8")
    print(f"[changelog_ledger] {len(df)} 条 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
