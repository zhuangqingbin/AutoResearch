#!/usr/bin/env python3
"""券商成交落盘 —— raw/<src>.csv 幂等 upsert、ingest_log、跨源合并重建 trades.csv。

design: docs/specs/2026-08-27-broker-trades-ingest-design.md §5/§8

两层幂等:文件级(sha256 在 ingest_log 且 status=ok → 跳过)、行级(row_hash upsert)。
`trades.csv` 不是追加出来的,是每次由 raw/* **全量确定性重建** —— 合并规则改了重跑即生效,
不会留下旧规则的残留行。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from autoresearch.broker import schema
from autoresearch.common import workspace as ws

#: 来源优先级(小 = 高):券商交割单 > 中国结算 > 截图;未知来源垫底
PRIORITY = {"gtht": 0, "tpy": 0, "chinaclear": 1, "screenshot": 2}
RAW_DIRNAME = "raw"
TRADES_CSV = "trades.csv"
LOG_NAME = "ingest_log.jsonl"


def root_or_default(root) -> Path:
    return Path(root) if root else ws.broker_root()


def raw_path(root, source_kind: str) -> Path:
    return root_or_default(root) / RAW_DIRNAME / f"{source_kind}.csv"


def trades_path(root) -> Path:
    return root_or_default(root) / TRADES_CSV


def log_path(root) -> Path:
    return root_or_default(root) / LOG_NAME


def sha256_of(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_raw(path: Path) -> pd.DataFrame:
    """读 raw/<src>.csv:文本列保持字符串(空 → ""),数值列转 float(空 → NaN),seq → int。"""
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")
    for col in schema.NUMERIC_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)
    df["seq"] = pd.to_numeric(df["seq"], errors="coerce").fillna(0).astype(int)
    return df


def upsert_raw(df: pd.DataFrame, root=None) -> tuple[int, int]:
    """按 row_hash 追加新行;返回 (新增, 重复)。无新增时**不改写文件**(字节稳定)。"""
    kinds = set(df["source_kind"])
    if len(kinds) != 1:
        raise ValueError(f"一次 upsert 只能一个来源,收到 {sorted(kinds)}")
    path = raw_path(root, kinds.pop())
    existing = (read_raw(path) if path.exists()
                else pd.DataFrame(columns=list(schema.RAW_STORE_COLUMNS)))
    new = df.loc[~df["row_hash"].isin(set(existing["row_hash"])), list(schema.RAW_STORE_COLUMNS)]
    if len(new):
        path.parent.mkdir(parents=True, exist_ok=True)
        out = pd.concat([existing, new], ignore_index=True) if len(existing) else new
        out.to_csv(path, index=False, encoding="utf-8")
    return len(new), len(df) - len(new)


def append_log(entry: dict, root=None) -> None:
    path = log_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def ingested_shas(root=None) -> set[str]:
    path = log_path(root)
    if not path.exists():
        return set()
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("status") == "ok" and rec.get("sha256"):
            out.add(rec["sha256"])
    return out
