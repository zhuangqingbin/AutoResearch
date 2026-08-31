"""harvest slim 档:L1 行读取 + 二段式落盘(表面块 / 深核块分离)。

design: docs/superpowers/plans/2026-08-31-stock-research-p0-p1.md task-10(D1.1 拆分)。

**纯搬家**(task-10 红线):本文件内容逐字节照搬自 `autoresearch/analyze/harvest.py`
拆分前的 `_l1_float`/`_l1_flag`/`_load_l1_row`/`_split_slim_for_progressive`/
`_write_slim_files` 五个函数(含常量),函数体未改一字节,只有 import 行按新文件位置
调整。

`_load_l1_row` 用到的 `ROOT` 在本文件本地重算(`Path(__file__).resolve().parents[2]`)
而不回引 harvest.py 的 `ROOT` —— 两者取值恒等(同目录深度),但**不**经
`from ... import ROOT` 静态拷贝可以避免一个真实存在的陷阱:若测试
`monkeypatch.setattr(harvest, "ROOT", tmp_path)`,静态拷贝不会跟着变
(`from X import Y` 绑定的是导入那一刻的值,不是 `X.Y` 的实时引用)。现有测试都不曾用
被 patch 的 `ROOT` 驱动 `_load_l1_row`,但本地重算从根上避免这类"改一处、漏一处"的
坑,且不产生任何行为差异。

`_l1_float`/`_l1_flag` **本模块自己定义**(不回引 harvest.py,见 `blocks_statements.py`
模块 docstring 的 DAG 说明——harvest.py 严格在顶端,`python -m` 直跑时任何回引都会
触发"模块执行两遍"的 ImportError)。`blocks_ashare.py` 的 `ashare_market_context_from_l1`
回引这里。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from autoresearch.common import workspace as ws
from autoresearch.dataflows.symbol_utils import normalize_symbol

ROOT = Path(__file__).resolve().parents[2]  # repo root (autoresearch/analyze/ → ../../)


def _l1_float(row: dict, key: str) -> float | None:
    """Float from an L1 row dict, or None if absent/NaN."""
    try:
        f = float(row.get(key))
    except (TypeError, ValueError):
        return None
    return None if f != f else f   # NaN != NaN


def _l1_flag(row: dict, key: str) -> bool | None:
    """Bool-ish L1 flag (ma_bull/above_ma60 persisted as 0/1/True/False/是)."""
    v = row.get(key)
    if v is None or (isinstance(v, float) and v != v):
        return None
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "是", "yes")
    try:
        return bool(float(v))
    except (TypeError, ValueError):
        return None


_P4_DEEP_TITLES = ("Income statement", "Earnings quality", "Solvency")
_P4_POINTER = ("\n<!-- P4 深核分界:深核块(利润表全表/盈利质量/偿付)已拆到同目录 `{deep_name}`。"
               "P1–P3/早停不读;survivor 进 P4 才 Read -->\n")


def _load_l1_row(ticker: str, trade_date: str, root: Path | None = None) -> dict | None:
    """This ticker's L1 召回因子行 from scan artifacts (L1_scored_full superset,
    fallback L1_recall_top1000). None when no scan ran that date / code absent →
    caller falls back to the live tushare fetch (standalone lite / 全量 analyze)。"""
    code = normalize_symbol(ticker).split(".")[0].zfill(6)
    base = (root or (ROOT / ws.scan_root())) / trade_date
    for fname in ("L1_scored_full.csv", "L1_recall_top1000.csv"):
        fp = base / fname
        if not fp.exists():
            continue
        try:
            df = pd.read_csv(fp, dtype={"code": str})
        except Exception:  # noqa: BLE001 — 坏文件 → 当未命中,走 live
            continue
        df["code"] = df["code"].astype(str).str.zfill(6)
        hit = df[df["code"] == code]
        if len(hit):
            return hit.iloc[0].to_dict()
    return None


def _split_slim_for_progressive(parts: list[str]) -> tuple[list[str], list[str]]:
    """slim 二段式:深核块(P4 陷阱维)与表面块分离,表面保序。早停率 ~90% 下深核随文件
    推送 = 多数卡白烧;survivor 用 Read 按需拉 deep 文件。无深核 → deep 空表。"""
    def _is_deep(p: str) -> bool:
        head = p[:120]
        return any(f"## {t}" in head for t in _P4_DEEP_TITLES)

    deep = [p for p in parts if _is_deep(p)]
    surface = [p for p in parts if not _is_deep(p)]
    return surface, deep


def _write_slim_files(out_dir: Path, ticker: str, trade_date: str, parts: list[str]) -> Path:
    """slim 落盘(二段式):表面块写 *_slim.md(尾插 deep 指针),深核块写 *_slim_deep.md。
    无深核块 → 只写单文件不插指针(老路不破)。纯函数式落盘,可 tmp_path 测。"""
    surface, deep = _split_slim_for_progressive(parts)
    out_path = out_dir / f"{ticker}_{trade_date}_slim.md"
    if deep:
        deep_path = out_dir / f"{ticker}_{trade_date}_slim_deep.md"
        deep_path.write_text("\n".join([
            f"# Deep 深核块(P4 陷阱核用) — {ticker} @ {trade_date}\n",
            "_survivor 进 P4 才 Read 本文件;早停卡不读。_\n", *deep]), encoding="utf-8")
        surface = [*surface, _P4_POINTER.format(deep_name=deep_path.name)]
    out_path.write_text("\n".join(surface), encoding="utf-8")
    return out_path
