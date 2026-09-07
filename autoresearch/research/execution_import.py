#!/usr/bin/env python3
"""C5 导入器:显式文件 → 已验证、已脱敏、单位标准化的行。**不访问券商、不访问网络。**

实施计划:`docs/superpowers/plans/2026-09-06-execution-evaluation.md` Task C5。

三种输入,三个来源,各归各的 owner:

- **快照**(`--snapshots`,CSV):逐行过 `contracts.execution.validate_snapshot`;`decision_at`
  列**不采信**——由 run 的时间锚派生(`decision_at_from_execution_block`),导入文件里那列只
  用来对账,不一致记 `DECISION_AT_MISMATCH`。
- **成交**(`--trades`,CSV):格式归 `broker/schema.py`(`TRADES_COLUMNS`),本模块只做**投影**:
  `fill_id = trade_id`、`position_id = (account_hash, code)`(隔夜仓:首笔买入日是 session,之后的卖出腿归同一仓)。券商导出没有委托信息
  → `submitted`/`requested_qty` 为 None,后面 `entry_status` 自然给 UNKNOWN。
- **成本模型**(`--policy`,JSON):`validate_cost_model`。

脱敏:账户号只保留 sha256 前 12 位;`name`(持有人/证券名)不带进研究目录。逐行 `error_code`
保留,不因一行坏数据跳过整份文件,也不因一行坏数据丢掉其余好数据。
"""
from __future__ import annotations

import csv
import hashlib
from pathlib import Path

from autoresearch.broker import schema as broker_schema
from autoresearch.contracts.execution import SNAPSHOT_FIELDS, validate_cost_model, validate_snapshot

#: 投影后的成交行只保留这些列 —— 白名单,不是黑名单:新加的列默认不进研究目录。
FILL_FIELDS: tuple[str, ...] = (
    "fill_id", "position_id", "account_hash", "code", "ts_code", "side", "trade_date",
    "trade_time", "price", "qty", "amount", "commission", "stamp_tax", "transfer_fee",
    "other_fee", "source_kind", "source_observation_id",
)
_JSON_NULLS = {"", "null", "None", "NaN"}


def minimize_record(row: dict, allowed) -> dict:
    return {key: row[key] for key in allowed if key in row}


def unique_records(rows: list[dict], key: str) -> list[dict]:
    seen: set = set()
    for row in rows:
        identity = row[key]
        if identity in seen:
            raise ValueError(f"duplicate {key}: {identity}")
        seen.add(identity)
    return rows


def account_hash(account: str) -> str:
    return hashlib.sha256(str(account).encode("utf-8")).hexdigest()[:12]


def _csv_rows(path: Path) -> list[dict]:
    with Path(path).open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _nullable(value):
    return None if value is None or str(value).strip() in _JSON_NULLS else value


def load_snapshots(path: Path | str, *, engine: str) -> tuple[list[dict], list[dict]]:
    """→ `(有效快照行, 逐行错误)`。跨引擎的行拒收(I02),错误行不进有效集也不丢。"""
    ok, errors = [], []
    for line_no, raw in enumerate(_csv_rows(Path(path)), start=2):
        row = {key: _nullable(raw.get(key)) for key in SNAPSHOT_FIELDS}
        if row["schema_version"] is not None and str(row["schema_version"]).isdigit():
            row["schema_version"] = int(row["schema_version"])
        if row["suspended"] is not None:
            row["suspended"] = {"true": True, "false": False}.get(str(row["suspended"]).lower(),
                                                                  row["suspended"])
        if row["quality_flags"] is not None and isinstance(row["quality_flags"], str):
            row["quality_flags"] = [f for f in row["quality_flags"].split(";") if f]
        try:
            validate_snapshot(row)
            if row["engine"] != engine:
                raise ValueError(f"foreign engine {row['engine']!r} (I02)")
        except ValueError as exc:
            errors.append({"line": line_no, "snapshot_id": row.get("snapshot_id"),
                           "error_code": str(exc)})
            continue
        ok.append(row)
    unique_records(ok, "snapshot_id")
    return ok, errors


def load_trades(path: Path | str) -> tuple[list[dict], list[dict]]:
    """券商 `trades.csv` → 脱敏成交行。列以 `broker/schema.TRADES_COLUMNS` 为准。"""
    fills, errors = [], []
    for line_no, raw in enumerate(_csv_rows(Path(path)), start=2):
        missing = [c for c in broker_schema.TRADES_COLUMNS if c not in raw]
        if missing:
            errors.append({"line": line_no, "error_code": f"missing columns {missing}"})
            continue
        side = raw["side"]
        if side not in broker_schema.SIDES:
            errors.append({"line": line_no, "error_code": f"invalid side {side!r}"})
            continue
        if not raw["trade_id"]:
            errors.append({"line": line_no, "error_code": "trade_id required as fill_id"})
            continue
        code = str(raw["code"]).zfill(6) if raw["code"] else ""
        acct = account_hash(raw["account"])
        row = {
            "fill_id": raw["trade_id"], "position_id": f"{acct}:{code}",
            "account_hash": acct, "code": code, "ts_code": raw["ts_code"], "side": side,
            "trade_date": raw["trade_date"], "trade_time": raw["trade_time"] or None,
            "price": _nullable(raw["price"]), "qty": _nullable(raw["qty"]),
            "amount": _nullable(raw["amount"]),
            "commission": _nullable(raw["commission"]), "stamp_tax": _nullable(raw["stamp_tax"]),
            "transfer_fee": _nullable(raw["transfer_fee"]), "other_fee": _nullable(raw["other_fee"]),
            "source_kind": raw["source_kind"],
            "source_observation_id": f"broker:{raw['source_kind']}:{raw['source_file']}",
        }
        fills.append(minimize_record(row, FILL_FIELDS))
    unique_records(fills, "fill_id")
    return fills, errors


def load_policy(path: Path | str) -> dict:
    import json
    return validate_cost_model(json.loads(Path(path).read_text(encoding="utf-8")))
